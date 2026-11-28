"""Point-in-time FotMob calendars and team histories for five European research leagues."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
import json
import re
from urllib.parse import urlencode

from . import fotmob


@dataclass(frozen=True)
class League:
    fotmob_id: int
    country: str
    name: str


LEAGUES = {
    "por": League(61, "POR", "Liga Portugal"),
    "scop": League(64, "SCO", "Premiership"),
    "bel1": League(40, "BEL", "First Division A"),
    "nor": League(59, "NOR", "Eliteserien"),
    "tur": League(71, "TUR", "Süper Lig"),
}
_API = "https://www.fotmob.com/api/data"


@dataclass(frozen=True)
class Fixture:
    league: str
    season: str
    phase: str
    fixture_id: str
    home_id: str
    away_id: str
    home_name: str
    away_name: str
    kickoff: datetime
    status: str
    score: tuple[int, int] | None
    observed_at: datetime
    source_sha256: str
    round_number: int | None = None


@dataclass(frozen=True)
class MatchHistory:
    fixture: Fixture
    home_xg: float | None
    away_xg: float | None
    home_starters: tuple[str, ...] | None
    away_starters: tuple[str, ...] | None
    observed_at: datetime
    source_sha256: str


def _utc(value: str | datetime) -> datetime:
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise ValueError("expected a timestamp with a UTC offset")
    return moment.astimezone(UTC)


def _id(value: object) -> str:
    if not isinstance(value, (str, int)) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise ValueError("invalid provider identifier")
    return str(value)


def seasons_for(league: str, as_of: datetime) -> tuple[str, str]:
    """Return the previous and current FotMob season, in the provider's notation."""
    if league not in LEAGUES:
        raise ValueError("unsupported league")
    at = _utc(as_of)
    if league == "nor":
        return str(at.year - 1), str(at.year)
    year = at.year - (at.month < 7)
    return f"{year-1}/{year}", f"{year}/{year+1}"


def calendar_url(league: str, season: str) -> str:
    spec = LEAGUES[league]
    if not isinstance(season, str) or not re.fullmatch(r"[0-9]{4}(/[0-9]{4})?", season):
        raise ValueError("invalid season")
    years = season.split("/")
    if (league == "nor") != (len(years) == 1) or (len(years) == 2 and int(years[1]) != int(years[0]) + 1):
        raise ValueError("season does not match league calendar")
    return f"{_API}/leagues?" + urlencode({"id": spec.fotmob_id, "season": season})


def match_url(fixture_id: str) -> str:
    return f"{_API}/matchDetails?matchId={_id(fixture_id)}"


def _old_belgian_regular(rows: list[dict]) -> set[str]:
    # Group stages restart at round one. Verify the entire regular double round robin
    # before assigning any row; never infer the partition from its round alone.
    last_round = [_utc(row["status"]["utcTime"]) for row in rows if str(row.get("round")) == "30"]
    if len(last_round) != 8:
        raise ValueError("unverified Belgian regular-season partition")
    boundary = max(last_round)
    regular = [row for row in rows if _utc(row["status"]["utcTime"]) <= boundary]
    rounds: dict[str, list[str]] = {}
    for row in regular:
        rounds.setdefault(str(row["round"]), []).extend((_id(row["home"]["id"]), _id(row["away"]["id"])))
    pairs = {(_id(row["home"]["id"]), _id(row["away"]["id"])) for row in regular}
    teams = {team for pair in pairs for team in pair}
    if (rounds.keys() != {str(n) for n in range(1, 31)} or len(teams) != 16
            or any(len(appearances) != 16 or set(appearances) != teams for appearances in rounds.values())
            or pairs != {(home, away) for home in teams for away in teams if home != away}):
        raise ValueError("unverified Belgian regular-season partition")
    return {_id(row["id"]) for row in regular}


def _phase(league: str, row: dict, old_regular: set[str] | None) -> str | None:
    number = str(row.get("round", ""))
    if old_regular is not None:
        if _id(row["id"]) in old_regular:
            return "regular"
        if number == "final":
            return "playoff"
        return "split" if number.isdecimal() and 1 <= int(number) <= 10 else None
    if not re.fullmatch(r"[1-9][0-9]*", number):
        return None
    if league == "scop" and int(number) > 33:
        return "split" if int(number) <= 38 else None
    if league == "nor" and int(number) > 30:
        return None
    return "regular"


def _excluded(status: dict) -> bool:
    return (fotmob._is_cancelled({"status": status})
            or any(status.get(flag) is True for flag in ("postponed", "awarded", "abandoned"))
            or str((status.get("reason") or {}).get("short", "")).upper() in
            {"PP", "P-P", "POST", "PST", "TBD", "CANC", "ABD", "AW", "WO"})


def _fixture(league: str, season: str, row: dict, phase: str, at: datetime, digest: str) -> Fixture | None:
    status = row["status"]
    if _excluded(status):
        return None
    kickoff = _utc(status["utcTime"])
    score = None
    if status.get("finished") is True:
        if (status.get("started") is not True or (status.get("reason") or {}).get("short") != "FT"
                or kickoff >= at):
            return None
        result = re.fullmatch(r"([0-9]+)\s*-\s*([0-9]+)", str(status.get("scoreStr", "")))
        if not result:
            raise ValueError("full-time fixture has no regulation score")
        score = int(result[1]), int(result[2])
        state = "finished"
    elif status.get("started") is True and kickoff <= at:
        state = "live"
    elif status.get("started") is False and status.get("finished") is False and kickoff > at:
        state = "scheduled"
    else:
        return None
    home, away = row["home"], row["away"]
    home_id, away_id = _id(home["id"]), _id(away["id"])
    if (home_id == away_id or not all(isinstance(side.get("name"), str) and side["name"].strip()
                                      for side in (home, away))):
        raise ValueError("missing or ambiguous teams")
    number = str(row.get("round", ""))
    return Fixture(league, season, phase, _id(row["id"]), home_id, away_id, home["name"], away["name"],
                   kickoff, state, score, at, digest, int(number) if number.isdecimal() else None)


def parse_calendar(league: str, season: str, raw: bytes, *, observed_at: datetime) -> tuple[Fixture, ...]:
    """Decode an exact league and season. Only ordinary 90-minute FT results carry a score."""
    calendar_url(league, season)
    spec = LEAGUES[league]
    doc = json.loads(raw)
    meta = doc["details"]
    if (_id(meta["id"]) != str(spec.fotmob_id) or meta.get("selectedSeason") != season
            or meta.get("country") != spec.country or meta.get("type") != "league" or meta.get("gender") != "male"):
        raise ValueError("FotMob returned a different league, season or competition")
    rows = doc["fixtures"]["allMatches"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("missing football calendar")
    digest, at = sha256(raw).hexdigest(), _utc(observed_at)
    old_regular = _old_belgian_regular(rows) if league == "bel1" and int(season[:4]) < 2026 else None
    fixtures = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("malformed calendar row")
        ident = _id(row["id"])
        if ident in seen:
            raise ValueError("duplicate fixture in football calendar")
        seen.add(ident)
        parent = row.get("parentLeagueId", row.get("leagueId", spec.fotmob_id))
        if _id(parent) != str(spec.fotmob_id) or row.get("isFriendly") is True:
            continue
        phase = _phase(league, row, old_regular)
        if phase:
            fixture = _fixture(league, season, row, phase, at, digest)
            if fixture is not None:
                fixtures.append(fixture)
    return tuple(sorted(fixtures, key=lambda f: (f.kickoff, f.fixture_id)))


def _starters(lineup: dict, key: str, team_id: str) -> tuple[str, ...] | None:
    side = lineup.get(key) or {}
    if str(side.get("id")) != team_id:
        return None
    starters = side.get("starters")
    if not isinstance(starters, list) or len(starters) != 11:
        return None
    try:
        ids = tuple(_id(player["id"]) for player in starters)
    except (KeyError, ValueError, TypeError):
        return None
    return ids if len(set(ids)) == 11 else None


def parse_history_detail(fixture: Fixture, raw: bytes, *, observed_at: datetime) -> MatchHistory:
    """Bind full-time xG and official XIs to the calendar identity, score and capture time."""
    at = _utc(observed_at)
    doc = json.loads(raw)
    general, header = doc["general"], doc["header"]
    status, teams = header["status"], header["teams"]
    ids = (fixture.home_id, fixture.away_id)
    if (fixture.status != "finished" or fixture.score is None or at <= fixture.kickoff
            or general.get("finished") is not True or status.get("finished") is not True
            or _excluded(status) or (status.get("reason") or {}).get("short") != "FT"
            or _id(general["matchId"]) != fixture.fixture_id
            or str(general.get("parentLeagueId") or general.get("leagueId")) != str(LEAGUES[fixture.league].fotmob_id)
            or _utc(general["matchTimeUTCDate"]) != fixture.kickoff or _utc(status["utcTime"]) != fixture.kickoff
            or tuple(_id(general[k]["id"]) for k in ("homeTeam", "awayTeam")) != ids
            or tuple(_id(side["id"]) for side in teams) != ids
            or any(type(side.get("score")) is not int for side in teams)
            or tuple(side["score"] for side in teams) != fixture.score):
        raise ValueError("full-time report differs from the calendar")
    pair = fotmob._extract_xg_pair(doc, "Expected goals (xG)")
    xg = tuple(fotmob._parse_xg_number(v) for v in pair) if pair is not None else (None, None)
    lineup = (doc.get("content") or {}).get("lineup") or {}
    home = away = None
    if str(lineup.get("matchId")) == fixture.fixture_id and lineup.get("lineupType") in {"standard", "confirmed"}:
        home = _starters(lineup, "homeTeam", fixture.home_id)
        away = _starters(lineup, "awayTeam", fixture.away_id)
        if home and away and set(home).intersection(away):
            home = away = None
    return MatchHistory(fixture, xg[0], xg[1], home, away, at, sha256(raw).hexdigest())


def _known_fixtures(target: Fixture, fixtures: Iterable[Fixture], as_of: datetime) -> tuple[Fixture, ...]:
    at = _utc(as_of)
    if target.status != "scheduled" or not target.observed_at <= at < target.kickoff:
        raise ValueError("target is not a known pregame fixture at this observation time")
    latest = {}
    for row in fixtures:
        if row.league != target.league or row.fixture_id == target.fixture_id or row.observed_at > at:
            continue
        current = latest.get(row.fixture_id)
        if current is None or row.observed_at > current.observed_at:
            latest[row.fixture_id] = row
        elif row.observed_at == current.observed_at and row != current:
            raise ValueError("conflicting calendar evidence at the same capture time")
    teams = {target.home_id, target.away_id}
    eligible = (row for row in latest.values() if row.phase == target.phase and row.status == "finished"
                and row.score is not None and row.kickoff < at and teams.intersection((row.home_id, row.away_id)))
    return tuple(sorted(eligible, key=lambda row: (row.kickoff, row.fixture_id)))


def recent_fixtures(target: Fixture, fixtures: Iterable[Fixture], *, as_of: datetime,
                    per_team: int = 5) -> tuple[Fixture, ...]:
    """Select the home side's latest home games and the away side's latest away games."""
    if type(per_team) is not int or per_team < 1:
        raise ValueError("per_team must be a positive integer")
    eligible = _known_fixtures(target, fixtures, as_of)
    home = [row.fixture_id for row in eligible if row.home_id == target.home_id]
    away = [row.fixture_id for row in eligible if row.away_id == target.away_id]
    selected = set(home[-per_team:]) | set(away[-per_team:])
    return tuple(row for row in eligible if row.fixture_id in selected)


def team_history(target: Fixture, fixtures: Iterable[Fixture], details: Mapping[str, MatchHistory],
                 *, as_of: datetime) -> tuple[MatchHistory, ...]:
    """Use only captured results and enrich from known, identically bound detail snapshots."""
    history = []
    for row in _known_fixtures(target, fixtures, as_of):
        detail = details.get(row.fixture_id)
        if detail and detail.observed_at <= as_of:
            original = detail.fixture
            if (original.league, original.season, original.phase, original.fixture_id, original.kickoff,
                    original.home_id, original.away_id, original.score) == (
                    row.league, row.season, row.phase, row.fixture_id, row.kickoff, row.home_id, row.away_id, row.score):
                history.append(replace(detail, fixture=row))
                continue
        history.append(MatchHistory(row, None, None, None, None, row.observed_at, row.source_sha256))
    return tuple(history)
