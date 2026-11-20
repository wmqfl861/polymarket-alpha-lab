"""B1 scan-time model-spec provider: the frozen interface plus the empty-registry fallback.

Design 04 section 10 and plan 05 section 2 (the B1 special
requirements registered in ``DELIVERY_PLAN.md`` 80.8): ``run-scan``
takes every model specification through this interface from day one,
so the scan never needs rewiring when the optimizer's registry (MO-2
storage, MO-7 registry implementation) arrives.  MO-7 extends THIS
file with the registry-backed provider -- it is the extender, not the
creator -- and swaps the one construction point inside the B1 scan
orchestration module; this fallback implementation then remains the
failure mode of every registry read (design 04 section 11: an empty
registry, a read failure, a spec-checksum mismatch and an unknown
family version ALL fall back to the frozen baseline and leave a
record; an optimizer fault never stops the scan).

Bit-identity anchor (plan 05 section 2, B1 row): with the registry
empty every lineage has exactly one role -- the active spec IS the
family baseline -- and ``ModelSpec.model_version()`` returns the
pipeline's frozen constants (``MODEL_VERSION_SOCCER`` /
``MODEL_VERSION_CRYPTO``), so every candidate the scan records carries
the same queue-partition string the unwired stack produces.

Crypto lineages: the MO-5d family (``optimize/families/crypto.py``)
registered its baseline, so the local placeholder definition B1
carried ("MO-5d/MO-7 should consume or replace this definition so the
spec identity stays single-sourced") is RETIRED --
``crypto_frozen_baseline()`` below is a thin delegation to the family
and the spec identity lives in exactly one place.  The delegation
carries the estimator default
params (empty at B1 only because no family existed yet); the baseline
``model_version()`` still returns ``pipeline.MODEL_VERSION_CRYPTO``,
so the empty-registry scan keeps the queue-partition string and the
B1 bit-identity anchor holds.  The MO-2 registry is empty (no stored
spec references the old placeholder sha), so the re-identification is
free of existing data.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from importlib import import_module
from typing import Protocol, runtime_checkable

from polymarket_alpha_lab.papersim.optimize.registry import MAX_CANDIDATES
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec

__all__ = [
    "CRYPTO_FROZEN_FAMILY_ID",
    "CRYPTO_FROZEN_PROCEDURE_VERSION",
    "FALLBACK_FAMILY_VERSION_UNKNOWN",
    "FALLBACK_LINEAGE_NOT_INITIALIZED",
    "FALLBACK_REGISTRY_EMPTY",
    "FALLBACK_REGISTRY_READ_FAILED",
    "FALLBACK_SPEC_CHECKSUM_MISMATCH",
    "FrozenBaselineProvider",
    "LineageSpecs",
    "RegistryProvider",
    "SpecProvider",
    "crypto_frozen_baseline",
    "family_ids_for_prefix",
    "scan_prefix_of",
]

#: The fallback reason recorded in the scan parameters (04 section 10
#: step 1: when the registry cannot be read everything falls back to
#: the baseline and the scan parameters record the fallback).  B1 has
#: exactly this one reason -- the registry does not exist yet; MO-7
#: adds the read-failure / checksum-mismatch / unknown-family variants.
FALLBACK_REGISTRY_EMPTY = "registry_empty"

#: MO-7 fallback reasons (design 04 section 11: an empty registry, a
#: read failure, a spec-checksum mismatch and an unknown family
#: version ALL fall back to the frozen baseline and leave a record).
#: ``lineage_not_initialized`` additionally covers a non-empty registry
#: that simply holds no row set for the consulted scan lineage (bare
#: prefix, range suffix, or a family never initialized).
FALLBACK_REGISTRY_READ_FAILED = "registry_read_failed"
FALLBACK_LINEAGE_NOT_INITIALIZED = "lineage_not_initialized"
FALLBACK_SPEC_CHECKSUM_MISMATCH = "spec_checksum_mismatch"
FALLBACK_FAMILY_VERSION_UNKNOWN = "family_version_unknown"

#: Registered family singletons (module name, attribute).  The registry
#: provider (MO-7) replaces this table with the MO-2 registry read; the
#: family modules themselves stay lazy (PEP 562, see
#: ``optimize/families/__init__.py``).  MO-5d registered the crypto
#: family (single-sourced baseline; see ``crypto_frozen_baseline``).
_FAMILY_SINGLETONS: dict[str, tuple[str, str]] = {
    "club-soccer-frozen": ("soccer_frozen", "SOCCER_FROZEN"),
    "crypto-gbm": ("crypto", "CRYPTO_GBM"),
}

#: Lineage prefix -> family id (a lineage is family x target x range,
#: design 04 section 3).  ``None`` marks a family no MO-5 adapter has
#: registered yet -- its frozen baseline is defined in this module
#: (vacuous since MO-5d; the mechanism stays for future families).
_LINEAGE_FAMILIES: tuple[tuple[str, str | None], ...] = (
    ("club-soccer", "club-soccer-frozen"),
    ("crypto", "crypto-gbm"),
)

#: Crypto family id and estimation-code generation, kept as the frozen
#: public seam B1 published.  MUST equal ``families.crypto.FAMILY_ID``
#: and ``families.crypto.PROCEDURE_VERSION``; the family-side and
#: provider-side tests pin the equality.
CRYPTO_FROZEN_FAMILY_ID = "crypto-gbm"
CRYPTO_FROZEN_PROCEDURE_VERSION = "crypto-r19e1"


def crypto_frozen_baseline() -> ModelSpec:
    """The frozen crypto baseline spec (delegated to the MO-5d family).

    Thin delegation to ``families.crypto.CRYPTO_GBM.baseline_spec()``:
    the identity lives in the family, single-sourced (B1's local
    definition was consumed on MO-5d landing).  The baseline carries
    the estimator default params (window mode, 30d single window --
    the r19 pricing path), an empty envelope (the A/B drift
    conventions are the code-frozen sensitivity band), and the legacy
    queue-partition string so the empty-registry scan keeps today's
    model-version identity.
    """

    return _registered_baseline(CRYPTO_FROZEN_FAMILY_ID)


@dataclass(frozen=True)
class LineageSpecs:
    """The role specs of one lineage at one scan (04 section 10 step 1).

    ``active`` is the spec that prices and enters; ``baseline`` is the
    family's permanent baseline; ``candidates``/``guard`` are the other
    roles (empty / ``None`` until MO-7's registry).  ``fallback_reason``
    records why the roles collapsed to the baseline (``None`` once the
    registry resolves real roles).  A scan consults the provider ONCE
    and freezes these specs for the whole run (step 5: a role change
    takes effect only between scans).

    ``registry`` (MO-7) carries the consulted registry lineages as
    ``(lineage_id, active_sha256, candidate_sha256s)`` tuples for the
    scan parameters: it makes estimator-lineage promotions VISIBLE
    without consuming them (the MO-8 boundary note) and keeps
    merged-candidate truncation auditable.  ``None`` on every
    fallback path and on the frozen-baseline provider, so B1 shapes
    compare equal.
    """

    active: ModelSpec
    baseline: ModelSpec
    candidates: tuple[ModelSpec, ...] = ()
    guard: ModelSpec | None = None
    fallback_reason: str | None = None
    registry: tuple[tuple[str, str, tuple[str, ...]], ...] | None = None


@runtime_checkable
class SpecProvider(Protocol):
    """The scan-side spec interface (frozen for B1; MO-7 adds the registry version)."""

    def specs_for_lineage(self, lineage: str) -> LineageSpecs:
        """Every role spec of one lineage, or the frozen baseline with a fallback record."""
        ...


def _registered_baseline(family_id: str) -> ModelSpec:
    module_name, attribute = _FAMILY_SINGLETONS[family_id]
    module = import_module(
        f"polymarket_alpha_lab.papersim.optimize.families.{module_name}")
    return getattr(module, attribute).baseline_spec()


class FrozenBaselineProvider:
    """The empty-registry provider: every lineage collapses to its frozen baseline.

    Registered families (``optimize.families``) contribute their
    ``baseline_spec()`` (both current lineages are registered since
    MO-5d); a lineage whose family has no adapter yet (the ``None``
    mapping in ``_LINEAGE_FAMILIES``) uses the frozen baseline defined
    in this module.  ``fallback_reason`` is always set -- with no
    registry there is nothing else to read (04 section 11), and the
    scan records it in its parameters.
    """

    def specs_for_lineage(self, lineage: str) -> LineageSpecs:
        text = str(lineage or "").strip()
        if not text:
            raise ValueError("papersim_provider_lineage_invalid")
        for prefix, family_id in _LINEAGE_FAMILIES:
            if text == prefix or text.startswith(prefix + "/"):
                if family_id is None:
                    baseline = crypto_frozen_baseline()
                else:
                    baseline = _registered_baseline(family_id)
                return LineageSpecs(
                    active=baseline,
                    baseline=baseline,
                    candidates=(),
                    guard=None,
                    fallback_reason=FALLBACK_REGISTRY_EMPTY,
                )
        raise ValueError(f"papersim_provider_lineage_unknown:{lineage!r}")


# ---------------------------------------------------------------------------
# MO-7: the registry-backed provider (plan 05 node MO-7, plan review R2)
# ---------------------------------------------------------------------------

def scan_prefix_of(lineage: str) -> tuple[str, str | None]:
    """Split one scan lineage into ``(prefix, target-or-None)``.

    Same prefix vocabulary and same refusals as
    :class:`FrozenBaselineProvider` (the B1 behavior is unchanged): an
    empty/blank lineage and an unknown prefix are configuration
    errors.  A bare prefix (``"club-soccer"``) yields ``target=None``
    -- accepted, never an error (the pinned B1 provider tests).
    """

    text = str(lineage or "").strip()
    if not text:
        raise ValueError("papersim_provider_lineage_invalid")
    for prefix, _family_id in _LINEAGE_FAMILIES:
        if text == prefix:
            return prefix, None
        if text.startswith(prefix + "/"):
            return prefix, text[len(prefix) + 1:]
    raise ValueError(f"papersim_provider_lineage_unknown:{lineage!r}")


def family_ids_for_prefix(prefix: str) -> tuple[str, ...]:
    """The deterministic family set of one scan prefix (plan MO-7 rule 2).

    The B1-pinned production family first, then every registered
    family id sharing the ``prefix + "-"`` namespace, lexicographically.
    Today: ``club-soccer -> (club-soccer-frozen, club-soccer-recalib)``,
    ``crypto -> (crypto-gbm,)``.  ``league-bridge`` shares no scan
    prefix and (with ``targets=()``) never yields a registry lineage.
    """

    production = dict(_LINEAGE_FAMILIES)[prefix]
    from polymarket_alpha_lab.papersim.optimize.families import iter_family_ids
    others = sorted(family_id for family_id in iter_family_ids()
                    if family_id != production and family_id.startswith(prefix + "-"))
    return (production,) + tuple(others)


def _referenced_specs(view) -> tuple[str, ...]:
    """Every role digest of one folded lineage view (retired excluded)."""

    digests = [view.active, view.baseline, *view.candidates]
    if view.guard is not None:
        digests.append(view.guard)
    return tuple(digests)


class RegistryProvider:
    """The registry-backed provider: one frozen snapshot per scan.

    Composition over :class:`FrozenBaselineProvider` (plan MO-7
    section 1.1): an EMPTY registry delegates wholesale -- the
    B1-identical ``LineageSpecs`` including ``registry_empty`` -- and
    every fallback path reuses the same frozen baseline construction
    with a specific reason.  ``from_connection`` loads the whole
    registry ONCE (roles folded, every referenced spec rebuilt), so a
    role change never takes effect mid-scan (04 section 10 step 5).

    Complete lineage mapping (plan MO-7 section 1.2, review P1-1):

    1. prefix resolution keeps the B1 vocabulary (unknown -> error);
    2. the family set of a prefix is the production family plus the
       same-namespace estimator families, so e.g. a recalib candidate
       on ``club-soccer-recalib/match_winner`` IS reachable from the
       scan lineage ``club-soccer/match_winner`` even though the
       frozen family has no such target;
    3. the roles merge WITHOUT same-target arbitration because they
       are structurally disjoint: ``active``/``baseline`` are ALWAYS
       the production family baseline (B1 packs are production-quoted;
       the label must stay the pipeline constant), while
       ``candidates``/``guard`` merge from EVERY consulted lineage --
       the estimator families AND the production lineage itself when
       the production family admits tunables (crypto-gbm does; the
       frozen soccer family structurally cannot: its only legal spec
       is the baseline, which the registry's candidate-not-new check
       refuses as a candidate);
    4. the naming-identity invariant (registry.py: a lineage id is
       ``family_id + '/' + target``) is DEFENDED here at read time: a
       lineage whose role specs belong to another family is corruption
       and falls the whole scan lineage back with
       ``family_version_unknown`` -- cross-family candidacy is
       expressed on the estimator's OWN lineage, which rule 3 already
       merges in;
    5. merged candidates sort by spec digest and cap at
       ``MAX_CANDIDATES`` (deterministic under future multi-family
       same-target sets; the consulted lineages stay visible in
       ``LineageSpecs.registry``).

    MO-8 boundary note (plan MO-7 sections 1.2/6.6): a PROMOTED
    estimator active (lineage active != its baseline) is surfaced in
    ``registry`` but NOT consumed -- MO-7's active is definitionally
    the production baseline because no promotion can exist on a
    production-named lineage (rule 4 plus the registry's
    candidate-not-new check).  Consuming promotions is an explicit
    MO-8 obligation that must land together with pack-side
    consultation of the provider; until then a promoted spec simply
    stops shadowing (it leaves ``candidates`` on handover) and the
    state stays visible, never silent.
    """

    def __init__(self, roles, specs, *, spec_failures=None, fallback=None) -> None:
        self._roles = roles
        self._specs = dict(specs)
        self._spec_failures = dict(spec_failures or {})
        self._fallback = fallback if fallback is not None else FrozenBaselineProvider()

    @classmethod
    def from_connection(cls, conn, *, fallback=None) -> "RegistryProvider":
        """Load the registry once; any read failure degrades to fallback.

        An unreadable registry yields the all-fallback instance
        (``roles=None``) -- an optimizer fault never stops the scan
        (04 section 11).  A referenced spec whose row is missing or
        whose canonical text does not round-trip its digest is a
        per-digest failure; the lineage referencing it falls back.
        """

        from polymarket_alpha_lab.papersim import store_psycopg
        from polymarket_alpha_lab.papersim.optimize.registry import (
            derive_lineage_roles,
        )
        from polymarket_alpha_lab.papersim.store import PapersimStoreError
        try:
            grouped = store_psycopg.load_all_role_events(conn)
            roles = {lineage: derive_lineage_roles(batches)
                     for lineage, batches in grouped.items()}
            specs: dict[str, ModelSpec] = {}
            failures: dict[str, str] = {}
            for view in roles.values():
                for digest in _referenced_specs(view):
                    if digest in specs or digest in failures:
                        continue
                    try:
                        spec = store_psycopg.load_model_spec(conn, digest)
                    except PapersimStoreError:
                        spec = None
                    if spec is None:
                        failures[digest] = FALLBACK_SPEC_CHECKSUM_MISMATCH
                    else:
                        specs[digest] = spec
        except Exception:  # noqa: BLE001 - fixed-code degradation, never a scan stopper
            return cls(roles=None, specs={}, fallback=fallback)
        return cls(roles=roles, specs=specs, spec_failures=failures,
                   fallback=fallback)

    def specs_for_lineage(self, lineage: str) -> LineageSpecs:
        prefix, target = scan_prefix_of(lineage)
        if self._roles is None:
            return self._fallen_back(lineage, FALLBACK_REGISTRY_READ_FAILED)
        if not self._roles:
            return self._fallback.specs_for_lineage(lineage)
        if target is None:
            return self._fallen_back(lineage, FALLBACK_LINEAGE_NOT_INITIALIZED)
        production = dict(_LINEAGE_FAMILIES)[prefix]
        consulted: list[tuple[str, str, object]] = []
        for family_id in family_ids_for_prefix(prefix):
            registry_lineage = f"{family_id}/{target}"
            if registry_lineage in self._roles:
                consulted.append((registry_lineage, family_id,
                                  self._roles[registry_lineage]))
        if not consulted:
            return self._fallen_back(lineage, FALLBACK_LINEAGE_NOT_INITIALIZED)
        reason = self._consultation_failure(consulted, production)
        if reason is not None:
            return self._fallen_back(lineage, reason)
        return self._merged(consulted, production)

    def _fallen_back(self, lineage: str, reason: str) -> LineageSpecs:
        base = self._fallback.specs_for_lineage(lineage)
        return replace(base, fallback_reason=reason)

    def _consultation_failure(self, consulted, production: str) -> str | None:
        """The first structural violation across the consulted lineages."""

        for _registry_lineage, family_id, view in consulted:
            if family_id == production:
                baseline = _registered_baseline(family_id)
                if view.baseline != baseline.spec_sha256():
                    return FALLBACK_FAMILY_VERSION_UNKNOWN
            lineage_baseline = self._specs.get(view.baseline)
            if lineage_baseline is None or view.baseline in self._spec_failures:
                return FALLBACK_SPEC_CHECKSUM_MISMATCH
            for digest in _referenced_specs(view):
                if digest in self._spec_failures:
                    return FALLBACK_SPEC_CHECKSUM_MISMATCH
                spec = self._specs.get(digest)
                if spec is None:
                    return FALLBACK_SPEC_CHECKSUM_MISMATCH
                if spec.family != family_id:
                    return FALLBACK_FAMILY_VERSION_UNKNOWN
                if digest != view.baseline \
                        and spec.procedure_version != lineage_baseline.procedure_version:
                    return FALLBACK_FAMILY_VERSION_UNKNOWN
        return None

    def _merged(self, consulted, production: str) -> LineageSpecs:
        baseline_spec = _registered_baseline(production)
        merged: list[tuple[str, ModelSpec]] = []
        for _registry_lineage, _family_id, view in consulted:
            for digest in view.candidates:
                merged.append((digest, self._specs[digest]))
        merged.sort(key=lambda item: item[0])
        candidates = tuple(spec for _digest, spec in merged[:MAX_CANDIDATES])
        guard = None
        for _registry_lineage, _family_id, view in consulted:
            if view.guard is not None:
                guard = self._specs[view.guard]
                break
        registry_view = tuple(
            (registry_lineage, view.active, tuple(view.candidates))
            for registry_lineage, _family_id, view in consulted)
        return LineageSpecs(
            active=baseline_spec, baseline=baseline_spec,
            candidates=candidates, guard=guard,
            fallback_reason=None, registry=registry_view)
