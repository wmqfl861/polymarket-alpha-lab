"""Opt-in native MO-7 shadow-parallel end-to-end proofs (plan 05 node MO-7).

Runs only inside the disposable native cluster environment (the CI
throwaway papersim partition or an explicitly assembled local cluster).
Never touches a business cluster: every test builds its own private
managed-shaped database.  The market source is the adapter stand-in
pack (frozen crypto request bundles) plus a synthetic ``kraken_candles``
citation, so no network is touched.

Proves the full MO-7 closed loop on a real database (the node's two
acceptance items): a reference B1 scan (frozen provider, empty
registry), then ``initialize_registry`` + one REAL crypto-gbm candidate
(volatility estimator override) + ``RegistryProvider.from_connection`` +
``run_scan`` with the snapshot-driven shadow source -- the active
candidates/entries/cash legs stay byte-identical to the reference, only
shadow rows appear (one per bundle-carrying record, hanging on the
ACTIVE candidate rows), ``replay`` still matches every active
candidate, ``replay.replay_shadow_prediction`` recomputes the stored
shadow row from the bundle + spec + pinned snapshots and detects a
divergent source, retry of the whole driver is idempotent (scan AND
shadows), and the attempt's return value carries the shadow counts and
seconds (the node's scan-duration acceptance record).
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from polymarket_alpha_lab import replay
from polymarket_alpha_lab.papersim import migrations as pm
from polymarket_alpha_lab.papersim import scan, snapshots, store, store_psycopg
from polymarket_alpha_lab.papersim.crypto_model import TerminalProbability
from polymarket_alpha_lab.papersim.gates import NewsCheck
from polymarket_alpha_lab.papersim.optimize.provider import (
    FrozenBaselineProvider,
    RegistryProvider,
)
from polymarket_alpha_lab.papersim.optimize.shadow import SnapshotShadowSource
from polymarket_alpha_lab.papersim.pipeline import EntryRequest
from polymarket_alpha_lab.replay import PapersimReplayError

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN,
                            reason='explicit native papersim proof is opt-in')

T0 = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.UTC)
T1 = datetime.datetime(2026, 11, 20, 12, 30, tzinfo=datetime.UTC)
EXPIRY = datetime.datetime(2026, 12, 31, 0, 0, tzinfo=datetime.UTC)


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
    name = 'pal_papersim_mo7shadow_' + uuid.uuid4().hex[:10]
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
    assert pm.apply_pending_migrations(owner, ROOT) == {
        'applied': len(pm.papersim_migration_catalog(ROOT)),
        'pending_before': len(pm.papersim_migration_catalog(ROOT))}
    owner.commit()
    owner.close()
    return owner_dsn.replace('user=pal_owner', 'user=pal_app')


@pytest.fixture
def app():
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
    key = 'bankroll|initial|seed'
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute('INSERT INTO papersim.bankroll_events (event_key, occurred_at, '
                       'currency, amount, reason, entry_id, scan_id, note, is_legacy) '
                       "VALUES (%s, %s, 'USD', %s, 'INITIAL', NULL, NULL, 'seed', false) "
                       'ON CONFLICT (event_key) DO NOTHING RETURNING event_id',
                       (key, T0, amount))


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


def _crypto_record(book_digest, kline_digest, *, key="eth-2000-term-yes",
                   queue="crypto/eth-terminal", stale_book=False) -> scan.MarketRecord:
    import dataclasses
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
        evaluated_at=T0,
        market_ref_kind="market_id",
        market_id="m-eth2000-term",
        market_type="terminal",
        snapshot_refs=(book_digest, kline_digest),
        book_snapshot_sha256=book_digest,
        events=scan.EventKeyMaterials(market_class="crypto", asset="ETH",
                                      expiry_at=EXPIRY, event_start_at=EXPIRY),
        request_bundle=replay.replay_bundle_payload(request),
    )


def _config(*, queue="crypto/eth-terminal"):
    from polymarket_alpha_lab.papersim import queue_policy
    return scan.ScanConfig(team="paper-team", operator="unattended", queues=(
        scan.QueueScanConfig(queue=queue, lineage="crypto/terminal",
                             league_scope=queue_policy.SCOPE_DOMESTIC,
                             data_source_version="test-v1", model_key="crypto",
                             market_type="terminal", pack=""),))


def _synthetic_closes(count=348):
    """Deterministic positive closes (no RNG, stable across runs)."""
    return [1000.0 + ((index * 37) % 23) for index in range(count)]


def _kline_payload(closes=None):
    return json.dumps({"asset": "ETH", "closes": closes or _synthetic_closes(),
                       "last_close_at": "2026-11-20T06:00:00+00:00"},
                      separators=(",", ":")).encode("utf-8")


def _register_citation(conn, content, kind, source):
    report = snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
        content=content, kind=kind, source=source, fetched_at=T0))
    return report["content_sha256"]


def _snapshot_reader(conn):
    def _read(digest):
        return snapshots.read_snapshot(conn, digest)
    return _read


def _run(conn, repo, config, records, *, provider, now, operation_key,
         shadow_source=None):
    root, head = repo
    return scan.run_scan(
        config, records, conn=conn, repo_root=root, provider=provider,
        now_utc=lambda: now, operation_key=operation_key,
        shadow_source=shadow_source)


def _rows(conn, sql, params=()):
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute(sql, params)
        columns = [description.name for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


@NATIVE
def test_scan_with_registered_candidate_shadows_and_replays(app, repo):
    root, head = repo
    _seed_initial_cash(app)
    book_digest = _register_citation(
        app, replay.bound_book_bytes(_eth_request()), 'clob_book', 'clob:m-eth2000-term')
    kline_digest = _register_citation(app, _kline_payload(), 'kraken_candles',
                                      'test:kraken-eth')
    records = (_crypto_record(book_digest, kline_digest, key="eth-2000-stale",
                              stale_book=True),)

    # 1) Reference scan BEFORE any registry row exists: the B1 frozen
    #    provider, no shadow source -- byte-identical B1 behaviour.
    reference = _run(app, repo, _config(), records,
                     provider=FrozenBaselineProvider(), now=T0,
                     operation_key='mo7-native|reference|1')
    assert reference["inserted"] and reference["shadow_rows"] == 0

    # 2) A real registry: the crypto family baseline plus ONE candidate
    #    spec (an estimator override), registered as CANDIDATE.
    from polymarket_alpha_lab.papersim.optimize.families.crypto import (
        CRYPTO_GBM,
        spec_with,
    )
    store_psycopg.initialize_registry(app, families=[CRYPTO_GBM], actor="op",
                                      code_commit=head, effective_at=T0)
    candidate = spec_with(vol_ew_decay=0.99)
    assert candidate.spec_sha256() != CRYPTO_GBM.baseline_spec().spec_sha256()
    store_psycopg.register_model_spec(app, candidate, source="optimizer",
                                      code_commit=head)
    store_psycopg.record_role_events(app, store.RoleEventBatch(events=(
        store.RoleEventSubmission(
            lineage="crypto-gbm/terminal",
            spec_sha256=candidate.spec_sha256(), role="CANDIDATE",
            effective_at=T0 + datetime.timedelta(hours=1),
            reason="mo7 native proof",
            forward_plan=json.dumps({"forward_metric": "log_loss",
                                     "first_look_matches": 60}, sort_keys=True),
            actor="auto:rules-v1", code_commit=head),)))

    # 3) The MO-7 scan: registry provider + snapshot shadow source.
    provider = RegistryProvider.from_connection(app)
    specs = provider.specs_for_lineage("crypto/terminal")
    assert specs.fallback_reason is None
    assert specs.active.spec_sha256() == CRYPTO_GBM.baseline_spec().spec_sha256()
    assert [spec.spec_sha256() for spec in specs.candidates] == [
        candidate.spec_sha256()]
    source = SnapshotShadowSource(_snapshot_reader(app))
    result = _run(app, repo, _config(), records, provider=provider, now=T1,
                  operation_key='mo7-native|shadow|1', shadow_source=source)
    assert result["inserted"]
    assert result["shadow_error"] is None
    assert result["shadow_rows"] == len(records)
    assert result["shadow_inserted"] == len(records)
    assert result["shadow_stats"]["fits"] == 1
    assert result["shadow_stats"]["predicts"] == len(records)
    assert result["shadow_seconds"] >= 0.0

    # 4) The active path is untouched: candidates, entries and cash legs
    #    byte-identical to the reference scan.
    candidate_columns = ('candidate_key, conclusion, side, market_kind, team, '
                         'model_name, model_version, median_probability, '
                         'variant_quotes, input_payload, gate_report, '
                         'rejection_reasons, evaluated_at, event_key, '
                         'event_start_at')
    reference_candidates = _rows(
        app, f'SELECT {candidate_columns} FROM papersim.candidates '
        'WHERE scan_id = %(scan_id)s ORDER BY candidate_key',
        {'scan_id': reference["scan_id"]})
    shadow_candidates = _rows(
        app, f'SELECT {candidate_columns} FROM papersim.candidates '
        'WHERE scan_id = %(scan_id)s ORDER BY candidate_key',
        {'scan_id': result["scan_id"]})
    assert reference_candidates == shadow_candidates
    reference_entries = _rows(
        app, 'SELECT stake, price, shares, fee_category, fee_rate, entry_fee, '
        'tier, entered_at FROM papersim.entries WHERE entry_id IN '
        '(SELECT entry_id FROM papersim.candidates WHERE scan_id = %(scan_id)s)',
        {'scan_id': reference["scan_id"]})
    shadow_entries = _rows(
        app, 'SELECT stake, price, shares, fee_category, fee_rate, entry_fee, '
        'tier, entered_at FROM papersim.entries WHERE entry_id IN '
        '(SELECT entry_id FROM papersim.candidates WHERE scan_id = %(scan_id)s)',
        {'scan_id': result["scan_id"]})
    assert reference_entries == shadow_entries
    reference_cash = _rows(
        app, 'SELECT occurred_at, currency, amount, reason FROM papersim.bankroll_events '
        'WHERE scan_id = %(scan_id)s ORDER BY event_id',
        {'scan_id': reference["scan_id"]})
    shadow_cash = _rows(
        app, 'SELECT occurred_at, currency, amount, reason FROM papersim.bankroll_events '
        'WHERE scan_id = %(scan_id)s ORDER BY event_id',
        {'scan_id': result["scan_id"]})
    assert reference_cash == shadow_cash

    # 5) The shadow rows hang on THIS scan's active candidate rows only.
    shadow_rows = _rows(
        app, 'SELECT shadow_key, candidate_id, spec_sha256, role_at_prediction, '
        'probability, variant_quotes, refusal_reason, gate_pass, gate_reason, '
        'hypothetical_fill_price, predicted_at FROM papersim.shadow_predictions '
        'ORDER BY shadow_prediction_id')
    assert len(shadow_rows) == len(records)
    active_ids = set(result["candidate_ids"].values())
    for row in shadow_rows:
        assert row["candidate_id"] in active_ids
        assert row["spec_sha256"] == candidate.spec_sha256()
        assert row["role_at_prediction"] == "CANDIDATE"
        assert row["refusal_reason"] is None
        assert 0.0 <= float(row["probability"]) <= 1.0
        assert row["gate_pass"] is False or row["gate_pass"] is True
        assert row["predicted_at"] == T0

    # 6) Replay: the active decisions still match bit-exactly, and the
    #    shadow recomputes from bundle + spec + pinned snapshots.
    for candidate_id in sorted(result["candidate_ids"].values()):
        assert replay.replay(app, candidate_id,
                             trusted_code_commit=head)["matched"] is True
    shadow_candidate_id = min(active_ids)
    outcome = replay.replay_shadow_prediction(
        app, shadow_candidate_id, candidate.spec_sha256(),
        trusted_code_commit=head, quote_source=source)
    assert outcome["matched"] is True
    assert outcome["lineage"] == "crypto/terminal"
    # A divergent history (different closes -> different sigma) must NOT
    # reproduce the stored row: the comparison is load-bearing.
    divergent_payload = _kline_payload(closes=[900.0 + ((index * 41) % 19)
                                               for index in range(348)])

    def _divergent_reader(digest):
        row = snapshots.read_snapshot(app, digest)
        if row is not None and str(row.get("kind")) == "kraken_candles":
            return {**row, "content": divergent_payload}
        return row

    divergent_source = SnapshotShadowSource(_divergent_reader)
    with pytest.raises(PapersimReplayError):
        replay.replay_shadow_prediction(
            app, shadow_candidate_id, candidate.spec_sha256(),
            trusted_code_commit=head, quote_source=divergent_source)
    # An unknown spec digest is an explicit failure.
    with pytest.raises(PapersimReplayError):
        replay.replay_shadow_prediction(
            app, shadow_candidate_id, "a" * 64,
            trusted_code_commit=head, quote_source=source)


@NATIVE
def test_scan_retry_is_idempotent_including_the_shadow_rows(app, repo):
    root, head = repo
    _seed_initial_cash(app)
    book_digest = _register_citation(
        app, replay.bound_book_bytes(_eth_request()), 'clob_book', 'clob:m-eth2000-term')
    kline_digest = _register_citation(app, _kline_payload(), 'kraken_candles',
                                      'test:kraken-eth')
    records = (_crypto_record(book_digest, kline_digest, key="eth-2000-stale",
                              stale_book=True),)
    from polymarket_alpha_lab.papersim.optimize.families.crypto import (
        CRYPTO_GBM,
        spec_with,
    )
    store_psycopg.initialize_registry(app, families=[CRYPTO_GBM], actor="op",
                                      code_commit=head, effective_at=T0)
    candidate = spec_with(vol_ew_decay=0.99)
    store_psycopg.register_model_spec(app, candidate, source="optimizer",
                                      code_commit=head)
    store_psycopg.record_role_events(app, store.RoleEventBatch(events=(
        store.RoleEventSubmission(
            lineage="crypto-gbm/terminal",
            spec_sha256=candidate.spec_sha256(), role="CANDIDATE",
            effective_at=T0 + datetime.timedelta(hours=1),
            reason="mo7 native proof",
            forward_plan='{"forward_metric": "log_loss"}',
            actor="auto:rules-v1", code_commit=head),)))
    provider = RegistryProvider.from_connection(app)
    source = SnapshotShadowSource(_snapshot_reader(app))
    first = _run(app, repo, _config(), records, provider=provider, now=T1,
                 operation_key='mo7-native|retry|1', shadow_source=source)
    assert first["inserted"] and first["shadow_inserted"] == 1
    # A second, unchanged attempt under the SAME scheduling key: the
    # scan read-back returns the original ids and every shadow row is
    # an idempotent replay (inserted == 0).
    again = _run(app, repo, _config(), records, provider=provider, now=T1,
                 operation_key='mo7-native|retry|1', shadow_source=source)
    assert again["inserted"] is False
    assert again["scan_id"] == first["scan_id"]
    assert again["candidate_ids"] == first["candidate_ids"]
    assert again["shadow_rows"] == 1
    assert again["shadow_inserted"] == 0
    assert _rows(app, 'SELECT count(*) AS n FROM papersim.shadow_predictions')[0]["n"] == 1
