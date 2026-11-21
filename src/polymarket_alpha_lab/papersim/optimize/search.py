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
import datetime as dt
import itertools
import math
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any
from polymarket_alpha_lab.papersim.optimize.spec import (
    ModelSpec,
    ParamDef,
    canonical_value,
    validate_param,
)
from polymarket_alpha_lab.papersim.optimize.walkforward import (
    CANDIDATE_SELECTED,
    NO_BETTER_CANDIDATE,
    OneSeResult,
    SKIP_PREFIX_INCOMPLETE,
    SKIP_TARGET_INCOMPLETE,
    Selection,
    SkippedBlock,
    TimedRecord,
    WalkFold,
    nested_select,
    one_standard_error_rule,
)
from collections import Counter
from hashlib import sha256
from types import MappingProxyType
from polymarket_alpha_lab.papersim.optimize.metrics import ScoredEvent, multiclass_log_loss

__all__ = (
    "BudgetGuard",
    "GridPoint",
    "RunBudgetExceeded",
    "enumerate_grid",
    "final_selection",
    "grid_space_summary",
    "grid_space_text",
    "CausalCohort",
    "CommonBlock",
    "CommonSelection",
    "Forecast",
    "ForecastBatch",
    "INSUFFICIENT_COMPARABLE_INNER_FOLDS",
    "INSUFFICIENT_COMPARABLE_SEARCH_FOLDS",
    "LineKey",
    "PlannedTrial",
    "ScopedSearchResult",
    "ScopedTrialRecord",
    "SearchBudget",
    "SearchCatalog",
    "SearchResult",
    "SourceDay",
    "TRIAL_ABORTED",
    "TRIAL_COMPLETED",
    "TRIAL_NO_EVENTS",
    "TrialRecord",
    "as_utc_instant",
    "build_catalog",
    "dimension_values",
    "enumerate_parameters",
    "holdout_prefix_folds",
    "nested_fold_ids",
    "normalize_targets",
    "plan_evaluations",
    "run_scoped_trials",
    "run_trials",
    "select_candidate",
    "select_common",
    "selected_prefix_folds",
    "validate_cohort",
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


#: Trial evaluated with at least one scored event.
TRIAL_COMPLETED = "completed"
#: Trial evaluated but the fold contributed no scored events.
TRIAL_NO_EVENTS = "no_test_events"
TRIAL_ABORTED = "aborted"

INSUFFICIENT_COMPARABLE_INNER_FOLDS = "insufficient_comparable_inner_folds"
INSUFFICIENT_COMPARABLE_SEARCH_FOLDS = "insufficient_comparable_search_folds"

_ScoreFn = Callable[[Mapping[str, object], WalkFold, tuple[str, ...]], Sequence[float]]


def dimension_values(
    definition: ParamDef, *, continuous_points: int | None = None
) -> tuple[object, ...]:
    """The enumerated values of ONE dimension, in deterministic order.

    Explicit choices keep their declared order. A fixed numeric bound
    contributes its one value. Other bounded numeric dimensions use
    ``continuous_points`` (at least two), including the endpoints and
    the declared default. Integer dimensions sample the integer lattice
    between the inclusive bounds.
    """
    if definition.choices:
        for value in definition.choices:
            validate_param(definition, value)
        return tuple(definition.choices)
    if definition.lower is None or definition.upper is None:
        raise ValueError(
            f"dimension {definition.name!r}: a numeric dimension without "
            "choices needs both bounds for deterministic enumeration"
        )
    lower, upper = definition.lower, definition.upper
    if not math.isfinite(lower) or not math.isfinite(upper) or upper < lower:
        raise ValueError(f"dimension {definition.name!r}: invalid numeric bounds")
    validate_param(definition, definition.default)
    if definition.kind == "int":
        lower, upper = math.ceil(lower), math.floor(upper)
    if lower == upper:
        return (definition.default,)
    if type(continuous_points) is not int or continuous_points < 2:
        raise ValueError(
            f"dimension {definition.name!r}: continuous_points >= 2 is "
            "required for bounded numeric dimensions"
        )
    step = (upper - lower) / (continuous_points - 1)
    values = [lower + index * step for index in range(continuous_points)]
    values[-1] = upper
    if definition.kind == "int":
        values = [round(value) for value in values]
    return tuple(sorted(set([definition.default, *values])))


def enumerate_parameters(
    definitions: Sequence[ParamDef],
    *,
    continuous_points: int | None = None,
) -> tuple[dict[str, object], ...]:
    """The full candidate grid in declared dimension order.

    Fold-major ``itertools.product`` over the per-dimension value
    tuples: the FIRST declared dimension varies slowest. Each candidate
    mapping is built with keys inserted in declared order. The result
    is a pure function of ``definitions`` (and, for bounded numeric
    dimensions, the runner-owned ``continuous_points``); shuffling any
    caller-side mapping cannot change it.
    """
    if not definitions:
        return ({},)
    names = [definition.name for definition in definitions]
    if len(set(names)) != len(names):
        raise ValueError("parameter definitions must have unique names")
    per_dimension = [
        dimension_values(definition, continuous_points=continuous_points)
        for definition in definitions
    ]
    candidates: list[dict[str, object]] = []
    for combination in itertools.product(*per_dimension):
        candidates.append(dict(zip(names, combination, strict=True)))
    return tuple(candidates)


def normalize_targets(
    declared: Sequence[str], requested: Iterable[str] | None = None
) -> tuple[str, ...]:
    """Freeze a nonempty scope in family declaration order."""
    if isinstance(declared, (str, bytes)) or not declared or any(
        not isinstance(target, str) or not target.strip() for target in declared
    ):
        raise ValueError("family must declare at least one nonempty optimization target")
    if len(set(declared)) != len(declared):
        raise ValueError("family declares duplicate targets")
    if requested is None:
        return tuple(declared)
    if isinstance(requested, (str, bytes)):
        raise ValueError("requested targets must be a collection of target identifiers")
    scope = tuple(requested)
    if not scope or any(not isinstance(target, str) or not target.strip() for target in scope):
        raise ValueError("requested targets must be nonempty identifiers")
    wanted = set(scope)
    if len(wanted) != len(scope):
        raise ValueError("requested targets contain duplicates")
    if wanted.difference(declared):
        raise ValueError(f"unknown requested targets: {sorted(wanted.difference(declared))}")
    return tuple(target for target in declared if target in wanted)


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    spec: ModelSpec
    spec_sha256: str
    automatic: bool


@dataclass(frozen=True, slots=True)
class SearchCatalog:
    incumbent: ModelSpec
    incumbent_sha256: str
    anchor: Mapping[str, object] | None
    strategy: str
    entries: tuple[CatalogEntry, ...]

    @property
    def specs(self) -> tuple[ModelSpec, ...]:
        return tuple(entry.spec for entry in self.entries)


def build_catalog(
    incumbent: ModelSpec,
    definitions: Sequence[ParamDef],
    build_spec: Callable[[Mapping[str, object]], ModelSpec],
    *,
    continuous_points: int | None = None,
    one_coordinate: bool = False,
) -> SearchCatalog:
    """Materialize the automatic catalogue and its exact manual comparator.

    The one-coordinate catalogue uses a legal frozen default centre when
    any registered axis of the incumbent is off its automatic support.
    Settings outside the tunable axes retain the incumbent's values.
    """
    names = tuple(definition.name for definition in definitions)
    if len(set(names)) != len(names):
        raise ValueError("parameter definitions must have unique names")
    if set(names).difference(incumbent.params):
        raise ValueError("incumbent spec is missing a registered parameter")
    axes = tuple(
        dimension_values(definition, continuous_points=continuous_points)
        for definition in definitions
    )
    params = dict(incumbent.params)
    incumbent_digest = incumbent.spec_sha256()
    incumbent_values = canonical_value(params)
    contract = (incumbent.family, incumbent.procedure_version, incumbent.data_contract)
    entries: dict[str, CatalogEntry] = {}
    anchor = None

    if one_coordinate:
        entries[incumbent_digest] = CatalogEntry(incumbent, incumbent_digest, False)
        supported = all(
            canonical_value(params[name]) in {canonical_value(value) for value in choices}
            for name, choices in zip(names, axes, strict=True)
        )
        centre = dict(params)
        if not supported:
            for definition, choices in zip(definitions, axes, strict=True):
                if canonical_value(definition.default) not in {
                    canonical_value(value) for value in choices
                }:
                    raise ValueError(f"default centre is outside the support of {definition.name!r}")
                centre[definition.name] = definition.default
        anchor = MappingProxyType(centre)
        changes = (
            {**centre, name: value}
            for name, choices in zip(names, axes, strict=True)
            for value in choices
            if canonical_value(value) != canonical_value(centre[name])
        )
        points = itertools.chain((centre,), changes)
        strategy = "incumbent_centered_one_coordinate"
    else:
        points = (
            {**params, **dict(zip(names, combination, strict=True))}
            for combination in itertools.product(*axes)
        )
        strategy = "declared_grid"

    has_alternatives = any(len(choices) > 1 for choices in axes)
    for point in points:
        point_values = canonical_value(point)
        at_incumbent = point_values == incumbent_values
        if point == params:
            entries.setdefault(incumbent_digest, CatalogEntry(incumbent, incumbent_digest, False))
        spec = incumbent if at_incumbent and not has_alternatives else build_spec(point)
        if (
            not isinstance(spec, ModelSpec)
            or (spec.family, spec.procedure_version, spec.data_contract) != contract
            or canonical_value(spec.params) != point_values
        ):
            raise ValueError("candidate builder returned a spec outside the run's contract")
        digest = spec.spec_sha256()
        if digest == incumbent_digest:
            spec = incumbent
        entries[digest] = CatalogEntry(spec, digest, True)
    entries.setdefault(incumbent_digest, CatalogEntry(incumbent, incumbent_digest, False))
    return SearchCatalog(incumbent, incumbent_digest, anchor, strategy, tuple(entries.values()))


@dataclass(frozen=True, slots=True)
class SourceDay:
    """Named source calendar dates and separately observed delayed information."""

    calendar: str
    prediction_date: dt.date
    label_date: dt.date
    observed_label_at: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class _CohortGroup:
    group_id: str
    prediction: dt.datetime | dt.date
    last_label: dt.datetime | dt.date
    keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CausalCohort:
    search_records: tuple[TimedRecord, ...]
    holdout_records: tuple[TimedRecord, ...]
    search_group_ids: tuple[str, ...]
    holdout_group_ids: tuple[str, ...]
    first_holdout_prediction: dt.datetime | dt.date
    source_calendar: str | None
    last_complete_date: dt.date | None
    source_timezone: dt.tzinfo | None = field(repr=False)
    _groups: tuple[_CohortGroup, ...] = field(repr=False)
    _complete_groups: frozenset[str] = field(repr=False)


def as_utc_instant(value: object, field: str) -> dt.datetime:
    """Return the UTC instant of an aware, representable local time."""
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be an aware datetime")
    try:
        offset = value.utcoffset()
        if offset is None:
            raise ValueError(f"{field} must be an aware datetime")
        instant = value.astimezone(dt.timezone.utc)
        local = instant.astimezone(value.tzinfo)
        if local.replace(tzinfo=None) != value.replace(tzinfo=None) or local.utcoffset() != offset:
            raise ValueError(f"{field} is not a real local time")
    except (OverflowError, TypeError) as exc:
        raise ValueError(f"{field} has an invalid UTC offset or range") from exc
    return instant


def _group_ids(values: Iterable[str], field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field} must be a collection of group identifiers")
    try:
        ids = tuple(values)
    except TypeError as exc:
        raise ValueError(f"{field} must be a collection of group identifiers") from exc
    if any(not isinstance(item, str) or not item.strip() for item in ids):
        raise ValueError(f"{field} must contain nonempty group identifiers")
    if len(set(ids)) != len(ids):
        raise ValueError(f"{field} must contain unique group identifiers")
    return ids


def validate_cohort(
    records: Sequence[TimedRecord],
    holdout_group_ids: Iterable[str],
    *,
    data_cutoff: dt.datetime,
    complete_groups: Iterable[str] | None = None,
    source_days: Mapping[str, SourceDay] | None = None,
    source_timezone: dt.tzinfo | None = None,
    last_complete_date: dt.date | None = None,
    source_order: Mapping[str, int] | None = None,
) -> CausalCohort:
    """Check the declared test groups, settled knowledge time and holdout suffix.

    On date-only sources the supplied source calendar and complete-day cutoff
    govern both label and prediction order. A precise delayed observation
    still needs to have occurred before the prediction period starts.
    """
    data_cutoff = as_utc_instant(data_cutoff, "data_cutoff")
    holdout_ids = _group_ids(holdout_group_ids, "holdout_group_ids")
    holdout = set(holdout_ids)
    if not holdout_ids:
        raise ValueError("holdout_group_ids must name unique, nonempty groups")
    if source_days is None:
        if source_timezone is not None or last_complete_date is not None:
            raise ValueError("source-day cutoff metadata requires source dates")
    elif (
        not isinstance(source_timezone, dt.tzinfo)
        or type(last_complete_date) is not dt.date
        or last_complete_date >= data_cutoff.astimezone(source_timezone).date()
    ):
        raise ValueError("date-only sources require an inclusive last complete source date")

    by_key = {}
    clocks = {}
    calendars = set()
    day_groups = {}
    group_days = {}
    for record in records:
        key = record.event_key
        group = record.group_id
        if not isinstance(key, str) or not key.strip() or key in by_key:
            raise ValueError("records must have unique, nonempty event keys")
        if not isinstance(group, str) or not group.strip():
            raise ValueError("records require a nonempty group identifier")
        if source_days is None:
            prediction = as_utc_instant(record.kickoff, "kickoff")
            label = as_utc_instant(record.available_at, "available_at")
            if label < prediction:
                raise ValueError(f"event {key!r} requires consistent aware source timestamps")
            if prediction >= data_cutoff or label >= data_cutoff:
                raise ValueError("records must be available before data_cutoff")
        else:
            day = source_days.get(key)
            if not isinstance(day, SourceDay) or not isinstance(day.calendar, str) or not day.calendar.strip():
                raise ValueError(f"event {key!r} requires a named source calendar and dates")
            if record.kickoff is not None:
                raise ValueError(f"date-only event {key!r} cannot carry a precise kickoff")
            observed = None
            if day.observed_label_at is not None:
                observed = as_utc_instant(day.observed_label_at, "observed_label_at")
            if record.available_at is not None:
                if as_utc_instant(record.available_at, "available_at") != observed:
                    raise ValueError(f"date-only event {key!r} has inconsistent observed availability")
            prediction, label = day.prediction_date, day.label_date
            if type(prediction) is not dt.date or type(label) is not dt.date or label < prediction:
                raise ValueError(f"event {key!r} requires consistent source dates")
            if observed is not None:
                if observed >= data_cutoff:
                    raise ValueError(f"event {key!r} has invalid delayed label availability")
                observed_day = observed.astimezone(source_timezone).date()
                if observed_day < label:
                    raise ValueError(f"event {key!r} was observed before its source label date")
                label = observed_day
            if prediction > last_complete_date or label > last_complete_date:
                raise ValueError("records must be available by the last complete source date")
            if day_groups.setdefault(prediction, group) != group or group_days.setdefault(group, prediction) != prediction:
                raise ValueError("source-day groups must each describe one complete source date")
            calendars.add(day.calendar)
        by_key[key] = record
        clocks[key] = prediction, label
    if source_days is not None and (len(calendars) != 1 or set(source_days) != set(by_key)):
        raise ValueError("source-day metadata must use one calendar and cover every event exactly")

    if source_order is None:
        ordered = sorted(by_key, key=lambda key: (clocks[key][0], key))
    else:
        if not isinstance(source_order, Mapping):
            raise ValueError("source_order must map each event to its original row index")
        positions = dict(source_order)
        if (positions.keys() != by_key.keys()
                or any(type(index) is not int or index < 0 for index in positions.values())
                or len(set(positions.values())) != len(positions)):
            raise ValueError("source_order must give every event a unique nonnegative original row index")
        ordered = sorted(by_key, key=lambda key: (clocks[key][0], positions[key]))
    groups: dict[str, list[str]] = {}
    for key in ordered:
        groups.setdefault(by_key[key].group_id, []).append(key)
    complete = frozenset(groups if complete_groups is None else _group_ids(complete_groups, "complete_groups"))
    if complete.difference(groups):
        raise ValueError("complete_groups contains unknown groups")
    if not holdout.issubset(complete):
        raise ValueError("holdout_group_ids contains unknown or incomplete groups")

    search_keys = tuple(key for key in ordered if by_key[key].group_id not in holdout)
    holdout_keys = tuple(key for key in ordered if by_key[key].group_id in holdout)
    first_holdout = clocks[holdout_keys[0]][0]
    if search_keys and clocks[search_keys[-1]][0] >= first_holdout:
        raise ValueError("holdout groups must form the temporal suffix")
    if any(clocks[key][1] >= first_holdout for key in search_keys):
        raise ValueError("search labels must be available strictly before the first holdout prediction")

    group_plan = tuple(
        _CohortGroup(group, clocks[keys[0]][0], max(clocks[key][1] for key in keys), tuple(keys))
        for group, keys in groups.items()
    )
    return CausalCohort(
        search_records=tuple(by_key[key] for key in search_keys),
        holdout_records=tuple(by_key[key] for key in holdout_keys),
        search_group_ids=tuple(group for group in groups if group not in holdout),
        holdout_group_ids=tuple(group for group in groups if group in holdout),
        first_holdout_prediction=first_holdout,
        source_calendar=next(iter(calendars), None),
        last_complete_date=last_complete_date,
        source_timezone=source_timezone,
        _groups=group_plan,
        _complete_groups=complete,
    )


@dataclass(frozen=True, eq=False, slots=True)
class _Prefix(Sequence[str]):
    """A fold's keys as a bounded view of one shared chronological sequence."""

    keys: tuple[str, ...] = field(repr=False)
    stop: int
    digest: str

    def __len__(self) -> int:
        return self.stop

    def __iter__(self) -> Iterator[str]:
        return itertools.islice(self.keys, self.stop)

    def __getitem__(self, index: int | slice) -> str | tuple[str, ...]:
        if isinstance(index, slice):
            return tuple(self.keys[pos] for pos in range(*index.indices(self.stop)))
        if index < 0:
            index += self.stop
        if not 0 <= index < self.stop:
            raise IndexError(index)
        return self.keys[index]

    def __add__(self, other: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(self) + other

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sequence) or isinstance(other, str):
            return NotImplemented
        if isinstance(other, _Prefix) and self.keys is other.keys:
            return self.stop == other.stop
        return len(self) == len(other) and all(a == b for a, b in zip(self, other))

    def __hash__(self) -> int:
        return hash(tuple(self))


def _add_digest_key(digest, key: str) -> None:
    encoded = key.encode("utf-8")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)


def _sequence_digest(keys: Sequence[str]) -> str:
    if isinstance(keys, _Prefix):
        return keys.digest
    digest = sha256()
    for key in keys:
        _add_digest_key(digest, key)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class _SourceDayFold(WalkFold):
    source_day: dt.date
    source_calendar: str


def selected_prefix_folds(
    cohort: CausalCohort,
    *,
    selected_group_ids: Iterable[str] | None = None,
    min_train_blocks: int = 2,
) -> tuple[tuple[WalkFold, ...], tuple[SkippedBlock, ...]]:
    """Construct only the preregistered search folds and share their prefixes.

    Official club rounds use the MO-4 builder. This generic expansion uses
    the established MO-3 rule for prior complete, strictly available groups.
    """
    selected_ids = cohort.search_group_ids
    if selected_group_ids is not None:
        selected_ids = _group_ids(selected_group_ids, "selected search groups")
    selected = set(selected_ids)
    if selected.difference(cohort.search_group_ids):
        raise ValueError("selected search groups must be unique and inside the search cohort")
    holdout = set(cohort.holdout_group_ids)
    groups = tuple(group for group in cohort._groups if group.group_id not in holdout)
    return _prefix_folds(cohort, groups, selected, min_train_blocks, selected_group_ids is not None)


def holdout_prefix_folds(
    cohort: CausalCohort, *, min_train_blocks: int = 2
) -> tuple[tuple[WalkFold, ...], tuple[SkippedBlock, ...]]:
    """Build past-only prefixes for the locked holdout's declared groups."""
    return _prefix_folds(cohort, cohort._groups, set(cohort.holdout_group_ids), min_train_blocks, True)


def _prefix_folds(
    cohort: CausalCohort,
    group_plan: Sequence[_CohortGroup],
    selected: set[str],
    min_train_blocks: int,
    record_warmup: bool,
) -> tuple[tuple[WalkFold, ...], tuple[SkippedBlock, ...]]:
    if type(min_train_blocks) is not int or min_train_blocks < 1:
        raise ValueError("at least one training block is required")

    complete = cohort._complete_groups
    keys = tuple(key for group in group_plan if group.group_id in complete for key in group.keys)
    groups = tuple(group.group_id for group in group_plan if group.group_id in complete)
    key_digest = sha256()
    group_digest = sha256()
    n_keys = n_groups = 0
    latest_label = None
    folds = []
    skipped = []
    for group in group_plan:
        group_id = group.group_id
        if group_id in selected:
            cutoff = group.prediction
            if type(cutoff) is dt.date:
                # This time is the start of a source period for MO-3 ordering.
                cutoff = dt.datetime.combine(cutoff, dt.time.min, tzinfo=cohort.source_timezone)
            if group_id not in complete:
                skipped.append(SkippedBlock(group_id, SKIP_TARGET_INCOMPLETE, cutoff))
            elif n_groups < min_train_blocks:
                if record_warmup:
                    skipped.append(SkippedBlock(group_id, "insufficient_training_blocks", cutoff))
            elif latest_label is not None and latest_label >= group.prediction:
                skipped.append(SkippedBlock(group_id, SKIP_PREFIX_INCOMPLETE, cutoff))
            else:
                common = dict(
                    block_id=group_id,
                    cutoff=cutoff,
                    train_keys=_Prefix(keys, n_keys, key_digest.hexdigest()),
                    test_keys=group.keys,
                    train_block_ids=_Prefix(groups, n_groups, group_digest.hexdigest()),
                )
                if type(group.prediction) is dt.date:
                    folds.append(_SourceDayFold(
                        **common, source_day=group.prediction, source_calendar=cohort.source_calendar,
                    ))
                else:
                    folds.append(WalkFold(**common))
        if group_id in complete:
            for key in group.keys:
                _add_digest_key(key_digest, key)
            _add_digest_key(group_digest, group_id)
            n_keys += len(group.keys)
            n_groups += 1
            latest_label = group.last_label if latest_label is None else max(latest_label, group.last_label)
    return tuple(folds), tuple(skipped)


def nested_fold_ids(outer: WalkFold, inner: Sequence[WalkFold]) -> tuple[str, ...]:
    """Identify earlier folds contained wholly in an outer training prefix."""
    outer_keys = outer.train_keys
    outer_cutoff = as_utc_instant(outer.cutoff, "outer fold cutoff")
    key_set = None
    qualifying = []
    for fold in inner:
        if as_utc_instant(fold.cutoff, "inner fold cutoff") >= outer_cutoff or not fold.test_keys:
            continue
        inner_keys = fold.train_keys
        if isinstance(outer_keys, _Prefix) and isinstance(inner_keys, _Prefix) and outer_keys.keys is inner_keys.keys:
            stop = len(inner_keys)
            fits = outer_keys[stop:stop + len(fold.test_keys)] == fold.test_keys
        else:
            if key_set is None:
                key_set = set(outer_keys)
            fits = key_set.issuperset(inner_keys) and key_set.issuperset(fold.test_keys)
        if fits:
            qualifying.append(fold.block_id)
    return tuple(qualifying)


@dataclass(frozen=True)
class SearchBudget:
    """Trial cap plus an injected deadline predicate (no clock reads).

    ``deadline`` returns True when the single run-wide time budget is
    exhausted. The locked v1 defaults are declared in the runner.
    """

    max_trials: int
    deadline: Callable[[], bool]

    def __post_init__(self) -> None:
        if isinstance(self.max_trials, bool) or not isinstance(self.max_trials, int) \
                or self.max_trials < 1:
            raise ValueError("max_trials must be a positive int")
        if not callable(self.deadline):
            raise ValueError("deadline must be a callable returning bool")


@dataclass(frozen=True, order=True, slots=True)
class LineKey:
    """Exact line identity in the sealed fixture/contract manifest."""

    event_key: str
    target: str
    line_key: str
    observation_id: str
    class_order: tuple[str, ...]

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (
            self.event_key, self.target, self.line_key, self.observation_id,
        )):
            raise ValueError("line identity requires nonempty event, target, line and observation IDs")
        if not isinstance(self.class_order, tuple) or len(self.class_order) not in (2, 3) or any(
            not isinstance(label, str) or not label.strip() for label in self.class_order
        ) or len(set(self.class_order)) != len(self.class_order):
            raise ValueError("line identity requires an ordered binary or three-way contract")

    def _identity(self) -> tuple:
        return (self.event_key, self.target, self.line_key, self.observation_id, self.class_order)


@dataclass(frozen=True, slots=True)
class Forecast:
    """A truth-verified forecast, optionally tagged as a scored family fallback."""

    row: ScoredEvent
    fallback_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ForecastBatch:
    universe: tuple[LineKey, ...]
    predictions: Mapping[LineKey, Forecast]
    refusals: Mapping[LineKey, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "universe", tuple(self.universe))
        object.__setattr__(self, "predictions", MappingProxyType(dict(self.predictions)))
        object.__setattr__(self, "refusals", MappingProxyType(dict(self.refusals)))


@dataclass(frozen=True, slots=True)
class PlannedTrial:
    """One physical evaluation before any predictions have been opened."""

    trial_index: int
    candidate_index: int
    fold: WalkFold
    fold_sha256: str
    spec: ModelSpec
    spec_sha256: str
    targets: tuple[str, ...]
    model_context: str


def _digest(value: object) -> str:
    return sha256(canonical_value(value).encode("utf-8")).hexdigest()


def plan_evaluations(
    folds: Sequence[WalkFold],
    catalogs: Mapping[str, SearchCatalog],
    *,
    model_contexts: Mapping[str, str] | None = None,
    diagnostic_only: bool = False,
) -> tuple[PlannedTrial, ...]:
    """Schedule and deduplicate each spec, prefix and model context.

    The caller shares a context only when the inputs and fit are identical.
    All exact comparators lead each fold's physical plan. The catalog's own
    order remains the tie-break order for each target's selection.
    """
    if not catalogs:
        raise ValueError("evaluation plan requires at least one target catalogue")
    contexts = {target: "shared" for target in catalogs} if model_contexts is None else dict(model_contexts)
    if contexts.keys() != catalogs.keys() or any(
        not isinstance(context, str) or not context for context in contexts.values()
    ):
        raise ValueError("model contexts must cover every declared target")
    if len({fold.block_id for fold in folds}) != len(folds):
        raise ValueError("evaluation folds must have unique block IDs")
    cutoffs = {
        fold.block_id: as_utc_instant(fold.cutoff, "evaluation fold prediction boundary")
        for fold in folds
    }
    ordered_folds = sorted(folds, key=lambda fold: (cutoffs[fold.block_id], fold.block_id))

    # One lane per model context. The retained target mask is the set of
    # targets whose predetermined catalogs request this exact spec and fit.
    schedule = {}
    for active_only in ((True,) if diagnostic_only else (True, False)):
        for target, catalog in catalogs.items():
            for entry in catalog.entries:
                if (entry.spec_sha256 == catalog.incumbent_sha256) == active_only:
                    key = (entry.spec_sha256, contexts[target])
                    spec, targets = schedule.setdefault(key, (entry.spec, set()))
                    if spec != entry.spec:
                        raise ValueError("a planned digest identifies two different specs")
                    targets.add(target)
    plan = []
    for fold in ordered_folds:
        source_day = getattr(fold, "source_day", None)
        fold_digest = _digest((
            fold.block_id,
            cutoffs[fold.block_id].isoformat(),
            None if source_day is None else source_day.isoformat(),
            getattr(fold, "source_calendar", None),
            _sequence_digest(fold.train_keys),
            _sequence_digest(fold.test_keys),
            _sequence_digest(fold.train_block_ids),
        ))
        for candidate_index, ((spec_sha256, context), (spec, targets)) in enumerate(schedule.items()):
            plan.append(PlannedTrial(
                trial_index=len(plan),
                candidate_index=candidate_index,
                fold=fold,
                fold_sha256=fold_digest,
                spec=spec,
                spec_sha256=spec_sha256,
                targets=tuple(target for target in catalogs if target in targets),
                model_context=context,
            ))
    return tuple(plan)


@dataclass(frozen=True, slots=True)
class ScopedTrialRecord:
    trial_index: int
    fold_block_id: str
    candidate_index: int
    spec_sha256: str
    model_context: str
    targets: tuple[str, ...]
    status: str
    n_events: int
    mean_loss: float | None
    issue: str | None = None


@dataclass(frozen=True, slots=True)
class ScopedSearchResult:
    plan: tuple[PlannedTrial, ...]
    trials: tuple[ScopedTrialRecord, ...]
    batches: Mapping[tuple[str, str, str], ForecastBatch]
    universes: Mapping[tuple[str, str], tuple[LineKey, ...]]
    budget_exhausted: bool
    issue: str | None

    @property
    def planned_trials(self) -> int:
        return len(self.plan)

    @property
    def started_trials(self) -> int:
        """The number of physical callbacks started, including aborted fits."""
        return len(self.trials)

    @property
    def evaluated_trials(self) -> int:
        return self.started_trials

    @property
    def completed_trials(self) -> int:
        return sum(trial.status == TRIAL_COMPLETED for trial in self.trials)

    @property
    def empty_trials(self) -> int:
        return sum(trial.status == TRIAL_NO_EVENTS for trial in self.trials)

    @property
    def aborted_trials(self) -> int:
        return sum(trial.status == TRIAL_ABORTED for trial in self.trials)

    @property
    def shared_target_uses(self) -> int:
        return sum(len(trial.targets) - 1 for trial in self.trials if trial.status != TRIAL_ABORTED)

    def is_complete(self) -> bool:
        return self.evaluated_trials == self.planned_trials and not self.budget_exhausted and self.issue is None


_ScopedScore = Callable[[ModelSpec, WalkFold, tuple[str, ...]], Mapping[str, ForecastBatch]]


def _index_universes(
    plan: Sequence[PlannedTrial], universes: Mapping[tuple[str, str], Sequence[LineKey]],
) -> dict[tuple[str, str], tuple[LineKey, ...]]:
    folds = {item.fold.block_id: item.fold for item in plan}
    expected = {(item.fold.block_id, target) for item in plan for target in item.targets}
    if not expected.issubset(universes):
        raise ValueError("each scheduled fold and target requires a sealed line universe")
    test_sets = {block: set(fold.test_keys) for block, fold in folds.items()}
    indexed = {}
    seen = set()
    for context, keys in universes.items():
        block, target = context
        rows = tuple(keys)
        if any(not isinstance(key, LineKey) or key.target != target for key in rows):
            raise ValueError("sealed line key does not belong to its target")
        if block in test_sets and any(key.event_key not in test_sets[block] for key in rows):
            raise ValueError("sealed line key does not belong to its test fold")
        unique = set(rows)
        if len(rows) != len(unique) or seen.intersection(unique):
            raise ValueError("sealed fixture-line universe contains duplicate keys")
        seen.update(unique)
        indexed[context] = tuple(sorted(unique))
    return indexed


def _verify_batch(batch: ForecastBatch, universe: tuple[LineKey, ...]) -> ForecastBatch:
    if not isinstance(batch, ForecastBatch) or tuple(sorted(batch.universe)) != universe:
        raise ValueError("forecast batch does not match the sealed line universe")
    predicted = set(batch.predictions)
    refused = set(batch.refusals)
    if predicted.intersection(refused) or predicted.union(refused) != set(universe):
        raise ValueError("every sealed line requires exactly one forecast or explicit refusal")
    if any(not isinstance(reason, str) or not reason for reason in batch.refusals.values()):
        raise ValueError("every refused line requires its reason")
    for key in sorted(predicted):
        forecast = batch.predictions[key]
        if not isinstance(forecast, Forecast) or not isinstance(forecast.row, ScoredEvent):
            raise ValueError("expected a verified forecast on the sealed line")
        row = forecast.row
        if (row.event_key, row.line_key) != (key.event_key, key.line_key):
            raise ValueError("forecast does not match its exact event and line identity")
        probabilities = row.probabilities
        if (
            not isinstance(probabilities, tuple)
            or len(probabilities) != len(key.class_order)
            or any(type(value) not in (float, int) or not 0 <= value <= 1 for value in probabilities)
            or not math.isclose(math.fsum(probabilities), 1.0, rel_tol=0.0, abs_tol=1e-9)
            or type(row.outcome_index) is not int
            or not 0 <= row.outcome_index < len(probabilities)
        ):
            raise ValueError("forecast does not match its class order and realization")
        if forecast.fallback_reason is not None and (
            not isinstance(forecast.fallback_reason, str) or not forecast.fallback_reason
        ):
            raise ValueError("a scored fallback requires its reason")
    return batch


def _forecast_loss(forecast: Forecast) -> float:
    row = forecast.row
    return multiclass_log_loss(row.probabilities, row.outcome_index, floor=1e-12)


def run_scoped_trials(
    plan: Sequence[PlannedTrial],
    evaluate: _ScopedScore,
    *,
    budget: SearchBudget,
    universes: Mapping[tuple[str, str], Sequence[LineKey]],
) -> ScopedSearchResult:
    """Evaluate the sealed plan once, retaining completed and aborted records."""
    ordered = tuple(plan)
    trials = []
    batches = {}
    indexed = {}
    issue = None
    exhausted = budget.deadline()
    if not exhausted:
        indexed = _index_universes(ordered, universes)
        for item in ordered:
            if len(trials) >= budget.max_trials or budget.deadline():
                exhausted = True
                break
            block_id = item.fold.block_id
            count = 0
            mean = None
            try:
                raw = evaluate(item.spec, item.fold, item.targets)
                if set(raw) != set(item.targets):
                    raise ValueError("forecast callback did not return the planned target mask")
                verified = {
                    target: _verify_batch(raw[target], indexed[block_id, target])
                    for target in item.targets
                }
                losses = tuple(
                    _forecast_loss(batch.predictions[key])
                    for target, batch in verified.items()
                    for key in indexed[block_id, target]
                    if key in batch.predictions
                )
                count = len(losses)
                mean = statistics.fmean(losses) if losses else None
                status = TRIAL_COMPLETED if losses else TRIAL_NO_EVENTS
                for target, batch in verified.items():
                    batches[block_id, item.spec_sha256, target] = batch
            except Exception as exc:
                status = TRIAL_ABORTED
                issue = f"{type(exc).__name__}: {exc}"
            trials.append(ScopedTrialRecord(
                trial_index=item.trial_index, fold_block_id=block_id,
                candidate_index=item.candidate_index, spec_sha256=item.spec_sha256,
                model_context=item.model_context, targets=item.targets, status=status,
                n_events=count, mean_loss=mean, issue=issue,
            ))
            if issue is not None:
                break
        if not exhausted:
            exhausted = budget.deadline()
    return ScopedSearchResult(
        ordered, tuple(trials), MappingProxyType(batches), MappingProxyType(indexed), exhausted, issue,
    )


@dataclass(frozen=True, slots=True)
class SpecCoverage:
    spec_sha256: str
    predicted_lines: int
    refused_lines: int
    refusal_reasons: tuple[tuple[str, int], ...]
    fallback_reasons: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class CommonBlock:
    fold_block_id: str
    eligible_lines: int
    common_lines: int
    eligible_matches: int
    common_matches: int
    mask_sha256: str
    coverages: tuple[SpecCoverage, ...]
    common_keys: tuple[LineKey, ...]
    missing_specs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CommonSelection:
    winner: ModelSpec
    selection: OneSeResult | None
    status: str
    blocks: tuple[CommonBlock, ...]
    mask_sha256: str

    @property
    def eligible_lines(self) -> int:
        return sum(block.eligible_lines for block in self.blocks)

    @property
    def common_lines(self) -> int:
        return sum(block.common_lines for block in self.blocks)

    @property
    def eligible_matches(self) -> int:
        return sum(block.eligible_matches for block in self.blocks)

    @property
    def common_matches(self) -> int:
        return sum(block.common_matches for block in self.blocks)

    @property
    def nonempty_blocks(self) -> int:
        return sum(block.common_lines > 0 for block in self.blocks)


def select_common(
    result: ScopedSearchResult,
    catalog: SearchCatalog,
    definitions: Sequence[ParamDef],
    target: str,
    fold_ids: Sequence[str],
    *,
    min_inner: int = 2,
    require_all: bool = True,
) -> CommonSelection:
    """Apply the same one-SE face to one target's causal inner or final context.

    Pool common fixture-line losses across blocks for the target objective.
    MO-3 takes the mean within each nonempty block for its standard error.
    """
    if type(min_inner) is not int or min_inner < 1 or len(set(fold_ids)) != len(fold_ids):
        raise ValueError("selection requires unique folds and a positive minimum")
    entries = catalog.entries
    blocks = []
    block_losses = [[] for _ in entries]
    for block_id in fold_ids:
        universe = result.universes.get((block_id, target), ())
        common = set(universe)
        found = []
        coverages = []
        missing = []
        for entry in entries:
            digest = entry.spec_sha256
            batch = result.batches.get((block_id, digest, target))
            if batch is None:
                missing.append(digest)
                common.clear()
                continue
            found.append(batch)
            common.intersection_update(batch.predictions)
            coverages.append(SpecCoverage(
                digest, len(batch.predictions), len(batch.refusals),
                tuple(sorted(Counter(batch.refusals.values()).items())),
                tuple(sorted(Counter(
                    forecast.fallback_reason for forecast in batch.predictions.values()
                    if forecast.fallback_reason is not None
                ).items())),
            ))
        keys = tuple(sorted(common))
        if keys:
            for losses, batch in zip(block_losses, found, strict=True):
                losses.append(tuple(_forecast_loss(batch.predictions[key]) for key in keys))
        blocks.append(CommonBlock(
            block_id, len(universe), len(keys),
            len({key.event_key for key in universe}), len({key.event_key for key in keys}),
            _digest((target, block_id, tuple(key._identity() for key in keys))),
            tuple(coverages), keys, tuple(missing),
        ))

    mask = _digest((target, tuple((block.fold_block_id, block.mask_sha256) for block in blocks)))
    nonempty = sum(block.common_lines > 0 for block in blocks)
    if (
        result.budget_exhausted or result.issue is not None
        or any(block.missing_specs for block in blocks)
        or nonempty < min_inner or (require_all and nonempty != len(blocks))
    ):
        status = INSUFFICIENT_COMPARABLE_SEARCH_FOLDS if require_all else INSUFFICIENT_COMPARABLE_INNER_FOLDS
        return CommonSelection(catalog.incumbent, None, status, tuple(blocks), mask)
    scores = [statistics.fmean(itertools.chain.from_iterable(losses)) for losses in block_losses]
    selection = one_standard_error_rule(
        catalog.specs, scores, block_losses, catalog.incumbent, definitions,
        lambda spec: dict(spec.params),
    )
    if selection.status == NO_BETTER_CANDIDATE and selection.winner != catalog.incumbent:
        # Two valid full specs can share parameter values but differ in envelope.
        selection = replace(selection, status=CANDIDATE_SELECTED)
    return CommonSelection(selection.winner, selection, selection.status, tuple(blocks), mask)


@dataclass(frozen=True)
class TrialRecord:
    """One evaluated trial: one candidate on one fold, exactly counted."""

    trial_index: int
    fold_block_id: str
    candidate_index: int
    status: str
    n_events: int
    mean_loss: float | None


@dataclass(frozen=True)
class SearchResult:
    """Complete accounting of one budgeted search.

    ``event_scores[i][j]`` is candidate ``i``'s per-event loss sequence
    on fold ``j`` (empty when the fold contributed no events);
    ``mean_losses[i][j]`` is its ``statistics.fmean`` over those events
    or ``None``. ``trials`` records every evaluated trial in order.
    ``budget_exhausted`` records an early stop or a deadline exceeded
    while the final trial was running.
    """

    candidates: tuple[Mapping[str, object], ...]
    fold_block_ids: tuple[str, ...]
    trials: tuple[TrialRecord, ...]
    mean_losses: tuple[tuple[float | None, ...], ...]
    event_scores: tuple[tuple[tuple[float, ...], ...], ...]
    budget_exhausted: bool

    @property
    def evaluated_trials(self) -> int:
        return len(self.trials)

    def is_complete(self) -> bool:
        """True when every candidate was evaluated on every fold."""
        expected = len(self.candidates) * len(self.fold_block_ids)
        return len(self.trials) == expected and not self.budget_exhausted


def run_trials(
    candidates: Sequence[Mapping[str, object]],
    folds: Sequence[WalkFold],
    score: _ScoreFn,
    *,
    budget: SearchBudget,
) -> SearchResult:
    """Evaluate every candidate on every fold under the budget.

    Trial order is fold-major (all candidates on fold 1, then fold 2,
    ...), candidates in declared order within a fold. The budget is
    checked BEFORE each trial: ``deadline()`` or ``max_trials`` reached
    stops the loop between trials with completed partials preserved.
    The deadline is also checked after the final trial.
    ``score(candidate, fold, fold.test_keys)`` mirrors the canonical
    ``walkforward.nested_select`` score contract and returns one loss
    per test event; means use ``statistics.fmean`` (the kernel
    convention).
    """
    if not candidates:
        raise ValueError("run_trials requires at least one candidate")

    trials: list[TrialRecord] = []
    mean_losses: list[list[float | None]] = [
        [None] * len(folds) for _ in candidates
    ]
    event_scores: list[list[tuple[float, ...]]] = [
        [()] * len(folds) for _ in candidates
    ]
    budget_exhausted = False
    trial_index = 0
    for fold_index, fold in enumerate(folds):
        for candidate_index, candidate in enumerate(candidates):
            if trial_index >= budget.max_trials or budget.deadline():
                budget_exhausted = True
                break
            losses = tuple(score(candidate, fold, fold.test_keys))
            count = len(losses)
            mean = statistics.fmean(losses) if losses else None
            mean_losses[candidate_index][fold_index] = mean
            event_scores[candidate_index][fold_index] = losses
            trials.append(
                TrialRecord(
                    trial_index=trial_index,
                    fold_block_id=fold.block_id,
                    candidate_index=candidate_index,
                    status=TRIAL_COMPLETED if count else TRIAL_NO_EVENTS,
                    n_events=count,
                    mean_loss=mean,
                )
            )
            trial_index += 1
        if budget_exhausted:
            break
    if trials and not budget_exhausted:
        budget_exhausted = budget.deadline()
    return SearchResult(
        candidates=tuple(candidates),
        fold_block_ids=tuple(fold.block_id for fold in folds),
        trials=tuple(trials),
        mean_losses=tuple(tuple(row) for row in mean_losses),
        event_scores=tuple(tuple(row) for row in event_scores),
        budget_exhausted=budget_exhausted,
    )


def select_candidate(
    result: SearchResult,
    param_defs: Sequence[ParamDef],
    incumbent_params: Mapping[str, object],
    *,
    multiplier: float = 1.0,
) -> OneSeResult:
    """Final selection through the canonical one-SE rule.

    Candidate scores are the means over EVERY fold's scored events
    (``statistics.fmean`` over the concatenated per-fold losses, the
    same convention ``nested_select`` applies to inner folds);
    ``block_scores[i]`` are the per-fold event-loss sequences, from
    which ``one_standard_error_rule`` derives the best candidate's
    block-mean standard error. Selection requires a completed search
    and at least one score per candidate. It compares full parameter
    mappings so fixed settings outside the search space preserve the
    incumbent's identity.
    """
    if not result.is_complete():
        raise ValueError("select_candidate requires a complete search result")
    if not result.fold_block_ids:
        raise ValueError("select_candidate requires at least one fold")

    scores: list[float] = []
    block_scores: list[list[tuple[float, ...]]] = []
    for per_fold in result.event_scores:
        flat = [value for fold_losses in per_fold for value in fold_losses]
        if not flat:
            raise ValueError(
                "select_candidate requires every candidate to have scored "
                "at least one event"
            )
        scores.append(statistics.fmean(flat))
        block_scores.append([tuple(fold_losses) for fold_losses in per_fold])
    incumbent = dict(incumbent_params)
    return one_standard_error_rule(
        tuple(result.candidates),
        scores,
        block_scores,
        incumbent,
        param_defs,
        lambda candidate: {**incumbent, **candidate},
        multiplier=multiplier,
    )
