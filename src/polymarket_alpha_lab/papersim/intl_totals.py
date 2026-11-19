"""Date-causal national-team goals model for over/under 2.5 shadow research."""

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


def _goal_rates(
    total: float, elo_home: float, elo_away: float, *, neutral: bool,
) -> tuple[float, float]:
    home, away = intl_model.pair_lambdas_from_elo(
        elo_home, elo_away, total, neutral=neutral,
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
    The most recent 12 matches in 730 days enter each team's estimate. Eight
    zero-residual pseudo-matches shrink attack and defence to the Elo prior.
    Each fixture goal rate combines the scoring team's attack residual and
    its opponent's conceding residual with equal weight. When either team
    has fewer than four observations the quote uses the tournament mean.
    The current Elo table plays no role.
    """

    def __init__(self, snapshot: dict) -> None:
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
                    total or intl_model.GLOBAL_FALLBACK_TOTAL,
                    ratings.get(row.home, intl_model.ELO_BASE),
                    ratings.get(row.away, intl_model.ELO_BASE),
                    neutral=row.neutral,
                )
                residuals.append((row, row.home_goals - home_rate, row.away_goals - away_rate))
            for row, home_residual, away_residual in residuals:
                home = ratings.get(row.home, intl_model.ELO_BASE)
                away = ratings.get(row.away, intl_model.ELO_BASE)
                delta = intl_model._elo_delta(row, home, away)
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
            return intl_model.GLOBAL_FALLBACK_TOTAL, 0
        _, goals, count = history[end - 1]
        return goals / count, count

    def _recent(self, team: str, day: int) -> list[_Observation]:
        history = self._team_history.get(team, [])
        end = bisect_left(history, day, key=attrgetter("day"))
        start = bisect_left(
            history, day - RECENT_DAYS, lo=max(0, end - MAX_RECENT_MATCHES),
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
        elif min(len(home), len(away)) < MIN_RECENT_MATCHES:
            fallback = "insufficient_recent_matches"
        if fallback:
            lambda_home = lambda_away = total / 2.0
            p_over25 = baseline_over25
        else:
            expected_home, expected_away = _goal_rates(
                total, home[-1].elo_after, away[-1].elo_after, neutral=fixture.neutral,
            )
            home_attack = sum(match.scored_residual for match in home) / (len(home) + PRIOR_MATCHES)
            home_defence = sum(match.conceded_residual for match in home) / (len(home) + PRIOR_MATCHES)
            away_attack = sum(match.scored_residual for match in away) / (len(away) + PRIOR_MATCHES)
            away_defence = sum(match.conceded_residual for match in away) / (len(away) + PRIOR_MATCHES)
            lambda_home = max(intl_model.LAMBDA_FLOOR, expected_home + (home_attack + away_defence) / 2)
            lambda_away = max(intl_model.LAMBDA_FLOOR, expected_away + (away_attack + home_defence) / 2)
            p_over25 = _over25(lambda_home, lambda_away)
        return IntlTotalsQuote(
            lambda_home, lambda_away, p_over25, 1.0 - p_over25, baseline_over25, total,
            tournament_matches, len(home), len(away), fallback,
        )
