"""Immutable registration evidence for the one approved FX-41 NBA schedule.

The managed store supplies the verified instance identity and owns the outer
transaction. Writers and commit confirmation take the registry lock before
the schedule lock. Readers of an optimization request use its consistent read
transaction. The evidence returned here can be consumed by a managed claim.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from re import fullmatch
from types import MappingProxyType

from polymarket_alpha_lab.papersim import snapshots, store
from polymarket_alpha_lab.papersim.optimize import nba_odr, runner
from polymarket_alpha_lab.papersim.optimize.registry import lineage_id

NBA_ODR_REGISTRATION_LOCK_NAMESPACE = "papersim.optimization.registration.v1"
NBA_ODR_REGISTRATION_OPERATION_KEY = f"optimize.register-experiment/{runner.NBA_ODR_SCHEDULE_ID}"
NBA_ODR_REGISTRATION_EVENT_KEY = store.command_run_event_key(
    NBA_ODR_REGISTRATION_OPERATION_KEY, 1, "SUCCEEDED",
)
NBA_ODR_REGISTRATION_LINEAGES = tuple(
    lineage_id(runner._NBA_ODR_FAMILY_ID, target) for target in nba_odr.NBA_ODR_LINES
)

_COMMAND = "optimize-register-experiment"
_INPUT_SCHEMA = "papersim.optimize/nba-odr-registration-input/v1"
_OUTPUT_SCHEMA = "papersim.optimize/nba-odr-registration/v1"
_MANIFEST_KIND = "optimizer_experiment_manifest"
_COMPARE_KIND = "optimizer_experiment_registration_comparison"
_METADATA_KIND = "optimizer_experiment_plan_metadata"
_PLAN_KIND = "optimizer_experiment_physical_plan"
_MATERIALS_KIND = runner.NBA_ODR_REGISTRATION_MATERIALS_KIND
_SOURCE_KIND = "registered_research_input"
_MATERIALS_NAME = "materials.json"
_COMPARISON_NAME = "comparison.json"
_MANIFEST_NAME = "manifest.json"

_APPROVAL_REF = runner.SourceArtifactRef(
    "bc33ccee3864a6b33116105f8fc373f0acc614eb34b1148d04ccf69e2dfd4313", 1_517,
)
_APPROVAL_AT = datetime(2026, 11, 21, 17, 13, 29, 760019, tzinfo=UTC)

_EVIDENCE_INVALID = "papersim_nba_odr_registration_evidence_invalid"
_SNAPSHOT_CONFLICT = "papersim_nba_odr_registration_snapshot_conflict"
_CONFLICT = "papersim_nba_odr_registration_conflict"

_EVENT_COLUMNS = (
    "run_event_id", "run_event_key", "operation_key", "attempt", "phase", "command",
    "queue_id", "scheduled_for", "occurred_at", "code_commit", "config_digest",
    "input_evidence", "output_evidence", "error_code", "scan_id",
)
_EVENT_SELECT = (
    "SELECT " + ", ".join(_EVENT_COLUMNS) + " FROM papersim.command_run_events WHERE run_event_key = %s"
)

_DecodeComparisons = Callable[[dict], Mapping[str, runner.RegistryComparison]]


@dataclass(frozen=True, slots=True)
class NBAODRRegistrationFacts:
    """Evidence observed in the caller's transaction, with a transient pointer."""

    run_event_id: int
    run_event_key: str
    occurred_at: datetime
    config_digest: str
    manifest_ref: runner.SourceArtifactRef
    manifest_bytes: bytes
    materials_ref: runner.SourceArtifactRef
    plan: runner.NBAODRPlan
    policy_context: runner.PolicyContext
    content_by_season: Mapping[str, bytes]

    @property
    def pointer(self) -> dict[str, int | str]:
        return {"schema_version": 1, "manifest_sha256": self.manifest_ref.sha256,
                "manifest_byte_length": self.manifest_ref.byte_length}


@dataclass(frozen=True, slots=True)
class _PinnedOriginal:
    ref: runner.SourceArtifactRef
    kind: str
    source: str
    fetched_at: datetime
    clock_field: str | None = None

    def registration(self, content: bytes) -> snapshots.SnapshotRegistration:
        if (type(content) is not bytes or len(content) != self.ref.byte_length
                or snapshots.snapshot_content_sha256(content) != self.ref.sha256):
            raise store.PapersimStoreError(_CONFLICT)
        if self.clock_field is not None:
            try:
                clock = _utc(datetime.fromisoformat(json.loads(content)[self.clock_field]))
            except (TypeError, ValueError, KeyError):
                raise store.PapersimStoreError(_EVIDENCE_INVALID) from None
            if clock != self.fetched_at:
                raise store.PapersimStoreError(_EVIDENCE_INVALID)
        return snapshots.SnapshotRegistration(content, self.kind, self.source, self.fetched_at, schema_version=1)


@dataclass(frozen=True, slots=True)
class _Artifacts:
    comparison: snapshots.SnapshotRegistration
    metadata: snapshots.SnapshotRegistration
    physical_plan: snapshots.SnapshotRegistration
    materials: snapshots.SnapshotRegistration
    manifest: snapshots.SnapshotRegistration
    plan: runner.NBAODRPlan
    policy_context: runner.PolicyContext
    input_evidence: str
    output_evidence: str
    config_digest: str


def _generated_source(name: str) -> str:
    return f"{NBA_ODR_REGISTRATION_OPERATION_KEY}/{name}"


def _pinned_originals() -> dict[str, _PinnedOriginal]:
    inventory = nba_odr.NBA_ODR_INDEPENDENT_ESPN
    return {
        "approval": _PinnedOriginal(_APPROVAL_REF, "optimizer_experiment_owner_approval",
                                    _generated_source("fx41-nba-single-run-owner-approval.json"),
                                    _APPROVAL_AT, "decision_observed_utc"),
        "source_inventory": _PinnedOriginal(
            runner.SourceArtifactRef(inventory.inventory_sha256, inventory.inventory_byte_length),
            "optimizer_experiment_source_inventory", _generated_source("nba-source-inventory.json"),
            inventory.inventory_observed_at, "utc",
        ),
        **{season: _PinnedOriginal(runner.SourceArtifactRef(pin.gzip_sha256, pin.gzip_byte_length),
                                   _SOURCE_KIND, pin.relpath, pin.retrieved_at)
           for season, pin in nba_odr.NBA_ODR_ARCHIVE_PINS.items()},
    }


def _scoring_sources(pins: Mapping[str, _PinnedOriginal]) -> list[dict]:
    entries = []
    missing = (
        ("team_schedule", "matching_original_espn_team_schedule_bundle_unavailable"),
        ("event_summary", "matching_original_espn_event_summary_bundle_unavailable"),
    )
    for season in nba_odr.NBA_ODR_ARCHIVE_PINS:
        pin = pins[season]
        entries.append({"role": "nba_season_archive", "key": pin.source, "kind": pin.kind,
                        "schema_version": 1, **asdict(pin.ref), "required": True,
                        "provenance": {"clock_basis": "snapshot_generated_at",
                                       "captured_at": runner._iso(pin.fetched_at)}})
        entries.extend({"role": role, "key": f"{role}/{season}/originals", "kind": _SOURCE_KIND,
                        "schema_version": 1, "sha256": None, "byte_length": None,
                        "required": False, "absent_reason": reason} for role, reason in missing)
    return entries


def _identity(code_commit: str, instance_sha256: str, migration_version: str) -> dict:
    if (type(code_commit) is not str or not fullmatch(r"[0-9a-f]{40}", code_commit)
            or type(instance_sha256) is not str or not fullmatch(r"[0-9a-f]{64}", instance_sha256)
            or type(migration_version) is not str or not fullmatch(r"[0-9]{14}", migration_version)):
        raise store.PapersimStoreError("papersim_nba_odr_registration_identity_invalid")
    return {"code_commit": code_commit, "instance_sha256": instance_sha256,
            "migration_version": migration_version}


def _utc(clock: datetime) -> datetime:
    if not isinstance(clock, datetime) or clock.utcoffset() is None:
        raise store.PapersimStoreError("papersim_nba_odr_registration_clock_invalid")
    return clock.astimezone(UTC)


def _artifact_ref(registration: snapshots.SnapshotRegistration) -> runner.SourceArtifactRef:
    return runner.SourceArtifactRef(registration.content_sha256, registration.byte_length)


def _assemble(*, identity, pins, content_by_season, comparisons, observed_at,
              decode_comparisons: _DecodeComparisons) -> _Artifacts:
    if (type(comparisons) is not dict or comparisons.keys() != set(NBA_ODR_REGISTRATION_LINEAGES)
            or observed_at < max(pin.fetched_at for pin in pins.values())):
        raise store.PapersimStoreError(_EVIDENCE_INVALID)

    def generated(name, kind, raw):
        return snapshots.SnapshotRegistration(raw, kind, _generated_source(name), observed_at, schema_version=1)

    def reference(ref, kind):
        return {**asdict(ref), "kind": kind, "schema_version": 1}

    # The caller shares the store's role/spec decoder with run claims. This
    # context contains the observed comparison rows used to plan shared fits.
    comparison = generated(_COMPARISON_NAME, _COMPARE_KIND, runner._canonical_json({
        "schema_version": 1, "observed_at": runner._iso(observed_at), "lineages": comparisons,
    }))
    comparison_ref = _artifact_ref(comparison)
    policy = runner.PolicyContext(observed_at, decode_comparisons(comparisons), watermark=comparison_ref.sha256)
    plan = runner.build_nba_odr_plan(
        content_by_season=content_by_season, code_commit=identity["code_commit"], policy_context=policy,
        model_contexts=dict.fromkeys(nba_odr.NBA_ODR_LINES, "shared"),
    )
    metadata_ref, plan_ref = plan.metadata_ref, plan.plan_ref
    metadata = generated("plan-metadata.json", _METADATA_KIND, plan.metadata_bytes)
    physical_plan = generated("physical-plan.json", _PLAN_KIND, plan.plan_bytes)

    originals = {key: reference(pin.ref, pin.kind) for key, pin in pins.items()}
    materials = generated(_MATERIALS_NAME, _MATERIALS_KIND, runner._canonical_json({
        "schema_version": 1, "schedule_id": runner.NBA_ODR_SCHEDULE_ID, "identity": identity,
        "originals": originals, "comparison": reference(comparison_ref, _COMPARE_KIND),
        "metadata": reference(metadata_ref, _METADATA_KIND), "physical_plan": reference(plan_ref, _PLAN_KIND),
    }))
    materials_ref = _artifact_ref(materials)
    protocol = runner.nba_odr_historical_protocol(materials_ref)
    manifest = generated(_MANIFEST_NAME, _MANIFEST_KIND, runner._canonical_json({
        "schema_version": 1, "protocol": protocol, "sources": _scoring_sources(pins),
    }))
    input_evidence = [{"schema": _INPUT_SCHEMA, "schedule_id": runner.NBA_ODR_SCHEDULE_ID,
                       "identity": identity, "originals": originals}]
    output_evidence = [{"schema": _OUTPUT_SCHEMA, "run_key": plan.run_key, "budget": protocol["budget"],
                        "manifest": asdict(_artifact_ref(manifest)), "materials": asdict(materials_ref),
                        "plan": asdict(plan_ref)}]
    config_digest = sha256(runner._canonical_json({"input_evidence": input_evidence,
                                                  "output_evidence": output_evidence})).hexdigest()
    return _Artifacts(comparison, metadata, physical_plan, materials, manifest, plan, policy,
                      runner._canonical_json(input_evidence).decode(),
                      runner._canonical_json(output_evidence).decode(), config_digest)


def _require_transaction(conn) -> None:
    if conn.info.transaction_status.name != "INTRANS":
        raise store.PapersimStoreError("papersim_nba_odr_registration_transaction_required")


def _event(conn) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(_EVENT_SELECT, (NBA_ODR_REGISTRATION_EVENT_KEY,))
        row = cursor.fetchone()
    return None if row is None else dict(zip(_EVENT_COLUMNS, row, strict=True))


def _event_time(event, identity) -> datetime:
    fixed = {"run_event_key": NBA_ODR_REGISTRATION_EVENT_KEY, "operation_key": NBA_ODR_REGISTRATION_OPERATION_KEY,
             "phase": "SUCCEEDED", "command": _COMMAND, "attempt": 1, "queue_id": None,
             "scheduled_for": None, "code_commit": identity["code_commit"], "scan_id": None, "error_code": None}
    if (event is None or type(event["run_event_id"]) is not int or event["run_event_id"] < 1
            or type(event["attempt"]) is not int or any(event[key] != value for key, value in fixed.items())):
        raise store.PapersimStoreError(_CONFLICT)
    return _utc(event["occurred_at"])


def _read_snapshot(conn, ref, kind, source, fetched_at=None) -> dict:
    row = snapshots.read_snapshot(conn, ref.sha256)
    if (row is None or row["content_sha256"] != ref.sha256 or row["kind"] != kind
            or type(row["schema_version"]) is not int or row["schema_version"] != 1
            or row["source"] != source or type(row["byte_length"]) is not int
            or row["byte_length"] != ref.byte_length or len(row["content"]) != ref.byte_length
            or snapshots.snapshot_content_sha256(row["content"]) != ref.sha256
            or (fetched_at is not None and row["fetched_at"] != fetched_at)):
        raise store.PapersimStoreError(_SNAPSHOT_CONFLICT)
    return row


def _verify_snapshot(conn, expected: snapshots.SnapshotRegistration) -> None:
    row = _read_snapshot(conn, _artifact_ref(expected), expected.kind, expected.source, expected.fetched_at)
    if row["content"] != expected.content:
        raise store.PapersimStoreError(_SNAPSHOT_CONFLICT)


def _canonical_object(content: bytes) -> dict:
    value = json.loads(content)
    if type(value) is not dict or runner._canonical_json(value) != content:
        raise store.PapersimStoreError(_EVIDENCE_INVALID)
    return value


def _facts(event, occurred_at, artifacts, content_by_season) -> NBAODRRegistrationFacts:
    if (occurred_at < artifacts.policy_context.started_at or event["config_digest"] != artifacts.config_digest
            or runner._canonical_json(event["input_evidence"]).decode() != artifacts.input_evidence
            or runner._canonical_json(event["output_evidence"]).decode() != artifacts.output_evidence):
        raise store.PapersimStoreError(_CONFLICT)
    return NBAODRRegistrationFacts(
        event["run_event_id"], NBA_ODR_REGISTRATION_EVENT_KEY, occurred_at, artifacts.config_digest,
        _artifact_ref(artifacts.manifest), artifacts.manifest.content, _artifact_ref(artifacts.materials),
        artifacts.plan, artifacts.policy_context, MappingProxyType(dict(content_by_season)),
    )


def _read_registration(conn, event, identity, pins, decode_comparisons, *, expected_manifest=None,
                       submitted_originals=None) -> NBAODRRegistrationFacts:
    occurred_at = _event_time(event, identity)
    try:
        inputs, = event["input_evidence"]
        outputs, = event["output_evidence"]
        if inputs["schema"] != _INPUT_SCHEMA or outputs["schema"] != _OUTPUT_SCHEMA:
            raise store.PapersimStoreError(_EVIDENCE_INVALID)
        if inputs["identity"] != identity:
            raise store.PapersimStoreError(_CONFLICT)
        manifest_ref = runner.SourceArtifactRef(**outputs["manifest"])
        if expected_manifest is not None and expected_manifest != manifest_ref:
            raise store.PapersimStoreError(_CONFLICT)
        materials_ref = runner.SourceArtifactRef(**outputs["materials"])
        materials = _read_snapshot(conn, materials_ref, _MATERIALS_KIND, _generated_source(_MATERIALS_NAME))
        material = _canonical_object(materials["content"])
        comparison = material["comparison"]
        comparison_ref = runner.SourceArtifactRef(comparison["sha256"], comparison["byte_length"])
        comparison_row = _read_snapshot(conn, comparison_ref, _COMPARE_KIND,
                                        _generated_source(_COMPARISON_NAME))
        comparison_document = _canonical_object(comparison_row["content"])
        observed_at = _utc(datetime.fromisoformat(comparison_document["observed_at"]))
        if materials["fetched_at"] != observed_at or comparison_row["fetched_at"] != observed_at:
            raise store.PapersimStoreError(_SNAPSHOT_CONFLICT)
    except store.PapersimStoreError:
        raise
    except (KeyError, TypeError, ValueError):
        raise store.PapersimStoreError(_EVIDENCE_INVALID) from None

    originals = {key: _read_snapshot(conn, pin.ref, pin.kind, pin.source, pin.fetched_at)["content"]
                 for key, pin in pins.items()}
    if submitted_originals is not None and originals != submitted_originals:
        raise store.PapersimStoreError(_CONFLICT)
    for key, content in originals.items():
        pins[key].registration(content)
    archives = {season: originals[season] for season in nba_odr.NBA_ODR_ARCHIVE_PINS}
    try:
        artifacts = _assemble(identity=identity, pins=pins, content_by_season=archives,
                              comparisons=comparison_document["lineages"], observed_at=observed_at,
                              decode_comparisons=decode_comparisons)
        facts = _facts(event, occurred_at, artifacts, archives)
    except store.PapersimStoreError:
        raise
    except (KeyError, TypeError, ValueError):
        raise store.PapersimStoreError(_EVIDENCE_INVALID) from None
    if (artifacts.comparison.content != comparison_row["content"]
            or artifacts.materials.content != materials["content"]):
        raise store.PapersimStoreError(_SNAPSHOT_CONFLICT)
    for expected in (artifacts.metadata, artifacts.physical_plan, artifacts.manifest):
        _verify_snapshot(conn, expected)
    return facts


def _read_nba_odr_registration(
    conn, *, code_commit: str, instance_sha256: str, migration_version: str,
    decode_comparisons: _DecodeComparisons, expected_manifest: runner.SourceArtifactRef | None = None,
) -> NBAODRRegistrationFacts | None:
    """Read and reconstruct the fixed receipt in the caller's consistent view."""
    _require_transaction(conn)
    identity = _identity(code_commit, instance_sha256, migration_version)
    if expected_manifest is not None and type(expected_manifest) is not runner.SourceArtifactRef:
        raise store.PapersimStoreError(_EVIDENCE_INVALID)
    event = _event(conn)
    if event is None:
        return None
    return _read_registration(conn, event, identity, _pinned_originals(), decode_comparisons,
                              expected_manifest=expected_manifest)


def _write_nba_odr_registration(
    conn, *, code_commit: str, instance_sha256: str, migration_version: str,
    approval_bytes: bytes, source_inventory_bytes: bytes, content_by_season: Mapping[str, bytes],
    read_comparisons: Callable[[], dict], decode_comparisons: _DecodeComparisons,
    record_command_run_event: Callable[[object, store.CommandRunEventSubmission], dict],
    wall_clock: Callable[[], datetime],
) -> NBAODRRegistrationFacts:
    """Append the fixed evidence under both caller-owned transaction locks.

    ``read_comparisons`` observes both lineage comparisons under those locks.
    The injected event writer keeps the managed store dependency one-way.
    The caller confirms the outer commit before using the returned pointer.
    """
    _require_transaction(conn)
    identity = _identity(code_commit, instance_sha256, migration_version)
    event = _event(conn)
    if not isinstance(content_by_season, Mapping) or content_by_season.keys() != nba_odr.NBA_ODR_ARCHIVE_PINS.keys():
        raise store.PapersimStoreError(_CONFLICT)
    archives = dict(content_by_season)
    supplied = {"approval": approval_bytes, "source_inventory": source_inventory_bytes, **archives}
    pins = _pinned_originals()
    originals = tuple(pin.registration(supplied[key]) for key, pin in pins.items())
    if event is not None:
        return _read_registration(conn, event, identity, pins, decode_comparisons, submitted_originals=supplied)

    comparisons = read_comparisons()
    artifacts = _assemble(identity=identity, pins=pins, content_by_season=archives, comparisons=comparisons,
                          observed_at=_utc(wall_clock()), decode_comparisons=decode_comparisons)
    # This savepoint keeps the snapshot/event set atomic even if the caller
    # handles a provenance conflict inside its outer transaction.
    with conn.transaction():
        for registration in (*originals, artifacts.comparison, artifacts.metadata, artifacts.physical_plan,
                             artifacts.materials, artifacts.manifest):
            snapshots.register_snapshot(conn, registration)
            _verify_snapshot(conn, registration)
        occurred_at = _utc(wall_clock())
        if occurred_at < artifacts.policy_context.started_at:
            raise store.PapersimStoreError("papersim_nba_odr_registration_clock_invalid")
        submission = store.CommandRunEventSubmission(
            operation_key=NBA_ODR_REGISTRATION_OPERATION_KEY, attempt=1, phase="SUCCEEDED", command=_COMMAND,
            code_commit=code_commit, occurred_at=occurred_at, config_digest=artifacts.config_digest,
            input_evidence=artifacts.input_evidence, output_evidence=artifacts.output_evidence,
        )
        record_command_run_event(conn, submission)
        # A duplicate append can return no ID. This row is authoritative.
        original = _event(conn)
        return _facts(original, _event_time(original, identity), artifacts, archives)
