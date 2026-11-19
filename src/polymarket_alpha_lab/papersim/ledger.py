"""Paper position ledger records.

Single responsibility: build and mark the paper ledger entries (open,
hold, exit, realized/unrealized) for gated paper positions, and compute
the per-position marks used in reports. This module defines the record
shapes and the arithmetic only. Durable persistence of these records is
project-private native PostgreSQL (Project Iron Rule 1) via the store
adapters elsewhere in this package; this module must not create
file-backed journals (legacy file surfaces are frozen).

Canonical lineage: coordinator-maintained ledger practice from the soccer
lineage (``r5c -> r15 -> r23``; position ids such as ``#48
Brentford-Forest O2.5 Over $250 @ 0.560`` with r24 recheck blocks) and the
crypto lineage (``r19`` positions-MTM table). Paper-only: entries record
simulated fills, never real orders.

WP-P1 ledger DSN, settlement arithmetic and DDL (W4):

- ``papersim_dsn()`` resolves the ledger DSN from ``PAPERSIM_PG_DSN``
  (documented example default:
  ``postgresql://paper_sim@127.0.0.1:55432/paper_sim``, the loopback
  paper_sim evidence cluster) and always passes the value through the
  single audited ``validate_local_postgres_dsn`` with
  ``env_var_name="PAPERSIM_PG_DSN"`` before any caller can open a
  connection. The SCRAM password is never part of the DSN and is never
  read, printed or committed by this module: operators inject it
  out-of-band via ``PAPERSIM_PG_PASSWORD_FILE`` (an environment variable
  holding the path of a one-line password file); only a connection
  adapter explicitly asked to connect may read that path at connect
  time. There is no default password path.
- ``classify_outcome`` buckets a resolution outcome as open / exited /
  settled. A discipline ``EXITED`` realizes trading PnL but is never a
  win or a loss: ``settle_natural`` rejects it and
  ``PaperSettlement.win_loss`` is ``None`` for exits, matching the fixed
  ``model_performance`` view (exits separated from W/L, included in
  total PnL) and the natural-resolution-only ``forecast_calibration``.
- ``settle_natural`` / ``settle_exit`` realize a position with
  ``pnl = proceeds - stake``: a natural win pays ``stake / price`` per
  $1.00 of payoff, a natural loss pays 0, and an exit realizes
  ``(stake / price) * exit_price`` (live book-of-record example: the Fed
  Jan-hike NO exited at 0.585 -> proceeds 339.30 on 580 shares).
- Net-PnL semantics (audit 2026-11-12 finding P-2, fix item FX-01):
  ``pnl`` keeps its GROSS meaning (``proceeds - stake``) so every
  existing anchor and test stays byte-identical; the fee layers are
  additive optional keyword arguments. ``PaperSettlement.fee_total``
  (default 0.0) carries the charged fees and the read-only property
  ``PaperSettlement.pnl_net`` returns ``pnl - fee_total``. With
  ``fee_rate``, ``settle_natural`` charges the official entry fee
  ``shares * rate * price * (1 - price)`` (shares = ``stake / price``,
  i.e. ``stake * rate * (1 - price)``; natural settlement has no exit
  fee); ``settle_exit`` accepts ``fee_rate`` (entry leg) and
  ``exit_fee_rate`` (sell leg, ``shares * rate * exit_price *
  (1 - exit_price)``) independently. Rates always come from
  :mod:`polymarket_alpha_lab.papersim.fees`
  (:func:`~polymarket_alpha_lab.papersim.fees.taker_fee_rate`) — no
  rate numbers are hardcoded here.
- ``queue_performance`` aggregates settled ledger rows (the live-
  cluster ``decisions``-shaped mappings) into gross and net per-queue
  performance using the same fee formula (V-6 evidence run 2026-11-12:
  ``fee = shares x rate x p x (1 - p)``, exits add sell fees at the
  reconstructed exit price, ``VOIDED`` rows are stake-refunded and
  excluded, unresolved rows are excluded). It is the minimal
  predecessor of the FX-11 evaluation module and is deliberately an
  ESTIMATE for history: whether the historical markets were actually
  charged, and at which tier, is not verifiable from the repo, so net
  figures are back-computed with the fee schedule as of
  ``fees.FEE_SCHEDULE_AS_OF`` and must be labeled as such by callers.
- ``bankroll_identity`` checks the reconciled book-of-record identity
  ``bankroll == cash + deployed`` (2026-11-01 row: 12374.33 = 9488.68
  cash + 2885.65 deployed).
- ``ddl()`` returns the exact text of the checked-in migration
  ``20261101000000_papersim_ledger.sql``: the live paper_sim
  ``decisions`` / ``resolutions`` / ``bankroll`` tables and the fixed
  ``model_performance`` / ``forecast_calibration`` views, mirrored
  verbatim plus the documented repo-convention deltas of
  paper-only/report-only/readonly boundary columns on ``decisions``
  and the NO-basket clause added to the ``avg_prob_of_realized`` hit
  predicate so it matches ``forecast_hits`` (codex-review F5; the LIVE
  paper_sim cluster view still needs the same one-line fix applied
  operationally - this module never touches the live cluster).

Historical clarification (snapshot dated 2026-11-12): the archived
forecast_calibration_viewdef.sql under
docs/paper-sim/ledger-snapshots/2026-11-12/ already contains all three
hit branches in both forecast_hits and avg_prob_of_realized, including
NO-basket. The earlier "still needs" wording above and the operational
note in the 2026-11-01 migration and its verbatim DDL mirror are historical
statements from that migration's capture date, not a current action item.
Their reviewed bytes remain unchanged. This clarification uses archived
evidence only; it neither verifies nor changes the live database.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import math

from polymarket_alpha_lab.papersim.fees import (
    FEE_SCHEDULE_AS_OF as _FEE_SCHEDULE_AS_OF,
)
from polymarket_alpha_lab.papersim.fees import (
    entry_fee as _entry_fee,
    exit_fee as _exit_fee,
    legacy_scan_fee_category as _legacy_scan_fee_category,
    taker_fee_rate as _taker_fee_rate,
)
from polymarket_alpha_lab.supabase_papersim_config import (
    DEFAULT_PAPERSIM_PG_DSN,
    PAPERSIM_PG_DSN_ENV_VAR,
    PAPERSIM_PG_PASSWORD_FILE_ENV_VAR,
    from_papersim_db_env,
)


PAPERSIM_LEDGER_MIGRATION_NAME = "20261101000000_papersim_ledger.sql"

OUTCOME_OPEN = "open"
OUTCOME_EXITED = "exited"
OUTCOME_SETTLED = "settled"
EXITED_OUTCOME = "EXITED"
NATURAL_OUTCOMES = ("YES", "NO", "YES-basket")
# Post-audit settlement labels seen on the live cluster from ids 62-70
# (V-6 evidence run 2026-11-12): the settlement worker records the
# realized label directly instead of the market's natural token.
WIN_OUTCOME = "win"
LOSS_OUTCOME = "loss"
SETTLED_LABEL_OUTCOMES = (WIN_OUTCOME, LOSS_OUTCOME)
# VOIDED markets refund the stake (recorded pnl 0.00, payoff = stake):
# excluded from performance with zero fees, never a win or a loss.
VOIDED_OUTCOME = "VOIDED"


@dataclass(frozen=True)
class PaperLedgerEntry:
    """One immutable paper ledger event for a single position."""

    position_id: str
    fixture: str
    question: str
    side: str
    notional: float
    price: float
    event: str  # "entry" | "recheck" | "exit"
    evidence: tuple[str, ...]


def record_entry(
    position_id: str,
    fixture: str,
    question: str,
    side: str,
    notional: float,
    price: float,
    *,
    evidence: tuple[str, ...] = (),
) -> PaperLedgerEntry:
    """Build an entry event for a newly gated paper position.

    FX-09L completion of the former P0 stub (09D design v2 section
    5.9): PURE event construction — validation and assembly only. Zero I/O,
    zero persistence, zero clock reads, no default fee category, no
    rounding. The returned entry carries the fixed ``event='entry'`` and
    ``evidence`` normalized to a tuple; durable persistence stays with the
    store adapters elsewhere in this package.
    """
    for name, value in (
        ("position_id", position_id),
        ("fixture", fixture),
        ("question", question),
        ("side", side),
    ):
        if type(value) is not str or not value:
            raise ValueError(f"{name} must be a non-empty string")
    _require_finite(notional, "notional")
    if notional <= 0:
        raise ValueError("notional must be positive")
    _require_finite(price, "price")
    if not 0.0 < price < 1.0:
        raise ValueError("price must be in the open interval (0, 1)")
    for item in evidence:
        if type(item) is not str:
            raise ValueError("evidence items must be strings")
    return PaperLedgerEntry(
        position_id=position_id,
        fixture=fixture,
        question=question,
        side=side,
        notional=notional,
        price=price,
        event="entry",
        evidence=tuple(evidence),
    )


def mark_to_market(entry_price: float, exit_bid: float) -> float:
    """Per-share mark-to-market delta: ``exit_bid - entry_price``.

    FX-09L completion of the former P0 stub (09D v2 section 5.9). The unit
    is pinned to the DOLLAR PRICE DIFFERENCE PER SHARE of the held side's
    same token: the raw float subtraction — no rounding, no share
    multiplication. Position size lives in the ledger store
    (``entries.shares``), so unrealized PnL composes as
    ``shares * mark_to_market(entry_price, mark)`` with a mark quoted on
    the entry's own side/token (0.56 -> 0.54 is -0.02 per share).
    """
    _require_finite(entry_price, "entry_price")
    if not 0.0 < entry_price < 1.0:
        raise ValueError("entry_price must be in the open interval (0, 1)")
    _require_finite(exit_bid, "exit_bid")
    if not 0.0 <= exit_bid <= 1.0:
        raise ValueError("exit_bid must be in the closed interval [0, 1]")
    return exit_bid - entry_price


def papersim_dsn(env: Mapping[str, str] | None = None) -> str:
    """Return the validated paper-sim ledger DSN.

    Resolution order:

    1. ``PAPERSIM_PG_DSN`` from ``env`` (default: the process
       environment, read inside the sanctioned config module
       ``supabase_papersim_config``), when set to a non-empty value.
    2. The documented example default
      ``postgresql://paper_sim@127.0.0.1:55432/paper_sim`` (loopback
       paper_sim cluster; no password embedded in the DSN).

    The resolved value always passes through the single audited
    ``validate_local_postgres_dsn`` with ``env_var_name="PAPERSIM_PG_DSN"``
    (both inside ``SupabasePapersimConfig``), so hosted hosts,
    keyword-DSN socket escapes and sqlite/jsonl file fallbacks are
    rejected here rather than at connect time.

    Password convention (documented only; never default-read, never
    printed, never committed): the SCRAM password for the ``paper_sim``
    role is supplied out-of-band via ``PAPERSIM_PG_PASSWORD_FILE`` — an
    environment variable holding the path of a one-line password file
    owned by the operator. This function never reads, logs or commits
    that file or its contents, and no default path exists. Only a
    connection adapter explicitly directed to connect may read the path
    at connect time and must never include it in logs or errors.
    """
    return from_papersim_db_env(env).resolved_dsn()


def classify_outcome(outcome: str | None) -> str:
    """Classify a resolution outcome as ``open`` / ``exited`` / ``settled``.

    ``None`` (no resolution row yet) means the position is still open.
    ``"EXITED"`` is a discipline exit: realized trading PnL, but never a
    win or a loss for forecast scoring. ``"YES"`` / ``"NO"`` /
    ``"YES-basket"`` are natural market resolutions. Any other token is
    unknown data and raises instead of being silently bucketed
    (``null``, ``0`` and ``unknown`` stay distinguishable).
    """
    if outcome is None:
        return OUTCOME_OPEN
    if outcome == EXITED_OUTCOME:
        return OUTCOME_EXITED
    if outcome in NATURAL_OUTCOMES:
        return OUTCOME_SETTLED
    raise ValueError(f"unknown resolution outcome: {outcome!r}")


@dataclass(frozen=True)
class PaperSettlement:
    """Realized result of one paper position: ``pnl = proceeds - stake``.

    ``pnl`` keeps its GROSS meaning (``proceeds - stake``) so existing
    anchors and tests stay byte-identical (FX-01). ``fee_total``
    (default ``0.0`` — no fee information supplied) carries the
    taker fees charged on the position when the caller passes fee
    rates; the read-only property ``pnl_net`` returns
    ``pnl - fee_total``, the net-of-fees realization.
    """

    classification: str  # OUTCOME_SETTLED | OUTCOME_EXITED
    proceeds: float
    pnl: float
    fee_total: float = 0.0

    @property
    def pnl_net(self) -> float:
        """Net realization after fees: ``pnl - fee_total``."""
        return self.pnl - self.fee_total

    @property
    def win_loss(self) -> str | None:
        """``"win"`` / ``"loss"`` for natural resolutions; ``None`` for exits.

        Encodes the fixed-view rule that an ``EXITED`` position realizes
        PnL (included in ``total_pnl``) but is excluded from the win and
        loss counts (``EXITED`` is not W/L).
        """
        if self.classification != OUTCOME_SETTLED:
            return None
        return "win" if self.pnl > 0.0 else "loss"


def _require_finite(value: float, name: str) -> None:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")


def _require_stake_and_price(stake: float, price: float) -> None:
    _require_finite(stake, "stake")
    _require_finite(price, "price")
    if stake <= 0:
        raise ValueError("stake must be positive")
    if not 0.0 < price < 1.0:
        raise ValueError("price must be in the open interval (0, 1)")


def _require_fee_rate(rate: float, name: str) -> None:
    """Validate an optional fee-rate argument (finite, non-negative)."""
    if type(rate) not in (int, float) or not math.isfinite(rate):
        raise ValueError(f"{name} must be a finite number, got {rate!r}")
    if rate < 0:
        raise ValueError(f"{name} must be non-negative, got {rate!r}")


def _natural_hit(side: str, outcome: str) -> bool:
    """Mirror the fixed ``forecast_calibration`` hit expression verbatim.

    ``YES`` and ``YES-basket`` sides hit when the natural outcome equals
    the side; ``NO`` and ``NO-basket`` sides hit when the outcome is
    ``NO`` (basket legs resolve on the natural ``NO``).
    """
    if side in ("YES", "YES-basket"):
        return outcome == side
    if side in ("NO", "NO-basket"):
        return outcome == "NO"
    raise ValueError(f"unknown side: {side!r}")


def settle_natural(
    stake: float,
    price: float,
    side: str,
    outcome: str,
    *,
    fee_rate: float | None = None,
) -> PaperSettlement:
    """Settle a naturally resolved position (win pays shares at $1.00).

    A win pays ``proceeds = stake / price`` (payout per $1.00 of payoff);
    a loss pays ``proceeds = 0``. In both cases ``pnl = proceeds - stake``
    (GROSS — unchanged meaning). ``EXITED`` is not a natural resolution
    and is rejected here — use :func:`settle_exit` for discipline exits.

    Net-PnL layer (FX-01): with the keyword-only ``fee_rate`` (taken
    from :func:`polymarket_alpha_lab.papersim.fees.taker_fee_rate`,
    never a hardcoded number), the entry fee
    ``shares * fee_rate * price * (1 - price)`` with
    ``shares = stake / price`` — exactly ``stake * fee_rate *
    (1 - price)`` — is charged into ``fee_total``. A natural settlement
    has no exit leg, so no exit fee exists. ``fee_rate`` of 0 is valid
    (fee-free categories); ``None`` (default) keeps ``fee_total = 0.0``
    and ``pnl_net == pnl``.
    """
    _require_stake_and_price(stake, price)
    if outcome not in NATURAL_OUTCOMES:
        raise ValueError(
            "settle_natural accepts natural resolutions only "
            f"({'/'.join(NATURAL_OUTCOMES)}); got {outcome!r} — use settle_exit for EXITED"
        )
    fee_total = 0.0
    if fee_rate is not None:
        _require_fee_rate(fee_rate, "fee_rate")
        fee_total = _entry_fee(stake, price, fee_rate)
    proceeds = stake / price if _natural_hit(side, outcome) else 0.0
    return PaperSettlement(
        classification=OUTCOME_SETTLED,
        proceeds=proceeds,
        pnl=proceeds - stake,
        fee_total=fee_total,
    )


def settle_exit(
    stake: float,
    price: float,
    exit_price: float,
    *,
    fee_rate: float | None = None,
    exit_fee_rate: float | None = None,
) -> PaperSettlement:
    """Settle a discipline exit at ``exit_price`` (``pnl = proceeds - stake``).

    The exit realizes ``proceeds = (stake / price) * exit_price`` on the
    full position; the result is classified ``exited`` so it carries PnL
    but never a win/loss label (book-of-record example: $400 NO @ 0.690
    exited at 0.585 -> proceeds 339.30 on 580 recorded shares, pnl -60.70
    gross; the exact per-share arithmetic is -60.87 on 579.7101 shares).

    Net-PnL layer (FX-01): ``fee_rate`` charges the entry leg
    (``stake * fee_rate * (1 - price)``) and ``exit_fee_rate`` the sell
    leg (``shares * exit_fee_rate * exit_price * (1 - exit_price)`` on
    ``shares = stake / price``); each is optional and independent, and
    ``fee_total`` is their sum (V-6 evidence run: exits add sell fees).
    Rates come from :func:`polymarket_alpha_lab.papersim.fees.taker_fee_rate`;
    ``None`` (default) keeps both legs uncharged (``fee_total = 0.0``).
    """
    _require_stake_and_price(stake, price)
    _require_finite(exit_price, "exit_price")
    if not 0.0 <= exit_price <= 1.0:
        raise ValueError("exit_price must be in the closed interval [0, 1]")
    shares = stake / price
    fee_total = 0.0
    if fee_rate is not None:
        _require_fee_rate(fee_rate, "fee_rate")
        fee_total += _entry_fee(stake, price, fee_rate)
    if exit_fee_rate is not None:
        _require_fee_rate(exit_fee_rate, "exit_fee_rate")
        fee_total += _exit_fee(shares, exit_price, exit_fee_rate)
    proceeds = shares * exit_price
    return PaperSettlement(
        classification=OUTCOME_EXITED,
        proceeds=proceeds,
        pnl=proceeds - stake,
        fee_total=fee_total,
    )


def bankroll_identity(
    bankroll: float,
    cash: float,
    deployed: float,
    *,
    tolerance: float = 0.005,
) -> bool:
    """Check the paper bankroll identity ``bankroll == cash + deployed``.

    The live book of record reconciles to exactly this identity (round-12
    recompute row and every subsequent row; e.g. 2026-11-01:
    12374.33 = 9488.68 cash + 2885.65 deployed). ``tolerance`` is a
    half-cent guard for money recorded at cent precision and float
    arithmetic; tighten or loosen it explicitly when needed.
    """
    for value, name in (
        (bankroll, "bankroll"),
        (cash, "cash"),
        (deployed, "deployed"),
    ):
        _require_finite(value, name)
    _require_finite(tolerance, "tolerance")
    if tolerance < 0.0:
        raise ValueError("tolerance must be non-negative")
    return abs(bankroll - (cash + deployed)) <= tolerance


# Net-PnL aggregation (FX-01, the minimal predecessor of the FX-11
# evaluation module): gross and net per-queue performance over settled
# ledger rows, using the V-6 evidence-run fee semantics.
PNL_SOURCE_RECOMPUTED = "recomputed"
PNL_SOURCE_RECORDED = "recorded"
_PNL_SOURCES = (PNL_SOURCE_RECOMPUTED, PNL_SOURCE_RECORDED)


@dataclass(frozen=True)
class QueuePerformance:
    """Gross/net performance of one ``market_type`` queue."""

    market_type: str
    categories: tuple[str, ...]  # distinct fee categories seen in the queue
    entries: int
    stake: float
    gross_pnl: float
    entry_fees: float
    exit_fees: float
    net_pnl: float  # gross_pnl - entry_fees - exit_fees

    @property
    def gross_roi(self) -> float | None:
        return self.gross_pnl / self.stake if self.stake else None

    @property
    def net_roi(self) -> float | None:
        return self.net_pnl / self.stake if self.stake else None


@dataclass(frozen=True)
class LedgerPerformance:
    """Aggregate gross/net performance over settled ledger rows.

    ``net_pnl`` nets entry AND exit fees; ``net_pnl_entry_fees_only``
    nets the entry leg alone (both are reported because the V-6
    evidence run kept exit sell fees as a separate variant). ``ROI``
    properties return ``None`` when no stake was counted. Net figures
    are an ESTIMATE back-computed with the fee schedule as of
    ``fee_schedule_as_of`` — whether the historical markets were
    actually charged, and at which tier, is not verifiable from the
    repo.
    """

    entries: int
    voided_excluded: int
    unresolved_excluded: int
    stake: float
    gross_pnl: float
    entry_fees: float
    exit_fees: float
    queues: tuple[QueuePerformance, ...]  # sorted by market_type
    team_classification: tuple[tuple[str, str, float], ...]  # (team, category, rate)
    fee_schedule_as_of: str

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.entry_fees - self.exit_fees

    @property
    def net_pnl_entry_fees_only(self) -> float:
        return self.gross_pnl - self.entry_fees

    @property
    def gross_roi(self) -> float | None:
        return self.gross_pnl / self.stake if self.stake else None

    @property
    def net_roi(self) -> float | None:
        return self.net_pnl / self.stake if self.stake else None

    @property
    def net_roi_entry_fees_only(self) -> float | None:
        """Net ROI (entry fees only); ``None`` when no stake was counted."""
        return self.net_pnl_entry_fees_only / self.stake if self.stake else None


def _row_outcome(row: Mapping[str, object]) -> str | None:
    """Normalize a row's outcome token; ``None`` means unresolved."""
    raw = row.get("outcome")
    if raw is None:
        return None
    token = str(raw).strip()
    return token or None


def _queue_row_pnl(
    row: Mapping[str, object],
    outcome: str,
    stake: float,
    price: float,
    pnl_source: str,
) -> tuple[float, bool]:
    """Gross PnL for one counted row and whether it is an exit.

    ``recorded`` mode uses the row's recorded ``pnl`` for every counted
    row (reproduces a book-of-record export as published, including its
    market-level joins). ``recomputed`` mode (V-6 primary) recomputes
    natural resolutions and the post-audit ``win``/``loss`` labels from
    first principles (``stake``/``price``/``side``), and takes the
    recorded ``pnl`` for ``EXITED`` rows because the exit price is not
    stored (V-6: all five live exits were single-entry markets).
    """
    if pnl_source == PNL_SOURCE_RECORDED or outcome == EXITED_OUTCOME:
        recorded = row.get("pnl")
        if recorded is None:
            raise ValueError(
                "a recorded 'pnl' is required for pnl_source="
                f"{PNL_SOURCE_RECORDED!r} rows and for {EXITED_OUTCOME!r} rows"
            )
        return float(recorded), outcome == EXITED_OUTCOME
    if outcome in SETTLED_LABEL_OUTCOMES:
        return (
            (stake / price - stake) if outcome == WIN_OUTCOME else -stake,
            False,
        )
    settlement = settle_natural(stake, price, str(row["side"]), outcome)
    return settlement.pnl, False


def queue_performance(
    rows: Iterable[Mapping[str, object]],
    *,
    pnl_source: str = PNL_SOURCE_RECOMPUTED,
) -> LedgerPerformance:
    """Aggregate settled ledger rows into gross and net per-queue performance.

    Rows are ``decisions``-shaped mappings (``team``, ``market_type``,
    ``side``, ``stake``, ``price``, ``outcome`` and — for exits and
    recorded mode — ``pnl``). Outcome handling mirrors the V-6 evidence
    run: unresolved rows (no outcome) and ``VOIDED`` rows (stake
    refunded) are excluded and counted; natural ``YES`` / ``NO`` /
    ``YES-basket``, the post-audit ``win`` / ``loss`` labels and
    ``EXITED`` rows are counted. Fees use the scan classification
    (:func:`polymarket_alpha_lab.papersim.fees.legacy_scan_fee_category`) and
    the category table — entry fee per row, plus the sell fee on
    ``EXITED`` rows at the exit price reconstructed from the recorded
    PnL (``(pnl + stake) / shares``).

    ``pnl_source`` selects the gross basis: ``"recomputed"`` (V-6
    primary, default) or ``"recorded"`` (reproduce an export as
    published). Pure computation, no I/O; the caller labels the net
    figures as schedule-back-computed estimates.
    """
    if pnl_source not in _PNL_SOURCES:
        raise ValueError(
            f"pnl_source must be one of {_PNL_SOURCES}, got {pnl_source!r}"
        )
    queue_acc: dict[str, dict[str, float]] = {}
    team_rates: dict[str, tuple[str, float]] = {}
    entries = 0
    voided = 0
    unresolved = 0
    stake_total = 0.0
    gross_total = 0.0
    entry_fees_total = 0.0
    exit_fees_total = 0.0
    for row in rows:
        outcome = _row_outcome(row)
        if outcome is None:
            unresolved += 1
            continue
        if outcome == VOIDED_OUTCOME:
            voided += 1
            continue
        if (
            outcome not in NATURAL_OUTCOMES
            and outcome not in SETTLED_LABEL_OUTCOMES
            and outcome != EXITED_OUTCOME
        ):
            raise ValueError(f"unknown resolution outcome: {outcome!r}")
        team = str(row.get("team") or "")
        market_type = str(row.get("market_type") or "unknown")
        stake = float(row["stake"])
        price = float(row["price"])
        _require_stake_and_price(stake, price)
        category = _legacy_scan_fee_category(team, market_type)
        rate = _taker_fee_rate(category)
        team_rates.setdefault(team, (category, rate))
        gross, is_exit = _queue_row_pnl(row, outcome, stake, price, pnl_source)
        fee_entry = _entry_fee(stake, price, rate)
        fee_exit = 0.0
        if is_exit:
            shares = stake / price
            exit_price = (gross + stake) / shares
            fee_exit = _exit_fee(shares, exit_price, rate)
        entries += 1
        stake_total += stake
        gross_total += gross
        entry_fees_total += fee_entry
        exit_fees_total += fee_exit
        if market_type not in queue_acc:
            queue_acc[market_type] = {
                "categories": set(),
                "entries": 0,
                "stake": 0.0,
                "gross_pnl": 0.0,
                "entry_fees": 0.0,
                "exit_fees": 0.0,
            }
        acc = queue_acc[market_type]
        acc["categories"].add(category)
        acc["entries"] += 1
        acc["stake"] += stake
        acc["gross_pnl"] += gross
        acc["entry_fees"] += fee_entry
        acc["exit_fees"] += fee_exit
    queues = tuple(
        QueuePerformance(
            market_type=market_type,
            categories=tuple(sorted(acc["categories"])),
            entries=int(acc["entries"]),
            stake=acc["stake"],
            gross_pnl=acc["gross_pnl"],
            entry_fees=acc["entry_fees"],
            exit_fees=acc["exit_fees"],
            net_pnl=acc["gross_pnl"] - acc["entry_fees"] - acc["exit_fees"],
        )
        for market_type, acc in sorted(queue_acc.items())
    )
    return LedgerPerformance(
        entries=entries,
        voided_excluded=voided,
        unresolved_excluded=unresolved,
        stake=stake_total,
        gross_pnl=gross_total,
        entry_fees=entry_fees_total,
        exit_fees=exit_fees_total,
        queues=queues,
        team_classification=tuple(
            (team, category, rate)
            for team, (category, rate) in sorted(team_rates.items())
        ),
        fee_schedule_as_of=_FEE_SCHEDULE_AS_OF,
    )


_MIGRATION_DDL = """\
-- WP-P1 W4: paper-sim ledger DDL mirrored from the live paper_sim
-- evidence cluster (loopback 127.0.0.1:55432, pulled 2026-11-01).
-- Mirrored verbatim: decisions / resolutions / bankroll tables and the
-- FIXED model_performance / forecast_calibration views (exits separated
-- from W/L; forecast calibration judged on natural resolutions only).
-- Documented repo-convention deltas versus the live cluster: the
-- paper_only / report_only / readonly boundary columns on decisions,
-- matching the repo paper_trade migration style; and the
-- avg_prob_of_realized hit predicate adds the NO-basket clause
-- (codex-review F5) so it is identical to the forecast_hits predicate:
-- a winning NO-basket row contributes model_prediction, not its
-- complement.
-- OPERATIONAL NOTE: the LIVE paper_sim cluster forecast_calibration
-- view still carries the one-clause-short avg_prob_of_realized
-- predicate and needs this same one-line fix applied operationally by
-- an operator; this migration never touches the live cluster. This
-- schema is paper evidence storage only; it grants no order, account
-- or execution authorization.

create table public.decisions (
  id bigserial primary key,
  ts timestamptz default now(),
  market_id text,
  question text,
  side text,
  price numeric,
  shares numeric,
  stake numeric,
  fair_low numeric,
  fair_high numeric,
  spot numeric,
  vol numeric,
  horizon_days numeric,
  sizing text,
  reason text,
  team text default 'crypto_eth'::text,
  model_prediction numeric,
  market_type text,
  paper_only boolean not null default true check (paper_only is true),
  report_only boolean not null default true check (report_only is true),
  readonly boolean not null default true check (readonly is true)
);

create table public.resolutions (
  market_id text primary key,
  ts timestamptz default now(),
  outcome text,
  payoff numeric,
  pnl numeric,
  evidence text
);

create table public.bankroll (
  ts timestamptz default now(),
  cash numeric,
  deployed numeric,
  note text
);

create view public.model_performance as
select d.market_type,
       count(*) as total_settled,
       sum(case when r.outcome = any (array['YES'::text, 'NO'::text, 'YES-basket'::text]) and r.pnl > 0::numeric then 1 else 0 end) as wins,
       sum(case when r.outcome = any (array['YES'::text, 'NO'::text, 'YES-basket'::text]) and r.pnl <= 0::numeric then 1 else 0 end) as losses,
       sum(case when r.outcome = 'EXITED'::text then 1 else 0 end) as exits,
       round(100.0 * sum(case when r.outcome = any (array['YES'::text, 'NO'::text, 'YES-basket'::text]) and r.pnl > 0::numeric then 1 else 0 end)::numeric
             / nullif(sum(case when r.outcome = any (array['YES'::text, 'NO'::text, 'YES-basket'::text]) then 1 else 0 end), 0)::numeric, 1) as win_rate_pct,
       round(sum(r.pnl), 2) as total_pnl,
       round(avg(d.model_prediction), 3) as avg_model_prediction
from public.decisions d
  join public.resolutions r on r.market_id = d.market_id
group by d.market_type;

create view public.forecast_calibration as
select d.market_type,
       count(*) as n,
       sum(case when (d.side = any (array['YES'::text, 'YES-basket'::text])) and r.outcome = d.side
                 or d.side = 'NO'::text and r.outcome = 'NO'::text
                 or d.side = 'NO-basket'::text and r.outcome = 'NO'::text
                then 1 else 0 end) as forecast_hits,
       round(avg(d.model_prediction), 3) as avg_forecast,
       round(avg(case when (d.side = any (array['YES'::text, 'YES-basket'::text])) and r.outcome = d.side
                       or d.side = 'NO'::text and r.outcome = 'NO'::text
                       or d.side = 'NO-basket'::text and r.outcome = 'NO'::text
                  then d.model_prediction
                  else 1::numeric - d.model_prediction end), 3) as avg_prob_of_realized
from public.decisions d
  join public.resolutions r on r.market_id = d.market_id
where r.outcome = any (array['YES'::text, 'NO'::text, 'YES-basket'::text])
group by d.market_type;
"""


def ddl() -> str:
    """Return the paper-sim ledger migration text (see the module docstring).

    The returned text is byte-identical to the checked-in migration
    ``supabase/migrations/20261101000000_papersim_ledger.sql`` and is the
    single DDL source for the ledger surface: the mirrored live
    ``decisions`` / ``resolutions`` / ``bankroll`` tables plus the fixed
    ``model_performance`` / ``forecast_calibration`` views and the
    repo-convention paper-only/report-only/readonly delta on ``decisions``.
    It is descriptive evidence for tests and tooling; applying it remains
    the exclusive job of the transactional native-Postgres migration
    ledger, never this module.
    """
    return _MIGRATION_DDL
