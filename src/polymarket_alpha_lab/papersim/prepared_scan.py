"""Managed B1 first binding, atomic entry write, and restart evidence."""

from dataclasses import asdict, replace
from datetime import datetime
import json
from pathlib import Path
import re

from polymarket_alpha_lab import replay
from polymarket_alpha_lab.papersim import prepare, prepare_inputs as inputs, queue_policy, risk_policy
from polymarket_alpha_lab.papersim import scan, snapshots, store, store_psycopg as pg
from polymarket_alpha_lab.papersim.d1compare import operations
from polymarket_alpha_lab.papersim.optimize.provider import LineageSpecs
from polymarket_alpha_lab.papersim.optimize.shadow import SnapshotShadowSource
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec


_BINDING_KIND = "papersim_b1_prepared"
_BINDING_SOURCE = "papersim:run-scan:prepared:v1"
_COVERAGE_KIND = "papersim_prepare_coverage"
_COVERAGE_SOURCE = "papersim:run-scan:coverage:v1"
_RECOVERY_KIND = "papersim_b1_recovery"
_RECOVERY_SOURCE = "papersim:run-scan:recovery:v1"
_CONFLICT = "papersim_prepare_operation_conflict"


def _check(condition, code=_CONFLICT):
    if not condition:
        raise scan.ScanError(code)


def _spec_document(spec):
    if spec is None:
        return None
    return {
        "family": spec.family, "procedure_version": spec.procedure_version,
        "params": spec.params, "envelope": spec.envelope, "data_contract": spec.data_contract,
        "legacy_model_version": spec.legacy_model_version,
        "spec_sha256": spec.spec_sha256(), "model_version": spec.model_version(),
    }


def _specs_document(roles):
    return {lineage: {
        "active": _spec_document(specs.active), "baseline": _spec_document(specs.baseline),
        "candidates": [_spec_document(spec) for spec in specs.candidates],
        "guard": _spec_document(specs.guard), "fallback_reason": specs.fallback_reason, "registry": specs.registry,
    } for lineage, specs in roles.items()}


def _load_spec(row):
    if row is None:
        return None
    parameters = {key: value for key, value in row.items() if key not in ("spec_sha256", "model_version")}
    spec = ModelSpec(**pg._spec_sequences_to_tuples(parameters))
    _check(spec.spec_sha256() == row["spec_sha256"] and spec.model_version() == row["model_version"])
    return spec


def _load_specs(document):
    return {lineage: LineageSpecs(
        active=_load_spec(roles["active"]), baseline=_load_spec(roles["baseline"]),
        candidates=tuple(_load_spec(spec) for spec in roles["candidates"]),
        guard=_load_spec(roles["guard"]), fallback_reason=roles["fallback_reason"],
        registry=pg._spec_sequences_to_tuples(roles["registry"]),
    ) for lineage, roles in document.items()}


def _binding_refs(input_set, config_sha):
    return [f"prepare_input_set:{input_set}", f"scan_config:{config_sha}"]


def _prepared_reference(digest, byte_length):
    return {"sha256": digest, "byte_length": byte_length}


def _pack_reference(pack):
    return _prepared_reference(pack.content_sha256, pack.byte_length)


def _candidate_witness(resolved, pack, excluded):
    return {
        "version": 1, "input_set_sha256": resolved.input_set_sha256,
        "batch_sha256": resolved.batch_sha256, "config_sha256": resolved.config_sha256,
        "pack": _pack_reference(pack), "decision_as_of": pack.decision_as_of.isoformat(),
        "queue": excluded.queue, "candidate_key": excluded.candidate_key,
        "reason_code": excluded.reason_code, "sources": list(excluded.source_refs),
    }


def _records(resolved, pack):
    rows = json.loads(pack.content)
    for row in rows:
        row["prepared_provenance"].update({
            "input_set_sha256": resolved.input_set_sha256, "pack": _pack_reference(pack),
            "config_sha256": resolved.config_sha256, "batch_sha256": resolved.batch_sha256,
        })
    for excluded in pack.excluded:
        identity = resolved.failure_records.get(excluded.candidate_key)
        if identity is None:
            continue
        active = resolved.specs[identity["lineage"]].active
        rows.append({
            **identity,
            "snapshot_refs": list(dict.fromkeys(
                ref["content_sha256"] for ref in excluded.source_refs if ref.get("content_sha256"))),
            "construction": {"constructor": replay.CONSTRUCTOR_MANAGED_PREPARATION,
                             "witness": _candidate_witness(resolved, pack, excluded)},
            "prepared_provenance": {"model_spec_sha256": active.spec_sha256(),
                                    "model_version": active.model_version()},
        })
    return scan.records_from_pack(sorted(rows, key=lambda row: (row["queue"], row["candidate_key"])),
                                  managed_preparation=True)


def _coverage(resolved, pack):
    return {
        "version": 1, "input_set_sha256": resolved.input_set_sha256,
        "batch_sha256": resolved.batch_sha256, "pack": _pack_reference(pack),
        "unbound": [*resolved.unbound_observations, *(
            {"queue": item.queue, "reason_code": item.reason_code, "sources": item.source_refs}
            for item in pack.excluded if item.candidate_key not in resolved.failure_records)],
    }


def _verify_base(conn, repo_root, config_bytes, input_set_sha, instance_id, *, pinned_specs=None):
    with pg.d1_read_view(conn):
        pg.d1_resolution_identity(conn, instance_id=instance_id)
        return inputs._resolve(conn, repo_root, config_bytes, inputs._scan_configuration(config_bytes),
                               input_set_sha, instance_id, pinned_specs=pinned_specs)


def _assemble(config, records, conn, *, started_at, finished_at, code_commit, key, specs,
              paused_queues, portfolio, material, shadow_budget_seconds):
    return scan.assemble_scan(
        config, records, specs=specs, portfolio=portfolio,
        admissions=scan.AdmissionReport(paused_queues=frozenset(paused_queues)),
        started_at=started_at, finished_at=finished_at, code_commit=code_commit,
        operation_key=key, preparation=material,
        shadow_source=SnapshotShadowSource(lambda digest: snapshots.read_snapshot(conn, digest)),
        shadow_budget_seconds=shadow_budget_seconds,
    )


def _receipt(conn, key, attempt, code_commit, config_sha, references, phase, at, *,
             outputs=(), scan_id=None, error_code=None):
    pg.record_command_run_event(conn, store.CommandRunEventSubmission(
        operation_key=key, attempt=attempt, phase=phase, command=scan.SCAN_COMMAND,
        occurred_at=at, code_commit=code_commit, config_digest=config_sha, input_evidence=list(references),
        output_evidence=list(outputs), scan_id=scan_id, error_code=error_code,
    ))
    return pg.read_command_run_event(conn, key, attempt, phase)


def _inspect(receipts, key, code_commit, config_sha, references):
    for row in receipts:
        _check(row["operation_key"] == key and row["code_commit"] == code_commit
               and row["config_digest"] == config_sha and row["input_evidence"] == references
               and row["command"] == scan.SCAN_COMMAND and row["queue_id"] is None
               and row["run_event_key"] == store.command_run_event_key(key, row["attempt"], row["phase"])
               and all(row[flag] for flag in ("paper_only", "report_only", "readonly")))
    phases = {phase: [row for row in receipts if row["phase"] == phase]
              for phase in ("STARTED", "PREPARED", "SUCCEEDED")}
    _check(len(phases["PREPARED"]) <= 1)
    return tuple(rows[0] if rows else None for rows in phases.values())


def _read_bound(conn, prepared):
    binding = operations._read_document(
        conn, prepared["output_evidence"], kind=_BINDING_KIND, source=_BINDING_SOURCE)
    _check(binding["operation_key"] == prepared["operation_key"]
           and binding["prepared_attempt"] == prepared["attempt"]
           and binding["prepared_at"] == prepared["occurred_at"].isoformat()
           and binding["config_sha256"] == prepared["config_digest"]
           and _binding_refs(binding["input_set_sha256"], binding["config_sha256"]) == prepared["input_evidence"])
    source = inputs._read_reference(conn, binding["config"], kind=inputs.CONFIG_KIND, source=inputs.CONFIG_SOURCE)
    sealed = inputs._read_reference(
        conn, binding["input_set"], kind=inputs.INPUT_SET_KIND, source=inputs.INPUT_SET_SOURCE)
    _check(source["content_sha256"] == binding["config_sha256"]
           and sealed["content_sha256"] == binding["input_set_sha256"])
    coverage = operations._read_document(
        conn, binding["coverage"], kind=_COVERAGE_KIND, source=_COVERAGE_SOURCE)
    _check(coverage["input_set_sha256"] == binding["input_set_sha256"]
           and coverage["batch_sha256"] == binding["batch_sha256"] and coverage["pack"] == binding["pack"])
    return binding, source["content"]


def _expected(binding):
    expected = dict(binding["business"])
    for name in ("started_at", "finished_at"):
        expected[name] = datetime.fromisoformat(expected[name])
    return expected


def _validate_first_start(first, prepared, binding):
    expected = _expected(binding)
    _check(first is not None and first["run_event_key"] == binding["first_start"]
           and first["scan_id"] is None and first["error_code"] is None and first["output_evidence"] == []
           and prepared["scan_id"] is None and prepared["error_code"] is None
           and first["code_commit"] == expected["code_commit"]
           and expected["started_at"] == first["occurred_at"]
           <= expected["finished_at"] <= prepared["occurred_at"])


def _result(written, binding, *, recovered, succeeded):
    return {
        **written, "recovered": recovered, "code_commit": binding["business"]["code_commit"],
        "input_set_sha256": binding["input_set_sha256"], "pack": binding["pack"],
        "operation_receipt": operations.canonical_document(operations._receipt_ref(succeeded)),
        "coverage": binding["coverage"], "paused_queues": binding["paused_queues"],
        "suppressed_entries": binding["suppressed_entries"], "model_versions": binding["model_versions"],
        "shadow_rows": binding["shadow_rows"],
    }


def _verify_eligibility(assembly, records, portfolio, admissions, paused_before):
    _check(admissions.paused_queues == frozenset(paused_before), "papersim_prepare_admission_changed")
    by_key = {record.candidate_key: record for record in records}
    for candidate in assembly.submission.candidates:
        entry = candidate.entry
        if entry is None:
            continue
        record = by_key[candidate.candidate_key]
        request = replay.request_from_replay_bundle(candidate.input_payload)
        event_scope = candidate.event_key or f"market:{candidate.market_id}"
        stake, fee = float(entry.stake), float(entry.entry_fee)
        available = risk_policy.bankroll_available(portfolio.bankroll, portfolio.cash, portfolio.reserved)
        _check(available + 1e-9 >= max(stake, round(stake + fee, 2)), "papersim_prepare_cash_changed")
        _check(risk_policy.team_add_allowed(portfolio.team_committed.get(record.team, 0.0),
                                           stake, cap=request.team_cap), "papersim_prepare_team_cap_changed")
        _check(risk_policy.held_fixture_add_allowed(portfolio.held_by_event.get(event_scope, ()),
                                                  request.side).allowed, "papersim_prepare_correlation_changed")
        portfolio = portfolio.with_entry(team=record.team, side=request.side, stake=stake, fee=fee,
                                         event_scope=event_scope)


def _write_pauses(conn, admissions, at):
    for decision in admissions.escalations:
        rule = store.QUEUE_CONTROL_RULE_FX17 if decision.review_reasons else store.QUEUE_CONTROL_RULE_BLUEPRINT
        pg.record_queue_control_event(conn, store.QueueControlSubmission(
            queue_id=decision.queue_id, control_kind="PAUSE", rule=rule,
            rule_version=queue_policy.QUEUE_POLICY_RULES_VERSION, observed_at=at,
            metrics={"action": decision.action, "review_reasons": list(decision.review_reasons),
                     "stop_reasons": list(decision.stop_reasons),
                     "keeps_latched_pause": decision.keeps_latched_pause},
        ))


def _recover(conn, binding, prepared, started, written, now, emit):
    old = pg.read_command_run_event(conn, binding["operation_key"], started["attempt"], "RECOVERY_READ")
    document = {
        "version": 1, "operation_key": binding["operation_key"], "attempt": started["attempt"],
        "started": started["run_event_key"], "first_start": binding["first_start"],
        "prepared": prepared["run_event_key"], "binding": prepared["output_evidence"],
        "scan_id": written["scan_id"], "scan_key": written["scan_key"],
        "scan_payload_sha256": binding["business"]["scan_payload_sha256"],
        "candidate_ids": written["candidate_ids"], "entry_ids": written["entry_ids"],
        "bankroll_event_keys": written["bankroll_event_keys"],
    }
    with conn.transaction():
        at = old["occurred_at"] if old else now()
        _check(at >= max(started["occurred_at"], prepared["occurred_at"]))
        refs = operations._append_document(conn, _RECOVERY_KIND, _RECOVERY_SOURCE, document, at)
        return emit("RECOVERY_READ", at, outputs=refs, scan_id=written["scan_id"])


def _run_locked(conn, config_bytes, repo_root, input_set_sha, key, attempt, commit, instance_id,
                now, evaluation_loader, shadow_budget_seconds, pack_base_dir):
    config_sha = inputs._digest(config_bytes)
    refs = _binding_refs(input_set_sha, config_sha)
    receipts = pg.d1_operation_receipts(conn, key)
    first, prepared, succeeded = _inspect(receipts, key, commit, config_sha, refs)

    def emit(phase, at, **values):
        return _receipt(conn, key, attempt, commit, config_sha, refs, phase, at, **values)

    if prepared is not None:
        binding, _ = _read_bound(conn, prepared)
        _validate_first_start(first, prepared, binding)
        committed = pg.committed_prepared_scan(conn, key, _expected(binding))
        if succeeded is not None:
            _check(committed is not None)
            proof = receipt_witness(conn, {
                "scan_id": committed["scan_id"], "scan_operation_key": key, "scan_code_commit": commit,
                "scan_started_at": _expected(binding)["started_at"],
                "scan_finished_at": _expected(binding)["finished_at"],
            }, receipts)
            _check(proof is not None)
            return _result(committed, binding, recovered=proof.get("recovery") is not None, succeeded=succeeded)
    else:
        _check(pg.committed_prepared_scan(conn, key) is None)
        committed = None
    prior = next((row for row in receipts if row["attempt"] == attempt and row["phase"] == "STARTED"), None)
    if any(row["attempt"] == attempt and row["phase"] == "FAILED" for row in receipts):
        raise scan.ScanError("papersim_prepare_attempt_already_failed")
    started = prior or emit("STARTED", now())
    first = first or started
    try:
        if committed is not None:
            recovery = _recover(conn, binding, prepared, started, committed, now, emit)
            success = emit("SUCCEEDED", now(), outputs=(
                committed["scan_key"], recovery["run_event_key"]), scan_id=committed["scan_id"])
            return _result(committed, binding, recovered=True, succeeded=success)

        config = inputs._scan_configuration(config_bytes)
        legacy_records = scan.load_pack_records(config, base_dir=pack_base_dir)
        specs = None if prepared is None else _load_specs(binding["specs"])
        with pg.d1_read_view(conn):
            pg.d1_resolution_identity(conn, instance_id=instance_id)
            resolved = inputs._resolve(conn, repo_root, config_bytes, config, input_set_sha,
                                       instance_id, pinned_specs=specs)
            pack = resolved.prepare_market_pack()
            records = (*_records(resolved, pack), *legacy_records)
            if prepared is None:
                specs = dict(resolved.specs)
                missing_records = [record for record in legacy_records if record.lineage not in specs]
                specs.update(scan.consult_provider(
                    inputs.RegistryProvider.from_connection(conn), missing_records) if missing_records else {})
                admissions = scan.pause_admissions(evaluation_loader(conn), config.queues, pg.latched_pauses(conn))
                portfolio = scan.read_portfolio_state(conn, prepared=True)
                ended_at = now()
                paused = sorted(admissions.paused_queues)
            else:
                portfolio = scan.PortfolioState(**pg._spec_sequences_to_tuples(binding["portfolio"]))
                ended_at = _expected(binding)["finished_at"]
                paused = binding["paused_queues"]
                shadow_budget_seconds = binding["shadow_budget_seconds"]
        prepared_candidates = [record.candidate_key for record in records if record.prepared_provenance is not None]
        material = {"input_set_sha256": resolved.input_set_sha256, "config_sha256": resolved.config_sha256,
                    "pack": _pack_reference(pack), "candidate_keys": prepared_candidates}
        with pg.d1_read_view(conn):
            assembly = _assemble(
                config, records, conn, started_at=first["occurred_at"], finished_at=ended_at, code_commit=commit,
                key=key, specs=specs, paused_queues=paused, portfolio=portfolio,
                material=material, shadow_budget_seconds=shadow_budget_seconds,
            )
        submission = pg.canonical_scan_submission(assembly.submission)
        business = operations.canonical_document({
            "scan_key": submission.scan.key(), "scan_payload_sha256": submission.payload_sha256(),
            "started_at": first["occurred_at"], "finished_at": ended_at, "team": config.team, "code_commit": commit,
            "candidate_keys": [row.candidate_key for row in submission.candidates],
            "entry_keys": [row.candidate_key for row in submission.candidates if row.entry is not None],
        })
        if prepared is None:
            at = now()
            with conn.transaction():
                config_row = snapshots.verify_snapshot(conn, resolved.config_sha256)
                input_row = snapshots.verify_snapshot(conn, input_set_sha)
                coverage = operations._append_document(conn, _COVERAGE_KIND, _COVERAGE_SOURCE,
                                                       _coverage(resolved, pack), at)
                binding = operations.canonical_document({
                    "version": 1, "operation_key": key, "prepared_attempt": attempt, "prepared_at": at,
                    "first_start": first["run_event_key"], **material,
                    "config": _prepared_reference(resolved.config_sha256, config_row["byte_length"]),
                    "input_set": _prepared_reference(input_set_sha, input_row["byte_length"]),
                    "batch_sha256": resolved.batch_sha256, "decision_as_of": resolved.decision_as_of,
                    "coverage": coverage, "specs": _specs_document(specs), "portfolio": asdict(portfolio),
                    "paused_queues": paused, "business": business, "model_versions": sorted(set(assembly.model_versions)),
                    "suppressed_entries": list(assembly.suppressed_entries), "shadow_rows": len(assembly.shadows),
                    "shadow_budget_seconds": shadow_budget_seconds,
                })
                outputs = operations._append_document(conn, _BINDING_KIND, _BINDING_SOURCE, binding, at)
                prepared = emit("PREPARED", at, outputs=outputs)
        else:
            _check(binding["business"] == business and binding["pack"] == material["pack"],
                   "papersim_prepare_reconstruction_conflict")

        with pg.prepared_scan_write_transaction(conn):
            current = scan.pause_admissions(evaluation_loader(conn), config.queues, pg.latched_pauses(conn))
            portfolio = scan.read_portfolio_state(conn, prepared=True)
            _verify_eligibility(assembly, records, portfolio, current, paused)
            _write_pauses(conn, current, first["occurred_at"])
            written = pg.record_scan(conn, submission)
            if assembly.shadows:
                pg.record_shadow_predictions(conn, assembly.shadows, written["candidate_ids"])
        committed = pg.committed_prepared_scan(conn, key, _expected(binding))
        _check(committed is not None)
        committed["inserted"] = written["inserted"]
        if prepared["attempt"] == attempt and first["attempt"] == attempt:
            outputs = (written["scan_key"], *prepared["output_evidence"])
        else:
            recovery = _recover(conn, binding, prepared, started, committed, now, emit)
            outputs = (written["scan_key"], recovery["run_event_key"])
        success = emit("SUCCEEDED", now(), outputs=outputs, scan_id=written["scan_id"])
        return _result(committed, binding, recovered=False, succeeded=success)
    except Exception as error:
        if not conn.closed:
            code = operations._error_code(error, "papersim_prepare_scan_failed")
            emit("FAILED", now(), error_code=code)
        if isinstance(error, (scan.ScanError, prepare.PreparationError, store.PapersimStoreError)):
            raise
        raise scan.ScanError("papersim_prepare_scan_failed") from None


def run_prepared_scan(conn, *, repo_root, config_bytes, input_set_sha256, operation_key, attempt,
                      now, evaluation_loader, shadow_budget_seconds, pack_base_dir=None, git_bin="git"):
    _check(type(operation_key) is str and re.fullmatch(r"[A-Za-z0-9_./:|+-]{1,240}", operation_key)
           and type(attempt) is int and 1 <= attempt <= 2147483647, "papersim_prepare_operation_invalid")
    snapshots.snapshot_digest_argument(input_set_sha256)
    root = Path(repo_root).resolve()
    commit = scan.require_clean_worktree(root, git_bin=git_bin)
    instance_id = pg.d1_managed_identity(conn, root)["instance_id"]
    with pg.d1_operation_lock(conn, operation_key):
        return _run_locked(conn, config_bytes, root, input_set_sha256, operation_key, attempt, commit,
                           instance_id, now, evaluation_loader, shadow_budget_seconds, pack_base_dir or root)


def receipt_witness(conn, candidate, receipts):
    """Verify the original successful B1 chain against its actual scan."""
    success = next((row for row in receipts if row["phase"] == "SUCCEEDED"
                    and row["command"] == scan.SCAN_COMMAND and row["scan_id"] == candidate["scan_id"]), None)
    preparations = [row for row in receipts if row["phase"] == "PREPARED" and row["command"] == scan.SCAN_COMMAND]
    if success is None or not preparations:
        return None
    _check(len(preparations) == 1, "papersim_prepare_receipt_invalid")
    prepared = preparations[0]
    binding, _ = _read_bound(conn, prepared)
    start, _, _ = _inspect(receipts, candidate["scan_operation_key"], candidate["scan_code_commit"],
                            binding["config_sha256"],
                            _binding_refs(binding["input_set_sha256"], binding["config_sha256"]))
    _validate_first_start(start, prepared, binding)
    expected = _expected(binding)
    written = pg.committed_prepared_scan(conn, candidate["scan_operation_key"], expected)
    _check(written is not None and written["scan_id"] == candidate["scan_id"]
           and (expected["started_at"], expected["finished_at"]) == (
               candidate["scan_started_at"], candidate["scan_finished_at"])
           and success["error_code"] is None and success["occurred_at"] >= prepared["occurred_at"])
    witness = {"first_start": operations._receipt_ref(start), "prepared": operations._receipt_ref(prepared),
               "successful": operations._receipt_ref(success), "binding": prepared["output_evidence"]}
    if success["attempt"] == start["attempt"] == prepared["attempt"] and success["output_evidence"] == [
            expected["scan_key"], *prepared["output_evidence"]]:
        return witness
    attempt_rows = {row["phase"]: row for row in receipts if row["attempt"] == success["attempt"]}
    recovery, resume = attempt_rows.get("RECOVERY_READ"), attempt_rows.get("STARTED")
    _check(resume is not None and recovery is not None and recovery["error_code"] is None
           and resume["error_code"] is None and resume["scan_id"] is None and resume["output_evidence"] == []
           and recovery["scan_id"] == written["scan_id"]
           and prepared["occurred_at"] <= recovery["occurred_at"] <= success["occurred_at"]
           and resume["occurred_at"] <= recovery["occurred_at"]
           and success["output_evidence"] == [expected["scan_key"], recovery["run_event_key"]])
    proof = operations._read_document(
        conn, recovery["output_evidence"], kind=_RECOVERY_KIND, source=_RECOVERY_SOURCE)
    _check(proof == {
        "version": 1, "operation_key": candidate["scan_operation_key"], "attempt": recovery["attempt"],
        "started": resume["run_event_key"], "first_start": start["run_event_key"],
        "prepared": prepared["run_event_key"], "binding": prepared["output_evidence"],
        "scan_id": written["scan_id"], "scan_key": written["scan_key"],
        "scan_payload_sha256": expected["scan_payload_sha256"], "candidate_ids": written["candidate_ids"],
        "entry_ids": written["entry_ids"], "bankroll_event_keys": list(written["bankroll_event_keys"]),
    })
    return {**witness, "resume": operations._receipt_ref(resume), "recovery": operations._receipt_ref(recovery)}


def replay_record(conn, operation_key, candidate_key):
    """Reconstruct one originally prepared record using pinned specs and raw bytes."""
    root = getattr(conn, "_papersim_managed_root", None)
    _check(root is not None, "papersim_prepare_managed_required")
    instance_id = pg.d1_managed_identity(conn, root)["instance_id"]
    receipts = pg.d1_operation_receipts(conn, operation_key)
    prepared = [row for row in receipts if row["phase"] == "PREPARED" and row["command"] == scan.SCAN_COMMAND]
    _check(len(prepared) == 1)
    binding, config_bytes = _read_bound(conn, prepared[0])
    if candidate_key not in binding["candidate_keys"]:
        return None
    resolved = _verify_base(conn, root, config_bytes, binding["input_set_sha256"],
                            instance_id, pinned_specs=_load_specs(binding["specs"]))
    pack = resolved.prepare_market_pack()
    _check(_pack_reference(pack) == binding["pack"] and binding["batch_sha256"] == resolved.batch_sha256)
    row = next((row for row in _records(resolved, pack) if row.candidate_key == candidate_key), None)
    _check(row is not None, "papersim_prepare_candidate_binding_missing")
    return row
