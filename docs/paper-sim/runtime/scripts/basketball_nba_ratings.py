"""Project NBA scores from completed, opponent-adjusted team box scores."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import groupby
import math
from statistics import median
from zoneinfo import ZoneInfo


VERSION = "wp-nba-ratings-20261122-v1"
ET = ZoneInfo("America/New_York")
FINAL_LAG = timedelta(hours=6)
VARIANTS = tuple((shift, width, scale) for shift in (-2, -1, 0, 1, 2)
                 for width, scale in (("narrow", 0.96), ("wide", 1.04)))
VARIANT_NAMES = tuple(f"strength-{shift:+d}-dispersion-{width}" for shift, width, _ in VARIANTS)


@dataclass(frozen=True)
class Parameters:
    efficiency_gain: float
    pace_gain: float
    home_margin: float
    rest_margin: float
    margin_sigma: float
    total_sigma: float
    season_retention: float


# Fit and select on 2024–25 before evaluating the later seasons or their prices.
PARAMETERS = Parameters(0.04, 0.055, 1.4, 3.1, 11.5, 18.9, 0.55)


@dataclass
class Team:
    attack: float = 0.0
    defense: float = 0.0
    pace: float = 0.0
    games: int = 0
    last_day: date | None = None


@dataclass(frozen=True)
class Final:
    event_id: str
    day: date
    tip: datetime
    available_at: datetime
    home: str
    away: str
    neutral: bool
    home_points: int
    away_points: int
    possessions: float

    @classmethod
    def from_row(cls, row):
        day = date.fromisoformat(row["date"])
        tip = datetime.fromisoformat(row["started"])
        observed = row.get("final_observed_at")
        observed_at = datetime.fromisoformat(observed) if observed is not None else tip
        home, away = row["home"], row["away"]
        home_points, away_points = row["home_pts"], row["away_pts"]
        possessions = (row["home_poss"] + row["away_poss"]) / 2
        if (tip.utcoffset() is None or tip.astimezone(ET).date() != day
                or observed_at.utcoffset() is None or observed_at < tip
                or home == away or not all(isinstance(value, str) and value for value in (home, away, row["event_id"]))
                or type(row["neutral"]) is not bool or home_points == away_points
                or any(type(points) is not int or points <= 0 for points in (home_points, away_points))
                or not math.isfinite(possessions) or not 55 <= possessions <= 170):
            raise ValueError("invalid_nba_ratings_final")
        return cls(row["event_id"], day, tip, max(tip + FINAL_LAG, observed_at), home, away,
                   row["neutral"], home_points, away_points, possessions)

    def evidence(self):
        return (self.event_id, self.tip.isoformat(), self.available_at.isoformat(), self.home, self.away,
                self.neutral, self.home_points, self.away_points, self.possessions)


@dataclass(frozen=True)
class Projection:
    margin: float
    total: float
    margin_sigma: float
    total_sigma: float

    def distributions(self):
        return [[self.margin * (1 + 0.03 * shift), self.margin_sigma * scale,
                 self.total + shift, self.total_sigma * scale] for shift, _, scale in VARIANTS]

    def home_probability(self):
        return median(0.5 * math.erfc(-margin / (sigma * math.sqrt(2)))
                      for margin, sigma, _, _ in self.distributions())


def _clip(value, bound):
    return max(-bound, min(bound, value))


def season_start(day):
    return day.year if day.month >= 7 else day.year - 1


def prepare(games):
    finals = sorted((Final.from_row(row) for row in games), key=lambda game: (game.day, game.tip, game.event_id))
    if len({game.event_id for game in finals}) != len(finals):
        raise ValueError("duplicate_nba_ratings_final")
    return finals


class Ratings:
    def __init__(self, parameters=PARAMETERS):
        self.parameters = parameters
        self.teams = {}
        self.season = None
        self.league_efficiency = 114.0
        self.league_pace = 99.5

    def advance(self, day):
        season = season_start(day)
        if self.season is not None and season < self.season:
            raise ValueError("out_of_order_nba_season")
        if self.season is not None and self.season != season:
            retain = self.parameters.season_retention ** (season - self.season)
            for team in self.teams.values():
                team.attack *= retain
                team.defense *= retain
                team.pace *= retain
                team.last_day = None
                team.games = 0
        self.season = season

    def _expectation(self, home, away, day, neutral):
        home_b2b = home.last_day is not None and (day - home.last_day).days == 1
        away_b2b = away.last_day is not None and (day - away.last_day).days == 1
        context = (0.0 if neutral else self.parameters.home_margin)
        context += self.parameters.rest_margin * (away_b2b - home_b2b)
        home_eff = self.league_efficiency + home.attack + away.defense + context / 2
        away_eff = self.league_efficiency + away.attack + home.defense - context / 2
        return home_eff, away_eff, self.league_pace + home.pace + away.pace

    def forecast(self, home, away, day, neutral, *, minimum_games):
        self.advance(day)
        home_team, away_team = self.teams.get(home), self.teams.get(away)
        if home_team is None or away_team is None or min(home_team.games, away_team.games) < minimum_games:
            return None
        home_eff, away_eff, pace = self._expectation(home_team, away_team, day, neutral)
        return Projection((home_eff - away_eff) * pace / 100, (home_eff + away_eff) * pace / 100,
                          self.parameters.margin_sigma, self.parameters.total_sigma)

    def observe(self, game):
        self.advance(game.day)
        home = self.teams.setdefault(game.home, Team())
        away = self.teams.setdefault(game.away, Team())
        home_eff, away_eff, pace = self._expectation(home, away, game.day, game.neutral)
        home_error = _clip(100 * game.home_points / game.possessions - home_eff, 25.0)
        away_error = _clip(100 * game.away_points / game.possessions - away_eff, 25.0)
        pace_error = _clip(game.possessions - pace, 12.0)
        gain = self.parameters.efficiency_gain
        home.attack += gain * home_error
        away.defense += gain * home_error
        away.attack += gain * away_error
        home.defense += gain * away_error
        self.league_efficiency += 0.015 * (home_error + away_error) / 2
        self.league_pace += 0.015 * pace_error
        for team in (home, away):
            team.pace += self.parameters.pace_gain * pace_error
            team.last_day = game.day
            team.games += 1


def cutoff_for(day, as_of):
    """Freeze the game day's estimates at 08:00 Eastern, or earlier when observed."""
    return min(datetime.combine(day, time(8), tzinfo=ET), as_of)


def prior_finals(finals, day, cutoff):
    for final in finals:
        if final.day >= day:
            break
        if final.available_at <= cutoff:
            yield final


def fit(finals, parameters=PARAMETERS):
    ratings = Ratings(parameters)
    for final in finals:
        ratings.observe(final)
    return ratings


def replay(finals, *, minimum_games, season, parameters=PARAMETERS):
    """Evaluate each game with the same cutoff and source fields used on discovery."""
    forecasts = {}
    selected = (game for game in finals if season_start(game.day) == season)
    for day, games in groupby(selected, key=lambda game: game.day):
        today = tuple(games)
        cutoff = cutoff_for(day, min(game.tip for game in today))
        ratings = fit(prior_finals(finals, day, cutoff), parameters)
        for game in today:
            if game.neutral:
                continue
            forecast = ratings.forecast(game.home, game.away, day, game.neutral, minimum_games=minimum_games)
            if forecast is not None:
                forecasts[game.event_id] = forecast
    return forecasts
