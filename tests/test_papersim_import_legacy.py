"""FX-09L tests: the legacy history importer (offline dry-run contracts).

Everything here is offline and read-only: the importer's dry run is a pure
function of the archived repo snapshot
(``docs/paper-sim/ledger-snapshots/2026-11-12``), so the design section 7/
section 8.3 acceptance numbers (allocation, conservation, net identity,
label semantics, idempotent double runs and the authorization gate) are
pinned without any database. The DB-side proofs (real insert, idempotent
re-run, CHECK rejection, managed-face conservation) live in
``tests/test_papersim_store_native.py`` on the disposable native cluster.
"""

from dataclasses import replace
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal
import importlib.util
import json
from pathlib import Path
import re
import sys

import pytest

from polymarket_alpha_lab.papersim import fees

ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "papersim_import_legacy.py"
SNAPSHOT_DIR = ROOT / "docs" / "paper-sim" / "ledger-snapshots" / "2026-11-12"
CENT = Decimal("0.01")


@pytest.fixture(scope="module")
def importer():
    spec = importlib.util.spec_from_file_location(
        "papersim_import_legacy_under_test", TOOL_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass annotation resolution
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def plan(importer):
    return importer.build_import_plan(importer.load_snapshot(SNAPSHOT_DIR))


@pytest.fixture(scope="module")
def report(importer, plan):
    return importer.render_report(plan)


# ---------------------------------------------------------------------------
# Deterministic dry run: double build is byte-identical (8.3 item 3)
# ---------------------------------------------------------------------------


def test_dry_run_double_build_is_byte_identical(importer):
    first = importer.load_snapshot(SNAPSHOT_DIR)
    second = importer.load_snapshot(SNAPSHOT_DIR)
    plan_a = importer.build_import_plan(first)
    plan_b = importer.build_import_plan(second)
    assert importer.render_report(plan_a) == importer.render_report(plan_b)
    assert plan_a.scan_key == plan_b.scan_key
    assert plan_a.payload_sha256 == plan_b.payload_sha256
    assert plan_a.table_digests() == plan_b.table_digests()
    assert plan_a.content_sha256 == plan_b.content_sha256


def test_report_announces_dry_run_and_source_rows(report):
    assert report.startswith("papersim legacy import - DRY RUN")
    assert f"source: repo-snapshot:{SNAPSHOT_DIR.as_posix()}" in report
    assert "rows: decisions=71 resolutions=65 bankroll=68" in report
    assert "authorization: production insertion requires --execute-production" in report


def test_scan_identity_is_the_pinned_legacy_synthesis(importer, plan):
    assert plan.scan.operator == "legacy_import"
    assert plan.scan.team == "legacy_mixed"
    assert plan.scan.is_legacy is True
    assert plan.scan.code_commit is None  # CHECK allows NULL on legacy rows
    assert plan.scan.gate_version == "legacy-import-v1"
    params = json.loads(plan.scan.params)
    assert params["marker"] == "legacy:decisions:71"
    assert params["rows"] == {"decisions": 71, "resolutions": 65, "bankroll": 68}
    assert re.fullmatch(r"[a-f0-9]{64}", plan.scan_key)
    # Deterministic from data: started/finished span the decision rows.
    assert plan.scan.started_at == min(row["ts"] for row in
                                       importer.load_snapshot(SNAPSHOT_DIR).decisions)


def test_candidates_and_entries_preserve_the_book(importer, plan):
    candidates = plan.submission.candidates
    assert len(candidates) == 71
    assert all(candidate.conclusion == "ACCEPTED" for candidate in candidates)
    assert all(candidate.is_legacy and candidate.legacy_source_id
               for candidate in candidates)
    assert all(candidate.market_id and not candidate.condition_id
               for candidate in candidates)
    sides = {"YES": 57, "NO": 12, "NO-basket": 1, "YES-basket": 1}
    counted = {side: 0 for side in sides}
    for candidate in candidates:
        counted[candidate.side] += 1
    assert counted == sides
    for candidate in candidates:
        entry = candidate.entry
        assert entry.fee_source == "estimated"
        assert entry.is_legacy is True
        assert entry.legacy_source_id == candidate.legacy_source_id
        # Reconstructed shares keep the managed CHECK satisfiable with the
        # recorded stake/price; the recorded shares survive in the payload.
        assert abs(entry.shares * entry.price - entry.stake) < Decimal("0.01")
        payload = json.loads(candidate.input_payload)
        assert set(payload) == {
            "id", "ts", "market_id", "question", "side", "price", "shares",
            "stake", "fair_low", "fair_high", "spot", "vol", "horizon_days",
            "sizing", "reason", "team", "model_prediction", "market_type",
        }
        assert payload["team"] == candidate.team
        assert Decimal(payload["stake"]) == entry.stake
        assert Decimal(payload["price"]) == entry.price
    by_id = {int(c.legacy_source_id): c for c in candidates}
    assert by_id[1].entry.shares == Decimal("750") / Decimal("0.515")
    assert json.loads(by_id[1].input_payload)["shares"] == "1456"
    assert json.loads(by_id[71].input_payload)["shares"] == "333.33"


def test_side_mapping_table_is_explicit_and_unmapped_fails(importer):
    # D-5: the observed vocabulary maps one-to-one; anything else fails
    # loudly instead of being rewritten to a nearest canonical value.
    assert dict(importer.SIDE_MAP) == {
        "YES": "YES", "NO": "NO",
        "YES-basket": "YES-basket", "NO-basket": "NO-basket",
    }
    for raw in ("Over", "Under", "yes", ""):
        with pytest.raises(importer.PapersimImportError,
                           match="papersim_import_side_unmapped"):
            importer.map_side(raw)


def test_market_kind_touch_rule(importer):
    assert importer.map_market_kind("crypto_touch") == "touch"
    assert importer.map_market_kind("commodity_touch") == "touch"
    assert importer.map_market_kind("ou_over") == "terminal"
    assert importer.map_market_kind(None) == "terminal"


# ---------------------------------------------------------------------------
# Allocation and conservation (8.3 item 4; design 7.4)
# ---------------------------------------------------------------------------


def test_market_level_proceeds_allocated_by_recorded_shares_once(plan):
    rows = {row.legacy_decision_id: row for row in plan.settlements
            if row.market_id == "3866000"}
    assert set(rows) == {1, 14}
    assert rows[1].proceeds == Decimal("1456.11")
    assert rows[14].proceeds == Decimal("1099.09")
    assert rows[1].pnl_gross == Decimal("706.11")  # stake=750.00
    assert rows[14].pnl_gross == Decimal("599.09")  # stake=500.00
    assert rows[1].pnl_gross + rows[14].pnl_gross == Decimal("1305.20")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("earlier", "precedes_entry"),
        ("partial_evidence", "evidence_conflict"),
        ("replaced_void", "evidence_conflict"),
        ("opposing_side", "direction_conflict"),
        ("partial_payout", "amounts_unverified"),
        ("swapped_shares", "amounts_unverified"),
        ("position_exit", "exit_attribution_unverified"),
    ],
)
def test_market_row_must_cover_all_decisions_before_import(importer, change, reason):
    snapshot = importer.load_snapshot(SNAPSHOT_DIR)
    market_id = "3866000"
    resolution = next(row for row in snapshot.resolutions if row["market_id"] == market_id)
    decisions = snapshot.decisions
    if change == "earlier":
        decisions = tuple(
            dict(row, ts=resolution["ts"] + timedelta(seconds=1))
            if row["id"] == 14 else row for row in decisions
        )
    elif change == "partial_evidence":
        resolution = dict(resolution, evidence="Settles decision #14.")
    elif change == "replaced_void":
        resolution = dict(resolution, evidence="#14 re-entry. #1 was previously voided.")
    elif change == "opposing_side":
        decisions = tuple(
            dict(row, side="NO") if row["id"] == 14 else row for row in decisions
        )
    elif change == "partial_payout":
        resolution = dict(resolution, pnl=Decimal("598.90"), payoff=Decimal("1098.90"))
    elif change == "swapped_shares":
        swapped = {1: Decimal("1099"), 14: Decimal("1456")}
        decisions = tuple(
            dict(row, shares=swapped[row["id"]]) if row["id"] in swapped else row
            for row in decisions
        )
    elif change == "position_exit":
        resolution = dict(resolution, outcome="EXITED")
    resolutions = tuple(
        resolution if row["market_id"] == market_id else row for row in snapshot.resolutions
    )
    with pytest.raises(importer.PapersimImportError) as error:
        importer.build_import_plan(replace(snapshot, decisions=decisions, resolutions=resolutions))
    message = str(error.value)
    assert f"papersim_legacy_resolution_{reason}" in message
    assert "market_id='3866000' decision_ids=['1', '14']" in message


def test_pooled_position_label_can_use_unit_payoff(importer):
    snapshot = importer.load_snapshot(SNAPSHOT_DIR)
    resolutions = tuple(
        dict(row, outcome="win", payoff=Decimal("1"))
        if row["market_id"] == "3866000" else row for row in snapshot.resolutions
    )
    plan = importer.build_import_plan(replace(snapshot, resolutions=resolutions))
    settled = [row for row in plan.settlements if row.market_id == "3866000"]
    assert [(row.win_loss, row.pnl_gross) for row in settled] == [
        ("win", Decimal("706.11")), ("win", Decimal("599.09")),
    ]
    market = next(row for row in plan.outcomes if row.market_id == "3866000")
    assert market.outcome == "UNKNOWN"


def test_settlement_pnl_gross_conserves_resolutions_exactly(
    importer, plan
):
    snapshot = importer.load_snapshot(SNAPSHOT_DIR)
    legacy_total = sum((row["pnl"] for row in snapshot.resolutions),
                       Decimal("0.00"))
    imported_total = sum((row.pnl_gross for row in plan.settlements),
                         Decimal("0.00"))
    assert imported_total == legacy_total == Decimal("3768.33")
    # Per-market conservation: every resolved, allocatable market keeps its
    # recorded market-level pnl exactly.
    per_market: dict[str, Decimal] = {}
    for row in plan.settlements:
        per_market[row.market_id] = per_market.get(
            row.market_id, Decimal("0.00")) + row.pnl_gross
    for resolution in snapshot.resolutions:
        if resolution["market_id"] in per_market:
            assert per_market[resolution["market_id"]] == resolution["pnl"]


def test_counts_and_open_positions(plan, report):
    assert len(plan.settlements) == 66
    classes = {"NATURAL": 0, "EXIT": 0, "VOIDED_REFUND": 0}
    for row in plan.settlements:
        classes[row.classification] += 1
    assert classes == {"NATURAL": 57, "EXIT": 5, "VOIDED_REFUND": 4}
    assert len(plan.outcomes) == 60
    assert "market_outcomes: 60 (YES=27 NO=19 YES-basket=1 VOIDED=4 " \
           "UNKNOWN(position labels)=9); EXITED labels without a market " \
           "row: 5" in report
    assert "settlements: 66 (NATURAL=57 EXIT=5 VOIDED_REFUND=4) " \
           "open-entries (no resolution): 5" in report


# ---------------------------------------------------------------------------
# Net-of-fees identity with schedule-sourced rates (8.3 item 5)
# ---------------------------------------------------------------------------


def test_net_identity_and_estimated_fee_layers(plan, report):
    entry = sum((row.fee_entry for row in plan.settlements), Decimal("0.00"))
    exit_leg = sum((row.fee_exit for row in plan.settlements), Decimal("0.00"))
    total = sum((row.fee_total for row in plan.settlements), Decimal("0.00"))
    gross = sum((row.pnl_gross for row in plan.settlements), Decimal("0.00"))
    net = sum((row.pnl_net for row in plan.settlements), Decimal("0.00"))
    assert total == entry + exit_leg == Decimal("441.01")
    assert gross == Decimal("3768.33")
    assert net == Decimal("3327.32")
    assert gross - total == net
    candidates = {int(c.legacy_source_id): c for c in plan.submission.candidates}
    clamped = 0
    for row in plan.settlements:
        assert row.pnl_net == row.pnl_gross - row.fee_total
        stake = candidates[row.legacy_decision_id].entry.stake
        raw = row.pnl_gross + stake
        if raw < 0:
            clamped += 1
            assert row.proceeds == Decimal("0.00")  # a loss pays zero
        else:
            assert row.proceeds == raw
    # Exactly one legacy row drifts below zero (id 15: recorded market pnl
    # -500.00 on stake 499.80 from integer-share cent drift).
    assert clamped == 1
    assert f"fees: entry={entry} exit={exit_leg} total={total} " \
           "source=estimated schedule-as-of=" in report
    assert f"conservation: settlements pnl_gross sum {gross} fee sum " \
           f"{total} net sum {net}" in report
    assert "net identity: pnl_gross - fee_total == pnl_net: OK" in report


def test_fees_come_from_the_schedule_not_hardcoded_numbers(importer, plan):
    source = TOOL_PATH.read_text(encoding="utf-8")
    # No rate numbers live in the importer; rates flow through the fees
    # module (team classification -> taker_fee_rate).
    assert not re.search(r"\b0\.0[57]\b", source)
    rows = {row.legacy_decision_id: row for row in plan.settlements}
    rate = Decimal(str(fees.taker_fee_rate(
        fees.legacy_scan_fee_category("crypto_eth", "crypto_touch"))))
    assert rate == Decimal("0.07")
    expected = (Decimal("750") * rate * (Decimal("1") - Decimal("0.515"))) \
        .quantize(CENT, rounding=ROUND_HALF_EVEN)
    assert rows[1].fee_entry == expected == Decimal("25.46")


# ---------------------------------------------------------------------------
# Label semantics: natural vs win/loss vs EXITED vs VOIDED (8.3 item 6)
# ---------------------------------------------------------------------------


def _plan_maps(plan):
    outcomes = {observation.market_id: observation
                for observation in plan.outcomes}
    settlements = {}
    for row in plan.settlements:
        settlements.setdefault(row.market_id, []).append(row)
    return outcomes, settlements


def test_win_loss_labels_become_unknown_market_rows(plan):
    outcomes, settlements = _plan_maps(plan)
    labeled = [market for market, observation in outcomes.items()
               if observation.outcome == "UNKNOWN"]
    assert len(labeled) == 9
    for market in labeled:
        observation = outcomes[market]
        assert observation.outcome_detail in {
            "recorded_label=win", "recorded_label=loss"}
        assert observation.confirmed_at is None  # UNKNOWN is never confirmed
        label = observation.outcome_detail.partition("=")[2]
        for row in settlements[market]:
            assert row.classification == "NATURAL"
            assert row.win_loss == label  # the position label survives
            assert row.outcome_label == label


def test_exited_has_no_market_row_and_reconstructs_exit_price(plan):
    outcomes, settlements = _plan_maps(plan)
    exited = [market for market, rows in settlements.items()
              if rows[0].classification == "EXIT"]
    assert len(exited) == 5
    candidates = {int(c.legacy_source_id): c
                  for c in plan.submission.candidates}
    for market in exited:
        assert market not in outcomes  # no market assertion row at all
        for row in settlements[market]:
            assert row.win_loss is None
            assert row.exit_price is not None
            entry = candidates[row.legacy_decision_id].entry
            assert row.exit_price == (row.pnl_gross + entry.stake) / entry.shares
            assert Decimal(0) <= row.exit_price <= Decimal(1)
            assert row.fee_exit > 0  # sell leg charged at reconstructed price


def test_voided_refunds_stake_with_zero_fees(plan):
    outcomes, settlements = _plan_maps(plan)
    voided = [market for market, observation in outcomes.items()
              if observation.outcome == "VOIDED"]
    assert len(voided) == 4
    candidates = {int(c.legacy_source_id): c
                  for c in plan.submission.candidates}
    for market in voided:
        for row in settlements[market]:
            entry = candidates[row.legacy_decision_id].entry
            assert row.classification == "VOIDED_REFUND"
            assert row.proceeds == entry.stake
            assert row.pnl_gross == row.pnl_net == row.fee_total == \
                row.fee_entry == row.fee_exit == Decimal("0.00")
            assert row.win_loss is None


def test_natural_outcomes_align_win_loss_by_side(plan):
    outcomes, settlements = _plan_maps(plan)
    candidates = {int(c.legacy_source_id): c
                  for c in plan.submission.candidates}
    natural = [market for market, observation in outcomes.items()
               if observation.outcome in ("YES", "NO", "YES-basket")]
    assert len(natural) == 47
    for market in natural:
        observation = outcomes[market]
        assert observation.confirmed_at == observation.observed_at
        assert observation.outcome_detail is None
        for row in settlements[market]:
            assert row.classification == "NATURAL"
            side = candidates[row.legacy_decision_id].side
            expected = "win" if (
                side in ("YES", "YES-basket") and observation.outcome == side
                or side in ("NO", "NO-basket")
                and observation.outcome == "NO") else "loss"
            assert row.win_loss == expected
            assert row.outcome_label == observation.outcome


# ---------------------------------------------------------------------------
# INSUFFICIENT markets are never zero-filled (design 7.4)
# ---------------------------------------------------------------------------


def _write_minimal_snapshot(directory: Path, decisions: str,
                            resolutions: str, bankroll: str) -> Path:
    header = ("id,ts,market_id,question,side,price,shares,stake,fair_low,"
              "fair_high,spot,vol,horizon_days,sizing,reason,team,"
              "model_prediction,market_type\n")
    (directory / "decisions.csv").write_text(
        header + decisions, encoding="utf-8")
    (directory / "resolutions.csv").write_text(
        "market_id,ts,outcome,payoff,pnl,evidence\n" + resolutions,
        encoding="utf-8")
    (directory / "bankroll.csv").write_text(
        "ts,cash,deployed,note\n" + bankroll, encoding="utf-8")
    return directory


@pytest.mark.parametrize("evidence", [
    "Combined #1 and #2",
    "Decision #102 was not voided.",
])
def test_same_share_count_keeps_different_entry_costs(evidence, importer, tmp_path):
    snapshot_dir = _write_minimal_snapshot(
        tmp_path,
        decisions=(
            "1,2026-10-10 13:00:00+00,m-cost,q1,YES,0.2,100,20.00,,,,,,,,crypto_eth,0.6,crypto_daily\n"
            "2,2026-10-10 14:00:00+00,m-cost,q1,YES,0.8,100,80.00,,,,,,,,crypto_eth,0.6,crypto_daily\n"
        ),
        resolutions=f"m-cost,2026-10-20 00:00:00+00,YES,200.00,100.00,{evidence}\n",
        bankroll="2026-10-10 12:00:00+00,1000.00,0.00,initial\n",
    )
    plan = importer.build_import_plan(importer.load_snapshot(snapshot_dir))
    assert [(row.proceeds, row.pnl_gross) for row in plan.settlements] == [
        (Decimal("100.00"), Decimal("80.00")),
        (Decimal("100.00"), Decimal("20.00")),
    ]


def test_invalid_shares_markets_are_reported_insufficient(importer, tmp_path):
    directory = _write_minimal_snapshot(
        tmp_path,
        decisions=(
            "1,2026-10-10 13:39:29.867432+00,m-ok,q1,YES,0.5,100,50.00,,,,"
            ",,,,crypto_eth,0.6,crypto_daily\n"
            "2,2026-10-10 14:00:00+00,m-zero,q2,YES,0.5,0,10.00,,,,,,,"
            ",sports_soccer,0.5,match_winner\n"
            "3,2026-10-10 15:00:00+00,m-null,q3,YES,0.5,,10.00,,,,,,,"
            ",sports_soccer,0.5,match_winner\n"
        ),
        resolutions=(
            "m-ok,2026-10-20 00:00:00+00,YES,200.00,150.00,e1\n"
            "m-zero,2026-10-20 00:00:00+00,YES,20.00,10.00,e2\n"
            "m-null,2026-10-20 00:00:00+00,NO,0.00,-10.00,e3\n"
        ),
        bankroll="2026-10-10 13:39:29+00,9950.00,50.00,round-1\n",
    )
    plan = importer.build_import_plan(importer.load_snapshot(directory))
    assert plan.insufficient_markets == ("m-null", "m-zero")
    assert [row.market_id for row in plan.settlements] == ["m-ok"]
    assert plan.settlements[0].pnl_gross == Decimal("150.00")
    report = importer.render_report(plan)
    assert "insufficient markets (no allocation, never zero-filled): 2" \
           " m-null,m-zero" in report


# ---------------------------------------------------------------------------
# Bankroll reconciliation checkpoints (8.3 item 7)
# ---------------------------------------------------------------------------


def test_bankroll_reconciliation_lists_every_row_explicitly(plan, report):
    assert len(plan.reconciliation) == 68
    assert "bankroll reconciliation: 68 snapshot rows" in report
    statuses = [row["status"] for row in plan.reconciliation]
    assert "diff" in statuses and "match" in statuses
    first = plan.reconciliation[0]
    # The INITIAL anchor is the first row's balance (cash + deployed):
    # 10000.00 at t0. Row 1 MATCHES on the bankroll-identity lens (the
    # event total reproduces cash + deployed exactly); its cash-only delta
    # (+1070.00 - round-1 entries landed 2ms AFTER the snapshot) is still
    # listed explicitly, never hidden.
    assert first["event_cash"] == Decimal("10000.00")
    assert first["snapshot_cash"] == Decimal("8930.00")
    assert first["delta"] == Decimal("1070.00")
    assert first["snapshot_total"] == Decimal("10000.00")
    assert first["event_total"] == Decimal("10000.00")
    assert first["delta_total"] == Decimal("0.00")
    assert first["status"] == "match"
    for row in plan.reconciliation:
        if row["status"] == "match":
            assert abs(row["delta_total"]) <= Decimal("0.005")
    # Later rows drift by the estimated fee layer and settle timing: all
    # diffs carry explicit deltas.
    diffs = [row for row in plan.reconciliation if row["status"] == "diff"]
    assert diffs and all(row["delta_total"] is not None for row in diffs)
    listed = [line for line in report.splitlines()
              if re.match(r"  2026-", line)]
    assert len(listed) == 68
    assert any(": diff" in line for line in listed)
    assert any(": match" in line for line in listed)


def test_single_initial_event_anchors_the_book(plan):
    key, occurred_at, amount, note = plan.initial_event
    assert key == "legacy:bankroll:initial"
    assert amount == Decimal("10000.00")
    assert occurred_at == plan.reconciliation[0]["ts"]
    assert "bankroll_identity" in note


# ---------------------------------------------------------------------------
# Authorization gate (8.3 item 10): dry run by default, production refuses
# ---------------------------------------------------------------------------


def _loaded_driver_modules() -> set[str]:
    """The psycopg distribution modules currently in ``sys.modules``.

    M1 CI fix (native run 43741792178): the native papersim lane runs the
    earlier native test files in one pytest process, so the driver may
    already be imported here. The offline proof becomes order-independent
    by comparing the loaded-driver delta across the call instead of
    requiring an empty ``sys.modules``.
    """
    return {name for name in sys.modules
            if name == "psycopg" or name.startswith("psycopg.")}


def test_cli_default_is_a_dry_run_that_never_loads_a_driver(importer, capsys):
    before = _loaded_driver_modules()  # empty offline; native may differ
    code = importer.main([])
    assert code == 0
    out = capsys.readouterr().out
    assert out.startswith("papersim legacy import - DRY RUN")
    assert "total 3768.33" in out
    assert _loaded_driver_modules() == before  # the dry run loaded no driver


def test_cli_refuses_production_insertion_without_authorization(
    importer, capsys
):
    # A target DSN without --execute-production is refused BEFORE any
    # connection is attempted (a dead port proves no connect happened).
    before = _loaded_driver_modules()
    dead = "postgresql://paper_sim@127.0.0.1:1/paper_sim"
    code = importer.main(["--dsn", dead])
    assert code == 2
    err = capsys.readouterr().err
    assert "production insertion is not authorized" in err
    assert "--execute-production" in err
    assert _loaded_driver_modules() == before  # no driver load on refusal


def test_cli_execute_production_requires_a_target(importer, capsys):
    code = importer.main(["--execute-production"])
    assert code == 2
    assert "requires a target" in capsys.readouterr().err


def test_cli_register_baseline_requires_the_legacy_face(importer, capsys):
    code = importer.main(["--register-baseline"])
    assert code == 2
    assert "--from-legacy-db" in capsys.readouterr().err


def test_cli_snapshot_dir_override(importer, tmp_path, capsys):
    _write_minimal_snapshot(
        tmp_path,
        decisions="1,2026-10-10 13:39:29.867432+00,m-ok,q1,YES,0.5,100,"
                  "50.00,,,,,,,,crypto_eth,0.6,crypto_daily\n",
        resolutions="m-ok,2026-10-20 00:00:00+00,YES,200.00,150.00,e1\n",
        bankroll="2026-10-10 13:39:29+00,9950.00,50.00,round-1\n")
    code = importer.main(["--snapshot-dir", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "rows: decisions=1 resolutions=1 bankroll=1" in out
    assert "total 150.00" in out


def test_cli_blocks_a_settlement_before_a_single_decision(importer, tmp_path, capsys):
    directory = _write_minimal_snapshot(
        tmp_path,
        decisions="1,2026-10-20 00:01:00+00,m-early,q1,YES,0.5,100,"
                  "50.00,,,,,,,,crypto_eth,0.6,crypto_daily\n",
        resolutions="m-early,2026-10-20 00:00:00+00,YES,100.00,50.00,e1\n",
        bankroll="2026-10-10 13:39:29+00,9950.00,50.00,round-1\n",
    )
    assert importer.main(["--snapshot-dir", str(directory)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "papersim_legacy_resolution_precedes_entry" in captured.err
    assert "market_id='m-early' decision_ids=['1']" in captured.err


@pytest.mark.parametrize(
    ("evidence", "blocked"),
    [
        ("Settles decision #102 only.", True),
        ("Applies to decision #102.", True),
        ("This settlement is for decision #102 only.", True),
        ("Settlement applies only to decision #102.", True),
        ("Only decision #102 was settled.", True),
        ("The only decision that was settled was #102.", True),
        ("Resolution for decisions #101 and #102.", True),
        ("This settlement does not settle decision #101.", True),
        ("Decision #101 was not settled.", True),
        ("Decision #101 hasn't been settled.", True),
        ("This settlement is not for decision #101.", True),
        ("This settlement does not apply to decision #101.", True),
        ("Settles decision #101. Decision #101 was not settled.", True),
        ("Settles decision #101. Same fixture as open #102 and #103.", False),
        ("Only decision #101 was settled. Compare previous market from decision #102.", False),
        ("Compare previous market from decision #102.", False),
        ("This settlement does not settle decision #102.", False),
        ("Decision #102 was not settled. Settles decision #101.", False),
        ("Same fixture as open #102 and #103.", False),
        ("Match confirmed by the published final score.", False),
        ("", False),
    ],
)
def test_single_decision_import_requires_consistent_explicit_attribution(
    evidence, blocked, importer, tmp_path, capsys,
):
    directory = _write_minimal_snapshot(
        tmp_path,
        decisions="101,2026-10-10 12:00:00+00,m-one,q1,YES,0.5,100,"
                  "50.00,,,,,,,,crypto_eth,0.6,crypto_daily\n",
        resolutions=f"m-one,2026-10-20 00:00:00+00,YES,100.00,50.00,{evidence}\n",
        bankroll="2026-10-10 11:00:00+00,1000.00,0.00,initial\n",
    )
    assert importer.main(["--snapshot-dir", str(directory)]) == (2 if blocked else 0)
    captured = capsys.readouterr()
    if blocked:
        assert captured.out == ""
        assert "papersim_legacy_resolution_evidence_conflict" in captured.err
        assert "market_id='m-one' decision_ids=['101']" in captured.err
    else:
        assert "total 50.00" in captured.out
        assert captured.err == ""
