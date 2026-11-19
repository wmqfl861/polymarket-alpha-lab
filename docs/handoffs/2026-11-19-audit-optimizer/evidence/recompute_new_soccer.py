#!/usr/bin/env python3
"""Independent recomputation of the 2026-11-19 new-soccer hold-out comparison.

Reviewer's own read-only script (2026-11-19 audit). It reads the committed
`holdout_predictions.csv` only; it never imports or runs repository code and
does not reuse the per-row Brier/log-loss columns stored in the file: every
score is recomputed here from the probabilities and the recorded result.

For each league x market it reports, on the rows that carry a market quote
("matched" cohort): n, Brier of the fixed baseline, the tuned model and the
de-vigged market, and a paired ISO-week block bootstrap interval for
(baseline - tuned) and (market - tuned)  [positive = tuned better].
It then counts how many of the cells would survive a Holm correction, which
the published report says was not applied.

Usage: python3 -I recompute_new_soccer.py <holdout_predictions.csv>
"""
from __future__ import annotations

import csv
import math
import random
import sys
from collections import defaultdict
from datetime import date

REPS = 2000
SEED = 20261119


def f(value: str) -> float | None:
    return float(value) if value not in ("", None) else None


def scores(row: dict, prefix: str) -> tuple[float, float] | None:
    """(brier, logloss) for one model on one row, recomputed from probabilities."""
    market = row["market"]
    result = row["result"]
    if market == "1x2":
        probs = [f(row[f"{prefix}_p_home"]), f(row[f"{prefix}_p_draw"]), f(row[f"{prefix}_p_away"])]
        if any(p is None for p in probs):
            return None
        index = {"home": 0, "draw": 1, "away": 2}[result]
        brier = sum((p - (1.0 if i == index else 0.0)) ** 2 for i, p in enumerate(probs))
        return brier, -math.log(max(probs[index], 1e-15))
    p_over, p_under = f(row[f"{prefix}_p_over"]), f(row[f"{prefix}_p_under"])
    if p_over is None or p_under is None:
        return None
    hit = {"over": 1.0, "under": 0.0}[result]
    return (p_over - hit) ** 2, -math.log(max(p_over if hit else p_under, 1e-15))


def week_key(day: str) -> tuple[int, int]:
    iso = date.fromisoformat(day).isocalendar()
    return iso[0], iso[1]


def block_ci(blocks: list[list[float]], rng: random.Random) -> tuple[float, float]:
    means = []
    for _ in range(REPS):
        total = count = 0.0
        for _ in blocks:
            block = blocks[rng.randrange(len(blocks))]
            total += sum(block)
            count += len(block)
        means.append(total / count)
    means.sort()
    return means[int(0.025 * REPS)], means[int(0.975 * REPS) - 1]


def p_two_sided(blocks: list[list[float]], rng: random.Random) -> float:
    """Sign-flip permutation p-value on block sums (paired differences)."""
    sums = [sum(b) for b in blocks]
    observed = abs(sum(sums))
    extreme = 0
    for _ in range(REPS):
        if abs(sum(s if rng.random() < 0.5 else -s for s in sums)) >= observed - 1e-15:
            extreme += 1
    return (extreme + 1) / (REPS + 1)


def main() -> None:
    path = sys.argv[1]
    cells: dict[tuple[str, str], list[dict]] = defaultdict(list)
    total_rows = 0
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            total_rows += 1
            cells[(row["league"], row["market"])].append(row)
    print(f"rows={total_rows} cells={len(cells)}")
    print("league market   n_all n_match | Brier baseline / tuned / market | base-tuned [95% CI] | market-tuned [95% CI]")
    rng = random.Random(SEED)
    tests: list[tuple[float, str, float]] = []
    tests_market: list[tuple[float, str, float]] = []
    for (league, market), rows in sorted(cells.items()):
        matched = []
        for row in rows:
            b, t, m = scores(row, "baseline"), scores(row, "tuned"), scores(row, "market")
            if b and t and m:
                matched.append((row["day"], b, t, m))
        if not matched:
            print(f"{league:6s} {market:7s} {len(rows):5d}     0 | no market-matched rows")
            continue
        n = len(matched)
        brier = [sum(x[i][0] for x in matched) / n for i in (1, 2, 3)]
        blocks_bt: dict[tuple[int, int], list[float]] = defaultdict(list)
        blocks_mt: dict[tuple[int, int], list[float]] = defaultdict(list)
        for day, b, t, m in matched:
            blocks_bt[week_key(day)].append(b[0] - t[0])
            blocks_mt[week_key(day)].append(m[0] - t[0])
        bt, mt = list(blocks_bt.values()), list(blocks_mt.values())
        ci_bt, ci_mt = block_ci(bt, rng), block_ci(mt, rng)
        gain_bt, gain_mt = brier[0] - brier[1], brier[2] - brier[1]
        tests.append((p_two_sided(bt, rng), f"{league}/{market}", gain_bt))
        tests_market.append((p_two_sided(mt, rng), f"{league}/{market}", gain_mt))
        print(f"{league:6s} {market:7s} {len(rows):5d} {n:5d} | {brier[0]:.6f} / {brier[1]:.6f} / {brier[2]:.6f} | "
              f"{gain_bt:+.6f} [{ci_bt[0]:+.6f},{ci_bt[1]:+.6f}] | {gain_mt:+.6f} [{ci_mt[0]:+.6f},{ci_mt[1]:+.6f}] weeks={len(bt)}")
    for label, family in (("tuned vs baseline", tests), ("tuned vs market", tests_market)):
        ordered = sorted(family)
        k = len(ordered)
        better = sum(1 for _, _, gain in ordered if gain > 0)
        nominal = [(p, name, gain) for p, name, gain in ordered if p < 0.05]
        holm = []
        for rank, (p, name, gain) in enumerate(ordered):
            if p <= 0.05 / (k - rank):
                holm.append((p, name, gain))
            else:
                break
        print(f"{label}: cells={k} tuned better in {better}; nominal p<0.05: {len(nominal)} "
              f"({sum(1 for _, _, g in nominal if g > 0)} in favour of tuned); "
              f"after Holm: {len(holm)} ({sum(1 for _, _, g in holm if g > 0)} in favour of tuned)")
        for p, name, gain in nominal:
            print(f"    {name}: gain={gain:+.6f} p={p:.4f}{' (survives Holm)' if (p, name, gain) in holm else ''}")


if __name__ == "__main__":
    main()
