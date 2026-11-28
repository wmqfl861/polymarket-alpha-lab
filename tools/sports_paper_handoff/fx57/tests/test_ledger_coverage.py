"""Replay thin, thick and pre-evidence positions in a disposable CNY ledger."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json

import pytest

import sports_paper_pricing as pricing
import sports_portfolio as portfolio


@pytest.mark.parametrize("thin_shares", ["1", "55"], ids=["one-share", "notional-and-fees-differ"])
def test_coverage_groups_show_amount_and_realized_pnl_without_changing_paper_prices(account, monkeypatch, capsys,
                                                                                   thin_shares):
    db, key = account
    now = datetime(2026, 11, 29, 12, tzinfo=UTC)
    observed = now
    tip = now + timedelta(hours=1)
    db.execute("UPDATE football_notify.sports_portfolio_account SET started_at=%s WHERE restart_key=%s",
               (now - timedelta(minutes=1), key))
    monkeypatch.setattr(portfolio.store, "db_now", lambda _: now)

    def quote(fixture_key, sport, shares):
        token = {"thin": "701", "thick": "702", "old": "703"}[fixture_key]
        condition = "synthetic-" + fixture_key
        signal = {"price_basis": "best_ask"}
        if shares is not None:
            book = {"asset_id": token, "market": condition, "timestamp": str(int(observed.timestamp() * 1000)),
                    "bids": [{"price": ".38", "size": "30"}],
                    "asks": [{"price": ".4", "size": shares}, {"price": ".8", "size": "5000"}]}
            signal.update(pricing.book_evidence(book, token_id=token, condition_id=condition, observed_at=observed))
        return portfolio.Quote(sport=sport, fixture_key=fixture_key, market_id=token, outcome="Yes",
                               model_probability=".62", price=".4", fee_rate=".05", observed_at=observed,
                               valid_until=observed + timedelta(minutes=5), kickoff=tip, token_id=token,
                               fixture_revision=1, model_version="synthetic", condition_id=condition,
                               settlement_evidence={"signal": signal})

    quotes = [quote("thin", "football", thin_shares), quote("thick", "basketball", "500"), quote("old", "football", None)]
    planned = portfolio.plan(db, quotes, restart_key=key)
    booked = portfolio.enter(db, quotes, restart_key=key, round_key="one", expected=planned)
    assert booked == planned == portfolio.enter(db, [], restart_key=key, round_key="one", expected=planned)
    assert {a.stake_fen for a in booked} == {15000}
    assert {a.quote.price for a in booked} == {Decimal(".4")}
    assert all(abs(a.units * Decimal(".412") - Decimal(150)) < Decimal("1e-20") for a in booked)
    assert [a.quote.settlement_evidence["best_ask_coverage"]["status"] for a in booked] == [
        "insufficient", "sufficient", "unknown"]

    # This scratch row emulates the on-disk shape before capacity capture existed.
    db.execute("""UPDATE football_notify.sports_portfolio_position
                  SET settlement_evidence=settlement_evidence-'best_ask_coverage'
                  WHERE restart_key=%s AND fixture_key='old'""", (key,))
    now = tip + timedelta(minutes=10)
    for q, result in zip(quotes, ("loss", "win"), strict=False):
        portfolio.settle(db, sport=q.sport, fixture_key=q.fixture_key, market_id=q.market_id, outcome=q.outcome,
                         result=result, evidence={"source": "synthetic-result"}, resolved_at=now, restart_key=key)
    statement = {row["fixture_key"]: row for row in portfolio.statement(db, restart_key=key)}
    fee_adjusted = statement["thin"]["best_ask_coverage"]["fee_inclusive_share_status"]
    assert fee_adjusted == ("sufficient" if thin_shares == "55" else "insufficient")
    assert statement["thin"]["best_ask_size"] == thin_shares and statement["thin"]["best_ask_price"] == "0.4"
    assert len(statement["thin"]["book_snapshot_sha256"]) == 64
    assert statement["old"]["best_ask_coverage"] == {"status": "unknown", "reason": "missing_recorded_coverage"}
    account = portfolio.status(db, restart_key=key)
    groups = account["by_best_ask_coverage"]
    for name in ("insufficient", "sufficient", "unknown"):
        assert (groups[name]["entries"], groups[name]["staked_fen"]) == (1, 15000)
    assert (groups["insufficient"]["losses"], groups["insufficient"]["realized_pnl_fen"]) == (1, -15000)
    assert groups["sufficient"]["realized_pnl_fen"] == statement["thick"]["pnl_fen"] > 0
    assert (groups["unknown"]["open_positions"], groups["unknown"]["realized_pnl_fen"]) == (1, 0)
    assert account["realized_pnl_fen"] == sum(group["realized_pnl_fen"] for group in groups.values())
    monkeypatch.setattr(portfolio.store, "connect", lambda **_: nullcontext(db))
    portfolio.main(["status", "--restart-key", key])
    report = json.loads(capsys.readouterr().out)["by_best_ask_coverage"]
    assert report["insufficient"]["staked_cny"] == "¥150.00"
    assert report["insufficient"]["realized_pnl_cny"] == "-¥150.00"
    assert report["sufficient"]["realized_pnl_cny"] == "¥214.08"
    assert report["unknown"]["at_risk_cny"] == "¥150.00" and report["unknown"]["realized_pnl_cny"] == "¥0.00"
