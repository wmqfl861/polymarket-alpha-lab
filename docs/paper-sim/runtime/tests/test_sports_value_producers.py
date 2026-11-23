"""Regression checks for ranking qualified football and basketball contracts."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import basketball_feed as basketball
import basketball_focus
import basketball_models as models
import football_feed as football
import football_focus
import football_legacy
from sports_portfolio import value_metrics
from polymarket_alpha_lab.papersim import gates


NOW = datetime(2026, 11, 23, 1, tzinfo=UTC)
TIP = NOW + timedelta(minutes=28)
FEE = 0.05
NEWS = gates.check_news("fixture", "verified", news_delta=gates.NEWS_UNCHANGED)
LIQUID_CONTRACT = {"liquidityNum": 40000, "feesEnabled": True,
                   "feeSchedule": {"rate": FEE, "takerOnly": True, "exponent": 1}}


def forecast(median, low):
    return {"model_median": median, "qs_variants": (low,) + (median,) * 9}


def contracts():
    definitions = (
        (100, "match_winner", None, "home", "away"),
        (200, "total", 228.5, "over", "under"),
    )
    return [
        basketball.Contract(
            str(number), f"0x{number:064x}", kind, line, None, basketball.EXPLICIT_OT,
            (basketball.Selection("Over" if line else "Home", str(number * 2), first, "First"),
             basketball.Selection("Under" if line else "Away", str(number * 2 + 1), second, "Second")),
            "fixture", "nba-example-2026-11-23", LIQUID_CONTRACT | {"id": str(number), "slug": f"nba-example-{number}"},
            f"{number:064x}",
        ) for number, kind, line, first, second in definitions
    ]


def raw_books(market, price):
    prices = (price, round(1.01 - price, 2))
    return {
        option.token_id: ({"asset_id": option.token_id, "market": market.condition_id,
                           "timestamp": str(int((NOW - timedelta(seconds=2)).timestamp() * 1000)),
                           "asks": [{"price": str(ask), "size": "2000"}],
                           "bids": [{"price": str(round(1 - prices[1 - index], 3)), "size": "2000"}]},
                          NOW - timedelta(seconds=1))
        for index, (option, ask) in enumerate(zip(market.options, prices, strict=True))
    }


def model_quote(model, kind, line, side):
    centre, low = (0.80, 0.77) if kind == "match_winner" else (0.34, 0.31)
    if model["model_version"] == models.NBA_VERSION and kind == "total":
        centre, low = .35, .32
    if side in ("away", "under"):
        centre, low = 1 - centre, 1 - centre
    return forecast(centre, low)


class ProducerValueTests(unittest.TestCase):
    def test_football_formal_returns_best_and_alternative_by_fee_adjusted_roi(self):
        pair = contracts()
        data = ({"sha256": "a" * 64}, (), ())
        selections = [(m, m.options[0], ([(price, 2000)], NOW - timedelta(seconds=2)), data)
                      for price, m in zip((.70, .25), pair, strict=True)]

        def screen(info, market, option, book, projection, news, raw):
            p = model_quote({"model_version": models.NBA_RATINGS_VERSION}, market.kind, market.line, option.side)
            gate = gates.evaluate_candidate(p, book[0], fee_rate=FEE, news=news, gamma_liquidity=40000)
            self.assertTrue(gate.decision.passed)
            return gate, p, FEE

        with patch.object(football, "_verify_live_contract", return_value=LIQUID_CONTRACT), patch.object(
            football, "_screen", side_effect=screen
        ):
            ordered = football._verified_signals({}, selections, NEWS, TIP, TIP, lambda: NOW, 10)
        self.assertEqual([signal["market_id"] for signal in ordered], ["200", "100"])
        self.assertEqual(ordered[0]["conservative_probability"], .31)
        self.assertEqual([signal["worst_edge"] for signal in ordered], [.06, .07])

    def test_football_focus_uses_model_roi_to_break_a_conservative_tie_before_deduplication(self):
        league = "epl"
        info = {"league": league, "fixture_id": "501", "fotmob_league_id": "47", "fotmob_home_id": "5",
                "fotmob_away_id": "7"}
        identity = ("501", "47", "5", "7")
        base = {"price": .25, "probability": .34, "conservative_probability": .31, "fee_rate": FEE,
                "fixture_key": "fixture", "kickoff": TIP, "valid_until": TIP, "revision": 1,
                "independent_identity": identity, "league_code": league, "market_kind": "total",
                "team_side": None, "line": 2.5, "model_version": football.MODEL_VERSION_SOCCER, "model_digest": "a" * 64,
                "worst_edge": .06, "worst_net_edge": .050625}
        entries = [base | {"market_id": "200"}, base | {"market_id": "300", "probability": .35},
                   base | {"market_id": "100", "price": .7, "probability": .8, "conservative_probability": .77,
                           "worst_edge": .07, "worst_net_edge": .0595}]
        saved = {"revision": 1, "kickoff": TIP, "fixture_state": "scheduled", "entered": False, "missing_scans": 0,
                 "last_seen": NOW, "info": info}
        scopes = {football_focus.admission.scope_key(league, "total", 2.5, None, football.MODEL_VERSION_SOCCER)}
        reports = {"fixture": {"status": "waiting"}}
        with (
            patch.object(football_focus, "_read_current", return_value={"fixture": saved}),
            patch.object(football_focus.admission, "active_scopes", return_value=scopes),
            patch.object(football, "_paper_projections", return_value=(({"sha256": "a" * 64},),)),
        ):
            selected = football_focus._still_current(entries, None, lambda: NOW, reports)
        self.assertEqual([entry["market_id"] for entry in selected], ["300"])
        self.assertEqual(reports["fixture"]["status"], "focus_candidate")

    def test_basketball_focus_retains_nba_variants_and_ranks_by_roi_before_deduplication(self):
        matches = contracts()
        books = raw_books(matches[0], .70) | raw_books(matches[1], .25)
        indexed = {contract.id: contract for contract in matches}
        info = {"id": "fixture", "league": "nba", "home": "Home Team", "away": "Away Team",
                "kickoff": football.utc_iso(TIP)}
        row = {"id": info["id"], "revision": 1, "info": info, "kickoff": TIP}
        pair = tuple({"model_version": version, "observed_at": football.utc_iso(NOW)} for version in
                     (models.NBA_VERSION, models.NBA_RATINGS_VERSION))
        with (
            patch.object(basketball_focus, "_fixtures", return_value=[row]),
            patch.object(basketball_focus, "_nba_fixture", return_value=pair),
            patch.object(basketball_focus, "_nba_markets", return_value=matches),
            patch.object(basketball, "_market_direct", side_effect=lambda id_, *_: (indexed[id_].raw, NOW)),
            patch.object(basketball, "_books", return_value=books),
            patch.object(basketball, "_contract", side_effect=lambda info, raw, *refs: indexed[raw["id"]]),
            patch.object(models, "quote", side_effect=model_quote),
        ):
            selections = basketball_focus.candidates(None, NOW, TIP)
        self.assertEqual([selected["market_id"] for selected in selections], ["200"])
        selected = selections[0]
        value = value_metrics(selected["probability"], selected["price"], selected["fee_rate"],
                              worst_probability=selected["conservative_probability"])
        self.assertEqual(selected["conservative_probability"], .31)
        self.assertAlmostEqual(selected["worst_edge"], .050625)
        self.assertEqual(selected["worst_net_edge"], selected["worst_edge"])
        self.assertAlmostEqual(float(value.conservative_roi), .31 / .259375 - 1)

    def test_ncaa_focus_preserves_three_point_stress_probability(self):
        market = contracts()[0]
        info = {"id": "fixture", "league": "cbb", "home": "Home Team", "away": "Away Team"}
        row = {"id": info["id"], "revision": 1, "info": info, "kickoff": TIP, "home_probability": .46,
               "model_observed_at": NOW - timedelta(seconds=5)}
        outcome = basketball_focus._evaluate(row, None, market, raw_books(market, .34), NOW)
        self.assertEqual(outcome["probability"], .46)
        self.assertAlmostEqual(outcome["conservative_probability"], .43)
        self.assertAlmostEqual(outcome["worst_edge"], .43 - (.34 + .05 * .34 * .66))

    def test_basketball_formal_main_and_backup_use_same_value_order(self):
        matches = contracts()
        books = raw_books(matches[0], .70) | raw_books(matches[1], .25)
        indexed = {contract.id: contract for contract in matches}
        official = {"state": "scheduled", "kickoff": football.utc_iso(TIP)}
        info = {"id": "fixture", "league": "nba", "kickoff": football.utc_iso(TIP), "state": "scheduled",
                "home": "Home Team", "away": "Away Team", "basketball_official": official,
                "_paper_scopes": {basketball._scope("nba", m.kind, m.line, m.team_side, models.NBA_VERSION) for m in matches}}
        projection = {"model_version": models.NBA_VERSION, "sha256": "a" * 64}
        with (
            patch.object(basketball, "_fixture_markets", return_value=("scheduled", None, matches, False)),
            patch.object(basketball, "_official_summary", return_value=(official, None)),
            patch.object(basketball, "_verified_model", side_effect=lambda *args, ratings=False: None if ratings else projection),
            patch.object(basketball, "_news_snapshot", return_value={"sha256": "a" * 64}),
            patch.object(basketball, "_lineup_check", return_value=NEWS),
            patch.object(basketball, "_books", return_value=books),
            patch.object(basketball, "_market_direct", side_effect=lambda id_, *_: (indexed[id_].raw, NOW)),
            patch.object(basketball, "_contract", side_effect=lambda info, raw, *refs: indexed[raw["id"]]),
            patch.object(models, "quote", side_effect=model_quote),
        ):
            report = basketball.review(info, NOW, "t30")
        self.assertEqual(report["status"], "paper")
        self.assertEqual([entry["market_id"] for entry in report["signals"]], ["200", "100"])
        self.assertEqual(report["signal"], report["signals"][0])
        self.assertEqual(report["signal"]["conservative_probability"], .32)
        self.assertTrue(all("conservative_probability" not in row for row in report["observations"]))

    def test_fixed_assessment_winners_are_reranked_without_rewriting_evidence(self):
        stage = SimpleNamespace(fixture_id="123", lead=30, kickoff=TIP)
        decisions, signals, pins = [], {}, {}
        for key, price, probability, low, originally_selected in (
            (("100", "Yes"), .70, .80, .77, True), (("200", "Over"), .25, .34, .31, False)
        ):
            issue = "same_fixture_other_direction_selected"
            decisions.append((key, {"passed": originally_selected, "failed": [] if originally_selected else [issue]}))
            market = {"event_slug": "event", "market_slug": f"contract-{key[0]}", "line": None,
                      "token_id": key[0] + "0", "condition_id": f"0x{int(key[0]):064x}", "fixture_key": "fixture"}
            pins[key] = ({"模型中位概率": probability, "变体低值": low, "变体高值": probability,
                          "_kind": "match_winner", "_side": "home"}, market)
            gate = gates.evaluate_candidate(forecast(probability, low), [(price, 2000)], gamma_liquidity=40000,
                                            news=NEWS, fee_rate=FEE)
            signals[key] = {"market_id": key[0], "outcome": key[1], "price": price, "fee_rate": FEE,
                            "model_probability": probability, "min_variant": low, "net_edge": gate.exec_edge,
                            "adjusted_edge": gate.exec_edge_haircut, "worst_edge": gate.worst_variant_edge,
                            "filled_usd": 250, "gamma_liquidity": 40000, "applied_edge_floor": 0.05,
                            "url": f"https://polymarket.com/event/event/{market['market_slug']}"}
        runner = SimpleNamespace(assessment_rows=lambda *args: (decisions, None),
                                 qualifying_quote=lambda *args: (True, None),
                                 paper_signal=lambda _, key, *__: signals[key])
        with patch.object(football_legacy, "_pinned_contracts", return_value=(
            {"league": "EPL"}, ("47", "1", "2"), "b" * 64, pins
        )):
            ordered = football_legacy._verified_signals(runner, stage, None, NOW, NOW)
        self.assertEqual([signal["market_id"] for signal in ordered], ["200", "100"])
        self.assertEqual(ordered[0]["conservative_probability"], .31)
        self.assertFalse(decisions[1][1]["passed"])


if __name__ == "__main__":
    unittest.main()
