"""Shared CNY paper account for qualified football and basketball recommendations."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Context, Decimal, DecimalException, InvalidOperation, ROUND_HALF_UP, localcontext
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

import football_store as store


RESTART_KEY = os.environ.get("PAL_PAPER_ACCOUNT_KEY") or None
OPENING_CASH_FEN = 1_000_000
BEIJING = ZoneInfo("Asia/Shanghai")
SPORTS = ("football", "basketball")
RESULTS = ("win", "loss", "refund", "split")
BASIS_POINTS = {"low": 50, "medium": 100, "high": 150}
SCHEMA_LOCK = 70231
MAX_EVIDENCE_BYTES = 32_768
# Formal feeds round the raw edge once to four decimals. A fee-adjusted edge
# can carry both that rounding and the four-decimal per-share fee rounding.
GROSS_EDGE_ROUNDING = Decimal("0.000050000001")
NET_EDGE_ROUNDING = Decimal("0.000100000001")

SCHEMA = """
CREATE SCHEMA IF NOT EXISTS football_notify;

CREATE TABLE IF NOT EXISTS football_notify.sports_portfolio_account (
    restart_key text PRIMARY KEY,
    started_at timestamptz NOT NULL,
    opening_cash_fen bigint NOT NULL CHECK (opening_cash_fen > 0)
);

CREATE TABLE IF NOT EXISTS football_notify.sports_portfolio_position (
    restart_key text NOT NULL REFERENCES football_notify.sports_portfolio_account (restart_key),
    round_key text NOT NULL,
    round_order integer NOT NULL CHECK (round_order > 0),
    sport text NOT NULL CHECK (sport IN ('football', 'basketball')),
    fixture_key text NOT NULL,
    fixture_revision integer NOT NULL CHECK (fixture_revision > 0),
    model_version text NOT NULL,
    market_id text NOT NULL,
    condition_id text NOT NULL,
    outcome text NOT NULL,
    direction text,
    token_id text NOT NULL,
    kickoff timestamptz NOT NULL,
    observed_at timestamptz NOT NULL,
    valid_until timestamptz NOT NULL,
    opened_at timestamptz NOT NULL,
    settlement_evidence jsonb NOT NULL CHECK (jsonb_typeof(settlement_evidence) = 'object'),
    model_probability numeric NOT NULL CHECK (model_probability BETWEEN 0 AND 1),
    price numeric NOT NULL CHECK (price > 0 AND price < 1),
    fee_rate numeric NOT NULL CHECK (fee_rate BETWEEN 0 AND 1),
    confidence text NOT NULL CHECK (confidence IN ('low', 'medium', 'high')),
    stake_fen bigint NOT NULL CHECK (stake_fen > 0),
    fee_fen bigint NOT NULL CHECK (fee_fen >= 0 AND fee_fen < stake_fen),
    units numeric NOT NULL CHECK (units > 0),
    result text CHECK (result IN ('win', 'loss', 'refund', 'split')),
    payout_fen bigint CHECK (payout_fen >= 0),
    resolved_at timestamptz,
    result_evidence jsonb CHECK (jsonb_typeof(result_evidence) = 'object'),
    PRIMARY KEY (restart_key, sport, fixture_key),
    UNIQUE (restart_key, market_id),
    UNIQUE (restart_key, round_key, round_order),
    CHECK (observed_at <= opened_at AND opened_at < LEAST(valid_until, kickoff)),
    CHECK ((result IS NULL) = (payout_fen IS NULL)),
    CHECK ((result IS NULL) = (resolved_at IS NULL)),
    CHECK ((result IS NULL) = (result_evidence IS NULL)),
    CHECK (resolved_at >= opened_at),
    CHECK (result NOT IN ('win', 'loss') OR resolved_at > kickoff),
    CHECK (result != 'refund' OR payout_fen = stake_fen),
    CHECK (result != 'loss' OR payout_fen = 0)
);

CREATE INDEX IF NOT EXISTS sports_portfolio_open_by_kickoff
ON football_notify.sports_portfolio_position (restart_key, kickoff) WHERE result IS NULL;
"""


class PortfolioError(store.StoreError):
    """A fixed reason code for the notification or settlement worker."""


class AllocationChanged(PortfolioError):
    """The quote or amount changed after preview."""

    def __init__(self):
        super().__init__("portfolio_allocation_changed")


def _decimal(value):
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("invalid portfolio number") from error
    if not numeric.is_finite():
        raise ValueError("invalid portfolio number")
    return numeric


def _probability(value):
    probability = _decimal(value)
    if not 0 <= probability <= 1:
        raise ValueError("invalid portfolio probability")
    return probability


def _label(value, limit=256):
    if (not isinstance(value, str) or not 0 < len(value) <= limit or value.strip() != value
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError("invalid portfolio label")
    return value


def _evidence(value):
    if not isinstance(value, dict):
        raise ValueError("portfolio evidence must be a JSON object")
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    if len(encoded) > MAX_EVIDENCE_BYTES:
        raise ValueError("portfolio evidence exceeds the size limit")
    return json.loads(encoded)


@dataclass(frozen=True)
class ValueMetrics:
    break_even_probability: Decimal
    odds: Decimal
    expected_roi: Decimal
    conservative_probability: Decimal
    conservative_roi: Decimal
    tier: str | None


@dataclass(frozen=True)
class Quote:
    sport: str
    fixture_key: str
    market_id: str
    outcome: str
    model_probability: Decimal
    price: Decimal
    fee_rate: Decimal
    observed_at: datetime
    valid_until: datetime
    kickoff: datetime
    token_id: str
    fixture_revision: int
    model_version: str
    condition_id: str
    direction: str | None = None
    settlement_evidence: dict | None = None

    def __post_init__(self):
        if self.sport not in SPORTS:
            raise ValueError("invalid portfolio sport")
        for value in (self.fixture_key, self.market_id, self.outcome, self.token_id,
                      self.model_version, self.condition_id):
            _label(value)
        if self.direction is not None:
            _label(self.direction)
        if type(self.fixture_revision) is not int or self.fixture_revision < 1:
            raise ValueError("invalid fixture revision")
        for field in ("model_probability", "price", "fee_rate"):
            object.__setattr__(self, field, _decimal(getattr(self, field)))
        for field in ("observed_at", "valid_until", "kickoff"):
            object.__setattr__(self, field, store.timestamp(getattr(self, field)))
        if not 0 <= self.model_probability <= 1 or not 0 <= self.fee_rate <= 1 or not 0 < self.price < 1:
            raise ValueError("invalid quote economics")
        if not self.observed_at < min(self.valid_until, self.kickoff):
            raise ValueError("invalid quote window")
        evidence = {} if self.settlement_evidence is None else _evidence(self.settlement_evidence)
        object.__setattr__(self, "settlement_evidence", evidence)

    @property
    def value(self) -> ValueMetrics:
        """Value the frozen quote using the explicit bounds from its signal."""
        signal = self.settlement_evidence.get("signal")
        if signal is None:
            return value_metrics(self.model_probability, self.price, self.fee_rate)
        if not isinstance(signal, dict):
            raise ValueError("invalid portfolio signal evidence")
        variants = signal.get("variants")
        if variants is not None and not isinstance(variants, list):
            raise ValueError("invalid portfolio signal variants")
        weakest = [_probability(value) for value in variants or ()]
        if signal.get("min_variant") is not None:
            weakest.append(_probability(signal["min_variant"]))
        return _value_metrics(
            self.model_probability, self.price, self.fee_rate,
            worst_probability=min(weakest, default=None), worst_edge=signal.get("worst_edge"),
            worst_net_edge=signal.get("worst_net_edge"), prefer_exact_signal=True,
        )


@dataclass(frozen=True)
class Allocation:
    quote: Quote
    confidence: str
    stake_fen: int
    fee_fen: int
    units: Decimal


def _nearest_fen(yuan):
    return int((yuan * 100).to_integral_value(rounding=ROUND_HALF_UP))


def format_cny(fen):
    """Display an integer number of Chinese cents as yuan."""
    sign = "-" if fen < 0 else ""
    whole, cents = divmod(abs(fen), 100)
    return f"{sign}¥{whole:,}.{cents:02d}"


def _unit_fee(price, rate):
    return rate * price * (1 - price)


def value_tier(conservative_roi):
    """Map a positive fee-inclusive ROI to the shared account's value tier."""
    roi = _decimal(conservative_roi)
    if roi <= 0:
        return None
    if roi >= Decimal("0.20"):
        return "high"
    return "medium" if roi >= Decimal("0.10") else "low"


def _edge_probability(basis, edge, rounding):
    edge = _decimal(edge)
    if not -1 <= edge <= 1:
        raise ValueError("invalid portfolio edge")
    probability = basis + edge
    if not -rounding <= probability <= 1 + rounding:
        raise ValueError("invalid portfolio edge probability")
    return min(Decimal(1), max(Decimal(0), probability))


def _value_metrics(probability, price, fee_rate, *, worst_probability, worst_edge, worst_net_edge,
                   prefer_exact_signal=False):
    probability = _probability(probability)
    price, fee_rate = _decimal(price), _decimal(fee_rate)
    if not 0 < price < 1 or not 0 <= fee_rate <= 1:
        raise ValueError("invalid quote economics")
    exact = None if worst_probability is None else _probability(worst_probability)
    try:
        with localcontext(Context(prec=50)):
            cost = price + _unit_fee(price, fee_rate)
            if not 0 < cost <= 1:
                raise ValueError("invalid portfolio cost")
            conservative = min(probability, exact) if exact is not None else probability
            for edge, basis, rounding in ((worst_edge, price, GROSS_EDGE_ROUNDING),
                                          (worst_net_edge, cost, NET_EDGE_ROUNDING)):
                if edge is None:
                    continue
                bound = _edge_probability(basis, edge, rounding)
                # The existing signals serialize rounded edges derived from the
                # same variants. Use their precise bound at a tier boundary.
                if prefer_exact_signal and exact is not None and abs(bound - exact) <= rounding:
                    continue
                conservative = min(conservative, bound)
            conservative_roi = conservative / cost - 1
            return ValueMetrics(cost, 1 / cost, probability / cost - 1, conservative,
                                conservative_roi, value_tier(conservative_roi))
    except DecimalException as error:
        raise ValueError("invalid portfolio valuation arithmetic") from error


def value_metrics(probability, price, fee_rate, *, worst_probability=None, worst_edge=None, worst_net_edge=None):
    """Compute payout odds and per-yuan ROI using the least optimistic supplied probability."""
    return _value_metrics(probability, price, fee_rate, worst_probability=worst_probability,
                          worst_edge=worst_edge, worst_net_edge=worst_net_edge)


def decimal_odds(price, fee_rate):
    """Total winning payout per yuan of cost, including the trade's fee."""
    price, fee_rate = _decimal(price), _decimal(fee_rate)
    if not 0 < price < 1 or not 0 <= fee_rate <= 1:
        raise ValueError("invalid quote economics")
    return 1 / (price + _unit_fee(price, fee_rate))


def _allocation(quote, confidence, stake_fen):
    with localcontext() as context:
        context.prec = 50
        # papersim.fees.fee_per_share uses this formula with floats. CNY bookkeeping uses Decimal.
        fee_per_unit = _unit_fee(quote.price, quote.fee_rate)
        # The qualifying ask VWAP is the price ratio for CNY simulation. The source's USD
        # depth check provides the price, without transferring its USD notional into this account.
        units = Decimal(stake_fen).scaleb(-2) / (quote.price + fee_per_unit)
        fee_fen = _nearest_fen(units * fee_per_unit)
    return Allocation(quote, confidence, stake_fen, fee_fen, units)


def _installed(connection):
    return connection.execute("""
        SELECT to_regclass('football_notify.sports_portfolio_account') IS NOT NULL AS installed
    """).fetchone()["installed"]


def status(connection, *, restart_key=RESTART_KEY):
    """Read CNY totals in integer fen, with one consistent snapshot across the sports."""
    empty = {"state": "uninitialized", "restart_key": restart_key, "currency": "CNY"}
    if not _installed(connection):
        return empty
    rows = connection.execute("""
        WITH totals AS (
            SELECT CASE WHEN GROUPING(sport) = 0 THEN 'sport'
                        WHEN GROUPING(confidence) = 0 THEN 'confidence' END AS group_name,
                   coalesce(sport, confidence) AS group_value,
                   count(*) AS entries,
                   count(*) FILTER (WHERE result IS NULL) AS open_positions,
                   count(result) AS settled_positions,
                   count(*) FILTER (WHERE result = 'win') AS wins,
                   count(*) FILTER (WHERE result = 'loss') AS losses,
                   count(*) FILTER (WHERE result = 'refund') AS refunds,
                   count(*) FILTER (WHERE result = 'split') AS splits,
                   coalesce(sum(stake_fen), 0) AS staked_fen,
                   coalesce(sum(stake_fen) FILTER (WHERE result IS NULL), 0) AS at_risk_fen,
                   coalesce(sum(payout_fen), 0) AS settled_payout_fen,
                   coalesce(sum(payout_fen - stake_fen), 0) AS realized_pnl_fen
            FROM football_notify.sports_portfolio_position WHERE restart_key = %s
            GROUP BY GROUPING SETS ((sport), (confidence), ())
        )
        SELECT started_at, opening_cash_fen, totals.*
        FROM football_notify.sports_portfolio_account CROSS JOIN totals WHERE restart_key = %s
    """, (restart_key, restart_key)).fetchall()
    if not rows:
        return empty
    total = next(row for row in rows if row["group_name"] is None)
    metrics = {name: int(value) for name, value in total.items()
               if name not in ("group_name", "group_value", "started_at", "opening_cash_fen")}
    groups = {
        "sport": {sport: dict.fromkeys(metrics, 0) for sport in SPORTS},
        "confidence": {confidence: dict.fromkeys(metrics, 0) for confidence in BASIS_POINTS},
    }
    for row in rows:
        if row["group_name"] is not None:
            groups[row["group_name"]][row["group_value"]] = {name: int(row[name]) for name in metrics}
    equity = total["opening_cash_fen"] + metrics["realized_pnl_fen"]
    return empty | metrics | {
        "state": "active", "started_at": total["started_at"],
        "started_at_beijing": total["started_at"].astimezone(BEIJING).isoformat(timespec="seconds"),
        "opening_cash_fen": total["opening_cash_fen"], "equity_fen": equity,
        "available_cash_fen": equity - metrics["at_risk_fen"],
        "by_sport": groups["sport"], "by_confidence": groups["confidence"],
    }


def restart(connection, *, restart_key=RESTART_KEY, opening_cash_fen=OPENING_CASH_FEN):
    """Initialize the authorized generation at the database clock, once per restart key."""
    _label(restart_key)
    if type(opening_cash_fen) is not int or not 0 < opening_cash_fen < 2**63:
        raise ValueError("invalid opening cash")
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(%s, %s)", (store.LOCK_NAMESPACE, SCHEMA_LOCK))
        connection.execute(SCHEMA)
        connection.execute("""
            INSERT INTO football_notify.sports_portfolio_account (restart_key, started_at, opening_cash_fen)
            VALUES (%s, clock_timestamp(), %s) ON CONFLICT (restart_key) DO NOTHING
        """, (restart_key, opening_cash_fen))
        account = status(connection, restart_key=restart_key)
        if account["opening_cash_fen"] != opening_cash_fen:
            raise PortfolioError("portfolio_opening_cash_conflict")
        return account


def _allocate(connection, quotes, restart_key, now, limit):
    account = status(connection, restart_key=restart_key)
    cash = account.get("available_cash_fen", 0)
    if cash <= 0 or not quotes or limit == 0:
        return []
    existing = connection.execute("""
        SELECT sport, fixture_key, market_id FROM football_notify.sports_portfolio_position
        WHERE restart_key = %s AND (fixture_key = ANY(%s) OR market_id = ANY(%s))
    """, (restart_key, [quote.fixture_key for quote in quotes], [quote.market_id for quote in quotes])).fetchall()
    used_fixtures = {(row["sport"], row["fixture_key"]) for row in existing}
    used_markets = {row["market_id"] for row in existing}
    ranked = []
    for quote in quotes:
        if not account["started_at"] <= quote.observed_at <= now < min(quote.valid_until, quote.kickoff):
            continue
        try:
            value = quote.value
        except ValueError:
            continue
        if value.tier is not None:
            ranked.append((quote, value))
    ranked.sort(key=lambda item: (item[1].conservative_roi, item[1].expected_roi), reverse=True)

    allocations = []
    for quote, value in ranked:
        fixture = quote.sport, quote.fixture_key
        if fixture in used_fixtures or quote.market_id in used_markets:
            continue
        confidence = value.tier
        stake_fen = min(cash, account["equity_fen"] * BASIS_POINTS[confidence] // 10_000)
        if stake_fen <= 0:
            continue
        allocations.append(_allocation(quote, confidence, stake_fen))
        cash -= stake_fen
        used_fixtures.add(fixture)
        used_markets.add(quote.market_id)
        if cash == 0 or len(allocations) == limit:
            break
    return allocations


def plan(connection, quotes, *, now=None, restart_key=RESTART_KEY, limit=None):
    """Preview allocations by conservative and then expected ROI, with stable ties."""
    now = store.timestamp(now) if now is not None else store.db_now(connection)
    return _allocate(connection, tuple(quotes), restart_key, now, limit)


def _lock_account(connection, restart_key):
    if not _installed(connection) or connection.execute("""
        SELECT 1 FROM football_notify.sports_portfolio_account WHERE restart_key = %s FOR UPDATE
    """, (restart_key,)).fetchone() is None:
        raise PortfolioError("portfolio_not_initialized")


QUOTE_FIELDS = tuple(field.name for field in fields(Quote))
ALLOCATION_FIELDS = ("confidence", "stake_fen", "fee_fen", "units")
INSERT_FIELDS = ("restart_key", "round_key", "round_order", "opened_at", *QUOTE_FIELDS, *ALLOCATION_FIELDS)


def enter(connection, quotes, *, round_key, expected=None, restart_key=RESTART_KEY, limit=None):
    """Book valid quotes at database time and replay earlier entries for the same round."""
    _label(round_key)
    with connection.transaction():
        _lock_account(connection, restart_key)
        rows = connection.execute("""
            SELECT * FROM football_notify.sports_portfolio_position
            WHERE restart_key = %s AND round_key = %s ORDER BY round_order
        """, (restart_key, round_key)).fetchall()
        now = store.db_now(connection)
        if rows:
            allocations = [Allocation(Quote(**{name: row[name] for name in QUOTE_FIELDS}),
                                      **{name: row[name] for name in ALLOCATION_FIELDS}) for row in rows]
        else:
            allocations = _allocate(connection, tuple(quotes), restart_key, now, limit)
        if expected is not None and allocations != list(expected):
            raise AllocationChanged()
        if not rows and allocations:
            inserts = []
            for order, item in enumerate(allocations, start=1):
                values = {name: getattr(item.quote, name) for name in QUOTE_FIELDS}
                values["settlement_evidence"] = Jsonb(_evidence(values["settlement_evidence"]))
                values.update({name: getattr(item, name) for name in ALLOCATION_FIELDS})
                values.update(restart_key=restart_key, round_key=round_key, round_order=order, opened_at=now)
                inserts.append(values)
            with connection.cursor() as cursor:
                cursor.executemany(
                    f"INSERT INTO football_notify.sports_portfolio_position ({', '.join(INSERT_FIELDS)}) "
                    "VALUES (" + ", ".join(f"%({name})s" for name in INSERT_FIELDS) + ")", inserts,
                )
        return allocations


def open_positions(connection, *, restart_key=RESTART_KEY, due_before=None, limit=None):
    """Return unsettled rows and the frozen contract references for result collection."""
    if not _installed(connection):
        return []
    due = store.timestamp(due_before) if due_before is not None else None
    return connection.execute("""
        SELECT * FROM football_notify.sports_portfolio_position
        WHERE restart_key = %s AND result IS NULL AND (%s::timestamptz IS NULL OR kickoff <= %s)
        ORDER BY kickoff, sport, fixture_key LIMIT %s
    """, (restart_key, due, due, limit)).fetchall()


def statement(connection, *, restart_key=RESTART_KEY, limit=50):
    """Read recent individual entries and their CNY payouts, newest round first."""
    if not _installed(connection):
        return []
    return connection.execute("""
        SELECT sport, fixture_key, kickoff, market_id, outcome, direction,
               settlement_evidence->'fixture'->>'home' AS home,
               settlement_evidence->'fixture'->>'away' AS away,
               confidence, model_probability, price, stake_fen, fee_fen, opened_at,
               result, payout_fen, payout_fen - stake_fen AS pnl_fen, resolved_at
        FROM football_notify.sports_portfolio_position WHERE restart_key = %s
        ORDER BY opened_at DESC, round_key, round_order LIMIT %s
    """, (restart_key, limit)).fetchall()


def _payout(row, result):
    if result == "refund":
        return row["stake_fen"]
    rates = {"win": Decimal(1), "loss": Decimal(0), "split": Decimal("0.5")}
    with localcontext() as context:
        context.prec = 50
        return _nearest_fen(row["units"] * rates[result])


def settle(connection, *, sport, fixture_key, market_id, outcome, result, evidence, resolved_at,
           restart_key=RESTART_KEY, token_id=None, condition_id=None, fixture_revision=None, model_version=None):
    """Settle by the original contract once, using the verified result and its observed time."""
    if result not in RESULTS:
        raise ValueError("invalid portfolio result")
    resolved = store.timestamp(resolved_at)
    proof = _evidence(evidence)
    if not proof:
        raise ValueError("empty portfolio result evidence")
    with connection.transaction():
        _lock_account(connection, restart_key)
        row = connection.execute("""
            SELECT * FROM football_notify.sports_portfolio_position
            WHERE restart_key = %s AND sport = %s AND fixture_key = %s
        """, (restart_key, sport, fixture_key)).fetchone()
        if row is None:
            raise PortfolioError("portfolio_position_missing")
        references = {"token_id": token_id, "condition_id": condition_id,
                      "fixture_revision": fixture_revision, "model_version": model_version}
        if ((row["market_id"], row["outcome"]) != (market_id, outcome)
                or any(value is not None and row[key] != value for key, value in references.items())):
            raise PortfolioError("portfolio_contract_mismatch")
        if row["result"] is not None:
            if row["result"] != result:
                raise PortfolioError("portfolio_result_conflict")
            return row
        if (not row["opened_at"] <= resolved <= store.db_now(connection)
                or (result in ("win", "loss") and resolved <= row["kickoff"])):
            raise PortfolioError("portfolio_result_time_invalid")
        return connection.execute("""
            UPDATE football_notify.sports_portfolio_position
            SET result = %s, payout_fen = %s, resolved_at = %s, result_evidence = %s
            WHERE restart_key = %s AND sport = %s AND fixture_key = %s RETURNING *
        """, (result, _payout(row, result), resolved, Jsonb(proof), restart_key, sport, fixture_key)).fetchone()


def _present_cny(account):
    amounts = {f"{key.removesuffix('_fen')}_cny": format_cny(value) if value is not None else None
               for key, value in account.items() if key.endswith("_fen")}
    return account | amounts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("restart", "status", "statement"))
    parser.add_argument("--restart-key", default=RESTART_KEY)
    parser.add_argument("--limit", type=int, default=50, help="maximum rows in a statement")
    args = parser.parse_args(argv)
    if args.limit < 0:
        parser.error("--limit must be nonnegative")
    if not args.restart_key:
        parser.error("provide --restart-key or configure PAL_PAPER_ACCOUNT_KEY")
    with store.connect(read_only=args.command != "restart") as connection:
        if args.command == "statement":
            report = [
                _present_cny(row) | {"opened_at_beijing": row["opened_at"].astimezone(BEIJING).isoformat(timespec="seconds")}
                for row in statement(connection, restart_key=args.restart_key, limit=args.limit)
            ]
        else:
            operation = restart if args.command == "restart" else status
            report = _present_cny(operation(connection, restart_key=args.restart_key))
            for key in ("by_sport", "by_confidence"):
                if key in report:
                    report[key] = {name: _present_cny(row) for name, row in report[key].items()}
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
