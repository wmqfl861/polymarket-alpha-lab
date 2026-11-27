"""Replay a registered soccer scope against thin, fresh synthetic quotes."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import football_daily as daily
import football_feed as feed
import football_research as research
import football_shortlist as shortlist


AS_OF = datetime(2026, 11, 22, 15, tzinfo=UTC)


KICKOFF = datetime(2026, 11, 22, 21, 30, tzinfo=UTC)


FIXTURE = {
    "id": "gamma:bra:90104950", "league": "bra", "state": "scheduled",
    "kickoff": research.utc_iso(KICKOFF), "home": "Fluminense FC", "away": "Mirassol FC",
}


def forecast_cache():
    block = {
        "calendar_at": research.utc_iso(AS_OF), "sha256": "a" * 64,
        "source": "https://www.fotmob.com/api/data/leagues?id=268&season=2026",
        "matches": [["5103714", FIXTURE["kickoff"], "9863", "975619", "fluminense", "mirassol"]],
        "results": [
            ["5103700", "2026-11-19T20:00:00Z", "fluminense", "mirassol", 0, 2],
            ["5103701", "2026-11-21T20:00:00Z", "fluminense", "mirassol", 88, 0],
        ],
    }
    return {"research": {"version": 1, "seasons": {"bra:2026": block}}}


def test_admitted_forecast_reaches_the_shortlist_by_exact_line_and_version():
    kickoff = KICKOFF
    now = kickoff - timedelta(minutes=29)
    reference = research._archive().models["bra"]
    candidate = replace(reference, holdout_start=date(2026, 7, 1), inputs_sha256="b" * 64)
    cache = forecast_cache()
    cache["research"]["seasons"]["bra:2026"]["calendar_at"] = feed.utc_iso(now)
    cache["research"]["model_registry"] = {
        candidate.version: {"registered_at": feed.utc_iso(now - timedelta(days=1)), "spec": candidate.spec}}
    saved = research.available_forecasts(FIXTURE, now, cache)[0].fixture
    scope = ("bra", "total", Decimal("3.5"), None, candidate.version)
    info = saved | {"base_slug": "bra-flu-mir-2026-11-22", "_paper_scopes": {scope}}
    row = {"info": info, "key": "test-t30", "fixture_key": info["id"], "revision": 1, "watch": True,
           "stage": "t30", "kickoff": kickoff, "starts_at": kickoff - timedelta(minutes=30),
           "deadline": kickoff - timedelta(minutes=15), "fixture_state": "scheduled", "legacy_id": None}
    raw = {"id": "1234", "description": feed._REGULATION, "slug": info["base_slug"] + "-total-3pt5",
           "feesEnabled": True, "feeSchedule": {"takerOnly": True, "exponent": 1, "rate": .05},
           "liquidityNum": 1}
    option = feed.Selection("小于 3.5 球", "Under", "901", "under", .35)
    market = feed.Market(info["base_slug"], raw, raw["id"], "0x" + "e" * 64, "total", 3.5, None, (option,))
    book = ([(.35, .1), (.75, 5000)], now - timedelta(seconds=1))
    news = feed.gates.check_news(info["id"], "T-30", news_delta=feed.gates.NEWS_UNCHANGED)
    with (patch.object(feed, "_clock", return_value=lambda: now),
          patch.object(feed, "_gamma_event_refs", return_value=["fixture"]),
          patch.object(feed, "_refresh_event", return_value={"active": True, "closed": False, "startTime": info["kickoff"]}),
          patch.object(feed, "_fotmob_evidence", return_value=("verified", {"observed_at": feed.utc_iso(now)}, None)),
          patch.object(feed, "_event_markets", return_value=[market]),
          patch.object(feed, "_quoted_markets", return_value=([(market, option, book)], False)),
          patch.object(feed, "_news", return_value=(news, "verified")),
          patch.object(feed, "_verify_live_contract", return_value=raw)):
        decision = daily.fetch_review(row, now, preview=True)
        assert decision["status"] == "paper", decision
        assert {s["model_version"] for s in decision["signals"]} == {candidate.version}
        assert decision["signal"]["line"] == 3.5
        selected, reasons = shortlist._rank_round([row], {row["key"]: decision}, now)
        assert reasons[row["key"]] == "qualified"
        assert [entry.direction for entry in selected] == ["小于 3.5 球"]
        entry, = selected
        assert entry.price == .35 and entry.quote.price == Decimal(".35")
        assert entry.quote.settlement_evidence["signal"]["price_basis"] == "best_ask"
        assert entry.quote.value.break_even_probability == Decimal(".35") * (1 + Decimal(".05") * Decimal(".65"))

        for other in (scope[:2] + (Decimal("2.5"), None, candidate.version),
                      ("bra", "total", Decimal("3.5"), None, "unregistered"),
                      ("uru1", "total", Decimal("3.5"), None, candidate.version)):
            changed = row | {"info": info | {"_paper_scopes": {other}}}
            assert daily.fetch_review(changed, now, preview=True)["status"] != "paper"
            kept, reasons = shortlist._rank_round([changed], {row["key"]: decision}, now)
            assert kept == [] and reasons[row["key"]] in {"market_not_admitted", "unsupported_league"}
