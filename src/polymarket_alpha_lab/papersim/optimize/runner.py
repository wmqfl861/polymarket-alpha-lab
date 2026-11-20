"""MO-6 optimize runner: family adapters, pure core, and the write shell.

Design 04 sections 5-11; plan 05 node MO-6 (plan r2 section 1.2).
Layering:

- ``RUN_RULES_V1`` — the registered rule-v1 numbers (DELIVERY_PLAN
  80.7, registration day 2026-11-20, frozen).  MO-6 executes the
  STAGE-1 subset; the stage-2 numbers are transcribed verbatim so
  MO-8's ``policy.py`` has one in-repo source to converge against
  (the closing pin lives in tests/test_papersim_optimize_runner_rules.py).
- Family adapters — the per-family contract layer: each model family
  has its own history/fixture payload (MO-5), so one adapter per
  family folds them into the uniform ``RunEvent`` stream plus
  ``rows()`` scoring.  Adapters are pure; model modules import lazily
  inside methods so importing this module loads no model code.
- ``run_optimize_core`` — the deterministic core: folds, nested
  selection, one-SE shrink, holdout paired comparison, the
  three-condition registration gate, Holm, the 28-day re-submission
  block, and the two registry branches (branch A: an empty or
  unreadable registry evaluates on the family baseline with NO
  registration capability; branch B: a selected winner on an
  uninitialized lineage fails closed as RUN_FAILED with no half
  registration).  No I/O; wall-clock enters only through the budget
  guard and never into any output byte.
- ``run_and_record`` — the write shell: receipts, snapshot
  registration, the optimization_runs row, optimizer specs and
  CANDIDATE role events with the pre-registered forward plan.  Every
  write is content-idempotent, so a crash between writes heals by
  re-running the same command with a bumped attempt number.

Paper-only: no order, account or credential surface anywhere.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import statistics
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from polymarket_alpha_lab.papersim import store
from polymarket_alpha_lab.papersim.optimize import search
from polymarket_alpha_lab.papersim.optimize.metrics import (
    ScoredEvent,
    cluster_mean_ci,
    holm,
    multiclass_log_loss,
    sign_flip_pvalue,
)
from polymarket_alpha_lab.papersim.optimize.registry import lineage_id
from polymarket_alpha_lab.papersim.optimize.scorecard import (
    build_scorecard,
    gate_d_walkforward,
    gate_e_market,
    gate_results,
)
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec, ParamDef
from polymarket_alpha_lab.papersim.optimize.walkforward import (
    WalkFold,
    expanding_matchday_folds,
)

__all__ = (
    "ADAPTERS",
    "RUN_PROFILES",
    "RUN_RULES_V1",
    "DataIntegrityError",
    "EventRows",
    "RegisteredInput",
    "RegistryLineageMissing",
    "RegistryView",
    "RowOutcome",
    "RowScore",
    "RunEvent",
    "RunProfile",
    "RunResult",
    "families_summary",
    "family_singleton",
    "read_club_archive",
    "resubmission_blocked",
    "resolve_run_inputs",
    "run_and_record",
    "run_optimize_core",
    "status_report",
)

UTC = dt.timezone.utc

#: Rule version string written into every run row and receipt context.
RULE_VERSION = "v1"

#: Stage-1 (holdout) minimums, alpha, the 0.8 envelope ratio, the 28-day
#: re-submission block and the rule-acceptance rates are REGISTERED
#: numbers (DELIVERY_PLAN 80.7, registration day 2026-11-20).  The
#: stage-2 / forward numbers below them are transcribed verbatim for
#: MO-8's policy.py convergence and are NOT enforced by MO-6.
#:
#: OPERATIONALIZATIONS — this block is the frozen rules-v1 reading of
#: 80.7's wording; revising any of them follows the same number-change
#: process as the numbers themselves (a node plan, a review, and a
#: re-registration); they are not tunables:
#: - Condition B "no calibration or validation-gate regression" means:
#:   the candidate scorecard has NO gate verdict that is FAIL while the
#:   incumbent's same (gate, check) verdict is not FAIL, AND the
#:   candidate calibration ECE is not greater than the incumbent's
#:   (both scorecards are built by build_scorecard on the SAME holdout
#:   events; None / NOT_ASSESSABLE is recorded and never blocks; the
#:   binary-only slope/intercept fields are descriptive in v1 and never
#:   gate).
#: - Condition C "the envelope must not narrow" means: the candidate's
#:   median per-row variant spread (each model's own frozen max-minus-min
#:   statistic over its envelope) on the holdout rows is at least 0.8x
#:   the incumbent's on the same rows.  Below the ratio the candidate is
#:   NOT auto-registered (owner decision); the numbers stay on the
#:   scorecard.  Empty-envelope lineages (intl-elo-goal-residuals,
#:   crypto-gbm): ``envelope == ()`` is the only legal spec shape (the
#:   family layers already enforce it), spread is undefined (None) and
#:   the gate records NOT_ASSESSABLE without blocking — there are no
#:   variants that could narrow.
#: - Condition A "CI upper bound < 0, Holm-corrected across lineages"
#:   means: the cluster-bootstrap 95% upper bound of the holdout paired
#:   mean (candidate minus incumbent) is below zero AND the lineage's
#:   sign-flip p-value is rejected by metrics.holm at alpha 0.05 over
#:   the lineages of this run that reached the holdout with sample
#:   floors met (insufficient lineages cannot register and do not enter
#:   the correction).
#: - The 28-day block counts from the PRIOR run's data cutoff (the
#:   deterministic evidence time), never from the writing clock.
RUN_RULES_V1: Mapping[str, Any] = {
    "rule_version": RULE_VERSION,
    "alpha": 0.05,
    "envelope_width_ratio": 0.8,
    "resubmission_days": 28,
    "fp_rate_max": 0.05,
    "detection_min": 0.80,
    "stage1_holdout_minimums": {
        "club-soccer-recalib": {"min_events": 300, "min_blocks": 20},
        "club-soccer-frozen": {"min_events": 300, "min_blocks": 20},
        "intl-elo-poisson": {"min_events": 300, "min_blocks": 0},
        "intl-elo-goal-residuals": {"min_events": 300, "min_blocks": 0},
        "nba-pace-efficiency": {"min_events": 300, "min_blocks": 30},
        "crypto-gbm": {"min_events": 150, "min_blocks": 20},
    },
    "stage2_forward_minimums": {
        "club-soccer(ou/btts/tt)": {"min_events": 150, "min_blocks": 2},
        "club-soccer(match_winner)": {"min_events": 300, "min_blocks": 2},
        "nba": {"min_events": 120, "min_blocks": 14},
        "intl": {"min_events": 60, "min_blocks": 2},
        "crypto": {"min_events": 60, "min_blocks": 28},
    },
    "max_shadow_windows": {
        "club": "12w", "nba": "10w", "intl": "12mo", "crypto": "6mo",
    },
    "promote_ci_upper_max": 0.005,
    "first_look_ci_tighten": "99pct",
}

#: Gate-D min_events conventions quoted from the scorecard module
#: docstring (scorecard.py gate_d_walkforward: "90 football / 20 NBA");
#: international lineages take the football class.  Descriptive input
#: to condition B, not a registered rule number.
GATE_D_MIN_EVENTS: Mapping[str, int] = {
    "club-soccer-recalib": 90,
    "club-soccer-frozen": 90,
    "intl-elo-poisson": 90,
    "intl-elo-goal-residuals": 90,
    "nba-pace-efficiency": 20,
    "crypto-gbm": 20,
}

#: Frozen family-specific scoring constants (published-study口径).
CLUB_TOTAL_LINES = (1.5, 2.5, 3.5)
INTL_TOTALS_LINE = 2.5
NBA_TOTALS_LINE = 225.5

OUTCOME_CANDIDATE = "CANDIDATE_REGISTERED"
OUTCOME_NO_BETTER = "NO_IMPROVEMENT"
OUTCOME_INSUFFICIENT = "INSUFFICIENT_DATA"
OUTCOME_FAILED = "RUN_FAILED"

LOGLOSS_FLOOR = 1e-12


# ---------------------------------------------------------------------------
# Run inputs, events and the adapter protocol
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RegisteredInput:
    """One input file's exact bytes plus its snapshot registration kind."""

    relpath: str
    kind: str
    content: bytes

    @property
    def content_sha256(self) -> str:
        return sha256(self.content).hexdigest()


@dataclass(frozen=True)
class RowOutcome:
    """One scoreable row an event declares (target x line)."""

    target: str
    line_key: str
    outcome_index: int


@dataclass(frozen=True)
class RunEvent:
    """One scoreable historical event; satisfies walkforward.TimedRecord."""

    event_key: str
    group_id: str
    kickoff: dt.datetime
    available_at: dt.datetime
    fixture: Any = None
    outcomes: tuple[RowOutcome, ...] = ()

    def outcomes_for(self, target: str) -> tuple[RowOutcome, ...]:
        return tuple(row for row in self.outcomes if row.target == target)


@dataclass(frozen=True)
class RowScore:
    """One predicted row: probabilities aligned with the declared outcome."""

    line_key: str
    probabilities: tuple[float, ...]
    variant_spread: float | None


@dataclass(frozen=True)
class EventRows:
    """All rows of one event for one target, or the refusal reason."""

    event_key: str
    refusal: str | None
    rows: tuple[RowScore, ...]


class FamilyRunAdapter:
    """The per-family contract layer (structural; no inheritance needed)."""

    family_id: str = ""
    memoize: bool = False

    def param_defs(self) -> tuple[ParamDef, ...]:
        raise NotImplementedError

    def targets(self) -> tuple[str, ...]:
        raise NotImplementedError

    def spec_for(self, params: Mapping[str, object]) -> ModelSpec:
        raise NotImplementedError

    def load(self, inputs: Sequence[RegisteredInput]) -> Any:
        raise NotImplementedError

    def events(self, payload: Any) -> tuple[RunEvent, ...]:
        raise NotImplementedError

    def folds(
        self, payload: Any
    ) -> tuple[tuple[WalkFold, ...], tuple[dict, ...], tuple[Any, ...]]:
        """(walk-forward folds, skipped-block notes, history tokens)."""

        raise NotImplementedError

    def history_for(self, payload: Any, token: Any) -> Any:
        raise NotImplementedError

    def rows(
        self,
        payload: Any,
        history: Any,
        history_key: str,
        spec: ModelSpec,
        target: str,
        event: RunEvent,
        *,
        include_variants: bool,
    ) -> EventRows:
        raise NotImplementedError


@dataclass(frozen=True)
class RunProfile:
    """Frozen per-family run profile (no CLI knob may move these)."""

    family_id: str
    fold_kind: str                       # "mo4_rounds" | "matchday"
    holdout_start_utc: dt.datetime       # kickoff_not_before semantics
    grid_overrides: Mapping[str, tuple] = field(default_factory=dict)
    max_seconds: float | None = 7200.0
    max_trials: int | None = 20000


# ---------------------------------------------------------------------------
# Family adapters
# ---------------------------------------------------------------------------

_FIT_CACHES: dict[str, dict] = {}


def _fit_cached(family_id: str, key, build):
    cache = _FIT_CACHES.setdefault(family_id, {})
    if key not in cache:
        cache[key] = build()
    return cache[key]


class _ClubSoccerRecalibAdapter(FamilyRunAdapter):
    """club-soccer-recalib: the WP-P4 archive -> MO-4 folds -> recalib."""

    family_id = "club-soccer-recalib"
    memoize = False

    def _family(self):
        from polymarket_alpha_lab.papersim.optimize.families.soccer_recalib import (
            SOCCER_RECALIB,
        )

        return SOCCER_RECALIB

    def param_defs(self):
        return self._family().param_space()

    def targets(self):
        from polymarket_alpha_lab.papersim.optimize.families.soccer_recalib import (
            TARGETS,
        )

        return TARGETS

    def spec_for(self, params):
        from polymarket_alpha_lab.papersim.optimize.families.soccer_recalib import (
            spec_for_params,
        )

        return spec_for_params(params)

    def load(self, inputs):
        from polymarket_alpha_lab.papersim.optimize.dataset import history_from_archive

        files = {item.relpath: item.content for item in inputs}
        return {"audit": history_from_archive(files)}

    def _mo4_folds(self, payload):
        if "mo4" not in payload:
            from polymarket_alpha_lab.papersim.optimize.dataset import build_folds

            payload["mo4"] = build_folds(payload["audit"])
        return payload["mo4"]

    def events(self, payload):
        mo4_folds, _ = self._mo4_folds(payload)
        events = {}
        for fold in mo4_folds:
            for match in fold.test:
                outcome = (
                    0 if match.home_goals > match.away_goals
                    else 1 if match.home_goals == match.away_goals
                    else 2
                )
                rows = [RowOutcome("match_winner", "1x2", outcome)]
                total = int(match.home_goals) + int(match.away_goals)
                for line in CLUB_TOTAL_LINES:
                    rows.append(
                        RowOutcome(
                            "over_under", f"over-{line}", 1 if total > line else 0
                        )
                    )
                events[match.match_id] = RunEvent(
                    event_key=match.match_id,
                    group_id=f"{match.league}:{match.season}:round-{match.matchweek}",
                    kickoff=match.kickoff,
                    available_at=match.available_at or match.kickoff,
                    fixture=match,
                    outcomes=tuple(rows),
                )
        return tuple(sorted(events.values(), key=lambda e: (e.kickoff, e.event_key)))

    def folds(self, payload):
        mo4_folds, skipped = self._mo4_folds(payload)
        walk = []
        tokens = []
        for fold in mo4_folds:
            walk.append(
                WalkFold(
                    block_id=f"{fold.league}:{fold.season}:round-{fold.matchweek}",
                    cutoff=fold.cutoff,
                    train_keys=tuple(m.match_id for m in fold.train),
                    test_keys=tuple(m.match_id for m in fold.test),
                    train_block_ids=(),
                )
            )
            tokens.append(fold)
        return (tuple(walk), tuple(dict(item) for item in skipped), tuple(tokens))

    def history_for(self, payload, token):
        from polymarket_alpha_lab.papersim.optimize.dataset import to_model_history

        return to_model_history(token)

    def rows(self, payload, history, history_key, spec, target, event, *, include_variants):
        from polymarket_alpha_lab.papersim import recalib
        from polymarket_alpha_lab.papersim.optimize.families.soccer_recalib import (
            envelope_specs,
        )

        family = self._family()
        fitted = _fit_cached(
            self.family_id, (history_key, spec.spec_sha256()),
            lambda: family.fit(history, spec, history.as_of),
        )
        base = self._predict_rows(fitted, event.fixture, target)
        if base is None:
            return EventRows(event.event_key, "model_refusal", ())
        if not include_variants:
            return EventRows(event.event_key, None, base)
        variant_rows = []
        for variant_spec in envelope_specs(spec):
            variant_fitted = _fit_cached(
                self.family_id, (history_key, variant_spec.spec_sha256()),
                lambda bound_spec=variant_spec: family.fit(
                    history, bound_spec, history.as_of
                ),
            )
            variant_rows.append(
                self._predict_rows(variant_fitted, event.fixture, target) or base
            )
        spreads = self._variant_spreads(base, variant_rows, target)
        rows = tuple(
            RowScore(row.line_key, row.probabilities, spread)
            for row, spread in zip(base, spreads)
        )
        return EventRows(event.event_key, None, rows)

    def _predict_rows(self, fitted, match, target):
        from polymarket_alpha_lab.papersim import recalib

        fixture = recalib.Fixture(
            league=match.league,
            season=match.season,
            matchweek=match.matchweek,
            kickoff=match.kickoff,
            home_team=match.home_team,
            away_team=match.away_team,
        )
        prediction = self._family().predict(fitted, fixture)
        if prediction is None:
            return None
        if target == "match_winner":
            # The WP-P4 published scoring convention normalizes the
            # categorical MEDIAN triple (the median of three variant
            # triples need not sum to one); the sanity preconditions
            # below refuse anything that is not a finite positive
            # triple — normalization is never a silent repair.
            raw = (prediction.home_win, prediction.draw, prediction.away_win)
            if not all(math.isfinite(value) and value > 0.0 for value in raw):
                raise DataIntegrityError(
                    "invalid_probability_vector",
                    f"recalib 1X2 median not finite-positive: {raw!r}",
                )
            total = math.fsum(raw)
            if not math.isfinite(total) or total <= 0.0:
                raise DataIntegrityError(
                    "invalid_probability_vector",
                    f"recalib 1X2 median mass not finite-positive: {total!r}",
                )
            return (RowScore("1x2", tuple(value / total for value in raw), None),)
        rows = []
        for line in CLUB_TOTAL_LINES:
            over = prediction.over_probability(line)
            rows.append(RowScore(f"over-{line}", (1.0 - over, over), None))
        return tuple(rows)

    def _variant_spreads(self, base, variant_rows, target):
        spreads = []
        classes = 3 if target == "match_winner" else 2
        for index, _row in enumerate(base):
            per_class = []
            for klass in range(classes):
                values = [
                    variant[index].probabilities[klass] for variant in variant_rows
                ]
                per_class.append(max(values) - min(values))
            spreads.append(max(per_class))
        return spreads


class _IntlEloAdapter(FamilyRunAdapter):
    """intl-elo-poisson: one frozen snapshot; per-fixture date barrier."""

    family_id = "intl-elo-poisson"
    memoize = True
    _tournament_prefix = "UEFA Nations League"

    def _family(self):
        from polymarket_alpha_lab.papersim.optimize.families.intl import INTL_ELO

        return INTL_ELO

    def param_defs(self):
        return self._family().param_space()

    def targets(self):
        from polymarket_alpha_lab.papersim.optimize.families.intl import TARGETS_ELO

        return TARGETS_ELO

    def spec_for(self, params):
        return self._family().spec_with(params)

    def load(self, inputs):
        return {"snapshot": _json_input(inputs, "intl_snapshot")}

    def _finished_events(self, payload):
        events = []
        for match in payload["snapshot"].get("matches", ()):
            if match.get("home_goals") is None or match.get("away_goals") is None:
                continue
            tournament = str(match.get("tournament", ""))
            if not tournament.startswith(self._tournament_prefix):
                continue
            date = str(match["date"])
            kickoff = dt.datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
            events.append((match, date, kickoff))
        return events

    def folds(self, payload):
        events = self.events(payload)
        folds, skipped = expanding_matchday_folds(events)
        return (
            folds,
            tuple({"block_id": item.block_id, "reason": item.reason} for item in skipped),
            tuple(None for _ in folds),
        )

    def history_for(self, payload, token):
        from polymarket_alpha_lab.papersim.optimize.families.intl import IntlHistory

        return IntlHistory(snapshot=payload["snapshot"])

    def _fixture(self, match):
        from polymarket_alpha_lab.papersim import intl_model
        from polymarket_alpha_lab.papersim.optimize.families.intl import (
            IntlFamilyFixture,
        )

        return IntlFamilyFixture(
            fixture=intl_model.IntlFixture(
                home=match["home"],
                away=match["away"],
                date=match["date"],
                neutral=bool(match.get("neutral", False)),
            ),
            tournament=match.get("tournament"),
        )


class _IntlEloPoissonAdapter(_IntlEloAdapter):
    """The nine-variant Elo->Poisson match-winner adapter."""

    def events(self, payload):
        events = []
        for match, date, kickoff in self._finished_events(payload):
            outcome = (
                0 if match["home_goals"] > match["away_goals"]
                else 1 if match["home_goals"] == match["away_goals"]
                else 2
            )
            events.append(
                RunEvent(
                    event_key=f"intl:{date}:{match['home']}:{match['away']}",
                    group_id=date,
                    kickoff=kickoff,
                    available_at=kickoff,
                    fixture=match,
                    outcomes=(RowOutcome("match_winner", "1x2", outcome),),
                )
            )
        return tuple(sorted(events, key=lambda e: (e.kickoff, e.event_key)))

    def rows(self, payload, history, history_key, spec, target, event, *, include_variants):
        family = self._family()
        fitted = family.fit(history, spec, event.kickoff)
        prediction = family.predict(fitted, self._fixture(event.fixture))
        if prediction is None:
            return EventRows(event.event_key, "model_refusal", ())
        spread = max(
            prediction.p_home_variant_spread,
            prediction.p_draw_variant_spread,
            prediction.p_away_variant_spread,
        )
        row = RowScore(
            "1x2",
            (prediction.p_home, prediction.p_draw, prediction.p_away),
            spread,
        )
        return EventRows(event.event_key, None, (row,))


class _IntlTotalsAdapter(_IntlEloAdapter):
    """intl-elo-goal-residuals: O/U 2.5 point model; EMPTY envelope."""

    family_id = "intl-elo-goal-residuals"
    memoize = True

    def _family(self):
        from polymarket_alpha_lab.papersim.optimize.families.intl import INTL_TOTALS

        return INTL_TOTALS

    def param_defs(self):
        return self._family().param_space()

    def targets(self):
        from polymarket_alpha_lab.papersim.optimize.families.intl import TARGETS_TOTALS

        return TARGETS_TOTALS

    def spec_for(self, params):
        return self._family().spec_with(params)

    def events(self, payload):
        events = []
        for match, date, kickoff in self._finished_events(payload):
            total = int(match["home_goals"]) + int(match["away_goals"])
            events.append(
                RunEvent(
                    event_key=f"intl:{date}:{match['home']}:{match['away']}",
                    group_id=date,
                    kickoff=kickoff,
                    available_at=kickoff,
                    fixture=match,
                    outcomes=(RowOutcome(
                        "over_under", f"over-{INTL_TOTALS_LINE}",
                        1 if total > INTL_TOTALS_LINE else 0,
                    ),),
                )
            )
        return tuple(sorted(events, key=lambda e: (e.kickoff, e.event_key)))

    def rows(self, payload, history, history_key, spec, target, event, *, include_variants):
        family = self._family()
        fitted = _fit_cached(
            self.family_id, spec.spec_sha256(),
            lambda: family.fit(
                history, spec, dt.datetime(2026, 1, 1, tzinfo=UTC)
            ),
        )
        quote = family.predict(fitted, self._fixture(event.fixture))
        row = RowScore(
            f"over-{INTL_TOTALS_LINE}",
            (quote.p_under25, quote.p_over25),
            None,  # empty-envelope lineage: spread undefined (rules v1)
        )
        return EventRows(event.event_key, None, (row,))


class _NbaAdapter(FamilyRunAdapter):
    """nba-pace-efficiency: one frozen ESPN snapshot; per-game date barrier."""

    family_id = "nba-pace-efficiency"
    memoize = True

    def _family_module(self):
        from polymarket_alpha_lab.papersim.optimize.families import nba as module

        return module

    def _family(self):
        return self._family_module().NBA_PACE_EFFICIENCY

    def param_defs(self):
        return self._family().param_space()

    def targets(self):
        from polymarket_alpha_lab.papersim.optimize.families.nba import TARGETS

        return TARGETS

    def spec_for(self, params):
        # MO-5c published no public spec constructor (registered debt);
        # the envelope is derived exactly as baseline_spec() derives it
        # and family.fit refuses any mismatch, so the round trip through
        # fit is the pin (tests/test_papersim_optimize_runner.py).
        module = self._family_module()
        family = module.NBA_PACE_EFFICIENCY
        checked = {
            defn.name: defn.default for defn in family.param_space()
        }
        checked.update(dict(params))
        return ModelSpec(
            family=module.FAMILY_ID,
            procedure_version=module.PROCEDURE_VERSION,
            params=checked,
            envelope=module._envelope_mappings(float(checked["half_life"])),
            data_contract=module.DATA_CONTRACT,
        )

    def load(self, inputs):
        return {"snapshot": _json_input(inputs, "espn_nba_snapshot")}

    def events(self, payload):
        snapshot = payload["snapshot"]
        paired: dict[tuple[str, str, str], dict[str, Any]] = {}
        for team, block in snapshot.get("teams", {}).items():
            for game in block.get("games", ()):
                if game.get("home"):
                    key = (str(game["date"]), str(game["opponent"]), team)
                    paired.setdefault(key, {})["home"] = (game, team)
                else:
                    key = (str(game["date"]), team, str(game["opponent"]))
                    paired.setdefault(key, {})["away"] = (game, team)
        events = []
        for (date, away, home), rows in sorted(paired.items()):
            if "home" not in rows or "away" not in rows:
                continue
            home_game, home_team = rows["home"]
            away_game, _away_team = rows["away"]
            if home_game.get("pts") is None or home_game.get("opp_pts") is None:
                continue
            kickoff = dt.datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
            home_win = 1 if home_game["pts"] > home_game["opp_pts"] else 0
            total = int(home_game["pts"]) + int(home_game["opp_pts"])
            fixture = {
                "date": date,
                "home": home_team,
                "away": away,
                "rest_home": home_game.get("rest_days"),
                "rest_away": away_game.get("rest_days"),
                "b2b_home": bool(home_game.get("b2b", False)),
                "b2b_away": bool(away_game.get("b2b", False)),
            }
            events.append(
                RunEvent(
                    event_key=f"nba:{date}:{away}:{home}",
                    group_id=date,
                    kickoff=kickoff,
                    available_at=kickoff,
                    fixture=fixture,
                    outcomes=(
                        RowOutcome("moneyline", "home-win", home_win),
                        RowOutcome(
                            "totals", f"over-{NBA_TOTALS_LINE}",
                            1 if total > NBA_TOTALS_LINE else 0,
                        ),
                    ),
                )
            )
        return tuple(events)

    def folds(self, payload):
        events = self.events(payload)
        folds, skipped = expanding_matchday_folds(events)
        return (
            folds,
            tuple({"block_id": item.block_id, "reason": item.reason} for item in skipped),
            tuple(None for _ in folds),
        )

    def history_for(self, payload, token):
        from polymarket_alpha_lab.papersim.optimize.families.nba import (
            NbaFamilyHistory,
        )

        return NbaFamilyHistory(snapshot=payload["snapshot"])

    def rows(self, payload, history, history_key, spec, target, event, *, include_variants):
        from polymarket_alpha_lab.papersim.optimize.families.nba import (
            NbaFamilyFixture,
        )

        family = self._family()
        fixture = event.fixture
        family_fixture = NbaFamilyFixture(
            home=fixture["home"],
            away=fixture["away"],
            rest_home=fixture["rest_home"],
            rest_away=fixture["rest_away"],
            b2b_home=fixture["b2b_home"],
            b2b_away=fixture["b2b_away"],
            before_date=fixture["date"],
        )
        fitted = _fit_cached(
            self.family_id, spec.spec_sha256(),
            lambda: family.fit(history, spec, dt.datetime(2026, 1, 1, tzinfo=UTC)),
        )
        prediction = family.predict(fitted, family_fixture)
        if prediction is None:
            return EventRows(event.event_key, "model_refusal", ())
        if target == "moneyline":
            row = RowScore(
                "home-win",
                (prediction.p_away, prediction.p_home),
                prediction.p_home_variant_spread,
            )
            return EventRows(event.event_key, None, (row,))
        quote = prediction.total_line_prob(NBA_TOTALS_LINE)
        row = RowScore(
            f"over-{NBA_TOTALS_LINE}",
            (quote.p_under, quote.p_over),
            quote.variant_spread,
        )
        return EventRows(event.event_key, None, (row,))


ADAPTERS: Mapping[str, FamilyRunAdapter] = {
    "club-soccer-recalib": _ClubSoccerRecalibAdapter(),
    "intl-elo-poisson": _IntlEloPoissonAdapter(),
    "intl-elo-goal-residuals": _IntlTotalsAdapter(),
    "nba-pace-efficiency": _NbaAdapter(),
}


RUN_PROFILES: Mapping[str, RunProfile] = {
    # Plan r2 section 3, pre-registered run parameters.  The club
    # cutoff is the archive pointer's pointed_at_utc literal (its
    # fetched_at_utc is null; deriving from file mtimes is forbidden).
    "club-soccer-recalib": RunProfile(
        family_id="club-soccer-recalib",
        fold_kind="mo4_rounds",
        holdout_start_utc=dt.datetime(2026, 9, 1, tzinfo=UTC),
        grid_overrides={},
        max_seconds=7200.0,
        max_trials=3000,
    ),
    "intl-elo-poisson": RunProfile(
        family_id="intl-elo-poisson",
        fold_kind="matchday",
        holdout_start_utc=dt.datetime(2024, 3, 21, tzinfo=UTC),
        grid_overrides={"slope": (0.40, 0.47, 0.55)},
        max_seconds=14400.0,
        max_trials=4000,
    ),
    "intl-elo-goal-residuals": RunProfile(
        family_id="intl-elo-goal-residuals",
        fold_kind="matchday",
        holdout_start_utc=dt.datetime(2024, 3, 21, tzinfo=UTC),
        grid_overrides={},
        max_seconds=3600.0,
        max_trials=4000,
    ),
    # NBA searches the REGISTERED v1 space (DELIVERY_PLAN 82.2): the
    # full 5x3x5x3x5 = 1125-point cartesian grid.  A dimension-at-a-
    # time sweep around the incumbent (17 specs) exists as a parallel
    # track's correction-plan alternative and is deliberately NOT
    # adopted: narrowing the search program is a rule change that
    # needs owner re-registration; recorded for the owner's decision.
    "nba-pace-efficiency": RunProfile(
        family_id="nba-pace-efficiency",
        fold_kind="matchday",
        holdout_start_utc=dt.datetime(2026, 11, 4, tzinfo=UTC),
        grid_overrides={},
        max_seconds=7200.0,
        max_trials=20000,
    ),
}


# ---------------------------------------------------------------------------
# Registry view inputs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LineageState:
    """The registry's view of one lineage at run time."""

    initialized: bool
    active_sha: str
    candidate_shas: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegistryView:
    """All lineages of the run's family plus the readability signal."""

    readable: bool
    lineages: Mapping[str, LineageState | None]

    def any_initialized(self) -> bool:
        return any(state is not None for state in self.lineages.values())


class RegistryLineageMissing(Exception):
    """Branch B: a candidate was selected on an uninitialized lineage.

    Fail-closed placement note (plan r2 section 1.2c'' said "winner
    passed the gate"): the check fires as soon as a candidate EMERGES
    (1-SE winner differs from the incumbent) on an uninitialized
    lineage — an initialized-elsewhere registry missing exactly this
    lineage is an inconsistent state and no stage-1 evidence computed
    after it can repair that, so no partial run row is completed.
    """


class DataIntegrityError(Exception):
    """A typed stream/boundary/label integrity failure (zero registration).

    Hardening against the parallel-track findings (correction plan
    items 2 and 3): the delayed-label boundary check (every
    selection-region label must be AVAILABLE strictly before the first
    holdout kickoff) and the outcome-table validation (every scored
    row must join the fixture-derived immutable line/outcome table
    with a legal probability vector).  Both raise BEFORE any scoring
    callback of the affected batch; the run ends RUN_FAILED with the
    typed code — an individual bad label is never filtered out
    silently and never scored.
    """

    def __init__(self, code: str, detail: str, *, phase: str = "") -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.phase = phase


#: Phase names recorded on failed runs (see BudgetGuard.set_phase).
PHASE_LOAD = "load"
PHASE_BOUNDARY = "boundary_validation"
PHASE_WALK_FORWARD = "walk_forward"
PHASE_FINAL_SELECTION = "final_selection"
PHASE_HOLDOUT = "holdout_scoring"
PHASE_GATES = "gate_evaluation"


def resubmission_blocked(
    recent_runs: Sequence[Mapping[str, Any]],
    lineage: str,
    spec_sha256: str,
    as_of: dt.datetime,
    *,
    days: int,
) -> bool:
    """True when ``spec_sha256`` failed on ``lineage`` within ``days``.

    The clock is the PRIOR run's data cutoff (deterministic evidence
    time); a lineage that REGISTERED does not block (a promoted
    candidate re-winning is the normal weekly rhythm).  The anchor is
    the IMMUTABLE optimization_runs rows (append-only, never the
    writing wall clock), and an idempotent same-key replay of the
    failed run creates no new row and therefore no new cooldown event.
    """
    window = dt.timedelta(days=days)
    for run in recent_runs:
        cutoff = run.get("data_cutoff")
        if isinstance(cutoff, str):
            cutoff = dt.datetime.fromisoformat(cutoff)
        if cutoff is None or as_of - cutoff > window:
            continue
        scorecard = run.get("scorecard")
        if isinstance(scorecard, str):
            scorecard = json.loads(scorecard)
        record = (scorecard or {}).get("lineages", {}).get(lineage)
        if not record:
            continue
        if record.get("winner_spec") == spec_sha256 and not record.get("registered"):
            return True
    return False


# ---------------------------------------------------------------------------
# Deterministic rendering helpers
# ---------------------------------------------------------------------------

def _number(value: float | None) -> str | None:
    return None if value is None else repr(value)


def _render(value: Any) -> Any:
    """Deterministic JSON-ready rendering (floats -> repr strings)."""
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _render(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_render(item) for item in value]
    if isinstance(value, dt.datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    raise TypeError(f"cannot render {type(value).__name__} deterministically")


def _canonical_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(
        _render(document), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _row_log_loss(probabilities: Sequence[float], outcome_index: int) -> float:
    return multiclass_log_loss(tuple(probabilities), outcome_index, floor=LOGLOSS_FLOOR)


def _validate_event_stream(events: Mapping[str, RunEvent]) -> None:
    """Typed stream sanity before ANY scoring callback runs.

    Every record must carry aware ``kickoff``/``available_at`` with
    ``kickoff <= available_at``; violations raise (phase: load), so a
    malformed stream never reaches a model.
    """
    for event in events.values():
        if event.kickoff.tzinfo is None or event.available_at.tzinfo is None:
            raise DataIntegrityError(
                "invalid_event_stream",
                f"naive timestamp on {event.event_key!r}",
                phase=PHASE_LOAD,
            )
        if event.kickoff > event.available_at:
            raise DataIntegrityError(
                "invalid_event_stream",
                f"kickoff after availability on {event.event_key!r}",
                phase=PHASE_LOAD,
            )


def _validate_selection_boundary(
    events: Mapping[str, RunEvent],
    walk_forward: Sequence[WalkFold],
    holdout: Sequence[WalkFold],
) -> dt.datetime:
    """The delayed-label boundary (parallel-track finding 2, hardening).

    ``selection_cutoff`` is the EARLIEST holdout kickoff.  EVERY
    selection-region label — training rows, test rows, fallback
    scoring, inner and outer folds, the 1-SE blocks — must be
    available STRICTLY before it; equality fails.  The check runs over
    the complete record set before any scoring callback and a
    violation is a typed RUN_FAILED (no per-label after-the-fact
    filtering): no selection label may encode information that only
    became knowable after the first holdout fixture kicked off.
    """
    if not holdout:
        raise DataIntegrityError(
            "empty_holdout_region",
            "the holdout region is empty",
            phase=PHASE_BOUNDARY,
        )
    selection_cutoff = min(
        min(events[key].kickoff for key in fold.test_keys) for fold in holdout
    )
    for fold in walk_forward:
        for key in fold.train_keys + fold.test_keys:
            event = events.get(key)
            if event is None:
                raise DataIntegrityError(
                    "invalid_event_stream",
                    f"fold {fold.block_id!r} references unknown event {key!r}",
                    phase=PHASE_BOUNDARY,
                )
            if not event.available_at < selection_cutoff:
                raise DataIntegrityError(
                    "selection_label_available_after_holdout_boundary",
                    f"{key!r} available_at {event.available_at.isoformat()} is not "
                    f"strictly before the first holdout kickoff "
                    f"{selection_cutoff.isoformat()} (fold {fold.block_id!r})",
                    phase=PHASE_BOUNDARY,
                )
    return selection_cutoff


def _outcome_table(
    events: Mapping[str, RunEvent], target: str
) -> dict[tuple[str, str], RowOutcome]:
    """The fixture-derived immutable line/outcome table (finding 3).

    Assembled once from the registered fixtures and their final
    settlements — independent of every model callback.  Winner,
    incumbent and (later) market rows are all joined against THIS
    table; the realized label and arity can never come from a model.
    """
    table: dict[tuple[str, str], RowOutcome] = {}
    for event in sorted(events.values(), key=lambda item: item.event_key):
        for outcome in event.outcomes_for(target):
            key = (event.event_key, outcome.line_key)
            if key in table:
                raise DataIntegrityError(
                    "duplicate_outcome_line",
                    f"duplicate line {key!r} in the outcome table",
                    phase=PHASE_HOLDOUT,
                )
            table[key] = outcome
    return table


def _validated_probabilities(probabilities: Sequence[float], arity: int) -> tuple[float, ...]:
    """Legal probability vector or a typed failure (never a silent repair)."""
    values = tuple(probabilities)
    if len(values) != arity or arity < 2:
        raise DataIntegrityError(
            "invalid_probability_vector",
            f"expected arity {arity}, got {len(values)}",
            phase=PHASE_HOLDOUT,
        )
    for value in values:
        if not isinstance(value, float) or not math.isfinite(value):
            raise DataIntegrityError(
                "invalid_probability_vector",
                f"non-finite component {value!r}",
                phase=PHASE_HOLDOUT,
            )
        if value < 0.0 or value > 1.0:
            raise DataIntegrityError(
                "invalid_probability_vector",
                f"component out of [0, 1]: {value!r}",
                phase=PHASE_HOLDOUT,
            )
    if abs(math.fsum(values) - 1.0) > 1e-9:
        raise DataIntegrityError(
            "invalid_probability_vector",
            f"components sum to {math.fsum(values)!r}, not 1",
            phase=PHASE_HOLDOUT,
        )
    return values


def _scored_events(
    event: RunEvent,
    rows: EventRows,
    target: str,
    outcome_table: Mapping[tuple[str, str], RowOutcome] | None = None,
) -> tuple[tuple[ScoredEvent, RowScore], ...]:
    """Join predicted rows onto the fixture-derived outcomes.

    With ``outcome_table`` supplied every row is validated against it
    (line exists, arity matches, probability vector legal) BEFORE it
    can enter scoring; a row the table does not know is a typed
    failure, not a silently dropped label.
    """
    if rows.refusal is not None:
        return ()
    by_line = {row.line_key: row for row in rows.rows}
    scored = []
    for outcome in event.outcomes_for(target):
        row = by_line.get(outcome.line_key)
        if row is None:
            continue
        if outcome_table is not None:
            table_entry = outcome_table.get((event.event_key, outcome.line_key))
            if table_entry is None:
                raise DataIntegrityError(
                    "holdout_outcome_mismatch",
                    f"line ({event.event_key!r}, {outcome.line_key!r}) is not in "
                    "the fixture-derived outcome table",
                    phase=PHASE_HOLDOUT,
                )
            if table_entry.outcome_index != outcome.outcome_index:
                raise DataIntegrityError(
                    "holdout_outcome_mismatch",
                    f"line ({event.event_key!r}, {outcome.line_key!r}) realized "
                    "label disagrees with the outcome table",
                    phase=PHASE_HOLDOUT,
                )
            _validated_probabilities(row.probabilities, _arity_of(outcome))
        scored.append(
            (
                ScoredEvent(
                    event_key=event.event_key,
                    line_key=outcome.line_key,
                    cluster_key=("event", event.event_key),
                    probabilities=row.probabilities,
                    outcome_index=outcome.outcome_index,
                ),
                row,
            )
        )
    return tuple(scored)


def _arity_of(outcome: RowOutcome) -> int:
    """Arity of one outcome line (binary=2; declared 3-way=3)."""
    return 3 if outcome.line_key == "1x2" else 2


def _median_spread(pairs: Sequence[tuple[ScoredEvent, RowScore]]) -> float | None:
    spreads = [row.variant_spread for _, row in pairs if row.variant_spread is not None]
    if not spreads:
        return None
    return statistics.median(spreads)


def _json_input(inputs: Sequence[RegisteredInput], kind: str) -> Any:
    primary = [item for item in inputs if item.kind == kind]
    if len(primary) != 1:
        raise ValueError(
            f"exactly one input of kind {kind!r} is required, got {len(primary)}"
        )
    return json.loads(primary[0].content.decode("utf-8"))


# ---------------------------------------------------------------------------
# The pure core
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunResult:
    outcome: str
    run_scorecard: dict
    artifact_bytes: bytes
    dataset_summary: dict
    snapshot_refs: tuple[str, ...]
    search_space_text_value: str
    active_specs: dict
    trial_count: int
    registered_lineages: tuple[str, ...]
    winner_specs: Mapping[str, str]
    error_code: str | None = None

    @property
    def artifacts_sha256(self) -> str:
        return sha256(self.artifact_bytes).hexdigest()


class _Memo:
    """Content-keyed prediction cache for snapshot families.

    Key = (family, event, target, spec identity, variant flag): a
    spec's identity (params + envelope + procedure version) is the
    single digest of everything prediction-side that can vary, and the
    enabled families' predictions are fold-independent by construction
    (each model's own strictly-earlier-date barrier), pinned by test.
    Club soccer is NOT memoized: its fit reads the fold's training set.
    """

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self._cache: dict[tuple, EventRows] = {}

    def rows_for(self, adapter, payload, history, history_key, spec, target, event,
                 *, include_variants):
        if not self.enabled:
            return adapter.rows(
                payload, history, history_key, spec, target, event,
                include_variants=include_variants,
            )
        key = (
            adapter.family_id, event.event_key, target, spec.spec_sha256(),
            bool(include_variants),
        )
        cached = self._cache.get(key)
        if cached is None:
            cached = adapter.rows(
                payload, history, history_key, spec, target, event,
                include_variants=include_variants,
            )
            self._cache[key] = cached
        return cached


def run_optimize_core(
    *,
    family_id: str,
    adapter: FamilyRunAdapter,
    inputs: Sequence[RegisteredInput],
    profile: RunProfile,
    as_of: dt.datetime,
    seed: int,
    incumbents: Mapping[str, ModelSpec],
    registry: RegistryView,
    recent_runs: Sequence[Mapping[str, Any]] = (),
    code_commit: str = "",
    budget: search.BudgetGuard | None = None,
) -> RunResult:
    """One complete stage-1 optimization run; fully deterministic.

    Same inputs (snapshots, spec space, seed, code commit, registry
    view, recent runs) produce byte-identical artifacts.  Budget
    exhaustion and branch B return a RUN_FAILED result that PRESERVES
    the completed per-lineage records — never a silent truncation.
    """
    if budget is None:
        budget = search.BudgetGuard(now_fn=time.monotonic)
    param_defs = adapter.param_defs()
    space_text = search.grid_space_text(param_defs, profile.grid_overrides)
    space_summary = search.grid_space_summary(param_defs, profile.grid_overrides)
    grid = search.enumerate_grid(param_defs, overrides=profile.grid_overrides)
    candidate_specs: list[ModelSpec] = []
    seen_shas: set[str] = set()
    for point in grid:
        spec = adapter.spec_for(point.params)
        if spec.spec_sha256() not in seen_shas:
            seen_shas.add(spec.spec_sha256())
            candidate_specs.append(spec)

    payload = adapter.load(inputs)
    all_events = adapter.events(payload)
    events_map = {event.event_key: event for event in all_events}
    if len(events_map) != len(all_events):
        raise ValueError("duplicate event keys in the event stream")
    folds, skipped, tokens = adapter.folds(payload)
    if not folds:
        raise ValueError("no walk-forward folds: the event stream is empty")
    histories = {
        fold.block_id: adapter.history_for(payload, token)
        for fold, token in zip(folds, tokens)
    }
    for fold in folds:
        if fold.cutoff >= as_of:
            raise ValueError(
                f"fold {fold.block_id} cutoff {fold.cutoff.isoformat()} is not "
                f"before the run's as-of {as_of.isoformat()}"
            )
    holdout = [
        fold for fold in folds
        if min(events_map[key].kickoff for key in fold.test_keys)
        >= profile.holdout_start_utc
    ]
    walk_forward = [fold for fold in folds if fold not in holdout]
    if not walk_forward:
        raise ValueError("the walk-forward region is empty (holdout window too early)")

    memo = _Memo(adapter.memoize)
    fallback_incumbent = not registry.readable or not registry.any_initialized()
    lineage_records: dict[str, dict[str, Any]] = {}

    try:
        budget.set_phase(PHASE_LOAD)
        # Typed integrity gates, whole-stream and BEFORE any scoring
        # callback: stream sanity, then the delayed-label boundary
        # (every selection-region label must be available strictly
        # before the first holdout kickoff; a violation ends the run
        # with zero callbacks — never a per-label after-the-fact
        # filter).
        _validate_event_stream(events_map)
        budget.set_phase(PHASE_BOUNDARY)
        _validate_selection_boundary(events_map, walk_forward, holdout)
        _run_lineages(
            lineage_records, family_id, adapter, payload, profile, as_of, seed,
            incumbents, registry, recent_runs, candidate_specs, param_defs,
            events_map, histories, walk_forward, holdout, memo, budget,
            fallback_incumbent,
        )
        budget.set_phase(PHASE_GATES)
        _apply_holm(lineage_records, seed)
        registered = _finalize_registrations(lineage_records)
        if registered:
            outcome = OUTCOME_CANDIDATE
        elif any(
            record.get("sample_status") == "insufficient"
            for record in lineage_records.values()
        ):
            outcome = OUTCOME_INSUFFICIENT
        else:
            outcome = OUTCOME_NO_BETTER
        error_code = None
        failed_phase = None
    except search.RunBudgetExceeded as error:
        outcome = OUTCOME_FAILED
        error_code = "budget_exhausted"
        failed_phase = error.phase or budget.phase
        registered = []
    except RegistryLineageMissing as error:
        outcome = OUTCOME_FAILED
        error_code = "registry_lineage_missing"
        failed_phase = budget.phase
        registered = []
    except DataIntegrityError as error:
        outcome = OUTCOME_FAILED
        error_code = error.code
        failed_phase = error.phase or budget.phase
        registered = []

    dataset_summary = {
        "events": len(events_map),
        "walk_forward_events": sum(len(fold.test_keys) for fold in walk_forward),
        "holdout_events": sum(len(fold.test_keys) for fold in holdout),
        "first_kickoff": min(event.kickoff for event in events_map.values()),
        "last_kickoff": max(event.kickoff for event in events_map.values()),
        "inputs": [[item.relpath, item.kind, item.content_sha256] for item in inputs],
    }
    trimmed = {
        lineage: {key: value for key, value in record.items()
                  if not key.startswith("_")}
        for lineage, record in lineage_records.items()
    }
    run_scorecard = {
        "rule_version": RULE_VERSION,
        "family": family_id,
        "data_cutoff": as_of,
        "seed": seed,
        "grid": space_summary,
        "outcome": outcome,
        "lineages": trimmed,
    }
    if error_code is not None:
        run_scorecard["error_code"] = error_code
        run_scorecard["failed_phase"] = failed_phase
    artifact = {
        "schema": "optimizer-run-artifact/1",
        "family": family_id,
        "data_cutoff": as_of,
        "seed": seed,
        "code_commit": code_commit,
        "rule_version": RULE_VERSION,
        "grid": space_summary,
        "inputs": dataset_summary["inputs"],
        "folds": {
            "walk_forward": [fold.block_id for fold in walk_forward],
            "holdout": [fold.block_id for fold in holdout],
            "skipped": [dict(item) for item in skipped],
        },
        "lineages": trimmed,
        "budget_exhausted": error_code == "budget_exhausted",
        "trials_completed": budget.trials,
        "outcome": outcome,
    }
    if error_code is not None:
        artifact["error_code"] = error_code
        artifact["failed_phase"] = failed_phase
    return RunResult(
        outcome=outcome,
        run_scorecard=run_scorecard,
        artifact_bytes=_canonical_bytes(artifact),
        dataset_summary=dataset_summary,
        snapshot_refs=tuple(item.content_sha256 for item in inputs),
        search_space_text_value=space_text,
        active_specs={
            lineage: record["incumbent_spec"]
            for lineage, record in lineage_records.items()
        },
        trial_count=budget.trials,
        registered_lineages=tuple(sorted(registered)),
        winner_specs={
            lineage: record["winner_spec"]
            for lineage, record in lineage_records.items()
            if record.get("winner_spec") is not None
        },
        error_code=error_code,
    )


def _run_lineages(
    lineage_records, family_id, adapter, payload, profile, as_of, seed,
    incumbents, registry, recent_runs, candidate_specs, param_defs,
    events_map, histories, walk_forward, holdout, memo, budget,
    fallback_incumbent,
) -> None:
    """One lineage per target of the RUN's family id (the adapter may be
    a test double carrying a different family id)."""

    defaults = {defn.name: defn.default for defn in param_defs}
    baseline = adapter.spec_for(defaults)
    for target in adapter.targets():
        lineage = lineage_id(family_id, target)
        incumbent = incumbents[lineage]
        state = registry.lineages.get(lineage)
        record: dict[str, Any] = {
            "target": target,
            "incumbent_spec": incumbent.spec_sha256(),
            "incumbent_source": (
                "family_baseline_fallback" if fallback_incumbent or state is None
                else "registry_active"
            ),
            "winner_spec": None,
            "selection_status": None,
            "sample_status": "ok",
            "registered": False,
            "blocked_reason": None,
            "gate": None,
            "scorecard": None,
            "incumbent_scorecard": None,
        }
        lineage_records[lineage] = record
        # The fixture-derived immutable line/outcome table: realized
        # labels and arities come from HERE for every callback (winner,
        # incumbent and, later, market) — never from a model row.
        table = _outcome_table(events_map, target)

        def score(spec, fold, test_keys, _target=target, _table=table):
            budget.charge()
            losses: list[float] = []
            history = histories[fold.block_id]
            for key in test_keys:
                event = events_map[key]
                rows = memo.rows_for(
                    adapter, payload, history, fold.block_id, spec, _target,
                    event, include_variants=False,
                )
                if rows.refusal is not None:
                    continue
                for scored, _row in _scored_events(event, rows, _target, _table):
                    losses.append(
                        _row_log_loss(scored.probabilities, scored.outcome_index)
                    )
            return losses

        # Walk-forward replay: per-fold nested selection (the SELECTION
        # PROGRAM is what gets scored), then the final selection + 1-SE.
        budget.set_phase(PHASE_WALK_FORWARD)
        fold_selections = []
        wf_selected: list[tuple[ScoredEvent, RowScore]] = []
        for fold in walk_forward:
            selection = search.select_per_fold(
                [fold], walk_forward, candidate_specs, score, incumbent,
                guard=budget,
            )[0]
            fold_selections.append((fold.block_id, selection))
            history = histories[fold.block_id]
            for key in fold.test_keys:
                event = events_map[key]
                rows = memo.rows_for(
                    adapter, payload, history, fold.block_id, selection.winner,
                    target, event, include_variants=False,
                )
                wf_selected.extend(_scored_events(event, rows, target, table))
        budget.set_phase(PHASE_FINAL_SELECTION)
        final_selection, one_se = search.final_selection(
            walk_forward, candidate_specs, score, incumbent, param_defs,
            lambda spec: dict(spec.params), guard=budget,
        )
        record["selection_status"] = final_selection.status
        record["winner_spec"] = final_selection.winner.spec_sha256()
        record["selection"] = {
            "status": final_selection.status,
            "winner_spec": final_selection.winner.spec_sha256(),
            "one_se": {
                "best_score": _number(one_se.best_score),
                "standard_error": _number(one_se.standard_error),
                "threshold": _number(one_se.threshold),
                "needs_preseason_reestimate": one_se.needs_preseason_reestimate,
            },
            "per_fold": [
                {
                    "block_id": block_id,
                    "status": selection.status,
                    "winner_spec": selection.winner.spec_sha256(),
                    "scores": [
                        [spec.spec_sha256(), _number(mean), count]
                        for spec, mean, count in selection.scores
                    ],
                }
                for block_id, selection in fold_selections
            ],
        }
        if (
            final_selection.status != "candidate_selected"
            or final_selection.winner.spec_sha256() == incumbent.spec_sha256()
        ):
            record["blocked_reason"] = "no_better_candidate"
            continue
        winner = final_selection.winner

        # Branch B: a selected candidate on an uninitialized lineage
        # cannot register and must not half-register (fail closed).
        if not fallback_incumbent and state is None:
            raise RegistryLineageMissing(lineage)

        holdout_keys = sorted(
            {key for fold in holdout for key in fold.test_keys}
        )
        # Review P1-2: the registered floor unit is MATCHES (场) — the
        # same unit as policy.STAGE1_HOLDOUT_MIN_MARKETS — and the
        # interval clusters by match ("同一场比赛的多条盘口线算一个
        # 簇").  Counting ROWS would let 100 three-line matches pose as
        # the 300-match floor; the row count stays on the sample record
        # for transparency only.
        n_holdout = len(holdout_keys)
        n_holdout_rows = sum(
            len(events_map[key].outcomes_for(target)) for key in holdout_keys
        )
        n_blocks = len(holdout)
        minimums = RUN_RULES_V1["stage1_holdout_minimums"].get(
            family_id, {"min_events": 0, "min_blocks": 0}
        )
        sample = {
            "holdout_events": n_holdout,
            "holdout_rows": n_holdout_rows,
            "holdout_blocks": n_blocks,
            "min_events": minimums["min_events"],
            "min_blocks": minimums["min_blocks"],
        }
        if n_holdout < minimums["min_events"] or n_blocks < minimums["min_blocks"]:
            record["sample_status"] = "insufficient"
            record["gate"] = {
                "sample": sample,
                "blocked_reason": "insufficient_holdout_sample",
            }
            continue
        if resubmission_blocked(
            recent_runs, lineage, winner.spec_sha256(), as_of,
            days=RUN_RULES_V1["resubmission_days"],
        ):
            record["blocked_reason"] = "resubmission_within_28d"
            continue
        # Branch A (empty/unreadable registry) and the candidate cap are
        # REGISTRATION blockers, not evaluation blockers: the holdout
        # evidence is computed in full and the block applies at the
        # final registration decision (plan r2 section 1.2c'').
        if fallback_incumbent:
            record["_registration_block"] = "registry_empty_or_unreadable"
        elif state is not None and len(state.candidate_shas) >= 2:
            record["_registration_block"] = "candidate_cap"

        # Holdout rows for winner / incumbent, variant spreads included.
        # Batch discipline (finding 3): the callbacks are collected
        # FIRST, then every row of BOTH specs is validated against the
        # outcome table (line join, arity, legal probability vector)
        # before any number enters the gate — one bad row fails the
        # whole run typed, with zero registration.
        budget.set_phase(PHASE_HOLDOUT)
        raw_winner: list[tuple[RunEvent, EventRows]] = []
        raw_incumbent: list[tuple[RunEvent, EventRows]] = []
        refusals = 0
        for fold in holdout:
            budget.charge()
            history = histories[fold.block_id]
            for key in fold.test_keys:
                event = events_map[key]
                for spec, sink in ((winner, raw_winner), (incumbent, raw_incumbent)):
                    rows = memo.rows_for(
                        adapter, payload, history, fold.block_id, spec, target,
                        event, include_variants=True,
                    )
                    if rows.refusal is not None:
                        if spec is winner:
                            refusals += 1
                        continue
                    sink.append((event, rows))
        winner_rows: list[tuple[ScoredEvent, RowScore]] = []
        incumbent_rows: list[tuple[ScoredEvent, RowScore]] = []
        for event, rows in raw_winner:
            winner_rows.extend(_scored_events(event, rows, target, table))
        for event, rows in raw_incumbent:
            incumbent_rows.extend(_scored_events(event, rows, target, table))

        winner_spread = _median_spread(winner_rows)
        incumbent_spread = _median_spread(incumbent_rows)
        if incumbent_spread is None and winner_spread is not None:
            raise DataIntegrityError(
                "envelope_shape_illegal",
                "the incumbent has no envelope but the candidate does "
                "(empty-envelope families admit only envelope == ())",
                phase=PHASE_HOLDOUT,
            )
        ratio = (
            None
            if winner_spread is None or incumbent_spread is None
            else winner_spread / incumbent_spread if incumbent_spread > 0.0
            else None
        )
        if winner_spread is None and incumbent_spread is None:
            spread_status = "not_assessable_empty_envelope"
        elif ratio is not None and ratio >= RUN_RULES_V1["envelope_width_ratio"]:
            spread_status = "pass"
        else:
            spread_status = "narrower_owner_decision"

        # Condition A inputs: paired diff, cluster interval, sign-flip p.
        by_cluster: dict[str, list[float]] = {}
        for scored, _row in winner_rows:
            match = _find(incumbent_rows, scored.event_key, scored.line_key)
            if match is None:
                continue
            diff = _row_log_loss(
                scored.probabilities, scored.outcome_index
            ) - _row_log_loss(match.probabilities, match.outcome_index)
            by_cluster.setdefault(scored.event_key, []).append(diff)
        clusters = [by_cluster[key] for key in sorted(by_cluster)]
        ci_lower, ci_upper = cluster_mean_ci(clusters)
        block_sums = [sum(block) for block in clusters]
        p_value = (
            sign_flip_pvalue(block_sums, seed=seed, reps=2000)
            if block_sums else 1.0
        )

        # Conditions B and C: scorecards on the SAME holdout rows; gate
        # D against the family baseline's walk-forward metrics.
        budget.set_phase(PHASE_GATES)
        baseline_wf = _spec_walk_forward_rows(
            adapter, payload, histories, walk_forward, events_map, baseline,
            target, memo, budget, table,
        )
        incumbent_wf = _spec_walk_forward_rows(
            adapter, payload, histories, walk_forward, events_map, incumbent,
            target, memo, budget, table,
        )
        gate_d_min = GATE_D_MIN_EVENTS.get(family_id, 30)
        gates_winner = gate_results(
            (),
            gate_d_walkforward(
                _wf_metrics(wf_selected), _wf_metrics(baseline_wf),
                len(wf_selected), min_events=gate_d_min,
            ),
            gate_e_market((), (), mad_max=0.06, min_events=1),
        )
        gates_incumbent = gate_results(
            (),
            gate_d_walkforward(
                _wf_metrics(incumbent_wf), _wf_metrics(baseline_wf),
                len(incumbent_wf), min_events=gate_d_min,
            ),
            gate_e_market((), (), mad_max=0.06, min_events=1),
        )
        # build_scorecard is called WITHOUT incumbent_events: the paired
        # summary would re-run the frozen 20,000-rep cluster bootstrap a
        # second time over the same pairs.  The decision numbers live in
        # the gate block above (one bootstrap + one sign-flip); the
        # scorecard stays the descriptive uniform surface.
        scorecard = build_scorecard(
            winner,
            [item[0] for item in winner_rows],
            variant_spread_median=winner_spread,
            refusals=refusals,
            gates=gates_winner,
            sign_flip_seed=seed,
        )
        incumbent_scorecard = build_scorecard(
            incumbent,
            [item[0] for item in incumbent_rows],
            variant_spread_median=incumbent_spread,
            gates=gates_incumbent,
            sign_flip_seed=seed,
        )
        record["gate"] = {
            "sample": sample,
            "paired": {
                "n_pairs": sum(len(block) for block in clusters),
                "n_clusters": len(clusters),
                "mean": _number(
                    statistics.fmean(
                        [value for block in clusters for value in block]
                    )
                ) if clusters else None,
                "ci95": [_number(ci_lower), _number(ci_upper)],
                "p_value": _number(p_value),
            },
            "holm_p_adjusted": None,   # filled by the family-wide correction
            "condition_a": None,
            "condition_b": _condition_b(scorecard, incumbent_scorecard),
            "condition_c": {
                "candidate_spread": _number(winner_spread),
                "incumbent_spread": _number(incumbent_spread),
                "ratio": _number(ratio),
                "status": spread_status,
            },
        }
        record["scorecard"] = scorecard.canonical_dict()
        record["incumbent_scorecard"] = incumbent_scorecard.canonical_dict()
        record["_holm_p"] = p_value


def _find(rows, event_key, line_key):
    for scored, _row in rows:
        if scored.event_key == event_key and scored.line_key == line_key:
            return scored
    return None


def _apply_holm(lineage_records: dict[str, dict], seed: int) -> None:
    """Family-wide Holm over the lineages that reached the holdout."""
    entries = [
        (lineage, record["_holm_p"])
        for lineage, record in lineage_records.items()
        if "_holm_p" in record
    ]
    if not entries:
        return
    rejected, adjusted = holm(
        [p for _, p in entries], alpha=RUN_RULES_V1["alpha"]
    )
    for index, (lineage, _p) in enumerate(entries):
        gate = lineage_records[lineage]["gate"]
        upper = gate["paired"]["ci95"][1]
        gate["holm_p_adjusted"] = _number(adjusted[index])
        gate["condition_a"] = bool(
            upper is not None and float(upper) < 0.0 and rejected[index]
        )


def _finalize_registrations(lineage_records: dict[str, dict]) -> list[str]:
    registered = []
    for lineage, record in lineage_records.items():
        gate = record.get("gate")
        if gate is None or gate.get("condition_a") is None:
            continue
        if not gate["condition_a"]:
            record["blocked_reason"] = (
                record["blocked_reason"] or "holdout_not_significant"
            )
            continue
        if not gate["condition_b"]["passed"]:
            record["blocked_reason"] = "scorecard_regression"
            continue
        if gate["condition_c"]["status"] == "narrower_owner_decision":
            record["blocked_reason"] = "envelope_narrower_owner_decision"
            continue
        block = record.pop("_registration_block", None)
        if block is not None:
            record["blocked_reason"] = block
            continue
        record["registered"] = True
        registered.append(lineage)
    return registered


def _spec_walk_forward_rows(
    adapter, payload, histories, walk_forward, events_map, spec, target, memo,
    budget, table,
):
    rows: list[tuple[ScoredEvent, RowScore]] = []
    for fold in walk_forward:
        budget.charge()
        history = histories[fold.block_id]
        for key in fold.test_keys:
            event = events_map[key]
            event_rows = memo.rows_for(
                adapter, payload, history, fold.block_id, spec, target, event,
                include_variants=False,
            )
            rows.extend(_scored_events(event, event_rows, target, table))
    return rows


def _wf_metrics(rows: Sequence[tuple[ScoredEvent, RowScore]]) -> dict:
    if not rows:
        return {"brier": None, "log_loss": None}
    losses = [
        _row_log_loss(scored.probabilities, scored.outcome_index)
        for scored, _row in rows
    ]
    briers = []
    for scored, _row in rows:
        brier = 0.0
        for index, probability in enumerate(scored.probabilities):
            realized = 1.0 if index == scored.outcome_index else 0.0
            brier += (probability - realized) ** 2
        briers.append(brier)
    return {"brier": statistics.fmean(briers), "log_loss": statistics.fmean(losses)}


def _condition_b(candidate: Any, incumbent: Any) -> dict:
    """Condition B operationalization (see the RUN_RULES_V1 docstring)."""
    incumbent_verdicts = {
        (gate.gate, gate.check): gate.verdict for gate in incumbent.gates
    }
    regressions = []
    for gate in candidate.gates:
        if gate.verdict != "FAIL":
            continue
        if incumbent_verdicts.get((gate.gate, gate.check)) != "FAIL":
            regressions.append([gate.gate, gate.check])
    ece_candidate = candidate.calibration_ece
    ece_incumbent = incumbent.calibration_ece
    ece_regression = (
        ece_candidate is not None
        and ece_incumbent is not None
        and ece_candidate > ece_incumbent
    )
    return {
        "passed": not regressions and not ece_regression,
        "gate_regressions": regressions,
        "calibration_ece": {
            "candidate": _number(ece_candidate),
            "incumbent": _number(ece_incumbent),
            "regression": ece_regression,
        },
    }


# ---------------------------------------------------------------------------
# Club archive reader (explicit read-only import of declared bytes)
# ---------------------------------------------------------------------------

def read_club_archive(root: str | Path) -> tuple[RegisteredInput, ...]:
    """Read the declared archive file set under ``root``; nothing else.

    Mirrors ``snapshots.registration_from_legacy_file``'s explicit
    read-only import discipline.  Optional companions (meta files,
    official fixture maps) simply stay absent when missing;
    ``history_from_archive`` fails closed on missing REQUIRED files at
    load time.
    """
    from polymarket_alpha_lab.papersim.optimize.dataset import (
        archive_fixture_paths,
        archive_read_paths,
    )

    base = Path(root).resolve()
    files: dict[str, bytes] = {}
    for relpath in archive_read_paths():
        path = base / relpath
        if path.is_file():
            files[relpath] = path.read_bytes()
    for relpath in archive_fixture_paths(files):
        path = base / relpath
        if path.is_file():
            files[relpath] = path.read_bytes()
    inputs = []
    for relpath in sorted(files):
        is_meta = ".meta." in relpath
        is_pointer = relpath == "understat_latest_pointer.json"
        is_fixture = "fixtures/" in relpath
        kind = (
            "optimizer_run_input"
            if is_meta or is_pointer or is_fixture
            else "fotmob_xg"
        )
        inputs.append(RegisteredInput(relpath, kind, files[relpath]))
    return tuple(inputs)


def resolve_run_inputs(document: Mapping[str, Any]) -> tuple[RegisteredInput, ...]:
    """Resolve the ``--input`` document into registered inputs.

    Two shapes: ``{"archive_root": ...}`` reads the DECLARED club
    archive set (``read_club_archive``); ``{"inputs": [{"path",
    "kind", "sha256"?}]}`` reads explicit files (bytes read once,
    optional digest verified — a mismatch fails before anything runs).
    The holdout window and the grid are NOT input knobs: they are
    frozen in the run profiles (search-space versioning discipline).
    """
    inputs: list[RegisteredInput] = []
    if "archive_root" in document:
        if document.get("inputs"):
            raise ValueError("input document mixes archive_root and inputs")
        inputs.extend(read_club_archive(document["archive_root"]))
    for entry in document.get("inputs", ()):
        path = Path(str(entry["path"]))
        content = path.read_bytes()
        declared = entry.get("sha256")
        if declared is not None and declared != sha256(content).hexdigest():
            raise ValueError(
                f"papersim_optimize_input_digest_mismatch:{entry['path']}"
            )
        inputs.append(
            RegisteredInput(str(entry.get("relpath", path.name)), str(entry["kind"]), content)
        )
    if not inputs:
        raise ValueError("papersim_optimize_inputs_empty")
    return tuple(inputs)


# ---------------------------------------------------------------------------
# The write shell
# ---------------------------------------------------------------------------

OPTIMIZE_RUN_COMMAND = "optimize-run"


def _forward_plan(family_id: str, target: str, as_of: dt.datetime) -> dict:
    """The pre-registered forward plan of ONE lineage (design §5-7).

    Review P1-1: the plan is per LINEAGE, never per family — a
    club-soccer match_winner registration carries the WDL row (300
    first / 600 final), an over_under registration the O/U row (150 /
    300).  The single source of the (family, target) table is MO-8's
    ``policy.FORWARD_THRESHOLDS`` (DELIVERY_PLAN 80.7, frozen); the
    ``RUN_RULES_V1["stage2_forward_minimums"]`` transcription is the
    T12 pin policy must keep matching (closing path: once MO-6 may
    depend on policy unconditionally, the transcription test flips to
    a direct policy-vs-pin comparison and the fallback below shrinks
    to synthetic test families only).
    """
    from polymarket_alpha_lab.papersim.optimize.policy import (
        FORWARD_THRESHOLDS,
    )

    row = FORWARD_THRESHOLDS.get((family_id, target))
    if row is not None:
        first = row.min_markets
        window_days = row.max_shadow_days
    else:
        # Test-double families only: the transcription's family-class
        # default, with the WDL row picked out by target.
        stage2 = RUN_RULES_V1["stage2_forward_minimums"]
        if family_id.startswith("club-soccer"):
            key = (
                "club-soccer(match_winner)"
                if target == "match_winner"
                else "club-soccer(ou/btts/tt)"
            )
            first = stage2[key]["min_events"]
            window_days = 84
        elif family_id.startswith("nba"):
            first = stage2["nba"]["min_events"]
            window_days = 70
        elif family_id.startswith("intl"):
            first = stage2["intl"]["min_events"]
            window_days = 365
        else:
            first = stage2["crypto"]["min_events"]
            window_days = 182
    return {
        "primary_metric": "mean_log_loss",
        "start": as_of.astimezone(UTC).isoformat(),
        "first_look_min_events": first,
        "final_look_min_events": 2 * first,
        "max_shadow_days": window_days,
        "rule_version": RULE_VERSION,
    }


def run_and_record(
    *,
    conn,
    repo_root,
    family_id: str,
    as_of: dt.datetime,
    inputs: Sequence[RegisteredInput],
    seed: int,
    budget_seconds: float | None = None,
    max_trials: int | None = None,
    operation_key: str | None = None,
    attempt: int = 1,
    now_utc: Callable[[], dt.datetime],
    dry_run: bool = False,
    git_bin: str = "git",
) -> dict:
    """One optimization run with receipts and the full write sequence.

    Order (plan r2 section 1.2d): clean-worktree check, STARTED
    receipt, input snapshot registration, registry read, core run,
    artifact snapshot registration, optimization_runs row, optimizer
    specs, CANDIDATE role events, SUCCEEDED receipt.  Every write is
    content-idempotent: a crash between writes heals by re-running
    the same command with a bumped ``attempt``.  ``dry_run`` does the
    full read+compute and writes NOTHING.
    """
    from polymarket_alpha_lab.papersim import scan as scan_module
    from polymarket_alpha_lab.papersim import snapshots, store_psycopg

    if family_id not in RUN_PROFILES or family_id not in ADAPTERS:
        raise ValueError(f"papersim_optimize_family_unknown:{family_id!r}")
    profile = RUN_PROFILES[family_id]
    adapter = ADAPTERS[family_id]
    code_commit = scan_module.require_clean_worktree(repo_root, git_bin=git_bin)
    as_of = as_of.astimezone(UTC)
    receipt_key = operation_key or (
        f"{OPTIMIZE_RUN_COMMAND}|{family_id}|{as_of:%Y%m%dT%H%M%SZ}|"
        f"seed-{seed}|mo6"
    )
    config_digest = sha256(
        _canonical_bytes(
            {
                "family": family_id,
                "as_of": as_of,
                "seed": seed,
                "inputs": [
                    [item.relpath, item.kind, item.content_sha256] for item in inputs
                ],
            }
        )
    ).hexdigest()
    budget = search.BudgetGuard(
        max_seconds=budget_seconds if budget_seconds is not None else profile.max_seconds,
        max_trials=max_trials if max_trials is not None else profile.max_trials,
        now_fn=time.monotonic,
    )

    def receipt(phase, *, error_code=None, output_evidence=()):
        store_psycopg.record_command_run_event(
            conn,
            store.CommandRunEventSubmission(
                operation_key=receipt_key,
                attempt=attempt,
                phase=phase,
                command=OPTIMIZE_RUN_COMMAND,
                occurred_at=now_utc(),
                code_commit=code_commit,
                config_digest=config_digest,
                input_evidence=[item.content_sha256 for item in inputs],
                output_evidence=list(output_evidence),
                error_code=error_code,
            ),
        )

    if not dry_run:
        receipt("STARTED")
    try:
        registry_view, incumbents = _read_registry(conn, family_id, adapter)
        recent_runs = store_psycopg.load_optimization_runs(conn, family_id)
        result = run_optimize_core(
            family_id=family_id, adapter=adapter, inputs=inputs, profile=profile,
            as_of=as_of, seed=seed, incumbents=incumbents,
            registry=registry_view, recent_runs=recent_runs,
            code_commit=code_commit, budget=budget,
        )
        if dry_run:
            return {
                "dry_run": True,
                "family": family_id,
                "outcome": result.outcome,
                "artifacts_sha256": result.artifacts_sha256,
                "run_scorecard": _render(result.run_scorecard),
                "trial_count": result.trial_count,
            }
        for item in inputs:
            snapshots.register_snapshot(
                conn,
                snapshots.SnapshotRegistration(
                    content=item.content,
                    kind=item.kind,
                    source=f"optimizer-run:{item.relpath}",
                    fetched_at=now_utc(),
                ),
            )
        artifacts = snapshots.register_snapshot(
            conn,
            snapshots.SnapshotRegistration(
                content=result.artifact_bytes,
                kind="optimizer_run_artifact",
                source=f"optimizer-run:{family_id}",
                fetched_at=now_utc(),
            ),
        )
        submission = store.OptimizationRunSubmission(
            family=family_id,
            data_cutoff=as_of,
            lineages=sorted(result.active_specs),
            dataset_summary=_render(result.dataset_summary),
            snapshot_refs=sorted(
                set(result.snapshot_refs) | {artifacts["content_sha256"]}
            ),
            search_space=json.loads(result.search_space_text_value),
            rule_version=RULE_VERSION,
            random_seed=seed,
            trial_count=result.trial_count,
            budget={
                "max_seconds": budget.max_seconds,
                "max_trials": budget.max_trials,
            },
            active_specs=dict(result.active_specs),
            outcome=result.outcome,
            scorecard=_render(result.run_scorecard),
            artifacts_sha256=artifacts["content_sha256"],
            code_commit=code_commit,
        )
        written = store_psycopg.record_optimization_run(conn, submission)
        registered = {}
        for lineage in result.registered_lineages:
            winner_sha = result.winner_specs.get(lineage)
            spec_row = store_psycopg.register_model_spec(
                conn,
                _spec_from_sha(adapter, winner_sha),
                source="optimizer",
                code_commit=code_commit,
                parent_spec_sha256=result.active_specs[lineage],
                optimization_id=written["optimization_id"],
            )
            role = store_psycopg.record_role_events(
                conn,
                store.RoleEventBatch(
                    events=(
                        store.RoleEventSubmission(
                            lineage=lineage,
                            spec_sha256=winner_sha,
                            role="CANDIDATE",
                            effective_at=now_utc(),
                            reason=(
                                f"optimizer run {written['optimization_key']}: "
                                "stage-1 gates passed (rules v1)"
                            ),
                            forward_plan=_forward_plan(
                            family_id,
                            result.run_scorecard["lineages"][lineage]["target"],
                            as_of,
                        ),
                            actor=f"auto:{RULE_VERSION}",
                            code_commit=code_commit,
                        ),
                    ),
                ),
            )
            registered[lineage] = {
                "spec_sha256": spec_row["spec_sha256"],
                "role_event_ids": role["role_event_ids"],
            }
        if result.outcome == OUTCOME_FAILED:
            # A budget-exhausted or integrity-failed run IS a failed
            # operation: the receipt says FAILED (the run row and the
            # partial artifact preserve the completed part); the command
            # itself exits 0 because recording the failure IS its job.
            receipt(
                "FAILED",
                error_code=result.error_code,
                output_evidence=[
                    written["optimization_key"], artifacts["content_sha256"]
                ],
            )
        else:
            receipt(
                "SUCCEEDED",
                output_evidence=[
                    written["optimization_key"], artifacts["content_sha256"]
                ],
            )
        return {
            "dry_run": False,
            "family": family_id,
            "outcome": result.outcome,
            "optimization_id": written["optimization_id"],
            "optimization_key": written["optimization_key"],
            "inserted": written["inserted"],
            "artifacts_sha256": artifacts["content_sha256"],
            "trial_count": result.trial_count,
            "registered_lineages": registered,
            "error_code": result.error_code,
        }
    except Exception as error:
        if not dry_run:
            code = getattr(error, "papersim_error_code", None) or type(error).__name__
            try:
                receipt("FAILED", error_code=str(code))
            except Exception:
                pass  # The original failure is the one to surface.
        raise


def _spec_from_sha(adapter: FamilyRunAdapter, spec_sha: str | None) -> ModelSpec:
    """Rebuild the winner ModelSpec for registration (sha must round-trip).

    The grid enumeration is deterministic, so re-deriving by sha is
    exact; the error below is the fail-closed guard.
    """
    if spec_sha is None:
        raise ValueError("papersim_optimize_winner_missing")
    for point in search.enumerate_grid(
        adapter.param_defs(),
        overrides=RUN_PROFILES[adapter.family_id].grid_overrides,
    ):
        spec = adapter.spec_for(point.params)
        if spec.spec_sha256() == spec_sha:
            return spec
    raise ValueError(f"papersim_optimize_winner_not_in_grid:{spec_sha}")


def _read_registry(conn, family_id: str, adapter: FamilyRunAdapter):
    """Read the registry view and resolve incumbent ModelSpecs.

    Branch A (empty or unreadable): every incumbent falls back to the
    family baseline and the run proceeds WITHOUT registration
    capability.  A non-empty registry that misses one of this family's
    lineages leaves ``state = None`` (branch B fires only when a
    candidate is actually selected).
    """
    from polymarket_alpha_lab.papersim import store_psycopg
    from polymarket_alpha_lab.papersim.optimize.registry import derive_lineage_roles

    lineages: dict[str, LineageState | None] = {}
    incumbents: dict[str, ModelSpec] = {}
    readable = True
    try:
        batches = store_psycopg.load_all_role_events(conn)
    except Exception:
        readable = False
        batches = {}
    defaults = {defn.name: defn.default for defn in adapter.param_defs()}
    for target in adapter.targets():
        lineage = lineage_id(family_id, target)
        grouped = batches.get(lineage) if readable else None
        roles = derive_lineage_roles(grouped) if grouped else None
        if roles is None:
            lineages[lineage] = None
            incumbents[lineage] = adapter.spec_for(defaults)
        else:
            lineages[lineage] = LineageState(
                initialized=True,
                active_sha=roles.active,
                candidate_shas=tuple(roles.candidates),
            )
            spec = store_psycopg.load_model_spec(conn, roles.active)
            if spec is None:
                raise ValueError(
                    f"papersim_registry_active_spec_unreadable:{lineage}:{roles.active}"
                )
            incumbents[lineage] = spec
    return RegistryView(readable=readable, lineages=lineages), incumbents


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

_FAMILY_SINGLETONS: Mapping[str, tuple[str, str]] = {
    "club-soccer-frozen": ("soccer_frozen", "SOCCER_FROZEN"),
    "club-soccer-recalib": ("soccer_recalib", "SOCCER_RECALIB"),
    "crypto-gbm": ("crypto", "CRYPTO_GBM"),
    "intl-elo-goal-residuals": ("intl", "INTL_TOTALS"),
    "intl-elo-poisson": ("intl", "INTL_ELO"),
    "league-bridge": ("league_bridge", "LEAGUE_BRIDGE"),
    "nba-pace-efficiency": ("nba", "NBA_PACE_EFFICIENCY"),
}


def family_singleton(family_id: str):
    """Lazy-resolve one family object (module attribute)."""
    from importlib import import_module

    module_name, attribute = _FAMILY_SINGLETONS[family_id]
    module = import_module(
        f"polymarket_alpha_lab.papersim.optimize.families.{module_name}"
    )
    return getattr(module, attribute)


def families_summary() -> dict:
    """The ``optimize families`` payload (no connection, no model state)."""
    from polymarket_alpha_lab.papersim.optimize.families import iter_family_ids

    families = []
    for family_id in iter_family_ids():
        family = family_singleton(family_id)
        space = family.param_space()
        baseline = family.baseline_spec()
        families.append(
            {
                "family_id": family_id,
                "procedure_version": family.procedure_version,
                "targets": list(family.targets()),
                "searchable": bool(space) and family_id in ADAPTERS,
                "param_space": [
                    {
                        "name": defn.name,
                        "kind": defn.kind,
                        "default": defn.default,
                        "choices": list(defn.choices),
                        "lower": defn.lower,
                        "upper": defn.upper,
                    }
                    for defn in space
                ],
                "baseline_spec_sha256": baseline.spec_sha256(),
                "baseline_model_version": baseline.model_version(),
            }
        )
    return {"families": families}


def status_report(conn, lineage_filter: str | None = None) -> dict:
    """The ``optimize status`` payload (read-only; MO-7 fields stay None)."""
    from polymarket_alpha_lab.papersim import store_psycopg
    from polymarket_alpha_lab.papersim.optimize.families import iter_family_ids
    from polymarket_alpha_lab.papersim.optimize.registry import derive_lineage_roles

    freeze = store_psycopg.registry_freeze_state(conn)
    batches = store_psycopg.load_all_role_events(conn)
    runs_by_family: dict[str, list[dict]] = {}
    lineages = []
    for family_id in iter_family_ids():
        family = family_singleton(family_id)
        runs = runs_by_family.get(family_id)
        if runs is None:
            runs = store_psycopg.load_optimization_runs(conn, family_id)
            runs_by_family[family_id] = runs
        for target in family.targets():
            lineage = lineage_id(family_id, target)
            if lineage_filter is not None and lineage != lineage_filter:
                continue
            grouped = batches.get(lineage)
            roles = derive_lineage_roles(grouped) if grouped else None
            lineages.append(
                {
                    "lineage": lineage,
                    "roles": roles.as_dict() if roles else None,
                    "frozen": (
                        freeze["global_frozen"]
                        or lineage in freeze["frozen_lineages"]
                    ),
                    "forward_sample_count": None,  # shadow_predictions: MO-7
                    "last_run": (
                        {
                            "data_cutoff": runs[0]["data_cutoff"].isoformat(),
                            "outcome": runs[0]["outcome"],
                        }
                        if runs else None
                    ),
                }
            )
    return {
        "freeze": freeze,
        "lineages": lineages,
        "shadow_predictions": "unavailable_until_mo7",
    }
