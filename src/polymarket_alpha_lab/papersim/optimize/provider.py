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

Crypto lineages have no registered family until MO-5d; their frozen
baseline spec is defined HERE (same field shape as
``soccer_frozen.baseline_spec()``: nothing to tune, no envelope, the
legacy crypto version string).  MO-5d/MO-7 should consume or replace
this definition so the spec identity stays single-sourced.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, runtime_checkable

from polymarket_alpha_lab.papersim import pipeline
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec

__all__ = [
    "CRYPTO_FROZEN_FAMILY_ID",
    "CRYPTO_FROZEN_PROCEDURE_VERSION",
    "FALLBACK_REGISTRY_EMPTY",
    "FrozenBaselineProvider",
    "LineageSpecs",
    "SpecProvider",
    "crypto_frozen_baseline",
]

#: The fallback reason recorded in the scan parameters (04 section 10
#: step 1: when the registry cannot be read everything falls back to
#: the baseline and the scan parameters record the fallback).  B1 has
#: exactly this one reason -- the registry does not exist yet; MO-7
#: adds the read-failure / checksum-mismatch / unknown-family variants.
FALLBACK_REGISTRY_EMPTY = "registry_empty"

#: Registered family singletons (module name, attribute).  The registry
#: provider (MO-7) replaces this table with the MO-2 registry read; the
#: family modules themselves stay lazy (PEP 562, see
#: ``optimize/families/__init__.py``).
_FAMILY_SINGLETONS: dict[str, tuple[str, str]] = {
    "club-soccer-frozen": ("soccer_frozen", "SOCCER_FROZEN"),
}

#: Lineage prefix -> family id (a lineage is family x target x range,
#: design 04 section 3).  ``None`` marks a family no MO-5 adapter has
#: registered yet -- its frozen baseline is defined in this module.
_LINEAGE_FAMILIES: tuple[tuple[str, str | None], ...] = (
    ("club-soccer", "club-soccer-frozen"),
    ("crypto", None),
)

CRYPTO_FROZEN_FAMILY_ID = "crypto-gbm"
CRYPTO_FROZEN_PROCEDURE_VERSION = "crypto-r19"


def crypto_frozen_baseline() -> ModelSpec:
    """The frozen crypto baseline spec (no registered family until MO-5d).

    Same field shape as ``soccer_frozen.baseline_spec()``: empty
    params (the crypto model's variant pair is code-frozen inside
    ``crypto_model``), an empty envelope, and the legacy
    queue-partition string so the empty-registry scan is bit-identical
    to the unwired stack.
    """

    return ModelSpec(
        family=CRYPTO_FROZEN_FAMILY_ID,
        procedure_version=CRYPTO_FROZEN_PROCEDURE_VERSION,
        params={},
        envelope=(),
        data_contract="crypto-frozen-v1",
        legacy_model_version=pipeline.MODEL_VERSION_CRYPTO,
    )


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
    """

    active: ModelSpec
    baseline: ModelSpec
    candidates: tuple[ModelSpec, ...] = ()
    guard: ModelSpec | None = None
    fallback_reason: str | None = None


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
    ``baseline_spec()``; unregistered ones (crypto until MO-5d) use the
    frozen baseline defined here.  ``fallback_reason`` is always set --
    with no registry there is nothing else to read (04 section 11), and
    the scan records it in its parameters.
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
