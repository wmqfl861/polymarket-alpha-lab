"""Date-causal national-team goals model for over/under 2.5 shadow research.

MO-5b parameterization: every tunable constant is a keyword-only
``IntlTotalsResearch`` constructor argument defaulting to the module
constant (``recent_days``, ``max_recent_matches``,
``min_recent_matches``, ``prior_matches``) or to the frozen
``intl_model`` constant this module reads (``elo_base``,
``home_advantage``, ``slope``, ``global_fallback_total``,
``lambda_floor``). Constructing with defaults is bit-identical to the
pre-parameterization class; the module-level constants stay the single
source of the frozen values.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from itertools import groupby
from operator import attrgetter, itemgetter

from polymarket_alpha_lab.papersim import intl_model, soccer_model

SHADOW_VERSION = "intl-elo-goal-residuals-v1"
BASELINE_VERSION = "intl-prior-tournament-mean-v1"
RECENT_DAYS = 730
MAX_RECENT_MATCHES = 12
MIN_RECENT_MATCHES = 4
PRIOR_MATCHES = 8


@dataclass(frozen=True)
class IntlTotalsQuote:
    """Fixture rates, over/under 2.5 and the paired tournament-mean quote."""

    lambda_home: float
    lambda_away: float
    p_over25: float
    p_under25: float
    baseline_over25: float
    baseline_total: float
    tournament_matches: int
    home_recent_matches: int
    away_recent_matches: int
    fallback_reason: str | None


@dataclass(frozen=True, slots=True)
class _Observation:
    day: int
    scored_residual: float
    conceded_residual: float
    elo_after: float


def _int_at_least(value: object, name: str, lower: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < lower:
        raise ValueError(f"{name} must be an int >= {lower}, got {value!r}")
    return value


def _goal_rates(
    total: float, elo_home: float, elo_away: float, *, neutral: bool,
    slope: float = intl_model.DEFAULT_SLOPE,
    home_advantage: float = intl_model.HOME_ADVANTAGE,
) -> tuple[float, float]:
    home, away = intl_model.pair_lambdas_from_elo(
        elo_home, elo_away, total, neutral=neutral,
        slope=slope, home_advantage=home_advantage,
    )
    # Normalize the Elo split to the tournament mean even when a floor binds.
    scale = total / (home + away)
    return home * scale, away * scale


def _over25(home: float, away: float) -> float:
    outputs = soccer_model.variant_outputs(home, away, rho=soccer_model.RHO_DC)
    return soccer_model.over_probability(outputs, 2.5)


class IntlTotalsResearch:
    """Index dated results once and quote fixtures using strictly earlier days.

    Each result contributes goals scored and conceded minus the expectations
    from the pre-day Elo split and its competition's pre-day mean total.
    The ``max_recent_matches`` most recent matches within ``recent_days``
    days enter each team's estimate (defaults 12 / 730).
    ``prior_matches`` zero-residual pseudo-matches shrink attack and
    defence to the Elo prior (default 8). Each fixture goal rate combines
    the scoring team's attack residual and its opponent's conceding
    residual with equal weight. When either team has fewer than
    ``min_recent_matches`` observations the quote uses the tournament
    mean (default 4). The current Elo table plays no role.

    All keyword-only constructor arguments default to the frozen module
    constants; a default construction is bit-identical to the
    pre-parameterization class (MO-5b).
    """

    def __init__(
        self,
        snapshot: dict,
        *,
        recent_days: int = RECENT_DAYS,
        max_recent_matches: int = MAX_RECENT_MATCHES,
        min_recent_matches: int = MIN_RECENT_MATCHES,
        prior_matches: int = PRIOR_MATCHES,
        elo_base: float = intl_model.ELO_BASE,
        home_advantage: float = intl_model.HOME_ADVANTAGE,
        slope: float = intl_model.DEFAULT_SLOPE,
        global_fallback_total: float = intl_model.GLOBAL_FALLBACK_TOTAL,
        lambda_floor: float = intl_model.LAMBDA_FLOOR,
    ) -> None:
        self._recent_days = _int_at_least(recent_days, "recent_days", 1)
        self._max_recent_matches = _int_at_least(
            max_recent_matches, "max_recent_matches", 1
        )
        self._min_recent_matches = _int_at_least(
            min_recent_matches, "min_recent_matches", 1
        )
        self._prior_matches = _int_at_least(prior_matches, "prior_matches", 0)
        self._elo_base = intl_model._finite(elo_base, "elo_base")
        self._home_advantage = intl_model._finite(
            home_advantage, "home_advantage", lower=0.0
        )
        self._slope = intl_model._finite(slope, "slope", lower=0.0, strictly=True)
        self._global_fallback_total = intl_model._finite(
            global_fallback_total, "global_fallback_total", lower=0.0, strictly=True
        )
        self._lambda_floor = intl_model._finite(
            lambda_floor, "lambda_floor", lower=0.0
        )
        intl_model._snapshot_envelope(snapshot)
        rows = intl_model._validated_matches(snapshot.get("matches"))
        self._team_history: dict[str, list[_Observation]] = defaultdict(list)
        self._tournament_history: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
        ratings: dict[str, float] = {}
        for match_date, matches in groupby(rows, key=attrgetter("date")):
            day = match_date.toordinal()
            # Resolve all expectations before applying the day's results.
            residuals = []
            for row in matches:
                total, _ = self._tournament_mean(row.tournament, day)
                home_rate, away_rate = _goal_rates(
                    total or self._global_fallback_total,
                    ratings.get(row.home, self._elo_base),
                    ratings.get(row.away, self._elo_base),
                    neutral=row.neutral,
                    slope=self._slope,
                    home_advantage=self._home_advantage,
                )
                residuals.append((row, row.home_goals - home_rate, row.away_goals - away_rate))
            for row, home_residual, away_residual in residuals:
                home = ratings.get(row.home, self._elo_base)
                away = ratings.get(row.away, self._elo_base)
                delta = intl_model._elo_delta(
                    row, home, away, home_advantage=self._home_advantage
                )
                ratings[row.home] = home + delta
                ratings[row.away] = away - delta
                self._team_history[row.home].append(
                    _Observation(day, home_residual, away_residual, home + delta)
                )
                self._team_history[row.away].append(
                    _Observation(day, away_residual, home_residual, away - delta)
                )
                history = self._tournament_history[row.tournament]
                previous_goals, count = history[-1][1:] if history else (0, 0)
                history.append((day, previous_goals + row.home_goals + row.away_goals, count + 1))

    def _tournament_mean(self, tournament: str | None, day: int) -> tuple[float, int]:
        history = self._tournament_history.get(tournament, ()) if tournament else ()
        end = bisect_left(history, day, key=itemgetter(0))
        if end == 0:
            return self._global_fallback_total, 0
        _, goals, count = history[end - 1]
        return goals / count, count

    def _recent(self, team: str, day: int) -> list[_Observation]:
        history = self._team_history.get(team, [])
        end = bisect_left(history, day, key=attrgetter("day"))
        start = bisect_left(
            history, day - self._recent_days,
            lo=max(0, end - self._max_recent_matches),
            hi=end, key=attrgetter("day"),
        )
        return history[start:end]

    def predict(
        self, fixture: intl_model.IntlFixture, *, tournament: str | None = None,
    ) -> IntlTotalsQuote:
        day = intl_model._validate_fixture(fixture).toordinal()
        intl_model._validate_tournament_arg(tournament)
        total, tournament_matches = self._tournament_mean(tournament, day)
        home = self._recent(fixture.home, day)
        away = self._recent(fixture.away, day)
        baseline_over25 = _over25(total / 2.0, total / 2.0)
        fallback = None
        if total == 0.0:
            fallback = "zero_tournament_mean"
        elif min(len(home), len(away)) < self._min_recent_matches:
            fallback = "insufficient_recent_matches"
        if fallback:
            lambda_home = lambda_away = total / 2.0
            p_over25 = baseline_over25
        else:
            expected_home, expected_away = _goal_rates(
                total, home[-1].elo_after, away[-1].elo_after,
                neutral=fixture.neutral,
                slope=self._slope, home_advantage=self._home_advantage,
            )
            home_attack = sum(match.scored_residual for match in home) / (len(home) + self._prior_matches)
            home_defence = sum(match.conceded_residual for match in home) / (len(home) + self._prior_matches)
            away_attack = sum(match.scored_residual for match in away) / (len(away) + self._prior_matches)
            away_defence = sum(match.conceded_residual for match in away) / (len(away) + self._prior_matches)
            lambda_home = max(self._lambda_floor, expected_home + (home_attack + away_defence) / 2)
            lambda_away = max(self._lambda_floor, expected_away + (away_attack + home_defence) / 2)
            p_over25 = _over25(lambda_home, lambda_away)
        return IntlTotalsQuote(
            lambda_home, lambda_away, p_over25, 1.0 - p_over25, baseline_over25, total,
            tournament_matches, len(home), len(away), fallback,
        )
