"""Schedule and deliver private football and basketball paper reminders."""

from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import monotonic

from psycopg import Error as DatabaseError
from psycopg.types.json import Jsonb

from football_feed import (CONFIRMED_BASELINE, CONFIRMED_OPENED_AT, INITIAL_CONFIRMED_LINEUP,
                           confirmed_review_opening, should_save_pregame_context)
import football_store as store
import sports_portfolio as portfolio


SCAN_HOURS = 72
LATE_AFTER = timedelta(minutes=10)
MAX_BATCH_REVIEWS = 4
REVIEW_BUDGET = timedelta(minutes=8, seconds=15)
REGULAR_REVIEW_BUDGET = timedelta(minutes=1)
REVIEW_SECONDS = timedelta(seconds=22)
STATES = {"scheduled", "cancelled", "postponed", "started"}
CONTEXT_FIELDS = {"fixture_id", "kickoff", "team_ids", "source", "observed_at", "lineup_type", "lineup_provider", "lineups", "sha256"}
BASKETBALL_BASELINE = "basketball_news_baseline"
SPORT_LABELS = {"football": "足球", "basketball": "篮球"}
WORKERS = {
    "urgent": ("urgent-1", "urgent-2"),
    "routine": ("routine-1", "routine-2", "routine-3"),
}
FOCUS_KEY = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,119}\Z")
FOCUS_HEADING = "足篮重点关注｜北京时间"
FOCUS_PREVIEW_HEADING = re.compile(re.escape(FOCUS_HEADING) + r" · .{1,100} \d+(?:\.\d+)?倍")
FOCUS_STAKE = re.compile(r"模拟\s*[¥￥]\s*(\d+(?:\.\d+)?)")
FOCUS_FIXTURE_LINE = re.compile(r"^(?:[1-5]\.\s+)?\d{1,2}月\d{1,2}日\s+\d{2}:\d{2}\s*｜\s*\S")
FOCUS_LIMIT = 5

# Only these exact historical occurrences use the fixed assessment timers.
LEGACY_DATE = (2026, 11, 22)
LEGACY_FIXTURES = {
    "5868133": ((13, 0), ("8634", "10205"), ("Barcelona", "巴塞罗那"), ("Villarreal", "比利亚雷亚尔")),
    "5781804": ((13, 30), ("8593", "10217"), ("Ajax", "阿贾克斯"), ("ADO Den Haag", "ADO 海牙")),
    "5802993": ((16, 15), ("8689", "9851"), ("Lorient", "洛里昂"), ("Rennes", "Stade Rennais", "雷恩")),
    "5795521": ((16, 30), ("8650", "10260"), ("Liverpool", "利物浦"), ("Manchester United", "Man United", "曼联")),
    "5802994": ((19, 45), ("8592", "8682"), ("Marseille", "Olympique Marseille", "Olympique de Marseille", "马赛"), ("Le Mans", "勒芒")),
}
LEGACY_LEAGUES = {
    "5868133": ("lal", "西甲"), "5781804": ("ere", "荷甲"), "5802993": ("fl1", "法甲"),
    "5795521": ("epl", "英超"), "5802994": ("fl1", "法甲"),
}


def utc_iso(at):
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def text_line(value, cap=90):
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())
    value = "".join(ch for ch in value if unicodedata.category(ch)[0] != "C")
    return value if len(value) <= cap else value[:cap - 1] + "…"


def normalized_team(value):
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKD", value).casefold()
    parts = re.findall(r"[^\W_]+", "".join(ch for ch in value if not unicodedata.combining(ch)))
    return "".join(part for part in parts if part not in {"afc", "fc", "cf", "1901"})


def team_pair_matches(left, right):
    for side in ("home", "away"):
        keys = (f"fotmob_{side}_id", f"{side}_id")
        direct = next((key for key in keys if left.get(key) and right.get(key)), None)
        if direct:
            if str(left[direct]) != str(right[direct]):
                return False
        else:
            left_names = {normalized_team(left.get(key)) for key in (side, f"{side}_alias")}
            right_names = {normalized_team(right.get(key)) for key in (side, f"{side}_alias")}
            if not (left_names & right_names) - {""}:
                return False
    return True


def historical_kickoff(pin):
    return datetime(*LEGACY_DATE, *pin[0], tzinfo=UTC)


def historical_owner(info, kickoff):
    if info.get("sport", "football") != "football":
        return None
    for fixture_id, pin in LEGACY_FIXTURES.items():
        if kickoff != historical_kickoff(pin):
            continue
        fotmob_id = info.get("fixture_id")
        if fotmob_id and str(fotmob_id) != fixture_id:
            continue
        actual_teams = tuple(str(info[key]) for key in ("fotmob_home_id", "fotmob_away_id") if info.get(key))
        if len(actual_teams) == 2:
            if actual_teams == pin[1]:
                return fixture_id
            continue
        home = {normalized_team(info.get("home")), normalized_team(info.get("home_alias"))}
        away = {normalized_team(info.get("away")), normalized_team(info.get("away_alias"))}
        if home.intersection(map(normalized_team, pin[2])) and away.intersection(map(normalized_team, pin[3])):
            return fixture_id
    return None


def validated_observations(fixtures, start, end):
    valid, rejected = [], 0
    for item in fixtures:
        try:
            if not isinstance(item, dict):
                raise ValueError("invalid fixture")
            kickoff = store.timestamp(item.get("kickoff"))
            if not start <= kickoff < end:
                continue
            if not isinstance(item.get("id"), str) or not item["id"] or len(item["id"]) > 240:
                raise ValueError("invalid fixture key")
            if not text_line(item.get("home")) or not text_line(item.get("away")):
                raise ValueError("invalid teams")
            if not isinstance(item.get("league"), str) or not item["league"]:
                raise ValueError("invalid competition")
            if item.get("sport", "football") not in SPORT_LABELS:
                raise ValueError("invalid sport")
            if item.get("state", "scheduled") not in STATES:
                raise ValueError("invalid fixture state")
            payload = dict(item)
            for reserved in ("review_context", CONFIRMED_BASELINE, "review_verified_move", "_paper_scopes", BASKETBALL_BASELINE):
                payload.pop(reserved, None)
            payload["kickoff"] = utc_iso(kickoff)
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) > 256_000 or b"\\u0000" in encoded:
                raise ValueError("invalid fixture encoding")
            valid.append((payload, kickoff))
        except (ValueError, TypeError, OverflowError):
            rejected += 1
    return valid, rejected


def limited_stats(value, depth=0):
    if isinstance(value, dict) and depth < 3:
        return {text_line(key, 48): limited_stats(val, depth + 1) for key, val in list(value.items())[:30]}
    if isinstance(value, (bool, int)) or value is None:
        return value
    return text_line(value, 96) if isinstance(value, str) else None


def scan_summary(stats, fixtures, rejected):
    return {
        "partial": stats.get("partial") is not False or rejected != 0,
        "fixtures": len(fixtures),
        "leagues": len({item.get("league") for item, _ in fixtures}),
        "modeled": sum(isinstance(item.get("model"), dict) for item, _ in fixtures),
        "highlighted": sum(item.get("highlight") is True for item, _ in fixtures),
        "matched": sum(bool(item.get("fixture_id")) for item, _ in fixtures),
        "rejected": rejected + (stats.get("rejected") if type(stats.get("rejected")) is int else 0),
        "window_start": limited_stats(stats.get("window_start")),
        "window_end": limited_stats(stats.get("window_end")),
        "sources": limited_stats(stats.get("sources")),
    }


def stage_windows(kickoff, watch, legacy_owner):
    advance_end = 90 if watch or legacy_owner else 15
    windows = [("advance", 180, advance_end)]
    if watch or legacy_owner:
        windows.extend((("t90", 90, 30), ("t30", 30, 0 if legacy_owner else 15)))
    windows.append(("safety", 15, 0))
    return [(stage, kickoff - timedelta(minutes=start), kickoff - timedelta(minutes=end)) for stage, start, end in windows]


def find_fixture(connection, info, kickoff):
    sport = info.get("sport", "football")
    row = connection.execute(
        "SELECT f.* FROM fixture_alias a JOIN fixture f ON f.id = a.fixture_key WHERE a.source_id = %s",
        (info["id"],),
    ).fetchone()
    if row:
        if row["info"].get("sport", "football") != sport:
            raise store.StoreError("fixture_alias_sport_mismatch")
        return row
    fotmob_id = str(info["fixture_id"]) if info.get("fixture_id") else ""
    rows = connection.execute(
        """
        SELECT * FROM fixture WHERE coalesce(info->>'sport', 'football') = %s
          AND (kickoff = %s OR (%s <> '' AND info->>'fixture_id' = %s AND kickoff > %s))
        """,
        (sport, kickoff, fotmob_id, fotmob_id, kickoff - timedelta(days=7)),
    ).fetchall()
    matching = [row for row in rows if row["info"].get("league") == info.get("league") and team_pair_matches(row["info"], info)]
    return matching[0] if len(matching) == 1 else None


def invalidate_jobs(connection, old, reason):
    connection.execute(
        """
        UPDATE job SET state = 'suppressed', reason = %s, lease_token = NULL, lease_until = NULL
        WHERE fixture_key = %s AND revision = %s AND state IN ('pending', 'leased')
        """,
        (reason, old["id"], old["revision"]),
    )


def replace_fixture(connection, info, kickoff, observed, *, old=None):
    old = old if old is not None else find_fixture(connection, info, kickoff)
    state = info.get("state", "scheduled")
    same_teams = old is not None and team_pair_matches(old["info"], info)
    same_clock = old is not None and old["kickoff"] == kickoff
    verified_move = old["info"].get("review_verified_move") if same_teams else None
    if (verified_move and state == "scheduled" and verified_move["previous"] == utc_iso(kickoff)
            and not info.get("review_verified_move") and not info.get("fixture_id")):
        # Wait for the schedule listing or an independent match to catch up with the verified move.
        return old
    if same_teams and same_clock and old["fixture_state"] != "scheduled" and state == "scheduled":
        # An active Gamma listing alone cannot overturn an explicit fixture-status check.
        state = old["fixture_state"]
    unchanged = same_teams and same_clock and old["fixture_state"] == state
    data = dict(info, kickoff=utc_iso(kickoff), state=state)
    data.pop(CONFIRMED_BASELINE, None)
    data.pop(CONFIRMED_OPENED_AT, None)
    data.pop(INITIAL_CONFIRMED_LINEUP, None)
    data.pop(BASKETBALL_BASELINE, None)
    if unchanged and old["info"].get("research") and not data.get("research"):
        for field in ("research", "fixture_id", "fotmob_home_id", "fotmob_away_id", "fotmob_league_id"):
            if field in old["info"]:
                data.setdefault(field, old["info"][field])
    if unchanged and old["info"].get("review_context") and not data.get("review_context"):
        data["review_context"] = old["info"]["review_context"]
    if unchanged and old["info"].get(CONFIRMED_BASELINE):
        data[CONFIRMED_BASELINE] = old["info"][CONFIRMED_BASELINE]
        if CONFIRMED_OPENED_AT in old["info"]:
            data[CONFIRMED_OPENED_AT] = old["info"][CONFIRMED_OPENED_AT]
        if INITIAL_CONFIRMED_LINEUP in old["info"]:
            data[INITIAL_CONFIRMED_LINEUP] = old["info"][INITIAL_CONFIRMED_LINEUP]
    if unchanged and data.get("sport") == "basketball" and old["info"].get(BASKETBALL_BASELINE):
        from basketball_feed import validate_news_baseline

        baseline_info = data
        if not data.get("basketball_official") and old["info"].get("id") == data["id"]:
            baseline_info = old["info"]
        baseline = validate_news_baseline(baseline_info, old["info"][BASKETBALL_BASELINE])
        if baseline:
            data[BASKETBALL_BASELINE] = baseline
    if unchanged and verified_move and not data.get("review_verified_move"):
        data["review_verified_move"] = verified_move
    if not unchanged:
        data.pop("review_context", None)
    if same_teams and not same_clock:
        model = data.get("model")
        if not isinstance(model, dict) or model.get("kickoff") != utc_iso(kickoff):
            data.pop("model", None)
    legacy_id = historical_owner(data, kickoff)
    highlighted = data.get("highlight") is True or legacy_id is not None or bool(unchanged and old["highlight"])
    watch = state == "scheduled" and (highlighted or data.get("watch") is True or bool(unchanged and old["watch"]))
    identity = old["id"] if old else info["id"]
    revision = old["revision"] + int(not unchanged) if old else 1
    first_seen = old["first_seen"] if unchanged else observed
    saved = connection.execute(
        """
        INSERT INTO fixture (id, revision, kickoff, fixture_state, info, watch, highlight, first_seen, last_seen, legacy_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
          revision = excluded.revision, kickoff = excluded.kickoff, fixture_state = excluded.fixture_state,
          info = excluded.info, watch = excluded.watch, highlight = excluded.highlight,
          first_seen = excluded.first_seen, last_seen = excluded.last_seen, missing_scans = 0, legacy_id = excluded.legacy_id
        RETURNING *
        """,
        (identity, revision, kickoff, state, Jsonb(data), watch, highlighted, first_seen, observed, legacy_id),
    ).fetchone()
    connection.execute("INSERT INTO fixture_alias VALUES (%s, %s) ON CONFLICT DO NOTHING", (info["id"], saved["id"]))
    if old is not None and not unchanged:
        invalidate_jobs(connection, old, "fixture_schedule_or_identity_changed")
    if state == "scheduled":
        for stage, start, end in stage_windows(kickoff, watch, legacy_id):
            if observed < end:
                store.add_job(connection, stage, start, end, fixture=saved,
                              legacy_window=bool(legacy_id and stage in {"t90", "t30"}))
            elif stage == "advance":
                connection.execute(
                    """UPDATE job SET state = 'expired', reason = 'later_stage_open', lease_token = NULL, lease_until = NULL
                       WHERE fixture_key = %s AND revision = %s AND stage = 'advance' AND state IN ('pending', 'leased')""",
                    (saved["id"], saved["revision"]),
                )
    return saved


def scan(connection, *, preview=False):
    from football_feed import discover

    store.binding(connection)
    if not preview and not store.lock_scan(connection):
        return {"state": "another_scan_running"}
    started = store.db_now(connection)
    end = started + timedelta(hours=SCAN_HOURS)
    try:
        items, stats = discover(started, hours=SCAN_HOURS, source_cache=store.source_cache(connection))
        observations, rejected = validated_observations(items, started, end)
        summary = scan_summary(stats, observations, rejected)
    except Exception:
        if not preview:
            store.save_scan_state(connection, started, store.db_now(connection), "failed", {"error": "public_discovery_unavailable"})
        raise store.StoreError("public_discovery_unavailable") from None
    result = "partial" if summary["partial"] else "complete"
    if preview:
        return {"state": result, **summary}
    with connection.transaction():
        store.lock_queue(connection)
        observed = store.db_now(connection)
        seen = [replace_fixture(connection, info, kickoff, observed)["id"] for info, kickoff in observations if kickoff > observed]
        if not summary["partial"]:
            connection.execute(
                """
                UPDATE fixture SET missing_scans = least(2, missing_scans + 1)
                WHERE kickoff > %s AND kickoff < %s AND fixture_state = 'scheduled'
                 AND coalesce(info->>'sport', 'football') = 'football'
                 AND last_seen < %s AND NOT (id = ANY(%s))
                """,
                (observed, end, started, seen),
            )
        store.save_source_cache(connection, stats.get("source_cache"))
        store.save_scan_state(connection, started, observed, result, summary)
    return {"state": result, **summary}


def historical_confirmations(connection, now, target):
    if now.date() != datetime(*LEGACY_DATE, tzinfo=UTC).date():
        return
    try:
        if store.owner_proof()[0] != target:
            return
    except store.StoreError:
        return
    for fixture_id, pin in LEGACY_FIXTURES.items():
        kickoff = historical_kickoff(pin)
        for lead in (90, 30):
            starts = kickoff - timedelta(minutes=lead)
            deadline = kickoff - timedelta(minutes=30 if lead == 90 else 0)
            if not starts <= now < kickoff + timedelta(minutes=15):
                continue
            try:
                item = store.read_private_json(store.LEGACY / f"{fixture_id}-{lead}" / "window-send-receipt.json")
                confirmed = store.timestamp(item.get("confirmed_at"))
                if (
                    item.get("stage_key") != f"pal-football-20261122-{fixture_id}-{lead}"
                    or item.get("state") != "accepted" or item.get("platform") != "weixin"
                    or item.get("target_verified") is not True or item.get("body_variant") not in {"primary", "fallback"}
                    or not starts <= confirmed <= min(deadline, now + timedelta(seconds=2))
                    or not store.is_hex(item.get("message_id_sha256"))
                ):
                    continue
            except (OSError, ValueError, TypeError, OverflowError):
                continue
            connection.execute(
                "INSERT INTO legacy_acceptance VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (fixture_id, lead, confirmed, item["message_id_sha256"]),
            )


def ensure_legacy_jobs(connection, now):
    for fixture_id, pin in LEGACY_FIXTURES.items():
        kickoff = historical_kickoff(pin)
        if not kickoff - timedelta(hours=3) <= now < kickoff:
            continue
        fixtures = connection.execute(
            """SELECT * FROM fixture WHERE coalesce(info->>'sport', 'football') = 'football'
               AND (kickoff = %s OR legacy_id = %s OR info->>'fixture_id' = %s)""",
            (kickoff, fixture_id, fixture_id),
        ).fetchall()
        matches = [
            row for row in fixtures
            if row["legacy_id"] == fixture_id or str(row["info"].get("fixture_id")) == fixture_id
            or historical_owner(row["info"], row["kickoff"]) == fixture_id
        ]
        if not matches:
            league, league_name = LEGACY_LEAGUES[fixture_id]
            identity = f"fotmob:{fixture_id}"
            info = {
                "id": identity, "fixture_id": fixture_id, "league": league, "league_name": league_name,
                "home": pin[2][0], "away": pin[3][0], "home_alias": pin[2][-1], "away_alias": pin[3][-1],
                "fotmob_home_id": pin[1][0], "fotmob_away_id": pin[1][1],
                "kickoff": utc_iso(kickoff), "state": "scheduled",
            }
            # A fixed schedule entry is enough to await its independent producer. Only a discovery
            # observation should make this pin look fresh to the routine feed and morning overview.
            matches = [connection.execute(
                """
                INSERT INTO fixture (id, revision, kickoff, fixture_state, info, watch, highlight,
                                     first_seen, last_seen, legacy_id)
                VALUES (%s, 1, %s, 'scheduled', %s, true, true, %s, %s, %s) RETURNING *
                """,
                (identity, kickoff, Jsonb(info), now, now - store.FIXTURE_FRESHNESS - timedelta(seconds=1), fixture_id),
            ).fetchone()]
        for row in matches:
            if row["kickoff"] != kickoff or row["fixture_state"] != "scheduled" or historical_owner(row["info"], kickoff) != fixture_id:
                continue
            if row["legacy_id"] != fixture_id:
                connection.execute("UPDATE fixture SET legacy_id = %s WHERE id = %s", (fixture_id, row["id"]))
            for stage, starts, deadline in stage_windows(kickoff, True, fixture_id):
                if stage in {"t90", "t30"} and now < deadline:
                    store.add_job(connection, stage, starts, deadline, fixture=row, legacy_window=True)


def worker_units(work):
    return [f"pal-football-notify-worker@{worker}.service" for kind, workers in WORKERS.items() if work.get(kind) for worker in workers]


def start_workers(work):
    units = worker_units(work)
    if units:
        result = subprocess.run(["/usr/bin/systemctl", "--user", "start", "--no-block", *units],
                                capture_output=True, timeout=10, check=False)
        if result.returncode:
            raise store.StoreError("delivery_workers_not_started")
    return units


def capture_scheduled_lineups(connection):
    from football_feed import LINEUP_BASELINE_CUTOFF, LINEUP_BASELINE_LEAD, collect_confirmed_baselines, published_lineup_time

    with connection.transaction():
        store.lock_queue(connection)
        now = store.db_now(connection)
        minute, claims = store.claim_lineup_attempts(
            connection, now, lead=LINEUP_BASELINE_LEAD, cutoff=LINEUP_BASELINE_CUTOFF,
        )
    summary = {"attempted": len(claims), "captured": 0, "rejected": {}}
    if not claims:
        return summary

    results = collect_confirmed_baselines(claims, now)
    with connection.transaction():
        store.lock_queue(connection)
        recorded = store.db_now(connection)
        for claim, (reason, context) in zip(claims, results, strict=True):
            current = connection.execute(
                "SELECT revision, kickoff, fixture_state, legacy_id, info FROM fixture WHERE id = %s",
                (claim["fixture_key"],),
            ).fetchone()
            identity_fields = ("fixture_id", "fotmob_league_id", "fotmob_home_id", "fotmob_away_id", "kickoff")
            if (not current or current["revision"] != claim["revision"] or current["kickoff"] != claim["kickoff"]
                    or current["fixture_state"] != "scheduled" or current["legacy_id"] is not None
                    or any(current["info"].get(field) != claim["info"].get(field) for field in identity_fields)):
                reason, context = "fixture_revision_or_identity_changed", None
            else:
                first = confirmed_review_opening(current["info"])
                if context:
                    context = review_context(current["info"], {"review_context": context})
                    observed = published_lineup_time(current["info"], context)
                    if observed is None or not now <= observed <= recorded < current["kickoff"]:
                        reason, context = "lineup_evidence_invalid", None
                    else:
                        saved = review_context(current["info"], {
                            "review_context": current["info"].get(CONFIRMED_BASELINE),
                        })
                        saved_at = published_lineup_time(current["info"], saved)
                        if saved_at is None or saved_at < observed:
                            update = {CONFIRMED_BASELINE: context}
                            first = confirmed_review_opening(current["info"] | update) if first is None else first
                            if first is not None:
                                update[CONFIRMED_OPENED_AT] = utc_iso(first)
                            if INITIAL_CONFIRMED_LINEUP not in current["info"]:
                                update[INITIAL_CONFIRMED_LINEUP] = saved if saved_at is not None else context
                            connection.execute("UPDATE fixture SET info = info || %s WHERE id = %s",
                                               (Jsonb(update), claim["fixture_key"]))
                # Also recover an older captured XI if this minute's source fetch failed.
                # The review worker still requires a current, independent confirmation and a fresh quote.
                if first is not None and first <= recorded and store.wake_confirmed_review(
                        connection, claim, first, recorded, PRICE_RECHECKS | {
                            "late_review", "opening_pending", "arrived_after_round_close", "window_already_processed",
                        }):
                    summary["reviews_activated"] = summary.get("reviews_activated", 0) + 1
            store.finish_lineup_attempt(connection, claim, minute, reason=reason, context=context, recorded_at=recorded)
            if context:
                summary["captured"] += 1
            else:
                summary["rejected"][reason] = summary["rejected"].get(reason, 0) + 1
    return summary


def plan_tick(connection):
    with connection.transaction():
        store.lock_queue(connection)
        now = store.db_now(connection)
        owner = store.binding(connection)
        historical_confirmations(connection, now, owner["target_digest"])
        ensure_legacy_jobs(connection, now)
        store.sweep(connection, now)
        classes = store.due_classes(connection, now)
        if store.recoverable(connection, now) and not classes.get("routine"):
            classes["routine"] = 1
    workers = start_workers(classes)
    lineups = capture_scheduled_lineups(connection)
    if lineups.get("reviews_activated"):
        # The tick may have dispatched the earlier round before the starting XI was published.
        workers = list(dict.fromkeys([*workers, *start_workers({"routine": 1})]))
    return {"state": "planned", "schedule_notifications": "disabled", "due": classes, "workers": workers,
            "confirmed_lineups": lineups}


def review_context(info, update):
    if not isinstance(update, dict) or not isinstance(update.get("review_context"), dict):
        return None
    context = {key: val for key, val in update["review_context"].items() if key in CONTEXT_FIELDS}
    expected_teams = [info.get("fotmob_home_id"), info.get("fotmob_away_id")]
    try:
        signed = {key: value for key, value in context.items() if key != "sha256"}
        serialized = json.dumps(signed, sort_keys=True, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
        if (not info.get("fixture_id") or str(context.get("fixture_id")) != str(info["fixture_id"])
                or context.get("kickoff") != info["kickoff"] or not all(expected_teams) or context.get("team_ids") != expected_teams
                or not store.is_hex(context.get("sha256")) or context["sha256"] != store.digest(serialized)
                or len(serialized.encode("utf-8")) > 32000):
            return None
        observed = store.timestamp(context.get("observed_at"))
        if not store.timestamp(info["kickoff"]) - timedelta(hours=8) <= observed < store.timestamp(info["kickoff"]):
            return None
    except (ValueError, TypeError, OverflowError):
        return None
    return context


def verified_review_update(info, stage, update):
    if info.get("sport") != "basketball":
        return "review_context", review_context(info, update)
    if stage not in {"advance", "t90"} or not isinstance(update, dict) or BASKETBALL_BASELINE not in update:
        return BASKETBALL_BASELINE, None
    from basketball_feed import validate_news_baseline

    return BASKETBALL_BASELINE, validate_news_baseline(info, update[BASKETBALL_BASELINE])


def update_review_fixture(connection, row, review):
    reason = review.get("reason_code")
    moved = None
    if review.get("moved_kickoff"):
        try:
            moved = store.timestamp(review["moved_kickoff"])
        except (ValueError, TypeError, OverflowError):
            pass
    field, context = verified_review_update(row["info"], row["stage"], review.get("fixture_update"))
    new_state = "started" if reason == "at_or_after_kickoff" else reason
    retires = new_state in {"cancelled", "postponed", "started"} or moved is not None and moved != row["kickoff"]
    if not retires and context is None:
        return True
    with connection.transaction():
        store.lock_queue(connection)
        current = connection.execute("SELECT * FROM fixture WHERE id = %s", (row["fixture_key"],)).fetchone()
        if not current or current["revision"] != row["revision"]:
            return False
        if retires:
            now = store.db_now(connection)
            revised_kickoff = moved or current["kickoff"]
            state = "scheduled" if moved and moved > now else new_state if new_state in STATES else "started"
            revised = dict(current["info"], state=state)
            if moved and state == "scheduled":
                revised["review_verified_move"] = {"previous": utc_iso(current["kickoff"]), "observed_at": utc_iso(now)}
            replace_fixture(connection, revised, revised_kickoff, now, old=current)
            return False
        _, context = verified_review_update(current["info"], row["stage"], {field: context})
        if context is None:
            return False
        if field == "review_context" and not should_save_pregame_context(current["info"], context):
            return True
        _, previous = verified_review_update(current["info"], row["stage"], {field: current["info"].get(field)})
        if not previous or store.timestamp(context["observed_at"]) > store.timestamp(previous["observed_at"]):
            info = current["info"] | {field: context}
            connection.execute("UPDATE fixture SET info = %s WHERE id = %s", (Jsonb(info), current["id"]))
    return True


def unavailable_review(reason="review_unavailable"):
    return {"status": "watch", "reason_code": reason, "coverage_gap": True}


def fetch_review(row, now, *, preview=False):
    try:
        if row.get("legacy_id") and row["stage"] in {"t90", "t30"}:
            from football_legacy import review

            result = review(row, now, wait=not preview)
        elif row["info"].get("sport") == "basketball":
            from sports_runtime import review_basketball

            result = review_basketball(row["info"], now, row["stage"])
        else:
            from football_feed import review

            result = review(row["info"], now, row["stage"])
    except Exception:
        return unavailable_review()
    if not isinstance(result, dict) or result.get("status") not in {"watch", "paper", "skip"}:
        return unavailable_review()
    return result


def paper_window_end(row, at=None):
    if row.get("legacy_id"):
        return row["deadline"]
    if row.get("stage") == "t90":
        confirmed = confirmed_review_opening(row.get("info", {}))
        if confirmed is not None and (at is None or at >= confirmed):
            return row["deadline"]
    return min(row["deadline"], row["starts_at"] + LATE_AFTER)


def apply_paper_scopes(connection, rows):
    from football_admission import active_scopes
    from sports_runtime import basketball_scopes

    sports = {row["info"].get("sport", "football") for row in rows}
    scopes = {}
    if "football" in sports:
        scopes["football"] = active_scopes(connection)
    if "basketball" in sports:
        scopes["basketball"] = basketball_scopes(connection)
    for row in rows:
        info = dict(row["info"])
        info["_paper_scopes"] = frozenset(scope for scope in scopes.get(info.get("sport", "football"), ())
                                          if scope[0] == info.get("league"))
        row["info"] = info


def booked_fixtures(connection, keys):
    if not keys or not portfolio._installed(connection):
        return set()
    return {(row["sport"], row["fixture_key"]) for row in connection.execute(
        """SELECT sport, fixture_key FROM football_notify.sports_portfolio_position
           WHERE restart_key = %s AND fixture_key = ANY(%s)""",
        (portfolio.RESTART_KEY, keys),
    )}


def paper_expiry(row, decision, now):
    from football_shortlist import _preferred_direction

    if decision["status"] != "paper":
        return None
    entry, _ = _preferred_direction(row, decision, now)
    return entry.valid_until if entry else None


def pregame_decisions(connection, rows, *, preview=False, now=None, budget_deadline=None, refresh_token=None):
    booked = set()
    if connection is not None:
        apply_paper_scopes(connection, rows)
        booked = booked_fixtures(connection, [row["fixture_key"] for row in rows if row["stage"] in {"t90", "t30"}])
    origin = now if now is not None else store.db_now(connection)
    tick = monotonic()

    def clock():
        if connection is not None:
            return store.db_now(connection)
        return origin + timedelta(seconds=monotonic() - tick)

    work_until = budget_deadline if budget_deadline is not None else origin + REVIEW_BUDGET
    decisions = {}
    for row in rows:
        stage, info = row["stage"], row["info"]
        if stage in {"t90", "t30"} and (info.get("sport", "football"), row.get("fixture_key")) in booked:
            decisions[row["key"]] = {"status": "watch", "reason_code": "fixture_already_booked"}
        elif stage == "safety" or stage == "advance" and not row["watch"]:
            decisions[row["key"]] = {"status": "watch", "reason_code": stage}
        elif (info.get("sport") == "basketball" and not info.get("_paper_scopes")
              and (stage in {"t90", "t30"} or info.get("league") != "nba" or not isinstance(info.get("model"), dict))):
            decisions[row["key"]] = {"status": "watch", "reason_code": "research_only"}

    def evaluate(row):
        at = clock()
        if at >= min(row["deadline"], row["kickoff"]):
            return {"status": "skip", "reason_code": "window_ended"}
        timed = row["stage"] in {"t90", "t30"}
        cutoff = paper_window_end(row, at) if timed else row["deadline"]
        if at >= work_until or (timed and origin < cutoff <= at + store.MIN_HANDOFF):
            return unavailable_review("review_budget_exhausted")
        result = fetch_review(row, at, preview=preview)
        if not row.get("legacy_id"):
            result = dict(result, assessed_at=clock().isoformat())
        return result

    legacy = []
    stable = []
    for row in rows:
        if row["key"] in decisions:
            continue
        collection = legacy if row.get("legacy_id") and row["stage"] in {"t90", "t30"} else stable
        collection.append(row)
    regular_deadline = None
    # The fixed producers can take minutes. Read them first so routine peers are quoted close to handoff.
    with ThreadPoolExecutor(max_workers=MAX_BATCH_REVIEWS) as pool:
        for group in (legacy, stable):
            if group is stable and group:
                regular_deadline = min(work_until, clock() + REGULAR_REVIEW_BUDGET)
                work_until = regular_deadline
            group.sort(key=lambda row: (
                not (row["stage"] in {"t90", "t30"} and origin < paper_window_end(row, origin)),
                paper_window_end(row, origin) if row["stage"] in {"t90", "t30"} else row["deadline"],
                row.get("detail", {}).get("pregame_review", {}).get("coverage") not in {None, "window_not_evaluated",
                                                                                     "review_budget_exhausted"},
                row.get("tries", 0),
                row["key"],
            ))
            batches = (group,) if group is legacy else (
                group[start:start + MAX_BATCH_REVIEWS] for start in range(0, len(group), MAX_BATCH_REVIEWS)
            )
            for batch in batches:
                for row, review in zip(batch, pool.map(evaluate, batch)):
                    if not preview:
                        update_review_fixture(connection, row, review)
                    decisions[row["key"]] = review
                    if group is stable:
                        expiry = paper_expiry(row, review, clock())
                        if expiry:
                            work_until = min(work_until, expiry - store.MIN_HANDOFF - REVIEW_SECONDS)
    if refresh_token is not None and regular_deadline is not None:
        refresh_expired_reviews(connection, rows, decisions, clock(), regular_deadline, token=refresh_token)
    return decisions


def refresh_expired_reviews(connection, rows, decisions, now, budget_deadline, *, token):
    """Re-quote expired directions within the round's remaining collection time."""
    from football_shortlist import _preferred_direction

    if (now + REVIEW_SECONDS >= budget_deadline
            or not any(decision["status"] == "paper" for decision in decisions.values())):
        return
    current = store.eligible_leases(connection, rows, token, now, handoff_margin=True)
    apply_paper_scopes(connection, current)
    booked = booked_fixtures(connection, [row["fixture_key"] for row in current])
    completed = now + REVIEW_SECONDS
    refresh = []
    protected = budget_deadline
    for row in current:
        if (row["info"].get("sport", "football"), row["fixture_key"]) in booked:
            continue
        entry, reason = _preferred_direction(row, decisions[row["key"]], now)
        if entry:
            protected = min(protected, entry.valid_until - store.MIN_HANDOFF)
        elif reason == "signal_expired" and regular_window_open(row, completed):
            refresh.append(row)
    checked = store.db_now(connection)
    if not refresh or checked + REVIEW_SECONDS >= protected:
        return
    refresh.sort(key=lambda row: (paper_window_end(row), row["key"]))
    # A single concurrent batch must fit around the quotes already ready to send.
    batch = [row for row in refresh if regular_window_open(row, checked + REVIEW_SECONDS)][:MAX_BATCH_REVIEWS]
    if batch:
        updated = pregame_decisions(connection, batch, now=checked, budget_deadline=protected - REVIEW_SECONDS)
        decisions.update((key, result) for key, result in updated.items()
                         if result["reason_code"] != "review_budget_exhausted")


EVIDENCE_GAPS = {
    "review_unavailable", "invalid_paper_evidence", "collection_unavailable", "assessment_unavailable",
    "legacy_evidence_incomplete", "books_unavailable", "markets_unavailable", "unsupported_league",
    "model_unavailable", "independent_fixture_missing", "fotmob_unavailable", "independent_fixture_changed",
    "news_baseline_missing", "lineups_unconfirmed", "direct_contract_unverified",
    "event_unavailable", "independent_provider_unavailable", "model_history_partial",
    "basketball_availability_unavailable", "basketball_availability_baseline_missing",
}
UNEVALUATED_REVIEWS = {
    "advance", "safety", "window_ended", "opening_pending", "late_review", "review_budget_exhausted", "fixture_changed",
    "research_only", "fixture_already_booked",
}
PRICE_RECHECKS = {
    "thresholds_unmet", "news_changed", "signal_expired", "below_top_five", "review_budget_exhausted",
    "nonpositive_conservative_roi",
    "review_unavailable", "model_unavailable", "independent_fixture_missing", "fotmob_unavailable",
    "independent_fixture_changed", "news_baseline_missing", "lineups_unconfirmed", "markets_unavailable",
    "books_unavailable", "direct_contract_unverified",
    "event_unavailable", "independent_provider_unavailable", "model_history_partial",
    "basketball_availability_unavailable", "basketball_availability_baseline_missing",
    "pregame_basketball_availability_changed", "starter_injury_status_conflict",
}


def regular_window_open(row, at):
    return (not row.get("legacy_id") and row["stage"] in {"t90", "t30"}
            and row["starts_at"] <= at < paper_window_end(row, at) - store.MIN_HANDOFF)


def round_review_entries(rows, decisions, reasons, *, kept, next_slot=None):
    entries = []
    for row in rows:
        key = row["key"]
        decision = decisions.get(key, {})
        decision_reason = store.safe_reason(decision.get("reason_code"), "review_unavailable")
        outcome = store.safe_reason(reasons.get(key), decision_reason)
        review = {"reason_code": decision_reason, "outcome": outcome}
        for field in ("assessed_at", "lineup_baseline", "lineup_comparison", "lineup_review", "price_candidates", "candidates",
                      "review_phase", "lineup_window_opened_at"):
            if field in decision:
                review[field] = decision[field]
        if decision.get("signals"):
            review["quotes"] = [{field: signal[field] for field in (
                "market_id", "outcome", "model_version", "price", "book_observed_at", "valid_until",
            ) if field in signal} for signal in decision["signals"] if isinstance(signal, dict)]
        for flag in ("coverage_gap", "partial_books", "partial_coverage"):
            if decision.get(flag) is True:
                review[flag] = True
        if decision_reason == "review_budget_exhausted":
            review["coverage"] = "review_budget_exhausted"
        elif decision_reason in UNEVALUATED_REVIEWS or outcome in UNEVALUATED_REVIEWS:
            review["coverage"] = "window_not_evaluated"
        elif decision_reason in EVIDENCE_GAPS or outcome in EVIDENCE_GAPS or review.get("coverage_gap"):
            review["coverage"] = "source_gap"
        elif review.get("partial_books") or review.get("partial_coverage"):
            review["coverage"] = "partial_evidence"
        else:
            review["coverage"] = "evaluated"
        if isinstance(decision.get("reason_codes"), list):
            review["reason_codes"] = [
                reason for reason in decision["reason_codes"][:64] if store.safe_reason(reason, None)
            ]
        entry = {"key": key, "reason": None if key in kept else outcome, "review": review}
        if (next_slot is not None and key not in kept and outcome in PRICE_RECHECKS
                and regular_window_open(row, next_slot)):
            entry["next_try"] = utc_iso(next_slot)
        entries.append(entry)
    return entries


def current_round_selection(connection, rows, token, target, decisions, now):
    from football_shortlist import select_round

    if any(row.get("legacy_id") for row in rows):
        historical_confirmations(connection, now, target)
    current = store.eligible_leases(connection, rows, token, now, handoff_margin=True)
    apply_paper_scopes(connection, current)
    selection = select_round(connection, current, decisions, now)
    for row in rows:
        if row["key"] not in selection.reasons:
            selection.reasons[row["key"]] = ("window_ended" if row["deadline"] <= now + store.MIN_HANDOFF
                                             else "fixture_changed")
    return selection


def round_body_identity(selection):
    return [row["key"] for row in selection.rows], selection.body, selection.valid_until, selection.allocations


def finish_round(connection, rows, token, key, now, decisions, selection, *, attempt_key=None):
    if attempt_key:
        from football_forward import record_recommendation

        for entry in selection.entries:
            if entry.quote.sport != "football":
                continue
            row = entry.row
            if row.get("legacy_id"):
                row = dict(row, info=row["info"] | entry.quote.settlement_evidence["fixture"])
                entry = replace(entry, row=row)
            if not record_recommendation(connection, row, entry, selected_at=now, signal=entry.signal):
                raise store.StoreError("football_recommendation_not_recorded")
    selected = selection.rows
    next_slot = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    entries = round_review_entries(rows, decisions, selection.reasons, kept={row["key"] for row in selected},
                                   next_slot=next_slot)
    store.record_round_reviews(connection, token, entries, round_key=key, recorded_at=now)
    store.close_round(connection, key, now, len(selected), reason="submitted" if attempt_key else "no_current_paper",
                      attempt_key=attempt_key)


class RoundClaimChanged(Exception):
    """The staged message needs a new proposal or a retry before any booking."""


def verify_claim_quotes(connection, selection, token, decisions, now):
    from football_shortlist import _preferred_direction

    current = store.eligible_leases(connection, selection.rows, token, now, handoff_margin=True)
    apply_paper_scopes(connection, current)
    by_key = {row["key"]: row for row in current}
    for entry in selection.entries:
        key = entry.row["key"]
        if key not in by_key:
            raise RoundClaimChanged
        verified, _ = _preferred_direction(by_key[key], decisions[key], now)
        if verified is None or verified.quote != entry.quote:
            raise RoundClaimChanged


def recover_receipt(connection, attempt, target, session):
    from weixin_delivery import recover

    native = {}
    if attempt["native_session_digest"] is not None:
        if attempt["native_session_digest"] != store.digest(session) or not attempt["native_session_verified"]:
            return "unknown"
        native = {"native_session": session, "native_session_verified": True}
    result = recover(stage_key=attempt["stage_key"], attempt_key=attempt["key"], body_variant=attempt["body_variant"],
                     deadline=attempt["deadline"], target_digest=target, body_digest=attempt["body_digest"], **native)
    with connection.transaction():
        store.lock_queue(connection)
        current = connection.execute("SELECT * FROM attempt WHERE key = %s", (attempt["key"],)).fetchone()
        return store.finish_attempt(connection, current, result, store.db_now(connection))


def worker(connection, worker_id):
    with store.native_delivery_lock(connection) as granted:
        if not granted:
            return {"state": "idle"}
        return run_worker(connection, worker_id)


def claim_pregame_round(connection, now, target, session, candidates):
    dispatch = now.replace(second=0, microsecond=0)

    def slot_key(minute):
        return f"wf-round-{store.digest(json.dumps([target, session, utc_iso(minute)]))[:48]}"

    key = slot_key(dispatch)
    unassigned = [row for row in candidates if not row["detail"].get("pregame_round")]
    if unassigned:
        origins = {}
        for row in unassigned:
            opening = row["starts_at"]
            minute = opening.replace(second=0, microsecond=0)
            if minute < opening:
                minute += timedelta(minutes=1)
            origins[row["key"]] = minute
        origin_keys = {minute: slot_key(minute) for minute in set(origins.values())}
        slots = {slot: (slot, closed) for slot, closed in store.round_slots(
            connection, {key, *origin_keys.values()},
        ).items()}
        # A catch-up round can have members from a missed timer minute. Its durable
        # membership seals those origins as well, for the same owner and native thread.
        for alias in store.round_origin_aliases(connection, origin_keys):
            if alias["key"] == slot_key(alias["dispatch_at"]):
                slots.setdefault(origin_keys[alias["minute"]], (alias["key"], alias["closed"]))
        arrivals, new = {}, []
        for row in unassigned:
            if regular_window_open(row, now):
                if key not in slots:
                    new.append(row)
                continue
            origin = origin_keys[origins[row["key"]]]
            matches = [slots[slot] for slot in (origin, key) if slot in slots]
            closed = next((round_key for round_key, finished in matches if finished), None)
            if closed:
                arrivals.setdefault(closed, []).append(row)
            elif not matches:
                new.append(row)
        for closed, rows in arrivals.items():
            store.record_late_round_arrivals(connection, rows, closed)
        unassigned = new
    recovering = next((row["detail"]["pregame_round"] for row in candidates if row["detail"].get("pregame_round")), None)
    if recovering:
        return recovering, [row for row in candidates if row["detail"].get("pregame_round") == recovering]
    if unassigned:
        store.open_round(connection, key, dispatch, unassigned)
        return key, unassigned
    return None, []


def run_worker(connection, worker_id):
    kind = worker_id.split("-", 1)[0]
    token = secrets.token_hex(16)
    with connection.transaction():
        store.lock_queue(connection)
        now = store.db_now(connection)
        target = store.binding(connection)["target_digest"]
        session = store.native_session(connection)
        historical_confirmations(connection, now, target)
        store.sweep(connection, now, reclaim_round=True)
        if store.native_submission_active(connection, session, now):
            return {"state": "idle"}
        pregame = store.due_pregame_rows(connection, now)
        round_key, rows = claim_pregame_round(connection, now, target, session, pregame)
        if not rows:
            rows = store.due_rows(connection, "urgent", now)
        if rows:
            store.lease_jobs(connection, rows, token, now, round_key=round_key)
        recovery = store.reserve_recovery(connection, now) if not rows and kind == "routine" else None
    if recovery:
        return {"state": recover_receipt(connection, recovery, target, session), "operation": "receipt_recovery"}
    if not rows:
        return {"state": "idle"}
    if round_key:
        return deliver_pregame_round(connection, rows, token, round_key, target, session, now)
    return deliver_focus(connection, rows[0], token, target, session)


def deliver_pregame_round(connection, rows, token, key, target, session, claimed_at):
    from weixin_delivery import deliver

    budget_deadline = claimed_at + REVIEW_BUDGET
    decisions = pregame_decisions(connection, rows, now=claimed_at, budget_deadline=budget_deadline,
                                  refresh_token=token)

    def selection():
        at = store.db_now(connection)
        return at, current_round_selection(connection, rows, token, target, decisions, at)

    while True:
        with connection.transaction():
            store.lock_queue(connection)
            now, prepared = selection()
            selected, body, valid_until = prepared.rows, prepared.body, prepared.valid_until
            if body is None:
                finish_round(connection, rows, token, key, now, decisions, prepared)
                return {"state": "suppressed", "stage": "pregame", "fixtures": 0, "considered": len(rows)}
            # Keep review coverage available when transport fails before the durable claim.
            store.record_round_reviews(connection, token, round_review_entries(
                rows, decisions, prepared.reasons, kept={row["key"] for row in rows},
            ))

        claimed = False

        def on_submit(*, attempt_key, body_variant, body_digest):
            nonlocal claimed
            if body_variant != "primary" or body_digest != store.digest(body):
                return False
            from football_admission import ADMISSION_LOCK
            from football_shortlist import LIST_LIMIT

            try:
                with connection.transaction():
                    store.lock_queue(connection)
                    if any(entry.quote.sport == "football" for entry in prepared.entries):
                        connection.execute("SELECT pg_advisory_xact_lock(%s, %s)", (store.LOCK_NAMESPACE, ADMISSION_LOCK))
                    _, current = selection()
                    if round_body_identity(current) != round_body_identity(prepared):
                        return False
                    portfolio.enter(connection, current.quotes, round_key=key, expected=current.allocations, limit=LIST_LIMIT)
                    account = portfolio.status(connection)
                    if (account["available_cash_fen"] != current.available_cash_fen
                            or account["realized_pnl_fen"] != current.realized_pnl_fen):
                        raise RoundClaimChanged
                    at = store.db_now(connection)
                    verify_claim_quotes(connection, current, token, decisions, at)
                    if not store.submit(connection, current.rows, token, target, key, valid_until, attempt_key=attempt_key,
                                        body_variant=body_variant, body_digest=body_digest, session=session):
                        raise RoundClaimChanged
                    finish_round(connection, rows, token, key, at, decisions, current, attempt_key=attempt_key)
                    if valid_until <= store.db_now(connection) + store.MIN_HANDOFF:
                        raise RoundClaimChanged
            except (RoundClaimChanged, portfolio.AllocationChanged):
                return False
            claimed = True
            return True

        try:
            result = deliver(body, stage_key=key, starts_at=max(row["starts_at"] for row in selected),
                             deadline=min(row["deadline"] for row in selected), target_digest=target,
                             on_submit=on_submit, valid_until=valid_until, native_session=session)
        except Exception:
            result = {"state": "unconfirmed", "reason": "handoff_interrupted"}
        if not claimed:
            with connection.transaction():
                store.lock_queue(connection)
                _, current = selection()
                if round_body_identity(current) != round_body_identity(prepared):
                    continue
        retry = min(300, 30 * (max(row["tries"] for row in rows) + 1))
        state = store.finish_delivery(connection, rows, token, result, retry_seconds=retry)
        return {"state": state, "stage": "pregame", "fixtures": len(selected), "considered": len(rows)}


def focus_stage_key(job_key):
    return f"wf-focus-{job_key[:48]}"


def deliver_focus(connection, row, token, target, session):
    from weixin_delivery import deliver

    stage_key = focus_stage_key(row["key"])
    rows = [row]

    def current_body():
        now = store.db_now(connection)
        current = store.eligible_leases(connection, rows, token, now, handoff_margin=True)
        return focus_body(connection, current[0], now) if current else None

    def suppress():
        connection.execute(
            """UPDATE job SET state = 'suppressed', reason = 'focus_unavailable',
                              lease_token = NULL, lease_until = NULL
               WHERE key = %s AND state = 'leased' AND lease_token = %s""",
            (row["key"], token),
        )
        return {"state": "suppressed", "stage": "focus", "fixtures": 0, "stage_key": stage_key}

    with connection.transaction():
        store.lock_queue(connection)
        body = current_body()
        if body is None:
            return suppress()
    expected_body = store.digest(body)
    withdrawn = None

    def on_submit(*, attempt_key, body_variant, body_digest):
        nonlocal withdrawn
        if body_variant != "primary" or body_digest != expected_body:
            return False
        with connection.transaction():
            store.lock_queue(connection)
            if portfolio._installed(connection):
                connection.execute("SELECT 1 FROM football_notify.sports_portfolio_account WHERE restart_key = %s FOR UPDATE",
                                   (portfolio.RESTART_KEY,)).fetchone()
            if current_body() != body:
                withdrawn = suppress()
                return False
            return store.submit(connection, rows, token, target, stage_key, row["deadline"], attempt_key=attempt_key,
                                body_variant=body_variant, body_digest=body_digest, session=session)

    try:
        result = deliver(body, stage_key=stage_key, starts_at=row["starts_at"], deadline=row["deadline"], target_digest=target,
                         on_submit=on_submit, native_session=session)
    except Exception:
        result = {"state": "unconfirmed", "reason": "handoff_interrupted"}
    if withdrawn:
        return withdrawn
    state = store.finish_delivery(connection, rows, token, result)
    return {"state": state, "stage": "focus", "fixtures": len(row["detail"]["fixture_keys"]), "stage_key": stage_key}


def login_lingering():
    try:
        result = subprocess.run(["/usr/bin/loginctl", "show-user", store.ROLE, "--property=Linger", "--value"],
                                text=True, capture_output=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0 and result.stdout.strip() in {"yes", "no"}:
        return result.stdout.strip() == "yes"
    return None


def status(connection):
    from sports_runtime import basketball_scan_state

    now = store.db_now(connection)
    owner = store.binding(connection)
    scanned = store.scan_state(connection)
    overview = connection.execute("SELECT * FROM digest_day ORDER BY local_day DESC LIMIT 1").fetchone()
    fixtures = connection.execute(
        """SELECT fixture_state, count(*) AS count, count(*) FILTER (WHERE highlight) AS highlighted,
                  count(*) FILTER (WHERE missing_scans >= 2 OR last_seen < %s) AS stale
            FROM fixture WHERE kickoff > %s GROUP BY fixture_state ORDER BY fixture_state""",
        (now - store.FIXTURE_FRESHNESS, now),
    ).fetchall()
    jobs = connection.execute(
        """SELECT stage, state, count(*) AS count FROM job WHERE deadline > %s GROUP BY stage, state ORDER BY stage, state""",
        (now - timedelta(days=3),),
    ).fetchall()
    next_job = connection.execute("SELECT min(next_try) AS at FROM job WHERE state = 'pending' AND deadline > %s", (now,)).fetchone()
    return {"state": "ready", "owner_binding_confirmed": True, "activated_at": owner["activated_at"],
            "as_of": now, "lingering": login_lingering(), "scan": scanned,
            "fixtures": fixtures, "schedule_notifications": "disabled", "last_schedule_digest": overview, "recent_jobs": jobs,
            "due": store.due_classes(connection, now), "next_pending_at": next_job["at"],
            "delivery_metric": "weixin_platform_acceptance",
            "portfolio": portfolio.status(connection), "basketball_scan": basketball_scan_state(connection)}


def verified_focus_fixtures(connection, fixture_ids, now):
    return connection.execute(
        """
        SELECT DISTINCT f.id, f.revision, f.kickoff
        FROM unnest(%s::text[]) AS requested(id)
        LEFT JOIN fixture_alias a ON a.source_id = requested.id
        JOIN fixture f ON f.id = coalesce(a.fixture_key, requested.id)
        WHERE f.kickoff > %s AND f.fixture_state = 'scheduled'
          AND f.last_seen BETWEEN %s AND %s AND f.missing_scans < 2
        ORDER BY f.id
        """,
        (fixture_ids, now + store.MIN_HANDOFF, now - store.FIXTURE_FRESHNESS, now),
    ).fetchall()


def valid_focus_body(body, fixture_count):
    from weixin_delivery import valid_body

    if not 1 <= fixture_count <= FOCUS_LIMIT or not valid_body(body):
        return False
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if (lines[0] != FOCUS_HEADING and not FOCUS_PREVIEW_HEADING.fullmatch(lines[0])
            or FOCUS_STAKE.findall(body) != ["0.00"] * fixture_count):
        return False
    starts = [i for i, line in enumerate(lines) if FOCUS_FIXTURE_LINE.match(line)]
    if len(starts) != fixture_count or starts[0] != 1:
        return False
    for start, end in zip(starts, [*starts[1:], len(lines)]):
        block = lines[start + 1:end]
        odds_start = next((i for i, line in enumerate(block) if line.startswith("参考赔率")), None)
        if odds_start is None:
            return False
        market_start = next((i for i, line in enumerate(block[:odds_start]) if "｜" in line), None)
        if market_start is None or " 对 " not in " ".join(block[:market_start]):
            return False
        market, _, direction = " ".join(block[market_start:odds_start]).partition("｜")
        metrics = " ".join(block[odds_start:])
        if (not market.strip() or not direction.strip() or not re.match(r"参考赔率\s*\d+(?:\.\d+)?\s*倍", metrics)
                or not re.search(r"模型(?:概率)?\s*\d+(?:\.\d+)?%", metrics)
                or FOCUS_STAKE.findall(metrics) != ["0.00"]):
            return False
    return True


def focus_occurrences(matches):
    return [{"id": row["id"], "revision": row["revision"], "kickoff": utc_iso(row["kickoff"])} for row in matches]


def focus_evidence_matches(evidence, matches):
    if not isinstance(evidence, list) or not any(
        isinstance(item, dict) and "fixture_key" in item and ("revision" in item or "kickoff" in item)
        for item in evidence
    ):
        return True
    if len(evidence) != len(matches):
        return False
    observed = set()
    for item in evidence:
        if (not isinstance(item, dict) or not isinstance(item.get("fixture_key"), str)
                or type(item.get("revision")) is not int):
            return False
        try:
            kickoff = store.timestamp(item.get("kickoff"))
        except (TypeError, ValueError, OverflowError):
            return False
        observed.add((item["fixture_key"], item["revision"], kickoff))
    return observed == {(row["id"], row["revision"], row["kickoff"]) for row in matches}


def focus_body(connection, row, now):
    detail = row["detail"]
    ids, body = detail.get("fixture_keys"), detail.get("body")
    if (detail.get("kind") != store.FOCUS_KIND or not isinstance(ids, list) or not 1 <= len(ids) <= FOCUS_LIMIT
            or not valid_focus_body(body, len(ids)) or store.digest(body) != detail.get("body_digest")):
        return None
    if detail.get("correction_key"):
        evidence = detail.get("evidence")
        if not isinstance(evidence, list) or len(evidence) != len(ids):
            return None
        try:
            if any(store.timestamp(item["valid_until"]) <= now for item in evidence):
                return None
        except (TypeError, KeyError, ValueError, OverflowError):
            return None
    matches = verified_focus_fixtures(connection, ids, now)
    if focus_occurrences(matches) != detail.get("fixtures") or booked_fixtures(connection, ids):
        return None
    return body


def enqueue_focus(connection, key, until, fixture_ids, body, *, preview=False, evidence=None, correction_key=None):
    if (not isinstance(key, str) or not FOCUS_KEY.fullmatch(key) or not isinstance(fixture_ids, (list, tuple))
            or not 1 <= len(fixture_ids) <= FOCUS_LIMIT
            or any(not isinstance(item, str) or not item or len(item) > 240 or "\x00" in item for item in fixture_ids)):
        raise store.StoreError("invalid_focus_key_or_fixture")
    if not valid_focus_body(body, len(fixture_ids)):
        raise store.StoreError("invalid_focus_body")
    if evidence is not None:
        try:
            encoded = json.dumps(evidence, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if (not isinstance(evidence, (dict, list)) or len(encoded.encode("utf-8")) > 32_768
                    or "\\u0000" in encoded):
                raise ValueError("invalid_focus_evidence")
            evidence = json.loads(encoded)
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise store.StoreError("invalid_focus_evidence") from None
    try:
        cutoff = store.timestamp(until)
    except (TypeError, ValueError, OverflowError):
        raise store.StoreError("invalid_focus_deadline") from None
    job_key = store.digest(f"owner-focus\n{key}")
    requested = sorted(fixture_ids)
    digest = store.digest(body)
    result = {"key": key, "job_key": job_key, "stage_key": focus_stage_key(job_key), "deadline": cutoff}
    with connection.transaction():
        if not preview:
            store.lock_queue(connection)
            store.binding(connection)
        existing = connection.execute("SELECT state, deadline, detail, reason FROM job WHERE key = %s", (job_key,)).fetchone()
        if existing:
            detail = existing["detail"]
            if (detail.get("kind") != store.FOCUS_KIND or detail.get("requested_fixtures") != requested
                    or detail.get("body_digest") != digest or existing["deadline"] != cutoff
                    or evidence is not None and detail.get("evidence") != evidence
                    or correction_key is not None and detail.get("correction_key") != correction_key):
                raise store.StoreError("focus_key_already_bound")
            if preview:
                return result | {"state": "preview", "body": body, "existing_state": existing["state"],
                                 "fixture_keys": detail["fixture_keys"]}
            return result | {"state": existing["state"], "reason": existing["reason"], "fixture_keys": detail["fixture_keys"]}
        now = store.db_now(connection)
        matches = verified_focus_fixtures(connection, requested, now)
        if len(matches) != len(requested):
            raise store.StoreError("focus_fixture_unavailable_or_repeated")
        if not focus_evidence_matches(evidence, matches):
            raise store.StoreError("focus_evidence_fixture_changed")
        if cutoff <= now + store.MIN_HANDOFF or cutoff > min(row["kickoff"] for row in matches):
            raise store.StoreError("focus_deadline_outside_prematch")
        ids = [row["id"] for row in matches]
        if booked_fixtures(connection, ids):
            raise store.StoreError("focus_fixture_already_booked")
        result["fixture_keys"] = ids
        if preview:
            return result | {"state": "preview", "body": body, "existing_state": None}
        lines = body.splitlines()
        detail = {"kind": store.FOCUS_KIND, "fixture_keys": ids, "fixtures": focus_occurrences(matches),
                  "requested_fixtures": requested, "body_digest": digest, "body": body,
                  "body_chars": len(body), "body_bytes": len(body.encode("utf-8")), "body_lines": len(lines),
                  "body_first_line": lines[0], "body_last_line": lines[-1]}
        if evidence is not None:
            detail["evidence"] = evidence
        if correction_key is not None:
            detail["correction_key"] = correction_key
        connection.execute(
            """
            INSERT INTO job (key, stage, starts_at, deadline, next_try, state, detail)
            VALUES (%s, 'note', %s, %s, %s, 'pending', %s)
            """,
            (job_key, now, cutoff, now, Jsonb(detail)),
        )
    start_workers({"urgent": 1})
    return result | {"state": "queued"}


def read_focus_body(path):
    from weixin_delivery import MAX_BODY_CHARS

    with open(path, "rb") as handle:
        raw = handle.read(4 * MAX_BODY_CHARS + 1)
    if len(raw) > 4 * MAX_BODY_CHARS:
        raise store.StoreError("invalid_focus_body")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise store.StoreError("invalid_focus_body") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "status", "route-check"):
        commands.add_parser(name)
    for name in ("scan", "tick", "digest", "send-note", "send-focus"):
        command = commands.add_parser(name)
        command.add_argument("--preview", action="store_true", help="Inspect without persisting a job or sending")
        if name in {"send-note", "send-focus"}:
            command.add_argument("--key", required=True, help="Stable caller-provided idempotency key")
            command.add_argument("--until", required=True, help="Last permissible send time, ISO 8601 with offset")
            command.add_argument("--fixture", action="append", required=True,
                                 help="Verified fixture ID or source alias, repeat for each fixture")
        if name == "send-focus":
            command.add_argument("--body-file", required=True, help="UTF-8 body for one focused observation")
    runner = commands.add_parser("worker")
    runner.add_argument("worker_id", choices=[worker for items in WORKERS.values() for worker in items])
    args = parser.parse_args()
    command = args.command
    preview = getattr(args, "preview", False)
    if command in {"digest", "send-note"}:
        print(json.dumps({"state": "disabled", "reason": "schedule_notifications_disabled"}))
        return 0
    try:
        with store.connect(read_only=preview or command in {"status", "route-check"}) as connection:
            if command == "init":
                result = {"state": "initialized", "activated_at": store.initialize(connection), "native_session_bound": True}
            elif command == "scan":
                result = scan(connection, preview=preview)
            elif command == "status" or command == "tick" and preview:
                result = status(connection)
            elif command == "tick":
                result = plan_tick(connection)
            elif command == "worker":
                result = worker(connection, args.worker_id)
            elif command == "route-check":
                from weixin_delivery import check_route

                result = check_route(store.binding(connection)["target_digest"])
                print(json.dumps(result))
                return 0 if result["route_verified"] else 1
            elif command == "send-focus":
                result = enqueue_focus(connection, args.key, args.until, args.fixture,
                                       read_focus_body(args.body_file), preview=preview)
        print(json.dumps(result, ensure_ascii=False, default=lambda value: value.isoformat()))
        return 0
    except store.StoreError as error:
        code = str(error)
    except DatabaseError:
        code = "notification_postgres_unavailable_or_query_failed"
    except (OSError, subprocess.TimeoutExpired):
        code = "notification_operation_unavailable"
    print(json.dumps({"state": "error", "reason": code}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
