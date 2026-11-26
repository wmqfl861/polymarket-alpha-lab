"""Fake-connection tests for the team evaluation attempt psycopg adapter.

Governing plan: docs/superpowers/plans/2026-07-13-team-forecast-atomic-persistence.md.
Re-imported per test against a fake store module: no real database, real
psycopg, or real store is ever touched here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import importlib
import sys
import types
from typing import Any

import pytest

import polymarket_alpha_lab.team_evidence_aggregation_types as n2t
from polymarket_alpha_lab.team_evidence_aggregation import build_team_evidence_aggregation_result
from polymarket_alpha_lab.team_evidence_aggregation_db_row import TeamEvaluationAttemptDbRow
import polymarket_alpha_lab.team_forecast_build_envelope as n3
from polymarket_alpha_lab.team_forecast_packet import TeamForecastEvidencePacket, TeamForecastPacket

ADAPTER = "polymarket_alpha_lab.team_evidence_aggregation_attempt_psycopg"
STORE = "polymarket_alpha_lab.team_evidence_aggregation_attempt_store"
LOCAL_DSN = "postgresql://postgres:local-secret@localhost:54322/postgres"
REMOTE_DSN = "postgresql://worker:remote-secret-token@db.remote.supabase.co:5432/polymarket"
BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
EVALUATED_AT = BASE_TIME + timedelta(seconds=100)
RECEIPT_TIME = datetime(2026, 7, 13, 8, 59, 30, tzinfo=timezone.utc)
RUN_STARTED_AT = datetime(2026, 7, 13, 9, tzinfo=timezone.utc)
MARKET_SLUG = "will-btc-close-above-105k-on-july-4"
SCOPE_VERSION = "team-evidence-aggregation-psycopg-test-v1"


def digest(label: str) -> str:
    return sha256(label.encode("ascii")).hexdigest()


def node2_config():
    d = Decimal
    return n2t.TeamEvidenceAggregationConfig(
        "generic-test-v1", d("60.000000"), d("20.000000"), d("1.000000"), d("1.000000"), 2,
        d("0.250000"), d("0.750000"), d("0.250000"), d("0.750000"), d("0.110000"), d("0.890000"),
        128, 32, 1024, 256, ())


def root_record(stem):
    lid, ld, cd = f"lineage:{stem}", digest(f"lineage-digest:{stem}"), digest(f"content:{stem}")
    t50, t55, t60, t65 = (BASE_TIME + timedelta(seconds=s) for s in (50, 55, 60, 65))
    cap = n2t.TeamEvidenceCapture(f"capture:{stem}", digest(f"capture-digest:{stem}"), lid, ld, cd, t55)
    rev = n2t.TeamEvidenceRevision(f"evidence:{stem}", digest(f"evidence-digest:{stem}"), None, None,
                                   lid, ld, cd, (), t50, t60)
    asm = n2t.TeamEvidenceAssessmentRevision(
        f"assessment:{stem}", digest(f"assessment-digest:{stem}"), None, None, f"evidence:{stem}",
        digest(f"evidence-digest:{stem}"), t65, Decimal("0.600000"), Decimal("0.200000"),
        digest(f"rationale:{stem}"), f"ind:{stem}", f"corr:{stem}")
    return n2t.TeamEvidenceAggregationRecord(n2t.TeamEvidenceSourceLineage(lid, ld), cap, rev, asm)


def aggregation_input(records):
    return n2t.TeamEvidenceAggregationInput(
        EVALUATED_AT, records, tuple(n2t.TeamEvidenceCurrentRevisionSelection(
            r.evidence_revision.evidence_revision_id, r.evidence_revision.evidence_revision_digest,
            r.assessment_revision.assessment_revision_id, r.assessment_revision.assessment_revision_digest)
            for r in records))


def accepted_receipt(record):
    return n3.TeamForecastEvaluatorReceipt(
        f"evaluator:{record.capture.capture_id}", digest(f"evaluator-input:{record.capture.capture_id}"),
        RECEIPT_TIME, "accepted", (record.source_lineage.source_lineage_id, record.capture.capture_id,
                                   record.evidence_revision.evidence_revision_id,
                                   record.assessment_revision.assessment_revision_id),
        ("evaluator_submitted",))


def forecast_template() -> TeamForecastPacket:
    return TeamForecastPacket(
        "forecast-template-001", "crypto_btc", "0xnode5condition", MARKET_SLUG,
        "Will BTC close above $105,000 on July 4?", "finance.crypto.btc", "crypto_price_threshold", "yes",
        Decimal("0.500000"), Decimal("0.700000"), Decimal("0.650000"), Decimal("0.900000"),
        Decimal("0.120000"), Decimal("0.540000"), Decimal("0.570000"), ("aggregation_ready",),
        ("btc-memory-2026-q2",), ("source-etf-flow-dashboard",), ("weekend_liquidity_gap",),
        "team-forecast-v0", "team-forecast-prompt-v3", RUN_STARTED_AT)


def evidence_template(record) -> TeamForecastEvidencePacket:
    return TeamForecastEvidencePacket(
        "evidence-template-001", "crypto_btc", MARKET_SLUG,
        f"source-{record.source_lineage.source_lineage_id}", "team_evaluator", RECEIPT_TIME, 60,
        "team_assessment", "Evaluator assessment bound to the accepted record identity.",
        record.assessment_revision.requested_weight, ("evaluator_submitted",))


def make_envelope(stem="alpha", label="node5-psycopg-001") -> n3.TeamForecastBuildEnvelope:
    records = (root_record(stem),)
    config_value = node2_config()
    result = build_team_evidence_aggregation_result(aggregation_input(records), config=config_value)
    return n3.build_team_forecast_build_envelope(
        result, aggregation_input=aggregation_input(records), config=config_value,
        scope=n3.TeamForecastEvaluationScope(
            SCOPE_VERSION, (("team_id", "crypto_btc"), ("market_slug", MARKET_SLUG)),
            ("gamma:market/12345", "clob:book/0xabc")),
        run_metadata=n3.TeamForecastRunMetadata(
            label, "team-forecast-generator-v1", "team-forecast-prompt-v3",
            RUN_STARTED_AT, RUN_STARTED_AT + timedelta(minutes=5)),
        evaluator_receipts=tuple(accepted_receipt(r) for r in records),
        legacy_forecast_packet=forecast_template(),
        legacy_evidence_packets=tuple(evidence_template(r) for r in records))


@dataclass(frozen=True)
class FakeWriteResult:
    tea_id: str
    inserted: bool


class FakeAdapters:
    def __init__(self) -> None:
        self.registered: list[tuple[type, type]] = []

    def register_dumper(self, cls: type, dumper: type) -> None:
        self.registered.append((cls, dumper))


class FakeConnection:
    def __init__(self) -> None:
        self.commit_count = self.rollback_count = self.close_count = 0
        self.adapters = FakeAdapters()

    def commit(self) -> None: self.commit_count += 1
    def rollback(self) -> None: self.rollback_count += 1
    def close(self) -> None: self.close_count += 1


class FakeCommitFailingConnection(FakeConnection):
    def commit(self) -> None:
        self.commit_count += 1; raise RuntimeError("commit failed without dsn")


class FakeCloseFailingConnection(FakeConnection):
    def close(self) -> None:
        self.close_count += 1; raise RuntimeError("close failed without dsn")


class FakeJsonbDumper:
    pass


@pytest.fixture(autouse=True)
def _reset_adapter_module_cache():
    sys.modules.pop(ADAPTER, None)
    yield
    sys.modules.pop(ADAPTER, None)


def _install_fake_psycopg(monkeypatch: pytest.MonkeyPatch, *, connect: Any) -> None:
    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=connect))
    monkeypatch.setitem(sys.modules, "psycopg.types.json", types.SimpleNamespace(
        JsonbDumper=FakeJsonbDumper))


def _unexpected_store_call(name: str) -> Any:
    def raise_unexpected(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"unexpected {name} store call")
    return raise_unexpected


def _install_fake_store(monkeypatch: pytest.MonkeyPatch, *, atomic: Any | None = None,
                        legacy: Any | None = None, rows: Any | None = None,
                        latest: Any | None = None) -> types.ModuleType:
    store_module = types.ModuleType(STORE)
    for name, fn, label in (
        ("_insert_attempt_rows_atomic", atomic, "atomic writer"),
        ("insert_team_evaluation_attempt", legacy, "legacy insert"),
        ("load_team_evaluation_attempt_rows", rows, "rows loader"),
        ("load_latest_team_evaluation_attempt", latest, "latest loader"),
    ):
        setattr(store_module, name, fn if fn is not None else _unexpected_store_call(label))
    monkeypatch.setitem(sys.modules, STORE, store_module)
    return store_module


def _import_adapter(monkeypatch: pytest.MonkeyPatch, *, atomic: Any | None = None,
                    legacy: Any | None = None, rows: Any | None = None,
                    latest: Any | None = None) -> types.ModuleType:
    _install_fake_store(monkeypatch, atomic=atomic, legacy=legacy, rows=rows, latest=latest)
    return importlib.import_module(ADAPTER)


def _block_psycopg_imports(monkeypatch: pytest.MonkeyPatch, *, missing: bool) -> None:
    # Cached modules bypass find_spec. Restore them at teardown for other tests.
    for name in tuple(sys.modules):
        if name == "psycopg" or name.startswith("psycopg."):
            monkeypatch.delitem(sys.modules, name)

    class BlockingFinder:
        def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> None:
            if fullname == "psycopg" and missing:
                raise ModuleNotFoundError("No module named 'psycopg'", name="psycopg")
            if fullname == "psycopg" or fullname.startswith("psycopg."):
                raise AssertionError("psycopg must not be imported yet")

    monkeypatch.setattr(sys, "meta_path", [BlockingFinder(), *sys.meta_path])


def _spy_dsn_validator(monkeypatch: pytest.MonkeyPatch, adapter: types.ModuleType,
                       events: list[str]) -> None:
    original = adapter.validate_local_postgres_dsn

    def spied(value: str, *, env_var_name: str) -> None:
        events.append("validate"); original(value, env_var_name=env_var_name)

    monkeypatch.setattr(adapter, "validate_local_postgres_dsn", spied)


def _assert_no_dsn(message: str) -> None:
    assert "postgresql://" not in message
    assert "local-secret" not in message and "remote-secret-token" not in message


def _ok_writer(events: list[str] | None = None, calls: list[Any] | None = None):
    def writer(connection: Any, rows: Any, *, table_name: str) -> Any:
        if events is not None: events.append("write")
        if calls is not None: calls.append((connection, rows, table_name))
        return tuple(FakeWriteResult(row.tea_id, True) for row in rows)
    return writer


def test_public_exports_and_module_import_stay_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_psycopg_imports(monkeypatch, missing=True)
    adapter = _import_adapter(monkeypatch)
    assert adapter.__all__ == (
        "insert_team_evaluation_attempt_with_psycopg",
        "persist_team_evaluation_attempts_with_psycopg",
    )
    for name in ("insert_team_evaluation_attempts_with_psycopg", "load_latest_team_evaluation_attempt_with_psycopg",
                 "load_team_evaluation_attempts_with_psycopg"):
        assert callable(getattr(adapter, name))
    assert "psycopg" not in sys.modules


@pytest.mark.parametrize("orphan", ("tea", "tfr", "run_metadata_absent", "run_metadata_malformed"))
def test_orphan_bindings_rejected_before_dsn_validation_and_psycopg_import(
    monkeypatch: pytest.MonkeyPatch, orphan: str,
) -> None:
    envelope = make_envelope()
    payload = n3.team_forecast_evaluation_scope_payload(envelope)
    if orphan == "tea":
        wrong_tea = "tea:v1:" + "0" * 64
        invalid = replace(envelope, tea_id=wrong_tea,
                          tfr_id=n3.team_forecast_run_id(wrong_tea, payload["run_metadata"]))
    elif orphan == "tfr":
        invalid = replace(envelope, tfr_id="tfr:v1:" + "1" * 64)
    elif orphan == "run_metadata_absent":
        invalid = replace(envelope, evaluation_scope_payload={
            key: value for key, value in payload.items() if key != "run_metadata"})
    else:
        invalid = replace(envelope, evaluation_scope_payload=dict(payload, run_metadata=123))
    match = {"tea": "tea_id", "tfr": "tfr_id"}.get(orphan, "run_metadata")
    events: list[str] = []
    _block_psycopg_imports(monkeypatch, missing=False)
    adapter = _import_adapter(monkeypatch)
    _spy_dsn_validator(monkeypatch, adapter, events)
    with pytest.raises(ValueError, match=match):
        adapter.insert_team_evaluation_attempts_with_psycopg(LOCAL_DSN, (invalid,))
    assert events == []


@pytest.mark.parametrize("envelopes", ((), [make_envelope()], (make_envelope(), object()), "x"))
def test_invalid_batch_shape_rejected_before_dsn_validation_and_psycopg_import(
    monkeypatch: pytest.MonkeyPatch, envelopes: Any,
) -> None:
    events: list[str] = []
    _block_psycopg_imports(monkeypatch, missing=False)
    adapter = _import_adapter(monkeypatch)
    _spy_dsn_validator(monkeypatch, adapter, events)
    with pytest.raises(ValueError, match="envelopes"):
        adapter.insert_team_evaluation_attempts_with_psycopg(LOCAL_DSN, envelopes)
    assert events == []


@pytest.mark.parametrize("table_name", ("", "UPPER_CASE", "a.b.c", "1starts-with-digit", "x" * 64, 7))
def test_invalid_table_name_rejected_before_dsn_validation_and_psycopg_import(
    monkeypatch: pytest.MonkeyPatch, table_name: Any,
) -> None:
    events: list[str] = []
    _block_psycopg_imports(monkeypatch, missing=False)
    adapter = _import_adapter(monkeypatch)
    _spy_dsn_validator(monkeypatch, adapter, events)
    with pytest.raises(ValueError, match="table_name"):
        adapter.insert_team_evaluation_attempts_with_psycopg(
            LOCAL_DSN, (make_envelope(),), table_name=table_name)
    assert events == []


def test_remote_dsn_rejected_before_psycopg_import_without_echoing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _block_psycopg_imports(monkeypatch, missing=False)
    adapter = _import_adapter(monkeypatch)
    with pytest.raises(ValueError) as exc_info:
        adapter.insert_team_evaluation_attempts_with_psycopg(REMOTE_DSN, (make_envelope(),))

    message = str(exc_info.value)
    assert "POLYMARKET_ALPHA_LAB_TEAM_EVIDENCE_AGGREGATION_DB_DSN" in message
    assert REMOTE_DSN not in message and "db.remote.supabase.co" not in message
    _assert_no_dsn(message)
    assert "psycopg" not in sys.modules


def test_missing_psycopg_error_mentions_install_extra_without_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _block_psycopg_imports(monkeypatch, missing=True)
    adapter = _import_adapter(monkeypatch)
    with pytest.raises(RuntimeError) as exc_info:
        adapter.insert_team_evaluation_attempts_with_psycopg(LOCAL_DSN, (make_envelope(),))

    message = str(exc_info.value)
    assert "psycopg is required" in message and "postgres extra" in message; _assert_no_dsn(message)


def test_atomic_write_validates_then_connects_then_writes_and_commits_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    events: list[str] = []
    connect_calls: list[str] = []
    writer_calls: list[Any] = []
    envelopes = (make_envelope("gamma", "node5-psycopg-g"), make_envelope("alpha", "node5-psycopg-a"),
                 make_envelope("beta", "node5-psycopg-b"))
    assert len({e.tea_id for e in envelopes}) == 3
    _install_fake_psycopg(
        monkeypatch, connect=lambda dsn: events.append("connect") or connect_calls.append(dsn) or connection)
    adapter = _import_adapter(monkeypatch, atomic=_ok_writer(events, writer_calls))
    _spy_dsn_validator(monkeypatch, adapter, events)

    results = adapter.insert_team_evaluation_attempts_with_psycopg(
        LOCAL_DSN, envelopes, table_name="public.team_evaluation_attempts")

    assert events == ["validate", "connect", "write"] and connect_calls == [LOCAL_DSN]
    assert [r.tea_id for r in results] == [e.tea_id for e in envelopes]  # input order kept
    assert all(r.inserted for r in results)
    writer_connection, rows, table_name = writer_calls[0]
    assert writer_connection is connection  # raw owned connection reaches the store
    assert table_name == "public.team_evaluation_attempts"
    assert [row.tea_id for row in rows] == [e.tea_id for e in envelopes]
    assert all(isinstance(row, TeamEvaluationAttemptDbRow) and row.paper_only is True
               and row.report_only is True and row.readonly is True for row in rows)
    assert connection.commit_count == 1 and connection.rollback_count == 0
    assert connection.close_count == 1


def test_atomic_write_duplicate_retry_sends_identical_rows_and_returns_writer_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    envelopes = (make_envelope("alpha", "node5-psycopg-retry"),)
    writer_calls: list[Any] = []
    _install_fake_psycopg(monkeypatch, connect=lambda dsn: connection)

    def writer(connection_arg: Any, rows: Any, *, table_name: str) -> Any:
        writer_calls.append(rows)
        inserted = len(writer_calls) == 1
        return tuple(FakeWriteResult(row.tea_id, inserted) for row in rows)

    adapter = _import_adapter(monkeypatch, atomic=writer)
    first = adapter.insert_team_evaluation_attempts_with_psycopg(LOCAL_DSN, envelopes)
    second = adapter.insert_team_evaluation_attempts_with_psycopg(LOCAL_DSN, envelopes)

    assert [r.inserted for r in first] == [True] and [r.inserted for r in second] == [False]
    assert writer_calls[0] == writer_calls[1]  # retry changes nothing about the rows
    assert connection.commit_count == 2 and connection.rollback_count == 0
    assert connection.close_count == 2



def test_atomic_write_store_failure_rolls_back_closes_reraises_without_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    _install_fake_psycopg(monkeypatch, connect=lambda dsn: connection)

    def writer(connection_arg: Any, parameter_rows: Any, *, table_name: str) -> Any:
        raise ValueError("store failed without dsn")

    adapter = _import_adapter(monkeypatch, atomic=writer)
    with pytest.raises(ValueError) as exc_info:
        adapter.insert_team_evaluation_attempts_with_psycopg(LOCAL_DSN, (make_envelope(),))

    assert "store failed without dsn" in str(exc_info.value)
    _assert_no_dsn(str(exc_info.value))
    assert connection.commit_count == 0 and connection.rollback_count == 1
    assert connection.close_count == 1


def test_atomic_write_store_base_exception_rolls_back_closes_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    _install_fake_psycopg(monkeypatch, connect=lambda dsn: connection)

    def writer(connection_arg: Any, parameter_rows: Any, *, table_name: str) -> Any:
        raise KeyboardInterrupt

    adapter = _import_adapter(monkeypatch, atomic=writer)
    with pytest.raises(KeyboardInterrupt):
        adapter.insert_team_evaluation_attempts_with_psycopg(LOCAL_DSN, (make_envelope(),))
    assert connection.commit_count == 0 and connection.rollback_count == 1
    assert connection.close_count == 1


def test_atomic_write_commit_failure_rolls_back_closes_reraises_without_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeCommitFailingConnection()
    _install_fake_psycopg(monkeypatch, connect=lambda dsn: connection)
    adapter = _import_adapter(monkeypatch, atomic=_ok_writer())
    with pytest.raises(RuntimeError) as exc_info:
        adapter.insert_team_evaluation_attempts_with_psycopg(LOCAL_DSN, (make_envelope(),))

    assert "commit failed without dsn" in str(exc_info.value)
    _assert_no_dsn(str(exc_info.value))
    assert connection.commit_count == 1 and connection.rollback_count == 1
    assert connection.close_count == 1


def test_atomic_write_result_count_mismatch_rolls_back_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    _install_fake_psycopg(monkeypatch, connect=lambda dsn: connection)

    def writer(connection_arg: Any, rows: Any, *, table_name: str) -> Any:
        return (FakeWriteResult(rows[0].tea_id, True),)

    adapter = _import_adapter(monkeypatch, atomic=writer)
    with pytest.raises(ValueError, match="one write result per"):
        adapter.insert_team_evaluation_attempts_with_psycopg(
            LOCAL_DSN, (make_envelope("alpha", "node5-a"), make_envelope("beta", "node5-b")))
    assert connection.commit_count == 0 and connection.rollback_count == 1
    assert connection.close_count == 1


def test_atomic_write_success_close_failure_propagates_without_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeCloseFailingConnection()
    _install_fake_psycopg(monkeypatch, connect=lambda dsn: connection)
    adapter = _import_adapter(monkeypatch, atomic=_ok_writer())
    with pytest.raises(RuntimeError) as exc_info:
        adapter.insert_team_evaluation_attempts_with_psycopg(LOCAL_DSN, (make_envelope(),))
    assert "close failed without dsn" in str(exc_info.value)
    _assert_no_dsn(str(exc_info.value))
    assert connection.commit_count == 1 and connection.rollback_count == 0
    assert connection.close_count == 1


def test_atomic_write_registers_dict_jsonb_dumper_on_owned_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    _install_fake_psycopg(monkeypatch, connect=lambda dsn: connection)
    adapter = _import_adapter(monkeypatch, atomic=_ok_writer())

    results = adapter.insert_team_evaluation_attempts_with_psycopg(
        LOCAL_DSN, (make_envelope(),))

    assert [r.inserted for r in results] == [True]
    assert connection.adapters.registered == [(dict, FakeJsonbDumper)] and connection.commit_count == 1
    assert connection.close_count == 1


def test_dumper_registration_failure_rolls_back_closes_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    _install_fake_psycopg(monkeypatch, connect=lambda dsn: connection)

    def fail_register(cls: type, dumper: type) -> None:
        raise RuntimeError("register dumper failed without dsn")

    monkeypatch.setattr(connection.adapters, "register_dumper", fail_register)
    adapter = _import_adapter(monkeypatch)  # atomic-writer fake raises if reached
    with pytest.raises(RuntimeError) as exc_info:
        adapter.insert_team_evaluation_attempts_with_psycopg(LOCAL_DSN, (make_envelope(),))
    assert "register dumper failed without dsn" in str(exc_info.value)
    _assert_no_dsn(str(exc_info.value))
    assert connection.commit_count == 0 and connection.rollback_count == 1
    assert connection.close_count == 1


def test_connect_failure_raises_redacted_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_connect(dsn: str) -> FakeConnection:
        raise RuntimeError(f"connection failed for {dsn}")

    _install_fake_psycopg(monkeypatch, connect=fail_connect)
    adapter = _import_adapter(monkeypatch)
    with pytest.raises(RuntimeError) as exc_info:
        adapter.insert_team_evaluation_attempts_with_psycopg(LOCAL_DSN, (make_envelope(),))
    assert "failed to connect" in str(exc_info.value)
    _assert_no_dsn(str(exc_info.value))


def test_persist_alias_routes_the_same_atomic_writer_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    writer_calls: list[Any] = []
    _install_fake_psycopg(monkeypatch, connect=lambda dsn: connection)
    adapter = _import_adapter(monkeypatch, atomic=_ok_writer(calls=writer_calls))
    envelopes = (make_envelope("alpha", "node5-persist-alias"),)

    results = adapter.persist_team_evaluation_attempts_with_psycopg(LOCAL_DSN, envelopes)

    assert [r.tea_id for r in results] == [envelopes[0].tea_id]
    assert [row.tea_id for row in writer_calls[0][1]] == [envelopes[0].tea_id]
    assert connection.commit_count == 1 and connection.rollback_count == 0 and connection.close_count == 1


def test_read_wrappers_delegate_to_store_loaders_over_owned_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    connect_calls: list[str] = []
    rows_sentinel, latest_sentinel = object(), object()
    seen: dict[str, Any] = {}
    _install_fake_psycopg(
        monkeypatch, connect=lambda dsn: connect_calls.append(dsn) or connection)

    def fake_rows(connection_arg: Any, *, tfr_id, scope_version, scope_key, limit, table_name):
        seen["rows"] = (connection_arg, tfr_id, scope_version, scope_key, limit, table_name)
        return (rows_sentinel,)

    def fake_latest(connection_arg: Any, *, tfr_id, scope_version, scope_key, table_name):
        seen["latest"] = (connection_arg, tfr_id, scope_version, scope_key, table_name)
        return latest_sentinel

    adapter = _import_adapter(monkeypatch, rows=fake_rows, latest=fake_latest)
    rows = adapter.load_team_evaluation_attempts_with_psycopg(
        LOCAL_DSN, tfr_id="tfr:v1:" + digest("run"), limit=10)
    latest = adapter.load_latest_team_evaluation_attempt_with_psycopg(
        LOCAL_DSN, scope_version="v1", scope_key=digest("scope"))

    assert rows == (rows_sentinel,) and latest is latest_sentinel
    assert connect_calls == [LOCAL_DSN, LOCAL_DSN]
    for key in ("rows", "latest"):
        assert seen[key][0] is connection  # loaders get the raw owned connection
    assert seen["rows"][1:] == ("tfr:v1:" + digest("run"), None, None, 10, "team_evaluation_attempts")
    assert seen["latest"][1:] == (None, "v1", digest("scope"), "team_evaluation_attempts")
    assert connection.commit_count == 2 and connection.rollback_count == 0
    assert connection.close_count == 2


@pytest.mark.parametrize("v1_prefix", ("tea:v1:", "tfr:v1:", "tfe:v1:"))
@pytest.mark.parametrize("field_name", ("tea_id", "tfr_id", "tfe_id"))
def test_legacy_insert_rejects_v1_identifiers_before_any_connection_activity(
    monkeypatch: pytest.MonkeyPatch, v1_prefix: str, field_name: str,
) -> None:
    row = types.SimpleNamespace(
        tea_id=v1_prefix + digest("legacy") if field_name == "tea_id" else "legacy-tea",
        tfr_id=v1_prefix + digest("legacy") if field_name == "tfr_id" else "legacy-tfr",
        tfe_id=v1_prefix + digest("legacy") if field_name == "tfe_id" else None,
        evaluation_scope_payload={"note": "plain"})
    events: list[str] = []
    _block_psycopg_imports(monkeypatch, missing=False)
    adapter = _import_adapter(monkeypatch)  # default fakes raise if any store call happens
    _spy_dsn_validator(monkeypatch, adapter, events)
    with pytest.raises(ValueError, match=v1_prefix):
        adapter.insert_team_evaluation_attempt_with_psycopg(LOCAL_DSN, row)
    assert events == []


def test_legacy_insert_rejects_v1_identifier_inside_legacy_payload_before_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = types.SimpleNamespace(
        tea_id="legacy-tea", tfr_id="legacy-tfr",
        evaluation_scope_payload={"nested": ["plain", "tfe:v1:" + digest("deep")]})
    events: list[str] = []
    _block_psycopg_imports(monkeypatch, missing=False)
    adapter = _import_adapter(monkeypatch)
    _spy_dsn_validator(monkeypatch, adapter, events)
    with pytest.raises(ValueError, match="tfe:v1:"):
        adapter.insert_team_evaluation_attempt_with_psycopg(LOCAL_DSN, row)
    assert events == []


def test_legacy_insert_delegates_to_public_store_insert_for_non_v1_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    row = types.SimpleNamespace(tea_id="legacy-tea", tfr_id="legacy-tfr")
    sentinel = object()
    legacy_calls: list[Any] = []
    _install_fake_psycopg(monkeypatch, connect=lambda dsn: connection)

    def legacy_insert(connection_arg: Any, row_arg: Any, *, table_name: str) -> Any:
        legacy_calls.append((connection_arg, row_arg, table_name))
        return sentinel

    adapter = _import_adapter(monkeypatch, legacy=legacy_insert)
    returned = adapter.insert_team_evaluation_attempt_with_psycopg(
        LOCAL_DSN, row, table_name="legacy_attempts")

    assert returned is sentinel
    legacy_connection, legacy_row, legacy_table = legacy_calls[0]
    assert legacy_connection is connection  # raw owned connection reaches the store
    assert legacy_row is row and legacy_table == "legacy_attempts"
    assert connection.commit_count == 1 and connection.rollback_count == 0
    assert connection.close_count == 1
