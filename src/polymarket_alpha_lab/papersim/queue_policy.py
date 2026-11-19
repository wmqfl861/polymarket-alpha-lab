"""FX-17 cross-league and blueprint universal pause rules: decision layer.

M2 node C1 (M2 plan section 3.8, OD-G numbers registered 2026-11-13 in
``DELIVERY_PLAN.md`` 77.3.2). This module is the pure decision layer
only: it maps queue metrics -- the same numbers ``papersim.evaluation``
already publishes -- to per-queue admission recommendations. It opens no
connection, reads no file, touches no clock and writes nothing; wiring
the decisions into run-scan admission, persisting pause state and
latching it across restarts is B1's job.

Rules (all thresholds are reused from ``evaluation``, never redeclared):

- FX-17 cross-league (win/loss boards whose league scope is explicitly
  CROSS_LEAGUE): >= 10 settled with net PnL strictly below -$600, or
  >= 20 entries with the CLV 95% upper bound strictly below 0, pause
  new entries for review. A DOMESTIC queue never uses this branch; the
  FX-17 reasons a domestic queue would otherwise produce are reported
  as suppressed, not acted on.
- Blueprint universal: >= 40 entries with the CLV 95% upper bound
  strictly below 0 or max drawdown strictly above $2,500 recommends
  stopping new entries (record-only mode). Applies to every scope.
- UNKNOWN metrics (``None``) are never treated as 0 and never trigger a
  pause by themselves.
- Latching: a previously written pause (``pause_review_latched`` /
  ``auto_stop_latched``) survives restarts, missing new data and metric
  rebounds. Decisions only stay or escalate, never de-escalate, and
  nothing here auto-resumes a queue; resumption requires human review.
- A pause means review is needed; it is never a claim that the strategy
  has been proven invalid.
- The kappa table is frozen exactly once before each season and never
  mid-season: ``check_kappa_freeze`` compares a season's frozen digest
  with the observed one; a mismatch (or a missing digest) blocks use of
  that input while keeping both digests as evidence.

During a pause, scanning, candidate recording, marks capture and
settling of existing positions continue -- that too is enforced by the
B1 admission wiring, not here.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from polymarket_alpha_lab.papersim import evaluation

QUEUE_POLICY_RULES_VERSION = "queue-policy-fx17-v1"

SCOPE_DOMESTIC = "DOMESTIC"
SCOPE_CROSS_LEAGUE = "CROSS_LEAGUE"
_LEAGUE_SCOPES = (SCOPE_DOMESTIC, SCOPE_CROSS_LEAGUE)

ACTION_CONTINUE = "CONTINUE"
ACTION_PAUSE_REVIEW = "PAUSE_REVIEW"
ACTION_AUTO_STOP_RECOMMEND = "AUTO_STOP_RECOMMEND"

KAPPA_FREEZE_MATCH = "MATCH"
KAPPA_FREEZE_DRIFTED = "DRIFTED"
KAPPA_FREEZE_UNVERIFIED = "UNVERIFIED"


def _require_scope(value: str) -> None:
    if value not in _LEAGUE_SCOPES:
        raise ValueError(
            f"league_scope must be one of {_LEAGUE_SCOPES}, got {value!r}"
        )


def _require_identity(value: str, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")


def _require_count(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer, got {value!r}")


@dataclass(frozen=True)
class QueuePauseInput:
    """One queue's pause-decision inputs plus its full queue identity.

    Identity (league scope, model version, market type, gate version and
    data-source version) is mandatory so a pause can never bleed between
    queues that differ on any of those dimensions. ``entered`` /
    ``settled`` / ``clv_upper_bound`` / ``max_drawdown`` / ``net_pnl``
    are the metrics ``evaluation`` publishes; ``None`` means UNKNOWN.
    The latch flags carry a previously written pause forward (B1
    persists them; this layer only consumes them).
    """

    queue_id: str
    league_scope: str
    model_key: str
    market_type: str
    gate_version: str
    data_source_version: str
    entered: int
    settled: int
    clv_upper_bound: float | None
    max_drawdown: float | None
    net_pnl: float | None
    pause_review_latched: bool = False
    auto_stop_latched: bool = False

    def __post_init__(self) -> None:
        _require_identity(self.queue_id, "queue_id")
        _require_scope(self.league_scope)
        _require_identity(self.model_key, "model_key")
        _require_identity(self.market_type, "market_type")
        _require_identity(self.gate_version, "gate_version")
        _require_identity(self.data_source_version, "data_source_version")
        _require_count(self.entered, "entered")
        _require_count(self.settled, "settled")


def queue_pause_input(
    queue: evaluation.QueueEvaluation,
    *,
    league_scope: str,
    data_source_version: str,
    queue_id: str | None = None,
    pause_review_latched: bool = False,
    auto_stop_latched: bool = False,
) -> QueuePauseInput:
    """Build the decision input from one ``evaluation`` queue output.

    The metric mapping mirrors ``evaluation._evaluate_queue`` exactly:
    the CLV bound is the queue's bootstrap CI upper bound; net PnL and
    max drawdown are UNKNOWN (``None``) unless at least one row was
    counted for ROI. Scope, data-source version and latch state are not
    evaluation outputs and must be supplied by the caller.
    """
    counted = queue.roi.counted > 0
    if queue_id is None:
        queue_id = "|".join((
            league_scope,
            queue.queue.model_key,
            queue.queue.market_type,
            queue.queue.gate_version,
            data_source_version,
        ))
    return QueuePauseInput(
        queue_id=queue_id,
        league_scope=league_scope,
        model_key=queue.queue.model_key,
        market_type=queue.queue.market_type,
        gate_version=queue.queue.gate_version,
        data_source_version=data_source_version,
        entered=queue.counts.entered,
        settled=queue.counts.settled,
        clv_upper_bound=queue.clv.ci[1],
        max_drawdown=queue.drawdown.max_drawdown if counted else None,
        net_pnl=queue.roi.net_pnl if counted else None,
        pause_review_latched=pause_review_latched,
        auto_stop_latched=auto_stop_latched,
    )


@dataclass(frozen=True)
class PauseDecision:
    """One queue's admission recommendation (paper evidence only).

    ``action`` is one of CONTINUE / PAUSE_REVIEW / AUTO_STOP_RECOMMEND.
    ``review_reasons`` are the FX-17 reasons actually acted on (always
    empty for a DOMESTIC queue), ``stop_reasons`` the universal 40-entry
    rule reasons, ``fx17_suppressed_reasons`` the FX-17 reasons observed
    but not acted on because the queue is domestic, and
    ``keeps_latched_pause`` marks decisions that hold a previously
    written pause. ``pause_suggestion`` retains the reused
    ``evaluation.pause_recommendation`` output as evidence.
    """

    queue_id: str
    league_scope: str
    model_key: str
    market_type: str
    gate_version: str
    data_source_version: str
    action: str
    review_reasons: tuple[str, ...]
    stop_reasons: tuple[str, ...]
    fx17_suppressed_reasons: tuple[str, ...]
    keeps_latched_pause: bool
    pause_suggestion: evaluation.PauseSuggestion
    rules_version: str = QUEUE_POLICY_RULES_VERSION
    suspension_rules_version: str = evaluation.SUSPENSION_RULES_VERSION
    paper_only: bool = True
    report_only: bool = True
    readonly: bool = True
    note: str = (
        "pause means review is required, not that the strategy is proven "
        "invalid; enforcement, latching and resumption are B1 admission "
        "wiring, never this decision layer"
    )

    @property
    def paused(self) -> bool:
        """True whenever new entries must not be admitted."""
        return self.action != ACTION_CONTINUE


def _decide_one(queue: QueuePauseInput) -> PauseDecision:
    # The single threshold computation: reuse the M1 function, never a
    # second copy of the OD-G numbers.
    suggestion = evaluation.pause_recommendation(
        entered=queue.entered,
        clv_upper_bound=queue.clv_upper_bound,
        max_drawdown=queue.max_drawdown,
        settled=queue.settled,
        net_pnl=queue.net_pnl,
    )
    fx17_applies = queue.league_scope == SCOPE_CROSS_LEAGUE
    review_reasons = suggestion.fx17_reasons if fx17_applies else ()
    suppressed = () if fx17_applies else suggestion.fx17_reasons
    # Decisions only stay or escalate: a latched pause survives missing
    # data (all-UNKNOWN metrics) and metric rebounds alike.
    review = bool(review_reasons) or queue.pause_review_latched
    stop = bool(suggestion.reasons) or queue.auto_stop_latched
    if stop:
        action = ACTION_AUTO_STOP_RECOMMEND
    elif review:
        action = ACTION_PAUSE_REVIEW
    else:
        action = ACTION_CONTINUE
    keeps_latch = (
        (queue.pause_review_latched or queue.auto_stop_latched)
        and action != ACTION_CONTINUE
    )
    return PauseDecision(
        queue_id=queue.queue_id,
        league_scope=queue.league_scope,
        model_key=queue.model_key,
        market_type=queue.market_type,
        gate_version=queue.gate_version,
        data_source_version=queue.data_source_version,
        action=action,
        review_reasons=review_reasons,
        stop_reasons=suggestion.reasons,
        fx17_suppressed_reasons=suppressed,
        keeps_latched_pause=keeps_latch,
        pause_suggestion=suggestion,
    )


def evaluate_pause(queues: Iterable[QueuePauseInput]) -> list[PauseDecision]:
    """Evaluate the pause rules for every queue, independently.

    Pure computation: same inputs give the same decisions, in input
    order, with no IO of any kind. Each queue is judged only on its own
    metrics; one queue's pause never affects another.
    """
    decisions = [_decide_one(queue) for queue in queues]
    seen: set[str] = set()
    for decision in decisions:
        if decision.queue_id in seen:
            raise ValueError(
                f"duplicate queue_id: {decision.queue_id!r}"
            )
        seen.add(decision.queue_id)
    return decisions


@dataclass(frozen=True)
class KappaFreezeCheck:
    """Season kappa-table freeze verification (evidence retained).

    ``status`` is MATCH (the observed table equals the pre-season frozen
    digest; the input may be used), DRIFTED (the table changed
    mid-season; the input must be blocked and this record kept as
    evidence) or UNVERIFIED (a digest is missing, so equivalence cannot
    be proven; the input must be blocked too -- never silently accepted).
    """

    season: str
    frozen_sha256: str | None
    observed_sha256: str | None
    status: str

    @property
    def usable(self) -> bool:
        return self.status == KAPPA_FREEZE_MATCH

    @property
    def blocked_reason(self) -> str | None:
        if self.status == KAPPA_FREEZE_MATCH:
            return None
        if self.status == KAPPA_FREEZE_DRIFTED:
            return (
                f"kappa table drifted mid-season {self.season!r}: "
                "block this input, keep the freeze evidence, refreeze "
                "only before the next season"
            )
        return (
            f"kappa freeze for season {self.season!r} is unverified: "
            "block this input until the pre-season digest is on record"
        )


def check_kappa_freeze(
    season: str,
    frozen_sha256: str | None,
    observed_sha256: str | None,
) -> KappaFreezeCheck:
    """Compare the season's frozen kappa digest with the observed one.

    The freeze happens exactly once before each season; a mid-season
    change is never accepted here. A ``None`` digest on either side is
    UNVERIFIED (blocked), a mismatch is DRIFTED (blocked), and only an
    exact match is usable. Digests are opaque non-empty strings.
    """
    _require_identity(season, "season")
    for name, value in (
        ("frozen_sha256", frozen_sha256),
        ("observed_sha256", observed_sha256),
    ):
        if value is not None and (not isinstance(value, str) or not value):
            raise ValueError(f"{name} must be a non-empty string or None")
    if frozen_sha256 is None or observed_sha256 is None:
        status = KAPPA_FREEZE_UNVERIFIED
    elif frozen_sha256 == observed_sha256:
        status = KAPPA_FREEZE_MATCH
    else:
        status = KAPPA_FREEZE_DRIFTED
    return KappaFreezeCheck(
        season=season,
        frozen_sha256=frozen_sha256,
        observed_sha256=observed_sha256,
        status=status,
    )
