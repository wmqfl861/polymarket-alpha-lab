"""Inspect one recorded execution claim without retrying or scoring research.

The managed existing lookup revalidates stored request/result bindings in one
read-only transaction. No raw evidence, model output, credentials or repair is
exported. A missing result says nothing about whether a worker is still alive.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from pathlib import Path

from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_execution import CapturedResearchExecution
from polymarket_alpha_lab.team_research_agent_types import identifier


def execution_summary(state: CapturedResearchExecution, *, record_id: str) -> dict:
    """Metadata for a persisted lookup, not live execution or global completeness.

    Only the existing lookup's already_captured/incomplete states are accepted;
    a capture_failed in-memory recovery object is not a database receipt.
    """
    identifier("record_id", record_id)
    if type(state) is not CapturedResearchExecution:
        raise ValueError("research_execution_inspection_invalid")
    state = replace(state)
    if state.request.record_id != record_id or state.status not in ("already_captured", "incomplete"):
        raise ValueError("research_execution_inspection_scope_invalid")
    request, record = state.request, state.record
    intake = request.intake
    research = None if record is None else record.run.research
    outcome = ("result_not_captured" if record is None else "captured_intake_blocked" if research is None
               else "captured_" + research.status)
    output = state.to_dict()
    output.update(inspection_status=outcome, team_id=intake.team_id, condition_id=intake.condition_id,
        market_slug=intake.market_slug, model_id=request.model_id, protocol_version=request.protocol_version,
        data_as_of=intake.as_of.isoformat(), forecast_cutoff_at=request.forecast_cutoff_at.isoformat(),
        intake_status=intake.status, intake_reason_code=intake.reason_code,
        eligible_source_count=len(intake.source_receipts), required_source_count=len(request.required_source_ids),
        limits=asdict(request.limits), max_start_delay_seconds=request.max_start_delay_seconds,
        this_claim_has_no_captured_result=record is None, worker_liveness="unknown",
        entire_history_checked=False, scoring_performed=False, forecast_approval_performed=False,
        automatic_retry_permitted=False, model_identity_verified=False,
        stored_research=None if research is None else dict(status=research.status, reason_code=research.reason_code,
            model_calls=research.model_calls, tool_calls=research.tool_calls, total_tokens=research.total_tokens,
            cited_source_count=len(research.source_ids)))
    return output


def _record_id(value: str) -> str:
    try:
        identifier("record_id", value)
    except ValueError:
        raise argparse.ArgumentTypeError("record-id must be a valid project record identifier") from None
    return value


def main(argv: list[str] | None = None, *, default_root: Path) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--record-id", type=_record_id, required=True)
    args = parser.parse_args(argv)
    # Import at invocation, not module initialization: confirmation already
    # imports the resolution summary. Reuse its checked emitter without a cycle.
    from polymarket_alpha_lab.research_resolution_confirmation_cli import _emit
    envelope = dict(record_id=args.record_id, lookup_scope="execution_claim_and_matching_attempt",
        public_network_called=False, live_model_called=False, business_writes_performed=False,
        paper_only=True, report_only=True, readonly=True)
    try:
        with ProjectPostgres(args.root).session() as session:
            state = session.inspect(record_id=args.record_id)
            result = None if state is None else execution_summary(state, record_id=args.record_id)
        # Publish only after successful cleanup, including the not-found case.
        envelope.update(status="claim_not_found" if result is None else "inspected", inspection=result)
        code = 3 if result is None else 0
    except (Exception, SystemExit):
        envelope.update(status="failed", reason_code="research_execution_inspection_failed", inspection=None)
        code = 1
    return _emit(envelope, code)


__all__ = ("execution_summary", "main")
