"""MO-3 optimize scorecard: unified scoring and validation gates A-E.

Design 04 section 3 / section 8, plan 05 node MO-3. The scorecard is one
uniform summary of a spec over a batch of events: log loss, Brier,
calibration, paired differences (candidate vs incumbent, candidate vs
market), variant spread, refusals and the family's validation gates A-E.

Binary events use the convention ``probabilities = (P(NO), P(YES))`` and
``outcome_index in (0, 1)``; the calibration slope/intercept is fitted
only for binary events (logistic recalibration of P(YES)). Multi-class
events (arity 3) report the mean one-vs-rest expected calibration error
instead; their slope/intercept is None by definition, never a stretched
analogy.

Threshold policy (review P2): this module defines NO numeric threshold of
its own. Every gate threshold -- and every OD-G / pause number -- is
passed in by the caller; the OD-G promotion numbers live only in
``papersim/evaluation.py`` (single source), and the family validation-gate
numbers come from the published validation reports (football:
~/paper-sim/round39/eredivisie_validation.md mirroring
~/paper-sim/tools/fotmob_validate.py; international:
~/paper-sim/review/wp_intl_validation/report.md; NBA:
~/paper-sim/review/wp_nba_validation/report.md). OD-G verdicts are NOT
computed here at all -- the evaluation module (and later optimize/policy)
is the only judgement source; tests pin that this module's binary paired
numbers match ``evaluation._market_scoring`` exactly on identical inputs
so the two can never drift.

Serialization: ``canonical_dict`` is fully deterministic -- keys sorted,
floats via ``repr`` (shortest round-trip representation) -- and every
scorecard number is computed over events in canonical order, so equal
inputs produce byte-identical JSON (pinned by tests).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Callable

from polymarket_alpha_lab.papersim.optimize.metrics import (
    ScoredEvent,
    _SIGN_FLIP_REPS_DEFAULT,
    calibration_slope_intercept,
    cluster_mean_ci,
    favorite_flips,
    group_clusters,
    mean_absolute_deviation,
    mean_of,
    multiclass_brier,
    multiclass_log_loss,
    one_vs_rest_calibration,
    paired_diffs,
    sign_flip_pvalue,
)
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec

_SIGN_FLIP_SEED_DEFAULT = 20261119

__all__ = (
    "GateResult",
    "PairedSummary",
    "Scorecard",
    "VERDICT_FAIL",
    "VERDICT_NOT_ASSESSABLE",
    "VERDICT_PASS",
    "build_scorecard",
    "gate_abc_stat",
    "gate_d_walkforward",
    "gate_e_market",
    "gate_results",
)

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_NOT_ASSESSABLE = "NOT_ASSESSABLE"

STATUS_INSUFFICIENT = "insufficient_sample"
STATUS_DESCRIPTIVE = "descriptive_only"

MIN_SAMPLE_COUNT = 30


@dataclass(frozen=True, slots=True)
class GateResult:
    """One validation-gate verdict with its key numbers (strings, stable)."""

    gate: str  # "A" .. "E"
    check: str
    verdict: str
    numbers: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class PairedSummary:
    """Paired candidate-minus-comparator summary with clustered interval.

    ``mean`` is the plain float mean of per-event differences in the
    canonical (sorted) event order; ``ci95`` uses the production frozen
    cluster bootstrap (evaluation module conventions); ``p_value`` is the
    block sign-flip permutation test. Positive mean = candidate worse
    (design 04 section 6).
    """

    n_pairs: int
    n_clusters: int
    mean: float | None
    ci95: tuple[float | None, float | None]
    p_value: float | None
    status: str


def _canonical_events(
    events: Sequence[ScoredEvent],
) -> tuple[ScoredEvent, ...]:
    """Canonical order: (event_key, line_key) -- input order is erased."""
    return tuple(sorted(events, key=lambda row: (row.event_key, row.line_key)))


def _paired_summary(
    left: Sequence[ScoredEvent],
    right: Sequence[ScoredEvent],
    *,
    seed: int,
    reps: int,
    loss: Callable[[ScoredEvent], float],
) -> PairedSummary:
    diff = paired_diffs(
        _canonical_events(left),
        _canonical_events(right),
        lambda a, b: loss(a) - loss(b),
    )
    if not diff.pairs:
        return PairedSummary(0, 0, None, (None, None), None, STATUS_INSUFFICIENT)
    clusters = group_clusters(diff.pairs)
    block_sums = [sum(block) for block in clusters]
    return PairedSummary(
        n_pairs=len(diff.pairs),
        n_clusters=len(clusters),
        mean=mean_of([value for _, value in diff.pairs]),
        ci95=cluster_mean_ci(clusters),
        p_value=sign_flip_pvalue(block_sums, seed=seed, reps=reps),
        status=STATUS_DESCRIPTIVE,
    )


def _log_loss(row: ScoredEvent) -> float:
    # Scorecard convention: single-sided 1e-12 floor on the realized
    # outcome (the WP-P4 study convention). The production binary path
    # with NO floor (Decimal, infinite-status) is the evaluation module's
    # own scoring; the two conventions are documented side by side and
    # never mixed (metrics module docstring).
    return multiclass_log_loss(row.probabilities, row.outcome_index, floor=1e-12)


def _brier(row: ScoredEvent) -> float:
    return multiclass_brier(row.probabilities, row.outcome_index)


@dataclass(frozen=True, slots=True)
class Scorecard:
    """Uniform per-spec score summary (design 04 section 3)."""

    family_id: str
    spec_sha256: str
    model_version: str
    n_events: int
    log_loss: float | None
    brier: float | None
    calibration_ece: float | None
    calibration_slope: float | None
    calibration_intercept: float | None
    zero_probability_events: int
    argmax_hits: int
    paired_vs_incumbent: PairedSummary | None
    paired_vs_market: PairedSummary | None
    variant_spread_median: float | None
    refusals: int
    gates: tuple[GateResult, ...]
    sample_status: str

    def canonical_dict(self) -> dict:
        """Deterministic plain-Python dict (sorted keys, repr floats)."""

        def number(value: float | None) -> str | None:
            return None if value is None else repr(value)

        def paired(summary: PairedSummary | None) -> dict | None:
            if summary is None:
                return None
            return {
                "ci95": [number(bound) for bound in summary.ci95],
                "mean": number(summary.mean),
                "n_clusters": summary.n_clusters,
                "n_pairs": summary.n_pairs,
                "p_value": number(summary.p_value),
                "status": summary.status,
            }

        return {
            "argmax_hits": self.argmax_hits,
            "brier": number(self.brier),
            "calibration_ece": number(self.calibration_ece),
            "calibration_intercept": number(self.calibration_intercept),
            "calibration_slope": number(self.calibration_slope),
            "family_id": self.family_id,
            "gates": [
                {
                    "check": gate.check,
                    "gate": gate.gate,
                    "numbers": [[name, value] for name, value in gate.numbers],
                    "verdict": gate.verdict,
                }
                for gate in sorted(self.gates, key=lambda g: (g.gate, g.check))
            ],
            "log_loss": number(self.log_loss),
            "model_version": self.model_version,
            "n_events": self.n_events,
            "paired_vs_incumbent": paired(self.paired_vs_incumbent),
            "paired_vs_market": paired(self.paired_vs_market),
            "refusals": self.refusals,
            "sample_status": self.sample_status,
            "spec_sha256": self.spec_sha256,
            "variant_spread_median": number(self.variant_spread_median),
            "zero_probability_events": self.zero_probability_events,
        }


def build_scorecard(
    spec: ModelSpec,
    events: Sequence[ScoredEvent],
    *,
    incumbent_events: Sequence[ScoredEvent] | None = None,
    market_events: Sequence[ScoredEvent] | None = None,
    variant_spread_median: float | None = None,
    refusals: int = 0,
    gates: tuple[GateResult, ...] = (),
    sign_flip_seed: int = _SIGN_FLIP_SEED_DEFAULT,
    sign_flip_reps: int = _SIGN_FLIP_REPS_DEFAULT,
) -> Scorecard:
    """Build one scorecard from a spec's scored events.

    Events are put in canonical order first, so shuffling the input
    cannot change any number (pinned by tests). All events must share one
    arity: 2 (binary, ``(P(NO), P(YES))``) or 3 (win/draw/lose).
    """
    ordered = _canonical_events(events)
    n = len(ordered)
    arities = {len(row.probabilities) for row in ordered}
    if len(arities) > 1:
        raise ValueError("mixed-arity events cannot share one scorecard")
    log_loss = mean_of([_log_loss(row) for row in ordered]) if ordered else None
    brier = mean_of([_brier(row) for row in ordered]) if ordered else None
    zero_probability_events = sum(
        1 for row in ordered if row.probabilities[row.outcome_index] <= 0.0
    )
    argmax_hits = sum(
        1
        for row in ordered
        if max(range(len(row.probabilities)), key=lambda i: row.probabilities[i])
        == row.outcome_index
    )
    if not ordered:
        ece = slope = intercept = None
    else:
        classes = len(next(iter(ordered)).probabilities)
        if classes == 2:
            diagnostics = one_vs_rest_calibration(ordered, 1)
            ece = (
                float(diagnostics.expected_calibration_error)
                if diagnostics.expected_calibration_error is not None
                else None
            )
            slope, intercept = calibration_slope_intercept(
                [(row.probabilities[1], row.outcome_index == 1) for row in ordered]
            )
        else:
            eces = []
            for index in range(classes):
                diagnostics = one_vs_rest_calibration(ordered, index)
                if diagnostics.expected_calibration_error is not None:
                    eces.append(float(diagnostics.expected_calibration_error))
            ece = mean_of(eces) if eces else None
            slope = intercept = None
    paired_incumbent = (
        _paired_summary(
            ordered,
            incumbent_events,
            seed=sign_flip_seed,
            reps=sign_flip_reps,
            loss=_log_loss,
        )
        if incumbent_events is not None
        else None
    )
    paired_market = (
        _paired_summary(
            ordered,
            market_events,
            seed=sign_flip_seed,
            reps=sign_flip_reps,
            loss=_log_loss,
        )
        if market_events is not None
        else None
    )
    return Scorecard(
        family_id=spec.family,
        spec_sha256=spec.spec_sha256(),
        model_version=spec.model_version(),
        n_events=n,
        log_loss=log_loss,
        brier=brier,
        calibration_ece=ece,
        calibration_slope=slope,
        calibration_intercept=intercept,
        zero_probability_events=zero_probability_events,
        argmax_hits=argmax_hits,
        paired_vs_incumbent=paired_incumbent,
        paired_vs_market=paired_market,
        variant_spread_median=variant_spread_median,
        refusals=refusals,
        gates=gates,
        sample_status=(
            STATUS_INSUFFICIENT if n < MIN_SAMPLE_COUNT else STATUS_DESCRIPTIVE
        ),
    )


# ---------------------------------------------------------------------------
# Validation gates A-E (unified structure, caller-supplied thresholds)
# ---------------------------------------------------------------------------


def gate_abc_stat(
    gate: str,
    check: str,
    value: float | None,
    *,
    threshold: float,
    direction: str,
    denominator: float | None = None,
    min_denominator: float | None = None,
) -> GateResult:
    """Generic threshold verdict for pre-extracted statistics (gates A/B/C).

    ``direction="lte"``: PASS when value <= threshold; ``"gte"``: PASS
    when value >= threshold. A None value, or an insufficient denominator,
    yields NOT_ASSESSABLE -- never a fabricated number.
    """
    if direction not in ("lte", "gte"):
        raise ValueError(f"unknown direction: {direction!r}")
    insufficient = value is None or (
        denominator is not None
        and min_denominator is not None
        and denominator < min_denominator
    )
    numbers: list[tuple[str, str]] = []
    if denominator is not None:
        numbers.append(("denominator", repr(denominator)))
    if min_denominator is not None:
        numbers.append(("min_denominator", repr(min_denominator)))
    numbers.append(("threshold", repr(threshold)))
    numbers.append(("value", "None" if value is None else repr(value)))
    if insufficient:
        return GateResult(gate, check, VERDICT_NOT_ASSESSABLE, tuple(numbers))
    passed = value <= threshold if direction == "lte" else value >= threshold
    return GateResult(
        gate, check, VERDICT_PASS if passed else VERDICT_FAIL, tuple(numbers)
    )


def gate_d_walkforward(
    model_metrics: Mapping[str, float | None],
    baseline_metrics: Mapping[str, float | None],
    n_events: int,
    *,
    min_events: int,
    metrics: Sequence[str] = ("brier", "log_loss"),
) -> GateResult:
    """Gate D: the walk-forward model beats the baseline on EVERY metric.

    ``NOT_ASSESSABLE`` when ``n_events < min_events`` or any metric value
    is missing; the family's own min_events (90 football / 20 NBA / the
    international report's cohort) is caller-supplied.
    """
    if n_events < min_events:
        return GateResult(
            "D",
            "walk_forward_beats_baseline",
            VERDICT_NOT_ASSESSABLE,
            (("min_events", str(min_events)), ("n_events", str(n_events))),
        )
    missing = [
        name
        for name in metrics
        if model_metrics.get(name) is None or baseline_metrics.get(name) is None
    ]
    if missing:
        return GateResult(
            "D",
            "walk_forward_beats_baseline",
            VERDICT_NOT_ASSESSABLE,
            (("missing", ",".join(missing)),),
        )
    beats_all = all(
        model_metrics[name] < baseline_metrics[name]  # type: ignore[operator]
        for name in metrics
    )
    numbers = [("n_events", str(n_events))]
    for name in metrics:
        numbers.append((f"baseline_{name}", repr(baseline_metrics[name])))
        numbers.append((f"model_{name}", repr(model_metrics[name])))
    return GateResult(
        "D",
        "walk_forward_beats_baseline",
        VERDICT_PASS if beats_all else VERDICT_FAIL,
        tuple(numbers),
    )


def gate_e_market(
    model_vectors: Sequence[Sequence[float]],
    market_vectors: Sequence[Sequence[float]],
    *,
    mad_max: float,
    flips_max: int = 0,
    min_events: int,
) -> GateResult:
    """Gate E: mean absolute probability deviation vs the market, plus flips.

    MAD is taken over ALL probability components of all fixtures (the
    published convention: "mean abs deviation over 24 1X2 probs" for 8
    fixtures). ``NOT_ASSESSABLE`` when fewer than ``min_events`` fixtures
    carry a market quote (report the gap, never fabricate).
    """
    if len(model_vectors) != len(market_vectors):
        raise ValueError("model and market vectors must align")
    numbers: tuple[tuple[str, str], ...]
    if len(model_vectors) < min_events:
        numbers = (
            ("min_events", str(min_events)),
            ("n_events", str(len(model_vectors))),
        )
        return GateResult("E", "market_mad", VERDICT_NOT_ASSESSABLE, numbers)
    flat_model = [value for vector in model_vectors for value in vector]
    flat_market = [value for vector in market_vectors for value in vector]
    mad = mean_absolute_deviation(flat_model, flat_market)
    flips = favorite_flips(model_vectors, market_vectors)
    passed = mad <= mad_max and flips <= flips_max
    numbers = (
        ("flips", str(flips)),
        ("flips_max", str(flips_max)),
        ("mad", repr(mad)),
        ("mad_max", repr(mad_max)),
        ("n_events", str(len(model_vectors))),
    )
    return GateResult(
        "E", "market_mad", VERDICT_PASS if passed else VERDICT_FAIL, numbers
    )


def gate_results(
    abc: Sequence[GateResult],
    d: GateResult,
    e: GateResult,
) -> tuple[GateResult, ...]:
    """Assemble the family gate tuple in A..E order (A/B/C may be absent)."""
    combined = (*abc, d, e)
    return tuple(sorted(combined, key=lambda gate: (gate.gate, gate.check)))
