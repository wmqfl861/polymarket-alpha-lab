"""MO-5d crypto family proofs (offline; plan 05 node MO-5 row 5d).

Acceptance anchors:

- The frozen module is untouched: ``crypto_model.py``'s LF-stream
  sha256 is pinned below (measured at main @ 3e52516f; recompute and
  re-register — never silently update — if a rebase moves it).
- The three frozen selfcheck anchors (ETH-2000 / ETH-2250 / BTC-65k)
  constrain ONLY the default spec (window mode, 30d single window),
  replayed through the ``vol_table`` fit path: within the selfcheck
  tolerance 1e-4 against the fixture's four-decimal values, and BIT
  EXACT against the direct ``crypto_model.touch_probability`` call.
- The ``ew`` estimator has NO frozen anchor; it is pinned by the
  synthetic-closes properties below (lam -> 1 approaches the flat
  RMS, weight normalization, recency monotonicity, refusal bounds).
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import math
import pathlib
import subprocess
import sys
import textwrap

import pytest

from polymarket_alpha_lab.papersim import crypto_model, pipeline
from polymarket_alpha_lab.papersim.optimize.families import crypto as crypto_family
from polymarket_alpha_lab.papersim.optimize.families.crypto import (
    CRYPTO_GBM,
    DATA_CONTRACT,
    EW_DECAY_CHOICES,
    EW_SUPPORT_BARS,
    FAMILY_ID,
    PARAM_SPACE,
    PROCEDURE_VERSION,
    TARGETS,
    WEIGHT_MIX_30_60,
    WEIGHT_MIX_ALL,
    WEIGHT_SINGLE_30D,
    WEIGHT_SINGLE_87D,
    WINDOW_LABELS,
    CryptoFamilyFixture,
    CryptoFamilyHistory,
    CryptoGbmFamily,
    ew_sigma,
    spec_with,
    window_sigma,
)
_AS_OF = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.timezone.utc)

#: The frozen module's LF-stream digest (see the module docstring).
PINNED_CRYPTO_MODEL_LF_SHA256 = (
    "5d31a34263a6346d284385572a41a80a5942a42c25e63f322339833f732e8713"
)

#: Baseline spec identity, computed once from the module as merged
#: (any edit to params, envelope, data contract or procedure version
#: must re-derive this anchor deliberately — recalib's pin precedent).
PINNED_BASELINE_SHA256 = (
    "33b3b294b44eac5a00e48021b84320f30946fdd507dd73989da7c2312b8915b9"
)

_CHECK_TOLERANCE = 1e-4  # the selfcheck tolerance (papersim/cli.py)


def _fixture() -> dict:
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "tests"
        / "fixtures"
        / "papersim"
        / "regression_r19_crypto.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _r19_horizon_hours(fixture: dict) -> float:
    """Recompute the r19 ``T`` exactly as the selfcheck helper does."""
    anchor = datetime.datetime.fromisoformat(
        fixture["anchor"]["raw_scan_generated_at"]
    )
    dec31 = datetime.datetime(2026, 12, 31, 23, 59, tzinfo=datetime.timezone.utc)
    return (dec31 - anchor).total_seconds() / 86400.0 * 24.0


def _synthetic_closes(count: int = 348) -> list[float]:
    """Deterministic positive closes (no RNG, stable across runs)."""
    return [
        2326.84 * (1.0 + 0.01 * math.sin(0.37 * i) + 0.002 * math.cos(0.11 * i))
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# Family shape, registry, search space
# ---------------------------------------------------------------------------


def test_family_shape_and_registry() -> None:
    assert isinstance(CRYPTO_GBM, CryptoGbmFamily)
    assert CRYPTO_GBM.family_id == FAMILY_ID == "crypto-gbm"
    assert CRYPTO_GBM.procedure_version == PROCEDURE_VERSION == "crypto-r19e1"
    assert CRYPTO_GBM.targets() == TARGETS == ("touch", "terminal")
    assert CRYPTO_GBM.param_space() == PARAM_SPACE
    names = tuple(defn.name for defn in PARAM_SPACE)
    assert names == ("vol_mode", "vol_weights", "vol_ew_decay")
    from polymarket_alpha_lab.papersim.optimize.families import iter_family_ids

    assert "crypto-gbm" in iter_family_ids()


def test_window_labels_and_support_derive_from_the_frozen_module() -> None:
    # The label ORDER defines vol_weights positions and is part of the
    # spec identity; EW_SUPPORT_BARS is derived, never duplicated.
    assert WINDOW_LABELS == ("7d", "30d", "60d", "87d")
    assert WINDOW_LABELS == tuple(crypto_model.VOL_WINDOW_BARS)
    assert EW_SUPPORT_BARS == 348 == max(crypto_model.VOL_WINDOW_BARS.values())


def test_param_space_grids_match_the_registered_search_space() -> None:
    # Version-frozen v1 grids (design 04 section 7; node record). Any
    # widening is a new node plan plus review, never a test edit.
    by_name = {defn.name: defn for defn in PARAM_SPACE}
    assert by_name["vol_mode"].choices == ("window", "ew")
    assert by_name["vol_mode"].default == "window"
    assert by_name["vol_weights"].choices == (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
        (0.0, 0.5, 0.5, 0.0),
        (0.25, 0.25, 0.25, 0.25),
    )
    assert by_name["vol_weights"].default == (0.0, 1.0, 0.0, 0.0)
    assert by_name["vol_ew_decay"].choices == EW_DECAY_CHOICES == (0.99, 0.995, 0.999)
    assert by_name["vol_ew_decay"].default == 0.995
    # Type normalization (MO-1 handover): every weight entry is a float
    # literal (0.0, never 0), never a bool.
    for choice in by_name["vol_weights"].choices:
        assert all(
            isinstance(weight, float) and not isinstance(weight, bool)
            for weight in choice
        )
    assert all(
        isinstance(choice, float) and not isinstance(choice, bool)
        for choice in EW_DECAY_CHOICES
    )


# ---------------------------------------------------------------------------
# Estimator: window mode (delegation and pooling)
# ---------------------------------------------------------------------------


def test_default_window_sigma_is_bit_identical_to_the_frozen_path() -> None:
    # Delegation, not reimplementation: the baseline estimator output
    # equals the frozen 30d slice exactly, and every single-window
    # choice equals its frozen table entry (the defensive
    # single-nonzero-weight short-circuit).
    closes = _synthetic_closes()
    table = crypto_model.realized_vol_windows(closes)
    assert window_sigma(closes, WEIGHT_SINGLE_30D) == table["30d"]
    assert table["30d"] == crypto_model.realized_vol_daily(closes[-120:])
    for weights, label in (
        ((1.0, 0.0, 0.0, 0.0), "7d"),
        ((0.0, 0.0, 1.0, 0.0), "60d"),
        ((0.0, 0.0, 0.0, 1.0), "87d"),
    ):
        assert window_sigma(closes, weights) == table[label]


def test_pooled_mixes_match_variance_pooling_and_stay_inside_the_band() -> None:
    closes = _synthetic_closes()
    table = crypto_model.realized_vol_windows(closes)
    s30, s60 = table["30d"], table["60d"]
    mix = window_sigma(closes, WEIGHT_MIX_30_60)
    assert mix == math.sqrt((0.5 * (s30 * s30) + 0.5 * (s60 * s60)) / 1.0)
    assert min(s30, s60) < mix < max(s30, s60)
    all_mix = window_sigma(closes, WEIGHT_MIX_ALL)
    values = [table[label] for label in WINDOW_LABELS]
    expected = math.sqrt(sum(0.25 * (s * s) for s in values) / 1.0)
    assert all_mix == expected
    assert min(values) < all_mix < max(values)


def test_window_mode_refuses_short_history_for_the_selected_windows() -> None:
    # P3-3 semantics: the refusal bound follows the LARGEST SELECTED
    # window, not a blanket 348 — the r19 silent slice must not pass a
    # short history off as a complete window.
    closes = _synthetic_closes()
    # 30d needs 120: 119 refused, 120 accepted and bit-identical.
    with pytest.raises(ValueError, match="window '30d' needs 120 closes"):
        CRYPTO_GBM.fit(
            CryptoFamilyHistory(closes={"ETH": closes[:119]}),
            CRYPTO_GBM.baseline_spec(),
            _AS_OF,
        )
    fitted = CRYPTO_GBM.fit(
        CryptoFamilyHistory(closes={"ETH": closes[:120]}),
        CRYPTO_GBM.baseline_spec(),
        _AS_OF,
    )
    assert fitted.sigma_daily["ETH"] == crypto_model.realized_vol_daily(closes[:120])
    # The 87d single window needs 348: 240 refused, 348 accepted.
    spec_87d = spec_with(vol_weights=WEIGHT_SINGLE_87D)
    with pytest.raises(ValueError, match="window '87d' needs 348 closes"):
        CRYPTO_GBM.fit(
            CryptoFamilyHistory(closes={"ETH": closes[:240]}), spec_87d, _AS_OF
        )
    CRYPTO_GBM.fit(CryptoFamilyHistory(closes={"ETH": closes}), spec_87d, _AS_OF)
    # The 30/60 mix selects 60d too: 120 closes are not enough.
    with pytest.raises(ValueError, match="window '60d' needs 240 closes"):
        CRYPTO_GBM.fit(
            CryptoFamilyHistory(closes={"ETH": closes[:120]}),
            spec_with(vol_weights=WEIGHT_MIX_30_60),
            _AS_OF,
        )


# ---------------------------------------------------------------------------
# Estimator: ew mode (synthetic-closes properties, no frozen anchor)
# ---------------------------------------------------------------------------


def test_ew_support_is_fixed_at_348_closes() -> None:
    closes = _synthetic_closes()
    with pytest.raises(ValueError, match="needs 348 closes"):
        ew_sigma(closes[:347], 0.995)
    # The estimator sees exactly the most recent 348 closes: anything
    # older is invisible (bit-identical sigma).
    longer = [1000.0 * 1.001 ** i for i in range(100)] + closes
    assert ew_sigma(longer, 0.995) == ew_sigma(closes, 0.995)


def test_ew_lambda_one_approaches_the_flat_rms() -> None:
    # No bit-exactness claim: the accumulation order differs from the
    # frozen flat RMS by design; the limit agreement is within 1e-12.
    closes = _synthetic_closes()
    flat = crypto_model.realized_vol_daily(closes[-348:])
    assert abs(ew_sigma(closes, 1.0) - flat) <= 1e-12
    for decay in EW_DECAY_CHOICES:
        assert math.isfinite(ew_sigma(closes, decay))


def test_ew_constant_returns_normalize_to_the_bar_return() -> None:
    # Powers of two divide exactly, so every log return is log(2.0)
    # and the weight-sum normalization must give |log 2| * 2 up to
    # float epsilon.
    closes = [2.0**i for i in range(348)]
    want = math.log(2.0) * math.sqrt(24.0 / 6.0)
    assert abs(ew_sigma(closes, 0.995) - want) <= 1e-12


def test_ew_recency_monotonicity() -> None:
    def series(old_ret: float, new_ret: float) -> list[float]:
        values = [1000.0]
        for i in range(347):
            ret = old_ret if i < 174 else new_ret
            values.append(values[-1] * math.exp(ret))
        return values

    old_calm_new_wild = series(0.0005, 0.02)
    old_wild_new_calm = old_calm_new_wild[::-1]  # mirrored path
    wild_now = ew_sigma(old_calm_new_wild, 0.99)
    wild_then = ew_sigma(old_calm_new_wild, 0.999)
    # Recent volatility dominates at fast decay; slow decay keeps more
    # of the calm history, so sigma drops.
    assert wild_now > wild_then
    calm_now = ew_sigma(old_wild_new_calm, 0.99)
    calm_then = ew_sigma(old_wild_new_calm, 0.999)
    # Mirrored: slow decay keeps more of the wild history.
    assert calm_now < calm_then


def test_ew_decay_validation() -> None:
    closes = _synthetic_closes()
    for bad in (0.0, -0.5, 1.5, 2.0, 1, True, "0.99"):
        with pytest.raises(ValueError):
            ew_sigma(closes, bad)
    assert ew_sigma(closes, 1.0) > 0.0


def test_window_weight_validation() -> None:
    closes = _synthetic_closes()
    bad_weights = (
        (1, 0, 0, 0),  # ints, not floats
        (0.0, 1.0),  # wrong arity
        (0.0, 0.0, 0.0, 0.0),  # nothing selected
        (-1.0, 1.0, 0.0, 0.0),  # negative
        "0100",  # not a sequence of floats at all
    )
    for bad in bad_weights:
        with pytest.raises(ValueError):
            window_sigma(closes, bad)


# ---------------------------------------------------------------------------
# Frozen anchor replay (the default spec only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_key", ["ETH-2000", "ETH-2250", "BTC-65k"])
def test_default_spec_reproduces_the_frozen_anchor(case_key: str) -> None:
    fixture = _fixture()
    case = fixture["cases"][case_key]
    vol_table = {
        asset: dict(table)
        for asset, table in fixture["inputs"]["realized_vol_daily"].items()
    }
    assert fixture["inputs"]["sig_table"] == "30d"  # the frozen pricing window
    fitted = CRYPTO_GBM.fit(
        CryptoFamilyHistory(vol_table=vol_table), CRYPTO_GBM.baseline_spec(), _AS_OF
    )
    assert dict(fitted.sigma_daily) == {
        "ETH": 0.02430307766195731, "BTC": 0.01583874457257652
    }
    horizon = _r19_horizon_hours(fixture)
    assert round(horizon, 2) == float(case["horizon_hours"])
    spot = float(fixture["inputs"]["spot"][case["asset"]])
    probs = CRYPTO_GBM.predict(
        fitted,
        CryptoFamilyFixture(
            asset=str(case["asset"]),
            spot=spot,
            barrier=float(case["barrier"]),
            horizon_hours=horizon,
            kind="touch",
        ),
    )
    assert isinstance(probs, crypto_model.TouchProbability)
    # Same tolerance semantics as the selfcheck: the fixture stores
    # four-decimal values (measured margins: |dA| <= 2.7e-5).
    assert abs(probs.convention_a - float(case["A"])) <= _CHECK_TOLERANCE
    assert abs(probs.convention_b - float(case["B"])) <= _CHECK_TOLERANCE
    # All three frozen barriers sit below spot: mean-flat B touches more.
    assert probs.convention_b > probs.convention_a
    # Bit-exact against the direct frozen call with the same inputs.
    direct = crypto_model.touch_probability(
        spot=spot,
        sigma_daily=fitted.sigma_daily[str(case["asset"])],
        barrier=float(case["barrier"]),
        horizon_hours=horizon,
        asset=str(case["asset"]),
    )
    assert probs.convention_a == direct.convention_a
    assert probs.convention_b == direct.convention_b


# ---------------------------------------------------------------------------
# Spec identity and fit strictness
# ---------------------------------------------------------------------------


def test_baseline_identity_is_pinned() -> None:
    baseline = CRYPTO_GBM.baseline_spec()
    assert baseline.spec_sha256() == PINNED_BASELINE_SHA256
    assert baseline.family == FAMILY_ID
    assert baseline.procedure_version == PROCEDURE_VERSION
    assert baseline.data_contract == DATA_CONTRACT == "crypto-frozen-v1"
    assert dict(baseline.params) == {
        "vol_mode": "window",
        "vol_weights": (0.0, 1.0, 0.0, 0.0),
        "vol_ew_decay": 0.995,
    }
    assert tuple(baseline.envelope) == ()
    # The legacy alias rides model_version but stays OUT of the spec
    # identity: renaming it cannot re-key a stored spec.
    assert baseline.model_version() == pipeline.MODEL_VERSION_CRYPTO
    assert baseline.legacy_model_version == pipeline.MODEL_VERSION_CRYPTO
    assert "crypto-r19+fx13" not in baseline.canonical_json()
    # Two constructions are identical (nothing is cached or random).
    assert baseline.spec_sha256() == CRYPTO_GBM.baseline_spec().spec_sha256()


def test_spec_with_overrides_and_derived_version_strings() -> None:
    baseline = CRYPTO_GBM.baseline_spec()
    assert spec_with().spec_sha256() == baseline.spec_sha256()
    assert spec_with(vol_mode="window").spec_sha256() == baseline.spec_sha256()
    ew_spec = spec_with(vol_mode="ew")
    assert ew_spec.spec_sha256() != baseline.spec_sha256()
    assert ew_spec.model_version() == (
        f"{PROCEDURE_VERSION}+{ew_spec.spec_sha256()[:12]}"
    )
    assert ew_spec.legacy_model_version is None
    with pytest.raises(ValueError, match="unknown params"):
        spec_with(half_life=20.0)
    with pytest.raises(ValueError):
        spec_with(vol_mode="flat")  # off the registered grid
    with pytest.raises(ValueError):
        spec_with(vol_weights=(0.1, 0.2, 0.3, 0.4))  # off the registered grid


def test_fit_rejects_invalid_specs() -> None:
    history = CryptoFamilyHistory(closes={"ETH": _synthetic_closes()})
    baseline = CRYPTO_GBM.baseline_spec()
    with pytest.raises(ValueError, match="family"):
        CRYPTO_GBM.fit(
            history,
            dataclasses.replace(baseline, family="club-soccer-frozen"),
            _AS_OF,
        )
    with pytest.raises(ValueError, match="procedure_version"):
        CRYPTO_GBM.fit(
            history,
            dataclasses.replace(baseline, procedure_version="crypto-r19"),
            _AS_OF,
        )
    unknown = dataclasses.replace(
        baseline,
        params={**dict(baseline.params), "vol_floor": 0.01},
    )
    with pytest.raises(ValueError, match="unknown params"):
        CRYPTO_GBM.fit(history, unknown, _AS_OF)
    short = dataclasses.replace(
        baseline, params={"vol_mode": "window", "vol_weights": WEIGHT_SINGLE_30D}
    )
    with pytest.raises(ValueError, match="missing params"):
        CRYPTO_GBM.fit(history, short, _AS_OF)
    tampered_envelope = dataclasses.replace(baseline, envelope=({"note": "x"},))
    with pytest.raises(ValueError, match="envelope"):
        CRYPTO_GBM.fit(history, tampered_envelope, _AS_OF)
    with pytest.raises(TypeError):
        CRYPTO_GBM.fit({"closes": {}}, baseline, _AS_OF)  # type: ignore[arg-type]


def test_fit_history_payload_rules() -> None:
    baseline = CRYPTO_GBM.baseline_spec()
    closes = {"ETH": _synthetic_closes()}
    table = {"ETH": {"30d": 0.02430307766195731}}
    # Exactly one of closes / vol_table.
    with pytest.raises(ValueError, match="exactly one"):
        CRYPTO_GBM.fit(CryptoFamilyHistory(), baseline, _AS_OF)
    with pytest.raises(ValueError, match="exactly one"):
        CRYPTO_GBM.fit(
            CryptoFamilyHistory(closes=closes, vol_table=table), baseline, _AS_OF
        )
    # vol_table replay is window-mode only.
    with pytest.raises(ValueError, match="window-mode only"):
        CRYPTO_GBM.fit(
            CryptoFamilyHistory(vol_table=table), spec_with(vol_mode="ew"), _AS_OF
        )
    # The table must carry every selected window.
    with pytest.raises(ValueError, match="missing the selected windows"):
        CRYPTO_GBM.fit(
            CryptoFamilyHistory(vol_table={"ETH": {"7d": 0.03}}), baseline, _AS_OF
        )
    # Table sigmas are positive finite floats.
    for bad in ({"30d": 0.0}, {"30d": -1.0}, {"30d": 1}, {"30d": True}):
        with pytest.raises(ValueError):
            CRYPTO_GBM.fit(
                CryptoFamilyHistory(vol_table={"ETH": bad}), baseline, _AS_OF
            )


# ---------------------------------------------------------------------------
# Predict: delegation, direction, refusals
# ---------------------------------------------------------------------------


def test_predict_delegates_bit_identically_on_synthetic_closes() -> None:
    closes = _synthetic_closes()
    fitted = CRYPTO_GBM.fit(
        CryptoFamilyHistory(closes={"ETH": closes}), CRYPTO_GBM.baseline_spec(), _AS_OF
    )
    sigma = fitted.sigma_daily["ETH"]
    touch = CRYPTO_GBM.predict(
        fitted,
        CryptoFamilyFixture(
            asset="ETH", spot=2326.84, barrier=2000.0, horizon_hours=1595.12
        ),
    )
    direct_touch = crypto_model.touch_probability(
        spot=2326.84, sigma_daily=sigma, barrier=2000.0,
        horizon_hours=1595.12, asset="ETH",
    )
    assert touch is not None
    assert (touch.convention_a, touch.convention_b) == (
        direct_touch.convention_a, direct_touch.convention_b
    )
    for yes_when, want in (("below", 1.0), ("above", 0.0)):
        terminal = CRYPTO_GBM.predict(
            fitted,
            CryptoFamilyFixture(
                asset="ETH", spot=2326.84, barrier=2000.0,
                horizon_hours=1595.12, kind="terminal", yes_when=yes_when,
            ),
        )
        record = crypto_model.terminal_probability(
            spot=2326.84, sigma_daily=sigma, barrier=2000.0,
            horizon_hours=1595.12, asset="ETH",
        )
        direct = crypto_model.terminal_yes_probability(record, yes_when=yes_when)
        assert terminal is not None and terminal.yes_when == yes_when
        assert (terminal.convention_a, terminal.convention_b) == (
            direct.convention_a, direct.convention_b
        )
        # sanity on the orientation formula itself
        if yes_when == "below":
            base_value = record.convention_a
        else:
            base_value = 1.0 - record.convention_a
        assert terminal.convention_a == base_value


def test_predict_refusals_and_direction_rules() -> None:
    fitted = CRYPTO_GBM.fit(
        CryptoFamilyHistory(vol_table={"ETH": {"30d": 0.02430307766195731}}),
        CRYPTO_GBM.baseline_spec(),
        _AS_OF,
    )
    # Unknown asset: explicit refusal, never a fallback sigma.
    assert (
        CRYPTO_GBM.predict(
            fitted,
            CryptoFamilyFixture(
                asset="SOL", spot=100.0, barrier=80.0, horizon_hours=100.0
            ),
        )
        is None
    )
    # Touch fixtures carry no direction; terminal fixtures must state one.
    with pytest.raises(ValueError, match="no yes_when"):
        CRYPTO_GBM.predict(
            fitted,
            CryptoFamilyFixture(
                asset="ETH", spot=2326.84, barrier=2000.0,
                horizon_hours=1595.12, yes_when="below",
            ),
        )
    with pytest.raises(ValueError, match="yes_when"):
        CRYPTO_GBM.predict(
            fitted,
            CryptoFamilyFixture(
                asset="ETH", spot=2326.84, barrier=2000.0,
                horizon_hours=1595.12, kind="terminal", yes_when="side",
            ),
        )
    with pytest.raises(ValueError, match="yes_when"):
        CRYPTO_GBM.predict(
            fitted,
            CryptoFamilyFixture(
                asset="ETH", spot=2326.84, barrier=2000.0,
                horizon_hours=1595.12, kind="terminal",
            ),
        )
    with pytest.raises(ValueError, match="kind"):
        CRYPTO_GBM.predict(
            fitted,
            CryptoFamilyFixture(
                asset="ETH", spot=2326.84, barrier=2000.0,
                horizon_hours=1595.12, kind="range",
            ),
        )
    # Frozen-model validation is delegated, not duplicated.
    with pytest.raises(ValueError):
        CRYPTO_GBM.predict(
            fitted,
            CryptoFamilyFixture(
                asset="ETH", spot=-1.0, barrier=2000.0, horizon_hours=10.0
            ),
        )
    with pytest.raises(TypeError):
        CRYPTO_GBM.predict(fitted, {"asset": "ETH"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The frozen module, the provider seam and import purity
# ---------------------------------------------------------------------------


def test_crypto_model_source_is_frozen_lf_sha256() -> None:
    # LF stream: CRLF checkouts must not false-fail the pin. The value
    # was measured at main @ 3e52516f; if a rebase moves it, stop and
    # re-audit the diff, then re-register deliberately.
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src"
        / "polymarket_alpha_lab"
        / "papersim"
        / "crypto_model.py"
    )
    raw = path.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    assert hashlib.sha256(lf).hexdigest() == PINNED_CRYPTO_MODEL_LF_SHA256


def test_provider_consumes_the_family_baseline() -> None:
    # B1's registered debt (provider.py docstring): the crypto baseline
    # is single-sourced in the family; the provider delegates.
    from polymarket_alpha_lab.papersim.optimize import provider

    assert provider.CRYPTO_FROZEN_FAMILY_ID == FAMILY_ID
    assert provider.CRYPTO_FROZEN_PROCEDURE_VERSION == PROCEDURE_VERSION
    assert (
        provider.crypto_frozen_baseline().spec_sha256()
        == CRYPTO_GBM.baseline_spec().spec_sha256()
    )
    roles = provider.FrozenBaselineProvider().specs_for_lineage("crypto/touch/eth")
    assert roles.active.model_version() == pipeline.MODEL_VERSION_CRYPTO
    assert roles.baseline.spec_sha256() == CRYPTO_GBM.baseline_spec().spec_sha256()
    assert roles.candidates == () and roles.guard is None
    assert roles.fallback_reason == provider.FALLBACK_REGISTRY_EMPTY


def test_family_import_purity() -> None:
    # crypto_model is already part of the papersim package baseline;
    # pipeline is the same pure legacy-version reference soccer_frozen
    # makes. Loading the family module must add nothing else.
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            textwrap.dedent(
                """
                import json
                import sys

                import polymarket_alpha_lab.papersim as base_pkg

                baseline = set(sys.modules)

                from polymarket_alpha_lab.papersim.optimize.families import crypto

                after = set(sys.modules)

                assert crypto.CRYPTO_GBM is not None
                print(
                    json.dumps(
                        {
                            "project": sorted(
                                name
                                for name in after - baseline
                                if name.startswith("polymarket_alpha_lab")
                            ),
                            "other": sorted(
                                name
                                for name in after - baseline
                                if not name.startswith("polymarket_alpha_lab")
                            ),
                            "psycopg": "psycopg" in after,
                        }
                    )
                )
                """
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"subprocess probe failed (exit {result.returncode}):\n"
        f"{result.stderr}"
    )
    payload = json.loads(result.stdout)
    assert payload["project"] == [
        "polymarket_alpha_lab.papersim.optimize",
        "polymarket_alpha_lab.papersim.optimize.families",
        "polymarket_alpha_lab.papersim.optimize.families.base",
        "polymarket_alpha_lab.papersim.optimize.families.crypto",
        "polymarket_alpha_lab.papersim.optimize.spec",
        "polymarket_alpha_lab.papersim.pipeline",
    ]
    assert payload["other"] == []
    assert payload["psycopg"] is False
