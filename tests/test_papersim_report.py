"""B4 report offline orchestration proofs (plan 05 section 2 / 80.8).

Everything here runs without a database and without a network: the
pipeline rows are synthetic read-only SELECT outputs handed to the pure
assembly, and the queue metrics come from ``evaluation.evaluate_queues``
over injected ``EvaluationCandidate`` rows.  The native lane
(tests/test_papersim_report_store_native.py) proves the same assembly
against a disposable cluster seeded through the ``run_scan`` path.

Pinned contracts:

- Stage matrix: scan evidence present with marks/outcomes/settlements
  absent renders ``STAGE_NOT_WIRED``/``NO_DATA`` with honest counts --
  never zero-filled or synthesized coverage.
- Determinism: two assemblies from identical inputs are byte-identical;
  the report key is content-addressed from scope + data-derived as_of +
  digest and never the writing clock.
- Basketball OT fixture: an overtime game's completed final score
  (118-112 after OT) is displayed and settled as the FULL-GAME final --
  no regulation-only filtering anywhere -- while soccer stays pinned to
  ``regulation_90_plus_stoppage`` in its own adapter.
"""

from __future__ import annotations

import datetime
import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from itertools import permutations

import pytest

from polymarket_alpha_lab.papersim import evaluation, pipeline, report, scan, store
from polymarket_alpha_lab.papersim.adapters import soccer_totals
from polymarket_alpha_lab.papersim import snapshots as snapshots_module

NOW = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.UTC)
LATER = NOW + datetime.timedelta(minutes=30)
EVEN_LATER = NOW + datetime.timedelta(hours=1)

#: The CLI subcommand registry at this node's base (B1 merged; B2/B3 not).
WIRED_AT_BASE = frozenset({"selfcheck", "schema", "validate-dsn",
                           "evaluate", "replay", scan.SCAN_COMMAND})

EXPIRY = datetime.datetime(2026, 12, 31, 0, 0, tzinfo=datetime.UTC)


def _receipt(command, operation_key, phase, occurred_at, *, attempt=1,
             run_event_id=None):
    row = {"command": command, "operation_key": operation_key,
           "phase": phase, "attempt": attempt, "occurred_at": occurred_at}
    if run_event_id is not None:
        row["run_event_id"] = run_event_id
    return row


def _scan_run(scan_id, *, team="pal-b1", finished_at=NOW,
              started_at=None, code_commit="a" * 40):
    return {"scan_id": scan_id, "team": team,
            "started_at": started_at or finished_at - datetime.timedelta(minutes=5),
            "finished_at": finished_at, "code_commit": code_commit,
            "scan_key": f"sk{scan_id}"}


def _settled_row(index: int) -> evaluation.EvaluationCandidate:
    return evaluation.EvaluationCandidate(
        candidate_id=f"c{index}",
        model_key="crypto",
        market_type="terminal",
        gate_version=pipeline.GATE_VERSION,
        market_key=f"m{index}",
        accepted=True,
        stake=50.0,
        fill_price=0.5,
        side="YES",
        fee_category="crypto",
        outcome="loss",
        recorded_pnl=-70.0,
        decision_time=NOW - datetime.timedelta(days=2),
        settled_time=NOW - datetime.timedelta(days=1),
    )


def _inputs(**overrides) -> report.ReportInputs:
    """The base offline fixture: one succeeded scan, nothing else wired."""
    fields = {
        "scope": report.REPORT_SCOPE_ALL,
        "wired_commands": WIRED_AT_BASE,
        "command_run_events": (
            _receipt(scan.SCAN_COMMAND, "run-scan|pal-b1|t1|b1", "STARTED",
                     NOW - datetime.timedelta(minutes=5), run_event_id=1),
            _receipt(scan.SCAN_COMMAND, "run-scan|pal-b1|t1|b1", "SUCCEEDED",
                     NOW, run_event_id=2),
            _receipt("evaluate", "evaluate|cli", "SUCCEEDED",
                     NOW - datetime.timedelta(minutes=1), run_event_id=3),
        ),
        "scan_runs": (_scan_run(1),),
        "queue_control_events": (),
        "bankroll_checks": (),
        "coverage": report.PipelineCoverage(candidates_total=2,
                                            candidates_with_marks=0,
                                            mark_observations=0,
                                            markets_bound=2,
                                            markets_with_outcome=0,
                                            outcome_observations=0,
                                            entries_total=1,
                                            entries_settled=0),
        "basketball_finals": (),
        "evaluation": evaluation.evaluate_queues(
            (_settled_row(index) for index in range(3))),
    }
    fields.update(overrides)
    return report.ReportInputs(**fields)


# ---------------------------------------------------------------------------
# Stage matrix (plan acceptance test 1)
# ---------------------------------------------------------------------------


def test_stage_matrix_scan_present_marks_outcomes_settlements_absent():
    assembly = report.assemble_report(_inputs())
    stages = assembly.document["stages"]

    assert assembly.document["stage_order"] == list(report.STAGE_ORDER)
    # Causal order with the exact stage commands.
    assert report.STAGE_COMMANDS == {
        "scan": "run-scan", "marks": "capture-marks", "outcomes": "settle",
        "settle": "settle", "evaluate": "evaluate"}

    assert stages["scan"]["status"] == report.STAGE_STATUS_OK
    assert stages["scan"]["wired"] is True
    assert stages["scan"]["row_count"] == 1
    assert stages["scan"]["candidates"] == 2
    assert stages["scan"]["entries"] == 1
    assert stages["scan"]["last_scan"]["scan_id"] == 1
    assert stages["scan"]["as_of"] == NOW.isoformat()

    for stage in ("marks", "outcomes", "settle"):
        assert stages[stage]["status"] == report.STAGE_STATUS_NOT_WIRED
        assert stages[stage]["wired"] is False
        assert stages[stage]["row_count"] == 0
    # Honest counts; the ratios are the real 0-of-N numbers (never a
    # fabricated verdict), and only a zero DENOMINATOR stays UNKNOWN.
    assert stages["marks"]["coverage_ratio"] == 0.0
    assert stages["outcomes"]["coverage_ratio"] == 0.0
    assert stages["settle"]["coverage_ratio"] == 0.0
    assert stages["marks"]["candidates_total"] == 2
    assert stages["marks"]["candidates_with_marks"] == 0
    assert stages["outcomes"]["markets_bound"] == 2
    assert stages["outcomes"]["markets_with_outcome"] == 0
    assert stages["settle"]["entries_total"] == 1
    assert stages["settle"]["entries_settled"] == 0

    assert stages["evaluate"]["status"] == report.STAGE_STATUS_OK
    assert stages["evaluate"]["queues"] == [{
        "model_key": "crypto", "market_type": "terminal",
        "gate_version": pipeline.GATE_VERSION}]

    # Paper boundary flags.
    assert assembly.document["paper_only"] is True
    assert assembly.document["report_only"] is True
    assert assembly.document["readonly"] is True


def test_empty_store_renders_all_stages_missing_without_synthesizing():
    assembly = report.assemble_report(report.ReportInputs(
        wired_commands=WIRED_AT_BASE,
        evaluation=evaluation.evaluate_queues(())))
    stages = assembly.document["stages"]
    # Wired commands without rows are NO_DATA; unwired stay STAGE_NOT_WIRED.
    assert stages["scan"]["status"] == report.STAGE_STATUS_NO_DATA
    assert stages["evaluate"]["status"] == report.STAGE_STATUS_NO_DATA
    assert stages["evaluate"]["queues"] == []
    for stage in ("marks", "outcomes", "settle"):
        assert stages[stage]["status"] == report.STAGE_STATUS_NOT_WIRED
    for stage in report.STAGE_ORDER:
        assert stages[stage]["row_count"] == 0
        assert stages[stage]["as_of"] is None
    # Zero denominators stay UNKNOWN -- never a synthesized 0.0 ratio.
    assert stages["marks"]["coverage_ratio"] is None
    assert stages["outcomes"]["coverage_ratio"] is None
    assert stages["settle"]["coverage_ratio"] is None
    assert assembly.document["as_of"] is None
    assert assembly.document["evaluation"]["queues"] == []


def test_wired_but_empty_b2_b3_stages_render_no_data_with_honest_zeros():
    wired = WIRED_AT_BASE | {report.CAPTURE_MARKS_COMMAND, report.SETTLE_COMMAND}
    assembly = report.assemble_report(_inputs(wired_commands=wired))
    stages = assembly.document["stages"]
    for stage in ("marks", "outcomes", "settle"):
        assert stages[stage]["status"] == report.STAGE_STATUS_NO_DATA
        assert stages[stage]["wired"] is True
        # Real 0-of-N ratios over honest counts (0 of 2 candidates, 0 of
        # 1 entries); the empty-store case above pins the 0-of-0 rule.
        assert stages["marks"]["coverage_ratio"] == 0.0
        assert stages["outcomes"]["coverage_ratio"] == 0.0
        assert stages["settle"]["coverage_ratio"] == 0.0


def test_stage_rows_win_over_wiredness_labels():
    # Rows exist for a stage whose command is not in the wired set (for
    # example rows written by the store tools before the CLI wiring):
    # the evidence is present, so the status is OK and the wired flag
    # stays false -- never "missing".
    coverage = report.PipelineCoverage(candidates_total=2,
                                       candidates_with_marks=2,
                                       mark_observations=5)
    assembly = report.assemble_report(_inputs(coverage=coverage))
    stage = assembly.document["stages"]["marks"]
    assert stage["status"] == report.STAGE_STATUS_OK
    assert stage["wired"] is False
    assert stage["row_count"] == 5
    assert stage["coverage_ratio"] == 1.0


# ---------------------------------------------------------------------------
# Determinism and the content-addressed identity (plan acceptance test 2)
# ---------------------------------------------------------------------------


def test_two_assemblies_from_identical_inputs_are_byte_identical():
    first = report.assemble_report(_inputs())
    second = report.assemble_report(_inputs())
    assert first.content == second.content
    assert first.content_sha256 == second.content_sha256
    assert first.report_key == second.report_key
    assert first.as_of == second.as_of
    # Canonical bytes: sorted keys, compact separators.
    text = first.content.decode("utf-8")
    assert text == json.dumps(first.document, sort_keys=True,
                              separators=(",", ":"), allow_nan=False)


def test_report_key_is_content_addressed_and_clock_free():
    base = report.assemble_report(_inputs())
    # Same content -> same key (the function takes no clock at all, so
    # the writing time structurally cannot enter the identity).
    assert report.report_operation_key("*", base.as_of,
                                       base.content_sha256) == base.report_key
    # New content (one more scan row) -> a new append-only identity.
    grown = report.assemble_report(_inputs(scan_runs=(_scan_run(1),
                                                      _scan_run(2,
                                                                finished_at=EVEN_LATER))))
    assert grown.content_sha256 != base.content_sha256
    assert grown.report_key != base.report_key
    assert grown.report_key.startswith("report|*|")
    assert base.report_key.endswith(base.content_sha256)
    # no-data stamp when nothing was ever observed.
    empty = report.assemble_report(report.ReportInputs(
        wired_commands=WIRED_AT_BASE, evaluation=evaluation.evaluate_queues(())))
    assert "no-data" in empty.report_key


def test_report_command_receipts_never_enter_the_content():
    # Rows of the report command itself must not change the document --
    # otherwise the writing clock would live inside the content digest.
    without = report.assemble_report(_inputs())
    extra = (_receipt(report.REPORT_COMMAND, "report|*|x|d1", "STARTED",
                      EVEN_LATER, run_event_id=9),
             _receipt(report.REPORT_COMMAND, "report|*|x|d1", "SUCCEEDED",
                      EVEN_LATER + datetime.timedelta(seconds=1),
                      run_event_id=10))
    with_rows = report.assemble_report(
        _inputs(command_run_events=_inputs().command_run_events + extra))
    assert with_rows.content == without.content
    assert with_rows.content_sha256 == without.content_sha256
    assert with_rows.report_key == without.report_key
    assert with_rows.document["receipts"]["self_receipts_excluded"] is True
    assert report.REPORT_COMMAND not in with_rows.document["receipts"][
        "counts_by_command"]


def test_late_arriving_evidence_mints_a_new_append_only_record():
    base = report.assemble_report(_inputs())
    later_rows = _inputs().command_run_events + (
        _receipt(report.CAPTURE_MARKS_COMMAND, "capture-marks|t2", "SUCCEEDED",
                 EVEN_LATER, run_event_id=11),)
    refreshed = report.assemble_report(_inputs(command_run_events=later_rows))
    assert refreshed.report_key != base.report_key
    # The old evidence is still there -- nothing was overwritten.
    assert refreshed.document["stages"]["marks"]["status"] == report.STAGE_STATUS_NOT_WIRED
    assert refreshed.document["receipts"]["counts_by_command"][
        report.CAPTURE_MARKS_COMMAND] == 1


# ---------------------------------------------------------------------------
# Receipts, latches and bankroll sections
# ---------------------------------------------------------------------------


def test_receipts_show_last_phase_per_operation_key():
    rows = (
        _receipt(scan.SCAN_COMMAND, "run-scan|pal-b1|t1|b1", "STARTED",
                 NOW - datetime.timedelta(minutes=5), run_event_id=1),
        _receipt(scan.SCAN_COMMAND, "run-scan|pal-b1|t1|b1", "FAILED",
                 NOW - datetime.timedelta(minutes=4), run_event_id=2),
        _receipt(scan.SCAN_COMMAND, "run-scan|pal-b1|t1|b1", "STARTED",
                 NOW - datetime.timedelta(minutes=3), attempt=2, run_event_id=3),
        _receipt(scan.SCAN_COMMAND, "run-scan|pal-b1|t1|b1", "SUCCEEDED",
                 NOW - datetime.timedelta(minutes=2), attempt=2, run_event_id=4),
        _receipt(scan.SCAN_COMMAND, "run-scan|pal-b1|t0|b1", "SUCCEEDED",
                 NOW - datetime.timedelta(days=1), run_event_id=5),
        _receipt("evaluate", "evaluate|cli", "SUCCEEDED",
                 NOW, run_event_id=6),
    )
    assembly = report.assemble_report(_inputs(command_run_events=rows))
    receipts = assembly.document["receipts"]
    assert receipts["counts_by_command"] == {"evaluate": 1, "run-scan": 5}
    by_operation = {(row["command"], row["operation_key"]): row
                    for row in receipts["last_phase_by_operation"]}
    retry = by_operation[(scan.SCAN_COMMAND, "run-scan|pal-b1|t1|b1")]
    assert retry["last_phase"] == "SUCCEEDED" and retry["receipt_count"] == 4
    assert retry["last_occurred_at"] == \
        (NOW - datetime.timedelta(minutes=2)).isoformat()
    first = by_operation[(scan.SCAN_COMMAND, "run-scan|pal-b1|t0|b1")]
    assert first["last_phase"] == "SUCCEEDED" and first["receipt_count"] == 1
    # Sorted by (command, operation_key).
    keys = [(row["command"], row["operation_key"])
            for row in receipts["last_phase_by_operation"]]
    assert keys == sorted(keys)


def test_queue_control_latch_folding_mirrors_the_store_reader():
    controls = (
        {"queue_id": "crypto/eth", "control_kind": "PAUSE",
         "rule": store.QUEUE_CONTROL_RULE_FX17, "observed_at": NOW,
         "control_event_id": 1},
        {"queue_id": "soccer/x", "control_kind": "PAUSE",
         "rule": store.QUEUE_CONTROL_RULE_BLUEPRINT, "observed_at": NOW,
         "control_event_id": 2},
        # A repeated pause between the resume folds idempotently.
        {"queue_id": "crypto/eth", "control_kind": "PAUSE",
         "rule": store.QUEUE_CONTROL_RULE_FX17,
         "observed_at": NOW + datetime.timedelta(minutes=5),
         "control_event_id": 3},
        {"queue_id": "crypto/eth", "control_kind": "RESUME",
         "rule": store.QUEUE_CONTROL_RULE_FX17,
         "observed_at": NOW + datetime.timedelta(minutes=10),
         "control_event_id": 4},
    )
    assembly = report.assemble_report(_inputs(queue_control_events=controls))
    section = assembly.document["queue_controls"]
    assert section["row_count"] == 4
    assert section["latch_by_queue"] == {
        "crypto/eth": {"pause_review": False, "auto_stop": False},
        "soccer/x": {"pause_review": False, "auto_stop": True},
    }
    assert section["last"]["control_kind"] == "RESUME"


def test_bankroll_section_reports_the_last_check():
    checks = (
        {"as_of": NOW - datetime.timedelta(days=1), "passed": True,
         "check_id": 1, "event_watermark": 10,
         "identity_difference": Decimal("0.001")},
        {"as_of": NOW, "passed": False, "check_id": 2,
         "event_watermark": 12, "identity_difference": Decimal("0.02")},
    )
    assembly = report.assemble_report(_inputs(bankroll_checks=checks))
    section = assembly.document["bankroll"]
    assert section["check_count"] == 2
    assert section["last_check"] == {"as_of": NOW.isoformat(), "passed": False,
                                     "event_watermark": 12,
                                     "identity_difference": 0.02}
    assert assembly.document["as_of"] == NOW.isoformat()


def test_as_of_is_data_derived_per_stage_and_overall():
    inputs = _inputs()
    assembly = report.assemble_report(inputs)
    assert assembly.as_of == NOW  # scan finished_at dominates
    assert assembly.document["stages"]["evaluate"]["as_of"] == \
        (NOW - datetime.timedelta(minutes=1)).isoformat()
    # ISO-string rows normalize to the same timestamps and the same bytes.
    string_rows = tuple(
        {**row, "occurred_at": row["occurred_at"].isoformat()}
        for row in inputs.command_run_events)
    from_strings = report.assemble_report(
        _inputs(command_run_events=string_rows))
    assert from_strings.content == assembly.content


def test_source_times_advance_only_their_stage_and_the_overall_maximum():
    clocks = {
        "marks": NOW + datetime.timedelta(minutes=5),
        "outcomes": NOW + datetime.timedelta(minutes=10),
        "settle": NOW + datetime.timedelta(minutes=20),
        "evaluate": NOW + datetime.timedelta(minutes=15),
    }
    assembly = report.assemble_report(_inputs(stage_evidence_times=clocks))
    assert {key: row["as_of"] for key, row in assembly.document["stages"].items()} == {
        "scan": NOW.isoformat(), **{key: value.isoformat() for key, value in clocks.items()}}
    assert assembly.as_of == clocks["settle"]
    early_observation = report.assemble_report(_inputs(stage_evidence_times={
        **clocks, "marks": NOW + datetime.timedelta(minutes=4)}))
    assert early_observation.as_of == assembly.as_of
    assert early_observation.content_sha256 != assembly.content_sha256
    assert early_observation.report_key != assembly.report_key
    with pytest.raises(report.ReportError, match="stage_evidence"):
        report.assemble_report(_inputs(stage_evidence_times={"settle": NOW.replace(tzinfo=None)}))


# ---------------------------------------------------------------------------
# Evaluation parity (single metric face)
# ---------------------------------------------------------------------------


def test_evaluation_block_matches_the_evaluate_command_payload_bit_for_bit():
    from polymarket_alpha_lab.papersim import cli

    queue_report = evaluation.evaluate_queues(
        (_settled_row(index) for index in range(3)))
    assembly = report.assemble_report(_inputs(evaluation=queue_report))
    expected = json.dumps(cli._jsonify(queue_report), sort_keys=True,
                          allow_nan=False)
    embedded = json.dumps(assembly.document["evaluation"], sort_keys=True,
                          allow_nan=False)
    assert embedded == expected
    assert report.report_json_value(queue_report) == cli._jsonify(queue_report)


def test_report_discloses_pending_recorded_pnl_by_queue_and_preserves_booked_zero():
    pending = replace(
        _settled_row(1), model_probability=0.60, market_mid=0.50, close_price=0.58,
        outcome=None, natural_outcome="YES", settled_time=None, recorded_pnl=None)
    booked_zero = replace(
        _settled_row(2), model_probability=0.60, market_mid=0.50, close_price=0.52,
        outcome="win", natural_outcome="YES", recorded_pnl=0.0)
    other_pending = replace(pending, candidate_id="a1", model_key="another-model")
    clean = replace(booked_zero, candidate_id="z1", model_key="clean")
    queue_report = evaluation.evaluate_queues((pending, booked_zero, other_pending, clean))
    crypto = ("crypto", "terminal", pipeline.GATE_VERSION)
    another = ("another-model", "terminal", pipeline.GATE_VERSION)
    counts = {crypto: 1, another: 1}
    coverage = report.PipelineCoverage(
        candidates_total=4, markets_bound=3, markets_with_outcome=3, outcome_observations=3,
        entries_total=4, entries_settled=2)
    fields = dict(evaluation=queue_report, coverage=coverage,
                  stage_evidence_times={"outcomes": LATER, "evaluate": LATER, "settle": NOW})
    assembly = report.assemble_report(_inputs(pending_recorded_by_queue=counts, **fields))
    stages = assembly.document["stages"]
    assert stages["evaluate"]["status"] == "PARTIAL"
    assert stages["evaluate"]["recorded_pnl_gap"] == {
        "reason": "settlement_not_recorded",
        "entries": 2,
        "by_queue": [
            {"model_key": model, "market_type": market, "gate_version": gate,
             "entries": 1, "recorded_pnl": None}
            for model, market, gate in (another, crypto)
        ],
    }
    assert stages["outcomes"]["coverage_ratio"] == 1.0
    assert stages["settle"]["coverage_ratio"] == 0.5
    assert (stages["outcomes"]["as_of"], stages["settle"]["as_of"],
            stages["evaluate"]["as_of"], assembly.as_of) == (
        LATER.isoformat(), NOW.isoformat(), LATER.isoformat(), LATER)
    assert assembly.document["evaluation"] == report.report_json_value(queue_report)
    queues = {row["queue"]["model_key"]: row for row in assembly.document["evaluation"]["queues"]}
    assert queues["crypto"]["counts"]["entered"] == 2
    assert queues["crypto"]["counts"]["unresolved"] == 1
    assert queues["crypto"]["scoring"]["markets_scored"] == 2
    assert queues["crypto"]["clv"]["entries_with_close"] == 2
    assert queues["crypto"]["roi"]["counted"] == 1
    assert queues["crypto"]["roi"]["gross_pnl"] == 0.0
    assert queues["another-model"]["roi"]["counted"] == 0
    assert queues["another-model"]["roi"]["status"] == "INSUFFICIENT"
    assert queues["another-model"]["roi"]["net_roi"] is None
    assert queues["clean"]["roi"]["counted"] == 1
    assert report.assemble_report(_inputs(
        pending_recorded_by_queue=dict(reversed(tuple(counts.items()))), **fields)
    ).content == assembly.content


@pytest.mark.parametrize("gap", [
    [],
    {("crypto", "terminal", pipeline.GATE_VERSION): 0},
    {("crypto", "terminal", pipeline.GATE_VERSION): True},
    {("another-model", "terminal", pipeline.GATE_VERSION): 1},
    {("crypto", "terminal", pipeline.GATE_VERSION): 2},
])
def test_report_refuses_invalid_recorded_pnl_gap_counts(gap):
    pending = replace(_settled_row(1), outcome=None, natural_outcome="NO",
                      settled_time=None, recorded_pnl=None)
    queue_report = evaluation.evaluate_queues((pending,))
    with pytest.raises(report.ReportError, match="^papersim_report_recorded_pnl_gap_invalid"):
        report.assemble_report(_inputs(evaluation=queue_report, pending_recorded_by_queue=gap))


def test_report_refuses_duplicate_evaluation_queues_when_disclosing_pending_pnl():
    pending = replace(_settled_row(1), outcome=None, natural_outcome="YES",
                      settled_time=None, recorded_pnl=None)
    evaluation_report = evaluation.evaluate_queues((pending,))
    duplicates = replace(evaluation_report, queues=evaluation_report.queues * 2)
    with pytest.raises(report.ReportError, match="^papersim_report_recorded_pnl_gap_invalid"):
        report.assemble_report(_inputs(
            evaluation=duplicates,
            pending_recorded_by_queue={("crypto", "terminal", pipeline.GATE_VERSION): 1}))


def test_empty_pending_pnl_metadata_keeps_existing_report_content():
    assembly = report.assemble_report(_inputs())
    assert assembly.document["stages"]["evaluate"]["status"] == "OK"
    assert "recorded_pnl_gap" not in assembly.document["stages"]["evaluate"]
    assert assembly.content_sha256 == "08134fabe719956374cb93132d6564de9b97c97aa91f2ad63df95a7e27daf081"


# ---------------------------------------------------------------------------
# Basketball full-game settlement: overtime-inclusive, all leagues
# ---------------------------------------------------------------------------


def test_basketball_ot_final_is_the_full_game_final_no_regulation_filtering():
    # An overtime game: regulation ended 110-110, the completed final is
    # 118-112 (regulation + OT) -- exactly the ESPN ``completed`` score.
    # Full-game moneyline/spread/total all settle on 118-112.
    assert report.basketball_full_game_outcome(
        market_kind="moneyline", side="home",
        home_score=118, away_score=112) == "win"
    assert report.basketball_full_game_outcome(
        market_kind="moneyline", side="away",
        home_score=118, away_score=112) == "loss"
    assert report.basketball_full_game_outcome(
        market_kind="spread", side="home", line=-5.5,
        home_score=118, away_score=112) == "win"
    assert report.basketball_full_game_outcome(
        market_kind="spread", side="away", line=+5.5,
        home_score=118, away_score=112) == "loss"
    assert report.basketball_full_game_outcome(
        market_kind="total", side="Over", line=229.5,
        home_score=118, away_score=112) == "win"
    assert report.basketball_full_game_outcome(
        market_kind="total", side="Under", line=229.5,
        home_score=118, away_score=112) == "loss"

    # The regulation-only presentation of the SAME game (110-110) would
    # leave the moneyline unresolved -- a tie has no winner.  The report
    # settles "win" above, so no regulation-only filtering happens
    # anywhere on the basketball path.
    assert report.basketball_full_game_outcome(
        market_kind="moneyline", side="home",
        home_score=110, away_score=110) is None

    # Soccer stays pinned to regulation plus stoppage in its own adapter;
    # the report module pins the same constant for the contrast.
    assert report.SOCCER_SETTLEMENT_PERIOD == soccer_totals._PERIOD
    assert report.SOCCER_SETTLEMENT_PERIOD == "regulation_90_plus_stoppage"


def test_basketball_display_row_shows_the_full_final_score():
    ot_game = report.BasketballMarketSettlement(
        market_ref="nba-bos-lal-20261120", league="nba",
        market_kind="spread", side="home", line=-5.5,
        home_score=118, away_score=112, completed=True)
    row = ot_game.display()
    assert row["final_score"] == {"home": 118, "away": 112}
    assert row["final_includes_overtime"] is True
    assert row["settlement"] == "win"
    assert row["unresolved_reason"] is None
    assert row["league"] == "nba"
    # The display lands in the settle stage deterministically.
    international = report.BasketballMarketSettlement(
        market_ref="acb-rmb-bcn-20261121", league="liga-acb",
        market_kind="total", side="Over", line=164.5,
        home_score=83, away_score=84, completed=True)
    assembly = report.assemble_report(_inputs(
        basketball_finals=(international, ot_game)))
    finals = assembly.document["stages"]["settle"]["basketball_finals"]
    assert [row["market_ref"] for row in finals] == [
        "acb-rmb-bcn-20261121", "nba-bos-lal-20261120"]  # sorted by league
    assert finals[1]["settlement"] == "win"  # 167 > 164.5


def test_basketball_rules_apply_across_leagues_identically():
    # The resolver is league-blind: the same scoreline settles the same
    # way for the NBA and for international leagues alike.
    for league in ("nba", "euroleague", "liga-acb", "bbl"):
        assert report.basketball_full_game_outcome(
            market_kind="moneyline", side="away",
            home_score=112, away_score=118) == "win"
        row = report.BasketballMarketSettlement(
            market_ref=f"{league}-a", league=league, market_kind="total",
            side="Under", line=230.5, home_score=118, away_score=112,
            completed=True)
        assert row.settlement() == "win"
        assert row.display()["final_includes_overtime"] is True


# Source-faithful reduced NBA fixtures. The IDs, tokens, results and time are
# synthetic. Separate opt-in native witnesses use the approved originals.
_FIXTURE_TIP = NOW - datetime.timedelta(hours=4)
_FIXTURE_CLOSE = _FIXTURE_TIP + datetime.timedelta(hours=3)
_FIXTURE_FETCH = _FIXTURE_CLOSE + datetime.timedelta(minutes=15)
_FIXTURE_FULL_GAME_RULE = (
    "The result will be determined based on the final score including any overtime periods."
)


def _nba_fixture():
    parent = {
        "id": "901", "gameId": 902, "slug": "nba-orl-gsw-2026-11-20", "seriesSlug": "nba-2026",
        "eventDate": "2026-11-20", "startTime": _FIXTURE_TIP.isoformat(), "title": "Magic vs. Warriors",
        "ended": True, "score": "112-118", "sport": {"sport": "nba", "ordering": "away"},
    }
    market = {
        "id": "903", "conditionId": "0x" + "a" * 64, "slug": parent["slug"],
        "sportsMarketType": "moneyline", "question": parent["title"],
        "description": '\n'.join((
            'If the Magic win, the market will resolve to "Magic".',
            'If the Warriors win, the market will resolve to "Warriors".',
            _FIXTURE_FULL_GAME_RULE,
        )),
        "gameStartTime": _FIXTURE_TIP.isoformat(), "outcomes": '["Magic","Warriors"]',
        "clobTokenIds": '["1000000001","1000000002"]', "outcomePrices": '["0","1"]',
        "resolutionSource": "https://www.nba.com/", "closed": True,
        "umaResolutionStatus": "resolved", "closedTime": _FIXTURE_CLOSE.isoformat(),
        "events": [parent],
    }
    competitors = [
        {"homeAway": "away", "score": "112", "team": {"abbreviation": "ORL", "id": "19"},
         "linescores": [{"displayValue": str(score)} for score in (27, 27, 28, 28, 2)]},
        {"homeAway": "home", "score": "118", "team": {"abbreviation": "GS", "id": "9"},
         "linescores": [{"displayValue": str(score)} for score in (30, 30, 25, 25, 8)]},
    ]
    summary = {
        "header": {"id": "904", "league": {"abbreviation": "NBA", "slug": "nba"},
                   "competitions": [{
                       "id": "904", "date": _FIXTURE_TIP.isoformat(),
                       "status": {"type": {"completed": True, "name": "STATUS_FINAL",
                                           "state": "post", "detail": "Final/OT"}},
                       "competitors": competitors,
                   }]},
        "boxscore": {},
    }
    return market, summary


def _nba_snapshot(body, kind, source, *, fetched_at=_FIXTURE_FETCH):
    if not isinstance(body, bytes):
        body = json.dumps(body, separators=(",", ":")).encode()
    return {"content_sha256": sha256(body).hexdigest(), "content": body, "byte_length": len(body),
            "kind": kind, "source": source, "fetched_at": fetched_at, "schema_version": 1}


def _nba_gamma_snapshot(market, *, shape="markets"):
    market = deepcopy(market)
    parent = market["events"][0]
    if shape == "markets":
        url = f"https://gamma-api.polymarket.com/markets?closed=true&slug={market['slug']}"
        return _nba_snapshot([market], "polymarket-gamma", url)
    if shape == "single_market":
        url = f"https://gamma-api.polymarket.com/markets/slug/{market['slug']}"
        return _nba_snapshot(market, "polymarket-gamma", url)
    del market["events"]
    event = {**parent, "markets": [market]}
    if shape == "single_event":
        return _nba_snapshot(event, "polymarket-gamma",
                             f"https://gamma-api.polymarket.com/events/{event['id']}")
    page = {"$schema": "https://gamma-api.polymarket.com/schemas/EventsPagination.json",
            "data": [event], "pagination": {"hasMore": False, "totalResults": 1}}
    return _nba_snapshot(page, "polymarket-gamma",
                         "https://gamma-api.polymarket.com/events/pagination?tag_slug=nba")


def _nba_score_snapshot(summary):
    event_id = summary["header"]["id"]
    return _nba_snapshot(
        summary, "espn-nba",
        f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={event_id}")


def _nba_candidate(market, snapshot_refs):
    opposing, chosen = json.loads(market["clobTokenIds"])
    return {
        "candidate_id": 1, "team": "basketball", "candidate_key": "b4-fixture",
        "condition_id": market["conditionId"], "market_id": market["id"],
        "market_slug": market["slug"], "side": "YES", "yes_token_id": chosen, "no_token_id": opposing,
        "event_key": f"gamma:{market['events'][0]['id']}", "event_start_at": _FIXTURE_TIP,
        "snapshot_refs": list(snapshot_refs),
    }


def _nba_report(candidate, rows, *, quotes=(), outcomes=()):
    evidence = report.verify_basketball_finals(
        candidates=(candidate,), quotes=quotes, outcomes=outcomes,
        snapshots={row["content_sha256"]: row for row in rows})
    return report.assemble_report(_inputs(basketball_evidence=evidence)).document["stages"]["settle"]


def test_basketball_snapshot_refs_are_candidate_scoped_and_bounded_before_byte_reads():
    market, _ = _nba_fixture()
    candidate = _nba_candidate(market, ["a" * 64])
    quote = {
        "candidate_id": 1, "evidence_role": "DECISION", "market_ref_kind": "market_id",
        "market_ref": market["id"], "side": "YES", "token_ref": candidate["yes_token_id"],
        "source_kind": "polymarket-gamma", "source_ref": "gamma-source",
        "snapshot_sha256": "b" * 64,
    }
    outcome = {
        "candidate_id": 1, "condition_id": market["conditionId"], "outcome": "YES",
        "source_snapshot_sha256": "c" * 64, "source_kind": "operator_attestation",
        "source_ref": "score-source",
    }
    metadata = {
        "a" * 64: {"content_sha256": "a" * 64, "kind": "polymarket-gamma",
                   "source": "candidate-source", "byte_length": 4,
                   "schema_version": 1, "fetched_at": NOW},
        "b" * 64: {"content_sha256": "b" * 64, "kind": "polymarket-gamma",
                   "source": "gamma-source", "byte_length": 5,
                   "schema_version": 1, "fetched_at": NOW},
        "c" * 64: {"content_sha256": "c" * 64, "kind": "espn-nba",
                   "source": "score-source", "byte_length": 7,
                   "schema_version": 1, "fetched_at": NOW},
    }
    refs = report.basketball_snapshot_references(
        candidates=(candidate,), quotes=(quote,), outcomes=(outcome,),
        snapshot_metadata=metadata)
    assert refs == {1: ("a" * 64, "b" * 64, "c" * 64)}
    assert report.bounded_basketball_snapshots(
        refs, metadata) == (("a" * 64, "b" * 64, "c" * 64), frozenset())
    selected, deferred = report.bounded_basketball_snapshots(
        refs, metadata, max_rows=2, max_bytes=100)
    assert selected == () and deferred == frozenset({1})
    selected, deferred = report.bounded_basketball_snapshots(
        refs, metadata, max_rows=10, max_bytes=10)
    assert selected == () and deferred == frozenset({1})
    bad_metadata = {**metadata, "b" * 64: {**metadata["b" * 64], "source": "wrong"}}
    assert report.basketball_snapshot_references(
        candidates=(candidate,), quotes=(quote,), outcomes=(outcome,),
        snapshot_metadata=bad_metadata) == {1: ("a" * 64, "c" * 64)}
    assert report.basketball_snapshot_references(
        candidates=(candidate,), quotes=({**quote, "candidate_id": 2},),
        outcomes=({**outcome, "condition_id": "different-market"},)
    ) == {1: ("a" * 64,)}


def test_incomplete_aggregate_read_cannot_present_a_verified_position():
    market, summary = _nba_fixture()
    rows = (_nba_gamma_snapshot(market), _nba_score_snapshot(summary))
    refs = [row["content_sha256"] for row in rows]
    evidence = report.verify_basketball_finals(
        candidates=(_nba_candidate(market, refs),), quotes=(), outcomes=(),
        snapshots=dict(zip(refs, rows, strict=True)), deferred_candidates=frozenset({1}))
    assert evidence.finals == ()
    assert evidence.unavailable[0].reason == "evidence_budget_exceeded"


@pytest.mark.parametrize("shape", ["markets", "single_market", "single_event", "event_page"])
def test_registered_gamma_resources_bind_the_ordered_token_to_an_independent_final(shape):
    market, summary = _nba_fixture()
    docs = (_nba_gamma_snapshot(market, shape=shape), _nba_score_snapshot(summary))
    candidate = _nba_candidate(market, (row["content_sha256"] for row in docs))
    settle = _nba_report(candidate, docs)
    final, = settle["basketball_finals"]
    assert settle["basketball_evidence"]["status"] == "OK"
    assert final["settlement"] == "win"
    assert final["side"] == "home"
    assert (final["home_team"], final["away_team"]) == ("GSW", "ORL")
    assert final["final_score"] == {"home": 118, "away": 112}
    assert final["regulation_score"] == {"home": 110, "away": 110} and final["overtime_periods"] == 1
    assert final["provenance"]["gamma_event_id"] == "901"
    assert final["provenance"]["gamma_game_id"] == "902"
    assert final["provenance"]["espn_event_id"] == "904"
    assert settle["as_of"] == _FIXTURE_FETCH.isoformat()


@pytest.mark.parametrize("change, reason", [
    ("period", "unsupported_period"), ("rules", "conflict"), ("condition", "conflict"),
    ("token", "conflict"), ("status", "not_completed"), ("incomplete", "incomplete_score"),
    ("other_tip", "ambiguous_match"), ("venue", "ambiguous_match"), ("repeat", "ambiguous_match"),
    ("score", "conflict"), ("bad_wrap", "missing_terms_or_reference"),
    ("digest", "missing_terms_or_reference"), ("cache_as_raw", "missing_terms_or_reference"),
])
def test_registered_finals_state_why_a_position_cannot_be_verified(change, reason):
    market, summary = _nba_fixture()
    competition = summary["header"]["competitions"][0]
    extra = []
    if change == "period":
        market["description"] = market["description"].replace(_FIXTURE_FULL_GAME_RULE, "")
    if change == "rules":
        market["description"] = market["description"].replace('to "Warriors"', 'to "Magic"')
    if change == "status":
        competition["status"]["type"]["completed"] = False
    if change == "incomplete":
        competition["competitors"][0].pop("score")
    if change == "other_tip":
        competition["date"] = NOW.isoformat()
    if change == "venue":
        competition["competitors"][0]["homeAway"] = "home"
        competition["competitors"][1]["homeAway"] = "away"
    if change == "repeat":
        duplicate = deepcopy(summary)
        duplicate["header"]["id"] = duplicate["header"]["competitions"][0]["id"] = "905"
        extra.append(_nba_score_snapshot(duplicate))
    if change == "score":
        market["events"][0]["score"] = "114-112"
    gamma, scoreboard = _nba_gamma_snapshot(market), _nba_score_snapshot(summary)
    if change == "cache_as_raw":
        cached = json.dumps(summary, indent=2, sort_keys=True, allow_nan=False).encode()
        scoreboard = _nba_snapshot(cached, "espn-nba", scoreboard["source"])
    if change == "bad_wrap":
        gamma = _nba_snapshot({"data": [market]}, "polymarket-gamma", gamma["source"])
    if change == "digest":
        gamma["content"] = gamma["content"][:-1] + b" "
    rows = [gamma, scoreboard, *extra]
    candidate = _nba_candidate(market, (row["content_sha256"] for row in rows))
    if change == "condition":
        candidate["condition_id"] = "0x" + "b" * 64
    if change == "token":
        candidate["yes_token_id"] = "1000009999"
    settle = _nba_report(candidate, rows)
    assert settle["basketball_finals"] == []
    assert settle["basketball_evidence"]["status"] == "UNAVAILABLE"
    assert settle["basketball_evidence"]["unavailable_positions"] == [
        {"candidate_id": 1, "market_ref": "903", "reason": reason}]
    assert settle["as_of"] is None


def test_quote_sources_and_outcomes_must_bind_the_same_candidate_and_market():
    market, summary = _nba_fixture()
    gamma, scoreboard = _nba_gamma_snapshot(market), _nba_score_snapshot(summary)
    candidate = _nba_candidate(market, [])
    quote = {
        "candidate_id": 1, "evidence_role": "DECISION", "market_ref_kind": "market_id",
        "market_ref": market["id"], "side": "YES", "token_ref": candidate["yes_token_id"],
        "source_kind": "polymarket-gamma", "source_ref": gamma["source"],
        "snapshot_sha256": gamma["content_sha256"], "observed_at": _FIXTURE_TIP,
    }
    outcome = {
        "candidate_id": 1, "condition_id": market["conditionId"], "outcome": "YES",
        "yes_token_id": candidate["yes_token_id"], "no_token_id": candidate["no_token_id"],
        "source_kind": "operator_attestation", "source_snapshot_sha256": scoreboard["content_sha256"],
        "confirmed_at": LATER, "observed_at": NOW,
    }
    pair = (gamma, scoreboard)
    settle = _nba_report(candidate, pair, quotes=(quote,), outcomes=(outcome,))
    assert settle["basketball_finals"][0]["settlement"] == "win"
    assert settle["as_of"] == LATER.isoformat()
    for changed in ({"candidate_id": 2}, {"token_ref": "1000009999"},
                    {"source_ref": "https://unrelated.invalid"}, {"market_ref": "909"}):
        settle = _nba_report(candidate, pair, quotes=({**quote, **changed},), outcomes=(outcome,))
        assert settle["basketball_finals"] == []
    plain_mark = _nba_snapshot([market], "capture_mark_pack", "b4-fixture")
    with_fallback = dict(quote, evidence_role="PERIODIC", source_kind="prepared",
                         snapshot_sha256=plain_mark["content_sha256"])
    settle = _nba_report(candidate, (plain_mark, scoreboard), quotes=(with_fallback,), outcomes=(outcome,))
    assert settle["basketball_evidence"]["status"] == "UNAVAILABLE"
    contradictory = {**outcome, "outcome": "NO"}
    settle = _nba_report(candidate, pair, quotes=(quote,), outcomes=(contradictory,))
    assert settle["basketball_evidence"]["unavailable_positions"][0]["reason"] == "conflict"
    wrong_family = {**outcome, "outcome": "UNKNOWN", "yes_token_id": "1000009999"}
    assert report.basketball_snapshot_references(
        candidates=(candidate,), quotes=(quote,), outcomes=(wrong_family,)
    ) == {1: (gamma["content_sha256"],)}
    settle = _nba_report(candidate, pair, quotes=(quote,), outcomes=(wrong_family,))
    assert settle["basketball_finals"] == []
    assert settle["basketball_evidence"]["unavailable_positions"][0]["reason"] == "missing_terms_or_reference"


@pytest.mark.parametrize("provider", ["gamma", "scoreboard"])
def test_alternate_verified_basketball_source_can_advance_or_conflict(provider):
    market, summary = _nba_fixture()
    gamma = _nba_gamma_snapshot(market)
    scoreboard = _nba_score_snapshot(summary)
    if provider == "gamma":
        equivalent = _nba_gamma_snapshot(market, shape="single_market")
        counterparts = (gamma, equivalent)
        independent = scoreboard
    else:
        equivalent = _nba_score_snapshot({**summary, "news": []})
        counterparts = (scoreboard, equivalent)
        independent = gamma
    for source in counterparts:
        pair = independent, source
        candidate = _nba_candidate(market, (row["content_sha256"] for row in pair))
        final, = _nba_report(candidate, pair)["basketball_finals"]
        assert final["provenance"][provider]["content_sha256"] == source["content_sha256"]
        assert final["settlement"] == "win" and final["overtime_periods"] == 1

    rows = [gamma, scoreboard, equivalent]
    candidate = _nba_candidate(market, (row["content_sha256"] for row in rows))
    baseline = _nba_report(candidate, rows)
    final, = baseline["basketball_finals"]
    display_digest = final["provenance"][provider]["content_sha256"]
    shown, = [row for row in counterparts if row["content_sha256"] == display_digest]
    alternate, = [row for row in counterparts if row["content_sha256"] != display_digest]
    observed = {
        "candidate_id": 1, "condition_id": market["conditionId"],
        "yes_token_id": candidate["yes_token_id"], "no_token_id": candidate["no_token_id"],
        "source_kind": alternate["kind"], "source_ref": alternate["source"],
        "source_snapshot_sha256": alternate["content_sha256"],
        "outcome": "YES", "observed_at": NOW, "confirmed_at": LATER,
    }
    inputs = _inputs()

    def assemble(candidate, rows, outcomes=(), *, observed_at=None):
        evidence = report.verify_basketball_finals(
            candidates=(candidate,), quotes=(), outcomes=outcomes,
            snapshots={row["content_sha256"]: row for row in rows})
        return report.assemble_report(replace(
            inputs, basketball_evidence=evidence, stage_evidence_times={"outcomes": observed_at}))

    agreement = assemble(candidate, rows, (observed,), observed_at=LATER)
    stage = agreement.document["stages"]["settle"]
    advanced, = stage["basketball_finals"]
    assert agreement.as_of == LATER
    assert stage["as_of"] == advanced["evidence_as_of"] == LATER.isoformat()
    assert advanced["provenance"] == final["provenance"]
    assert advanced["settlement"] == "win"
    no_observation = assemble(candidate, rows)
    assert agreement.report_key != no_observation.report_key
    unreferenced = _nba_score_snapshot({**summary, "leaders": []})
    assert assemble(candidate, [*rows, unreferenced]).content == no_observation.content
    earlier = dict(observed, confirmed_at=_FIXTURE_FETCH, observed_at=_FIXTURE_CLOSE)
    for refs in permutations(candidate["snapshot_refs"]):
        reordered = dict(candidate, snapshot_refs=list(refs))
        for snapshot_order in permutations(rows):
            for observation_order in permutations((observed, earlier)):
                assert assemble(
                    reordered, snapshot_order, observation_order, observed_at=LATER
                ).content == agreement.content

    for source in (shown, alternate):
        for instant in (NOW, LATER, EVEN_LATER):
            contradiction = dict(
                observed, outcome="NO", source_snapshot_sha256=source["content_sha256"],
                source_ref=source["source"], confirmed_at=instant)
            maximum = max(LATER, instant)
            for observation_order in permutations((observed, contradiction)):
                assembly = assemble(candidate, rows, observation_order, observed_at=maximum)
                settle = assembly.document["stages"]["settle"]
                assert settle["basketball_evidence"]["status"] == "UNAVAILABLE"
                assert settle["basketball_evidence"]["unavailable_positions"] == [
                    {"candidate_id": 1, "market_ref": market["id"], "reason": "conflict"}]
                assert settle["basketball_finals"] == [] and settle["as_of"] is None
                assert assembly.document["stages"]["outcomes"]["as_of"] == maximum.isoformat()
                assert assembly.as_of == maximum


def test_unmatched_basketball_evidence_cannot_advance_the_final_or_contradict_it():
    market, summary = _nba_fixture()
    gamma, scoreboard = _nba_gamma_snapshot(market), _nba_score_snapshot(summary)
    competitor = deepcopy(summary)
    # Keep a matching score process in progress and an unrelated completed
    # game decodable in the registry alongside the two proven sources.
    competition, = competitor["header"]["competitions"]
    competition["status"]["type"]["completed"] = False
    incomplete = _nba_score_snapshot(competitor)
    other = deepcopy(summary)
    other["header"]["id"] = "905"
    competition, = other["header"]["competitions"]
    competition["id"] = "905"
    other_tip = _FIXTURE_TIP - datetime.timedelta(days=1)
    competition["date"] = other_tip.isoformat()
    other_scoreboard = _nba_score_snapshot(other)
    parent, = market["events"]
    slug = f"nba-orl-gsw-{other_tip.date().isoformat()}"
    other_parent = dict(
        parent, id="906", gameId=907, eventDate=other_tip.date().isoformat(),
        startTime=other_tip.isoformat(), slug=slug)
    other_market = dict(
        market, id="904", conditionId="0x" + "b" * 64, slug=slug,
        gameStartTime=other_tip.isoformat(), events=[other_parent],
        clobTokenIds='["1000000041","1000000042"]')
    other_gamma = _nba_gamma_snapshot(other_market)
    rows = (gamma, scoreboard, incomplete, other_scoreboard, other_gamma)
    candidate = _nba_candidate(market, (row["content_sha256"] for row in rows))
    other_candidate = dict(_nba_candidate(other_market, (
        other_gamma["content_sha256"], other_scoreboard["content_sha256"])),
        candidate_id=2, event_start_at=other_tip)
    baseline = _nba_report(candidate, rows)
    contradiction = {
        "candidate_id": 1, "condition_id": market["conditionId"],
        "yes_token_id": candidate["yes_token_id"], "no_token_id": candidate["no_token_id"],
        "source_kind": scoreboard["kind"], "source_ref": scoreboard["source"],
        "source_snapshot_sha256": scoreboard["content_sha256"],
        "outcome": "NO", "observed_at": LATER, "confirmed_at": EVEN_LATER,
    }
    independent_rows = [
        dict(contradiction, source_kind=row["kind"], source_ref=row["source"],
             source_snapshot_sha256=row["content_sha256"])
        for row in (incomplete, other_scoreboard, other_gamma)
    ]
    independent_rows.extend(
        dict(contradiction, **change)
        for change in (
            {"candidate_id": 2},
            {"condition_id": other_market["conditionId"]},
            {"yes_token_id": "1000009998", "no_token_id": "1000009999"},
            {"source_ref": other_scoreboard["source"]},
            {"source_snapshot_sha256": "0" * 64},
            {"outcome": "UNKNOWN", "confirmed_at": None},
        ))
    assert _nba_report(candidate, rows, outcomes=independent_rows) == baseline
    for eligible in (independent_rows, [*independent_rows, contradiction]):
        report_evidence = report.verify_basketball_finals(
            candidates=(candidate, other_candidate), quotes=(), outcomes=eligible,
            snapshots={row["content_sha256"]: row for row in reversed(rows)})
        stage = report.assemble_report(
            _inputs(basketball_evidence=report_evidence)).document["stages"]["settle"]
        assert [(row["candidate_id"], row["settlement"])
                for row in stage["basketball_finals"]] == (
            [(1, "win"), (2, "win")] if eligible is independent_rows else [(2, "win")])
        assert stage["basketball_evidence"]["status"] == (
            "OK" if eligible is independent_rows else "PARTIAL")


def test_only_an_explicit_whole_number_push_can_preserve_an_unresolved_verified_score():
    market, summary = _nba_fixture()
    market.update(
        slug=market["slug"] + "-total-230", sportsMarketType="totals", question="Magic vs. Warriors: O/U 230",
        outcomes='["Over","Under"]', outcomePrices='["0.5","0.5"]', line=230,
        description='\n'.join((
            'If the combined final score is more than 230 points, the market will resolve to "Over".',
            'If the combined final score is less than 230 points, the market will resolve to "Under".',
            'If the combined final score is exactly 230 points, the market will resolve 50-50.',
            _FIXTURE_FULL_GAME_RULE,
        )))
    docs = (_nba_gamma_snapshot(market), _nba_score_snapshot(summary))
    candidate = _nba_candidate(market, (row["content_sha256"] for row in docs))
    final, = _nba_report(candidate, docs)["basketball_finals"]
    assert final["final_score"] == {"home": 118, "away": 112}
    assert final["settlement"] is None and final["unresolved_reason"] == "push_or_tie"
    another_gamma = _nba_gamma_snapshot(market, shape="single_market")
    push_rows = (*docs, another_gamma)
    push_candidate = dict(candidate, snapshot_refs=[row["content_sha256"] for row in push_rows])
    final, = _nba_report(push_candidate, push_rows)["basketball_finals"]
    shown = final["provenance"]["gamma"]["content_sha256"]
    alternate, = (row for row in (docs[0], another_gamma) if row["content_sha256"] != shown)
    recorded_push = {
        "candidate_id": candidate["candidate_id"], "condition_id": market["conditionId"],
        "yes_token_id": candidate["yes_token_id"], "no_token_id": candidate["no_token_id"],
        "source_kind": alternate["kind"], "source_ref": alternate["source"],
        "source_snapshot_sha256": alternate["content_sha256"],
        "outcome": "VOIDED", "observed_at": NOW, "confirmed_at": LATER,
    }
    settle = _nba_report(push_candidate, push_rows, outcomes=(recorded_push,))
    assert settle["basketball_finals"][0]["settlement"] is None
    assert settle["as_of"] == LATER.isoformat()
    falsely_won = dict(recorded_push, outcome="YES")
    settle = _nba_report(push_candidate, push_rows, outcomes=(falsely_won,))
    assert settle["basketball_finals"] == []
    assert settle["basketball_evidence"]["unavailable_positions"][0]["reason"] == "conflict"
    market.update(line=230.5, question="Magic vs. Warriors: O/U 230.5",
                  description=market["description"].replace("230", "230.5"))
    gamma = _nba_gamma_snapshot(market)
    candidate["snapshot_refs"][0] = gamma["content_sha256"]
    settle = _nba_report(candidate, (gamma, docs[1]))
    assert settle["basketball_finals"] == []
    assert settle["basketball_evidence"]["unavailable_positions"][0]["reason"] == "conflict"


def test_partial_or_incomplete_basketball_scores_stay_unresolved():
    incomplete = report.BasketballMarketSettlement(
        market_ref="nba-part", league="nba", market_kind="moneyline",
        side="home", home_score=118, completed=True)
    assert incomplete.settlement() is None
    assert incomplete.unresolved_reason() == "incomplete_score"
    assert incomplete.display()["final_score"] is None

    unplayed = report.BasketballMarketSettlement(
        market_ref="nba-live", league="nba", market_kind="spread",
        side="home", line=-5.5, home_score=110, away_score=110,
        completed=False)
    assert unplayed.settlement() is None
    assert unplayed.unresolved_reason() == "not_completed"

    assert report.basketball_full_game_outcome(
        market_kind="moneyline", side="home", home_score=None,
        away_score=112) is None

    # Whole-number push (impossible on real half-point books): unresolved.
    push = report.BasketballMarketSettlement(
        market_ref="nba-push", league="nba", market_kind="spread",
        side="home", line=-6, home_score=118, away_score=112, completed=True)
    assert push.settlement() is None
    assert push.unresolved_reason() == "push_or_tie"
    assert report.basketball_full_game_outcome(
        market_kind="total", side="Over", line=230,
        home_score=118, away_score=112) is None


def test_basketball_resolver_rejects_unknown_shapes():
    with pytest.raises(report.ReportError) as kind_error:
        report.basketball_full_game_outcome(market_kind="first-half",
                                            side="home", home_score=1,
                                            away_score=2)
    assert kind_error.value.code == "papersim_report_basketball_kind_invalid"
    with pytest.raises(report.ReportError) as side_error:
        report.basketball_full_game_outcome(market_kind="moneyline",
                                            side="YES", home_score=1,
                                            away_score=2)
    assert side_error.value.code == "papersim_report_basketball_side_invalid"
    with pytest.raises(report.ReportError) as line_error:
        report.basketball_full_game_outcome(market_kind="spread", side="home",
                                            home_score=1, away_score=2)
    assert line_error.value.code == "papersim_report_basketball_line_invalid"
    with pytest.raises(report.ReportError) as score_error:
        report.basketball_full_game_outcome(market_kind="total", side="Over",
                                            line=100.0, home_score=-1,
                                            away_score=2)
    assert score_error.value.code == "papersim_report_basketball_score_invalid"


# ---------------------------------------------------------------------------
# Validation (honest refusals, never silent skips)
# ---------------------------------------------------------------------------


def test_inputs_validation_rejects_bad_rows_and_coverage():
    with pytest.raises(report.ReportError) as missing_evaluation:
        report.assemble_report(report.ReportInputs(
            wired_commands=WIRED_AT_BASE, evaluation=None))
    assert missing_evaluation.value.code == "papersim_report_evaluation_missing"

    with pytest.raises(report.ReportError) as bad_coverage:
        report.assemble_report(_inputs(
            coverage=report.PipelineCoverage(candidates_total=1,
                                             candidates_with_marks=2)))
    assert bad_coverage.value.code == "papersim_report_coverage_invalid"

    with pytest.raises(report.ReportError) as bad_phase:
        report.assemble_report(_inputs(command_run_events=(
            _receipt(scan.SCAN_COMMAND, "op", "LATER", NOW),)))
    assert bad_phase.value.code == "papersim_report_row_invalid"

    with pytest.raises(report.ReportError) as bad_row:
        report.assemble_report(_inputs(scan_runs=(
            {"scan_id": "one", "team": "t",
             "started_at": NOW, "finished_at": NOW},)))
    assert bad_row.value.code == "papersim_report_row_invalid"

    with pytest.raises(report.ReportError) as naive_time:
        report.assemble_report(_inputs(bankroll_checks=(
            {"as_of": datetime.datetime(2026, 11, 20), "passed": True},)))
    assert naive_time.value.code == "papersim_report_row_invalid"

    for scope in ("", " a ", "with|pipe"):
        with pytest.raises(report.ReportError) as scope_error:
            report.assemble_report(_inputs(scope=scope))
        assert scope_error.value.code == "papersim_report_scope_invalid"

    with pytest.raises(report.ReportError) as wired_error:
        report.assemble_report(_inputs(wired_commands=frozenset({"",})))
    assert wired_error.value.code == "papersim_report_wired_commands_invalid"


# ---------------------------------------------------------------------------
# Evidence writer builders
# ---------------------------------------------------------------------------


def test_report_receipt_and_registration_builders_pin_the_contract():
    receipt = report.report_receipt(
        operation_key="report|*|no-data|" + "0" * 64,
        phase="SUCCEEDED", occurred_at=NOW, code_commit="b" * 40,
        output_evidence=("0" * 64,))
    receipt.validate()
    assert receipt.command == report.REPORT_COMMAND
    assert receipt.output_evidence == ["0" * 64]
    assert receipt.key() == store.command_run_event_key(
        "report|*|no-data|" + "0" * 64, 1, "SUCCEEDED")

    content = report.canonical_report_json({"as_of": None, "scope": "*"})
    registration = report.report_snapshot_registration(content,
                                                        fetched_at=NOW)
    registration.validate()
    assert registration.kind == report.REPORT_SNAPSHOT_KIND
    assert registration.content == content
    from hashlib import sha256
    assert registration.content_sha256 == sha256(content).hexdigest()

    with pytest.raises(report.ReportError) as content_error:
        report.report_snapshot_registration("not-bytes", fetched_at=NOW)
    assert content_error.value.code == "papersim_report_content_bytes_required"


def test_assembly_registers_as_one_snapshot_worth_of_bytes():
    assembly = report.assemble_report(_inputs())
    registration = report.report_snapshot_registration(assembly.content,
                                                       fetched_at=EVEN_LATER)
    # The digest covers the content only: a different fetch time cannot
    # change the identity (no wall clock inside the content digest).
    later_registration = report.report_snapshot_registration(
        assembly.content, fetched_at=EVEN_LATER + datetime.timedelta(days=1))
    assert registration.content_sha256 == later_registration.content_sha256 \
        == assembly.content_sha256


# ---------------------------------------------------------------------------
# Module hygiene
# ---------------------------------------------------------------------------


def test_report_module_imports_neither_the_cli_nor_the_driver():
    script = (
        "import sys\n"
        "from polymarket_alpha_lab.papersim import report\n"
        "assert 'polymarket_alpha_lab.papersim.cli' not in sys.modules,"
        " 'report pulled cli'\n"
        "assert 'polymarket_alpha_lab.papersim.store_psycopg' not in sys.modules,"
        " 'report pulled the driver adapter'\n"
        "assert 'psycopg' not in sys.modules, 'report pulled the driver'\n"
        "print('ok')\n"
    )
    completed = subprocess.run([sys.executable, "-c", script],
                               capture_output=True, text=True, check=True)
    assert completed.stdout.strip().endswith("ok")


def test_stage_command_names_pin_the_registered_constants():
    assert report.STAGE_COMMANDS["scan"] == scan.SCAN_COMMAND
    assert report.REPORT_COMMAND == "report"
    assert report.CAPTURE_MARKS_COMMAND == "capture-marks"
    assert report.SETTLE_COMMAND == "settle"
    assert report.REPORT_SNAPSHOT_KIND == "papersim-report"
    assert snapshots_module.SNAPSHOT_DEFAULT_SCHEMA_VERSION >= 1
