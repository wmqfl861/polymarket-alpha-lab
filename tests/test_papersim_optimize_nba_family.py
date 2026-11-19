"""MO-5c: the NBA pace/efficiency family.

Pins the contracts of ``papersim.optimize.families.nba`` (design 04
sections 7-8, plan 05 node MO-5c, reviewed node plan P2/P3 fixes):
family shape and registry, the baseline spec identity (envelope equals
the model's frozen VARIANTS; no fabricated legacy version string),
param-space validation including the b2b clamp fail-safe, the
envelope-around-the-center rule, fit/predict as a bit-identical thin
wrapper of ``nba_model.fixture_prediction``, and import purity.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import subprocess
import sys
import textwrap

import pytest

from polymarket_alpha_lab.papersim import nba_model
from polymarket_alpha_lab.papersim.optimize import ModelSpec, validate_param
from polymarket_alpha_lab.papersim.optimize.families import iter_family_ids
from polymarket_alpha_lab.papersim.optimize.families.base import ModelFamily
from polymarket_alpha_lab.papersim.optimize.families.nba import (
    B2B_FIT,
    DATA_CONTRACT,
    ENVELOPE_HALF_LIFE_RATIOS,
    ENVELOPE_SIGMA_SCALES,
    FAMILY_ID,
    NBA_PACE_EFFICIENCY,
    PARAM_SPACE,
    PROCEDURE_VERSION,
    TARGETS,
    NbaFamilyFixture,
    NbaFamilyHistory,
    _prediction_kwargs,
    _validated_params,
)

_AS_OF = datetime.datetime(2026, 11, 20, tzinfo=datetime.timezone.utc)


def _snapshot():
    """A small frozen-contract snapshot (same shape as the model tests)."""

    def row(date_s, opponent, is_home, pts, opp_pts, b2b, rest):
        return {
            "date": date_s,
            "home": is_home,
            "opponent": opponent,
            "poss": 100.0,
            "pts": pts,
            "opp_pts": opp_pts,
            "ortg": 112.0,
            "drtg": 112.0,
            "rest_days": rest,
            "b2b": b2b,
        }

    games = [
        ("2026-01-01", "AAA", "BBB", 113, 110, False, None),
        ("2026-01-02", "BBB", "AAA", 111, 108, True, 1),
        ("2026-01-03", "AAA", "BBB", 112, 109, False, 2),
    ]
    teams: dict[str, dict] = {}
    for date_s, home, away, hp, ap, b2b, rest in games:
        teams.setdefault(home, {"display": home, "games": []})
        teams.setdefault(away, {"display": away, "games": []})
        teams[home]["games"].append(
            row(date_s, away, True, hp, ap, b2b, None if rest is None else rest + 1)
        )
        teams[away]["games"].append(
            row(date_s, home, False, ap, hp, b2b, rest)
        )
    return {
        "generated_at": "2026-11-20T06:00:00+00:00",
        "season": "2026-27",
        "teams": teams,
        "injuries": {},
    }


_SNAP = _snapshot()
_HISTORY = NbaFamilyHistory(snapshot=_SNAP)


def _spec(**overrides):
    params = {defn.name: defn.default for defn in PARAM_SPACE}
    params.update(overrides)
    half_life = float(params["half_life"])
    envelope = tuple(
        dataclasses.asdict(spec) for spec in _envelope_of(half_life)
    )
    return ModelSpec(
        family=FAMILY_ID,
        procedure_version=PROCEDURE_VERSION,
        params=params,
        envelope=envelope,
        data_contract=DATA_CONTRACT,
    )


def _envelope_of(half_life):
    return tuple(
        nba_model.NbaVariantSpec(f"hl{value:g}-sig{scale:g}", value, scale)
        for value in (ratio * half_life for ratio in ENVELOPE_HALF_LIFE_RATIOS)
        for scale in ENVELOPE_SIGMA_SCALES
    )


# ---------------------------------------------------------------------------
# Family shape and registry
# ---------------------------------------------------------------------------


def test_family_shape_and_registry() -> None:
    assert isinstance(NBA_PACE_EFFICIENCY, ModelFamily)
    assert NBA_PACE_EFFICIENCY.family_id == FAMILY_ID == "nba-pace-efficiency"
    assert NBA_PACE_EFFICIENCY.procedure_version == PROCEDURE_VERSION == "wp-nba2"
    assert NBA_PACE_EFFICIENCY.targets() == TARGETS == ("moneyline", "totals")
    names = tuple(defn.name for defn in NBA_PACE_EFFICIENCY.param_space())
    assert names == tuple(defn.name for defn in PARAM_SPACE)
    assert NBA_PACE_EFFICIENCY.param_space() == PARAM_SPACE
    assert "nba-pace-efficiency" in iter_family_ids()
    # Type normalization (P3-3): numeric knobs are floats only; the b2b
    # choice set is exactly {"fit"} plus floats.
    for defn in PARAM_SPACE:
        if defn.name == "b2b_penalty":
            assert defn.choices == (B2B_FIT, 1.0, 2.0, 3.0, 4.0)
            assert all(
                isinstance(choice, float) for choice in defn.choices[1:]
            )
        else:
            assert all(
                isinstance(choice, float) and not isinstance(choice, bool)
                for choice in defn.choices
            )


def test_param_space_grids_match_the_registered_search_space() -> None:
    # Frozen first-version grids (design 04 section 7 + the node record).
    grids = {defn.name: defn.choices for defn in PARAM_SPACE}
    assert grids["half_life"] == (10.0, 15.0, 20.0, 28.0, 35.0)
    assert grids["sigma_scale"] == (0.9, 1.0, 1.1)
    assert grids["rest_points_per_day"] == (0.0, 0.25, 0.5)
    assert grids["total_bias"] == (-2.0, -1.0, 0.0, 1.0, 2.0)
    low, high = nba_model.B2B_PENALTY_CLAMP
    for choice in grids["b2b_penalty"][1:]:
        assert low <= choice <= high  # P2-i: the grid stays inside the clamp
    for defn in PARAM_SPACE:
        validate_param(defn, defn.default)  # every default is in-choice


# ---------------------------------------------------------------------------
# Baseline spec: identity, envelope, version string
# ---------------------------------------------------------------------------


def test_baseline_envelope_equals_the_frozen_six_variants() -> None:
    spec = NBA_PACE_EFFICIENCY.baseline_spec()
    expected = tuple(dataclasses.asdict(s) for s in nba_model.VARIANTS)
    assert tuple(dict(item) for item in spec.envelope) == expected
    assert [item["name"] for item in spec.envelope] == list(
        nba_model.VARIANT_NAMES
    )
    # "Around the new center": the derived grid at the default center
    # reproduces SWEEP_HALF_LIVES exactly (0.5/1.75 are exact binary
    # multiplications).
    half_lives = {
        item["half_life"] for item in NBA_PACE_EFFICIENCY.baseline_spec().envelope
    }
    assert sorted(half_lives) == list(nba_model.SWEEP_HALF_LIVES)
    # At another center the ratios hold and the names stay conventional.
    recentered = _envelope_of(28.0)
    assert {s.half_life for s in recentered} == {14.0, 28.0, 49.0}
    assert [s.name for s in recentered][::2] == ["hl14-sig1", "hl28-sig1", "hl49-sig1"]


def test_baseline_spec_identity_and_version_string() -> None:
    first = NBA_PACE_EFFICIENCY.baseline_spec()
    second = NBA_PACE_EFFICIENCY.baseline_spec()
    assert first.canonical_json() == second.canonical_json()
    assert first == second
    assert hash(first) == hash(second)
    assert dict(first.params) == {
        "half_life": 20.0,
        "sigma_scale": 1.0,
        "b2b_penalty": B2B_FIT,
        "rest_points_per_day": 0.0,
        "total_bias": 0.0,
    }
    # The NBA shadow model has NO legacy queue version string (pipeline
    # pins only soccer/crypto): None, and the derived identity format.
    assert first.legacy_model_version is None
    assert first.model_version() == (
        f"{PROCEDURE_VERSION}+{first.spec_sha256()[:12]}"
    )
    # Any parameter change re-identifies the spec.
    for override in (
        {"half_life": 15.0},
        {"sigma_scale": 0.9},
        {"b2b_penalty": 3.0},
        {"rest_points_per_day": 0.25},
        {"total_bias": 1.0},
    ):
        assert _spec(**override).spec_sha256() != first.spec_sha256()
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.family = "tampered"
    with pytest.raises(TypeError):
        first.params["half_life"] = 35.0


# ---------------------------------------------------------------------------
# fit validation
# ---------------------------------------------------------------------------


def test_fit_rejects_invalid_specs() -> None:
    family = NBA_PACE_EFFICIENCY
    with pytest.raises(TypeError, match="NbaFamilyHistory"):
        family.fit({"teams": {}}, family.baseline_spec(), _AS_OF)
    with pytest.raises(ValueError, match="family"):
        foreign = _spec()
        family.fit(
            _HISTORY,
            ModelSpec(
                family="other",
                procedure_version=PROCEDURE_VERSION,
                params=dict(foreign.params),
                envelope=foreign.envelope,
                data_contract=DATA_CONTRACT,
            ),
            _AS_OF,
        )
    with pytest.raises(ValueError, match="procedure_version"):
        stale = _spec()
        family.fit(
            _HISTORY,
            ModelSpec(
                family=FAMILY_ID,
                procedure_version="wp-nba1",
                params=dict(stale.params),
                envelope=stale.envelope,
                data_contract=DATA_CONTRACT,
            ),
            _AS_OF,
        )
    with pytest.raises(ValueError, match="unknown params"):
        with_rogue = _spec()
        family.fit(
            _HISTORY,
            ModelSpec(
                family=FAMILY_ID,
                procedure_version=PROCEDURE_VERSION,
                params={**dict(with_rogue.params), "rogue": 1.0},
                envelope=with_rogue.envelope,
                data_contract=DATA_CONTRACT,
            ),
            _AS_OF,
        )
    with pytest.raises(ValueError, match="missing params"):
        family.fit(
            _HISTORY,
            ModelSpec(
                family=FAMILY_ID,
                procedure_version=PROCEDURE_VERSION,
                params={"half_life": 20.0},
                envelope=_spec().envelope,
                data_contract=DATA_CONTRACT,
            ),
            _AS_OF,
        )
    for bad in (
        {"half_life": 12.0},
        {"sigma_scale": 1.2},
        {"b2b_penalty": 0.5},
        {"rest_points_per_day": 0.1},
        {"total_bias": 0.5},
    ):
        with pytest.raises(ValueError):
            family.fit(_HISTORY, _spec(**bad), _AS_OF)
    # A tampered envelope (right params, wrong grid) is refused: the
    # spec identity must not drift from the behavior it names.
    with pytest.raises(ValueError, match="envelope"):
        honest = _spec()
        tampered_envelope = tuple(
            dataclasses.asdict(spec) for spec in _envelope_of(35.0)
        )
        family.fit(
            _HISTORY,
            ModelSpec(
                family=FAMILY_ID,
                procedure_version=PROCEDURE_VERSION,
                params=dict(honest.params),
                envelope=tampered_envelope,
                data_contract=DATA_CONTRACT,
            ),
            _AS_OF,
        )


def test_b2b_clamp_failsafe_beyond_choice_membership() -> None:
    # P2-i: the clamp re-check is a fail-safe that must hold even if
    # the choice grid is ever widened past the model's clamp. Exercised
    # through the module-level validator directly (a widened grid is
    # unreachable through today's PARAM_SPACE by construction).
    low, high = nba_model.B2B_PENALTY_CLAMP
    inside = _spec(b2b_penalty=2.0)
    assert _validated_params(inside)["b2b_penalty"] == 2.0
    forged = _spec()
    forged_params = dict(forged.params)
    forged_params["b2b_penalty"] = high + 1.0
    with pytest.raises(ValueError, match="B2B_PENALTY_CLAMP"):
        _validated_params(
            ModelSpec(
                family=FAMILY_ID,
                procedure_version=PROCEDURE_VERSION,
                params=forged_params,
                envelope=forged.envelope,
                data_contract=DATA_CONTRACT,
            )
        )
    assert low == 0.0  # the guard's bound source is the model constant


def test_rest_mapping_never_enables_with_zero() -> None:
    # P3-5: 0.0 -> disabled; > 0 -> enabled AND the value passed.
    off = _prediction_kwargs(_validated_params(_spec()))
    assert off["use_rest_adjustment"] is False
    assert off["rest_points_per_day"] == 0.0
    on = _prediction_kwargs(
        _validated_params(_spec(rest_points_per_day=0.5))
    )
    assert on["use_rest_adjustment"] is True
    assert on["rest_points_per_day"] == 0.5
    assert not any(
        kwargs["use_rest_adjustment"] and kwargs["rest_points_per_day"] == 0.0
        for kwargs in (
            _prediction_kwargs(
                _validated_params(_spec(rest_points_per_day=value))
            )
            for value in (0.0, 0.25, 0.5)
        )
    )


# ---------------------------------------------------------------------------
# fit/predict: bit-identical thin wrapper
# ---------------------------------------------------------------------------


def test_baseline_predict_matches_direct_default_call() -> None:
    family = NBA_PACE_EFFICIENCY
    fitted = family.fit(_HISTORY, family.baseline_spec(), _AS_OF)
    for family_fixture in (
        NbaFamilyFixture(home="AAA", away="BBB"),
        NbaFamilyFixture(
            home="AAA",
            away="BBB",
            rest_home=1,
            rest_away=4,
            b2b_home=True,
        ),
        NbaFamilyFixture(home="AAA", away="BBB", before_date="2026-01-03"),
    ):
        model_fixture = nba_model.NbaFixture(
            home=family_fixture.home,
            away=family_fixture.away,
            rest_home=family_fixture.rest_home,
            rest_away=family_fixture.rest_away,
            b2b_home=family_fixture.b2b_home,
            b2b_away=family_fixture.b2b_away,
        )
        via_family = family.predict(fitted, family_fixture)
        direct = nba_model.fixture_prediction(
            _SNAP, model_fixture, before_date=family_fixture.before_date
        )
        assert via_family == direct
        assert repr(via_family) == repr(direct)
        assert (via_family is None) == (direct is None)
    # The soft refusal is passed through as None.
    refused = family.predict(fitted, NbaFamilyFixture(home="AAA", away="CCC"))
    assert refused is None
    with pytest.raises(TypeError, match="NbaFamilyFitted"):
        family.predict("not-fitted", NbaFamilyFixture(home="AAA", away="BBB"))
    with pytest.raises(TypeError, match="NbaFamilyFixture"):
        family.predict(fitted, ("AAA", "BBB"))


def test_tuned_spec_predict_matches_equivalent_kwargs() -> None:
    family = NBA_PACE_EFFICIENCY
    spec = _spec(
        half_life=15.0,
        sigma_scale=1.1,
        b2b_penalty=3.0,
        rest_points_per_day=0.25,
        total_bias=2.0,
    )
    fitted = family.fit(_HISTORY, spec, _AS_OF)
    family_fixture = NbaFamilyFixture(
        home="AAA", away="BBB", rest_home=2, rest_away=0, b2b_home=True
    )
    via_family = family.predict(fitted, family_fixture)
    direct = nba_model.fixture_prediction(
        _SNAP,
        nba_model.NbaFixture(
            home="AAA",
            away="BBB",
            rest_home=2,
            rest_away=0,
            b2b_home=True,
            b2b_away=False,
        ),
        variants=_envelope_of(15.0),
        sigma_scale=1.1,
        total_bias=2.0,
        use_rest_adjustment=True,
        rest_points_per_day=0.25,
        b2b_penalty_override=3.0,
    )
    assert via_family is not None and direct is not None
    assert via_family == direct
    assert repr(via_family) == repr(direct)
    # The tuning actually moved the prediction away from the baseline.
    baseline = family.predict(
        family.fit(_HISTORY, family.baseline_spec(), _AS_OF), family_fixture
    )
    assert baseline is not None
    assert via_family.exp_total != baseline.exp_total
    assert via_family.exp_margin != baseline.exp_margin


# ---------------------------------------------------------------------------
# Import purity
# ---------------------------------------------------------------------------


def test_family_import_purity() -> None:
    # nba_model is already part of the papersim package baseline
    # (papersim/__init__.py imports it), so loading the family module
    # must add nothing beyond the optimize subpackage itself (the
    # families package pulls in base/spec lazily-via-eager-import).
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

                from polymarket_alpha_lab.papersim.optimize.families import nba

                after = set(sys.modules)

                assert nba.NBA_PACE_EFFICIENCY is not None
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
        "polymarket_alpha_lab.papersim.optimize.families.nba",
        "polymarket_alpha_lab.papersim.optimize.spec",
    ]
    assert payload["other"] == []
    assert payload["psycopg"] is False
