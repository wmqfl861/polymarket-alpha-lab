"""WP-INTL-2 tests: international Elo->Poisson match model.

Hand-checkable layers, in the style of the nba/soccer model tests:

- Pure-bridge arithmetic on clean numbers (slope math, lambda split,
  LAMBDA_FLOOR extremes, neutral-venue home-advantage exclusion).
- Elo replay verified with independent test-side arithmetic (logistic
  expectation + gd multiplier + K tier), single-match and two-stage,
  including the K/gd-multiplier effect inside updates and the strictly
  before (no-peeking) barrier semantics.
- Ratings provenance: table fallback with the documented staleness
  caveat, and the never-mixed rule for half-replay pairs.
- The mini-tournament totals table (mean 2.51-style calibration from 4
  synthetic NL matches) with the global fallback.
- The nine-variant envelope (names, order, ranges, spreads, medians),
  1X2 sums (per variant and the exact median complement), O/U monotonic
  team/total quotes with gate-compatible variant_spread fields, BTTS and
  Dixon-Coles exact-score cells referenced against soccer_model grids.
- Fail-closed paths: soft refusals return None with an explicit reason;
  malformed input raises ValueError/TypeError; purity/determinism.
"""

from __future__ import annotations

import copy

import pytest

from polymarket_alpha_lab.papersim import intl_model as im
from polymarket_alpha_lab.papersim import intl_totals
from polymarket_alpha_lab.papersim.soccer_model import RHO_DC, score_grid

# ---------------------------------------------------------------------------
# Synthetic fixtures (deterministic; no network, no I/O, no intl_data import)
# ---------------------------------------------------------------------------

GEN = "2026-11-12T06:00:00+00:00"


def match_row(date, home, away, home_goals, away_goals,
              tournament="Friendly", neutral=False, venue=None):
    """One frozen-contract match row."""
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
    """One frozen-contract snapshot."""
    return {
        "generated_at": GEN,
        "teams": {} if teams is None else teams,
        "matches": list(matches),
    }


# One friendly: AAA 2-0 BBB at home on Jan 1 (K=20, gd multiplier 1.5).
REPLAY_SNAP = snap([match_row("2026-01-01", "AAA", "BBB", 2, 0)])

# Two-stage: AAA 2-0 BBB home (Jan 1), then BBB 1-0 AAA home (Feb 1).
TWO_STAGE_SNAP = snap(
    [
        match_row("2026-01-01", "AAA", "BBB", 2, 0),
        match_row("2026-02-01", "BBB", "AAA", 1, 0),
    ]
)

# WC-finals blowout: AAA 4-1 BBB (K=60, gd multiplier 1.75).
WC_SNAP = snap([match_row("2026-01-01", "AAA", "BBB", 4, 1, "FIFA World Cup")])

# Table-only snapshot: no matches, current Elo from the teams table.
TABLE_SNAP = snap([], teams={"AAA": {"elo": 1600.0}, "BBB": {"elo": 1500.0}})

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


def gd_mult_ref(gd):
    """Gd multiplier reference (independent test-side arithmetic)."""
    if gd <= 1:
        return 1.0
    if gd == 2:
        return 1.5
    return (11.0 + gd) / 8.0


def mutated(source, mutation):
    copy_ = copy.deepcopy(source)
    mutation(copy_)
    return copy_


# ---------------------------------------------------------------------------
# Policy constant and pure-bridge arithmetic
# ---------------------------------------------------------------------------


def test_policy_constant_and_variant_names() -> None:
    # International 1X2 stays research-only; the model computes it but
    # callers/gates must honor the ban mechanically.
    assert im.INTL_MW_RESEARCH_ONLY is True
    assert im.VARIANT_NAMES == (
        "slope0.40-totalx1",
        "slope0.40-totalx1.05",
        "slope0.40-totalx0.95",
        "slope0.47-totalx1",
        "slope0.47-totalx1.05",
        "slope0.47-totalx0.95",
        "slope0.55-totalx1",
        "slope0.55-totalx1.05",
        "slope0.55-totalx0.95",
    )
    assert len(im.VARIANTS) == 9


def test_k_factor_tiers_and_goal_difference_multiplier() -> None:
    assert im.k_factor("FIFA World Cup") == 60.0
    assert im.k_factor("FIFA World Cup qualification") == 40.0
    assert im.k_factor("UEFA Euro") == 50.0
    assert im.k_factor("UEFA Euro qualification") == 40.0
    assert im.k_factor("Copa América") == 50.0  # exact martj42 corpus spelling
    assert im.k_factor("AFC Asian Cup") == 50.0
    assert im.k_factor("UEFA Nations League") == 40.0
    assert im.k_factor("Friendly") == 20.0
    assert im.k_factor("Some New Trophy") == 40.0  # documented default
    with pytest.raises(ValueError, match="tournament"):
        im.k_factor(5)
    assert im.goal_difference_multiplier(0) == 1.0
    assert im.goal_difference_multiplier(1) == 1.0
    assert im.goal_difference_multiplier(2) == 1.5
    assert im.goal_difference_multiplier(3) == pytest.approx(14.0 / 8.0)
    assert im.goal_difference_multiplier(5) == pytest.approx(16.0 / 8.0)
    with pytest.raises(ValueError, match="goal difference"):
        im.goal_difference_multiplier(-1)
    with pytest.raises(ValueError, match="goal difference"):
        im.goal_difference_multiplier(1.5)


def test_expected_gd_and_lambda_split_hand_arithmetic() -> None:
    # Non-neutral: diff 100 + HA 100 -> effective 200 -> gd 0.47*2 = 0.94;
    # split of total 2.5 -> (1.72, 0.78).
    assert im.expected_gd(1600.0, 1500.0) == pytest.approx(0.94, abs=1e-12)
    lambdas = im.pair_lambdas_from_elo(1600.0, 1500.0, 2.5)
    assert lambdas == pytest.approx((1.72, 0.78), abs=1e-12)
    # Neutral excludes the home advantage: diff 100 -> gd 0.47.
    assert im.expected_gd(1600.0, 1500.0, neutral=True) == pytest.approx(
        0.47, abs=1e-12
    )
    assert im.pair_lambdas_from_elo(1600.0, 1500.0, 2.5, neutral=True) == (
        pytest.approx(1.485, abs=1e-12),
        pytest.approx(1.015, abs=1e-12),
    )
    # Configurable slope: 0.55 on the same gap.
    assert im.expected_gd(1600.0, 1500.0, slope=0.55) == pytest.approx(
        1.1, abs=1e-12
    )
    # Home-advantage exclusion is exactly slope * HA / 100 of gd.
    with_ha = im.expected_gd(1700.0, 1490.0, neutral=False)
    without_ha = im.expected_gd(1700.0, 1490.0, neutral=True)
    assert with_ha - without_ha == pytest.approx(0.47, abs=1e-12)


def test_lambda_floor_on_extreme_elo_gap() -> None:
    # Diff 800 (neutral): slope 0.55 -> gd 4.4; total 2.5 -> the away
    # lambda is negative raw (-0.95) and clamps at LAMBDA_FLOOR; the
    # pair then sums to more than the total (documented clamp drift).
    lambdas = im.pair_lambdas_from_elo(2100.0, 1300.0, 2.5, slope=0.55,
                                       neutral=True)
    assert lambdas[0] == pytest.approx(3.45, abs=1e-12)
    assert lambdas[1] == im.LAMBDA_FLOOR == 0.05
    assert sum(lambdas) > 2.5
    # Mirror case: the home side clamps when the away team is stronger.
    flipped = im.pair_lambdas_from_elo(1300.0, 2100.0, 2.5, slope=0.55,
                                       neutral=True)
    assert flipped == (0.05, pytest.approx(3.45, abs=1e-12))
    # Validation of the pure bridge.
    with pytest.raises(ValueError, match="slope"):
        im.pair_lambdas_from_elo(1600.0, 1500.0, 2.5, slope=0.0)
    with pytest.raises(ValueError, match="total"):
        im.pair_lambdas_from_elo(1600.0, 1500.0, 0.0)
    with pytest.raises(ValueError, match="elo_home"):
        im.pair_lambdas_from_elo(float("nan"), 1500.0, 2.5)
    with pytest.raises(ValueError, match="neutral"):
        im.expected_gd(1600.0, 1500.0, neutral="no")


# ---------------------------------------------------------------------------
# Elo replay
# ---------------------------------------------------------------------------


def test_elo_replay_hand_check_single_match() -> None:
    prediction = im.fixture_prediction(REPLAY_SNAP, fixture())
    assert prediction is not None
    # AAA 2-0 BBB home friendly: K=20, mult 1.5, home +100 Elo.
    delta = 20.0 * 1.5 * (1.0 - win_expected(100.0))
    assert prediction.elo_home == pytest.approx(1500.0 + delta, abs=1e-9)
    assert prediction.elo_away == pytest.approx(1500.0 - delta, abs=1e-9)
    assert prediction.rating_source == im.RATING_SOURCE_REPLAY
    assert prediction.ratings_stale is False
    assert (prediction.games_home, prediction.games_away) == (1, 1)
    # Bridge on the replayed gap: effective diff = 2*delta + 100.
    effective = 2.0 * delta + 100.0
    central = prediction.variants[3]
    assert central.spec.slope == im.DEFAULT_SLOPE
    assert central.spec.total_multiplier == 1.0
    assert central.expected_gd == pytest.approx(
        0.47 * effective / 100.0, abs=1e-9
    )
    assert central.lambda_home == pytest.approx(
        1.3 + 0.47 * effective / 200.0, abs=1e-9
    )
    assert central.lambda_away == pytest.approx(
        1.3 - 0.47 * effective / 200.0, abs=1e-9
    )
    # Neutral fixture: home advantage excluded from the bridge, and the
    # gd difference is exactly slope * HA / 100.
    neutral = im.fixture_prediction(REPLAY_SNAP, fixture(neutral=True))
    assert neutral is not None
    assert neutral.home_advantage_applied == 0.0
    assert prediction.home_advantage_applied == 100.0
    assert neutral.variants[3].expected_gd == pytest.approx(
        0.47 * (2.0 * delta) / 100.0, abs=1e-9
    )
    assert (
        central.expected_gd - neutral.variants[3].expected_gd
        == pytest.approx(0.47, abs=1e-12)
    )


def test_elo_replay_two_stage_reference() -> None:
    prediction = im.fixture_prediction(TWO_STAGE_SNAP, fixture())
    assert prediction is not None
    # Stage 1 (AAA 2-0 BBB home friendly): delta1 = 30*(1-E(100)).
    delta1 = 20.0 * 1.5 * (1.0 - win_expected(100.0))
    aaa_after_1 = 1500.0 + delta1
    bbb_after_1 = 1500.0 - delta1
    # Stage 2 (BBB 1-0 AAA home friendly): K=20, mult 1.0, the home
    # side BBB gets +100 on its (lower) rating.
    diff2 = bbb_after_1 + 100.0 - aaa_after_1
    delta2 = 20.0 * 1.0 * (1.0 - win_expected(diff2))
    assert prediction.elo_home == pytest.approx(aaa_after_1 - delta2, abs=1e-9)
    assert prediction.elo_away == pytest.approx(bbb_after_1 + delta2, abs=1e-9)
    assert (prediction.games_home, prediction.games_away) == (2, 2)


def test_elo_replay_k_and_gd_multiplier_in_updates() -> None:
    prediction = im.fixture_prediction(WC_SNAP, fixture())
    assert prediction is not None
    # WC finals blowout: K=60, gd 3 -> mult 1.75, home +100.
    delta = 60.0 * gd_mult_ref(3) * (1.0 - win_expected(100.0))
    assert prediction.elo_home == pytest.approx(1500.0 + delta, abs=1e-9)
    assert prediction.elo_away == pytest.approx(1500.0 - delta, abs=1e-9)


def test_elo_replay_no_peeking_barrier_semantics() -> None:
    # Fixture dated between the two matches: only match 1 counts.
    mid = im.fixture_prediction(TWO_STAGE_SNAP, fixture(date="2026-01-15"))
    assert mid is not None
    delta1 = 20.0 * 1.5 * (1.0 - win_expected(100.0))
    assert mid.elo_home == pytest.approx(1500.0 + delta1, abs=1e-9)
    assert (mid.games_home, mid.games_away) == (1, 1)
    # After both matches the rating must move (match 2 excluded above).
    end = im.fixture_prediction(TWO_STAGE_SNAP, fixture(date="2026-03-01"))
    assert end is not None
    assert end.elo_home != pytest.approx(mid.elo_home, abs=1e-9)
    # A match ON the fixture date is excluded (strictly before).
    on_date = im.fixture_prediction(TWO_STAGE_SNAP, fixture(date="2026-02-01"))
    assert on_date is not None
    assert (on_date.games_home, on_date.games_away) == (1, 1)
    assert on_date.elo_home == pytest.approx(mid.elo_home, abs=1e-9)


# ---------------------------------------------------------------------------
# Ratings provenance: table fallback and the never-mixed rule
# ---------------------------------------------------------------------------


def test_table_fallback_ratings_and_staleness_caveat() -> None:
    prediction = im.fixture_prediction(TABLE_SNAP, fixture())
    assert prediction is not None
    assert prediction.rating_source == im.RATING_SOURCE_CURRENT_TABLE
    assert prediction.ratings_stale is True  # documented current-Elo caveat
    assert prediction.elo_home == 1600.0
    assert prediction.elo_away == 1500.0
    assert (prediction.games_home, prediction.games_away) == (0, 0)
    # Effective diff 200 at slope 0.47, total 2.6: medians are exactly
    # the central variant's (1.77, 0.83).
    assert prediction.total_base == im.GLOBAL_FALLBACK_TOTAL == 2.6
    assert prediction.total_source == im.TOTAL_SOURCE_FALLBACK
    assert prediction.tournament_matches == 0
    assert prediction.lambda_home == pytest.approx(1.77, abs=1e-12)
    assert prediction.lambda_away == pytest.approx(0.83, abs=1e-12)


def test_mixed_provenance_and_unresolvable_pair() -> None:
    # AAA appears in matches, BBB does not; both have table entries ->
    # the table rates BOTH (never a mixed replay/table pair).
    mixed = snap(
        [match_row("2026-01-01", "AAA", "CCC", 1, 1)],
        teams={"AAA": {"elo": 1550.0}, "BBB": {"elo": 1500.0}},
    )
    prediction = im.fixture_prediction(mixed, fixture())
    assert prediction is not None
    assert prediction.rating_source == im.RATING_SOURCE_CURRENT_TABLE
    assert (prediction.elo_home, prediction.elo_away) == (1550.0, 1500.0)
    assert prediction.ratings_stale is True
    # Unresolvable: AAA only in matches (no table entry), BBB only in
    # the table -> provenance cannot be satisfied for both sides.
    bad = snap(
        [match_row("2026-01-01", "AAA", "CCC", 1, 1)],
        teams={"BBB": {"elo": 1500.0}},
    )
    assert im.fixture_prediction(bad, fixture()) is None
    reason = im.refusal_reason(bad, fixture())
    assert reason is not None and "AAA" in reason and "cannot be rated" in reason


@pytest.mark.parametrize("extra_day", ["2026-01-10", "2026-01-11"])
def test_prediction_and_shadow_ignore_same_day_and_later_totals(extra_day) -> None:
    tournament = "UEFA Nations League"
    dated = fixture(date="2026-01-10")
    prior = match_row("2026-01-01", "AAA", "BBB", 2, 0, tournament)
    complete = snap([prior, match_row(extra_day, "AAA", "BBB", 10, 0, tournament)])
    truncated = snap([prior])
    baseline = im.fixture_prediction(truncated, dated, tournament=tournament)
    assert baseline is not None
    assert baseline.total_base == 2.0
    assert baseline.over_line_prob(2.5).p_over == pytest.approx(0.323323, abs=1e-6)
    assert im.fixture_prediction(complete, dated, tournament=tournament) == baseline
    assert (
        intl_totals.IntlTotalsResearch(complete).predict(dated, tournament=tournament)
        == intl_totals.IntlTotalsResearch(truncated).predict(dated, tournament=tournament)
    )


def test_future_replay_membership_cannot_remove_stale_compatibility_quote() -> None:
    dated = fixture(date="2026-01-10")
    prior = match_row("2026-01-01", "AAA", "CCC", 1, 0)
    same_day = match_row(dated.date, "AAA", "BBB", 2, 1)
    teams = {"AAA": {"elo": 1550.0}, "BBB": {"elo": 1500.0}}
    quote = im.fixture_prediction(snap([prior], teams), dated)
    assert quote is not None and quote.ratings_stale
    assert im.fixture_prediction(snap([prior, same_day], teams), dated) == quote


def test_shadow_distinguishes_matchup_goal_tendencies_at_equal_elo() -> None:
    tournament = "UEFA Nations League"
    prior = []
    for day in range(1, 7):
        stamp = f"2026-01-{day:02d}"
        prior.extend([
            match_row(stamp, "AAA", "BBB", 3, 3, tournament, neutral=True),
            match_row(stamp, "CCC", "DDD", 0, 0, tournament, neutral=True),
        ])
    research = intl_totals.IntlTotalsResearch(snap(prior))
    fixtures = [
        fixture(home="AAA", away="BBB", date="2026-01-10", neutral=True),
        fixture(home="CCC", away="DDD", date="2026-01-10", neutral=True),
    ]
    high, low = [research.predict(game, tournament=tournament) for game in fixtures]
    assert high.baseline_total == low.baseline_total == 3.0
    assert high.p_over25 > high.baseline_over25 == low.baseline_over25 > low.p_over25
    augmented = intl_totals.IntlTotalsResearch(snap(prior + [
        match_row("2026-01-10", "AAA", "BBB", 0, 0, tournament),
        match_row("2026-01-11", "CCC", "DDD", 10, 0, tournament),
    ]))
    for game, quote in zip(fixtures, (high, low)):
        assert augmented.predict(game, tournament=tournament) == quote
        assert 0.0 < quote.p_over25 < 1.0
        assert 0.0 < quote.p_under25 < 1.0
        assert quote.p_over25 + quote.p_under25 == 1.0
        assert quote.fallback_reason is None
        assert quote.home_recent_matches == quote.away_recent_matches == 6


def test_shadow_without_history_uses_prior_and_ignores_current_table() -> None:
    dated = fixture()
    quote = intl_totals.IntlTotalsResearch(TABLE_SNAP).predict(dated, tournament="Cup X")
    assert quote.p_over25 == quote.baseline_over25
    assert quote.baseline_total == im.GLOBAL_FALLBACK_TOTAL
    assert quote.home_recent_matches == quote.away_recent_matches == quote.tournament_matches == 0
    assert quote.fallback_reason == "insufficient_recent_matches"
    assert intl_totals.IntlTotalsResearch(snap([])).predict(dated, tournament="Cup X") == quote


# ---------------------------------------------------------------------------
# Per-tournament totals table
# ---------------------------------------------------------------------------


def test_tournament_totals_mini_nl_corpus() -> None:
    totals = im.tournament_totals(NL_SNAP)
    assert set(totals) == {"UEFA Nations League"}
    nl = totals["UEFA Nations League"]
    # Totals 2 + 2 + 3 + 3 over four matches -> mean 2.5.
    assert nl.mean_total == pytest.approx(2.5, abs=1e-12)
    assert nl.matches == 4
    assert nl.tournament == "UEFA Nations League"
    lookup = im.tournament_mean_total(NL_SNAP, "UEFA Nations League")
    assert lookup == nl
    assert im.tournament_mean_total(NL_SNAP, "Friendly") is None
    with pytest.raises(ValueError, match="tournament"):
        im.tournament_mean_total(NL_SNAP, "")
    # Prediction with tournament context uses the corpus mean...
    prediction = im.fixture_prediction(
        NL_SNAP, fixture(), tournament="UEFA Nations League"
    )
    assert prediction is not None
    assert prediction.total_base == pytest.approx(2.5, abs=1e-12)
    assert prediction.total_source == im.TOTAL_SOURCE_TOURNAMENT
    assert prediction.tournament_matches == 4
    # ...and every variant splits its own (multiplied) total exactly.
    for variant in prediction.variants:
        assert variant.total == pytest.approx(
            2.5 * variant.spec.total_multiplier, abs=1e-12
        )
        assert variant.lambda_home + variant.lambda_away == pytest.approx(
            variant.total, abs=1e-9
        )
    # Unknown tournament and no tournament both fall back to 2.6.
    unknown = im.fixture_prediction(NL_SNAP, fixture(), tournament="Friendly")
    plain = im.fixture_prediction(NL_SNAP, fixture())
    assert unknown is not None and plain is not None
    for prediction_fallback in (unknown, plain):
        assert prediction_fallback.total_base == 2.6
        assert prediction_fallback.total_source == im.TOTAL_SOURCE_FALLBACK
        assert prediction_fallback.tournament_matches == 0


# ---------------------------------------------------------------------------
# Variant envelope, 1X2, totals and exact-score outputs
# ---------------------------------------------------------------------------


def test_variant_envelope_grid_fields_and_lambda_medians() -> None:
    prediction = im.fixture_prediction(TABLE_SNAP, fixture())
    assert prediction is not None
    assert tuple(v.spec.name for v in prediction.variants) == im.VARIANT_NAMES
    # Effective diff 200: expected_gd = slope * 2 for every variant.
    for variant in prediction.variants:
        assert variant.expected_gd == pytest.approx(
            2.0 * variant.spec.slope, abs=1e-12
        )
        assert variant.lambda_home == pytest.approx(
            variant.total / 2.0 + variant.spec.slope, abs=1e-12
        )
        assert variant.lambda_away == pytest.approx(
            variant.total / 2.0 - variant.spec.slope, abs=1e-12
        )
    # Medians equal the central variant's values (odd count).
    homes = [v.lambda_home for v in prediction.variants]
    aways = [v.lambda_away for v in prediction.variants]
    assert prediction.lambda_home == pytest.approx(sorted(homes)[4], abs=1e-15)
    assert prediction.lambda_away == pytest.approx(sorted(aways)[4], abs=1e-15)
    # Every swept dimension bites on the 1X2/BTTS aggregates.
    assert prediction.p_home_variant_spread > 1e-6
    assert prediction.p_draw_variant_spread > 1e-6
    assert prediction.p_away_variant_spread > 1e-6
    assert prediction.btts_variant_spread > 1e-6
    homes_probs = [v.outputs.home_win for v in prediction.variants]
    assert prediction.p_home_range == (min(homes_probs), max(homes_probs))
    assert min(homes_probs) <= prediction.p_home <= max(homes_probs)


def test_one_x_two_sums_ranges_and_complement() -> None:
    prediction = im.fixture_prediction(TABLE_SNAP, fixture())
    assert prediction is not None
    # Each variant's grid outputs sum to 1 (soccer machinery).
    for variant in prediction.variants:
        outputs = variant.outputs
        assert outputs.home_win + outputs.draw + outputs.away_win == pytest.approx(
            1.0, abs=1e-12
        )
    # Median triple sums to 1 exactly by the complement convention.
    assert prediction.p_home + prediction.p_draw + prediction.p_away == (
        pytest.approx(1.0, abs=1e-12)
    )
    assert prediction.p_away == pytest.approx(
        1.0 - prediction.p_home - prediction.p_draw, abs=1e-15
    )
    # The favorite at +200 Elo effective is the home side.
    assert prediction.p_home > prediction.p_away
    # Ranges bracket the medians; spreads are max - min.
    assert prediction.p_home_range[0] <= prediction.p_home <= prediction.p_home_range[1]
    assert prediction.p_draw_range[0] <= prediction.p_draw <= prediction.p_draw_range[1]
    assert prediction.p_away_variant_spread == pytest.approx(
        prediction.p_away_range[1] - prediction.p_away_range[0], abs=1e-15
    )


def test_over_under_team_total_quotes_monotone_and_gate_fields() -> None:
    prediction = im.fixture_prediction(TABLE_SNAP, fixture())
    assert prediction is not None
    lines = [0.5, 1.5, 2.5, 3.5, 4.5]
    quotes = [prediction.over_line_prob(line) for line in lines]
    overs = [quote.p_over for quote in quotes]
    unders = [quote.p_under for quote in quotes]
    assert all(overs[i] > overs[i + 1] for i in range(len(overs) - 1))
    assert all(unders[i] < unders[i + 1] for i in range(len(unders) - 1))
    for quote in quotes:
        assert quote.p_over + quote.p_under == pytest.approx(1.0, abs=1e-12)
        assert len(quote.over_variants) == 9
        assert quote.variant_spread == pytest.approx(
            max(quote.over_variants) - min(quote.over_variants), abs=1e-15
        )
        assert quote.over_range[0] <= quote.p_over <= quote.over_range[1]
        assert quote.under_range[0] <= quote.p_under <= quote.under_range[1]
    # Far tail collapses to exactly zero (grid caps at 20 goals).
    far = prediction.over_line_prob(100.0)
    assert far.p_over == 0.0 and far.p_under == 1.0
    # Team totals: the +200-Elo favorite carries the higher over prob.
    home_q = prediction.team_total_over_prob("home", 1.5)
    away_q = prediction.team_total_over_prob("away", 1.5)
    assert home_q.p_over > away_q.p_over
    assert home_q.p_over + home_q.p_under == pytest.approx(1.0, abs=1e-12)
    with pytest.raises(ValueError, match="side"):
        prediction.team_total_over_prob("middle", 1.5)
    with pytest.raises(ValueError, match="line"):
        prediction.over_line_prob(float("nan"))
    with pytest.raises(ValueError, match="line"):
        prediction.over_line_prob("2.5")


def test_btts_and_exact_score_quotes() -> None:
    prediction = im.fixture_prediction(TABLE_SNAP, fixture())
    assert prediction is not None
    btts_values = [v.outputs.btts for v in prediction.variants]
    assert 0.0 < prediction.btts < 1.0
    assert prediction.btts == pytest.approx(sorted(btts_values)[4], abs=1e-15)
    assert prediction.btts_range == (min(btts_values), max(btts_values))
    assert prediction.btts_variant_spread == pytest.approx(
        max(btts_values) - min(btts_values), abs=1e-15
    )
    # Exact-score cells reproduce soccer_model DC grids for every
    # variant's lambdas; medians/ranges aggregate the nine cells.
    for cell in ((0, 0), (1, 1), (2, 0), (0, 3)):
        quote = prediction.exact_score_prob(*cell)
        reference = tuple(
            score_grid(v.lambda_home, v.lambda_away, rho=RHO_DC)[cell[0]][cell[1]]
            for v in prediction.variants
        )
        assert quote.variants == pytest.approx(reference, abs=1e-15)
        assert quote.probability == pytest.approx(sorted(reference)[4], abs=1e-15)
        assert quote.range == (min(reference), max(reference))
        assert quote.variant_spread == pytest.approx(
            max(reference) - min(reference), abs=1e-15
        )
    # The full grid of one variant sums to 1 (normalization preserved).
    total_mass = sum(
        prediction.exact_score_prob(h, a).variants[3]
        for h in range(11)
        for a in range(11)
    )
    assert total_mass == pytest.approx(1.0, abs=1e-9)
    with pytest.raises(ValueError, match="home_goals"):
        prediction.exact_score_prob(11, 0)
    with pytest.raises(ValueError, match="away_goals"):
        prediction.exact_score_prob(1, -1)
    with pytest.raises(ValueError, match="away_goals"):
        prediction.exact_score_prob(1, 1.5)


# ---------------------------------------------------------------------------
# Fail-closed paths
# ---------------------------------------------------------------------------


def test_refusals_unknown_team_and_empty_history() -> None:
    # Unknown team: neither matches nor the teams table carry it.
    missing = fixture(home="ZZZ")
    assert im.fixture_prediction(TABLE_SNAP, missing) is None
    reason = im.refusal_reason(TABLE_SNAP, missing)
    assert reason is not None and "ZZZ" in reason and "missing from the snapshot" in reason
    assert im.refusal_reason(TABLE_SNAP, fixture()) is None
    # Empty history: fixture before (or on) the only match date.
    early = im.fixture_prediction(REPLAY_SNAP, fixture(date="2025-12-31"))
    assert early is None
    reason = im.refusal_reason(REPLAY_SNAP, fixture(date="2025-12-31"))
    assert reason is not None and "no matches strictly before" in reason
    assert "AAA" in reason
    # One-sided empty history: BBB's first match IS the fixture date.
    one_sided = snap(
        [
            match_row("2026-01-01", "AAA", "CCC", 1, 1),
            match_row("2026-06-01", "AAA", "BBB", 0, 0),
        ]
    )
    assert im.fixture_prediction(one_sided, fixture(date="2026-03-01")) is None
    reason = im.refusal_reason(one_sided, fixture(date="2026-03-01"))
    assert reason is not None and "away team 'BBB'" in reason


def test_validation_raises_value_errors() -> None:
    first = lambda s: s["matches"][0]
    cases = [
        (lambda s: first(s).update(home_goals=-1), "home_goals"),
        (lambda s: first(s).update(home_goals=True), "home_goals"),
        (lambda s: first(s).update(away_goals=1.5), "away_goals"),
        (lambda s: first(s).update(date="2026-02-30"), "date"),
        (lambda s: first(s).update(away="AAA"), "differ"),
        (lambda s: first(s).update(neutral="no"), "neutral"),
        (lambda s: first(s).update(venue=5), "venue"),
        (lambda s: first(s).pop("tournament"), "missing"),
        (lambda s: first(s).update(tournament=5), "tournament"),
    ]
    for mutation, needle in cases:
        with pytest.raises(ValueError, match=needle):
            im.fixture_prediction(mutated(REPLAY_SNAP, mutation), fixture())
    # Snapshot envelope.
    with pytest.raises(ValueError, match="snapshot"):
        im.fixture_prediction(None, fixture())
    with pytest.raises(ValueError, match="generated_at"):
        im.fixture_prediction({"teams": {}, "matches": []}, fixture())
    with pytest.raises(ValueError, match="teams"):
        im.fixture_prediction(
            {"generated_at": GEN, "matches": []}, fixture()
        )
    with pytest.raises(ValueError, match="matches"):
        im.fixture_prediction(
            {"generated_at": GEN, "teams": {}, "matches": {"a": 1}}, fixture()
        )
    # Fixture validation.
    with pytest.raises(TypeError, match="IntlFixture"):
        im.fixture_prediction(REPLAY_SNAP, ("AAA", "BBB", "2026-06-01"))
    with pytest.raises(ValueError, match="home"):
        im.fixture_prediction(REPLAY_SNAP, fixture(home=" "))
    with pytest.raises(ValueError, match="differ"):
        im.fixture_prediction(REPLAY_SNAP, fixture(away="AAA"))
    with pytest.raises(ValueError, match="date"):
        im.fixture_prediction(REPLAY_SNAP, fixture(date="June 1"))
    with pytest.raises(ValueError, match="neutral"):
        im.fixture_prediction(REPLAY_SNAP, fixture(neutral=1))
    # Tournament kwarg.
    with pytest.raises(ValueError, match="tournament"):
        im.fixture_prediction(REPLAY_SNAP, fixture(), tournament="")
    with pytest.raises(ValueError, match="tournament"):
        im.fixture_prediction(REPLAY_SNAP, fixture(), tournament=5)
    # Non-finite table Elo on the fallback path.
    nan_elo = snap([], teams={"AAA": {"elo": float("nan")}, "BBB": {"elo": 1500.0}})
    with pytest.raises(ValueError, match="elo"):
        im.fixture_prediction(nan_elo, fixture())
    # Degenerate zero-mean tournament calibration.
    zeros = snap(
        [
            match_row("2026-01-01", "AAA", "BBB", 0, 0, "Cup X"),
            match_row("2026-01-02", "BBB", "AAA", 0, 0, "Cup X"),
        ]
    )
    with pytest.raises(ValueError, match="mean total"):
        im.fixture_prediction(zeros, fixture(), tournament="Cup X")


def test_pure_and_deterministic() -> None:
    original = copy.deepcopy(TWO_STAGE_SNAP)
    first = im.fixture_prediction(TWO_STAGE_SNAP, fixture())
    second = im.fixture_prediction(TWO_STAGE_SNAP, fixture())
    assert first is not None and second is not None
    assert first == second
    assert TWO_STAGE_SNAP == original
    neutral_first = im.fixture_prediction(TABLE_SNAP, fixture(neutral=True))
    neutral_second = im.fixture_prediction(TABLE_SNAP, fixture(neutral=True))
    assert neutral_first is not None and neutral_second is not None
    assert neutral_first == neutral_second


def test_k_factor_vocabulary_pinned_to_adapter() -> None:
    """Cross-engine consistency (2026-11-12 review P1/P2 fix): the model's
    K classification must agree with the adapter's frozen table over the
    ENTIRE corpus tournament vocabulary, so replay Elo can never drift
    from the snapshot's stored elo_history scale."""
    import json
    import pathlib

    from polymarket_alpha_lab.papersim import intl_data

    snap_path = pathlib.Path(__file__).resolve().parents[1] / ".scratch" / "intl_snapshot.json"
    if not snap_path.exists():
        pytest.skip("real intl snapshot not present (vocabulary pin requires corpus)")
    vocab = {m["tournament"] for m in json.loads(snap_path.read_text(encoding="utf-8"))["matches"]}
    assert len(vocab) >= 200
    for tournament in vocab:
        assert im.k_factor(tournament) == intl_data.tournament_k(tournament), tournament


def test_dunder_all_entries_all_resolve() -> None:
    """Every __all__ name of both new intl submodules must resolve.

    Guard added after the 2026-11-12 review's three rounds: stale
    __all__ entries naming deleted constants made ``import *`` raise
    while the suite stayed green (nothing star-imports the submodule).
    """
    from polymarket_alpha_lab.papersim import intl_data, intl_model

    for module in (intl_data, intl_model):
        for name in module.__all__:
            assert hasattr(module, name), (module.__name__, name)
        ns: dict = {}
        exec(f"from {module.__name__} import *", ns)
        for name in module.__all__:
            assert name in ns, (module.__name__, name)
