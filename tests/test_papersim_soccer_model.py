"""WP-P1 W1 tests: 10-variant xG-Poisson soccer model.

Three layers:

- Unit invariants on synthetic rates: the Dixon-Coles tau touches ONLY
  the 0-0 / 0-1 / 1-0 / 1-1 cells, preserves row and column marginals
  and total mass (the four corrections cancel exactly for independent
  Poisson cells), the sweep set matches the frozen lineage list, and the
  market helpers follow the lineage push semantics. The production
  ``score_grid(..., rho=...)`` is additionally compared cell-by-cell
  against a from-first-principles recomputation (FX1, codex finding 10).
- Regression anchors: recompute the four frozen round-23 EPL cases from
  ``tests/fixtures/papersim/regression_r23_soccer.json`` and assert the
  medians, ranges and all ten per-variant quotes within 1e-4, plus the
  banned cross-league path (HOF-LYO) asserting ``None``.
- Candidate-builder gates (FX1, codex finding 1): the public
  snapshot-driven ``candidate_from_variants`` builder carries the TRUE
  per-variant quotes into the fair range — BOU-LEE O2.5 must be
  REJECTED by the frozen gates with the true 6.09c spread reproduced,
  while the pair-level ``over_under_quote`` stub (documented NOT
  gate-safe) collapses the same sweep to ten identical quotes.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from polymarket_alpha_lab.papersim import gates, soccer_model, xg

FIXTURES = Path(__file__).parent / "fixtures" / "papersim"

# EPL 2025-26 prior-season aggregates frozen at full float precision from
# /home/ubuntu/paper-sim/round23/raw/xg_epl_23.json key "EPL_2025" (380
# isResult dates, 38-row team histories; computed once during WP-P1 W1).
# The P0 excerpt froze only the eight EPL_2026 team rows, so the
# regression snapshot rebuilds the prior-season block from these
# aggregates: the single synthetic date row reproduces the league base
# rates exactly and the single-row histories reproduce each ever-present
# team's 2025 mean xG / xGA exactly. Promoted sides (Hull, Ipswich) are
# deliberately absent, exercising the league-average fallback exactly as
# the r23 model did for teams missing from the prior season.
EPL_2025_HOME_RATE = 1.6946258894736843
EPL_2025_AWAY_RATE = 1.3643311084210525
EPL_2025_TEAM_RATES = {
    "Bournemouth": (1.758804, 1.4939418157894737),
    "Brentford": (1.7435966842105264, 1.5555292368421054),
    "Chelsea": (1.9003278947368418, 1.5294069210526315),
    "Leeds": (1.5729118421052632, 1.4822607894736841),
    "Manchester United": (1.8996939473684211, 1.3231953684210527),
    "Nottingham Forest": (1.287795947368421, 1.7036741052631579),
}

LH, LA = 1.894, 1.364  # frozen BRE-FOR median rates
DC_PAIRS = ((0, 0), (0, 1), (1, 0), (1, 1))


def _raw_grids() -> tuple[list[list[float]], list[list[float]]]:
    """Unnormalized rho0 / DC grids built from the public PMF and tau."""
    plain = [
        [soccer_model.poisson_pmf(LH, i) * soccer_model.poisson_pmf(LA, j) for j in range(11)]
        for i in range(11)
    ]
    tau_00, tau_01, tau_10, tau_11 = soccer_model.dixon_coles_tau(LH, LA, soccer_model.RHO_DC)
    adjusted = [row[:] for row in plain]
    adjusted[0][0] *= tau_00
    adjusted[0][1] *= tau_01
    adjusted[1][0] *= tau_10
    adjusted[1][1] *= tau_11
    return plain, adjusted


def test_dixon_coles_tau_factors_and_floor() -> None:
    # (1 - lh*la*rho, 1 + lh*rho, 1 + la*rho, 1 - rho) at rho=-0.1.
    factors = soccer_model.dixon_coles_tau(LH, LA, soccer_model.RHO_DC)
    assert factors == pytest.approx(
        (1 + 0.1 * LH * LA, 1 - 0.1 * LH, 1 - 0.1 * LA, 1.1), rel=1e-12
    )
    # Lineage safety clamp: factors never drop below DC_FLOOR.
    assert soccer_model.dixon_coles_tau(5.0, 5.0, 0.9)[0] == soccer_model.DC_FLOOR
    assert soccer_model.dixon_coles_tau(1.0, 1.0, 0.5) == pytest.approx((0.5, 1.5, 1.5, 0.5))


def test_dc_changes_only_the_four_low_score_cells() -> None:
    plain, adjusted = _raw_grids()
    for i in range(11):
        for j in range(11):
            if (i, j) in DC_PAIRS:
                assert adjusted[i][j] != plain[i][j]
            else:
                assert adjusted[i][j] == plain[i][j]


def test_production_score_grid_matches_first_principles() -> None:
    """Every PRODUCTION grid cell vs an independent recomputation.

    FX1 (codex finding 10): ``_raw_grids()`` above adjusts raw cells by
    hand and never calls production, so a production change touching
    extra cells would have passed unnoticed. Here the expected cells are
    recomputed from first principles — plain ``math`` exp/factorial
    Poisson masses, the tau formulas written out literally (floor 0.01,
    non-binding at these rates), one shared normalization — and compared
    cell-by-cell with ``soccer_model.score_grid`` output.
    """
    rho = soccer_model.RHO_DC
    floor = 0.01  # lineage DC_FLOOR, spelled out to stay independent
    raw = {
        (i, j): (
            math.exp(-LH) * LH**i / math.factorial(i)
        ) * (
            math.exp(-LA) * LA**j / math.factorial(j)
        )
        for i in range(11)
        for j in range(11)
    }
    tau = {
        (0, 0): max(1.0 - LH * LA * rho, floor),
        (0, 1): max(1.0 + LH * rho, floor),
        (1, 0): max(1.0 + LA * rho, floor),
        (1, 1): max(1.0 - rho, floor),
    }
    total = sum(p * tau.get(cell, 1.0) for cell, p in raw.items())

    grid = soccer_model.score_grid(LH, LA, rho=rho)
    plain_grid = soccer_model.score_grid(LH, LA)
    plain_total = sum(raw.values())
    for i in range(11):
        for j in range(11):
            expected = raw[(i, j)] * tau.get((i, j), 1.0) / total
            assert grid[i][j] == pytest.approx(expected, rel=1e-12, abs=1e-15), (
                f"DC grid cell ({i},{j}) drifted from first principles"
            )
            assert plain_grid[i][j] == pytest.approx(
                raw[(i, j)] / plain_total, rel=1e-12, abs=1e-15
            ), f"plain grid cell ({i},{j}) drifted from first principles"


def test_dc_preserves_row_and_column_marginals_and_mass() -> None:
    plain, adjusted = _raw_grids()
    for i in range(11):
        assert sum(adjusted[i]) == pytest.approx(sum(plain[i]), rel=1e-12, abs=1e-15)
    for j in range(11):
        assert sum(adjusted[i][j] for i in range(11)) == pytest.approx(
            sum(plain[i][j] for i in range(11)), rel=1e-12, abs=1e-15
        )
    # The four cell corrections sum to exactly zero.
    correction = sum(
        adjusted[i][j] - plain[i][j] for i, j in DC_PAIRS
    )
    assert correction == pytest.approx(0.0, abs=1e-15)
    assert sum(sum(row) for row in adjusted) == pytest.approx(
        sum(sum(row) for row in plain), rel=1e-12, abs=1e-15
    )


def test_score_grid_normalizes() -> None:
    plain, _ = _raw_grids()
    total = sum(sum(row) for row in plain)
    grid = soccer_model.score_grid(LH, LA)
    assert len(grid) == 11 and all(len(row) == 11 for row in grid)
    assert sum(sum(row) for row in grid) == pytest.approx(1.0)
    assert grid[0][0] == pytest.approx(plain[0][0] / total)
    dc_grid = soccer_model.score_grid(LH, LA, rho=soccer_model.RHO_DC)
    assert sum(sum(row) for row in dc_grid) == pytest.approx(1.0)


def test_variant_outputs_distributions_and_relations() -> None:
    outputs = soccer_model.variant_outputs(LH, LA)
    assert outputs.home_win + outputs.draw + outputs.away_win == pytest.approx(1.0)
    assert sum(outputs.totals) == pytest.approx(1.0)
    assert sum(outputs.home_goals) == pytest.approx(1.0)
    assert sum(outputs.away_goals) == pytest.approx(1.0)
    assert sum(outputs.margins) == pytest.approx(1.0)
    assert len(outputs.totals) == 21 and len(outputs.margins) == 21
    assert len(outputs.home_goals) == 11 and len(outputs.away_goals) == 11
    grid = soccer_model.score_grid(LH, LA)
    for i in range(11):
        assert outputs.home_goals[i] == pytest.approx(sum(grid[i]))
        assert outputs.away_goals[i] == pytest.approx(sum(grid[j][i] for j in range(11)))
    assert outputs.margins[10] == pytest.approx(outputs.draw)  # margin 0
    # P(0-0) cell against the closed form (the 11x11 truncation mass is
    # ~5e-6 relative at these rates, hence the loose tolerance).
    assert outputs.totals[0] == pytest.approx(math.exp(-(LH + LA)), rel=1e-4)
    # BTTS = 1 - P(home 0) - P(away 0) + P(0-0).
    assert outputs.btts == pytest.approx(
        1.0 - outputs.home_goals[0] - outputs.away_goals[0] + outputs.totals[0]
    )


def test_dc_preserves_team_totals_and_high_ou_lines() -> None:
    plain_outputs = soccer_model.variant_outputs(LH, LA)
    dc_outputs = soccer_model.variant_outputs(LH, LA, rho=soccer_model.RHO_DC)
    # Marginal invariance: per-team goal distributions unchanged.
    assert dc_outputs.home_goals == pytest.approx(plain_outputs.home_goals, abs=1e-12)
    assert dc_outputs.away_goals == pytest.approx(plain_outputs.away_goals, abs=1e-12)
    # Every adjusted cell has total <= 2, so O/U lines >= 2.0 are invariant...
    for line in (2.0, 2.5, 3.5):
        assert soccer_model.over_probability(dc_outputs, line) == pytest.approx(
            soccer_model.over_probability(plain_outputs, line), abs=1e-12
        )
    # ...while low lines do move (0-0 / 1-1 mass is redistributed).
    assert soccer_model.over_probability(dc_outputs, 1.5) != pytest.approx(
        soccer_model.over_probability(plain_outputs, 1.5), abs=1e-6
    )
    assert dc_outputs.totals[0] > plain_outputs.totals[0]  # rho=-0.1 boosts 0-0


def test_over_probability_push_semantics() -> None:
    outputs = soccer_model.variant_outputs(LH, LA)
    assert soccer_model.over_probability(outputs, 2.5) == pytest.approx(
        1.0 - sum(outputs.totals[:3])
    )
    # Integer lines exclude the push: O3.0 and O3.5 both need 4+ goals.
    assert soccer_model.over_probability(outputs, 3.0) == pytest.approx(
        soccer_model.over_probability(outputs, 3.5)
    )
    assert soccer_model.over_probability(outputs, 0.5) == pytest.approx(1.0 - outputs.totals[0])


def test_team_total_over_and_side_validation() -> None:
    outputs = soccer_model.variant_outputs(LH, LA)
    assert soccer_model.team_total_over(outputs, "home", 0.5) == pytest.approx(
        1.0 - outputs.home_goals[0]
    )
    assert soccer_model.team_total_over(outputs, "away", 1.5) == pytest.approx(
        1.0 - outputs.away_goals[0] - outputs.away_goals[1]
    )
    with pytest.raises(ValueError):
        soccer_model.team_total_over(outputs, "bogus", 1.5)
    with pytest.raises(ValueError):
        soccer_model.cover_probability(outputs, "bogus", -1.5)


def test_cover_probability_margins() -> None:
    outputs = soccer_model.variant_outputs(LH, LA)
    # Home -1.5: needs margin >= 2 (indexes 12.. of the margin table).
    assert soccer_model.cover_probability(outputs, "home", -1.5) == pytest.approx(
        sum(outputs.margins[12:])
    )
    # Away -1.5: needs margin <= -2 (indexes 0..8).
    assert soccer_model.cover_probability(outputs, "away", -1.5) == pytest.approx(
        sum(outputs.margins[:9])
    )
    # Home +0 (moneyline equivalence): margin >= 1.
    assert soccer_model.cover_probability(outputs, "home", 0.0) == pytest.approx(outputs.home_win)
    assert soccer_model.cover_probability(outputs, "away", 0.0) == pytest.approx(outputs.away_win)


def test_median_lineage_semantics() -> None:
    assert soccer_model.median([3.0, 1.0, 2.0]) == 2.0
    assert soccer_model.median([1.0, 2.0, 3.0, 4.0]) == 2.5
    values = [float(i) for i in range(10)]
    assert soccer_model.median(values) == 4.5  # mean of the middle pair
    with pytest.raises(ValueError):
        soccer_model.median([])


def test_sweep_set_matches_frozen_lineage() -> None:
    sweeps = soccer_model.SWEEPS
    assert len(sweeps) == 10
    assert soccer_model.SWEEP_NAMES == (
        "blend w=.5 p=8 rho0",
        "blend w=.5 p=8 DC",
        "blend w=1.0 p=8",
        "blend w=.25 p=8",
        "blend w=.5 p=16",
        "blend w=.5 p=4",
        "current-only",
        "last-only",
        "cross-shrink .8",
        "cross-shrink 1.1",
    )
    assert [s.rho for s in sweeps].count(0.0) == 9
    assert sweeps[1].rho == soccer_model.RHO_DC == -0.1
    assert [(s.last_weight, s.prior_games) for s in sweeps[:6]] == [
        (0.5, 8.0),
        (0.5, 8.0),
        (1.0, 8.0),
        (0.25, 8.0),
        (0.5, 16.0),
        (0.5, 4.0),
    ]
    assert [s.mode for s in sweeps] == ["blend"] * 6 + ["cur", "last"] + ["blend"] * 2
    assert [s.shrink for s in sweeps] == [1.0] * 8 + [0.8, 1.1]


def _mini_snapshot() -> dict[str, object]:
    return {
        "EPL_2025": {
            "dates": [{"isResult": True, "xG": {"h": 1.5, "a": 1.5}}],
            "teams": {
                "1": {"title": "Alpha", "history": [{"xG": 1.5, "xGA": 1.5}]},
                "2": {"title": "Beta", "history": [{"xG": 2.0, "xGA": 1.0}]},
            },
        },
        "EPL_2026": {
            "dates": [],
            "teams": {
                "1": {
                    "title": "Alpha",
                    "history": [{"xG": 2.0, "xGA": 1.0}, {"xG": 1.0, "xGA": 2.0}],
                },
                "2": {
                    "title": "Beta",
                    "history": [{"xG": 3.0, "xGA": 0.0}, {"xG": 1.0, "xGA": 2.0}],
                },
            },
        },
        "bundesliga_2025": {
            "dates": [{"isResult": True, "xG": {"h": 1.7, "a": 1.4}}],
            "teams": {"3": {"title": "TSG 1899 Hoffenheim", "history": [{"xG": 1.5, "xGA": 1.5}]}},
        },
        "bundesliga_2026": {
            "dates": [],
            "teams": {"3": {"title": "TSG 1899 Hoffenheim", "history": [{"xG": 1.5, "xGA": 1.5}]}},
        },
        "ligue_1_2025": {
            "dates": [{"isResult": True, "xG": {"h": 1.6, "a": 1.3}}],
            "teams": {"4": {"title": "Olympique Lyonnais", "history": [{"xG": 1.4, "xGA": 1.4}]}},
        },
        "ligue_1_2026": {
            "dates": [],
            "teams": {"4": {"title": "Olympique Lyonnais", "history": [{"xG": 1.4, "xGA": 1.4}]}},
        },
    }


def test_fixture_variants_evaluate_all_ten_in_order() -> None:
    variants = soccer_model.fixture_variants(_mini_snapshot(), "Alpha", "EPL", "Beta", "EPL")
    assert variants is not None
    assert [v.spec.name for v in variants] == list(soccer_model.SWEEP_NAMES)
    # Variant 0 (blend w=.5 p=8): lh = 1.5 * 1 * (22/29), la = 1.5 * (36/29).
    assert variants[0].lambdas == pytest.approx((33 / 29, 54 / 29))
    # Variant 6 (current-only): lh = 1.5 * 1 * (2/3), la = 1.5 * (4/3) * 1.
    assert variants[6].lambdas == pytest.approx((1.0, 2.0))
    # DC variant differs from rho0 only through the grid, not the rates.
    assert variants[1].lambdas == variants[0].lambdas
    # Shrink variants scale the multipliers by their exponent.
    blend_att = 1.5  # league average baseline
    ratio_home = variants[0].lambdas[0] / blend_att
    assert variants[8].lambdas[0] == pytest.approx(blend_att * ratio_home ** 0.8)
    assert variants[9].lambdas[0] == pytest.approx(blend_att * ratio_home ** 1.1)
    # Outputs present for every variant.
    assert all(v.outputs.home_win + v.outputs.draw + v.outputs.away_win == pytest.approx(1.0) for v in variants)


def test_match_lambdas_returns_per_side_median() -> None:
    snapshot = _mini_snapshot()
    lambdas = soccer_model.match_lambdas(snapshot, "Alpha", "EPL", "Beta", "EPL")
    variants = soccer_model.fixture_variants(snapshot, "Alpha", "EPL", "Beta", "EPL")
    assert variants is not None and lambdas is not None
    assert lambdas[0] == soccer_model.median([v.lambdas[0] for v in variants])
    assert lambdas[1] == soccer_model.median([v.lambdas[1] for v in variants])


def test_match_lambdas_unresolvable_inputs_return_none() -> None:
    snapshot = _mini_snapshot()
    assert soccer_model.match_lambdas(snapshot, "Alpha", "Serie_A", "Beta", "Serie_A") is None
    assert soccer_model.match_lambdas(snapshot, "Alpha", "EPL", "Ghost", "EPL") is None
    assert soccer_model.fixture_variants(snapshot, "Alpha", "EPL", "Ghost", "EPL") is None


def test_fixture_variants_early_season_returning_team() -> None:
    """Codex review 2026-11-07 finding 6 at the sweep level.

    A roster team with prior-season rows but ZERO current-season rows
    (early-season returning side) keeps its prior evidence in the blend
    and last-only variants instead of silently collapsing to league
    average; the current-only variant has no data for it and keeps the
    documented league-average (1.0, 1.0) fallback.
    """
    snapshot = _mini_snapshot()
    snapshot["EPL_2025"]["teams"]["5"] = {"title": "Returning", "history": [{"xG": 3.0, "xGA": 0.75}]}  # type: ignore[index]
    snapshot["EPL_2026"]["teams"]["5"] = {"title": "Returning", "history": []}  # type: ignore[index]

    variants = soccer_model.fixture_variants(snapshot, "Returning", "EPL", "Beta", "EPL")
    assert variants is not None
    lambdas = {v.spec.name: v.lambdas for v in variants}
    # last-only: att = 3.0/1.5 = 2.0, def_Beta(last) = 1.0/1.5;
    # att_Beta(last) = 2.0/1.5, def = 0.75/1.5 = 0.5.
    assert lambdas["last-only"] == pytest.approx((2.0, 1.0))
    # current-only: Returning falls back to (1.0, 1.0); Beta's current
    # multipliers are (2.0/1.5, 1.0/1.5).
    assert lambdas["current-only"] == pytest.approx((1.0, 2.0))
    # The variants that can see the prior evidence lift the returning
    # side above its no-data baseline; the median pair moves with them.
    assert lambdas["last-only"][0] > lambdas["current-only"][0]
    assert lambdas["blend w=.5 p=8 rho0"][0] > lambdas["current-only"][0]
    median = soccer_model.match_lambdas(snapshot, "Returning", "EPL", "Beta", "EPL")
    assert median is not None
    assert median[0] > 1.0  # above the pure league-average baseline


def test_banned_cross_league_path_returns_none() -> None:
    data = json.loads((FIXTURES / "regression_banned_path.json").read_text(encoding="utf-8"))
    case = data["case"]
    snapshot = _mini_snapshot()
    home = case["home"]
    away = case["away"]
    assert soccer_model.match_lambdas(
        snapshot, home["team"], home["league"], away["team"], away["league"]
    ) is None
    assert soccer_model.fixture_variants(
        snapshot, home["team"], home["league"], away["team"], away["league"]
    ) is None
    assert case["expected_match_lambdas"] is None


def _r23_snapshot_bytes() -> bytes:
    fixture = json.loads((FIXTURES / "regression_r23_soccer.json").read_text(encoding="utf-8"))
    excerpt = fixture["xg_snapshot_excerpt"]
    prior_teams = {
        team_id: {
            "id": team_id,
            "title": row["title"],
            "history": [
                {"xG": EPL_2025_TEAM_RATES[row["title"]][0], "xGA": EPL_2025_TEAM_RATES[row["title"]][1]}
            ],
        }
        for team_id, row in excerpt["teams"].items()
        if row["title"] in EPL_2025_TEAM_RATES
    }
    snapshot = {
        "EPL_2025": {
            "dates": [{"isResult": True, "xG": {"h": EPL_2025_HOME_RATE, "a": EPL_2025_AWAY_RATE}}],
            "teams": prior_teams,
        },
        "EPL_2026": {"dates": [], "teams": excerpt["teams"]},
    }
    return json.dumps(snapshot).encode("utf-8")


def _load_r23_snapshot(tmp_path: Path) -> dict[str, object]:
    path = tmp_path / "r23_snapshot.json"
    path.write_bytes(_r23_snapshot_bytes())
    return xg.load_xg_snapshot(str(path))


R23_CASES = [
    ("BRE-FOR_O2.5", "Brentford", "Nottingham Forest", 2.5),
    ("HUL-IPS_O2.5", "Hull", "Ipswich", 2.5),
    ("CHE-MUN_O3.5", "Chelsea", "Manchester United", 3.5),
    ("BOU-LEE_O2.5", "Bournemouth", "Leeds", 2.5),
]


@pytest.mark.parametrize("case_id,home,away,line", R23_CASES)
def test_r23_regression_anchor(
    case_id: str, home: str, away: str, line: float, tmp_path: Path
) -> None:
    fixture = json.loads((FIXTURES / "regression_r23_soccer.json").read_text(encoding="utf-8"))
    case = fixture["cases"][case_id]
    snapshot = _load_r23_snapshot(tmp_path)

    variants = soccer_model.fixture_variants(snapshot, home, "EPL", away, "EPL")
    assert variants is not None
    assert len(variants) == 10
    quotes = [soccer_model.over_probability(v.outputs, line) for v in variants]

    # All ten per-variant quotes, in frozen sweep order.
    for got, want in zip(quotes, case["qs_variants"]):
        assert abs(got - want) <= 1e-4, f"{case_id} variant quote drifted"
    # Median / range aggregation.
    assert abs(soccer_model.median(quotes) - case["model_median"]) <= 1e-4
    assert abs(min(quotes) - case["model_range"][0]) <= 1e-4
    assert abs(max(quotes) - case["model_range"][1]) <= 1e-4

    # Median lambdas (fixture rows round to 3 decimals).
    lambdas = soccer_model.match_lambdas(snapshot, home, "EPL", away, "EPL")
    assert lambdas is not None
    assert abs(lambdas[0] - case["lambda_home"]) <= 5e-4
    assert abs(lambdas[1] - case["lambda_away"]) <= 5e-4
    assert abs(lambdas[0] + lambdas[1] - case["lambda_total"]) <= 1e-2


@pytest.mark.parametrize(
    "case_id,expected_median,expected_range",
    [
        ("BOU-LEE_O2.5", 0.6096, (0.5714, 0.6323)),
        ("BRE-FOR_O2.5", 0.6342, (0.6235, 0.6446)),
        ("HUL-IPS_O2.5", 0.6130, (0.5899, 0.6488)),
        ("CHE-MUN_O3.5", 0.4514, (0.4306, 0.4668)),
    ],
)
def test_r23_regression_anchor_literals(
    case_id: str,
    expected_median: float,
    expected_range: tuple[float, float],
    tmp_path: Path,
) -> None:
    """Anchor literals from the W1 brief, independent of fixture parsing."""
    home, away, line = next(
        (h, a, ln) for cid, h, a, ln in R23_CASES if cid == case_id
    )
    snapshot = _load_r23_snapshot(tmp_path)
    variants = soccer_model.fixture_variants(snapshot, home, "EPL", away, "EPL")
    assert variants is not None
    quotes = [soccer_model.over_probability(v.outputs, line) for v in variants]
    assert soccer_model.median(quotes) == pytest.approx(expected_median, abs=1e-4)
    assert min(quotes) == pytest.approx(expected_range[0], abs=1e-4)
    assert max(quotes) == pytest.approx(expected_range[1], abs=1e-4)


def test_over_under_quote_builds_candidate(tmp_path: Path) -> None:
    """Frozen pair-level stub contract, plus the TRUE spread via the
    public snapshot-driven builder (FX1: this test previously pinned the
    collapsed spread as if it were the candidate's uncertainty; it now
    pins the true BRE-FOR range 0.6235-0.6446 through the gate-safe
    path, keeping the stub collapse only as documented hazard behavior).
    """
    candidate = soccer_model.over_under_quote(
        (1.894, 1.364),
        2.5,
        market="Brentford FC vs. Nottingham Forest FC",
        market_id="5856164",
        entry_ask_vwap250=0.56,
    )
    assert isinstance(candidate, soccer_model.SoccerCandidate)
    assert candidate.match == "Brentford FC vs. Nottingham Forest FC"
    assert candidate.question == "Brentford FC vs. Nottingham Forest FC: O/U 2.5"
    assert candidate.kind == "total"
    assert candidate.side == 0
    assert candidate.market_id == "5856164"
    assert candidate.entry_ask_vwap250 == 0.56
    assert (candidate.lambda_home, candidate.lambda_away) == (1.894, 1.364)
    assert len(candidate.qs_variants) == 10
    assert candidate.model_median == soccer_model.median(candidate.qs_variants)
    assert candidate.model_range == (min(candidate.qs_variants), max(candidate.qs_variants))
    # Pair-level stub hazard (documented, NOT gate-safe): for O/U >= 2.0
    # the ten slots coincide — DC-neutral, rating dims not re-derivable
    # from a bare pair. The gateable fair range is pinned below instead.
    assert max(candidate.qs_variants) == pytest.approx(min(candidate.qs_variants), abs=1e-12)
    low_line = soccer_model.over_under_quote(
        (1.894, 1.364),
        1.5,
        market="m",
        market_id="1",
        entry_ask_vwap250=0.9,
    )
    assert low_line.qs_variants[1] != low_line.qs_variants[0]

    # FX1 (codex finding 1): the same BRE-FOR market through the public
    # snapshot-driven builder carries the TRUE per-variant quotes.
    snapshot = _load_r23_snapshot(tmp_path)
    built = soccer_model.candidate_from_variants(
        snapshot,
        "Brentford",
        "EPL",
        "Nottingham Forest",
        "EPL",
        2.5,
        "Brentford FC vs. Nottingham Forest FC",
        "5856164",
        0.56,
    )
    assert built is not None
    assert built.question == candidate.question
    assert built.model_range == pytest.approx((0.6235, 0.6446), abs=1e-4)
    assert max(built.qs_variants) - min(built.qs_variants) == pytest.approx(0.0211, abs=1e-4)
    # The builder's spread is genuinely wider than the stub's collapse.
    assert min(built.qs_variants) < max(built.qs_variants)


def test_candidate_from_variants_mini_snapshot() -> None:
    """Public builder field contract and consistency with fixture_variants."""
    snapshot = _mini_snapshot()
    variants = soccer_model.fixture_variants(snapshot, "Alpha", "EPL", "Beta", "EPL")
    assert variants is not None
    built = soccer_model.candidate_from_variants(
        snapshot,
        "Alpha",
        "EPL",
        "Beta",
        "EPL",
        2.5,
        "Alpha vs. Beta",
        "m1",
        0.5,
    )
    assert built is not None
    assert isinstance(built, soccer_model.SoccerCandidate)
    assert built.match == "Alpha vs. Beta"
    assert built.question == "Alpha vs. Beta: O/U 2.5"
    assert built.kind == "total"
    assert built.side == 0
    assert built.market_id == "m1"
    assert built.entry_ask_vwap250 == 0.5
    # The ten quotes ARE the per-variant over probabilities, in sweep order.
    assert built.qs_variants == tuple(
        soccer_model.over_probability(v.outputs, 2.5) for v in variants
    )
    assert built.model_median == soccer_model.median(built.qs_variants)
    assert built.model_range == (min(built.qs_variants), max(built.qs_variants))
    # True uncertainty present: the sweep does not collapse on the snapshot.
    assert min(built.qs_variants) < max(built.qs_variants)
    # Lambda fields are the per-side medians, identical to match_lambdas.
    lambdas = soccer_model.match_lambdas(snapshot, "Alpha", "EPL", "Beta", "EPL")
    assert lambdas is not None
    assert (built.lambda_home, built.lambda_away) == lambdas

    # Refused fixtures return None exactly like fixture_variants:
    # banned cross-league pairing and unknown teams.
    banned = json.loads((FIXTURES / "regression_banned_path.json").read_text(encoding="utf-8"))["case"]
    assert soccer_model.candidate_from_variants(
        snapshot,
        banned["home"]["team"],
        banned["home"]["league"],
        banned["away"]["team"],
        banned["away"]["league"],
        2.5,
        "banned",
        "m2",
        0.5,
    ) is None
    assert soccer_model.candidate_from_variants(
        snapshot, "Alpha", "EPL", "Ghost", "EPL", 2.5, "ghost", "m3", 0.5
    ) is None


def test_bou_lee_public_builder_to_gate_rejected_for_variant_spread(
    tmp_path: Path,
) -> None:
    """FX1 headline regression (codex finding 1): BOU-LEE O2.5 through the
    PUBLIC builder-to-gate path must be REJECTED for var>6c with the true
    6.09c spread reproduced — while the frozen pair-level stub collapses
    the same sweep and would pass (why the stub is documented NOT
    gate-safe).
    """
    case = json.loads((FIXTURES / "regression_r23_soccer.json").read_text(encoding="utf-8"))[
        "cases"
    ]["BOU-LEE_O2.5"]
    snapshot = _load_r23_snapshot(tmp_path)

    candidate = soccer_model.candidate_from_variants(
        snapshot,
        "Bournemouth",
        "EPL",
        "Leeds",
        "EPL",
        2.5,
        case["match"],
        case["market_id"],
        case["entry_ask_vwap250"],
    )
    assert candidate is not None

    # True per-variant uncertainty carried into the candidate fair range.
    assert candidate.model_median == pytest.approx(case["model_median"], abs=1e-4)
    assert candidate.model_range[0] == pytest.approx(case["model_range"][0], abs=1e-4)
    assert candidate.model_range[1] == pytest.approx(case["model_range"][1], abs=1e-4)
    for got, want in zip(candidate.qs_variants, case["qs_variants"]):
        assert abs(got - want) <= 1e-4
    assert abs(candidate.lambda_home - case["lambda_home"]) <= 5e-4
    assert abs(candidate.lambda_away - case["lambda_away"]) <= 5e-4

    # Gate path: book fills $250 at the frozen 0.52 ask, liquidity and a
    # concordant news check are supplied; the ONLY failing gate is the
    # variant spread (worst +5.14c gross and exec +8.96c gross / +7.71c
    # net of the 1.25c sports fee both pass -- fee accounting,
    # 2026-11-05: the frozen fixture edge is GROSS, carried by the
    # report as exec_edge_gross).
    book = [(case["entry_ask_vwap250"], 1000.0)]
    news = gates.check_news("BOU-LEE", "48h", news_delta="unchanged")
    report = gates.evaluate_candidate(
        candidate,
        book,
        "ask",
        gamma_liquidity=case["liq_gamma"],
        news=news,
    )
    assert report.variant_spread == pytest.approx(case["var_spread"], abs=1e-4)
    assert report.worst_variant_edge == pytest.approx(case["edge_worst_variant"], abs=1e-4)
    assert report.exec_edge_gross == pytest.approx(case["edge_exec"], abs=1e-4)
    assert report.exec_edge == pytest.approx(
        round(case["edge_exec"] - round(gates.fee_cost(case["entry_ask_vwap250"], gates.FEE_RATE_SPORTS), 4), 4),
        abs=1e-4,
    )
    assert report.decision.passed is False
    assert report.decision.failed == ("variant_spread",)
    assert case["fail_reasons"] == ["var>6c"]

    # Documented hazard contrast: the pair-level stub on the same fixture
    # collapses O/U 2.5 to ten identical quotes (~0.61 every slot), so the
    # same gates would PASS it. Pinned so any change to the frozen stub
    # that alters this hazard surface fails consciously here.
    stub = soccer_model.over_under_quote(
        (candidate.lambda_home, candidate.lambda_away),
        2.5,
        market=case["match"],
        market_id=case["market_id"],
        entry_ask_vwap250=case["entry_ask_vwap250"],
    )
    stub_report = gates.evaluate_candidate(
        stub,
        book,
        "ask",
        gamma_liquidity=case["liq_gamma"],
        news=news,
    )
    assert stub_report.variant_spread == 0.0
    assert stub_report.checks["variant_spread<=6c"] is True
    assert stub_report.decision.passed is True


# ---------------------------------------------------------------------------
# FX-10 (M1 plan §3; FX-10 node chain: DELIVERY_PLAN.md 78.1): generic candidate construction across market kinds.
#
# ``candidate_for_market`` must reproduce the frozen
# ``candidate_from_variants`` over-side output bit-for-bit on the four
# frozen round-23 fixtures, quote every other supported market per variant
# through the SAME frozen probability helpers (reverse direction as the
# exact ``1 - p`` complement), expose extra context only through the new
# ``CandidateEnvelope`` (the ``SoccerCandidate`` contract stays frozen),
# and FAIL LOUDLY -- explicit ``ValueError`` -- on construction errors so
# FX-08/FX-09 can record them, never falling back to the NOT-gate-safe
# ``over_under_quote`` stub.


def _r23_case_meta(case_id: str) -> tuple[dict, str, str, float]:
    fixture = json.loads((FIXTURES / "regression_r23_soccer.json").read_text(encoding="utf-8"))
    case = fixture["cases"][case_id]
    home, away, line = next((h, a, ln) for cid, h, a, ln in R23_CASES if cid == case_id)
    return case, home, away, line


@pytest.mark.parametrize("case_id", [c[0] for c in R23_CASES])
def test_candidate_for_market_over_matches_legacy_builder_bitwise(
    case_id: str, tmp_path: Path
) -> None:
    """FX-10 headline proof: the generic builder's O/U over output IS the
    frozen builder's output, bit for bit, on every frozen r23 fixture at
    its frozen line."""
    case, home, away, line = _r23_case_meta(case_id)
    snapshot = _load_r23_snapshot(tmp_path)

    legacy = soccer_model.candidate_from_variants(
        snapshot,
        home,
        "EPL",
        away,
        "EPL",
        line,
        case["match"],
        case["market_id"],
        case["entry_ask_vwap250"],
    )
    env = soccer_model.candidate_for_market(
        snapshot,
        home,
        "EPL",
        away,
        "EPL",
        case["match"],
        case["market_id"],
        case["entry_ask_vwap250"],
        kind="total",
        side="over",
        line=line,
    )
    assert legacy is not None and env is not None
    # Bitwise: identical code path, identical floats, identical fields.
    assert env.candidate == legacy
    assert env.candidate.qs_variants == legacy.qs_variants
    assert env.candidate.model_median == legacy.model_median
    assert env.candidate.model_range == legacy.model_range
    assert env.candidate.question == legacy.question
    assert env.candidate.lambda_home == legacy.lambda_home
    assert env.candidate.lambda_away == legacy.lambda_away
    # Envelope context for the frozen over path.
    assert env.kind == "total" and env.side == "over"
    assert env.line == line and env.team_side is None
    assert env.quote_source == "over_probability"
    assert env.complement is False
    assert env.bridge_consulted is False and env.bridge_source is None


@pytest.mark.parametrize("case_id", [c[0] for c in R23_CASES])
def test_candidate_for_market_over_25_matches_legacy_all_four(
    case_id: str, tmp_path: Path
) -> None:
    """The literal reading of the FX-10 proof: O/U over 2.5 on all four
    frozen fixtures (including CHE-MUN, whose frozen case is O3.5) is
    bitwise identical to the legacy builder."""
    case, home, away, _ = _r23_case_meta(case_id)
    snapshot = _load_r23_snapshot(tmp_path)
    legacy = soccer_model.candidate_from_variants(
        snapshot, home, "EPL", away, "EPL", 2.5, case["match"], "mid-o25", 0.5
    )
    env = soccer_model.candidate_for_market(
        snapshot,
        home,
        "EPL",
        away,
        "EPL",
        case["match"],
        "mid-o25",
        0.5,
        kind="total",
        side="over",
        line=2.5,
    )
    assert legacy is not None and env is not None
    assert env.candidate == legacy


def test_candidate_for_market_quotes_reuse_frozen_probability_functions() -> None:
    """Every kind's per-variant quotes ARE the frozen helpers' outputs, in
    frozen sweep order; the envelope records the actual quote source."""
    snapshot = _mini_snapshot()
    variants = soccer_model.fixture_variants(snapshot, "Alpha", "EPL", "Beta", "EPL")
    assert variants is not None
    market = "Alpha vs. Beta"

    def build(**selection: object) -> soccer_model.CandidateEnvelope:
        env = soccer_model.candidate_for_market(
            snapshot, "Alpha", "EPL", "Beta", "EPL", market, "m1", 0.5, **selection
        )
        assert env is not None
        return env

    over = build(kind="total", side="over", line=2.5)
    under = build(kind="total", side="under", line=2.5)
    assert over.candidate.qs_variants == tuple(
        soccer_model.over_probability(v.outputs, 2.5) for v in variants
    )
    assert under.candidate.qs_variants == tuple(
        1.0 - soccer_model.over_probability(v.outputs, 2.5) for v in variants
    )
    assert over.candidate.question == "Alpha vs. Beta: O/U 2.5"
    assert over.candidate.kind == "total" and over.candidate.side == 0
    lambdas = soccer_model.match_lambdas(snapshot, "Alpha", "EPL", "Beta", "EPL")
    assert lambdas is not None
    assert (over.candidate.lambda_home, over.candidate.lambda_away) == lambdas

    yes = build(kind="btts", side="yes")
    no = build(kind="btts", side="no")
    assert yes.candidate.qs_variants == tuple(
        soccer_model.both_teams_to_score(v.outputs) for v in variants
    )
    assert no.candidate.qs_variants == tuple(
        1.0 - soccer_model.both_teams_to_score(v.outputs) for v in variants
    )

    tt_home_over = build(kind="team_total", side="over", line=1.5, team_side="home")
    tt_home_under = build(kind="team_total", side="under", line=1.5, team_side="home")
    tt_away_over = build(kind="team_total", side="over", line=0.5, team_side="away")
    assert tt_home_over.candidate.qs_variants == tuple(
        soccer_model.team_total_over(v.outputs, "home", 1.5) for v in variants
    )
    assert tt_home_under.candidate.qs_variants == tuple(
        1.0 - soccer_model.team_total_over(v.outputs, "home", 1.5) for v in variants
    )
    assert tt_away_over.candidate.qs_variants == tuple(
        soccer_model.team_total_over(v.outputs, "away", 0.5) for v in variants
    )

    home = build(kind="match_winner", side="home")
    home_no = build(kind="match_winner", side="home_no")
    draw = build(kind="match_winner", side="draw")
    away = build(kind="match_winner", side="away")
    assert home.candidate.qs_variants == tuple(v.outputs.home_win for v in variants)
    assert home_no.candidate.qs_variants == tuple(1.0 - v.outputs.home_win for v in variants)
    assert draw.candidate.qs_variants == tuple(v.outputs.draw for v in variants)
    assert away.candidate.qs_variants == tuple(v.outputs.away_win for v in variants)

    cover = build(kind="spread", side="cover", line=-1.5, team_side="home")
    nocover = build(kind="spread", side="nocover", line=-1.5, team_side="home")
    assert cover.candidate.qs_variants == tuple(
        soccer_model.cover_probability(v.outputs, "home", -1.5) for v in variants
    )
    assert nocover.candidate.qs_variants == tuple(
        1.0 - soccer_model.cover_probability(v.outputs, "home", -1.5) for v in variants
    )

    # Envelope provenance: the actual forward quote source per kind.
    assert over.quote_source == "over_probability"
    assert yes.quote_source == "both_teams_to_score"
    assert tt_home_over.quote_source == "team_total_over"
    assert home.quote_source == "MatchOutputs.home_win"
    assert draw.quote_source == "MatchOutputs.draw"
    assert away.quote_source == "MatchOutputs.away_win"
    assert cover.quote_source == "cover_probability"
    assert all(
        env.complement is False
        for env in (over, yes, tt_home_over, tt_away_over, home, draw, away, cover)
    )
    assert all(env.complement is True for env in (under, no, tt_home_under, home_no, nocover))
    # Aggregation and lambdas stay the frozen convention everywhere.
    for env in (over, under, yes, no, tt_home_over, home, cover):
        assert env.candidate.model_median == soccer_model.median(env.candidate.qs_variants)
        assert env.candidate.model_range == (
            min(env.candidate.qs_variants),
            max(env.candidate.qs_variants),
        )
        assert (env.candidate.lambda_home, env.candidate.lambda_away) == lambdas


def test_candidate_for_market_reverse_direction_complements() -> None:
    """p and 1-p symmetry for every supported reverse direction."""
    snapshot = _mini_snapshot()
    selections = (
        (("total", "over", 2.5, None), ("total", "under", 2.5, None)),
        (("btts", "yes", None, None), ("btts", "no", None, None)),
        (("team_total", "over", 1.5, "away"), ("team_total", "under", 1.5, "away")),
        (("match_winner", "home", None, None), ("match_winner", "home_no", None, None)),
        (("match_winner", "draw", None, None), ("match_winner", "draw_no", None, None)),
        (("match_winner", "away", None, None), ("match_winner", "away_no", None, None)),
        (("spread", "cover", -0.5, "home"), ("spread", "nocover", -0.5, "home")),
        (("spread", "cover", 1.5, "away"), ("spread", "nocover", 1.5, "away")),
    )
    for forward_sel, reverse_sel in selections:
        forward = soccer_model.candidate_for_market(
            snapshot, "Alpha", "EPL", "Beta", "EPL", "m", "m1", 0.5,
            kind=forward_sel[0], side=forward_sel[1], line=forward_sel[2], team_side=forward_sel[3],
        )
        reverse = soccer_model.candidate_for_market(
            snapshot, "Alpha", "EPL", "Beta", "EPL", "m", "m2", 0.5,
            kind=reverse_sel[0], side=reverse_sel[1], line=reverse_sel[2], team_side=reverse_sel[3],
        )
        assert forward is not None and reverse is not None
        # Exact per-variant complement by construction.
        for q_fwd, q_rev in zip(forward.candidate.qs_variants, reverse.candidate.qs_variants):
            assert q_rev == 1.0 - q_fwd
        assert forward.candidate.model_median + reverse.candidate.model_median == pytest.approx(
            1.0, abs=1e-12
        )
        # Coarse side codes: 0 = Over/Yes forward, 1 = complement.
        assert forward.candidate.side == 0 and reverse.candidate.side == 1


def test_candidate_for_market_match_winner_medians_not_renormalized() -> None:
    """The three 1X2 outcome medians are raw per-outcome medians over the
    ten variants and are NOT renormalized to sum to 1 (the documented
    existing aggregation; WP-P4 observed 0.988-1.005 on real fixtures)."""
    snapshot = _mini_snapshot()
    variants = soccer_model.fixture_variants(snapshot, "Alpha", "EPL", "Beta", "EPL")
    assert variants is not None

    medians = {}
    for side, attr in (("home", "home_win"), ("draw", "draw"), ("away", "away_win")):
        env = soccer_model.candidate_for_market(
            snapshot, "Alpha", "EPL", "Beta", "EPL", "m", "m1", 0.5,
            kind="match_winner", side=side,
        )
        assert env is not None
        medians[side] = env.candidate.model_median
        assert env.candidate.model_median == soccer_model.median(
            [getattr(v.outputs, attr) for v in variants]
        )
    total = medians["home"] + medians["draw"] + medians["away"]
    assert total == pytest.approx(
        soccer_model.median([v.outputs.home_win for v in variants])
        + soccer_model.median([v.outputs.draw for v in variants])
        + soccer_model.median([v.outputs.away_win for v in variants]),
        abs=1e-12,
    )
    # Sane aggregate, but not forced to exactly 1.
    assert 0.9 <= total <= 1.1


def test_candidate_for_market_construction_errors_are_explicit() -> None:
    """Every construction error raises a loud ValueError (for FX-08/09 to
    record); nothing is silently defaulted or coerced."""
    snapshot = _mini_snapshot()

    def call(**selection: object) -> soccer_model.CandidateEnvelope:
        base: dict[str, object] = {"kind": "total", "side": "over", "line": 2.5}
        base.update(selection)
        env = soccer_model.candidate_for_market(
            snapshot, "Alpha", "EPL", "Beta", "EPL", "m", "m1", 0.5, **base
        )
        assert env is not None
        return env

    with pytest.raises(ValueError, match="kind"):
        call(kind="corners", side="over", line=2.5)
    with pytest.raises(ValueError, match="kind"):
        call(kind="ml", side="home")
    with pytest.raises(ValueError, match="side"):
        call(side="yes")
    with pytest.raises(ValueError, match="side"):
        call(kind="btts", side="over")
    with pytest.raises(ValueError, match="side"):
        call(kind="match_winner", side="home_yes")
    with pytest.raises(ValueError, match="side"):
        call(kind="spread", side="over", line=-1.5, team_side="home")
    with pytest.raises(ValueError, match="requires a line"):
        call(line=None)
    with pytest.raises(ValueError, match="team_side"):
        call(kind="team_total", side="over", line=1.5)
    with pytest.raises(ValueError, match="team_side"):
        call(kind="team_total", side="over", line=1.5, team_side="midfield")
    with pytest.raises(ValueError, match="takes no line"):
        call(kind="btts", side="yes", line=2.5)
    with pytest.raises(ValueError, match="takes no team_side"):
        call(kind="match_winner", side="home", line=None, team_side="home")
    # Half-ball rule: integer lines have pushes, quarter lines mix
    # settlements, non-finite / non-numeric values are garbage.
    for bad_line in (3.0, 2, 2.25, 1.75, -1.0, 0.0, float("inf"), float("-inf"), float("nan"), "2.5", True):
        with pytest.raises(ValueError, match="line"):
            call(line=bad_line)
    with pytest.raises(ValueError, match="line"):
        call(kind="spread", side="cover", line=-1, team_side="home")


def test_candidate_for_market_spread_is_quote_only_registry_rejects() -> None:
    """Spread envelopes are quotable, but the suspension registry keeps
    rejecting spread entry (FLAGGED_ONLY) -- quoting is not entry
    authorization."""
    snapshot = _mini_snapshot()
    env = soccer_model.candidate_for_market(
        snapshot, "Alpha", "EPL", "Beta", "EPL", "Alpha vs. Beta", "m1", 0.5,
        kind="spread", side="cover", line=-1.5, team_side="home",
    )
    assert env is not None
    assert env.candidate.kind == "spread"
    policy = gates.suspension_policy(env.candidate.kind)
    assert policy.policy == gates.POLICY_FLAGGED_ONLY
    assert policy.eligible is False
    cross = gates.suspension_policy(env.candidate.kind, cross_league=True)
    assert cross.eligible is False
    # Eligible kinds stay eligible through the same registry.
    for kind in ("total", "btts", "team_total"):
        assert gates.suspension_policy(kind).eligible is True
    assert gates.suspension_policy("match_winner").policy == gates.POLICY_ELIGIBLE_DEPRIORITIZED


def test_candidate_for_market_refused_fixture_returns_none_without_fallback() -> None:
    """Fixture refusal (banned cross-league pairing, unknown team/league)
    returns None exactly like the frozen builders -- never a fabricated
    candidate and never an ``over_under_quote`` fallback."""
    snapshot = _mini_snapshot()
    banned = json.loads((FIXTURES / "regression_banned_path.json").read_text(encoding="utf-8"))["case"]
    assert soccer_model.candidate_for_market(
        snapshot,
        banned["home"]["team"],
        banned["home"]["league"],
        banned["away"]["team"],
        banned["away"]["league"],
        "banned",
        "m1",
        0.5,
        kind="total",
        side="over",
        line=2.5,
    ) is None
    assert soccer_model.candidate_for_market(
        snapshot, "Alpha", "EPL", "Ghost", "EPL", "ghost", "m2", 0.5,
        kind="btts", side="yes",
    ) is None
    assert soccer_model.candidate_for_market(
        snapshot, "Alpha", "Serie_A", "Beta", "Serie_A", "noleague", "m3", 0.5,
        kind="match_winner", side="home",
    ) is None


def test_candidate_for_market_bridge_provenance() -> None:
    """The envelope's bridge record corresponds to the actual source: a
    bridged cross-league construction records the exact bridge table; a
    same-league construction never consults one (quotes identical with or
    without a bridge argument)."""
    snapshot = _mini_snapshot()
    bridge = {"bundesliga": -0.09, "ligue_1": -0.18}

    env = soccer_model.candidate_for_market(
        snapshot,
        "TSG 1899 Hoffenheim",
        "bundesliga",
        "Olympique Lyonnais",
        "ligue_1",
        "HOF-LYO",
        "m1",
        0.5,
        kind="total",
        side="over",
        line=2.5,
        bridge=bridge,
    )
    assert env is not None
    assert env.bridge_consulted is True
    assert env.bridge_source is bridge
    bridged = soccer_model.fixture_variants(
        snapshot,
        "TSG 1899 Hoffenheim",
        "bundesliga",
        "Olympique Lyonnais",
        "ligue_1",
        bridge=bridge,
    )
    assert bridged is not None
    assert env.candidate.qs_variants == tuple(
        soccer_model.over_probability(v.outputs, 2.5) for v in bridged
    )
    # The same fixture without a bridge is refused (None), never bridged
    # silently and never fallen back to the pair-level stub.
    assert soccer_model.candidate_for_market(
        snapshot,
        "TSG 1899 Hoffenheim",
        "bundesliga",
        "Olympique Lyonnais",
        "ligue_1",
        "HOF-LYO",
        "m2",
        0.5,
        kind="total",
        side="over",
        line=2.5,
    ) is None

    # Same-league: bridge is never consulted; quotes and provenance ignore it.
    with_bridge = soccer_model.candidate_for_market(
        snapshot, "Alpha", "EPL", "Beta", "EPL", "AB", "m3", 0.5,
        kind="total", side="over", line=2.5, bridge={"EPL": 0.9},
    )
    without_bridge = soccer_model.candidate_for_market(
        snapshot, "Alpha", "EPL", "Beta", "EPL", "AB", "m4", 0.5,
        kind="total", side="over", line=2.5,
    )
    assert with_bridge is not None and without_bridge is not None
    assert with_bridge.candidate.qs_variants == without_bridge.candidate.qs_variants
    assert with_bridge.bridge_consulted is False
    assert with_bridge.bridge_source is None
