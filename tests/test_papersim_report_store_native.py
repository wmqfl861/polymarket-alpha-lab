"""Opt-in B4 report proofs through the managed CLI on private PostgreSQL.

Each case uses its own project root and cluster. The qualified native prefix
supplies only binaries. All pipeline rows originate in the production store
and scan paths, with the B1 initial-cash fixture as setup.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
import datetime
from decimal import Decimal
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlencode
import uuid

import pytest

from polymarket_alpha_lab import replay
from polymarket_alpha_lab.papersim import cli as papersim_cli
from polymarket_alpha_lab.papersim import migrations as pm
from polymarket_alpha_lab.papersim import capture, pipeline, queue_policy, report, scan, snapshots, store
from polymarket_alpha_lab.papersim.crypto_model import TerminalProbability
from polymarket_alpha_lab.papersim.gates import NewsCheck
from polymarket_alpha_lab.papersim.optimize.provider import FrozenBaselineProvider
from polymarket_alpha_lab.papersim.pipeline import EntryRequest
from tests.native_port_selection import pick_port
from tests.test_papersim_report import (
    _FIXTURE_FULL_GAME_RULE,
    _nba_fixture,
    _nba_gamma_snapshot,
    _nba_score_snapshot,
    _nba_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get("POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES") == "1"
pytestmark = pytest.mark.skipif(not ENABLED, reason="explicit native papersim proof is opt-in")

T0 = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.UTC)
EXPIRY = datetime.datetime(2026, 12, 31, 0, 0, tzinfo=datetime.UTC)
PROVIDER = FrozenBaselineProvider()

# B2/B3 are wired at the acceptance head. The B4 pure tests separately pin
# STAGE_NOT_WIRED for an absent command.
EMPTY_STAGE_STATUSES = {
    "scan": "NO_DATA",
    "marks": "NO_DATA",
    "outcomes": "NO_DATA",
    "settle": "NO_DATA",
    "evaluate": "NO_DATA",
}


@pytest.fixture
def repo(tmp_path):
    """Prepare a clean Git root with the committed managed migration catalogs."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname="polymarket-alpha-lab"\n', encoding="utf-8")
    (root / ".gitignore").write_text("/.local/\n/runtime/\n", encoding="utf-8")
    shutil.copytree(ROOT / "database", root / "database")
    shutil.copytree(ROOT / "supabase" / "migrations", root / "supabase" / "migrations")
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.invalid"],
        ["config", "user.name", "T"],
        ["add", "--", ".gitignore", "pyproject.toml", "database", "supabase"],
        ["commit", "-qm", "B4 managed fixture"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    return root, head


@pytest.fixture
def database(repo, monkeypatch):
    with _managed_test_postgres(repo[0], monkeypatch) as connections:
        yield connections


@pytest.fixture
def app(database):
    return database[1]


@contextmanager
def _managed_test_postgres(root, monkeypatch):
    # Keep native driver imports inside the opt-in fixture.
    from polymarket_alpha_lab.papersim import store_psycopg
    from polymarket_alpha_lab.project_postgres.runtime import import_runtime_directory
    from polymarket_alpha_lab.project_postgres.server import ProjectPostgres

    prefix = Path(os.environ["POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX"])
    assert prefix.is_absolute() and (prefix / "bin").is_dir()
    for name in tuple(os.environ):
        if name.upper().startswith("PG"):
            monkeypatch.delenv(name)
    version = import_runtime_directory(root, prefix)
    assert version.split(".")[0] == "18"
    postgres = ProjectPostgres(root)
    assert postgres.initialize(port=pick_port())["status"] == "initialized"
    try:
        assert postgres.up()["status"] == "running"
        info = store_psycopg.read_instance(root)
        owner_dsn = store_psycopg.managed_dsn(info, owner=True, repo_root=root)
        with store_psycopg.connect_explicit(owner_dsn, label="PAPERSIM_MIGRATIONS_PG_DSN") as owner:
            migration_count = len(pm.papersim_migration_catalog(root))
            assert pm.apply_pending_migrations(owner, root) == {
                "applied": migration_count, "pending_before": migration_count}
            with store_psycopg.connect_managed(root) as connection:
                connection.autocommit = True
                yield owner, connection
    finally:
        assert postgres.down()["status"] == "stopped"


def _managed_cli(command, root, capsysbinary):
    code = papersim_cli.main([command, "--managed", "--repo-root", str(root)])
    captured = capsysbinary.readouterr()
    if code != 0 or captured.err:
        pytest.fail(f"{command} failed or wrote to stderr (exit {code})", pytrace=False)
    return captured.out


def _report_output(root, capsysbinary):
    output = _managed_cli("report", root, capsysbinary)
    assert output.endswith(b"\n") and output.count(b"\n") == 1
    document = json.loads(output.removesuffix(b"\n").decode("utf-8"))
    for flag in ("paper_only", "report_only", "readonly"):
        assert document[flag] is True
    return output, document


def _assert_integrated_stage_commands(capsysbinary):
    for command in ("capture-marks", "settle"):
        with pytest.raises(SystemExit) as result:
            papersim_cli.main([command, "--help"])
        assert result.value.code == 0
        help_output = capsysbinary.readouterr()
        assert help_output.out.startswith(f"usage: papersim {command} ".encode("utf-8"))


def _report_counts(conn):
    return conn.execute(
        "SELECT (SELECT count(*) FROM papersim.snapshots), "
        "(SELECT count(*) FROM papersim.command_run_events WHERE command = 'report')"
    ).fetchone()


def _saved_report(conn, output, document):
    content = output.removesuffix(b"\n")
    assert content == json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    digest = sha256(content).hexdigest()
    snapshot = snapshots.read_snapshot(conn, digest)
    assert snapshot is not None
    assert snapshot["content"] == content
    assert snapshot["content_sha256"] == digest
    assert snapshot["byte_length"] == len(content)
    assert snapshot["kind"] == report.REPORT_SNAPSHOT_KIND

    as_of = document["as_of"]
    operation_key = report.report_operation_key(
        document["scope"], datetime.datetime.fromisoformat(as_of) if as_of else None, digest)
    receipts = conn.execute(
        "SELECT phase, output_evidence, paper_only, report_only, readonly, "
        "run_event_id, run_event_key, attempt, occurred_at, scan_id, code_commit, "
        "queue_id, scheduled_for, config_digest, input_evidence, error_code "
        "FROM papersim.command_run_events WHERE command = 'report' AND operation_key = %s "
        "ORDER BY run_event_id",
        (operation_key,),
    ).fetchall()
    assert [(row[0], row[1]) for row in receipts] == [
        ("STARTED", []), ("SUCCEEDED", [digest])]
    assert all(row[2:5] == (True, True, True) for row in receipts)
    return operation_key, snapshot, receipts


def _registry_counts(conn):
    return conn.execute(
        "SELECT (SELECT count(*) FROM papersim.model_specs), "
        "(SELECT count(*) FROM papersim.model_role_events), "
        "(SELECT count(*) FROM papersim.optimization_runs)"
    ).fetchone()


def _assert_stage_queue(document, expected):
    assert document["stages"]["evaluate"]["queues"] == [expected], "stage queue identity"


def _assert_evaluation_queue(document, expected):
    assert [q["queue"] for q in document["evaluation"]["queues"]] == [expected], (
        "embedded evaluation queue identity")


def _check_queue_negative_controls(document, expected):
    bare_model_name = deepcopy(document)
    bare_model_name["stages"]["evaluate"]["queues"][0]["model_key"] = "crypto"
    with pytest.raises(AssertionError, match="stage queue identity"):
        _assert_stage_queue(bare_model_name, expected)
    _assert_evaluation_queue(bare_model_name, expected)

    wrong_version = deepcopy(document)
    wrong_version["evaluation"]["queues"][0]["queue"]["model_key"] = "crypto:wrong-version"
    with pytest.raises(AssertionError, match="embedded evaluation queue identity"):
        _assert_evaluation_queue(wrong_version, expected)
    _assert_stage_queue(wrong_version, expected)

    wrong_in_both = deepcopy(wrong_version)
    wrong_in_both["stages"]["evaluate"]["queues"][0]["model_key"] = "crypto:wrong-version"
    with pytest.raises(AssertionError, match="stage queue identity"):
        _assert_stage_queue(wrong_in_both, expected)
    with pytest.raises(AssertionError, match="embedded evaluation queue identity"):
        _assert_evaluation_queue(wrong_in_both, expected)


def _pause_count(conn):
    return conn.execute(
        "SELECT count(*) FROM papersim.queue_control_events "
        "WHERE queue_id = %s AND control_kind = 'PAUSE'",
        ("crypto/eth-terminal",),
    ).fetchone()[0]


def _seed_initial_cash(conn, amount=10_000.0):
    # B1 native-suite precedent: the one setup row the scan needs so its
    # entry is affordable (setup, not pipeline evidence).
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


def _crypto_record(*, queue="crypto/eth-terminal", key="eth-2000-term-yes"):
    request = _eth_request()
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


def _config():
    return scan.ScanConfig(
        team="pal-b1",
        queues=(scan.QueueScanConfig(
            queue="crypto/eth-terminal", lineage="crypto/terminal",
            league_scope=queue_policy.SCOPE_DOMESTIC,
            data_source_version="gamma+prices-v1", model_key="crypto",
            market_type="terminal"),),
    )


def _run_scan(conn, repo_root):
    return scan.run_scan(
        _config(), (_crypto_record(),), conn=conn, repo_root=repo_root,
        provider=PROVIDER,
        now_utc=lambda: T0,
        operation_key="run-scan|pal-b1|test",
    )


def test_empty_store_report_renders_all_stages_missing(app, repo, capsysbinary):
    root, _ = repo
    _assert_integrated_stage_commands(capsysbinary)
    assert _report_counts(app) == (0, 0)

    output, document = _report_output(root, capsysbinary)
    assert document["stage_order"] == ["scan", "marks", "outcomes", "settle", "evaluate"]
    stages = document["stages"]
    assert {stage: row["status"] for stage, row in stages.items()} == EMPTY_STAGE_STATUSES
    for row in stages.values():
        assert row["row_count"] == 0 and row["as_of"] is None
    for stage in ("marks", "outcomes", "settle"):
        assert stages[stage]["coverage_ratio"] is None
    assert document["evaluation"]["queues"] == []
    assert stages["evaluate"]["queues"] == []
    assert document["as_of"] is None

    assert _report_counts(app) == (1, 2)
    operation_key, snapshot, _ = _saved_report(app, output, document)
    assert operation_key == f"report|*|no-data|{snapshot['content_sha256']}"


def test_seeded_report_matches_evaluate_command_bit_for_bit(app, repo, capsysbinary):
    root, head = repo
    _assert_integrated_stage_commands(capsysbinary)
    _seed_initial_cash(app)
    scanned = _run_scan(app, root)
    assert scanned["inserted"] is True and scanned["candidate_ids"]
    assert scanned["code_commit"] == head
    (candidate_key, candidate_id), = scanned["candidate_ids"].items()
    assert candidate_key == "eth-2000-term-yes"
    assert candidate_id > 0
    row = app.execute(
        "SELECT c.model_name, c.model_version, c.event_key, c.event_start_at, "
        "c.conclusion, s.gate_version "
        "FROM papersim.candidates c JOIN papersim.scan_runs s USING (scan_id) "
        "WHERE c.scan_id = %s AND c.candidate_id = %s",
        (scanned["scan_id"], candidate_id),
    ).fetchone()
    assert row is not None
    model_name, model_version, event_key, event_start, conclusion, gate_version = row
    assert model_name == "crypto"
    assert model_version == pipeline.MODEL_VERSION_CRYPTO == "crypto-r19+fx13-2026-11-12"
    assert event_key == "crypto:ETH|2026-12-31"
    assert event_start == EXPIRY
    assert conclusion == "ACCEPTED"
    assert gate_version == pipeline.GATE_VERSION

    assert _registry_counts(app) == (0, 0, 0)
    _, document = _report_output(root, capsysbinary)
    stages = document["stages"]
    assert {stage: row["status"] for stage, row in stages.items()} == {
        **EMPTY_STAGE_STATUSES, "scan": "OK", "evaluate": "OK"}
    assert stages["scan"]["row_count"] == 1
    assert stages["scan"]["candidates"] == 1
    assert stages["scan"]["entries"] == 1
    assert stages["scan"]["last_scan"]["scan_id"] == scanned["scan_id"]
    assert stages["scan"]["last_scan"]["code_commit"] == head
    assert stages["marks"]["row_count"] == stages["outcomes"]["row_count"] == 0
    assert stages["settle"]["row_count"] == 0
    assert stages["settle"]["entries_total"] == 1
    assert stages["settle"]["entries_settled"] == 0
    assert stages["settle"]["coverage_ratio"] == 0.0
    assert stages["settle"]["basketball_finals"] == []

    expected_queue = {
        "model_key": f"{model_name}:{model_version}",
        "market_type": "terminal",
        "gate_version": gate_version,
    }
    _assert_stage_queue(document, expected_queue)
    _assert_evaluation_queue(document, expected_queue)
    _check_queue_negative_controls(document, expected_queue)

    by_operation = {(row["command"], row["operation_key"]): row
                    for row in document["receipts"]["last_phase_by_operation"]}
    assert by_operation[("run-scan", "run-scan|pal-b1|test")]["last_phase"] == "SUCCEEDED"
    assert "report" not in document["receipts"]["counts_by_command"]

    evaluate_output = _managed_cli("evaluate", root, capsysbinary)
    embedded = json.dumps(
        document["evaluation"], sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
    assert embedded == evaluate_output
    assert json.loads(evaluate_output)["queues"]
    assert _registry_counts(app) == (0, 0, 0)
    assert not any("registry" in key or "optimize" in key for key in document)
    assert "registry" not in json.dumps(document["stages"])


def test_second_invocation_duplicates_nothing(app, repo, capsysbinary):
    from polymarket_alpha_lab.papersim import store_psycopg

    root, _ = repo
    _seed_initial_cash(app)
    _run_scan(app, root)
    assert _report_counts(app) == (0, 0)

    first_output, first_document = _report_output(root, capsysbinary)
    first_evidence = _saved_report(app, first_output, first_document)
    first_key, first_snapshot, _ = first_evidence
    assert _report_counts(app) == (1, 2)

    assert datetime.datetime.now(datetime.UTC) > first_snapshot["fetched_at"]
    second_output, second_document = _report_output(root, capsysbinary)
    assert second_output == first_output
    assert _saved_report(app, second_output, second_document) == first_evidence
    assert _report_counts(app) == (1, 2)

    entry_id, stake, price, shares = app.execute(
        "SELECT entry_id, stake, price, shares FROM papersim.entries ORDER BY entry_id"
    ).fetchone()
    settled = store_psycopg.settlement_record(app, store.SettlementRecord(
        entry_id=int(entry_id),
        classification="NATURAL",
        outcome_label="YES",
        win_loss="win",
        proceeds=Decimal(shares),
        pnl_gross=Decimal(shares) - Decimal(stake),
        fee_total=0,
        fee_source="schedule",
        pnl_net=Decimal(shares) - Decimal(stake),
        evidence="b4-native-test late evidence",
        confirmed_by="b4-native-test",
        confirmed_at=T0 + datetime.timedelta(minutes=20)))
    assert settled["inserted"] is True

    third_output, third_document = _report_output(root, capsysbinary)
    third_key, third_snapshot, _ = _saved_report(app, third_output, third_document)
    assert third_output != first_output
    assert third_snapshot["content_sha256"] != first_snapshot["content_sha256"]
    assert third_key != first_key
    assert _report_counts(app) == (2, 4)
    assert third_document["stages"]["settle"]["entries_settled"] == 1
    assert third_document["stages"]["settle"]["coverage_ratio"] == 1.0
    evidence_at = (T0 + datetime.timedelta(minutes=20)).isoformat()
    assert third_document["stages"]["settle"]["as_of"] == evidence_at
    assert third_document["stages"]["evaluate"]["as_of"] == evidence_at
    assert third_document["as_of"] == evidence_at
    assert third_document["stages"]["scan"] == first_document["stages"]["scan"]
    assert third_document["stages"]["marks"] == first_document["stages"]["marks"]
    assert third_document["stages"]["outcomes"] == first_document["stages"]["outcomes"]
    assert _saved_report(app, first_output, first_document) == first_evidence
    _read_report_after_close(root, first_snapshot["content_sha256"], first_output.removesuffix(b"\n"))
    _read_report_after_close(root, third_snapshot["content_sha256"], third_output.removesuffix(b"\n"))


def test_report_reads_observe_one_repeatable_read_snapshot(app, repo, capsysbinary, monkeypatch):
    from polymarket_alpha_lab.papersim import store_psycopg

    root, _ = repo
    ordinary_loader = papersim_cli._evaluation_candidates_in_snapshot
    observed = []

    def observe_snapshot(reader, **kwargs):
        assert reader.execute("SHOW transaction_isolation").fetchone() == ("repeatable read",)
        assert reader.execute("SHOW transaction_read_only").fetchone() == ("on",)
        assert _pause_count(reader) == 0
        with store_psycopg.connect_managed(root) as writer:
            assert writer.info.backend_pid != reader.info.backend_pid
            recorded = store_psycopg.record_queue_control_event(writer, store.QueueControlSubmission(
                queue_id="crypto/eth-terminal",
                control_kind="PAUSE",
                rule=store.QUEUE_CONTROL_RULE_FX17,
                rule_version=queue_policy.QUEUE_POLICY_RULES_VERSION,
                metrics={"concurrent": True},
                observed_at=T0))
            assert recorded["inserted"] is True
        # The writer context has committed. A fresh application session can
        # already see the row while the production reader remains on its snapshot.
        assert _pause_count(app) == 1
        assert _pause_count(reader) == 0
        observed.append(reader.info.backend_pid)
        return ordinary_loader(reader, **kwargs)

    with monkeypatch.context() as hook:
        hook.setattr(papersim_cli, "_evaluation_candidates_in_snapshot", observe_snapshot)
        before_output, before = _report_output(root, capsysbinary)
    assert len(observed) == 1
    assert before["queue_controls"]["row_count"] == 0
    assert before["queue_controls"]["latch_by_queue"] == {}

    after_output, after = _report_output(root, capsysbinary)
    assert after["queue_controls"]["row_count"] == 1
    assert after["queue_controls"]["last"]["control_kind"] == "PAUSE"
    assert after["queue_controls"]["last"]["queue_id"] == "crypto/eth-terminal"
    assert after["queue_controls"]["latch_by_queue"]["crypto/eth-terminal"] == {
        "pause_review": True, "auto_stop": False}
    assert _report_counts(app) == (2, 4)
    assert _saved_report(app, before_output, before)[0] != _saved_report(app, after_output, after)[0]


def _register_basketball_source(conn, *, content, kind, source, fetched_at=None):
    registration = snapshots.SnapshotRegistration(
        content=content, kind=kind, source=source,
        fetched_at=fetched_at or datetime.datetime.now(datetime.UTC))
    registered = snapshots.register_snapshot(conn, registration)
    assert registered["kind"] == kind
    assert registered["content_sha256"] == sha256(content).hexdigest()
    assert registered["byte_length"] == len(content)
    return registered["content_sha256"]


def _basketball_candidate(market, parent, token_index, *, key, refs=(), decision=None,
                          event_key=None):
    tokens = json.loads(market["clobTokenIds"])
    tip = datetime.datetime.fromisoformat(market["gameStartTime"])
    evaluated_at = tip - datetime.timedelta(hours=1)
    fields = dict(
        team="b4-nba", candidate_key=key, side="YES", market_kind="other",
        market_type=market["sportsMarketType"], model_name="nba",
        market_ref_kind="condition_id", market_id=market["id"], market_slug=market["slug"],
        condition_id=market["conditionId"], yes_token_id=tokens[token_index],
        no_token_id=tokens[1 - token_index],
        event_key=event_key or store.candidate_event_key(
            market_class="sports", gamma_event_id=parent["id"]),
        event_start_at=tip, evaluated_at=evaluated_at)
    if decision:
        # Exercise the real MarketRecord -> bind_candidate DECISION path.
        # The fixture supplies a rejected replay decision and no entry.
        market_record = scan.MarketRecord(
            queue="nba/b4-native", candidate_key=key, team="b4-nba", side="YES", market_kind="other",
            lineage="nba", model_name="nba", evaluated_at=evaluated_at,
            condition_id=market["conditionId"], market_id=market["id"], market_slug=market["slug"],
            decision_snapshot_sha256=decision["content_sha256"],
            source_kind=decision["kind"], source_ref=decision["source"],
            yes_token_id=tokens[token_index], no_token_id=tokens[1 - token_index])
        request = EntryRequest(
            market_id=market_record.market_id, question=market["question"],
            fixture=parent["title"], market_kind="other", side="yes", book=[],
            book_time=evaluated_at)
        rejected = pipeline.decide_entry(request)
        assert rejected.accepted is False
        return replay.bind_candidate(
            request, rejected, **fields, snapshot_refs=refs,
            decision_snapshot_sha256=market_record.decision_snapshot_sha256,
            source_kind=market_record.source_kind, source_ref=market_record.source_ref)
    fields.pop("market_ref_kind")
    return store.CandidateSubmission(
        **fields, model_version="b4-native", snapshot_refs=json.dumps(refs))


def _record_basketball_scan(app, repo, candidates, *, operation_key="run-scan|b4-nba|proof"):
    from polymarket_alpha_lab.papersim import store_psycopg

    scan_end = max(candidate.evaluated_at for candidate in candidates)
    scan_row = store.ScanRunRecord(
        team="b4-nba", started_at=min(candidate.evaluated_at for candidate in candidates),
        finished_at=scan_end, code_commit=repo[1], gate_version=pipeline.GATE_VERSION,
        operation_key=operation_key)
    recorded = store_psycopg.record_scan(app, store.ScanSubmission(scan=scan_row, candidates=candidates))
    assert recorded["inserted"] and len(recorded["candidate_ids"]) == len(candidates)
    return recorded


def _read_report_after_close(root, digest, expected):
    readback = subprocess.run(
        [sys.executable, "-c", """
import sys
from pathlib import Path
from polymarket_alpha_lab.papersim import snapshots, store_psycopg

with store_psycopg.connect_managed(Path(sys.argv[1])) as connection:
    row = snapshots.read_snapshot(connection, sys.argv[2])
assert row["kind"] == "papersim-report"
assert row["content_sha256"] == sys.argv[2]
sys.stdout.buffer.write(row["content"])
""", str(root), digest], cwd=ROOT, capture_output=True, timeout=30)
    assert readback.returncode == 0 and not readback.stderr, "independent archive readback failed"
    assert readback.stdout == expected, "archived bytes differ from the closed command's stdout"


def test_registered_basketball_decision_explicit_mark_and_outcome_bindings(app, repo, capsysbinary, record_property):
    """Native synthetic official-shape witnesses for all reference carriers."""
    from polymarket_alpha_lab.papersim import store_psycopg

    root, _ = repo
    market, espn = _nba_fixture()
    parent, = market["events"]
    gamma = _nba_gamma_snapshot(market)
    scoreboard = _nba_score_snapshot(espn)
    for row in (gamma, scoreboard):
        _register_basketball_source(
            app, **{key: row[key] for key in ("content", "kind", "source", "fetched_at")})
    rejected = (
        _basketball_candidate(market, parent, 1, key="decision", decision=gamma),
        _basketball_candidate(market, parent, 1, key="explicit-mark"),
        _basketball_candidate(market, parent, 1, key="prepared-pack-only"),
        _basketball_candidate(market, parent, 1, key="unregistered-digest"),
    )
    recorded = _record_basketball_scan(app, repo, rejected)
    chosen = rejected[0]
    deadline = T0 + datetime.timedelta(minutes=4)
    store_psycopg.record_market_outcome(app, store.MarketOutcomeObservation(
        condition_id=chosen.condition_id, market_id=chosen.market_id, market_slug=chosen.market_slug,
        yes_token_id=chosen.yes_token_id, no_token_id=chosen.no_token_id,
        source_kind="operator_attestation", source_ref="b4-official-shape-fixture",
        source_snapshot_sha256=scoreboard["content_sha256"], outcome="YES",
        observed_at=T0, confirmed_at=deadline))
    marks = []
    for candidate in rejected[1:]:
        observation = {
            "candidate_key": candidate.candidate_key, "scan_id": recorded["scan_id"],
            "sampling_kind": "CLOSE", "observed_at": (T0 - datetime.timedelta(minutes=2)).isoformat(),
            "market_ref_kind": "condition_id", "market_ref": candidate.condition_id,
            "token_ref": candidate.yes_token_id, "side": candidate.side, "last": 1.0,
            "source_kind": "gamma_close", "source_ref": gamma["source"],
        }
        if candidate.candidate_key != "prepared-pack-only":
            observation["snapshot_sha256"] = (
                gamma["content_sha256"] if candidate.candidate_key == "explicit-mark" else "0" * 64)
        marks.append(observation)
    capture.run_capture(
        capture.CaptureConfig(team="b4-nba"),
        (capture.MarkPack("b4-nba-shape-fixture.json", json.dumps(marks).encode()),),
        conn=app, repo_root=root, now_utc=lambda: T0, operation_key="capture-marks|b4-nba|fixture")

    output, document = _report_output(root, capsysbinary)
    settle = document["stages"]["settle"]
    positives = [recorded["candidate_ids"][key] for key in ("decision", "explicit-mark")]
    assert [row["candidate_id"] for row in settle["basketball_finals"]] == positives
    assert all(row["settlement"] == "win" and row["overtime_periods"] == 1
               for row in settle["basketball_finals"])
    assert settle["basketball_evidence"]["status"] == "PARTIAL"
    failures = settle["basketball_evidence"]["unavailable_positions"]
    assert [row["candidate_id"] for row in failures] == [
        recorded["candidate_ids"][key] for key in ("prepared-pack-only", "unregistered-digest")]
    assert all(row["reason"] == "missing_terms_or_reference" for row in failures)
    assert settle["as_of"] == document["as_of"] == deadline.isoformat()
    assert document["stages"]["outcomes"]["as_of"] == deadline.isoformat()
    assert document["stages"]["marks"]["row_count"] == 3
    assert not app.execute("SELECT 1 FROM papersim.settlements").fetchone()
    _, snapshot, _ = _saved_report(app, output, document)
    _read_report_after_close(root, snapshot["content_sha256"], output.removesuffix(b"\n"))
    record_property("basketball_binding_evidence", json.dumps({
        "fixture": "synthetic_official_shape",
        "as_of": document["as_of"],
        "archive_digest": snapshot["content_sha256"],
        "decision_and_explicit_mark": positives,
        "prepared_pack_and_unregistered_digest": failures,
        "independent_readback": True,
    }, sort_keys=True))


_HISTORICAL_NBA_ROOT = "POLYMARKET_ALPHA_LAB_B4_HISTORICAL_NBA_ROOT"
_HISTORICAL_OT_GAMMA = "POLYMARKET_ALPHA_LAB_B4_HISTORICAL_OT_GAMMA"
_PRIVATE_SOURCE_VARIABLES = (_HISTORICAL_NBA_ROOT, _HISTORICAL_OT_GAMMA)


@pytest.fixture(params=(
    ("portable", "historical") if any(key in os.environ for key in _PRIVATE_SOURCE_VARIABLES)
    else ("portable",)
))
def nba_source_case(request, record_property):
    supplied = tuple(os.environ.get(key) for key in _PRIVATE_SOURCE_VARIABLES)
    if any(value is not None for value in supplied) and not all(supplied):
        pytest.fail("configure both private NBA source inputs together", pytrace=False)
    record_property("nba_source_case", request.param)
    record_property("historical_inputs_configured", all(supplied))
    return request.param


def _checked_evidence(content, length, digest):
    if len(content) != length or sha256(content).hexdigest() != digest:
        pytest.fail("approved historical source bytes differ", pytrace=False)
    return content


def _ot_gamma_original():
    capture_path = Path(os.environ[_HISTORICAL_OT_GAMMA])
    response = _checked_evidence(
        gzip.decompress(capture_path.read_bytes()),
        19_225, "5f29c395d9688740eaf08b03f44eb59454267475ffc9631016135f99aaec58ca")
    slugs = [base + suffix for base in ("nba-orl-gsw-2026-11-07", "nba-gsw-orl-2026-11-07")
             for suffix in ("", "-spread-home-7pt5", "-total-220pt5")]
    query = urlencode([("closed", "true"), *(("slug", slug) for slug in slugs)])
    source = f"https://gamma-api.polymarket.com/markets?{query}"
    assert capture_path.name == f"{sha256(source.encode()).hexdigest()}.json.gz"
    return response, source


def _historical_projection(nba_root):
    parent_digest = "36a30c25c7d5096c9e21e38eb1ed03672c59dce0a596252d1e1194634144ab79"
    page = _checked_evidence((nba_root / "round14/raw/gamma_nba.json").read_bytes(),
                             23_303_969, parent_digest)
    event, = (event for event in json.loads(page)["data"] if event["id"] == "1197295")
    markets = [market for market in event["markets"] if market["id"] in ("5765056", "5765057")]
    assert [market["id"] for market in markets] == ["5765056", "5765057"]
    projected = json.dumps({**event, "markets": markets}, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False).encode("utf-8")
    _checked_evidence(projected, 11_506, "e2b9d733a2bdb77ea5f801e7c8561a084e44e9c1d8891d44a6ef7b8f1f351db7")
    source = f"round14/raw/gamma_nba.json#sha256={parent_digest}&event=1197295&markets=5765056,5765057"
    return projected, source, event, markets


def _historical_espn_cache(nba_root, event_id, length, digest):
    filename = f"summary_{event_id}.json"
    content = _checked_evidence((nba_root / "nba/cache" / filename).read_bytes(), length, digest)
    payload = json.loads(content)
    assert payload["header"]["id"] == event_id
    if json.dumps(payload, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") != content:
        pytest.fail("summary source differs from the cache producer's serialization", pytrace=False)
    return content, f"papersim.espn_nba.fetch_game_summary:{filename}"


def _portable_nba_full_game_finals(app, repo, capsysbinary, record_property):
    """Use reduced official resource shapes and synthetic game observations."""
    root, _ = repo
    market, espn = _nba_fixture()
    parent, = market["events"]
    spread = dict(
        market, id="908", conditionId="0x" + "b" * 64,
        slug=market["slug"] + "-spread-home-5pt5", sportsMarketType="spreads",
        line=-5.5, question="Spread: Warriors (-5.5)", outcomePrices='["0","1"]',
        clobTokenIds='["1000000011","1000000012"]',
        description="\n".join((
            'If Warriors wins by more than 5.5 points, the market will resolve to "Warriors".',
            'If Magic loses by fewer than 5.5 points, or wins the game outright, the market will resolve to "Magic".',
            _FIXTURE_FULL_GAME_RULE,
        )))
    total = dict(
        market, id="906", conditionId="0x" + "c" * 64,
        slug=market["slug"] + "-total-229pt5", sportsMarketType="totals",
        line=229.5, question="Magic vs. Warriors: O/U 229.5",
        outcomes='["Over","Under"]', outcomePrices='["1","0"]',
        clobTokenIds='["1000000021","1000000022"]',
        description="\n".join((
            'If the combined final score is more than 229.5 points, the market will resolve to "Over".',
            'If the combined final score is less than 229.5 points, the market will resolve to "Under".',
            _FIXTURE_FULL_GAME_RULE,
        )))
    unsupported = dict(
        total, id="907", conditionId="0x" + "d" * 64, slug=market["slug"] + "-1h-total-229pt5",
        clobTokenIds='["1000000031","1000000032"]',
        description=total["description"].replace(_FIXTURE_FULL_GAME_RULE, ""))
    gamma_page = {
        "$schema": "https://gamma-api.polymarket.com/schemas/EventsPagination.json",
        "data": [{**parent, "markets": [
            {name: value for name, value in row.items() if name != "events"}
            for row in (market, spread, total, unsupported)
        ]}],
        "pagination": {"hasMore": False, "totalResults": 1},
    }
    gamma = _nba_snapshot(
        gamma_page, "polymarket-gamma",
        "https://gamma-api.polymarket.com/events/pagination?tag_slug=nba")
    score = _nba_score_snapshot(espn)
    partial = deepcopy(espn)
    competition, = partial["header"]["competitions"]
    competition["status"]["type"] = {"completed": False, "state": "in", "name": "STATUS_IN_PROGRESS"}
    for index, competitor in enumerate(competition["competitors"]):
        competitor["score"] = str(80 + index)
    incomplete = _nba_score_snapshot(partial)
    for row in (gamma, score, incomplete):
        _register_basketball_source(
            app, **{name: row[name] for name in ("content", "kind", "source", "fetched_at")})
    candidates = tuple(
        _basketball_candidate(row, parent, 1, key=key,
                              refs=(gamma["content_sha256"], evidence["content_sha256"]))
        for row, key, evidence in (
            (market, "fixture-moneyline", score), (spread, "fixture-spread", score),
            (total, "fixture-total", score), (unsupported, "fixture-unsupported", score),
            (total, "fixture-incomplete", incomplete))
    )
    recorded = _record_basketball_scan(app, repo, candidates)
    output, document = _report_output(root, capsysbinary)
    settle = document["stages"]["settle"]
    finals = {row["market_ref"]: row for row in settle["basketball_finals"]}
    assert set(finals) == {"903", "906", "908"}
    assert {key: (row["side"], row["line"], row["settlement"]) for key, row in finals.items()} == {
        "903": ("home", None, "win"), "906": ("Under", 229.5, "loss"), "908": ("home", -5.5, "win")}
    for final in finals.values():
        assert final["regulation_score"] == {"home": 110, "away": 110}
        assert final["final_score"] == {"home": 118, "away": 112}
        assert final["overtime_periods"] == 1 and final["final_includes_overtime"] is True
        assert final["provenance"]["gamma"] == {
            "kind": gamma["kind"], "content_sha256": gamma["content_sha256"]}
        assert final["provenance"]["scoreboard"] == {
            "kind": score["kind"], "content_sha256": score["content_sha256"]}
    failures = {row["candidate_id"]: row["reason"]
                for row in settle["basketball_evidence"]["unavailable_positions"]}
    assert failures == {
        recorded["candidate_ids"]["fixture-unsupported"]: "unsupported_period",
        recorded["candidate_ids"]["fixture-incomplete"]: "not_completed",
    }
    assert settle["basketball_evidence"]["status"] == "PARTIAL"
    assert settle["as_of"] == document["as_of"] == score["fetched_at"].isoformat()
    assert not app.execute("SELECT 1 FROM papersim.settlements").fetchone()
    archive = _saved_report(app, output, document)
    digest = archive[1]["content_sha256"]
    _read_report_after_close(root, digest, output.removesuffix(b"\n"))
    assert _report_output(root, capsysbinary) == (output, document)
    assert _saved_report(app, output, document) == archive
    record_property("portable_basketball_evidence", json.dumps({
        "fixture": "synthetic_official_shapes",
        "archive_digest": digest,
        "finals": list(finals.values()),
        "unavailable_reasons": list(failures.values()),
        "independent_readback": True,
    }, sort_keys=True))


def test_managed_nba_full_game_finals_from_allowed_sources(nba_source_case, app, repo, capsysbinary, record_property):
    if nba_source_case == "portable":
        _portable_nba_full_game_finals(app, repo, capsysbinary, record_property)
        return

    root, _ = repo
    nba_root = Path(os.environ[_HISTORICAL_NBA_ROOT])
    raw, gamma_source = _ot_gamma_original()
    raw_markets = {row["id"]: row for row in json.loads(raw)}
    gamma = _register_basketball_source(app, content=raw, kind="polymarket-gamma", source=gamma_source)
    projected, projection_source, parent, paired = _historical_projection(nba_root)
    projection = _register_basketball_source(
        app, content=projected, kind="polymarket-gamma-event-projection", source=projection_source)
    ot_bytes, ot_source = _historical_espn_cache(
        nba_root, "401909942", 873_944, "9ad12f75b4a8fa32587db75de02f2f2812d71abe0d24be4468a82dbe1100dc38")
    totals_bytes, totals_source = _historical_espn_cache(
        nba_root, "401909093", 875_922, "5c408eb0cded2b6076fd6fc2d691d7875ecc8246e6393a0ebebbef6060bd589e")
    score = _register_basketball_source(app, content=ot_bytes, kind="espn-nba-summary-cache", source=ot_source)
    other_score = _register_basketball_source(
        app, content=totals_bytes, kind="espn-nba-summary-cache", source=totals_source)

    # This source's filename says "final", while its event is still live.
    partial_bytes = _checked_evidence((nba_root / "round13/raw/espn_nba_20261022_final.json").read_bytes(),
                                      33_994, "1accadfcea27dccb13a984779e50486bd4a055a9532eeb89ff7d51d5bc6f4edb")
    partial_event, = (event for event in json.loads(partial_bytes)["events"] if event["id"] == "401909093")
    assert partial_event["status"]["type"]["completed"] is False
    assert {row["homeAway"]: row["score"] for row in partial_event["competitions"][0]["competitors"]} == {
        "home": "87", "away": "92"}
    partial = _register_basketball_source(
        app, content=partial_bytes, kind="espn-nba-scoreboard",
        source="round13/raw/espn_nba_20261022_final.json")
    ml, spread_without_ot, total_without_ot = (raw_markets[key] for key in ("6428610", "6767281", "6768491"))
    spread, total = paired
    candidates = tuple(
        _basketball_candidate(market, market["events"][0], index, key=market["id"], refs=(gamma, score))
        for market, index in ((ml, 1), (spread_without_ot, 0), (total_without_ot, 0))
    ) + (
        _basketball_candidate(spread, parent, 0, key="projection-spread", refs=(projection, other_score)),
        _basketball_candidate(
            total, parent, 1, key="projection-under", refs=(projection, other_score),
            event_key=store.candidate_event_key(market_class="sports", league="nba",
                                                home="PHI", away="CLE", kickoff_day="2026-10-22")),
        _basketball_candidate(total, parent, 1, key="incomplete-scoreboard", refs=(projection, partial)),
    )
    recorded = _record_basketball_scan(app, repo, candidates)

    command = subprocess.run(
        [sys.executable, "-m", "polymarket_alpha_lab.papersim.cli", "report",
         "--managed", "--repo-root", str(root)], cwd=ROOT, capture_output=True, timeout=30)
    assert command.returncode == 0 and not command.stderr, "historical managed report command failed"
    output = command.stdout
    if any(secret.encode() in output for candidate in candidates
           for secret in (candidate.condition_id, candidate.yes_token_id, candidate.no_token_id)):
        pytest.fail("historical output exposed a token or condition identifier", pytrace=False)
    document = json.loads(output)
    settle = document["stages"]["settle"]
    final = {row["market_ref"]: row for row in settle["basketball_finals"]}
    assert set(final) == {"6428610", "5765056", "5765057"}
    ot = final["6428610"]
    assert ot["final_score"] == {"home": 126, "away": 119}
    assert ot["regulation_score"] == {"home": 110, "away": 110} and ot["overtime_periods"] == 1
    assert (ot["home_team"], ot["away_team"], ot["settlement"]) == ("GSW", "ORL", "win")
    assert ot["provenance"]["gamma"] == {"kind": "polymarket-gamma", "content_sha256": gamma}
    assert ot["provenance"]["scoreboard"] == {"kind": "espn-nba-summary-cache", "content_sha256": score}
    assert (ot["provenance"]["gamma_event_id"], ot["provenance"]["gamma_game_id"],
            ot["provenance"]["espn_event_id"]) == ("1284243", "20024070", "401909942")
    under = final["5765057"]
    assert under["final_score"] == {"home": 114, "away": 106} and under["overtime_periods"] == 0
    assert (under["side"], under["line"], under["settlement"]) == ("Under", 236.5, "win")
    assert under["provenance"]["gamma"] == {
        "kind": "polymarket-gamma-event-projection", "content_sha256": projection,
        "parent_content_sha256": "36a30c25c7d5096c9e21e38eb1ed03672c59dce0a596252d1e1194634144ab79"}
    assert under["provenance"]["scoreboard"] == {
        "kind": "espn-nba-summary-cache", "content_sha256": other_score}
    assert (under["provenance"]["gamma_event_id"], under["provenance"]["gamma_game_id"],
            under["provenance"]["espn_event_id"]) == ("1197295", "20023885", "401909093")
    assert (final["5765056"]["side"], final["5765056"]["line"], final["5765056"]["settlement"]) == (
        "home", -2.5, "win")
    by_id = {row["candidate_id"]: row["reason"] for row in settle["basketball_evidence"]["unavailable_positions"]}
    assert by_id == {
        recorded["candidate_ids"]["6767281"]: "unsupported_period",
        recorded["candidate_ids"]["6768491"]: "unsupported_period",
        recorded["candidate_ids"]["incomplete-scoreboard"]: "not_completed",
    }
    assert settle["basketball_evidence"]["status"] == "PARTIAL"
    assert under["evidence_as_of"] == "2026-10-23T02:11:10.159381+00:00"
    assert ot["evidence_as_of"] == settle["as_of"] == document["as_of"] == "2026-11-08T04:43:18.503639+00:00"
    assert not app.execute("SELECT 1 FROM papersim.settlements").fetchone()
    evidence = _saved_report(app, output, document)
    _read_report_after_close(root, evidence[1]["content_sha256"], output.removesuffix(b"\n"))
    rerun = subprocess.run(command.args, cwd=ROOT, capture_output=True, timeout=30)
    assert rerun.returncode == 0 and not rerun.stderr and rerun.stdout == output
    assert _saved_report(app, rerun.stdout, document) == evidence
    record_property("historical_basketball_evidence", json.dumps({
        "archive_digest": evidence[1]["content_sha256"],
        "settlement_as_of": settle["as_of"],
        "finals": settle["basketball_finals"],
        "unavailable_positions": settle["basketball_evidence"]["unavailable_positions"],
        "incomplete_scoreboard_digest": partial,
        "independent_readback": True,
        "byte_identical_replay": True,
    }, sort_keys=True))


def _portable_espn_cache_relabel(app, repo, capsysbinary, record_property):
    root, _ = repo
    market, espn = _nba_fixture()
    gamma = _nba_gamma_snapshot(market)
    score = _nba_score_snapshot(espn)
    gamma_digest = _register_basketball_source(
        app, **{name: gamma[name] for name in ("content", "kind", "source", "fetched_at")})
    # The synthetic ESPN document uses the official summary fields and the
    # adapter cache producer's exact serialization.
    cache = json.dumps(espn, indent=2, sort_keys=True, allow_nan=False).encode("utf-8")
    incorrect_claim = _register_basketball_source(
        app, content=cache, kind="espn-nba", source=score["source"], fetched_at=score["fetched_at"])
    candidate = _basketball_candidate(
        market, market["events"][0], 1, key="fixture-cache-relabel", refs=(gamma_digest, incorrect_claim))
    _record_basketball_scan(app, repo, (candidate,))
    output, document = _report_output(root, capsysbinary)
    settle = document["stages"]["settle"]
    assert settle["basketball_finals"] == [] and settle["as_of"] is None
    assert settle["basketball_evidence"]["status"] == "UNAVAILABLE"
    assert settle["basketball_evidence"]["unavailable_positions"][0]["reason"] == "missing_terms_or_reference"
    assert not app.execute("SELECT 1 FROM papersim.settlements").fetchone()
    _, snapshot, _ = _saved_report(app, output, document)
    _read_report_after_close(root, snapshot["content_sha256"], output.removesuffix(b"\n"))
    record_property("portable_cache_relabel", json.dumps({
        "fixture": "synthetic_summary_cache_serialization",
        "archive_digest": snapshot["content_sha256"],
        "rejected_content_sha256": incorrect_claim,
        "unavailable_positions": settle["basketball_evidence"]["unavailable_positions"],
        "independent_readback": True,
    }, sort_keys=True))


def test_managed_espn_cache_cannot_supply_a_raw_summary(nba_source_case, app, repo, capsysbinary, record_property):
    if nba_source_case == "portable":
        _portable_espn_cache_relabel(app, repo, capsysbinary, record_property)
        return

    root, _ = repo
    raw, gamma_source = _ot_gamma_original()
    market, = [row for row in json.loads(raw) if row["id"] == "6428610"]
    gamma = _register_basketball_source(app, content=raw, kind="polymarket-gamma", source=gamma_source)
    cache, _ = _historical_espn_cache(
        Path(os.environ[_HISTORICAL_NBA_ROOT]), "401909942", 873_944,
        "9ad12f75b4a8fa32587db75de02f2f2812d71abe0d24be4468a82dbe1100dc38")
    incorrect_claim = _register_basketball_source(
        app, content=cache, kind="espn-nba",
        source="https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=401909942")
    candidate = _basketball_candidate(
        market, market["events"][0], 1, key="rebadged-cache", refs=(gamma, incorrect_claim))
    _record_basketball_scan(app, repo, (candidate,))
    output, document = _report_output(root, capsysbinary)
    settle = document["stages"]["settle"]
    assert settle["basketball_finals"] == []
    assert settle["as_of"] is None
    assert settle["basketball_evidence"]["unavailable_positions"][0]["reason"] == "missing_terms_or_reference"
    _, snapshot, _ = _saved_report(app, output, document)
    _read_report_after_close(root, snapshot["content_sha256"], output.removesuffix(b"\n"))
    record_property("historical_cache_relabel", json.dumps({
        "archive_digest": snapshot["content_sha256"],
        "rejected_content_sha256": incorrect_claim,
        "unavailable_positions": settle["basketball_evidence"]["unavailable_positions"],
        "independent_readback": True,
    }, sort_keys=True))


def _archive_rows(conn, operation_key, digest):
    """Read only evidence for this operation so it can also be checked in a child."""
    rows = conn.execute(
        "SELECT attempt, phase, run_event_id, occurred_at, output_evidence, "
        "run_event_key, config_digest, paper_only, report_only, readonly "
        "FROM papersim.command_run_events WHERE command = 'report' AND operation_key = %s "
        "ORDER BY run_event_id", (operation_key,),
    ).fetchall()
    snapshot = snapshots.read_snapshot(conn, digest)
    if snapshot is not None:
        snapshot["content"] = snapshot["content"].decode("utf-8")
        snapshot["fetched_at"] = snapshot["fetched_at"].isoformat()
    return {
        "receipts": [{"attempt": attempt, "phase": phase, "run_event_id": row_id,
                      "occurred_at": at.isoformat(), "output_evidence": output,
                      "run_event_key": event_key, "config_digest": config_digest,
                      "flags": [paper_only, report_only, readonly]}
                     for (attempt, phase, row_id, at, output, event_key, config_digest,
                          paper_only, report_only, readonly) in rows],
        "snapshot": snapshot,
    }


def _archive_ack(saved):
    return {
        **{phase: {"run_event_id": saved[phase]["run_event_id"],
                   "occurred_at": saved[phase]["occurred_at"].isoformat()}
           for phase in ("started", "succeeded")},
        "snapshot": {"content_sha256": saved["snapshot"]["content_sha256"],
                     "fetched_at": saved["snapshot"]["fetched_at"].isoformat()},
    }


def _assert_archive_ack(saved, state, output, *, attempt=1):
    digest = sha256(output.removesuffix(b"\n")).hexdigest()
    rows = [row for row in state["receipts"] if row["attempt"] == attempt]
    assert [(row["phase"], row["output_evidence"]) for row in rows] == [
        ("STARTED", []), ("SUCCEEDED", [digest])]
    assert rows[0]["run_event_id"] < rows[1]["run_event_id"]
    for row in rows:
        assert saved[row["phase"].lower()] == {
            key: row[key] for key in ("run_event_id", "occurred_at")}
    snapshot = state["snapshot"]
    assert snapshot["content"].encode("utf-8") + b"\n" == output
    assert snapshot["content_sha256"] == digest
    assert snapshot["byte_length"] == len(output) - 1
    assert snapshot["kind"] == report.REPORT_SNAPSHOT_KIND
    assert snapshot["source"] == "papersim:report"
    assert snapshot["schema_version"] == snapshots.SNAPSHOT_DEFAULT_SCHEMA_VERSION
    assert saved["snapshot"] == {key: snapshot[key] for key in ("content_sha256", "fetched_at")}


def _archive_child():
    """Run the real managed CLI, with a private control pipe for test checkpoints."""
    import sys

    from psycopg import Cursor
    from psycopg.pq import TransactionStatus

    from polymarket_alpha_lab.papersim import store_psycopg

    root = Path(sys.argv[1])
    gate = sys.argv[2]
    if gate == "readback":
        with store_psycopg.connect_managed(root) as conn:
            print(json.dumps(_archive_rows(conn, *sys.argv[3:])))
        return

    def send(event, **fields):
        notice = json.dumps({"event": event, **fields}).encode("utf-8") + b"\n"
        os.write(sys.stderr.fileno(), notice)

    trace = []
    reader = None
    original_read = store_psycopg.read_pipeline_state
    original_assemble = report.assemble_report
    original_archive = store_psycopg.archive_report
    original_append = store_psycopg.record_command_run_event
    original_register = store_psycopg.register_snapshot
    assembled = False
    active_assembly = None
    default_isolation = None

    class TraceCursor(Cursor):
        def execute(self, query, params=None, **kwargs):
            trace.append(query)
            result = super().execute(query, params, **kwargs)
            if gate == "waiting" and query.startswith("SELECT phase, run_event_id, occurred_at "):
                with Cursor(self.connection) as observer:
                    observer.execute("SELECT current_setting('transaction_isolation'), "
                                     "current_setting('transaction_read_only')")
                    assert observer.fetchone() == ("read committed", "off")
            return result

    def read(conn, **kwargs):
        nonlocal reader, default_isolation
        reader = conn
        assert conn.info.transaction_status == TransactionStatus.IDLE
        if gate == "waiting":
            conn.autocommit = True
            conn.execute("SET default_transaction_isolation TO 'repeatable read'")
            default_isolation = conn.execute("SHOW default_transaction_isolation").fetchone()[0]
            conn.autocommit = False
        conn.cursor_factory = TraceCursor
        state = original_read(conn, **kwargs)
        assert conn.info.transaction_status == TransactionStatus.IDLE
        assert trace[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        assert trace[1:] and all(sql.startswith("SELECT ") for sql in trace[1:])
        return state

    def assemble(inputs):
        nonlocal assembled
        assert reader.info.transaction_status == TransactionStatus.IDLE
        result = original_assemble(inputs)
        assembled = True
        return result

    def archive(conn, assembly, **kwargs):
        nonlocal active_assembly
        assert assembled and conn is reader
        assert conn.info.transaction_status == TransactionStatus.IDLE
        active_assembly = assembly
        send("opening", backend_pid=conn.info.backend_pid, key=assembly.report_key,
             digest=assembly.content_sha256, default_isolation=default_isolation)

        trace.clear()
        saved = original_archive(conn, assembly, **kwargs)
        assert conn.info.transaction_status == TransactionStatus.IDLE
        assert trace[0] == "SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE"
        assert trace[1] == "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"
        assert trace[2].startswith("SELECT phase, run_event_id, occurred_at ")
        send("saved", **_archive_ack(saved))
        if gate == "committed":
            assert sys.stdin.buffer.read(1) == b"1"
        return saved

    def hold(conn):
        assert conn.info.transaction_status == TransactionStatus.INTRANS
        isolation, readonly = conn.execute(
            "SELECT current_setting('transaction_isolation'), "
            "current_setting('transaction_read_only')").fetchone()
        state = _archive_rows(conn, active_assembly.report_key, active_assembly.content_sha256)
        assert [row["phase"] for row in state["receipts"]] == ["STARTED"]
        if gate == "started":
            assert state["snapshot"] is None
        else:
            assert state["snapshot"]["content"].encode("utf-8") == active_assembly.content
        send("held", isolation=isolation, readonly=readonly)
        assert sys.stdin.buffer.read(1) == b"1"

    def append(conn, submission):
        saved = original_append(conn, submission)
        if gate == "started" and submission.phase == "STARTED":
            hold(conn)
        return saved

    def register(conn, registration):
        saved = original_register(conn, registration)
        if gate == "snapshot":
            hold(conn)
        return saved

    store_psycopg.read_pipeline_state = read
    report.assemble_report = assemble
    store_psycopg.archive_report = archive
    store_psycopg.record_command_run_event = append
    store_psycopg.register_snapshot = register
    raise SystemExit(papersim_cli.main(["report", "--managed", "--repo-root", str(root)]))


_ARCHIVE_CHILD_ENTRY = (
    "from tests.test_papersim_report_store_native import _archive_child; _archive_child()")


def _launch_archive_child(root, *args):
    import sys

    return subprocess.Popen(
        [sys.executable, "-u", "-c", _ARCHIVE_CHILD_ENTRY, str(root), *args],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)


def _archive_child_notice(child, event):
    import select

    assert select.select([child.stderr], [], [], 15)[0], f"no archive checkpoint {event}"
    try:
        row = json.loads(child.stderr.readline(4096))
    except ValueError:
        pytest.fail(f"archive child failed before checkpoint {event}", pytrace=False)
    assert row.pop("event") == event
    return row


def _terminate_archive_child(child):
    if child.poll() is None:
        child.kill()
        child.wait(timeout=15)


def _read_archive_from_new_process(root, opening):
    with _launch_archive_child(root, "readback", opening["key"], opening["digest"]) as child:
        try:
            output, stderr = child.communicate(timeout=15)
            assert child.returncode == 0 and not stderr, "independent archive readback failed"
            return json.loads(output)
        finally:
            _terminate_archive_child(child)


def _wait_for_archive_state(conn, sql, params, *, message):
    import time

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if conn.execute(sql, params).fetchone() == (True,):
            return
        time.sleep(0.02)
    pytest.fail(message, pytrace=False)


def test_report_archive_concurrent_managed_callers_reuse_the_committed_rows(app, repo, record_property):
    root, _ = repo
    lower, = app.execute("SELECT clock_timestamp()").fetchone()
    with _launch_archive_child(root, "snapshot") as winner:
        try:
            first = _archive_child_notice(winner, "opening")
            assert _archive_child_notice(winner, "held") == {
                "isolation": "read committed", "readonly": "off"}
            with _launch_archive_child(root, "waiting") as waiter:
                try:
                    second = _archive_child_notice(waiter, "opening")
                    assert first["key"] == second["key"] and first["digest"] == second["digest"]
                    assert second["default_isolation"] == "repeatable read"
                    assert len({first["backend_pid"], second["backend_pid"], app.info.backend_pid}) == 3
                    _wait_for_archive_state(
                        app,
                        "WITH archive_key AS (SELECT hashtextextended(%s, 0) AS value) "
                        "SELECT EXISTS (SELECT 1 FROM pg_locks blocked JOIN pg_locks holder "
                        "USING (locktype, database, classid, objid, objsubid, mode) "
                        "CROSS JOIN archive_key WHERE blocked.pid = %s AND holder.pid = %s "
                        "AND blocked.locktype = 'advisory' AND blocked.mode = 'ExclusiveLock' "
                        "AND blocked.objsubid = 1 AND NOT blocked.granted AND holder.granted "
                        "AND blocked.classid::bigint = ((value >> 32) & 4294967295) "
                        "AND blocked.objid::bigint = (value & 4294967295) "
                        "AND holder.pid = ANY(pg_blocking_pids(blocked.pid)))",
                        (first["key"], second["backend_pid"], first["backend_pid"]),
                        message="second session did not wait on the first session's report advisory lock",
                    )
                    assert _report_counts(app) == (0, 0)
                    release_cut, = app.execute("SELECT clock_timestamp()").fetchone()
                    winner.stdin.write(b"1")
                    saved = _archive_child_notice(winner, "saved")
                    replayed = _archive_child_notice(waiter, "saved")
                    first_output, first_err = winner.communicate(timeout=15)
                    second_output, second_err = waiter.communicate(timeout=15)
                    assert winner.returncode == waiter.returncode == 0
                    assert not first_err and not second_err
                finally:
                    _terminate_archive_child(waiter)
        finally:
            _terminate_archive_child(winner)

    upper, = app.execute("SELECT clock_timestamp()").fetchone()
    assert first_output == second_output and first_output.endswith(b"\n")
    assert saved == replayed
    assert _report_counts(app) == (1, 2)
    persisted = _read_archive_from_new_process(root, first)
    _assert_archive_ack(saved, persisted, first_output)
    started, fetched, succeeded = map(datetime.datetime.fromisoformat, (
        saved["started"]["occurred_at"], saved["snapshot"]["fetched_at"],
        saved["succeeded"]["occurred_at"]))
    assert lower <= started <= fetched <= release_cut <= succeeded <= upper
    record_property("report_archive", json.dumps({
        "receipts": saved,
        "clock_bounds": [instant.isoformat() for instant in (lower, release_cut, upper)],
    }, sort_keys=True))


@pytest.mark.parametrize("gate", ("started", "snapshot", "committed"))
def test_report_archive_client_loss_before_stdout(app, repo, capsysbinary, gate, record_property):
    import select
    import signal

    root, _ = repo
    with _launch_archive_child(root, gate) as child:
        try:
            opening = _archive_child_notice(child, "opening")
            if gate == "committed":
                saved = _archive_child_notice(child, "saved")
                assert _report_counts(app) == (1, 2)
            else:
                assert _archive_child_notice(child, "held") == {
                    "isolation": "read committed", "readonly": "off"}
                assert _report_counts(app) == (0, 0)
            assert not select.select([child.stdout], [], [], 0)[0], "report stdout before acknowledgement"
            child.kill()
            output, stderr = child.communicate(timeout=15)
            assert child.returncode == -signal.SIGKILL and not output and not stderr
        finally:
            _terminate_archive_child(child)

    _wait_for_archive_state(
        app, "SELECT NOT EXISTS (SELECT 1 FROM pg_stat_activity WHERE pid = %s)",
        (opening["backend_pid"],), message="disconnected report backend did not exit")
    prior = _read_archive_from_new_process(root, opening)
    if gate != "committed":
        assert prior == {"receipts": [], "snapshot": None}
    output, document = _report_output(root, capsysbinary)
    assert _report_counts(app) == (1, 2)
    key, snapshot, _ = _saved_report(app, output, document)
    assert key == opening["key"] and snapshot["content_sha256"] == opening["digest"]
    complete = _read_archive_from_new_process(root, opening)
    assert len(complete["receipts"]) == 2
    assert complete["snapshot"]["content"].encode("utf-8") + b"\n" == output
    if gate == "committed":
        assert complete == prior
        _assert_archive_ack(saved, complete, output)
    record_property("report_archive", json.dumps({
        "receipts_after_loss": len(prior["receipts"]),
        "snapshot_after_loss": prior["snapshot"] is not None,
        "retry_receipts": complete["receipts"],
        "digest": snapshot["content_sha256"],
    }, sort_keys=True))


def _empty_report_assembly(scope="*"):
    from polymarket_alpha_lab.papersim import evaluation

    return report.assemble_report(report.ReportInputs(
        scope=scope, wired_commands=papersim_cli._wired_subcommands(),
        evaluation=evaluation.evaluate_queues(())))


def test_report_archive_persists_independent_clock_samples_and_later_attempt(app, repo, capsysbinary):
    from polymarket_alpha_lab.papersim import store_psycopg

    root, _ = repo
    assembly = _empty_report_assembly()
    started, fetched, succeeded = (T0 + datetime.timedelta(milliseconds=ms)
                                   for ms in (138, 2244, 8712))
    samples = iter((started, fetched, succeeded))
    first = store_psycopg.archive_report(app, assembly, clock=samples.__next__)
    assert list(samples) == []
    assert first["started"]["occurred_at"] == started
    assert first["snapshot"]["fetched_at"] == fetched
    assert first["succeeded"]["occurred_at"] == succeeded
    first_output, document = _report_output(root, capsysbinary)
    assert first_output == assembly.content + b"\n"
    assert _report_counts(app) == (1, 2)
    _saved_report(app, first_output, document)

    equal_time = T0 + datetime.timedelta(minutes=1)
    samples = iter((equal_time, equal_time))
    second = store_psycopg.archive_report(app, assembly, attempt=2, clock=samples.__next__)
    assert list(samples) == []
    assert second["started"]["occurred_at"] == second["succeeded"]["occurred_at"] == equal_time
    assert second["snapshot"] == first["snapshot"]
    assert second["started"]["run_event_id"] > first["succeeded"]["run_event_id"]
    assert _report_counts(app) == (1, 4)
    actual = _archive_rows(app, assembly.report_key, assembly.content_sha256)
    _assert_archive_ack(_archive_ack(first), actual, first_output)
    _assert_archive_ack(_archive_ack(second), actual, first_output, attempt=2)
    assert papersim_cli.main(["report", "--managed", "--repo-root", str(root), "--attempt", "2"]) == 0
    captured = capsysbinary.readouterr()
    assert captured.out == first_output and not captured.err
    assert _archive_rows(app, assembly.report_key, assembly.content_sha256) == actual


def test_report_archive_finishes_matching_historical_started(app, repo, capsysbinary):
    from polymarket_alpha_lab.papersim import store_psycopg

    root, _ = repo
    assembly = _empty_report_assembly()
    historical_at, = app.execute("SELECT clock_timestamp()").fetchone()
    historical = store_psycopg.record_command_run_event(app, report.report_receipt(
        operation_key=assembly.report_key, phase="STARTED", occurred_at=historical_at))
    assert historical["inserted"] is True and _report_counts(app) == (0, 1)
    output, document = _report_output(root, capsysbinary)
    key, snapshot, rows = _saved_report(app, output, document)
    assert key == assembly.report_key
    assert rows[0][5] == historical["run_event_id"] and rows[0][8] == historical_at
    assert historical_at <= snapshot["fetched_at"] <= rows[1][8]
    assert _report_counts(app) == (1, 2)


@pytest.mark.parametrize(("prior", "code"), (
    pytest.param("different_started", "papersim_command_run_conflict", id="different_started"),
    pytest.param("premature_success", "papersim_report_archive_phase_conflict", id="premature_success"),
    pytest.param("missing_snapshot", "papersim_report_archive_snapshot_missing", id="missing_snapshot"),
    pytest.param("different_snapshot", "papersim_report_archive_snapshot_conflict", id="different_snapshot"),
))
def test_report_archive_conflict_keeps_exact_prior_rows(app, prior, code):
    from polymarket_alpha_lab.papersim import store_psycopg

    assembly = _empty_report_assembly()
    if prior == "different_started":
        store_psycopg.record_command_run_event(app, report.report_receipt(
            operation_key=assembly.report_key, phase="STARTED", occurred_at=T0,
            config_digest="f" * 64))
    elif prior in {"premature_success", "missing_snapshot"}:
        if prior == "missing_snapshot":
            store_psycopg.record_command_run_event(app, report.report_receipt(
                operation_key=assembly.report_key, phase="STARTED", occurred_at=T0))
        store_psycopg.record_command_run_event(app, report.report_receipt(
            operation_key=assembly.report_key, phase="SUCCEEDED", occurred_at=T0,
            output_evidence=(assembly.content_sha256,)))
    else:
        store_psycopg.register_snapshot(app, report.report_snapshot_registration(
            assembly.content, fetched_at=T0, source="b4-native-unrelated"))

    previous = _archive_rows(app, assembly.report_key, assembly.content_sha256)
    counts = _report_counts(app)
    with pytest.raises(store.PapersimStoreError, match=f"^{code}$"):
        store_psycopg.archive_report(app, assembly)
    assert _report_counts(app) == counts
    assert _archive_rows(app, assembly.report_key, assembly.content_sha256) == previous


def test_report_stage_times_follow_source_rows_in_one_snapshot(
        app, repo, capsysbinary, monkeypatch, record_property):
    from polymarket_alpha_lab.papersim import store_psycopg

    root, _ = repo
    _seed_initial_cash(app)
    scan_result = _run_scan(app, root)
    candidate_id, = scan_result["candidate_ids"].values()
    market_id = _crypto_record().market_id
    archives = []

    def at(minutes):
        return T0 + datetime.timedelta(minutes=minutes)

    def capture(*, marks=None, outcomes=None, evaluate=at(-1), overall=T0):
        output, document = _report_output(root, capsysbinary)
        stage_times = {"scan": T0, "marks": marks, "outcomes": outcomes,
                       "settle": None, "evaluate": evaluate}
        assert {name: row["as_of"] for name, row in document["stages"].items()} == {
            name: instant.isoformat() if instant is not None else None
            for name, instant in stage_times.items()}
        assert document["as_of"] == overall.isoformat()
        assert document["receipts"]["counts_by_command"] == {"run-scan": 2}
        assert json.dumps(document["evaluation"], sort_keys=True, allow_nan=False).encode() + b"\n" == (
            _managed_cli("evaluate", root, capsysbinary))
        saved = _saved_report(app, output, document)
        archives.append((output, document, saved))
        return document

    baseline = capture()
    for minutes in (2, 1):
        mark = store_psycopg.mark_capture_upsert(app, store.CandidateMarkCapture(
            candidate_id=candidate_id, market_ref_kind="market_id", market_ref=market_id,
            observed_at=at(minutes), mid=Decimal("0.17"), source_ref="b4-native-periodic"))
        assert mark["candidate_mark_inserted"] is True
        marked = capture(marks=at(2), overall=at(2))
        assert marked["stages"]["marks"]["row_count"] == (1 if minutes == 2 else 2)
        assert marked["stages"]["marks"]["coverage_ratio"] == 1.0
        assert marked["evaluation"] == baseline["evaluation"]

    close = store.PriceEvidenceObservation(
        evidence_role="CLOSE", market_ref_kind="market_id", market_ref=market_id,
        observed_at=at(3), last=Decimal("0.22"), source_ref="b4-native-evaluate-only").validate()
    assert close.observed_at < EXPIRY
    ordinary_loader = papersim_cli._evaluation_candidates_in_snapshot
    completed = []

    def write_during_read(reader, *args, **kwargs):
        assert reader.execute("SHOW transaction_isolation").fetchone() == ("repeatable read",)
        assert reader.execute("SHOW transaction_read_only").fetchone() == ("on",)
        evidence_key = close.evidence_key_with(candidate_id)
        with store_psycopg.connect_managed(root) as writer:
            assert writer.info.backend_pid != reader.info.backend_pid
            # Seed only the existing evaluation input. The mark capture writer
            # would also add a mark, which would change the marks stage here.
            writer.execute(
                "INSERT INTO papersim.candidate_price_evidence "
                "(evidence_key, candidate_id, evidence_role, observed_at, market_ref_kind, "
                "market_ref, side, last, source_kind, source_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (evidence_key, candidate_id, close.evidence_role, close.observed_at,
                 close.market_ref_kind, close.market_ref, close.side, close.last,
                 close.source_kind, close.source_ref))
        query = "SELECT observed_at FROM papersim.candidate_price_evidence WHERE evidence_key = %s"
        assert app.execute(query, (evidence_key,)).fetchone() == (at(3),)
        assert reader.execute(query, (evidence_key,)).fetchone() is None
        completed.append(True)
        return ordinary_loader(reader, *args, **kwargs)

    with monkeypatch.context() as hook:
        hook.setattr(papersim_cli, "_evaluation_candidates_in_snapshot", write_during_read)
        racing_output, racing_report = _report_output(root, capsysbinary)
    assert completed == [True]
    assert (racing_output, racing_report) == archives[-1][:2]
    closed = capture(marks=at(2), evaluate=at(3), overall=at(3))
    assert closed["evaluation"] != marked["evaluation"]

    resolved = store_psycopg.record_market_outcome(app, store.MarketOutcomeObservation(
        market_id=market_id, outcome="VOIDED", observed_at=at(4), confirmed_at=at(5),
        source_ref="b4-native-voided"))
    assert resolved["inserted"] is True
    known = capture(marks=at(2), outcomes=at(5), evaluate=at(5), overall=at(5))
    assert known["stages"]["outcomes"]["row_count"] == 1
    assert known["stages"]["outcomes"]["coverage_ratio"] == 1.0
    assert known["evaluation"]["queues"][0]["counts"]["voided"] == 1

    unknown = store_psycopg.record_market_outcome(app, store.MarketOutcomeObservation(
        market_id=market_id, outcome="UNKNOWN", observed_at=at(6), source_ref="b4-native-unknown"))
    assert unknown["inserted"] is True
    observed = capture(marks=at(2), outcomes=at(6), evaluate=at(5), overall=at(6))
    assert observed["stages"]["outcomes"]["row_count"] == 2
    assert observed["stages"]["outcomes"]["coverage_ratio"] == 1.0
    assert observed["evaluation"] == known["evaluation"]

    assert len({saved[0] for _, _, saved in archives}) == len(archives)
    assert len({saved[1]["content_sha256"] for _, _, saved in archives}) == len(archives)
    assert _report_counts(app) == (len(archives), 2 * len(archives))
    assert datetime.datetime.now(datetime.UTC) > archives[-1][2][1]["fetched_at"]
    assert _report_output(root, capsysbinary) == archives[-1][:2]
    for output, document, saved in archives:
        assert _saved_report(app, output, document) == saved
    record_property("report_watermarks", json.dumps([
        {"stages": {name: row["as_of"] for name, row in document["stages"].items()},
         "overall": document["as_of"], "digest": saved[1]["content_sha256"]}
        for _, document, saved in archives
    ], sort_keys=True))


def _native_cli_args(root, command, *options):
    return [
        sys.executable, "-m", "polymarket_alpha_lab.papersim.cli",
        command, "--managed", "--repo-root", str(root), *options,
    ]


def _native_cli(root, command, *options):
    return subprocess.run(
        _native_cli_args(root, command, *options), cwd=ROOT, capture_output=True, timeout=30)


def _pipeline_rows(conn):
    """Fingerprint the evidence tables while excluding the report's own writes."""
    tables = (
        "scan_runs", "candidates", "candidate_price_evidence", "candidate_marks",
        "marks", "market_outcomes", "entries", "settlements", "bankroll_events",
        "queue_control_events", "bankroll_checks", "command_run_events", "snapshots",
    )
    excluded = {"command_run_events": "command <> 'report'",
                "snapshots": "kind <> 'papersim-report'"}
    return {
        table: conn.execute(
            "SELECT count(*), encode(sha256(convert_to(coalesce("
            "string_agg(payload, chr(10) ORDER BY payload), ''), 'UTF8')), 'hex') "
            f"FROM (SELECT to_jsonb(row)::text AS payload FROM papersim.{table} AS row "
            f"WHERE {excluded.get(table, 'true')}) AS evidence"
        ).fetchone()
        for table in tables
    }


def _report_archive_counts(conn):
    return conn.execute(
        "SELECT (SELECT count(*) FROM papersim.snapshots WHERE kind = 'papersim-report'), "
        "(SELECT count(*) FROM papersim.command_run_events WHERE command = 'report')"
    ).fetchone()


@dataclass(frozen=True)
class _ArchivedCliReport:
    output: bytes
    document: dict
    operation_key: str
    digest: str
    state: dict

    def read_again(self, root):
        return _read_archive_from_new_process(
            root, {"key": self.operation_key, "digest": self.digest})


def _check_native_report(root, *, code, stderr, output):
    if code != 0 or stderr:
        diagnostic = b"report: FAIL: recorded mode and EXITED rows require a recorded 'pnl'\n"
        failure = " (missing recorded PnL)" if stderr == diagnostic else ""
        pytest.fail(f"native report failed with exit {code}{failure}", pytrace=False)
    assert output.endswith(b"\n") and output.count(b"\n") == 1
    content = output.removesuffix(b"\n")
    document = json.loads(content)
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert content == canonical.encode("utf-8")
    assert all(document[flag] is True for flag in ("paper_only", "report_only", "readonly"))

    digest = sha256(content).hexdigest()
    as_of = document["as_of"]
    stamp = ("no-data" if as_of is None else
             datetime.datetime.fromisoformat(as_of).astimezone(datetime.UTC).strftime("%Y%m%d%H%M%S"))
    key = f"report|{document['scope']}|{stamp}|{digest}"
    archive = _read_archive_from_new_process(root, {"key": key, "digest": digest})
    snapshot = archive["snapshot"]
    assert snapshot is not None
    assert snapshot["content"].encode("utf-8") == content
    assert sha256(snapshot["content"].encode("utf-8")).hexdigest() == snapshot["content_sha256"] == digest
    assert snapshot["byte_length"] == len(content)
    assert (snapshot["kind"], snapshot["source"], snapshot["schema_version"]) == (
        "papersim-report", "papersim:report", 1)
    started, succeeded = archive["receipts"]
    assert [(row["phase"], row["attempt"], row["output_evidence"]) for row in (started, succeeded)] == [
        ("STARTED", 1, []), ("SUCCEEDED", 1, [digest])]
    assert all(row["flags"] == [True, True, True] for row in (started, succeeded))
    assert started["run_event_id"] < succeeded["run_event_id"]
    assert datetime.datetime.fromisoformat(started["occurred_at"]) <= datetime.datetime.fromisoformat(
        snapshot["fetched_at"]) <= datetime.datetime.fromisoformat(succeeded["occurred_at"])
    return _ArchivedCliReport(output, document, key, digest, archive)


def _capture_native_report(conn, root, *, replay=None):
    sources = _pipeline_rows(conn)
    snapshots_before, receipts_before = _report_archive_counts(conn)
    command = _native_cli(root, "report")
    result = _check_native_report(
        root, code=command.returncode, stderr=command.stderr, output=command.stdout)
    assert _pipeline_rows(conn) == sources
    if replay is not None:
        assert result == replay
        assert _report_archive_counts(conn) == (snapshots_before, receipts_before)
    else:
        assert _report_archive_counts(conn) == (snapshots_before + 1, receipts_before + 2)
    return result


def _assert_native_archives_retained(root, reports, record_property):
    assert len({row.operation_key for row in reports}) == len(reports)
    assert len({row.digest for row in reports}) == len(reports)
    for row in reports:
        assert row.read_again(root) == row.state
    record_property("cli_archive_transitions", json.dumps([
        {"as_of": row.document["as_of"], "digest": row.digest,
         "receipts": [(receipt["run_event_id"], receipt["occurred_at"])
                      for receipt in row.state["receipts"]]}
        for row in reports
    ], sort_keys=True))


def _priced_crypto_scan(conn, repo, records, *, scan_at=T0, rejected_key=None):
    from polymarket_alpha_lab.papersim import store_psycopg

    config = _config()
    assembled = scan.assemble_scan(
        config, records, admissions=scan.AdmissionReport(),
        portfolio=scan.read_portfolio_state(conn), started_at=scan_at, finished_at=scan_at,
        code_commit=repo[1], specs=scan.consult_provider(PROVIDER, records),
        operation_key=f"run-scan|b4-native|pricing|{records[0].candidate_key}")
    candidates = []
    for candidate in assembled.submission.candidates:
        rejected = candidate.candidate_key == rejected_key
        assert candidate.conclusion == ("REJECTED" if rejected else "ACCEPTED")
        assert (candidate.entry is None) == rejected
        decision, = candidate.price_evidence
        priced = replace(decision, bid=Decimal("0.13"), ask=Decimal("0.15"), mid=Decimal("0.14"))
        candidates.append(replace(candidate, price_evidence=(priced,)))
    result = store_psycopg.record_scan(
        conn, replace(assembled.submission, candidates=tuple(candidates)))
    assert result["inserted"] is True
    return result["candidate_ids"]


_CLOSE_AT = T0 + datetime.timedelta(minutes=3)
_OUTCOME_OBSERVED = EXPIRY + datetime.timedelta(minutes=2)
_OUTCOME_CONFIRMED = EXPIRY + datetime.timedelta(minutes=5)
_BOOKED_AT = EXPIRY + datetime.timedelta(minutes=9)


def _crypto_entry(conn, candidate_id):
    return conn.execute(
        "SELECT entry_id, stake, price, shares, entry_fee FROM papersim.entries "
        "WHERE candidate_id = %s", (candidate_id,)
    ).fetchone()


def _write_crypto_close(conn, candidate_id, market_id, *, entry_id=None, price=Decimal("0.22")):
    from polymarket_alpha_lab.papersim import store_psycopg

    fields = dict(observed_at=_CLOSE_AT, market_ref_kind="market_id",
                  market_ref=market_id, last=price, source_ref="b4-native-close")
    row = store_psycopg.mark_capture_upsert(
        conn, store.CandidateMarkCapture(candidate_id=candidate_id, sampling_kind="CLOSE",
                                         mid=price, **fields), entry_id=entry_id,
        close_evidence=store.PriceEvidenceObservation(evidence_role="CLOSE", **fields))
    assert row["candidate_mark_inserted"] and row["close_evidence_inserted"]
    return row["entry_mark_id"]


def _write_crypto_outcome(conn, market_id, outcome, *,
                          observed_at=_OUTCOME_OBSERVED, confirmed_at=_OUTCOME_CONFIRMED):
    from polymarket_alpha_lab.papersim import store_psycopg

    result = store_psycopg.record_market_outcome(conn, store.MarketOutcomeObservation(
        market_id=market_id, outcome=outcome, observed_at=observed_at,
        confirmed_at=confirmed_at, source_ref="b4-native-terminal"))
    assert result["inserted"] is True
    return result["outcome_id"]


def _natural_crypto_booking(entry, outcome_id, outcome, *, confirmed_at=_BOOKED_AT):
    entry_id, stake, _, shares, entry_fee = entry
    hit = outcome == "YES"
    proceeds = shares if hit else Decimal(0)
    return store.SettlementRecord(
        entry_id=entry_id, classification="NATURAL", outcome_label=outcome,
        win_loss="win" if hit else "loss", proceeds=proceeds, pnl_gross=proceeds - stake,
        fee_total=entry_fee, pnl_net=proceeds - stake - entry_fee, source_outcome_id=outcome_id,
        evidence="b4-native-natural", confirmed_by="b4-native", confirmed_at=confirmed_at)


def _checked_crypto_booking(conn, booking):
    return conn.execute(
        "SELECT s.classification, s.outcome_label, s.win_loss, s.pnl_gross, s.pnl_net, "
        "e.reason, e.amount, s.confirmed_at FROM papersim.settlements AS s "
        "JOIN papersim.bankroll_events AS e ON e.entry_id = s.entry_id AND e.event_key = %s "
        "WHERE s.entry_id = %s", (booking.event_key(), booking.entry_id)
    ).fetchall()


def _assert_crypto_booking(conn, booking):
    assert _checked_crypto_booking(conn, booking) == [(
        booking.classification, booking.outcome_label, booking.win_loss,
        booking.pnl_gross, booking.pnl_net, booking.cash_reason(), booking.cash_amount(),
        booking.confirmed_at,
    )]


def _write_crypto_booking(root, booking):
    from polymarket_alpha_lab.papersim import store_psycopg

    with store_psycopg.connect_managed(root) as writer:
        writer.execute("SET LOCAL statement_timeout = '10s'")
        assert store_psycopg.settlement_record(writer, booking)["inserted"] is True
    with store_psycopg.connect_managed(root) as observer:
        _assert_crypto_booking(observer, booking)


def _expected_crypto_queue(market_type="terminal"):
    return {
        "model_key": f"crypto:{pipeline.MODEL_VERSION_CRYPTO}",
        "market_type": market_type,
        "gate_version": pipeline.GATE_VERSION,
    }


def _assert_pnl_gap(document, queue):
    stage = document["stages"]["evaluate"]
    assert stage["status"] == "PARTIAL"
    assert stage["recorded_pnl_gap"] == {
        "reason": "settlement_not_recorded", "entries": 1,
        "by_queue": [{**queue, "entries": 1, "recorded_pnl": None}],
    }


def _assert_recorded_evaluation(root, document):
    command = _native_cli(root, "evaluate", "--pnl-basis", "recorded")
    assert command.returncode == 0 and not command.stderr
    assert command.stdout == json.dumps(
        document["evaluation"], sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"


@pytest.mark.parametrize("outcome", ("YES", "NO", "YES-basket"))
def test_report_keeps_market_scoring_while_natural_pnl_is_pending(app, repo, outcome, record_property):
    root, _ = repo
    record = _crypto_record()
    _seed_initial_cash(app)
    candidates = _priced_crypto_scan(app, repo, (record,))
    candidate_id = candidates[record.candidate_key]
    entry = _crypto_entry(app, candidate_id)
    assert entry is not None
    _write_crypto_close(app, candidate_id, record.market_id, entry_id=entry[0])

    initial = _capture_native_report(app, root)
    initial_stages = initial.document["stages"]
    assert "recorded_pnl_gap" not in initial_stages["evaluate"]
    assert initial_stages["outcomes"]["row_count"] == 0
    assert initial_stages["evaluate"]["as_of"] == initial.document["as_of"] == _CLOSE_AT.isoformat()
    first = _write_crypto_outcome(app, record.market_id, outcome)
    booking = _natural_crypto_booking(entry, first, outcome)
    assert _checked_crypto_booking(app, booking) == []
    observed_rows = _pipeline_rows(app)
    previous_archives = _report_archive_counts(app)
    diagnostic = b"evaluate: FAIL: recorded mode and EXITED rows require a recorded 'pnl'\n"
    for options in ((), ("--pnl-basis", "recorded")):
        strict = _native_cli(root, "evaluate", *options)
        assert (strict.returncode, strict.stdout, strict.stderr) == (1, b"", diagnostic)
    recomputed = _native_cli(root, "evaluate", "--pnl-basis", "recomputed")
    assert recomputed.returncode == 0 and not recomputed.stderr
    hypothetical, = json.loads(recomputed.stdout)["queues"]
    assert hypothetical["counts"]["settled"] == hypothetical["roi"]["counted"] == 1
    assert hypothetical["fills"]["hits"] == int(booking.win_loss == "win")
    assert hypothetical["roi"]["gross_pnl"] == pytest.approx(float(booking.pnl_gross))
    assert _pipeline_rows(app) == observed_rows
    assert _report_archive_counts(app) == previous_archives

    partial = _capture_native_report(app, root)
    document = partial.document
    stages = document["stages"]
    _assert_pnl_gap(document, _expected_crypto_queue())
    assert stages["outcomes"]["status"] == "OK"
    assert stages["outcomes"]["row_count"] == stages["outcomes"]["markets_with_outcome"] == 1
    assert stages["outcomes"]["coverage_ratio"] == 1.0
    assert stages["settle"]["entries_total"] == 1
    assert stages["settle"]["status"] == "NO_DATA"
    assert stages["settle"]["entries_settled"] == stages["settle"]["coverage_ratio"] == 0
    assert stages["settle"]["as_of"] is None
    observed = _OUTCOME_CONFIRMED.isoformat()
    assert stages["outcomes"]["as_of"] == stages["evaluate"]["as_of"] == document["as_of"] == observed
    queue, = document["evaluation"]["queues"]
    assert queue["counts"]["entered"] == queue["counts"]["unresolved"] == 1
    assert queue["counts"]["settled"] == queue["roi"]["counted"] == 0
    assert queue["roi"]["status"] == "INSUFFICIENT"
    assert queue["roi"]["gross_roi"] is queue["roi"]["net_roi"] is None
    assert queue["roi"]["entry_fees"] == queue["roi"]["gross_pnl"] == 0
    assert queue["clv"]["entries_with_close"] == 1
    assert queue["clv"]["mean"] == pytest.approx(0.22 - float(entry[2]))
    scoring = queue["scoring"]
    assert scoring == hypothetical["scoring"]
    assert scoring["markets_with_outcome"] == scoring["markets_scored"] == 1
    truth = int(booking.win_loss == "win")
    assert scoring["model_brier"] == pytest.approx((0.2224 - truth) ** 2)
    assert scoring["market_brier"] == pytest.approx((0.14 - truth) ** 2)
    _capture_native_report(app, root, replay=partial)

    _write_crypto_booking(root, booking)
    complete = _capture_native_report(app, root)
    completed = complete.document
    assert completed["stages"]["scan"] == stages["scan"]
    assert completed["stages"]["marks"] == stages["marks"]
    assert completed["stages"]["outcomes"] == stages["outcomes"]
    assert completed["stages"]["settle"]["entries_settled"] == 1
    assert completed["stages"]["settle"]["coverage_ratio"] == 1.0
    assert completed["stages"]["settle"]["as_of"] == _BOOKED_AT.isoformat()
    assert completed["stages"]["evaluate"]["as_of"] == completed["as_of"] == _BOOKED_AT.isoformat()
    assert completed["stages"]["evaluate"]["status"] == "OK"
    assert "recorded_pnl_gap" not in completed["stages"]["evaluate"]
    realized, = completed["evaluation"]["queues"]
    assert realized["counts"]["settled"] == realized["roi"]["counted"] == 1
    assert realized["counts"]["unresolved"] == 0
    assert realized["fills"]["hits"] == int(booking.win_loss == "win")
    assert realized["roi"]["gross_pnl"] == float(booking.pnl_gross)
    assert realized["scoring"] == scoring
    _assert_recorded_evaluation(root, completed)
    _capture_native_report(app, root, replay=complete)
    _assert_native_archives_retained(root, (initial, partial, complete), record_property)


def test_report_keeps_booked_zero_and_a_complete_queue_during_pending_pnl(app, repo, record_property):
    root, _ = repo
    _seed_initial_cash(app)

    def trade(key, market_type, decision_at, expiry):
        market_id = f"b4-{key}"
        fixture = f"ETH-fixture-{key}"
        request = replace(_eth_request(), market_id=market_id, fixture=fixture, news=_news(fixture),
                          now=decision_at, book_time=decision_at)
        record = _crypto_record(key=key)
        return replace(record, market_id=market_id, market_type=market_type, evaluated_at=decision_at,
                       events=replace(record.events, expiry_at=expiry, event_start_at=expiry),
                       request_bundle=replay.replay_bundle_payload(request))

    def add_natural_trade(key, market_type, decision_at, expiry, outcome):
        row = trade(key, market_type, decision_at, expiry)
        ids = _priced_crypto_scan(app, repo, (row,), scan_at=decision_at)
        observed = expiry + datetime.timedelta(minutes=1)
        confirmed = expiry + datetime.timedelta(minutes=2)
        booked = expiry + datetime.timedelta(minutes=3)
        outcome_id = _write_crypto_outcome(app, row.market_id, outcome,
                                          observed_at=observed, confirmed_at=confirmed)
        entry = _crypto_entry(app, ids[key])
        settlement = _natural_crypto_booking(entry, outcome_id, outcome, confirmed_at=booked)
        _write_crypto_booking(root, settlement)
        return entry, settlement

    early_exit = trade("early-exit", "terminal", T0, EXPIRY)
    exit_ids = _priced_crypto_scan(app, repo, (early_exit,))
    exit_candidate = exit_ids[early_exit.candidate_key]
    exit_entry, exit_stake, exit_price, _, exit_entry_fee = _crypto_entry(app, exit_candidate)
    exit_mark = _write_crypto_close(app, exit_candidate, early_exit.market_id,
                                    entry_id=exit_entry, price=exit_price)
    exit_time = _CLOSE_AT + datetime.timedelta(minutes=1)
    exit_booking = store.SettlementRecord(
        entry_id=exit_entry, classification="EXIT", exit_price=exit_price, proceeds=exit_stake,
        pnl_gross=Decimal(0), exit_fee=exit_entry_fee, fee_total=2 * exit_entry_fee,
        pnl_net=-2 * exit_entry_fee, exit_mark_id=exit_mark,
        evidence="b4-native-zero-exit", confirmed_by="b4-native", confirmed_at=exit_time)
    _write_crypto_booking(root, exit_booking)
    _write_crypto_outcome(app, early_exit.market_id, "NO")

    # Each new B1 scan starts after the preceding position has been booked.
    win_entry, win_booking = add_natural_trade(
        "win", "terminal", exit_time + datetime.timedelta(minutes=1), EXPIRY, "YES")
    interval = EXPIRY - T0
    control_scan = win_booking.confirmed_at + datetime.timedelta(minutes=1)
    _, control_booking = add_natural_trade(
        "control", "terminal-control", control_scan, control_scan + interval, "NO")
    pending_scan = control_booking.confirmed_at + datetime.timedelta(minutes=1)
    pending_expiry = pending_scan + interval
    pending = trade("pending", "terminal", pending_scan, pending_expiry)
    rejected = replace(pending, candidate_key="rejected",
                       request_bundle=replay.replay_bundle_payload(
                           replace(replay.request_from_replay_bundle(pending.request_bundle), book=[])))
    ids = _priced_crypto_scan(app, repo, (pending, rejected), scan_at=pending_scan,
                              rejected_key=rejected.candidate_key)
    assert _crypto_entry(app, ids[rejected.candidate_key]) is None
    pending_entry = _crypto_entry(app, ids[pending.candidate_key])
    initial = _capture_native_report(app, root)
    _assert_recorded_evaluation(root, initial.document)
    before, clean = initial.document["evaluation"]["queues"]
    assert before["queue"] == _expected_crypto_queue()
    assert clean["queue"] == _expected_crypto_queue("terminal-control")
    assert before["counts"]["exited"] == 1
    assert before["scoring"]["markets_scored"] == 2
    assert clean["roi"]["gross_pnl"] == float(control_booking.pnl_gross)

    observed = pending_expiry + datetime.timedelta(minutes=1)
    confirmed = pending_expiry + datetime.timedelta(minutes=2)
    booked = pending_expiry + datetime.timedelta(minutes=3)
    pending_id = _write_crypto_outcome(app, pending.market_id, "YES",
                                      observed_at=observed, confirmed_at=confirmed)
    partial = _capture_native_report(app, root)
    document = partial.document
    _assert_pnl_gap(document, before["queue"])
    affected, untouched = document["evaluation"]["queues"]
    assert json.dumps(untouched, sort_keys=True) == json.dumps(clean, sort_keys=True)
    assert affected["counts"] == {
        "candidates": 4, "accepted": 3, "rejected": 1, "construction_failed": 0,
        "entered": 3, "settled": 2, "exited": 1, "unresolved": 1, "voided": 0,
    }
    assert affected["roi"] == before["roi"]
    assert affected["roi"]["counted"] == 2
    assert affected["roi"]["gross_pnl"] == float(win_booking.pnl_gross)
    assert affected["roi"]["stake"] == pytest.approx(float(win_entry[1] + exit_stake))
    assert affected["roi"]["exit_fees"] == pytest.approx(float(exit_entry_fee))
    assert affected["drawdown"] == before["drawdown"]
    scoring = affected["scoring"]
    assert scoring["markets_with_outcome"] == scoring["markets_scored"] == 3
    assert scoring["deduped_repeats"] == 1
    assert scoring["model_brier"] == pytest.approx((2 * (1 - 0.2224) ** 2 + 0.2224 ** 2) / 3)
    stages = document["stages"]
    assert stages["outcomes"]["markets_with_outcome"] == stages["outcomes"]["row_count"] == 4
    assert stages["outcomes"]["coverage_ratio"] == 1.0
    assert stages["settle"]["entries_settled"] == 3
    assert stages["settle"]["coverage_ratio"] == 0.75
    assert stages["outcomes"]["as_of"] == stages["evaluate"]["as_of"] == document["as_of"] == confirmed.isoformat()
    assert stages["settle"]["as_of"] == control_booking.confirmed_at.isoformat()
    _capture_native_report(app, root, replay=partial)

    _write_crypto_booking(root, _natural_crypto_booking(pending_entry, pending_id, "YES", confirmed_at=booked))
    complete = _capture_native_report(app, root)
    assert complete.document["stages"]["settle"]["coverage_ratio"] == 1.0
    assert complete.document["as_of"] == booked.isoformat()
    assert "recorded_pnl_gap" not in complete.document["stages"]["evaluate"]
    assert complete.document["evaluation"]["queues"][0]["scoring"] == scoring
    assert complete.document["evaluation"]["queues"][1] == clean
    _assert_recorded_evaluation(root, complete.document)
    _assert_native_archives_retained(root, (initial, partial, complete), record_property)


def _blocked_report(conn, owner_pid):
    _wait_for_archive_state(
        conn,
        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity AS a JOIN pg_locks AS blocked "
        "ON a.pid = blocked.pid WHERE a.backend_xmin IS NOT NULL AND a.state = 'active' "
        "AND a.wait_event_type = 'Lock' AND a.query LIKE 'SELECT control_event_id,%%' "
        "AND blocked.relation = 'papersim.queue_control_events'::regclass "
        "AND blocked.mode = 'AccessShareLock' AND NOT blocked.granted "
        "AND %s = ANY(pg_blocking_pids(a.pid)) "
        "AND (SELECT count(*) FROM pg_locks AS held WHERE held.pid = a.pid "
        "AND held.granted AND held.mode = 'AccessShareLock' "
        "AND held.relation IN ('papersim.command_run_events'::regclass, "
        "'papersim.scan_runs'::regclass)) = 2)",
        (owner_pid,), message="report reader did not reach the queue controls in its established snapshot",
    )


def test_report_reads_the_pending_snapshot_when_settlement_commits_under_its_wait(app, database, repo, record_property):
    root, _ = repo
    owner, _ = database
    record = _crypto_record()
    _seed_initial_cash(app)
    ids = _priced_crypto_scan(app, repo, (record,))
    entry = _crypto_entry(app, ids[record.candidate_key])
    _write_crypto_close(app, ids[record.candidate_key], record.market_id, entry_id=entry[0])
    outcome_id = _write_crypto_outcome(app, record.market_id, "YES")
    booking = _natural_crypto_booking(entry, outcome_id, "YES")
    before = _capture_native_report(app, root)
    _assert_pnl_gap(before.document, _expected_crypto_queue())
    assert _checked_crypto_booking(app, booking) == []
    rows_before = _pipeline_rows(app)
    archives_before = _report_archive_counts(app)
    with subprocess.Popen(
        _native_cli_args(root, "report"), cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as child:
        try:
            with owner.transaction():
                owner.execute("SET LOCAL lock_timeout = '5s'")
                owner.execute("LOCK TABLE papersim.queue_control_events IN ACCESS EXCLUSIVE MODE")
                _blocked_report(app, owner.info.backend_pid)
                _write_crypto_booking(root, booking)
                _blocked_report(app, owner.info.backend_pid)
                assert child.poll() is None
                assert _report_archive_counts(app) == archives_before
            rows_after_commit = _pipeline_rows(app)
            changed = {key for key, value in rows_after_commit.items() if value != rows_before[key]}
            assert changed == {"settlements", "bankroll_events"}
            output, stderr = child.communicate(timeout=30)
            in_flight = _check_native_report(root, code=child.returncode, stderr=stderr, output=output)
            assert in_flight == before
        finally:
            _terminate_archive_child(child)
    assert _pipeline_rows(app) == rows_after_commit
    assert _report_archive_counts(app) == archives_before
    after = _capture_native_report(app, root)
    assert "recorded_pnl_gap" not in after.document["stages"]["evaluate"]
    assert after.document["stages"]["outcomes"] == before.document["stages"]["outcomes"]
    assert after.document["stages"]["settle"]["coverage_ratio"] == 1.0
    assert after.document["stages"]["settle"]["as_of"] == _BOOKED_AT.isoformat()
    assert after.document["stages"]["evaluate"]["as_of"] == after.document["as_of"] == _BOOKED_AT.isoformat()
    _assert_recorded_evaluation(root, after.document)
    _assert_native_archives_retained(root, (before, after), record_property)


@pytest.mark.parametrize(("phase", "error"), (
    ("STARTED", "papersim_command_run_conflict"),
    ("SUCCEEDED", "papersim_report_archive_phase_conflict"),
))
def test_partial_report_rejects_an_invalid_archive_attempt_through_the_cli(app, repo, phase, error):
    from polymarket_alpha_lab.papersim import store_psycopg

    root, _ = repo
    record = _crypto_record()
    _seed_initial_cash(app)
    _priced_crypto_scan(app, repo, (record,))
    _write_crypto_outcome(app, record.market_id, "YES")
    partial = _capture_native_report(app, root)
    _assert_pnl_gap(partial.document, _expected_crypto_queue())
    fields = ({"config_digest": "f" * 64} if phase == "STARTED"
              else {"output_evidence": (partial.digest,)})
    inserted = store_psycopg.record_command_run_event(app, report.report_receipt(
        operation_key=partial.operation_key, phase=phase, occurred_at=_BOOKED_AT,
        attempt=2, **fields))
    assert inserted["inserted"] is True
    before = partial.read_again(root)
    assert before["receipts"][:2] == partial.state["receipts"]
    prefix = before["receipts"][2]
    assert (prefix["attempt"], prefix["phase"], prefix["run_event_id"], prefix["occurred_at"]) == (
        2, phase, inserted["run_event_id"], _BOOKED_AT.isoformat())
    rows = _pipeline_rows(app)
    assert _report_archive_counts(app) == (1, 3)
    command = _native_cli(root, "report", "--attempt", "2")
    assert (command.returncode, command.stdout, command.stderr) == (
        1, b"", f"report: FAIL: {error}\n".encode("utf-8"))
    assert _report_archive_counts(app) == (1, 3)
    assert _pipeline_rows(app) == rows
    assert partial.read_again(root) == before


def _equivalent_nba_sources(provider, market, summary):
    gamma = _nba_gamma_snapshot(market)
    score = _nba_score_snapshot(summary)
    if provider == "gamma":
        with_terms_only = deepcopy(market)
        with_terms_only["events"][0].pop("score")
        return (gamma, _nba_gamma_snapshot(with_terms_only, shape="single_market")), score
    second_summary = _nba_score_snapshot({**summary, "news": []})
    second_summary["fetched_at"] -= datetime.timedelta(minutes=1)
    return (score, second_summary), gamma


def _source_digests(*sources):
    return tuple(source["content_sha256"] for source in sources)


def _register_nba_sources(conn, *sources):
    return tuple(_register_basketball_source(
        conn, **{name: row[name] for name in ("content", "kind", "source", "fetched_at")})
        for row in sources)


def _independent_nba_game(market, summary):
    tip = datetime.datetime.fromisoformat(market["gameStartTime"]) - datetime.timedelta(days=1)
    parent, = market["events"]
    slug = f"nba-orl-gsw-{tip.date().isoformat()}"
    other_parent = dict(parent, id="910", gameId=911, eventDate=tip.date().isoformat(),
                        startTime=tip.isoformat(), slug=slug)
    other = dict(market, id="912", conditionId="0x" + "b" * 64, slug=slug,
                 events=[other_parent], gameStartTime=tip.isoformat(),
                 clobTokenIds='["1000000041","1000000042"]')
    other["closedTime"] = (tip + datetime.timedelta(hours=3)).isoformat()
    independent = deepcopy(summary)
    independent["header"]["id"] = "913"
    competition, = independent["header"]["competitions"]
    competition.update(id="913", date=tip.isoformat())
    return other, (_nba_gamma_snapshot(other), _nba_score_snapshot(independent))


def _nba_observation(candidate, source, *, outcome, observed_at, confirmed_at=None, **changes):
    # The market_outcomes schema uses these observation kinds. The registered
    # official URL binds each observation to the exact source or rejects it.
    source_kind = "gamma_close" if source["kind"] == "polymarket-gamma" else "data_api_resolution"
    fields = dict(
        condition_id=candidate.condition_id, market_id=candidate.market_id,
        market_slug=candidate.market_slug, yes_token_id=candidate.yes_token_id,
        no_token_id=candidate.no_token_id, source_kind=source_kind, source_ref=source["source"],
        source_snapshot_sha256=source["content_sha256"], observed_at=observed_at,
        confirmed_at=confirmed_at, outcome=outcome)
    return store.MarketOutcomeObservation(**{**fields, **changes})


def _write_nba_observation(conn, observation):
    from polymarket_alpha_lab.papersim import store_psycopg

    result = store_psycopg.record_market_outcome(conn, observation)
    assert result["inserted"] is True
    persisted = conn.execute(
        "SELECT evidence_key, condition_id, market_id, market_slug, yes_token_id, no_token_id, "
        "source_kind, source_ref, source_snapshot_sha256, outcome, observed_at, confirmed_at "
        "FROM papersim.market_outcomes WHERE outcome_id = %s", (result["outcome_id"],)
    ).fetchone()
    assert persisted == (
        observation.evidence_key(), observation.condition_id, observation.market_id,
        observation.market_slug, observation.yes_token_id, observation.no_token_id,
        observation.source_kind, observation.source_ref, observation.source_snapshot_sha256,
        observation.outcome, observation.observed_at, observation.confirmed_at,
    )


def _assert_nba_final(final, *, candidate_id, market, gamma, score, as_of):
    assert (final["candidate_id"], final["market_ref"], final["side"], final["settlement"]) == (
        candidate_id, market["id"], "home", "win")
    assert (final["home_team"], final["away_team"]) == ("GSW", "ORL")
    assert final["final_score"] == {"home": 118, "away": 112}
    assert final["regulation_score"] == {"home": 110, "away": 110}
    assert final["overtime_periods"] == 1 and final["final_includes_overtime"] is True
    assert final["resolution_period"] == "full_game_including_overtime"
    assert final["evidence_as_of"] == as_of.isoformat()
    provenance = final["provenance"]
    assert set(provenance) == {
        "gamma", "scoreboard", "gamma_event_id", "gamma_game_id", "espn_event_id", "scheduled_tip"}
    assert provenance["gamma"] == {name: gamma[name] for name in ("kind", "content_sha256")}
    assert provenance["scoreboard"] == {name: score[name] for name in ("kind", "content_sha256")}
    assert provenance["gamma_event_id"] == market["events"][0]["id"]
    assert provenance["gamma_game_id"] == str(market["events"][0]["gameId"])
    assert provenance["espn_event_id"] == json.loads(score["content"])["header"]["id"]
    assert provenance["scheduled_tip"] == market["gameStartTime"]


def _assert_nba_clocks(document, *, outcomes_count, outcomes_time, settle_time, overall):
    outcomes, settle = (document["stages"][name] for name in ("outcomes", "settle"))
    assert outcomes["row_count"] == outcomes_count
    assert outcomes["as_of"] == (outcomes_time.isoformat() if outcomes_time else None)
    assert settle["as_of"] == (settle_time.isoformat() if settle_time else None)
    assert document["as_of"] == overall.isoformat()
    assert settle["entries_total"] == settle["entries_settled"] == 0


def _split_nba_sources(baseline, provider, equivalents):
    shown_digest = baseline["provenance"][provider]["content_sha256"]
    displayed, = (row for row in equivalents if row["content_sha256"] == shown_digest)
    alternate, = (row for row in equivalents if row["content_sha256"] != shown_digest)
    assert displayed["content"] != alternate["content"]
    return displayed, alternate


@pytest.mark.parametrize("provider", ("gamma", "scoreboard"))
def test_each_equivalent_nba_source_independently_proves_the_same_game(app, repo, provider, record_property):
    root, _ = repo
    market, summary = _nba_fixture()
    alternatives, independent = _equivalent_nba_sources(provider, market, summary)
    sources = (*alternatives, independent)
    assert _register_nba_sources(app, *sources) == _source_digests(*sources)
    candidates = tuple(
        _basketball_candidate(market, market["events"][0], 1, key=f"independent-source-{index}",
                              refs=_source_digests(source, independent))
        for index, source in enumerate(alternatives)
    )
    recorded = _record_basketball_scan(app, repo, candidates)
    result = _capture_native_report(app, root)
    settle = result.document["stages"]["settle"]
    assert settle["basketball_evidence"]["status"] == "OK"
    assert len(settle["basketball_finals"]) == len(candidates)
    for candidate, source, final in zip(candidates, alternatives, settle["basketball_finals"], strict=True):
        gamma, score = (source, independent) if provider == "gamma" else (independent, source)
        _assert_nba_final(final, candidate_id=recorded["candidate_ids"][candidate.candidate_key],
                          market=market, gamma=gamma, score=score,
                          as_of=max(gamma["fetched_at"], score["fetched_at"]))
    _assert_nba_clocks(result.document, outcomes_count=0, outcomes_time=None,
                       settle_time=independent["fetched_at"], overall=independent["fetched_at"])
    _register_nba_sources(app, _nba_score_snapshot({**summary, "leaders": []}))
    _capture_native_report(app, root, replay=result)
    _assert_native_archives_retained(root, (result,), record_property)


@pytest.mark.parametrize("provider", ("gamma", "scoreboard"))
@pytest.mark.parametrize("citation", ("displayed", "alternate"))
def test_report_rejects_a_contradiction_from_either_verified_nba_source(
        app, repo, provider, citation, record_property):
    root, _ = repo
    market, summary = _nba_fixture()
    alternatives, independent = _equivalent_nba_sources(provider, market, summary)
    sources = (*alternatives, independent)
    candidates_refs = _register_nba_sources(app, *sources)
    candidate = _basketball_candidate(market, market["events"][0], 1, key="position", refs=candidates_refs)
    recorded = _record_basketball_scan(app, repo, (candidate,))
    position = recorded["candidate_ids"][candidate.candidate_key]
    baseline = _capture_native_report(app, root)
    stage = baseline.document["stages"]["settle"]
    initial, = stage["basketball_finals"]
    displayed, alternate = _split_nba_sources(initial, provider, alternatives)
    gamma, score = (displayed, independent) if provider == "gamma" else (independent, displayed)
    _assert_nba_final(initial, candidate_id=position, market=market, gamma=gamma, score=score,
                      as_of=displayed["fetched_at"])
    _assert_nba_clocks(baseline.document, outcomes_count=0, outcomes_time=None,
                       settle_time=displayed["fetched_at"], overall=displayed["fetched_at"])

    cited = displayed if citation == "displayed" else alternate
    confirmed = T0 + datetime.timedelta(minutes=20)
    _write_nba_observation(app, _nba_observation(
        candidate, cited, outcome="NO", observed_at=T0, confirmed_at=confirmed))
    conflict = _capture_native_report(app, root)
    stage = conflict.document["stages"]["settle"]
    expected_problem = {"candidate_id": position, "market_ref": market["id"], "reason": "conflict"}
    assert stage["basketball_finals"] == [], "contrary observation from a verified source left a false final"
    assert stage["basketball_evidence"]["status"] == "UNAVAILABLE"
    assert stage["basketball_evidence"]["unavailable_positions"] == [expected_problem]
    _assert_nba_clocks(conflict.document, outcomes_count=1, outcomes_time=confirmed,
                       settle_time=None, overall=confirmed)

    other_market, other_sources = _independent_nba_game(market, summary)
    other_refs = _register_nba_sources(app, *other_sources)
    other = _basketball_candidate(other_market, other_market["events"][0], 1, key="independent", refs=other_refs)
    further_scan = _record_basketball_scan(app, repo, (other,), operation_key="run-scan|b4-nba|independent")
    partial = _capture_native_report(app, root)
    stage = partial.document["stages"]["settle"]
    assert stage["basketball_evidence"]["status"] == "PARTIAL"
    assert stage["basketball_evidence"]["unavailable_positions"] == [expected_problem]
    remaining, = stage["basketball_finals"]
    _assert_nba_final(remaining, candidate_id=further_scan["candidate_ids"][other.candidate_key],
                      market=other_market, gamma=other_sources[0], score=other_sources[1],
                      as_of=other_sources[1]["fetched_at"])
    _assert_nba_clocks(partial.document, outcomes_count=1, outcomes_time=confirmed,
                       settle_time=other_sources[1]["fetched_at"], overall=confirmed)
    _capture_native_report(app, root, replay=partial)
    _assert_native_archives_retained(root, (baseline, conflict, partial), record_property)


@pytest.mark.parametrize("provider", ("gamma", "scoreboard"))
def test_agreement_from_the_other_nba_source_advances_the_verified_final(app, repo, provider, record_property):
    root, _ = repo
    market, summary = _nba_fixture()
    alternatives, independent = _equivalent_nba_sources(provider, market, summary)
    registration = _register_nba_sources(app, *alternatives, independent)
    candidate = _basketball_candidate(market, market["events"][0], 1, key="position", refs=registration)
    _record_basketball_scan(app, repo, (candidate,))
    baseline = _capture_native_report(app, root)
    final, = baseline.document["stages"]["settle"]["basketball_finals"]
    displayed, alternate = _split_nba_sources(final, provider, alternatives)
    assert displayed == alternatives[0]
    latest_provider_clock = max(row["fetched_at"] for row in (*alternatives, independent))
    _assert_nba_clocks(baseline.document, outcomes_count=0, outcomes_time=None,
                       settle_time=latest_provider_clock, overall=latest_provider_clock)

    confirmed = T0 + datetime.timedelta(minutes=20)
    assert latest_provider_clock < T0 < confirmed
    agreeing = _nba_observation(candidate, alternate, outcome="YES", observed_at=T0, confirmed_at=confirmed)
    _write_nba_observation(app, agreeing)
    advanced = _capture_native_report(app, root)
    advanced_final = dict(final, evidence_as_of=confirmed.isoformat())
    assert advanced.document["stages"]["settle"]["basketball_finals"] == [advanced_final], (
        "agreeing alternate source did not advance the verified final")
    assert advanced_final["provenance"] == final["provenance"]
    _assert_nba_clocks(advanced.document, outcomes_count=1, outcomes_time=confirmed,
                       settle_time=confirmed, overall=confirmed)
    _capture_native_report(app, root, replay=advanced)

    older = replace(agreeing, observed_at=latest_provider_clock,
                    confirmed_at=T0 - datetime.timedelta(minutes=5))
    _write_nba_observation(app, older)
    preserved = _capture_native_report(app, root)
    assert preserved.document["stages"]["settle"] == advanced.document["stages"]["settle"]
    _assert_nba_clocks(preserved.document, outcomes_count=2, outcomes_time=confirmed,
                       settle_time=confirmed, overall=confirmed)

    unknown_time = confirmed + datetime.timedelta(minutes=12)
    _write_nba_observation(app, replace(
        agreeing, outcome="UNKNOWN", observed_at=unknown_time, confirmed_at=None))
    unknown = _capture_native_report(app, root)
    assert unknown.document["stages"]["settle"] == advanced.document["stages"]["settle"]
    assert unknown.document["stages"]["evaluate"] == advanced.document["stages"]["evaluate"]
    _assert_nba_clocks(unknown.document, outcomes_count=3, outcomes_time=unknown_time,
                       settle_time=confirmed, overall=unknown_time)
    _assert_native_archives_retained(root, (baseline, advanced, preserved, unknown), record_property)


@pytest.mark.parametrize("provider", ("gamma", "scoreboard"))
@pytest.mark.parametrize("difference", (-5, 0, 5), ids=("opposing-older", "equal-clocks", "opposing-newer"))
def test_nba_agreement_and_contradiction_are_order_independent(
        app, repo, monkeypatch, provider, difference, record_property):
    root, code_commit = repo
    mirror = root.parent / "mirror"
    subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(root), str(mirror)],
                   check=True, capture_output=True, timeout=15)
    market, summary = _nba_fixture()
    alternatives, independent = _equivalent_nba_sources(provider, market, summary)
    sources = (*alternatives, independent)
    agreed_at = T0 + datetime.timedelta(minutes=15)
    shift = datetime.timedelta(minutes=difference)
    latest_observation = max(agreed_at, agreed_at + shift)

    def run_history(conn, history_root, *, reverse=False):
        ordered_sources = tuple(reversed(sources)) if reverse else sources
        refs = _register_nba_sources(conn, *ordered_sources)
        candidate = _basketball_candidate(market, market["events"][0], 1, key="position", refs=refs)
        position = _record_basketball_scan(conn, (history_root, code_commit), (candidate,))[
            "candidate_ids"][candidate.candidate_key]
        baseline = _capture_native_report(conn, history_root)
        final, = baseline.document["stages"]["settle"]["basketball_finals"]
        shown, alternate = _split_nba_sources(final, provider, alternatives)
        agreeing = _nba_observation(candidate, shown, outcome="YES", observed_at=T0,
                                    confirmed_at=agreed_at)
        # Raw ESPN summaries can have the same source reference. The two
        # supported observation kinds keep equal-clock evidence keys distinct.
        opposing = _nba_observation(candidate, alternate, outcome="NO", observed_at=T0 + shift,
                                    confirmed_at=agreed_at + shift, source_kind="operator_attestation")
        assert opposing.evidence_key() != agreeing.evidence_key()
        order = (opposing, agreeing) if reverse else (agreeing, opposing)
        for observation in order:
            _write_nba_observation(conn, observation)
        conflict = _capture_native_report(conn, history_root)
        settle = conflict.document["stages"]["settle"]
        assert settle["basketball_finals"] == []
        assert settle["basketball_evidence"]["status"] == "UNAVAILABLE"
        assert settle["basketball_evidence"]["unavailable_positions"] == [
            {"candidate_id": position, "market_ref": market["id"], "reason": "conflict"}]
        _assert_nba_clocks(conflict.document, outcomes_count=2, outcomes_time=latest_observation,
                           settle_time=None, overall=latest_observation)
        _capture_native_report(conn, history_root, replay=conflict)
        _assert_native_archives_retained(history_root, (baseline, conflict), record_property)
        return baseline, conflict

    forward = run_history(app, root)
    with _managed_test_postgres(mirror, monkeypatch) as (_, mirror_app):
        reverse = run_history(mirror_app, mirror, reverse=True)
    for first, second in zip(forward, reverse, strict=True):
        assert (first.output, first.operation_key, first.digest) == (
            second.output, second.operation_key, second.digest)


def test_unbound_nba_outcomes_only_advance_the_raw_observation_clock(app, repo, record_property):
    root, _ = repo
    market, summary = _nba_fixture()
    gamma, score = _nba_gamma_snapshot(market), _nba_score_snapshot(summary)
    other_market, other_sources = _independent_nba_game(market, summary)
    invalid = _nba_snapshot({"event": summary}, score["kind"], score["source"])
    sources = (gamma, score, *other_sources, invalid)
    assert _register_nba_sources(app, *sources) == _source_digests(*sources)
    candidate = _basketball_candidate(
        market, market["events"][0], 1, key="position", refs=_source_digests(*sources))
    other = _basketball_candidate(
        other_market, other_market["events"][0], 1, key="other-game", refs=_source_digests(*other_sources))
    result = _record_basketball_scan(app, repo, (candidate, other))
    baseline = _capture_native_report(app, root)
    settle = baseline.document["stages"]["settle"]
    assert settle["basketball_evidence"]["status"] == "OK"
    for expected, source_pair, final in zip((candidate, other), ((gamma, score), other_sources),
                                            settle["basketball_finals"], strict=True):
        _assert_nba_final(final, candidate_id=result["candidate_ids"][expected.candidate_key],
                          market=market if expected is candidate else other_market,
                          gamma=source_pair[0], score=source_pair[1], as_of=score["fetched_at"])

    unrelated = {"condition_id": "0x" + "f" * 64}
    disjoint = {"yes_token_id": "1000009998", "no_token_id": "1000009999"}
    mismatch = {"source_ref": other_sources[1]["source"]}
    missing = {"source_snapshot_sha256": "0" * 64}
    reports = [baseline]
    for index, (source, changes) in enumerate((
        (score, unrelated), (score, disjoint), (score, mismatch), (score, missing),
        (invalid, {}), (other_sources[0], {}), (other_sources[1], {}),
    ), start=1):
        observed = T0 + datetime.timedelta(minutes=index)
        confirmed = observed + datetime.timedelta(minutes=10)
        observation = _nba_observation(candidate, source, outcome="NO", observed_at=observed,
                                       confirmed_at=confirmed, **changes)
        _write_nba_observation(app, observation)
        archive = _capture_native_report(app, root)
        assert archive.document["stages"]["settle"] == settle
        assert archive.document["stages"]["outcomes"]["markets_with_outcome"] == int(index > 1)
        _assert_nba_clocks(archive.document, outcomes_count=index, outcomes_time=confirmed,
                           settle_time=score["fetched_at"], overall=confirmed)
        reports.append(archive)
    _capture_native_report(app, root, replay=reports[-1])
    _assert_native_archives_retained(root, reports, record_property)


def test_an_alternate_nba_source_can_confirm_a_push_but_cannot_prove_a_partial_period(
        app, repo, record_property):
    root, _ = repo
    market, summary = _nba_fixture()
    market.update(
        slug=market["slug"] + "-total-230", sportsMarketType="totals", question="Magic vs. Warriors: O/U 230",
        outcomes='["Over","Under"]', outcomePrices='["0.5","0.5"]', line=230,
        description="\n".join((
            'If the combined final score is more than 230 points, the market will resolve to "Over".',
            'If the combined final score is less than 230 points, the market will resolve to "Under".',
            'If the combined final score is exactly 230 points, the market will resolve 50-50.',
            _FIXTURE_FULL_GAME_RULE,
        )))
    unsupported = dict(
        market, id="919", conditionId="0x" + "e" * 64, slug=market["slug"] + "-1h",
        question="1H Magic vs. Warriors: O/U 230", clobTokenIds='["1000000061","1000000062"]',
        description=market["description"].replace(_FIXTURE_FULL_GAME_RULE, ""))
    alternatives, score = _equivalent_nba_sources("gamma", market, summary)
    incomplete_period = _nba_gamma_snapshot(unsupported)
    refs = _register_nba_sources(app, *alternatives, score, incomplete_period)
    candidate = _basketball_candidate(market, market["events"][0], 1, key="push", refs=refs[:3])
    unsupported_candidate = _basketball_candidate(
        unsupported, unsupported["events"][0], 1, key="unsupported", refs=(refs[2], refs[3]))
    scan_result = _record_basketball_scan(app, repo, (candidate, unsupported_candidate))
    ids = scan_result["candidate_ids"]
    baseline = _capture_native_report(app, root)
    stage = baseline.document["stages"]["settle"]
    final, = stage["basketball_finals"]
    assert (final["candidate_id"], final["settlement"], final["unresolved_reason"]) == (
        ids["push"], None, "push_or_tie")
    assert final["final_score"] == {"home": 118, "away": 112} and final["overtime_periods"] == 1
    assert stage["basketball_evidence"]["status"] == "PARTIAL"
    unsupported_problem = {"candidate_id": ids["unsupported"], "market_ref": unsupported["id"],
                           "reason": "unsupported_period"}
    assert stage["basketball_evidence"]["unavailable_positions"] == [unsupported_problem]
    _, alternate = _split_nba_sources(final, "gamma", alternatives)
    confirmed = T0 + datetime.timedelta(minutes=20)
    voided = _nba_observation(candidate, alternate, outcome="VOIDED", observed_at=T0, confirmed_at=confirmed)
    _write_nba_observation(app, voided)
    advanced = _capture_native_report(app, root)
    stage = advanced.document["stages"]["settle"]
    assert stage["basketball_finals"] == [dict(final, evidence_as_of=confirmed.isoformat())]
    assert stage["basketball_evidence"]["unavailable_positions"] == [unsupported_problem]
    _assert_nba_clocks(advanced.document, outcomes_count=1, outcomes_time=confirmed,
                       settle_time=confirmed, overall=confirmed)

    later = confirmed + datetime.timedelta(minutes=1)
    _write_nba_observation(app, replace(voided, outcome="YES", observed_at=confirmed, confirmed_at=later))
    disputed = _capture_native_report(app, root)
    stage = disputed.document["stages"]["settle"]
    assert stage["basketball_finals"] == []
    assert stage["basketball_evidence"]["status"] == "UNAVAILABLE"
    assert stage["basketball_evidence"]["unavailable_positions"] == [
        {"candidate_id": ids["push"], "market_ref": market["id"], "reason": "conflict"},
        unsupported_problem,
    ]
    _assert_nba_clocks(disputed.document, outcomes_count=2, outcomes_time=later, settle_time=None, overall=later)
    _assert_native_archives_retained(root, (baseline, advanced, disputed), record_property)
