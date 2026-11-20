"""B3 settle offline orchestration proofs (plan 05 section 2 / 80.8).

Everything here runs without a database and without a network: the
open-entry snapshot comes from an injected loader, the settlement and
receipt writers are in-memory fakes (the real
``store_psycopg.settlement_record`` / ``record_command_run_event``
contracts), and the worktree check is injected except in the one
disposable-repo mirror test.  The native lane
(tests/test_papersim_settle_store_native.py) proves the transactional
writes on a disposable cluster.

Pinned semantics: natural win/loss and economics via ``ledger``
(and the ``_natural_hit`` side rule), the fee via
``fees.taker_fee_rate``, the VOIDED stake refund, pending-with-reason
causality (no captured terminal outcome -> the entry stays open), the
divergent re-settle conflict, receipt phase order and FAILED-on-error,
the dirty-worktree refusal before any write, the basketball full-game
OT invariant (captured outcome tokens only; in-progress/partial score
materials are non-evidence), and import purity (no driver, no adapter,
no CLI, no provider surface).
"""

from __future__ import annotations

import dataclasses
import datetime
from decimal import Decimal, localcontext
import inspect
import json
from pathlib import Path
import subprocess
import sys

import pytest

from polymarket_alpha_lab.papersim import fees, ledger, scan, settle, store

NOW = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.UTC)
T_OBSERVED = datetime.datetime(2026, 11, 20, 13, 10, tzinfo=datetime.UTC)
T_CONFIRMED = datetime.datetime(2026, 11, 20, 13, 40, tzinfo=datetime.UTC)
COMMIT = "a" * 40


def _entry(entry_id=7, *, candidate_id=70, side="YES", outcome="YES", outcome_id=101,
           materials=None, confirmed=T_CONFIRMED, observed=T_OBSERVED,
           stake=Decimal("250"), price=Decimal("0.56"), fee_category="crypto",
           entry_fee=Decimal("7.70"), fee_source="schedule", exit_marked=False,
           team="crypto_eth"):
    captured = None
    if outcome is not None:
        captured = settle.CapturedOutcome(
            outcome_id=outcome_id, outcome=outcome, observed_at=observed,
            confirmed_at=confirmed, source_kind="gamma_close",
            score_materials=materials, identity=settle.MarketIdentity(market_id="m-1"))
    return settle.OpenSettleEntry(
        entry_id=entry_id, candidate_id=candidate_id, team=team, side=side,
        stake=stake, price=price, fee_category=fee_category,
        entered_at=NOW - datetime.timedelta(days=2), fee_source=fee_source,
        condition_id=None, market_id="m-1", market_slug=None,
        exit_marked=exit_marked, outcome=captured, entry_fee=entry_fee)


class Harness:
    """In-memory settlement/receipt writers with an ordered event log."""

    def __init__(self):
        self.events = []       # ('receipt', phase) | ('settle', entry_id) in order
        self.receipts = []
        self.settlements = []
        self.next_id = 500
        self.settle_behavior = None  # optional callable(record) -> dict

    def receipt(self, conn, submission):
        submission.validate()
        self.events.append(("receipt", submission.phase))
        self.receipts.append(submission)
        return {"run_event_id": len(self.receipts),
                "run_event_key": submission.key(), "inserted": True}

    def settle(self, conn, record):
        self.events.append(("settle", record.entry_id))
        self.settlements.append(record)
        if self.settle_behavior is not None:
            return self.settle_behavior(record)
        self.next_id += 1
        return {"settlement_id": self.next_id, "inserted": True,
                "bankroll_event_id": self.next_id + 1000}


def _run(harness, entries, *, team="pal-b3", operator="unattended",
         operation_key="settle|pal-b3|test", now=NOW, commit=COMMIT):
    return settle.run_settle(
        team, conn=None, repo_root=None, now_utc=lambda: now,
        open_entries=lambda conn: tuple(entries), operator=operator,
        operation_key=operation_key, commit_check=lambda: commit,
        settle_writer=harness.settle, receipt_writer=harness.receipt)


# ---------------------------------------------------------------------------
# Natural win/loss and economics (ledger._natural_hit semantics + fees)
# ---------------------------------------------------------------------------

NATURAL_CASES = [
    ("YES", "YES", "win"),
    ("YES", "NO", "loss"),
    ("YES", "YES-basket", "loss"),  # the hit rule is verbatim, no special cases
    ("NO", "YES", "loss"),
    ("NO", "NO", "win"),
    ("YES-basket", "YES-basket", "win"),
    ("YES-basket", "NO", "loss"),
    ("NO-basket", "NO", "win"),
    ("NO-basket", "YES", "loss"),
]


@pytest.mark.parametrize("side,outcome,expected", NATURAL_CASES)
def test_natural_win_loss_follows_the_side_rule(side, outcome, expected):
    decision = settle.plan_settlement(_entry(side=side, outcome=outcome))
    assert decision.status == "SETTLED"
    assert decision.classification == "NATURAL"
    record = decision.record
    record.validate()
    assert record.win_loss == expected
    assert record.outcome_label == outcome  # the captured token, never re-derived
    assert record.source_outcome_id == 101
    assert record.exit_price is None and record.exit_fee == 0
    assert record.confirmed_by == "unattended" and record.confirmed_at == T_CONFIRMED
    assert record.event_key() == "settle:7"
    assert record.cash_reason() == "SETTLE_NATURAL"
    stake, price = 250.0, 0.56
    if expected == "win":
        assert float(record.proceeds) == pytest.approx(stake / price)
    else:
        assert float(record.proceeds) == 0.0
    fee_total = stake * fees.taker_fee_rate("crypto") * (1.0 - price)
    assert float(record.fee_total) == pytest.approx(fee_total)
    assert record.fee_total == Decimal("7.70")
    assert float(record.pnl_gross) == pytest.approx(float(record.proceeds) - stake)
    assert float(record.pnl_net) == pytest.approx(float(record.pnl_gross)
                                                  - float(record.fee_total))
    # the recorded triple satisfies the store identity exactly (Decimal)
    assert record.pnl_net == Decimal(str(record.pnl_gross)) \
        - Decimal(str(record.fee_total))


def test_natural_economics_equal_ledger_settle_natural_exactly():
    paper = ledger.settle_natural(250.0, 0.56, "YES", "YES",
                                  fee_rate=fees.taker_fee_rate("sports"))
    entry = _entry(side="YES", outcome="YES", fee_category="sports",
                   entry_fee=Decimal(str(paper.fee_total)))
    record = settle.plan_settlement(entry).record
    assert float(record.proceeds) == paper.proceeds
    assert float(record.pnl_gross) == paper.pnl
    assert float(record.fee_total) == paper.fee_total
    assert float(record.pnl_net) == paper.pnl_net
    assert record.win_loss == paper.win_loss == "win"
    # sports fee schedule (0.05), never a silent default
    assert float(record.fee_total) == pytest.approx(250.0 * 0.05 * 0.44)


def test_natural_confirmed_at_falls_back_to_observed_at():
    entry = _entry(confirmed=None)
    record = settle.plan_settlement(entry).record
    assert record.confirmed_at == T_OBSERVED


def test_voided_refund_returns_the_stake_with_zero_pnl():
    decision = settle.plan_settlement(_entry(outcome="VOIDED", outcome_id=55))
    assert decision.status == "SETTLED"
    record = decision.record
    record.validate()
    assert record.classification == "VOIDED_REFUND"
    assert record.win_loss is None  # never a win or a loss
    assert record.proceeds == Decimal("250")
    assert record.pnl_gross == 0 and record.fee_total == 0 and record.pnl_net == 0
    assert record.source_outcome_id == 55
    assert record.cash_reason() == "VOIDED_REFUND"
    assert record.event_key() == "settle:7"
    assert record.cash_amount(entry_fee=Decimal("7.70")) == Decimal("257.70")
    assert record.cash_amount(entry_fee=Decimal(0)) == Decimal("250")
    with pytest.raises(store.PapersimStoreError, match="papersim_entry_fee_invalid"):
        record.cash_amount()


@pytest.mark.parametrize("precision", [2, 28, 50])
def test_wide_money_planning_validation_and_bankroll_cancellation_are_exact(precision):
    fee = Decimal("0.1234567890123456789012345")
    with localcontext(prec=precision):
        entry = _entry(price=Decimal("0.0015"), entry_fee=fee)
        record = settle.plan_settlement(entry).record.validate()
        assert record.proceeds == Decimal("166666.66666666666")
        assert record.pnl_gross == Decimal("166416.66666666666")
        assert record.pnl_net == Decimal("166416.5432098776476543210987655")
        assert record.cash_amount() == record.proceeds
        manual_exit = dataclasses.replace(
            record, classification="EXIT", win_loss=None, exit_price=Decimal("0.5"),
            proceeds=Decimal("250"), exit_fee=fee).validate()
        assert manual_exit.cash_amount() == Decimal("249.8765432109876543210987655")
        assert store.bankroll_identity_difference(
            Decimal("9.74299876543210987654321098765E+3"), 250, Decimal("1E+4"), 0,
            Decimal("7.00123456789012345678901235")) == 0
        assert store.exact_sum(Decimal("9.99999999999999999999999999E+30"),
                               Decimal("1E+4")) == Decimal("1E+31")
        assert store.exact_sum(Decimal("9E+120000"), Decimal("1E-16000"),
                               Decimal("-9E+120000")) == Decimal("1E-16000")


@pytest.mark.parametrize(("shares", "stake", "valid"), [
    ("502", "250", False), ("502", "251", True), ("500.019998", "250", True),
    ("500.02", "250", False), ("500.020002", "250", False),
    ("499.98", "250", False), ("499.980002", "250", True),
])
def test_entry_share_product_validation_under_precision_two(shares, stake, valid):
    entry = store.EntrySubmission(
        stake=Decimal(stake), price=Decimal("0.5"), shares=Decimal(shares),
        fee_category="crypto", entry_fee=Decimal("7.70"), tier="standard", entered_at=NOW)
    with localcontext(prec=2):
        if valid:
            assert entry.validate() is entry
        else:
            with pytest.raises(store.PapersimStoreError, match="papersim_entry_stake_mismatch"):
                entry.validate()


# ---------------------------------------------------------------------------
# Pending causality (missing/UNKNOWN evidence, exits, score materials)
# ---------------------------------------------------------------------------

def test_no_captured_outcome_leaves_the_entry_pending():
    decision = settle.plan_settlement(_entry(outcome=None))
    assert decision.status == "PENDING"
    assert decision.reason == "no_captured_outcome"
    assert decision.record is None


def test_unknown_only_coverage_is_an_explicit_gap_not_a_filled_value():
    decision = settle.plan_settlement(_entry(outcome="UNKNOWN", outcome_id=8))
    assert decision.status == "PENDING" and decision.reason == "outcome_unknown"
    decision = settle.plan_settlement(_entry(outcome="UNKNOWN", outcome_id=None))
    assert decision.status == "PENDING" and decision.reason == "no_captured_outcome"


def test_exit_marked_entries_wait_for_manual_attestation():
    for outcome in (None, "UNKNOWN"):
        decision = settle.plan_settlement(_entry(outcome=outcome, exit_marked=True))
        assert decision.status == "PENDING"
        assert decision.reason == "exit_manual_attestation"
    # a resolved market settles even on an exit-marked entry (exit is moot)
    decision = settle.plan_settlement(_entry(outcome="NO", exit_marked=True))
    assert decision.status == "SETTLED" and decision.classification == "NATURAL"


def test_in_progress_and_partial_scores_are_non_evidence():
    in_progress = settle.ScoreMaterials(status="in_progress", home_score=118,
                                        away_score=120, note="Q4 6:32")
    decision = settle.plan_settlement(_entry(outcome="YES", materials=in_progress))
    assert decision.status == "PENDING"
    assert decision.reason == "score_materials_incomplete"
    assert decision.record is None
    partial = settle.ScoreMaterials(status="completed", home_score=None,
                                    away_score=125)
    assert settle.is_completed_game(partial) is False
    decision = settle.plan_settlement(_entry(outcome="YES", materials=partial))
    assert decision.reason == "score_materials_incomplete"
    voided_partial = settle.plan_settlement(_entry(outcome="VOIDED",
                                                   materials=in_progress))
    assert voided_partial.reason == "score_materials_incomplete"


# ---------------------------------------------------------------------------
# Basketball full-game OT invariant (tokens only, completed-game scores)
# ---------------------------------------------------------------------------

def test_is_completed_game_accepts_only_finished_whole_number_scores():
    final_ot = settle.ScoreMaterials(status="completed", home_score=128,
                                     away_score=125, note="final after OT")
    assert settle.is_completed_game(final_ot) is True
    assert settle.is_completed_game(settle.ScoreMaterials(
        status="completed", home_score=0, away_score=0)) is True
    for bad in (settle.ScoreMaterials(status="in_progress", home_score=118, away_score=120),
                settle.ScoreMaterials(status="halftime", home_score=55, away_score=60),
                settle.ScoreMaterials(status="completed", home_score=128.5, away_score=125),
                settle.ScoreMaterials(status="completed", home_score=-1, away_score=125),
                settle.ScoreMaterials(status="completed", home_score=None, away_score=125)):
        assert settle.is_completed_game(bad) is False


def test_nba_full_game_moneyline_settles_ot_inclusive_via_the_captured_token():
    # 128-125 after OT: the market-level token (captured by the capture
    # lane from the finished game) is the ONLY settlement input; the
    # completed final score merely accompanies the evidence.
    final_ot = settle.ScoreMaterials(status="completed", home_score=128,
                                     away_score=125, note="final after OT")
    decision = settle.plan_settlement(_entry(
        entry_id=11, side="YES", outcome="YES", outcome_id=901,
        fee_category="sports", entry_fee=Decimal("5.50"), materials=final_ot))
    assert decision.status == "SETTLED"
    record = decision.record
    assert record.classification == "NATURAL" and record.win_loss == "win"
    assert record.outcome_label == "YES"  # the token, never the score
    assert record.evidence is None  # no score text leaks into the settlement
    assert float(record.proceeds) == pytest.approx(250.0 / 0.56)


def test_nba_full_game_totals_settle_on_the_token_not_the_score_arithmetic():
    final_ot = settle.ScoreMaterials(status="completed", home_score=128,
                                     away_score=125)  # total 253, over line missed
    decision = settle.plan_settlement(_entry(
        entry_id=12, side="YES", outcome="NO", outcome_id=902,
        fee_category="sports", entry_fee=Decimal("5.50"), materials=final_ot))
    record = decision.record
    assert record.classification == "NATURAL" and record.win_loss == "loss"
    assert record.outcome_label == "NO"
    assert float(record.proceeds) == 0.0


def test_nba_in_progress_game_cannot_settle_a_full_game_market():
    in_progress = settle.ScoreMaterials(status="in_progress", home_score=118,
                                        away_score=120, note="Q4 6:32")
    decision = settle.plan_settlement(_entry(
        entry_id=13, side="YES", outcome="YES", outcome_id=903,
        fee_category="sports", materials=in_progress))
    assert decision.status == "PENDING"
    assert decision.reason == "score_materials_incomplete"
    assert decision.record is None


# ---------------------------------------------------------------------------
# Snapshot validation
# ---------------------------------------------------------------------------

def _invalid_entries():
    return [
        _entry(entry_id="7"),
        _entry(side="MAYBE"),
        _entry(stake=Decimal("0")),
        _entry(price=Decimal("1.0")),
        _entry(fee_category=""),
        _entry(fee_source="guessed"),
        _entry(exit_marked="yes"),
        _entry(candidate_id=0),
    ]


def test_snapshot_corruption_fails_loudly():
    for candidate in _invalid_entries():
        with pytest.raises(settle.SettleError) as error:
            settle.plan_settlement(candidate)
        assert error.value.code == "papersim_settle_snapshot_invalid"


def test_unknown_terminal_token_is_rejected_loudly():
    with pytest.raises(settle.SettleError) as error:
        settle.plan_settlement(_entry(outcome="MAYBE", outcome_id=1))
    assert error.value.code == "papersim_settle_outcome_token_invalid"


def test_evidence_without_any_timestamp_cannot_confirm():
    entry = _entry(confirmed=None, observed=None)
    with pytest.raises(settle.SettleError) as error:
        settle.plan_settlement(entry)
    assert error.value.code == "papersim_settle_evidence_time_missing"


class _OffsetMissing(datetime.tzinfo):
    def utcoffset(self, dt):
        return None


@pytest.mark.parametrize(("observed", "confirmed"), [
    (None, T_CONFIRMED),
    (T_OBSERVED.replace(tzinfo=None), T_CONFIRMED),
    (T_OBSERVED, T_CONFIRMED.replace(tzinfo=None)),
    (T_OBSERVED.replace(tzinfo=_OffsetMissing()), T_CONFIRMED),
])
def test_pure_planner_rejects_malformed_instants(observed, confirmed):
    with pytest.raises(settle.SettleError) as error:
        settle.plan_settlement(_entry(confirmed=confirmed, observed=observed))
    assert error.value.code == "papersim_settle_evidence_time_invalid"


def test_pure_planner_cannot_use_an_outcome_from_before_entry():
    stale = _entry(observed=NOW, confirmed=NOW - datetime.timedelta(minutes=1))
    stale = dataclasses.replace(stale, entered_at=NOW)
    assert settle.plan_settlement(stale).reason == "no_captured_outcome"
    with pytest.raises(settle.SettleError) as error:
        settle.plan_settlement(dataclasses.replace(stale, entered_at=None))
    assert error.value.code == "papersim_settle_snapshot_invalid"


def test_market_identity_precedence_and_lift_helper():
    entry = _entry()
    assert entry.market_identity == "m-1"
    with_condition = dataclasses.replace(entry, condition_id="c-1")
    assert with_condition.market_identity == "c-1"
    effective = store.EffectiveMarketOutcome(9, "NO", observed_at=T_OBSERVED,
                                             source_kind="clob_terminal_price")
    lifted = settle.captured_from_effective(effective, confirmed_at=T_CONFIRMED)
    assert (lifted.outcome_id, lifted.outcome, lifted.is_effective) == (9, "NO", True)
    assert lifted.evidence_time == T_CONFIRMED
    gap = settle.captured_from_effective(store.EffectiveMarketOutcome(None, "UNKNOWN"))
    assert not gap.is_effective and gap.evidence_time is None


# ---------------------------------------------------------------------------
# The driver: receipts, conflicts, idempotency, ordering, refusal
# ---------------------------------------------------------------------------

def test_run_settle_writes_settlements_and_ordered_receipts():
    harness = Harness()
    pending_entry = _entry(entry_id=3, outcome=None)
    settled_entry = _entry(entry_id=9, outcome="NO", outcome_id=77)
    result = _run(harness, (settled_entry, pending_entry))  # unordered input
    assert result["settled_count"] == 1 and result["pending_count"] == 1
    assert result["snapshot_entries"] == 2
    assert result["code_commit"] == COMMIT and result["team"] == "pal-b3"
    assert result["pending"] == [{"entry_id": 3, "reason": "no_captured_outcome"}]
    assert result["settled"][0]["entry_id"] == 9
    assert harness.events == [("receipt", "STARTED"), ("settle", 9),
                              ("receipt", "SUCCEEDED")]
    started, succeeded = harness.receipts
    assert started.phase == "STARTED" and started.command == "settle"
    assert started.operation_key == "settle|pal-b3|test"
    assert started.code_commit == COMMIT and started.output_evidence == []
    # entry-id ascending regardless of input order
    assert succeeded.output_evidence == [
        "pending:3:no_captured_outcome", "settled:9:NATURAL"]
    assert json.dumps(result, sort_keys=True, allow_nan=False)


def test_identical_rerun_is_a_safe_noop():
    harness = Harness()
    entry = _entry()
    first = _run(harness, (entry,), operation_key="settle|pal-b3|k")
    assert first["settled"][0]["inserted"] is True
    harness.settle_behavior = lambda record: {
        "settlement_id": 501, "inserted": False, "bankroll_event_id": 1501}
    second = _run(harness, (entry,), operation_key="settle|pal-b3|k")
    assert second["settled_count"] == 1
    assert second["settled"][0]["inserted"] is False
    assert second["settled"][0]["settlement_id"] == 501
    assert len(harness.settlements) == 2  # the writer saw the same content twice


def test_divergent_re_settle_surfaces_the_store_conflict():
    harness = Harness()
    entry = _entry()
    _run(harness, (entry,), operation_key="settle|pal-b3|k")
    _run(harness, (entry,), operation_key="settle|pal-b3|k")  # identical: fine

    def boom(record):
        raise store.PapersimStoreError("papersim_settlement_conflict")

    harness.settle_behavior = boom
    with pytest.raises(store.PapersimStoreError,
                       match="papersim_settlement_conflict"):
        _run(harness, (entry,), operation_key="settle|pal-b3|k")
    assert [submission.phase for submission in harness.receipts] == [
        "STARTED", "SUCCEEDED", "STARTED", "SUCCEEDED", "STARTED", "FAILED"]
    assert harness.receipts[-1].error_code == "papersim_settlement_conflict"


def test_receipt_failed_on_error_and_no_settlement_writes_survive():
    harness = Harness()
    with pytest.raises(settle.SettleError):
        _run(harness, (_entry(side="MAYBE", outcome=None),),
             operation_key="settle|pal-b3|bad")
    assert [submission.phase for submission in harness.receipts] == [
        "STARTED", "FAILED"]
    assert harness.receipts[-1].error_code == "papersim_settle_snapshot_invalid"
    assert harness.settlements == []


def test_loader_failure_after_started_appends_failed_receipt():
    harness = Harness()

    def exploding_loader(conn):
        raise RuntimeError("snapshot blew up")

    with pytest.raises(RuntimeError):
        settle.run_settle(
            "pal-b3", conn=None, repo_root=None, now_utc=lambda: NOW,
            open_entries=exploding_loader, operation_key="settle|pal-b3|k",
            commit_check=lambda: COMMIT, settle_writer=harness.settle,
            receipt_writer=harness.receipt)
    assert [submission.phase for submission in harness.receipts] == [
        "STARTED", "FAILED"]
    assert harness.receipts[-1].error_code == "RuntimeError"
    assert harness.settlements == []


def test_empty_snapshot_succeeds_with_zero_counts():
    harness = Harness()
    result = _run(harness, ())
    assert result["settled_count"] == 0 and result["pending_count"] == 0
    assert result["snapshot_entries"] == 0
    assert harness.receipts[-1].output_evidence == []
    assert harness.settlements == []


def test_default_operation_key_uses_the_b3_stamp_format():
    harness = Harness()
    result = settle.run_settle(
        "pal-b3", conn=None, repo_root=None, now_utc=lambda: NOW,
        open_entries=lambda conn: (), commit_check=lambda: COMMIT,
        settle_writer=harness.settle, receipt_writer=harness.receipt)
    stamp = NOW.astimezone(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    assert result["operation_key"] == f"settle|pal-b3|{stamp}|b3"


def test_team_and_operator_validation_refuse_before_any_write():
    harness = Harness()
    with pytest.raises(settle.SettleError) as team_error:
        _run(harness, (), team="   ")
    assert team_error.value.code == "papersim_settle_team_invalid"
    with pytest.raises(settle.SettleError) as operator_error:
        _run(harness, (), operator="")
    assert operator_error.value.code == "papersim_settle_operator_invalid"
    with pytest.raises(settle.SettleError) as attempt_error:
        settle.run_settle("pal-b3", conn=None, repo_root=None, now_utc=lambda: NOW,
                          open_entries=lambda conn: (), attempt=0,
                          commit_check=lambda: COMMIT,
                          settle_writer=harness.settle,
                          receipt_writer=harness.receipt)
    assert attempt_error.value.code == "papersim_settle_attempt_invalid"
    assert harness.receipts == [] and harness.settlements == []


# ---------------------------------------------------------------------------
# FX-03 clean-worktree refusal
# ---------------------------------------------------------------------------

def test_dirty_worktree_refusal_happens_before_any_write():
    harness = Harness()

    def dirty():
        raise settle.SettleError("papersim_settle_worktree_dirty", "?? dirt.txt")

    with pytest.raises(settle.SettleError) as error:
        settle.run_settle(
            "pal-b3", conn=None, repo_root=None, now_utc=lambda: NOW,
            open_entries=lambda conn: (), commit_check=dirty,
            settle_writer=harness.settle, receipt_writer=harness.receipt)
    assert error.value.code == "papersim_settle_worktree_dirty"
    assert harness.receipts == []  # no STARTED receipt on a refusal
    assert harness.settlements == []


def _git_repo(path: Path) -> Path:
    path.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "T"]):
        subprocess.run(["git", "-C", str(path), *args], check=True,
                       capture_output=True, text=True)
    (path / "seed.txt").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "seed.txt"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "seed"], check=True,
                   capture_output=True, text=True)
    return path


def test_settle_commit_check_mirrors_scan_on_a_disposable_repo(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    assert settle.settle_commit_check(repo) == scan.require_clean_worktree(repo)
    (repo / "untracked.txt").write_text("dirt", encoding="utf-8")
    with pytest.raises(settle.SettleError) as settle_refusal:
        settle.settle_commit_check(repo)
    assert settle_refusal.value.code == "papersim_settle_worktree_dirty"
    with pytest.raises(scan.ScanError) as scan_refusal:
        scan.require_clean_worktree(repo)
    assert scan_refusal.value.code == "papersim_scan_worktree_dirty"


# ---------------------------------------------------------------------------
# Module hygiene: import purity, no provider consult
# ---------------------------------------------------------------------------

def test_settle_module_import_is_pure():
    script = (
        "import sys\n"
        "from polymarket_alpha_lab.papersim import settle\n"
        "banned = ('polymarket_alpha_lab.papersim.cli',\n"
        "          'polymarket_alpha_lab.papersim.store_psycopg',\n"
        "          'polymarket_alpha_lab.papersim.scan',\n"
        "          'polymarket_alpha_lab.papersim.optimize',\n"
        "          'polymarket_alpha_lab.papersim.adapters.polymarket')\n"
        "for name in banned:\n"
        "    assert name not in sys.modules, name\n"
        "assert not [name for name in sys.modules if 'psycopg' in name], 'driver loaded'\n"
        "print('ok')\n"
    )
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True,
                               text=True, check=True)
    assert completed.stdout.strip().endswith("ok")


def test_settle_source_never_imports_adapter_driver_cli_or_provider():
    # The package __init__ preloads the pure stdlib contracts module
    # (papersim.adapters.contracts) for every papersim import, so the
    # process-level probe above cannot distinguish settle's own imports.
    # This source-level proof can: settle.py's module-level imports are
    # exactly the pure layers (fees/ledger/store + stdlib).
    import ast

    tree = ast.parse(Path(settle.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.col_offset == 0:
            imported.add(node.module)
    forbidden = ("polymarket_alpha_lab.papersim.adapters",
                 "polymarket_alpha_lab.papersim.store_psycopg",
                 "polymarket_alpha_lab.papersim.scan",
                 "polymarket_alpha_lab.papersim.cli",
                 "polymarket_alpha_lab.papersim.optimize",
                 "psycopg")
    for module in imported:
        assert not module.startswith(forbidden), module


def test_settle_never_consults_a_model_provider():
    # The (empty or not) model registry is irrelevant to settlement: no
    # provider parameter exists anywhere on the orchestration surface.
    for function in (settle.run_settle, settle.plan_settlement, settle.plan_all):
        assert "provider" not in inspect.signature(function).parameters
    assert "specs" not in inspect.signature(settle.run_settle).parameters


# ---------------------------------------------------------------------------
# CLI / store wiring (the B3 shared-file pass: the settle subcommand and
# the FX-29 production snapshot helpers; behavior proofs are native)
# ---------------------------------------------------------------------------

def test_papersim_cli_exposes_the_settle_subcommand():
    from polymarket_alpha_lab.papersim import cli

    parser = cli._build_parser()
    args = parser.parse_args(["settle", "--team", "crypto_eth", "--dsn",
                              "host=127.0.0.1 port=55432 dbname=pal_x user=pal_app"])
    assert args.command == "settle" and args.team == "crypto_eth"
    assert args.managed is False and args.dsn
    assert args.repo_root == "." and args.operator == "unattended"
    assert args.operation_key is None and args.attempt == 1
    args = parser.parse_args(["settle", "--team", "t", "--managed"])
    assert args.managed is True and args.dsn is None
    args = parser.parse_args(["settle", "--team", "t", "--managed",
                              "--operation-key", "k", "--attempt", "3",
                              "--operator", "op", "--repo-root", "/tmp/r"])
    assert (args.operation_key, args.attempt, args.operator) == ("k", 3, "op")
    assert args.repo_root == "/tmp/r"
    with pytest.raises(SystemExit):  # the connection source is required
        parser.parse_args(["settle", "--team", "t"])
    with pytest.raises(SystemExit):  # ... and mutually exclusive
        parser.parse_args(["settle", "--team", "t", "--managed", "--dsn", "d"])


def test_cli_settle_handler_wires_the_production_loader_and_dispatch():
    from polymarket_alpha_lab.papersim import cli, store_psycopg

    # the handler drives settle.run_settle with the PRODUCTION FX-29
    # snapshot loader from the store adapter (no test-side copy)
    source = inspect.getsource(cli._cmd_settle)
    assert "settle.run_settle(" in source
    assert "open_entries=store_psycopg.open_entries_for_settlement" in source
    assert 'label="PAPERSIM_SETTLE_PG_DSN"' in source
    # main() dispatches the subcommand
    assert 'args.command == "settle"' in inspect.getsource(cli.main)
    assert callable(store_psycopg.open_entries_for_settlement)


def test_fx29_production_readers_pin_the_one_snapshot_discipline():
    from polymarket_alpha_lab.papersim import cli, store_psycopg

    # the promoted settle loader: ONE repeatable-read read-only snapshot
    loader = inspect.getsource(store_psycopg.open_entries_for_settlement)
    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY" in loader
    assert "settle.OpenSettleEntry(" in loader
    # bankroll_daily_check (FX-29): the identity recomputation starts by
    # raising the isolation level (read-write: the receipt insert shares
    # the same snapshot-bounded transaction)
    check = inspect.getsource(store_psycopg.bankroll_daily_check)
    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'" in check
    assert "READ ONLY" not in check
    # the evaluate loader (FX-29): the three SELECTs share ONE
    # repeatable-read read-only transaction
    evaluate = inspect.getsource(cli._evaluation_candidates_from_store)
    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY" in evaluate
    assert evaluate.count("conn.transaction()") == 1
    assert "_fetch_rows(" not in evaluate
