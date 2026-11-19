"""Offline regression tests for the international research CLI."""

import csv
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "wp_intl_validate", Path(__file__).resolve().parents[1] / "tools" / "wp_intl_validate.py",
)
assert _spec is not None and _spec.loader is not None
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

KICKOFF = datetime(2026, 1, 10, 20, tzinfo=timezone.utc)
BEFORE = KICKOFF - timedelta(minutes=10)
GAME = "AAA vs. BBB"
MATCHDAY = KICKOFF.date().isoformat()
TOURNAMENT = harness.NL_TOURNAMENT


def condition_id(index):
    return f"0x{index:064x}"


def total_market(**overrides):
    return {
        "question": f"{GAME}: O/U 2.5",
        "sportsMarketType": "totals",
        "line": 2.5,
        "gameStartTime": KICKOFF.isoformat(),
        "endDate": (KICKOFF + timedelta(hours=2)).isoformat(),
        "conditionId": condition_id(1),
        "outcomes": '["Over", "Under"]',
        "outcomePrices": '["0.55", "0.45"]',
        "clobTokenIds": '["1001", "1002"]',
        "description": (
            'This market will resolve to "Over" if AAA and BBB combine to score 3 or more goals in this game. '
            'If the combined total is less than 3, this market will resolve to "Under". '
            'This market refers only to the outcome within the first 90 minutes of regular play plus stoppage time.'
        ),
        "updatedAt": (BEFORE - timedelta(minutes=1)).isoformat(),
        "bestBid": 0.54,
        "bestAsk": 0.56,
        **overrides,
    }


def moneyline_market(question="Will AAA beat BBB?", **overrides):
    return total_market(**{
        "question": question,
        "sportsMarketType": "moneyline",
        "line": None,
        "conditionId": condition_id(100),
        "clobTokenIds": ["2000", "2001"],
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.68", "0.32"]',
        "bestBid": 0.67,
        "bestAsk": 0.69,
        "description": (
            "This is a UEFA Nations League game between AAA and BBB. "
            "This market refers only to the outcome within the first 90 minutes of regular play plus stoppage time."
        ),
        **overrides,
    })


def event(*markets, **overrides):
    return {
        "id": "1",
        "title": GAME,
        "closed": False,
        "endDate": (KICKOFF + timedelta(hours=2)).isoformat(),
        "markets": list(markets),
        **overrides,
    }


def match(match_date="2026-01-01", home_goals=1, away_goals=1):
    return {
        "date": match_date,
        "home": "AAA",
        "away": "BBB",
        "home_goals": home_goals,
        "away_goals": away_goals,
        "tournament": TOURNAMENT,
        "neutral": False,
        "venue": None,
    }


def snapshot(*matches):
    return {
        "generated_at": "2026-01-09T18:00:00Z",
        "teams": {"AAA": {"elo": 1510.0}, "BBB": {"elo": 1490.0}},
        "matches": list(matches),
    }


@pytest.fixture
def run_shadow(monkeypatch, tmp_path):
    def run(
        events, *, clock_at=BEFORE, discovery_finished=None, detail_finished=None,
        detail=None, model_snapshot=None, dry_run=False,
    ):
        now = clock_at
        calls = []

        def discover(*, closed):
            assert closed is False
            nonlocal now
            if discovery_finished is not None:
                now = discovery_finished
            return events

        def fetch(event_id):
            nonlocal now
            calls.append(event_id)
            if detail_finished is not None:
                now = detail_finished
            if detail is not None:
                return detail
            return next(payload for payload in events if payload["id"] == event_id)

        monkeypatch.setattr(harness, "_utc_now", lambda: now)
        monkeypatch.setattr(harness, "gamma_nl_events", discover)
        monkeypatch.setattr(harness, "fetch_event_detail", fetch)
        path = tmp_path / "shadow.csv"
        summary = harness.record_shadow_rows(
            model_snapshot if model_snapshot is not None else snapshot(match()),
            "synthetic-snapshot.json",
            path,
            date_lo=MATCHDAY,
            date_hi=MATCHDAY,
            dry_run=dry_run,
        )
        return summary, path, calls

    return run


@pytest.mark.parametrize(
    ("advance", "when", "request_count"),
    [
        ("discovery_finished", KICKOFF, 0),
        ("clock_at", KICKOFF + timedelta(minutes=1), 0),
        ("detail_finished", KICKOFF, 1),
        ("detail_finished", KICKOFF + timedelta(seconds=1), 1),
    ],
)
def test_shadow_never_records_at_or_after_game_start(run_shadow, advance, when, request_count):
    summary, path, calls = run_shadow([event(total_market())], **{advance: when})
    assert summary["rows_prepared"] == summary["rows_recorded"] == 0
    assert len(calls) == request_count
    assert "outside the pre-kickoff window" in summary["failures"][0]["reason"]
    assert not path.exists()


@pytest.mark.parametrize(
    ("discovery", "detail"),
    [
        (event(total_market(), live=True), None),
        (event(total_market()), event(total_market(), gameStatus="STATUS_IN_PROGRESS")),
        (event(total_market(gameStartTime=KICKOFF.replace(tzinfo=None).isoformat())), None),
        (event(total_market(gameStartTime=None)), None),
        (event(total_market()), event(total_market(), updatedAt="date is unknown")),
        (event(total_market(observedAt=KICKOFF.isoformat())), None),
        (event(total_market(observedAt=(BEFORE + timedelta(seconds=1)).isoformat())), None),
        (event(total_market(observedAt="2026-01-10T19:10:00")), None),
    ],
)
def test_live_and_unverifiable_timestamps_do_not_become_pregame_rows(run_shadow, discovery, detail):
    summary, path, _ = run_shadow([discovery], detail=detail)
    assert summary["rows_prepared"] == summary["rows_recorded"] == 0
    assert summary["failures"]
    assert not path.exists()


def test_shadow_ignores_corners_and_first_half_and_keeps_source_observed_unknown(run_shadow):
    flagship = event(total_market(
        question=f"{GAME}: First Half O/U 2.5", sportsMarketType="first_half_totals",
        conditionId=condition_id(2), outcomePrices='["0.12", "0.88"]',
    ))
    sibling = event(
        total_market(question=f"{GAME}: Corners O/U 2.5", line=2.5, conditionId=condition_id(3)),
        total_market(),
        id="2", title=f"{GAME} - More Markets",
    )
    summary, path, calls = run_shadow([flagship, sibling])
    assert summary["rows_recorded"] == 1
    assert calls == ["1", "2"]
    assert summary["totals_shadow"][0]["market_ou25.observed_utc"] is None
    with path.open(newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["state_at_scan"] == "pre"
    assert row["tip_utc"] == KICKOFF.isoformat()
    assert row["market_ou25.condition_id"] == condition_id(1)
    assert row["market_ou25.yes"] == "0.55"
    assert row["market_ou25.token"] == "1001"
    assert row["market_ou25.response_received_utc"] == BEFORE.isoformat()
    assert row["market_ou25.observed_utc"] == ""
    assert row["model.rating_source"] == "replay"
    assert row["model_totals_shadow.fallback_reason"] == "insufficient_recent_matches"
    assert row["neutral"] == ""
    assert row["model.neutral_assumed"] == "False"
    assert row["model.neutral_source"] == "home-field assumption"


def test_shadow_rejects_conflicting_start_times_across_sibling_events(run_shadow):
    other_kickoff = (KICKOFF + timedelta(hours=1)).isoformat()
    summary, path, calls = run_shadow([
        event(total_market()),
        event(total_market(gameStartTime=other_kickoff), id="2", title=f"{GAME} - More Markets"),
    ])
    assert summary["rows_prepared"] == 0
    assert {failure["reason"] for failure in summary["failures"]} == {"conflicting fixture kickoff times"}
    assert calls == []
    assert not path.exists()


def test_shadow_rejects_conflicting_kickoffs_on_different_utc_dates(run_shadow):
    summary, path, calls = run_shadow([
        event(total_market()),
        event(
            total_market(gameStartTime=(KICKOFF + timedelta(hours=6)).isoformat()),
            id="2", title=f"{GAME} - More Markets",
        ),
    ])
    assert summary["rows_prepared"] == 0
    assert summary["failures"] == [{"game": GAME, "reason": "conflicting fixture kickoff times"}]
    assert calls == []
    assert not path.exists()


def test_shadow_uses_explicit_neutral_venue_from_event_detail(run_shadow):
    payload = event(total_market())
    summary, path, _ = run_shadow([payload], detail={**payload, "neutral": True})
    assert summary["rows_recorded"] == 1
    with path.open(newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["neutral"] == "True"
    assert row["model.neutral_assumed"] == ""
    assert row["model.neutral_source"] == "Gamma"
    expected = harness.intl_model.fixture_prediction(
        snapshot(match()), harness.intl_model.IntlFixture("AAA", "BBB", MATCHDAY, neutral=True),
        tournament=TOURNAMENT,
    )
    assert expected is not None
    assert float(row["model.lambda_home"]) == pytest.approx(expected.lambda_home, abs=0.00005)


def test_shadow_records_reason_for_missing_valid_over_price(run_shadow):
    summary, path, _ = run_shadow([event(total_market(outcomePrices="[]"))])
    assert summary["rows_recorded"] == 1
    assert any("lacks a valid Over price" in item["reason"] for item in summary["failures"])
    with path.open(newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["market_ou25.yes"] == row["model_vs_market.cents.over_2p5"] == ""


def test_shadow_over_is_aligned_to_its_token_and_observed_timestamp(run_shadow):
    observed = (BEFORE - timedelta(seconds=15)).isoformat()
    market = total_market(
        outcomes='["Under", "Over"]', outcomePrices='["0.45", "0.55"]', observedAt=observed,
    )
    summary, path, _ = run_shadow([event(market)])
    assert summary["rows_recorded"] == 1
    with path.open(newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["market_ou25.yes"] == "0.55"
    assert row["market_ou25.token"] == "1002"
    assert row["market_ou25.observed_utc"] == observed
    assert row["market_ou25.bid"] == row["market_ou25.ask"] == ""


@pytest.mark.parametrize("reverse", [False, True])
def test_shadow_selects_yes_price_and_token_together(run_shadow, reverse):
    market = moneyline_market()
    if reverse:
        market.update(
            outcomes='["No", "Yes"]', outcomePrices='["0.32", "0.68"]',
            clobTokenIds=["2001", "2000"], bestBid=0.31, bestAsk=0.33,
        )
    _, path, _ = run_shadow([event(market, total_market())])
    with path.open(newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["market_ml.prices.home"] == "0.68"
    assert row["market_ml.tokens.home"] == "2000"
    assert row["market_ml.bid.home"] == ("" if reverse else "0.67")
    assert row["market_ml.ask.home"] == ("" if reverse else "0.69")


@pytest.mark.parametrize("overrides", [
    {"outcomes": '["Yes"]'},
    {"outcomes": '["Yes", "Yes"]'},
    {"outcomes": '["Yes", "Maybe"]'},
    {"clobTokenIds": ["2000"]},
    {"outcomePrices": '["0.68"]'},
    {"outcomePrices": '["0.68", "invalid"]'},
])
def test_shadow_reports_unusable_binary_moneyline(run_shadow, overrides):
    summary, path, _ = run_shadow([event(moneyline_market(**overrides), total_market())])
    assert any(
        item["reason"] in {"invalid binary ML contract for home", "no valid Yes price for home"}
        for item in summary["failures"]
    )
    with path.open(newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["market_ml.prices.home"] == row["market_ml.tokens.home"] == ""
    assert row["model_vs_market.cents.home"] == ""


def test_shadow_rejects_stale_current_table_quotes(run_shadow):
    summary, path, calls = run_shadow([event(total_market())], model_snapshot=snapshot())
    assert summary["rows_prepared"] == summary["rows_recorded"] == 0
    assert summary["failures"][0]["reason"] == "strictly prior replay ratings required"
    assert calls == ["1"]
    assert not path.exists()


def test_shadow_keeps_existing_csv_header_and_appends_idempotently(run_shadow, tmp_path):
    path = tmp_path / "shadow.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=harness.LEGACY_SHADOW_COLUMNS)
        writer.writeheader()
        writer.writerow({"tip_utc": "2026-01-09T20:00:00+00:00", "game": GAME})
    first, _, _ = run_shadow([event(total_market())])
    second, _, calls = run_shadow([event(total_market())])
    assert first["rows_recorded"] == 1
    assert first["totals_shadow"][0]["model_totals_shadow.version"]
    assert second["rows_recorded"] == 0
    assert second["skipped_existing"][0]["game"] == GAME
    assert calls == []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        assert reader.fieldnames == harness.LEGACY_SHADOW_COLUMNS
    assert len(rows) == 2 and None not in rows[1]


def test_totals_replay_cli_scores_two_predictions_on_each_fixture(monkeypatch, tmp_path, capsys):
    source = tmp_path / "snapshot.json"
    source.write_text("synthetic", encoding="utf-8")
    matches = snapshot(match(), match(MATCHDAY, 3, 0), match("2026-01-11", 8, 0))
    monkeypatch.setattr(harness.intl_data, "load_intl_snapshot", lambda _: matches)
    monkeypatch.setattr(harness, "_get_json", lambda _: pytest.fail("offline replay used the network"))
    assert harness.main([
        "totals-replay", "--snapshot", str(source), "--date-lo", MATCHDAY, "--date-hi", MATCHDAY,
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["paired_matches"] == report["over25_outcomes"] == 1
    assert report["sample_dates"] == [MATCHDAY, MATCHDAY]
    assert report["baseline"]["brier"] == report["shadow"]["brier"]
    assert report["baseline"]["log_loss_nats"] == report["shadow"]["log_loss_nats"]
    assert report["shadow"]["matchup_coverage"] == 0.0
    assert report["shadow"]["fallback_counts"] == {"insufficient_recent_matches": 1}
    assert report["snapshot"]["sha256"]
    assert report["fixed_elo_slope_evidence"]["reference_fit"] == "2018–2026 UEFA Nations League"


def _write_board(path, generated, markets):
    game = {
        "fixture": GAME, "kickoff_utc": KICKOFF.isoformat(),
        "ml_yes_prices": {"home": 0.34, "draw": 0.33, "away": 0.33},
        "ou25_yes_price": 0.99,
    }
    if markets is not None:
        game["market_evidence"] = markets
    path.write_text(json.dumps({
        "generated_utc": generated.isoformat(),
        "soccer": {"nations_league_nov12": [game]},
    }), encoding="utf-8")
    return path


def _collect_board(path):
    return harness.collect_pretip_board(
        path,
        {f"{MATCHDAY}|AAA|BBB": {"p_home": 0.4, "p_draw": 0.25, "p_away": 0.35, "p_over25": 0.55}},
        {"AAA", "BBB"},
    )


@pytest.mark.parametrize("generated", [KICKOFF, KICKOFF + timedelta(hours=3)])
def test_frozen_board_cannot_turn_late_prices_into_prekickoff_evidence(tmp_path, generated):
    path = _write_board(tmp_path / "board.json", generated, None)
    ml, totals, failures = _collect_board(path)
    assert ml == totals == []
    assert failures == [{"game": GAME, "reason": "board capture is not verified before kickoff"}]
    assert harness.gate_e_market([], ml, totals, {"board_failures": failures})["verdict"] == "INSUFFICIENT-DATA"


def test_pregame_legacy_board_requires_contract_and_market_observation(tmp_path):
    path = _write_board(tmp_path / "board.json", BEFORE, None)
    ml, totals, failures = _collect_board(path)
    assert ml == totals == []
    assert failures == [{"game": GAME, "reason": "missing frozen market and observation evidence"}]


@pytest.mark.parametrize("invalid_market", [
    {"sportsMarketType": "first_half_totals"},
    {"description": total_market()["description"].replace("3 or more", "4 or more")},
    {"description": total_market()["description"].replace("less than 3", "less than 4")},
    {"description": total_market()["description"].replace("goals in this game", "goals in the first half")},
    {"description": total_market()["description"] + " This market includes any extra time and penalty shootouts."},
    {"question": f"{GAME}: Corners O/U 2.5"},
    {"conditionId": ""},
    {"conditionId": "unknown"},
    {"clobTokenIds": ["1001", "0"]},
    {"clobTokenIds": ["1001", "01001"]},
    {"clobTokenIds": ["1001", "unknown"]},
    {"observed_utc": KICKOFF.isoformat()},
    {"observed_utc": None},
])
def test_frozen_board_rejects_wrong_total_scope_threshold_or_observation(tmp_path, invalid_market):
    market = total_market(**{"observed_utc": BEFORE.isoformat(), **invalid_market})
    _, totals, failures = _collect_board(_write_board(tmp_path / "board.json", BEFORE, [market]))
    assert totals == []
    assert any("full-match O/U 2.5 contract" in item["reason"] for item in failures)


def test_frozen_board_compares_only_identified_over_outcome(tmp_path):
    market = total_market(
        observed_utc=BEFORE.isoformat(), outcomes='["Under", "Over"]',
        outcomePrices='["0.42", "0.58"]',
        description=(
            total_market()["description"]
            + " If the game is abandoned and has no official result, this market will resolve 50-50."
            + " If the final score is unavailable within 2 hours, official results may be used."
        ),
    )
    _, totals, _ = _collect_board(_write_board(tmp_path / "board.json", BEFORE, [market]))
    assert len(totals) == 1
    assert totals[0]["market_observed_utc"] == BEFORE.isoformat()
    assert totals[0]["condition_id"] == condition_id(1)
    assert totals[0]["token"] == "1002"
    assert totals[0]["pm_raw"] == 0.58


@pytest.mark.parametrize("replace_scope", [True, False])
def test_frozen_board_rejects_first_half_settlement_even_with_regulation_text(tmp_path, replace_scope):
    description = total_market()["description"]
    if replace_scope:
        description = description.replace(
            "This market refers only to the outcome within the first 90 minutes of regular play plus stoppage time.",
            "Only goals in the first half of the 90 minutes of regular play plus stoppage time count toward settlement.",
        )
    else:
        description += " Only first-half goals count toward settlement."
    market = total_market(description=description, observed_utc=BEFORE.isoformat())
    _, totals, failures = _collect_board(_write_board(tmp_path / "board.json", BEFORE, [market]))
    assert totals == []
    assert any("full-match O/U 2.5 contract" in item["reason"] for item in failures)


@pytest.mark.parametrize("invalid_identity", [None, {"conditionId": "unknown"}, {"clobTokenIds": ["0", "2001"]}])
def test_frozen_board_requires_three_distinct_identified_moneylines(tmp_path, invalid_identity):
    quotes = []
    for index, (question, price) in enumerate([
        ("Will AAA win on 2026-01-10?", "0.4"),
        ("Will AAA vs. BBB end in a draw?", "0.25"),
        ("Will BBB win on 2026-01-10?", "0.35"),
    ]):
        quotes.append(moneyline_market(
            question=question,
            outcomes=["Yes", "No"], outcomePrices=[price, str(1 - float(price))],
            conditionId=condition_id(100 + index),
            clobTokenIds=[str(2000 + 2 * index), str(2001 + 2 * index)],
            observed_utc=BEFORE.isoformat(),
        ))
    for field in ("outcomes", "outcomePrices", "clobTokenIds"):
        quotes[0][field].reverse()
    if invalid_identity is not None:
        quotes[0].update(invalid_identity)
    ml, totals, failures = _collect_board(_write_board(tmp_path / "board.json", BEFORE, quotes))
    assert totals == []
    if invalid_identity is None:
        assert {row["side"]: row["pm_raw"] for row in ml} == {"home": 0.4, "draw": 0.25, "away": 0.35}
        assert {row["side"]: row["token"] for row in ml}["home"] == "2000"
        assert all(row["market_observed_utc"] == BEFORE.isoformat() for row in ml)
        assert len(failures) == 1
    else:
        assert ml == []
        assert any("ML contracts" in item["reason"] for item in failures)


def test_historical_closings_use_yes_tokens_and_report_their_market_mad(monkeypatch):
    kickoff = datetime(2024, 11, 15, 19, 45, tzinfo=timezone.utc)
    day = kickoff.date().isoformat()
    events, predictions, teams, token_prices = [], {}, {"BBB"}, {}
    expected_tokens = set()
    for number in range(10):
        home = f"Team {number}"
        teams.add(home)
        markets = []
        for side, (question, yes_price) in enumerate([
            (f"Will {home} beat BBB?", 0.68),
            (f"Will {home} vs. BBB end in a draw?", 0.20),
            (f"Will BBB beat {home}?", 0.12),
        ]):
            no, yes = str(2000 + 6 * number + 2 * side), str(2001 + 6 * number + 2 * side)
            token_prices.update({no: 1 - yes_price, yes: yes_price})
            expected_tokens.add(yes)
            markets.append(moneyline_market(
                question=question, gameStartTime=kickoff.isoformat(), sportsMarketType=None,
                outcomes='["No", "Yes"]', outcomePrices=None, clobTokenIds=[no, yes],
                conditionId=condition_id(100 + 3 * number + side),
            ))
        events.append(event(*markets, title=f"{home} vs. BBB", id=str(number)))
        predictions[f"{day}|{home}|BBB"] = {
            "p_home": 0.16, "p_draw": 0.40, "p_away": 0.44, "p_over25": 0.5,
        }
    requested = []

    def close(token, kickoff_epoch):
        assert kickoff_epoch == int(kickoff.timestamp())
        requested.append(token)
        return {"price": token_prices[token], "minutes_before": 5}

    monkeypatch.setattr(harness, "closing_yes_price", close)
    monkeypatch.setattr(harness.time, "sleep", lambda _: None)
    rows, totals, notes = harness.collect_nov2024_closings(predictions, teams, events=events)
    assert len(requested) == 30 and set(requested) == expected_tokens
    assert len(rows) == 30 and totals == [] and notes["failures"] == []
    expected_prices = {"home": 0.68, "draw": 0.20, "away": 0.12}
    assert {row["token"] for row in rows} == expected_tokens
    for row in rows:
        assert row["pm_devig"] == pytest.approx(expected_prices[row["side"]])
    gate = harness.gate_e_market(rows, [], totals, notes)
    assert gate["closing_1x2"]["mad"] == pytest.approx(1.04 / 3)
    assert gate["verdict"] == "FAIL"

    requested.clear()
    events[0]["markets"][0]["outcomes"] = '["No", "No"]'
    rows, _, notes = harness.collect_nov2024_closings(predictions, teams, events=events)
    assert len(requested) == len(rows) == 27
    assert notes["failures"] == [{"game": "Team 0 vs. BBB", "reason": "missing ML markets ['home']"}]


def test_gate_a_uses_refresh_manifest_and_checks_input_hashes(monkeypatch, tmp_path):
    frozen = snapshot(match())
    frozen.update(source="intl-martj42-eloratings", elo_history=[])
    target = tmp_path / "snapshot.json"
    target.write_text(json.dumps(frozen), encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    manifest_sources = {}
    for key, name in zip(harness._REFRESH_INPUTS, [
        "results.csv", "world.tsv", "latest.tsv", "en.teams.tsv", "en.tournaments.tsv",
    ]):
        path = (tmp_path if key == "results" else cache) / name
        path.write_text(key, encoding="utf-8")
        manifest_sources[key] = {"file": name, "sha256": harness._sha256(path)}
    espn = tmp_path / "scoreboard.json"
    espn.write_text("[]", encoding="utf-8")
    manifest = {
        "sources": manifest_sources,
        "espn_scoreboards": [{"file": espn.name, "sha256": harness._sha256(espn)}],
        "generated_at": frozen["generated_at"],
        "cutoff_utc_date_exclusive": "2026-01-10",
        "date_basis": "source_fixture_calendar_date",
        "exclude_late_espn_kickoffs": True,
        "espn_kickoff_excluded_count": 1,
        "model_date_shifted_count": 1,
        "seed_match_count": 1, "supplement_match_count": 0, "match_count": 1,
    }
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(manifest), encoding="utf-8")
    calls = []

    def rebuild(seed, world, **kwargs):
        calls.append((seed, world, kwargs))
        return frozen, manifest

    monkeypatch.setattr(harness.intl_data, "build_refreshed_snapshot", rebuild)
    monkeypatch.setattr(harness.intl_data, "build_snapshot", lambda *args: pytest.fail("used legacy rebuild"))
    kwargs = {"refresh_evidence_path": evidence_path, "results_csv_path": tmp_path / "results.csv"}
    report = harness.gate_a_integrity(target, frozen, cache, espn_scoreboards=[espn], **kwargs)
    assert report["rebuild_from_cache_equal"] is True
    assert report["snapshot_sha256"] == harness._sha256(target)
    assert report["refresh"]["cutoff_utc_date_exclusive"] == "2026-01-10"
    assert report["refresh"]["espn_kickoff_excluded_count"] == 1
    assert report["refresh"]["model_date_shifted_count"] == 1
    assert calls[0][0] == tmp_path / "results.csv"
    assert calls[0][2]["cutoff_date"] == "2026-01-10"
    assert calls[0][2]["exclude_late_espn_kickoffs"] is True
    assert calls[0][2]["espn_scoreboards"] == [espn]
    missing = harness.gate_a_integrity(target, frozen, cache, **kwargs)
    assert "ESPN scoreboard paths/SHA-256 do not match" in missing["rebuild_from_cache_equal"]
    (cache / "latest.tsv").write_text("something else", encoding="utf-8")
    bad = harness.gate_a_integrity(target, frozen, cache, espn_scoreboards=[espn], **kwargs)
    assert "refresh input latest SHA-256 does not match" in bad["rebuild_from_cache_equal"]
    assert len(calls) == 1


def test_gate_a_retains_legacy_two_source_rebuild(monkeypatch, tmp_path):
    frozen = snapshot(match())
    frozen.update(source="intl-martj42-eloratings", elo_history=[])
    target = tmp_path / "snapshot.json"
    target.write_text(json.dumps(frozen), encoding="utf-8")
    (tmp_path / "results.csv").touch()
    (tmp_path / "world.tsv").touch()
    monkeypatch.setattr(harness.intl_data, "build_snapshot", lambda *args: frozen)
    report = harness.gate_a_integrity(target, frozen, tmp_path)
    assert report["rebuild_from_cache_equal"] is True
    assert report["rebuild_mode"] == "legacy two-source rebuild"
