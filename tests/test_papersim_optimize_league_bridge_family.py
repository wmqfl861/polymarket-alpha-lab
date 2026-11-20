"""MO-5e: the league-bridge family.

Pins the contracts of ``papersim.optimize.families.league_bridge``
(design 04 sections 2, 7 and 8; plan 05 node MO-5 row 5e; reviewed
node plan P2/P3 findings incorporated):

- family shape and registry (no search space, NO targets -- the
  bridge is an input, not a scoring lineage);
- the baseline spec identity pinned to the production constants
  (an owner re-freeze flips the identity: intended semantics);
- the pre-season trigger: UTC-day normalization rule, the strict
  boundary, purity (no clock/file);
- the acceptance anchor: a mid-season re-estimation request is
  REJECTED and leaves a record -- the in-process return value IS
  the record (persistence is MO-6's, review P3-3) -- while a
  pre-season request binds the harness report and proposes;
- freeze verification via ``queue_policy.check_kappa_freeze``
  (MATCH / DRIFTED / UNVERIFIED, evidence retained);
- ``predict`` as a bit-exact pure passthrough of ``xg.pair_lambdas``
  (review P2) plus the unshipped-haircut refusal;
- import purity.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import math
import subprocess
import sys
import textwrap

import pytest

from polymarket_alpha_lab.papersim import (
    league_strength,
    pipeline,
    queue_policy,
    xg,
)
from polymarket_alpha_lab.papersim.optimize import ModelSpec
from polymarket_alpha_lab.papersim.optimize.families import iter_family_ids
from polymarket_alpha_lab.papersim.optimize.families.base import ModelFamily
from polymarket_alpha_lab.papersim.optimize.families.league_bridge import (
    DATA_CONTRACT,
    FAMILY_ID,
    LEAGUE_BRIDGE,
    PROCEDURE_VERSION,
    STATUS_FREEZE_CHECK_FAILED,
    STATUS_PROPOSED,
    STATUS_REJECTED_MID_SEASON,
    TARGETS,
    LeagueBridgeFamily,
    LeagueBridgeFixture,
    LeagueBridgeFitted,
    LeagueBridgeHistory,
    LeagueSeason,
    RefreezeSummary,
    reevaluation_gate,
    request_refreeze,
    verify_in_force_freeze,
)

UTC = datetime.timezone.utc
HOF_TEAM = "TSG 1899 Hoffenheim"
LYO_TEAM = "Olympique Lyonnais"

#: The shipped freeze used FIRST_EVAL_DAY["2025-26"] = 2025-09-16;
#: a future season with the same convention exercises every branch.
SEASON = LeagueSeason("2026-27", datetime.datetime(2026, 9, 16, tzinfo=UTC))
BOUNDARY = datetime.datetime(2026, 9, 16, tzinfo=UTC)
PRE_SEASON = datetime.datetime(2026, 8, 1, 12, 30, tzinfo=UTC)
MID_SEASON = datetime.datetime(2026, 11, 20, 18, 0, tzinfo=UTC)


def _report(**overrides: object) -> dict[str, object]:
    """A WP-P5-shaped estimation report (baseline-identical default)."""

    payload: dict[str, object] = {
        "generated_at": "2026-08-01T00:00:00Z",
        "kappa": dict(league_strength.KAPPA.kappa),
        "fit_rule": "obs kickoff day < 2026-09-15",
        "leak_free": True,
        "n_train": 547,
        "away_haircut": league_strength.AWAY_HAIRCUT,
    }
    payload.update(overrides)
    return payload


def _proposal_spec(haircut: float = league_strength.AWAY_HAIRCUT) -> ModelSpec:
    """A spec with a perturbed haircut (or the baseline when 0.06)."""

    return ModelSpec(
        family=FAMILY_ID,
        procedure_version=PROCEDURE_VERSION,
        params={
            "kappa": dict(league_strength.KAPPA.kappa),
            "away_haircut": haircut,
        },
        envelope=(),
        data_contract=DATA_CONTRACT,
    )


def _frozen_records(matching: bool = True) -> tuple[tuple[str, str], ...]:
    """One in-force season record: its digest of the production table."""

    digest = pipeline.bridge_fingerprint(league_strength.KAPPA.kappa)
    return (("2025-26", digest if matching else "f" * 64),)


def _capitalized_bridge_snapshot() -> dict[str, object]:
    """Two leagues in the capitalized block convention KAPPA is keyed in."""

    return {
        "Bundesliga_2025": {
            "dates": [{"isResult": True, "xG": {"h": 1.5, "a": 1.5}}],
            "teams": {
                "3": {"title": HOF_TEAM, "history": [{"xG": 1.5, "xGA": 1.5}]},
            },
        },
        "Bundesliga_2026": {
            "dates": [],
            "teams": {
                "3": {"title": HOF_TEAM, "history": [{"xG": 1.5, "xGA": 1.5}]},
            },
        },
        "Ligue_1_2025": {
            "dates": [{"isResult": True, "xG": {"h": 1.5, "a": 1.5}}],
            "teams": {
                "4": {"title": LYO_TEAM, "history": [{"xG": 1.9, "xGA": 1.1}]},
            },
        },
        "Ligue_1_2026": {
            "dates": [],
            "teams": {
                "4": {"title": LYO_TEAM, "history": [{"xG": 1.9, "xGA": 1.1}]},
            },
        },
    }


def _fitted_baseline() -> LeagueBridgeFitted:
    return LEAGUE_BRIDGE.fit(
        LeagueBridgeHistory(report=_report(), season=SEASON),
        LEAGUE_BRIDGE.baseline_spec(),
        PRE_SEASON,
    )


# --------------------------------------------------------------- shape


def test_family_shape_and_registry() -> None:
    # MO-5e registers its own key; league-bridge sorts between
    # intl-elo-poisson and nba-pace-efficiency (review P3-4).
    assert iter_family_ids() == (
        "club-soccer-frozen",
        "club-soccer-recalib",
        "crypto-gbm",
        "intl-elo-goal-residuals",
        "intl-elo-poisson",
        "league-bridge",
        "nba-pace-efficiency",
    )
    assert isinstance(LEAGUE_BRIDGE, ModelFamily)
    assert LEAGUE_BRIDGE.family_id == FAMILY_ID == "league-bridge"
    assert LEAGUE_BRIDGE.procedure_version == PROCEDURE_VERSION == "p5r1"
    # Plan 05 row 5e: "不做搜索" -- no search space at all.
    assert LEAGUE_BRIDGE.param_space() == ()
    # The bridge is an input, not a scoring lineage: no targets.
    assert LEAGUE_BRIDGE.targets() == TARGETS == ()
    assert DATA_CONTRACT == "wp-p5-kappa-report-v1"


def test_baseline_spec_pins_production_constants() -> None:
    spec = LEAGUE_BRIDGE.baseline_spec()
    # Referenced from the module constants, verbatim values pinned
    # (the same pin discipline as test_papersim_league_strength).
    assert dict(spec.params["kappa"]) == dict(league_strength.KAPPA.kappa) == {
        "EPL": 0.021655,
        "La_liga": -0.071492,
        "Serie_A": -0.053532,
        "Bundesliga": -0.095116,
        "Ligue_1": -0.182982,
        "nuisance": -0.300105,
    }
    assert spec.params["away_haircut"] == league_strength.AWAY_HAIRCUT == 0.06
    assert spec.envelope == ()  # design 04 section 7: 包络不适用
    assert spec.legacy_model_version is None  # not a queue-partition version
    expected = f"p5r1+{spec.spec_sha256()[:12]}"
    assert spec.model_version() == expected
    # Stable: two constructions share one identity.
    assert LEAGUE_BRIDGE.baseline_spec().spec_sha256() == spec.spec_sha256()
    # Sensitivity (P3-5): an owner re-freeze (new constants) is a NEW
    # identity by design; with no lineages there is no registry init
    # conflict. A one-value perturbation must flip the digest.
    perturbed = ModelSpec(
        family=FAMILY_ID,
        procedure_version=PROCEDURE_VERSION,
        params={
            "kappa": {"EPL": 0.05, **{
                k: v for k, v in league_strength.KAPPA.kappa.items() if k != "EPL"
            }},
            "away_haircut": league_strength.AWAY_HAIRCUT,
        },
        envelope=(),
        data_contract=DATA_CONTRACT,
    )
    assert perturbed.spec_sha256() != spec.spec_sha256()


# -------------------------------------------------------------- trigger


def test_utc_day_rule_is_strict_and_timezone_normalized() -> None:
    # Day-granular on UTC calendar dates (review P3-1).
    day_before = datetime.datetime(2026, 9, 15, 23, 59, 59, 999999, tzinfo=UTC)
    assert reevaluation_gate(SEASON, day_before).allowed is True
    # The boundary day itself is IN season -- even at midnight.
    assert reevaluation_gate(SEASON, BOUNDARY).allowed is False
    late_same_day = datetime.datetime(2026, 9, 16, 23, 0, tzinfo=UTC)
    assert reevaluation_gate(SEASON, late_same_day).allowed is False
    # The same instant spelled in other zones gives the same verdict.
    plus14 = datetime.timezone(datetime.timedelta(hours=14))
    minus12 = datetime.timezone(datetime.timedelta(hours=-12))
    assert reevaluation_gate(SEASON, BOUNDARY.astimezone(plus14)).allowed is False
    just_before = BOUNDARY - datetime.timedelta(microseconds=1)
    assert reevaluation_gate(SEASON, just_before.astimezone(minus12)).allowed is True
    # Naive datetimes have no UTC day: refused, not guessed.
    with pytest.raises(ValueError, match="naive"):
        reevaluation_gate(SEASON, datetime.datetime(2026, 8, 1))
    with pytest.raises(ValueError, match="naive"):
        LeagueSeason("2026-27", datetime.datetime(2026, 9, 16))
    with pytest.raises(TypeError):
        reevaluation_gate(SEASON, "2026-08-01")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        reevaluation_gate("2026-27", PRE_SEASON)  # type: ignore[arg-type]


def test_gate_is_a_pure_value(monkeypatch) -> None:
    monkeypatch.setattr(
        "builtins.open", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no file access")
        )
    )
    first = reevaluation_gate(SEASON, MID_SEASON)
    second = reevaluation_gate(SEASON, MID_SEASON)
    assert first == second
    assert first.allowed is False
    assert first.season == "2026-27"
    assert first.reason is not None and "2026-27" in first.reason
    assert "2026-09-16" in first.reason  # the boundary day is named
    assert reevaluation_gate(SEASON, PRE_SEASON).reason is None


# ------------------------------------------------- request -> one record


def test_pre_season_request_is_proposed() -> None:
    summary = request_refreeze(
        SEASON, PRE_SEASON, _report(), frozen_records=_frozen_records()
    )
    assert summary.status == STATUS_PROPOSED
    assert summary.usable is True
    assert summary.reason is None
    # P3-2: the summary carries the proposed haircut alongside the table.
    assert summary.away_haircut == league_strength.AWAY_HAIRCUT
    assert dict(summary.kappa) == dict(league_strength.KAPPA.kappa)
    digest = pipeline.bridge_fingerprint(league_strength.KAPPA.kappa)
    assert summary.table_sha256 == digest
    baseline = LEAGUE_BRIDGE.baseline_spec()
    assert summary.bound_spec_sha256 == baseline.spec_sha256()
    assert summary.fit_rule == "obs kickoff day < 2026-09-15"
    assert summary.leak_free is True
    assert summary.n_train == 547
    (check,) = summary.freeze_checks
    assert check.status == queue_policy.KAPPA_FREEZE_MATCH
    assert check.season == "2025-26"
    # P3-3: the in-process return value IS the record; as_dict is the
    # JSON-ready registration view MO-6 persists.
    payload = summary.as_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["requested_at"] == PRE_SEASON.isoformat()
    assert payload["freeze_checks"][0]["status"] == "MATCH"
    # Purity: same inputs, same record.
    again = request_refreeze(
        SEASON, PRE_SEASON, _report(), frozen_records=_frozen_records()
    )
    assert again == summary


def test_mid_season_request_is_rejected_and_leaves_a_record() -> None:
    """Plan 05 row 5e acceptance: 赛季中任何重估请求都被拒绝并留下记录."""
    summary = request_refreeze(
        SEASON, MID_SEASON, _report(), frozen_records=_frozen_records()
    )
    assert summary.status == STATUS_REJECTED_MID_SEASON
    assert summary.usable is False
    assert summary.reason is not None and "refused" in summary.reason
    # NOTHING was bound: no proposal fields on the record at all.
    assert summary.away_haircut is None
    assert summary.kappa is None
    assert summary.table_sha256 is None
    assert summary.bound_spec_sha256 is None
    assert summary.fit_rule is None
    # The refusal keeps the freeze evidence of the in-force season.
    (check,) = summary.freeze_checks
    assert check.status == queue_policy.KAPPA_FREEZE_MATCH
    assert summary.as_dict()["status"] == "REJECTED_MID_SEASON"
    # The record is reproducible (a record, not a side effect).
    assert request_refreeze(
        SEASON, MID_SEASON, _report(), frozen_records=_frozen_records()
    ) == summary


def test_fit_refuses_mid_season_even_when_bypassing_the_gate() -> None:
    # Defense in depth: fit is the second layer.
    with pytest.raises(ValueError, match="mid-season bind refused"):
        LEAGUE_BRIDGE.fit(
            LeagueBridgeHistory(report=_report(), season=SEASON),
            LEAGUE_BRIDGE.baseline_spec(),
            MID_SEASON,
        )


# ----------------------------------------------------- freeze checking


def test_freeze_check_failure_blocks_the_proposal_and_keeps_evidence() -> None:
    summary = request_refreeze(
        SEASON, PRE_SEASON, _report(), frozen_records=_frozen_records(False)
    )
    assert summary.status == STATUS_FREEZE_CHECK_FAILED
    assert summary.usable is False
    assert summary.reason is not None and "drifted" in summary.reason
    # The proposal is retained as evidence, digests on both sides kept.
    assert summary.away_haircut == league_strength.AWAY_HAIRCUT
    assert dict(summary.kappa) == dict(league_strength.KAPPA.kappa)
    (check,) = summary.freeze_checks
    assert check.status == queue_policy.KAPPA_FREEZE_DRIFTED
    assert check.frozen_sha256 == "f" * 64
    assert check.observed_sha256 == pipeline.bridge_fingerprint(
        league_strength.KAPPA.kappa
    )
    # A missing digest is UNVERIFIED -- blocked the same way.
    missing = request_refreeze(
        SEASON, PRE_SEASON, _report(), frozen_records=(("2025-26", None),)
    )
    assert missing.status == STATUS_FREEZE_CHECK_FAILED
    assert "unverified" in (missing.reason or "")
    # Argument validation is delegated to check_kappa_freeze.
    with pytest.raises(ValueError, match="season"):
        verify_in_force_freeze((("", "a" * 64),))


def test_verify_in_force_freeze_defaults_to_the_production_table() -> None:
    digest = pipeline.bridge_fingerprint(league_strength.KAPPA.kappa)
    (match,) = verify_in_force_freeze((("2025-26", digest),))
    assert match.status == queue_policy.KAPPA_FREEZE_MATCH
    assert match.usable is True
    assert match.blocked_reason is None
    # An explicitly observed table equal to production is the same
    # check; a wrong type is refused.
    (again,) = verify_in_force_freeze(
        (("2025-26", digest),), observed_table=league_strength.KAPPA
    )
    assert again.status == queue_policy.KAPPA_FREEZE_MATCH
    with pytest.raises(TypeError):
        verify_in_force_freeze((("2025-26", digest),), observed_table={})  # type: ignore[arg-type]


# ------------------------------------------------- report/spec contracts


def test_report_contract_is_enforced() -> None:
    history = LeagueBridgeHistory(report=_report(), season=SEASON)
    spec = LEAGUE_BRIDGE.baseline_spec()
    good = LEAGUE_BRIDGE.fit(history, spec, PRE_SEASON)
    assert good.n_train == 547 and good.leak_free is True

    def fit_with(**overrides: object) -> None:
        LEAGUE_BRIDGE.fit(
            LeagueBridgeHistory(report=_report(**overrides), season=SEASON),
            spec,
            PRE_SEASON,
        )

    # kappa member: the shared loader rule (kappa_from_report).
    with pytest.raises(ValueError, match="kappa"):
        fit_with(kappa={"EPL": True})
    with pytest.raises(ValueError, match='must contain a "kappa"'):
        LEAGUE_BRIDGE.fit(
            LeagueBridgeHistory(report={"fit_rule": "x"}, season=SEASON),
            spec,
            PRE_SEASON,
        )
    # fit_rule: harness spelling, cutoff strictly before the boundary.
    for bad_rule in (
        "fit through 2026-01-30",
        "obs kickoff day <= 2026-09-15",
        20260915,
        None,
        "obs kickoff day < 2026-09-16",  # NOT strictly before the boundary
        "obs kickoff day < 2026-09-17",  # after it: leaky
    ):
        with pytest.raises(ValueError, match="fit_rule|cutoff"):
            fit_with(fit_rule=bad_rule)
    # leak_free must be self-certified true with a positive count.
    with pytest.raises(ValueError, match="leak_free"):
        fit_with(leak_free=False)
    with pytest.raises(ValueError, match="leak_free"):
        fit_with(leak_free=None)
    for bad_n in (0, -1, None, True, 547.0):
        with pytest.raises(ValueError, match="n_train"):
            fit_with(n_train=bad_n)
    # away_haircut: finite, non-negative.
    for bad_h in (None, -0.06, float("inf"), True):
        with pytest.raises(ValueError, match="away_haircut"):
            fit_with(away_haircut=bad_h)


def test_fit_binds_identity_to_behavior() -> None:
    # The report's estimate must BE the spec's table: a split between
    # spec identity and bound behavior is refused (the nba envelope
    # rule, applied to the kappa mapping).
    drifted = dict(league_strength.KAPPA.kappa)
    drifted["EPL"] = 0.05
    with pytest.raises(ValueError, match="report kappa != spec kappa"):
        LEAGUE_BRIDGE.fit(
            LeagueBridgeHistory(report=_report(kappa=drifted), season=SEASON),
            LEAGUE_BRIDGE.baseline_spec(),
            PRE_SEASON,
        )
    with pytest.raises(ValueError, match="away_haircut"):
        LEAGUE_BRIDGE.fit(
            LeagueBridgeHistory(report=_report(away_haircut=0.08), season=SEASON),
            LEAGUE_BRIDGE.baseline_spec(),
            PRE_SEASON,
        )


def test_fit_validates_the_spec_shape() -> None:
    history = LeagueBridgeHistory(report=_report(), season=SEASON)
    with pytest.raises(ValueError, match="family"):
        LEAGUE_BRIDGE.fit(
            history,
            ModelSpec(
                family="club-soccer-frozen",
                procedure_version=PROCEDURE_VERSION,
                params={},
                envelope=(),
                data_contract=DATA_CONTRACT,
            ),
            PRE_SEASON,
        )
    with pytest.raises(ValueError, match="procedure version"):
        LEAGUE_BRIDGE.fit(
            history,
            ModelSpec(
                family=FAMILY_ID,
                procedure_version="p5r0",
                params={},
                envelope=(),
                data_contract=DATA_CONTRACT,
            ),
            PRE_SEASON,
        )
    with pytest.raises(ValueError, match="data contract"):
        LEAGUE_BRIDGE.fit(
            history,
            ModelSpec(
                family=FAMILY_ID,
                procedure_version=PROCEDURE_VERSION,
                params={"kappa": dict(league_strength.KAPPA.kappa)},
                envelope=(),
                data_contract="wp-p4-match-history-v1",
            ),
            PRE_SEASON,
        )
    with pytest.raises(ValueError, match="envelope"):
        LEAGUE_BRIDGE.fit(
            history,
            ModelSpec(
                family=FAMILY_ID,
                procedure_version=PROCEDURE_VERSION,
                params={
                    "kappa": dict(league_strength.KAPPA.kappa),
                    "away_haircut": 0.06,
                },
                envelope=({"name": "x"},),
                data_contract=DATA_CONTRACT,
            ),
            PRE_SEASON,
        )
    with pytest.raises(ValueError, match="two keys"):
        LEAGUE_BRIDGE.fit(
            history,
            ModelSpec(
                family=FAMILY_ID,
                procedure_version=PROCEDURE_VERSION,
                params={"kappa": dict(league_strength.KAPPA.kappa)},
                envelope=(),
                data_contract=DATA_CONTRACT,
            ),
            PRE_SEASON,
        )
    with pytest.raises(TypeError):
        LEAGUE_BRIDGE.fit(_report(), history, PRE_SEASON)  # type: ignore[arg-type]


# --------------------------------------------------------------- predict


def test_predict_is_a_bit_exact_passthrough_of_xg() -> None:
    """Review P2: the family never re-derives the bridged arithmetic.

    ``predict`` delegates to ``xg.pair_lambdas(bridge=KAPPA.kappa)``
    and the results are bit-identical by construction; the property
    double-check below (home * f, away * haircut_factor / f with the
    haircut BEFORE the tilt division) guards the delegation itself.
    """
    fitted = _fitted_baseline()
    snapshot = _capitalized_bridge_snapshot()
    fixture = LeagueBridgeFixture(
        snapshot,
        HOF_TEAM,
        "Bundesliga",
        LYO_TEAM,
        "Ligue_1",
        last_weight=0.5,
        prior_games=8.0,
        season="2026",
    )
    got = LEAGUE_BRIDGE.predict(fitted, fixture)
    want = xg.pair_lambdas(
        snapshot,
        HOF_TEAM,
        "Bundesliga",
        LYO_TEAM,
        "Ligue_1",
        last_weight=0.5,
        prior_games=8.0,
        season="2026",
        bridge=league_strength.KAPPA.kappa,
    )
    assert got is not None and want is not None
    assert got == want  # bit-exact: the same single computation
    # Property: f unclamped (delta 0.087866 < ln 1.25); HOF is
    # league-average (multipliers 1.0), Lyon blend-w=.5-p=8 attack
    # 25/21 and defense 17/21 over base 1.5/1.5 (the frozen test
    # fixture convention of test_papersim_league_strength).
    delta = (
        league_strength.KAPPA.kappa["Bundesliga"]
        - league_strength.KAPPA.kappa["Ligue_1"]
    )
    factor = math.exp(delta)
    assert factor == league_strength.bridge_factor(
        league_strength.KAPPA.kappa, "Bundesliga", "Ligue_1"
    )
    assert got[0] == pytest.approx(1.5 * (17 / 21) * factor, rel=1e-12)
    assert got[1] == pytest.approx(
        1.5 * (25 / 21) * league_strength.AWAY_HAIRCUT_FACTOR / factor, rel=1e-12
    )
    # Unknown league: the explicit refusal propagates unchanged.
    unknown = LeagueBridgeFixture(
        snapshot,
        HOF_TEAM,
        "Eredivisie",
        LYO_TEAM,
        "Ligue_1",
        last_weight=0.5,
        prior_games=8.0,
        season="2026",
    )
    assert LEAGUE_BRIDGE.predict(fitted, unknown) is None
    with pytest.raises(TypeError):
        LEAGUE_BRIDGE.predict(fitted, fixture.snapshot)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        LEAGUE_BRIDGE.predict(fixture, fixture)  # type: ignore[arg-type]


def test_predict_refuses_an_unshipped_haircut() -> None:
    """A proposal haircut the frozen xg path cannot express is refused.

    Shipping a new haircut is an owner-authorized constant change
    (DELIVERY_PLAN section 75), never a family-side re-derivation of
    the bridged arithmetic (review P2).
    """
    fitted = LEAGUE_BRIDGE.fit(
        LeagueBridgeHistory(report=_report(away_haircut=0.08), season=SEASON),
        _proposal_spec(haircut=0.08),
        PRE_SEASON,
    )
    fixture = LeagueBridgeFixture(
        _capitalized_bridge_snapshot(),
        HOF_TEAM,
        "Bundesliga",
        LYO_TEAM,
        "Ligue_1",
        last_weight=0.5,
        prior_games=8.0,
        season="2026",
    )
    with pytest.raises(ValueError, match="unshipped proposal haircut"):
        LEAGUE_BRIDGE.predict(fitted, fixture)


def test_a_genuine_proposal_binds_end_to_end() -> None:
    """A perturbed pre-season proposal: PROPOSED with its own identity."""
    new_kappa = {"EPL": 0.05, **{
        k: v for k, v in league_strength.KAPPA.kappa.items() if k != "EPL"
    }}
    spec = ModelSpec(
        family=FAMILY_ID,
        procedure_version=PROCEDURE_VERSION,
        params={"kappa": new_kappa, "away_haircut": 0.08},
        envelope=(),
        data_contract=DATA_CONTRACT,
    )
    summary = request_refreeze(
        SEASON,
        PRE_SEASON,
        _report(kappa=new_kappa, away_haircut=0.08, fit_rule="obs kickoff day < 2026-08-01", n_train=612),
        spec=spec,
        frozen_records=_frozen_records(),
    )
    assert summary.status == STATUS_PROPOSED
    assert summary.away_haircut == 0.08  # P3-2: carried, not defaulted
    assert dict(summary.kappa) == new_kappa
    assert summary.table_sha256 == pipeline.bridge_fingerprint(new_kappa)
    assert summary.bound_spec_sha256 == spec.spec_sha256()
    assert summary.n_train == 612
    # The in-force freeze is still verified against PRODUCTION KAPPA
    # (the default observed table), not against the proposal.
    (check,) = summary.freeze_checks
    assert check.status == queue_policy.KAPPA_FREEZE_MATCH


# ------------------------------------------------------ record integrity


def test_summary_pairing_is_enforced() -> None:
    base = dict(
        family_id=FAMILY_ID,
        season="2026-27",
        status=STATUS_PROPOSED,
        requested_at=PRE_SEASON,
        first_eval_day=BOUNDARY,
        away_haircut=0.06,
        kappa=dict(league_strength.KAPPA.kappa),
        table_sha256="a" * 64,
        bound_spec_sha256="b" * 64,
        fit_rule="obs kickoff day < 2026-09-15",
    )
    # PROPOSED must not carry a reason...
    with pytest.raises(ValueError, match="no reason"):
        RefreezeSummary(**base, reason="why")
    # ...and must carry every proposal field.
    for missing in ("away_haircut", "kappa", "table_sha256", "fit_rule"):
        with pytest.raises(ValueError, match="must carry"):
            RefreezeSummary(**{k: v for k, v in base.items() if k != missing})
    # A rejection without a reason is not a record.
    with pytest.raises(ValueError, match="reason"):
        RefreezeSummary(
            family_id=FAMILY_ID,
            season="2026-27",
            status=STATUS_REJECTED_MID_SEASON,
            requested_at=MID_SEASON,
            first_eval_day=BOUNDARY,
        )
    # A rejection carries NO proposal fields.
    with pytest.raises(ValueError, match="carries no away_haircut"):
        RefreezeSummary(
            family_id=FAMILY_ID,
            season="2026-27",
            status=STATUS_REJECTED_MID_SEASON,
            requested_at=MID_SEASON,
            first_eval_day=BOUNDARY,
            reason="mid-season",
            away_haircut=0.06,
        )
    # FREEZE_CHECK_FAILED requires a non-MATCH check to point at.
    with pytest.raises(ValueError, match="non-MATCH"):
        RefreezeSummary(
            **{**base, "status": STATUS_FREEZE_CHECK_FAILED, "reason": "drift"}
        )
    with pytest.raises(ValueError, match="status"):
        RefreezeSummary(**{**base, "status": "MAYBE"})
    # The record's kappa mapping is deeply frozen.
    record = RefreezeSummary(**base)
    with pytest.raises(TypeError):
        record.kappa["EPL"] = 9.9  # type: ignore[index]


# ---------------------------------------------------------- import purity


def test_family_import_purity() -> None:
    # league_strength/xg are papersim-baseline modules; the family
    # additionally pulls the pure decision layers pipeline,
    # queue_policy and evaluation (with research_probability_scores
    # and its two type helpers) -- no database driver, no network.
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

                from polymarket_alpha_lab.papersim.optimize.families import league_bridge

                after = set(sys.modules)

                assert league_bridge.LEAGUE_BRIDGE is not None
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
        "polymarket_alpha_lab.papersim.evaluation",
        "polymarket_alpha_lab.papersim.optimize",
        "polymarket_alpha_lab.papersim.optimize.families",
        "polymarket_alpha_lab.papersim.optimize.families.base",
        "polymarket_alpha_lab.papersim.optimize.families.league_bridge",
        "polymarket_alpha_lab.papersim.optimize.spec",
        "polymarket_alpha_lab.papersim.pipeline",
        "polymarket_alpha_lab.papersim.queue_policy",
        "polymarket_alpha_lab.research_probability_scores",
        "polymarket_alpha_lab.team_research_agent_types",
        "polymarket_alpha_lab.team_taxonomy",
    ]
    assert payload["other"] == []
    assert payload["psycopg"] is False


def test_no_file_access_in_the_record_path(monkeypatch) -> None:
    # The trigger, the verification and the whole request->record
    # path are pure: any open() is a bug (the kappa_from_report twin
    # made the payload path file-free).
    monkeypatch.setattr(
        "builtins.open", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no file access")
        )
    )
    summary = request_refreeze(
        SEASON, PRE_SEASON, _report(), frozen_records=_frozen_records()
    )
    assert summary.status == STATUS_PROPOSED
    rejected = request_refreeze(
        SEASON, MID_SEASON, _report(), frozen_records=_frozen_records()
    )
    assert rejected.status == STATUS_REJECTED_MID_SEASON


def test_singleton_and_dataclass_types() -> None:
    assert isinstance(LEAGUE_BRIDGE, LeagueBridgeFamily)
    assert dataclasses.is_dataclass(LeagueSeason)
    assert dataclasses.is_dataclass(LeagueBridgeHistory)
    assert dataclasses.is_dataclass(LeagueBridgeFixture)
    assert dataclasses.is_dataclass(LeagueBridgeFitted)
    assert dataclasses.is_dataclass(RefreezeSummary)
    # The default request path: no spec means the current baseline.
    summary = request_refreeze(
        SEASON, PRE_SEASON, _report(), frozen_records=_frozen_records()
    )
    assert summary.bound_spec_sha256 == (
        LEAGUE_BRIDGE.baseline_spec().spec_sha256()
    )
