"""Displayed-price simulation, paired soccer quotes and registered research pricing."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from polymarket_alpha_lab.papersim import gates

import football_feed as feed
import football_focus as focus
import football_forward as research
import sports_paper_pricing as pricing
import sports_portfolio as portfolio


NOW = datetime(2026, 11, 27, 15, tzinfo=UTC)
NEWS = gates.check_news("test-match", "t30", news_delta=gates.NEWS_UNCHANGED)


def test_best_ask_requires_valid_positive_resting_size():
    bad = [(.01, 0), (.08, -1), (.02, float("nan")), (.001, float("inf")), (.04, True),
           {"price": "nan", "size": 10}, {"price": "inf", "size": 1}, (1, 20), (0, 20), {}]
    assert pricing.best_ask(bad) is None
    assert pricing.best_ask(iter([(.55, 9000), *bad, {"price": ".36001", "size": ".001"}])) == .36001
    assert pricing.evaluate_candidate({"model_median": .62, "qs_variants": [.62]}, bad,
                                     news=NEWS, fee_rate=.05) is None


@pytest.mark.parametrize("median,variants,price,fee,risk,news", [
    (.63, [.61, .65], .42, .05, False, NEWS),
    (.50, [.49, .50], .40, .05, False, NEWS),
    (.63, [.58, .68], .42, .05, False, NEWS),
    (.63, [.60, .65], .57, .05, False, NEWS),
    (.63, [.61, .65], .55, .05, True, NEWS),
    (.63, [.61, .65], .05, .05, False, NEWS),
    (.63, [.61, .65], .42, .05, False, None),
])
def test_displayed_price_keeps_every_other_gate(median, variants, price, fee, risk, news):
    quote = {"model_median": median, "qs_variants": variants}
    reference = gates.evaluate_candidate(quote, [(price, 10000)], gamma_liquidity=9000,
                                         fee_rate=fee, news=news, under_risk=risk)
    for asks in ([(price, .01)], [(.90, 10000), (price, .01)], [(price, 10000)]):
        daily = pricing.evaluate_candidate(quote, asks, fee_rate=fee, news=news, under_risk=risk)
        assert daily.entry_price == price
        assert daily.decision == reference.decision
        assert (daily.exec_edge, daily.exec_edge_haircut, daily.worst_variant_edge, daily.variant_spread) == (
            reference.exec_edge, reference.exec_edge_haircut, reference.worst_variant_edge, reference.variant_spread)


def test_soccer_pair_uses_displayed_ask_but_registered_research_still_requires_depth():
    home = feed.Selection("主队胜", "Yes", "901", "home", .40)
    away = feed.Selection("主队不胜", "No", "902", "home_no", .62)
    live = {"feesEnabled": True, "feeSchedule": {"rate": .05, "exponent": 1, "takerOnly": True}}
    market = feed.Market("match-slug", live, "101", "0x" + "a" * 64, "match_winner", None, None, (home, away))
    tip = NOW + timedelta(hours=1)
    def snapshot(opt, ask):
        return {"asset_id": opt.token_id, "market": market.condition_id, "timestamp": str(int(NOW.timestamp()*1000)),
                "bids": [{"price": str(ask - .02), "size": ".02"}],
                "asks": [{"price": str(ask), "size": ".01"}]}, NOW

    books = {"901": snapshot(home, .40), "902": snapshot(away, .62)}
    pair = focus._paired_books(market, books, NOW, tip)
    assert pair is not None
    forecast = {"model_median": .58, "qs_variants": [.58]*10}
    projection = SimpleNamespace(projection=({}, (), (1.65, 1.20)), market_quote=lambda *_: forecast)
    result, failed = focus._screen(market, home, pair["901"], projection, live)
    assert not failed and result[0].entry_price == .40
    evidence = focus._book_evidence(home, pair["901"])
    assert evidence["price"] == .40 and evidence["price_basis"] == "best_ask" and "filled_usd" not in evidence
    assert focus._screen(market, home, pair["901"], projection, {})[0] is None

    quote = research._quote(market.condition_id, "901", *books["901"], tip, NOW - feed.BOOK_AGE)
    assert quote["entry_price"] is None and quote["filled_usd"] < gates.DEFAULT_NOTIONAL_USD
    _, report, reasons = research._gate(market, home, forecast, quote, live, NEWS, projection.projection)
    assert report is None and {"fill_250", "fee_or_liquidity_unverified"} <= set(reasons)
    assert focus._paired_books(market, {"901": books["901"]}, NOW, tip) is None
    assert focus._paired_books(market, books, NOW + feed.BOOK_AGE + timedelta(seconds=1), tip) is None
    wrong, taken = books["901"]
    assert focus._paired_books(market, books | {"901": (wrong | {"asset_id": "999"}, taken)}, NOW, tip) is None


def test_ledger_estimates_simulated_units_at_displayed_ask_including_fee(account):
    db, key = account
    model = {"model_median": .62, "qs_variants": [.59, .63]}
    report = pricing.evaluate_candidate(model, [(.399, .01), (.80, 10000)], news=NEWS, fee_rate=.05)
    assert report.decision.passed
    observed = portfolio.store.db_now(db)
    quoted = portfolio.Quote(
        sport="football", fixture_key="thin-book-fixture", market_id="210", outcome="Yes",
        model_probability=".62", price=str(report.entry_price), fee_rate=".05", observed_at=observed,
        valid_until=observed + timedelta(minutes=5), kickoff=observed + timedelta(hours=1),
        token_id="synthetic-token", fixture_revision=1, model_version="synthetic-model",
        condition_id="synthetic-contract", settlement_evidence={
            "signal": {"price_basis": pricing.PRICE_BASIS, "variants": model["qs_variants"]},
        },
    )
    entry, = portfolio.enter(db, [quoted], round_key="thin-market", restart_key=key)
    position, = portfolio.open_positions(db, restart_key=key)
    unit_cost = Decimal(".399") + Decimal(".05") * Decimal(".399") * Decimal(".601")
    assert entry.stake_fen == 15000 and entry.quote.value.break_even_probability == unit_cost
    assert abs(entry.units * unit_cost - Decimal("150")) < Decimal("1e-18")
    assert position["price"] == Decimal(".399")
    assert position["settlement_evidence"]["signal"]["price_basis"] == pricing.PRICE_BASIS
