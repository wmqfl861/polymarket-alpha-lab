"""International national-team Elo->Poisson match model (WP-INTL-2).

Single responsibility: turn an international-soccer snapshot (frozen input
contract below) into 1X2 / over-under / BTTS / exact-score probabilities for
one fixture across a nine-variant sensitivity envelope. Deterministic, pure,
no network, no I/O, paper-only/report-only/readonly: nothing here
authenticates an account, handles credentials, signs or submits anything.
International markets stay research-only until the round-47 validation gates
(walk-forward log-loss, market-MAD vs PM closings, live shadow cohort and an
independent review) pass.

Input contract (FROZEN — the sibling ``intl_data`` adapter builds this
schema; ``k_factor`` delegates its K classification to the adapter's
frozen ``tournament_k`` — the single K authority (2026-11-12 review)::

    {"generated_at": "<iso>",
     "teams": {"<name>": {"elo": <float>}},
     "matches": [{"date": "<YYYY-MM-DD>", "home": "<name>", "away": "<name>",
                  "home_goals": <int>, "away_goals": <int>,
                  "tournament": "<str>", "neutral": <bool>,
                  "venue": "<str>|null"}]}

Prediction input (frozen dataclass): ``IntlFixture(home, away, date,
neutral=False)``. The fixture's tournament context is NOT on the dataclass;
callers pass ``fixture_prediction(..., tournament="UEFA Nations League")``
to select the per-tournament mean-total calibration (see the total
expectation below).

Model (round-47 feasibility design, ``~/paper-sim/round47/
nl_elo_feasibility.md`` §4 and probe artifact ``elo_fit_out.txt``):

- Ratings layer: full-history Elo replay over ``matches`` — the
  eloratings.net scheme the round-47 probe validated at Spearman
  rho = 0.996 against the published table across 35 UEFA teams. Base
  1500, home advantage +100 when a match's ``neutral`` is false, K by
  competition tier (60 World Cup finals / 50 continental finals / 40
  Nations League, all qualifiers and the documented default / 20
  friendlies) multiplied by the goal-difference multiplier (1.0 for a
  draw or one-goal game, 1.5 for two, ``(11+N)/8`` for N >= 3), with
  the logistic expected score ``1 / (1 + 10**(-dr/400))``.
- Elo->goals bridge (probe fit over 2018-2026 Nations League cohorts):
  ``E[gd] = slope * (elo_diff + home_adv) / 100`` with
  :data:`DEFAULT_SLOPE` = 0.47 goals per 100 Elo. Probe provenance: the
  fit measured 0.473 (R^2 0.28) over all 762 NL matches since 2018 and
  0.468-0.519 cycle by cycle (2022+ 0.468, 2026-27 0.519) — stable,
  which is why the envelope sweeps {0.40, 0.47, 0.55} around it. The
  slope is configurable per call through :func:`expected_gd` /
  :func:`pair_lambdas_from_elo`. The goal-difference expectation is
  split into lambdas through the total expectation:
  ``lambda_home = total/2 + gd/2``, ``lambda_away = total/2 - gd/2``,
  each floored at :data:`LAMBDA_FLOOR` = 0.05 (the floor only binds in
  extreme mismatches where ``|gd| > total``; the floored pair then sums
  to slightly more than ``total`` — a documented safety clamp, not a
  calibrated decomposition).
- Total expectation: per-tournament mean of ``home_goals + away_goals``
  over earlier matches of that tournament (the probe's NL mean
  total is 2.51 and H/D/A base rates 0.42/0.25/0.32 — international
  soccer is a LOW-scoring environment, so club-soccer priors ~2.7-2.9
  must not leak in). :data:`GLOBAL_FALLBACK_TOTAL` = 2.6 applies when
  no tournament context is given or the tournament has no strictly prior matches.
- Score grid: REUSED, not mirrored. ``soccer_model.score_grid`` /
  ``variant_outputs`` are pure ``(lh, la, rho, max_goals)`` functions
  with no xG-snapshot coupling, so this module imports them (with
  ``RHO_DC = -0.1``) instead of duplicating the 11x11 Poisson +
  Dixon-Coles machinery. Every frozen property documented there — tau
  applies only to the 0-0/0-1/1-0/1-1 cells, the grid renormalizes,
  team totals are DC-invariant, O/U lines >= 2.0 are DC-invariant,
  ``over(line)`` is push-excluded — holds here unchanged.
- Variant envelope (mirrors soccer's 10-sweep philosophy on the
  gate-compatible spread statistic, max-minus-min in cents, directly
  comparable to the ``variant_spread<=6c`` entry gate): the frozen
  nine-variant grid is :data:`SWEEP_SLOPES` {0.40, 0.47, 0.55} x
  :data:`SWEEP_TOTAL_MULTIPLIERS` {1.0, 1.05, 0.95} around the
  resolved tournament mean; every output is the median across the nine
  variants with the full min/max range and explicit variant-spread
  fields. Unlike the soccer sweep, rho is NOT a swept dimension: every
  variant runs at ``RHO_DC`` (the probe design pins the frozen lineage
  tau; totals >= 2.0 and team totals are DC-invariant anyway).
- 1X2 aggregation: ``p_home`` / ``p_draw`` are independent per-quantity
  medians and ``p_away = 1 - p_home - p_draw`` — soccer's own
  ``away_win = 1 - home_win - draw`` convention, so the median triple
  sums to 1 exactly (with per-quantity medians over an odd variant
  count, the complement is provably nonnegative); ranges and spreads
  are per-variant order statistics.

Policy (repo, load-bearing): ``soccer_model`` bans cross-league /
international 1X2 entry until a league-strength-bridged walk-forward
passes (``CROSS_LEAGUE_MW_WORST_EDGE_MIN`` in ``gates.py``), and national
teams ARE the international case. This module may COMPUTE 1X2 for
research and exposes :data:`INTL_MW_RESEARCH_ONLY` = True so callers and
gates can honor the ban mechanically. Entry-eligible V1 families are
totals / BTTS / exact-score (exact-score as a research family first),
mirroring the conservative totals-first entry policy already in force.

Causality boundary: replay Elo and each fixture's tournament total use
only matches strictly before the fixture date. The descriptive
``tournament_totals`` table summarizes the whole snapshot. When either
team lacks replay history and the current ``teams{}`` table covers both,
the compatibility query returns a quote marked ``ratings_stale=True``.
Table Elo is dated at ``generated_at`` and can include information after
the fixture. Backtests and the pre-kickoff shadow recorder require replay
ratings. Ratings provenance is per-fixture (both teams replay, or both use
the current table): the replay scale starts at base 1500 and differs from
the site table by a roughly uniform offset (probe: mean abs 62 Elo pts), so
a mixed-pair difference would inject that offset into every quote.

Fail-closed semantics (mirrors ``xg.pair_lambdas`` / ``nba_model``):
:func:`fixture_prediction` returns ``None`` — never a number — when a team
is unknown (neither in ``matches`` nor in ``teams{}``) or the table cannot
cover both teams when one or both lack strictly prior matches. The explicit reason
is available through :func:`refusal_reason`. Any malformed row, unknown
snapshot shape or non-finite value raises ``ValueError`` (hard fail, never
a silent NaN).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from polymarket_alpha_lab.papersim.soccer_model import (
    GRID_MAX_GOALS,
    MatchOutputs,
    RHO_DC,
    median,
    over_probability,
    score_grid,
    team_total_over,
    variant_outputs,
)

__all__ = [
    "DEFAULT_SLOPE",
    "ELO_BASE",
    "GLOBAL_FALLBACK_TOTAL",
    "HOME_ADVANTAGE",
    "INTL_MW_RESEARCH_ONLY",
    "IntlFixture",
    "IntlLineQuote",
    "IntlPrediction",
    "IntlScoreQuote",
    "IntlVariantResult",
    "IntlVariantSpec",
    "LAMBDA_FLOOR",
    "RATING_SOURCE_CURRENT_TABLE",
    "RATING_SOURCE_REPLAY",
    "SWEEP_SLOPES",
    "SWEEP_TOTAL_MULTIPLIERS",
    "TOTAL_SOURCE_FALLBACK",
    "TOTAL_SOURCE_TOURNAMENT",
    "TournamentTotal",
    "VARIANTS",
    "VARIANT_NAMES",
    "expected_gd",
    "fixture_prediction",
    "goal_difference_multiplier",
    "k_factor",
    "pair_lambdas_from_elo",
    "refusal_reason",
    "tournament_mean_total",
    "tournament_totals",
]

#: Policy note honored by callers and gates: international 1X2 (match
#: winner) is RESEARCH-ONLY here. The module computes 1X2 probabilities,
#: but entry eligibility is banned until the soccer_model cross-league /
#: international walk-forward gate passes (see module docstring).
INTL_MW_RESEARCH_ONLY = True

#: Elo engine (eloratings.net scheme, probe-validated at rho=0.996).
ELO_BASE = 1500.0
HOME_ADVANTAGE = 100.0


#: Elo->goals bridge (probe fit; see module docstring for provenance).
DEFAULT_SLOPE = 0.47
GLOBAL_FALLBACK_TOTAL = 2.6
LAMBDA_FLOOR = 0.05

#: Frozen envelope grids: slope x total-multiplier -> nine variants.
SWEEP_SLOPES: tuple[float, ...] = (0.40, 0.47, 0.55)
SWEEP_TOTAL_MULTIPLIERS: tuple[float, ...] = (1.0, 1.05, 0.95)

#: Provenance / calibration-source tags carried on every prediction.
RATING_SOURCE_REPLAY = "replay"
RATING_SOURCE_CURRENT_TABLE = "current_table"
TOTAL_SOURCE_TOURNAMENT = "tournament"
TOTAL_SOURCE_FALLBACK = "global_fallback"


# ---------------------------------------------------------------------------
# Frozen public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntlFixture:
    """One fixture to predict (frozen prediction input).

    ``date`` is the barrier for replay ratings and tournament totals.
    ``neutral=True`` excludes the home advantage from
    the Elo->goals bridge (the replay engine's venue effect enters through
    each historical match's own ``neutral`` flag instead).
    """

    home: str
    away: str
    date: str
    neutral: bool = False


@dataclass(frozen=True)
class IntlVariantSpec:
    """One envelope variant: bridge slope x total multiplier."""

    name: str
    slope: float
    total_multiplier: float


#: The nine-variant envelope, frozen in order (slope asc, multiplier in
#: the documented 1.0 / 1.05 / 0.95 listing order).
VARIANTS: tuple[IntlVariantSpec, ...] = tuple(
    IntlVariantSpec(
        name=f"slope{slope:.2f}-totalx{multiplier:g}",
        slope=slope,
        total_multiplier=multiplier,
    )
    for slope in SWEEP_SLOPES
    for multiplier in SWEEP_TOTAL_MULTIPLIERS
)

VARIANT_NAMES: tuple[str, ...] = tuple(spec.name for spec in VARIANTS)


@dataclass(frozen=True)
class TournamentTotal:
    """Mean total goals over one tournament's snapshot matches."""

    tournament: str
    mean_total: float
    matches: int


@dataclass(frozen=True)
class IntlVariantResult:
    """One evaluated envelope variant for a single fixture.

    ``expected_gd`` is the variant's own goal-difference expectation
    (``slope * effective_elo_diff / 100``), ``total`` its (multiplied)
    total expectation, and ``outputs`` the reused soccer-model per-market
    outputs of the variant's Dixon-Coles score grid.
    """

    spec: IntlVariantSpec
    total: float
    expected_gd: float
    lambda_home: float
    lambda_away: float
    outputs: MatchOutputs


@dataclass(frozen=True)
class IntlLineQuote:
    """Over/under quote for one line across the envelope.

    ``p_under = 1 - p_over`` exactly; ``over_variants`` carries the nine
    per-variant quotes so the entry gates can consume them directly
    (``variant_spread`` is the max-minus-min statistic the
    ``variant_spread<=6c`` gate compares, in cents when multiplied
    by 100).
    """

    line: float
    p_over: float
    p_under: float
    over_range: tuple[float, float]
    under_range: tuple[float, float]
    variant_spread: float
    over_variants: tuple[float, ...]


@dataclass(frozen=True)
class IntlScoreQuote:
    """Exact-score cell quote across the envelope (Dixon-Coles cells)."""

    home_goals: int
    away_goals: int
    probability: float
    range: tuple[float, float]
    variant_spread: float
    variants: tuple[float, ...]


@dataclass(frozen=True)
class IntlPrediction:
    """Median/range prediction for one fixture across the nine variants.

    Median fields aggregate the nine variant values (the middle order
    statistic for the odd count, soccer's convention); ``*_range`` fields
    are (min, max) across the variants; ``*_variant_spread`` fields are
    max - min. ``p_away = 1 - p_home - p_draw`` so the median 1X2 triple
    sums to 1 exactly. ``ratings_stale`` marks the current-table fallback
    (see the module's staleness caveat); ``home_advantage_applied`` is 0.0
    for neutral fixtures.
    """

    fixture: IntlFixture
    tournament: str | None
    total_base: float
    total_source: str
    tournament_matches: int
    rating_source: str
    ratings_stale: bool
    elo_home: float
    elo_away: float
    games_home: int
    games_away: int
    home_advantage_applied: float
    lambda_home: float
    lambda_away: float
    p_home: float
    p_draw: float
    p_away: float
    p_home_range: tuple[float, float]
    p_draw_range: tuple[float, float]
    p_away_range: tuple[float, float]
    p_home_variant_spread: float
    p_draw_variant_spread: float
    p_away_variant_spread: float
    btts: float
    btts_range: tuple[float, float]
    btts_variant_spread: float
    variants: tuple[IntlVariantResult, ...]

    def over_line_prob(self, line: float) -> IntlLineQuote:
        """P(total goals strictly above ``line``) across the envelope.

        Push-excluded lineage semantics, reused from
        ``soccer_model.over_probability``: ``over(line)`` sums the total
        distribution from ``floor(line) + 1`` goals up.
        """
        checked = _finite(line, "line")
        return _line_quote(
            checked,
            tuple(
                over_probability(variant.outputs, checked)
                for variant in self.variants
            ),
        )

    def team_total_over_prob(self, side: str, line: float) -> IntlLineQuote:
        """P(one team's goals strictly above ``line``); side home/away."""
        if side not in ("home", "away"):
            raise ValueError(f"side must be 'home' or 'away', got {side!r}")
        checked = _finite(line, "line")
        return _line_quote(
            checked,
            tuple(
                team_total_over(variant.outputs, side, checked)
                for variant in self.variants
            ),
        )

    def exact_score_prob(self, home_goals: int, away_goals: int) -> IntlScoreQuote:
        """P(exact score) for one cell of the Dixon-Coles score grid.

        Recomputes each variant's grid from its own lambdas at
        ``RHO_DC`` (identical arithmetic to the variant's ``outputs``)
        and aggregates the nine cell probabilities: median, min/max
        range and variant spread.
        """
        for name, value in (("home_goals", home_goals), ("away_goals", away_goals)):
            if type(value) is not int or not 0 <= value <= GRID_MAX_GOALS:
                raise ValueError(
                    f"{name} must be an int in 0..{GRID_MAX_GOALS}, got {value!r}"
                )
        cells = tuple(
            score_grid(
                variant.lambda_home,
                variant.lambda_away,
                rho=RHO_DC,
            )[home_goals][away_goals]
            for variant in self.variants
        )
        low, high = min(cells), max(cells)
        return IntlScoreQuote(
            home_goals=home_goals,
            away_goals=away_goals,
            probability=median(cells),
            range=(low, high),
            variant_spread=high - low,
            variants=cells,
        )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _finite(
    value: object,
    name: str,
    *,
    lower: float | None = None,
    strictly: bool = False,
) -> float:
    """Finite-number guard; rejects bools and non-finite values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {value!r}")
    if lower is not None:
        if strictly and number <= lower:
            raise ValueError(f"{name} must be greater than {lower}, got {value!r}")
        if not strictly and number < lower:
            raise ValueError(f"{name} must be at least {lower}, got {value!r}")
    return number


def _parse_date(value: object, name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date string, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


def _field(raw: Mapping, key: str, path: str) -> object:
    if key not in raw:
        raise ValueError(f"{path}.{key} is missing (frozen input contract)")
    return raw[key]


def _team_name(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a nonempty string, got {value!r}")
    return value


def _goals(value: object, path: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{path} must be a nonnegative int, got {value!r}")
    return value


def _boolean(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{path} must be a boolean, got {value!r}")
    return value


# ---------------------------------------------------------------------------
# Snapshot plumbing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _MatchRow:
    """One validated historical match row."""

    date: date
    home: str
    away: str
    home_goals: int
    away_goals: int
    tournament: str
    neutral: bool
    venue: str | None


def _snapshot_envelope(snapshot: object) -> Mapping:
    """Validate the frozen snapshot envelope; return the teams table."""
    if not isinstance(snapshot, Mapping):
        raise ValueError(f"snapshot must be a mapping, got {type(snapshot)!r}")
    if not isinstance(snapshot.get("generated_at"), str):
        raise ValueError(
            "snapshot['generated_at'] must be a string (frozen input contract)"
        )
    teams = snapshot.get("teams")
    if not isinstance(teams, Mapping):
        raise ValueError(
            "snapshot['teams'] must be a mapping of current Elo tables"
        )
    matches = snapshot.get("matches")
    if not isinstance(matches, Sequence) or isinstance(matches, (str, bytes)):
        raise ValueError("snapshot['matches'] must be a sequence of match rows")
    return teams


def _tournament_field(raw: Mapping, path: str) -> str:
    value = _field(raw, "tournament", path)
    if not isinstance(value, str):
        raise ValueError(f"{path}.tournament must be a string, got {value!r}")
    return value


def _match_row(raw: object, index: int) -> _MatchRow:
    path = f"matches[{index}]"
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path} must be an object")
    home = _team_name(_field(raw, "home", path), f"{path}.home")
    away = _team_name(_field(raw, "away", path), f"{path}.away")
    if home == away:
        raise ValueError(f"{path}.home must differ from away (got {home!r} twice)")
    venue = _field(raw, "venue", path)
    if venue is not None and not isinstance(venue, str):
        raise ValueError(f"{path}.venue must be a string or null, got {venue!r}")
    return _MatchRow(
        date=_parse_date(_field(raw, "date", path), f"{path}.date"),
        home=home,
        away=away,
        home_goals=_goals(_field(raw, "home_goals", path), f"{path}.home_goals"),
        away_goals=_goals(_field(raw, "away_goals", path), f"{path}.away_goals"),
        tournament=_tournament_field(raw, path),
        neutral=_boolean(_field(raw, "neutral", path), f"{path}.neutral"),
        venue=venue,
    )


def _validated_matches(matches: object) -> list[_MatchRow]:
    """Validate every match row; return the rows sorted by date (stable)."""
    if not isinstance(matches, Sequence) or isinstance(matches, (str, bytes)):
        raise ValueError("snapshot['matches'] must be a sequence of match rows")
    rows = [_match_row(raw, index) for index, raw in enumerate(matches)]
    rows.sort(key=lambda row: row.date)  # stable within a date
    return rows


# ---------------------------------------------------------------------------
# Elo engine (replay over the full match history)
# ---------------------------------------------------------------------------


def k_factor(tournament: str) -> float:
    """K base by competition tier — DELEGATES to :func:`polymarket_alpha_lab.papersim.intl_data.tournament_k`.

    The adapter's frozen exact-match table is the single K authority (the
    probe-validated rho=0.996 engine). This module previously carried its own
    substring classifier, which disagreed with the adapter on 8 corpus
    tournament strings (e.g. "African Cup of Nations", "CONCACAF
    Championship", "Oceania Nations Cup") and drifted replay Elo by up to
    ~18 points for 27 teams — the 2026-11-12 independent review's P1
    finding. Delegation removes the divergence by construction; the
    cross-engine vocabulary is pinned by test.
    """
    from polymarket_alpha_lab.papersim.intl_data import tournament_k

    if not isinstance(tournament, str):
        raise ValueError(f"tournament must be a string, got {tournament!r}")
    return tournament_k(tournament)


def goal_difference_multiplier(gd: int) -> float:
    """Goal-difference K multiplier: 1.0 (draw/one-goal), 1.5 (two),
    ``(11+N)/8`` for N >= 3 (the eloratings.net blowout scale)."""
    if type(gd) is not int or gd < 0:
        raise ValueError(f"goal difference must be a nonnegative int, got {gd!r}")
    if gd <= 1:
        return 1.0
    if gd == 2:
        return 1.5
    return (11.0 + gd) / 8.0


def _elo_expected(diff: float) -> float:
    """Logistic expected score for an Elo difference."""
    return 1.0 / (1.0 + 10.0 ** (-diff / 400.0))


def _elo_delta(row: _MatchRow, rating_home: float, rating_away: float) -> float:
    advantage = 0.0 if row.neutral else HOME_ADVANTAGE
    expected = _elo_expected(rating_home + advantage - rating_away)
    margin = row.home_goals - row.away_goals
    if margin > 0:
        result = 1.0
    elif margin == 0:
        result = 0.5
    else:
        result = 0.0
    return (
        k_factor(row.tournament)
        * goal_difference_multiplier(abs(margin))
        * (result - expected)
    )


def _replay_elos(
    rows: Sequence[_MatchRow],
    home: str,
    away: str,
    barrier: date,
) -> tuple[float, float, int, int]:
    """Replay the full history strictly before ``barrier``.

    Every match is replayed (all teams tracked — an update needs the
    opponent's current rating), in sorted date order, starting every team
    at :data:`ELO_BASE`. Returns ``(elo_home, elo_away, games_home,
    games_away)`` where the game counts are the strictly-prior matches
    involving each fixture team.
    """
    ratings: dict[str, float] = {}
    games = {home: 0, away: 0}
    for row in rows:
        if row.date >= barrier:
            break
        rating_home = ratings.setdefault(row.home, ELO_BASE)
        rating_away = ratings.setdefault(row.away, ELO_BASE)
        delta = _elo_delta(row, rating_home, rating_away)
        ratings[row.home] = rating_home + delta
        ratings[row.away] = rating_away - delta
        if row.home in games:
            games[row.home] += 1
        if row.away in games:
            games[row.away] += 1
    return (
        ratings.get(home, ELO_BASE),
        ratings.get(away, ELO_BASE),
        games[home],
        games[away],
    )


def _table_elo(teams: Mapping, team: str) -> float:
    """Validated current-table Elo for one team."""
    block = teams.get(team)
    if not isinstance(block, Mapping):
        raise ValueError(f"teams.{team} must be an object")
    return _finite(_field(block, "elo", f"teams.{team}"), f"teams.{team}.elo")


# ---------------------------------------------------------------------------
# Elo->goals bridge (pure, hand-checkable)
# ---------------------------------------------------------------------------


def expected_gd(
    elo_home: float,
    elo_away: float,
    *,
    slope: float = DEFAULT_SLOPE,
    neutral: bool = False,
    home_advantage: float = HOME_ADVANTAGE,
) -> float:
    """Expected goal difference from the Elo gap (probe fit, §docstring).

    ``E[gd] = slope * (elo_home - elo_away + home_advantage) / 100`` with
    the home advantage included only for non-neutral fixtures. ``slope``
    is configurable (default 0.47; probe provenance in the module
    docstring) and must be positive.
    """
    if type(neutral) is not bool:
        raise ValueError(f"neutral must be a boolean, got {neutral!r}")
    home = _finite(elo_home, "elo_home")
    away = _finite(elo_away, "elo_away")
    checked_slope = _finite(slope, "slope", lower=0.0, strictly=True)
    advantage = _finite(home_advantage, "home_advantage", lower=0.0)
    diff = home - away + (0.0 if neutral else advantage)
    return checked_slope * diff / 100.0


def pair_lambdas_from_elo(
    elo_home: float,
    elo_away: float,
    total: float,
    *,
    slope: float = DEFAULT_SLOPE,
    neutral: bool = False,
    home_advantage: float = HOME_ADVANTAGE,
) -> tuple[float, float]:
    """``(lambda_home, lambda_away)`` split of the total by the Elo GD.

    ``lambda_home = total/2 + gd/2`` and ``lambda_away = total/2 - gd/2``,
    each floored at :data:`LAMBDA_FLOOR` (extreme-mismatch safety clamp;
    see the module docstring for the documented sum drift when it binds).
    """
    gd = expected_gd(
        elo_home,
        elo_away,
        slope=slope,
        neutral=neutral,
        home_advantage=home_advantage,
    )
    checked_total = _finite(total, "total", lower=0.0, strictly=True)
    half_total = checked_total / 2.0
    return (
        max(half_total + gd / 2.0, LAMBDA_FLOOR),
        max(half_total - gd / 2.0, LAMBDA_FLOOR),
    )


# ---------------------------------------------------------------------------
# Per-tournament totals calibration
# ---------------------------------------------------------------------------


def tournament_totals(snapshot: object) -> dict[str, TournamentTotal]:
    """Mean total goals per tournament, from the snapshot's matches.

    Walks and validates every match row; the mapping is keyed by the
    exact tournament string, in first-appearance order. An empty match
    list yields an empty mapping (table-only snapshots are legal).
    """
    _snapshot_envelope(snapshot)
    rows = _validated_matches(snapshot.get("matches"))
    sums: dict[str, list[float]] = {}
    for row in rows:
        entry = sums.setdefault(row.tournament, [0.0, 0.0])
        entry[0] += row.home_goals + row.away_goals
        entry[1] += 1.0
    return {
        name: TournamentTotal(
            tournament=name,
            mean_total=total / count,
            matches=int(count),
        )
        for name, (total, count) in sums.items()
    }


def tournament_mean_total(snapshot: object, tournament: str) -> TournamentTotal | None:
    """One tournament's mean-total calibration, or None when absent."""
    if not isinstance(tournament, str) or not tournament.strip():
        raise ValueError(f"tournament must be a nonempty string, got {tournament!r}")
    return tournament_totals(snapshot).get(tournament)


def _resolve_total(
    rows: Sequence[_MatchRow],
    tournament: str | None,
    barrier: date,
) -> tuple[float, str, int]:
    """(total_base, source tag, corpus size) strictly before the fixture."""
    if tournament is None:
        return GLOBAL_FALLBACK_TOTAL, TOTAL_SOURCE_FALLBACK, 0
    total = 0.0
    count = 0
    for row in rows:
        if row.date >= barrier:
            break
        if row.tournament == tournament:
            total += row.home_goals + row.away_goals
            count += 1
    if count == 0:
        return GLOBAL_FALLBACK_TOTAL, TOTAL_SOURCE_FALLBACK, 0
    return total / count, TOTAL_SOURCE_TOURNAMENT, count


# ---------------------------------------------------------------------------
# Fixture prediction
# ---------------------------------------------------------------------------


def _validate_fixture(fixture: object) -> date:
    if not isinstance(fixture, IntlFixture):
        raise TypeError(f"fixture must be IntlFixture, got {type(fixture)!r}")
    for name in ("home", "away"):
        value = getattr(fixture, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"fixture.{name} must be a nonempty string")
    if fixture.home == fixture.away:
        raise ValueError("fixture home and away teams must differ")
    barrier = _parse_date(fixture.date, "fixture.date")
    if type(fixture.neutral) is not bool:
        raise ValueError(f"fixture.neutral must be a boolean, got {fixture.neutral!r}")
    return barrier


def _validate_tournament_arg(tournament: object) -> None:
    if tournament is not None and (
        not isinstance(tournament, str) or not tournament.strip()
    ):
        raise ValueError(f"tournament must be a nonempty string or None, got {tournament!r}")


def _resolve_ratings(
    teams: Mapping,
    rows: Sequence[_MatchRow],
    fixture: IntlFixture,
    barrier: date,
) -> tuple[float, float, int, int, str, bool] | str:
    """Causal replay ratings or a stale compatibility quote from the table."""
    elo_home, elo_away, games_home, games_away = _replay_elos(
        rows, fixture.home, fixture.away, barrier
    )
    if games_home and games_away:
        return elo_home, elo_away, games_home, games_away, RATING_SOURCE_REPLAY, False
    for label, team, games in (
        ("home", fixture.home, games_home),
        ("away", fixture.away, games_away),
    ):
        if not games and team not in teams:
            return (
                f"{label} team {team!r} has no matches strictly before {fixture.date} "
                "and is missing from the snapshot's teams table"
            )
    for label, team in (("home", fixture.home), ("away", fixture.away)):
        if team not in teams:
            return (
                f"{label} team {team!r} cannot be rated: the replay pairing "
                f"is incomplete (the opponent has no matches) and the teams "
                f"table has no entry; ratings provenance cannot be mixed"
            )
    return (
        _table_elo(teams, fixture.home),
        _table_elo(teams, fixture.away),
        0,
        0,
        RATING_SOURCE_CURRENT_TABLE,
        True,
    )


def refusal_reason(
    snapshot: object,
    fixture: IntlFixture,
) -> str | None:
    """Explicit soft-refusal reason, or None when predictable.

    Hard-validation problems (malformed fixture/snapshot) still raise
    from here; only the fail-closed rating refusals that make
    :func:`fixture_prediction` return ``None`` are reported as strings.
    """
    barrier = _validate_fixture(fixture)
    teams = _snapshot_envelope(snapshot)
    rows = _validated_matches(snapshot.get("matches"))
    resolved = _resolve_ratings(teams, rows, fixture, barrier)
    if isinstance(resolved, str):
        return resolved
    return None


def fixture_prediction(
    snapshot: object,
    fixture: IntlFixture,
    *,
    tournament: str | None = None,
) -> IntlPrediction | None:
    """Nine-variant median/range prediction for one international fixture.

    Returns ``None`` — never a number — whenever the fixture is refused
    (unknown team, unresolvable provenance, empty replay history; ask
    :func:`refusal_reason` for the explicit reason). Any malformed row or
    non-finite value anywhere raises ``ValueError``. ``tournament``
    selects the mean total of strictly prior matches in that tournament.
    A missing tournament history falls back to :data:`GLOBAL_FALLBACK_TOTAL`. Pure
    function: no network, no I/O, no mutation of the snapshot; identical
    inputs give identical outputs.
    """
    barrier = _validate_fixture(fixture)
    _validate_tournament_arg(tournament)
    teams = _snapshot_envelope(snapshot)
    rows = _validated_matches(snapshot.get("matches"))
    resolved = _resolve_ratings(teams, rows, fixture, barrier)
    if isinstance(resolved, str):
        return None
    elo_home, elo_away, games_home, games_away, source, stale = resolved
    total_base, total_source, corpus = _resolve_total(rows, tournament, barrier)
    if not total_base > 0.0:
        raise ValueError(
            f"tournament {tournament!r} has a degenerate mean total "
            f"({total_base!r}); refusing to price a zero-goal calibration"
        )
    results: list[IntlVariantResult] = []
    for spec in VARIANTS:
        gd = expected_gd(
            elo_home,
            elo_away,
            slope=spec.slope,
            neutral=fixture.neutral,
        )
        lambdas = pair_lambdas_from_elo(
            elo_home,
            elo_away,
            total_base * spec.total_multiplier,
            slope=spec.slope,
            neutral=fixture.neutral,
        )
        results.append(
            IntlVariantResult(
                spec=spec,
                total=total_base * spec.total_multiplier,
                expected_gd=gd,
                lambda_home=lambdas[0],
                lambda_away=lambdas[1],
                outputs=variant_outputs(lambdas[0], lambdas[1], rho=RHO_DC),
            )
        )
    prediction = _aggregate(
        fixture,
        tournament,
        total_base,
        total_source,
        corpus,
        elo_home,
        elo_away,
        games_home,
        games_away,
        source,
        stale,
        tuple(results),
    )
    _ensure_finite(prediction)
    return prediction


def _line_quote(line: float, overs: tuple[float, ...]) -> IntlLineQuote:
    low, high = min(overs), max(overs)
    p_over = median(overs)
    return IntlLineQuote(
        line=line,
        p_over=p_over,
        p_under=1.0 - p_over,
        over_range=(low, high),
        under_range=(1.0 - high, 1.0 - low),
        variant_spread=high - low,
        over_variants=overs,
    )


def _aggregate(
    fixture: IntlFixture,
    tournament: str | None,
    total_base: float,
    total_source: str,
    corpus: int,
    elo_home: float,
    elo_away: float,
    games_home: int,
    games_away: int,
    source: str,
    stale: bool,
    variants: tuple[IntlVariantResult, ...],
) -> IntlPrediction:
    home_wins = [variant.outputs.home_win for variant in variants]
    draws = [variant.outputs.draw for variant in variants]
    away_wins = [variant.outputs.away_win for variant in variants]
    btts_values = [variant.outputs.btts for variant in variants]
    p_home = median(home_wins)
    p_draw = median(draws)
    return IntlPrediction(
        fixture=fixture,
        tournament=tournament,
        total_base=total_base,
        total_source=total_source,
        tournament_matches=corpus,
        rating_source=source,
        ratings_stale=stale,
        elo_home=elo_home,
        elo_away=elo_away,
        games_home=games_home,
        games_away=games_away,
        home_advantage_applied=0.0 if fixture.neutral else HOME_ADVANTAGE,
        lambda_home=median([variant.lambda_home for variant in variants]),
        lambda_away=median([variant.lambda_away for variant in variants]),
        p_home=p_home,
        p_draw=p_draw,
        p_away=1.0 - p_home - p_draw,
        p_home_range=(min(home_wins), max(home_wins)),
        p_draw_range=(min(draws), max(draws)),
        p_away_range=(min(away_wins), max(away_wins)),
        p_home_variant_spread=max(home_wins) - min(home_wins),
        p_draw_variant_spread=max(draws) - min(draws),
        p_away_variant_spread=max(away_wins) - min(away_wins),
        btts=median(btts_values),
        btts_range=(min(btts_values), max(btts_values)),
        btts_variant_spread=max(btts_values) - min(btts_values),
        variants=variants,
    )


def _ensure_finite(prediction: IntlPrediction) -> None:
    scalars = (
        prediction.total_base,
        prediction.elo_home,
        prediction.elo_away,
        prediction.lambda_home,
        prediction.lambda_away,
        prediction.p_home,
        prediction.p_draw,
        prediction.p_away,
        prediction.btts,
    )
    for name, value in zip(
        (
            "total_base",
            "elo_home",
            "elo_away",
            "lambda_home",
            "lambda_away",
            "p_home",
            "p_draw",
            "p_away",
            "btts",
        ),
        scalars,
    ):
        if not math.isfinite(value):
            raise ValueError(f"non-finite model output {name}={value!r}")
    for variant in prediction.variants:
        for name in ("total", "expected_gd", "lambda_home", "lambda_away"):
            if not math.isfinite(getattr(variant, name)):
                raise ValueError(
                    f"non-finite model output {variant.spec.name}.{name}"
                )
