"""The NBA pace/efficiency shadow model wrapped as a tunable family.

MO-5c (design 04 sections 7-8; plan 05 node MO-5c; the reviewed node
plan's P2/P3 fixes are incorporated and cited inline). This module
wraps :mod:`papersim.nba_model` — parameterized by MO-5c so every
knob below is a keyword whose default IS the module constant — and
never imports the ESPN adapter: the history payload is an
already-built frozen-contract snapshot mapping.

Search space (first version, versioned per design 04 section 7: the
grids are frozen HERE and in the node record, never widened after
seeing results):

==================  ============================================
half_life           {10.0, 15.0, 20.0, 28.0, 35.0} (center)
sigma_scale         {0.9, 1.0, 1.1} (ONE knob, both sigmas)
b2b_penalty         {"fit", 1.0, 2.0, 3.0, 4.0}
rest_points_per_day {0.0, 0.25, 0.5}
total_bias          {-2.0, -1.0, 0.0, 1.0, 2.0}
==================  ============================================

Type normalization (MO-1 interface handover, applied here): numeric
knobs are always ``float`` — never ``int`` — so the canonical spec
text never mixes ``i:2`` with ``f:2.0``; the ``b2b_penalty`` choice
set is exactly ``{"fit"}`` plus floats (no other sentinel).

B2B semantics (reviewed plan P2): ``"fit"`` is today's behavior (fit
from eligible team-games, ``DEFAULT_B2B_PENALTY`` fallback); a numeric
choice is a flat assumption that replaces the fit and is reported as
``b2b_penalty_fitted=False``. Every numeric choice lies inside
``nba_model.B2B_PENALTY_CLAMP`` and the family re-checks that bound
against the model constant on every fit — the clamp is the model's
documented fail-safe, and the search space must not quietly escape it.

Rest semantics (reviewed plan P3-5): ``rest_points_per_day == 0.0``
maps to ``use_rest_adjustment=False`` (today's behavior — the module
constant 0.25 documents the magnitude only when enabled); a value
``> 0`` maps to ``use_rest_adjustment=True`` AND passes the value.
The combination "enabled with 0 points" is never produced.

Envelope (design 04 section 7: "the current six-variant structure,
around the new center"): half-lives ``(0.5*h, 1.0*h, 1.75*h)`` x
sigma scales ``(1.0, 1.1)``, named with the model's own convention so
the baseline envelope at ``h == DEFAULT_HALF_LIFE == 20.0`` equals
``nba_model.VARIANTS`` entry for entry (0.5 and 1.75 are exact binary
multiplications). The count of distinct half-lives is three (odd),
satisfying the model's grid validation.
"""

from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Mapping

from polymarket_alpha_lab.papersim import nba_model
from polymarket_alpha_lab.papersim.optimize.spec import (
    ModelSpec,
    ParamDef,
    freeze_value,
    validate_param,
)

__all__ = [
    "B2B_FIT",
    "DATA_CONTRACT",
    "ENVELOPE_HALF_LIFE_RATIOS",
    "ENVELOPE_SIGMA_SCALES",
    "FAMILY_ID",
    "NBA_PACE_EFFICIENCY",
    "PARAM_SPACE",
    "PROCEDURE_VERSION",
    "TARGETS",
    "NbaPaceEfficiencyFamily",
    "NbaFamilyFitted",
    "NbaFamilyFixture",
    "NbaFamilyHistory",
]

#: Family id (design 04 section 3: "NBA pace-efficiency").
FAMILY_ID = "nba-pace-efficiency"

#: Version of the estimation CODE, not of the parameters. Generational
#: meaning: "1" is the frozen WP-NBA module as merged 2026-11-10/18,
#: "2" is the MO-5c parameterized estimator — this is a CAPABILITY
#: generation, deliberately unrelated to the WP-NBA research round
#: numbering (WP-NBA-1 snapshot / WP-NBA-2 model / WP-NBA-3 validation
#: work packages). Default behavior is bit-identical across the bump
#: (reviewed plan P3-6).
PROCEDURE_VERSION = "wp-nba2"

#: Forward placeholder: the dataset layer (MO-4) finalizes the data
#: contract. Freely adjustable only until the FIRST REAL
#: ``optimize init`` writes baseline specs into the registry (MO-6
#: wires that command); after that, changing it re-identifies every
#: stored baseline spec (same caveat as soccer_frozen).
DATA_CONTRACT = "espn-nba-snapshot-v1"

#: Market types this family scores: the moneyline (p_home/p_away) and
#: totals (``total_line_prob`` quotes). Vocabulary is a lineage-key
#: component; align with MO-3/MO-6 before the first real init.
TARGETS: tuple[str, ...] = ("moneyline", "totals")

#: B2B sentinel meaning "today's behavior: fit from eligible rows".
B2B_FIT = "fit"

#: The frozen envelope structure around the half-life center.
ENVELOPE_HALF_LIFE_RATIOS: tuple[float, ...] = (0.5, 1.0, 1.75)
ENVELOPE_SIGMA_SCALES: tuple[float, ...] = (1.0, 1.1)

#: First-version search space (see the module docstring table).
PARAM_SPACE: tuple[ParamDef, ...] = (
    ParamDef(
        "half_life",
        "choice",
        nba_model.DEFAULT_HALF_LIFE,
        choices=(10.0, 15.0, 20.0, 28.0, 35.0),
    ),
    ParamDef("sigma_scale", "choice", 1.0, choices=(0.9, 1.0, 1.1)),
    ParamDef(
        "b2b_penalty",
        "choice",
        B2B_FIT,
        choices=(B2B_FIT, 1.0, 2.0, 3.0, 4.0),
    ),
    ParamDef(
        "rest_points_per_day", "choice", 0.0, choices=(0.0, 0.25, 0.5)
    ),
    ParamDef(
        "total_bias", "choice", 0.0, choices=(-2.0, -1.0, 0.0, 1.0, 2.0)
    ),
)


@dataclasses.dataclass(frozen=True)
class NbaFamilyHistory:
    """Fit inputs: one frozen-contract ESPN NBA snapshot mapping."""

    snapshot: Mapping[str, object]


@dataclasses.dataclass(frozen=True)
class NbaFamilyFixture:
    """One fixture to predict; fields mirror ``nba_model.NbaFixture``.

    ``before_date`` is the causality barrier passed straight through
    (the fixture's own date in walk-forward use).
    """

    home: str
    away: str
    rest_home: int | None = None
    rest_away: int | None = None
    b2b_home: bool = False
    b2b_away: bool = False
    before_date: str | None = None


@dataclasses.dataclass(frozen=True)
class NbaFamilyFitted:
    """Fitted state: frozen snapshot plus the validated spec binding.

    NBA re-estimates everything per call (league context, sigma walk,
    ratings), so the fitted state is the (frozen) history, the spec
    identity it is bound to, and the derived prediction kwargs.
    """

    snapshot: Mapping[str, object]
    spec_sha256: str
    kwargs: Mapping[str, object]


def _envelope_variants(half_life: float) -> tuple[nba_model.NbaVariantSpec, ...]:
    """The six-variant grid around ``half_life`` (module docstring).

    Order follows the model's own convention (half-life ascending x
    scale ascending); at ``half_life == DEFAULT_HALF_LIFE`` the result
    equals ``nba_model.VARIANTS`` entry for entry, names included.
    """

    return tuple(
        nba_model.NbaVariantSpec(f"hl{value:g}-sig{scale:g}", value, scale)
        for value in (ratio * half_life for ratio in ENVELOPE_HALF_LIFE_RATIOS)
        for scale in ENVELOPE_SIGMA_SCALES
    )


def _envelope_mappings(half_life: float) -> tuple[Mapping[str, object], ...]:
    return tuple(
        dataclasses.asdict(spec) for spec in _envelope_variants(half_life)
    )


def _validated_params(spec: ModelSpec) -> dict[str, object]:
    """Validate ``spec.params`` against PARAM_SPACE; return a plain dict.

    The numeric b2b bound against the model's ``B2B_PENALTY_CLAMP`` is
    checked BEFORE choice membership: the clamp is the model's
    documented fail-safe, so a value outside it is refused with the
    clamp message even if the choice grid is ever widened past it
    (reviewed plan P2-i).
    """

    if spec.family != FAMILY_ID:
        raise ValueError(
            f"{FAMILY_ID}.fit expects family {FAMILY_ID!r}, "
            f"got {spec.family!r}"
        )
    if spec.procedure_version != PROCEDURE_VERSION:
        raise ValueError(
            f"{FAMILY_ID}.fit expects procedure_version "
            f"{PROCEDURE_VERSION!r}, got {spec.procedure_version!r}"
        )
    by_name = {defn.name: defn for defn in PARAM_SPACE}
    given = dict(spec.params)
    unknown = sorted(set(given) - set(by_name))
    if unknown:
        raise ValueError(f"{FAMILY_ID}: unknown params {unknown}")
    missing = sorted(set(by_name) - set(given))
    if missing:
        raise ValueError(f"{FAMILY_ID}: missing params {missing}")
    b2b = given["b2b_penalty"]
    if b2b != B2B_FIT:
        low, high = nba_model.B2B_PENALTY_CLAMP
        if not low <= float(b2b) <= high:
            raise ValueError(
                f"{FAMILY_ID}: b2b_penalty {b2b!r} escapes the model's "
                f"B2B_PENALTY_CLAMP {nba_model.B2B_PENALTY_CLAMP!r}"
            )
    for name, defn in by_name.items():
        validate_param(defn, given[name])
    return given


def _prediction_kwargs(params: Mapping[str, object]) -> dict[str, object]:
    """Derive the ``fixture_prediction`` kwargs from validated params.

    Rest mapping (reviewed plan P3-5): 0.0 -> ``use_rest_adjustment
    = False`` (today's behavior); > 0 -> enabled AND the value passed.
    """

    rest = float(params["rest_points_per_day"])
    b2b = params["b2b_penalty"]
    return {
        "variants": _envelope_variants(float(params["half_life"])),
        "sigma_scale": float(params["sigma_scale"]),
        "total_bias": float(params["total_bias"]),
        "use_rest_adjustment": rest > 0.0,
        "rest_points_per_day": rest,
        "b2b_penalty_override": None if b2b == B2B_FIT else float(b2b),
    }


class NbaPaceEfficiencyFamily:
    """``ModelFamily`` implementation of the NBA pace/efficiency model."""

    family_id = FAMILY_ID
    procedure_version = PROCEDURE_VERSION

    def param_space(self) -> tuple[ParamDef, ...]:
        return PARAM_SPACE

    def baseline_spec(self) -> ModelSpec:
        """The built-in baseline: default params (= today's behavior),
        the six-variant envelope around the default center.

        ``legacy_model_version`` is deliberately ``None``: the NBA
        shadow model has no queue version string today (pipeline pins
        only soccer/crypto), and fabricating one would fake history.
        The baseline therefore also gets the derived
        ``"wp-nba2+<sha12>"`` identity.
        """

        return ModelSpec(
            family=FAMILY_ID,
            procedure_version=PROCEDURE_VERSION,
            params={defn.name: defn.default for defn in PARAM_SPACE},
            envelope=_envelope_mappings(nba_model.DEFAULT_HALF_LIFE),
            data_contract=DATA_CONTRACT,
        )

    def targets(self) -> tuple[str, ...]:
        return TARGETS

    def fit(
        self,
        history: NbaFamilyHistory,
        spec: ModelSpec,
        as_of: datetime.datetime,
    ) -> NbaFamilyFitted:
        """Bind one spec to the snapshot; ``as_of`` is provenance only.

        The spec must be complete, in-choice, inside the model's b2b
        clamp, and carry the envelope this family derives from its own
        params — a tampered envelope would split identity from
        behavior, so it is refused.
        """

        if not isinstance(history, NbaFamilyHistory):
            raise TypeError(
                f"{FAMILY_ID}.fit expects NbaFamilyHistory, "
                f"got {type(history).__name__}"
            )
        params = _validated_params(spec)
        expected_envelope = _envelope_mappings(float(params["half_life"]))
        if tuple(dict(item) for item in spec.envelope) != expected_envelope:
            raise ValueError(
                f"{FAMILY_ID}: envelope does not match the six-variant "
                f"structure derived from half_life={params['half_life']!r}"
            )
        return NbaFamilyFitted(
            snapshot=freeze_value(history.snapshot),
            spec_sha256=spec.spec_sha256(),
            kwargs=freeze_value(_prediction_kwargs(params)),
        )

    def predict(
        self, fitted: NbaFamilyFitted, fixture: NbaFamilyFixture
    ) -> nba_model.NbaPrediction | None:
        """Predict one fixture; ``None`` is the model's soft refusal."""

        if not isinstance(fitted, NbaFamilyFitted):
            raise TypeError(
                f"{FAMILY_ID}.predict expects NbaFamilyFitted, "
                f"got {type(fitted).__name__}"
            )
        if not isinstance(fixture, NbaFamilyFixture):
            raise TypeError(
                f"{FAMILY_ID}.predict expects NbaFamilyFixture, "
                f"got {type(fixture).__name__}"
            )
        model_fixture = nba_model.NbaFixture(
            home=fixture.home,
            away=fixture.away,
            rest_home=fixture.rest_home,
            rest_away=fixture.rest_away,
            b2b_home=fixture.b2b_home,
            b2b_away=fixture.b2b_away,
        )
        return nba_model.fixture_prediction(
            fitted.snapshot,
            model_fixture,
            before_date=fixture.before_date,
            **dict(fitted.kwargs),
        )


#: Module-level singleton family instance.
NBA_PACE_EFFICIENCY = NbaPaceEfficiencyFamily()
