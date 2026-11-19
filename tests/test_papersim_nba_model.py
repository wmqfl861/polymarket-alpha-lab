"""WP-NBA-2 tests: NBA pace/efficiency probabilistic model.

Hand-checkable layers, in the style of the soccer/crypto model tests:

- EWMA ratings verified two independent ways: exact half-life-1 weights
  (0.25 / 0.5 / 1.0 — hand arithmetic) and a recursive-decay reference
  for half-life 20 (a genuinely different code path from the closed-form
  weight loop).
- League context arithmetic (possession-weighted means, HCA from home
  rows, B2B fit eligibility + clamp both directions).
- The frozen expected-points hand-check on a symmetric snapshot: both
  teams league-average in every field, home rows all +3 -> HCA 3.0,
  margin exactly 3.0, total exactly 224.0, p_home = Phi(3/sigma).
- B2B sign (and both-sides margin neutrality), optional rest flag,
  sigma defaults vs walk-forward clamped fit, Normal-CDF complement
  (p_home + p_away = 1), totals monotonicity, the six-variant envelope
  (including the degenerate half-life-dimension caveat), the injury
  stub, purity/determinism, and the fail-closed paths (soft refusals
  return None with an explicit reason; malformed input raises).
"""

from __future__ import annotations

import copy
import math

import pytest

from polymarket_alpha_lab.papersim import nba_model as nb

# ---------------------------------------------------------------------------
# Synthetic fixtures (deterministic; no network, no I/O, no espn_nba import)
# ---------------------------------------------------------------------------


def build_snapshot(games, *, season="2026-27", injuries=None):
    """Build a frozen-contract snapshot from (date, home, away, hp, ap[, opts])."""
    teams: dict[str, dict] = {}
    for entry in games:
        date_s, home, away, home_pts, away_pts = entry[:5]
        opts = entry[5] if len(entry) > 5 else {}
        for team in (home, away):
            teams.setdefault(team, {"display": team, "games": []})

        def row(team_, opponent_, is_home, pts, opp_pts, poss_k, ortg_k, drtg_k):
            return {
                "date": date_s,
                "home": is_home,
                "opponent": opponent_,
                "poss": opts.get(poss_k, opts.get("poss", 100.0)),
                "pts": pts,
                "opp_pts": opp_pts,
                "ortg": opts.get(ortg_k, opts.get("ortg", 112.0)),
                "drtg": opts.get(drtg_k, opts.get("drtg", 112.0)),
                "rest_days": opts.get("rest_home" if is_home else "rest_away"),
                "b2b": opts.get("b2b_home" if is_home else "b2b_away", False),
            }

        teams[home]["games"].append(
            row(home, away, True, home_pts, away_pts, "poss_home", "ortg_home", "drtg_home")
        )
        teams[away]["games"].append(
            row(away, home, False, away_pts, home_pts, "poss_away", "ortg_away", "drtg_away")
        )
    return {
        "generated_at": "2026-11-09T06:00:00+00:00",
        "season": season,
        "teams": teams,
        "injuries": {} if injuries is None else injuries,
    }


# Symmetric base: every ortg/drtg 112, poss 100, all home rows +3.
BASE = build_snapshot(
    [
        ("2026-01-01", "AAA", "BBB", 113, 110),
        ("2026-01-02", "BBB", "AAA", 111, 108),
        ("2026-01-03", "AAA", "BBB", 112, 109),
    ]
)

# AAA rating fields vary per game (110/120/130, drtg 105/115/125,
# poss 98/100/102 across AAA's three games, oldest to newest).
EWMA_SNAP = build_snapshot(
    [
        ("2026-01-01", "AAA", "BBB", 113, 110, {"ortg_home": 110, "drtg_home": 105, "poss_home": 98}),
        ("2026-01-02", "BBB", "AAA", 111, 108, {"ortg_away": 120, "drtg_away": 115, "poss_away": 100}),
        ("2026-01-03", "AAA", "BBB", 112, 109, {"ortg_home": 130, "drtg_home": 125, "poss_home": 102}),
    ]
)

# Zero HCA: home margins +3 then -3.
HCA0_SNAP = build_snapshot(
    [
        ("2026-01-01", "AAA", "BBB", 113, 110),
        ("2026-01-02", "BBB", "AAA", 108, 111),
    ]
)

# 12 paired games with alternating +/-30 home margins and constant 230
# totals: 11 walk-forward residuals, margin std ~31 (clamped to 13.5),
# total std 0 (clamped to 13.0).
SIG_SNAP = build_snapshot(
    [
        (
            f"2026-03-{day:02d}",
            "AAA" if day % 2 else "BBB",
            "BBB" if day % 2 else "AAA",
            130 if day % 2 else 100,
            100 if day % 2 else 130,
        )
        for day in range(1, 13)
    ]
)

# B2B fit: 8 both-b2b games at 103 points, 8 rested games at 106.
def _b2b_games(b2b_pts, rested_pts):
    games = []
    for day in range(1, 17):
        home = "AAA" if day % 2 else "BBB"
        away = "BBB" if day % 2 else "AAA"
        pts = b2b_pts if day <= 8 else rested_pts
        games.append((f"2026-02-{day:02d}", home, away, pts, pts,
                      {"b2b_home": day <= 8, "b2b_away": day <= 8}))
    return games


B2B_SNAP = build_snapshot(_b2b_games(103, 106))
B2B_REV_SNAP = build_snapshot(_b2b_games(106, 103))

# Half-life-differentiating snapshot: AAA ortg 110/114/118.
ENV_SNAP = build_snapshot(
    [
        ("2026-01-01", "AAA", "BBB", 113, 110, {"ortg_home": 110}),
        ("2026-01-02", "BBB", "AAA", 111, 108, {"ortg_away": 114}),
        ("2026-01-03", "AAA", "BBB", 112, 109, {"ortg_home": 118}),
    ]
)


def fixture(**overrides):
    fields = {"home": "AAA", "away": "BBB", "rest_home": None,
              "rest_away": None, "b2b_home": False, "b2b_away": False}
    fields.update(overrides)
    return nb.NbaFixture(**fields)


def phi(z):
    """Standard normal CDF, the erfc form used by the module."""
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def ewma_ref(values, half_life):
    """Recursive-decay EWMA reference (independent arithmetic path)."""
    decay = 2.0 ** (-1.0 / half_life)
    weight = value_sum = 0.0
    for value in values:
        weight = weight * decay + 1.0
        value_sum = value_sum * decay + value
    return value_sum / weight


def mutated(mutation):
    snap = copy.deepcopy(BASE)
    mutation(snap)
    return snap


# ---------------------------------------------------------------------------
# League context
# ---------------------------------------------------------------------------


def test_league_context_hand_arithmetic() -> None:
    context = nb.league_context(BASE)
    # All rows poss=100, ortg=drtg=112 -> possession-weighted means 112,
    # mean team-game pace 100; six team-game rows.
    assert context.ortg == pytest.approx(112.0, abs=1e-12)
    assert context.drtg == pytest.approx(112.0, abs=1e-12)
    assert context.pace == pytest.approx(100.0, abs=1e-12)
    # Home rows margins +3, +3, +3 -> HCA exactly 3.
    assert context.hca == pytest.approx(3.0, abs=1e-12)
    # No b2b rows at all -> documented default, unfitted.
    assert context.b2b_penalty == nb.DEFAULT_B2B_PENALTY
    assert context.b2b_fitted is False
    assert context.team_games == 6


def test_b2b_penalty_fit_positive_and_negative_clamp() -> None:
    positive = nb.league_context(B2B_SNAP)
    assert positive.b2b_fitted is True
    # 16 rested rows at 106 pts vs 16 b2b rows at 103 -> +3.0, inside clamp.
    assert positive.b2b_penalty == pytest.approx(3.0, abs=1e-12)
    reversed_fit = nb.league_context(B2B_REV_SNAP)
    # b2b output higher than rested -> raw -3.0 clamped to 0.0.
    assert reversed_fit.b2b_fitted is True
    assert reversed_fit.b2b_penalty == pytest.approx(0.0, abs=1e-12)


# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------


def test_team_ratings_ewma_hand_check_half_life_one() -> None:
    # Half-life 1 game: weights 2**-2, 2**-1, 1 = 0.25 / 0.5 / 1.0.
    ratings = nb.team_ratings(EWMA_SNAP, "AAA", half_life=1.0)
    assert ratings is not None
    assert ratings.ortg == pytest.approx((0.25 * 110 + 0.5 * 120 + 1.0 * 130) / 1.75, abs=1e-12)
    assert ratings.drtg == pytest.approx((0.25 * 105 + 0.5 * 115 + 1.0 * 125) / 1.75, abs=1e-12)
    assert ratings.pace == pytest.approx((0.25 * 98 + 0.5 * 100 + 1.0 * 102) / 1.75, abs=1e-12)
    assert ratings.games == 3
    assert ratings.effective_games == pytest.approx(1.75, abs=1e-12)
    assert ratings.half_life == 1.0
    assert ratings.before_date is None


def test_team_ratings_matches_recursive_reference() -> None:
    ratings = nb.team_ratings(EWMA_SNAP, "AAA", half_life=20.0)
    assert ratings is not None
    assert ratings.ortg == pytest.approx(ewma_ref([110.0, 120.0, 130.0], 20.0), rel=1e-12)
    assert ratings.drtg == pytest.approx(ewma_ref([105.0, 115.0, 125.0], 20.0), rel=1e-12)
    assert ratings.pace == pytest.approx(ewma_ref([98.0, 100.0, 102.0], 20.0), rel=1e-12)


def test_team_ratings_barrier_no_peeking() -> None:
    # Barrier 2026-01-02: only the Jan 1 game is strictly before it.
    first = nb.team_ratings(EWMA_SNAP, "AAA", "2026-01-02", half_life=1.0)
    assert first is not None
    assert first.games == 1
    assert first.ortg == pytest.approx(110.0, abs=1e-12)
    assert first.effective_games == pytest.approx(1.0, abs=1e-12)
    assert first.before_date == "2026-01-02"
    # Barrier before every game -> soft refusal.
    assert nb.team_ratings(EWMA_SNAP, "AAA", "2026-01-01") is None


def test_team_ratings_missing_team_and_default_barrier() -> None:
    assert nb.team_ratings(BASE, "ZZZ") is None
    default = nb.team_ratings(EWMA_SNAP, "AAA")
    explicit = nb.team_ratings(EWMA_SNAP, "AAA", "2026-12-31")
    assert default is not None and explicit is not None
    # before_date=None means "no barrier": same ratings as a far-future one.
    assert (default.ortg, default.drtg, default.pace, default.games) == (
        explicit.ortg, explicit.drtg, explicit.pace, explicit.games
    )


def test_team_ratings_half_life_and_team_validation() -> None:
    for bad in (0.0, -5.0, float("nan")):
        with pytest.raises(ValueError, match="half_life"):
            nb.team_ratings(BASE, "AAA", half_life=bad)
    with pytest.raises(ValueError, match="team"):
        nb.team_ratings(BASE, "")


# ---------------------------------------------------------------------------
# fixture_prediction: hand checks
# ---------------------------------------------------------------------------


def test_fixture_prediction_hand_check_base() -> None:
    prediction = nb.fixture_prediction(BASE, fixture())
    assert prediction is not None
    # League-average teams: pace 100, exp ORtg 112 each -> 112 raw points
    # per side, HCA 3.0 split +1.5/-1.5, no b2b/rest/injury effects.
    assert prediction.pace_estimate == pytest.approx(100.0, abs=1e-12)
    assert prediction.exp_pts_home == pytest.approx(113.5, abs=1e-12)
    assert prediction.exp_pts_away == pytest.approx(110.5, abs=1e-12)
    assert prediction.exp_margin == pytest.approx(3.0, abs=1e-12)
    assert prediction.exp_total == pytest.approx(224.0, abs=1e-12)
    assert prediction.hca == pytest.approx(3.0, abs=1e-12)
    assert prediction.b2b_penalty == pytest.approx(nb.DEFAULT_B2B_PENALTY, abs=1e-12)
    assert prediction.b2b_penalty_fitted is False
    assert prediction.league_ortg == pytest.approx(112.0, abs=1e-12)
    assert prediction.league_pace == pytest.approx(100.0, abs=1e-12)
    # Only two walk-forward residuals -> sigma defaults 12.0 / 15.0;
    # six variants: three at Phi(3/12) and three at Phi(3/(12*1.1)).
    tight, loose = phi(3.0 / 12.0), phi(3.0 / (12.0 * 1.1))
    assert prediction.p_home == pytest.approx((tight + loose) / 2.0, abs=1e-12)
    assert prediction.p_home_range == pytest.approx((loose, tight), abs=1e-12)
    assert prediction.p_home_variant_spread == pytest.approx(tight - loose, abs=1e-12)
    assert prediction.p_away == pytest.approx(1.0 - prediction.p_home, abs=1e-12)


def test_p_home_p_away_complement_and_fifty_fifty() -> None:
    prediction = nb.fixture_prediction(BASE, fixture())
    assert prediction is not None
    assert prediction.p_home + prediction.p_away == pytest.approx(1.0, abs=1e-9)
    for variant in prediction.variants:
        assert variant.p_home + variant.p_away == pytest.approx(1.0, abs=1e-9)
    # Zero HCA and league-average ratings -> margin exactly 0 -> 0.5/0.5.
    flat = nb.fixture_prediction(HCA0_SNAP, fixture())
    assert flat is not None
    assert flat.hca == pytest.approx(0.0, abs=1e-12)
    assert flat.exp_margin == pytest.approx(0.0, abs=1e-12)
    assert flat.p_home == pytest.approx(0.5, abs=1e-12)
    assert flat.p_away == pytest.approx(0.5, abs=1e-12)
    assert flat.p_home_range == (pytest.approx(0.5, abs=1e-12), pytest.approx(0.5, abs=1e-12))


def test_b2b_adjustment_sign_and_both_sides() -> None:
    neither = nb.fixture_prediction(BASE, fixture())
    home_tired = nb.fixture_prediction(BASE, fixture(b2b_home=True))
    away_tired = nb.fixture_prediction(BASE, fixture(b2b_away=True))
    both_tired = nb.fixture_prediction(BASE, fixture(b2b_home=True, b2b_away=True))
    assert neither is not None and home_tired is not None
    assert away_tired is not None and both_tired is not None
    penalty = nb.DEFAULT_B2B_PENALTY
    assert home_tired.exp_margin == pytest.approx(neither.exp_margin - penalty, abs=1e-12)
    assert away_tired.exp_margin == pytest.approx(neither.exp_margin + penalty, abs=1e-12)
    # Both fatigued: margin neutral, total down by 2x penalty.
    assert both_tired.exp_margin == pytest.approx(neither.exp_margin, abs=1e-12)
    assert both_tired.exp_total == pytest.approx(neither.exp_total - 2.0 * penalty, abs=1e-12)
    assert home_tired.p_home < neither.p_home < away_tired.p_home


def test_rest_adjustment_flag() -> None:
    # Default off: rest values present but ignored.
    rested = nb.fixture_prediction(BASE, fixture(rest_home=3, rest_away=0))
    assert rested is not None
    assert rested.exp_margin == pytest.approx(3.0, abs=1e-12)
    # On: 0.25 pts per day of differential -> +0.75 margin, split +-.
    adjusted = nb.fixture_prediction(
        BASE, fixture(rest_home=3, rest_away=0), use_rest_adjustment=True
    )
    assert adjusted is not None
    assert adjusted.exp_margin == pytest.approx(3.75, abs=1e-12)
    assert adjusted.exp_pts_home == pytest.approx(113.875, abs=1e-12)
    assert adjusted.exp_pts_away == pytest.approx(110.125, abs=1e-12)
    assert adjusted.exp_total == pytest.approx(224.0, abs=1e-12)
    # Differential capped at 3 days.
    capped = nb.fixture_prediction(
        BASE, fixture(rest_home=7, rest_away=0), use_rest_adjustment=True
    )
    assert capped is not None
    assert capped.exp_margin == pytest.approx(3.75, abs=1e-12)
    # Unknown rest on either side -> no adjustment even when enabled.
    unknown = nb.fixture_prediction(
        BASE, fixture(rest_home=None, rest_away=0), use_rest_adjustment=True
    )
    assert unknown is not None
    assert unknown.exp_margin == pytest.approx(3.0, abs=1e-12)


# ---------------------------------------------------------------------------
# Sigma treatment
# ---------------------------------------------------------------------------


def test_sigma_defaults_with_few_residuals() -> None:
    prediction = nb.fixture_prediction(BASE, fixture())
    assert prediction is not None
    # 3 paired games -> 2 residuals (< MIN_RESIDUAL_GAMES) -> defaults.
    assert prediction.sigma_margin_residuals == 2
    assert prediction.sigma_total_residuals == 2
    for variant in prediction.variants:
        base_margin = nb.DEFAULT_SIGMA_MARGIN * variant.spec.sigma_scale
        base_total = nb.DEFAULT_SIGMA_TOTAL * variant.spec.sigma_scale
        assert variant.sigma_margin == pytest.approx(base_margin, abs=1e-12)
        assert variant.sigma_total == pytest.approx(base_total, abs=1e-12)


def test_sigma_clamped_with_wide_residuals() -> None:
    prediction = nb.fixture_prediction(SIG_SNAP, fixture())
    assert prediction is not None
    # 12 paired games -> 11 residuals: margins alternate +/-30 (std ~31,
    # clamped to the 13.5 upper bound); totals residual constant +6
    # (std 0, clamped to the 13.0 lower bound).
    assert prediction.sigma_margin_residuals == 11
    assert prediction.sigma_total_residuals == 11
    for variant in prediction.variants:
        assert variant.sigma_margin == pytest.approx(
            13.5 * variant.spec.sigma_scale, abs=1e-12
        )
        assert variant.sigma_total == pytest.approx(
            13.0 * variant.spec.sigma_scale, abs=1e-12
        )
    # The fitted value never leaves the documented clamp bounds.
    lo, hi = nb.SIGMA_MARGIN_CLAMP
    for variant in prediction.variants:
        if variant.spec.sigma_scale == 1.0:
            assert lo - 1e-12 <= variant.sigma_margin <= hi + 1e-12


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------


def test_totals_line_quote_monotonic_and_centered() -> None:
    prediction = nb.fixture_prediction(BASE, fixture())
    assert prediction is not None
    lines = [200.0, 210.0, 220.0, 224.0, 230.0, 240.0, 260.0]
    quotes = [prediction.total_line_prob(line) for line in lines]
    overs = [quote.p_over for quote in quotes]
    unders = [quote.p_under for quote in quotes]
    assert all(overs[i] > overs[i + 1] for i in range(len(overs) - 1))
    assert all(unders[i] < unders[i + 1] for i in range(len(unders) - 1))
    for quote in quotes:
        assert quote.p_over + quote.p_under == pytest.approx(1.0, abs=1e-12)
        assert len(quote.over_variants) == len(nb.VARIANTS)
        assert quote.variant_spread == pytest.approx(
            max(quote.over_variants) - min(quote.over_variants), abs=1e-12
        )
        assert quote.over_range[0] <= quote.p_over <= quote.over_range[1] + 1e-15
    # At the expected total every variant sits exactly at 0.5.
    centered = prediction.total_line_prob(224.0)
    assert centered.p_over == pytest.approx(0.5, abs=1e-12)
    assert centered.p_under == pytest.approx(0.5, abs=1e-12)


def test_total_line_prob_line_validation() -> None:
    prediction = nb.fixture_prediction(BASE, fixture())
    assert prediction is not None
    with pytest.raises(ValueError, match="line"):
        prediction.total_line_prob(float("nan"))
    with pytest.raises(ValueError, match="line"):
        prediction.total_line_prob("220")
    far = prediction.total_line_prob(1e6)
    assert far.p_over == pytest.approx(0.0, abs=1e-12)
    assert far.p_under == pytest.approx(1.0, abs=1e-12)


# ---------------------------------------------------------------------------
# Variant envelope
# ---------------------------------------------------------------------------


def test_variant_envelope_grid_and_spreads() -> None:
    prediction = nb.fixture_prediction(ENV_SNAP, fixture())
    assert prediction is not None
    assert nb.VARIANT_NAMES == (
        "hl10-sig1", "hl10-sig1.1", "hl20-sig1", "hl20-sig1.1", "hl35-sig1", "hl35-sig1.1",
    )
    assert tuple(v.spec.name for v in prediction.variants) == nb.VARIANT_NAMES
    # Varying AAA ortg makes the half-life dimension bite: all rating-side
    # spreads positive, every variant value inside the min/max range.
    assert prediction.exp_margin_variant_spread > 1e-6
    assert prediction.exp_total_variant_spread > 1e-6
    assert prediction.p_home_variant_spread > 1e-6
    margins = [v.exp_margin for v in prediction.variants]
    assert prediction.exp_margin_range == (min(margins), max(margins))
    assert min(margins) <= prediction.exp_margin <= max(margins)
    totals = [v.exp_total for v in prediction.variants]
    assert prediction.exp_total_range == (min(totals), max(totals))
    homes = [v.p_home for v in prediction.variants]
    assert prediction.p_home_range == (min(homes), max(homes))
    assert prediction.p_away_range == (1.0 - max(homes), 1.0 - min(homes))
    # Sigma scale relation inside each half-life pair.
    by_name = {v.spec.name: v for v in prediction.variants}
    for hl in (10.0, 20.0, 35.0):
        tight = by_name[f"hl{hl:g}-sig1"]
        loose = by_name[f"hl{hl:g}-sig1.1"]
        assert loose.sigma_margin == pytest.approx(1.1 * tight.sigma_margin, rel=1e-12)
        assert loose.sigma_total == pytest.approx(1.1 * tight.sigma_total, rel=1e-12)


def test_variant_envelope_degenerate_half_life_dimension() -> None:
    # BASE: identical rating fields every game -> every half-life gives
    # the same EWMAs, so the rating-side spread collapses to exactly
    # zero while the sigma-scale dimension still spreads p_home. The
    # envelope only quantifies the dimensions actually swept.
    prediction = nb.fixture_prediction(BASE, fixture())
    assert prediction is not None
    assert prediction.exp_margin_variant_spread == pytest.approx(0.0, abs=1e-12)
    assert prediction.exp_total_variant_spread == pytest.approx(0.0, abs=1e-12)
    assert prediction.p_home_variant_spread > 0.0


# ---------------------------------------------------------------------------
# Injury stub
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("before_date", [None, "2026-01-04"])
def test_injury_stub_multipliers(before_date: str | None) -> None:
    plain = nb.fixture_prediction(BASE, fixture(), before_date=before_date)
    assert plain is not None
    identity = nb.fixture_prediction(
        BASE,
        fixture(),
        before_date=before_date,
        injury_multipliers={"AAA": 1.0, "BBB": 1.0},
    )
    assert identity is not None
    assert identity.exp_pts_home == pytest.approx(plain.exp_pts_home, abs=1e-12)
    assert identity.exp_pts_away == pytest.approx(plain.exp_pts_away, abs=1e-12)
    adjusted = nb.fixture_prediction(
        BASE,
        fixture(),
        before_date=before_date,
        injury_multipliers={"AAA": 1.1, "BBB": 0.9},
    )
    assert adjusted is not None
    assert adjusted.exp_pts_home == pytest.approx(113.5 * 1.1, abs=1e-12)
    assert adjusted.exp_pts_away == pytest.approx(110.5 * 0.9, abs=1e-12)
    assert adjusted.exp_margin == pytest.approx(113.5 * 1.1 - 110.5 * 0.9, abs=1e-12)


def test_injury_stub_rejects_invalid() -> None:
    for bad in (float("nan"), 0.0, -1.0):
        with pytest.raises(ValueError, match="injury_multipliers"):
            nb.fixture_prediction(BASE, fixture(), injury_multipliers={"AAA": bad})
    with pytest.raises(ValueError, match="injury_multipliers"):
        nb.fixture_prediction(BASE, fixture(), injury_multipliers=("AAA", 1.0))


# ---------------------------------------------------------------------------
# Purity / determinism / refusals / validation
# ---------------------------------------------------------------------------


def test_pure_and_deterministic() -> None:
    original = copy.deepcopy(BASE)
    first = nb.fixture_prediction(BASE, fixture())
    second = nb.fixture_prediction(BASE, fixture())
    assert first is not None and second is not None
    assert first == second
    assert BASE == original


def test_refusal_missing_team() -> None:
    missing = fixture(home="ZZZ")
    assert nb.fixture_prediction(BASE, missing) is None
    reason = nb.refusal_reason(BASE, missing)
    assert reason is not None and "ZZZ" in reason and "missing" in reason
    assert nb.refusal_reason(BASE, fixture()) is None


def test_refusal_no_prior_games() -> None:
    early = fixture()
    assert nb.fixture_prediction(BASE, early, before_date="2026-01-01") is None
    reason = nb.refusal_reason(BASE, early, before_date="2026-01-01")
    assert reason is not None and "no games strictly before" in reason and "AAA" in reason


def test_fixture_prediction_barrier_changes_prediction() -> None:
    full = nb.fixture_prediction(EWMA_SNAP, fixture())
    partial = nb.fixture_prediction(EWMA_SNAP, fixture(), before_date="2026-01-03")
    assert full is not None and partial is not None
    # The Jan 3 game (AAA ortg 130) must move the ratings: no peeking
    # means the barrier genuinely excludes it.
    assert full.exp_margin != pytest.approx(partial.exp_margin, abs=1e-9)


@pytest.mark.parametrize("late_date", ["2026-02-17", "2026-02-18"])
@pytest.mark.parametrize("use_rest_adjustment", [False, True])
def test_fixture_prediction_cutoff_matches_truncated_snapshot(
    late_date: str, use_rest_adjustment: bool
) -> None:
    before_date = "2026-02-17"
    target = fixture(rest_home=1, rest_away=4, b2b_home=True)
    extreme = {
        "poss": 135,
        "ortg_home": 150,
        "drtg_away": 150,
        "ortg_away": 60,
        "drtg_home": 60,
        "b2b_home": True,
        "b2b_away": True,
    }
    full = build_snapshot(
        [
            *_b2b_games(103, 106),
            (late_date, "AAA", "BBB", 203, 81, extreme),
            (late_date, "CCC", "DDD", 200, 80, extreme),
        ],
        injuries={"AAA": [{"player": "Example", "status": "Out", "date": late_date}]},
    )
    expected = nb.fixture_prediction(
        B2B_SNAP, target, use_rest_adjustment=use_rest_adjustment
    )
    assert expected is not None
    assert expected.b2b_penalty == 3.0
    assert expected.b2b_penalty_fitted
    assert expected.sigma_margin_residuals == expected.sigma_total_residuals == 15

    truncated = nb.fixture_prediction(
        B2B_SNAP,
        target,
        before_date=before_date,
        use_rest_adjustment=use_rest_adjustment,
    )
    historical = nb.fixture_prediction(
        full,
        target,
        before_date=before_date,
        use_rest_adjustment=use_rest_adjustment,
    )
    assert truncated == expected
    assert historical == expected
    assert historical.total_line_prob(221.5) == expected.total_line_prob(221.5)

    forward = nb.fixture_prediction(full, target, use_rest_adjustment=use_rest_adjustment)
    assert forward is not None
    assert forward.league_pace > expected.league_pace
    assert forward.b2b_penalty == 0.0
    assert forward.sigma_margin_residuals == expected.sigma_margin_residuals + 1


def test_game_row_validation_errors() -> None:
    first_game = lambda snap: snap["teams"]["AAA"]["games"][0]
    cases = [
        ("ortg nan", lambda g: g.update(ortg=float("nan")), "ortg"),
        ("poss zero", lambda g: g.update(poss=0), "poss"),
        ("poss negative", lambda g: g.update(poss=-1), "poss"),
        ("home string", lambda g: g.update(home="true"), "home"),
        ("b2b string", lambda g: g.update(b2b="no"), "b2b"),
        ("bad date", lambda g: g.update(date="2026-02-30"), "date"),
        ("rest float", lambda g: g.update(rest_days=1.5), "rest_days"),
        ("pts negative", lambda g: g.update(pts=-5), "pts"),
        ("opp_pts negative", lambda g: g.update(opp_pts=-3), "opp_pts"),
        ("opponent empty", lambda g: g.update(opponent=""), "opponent"),
        ("field missing", lambda g: g.pop("ortg"), "missing"),
    ]
    for label, mutation, needle in cases:
        snap = mutated(lambda s: mutation(first_game(s)))
        with pytest.raises(ValueError, match=needle):
            nb.fixture_prediction(snap, fixture(), before_date=None)
        # team_ratings validates the same rows on its own path.
        if label != "field missing":
            with pytest.raises(ValueError):
                nb.team_ratings(snap, "AAA")


@pytest.mark.parametrize("before_date", [None, "2026-01-03"])
def test_paired_game_integrity_errors(before_date: str | None) -> None:
    def duplicate_home_row(snap):
        games = snap["teams"]["AAA"]["games"]
        games.append(dict(games[-1]))

    with pytest.raises(ValueError, match="duplicate"):
        nb.fixture_prediction(
            mutated(duplicate_home_row), fixture(), before_date=before_date
        )

    def inconsistent_scores(snap):
        snap["teams"]["BBB"]["games"][-1]["pts"] = 110  # AAA row says opp 109

    with pytest.raises(ValueError, match="inconsistent"):
        nb.fixture_prediction(
            mutated(inconsistent_scores), fixture(), before_date=before_date
        )


def test_snapshot_structure_validation() -> None:
    with pytest.raises(ValueError, match="snapshot"):
        nb.fixture_prediction(None, fixture())
    with pytest.raises(ValueError, match="teams"):
        nb.fixture_prediction({}, fixture())
    missing_season = dict(BASE)
    del missing_season["season"]
    with pytest.raises(ValueError, match="season"):
        nb.fixture_prediction(missing_season, fixture())
    bad_injuries = dict(BASE)
    bad_injuries["injuries"] = "nope"
    with pytest.raises(ValueError, match="injuries"):
        nb.fixture_prediction(bad_injuries, fixture())
    with pytest.raises(ValueError, match="no games"):
        empty_games = build_snapshot([("2026-01-01", "AAA", "BBB", 100, 100)])
        empty_games["teams"] = {
            "AAA": {"display": "AAA", "games": []},
            "BBB": {"display": "BBB", "games": []},
        }
        nb.league_context(empty_games)


def test_fixture_validation_errors() -> None:
    with pytest.raises(ValueError, match="differ"):
        nb.fixture_prediction(BASE, fixture(home="AAA", away="AAA"))
    with pytest.raises(ValueError, match="rest_home"):
        nb.fixture_prediction(BASE, fixture(rest_home=-1))
    with pytest.raises(ValueError, match="rest_away"):
        nb.fixture_prediction(BASE, fixture(rest_away=1.5))
    with pytest.raises(ValueError, match="b2b_home"):
        nb.fixture_prediction(BASE, fixture(b2b_home=1))
    with pytest.raises(TypeError, match="NbaFixture"):
        nb.fixture_prediction(BASE, ("AAA", "BBB", None, None, False, False))
    with pytest.raises(ValueError, match="use_rest_adjustment"):
        nb.fixture_prediction(BASE, fixture(), use_rest_adjustment="yes")
