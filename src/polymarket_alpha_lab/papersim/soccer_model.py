"""10-variant xG-Poisson soccer match model.

Single responsibility: turn xG team ratings into per-market probabilities
and executable candidate quotes for a single fixture — O/U totals, BTTS,
team totals, 1X2 and spreads — across the ten shrinkage/rho variants, and
apply the cross-league guard. The model is deterministic and read-only.

Canonical lineage: ``r5c -> r15 -> r23`` (current head
``round23/raw/model23.py``). Policy carried from the head and from
``review/FIXES.md`` (2026-10-30):

- ``match_winner`` (1X2): domestic is eligible-deprioritized ($150
  marginal tier); cross-league became conditionally eligible with the
  WP-P5 two-season bridged walk-forward pass (2026-11-12, §75) — entry
  requires ``gates.check_cross_league_mw`` (bridge present AND
  worst-variant edge >= +5c) and sizes at a fixed $150 via
  ``risk_policy.CROSS_LEAGUE_ML_CLASS``; international 1X2 remains
  research-only (``intl_model.INTL_MW_RESEARCH_ONLY``).
- Spreads are flagged-only (blowout-tail rule).
- The cross-league guard ``lgh != lga -> None`` must never be relaxed by
  callers or bespoke scripts; the HOF-LYO favorite-flip defect came from
  exactly that relaxation (see ``tests/fixtures/papersim/
  regression_banned_path.json``). WP-P2a adds the one sanctioned
  cross-league path: an explicit league-strength κ ``bridge`` table
  (``polymarket_alpha_lab.papersim.league_strength``) threaded through
  ``fixture_variants`` / ``match_lambdas`` /
  ``candidate_from_variants`` into ``xg.pair_lambdas``; relaxation
  without a bridge remains banned; cross-league MW follows the
  conditional §75 path above (the away side carries the WP-P5 haircut).

Implementation notes (frozen arithmetic, do not tune):

- Score grid: independent Poisson PMFs on an 11x11 grid (0-10 goals per
  side). The Dixon-Coles tau multiplies ONLY the 0-0, 0-1, 1-0 and 1-1
  cells by ``(1 - lh*la*rho)``, ``(1 + lh*rho)``, ``(1 + la*rho)`` and
  ``(1 - rho)`` (each floored at ``DC_FLOOR``, the lineage safety clamp),
  then the grid renormalizes to sum 1. For independent Poisson cells the
  four tau deltas cancel exactly, so the correction preserves row and
  column marginals and total mass: team-total distributions are
  DC-invariant, and O/U probabilities for lines >= 2.0 are DC-invariant
  because every adjusted cell has total goals <= 2.
- Ten-variant sweep: :data:`SWEEPS` freezes the lineage set and order
  (blend w=.5 p=8 rho0 / DC, w=1.0, w=.25, p=16, p=4, current-only,
  last-only, cross-shrink .8 / 1.1). Rating modes and shrink exponents
  re-derive per-variant lambdas from the xG snapshot through
  :func:`polymarket_alpha_lab.papersim.xg.pair_lambdas`.
- Aggregation: median over the ten per-variant quotes (mean of the two
  middle order statistics for the even count), with min/max range.
- Candidate builders: :func:`candidate_from_variants` is the public,
  GATE-SAFE builder — it re-derives every variant's lambdas from the xG
  snapshot through :func:`fixture_variants`, so the candidate's fair
  range carries the true ten-variant uncertainty. :func:`over_under_quote`
  is the frozen pair-level stub: a bare lambda pair cannot re-derive the
  rating-blend / shrink dimensions, so its ten sweep slots coincide for
  O/U lines >= 2.0 — its range is NOT gate-safe evidence (codex review
  2026-10-30, finding 1; regression: BOU-LEE must gate-reject at 6.09c
  through the builder).
- ``over``/``under`` semantics follow the lineage push convention:
  ``over(line) = P(total >= floor(line) + 1)``.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Mapping

from polymarket_alpha_lab.papersim.xg import pair_lambdas

__all__ = [
    "CandidateEnvelope",
    "DC_FLOOR",
    "GRID_MAX_GOALS",
    "LINEAGE_EPS",
    "MatchOutputs",
    "RHO_DC",
    "SWEEPS",
    "SWEEP_NAMES",
    "SoccerCandidate",
    "VariantResult",
    "VariantSpec",
    "both_teams_to_score",
    "candidate_for_market",
    "candidate_from_variants",
    "cover_probability",
    "dixon_coles_tau",
    "fixture_variants",
    "match_lambdas",
    "median",
    "over_probability",
    "over_under_quote",
    "poisson_pmf",
    "score_grid",
    "team_total_over",
    "variant_outputs",
]

#: Dixon-Coles rho frozen across the lineage (scan15.py / model23.py /
#: model24.py all pin -0.1). Refitting rho from O/U outcomes was
#: rejected by review (zero sensitivity above the tau cells).
RHO_DC = -0.1

#: Score-grid goals per side: 0..10 inclusive (11x11 grid).
GRID_MAX_GOALS = 10

#: Lineage epsilon guarding float lines in floor() and cover comparisons.
LINEAGE_EPS = 1e-9

#: Safety clamp: tau never multiplies a cell below this weight.
DC_FLOOR = 0.01


@dataclass(frozen=True)
class VariantSpec:
    """One sweep variant: rating weights, rho and shrink exponent."""

    name: str
    last_weight: float | None
    prior_games: float | None
    rho: float
    mode: str
    shrink: float


#: The ten-variant sweep, frozen in lineage order (names are regression
#: keys compared against the round-23 archives). ``last_weight`` /
#: ``prior_games`` are ``None`` for the pure current/last modes, which
#: ignore blend weights. The shrink exponent applies to the attack /
#: defense multipliers; the lineage encoded it in the variant name
#: prefix and kept the same values.
SWEEPS: tuple[VariantSpec, ...] = (
    VariantSpec("blend w=.5 p=8 rho0", 0.5, 8.0, 0.0, "blend", 1.0),
    VariantSpec("blend w=.5 p=8 DC", 0.5, 8.0, RHO_DC, "blend", 1.0),
    VariantSpec("blend w=1.0 p=8", 1.0, 8.0, 0.0, "blend", 1.0),
    VariantSpec("blend w=.25 p=8", 0.25, 8.0, 0.0, "blend", 1.0),
    VariantSpec("blend w=.5 p=16", 0.5, 16.0, 0.0, "blend", 1.0),
    VariantSpec("blend w=.5 p=4", 0.5, 4.0, 0.0, "blend", 1.0),
    VariantSpec("current-only", None, None, 0.0, "cur", 1.0),
    VariantSpec("last-only", None, None, 0.0, "last", 1.0),
    VariantSpec("cross-shrink .8", 0.5, 8.0, 0.0, "blend", 0.8),
    VariantSpec("cross-shrink 1.1", 0.5, 8.0, 0.0, "blend", 1.1),
)

SWEEP_NAMES: tuple[str, ...] = tuple(spec.name for spec in SWEEPS)


@dataclass(frozen=True)
class SoccerCandidate:
    """One priced candidate quote for a single market of one fixture."""

    match: str
    question: str
    kind: str
    side: int
    entry_ask_vwap250: float
    model_median: float
    model_range: tuple[float, float]
    qs_variants: tuple[float, ...]
    lambda_home: float
    lambda_away: float
    market_id: str


@dataclass(frozen=True)
class MatchOutputs:
    """Per-market probability outputs for one variant's score grid.

    ``totals[k]`` is P(total goals = k) for k in 0..2*max_goals;
    ``home_goals`` / ``away_goals`` are the per-side goal distributions;
    ``margins[m + max_goals]`` is P(home goals - away goals = m) for m in
    -max_goals..max_goals. ``away_win = 1 - home_win - draw`` exactly as
    the lineage computed it.
    """

    home_win: float
    draw: float
    away_win: float
    btts: float
    totals: tuple[float, ...]
    home_goals: tuple[float, ...]
    away_goals: tuple[float, ...]
    margins: tuple[float, ...]


@dataclass(frozen=True)
class VariantResult:
    """One evaluated sweep variant for a single fixture."""

    spec: VariantSpec
    lambdas: tuple[float, float]
    outputs: MatchOutputs


def poisson_pmf(rate: float, goals: int) -> float:
    """Poisson probability mass ``exp(-rate) * rate**goals / goals!``."""
    return math.exp(-rate) * rate ** goals / math.factorial(goals)


def dixon_coles_tau(
    lh: float,
    la: float,
    rho: float,
) -> tuple[float, float, float, float]:
    """Dixon-Coles tau factors for the (0-0, 0-1, 1-0, 1-1) cells.

    Each factor is floored at ``DC_FLOOR`` (the lineage's ``max(..., .01)``
    safety clamp). With independent Poisson cells and the floors not
    binding, the induced cell corrections cancel: row marginals, column
    marginals and total mass are all preserved.
    """
    return (
        max(1.0 - lh * la * rho, DC_FLOOR),
        max(1.0 + lh * rho, DC_FLOOR),
        max(1.0 + la * rho, DC_FLOOR),
        max(1.0 - rho, DC_FLOOR),
    )


def score_grid(
    lh: float,
    la: float,
    *,
    rho: float = 0.0,
    max_goals: int = GRID_MAX_GOALS,
) -> tuple[tuple[float, ...], ...]:
    """Normalized Poisson score grid with optional Dixon-Coles tau.

    Grid size is ``(max_goals + 1) x (max_goals + 1)`` (11x11 at the
    frozen default). When ``rho`` is nonzero the tau factors apply ONLY
    to the 0-0 / 0-1 / 1-0 / 1-1 cells, then the whole grid is divided
    by its total mass.
    """
    size = max_goals + 1
    grid = [
        [poisson_pmf(lh, i) * poisson_pmf(la, j) for j in range(size)]
        for i in range(size)
    ]
    if rho:
        tau_00, tau_01, tau_10, tau_11 = dixon_coles_tau(lh, la, rho)
        grid[0][0] *= tau_00
        grid[0][1] *= tau_01
        grid[1][0] *= tau_10
        grid[1][1] *= tau_11
    total = sum(sum(row) for row in grid)
    return tuple(tuple(value / total for value in row) for row in grid)


def variant_outputs(
    lh: float,
    la: float,
    *,
    rho: float = 0.0,
    max_goals: int = GRID_MAX_GOALS,
) -> MatchOutputs:
    """Derive every per-market output for one variant's score grid."""
    grid = score_grid(lh, la, rho=rho, max_goals=max_goals)
    size = max_goals + 1
    home_win = sum(grid[i][j] for i in range(size) for j in range(size) if i > j)
    draw = sum(grid[i][i] for i in range(size))
    away_win = 1.0 - home_win - draw
    totals = [0.0] * (2 * max_goals + 1)
    home_goals = [0.0] * size
    away_goals = [0.0] * size
    margins = [0.0] * (2 * max_goals + 1)
    btts = 0.0
    for i in range(size):
        for j in range(size):
            p = grid[i][j]
            totals[i + j] += p
            if i >= 1 and j >= 1:
                btts += p
            home_goals[i] += p
            away_goals[j] += p
            margins[i - j + max_goals] += p
    return MatchOutputs(
        home_win=home_win,
        draw=draw,
        away_win=away_win,
        btts=btts,
        totals=tuple(totals),
        home_goals=tuple(home_goals),
        away_goals=tuple(away_goals),
        margins=tuple(margins),
    )


def _over_index(line: float) -> int:
    return int(math.floor(line + LINEAGE_EPS)) + 1


def over_probability(outputs: MatchOutputs, line: float) -> float:
    """P(total goals strictly above ``line``), push-excluded (lineage tail)."""
    need = _over_index(line)
    return sum(outputs.totals[k] for k in range(need, len(outputs.totals)))


def both_teams_to_score(outputs: MatchOutputs) -> float:
    """P(both teams score at least one goal)."""
    return outputs.btts


def team_total_over(
    outputs: MatchOutputs,
    side: str,
    line: float,
) -> float:
    """P(one team's goals strictly above ``line``); ``side`` is home/away."""
    if side not in ("home", "away"):
        raise ValueError(f"side must be 'home' or 'away', got {side!r}")
    goals = outputs.home_goals if side == "home" else outputs.away_goals
    need = _over_index(line)
    return sum(goals[k] for k in range(need, len(goals)))


def cover_probability(
    outputs: MatchOutputs,
    side: str,
    line: float,
) -> float:
    """P(``side`` covers a Polymarket spread ``line``), pushes excluded.

    For ``side='home'`` the margin distribution is used directly; for
    ``side='away'`` it is negated. Covering needs margin strictly above
    ``-line`` (the lineage's ``need = -line`` comparison).
    """
    if side not in ("home", "away"):
        raise ValueError(f"side must be 'home' or 'away', got {side!r}")
    max_goals = len(outputs.margins) // 2
    need = -line
    probability = 0.0
    for index, p in enumerate(outputs.margins):
        margin = index - max_goals
        if side == "away":
            margin = -margin
        if margin > need + LINEAGE_EPS:
            probability += p
    return probability


def median(values: Sequence[float]) -> float:
    """Median: middle order statistic, mean of the middle pair when even."""
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        raise ValueError("median of an empty sequence")
    if count % 2:
        return ordered[count // 2]
    return (ordered[count // 2 - 1] + ordered[count // 2]) / 2.0


def fixture_variants(
    xg_snapshot: Mapping[str, object],
    home_team: str,
    home_league: str,
    away_team: str,
    away_league: str,
    *,
    season: str | None = None,
    bridge: Mapping[str, float] | None = None,
) -> tuple[VariantResult, ...] | None:
    """Evaluate all ten sweep variants for one fixture.

    Returns ``None`` whenever :func:`polymarket_alpha_lab.papersim.xg.pair_lambdas`
    refuses the fixture (cross-league guard, missing season blocks or
    unknown teams); this is the snapshot-driven, gateable variance path.
    ``bridge`` is the optional league-strength κ table threaded into
    every variant's ``pair_lambdas`` call: without it the cross-league
    guard stands unchanged; with it the WP-P2a bridged cross-league
    path applies. Same-league fixtures are unaffected by ``bridge``.
    """
    results: list[VariantResult] = []
    for spec in SWEEPS:
        lambdas = pair_lambdas(
            xg_snapshot,
            home_team,
            home_league,
            away_team,
            away_league,
            last_weight=0.0 if spec.last_weight is None else spec.last_weight,
            prior_games=0.0 if spec.prior_games is None else spec.prior_games,
            mode=spec.mode,
            shrink=spec.shrink,
            season=season,
            bridge=bridge,
        )
        if lambdas is None:
            return None
        outputs = variant_outputs(lambdas[0], lambdas[1], rho=spec.rho)
        results.append(VariantResult(spec=spec, lambdas=lambdas, outputs=outputs))
    return tuple(results)


def match_lambdas(
    xg_snapshot: Mapping[str, object],
    home_team: str,
    home_league: str,
    away_team: str,
    away_league: str,
    *,
    bridge: Mapping[str, float] | None = None,
) -> tuple[float, float] | None:
    """Expected goal rates ``(lambda_home, lambda_away)`` for one fixture.

    Returns ``None`` when the two clubs' xG ratings come from different
    leagues (``home_league != away_league``): domestic multipliers are not
    cross-league comparable and combining them raw produced the confirmed
    HOF-LYO favorite-flip structural defect. This guard is mandatory and
    must not be relaxed — except through the sanctioned WP-P2a
    mechanism: pass an explicit league-strength κ ``bridge`` table and
    the bridged cross-league path of
    :func:`polymarket_alpha_lab.papersim.xg.pair_lambdas` applies
    (``None`` again when either league is missing from the bridge).
    Same-league fixtures never consult ``bridge``.

    Implementation: the per-side MEDIAN of the ten sweep variants' rates
    (the lineage's ``med([v['lh'] ...])`` / ``med([v['la'] ...])``).
    """
    variants = fixture_variants(
        xg_snapshot,
        home_team,
        home_league,
        away_team,
        away_league,
        bridge=bridge,
    )
    if variants is None:
        return None
    return (
        median([variant.lambdas[0] for variant in variants]),
        median([variant.lambdas[1] for variant in variants]),
    )


def candidate_from_variants(
    xg_snapshot: Mapping[str, object],
    home_team: str,
    home_league: str,
    away_team: str,
    away_league: str,
    line: float,
    market: str,
    market_id: str,
    entry_ask_vwap250: float,
    *,
    season: str | None = None,
    bridge: Mapping[str, float] | None = None,
) -> SoccerCandidate | None:
    """Snapshot-driven Over/Under ``line`` candidate quote — the public,
    GATE-SAFE builder.

    Evaluates all ten sweep variants through :func:`fixture_variants`
    (each variant re-derives its own lambdas from ``xg_snapshot``) and
    carries the TRUE per-variant quotes into the candidate's
    ``qs_variants`` / ``model_median`` / ``model_range``, so the variant
    spread and worst-variant edge seen by the entry gates reflect the
    full rating-blend / shrink / rho uncertainty. ``lambda_home`` /
    ``lambda_away`` are the per-side medians (identical to
    :func:`match_lambdas`).

    Returns ``None`` whenever the fixture is refused — cross-league
    guard, missing season blocks or unknown teams — exactly like
    :func:`fixture_variants` / :func:`match_lambdas`. The optional
    league-strength ``bridge`` κ table threads through to
    :func:`fixture_variants` (sanctioned WP-P2a cross-league path;
    same-league candidates are unaffected by it). Callers must NOT
    fall back to :func:`over_under_quote` on a ``None`` here: that stub
    collapses the sweep variance and would silently pass the variant
    gates (codex review 2026-10-30, finding 1).

    Frozen regression anchor (``tests/fixtures/papersim/
    regression_r23_soccer.json``): BOU-LEE O2.5 built here reproduces
    the true range 0.5714-0.6323 (spread 6.09c) and is the only
    documented workflow path allowed to face the gates.
    """
    variants = fixture_variants(
        xg_snapshot,
        home_team,
        home_league,
        away_team,
        away_league,
        season=season,
        bridge=bridge,
    )
    if variants is None:
        return None
    quotes = tuple(over_probability(variant.outputs, line) for variant in variants)
    return SoccerCandidate(
        match=market,
        question=f"{market}: O/U {line:g}",
        kind="total",
        side=0,
        entry_ask_vwap250=entry_ask_vwap250,
        model_median=median(quotes),
        model_range=(min(quotes), max(quotes)),
        qs_variants=quotes,
        lambda_home=median([variant.lambdas[0] for variant in variants]),
        lambda_away=median([variant.lambdas[1] for variant in variants]),
        market_id=market_id,
    )


def over_under_quote(
    lambdas: tuple[float, float],
    line: float,
    *,
    market: str,
    market_id: str,
    entry_ask_vwap250: float,
) -> SoccerCandidate:
    """Over/Under ``line`` candidate quote across the ten model variants.

    ``lambdas`` must come from :func:`match_lambdas` (same-league only).

    .. warning::

        Pair-level stub — NOT gate-safe. The rating-blend and shrink
        dimensions of the sweep vary team RATINGS, which cannot be
        re-derived from a bare lambda pair, so every variant slot here
        re-evaluates the SAME provided rates and only the rho dimension
        differentiates. For O/U lines >= 2.0 the Dixon-Coles tau is
        provably neutral, so all ten quotes coincide: the candidate's
        ``model_range`` collapses to a point and the variant-spread /
        worst-variant gates see fabricated certainty (a candidate truly
        rejected for var>6c — e.g. frozen BOU-LEE, true spread 6.09c —
        would pass the gates when built via this path). Use
        :func:`candidate_from_variants` on the xG snapshot for anything
        that faces the entry gates; this stub remains only for
        pair-level inspection of a single already-chosen lambda pair.
    """
    lh, la = lambdas
    quotes = tuple(
        over_probability(variant_outputs(lh, la, rho=spec.rho), line) for spec in SWEEPS
    )
    return SoccerCandidate(
        match=market,
        question=f"{market}: O/U {line:g}",
        kind="total",
        side=0,
        entry_ask_vwap250=entry_ask_vwap250,
        model_median=median(quotes),
        model_range=(min(quotes), max(quotes)),
        qs_variants=quotes,
        lambda_home=lh,
        lambda_away=la,
        market_id=market_id,
    )


# ---------------------------------------------------------------------------
# FX-10 (M1 plan §3; FX-10 node chain: DELIVERY_PLAN.md 78.1): generic candidate construction across market kinds.
#
# Everything above this banner is the frozen WP-P1 surface: the bytes of
# ``candidate_from_variants`` / ``fixture_variants`` / ``match_lambdas`` /
# ``over_under_quote`` and the ``SoccerCandidate`` dataclass stay frozen.
# The generic builder below only APPENDS to that surface: it re-derives the
# ten variants through the same frozen ``fixture_variants`` call and quotes
# every supported market per variant through the same frozen probability
# helpers, so ``kind="total", side="over"`` reproduces the legacy builder's
# candidate bit-for-bit while the remaining selections (O/U under, BTTS
# yes/no, team totals both directions, 1X2 per outcome yes/no, spreads)
# become constructible without touching any frozen function.


@dataclass(frozen=True)
class CandidateEnvelope:
    """FX-10 envelope: a frozen :class:`SoccerCandidate` plus context.

    ``SoccerCandidate``'s field contract is frozen (WP-P1), so the generic
    builder's extra context rides in this envelope rather than in new
    candidate fields:

    - ``kind`` / ``side`` / ``line`` / ``team_side`` echo the exact lineage
      selection vocabulary (the round-44 scan ``model_quote`` semantics):
      ``total`` with side ``over``/``under``, ``btts`` with ``yes``/``no``,
      ``team_total`` with ``over``/``under`` plus ``team_side``
      ``home``/``away``, ``match_winner`` with ``home``/``draw``/``away``
      or the ``*_no`` complements, and ``spread`` with ``cover``/``nocover``
      plus ``team_side``. Registry aliases (``ml``, ``1x2``, ``handicap``)
      are policy-layer lookups, not constructor inputs.
    - ``quote_source`` names the frozen probability source that produced
      the FORWARD per-variant quotes and ``complement`` records whether the
      quoted side is the exact ``1 - p`` reverse direction, binding each
      candidate to its actual computation source.
    - ``bridge_consulted`` is True only when the two leagues differ AND a
      bridge table was supplied — the only case in which
      :func:`fixture_variants` -> ``pair_lambdas`` actually consults it;
      ``bridge_source`` then keeps that exact table object for downstream
      provenance hashing (FX-08 strict crossbridge). Same-league
      constructions never consult a bridge and record ``None``.
    """

    candidate: SoccerCandidate
    kind: str
    side: str
    line: float | None
    team_side: str | None
    quote_source: str
    complement: bool
    bridge_consulted: bool
    bridge_source: Mapping[str, float] | None


#: Market kinds constructible by :func:`candidate_for_market` and their
#: valid side vocabulary (registry canonical names; the suspension-registry
#: aliases ``ml``/``1x2``/``handicap`` are accepted by
#: ``gates.suspension_policy`` but are not constructor inputs).
_GENERIC_SIDES: dict[str, tuple[str, ...]] = {
    "total": ("over", "under"),
    "btts": ("yes", "no"),
    "team_total": ("over", "under"),
    "match_winner": ("home", "draw", "away", "home_no", "draw_no", "away_no"),
    "spread": ("cover", "nocover"),
}

#: Constructible market kinds, in registry-canonical order.
_GENERIC_KINDS: tuple[str, ...] = tuple(_GENERIC_SIDES)

#: 1X2 outcome -> :class:`MatchOutputs` probability attribute.
_MATCH_WINNER_ATTRS = {"home": "home_win", "draw": "draw", "away": "away_win"}

#: Reverse-direction side ids (quoted as the exact ``1 - p`` complement).
_COMPLEMENT_SIDES = frozenset(
    {"under", "no", "home_no", "draw_no", "away_no", "nocover"}
)


def _half_ball_line(kind: str, line: object) -> float:
    """Validate and return a half-ball ``line`` for the line-taking kinds.

    Only half-ball lines are accepted (``2 * line`` an odd integer): an
    integer line carries a push outcome that the frozen helpers exclude,
    and a quarter line mixes two settlements — both are construction
    errors that must fail loudly (for FX-08/FX-09 to record), never be
    silently approximated. Non-finite (NaN/inf) and non-numeric values
    are rejected the same way.
    """
    if isinstance(line, bool) or not isinstance(line, (int, float)):
        raise ValueError(f"{kind} line must be a real number, got {line!r}")
    value = float(line)
    if not math.isfinite(value):
        raise ValueError(f"{kind} line must be finite, got {line!r}")
    doubled = value * 2.0
    nearest = round(doubled)
    if abs(doubled - nearest) > LINEAGE_EPS or int(nearest) % 2 == 0:
        raise ValueError(
            f"{kind} line must be a half-ball line (2*line an odd integer); "
            f"integer and quarter lines are not quotable: got {line!r}"
        )
    return value


def candidate_for_market(
    xg_snapshot: Mapping[str, object],
    home_team: str,
    home_league: str,
    away_team: str,
    away_league: str,
    market: str,
    market_id: str,
    entry_ask_vwap250: float,
    *,
    kind: str,
    side: str,
    line: float | None = None,
    team_side: str | None = None,
    season: str | None = None,
    bridge: Mapping[str, float] | None = None,
) -> CandidateEnvelope | None:
    """Snapshot-driven candidate quote for one side of any supported market.

    The FX-10 generic builder. Selects the market with the frozen lineage
    vocabulary — ``kind`` plus ``side`` (plus ``line`` / ``team_side``
    where the kind needs them) — and re-derives all ten sweep variants
    through the same :func:`fixture_variants` call the frozen
    :func:`candidate_from_variants` uses:

    ================ ============================= =========================
    kind             side                          forward per-variant source
    ================ ============================= =========================
    ``total``        ``over`` / ``under``          :func:`over_probability`
    ``btts``         ``yes`` / ``no``              :func:`both_teams_to_score`
    ``team_total``   ``over``/``under`` + side     :func:`team_total_over`
    ``match_winner`` ``home``/``draw``/``away``    ``MatchOutputs`` fields
                    (and the ``*_no`` complements)
    ``spread``       ``cover``/``nocover`` + side  :func:`cover_probability`
    ================ ============================= =========================

    - ``kind="total", side="over"`` reproduces :func:`candidate_from_variants`
      bit-for-bit (same variants, same per-variant calls, same aggregation),
      so the frozen regression anchors apply unchanged.
    - Reverse directions (``under``, ``no``, ``*_no``, ``nocover``) are the
      exact per-variant complement ``1 - p`` of the forward quote — free of
      residue because only half-ball lines are accepted (no pushes).
    - Lines must be half-ball (``2 * line`` an odd integer): integer lines
      carry pushes, quarter lines mix settlements, NaN/inf and non-numeric
      values are garbage — every one raises :class:`ValueError`.
      Construction failures are explicit exceptions so FX-08/FX-09 can
      record them; this builder NEVER falls back to the NOT-gate-safe
      :func:`over_under_quote` stub on any failure path.
    - ``match_winner`` medians are per-outcome medians over the ten variants
      and are NOT renormalized to sum to 1 — the existing aggregation the
      WP-P4 report observed at 0.988-1.005 across real fixtures; this
      builder keeps that convention unchanged.
    - ``spread`` envelopes are quote-only: spreads stay FLAGGED_ONLY in the
      gates suspension registry; an envelope is not entry authorization.
    - Returns ``None`` exactly when :func:`fixture_variants` refuses the
      fixture (banned cross-league pairing without a bridge, bridge missing
      a league, unknown teams / missing season blocks) — the same refusal
      semantics as the frozen builders.

    Extra construction context (selection, quote source, bridge provenance)
    rides in the returned :class:`CandidateEnvelope`; the inner
    :class:`SoccerCandidate` keeps the frozen WP-P1 field contract, with
    ``side`` the coarse direction code 0 = Over/Yes/cover forward and
    1 = the complement (the frozen fixture label ``"Over/Yes(side0)"``).
    """
    normalized_kind = str(kind)
    normalized_side = str(side)
    if normalized_kind not in _GENERIC_KINDS:
        raise ValueError(f"kind must be one of {_GENERIC_KINDS}, got {kind!r}")
    valid_sides = _GENERIC_SIDES[normalized_kind]
    if normalized_side not in valid_sides:
        raise ValueError(
            f"side for kind {normalized_kind!r} must be one of "
            f"{valid_sides}, got {side!r}"
        )
    if normalized_kind in ("total", "team_total", "spread"):
        if line is None:
            raise ValueError(f"kind {normalized_kind!r} requires a line")
        checked_line: float | None = _half_ball_line(normalized_kind, line)
    else:
        if line is not None:
            raise ValueError(
                f"kind {normalized_kind!r} takes no line, got {line!r}"
            )
        checked_line = None
    if normalized_kind in ("team_total", "spread"):
        if team_side not in ("home", "away"):
            raise ValueError(
                f"kind {normalized_kind!r} requires team_side 'home' or "
                f"'away', got {team_side!r}"
            )
        checked_team_side: str | None = team_side
    else:
        if team_side is not None:
            raise ValueError(
                f"kind {normalized_kind!r} takes no team_side, got {team_side!r}"
            )
        checked_team_side = None
    complement = normalized_side in _COMPLEMENT_SIDES

    forward_attr = ""
    if normalized_kind == "total":
        quote_source = "over_probability"
        question = f"{market}: O/U {checked_line:g}"
    elif normalized_kind == "btts":
        quote_source = "both_teams_to_score"
        question = f"{market}: BTTS"
    elif normalized_kind == "team_total":
        quote_source = "team_total_over"
        team = home_team if checked_team_side == "home" else away_team
        question = f"{market}: {team} O/U {checked_line:g}"
    elif normalized_kind == "match_winner":
        forward_attr = _MATCH_WINNER_ATTRS[normalized_side.removesuffix("_no")]
        quote_source = f"MatchOutputs.{forward_attr}"
        label = {
            "home": f"{home_team} to win",
            "home_no": f"{home_team} not to win",
            "draw": "draw",
            "draw_no": "no draw",
            "away": f"{away_team} to win",
            "away_no": f"{away_team} not to win",
        }[normalized_side]
        question = f"{market}: {label}"
    else:  # spread
        quote_source = "cover_probability"
        team = home_team if checked_team_side == "home" else away_team
        question = f"{market}: {team} {checked_line:+g}"

    variants = fixture_variants(
        xg_snapshot,
        home_team,
        home_league,
        away_team,
        away_league,
        season=season,
        bridge=bridge,
    )
    if variants is None:
        return None
    if normalized_kind == "total":
        forward = tuple(
            over_probability(variant.outputs, checked_line) for variant in variants
        )
    elif normalized_kind == "btts":
        forward = tuple(
            both_teams_to_score(variant.outputs) for variant in variants
        )
    elif normalized_kind == "team_total":
        forward = tuple(
            team_total_over(variant.outputs, checked_team_side, checked_line)
            for variant in variants
        )
    elif normalized_kind == "match_winner":
        forward = tuple(
            getattr(variant.outputs, forward_attr) for variant in variants
        )
    else:  # spread
        forward = tuple(
            cover_probability(variant.outputs, checked_team_side, checked_line)
            for variant in variants
        )
    quotes = forward if not complement else tuple(1.0 - quote for quote in forward)

    candidate = SoccerCandidate(
        match=market,
        question=question,
        kind=normalized_kind,
        side=1 if complement else 0,
        entry_ask_vwap250=entry_ask_vwap250,
        model_median=median(quotes),
        model_range=(min(quotes), max(quotes)),
        qs_variants=quotes,
        lambda_home=median([variant.lambdas[0] for variant in variants]),
        lambda_away=median([variant.lambdas[1] for variant in variants]),
        market_id=market_id,
    )
    cross_league = home_league != away_league
    return CandidateEnvelope(
        candidate=candidate,
        kind=normalized_kind,
        side=normalized_side,
        line=checked_line,
        team_side=checked_team_side,
        quote_source=quote_source,
        complement=complement,
        bridge_consulted=bool(cross_league and bridge is not None),
        bridge_source=bridge if cross_league else None,
    )
