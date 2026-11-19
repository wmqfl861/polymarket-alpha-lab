"""Run the WP-NBA baseline and past-only calibration against matching NBA lines."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import random
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from unittest.mock import patch

from collect_polymarket import DIAGNOSTIC, STRICT
from polymarket_alpha_lab.papersim import espn_nba, nba_model
import wp_nba_validate as wp

TUNING_WEIGHTS = (0.0, 0.25, 0.50, 0.75, 1.0)
TUNE_START = {"2026-27": "2026-11-10", "2025-26": "2026-01-01", "2024-25": "2025-01-01"}
TEST_START = {"2026-27": "2026-11-13", "2025-26": "2026-02-16", "2024-25": "2025-02-16"}
KINDS = ("ml", "spread", "total")
PM_STRICT = "PM-explicit-full-game-OT"
PM_DIAGNOSTIC = "PM-NBA-final-score-diagnostic"
COHORTS = ("all_game_ml", "DK-postfinal-close-proxy", PM_STRICT, PM_DIAGNOSTIC)


def mean(values):
    return statistics.fmean(values)


def median(values):
    return statistics.median(values)


def read_gzip(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_snapshot(data):
    teams = {team: {"display": name, "games": []}
             for team, (_, name) in espn_nba.NBA_TEAMS.items()}
    for game in data["games"]:
        for side, other in (("home", "away"), ("away", "home")):
            team, opponent = game[side], game[other]
            pos = espn_nba._possessions(game[f"{side}_poss_inputs"])
            if pos != game[f"{side}_poss"]:
                raise ValueError(f"invalid possessions for {game['event_id']} {team}")
            pts, opp_pts = game[f"{side}_pts"], game[f"{other}_pts"]
            existing = teams[team]["games"]
            d = game["date"]
            previous = date.fromisoformat(existing[-1]["date"]) if existing else None
            rest = (date.fromisoformat(d) - previous).days if previous is not None else None
            existing.append({
                "date": d, "home": side == "home", "opponent": opponent,
                "poss": pos, "pts": pts, "opp_pts": opp_pts, "ortg": 100.0 * pts / pos,
                "drtg": 100.0 * opp_pts / pos, "rest_days": rest, "b2b": rest == 1,
            })
    snapshot = {"generated_at": data["retrieved_at"], "source": espn_nba.SOURCE_KEY,
                "season": data["season"], "teams": teams, "injuries": {}}
    return espn_nba._validate_snapshot(snapshot)


def baseline_replay(data, snapshot):
    """Call the public predictor with the formal WP-NBA date barrier.

    Exact memoization reuses sigma calibration for the same truncated snapshot
    date and half-life only. Public fixture logic and the six variants run per game.
    """
    games = wp.pair_snapshot_games(snapshot)
    identity = {(g["date"], g["home"], g["away"]): g for g in data["games"]}
    day = None
    original_sigma = nba_model._sigma_pass
    date_cache = {}

    def cached_sigma(rows_by_day, paired_by_day, half_life, context):
        if half_life not in date_cache:
            date_cache[half_life] = (context, original_sigma(rows_by_day, paired_by_day, half_life, context))
        cached_context, fitted = date_cache[half_life]
        if cached_context != context:
            raise ValueError("same-day context changed")
        return fitted

    forecasts = []
    with patch.object(nba_model, "_sigma_pass", cached_sigma):
        for game in games:
            gdate = game["date"]
            if gdate != day:
                day = gdate
                counts = wp.prior_counts(snapshot, day)
                truncated = wp.truncate_snapshot(snapshot, day)
                date_cache.clear()
            h, a = game["home"], game["away"]
            ph, pa = counts[h], counts[a]
            if min(ph, pa) < wp.DEFAULT_MIN_PRIOR_GAMES:
                continue
            fixture = nba_model.NbaFixture(
                home=h, away=a, rest_home=game["rest_home"], rest_away=game["rest_away"],
                b2b_home=game["b2b_home"], b2b_away=game["b2b_away"],
            )
            prediction = nba_model.fixture_prediction(truncated, fixture, before_date=day)
            if prediction is None:
                raise RuntimeError(f"refused eligible game {day} {a}@{h}")
            raw = identity[(day, h, a)]
            forecasts.append({
                "event_id": raw["event_id"], "tip": raw["started"], "date": day, "home": h, "away": a,
                "prior_home": ph, "prior_away": pa, "neutral": raw["neutral"],
                "actual_margin": game["home_pts"] - game["away_pts"],
                "actual_total": game["home_pts"] + game["away_pts"],
                "home_win": int(game["home_pts"] > game["away_pts"]),
                "p_home": prediction.p_home, "exp_margin": prediction.exp_margin,
                "exp_total": prediction.exp_total, "sigma_margin": prediction.sigma_margin,
                "sigma_total": prediction.sigma_total,
                "margin_variants": [(v.exp_margin, v.sigma_margin) for v in prediction.variants],
                "total_variants": [(v.exp_total, v.sigma_total) for v in prediction.variants],
                "ml_variant_spread": prediction.p_home_variant_spread,
                "dk_proxy": raw.get("dk_proxy", {}),
            })
    return forecasts


def affine_fit(rows, target):
    xs = [row[f"exp_{target}"] for row in rows]
    ys = [row[f"actual_{target}"] for row in rows]
    xbar, ybar = mean(xs), mean(ys)
    var = math.fsum((x - xbar) ** 2 for x in xs)
    cov = math.fsum((x - xbar) * (y - ybar) for x, y in zip(xs, ys))
    ridge = 800.0 if target == "margin" else 512.0
    slope = min(2.0, max(0.0, (cov + ridge) / (var + ridge)))
    intercept = ybar - slope * xbar
    sigma = statistics.stdev(y - (intercept + slope * x) for x, y in zip(xs, ys))
    return {"intercept": intercept, "slope": slope, "residual_sigma": sigma, "ridge": ridge}


def change_distribution(variants, params, weight):
    if weight == 0:
        return variants
    gain = 1.0 - weight + weight * params["slope"]
    shift = weight * params["intercept"]
    sigma2 = params["residual_sigma"] ** 2
    return [(gain * mu + shift,
             math.sqrt((1.0 - weight) * sigma ** 2 + weight * sigma2))
            for mu, sigma in variants]


def prob(variants, line):
    return median(0.5 * math.erfc(-(mu - line) / (math.sqrt(2) * sigma))
                  for mu, sigma in variants)


def nll(p, y):
    p = min(1 - 1e-12, max(1e-12, p))
    return -math.log(p) if y else -math.log1p(-p)


def validation_loss(rows, target, params, weight):
    losses = []
    for row in rows:
        variants = change_distribution(row[f"{target}_variants"], params, weight)
        if target == "margin":
            losses.append(nll(prob(variants, 0.0), row["home_win"]))
        else:
            # Continuous Gaussian mixture, with outcome scoring on the same
            # timestamped totals lines only after the model has been frozen.
            actual = row["actual_total"]
            dens = mean(math.exp(-0.5 * ((actual - mu) / sigma) ** 2)
                        / (math.sqrt(2 * math.pi) * sigma) for mu, sigma in variants)
            losses.append(-math.log(dens))
    return mean(losses)


def model_selection(forecasts, season):
    train = [row for row in forecasts if row["date"] < TUNE_START[season]]
    tune = [row for row in forecasts if TUNE_START[season] <= row["date"] < TEST_START[season]]
    test = [row for row in forecasts if row["date"] >= TEST_START[season]]
    if min(len(train), len(tune), len(test)) < 10:
        raise ValueError(f"insufficient chronological buckets: {season}")
    model = {}
    for target in ("margin", "total"):
        fit = affine_fit(train, target)
        losses = [(validation_loss(tune, target, fit, w), w) for w in TUNING_WEIGHTS]
        model[target] = {"fit": fit, "weight": min(losses)[1],
                         "tune_losses": {str(w): loss for loss, w in losses}}
    splits = {name: {"n": len(rows), "dates": [rows[0]["date"], rows[-1]["date"]]}
              for name, rows in (("train", train), ("tune", tune), ("holdout", test))}
    return model, splits


def season_rows(forecasts, season, market_data, model):
    clob = {r["event_id"]: r["quotes"] for r in market_data.get("rows", [])}
    output = []
    for row in forecasts:
        split = ("holdout" if row["date"] >= TEST_START[season] else "tune"
                 if row["date"] >= TUNE_START[season] else "train")
        base = {
            "season": season, "date_et": row["date"], "espn_event_id": row["event_id"], "tip_utc": row["tip"],
            "home": row["home"], "away": row["away"], "home_prior_games": row["prior_home"],
            "away_prior_games": row["prior_away"], "split": split, "neutral": row["neutral"],
            "actual_margin": row["actual_margin"], "actual_total": row["actual_total"],
            "baseline_margin": row["exp_margin"], "baseline_total": row["exp_total"],
            "baseline_margin_sigma": row["sigma_margin"], "baseline_total_sigma": row["sigma_total"],
        }
        optimized = {}
        for target in ("margin", "total"):
            cal = model[target]
            variants = change_distribution(row[f"{target}_variants"], cal["fit"], cal["weight"])
            if split == "holdout":
                base[f"optimized_{target}"] = median(mu for mu, _ in variants)
                base[f"optimized_{target}_sigma"] = median(s for _, s in variants)
                optimized[target] = variants
        quotes = [("all_game_ml", {"ml": {"line": 0.0}}),
                  ("DK-postfinal-close-proxy", row["dk_proxy"])]
        if row["event_id"] in clob:
            for scope, source in ((STRICT, PM_STRICT), (DIAGNOSTIC, PM_DIAGNOSTIC)):
                quotes.append((source, {
                    k: q for k, q in clob[row["event_id"]].items()
                    if q["settlement"]["scope"] == scope
                }))
        for source, entries in quotes:
            for kind, market in entries.items():
                line = market["line"]
                target = "total" if kind == "total" else "margin"
                actual = row[f"actual_{target}"]
                y = int(actual > line) if actual != line else None
                rule = market.get("settlement", {})
                if rule and rule["actual_primary"] != y:
                    raise ValueError("archived scoreboard and Gamma settlement differ")
                base_variants = row[f"{target}_variants"]
                raw_prob = prob(base_variants, line)
                opt_prob = prob(optimized[target], line) if optimized else None
                if kind == "ml" and not math.isclose(raw_prob, row["p_home"], abs_tol=1e-12):
                    raise ValueError("ML variant recomputation differs from the baseline")
                pm_t = market.get("token_observations") or [[None, None], [None, None]]
                output.append({
                    **base, "source": source, "market": kind, "line": line, "outcome": y,
                    "p_baseline": raw_prob, "p_optimized": opt_prob,
                    "p_market": market.get("p"), "market_slug": market.get("slug"),
                    "condition_id": market.get("condition_id"), "market_cutoff_tip_epoch": market.get("effective_tip"),
                    "market_side0_epoch": pm_t[0][0], "market_side0_price": pm_t[0][1],
                    "market_side1_epoch": pm_t[1][0], "market_side1_price": pm_t[1][1],
                    "market_rule_scope": rule.get("scope"), "market_final_primary": rule.get("actual_primary"),
                    "market_closed_time": rule.get("closed_time"),
                    "gamma_primary_index": rule.get("model_primary_index_in_gamma"),
                    "gamma_final_prices_in_outcome_order":
                        json.dumps(rule["gamma_terminal_prices"]) if rule else None,
                    "spread_baseline_variants": (max(prob([v], line) for v in base_variants)
                                                 - min(prob([v], line) for v in base_variants)),
                })
    return output


def loss_metrics(rows, probability):
    if not rows:
        return None
    ps = [probability(r) for r in rows]
    ys = [r["outcome"] for r in rows]
    return {"brier": wp.brier_score(ps, ys), "log_loss": wp.log_loss(ps, ys)[0],
            "accuracy": mean(int((p >= 0.5) == y) for p, y in zip(ps, ys))}


def block_ci(rows, candidate, benchmark):
    """Paired log-loss difference, with contiguous two-day date blocks."""
    grouped = defaultdict(list)
    for row in rows:
        y = row["outcome"]
        grouped[row["date_et"]].append(nll(row[candidate], y) - nll(row[benchmark], y))
    dates = sorted(grouped)
    if len(dates) < 4:
        return None
    blocks = [grouped[d] + grouped[dates[(i + 1) % len(dates)]] for i, d in enumerate(dates)]
    summaries = [(math.fsum(block), len(block)) for block in blocks]
    randomizer = random.Random(20261119)
    values = []
    for _ in range(2000):
        drawn = [summaries[randomizer.randrange(len(blocks))] for _ in range(math.ceil(len(dates)/2))]
        values.append(math.fsum(s for s, _ in drawn) / sum(n for _, n in drawn))
    ci = sorted(values)
    return [ci[49], ci[1949]]


def market_metrics(data, cohort, kind, split=None):
    picked = [row for row in data if row["source"] == cohort and row["market"] == kind
              and (split is None or row["split"] == split)]
    rows = [r for r in picked if r["outcome"] is not None]
    if not rows:
        return {"n": 0, "pushes": len(picked)}
    out = {
        "n": len(rows), "pushes": len(picked) - len(rows),
        "dates": [rows[0]["date_et"], rows[-1]["date_et"]],
        "over_or_home_rate": mean(r["outcome"] for r in rows),
        "variant_spread": {"max": max(r["spread_baseline_variants"] for r in rows),
                           "fraction_gt_6c": mean(r["spread_baseline_variants"] > 0.06 for r in rows)},
        "baseline": loss_metrics(rows, lambda r: r["p_baseline"]),
        "coin": loss_metrics(rows, lambda r: 0.5),
    }
    if all(r["p_optimized"] is not None for r in rows):
        out["optimized"] = loss_metrics(rows, lambda r: r["p_optimized"])
        out["optimized_minus_baseline_log_loss_ci95"] = block_ci(rows, "p_optimized", "p_baseline")
    if all(r["p_market"] is not None for r in rows):
        out["market"] = loss_metrics(rows, lambda r: r["p_market"])
        out["baseline_vs_market_mad"] = mean(abs(r["p_baseline"] - r["p_market"]) for r in rows)
        out["baseline_minus_market_log_loss_ci95"] = block_ci(rows, "p_baseline", "p_market")
        if "optimized" in out:
            out["optimized_vs_market_mad"] = mean(abs(r["p_optimized"] - r["p_market"]) for r in rows)
            out["optimized_minus_market_log_loss_ci95"] = block_ci(rows, "p_optimized", "p_market")
    if kind == "ml":
        out["home_always"] = loss_metrics(rows, lambda r: 1.0)
        out["favorite_flip_vs_market"] = (
            sum((r["p_baseline"] > 0.5) != (r["p_market"] > 0.5) for r in rows)
            if "market" in out else None
        )
    return out


def formal_gates(forecasts, snapshot, data, metrics):
    wp_b = wp.gate_b_fit(forecasts)
    wp_c = wp.gate_c_sanity(snapshot)
    ml = metrics["all_game_ml"]["ml"]
    gate_d = (ml["n"] >= wp.GATE_D_MIN_N
              and all(ml["baseline"][metric] < ml[benchmark][metric]
                      for metric in ("brier", "log_loss") for benchmark in ("home_always", "coin")))
    pm_ml = metrics[PM_STRICT]["ml"]
    pm_diagnostic = metrics[PM_DIAGNOSTIC]["ml"]
    dk_ml = metrics["DK-postfinal-close-proxy"]["ml"]
    return {
        "A": {
            "games": len(data["games"]), "teams": len(snapshot["teams"]),
            "dates": [data["games"][0]["date"], data["games"][-1]["date"]],
            "counts_per_team": dict(Counter({t: len(b["games"]) for t, b in snapshot["teams"].items()})),
            "snapshot_validated": True, "base_espn_snapshot_rebuilt_equal": data.get("base_snapshot_rebuilt_equal"),
            "historical_schedule_boxscore_crosschecked": data["season"] != "2026-27",
            "team_game_possessions_mean": mean(g[f"{s}_poss"] for g in data["games"] for s in ("home", "away")),
            "team_game_possessions_94_102_share":
                mean(94 <= g[f"{s}_poss"] <= 102 for g in data["games"] for s in ("home", "away")),
        },
        "B": wp_b,
        "C": {k: v for k, v in wp_c.items() if k != "table"},
        "D": {"passed": gate_d, "n": ml["n"], "min_n": wp.GATE_D_MIN_N},
        "E": {
            "mad_max": wp.GATE_E_MAX_MAD,
            "dk_postfinal_proxy_n": dk_ml["n"],
            "dk_postfinal_proxy_mad": dk_ml.get("baseline_vs_market_mad"),
            "pm_strict_n": pm_ml["n"],
            "pm_strict_mad": pm_ml.get("baseline_vs_market_mad"),
            "pm_diagnostic_n": pm_diagnostic["n"],
            "pm_diagnostic_mad": pm_diagnostic.get("baseline_vs_market_mad"),
            "dk_proxy_at_mad_bar": (dk_ml["baseline_vs_market_mad"] <= wp.GATE_E_MAX_MAD if dk_ml["n"] else None),
            "pm_strict_at_mad_bar": (pm_ml["baseline_vs_market_mad"] <= wp.GATE_E_MAX_MAD if pm_ml["n"] else None),
        },
        "retest_bar_whole_season_same_season_games": {
            "n": ml["n"], "min_n": 90, "attained": ml["n"] >= 90,
        },
        "formal_note": ("WP gate E keeps the historical DK proxy separate. Strict PM comparison requires "
                        "both price timestamps and explicit final score including overtime rules."),
    }


def mean_point_errors(rows):
    return {field: {
        "baseline_mae": mean(abs(r[f"baseline_{field}"] - r[f"actual_{field}"]) for r in rows),
        "optimized_mae": mean(abs(r[f"optimized_{field}"] - r[f"actual_{field}"]) for r in rows),
        "baseline_r": wp.pearson([r[f"baseline_{field}"] for r in rows], [r[f"actual_{field}"] for r in rows])[0],
        "optimized_r": wp.pearson([r[f"optimized_{field}"] for r in rows], [r[f"actual_{field}"] for r in rows])[0],
    } for field in ("margin", "total")}


def run_season(game_path, pm_path):
    data = read_gzip(game_path)
    season = data["season"]
    market_data = read_gzip(pm_path) if pm_path.is_file() else {}
    if market_data and market_data["season"] != season:
        raise ValueError("season conflict with market archive")
    snapshot = build_snapshot(data)
    forecasts = baseline_replay(data, snapshot)
    model, scopes = model_selection(forecasts, season)
    rows = season_rows(forecasts, season, market_data, model)
    all_metrics, holdout = (
        {cohort: {k: market_metrics(rows, cohort, k, split) for k in KINDS}
         for cohort in COHORTS}
        for split in (None, "holdout")
    )
    checks = formal_gates(forecasts, snapshot, data, all_metrics)
    point_errors = mean_point_errors([r for r in rows if r["source"] == "all_game_ml" and r["split"] == "holdout"])
    data_provenance = {k: v for k, v in data.items() if k != "games"}
    market_provenance = {k: v for k, v in market_data.items() if k != "rows"}
    archive_misses = Counter(f"{k} {v}" for row in market_data.get("rows", []) for k, v in row["missing"].items())
    return rows, {
        "same_season_formal_wp_metrics": checks, "splits": scopes, "model_selection": model,
        "baseline_all_eligible": all_metrics, "chronological_holdout": holdout, "holdout_point_fit": point_errors,
        "games_sha256": sha256(game_path), "prices_sha256": sha256(pm_path) if market_data else None,
        "games_source": data_provenance,
        "market_source": {**market_provenance, "missing_reasons": dict(archive_misses)},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path(__file__).parent)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--seasons", nargs="+", default=["2024-25", "2025-26", "2026-27"])
    args = parser.parse_args()
    results = {
        "repo": str(args.repo),
        "repo_revision": subprocess.check_output(["git", "-C", str(args.repo), "rev-parse", "HEAD"], text=True).strip(),
        "baseline_sources_sha256": {str(path): sha256(args.repo / path) for path in
            (Path("tools/wp_nba_validate.py"), Path("src/polymarket_alpha_lab/papersim/nba_model.py"),
             Path("src/polymarket_alpha_lab/papersim/espn_nba.py"))},
        "target_rule": {"ml": "home win", "spread": "actual home margin > line",
                        "total": "actual points from both teams > line", "pushes": "excluded from binary scoring"},
        "market_rule_cohorts": {
            PM_STRICT: "final score including overtime is explicit in contract; binary terminal agrees with NBA final score",
            PM_DIAGNOSTIC: "full-game contract does not expressly include OT; binary terminal agrees with NBA final score",
            "DK-postfinal-close-proxy": "retained ESPN DraftKings close archive fields without price timestamps",
        },
        "feature_rule": "same-season prior days only, eight earlier games each team; no market quotes, teammate injuries, or final season summaries",
        "season_results": {},
    }
    all_rows = []
    for season in args.seasons:
        rows, summary = run_season(args.input / f"games-{season}.json.gz", args.input / f"pm-{season}.json.gz")
        all_rows.extend(rows)
        results["season_results"][season] = summary
        print(f"{season}: {summary['splits']}", flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.json").write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")
    with (args.output / "predictions.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for row in all_rows for k in row)))
        writer.writeheader()
        writer.writerows(all_rows)


if __name__ == "__main__":
    main()
