"""Score the original 24–26 November Cup receipts, one outcome per completed match.

Run with Python 3.12 using "python score.py" in any directory.
"""

import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import fmean

ROOT = Path(__file__).resolve().parent
DIRECTIONS = ("home", "draw", "away")


def distribution(sides, field):
    vector = []
    for direction in DIRECTIONS:
        quotes = sides[direction]
        row = quotes.get("Yes") or quotes["No"]
        p = (row["bid"] + row["ask"]) / 2 if field == "midpoint" else row[field]
        if p is None:
            return None
        vector.append(p if row["choice"] == "Yes" else 1 - p)
    total = sum(vector)
    assert all(0 < p < 1 for p in vector) and total > 0
    return [p / total for p in vector]


def metrics(vector, outcome):
    actual = [float(j == outcome) for j in range(3)]
    return {
        "multiclass_brier_sum": sum((p - a) ** 2 for p, a in zip(vector, actual)),
        "rps": sum((sum(vector[:k]) - sum(actual[:k])) ** 2 for k in (1, 2)) / 2,
        "log_loss": -math.log(vector[outcome]),
        "top_pick_correct": float(vector.index(max(vector)) == outcome),
    }


def main():
    raw = (ROOT / "inputs.json.gz").read_bytes()
    data = json.loads(gzip.decompress(raw))
    matches = {f["ref"]: f for f in data["fixtures"]}
    assert len(matches) == len(data["fixtures"])
    groups = defaultdict(lambda: defaultdict(dict))
    selected = []
    unit_returns = []
    for row in data["archived_uel_sides"]:
        event = matches[row["gid"]]
        kickoff = datetime.fromisoformat(event["kickoff"])
        assert event["league"] == "uel"
        assert all(
            datetime.fromisoformat(row[name]) < kickoff for name in ("model_asof", "book_asof")
        )
        sides = groups[row["gid"]][row["direction"]]
        assert row["choice"] not in sides
        sides[row["choice"]] = row
        if row["selected_before_kickoff"]:
            home, away = event["goals"]
            result = "home" if home > away else "away" if away > home else "draw"
            won = (result == row["direction"]) == (row["choice"] == "Yes")
            cost = row["ask"] + row["fee"] * row["ask"] * (1 - row["ask"])
            unit_net = float(won) / cost - 1
            unit_returns.append(unit_net)
            selected.append(
                {
                    "public_event_id": event["ref"],
                    "home": event["home"],
                    "away": event["away"],
                    "final_goals": [home, away],
                    "direction": row["direction"],
                    "choice": row["choice"],
                    "archived_ask": row["ask"],
                    "all_in_cost": round(cost, 8),
                    "contract_won": won,
                    "hypothetical_unit_net": round(unit_net, 8),
                }
            )

    comparisons = defaultdict(list)
    fallback = []
    for gid, directions in sorted(groups.items()):
        event = matches[gid]
        home, away = event["goals"]
        result = 0 if home > away else 2 if away > home else 1
        if distribution(directions, "clubelo") is None:
            fallback.append(gid)
        for field in ("probability", "named_books", "oddsportal", "midpoint", "clubelo"):
            vector = distribution(directions, field)
            if vector is not None:
                comparisons[field].append(metrics(vector, result))
    first_seen = Counter()
    scores = Counter()
    for event in matches.values():
        r = event["runtime"]
        if datetime.fromisoformat(r["first_seen_utc"]) < datetime.fromisoformat(event["kickoff"]):
            first_seen[event["league"]] += 1
        if r["score_field"]:
            scores[event["league"]] += 1
    counts = Counter(f["league"] for f in matches.values())
    fields = ("multiclass_brier_sum", "rps", "log_loss", "top_pick_correct")
    return {
        "period": "2026-11-24 to 2026-11-26 UTC inclusive",
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "runtime_read_only_checked_utc": data["read_only_runtime_checked_utc"],
        "public_normal_finals": dict(counts),
        "runtime_discovered_before_kickoff": dict(first_seen),
        "runtime_typed_evidence_rows": data["typed_runtime_table_counts"],
        "runtime_fixture_info_with_score": {lg: scores[lg] for lg in counts},
        "archived_original_model": "Independent November 26 analysis. Half mean named de-vigged book probability, half ClubElo when available. With no ClubElo, half named books and half OddsPortal.",
        "snapshot_capture_utc": [data["refresh_from"], data["refresh_through"]],
        "uel_comparable_probability_boards": len(groups),
        "uel_direct_binary_contract_sides": len(data["archived_uel_sides"]),
        "uel_fixtures_with_clubelo": len(groups) - len(fallback),
        "uel_fallback_public_ids": fallback,
        "uel_confirmed_starter_lists_supplied": 0,
        "fixture_level_metrics": {
            name: {
                "n": len(rows),
                **{field: round(fmean(r[field] for r in rows), 8) for field in fields},
            }
            for name, rows in comparisons.items()
        },
        "metric_method": "Home, draw, away. Use affirmative contract probability or complement the negative side if absent. Normalize all three-way vectors to sum to one. Exchange comparator uses quoted bid/ask midpoints. Multiclass Brier is the sum over all three terms. RPS is the mean of the two cumulative squared errors. Smaller is better for these scores and natural-log loss. No synthetic fills.",
        "selected_under_original_refresh_rule": selected,
        "hypothetical_selected_1_unit_each": {
            "n": len(selected),
            "wins": sum(row["contract_won"] for row in selected),
            "net_units": round(sum(unit_returns), 8),
            "normal_time_settlement_assumption": True,
        },
        "official_ledger_writes": 0,
        "limits": [
            "The original UEL model differs from the registered WP-P5 bridge. No archived WP-P5 invocation or commit-bound cross_league_mw_check was found for these 36 fixtures.",
            "The independent scan preceded kickoff by hours. It does not provide the registered forward decision window, closing marks or an execution receipt.",
            "The named book probabilities and one OddsPortal fallback are components of the independent estimate. These baselines share data. Midpoints represent a separate exchange reference, not an executable three-way quote.",
            "The archive used a historical display-liquidity screen when it originally saved the 105 sides. It is not activated in this replay. Three missing contract sides need no imputation for event-level probability scoring.",
            "Zero confirmed starting elevens in the input snapshots. Qualitative previews on selected fixtures did not change the numeric probabilities.",
            "The three original highlighted sides are scored from normal-time results with their archived taker fee for illustration. No paper portfolio position, stake, realized profit or promotion is inferred.",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2, sort_keys=True))
