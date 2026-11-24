"""Synthetic originals and a caller-owned transaction for FX-41 registration."""

import gzip
import json
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace

import pytest

from polymarket_alpha_lab.papersim import nba_odr_registration as registration
from polymarket_alpha_lab.papersim import snapshots, store, store_psycopg
from polymarket_alpha_lab.papersim.optimize import nba_odr, runner
from polymarket_alpha_lab.papersim.optimize.families.nba import NBA_PACE_EFFICIENCY
from polymarket_alpha_lab.papersim.optimize.registry import LineageRoles
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec

_OBSERVED = datetime(2026, 11, 22, 0, 12, 34, 567890, tzinfo=UTC)
_WRITTEN = _OBSERVED + timedelta(seconds=1)
_IDENTITY = {"code_commit": "a" * 40, "instance_sha256": "b" * 64, "migration_version": "20261107000000"}
_EVENT_INSERT_COLUMNS = registration._EVENT_COLUMNS[1:]
_EVENT_COMPARE_COLUMNS = tuple(name for name in _EVENT_INSERT_COLUMNS if name not in {"run_event_key", "occurred_at"})


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def execute(self, statement, params):
        assert self.conn.info.transaction_status.name == "INTRANS"
        key = params[0]
        if statement == snapshots._SNAPSHOT_INSERT:
            self.conn.writes.append(("snapshot", key))
            if key not in self.conn.snapshots:
                self.conn.snapshots[key] = dict(zip(snapshots._ROW_COLUMNS, params[1:], strict=True))
                self.row = (key,)
        elif statement == snapshots._SNAPSHOT_SELECT:
            row = self.conn.snapshots.get(key)
            self.row = None if row is None else tuple(row[name] for name in snapshots._ROW_COLUMNS)
        elif statement == registration._EVENT_SELECT:
            row = self.conn.events.get(key)
            self.row = None if row is None else tuple(row[name] for name in registration._EVENT_COLUMNS)
        elif statement.startswith("SELECT operation_key, attempt, phase, command,"):
            row = self.conn.events.get(key)
            self.row = None if row is None else tuple(row[name] for name in _EVENT_COMPARE_COLUMNS)
        elif statement == store_psycopg._COMMAND_RUN_INSERT:
            self.conn.writes.append(("event", key))
            assert key not in self.conn.events
            event = dict(zip(_EVENT_INSERT_COLUMNS, params, strict=True))
            event["run_event_id"] = 53
            for name in ("input_evidence", "output_evidence"):
                event[name] = json.loads(event[name])
            self.conn.events[key] = event
            self.row = (event["run_event_id"],)
        else:
            pytest.fail(f"Unexpected registration statement {statement!r}")

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self):
        self.info = SimpleNamespace(transaction_status=SimpleNamespace(name="IDLE"))
        self.snapshots = {}
        self.events = {}
        self.writes = []

    def cursor(self):
        return _Cursor(self)

    @contextmanager
    def transaction(self):
        previous_status = self.info.transaction_status.name
        previous = deepcopy(self.snapshots), deepcopy(self.events)
        self.info.transaction_status.name = "INTRANS"
        try:
            yield
        except BaseException:
            self.snapshots, self.events = previous
            raise
        finally:
            self.info.transaction_status.name = previous_status


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _ref(raw):
    return runner.SourceArtifactRef(sha256(raw).hexdigest(), len(raw))


def _forbidden(*args, **kwargs):
    pytest.fail("Unexpected callback")


def _decode(rows):
    return {lineage: runner.RegistryComparison(
        LineageRoles(**row["roles"]), ModelSpec(**store_psycopg._spec_sequences_to_tuples(row["spec"])), row["issue"],
    ) for lineage, row in rows.items()}


@pytest.fixture
def request_arguments(monkeypatch):
    # Preserve original formatting and the independently stamped source clocks.
    approval = json.dumps({"decision_observed_utc": registration._APPROVAL_AT.isoformat()}, indent=2).encode()
    inventory_facts = nba_odr.NBA_ODR_INDEPENDENT_ESPN
    inventory = json.dumps({"utc": inventory_facts.inventory_observed_at.isoformat()}, indent=2).encode()
    monkeypatch.setattr(registration, "_APPROVAL_REF", _ref(approval))
    monkeypatch.setattr(nba_odr, "NBA_ODR_INDEPENDENT_ESPN", replace(
        inventory_facts, inventory_sha256=_ref(inventory).sha256, inventory_byte_length=len(inventory),
    ))
    archives = {}
    pins = {}
    for season, pin in nba_odr.NBA_ODR_ARCHIVE_PINS.items():
        decoded = _encode({"season": season, "retrieved_at": pin.retrieved_at.isoformat(), "games": []})
        raw = gzip.compress(decoded, mtime=0)
        archives[season] = raw
        pins[season] = replace(pin, gzip_sha256=_ref(raw).sha256, gzip_byte_length=len(raw),
                               decoded_sha256=_ref(decoded).sha256, decoded_byte_length=len(decoded))
    monkeypatch.setattr(nba_odr, "NBA_ODR_ARCHIVE_PINS", pins)

    baseline = NBA_PACE_EFFICIENCY.baseline_spec()
    spec = {"family": baseline.family, "procedure_version": baseline.procedure_version,
            "params": store_psycopg._plain_json(baseline.params), "envelope": store_psycopg._plain_json(baseline.envelope),
            "data_contract": baseline.data_contract, "legacy_model_version": baseline.legacy_model_version}
    comparisons = {}
    for index, lineage in enumerate(registration.NBA_ODR_REGISTRATION_LINEAGES, 1):
        digest = baseline.spec_sha256()
        head = {"spec_sha256": digest, "effective_at": (_OBSERVED - timedelta(days=1)).isoformat(), "id": index}
        comparisons[lineage] = {"roles": asdict(LineageRoles(lineage, digest, digest)), "spec": spec,
                                "baseline_spec_sha256": digest, "issue": None,
                                "watermarks": {"BASELINE": head, "ACTIVE": head}, "head": head}

    def metadata_plan(*, content_by_season, code_commit, policy_context, model_contexts):
        assert content_by_season == archives
        assert code_commit == _IDENTITY["code_commit"]
        assert policy_context.started_at == _OBSERVED
        assert policy_context.registry == _decode(comparisons)
        assert model_contexts == {"moneyline": "shared", "totals": "shared"}
        protocol = runner.nba_odr_historical_protocol(_ref(b"synthetic materials"))
        descriptor = runner.optimization_search_descriptor(
            NBA_PACE_EFFICIENCY, schedule_id=runner.NBA_ODR_SCHEDULE_ID, data_cutoff=nba_odr.NBA_ODR_AS_OF,
        )
        run_key = store.optimization_run_key(baseline.family, nba_odr.NBA_ODR_AS_OF, protocol["rule_version"],
                                             protocol["seed"], descriptor)
        plan_bytes = _encode([[0, "2024-25", "2024-10-25", "f" * 64, baseline.spec_sha256(), "shared", list(model_contexts)]])
        metadata_bytes = _encode({"policy_watermark": policy_context.watermark, "plan": asdict(_ref(plan_bytes))})
        return runner.NBAODRPlan(run_key, None, (), (), (), (), plan_bytes, metadata_bytes)

    monkeypatch.setattr(runner, "build_nba_odr_plan", metadata_plan)
    monkeypatch.setattr(nba_odr, "load_nba_odr_archives", _forbidden)
    return {"approval_bytes": approval, "source_inventory_bytes": inventory, "content_by_season": archives,
            "read_comparisons": lambda: deepcopy(comparisons)}


def _write(conn, originals, **overrides):
    timestamps = iter((_OBSERVED, _WRITTEN))
    arguments = {**_IDENTITY, **originals, "decode_comparisons": _decode,
                 "record_command_run_event": store_psycopg.record_command_run_event,
                 "wall_clock": lambda: next(timestamps), **overrides}
    with conn.transaction():
        result = registration._write_nba_odr_registration(conn, **arguments)
    return result


def _read(conn, **overrides):
    with conn.transaction():
        return registration._read_nba_odr_registration(conn, **{**_IDENTITY, "decode_comparisons": _decode, **overrides})


def test_round_trip_binds_exact_snapshots_manifest_and_first_receipt(request_arguments):
    conn = _Connection()
    first = _write(conn, request_arguments)
    writes = conn.writes.copy()
    assert [operation for operation, _ in writes] == ["snapshot"] * 9 + ["event"]
    assert len(conn.snapshots) == 9
    event_key = store.command_run_event_key(f"optimize.register-experiment/{runner.NBA_ODR_SCHEDULE_ID}", 1, "SUCCEEDED")
    assert tuple(conn.events) == (event_key,)
    event = conn.events[event_key]
    assert event["command"] == "optimize-register-experiment"
    assert first.run_event_id == event["run_event_id"] == 53
    assert first.run_event_key == event_key
    assert first.occurred_at == event["occurred_at"] == _WRITTEN
    assert first.policy_context.started_at == _OBSERVED
    assert first.config_digest == event["config_digest"] == sha256(_encode({
        field: event[field] for field in ("input_evidence", "output_evidence")
    })).hexdigest()
    assert first.content_by_season == request_arguments["content_by_season"]
    with pytest.raises(TypeError):
        first.content_by_season["2024-25"] = b"replacement"

    assert first.pointer == {"schema_version": 1, "manifest_sha256": _ref(first.manifest_bytes).sha256,
                             "manifest_byte_length": len(first.manifest_bytes)}
    manifest = json.loads(first.manifest_bytes)
    assert set(manifest) == {"schema_version", "protocol", "sources"}
    assert first.manifest_bytes == _encode(manifest)
    assert manifest["protocol"] == runner.nba_odr_historical_protocol(first.materials_ref)
    assert manifest["protocol"]["budget"]["max_trials"] == 4_811
    present = [source for source in manifest["sources"] if source["sha256"] is not None]
    absent = [source for source in manifest["sources"] if source["sha256"] is None]
    assert [(source["role"], source["key"]) for source in present] == [
        ("nba_season_archive", pin.relpath) for pin in nba_odr.NBA_ODR_ARCHIVE_PINS.values()
    ]
    assert [(source["role"], source["key"]) for source in absent] == [
        (role, f"{role}/{season}/originals") for season in nba_odr.NBA_ODR_ARCHIVE_PINS
        for role in ("team_schedule", "event_summary")
    ]
    assert all(source["required"] is False and source["byte_length"] is None for source in absent)
    assert len({source["absent_reason"] for source in absent}) == 2
    for source, pin in zip(present, nba_odr.NBA_ODR_ARCHIVE_PINS.values(), strict=True):
        row = conn.snapshots[source["sha256"]]
        assert row["content"] == request_arguments["content_by_season"][pin.season]
        assert row["source"] == source["key"]
        assert row["fetched_at"] == pin.retrieved_at
        assert source["provenance"] == {"clock_basis": "snapshot_generated_at",
                                         "captured_at": runner._iso(pin.retrieved_at)}

    material = json.loads(conn.snapshots[first.materials_ref.sha256]["content"])
    assert material["identity"] == _IDENTITY
    assert set(material["originals"]) == {"approval", "source_inventory", "2024-25", "2025-26"}
    for role, name, at in (("approval", "approval_bytes", registration._APPROVAL_AT),
                           ("source_inventory", "source_inventory_bytes", nba_odr.NBA_ODR_INDEPENDENT_ESPN.inventory_observed_at)):
        evidence = conn.snapshots[material["originals"][role]["sha256"]]
        assert evidence["content"] == request_arguments[name]
        assert evidence["fetched_at"] == at
    assert "manifest" not in material and "run_event_id" not in material
    assert material["physical_plan"]["sha256"] == first.plan.plan_ref.sha256 == event["output_evidence"][0]["plan"]["sha256"]
    assert material["metadata"]["sha256"] == first.plan.metadata_ref.sha256
    assert conn.snapshots[first.plan.plan_ref.sha256]["content"] == first.plan.plan_bytes
    assert conn.snapshots[first.plan.metadata_ref.sha256]["content"] == first.plan.metadata_bytes
    comparison = conn.snapshots[material["comparison"]["sha256"]]
    assert comparison["fetched_at"] == _OBSERVED
    assert first.policy_context.watermark == material["comparison"]["sha256"]
    assert json.loads(comparison["content"])["observed_at"] == runner._iso(_OBSERVED)
    assert event["output_evidence"][0]["run_key"] == first.plan.run_key

    # A separate consistent read or same-input retry decodes the first context.
    another = _Connection()
    another.snapshots, another.events = deepcopy(conn.snapshots), deepcopy(conn.events)
    assert _read(another, expected_manifest=first.manifest_ref) == first
    assert another.writes == []
    assert _write(conn, request_arguments, read_comparisons=_forbidden,
                  record_command_run_event=_forbidden, wall_clock=_forbidden) == first
    assert conn.writes == writes


def test_manifest_alone_cannot_substitute_for_fixed_event(request_arguments):
    conn = _Connection()
    first = _write(conn, request_arguments)
    isolated = _Connection()
    isolated.snapshots[first.manifest_ref.sha256] = conn.snapshots[first.manifest_ref.sha256]
    assert _read(isolated, expected_manifest=first.manifest_ref, decode_comparisons=_forbidden) is None
    assert isolated.writes == []


@pytest.mark.parametrize("overrides", [{"instance_sha256": "c" * 64}, {"migration_version": "20261107000001"},
                                        {"code_commit": "d" * 40}, {"expected_manifest": _ref(b"another manifest")}])
def test_request_must_match_the_first_fixed_binding(request_arguments, overrides):
    conn = _Connection()
    _write(conn, request_arguments)
    writes = conn.writes.copy()
    with pytest.raises(store.PapersimStoreError, match=registration._CONFLICT):
        _read(conn, **overrides, decode_comparisons=_forbidden)
    assert conn.writes == writes


@pytest.mark.parametrize("field,value", [
    ("kind", "unexpected_kind"), ("schema_version", 2), ("source", "different_source"),
    ("fetched_at", _OBSERVED), ("byte_length", 1), ("content", b"other bytes"),
])
def test_incompatible_reused_snapshot_rolls_back_all_new_evidence(request_arguments, field, value):
    conn = _Connection()
    source = registration._pinned_originals()["2024-25"]
    raw = request_arguments["content_by_season"]["2024-25"]
    with conn.transaction():
        snapshots.register_snapshot(conn, source.registration(raw))
    conn.snapshots[source.ref.sha256][field] = value
    first_rows = deepcopy(conn.snapshots)
    with conn.transaction(), pytest.raises(store.PapersimStoreError, match="papersim_(nba_odr_registration_)?snapshot_"):
        # Catch the conflict within the outer transaction so the inner savepoint
        # proves none of the preceding snapshot writes can survive it.
        timestamps = iter((_OBSERVED, _WRITTEN))
        registration._write_nba_odr_registration(
            conn, **_IDENTITY, **request_arguments, decode_comparisons=_decode,
            record_command_run_event=_forbidden, wall_clock=lambda: next(timestamps),
        )
    assert conn.snapshots == first_rows
    assert conn.events == {}
    assert len(conn.writes) > 1


@pytest.mark.parametrize("field", ["approval_bytes", "source_inventory_bytes", "content_by_season"])
def test_substituted_originals_are_rejected_before_writing(request_arguments, field):
    conn = _Connection()
    substitute = b"other original"
    if field == "content_by_season":
        substitute = {**request_arguments[field], "2024-25": substitute}
    with pytest.raises(store.PapersimStoreError, match=registration._CONFLICT):
        _write(conn, request_arguments, **{field: substitute}, read_comparisons=_forbidden)
    assert conn.writes == []


def test_inventory_keeps_its_own_observation_clock(monkeypatch, request_arguments):
    conn = _Connection()
    substitute = _encode({"utc": registration._APPROVAL_AT.isoformat()})
    monkeypatch.setattr(nba_odr, "NBA_ODR_INDEPENDENT_ESPN", replace(
        nba_odr.NBA_ODR_INDEPENDENT_ESPN, inventory_sha256=_ref(substitute).sha256,
        inventory_byte_length=len(substitute),
    ))
    with pytest.raises(store.PapersimStoreError, match=registration._EVIDENCE_INVALID):
        _write(conn, request_arguments, source_inventory_bytes=substitute, read_comparisons=_forbidden)
    assert conn.writes == []


def test_readback_checks_event_and_derived_snapshot_identity(request_arguments):
    conn = _Connection()
    first = _write(conn, request_arguments)
    conn.events[first.run_event_key]["command"] = "optimize-run"
    with pytest.raises(store.PapersimStoreError, match=registration._CONFLICT):
        _read(conn, decode_comparisons=_forbidden)
    event = conn.events[first.run_event_key]
    event["command"] = "optimize-register-experiment"
    event["output_evidence"][0]["budget"]["max_trials"] += 1
    with pytest.raises(store.PapersimStoreError, match=registration._CONFLICT):
        _read(conn)
    conn.events[first.run_event_key]["output_evidence"][0]["budget"]["max_trials"] -= 1
    conn.snapshots[first.plan.metadata_ref.sha256]["fetched_at"] = _WRITTEN
    with pytest.raises(store.PapersimStoreError, match=registration._SNAPSHOT_CONFLICT):
        _read(conn)


def test_receipt_is_read_back_when_append_helper_returns_no_id(request_arguments):
    conn = _Connection()

    def repeated(connection, submission):
        store_psycopg.record_command_run_event(connection, submission)
        result = store_psycopg.record_command_run_event(connection, submission)
        assert result["inserted"] is False and result["run_event_id"] is None
        return result

    first = _write(conn, request_arguments, record_command_run_event=repeated)
    assert first.run_event_id == 53
    assert first.occurred_at == _WRITTEN
    assert [write for write in conn.writes if write[0] == "event"] == [("event", first.run_event_key)]


def test_append_failure_rolls_back_evidence_and_receipt_inside_outer_transaction(request_arguments):
    conn = _Connection()

    def failing_append(connection, submission):
        store_psycopg.record_command_run_event(connection, submission)
        raise store.PapersimStoreError("synthetic_append_failure")

    timestamps = iter((_OBSERVED, _WRITTEN))
    with conn.transaction(), pytest.raises(store.PapersimStoreError, match="synthetic_append_failure"):
        registration._write_nba_odr_registration(
            conn, **_IDENTITY, **request_arguments, decode_comparisons=_decode,
            record_command_run_event=failing_append, wall_clock=lambda: next(timestamps),
        )
    assert conn.writes[-1] == ("event", registration.NBA_ODR_REGISTRATION_EVENT_KEY)
    assert conn.snapshots == conn.events == {}


def test_writer_requires_the_callers_transaction(request_arguments):
    conn = _Connection()
    with pytest.raises(store.PapersimStoreError, match="papersim_nba_odr_registration_transaction_required"):
        registration._write_nba_odr_registration(
            conn, **_IDENTITY, **request_arguments, decode_comparisons=_forbidden,
            record_command_run_event=_forbidden, wall_clock=_forbidden,
        )
    assert conn.writes == []
