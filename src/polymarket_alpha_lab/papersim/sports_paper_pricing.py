"""Best displayed ask used for small CNY simulated entries and watches."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import math

from polymarket_alpha_lab.papersim import gates


PRICE_BASIS = "best_ask"


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
