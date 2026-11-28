from copy import deepcopy

import pytest

from polymarket_alpha_lab.papersim.euro_soccer_model import MODEL_VERSION
from polymarket_alpha_lab.papersim.euro_soccer_outcomes import observation_id
from polymarket_alpha_lab.papersim.euro_soccer_report import summarize
from polymarket_alpha_lab.papersim.euro_soccer_sources import LEAGUES


KICKOFF = "2026-11-28T13:00:00+00:00"
FAMILIES = ("match_winner", "total_2_5")
TIMES = {"preview": "2026-11-28T10:00:00Z", "forward": "2026-11-28T12:37:00Z",
         "closing": "2026-11-28T12:58:00Z"}


def _observation(fixture_id="71", slot="forward", *, league="por", context=True):
    at = TIMES[slot]
    digest = "a" * 64
    probs = {"home_win": 0.5, "draw": 0.25, "away_win": 0.25, "over_2_5": 0.6, "under_2_5": 0.4}
    families = {f: {"status": "observed", "observed_at": at, "book_snapshot_sha256": digest} for f in FAMILIES}
    return {"kind": "observation", "league": league, "fixture_id": fixture_id, "season": "2026/2027",
            "home_id": "10", "away_id": "11",
            "phase": "regular", "kickoff": KICKOFF, "observed_at": at, "slot": slot,
            "research_only": True, "amount_cny": 0,
            "model_version": MODEL_VERSION, "forecast": {"baseline": probs, "context": probs if context else None},
            "markets": {"status": "complete", "families": families, "observed_at": at,
                        "window": "pregame" if slot == "preview" else slot, "book_snapshot_sha256": digest}}


def _result(*observations):
    fields = ("league", "fixture_id", "season", "phase", "kickoff", "home_id", "away_id", "model_version")
    return {k: observations[0][k] for k in fields} | {"kind": "result", "research_only": True,
                                                       "amount_cny": 0, "score": [2, 0],
                                                       "forward": None, "closing": None}


def _scored(observation, baseline=0.2, context=0.4, market=0.3):
    def metric(value):
        return None if value is None else {"brier": value, "log_loss": 0.9}

    scores = {family: {"market": metric(market), "baseline": metric(baseline), "context": metric(context)}
              for family in FAMILIES}
    return {"window": observation["slot"], "observation_id": observation_id(observation), "score": [2, 0],
            "scores": scores, "receipt": {"market_window": observation["markets"]["window"]}}


def _summary(*rows):
    return summarize(list(enumerate(rows)))


def test_all_five_research_leagues_visible_before_the_first_result():
    status = _summary()
    assert status["research_only"] is True and status["amount_cny"] == 0
    assert status["model_version"] == MODEL_VERSION
    assert status["leagues"].keys() == LEAGUES.keys()
    for league in status["leagues"].values():
        assert league["distinct_fixtures"] == league["result_fixtures"] == 0
        pair = league["paired_brier"]["closing"]["match_winner"]["baseline_vs_market"]
        assert pair == {"pairs": 0, "model_mean": None, "market_mean": None, "delta_model_minus_market": None}


def test_separate_fixtures_same_batch_and_slot_coverage():
    preview, forward, closing = [_observation(slot=slot, context=slot != "forward") for slot in TIMES]
    closing["markets"]["families"]["total_2_5"]["book_snapshot_sha256"] = "b" * 64
    partial = _observation("72")
    partial["forecast"]["baseline"] = partial["forecast"]["context"] = None
    partial["markets"]["families"]["match_winner"]["observed_at"] = TIMES["closing"]
    status = _summary(preview, forward, closing, partial, _result(forward), deepcopy(forward))["leagues"]["por"]
    assert status["distinct_fixtures"] == status["observed_fixtures"] == 2
    assert status["result_fixtures"] == status["context_fixtures"] == status["baseline_fixtures"] == 1
    assert status["both_market_fixtures"] == 1
    assert status["slots"]["forward"] == {"observations": 2, "baseline": 1, "context": 0,
                                         "match_winner": 1, "total_2_5": 2, "both_markets": 1}
    assert status["slots"]["preview"]["both_markets"] == 1
    assert status["slots"]["closing"]["both_markets"] == 0


def test_each_market_and_window_uses_its_own_paired_denominator():
    a, b, c, closing = _observation("71"), _observation("72"), _observation("73"), _observation("71", "closing")
    results = [_result(doc) for doc in (a, b, c)]
    results[0]["forward"] = _scored(a, 0.1, 0.3, 0.2)
    results[0]["closing"] = _scored(closing, 0.6, 0.1, 0.8)
    results[1]["forward"] = _scored(b, 0.2, None, 0.8)
    results[2]["forward"] = _scored(c, None, 0.9, 0.7)
    results[2]["forward"]["scores"]["total_2_5"]["market"] = None
    status = _summary(*results, a, b, c, closing)["leagues"]["por"]
    forward = status["paired_brier"]["forward"]
    assert forward["match_winner"]["baseline_vs_market"] == pytest.approx(
        {"pairs": 2, "model_mean": 0.15, "market_mean": 0.5, "delta_model_minus_market": -0.35})
    assert forward["match_winner"]["context_vs_market"] == pytest.approx(
        {"pairs": 2, "model_mean": 0.6, "market_mean": 0.45, "delta_model_minus_market": 0.15})
    assert forward["total_2_5"]["context_vs_market"]["pairs"] == 1
    assert forward["total_2_5"]["context_vs_market"]["market_mean"] == pytest.approx(0.2)
    assert status["paired_brier"]["closing"]["match_winner"]["baseline_vs_market"]["market_mean"] == 0.8
    assert status["paired_brier"]["closing"]["match_winner"]["context_vs_market"]["pairs"] == 1


@pytest.mark.parametrize("change", ["window", "market_window", "forecast_digest", "final_score", "rescheduled",
                                    "changed_away", "missing_original"])
def test_each_pair_must_have_the_original_observation_result_and_window(change):
    doc = _observation()
    result = _result(doc)
    result["forward"] = _scored(doc)
    if change == "window":
        result["forward"]["window"] = "closing"
    elif change == "market_window":
        result["forward"]["receipt"]["market_window"] = "closing"
    elif change == "forecast_digest":
        doc["forecast"]["context"] = None
    elif change == "final_score":
        result["score"] = [0, 1]
    elif change == "rescheduled":
        result["kickoff"] = "2026-11-28T15:00:00Z"
    elif change == "changed_away":
        result["away_id"] = "12"
    rows = [result] if change == "missing_original" else [result, doc]
    league = _summary(*rows)["leagues"]["por"]
    assert league["result_fixtures"] == 1
    assert league["distinct_fixtures"] == 1
    assert league["paired_brier"]["forward"]["match_winner"]["baseline_vs_market"]["pairs"] == 0


def test_different_model_phase_and_league_are_never_pooled():
    doc = _observation()
    result = _result(doc)
    result["forward"] = _scored(doc)
    status = _summary(doc, result, dict(doc, fixture_id="200", model_version="older"),
                      dict(doc, fixture_id="201", phase="playoff"), dict(doc, fixture_id="202", slot="result"),
                      dict(doc, fixture_id="203", kickoff="2026-11-28"), dict(doc, fixture_id="204", amount_cny=1),
                      dict(doc, fixture_id="205", research_only=False), _observation(league="tur"))["leagues"]
    assert status["por"]["distinct_fixtures"] == status["tur"]["distinct_fixtures"] == 1
    assert status["por"]["paired_brier"]["forward"]["match_winner"]["context_vs_market"]["pairs"] == 1
    assert status["tur"]["paired_brier"]["forward"]["match_winner"]["context_vs_market"]["pairs"] == 0


def test_conflicting_duplicates_are_visible_and_cannot_create_a_second_pair():
    doc = _observation()
    result = _result(doc)
    result["forward"] = _scored(doc)
    changed = dict(doc, observed_at=TIMES["preview"])
    for records in ((doc, changed, doc, result), (result, changed, doc)):
        league = _summary(*records)["leagues"]["por"]
        assert league["conflicting_records"] == 1
        assert league["paired_brier"]["forward"]["match_winner"]["context_vs_market"]["pairs"] == 0
    second_final = dict(result, kickoff="2026-11-29T14:00:00Z")
    league = _summary(doc, result, second_final)["leagues"]["por"]
    assert league["result_fixtures"] == league["conflicting_result_schedules"] == 1
    assert league["paired_brier"]["forward"]["match_winner"]["context_vs_market"]["pairs"] == 0


def test_zero_is_a_score_and_nonfinite_missing_or_boolean_values_are_omitted():
    doc = _observation()
    result = _result(doc)
    result["forward"] = _scored(doc, 0, float("nan"), 0.5)
    result["forward"]["scores"]["total_2_5"]["market"]["brier"] = True
    rows = _summary(doc, result)["leagues"]["por"]["paired_brier"]["forward"]
    assert rows["match_winner"]["baseline_vs_market"]["model_mean"] == 0
    assert rows["match_winner"]["context_vs_market"]["pairs"] == 0
    assert rows["total_2_5"]["baseline_vs_market"]["pairs"] == 0
