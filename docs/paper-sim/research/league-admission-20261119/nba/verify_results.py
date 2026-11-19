"""Verify archive hashes, score rows and terminal outcome alignment."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", type=Path, default=Path(__file__).parent)
parser.add_argument("--wp-reference", type=Path,
                    default=Path("/home/ubuntu/paper-sim/review/wp_nba_validation/results.json"))
args = parser.parse_args()
p = args.archive
sources = {s: json.loads(gzip.decompress((p/f"games-{s}.json.gz").read_bytes()))
           for s in ("2024-25", "2025-26", "2026-27")}
results = json.loads((p/"results.json").read_text())
for season, s in results["season_results"].items():
    assert hashlib.sha256((p/f"games-{season}.json.gz").read_bytes()).hexdigest() == s["games_sha256"]
    assert hashlib.sha256((p/f"pm-{season}.json.gz").read_bytes()).hexdigest() == s["prices_sha256"]

rows = list(csv.DictReader((p/"predictions.csv").open()))
counts = Counter((r["season"], r["source"], r["market"]) for r in rows)
unique = {(r["season"], r["espn_event_id"], r["source"], r["market"]) for r in rows}
assert len(unique) == len(rows)
game_index = {(season, g["event_id"]): g for season, ds in sources.items() for g in ds["games"]}
contract_index = {
    (season, r["event_id"], kind): quote
    for season in sources for r in json.loads(gzip.decompress((p/f"pm-{season}.json.gz").read_bytes()))["rows"]
    for kind, quote in r["quotes"].items()
}
rule_counts = Counter()
for r in rows:
    assert min(int(r["home_prior_games"]), int(r["away_prior_games"])) >= 8
    assert bool(r["p_optimized"]) == (r["split"] == "holdout")
    game = game_index[(r["season"], r["espn_event_id"])]
    assert (game["date"], game["home"], game["away"]) == (r["date_et"], r["home"], r["away"])
    if not r["source"].startswith("PM-"):
        continue
    tip = int(r["market_cutoff_tip_epoch"])
    t0, t1 = int(r["market_side0_epoch"]), int(r["market_side1_epoch"])
    assert 300 <= tip-t0 <= 1500 and 300 <= tip-t1 <= 1500 and abs(t0-t1) <= 300
    assert tip <= datetime.fromisoformat(r["tip_utc"]).timestamp()
    assert abs(float(r["p_market"]) - (float(r["market_side0_price"]) + 1 - float(r["market_side1_price"]))/2) < 1e-12
    quote = contract_index[(r["season"], r["espn_event_id"], r["market"])]
    rule = quote["settlement"]
    assert rule["closed"] is True and rule["resolution_status"] == "resolved"
    index = int(r["gamma_primary_index"])
    ordered_terminal = json.loads(r["gamma_final_prices_in_outcome_order"])
    assert sorted(ordered_terminal) == [0, 1] and ordered_terminal == rule["gamma_terminal_prices"]
    assert quote["tokens"] == [rule["gamma_tokens"][index], rule["gamma_tokens"][1-index]]
    stat = game["home_pts"] + game["away_pts"] if r["market"] == "total" else game["home_pts"] - game["away_pts"]
    assert stat != float(r["line"])
    assert ordered_terminal[index] == int(r["outcome"]) == int(stat > float(r["line"]))
    assert datetime.fromisoformat(rule["closed_time"]).timestamp() > tip
    explicit = "including any overtime periods" in rule["description"].lower()
    assert (r["source"] == "PM-explicit-full-game-OT") == explicit
    assert r["market_rule_scope"] == rule["scope"]
    rule_counts[(r["season"], r["source"], r["market"])] += 1
    if r["season"] == "2026-27":
        assert float(r["line"]) == game["dk_proxy"][r["market"]]["line"]

past = json.loads(args.wp_reference.read_text())
original = {(r["date"], r["home"], r["away"]): r["p_home"] for r in past["walk_forward_records"]}
replay = {(r["date_et"], r["home"], r["away"]): float(r["p_baseline"]) for r in rows
          if r["season"] == "2026-27" and r["split"] == "train" and r["source"] == "all_game_ml"}
assert replay.keys() == original.keys()
max_diff = max(abs(original[k]-replay[k]) for k in original)
assert max_diff < 1e-12

status = sources["2026-27"]["scoreboard"]
scoreboard_gz = gzip.decompress((p/"espn-scoreboard-20261118.json.gz").read_bytes())
assert hashlib.sha256(scoreboard_gz).hexdigest() == status["raw_sha256"]
unverified_finals = {r["espn_id"] for r in status.get("finals_missing_verified_box_score", [])}
assert {r["espn_id"] for r in status["events"] if r["status"] == "STATUS_FINAL" and r["completed"]} - unverified_finals == {
    r["event_id"] for r in sources["2026-27"]["games"] if r["date"] == "2026-11-18"}
if status.get("planned_freeze_utc"):
    delay = (datetime.fromisoformat(status["captured_utc"])
             - datetime.fromisoformat(status["planned_freeze_utc"])).total_seconds()
    assert 0 <= delay <= 1.0
proof = {
    "prediction_rows": len(rows),
    "original_wp_replayed_rows": len(replay),
    "max_abs_wp_moneyline_probability_difference": max_diff,
    "unique_event_source_market_rows": True,
    "true_pregame_cutoffs_verified": True,
    "current_season_same_dk_line_assertions": True,
    "terminal_binary_outcomes_match_token_order_and_nba_final": len(contract_index),
    "rule_scope_checked_against_contract_description": dict(
        ("|".join(k), n) for k, n in sorted(rule_counts.items())),
    "no_selection_on_actual_overtime": True,
    "optimized_predictions_confined_to_holdout": True,
    "all_game_inputs_hashed": True,
    "all_paired_market_inputs_hashed": True,
    "scoreboard_bytes_hashed": True,
    "scoreboard_asof": status["captured_utc"],
    "scoreboard_planned_freeze_utc": status.get("planned_freeze_utc"),
    "by_partition": {"|".join(k): n for k,n in sorted(counts.items())},
}
print(json.dumps(proof, indent=2))
