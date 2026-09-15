"""Read-only operator view of existing captured-research diagnostics.

No model, public fetch, outcome capture, migration or file-backed report. The
managed session's existing evaluator enforces complete visible execution claims
in one DB snapshot; this view never falls back to the weaker legacy loader.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
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
    parser.add_argument("--paper-scenarios", action="store_true",
        help="Read bounded hypothetical book/cost scenarios from stdin; no paper records are written")
    args = parser.parse_args(argv)
    if not 1 <= args.max_records <= MAX_RECORDS:
        parser.error("max-records must be 1..10000")
    try:
        ResearchProbabilityDiagnostics((), args.buckets, args.min_sample_count, args.min_bin_count)
    except ValueError:
        parser.error("sample and bin thresholds must be 1..10000")
    scenarios = None
    if args.paper_scenarios:
        from polymarket_alpha_lab.research_paper_inputs import MAX_INPUT_BYTES, decode_scenarios
        try:
            scenarios = decode_scenarios(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
        except KeyboardInterrupt:
            print(json.dumps(dict(status="interrupted", reason_code="paper_input_interrupted",
                                  business_writes_performed=False)))
            return 130
        except (Exception, SystemExit):
            print(json.dumps(dict(status="invalid_input", reason_code="paper_scenario_input_invalid",
                                  business_writes_performed=False)))
            return 2
    envelope = dict(public_network_called=False, live_model_called=False, business_writes_performed=False,
                    outcome_confirmation_performed=False, paper_only=True, report_only=True, readonly=True)
    try:
        with ProjectPostgres(args.root).session() as session:
            options = dict(generated_at=args.as_of, max_records=args.max_records,
                bucket_count=args.buckets, min_sample_count=args.min_sample_count, min_bin_count=args.min_bin_count)
            if scenarios is None:
                report = session.evaluate(**options)
                result = evaluation_summary(report, include_decisions=args.include_decisions)
            else:
                from polymarket_alpha_lab.research_paper_evaluation import ResearchPaperReplay
                replay = session.evaluate_paper(scenarios=scenarios, **options)
                if type(replay) is not ResearchPaperReplay:
                    raise ValueError("paper_receipt_invalid")
                replay = replace(replay)
                history = replay.history
                if (replay.scenarios != scenarios or len(history.records) > args.max_records
                        or (history.bucket_count, history.min_sample_count, history.min_bin_count)
                            != (args.buckets, args.min_sample_count, args.min_bin_count)
                        or (args.as_of is not None and history.generated_at != args.as_of)):
                    raise ValueError("paper_receipt_mismatch")
                result = replay.to_dict()
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
            raise
        envelope.update(status="interrupted", history_gate="not_established",
            reason_code="research_evaluation_interrupted", evaluation=None)
        code = 130
    except SystemExit:
        if scenarios is None:
            raise
        envelope.update(status="failed", history_gate="not_established",
            reason_code="research_evaluation_operation_failed", evaluation=None)
        code = 1
    except Exception:
        envelope.update(status="failed", history_gate="not_established",
            reason_code="research_evaluation_operation_failed", evaluation=None)
        code = 1
    print(json.dumps(envelope, ensure_ascii=True, allow_nan=False, indent=2))
    return code


__all__ = ("evaluation_summary", "main")
