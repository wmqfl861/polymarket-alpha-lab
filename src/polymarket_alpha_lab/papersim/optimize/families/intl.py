"""International national-team model families (MO-5b).

Design ``docs/handoffs/2026-11-19-audit-optimizer/04-optimizer-design.md``
sections 3, 7 and 8; plan ``05-development-plan.md`` node MO-5 row 5b.
Two families share this module because they share the international
snapshot data contract but are separate estimation procedures:

- ``intl-elo-poisson`` — the nine-variant Elo->Poisson research model
  (``papersim.intl_model``, WP-INTL-2). Parameters: the bridge slope
  (the envelope CENTER — the sweep itself is the spec's envelope) and
  the home advantage, which enters BOTH the Elo replay engine and the
  Elo->goals bridge (one constant, one parameter, threaded to every
  read site).
- ``intl-elo-goal-residuals`` — the date-causal O2.5 shadow research
  model (``papersim.intl_totals.IntlTotalsResearch``). Parameters: the
  recency window, the recency cap and the shrinkage prior.

Both lineages are research-only (``intl_model.INTL_MW_RESEARCH_ONLY``):
they may have baseline/active/candidate specs, but never gain entry
eligibility from the optimizer (design 04 section 3, last paragraph).

Model-version strings: the international models have never been part
of the scan pipeline, whose only version strings are
``pipeline.MODEL_VERSION_SOCCER``/``MODEL_VERSION_CRYPTO``. The totals
family's baseline keeps the existing research-report identity
``intl_totals.SHADOW_VERSION`` as its ``legacy_model_version`` so
``model_version()`` returns it verbatim; every other spec derives
``f"{procedure_version}+{spec_sha256()[:12]}"``. The paired
tournament-mean comparator (``intl_totals.BASELINE_VERSION``) is a
research-report label of ``tools/wp_intl_validate.py``, not a wrapped
family, and stays untouched.

Envelope semantics: the 1X2 family carries the frozen nine-variant
structure (slope x total-multiplier, ``intl_model.VARIANTS`` order)
around the spec's slope center. The baseline envelope is copied
verbatim from ``intl_model.VARIANTS`` — no arithmetic, because
``0.47 - 0.07`` is NOT float-equal to ``0.40`` — and ``spec_with``
short-circuits default parameters to ``baseline_spec()``, so
re-selecting the active parameters reproduces the baseline identity
exactly (the premise the one-standard-error rule relies on).
Non-default centers translate the frozen slope grid by the same
offsets; envelope endpoints may reach past the parameter's search
bounds, exactly as today's 0.40/0.55 endpoints coincide with them.

Empty-envelope lineages: ``intl-elo-goal-residuals`` has NO sensitivity
envelope (``spec.envelope == ()``) because the shadow totals model is a
point estimate paired with a tournament-mean baseline, not a variant
sweep. Gate and scorecard semantics for empty-envelope lineages — in
particular what "variant dispersion" and "the envelope must not narrow"
mean when there are no variants — ARE DEFINED BY MO-6 EXPLICITLY when
the scorecard layer lands; this module deliberately leaves the tuple
empty rather than inventing variants.

Import discipline: this module imports ``intl_model`` and
``intl_totals`` (and, through them, ``soccer_model``), so it loads
lazily through the PEP 562 registry in ``optimize.families``; importing
``polymarket_alpha_lab.papersim.optimize`` alone adds only ``optimize``
and ``optimize.spec`` (pinned by ``tests/test_papersim_optimize_spec.py``).
"""

from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Mapping, Sequence
from types import MappingProxyType

from polymarket_alpha_lab.papersim import intl_model, intl_totals
from polymarket_alpha_lab.papersim.optimize.spec import (
    ModelSpec,
    ParamDef,
    freeze_value,
    validate_param,
)

__all__ = [
    "DATA_CONTRACT",
    "FAMILY_ID_ELO",
    "FAMILY_ID_TOTALS",
    "INTL_ELO",
    "INTL_TOTALS",
    "IntlEloFamily",
    "IntlEloFitted",
    "IntlFamilyFixture",
    "IntlHistory",
    "IntlTotalsFamily",
    "IntlTotalsFitted",
    "PROCEDURE_VERSION_ELO",
    "PROCEDURE_VERSION_TOTALS",
    "TARGETS_ELO",
    "TARGETS_TOTALS",
]

#: Family id (design 04 section 3: "international Elo-Poisson").
FAMILY_ID_ELO = "intl-elo-poisson"

#: Version of the estimation CODE (the WP-INTL-2 Elo->Poisson engine),
#: not of the parameters. This model has never been in the scan
#: pipeline, so there is no pre-existing version string to preserve.
PROCEDURE_VERSION_ELO = "intl-elo-v1"

#: Family id of the goal-residuals totals shadow model.
FAMILY_ID_TOTALS = "intl-elo-goal-residuals"

#: The estimation-code version IS the existing research-report identity
#: (referenced from intl_totals, never copied as a literal), so derived
#: non-baseline versions read "intl-elo-goal-residuals-v1+<sha12>".
PROCEDURE_VERSION_TOTALS = intl_totals.SHADOW_VERSION

#: Forward placeholder: the dataset layer (MO-4) finalizes the data
#: contract. Freely adjustable only until the FIRST REAL
#: ``optimize init`` writes baseline specs into the registry (MO-6
#: wires that command); after that, changing it re-identifies every
#: stored baseline spec. (Same caveat as soccer_frozen.DATA_CONTRACT.)
DATA_CONTRACT = "intl-snapshot-v1"

#: Market types each family scores. ``match_winner`` is research-only
#: here (``intl_model.INTL_MW_RESEARCH_ONLY``): the family computes and
#: scores 1X2 probabilities but never gains entry eligibility.
TARGETS_ELO: tuple[str, ...] = ("match_winner",)
TARGETS_TOTALS: tuple[str, ...] = ("over_under",)


@dataclasses.dataclass(frozen=True)
class IntlHistory:
    """Fit inputs: one frozen international snapshot.

    ``snapshot`` is the frozen input contract of ``intl_model``'s
    docstring (``generated_at`` / ``teams`` / ``matches``), as built by
    the ``intl_data`` adapter.
    """

    snapshot: Mapping[str, object]


@dataclasses.dataclass(frozen=True)
class IntlFamilyFixture:
    """One fixture to predict: the model dataclass plus tournament context.

    ``tournament`` selects the per-tournament mean-total calibration
    (``intl_model``'s ``fixture_prediction``/``IntlTotalsResearch.predict``
    keyword of the same name).
    """

    fixture: intl_model.IntlFixture
    tournament: str | None = None


@dataclasses.dataclass(frozen=True)
class IntlEloFitted:
    """Fitted state of the Elo->Poisson family for one spec.

    The snapshot is deep-frozen into the fitted state so the recorded
    spec identity cannot drift from the data actually used (mirrors
    ``SoccerFrozenFitted``); ``variants`` is the spec's envelope
    rebuilt as ``IntlVariantSpec`` objects.
    """

    snapshot: Mapping[str, object]
    home_advantage: float
    variants: tuple[intl_model.IntlVariantSpec, ...]
    spec_sha256: str
    as_of: datetime.datetime


@dataclasses.dataclass(frozen=True)
class IntlTotalsFitted:
    """Fitted state of the goal-residuals family: the indexed research model.

    ``IntlTotalsResearch`` indexes dated results once (the expensive
    fit); ``predict`` is a cheap strictly-earlier-days lookup. ``as_of``
    is provenance only — causality is per fixture date by construction.
    """

    research: intl_totals.IntlTotalsResearch
    spec_sha256: str
    as_of: datetime.datetime


#: The frozen slope grid translated to offsets around the default
#: center. Applied ONLY to non-default centers; the baseline envelope
#: is copied verbatim from ``intl_model.VARIANTS`` (see module
#: docstring for the float-equality reason).
_SLOPE_OFFSETS = (
    intl_model.SWEEP_SLOPES[0] - intl_model.DEFAULT_SLOPE,
    0.0,
    intl_model.SWEEP_SLOPES[2] - intl_model.DEFAULT_SLOPE,
)


def _envelope_items(slope: float) -> tuple[Mapping[str, object], ...]:
    """The nine-variant envelope around ``slope`` (design 04 section 7).

    Default center: ``intl_model.VARIANTS`` verbatim (order included —
    variant order is part of the spec identity). Other centers: the
    frozen slope grid translated by the same offsets, multiplied by the
    unchanged total multipliers, named with the model's own format.
    """

    if slope == intl_model.DEFAULT_SLOPE:
        return tuple(
            MappingProxyType(dataclasses.asdict(spec))
            for spec in intl_model.VARIANTS
        )
    return tuple(
        MappingProxyType(
            {
                "name": f"slope{variant_slope:.2f}-totalx{multiplier:g}",
                "slope": variant_slope,
                "total_multiplier": multiplier,
            }
        )
        for variant_slope in (slope + offset for offset in _SLOPE_OFFSETS)
        for multiplier in intl_model.SWEEP_TOTAL_MULTIPLIERS
    )


def _default_params(space: tuple[ParamDef, ...]) -> dict[str, object]:
    return {defn.name: defn.default for defn in space}


def _checked_params(
    space: tuple[ParamDef, ...], params: Mapping[str, object]
) -> dict[str, object]:
    """Validate ``params`` against ``space``; missing names take defaults."""
    names = {defn.name for defn in space}
    unknown = sorted(set(params) - names)
    if unknown:
        raise ValueError(
            f"unknown parameters {unknown}; space is {sorted(names)}"
        )
    checked: dict[str, object] = {}
    for defn in space:
        value = params[defn.name] if defn.name in params else defn.default
        validate_param(defn, value)
        checked[defn.name] = value
    return checked


def _fit_params(
    family_id: str, space: tuple[ParamDef, ...], spec: ModelSpec
) -> dict[str, object]:
    """Strict validation for ``fit``: exactly the space's parameters, in space."""
    names = {defn.name for defn in space}
    unknown = sorted(set(spec.params) - names)
    if unknown:
        raise ValueError(
            f"{family_id} spec has unknown parameters {unknown}"
        )
    missing = sorted(names - set(spec.params))
    if missing:
        raise ValueError(f"{family_id} spec is missing parameters {missing}")
    for defn in space:
        validate_param(defn, spec.params[defn.name])
    return dict(spec.params)


def _variants_from_envelope(
    envelope: object,
) -> tuple[intl_model.IntlVariantSpec, ...]:
    """Rebuild ``IntlVariantSpec`` objects from a spec's envelope mappings."""
    if isinstance(envelope, (str, bytes)) or not isinstance(envelope, Sequence):
        raise ValueError(
            "envelope must be a sequence of variant mappings, got "
            f"{type(envelope)!r}"
        )
    items = tuple(envelope)
    if not items:
        raise ValueError(
            "intl-elo-poisson requires a nonempty variant envelope"
        )
    variants = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError(
                f"each envelope item must be a mapping, got {type(item).__name__}"
            )
        missing = sorted({"name", "slope", "total_multiplier"} - set(item))
        if missing:
            raise ValueError(f"envelope item is missing keys {missing}")
        variants.append(
            intl_model.IntlVariantSpec(
                name=str(item["name"]),
                slope=float(item["slope"]),
                total_multiplier=float(item["total_multiplier"]),
            )
        )
    return tuple(variants)


class IntlEloFamily:
    """``ModelFamily`` implementation of the nine-variant Elo->Poisson model."""

    family_id = FAMILY_ID_ELO
    procedure_version = PROCEDURE_VERSION_ELO

    def param_space(self) -> tuple[ParamDef, ...]:
        """First-version space (design 04 section 7, national-team row)."""

        return (
            ParamDef(
                name="slope",
                kind="float",
                default=intl_model.DEFAULT_SLOPE,
                lower=0.40,
                upper=0.55,
            ),
            ParamDef(
                name="home_advantage",
                kind="choice",
                default=intl_model.HOME_ADVANTAGE,
                choices=(60.0, 80.0, 100.0, 120.0),
            ),
        )

    def baseline_spec(self) -> ModelSpec:
        """Default parameters = today's behavior; no legacy version string."""

        return ModelSpec(
            family=FAMILY_ID_ELO,
            procedure_version=PROCEDURE_VERSION_ELO,
            params=_default_params(self.param_space()),
            envelope=_envelope_items(intl_model.DEFAULT_SLOPE),
            data_contract=DATA_CONTRACT,
        )

    def spec_with(self, params: Mapping[str, object]) -> ModelSpec:
        """Spec for one point of the search space (missing names default).

        Default parameters short-circuit to ``baseline_spec()`` so the
        re-selected active point shares the baseline identity — the
        premise of the one-standard-error rule.
        """

        checked = _checked_params(self.param_space(), params)
        if checked == _default_params(self.param_space()):
            return self.baseline_spec()
        return ModelSpec(
            family=FAMILY_ID_ELO,
            procedure_version=PROCEDURE_VERSION_ELO,
            params=checked,
            envelope=_envelope_items(float(checked["slope"])),
            data_contract=DATA_CONTRACT,
        )

    def targets(self) -> tuple[str, ...]:
        return TARGETS_ELO

    def fit(
        self,
        history: IntlHistory,
        spec: ModelSpec,
        as_of: datetime.datetime,
    ) -> IntlEloFitted:
        """Bind one spec to the snapshot; ``as_of`` is provenance only.

        The spec's slope must match the envelope variant at index
        ``len(spec.envelope) // 2``. ``home_advantage`` enters both the
        replay engine and the bridge.
        """

        if not isinstance(history, IntlHistory):
            raise TypeError(
                f"{FAMILY_ID_ELO}.fit expects IntlHistory, got "
                f"{type(history).__name__}"
            )
        if spec.family != FAMILY_ID_ELO:
            raise ValueError(
                f"{FAMILY_ID_ELO}.fit got a spec of family {spec.family!r}"
            )
        checked = _fit_params(FAMILY_ID_ELO, self.param_space(), spec)
        variants = _variants_from_envelope(spec.envelope)
        center_slope = variants[len(variants) // 2].slope
        if checked["slope"] != center_slope:
            raise ValueError(
                f"{FAMILY_ID_ELO} spec slope {checked['slope']!r} does not match "
                f"envelope center slope {center_slope!r}"
            )
        return IntlEloFitted(
            snapshot=freeze_value(history.snapshot),
            home_advantage=float(checked["home_advantage"]),
            variants=variants,
            spec_sha256=spec.spec_sha256(),
            as_of=as_of,
        )

    def predict(
        self, fitted: IntlEloFitted, fixture: IntlFamilyFixture
    ) -> intl_model.IntlPrediction | None:
        """Nine-variant prediction; ``None`` is the model's explicit refusal."""

        if not isinstance(fitted, IntlEloFitted):
            raise TypeError(
                f"{FAMILY_ID_ELO}.predict expects IntlEloFitted, got "
                f"{type(fitted).__name__}"
            )
        if not isinstance(fixture, IntlFamilyFixture):
            raise TypeError(
                f"{FAMILY_ID_ELO}.predict expects IntlFamilyFixture, got "
                f"{type(fixture).__name__}"
            )
        return intl_model.fixture_prediction(
            fitted.snapshot,
            fixture.fixture,
            tournament=fixture.tournament,
            home_advantage=fitted.home_advantage,
            variants=fitted.variants,
        )


class IntlTotalsFamily:
    """``ModelFamily`` implementation of the goal-residuals totals model."""

    family_id = FAMILY_ID_TOTALS
    procedure_version = PROCEDURE_VERSION_TOTALS

    def param_space(self) -> tuple[ParamDef, ...]:
        """First-version space (design 04 section 7, totals-model items)."""

        return (
            ParamDef(
                name="recent_days",
                kind="choice",
                default=intl_totals.RECENT_DAYS,
                choices=(365, 730, 1095),
            ),
            ParamDef(
                name="max_recent_matches",
                kind="choice",
                default=intl_totals.MAX_RECENT_MATCHES,
                choices=(8, 12, 16),
            ),
            ParamDef(
                name="prior_matches",
                kind="choice",
                default=intl_totals.PRIOR_MATCHES,
                choices=(4, 8, 12, 16),
            ),
        )

    def baseline_spec(self) -> ModelSpec:
        """Default parameters; legacy version = the research-report string."""

        return ModelSpec(
            family=FAMILY_ID_TOTALS,
            procedure_version=PROCEDURE_VERSION_TOTALS,
            params=_default_params(self.param_space()),
            envelope=(),  # empty-envelope lineage: MO-6 defines the semantics
            data_contract=DATA_CONTRACT,
            legacy_model_version=intl_totals.SHADOW_VERSION,
        )

    def spec_with(self, params: Mapping[str, object]) -> ModelSpec:
        """Spec for one point of the search space (missing names default)."""

        checked = _checked_params(self.param_space(), params)
        if checked == _default_params(self.param_space()):
            return self.baseline_spec()
        return ModelSpec(
            family=FAMILY_ID_TOTALS,
            procedure_version=PROCEDURE_VERSION_TOTALS,
            params=checked,
            envelope=(),  # empty-envelope lineage: MO-6 defines the semantics
            data_contract=DATA_CONTRACT,
        )

    def targets(self) -> tuple[str, ...]:
        return TARGETS_TOTALS

    def fit(
        self,
        history: IntlHistory,
        spec: ModelSpec,
        as_of: datetime.datetime,
    ) -> IntlTotalsFitted:
        """Index the snapshot once under the spec's parameters.

        ``as_of`` is provenance only: quotes use strictly-earlier days
        by construction. The model never refuses outright — reluctance
        is expressed on the quote (``fallback_reason``), which the
        scorecard layer (MO-3/MO-6) consumes.
        """

        if not isinstance(history, IntlHistory):
            raise TypeError(
                f"{FAMILY_ID_TOTALS}.fit expects IntlHistory, got "
                f"{type(history).__name__}"
            )
        if spec.family != FAMILY_ID_TOTALS:
            raise ValueError(
                f"{FAMILY_ID_TOTALS}.fit got a spec of family {spec.family!r}"
            )
        checked = _fit_params(FAMILY_ID_TOTALS, self.param_space(), spec)
        research = intl_totals.IntlTotalsResearch(
            dict(history.snapshot),
            recent_days=checked["recent_days"],
            max_recent_matches=checked["max_recent_matches"],
            prior_matches=checked["prior_matches"],
        )
        return IntlTotalsFitted(
            research=research,
            spec_sha256=spec.spec_sha256(),
            as_of=as_of,
        )

    def predict(
        self, fitted: IntlTotalsFitted, fixture: IntlFamilyFixture
    ) -> intl_totals.IntlTotalsQuote:
        """O2.5 quote (never ``None``; see ``fit`` for refusal semantics)."""

        if not isinstance(fitted, IntlTotalsFitted):
            raise TypeError(
                f"{FAMILY_ID_TOTALS}.predict expects IntlTotalsFitted, got "
                f"{type(fitted).__name__}"
            )
        if not isinstance(fixture, IntlFamilyFixture):
            raise TypeError(
                f"{FAMILY_ID_TOTALS}.predict expects IntlFamilyFixture, got "
                f"{type(fixture).__name__}"
            )
        return fitted.research.predict(
            fixture.fixture, tournament=fixture.tournament
        )


#: Module-level singleton family instances.
INTL_ELO = IntlEloFamily()
INTL_TOTALS = IntlTotalsFamily()
