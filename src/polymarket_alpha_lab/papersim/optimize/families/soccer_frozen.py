"""The frozen production soccer model wrapped as a zero-parameter family.

MO-1 (design 04 sections 3, 7 and 8; plan 05 node MO-1). The frozen
ten-variant xG-Poisson round-23 model IS the baseline of every
club-soccer lineage: its source (``papersim.soccer_model``) is never
modified by the optimization line, only wrapped. ``param_space()`` is
empty -- there is nothing to tune -- and ``fit`` accepts exactly one
spec, the baseline.

The baseline's model-version string is ``pipeline.MODEL_VERSION_SOCCER``
("soccer-r23"), referenced from the pipeline module and never copied
as a literal, so the queue identity of existing paper data is
preserved verbatim (``EntryDecision.model_version`` ->
``store.py`` candidate rows -> evaluation queue partitioning).
"""

from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Mapping
from types import MappingProxyType

from polymarket_alpha_lab.papersim import pipeline, soccer_model
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec, ParamDef, freeze_value

__all__ = [
    "DATA_CONTRACT",
    "FAMILY_ID",
    "PROCEDURE_VERSION",
    "SOCCER_FROZEN",
    "TARGETS",
    "SoccerFrozenFamily",
    "SoccerFrozenFitted",
    "SoccerFrozenFixture",
    "SoccerFrozenHistory",
]

#: Family id (design 04 section 3: "club soccer, xG-Poisson, frozen production").
FAMILY_ID = "club-soccer-frozen"

#: Version of the estimation CODE (the frozen SWEEPS definition), not
#: of the parameters. The lineage head is r23 (papersim package docstring).
PROCEDURE_VERSION = "r23"

#: Forward placeholder: the dataset layer (MO-4) finalizes the data
#: contract. Freely adjustable only until the FIRST REAL
#: ``optimize init`` writes baseline specs into the registry (MO-6
#: wires that command); after that, changing it re-identifies every
#: stored baseline spec.
DATA_CONTRACT = "understat-xg-snapshot-v1"

#: Market types this family scores. ``match_winner`` is suspended in
#: r23 (papersim package provenance note) and is not a scoring target.
TARGETS: tuple[str, ...] = ("over_under", "btts", "team_total")


@dataclasses.dataclass(frozen=True)
class SoccerFrozenHistory:
    """Fit inputs: one frozen xG snapshot plus the optional bridge table.

    ``xg_snapshot`` is what ``xg.load_xg_payload`` returns;
    ``bridge`` is the optional WP-P2a league-strength kappa table
    threaded into every variant's ``pair_lambdas`` call.
    """

    xg_snapshot: Mapping[str, Mapping[str, object]]
    bridge: Mapping[str, float] | None = None


@dataclasses.dataclass(frozen=True)
class SoccerFrozenFixture:
    """One fixture to predict; fields mirror ``soccer_model.fixture_variants``."""

    home_team: str
    home_league: str
    away_team: str
    away_league: str
    season: str | None = None


@dataclasses.dataclass(frozen=True)
class SoccerFrozenFitted:
    """Fitted state: the (frozen) history plus the spec identity it is bound to."""

    history: SoccerFrozenHistory
    baseline_sha256: str


def _envelope() -> tuple[Mapping[str, object], ...]:
    """The ten sweep variants, mechanically derived from ``soccer_model.SWEEPS``.

    Order is the lineage order -- variant names are regression keys
    (soccer_model.py SWEEPS comment) -- and is part of the spec
    identity. ``None`` weights (pure current/last modes) survive as
    ``None``; canonical text spells them ``null``.
    """

    return tuple(
        MappingProxyType(dataclasses.asdict(spec)) for spec in soccer_model.SWEEPS
    )


class SoccerFrozenFamily:
    """``ModelFamily`` implementation of the frozen production model (structural)."""

    family_id = FAMILY_ID
    procedure_version = PROCEDURE_VERSION

    def param_space(self) -> tuple[ParamDef, ...]:
        """Nothing to tune: the ten-variant definition is frozen."""

        return ()

    def baseline_spec(self) -> ModelSpec:
        """The built-in baseline: empty params, ten-variant envelope, legacy version."""

        return ModelSpec(
            family=FAMILY_ID,
            procedure_version=PROCEDURE_VERSION,
            params={},
            envelope=_envelope(),
            data_contract=DATA_CONTRACT,
            legacy_model_version=pipeline.MODEL_VERSION_SOCCER,
        )

    def targets(self) -> tuple[str, ...]:
        return TARGETS

    def fit(
        self,
        history: SoccerFrozenHistory,
        spec: ModelSpec,
        as_of: datetime.datetime,
    ) -> SoccerFrozenFitted:
        """Bind the baseline spec to ``history``; any other spec is refused.

        ``as_of`` is recorded for provenance only: the frozen family's
        state is the snapshot itself, nothing is re-estimated per
        date. The history mappings are deep-frozen into the fitted
        state so the recorded spec identity cannot drift from the
        data actually used.
        """

        if not isinstance(history, SoccerFrozenHistory):
            raise TypeError(
                f"{FAMILY_ID}.fit expects SoccerFrozenHistory, "
                f"got {type(history).__name__}"
            )
        baseline = self.baseline_spec()
        if spec.spec_sha256() != baseline.spec_sha256():
            raise ValueError(
                f"{FAMILY_ID} is frozen: only the baseline spec "
                f"(sha256 {baseline.spec_sha256()}) is fittable, got "
                f"{spec.spec_sha256()} for family {spec.family!r}"
            )
        frozen_history = SoccerFrozenHistory(
            xg_snapshot=freeze_value(history.xg_snapshot),
            bridge=(
                freeze_value(history.bridge) if history.bridge is not None else None
            ),
        )
        return SoccerFrozenFitted(
            history=frozen_history, baseline_sha256=baseline.spec_sha256()
        )

    def predict(
        self, fitted: SoccerFrozenFitted, fixture: SoccerFrozenFixture
    ) -> tuple[soccer_model.VariantResult, ...] | None:
        """Evaluate all ten variants for one fixture; ``None`` is a refusal.

        Pure passthrough of ``soccer_model.fixture_variants``: the
        cross-league guard, missing season blocks and unknown teams
        refuse exactly as in the frozen model.
        """

        if not isinstance(fitted, SoccerFrozenFitted):
            raise TypeError(
                f"{FAMILY_ID}.predict expects SoccerFrozenFitted, "
                f"got {type(fitted).__name__}"
            )
        if not isinstance(fixture, SoccerFrozenFixture):
            raise TypeError(
                f"{FAMILY_ID}.predict expects SoccerFrozenFixture, "
                f"got {type(fixture).__name__}"
            )
        return soccer_model.fixture_variants(
            fitted.history.xg_snapshot,
            fixture.home_team,
            fixture.home_league,
            fixture.away_team,
            fixture.away_league,
            season=fixture.season,
            bridge=fitted.history.bridge,
        )


#: Module-level singleton family instance.
SOCCER_FROZEN = SoccerFrozenFamily()
