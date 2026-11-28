"""Two pre-kickoff team-news observations and the FT result on a private research database."""

from datetime import timedelta
from hashlib import sha256
import json

import pytest

from polymarket_alpha_lab.papersim import cli, euro_soccer_store, euro_soccer_study
from polymarket_alpha_lab.papersim import euro_soccer_sources as sources
from tests import test_papersim_euro_soccer_markets as quotes
from tests import test_papersim_euro_soccer_model as squads
from tests import test_papersim_euro_soccer_sources as schedules
from tests.test_papersim_euro_soccer_store import NATIVE
from tests.test_papersim_report_store_native import _managed_test_postgres, repo  # noqa: F401


def _body(value):
    return json.dumps(value, sort_keys=True).encode()


def _historical_inputs():
    rows, reports = [], {}
    for item in squads.history():
        fixture = item.fixture
        home, away = fixture.score
        row = schedules.row(fixture.fixture_id, home=fixture.home_id, away=fixture.away_id, kickoff=fixture.kickoff)
        row["status"]["scoreStr"] = f"{home} - {away}"
        rows.append(row)
        report = schedules.report(fixture)
        for side, goals in zip(report["header"]["teams"], (home, away), strict=True):
            side["score"] = goals
        xg = report["content"]["stats"]["Periods"]["All"]["stats"][0]["stats"][0]
        xg["stats"] = [str(home), str(away)]
        lineup = report["content"]["lineup"]
        for key, players in (("homeTeam", item.home_starters), ("awayTeam", item.away_starters)):
            if players is not None:
                lineup[key] = squads.team(lineup[key]["id"], players)
            else:
                lineup[key] = squads.team(lineup[key]["id"], range(701, 712))
        reports[sources.match_url(fixture.fixture_id)] = _body(report)
    return rows, reports


@NATIVE
def test_observed_lineup_and_books_settle_into_paired_cli_status(repo, monkeypatch, capsys):
    tip = quotes.KICKOFF
    rows, details = _historical_inputs()
    old = schedules.row(800, home=80, away=81, kickoff=tip - timedelta(days=365))
    news = squads.payload()
    news["general"]["matchTimeUTCDate"] = news["header"]["status"]["utcTime"] = tip.isoformat()
    news["content"]["lineup"]["homeTeam"]["unavailable"] = [squads.absence("209", reason="suspension")]
    news_raw = _body(news)
    details[sources.match_url("1")] = news_raw
    books = quotes.PublicData()
    now = [tip - timedelta(minutes=25)]

    def clock():
        return now[0]

    def download(request):
        url = request["url"]
        if url == sources.calendar_url("por", "2025/2026"):
            return schedules.calendar("por", "2025/2026", [old])
        if url == sources.calendar_url("por", "2026/2027"):
            finished = clock() > tip
            target = schedules.row(1, home=10, away=20, kickoff=tip, finished=finished)
            target["home"]["name"], target["away"]["name"] = "Benfica", "Porto"
            if finished:
                target["status"]["scoreStr"] = "2 - 1"
            return schedules.calendar("por", "2026/2027", [*rows, target])
        if url in details:
            return details[url]
        books.gam_at = books.book_at = books.received = clock()
        return _body(books.fetch(url, request["body"]).payload)

    monkeypatch.setattr(euro_soccer_store, "_utc_now", clock)
    monkeypatch.setattr(euro_soccer_store, "_download", download)
    with _managed_test_postgres(repo[0], monkeypatch) as (_, conn):
        study = euro_soccer_store.StudyStore(conn)
        for slot, minutes in (("forward", 25), ("closing", 2)):
            now[0] = tip - timedelta(minutes=minutes)
            findings = euro_soccer_study.scan_league(study, "por", clock=clock)
            assert findings == {"league": "por", "captured": {slot: 1}, "results_added": 0, "gaps": []}
        now[0] = tip + timedelta(hours=2)
        assert euro_soccer_study.scan_league(study, "por", clock=clock)["results_added"] == 1
        assert euro_soccer_study.scan_league(study, "por", clock=clock)["results_added"] == 0
        records = {key.rsplit("/", 1)[-1]: doc for key, doc in study.records(euro_soccer_study.MODEL_VERSION + "/")}
        assert set(records) == {"forward", "closing", "result"}
        assert all(doc["research_only"] and doc["amount_cny"] == 0 for doc in records.values())
        assert records["result"]["score"] == [2, 1] and len(books.batches) == 2
        assert all(len(set(tokens)) == 8 for tokens in books.batches)
        assert cli.main(["euro-soccer", "status", "--repo-root", str(repo[0])]) == 0
        status = json.loads(capsys.readouterr().out)["leagues"]["por"]
        assert status["observed_fixtures"] == status["context_fixtures"] == status["result_fixtures"] == 1
        for slot in ("forward", "closing"):
            observation, scored = records[slot], records["result"][slot]
            model, market = observation["forecast"], observation["markets"]
            assert model["news"]["lineup_mode"] == "confirmed" and model["news"]["blockers"] == []
            assert model["news"]["source_refs"] == [sha256(news_raw).hexdigest()]
            assert model["context"]["home_win"] > model["baseline"]["home_win"]
            assert market["status"] == "complete" and market["window"] == slot
            assert scored["receipt"]["book_snapshot_sha256"] == market["book_snapshot_sha256"]
            assert scored["missing"] == {} and scored["receipt"]["result_source_sha256"] == records["result"]["calendar_sha256"]
            for family, scores in scored["scores"].items():
                assert set(scores) == {"baseline", "context", "market"}
                expected_market = 2/3 if family == "match_winner" else 0.25
                assert scores["market"]["brier"] == pytest.approx(expected_market)
                for name in ("baseline", "context"):
                    p = model[name]
                    expected = ((1 - p["home_win"])**2 + p["draw"]**2 + p["away_win"]**2
                                if family == "match_winner" else (1 - p["over_2_5"])**2)
                    assert scores[name]["brier"] == pytest.approx(expected)
                    paired = status["paired_brier"][slot][family][name + "_vs_market"]
                    assert paired["pairs"] == 1 and paired["model_mean"] == pytest.approx(expected)
                    assert paired["delta_model_minus_market"] == pytest.approx(expected - expected_market)
