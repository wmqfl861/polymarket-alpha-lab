#!/usr/bin/env python3
"""Spread of per-match paired log-loss differences, used to size forward samples.

Reviewer's own read-only script (2026-11-19 audit). It reads the committed
new-soccer `holdout_predictions.csv` only and recomputes every log loss from
the stored probabilities; it never imports or runs repository code. The small
ASEAN cohort (21 matches) is left out.

For 1X2 and O/U 2.5 it prints the mean and standard deviation of the per-match
difference (tuned minus baseline, tuned minus market) and the number of
independent matches needed to detect a given mean improvement with a one-sided
2.5% test at 80% power: n = ((1.96 + 0.84) * sd / delta) ** 2.

Usage: python3 -I paired_diff_spread.py <holdout_predictions.csv>
"""
from __future__ import annotations

import csv
import math
import statistics
import sys


def logloss(row: dict, prefix: str) -> float | None:
    if row["market"] == "1x2":
        value = {"home": row[f"{prefix}_p_home"], "draw": row[f"{prefix}_p_draw"],
                 "away": row[f"{prefix}_p_away"]}[row["result"]]
    else:
        value = row[f"{prefix}_p_over"] if row["result"] == "over" else row[f"{prefix}_p_under"]
    return -math.log(float(value)) if value not in ("", None) else None


def main() -> None:
    with open(sys.argv[1], newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["league"] != "asean"]
    for market in ("1x2", "ou2.5"):
        versus_baseline, versus_market = [], []
        for row in rows:
            if row["market"] != market:
                continue
            tuned, baseline, quoted = logloss(row, "tuned"), logloss(row, "baseline"), logloss(row, "market")
            if tuned is not None and baseline is not None:
                versus_baseline.append(tuned - baseline)
            if tuned is not None and quoted is not None:
                versus_market.append(tuned - quoted)
        sd = statistics.pstdev(versus_baseline)
        print(f"{market}: matches={len(versus_baseline)} tuned-baseline mean={statistics.mean(versus_baseline):+.5f} sd={sd:.4f}")
        for delta in (0.010, 0.005):
            print(f"    matches needed to detect {delta:.3f}: {((1.96 + 0.84) * sd / delta) ** 2:.0f}")
        print(f"    tuned-market: matches={len(versus_market)} mean={statistics.mean(versus_market):+.5f} "
              f"sd={statistics.pstdev(versus_market):.4f}")


if __name__ == "__main__":
    main()
