"""FX-11 paper-sim evaluation module tests.

Covers the M1 FX-11 node contract (docs/handoffs/2026-11-12-audit
03-fix-list.md FX-11; re-reviewed M1 plan section `FX-11：净指标、全候选
评分与已批准规则`):

- pure per-queue (model + market_type + gate version) metrics: counts,
  gross / two-sided-fee net ROI with clustered fixed-seed bootstrap,
  CLV, hit rate / average fill price, Brier / log loss, paired
  model-minus-market intervals, calibration, profit concentration,
  max drawdown;
- all-candidate scoring that is NOT conditioned on entry or settlement
  (all-rejected zero-entry queues still compare model vs market),
  actual-entry CLV / trade ROI strictly separated from unentered
  market-move diagnostics, UNKNOWN / INSUFFICIENT coverage instead of
  fabricated rows, zero-filled gaps, or rejections counted as losses;
- OD-G promotion criteria A-F (registered 2026-11-13 numbers) and the
  blueprint / FX-17 pause rules as suggestion output only;
- historical regression on the frozen 10-30 snapshot fixture
  (docs/paper-sim/review-2026-10-30/settled_ledger.json): gross 23.30%,
  top-market share 51.1%, excluding-top-market 12.22%, Brier 0.1873 vs
  0.2082, original bootstrap tolerance +/-1.5 percentage points.
  Historical regression, NOT v2-corrected performance.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from polymarket_alpha_lab.papersim import evaluation as ev
from polymarket_alpha_lab.papersim import ledger

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "docs" / "paper-sim" / "review-2026-10-30" / "settled_ledger.json"

_T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


def _snapshot_rows():
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def mk(**overrides):
    """One synthetic accepted+settled candidate; overrides adapt it."""
    base = dict(
        candidate_id="c1",
        model_key="soccer-v1",
        market_type="ou_over",
        gate_version="gates-v1",
        market_key="mk1",
        model_probability=0.65,
        market_mid=0.50,
        accepted=True,
        stake=100.0,
        fill_price=0.50,
        side="YES",
        fee_category="sports",
        fill_provenance=ev.FILL_BOOK_WALK,
        outcome="YES",
        recorded_pnl=100.0,
        decision_time=_T0,
        settled_time=_T0 + timedelta(days=1),
        close_price=None,
        match_key=None,
    )
    base.update(overrides)
    return ev.EvaluationCandidate(**base)


# ---------------------------------------------------------------------------
# Frozen versions and module surface
# ---------------------------------------------------------------------------


def test_frozen_version_constants_are_pinned():
    assert isinstance(ev.ALGORITHM_VERSION, str) and ev.ALGORITHM_VERSION
    assert isinstance(ev.PROMOTION_RULES_VERSION, str) and ev.PROMOTION_RULES_VERSION
    assert isinstance(ev.SUSPENSION_RULES_VERSION, str) and ev.SUSPENSION_RULES_VERSION
    assert ev.BOOTSTRAP_SEED == 20261114
    assert ev.BOOTSTRAP_RESAMPLES == 20000
    # OD-G registered numbers (blueprint 5.3, registered 2026-11-13).
    assert ev.PROMOTION_MIN_CANDIDATES == 200
    assert ev.PROMOTION_MIN_ENTRIES == 50
    assert ev.PROMOTION_MIN_SETTLED == 30
    assert ev.PROMOTION_MIN_RUN_DAYS == 28
    assert ev.PROMOTION_LOGLOSS_DIFF_UPPER_MAX == 0.010
    assert ev.PROMOTION_TOP_MARKET_SHARE_MAX == 0.30
    assert ev.PROMOTION_MAX_DRAWDOWN_USD == 2500.0
    assert ev.DRAWDOWN_UNIT_USD == 250.0
    # Blueprint pause + FX-17 numbers.
    assert ev.PAUSE_AFTER_ENTRIES == 40
    assert ev.FX17_SETTLED_MIN == 10
    assert ev.FX17_NET_PNL_PAUSE_BELOW == -600.0
    assert ev.FX17_ENTRIES_MIN == 20


def test_report_carries_paper_boundary_flags():
    report = ev.evaluate_queues([mk()])
    assert report.paper_only is True
    assert report.report_only is True
    assert report.readonly is True
    assert report.algorithm_version == ev.ALGORITHM_VERSION
    assert report.promotion_rules_version == ev.PROMOTION_RULES_VERSION
    assert report.suspension_rules_version == ev.SUSPENSION_RULES_VERSION


def test_empty_input_yields_empty_report():
    report = ev.evaluate_queues([])
    assert report.queues == ()


def test_queues_split_by_model_market_and_gate_version():
    rows = [
        mk(candidate_id="a"),
        mk(candidate_id="b", market_type="btts"),
        mk(candidate_id="c", model_key="soccer-v2"),
        mk(candidate_id="d", gate_version="gates-v2"),
    ]
    report = ev.evaluate_queues(rows)
    assert len(report.queues) == 4
    keys = {q.queue for q in report.queues}
    assert keys == {
        ev.QueueKey("soccer-v1", "ou_over", "gates-v1"),
        ev.QueueKey("soccer-v1", "btts", "gates-v1"),
        ev.QueueKey("soccer-v2", "ou_over", "gates-v1"),
        ev.QueueKey("soccer-v1", "ou_over", "gates-v2"),
    }


# ---------------------------------------------------------------------------
# Historical regression: 10-30 snapshot compatibility fixture
# ---------------------------------------------------------------------------


def test_ledger_snapshot_reproduces_published_audit_numbers():
    candidates = ev.candidates_from_ledger_rows(_snapshot_rows())
    report = ev.evaluate_queues(candidates)
    assert len(report.queues) == 1
    q = report.queues[0]
    # Counts: 40 rows, 5 exits, 35 natural resolutions.
    assert q.counts.candidates == 40
    assert q.counts.accepted == 40
    assert q.counts.rejected == 0
    assert q.counts.entered == 40
    assert q.counts.exited == 5
    assert q.counts.unresolved == 0
    assert q.counts.voided == 0
    # Gross ROI: recorded basis, 23.30% (stake 10954.36, pnl 2552.43).
    assert q.roi.counted == 40
    assert q.roi.stake == pytest.approx(10954.36, abs=0.01)
    assert q.roi.gross_pnl == pytest.approx(2552.43, abs=0.01)
    assert q.roi.gross_roi == pytest.approx(0.2330, abs=0.0005)
    # Net of two-sided estimated fees lands in the 20-21% band.
    assert q.roi.entry_fees == pytest.approx(294.25, abs=0.05)
    assert q.roi.exit_fees == pytest.approx(27.09, abs=0.02)
    assert 0.20 <= q.roi.net_roi <= 0.21
    assert q.roi.net_roi < q.roi.gross_roi
    # Hit rate and average fill price over natural resolutions.
    assert q.fills.natural_settled == 35
    assert q.fills.hits == 22
    assert q.fills.hit_rate == pytest.approx(22.0 / 35.0)
    assert q.fills.avg_fill_price == pytest.approx(
        sum(r["price"] for r in _snapshot_rows()) / 40.0, abs=1e-9
    )
    # All-candidate scoring: Brier 0.1873 (model) vs 0.2082 (entry price
    # used as the market proxy per the fixture's own label).
    assert q.scoring.markets_total == 40
    assert q.scoring.markets_scored == 35
    assert q.scoring.missing_outcome == 5  # the five EXITED markets
    assert q.scoring.model_brier == pytest.approx(0.1873, abs=5e-5)
    assert q.scoring.market_brier == pytest.approx(0.2082, abs=5e-5)
    assert q.scoring.brier_diff_mean == pytest.approx(-0.0209, abs=5e-5)
    assert q.scoring.model_log_loss_status == "finite"
    assert q.scoring.logloss_diff_mean is not None
    assert q.scoring.logloss_diff_mean < 0
    # Profit concentration: 51.1% (top market share of total pnl) and
    # 12.22% ROI after excluding that market.
    assert q.concentration.total_pnl == pytest.approx(2552.43, abs=0.01)
    assert q.concentration.top_market_pnl == pytest.approx(1305.20, abs=0.01)
    assert q.concentration.top_market_share == pytest.approx(0.511, abs=5e-4)
    assert q.roi.excluding_top_market_roi == pytest.approx(0.1222, abs=5e-4)
    assert q.concentration.status == ev.STATUS_OK
    # No close marks exist in the legacy rows: CLV is INSUFFICIENT, not zero.
    assert q.clv.entries_with_close == 0
    assert q.clv.mean is None
    assert q.clv.status == ev.STATUS_INSUFFICIENT


def test_ledger_snapshot_bootstrap_roi_interval_matches_original():
    q = ev.evaluate_queues(ev.candidates_from_ledger_rows(_snapshot_rows())).queues[0]
    low, high = q.roi.gross_roi_ci
    # Original audit bootstrap: -13.9% .. +60.8%; tolerance +/-1.5pp.
    assert low == pytest.approx(-0.139, abs=0.015)
    assert high == pytest.approx(0.608, abs=0.015)
    assert low <= q.roi.gross_roi <= high
    low_net, high_net = q.roi.net_roi_ci
    assert low_net <= q.roi.net_roi <= high_net
    # Brier paired interval contains the point estimate.
    b_low, b_high = q.scoring.brier_diff_ci
    assert b_low <= q.scoring.brier_diff_mean <= b_high


def test_ledger_snapshot_cross_checks_against_m0_ledger_net_semantics():
    rows = _snapshot_rows()
    q = ev.evaluate_queues(ev.candidates_from_ledger_rows(rows)).queues[0]
    performance = ledger.queue_performance(
        rows, pnl_source=ledger.PNL_SOURCE_RECORDED
    )
    assert q.roi.stake == pytest.approx(performance.stake, abs=0.01)
    assert q.roi.gross_pnl == pytest.approx(performance.gross_pnl, abs=0.01)
    assert q.roi.entry_fees == pytest.approx(performance.entry_fees, abs=0.05)
    assert q.roi.exit_fees == pytest.approx(performance.exit_fees, abs=0.02)
    assert q.roi.net_pnl == pytest.approx(performance.net_pnl, abs=0.05)


def test_ledger_snapshot_criteria_are_not_claimed_passed():
    # The fixture has no close marks and no book-walk provenance: C and B
    # must be NOT_ASSESSABLE, F is M1-blocked, and the queue must not be
    # promoted on a historical regression.
    q = ev.evaluate_queues(ev.candidates_from_ledger_rows(_snapshot_rows())).queues[0]
    verdicts = {c.key: c.verdict for c in q.promotion}
    assert verdicts["A"] == ev.VERDICT_FAIL  # 40 candidates < 200
    assert verdicts["B"] == ev.VERDICT_NOT_ASSESSABLE
    assert verdicts["C"] == ev.VERDICT_NOT_ASSESSABLE
    assert verdicts["F"] == ev.VERDICT_NOT_ASSESSABLE_M1
    assert q.overall_verdict == ev.VERDICT_FAIL
    assert q.pause.stop_new_entries is False


def test_ledger_loader_marks_fill_provenance_recorded():
    candidates = ev.candidates_from_ledger_rows(_snapshot_rows())
    assert all(c.fill_provenance == ev.FILL_RECORDED_PRICE for c in candidates)
    assert {c.fee_category for c in candidates} <= {"crypto", "sports"}
    # Team rule: gold/equity rows in crypto_touch are sports-rate.
    gold = [c for c in candidates if c.fee_category == "sports"]
    assert len(gold) >= 2


def test_hit_semantics_mirror_the_fixed_calibration_view():
    # NO-basket resolves on the natural NO (ledger._natural_hit / the
    # fixed forecast_calibration expression), pinned against the public
    # settle_natural arithmetic.
    rows = [
        mk(candidate_id="nb", side="NO-basket", model_probability=0.8,
           market_mid=0.6, fill_price=0.6, outcome="NO",
           recorded_pnl=66.67),
        mk(candidate_id="yb", side="YES-basket",
           model_probability=1.0, market_mid=0.98, fill_price=0.985,
           outcome="YES-basket", recorded_pnl=2.28),
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.fills.natural_settled == 2
    assert q.fills.hits == 2
    # Cross-check: a hit is exactly a positive settle_natural pnl.
    natural_rows = [r for r in _snapshot_rows()
                    if r["outcome"] in ledger.NATURAL_OUTCOMES]
    expected_hits = sum(
        1 for r in natural_rows
        if ledger.settle_natural(
            float(r["stake"]), float(r["price"]), str(r["side"]),
            str(r["outcome"])
        ).pnl > 0
    )
    fixture_q = ev.evaluate_queues(
        ev.candidates_from_ledger_rows(_snapshot_rows())
    ).queues[0]
    assert fixture_q.fills.hits == expected_hits == 22


# ---------------------------------------------------------------------------
# PnL semantics: VOIDED / unresolved / EXITED / recomputed natural
# ---------------------------------------------------------------------------


def test_voided_and_unresolved_rows_are_excluded_not_zeroed():
    rows = [
        mk(candidate_id="w1", market_key="mkw", outcome="VOIDED",
           recorded_pnl=0.0),
        mk(candidate_id="u1", market_key="mku", outcome=None,
           recorded_pnl=None),
        mk(candidate_id="k1", market_key="mk1"),
        mk(candidate_id="k2", market_key="mk2", outcome="NO",
           recorded_pnl=-100.0),
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.counts.candidates == 4
    assert q.counts.voided == 1
    assert q.counts.unresolved == 1
    assert q.roi.counted == 2
    assert q.roi.stake == pytest.approx(200.0)
    assert q.roi.gross_pnl == pytest.approx(0.0)  # +100 - 100
    # The unresolved row is reported, never fabricated as a loss.
    assert q.roi.status == ev.STATUS_OK


def test_exited_rows_counted_for_roi_but_not_natural_scoring():
    rows = [
        mk(candidate_id="x1", outcome="EXITED", recorded_pnl=-60.70,
           fee_category="sports"),
        mk(candidate_id="n1", market_key="mk9", outcome="YES", recorded_pnl=100.0),
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.counts.exited == 1
    assert q.roi.counted == 2  # exit still a counted entry for ROI
    assert q.fills.natural_settled == 1  # exit is not a natural outcome
    assert q.scoring.markets_scored == 1
    # Exit fee reconstructed at the exit price (ledger V-6 semantics).
    assert q.roi.exit_fees > 0.0


@pytest.mark.parametrize(("win", "loss"), [("win", "loss"), ("YES", "NO"), ("YES", "loss")])
def test_natural_hit_rate_includes_trade_labels_and_market_tokens(win, loss):
    rows = [
        mk(candidate_id="w", market_key="winner", outcome=win),
        mk(candidate_id="l", market_key="loser", outcome=loss, recorded_pnl=-100.0),
        mk(candidate_id="x", market_key="exit", outcome="EXITED",
           natural_outcome="YES", recorded_pnl=-10.0),
        mk(candidate_id="v", market_key="void", outcome="VOIDED",
           natural_outcome="YES", recorded_pnl=0.0),
        mk(candidate_id="u", market_key="open", outcome=None,
           natural_outcome="YES", recorded_pnl=None),
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.fills.natural_settled == 2
    assert q.fills.hits == 1
    assert q.fills.hit_rate == 0.5
    assert q.scoring.markets_scored == 3 + sum(
        outcome in ledger.NATURAL_OUTCOMES for outcome in (win, loss)
    )


def test_recomputed_basis_uses_public_settlement_arithmetic():
    rows = [
        mk(candidate_id="r1", stake=250.0, fill_price=0.56, side="YES",
           outcome="YES", recorded_pnl=None, fee_category="sports"),
    ]
    q = ev.evaluate_queues(rows, pnl_basis=ev.PNL_BASIS_RECOMPUTED).queues[0]
    settlement = ledger.settle_natural(250.0, 0.56, "YES", "YES")
    assert q.roi.gross_pnl == pytest.approx(settlement.pnl, abs=1e-9)
    assert q.roi.entry_fees == pytest.approx(
        250.0 * 0.05 * 0.44, abs=1e-9
    )
    assert q.roi.net_pnl == pytest.approx(
        settlement.pnl - 250.0 * 0.05 * 0.44, abs=1e-9
    )


def test_unknown_outcome_token_is_rejected():
    with pytest.raises(ValueError, match="unknown resolution outcome"):
        ev.evaluate_queues([mk(outcome="MUTUAL")])


# ---------------------------------------------------------------------------
# All-candidate scoring, UNKNOWN coverage, dedup pairing
# ---------------------------------------------------------------------------


def test_all_rejected_zero_entry_queue_still_scores_model_vs_market():
    rows = [
        # Rejected candidates keep their direction (side) and decision
        # probabilities but carry no stake/fill/fee/pnl fields.
        mk(candidate_id=f"r{i}", accepted=False, stake=None, fill_price=None,
           fee_category=None, fill_provenance=None, recorded_pnl=None,
           outcome="YES" if i % 2 == 0 else "NO",
           market_key=f"mk{i}")
        for i in range(6)
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.counts.candidates == 6
    assert q.counts.accepted == 0
    assert q.counts.rejected == 6
    assert q.counts.entered == 0
    # No entries: trade ROI is INSUFFICIENT, never zero or fabricated.
    assert q.roi.status == ev.STATUS_INSUFFICIENT
    assert q.roi.gross_roi is None
    assert q.roi.counted == 0
    assert q.fills.natural_settled == 0
    assert q.fills.hit_rate is None
    # Scoring is not conditioned on entry: all 6 markets still compared.
    assert q.scoring.markets_total == 6
    assert q.scoring.markets_scored == 6
    assert q.scoring.model_brier is not None
    assert q.scoring.market_brier is not None


def test_missing_outcome_and_prices_report_unknown_coverage():
    rows = [
        # Has everything.
        mk(candidate_id="ok1", market_key="mk-ok", outcome="YES"),
        # Natural outcome missing -> UNKNOWN, excluded from scoring.
        mk(candidate_id="no1", market_key="mk-no-outcome", outcome=None),
        # Model probability missing.
        mk(candidate_id="nm1", market_key="mk-no-model", model_probability=None,
           outcome="NO"),
        # Market mid missing.
        mk(candidate_id="nk1", market_key="mk-no-mid", market_mid=None,
           outcome="NO"),
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.scoring.markets_total == 4
    assert q.scoring.markets_scored == 1
    assert q.scoring.missing_outcome == 1
    assert q.scoring.missing_model_probability == 1
    assert q.scoring.missing_market_mid == 1
    assert q.counts.unresolved == 1
    # No zero-filling: the three gap markets are counted, not fabricated.
    assert q.scoring.model_brier == pytest.approx(0.1225)  # 0.65 on a YES hit


def test_repeated_market_observations_dedup_for_scoring_not_for_roi():
    rows = [
        mk(candidate_id="obs1", market_key="mk-dup", decision_time=_T0,
           outcome=None, recorded_pnl=None, accepted=False, stake=None,
           fill_price=None, fee_category=None, fill_provenance=None),
        mk(candidate_id="obs2", market_key="mk-dup",
           decision_time=_T0 + timedelta(days=1)),
        mk(candidate_id="obs3", market_key="mk-dup",
           decision_time=_T0 + timedelta(days=2)),
    ]
    q = ev.evaluate_queues(rows).queues[0]
    # Market-level scoring dedups to one pair: the earliest observation
    # (obs1: model 0.65 / mid 0.50) with the market's natural outcome YES
    # (first non-None natural outcome in observation order: obs2's).
    assert q.scoring.markets_total == 1
    assert q.scoring.markets_scored == 1
    assert q.scoring.deduped_repeats == 2
    assert q.scoring.model_brier == pytest.approx(0.1225)  # (0.65-1)^2
    # ROI counts every actual entry separately (obs2, obs3, both YES hits).
    assert q.roi.counted == 2
    assert q.roi.gross_pnl == pytest.approx(200.0)


def test_natural_outcome_feeds_scoring_independently_of_trade_labels():
    """M1 final review P2-01: trade outcomes (win/loss labels, EXITED) and
    the market-level natural result are separate inputs. A settled book
    whose rows carry trade labels keeps whole-market scoring alive through
    ``natural_outcome``; without it every settled market would silently
    lose its scoring pair."""
    rows = [
        # A win-labelled trade on a YES market and an EXITED trade on a NO
        # market: both markets stay scored through natural_outcome.
        mk(candidate_id="t1", market_key="mk-t1", outcome="win",
           recorded_pnl=100.0, natural_outcome="YES"),
        mk(candidate_id="t2", market_key="mk-t2", outcome="EXITED",
           recorded_pnl=-60.70, natural_outcome="NO"),
        # A trade row without any natural result still leaves the market
        # unscored (honest UNKNOWN), and a later observation of the same
        # market can supply the natural result.
        mk(candidate_id="t3a", market_key="mk-t3", outcome="EXITED",
           recorded_pnl=-10.0, decision_time=_T0),
        mk(candidate_id="t3b", market_key="mk-t3", outcome="loss",
           recorded_pnl=-100.0, decision_time=_T0 + timedelta(days=1),
           natural_outcome="NO"),
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.scoring.markets_total == 3
    assert q.scoring.markets_scored == 3
    assert q.scoring.missing_outcome == 0
    # Trade semantics stay trade-side: win/loss labels and both exits are
    # all counted ROI rows; exits stay excluded from natural hit counts.
    assert q.roi.counted == 4
    assert q.counts.exited == 2
    assert q.fills.natural_settled == 2
    assert q.fills.hits == 1
    assert q.fills.hit_rate == 0.5


def test_natural_outcome_must_be_a_natural_resolution():
    with pytest.raises(ValueError, match="natural_outcome"):
        mk(natural_outcome="win")
    with pytest.raises(ValueError, match="natural_outcome"):
        mk(natural_outcome="VOIDED")


def test_probability_endpoints_keep_infinite_log_loss_visible():
    # A wrong certain model forecast (p=1.0 on a NO) must surface as an
    # infinite log loss, not be clipped away.
    rows = [
        mk(candidate_id="e1", model_probability=1.0, market_mid=0.5,
           outcome="NO", recorded_pnl=-100.0),
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.scoring.model_log_loss is None
    assert q.scoring.model_log_loss_status == "infinite"
    assert q.scoring.infinite_log_loss_pairs == 1
    assert q.scoring.logloss_diff_mean is None
    verdicts = {c.key: c.verdict for c in q.promotion}
    assert verdicts["D"] == ev.VERDICT_NOT_ASSESSABLE
    # Brier stays finite and honest: (1.0 - 0)^2 = 1.
    assert q.scoring.model_brier == pytest.approx(1.0)


def test_certain_correct_forecast_keeps_finite_log_loss():
    rows = [mk(candidate_id="e2", model_probability=1.0, outcome="YES")]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.scoring.model_log_loss_status == "finite"
    assert q.scoring.model_log_loss == pytest.approx(0.0, abs=1e-12)
    assert q.scoring.infinite_log_loss_pairs == 0


def test_paired_model_minus_market_interval_sign():
    # Model 0.9 / market 0.1 on YES outcomes: model clearly better.
    rows = [
        mk(candidate_id=f"p{i}", market_key=f"mkp{i}", model_probability=0.9,
           market_mid=0.1, outcome="YES", recorded_pnl=100.0)
        for i in range(10)
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.scoring.brier_diff_mean < 0
    assert q.scoring.logloss_diff_mean < 0
    low, high = q.scoring.brier_diff_ci
    # Containment up to float-conversion slack (the point mean comes from
    # the exact Decimal context; the interval from float resampling).
    assert low - 1e-9 <= q.scoring.brier_diff_mean <= high + 1e-9


def test_calibration_bins_come_from_shared_scoring_module():
    rows = [
        mk(candidate_id=f"cal{i}", market_key=f"mkc{i}",
           model_probability=0.65, outcome="YES" if i % 20 < 13 else "NO")
        for i in range(40)
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.scoring.calibration_status == "descriptive_only"
    assert q.scoring.calibration_ece is not None
    assert len(q.scoring.calibration_bins) == 10
    occupied = [b for b in q.scoring.calibration_bins if b.count]
    assert occupied
    model_bin = next(
        b for b in q.scoring.calibration_bins if b.count
    )
    assert model_bin.mean_probability_yes is not None
    assert model_bin.observed_yes_rate is not None


# ---------------------------------------------------------------------------
# CLV vs unentered market-move diagnostics (strictly separated)
# ---------------------------------------------------------------------------


def test_entry_clv_and_unentered_moves_are_separate_blocks():
    rows = [
        # Entered with close: CLV = close - fill.
        mk(candidate_id="en1", market_key="mke1", fill_price=0.50,
           close_price=0.60),
        # Entered without close: excluded, counted as uncovered.
        mk(candidate_id="en2", market_key="mke2", close_price=None),
        # Rejected candidate with a close: unentered diagnostic only.
        mk(candidate_id="re1", market_key="mkr1", accepted=False, stake=None,
           fill_price=None, side=None, fee_category=None,
           fill_provenance=None, outcome=None, recorded_pnl=None,
           market_mid=0.45, close_price=0.55),
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.clv.entries_with_close == 1
    assert q.clv.mean == pytest.approx(0.10)
    assert q.clv.status == ev.STATUS_OK
    low, high = q.clv.ci
    assert low == pytest.approx(0.10)
    assert high == pytest.approx(0.10)
    # The rejected candidate never enters CLV or ROI.
    assert q.unentered_moves.coverage == 1
    assert q.unentered_moves.mean == pytest.approx(0.10)  # 0.55 - 0.45
    assert q.roi.counted == 2


def test_clv_price_direction_follows_position_side():
    # Side NO: probabilities are side-aligned, so CLV is still close-fill.
    rows = [
        mk(candidate_id="no1", market_key="mkn1", side="NO",
           model_probability=0.7, market_mid=0.69, fill_price=0.69,
           outcome="NO", recorded_pnl=44.93, close_price=0.75),
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.clv.mean == pytest.approx(0.06)


# ---------------------------------------------------------------------------
# Concentration and drawdown
# ---------------------------------------------------------------------------


def test_top_market_share_undefined_when_total_pnl_not_positive():
    rows = [
        mk(candidate_id="l1", market_key="mkl1", outcome="NO",
           recorded_pnl=-100.0),
        mk(candidate_id="l2", market_key="mkl2", outcome="NO",
           recorded_pnl=-50.0),
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.concentration.total_pnl < 0
    assert q.concentration.top_market_share is None
    assert q.concentration.status == ev.STATUS_UNDEFINED
    assert q.roi.excluding_top_market_roi is None
    verdicts = {c.key: c.verdict for c in q.promotion}
    assert verdicts["E"] == ev.VERDICT_FAIL


def test_concentration_groups_by_market_key():
    rows = [
        mk(candidate_id="g1", market_key="same", outcome="YES",
           recorded_pnl=80.0),
        mk(candidate_id="g2", market_key="same", outcome="YES",
           recorded_pnl=40.0),
        mk(candidate_id="g3", market_key="other", outcome="YES",
           recorded_pnl=30.0),
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.concentration.top_market_key == "same"
    assert q.concentration.top_market_pnl == pytest.approx(120.0)
    assert q.concentration.top_market_share == pytest.approx(120.0 / 150.0)
    # Net-of-fees concentration: each row pays a 2.5 entry fee (stake 100
    # @ 0.50, sports), so "same" nets 115 of a 142.5 total.
    assert q.concentration.net_total_pnl == pytest.approx(142.5)
    assert q.concentration.top_market_net_pnl == pytest.approx(115.0)
    assert q.concentration.top_market_net_share == pytest.approx(115.0 / 142.5)
    assert q.concentration.top_market_net_key == "same"


def test_criterion_e_concentration_uses_net_of_fees_basis():
    """M1 final review P2-02: the OD-G E concentration verdict must use
    the net-of-fees PnL basis (plan section 5 B-condition semantics:
    every PnL is after fees), not the gross basis. The reviewer's
    four-market
    construction: stakes 150/250/250/250, entries 0.50 and 3x
    0.6817148572467722, all natural YES at the sports rate — gross
    concentration 29.9900% (below the 30% bar) but net concentration
    30.1869404845% (at/above it). Gross keeps its historical-report
    role; the promotion verdict reads the net share."""
    p = 0.6817148572467722
    rows = [
        mk(candidate_id="net1", market_key="mkt-top", stake=150.0,
           fill_price=0.50, fee_category="sports", outcome="YES",
           recorded_pnl=150.0 / 0.50 - 150.0),
    ] + [
        mk(candidate_id=f"net{i}", market_key=f"mkt-{i}", stake=250.0,
           fill_price=p, market_mid=p, fee_category="sports", outcome="YES",
           recorded_pnl=250.0 / p - 250.0)
        for i in (2, 3, 4)
    ]
    q = ev.evaluate_queues(rows).queues[0]
    # Historical gross metric is still reported (10-30 anchors unchanged).
    assert q.concentration.top_market_key == "mkt-top"
    assert q.concentration.top_market_share == pytest.approx(0.299900, abs=5e-6)
    # The promotion basis is net of fees: 30.1869404845% >= 30% -> FAIL.
    assert q.concentration.net_total_pnl == pytest.approx(
        146.25 + 3 * (250.0 / p - 250.0 - 250.0 * 0.05 * (1.0 - p)),
        rel=1e-12,
    )
    assert q.concentration.top_market_net_pnl == pytest.approx(146.25)
    assert q.concentration.top_market_net_share == pytest.approx(
        0.301869404845, abs=1e-10
    )
    verdicts = {c.key: c for c in q.promotion}
    assert verdicts["E"].verdict == ev.VERDICT_FAIL
    assert any("30.2% of total" in reason for reason in verdicts["E"].reasons)


def test_max_drawdown_on_net_pnl_series():
    # Net per row: win +97.5 (fee 2.5), loss -102.5. Sequence:
    # +97.5, -102.5 x2 => peak 97.5, trough -107.5 => drawdown 205.0.
    rows = []
    times = [
        _T0,
        _T0 + timedelta(days=1),
        _T0 + timedelta(days=2),
    ]
    outcomes = ["YES", "NO", "NO"]
    for i, (when, outcome) in enumerate(zip(times, outcomes)):
        rows.append(mk(
            candidate_id=f"d{i}", market_key=f"mkd{i}", outcome=outcome,
            recorded_pnl=100.0 if outcome == "YES" else -100.0,
            settled_time=when,
        ))
    q = ev.evaluate_queues(rows).queues[0]
    assert q.drawdown.max_drawdown == pytest.approx(205.0)
    assert q.drawdown.limit_usd == 2500.0
    assert q.drawdown.unit_usd == 250.0
    assert q.drawdown.series_rows == 3


def test_drawdown_zero_when_monotonically_increasing():
    rows = [
        mk(candidate_id="u1", market_key="mku1", settled_time=_T0),
        mk(candidate_id="u2", market_key="mku2",
           settled_time=_T0 + timedelta(days=1)),
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.drawdown.max_drawdown == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Promotion criteria A-F: every threshold at equality and both neighbours
# ---------------------------------------------------------------------------


def test_criterion_a_boundaries():
    ok = dict(candidates=200, entered=50, settled=30, span_days=28.0)
    assert ev.criterion_a(**ok).verdict == ev.VERDICT_PASS
    cases = [
        ("candidates", 199, ev.VERDICT_FAIL),
        ("candidates", 200, ev.VERDICT_PASS),
        ("candidates", 201, ev.VERDICT_PASS),
        ("entered", 49, ev.VERDICT_FAIL),
        ("entered", 50, ev.VERDICT_PASS),
        ("entered", 51, ev.VERDICT_PASS),
        ("settled", 29, ev.VERDICT_FAIL),
        ("settled", 30, ev.VERDICT_PASS),
        ("settled", 31, ev.VERDICT_PASS),
        ("span_days", 27.9, ev.VERDICT_FAIL),
        ("span_days", 28.0, ev.VERDICT_PASS),
        ("span_days", 28.1, ev.VERDICT_PASS),
    ]
    for field, value, expected in cases:
        result = ev.criterion_a(**{**ok, field: value})
        assert result.verdict == expected, (field, value)
    # Span not measurable -> NOT_ASSESSABLE, never a silent PASS.
    assert ev.criterion_a(**{**ok, "span_days": None}).verdict == (
        ev.VERDICT_NOT_ASSESSABLE
    )
    assert ev.criterion_a(**ok).reasons == ()


def test_criterion_b_boundaries_and_provenance():
    ok = dict(
        entries=50,
        book_walk_entries=50,
        entries_with_fee_data=50,
        exited_entries=5,
        exited_with_fee_data=5,
    )
    assert ev.criterion_b(**ok).verdict == ev.VERDICT_PASS
    # Any non-book-walk fill is missing provenance, not a performance FAIL.
    assert ev.criterion_b(**{**ok, "book_walk_entries": 49}).verdict == (
        ev.VERDICT_NOT_ASSESSABLE
    )
    assert ev.criterion_b(**{**ok, "entries_with_fee_data": 49}).verdict == (
        ev.VERDICT_NOT_ASSESSABLE
    )
    assert ev.criterion_b(**{**ok, "exited_with_fee_data": 4}).verdict == (
        ev.VERDICT_NOT_ASSESSABLE
    )
    assert ev.criterion_b(**{**ok, "entries": 0}).verdict == (
        ev.VERDICT_NOT_ASSESSABLE
    )


def test_criterion_c_boundaries():
    ok = dict(clv_mean=0.02, clv_lower_bound=0.01, entries_with_close=40)
    assert ev.criterion_c(**ok).verdict == ev.VERDICT_PASS
    cases = [
        ("clv_mean", -0.001, ev.VERDICT_FAIL),
        ("clv_mean", 0.0, ev.VERDICT_FAIL),  # strict > 0
        ("clv_mean", 0.001, ev.VERDICT_PASS),
        ("clv_lower_bound", -0.001, ev.VERDICT_FAIL),
        ("clv_lower_bound", 0.0, ev.VERDICT_FAIL),  # strict > 0
        ("clv_lower_bound", 0.001, ev.VERDICT_PASS),
    ]
    for field, value, expected in cases:
        assert ev.criterion_c(**{**ok, field: value}).verdict == expected, (
            field, value
        )
    # No close coverage -> NOT_ASSESSABLE (never zero-filled to PASS).
    assert ev.criterion_c(
        clv_mean=None, clv_lower_bound=None, entries_with_close=0
    ).verdict == ev.VERDICT_NOT_ASSESSABLE


def test_criterion_d_boundaries():
    ok = dict(upper_bound=0.005, pairs=35, infinite_pairs=0)
    assert ev.criterion_d(**ok).verdict == ev.VERDICT_PASS
    cases = [
        ("upper_bound", 0.0099, ev.VERDICT_PASS),
        ("upper_bound", 0.0100, ev.VERDICT_PASS),  # inclusive <= +0.010
        ("upper_bound", 0.0101, ev.VERDICT_FAIL),
        ("upper_bound", 0.5, ev.VERDICT_FAIL),
    ]
    for field, value, expected in cases:
        assert ev.criterion_d(**{**ok, field: value}).verdict == expected, (
            field, value
        )
    assert ev.criterion_d(upper_bound=None, pairs=0,
                          infinite_pairs=0).verdict == ev.VERDICT_NOT_ASSESSABLE
    # Infinite pairs hide nothing: NOT_ASSESSABLE with the count reported.
    result = ev.criterion_d(upper_bound=0.005, pairs=35, infinite_pairs=2)
    assert result.verdict == ev.VERDICT_NOT_ASSESSABLE
    assert any("infinite" in r for r in result.reasons)


def test_criterion_e_boundaries():
    ok = dict(net_roi=0.01, top_market_net_share=0.20, max_drawdown=1000.0,
              stake=5000.0)
    assert ev.criterion_e(**ok).verdict == ev.VERDICT_PASS
    cases = [
        ("net_roi", -0.001, ev.VERDICT_FAIL),
        ("net_roi", 0.0, ev.VERDICT_FAIL),  # strict > 0
        ("net_roi", 0.001, ev.VERDICT_PASS),
        ("top_market_net_share", 0.299, ev.VERDICT_PASS),
        ("top_market_net_share", 0.300, ev.VERDICT_FAIL),  # strict < 30%
        ("top_market_net_share", 0.301, ev.VERDICT_FAIL),
        ("max_drawdown", 2499.99, ev.VERDICT_PASS),
        ("max_drawdown", 2500.00, ev.VERDICT_PASS),  # inclusive <= 2500
        ("max_drawdown", 2500.01, ev.VERDICT_FAIL),
    ]
    for field, value, expected in cases:
        assert ev.criterion_e(**{**ok, field: value}).verdict == expected, (
            field, value
        )
    # Undefined share (non-positive total net pnl) reported, not zero.
    result = ev.criterion_e(net_roi=-0.05, top_market_net_share=None,
                            max_drawdown=100.0, stake=5000.0)
    assert result.verdict == ev.VERDICT_FAIL
    assert any("undefined" in r for r in result.reasons)
    # No stake -> not assessable.
    assert ev.criterion_e(net_roi=None, top_market_net_share=None,
                          max_drawdown=None,
                          stake=0.0).verdict == ev.VERDICT_NOT_ASSESSABLE


def test_criterion_f_is_blocked_in_m1():
    result = ev.criterion_f()
    assert result.verdict == ev.VERDICT_NOT_ASSESSABLE_M1
    assert result.reasons


def test_overall_verdict_ordering():
    passing = [
        ev.CriterionResult("A", ev.VERDICT_PASS, ()),
        ev.CriterionResult("E", ev.VERDICT_PASS, ()),
    ]
    assert ev.overall_verdict(passing + [ev.criterion_f()]) == (
        ev.VERDICT_NOT_ASSESSABLE_M1
    )
    with_gap = passing + [
        ev.CriterionResult("C", ev.VERDICT_NOT_ASSESSABLE, ("no closes",)),
        ev.criterion_f(),
    ]
    assert ev.overall_verdict(with_gap) == ev.VERDICT_NOT_ASSESSABLE
    failing = passing + [
        ev.CriterionResult("D", ev.VERDICT_FAIL, ("worse than market",)),
        ev.criterion_f(),
    ]
    assert ev.overall_verdict(failing) == ev.VERDICT_FAIL


# ---------------------------------------------------------------------------
# Pause / FX-17 suggestion boundaries (output only, never wired)
# ---------------------------------------------------------------------------


def test_blueprint_pause_boundaries():
    ok = dict(entered=40, clv_upper_bound=-0.01, max_drawdown=1000.0,
              settled=40, net_pnl=100.0)
    assert ev.pause_recommendation(**ok).stop_new_entries is True
    # Entry-count boundary: strict >= 40.
    assert ev.pause_recommendation(
        **{**ok, "entered": 39}
    ).stop_new_entries is False
    # CLV upper bound boundary: strict < 0.
    assert ev.pause_recommendation(
        **{**ok, "clv_upper_bound": 0.0}
    ).stop_new_entries is False
    assert ev.pause_recommendation(
        **{**ok, "clv_upper_bound": 0.001}
    ).stop_new_entries is False
    # Drawdown boundary: strict > 2500.
    assert ev.pause_recommendation(
        **{**ok, "clv_upper_bound": 0.5, "max_drawdown": 2500.00}
    ).stop_new_entries is False
    assert ev.pause_recommendation(
        **{**ok, "clv_upper_bound": 0.5, "max_drawdown": 2500.01}
    ).stop_new_entries is True
    # Unknown CLV bound alone never triggers the pause.
    assert ev.pause_recommendation(
        **{**ok, "clv_upper_bound": None}
    ).stop_new_entries is False


def test_fx17_pause_review_boundaries():
    # Net-pnl clause tested with the CLV clause neutralized (positive
    # upper bound), and vice versa.
    ok = dict(entered=25, clv_upper_bound=0.5, max_drawdown=100.0,
              settled=10, net_pnl=-600.01)
    assert ev.pause_recommendation(**ok).fx17_pause_review is True
    # Settled boundary: strict >= 10.
    assert ev.pause_recommendation(
        **{**ok, "settled": 9}
    ).fx17_pause_review is False
    # Net pnl boundary: strict < -600.
    assert ev.pause_recommendation(
        **{**ok, "net_pnl": -600.00}
    ).fx17_pause_review is False
    assert ev.pause_recommendation(
        **{**ok, "net_pnl": -599.99}
    ).fx17_pause_review is False
    # Entry boundary for the CLV clause: strict >= 20.
    clv_clause = dict(entered=20, clv_upper_bound=-0.01, max_drawdown=100.0,
                      settled=5, net_pnl=100.0)
    assert ev.pause_recommendation(**clv_clause).fx17_pause_review is True
    assert ev.pause_recommendation(
        **{**clv_clause, "entered": 19}
    ).fx17_pause_review is False
    assert ev.pause_recommendation(
        **{**clv_clause, "clv_upper_bound": 0.0}
    ).fx17_pause_review is False


def test_pause_suggestion_is_reported_not_enforced():
    # A queue past 40 entries with negative CLV upper bound gets a
    # suggestion; the report states it, nothing is wired in M1.
    rows = [
        mk(candidate_id=f"pz{i}", market_key=f"mkz{i}", fill_price=0.50,
           close_price=0.40, decision_time=_T0 + timedelta(days=i),
           settled_time=_T0 + timedelta(days=i, hours=5))
        for i in range(41)
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.counts.entered == 41
    assert q.pause.stop_new_entries is True
    assert q.pause.reasons


# ---------------------------------------------------------------------------
# Determinism and input validation
# ---------------------------------------------------------------------------


def test_evaluation_is_deterministic():
    rows = [
        mk(candidate_id=f"t{i}", market_key=f"mkt{i}",
           outcome="YES" if i % 3 else "NO",
           recorded_pnl=100.0 if i % 3 else -100.0,
           close_price=0.55)
        for i in range(30)
    ]
    first = ev.evaluate_queues(rows)
    second = ev.evaluate_queues(rows)
    assert first == second


def test_bootstrap_clusters_repeat_observations_together():
    # Two markets observed twice each: cluster assignment puts repeated
    # observations of one market in the same cluster.
    rows = [
        mk(candidate_id="m1a", market_key="M1", match_key=None,
           decision_time=_T0),
        mk(candidate_id="m1b", market_key="M1", match_key=None,
           decision_time=_T0 + timedelta(hours=1)),
        mk(candidate_id="m2a", market_key="M2", match_key=None,
           decision_time=_T0),
        mk(candidate_id="m2b", market_key="M2", match_key=None,
           decision_time=_T0 + timedelta(hours=1)),
    ]
    clusters = ev.cluster_rows(rows)
    assert len(clusters) == 2
    assert {c[0].market_key for c in clusters} == {"M1", "M2"}
    # match_key overrides market_key as the cluster.
    rows2 = [
        mk(candidate_id="s1", market_key="S1", match_key="match-1"),
        mk(candidate_id="s2", market_key="S2", match_key="match-1"),
    ]
    assert len(ev.cluster_rows(rows2)) == 1


def test_rejected_candidate_must_not_carry_an_entry():
    with pytest.raises(ValueError, match="rejected candidate"):
        ev.EvaluationCandidate(
            candidate_id="bad", model_key="m", market_type="mt",
            gate_version="g", market_key="mk", accepted=False, stake=100.0,
        )


@pytest.mark.parametrize("field,value", [
    ("model_probability", 1.3),
    ("model_probability", -0.01),
    ("market_mid", 1.5),
    ("fill_price", 0.0),
    ("fill_price", 1.0),
    ("stake", 0.0),
    ("stake", -5.0),
    ("close_price", 1.2),
])
def test_out_of_range_inputs_are_rejected(field, value):
    with pytest.raises(ValueError):
        mk(**{field: value})


def test_duplicate_candidate_ids_are_rejected():
    rows = [mk(candidate_id="dup"), mk(candidate_id="dup", market_key="mk2")]
    with pytest.raises(ValueError, match="candidate_id"):
        ev.evaluate_queues(rows)


def test_unknown_side_token_is_rejected():
    with pytest.raises(ValueError, match="unknown side"):
        mk(side="MUTUAL")


def test_natural_resolution_requires_a_side():
    with pytest.raises(ValueError, match="requires a side"):
        mk(side=None)


def test_empty_queue_keys_are_rejected():
    with pytest.raises(ValueError):
        mk(model_key="")


# ---------------------------------------------------------------------------
# Full synthetic queue wiring (criteria computed from metric blocks)
# ---------------------------------------------------------------------------


def _full_passing_queue():
    rows = []
    for i in range(210):
        day = i // 7  # 30 distinct days
        hit = (i % 20) < 13
        rows.append(mk(
            candidate_id=f"s{i}",
            market_key=f"mks{i}",
            decision_time=_T0 + timedelta(days=day),
            settled_time=_T0 + timedelta(days=day, hours=6),
            model_probability=0.65,
            market_mid=0.50,
            fill_price=0.50,
            close_price=0.60,
            outcome="YES" if hit else "NO",
            recorded_pnl=100.0 if hit else -100.0,
        ))
    return rows


def test_full_synthetic_queue_assessment_wiring():
    report = ev.evaluate_queues(_full_passing_queue())
    assert len(report.queues) == 1
    q = report.queues[0]
    assert q.counts.candidates == 210
    assert q.roi.net_roi > 0
    assert q.clv.mean == pytest.approx(0.10)
    assert q.drawdown.max_drawdown <= 2500.0
    verdicts = {c.key: c.verdict for c in q.promotion}
    assert verdicts["A"] == ev.VERDICT_PASS
    assert verdicts["B"] == ev.VERDICT_PASS
    assert verdicts["C"] == ev.VERDICT_PASS
    assert verdicts["D"] == ev.VERDICT_PASS
    assert verdicts["E"] == ev.VERDICT_PASS
    assert verdicts["F"] == ev.VERDICT_NOT_ASSESSABLE_M1
    # M1 never reports a promotion: F needs M2 run evidence.
    assert q.overall_verdict == ev.VERDICT_NOT_ASSESSABLE_M1


def test_failing_queue_propagates_fail_overall():
    rows = [
        mk(candidate_id=f"f{i}", market_key=f"mkf{i}",
           model_probability=0.5, market_mid=0.9, fill_price=0.9,
           outcome="NO", recorded_pnl=-100.0,
           decision_time=_T0 + timedelta(days=i // 7),
           settled_time=_T0 + timedelta(days=i // 7, hours=6))
        for i in range(210)
    ]
    q = ev.evaluate_queues(rows).queues[0]
    assert q.roi.net_roi < 0
    assert q.overall_verdict == ev.VERDICT_FAIL
