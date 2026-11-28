"""Identity, phase and observation-time boundaries for the five research leagues."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json

import pytest

from polymarket_alpha_lab.papersim import euro_soccer_sources as source


NOW = datetime(2026, 11, 28, 6, tzinfo=UTC)
PAST = NOW - timedelta(days=7)
FUTURE = NOW + timedelta(days=1)


def row(ident=1, *, home=10, away=11, kickoff=PAST, number=1, finished=True):
    status = {"utcTime": kickoff.isoformat(), "started": finished, "finished": finished, "cancelled": False}
    if finished:
        status.update(reason={"short": "FT"}, scoreStr="2 - 0")
    return {"id": str(ident), "home": {"id": str(home), "name": f"Team {home}"},
            "away": {"id": str(away), "name": f"Team {away}"}, "round": str(number), "status": status}


def calendar(league, period, matches):
    league_spec = source.LEAGUES[league]
    return json.dumps({"details": {"id": league_spec.fotmob_id, "country": league_spec.country,
                                   "gender": "male", "type": "league", "selectedSeason": period},
                       "fixtures": {"allMatches": matches}}).encode()


def parse(matches, league="por", period="2026/2027", at=NOW):
    return source.parse_calendar(league, period, calendar(league, period, matches), observed_at=at)


def report(fixture):
    teams = [{"id": int(fixture.home_id), "score": 2}, {"id": int(fixture.away_id), "score": 0}]
    lineup = {"matchId": fixture.fixture_id, "lineupType": "standard"}
    for i, (k, team) in enumerate(zip(("homeTeam", "awayTeam"), teams, strict=True)):
        lineup[k] = {"id": team["id"], "starters": [{"id": t} for t in range(100+11*i, 111+11*i)]}
    status = {"finished": True, "started": True, "reason": {"short": "FT"}, "utcTime": fixture.kickoff.isoformat()}
    return {"general": {"matchId": fixture.fixture_id, "matchTimeUTCDate": fixture.kickoff.isoformat(),
                        "finished": True, "leagueId": 749400, "parentLeagueId": 61,
                        "homeTeam": teams[0], "awayTeam": teams[1]},
            "header": {"teams": teams, "status": status},
            "content": {"stats": {"Periods": {"All": {"stats": [{"stats": [
                {"title": "Expected goals (xG)", "stats": ["1.75", "0.0"]}]}]}}}, "lineup": lineup}}


def test_five_seasons_and_urls_follow_season_rollovers():
    june = datetime(2026, 6, 30, 23, 59, tzinfo=UTC)
    for league in ("scop", "por", "bel1", "tur"):
        assert source.seasons_for(league, june) == ("2024/2025", "2025/2026")
        assert source.seasons_for(league, june + timedelta(minutes=1)) == ("2025/2026", "2026/2027")
    assert source.seasons_for("nor", june) == ("2025", "2026")
    assert source.seasons_for("nor", datetime(2027, 1, 1, tzinfo=UTC)) == ("2026", "2027")
    assert source.calendar_url("nor", "2026").endswith("id=59&season=2026")
    assert source.calendar_url("scop", "2026/2027").endswith("id=64&season=2026%2F2027")
    assert source.match_url("123").endswith("matchId=123")
    with pytest.raises(ValueError):
        source.calendar_url("nor", "2026/2027")
    with pytest.raises(ValueError):
        source.calendar_url("por", "2025/2028")
    with pytest.raises(ValueError):
        source.seasons_for("nor", june.replace(tzinfo=None))
    with pytest.raises(ValueError):
        source.match_url("123&league=1")


def test_source_binds_league_season_phase_and_raw_bytes():
    prematch = row(22, number=3, finished=False, kickoff=FUTURE)
    split = row(23, number=34, finished=False, kickoff=FUTURE)
    completed = row(20, number=2)
    missing = row(24, number=4, finished=False, kickoff=FUTURE)
    missing["status"]["postponed"] = True
    awarded = row(25)
    awarded["status"]["awarded"] = True
    wrong_league = row(26) | {"parentLeagueId": 999}
    friendly = row(27) | {"isFriendly": True}
    wrong_period = row(28)
    wrong_period["status"]["reason"]["short"] = "AET"
    blob = calendar("scop", "2026/2027", [split, prematch, completed, missing, awarded, wrong_league, friendly, wrong_period])
    data = source.parse_calendar("scop", "2026/2027", blob, observed_at=NOW)
    assert [(x.fixture_id, x.phase, x.status, x.score) for x in data] == [
        ("20", "regular", "finished", (2, 0)), ("22", "regular", "scheduled", None), ("23", "split", "scheduled", None)]
    assert all(x.observed_at == NOW and x.source_sha256 == sha256(blob).hexdigest() for x in data)
    for league, period in [("por", "2026/2027"), ("scop", "2025/2026")]:
        with pytest.raises(ValueError):
            source.parse_calendar(league, period, blob, observed_at=NOW)
    with pytest.raises(ValueError, match="duplicate fixture"):
        parse([completed, completed])


def belgian_calendar():
    sides, pairs = list(range(1, 17)), []
    for _ in range(15):
        pairs.append(list(zip(sides[:8], reversed(sides[8:]), strict=True)))
        sides = [sides[0], sides[-1], *sides[1:-1]]
    pairs.extend([[(away, home) for home, away in group] for group in pairs])
    start = datetime(2025, 7, 25, 18, tzinfo=UTC)
    return [row(100 + 8*i+j, home=h, away=a, number=i+1, kickoff=start+timedelta(days=i*7))
            for i, group in enumerate(pairs) for j,(h,a) in enumerate(group)]


def test_belgium_rounds_restart_after_validated_double_round_robin():
    regular = belgian_calendar()
    group = row(999, home=1, away=16, number=1, kickoff=datetime(2026, 4, 5, tzinfo=UTC))
    knockout = row(1000, number="final", kickoff=datetime(2026, 5, 25, tzinfo=UTC))
    fixtures = parse([knockout, group, *reversed(regular)], "bel1", "2025/2026")
    assert sum(f.phase == "regular" for f in fixtures) == 240
    assert [(f.fixture_id, f.phase) for f in fixtures[-2:]] == [("999", "split"), ("1000", "playoff")]
    with pytest.raises(ValueError, match="partition"):
        parse([group, *regular[1:]], "bel1", "2025/2026")
    new = parse([row(number=34)], "bel1")
    assert new[0].phase == "regular"


def test_full_time_detail_binds_identity_and_preserves_actual_source_time():
    fixture, = parse([row()])
    doc = report(fixture)
    raw = json.dumps(doc).encode()
    result = source.parse_history_detail(fixture, raw, observed_at=NOW)
    assert (result.home_xg, result.away_xg, result.source_sha256) == (1.75, 0.0, sha256(raw).hexdigest())
    assert result.home_starters == tuple(str(i) for i in range(100,111))
    assert result.away_starters == tuple(str(i) for i in range(111,122))
    doc["content"].pop("stats")
    doc["content"]["lineup"]["lineupType"] = "predicted"
    unavailable = source.parse_history_detail(fixture, json.dumps(doc).encode(), observed_at=NOW)
    assert unavailable.home_xg is None and unavailable.home_starters is None
    with pytest.raises(ValueError):
        source.parse_history_detail(fixture, raw, observed_at=PAST-timedelta(seconds=1))


@pytest.mark.parametrize("field", ["teams", "kickoff", "score", "parent", "awarded"])
def test_conflicting_full_time_identity_is_rejected(field):
    fixture, = parse([row()])
    doc = report(fixture)
    if field == "teams":
        doc["header"]["teams"].reverse()
    if field == "kickoff":
        doc["general"]["matchTimeUTCDate"] = FUTURE.isoformat()
    if field == "score":
        doc["header"]["teams"][0]["score"] = 1
    if field == "parent":
        doc["general"]["parentLeagueId"] = 999
    if field == "awarded":
        doc["header"]["status"]["awarded"] = True
    with pytest.raises(ValueError):
        source.parse_history_detail(fixture, json.dumps(doc).encode(), observed_at=NOW)


def test_point_in_time_selection_and_bounded_recent_teams():
    matches = parse([row(1, home=10, away=15, kickoff=PAST-timedelta(days=1)), row(2, home=10, away=15),
                     row(3, home=14, away=11), row(4, home=10, away=11, kickoff=FUTURE, finished=False)])
    old, home, away, target = matches
    other = replace(old, fixture_id="7", phase="split")
    outside = replace(old, fixture_id="8", league="scop")
    future = replace(old, fixture_id="9", observed_at=NOW + timedelta(minutes=1))
    reverse = replace(old, fixture_id="12", home_id=target.away_id, away_id=target.home_id, kickoff=PAST+timedelta(hours=2))
    all_rows = [old, home, away, target, other, outside, future, reverse]
    selected = source.recent_fixtures(target, all_rows, as_of=NOW, per_team=1)
    assert tuple(x.fixture_id for x in selected) == ("2", "3")

    home_detail = source.parse_history_detail(home, json.dumps(report(home)).encode(), observed_at=NOW)
    late = replace(home_detail, fixture=away, observed_at=NOW+timedelta(minutes=1))
    history = source.team_history(target, all_rows, {"2": home_detail, "3": late}, as_of=NOW)
    assert [(x.fixture.fixture_id, x.home_xg) for x in history] == [
        ("1", None), ("2", 1.75), ("3", None), ("12", None)]
    assert history[0].source_sha256 == old.source_sha256
    assert history[2].source_sha256 == away.source_sha256
    with pytest.raises(ValueError):
        source.team_history(target, all_rows, {}, as_of=target.kickoff)
    with pytest.raises(ValueError):
        source.recent_fixtures(target, all_rows + [replace(home, score=(0,0))], as_of=NOW)
