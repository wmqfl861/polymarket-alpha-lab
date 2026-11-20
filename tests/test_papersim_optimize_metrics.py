"""MO-3 optimize metrics kernel tests.

Covers the review-pinned conventions: the four log-loss floor conventions,
the two interval functions (production forward vs study reproduction with
its asymmetric index formulas), the sign-flip permutation test with its
+1 correction and 1e-15 tolerance, Holm step-down with stable ordering,
calibration diagnostics, the Decimal production paired path pinned exactly
against the evaluation module, and the three numeric anchors:

1. the 30 new-soccer cells' Brier (holdout_predictions.csv vs
   evidence/recompute_new_soccer_output.txt);
2. the NBA per-group log losses (predictions.csv vs
   evidence/recompute_nba_output.txt);
3. the WP-P4 360-fixture paired comparison (server-only evidence JSON,
   skipped when absent; SHA-256 pinned).
"""
from __future__ import annotations

import csv
import errno
import hashlib
import json
import math
import random
import statistics
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_alpha_lab.papersim import evaluation as ev
from polymarket_alpha_lab.papersim.optimize import metrics as mx
from polymarket_alpha_lab.research_probability_scores import (
    BinaryResearchObservation,
    ResearchProbabilityDiagnostics,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "handoffs" / "2026-11-19-audit-optimizer" / "evidence"
RESEARCH = ROOT / "docs" / "paper-sim" / "research" / "league-admission-20261119"
NEW_SOCCER_CSV = RESEARCH / "new-soccer" / "holdout_predictions.csv"
NEW_SOCCER_OUT = EVIDENCE / "recompute_new_soccer_output.txt"
NBA_CSV = RESEARCH / "nba" / "predictions.csv"
NBA_OUT = EVIDENCE / "recompute_nba_output.txt"
WP_P4_JSON = Path(
    "/home/ubuntu/paper-sim/review/wp-p4-evidence/wp-p4-walkforward-2026-11-07.json"
)
WP_P4_SHA256 = "51d7da0e6dd09176b8c6141ba1fd99dc7e63e0995ea4a354f6b5fc1c41ce7899"


def _wp_p4_evidence_usable() -> bool:
    """Check for WP-P4 evidence during collection.

    CI accounts may lack permission to traverse the server-only parent path.
    Treat that case as non-availability and surface other probe failures.
    """
    try:
        return WP_P4_JSON.exists()
    except PermissionError:
        return False


# ---------------------------------------------------------------------------
# Scalar scores and floor conventions
# ---------------------------------------------------------------------------


def test_scalar_scores_hand_computed():
    assert mx.binary_brier(0.8, True) == pytest.approx(0.04000000000000002)
    assert mx.binary_brier(0.8, False) == pytest.approx(0.6400000000000001)
    # Sum-form multi-class Brier: 0.2^2 + (0.5-1)^2 + 0.3^2 = 0.38.
    assert mx.multiclass_brier((0.2, 0.5, 0.3), 1) == pytest.approx(0.38)
    assert mx.multiclass_log_loss((0.2, 0.5, 0.3), 1, floor=1e-12) == pytest.approx(-math.log(0.5))


def test_logloss_floor_conventions():
    # (1) production: Decimal, no floor -- pinned via the shared module's
    #     infinite status rather than a number.
    diag = ResearchProbabilityDiagnostics(
        (BinaryResearchObservation(Decimal("1.0"), False),)
    )
    assert diag.log_loss_status == "infinite"
    assert diag.infinite_log_loss_count == 1
    # (2) WP-P4: single-sided 1e-12 on the realized probability.
    assert mx.binary_log_loss(0.0, True, floor=1e-12, clamp="realized") == pytest.approx(-math.log(1e-12))
    assert mx.binary_log_loss(1.0, False, floor=1e-12, clamp="realized") == pytest.approx(-math.log(1e-12))
    # (3) new-soccer: single-sided 1e-15 on the realized probability.
    assert mx.multiclass_log_loss((0.0, 1.0, 0.0), 0, floor=1e-15) == pytest.approx(-math.log(1e-15))
    assert mx.multiclass_log_loss((0.5, 0.5, 0.0), 2, floor=1e-15) == pytest.approx(-math.log(1e-15))
    # (4) NBA: two-sided clamp BEFORE selecting the realized side; the
    # expected values re-state the evidence expression verbatim
    # (recompute_nba.logloss) so float rounding cannot drift.
    def nba_logloss(p: float, y: int) -> float:
        p = min(max(p, 1e-15), 1 - 1e-15)
        return -math.log(p if y == 1 else 1 - p)

    assert mx.binary_log_loss(0.0, True, floor=1e-15, clamp="both") == nba_logloss(0.0, 1)
    assert mx.binary_log_loss(0.0, False, floor=1e-15, clamp="both") == nba_logloss(0.0, 0)
    assert mx.binary_log_loss(1.0, False, floor=1e-15, clamp="both") == nba_logloss(1.0, 0)
    assert mx.binary_log_loss(1.0, True, floor=1e-15, clamp="both") == nba_logloss(1.0, 1)
    # Single-sided does NOT protect the unrealized side.
    assert mx.binary_log_loss(1.0, False, floor=1e-15, clamp="realized") == pytest.approx(-math.log(1e-15))
    with pytest.raises(ValueError):
        mx.binary_log_loss(0.5, True, clamp="nonsense")


def test_mean_of_is_builtin_sum_over_len():
    # Python 3.12's built-in sum() uses compensated summation for floats,
    # exactly like the evidence recompute scripts -- that is the point.
    assert mx.mean_of([0.1, 0.2, 0.3]) == sum([0.1, 0.2, 0.3]) / 3
    assert mx.mean_of([0.1, 0.2, 0.3]) == pytest.approx(0.2)
    assert mx.mean_of([0.5]) == 0.5
    with pytest.raises(ValueError):
        mx.mean_of([])


# ---------------------------------------------------------------------------
# Pairing and clusters
# ---------------------------------------------------------------------------


def _event(key: str, line: str, cluster: str, probs: tuple[float, ...], index: int):
    return mx.ScoredEvent(
        event_key=key, line_key=line,
        cluster_key=("match_key", cluster), probabilities=probs, outcome_index=index,
    )


def test_paired_diffs_matching_and_unpaired_counts():
    left = [
        _event("m1", "1x2", "m1", (0.2, 0.5, 0.3), 1),
        _event("m2", "ou2.5", "m2", (0.4, 0.6), 1),
        _event("m3", "1x2", "m3", (0.6, 0.2, 0.2), 0),
    ]
    right = [
        _event("m1", "1x2", "m1", (0.3, 0.4, 0.3), 1),
        _event("m4", "1x2", "m4", (0.3, 0.3, 0.4), 2),
        _event("m3", "1x2", "m3", (0.5, 0.3, 0.2), 0),
    ]
    diff = mx.paired_diffs(
        left, right,
        lambda a, b: -math.log(max(1e-12, a.probabilities[a.outcome_index]))
        + math.log(max(1e-12, b.probabilities[b.outcome_index])),
    )
    assert diff.unpaired_left == 1
    assert diff.unpaired_right == 1
    assert [key for key, _ in diff.pairs] == [("match_key", "m1"), ("match_key", "m3")]


def test_group_clusters_first_appearance_order():
    pairs = [(("k", "b"), 1.0), (("k", "a"), 2.0), (("k", "b"), 3.0)]
    assert mx.group_clusters(pairs) == [[1.0, 3.0], [2.0]]


# ---------------------------------------------------------------------------
# Intervals: production forward vs study reproduction
# ---------------------------------------------------------------------------


def test_production_ci_forwards_evaluation_exactly():
    rng = random.Random(31)
    clusters = [[rng.uniform(-0.2, 0.2) for _ in range(3)] for _ in range(7)]
    assert mx.cluster_mean_ci(clusters) == ev._bootstrap_mean_ci(clusters)
    assert mx.cluster_mean_ci([]) == (None, None)
    ratio_clusters = [[(0.1, 0.4), (-0.2, 0.6)], [(0.3, 0.9)]]
    assert mx.cluster_ratio_ci(ratio_clusters) == ev._bootstrap_ratio_ci(ratio_clusters)


def test_study_block_interval_index_shape_is_pinned():
    # Reproduce the draw sequence by hand (same seed, same order) and pin
    # the ASYMMETRIC index pair: lo = draws[49], hi = draws[1949] at
    # R=2000 (int(.975 * 1999) = int(1949.025) = 1949; the review note's
    # "1948" was an arithmetic slip -- the evidence code decides).
    rng = random.Random(77)
    blocks = {
        f"lg:{i:03d}:round-{i % 9}": [rng.uniform(-0.5, 0.5) for _ in range(1 + i % 3)]
        for i in range(40)
    }
    result = mx.study_block_interval(blocks, seed=20261107, reps=2000)
    mirror = random.Random(20261107)
    group = [blocks[key] for key in sorted(blocks)]
    draws = []
    for _ in range(2000):
        selected = [group[mirror.randrange(len(group))] for _ in group]
        draws.append(
            sum(sum(block) for block in selected)
            / sum(len(block) for block in selected)
        )
    draws.sort()
    assert result.mean == statistics.fmean(
        [value for block in blocks.values() for value in block]
    )
    assert result.ci95[0] == draws[int(0.025 * 1999)] == draws[49]
    assert result.ci95[1] == draws[int(0.975 * 1999)] == draws[1949]
    # The asymmetry is observable on this data: neighbours differ.
    assert draws[49] != draws[50]
    assert draws[1949] != draws[1948]
    assert result.n_items == sum(len(block) for block in blocks.values())
    assert result.n_blocks == 40
    with pytest.raises(ValueError):
        mx.study_block_interval({"only": [1.0]})
    with pytest.raises(ValueError):
        mx.study_block_interval({"a": [1.0], "b": [2.0]}, reps=50)


# ---------------------------------------------------------------------------
# Sign-flip test and Holm correction
# ---------------------------------------------------------------------------


def test_sign_flip_pvalue_bounds_and_tolerance():
    # All-positive block sums: every flip stays far from zero except the
    # all-negative one; p stays above the (0+1)/(R+1) floor.
    sums = [0.4, 0.6, 0.8, 1.2]
    p = mx.sign_flip_pvalue(sums, seed=20261119, reps=2000)
    assert p > (0 + 1) / 2001
    assert p <= 1.0
    # Perfectly symmetric pair: observed statistic is zero, so every flip
    # is extreme and p == 1.0.
    assert mx.sign_flip_pvalue([1.5, -1.5], seed=1, reps=500) == 1.0
    # Tolerance: a flip that lands exactly 1e-16 short still counts.
    assert mx.sign_flip_pvalue([1.0, -1.0 + 1e-16], seed=2, reps=200) == 1.0


def test_holm_step_down_stable_order():
    rejected, adjusted = mx.holm([0.04, 0.01, 0.03])
    # Sorted: 0.01 (thr .0167), 0.03 (thr .025 -> stop), 0.04.
    assert rejected == (False, True, False)
    assert adjusted[1] == pytest.approx(0.03)
    assert adjusted[0] == pytest.approx(0.06)
    assert adjusted[2] == pytest.approx(0.06)
    # Stability: equal p-values keep original order in rejection walking.
    rejected, _ = mx.holm([0.02, 0.02, 0.02])
    # thresholds: .0167 (fail) -> stop immediately: nothing rejected.
    assert rejected == (False, False, False)
    rejected, _ = mx.holm([0.01, 0.01, 0.5])
    # thresholds: .0167, .025 -> both small ones rejected, third stops.
    assert rejected == (True, True, False)
    # All far above alpha: nothing rejected; adjusted capped at 1.
    rejected, adjusted = mx.holm([0.9, 0.8])
    assert rejected == (False, False)
    assert adjusted == (1.0, 1.0)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def test_calibration_diagnostics_and_slope_intercept():
    # Deterministic cohorts: for each reported p, exactly k * p YES
    # observations and k * (1 - p) NO observations (k = 10) -- the
    # fitted slope of a calibrated reporter is ~1, intercept ~0.
    calibrated = []
    for step in range(1, 20):  # p = 0.05 .. 0.95
        p = step / 20
        yes = round(10 * p)
        calibrated.extend([(p, True)] * yes)
        calibrated.extend([(p, False)] * (10 - yes))
    slope, intercept = mx.calibration_slope_intercept(calibrated)
    assert abs(slope - 1.0) < 0.1
    assert abs(intercept) < 0.1
    # Overconfident: reported probabilities are stretched away from 0.5
    # by factor 1.5 relative to the true rates -> fitted slope < 1.
    overconfident = []
    for step in range(1, 20):
        p = step / 20
        reported = min(max(0.5 + 1.5 * (p - 0.5), 0.01), 0.99)
        yes = round(10 * p)
        overconfident.extend([(reported, True)] * yes)
        overconfident.extend([(reported, False)] * (10 - yes))
    slope_over, _ = mx.calibration_slope_intercept(overconfident)
    assert slope_over < 0.85
    diagnostics = mx.binary_calibration(
        [
            (Decimal(str(p)), y)
            for p, y in [(0.7, True), (0.7, False), (0.3, False), (0.3, False)]
        ],
        bucket_count=2,
        min_sample_count=1,
        min_bin_count=1,
    )
    assert diagnostics.sample_count == 4
    assert diagnostics.mean_brier_score is not None
    events = [
        _event(f"m{i}", "1x2", f"m{i}", (0.25, 0.5, 0.25), 1) for i in range(4)
    ]
    assert mx.one_vs_rest_calibration(events, 1).sample_count == 4


def test_mad_and_flips():
    assert mx.mean_absolute_deviation([0.6, 0.4], [0.5, 0.5]) == pytest.approx(0.1)
    assert mx.favorite_flips([[0.6, 0.4]], [[0.45, 0.55]]) == 1
    assert mx.favorite_flips([[0.6, 0.4]], [[0.7, 0.3]]) == 0


# ---------------------------------------------------------------------------
# Decimal production paired path vs evaluation module (exact)
# ---------------------------------------------------------------------------


def _candidate(cid: str, market: str, model: float, mid: float, outcome: str):
    return ev.EvaluationCandidate(
        candidate_id=cid,
        model_key="soccer-v1",
        market_type="ou_over",
        gate_version="gates-v1",
        market_key=market,
        model_probability=model,
        market_mid=mid,
        side="YES",
        outcome=outcome,
    )


def test_production_binary_paired_matches_evaluation_market_scoring_exactly():
    candidates = [
        _candidate("c1", "mk1", 0.65, 0.50, "YES"),
        _candidate("c2", "mk1", 0.65, 0.50, "YES"),  # repeat observation
        _candidate("c3", "mk2", 0.40, 0.55, "NO"),
        _candidate("c4", "mk3", 0.30, 0.20, "NO"),
    ]
    scoring = ev._market_scoring(candidates)
    paired = mx.production_binary_paired(
        model_probabilities=[0.65, 0.40, 0.30],
        comparator_probabilities=[0.50, 0.55, 0.20],
        realized=[True, False, False],
        cluster_keys=[("market_key", "mk1"), ("market_key", "mk2"), ("market_key", "mk3")],
    )
    assert paired.brier_mean == scoring.brier_diff_mean
    assert paired.log_mean == scoring.logloss_diff_mean
    assert paired.brier_ci == scoring.brier_diff_ci
    assert paired.log_ci == scoring.logloss_diff_ci
    assert paired.infinite_pairs == scoring.infinite_log_loss_pairs
    assert paired.n_pairs == scoring.markets_scored


def test_production_binary_paired_infinite_is_never_averaged():
    paired = mx.production_binary_paired(
        model_probabilities=[1.0, 0.5],
        comparator_probabilities=[0.5, 0.5],
        realized=[False, True],
        cluster_keys=[("market_key", "a"), ("market_key", "b")],
    )
    assert paired.infinite_pairs == 1
    assert paired.log_mean is None
    assert paired.log_ci == (None, None)
    assert paired.brier_mean is not None


# ---------------------------------------------------------------------------
# Anchor 1: the 30 new-soccer cells' Brier
# ---------------------------------------------------------------------------


def _parse_new_soccer_output() -> dict[tuple[str, str], tuple[int, int, float, float, float]]:
    expected = {}
    for line in NEW_SOCCER_OUT.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "n_match" in line or "|" not in line:
            continue
        head, brier_segment = line.split("|")[0], line.split("|")[1]
        league, market, n_all, n_match = head.split()
        b0, b1, b2 = (float(part) for part in brier_segment.split("/"))
        expected[(league, market)] = (int(n_all), int(n_match), b0, b1, b2)
    return expected


def test_anchor1_new_soccer_30_cells_brier():
    expected = _parse_new_soccer_output()
    assert len(expected) == 30
    with NEW_SOCCER_CSV.open(newline="", encoding="utf-8") as handle:
        cells = mx.new_soccer_scores(csv.DictReader(handle))
    assert set(cells) == set(expected)
    for key, (n_all, n_match, b0, b1, b2) in expected.items():
        cell = cells[key]
        assert cell.n_all == n_all, key
        assert cell.n_match == n_match, key
        assert round(cell.brier_baseline, 6) == b0, key
        assert round(cell.brier_tuned, 6) == b1, key
        assert round(cell.brier_market, 6) == b2, key


def test_anchor1_reproduces_byte_identically_on_rerun():
    with NEW_SOCCER_CSV.open(newline="", encoding="utf-8") as handle:
        first = mx.new_soccer_scores(csv.DictReader(handle))
    with NEW_SOCCER_CSV.open(newline="", encoding="utf-8") as handle:
        second = mx.new_soccer_scores(csv.DictReader(handle))

    def plain(cells):
        return {
            str(key): [float(value) for value in (
                cell.n_all, cell.n_match, cell.brier_baseline,
                cell.brier_tuned, cell.brier_market, cell.logloss_baseline,
                cell.logloss_tuned, cell.logloss_market,
            )]
            for key, cell in cells.items()
        }

    assert json.dumps(plain(first), sort_keys=True) == json.dumps(
        plain(second), sort_keys=True
    )


# ---------------------------------------------------------------------------
# Anchor 2: the NBA per-group log losses
# ---------------------------------------------------------------------------


def _parse_nba_output() -> dict[tuple[str, str, str, str], dict]:
    expected = {}
    for line in NBA_OUT.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "paired |" in line or "|" not in line:
            continue
        segments = line.split("|")
        season, split, market, source, rows, paired_n = segments[0].split()
        key = (season, split, market, source)
        if paired_n == "0":
            only = float(segments[1].split()[-2])
            expected[key] = {
                "rows": int(rows), "paired": 0, "baseline_only_ll": only,
                "ll": None, "brier": None,
            }
        else:
            ll = [float(part) for part in segments[1].split("/")]
            brier = [float(part) for part in segments[2].split("/")]
            expected[key] = {
                "rows": int(rows), "paired": int(paired_n),
                "ll": ll, "brier": brier, "baseline_only_ll": None,
            }
    return expected


def test_anchor2_nba_group_logloss():
    expected = _parse_nba_output()
    assert len(expected) == 31
    with NBA_CSV.open(newline="", encoding="utf-8") as handle:
        groups = mx.nba_group_scores(csv.DictReader(handle))
    assert set(groups) == set(expected)
    for key, want in expected.items():
        group = groups[key]
        assert group.rows == want["rows"], key
        assert group.paired == want["paired"], key
        if want["paired"]:
            assert round(group.logloss_baseline, 4) == want["ll"][0], key
            assert round(group.logloss_optimized, 4) == want["ll"][1], key
            assert round(group.logloss_market, 4) == want["ll"][2], key
            assert round(group.brier_baseline, 4) == want["brier"][0], key
            assert round(group.brier_optimized, 4) == want["brier"][1], key
            assert round(group.brier_market, 4) == want["brier"][2], key
        else:
            assert round(group.baseline_only_logloss, 4) == want["baseline_only_ll"], key


# ---------------------------------------------------------------------------
# Anchor 3: WP-P4 360-fixture paired comparison (server-only input)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _wp_p4_evidence_usable(), reason="WP-P4 evidence JSON is server-only"
)
def test_anchor3_wp_p4_paired_comparison():
    digest = hashlib.sha256(WP_P4_JSON.read_bytes()).hexdigest()
    assert digest == WP_P4_SHA256
    rows = json.loads(WP_P4_JSON.read_text(encoding="utf-8"))["fixture_predictions"]

    def outcome_of(row) -> int:
        if row["home_goals"] > row["away_goals"]:
            return 0
        if row["home_goals"] == row["away_goals"]:
            return 1
        return 2

    def row_event(row, name: str) -> mx.ScoredEvent:
        forecasts = row["forecasts"][name]
        return mx.ScoredEvent(
            event_key=row["match_id"],
            line_key="1x2",
            cluster_key=("match_key", row["block_id"]),
            probabilities=tuple(forecasts["scoring_1x2"]),
            outcome_index=outcome_of(row),
        )

    paired_rows = [
        row for row in rows
        if "production10" in row["forecasts"] and "recalibrated" in row["forecasts"]
    ]
    candidate = [row_event(row, "recalibrated") for row in paired_rows]
    incumbent = [row_event(row, "production10") for row in paired_rows]
    assert len(candidate) == 360

    def loss(event: mx.ScoredEvent) -> float:
        return mx.multiclass_log_loss(
            event.probabilities, event.outcome_index, floor=1e-12
        )

    # The kernel recomputes the recorded per-fixture losses exactly.
    for event, row in zip(candidate, paired_rows):
        assert loss(event) == row["forecasts"]["recalibrated"]["logloss_1x2"]
    for event, row in zip(incumbent, paired_rows):
        assert loss(event) == row["forecasts"]["production10"]["logloss_1x2"]
    # Published means on the paired 360-fixture subset.
    assert round(statistics.fmean(loss(e) for e in candidate), 6) == 1.011281
    assert round(statistics.fmean(loss(e) for e in incumbent), 6) == 1.009471
    # Paired difference and the WP-P4 study interval, end to end.
    diff = mx.paired_diffs(candidate, incumbent, lambda a, b: loss(a) - loss(b))
    blocks: dict[str, list[float]] = {}
    for (_kind, block_id), value in diff.pairs:
        blocks.setdefault(block_id, []).append(value)
    result = mx.study_block_interval(blocks, seed=20261107, reps=2000)
    assert result.n_items == 360
    assert result.n_blocks == 36
    assert round(result.mean, 6) == 0.001811
    assert round(result.ci95[0], 6) == -0.007597
    assert round(result.ci95[1], 6) == 0.010589


def test_wp_p4_probe_and_read_fault_policy(tmp_path, monkeypatch):
    """Exercise probe and read results independently of server permissions."""
    module = sys.modules[__name__]
    real_server_path = str(WP_P4_JSON)
    path_type = type(WP_P4_JSON)

    class _DeniedTraversal(path_type):
        def exists(self):
            raise PermissionError(errno.EACCES, "simulated non-traversable parent")

    class _FaultyProbe(path_type):
        def exists(self):
            raise OSError(errno.EIO, "simulated unrelated probe fault")

    class _LyingUsable(path_type):
        def exists(self):
            return True

        def read_bytes(self):
            raise OSError(errno.EIO, "simulated read fault after positive probe")

    readable = tmp_path / "readable.json"
    readable.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(module, "WP_P4_JSON", tmp_path / "absent.json")
    assert _wp_p4_evidence_usable() is False
    monkeypatch.setattr(module, "WP_P4_JSON", _DeniedTraversal(real_server_path))
    assert _wp_p4_evidence_usable() is False
    monkeypatch.setattr(module, "WP_P4_JSON", _FaultyProbe(real_server_path))
    with pytest.raises(OSError) as probe_fault:
        _wp_p4_evidence_usable()
    assert probe_fault.value.errno == errno.EIO
    monkeypatch.setattr(module, "WP_P4_JSON", readable)
    assert _wp_p4_evidence_usable() is True
    monkeypatch.setattr(module, "WP_P4_JSON", _LyingUsable(real_server_path))
    assert _wp_p4_evidence_usable() is True
    with pytest.raises(OSError) as read_fault:
        test_anchor3_wp_p4_paired_comparison()
    assert read_fault.value.errno == errno.EIO
