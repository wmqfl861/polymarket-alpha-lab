"""Crypto GBM barrier family with a swappable volatility estimator (MO-5d).

Design ``docs/handoffs/2026-11-19-audit-optimizer/04-optimizer-design.md``
sections 3, 7 and 8; plan ``05-development-plan.md`` node MO-5 row 5d.
This module wraps :mod:`papersim.crypto_model` — FROZEN, wrapped only,
never modified (its LF-stream sha256 is pinned in this node's tests) —
and contributes the one thing the frozen module does not have: the
volatility estimator as an explicit, swappable component.  Everything
here is a pure state function (estimator code) plus thin delegation to
the frozen probability math; no search, no storage, no network, no
CLI wiring (MO-6 owns the commands, MO-10 the historical dataset).

Search space (first version, versioned per design 04 section 7: frozen
HERE and in the node record, never widened after seeing results):

==================  ==============================================
vol_mode            {"window", "ew"} (center "window")
vol_weights         six pre-registered weight tuples over
                    (7d, 30d, 60d, 87d): the four single windows
                    plus (0.0, 0.5, 0.5, 0.0) and
                    (0.25, 0.25, 0.25, 0.25); default = the 30d
                    single window (the r19 pricing path)
vol_ew_decay        per-6h-bar decay {0.99, 0.995, 0.999} (half
                    lives ~69/138/693 bars = 17.3/34.6/173 days);
                    default 0.995, effective only in "ew" mode
==================  ==============================================

The 04 section 7 tunable "term-layered scale correction" is
deliberately NOT in v1: it needs per-expiry settled-market data that
only MO-10 provides.  Widening is a new node plan plus review, never
a silent edit (design 04 section 7, last paragraph).

Estimator semantics (both are uncentered RMS estimators at daily
scale, r19-faithful: the sample mean is never subtracted — a
one-sided run of same-sign returns is vol, not zero):

- ``window`` mode pools the FROZEN multi-window table
  (``crypto_model.realized_vol_windows``, delegated, never
  reimplemented) by variance weights
  ``sqrt(sum(w_i * s_i^2) / sum(w_i))``.  With exactly one nonzero
  weight the selected window's value is returned unchanged — a
  DEFENSIVE short-circuit that removes any dependence on IEEE
  rounding details of the pooled expression, not a claim that
  ``sqrt(s * s) != s`` occurs in this value range (the review found
  zero counterexamples).
- ``ew`` mode is the normalized exponentially weighted RMS over the
  most recent ``EW_SUPPORT_BARS`` (348) 6h closes:
  ``sqrt(sum(lam^k * r_k^2) / sum(lam^k)) * sqrt(24 / 6)`` with the
  newest return weighted 1.

Short-history semantics (the two modes differ, deliberately):

- ``window`` mode on the closes path refuses fewer closes than the
  LARGEST SELECTED window requires (the 87d tuple needs 348, the 30d
  baseline 120).  The frozen ``realized_vol_windows`` silently runs
  on whatever exists (the r19 compatibility slice, see its own
  docstring); this family never lets that slice pass a short history
  off as a complete window — the same direction as the sanctioned
  bridge ``adapters/prices.closes_for_vol_window``.
- ``ew`` mode REQUIRES at least 348 closes and computes over exactly
  the most recent 348 (its support is fixed, independent of the
  decay; with only 347 returns available lam=0.999 is near-flat over
  the support, a registered choice, not a bug).

History payload: exactly one of ``closes`` (per-asset complete 6h
closes — the estimation input) or ``vol_table`` (per-asset frozen
multi-window sigma table — the FROZEN-RECORD REPLAY input: the r19
book of record stores the computed table, not the raw klines, and the
selfcheck anchor reads the same table).  ``vol_table`` is refused in
``ew`` mode: exponential weighting is defined over closes only.

Envelope semantics: the crypto sensitivity band is the code-frozen
A/B drift-convention pair itself — ``touch_probability`` and
``terminal_probability`` always return both conventions and
``conservative_edges`` prices off the conservative end of the band
(min/max), so NO spec can narrow it (design 04 section 7 crypto row:
"the two drift conventions stay unchanged").  The spec's envelope
tuple is therefore EMPTY, mirroring the empty-envelope lineage
precedent of ``intl-elo-goal-residuals``; scorecard semantics for
empty envelopes are defined by MO-6 explicitly.

Anchor acceptance (plan 05 node MO-5 row 5d): the three frozen
selfcheck anchors (ETH-2000 / ETH-2250 / BTC-65k) constrain ONLY the
default spec — window mode, 30d single window.  Replayed through the
``vol_table`` fit path they reproduce the fixture's A/B values within
the selfcheck tolerance (1e-4; the fixture stores four-decimal
values) and match the direct ``crypto_model.touch_probability`` call
bit for bit.  The ``ew`` estimator has NO frozen anchor; it is pinned
by synthetic-closes unit tests (lam -> 1 approaches the flat RMS,
weight normalization, recency monotonicity, refusal boundaries).

Model-version strings: the baseline keeps the pipeline's frozen
``MODEL_VERSION_CRYPTO`` as its ``legacy_model_version`` (referenced
from ``pipeline``, never copied as a literal), so the empty-registry
scan keeps today's queue identity (the B1 bit-identity anchor).
``PROCEDURE_VERSION`` is the estimation CODE generation ("crypto-r19"
= the frozen module as merged, "r19e1" = the estimator component
added by MO-5d; default behavior bit-identical — the NBA "wp-nba2"
precedent), so derived non-baseline versions read
"crypto-r19e1+<sha12>", visually distinct from the legacy alias
"crypto-r19+fx13-2026-11-12".

Import discipline: this module imports ``crypto_model`` and
``pipeline`` (``crypto_model`` is already in the papersim package
baseline; ``pipeline`` is the same pure legacy-version reference
``soccer_frozen`` makes), so it loads lazily through the PEP 562
registry in ``optimize.families``; importing
``polymarket_alpha_lab.papersim.optimize`` alone adds only
``optimize`` and ``optimize.spec``.
"""

from __future__ import annotations

import dataclasses
import datetime
import math
from collections.abc import Mapping, Sequence

from polymarket_alpha_lab.papersim import crypto_model, pipeline
from polymarket_alpha_lab.papersim.optimize.spec import (
    ModelSpec,
    ParamDef,
    freeze_value,
    validate_param,
)

__all__ = [
    "CRYPTO_GBM",
    "DATA_CONTRACT",
    "EW_DECAY_CHOICES",
    "EW_SUPPORT_BARS",
    "FAMILY_ID",
    "PARAM_SPACE",
    "PROCEDURE_VERSION",
    "TARGETS",
    "WEIGHT_MIX_30_60",
    "WEIGHT_MIX_ALL",
    "WEIGHT_SINGLE_7D",
    "WEIGHT_SINGLE_30D",
    "WEIGHT_SINGLE_60D",
    "WEIGHT_SINGLE_87D",
    "WINDOW_LABELS",
    "CryptoFamilyFitted",
    "CryptoFamilyFixture",
    "CryptoFamilyHistory",
    "CryptoGbmFamily",
    "effective_sigma",
    "ew_sigma",
    "spec_with",
    "window_sigma",
]

#: Family id (design 04 section 3: "crypto GBM").  MUST equal the key
#: ``optimize/provider.py`` registers for crypto lineages: B1's local
#: baseline definition was consumed by this module (single-sourced
#: identity, enforced by tests on both sides).
FAMILY_ID = "crypto-gbm"

#: Version of the estimation CODE, not of the parameters: "e1" is the
#: first estimator generation (the swappable volatility estimator
#: added by MO-5d; default behavior bit-identical to the frozen r19
#: path).  See the module docstring for the naming rationale.
PROCEDURE_VERSION = "crypto-r19e1"

#: Forward placeholder: the history payload contract (per-asset
#: complete 6h closes, or a frozen multi-window sigma table for
#: replay).  Freely adjustable only until the FIRST REAL
#: ``optimize init`` writes baseline specs into the registry (MO-6
#: wires that command); after that, changing it re-identifies every
#: stored baseline spec (same caveat as soccer_frozen.DATA_CONTRACT).
#: MO-10's historical market dataset may supersede it.
DATA_CONTRACT = "crypto-frozen-v1"

#: Market types this family scores.  Vocabulary mirrors the pipeline
#: crypto kinds (``crypto_touch`` / ``crypto_terminal``); align with
#: MO-3/MO-6 before the first real init (same caveat as nba.py).
TARGETS: tuple[str, ...] = ("touch", "terminal")

#: The frozen window labels in ``crypto_model.VOL_WINDOW_BARS`` key
#: order.  The order defines the positions of every ``vol_weights``
#: tuple and is part of the spec identity (pinned by tests).
WINDOW_LABELS: tuple[str, ...] = tuple(crypto_model.VOL_WINDOW_BARS)

#: The ``ew`` support: the longest frozen window (348 6h bars = 87d),
#: derived from the frozen table, never duplicated as a literal.
EW_SUPPORT_BARS = max(crypto_model.VOL_WINDOW_BARS.values())

#: Per-6h-bar decay choices.  Half-lives: lam=0.99 -> ~69 bars
#: (~17.3d), 0.995 -> ~138 bars (~34.6d), 0.999 -> ~693 bars
#: (~173d); the 30d window (120 bars) sits between 0.995 and 0.999.
EW_DECAY_CHOICES: tuple[float, ...] = (0.99, 0.995, 0.999)

#: The six pre-registered weight tuples (design 04 section 7: windows
#: {7, 30, 60, 87}d "and their weighting").  All entries are float
#: literals (0.0, never 0) so the canonical spec text never mixes
#: ``i:0`` with ``f:0.0`` (the MO-1 type-normalization handover).
WEIGHT_SINGLE_7D = (1.0, 0.0, 0.0, 0.0)
WEIGHT_SINGLE_30D = (0.0, 1.0, 0.0, 0.0)  # baseline: the r19 pricing path
WEIGHT_SINGLE_60D = (0.0, 0.0, 1.0, 0.0)
WEIGHT_SINGLE_87D = (0.0, 0.0, 0.0, 1.0)
WEIGHT_MIX_30_60 = (0.0, 0.5, 0.5, 0.0)
WEIGHT_MIX_ALL = (0.25, 0.25, 0.25, 0.25)

#: First-version search space (see the module docstring table).
PARAM_SPACE: tuple[ParamDef, ...] = (
    ParamDef("vol_mode", "choice", "window", choices=("window", "ew")),
    ParamDef(
        "vol_weights",
        "choice",
        WEIGHT_SINGLE_30D,
        choices=(
            WEIGHT_SINGLE_7D,
            WEIGHT_SINGLE_30D,
            WEIGHT_SINGLE_60D,
            WEIGHT_SINGLE_87D,
            WEIGHT_MIX_30_60,
            WEIGHT_MIX_ALL,
        ),
    ),
    ParamDef("vol_ew_decay", "choice", 0.995, choices=EW_DECAY_CHOICES),
)


# ---------------------------------------------------------------------------
# Estimator state functions (pure)
# ---------------------------------------------------------------------------


def _checked_weights(weights: object) -> tuple[float, ...]:
    """Validate a weight tuple against ``WINDOW_LABELS``; return plain floats."""

    if isinstance(weights, (str, bytes)) or not isinstance(weights, Sequence):
        raise ValueError(
            f"vol_weights must be a sequence, got {type(weights).__name__}"
        )
    values = tuple(weights)
    if len(values) != len(WINDOW_LABELS):
        raise ValueError(
            f"vol_weights must carry one weight per window {WINDOW_LABELS}, "
            f"got {len(values)} weights"
        )
    for weight in values:
        if isinstance(weight, bool) or not isinstance(weight, float):
            raise ValueError(f"vol_weights entries must be floats, got {weight!r}")
        if not math.isfinite(weight) or weight < 0.0:
            raise ValueError(
                f"vol_weights entries must be finite and >= 0.0, got {weight!r}"
            )
    if sum(values) <= 0.0:
        raise ValueError("vol_weights needs at least one positive weight")
    return values


def _selected(weights: Sequence[float]) -> list[tuple[str, float]]:
    """The (label, weight) pairs with nonzero weight, in WINDOW_LABELS order."""

    return [
        (label, weight)
        for label, weight in zip(WINDOW_LABELS, weights)
        if weight > 0.0
    ]


def _pool(sigmas: Mapping[str, float], selected: Sequence[tuple[str, float]]) -> float:
    """Variance-pool selected window sigmas; a single window passes through.

    With exactly one selected window its sigma is returned unchanged —
    a defensive short-circuit removing any dependence on IEEE rounding
    details of the pooled expression (see the module docstring).
    """

    if len(selected) == 1:
        return sigmas[selected[0][0]]
    weight_total = 0.0
    variance = 0.0
    for label, weight in selected:
        sigma = sigmas[label]
        variance += weight * (sigma * sigma)
        weight_total += weight
    return math.sqrt(variance / weight_total)


def window_sigma(closes: Sequence[float], weights: Sequence[float]) -> float:
    """Pool the frozen multi-window vol table by variance weights (pure).

    The per-window sigmas come from ``crypto_model.realized_vol_windows``
    — delegated, never reimplemented.  Refuses fewer closes than the
    LARGEST SELECTED window requires (module docstring, short-history
    semantics); the frozen estimator's r19 compatibility slice must not
    pass a short history off as a complete window through this family.
    """

    checked = _checked_weights(weights)
    selected = _selected(checked)
    for label, _ in selected:
        required = crypto_model.VOL_WINDOW_BARS[label]
        if len(closes) < required:
            raise ValueError(
                f"window {label!r} needs {required} closes, got {len(closes)}; "
                "a short history must not pass as a complete window"
            )
    return _pool(crypto_model.realized_vol_windows(closes), selected)


def ew_sigma(closes: Sequence[float], decay: float) -> float:
    """Normalized uncentered exponentially weighted RMS at daily scale (pure).

    Requires at least ``EW_SUPPORT_BARS`` (348) closes and computes
    over exactly the most recent 348; the newest return carries weight
    1 and the k-bars-old return carries ``decay ** k``.  The weight-sum
    normalization makes constant returns give ``abs(r) * 2`` up to
    float epsilon, and the returns are uncentered exactly as the frozen
    ``realized_vol_daily`` computes them (the sample mean is never
    subtracted).
    """

    if isinstance(decay, bool) or not isinstance(decay, float):
        raise ValueError(f"vol_ew_decay must be a float, got {decay!r}")
    if not 0.0 < decay <= 1.0:
        raise ValueError(f"vol_ew_decay must lie in (0, 1], got {decay!r}")
    if len(closes) < EW_SUPPORT_BARS:
        raise ValueError(
            f"the ew estimator needs {EW_SUPPORT_BARS} closes, got {len(closes)}"
        )
    window = closes[-EW_SUPPORT_BARS:]
    if any(close <= 0.0 for close in window):
        raise ValueError("closes must be positive prices")
    # Newest return first: index i walks from the last bar backwards so
    # returns[0] is the most recent complete bar's log return.
    returns = [
        math.log(window[i] / window[i - 1])
        for i in range(len(window) - 1, 0, -1)
    ]
    weight_total = 0.0
    weighted_squares = 0.0
    factor = 1.0
    for value in returns:
        weighted_squares += factor * (value * value)
        weight_total += factor
        factor *= decay
    return math.sqrt(weighted_squares / weight_total) * math.sqrt(24.0 / 6.0)


def _checked_estimator_params(params: Mapping[str, object]) -> dict[str, object]:
    """Validate a full estimator param mapping against PARAM_SPACE."""

    by_name = {defn.name: defn for defn in PARAM_SPACE}
    given = dict(params)
    unknown = sorted(set(given) - set(by_name))
    if unknown:
        raise ValueError(f"{FAMILY_ID}: unknown params {unknown}")
    missing = sorted(set(by_name) - set(given))
    if missing:
        raise ValueError(f"{FAMILY_ID}: missing params {missing}")
    for name, defn in by_name.items():
        validate_param(defn, given[name])
    return given


def effective_sigma(params: Mapping[str, object], closes: Sequence[float]) -> float:
    """The daily sigma of one spec's estimator over ``closes`` (pure dispatch)."""

    checked = _checked_estimator_params(params)
    if checked["vol_mode"] == "window":
        return window_sigma(closes, checked["vol_weights"])
    return ew_sigma(closes, checked["vol_ew_decay"])


# ---------------------------------------------------------------------------
# History / fixture payloads and the fitted state
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class CryptoFamilyHistory:
    """Fit inputs: complete 6h closes OR a frozen multi-window sigma table.

    Exactly one of the two fields must be given: ``closes`` is the
    estimation input (per-asset complete 6h bar closes; the family
    refuses histories shorter than the selected estimator needs);
    ``vol_table`` is the frozen-record replay input (per-asset
    window->sigma mapping, the form the r19 book of record stores).
    """

    closes: Mapping[str, Sequence[float]] | None = None
    vol_table: Mapping[str, Mapping[str, float]] | None = None


@dataclasses.dataclass(frozen=True)
class CryptoFamilyFixture:
    """One market to predict.

    ``kind`` is "touch" (first-passage) or "terminal" (at-expiry).
    Terminal fixtures must state the market's YES side via
    ``yes_when`` ("below"/"above", FX-13); touch fixtures must leave
    it ``None`` (their YES is the touch event itself).  ``spot`` /
    ``barrier`` / ``horizon_hours`` validation is the frozen model's
    own (delegated).
    """

    asset: str
    spot: float
    barrier: float
    horizon_hours: float
    kind: str = "touch"
    yes_when: str | None = None


@dataclasses.dataclass(frozen=True)
class CryptoFamilyFitted:
    """Fitted state: the effective daily sigma per asset plus the spec binding.

    The sigma mapping is deep-frozen into the fitted state so the
    recorded spec identity cannot drift from the state actually used
    (mirrors ``SoccerFrozenFitted``); ``as_of`` is provenance only.
    """

    sigma_daily: Mapping[str, float]
    spec_sha256: str
    as_of: datetime.datetime


def spec_with(**overrides: object) -> ModelSpec:
    """A spec of this family: PARAM_SPACE defaults plus ``overrides``.

    All-default ``overrides`` short-circuit to ``baseline_spec()`` so
    re-selecting the active parameters reproduces the baseline
    identity exactly — the premise the one-standard-error rule relies
    on (mirrors ``intl.spec_with``).
    """

    defaults = {defn.name: defn.default for defn in PARAM_SPACE}
    unknown = sorted(set(overrides) - set(defaults))
    if unknown:
        raise ValueError(f"{FAMILY_ID}: unknown params {unknown}")
    params = dict(defaults)
    params.update(overrides)
    _checked_estimator_params(params)  # off-surface values fail here
    if all(params[name] == defaults[name] for name in defaults):
        return CRYPTO_GBM.baseline_spec()
    return ModelSpec(
        family=FAMILY_ID,
        procedure_version=PROCEDURE_VERSION,
        params=params,
        envelope=(),
        data_contract=DATA_CONTRACT,
    )


class CryptoGbmFamily:
    """``ModelFamily`` implementation of the crypto GBM barrier model."""

    family_id = FAMILY_ID
    procedure_version = PROCEDURE_VERSION

    def param_space(self) -> tuple[ParamDef, ...]:
        """First-version space (design 04 section 7, crypto row)."""

        return PARAM_SPACE

    def baseline_spec(self) -> ModelSpec:
        """The built-in baseline: default params (= today's behavior), empty envelope.

        ``legacy_model_version`` is the pipeline's frozen crypto string
        so the empty-registry scan keeps today's queue identity (the
        B1 bit-identity anchor); non-baseline specs derive
        ``"crypto-r19e1+<sha12>"``.
        """

        return ModelSpec(
            family=FAMILY_ID,
            procedure_version=PROCEDURE_VERSION,
            params={defn.name: defn.default for defn in PARAM_SPACE},
            envelope=(),
            data_contract=DATA_CONTRACT,
            legacy_model_version=pipeline.MODEL_VERSION_CRYPTO,
        )

    def targets(self) -> tuple[str, ...]:
        return TARGETS

    def _checked_spec(self, spec: ModelSpec) -> dict[str, object]:
        """Strict fit-time validation: family, version, full params, empty envelope."""

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
        params = _checked_estimator_params(spec.params)
        if tuple(spec.envelope) != ():
            raise ValueError(
                f"{FAMILY_ID}: the envelope must be empty — the A/B drift "
                "conventions are the code-frozen sensitivity band (see "
                "the module docstring); a non-empty envelope would split "
                "identity from behavior"
            )
        return params

    def fit(
        self,
        history: CryptoFamilyHistory,
        spec: ModelSpec,
        as_of: datetime.datetime,
    ) -> CryptoFamilyFitted:
        """Bind one spec to ``history``; ``as_of`` is provenance only.

        Exactly one of ``history.closes`` / ``history.vol_table`` must
        be given (module docstring).  The vol_table path is refused in
        ``ew`` mode: exponential weighting is defined over closes only.
        """

        if not isinstance(history, CryptoFamilyHistory):
            raise TypeError(
                f"{FAMILY_ID}.fit expects CryptoFamilyHistory, "
                f"got {type(history).__name__}"
            )
        params = self._checked_spec(spec)
        if (history.closes is None) == (history.vol_table is None):
            raise ValueError(
                f"{FAMILY_ID}.fit takes exactly one of closes or vol_table, "
                f"got closes={'set' if history.closes is not None else 'None'} "
                f"and vol_table="
                f"{'set' if history.vol_table is not None else 'None'}"
            )
        if history.vol_table is not None:
            sigma_daily = self._sigma_from_vol_table(history.vol_table, params)
        else:
            sigma_daily = self._sigma_from_closes(history.closes, params)
        return CryptoFamilyFitted(
            sigma_daily=freeze_value(sigma_daily),
            spec_sha256=spec.spec_sha256(),
            as_of=as_of,
        )

    def _sigma_from_vol_table(
        self, vol_table: Mapping[str, Mapping[str, float]], params: Mapping[str, object]
    ) -> dict[str, float]:
        """Frozen-record replay: pool the table's selected windows per asset."""

        if params["vol_mode"] == "ew":
            raise ValueError(
                f"{FAMILY_ID}: vol_table replay is window-mode only; the ew "
                "estimator is defined over closes"
            )
        selected = _selected(params["vol_weights"])
        sigma_daily: dict[str, float] = {}
        for asset, table in vol_table.items():
            if not isinstance(asset, str) or not asset:
                raise ValueError("vol_table assets must be non-empty strings")
            if not isinstance(table, Mapping):
                raise ValueError(
                    f"vol_table[{asset!r}] must be a window->sigma mapping, "
                    f"got {type(table).__name__}"
                )
            missing = [label for label, _ in selected if label not in table]
            if missing:
                raise ValueError(
                    f"vol_table[{asset!r}] is missing the selected windows "
                    f"{missing}"
                )
            for label, _ in selected:
                sigma = table[label]
                if isinstance(sigma, bool) or not isinstance(sigma, float):
                    raise ValueError(
                        f"vol_table[{asset!r}][{label!r}] must be a float, "
                        f"got {sigma!r}"
                    )
                if not math.isfinite(sigma) or sigma <= 0.0:
                    raise ValueError(
                        f"vol_table[{asset!r}][{label!r}] must be a positive "
                        f"finite sigma, got {sigma!r}"
                    )
            sigma_daily[asset] = _pool(table, selected)
        return sigma_daily

    def _sigma_from_closes(
        self, closes: Mapping[str, Sequence[float]], params: Mapping[str, object]
    ) -> dict[str, float]:
        """Estimation path: run the spec's estimator over each asset's closes."""

        sigma_daily: dict[str, float] = {}
        for asset, series in closes.items():
            if not isinstance(asset, str) or not asset:
                raise ValueError("closes assets must be non-empty strings")
            sigma_daily[asset] = effective_sigma(params, series)
        return sigma_daily

    def predict(
        self, fitted: CryptoFamilyFitted, fixture: CryptoFamilyFixture
    ) -> crypto_model.TouchProbability | crypto_model.TerminalYesProbability | None:
        """Predict one market; ``None`` is an explicit refusal, never a fallback.

        Pure delegation to the frozen probability math: touch markets
        get the ``TouchProbability`` record; terminal markets get the
        FX-13 YES-oriented ``TerminalYesProbability`` (the caller never
        re-orients, so an ``above on date`` market cannot be priced as
        below-barrier by omission).  An asset missing from the fitted
        sigma state refuses with ``None``.
        """

        if not isinstance(fitted, CryptoFamilyFitted):
            raise TypeError(
                f"{FAMILY_ID}.predict expects CryptoFamilyFitted, "
                f"got {type(fitted).__name__}"
            )
        if not isinstance(fixture, CryptoFamilyFixture):
            raise TypeError(
                f"{FAMILY_ID}.predict expects CryptoFamilyFixture, "
                f"got {type(fixture).__name__}"
            )
        if fixture.asset not in fitted.sigma_daily:
            return None
        sigma = fitted.sigma_daily[fixture.asset]
        if fixture.kind == "touch":
            if fixture.yes_when is not None:
                raise ValueError(
                    "touch fixtures take no yes_when (YES is the touch event)"
                )
            return crypto_model.touch_probability(
                fixture.spot,
                sigma,
                fixture.barrier,
                fixture.horizon_hours,
                asset=fixture.asset,
            )
        if fixture.kind == "terminal":
            if fixture.yes_when not in ("below", "above"):
                raise ValueError(
                    "terminal fixtures need yes_when 'below' or 'above', got "
                    f"{fixture.yes_when!r}"
                )
            record = crypto_model.terminal_probability(
                fixture.spot,
                sigma,
                fixture.barrier,
                fixture.horizon_hours,
                asset=fixture.asset,
            )
            return crypto_model.terminal_yes_probability(
                record, yes_when=fixture.yes_when
            )
        raise ValueError(
            f"kind must be 'touch' or 'terminal', got {fixture.kind!r}"
        )


#: Module-level singleton family instance.
CRYPTO_GBM = CryptoGbmFamily()
