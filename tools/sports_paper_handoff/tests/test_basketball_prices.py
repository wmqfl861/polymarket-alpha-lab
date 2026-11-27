"""Synthetic contracts exercise the formal basketball review and the zero-stake watch list."""

from datetime import UTC, datetime, timedelta
import json
from unittest.mock import patch

import pytest

import basketball_feed as feed
import basketball_focus as focus
import basketball_models as models


TIP = datetime(2026, 11, 22, 20, 30, tzinfo=UTC)


FIXTURE = {
    'id': 'gamma:basketball:nba:20024157', 'sport': 'basketball', 'league': 'nba',
    'home': 'Hawks', 'away': 'Pistons', 'home_abbr': 'atl', 'away_abbr': 'det',
    'kickoff': feed.utc_iso(TIP), 'state': 'scheduled', 'base_slug': 'nba-det-atl-2026-11-22',
    'events': [{'id': '1338695', 'slug': 'nba-det-atl-2026-11-22'}], 'game_id': '20024157',
}


OFFICIAL = {
    'provider': 'espn:nba', 'id': '401910029', 'home_id': '1', 'away_id': '8', 'home_code': 'ATL', 'away_code': 'DET',
    'kickoff': FIXTURE['kickoff'], 'state': 'scheduled', 'schedule_source': feed.ESPN + '/nba/summary?event=401910029',
}


RULES = ('If the Pistons win, the market will resolve to "Pistons".\n'
         'If the Hawks win, the market will resolve to "Hawks".\n'
         'If the game is canceled, this market will resolve 50-50.\n'
         'The result will be determined based on the final score including any overtime periods.')


RAW = {
    'id': '6840919', 'conditionId': '0x' + '1' * 64, 'slug': FIXTURE['base_slug'], 'question': 'Pistons vs. Hawks',
    'gameStartTime': FIXTURE['kickoff'], 'sportsMarketType': 'moneyline', 'line': None,
    'description': RULES, 'resolutionSource': 'https://www.nba.com/',
    'outcomes': json.dumps(['Pistons', 'Hawks']), 'clobTokenIds': json.dumps(['301', '302']),
    'active': True, 'closed': False, 'archived': False, 'acceptingOrders': True, 'enableOrderBook': True,
    'feesEnabled': True, 'feeSchedule': {'exponent': 1, 'rate': 0.05, 'takerOnly': True}, 'liquidityNum': 12000,
}


def _model(now):
    return models.model_document(FIXTURE, OFFICIAL, models.NBA_VERSION, {
        'variant_names': list(models.nba_model.VARIANT_NAMES), 'distributions': [[8.0, 12.0, 235.5, 17.0]] * 6,
        'expected_total': 235.5, 'expected_margin': 8.0,
    }, now - timedelta(minutes=2))


def test_review_returns_qualifying_nba_signal_with_scopes_and_exact_requoted_price():
    now = TIP - timedelta(minutes=28)
    model = _model(now)
    official = OFFICIAL | {'schedule_observed_at': feed.utc_iso(now)}
    earlier = OFFICIAL | {'schedule_observed_at': feed.utc_iso(TIP - timedelta(hours=2))}
    payload = {'injuries': []}
    fixture = FIXTURE | {'model': model, 'basketball_official': official}
    fixture['basketball_news_baseline'] = feed._news_snapshot(payload, earlier, fixture)
    fixture['_paper_scopes'] = frozenset({('nba', 'match_winner', None, None, models.NBA_VERSION)})
    raw = {key: val for key, val in RAW.items() if key != 'liquidityNum'}
    market = feed._contract(fixture, raw, '1338695', fixture['base_slug'])
    asks = {'301': .57, '302': .45}
    bids = {'301': .55, '302': .43}

    def parse(contract, opt, raw):
        return {'asks': [(asks[opt.token_id], .01), (.85, 10000)], 'bid': bids[opt.token_id], 'ask': asks[opt.token_id],
                'midpoint': (asks[opt.token_id] + bids[opt.token_id]) / 2, 'at': now, 'received': now}

    with patch.object(feed, '_fixture_markets', return_value=('scheduled', None, [market], False)), \
         patch.object(feed, '_official_summary', return_value=(official, payload)), \
         patch.object(feed, '_books', return_value={}), patch.object(feed, '_parsed_book', side_effect=parse), \
         patch.object(feed, '_market_direct', return_value=(raw, now)):
        reviewed = feed.review(fixture, now, 't30')
        forward = feed.review(fixture, now, 'forward')
        watched = feed.review(fixture | {'_paper_scopes': frozenset()}, now, 't30')
    assert watched['status'] == 'watch' and watched['signals'] == []
    assert reviewed['status'] == 'paper'
    assert len(reviewed['signals']) == 1
    signal = reviewed['signal']
    assert signal['token_id'] == '302' and signal['price'] == .45 and signal['fee_rate'] == .05
    assert signal['price_basis'] == 'best_ask' and signal['entry_price'] == signal['price']
    assert 'depth_filled_usd' not in signal
    official_side = next(item for item in forward['observations'] if item['token_id'] == signal['token_id'])
    assert official_side['entry_price'] == .85 and official_side['depth_filled_usd'] == 250
    assert 'fee_depth_or_liquidity_unverified' in official_side['reasons']
    assert signal['direct_contract_verified'] and len(signal['variants']) == 6
    assert signal['valid_until'] == feed.utc_iso(now + feed.common.BOOK_AGE)


def _market_fixture(tip, league="cbb"):
    slug = f"{league}-det-atl-{focus.college.season_day(tip)}"
    info = FIXTURE | {"league": league, "id": "focus-test", "kickoff": feed.utc_iso(tip),
                              "base_slug": slug, "root_event_id": "1338695",
                              "events": [{"id": "1338695", "slug": slug}]}
    metadata = info["events"][0] | {"startTime": info["kickoff"], "active": True, "closed": False,
                                     "gameId": info["game_id"]}
    raw = RAW | {"slug": slug, "gameStartTime": info["kickoff"], "events": [metadata],
                         "resolutionSource": f"https://www.{'nba' if league == 'nba' else 'ncaa'}.com/"}
    return info, raw


def _book(now, price, depth=2500):
    return {"bids": [(price - .02, depth)], "asks": [(price, depth)], "bid": price - .02, "ask": price,
            "at": now - timedelta(seconds=1), "received": now, "midpoint": price - .01}


def test_college_best_ask_survives_thin_books_and_still_checks_fees_clock_and_models(monkeypatch):
    tip = TIP
    now = tip - timedelta(hours=1)
    info, raw = _market_fixture(tip)
    contract = feed._contract(info, raw, info["root_event_id"], info["base_slug"])
    row = {"id": info["id"], "revision": 1, "info": info, "kickoff": tip,
           "home_probability": .63, "model_observed_at": now - timedelta(hours=1)}
    books = {"301": _book(now, .28), "302": _book(now, .74)}
    monkeypatch.setattr(feed, "_parsed_book", lambda _m, side, _raw: books.get(side.token_id))
    result = focus._evaluate(row, None, contract, {}, now)
    assert result["research_only"] and result["amount_cny"] == 0 and result["token_id"] == "301"
    assert result["model_version"] == focus.college.VERSION and result["market_kind"] == "胜负"
    assert result["market_label"] == "全场胜负（含加时）" and result["condition_side"] == "away"
    assert result["edge"] == pytest.approx(.37 - .28 - .05 * .28 * .72)
    assert result["worst_edge"] == pytest.approx(result["edge"] - .03)

    exaggerated = row | {"home_probability": .49}
    assert focus._evaluate(exaggerated, None, contract, {}, now) is None
    books["301"] = _book(now, .32)
    books["302"] = _book(now, .70)
    assert focus._evaluate(row, None, contract, {}, now) is None
    books["301"] = _book(now, .28)
    books["301"]["asks"] = [(.34, 10000), (.28, .01)]
    books["302"] = _book(now, .74, depth=.01)
    contract.raw.pop("liquidityNum")
    thin = focus._evaluate(row, None, contract, {}, now)
    assert (thin["token_id"], thin["price"], thin["price_basis"]) == ("301", .28, "best_ask")
    assert thin["edge"] == result["edge"] and thin["worst_edge"] == result["worst_edge"]
    books["302"]["asks"] = [(.74, 0)]
    assert focus._evaluate(row, None, contract, {}, now) is None
    books["302"] = _book(now - timedelta(minutes=3), .74)
    assert focus._evaluate(row, None, contract, {}, now) is None
    assert focus._evaluate(row, None, contract, {}, tip) is None


def test_nba_focus_checks_both_original_models_and_envelope(monkeypatch):
    tip = TIP
    now = tip - timedelta(hours=1)
    info, raw = _market_fixture(tip, "nba")
    contract = feed._contract(info, raw, info["root_event_id"], info["base_slug"])
    row = {"id": info["id"], "revision": 1, "info": info, "kickoff": tip}
    books = {"301": _book(now, .40), "302": _book(now, .62)}
    monkeypatch.setattr(feed, "_parsed_book", lambda _m, side, _raw: books[side.token_id])

    def projection(version, margin, variants):
        return {"model_version": version, "observed_at": feed.utc_iso(now - timedelta(minutes=4)),
                "distributions": [[value, 10., 225., 18.] for value in [margin] * 4 + variants]}

    base = projection(focus.models.NBA_VERSION, 7, [7, 7])
    new = projection(focus.models.NBA_RATINGS_VERSION, 6, [6] * 6)
    result = focus._evaluate(row, (base, new), contract, {}, now)
    assert result["probability"] > .72 and result["worst_edge"] > .08 and result["token_id"] == "302"
    weak = projection(focus.models.NBA_RATINGS_VERSION, 6, [3] + [6] * 5)
    assert focus._evaluate(row, (base, weak), contract, {}, now) is None
    disagree = projection(focus.models.NBA_RATINGS_VERSION, -7, [-7] * 6)
    assert focus._evaluate(row, (base, disagree), contract, {}, now) is None
