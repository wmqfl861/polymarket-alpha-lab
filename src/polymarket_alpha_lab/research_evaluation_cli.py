"""Read-only operator view of existing captured-research diagnostics.

The default shows probability diagnostics; --settled-paper shows the existing
reviewed, assumed-cost settlement view. Neither mode approves a forecast or fee.
No model, public fetch, outcome capture, migration or file-backed report. The
managed session's existing evaluator enforces complete visible execution claims
in one DB snapshot; this view never falls back to the weaker legacy loader.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_resolution_confirmation_cli import _emit
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
    parser.add_argument("--settled-paper", action="store_true",
        help="show saved simulation settlement bounds, not actual account PnL")
    args = parser.parse_args(argv)
    if not 1 <= args.max_records <= MAX_RECORDS:
        parser.error("max-records must be 1..10000")
    try:
        ResearchProbabilityDiagnostics((), args.buckets, args.min_sample_count, args.min_bin_count)
    except ValueError:
        parser.error("sample and bin thresholds must be 1..10000")
    if args.settled_paper:
        return _run_settled_paper(args)
    envelope = dict(public_network_called=False, live_model_called=False, business_writes_performed=False,
                    outcome_confirmation_performed=False, paper_only=True, report_only=True, readonly=True)
    try:
        with ProjectPostgres(args.root).session() as session:
            report = session.evaluate(generated_at=args.as_of, max_records=args.max_records,
                bucket_count=args.buckets, min_sample_count=args.min_sample_count, min_bin_count=args.min_bin_count)
            result = evaluation_summary(report, include_decisions=args.include_decisions)
        # A session-exit error must not print an apparently successful report.
        envelope.update(status="evaluated", history_gate="complete_visible_execution_claims", evaluation=result)
        code = 0
    except ResearchCaptureConflict as error:
        reason = error.args[0] if len(error.args) == 1 and type(error.args[0]) is str else None
        known = reason in _BLOCKS
        envelope.update(status="blocked" if known else "failed", history_gate="not_established",
            reason_code=reason if known else "research_evaluation_operation_failed", evaluation=None)
        code = 1
    except (Exception, SystemExit):
        envelope.update(status="failed", history_gate="not_established",
            reason_code="research_evaluation_operation_failed", evaluation=None)
        code = 1
    return _emit(envelope, code)


def _settled_paper_summary(report: dict, *, include_decisions: bool) -> dict:
    """Project the existing managed export, without recalculating any money.

    Envelope checks detect incompatible exports, not forged source authenticity.
    Source/amount verification belongs to the fixed managed settlement evaluator.
    Never use this presenter as an independent database/history validator.
    """
    from polymarket_alpha_lab.research_paper_settlement import _STATUSES

    constants = dict(schema_version="research-paper-settlement-v1",
        complete_visible_execution_history=True, single_database_snapshot=True,
        settled_subset_only=True, money_unit="binary_payout_units", costs_are_assumptions=True,
        source_authentication_performed=False, tariff_verified=False,
        commit_before_cutoff_verified=False, actual_account_pnl=None,
        paper_trades_created=0, business_writes_performed=False,
        portfolio_return_computed=False, strategy_validation_performed=False,
        paper_only=True, report_only=True, readonly=True)
    fields = {"history", "attempts", "groups", "attempt_count", "paper_evidence_count",
              "status_counts", "input_sha256"}
    if (type(report) is not dict or type(include_decisions) is not bool
            or set(report) != fields | constants.keys()
            or any(type(report[k]) is not type(v) or report[k] != v for k, v in constants.items())):
        raise ValueError("settled_paper_export_invalid")
    history, rows = report["history"], report["attempts"]
    if (type(history) is not dict or history.get("schema_version") != "research-evaluation-v1"
            or any(history.get(k) is not True for k in ("paper_only", "report_only", "readonly"))
            or type(rows) is not list or type(report["groups"]) is not list
            or type(report["attempt_count"]) is not int or report["attempt_count"] != len(rows)
            or type(report["paper_evidence_count"]) is not int
            or not 0 <= report["paper_evidence_count"] <= len(rows)
            or history.get("record_count") != len(rows)
            or type(history.get("decisions")) is not list or len(history["decisions"]) != len(rows)):
        raise ValueError("settled_paper_export_invalid")
    for row, decision in zip(rows, history["decisions"], strict=True):
        if (type(row) is not dict or type(decision) is not dict
                or any(row[k] != decision[k] for k in
                       ("record_id", "record_sha256", "team_id", "model_id", "protocol_version", "condition_id"))
                or row["original_reason_code"] != decision["reason_code"]
                or row["status"] not in _STATUSES):
            raise ValueError("settled_paper_export_invalid")
    counts = report["status_counts"]
    if (type(counts) is not dict or set(counts) != set(_STATUSES)
            or any(type(counts[k]) is not int or counts[k] != sum(r["status"] == k for r in rows)
                   for k in _STATUSES)):
        raise ValueError("settled_paper_export_invalid")
    # Detach the JSON export before omitting per-record data. Refuse non-finite
    # values instead of emitting nonstandard JSON; preserve decimal strings.
    output = json.loads(json.dumps(report, ensure_ascii=True, allow_nan=False))
    if not include_decisions:
        output.pop("attempts")
        output["history"].pop("decisions")
    output["decisions_included"] = include_decisions
    return output


def _run_settled_paper(args) -> int:
    """One read, one output, no fallback/retry or new capture permission."""
    envelope = dict(evaluation_kind="settled_paper", public_network_called=False,
        live_model_called=False, business_writes_performed=False,
        outcome_confirmation_performed=False, paper_only=True, report_only=True, readonly=True)
    reason = "research_paper_evaluation_operation_failed"
    try:
        with ProjectPostgres(args.root).session() as session:
            report = session.evaluate_settled_paper_research(generated_at=args.as_of,
                max_records=args.max_records, bucket_count=args.buckets,
                min_sample_count=args.min_sample_count, min_bin_count=args.min_bin_count)
            result = _settled_paper_summary(report, include_decisions=args.include_decisions)
            history = result["history"]
            at = _timestamp(history["generated_at"])
            if (args.as_of is not None and at != args.as_of) or any(
                    type(history[k]) is not int or not 0 <= history[k] <= args.max_records
                    for k in ("record_count", "outcome_count")):
                raise ValueError("settled_paper_request_mismatch")
            for group in history["groups"]:
                scores = group["scores"]
                if (any(scores.get(k) is not True for k in ("paper_only", "report_only", "readonly"))
                        or any(type(scores[k]) is not int or scores[k] != v for k, v in
                            (("bucket_count", args.buckets), ("min_sample_count", args.min_sample_count),
                             ("min_bin_count", args.min_bin_count)))):
                    raise ValueError("settled_paper_request_mismatch")
        # The shared emitter serializes only after successful managed cleanup.
        envelope.update(status="evaluated",
            history_gate="complete_visible_execution_claims", evaluation=result)
        code = 0
    except KeyboardInterrupt:
        envelope.update(status="interrupted", reason_code="research_paper_evaluation_interrupted")
        code = 130
    except ResearchCaptureConflict as error:
        value = error.args[0] if len(error.args) == 1 and type(error.args[0]) is str else None
        known = value in _BLOCKS | {"research_paper_settlement_read_limit"}
        envelope.update(status="blocked" if known else "failed", reason_code=value if known else reason)
        code = 1
    except (Exception, SystemExit):
        envelope.update(status="failed", reason_code=reason)
        code = 1
    if code:
        envelope.update(history_gate="not_established", evaluation=None)
    return _emit(envelope, code)


__all__ = ("evaluation_summary", "main")
