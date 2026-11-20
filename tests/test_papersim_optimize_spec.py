"""MO-1: model-spec identity and the model-family interface.

Pins the contracts of ``papersim.optimize.spec`` and
``papersim.optimize.families`` (design 04 section 8, plan 05 node
MO-1): canonical-text invariance and sensitivity, the pinned
``policy_code_sha256()`` anchor, the frozen soccer family passthrough
against the frozen r23 fixture, and import purity (the subpackage adds
no database driver or network module and stays out of CLI startup).
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from polymarket_alpha_lab.papersim import (
    fees,
    gates,
    pipeline,
    risk_policy,
    soccer_model,
    xg,
)
from polymarket_alpha_lab.papersim.optimize import (
    ModelSpec,
    ParamDef,
    canonical_value,
    decimal_text,
    validate_param,
)
from polymarket_alpha_lab.papersim.optimize.families import iter_family_ids
from polymarket_alpha_lab.papersim.optimize.families.base import ModelFamily
from polymarket_alpha_lab.papersim.optimize.families.soccer_frozen import (
    DATA_CONTRACT,
    FAMILY_ID,
    PROCEDURE_VERSION,
    SOCCER_FROZEN,
    TARGETS,
    SoccerFrozenFixture,
    SoccerFrozenHistory,
)

FIXTURES = Path(__file__).parent / "fixtures" / "papersim"

_AS_OF = datetime.datetime(2026, 11, 19, tzinfo=datetime.timezone.utc)

#: Pinned policy fingerprint. Measured on the server from the PRE-MERGE
#: baseline main @ e2510315 (2026-11-20): check out e2510315 and call
#: ``policy_code_sha256()`` to re-audit the anchor. The paired
#: structural test below explains where the value comes from.
PINNED_POLICY_CODE_SHA256 = (
    "a18e51eae3a4a0d6f6801663c91e1dd93fa35767db6574a22508ca0770bf940e"
)


def _run_python(code: str) -> str:
    result = subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"subprocess probe failed (exit {result.returncode}):\n{result.stderr}"
    )
    return result.stdout


# ---------------------------------------------------------------------------
# Canonical text: invariance and sensitivity
# ---------------------------------------------------------------------------


def test_canonical_text_is_key_order_independent() -> None:
    # Insertion order of mapping keys must not matter (plan MO-1).
    a = canonical_value({"b": 2, "a": 1})
    b = canonical_value({"a": 1, "b": 2})
    assert a == b == '{s:"a":i:1,s:"b":i:2}'
    left = ModelSpec(
        family="f",
        procedure_version="v1",
        params={"half_life": 90.0, "prior": 8},
        envelope=({"rho": 0.0, "w": 0.5},),
        data_contract="dc",
    )
    right = ModelSpec(
        family="f",
        procedure_version="v1",
        params={"prior": 8, "half_life": 90.0},
        envelope=({"w": 0.5, "rho": 0.0},),
        data_contract="dc",
    )
    assert left.canonical_json() == right.canonical_json()
    assert left.spec_sha256() == right.spec_sha256()
    assert left == right


def test_canonical_text_is_float_spelling_independent() -> None:
    # The same float written differently spells the same identity; the
    # decimal expansion kills scientific notation (1e-9 vs 0.000000001).
    assert canonical_value(0.5) == canonical_value(5e-1) == canonical_value(0.50)
    assert decimal_text(0.5) == "0.5"
    assert canonical_value(1e-9) == canonical_value(0.000000001)
    assert decimal_text(1e-9) == "0.000000001"
    assert decimal_text(1e20) == "100000000000000000000"
    left = ModelSpec(
        family="f",
        procedure_version="v1",
        params={"half_life": 9e1},
        envelope=(),
        data_contract="dc",
    )
    right = ModelSpec(
        family="f",
        procedure_version="v1",
        params={"half_life": 90.0},
        envelope=(),
        data_contract="dc",
    )
    assert left.spec_sha256() == right.spec_sha256()


def test_canonical_text_distinguishes_types() -> None:
    # int, float and str encodings never collide; None is null.
    int8 = canonical_value(8)
    float8 = canonical_value(8.0)
    str8 = canonical_value("8")
    assert len({int8, float8, str8}) == 3
    assert int8 == "i:8"
    assert float8 == "f:8.0"
    assert str8 == 's:"8"'
    assert canonical_value(None) == "null"
    assert canonical_value("null") != "null"
    assert canonical_value(True) == "true"
    assert canonical_value(False) == "false"
    # Nested: arrays keep order, mappings sort keys.
    assert canonical_value([8, 8.0]) == "[i:8,f:8.0]"
    assert canonical_value((8, 8.0)) == "[i:8,f:8.0]"
    assert canonical_value({"k": [1, {"z": 1, "a": 2}]}) == '{s:"k":[i:1,{s:"a":i:2,s:"z":i:1}]}'


def test_canonical_rejects_nonfinite_floats_and_unknown_types() -> None:
    # Identity is undefined for non-finite floats and opaque objects.
    with pytest.raises(ValueError):
        decimal_text(float("inf"))
    with pytest.raises(ValueError):
        decimal_text(float("nan"))
    with pytest.raises(ValueError):
        canonical_value(float("nan"))
    with pytest.raises(TypeError):
        canonical_value(object())
    with pytest.raises(TypeError):
        canonical_value({1: "int key"})


def test_spec_sha_changes_with_any_field() -> None:
    # Sensitivity: only the FIVE identity-bearing fields are traversed
    # (family / procedure_version / params / envelope / data_contract).
    # legacy_model_version is INTENTIONALLY excluded from the digest --
    # it is a historical alias, not spec semantics (see spec.py and the
    # legacy tests). Do not "fix" this asymmetry.
    base = ModelSpec(
        family="club-soccer-recalib",
        procedure_version="wp-p4",
        params={"half_life": 90.0, "prior_games": 8},
        envelope=({"half_life": 45.0}, {"half_life": 180.0}),
        data_contract="understat-xg-snapshot-v1",
    )
    variants = [
        dataclasses.replace(base, family="club-soccer-recalib-2"),
        dataclasses.replace(base, procedure_version="wp-p5"),
        dataclasses.replace(base, params={"half_life": 90.0, "prior_games": 12}),
        dataclasses.replace(base, params={"half_life": 120.0, "prior_games": 8}),
        dataclasses.replace(base, envelope=({"half_life": 46.0}, {"half_life": 180.0})),
        dataclasses.replace(base, envelope=({"half_life": 45.0},)),
        dataclasses.replace(base, data_contract="understat-xg-snapshot-v2"),
    ]
    shas = {base.spec_sha256()}
    for spec in variants:
        assert spec.spec_sha256() not in shas
        shas.add(spec.spec_sha256())


# ---------------------------------------------------------------------------
# Model-version strings
# ---------------------------------------------------------------------------


def test_model_version_legacy_passthrough() -> None:
    # Frozen baselines keep today's version strings verbatim so the
    # queue identity of existing paper data is unchanged (pipeline.py
    # 117-118 -> EntryDecision.model_version -> store candidate rows).
    baseline = SOCCER_FROZEN.baseline_spec()
    assert baseline.model_version() == pipeline.MODEL_VERSION_SOCCER
    assert baseline.model_version() == "soccer-r23"  # literal pin
    crypto_alias = ModelSpec(
        family="crypto-gbm",
        procedure_version="r19",
        params={"window": 30},
        envelope=({"window": 30},),
        data_contract="klines-v1",
        legacy_model_version=pipeline.MODEL_VERSION_CRYPTO,
    )
    assert crypto_alias.model_version() == pipeline.MODEL_VERSION_CRYPTO
    assert crypto_alias.model_version() == "crypto-r19+fx13-2026-11-12"  # literal pin
    # The alias is NOT part of the spec identity (deliberate), so
    # dropping it keeps the digest while changing the version string.
    no_alias = dataclasses.replace(crypto_alias, legacy_model_version=None)
    assert no_alias.spec_sha256() == crypto_alias.spec_sha256()
    assert no_alias.model_version() != crypto_alias.model_version()


def test_model_version_derived_for_non_legacy() -> None:
    # Every non-baseline spec gets a fresh content-derived identity:
    # procedure_version + first 12 hex of the spec digest.
    spec = ModelSpec(
        family="club-soccer-recalib",
        procedure_version="wp-p4",
        params={"half_life": 90.0},
        envelope=({"half_life": 45.0},),
        data_contract="understat-xg-snapshot-v1",
    )
    assert spec.model_version() == f"wp-p4+{spec.spec_sha256()[:12]}"
    changed = dataclasses.replace(spec, params={"half_life": 91.0})
    assert changed.model_version() != spec.model_version()
    assert changed.model_version().startswith("wp-p4+")


# ---------------------------------------------------------------------------
# policy_code_sha256: pinned anchor + structural recompute
# ---------------------------------------------------------------------------


def test_policy_code_sha256_reproducible_from_public_constants() -> None:
    # Structural proof of where the pinned value comes from: rebuild
    # the payload from the nine public frozen constants exactly as
    # pipeline.py:262-274 does and digest it independently. If any of
    # those constants changes, this and the pinned anchor below both
    # fail with a diff that names the moved surface.
    payload = {
        "pipeline_version": pipeline.PIPELINE_VERSION,
        "gate_version": pipeline.GATE_VERSION,
        "model_versions": [
            pipeline.MODEL_VERSION_CRYPTO,
            pipeline.MODEL_VERSION_SOCCER,
        ],
        "step_order": list(pipeline.STEP_ORDER),
        "gates": dict(gates.GATES),
        "cross_league_worst_edge_min": gates.CROSS_LEAGUE_MW_WORST_EDGE_MIN,
        "acceptance_notional_usd": gates.DEFAULT_NOTIONAL_USD,
        "default_team_cap_usd": risk_policy.DEFAULT_TEAM_CAP_USD,
        "fee_schedule_as_of": fees.FEE_SCHEDULE_AS_OF,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert digest == pipeline.policy_code_sha256()


def test_policy_code_sha256_pinned() -> None:
    # The value is frozen policy surface, not a magic number: the
    # anchor was measured on the server from the pre-merge baseline
    # main @ e2510315 (2026-11-20); the structural test above explains
    # its provenance.
    assert pipeline.policy_code_sha256() == PINNED_POLICY_CODE_SHA256


# ---------------------------------------------------------------------------
# ParamDef
# ---------------------------------------------------------------------------


def test_paramdef_full_surface_and_validation() -> None:
    # All fields of design 04 (section 8), including season_frozen,
    # so MO-5 family adapters never need an interface rework.
    half_life = ParamDef("half_life", "float", 180.0, lower=30.0, upper=400.0)
    prior_games = ParamDef("prior_games", "int", 8, lower=1, upper=32)
    drift = ParamDef(
        "drift", "choice", "median-flat", choices=("zero-log", "median-flat")
    )
    kappa = ParamDef("kappa_scale", "float", 1.0, season_frozen=True)
    assert half_life.season_frozen is False
    assert kappa.season_frozen is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        half_life.name = "renamed"

    validate_param(half_life, 90.0)
    validate_param(half_life, 90)  # an int is an acceptable float value
    with pytest.raises(ValueError):
        validate_param(half_life, 10.0)  # below lower
    with pytest.raises(ValueError):
        validate_param(half_life, 500.0)  # above upper
    with pytest.raises(ValueError):
        validate_param(half_life, True)  # a bool is not a number here

    validate_param(prior_games, 8)
    with pytest.raises(ValueError):
        validate_param(prior_games, 8.0)  # a float is not an int
    with pytest.raises(ValueError):
        validate_param(prior_games, True)

    validate_param(drift, "zero-log")
    with pytest.raises(ValueError):
        validate_param(drift, "mean-flat")

    with pytest.raises(ValueError):
        ParamDef("bad", "string", "x")  # unknown kind
    with pytest.raises(ValueError):
        ParamDef("empty", "choice", "x")  # choice kind needs choices


# ---------------------------------------------------------------------------
# The frozen soccer family
# ---------------------------------------------------------------------------


def test_soccer_frozen_family_shape() -> None:
    # The frozen production model is a family with NOTHING to tune:
    # empty param space, ten-variant envelope, baseline targets.
    assert isinstance(SOCCER_FROZEN, ModelFamily)
    assert SOCCER_FROZEN.family_id == FAMILY_ID == "club-soccer-frozen"
    assert SOCCER_FROZEN.procedure_version == PROCEDURE_VERSION == "r23"
    assert SOCCER_FROZEN.param_space() == ()
    spec = SOCCER_FROZEN.baseline_spec()
    assert dict(spec.params) == {}
    assert len(spec.envelope) == 10
    names = tuple(str(item["name"]) for item in spec.envelope)
    assert names == soccer_model.SWEEP_NAMES
    assert spec.data_contract == DATA_CONTRACT
    assert SOCCER_FROZEN.targets() == TARGETS == ("over_under", "btts", "team_total")
    assert "match_winner" not in SOCCER_FROZEN.targets()  # suspended in r23
    # MO-5 lanes each extend this pin; the last lane to merge takes the
    # union here (5e landed the seven-family union).
    assert iter_family_ids() == (
        "club-soccer-frozen",
        "club-soccer-recalib",
        "crypto-gbm",
        "intl-elo-goal-residuals",
        "intl-elo-poisson",
        "league-bridge",
        "nba-pace-efficiency",
    )


def test_soccer_frozen_baseline_spec_is_immutable_and_stable() -> None:
    # Two constructions are identical, and the deep freeze actually
    # blocks mutation at every mapped depth (a single MappingProxyType
    # wrap would be a shallow guard).
    first = SOCCER_FROZEN.baseline_spec()
    second = SOCCER_FROZEN.baseline_spec()
    assert first.canonical_json() == second.canonical_json()
    assert first == second
    assert hash(first) == hash(second)
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.family = "tampered"
    with pytest.raises(TypeError):
        first.params["half_life"] = 0.5
    with pytest.raises(TypeError):
        first.envelope[0]["rho"] = 0.0
    # Mutating the CALLER's input after construction changes nothing:
    # __post_init__ froze its own copies.
    params = {"half_life": 90.0}
    spec = ModelSpec(
        family="f",
        procedure_version="v1",
        params=params,
        envelope=(),
        data_contract="dc",
    )
    params["half_life"] = 120.0
    assert dict(spec.params) == {"half_life": 90.0}


def test_soccer_frozen_fit_rejects_any_non_baseline_spec() -> None:
    # Frozen means exactly one fittable spec: the baseline. A tampered
    # parameter set, a foreign family or a wrong history type is
    # refused with its own error.
    history = SoccerFrozenHistory(xg_snapshot={})
    baseline = SOCCER_FROZEN.baseline_spec()
    tampered = ModelSpec(
        family=FAMILY_ID,
        procedure_version=PROCEDURE_VERSION,
        params={"half_life": 90.0},
        envelope=baseline.envelope,
        data_contract=DATA_CONTRACT,
        legacy_model_version=pipeline.MODEL_VERSION_SOCCER,
    )
    with pytest.raises(ValueError, match="frozen"):
        SOCCER_FROZEN.fit(history, tampered, _AS_OF)
    foreign = dataclasses.replace(baseline, family="club-soccer-recalib")
    with pytest.raises(ValueError, match="frozen"):
        SOCCER_FROZEN.fit(history, foreign, _AS_OF)
    with pytest.raises(TypeError):
        SOCCER_FROZEN.fit({"xg_snapshot": {}}, baseline, _AS_OF)


# EPL 2025 prior-season rates and the reconstruction below mirror the
# selfcheck embedding in papersim/cli.py:101-121, 226-254 (frozen r23
# fixture; if a value ever changes it is a fixture change, not a
# tune-up). Hull and Ipswich are deliberately absent from the table
# (promoted sides), exercising the league-average fallback.
EPL_2025_HOME_RATE = 1.6946258894736843
EPL_2025_AWAY_RATE = 1.3643311084210525
EPL_2025_TEAM_RATES: dict[str, tuple[float, float]] = {
    "Bournemouth": (1.758804, 1.4939418157894737),
    "Brentford": (1.7435966842105264, 1.5555292368421054),
    "Chelsea": (1.9003278947368418, 1.5294069210526315),
    "Leeds": (1.5729118421052632, 1.4822607894736841),
    "Manchester United": (1.8996939473684211, 1.3231953684210527),
    "Nottingham Forest": (1.287795947368421, 1.7036741052631579),
}


def _r23_snapshot(fixture: dict) -> dict:
    excerpt = fixture["xg_snapshot_excerpt"]
    prior_teams = {
        team_id: {
            "id": team_id,
            "title": row["title"],
            "history": [
                {
                    "xG": EPL_2025_TEAM_RATES[row["title"]][0],
                    "xGA": EPL_2025_TEAM_RATES[row["title"]][1],
                }
            ],
        }
        for team_id, row in excerpt["teams"].items()
        if row["title"] in EPL_2025_TEAM_RATES
    }
    return {
        "EPL_2025": {
            "dates": [
                {
                    "isResult": True,
                    "xG": {"h": EPL_2025_HOME_RATE, "a": EPL_2025_AWAY_RATE},
                }
            ],
            "teams": prior_teams,
        },
        "EPL_2026": {"dates": [], "teams": excerpt["teams"]},
    }


@pytest.mark.parametrize(
    "case_id, home, away, line",
    [
        ("BRE-FOR_O2.5", "Brentford", "Nottingham Forest", 2.5),
        ("HUL-IPS_O2.5", "Hull", "Ipswich", 2.5),
        ("BOU-LEE_O2.5", "Bournemouth", "Leeds", 2.5),
    ],
)
def test_soccer_frozen_predict_matches_r23_frozen_fixture(
    case_id: str, home: str, away: str, line: float
) -> None:
    # The family wrapper is a pure passthrough: fit + predict reproduce
    # the frozen r23 fixture bytes (same anchor as the selfcheck
    # soccer-r23 checks, tolerance 1e-4 / lambdas 5e-4).
    fixture = json.loads(
        (FIXTURES / "regression_r23_soccer.json").read_text(encoding="utf-8")
    )
    case = fixture["cases"][case_id]
    snapshot = xg.load_xg_payload(_r23_snapshot(fixture))
    fitted = SOCCER_FROZEN.fit(
        SoccerFrozenHistory(xg_snapshot=snapshot),
        SOCCER_FROZEN.baseline_spec(),
        _AS_OF,
    )
    variants = SOCCER_FROZEN.predict(
        fitted, SoccerFrozenFixture(home, "EPL", away, "EPL")
    )
    assert variants is not None
    assert len(variants) == 10
    quotes = [soccer_model.over_probability(v.outputs, line) for v in variants]
    for got, want in zip(quotes, case["qs_variants"]):
        assert abs(got - float(want)) <= 1e-4
    assert abs(soccer_model.median(quotes) - float(case["model_median"])) <= 1e-4
    assert abs(min(quotes) - float(case["model_range"][0])) <= 1e-4
    assert abs(max(quotes) - float(case["model_range"][1])) <= 1e-4
    lambda_home = soccer_model.median([v.lambdas[0] for v in variants])
    lambda_away = soccer_model.median([v.lambdas[1] for v in variants])
    assert abs(lambda_home - float(case["lambda_home"])) <= 5e-4
    assert abs(lambda_away - float(case["lambda_away"])) <= 5e-4


def test_soccer_frozen_predict_returns_none_for_refused_fixture() -> None:
    # Cross-league fixture without a bridge: None is the explicit
    # refusal passthrough of fixture_variants (same banned path as the
    # selfcheck no-bridge check).
    snapshot = xg.load_xg_payload(
        {
            "bundesliga_2025": {
                "dates": [{"isResult": True, "xG": {"h": 1.7, "a": 1.4}}],
                "teams": {
                    "3": {
                        "title": "TSG 1899 Hoffenheim",
                        "history": [{"xG": 1.5, "xGA": 1.5}],
                    }
                },
            },
            "bundesliga_2026": {
                "dates": [],
                "teams": {
                    "3": {
                        "title": "TSG 1899 Hoffenheim",
                        "history": [{"xG": 1.5, "xGA": 1.5}],
                    }
                },
            },
            "ligue_1_2025": {
                "dates": [{"isResult": True, "xG": {"h": 1.6, "a": 1.3}}],
                "teams": {
                    "4": {
                        "title": "Olympique Lyonnais",
                        "history": [{"xG": 1.4, "xGA": 1.4}],
                    }
                },
            },
            "ligue_1_2026": {
                "dates": [],
                "teams": {
                    "4": {
                        "title": "Olympique Lyonnais",
                        "history": [{"xG": 1.4, "xGA": 1.4}],
                    }
                },
            },
        }
    )
    fitted = SOCCER_FROZEN.fit(
        SoccerFrozenHistory(xg_snapshot=snapshot),
        SOCCER_FROZEN.baseline_spec(),
        _AS_OF,
    )
    refused = SOCCER_FROZEN.predict(
        fitted,
        SoccerFrozenFixture(
            "TSG 1899 Hoffenheim", "bundesliga", "Olympique Lyonnais", "ligue_1"
        ),
    )
    assert refused is None


# ---------------------------------------------------------------------------
# Import purity and CLI startup
# ---------------------------------------------------------------------------


def test_optimize_subpackage_import_purity() -> None:
    # Two-step snapshot in a fresh process. The papersim package
    # baseline already imports urllib via fotmob (fotmob.py:73-75), so
    # the purity contract is "zero NEW modules beyond the subpackage
    # itself (plus the pure pipeline module referenced for the legacy
    # version constant) and no database driver at any point".
    payload = json.loads(
        _run_python(
            """
            import json
            import sys

            import polymarket_alpha_lab.papersim as base_pkg  # baseline

            baseline = set(sys.modules)

            import polymarket_alpha_lab.papersim.optimize as optimize_pkg

            after_optimize = set(sys.modules)

            import polymarket_alpha_lab.papersim.optimize.families.soccer_frozen

            after_family = set(sys.modules)

            def project(names):
                return sorted(
                    name for name in names if name.startswith("polymarket_alpha_lab")
                )

            def other(names):
                return sorted(
                    name for name in names if not name.startswith("polymarket_alpha_lab")
                )

            assert optimize_pkg.ModelSpec is not None
            print(
                json.dumps(
                    {
                        "step1_project": project(after_optimize - baseline),
                        "step2_project": project(after_family - after_optimize),
                        "step1_other": other(after_optimize - baseline),
                        "step2_other": other(after_family - after_optimize),
                        "psycopg_loaded": "psycopg" in after_family,
                    }
                )
            )
            """
        )
    )
    assert payload["step1_project"] == [
        "polymarket_alpha_lab.papersim.optimize",
        "polymarket_alpha_lab.papersim.optimize.spec",
    ]
    assert payload["step2_project"] == [
        "polymarket_alpha_lab.papersim.optimize.families",
        "polymarket_alpha_lab.papersim.optimize.families.base",
        "polymarket_alpha_lab.papersim.optimize.families.soccer_frozen",
        "polymarket_alpha_lab.papersim.pipeline",
    ]
    assert payload["step1_other"] == []
    assert payload["step2_other"] == []
    assert payload["psycopg_loaded"] is False


def test_cli_startup_does_not_import_optimize() -> None:
    # The papersim CLI startup path never imports the optimize
    # subpackage (command wiring is MO-6, behind on-demand imports),
    # and the startup module budget holds (limit 20, baseline 19).
    output = _run_python(
        """
        import sys

        import polymarket_alpha_lab.papersim.cli  # noqa: F401

        names = [
            name
            for name in sys.modules
            if name == "polymarket_alpha_lab"
            or name.startswith("polymarket_alpha_lab.")
        ]
        print(len(names))
        print("polymarket_alpha_lab.papersim.optimize" in sys.modules)
        """
    ).splitlines()
    count = int(output[0])
    assert count <= 20, f"papersim.cli startup loads {count} project modules (limit 20)"
    assert output[1] == "False", "optimize subpackage leaked into CLI startup"
