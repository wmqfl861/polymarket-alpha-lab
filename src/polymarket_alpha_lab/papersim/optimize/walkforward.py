"""MO-3 optimize walk-forward kernel: expanding folds, nested selection.

Design 04 section 8 / plan 05 node MO-3. Pure time-slicing on caller-
supplied in-memory records: no data loading (that is MO-4's dataset
layer), no model code, standard library only. The expanding semantics
mirror ``tools/wp_p4_history.build_folds`` (2026-11-07): a group (full
matchweek / match day / expiry date) becomes a TEST block only when it
is complete; the fold cutoff is frozen at the group's earliest kickoff;
training is every complete group whose every record is available strictly
BEFORE that cutoff; a target group whose prior prefix is not fully
available at the cutoff is skipped with the stated reason.

Leakage protection by construction: cutoffs only ever look at records
with ``available_at < cutoff`` (strict), so adding future rows (cutoff
day or later) can never change an existing fold -- pinned by the
future-row-injection tests, one per generator.
"""
from __future__ import annotations

import datetime
import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from polymarket_alpha_lab.papersim.optimize.spec import ParamDef

__all__ = (
    "OneSeResult",
    "Selection",
    "SkippedBlock",
    "TimedRecord",
    "WalkFold",
    "count_changed_params",
    "expanding_expiry_folds",
    "expanding_group_folds",
    "expanding_matchday_folds",
    "expanding_matchweek_folds",
    "nested_select",
    "one_standard_error_rule",
    "param_distance",
)

SKIP_TARGET_INCOMPLETE = "target_block_incomplete"
SKIP_PREFIX_INCOMPLETE = "prior_block_prefix_incomplete_at_cutoff"


class TimedRecord(Protocol):
    """Minimal record contract for the expanding fold generators.

    ``available_at`` is the knowledge time (kickoff plus completion lag,
    or the dataset layer's availability timestamp); ``kickoff`` orders
    records and freezes the target group's cutoff. ``group_id`` is the
    expansion unit (ISO matchweek, match day or expiry date).
    """

    event_key: str
    group_id: str
    kickoff: datetime.datetime
    available_at: datetime.datetime


@dataclass(frozen=True, slots=True)
class WalkFold:
    """One expanding-window fold: test = one complete group."""

    block_id: str
    cutoff: datetime.datetime
    train_keys: tuple[str, ...]
    test_keys: tuple[str, ...]
    train_block_ids: tuple[str, ...]

    @property
    def all_keys(self) -> tuple[str, ...]:
        return self.train_keys + self.test_keys


@dataclass(frozen=True, slots=True)
class SkippedBlock:
    """A group that could not become a fold, with the stated reason."""

    block_id: str
    reason: str
    cutoff: datetime.datetime | None = None


def _sort_key(record: TimedRecord) -> tuple[datetime.datetime, str]:
    return (record.kickoff, record.event_key)


def expanding_group_folds(
    records: Sequence[TimedRecord],
    *,
    min_train_blocks: int = 2,
    complete_groups: frozenset[str] | None = None,
) -> tuple[tuple[WalkFold, ...], tuple[SkippedBlock, ...]]:
    """Generic expanding-window folds over pre-grouped records.

    Groups are ordered by their earliest kickoff (ties by group id). A
    group qualifies as a test block when it is complete (every group is
    complete when ``complete_groups`` is None -- the caller decides
    completeness from official schedules) and at least ``min_train_blocks``
    complete groups precede it. The cutoff is the group's earliest
    kickoff; the training set is EVERY earlier complete group, and each
    of them must be fully available strictly before the cutoff -- any
    unavailable earlier group blocks the fold as
    ``prior_block_prefix_incomplete_at_cutoff`` (the literal expanding
    prefix of ``build_folds``: a postponed old-round game blocks the
    affected fold). Test/train keys are ordered by (kickoff, event_key).

    ONE deliberate divergence from ``build_folds`` (code-review P3-1,
    declared here): ``build_folds`` also blocks a target when an earlier
    ROUND is incomplete (``not complete.get(rnd)`` joins the ``missing``
    check); this function instead EXCLUDES incomplete earlier groups from
    the prior set entirely -- they neither train nor block, because
    completeness here is a caller-supplied property of the data layer
    (MO-4), not an official-round-map lookup. When parity with
    ``build_folds`` is required, pass only the complete prefix as records
    or consult the skipped list for excluded groups.
    """
    if isinstance(min_train_blocks, bool) or min_train_blocks < 1:
        raise ValueError("at least one training block is required")
    groups: dict[str, list[TimedRecord]] = {}
    for record in records:
        groups.setdefault(record.group_id, []).append(record)
    ordered = sorted(
        groups.items(),
        key=lambda item: (min(_sort_key(record) for record in item[1]), item[0]),
    )
    is_complete = (
        (lambda gid: True)
        if complete_groups is None
        else (lambda gid: gid in complete_groups)
    )
    eligible = [gid for gid, _rows in ordered if is_complete(gid)]
    folds: list[WalkFold] = []
    skipped: list[SkippedBlock] = []
    for target_position, target in enumerate(eligible):
        if target_position < min_train_blocks:
            continue
        rows = groups[target]
        cutoff = min(record.kickoff for record in rows)
        prior = eligible[:target_position]
        blocked = [
            gid
            for gid in prior
            if max(record.available_at for record in groups[gid]) >= cutoff
        ]
        if blocked:
            skipped.append(SkippedBlock(target, SKIP_PREFIX_INCOMPLETE, cutoff=cutoff))
            continue
        train_keys: list[str] = []
        train_ids: list[str] = []
        for gid in prior:
            train_ids.append(gid)
            train_keys.extend(
                record.event_key
                for record in sorted(groups[gid], key=_sort_key)
            )
        test_keys = tuple(
            record.event_key for record in sorted(rows, key=_sort_key)
        )
        folds.append(
            WalkFold(
                block_id=target,
                cutoff=cutoff,
                train_keys=tuple(train_keys),
                test_keys=test_keys,
                train_block_ids=tuple(train_ids),
            )
        )
    for gid, _rows in ordered:
        if not is_complete(gid):
            skipped.append(SkippedBlock(gid, SKIP_TARGET_INCOMPLETE))
    return (
        tuple(sorted(folds, key=lambda fold: (fold.cutoff, fold.block_id))),
        tuple(sorted(skipped, key=lambda skip: (skip.block_id, skip.reason))),
    )


@dataclass(frozen=True, slots=True)
class _Regrouped:
    """A record re-grouped under a kickoff-derived group id."""

    record: TimedRecord
    group_id: str

    @property
    def event_key(self) -> str:
        return self.record.event_key

    @property
    def kickoff(self) -> datetime.datetime:
        return self.record.kickoff

    @property
    def available_at(self) -> datetime.datetime:
        return self.record.available_at


def expanding_matchweek_folds(
    records: Sequence[TimedRecord],
    *,
    min_train_blocks: int = 2,
    complete_groups: frozenset[str] | None = None,
) -> tuple[tuple[WalkFold, ...], tuple[SkippedBlock, ...]]:
    """Expanding folds by full ISO matchweek of the kickoff time."""
    grouped = [
        _Regrouped(record, f"{record.kickoff.isocalendar()[0]}-W{record.kickoff.isocalendar()[1]:02d}")
        for record in records
    ]
    return expanding_group_folds(
        grouped, min_train_blocks=min_train_blocks, complete_groups=complete_groups
    )


def expanding_matchday_folds(
    records: Sequence[TimedRecord],
    *,
    min_train_blocks: int = 2,
    complete_groups: frozenset[str] | None = None,
) -> tuple[tuple[WalkFold, ...], tuple[SkippedBlock, ...]]:
    """Expanding folds by kickoff match day (UTC date)."""
    grouped = [
        _Regrouped(record, record.kickoff.date().isoformat())
        for record in records
    ]
    return expanding_group_folds(
        grouped, min_train_blocks=min_train_blocks, complete_groups=complete_groups
    )


def expanding_expiry_folds(
    records: Sequence[TimedRecord],
    *,
    min_train_blocks: int = 2,
    complete_groups: frozenset[str] | None = None,
) -> tuple[tuple[WalkFold, ...], tuple[SkippedBlock, ...]]:
    """Expanding folds by the record's own ``group_id`` (crypto expiry date).

    For expiry families the caller sets ``group_id`` to the expiry date
    string; knowledge time (``available_at``) still gates training.
    """
    return expanding_group_folds(
        records, min_train_blocks=min_train_blocks, complete_groups=complete_groups
    )


# ---------------------------------------------------------------------------
# Nested selection (design 04 section 5, measure 2)
# ---------------------------------------------------------------------------

FALLBACK_INSUFFICIENT_INNER = "fixed_predeclared_fallback_insufficient_inner_folds"
NESTED_SELECTION = "nested_training_only_selection"


@dataclass(frozen=True, slots=True)
class Selection:
    """Result of one nested selection (or its pre-declared fallback)."""

    winner: object
    status: str
    scores: tuple[tuple[object, float, int], ...]  # (candidate, mean, n)
    inner_block_ids: tuple[str, ...]


def nested_select(
    outer: WalkFold,
    inner: Sequence[WalkFold],
    candidates: Sequence[object],
    score: Callable[[object, WalkFold, tuple[str, ...]], Sequence[float]],
    *,
    fallback: object,
    min_inner: int = 2,
    eligible: Callable[[object], bool] | None = None,
) -> Selection:
    """Select a candidate on inner held-out folds wholly inside the train set.

    Mirrors ``wp_p4_walkforward.select_config``: inner folds qualify when
    their cutoff precedes the outer cutoff and every inner key (train and
    test) lies in the outer training set. Fewer than ``min_inner``
    qualifying folds, or no eligible candidate, returns the pre-declared
    ``fallback``. Objective: the MEAN of the per-event scores over all
    qualifying inner test events, smaller is better, ties break by
    declared candidate order (``min`` keeps the first). ``score(candidate,
    fold, fold.test_keys)`` returns one loss per test event; means use
    ``statistics.fmean`` (the WP-P4 convention).
    """
    outer_keys = set(outer.train_keys)
    qualifying = [
        fold
        for fold in inner
        if fold.cutoff < outer.cutoff
        and fold.all_keys
        and set(fold.all_keys) <= outer_keys
    ]
    inner_ids = tuple(fold.block_id for fold in qualifying)
    if len(qualifying) < min_inner:
        return Selection(fallback, FALLBACK_INSUFFICIENT_INNER, (), inner_ids)
    scores: list[tuple[object, float, int]] = []
    for candidate in candidates:
        if eligible is not None and not eligible(candidate):
            continue
        losses: list[float] = []
        for fold in qualifying:
            losses.extend(score(candidate, fold, fold.test_keys))
        if not losses:
            continue
        scores.append((candidate, statistics.fmean(losses), len(losses)))
    if not scores:
        return Selection(fallback, FALLBACK_INSUFFICIENT_INNER, (), inner_ids)
    best = min(scores, key=lambda item: item[1])
    return Selection(best[0], NESTED_SELECTION, tuple(scores), inner_ids)


# ---------------------------------------------------------------------------
# One-standard-error rule (design 04 section 5, measure 3)
# ---------------------------------------------------------------------------

NO_BETTER_CANDIDATE = "no_better_candidate"
CANDIDATE_SELECTED = "candidate_selected"


@dataclass(frozen=True, slots=True)
class OneSeResult:
    """Outcome of the one-standard-error rule with the incumbent shrinkage."""

    winner: object
    status: str
    best_score: float
    standard_error: float
    threshold: float
    needs_preseason_reestimate: bool


def one_standard_error_rule(
    candidates: Sequence[object],
    scores: Sequence[float],
    block_scores: Sequence[Sequence[float]],
    incumbent: object,
    param_defs: Sequence[ParamDef],
    params_of: Callable[[object], dict],
    *,
    multiplier: float = 1.0,
) -> OneSeResult:
    """One-SE rule shrinking to the incumbent (design 04 section 5 #3).

    Exact semantics (review P2, pinned here):
    - scores are losses (smaller is better); ``best = min(scores)``;
    - ``SE(best) = stdev(block means of the BEST candidate, ddof=1)
      / sqrt(n_blocks)`` -- sample standard deviation over the best
      candidate's inner-block mean scores; fewer than two blocks gives
      SE = 0.0 (plain minimization);
    - eligible candidates: ``score <= best + multiplier * SE`` -- one
      sided, widening upward only, never downward;
    - among eligible candidates minimise ``param_distance`` to the
      incumbent: bounded numeric params contribute ``|a - b| / (upper -
      lower)``; unbounded numeric params contribute the raw ``|a - b|``;
      ``choice`` params contribute 1.0 when differing, 0.0 when equal;
      a param present on one side only counts as a full unit;
    - distance ties break toward FEWER changed params (design 04 #3),
      then declared order;
    - a winner whose params equal the incumbent's keeps the incumbent:
      status ``no_better_candidate`` (the holdout is never touched);
    - ``needs_preseason_reestimate`` is True when the winner changes any
      ``season_frozen`` param (the policy layer blocks in-season use).
    """
    if (
        not candidates
        or len(candidates) != len(scores)
        or len(scores) != len(block_scores)
    ):
        raise ValueError("candidates, scores and block_scores must align non-empty")
    best_score = min(scores)
    best_index = scores.index(best_score)
    best_block_means = [
        statistics.fmean(block) for block in block_scores[best_index] if block
    ]
    if len(best_block_means) >= 2:
        standard_error = statistics.stdev(best_block_means) / math.sqrt(
            len(best_block_means)
        )
    else:
        standard_error = 0.0
    threshold = best_score + multiplier * standard_error
    incumbent_params = params_of(incumbent)
    ranked = []
    for index, candidate in enumerate(candidates):
        if scores[index] > threshold:
            continue
        params = params_of(candidate)
        ranked.append(
            (
                param_distance(param_defs, incumbent_params, params),
                count_changed_params(param_defs, incumbent_params, params),
                index,
                candidate,
                params,
            )
        )
    if ranked:
        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        winner, winner_params = ranked[0][3], ranked[0][4]
    else:
        winner = candidates[best_index]
        winner_params = params_of(winner)
    status = (
        NO_BETTER_CANDIDATE
        if winner_params == incumbent_params
        else CANDIDATE_SELECTED
    )
    needs_preseason = any(
        definition.season_frozen
        and winner_params.get(definition.name)
        != incumbent_params.get(definition.name)
        for definition in param_defs
    )
    return OneSeResult(
        winner=winner,
        status=status,
        best_score=best_score,
        standard_error=standard_error,
        threshold=threshold,
        needs_preseason_reestimate=needs_preseason,
    )


def param_distance(
    defs: Sequence[ParamDef],
    left: dict,
    right: dict,
) -> float:
    """Normalized distance between two parameter mappings (see one_se)."""
    total = 0.0
    for definition in defs:
        a, b = left.get(definition.name), right.get(definition.name)
        if a is None or b is None:
            total += 0.0 if a is b else 1.0
            continue
        if definition.kind == "choice":
            total += 0.0 if a == b else 1.0
            continue
        if definition.lower is not None and definition.upper is not None:
            span = float(definition.upper) - float(definition.lower)
            if span > 0:
                total += abs(float(a) - float(b)) / span
            else:
                total += 0.0 if a == b else 1.0
        else:
            total += abs(float(a) - float(b))
    return total


def count_changed_params(
    defs: Sequence[ParamDef], left: dict, right: dict
) -> int:
    """Number of parameters whose values differ (missing counts as changed)."""
    return sum(
        1
        for definition in defs
        if left.get(definition.name) != right.get(definition.name)
    )
