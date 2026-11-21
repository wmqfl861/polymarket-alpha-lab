"""MO-6 optimize runner for family adapters and pure experiment computation.

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
- ``execute_optimization_run`` — the deterministic core for registered
  experiments. The managed store owns the claim and publishes its result.
- ``run_and_record`` — the legacy Python entry point reports the requirement
  for a registered experiment manifest.

Paper-only: no order, account or credential surface anywhere.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import statistics
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from functools import lru_cache
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from typing import Any, TYPE_CHECKING
from polymarket_alpha_lab.papersim import store
from polymarket_alpha_lab.papersim.evaluation import (
    BOOTSTRAP_PERCENTILES as _BOOTSTRAP_PERCENTILES,
)
from polymarket_alpha_lab.papersim.optimize import registry as _rule_registry
from polymarket_alpha_lab.papersim.optimize import search
from polymarket_alpha_lab.papersim.optimize.metrics import (
    ScoredEvent,
    _HOLM_ALPHA_DEFAULT as HOLM_ALPHA,
    _SIGN_FLIP_REPS_DEFAULT as SIGN_FLIP_REPS,
    cluster_mean_ci,
    holm,
    multiclass_log_loss,
    sign_flip_pvalue,
)
from polymarket_alpha_lab.papersim.optimize.registry import LineageRoles, MAX_CANDIDATES, lineage_id
from polymarket_alpha_lab.papersim.optimize.scorecard import (
    GateResult,
    VERDICT_FAIL,
    VERDICT_NOT_ASSESSABLE,
    VERDICT_PASS,
    _SIGN_FLIP_SEED_DEFAULT as SIGN_FLIP_SEED,
    build_scorecard,
    gate_abc_stat,
    gate_d_walkforward,
    gate_e_market,
    gate_results,
)
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec, ParamDef, freeze_value
from polymarket_alpha_lab.papersim.optimize.walkforward import (
    OneSeResult,
    SKIP_PREFIX_INCOMPLETE,
    SkippedBlock,
    WalkFold,
    expanding_matchday_folds,
)
from bisect import bisect_left
from collections import defaultdict
from types import MappingProxyType
from zoneinfo import ZoneInfo
from polymarket_alpha_lab.papersim.optimize.search import (
    CausalCohort,
    Forecast,
    ForecastBatch,
    LineKey,
    ScopedTrialRecord,
    SearchBudget,
    SourceDay,
    TRIAL_ABORTED,
    TRIAL_COMPLETED,
    TRIAL_NO_EVENTS,
    _CohortGroup,
    as_utc_instant as _aware,
    build_catalog,
    holdout_prefix_folds,
    nested_fold_ids,
    normalize_targets,
    plan_evaluations,
    run_scoped_trials,
    select_common,
    selected_prefix_folds,
    validate_cohort,
)
from polymarket_alpha_lab.papersim.store import OPTIMIZATION_OUTCOMES, OptimizationRunSubmission

if TYPE_CHECKING:
    from polymarket_alpha_lab.papersim.optimize.dataset import HistoryAudit, MarketObservation
    from polymarket_alpha_lab.papersim.optimize.families.nba import NbaFamilyFixture


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
    "CANDIDATE_REGISTERED",
    "CI_CONFIDENCE_LEVEL",
    "CONTINUOUS_GRID_POINTS",
    "ClubGoalReference",
    "ENVELOPE_MIN_RATIO",
    "FOLD_KINDS",
    "FixtureLine",
    "FixtureRecord",
    "HOLM_ALPHA",
    "HoldoutEvaluation",
    "InternationalFixture",
    "IntlSourceRow",
    "INSUFFICIENT_DATA",
    "JudgedRejection",
    "LegacyRejection",
    "LineageDecision",
    "LineageStat",
    "MAX_TRIALS",
    "NBASourceManifest",
    "NO_IMPROVEMENT",
    "OptimizationRunResult",
    "PolicyContext",
    "Prediction",
    "RESUBMISSION_BAN_DAYS",
    "RUN_FAILED",
    "RULE_VERSION",
    "Refusal",
    "RegistryComparison",
    "RejectionReceipt",
    "SIGN_FLIP_REPS",
    "SIGN_FLIP_SEED",
    "STAGE1_HOLDOUT_FLOORS",
    "ScopedFold",
    "SourceArtifactRef",
    "TIME_BUDGET_SECONDS",
    "VariantPrediction",
    "banned_spec_digests",
    "bootstrap_resamples",
    "club_fixture_records",
    "club_model_history",
    "evaluate_stage1_rule",
    "execute_optimization_run",
    "historical_protocol",
    "historical_run_arguments",
    "international_history_at",
    "international_replay_prediction",
    "international_snapshot_records",
    "nba_history_at",
    "nba_snapshot_cohort",
    "optimization_search_descriptor",
    "resolve_fold_kind",
    "resolve_stage1_floors",
    "stage1_floor_class",
    "variant_ids",
)


UTC = dt.timezone.utc

#: Rule version string written into every run row and receipt context.
RULE_VERSION = "v1"

#: Section 80.7 projections (registered 2026-11-20).
CI_CONFIDENCE_LEVEL = (_BOOTSTRAP_PERCENTILES[1] - _BOOTSTRAP_PERCENTILES[0]) / 100
ENVELOPE_MIN_RATIO = _rule_registry._ENVELOPE_MIN_RATIO
RESUBMISSION_BAN_DAYS = _rule_registry._RESUBMISSION_BAN_DAYS
STAGE1_HOLDOUT_FLOORS: Mapping[str, tuple[int, int]] = _rule_registry._STAGE1_HOLDOUT_FLOORS
_STAGE1_CI_UPPER_BOUND = _rule_registry._STAGE1_CI_UPPER_BOUND

#: Legacy, JSON-ready projection of the registered rules and the MO-6
#: synthetic acceptance thresholds.
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
    "alpha": HOLM_ALPHA,
    "envelope_width_ratio": ENVELOPE_MIN_RATIO,
    "resubmission_days": RESUBMISSION_BAN_DAYS,
    "fp_rate_max": _rule_registry._FALSE_POSITIVE_RATE_MAX,
    "detection_min": _rule_registry._DETECTION_RATE_MIN,
    "stage1_holdout_minimums": {
        family_id: {"min_events": STAGE1_HOLDOUT_FLOORS[floor_class][0],
                    "min_blocks": STAGE1_HOLDOUT_FLOORS[floor_class][1]}
        for family_id, floor_class in _rule_registry._STAGE1_FAMILY_CLASSES.items()
    },
    # The legacy min_blocks field can mean complete blocks or span days.
    "stage2_forward_minimums": {
        key: {
            "min_events": row.min_events,
            "min_blocks": (
                row.min_complete_blocks if row.min_complete_blocks is not None else row.min_span_days
            ),
        }
        for key, row in _rule_registry._LEGACY_FORWARD_ROWS.items()
    },
    "max_shadow_windows": {
        key: _rule_registry._shadow_window_label(key)
        for key in _rule_registry._SHADOW_WINDOWS
    },
    "promote_ci_upper_max": _rule_registry._PROMOTE_CI_UPPER_MAX,
    "first_look_ci_tighten": "%gpct" % (
        _rule_registry._FIRST_EVIDENCE_CI_PERCENTILES[1]
        - _rule_registry._FIRST_EVIDENCE_CI_PERCENTILES[0]
    ),
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
        # EVERY completed match of the audit, not only fold test blocks:
        # the selection-boundary validator must resolve TRAIN labels too
        # (a fold's training rows are selection-region labels exactly as
        # its test rows are).  Scoring stays fold-test-only everywhere.
        def event_of(match):
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
            return RunEvent(
                event_key=match.match_id,
                group_id=f"{match.league}:{match.season}:round-{match.matchweek}",
                kickoff=match.kickoff,
                available_at=match.available_at or match.kickoff,
                fixture=match,
                outcomes=tuple(rows),
            )

        mo4_folds, _ = self._mo4_folds(payload)
        seen = set()
        events = []
        for fold in mo4_folds:
            for match in (*fold.train, *fold.test):
                if match.match_id not in seen:
                    seen.add(match.match_id)
                    events.append(event_of(match))
        return tuple(sorted(events, key=lambda e: (e.kickoff, e.event_key)))

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
    # AMENDED WINDOW (landing deviation, ratified via the fix PR): the
    # pre-registered 2026-09-01 window is INFEASIBLE on the actual
    # archive — three postponed Eredivisie 2026 round-3 matches (kickoff
    # Sep 8/9/15) sit in walk-forward fold test sets, so their labels
    # realize after the first holdout kickoff and the registered
    # delayed-label boundary invariant (violations -> RUN_FAILED, never
    # per-label filtering) rejects the run.  A scan over every fold
    # boundary found 15 windows with ZERO boundary violations and floors
    # met; the LATEST feasible one maximizes the selection region's
    # recency: holdout = 425 matches / 43 round blocks (floor 300/20),
    # walk-forward = 26 folds.  Derived from data-manifest dates only.
    "club-soccer-recalib": RunProfile(
        family_id="club-soccer-recalib",
        fold_kind="mo4_rounds",
        holdout_start_utc=dt.datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
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
        elif state is not None and len(state.candidate_shas) >= MAX_CANDIDATES:
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
            sign_flip_pvalue(block_sums, seed=seed, reps=SIGN_FLIP_REPS)
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
            upper is not None and float(upper) < _STAGE1_CI_UPPER_BOUND and rejected[index]
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
# Legacy experiment helpers
# ---------------------------------------------------------------------------

OPTIMIZE_RUN_COMMAND = "optimize-run"


def _forward_plan(family_id: str, target: str, as_of: dt.datetime) -> dict:
    """The pre-registered forward plan of one lineage (design 04 section 5).

    Registered combinations use the policy row. The legacy fallback
    resolves other combinations by family prefix and the club target.
    """
    from polymarket_alpha_lab.papersim.optimize.policy import (
        FORWARD_THRESHOLDS,
        SECOND_LOOK_SAMPLE_MULTIPLIER,
    )

    row = FORWARD_THRESHOLDS.get((family_id, target))
    if row is not None:
        first = row.min_markets
        window_days = row.max_shadow_days
    else:
        if family_id.startswith("club-soccer"):
            key = (
                "club-soccer(match_winner)"
                if target == "match_winner"
                else "club-soccer(ou/btts/tt)"
            )
        elif family_id.startswith("nba"):
            key = "nba"
        elif family_id.startswith("intl"):
            key = "intl"
        else:
            key = "crypto"
        first = RUN_RULES_V1["stage2_forward_minimums"][key]["min_events"]
        window_days = _rule_registry._shadow_window_days(
            _rule_registry._LEGACY_FORWARD_ROWS[key].shadow_window
        )
    return {
        "primary_metric": "mean_log_loss",
        "start": as_of.astimezone(UTC).isoformat(),
        "first_look_min_events": first,
        "final_look_min_events": SECOND_LOOK_SAMPLE_MULTIPLIER * first,
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
    """Report the registered manifest requirement for the legacy signature.

    Registered requests use the managed store's execution and preview entry
    points. Independent research can pass its own inputs to either pure core.
    """
    raise ValueError("papersim_optimize_input_manifest_required")


def _submission_lineage_names(
    family_id: str, adapter: FamilyRunAdapter, result: RunResult
) -> list[str]:
    """The run row's lineage list: the finished lineages when any, else
    the family's DECLARED set.

    An early integrity/budget failure legitimately carries an empty
    ``active_specs`` map (no lineage finished); the failed row must
    still land, and store validation rejects an empty list — the
    declared set is what the run was scoped to.
    """
    return sorted(result.active_specs) or sorted(
        f"{family_id}/{target}" for target in adapter.targets()
    )


def _spec_from_sha(adapter: FamilyRunAdapter, spec_sha: str | None) -> ModelSpec:
    """Rebuild a legacy grid winner by SHA-256.

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
    """Build the legacy core's registry view and resolve incumbent ModelSpecs.

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


def bootstrap_resamples() -> int:
    """The kernel-frozen cluster-bootstrap resample count (lazy, no re-home).

    Returns ``evaluation.BOOTSTRAP_RESAMPLES`` -- the number is owned by
    the frozen evaluation kernel; the runner records it, never
    re-implements it, and the pinning test asserts equality.
    """
    from polymarket_alpha_lab.papersim.evaluation import BOOTSTRAP_RESAMPLES

    return BOOTSTRAP_RESAMPLES


#: V1 compute caps. A trial is one candidate on one fold, so a complete
#: grid can exceed the cap across folds even when the grid fits by itself.
MAX_TRIALS = 2000
TIME_BUDGET_SECONDS = 3600

#: Locked v1 grid resolution for bounded continuous dimensions without a
#: declared choice set (the intl Elo slope is the only v1 example): the
#: evenly spaced endpoint-inclusive grid plus the declared default.
CONTINUOUS_GRID_POINTS = 5

#: Fold kind per baseline family (the walkforward kernel generator to
#: use). Unknown families -- including any family merged after this node
#: -- resolve to ``DEFAULT_FOLD_KIND`` with zero MO-6 code change (the
#: registry itself is consumed dynamically through ``iter_family_ids``).
FOLD_KINDS: Mapping[str, str] = MappingProxyType({
    "club-soccer-frozen": "matchweek",
    "club-soccer-recalib": "matchweek",
    "crypto-gbm": "expiry",
    "intl-elo-goal-residuals": "matchday",
    "intl-elo-poisson": "matchday",
    "nba-pace-efficiency": "matchday",
})
DEFAULT_FOLD_KIND = "matchday"

#: Outcome tokens re-exported for callers (canonical home: store).
CANDIDATE_REGISTERED, NO_IMPROVEMENT, INSUFFICIENT_DATA, RUN_FAILED = (
    OPTIMIZATION_OUTCOMES
)

# ---------------------------------------------------------------------------
# Resolvers (fold kind, stage-1 floors)
# ---------------------------------------------------------------------------


def resolve_fold_kind(family_id: str) -> str:
    """The walkforward generator kind of one family id.

    Baseline families resolve through ``FOLD_KINDS``; anything else --
    including families merged after this node -- falls back to
    ``DEFAULT_FOLD_KIND`` so a registry extension needs no MO-6 change.
    """
    return FOLD_KINDS.get(family_id, DEFAULT_FOLD_KIND)


def stage1_floor_class(family_id: str) -> str:
    """The section 80.7 floor class of one family id (prefix convention)."""
    for class_name in STAGE1_HOLDOUT_FLOORS:
        if family_id == class_name or family_id.startswith(f"{class_name}-"):
            return class_name
    raise ValueError(
        f"family {family_id!r} maps to no section 80.7 stage-1 floor class; "
        "pass floor_class explicitly (one of "
        f"{sorted(STAGE1_HOLDOUT_FLOORS)})"
    )


def resolve_stage1_floors(
    family_id: str, *, floor_class: str | None = None
) -> tuple[int, int]:
    """(min_events, min_blocks) of one family, straight from the table."""
    try:
        key = stage1_floor_class(family_id)
    except ValueError:
        if floor_class is None:
            raise
        key = floor_class
    if floor_class is not None and floor_class != key:
        raise ValueError(f"family {family_id!r} requires the {key!r} stage-1 floors")
    if key not in STAGE1_HOLDOUT_FLOORS:
        raise ValueError(
            f"unknown stage-1 floor class {key!r}; registered classes: "
            f"{sorted(STAGE1_HOLDOUT_FLOORS)}"
        )
    return STAGE1_HOLDOUT_FLOORS[key]


# ---------------------------------------------------------------------------
# Stage-1 rule (section 80.7, applied exactly as registered)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineageStat:
    """The per-lineage inputs of the stage-1 registration rule."""

    lineage: str
    n_events: int
    n_blocks: int
    ci95_upper: float | None
    p_value: float | None
    calibration_regression: bool = False
    gate_regression: bool = False
    envelope_status: str = "ok"
    attempted: bool = True
    status: str = "holdout_assessed"
    evidence_unverified: bool = False
    has_model_refusals: bool = False


@dataclass(frozen=True)
class LineageDecision:
    """The per-lineage verdict of the stage-1 rule, with every basis."""

    lineage: str
    n_events: int
    n_blocks: int
    registered: bool
    floors_met: bool
    ci_passes: bool
    holm_rejected: bool | None  # None: Holm not applied (single lineage)
    calibration_regression: bool
    gate_regression: bool
    envelope_status: str
    flags: tuple[str, ...] = ()
    attempted: bool = True
    status: str = "holdout_assessed"
    statistically_eligible: bool = False


def envelope_status(
    candidate_median: float | None, incumbent_median: float | None
) -> str:
    """Section 80.7 envelope guard: ``narrowed`` blocks registration.

    Both medians present: ``narrowed`` when the candidate's median is
    below ``ENVELOPE_MIN_RATIO`` times the incumbent's. Either side
    unavailable (empty-envelope families, section 82.2): no spec can
    demonstrate narrowing, the guard passes vacuously --
    ``not_assessable`` -- and the artifact records it.
    """
    if candidate_median is None or incumbent_median is None:
        return "not_assessable"
    if not math.isfinite(candidate_median) or not math.isfinite(incumbent_median):
        return "not_assessable"
    if incumbent_median < 0.0 or candidate_median < 0.0:
        return "not_assessable"
    if candidate_median < ENVELOPE_MIN_RATIO * incumbent_median:
        return "narrowed"
    return "ok"


def evaluate_stage1_rule(
    stats: Sequence[LineageStat],
    *,
    min_events: int,
    min_blocks: int,
) -> tuple[tuple[LineageDecision, ...], bool]:
    """Apply the section 80.7 stage-1 rule to every lineage of one run.

    Floors first: any evaluated lineage below the family's holdout
    floors makes the whole run ``INSUFFICIENT_DATA`` (returns
    ``insufficient=True``; decisions still carry the counts). Then the
    registration conjunction per lineage: holdout paired-diff 95% CI
    upper < 0 (Holm-corrected when the run evaluates multiple
    lineages), no calibration regression, no validation-gate
    regression, envelope not narrowed. A narrowed envelope NEVER
    auto-registers -- the decision carries the
    ``envelope_narrowed_owner_decision`` flag for the owner.
    """
    decisions: list[LineageDecision] = []
    floors_met = tuple(
        stat.attempted and stat.n_events >= min_events and stat.n_blocks >= min_blocks for stat in stats
    )
    insufficient = not stats or any(
        stat.attempted and not passed for stat, passed in zip(stats, floors_met, strict=True)
    )
    rejected: tuple[bool, ...] | None = None
    if len(stats) > 1 and not insufficient and any(stat.attempted for stat in stats):
        pvalues = [
            stat.p_value if stat.attempted and stat.p_value is not None else 1.0 for stat in stats
        ]
        rejected, _adjusted = holm(pvalues, alpha=HOLM_ALPHA)
    for index, stat in enumerate(stats):
        ci_passes = stat.ci95_upper is not None and stat.ci95_upper < _STAGE1_CI_UPPER_BOUND
        holm_rejected = None if rejected is None else rejected[index]
        significance = ci_passes and (
            True if holm_rejected is None else holm_rejected
        )
        flags: list[str] = []
        if stat.evidence_unverified:
            flags.append("required_quality_evidence_unverified")
        if stat.attempted and stat.envelope_status == "narrowed":
            flags.append("envelope_narrowed_owner_decision")
        if stat.attempted and stat.envelope_status == "not_assessable":
            flags.append("envelope_not_assessable")
        if stat.envelope_status == "unverified":
            flags.append("envelope_dispersion_unverified")
        registered = (
            stat.attempted
            and significance
            and not insufficient
            and not stat.evidence_unverified
            and not stat.calibration_regression
            and not stat.gate_regression
            and stat.envelope_status in ("ok", "not_assessable")
        )
        decisions.append(
            LineageDecision(
                lineage=stat.lineage,
                n_events=stat.n_events,
                n_blocks=stat.n_blocks,
                registered=registered,
                floors_met=floors_met[index],
                ci_passes=ci_passes,
                holm_rejected=holm_rejected,
                calibration_regression=stat.calibration_regression,
                gate_regression=stat.gate_regression,
                envelope_status=stat.envelope_status,
                flags=tuple(flags),
                attempted=stat.attempted,
                status=stat.status,
                statistically_eligible=registered,
            )
        )
    return tuple(decisions), insufficient



_SEARCH_STRATEGY = "mo6-common-cohort-v1"
_DEFAULT_SCHEDULE = "declared-chronological-suffix"
_UTC = dt.timezone.utc
_BAN_REASON = "stage1_resubmission_ban"
_QUARANTINE_REASON = "stage1_rejection_quarantined"
_INTL_REPORT = "WP-INTL-3/tools/wp_intl_validate.py"
_INTL_NATIONS_LEAGUE = "UEFA Nations League"
_INTL_GATE_D_MIN = 20
_INTL_RESEARCH_EPS = 1e-12
_NBA_REPORT = "WP-NBA-3/tools/wp_nba_validate.py"
_NBA_LINES = MappingProxyType({
    "moneyline": (None, ("away", "home")),
    "totals": (225.5, ("under", "over")),
})


def _canonical_json(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _historical_family(family):
    if not isinstance(family, str):
        return family
    family_names = {
        "club-soccer-recalib": "SOCCER_RECALIB",
        "intl-elo-goal-residuals": "INTL_TOTALS",
        "intl-elo-poisson": "INTL_ELO",
        "nba-pace-efficiency": "NBA_PACE_EFFICIENCY",
    }
    if family not in family_names:
        raise ValueError("family has no registered MO-6 historical protocol")
    from polymarket_alpha_lab.papersim.optimize import families

    return getattr(getattr(families, family), family_names[family])


def historical_protocol(family) -> dict:
    """Return the canonical manifest protocol for a supported historical run."""
    family = _historical_family(family)
    family_id = family.family_id
    match_winner = {"market_type": "match_winner", "line": None, "class_order": ["home", "draw", "away"]}
    if family_id == "club-soccer-recalib":
        schedule = "mo6-club-official-round-suffix-v1"
        calendar, timezone = "official league/season/round with UTC kickoff", "UTC"
        cutoff, last_complete = "2026-11-10T10:33:51Z", None
        cohort = {"search_rule": "all_preceding_official_groups",
                  "holdout_rule": "longest_complete_chronological_suffix_after_last_incomplete_official_round",
                  "first_holdout_boundary": "2026-11-06T19:00:00Z"}
        lines = {"match_winner": [match_winner], "over_under": [
            {"market_type": "over_under", "line": line, "class_order": ["under", "over"]}
            for line in (1.5, 2.5, 3.5)
        ]}
    elif family_id == "nba-pace-efficiency":
        schedule = "mo6-nba-espn-november-suffix-v1"
        calendar, timezone = "ESPN NBA source date", "America/New_York"
        cutoff, last_complete = "2026-11-10T05:00:00Z", "2026-11-09"
        cohort = {"search_rule": "all_preceding_complete_source_dates",
                  "holdout_rule": "complete_source_date_suffix", "first_holdout_date": "2026-11-01"}
        lines = {target: [{"market_type": target, "line": line, "class_order": list(order)}]
                 for target, (line, order) in _NBA_LINES.items()}
    elif family_id in ("intl-elo-poisson", "intl-elo-goal-residuals"):
        schedule = "mo6-intl-complete-day-suffix-v1"
        calendar, timezone = "international archive source date", "UTC"
        cutoff, last_complete = "2026-10-07T00:00:00Z", "2026-10-06"
        cohort = {"search_rule": "latest_eligible_complete_source_dates", "search_dates": 24,
                  "holdout_rule": "complete_source_date_suffix", "first_holdout_date": "2025-01-01"}
        lines = {"match_winner": [match_winner]} if family_id == "intl-elo-poisson" else {"over_under": [
            {"market_type": "over_under", "line": 2.5, "class_order": ["under", "over"]},
        ]}
    else:
        raise ValueError("family has no registered MO-6 historical protocol")
    targets = list(family.targets())
    if targets != list(lines):
        raise ValueError("model targets disagree with the registered historical protocol")
    return {
        "family": family_id, "procedure_version": family.procedure_version, "rule_version": RULE_VERSION,
        "schedule_id": schedule, "seed": 20261120, "data_cutoff": cutoff, "targets": targets,
        "source_calendar": calendar, "source_timezone": timezone, "last_complete_date": last_complete,
        "lines": lines, "cohort": cohort,
    }


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _iso(moment: dt.datetime | dt.date) -> str:
    if isinstance(moment, dt.datetime):
        return _aware(moment, "timestamp").isoformat().replace("+00:00", "Z")
    return moment.isoformat()


def _require_hex(value: str, length: int, name: str) -> None:
    if not isinstance(value, str) or len(value) != length or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise ValueError(f"{name} must be a {length}-character lowercase hexadecimal digest")


def _identifier(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _items(values, name):
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise ValueError(f"{name} must be a collection")
    try:
        return tuple(values)
    except TypeError as exc:
        raise ValueError(f"{name} must be a collection") from exc


@dataclass(frozen=True)
class SourceArtifactRef:
    """A registered byte identity for a provider snapshot or raw response."""

    sha256: str
    byte_length: int

    def __post_init__(self) -> None:
        _require_hex(self.sha256, 64, "source artifact")
        if type(self.byte_length) is not int or self.byte_length < 1:
            raise ValueError("source artifact byte length must be a positive integer")


@dataclass(frozen=True)
class IntlSourceRow:
    """Preregistered match position and facts to check against the raw array."""

    snapshot: SourceArtifactRef
    index: int
    home: str
    away: str
    tournament: str
    neutral: bool
    home_goals: int
    away_goals: int

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, SourceArtifactRef) or type(self.index) is not int or self.index < 0:
            raise ValueError("international source row requires a snapshot and nonnegative index")
        if not all(map(_identifier, (self.home, self.away, self.tournament))) or self.home == self.away:
            raise ValueError("international source row has invalid match identity")
        if type(self.neutral) is not bool or any(
            type(goals) is not int or goals < 0 for goals in (self.home_goals, self.away_goals)
        ):
            raise ValueError("international source row has invalid neutral or final score")

    @property
    def event_key(self) -> str:
        return sha256(_canonical_json([self.snapshot.sha256, "matches", self.index])).hexdigest()


@dataclass(frozen=True)
class NBASourceManifest:
    """The registered snapshot and independent ESPN responses for one season."""

    snapshot: SourceArtifactRef
    season: str
    schedules: Mapping[str, SourceArtifactRef]
    summaries: Mapping[str, SourceArtifactRef]

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, SourceArtifactRef) or not _identifier(self.season):
            raise ValueError("NBA source manifest requires a snapshot and season")
        for field_name in ("schedules", "summaries"):
            refs = getattr(self, field_name)
            if not isinstance(refs, Mapping) or any(
                not _identifier(key) or not isinstance(ref, SourceArtifactRef) for key, ref in refs.items()
            ):
                raise ValueError(f"NBA {field_name} must map provider keys to source artifacts")
            object.__setattr__(self, field_name, MappingProxyType(dict(refs)))


@dataclass(frozen=True)
class FixtureRecord:
    """Outcome-free event identity, group, and source-specific knowledge clocks.

    Dates are source calendar periods. Use source_day/label_day for date-only
    data and reserve available_at for an independently observed delay.
    Crypto contract expiry is independent of the prediction and label clocks.
    """

    event_key: str
    group_id: str
    kickoff: dt.datetime | None = None
    available_at: dt.datetime | None = None
    source_day: dt.date | None = None
    label_day: dt.date | None = None
    floor_block: str | None = None
    prediction_at: dt.datetime | None = None
    intl_source_row: IntlSourceRow | None = None
    expiry_at: dt.datetime | None = None


@dataclass(frozen=True)
class FixtureLine:
    """One independently settled contract and its registered decision quote."""

    event_key: str
    target: str
    line_key: str
    observation_id: str
    class_order: tuple[str, ...]
    outcome_index: int
    market_type: str
    line: float | None = None
    decision_at: dt.datetime | None = None
    quote_source: str | None = None
    quote_snapshot_sha256: str | None = None
    quote_sides: tuple[str, ...] = ()
    dispersion_class: str | None = None
    benchmark_probabilities: tuple[float, ...] | None = None
    actual_total: float | None = None
    actual_margin: float | None = None
    competition: str | None = None
    home_team: str | None = None
    away_team: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "class_order", _items(self.class_order, "fixture class order"))
        object.__setattr__(self, "quote_sides", _items(self.quote_sides, "fixture quote sides"))
        if self.benchmark_probabilities is not None:
            object.__setattr__(self, "benchmark_probabilities", _items(
                self.benchmark_probabilities, "fixture benchmark probabilities",
            ))

    @property
    def key(self) -> LineKey:
        return LineKey(self.event_key, self.target, self.line_key, self.observation_id, self.class_order)


@dataclass(frozen=True)
class ClubGoalReference:
    """Independent scorer's fixture totals from a verified comparison snapshot."""

    snapshot_sha256: str
    home_goals: int
    away_goals: int

    def __post_init__(self) -> None:
        _require_hex(self.snapshot_sha256, 64, "club reference snapshot")
        if any(type(goals) is not int or goals < 0 for goals in (self.home_goals, self.away_goals)):
            raise ValueError("club reference goals must be nonnegative integers")


@dataclass(frozen=True)
class VariantPrediction:
    """One identified envelope member for exactly one fixture contract."""

    variant_id: str
    spec_sha256: str
    event_key: str
    target: str
    line_key: str
    class_order: tuple[str, ...]
    probabilities: tuple[float, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "class_order", _items(self.class_order, "variant class order"))
        object.__setattr__(self, "probabilities", _items(self.probabilities, "variant probabilities"))


@dataclass(frozen=True)
class Prediction:
    """Adapter outputs for a line. Settlement labels come from FixtureLine."""

    event_key: str
    line_key: str
    probabilities: tuple[float, ...]
    class_order: tuple[str, ...]
    variants: tuple[VariantPrediction, ...] = ()
    predicted_at: dt.datetime | None = None
    fallback_reason: str | None = None
    expected_total: float | None = None
    expected_margin: float | None = None
    home_rating: float | None = None
    away_rating: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "class_order", _items(self.class_order, "forecast class order"))
        object.__setattr__(self, "probabilities", _items(self.probabilities, "forecast probabilities"))
        object.__setattr__(self, "variants", _items(self.variants, "forecast variants"))


@dataclass(frozen=True)
class Refusal:
    event_key: str
    line_key: str
    reason: str


@dataclass(frozen=True)
class HoldoutEvaluation:
    """Exact prediction/refusal partition of the target scope.

    Redundant legacy score, market and gate fields can only be cross-checked.
    """

    events: tuple[Prediction | ScoredEvent, ...]
    variant_spread_median: float | None = None
    refusals: tuple[Refusal, ...] = ()
    gates: tuple[GateResult, ...] = ()
    market_events: tuple[ScoredEvent, ...] | None = None


@dataclass(frozen=True)
class ScopedFold(WalkFold):
    """Keys allowed at a prediction boundary and the boundary's time precision."""

    source_day: dt.date | None = None
    source_calendar: str | None = None
    international_fixtures: Mapping[str, InternationalFixture] = field(
        default_factory=lambda: MappingProxyType({}), repr=False, compare=False,
    )
    international_benchmarks: Mapping[tuple[str, str], tuple[float, ...]] = field(
        default_factory=lambda: MappingProxyType({}), repr=False, compare=False,
    )
    _international_history: _InternationalHistory | None = field(default=None, repr=False, compare=False)
    nba_fixtures: Mapping[str, NbaFamilyFixture] = field(
        default_factory=lambda: MappingProxyType({}), repr=False, compare=False,
    )
    _nba_history: Mapping[str, object] | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class InternationalFixture:
    """Authorized test identity presented to an international adapter."""

    source_event_key: str
    date: dt.date
    home: str
    away: str
    tournament: str
    neutral: bool


@dataclass(frozen=True)
class RegistryComparison:
    """Round-trip verified ACTIVE evidence or an explicit registry state."""

    roles: LineageRoles | None = None
    spec: ModelSpec | None = None
    issue: str | None = None

    def __post_init__(self) -> None:
        if self.roles is not None:
            object.__setattr__(self, "roles", replace(
                self.roles, candidates=tuple(self.roles.candidates), retired=tuple(self.roles.retired)
            ))


@dataclass(frozen=True)
class RejectionReceipt:
    """Judgment from a verified artifact linked to a committed SUCCESS receipt."""

    run_key: str
    family: str
    lineage: str
    spec_sha256: str
    receipt_id: str
    artifact_sha256: str
    rejected_at: dt.datetime

    def __post_init__(self) -> None:
        _require_hex(self.spec_sha256, 64, "rejected spec")
        _require_hex(self.artifact_sha256, 64, "rejection artifact")
        object.__setattr__(self, "rejected_at", _aware(self.rejected_at, "rejection receipt time"))
        if not _rejection_link(self) or not _identifier(self.receipt_id):
            raise ValueError("inconsistent verified rejection linkage")


@dataclass(frozen=True)
class LegacyRejection:
    """A relevant archived judgment whose terminal linkage cannot be proved."""

    run_key: str
    family: str
    lineage: str
    spec_sha256: str
    reason: str

    def __post_init__(self) -> None:
        _require_hex(self.spec_sha256, 64, "legacy rejected spec")
        if not _rejection_link(self) or not _identifier(self.reason):
            raise ValueError("inconsistent legacy rejection evidence")


def _rejection_link(row):
    run_prefix = f"or|{row.family}|"
    lineage_prefix = f"{row.family}/"
    return (all(map(_identifier, (row.run_key, row.family, row.lineage)))
            and row.run_key.startswith(run_prefix) and _identifier(row.run_key.removeprefix(run_prefix))
            and row.lineage.startswith(lineage_prefix) and _identifier(row.lineage.removeprefix(lineage_prefix)))


def _rejection_facts(rows):
    links = {}
    for row in _items(rows, "prior judgments"):
        if not isinstance(row, (RejectionReceipt, LegacyRejection)):
            raise ValueError("prior judgments must be linked receipts or explicit legacy quarantine")
        key = (row.run_key, row.lineage, row.spec_sha256)
        if links.setdefault(key, row) != row:
            raise ValueError("conflicting evidence for a prior judgment")
    return tuple(links.values())


@dataclass(frozen=True)
class PolicyContext:
    """Receipt-bound registry and rejection evidence captured at original STARTED."""

    started_at: dt.datetime
    registry: Mapping[str, RegistryComparison]
    prior_rejections: tuple[RejectionReceipt | LegacyRejection, ...] = ()
    watermark: str | None = None
    global_issue: str | None = None
    global_frozen: bool = False
    frozen_lineages: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "started_at", _aware(self.started_at, "original STARTED time"))
        object.__setattr__(self, "registry", MappingProxyType(dict(self.registry)))
        object.__setattr__(self, "frozen_lineages", frozenset(self.frozen_lineages))
        object.__setattr__(self, "prior_rejections", _rejection_facts(self.prior_rejections))


@dataclass(frozen=True)
class JudgedRejection:
    lineage: str
    spec_sha256: str
    reasons: tuple[str, ...]
    n_events: int
    n_blocks: int


@dataclass(frozen=True)
class OptimizationRunResult:
    """Pure outcome and proposed specs for the managed publication transaction."""

    outcome: str
    submission: OptimizationRunSubmission
    artifact_bytes: bytes
    artifacts_sha256: str
    run_key: str
    trials: tuple[ScopedTrialRecord, ...]
    selections: Mapping[str, OneSeResult | None]
    decisions: tuple[LineageDecision, ...]
    flags: tuple[str, ...]
    budget_exhausted: bool
    comparison_specs: Mapping[str, ModelSpec]
    verified_active_specs: Mapping[str, str]
    winner_specs: Mapping[str, ModelSpec]
    proposed_specs: Mapping[str, ModelSpec]
    statistical_rejections: tuple[JudgedRejection, ...]

    @property
    def registered_lineages(self) -> tuple[str, ...]:
        """Publication is owned by the managed caller."""
        return ()

    @property
    def winner_spec(self) -> ModelSpec | None:
        if len(self.selections) == 1:
            return next(iter(self.winner_specs.values()), None)
        return None

    @property
    def selection(self) -> tuple[Mapping[str, object] | None, OneSeResult | None]:
        winner = self.winner_spec
        if winner is None:
            return None, None
        return winner.params, next(iter(self.selections.values()))


def banned_spec_digests(
    prior_runs: Iterable[RejectionReceipt | LegacyRejection],
    *,
    now: dt.datetime,
    family: str,
    run_key: str,
) -> dict[str, dict[str, object]]:
    """Apply the family/spec ban using actual judgment instants and its own run key."""
    instant = _aware(now, "original policy instant")
    verified = defaultdict(list)
    quarantined = defaultdict(list)
    for row in _rejection_facts(prior_runs):
        if row.family != family or row.run_key == run_key:
            continue
        evidence = {
            "run_key": row.run_key,
            "lineage": row.lineage,
            "spec_sha256": row.spec_sha256,
        }
        if isinstance(row, RejectionReceipt):
            expiry = row.rejected_at + dt.timedelta(days=RESUBMISSION_BAN_DAYS)
            if not row.rejected_at <= instant < expiry:
                continue
            order = (row.rejected_at, row.run_key, row.lineage, row.receipt_id)
            verified[row.spec_sha256].append((order, dict(evidence,
                reason=_BAN_REASON, evidence_kind="verified_rejection_receipt", receipt_id=row.receipt_id,
                artifact_sha256=row.artifact_sha256, rejected_at=_iso(row.rejected_at),
                expires_at=_iso(expiry),
            )))
        else:
            quarantined[row.spec_sha256].append(dict(evidence,
                reason=_QUARANTINE_REASON, evidence_kind="legacy_rejection",
                issue=row.reason, rejected_at=None, expires_at=None,
            ))
    banned = {}
    for digest in sorted(set(verified) | set(quarantined)):
        unresolved = sorted(quarantined[digest], key=lambda row: (row["run_key"], row["lineage"], row["issue"]))
        selected = dict(max(verified[digest], key=lambda item: item[0])[1] if verified[digest] else unresolved[-1])
        if unresolved:
            selected["quarantined_evidence"] = unresolved
        banned[digest] = selected
    return banned


def optimization_search_descriptor(
    family, *, targets: Iterable[str] | None = None, schedule_id: str = _DEFAULT_SCHEDULE,
    data_cutoff: dt.datetime | None = None,
) -> dict:
    """Pre-inference descriptor suitable for the store's optimization_run_key."""
    selected = normalize_targets(_items(family.targets(), "family targets"),
                                 None if targets is None else _items(targets, "requested targets"))
    if not isinstance(schedule_id, str) or not schedule_id.strip():
        raise ValueError("schedule_id must identify the preregistered schedule")
    descriptor = {
        "label": RULE_VERSION,
        "strategy": _SEARCH_STRATEGY,
        "schedule_id": schedule_id,
        "targets": list(selected),
        "catalog": "incumbent_coordinate_sweep" if family.family_id == "nba-pace-efficiency" else "cartesian",
        "param_defs": [_plain(asdict(definition)) for definition in family.param_space()],
        "continuous_grid_points": CONTINUOUS_GRID_POINTS,
    }
    if data_cutoff is not None:
        descriptor["data_cutoff"] = _iso(data_cutoff)
    return descriptor


class _DeadlineExpired(Exception):
    pass


class _AdapterFailure(Exception):
    """An already-recorded callback exception or invalid result."""


class _BatchIntegrityError(ValueError):
    """Validation failure with a message controlled by the runner."""


def _callback_failure(step, identity, error, reason):
    return {**identity, "step": step, "error_class": type(error).__name__, "reason": reason}


def variant_ids(spec: ModelSpec) -> tuple[str, ...]:
    """Expected variant identities in envelope order, including parameter-only variants."""
    names = tuple(str(item.get("name", index)) for index, item in enumerate(spec.envelope))
    if len(set(names)) != len(names):
        raise ValueError("model envelope has duplicate variant identities")
    return names


def _probabilities(values, arity: int) -> tuple[float, ...]:
    values = tuple(values)
    if len(values) != arity or any(
        not _finite_number(value) or not 0 <= value <= 1
        for value in values
    ) or not math.isclose(math.fsum(values), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise _BatchIntegrityError("forecast must contain a normalized vector of the registered arity")
    return values


def _verify_spec(spec, baseline: ModelSpec) -> None:
    if not isinstance(spec, ModelSpec) or (
        spec.family, spec.procedure_version, spec.data_contract
    ) != (baseline.family, baseline.procedure_version, baseline.data_contract):
        raise ValueError("model spec must match the family's procedure and data contract")
    variant_ids(spec)


def _registry_comparisons(family, lineages, policy, override, baseline):
    declared = {lineage_id(family.family_id, target) for target in family.targets()}
    if (set(policy.registry) | policy.frozen_lineages) - declared:
        raise ValueError("registry context contains an unknown lineage")
    if isinstance(override, Mapping) and set(override) != set(lineages):
        raise ValueError("explicit comparison specs must cover the requested lineages")
    comparisons, actives, evidence, issues = {}, {}, {}, []
    baseline_digest = baseline.spec_sha256()
    for lineage in lineages:
        current = policy.registry.get(lineage, RegistryComparison())
        if not isinstance(current, RegistryComparison):
            raise ValueError("registry evidence must be a RegistryComparison")
        roles, spec = current.roles, current.spec
        if roles is not None and roles.lineage != lineage:
            raise ValueError(f"registry roles do not match the lineage {lineage!r}")
        issue = current.issue
        if roles is not None and roles.baseline != baseline_digest:
            issue = issue or "registry_baseline_digest_mismatch"
        if (roles is None and spec is not None) or (roles is not None and (
            not isinstance(spec, ModelSpec) or spec.spec_sha256() != roles.active
        )):
            issue = issue or "registry_active_digest_unverified"
        if spec is not None:
            try:
                _verify_spec(spec, baseline)
            except ValueError:
                issue = issue or "registry_spec_contract_mismatch"
        if issue:
            issues.append(f"{issue}:{lineage}")
        if roles is not None and spec is not None and not issue:
            comparison = spec
            actives[lineage] = roles.active
            source = "verified_active"
        else:
            comparison = baseline
            source = "baseline_diagnostic_fallback"
        explicit = override.get(lineage) if isinstance(override, Mapping) else override
        if explicit is not None and explicit != comparison:
            raise ValueError(f"explicit comparator does not match verified lineage context {lineage!r}")
        comparisons[lineage] = comparison
        evidence[lineage] = {
            "source": source,
            "issue": issue,
            "spec_sha256": comparison.spec_sha256(),
            "roles": None if roles is None else roles.as_dict(),
        }
    return comparisons, actives, evidence, tuple(issues)


def _fixture_universe(fixture_lines, records, targets):
    scope = defaultdict(list)
    identities, contracts = set(), set()
    order_by_target, registered_facts = {}, {}
    for row in fixture_lines:
        if row.event_key not in records or row.target not in targets:
            raise ValueError("fixture-line manifest includes an unknown event or target")
        order = row.class_order
        if not all(_identifier(value) for value in (
            row.event_key, row.target, row.line_key, row.observation_id, row.market_type, *order,
        )) or len(order) not in (2, 3) or len(set(order)) != len(order):
            raise ValueError("invalid fixture-line identity or class order")
        if order_by_target.setdefault(row.target, order) != order:
            raise ValueError("fixture-line class order changed within a target")
        if type(row.outcome_index) is not int or not 0 <= row.outcome_index < len(order):
            raise ValueError("invalid registered fixture realization")
        if row.line is not None and not _finite_number(row.line):
            raise ValueError("fixture strike must be a finite number")
        if row.dispersion_class is not None and row.dispersion_class not in order:
            raise ValueError("fixture dispersion quote is outside the registered class order")
        if row.quote_sides and (
            len(row.quote_sides) != len(order) or len(set(row.quote_sides)) != len(order)
            or not all(map(_identifier, row.quote_sides))
        ):
            raise ValueError("fixture quote-side order is invalid")
        if row.quote_source is not None and not _identifier(row.quote_source):
            raise ValueError("fixture quote source must be an identifier")
        if row.quote_snapshot_sha256 is not None:
            _require_hex(row.quote_snapshot_sha256, 64, "market snapshot")
        if row.benchmark_probabilities is not None:
            _probabilities(row.benchmark_probabilities, len(order))
        for value in (row.actual_total, row.actual_margin):
            if value is not None and not _finite_number(value):
                raise ValueError("invalid registered fixture measurement")
        for field in ("actual_total", "actual_margin", "home_team", "away_team", "competition"):
            value = getattr(row, field)
            if field in ("home_team", "away_team", "competition") and value is not None and not _identifier(value):
                raise ValueError("fixture source name must be an identifier")
            if value is not None and registered_facts.setdefault((row.event_key, field), value) != value:
                raise ValueError("fixture-line manifest has conflicting measurements for one event")
        identity = (row.event_key, row.target, row.line_key)
        contract = (row.event_key, row.target, row.market_type, row.line)
        if identity in identities or contract in contracts:
            raise ValueError("duplicate or aliased contract in fixture-line manifest")
        identities.add(identity)
        contracts.add(contract)
        scope[(records[row.event_key].group_id, row.target)].append(row)
    return {group: tuple(sorted(rows, key=lambda row: (row.event_key, row.line_key)))
            for group, rows in scope.items()}


@dataclass(frozen=True)
class _VerifiedBatch:
    forecast: ForecastBatch
    predictions: Mapping[LineKey, Prediction]
    spread: Mapping[LineKey, float | None]
    reported_median: float | None
    reported_market: tuple[ScoredEvent, ...] | None
    structural_refusals: frozenset[LineKey] = frozenset()


@dataclass(frozen=True)
class _PredictionBoundary:
    cutoff: dt.datetime | None
    latest_training_label: dt.datetime | None


def _prediction_boundaries(folds, records):
    boundaries = {}
    for fold in folds:
        cutoff = _aware(fold.cutoff, "fold cutoff")
        if fold.source_day is not None:
            boundary = _PredictionBoundary(None, None)
        else:
            latest = max((records[key].available_at for key in fold.train_keys), default=None)
            if latest is not None and latest >= cutoff:
                raise ValueError("fold training labels must be available strictly before the prediction cutoff")
            boundary = _PredictionBoundary(cutoff, latest)
        boundaries.update(dict.fromkeys(fold.test_keys, boundary))
    return boundaries


def _verified_prediction_time(raw, record, boundary, *, is_crypto=False):
    if raw.predicted_at is None:
        return None
    try:
        instant = _aware(raw.predicted_at, "forecast time")
    except ValueError as error:
        raise _BatchIntegrityError("forecast time must be a real aware datetime") from error
    if boundary.cutoff is None:
        raise _BatchIntegrityError("date-only fixture has no registered precise prediction boundary")
    if is_crypto:
        try:
            _require_prediction_before_expiry(record, instant)
        except ValueError as error:
            raise _BatchIntegrityError(str(error)) from error
    if instant > min(boundary.cutoff, record.prediction_at or record.kickoff, record.kickoff):
        raise _BatchIntegrityError("forecast time is after its frozen prediction boundary")
    if boundary.latest_training_label is not None and instant <= boundary.latest_training_label:
        raise _BatchIntegrityError("forecast time is not strictly after its latest training label")
    return instant


def _verify_batch(spec, evaluation, universe, clusters, records, boundaries, intl_benchmarks, *, is_crypto=False):
    if not isinstance(evaluation, HoldoutEvaluation):
        raise _BatchIntegrityError("adapter must return a prediction and refusal batch")
    keyed = {(row.event_key, row.line_key): row for row in universe}
    expected_variants = set(variant_ids(spec))
    digest = spec.spec_sha256()
    rows, raw_predictions, spread, refusals = {}, {}, {}, {}
    for raw in _batch_rows(evaluation.events, "events"):
        if not isinstance(raw, (ScoredEvent, Prediction)) or not all(map(_identifier, (raw.event_key, raw.line_key))):
            raise _BatchIntegrityError("expected a forecast with valid fixture-line identity")
        contract = keyed.get((raw.event_key, raw.line_key))
        if contract is None:
            raise _BatchIntegrityError("forecast returned an unmapped fixture-line")
        key = contract.key
        cluster = clusters[raw.event_key]
        if key in rows:
            raise _BatchIntegrityError("adapter returned duplicate predictions")
        if isinstance(raw, ScoredEvent):
            if contract.class_order not in (("no", "yes"), ("home", "draw", "away")):
                raise _BatchIntegrityError("legacy forecast cannot attest the registered class order")
            if type(raw.outcome_index) is not int or raw.outcome_index != contract.outcome_index:
                raise _BatchIntegrityError("forecast realization conflicts with registered truth")
            if raw.cluster_key != cluster:
                raise _BatchIntegrityError("forecast cluster conflicts with registered match identity")
            raw = Prediction(raw.event_key, raw.line_key, raw.probabilities, contract.class_order)
        if not isinstance(raw, Prediction) or tuple(raw.class_order) != contract.class_order:
            raise _BatchIntegrityError("forecast class order conflicts with fixture contract")
        probabilities = _probabilities(raw.probabilities, len(contract.class_order))
        instant = _verified_prediction_time(
            raw, records[raw.event_key], boundaries[raw.event_key], is_crypto=is_crypto,
        )
        if raw.fallback_reason is not None and (
            not isinstance(raw.fallback_reason, str) or not raw.fallback_reason.strip()
        ):
            raise _BatchIntegrityError("scored fallback requires its documented reason")
        if raw.fallback_reason is not None and spec.family in {
            "nba-pace-efficiency", "club-soccer-frozen", "club-soccer-recalib",
            "intl-elo-poisson", "crypto-gbm",
        }:
            raise _BatchIntegrityError("model family uses explicit refusal for unavailable forecasts")
        benchmark = intl_benchmarks.get((raw.event_key, contract.target)) if _intl_formal(contract) else None
        if benchmark is not None and spec.family == "intl-elo-poisson" and not all(benchmark.team_history):
            raise _BatchIntegrityError("international Elo forecast requires strictly earlier games for both teams")
        if spec.family == "intl-elo-goal-residuals" and _intl_formal(contract):
            if raw.fallback_reason is not None and raw.fallback_reason not in (
                "zero_tournament_mean", "insufficient_recent_matches",
            ):
                raise _BatchIntegrityError("international totals forecast has inconsistent fallback eligibility")
            if benchmark is not None and (raw.fallback_reason != benchmark.fallback(spec) or (
                raw.fallback_reason is not None and probabilities != benchmark.causal
            )):
                raise _BatchIntegrityError("international totals fallback conflicts with its causal baseline")
        for value in (raw.expected_total, raw.expected_margin, raw.home_rating, raw.away_rating):
            if value is not None and not _finite_number(value):
                raise _BatchIntegrityError("invalid forecast validation observable")
        complete = {}
        for variant in raw.variants:
            if not isinstance(variant, VariantPrediction) or not _identifier(variant.variant_id) or (
                variant.variant_id in complete or variant.variant_id not in expected_variants
            ):
                raise _BatchIntegrityError("duplicate or unknown forecast variant")
            if (variant.spec_sha256, variant.event_key, variant.target, variant.line_key,
                tuple(variant.class_order)) != (
                digest, contract.event_key, contract.target, contract.line_key, contract.class_order,
            ):
                raise _BatchIntegrityError("forecast variant does not match its spec or contract")
            complete[variant.variant_id] = _probabilities(variant.probabilities, len(contract.class_order))
        component = contract.dispersion_class
        if component is None and len(contract.class_order) == 2:
            component = contract.class_order[1]
        dispersion = None
        if component is not None and complete and complete.keys() == expected_variants:
            index = contract.class_order.index(component)
            values = [vector[index] for vector in complete.values()]
            dispersion = max(values) - min(values)
        score = ScoredEvent(raw.event_key, raw.line_key, cluster, probabilities, contract.outcome_index)
        rows[key] = Forecast(score, raw.fallback_reason)
        raw_predictions[key] = replace(raw, probabilities=probabilities, class_order=contract.class_order,
                                       variants=tuple(sorted(raw.variants, key=lambda item: item.variant_id)),
                                       predicted_at=instant)
        spread[key] = dispersion
    for raw in _batch_rows(evaluation.refusals, "refusals"):
        if not isinstance(raw, Refusal) or not all(map(_identifier, (raw.event_key, raw.line_key, raw.reason))):
            raise _BatchIntegrityError("a refusal requires a fixture-line identity and explicit reason")
        contract = keyed.get((raw.event_key, raw.line_key))
        if contract is None or contract.key in rows or contract.key in refusals:
            raise _BatchIntegrityError("unmapped, duplicate or contradictory refusal")
        refusals[contract.key] = raw.reason
    if len(rows) + len(refusals) != len(keyed):
        raise _BatchIntegrityError("adapter did not account for every registered fixture-line")
    gates = _batch_rows(evaluation.gates, "gate assertions")
    if gates:
        raise _BatchIntegrityError("adapter supplied gate verdicts without matching recomputed evidence")
    median = evaluation.variant_spread_median
    if median is not None and (not _finite_number(median) or median < 0):
        raise _BatchIntegrityError("invalid reported dispersion")
    market = evaluation.market_events
    if market is not None:
        market = _batch_rows(market, "market assertions")
        if any(not isinstance(row, ScoredEvent) or not _identifier(row.event_key) or not _identifier(row.line_key)
               for row in market):
            raise _BatchIntegrityError("invalid reported market rows")
    return _VerifiedBatch(
        ForecastBatch(tuple(row.key for row in universe), rows, refusals),
        MappingProxyType(raw_predictions), MappingProxyType(spread),
        median, market,
    )


def _batch_rows(values, name):
    if not isinstance(values, (tuple, list)):
        raise _BatchIntegrityError(f"adapter {name} must be a sequence of rows")
    return tuple(values)


def _block_counts(keys, records):
    events = {key.event_key for key in keys}
    blocks = {records[event].floor_block or records[event].group_id for event in events}
    return len(events), len(blocks)


def _coverage(candidate, incumbent, paired_keys, records):
    eligible = candidate.forecast.universe
    games, blocks = _block_counts(paired_keys, records)
    return {
        "eligible_lines": len(eligible),
        "eligible_games": _block_counts(eligible, records)[0],
        "eligible_blocks": _block_counts(eligible, records)[1],
        "candidate_predicted": len(candidate.forecast.predictions),
        "candidate_predicted_games": _block_counts(candidate.forecast.predictions, records)[0],
        "incumbent_predicted_games": _block_counts(incumbent.forecast.predictions, records)[0],
        "incumbent_predicted": len(incumbent.forecast.predictions),
        "candidate_only_refusals": len(candidate.forecast.refusals.keys() - incumbent.forecast.refusals.keys()),
        "incumbent_only_refusals": len(incumbent.forecast.refusals.keys() - candidate.forecast.refusals.keys()),
        "joint_refusals": len(candidate.forecast.refusals.keys() & incumbent.forecast.refusals.keys()),
        "paired_lines": len(paired_keys),
        "paired_games": games,
        "paired_blocks": blocks,
        "paired_mask_sha256": _mask_digest(paired_keys),
    }


def _mask_digest(keys):
    return sha256(_canonical_json([asdict(key) for key in keys])).hexdigest()


def _market_index(observations):
    from polymarket_alpha_lab.papersim.optimize.dataset import MarketObservation

    index = defaultdict(list)
    for obs in observations:
        if not isinstance(obs, MarketObservation) or not all(map(_identifier, (
            obs.match_key, obs.market_type, obs.side, obs.source,
        ))):
            raise ValueError("market observations require a registered market identity")
        if obs.line is not None and not _finite_number(obs.line):
            raise ValueError("market observation strike must be a finite number")
        if obs.snapshot_sha256 is not None:
            _require_hex(obs.snapshot_sha256, 64, "market observation snapshot")
        if not _finite_number(obs.mid):
            raise ValueError("market observation midpoint must be a finite number")
        obs = replace(obs, observed_at=_aware(obs.observed_at, "market observation time"))
        index[(obs.match_key, obs.market_type, obs.line)].append(obs)
    return index


def _market_pair(keys, contracts, records, index, candidate, incumbent, clusters, snapshot_refs):
    rows, missing, identity = [], {}, []
    for key in keys:
        contract, record = contracts[key], records[key.event_key]
        instant = contract.decision_at
        reason = "no_registered_decision_quote"
        if instant is not None and contract.quote_source and contract.quote_snapshot_sha256 in snapshot_refs:
            reason = "same_time_forecast_unverified"
            if (
                record.source_day is None and record.kickoff is not None
                and record.prediction_at == instant < record.kickoff
                and candidate.predictions[key].predicted_at == instant
                and incumbent.predictions[key].predicted_at == instant
            ):
                reason = "no_unique_source_timed_quote"
                sides = contract.quote_sides or contract.class_order
                by_side = defaultdict(list)
                for obs in index[(contract.event_key, contract.market_type, contract.line)]:
                    if obs.side in sides and obs.source == contract.quote_source and (
                        obs.snapshot_sha256 == contract.quote_snapshot_sha256
                        and obs.observed_at == instant
                    ):
                        by_side[obs.side].append(obs)
                if set(by_side) == set(sides) and all(len(found) == 1 for found in by_side.values()):
                    try:
                        probabilities = _probabilities(
                            [by_side[side][0].mid for side in sides], len(contract.class_order)
                        )
                    except ValueError:
                        reason = "invalid_market_probability_vector"
                    else:
                        rows.append(ScoredEvent(
                            key.event_key, key.line_key, clusters[key.event_key], probabilities,
                            contract.outcome_index,
                        ))
                        identity.append({
                            "event_key": key.event_key, "line_key": key.line_key,
                            "observation_id": key.observation_id, "source": contract.quote_source,
                            "observed_at": _iso(instant), "class_order": list(contract.class_order),
                            "sides": list(sides), "snapshot_sha256": contract.quote_snapshot_sha256,
                        })
                        continue
        missing[reason] = missing.get(reason, 0) + 1
    return tuple(rows), {"missing": missing, "observations": identity}


def _spread_median(batch, keys):
    values = [batch.spread[key] for key in keys]
    median = statistics.median(values) if values and all(value is not None for value in values) else None
    if batch.reported_median is not None and (
        median is None or not math.isclose(median, batch.reported_median, rel_tol=1e-12, abs_tol=1e-15)
    ):
        raise ValueError("reported dispersion is inconsistent with the matched variants")
    return median


def _report_check(gate, *, source, denominator, unit, required=False, reason=None):
    return replace(gate, numbers=(
        *gate.numbers,
        ("source", source), ("scope_denominator", str(denominator)), ("unit", unit),
        ("required", str(required).lower()),
        *((("reason", reason),) if reason else ()),
    ))


def _unavailable(gate, check, *, source, denominator, unit, reason, required=False):
    return _report_check(GateResult(gate, check, VERDICT_NOT_ASSESSABLE, ()),
                         source=source, denominator=denominator, unit=unit,
                         reason=reason, required=required)


def _ranks(values):
    levels = defaultdict(list)
    for index, value in enumerate(values):
        levels[value].append(index)
    ranks = [0.0] * len(values)
    position = 1
    for value in sorted(levels):
        indices = levels[value]
        rank = position + (len(indices) - 1) / 2
        for index in indices:
            ranks[index] = rank
        position += len(indices)
    return ranks


def _correlation(left, right, *, ranked=False):
    if ranked:
        left, right = _ranks(left), _ranks(right)
    try:
        return statistics.correlation(left, right)
    except statistics.StatisticsError:
        return None


def _corr_p(r, count):
    if r is None or count < 4:
        return None
    if abs(r) >= 1.0:
        return 0.0
    return math.erfc(abs(math.atanh(r)) * math.sqrt((count - 3) / 2))


def _check(gate, name, value, threshold, direction, count, minimum, *, source, unit, required=True):
    return _report_check(
        gate_abc_stat(gate, name, value, threshold=threshold, direction=direction,
                      denominator=count, min_denominator=minimum),
        source=source, denominator=count, unit=unit, required=required,
        reason="missing_required_observable" if value is None else None,
    )


def _event_observables(keys, contracts, batch, attr, known):
    rows = {}
    for key in keys:
        model = getattr(batch.predictions[key], attr)
        actual = getattr(contracts[key], known)
        data = (model, actual)
        if rows.setdefault(key.event_key, data) != data:
            raise ValueError("one game has inconsistent gate observables across lines")
    if not rows or any(model is None or actual is None for model, actual in rows.values()):
        return (), ()
    return tuple(zip(*rows.values(), strict=True))


def _rating_sanity(keys, contracts, batch, *, intl=False):
    teams = defaultdict(list)
    seen = set()
    for key in keys:
        if key.event_key in seen:
            continue
        seen.add(key.event_key)
        contract, prediction = contracts[key], batch.predictions[key]
        if (
            not contract.home_team or not contract.away_team or contract.actual_margin is None
            or prediction.home_rating is None or prediction.away_rating is None
        ):
            return None, 0
        home = 0.5 if contract.actual_margin == 0 else float(contract.actual_margin > 0)
        teams[contract.home_team].append((prediction.home_rating, home))
        teams[contract.away_team].append((prediction.away_rating, 1 - home))
    qualified = [tuple(zip(*values, strict=True)) for values in teams.values()
                 if not intl or len(values) >= 30]
    return _correlation(
        [statistics.fmean(ratings) for ratings, _ in qualified],
        [statistics.fmean(results) for _, results in qualified], ranked=True,
    ), len(qualified)


def _metrics(card):
    return {"log_loss": card.log_loss, "brier": card.brier}


def _benchmark_gate(name, fixed, metrics, spec, keys, contracts, model, model_metrics,
                    games, minimum, report):
    vectors = [fixed if fixed is not None else contracts[key].benchmark_probabilities for key in keys]
    available = bool(vectors) and all(vector is not None for vector in vectors)
    baseline = _metrics(build_scorecard(spec, tuple(
        replace(row, probabilities=vector) for row, vector in zip(model, vectors, strict=True)
    ))) if available and all(len(vector) == len(row.probabilities)
                             for row, vector in zip(model, vectors, strict=True)) else {}
    return _report_check(replace(
        gate_d_walkforward(model_metrics, baseline, games, min_events=minimum, metrics=metrics),
        check=f"walk_forward_beats_{name}",
    ), source=report, denominator=games, unit="paired games", required=True,
        reason=None if baseline else "published_benchmark_missing_on_comparison_cohort")


def _intl_research_loss(rows, *, over_index=None):
    """Replicate the WP-INTL-3 float accumulation in the supplied source order."""
    total = 0.0
    clipped = 0
    n = 0
    for probabilities, outcome in rows:
        p = probabilities[outcome if over_index is None else over_index]
        q = min(max(p, _INTL_RESEARCH_EPS), 1.0 - _INTL_RESEARCH_EPS)
        clipped += q != p
        total += -math.log(q if over_index is None or outcome == over_index else 1.0 - q)
        n += 1
    return (total / n if n else None), clipped


def _intl_gate_d(target, keys, contracts, batch, source, benchmarks, scope):
    formal = {key for key in keys if key.target == target and _intl_formal(contracts[key])}
    all_events = {key.event_key for key in keys}
    source_rows = {} if source is None else source.identities
    unclassified = all_events - source_rows.keys()
    eligible = {key.event_key for key in batch.forecast.universe if key.target == target and (
        _intl_formal(contracts[key]) and key.event_key in source_rows
        and source_rows[key.event_key].tournament == _INTL_NATIONS_LEAGUE
    )}
    missing_contracts = {event for event in (all_events & source_rows.keys()) - eligible
                         if source_rows[event].tournament == _INTL_NATIONS_LEAGUE}
    cohort = sorted((key for key in formal if key.event_key in source_rows
                     and source_rows[key.event_key].tournament == _INTL_NATIONS_LEAGUE),
                    key=lambda key: source_rows[key.event_key].index)
    missing_benchmarks = sum((key.event_key, target) not in benchmarks for key in cohort)
    if unclassified:
        reason = "missing_verified_match_source_or_competition"
    elif missing_contracts:
        reason = "missing_registered_research_contract"
    elif missing_benchmarks:
        reason = "missing_verified_causal_benchmark"
    elif len(cohort) < _INTL_GATE_D_MIN:
        reason = "insufficient_paired_uefa_nations_league_matches"
    else:
        reason = None
    score_rows = tuple(batch.forecast.predictions[key].row for key in cohort)
    over_index = cohort[0].class_order.index("over") if cohort and target == "over_under" else None
    model_loss, model_clipped = _intl_research_loss(
        ((row.probabilities, row.outcome_index) for row in score_rows), over_index=over_index,
    )
    names = ("tournament_mean_total", "coin_flip") if target == "over_under" else (
        "causal_tournament_base_rate", "uniform",
    )
    references = [(source_rows[key.event_key].index, source_rows[key.event_key].event_key) for key in cohort]
    model_refusals = batch.forecast.refusals.keys() - batch.structural_refusals
    actual_refused = {key.event_key for key in model_refusals if key.event_key in eligible and _intl_formal(contracts[key])}
    results = []
    for name, field_name in zip(names, ("causal", "fixed"), strict=True):
        baseline_loss, baseline_clipped = _intl_research_loss((
            (getattr(benchmarks[key.event_key, target], field_name), row.outcome_index)
            for key, row in zip(cohort, score_rows, strict=True)
        ), over_index=over_index) if not missing_benchmarks else (None, 0)
        gate = gate_d_walkforward(
            {"log_loss": model_loss if reason is None else None},
            {"log_loss": baseline_loss if reason is None else None},
            len(cohort), min_events=_INTL_GATE_D_MIN, metrics=("log_loss",),
        )
        numbers = dict(gate.numbers)
        numbers.update({
            "n_events": str(len(cohort)), "model_log_loss": repr(model_loss), "baseline_log_loss": repr(baseline_loss),
            "model_clipped": str(model_clipped), "baseline_clipped": str(baseline_clipped),
            "scope": scope, "available_matches": str(len(eligible)), "refused_matches": str(len(actual_refused)),
            "all_eligible_matches": str(len({key.event_key for key in batch.forecast.universe})),
            "all_refused_matches": str(len({key.event_key for key in model_refusals})),
            "all_comparison_matches": str(len(all_events)), "unclassified_matches": str(len(unclassified)),
            "missing_formal_contracts": str(len(missing_contracts)), "missing_causal_benchmarks": str(missing_benchmarks),
            "source_snapshot_sha256": source.ref.sha256 if source else "",
            "source_match_mask_sha256": sha256(_canonical_json(references)).hexdigest(),
            "ordered_original_row_indices": ",".join(str(index) for index, _ in references),
            "order": "source day, original match row index",
        })
        results.append(_report_check(replace(gate, check=f"walk_forward_beats_{name}", numbers=tuple(numbers.items())),
                                     source=_INTL_REPORT, denominator=len(cohort),
                                     unit="paired UEFA Nations League matches", reason=reason, required=True))
    return tuple(results)


def _validation_gates(family, target, spec, keys, contracts, batch, model, market,
                      *, source_integrity, source_count, source_checks=(), international=None,
                      intl_benchmarks=None, scope="paired_holdout"):
    report = {
        "nba": _NBA_REPORT,
        "intl": _INTL_REPORT,
        "club-soccer": "round39/eredivisie_validation.md",
    }.get(family)
    if report is None:
        return tuple(_unavailable(gate, "family_validation", source="MO-3", denominator=len(keys),
                                  unit="lines", reason="no_applicable_registered_family_check")
                     for gate in "ABCDE")
    games = len({key.event_key for key in keys})
    checks = [_check("A", "registered_snapshot_byte_identity", source_integrity, 1, "gte",
                     source_count, 1, source="registered snapshot manifest", unit="source snapshots")]
    checks.extend(source_checks)
    market_rule = None
    if family == "nba":
        model_totals, actual_totals = _event_observables(keys, contracts, batch, "expected_total", "actual_total")
        r = _correlation(model_totals, actual_totals)
        p = _corr_p(r, len(model_totals))
        checks.extend((
            _check("B", "expected_vs_actual_total_pearson_r", r, 0.25, "gte", games, 20,
                   source=report, unit="paired games"),
            _check("B", "expected_vs_actual_total_pearson_p", p, math.nextafter(0.05, 0.0), "lte",
                   games, 20, source=report, unit="paired games"),
        ))
        rho, teams = _rating_sanity(keys, contracts, batch)
        checks.append(_check("C", "net_rating_vs_win_rate_spearman", rho, 0.5, "gte", teams, 30,
                             source=report, unit="teams on paired games"))
        if target == "moneyline":
            market_rule = (0.06, games, 1)
        minimum = 20
    elif family == "intl":
        if target == "over_under":
            model_totals, actual_totals = _event_observables(keys, contracts, batch, "expected_total", "actual_total")
            un = [key for key in keys if contracts[key].competition == "UEFA Nations League"]
            bias = abs(statistics.fmean(model_totals) - statistics.fmean(actual_totals)) if model_totals else None
            probability_bias = abs(statistics.fmean(
                batch.forecast.predictions[key].row.probabilities[1] - contracts[key].outcome_index
                for key in un
            )) if un else None
            checks.extend((
                _check("B", "mean_total_bias", bias, 0.15, "lte", len(un), 20,
                       source=report, unit="paired UEFA Nations League matches"),
                _check("B", "over_2_5_probability_bias", probability_bias, 0.05, "lte", len(un), 20,
                       source=report, unit="paired UEFA Nations League matches"),
                _unavailable("C", "elo_rating_sanity", source=report, denominator=games,
                             unit="paired games", reason="goal_residual_model_has_no_elo_ratings"),
            ))
        else:
            predicted, actual = _event_observables(keys, contracts, batch, "expected_margin", "actual_margin")
            r = _correlation(predicted, actual)
            p = _corr_p(r, len(predicted))
            un = len({key.event_key for key in keys if contracts[key].competition == "UEFA Nations League"})
            checks.extend((
                _check("B", "expected_vs_actual_margin_pearson_r", r, 0.15, "gte", un, 20,
                       source=report, unit="paired UEFA Nations League matches"),
                _check("B", "expected_vs_actual_margin_pearson_p", p, math.nextafter(0.05, 0.0), "lte",
                       un, 20, source=report, unit="paired UEFA Nations League matches"),
            ))
            rho, teams = _rating_sanity(keys, contracts, batch, intl=True)
            checks.append(_check("C", "elo_vs_win_rate_spearman", rho, 0.5, "gte", teams, 30,
                                 source=report, unit="teams with 30 paired matches"))
        minimum = 20
        market_rule = (0.05, games, 10)
    else:
        minimum = 90
        if target == "match_winner":
            market_rule = (0.06, 0, 8)
    standard_metrics = ("brier", "log_loss")
    if family == "nba":
        benchmarks = [("coin_flip", (0.5, 0.5), standard_metrics if target == "moneyline" else ("log_loss",))]
        if target == "moneyline":
            benchmarks.append(("home_always", (0.0, 1.0), standard_metrics))
    else:
        benchmarks = [("published_prior_season_baseline", None, standard_metrics)]
    if family == "intl":
        d, *additional_d = _intl_gate_d(target, keys, contracts, batch, international, intl_benchmarks or {}, scope)
    else:
        model_metrics = _metrics(build_scorecard(spec, model))
        d, *additional_d = [
            _benchmark_gate(name, probabilities, metrics, spec, keys, contracts, model, model_metrics,
                            games, minimum, report)
            for name, probabilities, metrics in benchmarks
        ]
    if market_rule is None:
        e = _unavailable("E", "market_mad", source=report, denominator=games, unit="paired games",
                         reason="no_published_market_gate_for_target")
    else:
        mad, flips, count = market_rule
        model_by_key = {(row.event_key, row.line_key): row for row in model}
        e = _report_check(gate_e_market(
            [model_by_key[(row.event_key, row.line_key)].probabilities for row in market],
            [row.probabilities for row in market], mad_max=mad, flips_max=flips, min_events=count,
        ), source=report, denominator=len(market), unit="same-time lines",
            reason="no_same_time_market_quotes" if not market else None)
    return gate_results((*checks, *additional_d), d, e)


def _club_records(audit: HistoryAudit, data_cutoff):
    """Certify the MO-4 official partition and reconcile its two event clocks."""
    from polymarket_alpha_lab.papersim.optimize import dataset

    if not isinstance(audit, dataset.HistoryAudit) or any(
        not isinstance(row, dataset.ScheduleFixture) for row in audit.schedule
    ) or any(not isinstance(row, dataset.HistoricalMatch) for row in audit.matches):
        raise ValueError("club history requires its official schedule and historical match records")
    official_groups = defaultdict(list)
    rounds = {}
    for league, season in sorted({(row.league, row.season) for row in audit.schedule}):
        fixtures, results, complete = dataset._round_rows(audit, league, season)
        for number, official in fixtures.items():
            group = f"{league}/{season}/{number}"
            matchups = {row.fixture_id: row for row in official}
            settled, source_times = [], []
            if len(matchups) != len(official):
                raise ValueError("official round contains duplicate fixture identities")
            for match in results.get(number, ()):
                if match.schedule_match_id not in matchups:
                    continue
                schedule = matchups[match.schedule_match_id]
                if (match.home_team, match.away_team) != (schedule.home_team, schedule.away_team):
                    raise ValueError("official/source fixture teams disagree")
                official_kickoff = _aware(schedule.kickoff, "official fixture kickoff")
                source_kickoff = _aware(match.source_kickoff or match.kickoff, "source fixture kickoff")
                source_times.append(source_kickoff)
                later = max(official_kickoff, source_kickoff)
                available = later + dt.timedelta(hours=3)
                if match.available_at is not None:
                    available = max(available, _aware(match.available_at, "source result availability"))
                if available < data_cutoff:
                    settled.append((match, later, available))
            kickoff = min(
                [_aware(row.kickoff, "official round kickoff") for row in official] +
                source_times
            )
            rounds[group] = (league, season, number, kickoff, bool(complete.get(number)),
                             len(official))
            for match, later, available in settled:
                year, week, _ = kickoff.isocalendar()
                official_groups[group].append((
                    FixtureRecord(match.match_id, group, later, available, floor_block=f"{year}-W{week:02d}",
                                  prediction_at=kickoff),
                    match,
                ))
    expected = {rec.event_key: rec for entries in official_groups.values() for rec, _ in entries}
    if len(expected) != sum(map(len, official_groups.values())):
        raise ValueError("MO-4 official match identities are not unique")
    return tuple(expected.values()), (official_groups, rounds)


def club_fixture_records(audit: HistoryAudit, data_cutoff: dt.datetime) -> tuple[FixtureRecord, ...]:
    """Outcome-free official identities derived from the registered MO-4 audit."""
    return _club_records(audit, _aware(data_cutoff, "data_cutoff"))[0]


def _club_folds(audit, club_data, group_ids, min_train_blocks):
    from polymarket_alpha_lab.papersim.optimize import dataset
    from polymarket_alpha_lab.papersim.optimize.walkforward import SkippedBlock

    official_groups, rounds = club_data
    folds, skipped = [], []
    for group in group_ids:
        league, season, number, boundary, complete, count = rounds[group]
        test = official_groups[group]
        if number <= min_train_blocks:
            skipped.append(SkippedBlock(group, "insufficient_training_blocks", boundary))
            continue
        if not complete or len(test) != count:
            skipped.append(SkippedBlock(group, "target_round_incomplete", boundary))
            continue
        train_groups = []
        for earlier in range(1, number):
            prefix = f"{league}/{season}/{earlier}"
            prior = rounds.get(prefix)
            rows = official_groups.get(prefix, ())
            if prior is None or not prior[4] or len(rows) != prior[5] or (
                any(record.available_at >= boundary for record, _ in rows)
            ):
                skipped.append(SkippedBlock(group, "prior_round_prefix_incomplete_at_cutoff", boundary))
                break
            train_groups.append(prefix)
        else:
            prior_seasons = sorted({yr for lg, yr, *_ in rounds.values()
                                    if lg == league and int(yr) < int(season)})
            for prior_season in prior_seasons:
                prior_groups = [name for name, row in rounds.items()
                                if row[:2] == (league, prior_season)]
                history = tuple((record, match) for name in prior_groups
                                for record, match in official_groups.get(name, ()))
                if (
                    dataset._complete_season(audit, league, prior_season, tuple(match for _, match in history))
                    and all(record.available_at < boundary for record, _ in history)
                ):
                    train_groups.extend(prior_groups)
            train = [record for name in train_groups for record, _ in official_groups[name]]
            train.sort(key=lambda record: (record.kickoff, record.event_key))
            folds.append(ScopedFold(group, boundary, tuple(record.event_key for record in train),
                                    tuple(sorted(record.event_key for record, _ in test)),
                                    tuple(train_groups)))
    return tuple(folds), tuple(skipped)


def club_model_history(audit: HistoryAudit, fold: ScopedFold):
    """Adapt the exact runner keys to the MO-4 model history contract."""
    from polymarket_alpha_lab.papersim.optimize.dataset import Fold, to_model_history

    league, season, number = fold.block_id.split("/")
    matches = {match.match_id: match for match in audit.matches}
    official = {(row.league, row.season, row.fixture_id): row for row in audit.schedule}

    def reconciled(key):
        match = matches[key]
        schedule = official[(match.league, match.season, match.schedule_match_id)]
        kickoff = max(_aware(schedule.kickoff, "official fixture kickoff"),
                      _aware(match.source_kickoff or match.kickoff, "source fixture kickoff"))
        available = _aware(match.available_at, "source result availability") if match.available_at is not None else None
        return replace(match, kickoff=kickoff, available_at=available)

    train = tuple(reconciled(key) for key in fold.train_keys)
    test = tuple(reconciled(key) for key in fold.test_keys)
    completed_groups = frozenset(
        (match.league, match.season, match.matchweek) for match in train
    )
    return to_model_history(Fold(league, season, int(number), fold.cutoff,
                                 train, test, completed_groups, 3.0))


def international_history_at(
    snapshot: Mapping[str, object] | None = None, fold: ScopedFold | None = None, *,
    matches_by_key: Mapping[str, Mapping[str, object]] | None = None,
) -> dict:
    """A safe international replay envelope over one authorized source-day prefix."""
    if not isinstance(fold, ScopedFold) or fold.source_day is None:
        raise ValueError("international replay requires a source-day fold")
    if fold._international_history is not None:
        return fold._international_history.snapshot()
    if snapshot is None or matches_by_key is None:
        raise ValueError("international replay requires its snapshot and match identities")
    boundary = fold.source_day.isoformat()
    indexed = defaultdict(list)
    for index, row in enumerate(snapshot["matches"]):
        indexed[row["date"], row["home"], row["away"]].append((index, row))
    positions = {}
    for key in fold.train_keys:
        row = matches_by_key[key]
        identity = (row["date"], row["home"], row["away"])
        candidates = indexed[identity]
        if len(candidates) != 1 or candidates[0][1] != row or candidates[0][0] in positions:
            raise ValueError("international replay requires a unique source row identity")
        if row["date"] >= boundary:
            raise ValueError("international replay can use only strictly earlier source dates")
        positions[candidates[0][0]] = row
    matches = tuple(positions[i] for i in sorted(positions, key=lambda i: (positions[i]["date"], i)))
    # The current teams table is a forbidden fallback. The historical Elo model
    # rebuilds ratings and tournament priors from these same matches.
    return {
        "generated_at": snapshot["generated_at"],
        "source": snapshot["source"],
        "matches": matches,
        "teams": {},
        "elo_history": (),
    }


def nba_history_at(
    snapshot: Mapping[str, object] | None = None, fold: ScopedFold | None = None, *,
    game_keys: Mapping[tuple[str, str, str], str] | None = None,
) -> dict:
    """Use the sealed earlier NBA games, or verify caller-supplied legacy bindings."""
    if not isinstance(fold, ScopedFold) or fold.source_day is None:
        raise ValueError("NBA replay requires a source-day fold")
    if fold._nba_history is not None:
        return dict(fold._nba_history)
    if snapshot is None or game_keys is None:
        raise ValueError("NBA replay requires its snapshot and game identities")
    paired, _, _ = _nba_pairs(snapshot)
    selected = [(key, identity) for identity, key in game_keys.items() if key in fold.train_keys]
    if len(selected) != len(fold.train_keys) or {key for key, _ in selected} != set(fold.train_keys) or any(
        identity not in paired for _, identity in selected
    ):
        raise ValueError("NBA replay keys do not identify unique reciprocal source games")
    return _nba_history(snapshot, fold, dict(selected), paired)


def _nba_history(snapshot, fold, identities, source_games):
    boundary = fold.source_day.isoformat()
    rows = defaultdict(list)
    for day, home, away in sorted(identities[key] for key in fold.train_keys):
        if day >= boundary:
            raise ValueError("NBA replay can use only strictly earlier source dates")
        game = source_games[day, home, away]
        rows[home].append(game.home)
        rows[away].append(game.away)
    return {
        "generated_at": snapshot["generated_at"], "source": snapshot["source"],
        "season": snapshot["season"], "injuries": {},
        "teams": {team: {"display": snapshot["teams"][team]["display"], "games": tuple(games)}
                  for team, games in rows.items()},
    }


def international_replay_prediction(family, fitted, fixture):
    """Refuse an international Elo compatibility-table forecast."""
    prediction = family.predict(fitted, fixture)
    if getattr(prediction, "ratings_stale", False) or (
        getattr(prediction, "rating_source", "replay") != "replay"
    ):
        return None
    return prediction


def _causal_records(records, source_calendar):
    source_days, timed = {}, []
    has_days = any(record.source_day is not None or record.label_day is not None for record in records)
    if has_days and (not isinstance(source_calendar, str) or not source_calendar.strip()):
        raise ValueError("date-only inputs require the named source calendar")
    if not has_days and source_calendar is not None:
        raise ValueError("a named source-day calendar requires date-only records")
    for record in records:
        if has_days:
            if record.kickoff is not None or record.prediction_at is not None:
                raise ValueError("date-only fixtures cannot carry fabricated precise fixture times")
            if type(record.source_day) is not dt.date or type(record.label_day) is not dt.date:
                raise ValueError("date-only fixtures must consistently provide source and label dates")
            source_days[record.event_key] = SourceDay(
                source_calendar, record.source_day, record.label_day, record.available_at
            )
            timed.append(record)
        else:
            kickoff = _aware(record.kickoff, "fixture kickoff")
            available = _aware(record.available_at, "label availability")
            prediction = kickoff if record.prediction_at is None else _aware(record.prediction_at, "prediction time")
            if available < kickoff or prediction > kickoff:
                raise ValueError("fixture timing contradicts its prediction or availability")
            timed.append(replace(record, kickoff=prediction, available_at=available))
    return tuple(timed), source_days if has_days else None


def _registered_records(records):
    normalized = {}
    for row in _items(records, "fixture records"):
        if not isinstance(row, FixtureRecord) or not all(map(_identifier, (row.event_key, row.group_id))) or (
            row.event_key in normalized
        ):
            raise ValueError("records must have unique event keys and use FixtureRecord")
        if row.intl_source_row is not None and not isinstance(row.intl_source_row, IntlSourceRow):
            raise ValueError("international fixture source must use IntlSourceRow")
        clocks = {field: _aware(getattr(row, field), f"fixture {field}") for field in (
            "kickoff", "available_at", "prediction_at", "expiry_at",
        ) if getattr(row, field) is not None}
        normalized[row.event_key] = replace(row, **clocks)
    return normalized


def _registered_lines(fixture_lines):
    checked = []
    for row in _items(fixture_lines, "fixture lines"):
        if not isinstance(row, FixtureLine) or not _identifier(row.event_key) or not _identifier(row.target):
            raise ValueError("fixture contracts must use FixtureLine with valid event and target identifiers")
        if row.decision_at is not None:
            instant = _aware(row.decision_at, "registered quote decision time")
            row = replace(row, decision_at=instant)
        checked.append(row)
    return tuple(checked)


def _scoped_folds(folds, records, source_calendar):
    return tuple(ScopedFold(
        fold.block_id, _aware(fold.cutoff, "fold cutoff"), fold.train_keys, fold.test_keys, fold.train_block_ids,
        records[fold.test_keys[0]].source_day, source_calendar,
    ) for fold in folds)


def _verified_sources(source_artifacts, snapshot_refs):
    evidence = {}
    for digest, content in source_artifacts.items():
        _require_hex(digest, 64, "source hash")
        if digest not in snapshot_refs or not isinstance(content, bytes) or sha256(content).hexdigest() != digest:
            raise ValueError("source bytes do not match a registered snapshot reference")
        evidence[digest] = len(content)
    return evidence


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _source_document(archives, evidence, ref):
    if not isinstance(ref, SourceArtifactRef) or evidence.get(ref.sha256) != ref.byte_length:
        raise ValueError("source artifact does not match its registered digest and byte length")
    try:
        document = json.loads(archives[ref.sha256])
    except (ValueError, UnicodeError) as error:
        raise ValueError("registered source artifact is not a JSON object") from error
    if not isinstance(document, dict):
        raise ValueError("registered source artifact is not a JSON object")
    return document


def _iso_date(value):
    try:
        day = dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return day if day.isoformat() == value else None


def _crypto_expiry_days(records, expiry_groups, archives, evidence):
    groups = {}
    if expiry_groups is not None:
        document = _source_document(archives, evidence, expiry_groups)
        if set(document) != {"expiry_groups"} or not isinstance(document["expiry_groups"], list):
            raise ValueError("registered crypto source must list expiry-day groups")
        for entry in document["expiry_groups"]:
            if not isinstance(entry, dict) or set(entry) != {"group_id", "expiry_day"}:
                raise ValueError("invalid registered crypto expiry group")
            group, day = entry["group_id"], _iso_date(entry["expiry_day"])
            if not _identifier(group) or day is None or group in groups:
                raise ValueError("invalid or duplicated registered crypto expiry group")
            groups[group] = day

    bindings = {}
    alias_days = {}
    for key, record in records.items():
        days = {day for day in (
            _iso_date(record.group_id), groups.get(record.group_id),
            record.expiry_at.date() if record.expiry_at is not None else None,
        ) if day is not None}
        if len(days) != 1:
            raise ValueError(f"crypto event {key!r} requires one consistent UTC expiry day from its contract or source group")
        day = days.pop().isoformat()
        previous = alias_days.setdefault(record.group_id, day)
        if previous != day:
            raise ValueError(f"crypto group {record.group_id!r} spans UTC expiry days {previous} and {day}")
        if record.floor_block is not None and record.floor_block != day:
            raise ValueError(f"crypto event {key!r} floor block conflicts with the verified UTC expiry day {day}")
        records[key] = replace(record, floor_block=day)
        bindings[key] = {"group_id": record.group_id, "expiry_day": day,
                         "expiry_at": None if record.expiry_at is None else _iso(record.expiry_at)}
    return bindings


def _require_prediction_before_expiry(record, start, end=None):
    """Prove an instant or an entire source-day interval precedes expiry."""
    expiry = record.expiry_at
    if expiry is not None:
        boundary = expiry
        expired = start >= expiry
        limit = _iso(expiry)
    else:
        day = dt.date.fromisoformat(record.floor_block)
        boundary = dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)
        expired = start.date() > day
        limit = f"UTC expiry day {day}"
    if expired:
        raise ValueError(f"crypto event {record.event_key!r} prediction {_iso(start)} is at or after expiry ({limit})")
    if start >= boundary or (end is not None and end > boundary):
        raise ValueError(f"crypto event {record.event_key!r} cannot prove prediction before expiry ({limit})")


_SECONDS_PER_DAY = 86_400


@lru_cache(maxsize=512)
def _crypto_utc_offset_changes(day: dt.date, timezone: ZoneInfo) -> tuple[tuple[int, int | None], ...]:
    """Return the start of each UTC offset segment in seconds since UTC midnight."""
    # TZif transition timestamps, offsets and the POSIX continuation rules
    # have whole-second precision. Check every possible change in the loaded
    # ZoneInfo instance, including its continuation rules. Cache UTC days so
    # overlapping civil-date windows share the work.
    step = dt.timedelta(seconds=1)
    instant = dt.datetime.combine(day, dt.time.min, tzinfo=timezone)
    to_local = timezone.fromutc
    previous = None
    changes = []
    for second in range(_SECONDS_PER_DAY):
        try:
            offset = to_local(instant) - instant  # The same tzinfo makes this wall-time subtraction.
        except OverflowError:
            if day not in (dt.date.min, dt.date.max):
                raise
            offset = None  # The local instant lies outside the datetime calendar.
        if offset != previous:
            offset_seconds = None if offset is None else int(offset.total_seconds())
            if offset_seconds is not None and abs(offset_seconds) >= _SECONDS_PER_DAY:
                raise ValueError("source timezone has an invalid UTC offset")
            changes.append((second, offset_seconds))
            previous = offset
        if second < _SECONDS_PER_DAY - 1:
            instant += step
    changes.append((_SECONDS_PER_DAY, None))
    return tuple(changes)


def _crypto_day_bounds(day: dt.date, timezone: dt.timezone | ZoneInfo) -> tuple[dt.datetime, dt.datetime]:
    midnight = dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)
    if isinstance(timezone, dt.timezone):
        start = midnight - timezone.utcoffset(None)
        return start, start + dt.timedelta(days=1)

    first = last = None
    ordinal = day.toordinal()
    # A datetime's offset has magnitude less than a day, so only these UTC
    # dates can contribute to the preimage of the requested local date.
    for utc_ordinal in range(max(dt.date.min.toordinal(), ordinal - 1),
                             min(dt.date.max.toordinal(), ordinal + 1) + 1):
        utc_day = dt.date.fromordinal(utc_ordinal)
        utc_midnight = dt.datetime.combine(utc_day, dt.time.min, tzinfo=dt.timezone.utc)
        civil_midnight = (ordinal - utc_ordinal) * _SECONDS_PER_DAY
        for (lower, offset), (upper, _) in pairwise(_crypto_utc_offset_changes(utc_day, timezone)):
            if offset is None:
                continue
            date_start = civil_midnight - offset
            start = max(lower, date_start)
            end = min(upper, date_start + _SECONDS_PER_DAY)
            if start < end:
                if utc_day == dt.date.min and lower == 0 and date_start < 0:
                    raise OverflowError("source date begins outside the UTC datetime range")
                if first is None:
                    first = utc_midnight + dt.timedelta(seconds=start)
                last = utc_midnight + dt.timedelta(seconds=end)
    if first is None:
        raise ValueError(f"source date {day} does not exist in timezone {timezone}")
    return first, last


@dataclass(frozen=True)
class _CryptoClock:
    operation_start: dt.datetime
    label_boundary: dt.datetime
    operation_end: dt.datetime | None = None
    effective_label_day: dt.date | None = None
    label_end: dt.datetime | None = None
    effective_label_end: dt.datetime | None = None


def _crypto_clocks(records, source_days, data_cutoff, source_timezone, last_complete_date):
    clocks = {}
    if source_days is None:
        if source_timezone is not None or last_complete_date is not None:
            raise ValueError("source-day cutoff metadata requires source dates")
        for record in records:
            prediction, label = record.kickoff, record.available_at
            _require_prediction_before_expiry(record, prediction)
            if prediction >= data_cutoff or label >= data_cutoff:
                raise ValueError(f"crypto event {record.event_key!r} must be available before data_cutoff")
            clocks[record.event_key] = _CryptoClock(prediction, label)
        return clocks

    if not isinstance(source_timezone, dt.tzinfo) or type(last_complete_date) is not dt.date:
        raise ValueError("date-only sources require an inclusive last complete source date")
    if type(source_timezone) not in (dt.timezone, ZoneInfo) or (
        isinstance(source_timezone, ZoneInfo) and not source_timezone.key
    ):
        raise ValueError("date-only sources require a usable source timezone with verifiable offset transitions")
    try:
        cutoff_day = data_cutoff.astimezone(source_timezone).date()
    except (TypeError, ValueError, OverflowError, NotImplementedError) as error:
        raise ValueError("date-only sources require a usable source timezone") from error
    if last_complete_date >= cutoff_day:
        raise ValueError("date-only sources require an inclusive last complete source date")
    bounds = {}

    def day_bounds(day):
        if day not in bounds:
            try:
                bounds[day] = _crypto_day_bounds(day, source_timezone)
            except OverflowError as error:
                raise ValueError(f"invalid UTC bounds for source date {day} in timezone {source_timezone}") from error
        return bounds[day]

    for record in records:
        key = record.event_key
        day = source_days[key]
        source, label, observed = day.prediction_date, day.label_date, day.observed_label_at
        if source > label:
            raise ValueError(f"crypto event {key!r} requires consistent source dates")
        effective_label = label
        if observed is not None:
            if observed != record.available_at or observed >= data_cutoff:
                raise ValueError(f"crypto event {key!r} has invalid delayed label availability")
            effective_label = observed.astimezone(source_timezone).date()
            if effective_label < label:
                raise ValueError(f"crypto event {key!r} was observed before its source label date")
        if effective_label > last_complete_date:
            raise ValueError(f"crypto event {key!r} must be available by the last complete source date")
        start, end = day_bounds(source)
        _, label_end = day_bounds(label)
        _, effective_label_end = day_bounds(effective_label)
        if end > data_cutoff or effective_label_end > data_cutoff:
            raise ValueError(f"crypto event {key!r} must be available before data_cutoff")
        _require_prediction_before_expiry(record, start, end)
        # The shared prefix planner compares the latest possible label instant
        # strictly before the cutoff. The civil day ends at an exclusive bound.
        label_boundary = effective_label_end - dt.timedelta.resolution
        if observed is not None:
            label_boundary = max(label_boundary, observed)
        clocks[key] = _CryptoClock(start, label_boundary, end, effective_label, label_end, effective_label_end)
    return clocks


def _crypto_clock_evidence(clocks, records):
    evidence = {}
    for key, clock in clocks.items():
        row = records[key]
        if clock.operation_end is None:
            evidence[key] = {"operation_at": _iso(clock.operation_start), "available_at": _iso(clock.label_boundary)}
        else:
            evidence[key] = {
                "source_day": _iso(row.source_day), "label_day": _iso(row.label_day),
                "effective_label_day": _iso(clock.effective_label_day),
                "prediction_period_start": _iso(clock.operation_start),
                "prediction_period_end": _iso(clock.operation_end),
                "label_period_end": _iso(clock.label_end),
                "effective_label_period_end": _iso(clock.effective_label_end),
                "available_at": None if row.available_at is None else _iso(row.available_at),
            }
    return evidence


def _crypto_cohort(records, holdout_group_ids, selected_group_ids, complete_groups, *,
                   data_cutoff, source_days, source_calendar, source_timezone, last_complete_date):
    clocks = _crypto_clocks(records, source_days, data_cutoff, source_timezone, last_complete_date)
    by_key = {row.event_key: row for row in records}
    aliases = defaultdict(set)
    keys_by_day = defaultdict(list)
    for row in records:
        aliases[row.floor_block].add(row.group_id)
        keys_by_day[row.floor_block].append(row.event_key)
    all_aliases = {alias for group in aliases.values() for alias in group}

    def date_groups(values, field, *, allow_empty=False):
        ids = _items(values, field)
        if (not ids and not allow_empty) or any(not _identifier(group) for group in ids):
            raise ValueError(f"{field} must name nonempty group identifiers")
        members = set(ids)
        if len(members) != len(ids) or members - all_aliases:
            raise ValueError(f"{field} must name unique, known groups")
        days = set()
        for day, group in sorted(aliases.items()):
            selected = group & members
            if selected and group - selected:
                raise ValueError(f"crypto UTC expiry day {day} splits {field} across aliases "
                                 f"{sorted(selected)} and {sorted(group - selected)}")
            if selected:
                days.add(day)
        return days

    holdout = date_groups(holdout_group_ids, "holdout_group_ids")
    complete = set(aliases) if complete_groups is None else date_groups(
        complete_groups, "complete_groups", allow_empty=True,
    )
    if holdout - complete:
        raise ValueError("holdout_group_ids contains incomplete groups")
    selected = None
    if selected_group_ids is not None:
        selected = date_groups(selected_group_ids, "search_group_ids")
        if selected & holdout:
            raise ValueError("selected search groups must be inside the sealed search cohort")
    days = sorted(keys_by_day)
    if set(days[-len(holdout):]) != holdout:
        raise ValueError("holdout groups must form a suffix in UTC expiry day order")
    group_plan = []
    incomplete_source_prefixes = set()
    latest_label_day = None
    for day in days:
        keys = tuple(sorted(keys_by_day[day], key=lambda key: (clocks[key].operation_start, key)))
        group_plan.append(_CohortGroup(
            day, clocks[keys[0]].operation_start, max(clocks[key].label_boundary for key in keys), keys,
        ))
        if source_days is not None:
            if latest_label_day is not None and latest_label_day >= source_days[keys[0]].prediction_date:
                incomplete_source_prefixes.add(day)
            if day in complete:
                label_day = max(clocks[key].effective_label_day for key in keys)
                latest_label_day = label_day if latest_label_day is None else max(latest_label_day, label_day)
    search = group_plan[:-len(holdout)]
    held = group_plan[-len(holdout):]
    first_holdout = min(group.prediction for group in held)
    first_source_day = None if source_days is None else first_holdout.astimezone(source_timezone).date()
    if any(clocks[key].operation_start >= first_holdout for group in search for key in group.keys):
        raise ValueError("holdout groups must form the temporal suffix")
    if any(group.last_label >= first_holdout or (
        first_source_day is not None and any(clocks[key].effective_label_day >= first_source_day for key in group.keys)
    ) for group in search):
        raise ValueError("search labels must be available strictly before the first holdout prediction")

    cohort = CausalCohort(
        search_records=tuple(by_key[key] for group in search for key in group.keys),
        holdout_records=tuple(by_key[key] for group in held for key in group.keys),
        search_group_ids=tuple(group.group_id for group in search),
        holdout_group_ids=tuple(group.group_id for group in held),
        first_holdout_prediction=first_holdout if first_source_day is None else first_source_day,
        source_calendar=source_calendar,
        last_complete_date=last_complete_date,
        source_timezone=source_timezone,
        _groups=tuple(group_plan),
        _complete_groups=frozenset(complete),
    )
    return cohort, None if selected is None else tuple(sorted(selected)), clocks, incomplete_source_prefixes


def _crypto_source_day_folds(folds, skipped, incomplete_prefixes):
    """Apply civil-date ordering to the folds already checked in UTC."""
    if not incomplete_prefixes:
        return folds, skipped
    rejected = tuple(SkippedBlock(fold.block_id, SKIP_PREFIX_INCOMPLETE, fold.cutoff)
                     for fold in folds if fold.block_id in incomplete_prefixes)
    accepted = tuple(fold for fold in folds if fold.block_id not in incomplete_prefixes)
    return accepted, tuple(sorted(skipped + rejected, key=lambda block: block.block_id))


def _verify_crypto_quote_times(lines, records, clocks, source_timezone):
    for line in lines:
        instant = line.decision_at
        if instant is None or line.event_key not in records:
            continue
        record = records[line.event_key]
        _require_prediction_before_expiry(record, instant)
        clock = clocks[line.event_key]
        if clock.operation_end is None:
            within = instant <= clock.operation_start
        else:
            within = (clock.operation_start <= instant < clock.operation_end
                      and instant.astimezone(source_timezone).date() == record.source_day)
        if not within:
            raise ValueError(f"crypto event {line.event_key!r} quote decision time is outside its registered prediction window")


def _international_row(ref, index, match):
    return IntlSourceRow(ref, index, *(match[field] for field in (
        "home", "away", "tournament", "neutral", "home_goals", "away_goals",
    )))


def international_snapshot_records(content: bytes, snapshot: SourceArtifactRef, *,
                                   last_complete_date: dt.date) -> tuple[FixtureRecord, ...]:
    """Create one date-only fixture for each eligible position in a signed archive."""
    from polymarket_alpha_lab.papersim import intl_data

    if not isinstance(snapshot, SourceArtifactRef) or type(last_complete_date) is not dt.date:
        raise ValueError("international records require a registered snapshot and last complete date")
    archives = {snapshot.sha256: content}
    evidence = _verified_sources(archives, archives.keys())
    document = _source_document(archives, evidence, snapshot)
    intl_data._validate_snapshot(document)
    records = []
    for index, match in enumerate(document["matches"]):
        day = dt.date.fromisoformat(match["date"])
        if day <= last_complete_date:
            row = _international_row(snapshot, index, match)
            records.append(FixtureRecord(row.event_key, match["date"], source_day=day, label_day=day,
                                         intl_source_row=row))
    return tuple(records)


@dataclass(frozen=True)
class _InternationalSource:
    ref: SourceArtifactRef
    generated_at: str
    source: str
    dates: tuple[dt.date, ...]
    matches_by_key: Mapping[str, Mapping[str, object]]
    identities: Mapping[str, IntlSourceRow]


@dataclass(frozen=True)
class _InternationalHistory:
    """One immutable part of a sealed training history, with earlier parts only."""

    generated_at: str
    source: str
    rows: tuple[Mapping[str, object], ...]
    earlier: _InternationalHistory | None

    def snapshot(self):
        chunks = []
        chunk = self
        while chunk is not None:
            chunks.append(chunk.rows)
            chunk = chunk.earlier
        return {"generated_at": self.generated_at, "source": self.source,
                "matches": tuple(row for rows in reversed(chunks) for row in rows),
                "teams": {}, "elo_history": ()}


def _international_source(ref, archives, evidence, records, lines):
    from polymarket_alpha_lab.papersim import intl_data

    declared_refs = {row.intl_source_row.snapshot for row in records.values() if row.intl_source_row is not None}
    if ref is not None:
        if not isinstance(ref, SourceArtifactRef):
            raise ValueError("international snapshot must be a registered source artifact reference")
        declared_refs.add(ref)
    if not declared_refs:
        return None
    if len(declared_refs) != 1:
        raise ValueError("international row identities must use one verified snapshot")
    ref = next(iter(declared_refs))
    document = _source_document(archives, evidence, ref)
    intl_data._validate_snapshot(document)
    matches = document["matches"]
    dates = tuple(dt.date.fromisoformat(match["date"]) for match in matches)
    positions = defaultdict(list)
    for index, match in enumerate(matches):
        positions[dates[index], match["home"], match["away"]].append(index)
    by_event = defaultdict(list)
    for line in lines:
        by_event[line.event_key].append(line)
    identities, used, matches_by_key = {}, set(), {}
    for key, record in records.items():
        reference = record.intl_source_row
        if reference is None:
            teams = {(line.home_team, line.away_team) for line in by_event[key] if line.home_team and line.away_team}
            if not teams:
                continue
            (home, away), = teams
            candidates = positions[record.source_day, home, away]
            if len(candidates) != 1:
                raise ValueError("legacy international fixture does not identify a unique original match row")
            reference = _international_row(ref, candidates[0], matches[candidates[0]])
        index = reference.index
        if index >= len(matches) or dates[index] != record.source_day or reference != _international_row(ref, index, matches[index]):
            raise ValueError("international fixture identity, date or final score conflicts with its source row")
        if index in used:
            raise ValueError("multiple fixture events reference the same international source row")
        used.add(index)
        identities[key] = reference
        matches_by_key[key] = MappingProxyType(matches[index])
    return _InternationalSource(ref, document["generated_at"], document["source"], dates,
                                MappingProxyType(matches_by_key), MappingProxyType(identities))


def _intl_formal(line):
    if line.target == "match_winner":
        return line.market_type == "match_winner" and line.line is None and line.class_order == ("home", "draw", "away")
    return (line.target == "over_under" and line.market_type in ("over_under", "totals", "total")
            and line.line == 2.5 and set(line.class_order) == {"over", "under"})


def _verify_international_contracts(lines, source):
    seen = set()
    for line in lines:
        formal = _intl_formal(line)
        if formal:
            identity_key = line.event_key, line.target
            if identity_key in seen:
                raise ValueError("duplicate formal international contract for one source game and target")
            seen.add(identity_key)
        if source is None:
            continue
        identity = source.identities.get(line.event_key)
        if identity is None:
            continue
        margin = identity.home_goals - identity.away_goals
        total = identity.home_goals + identity.away_goals
        if any(provided is not None and provided != actual for provided, actual in (
            (line.competition, identity.tournament), (line.home_team, identity.home), (line.away_team, identity.away),
            (line.actual_total, total), (line.actual_margin, margin),
        )):
            raise ValueError("international contract facts conflict with the original match row")
        if formal:
            realized = _intl_outcome(margin, total >= 3, line.class_order)
            if line.outcome_index != realized:
                raise ValueError("international contract realization conflicts with the original match score")


def _intl_outcome(margin, over, order):
    if len(order) == 3:
        return 0 if margin > 0 else 1 if margin == 0 else 2
    return order.index("over" if over else "under")


@dataclass(frozen=True)
class _InternationalBenchmark:
    causal: tuple[float, ...]
    fixed: tuple[float, ...]
    goal_sum: int
    n_matches: int
    class_counts: tuple[int, int, int]
    team_history: tuple[tuple[int, ...], tuple[int, ...]]
    day: int
    class_order: tuple[str, ...]
    line: float | None

    def fallback(self, spec):
        from polymarket_alpha_lab.papersim import intl_totals

        if self.n_matches and self.goal_sum == 0:
            return "zero_tournament_mean"
        earliest = self.day - spec.params.get("recent_days", intl_totals.RECENT_DAYS)
        if min(len(days) - bisect_left(days, earliest) for days in self.team_history) < intl_totals.MIN_RECENT_MATCHES:
            return "insufficient_recent_matches"
        return None


def _international_folds(folds, source, records, universe):
    if source is None:
        return folds, {}
    from polymarket_alpha_lab.papersim import intl_model, intl_totals

    statistics_by_tournament = defaultdict(lambda: [0, 0, 0, 0, 0])
    team_dates = defaultdict(tuple)
    previous_size = 0
    history = None
    missing_rows = 0
    checked = []
    benchmarks = {}
    for fold in folds:
        fixtures = {}
        selected_benchmarks = {}
        new_rows = fold.train_keys[previous_size:]
        previous_size = len(fold.train_keys)
        authorized_rows = []
        for key in new_rows:
            match = source.matches_by_key.get(key)
            if match is None:
                missing_rows += 1
                continue
            authorized_rows.append(match)
            day = records[key].source_day.toordinal()
            row = statistics_by_tournament[match["tournament"]]
            row[0] += match["home_goals"] + match["away_goals"]
            row[1] += 1
            row[2 + _intl_outcome(match["home_goals"] - match["away_goals"], False, ("home", "draw", "away"))] += 1
            for team in (match["home"], match["away"]):
                team_dates[team] = (*team_dates[team], day)[-intl_totals.MIN_RECENT_MATCHES:]
        if authorized_rows:
            history = _InternationalHistory(source.generated_at, source.source, tuple(authorized_rows), history)
        history_known = (missing_rows == 0 and len(fold.train_keys) == bisect_left(source.dates, fold.source_day))
        by_day_tournament = {}
        for key in fold.test_keys:
            row = source.identities.get(key)
            if row is None:
                continue
            fixtures[key] = InternationalFixture(row.event_key, fold.source_day, row.home, row.away,
                                                 row.tournament, row.neutral)
        for target in ("match_winner", "over_under"):
            for contract in universe.get((fold.block_id, target), ()):
                key = contract.event_key
                if not _intl_formal(contract) or key not in fixtures or not history_known:
                    continue
                fixture = fixtures[key]
                tournament = fixture.tournament
                goals, count, *classes = statistics_by_tournament[tournament]
                if target == "match_winner":
                    fixed = (1.0 / 3.0,) * 3
                    causal = tuple(value / count for value in classes) if count else fixed
                else:
                    if tournament not in by_day_tournament:
                        total = goals / count if count else intl_model.GLOBAL_FALLBACK_TOTAL
                        by_day_tournament[tournament] = intl_totals._over25(total / 2.0, total / 2.0)
                    over = by_day_tournament[tournament]
                    sides = {"over": over, "under": 1.0 - over}
                    causal = tuple(sides[side] for side in contract.class_order)
                    fixed = (0.5, 0.5)
                if contract.benchmark_probabilities is not None and contract.benchmark_probabilities != causal:
                    raise ValueError("international contract cache conflicts with its causal benchmark")
                dates = (team_dates[fixture.home], team_dates[fixture.away])
                benchmark = _InternationalBenchmark(causal, fixed, goals, count, tuple(classes), dates,
                                                     fold.source_day.toordinal(), contract.class_order, contract.line)
                benchmarks[key, target] = benchmark
                selected_benchmarks[source.identities[key].event_key, target] = causal
        checked.append(replace(fold, international_fixtures=MappingProxyType(fixtures),
                               international_benchmarks=MappingProxyType(selected_benchmarks),
                               _international_history=history if missing_rows == 0 else None))
    return tuple(checked), benchmarks


def _provider_snapshots(archives, provider):
    selected = []
    for digest, content in archives.items():
        try:
            document = json.loads(content)
        except (ValueError, UnicodeError):
            continue
        if isinstance(document, dict) and document.get("source") == provider.SOURCE_KEY:
            selected.append((digest, document))
    return selected


@dataclass(frozen=True)
class _NBAGame:
    home: Mapping[str, object]
    away: Mapping[str, object]

    @property
    def margin(self):
        return self.home["pts"] - self.away["pts"]

    @property
    def total(self):
        return self.home["pts"] + self.away["pts"]

    def settled_side(self, target):
        if target == "moneyline":
            return "home" if self.margin > 0 else "away"
        return "over" if self.total > _NBA_LINES["totals"][0] else "under"


@dataclass(frozen=True)
class _NBASource:
    ref: SourceArtifactRef
    snapshot: Mapping[str, object]
    games: Mapping[tuple[str, str, str], _NBAGame]

    def event_key(self, identity):
        return sha256(_canonical_json((self.ref.sha256, *identity))).hexdigest()


def _nba_pairs(snapshot):
    sides = defaultdict(lambda: defaultdict(list))
    team_days = defaultdict(int)
    for team, block in snapshot["teams"].items():
        for row in block["games"]:
            day, opponent = row["date"], row["opponent"]
            home, away = (team, opponent) if row["home"] else (opponent, team)
            sides[day, home, away][row["home"]].append(row)
            team_days[day, team] += 1
    paired, issues = {}, set()
    for (day, home, away), pair in sides.items():
        if team_days[day, home] > 1 or team_days[day, away] > 1:
            issues.add("duplicate_snapshot_team_row")
            continue
        if len(pair) != 2:
            issues.add("unpaired_snapshot_team_row")
            continue
        home_row, away_row = pair[True][0], pair[False][0]
        if (home_row["pts"], home_row["opp_pts"]) != (away_row["opp_pts"], away_row["pts"]):
            issues.add("snapshot_team_rows_disagree_on_final_score")
            continue
        if home_row["pts"] == away_row["pts"]:
            issues.add("snapshot_game_tied_at_final_score")
            continue
        paired[day, home, away] = _NBAGame(MappingProxyType(home_row), MappingProxyType(away_row))
    return paired, sum(team_days.values()), next(iter(sorted(issues)), None)


def nba_snapshot_cohort(content: bytes, snapshot: SourceArtifactRef, *,
                        last_complete_date: dt.date) -> tuple[tuple[FixtureRecord, ...], tuple[FixtureLine, ...]]:
    """Assemble date-only NBA fixtures and settled lines from one registered ESPN snapshot."""
    from polymarket_alpha_lab.papersim import espn_nba

    if not isinstance(snapshot, SourceArtifactRef) or type(last_complete_date) is not dt.date:
        raise ValueError("NBA fixtures require a registered snapshot and last complete date")
    archives = {snapshot.sha256: content}
    document = _source_document(archives, _verified_sources(archives, archives.keys()), snapshot)
    espn_nba._validate_snapshot(document)
    games, _, issue = _nba_pairs(document)
    if issue:
        raise ValueError(f"NBA snapshot does not identify reciprocal completed games: {issue}")
    source = _NBASource(snapshot, document, games)
    records, lines = [], []
    for identity, game in sorted(source.games.items()):
        date_string, home, away = identity
        day = dt.date.fromisoformat(date_string)
        if day > last_complete_date:
            continue
        key = source.event_key(identity)
        records.append(FixtureRecord(key, date_string, source_day=day, label_day=day))
        for target, (line, order) in _NBA_LINES.items():
            lines.append(FixtureLine(
                key, target, target, f"{key}/{target}", order, order.index(game.settled_side(target)), target, line,
                actual_total=game.total, actual_margin=game.margin, home_team=home, away_team=away,
            ))
    return tuple(records), tuple(lines)


def _nba_schedule_events(provider, schedules, season):
    seen = defaultdict(dict)
    year = provider.season_year(season)
    for team, schedule in schedules.items():
        own = schedule.get("team")
        requested = schedule.get("requestedSeason")
        if (not isinstance(own, dict) or own.get("abbreviation") != team
            or not isinstance(requested, dict) or requested.get("year") != year or requested.get("type") != 2):
            raise ValueError("registered NBA schedule does not match its team and season")
        for event in provider._completed_events(schedule, team):
            id_ = event["id"]
            opponents = {event[side]["abbrev"] for side in ("home", "away")}
            if (not id_.isascii() or not id_.isdigit() or len(opponents) != 2 or team not in opponents
                or not opponents <= provider.NBA_TEAMS.keys() or team in seen[id_]):
                raise ValueError("registered NBA schedule has a duplicate or misidentified completed event")
            seen[id_][team] = event
    events = {}
    for id_, rows in seen.items():
        event = next(iter(rows.values()))
        if any(row != event for row in rows.values()):
            raise ValueError("registered NBA schedule rows conflict for the same event")
        teams = {event[side]["abbrev"] for side in ("home", "away")}
        if teams.issubset(schedules) and set(rows) != teams:
            raise ValueError("registered NBA schedules contain a single-sided completed event")
        events[id_] = event
    return events


def _nba_summary_identity(summary, id_, season, provider, event):
    header = summary.get("header")
    competitions = header.get("competitions") if isinstance(header, dict) else None
    if not isinstance(competitions, list) or len(competitions) != 1 or not isinstance(competitions[0], dict):
        raise ValueError("registered NBA summary lacks its event header")
    competition = competitions[0]
    ids = (header.get("id"), competition.get("id"))
    if any(value is not None and str(value) != id_ for value in ids):
        raise ValueError("registered NBA summary does not match its event key")
    header_season = header.get("season")
    if header_season is not None and (not isinstance(header_season, dict) or (
        header_season.get("year"), header_season.get("type")) != (provider.season_year(season), 2)
    ):
        raise ValueError("registered NBA summary does not match its season")
    if event is not None:
        header_teams = competition.get("competitors")
        if not isinstance(header_teams, list) or len(header_teams) != 2 or any(
            not isinstance(row, dict) or not isinstance(row.get("team"), dict) for row in header_teams
        ) or {row.get("homeAway"): row["team"].get("abbreviation") for row in header_teams} != {
            side: event[side]["abbrev"] for side in ("home", "away")
        }:
            raise ValueError("registered NBA summary does not match its event opponents")
        timestamp = competition.get("date")
        if not isinstance(timestamp, str) or provider._et_date(timestamp, id_).isoformat() != event["date"]:
            raise ValueError("registered NBA summary does not match its event date")


def _nba_rebuild(snapshot, manifest, archives, evidence, provider, paired_games):
    details = {
        "available_team_schedules": 0, "required_team_schedules": provider.TEAM_COUNT,
        "available_game_summaries": 0, "required_game_summaries": paired_games,
        "missing_team_schedules": sorted(provider.NBA_TEAMS), "missing_game_summaries": None,
        "teams_equal": None, "injuries_equal": None, "rebuilt_normalized_tables_sha256": None,
    }
    if manifest is None:
        return VERDICT_NOT_ASSESSABLE, "registered_raw_schedules_and_summaries_unavailable", details
    if manifest.season != snapshot["season"] or manifest.schedules.keys() - provider.NBA_TEAMS.keys():
        raise ValueError("NBA source manifest does not match the snapshot season or official teams")
    refs = (manifest.snapshot, *manifest.schedules.values(), *manifest.summaries.values())
    if len({ref.sha256 for ref in refs}) != len(refs):
        raise ValueError("NBA source manifest reuses bytes across distinct source identities")
    schedules = {team: _source_document(archives, evidence, ref) for team, ref in manifest.schedules.items()}
    summaries = {id_: _source_document(archives, evidence, ref) for id_, ref in manifest.summaries.items()}
    missing_teams = sorted(provider.NBA_TEAMS.keys() - schedules.keys())
    details.update(available_team_schedules=len(schedules), missing_team_schedules=missing_teams)
    try:
        events = _nba_schedule_events(provider, schedules, manifest.season)
        for id_, summary in summaries.items():
            if not id_.isascii() or not id_.isdigit() or (not missing_teams and id_ not in events):
                raise ValueError("NBA manifest summary does not identify a completed schedule event")
            _nba_summary_identity(summary, id_, manifest.season, provider, events.get(id_))
        missing_summaries = sorted(events.keys() - summaries.keys())
        details.update(available_game_summaries=len(events.keys() & summaries.keys()),
                       required_game_summaries=max(paired_games, len(events)) if missing_teams else len(events),
                       missing_game_summaries=missing_summaries)
        if missing_teams:
            return VERDICT_NOT_ASSESSABLE, "registered_raw_team_schedules_incomplete", details
        if missing_summaries:
            return VERDICT_NOT_ASSESSABLE, "registered_raw_game_summaries_incomplete", details
        rebuilt = provider.build_snapshot({"season": manifest.season, "generated_at": snapshot["generated_at"],
                                           "schedules": schedules, "summaries": summaries}, season=manifest.season)
    except provider.EspnNbaSchemaError:
        return VERDICT_FAIL, "registered_raw_nba_rebuild_failed", details
    details.update(teams_equal=rebuilt["teams"] == snapshot["teams"],
                   injuries_equal=rebuilt["injuries"] == snapshot["injuries"],
                   rebuilt_normalized_tables_sha256=sha256(_canonical_json({
                       table: rebuilt[table] for table in ("teams", "injuries")
                   })).hexdigest())
    if all(details[key] for key in ("teams_equal", "injuries_equal")):
        return VERDICT_PASS, None, details
    return VERDICT_FAIL, "registered_raw_nba_rebuild_differs", details


def _nba_source_checks(archives, evidence, manifest):
    from polymarket_alpha_lab.papersim import espn_nba

    selected = _provider_snapshots(archives, espn_nba)
    if manifest is not None:
        if not isinstance(manifest, NBASourceManifest):
            raise ValueError("NBA source manifest must use NBASourceManifest")
        if evidence.get(manifest.snapshot.sha256) != manifest.snapshot.byte_length or len(selected) != 1 or (
            selected[0][0] != manifest.snapshot.sha256
        ):
            raise ValueError("NBA source manifest does not identify a unique verified ESPN snapshot")
    if len(selected) != 1:
        return (_unavailable("A", "provider_schema_reload", source=_NBA_REPORT, denominator=len(selected),
                             unit="provider snapshots", reason="missing_unique_registered_provider_snapshot",
                             required=True),), {}, None
    snapshot_sha256, snapshot = selected[0]
    ref = SourceArtifactRef(snapshot_sha256, evidence[snapshot_sha256])
    try:
        espn_nba._validate_snapshot(snapshot)
    except (ValueError, KeyError, TypeError):
        gate = _report_check(GateResult("A", "provider_schema_reload", VERDICT_FAIL, ()), source=_NBA_REPORT,
                             denominator=1, unit="provider snapshots", required=True, reason="invalid_espn_snapshot")
        return (gate,), {"snapshot_sha256": snapshot_sha256, "complete": False}, None
    paired, team_rows, pair_issue = _nba_pairs(snapshot)
    verdict, reason, rebuild = _nba_rebuild(snapshot, manifest, archives, evidence, espn_nba, len(paired))
    # Reload, pairing and reconstruction each attest a different source fact.
    reload = _check("A", "provider_schema_reload", 1, 1, "gte", 1, 1,
                    source=_NBA_REPORT, unit="provider snapshots")
    schedule = _report_check(GateResult("A", "paired_official_schedule", VERDICT_FAIL if pair_issue or not paired else VERDICT_PASS,
                                        (("teams", str(len(snapshot["teams"]))), ("team_rows", str(team_rows)))),
                             source=_NBA_REPORT, denominator=len(paired), unit="paired NBA games", required=True,
                             reason=pair_issue or ("no_complete_snapshot_games" if not paired else None))
    rebuild_gate = _report_check(GateResult("A", "independent_espn_snapshot_rebuild", verdict,
                                            tuple((key, str(value)) for key, value in rebuild.items())),
                                 source=_NBA_REPORT, denominator=len(paired), unit="paired NBA games", required=True,
                                 reason=reason)
    complete = schedule.verdict == VERDICT_PASS and verdict == VERDICT_PASS
    overall = VERDICT_PASS if complete else (
        VERDICT_FAIL if VERDICT_FAIL in (schedule.verdict, verdict) else VERDICT_NOT_ASSESSABLE
    )
    return (reload, schedule, rebuild_gate), {
        "snapshot_sha256": snapshot_sha256, "snapshot_byte_length": evidence[snapshot_sha256],
        "season": snapshot["season"], "official_teams": len(snapshot["teams"]), "raw_team_rows": team_rows,
        "paired_games": len(paired), "paired_source_days": len({key[0] for key in paired}),
        "pairing_issue": pair_issue, "rebuild_verdict": verdict, "rebuild_reason": reason,
        "complete": complete, "verdict": overall, **rebuild,
        "snapshot_normalized_tables_sha256": sha256(_canonical_json({
            table: snapshot[table] for table in ("teams", "injuries")
        })).hexdigest(),
        "schedules": {} if manifest is None else {team: asdict(ref) for team, ref in manifest.schedules.items()},
        "summaries": {} if manifest is None else {id_: asdict(ref) for id_, ref in manifest.summaries.items()},
    }, _NBASource(ref, snapshot, paired)


def _source_checks(family_id, archives, evidence, club_history, club_data, club_goals,
                   data_cutoff, nba_manifest, international):
    if family_id == "nba-pace-efficiency":
        return _nba_source_checks(archives, evidence, nba_manifest)
    if family_id.startswith("intl-elo-"):
        if international is not None:
            check = _check("A", "provider_schema_reload", 1, 1, "gte", 1, 1,
                           source=_INTL_REPORT, unit="provider snapshots")
            return (check,), {}, None
        return (_unavailable("A", "provider_schema_reload", source=_INTL_REPORT, denominator=0,
                             unit="provider snapshots", reason="missing_registered_provider_snapshot", required=True),), {}, None
    if family_id.startswith("club-soccer-") and club_data is not None:
        return _club_source_checks(club_history, club_data, club_goals, data_cutoff), {}, None
    return (), {}, None


def _verify_nba_contracts(lines, records, source):
    if source is None:
        raise ValueError("NBA scoring requires a unique, verified ESPN snapshot")
    used, identities, contracts = {}, {}, []
    evidence = defaultdict(dict)
    canonical_games = {source.event_key(identity): identity for identity in source.games}
    for line in lines:
        key = line.event_key
        if key not in records or not all(map(_identifier, (line.home_team, line.away_team))):
            raise ValueError("NBA contract requires its declared event and both source teams")
        identity = (records[key].source_day.isoformat(), line.home_team, line.away_team)
        game = source.games.get(identity)
        if game is None:
            raise ValueError("NBA fixture does not match a registered paired source game")
        if key in canonical_games and canonical_games[key] != identity:
            raise ValueError("NBA fixture conflicts with the source identity of its event key")
        if identities.setdefault(key, identity) != identity:
            raise ValueError("NBA lines for one event identify different source games")
        if used.setdefault(identity, key) != key:
            raise ValueError("multiple fixture events reference one NBA source game")
        terms = _NBA_LINES.get(line.target)
        if terms is None or line.market_type != line.target or (line.line, line.class_order) != terms:
            raise ValueError("NBA contract has an incompatible market, strike or class order")
        quote_orders = (line.class_order, (line.away_team, line.home_team)) if line.target == "moneyline" else (line.class_order,)
        if line.quote_sides and line.quote_sides not in quote_orders:
            raise ValueError("NBA contract quote sides conflict with its class order")
        if any(value is not None and (not _finite_number(value) or value != actual) for value, actual in (
            (line.actual_total, game.total), (line.actual_margin, game.margin),
        )):
            raise ValueError("NBA fixture score conflicts with the registered source game")
        realized = game.settled_side(line.target)
        if type(line.outcome_index) is not int or line.outcome_index != line.class_order.index(realized):
            raise ValueError("NBA fixture realization conflicts with the registered full-game score")
        if line.target in evidence[key]:
            raise ValueError("duplicate NBA contract target for one source game")
        contracts.append(replace(line, actual_total=game.total, actual_margin=game.margin))
        evidence[key][line.target] = {
            "market_type": line.market_type, "line": line.line, "line_key": line.line_key,
            "observation_id": line.observation_id, "class_order": line.class_order,
            "outcome_index": line.outcome_index, "realized_side": realized,
        }
    if identities.keys() != records.keys():
        raise ValueError("every declared NBA fixture must identify a source game through its registered lines")
    source_rows = {}
    for key, identity in identities.items():
        day, home, away = identity
        game = source.games[identity]
        source_rows[key] = {
            "source_identity": (source.ref.sha256, *identity), "source_event_key": source.event_key(identity),
            "source_day": day, "home": home, "away": away, "home_points": game.home["pts"],
            "away_points": game.away["pts"], "actual_margin": game.margin, "actual_total": game.total,
            "contracts": evidence[key],
        }
    return tuple(contracts), identities, source_rows


def _nba_folds(folds, source, identities):
    if source is None:
        return folds
    from polymarket_alpha_lab.papersim.optimize.families.nba import NbaFamilyFixture

    scoped = []
    for fold in folds:
        fixtures = {}
        for key in fold.test_keys:
            day, home, away = identities[key]
            game = source.games[day, home, away]
            fixtures[key] = NbaFamilyFixture(home, away, game.home["rest_days"], game.away["rest_days"],
                                             game.home["b2b"], game.away["b2b"], day)
        history = _nba_history(source.snapshot, fold, identities, source.games)
        scoped.append(replace(fold, nba_fixtures=MappingProxyType(fixtures), _nba_history=freeze_value(history)))
    return tuple(scoped)


def historical_run_arguments(*, family, manifest, source_artifacts, deadline):
    """Build a fixed historical cohort and callbacks exclusively from registered bytes."""
    family = _historical_family(family)
    protocol = historical_protocol(family)
    if (not isinstance(manifest, Mapping) or set(manifest) != {"schema_version", "protocol", "sources"}
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or manifest["protocol"] != protocol or not callable(deadline)):
        raise ValueError("historical manifest does not match the registered protocol")
    sources = _items(manifest.get("sources"), "historical sources")
    inventories, absent, seen = defaultdict(dict), [], set()
    for source in sources:
        if not isinstance(source, Mapping) or not all(_identifier(source.get(field)) for field in ("role", "key")):
            raise ValueError("historical source requires a registered role and key")
        role, key = source["role"], source["key"]
        if (role, key) in seen:
            raise ValueError("historical source contains a duplicate role and key")
        seen.add((role, key))
        if source.get("required") is False and _identifier(source.get("absent_reason")):
            if source.get("sha256") is not None or source.get("byte_length") is not None:
                raise ValueError("absent historical source cannot also declare bytes")
            absent.append({"role": role, "key": key, "reason": source["absent_reason"]})
            continue
        inventories[role][key] = SourceArtifactRef(source.get("sha256"), source.get("byte_length"))
    refs = {ref for by_key in inventories.values() for ref in by_key.values()}
    lengths = {ref.sha256: ref.byte_length for ref in refs}
    if (not isinstance(source_artifacts, Mapping) or len(refs) != len(lengths)
            or source_artifacts.keys() != lengths.keys()
            or _verified_sources(source_artifacts, lengths) != lengths):
        raise ValueError("historical source bytes do not match the role inventory")

    family_id = family.family_id
    common = {"family": family, "targets": protocol["targets"], "schedule_id": protocol["schedule_id"]}
    if family_id == "club-soccer-recalib":
        arguments, predict_fold, summary = _historical_club(family, protocol, inventories, source_artifacts, deadline)
    elif family_id == "nba-pace-efficiency":
        arguments, predict_fold, summary = _historical_nba(family, protocol, inventories, source_artifacts, deadline)
    else:
        arguments, predict_fold, summary = _historical_intl(family, protocol, inventories, source_artifacts, deadline)
    if protocol["last_complete_date"] is not None:
        common.update(source_calendar=protocol["source_calendar"], source_timezone=ZoneInfo(protocol["source_timezone"]),
                      last_complete_date=dt.date.fromisoformat(protocol["last_complete_date"]))

    def evaluate_holdout(spec, target, folds):
        events, refusals = [], []
        for fold in folds:
            batch = predict_fold(spec, fold, (target,))[target]
            events.extend(batch.events)
            refusals.extend(batch.refusals)
        return HoldoutEvaluation(tuple(events), refusals=tuple(refusals))

    return {**common, **arguments, "evaluate": predict_fold, "evaluate_holdout": evaluate_holdout,
            "dataset_summary": {**summary, "historical_protocol": protocol, "absent_sources": absent}}


def _historical_contracts(key, home, away, competition, margin, total, terms):
    rows = []
    for target, lines in terms.items():
        for line in lines:
            strike, order = line["line"], tuple(line["class_order"])
            line_key = "1x2" if strike is None else f"over-{strike:g}"
            outcome = _intl_outcome(margin, strike is not None and total > strike, order)
            rows.append(FixtureLine(key, target, line_key, f"{key}/{line_key}", order, outcome,
                                    line["market_type"], strike, dispersion_class="home" if strike is None else "over",
                                    actual_total=total, actual_margin=margin, competition=competition,
                                    home_team=home, away_team=away))
    return rows


def _historical_evaluator(lines, prepare, deadline):
    indexed = defaultdict(list)
    for line in lines:
        indexed[line.event_key].append(line)

    def evaluate(spec, fold, targets):
        if deadline():
            raise _DeadlineExpired
        predict = prepare(spec, fold)
        events, refusals = {target: [] for target in targets}, {target: [] for target in targets}
        for event in fold.test_keys:
            if deadline():
                raise _DeadlineExpired
            scoped = tuple(line for line in indexed[event] if line.target in events)
            if scoped:
                predictions, reason = predict(event, scoped)
                if reason is not None:
                    for line in scoped:
                        refusals[line.target].append(Refusal(event, line.line_key, reason))
                else:
                    for line, prediction in zip(scoped, predictions, strict=True):
                        events[line.target].append(prediction)
        return {target: HoldoutEvaluation(tuple(events[target]), refusals=tuple(refusals[target])) for target in targets}

    return evaluate


def _historical_prediction(spec, line, probabilities, variants=(), **observables):
    digest = spec.spec_sha256()
    envelope = tuple(VariantPrediction(identifier, digest, line.event_key, line.target, line.line_key,
                                       line.class_order, vector)
                     for identifier, vector in zip(variant_ids(spec), variants, strict=True))
    return Prediction(line.event_key, line.line_key, probabilities, line.class_order, variants=envelope,
                      **observables)


def _historical_day_groups(records, protocol):
    first_holdout = protocol["cohort"]["first_holdout_date"]
    days = sorted({row.group_id for row in records})
    holdout = tuple(day for day in days if day >= first_holdout)
    if not holdout:
        raise ValueError("historical source has no registered holdout dates")
    return holdout


def _historical_intl(family, protocol, inventories, archives, deadline):
    from polymarket_alpha_lab.papersim import intl_model
    from polymarket_alpha_lab.papersim.optimize.families.intl import IntlFamilyFixture, IntlHistory

    snapshots = inventories.get("intl_snapshot", {})
    if inventories.keys() != {"intl_snapshot"} or snapshots.keys() != {"snapshot"}:
        raise ValueError("international history requires its registered snapshot")
    ref = snapshots["snapshot"]
    records = international_snapshot_records(archives[ref.sha256], ref,
                                             last_complete_date=dt.date.fromisoformat(protocol["last_complete_date"]))
    lines = tuple(line for record in records for source in (record.intl_source_row,) for line in _historical_contracts(
        record.event_key, source.home, source.away, source.tournament, source.home_goals - source.away_goals,
        source.home_goals + source.away_goals, protocol["lines"],
    ))
    holdout = _historical_day_groups(records, protocol)
    search_days = sorted({row.group_id for row in records if row.group_id < holdout[0]})
    selected = search_days[2:][-protocol["cohort"]["search_dates"]:]
    if not selected:
        raise ValueError("international history has no eligible pre-holdout search date")

    def prepare(spec, fold):
        fitted = family.fit(IntlHistory(international_history_at(fold=fold)), spec, fold.cutoff)

        def predict(key, scoped):
            game = fold.international_fixtures[key]
            fixture = IntlFamilyFixture(intl_model.IntlFixture(game.home, game.away, game.date.isoformat(), game.neutral),
                                        game.tournament)
            result = international_replay_prediction(family, fitted, fixture)
            if result is None:
                return (), "international_replay_prediction_unavailable"
            (line,) = scoped
            if line.target == "match_winner":
                variants = tuple((v.outputs.home_win, v.outputs.draw, v.outputs.away_win) for v in result.variants)
                forecast = _historical_prediction(spec, line, (result.p_home, result.p_draw, result.p_away),
                                                  variants, expected_total=result.lambda_home + result.lambda_away,
                                                  expected_margin=result.lambda_home - result.lambda_away,
                                                  home_rating=result.elo_home, away_rating=result.elo_away)
            else:
                forecast = _historical_prediction(spec, line, (result.p_under25, result.p_over25),
                                                  fallback_reason=result.fallback_reason,
                                                  expected_total=result.lambda_home + result.lambda_away)
            return (forecast,), None

        return predict

    arguments = {"records": records, "fixture_lines": lines, "holdout_group_ids": holdout,
                 "search_group_ids": tuple(selected), "international_snapshot": ref,
                 "build_candidate_spec": family.spec_with}
    return arguments, _historical_evaluator(lines, prepare, deadline), {"snapshot_sha256": ref.sha256}


def _historical_nba(family, protocol, inventories, archives, deadline):
    from polymarket_alpha_lab.papersim import espn_nba, nba_model
    from polymarket_alpha_lab.papersim.optimize.families.nba import NbaFamilyHistory, _envelope_mappings

    snapshots = inventories.get("nba_snapshot", {})
    if inventories.keys() - {"nba_snapshot", "team_schedule", "event_summary"} or snapshots.keys() != {"snapshot"}:
        raise ValueError("NBA history requires its uniquely registered snapshot")
    ref = snapshots["snapshot"]
    raw = archives[ref.sha256]
    snapshot = json.loads(raw)
    records, lines = nba_snapshot_cohort(raw, ref, last_complete_date=dt.date.fromisoformat(protocol["last_complete_date"]))
    holdout = _historical_day_groups(records, protocol)

    def raw_refs(role, prefix, suffix):
        refs = {}
        for key, ref in inventories.get(role, {}).items():
            name = key.removeprefix(f"{role}/{prefix}").removesuffix(suffix)
            if not _identifier(name) or key != f"{role}/{prefix}{name}{suffix}" or "/" in name:
                raise ValueError("NBA raw source key does not identify a team schedule or event summary")
            refs[name] = ref
        return refs

    season = snapshot["season"]
    schedules = raw_refs("team_schedule", "schedule_", f"_{espn_nba.season_year(season)}.json")
    summaries = raw_refs("event_summary", "summary_", ".json")
    manifest = NBASourceManifest(ref, season, schedules, summaries) if schedules or summaries else None
    baseline = family.baseline_spec()

    def build(params):
        return replace(baseline, params=params, legacy_model_version=None, envelope=_envelope_mappings(params["half_life"]))

    def prepare(spec, fold):
        history = nba_history_at(fold=fold)
        fitted = family.fit(NbaFamilyHistory(history), spec, fold.cutoff)
        ratings = {}

        def net_rating(team, before):
            if team not in ratings:
                result = nba_model.team_ratings(history, team, before_date=before, half_life=spec.params["half_life"])
                ratings[team] = None if result is None else result.ortg - result.drtg
            return ratings[team]

        def predict(key, scoped):
            fixture = fold.nba_fixtures[key]
            result = family.predict(fitted, fixture)
            if result is None:
                actual = nba_model.NbaFixture(fixture.home, fixture.away, fixture.rest_home, fixture.rest_away,
                                              fixture.b2b_home, fixture.b2b_away)
                return (), nba_model.refusal_reason(history, actual, before_date=fixture.before_date)
            forecasts = []
            for line in scoped:
                if line.target == "moneyline":
                    probabilities = (result.p_away, result.p_home)
                    variants = tuple((variant.p_away, variant.p_home) for variant in result.variants)
                else:
                    quote = result.total_line_prob(line.line)
                    probabilities = (quote.p_under, quote.p_over)
                    variants = tuple((1.0 - p, p) for p in quote.over_variants)
                forecasts.append(_historical_prediction(spec, line, probabilities, variants,
                                                        expected_total=result.exp_total, expected_margin=result.exp_margin,
                                                        home_rating=net_rating(fixture.home, fixture.before_date),
                                                        away_rating=net_rating(fixture.away, fixture.before_date)))
            return forecasts, None

        return predict

    arguments = {"records": records, "fixture_lines": lines, "holdout_group_ids": holdout,
                 "nba_source_manifest": manifest, "build_candidate_spec": build}
    return arguments, _historical_evaluator(lines, prepare, deadline), {"snapshot_sha256": ref.sha256}


def _historical_club(family, protocol, inventories, archives, deadline):
    from polymarket_alpha_lab.papersim import recalib
    from polymarket_alpha_lab.papersim.optimize import dataset
    from polymarket_alpha_lab.papersim.optimize.families.soccer_recalib import envelope_specs, spec_for_params

    club_refs = inventories.get("club_archive", {})
    files = {key: archives[ref.sha256] for key, ref in club_refs.items()}
    if (inventories.keys() != {"club_archive"}
            or files.keys() - set((*dataset.archive_read_paths(), *dataset.archive_fixture_paths(files)))):
        raise ValueError("club source lies outside the official archive inventory")
    pointer = json.loads(files[dataset.ARCHIVE_POINTER_PATH])
    if pointer.get("pointed_at_utc") != protocol["data_cutoff"]:
        raise ValueError("club archive pointer does not match the registered cutoff")
    audit = dataset.history_from_archive(files)
    records, (official_groups, rounds) = _club_records(audit, dt.datetime.fromisoformat(protocol["data_cutoff"]))
    complete = {group for group in official_groups if rounds[group][4] and len(official_groups[group]) == rounds[group][5]}
    latest_incomplete = max((rounds[group][3] for group in official_groups.keys() - complete), default=None)
    boundaries = sorted({rounds[group][3] for group in official_groups})
    expected = dt.datetime.fromisoformat(protocol["cohort"]["first_holdout_boundary"])
    for boundary in boundaries:
        if latest_incomplete is not None and boundary <= latest_incomplete:
            continue
        holdout = tuple(group for group in official_groups if rounds[group][3] >= boundary)
        held = set(holdout)
        if held.issubset(complete) and all(row.available_at < boundary for row in records if row.group_id not in held):
            break
    else:
        raise ValueError("club archive has no complete causal official-round suffix")
    if boundary != expected:
        raise ValueError("club official-round suffix does not match the registered boundary")

    lines, references, fixtures, joins = [], {}, {}, []
    schedule = {(row.league, row.season, row.fixture_id): row for row in audit.schedule}
    for group in official_groups.values():
        for record, match in group:
            lines.extend(_historical_contracts(record.event_key, match.home_team, match.away_team, match.league,
                                               match.home_goals - match.away_goals, match.home_goals + match.away_goals,
                                               protocol["lines"]))
            fixtures[record.event_key] = recalib.Fixture(match.league, match.season, match.matchweek, record.kickoff,
                                                         match.home_team, match.away_team)
            official = schedule[match.league, match.season, match.schedule_match_id]
            if (match.source_path == dataset.ARCHIVE_BIG5_PATH and match.source_kickoff.date() < dt.date(2026, 11, 1)
                    and match.source_match_id != official.fixture_id and official.score is not None):
                key = f"tools/fotmob_cache/fixtures/leagues_{dataset.LEAGUES[match.league][0]}_{match.season}.json"
                home, away = (int(value) for value in official.score.split("-"))
                references[record.event_key] = ClubGoalReference(club_refs[key].sha256, home, away)
                joins.append({"event_key": record.event_key, "official_fixture_id": official.fixture_id,
                              "source_fixture_id": match.source_match_id, "snapshot_sha256": club_refs[key].sha256})

    def prepare(spec, fold):
        history = club_model_history(audit, fold)
        fitted = family.fit(history, spec, fold.cutoff)
        variants, fits = [], {spec.spec_sha256(): fitted}
        for variant in envelope_specs(spec):
            if deadline():
                raise _DeadlineExpired
            digest = variant.spec_sha256()
            if digest not in fits:
                fits[digest] = family.fit(history, variant, fold.cutoff)
            variants.append(fits[digest])

        def probabilities(result, line):
            if line.target == "match_winner":
                vector = (result.home_win, result.draw, result.away_win)
                if any(not math.isfinite(value) or value <= 0.0 for value in vector):
                    raise ValueError("club 1X2 median has invalid probability mass")
                total = math.fsum(vector)
                return tuple(value / total for value in vector)
            over = result.over_probability(line.line)
            return 1.0 - over, over

        def predict(key, scoped):
            fixture = fixtures[key]
            center = family.predict(fitted, fixture)
            if center is None:
                return (), "club_recalibration_model_refusal"
            envelope = []
            for variant in variants:
                if deadline():
                    raise _DeadlineExpired
                estimate = family.predict(variant, fixture)
                if estimate is None:
                    return (), "club_recalibration_envelope_member_refusal"
                envelope.append(estimate)
            return tuple(_historical_prediction(
                spec, line, probabilities(center, line), tuple(probabilities(variant, line) for variant in envelope),
                expected_total=center.lambda_home + center.lambda_away, expected_margin=center.lambda_home - center.lambda_away,
            ) for line in scoped), None

        return predict

    arguments = {"records": records, "fixture_lines": tuple(lines), "holdout_group_ids": holdout,
                 "club_history": audit, "club_goal_references": references, "build_candidate_spec": spec_for_params}
    summary = {"archive_sources": audit.sources, "archive_notes": audit.notes, "archive_exclusions": audit.exclusions,
               "independent_score_joins": joins, "independent_score_joins_possible": len(records),
               "independent_score_limit": "FotMob-sourced results cannot independently attest their FotMob fixture scores"}
    return arguments, _historical_evaluator(lines, prepare, deadline), summary


def _club_source_checks(audit, club_data, goal_references, data_cutoff):
    from polymarket_alpha_lab.papersim.optimize import dataset

    report = "round39/eredivisie_validation.md"
    official_groups, _ = club_data
    settled = sum(len(rows) for rows in official_groups.values())
    eligible = sum(
        row.finished and not row.cancelled and not row.awarded
        and _aware(row.kickoff, "official fixture kickoff") + dt.timedelta(hours=3) < data_cutoff
        for row in audit.schedule
    )
    rows_by_season = defaultdict(list)
    for official in official_groups.values():
        for _, match in official:
            rows_by_season[match.league, match.season].append(match)
    rhos, complete_matches = [], []
    for (league, season), matches in sorted(rows_by_season.items()):
        if not dataset._complete_season(audit, league, season, tuple(matches)):
            continue
        complete_matches.extend(matches)
        teams = defaultdict(lambda: [0.0, 0])
        for match in matches:
            teams[match.home_team][0] += match.home_xg
            teams[match.home_team][1] += match.home_goals
            teams[match.away_team][0] += match.away_xg
            teams[match.away_team][1] += match.away_goals
        rho = _correlation(
            [xg for xg, _ in teams.values()], [goals for _, goals in teams.values()]
        )
        if rho is not None:
            rhos.append(rho)
    checks = (
        _check("A", "finished_official_fixture_xg_coverage", settled / eligible if eligible else None,
               0.95, "gte", eligible, 1, source=report, unit="finished official fixtures"),
        _check("C", "team_xg_vs_goals_correlation", min(rhos) if rhos else None,
               math.nextafter(0.7, 1.0), "gte", len(rhos), 1, source=report,
               unit="completed source league-seasons"),
    )
    return (*checks, *_club_goal_checks(complete_matches, goal_references, report))


def _club_goal_checks(fixtures, references, report):
    if not references or not fixtures:
        return (_unavailable("B", "league_goal_fit", source=report, denominator=len(fixtures),
                             unit="complete-season source fixtures",
                             reason="missing_published_league_goal_fit_source", required=True),)
    matches = [(match, references[match.match_id]) for match in fixtures if match.match_id in references]
    coverage = len(matches) / len(fixtures)
    checks = [_check("B", "independent_score_join_coverage", coverage, 0.99, "gte", len(fixtures), 1,
                     source=report, unit="complete-season source fixtures")]
    for side in ("home", "away"):
        official = sum(getattr(reference, f"{side}_goals") for _, reference in matches)
        actual = sum(getattr(match, f"{side}_goals") for match, _ in matches)
        predicted = math.fsum(getattr(match, f"{side}_xg") for match, _ in matches)
        checks.extend((
            _check("B", f"{side}_actual_score_integrity",
                   abs(actual - official) / official if official else None,
                   math.nextafter(0.01, 0.0), "lte", len(matches), 1, source=report,
                   unit="independently matched source fixtures"),
            _check("B", f"{side}_league_goal_fit",
                   abs(predicted - official) / official if official else None, 0.08, "lte",
                   len(matches), 1, source=report, unit="independently matched source fixtures"),
        ))
    return tuple(checks)


def _sealed_input_digest(cohort, records, universe, targets, *, nba_games, expiry_groups, crypto_chronology=None):
    digest = sha256()
    for timed in (*cohort.search_records, *cohort.holdout_records):
        row = asdict(records[timed.event_key])
        if row["expiry_at"] is None:
            del row["expiry_at"]
        for field in ("kickoff", "available_at", "source_day", "label_day", "prediction_at", "expiry_at"):
            if row.get(field) is not None:
                row[field] = _iso(row[field])
        digest.update(_canonical_json(row))
        digest.update(b"\n")
    for group in (*cohort.search_group_ids, *cohort.holdout_group_ids):
        for target in targets:
            for contract in universe.get((group, target), ()):
                row = asdict(contract)
                if row["decision_at"] is not None:
                    row["decision_at"] = _iso(row["decision_at"])
                digest.update(_canonical_json(row))
                digest.update(b"\n")
    if nba_games:
        digest.update(_canonical_json({"nba_source_games": nba_games}) + b"\n")
    if expiry_groups is not None:
        digest.update(_canonical_json({"crypto_expiry_groups": asdict(expiry_groups)}) + b"\n")
    if crypto_chronology is not None:
        digest.update(_canonical_json({
            **crypto_chronology,
            "holdout_group_ids": cohort.holdout_group_ids,
            "source_calendar": cohort.source_calendar,
            "source_timezone": None if cohort.source_timezone is None else str(cohort.source_timezone),
            "last_complete_date": None if cohort.last_complete_date is None else _iso(cohort.last_complete_date),
        }) + b"\n")
    return digest.hexdigest()


def _search_forecast_artifact(search, verified_batches):
    if search is None:
        return []
    rows = []
    for trial in search.plan:
        for target in trial.targets:
            batch = search.batches.get((trial.fold.block_id, trial.spec_sha256, target))
            if batch is None:
                continue
            verified = verified_batches[trial.fold.block_id, trial.spec_sha256, target]
            rows.append({
                "trial_index": trial.trial_index, "target": target,
                "predictions": _prediction_payload(verified, "search", None, trial.spec_sha256),
                "refusals": [(key.event_key, key.line_key, batch.refusals[key]) for key in sorted(batch.refusals)],
            })
    return rows


def _comparison_payload(comparison):
    return {
        "status": comparison.status,
        "winner_spec_sha256": comparison.winner.spec_sha256(),
        "mask_sha256": comparison.mask_sha256,
        "eligible_lines": comparison.eligible_lines,
        "eligible_matches": comparison.eligible_matches,
        "common_lines": comparison.common_lines,
        "common_matches": comparison.common_matches,
        "nonempty_blocks": comparison.nonempty_blocks,
        "blocks": [asdict(block) for block in comparison.blocks],
        "selection": _one_se_payload(comparison.selection),
    }


def _one_se_payload(selection):
    if selection is None:
        return None
    return {
        "status": selection.status,
        "best_score": repr(selection.best_score),
        "standard_error": repr(selection.standard_error),
        "threshold": repr(selection.threshold),
        "needs_preseason_reestimate": selection.needs_preseason_reestimate,
    }


def _prediction_payload(batch, name, lineage, digest):
    rows = []
    for key, forecast in sorted(batch.forecast.predictions.items(), key=lambda item: (
        item[0].event_key, item[0].line_key
    )):
        raw = batch.predictions[key]
        rows.append({
            **asdict(forecast.row), "lineage": lineage, "model": name, "spec_sha256": digest,
            "class_order": list(key.class_order), "observation_id": key.observation_id,
            "variants": [asdict(variant) for variant in raw.variants],
            "fallback_reason": raw.fallback_reason,
            "predicted_at": None if raw.predicted_at is None else _iso(raw.predicted_at),
            "expected_total": raw.expected_total, "expected_margin": raw.expected_margin,
            "home_rating": raw.home_rating, "away_rating": raw.away_rating,
        })
    return rows


def _batch_refusals(batch):
    return _refusal_payload(batch.forecast.refusals)


def _refusal_payload(refusals):
    return [{"event_key": key.event_key, "line_key": key.line_key, "reason": reason}
            for key, reason in sorted(refusals.items(), key=lambda item: (
                item[0].event_key, item[0].line_key,
            ))]


def _holdout_comparison(
    *, family, target, lineage, candidate, incumbent, left, right,
    universe, records, markets, clusters, source_evidence, source_refs, source_checks,
    international, intl_benchmarks,
):
    paired = tuple(sorted(left.forecast.predictions.keys() & right.forecast.predictions.keys(),
                         key=lambda key: (key.event_key, key.line_key)))
    coverage = _coverage(left, right, paired, records)
    contracts = {contract.key: contract for contract in universe}
    market, market_payload = _market_pair(
        paired, contracts, records, markets, left, right, clusters, source_refs,
    )
    left_spread, right_spread = (_spread_median(batch, paired) for batch in (left, right))
    spread_status = envelope_status(left_spread, right_spread)
    if spread_status == "not_assessable" and (candidate.envelope or incumbent.envelope):
        spread_status = "unverified"
    if left.reported_market is not None or right.reported_market is not None:
        for reported in (left.reported_market, right.reported_market):
            if reported is not None and (
                tuple(sorted(reported, key=lambda row: (row.event_key, row.line_key))) != market
            ):
                raise ValueError("copied market rows disagree with registered observations")
    source_valid = 1 if source_refs and source_refs == source_evidence.keys() else None
    left_rows = tuple(left.forecast.predictions[key].row for key in paired)
    right_rows = tuple(right.forecast.predictions[key].row for key in paired)
    left_gates = _validation_gates(family, target, candidate, paired, contracts, left, left_rows, market,
                                   source_integrity=source_valid, source_count=len(source_evidence),
                                   source_checks=source_checks, international=international,
                                   intl_benchmarks=intl_benchmarks)
    right_gates = _validation_gates(family, target, incumbent, paired, contracts, right, right_rows, market,
                                    source_integrity=source_valid, source_count=len(source_evidence),
                                    source_checks=source_checks, international=international,
                                    intl_benchmarks=intl_benchmarks)
    left_card = build_scorecard(
        candidate, left_rows, incumbent_events=right_rows, market_events=market or None,
        variant_spread_median=left_spread, refusals=len(left.forecast.refusals),
        gates=left_gates, sign_flip_seed=SIGN_FLIP_SEED, sign_flip_reps=SIGN_FLIP_REPS,
    )
    right_card = build_scorecard(
        incumbent, right_rows, market_events=market or None,
        variant_spread_median=right_spread, refusals=len(right.forecast.refusals),
        gates=right_gates, sign_flip_seed=SIGN_FLIP_SEED, sign_flip_reps=SIGN_FLIP_REPS,
    )
    if market:
        market_keys = {(row.event_key, row.line_key) for row in market}
        matched = [key for key in paired if (key.event_key, key.line_key) in market_keys]
        market_payload.update(
            lines=len(matched), games=_block_counts(matched, records)[0],
            mask_sha256=_mask_digest(matched),
            absolute_losses={
                "candidate": repr(build_scorecard(candidate, [left.forecast.predictions[key].row for key in matched]).log_loss),
                "incumbent": repr(build_scorecard(incumbent, [right.forecast.predictions[key].row for key in matched]).log_loss),
                "market": repr(build_scorecard(incumbent, market).log_loss),
            },
        )
    else:
        market_payload.update(lines=0, games=0, mask_sha256=_mask_digest(()), absolute_losses=None)
    ci = left_card.paired_vs_incumbent
    cal_reg = None not in (left_card.calibration_ece, right_card.calibration_ece) and (
        left_card.calibration_ece > right_card.calibration_ece
    )
    gate_reg = {(gate.gate, gate.check) for gate in right_gates if gate.verdict == VERDICT_PASS} - {
        (gate.gate, gate.check) for gate in left_gates if gate.verdict == VERDICT_PASS
    }
    unverified = tuple(
        f"{label}/{gate.gate}/{gate.check}"
        for label, gates in (("candidate", left_gates), ("incumbent", right_gates))
        for gate in gates
        if ("required", "true") in gate.numbers and (
            gate.verdict == VERDICT_NOT_ASSESSABLE
            or gate.verdict == VERDICT_FAIL and family == "nba" and gate in source_checks
        )
    )
    stat = LineageStat(
        lineage, coverage["paired_games"], coverage["paired_blocks"],
        None if ci is None else ci.ci95[1], None if ci is None else ci.p_value,
        cal_reg, bool(gate_reg), spread_status,
        evidence_unverified=bool(unverified),
        has_model_refusals=any(batch.forecast.refusals.keys() - batch.structural_refusals for batch in (left, right)),
    )
    payload = {
        "coverage": coverage, "market": market_payload,
        "dispersion": {
            "unit": "probability", "aggregation": "median of per-line variant ranges",
            "components": sorted({
                contracts[key].dispersion_class or key.class_order[1]
                for key in paired
                if len(key.class_order) == 2 or contracts[key].dispersion_class is not None
            }),
            "paired_lines": len(paired),
            "candidate_lines_with_all_variants": sum(left.spread[key] is not None for key in paired),
            "incumbent_lines_with_all_variants": sum(right.spread[key] is not None for key in paired),
        },
        "unverified_required_checks": unverified,
        "candidate_refusals": _batch_refusals(left), "incumbent_refusals": _batch_refusals(right),
    }
    return stat, left_card.canonical_dict(), right_card.canonical_dict(), payload


def _active_diagnostic(family, target, lineage, spec, fold_batches, contracts,
                       source_evidence, source_refs, source_checks, international, intl_benchmarks):
    if not fold_batches:
        return None, []
    predictions, raw, spread, refusals, forecast_rows = {}, {}, {}, {}, []
    for fold, batch in fold_batches:
        predictions.update(batch.forecast.predictions)
        raw.update(batch.predictions)
        spread.update(batch.spread)
        refusals.update(batch.forecast.refusals)
        forecast_rows.extend(dict(row, fold=fold) for row in _prediction_payload(
            batch, "incumbent", lineage, spec.spec_sha256()
        ))
    sealed = tuple(sorted((*predictions, *refusals), key=lambda key: (key.event_key, key.line_key)))
    keys = tuple(key for key in sealed if key in predictions)
    batch = _VerifiedBatch(ForecastBatch(sealed, predictions, refusals), raw, spread, None, None)
    events = tuple(predictions[key].row for key in keys)
    gates = _validation_gates(
        family, target, spec, keys, contracts, batch, events, (),
        source_integrity=1 if source_refs and source_refs == source_evidence.keys() else None,
        source_count=len(source_evidence), source_checks=source_checks, international=international,
        intl_benchmarks=intl_benchmarks, scope="search_out_of_fold_descriptive",
    )
    card = build_scorecard(spec, events, variant_spread_median=_spread_median(batch, keys),
                           refusals=len(refusals), gates=gates).canonical_dict()
    return {
        **card, "scope": "search_out_of_fold_descriptive", "eligible_lines": len(sealed),
        "forecast_mask_sha256": _mask_digest(keys), "refusal_rows": _batch_refusals(batch),
        "holdout_comparison": "unavailable_without_opened_qualifying_holdout",
    }, forecast_rows


def _closed_stat(lineage, status):
    return LineageStat(lineage, 0, 0, None, None, envelope_status="not_assessable",
                       attempted=False, status=status)


def _judgment_reasons(decision):
    return tuple(reason for failed, reason in (
        (not decision.ci_passes, "holdout_ci_upper_nonnegative"),
        (decision.holm_rejected is False, "holdout_holm_not_rejected"),
        (decision.calibration_regression, "calibration_regression"),
        (decision.gate_regression, "validation_gate_regression"),
    ) if failed)


def _publication_block(policy, lineage, spec, verified, baseline):
    if lineage not in verified:
        return "registry_lineage_uninitialized"
    roles = policy.registry[lineage].roles
    digest = spec.spec_sha256()
    if digest == baseline or digest in {roles.active, roles.baseline, *roles.retired, *roles.candidates}:
        return "spec_already_has_registry_role"
    if len(roles.candidates) >= MAX_CANDIDATES:
        return "registry_candidate_capacity"
    if policy.global_frozen or lineage in policy.frozen_lineages:
        return "registry_frozen"
    return None


def _trial_payload(trial):
    row = asdict(trial)
    if "mean_loss" in row and row["mean_loss"] is not None:
        row["mean_loss"] = repr(row["mean_loss"])
    return row


def _augment_refusals(batch, universe, closed_rows):
    return replace(batch, forecast=ForecastBatch(tuple(row.key for row in universe), batch.forecast.predictions,
                                                {**batch.forecast.refusals, **closed_rows}),
                   structural_refusals=frozenset(closed_rows))


def execute_optimization_run(
    *,
    family,
    records: Sequence[FixtureRecord],
    fixture_lines: Sequence[FixtureLine],
    holdout_group_ids: Iterable[str],
    evaluate: Callable[[ModelSpec, ScopedFold, tuple[str, ...]], Mapping[str, HoldoutEvaluation]],
    evaluate_holdout: Callable[[ModelSpec, str, tuple[ScopedFold, ...]], HoldoutEvaluation],
    data_cutoff: dt.datetime,
    random_seed: int,
    code_commit: str,
    policy_context: PolicyContext,
    deadline: Callable[[], bool],
    targets: Iterable[str] | None = None,
    search_group_ids: Iterable[str] | None = None,
    schedule_id: str = _DEFAULT_SCHEDULE,
    source_calendar: str | None = None,
    source_timezone: dt.tzinfo | None = None,
    last_complete_date: dt.date | None = None,
    club_history: HistoryAudit | None = None,
    club_goal_references: Mapping[str, ClubGoalReference] | None = None,
    market_observations: Iterable[MarketObservation] = (),
    source_artifacts: Mapping[str, bytes] | None = None,
    international_snapshot: SourceArtifactRef | None = None,
    nba_source_manifest: NBASourceManifest | None = None,
    crypto_expiry_groups: SourceArtifactRef | None = None,
    incumbent_spec: ModelSpec | Mapping[str, ModelSpec] | None = None,
    build_candidate_spec: Callable[[Mapping[str, object]], ModelSpec] | Mapping[str, Callable] | None = None,
    model_contexts: Mapping[str, str] | None = None,
    max_trials: int = MAX_TRIALS,
    fold_kind: str | None = None,
    floor_class: str | None = None,
    dataset_summary: Mapping[str, object] | None = None,
    snapshot_refs: Iterable[str] = (),
    complete_groups: frozenset[str] | None = None,
    min_train_blocks: int = 2,
) -> OptimizationRunResult:
    """Verify the immutable experiment before inference and compute a publication draft.

    The two callbacks see only their target masks and prevalidated fold keys.
    The holdout callback receives one locked spec/lineage and its causal folds
    per call. Its outcome, proposals, and rejection records inform the managed
    publisher, which owns the final receipt, transaction, and committed state.
    """
    _require_hex(code_commit, 40, "code_commit")
    data_cutoff = _aware(data_cutoff, "data_cutoff")
    if type(random_seed) is not int or not callable(deadline):
        raise ValueError("random_seed must be an int and deadline must be callable")
    if not callable(evaluate) or not callable(evaluate_holdout):
        raise ValueError("search and holdout evaluators must be callable")
    if not isinstance(policy_context, PolicyContext) or policy_context.started_at < data_cutoff:
        raise ValueError("policy context must pin an original STARTED at or after data_cutoff")
    declarations = _items(family.targets(), "family targets")
    if not declarations:
        raise ValueError(f"family {family.family_id!r} declares no optimization targets")
    targets = normalize_targets(declarations, None if targets is None else _items(targets, "requested targets"))
    descriptor = optimization_search_descriptor(
        family, targets=targets, schedule_id=schedule_id, data_cutoff=data_cutoff,
    )
    definitions = tuple(family.param_space())
    if model_contexts is not None:
        if not isinstance(model_contexts, Mapping) or set(model_contexts) != set(targets) or any(
            not _identifier(value) for value in model_contexts.values()
        ):
            raise ValueError("model contexts must cover every declared target")
        model_contexts = MappingProxyType(dict(model_contexts))
    if isinstance(build_candidate_spec, Mapping):
        if set(build_candidate_spec) - set(targets) or not all(map(callable, build_candidate_spec.values())):
            raise ValueError("candidate builders must be callable and identify a requested target")
        build_candidate_spec = MappingProxyType(dict(build_candidate_spec))
    elif build_candidate_spec is not None and not callable(build_candidate_spec):
        raise ValueError("candidate builder must be callable")
    family_id = family.family_id
    lineages = tuple(lineage_id(family_id, target) for target in targets)
    target_lineages = dict(zip(targets, lineages, strict=True))
    if type(max_trials) is not int or not 1 <= max_trials <= MAX_TRIALS:
        raise ValueError("max_trials must be positive and within the registered cap")
    if type(min_train_blocks) is not int or min_train_blocks < 1:
        raise ValueError("at least one training block is required")
    kind = resolve_fold_kind(family_id) if fold_kind is None else fold_kind
    if kind not in {"expiry", "matchday", "matchweek"}:
        raise ValueError(f"unknown fold kind {kind!r}")
    min_events, min_blocks = resolve_stage1_floors(family_id, floor_class=floor_class)
    family_class = floor_class or stage1_floor_class(family_id)
    first_deadline = deadline()
    manifest_summary = _plain(freeze_value(dict(dataset_summary or {})))
    refs = _items(snapshot_refs, "snapshot references")
    for digest in refs:
        _require_hex(digest, 64, "snapshot reference")
    if len(refs) != len(set(refs)):
        raise ValueError("snapshot references must be unique")
    refs = tuple(sorted(refs))
    _canonical_json(manifest_summary)
    base_submission = OptimizationRunSubmission(
        family=family_id, data_cutoff=data_cutoff, lineages=list(lineages),
        dataset_summary=manifest_summary, snapshot_refs=list(refs), search_space=descriptor,
        rule_version=RULE_VERSION, random_seed=random_seed,
        budget={"max_trials": max_trials, "time_budget_seconds": TIME_BUDGET_SECONDS, "deadline": "injected"},
        code_commit=code_commit,
    )
    run_key = base_submission.key()

    by_key = _registered_records(records)
    is_crypto = family_id == "crypto-gbm"
    is_international = family_id.startswith("intl-elo-")
    is_nba = family_id == "nba-pace-efficiency"
    if not is_crypto and (crypto_expiry_groups is not None or any(row.expiry_at for row in by_key.values())):
        raise ValueError("crypto expiry evidence belongs to the crypto model family")
    if nba_source_manifest is not None and not is_nba:
        raise ValueError("NBA source evidence belongs to the NBA model family")
    if source_artifacts is not None and not isinstance(source_artifacts, Mapping):
        raise ValueError("source artifacts must map registered digests to verified bytes")
    archived = dict(source_artifacts or {})
    if not first_deadline:
        source_evidence = _verified_sources(archived, refs)
    elif isinstance(crypto_expiry_groups, SourceArtifactRef) and crypto_expiry_groups.sha256 in archived:
        digest = crypto_expiry_groups.sha256
        source_evidence = _verified_sources({digest: archived[digest]}, refs)
    else:
        source_evidence = {}
    crypto_expiries = _crypto_expiry_days(by_key, crypto_expiry_groups, archived, source_evidence) if is_crypto else {}
    club_data = None
    if family_id.startswith("club-soccer-") or club_history is not None:
        if club_history is None:
            raise ValueError("club soccer requires its registered MO-4 official schedule audit")
        official_records, club_data = _club_records(club_history, data_cutoff)
        if by_key != {row.event_key: row for row in official_records}:
            raise ValueError("club records must match the reconciled MO-4 official fixture records")
        official_groups, rounds = club_data
        verified_complete = frozenset(
            group for group, row in rounds.items() if row[4] and len(official_groups[group]) == row[5]
        )
        if complete_groups is not None:
            supplied = _items(complete_groups, "complete_groups")
            if any(not _identifier(group) for group in supplied) or len(set(supplied)) != len(supplied) or (
                set(supplied) != verified_complete
            ):
                raise ValueError("club complete groups must match the MO-4 official partition")
        complete_groups = verified_complete
    if club_data is None and not is_crypto:
        for key, row in by_key.items():
            if row.source_day is not None and type(row.source_day) is not dt.date:
                raise ValueError("fixture source_day must be a date without time")
            block = (row.source_day.isoformat() if row.source_day is not None else
                     row.kickoff.date().isoformat() if row.kickoff is not None else None)
            if row.floor_block is not None and block is not None and row.floor_block != block:
                raise ValueError("floor block conflicts with the fixture's source calendar")
            by_key[key] = replace(row, floor_block=block)
    if is_nba or is_international:
        if not source_calendar or not by_key or any(type(row.source_day) is not dt.date for row in by_key.values()):
            raise ValueError("this family requires its named source-day chronology")
    timed_records, source_days = _causal_records(tuple(by_key.values()), source_calendar)
    fixture_lines = _registered_lines(fixture_lines)
    international = None
    source_order = None
    if is_international and not first_deadline:
        international = _international_source(international_snapshot, archived, source_evidence, by_key, fixture_lines)
        _verify_international_contracts(fixture_lines, international)
        if international is not None:
            source_order = {key: (international.identities[key].index if key in international.identities
                                  else len(international.dates) + offset)
                            for offset, key in enumerate(sorted(by_key))}
    elif not is_international and (international_snapshot is not None or any(row.intl_source_row for row in by_key.values())):
        raise ValueError("international source evidence belongs to the international model family")
    if club_goal_references is not None and not isinstance(club_goal_references, Mapping):
        raise ValueError("club score references must map fixture keys to reference rows")
    club_goals = dict(club_goal_references or {})
    if not first_deadline and any(
        key not in by_key or not isinstance(reference, ClubGoalReference)
        or reference.snapshot_sha256 not in source_evidence
        for key, reference in club_goals.items()
    ):
        raise ValueError("club score references must link an official fixture to verified snapshot bytes")
    source_checks, nba_evidence, nba_source = ((), {}, None) if first_deadline else _source_checks(
        family_id, archived, source_evidence, club_history, club_data, club_goals, data_cutoff,
        nba_source_manifest, international,
    )
    nba_identities, nba_sources = {}, {}
    if is_nba and not first_deadline:
        fixture_lines, nba_identities, nba_sources = _verify_nba_contracts(fixture_lines, by_key, nba_source)
    registered_records = by_key
    crypto_chronology = None
    incomplete_crypto_prefixes = frozenset()
    if is_crypto:
        cohort, selected_ids, clocks, incomplete_crypto_prefixes = _crypto_cohort(
            timed_records, holdout_group_ids, search_group_ids, complete_groups, data_cutoff=data_cutoff,
            source_days=source_days, source_calendar=source_calendar, source_timezone=source_timezone,
            last_complete_date=last_complete_date,
        )
        _verify_crypto_quote_times(fixture_lines, by_key, clocks, source_timezone)
        crypto_chronology = {
            "crypto_source_clocks": _crypto_clock_evidence(clocks, by_key),
            "crypto_complete_expiry_days": sorted(cohort._complete_groups),
        }
        # Keep the registered alias for the input seal and use the verified
        # date for the contract universe, fold scopes and all report masks.
        by_key = {key: replace(record, group_id=record.floor_block) for key, record in by_key.items()}
    universe_by_group = _fixture_universe(fixture_lines, by_key, declarations)
    contracts = {row.key: row for row in fixture_lines}
    if not is_crypto:
        cohort = validate_cohort(
            timed_records, holdout_group_ids, data_cutoff=data_cutoff,
            complete_groups=complete_groups, source_days=source_days,
            source_timezone=source_timezone, last_complete_date=last_complete_date, source_order=source_order,
        )
        selected_ids = None if search_group_ids is None else _items(search_group_ids, "search_group_ids")
        if selected_ids is not None and any(not _identifier(group) for group in selected_ids):
            raise ValueError("selected search groups must be nonempty identifiers")
        if selected_ids is not None and (
            not selected_ids or len(selected_ids) != len(set(selected_ids))
            or set(selected_ids) - set(cohort.search_group_ids)
        ):
            raise ValueError("selected search groups must be unique and inside the sealed search cohort")
    selected_set = None if selected_ids is None else frozenset(selected_ids)
    sealed_input_digest = _sealed_input_digest(
        cohort, registered_records, universe_by_group, targets, nba_games=nba_sources,
        expiry_groups=crypto_expiry_groups, crypto_chronology=crypto_chronology,
    )
    holdout_by_target = {
        target: () if is_nba and first_deadline else tuple(
            row for group in cohort.holdout_group_ids for row in universe_by_group.get((group, target), ())
        )
        for target in targets
    }
    holdout_sizes = {
        target: _block_counts(tuple(row.key for row in rows), by_key)
        for target, rows in holdout_by_target.items()
    }
    if is_crypto or is_nba:
        holdout_event_count, holdout_block_count = _block_counts(
            (row.key for lines in holdout_by_target.values() for row in lines), by_key,
        )
    else:
        holdout_event_count = len(cohort.holdout_records)
        holdout_block_count = len({by_key[row.event_key].floor_block for row in cohort.holdout_records})
    structural_floor = any(
        games < min_events or blocks < min_blocks for games, blocks in holdout_sizes.values()
    )
    markets = _market_index(_items(market_observations, "market observations"))
    source_rows = {} if international is None else international.identities
    source_event_keys = {key: row.event_key for key, row in source_rows.items()}
    source_event_keys.update({key: row["source_event_key"] for key, row in nba_sources.items()})
    clusters = {key: ("settled-market" if family_class == "crypto" else "match", source_event_keys.get(key, key))
                for key in by_key}
    registry_issue = policy_context.global_issue
    baseline = family.baseline_spec()
    _verify_spec(baseline, baseline)
    comparisons, actives, comparison_evidence, registry_flags = _registry_comparisons(
        family, lineages, policy_context, incumbent_spec, baseline,
    )
    flags = list(registry_flags)
    flags.extend(f"registry_fallback:{lineage}" for lineage in lineages if lineage not in actives)
    if registry_issue:
        flags.append(registry_issue)
    if structural_floor:
        flags.append("holdout_floors_unmet")
    outcome = INSUFFICIENT_DATA if structural_floor else NO_IMPROVEMENT
    budget_exhausted = False
    cohorts_report, nested_rows, winner_specs, selections, cooldown = {}, [], {}, {}, {}
    stats = {lineage: _closed_stat(lineage, "holdout_not_opened") for lineage in lineages}
    candidate_cards, incumbent_cards, diagnostics = {}, {}, {}
    holdout_comparisons, holdout_calls, holdout_closures = {}, [], {}
    search_predictions, predictions, proposals, rejections = [], [], {}, []
    catalogs, planned, trials, folds, skipped = {}, (), (), (), ()
    holdout_folds, holdout_skipped = (), ()
    planned_holdout_folds, planned_holdout_skips = (), ()
    active_batches = defaultdict(list)
    search_verified_batches = {}
    decisions = tuple(
        LineageDecision(lineage, 0, 0, False, False, False, None, False, False, "not_assessable",
                        attempted=False, status="holdout_not_opened")
        for lineage in lineages
    )
    issue = None
    failure = None
    search = None
    planned_target_uses = 0
    search_insufficient = set()
    intl_benchmarks, search_boundaries = {}, {}

    def check_deadline():
        if deadline():
            raise _DeadlineExpired

    def spec_builder(target, comparator):
        supplied = build_candidate_spec
        if isinstance(supplied, Mapping):
            supplied = supplied.get(target)
        if supplied is None:
            supplied = getattr(family, "spec_with", None)

        def build(params):
            nonlocal failure
            check_deadline()
            if supplied is not None:
                identity = {"target": target, "lineage": target_lineages[target]}
                try:
                    spec = supplied(params)
                except Exception as error:
                    failure = _callback_failure("spec_builder", identity, error, "callback_exception")
                    raise _AdapterFailure from error
            elif comparator.envelope:
                raise ValueError(f"family {family_id!r} requires a canonical build_candidate_spec")
            else:
                spec = replace(comparator, params=params, legacy_model_version=None)
            try:
                _verify_spec(spec, baseline)
            except ValueError as error:
                failure = _callback_failure("spec_builder", {"target": target, "lineage": target_lineages[target]},
                                            error, "invalid_model_spec")
                raise _AdapterFailure from error
            return spec

        return build

    def search_evaluate(spec, fold, mask):
        nonlocal failure
        digest = spec.spec_sha256()
        identity = {"fold": fold.block_id, "spec_sha256": digest, "targets": mask,
                    "lineages": tuple(target_lineages[target] for target in mask)}
        try:
            raw = evaluate(spec, fold, mask)
        except _DeadlineExpired:
            raise
        except Exception as error:
            failure = _callback_failure("search_callback", identity, error, "callback_exception")
            raise _AdapterFailure("search callback raised an exception") from error
        try:
            if not isinstance(raw, Mapping) or set(raw) != set(mask):
                raise _BatchIntegrityError("adapter targets conflict with the forecast-independent mask")
            verified = {
                target: _verify_batch(spec, raw[target], universe_by_group.get((fold.block_id, target), ()),
                                      clusters, by_key, search_boundaries, search_benchmarks, is_crypto=is_crypto)
                for target in mask
            }
            if any(batch.reported_median is not None or batch.reported_market is not None for batch in verified.values()):
                raise _BatchIntegrityError("search adapter supplied summary assertions outside its fold scope")
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            reason = str(error) if isinstance(error, _BatchIntegrityError) else "invalid_search_forecast_batch"
            failure = _callback_failure("search_validation", identity, error, reason)
            raise _AdapterFailure(reason) from error
        for target, batch in verified.items():
            search_verified_batches[fold.block_id, digest, target] = batch
            if digest == comparison_evidence[target_lineages[target]]["spec_sha256"]:
                active_batches[target].append((fold.block_id, batch))
        return {target: batch.forecast for target, batch in verified.items()}

    def holdout_evaluate(spec, target, lineage, side, universe):
        nonlocal failure
        check_deadline()
        identity = {"lineage": lineage, "target": target, "side": side, "spec_sha256": spec.spec_sha256(),
                    "folds": tuple(fold.block_id for fold in holdout_folds)}
        call = {**identity, "status": "started", "eligible_line_count": len(universe),
                "prediction_count": 0, "refusal_count": 0}
        holdout_calls.append(call)
        try:
            raw = evaluate_holdout(spec, target, holdout_folds)
        except _DeadlineExpired:
            call.update(status="aborted", failure={"reason": "compute_deadline"})
            raise
        except Exception as error:
            failure = _callback_failure("holdout_callback", identity, error, "callback_exception")
            call.update(status="aborted", failure=failure)
            raise _AdapterFailure from error
        try:
            batch = _verify_batch(
                spec, raw, universe, clusters, by_key, holdout_boundaries, holdout_benchmarks, is_crypto=is_crypto,
            )
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            reason = str(error) if isinstance(error, _BatchIntegrityError) else "invalid_holdout_forecast_batch"
            failure = _callback_failure("holdout_validation", identity, error, reason)
            call.update(status="aborted", failure=failure)
            raise _AdapterFailure from error
        call.update(status="completed", prediction_count=len(batch.forecast.predictions),
                    refusal_count=len(batch.forecast.refusals), refusals=[
                        dict(row, fold=by_key[row["event_key"]].group_id) for row in _batch_refusals(batch)
                    ])
        predictions.extend(
            dict(row, holdout_call_index=len(holdout_calls) - 1, fold=by_key[row["event_key"]].group_id)
            for row in _prediction_payload(batch, side, lineage, spec.spec_sha256())
        )
        return batch

    try:
        if first_deadline:
            raise _DeadlineExpired
        check_deadline()
        if club_data is not None and not is_crypto:
            requested = set(cohort.search_group_ids) if selected_set is None else selected_set
            folds, skipped = _club_folds(
                club_history, club_data, tuple(group for group in cohort.search_group_ids if group in requested),
                min_train_blocks,
            )
        else:
            folds, skipped = selected_prefix_folds(cohort, selected_group_ids=selected_ids,
                                                   min_train_blocks=min_train_blocks)
            folds, skipped = _crypto_source_day_folds(folds, skipped, incomplete_crypto_prefixes)
            folds = _scoped_folds(folds, by_key, source_calendar)
        if club_data is not None and not is_crypto:
            planned_holdout_folds, planned_holdout_skips = _club_folds(
                club_history, club_data, cohort.holdout_group_ids, min_train_blocks,
            )
        else:
            planned_holdout_folds, planned_holdout_skips = holdout_prefix_folds(cohort, min_train_blocks=min_train_blocks)
            planned_holdout_folds, planned_holdout_skips = _crypto_source_day_folds(
                planned_holdout_folds, planned_holdout_skips, incomplete_crypto_prefixes,
            )
            planned_holdout_folds = _scoped_folds(planned_holdout_folds, by_key, source_calendar)
        sealed_folds, intl_benchmarks = _international_folds((*folds, *planned_holdout_folds), international,
                                                             by_key, universe_by_group)
        sealed_folds = _nba_folds(sealed_folds, nba_source, nba_identities)
        folds, planned_holdout_folds = sealed_folds[:len(folds)], sealed_folds[len(folds):]
        search_boundaries = _prediction_boundaries(folds, by_key)
        holdout_boundaries = _prediction_boundaries(planned_holdout_folds, by_key)
        search_benchmarks = {key: value for key, value in intl_benchmarks.items() if key[0] in search_boundaries}
        holdout_benchmarks = {key: value for key, value in intl_benchmarks.items() if key[0] in holdout_boundaries}
        check_deadline()
    except _DeadlineExpired:
        budget_exhausted = True
        outcome = RUN_FAILED

    try:
        if budget_exhausted:
            raise _DeadlineExpired
        diagnostic_only = structural_floor or bool(registry_flags) or bool(registry_issue)
        for target, lineage in target_lineages.items():
            check_deadline()
            comparator = comparisons[lineage]
            if diagnostic_only:
                # Diagnostic folds need only the exact comparator.
                catalogs[target] = build_catalog(comparator, (), spec_builder(target, comparator),
                                                 continuous_points=CONTINUOUS_GRID_POINTS)
            else:
                catalogs[target] = build_catalog(
                    comparator, definitions, spec_builder(target, comparator),
                    continuous_points=CONTINUOUS_GRID_POINTS, one_coordinate=family_id == "nba-pace-efficiency",
                )
        planned = plan_evaluations(folds, catalogs, model_contexts=model_contexts, diagnostic_only=diagnostic_only)
        planned_target_uses = sum(len(trial.targets) for trial in planned)
        universes = {(fold.block_id, target): tuple(row.key for row in universe_by_group.get((fold.block_id, target), ()))
                     for fold in folds for target in targets}
        search = run_scoped_trials(planned, search_evaluate,
                                   budget=SearchBudget(max_trials, deadline), universes=universes)
        trials = search.trials
        if search.budget_exhausted:
            raise _DeadlineExpired
        if search.issue:
            if failure is not None:
                raise _AdapterFailure
            raise ValueError(search.issue)
        check_deadline()
        for target, lineage in target_lineages.items():
            check_deadline()
            diagnostic, forecast_rows = _active_diagnostic(
                family_class, target, lineage, comparisons[lineage], active_batches[target],
                contracts, source_evidence, set(refs), source_checks, international, search_benchmarks,
            )
            if diagnostic is not None:
                diagnostics[lineage] = diagnostic
                search_predictions.extend(forecast_rows)
        if registry_flags or registry_issue:
            outcome = RUN_FAILED
            stats = {lineage: _closed_stat(lineage, "registry_context_unverified") for lineage in lineages}
        elif structural_floor:
            stats = {lineage: _closed_stat(lineage, "structural_holdout_floor") for lineage in lineages}
        else:
            mandatory_skip = any(
                selected_ids is not None or row.reason != "insufficient_training_blocks" for row in skipped
            )
            bans = banned_spec_digests(policy_context.prior_rejections, now=policy_context.started_at,
                                       family=family_id, run_key=run_key)
            for target, lineage in target_lineages.items():
                check_deadline()
                catalog = catalogs[target]
                comparator = comparisons[lineage]
                for fold in folds:
                    check_deadline()
                    inner_ids = nested_fold_ids(fold, folds)
                    nested = select_common(search, catalog, definitions, target, inner_ids, require_all=False)
                    batch = search.batches[(fold.block_id, nested.winner.spec_sha256(), target)]
                    outer_rows = tuple(value.row for value in batch.predictions.values())
                    outer = build_scorecard(nested.winner, outer_rows, refusals=len(batch.refusals))
                    nested_rows.append({
                        **_comparison_payload(nested), "fold": fold.block_id, "lineage": lineage,
                        "inner_block_ids": inner_ids, "outer_mean_loss": outer.canonical_dict()["log_loss"],
                        "outer_predicted_lines": len(batch.predictions),
                        "outer_eligible_lines": len(batch.universe),
                    })
                final = select_common(search, catalog, definitions, target,
                                      tuple(fold.block_id for fold in folds))
                cohorts_report[lineage] = _comparison_payload(final)
                selections[lineage] = final.selection
                if mandatory_skip or final.selection is None:
                    stats[lineage] = _closed_stat(lineage, "insufficient_comparable_search_folds")
                    search_insufficient.add(lineage)
                    flags.append(f"insufficient_comparable_search_folds:{lineage}")
                else:
                    winner_specs[lineage] = final.winner
                    digest = final.winner.spec_sha256()
                    if digest == comparator.spec_sha256():
                        stats[lineage] = _closed_stat(lineage, "holdout_untouched_incumbent_selected")
                        flags.append("holdout_untouched_incumbent_selected")
                    elif digest in bans:
                        cooldown[lineage] = bans[digest]
                        reason = bans[digest]["reason"]
                        stats[lineage] = _closed_stat(lineage, reason)
                        flags.append(reason)
                    else:
                        stats[lineage] = _closed_stat(lineage, "locked_challenger")
            for target, lineage in target_lineages.items():
                check_deadline()
                if stats[lineage].status != "locked_challenger":
                    continue
                holdout_folds, holdout_skipped = planned_holdout_folds, planned_holdout_skips
                stats[lineage] = LineageStat(lineage, 0, 0, None, None, status="holdout_in_progress")
                universe = holdout_by_target[target]
                closed = {skip.block_id: skip.reason for skip in holdout_skipped}
                admitted = tuple(row for row in universe if row.event_key in holdout_boundaries)
                excluded = {row.key: closed.get(by_key[row.event_key].group_id, "missing_causal_holdout_prefix")
                            for row in universe if row.event_key not in holdout_boundaries}
                holdout_closures[lineage] = _refusal_payload(excluded)
                if not holdout_folds:
                    stats[lineage] = _closed_stat(lineage, "no_executable_holdout_folds")
                    continue
                spec = winner_specs[lineage]
                comparator = comparisons[lineage]
                left = holdout_evaluate(spec, target, lineage, "candidate", admitted)
                right = holdout_evaluate(comparator, target, lineage, "incumbent", admitted)
                check_deadline()
                stat, candidate, incumbent, payload = _holdout_comparison(
                    family=family_class, target=target, lineage=lineage, candidate=spec,
                    incumbent=comparator, left=_augment_refusals(left, universe, excluded),
                    right=_augment_refusals(right, universe, excluded),
                    universe=universe, records=by_key, markets=markets, clusters=clusters,
                    source_evidence=source_evidence, source_refs=set(refs), source_checks=source_checks,
                    international=international, intl_benchmarks=holdout_benchmarks,
                )
                stats[lineage] = stat
                candidate_cards[lineage], incumbent_cards[lineage] = candidate, incumbent
                holdout_comparisons[lineage] = payload
                check_deadline()

        decisions, low_paired = evaluate_stage1_rule(
            tuple(stats.values()), min_events=min_events, min_blocks=min_blocks,
        )
        quarantine = any(row.status == _QUARANTINE_REASON for row in stats.values())
        if low_paired or any(row.status == "no_executable_holdout_folds" for row in stats.values()):
            outcome = INSUFFICIENT_DATA
            flags.append("holdout_floors_unmet")
        elif search_insufficient and not quarantine and not any(stat.attempted for stat in stats.values()):
            outcome = INSUFFICIENT_DATA
        if outcome not in (RUN_FAILED, INSUFFICIENT_DATA):
            revised, blocked = [], False
            for decision in decisions:
                lineage = decision.lineage
                decision = replace(decision, registered=False)
                if decision.statistically_eligible:
                    spec = winner_specs[lineage]
                    result = selections[lineage]
                    reason = "preseason_reestimate_required" if result.needs_preseason_reestimate else (
                        _publication_block(policy_context, lineage, spec, actives, baseline.spec_sha256())
                    )
                    if reason is None:
                        proposals[lineage] = spec
                        decision = replace(decision, status="proposed")
                    else:
                        blocked = True
                        decision = replace(decision, status=reason)
                        flags.append(f"{reason}:{lineage}")
                elif (decision.attempted and not stats[lineage].evidence_unverified
                      and not stats[lineage].has_model_refusals and decision.envelope_status != "unverified"
                      and (reasons := _judgment_reasons(decision))):
                    rejections.append(JudgedRejection(
                        lineage, winner_specs[lineage].spec_sha256(), reasons, decision.n_events, decision.n_blocks,
                    ))
                    decision = replace(decision, status="statistically_rejected")
                revised.append(decision)
            decisions = tuple(revised)
            outcome = CANDIDATE_REGISTERED if proposals else RUN_FAILED if blocked or quarantine else NO_IMPROVEMENT
        else:
            decisions = tuple(replace(decision, registered=False, statistically_eligible=False) for decision in decisions)
    except _DeadlineExpired:
        budget_exhausted = True
        outcome = RUN_FAILED
    except _AdapterFailure:
        issue = failure["reason"]
        outcome = RUN_FAILED
        flags.append("external_callback_failure" if issue == "callback_exception" else "evaluation_integrity_failure")
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError) as error:
        issue = "scorecard_numeric_failure" if isinstance(error, ArithmeticError) else str(error)
        outcome = RUN_FAILED
        flags.append("evaluation_integrity_failure")
    if budget_exhausted or issue is not None:
        decisions = tuple(LineageDecision(
            stat.lineage, stat.n_events, stat.n_blocks, False,
            stat.attempted and stat.n_events >= min_events and stat.n_blocks >= min_blocks,
            stat.ci95_upper is not None and stat.ci95_upper < _STAGE1_CI_UPPER_BOUND, None,
            stat.calibration_regression, stat.gate_regression, stat.envelope_status,
            attempted=stat.attempted, status=stat.status,
        ) for stat in stats.values())

    for decision in decisions:
        flags.extend(f"{flag}:{decision.lineage}" for flag in decision.flags)

    def finalize():
        final_flags = tuple(sorted(set(flags) | ({"budget_exhausted"} if budget_exhausted else set())))
        final_decisions = decisions
        final_proposals = proposals
        final_rejections = tuple(rejections)
        if budget_exhausted or issue or outcome == INSUFFICIENT_DATA:
            final_proposals = {}
            final_rejections = ()
            final_decisions = tuple(replace(decision, registered=False, statistically_eligible=False)
                                    for decision in decisions)
        if outcome == RUN_FAILED:
            final_proposals = {}
            final_rejections = ()
        stat_rows = [{
            "lineage": stat.lineage, "attempted": stat.attempted,
            "p_value": stat.p_value if stat.attempted else 1.0,
            "reason": stat.status, "observed_p_value": stat.p_value,
        } for stat in stats.values()]
        artifact = {
            "schema": "papersim.optimize.runner/artifact/v2",
            "rule_version": RULE_VERSION, "run_key": run_key,
            "statistical_parameters": {
                "bootstrap_resamples": bootstrap_resamples(), "ci_confidence_level": CI_CONFIDENCE_LEVEL,
                "holm_alpha": HOLM_ALPHA, "sign_flip_reps": SIGN_FLIP_REPS, "sign_flip_seed": SIGN_FLIP_SEED,
            },
            "family": family_id, "procedure_version": family.procedure_version,
            "data_cutoff": _iso(data_cutoff), "random_seed": random_seed, "code_commit": code_commit,
            "fold_kind": kind, "search_space": descriptor, "outcome": outcome, "flags": final_flags,
            "policy_started_at": _iso(policy_context.started_at), "policy_watermark": policy_context.watermark,
            "policy_controls": {
                "global_frozen": policy_context.global_frozen, "frozen_lineages": sorted(policy_context.frozen_lineages)
            },
            "dataset_summary": manifest_summary, "snapshot_refs": refs, "verified_source_bytes": source_evidence,
            "nba_source_integrity": nba_evidence,
            **({"nba_source_games": nba_sources} if is_nba else {}),
            **({
                "crypto_expiry_days": crypto_expiries,
                "crypto_expiry_groups": None if crypto_expiry_groups is None else asdict(crypto_expiry_groups),
                **crypto_chronology,
            } if is_crypto else {}),
            "international_source_rows": {
                key: {"source_identity": [row.snapshot.sha256, "matches", row.index],
                      "source_event_key": row.event_key, "source_day": _iso(by_key[key].source_day),
                      "home": row.home, "away": row.away, "tournament": row.tournament,
                      "neutral": row.neutral, "home_goals": row.home_goals, "away_goals": row.away_goals}
                for key, row in source_rows.items()
            },
            "international_benchmarks": [
                {"source_event_key": source_rows[key].event_key, "target": target,
                 "source_day": _iso(by_key[key].source_day),
                 "source_original_row_index": source_rows[key].index, "causal_probabilities": entry.causal,
                 "fixed_probabilities": entry.fixed, "goal_sum": entry.goal_sum,
                 "tournament_matches": entry.n_matches, "home_draw_away_counts": entry.class_counts,
                 "class_order": entry.class_order, "line": entry.line}
                for (key, target), entry in sorted(intl_benchmarks.items(), key=lambda item: (
                    source_rows[item[0][0]].index, item[0][1],
                ))
                if key in search_boundaries or target_lineages.get(target) in holdout_comparisons
            ],
            "sealed_input_sha256": sealed_input_digest,
            "source_clock": {
                "precision": "date" if source_days is not None else "timestamp",
                "calendar": cohort.source_calendar, "timezone": str(source_timezone) if source_timezone else None,
                "last_complete_date": None if last_complete_date is None else _iso(last_complete_date),
                "source_day_cutoff_inclusive": source_days is not None,
                "first_holdout_prediction": _iso(cohort.first_holdout_prediction),
                "club_completion": "later official/source kickoff plus three event-time hours" if club_data else None,
            },
            "comparison_specs": comparison_evidence, "active_specs": actives,
            "catalogs": {
                target_lineages[target]: {
                    "anchor": _plain(catalog.anchor), "strategy": catalog.strategy,
                    "entries": [{
                        "spec_sha256": entry.spec_sha256, "automatic": entry.automatic, "params": _plain(entry.spec.params),
                    } for entry in catalog.entries],
                } for target, catalog in catalogs.items()
            },
            "folds": [fold.block_id for fold in folds],
            "search_group_ids": [group for group in cohort.search_group_ids if selected_set is None or group in selected_set],
            "skipped_blocks": [{"block_id": row.block_id, "reason": row.reason} for row in skipped],
            "holdout": {
                "group_ids": cohort.holdout_group_ids, "n_events": holdout_event_count,
                "n_blocks": holdout_block_count,
                "floors": {"min_events": min_events, "min_blocks": min_blocks},
                "eligible_by_lineage": {
                    target_lineages[target]: dict(zip(("games", "blocks"), counts, strict=True))
                    for target, counts in holdout_sizes.items()
                },
                "evaluated_folds": [fold.block_id for fold in holdout_folds],
                "skips": [{"block_id": row.block_id, "reason": row.reason} for row in holdout_skipped],
                **({
                    "planned_folds": [fold.block_id for fold in planned_holdout_folds],
                    "planned_skips": [{"block_id": row.block_id, "reason": row.reason}
                                      for row in planned_holdout_skips],
                } if is_crypto else {}),
                "comparisons": holdout_comparisons, "callbacks": holdout_calls, "structural_closures": holdout_closures,
            },
            "planned_evaluations": [{
                "index": trial.trial_index, "fold": trial.fold.block_id, "spec_sha256": trial.spec_sha256,
                "targets": trial.targets, "model_context": trial.model_context,
                "fold_sha256": trial.fold_sha256,
            } for trial in planned],
            "trials": [_trial_payload(trial) for trial in trials],
            "search_trial_forecasts": _search_forecast_artifact(search, search_verified_batches),
            "nested_selection": nested_rows, "selection_by_lineage": cohorts_report, "cooldown": cooldown,
            "holm_hypotheses": stat_rows, "decisions": [asdict(decision) for decision in final_decisions],
            "candidate_scorecards": candidate_cards, "incumbent_scorecards": incumbent_cards,
            "diagnostic_scorecards": diagnostics, "search_predictions": search_predictions, "predictions": predictions,
            "proposed_specs": {lineage: spec.spec_sha256() for lineage, spec in final_proposals.items()},
            "statistical_rejections": [asdict(row) for row in final_rejections],
            "issue": issue, "failure": failure,
            "counts": {
                "evaluated_trials": len(trials), "planned_trials": len(planned), "search_folds_constructed": len(folds),
                "holdout_folds_constructed": len(holdout_folds), "holdout_callbacks": len(holdout_calls),
                "completed_holdout_callbacks": sum(call["status"] == "completed" for call in holdout_calls),
                "aborted_holdout_callbacks": sum(call["status"] == "aborted" for call in holdout_calls),
                "completed_trials": sum(trial.status == TRIAL_COMPLETED for trial in trials),
                "empty_trials": sum(trial.status == TRIAL_NO_EVENTS for trial in trials),
                "aborted_trials": sum(trial.status == TRIAL_ABORTED for trial in trials),
                "shared_target_uses_planned": planned_target_uses - len(planned),
                "shared_target_uses_completed": 0 if search is None else search.shared_target_uses,
                "predictions": len(predictions), "search_predictions": len(search_predictions),
            },
        }
        artifact_bytes = _canonical_json(artifact)
        digest = sha256(artifact_bytes).hexdigest()
        submission = replace(
            base_submission, trial_count=len(trials), outcome=outcome, active_specs=actives,
            artifacts_sha256=digest, scorecard=candidate_cards or diagnostics,
        ).validate()
        return OptimizationRunResult(
            outcome, submission, artifact_bytes, digest, run_key, trials, MappingProxyType(dict(selections)),
            final_decisions, final_flags, budget_exhausted, MappingProxyType(comparisons),
            MappingProxyType(actives), MappingProxyType(dict(winner_specs)),
            MappingProxyType(dict(final_proposals)), final_rejections,
        )

    result = finalize()
    if deadline() and not budget_exhausted:
        budget_exhausted = True
        outcome = RUN_FAILED
        return finalize()
    return result
