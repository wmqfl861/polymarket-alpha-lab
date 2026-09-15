"""Read-only operator view of existing captured-research diagnostics.

No model, public fetch, outcome capture, migration or file-backed report. The
managed session's existing evaluator enforces complete visible execution claims
in one DB snapshot; this view never falls back to the weaker legacy loader.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import sys
from pathlib import Path

from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_capture_psycopg import ResearchCaptureConflict
from polymarket_alpha_lab.research_probability_scores import BUCKET_COUNTS, ResearchProbabilityDiagnostics
from polymarket_alpha_lab.team_research_evaluation import MAX_RECORDS, REASONS, ResearchEvaluationReport

_BLOCKS = frozenset(("research_execution_history_incomplete", "research_capture_history_limit",
                     "research_evaluation_from_future"))


def evaluation_summary(report: ResearchEvaluationReport, *, include_decisions: bool = False) -> dict:
    """Recompute existing canonical diagnostics, never pool or rank model scores.

    This pure formatter does not authenticate the report's origin or history.
    Only main's successful managed read establishes the execution-history gate.
    """
    if type(report) is not ResearchEvaluationReport or type(include_decisions) is not bool:
        raise ValueError("research_evaluation_summary_invalid")
    output = report.to_dict()  # Revalidates inputs and rebuilds derived values.
    decisions = output.pop("decisions")
    counts = {reason: sum(row["reason_code"] == reason for row in decisions) for reason in REASONS}
    visible = len(decisions) - counts["not_yet_recorded"]
    state = ("no_visible_attempts" if not visible else
             "diagnostics_available" if counts["scored"] else "no_scored_forecasts")
    output.update(evaluation_status=state, visible_attempt_count=visible,
        selected_attempt_count=visible - counts["later_attempt"],
        scored_decision_count=counts["scored"],
        scored_condition_count=len({row["condition_id"] for row in decisions if row["reason_code"] == "scored"}),
        decision_counts=counts, group_count=len(output["groups"]), decisions_included=include_decisions,
        pooled_score_computed=False, forecast_approval_performed=False)
    if include_decisions:
        output["decisions"] = decisions
    return output


def _timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("timezone required")
        return result.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        raise argparse.ArgumentTypeError("as-of must be an ISO timestamp with an explicit timezone") from None


def main(argv: list[str] | None = None, *, default_root: Path) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--as-of", type=_timestamp, help="optional historical cutoff; future DB times are rejected")
    parser.add_argument("--max-records", type=int, default=MAX_RECORDS)
    parser.add_argument("--buckets", type=int, choices=BUCKET_COUNTS, default=10)
    parser.add_argument("--min-sample-count", type=int, default=30)
    parser.add_argument("--min-bin-count", type=int, default=5)
    parser.add_argument("--include-decisions", action="store_true", help="also show per-record IDs, reasons and probabilities")
    parser.add_argument("--paper-stdin", action="store_true", help="read bounded historical paper scenarios from stdin; no writes")
    args = parser.parse_args(argv)
    if not 1 <= args.max_records <= MAX_RECORDS:
        parser.error("max-records must be 1..10000")
    try:
        ResearchProbabilityDiagnostics((), args.buckets, args.min_sample_count, args.min_bin_count)
    except ValueError:
        parser.error("sample and bin thresholds must be 1..10000")
    envelope = dict(public_network_called=False, live_model_called=False, business_writes_performed=False,
                    outcome_confirmation_performed=False, paper_only=True, report_only=True, readonly=True)
    scenarios = None
    if args.paper_stdin:
        from polymarket_alpha_lab.research_paper import MAX_INPUT_BYTES
        from polymarket_alpha_lab.research_paper_input import decode_paper_scenarios
        try:
            scenarios = decode_paper_scenarios(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
        except (Exception, SystemExit, KeyboardInterrupt) as error:
            envelope.update(status="blocked", reason_code="research_paper_input_invalid", evaluation=None,
                            history_gate="not_established")
            print(json.dumps(envelope, ensure_ascii=True, allow_nan=False, indent=2))
            return 130 if isinstance(error, KeyboardInterrupt) else 2
    try:
        with ProjectPostgres(args.root).session() as session:
            config = dict(generated_at=args.as_of, max_records=args.max_records,
                bucket_count=args.buckets, min_sample_count=args.min_sample_count, min_bin_count=args.min_bin_count)
            if scenarios is None:
                report = session.evaluate(**config)
                result = evaluation_summary(report, include_decisions=args.include_decisions)
            else:
                from polymarket_alpha_lab.research_paper import ResearchPaperEvaluation
                report = session.evaluate_paper(scenarios=scenarios, **config)
                if (type(report) is not ResearchPaperEvaluation
                        or tuple(s.content_sha256 for s in report.scenarios) != tuple(s.content_sha256 for s in scenarios)
                        or (args.as_of is not None and report.evaluation.generated_at != args.as_of)
                        or (report.evaluation.bucket_count, report.evaluation.min_sample_count,
                            report.evaluation.min_bin_count) != (args.buckets, args.min_sample_count, args.min_bin_count)):
                    raise ValueError("research_paper_receipt_mismatch")
                result = report.to_dict()
        # A session-exit error must not print an apparently successful report.
        envelope.update(status="evaluated", history_gate="complete_visible_execution_claims", evaluation=result)
        code = 0
    except ResearchCaptureConflict as error:
        reason = str(error)
        known = reason in _BLOCKS
        envelope.update(status="blocked" if known else "failed", history_gate="not_established",
            reason_code=reason if known else "research_evaluation_operation_failed", evaluation=None)
        code = 1
    except KeyboardInterrupt:
        if scenarios is None:
            raise  # Preserve the original non-paper CLI interruption contract.
        envelope.update(status="interrupted", history_gate="not_established",
            reason_code="research_evaluation_interrupted", evaluation=None)
        code = 130
    except (Exception, SystemExit) as error:
        if scenarios is None and isinstance(error, SystemExit):
            raise  # Only the opt-in paper mode adds this sanitized boundary.
        envelope.update(status="failed", history_gate="not_established",
            reason_code="research_evaluation_operation_failed", evaluation=None)
        code = 1
    print(json.dumps(envelope, ensure_ascii=True, allow_nan=False, indent=2))
    return code


__all__ = ("evaluation_summary", "main")
