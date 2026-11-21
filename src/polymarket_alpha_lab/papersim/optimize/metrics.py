"""MO-3 optimize metrics kernel: scoring, paired intervals, calibration.

Design 04 section 8 / plan 05 node MO-3. Pure functions: standard library
plus the shared Decimal scoring module (:mod:`research_probability_scores`)
and the evaluation module's frozen cluster bootstrap (public since the
MO-3 extraction). No I/O, no network, no database, no clock reads.

Conventions kept explicit (review P2; never mix them silently):

Log-loss floors -- four published conventions, always explicit parameters
of value + side + which probability they act on:

1. production binary scoring (shared module + evaluation): Decimal, NO
   floor; a wrong certainty surfaces as an infinite-loss count/status;
2. WP-P4 (tools/wp_p4_walkforward.py, EPSILON = 1e-12): single-sided
   ``max(1e-12, p_realized)`` on the realized-outcome probability;
3. 2026-11-19 new-soccer (evidence/recompute_new_soccer.py): single-sided
   ``max(1e-15, p_realized)`` on the realized-outcome probability;
4. NBA (evidence/recompute_nba.py): two-sided clamp ``[1e-15, 1 - 1e-15]``
   applied to p before selecting the realized side.

Intervals -- two distinct functions, both frozen:

- production: :func:`cluster_mean_ci` / :func:`cluster_ratio_ci` forward to
  the evaluation module's frozen bootstrap (seed 20261114, 20000 resamples,
  linear-interpolation percentiles; cluster key = match_key else
  market_key, first-appearance order);
- study reproduction: :func:`study_block_interval` reproduces the WP-P4
  matchweek resampling verbatim (seed 20261107, 2000 resamples, INDEX
  percentiles ``lo = draws[int(.025 * (R - 1))]`` = ``draws[49]`` and
  ``hi = draws[int(.975 * (R - 1))]`` = ``draws[1949]`` at R=2000 --
  NOT symmetric: swapping the two index formulas shifts the fourth
  decimal; and the 2026-11-19 new-soccer reviewer script uses yet
  another pair of indices, so its interval column is NOT reproduced by
  this function).

Paired-difference call convention (review P2): ``left`` is always the
candidate/shadow, ``right`` the incumbent/comparator; the difference is
``left - right``, so a POSITIVE log-loss/Brier difference means the
candidate is WORSE (design 04 section 6: negative favours the candidate).

Float vs Decimal: the research-reproduction metrics (anchor numbers) are
float because the published numbers were computed in float; the
production binary paired path (:func:`production_binary_paired`) mirrors
the evaluation module's Decimal arithmetic line for line so the two can
never drift (pinned exactly by tests). The two are never mixed in one
number.
"""
from __future__ import annotations

import math
import random
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, localcontext
from typing import Callable

from polymarket_alpha_lab.papersim.evaluation import (
    cluster_bootstrap_mean_ci,
    cluster_bootstrap_ratio_ci,
)
from polymarket_alpha_lab.research_probability_scores import CONTEXT, QUANTUM

_SIGN_FLIP_REPS_DEFAULT = 2000
_HOLM_ALPHA_DEFAULT = 0.05

__all__ = (
    "NewSoccerCell",
    "NbaGroup",
    "PairedDiff",
    "ProductionPaired",
    "ScoredEvent",
    "StudyInterval",
    "binary_brier",
    "binary_log_loss",
    "binary_calibration",
    "calibration_slope_intercept",
    "cluster_mean_ci",
    "cluster_ratio_ci",
    "favorite_flips",
    "group_clusters",
    "holm",
    "mean_absolute_deviation",
    "mean_of",
    "multiclass_brier",
    "multiclass_log_loss",
    "nba_group_scores",
    "new_soccer_scores",
    "one_vs_rest_calibration",
    "paired_diffs",
    "production_binary_paired",
    "sign_flip_pvalue",
    "study_block_interval",
)

# ---------------------------------------------------------------------------
# Scalar scores (float, research-reproduction conventions)
# ---------------------------------------------------------------------------


def binary_log_loss(
    p: float, realized: bool, *, floor: float = 1e-15, clamp: str = "both"
) -> float:
    """Binary log loss under an explicit floor convention.

    ``clamp="both"``: clamp p into ``[floor, 1 - floor]`` first, then pick
    the realized side (NBA convention). ``clamp="realized"``: form the
    realized-side probability first and floor only that one (WP-P4 with
    floor=1e-12; new-soccer over/under with floor=1e-15).
    """
    if clamp == "both":
        q = min(max(p, floor), 1.0 - floor)
        return -math.log(q if realized else 1.0 - q)
    if clamp == "realized":
        q = p if realized else 1.0 - p
        return -math.log(max(floor, q))
    raise ValueError(f"unknown clamp convention: {clamp!r}")


def binary_brier(p: float, realized: bool) -> float:
    """Binary Brier score ``(p - y)**2``."""
    return (p - (1.0 if realized else 0.0)) ** 2


def multiclass_log_loss(
    probabilities: Sequence[float], outcome_index: int, *, floor: float
) -> float:
    """Multi-class log loss, single-sided floor on the realized outcome.

    ``-log(max(floor, probabilities[outcome_index]))`` -- the published
    convention of both the WP-P4 study (floor 1e-12) and the 2026-11-19
    new-soccer recompute (floor 1e-15). No two-sided clamp exists for the
    multi-class anchors; do not invent one.
    """
    if not 0 <= outcome_index < len(probabilities):
        raise ValueError("outcome_index out of range")
    return -math.log(max(floor, probabilities[outcome_index]))


def multiclass_brier(
    probabilities: Sequence[float], outcome_index: int
) -> float:
    """Multi-class Brier score in SUM form: ``sum((p_i - y_i)**2)``.

    Not divided by K: the three-class range is [0, 2], matching both
    published anchors (evidence/recompute_new_soccer.py and WP-P4
    score_forecast). Not comparable to binary Brier values.
    """
    if not 0 <= outcome_index < len(probabilities):
        raise ValueError("outcome_index out of range")
    return sum(
        (p - (1.0 if i == outcome_index else 0.0)) ** 2
        for i, p in enumerate(probabilities)
    )


def mean_of(values: Sequence[float]) -> float:
    """Plain ``sum(values) / len(values)`` in the given order.

    The evidence recompute scripts average exactly this way; do not swap
    in ``statistics.fmean`` (different rounding) on reproduction paths.
    """
    if not values:
        raise ValueError("mean of empty sequence")
    return sum(values) / len(values)


# ---------------------------------------------------------------------------
# Generic scored-event records and pairing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScoredEvent:
    """One model's forecast for one line of one event (match/expiry).

    ``cluster_key`` uses the evaluation module's frozen shape
    ``(kind, value)``; events sharing an ``event_key`` (same match, many
    lines) belong to one cluster.
    """

    event_key: str
    line_key: str
    cluster_key: tuple[str, str]
    probabilities: tuple[float, ...]
    outcome_index: int


@dataclass(frozen=True, slots=True)
class PairedDiff:
    """Per-line paired differences ``left - right`` in input order.

    Pairs are matched on ``(event_key, line_key)``; unpaired rows are
    counted, never zero-filled. ``left`` = candidate/shadow,
    ``right`` = incumbent/comparator (positive = candidate worse).
    """

    pairs: tuple[tuple[tuple[str, str], float], ...]
    unpaired_left: int
    unpaired_right: int


def paired_diffs(
    left: Sequence[ScoredEvent],
    right: Sequence[ScoredEvent],
    score: Callable[[ScoredEvent, ScoredEvent], float],
) -> PairedDiff:
    """Pair two scored-event sequences and apply ``score`` per pair."""
    right_index = {(row.event_key, row.line_key): row for row in right}
    seen_right: set[tuple[str, str]] = set()
    pairs: list[tuple[tuple[str, str], float]] = []
    unpaired_left = 0
    for row in left:
        match = right_index.get((row.event_key, row.line_key))
        if match is None:
            unpaired_left += 1
            continue
        seen_right.add((row.event_key, row.line_key))
        pairs.append((row.cluster_key, score(row, match)))
    return PairedDiff(
        pairs=tuple(pairs),
        unpaired_left=unpaired_left,
        unpaired_right=len(right) - len(seen_right),
    )


def group_clusters(
    pairs: Sequence[tuple[tuple[str, str], float]],
) -> list[list[float]]:
    """Group paired differences into clusters, first-appearance order.

    Same grouping discipline as ``evaluation.cluster_rows``.
    """
    groups: dict[tuple[str, str], list[float]] = {}
    for key, value in pairs:
        groups.setdefault(key, []).append(value)
    return [groups[key] for key in groups]


# ---------------------------------------------------------------------------
# Intervals
# ---------------------------------------------------------------------------


def cluster_mean_ci(
    clusters: Sequence[Sequence[float]],
) -> tuple[float | None, float | None]:
    """PRODUCTION interval: forward to the evaluation frozen bootstrap."""
    return cluster_bootstrap_mean_ci(clusters)


def cluster_ratio_ci(
    clusters: Sequence[Sequence[tuple[float, float]]],
) -> tuple[float | None, float | None]:
    """PRODUCTION ratio interval: forward to the evaluation bootstrap."""
    return cluster_bootstrap_ratio_ci(clusters)


@dataclass(frozen=True, slots=True)
class StudyInterval:
    """WP-P4-style matchweek block resampling result (study reproduction)."""

    mean: float
    ci95: tuple[float, float]
    n_items: int
    n_blocks: int
    reps: int


def study_block_interval(
    blocks: Mapping[str, Sequence[float]],
    *,
    seed: int = 20261107,
    reps: int = 2000,
) -> StudyInterval:
    """Reproduce the WP-P4 paired matchweek resampling verbatim.

    Mirrors ``tools/wp_p4_walkforward.paired_block_interval`` (2026-11-07):
    whole-block resampling of the per-fixture differences, ``statistics.fmean``
    for the point estimate, ``group = [blocks[k] for k in sorted(blocks)]``
    for the draw order, and INDEX percentiles -- at ``reps=2000``:
    ``lo = draws[int(.025 * (reps - 1))] = draws[49]`` and
    ``hi = draws[int(.975 * (reps - 1))] = draws[1949]`` (0.975 * 1999 =
    1949.025 -> 1949; the plan-review note that wrote 1948 was an
    arithmetic slip -- the evidence code and the reproduced anchor both
    give 1949). The two index formulas are NOT interchangeable (swapping
    them shifts the fourth decimal); the 2026-11-19 new-soccer reviewer
    interval uses different indices and is therefore NOT reproduced here.
    Study-reproduction only -- production intervals use :func:`cluster_mean_ci`.
    """
    items = [value for block in blocks.values() for value in block]
    if not items:
        raise ValueError("no paired observations")
    if len(blocks) < 2:
        raise ValueError("insufficient independent blocks")
    if reps < 100:
        raise ValueError("at least 100 bootstrap repetitions required")
    mean = statistics.fmean(items)
    rng = random.Random(seed)
    group = [blocks[key] for key in sorted(blocks)]
    draws: list[float] = []
    for _ in range(reps):
        selected = [group[rng.randrange(len(group))] for _ in group]
        draws.append(
            sum(sum(block) for block in selected)
            / sum(len(block) for block in selected)
        )
    draws.sort()
    lo = draws[int(0.025 * (reps - 1))]
    hi = draws[int(0.975 * (reps - 1))]
    return StudyInterval(
        mean=mean,
        ci95=(lo, hi),
        n_items=len(items),
        n_blocks=len(blocks),
        reps=reps,
    )


# ---------------------------------------------------------------------------
# Sign-flip permutation test and Holm correction
# ---------------------------------------------------------------------------


def sign_flip_pvalue(
    block_sums: Sequence[float],
    *,
    seed: int,
    reps: int = _SIGN_FLIP_REPS_DEFAULT,
) -> float:
    """Two-sided sign-flip permutation p-value on block sums.

    Statistic ``T = |sum(block_sums)|``; each of ``reps`` draws flips every
    block sign independently with probability 0.5; a draw is extreme when
    its statistic is ``>= T - 1e-15`` (tolerance pinned by the published
    evidence script). Returns ``(extreme + 1) / (reps + 1)`` (the ``+1``
    keeps p > 0 and admits the all-extreme lower bound ``(0 + 1)/(R + 1)``).
    """
    observed = abs(sum(block_sums))
    rng = random.Random(seed)
    extreme = 0
    for _ in range(reps):
        flipped = sum(
            value if rng.random() < 0.5 else -value for value in block_sums
        )
        if abs(flipped) >= observed - 1e-15:
            extreme += 1
    return (extreme + 1) / (reps + 1)


def holm(
    pvalues: Sequence[float], *, alpha: float = _HOLM_ALPHA_DEFAULT
) -> tuple[tuple[bool, ...], tuple[float, ...]]:
    """Holm step-down correction (FWER) over ``k = len(pvalues)`` p-values.

    Order by ``(p, original index)`` (stable). Walking up: reject while
    ``p_(i) <= alpha / (k - i + 1)`` (i counted from 1); the first failure
    stops the procedure -- that hypothesis and every later one are not
    rejected. Adjusted p-values are the monotone running maxima of
    ``(k - i + 1) * p_(i)`` capped at 1. Returns ``(rejected, adjusted)``
    both in the ORIGINAL input order.
    """
    n = len(pvalues)
    order = sorted(range(n), key=lambda i: (pvalues[i], i))
    rejected = [False] * n
    adjusted = [0.0] * n
    running = 0.0
    stopped = False
    for rank, index in enumerate(order):
        candidate = (n - rank) * pvalues[index]
        running = max(running, candidate)
        adjusted[index] = min(1.0, running)
        if stopped or pvalues[index] > alpha / (n - rank):
            stopped = True
        else:
            rejected[index] = True
    return tuple(rejected), tuple(adjusted)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def binary_calibration(
    observations: Sequence[tuple[Decimal, bool]],
    *,
    bucket_count: int = 10,
    min_sample_count: int = 30,
    min_bin_count: int = 5,
):
    """Production binary calibration: the shared Decimal diagnostics module.

    Returns ``ResearchProbabilityDiagnostics`` unchanged -- the single
    source of Brier/log-loss/calibration semantics for binary production
    scoring (same module the evaluation queue scoring uses).
    """
    from polymarket_alpha_lab.research_probability_scores import (
        BinaryResearchObservation,
        ResearchProbabilityDiagnostics,
    )

    return ResearchProbabilityDiagnostics(
        tuple(BinaryResearchObservation(p, y) for p, y in observations),
        bucket_count=bucket_count,
        min_sample_count=min_sample_count,
        min_bin_count=min_bin_count,
    )


def one_vs_rest_calibration(
    events: Sequence[ScoredEvent], index: int
):
    """One-vs-rest binary calibration of one class of a multi-class set."""
    observations = [
        (
            Decimal(str(min(max(row.probabilities[index], 0.0), 1.0))).quantize(
                QUANTUM, rounding=ROUND_HALF_EVEN
            ),
            row.outcome_index == index,
        )
        for row in events
    ]
    return binary_calibration(observations)


_CalibrationGroups = Sequence[tuple[float, int, int]]
_DecimalCalibrationGroups = Sequence[tuple[Decimal, int, int]]
_DecimalInterval = tuple[Decimal, Decimal]
_CALIBRATION_PRECISIONS = (34, 68, 136)  # 2, 4, 8 times binary64's 17 round-trip digits.


class _CalibrationBounds:
    """Outward-rounded Decimal enclosures for one fit comparison."""

    def __init__(self, precision: int) -> None:
        self.lower = Context(prec=precision, rounding=ROUND_FLOOR)
        self.upper = Context(prec=precision, rounding=ROUND_CEILING)

    def add(self, left: _DecimalInterval, right: _DecimalInterval) -> _DecimalInterval:
        return self.lower.add(left[0], right[0]), self.upper.add(left[1], right[1])

    def subtract(self, left: _DecimalInterval, right: _DecimalInterval) -> _DecimalInterval:
        return self.lower.subtract(left[0], right[1]), self.upper.subtract(left[1], right[0])

    def scale(self, value: _DecimalInterval, factor: Decimal) -> _DecimalInterval:
        lo, hi = value if factor >= 0 else value[::-1]
        return self.lower.multiply(lo, factor), self.upper.multiply(hi, factor)

    def multiply(self, left: _DecimalInterval, right: _DecimalInterval) -> _DecimalInterval:
        return (
            min(self.lower.multiply(a, b) for a in left for b in right),
            max(self.upper.multiply(a, b) for a in left for b in right),
        )

    def divide(self, numerator: _DecimalInterval, denominator: _DecimalInterval) -> _DecimalInterval:
        # The fitting equations only divide by positive probabilities or information.
        lo, hi = numerator
        return (
            self.lower.divide(lo, denominator[0] if lo < 0 else denominator[1]),
            self.upper.divide(hi, denominator[1] if hi < 0 else denominator[0]),
        )

    def total(self, terms: Iterable[_DecimalInterval]) -> _DecimalInterval:
        lo = hi = Decimal(0)
        for term_lo, term_hi in terms:
            lo = self.lower.add(lo, term_lo)
            hi = self.upper.add(hi, term_hi)
        return lo, hi

    def square(self, value: _DecimalInterval) -> _DecimalInterval:
        lo, hi = self.absolute(value)
        return self.lower.multiply(lo, lo), self.upper.multiply(hi, hi)

    @staticmethod
    def absolute(value: _DecimalInterval) -> _DecimalInterval:
        lo, hi = value
        if lo >= 0:
            return value
        if hi <= 0:
            return hi.copy_negate(), lo.copy_negate()
        return Decimal(0), max(lo.copy_negate(), hi)

    def exponential(self, value: _DecimalInterval) -> _DecimalInterval:
        # Decimal exp and ln round to nearest even. Take the adjacent Decimal
        # at each end before propagating bounds through the next operation.
        lo, hi = value
        return (
            max(Decimal(0), self.lower.next_minus(self.lower.exp(lo))) if lo else Decimal(1),
            self.upper.next_plus(self.upper.exp(hi)) if hi else Decimal(1),
        )

    def softplus(self, value: _DecimalInterval) -> _DecimalInterval:
        def endpoint(x: Decimal, ctx: Context, lower: bool) -> Decimal:
            tail = ctx.exp(x.copy_abs().copy_negate())
            if x:
                tail = max(Decimal(0), ctx.next_minus(tail)) if lower else ctx.next_plus(tail)
            log = ctx.ln(ctx.add(Decimal(1), tail))
            log = max(Decimal(0), ctx.next_minus(log)) if lower else ctx.next_plus(log)
            return ctx.add(max(Decimal(0), x), log)

        return endpoint(value[0], self.lower, True), endpoint(value[1], self.upper, False)


def _calibration_groups(observations: Sequence[tuple[float, bool]]) -> _CalibrationGroups:
    counts: dict[float, list[int]] = {}
    for probability, outcome in observations:
        probability = min(max(probability, 1e-6), 1.0 - 1e-6)
        logit = math.log(probability / (1.0 - probability))
        if not math.isfinite(logit):
            raise ValueError("calibration requires finite logits")
        counts.setdefault(logit, [0, 0])[int(bool(outcome))] += 1
    return [(x, no, yes) for x, (no, yes) in sorted(counts.items())]


def _calibration_newton_step(
    groups: _CalibrationGroups, point: tuple[float, float],
) -> tuple[float, float] | None:
    intercept, slope = point
    rows = []
    for x, no, yes in groups:
        eta = intercept + slope * x
        if not math.isfinite(eta):
            return None
        q = math.exp(-abs(eta))
        denominator = 1.0 + q
        if yes == no:
            residual = yes * math.expm1(-abs(eta)) / denominator
            if eta < 0:
                residual = -residual
        else:
            residual = (yes * q - no if eta >= 0 else yes - no * q) / denominator
        rows.append((x, residual, (no + yes) * q / (denominator * denominator)))

    # Work near a point carrying information so that closely spaced logits
    # retain their differences even when they are far from zero.
    origin = max(rows, key=lambda row: row[2])[0]
    shifted = [(x - origin, residual, weight) for x, residual, weight in rows]
    weight = math.fsum(w for _, _, w in shifted)
    if weight == 0:
        return None
    mean = math.fsum(x * w for x, _, w in shifted) / weight
    centered = [(x - mean, residual, w) for x, residual, w in shifted]
    information = math.fsum(w * x * x for x, _, w in centered)
    if information == 0:
        return None
    slope_step = math.fsum(x * r for x, r, _ in centered) / information
    intercept_step = math.fsum(r for _, r, _ in centered) / weight - (origin + mean) * slope_step
    if not math.isfinite(slope_step) or not math.isfinite(intercept_step):
        return None
    return intercept_step, slope_step


def _calibration_eta(
    bounds: _CalibrationBounds, point: tuple[_DecimalInterval, _DecimalInterval], x: Decimal,
) -> _DecimalInterval:
    intercept, slope = point
    return bounds.add(intercept, bounds.scale(slope, x))


def _calibration_decimal_point(point: tuple[float, float]) -> tuple[_DecimalInterval, _DecimalInterval]:
    intercept, slope = map(Decimal.from_float, point)
    return (intercept, intercept), (slope, slope)


def _calibration_tail(bounds: _CalibrationBounds, eta: _DecimalInterval) -> _DecimalInterval:
    return bounds.exponential(bounds.scale(bounds.absolute(eta), Decimal(-1)))


def _calibration_score(
    bounds: _CalibrationBounds, eta: _DecimalInterval, tail: _DecimalInterval,
    denominator: _DecimalInterval, no: int, yes: int,
) -> _DecimalInterval:
    no_count, yes_count = Decimal(no), Decimal(yes)
    numerators = []
    if eta[1] >= 0:
        numerators.append(bounds.subtract(bounds.scale(tail, yes_count), (no_count, no_count)))
    if eta[0] < 0:
        numerators.append(bounds.subtract((yes_count, yes_count), bounds.scale(tail, no_count)))
    # If the linear predictor interval crosses zero, enclose both forms.
    numerator = min(lo for lo, _ in numerators), max(hi for _, hi in numerators)
    return bounds.divide(numerator, denominator)


def _calibration_precise_step(
    groups: _DecimalCalibrationGroups, point: tuple[float, float], tolerance: float,
) -> tuple[bool, tuple[float, float] | None]:
    """Certify the original-coordinate full step, or recompute its direction."""
    decimal_point = _calibration_decimal_point(point)
    limit = Decimal.from_float(tolerance)
    origin = groups[len(groups) // 2][0]
    one = Decimal(1), Decimal(1)
    for precision in _CALIBRATION_PRECISIONS:
        bounds = _CalibrationBounds(precision)
        rows = []
        for x, no, yes in groups:
            eta = _calibration_eta(bounds, decimal_point, x)
            tail = _calibration_tail(bounds, eta)
            denominator = bounds.add(one, tail)
            score = _calibration_score(bounds, eta, tail, denominator, no, yes)
            weight = bounds.scale(bounds.divide(tail, bounds.square(denominator)), Decimal(no + yes))
            rows.append((bounds.subtract((x, x), (origin, origin)), score, weight))
        weight = bounds.total(w for _, _, w in rows)
        if weight[0] <= 0:
            continue
        mean = bounds.divide(bounds.total(bounds.multiply(x, w) for x, _, w in rows), weight)
        centered = [(bounds.subtract(x, mean), r, w) for x, r, w in rows]
        information = bounds.total(bounds.multiply(w, bounds.square(x)) for x, _, w in centered)
        if information[0] <= 0:
            continue
        slope = bounds.divide(bounds.total(bounds.multiply(x, r) for x, r, _ in centered), information)
        intercept = bounds.subtract(
            bounds.divide(bounds.total(r for _, r, _ in centered), weight),
            bounds.multiply(bounds.add((origin, origin), mean), slope),
        )
        magnitude = bounds.add(bounds.absolute(intercept), bounds.absolute(slope))
        if magnitude[1] <= limit:
            return True, None
        if magnitude[0] > limit:
            with localcontext(Context(prec=precision)):
                step = tuple(float((lo + hi) / 2) for lo, hi in (intercept, slope))
            if all(map(math.isfinite, step)):
                return False, (step[0], step[1])
    return False, None


def _calibration_float_loss(groups: _CalibrationGroups, point: tuple[float, float]) -> float:
    intercept, slope = point
    terms = []
    for x, no, yes in groups:
        eta = intercept + slope * x
        if not math.isfinite(eta):
            return math.inf
        softplus_tail = math.log1p(math.exp(-abs(eta)))
        terms.append((no + yes) * softplus_tail + (no if eta >= 0 else yes) * abs(eta))
    try:
        return math.fsum(terms)
    except OverflowError:
        return math.inf


def _calibration_loss_bounds(
    bounds: _CalibrationBounds, groups: _DecimalCalibrationGroups,
    point: tuple[_DecimalInterval, _DecimalInterval],
) -> _DecimalInterval:
    terms = []
    for x, no, yes in groups:
        eta = _calibration_eta(bounds, point, x)
        if no:
            terms.append(bounds.scale(bounds.softplus(eta), Decimal(no)))
        if yes:
            terms.append(bounds.scale(bounds.softplus(bounds.scale(eta, Decimal(-1))), Decimal(yes)))
    return bounds.total(terms)


def _calibration_taylor_upper(
    bounds: _CalibrationBounds, groups: _DecimalCalibrationGroups,
    current: tuple[_DecimalInterval, _DecimalInterval], trial: tuple[_DecimalInterval, _DecimalInterval],
) -> Decimal:
    change = bounds.subtract(trial[0], current[0]), bounds.subtract(trial[1], current[1])
    one = Decimal(1), Decimal(1)
    terms = []
    for x, no, yes in groups:
        eta = _calibration_eta(bounds, current, x)
        step = _calibration_eta(bounds, change, x)
        tail = _calibration_tail(bounds, eta)
        residual = _calibration_score(bounds, eta, tail, bounds.add(one, tail), no, yes)
        # L' = -score and L'' <= 1/4 in each observation's linear predictor.
        linear = bounds.multiply(bounds.scale(residual, Decimal(-1)), step)
        quadratic = bounds.scale(bounds.scale(bounds.square(step), Decimal(no + yes)), Decimal("0.125"))
        terms.append(bounds.add(linear, quadratic))
    return bounds.total(terms)[1]


def _calibration_descends(
    groups: _DecimalCalibrationGroups, point: tuple[float, float], candidate: tuple[float, float],
    *, taylor_first: bool,
) -> bool:
    current = _calibration_decimal_point(point)
    trial = _calibration_decimal_point(candidate)
    for precision in _CALIBRATION_PRECISIONS:
        bounds = _CalibrationBounds(precision)
        if taylor_first and _calibration_taylor_upper(bounds, groups, current, trial) < 0:
            return True
        difference = bounds.subtract(
            _calibration_loss_bounds(bounds, groups, trial),
            _calibration_loss_bounds(bounds, groups, current),
        )
        if difference[1] < 0:
            return True
        if difference[0] > 0:
            return False
        taylor_first = True
    return False


def _calibration_backtrack(
    groups: _CalibrationGroups, decimal_groups: _DecimalCalibrationGroups,
    point: tuple[float, float], direction: tuple[float, float],
) -> tuple[float, float] | None:
    loss = _calibration_float_loss(groups, point)
    scale = 1.0
    while scale:
        trial = point[0] + scale * direction[0], point[1] + scale * direction[1]
        if trial == point:
            break
        if all(map(math.isfinite, trial)):
            trial_loss = _calibration_float_loss(groups, trial)
            # Try the cheaper descent certificate unless the float estimate
            # predicts an increase beyond rounding. Decimal bounds decide.
            taylor_first = trial_loss - loss <= math.ulp(loss) + math.ulp(trial_loss)
            if _calibration_descends(decimal_groups, point, trial, taylor_first=taylor_first):
                return trial
        scale *= 0.5
    return None


def calibration_slope_intercept(
    observations: Sequence[tuple[float, bool]],
    *,
    iterations: int = 100,
    tolerance: float = 1e-10,
) -> tuple[float, float] | tuple[None, None]:
    """Fit the unpenalized binary logistic calibration and return ``(slope, intercept)``.

    Probabilities are clipped to ``[1e-6, 1 - 1e-6]`` before the logit.
    A single outcome class, constant clipped logits, or separation yields
    ``(None, None)`` because there is no finite unique two-parameter fit.
    ``iterations`` bounds accepted Newton updates. Convergence requires the
    full step's L1 norm in the original coefficients to be within ``tolerance``.
    A fit that cannot be certified within the budget raises ``ValueError``.
    """
    if not observations:
        raise ValueError("calibration of empty sequence")
    groups = _calibration_groups(observations)
    negatives = [x for x, no, _ in groups if no]
    positives = [x for x, _, yes in groups if yes]
    if not negatives or not positives or len(groups) == 1:
        return None, None
    if negatives[-1] <= positives[0] or positives[-1] <= negatives[0]:
        return None, None

    decimal_groups = [(Decimal.from_float(x), no, yes) for x, no, yes in groups]
    point = (0.0, 1.0)  # intercept, slope
    budget = max(0, iterations)
    for updates in range(budget + 1):
        step = _calibration_newton_step(groups, point)
        precise = step is None or abs(step[0]) + abs(step[1]) <= tolerance or updates == budget
        if precise:
            converged, step = _calibration_precise_step(decimal_groups, point, tolerance)
            if converged:
                return point[1], point[0]
        if step is None or updates == budget:
            break
        trial = _calibration_backtrack(groups, decimal_groups, point, step)
        if trial is None and not precise:
            converged, refined = _calibration_precise_step(decimal_groups, point, tolerance)
            if converged:
                return point[1], point[0]
            if refined is not None and refined != step:
                trial = _calibration_backtrack(groups, decimal_groups, point, refined)
        if trial is None:
            break
        point = trial
    raise ValueError("calibration fit did not converge")


# ---------------------------------------------------------------------------
# Market-comparison primitives (validation gate E)
# ---------------------------------------------------------------------------


def mean_absolute_deviation(
    model: Sequence[float], market: Sequence[float]
) -> float:
    """Mean absolute probability deviation over aligned components."""
    if len(model) != len(market):
        raise ValueError("model and market probability vectors must align")
    if not model:
        raise ValueError("deviation of empty sequence")
    return mean_of([abs(p - q) for p, q in zip(model, market)])


def favorite_flips(
    model_vectors: Sequence[Sequence[float]],
    market_vectors: Sequence[Sequence[float]],
) -> int:
    """Count fixtures whose argmax differs between model and market."""
    if len(model_vectors) != len(market_vectors):
        raise ValueError("model and market vectors must align")
    return sum(
        1
        for model, market in zip(model_vectors, market_vectors)
        if max(range(len(model)), key=lambda i: model[i])
        != max(range(len(market)), key=lambda i: market[i])
    )


# ---------------------------------------------------------------------------
# Production binary paired path (Decimal, mirrors evaluation._market_scoring)
# ---------------------------------------------------------------------------


def _decimal_probability(value: float) -> Decimal:
    """Identical to ``evaluation._decimal_probability`` (pinned by tests)."""
    return Decimal(str(value)).quantize(QUANTUM, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True, slots=True)
class ProductionPaired:
    """Decimal paired model-vs-comparator summary (production semantics).

    ``brier_mean`` / ``log_mean`` are model-minus-comparator differences;
    the log interval is ``(None, None)`` whenever any pair contains a
    zero realized probability (infinite term, never averaged away) --
    exactly the evaluation module's frozen semantics.
    """

    n_pairs: int
    brier_mean: float | None
    brier_ci: tuple[float | None, float | None]
    log_mean: float | None
    log_ci: tuple[float | None, float | None]
    infinite_pairs: int


def production_binary_paired(
    model_probabilities: Sequence[float],
    comparator_probabilities: Sequence[float],
    realized: Sequence[bool],
    cluster_keys: Sequence[tuple[str, str]],
) -> ProductionPaired:
    """Decimal paired differences, mirroring ``evaluation._market_scoring``.

    Same arithmetic, same order, same unclipped-infinite semantics as the
    evaluation module's queue scoring (source: evaluation.py
    ``_market_scoring``); exact equality is pinned by tests so the two
    implementations can never drift. Cluster order: first appearance.
    """
    n = len(model_probabilities)
    if not (len(comparator_probabilities) == len(realized) == len(cluster_keys) == n):
        raise ValueError("paired inputs must align")
    brier_items: list[tuple[tuple[str, str], Decimal]] = []
    log_items: list[tuple[tuple[str, str], Decimal]] = []
    infinite_pairs = 0
    with localcontext(CONTEXT):
        for p, q, hit, key in zip(
            model_probabilities, comparator_probabilities, realized, cluster_keys
        ):
            pd = _decimal_probability(p)
            qd = _decimal_probability(q)
            y = Decimal(int(hit))
            brier_items.append((key, (pd - y) ** 2 - (qd - y) ** 2))
            p_true = pd if hit else Decimal(1) - pd
            q_true = qd if hit else Decimal(1) - qd
            if p_true == 0 or q_true == 0:
                infinite_pairs += 1
            else:
                log_items.append((key, -p_true.ln() + q_true.ln()))
        brier_mean = (
            sum((value for _, value in brier_items), Decimal(0)) / len(brier_items)
            if brier_items
            else None
        )
        log_mean = (
            sum((value for _, value in log_items), Decimal(0)) / len(log_items)
            if log_items and not infinite_pairs
            else None
        )
    brier_ci = (
        cluster_bootstrap_mean_ci(
            _decimal_clusters([(k, v) for k, v in brier_items])
        )
        if brier_items
        else (None, None)
    )
    log_ci = (
        cluster_bootstrap_mean_ci(_decimal_clusters(log_items))
        if log_items and not infinite_pairs
        else (None, None)
    )
    return ProductionPaired(
        n_pairs=n,
        brier_mean=float(brier_mean) if brier_mean is not None else None,
        brier_ci=brier_ci,
        log_mean=float(log_mean) if log_mean is not None else None,
        log_ci=log_ci,
        infinite_pairs=infinite_pairs,
    )


def _decimal_clusters(
    items: Sequence[tuple[tuple[str, str], Decimal]],
) -> list[list[float]]:
    """Cluster Decimal diffs by first appearance (evaluation grouping)."""
    groups: dict[tuple[str, str], list[float]] = {}
    for key, value in items:
        groups.setdefault(key, []).append(float(value))
    return [groups[key] for key in groups]


# ---------------------------------------------------------------------------
# Anchor-reproduction entry points (float, published evidence conventions)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NewSoccerCell:
    """One (league, market) cell of the 2026-11-19 new-soccer recompute."""

    league: str
    market: str
    n_all: int
    n_match: int
    brier_baseline: float
    brier_tuned: float
    brier_market: float
    logloss_baseline: float | None
    logloss_tuned: float | None
    logloss_market: float | None

    @property
    def key(self) -> tuple[str, str]:
        return (self.league, self.market)


def _new_soccer_row_scores(
    row: Mapping[str, str], prefix: str
) -> tuple[float, float] | None:
    """(brier, logloss) for one model on one row, from probabilities only.

    Float convention of evidence/recompute_new_soccer.py: 1x2 is the
    three-class sum-form Brier and a single-sided 1e-15 log floor; ou is
    binary with the same single-sided floor.
    """

    def f(name: str) -> float | None:
        value = row.get(name, "")
        return float(value) if value not in ("", None) else None

    market = row["market"]
    result = row["result"]
    if market == "1x2":
        probs = [f(f"{prefix}_p_home"), f(f"{prefix}_p_draw"), f(f"{prefix}_p_away")]
        if any(p is None for p in probs):
            return None
        index = {"home": 0, "draw": 1, "away": 2}[result]
        brier = sum(
            (p - (1.0 if i == index else 0.0)) ** 2 for i, p in enumerate(probs)
        )
        return brier, -math.log(max(probs[index], 1e-15))
    p_over, p_under = f(f"{prefix}_p_over"), f(f"{prefix}_p_under")
    if p_over is None or p_under is None:
        return None
    hit = result == "over"
    return (
        (p_over - (1.0 if hit else 0.0)) ** 2,
        -math.log(max(p_over if hit else p_under, 1e-15)),
    )


def new_soccer_scores(
    rows: Iterable[Mapping[str, str]],
) -> dict[tuple[str, str], NewSoccerCell]:
    """Reproduce the 2026-11-19 new-soccer per-cell Brier/log-loss means.

    Input: ``csv.DictReader`` rows of ``new-soccer/holdout_predictions.csv``
    in file order. Grouping key ``(league, market)``; a row enters the
    paired cohort only when baseline, tuned AND market probabilities are
    all present. Averages use ``sum(...) / n`` over the file order
    (``mean_of``). The 30 published cells are pinned by tests against
    ``evidence/recompute_new_soccer_output.txt``.
    """
    cells: dict[tuple[str, str], list[Mapping[str, str]]] = {}
    for row in rows:
        cells.setdefault((row["league"], row["market"]), []).append(row)
    result: dict[tuple[str, str], NewSoccerCell] = {}
    for (league, market), cell_rows in sorted(cells.items()):
        matched: list[tuple[tuple[float, float], ...]] = []
        for row in cell_rows:
            scores = (
                _new_soccer_row_scores(row, "baseline"),
                _new_soccer_row_scores(row, "tuned"),
                _new_soccer_row_scores(row, "market"),
            )
            if all(score is not None for score in scores):
                matched.append(scores)  # type: ignore[arg-type]
        n = len(matched)
        if not n:
            raise ValueError(f"no market-matched rows in cell {league}/{market}")
        brier = [mean_of([row[i][0] for row in matched]) for i in (0, 1, 2)]
        logloss = [mean_of([row[i][1] for row in matched]) for i in (0, 1, 2)]
        result[(league, market)] = NewSoccerCell(
            league=league,
            market=market,
            n_all=len(cell_rows),
            n_match=n,
            brier_baseline=brier[0],
            brier_tuned=brier[1],
            brier_market=brier[2],
            logloss_baseline=logloss[0],
            logloss_tuned=logloss[1],
            logloss_market=logloss[2],
        )
    return result


@dataclass(frozen=True, slots=True)
class NbaGroup:
    """One (season, split, market, source) group of the NBA recompute."""

    season: str
    split: str
    market: str
    source: str
    rows: int
    paired: int
    logloss_baseline: float | None
    logloss_optimized: float | None
    logloss_market: float | None
    brier_baseline: float | None
    brier_optimized: float | None
    brier_market: float | None
    baseline_only_n: int
    baseline_only_logloss: float | None

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.season, self.split, self.market, self.source)


def _nba_row(
    row: Mapping[str, str],
) -> tuple[int, float | None, float | None, float | None] | None:
    outcome = row.get("outcome", "")
    if outcome in ("", None):
        return None

    def num(name: str) -> float | None:
        value = row.get(name, "")
        return float(value) if value not in ("", None) else None

    return (
        int(float(outcome)),
        num("p_baseline"),
        num("p_optimized"),
        num("p_market"),
    )


def nba_group_scores(
    rows: Iterable[Mapping[str, str]],
) -> dict[tuple[str, str, str, str], NbaGroup]:
    """Reproduce the NBA per-group log-loss/Brier means (2026-11-19 recompute).

    Input: ``csv.DictReader`` rows of ``nba/predictions.csv`` in file order.
    Grouping key ``(season, split, market, source)``; the paired cohort
    requires all three probabilities; the two-sided ``[1e-15, 1 - 1e-15]``
    clamp is the NBA convention. Groups without a full triple report the
    baseline-only log loss. Pinned by tests against
    ``evidence/recompute_nba_output.txt``.
    """

    def logloss(p: float, y: int) -> float:
        p = min(max(p, 1e-15), 1 - 1e-15)
        return -math.log(p if y == 1 else 1 - p)

    groups: dict[tuple[str, str, str, str], list[tuple[int, float | None, float | None, float | None]]] = {}
    for row in rows:
        parsed = _nba_row(row)
        if parsed is None:
            continue
        key = (row["season"], row["split"], row["market"], row["source"])
        groups.setdefault(key, []).append(parsed)
    result: dict[tuple[str, str, str, str], NbaGroup] = {}
    for key in sorted(groups):
        group_rows = groups[key]
        paired = [r for r in group_rows if r[1] is not None and r[2] is not None and r[3] is not None]
        base_only = [r for r in group_rows if r[1] is not None]
        if paired:
            n = len(paired)
            loglosses = [mean_of([logloss(r[i], r[0]) for r in paired]) for i in (1, 2, 3)]
            briers = [mean_of([(r[i] - r[0]) ** 2 for r in paired]) for i in (1, 2, 3)]
            result[key] = NbaGroup(
                season=key[0], split=key[1], market=key[2], source=key[3],
                rows=len(group_rows), paired=n,
                logloss_baseline=loglosses[0], logloss_optimized=loglosses[1],
                logloss_market=loglosses[2],
                brier_baseline=briers[0], brier_optimized=briers[1],
                brier_market=briers[2],
                baseline_only_n=0, baseline_only_logloss=None,
            )
        else:
            only = (
                mean_of([logloss(r[1], r[0]) for r in base_only])
                if base_only else None
            )
            result[key] = NbaGroup(
                season=key[0], split=key[1], market=key[2], source=key[3],
                rows=len(group_rows), paired=0,
                logloss_baseline=None, logloss_optimized=None,
                logloss_market=None,
                brier_baseline=None, brier_optimized=None, brier_market=None,
                baseline_only_n=len(base_only), baseline_only_logloss=only,
            )
    return result
