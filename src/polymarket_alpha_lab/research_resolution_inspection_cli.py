"""Inspect one retained resolution review without confirming or rescoring it.

The existing managed lookup rechecks canonical evidence and an optional linked
outcome. Export metadata, not raw bodies or operator identity/source text. The
assessment is evaluated at the stored check time, not refreshed at lookup time.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_resolution import assess_resolution
from polymarket_alpha_lab.research_resolution_codec import encode_resolution
from polymarket_alpha_lab.research_resolution_store import StoredResolutionReview
from polymarket_alpha_lab.team_research_agent_types import identifier
from polymarket_alpha_lab.team_research_evaluation import ResearchEvaluationOutcome


_INSPECTIONS = {"pending": "recorded_pending", "blocked": "recorded_blocked",
                "needs_confirmation": "recorded_needs_confirmation", "ready": "recorded_operator_confirmed"}


def resolution_review_summary(review: StoredResolutionReview, *, review_id: str) -> dict:
    """Revalidate a detached receipt and expose only deliberately chosen fields.

    A pure caller-supplied object is not proof of a database read. The managed
    CLI performs that read. A null linked outcome means THIS review has no link,
    not that no other review or legacy outcome exists for the same market.
    """
    identifier("review_id", review_id)
    if type(review) is not StoredResolutionReview:
        raise ValueError("research_resolution_inspection_invalid")
    outcome = review.outcome
    if outcome is not None:
        if type(outcome) is not ResearchEvaluationOutcome:
            raise ValueError("research_resolution_inspection_invalid")
        # StoredResolutionReview validates its outcome in place. Detach first so
        # revalidation/UTC normalization cannot modify the caller's nested object.
        outcome = replace(outcome)
    review = replace(review, outcome=outcome)
    item = review.submission
    if item.review_id != review_id:
        raise ValueError("research_resolution_inspection_scope_invalid")
    assessment = assess_resolution(item)
    payload = encode_resolution(item).encode("utf-8")
    proof = item.confirmation
    return dict(review_id=review_id, condition_id=item.condition_id, market_slug=item.snapshot.market_slug,
        inspection_status=_INSPECTIONS[assessment.status],
        checked_at=item.checked_at.isoformat(), recorded_at=review.recorded_at.isoformat(),
        payload_sha256=sha256(payload).hexdigest(), payload_bytes=len(payload),
        assessment=dict(status=assessment.status, reason_code=assessment.reason_code,
                        candidate_yes=assessment.candidate_yes),
        assessment_basis="stored_checked_at", current_freshness_checked=False,
        snapshot=dict(fetched_at=item.snapshot.fetched_at.isoformat(),
                      content_sha256=item.snapshot.content_sha256, bytes=len(item.snapshot.raw_json)),
        confirmation_provided=proof is not None,
        submitted_confirmation=None if proof is None else dict(asserted_yes=proof.actual_yes,
            asserted_resolved_at=proof.resolved_at.isoformat(), confirmed_at=proof.confirmed_at.isoformat(),
            gamma_content_sha256=proof.gamma_content_sha256, source_content_sha256=proof.source_content_sha256,
            source_bytes=len(proof.source_text.encode("utf-8")), independently_verified_assertion=proof.independently_verified),
        linked_outcome=None if outcome is None else dict(actual_yes=outcome.actual_yes,
            forecast_cutoff_at=outcome.forecast_cutoff_at.isoformat(), resolved_at=outcome.resolved_at.isoformat(),
            recorded_at=outcome.recorded_at.isoformat(), source_content_sha256=outcome.source_content_sha256),
        other_reviews_checked=False, market_settlement_status_checked=False,
        independent_verification_performed=False, scoring_performed=False,
        forecast_approval_performed=False, paper_only=True, report_only=True, readonly=True)


def _review_id(value: str) -> str:
    try:
        identifier("review_id", value)
    except ValueError:
        raise argparse.ArgumentTypeError("review-id must be a valid project review identifier") from None
    return value


def main(argv: list[str] | None = None, *, default_root: Path) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--review-id", type=_review_id, required=True)
    args = parser.parse_args(argv)
    envelope = dict(review_id=args.review_id, lookup_scope="resolution_review_and_its_linked_outcome",
        public_network_called=False, live_model_called=False, business_writes_performed=False,
        outcome_confirmation_performed=False, paper_only=True, report_only=True, readonly=True)
    try:
        with ProjectPostgres(args.root).session() as session:
            review = session.inspect_resolution(review_id=args.review_id)
            result = None if review is None else resolution_review_summary(review, review_id=args.review_id)
        # No apparent success (including not-found) before successful cleanup.
        envelope.update(status="review_not_found" if result is None else "inspected", inspection=result)
        code = 3 if result is None else 0
    except (Exception, SystemExit):
        envelope.update(status="failed", reason_code="research_resolution_inspection_failed", inspection=None)
        code = 1
    # Confirmation imports the pure summary above; defer this reverse import
    # until both modules are initialized. Reuse the existing checked writer.
    from polymarket_alpha_lab.research_resolution_confirmation_cli import _emit
    return _emit(envelope, code)


__all__ = ("resolution_review_summary", "main")
