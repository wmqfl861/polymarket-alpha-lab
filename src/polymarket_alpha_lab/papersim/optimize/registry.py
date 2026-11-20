"""The pure model-registry fold: role events -> lineage role views.

MO-2 of the model auto-optimization line (design
``docs/handoffs/2026-11-19-audit-optimizer/04-optimizer-design.md``
sections 3 and 9; plan ``05-development-plan.md`` node MO-2).
Everything here is pure: standard library only, no network, no
database driver, no clock reads, no file I/O, and deliberately no
import of ``papersim.store`` or the family modules -- the store
adapter converts database rows into :class:`RoleEvent` and the
provider (MO-7) turns views back into specs.

Lineage identity invariant (MO-2 plan review P2): a lineage id is
``family_id + '/' + target``. Design 04 section 3 defines a lineage as
family x market type x RANGE, and MO-2 compresses the range dimension
into the family id under one pinned rule: **one family never spans
ranges -- a new range means a NEW family id** (e.g. a future domestic
vs cross-league split registers two families, never two ranges of one
family). ``tests/test_papersim_optimize_registry.py`` asserts the
current single-range fact for every registered family so any deviation
from this rule must land as a deliberate, reviewed change.

Batch semantics: the registry never sees a flat event stream. Events
arrive in BATCHES, one batch per writing transaction
(:class:`papersim.store.RoleEventBatch`). The promotion handover rule
of 05 node MO-2 -- the old active turning GUARD and the candidate
turning ACTIVE must be in the SAME transaction -- is therefore the
statement that a handover batch contains BOTH events, and
:func:`validate_role_event_batch` rejects every batch shape that is
not one of the five legal ones (initialization, candidate
registration, retirement, handover, rollback).

Checked invariants (05 node MO-2): exactly one active per lineage; the
baseline exists forever once written; at most two candidates; at most
one guard; handover events share one batch; an UNFREEZE control event
is never decided by an automatic rule.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

__all__ = [
    "RegistryInvariantError",
    "RoleEvent",
    "LineageRoles",
    "ControlEventRecord",
    "lineage_id",
    "validate_role_event_batch",
    "derive_lineage_roles",
    "group_events_by_lineage",
    "validate_control_event",
]

#: Roles tracked per lineage (04 section 3). ACTIVE is unique, BASELINE
#: is permanent, CANDIDATE caps at two, GUARD caps at one, RETIRED is
#: the terminal label.
ROLE_TOKENS = ("ACTIVE", "BASELINE", "CANDIDATE", "GUARD", "RETIRED")

#: How many candidates a lineage may hold at once (04 section 3).
MAX_CANDIDATES = 2


class RegistryInvariantError(ValueError):
    """A role-event sequence violates a registry invariant.

    Fixed public error codes only (``papersim_registry_*``); never
    provider output, credentials or row data.
    """


def _fail(code: str):
    raise RegistryInvariantError(code) from None


def lineage_id(family_id: str, target: str) -> str:
    """The lineage id of one family/target pair: ``family_id '/' target``.

    The range dimension is folded into the family id (see the module
    docstring invariant: one family never spans ranges -- a new range
    is a new family id, never a third path segment).
    """

    if not isinstance(family_id, str) or not family_id \
            or not isinstance(target, str) or not target:
        _fail("papersim_registry_lineage_id_invalid")
    return f"{family_id}/{target}"


@dataclass(frozen=True)
class RoleEvent:
    """One persisted role-change event as the registry sees it.

    ``role_event_id`` (when present) is the database sequence value;
    the fold uses it only as a stable tie-break inside one
    ``effective_at`` second.
    """

    lineage: str
    spec_sha256: str
    role: str
    effective_at: datetime
    reason: str
    forward_plan: Mapping[str, object] | None = None
    decision_id: int | None = None
    actor: str = ""
    code_commit: str = ""
    role_event_id: int | None = None

    def __post_init__(self) -> None:
        if self.role not in ROLE_TOKENS:
            _fail("papersim_registry_role_invalid")
        if not isinstance(self.lineage, str) or not self.lineage:
            _fail("papersim_registry_lineage_invalid")
        if not isinstance(self.spec_sha256, str) or len(self.spec_sha256) != 64 \
                or any(c not in "0123456789abcdef" for c in self.spec_sha256):
            _fail("papersim_registry_spec_invalid")
        if not isinstance(self.effective_at, datetime) \
                or self.effective_at.tzinfo is None:
            _fail("papersim_registry_time_invalid")
        if self.role == "CANDIDATE" and self.forward_plan is None:
            _fail("papersim_registry_forward_plan_missing")


@dataclass(frozen=True)
class LineageRoles:
    """The current role view of one lineage (the fold result)."""

    lineage: str
    baseline: str
    active: str
    candidates: tuple[str, ...] = ()
    guard: str | None = None
    retired: tuple[str, ...] = ()
    batches: int = 1

    def as_dict(self) -> dict:
        return {"lineage": self.lineage, "baseline": self.baseline,
                "active": self.active, "candidates": list(self.candidates),
                "guard": self.guard, "retired": list(self.retired)}


def _state_after_initialization(batch: Sequence[RoleEvent]) -> LineageRoles:
    """The one legal first batch: [BASELINE(spec), ACTIVE(spec)], same spec."""

    if len(batch) != 2:
        _fail("papersim_registry_initialization_shape")
    first, second = batch
    if first.role != "BASELINE" or second.role != "ACTIVE":
        _fail("papersim_registry_initialization_shape")
    if first.spec_sha256 != second.spec_sha256:
        _fail("papersim_registry_initialization_spec")
    return LineageRoles(lineage=first.lineage, baseline=first.spec_sha256,
                        active=second.spec_sha256)


def _validate_candidate_registration(prior: LineageRoles,
                                     batch: Sequence[RoleEvent]) -> LineageRoles:
    """``[CANDIDATE(spec)]`` with a forward plan, room under the cap."""

    if len(batch) != 1 or batch[0].role != "CANDIDATE":
        _fail("papersim_registry_batch_shape")
    event = batch[0]
    spec = event.spec_sha256
    if spec in (prior.active, prior.baseline) or spec in prior.candidates \
            or spec in prior.retired:
        _fail("papersim_registry_candidate_not_new")
    if len(prior.candidates) >= MAX_CANDIDATES:
        _fail("papersim_registry_candidate_cap")
    return LineageRoles(lineage=prior.lineage, baseline=prior.baseline,
                        active=prior.active,
                        candidates=prior.candidates + (spec,),
                        guard=prior.guard, retired=prior.retired,
                        batches=prior.batches + 1)


def _validate_retirement(prior: LineageRoles,
                         batch: Sequence[RoleEvent]) -> LineageRoles:
    """``[RETIRED(spec)]`` retiring one current candidate or the guard."""

    if len(batch) != 1 or batch[0].role != "RETIRED":
        _fail("papersim_registry_batch_shape")
    spec = batch[0].spec_sha256
    if spec not in prior.candidates and spec != prior.guard:
        _fail("papersim_registry_retire_not_held")
    candidates = tuple(s for s in prior.candidates if s != spec)
    guard = None if spec == prior.guard else prior.guard
    retired = prior.retired + (spec,)
    return LineageRoles(lineage=prior.lineage, baseline=prior.baseline,
                        active=prior.active, candidates=candidates, guard=guard,
                        retired=retired, batches=prior.batches + 1)


def _validate_handover(prior: LineageRoles,
                       batch: Sequence[RoleEvent]) -> LineageRoles:
    """The promotion handover batch: old active -> GUARD, candidate -> ACTIVE.

    05 node MO-2: the two events MUST share one batch (one database
    transaction at the writer). A previous guard, if present, must
    retire in the SAME batch (the guard cap stays at one). The
    promoted spec must be a registered candidate; the demoted active
    must be exactly the current active.

    MO-8 tightening (MO-2 code-review P3 follow-up; the real handover
    writer arrives with MO-8's ``apply``): a handover batch may carry a
    RETIRED event ONLY to retire a PRESENT previous guard. With no
    prior guard a stray RETIRED event used to pass the shape check and
    was silently dropped from the fold -- it now fails with a fixed
    code instead.
    """

    retired = tuple(event for event in batch if event.role == "RETIRED")
    guards = tuple(event for event in batch if event.role == "GUARD")
    actives = tuple(event for event in batch if event.role == "ACTIVE")
    others = tuple(event for event in batch
                   if event.role not in ("RETIRED", "GUARD", "ACTIVE"))
    if others or len(guards) != 1 or len(actives) != 1 or len(retired) > 1:
        _fail("papersim_registry_batch_shape")
    promoted = actives[0]
    if promoted.spec_sha256 not in prior.candidates:
        _fail("papersim_registry_handover_not_candidate")
    if guards[0].spec_sha256 != prior.active:
        _fail("papersim_registry_handover_guard_mismatch")
    if prior.guard is not None:
        # A second handover retires the old guard inside the SAME batch.
        if not retired or retired[0].spec_sha256 != prior.guard:
            _fail("papersim_registry_handover_guard_cap")
        retired_specs = prior.retired + (prior.guard,)
    else:
        # MO-8 tightening: no previous guard means the batch has no
        # legal RETIRED event at all (a stray target was previously
        # accepted and silently dropped -- never again).
        if retired:
            _fail("papersim_registry_handover_stray_retired")
        retired_specs = prior.retired
    candidates = tuple(s for s in prior.candidates if s != promoted.spec_sha256)
    return LineageRoles(lineage=prior.lineage, baseline=prior.baseline,
                        active=promoted.spec_sha256, candidates=candidates,
                        guard=guards[0].spec_sha256, retired=retired_specs,
                        batches=prior.batches + 1)


def _validate_rollback(prior: LineageRoles,
                       batch: Sequence[RoleEvent]) -> LineageRoles:
    """The rollback batch: ``[RETIRED(active), ACTIVE(guard)]`` (MO-8).

    Rolling back TO the baseline (the guard being the frozen baseline
    spec) is legal: the baseline keeps its BASELINE event and simply
    holds ACTIVE again, exactly as it did at initialization.
    """

    if len(batch) != 2:
        _fail("papersim_registry_batch_shape")
    retired_events = tuple(event for event in batch if event.role == "RETIRED")
    actives = tuple(event for event in batch if event.role == "ACTIVE")
    if len(retired_events) != 1 or len(actives) != 1:
        _fail("papersim_registry_batch_shape")
    if retired_events[0].spec_sha256 != prior.active:
        _fail("papersim_registry_rollback_active_mismatch")
    if prior.guard is None or actives[0].spec_sha256 != prior.guard:
        _fail("papersim_registry_rollback_guard_mismatch")
    return LineageRoles(lineage=prior.lineage, baseline=prior.baseline,
                        active=prior.guard,
                        candidates=prior.candidates,
                        guard=None,
                        retired=prior.retired + (prior.active,),
                        batches=prior.batches + 1)


def validate_role_event_batch(prior: LineageRoles | None,
                              batch: Sequence[RoleEvent]) -> LineageRoles:
    """Validate one transaction's events against ``prior``; return the new view.

    ``prior`` is ``None`` only for a lineage's first batch, which must
    be the initialization pair. Every later batch must be exactly one
    of the five legal shapes; anything else -- a lone ACTIVE flip, a
    second BASELINE, a handover missing its GUARD event, an unknown
    role mix -- fails with a fixed ``papersim_registry_*`` code.
    """

    if not isinstance(batch, Sequence) or not batch:
        _fail("papersim_registry_batch_empty")
    for event in batch:
        if not isinstance(event, RoleEvent):
            _fail("papersim_registry_event_type")
    lineages = {event.lineage for event in batch}
    if len(lineages) != 1:
        _fail("papersim_registry_batch_lineage")
    if prior is not None and next(iter(lineages)) != prior.lineage:
        _fail("papersim_registry_batch_lineage")
    stamps = {event.effective_at for event in batch}
    if len(stamps) != 1:
        _fail("papersim_registry_batch_time")
    if prior is None:
        return _state_after_initialization(batch)
    roles = [event.role for event in batch]
    if "BASELINE" in roles:
        _fail("papersim_registry_duplicate_baseline")
    if "ACTIVE" in roles:
        if "GUARD" in roles:
            return _validate_handover(prior, batch)
        if roles.count("RETIRED") == 1 and len(batch) == 2:
            return _validate_rollback(prior, batch)
        _fail("papersim_registry_batch_shape")
    if roles == ["CANDIDATE"]:
        return _validate_candidate_registration(prior, batch)
    if roles == ["RETIRED"]:
        return _validate_retirement(prior, batch)
    _fail("papersim_registry_batch_shape")


def derive_lineage_roles(batches: Sequence[Sequence[RoleEvent]]) -> LineageRoles | None:
    """Fold all of one lineage's batches into its current role view.

    An EMPTY batch sequence returns ``None`` -- the registry-empty
    signal the provider (MO-7) turns into "every lineage falls back to
    its family baseline" (04 section 11). Any illegal batch raises
    :class:`RegistryInvariantError`; the caller treats that as
    read-failure fallback to the baseline as well, never as a scan
    stopper.
    """

    if not batches:
        return None
    view: LineageRoles | None = None
    for batch in batches:
        view = validate_role_event_batch(view, batch)
    assert view is not None
    return view


def group_events_by_lineage(
    batches: Sequence[Sequence[RoleEvent]],
) -> dict[str, list[list[RoleEvent]]]:
    """Group mixed-lineage batches per lineage preserving order.

    The store adapter loads all role events ordered by
    ``(lineage, effective_at, role_event_id)`` and groups them into
    batches: consecutive events of one lineage sharing the same
    ``effective_at`` form one batch (the writer's transaction wrote
    them together). Reconstructed batches feed
    :func:`derive_lineage_roles` per lineage.
    """

    grouped: dict[str, list[RoleEvent]] = {}
    for batch in batches:
        for event in batch:
            grouped.setdefault(event.lineage, []).append(event)
    result: dict[str, list[list[RoleEvent]]] = {}
    for lineage, events in grouped.items():
        events.sort(key=lambda e: (e.effective_at,
                                   e.role_event_id if e.role_event_id is not None else 0))
        batches_out: list[list[RoleEvent]] = []
        current: list[RoleEvent] = []
        current_stamp = None
        for event in events:
            if current and event.effective_at != current_stamp:
                batches_out.append(current)
                current = []
            current.append(event)
            current_stamp = event.effective_at
        if current:
            batches_out.append(current)
        result[lineage] = batches_out
    return result


@dataclass(frozen=True)
class ControlEventRecord:
    """One optimizer freeze/unfreeze record as the registry sees it."""

    scope: str
    lineage: str | None
    control_kind: str
    reason: str
    actor: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        if self.control_kind not in ("FREEZE", "UNFREEZE"):
            _fail("papersim_registry_control_kind_invalid")
        if self.scope not in ("GLOBAL", "LINEAGE"):
            _fail("papersim_registry_control_scope_invalid")
        if (self.scope == "LINEAGE") != (self.lineage is not None):
            _fail("papersim_registry_control_scope_lineage")
        if not isinstance(self.occurred_at, datetime) \
                or self.occurred_at.tzinfo is None:
            _fail("papersim_registry_time_invalid")


def validate_control_event(control_kind: str, actor: str) -> None:
    """An UNFREEZE is never decided by an automatic rule (04 section 9).

    Checked here (pure registry), again in
    ``store.OptimizerControlEventSubmission.validate`` and enforced a
    third time by the database CHECK on
    ``papersim.optimizer_control_events`` -- three layers, one rule.
    """

    if control_kind not in ("FREEZE", "UNFREEZE"):
        _fail("papersim_registry_control_kind_invalid")
    if not isinstance(actor, str) or not actor:
        _fail("papersim_registry_control_actor_invalid")
    if control_kind == "UNFREEZE" and actor.startswith("auto:"):
        _fail("papersim_registry_unfreeze_actor_invalid")
