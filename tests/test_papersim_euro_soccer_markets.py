"""Five-league discovery and paired pregame quotes from synthetic public responses."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import parse_qs, urlsplit
import json
import pytest

from polymarket_alpha_lab.papersim import euro_soccer_markets as markets


NOW = datetime(2026, 11, 28, 12, tzinfo=UTC)
KICKOFF = NOW + timedelta(hours=2)
TEAMS = ("Sport Lisboa e Benfica", "FC Porto")


def raw_market(leg, number):
    slug = "por-ben-por-2026-11-28"
    home, away = TEAMS
    title = f"{home} vs. {away}"
    total = leg == "total_2_5"
    suffix = {"home": "ben", "away": "por", "draw": "draw", "total_2_5": "total-2pt5"}[leg]
    event = {"slug": slug + ("-more-markets" if total else ""), "title": title + (" - More Markets" if total else ""),
             "gameId": 42, "seriesSlug": "primeira-liga", "active": True, "closed": False, "archived": False,
             "startTime": KICKOFF.isoformat()}
    subject = {"home": f"{home} wins", "away": f"{away} wins", "draw": "the game ends in a draw"}
    if total:
        rule = (f'This market will resolve to "Over" if {home} and {away} combine to score 3 or more goals in this game. '
                'If the combined total is less than 3, this market will resolve to "Under".')
    else:
        rule = f'If {subject[leg]}, this market will resolve to "Yes". Otherwise, this market will resolve to "No".'
    return {"slug": slug + "-" + suffix, "events": [event], "id": str(number), "conditionId": "0x" + f"{number:064x}",
            "groupItemTitle": {"home": home, "away": away, "draw": f"Draw ({title})", "total_2_5": "O/U 2.5"}[leg],
            "active": True, "acceptingOrders": True, "closed": False, "archived": False, "enableOrderBook": True,
            "acceptingOrdersTimestamp": (NOW-timedelta(days=1)).isoformat(), "gameStartTime": KICKOFF.isoformat(),
            "sportsMarketType": "totals" if total else "moneyline", "line": 2.5 if total else None,
            "outcomes": json.dumps(["Over", "Under"] if total else ["Yes", "No"]),
            "clobTokenIds": json.dumps([str(number * 10), str(number * 10 + 1)]),
            "description": rule + " This market refers only to the first 90 minutes of regular play plus stoppage time.",
            "feesEnabled": True, "feeSchedule": {"rate": 0.05, "exponent": 1, "takerOnly": True},
            "liquidityClob": 3000}


class PublicData:
    def __init__(self):
        self.markets = [raw_market(leg, i) for i, leg in enumerate(("home", "draw", "away", "total_2_5"), 1)]
        self.gam_at = self.book_at = self.received = NOW
        self.batches = []
        self.gamma_calls = []
        self.book_edits = lambda rows: rows
        self.page = markets.PAGE_SIZE

    def fetch(self, url, body):
        parsed = urlsplit(url)
        if parsed.hostname == "gamma-api.polymarket.com":
            assert parsed.path == "/markets" and body is None
            query = parse_qs(parsed.query)
            self.gamma_calls.append(query)
            assert query["tag_id"] == [markets.LEAGUE_TAGS["por"]] and query["closed"] == ["false"]
            offset, kind = int(query["offset"][0]), query["sports_market_types"][0]
            rows = [m for m in self.markets if m["sportsMarketType"] == kind][offset:offset+self.page]
            at = self.gam_at
        else:
            assert url == "https://clob.polymarket.com/books"
            tokens = [o["token_id"] for o in body]
            self.batches.append(tokens)
            index = {t: m["conditionId"] for m in self.markets for t in markets.pm._maybe_json_list(m["clobTokenIds"])}
            rows = self.book_edits([{"asset_id": token, "market": index[token],
                                     "timestamp": str(int(self.book_at.timestamp() * 1000)),
                                     "bids": [{"price": "0.39", "size": "100"}],
                                     "asks": [{"price": "0.4", "size": "2"}, {"price": "0.5", "size": "3000"}]}
                                    for token in tokens])
            at = self.received
        raw = json.dumps(rows, sort_keys=True).encode()
        return markets.PublicResponse(rows, at, sha256(raw).hexdigest())

    def discover(self):
        return markets.discover_markets("por", NOW, KICKOFF+timedelta(days=1), self.fetch)

    def observe(self, *, home="Benfica", away="Porto", kickoff=KICKOFF, fixture_id=None):
        listings, gaps = self.discover()
        assert not gaps
        return markets.observe_markets("por", home, away, kickoff, listings, self.fetch, fixture_id=fixture_id)


def test_identity_fees_and_all_eight_tokens_use_one_batch_with_depth():
    data = PublicData()
    report = data.observe()
    assert list(markets.LEAGUE_TAGS) == ["por", "scop", "bel1", "nor", "tur"]
    assert report["status"] == "complete" and report["window"] == "pregame"
    assert len(data.batches) == 1 and len(set(data.batches[0])) == 8
    assert report["book_snapshot_sha256"] in report["source_sha256s"] and len(report["source_sha256s"]) == 3
    family = report["families"]["match_winner"]
    assert family["market_probabilities"] == pytest.approx({"home": 1/3, "draw": 1/3, "away": 1/3})
    result = family["contracts"]["home"]
    assert result["gamma_snapshot_sha256"] in report["source_sha256s"] and result["fee_rate"] == .05
    quote = result["tokens"]["yes"]
    assert (quote["bid"], quote["ask"], quote["depth_near_2c_usd"]) == (.39, .4, 1)
    assert quote["research_fill_usd"] == 250 and quote["ask"] < quote["research_vwap"]
    assert quote["fee_per_share_at_ask"] == pytest.approx(.05*.4*.6)
    assert report["families"]["total_2_5"]["market_probabilities"] == {"over": .5, "under": .5}


@pytest.mark.parametrize("change", [
    {"feeSchedule": None}, {"feesEnabled": False}, {"feeSchedule": {"rate": .004, "exponent": 1, "takerOnly": True}},
    {"description": "Including extra time and penalties."}, {"acceptingOrders": False},
    {"groupItemTitle": "FC Porto"}, {"outcomes": "[truncated"}, {"clobTokenIds": ["10", "10"]},
])
def test_missing_or_contradictory_contract_discards_whole_three_way_family(change):
    data = PublicData()
    data.markets[0].update(change)
    report = data.observe()
    assert report["status"] == "partial"
    assert report["families"]["match_winner"]["contracts"] == {}
    assert report["families"]["total_2_5"]["status"] == "observed"
    assert data.batches == [["40", "41"]]


def test_contradictory_payout_text_cannot_be_validated_from_slug_or_line():
    data = PublicData()
    total = data.markets[-1]
    total["description"] = total["description"].replace("score 3 or more", "score 4 or more")
    assert data.observe()["families"]["total_2_5"]["status"] == "unavailable"


@pytest.mark.parametrize("home,away,offset", [("Porto","Benfica",0), ("Benfica B","Porto",0), ("Benfica","Porto",120)])
def test_swaps_other_clubs_and_rescheduled_kickoff_are_gaps(home, away, offset):
    data = PublicData()
    report = data.observe(home=home, away=away, kickoff=KICKOFF+timedelta(minutes=offset))
    assert report["status"] == "unavailable" and report["observed_at"] is None and not data.batches


@pytest.mark.parametrize("edit", [
    lambda rows: rows[1:],
    lambda rows: rows+[rows[0]],
    lambda rows: [rows[0] | {"market": "0x"+"e"*64}, *rows[1:]],
    lambda rows: [rows[0] | {"timestamp": "0"}, *rows[1:]],
    lambda rows: [rows[0] | {"bids": [{"price":".45", "size":100}]}, *rows[1:]],
    lambda rows: [rows[0] | {"asks": []}, *rows[1:]],
    lambda rows: [rows[0] | {"timestamp":str(int((NOW+timedelta(seconds=1)).timestamp()*1000))}, *rows[1:]],
])
def test_bad_clob_side_is_not_spliced_into_market_family(edit):
    data = PublicData()
    data.book_edits = edit
    assert data.observe()["families"]["match_winner"]["status"] == "unavailable"
    data.book_edits = lambda rows: [rows[0]]   # No prior entries can fill this newer response.
    assert data.observe()["status"] == "unavailable"
    assert len(data.batches) == 2


def test_thin_quote_keeps_observed_bbo_and_marks_the_research_fill_unknown():
    data = PublicData()
    data.book_edits = lambda rows: [r | {"asks": [{"price": "0.4", "size":"0.1"}]} for r in rows]
    report = data.observe()
    q = report["families"]["total_2_5"]["contracts"]["total_2_5"]["tokens"]["over"]
    assert q["research_vwap"] is None and q["research_fill_usd"] == .04
    assert q["fee_per_share_at_research_vwap"] is None and q["ask"] == .4


@pytest.mark.parametrize("lead,window", [(30,"forward"),(3,"closing")])
def test_raw_book_clock_inside_registered_window(lead, window):
    data = PublicData()
    data.received = data.gam_at = KICKOFF-timedelta(minutes=lead)
    data.book_at = data.received-timedelta(milliseconds=1)
    report = data.observe()
    assert report["window"] == window and report["status"] == "unavailable"
    data.book_at = data.received
    assert data.observe()["status"] == "complete"


def test_fee_metadata_has_its_own_freshness_and_positive_provenance():
    data = PublicData()
    data.gam_at = NOW - markets.MAX_AGE - timedelta(microseconds=1)
    assert data.observe()["status"] == "unavailable"
    data.gam_at = NOW+timedelta(seconds=1)
    assert data.observe()["status"] == "unavailable"


def test_incomplete_page_exposes_gap_and_discards_ambiguous_family(monkeypatch):
    data = PublicData()
    monkeypatch.setattr(markets, "PAGE_SIZE", 2)
    data.page = 2
    assert len(data.discover()[0]) == 4
    assert [q["offset"][0] for q in data.gamma_calls] == ["0", "2", "0"]
    monkeypatch.setattr(markets, "MAX_PAGES", 1)
    selected, gaps = data.discover()
    assert gaps == ("moneyline_gamma_page_limit",)
    assert [v.leg for v in selected] == ["total_2_5"]


def test_conflicting_game_or_contract_identity_never_completes_a_family():
    data = PublicData()
    data.markets[-1]["events"][0]["gameId"] = 600
    assert data.observe()["families"]["total_2_5"]["reasons"] == ["ambiguous_gamma_fixture"]
    assert not data.batches
    data.markets[-1]["events"][0]["gameId"] = 42
    data.markets[1]["conditionId"] = data.markets[2]["conditionId"]
    report = data.observe()
    assert report["families"]["match_winner"]["reasons"] == ["contract_identity_collision"]
    assert report["families"]["total_2_5"]["status"] == "observed"


def edge_inputs():
    data = PublicData()
    # Use distinct YES, NO, Over and Under quotes so that a swapped token cannot pass.
    asks = {"10": .26, "11": .75, "20": .29, "21": .72, "30": .50, "31": .51, "40": .61, "41": .40}
    data.book_edits = lambda books: [b | {"asks": [{"price": str(asks[b["asset_id"]]), "size": "1"}],
                                          "bids": [{"price": str(asks[b["asset_id"]]-.01), "size": "1"}]}
                                     for b in books]
    probs = dict(home_win=.30, draw=.25, away_win=.45, over_2_5=.65, under_2_5=.35)
    model = {"fixture_id": "12", "league": "por", "version": "v1", "research_only": True, "amount_cny": 0,
             "as_of": NOW.isoformat(), "status": "team_news_scenario", "source_refs": ["a"*64],
             "news": {"source_refs": ["a"*64], "fetched_at": NOW.isoformat(), "blockers": []},
             "baseline": probs, "context": probs | {"home_win": .41, "away_win": .34}}
    return model, data.observe(fixture_id="12")


def test_five_fee_adjusted_edges_use_actual_best_ask_of_each_named_outcome():
    model, market = edge_inputs()
    edges = markets.research_edges(model, market)
    assert set(edges) == {"home", "away", "draw", "over", "under"}
    for side, ask, base, context in (("home", .26, .30, .41), ("draw", .29, .25, .25), ("away", .50, .45, .34),
                                      ("over", .61, .65, .65), ("under", .40, .35, .35)):
        fee = .05 * ask * (1-ask)
        assert edges[side] == pytest.approx(dict(ask=ask, fee_per_share=fee, break_even=ask+fee,
                                                baseline=base-ask-fee, context=context-ask-fee))


@pytest.mark.parametrize("change", [{"fixture_id": "99"}, {"league": "scop"}, {"amount_cny": 5},
                                    {"as_of": KICKOFF.isoformat()}, {"source_refs": []}])
def test_unbound_or_late_model_has_no_edge_but_preserves_observed_cost(change):
    model, market = edge_inputs()
    edges = markets.research_edges(model | change, market)
    assert all(v["baseline"] is None and v["context"] is None and v["break_even"] > v["ask"] for v in edges.values())


def test_missing_contract_fee_or_context_has_no_invented_cost_or_edge():
    model, market = edge_inputs()
    model["news"]["blockers"] = ["lineups_unavailable"]
    market["families"]["match_winner"]["contracts"]["draw"]["tokens"]["yes"].pop("fee_per_share_at_ask")
    market["families"]["total_2_5"]["status"] = "unavailable"
    edges = markets.research_edges(model, market)
    assert edges["draw"] == dict(ask=.29, fee_per_share=None, break_even=None, baseline=None, context=None)
    assert set(edges["over"].values()) == {None} and set(edges["under"].values()) == {None}
    assert edges["home"]["baseline"] is not None and edges["home"]["context"] is None
    assert markets.research_edges(None, market)["away"]["break_even"] > .5
    assert all(set(v.values()) == {None} for v in markets.research_edges(model, None).values())


def test_edge_comparison_never_uses_different_book_batch_or_wrong_window():
    model, market = edge_inputs()
    market["families"]["match_winner"]["book_snapshot_sha256"] = "b"*64
    edges = markets.research_edges(model, market)
    assert set(edges["home"].values()) == {None} and edges["over"]["context"] is not None
    market["window"] = "closing"
    assert all(set(v.values()) == {None} for v in markets.research_edges(model, market).values())
