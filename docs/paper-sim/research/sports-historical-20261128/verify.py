"""Check public evidence exports offline. Run with Python 3.12+ from any directory."""

from __future__ import annotations

import gzip
import json
from collections import defaultdict
from datetime import datetime, timedelta
from hashlib import sha256
from math import isclose, log
from pathlib import Path
from random import Random
from statistics import fmean

DIRECTORY = Path(__file__).resolve().parent


def read(name):
    path = DIRECTORY / name
    raw = path.read_bytes()
    return gzip.decompress(raw) if path.suffix == ".gz" else raw


def require(test, detail):
    if not test:
        raise ValueError(detail)


def close(actual, expected, detail):
    require(isclose(actual, expected, abs_tol=1e-12, rel_tol=0), detail)


def stamp(value):
    at = datetime.fromisoformat(value)
    require(at.tzinfo is not None, "Timestamp lacks a time zone")
    return at


def soccer_scores(family, probabilities, score):
    home, away = score
    n = 3 if family == "match_winner" else 2
    require(
        len(probabilities) == n and all(0 <= p <= 1 for p in probabilities), "Invalid probabilities"
    )
    close(sum(probabilities), 1, "Probabilities do not sum to one")
    if family == "match_winner":
        outcome = 0 if home > away else 2 if away > home else 1
        brier = sum((p - int(i == outcome)) ** 2 for i, p in enumerate(probabilities))
    else:
        outcome = int(home + away < 3)  # vector order is Over, Under
        brier = (probabilities[0] - (outcome == 0)) ** 2
    return brier, -log(max(probabilities[outcome], 1e-15))


def interval_by_week(rows):
    weeks = defaultdict(list)
    for week, delta in rows:
        weeks[week].append(delta)
    blocks = [(sum(values), len(values)) for _, values in sorted(weeks.items())]
    rng = Random(20261128)
    draws = []
    for _ in range(3000):
        sample = rng.choices(blocks, k=len(blocks))
        draws.append(sum(total for total, _ in sample) / sum(n for _, n in sample))
    draws.sort()
    return [draws[int((len(draws) - 1) * q)] for q in (0.025, 0.975)]


def verify_five():
    report = json.loads(read("new-five.json"))
    raw = read("new-five-fixtures.json.gz")
    require(sha256(raw).hexdigest() == report["historical_rows_sha256"], "Fixture export digest")
    sources = read("new-five-sources.json.gz")
    require(sha256(sources).hexdigest() == report["input_manifest_sha256"], "Input manifest digest")
    require(
        len(json.loads(sources)["inputs"]) == report["registered_input_count"],
        "Forecast input count",
    )
    packet = json.loads(raw)
    require(
        packet["model_version"] == report["model_version"]
        and packet["code"] == report["repository_commit"],
        "Fixture export belongs to another model or code revision",
    )
    rows = packet["reconstructed_rows"]
    require(len({(r["league"], r["fixture_id"]) for r in rows}) == len(rows), "Duplicate fixture")
    totals = defaultdict(int)
    for league, summary in report["leagues"].items():
        games = [r for r in rows if r["league"] == league]
        require(len(games) == summary["normal_regular_targets"], f"{league} fixture count")
        for r in games:
            require(
                stamp(r["reconstruction_cutoff"]) == stamp(r["kickoff"]) - timedelta(minutes=30),
                f"{league} model cutoff",
            )
            require(
                r["fixture_id"] not in r["historical_details"], "Target used as its own history"
            )
        for family, expected in summary["metrics"].items():
            all_scores, pairs = [], []
            for row in games:
                model = row["probabilities"].get(family)
                if model is None:
                    continue
                model_score = soccer_scores(family, model, row["score"])
                all_scores.append(model_score)
                market = row["archived_bookmaker_close"].get(family)
                if market is not None:
                    week = stamp(row["kickoff"]).isocalendar()[:2]
                    pairs.append((model_score, soccer_scores(family, market, row["score"]), week))
            require(
                len(all_scores) == expected["all_model_n"] == summary["forecasts"],
                f"{league} model count",
            )
            require(len(pairs) == expected["paired_n"], f"{league} paired count")
            totals[family] += len(pairs)
            for i, metric in enumerate(("brier", "log_loss")):
                close(
                    fmean(s[i] for s in all_scores),
                    expected["all_model_" + metric],
                    f"{league} model {metric}",
                )
                if pairs:
                    for j, term in enumerate(("model", "market")):
                        close(
                            fmean(pair[j][i] for pair in pairs),
                            expected["paired"][term + "_" + metric],
                            f"{league} {family} {term} {metric}",
                        )
            if pairs:
                ci = interval_by_week([(week, mod[0] - mkt[0]) for mod, mkt, week in pairs])
                for actual, archived in zip(
                    ci, expected["paired"]["week_paired_brier_delta_ci95"], strict=True
                ):
                    close(actual, archived, f"{league} {family} weekly interval")
    return {"target_fixtures": len(rows), "paired_counts": dict(totals)}


def verify_binary(rows, report):
    require(
        len(rows) == report["model"]["n"] == report["market"]["n"], "Different paired denominators"
    )
    if not rows:
        require(
            report["model"]["brier"] is None and report["market"]["brier"] is None, "Empty Brier"
        )
        return
    for i, predictor in enumerate(("model", "market")):
        probabilities = [row[i] for row in rows]
        outcomes = [row[2] for row in rows]
        require(all(0 < p < 1 for p in probabilities), "Binary probability bounds")
        close(
            fmean((p - y) ** 2 for p, y in zip(probabilities, outcomes)),
            report[predictor]["brier"],
            "Binary Brier",
        )
        close(
            fmean(-log(p if y else 1 - p) for p, y in zip(probabilities, outcomes)),
            report[predictor]["log_loss"],
            "Binary log loss",
        )


def verify_norway():
    claims = json.loads(read("norway-polymarket.json"))
    packet = read("new-five-fixtures.json.gz")
    require(
        sha256(packet).hexdigest() == claims["input_rows_uncompressed_sha256"],
        "Norway model export",
    )
    forecasts = {
        r["fixture_id"]: r for r in json.loads(packet)["reconstructed_rows"] if r["league"] == "nor"
    }
    paired = []
    seen = set()
    for contract in claims["contracts"]:
        key = contract["fixture_id"]
        fixture = forecasts[key]
        require(
            key not in seen and contract["score"] == fixture["score"],
            "Duplicate or mismatched Norway game",
        )
        seen.add(key)
        tip = stamp(fixture["kickoff"])
        require(tip == stamp(contract["kickoff_utc"]), "Norway kickoff")
        anchor = tip - timedelta(minutes=30)
        require(stamp(contract["forecast_anchor_utc"]) == anchor, "Norway forecast anchor")
        over, under = (contract["curve_receipts"][side] for side in ("Over", "Under"))
        times = [stamp(side["selected_utc"]) for side in (over, under)]
        require(
            all(anchor - timedelta(minutes=10) <= t <= anchor for t in times), "Norway price window"
        )
        require(abs(times[0] - times[1]) <= timedelta(minutes=5), "Norway price pair timing")
        mkt = over["chart_price"] / (over["chart_price"] + under["chart_price"])
        model = fixture["probabilities"]["total_2_5"][0]
        outcome = int(sum(fixture["score"]) > 2)
        close(mkt, contract["chart_normalized_p_over"], "Norway normalized curve")
        close(model, contract["model_p_over"], "Norway archived forecast")
        require(outcome == contract["score_over_2_5"], "Norway result")
        paired.append((model, mkt, outcome))
    summary = claims["score_on_exact_same_matches"]
    verify_binary(
        paired, {"model": summary["model"], "market": summary["normalized_two_curve_probability"]}
    )
    return len(paired)


def verify_basketball():
    observations = json.loads(read("basketball-observations.json.gz"))
    report = json.loads(read("basketball.json"))
    require(report["snapshot_read_at"] == observations["read_at"], "Basketball snapshot identity")
    first = {}
    for row in sorted(
        observations["nba_forward_home_records"], key=lambda x: stamp(x["captured_at"])
    ):
        tip = stamp(row["kickoff"])
        captured = stamp(row["captured_at"])
        require(
            tip - timedelta(minutes=30)
            <= stamp(row["started_at"])
            <= captured
            < tip - timedelta(minutes=20),
            "NBA prediction outside the registered window",
        )
        require(
            stamp(row["price_at"]) <= captured and stamp(row["settled_at"]) > tip,
            "NBA price or resolution clock",
        )
        require(
            row["payout"]
            == int(row["home_score"] > row["away_score"])
            == int(row["result"] == "win")
            and row["home_score"] != row["away_score"],
            "NBA score and contract disagree",
        )
        if row["candidate"] is not None:
            require(
                stamp(row["candidate_input_cutoff"])
                <= stamp(row["candidate_observed"])
                <= captured,
                "NBA ratings input cutoff",
            )
        if row["baseline"] is not None and row["market"] is not None:
            first.setdefault((row["fixture_key"], tip), row)
    counts = {}
    for version in ("baseline", "candidate"):
        rows = [
            (r[version], r["market"], r["payout"]) for r in first.values() if r[version] is not None
        ]
        summary = report["nba"]["prospective_strict_T30_home_side"][version]
        verify_binary(rows, summary)
        close(fmean(abs(p - q) for p, q, _ in rows), summary["mad"], "NBA market deviation")
        counts["nba_" + version] = len(rows)
    for league, booking in report["ncaa"].items():
        pairs = {}
        for row in observations["college_rows"]:
            if (
                row["league"] != league
                or row["state"] != "final"
                or row["market_state"] != "verified"
            ):
                continue
            if row["price"] is None or row["model_at_price"] is None:
                continue
            tip, quote = stamp(row["kickoff"]), stamp(row["price_at"])
            require(
                stamp(row["observed_at"]) < tip
                and tip - timedelta(minutes=30) <= quote < tip - timedelta(minutes=20),
                "NCAA forecast/price clock",
            )
            require(row["home_score"] != row["away_score"], "NCAA tied final")
            key = (row["official_id"], tip)
            if key not in pairs or quote < pairs[key][0]:
                pairs[key] = (
                    quote,
                    float(row["model_at_price"]),
                    float(row["price"]),
                    int(row["home_score"] > row["away_score"]),
                )
        estimate = booking["prospective"]
        verify_binary(
            [row[1:] for row in pairs.values()],
            {"model": estimate["strict_model"], "market": estimate["strict_market"]},
        )
        counts[league] = len(pairs)
    return counts


def verify_scorecards():
    summary = json.loads(read("optimizer-and-nations.json"))
    sealed = DIRECTORY.parent.parent / "design/optimizer-phase1/2026-11-25"
    for name, expected in summary["optimizer"]["verified_scorecards"].items():
        raw = (sealed / name).read_bytes()
        require(
            len(raw) == expected["bytes"] and sha256(raw).hexdigest() == expected["sha256"], name
        )
    return len(summary["optimizer"]["verified_scorecards"])


def verify_archived_football():
    repo = DIRECTORY.parents[3]
    archives = json.loads(read("football-archives-byte-check.json"))["replays"]
    checked = 0
    for archive in archives.values():
        for item in archive["outputs"]:
            raw = (repo / item["file"]).read_bytes()
            require(
                len(raw) == item["bytes"] and sha256(raw).hexdigest() == item["sha256"],
                item["file"],
            )
            checked += 1
    return checked


if __name__ == "__main__":
    checks = {
        "five_leagues": verify_five(),
        "norway_additional_curve_pairs": verify_norway(),
        "basketball_original_pairs": verify_basketball(),
        "sealed_scorecards": verify_scorecards(),
        "archived_football_files": verify_archived_football(),
    }
    print(json.dumps(checks, sort_keys=True))
