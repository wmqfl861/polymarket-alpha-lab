"""MO-6 stage-1 false-positive stress: 200 seeds per condition (plan A3).

Registered acceptance (05 node MO-6, lines 112-113): on equal-quality
synthetic data the stage-1 rule registers a candidate for at most 5%
of 200 distinct seeds (<= 10/200); on truly-better-by-0.02 synthetic
data it registers for at least 80% (>= 160/200). Seeds and ACTUAL
counts are recorded and asserted -- never estimated, never skipped,
never reduced. The synthetic data runs through the FULL stage-1
pipeline: grid search -> per-fold nested selection -> one-SE final
selection -> holdout paired CI rule with the section 80.7 floors of a
registered floor class (crypto: 150 events / 20 blocks) applied
verbatim (160 holdout events in 20 blocks).

Both conditions share one generative model: per-event losses
``0.6 + N(0, sigma)`` per parameter value; the better condition
subtracts exactly 0.02 from the ``theta=0.4`` losses on BOTH the
search and the holdout periods, so the selection and the holdout rule
see the same true effect. The incumbent is the baseline ``theta=0.2``
and sits ON the grid, so the one-SE shrinkage can (and under equal
quality usually does) land back on the incumbent, leaving the holdout
untouched -- exactly the registered anti-overfitting behaviour.
"""
from __future__ import annotations

import datetime as dt
import math
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor

from polymarket_alpha_lab.papersim.optimize.registry import LineageRoles
from polymarket_alpha_lab.papersim.optimize.runner import (
    CANDIDATE_REGISTERED,
    FixtureLine,
    FixtureRecord,
    HoldoutEvaluation,
    PolicyContext,
    Prediction,
    RegistryComparison,
    NO_IMPROVEMENT,
    execute_optimization_run,
)
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec, ParamDef

UTC = dt.timezone.utc
CODE_COMMIT = "a" * 40
DATA_CUTOFF = dt.datetime(2026, 11, 20, 6, 0, tzinfo=UTC)

#: The 200 distinct seeds (recorded; every one runs in both conditions).
STRESS_SEEDS: tuple[int, ...] = tuple(range(2026112000, 2026112200))

#: Registered thresholds (05:113): <= 5% of 200, >= 80% of 200.
MAX_EQUAL_REGISTRATIONS = 10
MIN_BETTER_REGISTRATIONS = 160

#: The truly-better effect size, exactly as registered ("确实好 0.02").
BETTER_EFFECT = 0.02

#: Per-side loss noise; sigma*sqrt(2) ~ 0.0113 per paired difference.
SIGMA = 0.008

#: Observed results, recorded for the report (exact counts, durations).
OBSERVED: dict[str, object] = {}


class StressFamily:
    family_id = "stress-synth"
    procedure_version = "stress-v1"

    def param_space(self):
        return (ParamDef("theta", "choice", 0.2, choices=(0.1, 0.2, 0.3, 0.4)),)

    def baseline_spec(self) -> ModelSpec:
        return ModelSpec(
            family=self.family_id,
            procedure_version=self.procedure_version,
            params={"theta": 0.2},
            envelope=(),
            data_contract="stress-v1",
        )

    def targets(self):
        return ("synthetic",)

    def fit(self, history, spec, as_of):  # pragma: no cover -- seam stub
        return None

    def predict(self, fitted, fixture):  # pragma: no cover -- seam stub
        return None


def Record(event_key, group_id, kickoff):
    return FixtureRecord(event_key, group_id, kickoff, kickoff + dt.timedelta(hours=3))


def _harness(seed: int, effect: float):
    """Build the full-pipeline inputs for one seed (deterministic)."""
    family = StressFamily()
    thetas = (0.1, 0.2, 0.3, 0.4)
    rng = random.Random(seed)
    records: list[Record] = []
    search_keys: list[str] = []
    holdout_keys: list[str] = []
    loss: dict[tuple[float, str], float] = {}
    day0 = dt.datetime(2026, 8, 3, 18, 0, tzinfo=UTC)
    for block in range(8):  # search period: 8 blocks x 4 events
        group = f"S{block:02d}"
        kickoff = day0 + dt.timedelta(days=7 * block)
        for event in range(4):
            key = f"s{block:02d}e{event}"
            records.append(Record(key, group, kickoff))
            search_keys.append(key)
            for theta in thetas:
                loss[(theta, key)] = max(
                    1e-6,
                    0.6 + rng.gauss(0.0, SIGMA)
                    - (effect if theta == 0.4 else 0.0),
                )
    holdout_day0 = day0 + dt.timedelta(days=56)
    for block in range(20):  # holdout: 20 blocks x 8 events = 160
        group = f"H{block:02d}"
        kickoff = holdout_day0 + dt.timedelta(days=block)
        for event in range(8):
            key = f"h{block:02d}e{event}"
            records.append(Record(key, group, kickoff))
            holdout_keys.append(key)
            for theta in thetas:
                loss[(theta, key)] = max(
                    1e-6,
                    0.6 + rng.gauss(0.0, SIGMA)
                    - (effect if theta == 0.4 else 0.0),
                )
    fixture_lines = tuple(
        FixtureLine(record.event_key, "synthetic", "line", record.event_key,
                    ("no", "yes"), 1, "binary") for record in records
    )

    def events(spec, keys):
        theta = spec.params["theta"]
        rows = []
        for key in keys:
            probability = math.exp(-loss[(theta, key)])
            rows.append(Prediction(key, "line", (1.0 - probability, probability),
                                   ("no", "yes")))
        return tuple(rows)

    def evaluate(spec, fold, targets):
        return {target: HoldoutEvaluation(events(spec, fold.test_keys)) for target in targets}

    def evaluate_holdout(spec, target, folds):
        return HoldoutEvaluation(events(spec, tuple(key for fold in folds for key in fold.test_keys)))

    active = family.baseline_spec()
    digest = active.spec_sha256()
    lineage = "stress-synth/synthetic"
    policy = PolicyContext(
        dt.datetime(2026, 11, 20, 7, 0, tzinfo=UTC),
        {lineage: RegistryComparison(LineageRoles(lineage, digest, digest), active)},
    )
    holdout_group_ids = frozenset(
        record.group_id for record in records
        if record.group_id.startswith("H")
    )

    def run():
        return execute_optimization_run(
            family=family,
            records=records,
            fixture_lines=fixture_lines,
            holdout_group_ids=holdout_group_ids,
            evaluate=evaluate,
            evaluate_holdout=evaluate_holdout,
            data_cutoff=DATA_CUTOFF,
            random_seed=seed,
            code_commit=CODE_COMMIT,
            policy_context=policy,
            deadline=lambda: False,
            floor_class="crypto",  # section 80.7 floors, verbatim
            dataset_summary={"origin": "synthetic-stress"},
        )

    return run


def _evaluate_seed(case: tuple[int, float]) -> tuple[int, str]:
    seed, effect = case
    return seed, _harness(seed, effect)().outcome


def _run_condition(effect: float) -> tuple[int, list[tuple[int, str]]]:
    # All seeds run exactly once. Parallel processes keep frozen resampling
    # over distinct game clusters practical on Linux CI and development hosts.
    workers = min(8, len(os.sched_getaffinity(0)))
    with ProcessPoolExecutor(max_workers=workers) as processes:
        outcomes = list(processes.map(_evaluate_seed, ((seed, effect) for seed in STRESS_SEEDS)))
    return sum(outcome == CANDIDATE_REGISTERED for _, outcome in outcomes), outcomes


def test_equal_quality_registration_rate_at_most_5_percent():
    started = time.monotonic()
    registered, outcomes = _run_condition(0.0)
    duration = time.monotonic() - started
    OBSERVED["equal"] = {
        "registered": registered,
        "n_seeds": len(outcomes),
        "duration_seconds": round(duration, 3),
    }
    # every one of the 200 distinct seeds ran, exactly once, no skips
    assert len(outcomes) == 200
    assert len({seed for seed, _ in outcomes}) == 200
    assert all(outcome in (CANDIDATE_REGISTERED, NO_IMPROVEMENT) for _, outcome in outcomes)
    # the registered threshold: <= 5% of 200
    assert registered <= MAX_EQUAL_REGISTRATIONS, (
        f"equal-quality registration rate too high: {registered}/200 "
        f"(threshold {MAX_EQUAL_REGISTRATIONS}); outcomes={outcomes}"
    )


def test_truly_better_by_0_02_registration_rate_at_least_80_percent():
    started = time.monotonic()
    registered, outcomes = _run_condition(BETTER_EFFECT)
    duration = time.monotonic() - started
    OBSERVED["better"] = {
        "registered": registered,
        "n_seeds": len(outcomes),
        "duration_seconds": round(duration, 3),
    }
    assert len(outcomes) == 200
    assert len({seed for seed, _ in outcomes}) == 200
    assert all(outcome in (CANDIDATE_REGISTERED, NO_IMPROVEMENT) for _, outcome in outcomes)
    # the registered threshold: >= 80% of 200
    assert registered >= MIN_BETTER_REGISTRATIONS, (
        f"truly-better-by-{BETTER_EFFECT} registration rate too low: "
        f"{registered}/200 (threshold {MIN_BETTER_REGISTRATIONS}); "
        f"outcomes={outcomes}"
    )
