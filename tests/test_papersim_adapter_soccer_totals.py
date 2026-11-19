import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from polymarket_alpha_lab.papersim.adapters.soccer_totals import compare_totals


@pytest.fixture
def evidence():
    kickoff = "2026-11-17T19:45Z"
    polymarket_fixture = dict(source_id="polymarket", fixture_id="90108501",
                              home_team_id="3268268", away_team_id="3268262")
    external_fixture = dict(source_id="espn", fixture_id="401861195",
                            home_team_id="471", away_team_id="466")
    period = "regulation_90_plus_stoppage"
    total = dict(kind="total_goals", period=period, line="3.5")
    condition = "0x" + "a" * 64
    market = dict(total, source_reference="gamma-snapshot", fetched_at="2026-11-17T18:53Z",
                  kickoff_at=kickoff, fixture_id="90108501", market_id="6674237",
                  condition_id=condition, outcomes=["Under", "Over"], token_ids=["20", "10"],
                  fee_schedule={"rate": "0.05", "exponent": 1, "takerOnly": True},
                  active=True, closed=False, accepting_orders=True)
    shared_rules = dict(
        fetched_at="2026-11-17T18:45Z", period=period,
        canceled="split_50_50", postponed="split_50_50", start_deadline_at="2026-11-20T04:59Z",
        abandoned_determined="settle_determined", abandoned_resolution_order=["official_ruling", "declared_score"],
        abandonment_deadline_at="2026-11-20T04:59Z", abandoned_no_result="split_50_50",
        missing_result="split_50_50", result_deadline_seconds=86400, result_sources=["uefa_nations_league"],
    )
    pm_rules = dict(shared_rules, source_reference="gamma-rule-text", market_id=market["market_id"],
                    fixture_id="90108501", condition_id=condition)
    external_rules = dict(shared_rules, source_reference="provider-rule-text", market_id="35025160/0OU87257387",
                          fixture_id="401861195", provider_id="100")
    shared_book = dict(market=condition, source_reference="clob-snapshot", fetched_at="2026-11-17T18:57:12Z",
                       timestamp=round(datetime.fromisoformat("2026-11-17T18:57:11Z").timestamp() * 1000),
                       min_order_size="5")
    books = [
        dict(shared_book, asset_id="10", asks=[dict(price="0.39", size="100")]),
        dict(shared_book, asset_id="20", asks=[dict(price="0.59", size="100"), dict(price="0.56", size="10")]),
    ]
    quote = dict(total, source_id="espn", fixture_id="401861195", provider_id="100",
                 market_id="35025160/0OU87257387", source_reference="espn-snapshot",
                 fetched_at="2026-11-17T18:57:38Z", observed_at="2026-11-17T18:57:30Z")
    return {
        "decision_at": "2026-11-17T18:58Z",
        "fixture_binding": dict(source_reference="fixture-crosswalk", kickoff_at=kickoff,
                                polymarket=polymarket_fixture, external=external_fixture),
        "polymarket": dict(fixture=dict(polymarket_fixture, kickoff_at=kickoff),
                           market=market, rules=pm_rules, books=books),
        "external": dict(fixture=dict(external_fixture, kickoff_at=kickoff), rules=external_rules,
                         quotes=[dict(quote, side="Over", american_odds="+140"),
                                 dict(quote, side="Under", american_odds="-180", line=3.5)]),
    }


def test_same_line_comparison_preserves_outcome_labels_and_signed_cost_difference(evidence):
    result = compare_totals(evidence)
    assert result["status"] == "accepted"
    assert result["reasons"] == []
    assert result["differences_indicative"] is False
    assert result["paper_only"] is result["report_only"] is result["readonly"] is True
    assert result["line"] == "3.5"
    assert result["polymarket"]["rules"]["canceled"] == "split_50_50"

    over, under = (result["sides"][label] for label in ("Over", "Under"))
    assert (over["token_id"], under["token_id"]) == ("10", "20")
    assert over["reference_probability"] == pytest.approx(0.39325842696629215)
    assert under["reference_probability"] == pytest.approx(1 - over["reference_probability"])
    assert over["buy_vwap"] == 0.39
    assert over["estimated_taker_fee_per_share"] == pytest.approx(0.011895)
    assert over["estimated_buy_cost_per_share"] == pytest.approx(0.401895)
    assert over["net_reference_difference_cents"] == pytest.approx(-0.8636573)
    assert under["buy_vwap"] == round(20 / (10 + 14.4 / 0.59), 4)
    assert under["net_reference_difference_cents"] > 0


def test_frozen_comparison_preserves_indicative_values_when_observations_are_missing(evidence):
    for quote in evidence["external"]["quotes"]:
        quote["observed_at"] = None
        quote["fetched_at"] = "2026-11-17T18:58:57.643079Z"
    evidence["external"]["rules"] = None
    evidence["decision_at"] = "2026-11-17T18:59Z"
    del evidence["polymarket"]["market"]["fetched_at"]

    result = compare_totals(evidence)
    assert result["status"] == "rejected"
    assert result["differences_indicative"] is True
    assert result["reasons"] == [
        "polymarket_market_fetched_at_missing", "external_observed_at_missing", "external_rules_missing",
    ]
    assert result["polymarket"]["fetched_at"] is None
    assert result["external"]["fetched_at"] == "2026-11-17T18:58:57.643079+00:00"
    assert result["external"]["rules"] is None
    assert result["sides"]["Over"]["external_observed_at"] is None
    assert result["sides"]["Over"]["net_reference_difference_cents"] == pytest.approx(-0.8636573)


@pytest.mark.parametrize(("path", "value", "reason"), [
    (("decision_at",), "2026-11-17T19:45Z", "decision_at_or_after_kickoff"),
    (("external", "fixture", "home_team_id"), "466", "fixture_teams_not_distinct"),
    (("external", "fixture", "away_team_id"), "467", "external_fixture_binding_conflict"),
    (("polymarket", "market", "kickoff_at"), "2026-11-17T20:45Z", "polymarket_market_kickoff_conflict"),
    (("polymarket", "market", "fixture_id"), "90108502", "polymarket_market_fixture_conflict"),
    (("polymarket", "market", "outcomes"), ["Yes", "No"], "polymarket_outcomes_unsupported"),
    (("polymarket", "market", "fetched_at"), "2026-11-17T18:58Z", "polymarket_market_fetched_at_or_after_decision"),
    (("external", "quotes"), [], "external_quote_pair_required"),
    (("external", "quotes", 1, "fixture_id"), "401861194", "external_quote_fixture_conflict"),
    (("external", "quotes", 1, "line"), "2.5", "external_line_conflict"),
    (("external", "quotes", 1, "line"), "3.50000000000000000000000000001", "external_line_not_half_goal"),
    (("external", "quotes", 1, "kind"), "total_corners", "external_kind_unsupported"),
    (("external", "quotes", 1, "period"), "first_half", "external_period_unsupported"),
    (("external", "quotes", 1, "provider_id"), "101", "external_quote_capture_conflict"),
    (("external", "quotes", 1, "market_id"), "35025160/other-market", "external_quote_capture_conflict"),
    (("external", "quotes", 1, "fetched_at"), "2026-11-17T18:57:39Z", "external_quote_capture_conflict"),
    (("external", "quotes", 1, "observed_at"), "2026-11-17T18:57:31Z", "external_quote_observation_conflict"),
    (("external", "quotes", 1, "observed_at"), "2026-11-17T18:50Z", "external_observation_stale"),
    (("external", "quotes", 1, "observed_at"), "2026-11-17T18:57:39Z", "external_observation_after_capture"),
    (("external", "quotes", 1, "fetched_at"), "2026-11-17T18:58Z", "external_fetched_at_or_after_decision"),
    (("external", "quotes", 1, "american_odds"), True, "external_american_odds_invalid"),
    (("external", "rules", "canceled"), "void", "settlement_rules_conflict"),
    (("external", "rules", "provider_id"), "other", "external_rules_identity_conflict"),
    (("external", "rules", "result_deadline_seconds"), None, "external_rules_incomplete_or_unsupported"),
    (("polymarket", "books", 0, "asset_id"), "30", "book_token_conflict"),
    (("polymarket", "books", 0, "market"), "0x" + "b" * 64, "book_condition_conflict"),
    (("polymarket", "books", 0, "timestamp"), None, "book_observed_at_missing:Over"),
    (("polymarket", "books", 0, "timestamp"), 0, "book_observation_stale:Over"),
    (("polymarket", "books", 0, "fetched_at"), "2026-11-17T19:45Z", "book_fetched_at_or_after_decision:Over"),
    (("polymarket", "books", 0, "asks"), [], "book_depth_insufficient:Over"),
    (("polymarket", "books", 0, "min_order_size"), "60", "book_min_order_size_not_met:Over"),
    (("polymarket", "market", "fee_schedule", "rate"), "0.06", "polymarket_fee_schedule_conflict"),
])
def test_rejects_unusable_evidence(evidence, path, value, reason):
    target = evidence
    for field in path[:-1]:
        target = target[field]
    target[path[-1]] = value

    result = compare_totals(evidence)
    assert result["status"] == "rejected"
    assert result["reasons"] == [reason]


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("active", False, "inactive"),
    ("closed", True, "closed"),
    ("accepting_orders", False, "not_accepting_orders"),
    ("active", None, "active_missing"),
    ("closed", None, "closed_missing"),
    ("accepting_orders", None, "accepting_orders_missing"),
    ("active", 1, "active_invalid"),
    ("closed", 0, "closed_invalid"),
    ("accepting_orders", "true", "accepting_orders_invalid"),
    ("fetched_at", "2026-11-17T18:52:59.999999Z", "capture_stale"),
])
def test_unavailable_or_stale_market_only_yields_indicative_differences(evidence, field, value, reason):
    evidence["polymarket"]["market"][field] = value
    result = compare_totals(evidence)
    assert result["status"] == "rejected"
    assert result["reasons"] == [f"polymarket_market_{reason}"]
    assert result["differences_indicative"] is True
    assert result["sides"]["Over"]["net_reference_difference_cents"] == pytest.approx(-0.8636573)


def test_thin_book_reports_available_depth_without_full_fill_difference(evidence):
    evidence["polymarket"]["books"][0]["asks"][0]["size"] = "10"
    result = compare_totals(evidence)
    assert result["status"] == "rejected"
    row = result["sides"]["Over"]
    assert row["book_rejection"] == "book_depth_insufficient"
    assert row["filled"] is False
    assert row["filled_notional_usd"] == 3.9
    assert "net_reference_difference_cents" not in row
    assert result["sides"]["Under"]["net_reference_difference_cents"] > 0


def test_insufficient_depth_inside_walk_tolerance_is_rejected(evidence):
    evidence["polymarket"]["books"][0]["asks"][0]["size"] = "51.28205"
    result = compare_totals(evidence)
    assert result["reasons"] == ["book_depth_insufficient:Over"]
    assert result["sides"]["Over"]["filled"] is False


def test_settlement_report_preserves_conflicting_payout_terms(evidence):
    evidence["external"]["rules"]["canceled"] = "void"
    evidence["external"]["rules"]["abandonment_deadline_at"] = "2026-11-21T04:59Z"
    result = compare_totals(evidence)
    assert result["reasons"] == ["settlement_rules_conflict"]
    assert result["settlement_rule_conflicts"] == ["canceled", "abandonment_deadline_at"]
    assert result["polymarket"]["rules"]["canceled"] == "split_50_50"
    assert result["external"]["rules"]["canceled"] == "void"


@pytest.mark.parametrize("stdin", [True, False])
def test_command_reads_json_and_preserves_exact_line(evidence, tmp_path, stdin):
    payload = json.dumps(evidence)
    if stdin:
        # An ordinary float JSON parser rounds this number to 3.5.
        payload = payload.replace('"line": 3.5', '"line": 3.50000000000000000000000000001')
    path = tmp_path / "evidence.json"
    if not stdin:
        path.write_text(payload, encoding="utf-8")
    source = Path(__file__).resolve().parents[1] / "src"
    command = [sys.executable, "-B", "-m", "polymarket_alpha_lab.papersim.adapters.soccer_totals"]
    result = subprocess.run(
        command if stdin else [*command, str(path)],
        input=payload if stdin else None, capture_output=True, text=True, check=True,
        env={**os.environ, "PYTHONPATH": str(source)},
    )
    report = json.loads(result.stdout)
    assert report["status"] == ("rejected" if stdin else "accepted")
    assert report["reasons"] == (["external_line_not_half_goal"] if stdin else [])
