"""FX-08 single entry point: :func:`decide_entry` over the frozen stack.

Single responsibility: assemble the frozen decision stack of the
papersim library -- book freshness, suspension policy, category fee
resolution, the FX-13 crypto direction precondition,
:func:`~polymarket_alpha_lab.papersim.gates.evaluate_candidate`, the
strict crossbridge evidence check plus
:func:`~polymarket_alpha_lab.papersim.gates.check_cross_league_mw`,
:func:`~polymarket_alpha_lab.papersim.risk_policy.held_fixture_add_allowed`,
the entry-derived sizing tier, the acceptance-notional cap, the team
cap and the bankroll/cash cover -- into ONE pure decision function
that collects, orders and records EVERY rejection reason for replay
(FX-08, 2026-11-12 audit findings P-1/P-13, M1 plan lane A; R06 fee
acceptance advice recorded in DELIVERY_PLAN.md 77.7).

Purity: no network, no database, no clock reads, no callbacks, no file
I/O. Time (``book_time``/``now``/``max_book_age_seconds``), positions
(``held_sides``, team exposure, bankroll state), the news record, the
RAW order book and the model/bridge evidence are explicit inputs.
Missing evidence is recorded as ``blocked`` -- never patched with 0, a
default category or a silently assumed direction -- while every other
computable rejection reason is still collected.

FX-13 precondition (audit P-8): crypto candidates must arrive as a raw
probability record. ``crypto_terminal`` markets take a
``TerminalProbability`` plus a REQUIRED ``yes_when`` in
``{"below", "above"}`` and are priced ONLY through
:func:`~polymarket_alpha_lab.papersim.crypto_model.conservative_edges_for_terminal`
(which reorients to the market's YES side internally via
``terminal_yes_probability``); ``crypto_touch`` markets take a
``TouchProbability`` straight through
:func:`~polymarket_alpha_lab.papersim.crypto_model.conservative_edges`
with NO terminal transform. A pre-transformed record, a caller-derived
model quote on a crypto market, a missing/invalid/conflicting
``yes_when``, or a touch/terminal mixture is rejected with its own
reason. The original (pre-transform) probability, the market kind,
``yes_when``, the transformed probabilities and the direction
provenance are all stored on the decision so replay recomputes the
same edges without trusting any caller-side transformation.

Fees (FX-06/R06): the fee category is an explicit input resolved via
:func:`~polymarket_alpha_lab.papersim.fees.taker_fee_rate` /
:func:`~polymarket_alpha_lab.papersim.fees.resolve_taker_fee_rate`;
a market-metadata rate that disagrees with the table rejects the
candidate, a zero rate is a valid value, and
``legacy_scan_fee_category`` is NEVER called on the new-entry path.

Paper boundary: this module decides PAPER candidacy only
(``paper_only``/``report_only``/``readonly``); it grants no execution
authorization and never touches an order.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from polymarket_alpha_lab.papersim import crypto_model, fees, gates, risk_policy
from polymarket_alpha_lab.papersim.crypto_model import (
    TerminalProbability,
    TouchProbability,
)
from polymarket_alpha_lab.papersim.gates import (
    BookWalk,
    GateDecision,
    GateReport,
    NewsCheck,
    SuspensionDecision,
)
from polymarket_alpha_lab.papersim.risk_policy import SizingTier

__all__ = [
    "BUY_SIDES",
    "CRYPTO_KINDS",
    "KIND_CRYPTO_TERMINAL",
    "KIND_CRYPTO_TOUCH",
    "MATCH_WINNER_KINDS",
    "PIPELINE_VERSION",
    "GATE_VERSION",
    "MODEL_VERSION_CRYPTO",
    "MODEL_VERSION_SOCCER",
    "STEP_ORDER",
    "CryptoEvidence",
    "EntryDecision",
    "EntryRequest",
    "StepResult",
    "bridge_fingerprint",
    "decide_entry",
    "policy_code_sha256",
]

# ---------------------------------------------------------------------------
# Version pins (queue-partition strings; FX-08 output requirement)
# ---------------------------------------------------------------------------

#: This module's decision-stack version. Changes only with owner-visible
#: pipeline behavior changes (step set/order or reason alphabet).
PIPELINE_VERSION = "papersim-pipeline-fx08-2026-11-14"

#: Gate-stack version: the frozen gate thresholds and the 2026-11-07
#: fee/haircut order centralization, the FX-06 category fee schedule
#: date, the 2026-11-07 risk-policy class constants and the FX-13
#: crypto direction fix.
GATE_VERSION = (
    "gates-2026-11-07"
    f"+fees-{fees.FEE_SCHEDULE_AS_OF}"
    "+risk-2026-11-07"
    "+crypto-fx13-2026-11-12"
)

#: Model lineage versions per evidence path.
MODEL_VERSION_SOCCER = "soccer-r23"
MODEL_VERSION_CRYPTO = "crypto-r19+fx13-2026-11-12"

# ---------------------------------------------------------------------------
# Market-kind vocabulary
# ---------------------------------------------------------------------------

KIND_CRYPTO_TOUCH = "crypto_touch"
KIND_CRYPTO_TERMINAL = "crypto_terminal"
CRYPTO_KINDS = frozenset({KIND_CRYPTO_TOUCH, KIND_CRYPTO_TERMINAL})

#: Match-winner aliases normalized by the entry point for sizing-class
#: derivation (mirrors the gates-internal alias set; ``gates``
#: normalizes independently on every call).
MATCH_WINNER_KINDS = frozenset({"match_winner", "1x2", "ml"})

#: Valid crypto buy directions (the YES/NO token being bought).
BUY_SIDES = ("yes", "no")

#: Valid terminal-market YES directions (FX-13).
YES_WHEN_VALUES = ("below", "above")

# ---------------------------------------------------------------------------
# Step order and statuses (canonical, recorded on every decision)
# ---------------------------------------------------------------------------

STEP_BOOK_FRESHNESS = "book_freshness"
STEP_SUSPENSION = "suspension_policy"
STEP_FEE = "fee_category"
STEP_UNDER_RISK = "under_risk_screen"
STEP_CRYPTO_DIRECTION = "crypto_direction"
STEP_GATE = "gate_evaluate"
STEP_CROSS_LEAGUE = "cross_league_mw"
STEP_HELD_FIXTURE = "held_fixture"
STEP_SIZING = "sizing"
STEP_ACTUAL_WALK = "actual_walk"
STEP_NOTIONAL_CAP = "notional_cap"
STEP_TEAM_CAP = "team_cap"
STEP_BANKROLL = "bankroll_cash"

#: Canonical step order (FX-08: freshness -> suspension -> fee ->
#: evaluate_candidate -> cross_league_mw -> held_fixture -> sizing ->
#: caps -> bankroll; the FX-13 precondition and the under-risk screen
#: must complete before the gate because they feed it).
STEP_ORDER: tuple[str, ...] = (
    STEP_BOOK_FRESHNESS,
    STEP_SUSPENSION,
    STEP_FEE,
    STEP_UNDER_RISK,
    STEP_CRYPTO_DIRECTION,
    STEP_GATE,
    STEP_CROSS_LEAGUE,
    STEP_HELD_FIXTURE,
    STEP_SIZING,
    STEP_ACTUAL_WALK,
    STEP_NOTIONAL_CAP,
    STEP_TEAM_CAP,
    STEP_BANKROLL,
)

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_BLOCKED = "blocked"
STATUS_ERROR = "error"

# ---------------------------------------------------------------------------
# Rejection-reason alphabet (stable ids; every id has at least one test)
# ---------------------------------------------------------------------------

REASON_STALE_BOOK = "stale_book"
REASON_BOOK_TIME_MISSING = "book_time_missing"
REASON_NOW_MISSING = "now_missing"
REASON_MAX_AGE_MISSING = "max_book_age_missing"
REASON_BOOK_TIME_IN_FUTURE = "book_time_in_future"
REASON_TIME_INCOMPARABLE = "time_inputs_incomparable"
REASON_SUSPENSION_INELIGIBLE = "suspension_ineligible"
REASON_FEE_CATEGORY_MISSING = "fee_category_missing"
REASON_FEE_CATEGORY_UNKNOWN = "fee_category_unknown"
REASON_FEE_RATE_CONFLICT = "fee_rate_conflict"
REASON_UNDER_RISK_INPUTS_MISSING = "under_risk_inputs_missing"
REASON_MODEL_EVIDENCE_MISSING = "model_evidence_missing"
REASON_MODEL_EVIDENCE_AMBIGUOUS = "model_evidence_ambiguous"
REASON_MODEL_EVIDENCE_MISMATCH = "model_evidence_mismatch"
REASON_PROBABILITY_TYPE_CONFLICT = "probability_type_conflict"
REASON_YES_WHEN_MISSING = "yes_when_missing"
REASON_YES_WHEN_INVALID = "yes_when_invalid"
REASON_YES_WHEN_CONFLICT = "yes_when_conflict"
REASON_TOUCH_TERMINAL_CONFLICT = "touch_terminal_conflict"
REASON_TERMINAL_PRETRANSFORMED = "terminal_record_pretransformed"
REASON_BUY_SIDE_MISSING = "buy_side_missing"
REASON_BUY_SIDE_INVALID = "buy_side_invalid"
REASON_BUY_SIDE_CONFLICT = "buy_side_conflict"
REASON_CRYPTO_ASKS_MISSING = "crypto_asks_missing"
REASON_CRYPTO_ASKS_INVALID = "crypto_asks_invalid"
REASON_CRYPTO_ASK_CONFLICT = "crypto_ask_conflict"
REASON_NEWS_MISSING = "news_missing"
REASON_CROSS_LEAGUE_MW_BLOCKED = "cross_league_mw_blocked"
REASON_CROSS_LEAGUE_LEAGUE_MISMATCH = "cross_league_league_mismatch"
REASON_BRIDGE_INVALID = "bridge_invalid"
REASON_BRIDGE_FINGERPRINT_MISSING = "bridge_fingerprint_missing"
REASON_BRIDGE_FINGERPRINT_MISMATCH = "bridge_fingerprint_mismatch"
REASON_HELD_FIXTURE_BLOCKED = "held_fixture_blocked"
REASON_TEAM_CAP_EXCEEDED = "team_cap_exceeded"
REASON_BANKROLL_INSUFFICIENT = "bankroll_insufficient"
REASON_NOTIONAL_CAP_EXCEEDED = "notional_cap_exceeded"
REASON_ACTUAL_WALK_INCOMPLETE = "actual_walk_incomplete"
REASON_STEP_ERROR = "step_error"

#: Gate-failure ids surfaced verbatim from
#: ``gates.evaluate_candidate`` (variant_spread, worst_edge, exec_edge,
#: liq, fill_250, entry_window, news_concordant) and from
#: ``gates.check_cross_league_mw`` (cross_league_bridge,
#: cross_league_worst_edge) join the same alphabet unchanged.
GATE_REASON_IDS: tuple[str, ...] = (
    "variant_spread",
    "worst_edge",
    "exec_edge",
    "liq",
    "fill_250",
    "entry_window",
    "news_concordant",
    "cross_league_bridge",
    "cross_league_worst_edge",
)

#: Money comparison tolerance (cents-scale deterministic arithmetic).
_MONEY_EPSILON = 1e-9

#: Tolerance for cross-checking a caller-supplied best ask against the
#: raw book's best level (both are 4-dp price ticks).
_ASK_MATCH_TOLERANCE = 1e-6


def policy_code_sha256() -> str:
    """Deterministic policy fingerprint of the decision stack.

    Pure digest over the decision-relevant frozen surface: this
    module's version pins, the canonical step order, the frozen gate
    thresholds and cross-league extra margin, the acceptance notional,
    the default team cap and the fee-schedule as-of date. It is NOT a
    VCS commit id; it changes iff the recorded policy surface changes,
    so two decisions with equal ``code_sha256`` ran the same policy
    constants and step order.
    """

    payload = {
        "pipeline_version": PIPELINE_VERSION,
        "gate_version": GATE_VERSION,
        "model_versions": [MODEL_VERSION_CRYPTO, MODEL_VERSION_SOCCER],
        "step_order": list(STEP_ORDER),
        "gates": dict(gates.GATES),
        "cross_league_worst_edge_min": gates.CROSS_LEAGUE_MW_WORST_EDGE_MIN,
        "acceptance_notional_usd": gates.DEFAULT_NOTIONAL_USD,
        "default_team_cap_usd": risk_policy.DEFAULT_TEAM_CAP_USD,
        "fee_schedule_as_of": fees.FEE_SCHEDULE_AS_OF,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def bridge_fingerprint(bridge: Mapping[str, Any]) -> str:
    """Stable SHA-256 fingerprint of one bridge kappa table.

    The construction side records this fingerprint on the candidate it
    builds; :func:`decide_entry` recomputes it over the bridge evidence
    handed in with the entry and rejects on mismatch, binding the entry
    to the bridge the candidate was actually constructed with.
    """

    canonical = json.dumps(
        {str(key): float(value) for key, value in sorted(bridge.items())},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntryRequest:
    """One explicit, immutable entry-decision request.

    Every field is caller-supplied evidence; the pipeline fills in
    nothing on its own. ``None`` on a required-evidence field surfaces
    as a ``blocked`` step with its own reason, never as a silent
    default. ``book`` is the RAW ask ladder of the token being bought
    (the acceptance walk and the actual-stake walk both walk it
    afresh; a caller-computed walk is never trusted).
    """

    # --- market identity / context --------------------------------------
    market_id: str
    question: str
    fixture: str
    market_kind: str
    side: str
    book: Sequence[Any]
    # --- fee evidence (FX-06/R06) ---------------------------------------
    fee_category: str | None = None
    market_rate: float | None = None
    # --- league / bridge evidence (strict crossbridge) -------------------
    cross_league: bool = False
    home_league: str | None = None
    away_league: str | None = None
    bridge: Mapping[str, Any] | None = None
    bridge_fingerprint: str | None = None
    # --- model evidence (soccer path) ------------------------------------
    model_quote: Any = None
    gamma_liquidity: float | None = None
    # --- model evidence (crypto path, FX-13) -----------------------------
    probability: TouchProbability | TerminalProbability | Any | None = None
    yes_when: str | None = None
    contract_direction: str | None = None
    buy_side: str | None = None
    yes_ask: float | None = None
    no_ask: float | None = None
    yes_token: str | None = None
    no_token: str | None = None
    buy_token: str | None = None
    # --- news / risk evidence --------------------------------------------
    news: NewsCheck | None = None
    under_risk: bool = False
    o25_hits: int | None = None
    line: float | None = None
    lambda_home: float | None = None
    lambda_away: float | None = None
    lineup_confirmation: float | None = None
    # --- portfolio state --------------------------------------------------
    bankroll: float = 0.0
    cash: float = 0.0
    reserved: float = 0.0
    held_sides: tuple[str, ...] = ()
    team: str | None = None
    team_committed: float = 0.0
    team_cap: float = risk_policy.DEFAULT_TEAM_CAP_USD
    # --- time evidence -----------------------------------------------------
    book_time: datetime.datetime | None = None
    now: datetime.datetime | None = None
    max_book_age_seconds: float | None = None


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StepResult:
    """One pipeline step's status, its own reasons and original result.

    ``status`` is ``pass`` / ``fail`` / ``blocked`` / ``error``;
    ``result`` carries the ORIGINAL underlying result object
    (``SuspensionDecision``, ``GateReport``, ``GateDecision``,
    ``ConservativeEdges``, ``SizingTier``, ``BookWalk``,
    ``CorrelationDecision`` or a plain value) unchanged; ``detail`` is
    human-readable evidence. A step blocked by an earlier blocked
    dependency carries ``reasons=()`` (the blocking reason already
    forces rejection) and names the blocker in ``detail``.
    """

    step: str
    status: str
    reasons: tuple[str, ...] = ()
    result: Any = None
    detail: str = ""


@dataclass(frozen=True)
class CryptoEvidence:
    """FX-13 replay record for one crypto candidate.

    Stores the ORIGINAL (pre-transform) convention pair, the market
    kind, ``yes_when``, the market-YES-oriented pair with its direction
    provenance, the conservative edges, the raw asks and the derived
    gate quote -- everything needed to replay the edge arithmetic
    without trusting any caller-side transformation.
    """

    market_kind: str
    record_type: str
    asset: str
    barrier: float
    horizon_hours: float
    original_convention_a: float
    original_convention_b: float
    yes_when: str | None
    direction_source: str
    market_yes_convention_a: float | None
    market_yes_convention_b: float | None
    fair_yes: float
    fair_no: float
    edge_yes: float
    edge_no: float
    buy_side: str
    yes_ask: float
    no_ask: float
    model_median: float
    qs_variants: tuple[float, float]


@dataclass(frozen=True)
class EntryDecision:
    """Immutable outcome of :func:`decide_entry`."""

    accepted: bool
    reasons: tuple[str, ...]
    steps: tuple[StepResult, ...]
    market_id: str
    fixture: str
    market_kind: str
    tier: SizingTier | None
    notional: float | None
    market_class: str | None
    fee_category: str | None
    fee_rate: float | None
    market_rate: float | None
    entry_fee: float | None
    acceptance_walk: BookWalk | None
    actual_walk: BookWalk | None
    gate_report: GateReport | None
    cross_league_mw_check: GateDecision | None
    crypto_evidence: CryptoEvidence | None
    under_risk_flagged: bool
    bridge_sha256: str | None
    pipeline_version: str
    gate_version: str
    model_version: str
    code_sha256: str


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def _normalize_kind(kind: str) -> str:
    return str(kind or "").strip().lower()


def _is_match_winner(kind_normalized: str) -> bool:
    return kind_normalized in MATCH_WINNER_KINDS


def _side_label(side: str) -> str:
    return str(side or "").strip().lower()


def _numeric(value: Any) -> float | None:
    """Strict finite-number reader: bools are NOT numbers here."""

    if isinstance(value, bool):
        return None
    if type(value) not in (int, float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _best_ask(levels: Sequence[Any]) -> float | None:
    """Cheapest valid positive ask price in a raw ladder, if any."""

    prices = []
    for level in levels:
        try:
            if isinstance(level, Mapping):
                price, size = float(level["price"]), float(level["size"])
            else:
                price, size = float(level[0]), float(level[1])
        except (TypeError, ValueError, KeyError, IndexError):
            return None
        if price > 0 and size > 0:
            prices.append(price)
    return min(prices) if prices else None


def _quote_field(quote: Any, names: tuple[str, ...]) -> Any:
    """First present non-None field off a quote object or mapping."""

    for name in names:
        if isinstance(quote, Mapping):
            if quote.get(name) is not None:
                return quote[name]
        else:
            value = getattr(quote, name, None)
            if value is not None:
                return value
    return None


def _decision(
    request: EntryRequest,
    steps: list[StepResult],
    reasons: list[str],
    *,
    tier: SizingTier | None,
    notional: float | None,
    market_class: str | None,
    fee_rate: float | None,
    entry_fee: float | None,
    actual_walk: BookWalk | None,
    gate_report: GateReport | None,
    cross_league_mw_check: GateDecision | None,
    crypto_evidence: CryptoEvidence | None,
    under_risk_flagged: bool,
    bridge_sha256: str | None,
) -> EntryDecision:
    kind_normalized = _normalize_kind(request.market_kind)
    return EntryDecision(
        accepted=not reasons,
        reasons=tuple(sorted(set(reasons))),
        steps=tuple(steps),
        market_id=request.market_id,
        fixture=request.fixture,
        market_kind=kind_normalized,
        tier=tier,
        notional=notional,
        market_class=market_class,
        fee_category=(
            str(request.fee_category).strip().lower()
            if request.fee_category is not None
            else None
        ),
        fee_rate=fee_rate,
        market_rate=request.market_rate,
        entry_fee=entry_fee,
        acceptance_walk=(gate_report.walk if gate_report is not None else None),
        actual_walk=actual_walk,
        gate_report=gate_report,
        cross_league_mw_check=cross_league_mw_check,
        crypto_evidence=crypto_evidence,
        under_risk_flagged=under_risk_flagged,
        bridge_sha256=bridge_sha256,
        pipeline_version=PIPELINE_VERSION,
        gate_version=GATE_VERSION,
        model_version=(
            MODEL_VERSION_CRYPTO
            if kind_normalized in CRYPTO_KINDS
            else MODEL_VERSION_SOCCER
        ),
        code_sha256=policy_code_sha256(),
    )


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------


def _step_book_freshness(request: EntryRequest) -> StepResult:
    """Freshness gate: complete time evidence, no future books, no stale books."""

    missing = [
        name
        for name, present in (
            ("book_time", request.book_time),
            ("now", request.now),
            ("max_book_age_seconds", request.max_book_age_seconds),
        )
        if present is None
    ]
    if missing:
        reason = {
            "book_time": REASON_BOOK_TIME_MISSING,
            "now": REASON_NOW_MISSING,
            "max_book_age_seconds": REASON_MAX_AGE_MISSING,
        }[missing[0]]
        return StepResult(
            STEP_BOOK_FRESHNESS,
            STATUS_BLOCKED,
            (reason,),
            None,
            f"missing time evidence: {', '.join(missing)}",
        )
    try:
        age = (request.now - request.book_time).total_seconds()
    except TypeError:
        return StepResult(
            STEP_BOOK_FRESHNESS,
            STATUS_BLOCKED,
            (REASON_TIME_INCOMPARABLE,),
            None,
            "book_time and now must be same-awareness datetimes",
        )
    max_age = _numeric(request.max_book_age_seconds)
    if max_age is None or max_age < 0:
        return StepResult(
            STEP_BOOK_FRESHNESS,
            STATUS_BLOCKED,
            (REASON_TIME_INCOMPARABLE,),
            None,
            f"max_book_age_seconds must be a finite non-negative number, got "
            f"{request.max_book_age_seconds!r}",
        )
    if age < 0:
        return StepResult(
            STEP_BOOK_FRESHNESS,
            STATUS_FAIL,
            (REASON_BOOK_TIME_IN_FUTURE,),
            age,
            f"book_time is {abs(age):.1f}s in the future relative to now",
        )
    if age > max_age:
        return StepResult(
            STEP_BOOK_FRESHNESS,
            STATUS_FAIL,
            (REASON_STALE_BOOK,),
            age,
            f"book age {age:.1f}s exceeds max {max_age:.1f}s",
        )
    return StepResult(STEP_BOOK_FRESHNESS, STATUS_PASS, (), age, f"book age {age:.1f}s")


def _step_suspension(request: EntryRequest) -> tuple[StepResult, SuspensionDecision]:
    decision = gates.suspension_policy(request.market_kind, cross_league=request.cross_league)
    if decision.eligible:
        return (
            StepResult(
                STEP_SUSPENSION,
                STATUS_PASS,
                (),
                decision,
                f"policy={decision.policy} eligible",
            ),
            decision,
        )
    return (
        StepResult(
            STEP_SUSPENSION,
            STATUS_FAIL,
            (REASON_SUSPENSION_INELIGIBLE,),
            decision,
            f"policy={decision.policy} not eligible: {decision.reason}",
        ),
        decision,
    )


def _step_fee(request: EntryRequest) -> tuple[StepResult, float | None]:
    """Explicit category resolution (FX-06 precedence; R06 acceptance)."""

    if request.fee_category is None or not str(request.fee_category).strip():
        return (
            StepResult(
                STEP_FEE,
                STATUS_BLOCKED,
                (REASON_FEE_CATEGORY_MISSING,),
                None,
                "fee category is a required explicit input; it is never inferred "
                "from team or market_type",
            ),
            None,
        )
    category = str(request.fee_category).strip().lower()
    try:
        fees.taker_fee_rate(category)
    except ValueError:
        return (
            StepResult(
                STEP_FEE,
                STATUS_FAIL,
                (REASON_FEE_CATEGORY_UNKNOWN,),
                None,
                f"unknown fee category {category!r}",
            ),
            None,
        )
    try:
        rate = fees.resolve_taker_fee_rate(category, market_rate=request.market_rate)
    except ValueError as exc:
        return (
            StepResult(
                STEP_FEE,
                STATUS_FAIL,
                (REASON_FEE_RATE_CONFLICT,),
                None,
                str(exc),
            ),
            None,
        )
    return (
        StepResult(
            STEP_FEE,
            STATUS_PASS,
            (),
            rate,
            f"category={category} rate={rate} "
            f"(market_rate={'absent' if request.market_rate is None else request.market_rate})",
        ),
        rate,
    )


def _screenable(kind_normalized: str, side_label: str) -> bool:
    """Mirror of the gates under_risk screen applicability rule."""

    is_over = kind_normalized == "total" and side_label.startswith("over")
    is_yes = kind_normalized == "btts" and side_label == "yes"
    return is_over or is_yes


def _step_under_risk(
    request: EntryRequest, kind_normalized: str, quote: Any
) -> tuple[StepResult, bool | None]:
    """Low-scoring-risk screen: explicit flag or raw materials."""

    side_label = _side_label(request.side)
    if request.under_risk:
        return (
            StepResult(
                STEP_UNDER_RISK,
                STATUS_PASS,
                (),
                True,
                "explicit under_risk flag: exec bar raised to +8c",
            ),
            True,
        )
    if not _screenable(kind_normalized, side_label):
        return (
            StepResult(
                STEP_UNDER_RISK,
                STATUS_PASS,
                (),
                False,
                "under_risk screen not applicable to this kind/side",
            ),
            False,
        )
    lambda_home = _quote_field(quote, ("lambda_home",)) if quote is not None else None
    lambda_away = _quote_field(quote, ("lambda_away",)) if quote is not None else None
    if lambda_home is None:
        lambda_home = request.lambda_home
    if lambda_away is None:
        lambda_away = request.lambda_away
    o25_valid = (
        request.o25_hits is not None
        and not isinstance(request.o25_hits, bool)
        and isinstance(request.o25_hits, int)
    )
    materials = {
        "o25_hits": request.o25_hits if o25_valid else None,
        "lambda_home": lambda_home if (lambda_home is not None and _numeric(lambda_home) is not None) else None,
        "lambda_away": lambda_away if (lambda_away is not None and _numeric(lambda_away) is not None) else None,
        "line": request.line if (request.line is not None and _numeric(request.line) is not None) else None,
    }
    missing = [name for name, value in materials.items() if value is None]
    if missing:
        return (
            StepResult(
                STEP_UNDER_RISK,
                STATUS_BLOCKED,
                (REASON_UNDER_RISK_INPUTS_MISSING,),
                None,
                f"under_risk screen applies but materials missing or invalid: "
                f"{', '.join(missing)}",
            ),
            None,
        )
    flagged, why = gates.under_risk_check(
        kind=kind_normalized,
        side=side_label,
        o25_hits=int(request.o25_hits),
        lambda_total=float(lambda_home) + float(lambda_away),
        lambda_home=float(lambda_home),
        lambda_away=float(lambda_away),
        line=float(request.line),
    )
    detail = "flagged: " + why if flagged else "not flagged"
    return (
        StepResult(STEP_UNDER_RISK, STATUS_PASS, (), flagged, detail),
        flagged,
    )


def _step_crypto_direction(
    request: EntryRequest, kind_normalized: str, levels: Sequence[Any]
) -> tuple[StepResult, tuple[Any, CryptoEvidence | None] | None]:
    """FX-13 precondition and the derived conservative gate quote."""

    probability = request.probability
    if probability is None and request.model_quote is None:
        return (
            StepResult(
                STEP_CRYPTO_DIRECTION,
                STATUS_BLOCKED,
                (REASON_MODEL_EVIDENCE_MISSING,),
                None,
                f"no model evidence for a {kind_normalized} market",
            ),
            None,
        )
    if probability is not None and request.model_quote is not None:
        return (
            StepResult(
                STEP_CRYPTO_DIRECTION,
                STATUS_BLOCKED,
                (REASON_MODEL_EVIDENCE_AMBIGUOUS,),
                None,
                "both a probability record and a model quote supplied",
            ),
            None,
        )
    if kind_normalized in CRYPTO_KINDS:
        if probability is None:
            return (
                StepResult(
                    STEP_CRYPTO_DIRECTION,
                    STATUS_BLOCKED,
                    (REASON_MODEL_EVIDENCE_MISMATCH,),
                    None,
                    "crypto markets must arrive as a raw probability record; a "
                    "caller-derived model quote is not trusted (FX-13)",
                ),
                None,
            )
        return _crypto_quote(request, kind_normalized, probability, levels)
    if probability is not None:
        return (
            StepResult(
                STEP_CRYPTO_DIRECTION,
                STATUS_BLOCKED,
                (REASON_MODEL_EVIDENCE_MISMATCH,),
                None,
                "a crypto probability record was supplied for a non-crypto market kind",
            ),
            None,
        )
    # Soccer-style market: the model quote IS the evidence; it must
    # expose the gate fields (evaluate_candidate validates them).
    return (
        StepResult(
            STEP_CRYPTO_DIRECTION,
            STATUS_PASS,
            (),
            (request.model_quote, None),
            "model quote passed through for a non-crypto market kind",
        ),
        (request.model_quote, None),
    )


def _crypto_quote(
    request: EntryRequest,
    kind_normalized: str,
    probability: Any,
    levels: Sequence[Any],
) -> tuple[StepResult, tuple[Any, CryptoEvidence | None] | None]:
    reasons: list[str] = []
    details: list[str] = []

    # --- record type vs market kind -------------------------------------
    terminal = isinstance(probability, TerminalProbability)
    touch = isinstance(probability, TouchProbability)
    if not terminal and not touch:
        if type(probability).__name__ == "TerminalYesProbability":
            reasons.append(REASON_TERMINAL_PRETRANSFORMED)
            details.append(
                "a pre-transformed TerminalYesProbability was supplied; pass the "
                "raw TerminalProbability and yes_when (FX-13)"
            )
        else:
            reasons.append(REASON_MODEL_EVIDENCE_MISMATCH)
            details.append(
                f"probability evidence is {type(probability).__name__}, expected "
                "TouchProbability or TerminalProbability"
            )
    elif terminal and kind_normalized != KIND_CRYPTO_TERMINAL:
        reasons.append(REASON_PROBABILITY_TYPE_CONFLICT)
        details.append(
            f"TerminalProbability record on a {kind_normalized} market"
        )
    elif touch and kind_normalized != KIND_CRYPTO_TOUCH:
        reasons.append(REASON_PROBABILITY_TYPE_CONFLICT)
        details.append(f"TouchProbability record on a {kind_normalized} market")

    # --- direction vocabulary --------------------------------------------
    yes_when = request.yes_when
    if touch and yes_when is not None:
        reasons.append(REASON_TOUCH_TERMINAL_CONFLICT)
        details.append(
            "yes_when supplied for a touch market: touch records go straight to "
            "conservative_edges with no direction transform"
        )
    if terminal:
        if yes_when is None:
            reasons.append(REASON_YES_WHEN_MISSING)
            details.append("terminal markets require an explicit yes_when")
        elif yes_when not in YES_WHEN_VALUES:
            reasons.append(REASON_YES_WHEN_INVALID)
            details.append(f"yes_when must be 'below' or 'above', got {yes_when!r}")
        contract = request.contract_direction
        if contract is not None:
            if contract not in YES_WHEN_VALUES:
                reasons.append(REASON_YES_WHEN_CONFLICT)
                details.append(
                    f"contract metadata direction {contract!r} is not a valid "
                    "above/below token"
                )
            elif yes_when in YES_WHEN_VALUES and contract != yes_when:
                reasons.append(REASON_YES_WHEN_CONFLICT)
                details.append(
                    f"contract metadata direction {contract!r} disagrees with the "
                    f"supplied yes_when {yes_when!r}"
                )

    # --- buy direction ------------------------------------------------------
    buy_side = request.buy_side
    if buy_side is None:
        reasons.append(REASON_BUY_SIDE_MISSING)
        details.append("crypto candidates require an explicit buy side (yes/no)")
    elif buy_side not in BUY_SIDES:
        reasons.append(REASON_BUY_SIDE_INVALID)
        details.append(f"buy_side must be 'yes' or 'no', got {buy_side!r}")
    if (
        buy_side in BUY_SIDES
        and request.buy_token is not None
        and (request.yes_token is not None or request.no_token is not None)
    ):
        if request.yes_token is not None and request.buy_token == request.yes_token:
            if buy_side != "yes":
                reasons.append(REASON_BUY_SIDE_CONFLICT)
                details.append("buy_token is the YES token but buy_side is not 'yes'")
        elif request.no_token is not None and request.buy_token == request.no_token:
            if buy_side != "no":
                reasons.append(REASON_BUY_SIDE_CONFLICT)
                details.append("buy_token is the NO token but buy_side is not 'no'")
        else:
            reasons.append(REASON_BUY_SIDE_CONFLICT)
            details.append(
                "buy_token matches neither the YES nor the NO token of the contract"
            )

    # --- raw asks ------------------------------------------------------------
    yes_ask = _numeric(request.yes_ask)
    no_ask = _numeric(request.no_ask)
    if yes_ask is None or no_ask is None:
        if request.yes_ask is None or request.no_ask is None:
            reasons.append(REASON_CRYPTO_ASKS_MISSING)
            details.append("conservative edges require both the raw yes_ask and no_ask")
        else:
            reasons.append(REASON_CRYPTO_ASKS_INVALID)
            details.append("yes_ask/no_ask must be finite numbers")
    else:
        if not (0.0 < yes_ask < 1.0) or not (0.0 < no_ask < 1.0):
            reasons.append(REASON_CRYPTO_ASKS_INVALID)
            details.append("yes_ask/no_ask must lie strictly inside (0, 1)")
        else:
            best = _best_ask(levels)
            if best is not None and buy_side in BUY_SIDES:
                bought_ask = yes_ask if buy_side == "yes" else no_ask
                if abs(bought_ask - best) > _ASK_MATCH_TOLERANCE:
                    reasons.append(REASON_CRYPTO_ASK_CONFLICT)
                    details.append(
                        f"the supplied {'yes' if buy_side == 'yes' else 'no'} ask "
                        f"{bought_ask} disagrees with the raw book's best ask {best}"
                    )

    if reasons:
        return (
            StepResult(
                STEP_CRYPTO_DIRECTION,
                STATUS_FAIL if terminal or touch else STATUS_BLOCKED,
                tuple(reasons),
                None,
                "; ".join(details),
            ),
            None,
        )

    # --- conservative edges through the mandated paths ---------------------
    if terminal:
        # FX-13: the ONLY sanctioned path for terminal markets; the
        # transform to the market's YES side happens inside the wrapper.
        edges = crypto_model.conservative_edges_for_terminal(
            probability, yes_when=yes_when, yes_ask=yes_ask, no_ask=no_ask
        )
        yes_pair = crypto_model.terminal_yes_probability(probability, yes_when=yes_when)
        oriented = (yes_pair.convention_a, yes_pair.convention_b)
        direction_source = "pipeline:terminal_yes_probability(via conservative_edges_for_terminal)"
        market_yes = oriented
    else:
        edges = crypto_model.conservative_edges(
            probability, yes_ask=yes_ask, no_ask=no_ask
        )
        oriented = (probability.convention_a, probability.convention_b)
        direction_source = "pipeline:none(touch: YES is the touch event)"
        market_yes = None
        yes_when = None

    if buy_side == "yes":
        variants = oriented
        median = edges.fair_yes
    else:
        variants = (1.0 - oriented[0], 1.0 - oriented[1])
        median = edges.fair_no

    evidence = CryptoEvidence(
        market_kind=kind_normalized,
        record_type="terminal" if terminal else "touch",
        asset=probability.asset,
        barrier=probability.barrier,
        horizon_hours=probability.horizon_hours,
        original_convention_a=probability.convention_a,
        original_convention_b=probability.convention_b,
        yes_when=yes_when,
        direction_source=direction_source,
        market_yes_convention_a=market_yes[0] if market_yes else None,
        market_yes_convention_b=market_yes[1] if market_yes else None,
        fair_yes=edges.fair_yes,
        fair_no=edges.fair_no,
        edge_yes=edges.edge_yes,
        edge_no=edges.edge_no,
        buy_side=buy_side,
        yes_ask=yes_ask,
        no_ask=no_ask,
        model_median=median,
        qs_variants=variants,
    )
    quote = {"model_median": median, "qs_variants": variants}
    return (
        StepResult(
            STEP_CRYPTO_DIRECTION,
            STATUS_PASS,
            (),
            (quote, evidence),
            f"{evidence.record_type} record oriented via {direction_source}; "
            f"buy {buy_side.upper()}",
        ),
        (quote, evidence),
    )


def _step_gate(
    request: EntryRequest,
    quote: Any | None,
    fee_rate: float | None,
    under_risk_flagged: bool | None,
    levels: Sequence[Any],
) -> tuple[StepResult, GateReport | None]:
    """The frozen gate set over the raw book (ASK-only, $250 acceptance walk)."""

    gate_reasons: list[str] = []
    if quote is None:
        return (
            StepResult(
                STEP_GATE,
                STATUS_BLOCKED,
                (),
                None,
                "gate evaluation blocked: no usable model quote (see upstream steps)",
            ),
            None,
        )
    if fee_rate is None:
        return (
            StepResult(
                STEP_GATE,
                STATUS_BLOCKED,
                (),
                None,
                "gate evaluation blocked: fee category unresolved (see upstream steps)",
            ),
            None,
        )
    if under_risk_flagged is None:
        return (
            StepResult(
                STEP_GATE,
                STATUS_BLOCKED,
                (),
                None,
                "gate evaluation blocked: under_risk bar undetermined "
                "(see upstream steps)",
            ),
            None,
        )
    if request.news is None:
        gate_reasons.append(REASON_NEWS_MISSING)
    try:
        report = gates.evaluate_candidate(
            quote,
            levels,
            gates.SIDE_ASK,
            gamma_liquidity=request.gamma_liquidity,
            news=request.news,
            under_risk=under_risk_flagged,
            fee_rate=fee_rate,
        )
    except (ValueError, TypeError) as exc:
        return (
            StepResult(
                STEP_GATE,
                STATUS_ERROR,
                (REASON_STEP_ERROR,),
                None,
                f"evaluate_candidate rejected the evidence: {exc}",
            ),
            None,
        )
    gate_reasons.extend(report.decision.failed)
    status = STATUS_FAIL if gate_reasons else STATUS_PASS
    detail = (
        "gate checks: "
        + ", ".join(f"{name}={'ok' if ok else 'FAIL'}" for name, ok in report.checks.items())
    )
    return (
        StepResult(STEP_GATE, status, tuple(gate_reasons), report, detail),
        report,
    )


def _validate_crossbridge(
    request: EntryRequest, kind_normalized: str
) -> tuple[list[str], bool, str | None]:
    """Strict crossbridge evidence check (leagues, kappa, fingerprint)."""

    reasons: list[str] = []
    home = request.home_league
    away = request.away_league
    if request.cross_league:
        if home is None or away is None or not str(home).strip() or not str(away).strip():
            reasons.append(REASON_CROSS_LEAGUE_LEAGUE_MISMATCH)
            return reasons, False, None
        if str(home).strip().lower() == str(away).strip().lower():
            reasons.append(REASON_CROSS_LEAGUE_LEAGUE_MISMATCH)
            return reasons, False, None
    elif home is not None and away is not None:
        if str(home).strip().lower() != str(away).strip().lower():
            reasons.append(REASON_CROSS_LEAGUE_LEAGUE_MISMATCH)
            return reasons, False, None
    if not request.cross_league:
        return reasons, False, None
    bridge = request.bridge
    if bridge is None:
        # Absent bridge: the frozen check_cross_league_mw owns this outcome
        # (its cross_league_bridge failure); no extra reason here.
        return reasons, False, None
    if not isinstance(bridge, Mapping):
        reasons.append(REASON_BRIDGE_INVALID)
        return reasons, False, None
    kappa_home = bridge.get(str(home).strip().lower(), bridge.get(str(home)))
    kappa_away = bridge.get(str(away).strip().lower(), bridge.get(str(away)))
    for kappa in (kappa_home, kappa_away):
        if _numeric(kappa) is None:
            reasons.append(REASON_BRIDGE_INVALID)
            return reasons, False, None
    # Pure kappa-table rule (review P2-1 fix, same semantics as the
    # league-key kappa checks above): EVERY bridge value -- including
    # any key outside the fixture's two leagues -- must be a finite
    # real number. A non-numeric extra (e.g. a "note" annotation) or a
    # numeric STRING is invalid bridge evidence and is rejected here,
    # BEFORE bridge_fingerprint's float() conversion can raise out of
    # decide_entry; an exception never escapes, the decision records
    # bridge_invalid instead. Numeric extras stay valid when the
    # fingerprint covers the same table.
    for value in bridge.values():
        if _numeric(value) is None:
            reasons.append(REASON_BRIDGE_INVALID)
            return reasons, False, None
    if request.bridge_fingerprint is None:
        reasons.append(REASON_BRIDGE_FINGERPRINT_MISSING)
        return reasons, False, None
    digest = bridge_fingerprint(bridge)
    if digest != request.bridge_fingerprint:
        reasons.append(REASON_BRIDGE_FINGERPRINT_MISMATCH)
        return reasons, False, None
    return reasons, True, digest


def _step_cross_league(
    request: EntryRequest,
    kind_normalized: str,
    report: GateReport | None,
) -> tuple[StepResult, GateDecision | None, str | None]:
    """Strict crossbridge validation + the frozen cross-league MW check."""

    bridge_reasons, bridge_ok, digest = _validate_crossbridge(request, kind_normalized)
    reasons = list(bridge_reasons)
    is_mw = _is_match_winner(kind_normalized)
    worst = report.worst_variant_edge if report is not None else None
    needs_edge = request.cross_league and is_mw
    if needs_edge and worst is None:
        reasons.append(REASON_CROSS_LEAGUE_MW_BLOCKED)
        return (
            StepResult(
                STEP_CROSS_LEAGUE,
                STATUS_BLOCKED if not bridge_reasons else STATUS_FAIL,
                tuple(reasons),
                None,
                "cross-league match_winner check blocked: no walked worst-variant "
                "edge (empty book or blocked gate step)",
            ),
            None,
            digest,
        )
    try:
        decision = gates.check_cross_league_mw(
            request.market_kind,
            worst_variant_edge=worst if worst is not None else 0.0,
            cross_league=request.cross_league,
            bridge_present=bridge_ok,
        )
    except ValueError as exc:
        return (
            StepResult(
                STEP_CROSS_LEAGUE,
                STATUS_ERROR,
                (REASON_STEP_ERROR,) + tuple(reasons),
                None,
                f"check_cross_league_mw rejected the evidence: {exc}",
            ),
            None,
            digest,
        )
    reasons.extend(decision.failed)
    status = STATUS_FAIL if reasons else STATUS_PASS
    detail = (
        f"bridge={'validated' if bridge_ok else 'not validated'}; "
        f"worst_variant_edge={worst if worst is not None else 'n/a'}; "
        f"failed={decision.failed or 'none'}"
    )
    return (
        StepResult(STEP_CROSS_LEAGUE, status, tuple(reasons), decision, detail),
        decision,
        digest,
    )


def _derive_market_class(kind_normalized: str, cross_league: bool) -> str:
    """Sizing class is DERIVED here -- callers cannot override it."""

    if _is_match_winner(kind_normalized):
        if cross_league:
            return risk_policy.CROSS_LEAGUE_ML_CLASS
        return risk_policy.DOMESTIC_ML_CLASS
    return risk_policy.STANDARD_CLASS


def _step_sizing(
    request: EntryRequest,
    kind_normalized: str,
    report: GateReport | None,
) -> tuple[StepResult, SizingTier | None, float | None, str | None]:
    market_class = _derive_market_class(kind_normalized, request.cross_league)
    worst = report.worst_variant_edge if report is not None else None
    # Evidence-derived fragility for the domestic ML class: a pass whose
    # worst-variant margin sits below the cross-league extra margin is a
    # thin-margin (fragile) pass -- sized at the reduced unit.
    fragile = (
        _is_match_winner(kind_normalized)
        and not request.cross_league
        and worst is not None
        and worst < gates.CROSS_LEAGUE_MW_WORST_EDGE_MIN
    )
    try:
        tier = risk_policy.sizing_tier(
            market_class=market_class,
            fragile_pass=fragile,
            lineup_confirmation=request.lineup_confirmation,
        )
        notional = risk_policy.tier_notional(tier, lineup_confirmation=request.lineup_confirmation)
    except ValueError as exc:
        return (
            StepResult(
                STEP_SIZING,
                STATUS_ERROR,
                (REASON_STEP_ERROR,),
                None,
                f"sizing rejected the evidence: {exc}",
            ),
            None,
            None,
            None,
        )
    detail = (
        f"class={market_class} tier={tier.tier} notional={notional} "
        f"fragile_pass={fragile}"
    )
    return (
        StepResult(STEP_SIZING, STATUS_PASS, (), tier, detail),
        tier,
        notional,
        market_class,
    )


def _step_actual_walk(
    levels: Sequence[Any], notional: float | None
) -> tuple[StepResult, BookWalk | None]:
    if notional is None:
        return (
            StepResult(
                STEP_ACTUAL_WALK,
                STATUS_BLOCKED,
                (),
                None,
                "actual walk blocked: no notional (see sizing step)",
            ),
            None,
        )
    try:
        walk = gates.walk_book(levels, side=gates.SIDE_ASK, notional=notional)
    except (TypeError, ValueError, KeyError, IndexError) as exc:
        return (
            StepResult(
                STEP_ACTUAL_WALK,
                STATUS_ERROR,
                (REASON_STEP_ERROR,),
                None,
                f"walk_book rejected the book evidence: {exc}",
            ),
            None,
        )
    if not walk.filled or walk.vwap is None:
        return (
            StepResult(
                STEP_ACTUAL_WALK,
                STATUS_FAIL,
                (REASON_ACTUAL_WALK_INCOMPLETE,),
                walk,
                f"book could not absorb the full {notional} stake "
                f"(filled {walk.filled_notional})",
            ),
            walk,
        )
    return (
        StepResult(
            STEP_ACTUAL_WALK,
            STATUS_PASS,
            (),
            walk,
            f"actual walk: vwap={walk.vwap} shares={walk.shares}",
        ),
        walk,
    )


# ---------------------------------------------------------------------------
# The single entry point
# ---------------------------------------------------------------------------


def decide_entry(request: EntryRequest) -> EntryDecision:
    """Decide paper-entry candidacy for one explicit request.

    Runs the canonical step order (:data:`STEP_ORDER`), collecting
    EVERY rejection reason (a ``blocked`` dependency still lets the
    remaining computable checks run and record their own outcomes).
    Returns an immutable :class:`EntryDecision` whose ``accepted`` is
    True iff ``reasons`` is empty. Pure computation; see the module
    docstring for the FX-13/FX-06 evidence contracts.
    """

    reasons: list[str] = []
    steps: list[StepResult] = []
    levels = list(request.book)
    kind_normalized = _normalize_kind(request.market_kind)

    # 1. book freshness
    step = _step_book_freshness(request)
    steps.append(step)
    reasons.extend(step.reasons)

    # 2. suspension policy
    step, _suspension = _step_suspension(request)
    steps.append(step)
    reasons.extend(step.reasons)

    # 3. explicit fee category (FX-06/R06)
    step, fee_rate = _step_fee(request)
    steps.append(step)
    reasons.extend(step.reasons)

    # 4. under-risk screen (feeds the gate bar)
    quote_probe = request.model_quote if kind_normalized not in CRYPTO_KINDS else None
    step, under_risk_flagged = _step_under_risk(request, kind_normalized, quote_probe)
    steps.append(step)
    reasons.extend(step.reasons)

    # 5. FX-13 crypto direction precondition + quote derivation
    step, quote_pack = _step_crypto_direction(request, kind_normalized, levels)
    steps.append(step)
    reasons.extend(step.reasons)
    quote = quote_pack[0] if quote_pack is not None else None
    crypto_evidence = quote_pack[1] if quote_pack is not None else None

    # 6. the frozen gate set (original $250 ASK acceptance walk inside)
    step, report = _step_gate(request, quote, fee_rate, under_risk_flagged, levels)
    steps.append(step)
    reasons.extend(step.reasons)

    # 7. strict crossbridge + cross-league match-winner check
    step, cross_mw, bridge_digest = _step_cross_league(request, kind_normalized, report)
    steps.append(step)
    reasons.extend(step.reasons)

    # 8. held-fixture correlation
    correlation = risk_policy.held_fixture_add_allowed(request.held_sides, request.side)
    if correlation.allowed:
        step = StepResult(STEP_HELD_FIXTURE, STATUS_PASS, (), correlation, correlation.reason)
    else:
        step = StepResult(
            STEP_HELD_FIXTURE, STATUS_FAIL, (REASON_HELD_FIXTURE_BLOCKED,), correlation,
            correlation.reason,
        )
        reasons.append(REASON_HELD_FIXTURE_BLOCKED)
    steps.append(step)

    # 9. sizing (class derived here; not caller-overridable)
    step, tier, notional, market_class = _step_sizing(request, kind_normalized, report)
    steps.append(step)
    reasons.extend(step.reasons)

    # 10. actual-stake walk over the same raw book
    step, actual_walk = _step_actual_walk(levels, notional)
    steps.append(step)
    reasons.extend(step.reasons)

    # 11. acceptance-notional cap (the acceptance walk was $250)
    if notional is None:
        steps.append(
            StepResult(
                STEP_NOTIONAL_CAP, STATUS_BLOCKED, (), None,
                "notional cap blocked: no notional (see sizing step)",
            )
        )
    elif notional > gates.DEFAULT_NOTIONAL_USD + _MONEY_EPSILON:
        steps.append(
            StepResult(
                STEP_NOTIONAL_CAP,
                STATUS_FAIL,
                (REASON_NOTIONAL_CAP_EXCEEDED,),
                notional,
                f"notional {notional} exceeds the acceptance walk "
                f"{gates.DEFAULT_NOTIONAL_USD}",
            )
        )
        reasons.append(REASON_NOTIONAL_CAP_EXCEEDED)
    else:
        steps.append(
            StepResult(STEP_NOTIONAL_CAP, STATUS_PASS, (), notional, f"notional {notional}")
        )

    # 12. team cap
    try:
        team_ok = risk_policy.team_add_allowed(
            request.team_committed,
            notional if notional is not None else 0.0,
            cap=request.team_cap,
        )
    except (TypeError, ValueError) as exc:
        steps.append(
            StepResult(
                STEP_TEAM_CAP,
                STATUS_ERROR,
                (REASON_STEP_ERROR,),
                None,
                f"team cap arithmetic rejected the evidence: {exc}",
            )
        )
        reasons.append(REASON_STEP_ERROR)
    else:
        if team_ok:
            steps.append(
                StepResult(
                    STEP_TEAM_CAP, STATUS_PASS, (), team_ok,
                    f"team exposure {request.team_committed} + {notional} fits cap "
                    f"{request.team_cap}",
                )
            )
        else:
            steps.append(
                StepResult(
                    STEP_TEAM_CAP,
                    STATUS_FAIL,
                    (REASON_TEAM_CAP_EXCEEDED,),
                    team_ok,
                    f"team exposure {request.team_committed} + {notional} exceeds cap "
                    f"{request.team_cap}",
                )
            )
            reasons.append(REASON_TEAM_CAP_EXCEEDED)

    # 13. bankroll / cash cover (principal + entry fee, charged once)
    entry_fee = None
    if notional is None or actual_walk is None or actual_walk.vwap is None:
        steps.append(
            StepResult(
                STEP_BANKROLL, STATUS_BLOCKED, (), None,
                "bankroll check blocked: no notional or no walked entry price",
            )
        )
    elif fee_rate is None:
        steps.append(
            StepResult(
                STEP_BANKROLL, STATUS_BLOCKED, (), None,
                "bankroll check blocked: entry fee undetermined "
                "(fee category unresolved)",
            )
        )
    else:
        try:
            fee = round(fees.entry_fee(notional, actual_walk.vwap, fee_rate), 4)
            available = risk_policy.bankroll_available(
                request.bankroll, request.cash, request.reserved
            )
        except (TypeError, ValueError) as exc:
            steps.append(
                StepResult(
                    STEP_BANKROLL,
                    STATUS_ERROR,
                    (REASON_STEP_ERROR,),
                    None,
                    f"bankroll arithmetic rejected the evidence: {exc}",
                )
            )
            reasons.append(REASON_STEP_ERROR)
        else:
            entry_fee = fee
            covers = (
                available + _MONEY_EPSILON >= notional
                and available + _MONEY_EPSILON >= round(notional + fee, 2)
            )
            if covers:
                steps.append(
                    StepResult(
                        STEP_BANKROLL, STATUS_PASS, (), available,
                        f"available {available} covers notional {notional} "
                        f"+ entry fee {fee}",
                    )
                )
            else:
                steps.append(
                    StepResult(
                        STEP_BANKROLL,
                        STATUS_FAIL,
                        (REASON_BANKROLL_INSUFFICIENT,),
                        available,
                        f"available {available} does not cover notional {notional} "
                        f"+ entry fee {fee} (principal + fee, charged once)",
                    )
                )
                reasons.append(REASON_BANKROLL_INSUFFICIENT)

    return _decision(
        request,
        steps,
        reasons,
        tier=tier,
        notional=notional,
        market_class=market_class,
        fee_rate=fee_rate,
        entry_fee=entry_fee,
        actual_walk=actual_walk,
        gate_report=report,
        cross_league_mw_check=cross_mw,
        crypto_evidence=crypto_evidence,
        under_risk_flagged=bool(under_risk_flagged),
        bridge_sha256=bridge_digest,
    )
