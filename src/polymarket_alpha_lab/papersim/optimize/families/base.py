"""The model-family interface (design 04, section 8; plan 05, node MO-1)."""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec, ParamDef

__all__ = ["ModelFamily"]


@runtime_checkable
class ModelFamily(Protocol):
    """Uniform interface of one model family.

    A family is one model plus its estimation procedure (design 04,
    section 3): "club-soccer frozen production", "club-soccer
    recalibration estimator", "international Elo-Poisson", "NBA
    pace-efficiency", "crypto GBM". Implementations are structural:
    they provide the attributes and methods below without subclassing
    anything; ``runtime_checkable`` verifies presence, not signatures.

    Every family defines its own concrete ``history`` and ``fixture``
    payloads (MO-1 uses ``SoccerFrozenHistory``/``SoccerFrozenFixture``
    dataclasses; MO-5 families follow the same pattern), so the
    protocol types them loosely as mappings.
    """

    family_id: str
    """Stable slug, e.g. "club-soccer-frozen"."""

    procedure_version: str
    """Version of the estimation CODE (not of the parameters)."""

    def param_space(self) -> tuple[ParamDef, ...]:
        """Tunable parameters; the frozen production families return ``()``."""
        ...

    def baseline_spec(self) -> ModelSpec:
        """The built-in frozen baseline spec of this family."""
        ...

    def targets(self) -> tuple[str, ...]:
        """Market types this family scores (a lineage is family x target x range)."""
        ...

    def fit(
        self, history: Mapping[str, object], spec: ModelSpec, as_of: datetime.datetime
    ) -> object:
        """Bind one spec to history at ``as_of``; returns an opaque fitted state.

        ``as_of`` is the knowledge cutoff for provenance; a frozen
        family re-estimates nothing.
        """
        ...

    def predict(self, fitted: object, fixture: Mapping[str, object]) -> object | None:
        """Predict for one fixture; ``None`` is an explicit refusal, never a fallback."""
        ...
