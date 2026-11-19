"""Opt-in native B1 run-scan end-to-end proofs (plan 05 section 2 / 80.8).

Runs only inside the disposable native cluster environment (the CI
throwaway papersim partition or an explicitly assembled local cluster).
Never touches a business cluster: every test builds its own private
managed-shaped database.  The market source is the adapter stand-in
pack (frozen crypto request bundles), so no network is touched.

Proves the full B1 closed loop on a real database: the clean-worktree
commit check feeding every receipt (FX-03), the one-transaction scan
write (run row with the provider fallback registration + candidates
with FX-26 event keys + entries + cash legs), the FX-17 pause latch
(seeded PAUSE suppresses entries; fresh escalations append evidence
rows; a reviewed RESUME un-latches the queue; multiple PAUSE rows fold
idempotently), the run receipts (STARTED/SUCCEEDED with the verified
commit and the scan id), cash identity after the entry, ``replay``
over every recorded candidate, and retry idempotency of the whole
driver.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from polymarket_alpha_lab import replay
from polymarket_alpha_lab.papersim import migrations as pm
from polymarket_alpha_lab.papersim import evaluation, pipeline, queue_policy, scan, store
from polymarket_alpha_lab.papersim.crypto_model import TerminalProbability
from polymarket_alpha_lab.papersim.gates import NewsCheck
from polymarket_alpha_lab.papersim.optimize.provider import FrozenBaselineProvider
from polymarket_alpha_lab.papersim.pipeline import EntryRequest

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN,
                            reason='explicit native papersim proof is opt-in')

T0 = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.UTC)
EXPIRY = datetime.datetime(2026, 12, 31, 0, 0, tzinfo=datetime.UTC)
PROVIDER = FrozenBaselineProvider()


def _connect(dsn, *, autocommit=True):
    psycopg = pytest.importorskip('psycopg')
    connection = psycopg.connect(dsn)
    connection.autocommit = autocommit
    return connection


def _rebind_database(dsn: str, database: str) -> str:
    return ' '.join(f'dbname={database}' if token.startswith('dbname=') else token
                    for token in dsn.split(' '))


def _fresh_managed_database() -> str:
    """A private migrated APP-dsn database inside the throwaway cluster."""
    psycopg = pytest.importorskip('psycopg')
    admin = _connect(OWNER_DSN)
    name = 'pal_papersim_b1scan_' + uuid.uuid4().hex[:10]
    with admin.cursor() as cursor:
        cursor.execute("SELECT system_identifier::text FROM pg_control_system()")
        system_id = cursor.fetchone()[0]
        cursor.execute(f"CREATE DATABASE {name} OWNER pal_owner")
    admin.close()
    owner_dsn = _rebind_database(OWNER_DSN, name)
    bootstrap = psycopg.connect(owner_dsn)
    bootstrap.autocommit = True
    with bootstrap.cursor() as cursor:
        from polymarket_alpha_lab.project_postgres import sql
        cursor.execute(sql.bootstrap_sql(uuid.uuid4().hex, 'b' * 64, system_id))
    bootstrap.close()
    owner = _connect(owner_dsn, autocommit=False)
    assert pm.apply_pending_migrations(owner, ROOT) == {'applied': 5, 'pending_before': 5}
    owner.commit()
    owner.close()
    return owner_dsn.replace('user=pal_owner', 'user=pal_app')


@pytest.fixture
def app():
    from polymarket_alpha_lab.papersim import store_psycopg
    connection = store_psycopg.connect_explicit(_fresh_managed_database(),
                                                label='PAPERSIM_MIGRATIONS_PG_DSN')
    connection.autocommit = True
    yield connection
    connection.close()


@pytest.fixture
def repo(tmp_path):
    """A committed clean git repository (the FX-03 verified-commit source)."""
    root = tmp_path / 'repo'
    root.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "T"]):
        subprocess.run(["git", "-C", str(root), *args], check=True,
                       capture_output=True, text=True)
    (root / "seed.txt").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "seed.txt"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed"], check=True,
                   capture_output=True, text=True)
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()
    return root, head


def _seed_initial_cash(conn, amount=10_000.0):
    with conn.transaction():
        conn.execute('INSERT INTO papersim.bankroll_events (event_key, occurred_at, '
                     'currency, amount, reason, entry_id, scan_id, reverses_event_id, '
                     'is_legacy) VALUES (%s,%s,%s,%s,%s,NULL,NULL,NULL,false)',
                     (f'seed|initial|{uuid.uuid4().hex[:8]}', T0 - datetime.timedelta(hours=1),
                      'USD', amount, 'INITIAL'))


def _news(fixture: str) -> NewsCheck:
    return NewsCheck(fixture=fixture, delta="48h", concordant=True,
                     blockers=(), sources=(f"operator-attestation:{fixture}",))


def _eth_request() -> EntryRequest:
    return EntryRequest(
        market_id="m-eth2000-term",
        question="Ethereum below $2,000 on date",
        fixture="ETH-2000",
        market_kind="crypto_terminal",
        side="yes",
        book=[(0.15, 5000.0)],
        fee_category="crypto",
        probability=TerminalProbability(
            asset="ETH", barrier=2000.0, horizon_hours=1595.12,
            convention_a=0.2224, convention_b=0.2531,
        ),
        yes_when="below",
        buy_side="yes",
        yes_ask=0.15,
        no_ask=0.80,
        gamma_liquidity=6000.0,
        news=_news("ETH-2000"),
        bankroll=10_000.0,
        cash=10_000.0,
        reserved=0.0,
        book_time=T0,
        now=T0,
        max_book_age_seconds=300.0,
    )


def _crypto_record(*, queue="crypto/eth-terminal", key="eth-2000-term-yes",
                   stale_book=False):
    request = _eth_request()
    if stale_book:
        request = dataclasses.replace(request, book_time=T0 - datetime.timedelta(hours=1))
    return scan.MarketRecord(
        queue=queue,
        candidate_key=key,
        team="crypto-desk",
        side="YES",
        market_kind="terminal",
        lineage="crypto/terminal",
        model_name="crypto",
        evaluated_at=T0 - datetime.timedelta(minutes=1),
        market_ref_kind="market_id",
        market_id="m-eth2000-term",
        median_probability=0.2224,
        events=scan.EventKeyMaterials(market_class="crypto", asset="ETH",
                                      expiry_at=EXPIRY, event_start_at=EXPIRY),
        request_bundle=replay.replay_bundle_payload(request),
    )


def _config(*, queue="crypto/eth-terminal", scope=queue_policy.SCOPE_DOMESTIC):
    return scan.ScanConfig(
        team="pal-b1",
        queues=(scan.QueueScanConfig(
            queue=queue, lineage="crypto/terminal", league_scope=scope,
            data_source_version="gamma+prices-v1", model_key="crypto",
            market_type="terminal"),),
    )


def _cross_league_rows(count=10):
    rows = []
    for index in range(count):
        rows.append(evaluation.EvaluationCandidate(
            candidate_id=f"c{index}", model_key="crypto", market_type="terminal",
            gate_version=pipeline.GATE_VERSION, market_key=f"m{index}",
            accepted=True, stake=50.0, fill_price=0.5, side="YES",
            fee_category="crypto", outcome="loss", recorded_pnl=-70.0,
            decision_time=T0 - datetime.timedelta(days=2),
            settled_time=T0 - datetime.timedelta(days=1),
        ))
    return rows


def _run(conn, repo_root, config, records, *, evaluation_candidates=(),
         operation_key="run-scan|pal-b1|test", now=None):
    moment = now or T0
    return scan.run_scan(
        config, records, conn=conn, repo_root=repo_root, provider=PROVIDER,
        now_utc=lambda: moment, evaluation_candidates=evaluation_candidates,
        operation_key=operation_key,
    )


# ---------------------------------------------------------------------------
# The full closed loop
# ---------------------------------------------------------------------------

@NATIVE
def test_full_scan_writes_candidates_entries_cash_receipts_and_replays(app, repo):
    repo_root, head = repo
    from polymarket_alpha_lab.papersim import store_psycopg
    _seed_initial_cash(app)
    store_psycopg.record_queue_control_event(app, store.QueueControlSubmission(
        queue_id="crypto/eth-paused", control_kind="PAUSE",
        rule=store.QUEUE_CONTROL_RULE_FX17, rule_version=queue_policy.QUEUE_POLICY_RULES_VERSION,
        metrics={"seed": True}, observed_at=T0 - datetime.timedelta(days=1)))
    config = scan.ScanConfig(team="pal-b1", queues=(
        scan.QueueScanConfig(queue="crypto/eth-terminal", lineage="crypto/terminal",
                             league_scope=queue_policy.SCOPE_DOMESTIC,
                             data_source_version="gamma+prices-v1",
                             model_key="crypto", market_type="terminal"),
        scan.QueueScanConfig(queue="crypto/eth-paused", lineage="crypto/terminal",
                             league_scope=queue_policy.SCOPE_CROSS_LEAGUE,
                             data_source_version="gamma+prices-v1",
                             model_key="crypto-paused", market_type="terminal-paused"),
    ))
    # The paused-queue record runs FIRST (clean portfolio state) so its
    # acceptance is decided on the model, not on the frozen same-side
    # held-fixture correlation left by an earlier entry of this scan.
    records = (_crypto_record(queue="crypto/eth-paused", key="eth-2000-paused"),
               _crypto_record())
    result = _run(app, repo_root, config, records)

    assert result["inserted"] is True and result["code_commit"] == head
    assert result["suppressed_entries"] == ["eth-2000-paused"]
    assert result["paused_queues"] == ["crypto/eth-paused"]
    assert result["model_versions"] == [pipeline.MODEL_VERSION_CRYPTO]

    # The run row carries the verified commit, the operation key and the
    # registered provider fallback (04 section 10 step 1).
    row = app.execute('SELECT code_commit, operation_key, params FROM '
                      'papersim.scan_runs WHERE scan_id = %s',
                      (result["scan_id"],)).fetchone()
    assert row[0] == head and row[1] == "run-scan|pal-b1|test"
    params = row[2]
    provider_section = params["provider"]["specs"]["crypto/terminal"]
    assert provider_section["fallback_reason"] == "registry_empty"
    assert provider_section["active_spec_sha256"] == provider_section["baseline_spec_sha256"]

    # Candidates: the paused queue records the accepted candidate
    # WITHOUT its entry, the open queue enters, both with the FX-26 key.
    rows = app.execute('SELECT candidate_key, conclusion, event_key, event_start_at '
                       'FROM papersim.candidates WHERE scan_id = %s '
                       'ORDER BY candidate_id', (result["scan_id"],)).fetchall()
    assert [(r[0], r[1]) for r in rows] == [("eth-2000-paused", "ACCEPTED"),
                                            ("eth-2000-term-yes", "ACCEPTED")]
    for row in rows:
        assert row[2] == f"crypto:ETH|{EXPIRY.date().isoformat()}"
        assert row[3] == EXPIRY
    entry_rows = app.execute('SELECT count(*) FROM papersim.entries').fetchone()
    assert entry_rows[0] == 1

    # Cash identity: seed minus (stake + entry fee) of the ONE entry.
    entry = app.execute('SELECT stake, entry_fee FROM papersim.entries').fetchone()
    total = app.execute('SELECT sum(amount) FROM papersim.bankroll_events').fetchone()[0]
    assert float(total) == 10_000.0 - float(entry[0]) - float(entry[1])

    # Receipts: STARTED and SUCCEEDED, both stamped with the verified
    # commit; SUCCEEDED links the scan.
    receipts = app.execute("SELECT phase, code_commit, scan_id FROM "
                           "papersim.command_run_events ORDER BY run_event_id").fetchall()
    assert [(r[0], r[1], r[2]) for r in receipts] == [
        ("STARTED", head, None), ("SUCCEEDED", head, result["scan_id"])]

    # The frozen stack replays every recorded candidate bit for bit.
    ids = [row[0] for row in app.execute(
        'SELECT candidate_id FROM papersim.candidates WHERE scan_id = %s '
        'ORDER BY candidate_id', (result["scan_id"],)).fetchall()]
    for candidate_id in ids:
        assert replay.replay(app, candidate_id, trusted_code_commit=head)["matched"] is True


@NATIVE
def test_retry_of_an_unchanged_scan_is_idempotent(app, repo):
    repo_root, head = repo
    records = (_crypto_record(key="eth-2000-stale", stale_book=True),)
    first = _run(app, repo_root, _config(), records,
                 operation_key="run-scan|pal-b1|retry")
    second = _run(app, repo_root, _config(), records,
                  operation_key="run-scan|pal-b1|retry")
    assert first["inserted"] is True and second["inserted"] is False
    assert second["scan_id"] == first["scan_id"]
    assert app.execute('SELECT count(*) FROM papersim.scan_runs').fetchone()[0] == 1
    assert app.execute('SELECT count(*) FROM papersim.candidates').fetchone()[0] == 1
    assert app.execute('SELECT count(*) FROM papersim.command_run_events').fetchone()[0] == 2


# ---------------------------------------------------------------------------
# FX-17 latch lifecycle (escalation, multi-row folding, RESUME un-latch)
# ---------------------------------------------------------------------------

@NATIVE
def test_pause_escalation_appends_rows_and_resume_unlatches_per_queue(app, repo):
    repo_root, head = repo
    from polymarket_alpha_lab.papersim import store_psycopg
    _seed_initial_cash(app)
    config = _config(scope=queue_policy.SCOPE_CROSS_LEAGUE)
    queue = "crypto/eth-terminal"

    # First scan with tripping metrics: a fresh PAUSE row lands and the
    # queue's entry is suppressed.
    first = _run(app, repo_root, config, (_crypto_record(),),
                 evaluation_candidates=_cross_league_rows(),
                 operation_key="run-scan|pal-b1|esc1")
    assert first["escalations"] == [queue]
    assert first["suppressed_entries"] == ["eth-2000-term-yes"]
    latched = store_psycopg.latched_pauses(app)
    assert latched[queue] == (True, False)
    pause_count = app.execute("SELECT count(*) FROM papersim.queue_control_events "
                              "WHERE control_kind = 'PAUSE'").fetchone()[0]
    assert pause_count == 1

    # Second scan, same tripping metrics at a later time: another PAUSE
    # row is APPENDED (append-only evidence; the key carries observed_at)
    # and the folded latch is unchanged -- repeated rows fold idempotently.
    second = _run(app, repo_root, config, (_crypto_record(),),
                  evaluation_candidates=_cross_league_rows(),
                  operation_key="run-scan|pal-b1|esc2",
                  now=T0 + datetime.timedelta(minutes=10))
    assert second["escalations"] == [queue]
    assert app.execute("SELECT count(*) FROM papersim.queue_control_events "
                       "WHERE control_kind = 'PAUSE'").fetchone()[0] == 2
    assert store_psycopg.latched_pauses(app)[queue] == (True, False)

    # A reviewed RESUME row AFTER the pauses un-latches the queue
    # (per-queue un-latch: any RESUME clears the queue's latches); the
    # next scan re-enters.
    store_psycopg.record_queue_control_event(app, store.QueueControlSubmission(
        queue_id=queue, control_kind="RESUME", rule=store.QUEUE_CONTROL_RULE_FX17,
        rule_version=queue_policy.QUEUE_POLICY_RULES_VERSION,
        metrics={"review": "owner"}, reviewed_by="owner",
        observed_at=T0 + datetime.timedelta(minutes=15)))
    assert store_psycopg.latched_pauses(app)[queue] == (False, False)
    third = _run(app, repo_root, config, (_crypto_record(key="eth-2000-after-resume"),),
                 operation_key="run-scan|pal-b1|esc3",
                 now=T0 + datetime.timedelta(minutes=20))
    # Without tripping metrics the queue stays admitted.
    assert third["escalations"] == []
    assert third["suppressed_entries"] == []
    assert app.execute('SELECT count(*) FROM papersim.entries').fetchone()[0] == 1
