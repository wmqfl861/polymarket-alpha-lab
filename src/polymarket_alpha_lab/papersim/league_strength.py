"""League-strength bridging for cross-league xG fixtures.

Single responsibility: hold the league -> kappa (log-strength) table
and compute the clamped multiplicative bridge factor that makes xG
multipliers from two different domestic leagues comparable. This is
the WP-P2a mechanism module: it consumes a kappa table (estimated and
anchored elsewhere) and exposes no estimation of its own. Since
2026-11-10 the production κ table is frozen IN this module
(:data:`KAPPA`, WP-P5 two-season revalidation) together with the
away-side corrector :data:`AWAY_HAIRCUT`; the superseded WP-P2b
table stays importable as :data:`SUPERSEDED_KAPPA_WP_P2B` for
rollback provenance.

Motivation (review/FIXES.md sections 5-6, 2026-10-30): domestic xG
attack / defense multipliers are measured relative to their OWN
league's average and are not cross-league comparable. Combining them
raw produced the confirmed HOF-LYO favorite-flip structural defect
(model made Lyon the favorite while the market correctly had
Hoffenheim). ``xg.pair_lambdas`` therefore returns ``None`` for
cross-league fixtures unless an explicit bridge table is supplied;
this module defines what that bridge means.

Model (frozen mechanism, κ values are calibrated separately):

- ``kappa[league]`` is the league's log-strength: a team from league L
  carries absolute attack quality scaled by ``exp(kappa_L)`` and
  concedes scaled by ``exp(-kappa_L)``. Within one league the two
  effects cancel at the league average, which is exactly why domestic
  multipliers need no bridge and why the same-league path of
  ``pair_lambdas`` never consults this module.
- For a cross-league fixture (home league X, away league Y) the home
  rate is multiplied by ``f = exp(clamp(kappa_X - kappa_Y, +-ln 1.25))``
  and the away rate divided by the same ``f``: the team from the
  STRONGER league (larger κ) is boosted on both sides of the ball. The
  sign is load-bearing — with Bundesliga κ above Ligue 1 κ (UEFA
  country-coefficient order at the 2026 freeze), the bridge moves the
  HOF-LYO fixture toward Hoffenheim, the direction the market showed.
- The clamp bounds the bridge displacement to a factor of 1.25 per
  side (edge ratio at most ``1.25**2 = 1.5625``): the κ table is now
  calibrated (WP-P5 two-season strict walk-forward, G2 PASS), and the
  clamp is retained as frozen policy so no re-estimation error can
  displace a modeled favorite by more than that (DELIVERY_PLAN
  known-gap #1: cap the away-side edge).
- The away side additionally carries the WP-P5 pre-registered
  corrector :data:`AWAY_HAIRCUT` = 0.06: the BRIDGED away λ is
  multiplied by ``exp(-AWAY_HAIRCUT)`` (see
  :func:`apply_away_haircut`). This mirrors the two-season harness
  corrector exactly (``ba * exp(lc_a - kd - haircut)``; the home side
  is untouched) and applies only to bridged cross-league rates — the
  same-league path never consults this module.

κ reference anchor: UEFA men's country coefficients
(:data:`UEFA_COEFFICIENT_REFERENCE_URL`) — higher coefficient means a
stronger league, so κ derived from it is monotone in league strength.
The anchor is documentation for the estimation passes (WP-P2b, then
the WP-P5 two-season refit that produced :data:`KAPPA`); this module
never fetches it and performs no network access.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

__all__ = [
    "AWAY_HAIRCUT",
    "AWAY_HAIRCUT_FACTOR",
    "BRIDGE_CLAMP_LOG",
    "BRIDGE_CLAMP_RATIO",
    "KAPPA",
    "LeagueStrengthTable",
    "SUPERSEDED_KAPPA_WP_P2B",
    "UEFA_COEFFICIENT_REFERENCE_URL",
    "apply_away_haircut",
    "bridge_factor",
    "kappa_from_report",
    "load_kappa_report",
    "load_kappa_table",
]

#: Maximum multiplicative bridge displacement per side: the clamp
#: bounds ``f`` to ``[1 / 1.25, 1.25]`` (frozen WP-P2a policy value —
#: calibration-free headroom until the walk-forward backtest lands).
BRIDGE_CLAMP_RATIO = 1.25

#: The same clamp on the κ-difference scale: ``ln(1.25)``.
BRIDGE_CLAMP_LOG = math.log(BRIDGE_CLAMP_RATIO)

#: Documentation anchor for the κ estimation pass (WP-P2b): UEFA
#: men's country coefficients, where a HIGHER coefficient means a
#: STRONGER league and κ must be monotone in it. Reference only —
#: this module performs no network access and embeds no coefficient
#: values.
UEFA_COEFFICIENT_REFERENCE_URL = (
    "https://www.uefa.com/nationalassociations/uefarankings/"
)

#: Away-side haircut ``h`` (WP-P5 corrector, shipped 2026-11-10 with
#: owner authorization "按你推荐的做" on the SWAP + FROZEN TABLE +
#: HAIRCUT 0.06 recommendation). The pre-registered family was
#: ``exp(-h)`` for ``h`` in {0.06, 0.08, 0.10}; 0.06 is the mildest.
#: Mechanics mirror the two-season harness corrector EXACTLY
#: (``~/paper-sim/review/wp_p5/two_season/two_season_revalidate.py``,
#: ``eval_variant``: away ``ba * exp(lc_a - kd - haircut)``, home
#: side untouched): the bridged away λ is multiplied by
#: ``exp(-AWAY_HAIRCUT)`` via :func:`apply_away_haircut`. It applies
#: ONLY to bridged cross-league away rates; the same-league path
#: never consults it. Provenance: WP-P5 canonical run + bootstrap
#: (``~/paper-sim/review/wp_p5/two_season/sensitivity/canon_boot/
#: two_season_report.json``, sha 6ceadb6a…): pooled T2 0.05069 ->
#: 0.01785 with bootstrap replicate pass 0.76 -> 0.94; the fragile
#: 2025-26 season T2 0.07900 -> 0.03828; cost 1.6% of the pooled T1
#: edge (-0.00944 -> -0.00929); no gate regression anywhere.
AWAY_HAIRCUT = 0.06

#: ``exp(-AWAY_HAIRCUT)`` — the multiplicative factor applied to the
#: bridged away λ (0.9417645335842487 for the shipped 0.06 haircut).
AWAY_HAIRCUT_FACTOR = math.exp(-AWAY_HAIRCUT)


@dataclass(frozen=True)
class LeagueStrengthTable:
    """Immutable league-key -> κ (log-strength) table.

    ``kappa`` maps Understat league keys (``"EPL"``, ``"bundesliga"``,
    ``"ligue_1"``, ...) to finite floats. Pass ``table.kappa`` where a
    ``Mapping[str, float]`` bridge is expected (``xg.pair_lambdas`` and
    the ``soccer_model`` threading).

    Deep immutability (codex review 2026-11-03, minor 4): the mapping
    is copied at construction and stored as a read-only
    :class:`types.MappingProxyType` view, so neither mutating the
    caller's source dict after construction nor writing through
    ``table.kappa`` can change the table. Item assignment on
    ``table.kappa`` raises ``TypeError``.
    """

    kappa: Mapping[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "kappa", MappingProxyType(dict(self.kappa)))


#: FROZEN production league-strength κ table (WP-P5 two-season
#: revalidation; shipped 2026-11-10 by owner authorization
#: "按你推荐的做" on the SWAP + FROZEN TABLE + HAIRCUT 0.06
#: recommendation).
#:
#: Provenance — exact values are ``tables.pre_2025_26.kappa`` of the
#: canonical two-season run + kappa bootstrap
#: (``~/paper-sim/review/wp_p5/two_season/sensitivity/canon_boot/
#: two_season_report.json``, sha 6ceadb6a…; decision memo
#: ``sensitivity/decision_memo.md``, 2026-11-10):
#:
#: - Two-season zero-placeholder refit on UCL/UEL FT matches
#:   (2023/24 + 2024/25 FotMob backfill for club multipliers,
#:   2025/26 Understat for p2b evaluation continuity); fit rule
#:   ``"obs kickoff day < 2025-09-16"``, leak-free, n_train=547,
#:   3 UEFA-coefficient ridge columns.
#: - Strict time-ordered T1-T5 gate protocol; the canonical run is
#:   the formal G2 PASS (both verdicts, full-mode 3552 s, T5
#:   determinism).
#: - Provider-invariant: the frozen candidate tables are byte-wise
#:   identical under the mixed-source and all-FotMob source maps
#:   (max κ shift 0.000000; no 2025-26 observation enters the frozen
#:   fit), so the swap carries no G1 provider risk.
#: - Bootstrap 95% CIs (B=300 match-resample refits, pre_2025_26):
#:   EPL +0.0217 [-0.0658, +0.1737]; La_liga -0.0715 [-0.1609,
#:   +0.0571]; Serie_A -0.0535 [-0.1281, +0.0939]; Bundesliga -0.0951
#:   [-0.1850, +0.0382]; Ligue_1 -0.1830 [-0.2798, -0.0411];
#:   nuisance -0.3001 [-0.3001, -0.1650] (capped: the -0.300105
#:   point equals the fit cap 0.30010459245033816).
#: - Re-freeze cadence: per the report's G2 rule the table is frozen
#:   pre-season each year (pre_2024_25 is the prior freeze, retained
#:   in the report; ``full_through_2026_01_30_IN_SAMPLE`` there is a
#:   reference fit, NOT shippable).
#:
#: Thread ``bridge=KAPPA.kappa`` through the soccer-modeling entry
#: points; the mapping is deeply immutable (read-only view; mutation
#: raises ``TypeError``). Keys follow the capitalized league-group
#: convention of the production merged big-5 snapshot blocks
#: (``EPL_2026``, ``La_liga_2026``, ``Serie_A_2026``,
#: ``Bundesliga_2026``, ``Ligue_1_2026``) that the WP-P2b/WP-P5 fits
#: grouped on; a lowercase Understat-style league key will NOT resolve
#: and the fixture is refused.
KAPPA = LeagueStrengthTable(
    kappa={
        "EPL": 0.021655,
        "La_liga": -0.071492,
        "Serie_A": -0.053532,
        "Bundesliga": -0.095116,
        "Ligue_1": -0.182982,
        "nuisance": -0.300105,
    }
)

#: SUPERSEDED WP-P2b κ table — retained for ROLLBACK PROVENANCE
#: ONLY; never thread it as the production bridge. It was the
#: production table from 2026-11-06 (WP-P2b validation) until the
#: WP-P5 swap on 2026-11-10, archived verbatim at
#: ``~/paper-sim/round31/raw/league_strength_kappa.json`` (server
#: pal-dev): an in-sample fit over the full three-season
#: UCL/UEL window (n=835, ridge_lambda 15, UEFA-coefficient ridge
#: targets, nuisance at the 0.300105 cap) that reused the Understat
#: 2025/26 club multipliers for the earlier seasons. The WP-P5 refit
#: (:data:`KAPPA`) replaced it because it is two-season
#: zero-placeholder, strictly time-ordered (leak-free freeze) and
#: provider-invariant. Deeply immutable, same as the shipped table.
SUPERSEDED_KAPPA_WP_P2B = LeagueStrengthTable(
    kappa={
        "EPL": 0.126994,
        "La_liga": -0.086886,
        "Serie_A": -0.0961,
        "Bundesliga": -0.111525,
        "Ligue_1": -0.164953,
        "nuisance": -0.300105,
    }
)


def _validated_kappa_entry(league: object, value: object) -> float:
    """Validate one ``league -> κ`` entry; the single shared rule.

    A κ must be a finite real number. ``bool`` is rejected explicitly
    even though ``bool`` subclasses ``int`` (``float(True) == 1.0``
    would silently pass a JSON ``true`` through as a strength), and
    NaN/Inf fail :func:`math.isfinite` — a NaN κ would otherwise
    propagate through the clamp (``max``/``min`` comparisons are False
    against NaN) to the positive clamp limit as a silently plausible
    1.25 factor (codex review 2026-11-03, major 1). Raises
    ``ValueError`` naming the offending league.
    """
    if isinstance(value, bool):
        raise ValueError(
            f"kappa for league {league!r} must be a finite number, got {value!r} (bool)"
        )
    try:
        entry = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"kappa for league {league!r} must be a number, got {value!r}"
        ) from exc
    if not math.isfinite(entry):
        raise ValueError(
            f"kappa for league {league!r} must be a finite number, got {value!r}"
        )
    return entry


def load_kappa_table(path: str) -> LeagueStrengthTable:
    """Load a κ table from a flat JSON object file ``{"<league>": kappa}``.

    Values must be finite numbers. Raises ``ValueError`` for a
    non-object root, non-numeric, boolean or non-finite κ values; the
    loader never touches the network.
    """
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("league kappa table root must be a JSON object")
    kappa: dict[str, float] = {}
    for league, value in payload.items():
        kappa[str(league)] = _validated_kappa_entry(league, value)
    return LeagueStrengthTable(kappa=kappa)


def kappa_from_report(payload: Mapping[str, object]) -> LeagueStrengthTable:
    """Extract a κ table from an in-memory estimation report mapping.

    Pure-payload twin of :func:`load_kappa_report` (MO-5e: the
    league-bridge family binds already-produced report artifacts
    without file I/O). ``payload`` is the parsed report object whose
    ``"kappa"`` member holds the league -> κ mapping; the
    validation, error messages and immutability guarantees are the
    file loader's, verbatim. Raises ``ValueError`` when the root is
    not a mapping, the ``"kappa"`` member is missing or not a
    mapping, or any κ entry is non-numeric, boolean or non-finite.
    No network access, no file access.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("league kappa report root must be a JSON object")
    nested = payload.get("kappa")
    if not isinstance(nested, Mapping):
        raise ValueError(
            'league kappa report must contain a "kappa" JSON object, got '
            f"{type(nested).__name__}"
        )
    kappa: dict[str, float] = {}
    for league, value in nested.items():
        kappa[str(league)] = _validated_kappa_entry(league, value)
    return LeagueStrengthTable(kappa=kappa)


def load_kappa_report(path: str) -> LeagueStrengthTable:
    """Load a κ table from a WP-P2b nested estimation report.

    The estimation pass (WP-P2b) writes a JSON report object whose
    ``"kappa"`` member holds the league -> κ mapping, alongside its own
    metadata (backtest window, anchor, diagnostics), e.g.::

        {
          "generated_at": "2026-11-03T00:00:00Z",
          "anchor": "uefa_country_coefficients",
          "backtest": {"seasons": ["2023", "2024"], "status": "PASS"},
          "kappa": {"bundesliga": 0.05, "ligue_1": 0.0}
        }

    This loader extracts the ``"kappa"`` sub-object (the flat
    :func:`load_kappa_table` path stays available for a standalone
    flat config file); the parsing itself is
    :func:`kappa_from_report` over the ``json.load`` payload, so the
    in-memory and file paths share one rule. Raises ``ValueError``
    when the root is not an object, the ``"kappa"`` member is
    missing or not an object, or any κ entry is non-numeric,
    boolean or non-finite. No network access.
    """
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return kappa_from_report(payload)


def bridge_factor(
    kappa: Mapping[str, float],
    home_league: str,
    away_league: str,
) -> float | None:
    """Clamped league-strength bridge ``f`` for one cross-league fixture.

    ``f = exp(clamp(kappa[home_league] - kappa[away_league], -ln 1.25, ln 1.25))``:
    the home rate is multiplied by ``f`` and the away rate divided by
    it, so the team from the stronger (larger-κ) league is boosted on
    both sides. Returns ``None`` when either league key is missing
    from ``kappa`` — an unknown league has no calibrated strength, and
    the caller must refuse the fixture rather than bridge through a
    default.

    Both selected κ values are validated (finite real numbers, no
    booleans) BEFORE any arithmetic: a NaN or infinite κ raises
    ``ValueError`` naming the offending league instead of leaking
    through the clamp — a NaN delta compares False against both clamp
    bounds, so it would otherwise surface as a silently plausible
    factor of exactly ``1.25`` (codex review 2026-11-03, major 1).
    """
    if home_league not in kappa or away_league not in kappa:
        return None
    kappa_home = _validated_kappa_entry(home_league, kappa[home_league])
    kappa_away = _validated_kappa_entry(away_league, kappa[away_league])
    delta = kappa_home - kappa_away
    clamped = max(-BRIDGE_CLAMP_LOG, min(BRIDGE_CLAMP_LOG, delta))
    return math.exp(clamped)


def apply_away_haircut(lambda_away: float) -> float:
    """Apply the WP-P5 away-side haircut to one bridged away λ.

    Returns ``lambda_away * AWAY_HAIRCUT_FACTOR`` — the exact mirror
    of the two-season harness corrector (``eval_variant`` in
    ``two_season_revalidate.py``: away ``ba * exp(lc_a - kd -
    haircut)``, i.e. the bridged away rate divided by the bridge
    factor and THEN multiplied by ``exp(-0.06)``; the home rate is
    never haircut). Apply it to the AWAY λ of a bridged cross-league
    pair only; same-league rates are out of scope by construction.

    Kept a pure multiplication on purpose (no clamping, no further
    validation): the harness applies the corrector at exactly this
    point of the arithmetic and nothing else.
    """
    return float(lambda_away) * AWAY_HAIRCUT_FACTOR
