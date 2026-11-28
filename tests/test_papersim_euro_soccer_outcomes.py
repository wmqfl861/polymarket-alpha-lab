"""An independently observed regulation score can only grade the matching archived forecast."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import math

import pytest

from polymarket_alpha_lab.papersim.euro_soccer_outcomes import observation_id, score_outcome
from polymarket_alpha_lab.papersim.euro_soccer_sources import Fixture


KICKOFF = datetime(2026, 11, 28, 18, tzinfo=UTC)
FINAL_AT = KICKOFF + timedelta(hours=2)
NOW = FINAL_AT + timedelta(minutes=5)
FINAL = Fixture("por", "2026/2027", "regular", "12", "10", "20", "Benfica", "Porto",
                KICKOFF, "finished", (2, 2), FINAL_AT, "f" * 64)


def document(minutes=25, slot="forward"):
    cutoff = KICKOFF - timedelta(minutes=minutes)
    captured = cutoff.isoformat()
    forecast_at = (cutoff - timedelta(seconds=1)).isoformat()
    ident = dict(league=FINAL.league, season=FINAL.season, phase=FINAL.phase, fixture_id=FINAL.fixture_id,
                 home_id=FINAL.home_id, away_id=FINAL.away_id, home_name=FINAL.home_name, away_name=FINAL.away_name,
                 kickoff=KICKOFF.isoformat())
    p = {"home_win": .5, "draw": .2, "away_win": .3, "over_2_5": .4, "under_2_5": .6}
    model = {"league": "por", "fixture_id": "12", "as_of": forecast_at, "version": "v1", "research_only": True,
             "amount_cny": 0, "status": "team_news_scenario", "source_refs": ["c"*64, "a"*64],
             "baseline": p, "context": p | {"home_win": .4, "draw": .3, "over_2_5": .48, "under_2_5": .52},
             "news": {"source_refs": ["a"*64], "fetched_at": forecast_at, "blockers": []}}
    batch = {"status": "observed", "book_snapshot_sha256": "b"*64, "observed_at": captured}
    market = {"fixture": {"league": "por", "fixture_id": "12", "home": "Benfica", "away": "Porto",
                          "kickoff": KICKOFF.isoformat()}, "observed_at": captured, "status": "complete",
              "window": "pregame" if slot == "preview" else slot, "book_snapshot_sha256": "b"*64,
              "source_sha256s": ["b"*64, "d"*64],
              "families": {"match_winner": batch | {"market_probabilities": {"home": .4, "draw": .25, "away": .35}},
                           "total_2_5": batch | {"market_probabilities": {"over": .45, "under": .55}}}}
    return {"fixture": ident, **ident, "observed_at": captured, "slot": slot, "model_version": "v1",
            "forecast": model, "markets": market, "research_only": True, "amount_cny": 0,
            "calendar_sha256": "c"*64, "observation_id": "v1/por/12/" + slot}


def test_all_six_scores_against_independent_ft_and_replay_hash():
    obs = document()
    result = score_outcome(obs, FINAL, as_of=NOW)
    assert result["result"] == {"match_winner": "draw", "total_2_5": "over", "home_goals": 2, "away_goals": 2}
    assert result["score"] == [2, 2] and result["missing"] == {}
    assert result["window"] == "forward" and result["receipt"]["market_window"] == "forward"
    assert result["receipt"]["result_source_sha256"] == FINAL.source_sha256
    assert result["receipt"]["managed_observation_id"] == obs["observation_id"]
    assert result["observation_id"] == observation_id(obs) == observation_id(dict(reversed(obs.items())))
    assert score_outcome(obs, FINAL, as_of=NOW + timedelta(days=1)) == result
    assert observation_id(obs | {"observed_at": FINAL_AT.isoformat()}) != result["observation_id"]
    for source, win, over in (("baseline", (.5, .2, .3), .4), ("context", (.4, .3, .3), .48),
                              ("market", (.4, .25, .35), .45)):
        assert result["scores"]["match_winner"][source] == pytest.approx(
            {"brier": win[0]**2 + (win[1]-1)**2 + win[2]**2, "log_loss": -math.log(win[1])})
        assert result["scores"]["total_2_5"][source] == pytest.approx(
            {"brier": (over-1)**2, "log_loss": -math.log(over)})


@pytest.mark.parametrize("score,winner,under", [((0, 1), "away", True), ((2, 0), "home", True), ((3, 0), "home", False)])
def test_other_regulation_outcomes_and_slot_pairing(score, winner, under):
    obs = document(90, "preview")
    result = score_outcome(obs, replace(FINAL, score=score), as_of=NOW)
    assert result["window"] == "preview" and result["receipt"]["market_window"] == "pregame"
    assert result["result"]["match_winner"] == winner
    assert result["scores"]["total_2_5"]["baseline"]["log_loss"] == pytest.approx(-math.log(.6 if under else .4))
    assert score_outcome(document(2, "closing"), FINAL, as_of=NOW)["missing"] == {}


@pytest.mark.parametrize("change", [
    {"status": "scheduled"}, {"status": "awarded"}, {"score": None}, {"score": (True, 0)}, {"score": (-1, 0)},
    {"score": (1.5, 0)}, {"league": "scop"}, {"season": "2025/2026"}, {"phase": "split"}, {"fixture_id": "90"},
    {"home_id": "20", "away_id": "10"}, {"kickoff": KICKOFF+timedelta(minutes=1)}, {"observed_at": KICKOFF},
    {"observed_at": NOW+timedelta(microseconds=1)}, {"source_sha256": "missing"},
])
def test_unverified_result_identity_time_and_scores_return_no_receipt(change):
    assert score_outcome(document(), replace(FINAL, **change), as_of=NOW) is None


@pytest.mark.parametrize("change", [
    {"observed_at": KICKOFF.isoformat()}, {"observed_at": KICKOFF.replace(tzinfo=None).isoformat()},
    {"slot": "closing"}, {"fixture_id": "999"}, {"amount_cny": 1}, {"research_only": False},
])
def test_invalid_outer_observation_is_rejected(change):
    assert score_outcome(document() | change, FINAL, as_of=NOW) is None
    assert score_outcome(document(), FINAL, as_of=NOW.replace(tzinfo=None)) is None


@pytest.mark.parametrize("change,reason", [
    ({"as_of": KICKOFF.isoformat()}, "forecast_outside_observation"),
    ({"as_of": (KICKOFF-timedelta(hours=2)).isoformat()}, "forecast_outside_observation"),
    ({"version": "other"}, "forecast_identity_invalid"), ({"amount_cny": 1}, "forecast_identity_invalid"),
    ({"source_refs": ["a"*64]}, "forecast_source_receipt_missing"),
])
def test_bad_model_does_not_erase_observed_market(change, reason):
    obs = document()
    obs["forecast"].update(change)
    result = score_outcome(obs, FINAL, as_of=NOW)
    assert result["missing"]["match_winner"] == {"baseline": reason, "context": reason}
    assert result["scores"]["total_2_5"]["market"] and result["scores"]["total_2_5"]["context"] is None


@pytest.mark.parametrize("update,reason", [
    ({"blockers": ["not_yet_available"]}, "news_unavailable_or_blocked"),
    ({"fetched_at": KICKOFF.isoformat()}, "news_outside_forecast_window"),
    ({"fetched_at": (KICKOFF-timedelta(hours=1)).isoformat()}, "news_outside_forecast_window"),
    ({"source_refs": ["e"*64]}, "news_source_receipt_missing"),
])
def test_unavailable_context_preserves_baseline_and_market(update, reason):
    obs = document()
    obs["forecast"]["news"].update(update)
    result = score_outcome(obs, FINAL, as_of=NOW)
    assert result["missing"] == {family: {"context": reason} for family in result["scores"]}
    assert result["scores"]["match_winner"]["baseline"] and result["scores"]["total_2_5"]["market"]


@pytest.mark.parametrize("unsafe", [True, None, float("nan"), float("inf"), 1.1, -.01, .7])
def test_invalid_probability_only_drops_its_own_source_and_family(unsafe):
    obs = document()
    obs["forecast"]["baseline"]["draw"] = unsafe
    result = score_outcome(obs, FINAL, as_of=NOW)
    assert result["missing"] == {"match_winner": {"baseline": "invalid_probability_distribution"}}
    assert result["scores"]["match_winner"]["market"] and result["scores"]["total_2_5"]["baseline"]


@pytest.mark.parametrize("change,reason", [
    ({"observed_at": KICKOFF.isoformat()}, "market_outside_observation"),
    ({"book_snapshot_sha256": "0"*64}, "market_batch_receipt_missing"), ({"window": "pregame"}, "market_window_mismatch"),
    ({"fixture": {"league": "por", "home": "Porto", "away": "Benfica", "kickoff": KICKOFF.isoformat()}},
     "market_fixture_mismatch"),
])
def test_unusable_market_never_erases_model_forecasts(change, reason):
    obs = document()
    obs["markets"].update(change)
    result = score_outcome(obs, FINAL, as_of=NOW)
    assert result["missing"] == {family: {"market": reason} for family in result["scores"]}


@pytest.mark.parametrize("change,reason", [
    ({"status": "unavailable"}, "market_family_unavailable"),
    ({"book_snapshot_sha256": "e"*64}, "market_family_batch_mismatch"),
    ({"observed_at": (KICKOFF-timedelta(hours=1)).isoformat()}, "market_family_batch_mismatch"),
    ({"market_probabilities": {"home": .5, "draw": .5}}, "invalid_probability_distribution"),
])
def test_one_market_family_cannot_borrow_another_quote(change, reason):
    obs = document()
    obs["markets"]["families"]["match_winner"].update(change)
    result = score_outcome(obs, FINAL, as_of=NOW)
    assert result["missing"] == {"match_winner": {"market": reason}}
    assert result["scores"]["total_2_5"]["market"]


def test_result_still_has_a_receipt_when_all_six_inputs_are_missing():
    obs = document() | {"markets": None, "forecast": None}
    result = score_outcome(obs, FINAL, as_of=NOW)
    assert all(value is None for group in result["scores"].values() for value in group.values())
    assert len(result["missing"]["total_2_5"]) == 3
    assert result["score"] == [2, 2] and result["amount_cny"] == 0
