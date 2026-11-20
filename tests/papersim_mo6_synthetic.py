"""Shared synthetic fixtures for the MO-6 runner tests (not a test module).

The synthetic "coin" family walks the REAL runner core end to end —
the same nested selection, one-SE shrink, holdout paired comparison,
three-condition gate, Holm and registration decision as the real
families — so the rule tests (false-positive rate, detection rate)
exercise the production decision path, never a re-implementation.

Deterministic per seed: ``make_payload`` encodes the realized outcomes
in the payload BYTES, so the adapter stays pure and the whole run is
reproducible from the input digest alone.

Shape (pinned by tests/test_papersim_optimize_runner_rules.py):
- theta grid {0.30..0.70} step 0.10, incumbent 0.50; the effect world
  q = 0.60 gives the true best model a log-loss edge of
  0.6931 - 0.6730 = 0.0201 (~"truly better by 0.02").
- 100 matchday blocks, each ONE event carrying 20 binary market rows
  (the real-family shape: one match, many lines): walk-forward blocks
  0..39 (800 rows), holdout blocks 40..99 (60 events / 1200 rows —
  the club-class floor is 300 rows / 20 blocks).  Event-level
  clustering keeps the frozen 20,000-rep cluster bootstrap at 60
  clusters, which is what makes 200 seeds affordable; the power
  arithmetic is unchanged (rows drive it).
- The family id stays "synthetic-coin"; the run's family_id argument
  may be set to a registered family id (e.g. club-soccer-recalib) in
  tests that need the club-class stage-1 floors to apply.
"""

from __future__ import annotations

import datetime as dt
import json
import random

from polymarket_alpha_lab.papersim.optimize.runner import (
    EventRows,
    FamilyRunAdapter,
    RegisteredInput,
    RowOutcome,
    RowScore,
    RunEvent,
    RunProfile,
)
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec, ParamDef
from polymarket_alpha_lab.papersim.optimize.walkforward import expanding_matchday_folds

FAMILY = "synthetic-coin"
PROCEDURE_VERSION = "synthetic-v1"
DATA_CONTRACT = "synthetic-v1"
THETA_GRID = (0.30, 0.40, 0.50, 0.60, 0.70)
INCUMBENT_THETA = 0.50
BLOCKS = 100
HOLDOUT_START_BLOCK = 40
ROWS_PER_EVENT = 20
BASE_DATE = dt.date(2026, 1, 1)

#: The club-class stage-1 row of the registered table (300 rows /
#: 20 blocks) — the class the synthetic floors reference in tests.
CLUB_CLASS_MINIMUMS = {"min_events": 300, "min_blocks": 20}


def make_payload(*, q: float, seed: int, q2: float | None = None) -> bytes:
    """Deterministic realized outcomes for both coin targets."""
    rng = random.Random(seed)
    blocks = []
    for _ in range(BLOCKS):
        block = []
        for _ in range(ROWS_PER_EVENT):
            yes = 1 if rng.random() < q else 0
            yes2 = 1 if rng.random() < (q2 if q2 is not None else q) else 0
            block.append([yes, yes2])
        blocks.append(block)
    return json.dumps(
        {"q": q, "seed": seed, "blocks": blocks}, sort_keys=True
    ).encode("utf-8")


def make_input(*, q: float, seed: int, q2: float | None = None) -> RegisteredInput:
    return RegisteredInput(
        relpath="synthetic-payload.json",
        kind="synthetic_payload",
        content=make_payload(q=q, seed=seed, q2=q2),
    )


def make_profile(**overrides) -> RunProfile:
    """The synthetic run profile; holdout starts at block 40's date."""
    values = {
        "family_id": "synthetic-coin",
        "fold_kind": "matchday",
        "holdout_start_utc": dt.datetime.combine(
            BASE_DATE + dt.timedelta(days=HOLDOUT_START_BLOCK),
            dt.time(),
            dt.timezone.utc,
        ),
        "grid_overrides": {"theta": THETA_GRID},
        "max_seconds": None,
        "max_trials": None,
    }
    values.update(overrides)
    return RunProfile(**values)


def theta_spec(theta: float) -> ModelSpec:
    return ModelSpec(
        family=FAMILY,
        procedure_version=PROCEDURE_VERSION,
        params={"theta": theta},
        envelope=(),
        data_contract=DATA_CONTRACT,
    )


def _line_key(index: int) -> str:
    return f"yes-{index:02d}"


class CoinAdapter(FamilyRunAdapter):
    """The synthetic coin family (targets configurable for branch tests)."""

    family_id = FAMILY
    memoize = False

    def __init__(self, targets=("coin",), *, spreads=None, bad_probs=False):
        self._targets = tuple(targets)
        # spreads: optional {theta: variant_spread} map for the 0.8x
        # gate tests; absent -> empty-envelope semantics (spread None).
        self._spreads = spreads
        self._bad_probs = bad_probs
        self.rows_calls = 0

    def param_defs(self):
        return (ParamDef("theta", "float", INCUMBENT_THETA, lower=0.05, upper=0.95),)

    def targets(self):
        return self._targets

    def spec_for(self, params):
        return theta_spec(float(params["theta"]))

    def load(self, inputs):
        primary = [item for item in inputs if item.kind == "synthetic_payload"]
        if len(primary) != 1:
            raise ValueError("exactly one synthetic_payload input is required")
        return json.loads(primary[0].content.decode("utf-8"))

    def events(self, payload):
        events = []
        for block_index, block in enumerate(payload["blocks"]):
            date = BASE_DATE + dt.timedelta(days=block_index)
            kickoff = dt.datetime.combine(date, dt.time(), dt.timezone.utc)
            group = f"b{block_index:03d}"
            outcomes = tuple(
                RowOutcome("coin", _line_key(index), yes)
                for index, (yes, _yes2) in enumerate(block)
            ) + tuple(
                RowOutcome("coin2", _line_key(index), yes2)
                for index, (_yes, yes2) in enumerate(block)
            )
            events.append(
                RunEvent(
                    event_key=f"{group}-match",
                    group_id=group,
                    kickoff=kickoff,
                    available_at=kickoff,
                    outcomes=outcomes,
                )
            )
        return tuple(events)

    def folds(self, payload):
        events = self.events(payload)
        folds, skipped = expanding_matchday_folds(events)
        return (
            folds,
            tuple(
                {"block_id": item.block_id, "reason": item.reason} for item in skipped
            ),
            tuple(None for _ in folds),
        )

    def history_for(self, payload, token):
        return payload

    def rows(self, payload, history, history_key, spec, target, event, *,
             include_variants):
        self.rows_calls += 1
        theta = float(spec.params["theta"])
        if self._bad_probs and abs(theta - 0.60) < 1e-12:
            return EventRows(
                event.event_key, None,
                (RowScore(_line_key(0), (0.3, 0.6), None),),  # sums to 0.9
            )
        spread = None
        if self._spreads is not None:
            spread = self._spreads.get(theta)
        # The row count follows the event's DECLARED lines, so a shaped
        # payload (fewer/more rows per match) needs no adapter change.
        count = len(event.outcomes_for(target))
        return EventRows(
            event.event_key, None,
            tuple(
                RowScore(_line_key(index), (1.0 - theta, theta), spread)
                for index in range(count)
            ),
        )


def make_shaped_input(*, q: float, seed: int, wf_blocks: int, holdout_blocks: int,
                      rows_per_event: int) -> RegisteredInput:
    """A shaped payload: block i carries ONE event of ``rows_per_event``
    binary rows (a match with that many market lines).  Blocks
    0..wf_blocks-1 are the walk-forward region, the rest holdout."""
    rng = random.Random(seed)
    blocks = []
    for _ in range(wf_blocks + holdout_blocks):
        block = []
        for _ in range(rows_per_event):
            yes = 1 if rng.random() < q else 0
            yes2 = 1 if rng.random() < q else 0
            block.append([yes, yes2])
        blocks.append(block)
    content = json.dumps({"q": q, "seed": seed, "blocks": blocks},
                         sort_keys=True).encode("utf-8")
    return RegisteredInput("synthetic-payload.json", "synthetic_payload", content)


def make_shaped_profile(*, wf_blocks: int) -> RunProfile:
    return make_profile(
        holdout_start_utc=dt.datetime.combine(
            BASE_DATE + dt.timedelta(days=wf_blocks), dt.time(), dt.timezone.utc
        )
    )


def run_shaped(*, q: float, seed: int, wf_blocks: int, holdout_blocks: int,
               rows_per_event: int, family_id: str = FAMILY):
    """Drive the core over a shaped coin layout (P1-2 style tests)."""
    from polymarket_alpha_lab.papersim.optimize import runner

    adapter = CoinAdapter()
    profile = make_shaped_profile(wf_blocks=wf_blocks)
    registry, incumbent = make_registry(adapter, family_id=family_id)
    incumbents = {
        f"{family_id}/{target}": incumbent for target in adapter.targets()
    }
    result = runner.run_optimize_core(
        family_id=family_id,
        adapter=adapter,
        inputs=(make_shaped_input(
            q=q, seed=seed, wf_blocks=wf_blocks,
            holdout_blocks=holdout_blocks, rows_per_event=rows_per_event,
        ),),
        profile=profile,
        as_of=dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc),
        seed=seed,
        incumbents=incumbents,
        registry=registry,
        code_commit="t" * 40,
    )
    return result


def make_registry(adapter: CoinAdapter, *, initialized: bool = True,
                  family_id: str = FAMILY):
    """A registry view over the adapter's targets (all or none)."""
    from polymarket_alpha_lab.papersim.optimize.runner import (
        LineageState,
        RegistryView,
    )

    incumbent = theta_spec(INCUMBENT_THETA)
    lineages = {}
    for target in adapter.targets():
        key = f"{family_id}/{target}"
        lineages[key] = (
            LineageState(True, incumbent.spec_sha256(), ()) if initialized else None
        )
    return RegistryView(readable=True, lineages=lineages), incumbent


def run_coin(*, adapter=None, q=0.60, q2=None, seed=1, family_id=FAMILY,
             registry=None, incumbent=None, recent_runs=(), budget=None,
             profile=None):
    """Drive run_optimize_core over the synthetic family (test shorthand)."""
    from polymarket_alpha_lab.papersim.optimize import runner

    adapter = adapter or CoinAdapter()
    profile = profile or make_profile()
    if registry is None or incumbent is None:
        registry, incumbent = make_registry(adapter, family_id=family_id)
    incumbents = {
        f"{family_id}/{target}": incumbent for target in adapter.targets()
    }
    return runner.run_optimize_core(
        family_id=family_id,
        adapter=adapter,
        inputs=(make_input(q=q, q2=q2, seed=seed),),
        profile=profile,
        as_of=dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
        seed=seed,
        incumbents=incumbents,
        registry=registry,
        recent_runs=recent_runs,
        code_commit="t" * 40,
        budget=budget,
    ), adapter
