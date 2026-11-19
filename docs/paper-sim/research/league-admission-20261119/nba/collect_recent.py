"""Normalize the ESPN 2026–27 cache and optionally append newly final games."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from collect_history import ESPN, fetch
from polymarket_alpha_lab.papersim import espn_nba

ET = ZoneInfo("America/New_York")


def number(value):
    try:
        return float(str(value).replace("−", "-"))
    except (TypeError, ValueError):
        return None


def fair(first, second):
    def probability(o):
        if o is None or abs(o) < 100:
            return None
        return 100 / (o + 100) if o > 0 else -o / (100 - o)

    a, b = probability(first), probability(second)
    return a / (a + b) if a is not None and b is not None else None


def dk_quote(payload):
    """Keep the three DK closing proxies as separate complete pairs."""
    quotes = {}
    entries = [e for e in payload.get("pickcenter", [])
               if e.get("provider", {}).get("name", "").replace(" ", "").lower() == "draftkings"]
    if len(entries) != 1:
        return quotes
    dk = entries[0]

    def read(market, side):
        return dk.get(market, {}).get(side, {}).get("close", {})

    ml = [number(read("moneyline", side).get("odds")) for side in ("home", "away")]
    p = fair(*ml)
    if p is not None:
        quotes["ml"] = {"line": 0.0, "p": p, "american": ml}
    home, away = [read("pointSpread", side) for side in ("home", "away")]
    line = number(home.get("line"))
    opposite = number(away.get("line"))
    odds = [number(side.get("odds")) for side in (home, away)]
    p = fair(*odds)
    if line is not None and opposite == -line and p is not None:
        quotes["spread"] = {"line": -line, "p": p, "american": odds}
    over, under = [read("total", side) for side in ("over", "under")]
    line = number(str(over.get("line")).removeprefix("o").removeprefix("O"))
    opposite = number(str(under.get("line")).removeprefix("u").removeprefix("U"))
    odds = [number(side.get("odds")) for side in (over, under)]
    p = fair(*odds)
    if line is not None and opposite == line and p is not None:
        quotes["total"] = {"line": line, "p": p, "american": odds}
    return quotes


def normalize_game(payload, eid):
    header = payload["header"]
    competition = header["competitions"][0]
    status = competition["status"]["type"]
    if not (status.get("completed") is True and status.get("state") == "post"
            and status.get("name") == "STATUS_FINAL"):
        raise ValueError(f"ESPN summary is unfinalized: {eid}: {status}")
    if str(competition["id"]) != eid:
        raise ValueError(f"summary event id mismatch: {eid}")
    sides = {side["homeAway"]: side for side in competition["competitors"]}
    if set(sides) != {"home", "away"}:
        raise ValueError(f"missing team side: {eid}")
    home, away = (sides[side]["team"]["abbreviation"] for side in ("home", "away"))
    started = datetime.fromisoformat(competition["date"])
    day = started.astimezone(ET).date().isoformat()
    scores = espn_nba._box_line(payload, eid)
    h, a = scores[home], scores[away]
    return {
        "date": day, "home": home, "away": away, "event_id": eid,
        "started": started.isoformat(), "neutral": bool(competition.get("neutralSite", False)),
        "home_pts": h["pts"], "away_pts": a["pts"],
        "home_poss": espn_nba._possessions(h), "away_poss": espn_nba._possessions(a),
        "home_poss_inputs": {k: v for k, v in h.items() if k != "pts"},
        "away_poss_inputs": {k: v for k, v in a.items() if k != "pts"},
        "dk_proxy": dk_quote(payload),
    }


def bundle(args):
    raw = {
        "generated_at": json.loads(args.base_snapshot.read_bytes())["generated_at"],
        "season": "2026-27",
        "schedules": {},
        "summaries": {},
    }
    for team in espn_nba.NBA_TEAMS:
        path = args.base_cache / f"schedule_{team}_2027.json"
        raw["schedules"][team] = json.loads(path.read_bytes())
    rows = []
    for path in sorted(args.base_cache.glob("summary_*.json")):
        eid = path.stem.removeprefix("summary_")
        payload = json.loads(path.read_bytes())
        raw["summaries"][eid] = payload
        rows.append(normalize_game(payload, eid))
    validated = espn_nba.load_nba_snapshot(args.base_snapshot)
    rebuilt = espn_nba.build_snapshot(raw)
    same = rebuilt["teams"] == validated["teams"] and rebuilt["injuries"] == validated["injuries"]
    if len(rows) != 210 or not same:
        raise ValueError("expected 210 games and the retained ESPN snapshot to rebuild")
    return rows, validated


def append_new_finals(args, rows, captured):
    day = date.fromisoformat(args.scoreboard_day)
    url = f"{ESPN}/scoreboard?{urlencode({'dates': day.strftime('%Y%m%d'), 'limit': 100})}"
    path = args.cache / f"scoreboard-{day}-{captured.strftime('%H%M%SZ')}.json"
    raw_data = fetch(url, path, 1.2)
    payload = json.loads(raw_data)
    if args.scoreboard_output:
        args.scoreboard_output.write_bytes(gzip.compress(raw_data, mtime=0))
    status_rows = []
    missing_finals = []
    inv = {row["event_id"]: row for row in rows}
    for event in payload["events"]:
        eid = str(event["id"])
        competition = event["competitions"][0]
        status = competition["status"]["type"]
        if status["name"] != event["status"]["type"]["name"]:
            raise ValueError(f"ESPN scoreboard status conflict {eid}")
        status_rows.append({"espn_id": eid, "game": event["shortName"], "start_utc": event["date"],
                            "status": status["name"], "completed": status["completed"]})
        if (status["name"] != "STATUS_FINAL" or status["state"] != "post"
                or status["completed"] is not True):
            continue
        if datetime.fromisoformat(event["date"]) >= captured:
            raise ValueError(f"future final in response: {eid}")
        if eid in inv:
            continue
        try:
            summary = fetch(f"{ESPN}/summary?{urlencode({'event': eid})}", args.cache / f"summary-{eid}.json", 1.2)
            row = normalize_game(json.loads(summary), eid)
            scores = {s["homeAway"]: (s["team"]["abbreviation"], int(s["score"]))
                      for s in competition["competitors"]}
            if (row["date"] != day.isoformat()
                    or scores != {"home": (row["home"], row["home_pts"]),
                                  "away": (row["away"], row["away_pts"])}):
                raise ValueError(f"scoreboard/summary conflict {eid}")
        except (OSError, IndexError, KeyError, TypeError, ValueError, espn_nba.EspnNbaError) as exc:
            missing_finals.append({"espn_id": eid, "game": event["shortName"], "reason": str(exc)})
            continue
        rows.append(row)
    return {"source_url": url, "captured_utc": captured.isoformat(),
            "planned_freeze_utc": args.freeze_utc, "raw_sha256": hashlib.sha256(raw_data).hexdigest(),
            "events": status_rows, "finals_missing_verified_box_score": missing_finals}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-cache", type=Path, required=True)
    parser.add_argument("--base-snapshot", type=Path, required=True)
    parser.add_argument("--prior-history", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--scoreboard-day")
    parser.add_argument("--scoreboard-output", type=Path)
    parser.add_argument("--freeze-utc", help="UTC target at which the frozen scoreboard request is sent")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    rows, snapshot = bundle(args)
    if args.freeze_utc:
        target = datetime.fromisoformat(args.freeze_utc)
        while target > (now := datetime.now(timezone.utc)):
            time.sleep(min(20, (target - now).total_seconds()))
    captured = datetime.now(timezone.utc)
    status = append_new_finals(args, rows, captured) if args.scoreboard_day else None
    earlier = json.loads(args.prior_history.read_bytes())["rows"]
    data = {
        "season": snapshot["season"],
        "retrieved_at": captured.isoformat(),
        "base_snapshot_rebuilt_equal": True,
        "base_snapshot_sha256": hashlib.sha256(args.base_snapshot.read_bytes()).hexdigest(),
        "base_snapshot_date": snapshot["generated_at"],
        "base_schedule_cache": str(args.base_cache),
        "base_games": 210,
        "prior_scan_rows": len(earlier),
        "prior_scan_rows_min8": sum(r["prior_n"] >= 8 for r in earlier),
        "prior_scan_sha256": hashlib.sha256(args.prior_history.read_bytes()).hexdigest(),
        "scoreboard": status,
        "sources": {"box_scores": ESPN + "/summary?event={espn_event_id}",
                    "draftkings": "ESPN pickcenter close fields from finals, no quote-time fields"},
        "games": sorted(rows, key=lambda row: (row["date"], row["home"], row["away"])),
    }
    if len({(r["date"], r["home"], r["away"]) for r in rows}) != len(rows):
        raise ValueError("duplicate completed matchup")
    args.output.write_bytes(gzip.compress(json.dumps(data, sort_keys=True).encode(), mtime=0))
    print(f"{len(rows)} games; {0 if status is None else sum(e['completed'] for e in status['events'])} "
          f"new scoreboard finals at {captured.isoformat()}", flush=True)


if __name__ == "__main__":
    main()
