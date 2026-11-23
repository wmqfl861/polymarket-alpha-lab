"""Regression tests for rolling domestic coverage and separately scored totals."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import football_feed as feed
import football_model as model
import football_totals_calibration as totals
from polymarket_alpha_lab.papersim import soccer_model


NOW = datetime(2026, 11, 23, tzinfo=UTC)


def match_row(kickoff, *, score=(4, 0), state="finished", home_id="123", away_id="456"):
    return {"kickoff": feed.utc_iso(kickoff), "home_id": home_id, "away_id": away_id,
            "home": "Werder Bremen", "away": "Hamburger SV", "state": state, "score": list(score)}


class ModelRefreshTests(unittest.TestCase):
    def test_market_club_names_match_only_the_correct_senior_fixture(self):
        kickoff = feed.utc_iso(NOW)
        for market_name, provider_name in (
            ("Real Betis Balompié", "Real Betis"),
            ("SC Paderborn 07", "Paderborn"),
            ("SV 07 Elversberg", "Elversberg"),
            ("ES Troyes AC", "Troyes"),
        ):
            with self.subTest(club=market_name):
                fixture = {"home": market_name, "away": "Opponent FC", "home_alias": "", "away_alias": "",
                           "kickoff": kickoff}
                row = {"kickoff": kickoff, "home": provider_name, "home_short": provider_name,
                       "away": "Opponent", "away_short": "Opponent"}
                others = {
                    "reserves": {**row, "home": provider_name + " II", "home_short": provider_name + " II"},
                    "women": {**row, "home": provider_name + " Women", "home_short": provider_name + " Women"},
                    "wrong_time": {**row, "kickoff": feed.utc_iso(NOW + timedelta(minutes=5))},
                    "swapped": {**row, "home": "Opponent", "home_short": "Opponent",
                                "away": provider_name, "away_short": provider_name},
                }
                self.assertEqual(model._match_fixture(fixture, {"senior": row, **others})[0], "senior")
                self.assertIsNone(model._match_fixture(fixture, others))
                self.assertIsNone(model._match_fixture(fixture, {"one": row, "two": row}))

    def test_all_seven_leagues_refresh_without_a_polymarket_fixture(self):
        def calendar(comp, year, deadline, clock):
            offset = timedelta(days=4 if year == 2026 else 370)
            return {"calendar": {"55": match_row(NOW - offset)}, "calendar_at": feed.utc_iso(clock())}

        with (patch.object(model, "_calendar", side_effect=calendar) as fetched,
              patch.object(model, "_seed_rows", return_value={}),
              patch.object(model, "_historical_result", return_value=[1.52, 1.54, 4, 0])):
            sources, cache = model.refresh_models([], lambda: NOW, None)

        self.assertEqual({c.args[0] for c in fetched.call_args_list if c.args[1] == 2026},
                         set(model.COMPETITIONS.values()))
        for code in model.COMPETITIONS:
            self.assertEqual(sources[f"{code}:2026"]["completed"], 1)
            self.assertEqual(cache["seasons"][f"{code}:2026"]["results"]["55"], [1.52, 1.54, 4, 0])

    def test_out_of_window_result_retries_and_retains_verified_score(self):
        kickoff = NOW - timedelta(hours=5)
        old = {"version": 1, "seasons": {"bun:2026": {
            "calendar": {"55": match_row(kickoff, score=(), state="scheduled")},
            "calendar_at": feed.utc_iso(kickoff), "results": {},
        }}}
        bundesliga = model.COMPETITIONS["bun"]

        def calendar(comp, year, deadline, clock):
            if comp == bundesliga and year == 2026:
                return {"calendar": {"55": match_row(kickoff)}, "calendar_at": feed.utc_iso(clock())}
            raise OSError("mock league unavailable")

        def result(comp, match_id, match, deadline):
            return None if retrieve.call_count == 1 else [1.52, 1.54, 4, 0]

        with (patch.object(model, "_calendar", side_effect=calendar),
              patch.object(model, "_seed_rows", return_value={}),
              patch.object(model, "_historical_result", side_effect=result) as retrieve):
            _, first = model.refresh_models([], lambda: NOW, old)
            retry_at = first["seasons"]["bun:2026"]["retry"]["55"]
            self.assertEqual(feed.stamp(retry_at), NOW + model.HISTORY_RETRY)
            _, second = model.refresh_models([], lambda: NOW + timedelta(minutes=10), first)
            self.assertEqual(retrieve.call_count, 1)
            _, third = model.refresh_models([], lambda: NOW + model.HISTORY_RETRY, second)
            self.assertEqual(retrieve.call_count, 2)
        result_cache = third["seasons"]["bun:2026"]
        self.assertEqual(result_cache["results"]["55"], [1.52, 1.54, 4, 0])
        self.assertEqual(result_cache["retry"], {})


class CalibrationTests(unittest.TestCase):
    def test_logit_fit_rejects_holdout_matches_and_duplicate_fixtures(self):
        early = totals.TRAIN_END - timedelta(days=10)
        variant_overs = ((0.72,) * 10, (0.55,) * 10, (0.32,) * 10)
        rows = [
            totals.Match("bun", str(i), str(i // 10), early, early, early + totals.COMPLETION_LAG,
                         ("home", "away"), (i % 5, 0), variant_overs, {})
            for i in range(60)
        ]
        fitted = totals.fit(rows)
        self.assertTrue(fitted["model_version"].startswith(totals.VERSION_PREFIX))
        self.assertEqual(totals.fit(list(reversed(rows))), fitted)
        probabilities = [rows[0].candidate(line, fitted["parameters"]) for line in totals.LINES]
        self.assertGreater(probabilities[0], probabilities[1])
        self.assertGreater(probabilities[1], probabilities[2])
        with self.assertRaisesRegex(ValueError, "pre-holdout"):
            totals.fit(rows + [rows[0]])
        with self.assertRaisesRegex(ValueError, "pre-holdout"):
            totals.fit(rows + [totals.Match("bun", "61", "6", totals.TRAIN_END, totals.TRAIN_END,
                                             totals.TRAIN_END + totals.COMPLETION_LAG, ("home", "away"),
                                             (2, 1), variant_overs, {})])

    def test_tail_uses_only_verified_past_results_and_matches_both_sides(self):
        comp = model.Competition("Bundesliga", 54, 2, 2)
        prior = {"calendar": {"1": match_row(NOW - timedelta(days=90), score=(0, 0)),
                              "2": match_row(NOW - timedelta(days=89), score=(2, 2))},
                 "results": {"1": [0.5, 0.5, 0, 0], "2": [1.5, 1.5, 2, 2]}}
        current = {"calendar": {"3": match_row(NOW - timedelta(days=60), score=(0, 2)),
                                "4": match_row(NOW - timedelta(hours=2), score=(0, 9)),
                                "5": match_row(NOW - timedelta(days=5), score=(0, 9))},
                   "results": {"3": [1, 2, 0, 2], "4": [1, 3, 0, 9], "5": [1, 3, 0, 8]}}
        tail = totals.league_tail_inputs(comp, current, prior, NOW)
        self.assertEqual((tail["prior_matches"], tail["recent_matches"]), (2, 1))
        self.assertAlmostEqual(tail["overs"][0], 15.5 / 30.5)
        self.assertAlmostEqual(tail["overs"][1], 15 / 30.5)
        self.assertAlmostEqual(tail["overs"][2], 15 / 30.5)

    def test_market_join_checks_cutoff_score_clock_and_oriented_teams(self):
        at = NOW + timedelta(days=1)
        row = totals.Match("bun", "123", "4", at - timedelta(hours=2), at, at + totals.COMPLETION_LAG,
                           ("Werder Bremen", "Hamburger SV"), (4, 0), (), {})
        market_time = at - timedelta(minutes=65)
        key = "bun", at, feed.team_key("Werder Bremen"), feed.team_key("Hamburger SV")
        self.assertEqual(totals._market_price(row, {key: ((4, 0), market_time, 0.55)}), 0.55)
        self.assertIsNone(totals._market_price(row, {key: ((0, 4), market_time, 0.55)}))
        self.assertIsNone(totals._market_price(row, {key: ((4, 0), at - timedelta(minutes=71), 0.55)}))

    def test_projection_does_not_pass_unregistered_lines_with_the_new_version(self):
        registration = {"model_version": totals._tail_version(), "parameters": totals.TAIL_CONFIG, "season": 2026,
                        "registered_at": feed.utc_iso(NOW - timedelta(minutes=1)), "status": "forward_shadow"}
        info = {"league": "bun", "state": "scheduled", "kickoff": feed.utc_iso(NOW + timedelta(hours=1))}
        observed = feed.utc_iso(NOW - timedelta(minutes=3))
        data = {"source": "test", "sha256": "b" * 64, "observed_at": observed,
                "league_totals": {"overs": [0.81, 0.51, 0.25], "sha256": "d" * 64, "observed_at": observed}}
        variants = [soccer_model.variant_outputs(1.5, 1.3)] * 10
        projection = (data, variants, (1.5, 1.3))
        selected = totals.make_shadow_context(info, NOW, registration, projection=projection)
        self.assertEqual(selected.model_version, registration["model_version"])
        market = SimpleNamespace(kind="total", team_side=None, line=2.5)
        over = selected.market_quote(market, SimpleNamespace(side="over"))
        under = selected.market_quote(market, SimpleNamespace(side="under"))
        self.assertAlmostEqual(over["model_median"] + under["model_median"], 1)
        self.assertNotEqual(over, selected.baseline_quote(market, SimpleNamespace(side="over")))
        market.line = 4.5
        self.assertIsNone(selected.market_quote(market, SimpleNamespace(side="under")))
        market.kind = "team_total"
        market.line = 2.5
        self.assertIsNone(selected.market_quote(market, SimpleNamespace(side="over")))
        failed = registration | {"model_version": totals._version({"intercept": 0.1, "slope": 0.9})}
        self.assertIsNone(totals.make_shadow_context(info, NOW, failed, projection=projection))
        self.assertIsNone(totals.make_shadow_context(info, NOW + timedelta(minutes=26),
                                                     registration, projection=projection))

    def test_fixed_plan_requires_two_wholly_unstarted_official_rounds(self):
        comp = model.Competition("Test", 54, 4, 8)
        calendar = {}
        for round_ in (1, 2, 3, 4):
            for game in (0, 1):
                state = "finished" if (round_, game) == (1, 0) else "scheduled"
                row = match_row(NOW + timedelta(days=round_),
                                state=state, home_id=str(game + 30), away_id=str(game + 40))
                calendar[str(round_ * 10 + game)] = row | {"round": round_}
        with patch.object(model, "COMPETITIONS", {"bun": comp}):
            planned = totals._planned_rounds({"seasons": {"bun:2026": {"calendar": calendar}}}, NOW)
        self.assertEqual([round_["matchweek"] for round_ in planned["bun"]], [2, 3])
        self.assertEqual([match["fixture_id"] for match in planned["bun"][0]["fixtures"]], ["20", "21"])

    def test_rounds_are_selected_by_earliest_completion_with_structural_tiebreaks(self):
        comp = model.Competition("Test", 54, 4, 8)
        calendar = {}
        for round_, delay in [(1, (1, 45)), (2, (2, 5)), (3, (3, 5)), (4, (3, 5))]:
            for game, days in enumerate(delay):
                row = match_row(NOW + timedelta(days=days), state="scheduled",
                                home_id=str(game + 30), away_id=str(game + 40))
                calendar[str(round_ * 10 + game)] = row | {"round": round_}
        with patch.object(model, "COMPETITIONS", {"bun": comp}):
            planned = totals._planned_rounds({"seasons": {"bun:2026": {"calendar": calendar}}}, NOW)
        self.assertEqual([round_["matchweek"] for round_ in planned["bun"]], [2, 3])

    def test_forward_scores_require_same_contract_and_a_result_available_by_report_time(self):
        registered = feed.utc_iso(NOW - timedelta(days=3))
        kickoff = NOW - timedelta(hours=4)
        captured = kickoff - timedelta(minutes=25)
        item = {"fixture_id": "55", "kickoff": feed.utc_iso(kickoff), "team_ids": ["123", "456"]}
        registration = {"registered_at": registered, "model_version": totals._tail_version(), "protocol": {},
                        "planned_matchweeks": {"bun": [{"matchweek": 1, "fixtures": [item]}]}}
        base = {"league": "bun", "fixture_id": "55", "team_ids": item["team_ids"], "kickoff": kickoff,
                "matchweek": 1, "line": 2.5, "market_id": "77", "fixture_revision": 1,
                "observed_at": captured, "model_observed_at": captured - timedelta(minutes=2),
                "book_observed_at": captured, "market_price_basis": "clob_midpoint",
                "home_goals": 4, "away_goals": 0, "result_state": "resolved", "result_at": kickoff + timedelta(hours=2),
                "evidence": {"direct_contract_verified": True, "research": {
                    "baseline_model_version": totals.MODEL_VERSION_SOCCER, "registered_at": registered,
                }}}
        over = base | {"side": "over", "model_probability": 0.65, "baseline_probability": 0.6,
                       "market_price": 0.59, "bid_price": 0.58, "ask_price": 0.60, "won": True}
        under = base | {"side": "under", "model_probability": 0.35, "baseline_probability": 0.4,
                        "market_price": 0.41, "bid_price": 0.40, "ask_price": 0.42, "won": False}
        observations = [over, under]

        def fetch(*args):
            return SimpleNamespace(fetchall=lambda: observations)

        def paired(rows, lhs, rhs, metric):
            return [row["forecasts"][lhs][metric] - row["forecasts"][rhs][metric]
                    for row in rows if rhs in row["forecasts"]]

        connection = SimpleNamespace(execute=fetch)
        verified = totals.forward_progress(connection, registration, NOW, paired)["leagues"]["bun"]["2.5"]
        self.assertEqual(verified["complete_matchweeks"], 1)
        self.assertEqual(verified["market_pairs"], 1)
        self.assertLess(verified["paired_deltas"]["production10"]["brier"][0], 0)
        under["result_at"] = NOW + timedelta(minutes=1)
        pending = totals.forward_progress(connection, registration, NOW, paired)["leagues"]["bun"]["2.5"]
        self.assertEqual(pending["captured_pairs"], 1)
        self.assertEqual(pending["scored_fixtures"], 0)
        under["market_id"] = "78"
        crossed = totals.forward_progress(connection, registration, NOW, paired)["leagues"]["bun"]["2.5"]
        self.assertEqual(crossed["captured_pairs"], 0)


if __name__ == "__main__":
    unittest.main()
