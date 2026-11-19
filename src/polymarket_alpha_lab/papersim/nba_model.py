"""NBA pace/efficiency probabilistic model for papersim (WP-NBA-2).

Single responsibility: turn an ESPN-sourced NBA snapshot (frozen input
contract below) into moneyline and totals probabilities for one fixture
across a six-variant sensitivity envelope. Deterministic, pure, no
network, no I/O, paper-only/report-only: nothing here authenticates an
account, handles credentials, signs or submits anything. NBA stays
research-only until the walk-forward gates of the round-46 feasibility
design (``~/paper-sim/round46/nba_feasibility.md`` §4/§5) pass.

Input contract (FROZEN — the sibling ``espn_nba`` adapter builds this
schema; this module never imports it and is tested against synthetic
fixtures only)::

    {"generated_at": "<iso>", "season": "<str>",
     "teams": {"BOS": {"display": "Boston Celtics",
        "games": [{"date": "2026-10-22", "home": true, "opponent": "NYK",
                   "poss": 99.5, "pts": 118, "opp_pts": 110,
                   "ortg": 118.6, "drtg": 110.6,
                   "rest_days": 2, "b2b": false}]}},
     "injuries": {"BOS": [{"player": "...", "status": "...", "date": "..."}]}}

``season`` / ``generated_at`` must be present as strings (frozen
contract) but are not consumed. ``injuries`` is tolerated as absent and
never consumed: the injury layer is a STUB (multipliers dict parameter,
default 1.0) — news layer future work.

Model (round-46 feasibility design §4):

- Ratings: exponentially-weighted mean ORtg / DRtg / possessions per
  team with the half-life measured in GAMES — the most recent game has
  weight 1 and the game ``k`` back has weight ``2**(-k/half_life)``,
  the same ``exp2(-age/half_life)`` weighting as ``recalib``'s
  day-based decay with games as the age unit.
- Date cutoff: a fixture uses only games strictly before ``before_date``
  for both ratings and calibration (every game when None).
- Expected points: see the frozen derivation below.
- HCA: league-average home margin over the eligible home rows
  (reported on every prediction); applied +hca/2 home / -hca/2 away so
  the full effect lands on the margin and none on the total.
- B2B: flat points penalty on the fatigued side; both-b2b leaves the
  margin unchanged and cuts the total by 2x penalty. Fit from eligible
  rows when at least B2B_FIT_MIN_GAMES team-games sit on each side
  (league-average offensive output difference, clamped to
  B2B_PENALTY_CLAMP); otherwise the documented assumption
  DEFAULT_B2B_PENALTY = 2.0 pts.
- Rest differential: OPTIONAL (``use_rest_adjustment=False`` default).
  When enabled and both rest counts are known, adds
  REST_POINTS_PER_DAY per day of differential (capped at
  REST_MAX_DIFF_DAYS), split +/- so totals stay neutral. Documented
  assumption, not fit.
- Margin/total distributions: Normal CDFs (CLT over ~100 possessions;
  feasibility §4 deems the Normal adequate for V1 and defers
  Skellam/alternatives). No draws: ``p_away = 1 - p_home`` exactly.

Expected-points derivation (frozen V1 arithmetic, do not tune):

1. Possessions are transitive — one shared game pace. Team pace ratings
   are EWMA possessions; the fixture estimate anchors the multiplicative
   interaction at the league mean,
   ``pace_est = pace_home * pace_away / league_pace``
   (mirrors soccer's ``league_rate * attack * defense`` structure: an
   average-paced opponent leaves the other side's deviation fully
   expressed, two average teams reproduce the league pace exactly).
2. Efficiency interaction is additive per 100 possessions: the home
   offense expects its own ORtg plus the away defense's deviation from
   the league mean,
   ``exp_ortg_home = ortg_home + drtg_away - league_ortg``
   (symmetric for the away side). Summing both sides reproduces the
   feasibility total ``(ORtg_H + DRtg_A + ORtg_A + DRtg_H - 2*lg)/100 *
   pace``; differencing reproduces ``margin = pace * (Net_H - Net_A) /
   100`` with ``Net = ORtg - DRtg``.
3. ``pts = pace_est * exp_ortg / 100`` by the ORtg definition.

Sigma treatment: sigma is fit PER HALF-LIFE from walk-forward
residuals. For each eligible game with both team rows present, the
model re-predicts it using team ratings from strictly earlier dates
and the league context fitted for the fixture. It uses the game's
B2B flags, with rest and injury adjustments disabled, and records
actual-minus-predicted margin and total. With at least
MIN_RESIDUAL_GAMES residuals, sigma is the sample std (ddof=1) clamped
into SIGMA_MARGIN_CLAMP (10.5-13.5, feasibility target 11.5-12.5) and
SIGMA_TOTAL_CLAMP (13.0-18.0, feasibility target 14-16); below that
count the documented defaults DEFAULT_SIGMA_MARGIN = 12.0 /
DEFAULT_SIGMA_TOTAL = 15.0 apply. The x1.1 envelope variants scale the
clamped sigma and may exceed the clamp BY DESIGN (sensitivity
dimension, not a fit).

Variant envelope (mirrors soccer_model's sweep philosophy on the
gate-compatible spread statistic, ``max - min`` in cents, directly
comparable to the gates' var<=6c rule): the frozen six-variant grid is
SWEEP_HALF_LIVES {10, 20, 35} x SWEEP_SIGMA_SCALES {1.0, 1.1}; every
output is the median across the six variants with the full min/max
range and explicit variant-spread fields. Degenerate-dimension caveat:
with fewer than two prior games per team the half-life dimension
cannot differentiate (all EWMAs coincide) and the rating-side spread
collapses to zero — the envelope only quantifies the dimensions swept,
exactly like soccer's pair-level stub warning.

Causality boundary: pass the fixture date as ``before_date`` to bound
team ratings, league means, HCA, the B2B fit and sigma to games before
that date. The caller supplies the fixture's rest and B2B values and
any explicit injury multipliers available at prediction time.

Fail-closed semantics (mirrors ``xg.pair_lambdas``'s cross-league
guard): ``team_ratings`` / ``fixture_prediction`` return ``None`` for a
missing team or a team with no games strictly before the barrier —
soft refusals whose explicit reason is available through
``refusal_reason``. Any malformed input or non-finite value anywhere
raises ``ValueError`` (hard fail, never a silent NaN).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

__all__ = [
    "B2B_FIT_MIN_GAMES",
    "B2B_PENALTY_CLAMP",
    "DEFAULT_B2B_PENALTY",
    "DEFAULT_HALF_LIFE",
    "DEFAULT_SIGMA_MARGIN",
    "DEFAULT_SIGMA_TOTAL",
    "MIN_RESIDUAL_GAMES",
    "NbaFixture",
    "NbaLeagueContext",
    "NbaPrediction",
    "NbaTeamRatings",
    "NbaVariantResult",
    "NbaVariantSpec",
    "REST_MAX_DIFF_DAYS",
    "REST_POINTS_PER_DAY",
    "SIGMA_MARGIN_CLAMP",
    "SIGMA_TOTAL_CLAMP",
    "SWEEP_HALF_LIVES",
    "SWEEP_SIGMA_SCALES",
    "TotalLineQuote",
    "VARIANT_NAMES",
    "VARIANTS",
    "fixture_prediction",
    "league_context",
    "refusal_reason",
    "team_ratings",
]

#: Default EWMA half-life in games (hyperparameter; feasibility design).
DEFAULT_HALF_LIFE = 20.0

#: Frozen envelope grids: half-life x sigma-scale -> six variants.
SWEEP_HALF_LIVES: tuple[float, ...] = (10.0, 20.0, 35.0)
SWEEP_SIGMA_SCALES: tuple[float, ...] = (1.0, 1.1)

#: Margin sigma: default (few residuals), clamp bounds. Feasibility
#: target range 11.5-12.5 sits inside the clamp.
DEFAULT_SIGMA_MARGIN = 12.0
SIGMA_MARGIN_CLAMP: tuple[float, float] = (10.5, 13.5)

#: Totals sigma: default (few residuals), clamp bounds. Feasibility
#: target range 14-16 sits inside the clamp.
DEFAULT_SIGMA_TOTAL = 15.0
SIGMA_TOTAL_CLAMP: tuple[float, float] = (13.0, 18.0)

#: Minimum walk-forward residuals before sigma is fit from data.
MIN_RESIDUAL_GAMES = 10

#: B2B penalty: fixed assumption when unidentifiable from the snapshot,
#: and the fit's eligibility/clamp bounds. The fit is the league-average
#: offensive-output difference (non-B2B minus B2B team-games), clamped.
DEFAULT_B2B_PENALTY = 2.0
B2B_FIT_MIN_GAMES = 8
B2B_PENALTY_CLAMP: tuple[float, float] = (0.0, 4.0)

#: Rest-differential adjustment (OPTIONAL, default off): documented
#: assumptions, never fit.
REST_POINTS_PER_DAY = 0.25
REST_MAX_DIFF_DAYS = 3


# ---------------------------------------------------------------------------
# Frozen public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NbaFixture:
    """One fixture to predict (frozen prediction input).

    ``rest_*``/``b2b_*`` describe each side entering THIS game; the
    snapshot's per-game B2B flags feed the sigma walk and the B2B fit.
    """

    home: str
    away: str
    rest_home: int | None
    rest_away: int | None
    b2b_home: bool
    b2b_away: bool


@dataclass(frozen=True)
class NbaTeamRatings:
    """EWMA ratings for one team at one barrier date."""

    team: str
    ortg: float
    drtg: float
    pace: float
    games: int
    effective_games: float
    half_life: float
    before_date: str | None


@dataclass(frozen=True)
class NbaLeagueContext:
    """League calibration for the selected team-game rows.

    ``ortg`` / ``drtg`` are possession-weighted league means, ``pace``
    the mean team-game possessions, ``hca`` the mean home-row margin,
    ``b2b_penalty`` the fitted-or-default flat points penalty (sign
    convention: subtracted from the fatigued side's expected points)
    and ``team_games`` the count of team-game rows behind them.
    """

    ortg: float
    drtg: float
    pace: float
    hca: float
    b2b_penalty: float
    b2b_fitted: bool
    team_games: int


@dataclass(frozen=True)
class NbaVariantSpec:
    """One envelope variant: rating half-life x sigma scale."""

    name: str
    half_life: float
    sigma_scale: float


#: The six-variant sweep, frozen in order (hl asc, sigma scale asc).
VARIANTS: tuple[NbaVariantSpec, ...] = tuple(
    NbaVariantSpec(f"hl{half_life:g}-sig{scale:g}", half_life, scale)
    for half_life in SWEEP_HALF_LIVES
    for scale in SWEEP_SIGMA_SCALES
)
VARIANT_NAMES: tuple[str, ...] = tuple(spec.name for spec in VARIANTS)


@dataclass(frozen=True)
class NbaVariantResult:
    """One evaluated variant for a single fixture."""

    spec: NbaVariantSpec
    pace_estimate: float
    exp_pts_home: float
    exp_pts_away: float
    exp_margin: float
    exp_total: float
    sigma_margin: float
    sigma_total: float
    p_home: float
    p_away: float


@dataclass(frozen=True)
class TotalLineQuote:
    """Over/under quote for one totals line across the envelope.

    ``p_under = 1 - p_over`` exactly; ``over_variants`` mirrors the
    soccer candidate's ``qs_variants`` so the entry gates can consume
    the per-variant quotes directly (``variant_spread`` is the
    max-minus-min statistic the var<=6c gate compares, in cents when
    multiplied by 100).
    """

    line: float
    p_over: float
    p_under: float
    over_range: tuple[float, float]
    under_range: tuple[float, float]
    variant_spread: float
    over_variants: tuple[float, ...]


@dataclass(frozen=True)
class NbaPrediction:
    """Median/range prediction for one fixture across the six variants.

    Median fields aggregate the six variant values (mean of the middle
    pair for the even count, soccer's convention); ``*_range`` fields
    are (min, max) across the variants; ``*_variant_spread`` fields are
    max - min. ``sigma_margin_residuals`` /
    ``sigma_total_residuals`` report the walk-forward residual count
    behind the sigma fit (identical membership for every half-life:
    the skip rule only requires >=1 strictly-earlier game per team).
    """

    fixture: NbaFixture
    hca: float
    b2b_penalty: float
    b2b_penalty_fitted: bool
    league_ortg: float
    league_pace: float
    pace_estimate: float
    exp_pts_home: float
    exp_pts_away: float
    exp_margin: float
    exp_total: float
    p_home: float
    p_away: float
    sigma_margin: float
    sigma_total: float
    sigma_margin_residuals: int
    sigma_total_residuals: int
    p_home_range: tuple[float, float]
    p_away_range: tuple[float, float]
    exp_margin_range: tuple[float, float]
    exp_total_range: tuple[float, float]
    p_home_variant_spread: float
    exp_margin_variant_spread: float
    exp_total_variant_spread: float
    variants: tuple[NbaVariantResult, ...]

    def total_line_prob(self, line: float) -> TotalLineQuote:
        """P(total > ``line``) / P(total <= ``line``) for any finite line.

        Normal tail per variant; lines are treated as continuous
        (Polymarket NBA totals rungs are x.5 lines, so pushes have
        probability zero under this model and are not modelled).
        """
        checked = _number(line, "line")
        overs = tuple(
            _norm_cdf((variant.exp_total - checked) / variant.sigma_total)
            for variant in self.variants
        )
        low, high = min(overs), max(overs)
        p_over = _median(overs)
        return TotalLineQuote(
            line=checked,
            p_over=p_over,
            p_under=1.0 - p_over,
            over_range=(low, high),
            under_range=(1.0 - high, 1.0 - low),
            variant_spread=high - low,
            over_variants=overs,
        )


# ---------------------------------------------------------------------------
# Internal snapshot plumbing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _GameRow:
    """One validated team-game row."""

    team: str
    date: date
    home: bool
    opponent: str
    poss: float
    pts: float
    opp_pts: float
    ortg: float
    drtg: float
    rest_days: int | None
    b2b: bool


def _number(
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
            raise ValueError(
                f"{name} must be greater than {lower}, got {value!r}"
            )
        if not strictly and number < lower:
            raise ValueError(f"{name} must be at least {lower}, got {value!r}")
    return number


def _parse_date(value: object, name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date string, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}"
        ) from exc


def _snapshot_teams(snapshot: object) -> Mapping:
    """Validate the frozen snapshot envelope; return the teams table."""
    if not isinstance(snapshot, Mapping):
        raise ValueError(f"snapshot must be a mapping, got {type(snapshot)!r}")
    teams = snapshot.get("teams")
    if not isinstance(teams, Mapping) or not teams:
        raise ValueError(
            "snapshot['teams'] must be a nonempty mapping of team tables"
        )
    for key in ("season", "generated_at"):
        if not isinstance(snapshot.get(key), str):
            raise ValueError(
                f"snapshot[{key!r}] must be a string (frozen input contract)"
            )
    injuries = snapshot.get("injuries", {})
    if not isinstance(injuries, Mapping):
        raise ValueError("snapshot['injuries'] must be a mapping when present")
    return teams


def _field(raw: Mapping, key: str, path: str) -> object:
    if key not in raw:
        raise ValueError(f"{path}.{key} is missing (frozen input contract)")
    return raw[key]


def _game_row(team: str, raw: object, index: int) -> _GameRow:
    path = f"teams.{team}.games[{index}]"
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path} must be an object")
    opponent = _field(raw, "opponent", path)
    if not isinstance(opponent, str) or not opponent:
        raise ValueError(f"{path}.opponent must be a nonempty string")
    if opponent == team:
        raise ValueError(f"{path}.opponent must differ from the team itself")
    home = _field(raw, "home", path)
    if type(home) is not bool:
        raise ValueError(f"{path}.home must be a boolean")
    b2b = _field(raw, "b2b", path)
    if type(b2b) is not bool:
        raise ValueError(f"{path}.b2b must be a boolean")
    rest_raw = _field(raw, "rest_days", path)
    if rest_raw is not None:
        if type(rest_raw) is not int or rest_raw < 0:
            raise ValueError(
                f"{path}.rest_days must be a nonnegative int or None, "
                f"got {rest_raw!r}"
            )
    return _GameRow(
        team=team,
        date=_parse_date(_field(raw, "date", path), f"{path}.date"),
        home=home,
        opponent=opponent,
        poss=_number(_field(raw, "poss", path), f"{path}.poss", lower=0.0, strictly=True),
        pts=_number(_field(raw, "pts", path), f"{path}.pts", lower=0.0),
        opp_pts=_number(_field(raw, "opp_pts", path), f"{path}.opp_pts", lower=0.0),
        ortg=_number(_field(raw, "ortg", path), f"{path}.ortg", lower=0.0),
        drtg=_number(_field(raw, "drtg", path), f"{path}.drtg", lower=0.0),
        rest_days=rest_raw,
        b2b=b2b,
    )


def _team_games(teams: Mapping, team: str) -> list[_GameRow] | None:
    """Validated chronological rows for one team; None when absent."""
    if team not in teams:
        return None
    block = teams[team]
    if not isinstance(block, Mapping):
        raise ValueError(f"teams.{team} must be an object")
    games = _field(block, "games", f"teams.{team}")
    if not isinstance(games, Sequence) or isinstance(games, (str, bytes)):
        raise ValueError(f"teams.{team}.games must be a sequence")
    rows = [_game_row(team, raw, index) for index, raw in enumerate(games)]
    rows.sort(key=lambda row: row.date)  # stable within a date
    return rows


# ---------------------------------------------------------------------------
# League-level calibration
# ---------------------------------------------------------------------------


def league_context(snapshot: object) -> NbaLeagueContext:
    """League calibration over every game in the supplied snapshot.

    Walks and validates every team-game row: possession-weighted league
    ORtg/DRtg means, mean team-game pace, mean home-row margin (HCA),
    and the B2B penalty (fitted from league-average offensive output
    when at least ``B2B_FIT_MIN_GAMES`` team-games sit on each side,
    else the documented default). Raises on an empty snapshot — with no
    games there is nothing to calibrate.
    """
    teams = _snapshot_teams(snapshot)
    rows: list[_GameRow] = []
    for team in teams:
        rows.extend(_team_games(teams, team) or [])
    return _league_context_from_rows(rows)


def _league_context_from_rows(rows: Sequence[_GameRow]) -> NbaLeagueContext:
    if not rows:
        raise ValueError("snapshot contains no games; league context undefined")
    poss_sum = math.fsum(row.poss for row in rows)
    ortg = math.fsum(row.poss * row.ortg for row in rows) / poss_sum
    drtg = math.fsum(row.poss * row.drtg for row in rows) / poss_sum
    pace = poss_sum / len(rows)
    home_rows = [row for row in rows if row.home]
    hca = (
        math.fsum(row.pts - row.opp_pts for row in home_rows) / len(home_rows)
        if home_rows
        else 0.0
    )
    b2b_pts = [row.pts for row in rows if row.b2b]
    rested_pts = [row.pts for row in rows if not row.b2b]
    if len(b2b_pts) >= B2B_FIT_MIN_GAMES and len(rested_pts) >= B2B_FIT_MIN_GAMES:
        raw_penalty = (
            math.fsum(rested_pts) / len(rested_pts)
            - math.fsum(b2b_pts) / len(b2b_pts)
        )
        low, high = B2B_PENALTY_CLAMP
        penalty = min(max(raw_penalty, low), high)
        fitted = True
    else:
        penalty = DEFAULT_B2B_PENALTY
        fitted = False
    return NbaLeagueContext(
        ortg=ortg,
        drtg=drtg,
        pace=pace,
        hca=hca,
        b2b_penalty=penalty,
        b2b_fitted=fitted,
        team_games=len(rows),
    )


# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------


def _check_half_life(half_life: float) -> None:
    _number(half_life, "half_life", lower=0.0, strictly=True)


def _ratings_from_rows(
    rows: Sequence[_GameRow],
    team: str,
    half_life: float,
    before_date: str | None,
) -> NbaTeamRatings | None:
    """EWMA ratings over a team's selected chronological rows."""
    if not rows:
        return None
    count = len(rows)
    weights = [
        math.exp2(-(count - 1 - index) / half_life) for index in range(count)
    ]
    weight_sum = math.fsum(weights)
    return NbaTeamRatings(
        team=team,
        ortg=math.fsum(w * row.ortg for w, row in zip(weights, rows))
        / weight_sum,
        drtg=math.fsum(w * row.drtg for w, row in zip(weights, rows))
        / weight_sum,
        pace=math.fsum(w * row.poss for w, row in zip(weights, rows))
        / weight_sum,
        games=count,
        effective_games=weight_sum,
        half_life=half_life,
        before_date=before_date,
    )


def team_ratings(
    snapshot: object,
    team: str,
    before_date: str | None = None,
    *,
    half_life: float = DEFAULT_HALF_LIFE,
) -> NbaTeamRatings | None:
    """EWMA ORtg/DRtg/pace for one team over games strictly before a date.

    ``before_date=None`` means no barrier (every snapshot game). The
    age unit is games: the most recent game weighs 1 and the game ``k``
    back weighs ``2**(-k/half_life)``; ``effective_games`` is the
    weight mass (recalib's effective-matches analogue). Returns ``None``
    when the team is missing from the snapshot or has no game strictly
    before the barrier — the soft, fail-closed refusal. Non-finite or
    malformed rows raise ``ValueError``.
    """
    _check_half_life(half_life)
    if not isinstance(team, str) or not team:
        raise ValueError("team must be a nonempty string")
    teams = _snapshot_teams(snapshot)
    barrier = (
        _parse_date(before_date, "before_date")
        if before_date is not None
        else None
    )
    rows = _team_games(teams, team)
    if rows is None:
        return None
    if barrier is not None:
        rows = [row for row in rows if row.date < barrier]
    return _ratings_from_rows(rows, team, half_life, before_date)


# ---------------------------------------------------------------------------
# Expected-points arithmetic (frozen derivation; see module docstring)
# ---------------------------------------------------------------------------


def _adjusted_outcome(
    ratings_home: NbaTeamRatings,
    ratings_away: NbaTeamRatings,
    context: NbaLeagueContext,
    *,
    b2b_home: bool,
    b2b_away: bool,
    rest_home: int | None,
    rest_away: int | None,
    use_rest: bool,
    mult_home: float,
    mult_away: float,
) -> tuple[float, float, float]:
    """(pace_estimate, exp home pts, exp away pts) with every adjustment.

    Order: raw pace/efficiency points -> B2B flat penalty -> HCA split
    -> optional rest-differential split -> injury stub multipliers.
    """
    pace_estimate = ratings_home.pace * ratings_away.pace / context.pace
    exp_ortg_home = ratings_home.ortg + ratings_away.drtg - context.ortg
    exp_ortg_away = ratings_away.ortg + ratings_home.drtg - context.ortg
    pts_home = pace_estimate * exp_ortg_home / 100.0
    pts_away = pace_estimate * exp_ortg_away / 100.0
    if b2b_home:
        pts_home -= context.b2b_penalty
    if b2b_away:
        pts_away -= context.b2b_penalty
    pts_home += context.hca / 2.0
    pts_away -= context.hca / 2.0
    if use_rest and rest_home is not None and rest_away is not None:
        differential = float(rest_home - rest_away)
        capped = max(-float(REST_MAX_DIFF_DAYS), min(float(REST_MAX_DIFF_DAYS), differential))
        adjustment = REST_POINTS_PER_DAY * capped
        pts_home += adjustment / 2.0
        pts_away -= adjustment / 2.0
    pts_home *= mult_home
    pts_away *= mult_away
    return pace_estimate, pts_home, pts_away


def _norm_cdf(z: float) -> float:
    """Standard normal CDF (erfc form, no scipy)."""
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def _median(values: Sequence[float]) -> float:
    """Median: middle order statistic, mean of the middle pair if even."""
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        raise ValueError("median of an empty sequence")
    if count % 2:
        return ordered[count // 2]
    return (ordered[count // 2 - 1] + ordered[count // 2]) / 2.0


# ---------------------------------------------------------------------------
# Walk-forward sigma fit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SigmaFit:
    margin: float
    total: float
    residual_games: int


def _sample_std(values: Sequence[float]) -> float:
    count = len(values)
    mean = math.fsum(values) / count
    variance = math.fsum((value - mean) ** 2 for value in values) / (count - 1)
    return math.sqrt(variance)


def _fit_sigma(
    residuals: Sequence[float], default: float, clamp: tuple[float, float]
) -> float:
    if len(residuals) < MIN_RESIDUAL_GAMES:
        return default
    low, high = clamp
    return min(max(_sample_std(residuals), low), high)


def _pair_games(
    by_team: Mapping[str, Sequence[_GameRow]],
) -> tuple[dict[date, list[tuple[_GameRow, _GameRow]]], dict[date, list[_GameRow]]]:
    """Pair the two team rows of each game; group rows by date.

    Pairing requires both sides' rows (the adapter emits both); the two
    rows must agree on the score, and no two rows may claim the same
    side of the same game or contradict each other on venue — any
    disagreement raises (integrity, fail-closed).
    """
    rows_by_date: dict[date, list[_GameRow]] = {}
    home_sides: dict[tuple[date, str, str], _GameRow] = {}
    away_sides: dict[tuple[date, str, str], _GameRow] = {}
    for team in by_team:
        for row in by_team[team]:
            rows_by_date.setdefault(row.date, []).append(row)
            if row.home:
                key = (row.date, row.team, row.opponent)
                if key in home_sides:
                    raise ValueError(f"duplicate home row for game {key}")
                home_sides[key] = row
            else:
                key = (row.date, row.opponent, row.team)
                if key in away_sides:
                    raise ValueError(f"duplicate away row for game {key}")
                away_sides[key] = row
    for day, first, second in home_sides:
        if (day, second, first) in home_sides:
            raise ValueError(
                f"conflicting venue rows: {first} and {second} both claim "
                f"home on {day}"
            )
    for day, first, second in away_sides:
        if (day, second, first) in away_sides:
            raise ValueError(
                f"conflicting venue rows: {first} and {second} both claim "
                f"away on {day}"
            )
    paired: dict[date, list[tuple[_GameRow, _GameRow]]] = {}
    for key, home_row in home_sides.items():
        away_row = away_sides.get(key)
        if away_row is None:
            continue
        if (
            home_row.pts != away_row.opp_pts
            or home_row.opp_pts != away_row.pts
        ):
            raise ValueError(
                f"inconsistent scores between the two team rows of game {key}"
            )
        paired.setdefault(key[0], []).append((home_row, away_row))
    return paired, rows_by_date


def _state_ratings(
    state: Mapping[str, Sequence[float]], team: str, half_life: float
) -> NbaTeamRatings | None:
    """Ratings from an incremental EWMA state; None before any game."""
    weights, ortg_sum, drtg_sum, pace_sum, games = state[team]
    if weights <= 0.0:
        return None
    return NbaTeamRatings(
        team=team,
        ortg=ortg_sum / weights,
        drtg=drtg_sum / weights,
        pace=pace_sum / weights,
        games=int(games),
        effective_games=weights,
        half_life=half_life,
        before_date=None,
    )


def _sigma_pass(
    rows_by_date: Mapping[date, Sequence[_GameRow]],
    paired_by_date: Mapping[date, Sequence[tuple[_GameRow, _GameRow]]],
    half_life: float,
    context: NbaLeagueContext,
) -> _SigmaFit:
    """Walk-forward margin/total residual sigma for one half-life.

    Dates act as barriers for ratings: all predictions for a date use
    strictly earlier ratings, then that date's rows update the states.
    Each prediction uses the fixture's fitted league context and that
    game's B2B flags, with rest and injury adjustments disabled.
    """
    decay = math.exp2(-1.0 / half_life)
    state: dict[str, list[float]] = {}
    for rows in rows_by_date.values():
        for row in rows:
            state.setdefault(row.team, [0.0, 0.0, 0.0, 0.0, 0.0])
    margin_residuals: list[float] = []
    total_residuals: list[float] = []
    for day in sorted(rows_by_date):
        for home_row, away_row in paired_by_date.get(day, ()):
            ratings_home = _state_ratings(state, home_row.team, half_life)
            ratings_away = _state_ratings(state, away_row.team, half_life)
            if ratings_home is None or ratings_away is None:
                continue
            _, pts_home, pts_away = _adjusted_outcome(
                ratings_home,
                ratings_away,
                context,
                b2b_home=home_row.b2b,
                b2b_away=away_row.b2b,
                rest_home=None,
                rest_away=None,
                use_rest=False,
                mult_home=1.0,
                mult_away=1.0,
            )
            margin_residuals.append(
                (home_row.pts - home_row.opp_pts) - (pts_home - pts_away)
            )
            total_residuals.append(
                (home_row.pts + home_row.opp_pts) - (pts_home + pts_away)
            )
        for row in rows_by_date[day]:
            entry = state[row.team]
            for index in range(4):
                entry[index] *= decay
            entry[0] += 1.0
            entry[1] += row.ortg
            entry[2] += row.drtg
            entry[3] += row.poss
            entry[4] += 1.0
    return _SigmaFit(
        margin=_fit_sigma(margin_residuals, DEFAULT_SIGMA_MARGIN, SIGMA_MARGIN_CLAMP),
        total=_fit_sigma(total_residuals, DEFAULT_SIGMA_TOTAL, SIGMA_TOTAL_CLAMP),
        residual_games=len(margin_residuals),
    )


# ---------------------------------------------------------------------------
# Fixture prediction
# ---------------------------------------------------------------------------


def _validate_fixture(fixture: object) -> None:
    if not isinstance(fixture, NbaFixture):
        raise TypeError(f"fixture must be NbaFixture, got {type(fixture)!r}")
    for name in ("home", "away"):
        value = getattr(fixture, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"fixture.{name} must be a nonempty string")
    if fixture.home == fixture.away:
        raise ValueError("fixture home and away teams must differ")
    for name in ("rest_home", "rest_away"):
        value = getattr(fixture, name)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(
                f"fixture.{name} must be a nonnegative int or None, got {value!r}"
            )
    for name in ("b2b_home", "b2b_away"):
        if type(getattr(fixture, name)) is not bool:
            raise ValueError(f"fixture.{name} must be a boolean")


def _resolve_multipliers(
    injury_multipliers: object, fixture: NbaFixture
) -> tuple[float, float]:
    """Injury STUB: per-team point multipliers, default 1.0 (future work)."""
    if injury_multipliers is None:
        return (1.0, 1.0)
    if not isinstance(injury_multipliers, Mapping):
        raise ValueError("injury_multipliers must be a mapping or None")
    values = []
    for team in (fixture.home, fixture.away):
        if team in injury_multipliers:
            values.append(
                _number(
                    injury_multipliers[team],
                    f"injury_multipliers[{team!r}]",
                    lower=0.0,
                    strictly=True,
                )
            )
        else:
            values.append(1.0)
    return (values[0], values[1])


def refusal_reason(
    snapshot: object,
    fixture: NbaFixture,
    *,
    before_date: str | None = None,
) -> str | None:
    """Explicit soft-refusal reason, or None when predictable.

    Hard-validation problems (malformed fixture/snapshot) still raise
    from here; only the fail-closed team-availability refusals that
    make ``fixture_prediction`` return ``None`` are reported as
    strings.
    """
    _validate_fixture(fixture)
    teams = _snapshot_teams(snapshot)
    barrier = (
        _parse_date(before_date, "before_date")
        if before_date is not None
        else None
    )
    boundary = before_date if before_date is not None else "end of snapshot"
    for label, team in (("home", fixture.home), ("away", fixture.away)):
        rows = _team_games(teams, team)
        if rows is None:
            return f"{label} team {team!r} is missing from the snapshot teams"
        usable = [row for row in rows if barrier is None or row.date < barrier]
        if not usable:
            return (
                f"{label} team {team!r} has no games strictly before "
                f"{boundary}"
            )
    return None


def fixture_prediction(
    snapshot: object,
    fixture: NbaFixture,
    *,
    before_date: str | None = None,
    injury_multipliers: Mapping[str, float] | None = None,
    use_rest_adjustment: bool = False,
) -> NbaPrediction | None:
    """Six-variant median/range prediction for one fixture.

    Returns ``None`` — never a number — whenever either team is missing
    from the snapshot or has no game strictly before ``before_date``
    (the soft refusal; ask :func:`refusal_reason` for the explicit
    reason). Ratings and calibration use only games strictly before
    that date, or all games when it is None. Malformed rows or non-finite
    outputs raise ``ValueError``. The prediction is deterministic and
    leaves the snapshot untouched.
    """
    _validate_fixture(fixture)
    if not isinstance(use_rest_adjustment, bool):
        raise ValueError("use_rest_adjustment must be a boolean")
    reason = refusal_reason(snapshot, fixture, before_date=before_date)
    if reason is not None:
        return None
    teams = _snapshot_teams(snapshot)
    barrier = (
        _parse_date(before_date, "before_date")
        if before_date is not None
        else None
    )
    mult_home, mult_away = _resolve_multipliers(injury_multipliers, fixture)
    by_team = {team: _team_games(teams, team) or [] for team in teams}
    # Check full-snapshot pair integrity before selecting the eligible dates.
    paired_by_date, rows_by_date = _pair_games(by_team)
    if barrier is not None:
        rows_by_date = {
            day: rows for day, rows in rows_by_date.items() if day < barrier
        }
        paired_by_date = {
            day: pairs for day, pairs in paired_by_date.items() if day in rows_by_date
        }
        by_team = {
            team: [row for row in rows if row.date in rows_by_date]
            for team, rows in by_team.items()
        }
    context = _league_context_from_rows(
        [row for rows in by_team.values() for row in rows]
    )
    sigma_fits = {
        half_life: _sigma_pass(rows_by_date, paired_by_date, half_life, context)
        for half_life in SWEEP_HALF_LIVES
    }
    ratings_by_half_life = {
        half_life: (
            _ratings_from_rows(
                by_team[fixture.home], fixture.home, half_life, before_date
            ),
            _ratings_from_rows(
                by_team[fixture.away], fixture.away, half_life, before_date
            ),
        )
        for half_life in SWEEP_HALF_LIVES
    }
    variant_results: list[NbaVariantResult] = []
    for spec in VARIANTS:
        ratings_home, ratings_away = ratings_by_half_life[spec.half_life]
        if ratings_home is None or ratings_away is None:  # refusal already checked
            raise ValueError(
                f"no usable ratings for {spec.name}; refuse via "
                "refusal_reason before calling fixture_prediction"
            )
        pace_estimate, pts_home, pts_away = _adjusted_outcome(
            ratings_home,
            ratings_away,
            context,
            b2b_home=fixture.b2b_home,
            b2b_away=fixture.b2b_away,
            rest_home=fixture.rest_home,
            rest_away=fixture.rest_away,
            use_rest=use_rest_adjustment,
            mult_home=mult_home,
            mult_away=mult_away,
        )
        fit = sigma_fits[spec.half_life]
        sigma_margin = fit.margin * spec.sigma_scale
        sigma_total = fit.total * spec.sigma_scale
        p_home = _norm_cdf((pts_home - pts_away) / sigma_margin)
        variant_results.append(
            NbaVariantResult(
                spec=spec,
                pace_estimate=pace_estimate,
                exp_pts_home=pts_home,
                exp_pts_away=pts_away,
                exp_margin=pts_home - pts_away,
                exp_total=pts_home + pts_away,
                sigma_margin=sigma_margin,
                sigma_total=sigma_total,
                p_home=p_home,
                p_away=1.0 - p_home,
            )
        )
    _ensure_finite(variant_results)
    return _aggregate(fixture, context, sigma_fits, tuple(variant_results))


def _ensure_finite(variants: Sequence[NbaVariantResult]) -> None:
    for variant in variants:
        for name in (
            "pace_estimate",
            "exp_pts_home",
            "exp_pts_away",
            "exp_margin",
            "exp_total",
            "sigma_margin",
            "sigma_total",
            "p_home",
            "p_away",
        ):
            value = getattr(variant, name)
            if not math.isfinite(value):
                raise ValueError(
                    f"non-finite model output {variant.spec.name}.{name}={value!r}"
                )


def _aggregate(
    fixture: NbaFixture,
    context: NbaLeagueContext,
    sigma_fits: Mapping[float, _SigmaFit],
    variants: tuple[NbaVariantResult, ...],
) -> NbaPrediction:
    def median_of(name: str) -> float:
        return _median([getattr(variant, name) for variant in variants])

    def range_of(name: str) -> tuple[float, float]:
        values = [getattr(variant, name) for variant in variants]
        return (min(values), max(values))

    p_home_median = median_of("p_home")
    p_home_low, p_home_high = range_of("p_home")
    margin_low, margin_high = range_of("exp_margin")
    total_low, total_high = range_of("exp_total")
    central_fit = sigma_fits[DEFAULT_HALF_LIFE]
    return NbaPrediction(
        fixture=fixture,
        hca=context.hca,
        b2b_penalty=context.b2b_penalty,
        b2b_penalty_fitted=context.b2b_fitted,
        league_ortg=context.ortg,
        league_pace=context.pace,
        pace_estimate=median_of("pace_estimate"),
        exp_pts_home=median_of("exp_pts_home"),
        exp_pts_away=median_of("exp_pts_away"),
        exp_margin=median_of("exp_margin"),
        exp_total=median_of("exp_total"),
        p_home=p_home_median,
        p_away=1.0 - p_home_median,
        sigma_margin=median_of("sigma_margin"),
        sigma_total=median_of("sigma_total"),
        sigma_margin_residuals=central_fit.residual_games,
        sigma_total_residuals=central_fit.residual_games,
        p_home_range=(p_home_low, p_home_high),
        p_away_range=(1.0 - p_home_high, 1.0 - p_home_low),
        exp_margin_range=(margin_low, margin_high),
        exp_total_range=(total_low, total_high),
        p_home_variant_spread=p_home_high - p_home_low,
        exp_margin_variant_spread=margin_high - margin_low,
        exp_total_variant_spread=total_high - total_low,
        variants=variants,
    )
