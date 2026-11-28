"""Structured pre-match availability bound to a registered FotMob source snapshot."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from .euro_soccer_sources import LEAGUES, Fixture, match_url


NEWS_MAX_AGE = timedelta(minutes=5)
_ABSENCES = {"injury", "illness", "suspension", "internationalDuty"}
_UNCERTAIN = ("doubtful", "questionable", "day to day", "day-to-day", "fitness test", "probable")
_LINEUPS = {"standard": "confirmed", "confirmed": "confirmed", "predicted": "predicted",
            "lastStarting11": "previous"}


def _instant(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.utcoffset() is not None else None


def _id(value: object) -> str | None:
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        return None
    value = str(value)
    return value if value.isascii() and value.isdecimal() and int(value) > 0 else None


def fixture_identity(fixture: Fixture) -> tuple:
    return (fixture.league, fixture.season, fixture.phase, fixture.fixture_id,
            fixture.home_id, fixture.away_id, fixture.kickoff)


@dataclass(frozen=True)
class TeamReport:
    team_id: str
    starters: tuple[str, ...] | None
    # Each tuple holds a public player ID and the provider's structured reason.
    unavailable: tuple[tuple[str, str], ...]
    uncertain: tuple[tuple[str, str], ...]
    absences_known: bool


@dataclass(frozen=True)
class TeamNews:
    identity: tuple
    fetched_at: datetime
    source_sha256: str | None
    lineup_mode: str
    home: TeamReport | None
    away: TeamReport | None
    blockers: tuple[str, ...]


def _side(team: dict, expected: str, mode: str, fetched_at: datetime) -> TeamReport:
    if _id(team.get("id")) != expected:
        raise ValueError("news_team_identity_mismatch")
    starters = None
    if mode in ("confirmed", "predicted"):
        squad = team.get("starters")
        ids = [_id(p.get("id")) for p in squad if isinstance(p, dict)] if isinstance(squad, list) else []
        if len(ids) != 11 or len(squad) != 11 or None in ids or len(set(ids)) != 11:
            raise ValueError("news_lineup_incomplete")
        starters = tuple(sorted(ids))
    missing = team.get("unavailable")
    out, uncertain = {}, {}
    if missing is not None and not isinstance(missing, list):
        raise ValueError("news_unrecognized_availability")
    if isinstance(missing, list):
        for row in missing:
            pid = _id(row.get("id")) if isinstance(row, dict) else None
            detail = row.get("unavailability") if pid else None
            if not isinstance(detail, dict) or detail.get("type") not in _ABSENCES:
                raise ValueError("news_unrecognized_availability")
            if detail.get("lastUpdated") is not None:
                updated = _instant(detail.get("lastUpdated"))
                if updated is None or updated > fetched_at:
                    raise ValueError("news_invalid_update_time")
            expected_return = detail.get("expectedReturn") or ""
            if not isinstance(expected_return, str):
                raise ValueError("news_unrecognized_availability")
            reasons = uncertain if any(x in expected_return.casefold() for x in _UNCERTAIN) else out
            cause = detail["type"]
            if pid in reasons and reasons[pid] != cause:
                raise ValueError("news_conflicting_availability")
            reasons[pid] = cause
    if out.keys() & uncertain.keys() or out.keys() & set(starters or ()):
        raise ValueError("news_starters_or_status_conflict")
    return TeamReport(expected, starters, tuple(sorted(out.items())), tuple(sorted(uncertain.items())),
                      isinstance(missing, list))


def _team_players(side: TeamReport) -> set[str]:
    return set(side.starters or ()) | dict(side.unavailable).keys() | dict(side.uncertain).keys()


def parse_prematch_news(fixture: Fixture, raw: bytes, *, source_url: str, fetched_at: datetime,
                        registered_sha256: str, as_of: datetime) -> TeamNews:
    """Read registered original bytes. A blocked snapshot cannot condition a forecast."""
    identity = fixture_identity(fixture)
    digest = None
    try:
        if fetched_at.utcoffset() is None or as_of.utcoffset() is None:
            raise ValueError("news_timestamp_naive")
        if source_url != match_url(fixture.fixture_id) or hashlib.sha256(raw).hexdigest() != registered_sha256:
            raise ValueError("news_unbound_source")
        digest = registered_sha256
        if not fetched_at <= as_of < fixture.kickoff or as_of - fetched_at > NEWS_MAX_AGE:
            raise ValueError("news_outside_prematch_window")
        source = json.loads(raw)
        general, header = source["general"], source["header"]
        status = header["status"]
        league_id = general.get("parentLeagueId") or general["leagueId"]
        teams = [item["id"] for item in header["teams"]]
        if (fixture.league not in LEAGUES or _id(league_id) != str(LEAGUES[fixture.league].fotmob_id)
                or _id(general["matchId"]) != fixture.fixture_id
                or [_id(general[name]["id"]) for name in ("homeTeam", "awayTeam")] != [fixture.home_id, fixture.away_id]
                or [_id(t) for t in teams] != [fixture.home_id, fixture.away_id]
                or _instant(general["matchTimeUTCDate"]) != fixture.kickoff
                or _instant(status["utcTime"]) != fixture.kickoff):
            raise ValueError("news_fixture_identity_mismatch")
        if (fixture.status != "scheduled" or source.get("ongoing") is not False
                or any(general.get(k) is not False for k in ("started", "finished"))
                or any(status.get(k) is not False for k in ("started", "finished", "cancelled"))
                or status.get("postponed") or status.get("awarded")):
            raise ValueError("news_not_prematch")
        lineup = source["content"].get("lineup")
        if not isinstance(lineup, dict):
            raise ValueError("news_unpublished")
        if _id(lineup.get("matchId")) != fixture.fixture_id:
            raise ValueError("news_fixture_identity_mismatch")
        mode = _LINEUPS.get(lineup.get("lineupType"), "unpublished")
        if mode in ("confirmed", "predicted") and lineup.get("source") == "lastStartingLineups":
            raise ValueError("news_lineup_source_conflict")
        home = _side(lineup["homeTeam"], fixture.home_id, mode, fetched_at)
        away = _side(lineup["awayTeam"], fixture.away_id, mode, fetched_at)
        if _team_players(home) & _team_players(away):
            raise ValueError("news_player_team_conflict")
        return TeamNews(identity, fetched_at, digest, mode, home, away, ())
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        reason = str(exc) if type(exc) is ValueError and str(exc).startswith("news_") else "news_invalid_document"
        return TeamNews(identity, fetched_at, digest, "unavailable", None, None, (reason,))
