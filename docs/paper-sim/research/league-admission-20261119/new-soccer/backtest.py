"""Freeze an early-data goal model and evaluate the chronological holdout."""

import argparse
from bisect import bisect_left
from collections import defaultdict
import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
import gzip
from hashlib import sha256
from itertools import groupby, product
import json
import math
from pathlib import Path
import random
from statistics import fmean, median

from collect import ARCHIVE_ZONE, HOLDOUT, LINES, TUNING


MARKETS = ("1x2", *(f"ou{x:.1f}" for x in LINES))
MARKET_LABELS = {"1x2": ("home", "draw", "away")}
MARKET_LABELS.update({market: ("over", "under") for market in MARKETS[1:]})
HOME_PRIOR, AWAY_PRIOR, LEAGUE_PRIOR_MATCHES = 1.4, 1.1, 12
MODEL_NAMES = ("league", "baseline", "tuned", "market")


@dataclass(frozen=True)
class Parameters:
    half_life: int
    team_prior: int
    team_power: float
    rho: float
    pace: float


LEAGUE_ONLY = Parameters(300, 14, 0.0, 0.0, 1.0)
BASELINE = Parameters(300, 14, 0.75, -0.08, 1.0)
HALF_LIVES = (120, 300, 600)


def input_records(source, stem):
    path = source / (stem + ".jsonl")
    opener = open
    if not path.exists():
        path = path.with_suffix(".jsonl.gz")
        opener = gzip.open
    with opener(path, "rt", encoding="utf-8") as file:
        return [json.loads(line) for line in file]


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def save_csv(path, rows):
    if rows:
        fields = list(dict.fromkeys(field for row in rows for field in row))
        with path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)


def league_split(records):
    grouped = defaultdict(list)
    for record in records:
        game = dict(record)
        game["date"] = date.fromisoformat(game["day"])
        grouped[game["league"]].append(game)
    for games in grouped.values():
        games.sort(key=lambda game: (game["date"], game["id"]))
    return grouped


def digest_games(games):
    encoded = [
        json.dumps(game, ensure_ascii=False, default=str, sort_keys=True)
        for game in games
    ]
    return sha256("\n".join(encoded).encode()).hexdigest()


def dated_ratings(games, half_life):
    """Produce a daily walk-forward state using games at least two days older."""
    assert games
    first_day = games[0]["date"]
    known = dict()
    team_stats = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    league_hg = league_ag = league_games = 0.0
    next_played = 0
    for match_day, group in groupby(games, key=lambda game: game["date"]):
        previous_cutoff = match_day - timedelta(days=1)
        while games[next_played]["date"] < previous_cutoff:
            older = games[next_played]
            f = math.exp2((older["date"] - first_day).days / half_life)
            hg, ag = older["hg"], older["ag"]
            home, away = team_stats[older["home"]], team_stats[older["away"]]
            home[0] += f * hg
            home[1] += f * ag
            home[2] += f
            away[0] += f * ag
            away[1] += f * hg
            away[3] += f
            league_hg += f * hg
            league_ag += f * ag
            league_games += f
            next_played += 1
        decay = math.exp2(-(match_day - first_day).days / half_life)
        weight = league_games * decay
        lg_h = (league_hg * decay + LEAGUE_PRIOR_MATCHES * HOME_PRIOR) / (
            weight + LEAGUE_PRIOR_MATCHES
        )
        lg_a = (league_ag * decay + LEAGUE_PRIOR_MATCHES * AWAY_PRIOR) / (
            weight + LEAGUE_PRIOR_MATCHES
        )
        for game in group:
            hs = tuple(x * decay for x in team_stats[game["home"]])
            aws = tuple(x * decay for x in team_stats[game["away"]])
            known[game["id"]] = (lg_h, lg_a, hs, aws)
    return known


def goal_rates(state, parameters):
    league_home, league_away, home_stats, away_stats = state
    shrinkage = parameters.team_prior * (league_home + league_away) / 2

    def strength(stats):
        goals, conceded, home_games, away_games = stats
        attack = (goals + shrinkage) / (home_games * league_home + away_games * league_away + shrinkage)
        defense = (conceded + shrinkage) / (home_games * league_away + away_games * league_home + shrinkage)
        return attack, defense

    home_attack, home_defense = strength(home_stats)
    away_attack, away_defense = strength(away_stats)
    home_rate = league_home * (home_attack * away_defense) ** parameters.team_power * parameters.pace
    away_rate = league_away * (away_attack * home_defense) ** parameters.team_power * parameters.pace
    # Stay in the plausible goal range and keep Dixon-Coles tau positive.
    return tuple(min(4.5, max(0.15, rate)) for rate in (home_rate, away_rate))


def poisson(rate, limit):
    probabilities = [math.exp(-rate)]
    for n in range(1, limit + 1):
        probabilities.append(probabilities[-1] * rate / n)
    return probabilities


def market_probabilities(rates, rho, markets):
    home_rate, away_rate = rates
    # Dixon-Coles shifts these four cells with corrections that sum to zero.
    delta = rho * home_rate * away_rate * math.exp(-home_rate - away_rate)
    predictions = {}
    if "1x2" in markets:
        ph, pa = poisson(home_rate, 24), poisson(away_rate, 24)
        home_prob, draw_prob, below = 0.0, 0.0, 0.0
        for p_home_goals, p_away_goals in zip(ph, pa, strict=True):
            home_prob += p_home_goals * below
            draw_prob += p_home_goals * p_away_goals
            below += p_away_goals
        home_prob += delta
        draw_prob -= 2 * delta
        predictions["1x2"] = (home_prob, draw_prob, 1 - home_prob - draw_prob)
    totals = [market for market in markets if market != "1x2"]
    if totals:
        combined = poisson(home_rate + away_rate, 5)
        for market in totals:
            line = float(market[2:])
            under = sum(combined[:int(line) + 1])
            if line == 1.5:
                under += delta
            predictions[market] = (1 - under, under)
    return predictions


def outcome(game, market):
    home, away = game["hg"], game["ag"]
    if market == "1x2":
        return 0 if home > away else (1 if home == away else 2)
    return int(home + away < float(market[2:]))


def loss(probabilities, actual, market):
    components = [(p - (i == actual)) ** 2 for i, p in enumerate(probabilities)]
    # Standard multiclass sum and standard binary Brier.
    brier = sum(components) if market == "1x2" else components[0]
    return brier, -math.log(max(probabilities[actual], 1e-15))


def candidates(market):
    rhos, paces = ((0.0, -0.08, -0.15), (1.0,)) if market == "1x2" else (
        (0.0,), (0.9, 1.0, 1.1)
    )
    yield LEAGUE_ONLY
    yield BASELINE
    for vals in product(HALF_LIVES, (6, 14, 30), (0.45, 0.75, 1.0), rhos, paces):
        parameters = Parameters(*vals)
        if parameters != BASELINE:
            yield parameters


def tune(source, output):
    frozen, candidate_rows = {}, []
    for league, unfiltered in sorted(league_split(input_records(source, "fixtures")).items()):
        games = [game for game in unfiltered if game["date"] < HOLDOUT]
        tune_start = date.fromisoformat(TUNING[league])
        tuning = [game for game in games if game["date"] >= tune_start]
        if not tuning:
            raise ValueError(f"{league} has no pre-holdout tuning fixtures")
        earlier = len(games) - len(tuning)
        variants = {half: dated_ratings(games, half) for half in HALF_LIVES}
        settings = {
            "history_hash": digest_games(games),
            "first_date": games[0]["day"],
            "holdout_start": str(HOLDOUT),
            "tuning_start": str(tune_start),
            "warmup_n": earlier,
            "tuning_n": len(tuning),
            "models": {},
        }
        for market in ("1x2", "ou2.5"):
            scored = []
            results = [outcome(game, market) for game in tuning]
            for parameters in candidates(market):
                rating = variants[parameters.half_life]
                observed_losses = [
                    loss(market_probabilities(
                        goal_rates(rating[game["id"]], parameters),
                        parameters.rho, (market,),
                    )[market], actual, market)
                    for game, actual in zip(tuning, results, strict=True)
                ]
                brier, log = [fmean(values) for values in zip(*observed_losses, strict=True)]
                scored.append((brier, log, parameters))
            scored.sort(key=lambda line: line[:2])
            winner_brier, winner_log, best = scored[0]
            settings["models"][market] = {
                "parameters": asdict(best), "brier": winner_brier, "log_loss": winner_log,
            }
            candidate_rows.extend(
                {"league": league, "market": market, "rank": rank, "n": len(tuning),
                 **asdict(params), "brier": brier, "log_loss": log}
                for rank, (brier, log, params) in enumerate(scored, 1)
            )
            print(f"frozen {league} {market} {len(tuning)} fixtures Brier {winner_brier:.6f}")
        frozen[league] = settings
    save_json(output / "frozen_parameters.json", frozen)
    save_csv(output / "tuning.csv", candidate_rows)


def market_consensus(price_record, market):
    offers = price_record.get("prices", {}).get(market, {})
    probabilities, stamps = [], []
    for prices in offers.values():
        odds, times = zip(*prices, strict=True)
        reciprocal = [1 / price for price in odds]
        overround = sum(reciprocal)
        probabilities.append(tuple(component / overround for component in reciprocal))
        stamps.extend(datetime.fromisoformat(time) for time in times)
    if not probabilities:
        return None, 0, None, None
    kickoff = datetime.fromisoformat(price_record["kickoff"]).astimezone(timezone.utc)
    minutes = [(kickoff - stamp.astimezone(timezone.utc)).total_seconds() / 60 for stamp in stamps]
    assert 0 < min(minutes) <= max(minutes) <= 1440
    consensus = tuple(fmean(column) for column in zip(*probabilities, strict=True))
    return consensus, len(probabilities), min(minutes), max(minutes)


def predictions(source, output):
    frozen = json.loads((output / "frozen_parameters.json").read_text())
    price_rows = input_records(source, "prices")
    archive = {(row["league"], row["id"]): row for row in price_rows}
    assert len(archive) == len(price_rows), "duplicate price archive record"
    results = {}
    for league, games in sorted(league_split(input_records(source, "fixtures")).items()):
        known = frozen[league]
        assert digest_games([game for game in games if game["date"] < HOLDOUT]) == known["history_hash"]
        rates = {half: dated_ratings(games, half) for half in HALF_LIVES}
        trained = {
            market: Parameters(**settings["parameters"])
            for market, settings in known["models"].items()
        }
        holdout = defaultdict(list)
        for game in games:
            if game["date"] < HOLDOUT:
                continue
            match_id = game["id"]
            price_record = archive.get((league, match_id), {})
            forecast_as_of = datetime.combine(game["date"], time.min, ARCHIVE_ZONE) - timedelta(minutes=1)
            kickoff = price_record.get("kickoff")
            if kickoff:
                assert forecast_as_of < datetime.fromisoformat(kickoff)
            model_parameters = {
                "league": (LEAGUE_ONLY, LEAGUE_ONLY),
                "baseline": (BASELINE, BASELINE),
                "tuned": (trained["1x2"], trained["ou2.5"]),
            }
            according_to_model = {}
            for name, (result_params, totals_params) in model_parameters.items():
                result_rates = goal_rates(rates[result_params.half_life][match_id], result_params)
                totals_rates = goal_rates(rates[totals_params.half_life][match_id], totals_params)
                according_to_model[name] = market_probabilities(
                    result_rates, result_params.rho, ("1x2",)
                ) | market_probabilities(totals_rates, totals_params.rho, MARKETS[1:])
            for market in MARKETS:
                market_probs, books, latest, earliest = market_consensus(price_record, market)
                actual = outcome(game, market)
                labels = MARKET_LABELS[market]
                row = {
                    "league": league, "id": match_id, "day": game["day"], "phase": game["phase"],
                    "forecast_as_of": forecast_as_of.isoformat(),
                    "last_allowed_result_day": str(game["date"] - timedelta(days=2)),
                    "kickoff": kickoff,
                    "home": game["home"], "away": game["away"], "hg_90": game["hg"],
                    "ag_90": game["ag"], "market": market, "result": labels[actual],
                    "n_bookmakers": books, "newest_quote_minutes_before_kickoff": latest,
                    "oldest_quote_minutes_before_kickoff": earliest, "source_url": game["url"],
                }
                for name in MODEL_NAMES:
                    probs = market_probs if name == "market" else according_to_model[name][market]
                    if probs is None:
                        continue
                    assert abs(sum(probs) - 1) < 1e-10 and all(0 < prob < 1 for prob in probs)
                    row[f"{name}_brier"], row[f"{name}_logloss"] = loss(probs, actual, market)
                    row[f"{name}_argmax"] = labels[max(range(len(probs)), key=probs.__getitem__)]
                    row.update({f"{name}_p_{label}": prob for label, prob in zip(labels, probs, strict=True)})
                holdout[market].append(row)
        results[league] = dict(holdout)
    return results


def brier_gain(rows, comparison):
    by_week = defaultdict(list)
    for row in rows:
        day = date.fromisoformat(row["day"])
        by_week[day.isocalendar()[:2]].append(row[f"{comparison}_brier"] - row["tuned_brier"])
    weeks = [(sum(losses), len(losses)) for losses in by_week.values()]
    if len(weeks) < 2:
        return None, None
    rng = random.Random(20261119)
    distribution = []
    for _ in range(2000):
        blocks = rng.choices(weeks, k=len(weeks))
        numerator, denominator = [sum(values) for values in zip(*blocks, strict=True)]
        distribution.append(numerator / denominator)
    distribution.sort()
    return distribution[50], distribution[1949]


def score(source, output):
    observed = predictions(source, output)
    metrics, components, all_rows = [], [], []
    for league, markets in observed.items():
        for market, rows in markets.items():
            all_rows.extend(rows)
            paired = [row for row in rows if row["n_bookmakers"]]
            if not paired:
                print(f"{league} {market} has no matched archived prices")
            for cohort, cohort_rows in (("all", rows), ("matched", paired)):
                stat = {"league": league, "market": market, "cohort": cohort, "n": len(cohort_rows),
                        "all_holdout_n": len(rows)}
                if not cohort_rows:
                    metrics.append(stat)
                    continue
                stat["first_date"], stat["last_date"] = cohort_rows[0]["day"], cohort_rows[-1]["day"]
                if cohort == "matched":
                    stat["median_bookmakers"] = median(row["n_bookmakers"] for row in cohort_rows)
                labels = MARKET_LABELS[market]
                component_rows = [
                    {"league": league, "market": market, "cohort": cohort, "outcome": label,
                     "n": len(cohort_rows), "observed": fmean(row["result"] == label for row in cohort_rows)}
                    for label in labels
                ]
                for name in MODEL_NAMES:
                    if name == "market" and cohort != "matched":
                        continue
                    stat[f"{name}_brier"] = fmean(row[f"{name}_brier"] for row in cohort_rows)
                    stat[f"{name}_logloss"] = fmean(row[f"{name}_logloss"] for row in cohort_rows)
                    stat[f"{name}_accuracy"] = fmean(row[f"{name}_argmax"] == row["result"] for row in cohort_rows)
                    for component in component_rows:
                        label = component["outcome"]
                        p_label = f"{name}_p_{label}"
                        component[f"{name}_mean_probability"] = fmean(row[p_label] for row in cohort_rows)
                        component[f"{name}_one_vs_rest_brier"] = fmean(
                            (row[p_label] - (row["result"] == label)) ** 2 for row in cohort_rows
                        )
                for comparison in ("baseline", "market"):
                    if f"{comparison}_brier" not in stat:
                        continue
                    stat[f"gain_vs_{comparison}"] = stat[f"{comparison}_brier"] - stat["tuned_brier"]
                    low, high = brier_gain(cohort_rows, comparison)
                    stat[f"gain_vs_{comparison}_ci_lo"] = low
                    stat[f"gain_vs_{comparison}_ci_hi"] = high
                metrics.append(stat)
                components.extend(component_rows)
    save_csv(output / "holdout_predictions.csv", all_rows)
    save_csv(output / "metrics.csv", metrics)
    save_csv(output / "outcome_metrics.csv", components)
    save_json(output / "metrics.json", metrics)
    for row in metrics:
        if row["cohort"] == "matched" and row["market"] in ("1x2", "ou2.5") and row["n"]:
            print("{} {} n={}/{} baseline={:.6f} tuned={:.6f} market={:.6f} delta={:.6f}".format(
                row["league"], row["market"], row["n"], row["all_holdout_n"], row["baseline_brier"],
                row["tuned_brier"], row["market_brier"], row["gain_vs_market"],
            ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("freeze", "score"))
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.stage == "freeze":
        tune(args.data, args.output)
    else:
        score(args.data, args.output)


if __name__ == "__main__":
    main()
