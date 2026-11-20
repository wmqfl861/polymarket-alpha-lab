"""MO-8 promotion policy kernel: the versioned judgement rules (pure).

Design ``docs/handoffs/2026-11-19-audit-optimizer/04-optimizer-design.md``
sections 5/6 and plan 05 node MO-8; the registered numbers are frozen
verbatim in ``DELIVERY_PLAN.md`` section 80.7 (registered 2026-11-20,
OD-J).  This module is the ONE place the rule numbers live: every
threshold, interval level and window length below is part of the
versioned rule -- changing any of them means a new ``RULE_VERSION``
and a fresh registration, never an in-place edit.

Pure: standard library only, plus the evaluation module's frozen
cluster bootstrap through :func:`decide` inputs already computed by
the caller.  No I/O, no network, no database driver, no clock reads
(every timestamp arrives as an explicit argument), and no import of
``papersim.store``/``store_psycopg``/``provider`` -- the CLI assembles
:class:`LineageFacts` and :class:`ForwardStats` from the database and
calls the judgement functions here.

Conclusions are exactly the seven closed tokens of
``papersim.store.DECISION_CONCLUSIONS`` (mirrored by the database
CHECK on ``papersim.promotion_decisions``); block reasons are fixed
``papersim_policy_block_*`` codes, never free text.

Look semantics (plan v2, review P2-1): ``look_number`` is ONLY the
idempotency-key counter -- one per written decision row for a
(lineage, class, candidate, rule_version), BLOCKED rows included.  The
interval level is chosen by ``evidence_ordinal`` -- the count of prior
NON-BLOCKED evidence judgements -- so a frozen look cannot silently
loosen the first evidence judgement from 99% back to 95%: the first
time the rule actually sees evidence it uses the tightened 99%
interval (``DELIVERY_PLAN`` 80.7 "第一次判时把区间从 95% 收紧到 99%" =
the first EVIDENCE look), the second and final one uses 95%.

Freeze and shadow expiry (review P3-8, pinned interpretation): a
freeze does NOT exempt expiry retirement -- a candidate whose
pre-registered shadow window has run out is retired even while the
optimizer is frozen ("到期没达标自动退役", design section 5 measure 7);
the retirement batch changes no active, so the freeze invariant
"已有的现役保持不变" holds.  All other conclusions under freeze are
BLOCKED with the frozen code.
"""

from __future__ import annotations

import datetime as _datetime
from collections.abc import Mapping
from dataclasses import dataclass, field

__all__ = [
    "BLOCK_REASON_CODES",
    "BLOCKED_FROZEN",
    "BLOCKED_HANDOVER_WINDOW",
    "BLOCKED_INTEGRITY",
    "BLOCKED_LINEAGE_PAUSED",
    "BLOCKED_SEASON_FROZEN",
    "CONCLUSION_BLOCKED",
    "CONCLUSION_CONTINUE",
    "CONCLUSION_HOLD",
    "CONCLUSION_PROMOTE",
    "CONCLUSION_PROMOTE_PENDING_CONFIRM",
    "CONCLUSION_RETIRE",
    "CONCLUSION_ROLLBACK",
    "FIRST_EVIDENCE_CI_PERCENTILES",
    "ForwardStats",
    "ForwardThreshold",
    "FORWARD_THRESHOLDS",
    "HANDOVER_WINDOW_DAYS",
    "LineageFacts",
    "PROMOTE_CI_UPPER_MAX",
    "PROMOTE_PAIRED_MEAN_MAX",
    "OWNER_CONFIRM_QUEUE_ENTRIES",
    "RULE_REGISTERED",
    "RULE_VERSION",
    "SECOND_EVIDENCE_CI_PERCENTILES",
    "SECOND_LOOK_SAMPLE_MULTIPLIER",
    "CLOSE_DEVIATION_MIN_GAP",
    "CLOSE_DEVIATION_MIN_MARKETS",
    "ENVELOPE_MIN_RATIO",
    "INTL_WINDOW_GAP_DAYS",
    "STAGE1_HOLDOUT_MIN_BLOCKS",
    "STAGE1_HOLDOUT_MIN_MARKETS",
    "Verdict",
    "classify_threshold",
    "complete_matchweek_blocks",
    "decide_forward",
    "decide_rollback",
    "forward_look_due",
    "intl_window_blocks",
    "iso_matchweek_key",
    "rollback_look_due",
    "span_days_of",
]

# ---------------------------------------------------------------------------
# Rule identity
# ---------------------------------------------------------------------------

#: The versioned rule identity; the ``decided_by``/``actor`` prefix of
#: every automatic decision and role event this module produces.
RULE_VERSION = "promo-rule-v1"

#: The registration date of the numbers below (DELIVERY_PLAN 80.7).
RULE_REGISTERED = "2026-11-20"

# ---------------------------------------------------------------------------
# Registered numbers (DELIVERY_PLAN 80.7, frozen 2026-11-20)
# ---------------------------------------------------------------------------

#: 配对差（候选减现役）均值 ≤ 0（含 0；负数候选更好）。
PROMOTE_PAIRED_MEAN_MAX = 0.0

#: 95% 区间上界 ≤ +0.005（含）。
PROMOTE_CI_UPPER_MAX = 0.005

#: 第一次证据判把区间从 95% 收紧到 99%（percentile pair; the frozen
#: bootstrap draws themselves are unchanged -- evaluation lane exposes
#: the parameterized extraction).
FIRST_EVIDENCE_CI_PERCENTILES = (0.5, 99.5)

#: 第二次（最后一次）证据判用 95%。
SECOND_EVIDENCE_CI_PERCENTILES = (2.5, 97.5)

#: 第一次没过：样本翻倍时判第二次（也是最后一次）。
SECOND_LOOK_SAMPLE_MULTIPLIER = 2

#: "候选会下注的那些市场"的收盘价偏离均值不比现役低 1 美分以上：
#: gap（候选均值 − 现役均值）≤ −0.01 即拦截（"以上"含本数，钉死解释）。
CLOSE_DEVIATION_MIN_GAP = -0.01

#: 这类市场不足 30 个时只标注，不拦截。
CLOSE_DEVIATION_MIN_MARKETS = 30

#: 现役所在的队列累计 30 笔以上入场（含 30）→"晋级，待确认"。
OWNER_CONFIRM_QUEUE_ENTRIES = 30

#: 距上次换届不足 28 天（< 28 拦截；换回不重置窗口——裁定①）。
HANDOVER_WINDOW_DAYS = 28

#: 登记门（第一阶段，MO-6 消费）：候选包络离散度中位数不得低于现役
#: 的 0.8 倍。数字的唯一出处仍在此模块。
ENVELOPE_MIN_RATIO = 0.8

#: 第一次判的留出期下限（第一阶段，MO-6 消费）：登记行样本数口径。
STAGE1_HOLDOUT_MIN_MARKETS = 300
STAGE1_HOLDOUT_MIN_BLOCKS = 20


@dataclass(frozen=True)
class ForwardThreshold:
    """One family/target row of the 80.7 sample table (versioned).

    ``min_markets``/``min_complete_blocks``/``min_span_days`` are the
    FIRST evidence look's forward minimum (None = the row sets no such
    condition); the second look fires at ``SECOND_LOOK_SAMPLE_MULTIPLIER
    * min_markets``.  ``max_shadow_days`` is the longest shadow period
    in days, converted from the registered calendar units (12 周 = 84,
    10 周 = 70, 12 个月 = 365, 6 个月 = 182; the conversion is part of
    the versioned rule).
    """

    min_markets: int
    max_shadow_days: int
    family_class: str
    min_complete_blocks: int | None = None
    min_span_days: int | None = None


_OU = ForwardThreshold(min_markets=150, max_shadow_days=84,
                       family_class="club-soccer-ou", min_complete_blocks=2)
_WDL = ForwardThreshold(min_markets=300, max_shadow_days=84,
                        family_class="club-soccer-wdl", min_complete_blocks=2)
_NBA = ForwardThreshold(min_markets=120, max_shadow_days=70,
                        family_class="nba", min_span_days=14)
_INTL = ForwardThreshold(min_markets=60, max_shadow_days=365,
                         family_class="intl", min_complete_blocks=2)
_CRYPTO = ForwardThreshold(min_markets=60, max_shadow_days=182,
                           family_class="crypto", min_span_days=28)

#: The full (family_id, target) → threshold mapping.  Unregistered
#: combinations fail :func:`classify_threshold` -- never a guessed row.
FORWARD_THRESHOLDS: Mapping[tuple[str, str], ForwardThreshold] = {
    ("club-soccer-frozen", "over_under"): _OU,
    ("club-soccer-frozen", "btts"): _OU,
    ("club-soccer-frozen", "team_total"): _OU,
    ("club-soccer-recalib", "over_under"): _OU,
    ("club-soccer-recalib", "match_winner"): _WDL,
    ("nba-pace-efficiency", "moneyline"): _NBA,
    ("nba-pace-efficiency", "totals"): _NBA,
    ("intl-elo-poisson", "match_winner"): _INTL,
    ("intl-elo-poisson", "over_under"): _INTL,
    ("intl-elo-goal-residuals", "match_winner"): _INTL,
    ("intl-elo-goal-residuals", "over_under"): _INTL,
    ("crypto-gbm", "touch"): _CRYPTO,
    ("crypto-gbm", "terminal"): _CRYPTO,
}

# ---------------------------------------------------------------------------
# Conclusions and block reasons (fixed codes only)
# ---------------------------------------------------------------------------

CONCLUSION_PROMOTE = "PROMOTE"
CONCLUSION_PROMOTE_PENDING_CONFIRM = "PROMOTE_PENDING_CONFIRM"
CONCLUSION_CONTINUE = "CONTINUE"
CONCLUSION_RETIRE = "RETIRE"
CONCLUSION_ROLLBACK = "ROLLBACK"
CONCLUSION_HOLD = "HOLD"
CONCLUSION_BLOCKED = "BLOCKED"

BLOCKED_LINEAGE_PAUSED = "papersim_policy_block_lineage_paused"
BLOCKED_FROZEN = "papersim_policy_block_frozen"
BLOCKED_HANDOVER_WINDOW = "papersim_policy_block_handover_window"
BLOCKED_SEASON_FROZEN = "papersim_policy_block_season_frozen_params"
BLOCKED_INTEGRITY = "papersim_policy_block_integrity_gate"

#: Every fixed block code (closed set; mirrors the hard-block row of 80.7).
BLOCK_REASON_CODES = (
    BLOCKED_LINEAGE_PAUSED,
    BLOCKED_FROZEN,
    BLOCKED_HANDOVER_WINDOW,
    BLOCKED_SEASON_FROZEN,
    BLOCKED_INTEGRITY,
)

#: 国际比赛窗口的聚簇规则：相邻事件日间隔超过该天数即开新窗口
#: （版本化规则的一部分）。
INTL_WINDOW_GAP_DAYS = 7


# ---------------------------------------------------------------------------
# Inputs (assembled by the CLI; pure data)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineageFacts:
    """The lineage-level facts one judgement runs on (no clock reads).

    Every field is assembled from recorded evidence (registry fold,
    control-event fold, queue latch bindings, entry counts, scan
    receipts); nothing here is computed from the writing moment.
    """

    lineage: str
    active_spec: str
    guard_spec: str | None = None
    candidate_specs: tuple[str, ...] = ()
    candidate_registered_at: Mapping[str, _datetime.datetime] = field(
        default_factory=dict)
    #: 最近一次 PROMOTE 换届批的 effective_at；换回批不重置窗口（裁定①）。
    last_handover_at: _datetime.datetime | None = None
    frozen: bool = False
    lineage_paused_or_stopped: bool = False
    active_queue_entries: int = 0
    touches_season_frozen_params: bool = False
    integrity_ok: bool = True
    #: 换届后窗口的完整性/验证门回归（ROLLBACK 类触发二，80.7 换回守卫行）。
    guard_integrity_regression: bool = False
    guard_validation_gate_regression: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.lineage, str) or not self.lineage:
            raise ValueError("papersim_policy_lineage_invalid")
        for name in (self.active_spec,):
            if not isinstance(name, str) or len(name) != 64:
                raise ValueError("papersim_policy_spec_invalid")
        if self.guard_spec is not None \
                and (not isinstance(self.guard_spec, str)
                     or len(self.guard_spec) != 64):
            raise ValueError("papersim_policy_spec_invalid")
        for spec in self.candidate_specs:
            if not isinstance(spec, str) or len(spec) != 64:
                raise ValueError("papersim_policy_spec_invalid")
        if type(self.active_queue_entries) is not int \
                or self.active_queue_entries < 0:
            raise ValueError("papersim_policy_entries_invalid")
        for stamp in self.candidate_registered_at.values():
            if not isinstance(stamp, _datetime.datetime) \
                    or stamp.tzinfo is None:
                raise ValueError("papersim_policy_time_invalid")
        for stamp in (self.last_handover_at,):
            if stamp is not None and (not isinstance(stamp, _datetime.datetime)
                                      or stamp.tzinfo is None):
                raise ValueError("papersim_policy_time_invalid")


@dataclass(frozen=True)
class ForwardStats:
    """One pair's forward statistics (recomputable, clock-free).

    ``spec`` is the judged candidate (FORWARD) or the new active
    (ROLLBACK); ``comparator`` is the incumbent active (FORWARD) or
    the guard (ROLLBACK).  ``paired_mean``/``ci95``/``ci99`` are the
    cluster-bootstrap paired differences "spec minus comparator"
    (negative favours the judged spec).  ``close_dev_gap`` is
    candidate's mean |close − probability| minus the incumbent's on
    the would-bet subset (None = no computable deviation: annotated,
    never a hard block -- completeness belongs to ``integrity_ok``).
    """

    spec: str
    comparator: str
    n_markets: int = 0
    n_event_clusters: int = 0
    n_complete_blocks: int = 0
    span_days: int = 0
    watermark: _datetime.datetime | None = None
    paired_mean: float | None = None
    ci95: tuple[float | None, float | None] = (None, None)
    ci99: tuple[float | None, float | None] = (None, None)
    close_dev_markets: int = 0
    close_dev_gap: float | None = None
    unpaired: int = 0

    def __post_init__(self) -> None:
        for name in (self.spec, self.comparator):
            if not isinstance(name, str) or len(name) != 64:
                raise ValueError("papersim_policy_spec_invalid")
        for name in (self.n_markets, self.n_event_clusters,
                     self.n_complete_blocks, self.close_dev_markets,
                     self.unpaired):
            if type(name) is not int or name < 0:
                raise ValueError("papersim_policy_count_invalid")
        if type(self.span_days) is not int or self.span_days < 0:
            raise ValueError("papersim_policy_span_invalid")
        if self.watermark is not None \
                and (not isinstance(self.watermark, _datetime.datetime)
                     or self.watermark.tzinfo is None):
            raise ValueError("papersim_policy_time_invalid")
        for bound in (self.paired_mean, self.close_dev_gap):
            if bound is not None and type(bound) is not float:
                raise ValueError("papersim_policy_number_invalid")


@dataclass(frozen=True)
class Verdict:
    """One judgement's outcome (canonical, JSON-ready)."""

    conclusion: str
    look_number: int
    evidence_ordinal: int
    rule_version: str = RULE_VERSION
    block_reason: str | None = None
    interval_level: str | None = None
    metrics: dict = field(default_factory=dict)
    evidence_watermark: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.conclusion not in (CONCLUSION_PROMOTE,
                                   CONCLUSION_PROMOTE_PENDING_CONFIRM,
                                   CONCLUSION_CONTINUE, CONCLUSION_RETIRE,
                                   CONCLUSION_ROLLBACK, CONCLUSION_HOLD,
                                   CONCLUSION_BLOCKED):
            raise ValueError("papersim_policy_conclusion_invalid")
        if (self.conclusion == CONCLUSION_BLOCKED) \
                != (self.block_reason is not None):
            raise ValueError("papersim_policy_block_reason_invalid")
        if self.block_reason is not None \
                and self.block_reason not in BLOCK_REASON_CODES:
            raise ValueError("papersim_policy_block_reason_invalid")
        if type(self.look_number) is not int or self.look_number < 1:
            raise ValueError("papersim_policy_look_invalid")
        if type(self.evidence_ordinal) is not int or self.evidence_ordinal < 0:
            raise ValueError("papersim_policy_ordinal_invalid")


# ---------------------------------------------------------------------------
# Block/span derivations (versioned with the rule)
# ---------------------------------------------------------------------------


def iso_matchweek_key(start: _datetime.datetime) -> tuple[int, int]:
    """The ISO (year, week) of one event start (UTC)."""
    if not isinstance(start, _datetime.datetime) or start.tzinfo is None:
        raise ValueError("papersim_policy_time_invalid")
    moment = start.astimezone(_datetime.UTC)
    iso = moment.isocalendar()
    return (int(iso[0]), int(iso[1]))


def complete_matchweek_blocks(starts, watermark: _datetime.datetime) -> int:
    """Count matchweek blocks whose whole ISO week ended before the watermark.

    A block is COMPLETE when the ISO week containing its events has
    fully passed the watermark (the Monday 00:00 UTC of that week plus
    seven days is at or before the watermark) -- "跨不少于 2 个完整
    比赛周" counts only finished weeks, never the running one.
    """
    if not isinstance(watermark, _datetime.datetime) or watermark.tzinfo is None:
        raise ValueError("papersim_policy_time_invalid")
    weeks = set()
    for start in starts:
        year, week = iso_matchweek_key(start)
        monday = _datetime.datetime.fromisocalendar(
            year, week, 1).replace(tzinfo=_datetime.UTC)
        weeks.add((year, week, monday))
    return len({(year, week) for year, week, monday in weeks
                if monday + _datetime.timedelta(days=7) <= watermark})


def intl_window_blocks(starts) -> int:
    """Cluster event days into international windows (gap rule).

    Distinct UTC event days, sorted; a gap strictly greater than
    ``INTL_WINDOW_GAP_DAYS`` days between consecutive event days
    starts a new window ("跨不少于 2 个国际比赛窗口"; the gap rule is
    part of the versioned rule).
    """
    days = sorted({start.astimezone(_datetime.UTC).date() for start in starts})
    if not days:
        return 0
    blocks = 1
    for previous, current in zip(days, days[1:]):
        if (current - previous).days > INTL_WINDOW_GAP_DAYS:
            blocks += 1
    return blocks


def span_days_of(starts) -> int:
    """Whole days between the earliest and latest event start."""
    moments = [start.astimezone(_datetime.UTC) for start in starts]
    if not moments:
        return 0
    return (max(moments) - min(moments)).days


# ---------------------------------------------------------------------------
# Scheduling (pure predicates on the stats)
# ---------------------------------------------------------------------------


def _sample_conditions_met(stats: ForwardStats, threshold: ForwardThreshold,
                           markets: int) -> bool:
    if stats.n_markets < markets:
        return False
    if threshold.min_complete_blocks is not None \
            and stats.n_complete_blocks < threshold.min_complete_blocks:
        return False
    if threshold.min_span_days is not None \
            and stats.span_days < threshold.min_span_days:
        return False
    return True


def forward_look_due(stats: ForwardStats, threshold: ForwardThreshold,
                     evidence_ordinal: int) -> bool:
    """Is an evidence look due?  ordinal 0 at the minimum, ordinal 1 at
    double; no third evidence look ever (the shadow-expiry retirement
    is a separate path inside :func:`decide_forward`)."""
    if evidence_ordinal == 0:
        return _sample_conditions_met(stats, threshold, threshold.min_markets)
    if evidence_ordinal == 1:
        return _sample_conditions_met(
            stats, threshold,
            threshold.min_markets * SECOND_LOOK_SAMPLE_MULTIPLIER)
    return False


def rollback_look_due(stats: ForwardStats, threshold: ForwardThreshold) -> bool:
    """The scheduled ROLLBACK look: new-active-vs-guard sample at the
    lineage's own forward minimum (one evidence look per guard)."""
    return _sample_conditions_met(stats, threshold, threshold.min_markets)


def classify_threshold(family_id: str, target: str) -> ForwardThreshold:
    """The threshold row of one (family, target); unregistered fails."""
    row = FORWARD_THRESHOLDS.get((family_id, target))
    if row is None:
        raise ValueError("papersim_policy_threshold_unknown")
    return row


# ---------------------------------------------------------------------------
# The judgement functions
# ---------------------------------------------------------------------------


def _number(value: float | None) -> str | None:
    return None if value is None else repr(value)


def _watermark_dict(stats: ForwardStats) -> dict:
    return {
        "n_complete_blocks": stats.n_complete_blocks,
        "n_event_clusters": stats.n_event_clusters,
        "n_markets": stats.n_markets,
        "span_days": stats.span_days,
        "spec": stats.spec,
        "comparator": stats.comparator,
        "unpaired": stats.unpaired,
        "watermark": None if stats.watermark is None
        else stats.watermark.isoformat(),
    }


def _forward_metrics(stats: ForwardStats, facts: LineageFacts,
                     upper: float | None, level: str,
                     close_flagged: bool, close_blocked: bool) -> dict:
    return {
        "active_queue_entries": facts.active_queue_entries,
        "ci_applied_level": level,
        "ci95_lower": _number(stats.ci95[0]),
        "ci95_upper": _number(stats.ci95[1]),
        "ci99_lower": _number(stats.ci99[0]),
        "ci99_upper": _number(stats.ci99[1]),
        "close_dev_flagged_only": close_flagged,
        "close_dev_gap": _number(stats.close_dev_gap),
        "close_dev_markets": stats.close_dev_markets,
        "close_dev_violation": close_blocked,
        "interval_upper_applied": _number(upper),
        "paired_mean": _number(stats.paired_mean),
    }


def _close_deviation_check(stats: ForwardStats) -> tuple[bool, bool, bool]:
    """(ok, flagged_only, violation) on the close-price deviation rule.

    Under :data:`CLOSE_DEVIATION_MIN_MARKETS` would-bet markets (or a
    None gap) the condition is ANNOTATED, never blocking; at or above
    the minimum a gap at or below :data:`CLOSE_DEVIATION_MIN_GAP`
    blocks ("低 1 美分以上"含本数).
    """
    if stats.close_dev_markets < CLOSE_DEVIATION_MIN_MARKETS \
            or stats.close_dev_gap is None:
        return (True, True, False)
    blocked = stats.close_dev_gap <= CLOSE_DEVIATION_MIN_GAP
    return (not blocked, False, blocked)


def _forward_hard_block(facts: LineageFacts,
                        as_of: _datetime.datetime) -> str | None:
    if facts.lineage_paused_or_stopped:
        return BLOCKED_LINEAGE_PAUSED
    if facts.frozen:
        return BLOCKED_FROZEN
    if facts.last_handover_at is not None:
        window = _datetime.timedelta(days=HANDOVER_WINDOW_DAYS)
        if as_of - facts.last_handover_at < window:
            return BLOCKED_HANDOVER_WINDOW
    if facts.touches_season_frozen_params:
        return BLOCKED_SEASON_FROZEN
    if not facts.integrity_ok:
        return BLOCKED_INTEGRITY
    return None


def decide_forward(facts: LineageFacts, stats: ForwardStats, *,
                   look_number: int, evidence_ordinal: int,
                   threshold: ForwardThreshold,
                   as_of: _datetime.datetime) -> Verdict:
    """One FORWARD evidence look of one candidate against the active.

    Order of evaluation (plan v2): shadow expiry FIRST (a freeze does
    not exempt expiry retirement), then the five hard blocks (BLOCKED
    consumes the look key but NOT the evidence ordinal), then the
    promotion conditions with the interval chosen by evidence_ordinal.
    """
    if stats.spec not in facts.candidate_registered_at:
        raise ValueError("papersim_policy_candidate_unregistered")
    base = {"look_number": look_number,
            "evidence_ordinal": evidence_ordinal}

    # 1. Shadow expiry (P3-8: fires even while frozen).
    registered_at = facts.candidate_registered_at[stats.spec]
    if stats.watermark is not None and stats.watermark - registered_at > \
            _datetime.timedelta(days=threshold.max_shadow_days):
        return Verdict(
            conclusion=CONCLUSION_RETIRE, block_reason=None,
            interval_level=None,
            metrics={"reason": "shadow_window_expired",
                     "max_shadow_days": threshold.max_shadow_days},
            evidence_watermark=_watermark_dict(stats), **base)

    # 2. Hard blocks.
    reason = _forward_hard_block(facts, as_of)
    if reason is not None:
        return Verdict(
            conclusion=CONCLUSION_BLOCKED, block_reason=reason,
            interval_level=None, metrics={},
            evidence_watermark=_watermark_dict(stats), **base)

    # 3. Promotion conditions; interval by EVIDENCE ordinal (P2-1).
    if evidence_ordinal == 0:
        level, upper = "ci99", stats.ci99[1]
    elif evidence_ordinal == 1:
        level, upper = "ci95", stats.ci95[1]
    else:
        raise ValueError("papersim_policy_ordinal_invalid")
    close_ok, close_flagged, close_blocked = _close_deviation_check(stats)
    mean_ok = stats.paired_mean is not None \
        and stats.paired_mean <= PROMOTE_PAIRED_MEAN_MAX
    upper_ok = upper is not None and upper <= PROMOTE_CI_UPPER_MAX
    metrics = _forward_metrics(stats, facts, upper, level,
                               close_flagged, close_blocked)
    if mean_ok and upper_ok and close_ok:
        if facts.active_queue_entries >= OWNER_CONFIRM_QUEUE_ENTRIES:
            conclusion = CONCLUSION_PROMOTE_PENDING_CONFIRM
        else:
            conclusion = CONCLUSION_PROMOTE
        return Verdict(conclusion=conclusion, block_reason=None,
                       interval_level=level, metrics=metrics,
                       evidence_watermark=_watermark_dict(stats), **base)
    if evidence_ordinal == 0:
        return Verdict(conclusion=CONCLUSION_CONTINUE, block_reason=None,
                       interval_level=level, metrics=metrics,
                       evidence_watermark=_watermark_dict(stats), **base)
    return Verdict(conclusion=CONCLUSION_RETIRE, block_reason=None,
                   interval_level=level, metrics=metrics,
                   evidence_watermark=_watermark_dict(stats), **base)


def decide_rollback(facts: LineageFacts, stats: ForwardStats, *,
                    look_number: int, threshold: ForwardThreshold,
                    as_of: _datetime.datetime) -> Verdict:
    """One ROLLBACK look of the new active against the guard.

    Only the optimizer freeze blocks a rollback look (a rollback is a
    safety action: the 28-day handover window explicitly excludes it,
    and pause/season-frozen/integrity are promotion-side conditions).
    Triggers: the guard-side integrity/validation regression (fires
    regardless of sample due-ness -- the caller checks it) or the 95%
    interval LOWER bound strictly above zero (新现役显著更差).
    """
    if facts.guard_spec is None:
        raise ValueError("papersim_policy_guard_absent")
    if facts.guard_spec != stats.comparator or facts.active_spec != stats.spec:
        raise ValueError("papersim_policy_rollback_pair_invalid")
    base = {"look_number": look_number, "evidence_ordinal": 0}
    if facts.frozen:
        return Verdict(conclusion=CONCLUSION_BLOCKED,
                       block_reason=BLOCKED_FROZEN, interval_level=None,
                       metrics={},
                       evidence_watermark=_watermark_dict(stats), **base)
    metrics = {
        "ci95_lower": _number(stats.ci95[0]),
        "ci95_upper": _number(stats.ci95[1]),
        "guard_integrity_regression": facts.guard_integrity_regression,
        "guard_validation_gate_regression":
            facts.guard_validation_gate_regression,
        "interval_lower_applied": _number(stats.ci95[0]),
        "paired_mean": _number(stats.paired_mean),
    }
    if facts.guard_integrity_regression \
            or facts.guard_validation_gate_regression:
        return Verdict(conclusion=CONCLUSION_ROLLBACK, block_reason=None,
                       interval_level="ci95", metrics=metrics,
                       evidence_watermark=_watermark_dict(stats), **base)
    lower = stats.ci95[0]
    if lower is not None and lower > 0.0:
        return Verdict(conclusion=CONCLUSION_ROLLBACK, block_reason=None,
                       interval_level="ci95", metrics=metrics,
                       evidence_watermark=_watermark_dict(stats), **base)
    return Verdict(conclusion=CONCLUSION_HOLD, block_reason=None,
                   interval_level="ci95", metrics=metrics,
                   evidence_watermark=_watermark_dict(stats), **base)
