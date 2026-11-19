"""MO-5a tests: the recalib family wraps recalib verbatim.

Acceptance anchors (plan 05 node MO-5 row 5a; review F1-F10):
- default-parameter path is BIT-IDENTICAL to a direct ``recalib.fit``;
- search space v1 equals the WP-P4 3x3 grid in declared order;
- envelope is center plus the eight predeclared perturbations;
- model versions carry no legacy alias (``p4r1+<sha12>``);
- forced low-score rho rebuilds BOTH parameter layers and keeps Config;
- the registry lists this family next to the frozen baseline (F1: the
  cross-lane merge of registration lines is owned by the coordinator).

Synthetic data only: these tests never touch the WP-P4 server archive;
the 65-fold reproduction against the published evidence JSON runs on
the server via ``tools/mo5a_repro_wp_p4.py`` (review F2/F3).
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
import textwrap
from types import MappingProxyType

import pytest

from polymarket_alpha_lab.papersim import recalib
from polymarket_alpha_lab.papersim.optimize.families import (
    iter_family_ids,
    soccer_recalib as sr,
)
from polymarket_alpha_lab.papersim.optimize.families.base import ModelFamily
from polymarket_alpha_lab.papersim.optimize.families.soccer_recalib import (
    DATA_CONTRACT,
    DEFAULT_PARAMS,
    FAMILY_ID,
    LOW_SCORE_RHO_CHOICES,
    PROCEDURE_VERSION,
    RECALIB_SOURCE_SHA256,
    SEARCH_SPACES,
    SEARCH_SPACE_V1,
    SEARCH_SPACE_V2,
    SOCCER_RECALIB,
    SPARSE_TEAM_PRIOR_CHOICES,
    TARGETS,
    combination_count,
    envelope_params,
    envelope_specs,
    space_summary,
    space_version,
    spec_for_params,
)
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec
from polymarket_alpha_lab.papersim.recalib import Config, Fixture, History, Match, fit
from tools import wp_p4_history as history_tool
from tools import wp_p4_walkforward as wf
from tests.test_wp_p4_history import mini_audit


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
CUTOFF = START + timedelta(days=120)

FAMILY = SOCCER_RECALIB
BASELINE = FAMILY.baseline_spec()

# Regression anchors, computed once from the module as merged (any edit
# to the baseline spec surface moves these digests and must be reviewed).
PINNED_BASELINE_SHA256 = "6dc3f30fed0d063ad75849b993edb1ac9e596303f71ec37c7772b91680240cd8"
PINNED_BASELINE_MODEL_VERSION = "p4r1+6dc3f30fed0d"


def match(n: int, *, league: str = "EPL", season: str = "2026", **changes: object) -> Match:
    row = Match(f"source:{league}:{season}:{n}", league, season, n + 1,
                START + timedelta(days=n), "Alpha", "Beta", 1.0, 1.0, 1, 1)
    return replace(row, **changes)


def history(rows: tuple[Match, ...], *, as_of: datetime = CUTOFF, **changes: object) -> History:
    value = History(as_of, rows, frozenset((r.league, r.season, r.matchweek) for r in rows),
                    {r.league: "2026" for r in rows})
    return replace(value, **changes)


def fixture(*, league: str = "EPL", **changes: object) -> Fixture:
    value = Fixture(league, "2026", 200, CUTOFF, "Alpha", "Beta")
    return replace(value, **changes)


def mixed_rows() -> tuple[Match, ...]:
    return tuple(match(n, home_xg=1.0 + n % 3, away_xg=0.5 + n % 2,
                       home_goals=n % 4, away_goals=n % 3) for n in range(40))


def two_league_rows() -> tuple[Match, ...]:
    # "Large" fits a league-level rho (>=80 weighted matches, >=20 low
    # scores); "Small" falls back to the global rho (10 matches).
    large = tuple(match(n, league="Large", home_goals=n % 2, away_goals=n % 2)
                  for n in range(90))
    small = tuple(match(n, league="Small", home_goals=1, away_goals=0)
                  for n in range(10))
    return large + small


# ---------------------------------------------------------------- shape


def test_family_shape_and_defaults() -> None:
    assert isinstance(FAMILY, ModelFamily)
    assert FAMILY.family_id == FAMILY_ID == "club-soccer-recalib"
    assert FAMILY.procedure_version == PROCEDURE_VERSION == "p4r1"
    # F10: the procedure identity is pinned to the WP-P4 recalib bytes.
    assert RECALIB_SOURCE_SHA256 == (
        "f6badedb3cf9d72628af7a186c0e9183d69bca12b204a1df946e650a0cd282a9"
    )
    assert FAMILY.targets() == TARGETS == ("match_winner", "over_under")
    assert dict(BASELINE.params) == dict(DEFAULT_PARAMS)
    assert dict(DEFAULT_PARAMS) == {
        "half_life_days": 120.0, "team_prior_matches": 8.0,
        "sparse_team_prior": None, "league_prior_matches": 20.0,
        "low_score_rho": "fit", "completion_lag_hours": 3.0,
    }
    # The baseline equals recalib's Config() defaults and therefore the
    # WP-P4 fallback configuration (120/8) and the fixed shadow config.
    from dataclasses import asdict

    assert asdict(Config()) == {
        **asdict(Config(half_life_days=120.0, team_prior_matches=8.0,
                        league_prior_matches=20.0, completion_lag_hours=3.0)),
        "sparse_prior_matches": None,
    }
    assert BASELINE.data_contract == DATA_CONTRACT == "wp-p4-match-history-v1"
    assert len(BASELINE.envelope) == 9


def test_family_registry_lists_both_club_soccer_families() -> None:
    # F1: this branch registers its own key; the three-way merge across
    # the parallel MO-5 lanes is owned by the coordinator.
    # MO-5c landing (cherry-pick) extended this pin to the union of the
    # merged lanes; the MO-5b landing extended it to five; 5d/5e extend
    # it further on their own landings.
    assert iter_family_ids() == (
        "club-soccer-frozen",
        "club-soccer-recalib",
        "intl-elo-goal-residuals",
        "intl-elo-poisson",
        "nba-pace-efficiency",
    )
    assert sr.FAMILY_ID == "club-soccer-recalib"


# ------------------------------------------------------------------ T1


def test_default_path_is_bit_identical_to_direct_recalib() -> None:
    rows = mixed_rows()
    model_history = history(rows)
    fitted = FAMILY.fit(model_history, BASELINE, CUTOFF)
    direct = fit(model_history)
    assert fitted.estimator == direct
    assert fitted.forced_rho is None
    assert fitted.spec_sha256 == BASELINE.spec_sha256()
    ours = FAMILY.predict(fitted, fixture())
    theirs = direct.predict(fixture())
    assert ours == theirs
    assert ours is not None and ours.parameters.config == direct.config


def test_default_path_bit_identical_on_mini_audit_fold(mini_audit) -> None:
    folds, _ = history_tool.build_folds(mini_audit)
    fold = folds[0]
    model_history = history_tool.to_model_history(fold)
    fitted = FAMILY.fit(model_history, BASELINE, fold.cutoff)
    direct = fit(model_history)
    assert fitted.estimator == direct
    for row in fold.test:
        model_fixture = recalib.Fixture(
            league=row.league, season=row.season, matchweek=row.matchweek,
            kickoff=row.kickoff, home_team=row.home_team, away_team=row.away_team)
        ours = FAMILY.predict(fitted, model_fixture)
        assert ours is not None
        assert ours == direct.predict(model_fixture)


def test_v1_grid_specs_all_fit_and_predict() -> None:
    model_history = history(mixed_rows())
    for half_life in (60.0, 120.0, 240.0):
        for prior in (4.0, 8.0, 16.0):
            spec = spec_for_params({
                "half_life_days": half_life, "team_prior_matches": prior})
            fitted = FAMILY.fit(model_history, spec, CUTOFF)
            assert FAMILY.predict(fitted, fixture()) is not None
            assert fitted.estimator.config.half_life_days == half_life
            assert fitted.estimator.config.team_prior_matches == prior


# ------------------------------------------------------------------ T2


def test_v1_grid_equals_wp_p4_grid_in_declared_order() -> None:
    space = FAMILY.param_space()
    assert space == SEARCH_SPACE_V1
    assert [defn.name for defn in space] == ["half_life_days", "team_prior_matches"]
    assert space[0].choices == wf.HALF_LIVES == (60.0, 120.0, 240.0)
    assert space[1].choices == wf.TEAM_PRIORS == (4.0, 8.0, 16.0)
    grid = [(half_life, prior)
            for half_life in space[0].choices for prior in space[1].choices]
    assert len(grid) == 9
    identities = {spec_for_params({"half_life_days": half_life,
                                   "team_prior_matches": prior}).spec_sha256()
                  for half_life, prior in grid}
    assert len(identities) == 9


def test_spaces_versions_and_comparison_counts() -> None:
    # F5: param_space() exposes v1 only; counts feed the scorecard's
    # "how many configurations were compared" field.
    assert set(SEARCH_SPACES) == {"v1", "v2"}
    assert FAMILY.param_space() is SEARCH_SPACE_V1
    assert combination_count(SEARCH_SPACE_V1) == 9
    assert combination_count(SEARCH_SPACE_V2) == 6 * 5 * 6 * 3 * 3 == 1620
    summary = space_summary()
    assert dict(summary)["version"] == "v1"
    assert dict(summary)["combinations"] == 9
    assert dict(summary["dimensions"])["half_life_days"] == (60.0, 120.0, 240.0)
    assert space_version(SEARCH_SPACE_V2) == "v2"
    with pytest.raises(ValueError):
        space_version(())


def test_v2_sparse_prior_enumeration_is_pinned() -> None:
    # F5: "same as team prior" is resolved to the exact union; None is
    # the unsearched default and stays out of the v2 grid.
    defs = {defn.name: defn for defn in SEARCH_SPACE_V2}
    assert defs["sparse_team_prior"].choices == (0.0, 4.0, 6.0, 8.0, 12.0, 16.0)
    assert None not in defs["sparse_team_prior"].choices
    assert SPARSE_TEAM_PRIOR_CHOICES == (None, 0.0, 4.0, 6.0, 8.0, 12.0, 16.0)
    assert LOW_SCORE_RHO_CHOICES == ("fit", -0.1, 0.0)
    assert [defn.name for defn in SEARCH_SPACE_V2] == [
        "half_life_days", "team_prior_matches", "sparse_team_prior",
        "league_prior_matches", "low_score_rho"]


# ------------------------------------------------------------------ T3


def test_envelope_is_center_plus_eight_perturbations() -> None:
    base = dict(DEFAULT_PARAMS)
    env = envelope_params(base)
    assert len(env) == 9
    assert dict(env[0]) == base
    assert env[1]["half_life_days"] == 60.0
    assert env[2]["half_life_days"] == 240.0
    assert env[3]["team_prior_matches"] == 4.0
    assert env[4]["team_prior_matches"] == 16.0
    assert env[5]["low_score_rho"] == 0.0
    assert env[6]["low_score_rho"] == -0.1
    assert env[7]["sparse_team_prior"] == 0.0
    assert env[8]["sparse_team_prior"] == 16.0
    for item in env[1:]:  # each perturbation changes exactly one key
        assert [key for key in base if item[key] != base[key]] != []
        assert len([key for key in base if item[key] != base[key]]) == 1
    assert [dict(item) for item in BASELINE.envelope] == [dict(item) for item in env]


def test_envelope_of_off_grid_center_and_identity_stability() -> None:
    spec = spec_for_params({"half_life_days": 90.0, "team_prior_matches": 6.0})
    assert [item["half_life_days"] for item in spec.envelope] == [
        90.0, 45.0, 180.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0]
    assert [item["team_prior_matches"] for item in spec.envelope] == [
        6.0, 6.0, 6.0, 3.0, 12.0, 6.0, 6.0, 6.0, 6.0]
    again = spec_for_params({"team_prior_matches": 6.0, "half_life_days": 90.0})
    assert again.spec_sha256() == spec.spec_sha256()
    assert spec == again
    # defaults fill partial assignments
    assert spec_for_params({}).spec_sha256() == spec_for_params(
        {"half_life_days": 120.0}).spec_sha256()
    # int vs float spellings never share an identity (optimize.spec rule)
    assert spec_for_params({"half_life_days": 120}).spec_sha256() != BASELINE.spec_sha256()


def test_envelope_specs_lift_nine_fittable_variants() -> None:
    specs = envelope_specs(BASELINE)
    assert len(specs) == 9
    assert len({spec.spec_sha256() for spec in specs}) == 9
    model_history = history(mixed_rows())
    for spec in specs:
        assert FAMILY.fit(model_history, spec, CUTOFF).estimator.config.half_life_days == (
            dict(spec.params)["half_life_days"])


# ------------------------------------------------------------------ T4


def test_model_version_has_no_legacy_alias() -> None:
    spec = BASELINE
    assert spec.legacy_model_version is None
    assert spec.model_version() == f"{PROCEDURE_VERSION}+{spec.spec_sha256()[:12]}"
    assert spec.model_version().startswith("p4r1+")
    other = spec_for_params({"half_life_days": 60.0})
    assert other.model_version() != spec.model_version()


def test_baseline_identity_is_pinned() -> None:
    # Regression anchor: any change to the baseline spec (params,
    # envelope, contract, procedure version) moves this digest.
    assert BASELINE.spec_sha256() == PINNED_BASELINE_SHA256
    assert BASELINE.model_version() == PINNED_BASELINE_MODEL_VERSION


# ------------------------------------------------------------------ T5


def test_forced_rho_rebuilds_both_layers_and_keeps_config() -> None:
    model_history = history(two_league_rows())
    spec = spec_for_params({"low_score_rho": -0.1})
    fitted = FAMILY.fit(model_history, spec, CUTOFF)
    assert fitted.forced_rho == -0.1
    estimator = fitted.estimator
    # League layer: EVERY league, including the global-fallback one.
    assert set(estimator.league_parameters) == {"Large", "Small"}
    for name, league in estimator.league_parameters.items():
        assert league.rho == -0.1, name
        assert league.rho_source == "forced_by_spec", name
    # Global layer too: predict falls back to it for unfitted leagues.
    assert estimator.global_parameters.rho == -0.1
    assert estimator.global_parameters.rho_source == "forced_by_spec"
    # F4: the Config object keeps the spec-derived values verbatim.
    assert estimator.config == Config(
        half_life_days=120.0, team_prior_matches=8.0, sparse_prior_matches=None,
        league_prior_matches=20.0, completion_lag_hours=3.0)
    # Bit-identical to a manual dataclasses.replace of the direct fit.
    direct = fit(model_history)
    manual = replace(
        direct,
        league_parameters=MappingProxyType({
            name: replace(league, rho=-0.1, rho_source="forced_by_spec")
            for name, league in direct.league_parameters.items()}),
        global_parameters=replace(direct.global_parameters, rho=-0.1,
                                  rho_source="forced_by_spec"))
    assert estimator == manual
    assert FAMILY.predict(fitted, fixture(league="Large")) == manual.predict(
        fixture(league="Large"))


def test_forced_rho_zero_prediction_uses_zero() -> None:
    model_history = history(two_league_rows())
    fitted = FAMILY.fit(model_history, spec_for_params({"low_score_rho": 0.0}), CUTOFF)
    assert fitted.forced_rho == 0.0
    output = FAMILY.predict(fitted, fixture(league="Large"))
    assert output is not None and output.rho == 0.0


def test_forced_rho_predicts_via_global_layer_for_unfitted_league() -> None:
    rows = mixed_rows()
    model_history = History(CUTOFF, rows,
                            frozenset((row.league, row.season, row.matchweek) for row in rows),
                            {"EPL": "2026", "Ghost": "2026"})
    fitted = FAMILY.fit(model_history, spec_for_params({"low_score_rho": -0.1}), CUTOFF)
    assert "Ghost" not in fitted.estimator.league_parameters
    output = FAMILY.predict(fitted, fixture(league="Ghost"))
    assert output is not None
    assert output.parameters.league is fitted.estimator.global_parameters
    assert output.parameters.league.rho == -0.1


def test_forced_minus_0p1_is_clamped_into_the_feasible_interval() -> None:
    # A heavy scorer drives lambda_home above 10, where recalib's
    # feasible-interval lower bound -(1-eps)/lambda exceeds -0.1: the
    # forced value must be projected upward at predict time (F4).
    rows = []
    for n in range(100):
        if n < 12:
            rows.append(match(n, home_team="Heavy", away_team="Beta",
                              home_xg=30.0, away_xg=1.0, home_goals=8, away_goals=0))
        else:
            teams = ("NeutralA", "NeutralB") if n % 2 else ("NeutralB", "NeutralA")
            rows.append(match(n, home_team=teams[0], away_team=teams[1],
                              home_xg=1.0, away_xg=1.0, home_goals=1, away_goals=1))
    model_history = history(tuple(rows))
    precondition = fit(model_history).predict(fixture(home_team="Heavy", away_team="Beta"))
    assert 10.0 < precondition.lambda_home <= 100.0
    fitted = FAMILY.fit(model_history, spec_for_params({"low_score_rho": -0.1}), CUTOFF)
    output = FAMILY.predict(fitted, fixture(home_team="Heavy", away_team="Beta"))
    assert output is not None
    assert output.rho > -0.1  # clamped by recalib, not applied blindly
    assert output.parameters.fitted_rho == -0.1
    assert output.parameters.rho_projected is True
    assert all(probability >= 0.0 for probability in output.totals)
    assert sum(output.totals) == pytest.approx(1.0)


# ------------------------------------------------------------------ T6


def test_fit_refuses_wrong_family_or_procedure_version() -> None:
    model_history = history(mixed_rows())
    foreign = replace(BASELINE, family="club-soccer-frozen")
    with pytest.raises(ValueError, match="family"):
        FAMILY.fit(model_history, foreign, CUTOFF)
    future = replace(BASELINE, procedure_version="p4r2")
    with pytest.raises(ValueError, match="procedure"):
        FAMILY.fit(model_history, future, CUTOFF)


def test_spec_for_params_refuses_off_surface_values() -> None:
    for bad in (
        {"sparse_team_prior": 3.0},
        {"league_prior_matches": 15.0},
        {"low_score_rho": 0.2},
        {"low_score_rho": True},
        {"completion_lag_hours": 2.0},
        {"half_life_days": 0.0},
        {"bogus_key": 1},
    ):
        with pytest.raises(ValueError):
            spec_for_params(bad)


def test_fit_refuses_short_params_and_tampered_envelope() -> None:
    model_history = history(mixed_rows())
    short = ModelSpec(family=FAMILY_ID, procedure_version=PROCEDURE_VERSION,
                      params={"half_life_days": 60.0}, envelope=BASELINE.envelope,
                      data_contract=DATA_CONTRACT)
    with pytest.raises(ValueError, match="six keys"):
        FAMILY.fit(model_history, short, CUTOFF)
    narrow = ModelSpec(family=FAMILY_ID, procedure_version=PROCEDURE_VERSION,
                       params=dict(BASELINE.params),
                       envelope=tuple(BASELINE.envelope)[:8], data_contract=DATA_CONTRACT)
    with pytest.raises(ValueError, match="envelope"):
        FAMILY.fit(model_history, narrow, CUTOFF)
    forged = ModelSpec(family=FAMILY_ID, procedure_version=PROCEDURE_VERSION,
                       params=dict(BASELINE.params),
                       envelope=envelope_params({"half_life_days": 240.0}),
                       data_contract=DATA_CONTRACT)
    with pytest.raises(ValueError, match="envelope"):
        FAMILY.fit(model_history, forged, CUTOFF)


def test_fit_refuses_as_of_disagreement_and_type_errors() -> None:
    model_history = history(mixed_rows())
    with pytest.raises(ValueError, match="as_of"):
        FAMILY.fit(model_history, BASELINE, CUTOFF + timedelta(hours=1))
    with pytest.raises(TypeError):
        FAMILY.fit({"matches": ()}, BASELINE, CUTOFF)
    with pytest.raises(TypeError):
        FAMILY.fit(model_history, "spec", CUTOFF)


def test_predict_refusals_return_none_and_type_errors_raise() -> None:
    fitted = FAMILY.fit(history(mixed_rows()), BASELINE, CUTOFF)
    assert FAMILY.predict(fitted, fixture(kickoff=CUTOFF - timedelta(hours=1))) is None
    assert FAMILY.predict(fitted, Fixture("EPL", "2099", 200, CUTOFF, "Alpha", "Beta")) is None
    assert FAMILY.predict(fitted, fixture(matchweek=1)) is None
    with pytest.raises(TypeError):
        FAMILY.predict(fitted, {"league": "EPL"})
    with pytest.raises(TypeError):
        FAMILY.predict("fitted", fixture())


# ------------------------------------------------------------------ T7


def _run_python(code: str) -> str:
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_family_module_import_purity() -> None:
    # Importing the family adds ONLY the family module and recalib
    # (pure standard library) beyond the optimize subpackage; no
    # database driver, no network module, ever.
    payload = json.loads(_run_python(textwrap.dedent(
        """
        import json
        import sys

        import polymarket_alpha_lab.papersim as base_pkg  # baseline

        baseline = set(sys.modules)

        import polymarket_alpha_lab.papersim.optimize as optimize_pkg

        after_optimize = set(sys.modules)

        import polymarket_alpha_lab.papersim.optimize.families.soccer_recalib

        after_family = set(sys.modules)

        def project(names):
            return sorted(name for name in names
                          if name.startswith("polymarket_alpha_lab"))

        def other(names):
            return sorted(name for name in names
                          if not name.startswith("polymarket_alpha_lab"))

        assert optimize_pkg.ModelSpec is not None
        print(json.dumps({
            "step1_project": project(after_optimize - baseline),
            "step2_project": project(after_family - after_optimize),
            "step1_other": other(after_optimize - baseline),
            "step2_other": other(after_family - after_optimize),
            "psycopg_loaded": "psycopg" in after_family,
        }))
        """
    )))
    assert payload["step1_project"] == [
        "polymarket_alpha_lab.papersim.optimize",
        "polymarket_alpha_lab.papersim.optimize.spec",
    ]
    assert payload["step2_project"] == [
        "polymarket_alpha_lab.papersim.optimize.families",
        "polymarket_alpha_lab.papersim.optimize.families.base",
        "polymarket_alpha_lab.papersim.optimize.families.soccer_recalib",
        "polymarket_alpha_lab.papersim.recalib",
    ]
    assert payload["step1_other"] == []
    assert payload["step2_other"] == []
    assert payload["psycopg_loaded"] is False
