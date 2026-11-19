#!/usr/bin/env python3
"""Independent recomputation of the 2026-11-19 NBA hold-out log-loss table.

Reviewer's own read-only script (2026-11-19 audit). It reads the committed
`predictions.csv` only and recomputes binary log loss and Brier score from the
stored probabilities and outcomes; it never imports or runs repository code.

Rows are grouped by season, split, market and source. A row enters the paired
comparison only when baseline, optimized and market probabilities are all present.

Usage: python3 -I recompute_nba.py <predictions.csv>
"""
from __future__ import annotations

import csv
import math
import sys
from collections import defaultdict


def num(value: str) -> float | None:
    return float(value) if value not in ("", None) else None


def logloss(p: float, y: int) -> float:
    p = min(max(p, 1e-15), 1 - 1e-15)
    return -math.log(p if y == 1 else 1 - p)


def main() -> None:
    groups: dict[tuple[str, str, str, str], list[tuple[int, float | None, float | None, float | None]]] = defaultdict(list)
    with open(sys.argv[1], newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["outcome"] in ("", None):
                continue
            key = (row["season"], row["split"], row["market"], row["source"])
            groups[key].append((int(float(row["outcome"])), num(row["p_baseline"]), num(row["p_optimized"]), num(row["p_market"])))
    print("season   split    market source              rows paired | logloss baseline / optimized / market | Brier baseline / optimized / market")
    for key in sorted(groups):
        rows = groups[key]
        paired = [r for r in rows if r[1] is not None and r[2] is not None and r[3] is not None]
        if not paired:
            base_only = [r for r in rows if r[1] is not None]
            if base_only:
                ll = sum(logloss(r[1], r[0]) for r in base_only) / len(base_only)
                print(f"{key[0]:8s} {key[1]:8s} {key[2]:6s} {key[3]:18s} {len(rows):5d}      0 | baseline-only logloss {ll:.4f} (n={len(base_only)})")
            continue
        n = len(paired)
        ll = [sum(logloss(r[i], r[0]) for r in paired) / n for i in (1, 2, 3)]
        br = [sum((r[i] - r[0]) ** 2 for r in paired) / n for i in (1, 2, 3)]
        print(f"{key[0]:8s} {key[1]:8s} {key[2]:6s} {key[3]:18s} {len(rows):5d} {n:6d} | {ll[0]:.4f} / {ll[1]:.4f} / {ll[2]:.4f} | {br[0]:.4f} / {br[1]:.4f} / {br[2]:.4f}")


if __name__ == "__main__":
    main()
