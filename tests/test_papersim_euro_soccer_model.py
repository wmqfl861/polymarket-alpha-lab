"""Team form, observed news and point-in-time boundaries for the five-league study."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json

import pytest

from polymarket_alpha_lab.papersim import euro_soccer_model as model
from polymarket_alpha_lab.papersim.euro_soccer_sources import Fixture, MatchHistory, match_url


NOW = datetime(2026, 11, 28, 12, tzinfo=UTC)
TARGET = Fixture("por", "2026/2027", "regular", "1", "10", "20", "Home", "Away", NOW + timedelta(hours=1),
                 "scheduled", None, NOW, "a" * 64)
HOME_XI = tuple(str(i) for i in range(101, 112))
ALTERNATE = HOME_XI[:2] + tuple(str(i) for i in range(201, 210))
AWAY_XI = tuple(str(i) for i in range(401, 412))


def team(team_id, players):
    return {"id": int(team_id), "starters": [{"id": int(p)} for p in players], "unavailable": []}


def payload(mode="standard"):
    tip = TARGET.kickoff.isoformat()
    return {
        "general": {"matchId": "1", "parentLeagueId": 61, "leagueId": 61, "matchTimeUTCDate": tip,
                    "homeTeam": {"id": 10}, "awayTeam": {"id": 20}, "started": False, "finished": False},
        "header": {"teams": [{"id": 10}, {"id": 20}], "status": {"utcTime": tip, "started": False,
                   "finished": False, "cancelled": False}},
        "ongoing": False,
        "content": {"lineup": {"matchId": 1, "lineupType": mode,
                               "homeTeam": team(10, HOME_XI), "awayTeam": team(20, AWAY_XI)}},
    }


def capture(doc, **replacements):
    raw = json.dumps(doc).encode()
    kwargs = {"source_url": match_url(TARGET.fixture_id), "fetched_at": NOW, "as_of": NOW,
              "registered_sha256": sha256(raw).hexdigest()} | replacements
    return model.parse_prematch_news(TARGET, raw, **kwargs)


def history():
    rows = []
    for i, home_players in enumerate([HOME_XI, HOME_XI, ALTERNATE, ALTERNATE]):
        tip = NOW - timedelta(days=10+i)
        old = replace(TARGET, fixture_id=str(i+30), away_id=str(i+100), kickoff=tip,
                      status="finished", score=(4-i, i))
        rows.append(MatchHistory(old, 4.-i, float(i), home_players, None, NOW, "b"*64))
        old = replace(old, fixture_id=str(i+40), home_id=str(i+100), away_id=TARGET.away_id, score=(1, 1))
        rows.append(MatchHistory(old, None, None, None, AWAY_XI, NOW, "c"*64))
    return rows


def absence(player, reason="injury", expected="Late January"):
    return {"id": int(player), "unavailability": {"type": reason, "expectedReturn": expected}}


def test_current_eleven_changes_probabilities_with_historical_support():
    standard = model.forecast(TARGET, history(), news=capture(payload()), as_of=NOW)
    alternate = payload("predicted")
    alternate["content"]["lineup"]["homeTeam"] = team(10, ALTERNATE)
    alternate = model.forecast(TARGET, history(), news=capture(alternate), as_of=NOW)
    assert standard["status"] == alternate["status"] == "team_news_scenario"
    assert standard["news"]["lineup_mode"] == "confirmed" and alternate["news"]["lineup_mode"] == "predicted"
    assert standard["teams"]["home"]["matches_with_xg"] == 4
    assert standard["teams"]["home"]["matches_with_lineups"] == 4
    assert standard["baseline"] == alternate["baseline"]
    assert standard["context"]["home_win"] > standard["baseline"]["home_win"] > alternate["context"]["home_win"]
    assert standard["teams"]["away"]["status"] == "no_historical_lineup_contrast"
    for p in (standard["baseline"], standard["context"], alternate["context"]):
        assert p["home_win"] + p["draw"] + p["away_win"] == pytest.approx(1)
        assert p["over_2_5"] + p["under_2_5"] == pytest.approx(1)
    assert standard["research_only"] and standard["amount_cny"] == 0


def test_previous_xi_is_not_a_new_lineup_but_reported_suspension_can_inform_a_scenario():
    doc = payload("lastStarting11")
    report = doc["content"]["lineup"]["homeTeam"]
    news_only = capture(doc)
    assert news_only.home.starters is None
    assert model.forecast(TARGET, history(), news=news_only, as_of=NOW)["context"] is None
    suspended = absence("105", "suspension", "Next match")
    report["unavailable"] = [suspended, suspended]
    news = capture(doc)
    assert news.home.unavailable == (("105", "suspension"),)
    res = model.forecast(TARGET, history(), news=news, as_of=NOW)
    assert res["teams"]["home"]["basis"] == "available_historical_starters"
    assert res["context"]["home_win"] < res["baseline"]["home_win"]
    report["unavailable"] = [absence("105", expected="Doubtful")]
    doubtful = capture(doc)
    assert doubtful.home.uncertain == (("105", "injury"),) and not doubtful.home.unavailable
    assert model.forecast(TARGET, history(), news=doubtful, as_of=NOW)["context"] is None
    del report["unavailable"]
    assert capture(doc).home.absences_known is False


@pytest.mark.parametrize("change", [
    lambda d: d["general"].update(matchId="999"),
    lambda d: d["header"]["teams"].reverse(),
    lambda d: d["general"].update(parentLeagueId=64),
    lambda d: d["header"]["status"].update(utcTime=(TARGET.kickoff + timedelta(hours=1)).isoformat()),
    lambda d: d["header"]["status"].update(finished=True),
    lambda d: d["content"].update(lineup=None),
    lambda d: d["content"]["lineup"].update(source="lastStartingLineups"),
    lambda d: d["content"]["lineup"]["homeTeam"].update(unavailable={"injury": 105}),
    lambda d: d["content"]["lineup"]["homeTeam"].update(unavailable=[absence("105")]),
    lambda d: d["content"]["lineup"]["awayTeam"].update(unavailable=[absence("105")]),
    lambda d: d["content"]["lineup"]["homeTeam"].update(starters=[{"id": 23}] * 11),
    lambda d: d["content"]["lineup"]["homeTeam"].update(unavailable=[{"id": 30, "unavailability": {
        "type": "injury", "lastUpdated": (NOW + timedelta(minutes=1)).isoformat()}}]),
])
def test_identity_lineup_and_temporal_conflicts_block_only_the_news(change):
    doc = payload()
    change(doc)
    news = capture(doc)
    result = model.forecast(TARGET, history(), news=news, as_of=NOW)
    assert news.blockers and result["baseline"] and result["context"] is None
    assert result["status"] == "team_baseline"


def test_source_hash_time_and_target_must_remain_bound_at_forecast_cutoff():
    doc = payload()
    for news in (capture(doc, registered_sha256="d" * 64),
                 capture(doc, source_url=match_url("2")),
                 capture(doc, fetched_at=NOW + timedelta(seconds=1)),
                 capture(doc, fetched_at=NOW - timedelta(minutes=6))):
        assert news.blockers
        assert model.forecast(TARGET, history(), news=news, as_of=NOW)["context"] is None
    news = capture(doc)
    assert set(model.forecast(TARGET, history(), news=news, as_of=NOW)["source_refs"]) == {
        "a" * 64, "b" * 64, "c" * 64, news.source_sha256}
    later = model.forecast(TARGET, history(), news=news, as_of=NOW + timedelta(minutes=6))
    assert later["context"] is None and "news_outside_prematch_window" in later["reasons"]
    other = model.forecast(replace(TARGET, fixture_id="200"), history(), news=news, as_of=NOW)
    assert other["context"] is None and "news_fixture_identity_mismatch" in other["reasons"]


def test_historical_results_and_later_lineups_cannot_leak_into_earlier_cutoff():
    rows = history()
    expected = model.forecast(TARGET, rows, news=None, as_of=NOW)
    later = NOW + timedelta(seconds=1)
    bad = [replace(rows[0], observed_at=later, home_xg=10), rows[0],
           replace(rows[0], fixture=replace(rows[0].fixture, fixture_id="501", league="tur")),
           replace(rows[0], fixture=replace(rows[0].fixture, fixture_id="502", phase="split")),
           replace(rows[0], fixture=replace(rows[0].fixture, fixture_id="503", observed_at=later)),
           replace(rows[0], fixture=replace(rows[0].fixture, fixture_id="504", status="scheduled")),
           replace(rows[0], fixture=replace(rows[0].fixture, fixture_id="505", season="2001/2002"))]
    assert model.forecast(TARGET, bad + rows[::-1], news=None, as_of=NOW) == expected
    assert model.forecast(TARGET, [], news=None, as_of=NOW)["baseline"] is None
    assert model.forecast(TARGET, rows, news=None, as_of=TARGET.kickoff)["baseline"] is None
    assert model.forecast(replace(TARGET, season="2001/2002"), rows, news=None, as_of=NOW)["baseline"] is None


def test_missing_lineup_detail_preserves_score_only_baseline():
    row = history()
    incomplete = [replace(r, home_starters=None) if r.fixture.fixture_id in {"30", "31"} else r for r in row]
    result = model.forecast(TARGET, incomplete, news=capture(payload()), as_of=NOW)
    assert result["baseline"] and result["context"] is None
    assert result["teams"]["home"]["status"] == "insufficient_historical_lineups"
