"""MO-6 optimize grid search: enumeration, budget guard, final selection.

Design 04 sections 8 and 11; plan 05 node MO-6 (plan r2 section 1.1).
Pure functions on top of the MO-3 kernel: standard library only, no
network, no database, no clock reads other than the caller-injected
``now_fn``.  Determinism contract: the same ``param_defs`` +
``overrides`` always enumerate the same grid in the same declared
order, and ``grid_space_text`` is the canonical identity that feeds
``store.optimization_run_key`` (a search space is re-identified by its
content, never by the writing clock).

Grid discipline (plan 05 section 7, "search spaces are versioned"):
``choice`` dimensions enumerate their closed option set exactly as
declared; ``float``/``int`` dimensions REQUIRE an explicit closed
point list in ``overrides`` — an unenumerated numeric dimension is a
planning error and fails closed here, before any model runs.  Every
override value passes ``spec.validate_param`` (booleans never satisfy
a numeric kind, bounds are inclusive).
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from polymarket_alpha_lab.papersim.optimize.spec import (
    ParamDef,
    validate_param,
)
from polymarket_alpha_lab.papersim.optimize.walkforward import (
    OneSeResult,
    Selection,
    WalkFold,
    nested_select,
    one_standard_error_rule,
)

__all__ = (
    "BudgetGuard",
    "GridPoint",
    "RunBudgetExceeded",
    "enumerate_grid",
    "final_selection",
    "grid_space_summary",
    "grid_space_text",
)


@dataclass(frozen=True)
class GridPoint:
    """One grid point: a frozen parameter mapping plus its declared-order index."""

    params: Mapping[str, object]
    indices: tuple[int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", dict(self.params))


def _dimension_values(
    defn: ParamDef, overrides: Mapping[str, Sequence[object]]
) -> tuple[object, ...]:
    """The closed point list of one dimension, declared order preserved."""
    if defn.kind == "choice":
        if defn.name in overrides:
            raise ValueError(
                f"grid dimension {defn.name!r} is a closed choice set; an "
                "override would silently re-identify the search space"
            )
        return tuple(defn.choices)
    override = overrides.get(defn.name)
    if not override:
        raise ValueError(
            f"grid dimension {defn.name!r} is numeric and needs an explicit "
            "closed point list in overrides (implicit lattices are not a "
            "declared search space)"
        )
    values = tuple(override)
    for value in values:
        validate_param(defn, value)
    return values


def enumerate_grid(
    param_defs: Sequence[ParamDef],
    *,
    overrides: Mapping[str, Sequence[object]] | None = None,
) -> tuple[GridPoint, ...]:
    """Enumerate the full grid in DECLARED order (last dimension fastest).

    For the club-soccer-recalib v1 space this reproduces the WP-P4
    order (half-life outer, team-prior inner).  Unknown override names
    fail closed: an override the space does not declare is a planning
    error, not extra search room.
    """
    resolved = dict(overrides or {})
    declared = {defn.name for defn in param_defs}
    unknown = sorted(set(resolved) - declared)
    if unknown:
        raise ValueError(f"overrides name undeclared dimensions: {unknown}")
    dimensions = [
        (defn, _dimension_values(defn, resolved)) for defn in param_defs
    ]
    if not dimensions:
        return (GridPoint({}, ()),)
    names = [defn.name for defn, _ in dimensions]
    points: list[GridPoint] = []
    for combination in _product_indices([len(values) for _, values in dimensions]):
        params: dict[str, object] = {}
        for index, (name, (_, values)) in enumerate(zip(names, dimensions)):
            params[name] = values[combination[index]]
        points.append(GridPoint(params, tuple(combination)))
    return tuple(points)


def _product_indices(sizes: Sequence[int]) -> Any:
    """itertools.product over index ranges (declared order, last fastest)."""
    from itertools import product

    return product(*(range(size) for size in sizes))


def grid_space_text(
    param_defs: Sequence[ParamDef],
    overrides: Mapping[str, Sequence[object]] | None = None,
) -> str:
    """Canonical JSON text of the search space (identity of the run key).

    Strict JSON (the store layer re-parses it into the run row and
    hashes it for the run key); sorted keys, compact separators, floats
    serialized by ``repr`` through the json module — the same values
    always map to the same text, so one space can never split into two
    identities.
    """
    import json

    resolved = dict(overrides or {})
    dimensions = {
        defn.name: list(_dimension_values(defn, resolved)) for defn in param_defs
    }
    return json.dumps(
        {"dimensions": dimensions}, sort_keys=True, separators=(",", ":")
    )


def grid_space_summary(
    param_defs: Sequence[ParamDef],
    overrides: Mapping[str, Sequence[object]] | None = None,
) -> dict:
    """Plain-Python comparison-count metadata for receipts and scorecards."""
    resolved = dict(overrides or {})
    dimensions: dict[str, tuple[object, ...]] = {}
    combinations = 1
    for defn in param_defs:
        values = _dimension_values(defn, resolved)
        dimensions[defn.name] = values
        combinations *= len(values)
    return {
        "dimensions": {name: tuple(values) for name, values in dimensions.items()},
        "combinations": combinations,
    }


class RunBudgetExceeded(Exception):
    """The run crossed its time or trial budget; carries the completed count.

    ``phase`` names the pipeline stage that was in flight (the failed
    run row records it so a budget report says WHERE the money ran
    out, not just that it ran out).
    """

    def __init__(self, message: str, *, trials: int, reason: str, phase: str = "") -> None:
        super().__init__(message)
        self.trials = trials
        self.reason = reason
        self.phase = phase


class BudgetGuard:
    """Dual time/trial budget; one charge = one (candidate x fold) scoring unit.

    Wall-clock enters only through the injected ``now_fn`` so tests can
    fast-forward deterministically; a guard with both limits ``None``
    never raises (the runner's profiles still declare explicit caps).
    The runner charges DENSELY (before and after expensive work, between
    holdout callbacks, after selection, before releasing results) and
    keeps ``phase`` current, so the monotonic deadline is re-checked at
    every stage transition, not only inside the scoring loop.
    """

    def __init__(
        self,
        *,
        max_seconds: float | None = None,
        max_trials: int | None = None,
        now_fn: Callable[[], float],
    ) -> None:
        self.max_seconds = max_seconds
        self.max_trials = max_trials
        self.now_fn = now_fn
        self.trials = 0
        self.phase = ""
        self._start = now_fn()

    def set_phase(self, phase: str) -> None:
        """Record the current pipeline stage and re-check the deadline."""

        self.phase = phase
        self.charge(0)

    def charge(self, units: int = 1) -> None:
        # Check first, count after: ``trials`` always equals the number
        # of COMPLETED units (what a failed run row reports).
        if self.max_trials is not None and self.trials + units > self.max_trials:
            raise RunBudgetExceeded(
                f"trial budget exceeded: {self.trials + units} > {self.max_trials}",
                trials=self.trials,
                reason="max_trials",
                phase=self.phase,
            )
        if self.max_seconds is not None:
            elapsed = self.now_fn() - self._start
            if elapsed > self.max_seconds:
                raise RunBudgetExceeded(
                    f"time budget exceeded: {elapsed:.3f}s > {self.max_seconds}s",
                    trials=self.trials,
                    reason="max_seconds",
                    phase=self.phase,
                )
        self.trials += units


def final_selection(
    folds: Sequence[WalkFold],
    candidates: Sequence[object],
    score: Callable[[object, WalkFold, tuple[str, ...]], Sequence[float]],
    incumbent: object,
    param_defs: Sequence[ParamDef],
    params_of: Callable[[object], dict],
    *,
    guard: BudgetGuard | None = None,
) -> tuple[Selection, OneSeResult]:
    """Final selection over ALL walk-forward folds, then the one-SE rule.

    Step 1 (the program's own evidence): every candidate is scored on
    every fold's held-out events; the per-fold means are the blocks.
    Step 2: ``walkforward.one_standard_error_rule`` shrinks toward the
    incumbent (distance, then fewer changed params, then declared
    order); a winner equal to the incumbent keeps the incumbent and
    the holdout is never touched.
    """
    if not candidates:
        raise ValueError("final_selection needs at least one candidate")
    losses_per_candidate: list[list[float]] = []
    blocks_per_candidate: list[list[list[float]]] = []
    for candidate in candidates:
        all_losses: list[float] = []
        block_losses: list[list[float]] = []
        for fold in folds:
            if guard is not None:
                guard.charge()
            losses = list(score(candidate, fold, fold.test_keys))
            if losses:
                block_losses.append(losses)
            all_losses.extend(losses)
        if not all_losses:
            raise ValueError(
                "final_selection scored zero events; the walk-forward region "
                "is empty (check the fold split and event filter)"
            )
        losses_per_candidate.append(all_losses)
        blocks_per_candidate.append(block_losses)
    scores = [statistics.fmean(losses) for losses in losses_per_candidate]
    one_se = one_standard_error_rule(
        candidates,
        scores,
        blocks_per_candidate,
        incumbent,
        param_defs,
        params_of,
    )
    selection = Selection(
        winner=one_se.winner,
        status=one_se.status,
        scores=tuple(
            (candidate, score_value, len(losses))
            for candidate, score_value, losses in zip(
                candidates, scores, losses_per_candidate
            )
        ),
        inner_block_ids=tuple(fold.block_id for fold in folds),
    )
    return selection, one_se


def select_per_fold(
    outer_folds: Sequence[WalkFold],
    inner_folds: Sequence[WalkFold],
    candidates: Sequence[object],
    score: Callable[[object, WalkFold, tuple[str, ...]], Sequence[float]],
    fallback: object,
    *,
    guard: BudgetGuard | None = None,
    min_inner: int = 2,
) -> tuple[Selection, ...]:
    """Nested selection for each outer fold (thin driver over nested_select).

    The qualification rule (inner cutoff precedes the outer cutoff and
    every inner key lies in the outer training set) stays inside
    ``walkforward.nested_select`` — one implementation, no second copy.
    """
    selections = []
    for fold in outer_folds:
        if guard is not None:
            guard.charge()
        selections.append(
            nested_select(
                fold,
                inner_folds,
                candidates,
                score,
                fallback=fallback,
                min_inner=min_inner,
            )
        )
    return tuple(selections)
