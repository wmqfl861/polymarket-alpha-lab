"""Prepare a short, dated owner watchlist using verified football and basketball prices."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json

import football_feed as feed
import football_shortlist as shortlist
import football_store as store
import sports_portfolio as portfolio


TITLE = "足篮重点关注｜北京时间"
REFERENCE_LIFE = timedelta(minutes=10)


def _source(sport, now, end):
    if sport == "football":
        from football_focus import candidates
    else:
        from basketball_focus import candidates
    with store.connect(read_only=True) as db:
        return candidates(db, now, end)


def _text(value, limit):
    return feed.clean_label(value, limit)


def _watch_value(row):
    if row.get("conservative_probability") is not None:
        floor = {"worst_probability": row["conservative_probability"]}
    elif row.get("worst_net_edge") is not None:
        floor = {"worst_net_edge": row["worst_net_edge"]}
    elif row.get("worst_edge") is not None:
        # Older basketball watch snapshots already deducted the fee from this field.
        field = "worst_net_edge" if row["sport"] == "basketball" else "worst_edge"
        floor = {field: row["worst_edge"]}
    else:
        raise ValueError("missing conservative watch estimate")
    return portfolio.value_metrics(row["probability"], row["price"], row["fee_rate"], **floor)


def _rank(row):
    try:
        value = _watch_value(row)
        tip = store.timestamp(row["kickoff"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if value.tier is None or not feed.numeric_id(row.get("market_id")):
        return None
    return -value.conservative_roi, -value.expected_roi, tip, str(row["market_id"]), row["fixture_key"]


def pick(candidates, now, end, booked, already_notified=(), earlier_highlights=(), *, handoff_margin=timedelta(0)):
    # Keep the earlier highlights in the competition. Otherwise each scan would
    # merely send the next five rows of the same list.
    ranked = [(rank, row) for row in earlier_highlights if row.get("fixture_key") in already_notified
              and (rank := _rank(row)) is not None]
    for item in candidates:
        sport, key = item.get("sport"), item.get("fixture_key")
        quote, tip = item.get("quoted_at"), item.get("kickoff")
        if (sport not in shortlist.SPORT_LABELS or not key or (sport, key) in booked or key in already_notified
                or not all(isinstance(t, datetime) and t.utcoffset() is not None for t in (quote, tip))
                or not now + store.MIN_HANDOFF < tip <= end or not now - feed.BOOK_AGE <= quote <= now
                or not isinstance(item.get("valid_until"), datetime) or item["valid_until"].utcoffset() is None
                or item["valid_until"] <= now + handoff_margin
                or not all(_text(item.get(field), 120) for field in ("home", "away", "direction", "market_kind", "league"))
                or not feed.numeric_id(item.get("market_id")) or not feed.numeric_id(item.get("token_id"))
                or not isinstance(item.get("condition_id"), str) or not feed._CONDITION_ID.fullmatch(item["condition_id"])):
            continue
        p, edge = feed.number(item.get("probability")), feed.number(item.get("edge"))
        if p is None or edge is None or not 0 < p < 1 or edge <= 0:
            continue
        rank = _rank(item)
        if rank is not None:
            ranked.append((rank, item))
    chosen, seen = [], set()
    for _, item in sorted(ranked, key=lambda pair: pair[0]):
        if item["fixture_key"] not in seen:
            seen.add(item["fixture_key"])
            if item["fixture_key"] not in already_notified:
                chosen.append(item)
        if len(seen) == shortlist.LIST_LIMIT:
            break
    return chosen


def render(entries):
    if not entries:
        return None
    lines = [TITLE]
    first_pick = None
    clocks = []
    for number, item in enumerate(entries, 1):
        tip = item["kickoff"].astimezone(feed.BEIJING).strftime("%m月%d日 %H:%M")
        clocks.append(item["quoted_at"].astimezone(feed.BEIJING).replace(second=0, microsecond=0))
        value = _watch_value(item)
        if number == 1:
            first_pick = item["direction"], value.odds
        probability = portfolio._decimal(item["probability"])
        tier = shortlist.CONFIDENCE_LABELS[value.tier]
        home = _text(item.get("home_display") or item["home"], 65)
        away = _text(item.get("away_display") or item["away"], 65)
        kind = item.get("market_label") or item.get("market_kind_code") or item["market_kind"]
        if kind in {"match_winner", "total", "team_total", "spread", "btts"}:
            kind = shortlist.market_label(item["sport"], kind)
        lineup_note = ""
        if item["sport"] == "football":
            if item.get("lineup_pending"):
                lineup_note = "｜首发待公布"
            elif item.get("lineup_type") == "predicted":
                lineup_note = "｜预测首发，待官宣"
        lines.append(f"{number}. {tip}｜{_text(item['league'], 35)}\n{home} 对 {away}\n"
                     f"{_text(kind, 45)}｜{shortlist.short_direction(item['direction'], 90)}\n"
                     f"参考赔率 {value.odds:.2f}倍｜模型概率 {probability:.1%}｜保本胜率 {value.break_even_probability:.1%}\n"
                     f"预期净回报 {value.expected_roi:+.1%}｜保守 {value.conservative_roi:+.1%}｜价值档 {tier}｜模拟 ¥0.00{lineup_note}")
    earliest, latest = min(clocks), max(clocks)
    prices_at = earliest.strftime("%m月%d日 %H:%M")
    if latest != earliest:
        prices_at += " 至 " + latest.strftime("%H:%M" if latest.date() == earliest.date() else "%m月%d日 %H:%M")
    lines.append(f"以上仅供观察。取价 {prices_at}，十进制赔率已含手续费及本金。")
    return shortlist.single_weixin_message("\n".join(lines), first_pick=first_pick)


def daily_key(now):
    start = (now.astimezone(feed.BEIJING) - timedelta(hours=8)).replace(hour=8, minute=0, second=0, microsecond=0)
    return f"focus-{start:%Y%m%d}", start


def build(db, now, *, already_notified=(), earlier_highlights=(), handoff_margin=timedelta(0)):
    end = now + timedelta(hours=24)
    errors, rows = {}, []
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {sport: pool.submit(_source, sport, now, end) for sport in ("football", "basketball")}
        for sport, task in jobs.items():
            try:
                rows.extend(task.result())
            except Exception as exc:
                errors[sport] = type(exc).__name__
    current = store.db_now(db)
    booked = set()
    if portfolio._installed(db):
        booked = {(row["sport"], row["fixture_key"]) for row in db.execute("""
            SELECT sport,fixture_key FROM sports_portfolio_position
            WHERE restart_key=%s AND kickoff>%s AND kickoff<=%s
            """, (portfolio.RESTART_KEY, current, end))}
    from weixin_delivery import MAX_BODY_CHARS

    entries = []
    for row in pick(rows, current, end, booked, already_notified, earlier_highlights, handoff_margin=handoff_margin):
        value = _watch_value(row)
        entries.append(row | {"conservative_probability": float(value.conservative_probability),
                              "break_even_probability": float(value.break_even_probability),
                              "expected_roi": float(value.expected_roi), "conservative_roi": float(value.conservative_roi),
                              "value_tier": value.tier})
    body = render(entries)
    while body and len(body) > MAX_BODY_CHARS:
        entries.pop()
        body = render(entries)
    return {"window_start": now, "window_end": end, "as_of": current, "source_errors": errors,
            "entries": entries, "body": body, "booked_fixtures": len(booked)}


def publish(db, *, preview=False, correction_key=None, correction_until=None):
    import football_daily as daily
    from sports_runtime import task_lock

    correction = correction_key is not None
    if correction:
        if (preview or not isinstance(correction_key, str) or not daily.FOCUS_KEY.fullmatch(correction_key)
                or len(correction_key) > 96):
            raise ValueError("invalid correction request")
        correction_until = store.timestamp(correction_until)
    with task_lock(db, 29) as held:
        if not held:
            return {"state": "focus_scan_running"}
        now = store.db_now(db)
        key, start = daily_key(now)
        if correction:
            previous = db.execute(f"""
                SELECT j.key,j.state,j.attempt_key,{store.VERIFIED_NATIVE_NOT_INVOKED_SQL} AS never_sent
                FROM job j LEFT JOIN attempt a ON a.key=j.attempt_key
                WHERE j.stage='note' AND j.detail->>'correction_key'=%s ORDER BY j.starts_at DESC
                """, (correction_key,)).fetchall()
            for job in previous:
                if job["never_sent"]:
                    continue
                if job["attempt_key"] or job["state"] not in {"expired", "suppressed"}:
                    return {"state": job["state"], "job_key": job["key"], "existing": True}
            if now + store.MIN_HANDOFF >= correction_until:
                return {"state": "correction_window_closed"}
        already_notified, earlier_highlights = set(), []
        if not preview:
            history = db.execute("""
                SELECT key,state,starts_at,detail->'fixture_keys' AS fixtures,detail->'evidence' AS evidence FROM job
                WHERE stage='note' AND detail->>'kind'='verified_owner_focus'
                  AND starts_at >= %s AND state IN ('pending','leased','submitted','unknown','accepted')
                ORDER BY starts_at DESC
                """, (now - timedelta(hours=24),)).fetchall()
            for notice in history:
                if notice["state"] in {"pending", "leased", "submitted"}:
                    if correction:
                        return {"state": "focus_queue_busy"}
                    return {"state": notice["state"], "job_key": notice["key"], "existing": True}
                if correction:
                    continue
                ids = notice["fixtures"] or []
                already_notified.update(ids)
                if notice["starts_at"] >= start and isinstance(notice["evidence"], list):
                    earlier_highlights.extend(row for row in notice["evidence"] if isinstance(row, dict)
                                              and row.get("fixture_key") in ids and feed.number(row.get("edge")) is not None
                                              and feed.numeric_id(row.get("market_id")) and feed.stamp(row.get("kickoff")))
        summary = build(db, now, already_notified=already_notified, earlier_highlights=earlier_highlights,
                        handoff_margin=store.MIN_HANDOFF if correction else timedelta(0))
        entries = summary["entries"]
        if preview or not entries:
            return {"state": "preview" if preview else "no_focus", **summary}
        cutoff = min(min(item["kickoff"], item["quoted_at"] + REFERENCE_LIFE) for item in entries)
        if correction:
            # As with ordinary zero-stake notes, delivery is bounded by the
            # dated reference window. The queue rechecks live quotes at claim.
            cutoff = min(cutoff, correction_until)
            if min(cutoff, *(item["valid_until"] for item in entries)) <= store.db_now(db) + store.MIN_HANDOFF:
                return {"state": "waiting_for_fresh_quote"}
        fixtures = [item["fixture_key"] for item in entries]
        identity = [(item["fixture_key"], item["revision"], item["token_id"], item["quoted_at"].isoformat()) for item in entries]
        delivery_key = f"{key}-{feed.document_digest(identity)[:18]}"
        if correction:
            delivery_key = correction_key if not previous else f"{correction_key}-{len(previous) + 1}"
        evidence_fields = ("sport", "league", "fixture_key", "revision", "kickoff", "home", "away", "home_display",
                           "away_display", "market_kind", "market_label", "market_kind_code", "line",
                           "direction", "market_id", "token_id", "condition_id", "model_version", "model_digest",
                           "probability", "price", "fee_rate", "quoted_at", "edge", "worst_net_edge", "worst_edge",
                           "conservative_probability", "break_even_probability", "expected_roi", "conservative_roi", "value_tier",
                           "market_probability", "depth_near2c_usd", "valid_until", "reason", "market_url", "url",
                           "official_source", "official_id", "contract_digest", "condition_side", "independent_fixture_url",
                           "lineup_type", "lineup_pending", "lineup_digest", "lineup_observed_at", "market_terms_sha256")
        evidence = [{k: item[k] for k in evidence_fields if k in item} for item in entries]
        evidence = json.loads(json.dumps(evidence, default=str, allow_nan=False))
        queued = daily.enqueue_focus(db, delivery_key, cutoff.isoformat(), fixtures, summary["body"], evidence=evidence,
                                     correction_key=correction_key)
        return {**summary, **queued}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preview", "publish"))
    mode = parser.parse_args().mode
    with store.connect(read_only=mode == "preview") as connection:
        result = publish(connection, preview=mode == "preview")
    print(json.dumps(result, default=str, ensure_ascii=False))
