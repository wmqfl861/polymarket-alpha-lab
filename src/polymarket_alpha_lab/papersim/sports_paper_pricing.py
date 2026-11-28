"""Best displayed ask used for small CNY simulated entries and watches."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import math
import re

from polymarket_alpha_lab.papersim import gates


PRICE_BASIS = "best_ask"
CNY_PER_USD = Decimal("6.654376")
CURRENCY_RATE_VERSION = "ecb-cross-2026-11-27-v1"
CURRENCY_RATE_REFERENCE = {
    "source": "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
    "reference_day": "2026-11-27",
    "eur_usd": "1.0957",
    "eur_cny": "7.2912",
    "canonical_response_sha256": "04260ffcc31751b874cb7d3d7468df4c0ea76cf5bd4ca229d65e93e23c0eca62",
    "observed_at_utc": "2026-11-28T07:56:30.438598+00:00",
    "cny_per_usd": str(CNY_PER_USD),
}
BOOK_EVIDENCE_FIELDS = (
    "best_ask_price", "best_ask_size", "book_snapshot_sha256", "book_snapshot_encoding",
    "book_token_id", "book_condition_id", "book_observed_at", "currency_rate",
)


def best_ask(levels: Iterable) -> float | None:
    lowest = None
    for level in levels:
        try:
            price, size = (level["price"], level["size"]) if isinstance(level, Mapping) else level
            if isinstance(price, bool) or isinstance(size, bool):
                continue
            price, size = float(price), float(size)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if 0 < price < 1 and size > 0 and math.isfinite(size):
            lowest = price if lowest is None else min(price, lowest)
    return lowest


def _best_ask_details(levels: Iterable) -> tuple[Decimal, Decimal] | None:
    """Return the lowest valid ask and all shares resting at that exact price."""
    lowest = None
    size_at_lowest = Decimal(0)
    for level in levels:
        try:
            price, size = (level["price"], level["size"]) if isinstance(level, Mapping) else level
            if isinstance(price, bool) or isinstance(size, bool):
                continue
            price, size = Decimal(str(price)), Decimal(str(size))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
        if (not price.is_finite() or not size.is_finite() or not 0 < float(price) < 1
                or not 0 < float(size) < math.inf):
            continue
        if lowest is None or price < lowest:
            lowest, size_at_lowest = price, size
        elif price == lowest:
            with localcontext() as ctx:
                ctx.prec = 50
                size_at_lowest += size
    return (lowest, size_at_lowest) if lowest is not None else None


def book_evidence(raw_book, *, token_id, condition_id, observed_at):
    """Bind the displayed ask to the complete canonical JSON book snapshot."""
    if not isinstance(raw_book, dict) or not isinstance(token_id, str) or not isinstance(condition_id, str):
        return None
    if raw_book.get("asset_id") != token_id or raw_book.get("market") != condition_id:
        return None
    if not isinstance(raw_book.get("asks"), list):
        return None
    details = _best_ask_details(raw_book["asks"])
    if details is None or not isinstance(observed_at, datetime) or observed_at.utcoffset() is None:
        return None
    try:
        canonical = json.dumps(raw_book, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    price, size = details
    return {
        "best_ask_price": format(price, "f"),
        "best_ask_size": format(size, "f"),
        "book_snapshot_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "book_snapshot_encoding": "canonical_json",
        "book_token_id": token_id,
        "book_condition_id": condition_id,
        "book_observed_at": observed_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "currency_rate": {
            "version": CURRENCY_RATE_VERSION,
            "cny_per_usd": format(CNY_PER_USD, "f"),
            "reference": CURRENCY_RATE_REFERENCE,
        } if observed_at >= datetime.fromisoformat(CURRENCY_RATE_REFERENCE["observed_at_utc"]) else None,
    }


def best_ask_coverage(signal, *, price, fee_per_share, stake_fen, token_id, condition_id, observed_at):
    """Compare displayed USD notional to the whole stake and to shares needed after fees.

    EUR cross-rates are fixed for prospective reports only. This calculation
    does not change the simulation's allocation, odds, units, fees or payouts.
    """
    if not isinstance(signal, Mapping) or not signal.get("book_snapshot_sha256"):
        return {"status": "unknown", "reason": "missing_snapshot"}
    digest = signal["book_snapshot_sha256"]
    if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or signal.get("book_snapshot_encoding") != "canonical_json"
            or signal.get("book_token_id") != token_id or signal.get("book_condition_id") != condition_id):
        return {"status": "unknown", "reason": "invalid_snapshot_identity"}
    try:
        at = datetime.fromisoformat(signal["book_observed_at"])
        ask = Decimal(str(signal["best_ask_price"]))
        shares = Decimal(str(signal["best_ask_size"]))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return {"status": "unknown", "reason": "invalid_snapshot"}
    if (at.utcoffset() is None or at != observed_at or not ask.is_finite() or not shares.is_finite()
            or ask != price or not 0 < ask < 1 or shares <= 0):
        return {"status": "unknown", "reason": "invalid_snapshot"}
    rate = signal.get("currency_rate")
    if (not isinstance(rate, Mapping) or rate.get("version") != CURRENCY_RATE_VERSION
            or rate.get("cny_per_usd") != str(CNY_PER_USD)
            or rate.get("reference") != CURRENCY_RATE_REFERENCE
            or at < datetime.fromisoformat(CURRENCY_RATE_REFERENCE["observed_at_utc"])):
        return {"status": "unknown", "reason": "missing_registered_rate"}
    with localcontext() as ctx:
        ctx.prec = 50
        spend_cny = Decimal(stake_fen).scaleb(-2)
        unit_cost = ask + fee_per_share
        spend_usd = spend_cny / CNY_PER_USD
        required_shares = spend_usd / unit_cost
        # Cross-multiply to avoid classifying against rounded quotients.
        notional_usd = ask * shares
        notional_covers_stake = notional_usd * CNY_PER_USD >= spend_cny
        shares_cover_after_fees = shares * unit_cost * CNY_PER_USD >= spend_cny
        return {
            "status": "sufficient" if notional_covers_stake else "insufficient",
            "fee_inclusive_share_status": "sufficient" if shares_cover_after_fees else "insufficient",
            "rate_version": rate["version"], "cny_per_usd": rate["cny_per_usd"],
            "stake_usd_including_fee": str(spend_usd), "required_shares": str(required_shares),
            "best_ask_notional_usd": str(notional_usd),
        }


@dataclass(frozen=True)
class PaperPrice:
    entry_price: float
    exec_edge: float
    exec_edge_haircut: float
    worst_variant_edge: float
    variant_spread: float
    decision: gates.GateDecision


def evaluate_candidate(quote, asks, *, news, fee_rate, under_risk=False) -> PaperPrice | None:
    price = best_ask(asks)
    if price is None:
        return None
    median = float(quote["model_median"])
    variants = tuple(float(p) for p in quote["qs_variants"])
    if (not variants or not 0 <= median <= 1 or not all(0 <= p <= 1 for p in variants)
            or not 0 <= fee_rate <= 1):
        return None

    spread = round(max(variants) - min(variants), 4)
    worst_edge = round(min(variants) - price, 4)
    gross_edge = round(median - price, 4)
    net_edge = gates._apply_fee_and_haircut(gross_edge, price, fee_rate)
    adjusted_edge = gates.haircut_edge(net_edge, median)
    exec_min = gates.UNDER_RISK_EXEC_EDGE_MIN if under_risk else gates.GATES["exec_edge_min"]
    checks = {
        "variant_spread": spread <= gates.GATES["variant_spread_max"] + gates.GATE_EPSILON,
        "worst_edge": worst_edge >= gates.GATES["worst_edge_min"] - gates.GATE_EPSILON,
        "exec_edge": adjusted_edge >= exec_min - gates.GATE_EPSILON,
        "entry_window": gates.ENTRY_WINDOW[0] <= price <= gates.ENTRY_WINDOW[1],
        "news_concordant": news is not None and news.concordant,
    }
    failed = tuple(name for name, passed in checks.items() if not passed)
    return PaperPrice(price, net_edge, adjusted_edge, worst_edge, spread,
                      gates.GateDecision(passed=not failed, failed=failed))
