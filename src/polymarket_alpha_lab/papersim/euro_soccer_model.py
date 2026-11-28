"""Research forecasts from venue-specific team form and observed lineup similarity."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
import math
from statistics import fmean

from . import soccer_model
from .euro_soccer_sources import Fixture, LEAGUES, MatchHistory, seasons_for
from .euro_soccer_team_news import NEWS_MAX_AGE, TeamNews, TeamReport, fixture_identity, parse_prematch_news


__all__ = ["MODEL_VERSION", "forecast", "parse_prematch_news"]
MODEL_VERSION = "euro-soccer-venue-lineups-20261128-v1"
# Minimum number of distinct same-venue final matches for a research estimate.
MIN_TEAM_GAMES = 3


@dataclass(frozen=True)
class _Performance:
    scored: float
    conceded: float
    lineup: frozenset[str] | None
    has_xg: bool


def _eligible_history(fixture: Fixture, history: Iterable[MatchHistory], as_of: datetime) -> list[MatchHistory]:
    games: dict[str, MatchHistory] = {}
    conflicts: set[str] = set()
    seasons = seasons_for(fixture.league, fixture.kickoff)
    for entry in history:
        match = entry.fixture
        if (match.league != fixture.league or match.season not in seasons or match.phase != fixture.phase
                or match.status != "finished"
                or match.score is None or match.fixture_id == fixture.fixture_id
                or match.home_id == match.away_id or match.kickoff >= as_of
                or not match.kickoff <= match.observed_at <= as_of
                or not match.kickoff <= entry.observed_at <= as_of):
            continue
        if len(match.score) != 2 or any(type(n) is not int or n < 0 for n in match.score):
            continue
        if not ((match.home_id == fixture.home_id) or (match.away_id == fixture.away_id)):
            continue
        old = games.get(match.fixture_id)
        if old is None or old.observed_at < entry.observed_at:
            games[match.fixture_id] = entry
            conflicts.discard(match.fixture_id)
        elif old.observed_at == entry.observed_at and old != entry:
            conflicts.add(match.fixture_id)
    return [games[k] for k in sorted(games.keys() - conflicts)]


def _performance(entry: MatchHistory, as_home: bool) -> _Performance:
    home_goals, away_goals = entry.fixture.score
    has_xg = all(type(v) in (float, int) and math.isfinite(v) and v >= 0
                 for v in (entry.home_xg, entry.away_xg))
    # Goals and xG each describe the same match. Every fixture still carries one vote.
    home_rate = (home_goals + entry.home_xg) / 2 if has_xg else home_goals
    away_rate = (away_goals + entry.away_xg) / 2 if has_xg else away_goals
    starting = entry.home_starters if as_home else entry.away_starters
    lineup = frozenset(starting) if starting and len(starting) == len(set(starting)) == 11 else None
    scored, conceded = (home_rate, away_rate) if as_home else (away_rate, home_rate)
    return _Performance(scored, conceded, lineup, has_xg)


def _rates(games: list[_Performance], report: TeamReport | None) -> tuple[tuple[float, float], tuple[float, float] | None, dict]:
    raw = (fmean(g.scored for g in games), fmean(g.conceded for g in games))
    with_lineups = [g for g in games if g.lineup is not None]
    info = {"matches": len(games), "matches_with_xg": sum(g.has_xg for g in games),
            "matches_with_lineups": len(with_lineups)}
    if report is None or not (report.starters or report.unavailable):
        return raw, None, info | {"status": "no_current_lineup_or_reported_absence"}
    if len(with_lineups) < MIN_TEAM_GAMES:
        return raw, None, info | {"status": "insufficient_historical_lineups"}
    # Absences already represented in the current XI must only affect its similarity once.
    if report.starters is not None:
        players = set(report.starters)
        weights = [len(g.lineup & players) for g in with_lineups]
        basis = "starting_eleven_overlap"
    else:
        players = set(dict(report.unavailable))
        weights = [len(g.lineup - players) for g in with_lineups]
        basis = "available_historical_starters"
    info["basis"] = basis
    info["overlap_range"] = [min(weights), max(weights)]
    if sum(w > 0 for w in weights) < MIN_TEAM_GAMES:
        return raw, None, info | {"status": "insufficient_comparable_lineups"}
    if min(weights) == max(weights):
        return raw, None, info | {"status": "no_historical_lineup_contrast"}
    # Keep the total weight of matches with known lineups constant. Thus adding lineup
    # coverage to one team cannot indirectly remove the matches without lineup coverage.
    scale = len(with_lineups) / sum(weights)
    adjusted = []
    for attribute in ("scored", "conceded"):
        known = sum(scale * w * getattr(g, attribute) for g, w in zip(with_lineups, weights, strict=True))
        other = sum(getattr(g, attribute) for g in games if g.lineup is None)
        adjusted.append((known + other) / len(games))
    return raw, (adjusted[0], adjusted[1]), info | {"status": "lineup_weighted"}


def _probabilities(home: tuple[float, float], away: tuple[float, float]) -> dict | None:
    home_rate = (home[0] + away[1]) / 2
    away_rate = (away[0] + home[1]) / 2
    if not all(math.isfinite(v) and 0 < v <= soccer_model.GRID_MAX_GOALS for v in (home_rate, away_rate)):
        return None
    outputs = soccer_model.variant_outputs(home_rate, away_rate, rho=soccer_model.RHO_DC)
    over = soccer_model.over_probability(outputs, 2.5)
    return {"home_win": outputs.home_win, "draw": outputs.draw, "away_win": outputs.away_win,
            "over_2_5": over, "under_2_5": 1 - over, "home_xg": home_rate, "away_xg": away_rate}


def forecast(fixture: Fixture, history: Iterable[MatchHistory], *, news: TeamNews | None,
             as_of: datetime) -> dict:
    """Estimate 90-minute probabilities; expose a separate, unvalidated news scenario."""
    result = {"version": MODEL_VERSION, "fixture_id": fixture.fixture_id, "league": fixture.league,
              "as_of": as_of.isoformat(), "research_only": True, "amount_cny": 0,
              "baseline": None, "context": None, "news": None, "teams": {}, "source_refs": [],
              "status": "unavailable", "reasons": []}
    if as_of.utcoffset() is None:
        raise ValueError("a timezone-aware research cutoff is required")
    if (fixture.league not in LEAGUES or fixture.phase != "regular" or fixture.status != "scheduled"
            or fixture.home_id == fixture.away_id or not fixture.observed_at <= as_of < fixture.kickoff
            or fixture.season != seasons_for(fixture.league, fixture.kickoff)[1]):
        result["reasons"].append("fixture_outside_research_scope_or_cutoff")
        return result
    rows = _eligible_history(fixture, history, as_of)
    refs = {fixture.source_sha256}
    for row in rows:
        refs.update((row.source_sha256, row.fixture.source_sha256))
    sides = (None, None)
    if news is None:
        result["reasons"].append("news_not_collected")
    elif news.identity != fixture_identity(fixture):
        result["reasons"].append("news_fixture_identity_mismatch")
    else:
        blockers = list(news.blockers)
        if not blockers and (not news.source_sha256 or news.home is None or news.away is None):
            blockers.append("news_unbound_source")
        if not blockers and not (news.fetched_at.utcoffset() is not None and news.fetched_at <= as_of
                                 and as_of - news.fetched_at <= NEWS_MAX_AGE):
            blockers.append("news_outside_prematch_window")
        if news.source_sha256 and news.fetched_at.utcoffset() is not None and news.fetched_at <= as_of:
            refs.add(news.source_sha256)
        result["news"] = {"lineup_mode": news.lineup_mode, "fetched_at": news.fetched_at.isoformat(),
                          "source_refs": [news.source_sha256] if news.source_sha256 in refs else [],
                          "blockers": blockers}
        if blockers:
            result["reasons"].extend(blockers)
        else:
            sides = news.home, news.away
            result["news"].update(home=asdict(news.home), away=asdict(news.away))
    result["source_refs"] = sorted(refs)
    samples = ([_performance(r, True) for r in rows if r.fixture.home_id == fixture.home_id],
               [_performance(r, False) for r in rows if r.fixture.away_id == fixture.away_id])
    if min(map(len, samples)) < MIN_TEAM_GAMES:
        result["teams"] = {"home": {"matches": len(samples[0])}, "away": {"matches": len(samples[1])}}
        result["reasons"].append("insufficient_same_venue_team_history")
        return result
    home, hc, hs = _rates(samples[0], sides[0])
    away, ac, aws = _rates(samples[1], sides[1])
    result["teams"] = {"home": hs, "away": aws}
    result["baseline"] = _probabilities(home, away)
    if result["baseline"] is None:
        result["reasons"].append("goal_rates_outside_score_grid")
        return result
    result["status"] = "team_baseline"
    if hc is not None or ac is not None:
        result["context"] = _probabilities(hc or home, ac or away)
        if result["context"] is not None:
            result["status"] = "team_news_scenario"
        else:
            result["reasons"].append("context_goal_rates_outside_score_grid")
    return result
