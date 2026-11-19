"""FX-11 paper-sim queue evaluation: pure metrics plus OD-G rule verdicts.

Single responsibility: turn already-collected paper evidence into per-queue
performance metrics, OD-G promotion-criterion verdicts (A-F, registered
2026-11-13) and pause/suspension SUGGESTIONS. Pure computation only: no I/O,
no database, no network, no order path; ``paper_only`` / ``report_only`` /
``readonly`` are structural here — this module cannot authorize anything.

A queue is one (model, market type, gate version) triple. Performance is
never merged across queues.

09 wiring points (M1 leaves them as this in-memory interface; no tables are
created here):

- ``EvaluationCandidate`` is the candidate/market natural-result row that
  nodes 09D/09S will feed from their ``candidates`` / ``market_outcomes`` /
  ``candidate_price_evidence`` tables: the decision-time model probability
  and market mid, the later close mark, the natural resolution and the
  actual paper entry (if any). ``candidates_from_ledger_rows`` is the
  read-only compatibility loader for the frozen 10-30 ledger snapshot and
  is NOT the future production path.
- The ``outcome`` field carries market-level natural results
  (``YES``/``NO``/``YES-basket``), the post-audit ``win``/``loss`` labels,
  ``EXITED``, ``VOIDED`` or ``None`` (unknown). ``EXITED`` is never treated
  as a natural result: exits realize PnL for ROI but are excluded from
  forecast scoring and hit counts. ``natural_outcome`` (M1 final review
  P2-01) is the independent market-level natural result a settled trade
  row keeps for whole-market scoring while its ``outcome`` carries the
  trade-side label (``win``/``loss``/``EXITED``/``VOIDED``) from the
  settlement ledger; scoring accepts either field, trade PnL never does.

Scoring reuse (no fifth implementation): Brier, log loss, infinite-loss
status and calibration come from
``polymarket_alpha_lab.research_probability_scores`` — the shared Decimal
scoring module. Floats are converted to bounded ``Decimal`` probabilities
at this module's entry (quantized to that module's ``QUANTUM`` with its
``ROUND_HALF_EVEN``). Probabilities are never clipped: a wrong certain
forecast surfaces as an explicit infinite log-loss count/status, and any
market pair containing an infinite term keeps criterion D
NOT_ASSESSABLE instead of being averaged away.

Frozen algorithm decisions (``ALGORITHM_VERSION``; fixed before the
acceptance run, never tuned afterwards):

- dedup pairing: repeated observations of one ``market_key`` are reduced to
  one scoring pair — the earliest observation by (decision_time,
  candidate_id) supplies the probabilities and side, the earliest non-None
  natural outcome in the same order supplies the result;
- price direction: every probability (model, mid, fill, close) is
  side-aligned ``P(side)``; favorable movement is ``close - reference``;
- ROI denominator: the stake of resolved entries only (natural + win/loss
  labels + exits); VOIDED and unresolved rows are excluded and counted,
  never zero-filled;
- rounding: metrics are reported as floats converted from the Decimal
  scoring context at the last step; bootstrap percentiles use linear
  interpolation;
- bootstrap: fixed seed (20000 resamples), cluster resampling by the
  frozen key ``match_key`` if set else ``market_key`` — the finest
  correlation key the data model carries. Day-level aggregation of
  independent markets is deliberately NOT used: it would coarsen the
  historical regression interval beyond the published numbers.

The OD-G numbers below are the blueprint 5.3 values registered 2026-11-13.
Changing them is a frozen-threshold change requiring owner approval; in M1
the verdicts are rule determinations and pause SUGGESTIONS only — the
automatic suspension wiring belongs to M2/FX-17, and criterion F (30 days
unattended operation) is output as ``NOT_ASSESSABLE_M1`` because it needs
M2 run evidence that cannot exist yet.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from polymarket_alpha_lab.papersim import fees
from polymarket_alpha_lab.papersim import ledger
from polymarket_alpha_lab.research_probability_scores import (
    CONTEXT,
    QUANTUM,
    BinaryResearchObservation,
    ResearchProbabilityDiagnostics,
)

# ---------------------------------------------------------------------------
# Frozen versions and rule constants
# ---------------------------------------------------------------------------

ALGORITHM_VERSION = "fx11-evaluation-alg-v1"
PROMOTION_RULES_VERSION = "odg-registered-2026-11-13"
SUSPENSION_RULES_VERSION = "blueprint-pause+fx17-registered-2026-11-13"

BOOTSTRAP_SEED = 20261114
# 20000 resamples (the audit evidence run's precision) so the 2.5%/97.5%
# tail percentiles carry >= 500 draws each: with fewer draws the Monte
# Carlo noise alone can exceed the historical-regression acceptance
# tolerance (+/- 1.5 percentage points on the 10-30 snapshot) regardless
# of the resampling method.
BOOTSTRAP_RESAMPLES = 20000
BOOTSTRAP_PERCENTILES = (2.5, 97.5)
# Frozen bootstrap cluster key: match_key when set, else market_key (the
# mandatory per-market identity). Repeated observations of one match or
# one market are resampled together; distinct markets never merge into a
# day cluster.
CLUSTER_PRIORITY = ("match_key", "market_key")

PNL_BASIS_RECORDED = ledger.PNL_SOURCE_RECORDED
PNL_BASIS_RECOMPUTED = ledger.PNL_SOURCE_RECOMPUTED
_PNL_BASES = (PNL_BASIS_RECORDED, PNL_BASIS_RECOMPUTED)

FILL_BOOK_WALK = "book_walk"
FILL_RECORDED_PRICE = "recorded_price"
_FILL_PROVENANCES = (FILL_BOOK_WALK, FILL_RECORDED_PRICE)

# OD-G promotion thresholds (blueprint 5.3, registered 2026-11-13).
PROMOTION_MIN_CANDIDATES = 200
PROMOTION_MIN_ENTRIES = 50
PROMOTION_MIN_SETTLED = 30
PROMOTION_MIN_RUN_DAYS = 28
PROMOTION_CLV_MIN = 0.0  # CLV mean AND 95% lower bound strictly greater
PROMOTION_LOGLOSS_DIFF_UPPER_MAX = 0.010  # inclusive upper bound
PROMOTION_NET_ROI_MIN = 0.0  # strictly greater
PROMOTION_TOP_MARKET_SHARE_MAX = 0.30  # strictly lower
DRAWDOWN_UNIT_USD = 250.0
PROMOTION_MAX_DRAWDOWN_USD = 10.0 * DRAWDOWN_UNIT_USD

# Blueprint pause rule and FX-17 numbers (registered 2026-11-13).
PAUSE_AFTER_ENTRIES = 40
PAUSE_CLV_UPPER_BOUND_BELOW = 0.0  # strictly lower
PAUSE_DRAWDOWN_ABOVE = PROMOTION_MAX_DRAWDOWN_USD  # strictly higher
FX17_SETTLED_MIN = 10
FX17_NET_PNL_PAUSE_BELOW = -600.0  # strictly lower (4 units)
FX17_ENTRIES_MIN = 20
# FX-17 third clause — the kappa table is re-frozen exactly once before each
# season and never mid-season — is a policy statement about upstream inputs,
# not a computable queue metric; it is intentionally not evaluated here.

STATUS_OK = "OK"
STATUS_INSUFFICIENT = "INSUFFICIENT"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_UNDEFINED = "UNDEFINED"

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_NOT_ASSESSABLE = "NOT_ASSESSABLE"
VERDICT_NOT_ASSESSABLE_M1 = "NOT_ASSESSABLE_M1"

_MAX_DT = datetime.max.replace(tzinfo=timezone.utc)
_TRADE_NATURAL_OUTCOMES = (
    *ledger.NATURAL_OUTCOMES,
    *ledger.SETTLED_LABEL_OUTCOMES,
)
_COUNTED_OUTCOMES = (*_TRADE_NATURAL_OUTCOMES, ledger.EXITED_OUTCOME)
_ALLOWED_OUTCOMES = (*_COUNTED_OUTCOMES, ledger.VOIDED_OUTCOME, None)
_ALLOWED_SIDES = ("YES", "NO", "YES-basket", "NO-basket")


# ---------------------------------------------------------------------------
# Input record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationCandidate:
    """One scored candidate (accepted OR rejected) with its market evidence.

    09 wiring point: this is the in-memory shape of the 09D/09S
    candidate/market natural-result join — decision-time model probability
    and market mid, the close mark, the natural resolution and the actual
    paper entry where one exists. Rejected candidates keep ``side`` and the
    decision probabilities but MUST NOT carry entry fields (``stake``,
    ``fill_price``, ``fee_category``, ``fill_provenance``, ``recorded_pnl``):
    a rejection is never a trade and never a loss.
    """

    candidate_id: str
    model_key: str
    market_type: str
    gate_version: str
    market_key: str
    model_probability: float | None = None  # decision-time P(side)
    market_mid: float | None = None  # decision-time market implied P(side)
    accepted: bool = False
    stake: float | None = None
    fill_price: float | None = None  # average fill price of the entry
    side: str | None = None
    fee_category: str | None = None  # fees.taker_fee_rate category
    fill_provenance: str | None = None
    outcome: str | None = None
    natural_outcome: str | None = None  # market natural result, scoring-only
    recorded_pnl: float | None = None
    decision_time: datetime | None = None
    settled_time: datetime | None = None
    close_price: float | None = None  # later market mark, P(side)
    match_key: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "candidate_id", "model_key", "market_type", "gate_version",
            "market_key",
        ):
            value = getattr(self, name)
            if type(value) is not str or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if type(self.accepted) is not bool:
            raise ValueError("accepted must be a bool")
        _require_probability(
            self.model_probability, "model_probability", closed=True
        )
        _require_probability(self.market_mid, "market_mid", closed=True)
        _require_probability(self.close_price, "close_price", closed=True)
        if self.stake is not None:
            _require_finite(self.stake, "stake")
            if self.stake <= 0:
                raise ValueError("stake must be positive")
        if self.fill_price is not None and not 0.0 < self.fill_price < 1.0:
            raise ValueError("fill_price must be in the open interval (0, 1)")
        if self.recorded_pnl is not None:
            _require_finite(self.recorded_pnl, "recorded_pnl")
        if self.outcome not in _ALLOWED_OUTCOMES:
            raise ValueError(f"unknown resolution outcome: {self.outcome!r}")
        if self.natural_outcome is not None and (
            self.natural_outcome not in ledger.NATURAL_OUTCOMES
        ):
            raise ValueError(
                "natural_outcome must be one of "
                f"{ledger.NATURAL_OUTCOMES} or None; got "
                f"{self.natural_outcome!r}"
            )
        if self.side is not None and self.side not in _ALLOWED_SIDES:
            raise ValueError(f"unknown side: {self.side!r}")
        if self.outcome in ledger.NATURAL_OUTCOMES and self.side is None:
            raise ValueError(
                "a natural resolution requires a side to score against"
            )
        if self.fill_provenance is not None and (
            self.fill_provenance not in _FILL_PROVENANCES
        ):
            raise ValueError(
                f"fill_provenance must be one of {_FILL_PROVENANCES}"
            )
        for name in ("decision_time", "settled_time"):
            value = getattr(self, name)
            if value is not None and (
                type(value) is not datetime or value.tzinfo is None
            ):
                raise ValueError(f"{name} must be a timezone-aware datetime")
        if not self.accepted and any(
            getattr(self, name) is not None
            for name in (
                "stake", "fill_price", "fee_category", "fill_provenance",
                "recorded_pnl",
            )
        ):
            raise ValueError(
                "a rejected candidate must not carry entry fields "
                "(stake/fill_price/fee_category/fill_provenance/recorded_pnl)"
            )
        if self.accepted and self.stake is not None:
            if self.fill_price is None:
                raise ValueError("an entered candidate requires fill_price")
            if self.fee_category is None:
                raise ValueError("an entered candidate requires fee_category")
        if self.fee_category is not None:
            fees.taker_fee_rate(self.fee_category)  # validates the category
        if self.match_key is not None and not str(self.match_key).strip():
            raise ValueError("match_key must be a non-empty string when set")


def _require_finite(value: float, name: str) -> None:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")


def _require_probability(value: float | None, name: str, *,
                         closed: bool = False) -> None:
    if value is None:
        return
    _require_finite(value, name)
    if closed:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be in the [0, 1] interval")
    elif not 0.0 < value < 1.0:
        raise ValueError(f"{name} must be in the (0, 1) interval")


def natural_hit(side: str, outcome: str) -> bool:
    """Side-aligned hit test, mirroring ``ledger._natural_hit`` verbatim.

    That private helper mirrors the fixed ``forecast_calibration`` view
    expression (``YES``/``YES-basket`` hit when the outcome equals the
    side; ``NO``/``NO-basket`` hit on the natural ``NO``). This public
    mirror is pinned against the public ``ledger.settle_natural``
    arithmetic by tests (a hit is exactly a positive settlement PnL).
    """
    if side in ("YES", "YES-basket"):
        return outcome == side
    if side in ("NO", "NO-basket"):
        return outcome == "NO"
    raise ValueError(f"unknown side: {side!r}")


def _decimal_probability(value: float) -> Decimal:
    """Convert one float probability to the shared scoring module's Decimal.

    Entry-point conversion only (the scoring module owns the semantics):
    ``Decimal(str(value))`` avoids binary-float noise, then quantize to the
    module's ``QUANTUM`` (1e-12) with its ``ROUND_HALF_EVEN``. This never
    clips endpoints: 0 and 1 stay 0 and 1.
    """
    return Decimal(str(value)).quantize(QUANTUM, rounding=ROUND_HALF_EVEN)


# ---------------------------------------------------------------------------
# Output records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueueKey:
    model_key: str
    market_type: str
    gate_version: str


@dataclass(frozen=True)
class QueueCounts:
    candidates: int
    accepted: int
    rejected: int
    entered: int
    settled: int
    exited: int
    unresolved: int
    voided: int


@dataclass(frozen=True)
class RoiMetrics:
    counted: int
    stake: float
    gross_pnl: float
    entry_fees: float
    exit_fees: float
    net_pnl: float
    gross_roi: float | None
    net_roi: float | None
    gross_roi_ci: tuple[float | None, float | None]
    net_roi_ci: tuple[float | None, float | None]
    excluding_top_market_roi: float | None
    status: str  # OK when counted rows exist, INSUFFICIENT otherwise
    fee_schedule_as_of: str


@dataclass(frozen=True)
class FillMetrics:
    natural_settled: int
    hits: int
    hit_rate: float | None
    avg_fill_price: float | None
    status: str


@dataclass(frozen=True)
class ClvMetrics:
    entries_eligible: int
    entries_with_close: int
    mean: float | None
    ci: tuple[float | None, float | None]
    status: str


@dataclass(frozen=True)
class MoveMetrics:
    coverage: int
    mean: float | None
    status: str


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    count: int
    mean_probability_yes: float | None
    observed_yes_rate: float | None
    sparse: bool


@dataclass(frozen=True)
class MarketScoring:
    markets_total: int
    markets_scored: int
    missing_outcome: int
    missing_model_probability: int
    missing_market_mid: int
    missing_side: int
    deduped_repeats: int
    model_brier: float | None
    market_brier: float | None
    brier_diff_mean: float | None
    brier_diff_ci: tuple[float | None, float | None]
    model_log_loss: float | None
    market_log_loss: float | None
    model_log_loss_status: str
    market_log_loss_status: str
    infinite_log_loss_pairs: int
    logloss_diff_mean: float | None
    logloss_diff_ci: tuple[float | None, float | None]
    calibration_ece: float | None
    calibration_bins: tuple[CalibrationBin, ...]
    calibration_status: str


@dataclass(frozen=True)
class ConcentrationMetrics:
    """Profit concentration of one queue, gross AND net of fees.

    The gross fields keep their historical-report role (the frozen 10-30
    regression anchors: total 2552.43 / top 1305.20 / share 51.1%). The
    ``*net*`` fields (M1 final review P2-02) are the net-of-fees numbers
    the OD-G criterion E verdict reads: plan section 5 B-condition
    semantics make every PnL used for promotion net of fees.
    """

    total_pnl: float
    top_market_key: str | None
    top_market_pnl: float | None
    top_market_share: float | None  # None/UNDEFINED when total pnl <= 0
    net_total_pnl: float
    top_market_net_key: str | None
    top_market_net_pnl: float | None
    top_market_net_share: float | None  # None when net total pnl <= 0
    status: str


@dataclass(frozen=True)
class DrawdownMetrics:
    max_drawdown: float
    unit_usd: float
    limit_usd: float
    series_rows: int


@dataclass(frozen=True)
class CriterionResult:
    key: str  # "A" .. "F"
    verdict: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PauseSuggestion:
    stop_new_entries: bool
    reasons: tuple[str, ...]
    fx17_pause_review: bool
    fx17_reasons: tuple[str, ...]
    note: str = (
        "M1 suggestion output only: automatic suspension wiring is M2/FX-17"
    )


@dataclass(frozen=True)
class QueueEvaluation:
    queue: QueueKey
    counts: QueueCounts
    roi: RoiMetrics
    fills: FillMetrics
    clv: ClvMetrics
    unentered_moves: MoveMetrics
    scoring: MarketScoring
    concentration: ConcentrationMetrics
    drawdown: DrawdownMetrics
    promotion: tuple[CriterionResult, ...]
    pause: PauseSuggestion
    overall_verdict: str


@dataclass(frozen=True)
class EvaluationReport:
    queues: tuple[QueueEvaluation, ...]
    algorithm_version: str
    promotion_rules_version: str
    suspension_rules_version: str
    paper_only: bool = True
    report_only: bool = True
    readonly: bool = True


# ---------------------------------------------------------------------------
# OD-G criteria and pause rules (registered 2026-11-13 numbers)
# ---------------------------------------------------------------------------


def criterion_a(
    candidates: int, entered: int, settled: int, span_days: float | None,
) -> CriterionResult:
    """A sample: >=200 stored candidates (accepted plus rejected), >=50
    entries, >=30 settled (resolved entries: natural outcomes, win/loss
    labels and exits — the ledger's counted set), and a same-gate-version
    forward span of >= 4 weeks. Frozen span rule: 28 days measured across
    the queue's decision timestamps, falling back to settled timestamps
    when no decision times exist; unattended continuity itself is proven
    by criterion F (M2)."""
    failures = []
    if candidates < PROMOTION_MIN_CANDIDATES:
        failures.append(
            f"candidates {candidates} < {PROMOTION_MIN_CANDIDATES}"
        )
    if entered < PROMOTION_MIN_ENTRIES:
        failures.append(f"entries {entered} < {PROMOTION_MIN_ENTRIES}")
    if settled < PROMOTION_MIN_SETTLED:
        failures.append(f"settled {settled} < {PROMOTION_MIN_SETTLED}")
    if failures:
        return CriterionResult("A", VERDICT_FAIL, tuple(failures))
    if span_days is None:
        return CriterionResult(
            "A", VERDICT_NOT_ASSESSABLE,
            ("forward-run span not measurable (no decision timestamps)",),
        )
    if span_days < PROMOTION_MIN_RUN_DAYS:
        return CriterionResult(
            "A", VERDICT_FAIL,
            (f"same-gate forward span {span_days:.1f} days < "
             f"{PROMOTION_MIN_RUN_DAYS} days",),
        )
    return CriterionResult("A", VERDICT_PASS, ())


def criterion_b(
    entries: int,
    book_walk_entries: int,
    entries_with_fee_data: int,
    exited_entries: int,
    exited_with_fee_data: int,
) -> CriterionResult:
    """B cost: fills walked the book at the actual stake, every counted PnL
    is net of fees, and exits carry sell fees. This is a provenance
    standard: missing evidence is NOT_ASSESSABLE, never a silent PASS."""
    gaps = []
    if entries == 0:
        gaps.append("no entries to evidence cost handling")
    if book_walk_entries != entries:
        gaps.append(
            f"{entries - book_walk_entries} of {entries} entries lack "
            "book-walk fill provenance"
        )
    if entries_with_fee_data != entries:
        gaps.append(
            f"{entries - entries_with_fee_data} of {entries} entries lack "
            "fee data"
        )
    if exited_with_fee_data != exited_entries:
        gaps.append(
            f"{exited_entries - exited_with_fee_data} of {exited_entries} "
            "exits lack sell-fee data"
        )
    if gaps:
        return CriterionResult("B", VERDICT_NOT_ASSESSABLE, tuple(gaps))
    return CriterionResult("B", VERDICT_PASS, ())


def criterion_c(
    clv_mean: float | None,
    clv_lower_bound: float | None,
    entries_with_close: int,
) -> CriterionResult:
    """C lead indicator: closing-line value mean > 0 AND the 95% interval
    lower bound > 0 (both strict). No close coverage is NOT_ASSESSABLE —
    never zero-filled into a PASS."""
    if entries_with_close == 0 or clv_mean is None:
        return CriterionResult(
            "C", VERDICT_NOT_ASSESSABLE,
            ("no close marks: CLV not measurable (INSUFFICIENT coverage)",),
        )
    if clv_lower_bound is None:
        return CriterionResult(
            "C", VERDICT_NOT_ASSESSABLE, ("CLV interval not measurable",)
        )
    failures = []
    if clv_mean <= PROMOTION_CLV_MIN:
        failures.append(f"CLV mean {clv_mean:.4f} <= 0")
    if clv_lower_bound <= PROMOTION_CLV_MIN:
        failures.append(
            f"CLV 95% lower bound {clv_lower_bound:.4f} <= 0"
        )
    if failures:
        return CriterionResult("C", VERDICT_FAIL, tuple(failures))
    return CriterionResult("C", VERDICT_PASS, ())


def criterion_d(
    upper_bound: float | None, pairs: int, infinite_pairs: int,
) -> CriterionResult:
    """D market baseline: over ALL scanned markets (entries and rejections,
    deduplicated), the 95% upper bound of the paired log-loss difference
    (model minus market) must be <= +0.010 (inclusive)."""
    if pairs == 0:
        return CriterionResult(
            "D", VERDICT_NOT_ASSESSABLE,
            ("no market pairs with model probability, market mid and a "
             "natural result",),
        )
    if infinite_pairs:
        return CriterionResult(
            "D", VERDICT_NOT_ASSESSABLE,
            (f"{infinite_pairs} market pair(s) have an infinite log loss "
             "(unclipped certain forecast); the paired interval cannot be "
             "averaged",),
        )
    if upper_bound is None:
        return CriterionResult(
            "D", VERDICT_NOT_ASSESSABLE, ("log-loss interval not measurable",)
        )
    if upper_bound > PROMOTION_LOGLOSS_DIFF_UPPER_MAX:
        return CriterionResult(
            "D", VERDICT_FAIL,
            (f"paired log-loss difference 95% upper bound {upper_bound:.4f} "
             f"> +{PROMOTION_LOGLOSS_DIFF_UPPER_MAX:.3f}",),
        )
    return CriterionResult("D", VERDICT_PASS, ())


def criterion_e(
    net_roi: float | None,
    top_market_net_share: float | None,
    max_drawdown: float | None,
    stake: float,
) -> CriterionResult:
    """E outcome: net-of-fees ROI point estimate > 0 (strict), the most
    profitable single market < 30% of the queue's total NET-of-fees PnL
    (strict; UNDEFINED when the total net PnL is not positive), and max
    drawdown <= 10 standard units of $250 (inclusive).

    M1 final review P2-02: the concentration input is the net-of-fees
    share (``ConcentrationMetrics.top_market_net_share``) per the plan
    section 5 B-condition semantics — every PnL used for the promotion
    verdict is after fees. The gross share stays a reported historical
    metric only and must never gate this criterion."""
    if stake <= 0 or net_roi is None or max_drawdown is None:
        return CriterionResult(
            "E", VERDICT_NOT_ASSESSABLE,
            ("no resolved entries: outcome criteria not measurable",),
        )
    failures = []
    if net_roi <= PROMOTION_NET_ROI_MIN:
        failures.append(f"net ROI {net_roi:.4f} <= 0")
    if top_market_net_share is None:
        failures.append(
            "profit concentration undefined (total net pnl not positive)"
        )
    elif top_market_net_share >= PROMOTION_TOP_MARKET_SHARE_MAX:
        failures.append(
            f"top market {top_market_net_share:.1%} of total net pnl >= "
            f"{PROMOTION_TOP_MARKET_SHARE_MAX:.0%}"
        )
    if max_drawdown > PROMOTION_MAX_DRAWDOWN_USD:
        failures.append(
            f"max drawdown ${max_drawdown:.2f} exceeds "
            f"${PROMOTION_MAX_DRAWDOWN_USD:.2f}"
        )
    if failures:
        return CriterionResult("E", VERDICT_FAIL, tuple(failures))
    return CriterionResult("E", VERDICT_PASS, ())


def criterion_f() -> CriterionResult:
    """F operations: 30 days of unattended scheduled scans with no manual
    database edits, daily bankroll identity and per-row replay. Depends on
    M2 run evidence; M1 must never claim it."""
    return CriterionResult(
        "F", VERDICT_NOT_ASSESSABLE_M1,
        ("requires M2 unattended-run evidence: 30 days of scheduled scans, "
         "no manual DB edits, daily bankroll identity, per-candidate "
         "replay",),
    )


def overall_verdict(results: Iterable[CriterionResult]) -> str:
    """FAIL beats NOT_ASSESSABLE; a queue with A-E PASS is still
    NOT_ASSESSABLE_M1 because F needs M2 run evidence."""
    results = tuple(results)
    if any(r.verdict == VERDICT_FAIL for r in results):
        return VERDICT_FAIL
    if any(r.verdict == VERDICT_NOT_ASSESSABLE for r in results):
        return VERDICT_NOT_ASSESSABLE
    return VERDICT_NOT_ASSESSABLE_M1


def pause_recommendation(
    entered: int,
    clv_upper_bound: float | None,
    max_drawdown: float | None,
    settled: int,
    net_pnl: float | None,
) -> PauseSuggestion:
    """Blueprint pause rule + FX-17 review triggers (suggestions only).

    Blueprint: after >= 40 entries, a CLV 95% upper bound strictly below 0
    or a drawdown strictly above $2,500 suggests stopping new entries.
    FX-17: >= 10 settled with net PnL strictly below -$600, or >= 20
    entries with a CLV upper bound strictly below 0, suggests pausing for
    review. Unknown inputs never trigger a pause by themselves.
    """
    reasons = []
    if entered >= PAUSE_AFTER_ENTRIES:
        if clv_upper_bound is not None and (
            clv_upper_bound < PAUSE_CLV_UPPER_BOUND_BELOW
        ):
            reasons.append(
                f"{entered} entries with CLV 95% upper bound "
                f"{clv_upper_bound:.4f} < 0"
            )
        if max_drawdown is not None and (
            max_drawdown > PAUSE_DRAWDOWN_ABOVE
        ):
            reasons.append(
                f"{entered} entries with max drawdown ${max_drawdown:.2f} "
                f"> ${PAUSE_DRAWDOWN_ABOVE:.2f}"
            )
    fx17_reasons = []
    if settled >= FX17_SETTLED_MIN and net_pnl is not None and (
        net_pnl < FX17_NET_PNL_PAUSE_BELOW
    ):
        fx17_reasons.append(
            f"{settled} settled with net pnl ${net_pnl:.2f} < "
            f"${FX17_NET_PNL_PAUSE_BELOW:.2f}"
        )
    if entered >= FX17_ENTRIES_MIN and clv_upper_bound is not None and (
        clv_upper_bound < PAUSE_CLV_UPPER_BOUND_BELOW
    ):
        fx17_reasons.append(
            f"{entered} entries with CLV 95% upper bound "
            f"{clv_upper_bound:.4f} < 0"
        )
    return PauseSuggestion(
        stop_new_entries=bool(reasons),
        reasons=tuple(reasons),
        fx17_pause_review=bool(fx17_reasons),
        fx17_reasons=tuple(fx17_reasons),
    )


# ---------------------------------------------------------------------------
# Frozen bootstrap helpers
# ---------------------------------------------------------------------------


def _cluster_key(candidate: EvaluationCandidate) -> tuple[str, str]:
    """Frozen cluster key: ``match_key`` when set, else ``market_key``.

    Repeated observations of one match or one market are resampled
    together; distinct markets never merge into a day cluster (see
    ``CLUSTER_PRIORITY``).
    """
    if candidate.match_key:
        return ("match_key", candidate.match_key)
    return ("market_key", candidate.market_key)


def cluster_rows(
    candidates: Sequence[EvaluationCandidate],
) -> tuple[tuple[EvaluationCandidate, ...], ...]:
    """Group candidates into bootstrap clusters by the frozen cluster key;
    groups keep first-appearance order."""
    groups: dict[tuple[str, str], list[EvaluationCandidate]] = {}
    for candidate in candidates:
        groups.setdefault(_cluster_key(candidate), []).append(candidate)
    return tuple(tuple(group) for group in groups.values())


def _cluster_decimal_items(
    items: Sequence[tuple[EvaluationCandidate, Decimal]],
) -> list[list[float]]:
    """Align per-market Decimal diffs to the frozen clusters (by the
    pair representative's cluster key, first-appearance order)."""
    groups: dict[tuple[str, str], list[float]] = {}
    order: list[tuple[str, str]] = []
    for representative, value in items:
        key = _cluster_key(representative)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(float(value))
    return [groups[key] for key in order]


def _percentile(sorted_values: Sequence[float], percent: float) -> float:
    """Linear-interpolation percentile on pre-sorted values (frozen)."""
    if not sorted_values:
        raise ValueError("percentile of empty sequence")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (percent / 100.0) * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[int(position)]
    fraction = position - lower
    return (
        sorted_values[lower]
        + (sorted_values[upper] - sorted_values[lower]) * fraction
    )


def _bootstrap_ratio_ci(
    clusters: Sequence[Sequence[tuple[float, float]]],
) -> tuple[float | None, float | None]:
    """Cluster bootstrap of sum(pnl)/sum(stake) with the frozen seed."""
    if not clusters:
        return (None, None)
    rng = random.Random(BOOTSTRAP_SEED)
    draws: list[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        pnl = 0.0
        stake = 0.0
        for _ in clusters:
            for row_pnl, row_stake in clusters[rng.randrange(len(clusters))]:
                pnl += row_pnl
                stake += row_stake
        draws.append(pnl / stake if stake else 0.0)
    draws.sort()
    return (
        _percentile(draws, BOOTSTRAP_PERCENTILES[0]),
        _percentile(draws, BOOTSTRAP_PERCENTILES[1]),
    )


def _bootstrap_mean_ci(
    clusters: Sequence[Sequence[float]],
) -> tuple[float | None, float | None]:
    """Cluster bootstrap of a mean with the frozen seed."""
    if not clusters:
        return (None, None)
    rng = random.Random(BOOTSTRAP_SEED)
    draws: list[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        total = 0.0
        count = 0
        for _ in clusters:
            for value in clusters[rng.randrange(len(clusters))]:
                total += value
                count += 1
        draws.append(total / count if count else 0.0)
    draws.sort()
    return (
        _percentile(draws, BOOTSTRAP_PERCENTILES[0]),
        _percentile(draws, BOOTSTRAP_PERCENTILES[1]),
    )


# ---------------------------------------------------------------------------
# Queue evaluation
# ---------------------------------------------------------------------------


def _row_gross_pnl(
    candidate: EvaluationCandidate, pnl_basis: str
) -> float:
    outcome = candidate.outcome
    stake = float(candidate.stake)
    price = float(candidate.fill_price)
    if pnl_basis == PNL_BASIS_RECORDED or outcome == ledger.EXITED_OUTCOME:
        if candidate.recorded_pnl is None:
            raise ValueError(
                "recorded mode and EXITED rows require a recorded 'pnl'"
            )
        return float(candidate.recorded_pnl)
    if outcome in ledger.SETTLED_LABEL_OUTCOMES:
        return (stake / price - stake) if outcome == ledger.WIN_OUTCOME else -stake
    return ledger.settle_natural(
        stake, price, str(candidate.side), outcome
    ).pnl


@dataclass(frozen=True)
class _RoiRow:
    candidate: EvaluationCandidate
    gross: float
    entry_fee: float
    exit_fee: float

    @property
    def net(self) -> float:
        return self.gross - self.entry_fee - self.exit_fee


def _cluster_roi_rows(
    rows: Sequence[_RoiRow],
) -> list[list[_RoiRow]]:
    """Cluster ROI rows by the frozen cluster key of their candidates."""
    groups: dict[tuple[str, str], list[_RoiRow]] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        key = _cluster_key(row.candidate)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    return [groups[key] for key in order]


def _roi_metrics(
    counted: Sequence[EvaluationCandidate], pnl_basis: str,
) -> tuple[RoiMetrics, tuple[_RoiRow, ...]]:
    rows: list[_RoiRow] = []
    for candidate in counted:
        gross = _row_gross_pnl(candidate, pnl_basis)
        rate = fees.taker_fee_rate(candidate.fee_category)
        stake = float(candidate.stake)
        price = float(candidate.fill_price)
        entry_fee = fees.entry_fee(stake, price, rate)
        exit_fee = 0.0
        if candidate.outcome == ledger.EXITED_OUTCOME:
            shares = stake / price
            exit_price = (gross + stake) / shares
            exit_fee = fees.exit_fee(shares, exit_price, rate)
        rows.append(_RoiRow(candidate, gross, entry_fee, exit_fee))
    stake = sum(float(row.candidate.stake) for row in rows)
    gross = sum(row.gross for row in rows)
    entry_fees = sum(row.entry_fee for row in rows)
    exit_fees = sum(row.exit_fee for row in rows)
    net = sum(row.net for row in rows)
    status = STATUS_OK if rows else STATUS_INSUFFICIENT
    gross_roi = gross / stake if stake else None
    net_roi = net / stake if stake else None
    if rows:
        grouped = _cluster_roi_rows(rows)
        gross_clusters = [
            [(row.gross, float(row.candidate.stake)) for row in group]
            for group in grouped
        ]
        net_clusters = [
            [(row.net, float(row.candidate.stake)) for row in group]
            for group in grouped
        ]
        gross_ci = _bootstrap_ratio_ci(gross_clusters)
        net_ci = _bootstrap_ratio_ci(net_clusters)
    else:
        gross_ci = (None, None)
        net_ci = (None, None)
    excluding: float | None = None
    if rows and gross > 0:
        by_market: dict[str, list[_RoiRow]] = {}
        for row in rows:
            by_market.setdefault(row.candidate.market_key, []).append(row)
        top = max(by_market.values(), key=lambda group: sum(
            row.gross for row in group
        ))
        top_ids = {id(row) for row in top}
        excluded_rows = [row for row in rows if id(row) not in top_ids]
        excluded_stake = sum(
            float(row.candidate.stake) for row in excluded_rows
        )
        if excluded_stake > 0:
            excluding = sum(row.gross for row in excluded_rows) / excluded_stake
    return (
        RoiMetrics(
            counted=len(rows),
            stake=stake,
            gross_pnl=gross,
            entry_fees=entry_fees,
            exit_fees=exit_fees,
            net_pnl=net,
            gross_roi=gross_roi,
            net_roi=net_roi,
            gross_roi_ci=gross_ci,
            net_roi_ci=net_ci,
            excluding_top_market_roi=excluding,
            status=status,
            fee_schedule_as_of=fees.FEE_SCHEDULE_AS_OF,
        ),
        tuple(rows),
    )


def _market_scoring(
    candidates: Sequence[EvaluationCandidate],
) -> MarketScoring:
    groups: dict[str, list[EvaluationCandidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.market_key, []).append(candidate)
    pairs: list[tuple[EvaluationCandidate, str]] = []
    missing_outcome = 0
    missing_model = 0
    missing_mid = 0
    missing_side = 0
    for group in groups.values():
        ordered = sorted(group, key=_representative_order)
        representative = ordered[0]
        # The market's natural result: the earliest row exposing one,
        # either directly through its outcome or — for rows whose outcome
        # carries a trade label (win/loss/EXITED/VOIDED) — through the
        # independent natural_outcome field (M1 final review P2-01).
        outcome = next(
            (
                token
                for token in (
                    row.outcome if row.outcome in ledger.NATURAL_OUTCOMES
                    else row.natural_outcome
                    for row in ordered
                )
                if token is not None
            ),
            None,
        )
        if outcome is None:
            missing_outcome += 1
        elif representative.model_probability is None:
            missing_model += 1
        elif representative.market_mid is None:
            missing_mid += 1
        elif representative.side is None:
            missing_side += 1
        else:
            pairs.append((representative, outcome))
    markets_total = len(groups)
    model_obs = tuple(
        BinaryResearchObservation(
            _decimal_probability(representative.model_probability),
            natural_hit(representative.side, outcome),
        )
        for representative, outcome in pairs
    )
    market_obs = tuple(
        BinaryResearchObservation(
            _decimal_probability(representative.market_mid),
            natural_hit(representative.side, outcome),
        )
        for representative, outcome in pairs
    )
    model_diag = ResearchProbabilityDiagnostics(model_obs)
    market_diag = ResearchProbabilityDiagnostics(market_obs)
    brier_items: list[tuple[EvaluationCandidate, Decimal]] = []
    log_items: list[tuple[EvaluationCandidate, Decimal]] = []
    infinite_pairs = 0
    with localcontext(CONTEXT):
        for representative, outcome in pairs:
            hit = natural_hit(representative.side, outcome)
            p = _decimal_probability(representative.model_probability)
            q = _decimal_probability(representative.market_mid)
            y = Decimal(int(hit))
            brier_items.append(
                (representative, (p - y) ** 2 - (q - y) ** 2)
            )
            p_true = p if hit else Decimal(1) - p
            q_true = q if hit else Decimal(1) - q
            if p_true == 0 or q_true == 0:
                # Unclipped: an infinite term is counted and keeps the
                # paired interval undefined instead of being averaged in.
                infinite_pairs += 1
            else:
                log_items.append(
                    (representative, -p_true.ln() + q_true.ln())
                )
        brier_mean = (
            sum((value for _, value in brier_items), Decimal(0))
            / len(brier_items)
            if brier_items else None
        )
        log_mean = (
            sum((value for _, value in log_items), Decimal(0))
            / len(log_items)
            if log_items and not infinite_pairs else None
        )
    brier_ci = (
        _bootstrap_mean_ci(_cluster_decimal_items(brier_items))
        if brier_items else (None, None)
    )
    log_ci = (
        _bootstrap_mean_ci(_cluster_decimal_items(log_items))
        if log_items and not infinite_pairs else (None, None)
    )
    bins = tuple(
        CalibrationBin(
            lower=float(bin.lower),
            upper=float(bin.upper),
            count=bin.count,
            mean_probability_yes=(
                float(bin.mean_probability_yes)
                if bin.mean_probability_yes is not None else None
            ),
            observed_yes_rate=(
                float(bin.observed_yes_rate)
                if bin.observed_yes_rate is not None else None
            ),
            sparse=bin.sparse,
        )
        for bin in model_diag.bins
    )
    return MarketScoring(
        markets_total=markets_total,
        markets_scored=len(pairs),
        missing_outcome=missing_outcome,
        missing_model_probability=missing_model,
        missing_market_mid=missing_mid,
        missing_side=missing_side,
        deduped_repeats=len(candidates) - markets_total,
        model_brier=(
            float(model_diag.mean_brier_score)
            if model_diag.mean_brier_score is not None else None
        ),
        market_brier=(
            float(market_diag.mean_brier_score)
            if market_diag.mean_brier_score is not None else None
        ),
        brier_diff_mean=float(brier_mean) if brier_mean is not None else None,
        brier_diff_ci=brier_ci,
        model_log_loss=(
            float(model_diag.mean_log_loss)
            if model_diag.mean_log_loss is not None else None
        ),
        market_log_loss=(
            float(market_diag.mean_log_loss)
            if market_diag.mean_log_loss is not None else None
        ),
        model_log_loss_status=model_diag.log_loss_status,
        market_log_loss_status=market_diag.log_loss_status,
        infinite_log_loss_pairs=infinite_pairs,
        logloss_diff_mean=float(log_mean) if log_mean is not None else None,
        logloss_diff_ci=log_ci,
        calibration_ece=(
            float(model_diag.expected_calibration_error)
            if model_diag.expected_calibration_error is not None else None
        ),
        calibration_bins=bins,
        calibration_status=model_diag.sample_status,
    )


def _representative_order(candidate: EvaluationCandidate) -> tuple:
    return (
        candidate.decision_time
        if candidate.decision_time is not None
        else _MAX_DT,
        candidate.candidate_id,
    )


def _series_order(candidate: EvaluationCandidate) -> tuple:
    moment = (
        candidate.settled_time
        if candidate.settled_time is not None
        else candidate.decision_time
    )
    return (moment if moment is not None else _MAX_DT, candidate.candidate_id)


def _evaluate_queue(
    key: QueueKey,
    candidates: Sequence[EvaluationCandidate],
    pnl_basis: str,
) -> QueueEvaluation:
    entered = [
        c for c in candidates if c.accepted and c.stake is not None
    ]
    counted = [c for c in entered if c.outcome in _COUNTED_OUTCOMES]
    exited = [c for c in entered if c.outcome == ledger.EXITED_OUTCOME]
    counts = QueueCounts(
        candidates=len(candidates),
        accepted=sum(1 for c in candidates if c.accepted),
        rejected=sum(1 for c in candidates if not c.accepted),
        entered=len(entered),
        settled=len(counted),
        exited=len(exited),
        unresolved=sum(1 for c in candidates if c.outcome is None),
        voided=sum(1 for c in candidates if c.outcome == ledger.VOIDED_OUTCOME),
    )
    roi, roi_rows = _roi_metrics(counted, pnl_basis)

    natural = [c for c in counted if c.outcome in _TRADE_NATURAL_OUTCOMES]
    hits = sum(
        c.outcome == ledger.WIN_OUTCOME
        if c.outcome in ledger.SETTLED_LABEL_OUTCOMES
        else natural_hit(str(c.side), str(c.outcome))
        for c in natural
    )
    fills = FillMetrics(
        natural_settled=len(natural),
        hits=hits,
        hit_rate=(hits / len(natural)) if natural else None,
        avg_fill_price=(
            sum(float(c.fill_price) for c in counted) / len(counted)
            if counted else None
        ),
        status=STATUS_OK if counted else STATUS_INSUFFICIENT,
    )

    with_close = [
        c for c in entered
        if c.close_price is not None and c.fill_price is not None
    ]
    clv_values = [float(c.close_price) - float(c.fill_price) for c in with_close]
    if with_close:
        clv_clusters = [
            [float(c.close_price) - float(c.fill_price) for c in group]
            for group in cluster_rows(with_close)
        ]
        clv_ci = _bootstrap_mean_ci(clv_clusters)
        clv_mean = sum(clv_values) / len(clv_values)
        clv_status = STATUS_OK
    else:
        clv_ci = (None, None)
        clv_mean = None
        clv_status = STATUS_INSUFFICIENT
    clv = ClvMetrics(
        entries_eligible=len(entered),
        entries_with_close=len(with_close),
        mean=clv_mean,
        ci=clv_ci,
        status=clv_status,
    )

    unentered = [
        c for c in candidates
        if not c.accepted
        and c.market_mid is not None
        and c.close_price is not None
    ]
    move_values = [
        float(c.close_price) - float(c.market_mid) for c in unentered
    ]
    unentered_moves = MoveMetrics(
        coverage=len(unentered),
        mean=(sum(move_values) / len(move_values)) if move_values else None,
        status=STATUS_OK if move_values else STATUS_INSUFFICIENT,
    )

    scoring = _market_scoring(candidates)

    by_market: dict[str, list[_RoiRow]] = {}
    for row in roi_rows:
        by_market.setdefault(row.candidate.market_key, []).append(row)
    # Gross concentration stays the historical-report block (the 10-30
    # anchors: 51.1% top-market share). The OD-G criterion E verdict
    # reads the net-of-fees concentration (M1 final review P2-02): the
    # most profitable market by net PnL over the queue's total net PnL.
    total_pnl = sum(row.gross for row in roi_rows)
    if by_market and total_pnl > 0:
        top_key = max(
            by_market,
            key=lambda market: sum(row.gross for row in by_market[market]),
        )
        top_pnl = sum(row.gross for row in by_market[top_key])
        share = top_pnl / total_pnl
        concentration_status = STATUS_OK
    else:
        top_key = None
        top_pnl = None
        share = None
        concentration_status = (
            STATUS_UNDEFINED if roi_rows else STATUS_INSUFFICIENT
        )
    net_total_pnl = sum(row.net for row in roi_rows)
    if by_market and net_total_pnl > 0:
        net_top_key = max(
            by_market,
            key=lambda market: sum(row.net for row in by_market[market]),
        )
        net_top_pnl = sum(row.net for row in by_market[net_top_key])
        net_share = net_top_pnl / net_total_pnl
    else:
        net_top_key = None
        net_top_pnl = None
        net_share = None
    concentration = ConcentrationMetrics(
        total_pnl=total_pnl,
        top_market_key=top_key,
        top_market_pnl=top_pnl,
        top_market_share=share,
        net_total_pnl=net_total_pnl,
        top_market_net_key=net_top_key,
        top_market_net_pnl=net_top_pnl,
        top_market_net_share=net_share,
        status=concentration_status,
    )

    ordered_rows = sorted(roi_rows, key=lambda row: _series_order(row.candidate))
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for row in ordered_rows:
        cumulative += row.net
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    drawdown = DrawdownMetrics(
        max_drawdown=max_drawdown,
        unit_usd=DRAWDOWN_UNIT_USD,
        limit_usd=PROMOTION_MAX_DRAWDOWN_USD,
        series_rows=len(ordered_rows),
    )

    decision_times = [c.decision_time for c in candidates if c.decision_time]
    fallback_times = [c.settled_time for c in candidates if c.settled_time]
    span_source = decision_times or fallback_times
    span_days = (
        (max(span_source) - min(span_source)).total_seconds() / 86400.0
        if span_source else None
    )

    book_walk = sum(
        1 for c in entered if c.fill_provenance == FILL_BOOK_WALK
    )
    fee_data = sum(1 for c in entered if c.fee_category is not None)
    exited_fee_data = sum(1 for c in exited if c.fee_category is not None)
    promotion = (
        criterion_a(
            candidates=counts.candidates,
            entered=counts.entered,
            settled=counts.settled,
            span_days=span_days,
        ),
        criterion_b(
            entries=counts.entered,
            book_walk_entries=book_walk,
            entries_with_fee_data=fee_data,
            exited_entries=len(exited),
            exited_with_fee_data=exited_fee_data,
        ),
        criterion_c(
            clv_mean=clv.mean,
            clv_lower_bound=clv.ci[0],
            entries_with_close=clv.entries_with_close,
        ),
        criterion_d(
            upper_bound=scoring.logloss_diff_ci[1],
            pairs=scoring.markets_scored,
            infinite_pairs=scoring.infinite_log_loss_pairs,
        ),
        criterion_e(
            net_roi=roi.net_roi,
            top_market_net_share=concentration.top_market_net_share,
            max_drawdown=(
                drawdown.max_drawdown if roi_rows else None
            ),
            stake=roi.stake,
        ),
        criterion_f(),
    )
    pause = pause_recommendation(
        entered=counts.entered,
        clv_upper_bound=clv.ci[1],
        max_drawdown=drawdown.max_drawdown if roi_rows else None,
        settled=counts.settled,
        net_pnl=roi.net_pnl if roi_rows else None,
    )
    return QueueEvaluation(
        queue=key,
        counts=counts,
        roi=roi,
        fills=fills,
        clv=clv,
        unentered_moves=unentered_moves,
        scoring=scoring,
        concentration=concentration,
        drawdown=drawdown,
        promotion=promotion,
        pause=pause,
        overall_verdict=overall_verdict(promotion),
    )


def evaluate_queues(
    candidates: Iterable[EvaluationCandidate],
    *,
    pnl_basis: str = PNL_BASIS_RECORDED,
) -> EvaluationReport:
    """Evaluate every queue in ``candidates``.

    ``pnl_basis`` selects the gross-PnL basis exactly like
    ``ledger.queue_performance``: ``"recorded"`` (default; reproduces a
    book-of-record export as published) or ``"recomputed"`` (V-6 primary:
    natural resolutions and win/loss labels recomputed from first
    principles, exits keep their recorded PnL). Pure computation.
    """
    if pnl_basis not in _PNL_BASES:
        raise ValueError(f"pnl_basis must be one of {_PNL_BASES}")
    rows = tuple(candidates)
    seen: set[str] = set()
    for candidate in rows:
        if candidate.candidate_id in seen:
            raise ValueError(
                f"duplicate candidate_id: {candidate.candidate_id!r}"
            )
        seen.add(candidate.candidate_id)
    grouped: dict[QueueKey, list[EvaluationCandidate]] = {}
    for candidate in rows:
        key = QueueKey(
            candidate.model_key,
            candidate.market_type,
            candidate.gate_version,
        )
        grouped.setdefault(key, []).append(candidate)
    queues = tuple(
        _evaluate_queue(key, group, pnl_basis)
        for key, group in grouped.items()
    )
    return EvaluationReport(
        queues=queues,
        algorithm_version=ALGORITHM_VERSION,
        promotion_rules_version=PROMOTION_RULES_VERSION,
        suspension_rules_version=SUSPENSION_RULES_VERSION,
    )


# ---------------------------------------------------------------------------
# Read-only historical compatibility loader (10-30 snapshot)
# ---------------------------------------------------------------------------

_LEDGER_MODEL_KEY = "ledger-history"
_LEDGER_MARKET_TYPE = "ledger-history"
_LEDGER_GATE_VERSION = "unversioned-history"


def candidates_from_ledger_rows(
    rows: Iterable[Mapping[str, object]],
) -> tuple[EvaluationCandidate, ...]:
    """Map settled-ledger export rows to evaluation candidates (read-only).

    Compatibility loader for the frozen 10-30 snapshot
    (``docs/paper-sim/review-2026-10-30/settled_ledger.json``) and shape-
    compatible exports. Queue identity is the frozen historical single
    queue (``ledger-history``): the published evidence numbers are
    whole-book, not per-market-type. The row's ``question`` is the market
    key (legacy rows carry no market id). The entry fill price doubles as
    the market-implied proxy — that is the fixture's own label
    (``brier_entry_price``); legacy rows store no decision-time mid and no
    close marks, so CLV coverage is honestly zero. Fees classify through
    the scan team rule (:func:`fees.legacy_scan_fee_category`), and net
    figures remain schedule-back-computed estimates.
    """
    candidates: list[EvaluationCandidate] = []
    for index, row in enumerate(rows):
        row_id = row.get("id", index)
        question = row.get("question") or f"ledger-row-{row_id}"
        outcome = row.get("outcome")
        outcome_token = str(outcome).strip() if outcome is not None else None
        settled_at = row.get("settled_at")
        settled_time = (
            datetime.fromisoformat(str(settled_at))
            if settled_at is not None else None
        )
        candidates.append(
            EvaluationCandidate(
                candidate_id=f"ledger-{row_id}",
                model_key=_LEDGER_MODEL_KEY,
                market_type=_LEDGER_MARKET_TYPE,
                gate_version=_LEDGER_GATE_VERSION,
                market_key=str(question),
                model_probability=(
                    float(row["model_prediction"])
                    if row.get("model_prediction") is not None else None
                ),
                market_mid=(
                    float(row["price"])
                    if row.get("price") is not None else None
                ),
                accepted=True,
                stake=(
                    float(row["stake"])
                    if row.get("stake") is not None else None
                ),
                fill_price=(
                    float(row["price"])
                    if row.get("price") is not None else None
                ),
                side=str(row["side"]) if row.get("side") is not None else None,
                fee_category=fees.legacy_scan_fee_category(
                    str(row.get("team") or ""),
                    str(row.get("market_type") or "unknown"),
                ),
                fill_provenance=FILL_RECORDED_PRICE,
                outcome=outcome_token or None,
                recorded_pnl=(
                    float(row["pnl"]) if row.get("pnl") is not None else None
                ),
                decision_time=None,
                settled_time=settled_time,
                close_price=None,
                match_key=None,
            )
        )
    return tuple(candidates)
