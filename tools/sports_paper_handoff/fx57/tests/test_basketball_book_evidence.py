"""Basketball formal and zero-stake selected-side book capture."""

from datetime import timedelta
from unittest.mock import patch

import basketball_feed as feed
import basketball_focus as focus
import basketball_models as models
import football_shortlist as shortlist
from tools.sports_paper_handoff.tests.test_basketball_prices import FIXTURE, OFFICIAL, RAW, TIP, _market_fixture, _model


def test_formal_basketball_uses_the_selected_raw_book_for_its_signal(persist_quote):
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

    books = {opt.token_id: ({"asset_id": opt.token_id, "market": market.condition_id,
                             "timestamp": str(int(now.timestamp() * 1000)),
                             "asks": [{"price": asks[opt.token_id], "size": ".01"}, {"price": ".85", "size": "10000"}],
                             "bids": [{"price": bids[opt.token_id], "size": "1"}]}, now) for opt in market.options}

    with patch.object(feed, '_fixture_markets', return_value=('scheduled', None, [market], False)), \
         patch.object(feed, '_official_summary', return_value=(official, payload)), \
         patch.object(feed, '_books', return_value=books), \
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
    assert signal["best_ask_price"] == "0.45" and signal["best_ask_size"] == "0.01"
    assert signal["book_snapshot_sha256"] == feed.common.document_digest(books["302"][0])
    assert signal["book_token_id"] == signal["token_id"] and signal["book_condition_id"] == signal["condition_id"]
    assert 'depth_filled_usd' not in signal
    official_side = next(item for item in forward['observations'] if item['token_id'] == signal['token_id'])
    assert official_side['entry_price'] == .85 and official_side['depth_filled_usd'] == 250
    assert 'fee_depth_or_liquidity_unverified' in official_side['reasons']
    assert signal['direct_contract_verified'] and len(signal['variants']) == 6
    assert signal['valid_until'] == feed.utc_iso(now + feed.common.BOOK_AGE)
    row = {"info": fixture, "fixture_key": fixture["id"], "revision": 1, "kickoff": TIP}
    entry, reason = shortlist._signal_direction(row, signal, [signal["valid_until"]], now, TIP, now)
    assert reason == "qualified"
    saved = persist_quote(entry.quote, now)
    assert saved["signal"]["book_snapshot_sha256"] == feed.common.document_digest(books["302"][0])
    assert saved["signal"]["best_ask_size"] == "0.01" and saved["signal"]["book_token_id"] == "302"


def test_zero_stake_watch_saves_the_selected_raw_basketball_book(persist_watch):
    now = TIP - timedelta(hours=1)
    info, raw = _market_fixture(TIP)
    market = feed._contract(info, raw, info["root_event_id"], info["base_slug"])
    row = {"id": info["id"], "revision": 1, "info": info, "kickoff": TIP,
           "home_probability": .63, "model_observed_at": now - timedelta(hours=1)}
    books = {opt.token_id: ({"asset_id": opt.token_id, "market": market.condition_id,
                             "timestamp": str(int(now.timestamp() * 1000)),
                             "asks": [{"price": ask, "size": "1"}, {"price": ".82", "size": "400"}],
                             "bids": [{"price": bid, "size": "12"}]}, now)
             for opt, ask, bid in zip(market.options, (".28", ".74"), (".26", ".72"), strict=True)}
    watch = focus._evaluate(row, None, market, books, now)
    assert watch["amount_cny"] == 0 and watch["token_id"] == "301"
    assert watch["best_ask_coverage"] == {"status": "not_applicable", "reason": "zero_stake_observation"}
    assert (watch["best_ask_price"], watch["best_ask_size"]) == ("0.28", "1")
    assert watch["book_snapshot_sha256"] == feed.common.document_digest(books["301"][0])
    assert watch["book_snapshot_sha256"] != feed.common.document_digest(books["302"][0])
    saved = persist_watch(watch, now)
    assert saved["amount_cny"] == 0 and saved["book_snapshot_sha256"] == feed.common.document_digest(books["301"][0])
    assert saved["best_ask_size"] == "1" and saved["best_ask_coverage"]["status"] == "not_applicable"
    first, observed = books["301"]
    assert focus._evaluate(row, None, market, books | {"301": (first | {"asset_id": "302"}, observed)}, now) is None
