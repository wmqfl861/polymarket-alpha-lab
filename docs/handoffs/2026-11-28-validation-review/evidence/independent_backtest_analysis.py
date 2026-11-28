#!/usr/bin/env python3
"""Auditor's own read-only analysis of the published five-league replay export.

Input: docs/paper-sim/research/sports-historical-20261128/new-five-fixtures.json.gz
(per fixture: frozen-model probabilities, de-vigged bookmaker closing probabilities, final score).
Nothing is fetched and nothing is written; the report goes to standard output.

Usage: python3 -I independent_backtest_analysis.py <path to new-five-fixtures.json.gz>

Questions asked of the data (all on fixtures that have both a model and a market probability):
  1. calibration slope of the model and of the market (logistic recalibration);
  2. does the model carry information the closing market does not? (logit regression of the
     outcome on both; week-block bootstrap of the model coefficient);
  3. the Brier-optimal linear blend weight of the model against the market;
  4. how far shrinking the model towards the league base rate closes the gap;
  5. what following the model's disagreements would have returned at fair closing prices;
  6. how many fixtures a comparison of this kind needs before an edge of a given size is visible;
  7. how much of the three-way gap a recalibration fitted on earlier fixtures closes on later ones;
  8. whether using xG beside goals helps the published model.
Python 3.10+ standard library only. Seeds are fixed, so the output is reproducible.
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict
from datetime import datetime
from math import exp, log, sqrt
from random import Random
from statistics import fmean, pstdev

EPS = 1e-9


def logit(p: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return log(p / (1 - p))


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def invert(a: list[list[float]]) -> list[list[float]]:
    n = len(a)
    cols = [solve(a, [1.0 if i == j else 0.0 for i in range(n)]) for j in range(n)]
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def logistic(xs: list[list[float]], ys: list[int]) -> tuple[list[float], list[float]]:
    """Maximum-likelihood logistic regression (intercept added); returns coefficients and model s.e."""
    k = len(xs[0]) + 1
    beta = [0.0] * k
    hess = [[0.0] * k for _ in range(k)]
    for _ in range(40):
        grad = [0.0] * k
        hess = [[0.0] * k for _ in range(k)]
        for x, y in zip(xs, ys):
            row = [1.0] + x
            z = sum(b * v for b, v in zip(beta, row))
            p = 1 / (1 + exp(-max(min(z, 35), -35)))
            w = p * (1 - p)
            for i in range(k):
                grad[i] += (y - p) * row[i]
                for j in range(k):
                    hess[i][j] += w * row[i] * row[j]
        step = solve(hess, grad)
        beta = [b + s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-10:
            break
    cov = invert(hess)
    return beta, [sqrt(max(cov[i][i], 0.0)) for i in range(k)]


def block_bootstrap(rows, weeks, statistic, draws=400, seed=20261128):
    groups = defaultdict(list)
    for row, week in zip(rows, weeks):
        groups[week].append(row)
    blocks = [groups[w] for w in sorted(groups)]
    rng = Random(seed)
    values = []
    for _ in range(draws):
        sample = [r for block in rng.choices(blocks, k=len(blocks)) for r in block]
        values.append(statistic(sample))
    values.sort()
    return values[int((len(values) - 1) * 0.025)], values[int((len(values) - 1) * 0.975)]


def blend_weight(rows) -> float:
    """w minimising mean((w*model + (1-w)*market - y)^2); unconstrained closed form."""
    num = sum((m - q) * (y - q) for m, q, y in rows)
    den = sum((m - q) ** 2 for m, q, y in rows)
    return num / den if den else 0.0


def brier(rows, pick) -> float:
    return fmean((pick(m, q) - y) ** 2 for m, q, y in rows)


def analyse(name: str, rows: list[tuple[float, float, int]], weeks: list) -> None:
    n = len(rows)
    base = fmean(y for _, _, y in rows)
    b_model, b_market = brier(rows, lambda m, q: m), brier(rows, lambda m, q: q)
    print(f"\n== {name}: n={n}, outcome rate={base:.4f}")
    print(f"Brier: model {b_model:.5f}, market {b_market:.5f}, constant base rate {base * (1 - base):.5f}")
    for label, idx in (("model", 0), ("market", 1)):
        beta, se = logistic([[logit(r[idx])] for r in rows], [r[2] for r in rows])
        print(f"calibration slope of the {label}: {beta[1]:.3f} (s.e. {se[1]:.3f}); 1.000 = well calibrated")
    beta, se = logistic([[logit(q), logit(m)] for m, q, _ in rows], [r[2] for r in rows])
    lo, hi = block_bootstrap(
        rows, weeks, lambda s: logistic([[logit(q), logit(m)] for m, q, _ in s], [r[2] for r in s])[0][2]
    )
    print(
        f"outcome on logit(market) and logit(model): market {beta[1]:.3f} (s.e. {se[1]:.3f}), "
        f"model {beta[2]:.3f} (s.e. {se[2]:.3f}; week-block 95% interval {lo:.3f} to {hi:.3f})"
    )
    w = blend_weight(rows)
    lo, hi = block_bootstrap(rows, weeks, blend_weight)
    b_blend = brier(rows, lambda m, q: w * m + (1 - w) * q)
    print(
        f"Brier-optimal blend weight on the model: {w:.3f} (week-block 95% interval {lo:.3f} to {hi:.3f}); "
        f"in-sample blend Brier {b_blend:.5f} against market {b_market:.5f}"
    )
    best = min(
        ((lam / 20, brier(rows, lambda m, q, lam=lam: lam / 20 * m + (1 - lam / 20) * base)) for lam in range(21)),
        key=lambda t: t[1],
    )
    print(
        f"shrinking the model towards the base rate: best weight on the model {best[0]:.2f}, "
        f"Brier {best[1]:.5f} (model unshrunk {b_model:.5f}, market {b_market:.5f}); in-sample"
    )
    print(f"mean absolute model-market difference: {100 * fmean(abs(m - q) for m, q, _ in rows):.2f} points")
    for threshold in (0.03, 0.05, 0.08, 0.10):
        returns = []
        for m, q, y in rows:
            if m - q > threshold:        # model says the outcome is under-priced: back it at fair odds 1/q
                returns.append(y / q - 1)
            elif q - m > threshold:      # model says over-priced: back the complement at 1/(1-q)
                returns.append((1 - y) / (1 - q) - 1)
        if returns:
            mean = fmean(returns)
            half = 1.96 * pstdev(returns) / sqrt(len(returns))
            print(
                f"following disagreements larger than {threshold:.2f} at fair closing prices: "
                f"{len(returns)} bets, mean return {100 * mean:+.1f}% (naive 95% +/- {100 * half:.1f} points)"
            )
    deltas = [(m - y) ** 2 - (q - y) ** 2 for m, q, y in rows]
    sd = pstdev(deltas)
    print(f"per-fixture Brier difference: mean {fmean(deltas):+.5f}, s.d. {sd:.5f}")
    for effect in (0.002, 0.005, 0.010):
        print(
            f"  fixtures needed for a true difference of {effect:.3f} to give a 95% interval "
            f"excluding zero about half the time: {int((1.96 * sd / effect) ** 2):,}"
        )


def main() -> None:
    packet = json.loads(gzip.decompress(open(sys.argv[1], "rb").read()))
    rows = packet["reconstructed_rows"]
    print(f"model_version={packet['model_version']} code={packet['code'][:8]} fixtures={len(rows)}")
    sets = defaultdict(lambda: ([], []))
    for r in rows:
        week = datetime.fromisoformat(r["kickoff"]).isocalendar()[:2]
        home, away = r["score"]
        model, market = r["probabilities"], r["archived_bookmaker_close"]
        if model.get("total_2_5") and market.get("total_2_5"):
            item = (model["total_2_5"][0], market["total_2_5"][0], int(home + away > 2))
            for key in ("over 2.5, all leagues", f"over 2.5, {r['league']}"):
                sets[key][0].append(item)
                sets[key][1].append(week)
        if model.get("match_winner") and market.get("match_winner"):
            for i, (label, hit) in enumerate((("home win", home > away), ("draw", home == away), ("away win", away > home))):
                sets[f"{label}, all leagues"][0].append((model["match_winner"][i], market["match_winner"][i], int(hit)))
                sets[f"{label}, all leagues"][1].append(week)
    order = ["over 2.5, all leagues"] + sorted(k for k in sets if k.startswith("over 2.5, ") and "all" not in k)
    order += ["home win, all leagues", "draw, all leagues", "away win, all leagues"]
    for key in order:
        analyse(key, *sets[key])
    out_of_sample_recalibration(rows)
    xg_against_goals_only(rows)


def out_of_sample_recalibration(rows) -> None:
    """Fit a per-outcome logistic recalibration on the earlier half of the fixtures (by kickoff),
    apply it to the later half, renormalise, and score the three-way Brier there."""
    usable = sorted(
        (r for r in rows if r["probabilities"].get("match_winner") and r["archived_bookmaker_close"].get("match_winner")),
        key=lambda r: r["kickoff"],
    )
    half = len(usable) // 2
    train, test = usable[:half], usable[half:]

    def hit(r, i):
        home, away = r["score"]
        return int((home > away, home == away, away > home)[i])

    fits = [
        logistic([[logit(r["probabilities"]["match_winner"][i])] for r in train], [hit(r, i) for r in train])[0]
        for i in range(3)
    ]

    def three_way(r, probs):
        return sum((probs[i] - hit(r, i)) ** 2 for i in range(3))

    raw, recal, market = [], [], []
    for r in test:
        model = r["probabilities"]["match_winner"]
        adj = [1 / (1 + exp(-(fits[i][0] + fits[i][1] * logit(model[i])))) for i in range(3)]
        total = sum(adj)
        raw.append(three_way(r, model))
        recal.append(three_way(r, [v / total for v in adj]))
        market.append(three_way(r, r["archived_bookmaker_close"]["match_winner"]))
    print(f"\n== three-way result, recalibration fitted on the first {len(train)} fixtures and scored on the last {len(test)}")
    print("fitted slopes (home, draw, away): " + ", ".join(f"{f[1]:.3f}" for f in fits))
    print(
        f"three-way Brier on the later half: model as published {fmean(raw):.5f}, "
        f"model recalibrated {fmean(recal):.5f}, market {fmean(market):.5f}"
    )
    print(
        f"share of the model-market gap closed by recalibration alone: "
        f"{100 * (fmean(raw) - fmean(recal)) / (fmean(raw) - fmean(market)):.0f}%"
    )


def xg_against_goals_only(rows) -> None:
    for family in ("match_winner", "total_2_5"):
        pairs = [
            (r["model_scores"][family]["brier"], r["same_model_goals_only_scores"][family]["brier"])
            for r in rows
            if r.get("model_scores", {}).get(family) and r.get("same_model_goals_only_scores", {}).get(family)
        ]
        deltas = [a - b for a, b in pairs]
        print(
            f"\n== {family}: published model (goals and xG) against the same model on goals only, n={len(pairs)}: "
            f"Brier {fmean(a for a, _ in pairs):.5f} against {fmean(b for _, b in pairs):.5f}; "
            f"mean difference {fmean(deltas):+.5f} (naive 95% +/- {1.96 * pstdev(deltas) / sqrt(len(deltas)):.5f})"
        )


if __name__ == "__main__":
    main()
