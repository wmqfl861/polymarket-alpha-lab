"""MO-5b tests: international model families and explicit-default parameters.

Three layers (mirroring tests/test_papersim_optimize_spec.py):

- Parameterization: explicit DEFAULT keyword arguments are bit-identical
  to omitted arguments (dataclass equality on full predictions), and
  NON-DEFAULT arguments demonstrably reach every layer they must change
  — the Elo replay AND the bridge for ``home_advantage``, the variant
  sweep for ``variants``, the recency window / cap / prior for the
  totals model. The non-default assertions are load-bearing: "explicit
  defaults == omitted" would also pass if a keyword argument were
  silently dropped, and only changed-output assertions catch a broken
  forwarding chain (plan-review finding P2-3).
- Spec identity: the param space equals design 04 section 7; the
  baseline envelope is ``intl_model.VARIANTS`` verbatim (the baseline
  path does NO offset arithmetic, because ``0.47 - 0.07`` is not
  float-equal to ``0.40``); ``spec_with`` of the default parameters
  shares the baseline identity (the one-standard-error rule's
  premise); any parameter change re-identifies; the totals baseline
  keeps ``intl_totals.SHADOW_VERSION`` verbatim as its model version.
- Family wiring: both families reproduce direct model calls (default
  and non-default specs), refusals pass through, wrong families and
  out-of-space parameters are rejected, the lazy registry lists both
  ids, and the import-purity contract of MO-1 still holds.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime
import json
import subprocess
import sys
import textwrap

import pytest

from polymarket_alpha_lab.papersim import intl_model as im
from polymarket_alpha_lab.papersim import intl_totals
from polymarket_alpha_lab.papersim.optimize.families import intl as intl_family
from polymarket_alpha_lab.papersim.optimize.families.base import ModelFamily
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec, validate_param

GEN = "2026-11-12T06:00:00+00:00"
AS_OF = datetime.datetime(2026, 11, 12, 6, 0, 0, tzinfo=datetime.timezone.utc)


def match_row(date, home, away, home_goals, away_goals,
              tournament="Friendly", neutral=False, venue=None):
    return {
        "date": date,
        "home": home,
        "away": away,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "tournament": tournament,
        "neutral": neutral,
        "venue": venue,
    }


def snap(matches, teams=None):
    return {
        "generated_at": GEN,
        "teams": {} if teams is None else teams,
        "matches": list(matches),
    }


# One friendly: AAA 2-0 BBB at home on Jan 1 (K=20, gd multiplier 1.5).
REPLAY_SNAP = snap([match_row("2026-01-01", "AAA", "BBB", 2, 0)])

# NL mini corpus: four matches with total goals 2 / 2 / 3 / 3 -> mean 2.5.
NL_SNAP = snap(
    [
        match_row("2026-01-01", "AAA", "CCC", 1, 1, "UEFA Nations League"),
        match_row("2026-01-02", "BBB", "DDD", 2, 0, "UEFA Nations League"),
        match_row("2026-01-03", "CCC", "AAA", 0, 3, "UEFA Nations League"),
        match_row("2026-01-04", "DDD", "BBB", 1, 2, "UEFA Nations League"),
    ]
)


def fixture(**overrides):
    fields = {"home": "AAA", "away": "BBB", "date": "2026-06-01"}
    fields.update(overrides)
    return im.IntlFixture(**fields)


def win_expected(diff):
    """Logistic expected score (independent test-side arithmetic)."""
    return 1.0 / (1.0 + 10.0 ** (-diff / 400.0))


# ---------------------------------------------------------------------------
# Model layer: explicit defaults are bit-identical, non-defaults move output
# ---------------------------------------------------------------------------


def test_explicit_default_kwargs_are_bit_identical() -> None:
    default = im.fixture_prediction(NL_SNAP, fixture(), tournament="UEFA Nations League")
    explicit = im.fixture_prediction(
        NL_SNAP,
        fixture(),
        tournament="UEFA Nations League",
        elo_base=im.ELO_BASE,
        home_advantage=im.HOME_ADVANTAGE,
        global_fallback_total=im.GLOBAL_FALLBACK_TOTAL,
        variants=im.VARIANTS,
    )
    assert default is not None and explicit is not None
    assert default == explicit  # frozen-dataclass equality: bit-identical outputs
    # The lambda_floor kwarg on the bridge helper.
    assert im.pair_lambdas_from_elo(1600.0, 1500.0, 2.5) == im.pair_lambdas_from_elo(
        1600.0, 1500.0, 2.5, lambda_floor=im.LAMBDA_FLOOR
    )
    # Refusal path is parameter-independent (unknown team).
    unknown = im.IntlFixture("XXX", "BBB", "2026-06-01")
    assert im.fixture_prediction(NL_SNAP, unknown, home_advantage=60.0) is None


def test_home_advantage_kwarg_changes_replay_and_bridge() -> None:
    # P2-3: a non-default home advantage must move BOTH the Elo replay
    # and the Elo->goals bridge; hand arithmetic pins each layer.
    pred60 = im.fixture_prediction(REPLAY_SNAP, fixture(), home_advantage=60.0)
    pred100 = im.fixture_prediction(REPLAY_SNAP, fixture())
    assert pred60 is not None and pred100 is not None
    assert pred60 != pred100
    assert pred60.home_advantage_applied == 60.0
    assert pred100.home_advantage_applied == 100.0
    # Replay layer: AAA 2-0 BBB home friendly, K=20, mult 1.5, HA=60.
    delta = 20.0 * 1.5 * (1.0 - win_expected(60.0))
    assert pred60.elo_home == pytest.approx(1500.0 + delta, abs=1e-9)
    assert pred60.elo_away == pytest.approx(1500.0 - delta, abs=1e-9)
    # Bridge layer on the replayed gap: effective diff = 2*delta + 60.
    effective = 2.0 * delta + 60.0
    central = pred60.variants[3]
    assert central.spec.slope == im.DEFAULT_SLOPE
    assert central.expected_gd == pytest.approx(
        0.47 * effective / 100.0, abs=1e-9
    )
    # Validation: negative advantage is rejected at the public boundary.
    with pytest.raises(ValueError, match="home_advantage"):
        im.fixture_prediction(REPLAY_SNAP, fixture(), home_advantage=-1.0)


def test_variants_kwarg_replaces_the_sweep() -> None:
    # P2-3: a reduced envelope must change the aggregated output and the
    # aggregation must equal the median of exactly those variants.
    low_row = im.VARIANTS[0:3]  # the three slope-0.40 variants
    reduced = im.fixture_prediction(REPLAY_SNAP, fixture(), variants=low_row)
    full = im.fixture_prediction(REPLAY_SNAP, fixture())
    assert reduced is not None and full is not None
    home_wins = [variant.outputs.home_win for variant in reduced.variants]
    assert reduced.p_home == im.median(home_wins)
    assert reduced.p_home != full.p_home  # strictly narrower sweep moves the median
    with pytest.raises(ValueError, match="nonempty"):
        im.fixture_prediction(REPLAY_SNAP, fixture(), variants=())
    with pytest.raises(ValueError, match="IntlVariantSpec"):
        im.fixture_prediction(REPLAY_SNAP, fixture(), variants=(0.47,))
    with pytest.raises(ValueError, match="sequence"):
        im.fixture_prediction(REPLAY_SNAP, fixture(), variants="slope0.47")


def _totals_history_snapshot():
    """Ten Cup X matches per team pair, all >365 and <=730 days before the fixture."""
    dates = [
        "2025-01-05", "2025-01-15", "2025-01-25", "2025-02-04",
        "2025-02-14", "2025-02-24", "2025-03-06", "2025-03-16",
        "2025-03-26", "2025-04-05",
    ]
    rows = []
    for index, date in enumerate(dates):
        rows.append(
            match_row(date, "HOME", "OPPL", index % 4, (index + 1) % 3, "Cup X")
        )
        rows.append(
            match_row(date, "AWAY", "OPPJ", index % 3, index % 4, "Cup X")
        )
    return snap(rows)


TOTALS_SNAP = _totals_history_snapshot()
TOTALS_FIXTURE = im.IntlFixture("HOME", "AWAY", "2026-06-01")


def test_totals_explicit_default_kwargs_are_bit_identical() -> None:
    default = intl_totals.IntlTotalsResearch(TOTALS_SNAP)
    explicit = intl_totals.IntlTotalsResearch(
        TOTALS_SNAP,
        recent_days=intl_totals.RECENT_DAYS,
        max_recent_matches=intl_totals.MAX_RECENT_MATCHES,
        min_recent_matches=intl_totals.MIN_RECENT_MATCHES,
        prior_matches=intl_totals.PRIOR_MATCHES,
        elo_base=im.ELO_BASE,
        home_advantage=im.HOME_ADVANTAGE,
        slope=im.DEFAULT_SLOPE,
        global_fallback_total=im.GLOBAL_FALLBACK_TOTAL,
        lambda_floor=im.LAMBDA_FLOOR,
    )
    quote_default = default.predict(TOTALS_FIXTURE, tournament="Cup X")
    quote_explicit = explicit.predict(TOTALS_FIXTURE, tournament="Cup X")
    assert quote_default == quote_explicit
    assert quote_default.fallback_reason is None  # ten obs >= min 4
    # Fallback path (no history at all) is also identical.
    empty = snap([])
    assert intl_totals.IntlTotalsResearch(empty).predict(
        TOTALS_FIXTURE, tournament="Cup X"
    ) == intl_totals.IntlTotalsResearch(
        empty, prior_matches=16, recent_days=1095
    ).predict(TOTALS_FIXTURE, tournament="Cup X")


def test_totals_recent_days_kwarg_moves_the_window() -> None:
    # P2-3: all ten observations sit >365 days before the fixture, so the
    # in-space window 365 must fall back while the default 730 does not.
    default = intl_totals.IntlTotalsResearch(TOTALS_SNAP).predict(
        TOTALS_FIXTURE, tournament="Cup X"
    )
    narrowed = intl_totals.IntlTotalsResearch(
        TOTALS_SNAP, recent_days=365
    ).predict(TOTALS_FIXTURE, tournament="Cup X")
    assert default.fallback_reason is None
    assert narrowed.fallback_reason == "insufficient_recent_matches"
    assert narrowed.p_over25 == narrowed.baseline_over25  # tournament-mean fallback
    assert narrowed.p_over25 != default.p_over25


def test_totals_prior_and_cap_kwargs_move_the_estimate() -> None:
    # P2-3: shrinkage prior and recency cap must change matchup quotes.
    research = intl_totals.IntlTotalsResearch(TOTALS_SNAP)
    prior4 = intl_totals.IntlTotalsResearch(
        TOTALS_SNAP, prior_matches=4
    ).predict(TOTALS_FIXTURE, tournament="Cup X")
    prior16 = intl_totals.IntlTotalsResearch(
        TOTALS_SNAP, prior_matches=16
    ).predict(TOTALS_FIXTURE, tournament="Cup X")
    assert prior4.fallback_reason is None and prior16.fallback_reason is None
    assert (prior4.lambda_home, prior4.lambda_away) != (
        prior16.lambda_home,
        prior16.lambda_away,
    )
    capped = intl_totals.IntlTotalsResearch(
        TOTALS_SNAP, max_recent_matches=8
    ).predict(TOTALS_FIXTURE, tournament="Cup X")
    base = research.predict(TOTALS_FIXTURE, tournament="Cup X")
    assert capped.fallback_reason is None
    assert (capped.lambda_home, capped.lambda_away) != (
        base.lambda_home,
        base.lambda_away,
    )


def test_totals_constructor_validation() -> None:
    # P3: the validity floors are explicit (min_recent_matches >= 1 etc.).
    with pytest.raises(ValueError, match="recent_days"):
        intl_totals.IntlTotalsResearch(TOTALS_SNAP, recent_days=0)
    with pytest.raises(ValueError, match="max_recent_matches"):
        intl_totals.IntlTotalsResearch(TOTALS_SNAP, max_recent_matches=0)
    with pytest.raises(ValueError, match="min_recent_matches"):
        intl_totals.IntlTotalsResearch(TOTALS_SNAP, min_recent_matches=0)
    with pytest.raises(ValueError, match="prior_matches"):
        intl_totals.IntlTotalsResearch(TOTALS_SNAP, prior_matches=-1)
    with pytest.raises(ValueError, match="slope"):
        intl_totals.IntlTotalsResearch(TOTALS_SNAP, slope=0.0)
    with pytest.raises(ValueError, match="home_advantage"):
        intl_totals.IntlTotalsResearch(TOTALS_SNAP, home_advantage=-5.0)


# ---------------------------------------------------------------------------
# Family shape, param space and spec identity
# ---------------------------------------------------------------------------


def test_elo_family_shape_and_space() -> None:
    assert isinstance(intl_family.INTL_ELO, ModelFamily)
    assert intl_family.INTL_ELO.family_id == intl_family.FAMILY_ID_ELO
    assert intl_family.INTL_ELO.family_id == "intl-elo-poisson"
    assert intl_family.INTL_ELO.procedure_version == "intl-elo-v1"
    space = intl_family.INTL_ELO.param_space()
    assert tuple(defn.name for defn in space) == ("slope", "home_advantage")
    slope, home_advantage = space
    assert (slope.kind, slope.default, slope.lower, slope.upper) == (
        "float", im.DEFAULT_SLOPE, 0.40, 0.55
    )
    assert (home_advantage.kind, home_advantage.default) == (
        "choice", im.HOME_ADVANTAGE
    )
    assert home_advantage.choices == (60.0, 80.0, 100.0, 120.0)
    assert intl_family.INTL_ELO.targets() == ("match_winner",)
    assert intl_family.DATA_CONTRACT == "intl-snapshot-v1"


def test_totals_family_shape_and_space() -> None:
    assert isinstance(intl_family.INTL_TOTALS, ModelFamily)
    assert intl_family.INTL_TOTALS.family_id == "intl-elo-goal-residuals"
    assert intl_family.INTL_TOTALS.procedure_version == intl_totals.SHADOW_VERSION
    space = intl_family.INTL_TOTALS.param_space()
    assert tuple(defn.name for defn in space) == (
        "recent_days", "max_recent_matches", "prior_matches"
    )
    by_name = {defn.name: defn for defn in space}
    assert by_name["recent_days"].choices == (365, 730, 1095)
    assert by_name["recent_days"].default == intl_totals.RECENT_DAYS == 730
    assert by_name["max_recent_matches"].choices == (8, 12, 16)
    assert by_name["prior_matches"].choices == (4, 8, 12, 16)
    assert by_name["prior_matches"].default == intl_totals.PRIOR_MATCHES == 8
    assert intl_family.INTL_TOTALS.targets() == ("over_under",)


def test_elo_baseline_envelope_is_verbatim_variants() -> None:
    spec = intl_family.INTL_ELO.baseline_spec()
    assert len(spec.envelope) == 9
    assert [dict(item) for item in spec.envelope] == [
        dataclasses.asdict(variant) for variant in im.VARIANTS
    ]
    assert tuple(str(item["name"]) for item in spec.envelope) == im.VARIANT_NAMES
    # Float-identical slopes: the baseline path does NO offset arithmetic.
    slopes = [float(item["slope"]) for item in spec.envelope]
    assert slopes == [slope for slope in im.SWEEP_SLOPES for _ in range(3)]
    multipliers = [float(item["total_multiplier"]) for item in spec.envelope]
    assert multipliers == list(im.SWEEP_TOTAL_MULTIPLIERS) * 3


def test_totals_baseline_has_empty_envelope_and_legacy_version() -> None:
    spec = intl_family.INTL_TOTALS.baseline_spec()
    assert spec.envelope == ()  # MO-6 defines empty-envelope gate semantics
    assert spec.legacy_model_version == intl_totals.SHADOW_VERSION
    assert spec.model_version() == "intl-elo-goal-residuals-v1"


def test_spec_identity_short_circuit_and_reidentification() -> None:
    # Default parameters (partial or complete) share the baseline identity.
    baseline = intl_family.INTL_ELO.baseline_spec()
    assert intl_family.INTL_ELO.spec_with({}).spec_sha256() == baseline.spec_sha256()
    assert intl_family.INTL_ELO.spec_with(
        {"slope": im.DEFAULT_SLOPE, "home_advantage": im.HOME_ADVANTAGE}
    ).spec_sha256() == baseline.spec_sha256()
    assert intl_family.INTL_ELO.baseline_spec().spec_sha256() == (
        intl_family.INTL_ELO.baseline_spec().spec_sha256()
    )
    # Any change re-identifies; the derived version string carries it.
    for params in ({"slope": 0.50}, {"home_advantage": 80.0}):
        spec = intl_family.INTL_ELO.spec_with(params)
        assert spec.spec_sha256() != baseline.spec_sha256()
        assert spec.legacy_model_version is None
        assert spec.model_version() == (
            f"intl-elo-v1+{spec.spec_sha256()[:12]}"
        )
    totals_baseline = intl_family.INTL_TOTALS.baseline_spec()
    assert intl_family.INTL_TOTALS.spec_with({}).spec_sha256() == (
        totals_baseline.spec_sha256()
    )
    changed = intl_family.INTL_TOTALS.spec_with({"recent_days": 365})
    assert changed.spec_sha256() != totals_baseline.spec_sha256()
    assert changed.legacy_model_version is None
    assert changed.model_version().startswith("intl-elo-goal-residuals-v1+")


def test_elo_envelope_around_new_center() -> None:
    spec = intl_family.INTL_ELO.spec_with({"slope": 0.50})
    offsets = (
        im.SWEEP_SLOPES[0] - im.DEFAULT_SLOPE,
        0.0,
        im.SWEEP_SLOPES[2] - im.DEFAULT_SLOPE,
    )
    expected_slopes = [0.50 + offset for offset in offsets]
    slopes = [float(item["slope"]) for item in spec.envelope]
    assert slopes == [slope for slope in expected_slopes for _ in range(3)]
    assert slopes[3] == 0.50  # the center itself, unchanged
    assert [float(item["total_multiplier"]) for item in spec.envelope] == (
        list(im.SWEEP_TOTAL_MULTIPLIERS) * 3
    )
    assert [str(item["name"]) for item in spec.envelope] == [
        f"slope{slope:.2f}-totalx{multiplier:g}"
        for slope in expected_slopes
        for multiplier in im.SWEEP_TOTAL_MULTIPLIERS
    ]

    fitted = intl_family.INTL_ELO.fit(
        intl_family.IntlHistory(REPLAY_SNAP), spec, AS_OF
    )
    assert [dataclasses.asdict(variant) for variant in fitted.variants] == [
        dict(item) for item in spec.envelope
    ]
    wrapped = intl_family.IntlFamilyFixture(fixture())
    direct = im.fixture_prediction(REPLAY_SNAP, fixture(), variants=fitted.variants)
    assert direct is not None
    assert intl_family.INTL_ELO.predict(fitted, wrapped) == direct


def test_out_of_space_parameters_are_rejected() -> None:
    for params in ({"slope": 0.39}, {"slope": 0.56}, {"home_advantage": 90.0}):
        with pytest.raises(ValueError):
            intl_family.INTL_ELO.spec_with(params)
    with pytest.raises(ValueError):
        intl_family.INTL_TOTALS.spec_with({"recent_days": 400})
    with pytest.raises(ValueError, match="unknown"):
        intl_family.INTL_ELO.spec_with({"bogus": 1})
    slope_def = intl_family.INTL_ELO.param_space()[0]
    with pytest.raises(ValueError):
        validate_param(slope_def, True)  # booleans are never numbers here


# ---------------------------------------------------------------------------
# Family wiring: fit/predict against direct model calls
# ---------------------------------------------------------------------------


def test_elo_family_predict_matches_direct_model() -> None:
    snapshot = copy.deepcopy(NL_SNAP)
    history = intl_family.IntlHistory(snapshot)
    wrapped = intl_family.IntlFamilyFixture(fixture(), tournament="UEFA Nations League")
    before_fit = copy.deepcopy(snapshot)
    fitted = intl_family.INTL_ELO.fit(
        history, intl_family.INTL_ELO.baseline_spec(), AS_OF
    )
    assert snapshot == before_fit
    assert intl_family.INTL_ELO.predict(fitted, wrapped) == im.fixture_prediction(
        NL_SNAP, fixture(), tournament="UEFA Nations League"
    )
    # P2-3 end-to-end: a non-default home-advantage spec through the family.
    spec60 = intl_family.INTL_ELO.spec_with({"home_advantage": 60.0})
    fitted60 = intl_family.INTL_ELO.fit(history, spec60, AS_OF)
    through_family = intl_family.INTL_ELO.predict(fitted60, wrapped)
    direct = im.fixture_prediction(
        NL_SNAP, fixture(), tournament="UEFA Nations League", home_advantage=60.0
    )
    assert through_family == direct
    assert through_family != intl_family.INTL_ELO.predict(fitted, wrapped)
    assert snapshot == before_fit
    assert copy.deepcopy(NL_SNAP) == NL_SNAP


def test_elo_family_refusal_passthrough() -> None:
    fitted = intl_family.INTL_ELO.fit(
        intl_family.IntlHistory(REPLAY_SNAP),
        intl_family.INTL_ELO.baseline_spec(),
        AS_OF,
    )
    unknown = intl_family.IntlFamilyFixture(im.IntlFixture("XXX", "BBB", "2026-06-01"))
    assert intl_family.INTL_ELO.predict(fitted, unknown) is None


def test_totals_family_predict_matches_direct_model() -> None:
    history = intl_family.IntlHistory(TOTALS_SNAP)
    wrapped = intl_family.IntlFamilyFixture(TOTALS_FIXTURE, tournament="Cup X")
    fitted = intl_family.INTL_TOTALS.fit(
        history, intl_family.INTL_TOTALS.baseline_spec(), AS_OF
    )
    assert intl_family.INTL_TOTALS.predict(fitted, wrapped) == (
        intl_totals.IntlTotalsResearch(TOTALS_SNAP).predict(
            TOTALS_FIXTURE, tournament="Cup X"
        )
    )
    # P2-3 end-to-end: a non-default recency spec through the family.
    spec365 = intl_family.INTL_TOTALS.spec_with({"recent_days": 365})
    fitted365 = intl_family.INTL_TOTALS.fit(history, spec365, AS_OF)
    assert intl_family.INTL_TOTALS.predict(fitted365, wrapped) == (
        intl_totals.IntlTotalsResearch(TOTALS_SNAP, recent_days=365).predict(
            TOTALS_FIXTURE, tournament="Cup X"
        )
    )
    assert intl_family.INTL_TOTALS.predict(fitted365, wrapped) != (
        intl_family.INTL_TOTALS.predict(fitted, wrapped)
    )


def test_elo_family_fit_rejects_slope_mismatch_at_envelope_center() -> None:
    family = intl_family.INTL_ELO
    baseline = family.baseline_spec()
    spec = ModelSpec(
        family=baseline.family,
        procedure_version=baseline.procedure_version,
        params={"slope": 0.40, "home_advantage": im.HOME_ADVANTAGE},
        envelope=baseline.envelope,
        data_contract=baseline.data_contract,
    )
    with pytest.raises(ValueError, match="does not match envelope center slope"):
        family.fit(intl_family.IntlHistory(REPLAY_SNAP), spec, AS_OF)


@pytest.mark.parametrize("variant_indices", [(4,), (6, 4), (0, 4, 1)])
def test_elo_family_fit_accepts_shorter_envelopes(
    variant_indices: tuple[int, ...],
) -> None:
    family = intl_family.INTL_ELO
    baseline = family.baseline_spec()
    spec = ModelSpec(
        family=baseline.family,
        procedure_version=baseline.procedure_version,
        params=dict(baseline.params),
        envelope=tuple(baseline.envelope[index] for index in variant_indices),
        data_contract=baseline.data_contract,
    )
    fitted = family.fit(intl_family.IntlHistory(REPLAY_SNAP), spec, AS_OF)
    assert fitted.variants == tuple(im.VARIANTS[index] for index in variant_indices)


def test_fit_rejections() -> None:
    history = intl_family.IntlHistory(REPLAY_SNAP)
    with pytest.raises(TypeError, match="IntlHistory"):
        intl_family.INTL_ELO.fit({"matches": []}, intl_family.INTL_ELO.baseline_spec(), AS_OF)
    with pytest.raises(TypeError, match="IntlHistory"):
        intl_family.INTL_TOTALS.fit([], intl_family.INTL_TOTALS.baseline_spec(), AS_OF)
    foreign = intl_family.INTL_TOTALS.baseline_spec()
    with pytest.raises(ValueError, match="family"):
        intl_family.INTL_ELO.fit(history, foreign, AS_OF)
    with pytest.raises(ValueError, match="family"):
        intl_family.INTL_TOTALS.fit(history, intl_family.INTL_ELO.baseline_spec(), AS_OF)
    # A spec whose params are absent or out of space cannot be fitted.
    empty_params = ModelSpec(
        family="intl-elo-poisson",
        procedure_version="intl-elo-v1",
        params={},
        envelope=intl_family._envelope_items(im.DEFAULT_SLOPE),
        data_contract=intl_family.DATA_CONTRACT,
    )
    with pytest.raises(ValueError, match="missing"):
        intl_family.INTL_ELO.fit(history, empty_params, AS_OF)
    with pytest.raises(ValueError, match="nonempty variant envelope"):
        intl_family.INTL_ELO.fit(
            history,
            ModelSpec(
                family="intl-elo-poisson",
                procedure_version="intl-elo-v1",
                params={"slope": im.DEFAULT_SLOPE, "home_advantage": 80.0},
                envelope=(),
                data_contract=intl_family.DATA_CONTRACT,
            ),
            AS_OF,
        )
    fitted = intl_family.INTL_ELO.fit(history, intl_family.INTL_ELO.baseline_spec(), AS_OF)
    with pytest.raises(TypeError, match="IntlEloFitted"):
        intl_family.INTL_ELO.predict("not-fitted", intl_family.IntlFamilyFixture(fixture()))
    with pytest.raises(TypeError, match="IntlFamilyFixture"):
        intl_family.INTL_ELO.predict(fitted, fixture())
    totals_fitted = intl_family.INTL_TOTALS.fit(
        history, intl_family.INTL_TOTALS.baseline_spec(), AS_OF
    )
    with pytest.raises(TypeError, match="IntlTotalsFitted"):
        intl_family.INTL_TOTALS.predict(fitted, intl_family.IntlFamilyFixture(fixture()))


def test_registry_lists_both_intl_families() -> None:
    from polymarket_alpha_lab.papersim.optimize.families import iter_family_ids

    ids = iter_family_ids()
    assert "intl-elo-poisson" in ids
    assert "intl-elo-goal-residuals" in ids
    import polymarket_alpha_lab.papersim.optimize.families as families_pkg

    module = getattr(families_pkg, "intl-elo-poisson")
    assert module.INTL_ELO is intl_family.INTL_ELO
    assert getattr(families_pkg, "intl-elo-goal-residuals").INTL_TOTALS is (
        intl_family.INTL_TOTALS
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


def test_import_purity_of_the_intl_family_module() -> None:
    # Same two-step snapshot contract as MO-1's purity test: importing
    # the intl family module adds exactly the family modules plus the
    # intl model sources (soccer_model is already in the papersim
    # baseline), and no database driver or other new dependency.
    payload = json.loads(
        _run_python(
            """
            import json
            import sys

            import polymarket_alpha_lab.papersim as base_pkg  # baseline

            baseline = set(sys.modules)

            import polymarket_alpha_lab.papersim.optimize as optimize_pkg

            after_optimize = set(sys.modules)

            import polymarket_alpha_lab.papersim.optimize.families.intl

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
        "polymarket_alpha_lab.papersim.intl_model",
        "polymarket_alpha_lab.papersim.intl_totals",
        "polymarket_alpha_lab.papersim.optimize.families",
        "polymarket_alpha_lab.papersim.optimize.families.base",
        "polymarket_alpha_lab.papersim.optimize.families.intl",
    ]
    assert payload["step1_other"] == []
    assert payload["step2_other"] == []
    assert payload["psycopg_loaded"] is False
