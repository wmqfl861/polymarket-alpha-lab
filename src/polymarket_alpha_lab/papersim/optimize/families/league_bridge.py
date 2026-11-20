"""The cross-league strength table wrapped as a season-frozen family.

MO-5e (design 04 sections 2, 7 and 8; plan 05 node MO-5 row 5e; the
reviewed node plan's P2/P3 findings are incorporated and cited
inline). The frozen league-strength kappa table
(:data:`league_strength.KAPPA`, WP-P5 pre_2025_26) and the away-side
corrector (:data:`league_strength.AWAY_HAIRCUT`) are an INPUT to
every club-soccer lineage's cross-league range, not a scoring model
of their own. This family therefore declares NO targets
(``targets() -> ()`` -- no lineage, no candidates, no role events)
and NO search space (``param_space() -> ()``; plan 05 row 5e: "不做
搜索"). What it provides is exactly the three things design 04
section 7 assigns the optimizer for this row:

- **Pre-season re-estimation trigger** (:func:`reevaluation_gate`).
  The kappa table is re-frozen exactly once before each season and
  never mid-season (FX-17 third clause; design 04 section 2). The
  freeze boundary follows the WP-P5 harness convention: each season
  has a FIRST EVALUATION DAY (``FIRST_EVAL_DAY`` in
  ``two_season_revalidate.py``; the shipped table's fit rule is
  "obs kickoff day < 2025-09-16") and everything strictly before
  that UTC day is pre-season. The boundary arrives WITH the request
  (``LeagueSeason`` in the gate call / history payload); the family
  deliberately ships no season calendar of its own -- the harness
  is the authority for future seasons' first eval days (review
  P3-1). Time comparison is day-granular on UTC calendar dates
  (:func:`_utc_day`, pinned by tests).
- **Summary registration** (:class:`RefreezeSummary`,
  :func:`request_refreeze`). The in-process return value IS the
  record (review P3-3); persisting it into
  ``papersim.optimization_runs`` / snapshots is MO-6's runner. A
  mid-season rejection is a record too -- it keeps the refusal
  reason and the freeze evidence and carries NO new table (no
  kappa, no haircut: nothing re-estimable was bound). The summary
  always carries the proposed ``away_haircut`` when a proposal
  exists (review P3-2; design 04 section 7 "客场折减同时重估").
- **Freeze verification** (:func:`verify_in_force_freeze`). After
  any re-estimation request, ``queue_policy.check_kappa_freeze``
  proves the in-force seasons' frozen digests still match the
  observed production table; block + keep evidence, never silently
  accept. For the NEW season (no frozen digest yet) the check is
  expectedly UNVERIFIED -- the proposal's registration creates the
  digest; the failure semantics apply to seasons already in force.

Re-estimation itself is NOT re-implemented here: the numeric
procedure is the WP-P5 two-season harness (server research tree),
and :meth:`LeagueBridgeFamily.fit` only binds its already-produced
report artifact via :func:`league_strength.kappa_from_report` (the
pure-payload twin of ``load_kappa_report`` promoted by this node).
Swapping the production table (the ``KAPPA`` / ``AWAY_HAIRCUT``
module constants) stays an owner-authorized, reviewed code change
exactly as WP-P5 was (DELIVERY_PLAN section 75): this family
proposes, it never ships.

``predict`` is a pure passthrough of :func:`xg.pair_lambdas` with
``bridge=fitted.table.kappa`` (review P2; the soccer_frozen
"pure passthrough" precedent): the bridged arithmetic -- home
``* f``, away ``* AWAY_HAIRCUT_FACTOR / f`` with the haircut
applied BEFORE the tilt division (xg.py comments on the
float-exactness of that order) -- lives in exactly one place. A
fitted haircut that differs from the module constant is an
UNSHIPPED proposal the frozen xg path cannot express; ``predict``
refuses it rather than re-deriving the arithmetic.

Baseline identity: ``baseline_spec`` REFERENCES the module
constants (``dict(KAPPA.kappa)`` / ``AWAY_HAIRCUT``), so an owner
re-freeze flips the baseline spec sha. That is the intended
semantics -- a new season's table is a new identity -- not a
defect; and since this family has no lineages, a re-freeze cannot
conflict with any registry initialization (review P3-5).
"""

from __future__ import annotations

import dataclasses
import datetime
import math
import re
from collections.abc import Mapping, Sequence

from polymarket_alpha_lab.papersim import (
    league_strength,
    pipeline,
    queue_policy,
    xg,
)
from polymarket_alpha_lab.papersim.optimize.spec import (
    ModelSpec,
    ParamDef,
    freeze_value,
)

__all__ = [
    "DATA_CONTRACT",
    "FAMILY_ID",
    "LEAGUE_BRIDGE",
    "PROCEDURE_VERSION",
    "STATUS_FREEZE_CHECK_FAILED",
    "STATUS_PROPOSED",
    "STATUS_REJECTED_MID_SEASON",
    "TARGETS",
    "LeagueBridgeFamily",
    "LeagueBridgeFitted",
    "LeagueBridgeFixture",
    "LeagueBridgeHistory",
    "LeagueSeason",
    "RefreezeSummary",
    "reevaluation_gate",
    "request_refreeze",
    "verify_in_force_freeze",
]

#: Family id (design 04 section 3 / section 7 row "跨联赛强度表").
FAMILY_ID = "league-bridge"

#: Version of the estimation CODE, not of the parameters: generation
#: "1" of the WP-P5 two-season harness whose report artifact this
#: family binds (same convention as recalib's "p4r1"). A change to
#: the harness protocol -- cutoff rule, corrector family, report
#: shape -- requires a new procedure version, not a new param.
PROCEDURE_VERSION = "p5r1"

#: The data contract of the report artifact ``fit`` consumes: a
#: WP-P5-shaped estimation report mapping whose members are
#: ``"kappa"`` (league -> kappa, validated by
#: :func:`league_strength.kappa_from_report`), ``"fit_rule"`` in the
#: harness's exact spelling ``"obs kickoff day < YYYY-MM-DD"``,
#: ``"leak_free"`` (must be true), ``"n_train"`` (positive int) and
#: ``"away_haircut"`` (the harness corrector_selection's landing
#: value). Forward placeholder like every family's contract: freely
#: adjustable only until the FIRST REAL ``optimize init`` (MO-6).
DATA_CONTRACT = "wp-p5-kappa-report-v1"

#: Market types this family scores: NONE. The bridge table is an
#: input to the club-soccer families' cross-league range, not a
#: scoring model; no targets means no lineage, hence no candidates,
#: no shadow and no role events -- only the pre-season refreeze
#: trigger/summary/verification of this module.
TARGETS: tuple[str, ...] = ()

#: Summary statuses (see :class:`RefreezeSummary`).
STATUS_PROPOSED = "PROPOSED"
STATUS_REJECTED_MID_SEASON = "REJECTED_MID_SEASON"
STATUS_FREEZE_CHECK_FAILED = "FREEZE_CHECK_FAILED"
_STATUSES = frozenset(
    {STATUS_PROPOSED, STATUS_REJECTED_MID_SEASON, STATUS_FREEZE_CHECK_FAILED}
)

#: The harness fit-rule spelling this module binds: the WP-P5
#: two-season harness writes exactly ``"obs kickoff day < %s" % day``.
_FIT_RULE_RE = re.compile(r"^obs kickoff day < (\d{4}-\d{2}-\d{2})$")

_MID_SEASON_REASON = (
    "kappa re-estimation refused: season {season!r} is already in play "
    "(first eval day {day}); the table is re-frozen exactly once before "
    "each season and never mid-season (FX-17 / design 04 section 2)"
)

_PROPOSAL_FIELDS = (
    "away_haircut",
    "kappa",
    "table_sha256",
    "bound_spec_sha256",
    "fit_rule",
)


def _utc_day(value: datetime.datetime) -> datetime.date:
    """UTC calendar day of one instant; tz-aware required (fail-closed).

    The harness freezes on whole kickoff DAYS ("obs kickoff day"),
    so the pre-season test is day-granular: both sides are
    normalized to their UTC calendar date and compared strictly.
    A naive datetime has no UTC day, so it is refused rather than
    guessed (review P3-1; pinned by tests).
    """

    if not isinstance(value, datetime.datetime):
        raise TypeError(f"expected a datetime, got {type(value).__name__}")
    if value.tzinfo is None:
        raise ValueError(
            f"naive datetime has no UTC day: {value!r}; pass tz-aware"
        )
    return value.astimezone(datetime.timezone.utc).date()


@dataclasses.dataclass(frozen=True)
class LeagueSeason:
    """One season's freeze calendar: label plus first evaluation day.

    ``first_eval_day`` is the harness's ``FIRST_EVAL_DAY`` for the
    season (the shipped 2025-26 freeze used 2025-09-16): the first
    day of in-season play, and therefore the exclusive upper bound
    of every legal re-estimation. The calendar travels WITH the
    request -- the family keeps no season table of its own (review
    P3-1). Tz-aware required.
    """

    season: str
    first_eval_day: datetime.datetime

    def __post_init__(self) -> None:
        if not isinstance(self.season, str) or not self.season:
            raise ValueError(
                f"season label must be a non-empty string, got {self.season!r}"
            )
        # Fail closed on a naive or non-datetime boundary.
        _utc_day(self.first_eval_day)


@dataclasses.dataclass(frozen=True)
class ReevaluationGate:
    """The pre-season trigger's verdict for one request (pure value)."""

    allowed: bool
    season: str
    requested_at: datetime.datetime
    first_eval_day: datetime.datetime
    reason: str | None


def reevaluation_gate(
    season: LeagueSeason, as_of: datetime.datetime
) -> ReevaluationGate:
    """May the kappa table be re-estimated for ``season`` at ``as_of``?

    Pre-season is ``utc_day(as_of) < utc_day(season.first_eval_day)``
    (strict, day-granular, timezone-normalized). Mid-season requests
    are refused with a fixed reason string -- the refusal itself is
    a record the caller registers; this function never touches a
    clock, file or connection (pinned by tests).
    """

    if not isinstance(season, LeagueSeason):
        raise TypeError(
            f"reevaluation_gate expects LeagueSeason, got {type(season).__name__}"
        )
    boundary = _utc_day(season.first_eval_day)
    allowed = _utc_day(as_of) < boundary
    reason = (
        None
        if allowed
        else _MID_SEASON_REASON.format(season=season.season, day=boundary.isoformat())
    )
    return ReevaluationGate(
        allowed=allowed,
        season=season.season,
        requested_at=as_of,
        first_eval_day=season.first_eval_day,
        reason=reason,
    )


@dataclasses.dataclass(frozen=True)
class LeagueBridgeHistory:
    """Fit inputs: one estimation report plus the season it targets."""

    report: Mapping[str, object]
    season: LeagueSeason


@dataclasses.dataclass(frozen=True)
class LeagueBridgeFixture:
    """One bridged fixture; fields mirror :func:`xg.pair_lambdas`.

    ``last_weight``/``prior_games`` are the variant knobs of the
    frozen soccer model (no defaults there, none here); the rest
    carry ``pair_lambdas``' own defaults.
    """

    snapshot: Mapping[str, object]
    home_team: str
    home_league: str
    away_team: str
    away_league: str
    last_weight: float
    prior_games: float
    mode: str = "blend"
    shrink: float = 1.0
    season: str | None = None


@dataclasses.dataclass(frozen=True)
class LeagueBridgeFitted:
    """Fitted state: the bound table plus its report provenance.

    The provenance scalars travel with the fitted state so a
    summary can be built without re-parsing the report.
    """

    table: league_strength.LeagueStrengthTable
    away_haircut: float
    spec_sha256: str
    fit_rule: str
    leak_free: bool
    n_train: int


def _finite_number(value: object, name: str) -> float:
    """Require a finite real number (bool rejected); return ``float``."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    entry = float(value)
    if not math.isfinite(entry):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return entry


def _validated_params(spec: ModelSpec) -> dict[str, object]:
    """Validate ``spec`` against this family's frozen shape; early fail."""

    if spec.family != FAMILY_ID:
        raise ValueError(
            f"{FAMILY_ID}.fit expects family {FAMILY_ID!r}, got {spec.family!r}"
        )
    if spec.procedure_version != PROCEDURE_VERSION:
        raise ValueError(
            f"{FAMILY_ID}: procedure version {spec.procedure_version!r} "
            f"!= {PROCEDURE_VERSION!r}"
        )
    if spec.data_contract != DATA_CONTRACT:
        raise ValueError(
            f"{FAMILY_ID}: data contract {spec.data_contract!r} "
            f"!= {DATA_CONTRACT!r}"
        )
    if tuple(spec.envelope) != ():
        raise ValueError(
            f"{FAMILY_ID}: the bridge has no envelope (design 04 section 7: "
            "not applicable); got a non-empty one"
        )
    params = dict(spec.params)
    if set(params) != {"kappa", "away_haircut"}:
        raise ValueError(
            f"{FAMILY_ID} params must be exactly the two keys "
            f"['away_haircut', 'kappa'], got {sorted(params)!r}"
        )
    kappa = params["kappa"]
    if not isinstance(kappa, Mapping) or not kappa:
        raise ValueError(
            f"{FAMILY_ID}: 'kappa' must be a non-empty league -> k mapping"
        )
    for key, value in kappa.items():
        if not isinstance(key, str) or not key:
            raise ValueError(
                f"{FAMILY_ID}: kappa keys must be non-empty strings, got {key!r}"
            )
        _finite_number(value, f"kappa[{key!r}]")
    haircut = _finite_number(params["away_haircut"], "'away_haircut'")
    if haircut < 0.0:
        raise ValueError(
            f"{FAMILY_ID}: 'away_haircut' must be >= 0, got {haircut!r}"
        )
    return params


def _report_facts(
    report: Mapping[str, object], season: LeagueSeason
) -> tuple[league_strength.LeagueStrengthTable, float, str, bool, int]:
    """Extract and validate the report; return (table, haircut, rule...).

    The kappa member is parsed by :func:`league_strength.kappa_
    from_report` (the shared loader rule). The fit rule must carry a
    cutoff day STRICTLY before the season's first eval day and the
    report must self-certify leak-freedom with a positive training
    count -- the same leak-freeze the shipped table's provenance
    documents (league_strength.py, KAPPA docstring).
    """

    if not isinstance(report, Mapping):
        raise ValueError(
            f"kappa report must be a mapping, got {type(report).__name__}"
        )
    table = league_strength.kappa_from_report(report)
    fit_rule = report.get("fit_rule")
    if not isinstance(fit_rule, str):
        raise ValueError(
            f"kappa report 'fit_rule' must be the harness string "
            f"'obs kickoff day < YYYY-MM-DD', got {fit_rule!r}"
        )
    match = _FIT_RULE_RE.match(fit_rule)
    if match is None:
        raise ValueError(
            f"kappa report 'fit_rule' must be the harness string "
            f"'obs kickoff day < YYYY-MM-DD', got {fit_rule!r}"
        )
    cutoff = datetime.date.fromisoformat(match.group(1))
    boundary = _utc_day(season.first_eval_day)
    if not cutoff < boundary:
        raise ValueError(
            f"kappa report fit cutoff {cutoff.isoformat()} is not strictly "
            f"before season {season.season!r} first eval day "
            f"{boundary.isoformat()}; refusing a non-leak-free bind"
        )
    leak_free = report.get("leak_free")
    if leak_free is not True:
        raise ValueError(
            f"kappa report must carry leak_free=true, got {leak_free!r}"
        )
    n_train = report.get("n_train")
    if type(n_train) is not int or n_train <= 0:
        raise ValueError(
            f"kappa report 'n_train' must be a positive int, got {n_train!r}"
        )
    haircut = _finite_number(report.get("away_haircut"), "'away_haircut'")
    if haircut < 0.0:
        raise ValueError(f"kappa report 'away_haircut' must be >= 0, got {haircut!r}")
    return table, haircut, fit_rule, True, n_train


def verify_in_force_freeze(
    frozen_records: Sequence[tuple[str, str]],
    observed_table: league_strength.LeagueStrengthTable | None = None,
) -> tuple[queue_policy.KappaFreezeCheck, ...]:
    """Re-check every in-force season's freeze against the observed table.

    The observed digest uses :func:`pipeline.bridge_fingerprint` --
    the SAME spelling the construction side records on candidates
    and ``decide_entry`` recomputes -- so optimizer summaries and
    scan evidence can be compared directly. ``observed_table``
    defaults to the production :data:`league_strength.KAPPA` (the
    in-force table a re-estimation must not disturb). Argument
    validation (non-empty season label / digests) is delegated to
    ``queue_policy.check_kappa_freeze``.
    """

    table = league_strength.KAPPA if observed_table is None else observed_table
    if not isinstance(table, league_strength.LeagueStrengthTable):
        raise TypeError(
            "observed_table must be a league_strength.LeagueStrengthTable, "
            f"got {type(table).__name__}"
        )
    digest = pipeline.bridge_fingerprint(table.kappa)
    return tuple(
        queue_policy.check_kappa_freeze(str(label), frozen, digest)
        for label, frozen in frozen_records
    )


@dataclasses.dataclass(frozen=True)
class RefreezeSummary:
    """The registration record of one re-estimation request.

    The in-process return value IS the record (review P3-3):
    MO-6's runner persists it (``papersim.optimization_runs`` /
    snapshot bytes); this module opens no connection and writes no
    file. Field pairing is enforced at construction:

    - ``PROPOSED``: a pre-season bind whose every in-force freeze
      check is MATCH; carries the full proposal (kappa table,
      ``away_haircut`` -- review P3-2 -- digests, fit provenance)
      and no reason. ``usable`` is True.
    - ``FREEZE_CHECK_FAILED``: the bind succeeded but at least one
      in-force season's freeze check is not MATCH; the proposal is
      retained as evidence and the reason quotes the failed check's
      ``blocked_reason``. ``usable`` is False.
    - ``REJECTED_MID_SEASON``: the request arrived in-season; NO
      proposal fields at all (nothing was bound), the refusal
      reason is mandatory and the freeze checks ride along as
      evidence. ``usable`` is False.
    """

    family_id: str
    season: str
    status: str
    requested_at: datetime.datetime
    first_eval_day: datetime.datetime
    reason: str | None = None
    away_haircut: float | None = None
    kappa: Mapping[str, float] | None = None
    table_sha256: str | None = None
    bound_spec_sha256: str | None = None
    fit_rule: str | None = None
    leak_free: bool | None = None
    n_train: int | None = None
    freeze_checks: tuple[queue_policy.KappaFreezeCheck, ...] = ()

    def __post_init__(self) -> None:
        if self.family_id != FAMILY_ID:
            raise ValueError(f"summary family_id must be {FAMILY_ID!r}")
        for name in ("requested_at", "first_eval_day"):
            if not isinstance(getattr(self, name), datetime.datetime):
                raise TypeError(f"{name} must be a datetime")
        if self.status not in _STATUSES:
            raise ValueError(f"unknown summary status {self.status!r}")
        if self.status == STATUS_REJECTED_MID_SEASON:
            if self.reason is None:
                raise ValueError("a rejection must carry its reason")
            for name in (*_PROPOSAL_FIELDS, "leak_free", "n_train"):
                if getattr(self, name) is not None:
                    raise ValueError(
                        f"a mid-season rejection carries no {name}: nothing "
                        "was bound"
                    )
        else:
            for name in _PROPOSAL_FIELDS:
                if getattr(self, name) is None:
                    raise ValueError(f"a {self.status} summary must carry {name}")
            if self.status == STATUS_PROPOSED and self.reason is not None:
                raise ValueError("a PROPOSED summary carries no reason")
            if self.status == STATUS_FREEZE_CHECK_FAILED:
                if self.reason is None:
                    raise ValueError("a freeze-check failure must carry its reason")
                if not any(
                    check.status != queue_policy.KAPPA_FREEZE_MATCH
                    for check in self.freeze_checks
                ):
                    raise ValueError(
                        "FREEZE_CHECK_FAILED requires at least one non-MATCH "
                        "freeze check"
                    )
        if self.kappa is not None:
            object.__setattr__(
                self, "kappa", freeze_value(dict(self.kappa))
            )

    @property
    def usable(self) -> bool:
        """True only for PROPOSED (mirrors ``KappaFreezeCheck.usable``)."""

        return self.status == STATUS_PROPOSED

    def as_dict(self) -> dict[str, object]:
        """JSON-ready view (dates ISO, checks as plain dicts)."""

        return {
            "family_id": self.family_id,
            "season": self.season,
            "status": self.status,
            "requested_at": self.requested_at.isoformat(),
            "first_eval_day": self.first_eval_day.isoformat(),
            "reason": self.reason,
            "away_haircut": self.away_haircut,
            "kappa": None if self.kappa is None else dict(self.kappa),
            "table_sha256": self.table_sha256,
            "bound_spec_sha256": self.bound_spec_sha256,
            "fit_rule": self.fit_rule,
            "leak_free": self.leak_free,
            "n_train": self.n_train,
            "freeze_checks": [
                {
                    "season": check.season,
                    "frozen_sha256": check.frozen_sha256,
                    "observed_sha256": check.observed_sha256,
                    "status": check.status,
                }
                for check in self.freeze_checks
            ],
        }


def request_refreeze(
    season: LeagueSeason,
    as_of: datetime.datetime,
    report: Mapping[str, object],
    spec: ModelSpec | None = None,
    frozen_records: Sequence[tuple[str, str]] = (),
    observed_table: league_strength.LeagueStrengthTable | None = None,
) -> RefreezeSummary:
    """One re-estimation request -> one summary record (the orchestrator).

    Order of operations (the reviewed plan's two ``check_kappa_
    freeze`` call points): the gate first -- a mid-season request is
    rejected with a record and never reads the report; on a pass,
    ``fit`` binds the report and ``verify_in_force_freeze`` proves
    the in-force seasons were not disturbed. ``spec`` defaults to
    the current baseline (binding the production table verbatim);
    a genuine proposal passes its own spec. Pure: same inputs give
    the same summary, no clock, file or connection.
    """

    gate = reevaluation_gate(season, as_of)
    if not gate.allowed:
        return RefreezeSummary(
            family_id=FAMILY_ID,
            season=season.season,
            status=STATUS_REJECTED_MID_SEASON,
            requested_at=as_of,
            first_eval_day=season.first_eval_day,
            reason=gate.reason,
            freeze_checks=verify_in_force_freeze(frozen_records, observed_table),
        )
    bound_spec = LEAGUE_BRIDGE.baseline_spec() if spec is None else spec
    fitted = LEAGUE_BRIDGE.fit(
        LeagueBridgeHistory(report=report, season=season), bound_spec, as_of
    )
    checks = verify_in_force_freeze(frozen_records, observed_table)
    common: dict[str, object] = {
        "family_id": FAMILY_ID,
        "season": season.season,
        "requested_at": as_of,
        "first_eval_day": season.first_eval_day,
        "away_haircut": fitted.away_haircut,
        "kappa": dict(fitted.table.kappa),
        "table_sha256": pipeline.bridge_fingerprint(fitted.table.kappa),
        "bound_spec_sha256": fitted.spec_sha256,
        "fit_rule": fitted.fit_rule,
        "leak_free": fitted.leak_free,
        "n_train": fitted.n_train,
        "freeze_checks": checks,
    }
    failed = next(
        (
            check
            for check in checks
            if check.status != queue_policy.KAPPA_FREEZE_MATCH
        ),
        None,
    )
    if failed is not None:
        return RefreezeSummary(
            status=STATUS_FREEZE_CHECK_FAILED,
            reason=f"freeze verification failed: {failed.blocked_reason}",
            **common,
        )
    return RefreezeSummary(status=STATUS_PROPOSED, **common)


class LeagueBridgeFamily:
    """``ModelFamily`` implementation of the frozen strength table."""

    family_id = FAMILY_ID
    procedure_version = PROCEDURE_VERSION

    def param_space(self) -> tuple[ParamDef, ...]:
        """Nothing to search (plan 05 row 5e); re-estimation is not search."""

        return ()

    def baseline_spec(self) -> ModelSpec:
        """The in-force table as a spec: constants referenced, not copied.

        An owner re-freeze therefore changes the baseline identity
        (a new season's table IS a new identity -- intended
        semantics, review P3-5); with no lineages there is no
        registry init conflict. No legacy version string: the
        bridge is not a queue-partition model version (queue
        identity stays with the club-soccer families it feeds).
        """

        return ModelSpec(
            family=FAMILY_ID,
            procedure_version=PROCEDURE_VERSION,
            params={
                "kappa": dict(league_strength.KAPPA.kappa),
                "away_haircut": league_strength.AWAY_HAIRCUT,
            },
            envelope=(),
            data_contract=DATA_CONTRACT,
        )

    def targets(self) -> tuple[str, ...]:
        return TARGETS

    def fit(
        self,
        history: LeagueBridgeHistory,
        spec: ModelSpec,
        as_of: datetime.datetime,
    ) -> LeagueBridgeFitted:
        """Bind one report artifact to one spec, pre-season only.

        Defense in depth: even a caller that bypasses
        :func:`reevaluation_gate` cannot bind mid-season -- ``as_of``
        on/after the season's first eval day is refused here as
        well. The report's estimate must equal the spec's table
        exactly (identity and behavior must not split, the nba
        family's envelope rule).
        """

        if not isinstance(history, LeagueBridgeHistory):
            raise TypeError(
                f"{FAMILY_ID}.fit expects LeagueBridgeHistory, "
                f"got {type(history).__name__}"
            )
        if not isinstance(spec, ModelSpec):
            raise TypeError(
                f"{FAMILY_ID}.fit expects ModelSpec, got {type(spec).__name__}"
            )
        if not isinstance(as_of, datetime.datetime):
            raise TypeError(
                f"{FAMILY_ID}.fit expects a datetime as_of, "
                f"got {type(as_of).__name__}"
            )
        params = _validated_params(spec)
        season = history.season
        boundary = _utc_day(season.first_eval_day)
        if _utc_day(as_of) >= boundary:
            raise ValueError(
                f"{FAMILY_ID}: mid-season bind refused for season "
                f"{season.season!r} (first eval day {boundary.isoformat()}, "
                f"as_of {_utc_day(as_of).isoformat()}); re-freeze only "
                "before the season starts"
            )
        table, haircut, fit_rule, leak_free, n_train = _report_facts(
            history.report, season
        )
        spec_kappa = {
            str(key): float(value) for key, value in params["kappa"].items()
        }
        if dict(table.kappa) != spec_kappa:
            raise ValueError(
                f"{FAMILY_ID}: report kappa != spec kappa; identity and "
                "behavior must not split"
            )
        if haircut != float(params["away_haircut"]):
            raise ValueError(
                f"{FAMILY_ID}: report away_haircut {haircut!r} != spec "
                f"away_haircut {params['away_haircut']!r}"
            )
        return LeagueBridgeFitted(
            table=table,
            away_haircut=haircut,
            spec_sha256=spec.spec_sha256(),
            fit_rule=fit_rule,
            leak_free=leak_free,
            n_train=n_train,
        )

    def predict(
        self, fitted: LeagueBridgeFitted, fixture: LeagueBridgeFixture
    ) -> tuple[float, float] | None:
        """Bridged rates for one fixture; a pure passthrough of ``xg``.

        Delegates to :func:`xg.pair_lambdas` with
        ``bridge=fitted.table.kappa`` (review P2): the clamp, the
        tilt (home ``* f``, away ``/ f``) and the away haircut
        BEFORE the tilt division live in exactly one place, and
        ``None`` propagates unchanged (unknown league, missing
        blocks, the structural guards). A fitted haircut that
        differs from ``league_strength.AWAY_HAIRCUT`` is an unshipped
        proposal the frozen xg path cannot express -- refused here
        rather than re-derived.
        """

        if not isinstance(fitted, LeagueBridgeFitted):
            raise TypeError(
                f"{FAMILY_ID}.predict expects LeagueBridgeFitted, "
                f"got {type(fitted).__name__}"
            )
        if not isinstance(fixture, LeagueBridgeFixture):
            raise TypeError(
                f"{FAMILY_ID}.predict expects LeagueBridgeFixture, "
                f"got {type(fixture).__name__}"
            )
        if fitted.away_haircut != league_strength.AWAY_HAIRCUT:
            raise ValueError(
                f"{FAMILY_ID}.predict: unshipped proposal haircut "
                f"{fitted.away_haircut!r}; the frozen xg path applies "
                f"league_strength.AWAY_HAIRCUT=={league_strength.AWAY_HAIRCUT!r} "
                "-- shipping a new haircut is an owner-authorized constant "
                "change, not a family-side re-derivation (review P2)"
            )
        return xg.pair_lambdas(
            fixture.snapshot,
            fixture.home_team,
            fixture.home_league,
            fixture.away_team,
            fixture.away_league,
            last_weight=fixture.last_weight,
            prior_games=fixture.prior_games,
            mode=fixture.mode,
            shrink=fixture.shrink,
            season=fixture.season,
            bridge=fitted.table.kappa,
        )


#: Module-level singleton family instance.
LEAGUE_BRIDGE = LeagueBridgeFamily()
