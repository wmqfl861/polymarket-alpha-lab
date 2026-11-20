"""Exact-equality pin of the MO-3 public bootstrap extraction.

Plan 05 node MO-3 (review-approved option A): the public cluster-bootstrap
functions must reproduce the frozen private helpers exactly — ``==``, never
``pytest.approx`` — for empty input, a single cluster, many clusters and
the ratio helper's zero-stake branch. The frozen constants are pinned so
the extraction cannot silently retune anything.
"""
from __future__ import annotations

import random

from polymarket_alpha_lab.papersim import evaluation as ev


def _float_clusters(seed: int, n_blocks: int, n_rows: int) -> list[list[float]]:
    rng = random.Random(seed)
    return [[rng.uniform(-1.0, 1.0) for _ in range(n_rows)] for _ in range(n_blocks)]


def test_frozen_bootstrap_constants_are_pinned():
    assert ev.BOOTSTRAP_SEED == 20261114
    assert ev.BOOTSTRAP_RESAMPLES == 20000
    assert ev.BOOTSTRAP_PERCENTILES == (2.5, 97.5)
    assert ev.ALGORITHM_VERSION == "fx11-evaluation-alg-v1"
    assert ev.CLUSTER_PRIORITY == ("match_key", "market_key")


def test_public_mean_ci_equals_private_exactly():
    cases: list[list[list[float]]] = [
        [],
        [[0.5]],
        [[0.25, 0.75]],
        _float_clusters(11, 5, 3),
        _float_clusters(12, 23, 1),
    ]
    for clusters in cases:
        public = ev.cluster_bootstrap_mean_ci(clusters)
        private = ev._bootstrap_mean_ci(clusters)
        assert public == private
        assert type(public) is type(private)


def test_public_ratio_ci_equals_private_exactly():
    zero_stake = [[(0.3, 0.0), (-0.2, 0.0)]]  # zero-stake branch: draw 0.0
    cases: list[list[list[tuple[float, float]]]] = [
        [],
        [[(1.0, 2.0)]],
        [[(0.5, 1.0), (-0.25, 2.0)], [(1.5, 3.0)]],
        zero_stake,
        zero_stake + [[(0.4, 0.5)]],
    ]
    for clusters in cases:
        public = ev.cluster_bootstrap_ratio_ci(clusters)
        private = ev._bootstrap_ratio_ci(clusters)
        assert public == private


def test_public_mean_ci_is_deterministic_and_ordered():
    clusters = _float_clusters(21, 8, 4)
    first = ev.cluster_bootstrap_mean_ci(clusters)
    assert first == ev.cluster_bootstrap_mean_ci(clusters)
    assert first[0] is not None and first[1] is not None
    assert first[0] <= first[1]
