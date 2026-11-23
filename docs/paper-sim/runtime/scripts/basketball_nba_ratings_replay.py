"""Reproduce NBA validation against the frozen baseline and optionally a saved research snapshot."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
from statistics import fmean

import basketball_models as models


FROZEN = models.ARCHIVE / "nba"
SEASONS = ("2024-25", "2025-26", "2026-27")
STRICT = "explicit-full-game-including-OT"
DIAGNOSTIC = "NBA-final-score-OT-not-explicit"


def _quotes(prices, scope):
    return {event: {kind: quote for kind, quote in markets.items() if quote["settlement"]["scope"] == scope}
            for event, markets in prices.items()}


def _archived_baseline(rows, game_index, season):
    wp = models._wp()
    forecasts = []
    paired = defaultdict(list)
    for row in rows:
        game = game_index.get(row["espn_event_id"])
        if row["season"] != season or not game or game["neutral"]:
            continue
        if row["source"] == "all_game_ml":
            forecasts.append({"date": row["date_et"], "p_home": float(row["p_baseline"]), "home_win": int(row["outcome"]),
                              "exp_margin": float(row["baseline_margin"]), "exp_total": float(row["baseline_total"]),
                              "actual_total": int(row["actual_total"]), "actual_margin": int(row["actual_margin"])})
        if row["market_rule_scope"] in {STRICT, DIAGNOSTIC} and row["market"] in {"ml", "total"} and row["outcome"]:
            paired[(row["market"], row["market_rule_scope"])].append((float(row["p_baseline"]),
                                                                       float(row["p_market"]), int(row["outcome"])))
    output = models._nba_retest_summary(list(game_index.values()), forecasts, models.NBA_VERSION)
    output["B"] = models._json_finite(wp.gate_b_fit(forecasts))
    output["D"] = wp.ml_metrics(forecasts)
    output["exchange_comparisons"] = {f"{kind}/{scope}": models._nba_paired_scores(values) | {
        "mad": fmean(abs(prediction - market) for prediction, market, _ in values),
    } for (kind, scope), values in paired.items()}
    return output


def _replay(season, archived_baseline, cache=None):
    frozen, prices = models.nba_archive(season)
    official = {game["event_id"]: game for game in frozen}
    strict, diagnostics = _quotes(prices, STRICT), _quotes(prices, DIAGNOSTIC)
    if cache is None:
        now = models.stamp(max(frozen, key=lambda game: game["started"])["started"]) + timedelta(hours=12)
    else:
        if cache["nba_season"] != season:
            raise ValueError("rolling cache season does not match archive")
        now = models.stamp(cache["research_report"]["observed_at"])
        official.update(cache["nba_final_games"])
        strict.update(cache["nba_strict_pm"])
        diagnostics.update(cache["nba_diagnostic_pm"])
    games = list(official.values())
    snapshot = models.nba_snapshot(games, season, now)
    history = models.nba_ratings_history(games, season, (cache or {}).get("nba_prior_final_games"))
    if cache is None:
        baseline = _archived_baseline(archived_baseline, official, season)
    else:
        baseline, _ = models.nba_retest(games, snapshot, strict, diagnostics, {})
    candidate = models.nba_ratings_retest(games, snapshot, history, strict, diagnostics, now)
    return {"season": season, "as_of": models.utc_iso(now), "baseline": baseline, "candidate": candidate}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rolling-cache", type=Path, help="Include a saved snapshot of the existing NBA research cache")
    args = parser.parse_args()
    prediction_source = FROZEN / "predictions.csv"
    with prediction_source.open(newline="", encoding="utf-8") as archive:
        archived_baseline = list(csv.DictReader(archive))
    paths = [prediction_source, *(FROZEN / f"{kind}-{season}.json.gz" for season in SEASONS for kind in ("games", "pm"))]
    report = {
        "sources_sha256": {path.name: sha256(path.read_bytes()).hexdigest() for path in paths},
        "development": {"season": "2024-25", "fit_before_et": "2025-01-10", "fit_games": 425,
                        "tuning_games": 675, "tune_last_et": "2025-04-13", "market_inputs": False},
        "holdouts": [_replay(season, archived_baseline) for season in SEASONS[1:]],
    }
    if args.rolling_cache:
        cache_source = args.rolling_cache.read_bytes()
        report["rolling_cache_sha256"] = sha256(cache_source).hexdigest()
        report["rolling"] = _replay(SEASONS[-1], archived_baseline, json.loads(cache_source))
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
