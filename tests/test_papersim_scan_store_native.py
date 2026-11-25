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

from concurrent.futures import ThreadPoolExecutor
import dataclasses
import datetime
from importlib import import_module
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import ModuleType
import uuid

import pytest

from polymarket_alpha_lab import replay
from polymarket_alpha_lab.papersim import migrations as pm
from polymarket_alpha_lab.papersim import cli, evaluation, pipeline, queue_policy, scan, store
from polymarket_alpha_lab.papersim.crypto_model import TerminalProbability
from polymarket_alpha_lab.papersim.gates import NewsCheck
from polymarket_alpha_lab.papersim.optimize.provider import FrozenBaselineProvider, RegistryProvider
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
    # MO-2 land: the managed catalog grew to six entries; assert
    # against the live catalog length (the m2_store_native precedent)
    # instead of pinning a count that every future migration bumps.
    assert pm.apply_pending_migrations(owner, ROOT) == {
        'applied': len(pm.papersim_migration_catalog(ROOT)),
        'pending_before': len(pm.papersim_migration_catalog(ROOT))}
    owner.commit()
    owner.close()
    return owner_dsn.replace('user=pal_owner', 'user=pal_app')


@pytest.fixture
def app_dsn():
    return _fresh_managed_database()


@pytest.fixture
def app(app_dsn):
    from polymarket_alpha_lab.papersim import store_psycopg
    connection = store_psycopg.connect_explicit(app_dsn,
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
# FX-47 CLI quote persistence and replay
# ---------------------------------------------------------------------------

VARIANT_QUOTES = [0.1, 0.2]
CANONICAL_QUOTES = "[0.1,0.2]"
NONCANONICAL_QUOTES = "[ 0.10, 0.2 ]"
QUOTE_OPERATION = "run-scan|pal-b1|variant-quotes"


@pytest.fixture
def variant_quote_pack(app, tmp_path):
    _seed_initial_cash(app)
    pack_path = tmp_path / "quotes.json"
    config_path = tmp_path / "scan-config.json"
    config = _config()
    config = dataclasses.replace(config, queues=(
        dataclasses.replace(config.queues[0], pack=pack_path.name),))
    return config, config_path, pack_path


def _variant_quote_record(quotes, *, stale_book=True, **changes):
    return dataclasses.replace(_crypto_record(stale_book=stale_book), variant_quotes=quotes, **changes)


def _write_variant_quote_inputs(variant_quote_pack, records, *, operator):
    config, config_path, pack_path = variant_quote_pack
    if operator is not None:
        config = dataclasses.replace(config, operator=operator)
    config_path.write_text(json.dumps(dataclasses.asdict(config)), encoding="utf-8")
    pack_path.write_text(json.dumps([dataclasses.asdict(record) for record in records],
                                    default=datetime.datetime.isoformat), encoding="utf-8")
    return config


@pytest.fixture
def run_variant_quote_scan(app_dsn, repo, variant_quote_pack, monkeypatch, capsys):
    _, config_path, _ = variant_quote_pack
    # The store and psycopg have already bound a real timestamp when seeding cash.
    # Load the CLI's delayed imports before scoping the local datetime import.
    for module in ("optimize.provider", "optimize.shadow", "snapshots"):
        import_module(f"polymarket_alpha_lab.papersim.{module}")

    class FixedScanDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return T0.astimezone().replace(tzinfo=None)
            return T0.astimezone(tz)

    clock = ModuleType("datetime")
    vars(clock).update(vars(datetime))
    clock.datetime = FixedScanDateTime

    def run(quotes=None, *, attempt=1, records=None, operator=None, **changes):
        if records is None:
            records = (_variant_quote_record(quotes, **changes),)
        _write_variant_quote_inputs(variant_quote_pack, records, operator=operator)
        args = ["run-scan", "--config", str(config_path), "--dsn", app_dsn,
                "--repo-root", str(repo[0]), "--operation-key", QUOTE_OPERATION,
                "--attempt", str(attempt)]
        with monkeypatch.context() as clock_patch:
            clock_patch.setitem(sys.modules, "datetime", clock)
            code = cli.main(args)
        assert sys.modules["datetime"] is datetime
        return code, capsys.readouterr()

    return run


def _variant_quote_submission(app, repo, variant_quote_pack, quotes=None, *, records=None, operator=None):
    if records is None:
        records = (_variant_quote_record(quotes),)
    config = _write_variant_quote_inputs(variant_quote_pack, records, operator=operator)
    _, config_path, _ = variant_quote_pack
    records = scan.load_pack_records(config, base_dir=config_path.parent)
    return scan.assemble_scan(
        config, records, admissions=scan.pause_admissions((), config.queues, {}),
        portfolio=scan.read_portfolio_state(app), started_at=T0, finished_at=T0,
        code_commit=repo[1], specs=scan.consult_provider(RegistryProvider.from_connection(app), records),
        operation_key=QUOTE_OPERATION,
    ).submission


def _seed_old_variant_quote_scan(app, submission):
    # Bind the original quote value with the legacy SQL and parameter order.
    row = submission.scan
    with app.transaction():
        (scan_id,) = app.execute(
            "INSERT INTO papersim.scan_runs (scan_key, scan_payload_sha256, team, started_at, "
            "finished_at, code_commit, schema_version, gate_version, operator, params, "
            "is_legacy, operation_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) "
            "RETURNING scan_id",
            (row.key(), submission.payload_sha256(), row.team, row.started_at, row.finished_at,
             row.code_commit, row.schema_version, row.gate_version, row.operator, row.params,
             row.is_legacy, row.operation_key),
        ).fetchone()
        for candidate in submission.candidates:
            assert candidate.conclusion == "REJECTED" and candidate.entry is None
            assert candidate.price_evidence
            (candidate_id,) = app.execute(
                "INSERT INTO papersim.candidates (scan_id, candidate_key, legacy_source_id, "
                "condition_id, market_id, market_slug, yes_token_id, no_token_id, side, "
                "market_kind, market_type, event_key, event_start_at, team, binding_version, "
                "model_name, model_version, median_probability, variant_quotes, input_payload, "
                "snapshot_refs, order_book, book_fetched_at, conclusion, gate_report, "
                "rejection_reasons, evaluated_at, is_legacy) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,"
                "%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s) RETURNING candidate_id",
                (scan_id, candidate.candidate_key, candidate.legacy_source_id, candidate.condition_id,
                 candidate.market_id, candidate.market_slug, candidate.yes_token_id, candidate.no_token_id,
                 candidate.side, candidate.market_kind, candidate.market_type, candidate.event_key,
                 candidate.event_start_at, candidate.team, candidate.binding_version, candidate.model_name,
                 candidate.model_version, candidate.median_probability, candidate.variant_quotes,
                 candidate.input_payload, candidate.snapshot_refs, candidate.order_book, candidate.book_fetched_at,
                 candidate.conclusion, candidate.gate_report, candidate.rejection_reasons,
                 candidate.evaluated_at, candidate.is_legacy),
            ).fetchone()
            for evidence in candidate.price_evidence:
                app.execute(
                    "INSERT INTO papersim.candidate_price_evidence (evidence_key, candidate_id, "
                    "evidence_role, observed_at, model_probability, market_ref_kind, market_ref, "
                    "token_ref, side, bid, ask, mid, last, book_sha256, snapshot_sha256, "
                    "source_kind, source_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (evidence.evidence_key_with(candidate_id), candidate_id, evidence.evidence_role,
                     evidence.observed_at, evidence.model_probability, evidence.market_ref_kind,
                     evidence.market_ref, evidence.token_ref, evidence.side, evidence.bid, evidence.ask,
                     evidence.mid, evidence.last, evidence.book_sha256, evidence.snapshot_sha256,
                     evidence.source_kind, evidence.source_ref),
                )
    return scan_id


def _stored_variant_quote_scan(app):
    scans = app.execute(
        "SELECT scan_id, scan_key, convert_to(scan_payload_sha256, 'UTF8'), code_commit, "
        "operation_key FROM papersim.scan_runs",
    ).fetchall()
    candidates = app.execute(
        "SELECT candidate_id, scan_id, candidate_key, conclusion, jsonb_typeof(variant_quotes), "
        "variant_quotes, convert_to(variant_quotes::text, 'UTF8') FROM papersim.candidates",
    ).fetchall()
    [(scan_id, key, digest, commit, operation)] = scans
    [(candidate_id, candidate_scan_id, candidate_key, conclusion, kind, quotes, quote_bytes)] = candidates
    (entry_rows, cash_rows) = app.execute(
        "SELECT (SELECT count(*) FROM papersim.entries), "
        "(SELECT count(*) FROM papersim.bankroll_events WHERE scan_id IS NOT NULL)",
    ).fetchone()
    assert operation == QUOTE_OPERATION
    assert candidate_scan_id == scan_id and conclusion == "REJECTED"
    assert kind == "array" and quotes == VARIANT_QUOTES
    assert entry_rows == cash_rows == 0
    return {"scan_id": scan_id, "scan_key": key, "code_commit": commit,
            "candidate_ids": {candidate_key: candidate_id}, "scan_payload_sha256": digest.decode("utf-8"),
            "variant_quotes_type": kind, "variant_quotes": quotes,
            "variant_quotes_db_text": quote_bytes.decode("utf-8"),
            "scan_rows": len(scans), "candidate_rows": len(candidates),
            "entry_rows": entry_rows, "cash_rows": cash_rows}


def _variant_quote_receipts(app, attempt=1):
    return app.execute(
        "SELECT phase, scan_id, error_code FROM papersim.command_run_events "
        "WHERE operation_key = %s AND attempt = %s ORDER BY run_event_id",
        (QUOTE_OPERATION, attempt),
    ).fetchall()


def _successful_variant_quote_run(app, outcome, *, inserted, expected_digest):
    code, output = outcome
    assert code == 0, output.err
    assert not output.err
    result = json.loads(output.out)
    stored = _stored_variant_quote_scan(app)
    assert result["inserted"] is inserted
    for field in ("scan_id", "scan_key", "candidate_ids", "code_commit"):
        assert result[field] == stored[field]
    assert stored["scan_payload_sha256"] == expected_digest
    assert result["bankroll_event_keys"] == []
    return {"exit_code": code, "inserted": inserted, **stored}


def _conflicting_variant_quote_run(app, outcome, original, attempt):
    code, output = outcome
    error = "papersim_scan_payload_conflict"
    assert code == 1
    assert not output.out and output.err == f"run-scan: FAIL: {error}\n"
    assert _stored_variant_quote_scan(app) == original
    receipts = _variant_quote_receipts(app, attempt)
    assert receipts == [("STARTED", None, None), ("FAILED", None, error)]
    return {"exit_code": code, "error": error, "attempt": attempt, "receipts": receipts}


@NATIVE
@pytest.mark.parametrize(("first", "second"), (
    pytest.param(NONCANONICAL_QUOTES, VARIANT_QUOTES, id="text-first"),
    pytest.param(VARIANT_QUOTES, NONCANONICAL_QUOTES, id="array-first"),
))
def test_run_scan_variant_quotes_replays_both_forms(
        app, repo, variant_quote_pack, run_variant_quote_scan, record_property, first, second):
    submission = _variant_quote_submission(app, repo, variant_quote_pack, CANONICAL_QUOTES)
    digest = submission.payload_sha256()
    inserted = _successful_variant_quote_run(app, run_variant_quote_scan(first),
                                             inserted=True, expected_digest=digest)
    replayed = _successful_variant_quote_run(app, run_variant_quote_scan(second),
                                             inserted=False, expected_digest=digest)
    assert replayed == {**inserted, "inserted": False}
    receipts = _variant_quote_receipts(app)
    assert receipts == [("STARTED", None, None), ("SUCCEEDED", inserted["scan_id"], None)]
    record_property("fx47_variant_quotes", json.dumps({"runs": [inserted, replayed], "receipts": receipts}))


@NATIVE
@pytest.mark.parametrize("text", (CANONICAL_QUOTES, NONCANONICAL_QUOTES), ids=("canonical", "noncanonical"))
def test_run_scan_replays_legacy_variant_quotes(
        app, repo, variant_quote_pack, run_variant_quote_scan, record_property, text):
    legacy = _variant_quote_submission(app, repo, variant_quote_pack, text)
    digest = legacy.payload_sha256()
    canonical_digest = _variant_quote_submission(app, repo, variant_quote_pack,
                                                CANONICAL_QUOTES).payload_sha256()
    assert (digest == canonical_digest) is (text == CANONICAL_QUOTES)
    _seed_old_variant_quote_scan(app, legacy)
    original = _stored_variant_quote_scan(app)
    assert original["scan_payload_sha256"] == digest
    quote_scale = "[0.1, 0.2]" if text == CANONICAL_QUOTES else "[0.10, 0.2]"
    assert original["variant_quotes_db_text"] == quote_scale
    exact = _successful_variant_quote_run(app, run_variant_quote_scan(text),
                                          inserted=False, expected_digest=digest)
    assert exact == {"exit_code": 0, "inserted": False, **original}

    if text == CANONICAL_QUOTES:
        alternate = [_successful_variant_quote_run(app, run_variant_quote_scan(VARIANT_QUOTES),
                                                   inserted=False, expected_digest=digest)]
        assert alternate == [exact]
    else:
        alternate = []
        for attempt, quotes in ((2, VARIANT_QUOTES), (3, "[0.10, 0.2]")):
            outcome = run_variant_quote_scan(quotes, attempt=attempt)
            alternate.append(_conflicting_variant_quote_run(app, outcome, original, attempt))
    receipts = _variant_quote_receipts(app)
    assert receipts == [("STARTED", None, None), ("SUCCEEDED", original["scan_id"], None)]
    record_property("fx47_legacy_variant_quotes", json.dumps({
        "input_text": text, "seed": original, "exact": exact, "alternate": alternate, "receipts": receipts,
    }))


@NATIVE
@pytest.mark.parametrize("legacy", (False, True), ids=("new", "legacy-noncanonical"))
def test_run_scan_variant_quotes_conflict_preserves_rows(
        app, repo, variant_quote_pack, run_variant_quote_scan, record_property, legacy):
    original_submission = _variant_quote_submission(
        app, repo, variant_quote_pack, NONCANONICAL_QUOTES if legacy else CANONICAL_QUOTES)
    if legacy:
        _seed_old_variant_quote_scan(app, original_submission)
    else:
        _successful_variant_quote_run(app, run_variant_quote_scan(VARIANT_QUOTES), inserted=True,
                                      expected_digest=original_submission.payload_sha256())
    original = _stored_variant_quote_scan(app)
    assert original["scan_payload_sha256"] == original_submission.payload_sha256()
    changes = (
        ("[ 0.11, 0.2 ]" if legacy else [0.11, 0.2], {}),
        ("[ 0.2, 0.10 ]" if legacy else [0.2, 0.1], {}),
        (NONCANONICAL_QUOTES if legacy else VARIANT_QUOTES, {"median_probability": 0.24}),
        ("[ 0.10000000000000001, 0.2 ]", {}),
    )
    rejected = []
    for attempt, (quotes, fields) in enumerate(changes, start=2):
        outcome = run_variant_quote_scan(quotes, attempt=attempt, **fields)
        rejected.append(_conflicting_variant_quote_run(app, outcome, original, attempt))
    record_property("fx47_variant_quotes_conflicts", json.dumps({"seed": original, "runs": rejected}))


@NATIVE
def test_run_scan_preserves_precise_variant_quote_text(app, repo, variant_quote_pack, run_variant_quote_scan):
    canonical = "[0.10000000000000001,0.2]"
    submission = _variant_quote_submission(app, repo, variant_quote_pack, canonical)
    digest = submission.payload_sha256()
    first = _successful_variant_quote_run(app, run_variant_quote_scan("[ 0.10000000000000001, 0.2 ]"),
                                          inserted=True, expected_digest=digest)
    assert first["variant_quotes_db_text"] == "[0.10000000000000001, 0.2]"
    second = _successful_variant_quote_run(app, run_variant_quote_scan("[0.100000000000000010,0.20]"),
                                           inserted=False, expected_digest=digest)
    assert second == {**first, "inserted": False}


SCAN_PAYLOAD_CONFLICT = "papersim_scan_payload_conflict"
LEGACY_EMPTY_LIST = ("object", b"{}")
EMPTY_ARRAY = ("array", b"[]")
NUMBER_ARRAY = ("array", b"[0.1, 0.2]")
NONCANONICAL_NUMBER_ARRAY = ("array", b"[0.10, 0.2]")


def _quote_scan_snapshot(app, scan_key):
    """Read every business row for one key, including the original quote and digest bytes."""
    return {
        "scans": app.execute(
            "SELECT s.scan_id, s.scan_key, s.code_commit, convert_to(s.scan_payload_sha256, 'UTF8'), "
            "to_jsonb(s)::text FROM papersim.scan_runs s WHERE s.scan_key = %s",
            (scan_key,),
        ).fetchall(),
        "candidates": app.execute(
            "SELECT c.candidate_id, c.candidate_key, jsonb_typeof(c.variant_quotes), "
            "convert_to(c.variant_quotes::text, 'UTF8'), to_jsonb(c)::text "
            "FROM papersim.candidates c JOIN papersim.scan_runs s USING (scan_id) "
            "WHERE s.scan_key = %s ORDER BY c.candidate_id",
            (scan_key,),
        ).fetchall(),
        "evidence": app.execute(
            "SELECT p.evidence_id, p.candidate_id, to_jsonb(p)::text "
            "FROM papersim.candidate_price_evidence p JOIN papersim.candidates c USING (candidate_id) "
            "JOIN papersim.scan_runs s USING (scan_id) WHERE s.scan_key = %s ORDER BY p.evidence_id",
            (scan_key,),
        ).fetchall(),
        "entries": app.execute(
            "SELECT e.entry_id, e.candidate_id, to_jsonb(e)::text "
            "FROM papersim.entries e JOIN papersim.candidates c USING (candidate_id) "
            "JOIN papersim.scan_runs s USING (scan_id) WHERE s.scan_key = %s ORDER BY e.entry_id",
            (scan_key,),
        ).fetchall(),
        "cash": app.execute(
            "SELECT b.event_key, b.entry_id, to_jsonb(b)::text "
            "FROM papersim.bankroll_events b JOIN papersim.scan_runs s USING (scan_id) "
            "WHERE s.scan_key = %s ORDER BY b.event_id",
            (scan_key,),
        ).fetchall(),
    }


def _assert_quote_scan_snapshot(snapshot, submission, stored_quotes):
    [(_, key, commit, digest, _)] = snapshot["scans"]
    assert key == submission.scan.key() and commit == submission.scan.code_commit
    assert digest == submission.payload_sha256().encode("utf-8")
    assert [(key, kind, raw) for _, key, kind, raw, _ in snapshot["candidates"]] == [
        (candidate.candidate_key, kind, raw)
        for candidate, (kind, raw) in zip(submission.candidates, stored_quotes, strict=True)
    ]
    candidate_ids = {key: candidate_id for candidate_id, key, *_ in snapshot["candidates"]}
    assert [candidate_id for _, candidate_id, _ in snapshot["evidence"]] == [
        candidate_ids[candidate.candidate_key]
        for candidate in submission.candidates for _ in candidate.price_evidence
    ]
    assert [candidate_id for _, candidate_id, _ in snapshot["entries"]] == [
        candidate_ids[candidate.candidate_key] for candidate in submission.candidates
        if candidate.entry is not None
    ]
    assert [(key, entry_id) for key, entry_id, _ in snapshot["cash"]] == [
        (f"entry:{entry_id}:cash", entry_id) for entry_id, _, _ in snapshot["entries"]
    ]


def _quote_scan_result(snapshot, *, inserted):
    [(scan_id, key, _, _, _)] = snapshot["scans"]
    return {
        "scan_id": scan_id,
        "scan_key": key,
        "inserted": inserted,
        "candidate_ids": {key: candidate_id for candidate_id, key, *_ in snapshot["candidates"]},
        "bankroll_event_keys": tuple(key for key, _, _ in snapshot["cash"]),
    }


def _assert_quote_scan_cli(outcome, snapshot, *, inserted):
    code, output = outcome
    assert code == 0, output.err
    assert not output.err
    actual = json.loads(output.out)
    expected = _quote_scan_result(snapshot, inserted=inserted)
    expected["bankroll_event_keys"] = list(expected["bankroll_event_keys"])
    expected["code_commit"] = snapshot["scans"][0][2]
    assert {key: actual[key] for key in expected} == expected


def _assert_quote_scan_replay(app, submission, outcome, snapshot):
    from polymarket_alpha_lab.papersim import store_psycopg

    key = submission.scan.key()
    _assert_quote_scan_cli(outcome, snapshot, inserted=False)
    assert _quote_scan_snapshot(app, key) == snapshot
    assert store_psycopg.record_scan(app, submission) == _quote_scan_result(snapshot, inserted=False)
    assert _quote_scan_snapshot(app, key) == snapshot


def _assert_quote_scan_conflict(app, submission, outcome, snapshot, attempt):
    from polymarket_alpha_lab.papersim import store_psycopg

    key = submission.scan.key()
    code, output = outcome
    assert code == 1
    assert not output.out and output.err == f"run-scan: FAIL: {SCAN_PAYLOAD_CONFLICT}\n"
    assert _variant_quote_receipts(app, attempt) == [
        ("STARTED", None, None), ("FAILED", None, SCAN_PAYLOAD_CONFLICT),
    ]
    assert _quote_scan_snapshot(app, key) == snapshot
    with pytest.raises(store.PapersimStoreError) as error:
        store_psycopg.record_scan(app, submission)
    assert error.value.code == SCAN_PAYLOAD_CONFLICT
    assert _quote_scan_snapshot(app, key) == snapshot


def _record_quote_scan_evidence(record_property, snapshot):
    [(scan_id, scan_key, _, digest, _)] = snapshot["scans"]
    record_property("fx47_quote_scan", json.dumps({
        "scan_id": scan_id, "scan_key": scan_key, "digest": digest.decode("utf-8"),
        "candidates": [
            {"id": candidate_id, "key": key, "quote_type": kind, "quote_text": text.decode("utf-8")}
            for candidate_id, key, kind, text, _ in snapshot["candidates"]
        ],
        "evidence_ids": [evidence_id for evidence_id, _, _ in snapshot["evidence"]],
        "entry_ids": [entry_id for entry_id, _, _ in snapshot["entries"]],
        "cash_keys": [key for key, _, _ in snapshot["cash"]],
    }))


@NATIVE
@pytest.mark.parametrize(("legacy_quotes", "successful", "conflicting", "stored_quotes"), (
    pytest.param([], ([], "[]", "[ ]"), (), LEGACY_EMPTY_LIST, id="python-empty-list"),
    pytest.param("[]", ("[]", []), (), EMPTY_ARRAY, id="canonical-empty-text"),
    pytest.param("[ ]", ("[ ]",), ([], "[]"), EMPTY_ARRAY, id="noncanonical-empty-text"),
))
def test_run_scan_replays_legacy_empty_variant_quotes(
        app, repo, variant_quote_pack, run_variant_quote_scan, record_property,
        legacy_quotes, successful, conflicting, stored_quotes):
    legacy = _variant_quote_submission(app, repo, variant_quote_pack, legacy_quotes)
    canonical = _variant_quote_submission(app, repo, variant_quote_pack, "[]")
    assert (legacy.payload_sha256() == canonical.payload_sha256()) is (legacy_quotes == "[]")
    scan_id = _seed_old_variant_quote_scan(app, legacy)
    snapshot = _quote_scan_snapshot(app, legacy.scan.key())
    _assert_quote_scan_snapshot(snapshot, legacy, (stored_quotes,))

    for quotes in successful:
        submission = _variant_quote_submission(app, repo, variant_quote_pack, quotes)
        _assert_quote_scan_replay(app, submission, run_variant_quote_scan(quotes), snapshot)
    assert _variant_quote_receipts(app) == [("STARTED", None, None), ("SUCCEEDED", scan_id, None)]
    for attempt, quotes in enumerate(conflicting, start=2):
        submission = _variant_quote_submission(app, repo, variant_quote_pack, quotes)
        outcome = run_variant_quote_scan(quotes, attempt=attempt)
        _assert_quote_scan_conflict(app, submission, outcome, snapshot, attempt)
    _record_quote_scan_evidence(record_property, snapshot)


@NATIVE
@pytest.mark.parametrize(("first", "second"), (
    pytest.param("[]", [], id="text-first"),
    pytest.param([], "[ ]", id="list-first"),
))
def test_run_scan_writes_empty_variant_quotes_as_array(
        app, repo, variant_quote_pack, run_variant_quote_scan, record_property, first, second):
    canonical = _variant_quote_submission(app, repo, variant_quote_pack, "[]")
    key = canonical.scan.key()
    assert not any(_quote_scan_snapshot(app, key).values())
    outcome = run_variant_quote_scan(first)
    snapshot = _quote_scan_snapshot(app, key)
    _assert_quote_scan_snapshot(snapshot, canonical, (EMPTY_ARRAY,))
    _assert_quote_scan_cli(outcome, snapshot, inserted=True)

    submission = _variant_quote_submission(app, repo, variant_quote_pack, second)
    _assert_quote_scan_replay(app, submission, run_variant_quote_scan(second), snapshot)
    assert _variant_quote_receipts(app) == [
        ("STARTED", None, None), ("SUCCEEDED", snapshot["scans"][0][0], None),
    ]
    _record_quote_scan_evidence(record_property, snapshot)


def _variant_quote_records(*quotes):
    return tuple(_variant_quote_record(value, candidate_key=f"variant-quote-{index}")
                 for index, value in enumerate(quotes))


@NATIVE
def test_run_scan_replays_legacy_mixed_variant_quotes(
        app, repo, variant_quote_pack, run_variant_quote_scan, record_property):
    records = _variant_quote_records([], "[]", NONCANONICAL_QUOTES, CANONICAL_QUOTES)
    legacy = _variant_quote_submission(app, repo, variant_quote_pack, records=records)
    canonical_records = _variant_quote_records("[]", "[]", CANONICAL_QUOTES, CANONICAL_QUOTES)
    canonical = _variant_quote_submission(app, repo, variant_quote_pack, records=canonical_records)
    assert legacy.payload_sha256() != canonical.payload_sha256()
    scan_id = _seed_old_variant_quote_scan(app, legacy)
    snapshot = _quote_scan_snapshot(app, legacy.scan.key())
    _assert_quote_scan_snapshot(snapshot, legacy,
                                (LEGACY_EMPTY_LIST, EMPTY_ARRAY, NONCANONICAL_NUMBER_ARRAY, NUMBER_ARRAY))

    mixed = _variant_quote_records("[]", [], NONCANONICAL_QUOTES, VARIANT_QUOTES)
    retry = _variant_quote_submission(app, repo, variant_quote_pack, records=mixed)
    assert retry.payload_sha256() != legacy.payload_sha256()
    _assert_quote_scan_replay(app, legacy, run_variant_quote_scan(records=records), snapshot)
    _assert_quote_scan_replay(app, retry, run_variant_quote_scan(records=mixed), snapshot)
    assert _variant_quote_receipts(app) == [("STARTED", None, None), ("SUCCEEDED", scan_id, None)]

    # Changing the noncanonical spelling loses its original bytes. Rewriting
    # only the canonical position as noncanonical text also cannot match the
    # same full digest while preserving the spelling in the third position.
    changes = (
        _variant_quote_records("[]", [], CANONICAL_QUOTES, VARIANT_QUOTES),
        _variant_quote_records("[]", [], VARIANT_QUOTES, VARIANT_QUOTES),
        _variant_quote_records("[]", [], NONCANONICAL_QUOTES, "[0.1, 0.2]"),
    )
    for attempt, changed_records in enumerate(changes, start=2):
        changed = _variant_quote_submission(app, repo, variant_quote_pack, records=changed_records)
        outcome = run_variant_quote_scan(records=changed_records, attempt=attempt)
        _assert_quote_scan_conflict(app, changed, outcome, snapshot, attempt)
    _record_quote_scan_evidence(record_property, snapshot)


@NATIVE
def test_run_scan_replays_legacy_empty_list_with_equivalent_canonical_quote(
        app, repo, variant_quote_pack, run_variant_quote_scan, record_property):
    legacy_records = _variant_quote_records([], CANONICAL_QUOTES)
    retry_records = _variant_quote_records("[ ]", NONCANONICAL_QUOTES)
    legacy = _variant_quote_submission(app, repo, variant_quote_pack, records=legacy_records)
    retry = _variant_quote_submission(app, repo, variant_quote_pack, records=retry_records)
    assert retry.payload_sha256() != legacy.payload_sha256()
    scan_id = _seed_old_variant_quote_scan(app, legacy)
    snapshot = _quote_scan_snapshot(app, legacy.scan.key())
    _assert_quote_scan_snapshot(snapshot, legacy, (LEGACY_EMPTY_LIST, NUMBER_ARRAY))

    outcome = run_variant_quote_scan(records=retry_records)
    _assert_quote_scan_replay(app, retry, outcome, snapshot)
    assert _variant_quote_receipts(app) == [("STARTED", None, None), ("SUCCEEDED", scan_id, None)]
    _record_quote_scan_evidence(record_property, snapshot)


@NATIVE
def test_run_scan_legacy_empty_list_rejects_changed_submission(
        app, repo, variant_quote_pack, run_variant_quote_scan, record_property):
    first, second = _variant_quote_records([], CANONICAL_QUOTES)
    records = (first, second)
    legacy = _variant_quote_submission(app, repo, variant_quote_pack, records=records)
    _seed_old_variant_quote_scan(app, legacy)
    snapshot = _quote_scan_snapshot(app, legacy.scan.key())
    _assert_quote_scan_snapshot(snapshot, legacy, (LEGACY_EMPTY_LIST, NUMBER_ARRAY))

    changes = (
        ((dataclasses.replace(first, variant_quotes=VARIANT_QUOTES), second), None),
        ((first, dataclasses.replace(second, median_probability=0.24)), None),
        ((second, first), None),
        (records, "fx47-other-operator"),
    )
    for attempt, (changed_records, operator) in enumerate(changes, start=2):
        changed = _variant_quote_submission(
            app, repo, variant_quote_pack, records=changed_records, operator=operator)
        assert changed.scan.key() == legacy.scan.key()
        assert changed.payload_sha256() != legacy.payload_sha256()
        outcome = run_variant_quote_scan(records=changed_records, operator=operator, attempt=attempt)
        _assert_quote_scan_conflict(app, changed, outcome, snapshot, attempt)
    _record_quote_scan_evidence(record_property, snapshot)


@pytest.fixture
def scan_race_connections(app, app_dsn):
    import psycopg

    from polymarket_alpha_lab.papersim import store_psycopg

    with (store_psycopg.connect_explicit(app_dsn) as contender,
          store_psycopg.connect_explicit(app_dsn) as observer):
        contender.autocommit = observer.autocommit = True
        for connection in (app, contender, observer):
            connection.execute("SET statement_timeout = '20s'")
        for writer in (app, contender):
            writer.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
            with writer.transaction():
                assert writer.execute("SHOW transaction_isolation").fetchone() == ("read committed",)
        assert len({connection.info.backend_pid for connection in (app, contender, observer)}) == 3
        yield app, contender, observer


def _wait_for_scan_key_conflict(observer, holder, contender):
    holder_pid, contender_pid = holder.info.backend_pid, contender.info.backend_pid
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        (blocked,) = observer.execute(
            "SELECT %s = ANY(pg_blocking_pids(%s)) AND EXISTS "
            "(SELECT 1 FROM pg_locks WHERE pid = %s AND locktype = 'transactionid' AND NOT granted)",
            (holder_pid, contender_pid, contender_pid),
        ).fetchone()
        if blocked:
            return
        time.sleep(0.02)
    pytest.fail("the scan contender never blocked on the holder's transaction")


@NATIVE
@pytest.mark.parametrize("scenario", ("commit-equivalent", "commit-divergent", "rollback"))
def test_record_scan_waits_for_legacy_scan_writer(
        app, repo, variant_quote_pack, scan_race_connections, record_property, scenario):
    from polymarket_alpha_lab.papersim import store_psycopg

    holder, contender, observer = scan_race_connections
    legacy = _variant_quote_submission(app, repo, variant_quote_pack, [])
    canonical = _variant_quote_submission(app, repo, variant_quote_pack, "[]")
    operator = "fx47-other-operator" if scenario == "commit-divergent" else None
    replay = _variant_quote_submission(app, repo, variant_quote_pack, "[ ]", operator=operator)
    key = legacy.scan.key()
    assert replay.scan.key() == canonical.scan.key() == key
    assert legacy.payload_sha256() != canonical.payload_sha256()
    assert replay.payload_sha256() != legacy.payload_sha256()

    with ThreadPoolExecutor(max_workers=1) as writers:
        with holder.transaction(force_rollback=(scenario == "rollback")):
            legacy_id = _seed_old_variant_quote_scan(holder, legacy)
            staged = _quote_scan_snapshot(holder, key)
            _assert_quote_scan_snapshot(staged, legacy, (LEGACY_EMPTY_LIST,))
            assert not any(_quote_scan_snapshot(observer, key).values())
            waiting = writers.submit(store_psycopg.record_scan, contender, replay)
            _wait_for_scan_key_conflict(observer, holder, contender)

        if scenario == "commit-divergent":
            with pytest.raises(store.PapersimStoreError) as error:
                waiting.result(timeout=20)
            assert error.value.code == SCAN_PAYLOAD_CONFLICT
            final = _quote_scan_snapshot(observer, key)
            assert final == staged
        else:
            written = waiting.result(timeout=20)
            final = _quote_scan_snapshot(observer, key)
            if scenario == "rollback":
                _assert_quote_scan_snapshot(final, canonical, (EMPTY_ARRAY,))
                assert written == _quote_scan_result(final, inserted=True)
                assert written["scan_id"] != legacy_id
            else:
                assert written == _quote_scan_result(staged, inserted=False)
                assert final == staged
    record_property("fx47_wait_observed", True)
    _record_quote_scan_evidence(record_property, final)


@NATIVE
def test_record_scan_concurrent_new_writers_return_same_ids_and_cash_key(
        app, repo, variant_quote_pack, scan_race_connections, record_property):
    from polymarket_alpha_lab.papersim import store_psycopg

    holder, contender, observer = scan_race_connections
    records = (
        _variant_quote_record([], candidate_key="quote-race-rejected"),
        _variant_quote_record(VARIANT_QUOTES, stale_book=False, candidate_key="quote-race-entered"),
    )
    first = _variant_quote_submission(app, repo, variant_quote_pack, records=records)
    rejected, accepted = first.candidates
    assert rejected.entry is None and accepted.entry is not None
    canonical = dataclasses.replace(first, candidates=(
        dataclasses.replace(rejected, variant_quotes="[]"),
        dataclasses.replace(accepted, variant_quotes=CANONICAL_QUOTES),
    ))
    second = dataclasses.replace(first, candidates=(
        dataclasses.replace(rejected, variant_quotes="[ ]"),
        dataclasses.replace(accepted, variant_quotes=NONCANONICAL_QUOTES),
    ))
    assert first.payload_sha256() != second.payload_sha256()

    key = first.scan.key()
    with ThreadPoolExecutor(max_workers=1) as writers:
        with holder.transaction():
            winner = store_psycopg.record_scan(holder, first)
            staged = _quote_scan_snapshot(holder, key)
            _assert_quote_scan_snapshot(staged, canonical, (EMPTY_ARRAY, NUMBER_ARRAY))
            assert winner == _quote_scan_result(staged, inserted=True)
            assert len(winner["bankroll_event_keys"]) == 1
            assert not any(_quote_scan_snapshot(observer, key).values())
            waiting = writers.submit(store_psycopg.record_scan, contender, second)
            _wait_for_scan_key_conflict(observer, holder, contender)
        assert waiting.result(timeout=20) == {**winner, "inserted": False}
    assert _quote_scan_snapshot(observer, key) == staged
    record_property("fx47_wait_observed", True)
    _record_quote_scan_evidence(record_property, staged)


@NATIVE
def test_record_scan_different_key_preserves_operation_key_violation(
        app, repo, variant_quote_pack, record_property):
    import psycopg

    from polymarket_alpha_lab.papersim import store_psycopg

    first = _variant_quote_submission(app, repo, variant_quote_pack, "[]")
    later = T0 + datetime.timedelta(minutes=1)
    second = dataclasses.replace(
        first, scan=dataclasses.replace(first.scan, started_at=later, finished_at=later))
    key, other_key = first.scan.key(), second.scan.key()
    assert key != other_key and first.scan.operation_key == second.scan.operation_key == QUOTE_OPERATION

    winner = store_psycopg.record_scan(app, first)
    staged = _quote_scan_snapshot(app, key)
    _assert_quote_scan_snapshot(staged, first, (EMPTY_ARRAY,))
    assert winner == _quote_scan_result(staged, inserted=True)
    assert not any(_quote_scan_snapshot(app, other_key).values())
    with pytest.raises(psycopg.errors.UniqueViolation) as error:
        store_psycopg.record_scan(app, second)
    assert error.value.sqlstate == "23505"
    assert error.value.diag.constraint_name == "scan_runs_operation_key_idx"
    assert not any(_quote_scan_snapshot(app, other_key).values())
    assert _quote_scan_snapshot(app, key) == staged
    _record_quote_scan_evidence(record_property, staged)


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
