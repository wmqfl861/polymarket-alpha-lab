"""Soccer formal and zero-stake selected-side book capture."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import football_daily as daily
import football_feed as feed
import football_focus as focus
import football_research as research
import football_shortlist as shortlist
from tools.sports_paper_handoff.tests.test_football_prices import FIXTURE, KICKOFF, forecast_cache


def test_soccer_formal_signal_retains_the_bound_raw_book_through_shortlisting(persist_quote):
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
    observed = now - timedelta(seconds=1)
    book = {"asset_id": option.token_id, "market": market.condition_id,
            "timestamp": str(int(observed.timestamp() * 1000)),
            "asks": [{"price": ".35", "size": "1"}, {"price": ".75", "size": "5000"}],
            "bids": [{"price": ".34", "size": "35"}]}
    news = feed.gates.check_news(info["id"], "T-30", news_delta=feed.gates.NEWS_UNCHANGED)
    with (patch.object(feed, "_clock", return_value=lambda: now),
          patch.object(feed, "_gamma_event_refs", return_value=["fixture"]),
          patch.object(feed, "_refresh_event", return_value={"active": True, "closed": False, "startTime": info["kickoff"]}),
          patch.object(feed, "_fotmob_evidence", return_value=("verified", {"observed_at": feed.utc_iso(now)}, None)),
          patch.object(feed, "_event_markets", return_value=[market]),
          patch.object(feed, "_book_batch", return_value={option.token_id: (book, now)}),
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
        signal = entry.quote.settlement_evidence["signal"]
        assert signal["price_basis"] == "best_ask"
        assert Decimal(signal["best_ask_price"]) == entry.quote.price and signal["best_ask_size"] == "1"
        assert signal["book_snapshot_sha256"] == feed.document_digest(book)
        assert (signal["book_token_id"], signal["book_condition_id"]) == (option.token_id, market.condition_id)
        assert entry.quote.value.break_even_probability == Decimal(".35") * (1 + Decimal(".05") * Decimal(".65"))
        saved = persist_quote(entry.quote, now)
        assert saved["signal"]["book_snapshot_sha256"] == feed.document_digest(book)
        assert saved["signal"]["best_ask_size"] == "1" and saved["signal"]["book_token_id"] == option.token_id


def test_zero_stake_soccer_keeps_selected_book_in_saved_watch_evidence(persist_watch):
    now = datetime(2026, 11, 29, 15, tzinfo=UTC)
    tip = now + timedelta(hours=1)
    home = feed.Selection("主队胜", "Yes", "901", "home", .4)
    away = feed.Selection("主队不胜", "No", "902", "home_no", .62)
    raw = {"slug": "test-winner", "feesEnabled": True,
           "feeSchedule": {"rate": .05, "exponent": 1, "takerOnly": True}}
    market = feed.Market("test-match", raw, "101", "0x" + "a" * 64, "match_winner", None, None, (home, away))
    books = {side.token_id: ({"asset_id": side.token_id, "market": market.condition_id,
                              "timestamp": str(int(now.timestamp() * 1000)),
                              "asks": [{"price": ask, "size": "1"}, {"price": ".84", "size": "200"}],
                              "bids": [{"price": bid, "size": "12"}]}, now)
             for side, ask, bid in ((home, ".4", ".38"), (away, ".62", ".60"))}
    fixture = focus.Fixture({"id": "fixture", "revision": 1, "kickoff": tip, "last_seen": now,
                             "info": {"league": "epl", "home": "Home", "away": "Away",
                                      "fotmob_home_id": "11", "fotmob_away_id": "22"}}, (), {})
    forecast = {"model_median": .58, "qs_variants": [.58] * 10}
    projection = SimpleNamespace(projection=({"observed_at": feed.utc_iso(now), "sha256": "f" * 64}, (), (1.65, 1.2)),
                                 market_quote=lambda *_: forecast)
    lineup = {"observed_at": feed.utc_iso(now), "source": "synthetic-lineup", "sha256": "e" * 64,
              "lineup_type": "predicted"}
    pair = focus._paired_books(market, books, now, tip)
    result, failures = focus._screen(market, home, pair["901"], projection, raw)
    assert not failures
    watch = focus._candidate(fixture, market, home, projection, result, pair, lineup, "watch", raw, now)
    assert watch["stake_fen"] == 0 and watch["book"]["home"]["best_ask_size"] == "1"
    assert watch["book_snapshot_sha256"] == feed.document_digest(books["901"][0])
    assert watch["book_snapshot_sha256"] != watch["book"]["home_no"]["book_snapshot_sha256"]

    saved = persist_watch(watch, now)
    assert {k: saved[k] for k in feed.paper_pricing.BOOK_EVIDENCE_FIELDS} == {
        k: watch[k] for k in feed.paper_pricing.BOOK_EVIDENCE_FIELDS}
    assert saved["stake_fen"] == 0
    assert saved["best_ask_coverage"] == {"status": "not_applicable", "reason": "zero_stake_observation"}
