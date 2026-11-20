"""WP-P2a Phase C tests: league-strength bridge (κ table + guarded wiring).

Covers the ``league_strength`` module contracts (κ table type, JSON
loader, clamped bridge factor) and the bridge wiring through
``xg.pair_lambdas`` / ``soccer_model`` without regressing the frozen
same-league arithmetic or the unbridged cross-league guard:

- Same-league invariance: with AND without a bridge, same-league
  outputs are byte-identical at every level (``pair_lambdas``,
  ``fixture_variants``, ``match_lambdas``, ``candidate_from_variants``).
- Cross-league without a bridge: the lineage guard (``None``) stands.
- Cross-league with a bridge: rates come back, each side keeping its
  OWN league's base rate and multipliers, tilted by the clamped factor.
- Clamp: a κ difference beyond ``ln(1.25)`` is clamped to the 1.25
  ratio; unknown leagues in the bridge return ``None``.
- HOF-LYO direction: the synthetic fixture below reproduces the
  historical favorite-flip direction (raw cross-league multipliers
  favor Lyon) and a bridge with Bundesliga κ above Ligue 1 κ — UEFA
  country-coefficient order at the 2026 freeze — flips the modeled
  favorite toward Hoffenheim, the side the market correctly priced.
- Bridge factor math: ``f = exp(clamp(kappa_home - kappa_away, ...))``.

The synthetic snapshot is hand-computable: Hoffenheim is exactly
league-average in every rating mode (multipliers 1.0), and Lyon's
blend-w=.5-p=8 multipliers reduce to the exact rationals ``25/21``
(attack) and ``17/21`` (defense).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from polymarket_alpha_lab.papersim import league_strength, soccer_model, xg

HOF_TEAM = "TSG 1899 Hoffenheim"
HOF_LEAGUE = "bundesliga"
LYO_TEAM = "Olympique Lyonnais"
LYO_LEAGUE = "ligue_1"

#: Bridge with Bundesliga stronger at exactly the clamp displacement.
STRONG_BUNDES_LIGA_BRIDGE = {
    HOF_LEAGUE: math.log(1.25),
    LYO_LEAGUE: 0.0,
}

#: Neutral bridge: equal κ, so f = exp(0) = 1 and the pair reduces to
#: the raw (defect-direction) cross-league combination.
NEUTRAL_BRIDGE = {HOF_LEAGUE: 0.1, LYO_LEAGUE: 0.1}


def _bridge_snapshot() -> dict[str, object]:
    """Hand-computable EPL + bundesliga + ligue_1 snapshot.

    Hoffenheim is league-average everywhere (1.5 goals for/against,
    league base 1.5/1.5), so his multipliers are exactly 1.0 in every
    rating mode. Lyon's history is 1.9 for / 1.1 against in a league
    with base 1.5/1.5: the raw cross-league combination favors Lyon
    (the HOF-LYO defect direction) while he remains inside the clamp's
    ability to flip the favorite once Bundesliga is bridged stronger.
    """
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
            "dates": [{"isResult": True, "xG": {"h": 1.5, "a": 1.5}}],
            "teams": {
                "3": {"title": HOF_TEAM, "history": [{"xG": 1.5, "xGA": 1.5}]},
            },
        },
        "bundesliga_2026": {
            "dates": [],
            "teams": {
                "3": {"title": HOF_TEAM, "history": [{"xG": 1.5, "xGA": 1.5}]},
            },
        },
        "ligue_1_2025": {
            "dates": [{"isResult": True, "xG": {"h": 1.5, "a": 1.5}}],
            "teams": {
                "4": {"title": LYO_TEAM, "history": [{"xG": 1.9, "xGA": 1.1}]},
            },
        },
        "ligue_1_2026": {
            "dates": [],
            "teams": {
                "4": {"title": LYO_TEAM, "history": [{"xG": 1.9, "xGA": 1.1}]},
            },
        },
    }


def test_bridge_factor_is_exp_of_home_minus_away_kappa() -> None:
    kappa = {"EPL": 0.3, "La_liga": 0.1}
    # f = exp(kappa_home - kappa_away): stronger HOME league boosts home.
    assert league_strength.bridge_factor(kappa, "EPL", "La_liga") == pytest.approx(
        math.exp(0.2), rel=1e-15
    )
    assert league_strength.bridge_factor(kappa, "La_liga", "EPL") == pytest.approx(
        math.exp(-0.2), rel=1e-15
    )
    # Equal kappa -> exactly neutral (exp(0.0) == 1.0).
    assert league_strength.bridge_factor(kappa, "EPL", "EPL") == 1.0
    # Antisymmetry: reversing the fixture inverts the factor.
    assert league_strength.bridge_factor(kappa, "EPL", "La_liga") * league_strength.bridge_factor(
        kappa, "La_liga", "EPL"
    ) == pytest.approx(1.0, rel=1e-15)
    # Clamp constant wiring.
    assert league_strength.BRIDGE_CLAMP_RATIO == 1.25
    assert league_strength.BRIDGE_CLAMP_LOG == pytest.approx(math.log(1.25), rel=1e-15)


def test_bridge_factor_missing_league_returns_none() -> None:
    kappa = {"EPL": 0.1}
    assert league_strength.bridge_factor(kappa, "EPL", "Serie_A") is None
    assert league_strength.bridge_factor(kappa, "Serie_A", "EPL") is None
    assert league_strength.bridge_factor({}, "EPL", "La_liga") is None


def test_bridge_factor_clamps_at_ratio_1_25() -> None:
    # A kappa difference far beyond ln(1.25) clamps to the 1.25 ratio.
    assert league_strength.bridge_factor({"A": 5.0, "B": 0.0}, "A", "B") == pytest.approx(1.25)
    assert league_strength.bridge_factor({"A": 5.0, "B": 0.0}, "B", "A") == pytest.approx(0.8)
    # Exactly at the clamp boundary: still the capped ratio.
    at_bound = {"A": league_strength.BRIDGE_CLAMP_LOG, "B": 0.0}
    assert league_strength.bridge_factor(at_bound, "A", "B") == pytest.approx(1.25)
    just_inside = {"A": 0.2, "B": 0.0}  # 0.2 < ln(1.25) ~ 0.2231
    assert league_strength.bridge_factor(just_inside, "A", "B") == pytest.approx(math.exp(0.2))


def test_load_kappa_table_round_trip_and_validation(tmp_path: Path) -> None:
    payload = {HOF_LEAGUE: 0.05, LYO_LEAGUE: 0.0, "EPL": -0.02}
    path = tmp_path / "kappa.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    table = league_strength.load_kappa_table(str(path))
    assert isinstance(table, league_strength.LeagueStrengthTable)
    assert dict(table.kappa) == payload
    # The loaded mapping is the bridge the model functions consume.
    lambdas = soccer_model.match_lambdas(
        _bridge_snapshot(), HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE, bridge=table.kappa
    )
    assert lambdas is not None

    bad_root = tmp_path / "bad_root.json"
    bad_root.write_text(json.dumps([0.1, 0.2]), encoding="utf-8")
    with pytest.raises(ValueError):
        league_strength.load_kappa_table(str(bad_root))

    bad_value = tmp_path / "bad_value.json"
    bad_value.write_text(json.dumps({"EPL": "strong"}), encoding="utf-8")
    with pytest.raises(ValueError):
        league_strength.load_kappa_table(str(bad_value))

    non_finite = tmp_path / "non_finite.json"
    non_finite.write_text(json.dumps({"EPL": float("inf")}), encoding="utf-8")
    with pytest.raises(ValueError):
        league_strength.load_kappa_table(str(non_finite))

    with pytest.raises(FileNotFoundError):
        league_strength.load_kappa_table(str(tmp_path / "absent.json"))


def test_league_strength_table_is_frozen() -> None:
    table = league_strength.LeagueStrengthTable(kappa={"EPL": 0.1})
    with pytest.raises(AttributeError):
        table.kappa = {"La_liga": 0.2}  # type: ignore[misc]


def test_same_league_pair_lambdas_identical_with_and_without_bridge() -> None:
    snapshot = _bridge_snapshot()
    bridging = {"EPL": 0.25, HOF_LEAGUE: 0.1, LYO_LEAGUE: -0.1}
    for mode in xg.RATING_MODES:
        without = xg.pair_lambdas(
            snapshot, "Alpha", "EPL", "Beta", "EPL",
            last_weight=0.5, prior_games=8.0, mode=mode,
        )
        with_bridge = xg.pair_lambdas(
            snapshot, "Alpha", "EPL", "Beta", "EPL",
            last_weight=0.5, prior_games=8.0, mode=mode, bridge=bridging,
        )
        assert without is not None
        # Byte-identical: the same-league path never consults the bridge.
        assert with_bridge == without


def test_same_league_higher_levels_ignore_bridge() -> None:
    snapshot = _bridge_snapshot()
    bridging = {"EPL": 0.25, HOF_LEAGUE: 0.1, LYO_LEAGUE: -0.1}

    variants_plain = soccer_model.fixture_variants(snapshot, "Alpha", "EPL", "Beta", "EPL")
    variants_bridge = soccer_model.fixture_variants(
        snapshot, "Alpha", "EPL", "Beta", "EPL", bridge=bridging
    )
    assert variants_plain is not None and variants_bridge is not None
    assert variants_bridge == variants_plain  # all ten variants, bit-for-bit

    lambdas_plain = soccer_model.match_lambdas(snapshot, "Alpha", "EPL", "Beta", "EPL")
    lambdas_bridge = soccer_model.match_lambdas(
        snapshot, "Alpha", "EPL", "Beta", "EPL", bridge=bridging
    )
    assert lambdas_plain is not None
    assert lambdas_bridge == lambdas_plain

    candidate_plain = soccer_model.candidate_from_variants(
        snapshot, "Alpha", "EPL", "Beta", "EPL", 2.5, "Alpha vs. Beta", "m1", 0.5
    )
    candidate_bridge = soccer_model.candidate_from_variants(
        snapshot, "Alpha", "EPL", "Beta", "EPL", 2.5, "Alpha vs. Beta", "m1", 0.5,
        bridge=bridging,
    )
    assert candidate_plain is not None
    assert candidate_bridge == candidate_plain  # full dataclass equality


def test_cross_league_guard_unchanged_without_bridge() -> None:
    snapshot = _bridge_snapshot()
    assert xg.pair_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
        last_weight=0.5, prior_games=8.0,
    ) is None
    assert soccer_model.fixture_variants(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE
    ) is None
    assert soccer_model.match_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE
    ) is None
    assert soccer_model.candidate_from_variants(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE, 2.5, "HOF-LYO", "m2", 0.5
    ) is None


def test_cross_league_with_bridge_returns_lambdas() -> None:
    snapshot = _bridge_snapshot()
    # Variant 0 (blend w=.5 p=8): HOF multipliers exactly 1.0; Lyon's
    # attack 25/21, defense 17/21; base rates 1.5/1.5; f = 1.25.
    # lambda_home = 1.5 * 1 * (17/21) * 1.25 = 85/56,
    # lambda_away  = 1.5 * (25/21) * 1 / 1.25 = 10/7.
    lambdas = xg.pair_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
        last_weight=0.5, prior_games=8.0, bridge=STRONG_BUNDES_LIGA_BRIDGE,
    )
    assert lambdas is not None
    assert lambdas[0] == pytest.approx(85 / 56, rel=1e-12)
    # Away carries the WP-P5 away haircut on top of the bridge tilt.
    assert lambdas[1] == pytest.approx(10 / 7 * league_strength.AWAY_HAIRCUT_FACTOR, rel=1e-12)

    # Explicit season threading resolves both leagues' blocks.
    explicit = xg.pair_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
        last_weight=0.5, prior_games=8.0, season="2026",
        bridge=STRONG_BUNDES_LIGA_BRIDGE,
    )
    assert explicit == lambdas

    # All ten variants evaluate and the higher levels return values.
    variants = soccer_model.fixture_variants(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
        bridge=STRONG_BUNDES_LIGA_BRIDGE,
    )
    assert variants is not None
    assert [v.spec.name for v in variants] == list(soccer_model.SWEEP_NAMES)
    medians = soccer_model.match_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
        bridge=STRONG_BUNDES_LIGA_BRIDGE,
    )
    assert medians is not None
    candidate = soccer_model.candidate_from_variants(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE, 2.5, "HOF-LYO", "m2", 0.5,
        bridge=STRONG_BUNDES_LIGA_BRIDGE,
    )
    assert candidate is not None
    assert candidate.lambda_home == medians[0]
    assert candidate.lambda_away == medians[1]


def test_cross_league_unknown_league_in_bridge_returns_none() -> None:
    snapshot = _bridge_snapshot()
    for bridge in (
        {HOF_LEAGUE: 0.1},  # away league missing from the bridge
        {LYO_LEAGUE: 0.1},  # home league missing from the bridge
        {},
    ):
        assert xg.pair_lambdas(
            snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
            last_weight=0.5, prior_games=8.0, bridge=bridge,
        ) is None
        assert soccer_model.match_lambdas(
            snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE, bridge=bridge
        ) is None


def test_cross_league_guard_parity_with_bridge() -> None:
    """A bridge does NOT soften the per-league resolvability guards."""
    snapshot = _bridge_snapshot()
    bridge = dict(STRONG_BUNDES_LIGA_BRIDGE)
    # Unknown team in its own league's table.
    assert xg.pair_lambdas(
        snapshot, "Ghost", HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
        last_weight=0.5, prior_games=8.0, bridge=bridge,
    ) is None
    assert xg.pair_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, "Ghost", LYO_LEAGUE,
        last_weight=0.5, prior_games=8.0, bridge=bridge,
    ) is None
    # League absent from the snapshot entirely.
    assert xg.pair_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, "Alpha", "Serie_A",
        last_weight=0.5, prior_games=8.0, bridge={**bridge, "Serie_A": 0.1},
    ) is None
    # Season without blocks for a league.
    assert xg.pair_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
        last_weight=0.5, prior_games=8.0, season="2030", bridge=bridge,
    ) is None


def test_bridge_scales_every_variant_lambda_exactly() -> None:
    """The bridge is a pure post-multiplier tilt: with f = 1.25 exactly
    at the clamp, every variant's home rate is the neutral rate times
    the factor and the away rate the neutral rate divided by it (the
    per-variant multipliers are untouched — exact float equality)."""
    snapshot = _bridge_snapshot()
    neutral = soccer_model.fixture_variants(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE, bridge=NEUTRAL_BRIDGE
    )
    bridged = soccer_model.fixture_variants(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
        bridge=STRONG_BUNDES_LIGA_BRIDGE,
    )
    assert neutral is not None and bridged is not None
    factor = league_strength.bridge_factor(STRONG_BUNDES_LIGA_BRIDGE, HOF_LEAGUE, LYO_LEAGUE)
    assert factor == pytest.approx(1.25)
    for plain, tilted in zip(neutral, bridged):
        assert tilted.lambdas[0] == plain.lambdas[0] * factor
        assert tilted.lambdas[1] == plain.lambdas[1] / factor


def test_bridged_clamp_caps_displacement() -> None:
    """A kappa difference of 1.0 clamps to the same factor as one of
    ln(1.25): identical lambdas, so no kappa error can push a fixture
    further than the 1.25-per-side ratio."""
    snapshot = _bridge_snapshot()
    capped = xg.pair_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
        last_weight=0.5, prior_games=8.0,
        bridge={HOF_LEAGUE: 1.0, LYO_LEAGUE: 0.0},
    )
    at_bound = xg.pair_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
        last_weight=0.5, prior_games=8.0, bridge=STRONG_BUNDES_LIGA_BRIDGE,
    )
    assert capped is not None and at_bound is not None
    assert capped == at_bound


def test_hof_lyo_favorite_flips_toward_hof_with_bundesliga_stronger() -> None:
    """Direction check against the frozen banned-path case.

    The synthetic Lyon ratings are strong enough that the RAW
    cross-league combination (neutral bridge, f = 1) makes Lyon the
    modeled favorite — the historical favorite-flip direction. With a
    bridge where Bundesliga's κ is above Ligue 1's (UEFA
    country-coefficient order at the 2026 freeze), the clamped factor
    flips the modeled favorite toward Hoffenheim, the side the market
    correctly priced (see ``regression_banned_path.json``).
    """
    snapshot = _bridge_snapshot()

    neutral = soccer_model.match_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE, bridge=NEUTRAL_BRIDGE
    )
    assert neutral is not None
    neutral_outputs = soccer_model.variant_outputs(neutral[0], neutral[1])
    # Defect direction reproduced: raw multipliers favor Lyon.
    assert neutral[0] < neutral[1]
    assert neutral_outputs.away_win > neutral_outputs.home_win

    bridged = soccer_model.match_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
        bridge=STRONG_BUNDES_LIGA_BRIDGE,
    )
    assert bridged is not None
    # The bridge moved BOTH rates toward Hoffenheim.
    assert bridged[0] > neutral[0]
    assert bridged[1] < neutral[1]
    bridged_outputs = soccer_model.variant_outputs(bridged[0], bridged[1])
    # Favorite flipped toward HOF: home now outrights away.
    assert bridged[0] > bridged[1]
    assert bridged_outputs.home_win > bridged_outputs.away_win


# ---------------------------------------------------------------------------
# Non-finite / boolean κ rejection (codex review 2026-11-03, major 1)
# ---------------------------------------------------------------------------


def test_bridge_factor_rejects_non_finite_and_boolean_kappa() -> None:
    """A NaN κ compares False against BOTH clamp bounds, so an unguarded
    clamp surfaced it as a silently plausible factor of exactly 1.25;
    it must instead raise ValueError naming the offending league."""
    # NaN on either side raises, naming the offending league.
    with pytest.raises(ValueError, match="EPL"):
        league_strength.bridge_factor(
            {"EPL": float("nan"), "La_liga": 0.1}, "EPL", "La_liga"
        )
    with pytest.raises(ValueError, match="La_liga"):
        league_strength.bridge_factor(
            {"EPL": 0.1, "La_liga": float("nan")}, "EPL", "La_liga"
        )
    # +/- infinity equally rejected on either side.
    for infinite in (float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="EPL"):
            league_strength.bridge_factor(
                {"EPL": infinite, "La_liga": 0.1}, "EPL", "La_liga"
            )
        with pytest.raises(ValueError, match="La_liga"):
            league_strength.bridge_factor(
                {"EPL": 0.1, "La_liga": infinite}, "EPL", "La_liga"
            )
    # bool subclasses int and float(True) == 1.0 would silently pass.
    with pytest.raises(ValueError, match="EPL"):
        league_strength.bridge_factor(
            {"EPL": True, "La_liga": 0.1}, "EPL", "La_liga"  # type: ignore[dict-item]
        )
    with pytest.raises(ValueError, match="La_liga"):
        league_strength.bridge_factor(
            {"EPL": 0.1, "La_liga": False}, "EPL", "La_liga"  # type: ignore[dict-item]
        )
    # A non-numeric κ is rejected too (no silent coercion).
    with pytest.raises(ValueError, match="EPL"):
        league_strength.bridge_factor(
            {"EPL": "strong", "La_liga": 0.1}, "EPL", "La_liga"  # type: ignore[dict-item]
        )


def test_candidate_from_variants_rejects_non_finite_bridge_kappa() -> None:
    """The guard fires through the full public path: a bridge threaded
    into ``candidate_from_variants`` reaches ``bridge_factor``, which
    must raise rather than emit a clamped quote from a poisoned κ."""
    snapshot = _bridge_snapshot()
    for bad in (float("nan"), float("inf"), float("-inf"), True):
        with pytest.raises(ValueError):
            soccer_model.candidate_from_variants(
                snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
                2.5, "HOF-LYO", "m2", 0.5,
                bridge={HOF_LEAGUE: bad, LYO_LEAGUE: 0.0},
            )
        with pytest.raises(ValueError):
            soccer_model.candidate_from_variants(
                snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
                2.5, "HOF-LYO", "m2", 0.5,
                bridge={HOF_LEAGUE: 0.0, LYO_LEAGUE: bad},
            )


def test_load_kappa_table_rejects_boolean_entry(tmp_path: Path) -> None:
    # JSON true parses to a Python bool; float(True) == 1.0 must not
    # let it through as a league strength of exactly 1.0.
    boolean = tmp_path / "boolean.json"
    boolean.write_text(json.dumps({HOF_LEAGUE: True}), encoding="utf-8")
    with pytest.raises(ValueError):
        league_strength.load_kappa_table(str(boolean))


# ---------------------------------------------------------------------------
# Nested WP-P2b report loader (codex review 2026-11-03, minor 3)
# ---------------------------------------------------------------------------


def test_load_kappa_report_extracts_nested_kappa_object(tmp_path: Path) -> None:
    """The WP-P2b estimation report carries its κ mapping under a
    ``"kappa"`` member next to its own metadata; the report loader
    extracts exactly that sub-object."""
    expected = {HOF_LEAGUE: 0.05, LYO_LEAGUE: 0.0, "EPL": -0.02}
    report = {
        "generated_at": "2026-11-03T00:00:00Z",
        "anchor": league_strength.UEFA_COEFFICIENT_REFERENCE_URL,
        "backtest": {"seasons": ["2023", "2024"], "status": "PASS"},
        "kappa": expected,
    }
    path = tmp_path / "kappa_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    table = league_strength.load_kappa_report(str(path))
    assert isinstance(table, league_strength.LeagueStrengthTable)
    assert dict(table.kappa) == expected
    # The extracted table is directly usable as the bridge.
    lambdas = soccer_model.match_lambdas(
        _bridge_snapshot(), HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
        bridge=table.kappa,
    )
    assert lambdas is not None

    # A flat table (no "kappa" member) is the OTHER loader's format.
    flat = tmp_path / "flat.json"
    flat.write_text(json.dumps({HOF_LEAGUE: 0.05}), encoding="utf-8")
    with pytest.raises(ValueError):
        league_strength.load_kappa_report(str(flat))

    # "kappa" present but not an object.
    scalar = tmp_path / "kappa_scalar.json"
    scalar.write_text(json.dumps({"kappa": 0.1}), encoding="utf-8")
    with pytest.raises(ValueError):
        league_strength.load_kappa_report(str(scalar))

    # Invalid entries inside the nested object are rejected identically
    # to the flat loader.
    for label, bad_entry in (("inf", float("inf")), ("bool", True), ("str", "strong")):
        bad = tmp_path / f"bad_{label}.json"
        bad.write_text(json.dumps({"kappa": {HOF_LEAGUE: bad_entry}}), encoding="utf-8")
        with pytest.raises(ValueError):
            league_strength.load_kappa_report(str(bad))

    # Non-object root.
    bad_root = tmp_path / "bad_root_report.json"
    bad_root.write_text(json.dumps([1, 2]), encoding="utf-8")
    with pytest.raises(ValueError):
        league_strength.load_kappa_report(str(bad_root))

    # And the flat loader refuses the nested report (its metadata
    # values are not numbers) — the two documented paths stay separate.
    with pytest.raises(ValueError):
        league_strength.load_kappa_table(str(path))

    with pytest.raises(FileNotFoundError):
        league_strength.load_kappa_report(str(tmp_path / "absent_report.json"))


# ---------------------------------------------------------------------------
# Deep immutability of the κ table (codex review 2026-11-03, minor 4)
# ---------------------------------------------------------------------------


def test_league_strength_table_kappa_mapping_is_read_only() -> None:
    """A frozen dataclass over a plain dict was only shallowly
    immutable: mutating the wrapped dict mutated the table."""
    source: dict[str, float] = {"EPL": 0.1}
    table = league_strength.LeagueStrengthTable(kappa=source)
    with pytest.raises(TypeError):
        table.kappa["EPL"] = 0.5  # type: ignore[index]
    with pytest.raises(TypeError):
        table.kappa["La_liga"] = 0.2  # type: ignore[index]
    with pytest.raises(TypeError):
        del table.kappa["EPL"]  # type: ignore[index]
    # Mutating the caller's source dict AFTER construction is isolated.
    source["EPL"] = 9.9
    source["La_liga"] = -9.9
    assert dict(table.kappa) == {"EPL": 0.1}


# ---------------------------------------------------------------------------
# Asymmetric league base rates (codex review 2026-11-03, minor 5)
# ---------------------------------------------------------------------------


def _asymmetric_snapshot() -> dict[str, object]:
    """Two leagues with UNEQUAL home/away base rate splits.

    The earlier bridge fixtures used symmetric 1.5/1.5 base rates in
    BOTH leagues, so a home/away base-rate swap between the leagues
    could not be detected. Here the prior-season league results are:

    - bundesliga: one finished date ``h=2.5, a=0.5`` -> home rate 2.5,
      away rate 0.5, league average (2.5+0.5)/2 = 1.5;
    - ligue_1: one finished date ``h=1.0, a=2.0`` -> home rate 1.0,
      away rate 2.0, league average (1.0+2.0)/2 = 1.5.

    Both teams sit exactly at their league's 1.5 average in both
    seasons, so every rating mode gives multipliers of exactly 1.0 and
    the hand-computed expectations below are exact.
    """
    return {
        "bundesliga_2025": {
            "dates": [{"isResult": True, "xG": {"h": 2.5, "a": 0.5}}],
            "teams": {"3": {"title": HOF_TEAM, "history": [{"xG": 1.5, "xGA": 1.5}]}},
        },
        "bundesliga_2026": {
            "dates": [],
            "teams": {"3": {"title": HOF_TEAM, "history": [{"xG": 1.5, "xGA": 1.5}]}},
        },
        "ligue_1_2025": {
            "dates": [{"isResult": True, "xG": {"h": 1.0, "a": 2.0}}],
            "teams": {"4": {"title": LYO_TEAM, "history": [{"xG": 1.5, "xGA": 1.5}]}},
        },
        "ligue_1_2026": {
            "dates": [],
            "teams": {"4": {"title": LYO_TEAM, "history": [{"xG": 1.5, "xGA": 1.5}]}},
        },
    }


def test_cross_league_asymmetric_base_rates_keep_own_league_rates() -> None:
    """Hand-computed lambdas proving each side keeps its OWN league's
    home/away base rate under the bridge.

    Neutral bridge (f = exp(0.1 - 0.1) = 1 exactly, multipliers 1.0):

    - lambda_home = bundesliga home rate 2.5 * 1 * 1 * 1 = 2.5 — NOT
      ligue_1's home rate 1.0;
    - lambda_away = ligue_1 away rate 2.0 * 1 * 1 * haircut = 2.0 * exp(-0.06)
      ≈ 1.8835 — NOT bundesliga's away rate 0.5 (haircut per WP-P5).
    """
    snapshot = _asymmetric_snapshot()

    neutral = xg.pair_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
        last_weight=0.5, prior_games=8.0, bridge=NEUTRAL_BRIDGE,
    )
    assert neutral == (2.5, 2.0 * league_strength.AWAY_HAIRCUT_FACTOR)

    # Every sweep variant, not just the blend: with exactly-average
    # teams all ten modes reduce to the same hand-computed pair.
    variants = soccer_model.fixture_variants(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE, bridge=NEUTRAL_BRIDGE
    )
    assert variants is not None
    assert [variant.lambdas for variant in variants] == [
        (2.5, 2.0 * league_strength.AWAY_HAIRCUT_FACTOR)
    ] * len(variants)
    medians = soccer_model.match_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE, bridge=NEUTRAL_BRIDGE
    )
    assert medians == (2.5, 2.0 * league_strength.AWAY_HAIRCUT_FACTOR)

    # Bridged (f = exp(ln 1.25) ~ 1.25): lambda_home = 2.5 * f and
    # lambda_away = 2.0 / f — the own-league base rates tilted by the
    # clamped factor, not replaced by the other league's rates.
    factor = league_strength.bridge_factor(
        STRONG_BUNDES_LIGA_BRIDGE, HOF_LEAGUE, LYO_LEAGUE
    )
    assert factor == pytest.approx(1.25)
    bridged = xg.pair_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
        last_weight=0.5, prior_games=8.0, bridge=STRONG_BUNDES_LIGA_BRIDGE,
    )
    assert bridged is not None
    assert bridged[0] == pytest.approx(2.5 * factor, rel=1e-15)
    assert bridged[1] == pytest.approx(2.0 * league_strength.AWAY_HAIRCUT_FACTOR / factor, rel=1e-15)

    # Reversing the fixture swaps WHICH league supplies each rate:
    # ligue_1 at home supplies its home rate 1.0, bundesliga away
    # supplies its away rate 0.5 (f = exp(0 - ln 1.25) = 1/1.25).
    reversed_pair = xg.pair_lambdas(
        snapshot, LYO_TEAM, LYO_LEAGUE, HOF_TEAM, HOF_LEAGUE,
        last_weight=0.5, prior_games=8.0, bridge=STRONG_BUNDES_LIGA_BRIDGE,
    )
    assert reversed_pair is not None
    assert reversed_pair[0] == pytest.approx(1.0 / factor, rel=1e-15)
    assert reversed_pair[1] == pytest.approx(
        0.5 * league_strength.AWAY_HAIRCUT_FACTOR * factor, rel=1e-15
    )


# ---------------------------------------------------------------------------
# WP-P5 frozen production table + away-side haircut (2026-11-10)
# ---------------------------------------------------------------------------


def test_shipped_kappa_table_pins_pre_2025_26_values() -> None:
    """The production κ table is the WP-P5 canonical pre_2025_26
    freeze, verbatim from tables.pre_2025_26.kappa of
    sensitivity/canon_boot/two_season_report.json (sha 6ceadb6a…) —
    fit rule "obs kickoff day < 2025-09-16", leak-free, n_train=547.
    Rounded memo figures are NOT acceptable; the file's exact values
    are pinned here so a re-freeze or transcription slip fails."""
    assert dict(league_strength.KAPPA.kappa) == {
        "EPL": 0.021655,
        "La_liga": -0.071492,
        "Serie_A": -0.053532,
        "Bundesliga": -0.095116,
        "Ligue_1": -0.182982,
        "nuisance": -0.300105,
    }


def _capitalized_snapshot() -> dict[str, object]:
    """The bridge snapshot remapped to the capitalized league-block
    convention the frozen κ tables are keyed in (``Bundesliga_2026``,
    ``Ligue_1_2026`` — the production merged big-5 snapshot blocks;
    the WP-P2b/WP-P5 fits group leagues exactly so)."""
    remap = {HOF_LEAGUE: "Bundesliga", LYO_LEAGUE: "Ligue_1", "EPL": "EPL"}
    snapshot: dict[str, object] = {}
    for key, block in _bridge_snapshot().items():
        league, _, season = key.rpartition("_")
        snapshot[f"{remap.get(league, league)}_{season}"] = block
    return snapshot


def test_shipped_kappa_table_is_immutable_and_threads_through_path() -> None:
    """MappingProxyType discipline on the shipped table: item writes
    raise TypeError, and the table resolves real fixtures through the
    guarded modeling path (capitalized league keys, as keyed)."""
    with pytest.raises(TypeError):
        league_strength.KAPPA.kappa["EPL"] = 9.9  # type: ignore[index]
    with pytest.raises(TypeError):
        league_strength.KAPPA.kappa["Eredivisie"] = 0.0  # type: ignore[index]
    lambdas = soccer_model.match_lambdas(
        _capitalized_snapshot(), HOF_TEAM, "Bundesliga", LYO_TEAM, "Ligue_1",
        bridge=league_strength.KAPPA.kappa,
    )
    assert lambdas is not None
    # A league absent from the table still refuses the fixture (no
    # default κ): Eredivisie is not a KAPPA key.
    assert soccer_model.match_lambdas(
        _capitalized_snapshot(), HOF_TEAM, "Eredivisie", LYO_TEAM, "Ligue_1",
        bridge=league_strength.KAPPA.kappa,
    ) is None


def test_superseded_wp_p2b_table_retained_for_rollback() -> None:
    """Rollback provenance: the superseded WP-P2b table (in-sample
    n=835 fit, archived at round31/raw/league_strength_kappa.json)
    stays importable with its exact values, is documented as
    superseded, and is NOT the active production table."""
    assert dict(league_strength.SUPERSEDED_KAPPA_WP_P2B.kappa) == {
        "EPL": 0.126994,
        "La_liga": -0.086886,
        "Serie_A": -0.0961,
        "Bundesliga": -0.111525,
        "Ligue_1": -0.164953,
        "nuisance": -0.300105,
    }
    assert league_strength.SUPERSEDED_KAPPA_WP_P2B is not league_strength.KAPPA
    # The two tables genuinely differ (the swap took effect)...
    assert (
        dict(league_strength.SUPERSEDED_KAPPA_WP_P2B.kappa)
        != dict(league_strength.KAPPA.kappa)
    )
    # ...on the Big-5 strengths, while the capped nuisance term carries over.
    assert (
        dict(league_strength.SUPERSEDED_KAPPA_WP_P2B.kappa)["nuisance"]
        == dict(league_strength.KAPPA.kappa)["nuisance"]
        == -0.300105
    )
    # Same immutability discipline on the superseded table.
    with pytest.raises(TypeError):
        league_strength.SUPERSEDED_KAPPA_WP_P2B.kappa["EPL"] = 0.0  # type: ignore[index]
    # Both tables are first-class documented module constants.
    assert "KAPPA" in league_strength.__all__
    assert "SUPERSEDED_KAPPA_WP_P2B" in league_strength.__all__


def test_away_haircut_constant_and_factor_arithmetic() -> None:
    """The WP-P5 away haircut is exactly 0.06 and its factor exactly
    exp(-0.06) — hand-checkable: exp(-0.06) = 0.9417645335842487…"""
    assert league_strength.AWAY_HAIRCUT == 0.06
    assert league_strength.AWAY_HAIRCUT_FACTOR == pytest.approx(
        math.exp(-0.06), rel=1e-15
    )
    assert league_strength.AWAY_HAIRCUT_FACTOR == pytest.approx(
        0.9417645335842487, rel=1e-15
    )
    # The factor is the constant's exponential, wired not hardcoded.
    assert league_strength.AWAY_HAIRCUT_FACTOR == math.exp(
        -league_strength.AWAY_HAIRCUT
    )


def test_apply_away_haircut_arithmetic() -> None:
    """Hand-checkable corrector arithmetic at the exact 0.06: pure
    multiplication of the bridged away λ by exp(-0.06)."""
    # 1.5 * exp(-0.06) = 1.5 * 0.9417645335842487 = 1.4126468003763731…
    assert league_strength.apply_away_haircut(1.5) == pytest.approx(
        1.4126468003763731, rel=1e-12
    )
    # The asymmetric-snapshot pair's away rate 2.0 under the haircut.
    assert league_strength.apply_away_haircut(2.0) == pytest.approx(
        2.0 * math.exp(-0.06), rel=1e-15
    )
    # Pure multiplicative tilt: proportional in the input, zero fixed.
    assert (
        league_strength.apply_away_haircut(2.0)
        / league_strength.apply_away_haircut(1.0)
        == pytest.approx(2.0, rel=1e-15)
    )
    assert league_strength.apply_away_haircut(0.0) == 0.0
    # The clipped HOF-LYO away rate 10/7 haircut exactly as the
    # harness would (ba * exp(lc_a - kd - haircut)).
    assert league_strength.apply_away_haircut(10 / 7) == pytest.approx(
        (10 / 7) * math.exp(-0.06), rel=1e-15
    )


def test_bridged_away_haircut_composes_with_bridge_factor() -> None:
    """The full WP-P5 composition on the hand-computable snapshot,
    replicating the harness formula exactly:

        home_bridged = home_neutral * f            (NOT haircut)
        away_bridged = away_neutral / f * exp(-0.06)

    where f is the clamped bridge factor — the harness's
    ``bh * exp(lc_h + kd)`` / ``ba * exp(lc_a - kd - haircut)``.
    """
    snapshot = _bridge_snapshot()
    neutral = xg.pair_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
        last_weight=0.5, prior_games=8.0, bridge=NEUTRAL_BRIDGE,
    )
    bridged = xg.pair_lambdas(
        snapshot, HOF_TEAM, HOF_LEAGUE, LYO_TEAM, LYO_LEAGUE,
        last_weight=0.5, prior_games=8.0, bridge=STRONG_BUNDES_LIGA_BRIDGE,
    )
    assert neutral is not None and bridged is not None
    f = league_strength.bridge_factor(
        STRONG_BUNDES_LIGA_BRIDGE, HOF_LEAGUE, LYO_LEAGUE
    )
    assert f == pytest.approx(1.25)
    # Home side: tilted by f, never haircut.
    assert bridged[0] == pytest.approx(neutral[0] * f, rel=1e-12)
    # Away side: tilted by 1/f AND then haircut by exp(-0.06).
    assert league_strength.apply_away_haircut(bridged[1]) == pytest.approx(
        neutral[1] / f * math.exp(-0.06), rel=1e-12
    )
    # The haircut moves the away rate DOWN by exactly 5.8235…%:
    # 1 - exp(-0.06) = 0.05823546641575131.
    assert 1.0 - (
        league_strength.apply_away_haircut(bridged[1]) / bridged[1]
    ) == pytest.approx(0.05823546641575131, rel=1e-12)


def test_shipped_vs_superseded_table_fixture_displacement() -> None:
    """The swap's headline geometry: the OLD WP-P2b table drove EPL
    vs Ligue_1 to the clamp (Δκ = 0.291947 > ln 1.25), the NEW frozen
    table sits inside it (Δκ = 0.204637 < ln 1.25) — the shipped
    bridge is milder on the strongest pair and no longer saturates."""
    new_f = league_strength.bridge_factor(
        league_strength.KAPPA.kappa, "EPL", "Ligue_1"
    )
    old_f = league_strength.bridge_factor(
        league_strength.SUPERSEDED_KAPPA_WP_P2B.kappa, "EPL", "Ligue_1"
    )
    assert new_f == pytest.approx(math.exp(0.021655 - (-0.182982)), rel=1e-15)
    assert old_f == 1.25  # clamped under the superseded table
    assert new_f < old_f


# ---------------------------------------------------------------------------
# kappa_from_report: the pure payload twin (MO-5e)
# ---------------------------------------------------------------------------


def test_kappa_from_report_is_the_loaders_pure_twin(tmp_path, monkeypatch) -> None:
    """The in-memory rule and the file loader share one spelling: a
    report payload round-tripped through JSON gives the identical
    table, the payload path never opens a file, and the malformed
    cases raise the loader's own messages."""
    payload = {
        "generated_at": "2026-11-03T00:00:00Z",
        "anchor": "uefa_country_coefficients",
        "backtest": {"seasons": ["2023", "2024"], "status": "PASS"},
        "kappa": {"EPL": 0.021655, "Ligue_1": -0.182982, "nuisance": -0.300105},
    }
    path = tmp_path / "kappa_report.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    via_file = league_strength.load_kappa_report(str(path))
    via_payload = league_strength.kappa_from_report(payload)
    assert dict(via_file.kappa) == dict(via_payload.kappa)
    # Deeply immutable, same as every shipped table.
    with pytest.raises(TypeError):
        via_payload.kappa["EPL"] = 9.9  # type: ignore[index]
    # Pure: the payload path never touches the filesystem.
    monkeypatch.setattr(
        "builtins.open",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no file access")),
    )
    again = league_strength.kappa_from_report(payload)
    assert dict(again.kappa) == dict(via_payload.kappa)
    # Error parity with the file loader.
    with pytest.raises(ValueError, match='must contain a "kappa" JSON object'):
        league_strength.kappa_from_report({"generated_at": "x"})
    with pytest.raises(ValueError, match="root must be a JSON object"):
        league_strength.kappa_from_report([1, 2])  # type: ignore[arg-type]
    # bool and non-finite are invalid; numeric strings ARE coerced
    # by the loader rule (the pipeline bridge check is the layer
    # that rejects numeric strings, not the loader).
    for bad in (
        {"kappa": {"EPL": True}},
        {"kappa": {"EPL": float("nan")}},
        {"kappa": {"EPL": float("inf")}},
    ):
        with pytest.raises(ValueError, match="number"):
            league_strength.kappa_from_report(bad)
    assert dict(
        league_strength.kappa_from_report({"kappa": {"EPL": "0.1"}}).kappa
    ) == {"EPL": 0.1}
    assert "kappa_from_report" in league_strength.__all__
