"""Club-soccer recalibration estimator wrapped as a tunable model family.

MO-5a of the model auto-optimization line (design
``docs/handoffs/2026-11-19-audit-optimizer/04-optimizer-design.md``
sections 7 and 8; plan ``05-development-plan.md`` node MO-5 row 5a).
The estimation code is ``papersim.recalib`` -- the WP-P4 shadow
estimator -- wrapped WITHOUT modification: ``recalib.py`` is on the
never-touch list of the optimization line (plan 05 section 4).

Procedure identity (review F10): ``PROCEDURE_VERSION = "p4r1"`` denotes
the estimation code exactly as delivered by WP-P4, i.e.
``src/polymarket_alpha_lab/papersim/recalib.py`` with SHA-256
``f6badedb3cf9d72628af7a186c0e9183d69bca12b204a1df946e650a0cd282a9``
(LF-normalized blob, registered in ``docs/paper-sim/wp-p4-evidence-manifest.json``).
Changing ``recalib.py`` requires a new procedure version, not a new
parameter spec.

Parameter surface (review F6) -- ``spec.params`` always carries EXACTLY
these six keys, in the family's canonical spelling:

- ``half_life_days`` (float, days; v1 grid 60/120/240)
- ``team_prior_matches`` (float, pseudo-matches; v1 grid 4/8/16)
- ``sparse_team_prior`` (None or one of 0.0/4.0/6.0/8.0/12.0/16.0;
  None means "no override", i.e. sparse teams use ``team_prior_matches``)
- ``league_prior_matches`` (10.0/20.0/40.0)
- ``low_score_rho`` ("fit" / -0.1 / 0.0; "fit" is recalib's
  score-likelihood Dixon-Coles fit)
- ``completion_lag_hours`` (fixed 3.0, never searched; part of the spec
  identity because it changes which rounds enter training)

Search spaces (review F5): ``SEARCH_SPACE_V1`` is the WP-P4 3x3 grid --
only the FIRST TWO keys above vary, in the published declaration order
(half-life outer, team prior inner); the remaining keys sit at their
defaults (120/8/None/20/"fit"/3.0), which is exactly recalib's
``Config()`` default and the WP-P4 fallback configuration. The v2 space
of design 04 section 7 is fully declared here (sparse prior enumeration
pinned to 0/4/6/8/12/16 -- the union of "same as team prior" with
{0,4,12,16}; None is the unsearched default) but ``param_space()``
returns v1 ONLY. Activating v2 requires a node plan and review (plan 05
section 7); the scorecard must record the comparison count, available
from ``combination_count()``/``space_summary()``.

Envelope (design 04 section 7): center plus EIGHT perturbations in a
fixed order -- half-life x0.5, x2; team prior x0.5, x2; low-score rho
0.0, -0.1; sparse prior 0.0, 16.0. Nine variants total, order is part
of the spec identity (``optimize.spec`` module docstring). Envelope
values may fall outside the search grid by construction (x0.5/x2 of an
off-grid center); ``half_life_days`` and ``team_prior_matches`` are
therefore validated as positive floats, not as closed choices.

Forced low-score rho (review F4): recalib's ``Config`` has no fixed-rho
field, so a numeric ``low_score_rho`` is applied AFTER fitting by
rebuilding the estimator with ``dataclasses.replace``: the rho of BOTH
``league_parameters`` (every league) AND ``global_parameters`` is set to
the forced value with ``rho_source = "forced_by_spec"`` -- both layers,
because ``Estimator.predict`` reads the league layer and falls back to
the global layer for leagues without fitted parameters. ``Estimator.config``
is left untouched (it keeps the spec-derived ``Config`` verbatim). At
predict time recalib itself clamps rho into the per-fixture feasible
interval (recalib.py ``Estimator.predict``), so a forced -0.1 can be
projected upward for extreme goal rates; that clamping is recalib
behaviour, not family behaviour.

Registration (review F1): this family registers itself in
``optimize/families/__init__.py`` (``_FAMILY_MODULES``) on its own
branch; sibling MO-5 lanes add their own keys. The three-way merge of
those registration lines and of the ``iter_family_ids`` test pin is
owned by the COORDINATOR, who controls merge order (last merger takes
the union).

Everything here is pure: standard library plus ``papersim.recalib`` and
``optimize.spec``; no network, no database, no clock reads, no file I/O.
"""

from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Mapping
from types import MappingProxyType

from polymarket_alpha_lab.papersim import recalib
from polymarket_alpha_lab.papersim.optimize.spec import (
    ModelSpec,
    ParamDef,
    canonical_value,
    validate_param,
)

__all__ = [
    "DATA_CONTRACT",
    "FAMILY_ID",
    "PROCEDURE_VERSION",
    "RECALIB_SOURCE_SHA256",
    "SEARCH_SPACE_V1",
    "SEARCH_SPACE_V2",
    "SEARCH_SPACES",
    "SOCCER_RECALIB",
    "SPARSE_TEAM_PRIOR_CHOICES",
    "TARGETS",
    "SoccerRecalibFamily",
    "SoccerRecalibFitted",
    "combination_count",
    "envelope_params",
    "envelope_specs",
    "space_summary",
    "space_version",
    "spec_for_params",
]

#: Family id (design 04 section 3: "club soccer, recalibration estimator").
FAMILY_ID = "club-soccer-recalib"

#: Version of the estimation CODE, not of the parameters. "p4r1" is the
#: recalib.py delivered by WP-P4, byte-identity below (review F10).
PROCEDURE_VERSION = "p4r1"

#: LF-normalized SHA-256 of src/polymarket_alpha_lab/papersim/recalib.py
#: as registered by docs/paper-sim/wp-p4-evidence-manifest.json.
RECALIB_SOURCE_SHA256 = (
    "f6badedb3cf9d72628af7a186c0e9183d69bca12b204a1df946e650a0cd282a9"
)

#: Forward placeholder: the dataset layer (MO-4) finalizes the data
#: contract. Freely adjustable only until the FIRST REAL ``optimize
#: init`` writes baseline specs into the registry (MO-6); after that,
#: changing it re-identifies every stored spec of this family.
DATA_CONTRACT = "wp-p4-match-history-v1"

#: Market types this family scores. recalib.Prediction exposes the 1X2
#: triple and the total-goals vector (over_probability); btts and team
#: totals would need the joint score grid, which recalib's public
#: interface does not provide.
TARGETS: tuple[str, ...] = ("match_winner", "over_under")

#: Spec-legal sparse-prior values (review F5). ``None`` is the default
#: "no override" (sparse teams then use ``team_prior_matches``); the six
#: numeric values are the v2 search enumeration -- the union of "same
#: as the team-prior grid {4,6,8,12,16}" with {0,4,12,16} from design
#: 04 section 7. Infinity (the P4-2 study's sixth arm) is NOT a spec
#: value here.
SPARSE_TEAM_PRIOR_CHOICES: tuple[object, ...] = (
    None, 0.0, 4.0, 6.0, 8.0, 12.0, 16.0,
)

#: Closed choice sets for the three enumerated non-grid knobs.
LEAGUE_PRIOR_CHOICES: tuple[float, ...] = (10.0, 20.0, 40.0)
LOW_SCORE_RHO_CHOICES: tuple[object, ...] = ("fit", -0.1, 0.0)

#: The six-key parameter surface with family defaults == recalib's
#: ``Config()`` defaults == the WP-P4 fallback configuration
#: (tools/wp_p4_walkforward.py select_config) and the fixed 120d/8match
#: shadow diagnostic (tools/wp_p4_market.py fixed_shadow_predictor).
DEFAULT_PARAMS: Mapping[str, object] = MappingProxyType({
    "half_life_days": 120.0,
    "team_prior_matches": 8.0,
    "sparse_team_prior": None,
    "league_prior_matches": 20.0,
    "low_score_rho": "fit",
    "completion_lag_hours": 3.0,
})

_FULL_PARAMETER_DEFS: tuple[ParamDef, ...] = (
    # The two grid knobs are open positive floats: envelope x0.5/x2 of
    # an off-grid center must stay spec-legal. Grid membership itself
    # is pinned by the search spaces below, not by validation.
    ParamDef("half_life_days", "float", 120.0, lower=1.0),
    ParamDef("team_prior_matches", "float", 8.0, lower=0.0),
    ParamDef("sparse_team_prior", "choice", None, choices=SPARSE_TEAM_PRIOR_CHOICES),
    ParamDef("league_prior_matches", "choice", 20.0, choices=LEAGUE_PRIOR_CHOICES),
    ParamDef("low_score_rho", "choice", "fit", choices=LOW_SCORE_RHO_CHOICES),
    ParamDef("completion_lag_hours", "float", 3.0, lower=3.0),
)

#: Search space v1: the WP-P4 3x3 grid, declaration order preserved
#: (half-life outer, team prior inner; ties resolve in this order).
#: Exactly the grid of tools/wp_p4_walkforward.py HALF_LIVES x TEAM_PRIORS.
SEARCH_SPACE_V1: tuple[ParamDef, ...] = (
    ParamDef("half_life_days", "choice", 120.0, choices=(60.0, 120.0, 240.0)),
    ParamDef("team_prior_matches", "choice", 8.0, choices=(4.0, 8.0, 16.0)),
)

#: Search space v2: design 04 section 7, fully declared, NOT active.
#: Activation requires a node plan and review (plan 05 section 7 risk
#: table); 6*5*6*3*3 = 1620 combinations per cutoff before the
#: nine-variant envelope, a budget question for MO-6.
SEARCH_SPACE_V2: tuple[ParamDef, ...] = (
    ParamDef("half_life_days", "choice", 120.0,
             choices=(60.0, 90.0, 120.0, 180.0, 240.0, 365.0)),
    ParamDef("team_prior_matches", "choice", 8.0,
             choices=(4.0, 6.0, 8.0, 12.0, 16.0)),
    ParamDef("sparse_team_prior", "choice", None, choices=(0.0, 4.0, 6.0, 8.0, 12.0, 16.0)),
    ParamDef("league_prior_matches", "choice", 20.0, choices=LEAGUE_PRIOR_CHOICES),
    ParamDef("low_score_rho", "choice", "fit", choices=LOW_SCORE_RHO_CHOICES),
)

#: Versioned search spaces; ``param_space()`` returns v1 only (F5).
SEARCH_SPACES: Mapping[str, tuple[ParamDef, ...]] = MappingProxyType({
    "v1": SEARCH_SPACE_V1,
    "v2": SEARCH_SPACE_V2,
})

#: Rho source marker written by the forced-rho rebuild (review F4).
FORCED_RHO_SOURCE = "forced_by_spec"


@dataclasses.dataclass(frozen=True)
class SoccerRecalibFitted:
    """Fitted state: the (possibly rho-forced) estimator plus identity."""

    estimator: recalib.Estimator
    spec_sha256: str
    forced_rho: float | None


def _utc(value: object, name: str) -> datetime.datetime:
    if (not isinstance(value, datetime.datetime) or value.tzinfo is None
            or value.utcoffset() is None):
        raise ValueError(f"{name} must be an aware datetime")
    return value.astimezone(datetime.timezone.utc)


def _validate_params(params: Mapping[str, object]) -> None:
    """Exact six-key surface, closed sets where the design enumerates."""

    expected = {defn.name for defn in _FULL_PARAMETER_DEFS}
    if set(params) != expected:
        missing = sorted(expected - set(params))
        extra = sorted(set(params) - expected)
        raise ValueError(
            f"{FAMILY_ID} params must be exactly the six keys {sorted(expected)}; "
            f"missing={missing}, unexpected={extra}"
        )
    for defn in _FULL_PARAMETER_DEFS:
        value = params[defn.name]
        if isinstance(value, bool):
            # bool would compare equal to 0.0/1.0 inside choice sets.
            raise ValueError(f"{defn.name}: booleans are not parameter values")
        validate_param(defn, value)


def _config_for_params(params: Mapping[str, object]) -> recalib.Config:
    """Map the six spec keys onto recalib's Config (keyword-only fit)."""

    return recalib.Config(
        half_life_days=float(params["half_life_days"]),
        team_prior_matches=float(params["team_prior_matches"]),
        sparse_prior_matches=params["sparse_team_prior"],
        league_prior_matches=float(params["league_prior_matches"]),
        completion_lag_hours=float(params["completion_lag_hours"]),
    )


def envelope_params(
    params: Mapping[str, object]
) -> tuple[Mapping[str, object], ...]:
    """Center plus the eight predeclared perturbations, fixed order.

    Order is part of the spec identity. Each perturbation changes
    exactly one key. A perturbation numerically equal to the center
    still occupies its slot (the count is always nine, mirroring the
    frozen production model's fixed ten-variant structure).
    """

    base = dict(DEFAULT_PARAMS)
    base.update(dict(params))
    half_life = float(base["half_life_days"])
    team_prior = float(base["team_prior_matches"])
    return (
        dict(base),
        {**base, "half_life_days": half_life * 0.5},
        {**base, "half_life_days": half_life * 2.0},
        {**base, "team_prior_matches": team_prior * 0.5},
        {**base, "team_prior_matches": team_prior * 2.0},
        {**base, "low_score_rho": 0.0},
        {**base, "low_score_rho": -0.1},
        {**base, "sparse_team_prior": 0.0},
        {**base, "sparse_team_prior": 16.0},
    )


def spec_for_params(params: Mapping[str, object]) -> ModelSpec:
    """Build the spec for a (possibly partial) parameter assignment.

    Missing keys take ``DEFAULT_PARAMS``; the envelope is derived
    mechanically from the merged parameters. Callers own grid
    membership: only ``SEARCH_SPACE_V1`` enumerations may reach the
    search/registry while v1 is active.
    """

    merged = dict(DEFAULT_PARAMS)
    merged.update(dict(params))
    _validate_params(merged)
    return ModelSpec(
        family=FAMILY_ID,
        procedure_version=PROCEDURE_VERSION,
        params=merged,
        envelope=envelope_params(merged),
        data_contract=DATA_CONTRACT,
    )


def envelope_specs(spec: ModelSpec) -> tuple[ModelSpec, ...]:
    """Lift each envelope entry of ``spec`` to a full ModelSpec.

    The search/runner (MO-6) and the scorecard fit these nine variants;
    the returned order equals ``spec.envelope`` order.
    """

    return tuple(spec_for_params(dict(item)) for item in spec.envelope)


def space_version(space: tuple[ParamDef, ...]) -> str:
    """Label of a declared search space ("v1"/"v2"), else raise."""

    for label, declared in SEARCH_SPACES.items():
        if space is declared or space == declared:
            return label
    raise ValueError("unknown search space; use SEARCH_SPACE_V1 or SEARCH_SPACE_V2")


def combination_count(space: tuple[ParamDef, ...] = SEARCH_SPACE_V1) -> int:
    """Number of grid points (comparisons) of a search space.

    The scorecard must record this count for every run (plan 05
    section 7: "记分卡里始终写明本次比较了多少组").
    """

    count = 1
    for defn in space:
        if defn.kind != "choice" or not defn.choices:
            raise ValueError(f"space dimension {defn.name!r} is not a closed choice grid")
        count *= len(defn.choices)
    return count


def space_summary(
    space: tuple[ParamDef, ...] = SEARCH_SPACE_V1,
) -> Mapping[str, object]:
    """Comparison-count metadata for run receipts and scorecards."""

    return MappingProxyType({
        "version": space_version(space),
        "dimensions": MappingProxyType({
            defn.name: tuple(defn.choices) for defn in space
        }),
        "combinations": combination_count(space),
    })


def _force_rho(estimator: recalib.Estimator, value: float) -> recalib.Estimator:
    """Rebuild the estimator with forced rho on BOTH layers (review F4).

    ``Estimator.predict`` reads the league layer and falls back to the
    global layer for leagues without fitted parameters, so replacing
    only one layer leaks the fitted rho. ``config`` is deliberately NOT
    touched: it keeps the spec-derived Config verbatim. predict-time
    clamping into the feasible interval stays inside recalib.
    """

    leagues = {
        name: dataclasses.replace(lp, rho=value, rho_source=FORCED_RHO_SOURCE)
        for name, lp in estimator.league_parameters.items()
    }
    globe = dataclasses.replace(
        estimator.global_parameters, rho=value, rho_source=FORCED_RHO_SOURCE
    )
    return dataclasses.replace(
        estimator,
        league_parameters=MappingProxyType(leagues),
        global_parameters=globe,
    )


class SoccerRecalibFamily:
    """``ModelFamily`` implementation of the recalibration estimator.

    ``fit`` wraps ``recalib.fit(history, config=Config(...))`` verbatim;
    ``predict`` passes through ``Estimator.predict`` and maps recalib's
    documented refusals (``ValueError``: kickoff before the cutoff,
    unknown target league-season, round at or before a fitted round) to
    ``None`` -- an explicit refusal, never a fallback.
    """

    family_id = FAMILY_ID
    procedure_version = PROCEDURE_VERSION

    def param_space(self) -> tuple[ParamDef, ...]:
        """The ACTIVE search space: v1, the WP-P4 3x3 grid (review F5)."""

        return SEARCH_SPACE_V1

    def baseline_spec(self) -> ModelSpec:
        """Baseline: recalib ``Config()`` defaults == the WP-P4 120/8."""

        return spec_for_params({})

    def targets(self) -> tuple[str, ...]:
        return TARGETS

    def fit(
        self,
        history: recalib.History,
        spec: ModelSpec,
        as_of: datetime.datetime,
    ) -> SoccerRecalibFitted:
        """Bind one spec to ``history`` at ``as_of`` (both must agree)."""

        if not isinstance(history, recalib.History):
            raise TypeError(
                f"{FAMILY_ID}.fit expects recalib.History, "
                f"got {type(history).__name__}"
            )
        if not isinstance(spec, ModelSpec):
            raise TypeError(
                f"{FAMILY_ID}.fit expects ModelSpec, got {type(spec).__name__}"
            )
        if spec.family != FAMILY_ID:
            raise ValueError(
                f"{FAMILY_ID}.fit got a spec of family {spec.family!r}"
            )
        if spec.procedure_version != PROCEDURE_VERSION:
            raise ValueError(
                f"{FAMILY_ID}.fit got procedure_version "
                f"{spec.procedure_version!r}, expected {PROCEDURE_VERSION!r}"
            )
        params = dict(spec.params)
        _validate_params(params)
        derived = envelope_params(params)
        if canonical_value([dict(item) for item in spec.envelope]) != canonical_value(
            list(derived)
        ):
            raise ValueError(
                f"{FAMILY_ID} refuses a spec whose envelope is not the "
                "mechanically derived nine variants (a narrower envelope "
                "would silently relax the gate spread rules, design 04 "
                "section 6)"
            )
        if _utc(as_of, "as_of") != _utc(history.as_of, "history.as_of"):
            raise ValueError(
                "as_of must equal history.as_of; the history cutoff is the "
                "single source of truth for the knowledge boundary"
            )
        config = _config_for_params(params)
        estimator = recalib.fit(history, config=config)
        rho_setting = params["low_score_rho"]
        forced = None if rho_setting == "fit" else float(rho_setting)
        if forced is not None:
            estimator = _force_rho(estimator, forced)
        return SoccerRecalibFitted(
            estimator=estimator, spec_sha256=spec.spec_sha256(), forced_rho=forced
        )

    def predict(
        self, fitted: SoccerRecalibFitted, fixture: recalib.Fixture
    ) -> recalib.Prediction | None:
        """Predict one fixture; ``None`` is an explicit refusal."""

        if not isinstance(fitted, SoccerRecalibFitted):
            raise TypeError(
                f"{FAMILY_ID}.predict expects SoccerRecalibFitted, "
                f"got {type(fitted).__name__}"
            )
        if not isinstance(fixture, recalib.Fixture):
            raise TypeError(
                f"{FAMILY_ID}.predict expects recalib.Fixture, "
                f"got {type(fixture).__name__}"
            )
        try:
            return fitted.estimator.predict(fixture)
        except ValueError:
            # recalib raises ValueError for every documented refusal:
            # kickoff precedes the fit cutoff, fixture league-season is
            # not an explicit target, fixture round at/before a fitted
            # round. Malformed fixtures also refuse (never a fallback).
            return None


#: Module-level singleton family instance.
SOCCER_RECALIB = SoccerRecalibFamily()
