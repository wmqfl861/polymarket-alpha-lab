"""MO-3 optimize scorecard tests.

Covers the scorecard structure on binary and multi-class events, the
deterministic canonical serialization (byte-identical reruns, input-order
insensitivity), the unified validation gates A-E with caller-supplied
thresholds (including the reflection pin that this module hides no
numeric defaults), and the single-source pin: this module never computes
OD-G verdicts and its binary paired numbers come from the same Decimal
arithmetic as the evaluation module (that equality itself is tested in
tests/test_papersim_optimize_metrics.py).
"""
from __future__ import annotations

import inspect
import json
import math

import pytest

from polymarket_alpha_lab.papersim.optimize import scorecard as sc
from polymarket_alpha_lab.papersim.optimize.metrics import ScoredEvent
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec

SPEC = ModelSpec(
    family="club-soccer-frozen",
    procedure_version="frozen-2026-11-19",
    params={},
    envelope=({"variant": "median-10"},),
    data_contract="understat-shape-v1",
    legacy_model_version="soccer-r23",
)


def _binary_event(key: str, p_yes: float, hit: bool, line: str = "ml"):
    return ScoredEvent(
        event_key=key,
        line_key=line,
        cluster_key=("match_key", key),
        probabilities=(1.0 - p_yes, p_yes),
        outcome_index=1 if hit else 0,
    )


def _three_way_event(key: str, probs: tuple[float, float, float], index: int):
    return ScoredEvent(
        event_key=key,
        line_key="1x2",
        cluster_key=("match_key", key),
        probabilities=probs,
        outcome_index=index,
    )


def _binary_batch(n: int = 35):
    events = []
    for i in range(n):
        p_yes = 0.30 + 0.02 * (i % 20)
        hit = (i % 3 != 0)
        events.append(_binary_event(f"m{i:02d}", p_yes, hit))
    return events


def _incumbent_batch(events):
    return [
        _binary_event(row.event_key, min(max(row.probabilities[1] - 0.05, 0.01), 0.99), row.outcome_index == 1)
        for row in events
    ]


# ---------------------------------------------------------------------------
# Scorecard structure
# ---------------------------------------------------------------------------


def test_build_scorecard_binary_events():
    events = _binary_batch()
    incumbent = _incumbent_batch(events)
    market = [
        _binary_event(row.event_key, 0.5, row.outcome_index == 1) for row in events
    ]
    gates = sc.gate_results(
        (
            sc.gate_abc_stat("A", "xg_coverage", 0.97, threshold=0.95, direction="gte"),
        ),
        sc.gate_d_walkforward(
            {"brier": 0.19, "log_loss": 0.55},
            {"brier": 0.25, "log_loss": 0.69},
            n_events=events.__len__(),
            min_events=30,
        ),
        sc.gate_e_market(
            [row.probabilities for row in events],
            [row.probabilities for row in market],
            mad_max=0.06,
            min_events=30,
        ),
    )
    card = sc.build_scorecard(
        SPEC, events,
        incumbent_events=incumbent,
        market_events=market,
        variant_spread_median=0.041,
        refusals=2,
        gates=gates,
    )
    assert card.family_id == "club-soccer-frozen"
    assert card.spec_sha256 == SPEC.spec_sha256()
    assert card.model_version == "soccer-r23"
    assert card.n_events == 35
    assert card.sample_status == sc.STATUS_DESCRIPTIVE
    expected_ll = sum(
        -math.log(max(1e-12, row.probabilities[row.outcome_index])) for row in events
    ) / 35
    assert card.log_loss == pytest.approx(expected_ll)
    expected_brier = sum(
        (row.probabilities[0] - (1.0 if row.outcome_index == 0 else 0.0)) ** 2
        + (row.probabilities[1] - (1.0 if row.outcome_index == 1 else 0.0)) ** 2
        for row in events
    ) / 35
    assert card.brier == pytest.approx(expected_brier)
    assert card.argmax_hits == sum(
        1
        for row in events
        if row.outcome_index == (1 if row.probabilities[1] >= 0.5 else 0)
    )
    assert card.calibration_slope is not None
    assert card.paired_vs_incumbent is not None
    assert card.paired_vs_incumbent.n_pairs == 35
    expected_diff = sum(
        -math.log(max(1e-12, row.probabilities[row.outcome_index]))
        + math.log(
            max(1e-12, inc.probabilities[inc.outcome_index])
        )
        for row, inc in zip(events, incumbent)
    ) / 35
    assert card.paired_vs_incumbent.mean == pytest.approx(expected_diff)
    assert 0.0 < card.paired_vs_incumbent.p_value <= 1.0
    assert card.paired_vs_incumbent.ci95[0] <= card.paired_vs_incumbent.ci95[1]
    assert card.variant_spread_median == 0.041
    assert card.refusals == 2
    assert [gate.gate for gate in card.gates] == ["A", "D", "E"]


def test_build_scorecard_multiclass_and_mixed_arity_rejected():
    events = [
        _three_way_event(f"m{i:02d}", (0.2 + 0.02 * (i % 10), 0.3, 0.5 - 0.02 * (i % 10)), i % 3)
        for i in range(35)
    ]
    card = sc.build_scorecard(SPEC, events)
    assert card.calibration_ece is not None
    assert card.calibration_slope is None  # binary-only by definition
    assert card.log_loss is not None
    with pytest.raises(ValueError):
        sc.build_scorecard(SPEC, events + [_binary_event("x", 0.5, True)])


def test_insufficient_sample_status():
    card = sc.build_scorecard(SPEC, _binary_batch(10))
    assert card.sample_status == sc.STATUS_INSUFFICIENT
    assert card.n_events == 10


# ---------------------------------------------------------------------------
# Canonical serialization
# ---------------------------------------------------------------------------


def test_canonical_dict_deterministic_and_order_insensitive():
    events = _binary_batch()
    incumbent = _incumbent_batch(events)
    shuffled = list(reversed(events))
    first = sc.build_scorecard(
        SPEC, events, incumbent_events=incumbent
    ).canonical_dict()
    second = sc.build_scorecard(
        SPEC, shuffled, incumbent_events=list(reversed(incumbent))
    ).canonical_dict()
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    rerun = sc.build_scorecard(
        SPEC, events, incumbent_events=incumbent
    ).canonical_dict()
    assert json.dumps(rerun, sort_keys=True) == json.dumps(first, sort_keys=True)
    assert first["model_version"] == "soccer-r23"
    assert first["gates"] == []


# ---------------------------------------------------------------------------
# Validation gates A-E
# ---------------------------------------------------------------------------


def test_gate_abc_stat_verdicts():
    ok = sc.gate_abc_stat("B", "xg_dev", 0.038, threshold=0.08, direction="lte")
    assert ok.verdict == sc.VERDICT_PASS
    bad = sc.gate_abc_stat("B", "xg_dev", 0.09, threshold=0.08, direction="lte")
    assert bad.verdict == sc.VERDICT_FAIL
    corr = sc.gate_abc_stat("C", "corr", 0.86, threshold=0.7, direction="gte")
    assert corr.verdict == sc.VERDICT_PASS
    missing = sc.gate_abc_stat("C", "corr", None, threshold=0.7, direction="gte")
    assert missing.verdict == sc.VERDICT_NOT_ASSESSABLE
    small = sc.gate_abc_stat(
        "C", "corr", 0.9, threshold=0.5, direction="gte", denominator=12.0,
        min_denominator=30.0,
    )
    assert small.verdict == sc.VERDICT_NOT_ASSESSABLE
    with pytest.raises(ValueError):
        sc.gate_abc_stat("A", "x", 1.0, threshold=1.0, direction="eq")


def test_gate_d_walkforward_verdicts():
    model = {"brier": 0.16, "log_loss": 0.50}
    baseline = {"brier": 0.25, "log_loss": 0.69}
    assert sc.gate_d_walkforward(
        model, baseline, n_events=90, min_events=90
    ).verdict == sc.VERDICT_PASS
    assert sc.gate_d_walkforward(
        model, baseline, n_events=89, min_events=90
    ).verdict == sc.VERDICT_NOT_ASSESSABLE
    assert sc.gate_d_walkforward(
        {"brier": 0.16, "log_loss": 0.70}, baseline, n_events=90, min_events=90
    ).verdict == sc.VERDICT_FAIL
    assert sc.gate_d_walkforward(
        {"brier": None, "log_loss": 0.5}, baseline, n_events=90, min_events=90
    ).verdict == sc.VERDICT_NOT_ASSESSABLE


def test_gate_e_market_verdicts():
    model = [[0.55, 0.45]] * 8
    market = [[0.60, 0.40]] * 8
    assert sc.gate_e_market(
        model, market, mad_max=0.06, min_events=8
    ).verdict == sc.VERDICT_PASS
    flip_market = [[0.40, 0.60]] * 8
    result = sc.gate_e_market(model, flip_market, mad_max=0.06, min_events=8)
    assert result.verdict == sc.VERDICT_FAIL  # flips everywhere
    assert sc.gate_e_market(
        model, market, mad_max=0.04, min_events=8
    ).verdict == sc.VERDICT_FAIL  # MAD 0.05 > 0.04
    assert sc.gate_e_market(
        model, market, mad_max=0.06, min_events=9
    ).verdict == sc.VERDICT_NOT_ASSESSABLE


def test_gate_results_orders_a_to_e():
    gates = sc.gate_results(
        (
            sc.gate_abc_stat("C", "c", 1.0, threshold=0.5, direction="gte"),
            sc.gate_abc_stat("A", "a", 1.0, threshold=0.5, direction="gte"),
        ),
        sc.gate_d_walkforward(
            {"brier": 0.1}, {"brier": 0.2}, n_events=90, min_events=90,
            metrics=("brier",),
        ),
        sc.gate_e_market([[0.5]], [[0.5]], mad_max=0.1, min_events=1),
    )
    assert [gate.gate for gate in gates] == ["A", "C", "D", "E"]


def test_module_hides_no_numeric_threshold_defaults():
    # The scorecard module owns no copy of any registered number: every
    # gate THRESHOLD is a keyword-only parameter WITHOUT a default (the
    # OD-G numbers live only in papersim/evaluation.py).
    thresholds = {
        sc.gate_abc_stat: ("threshold",),
        sc.gate_d_walkforward: ("min_events",),
        sc.gate_e_market: ("mad_max", "min_events"),
    }
    for function, names in thresholds.items():
        parameters = inspect.signature(function).parameters
        for name in names:
            assert parameters[name].default is inspect.Parameter.empty, name
            assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY, name
