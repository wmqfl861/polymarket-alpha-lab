"""Rank and preview current football and basketball paper directions."""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

if __name__ == "__main__":
    sys.dont_write_bytecode = True

from psycopg import Error as DatabaseError

import football_feed as feed
import football_store as store
import sports_portfolio as portfolio


LIST_LIMIT = 5
SPORT_LABELS = {"football": "足球", "basketball": "篮球"}
CONFIDENCE_LABELS = {"low": "低", "medium": "中", "high": "高"}
STAGE_LABELS = {"t90": "T−90", "t30": "T−30"}
GATE_REJECTIONS = frozenset({
    "thresholds_unmet", "news_changed", "no_direction_passed",
    "pregame_basketball_availability_changed", "starter_injury_status_conflict",
    "nonpositive_conservative_roi",
})
SCHEDULE_CHANGES = frozenset({"fixture_changed", "cancelled", "postponed", "started", "at_or_after_kickoff"})
WINDOW_REASONS = frozenset({"opening_pending", "late_review", "signal_expired", "outside_review_window"})
EVIDENCE_GAPS = frozenset({
    "review_unavailable", "review_budget_exhausted", "invalid_paper_evidence", "model_unavailable",
    "independent_fixture_missing", "fotmob_unavailable", "independent_fixture_changed", "news_baseline_missing",
    "lineups_unconfirmed", "markets_unavailable", "books_unavailable", "direct_contract_unverified",
    "collection_unavailable", "assessment_unavailable", "legacy_evidence_incomplete",
    "event_unavailable", "independent_provider_unavailable", "model_history_partial",
    "basketball_availability_unavailable", "basketball_availability_baseline_missing",
})


@dataclass(frozen=True)
class PaperDirection:
    row: dict
    market_id: str
    outcome: str
    direction: str
    price: float
    model_probability: float
    net_edge: float
    worst_edge: float
    adjusted_edge: float
    observed_at: datetime
    valid_until: datetime
    signal: dict
    quote: portfolio.Quote


@dataclass(frozen=True)
class RoundSelection:
    entries: tuple[PaperDirection, ...]
    allocations: tuple[portfolio.Allocation, ...]
    quotes: tuple[portfolio.Quote, ...]
    reasons: dict
    available_cash_fen: int | None
    realized_pnl_fen: int | None
    incomplete: bool

    @property
    def rows(self):
        return [entry.row for entry in self.entries]

    @property
    def valid_until(self):
        return min((entry.valid_until for entry in self.entries), default=None)

    @property
    def body(self):
        return _body(self)


def _timestamp(value):
    try:
        return store.timestamp(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _fixture_kickoff(row):
    kickoff = _timestamp(row.get("kickoff"))
    info = feed.record(row.get("info"))
    if (kickoff is not None and _timestamp(info.get("kickoff")) == kickoff
            and row.get("fixture_state") == "scheduled" and info.get("state", "scheduled") == "scheduled"
            and feed.clean_label(info.get("home")) and feed.clean_label(info.get("away"))):
        return kickoff
    return None


def _fixed_owner(row):
    owner = row.get("legacy_id")
    if not owner or row["info"].get("sport", "football") != "football":
        return None
    from football_daily import historical_owner

    kickoff = _fixture_kickoff(row)
    return owner if kickoff is not None and historical_owner(row["info"], kickoff) == owner else None


def _paper_windows(row, *, legacy):
    stage = row.get("stage")
    kickoff = _fixture_kickoff(row)
    starts_at = _timestamp(row.get("starts_at"))
    deadline = _timestamp(row.get("deadline"))
    if kickoff is None or starts_at is None or deadline is None or stage not in feed._STAGE_WINDOWS:
        return ()
    lead, end_lead = feed._STAGE_WINDOWS[stage]
    opening = kickoff - timedelta(minutes=lead)
    if legacy and stage == "t30":
        end_lead = 0
    cutoff = min(kickoff, deadline, kickoff - timedelta(minutes=end_lead))
    if legacy:
        return ((max(opening, starts_at), cutoff),)
    regular = (max(opening, starts_at), min(cutoff, opening + feed._LATE))
    confirmed = feed.confirmed_review_opening(row["info"]) if stage == "t90" else None
    if confirmed is not None and max(confirmed, starts_at) < cutoff:
        return regular, (max(confirmed, starts_at), cutoff)
    return (regular,)


def _paper_window(row, *, legacy, at=None):
    windows = _paper_windows(row, legacy=legacy)
    if len(windows) > 1 and at is not None and at >= windows[1][0]:
        return windows[1]
    return windows[0] if windows else None


def _basketball_settlement_info(info, signal):
    from basketball_feed import EXPLICIT_OT

    official = feed.record(signal.get("independent_fixture"))
    saved = feed.record(info.get("basketball_official"))
    fields = ("provider", "id", "home_id", "away_id", "kickoff")
    event_id = feed.numeric_id(signal.get("event_id"))
    references = [ref.get("slug") for ref in feed.rows(info.get("events")) if ref.get("id") == event_id]
    if (signal.get("fixture_key") != info.get("id") or signal.get("settlement_scope") != EXPLICIT_OT
            or signal.get("direct_contract_verified") is not True or not store.is_hex(signal.get("contract_digest"))
            or not event_id or len(references) != 1 or references[0] != signal.get("event_slug")
            or not isinstance(references[0], str) or not feed._SLUG.fullmatch(references[0])
            or not isinstance(info.get("base_slug"), str) or not feed._SLUG.fullmatch(info["base_slug"])
            or signal.get("side") not in ("home", "away", "over", "under")
            or not all(official.get(field) and official[field] == saved.get(field) for field in fields)
            or _timestamp(official["kickoff"]) is None):
        return None
    return official


def _display_direction(row, signal, source_label):
    info = row["info"]
    if row.get("legacy_id") or info.get("sport", "football") != "football":
        return source_label
    kind, side, outcome = (signal.get(field) for field in ("market_kind", "side", "outcome"))
    if kind == "match_winner":
        home, away = _teams(info)
        if (side, outcome) == ("home", "Yes"):
            return f"{home}主胜"
        if (side, outcome) == ("away", "Yes"):
            return f"{away}客胜"
        if outcome == "No" and side in ("home_no", "away_no"):
            team, opponent = (home, away) if side == "home_no" else (away, home)
            label = f"{team}不胜（{opponent}胜或平）"
            return label if len(label) <= 64 else f"{team}不胜（对手胜或平）"
        if (side, outcome) == ("draw_no", "No"):
            return "分出胜负（任一队获胜）"
    if kind == "team_total":
        team_side, line = signal.get("team_side"), feed.number(signal.get("line"))
        if team_side in ("home", "away") and line is not None and (side, outcome) in (("over", "Over"), ("under", "Under")):
            home, away = _teams(info)
            team = home if team_side == "home" else away
            comparison = "大于" if side == "over" else "小于"
            return f"{team}进球{comparison} {line:g} 球"
    return source_label


def _lowest_variant(signal):
    if "variants" in signal and (not isinstance(signal["variants"], (list, tuple)) or not signal["variants"]):
        raise ValueError("invalid model variants")
    raw = list(signal.get("variants", ()))
    raw.extend(signal[key] for key in ("min_variant", "conservative_probability") if signal.get(key) is not None)
    try:
        variants = [Decimal(str(item)) for item in raw]
    except (InvalidOperation, ValueError) as error:
        raise ValueError("invalid model probability bound") from error
    if not all(item.is_finite() and 0 <= item <= 1 for item in variants):
        raise ValueError("invalid model probability bound")
    return min(variants, default=None)


def _signal_direction(row, signal, explicit_expiries, assessment, cutoff, now):
    market_id = feed.numeric_id(signal.get("market_id"))
    direction = feed.clean_label(signal.get("direction"), 64)
    outcome = feed.clean_label(signal.get("outcome"), 48)
    observed_at = _timestamp(signal.get("book_observed_at"))
    price = feed.number(signal.get("price"))
    model = feed.number(signal.get("model_probability"))
    fee = feed.number(signal.get("fee_rate"))
    token = feed.numeric_id(signal.get("token_id"))
    condition = signal.get("condition_id")
    net, worst, adjusted = (feed.number(signal.get(field)) for field in ("net_edge", "worst_edge", "adjusted_edge"))
    if (not market_id or not direction or not outcome or price is None or not 0 < price < 1
            or model is None or not 0 <= model <= 1
            or any(edge is None or not -1 <= edge <= 1 for edge in (net, worst, adjusted))
            or fee is None or not 0 <= fee <= 1 or not token
            or not isinstance(condition, str) or not feed._CONDITION_ID.fullmatch(condition)
            or observed_at is None or observed_at > assessment):
        return None, "invalid_paper_evidence"
    bounds = [_timestamp(value) for value in explicit_expiries if value is not None]
    if not bounds or any(value is None or value <= observed_at for value in bounds):
        return None, "invalid_paper_evidence"
    try:
        validity = min(cutoff, observed_at + feed.BOOK_AGE, *bounds)
    except OverflowError:
        return None, "invalid_paper_evidence"
    if validity <= now + store.MIN_HANDOFF:
        return None, "signal_expired"
    direction = _display_direction(row, signal, direction)
    info = row["info"]
    fixture = {name: info[name] for name in (
        "id", "sport", "league", "home", "away", "home_id", "away_id", "home_alias", "away_alias",
        "home_abbr", "away_abbr", "base_slug", "events", "game_id", "kickoff", "state",
        "fixture_id", "fotmob_league_id", "fotmob_home_id", "fotmob_away_id",
    ) if name in info}
    if row.get("legacy_id"):
        fixture.update(feed.record(signal.get("fixed_fixture")))
    if info.get("sport") == "basketball":
        official = _basketball_settlement_info(info, signal)
        if official is None:
            return None, "invalid_paper_evidence"
        fixture["basketball_official"] = official
    try:
        # Only precise model evidence can populate min_variant. Older signals
        # retain their rounded pre-fee edge for Quote.value to interpret.
        weakest = _lowest_variant(signal)
        if weakest is not None:
            signal = dict(signal, min_variant=str(weakest))
        quote = portfolio.Quote(
            sport=info.get("sport", "football"), fixture_key=row["fixture_key"], fixture_revision=row["revision"],
            market_id=market_id, outcome=outcome, model_probability=signal["model_probability"],
            price=signal["price"], fee_rate=signal["fee_rate"],
            observed_at=observed_at, valid_until=validity, kickoff=row["kickoff"], direction=direction,
            token_id=token, condition_id=condition, model_version=signal.get("model_version"),
            settlement_evidence={"fixture": fixture, "signal": signal},
        )
        if weakest is not None and weakest > quote.model_probability:
            return None, "invalid_paper_evidence"
        if quote.value.conservative_roi <= 0:
            return None, "nonpositive_conservative_roi"
    except (ValueError, TypeError, KeyError, OverflowError):
        return None, "invalid_paper_evidence"
    return PaperDirection(row, market_id, outcome, direction, price, model, net, worst, adjusted,
                          observed_at, validity, quote.settlement_evidence["signal"], quote), "qualified"


def _direction_rank(entry):
    value = entry.quote.value
    return (value.conservative_roi.copy_negate(), value.expected_roi.copy_negate(),
            -entry.worst_edge, -entry.adjusted_edge, entry.market_id)


def _preferred_direction(row, decision, now):
    kickoff = _fixture_kickoff(row)
    if kickoff is None:
        return None, "fixture_changed"
    if kickoff <= now:
        return None, "at_or_after_kickoff"
    reason = store.safe_reason(decision.get("reason_code"), "review_unavailable")
    if decision.get("status") != "paper":
        return None, reason
    legacy = decision.get("source") == "legacy"
    if (reason != "qualified" or decision.get("coverage_gap") is True or legacy != bool(row.get("legacy_id"))
            or legacy and (_fixed_owner(row) is None or decision.get("assessed_at") is None)):
        return None, "invalid_paper_evidence"
    info = row["info"]
    sport = info.get("sport", "football")
    if sport not in SPORT_LABELS or (legacy and sport != "football"):
        return None, "invalid_paper_evidence"
    if sport == "football" and not legacy and info.get("league") not in feed.MODELED_LEAGUES and not info.get("_paper_scopes"):
        return None, "unsupported_league"
    if decision.get("moved_kickoff") is not None:
        return None, "fixture_changed"
    window = _paper_window(row, legacy=legacy, at=now)
    if window is None:
        return None, "outside_review_window"
    opening, cutoff = window
    if now < opening:
        return None, "opening_pending"
    if cutoff <= now + store.MIN_HANDOFF:
        return None, "late_review"

    assessment = now
    if decision.get("assessed_at") is not None:
        assessment = _timestamp(decision["assessed_at"])
        if assessment is None or not opening <= assessment < cutoff or assessment > now:
            return None, "invalid_paper_evidence"
    primary = feed.record(decision.get("signal"))
    signals = feed.rows(decision.get("signals"))
    if primary and primary not in signals:
        signals.append(primary)

    eligible = []
    failures = set()
    confirmed_phase = (sport == "football" and not legacy and row["stage"] == "t90"
                       and opening >= kickoff - feed.LINEUP_BASELINE_LEAD)
    if confirmed_phase:
        retained = info.get(feed.CONFIRMED_BASELINE)
        retained_at = feed.published_lineup_time(info, retained)
        retained_digest = feed.confirmed_personnel_digest(retained)
    for signal in signals:
        if confirmed_phase:
            observed = _timestamp(signal.get("book_observed_at"))
            lineup_at = _timestamp(feed.record(signal.get("lineup_review")).get("current_at"))
            digest = signal.get("confirmed_personnel_sha256")
            if (signal.get("review_phase") != "confirmed_lineups" or observed is None or observed < opening
                    or not store.is_hex(digest) or lineup_at is None or not opening <= lineup_at <= assessment
                    or _timestamp(signal.get("lineup_window_opened_at")) != opening):
                failures.add("invalid_paper_evidence")
                continue
            if lineup_at + feed.BOOK_AGE <= now + store.MIN_HANDOFF:
                failures.add("signal_expired")
                continue
            if retained_at is None or (retained_at >= lineup_at and retained_digest != digest):
                failures.add("news_changed")
                continue
        if sport == "basketball":
            from sports_runtime import basketball_scope_permitted

            permitted = basketball_scope_permitted(info, signal)
        else:
            version = feed.MODEL_VERSION_SOCCER if legacy else signal.get("model_version")
            kind = signal.get("market_kind")
            permitted = bool(kind in ("btts", "match_winner", "team_total", "total")
                             and signal.get("outcome") in ("Yes", "No", "Over", "Under")
                             and feed.gates.suspension_policy(kind).eligible
                             and feed.scope_permitted(info, kind, signal.get("line"), signal.get("team_side"), version))
        if not permitted:
            failures.add("market_not_admitted")
            continue
        bounds = [signal.get("valid_until")]
        if signal == primary:
            bounds.append(decision.get("valid_until"))
        entry, failure = _signal_direction(row, signal, bounds, assessment, cutoff, now)
        if entry is not None:
            eligible.append(entry)
        else:
            failures.add(failure)
    if eligible:
        return min(eligible, key=_direction_rank), "qualified"
    if "nonpositive_conservative_roi" in failures:
        return None, "nonpositive_conservative_roi"
    if "market_not_admitted" in failures:
        return None, "market_not_admitted"
    if "news_changed" in failures:
        return None, "news_changed"
    return None, "signal_expired" if "signal_expired" in failures else "invalid_paper_evidence"


def _team_matches(left, right, side):
    for field in (f"fotmob_{side}_id", f"{side}_id"):
        left_id, right_id = feed.numeric_id(left.get(field)), feed.numeric_id(right.get(field))
        if left_id and right_id:
            return left_id == right_id
    left_names = [feed.clean_label(left.get(key)) for key in (side, f"{side}_alias")]
    right_names = [feed.clean_label(right.get(key)) for key in (side, f"{side}_alias")]
    return any(a and b and (a.casefold() == b.casefold() or feed.same_team(a, b))
               for a in left_names for b in right_names)


def _same_occurrence(left, right):
    left_info, right_info = left["info"], right["info"]
    if left_info.get("sport", "football") != right_info.get("sport", "football"):
        return False
    if left.get("fixture_key") and left["fixture_key"] == right.get("fixture_key"):
        return True
    if _timestamp(left["kickoff"]) != _timestamp(right["kickoff"]):
        return False
    left_id = feed.numeric_id(left.get("legacy_id") or left_info.get("fixture_id"))
    right_id = feed.numeric_id(right.get("legacy_id") or right_info.get("fixture_id"))
    if left_id and right_id:
        return left_id == right_id
    if not left_info.get("league") or left_info["league"] != right_info.get("league"):
        return False
    left_game = feed.numeric_id(left_info.get("game_id"))
    if left_game and left_game == feed.numeric_id(right_info.get("game_id")):
        return True
    return all(_team_matches(left_info, right_info, side) for side in ("home", "away"))


def _rank_round(rows, decisions, now):
    reasons, eligible = {}, []
    for row in rows:
        key = row["key"]
        entry, reasons[key] = _preferred_direction(row, feed.record(decisions.get(key)), now)
        if entry is not None:
            eligible.append(entry)
    eligible.sort(key=lambda entry: (*_direction_rank(entry), str(entry.row.get("fixture_key") or ""), entry.row["key"]))

    unique = []
    for entry in eligible:
        key = entry.row["key"]
        if any(_same_occurrence(entry.row, prior.row) for prior in unique):
            reasons[key] = "duplicate_fixture"
            continue
        unique.append(entry)
    return unique, reasons


def _beijing(value):
    if value is None:
        return None
    return value.astimezone(feed.BEIJING).strftime("%m月%d日 %H:%M")


def _teams(info, limit=46):
    labels = []
    for side in ("home", "away"):
        name = feed.clean_label(info.get(side), limit)
        alias = feed.clean_label(info.get(f"{side}_alias"), limit)
        labels.append(alias if any("\u3400" <= char <= "\u9fff" for char in alias) else name)
    return labels


def short_direction(direction, limit):
    label = feed.clean_label(direction, len(direction))
    if len(label) <= limit:
        return label
    # Keep the team identifier at the front and the betting condition at the
    # end when a long club name would push the odds out of the notification.
    head = (limit - 1) // 2
    tail = limit - head - 1
    return label[:head].rstrip() + "…" + label[-tail:].lstrip()


def single_weixin_message(body, *, first_pick=None):
    """Keep the heading and all its details in one Hermes Weixin message."""
    lines = body.splitlines()
    if not lines:
        return ""
    result = [lines[0].strip()]
    for line in lines[1:]:
        # Hermes keeps indented continuations in one bubble. Its formatter wraps
        # at 120 characters, so reserve the two spaces on every physical line.
        for part in textwrap.wrap(line.strip(), width=118, break_on_hyphens=False):
            result.append("  " + part)
    if first_pick is not None:
        from weixin_delivery import MAX_BODY_CHARS

        direction, odds = first_pick
        prefix, suffix = result[0] + " · ", f" {odds:.2f}倍"
        remaining = MAX_BODY_CHARS - sum(len(line) + 1 for line in result[1:])
        label_limit = max(12, min(100, remaining) - len(prefix) - len(suffix))
        result[0] = prefix + short_direction(direction, label_limit) + suffix
    return "\n".join(result)


def _body(selection):
    if not selection.entries:
        return None
    lines = ["足篮正式模拟单｜北京时间"]
    first_pick = None
    for number, (entry, allocation) in enumerate(zip(selection.entries, selection.allocations), 1):
        row = entry.row
        info = row["info"]
        home, away = _teams(info, 28)
        sport = SPORT_LABELS[entry.quote.sport]
        league = feed.clean_label(info.get("league_name") or info.get("league"), 18)
        kickoff = _beijing(_timestamp(row["kickoff"]))
        stake = portfolio.format_cny(allocation.stake_fen)
        value = entry.quote.value
        if number == 1:
            first_pick = entry.direction, value.odds
        tier = CONFIDENCE_LABELS[value.tier]
        kind = market_label(entry.quote.sport, entry.signal.get("market_kind"))
        lines.append(f"{number}. {league}（{sport}）｜{kickoff}｜{home} 对 {away}\n"
                     f"{kind} · {entry.direction}\n"
                     f"模型概率 {entry.quote.model_probability:.1%}｜含费赔率 {value.odds:.2f}倍｜保本胜率 {value.break_even_probability:.2%}\n"
                     f"净回报 预期 {value.expected_roi:+.1%}｜保守 {value.conservative_roi:+.1%}（价值{tier}档）｜模拟下注 {stake}")
    pnl = portfolio.format_cny(selection.realized_pnl_fen)
    balance = portfolio.format_cny(selection.available_cash_fen)
    until = selection.valid_until.astimezone(feed.BEIJING).strftime("%m/%d %H:%M:%S")
    lines.append(f"本单有效至 {until}\n累计已实现盈亏 {pnl}｜本轮入账后可用余额 {balance}")
    return single_weixin_message("\n".join(lines), first_pick=first_pick)


def market_label(sport, kind):
    if sport == "basketball":
        return {"match_winner": "全场胜负（含加时）", "total": "全场大小分（含加时）",
                "spread": "让分胜负（含加时）"}.get(kind, "篮球盘口")
    return {"match_winner": "胜平负", "total": "全场进球大小", "team_total": "球队进球大小",
            "btts": "双方进球"}.get(kind, "足球盘口")


def _partial_coverage(decision):
    return any(decision.get(flag) is True for flag in ("partial_books", "partial_coverage"))


def _has_evidence_gap(decision, reason):
    return reason in EVIDENCE_GAPS or decision.get("coverage_gap") is True or _partial_coverage(decision)


def select_round(connection, rows, decisions, now):
    """Build a read-only shortlist funded by currently available simulated cash."""
    now = store.timestamp(now)
    ranked, reasons = _rank_round(rows, decisions, now)
    account = portfolio.status(connection)
    quotes = tuple(entry.quote for entry in ranked)
    allocations = tuple(portfolio.plan(connection, quotes, now=now, limit=LIST_LIMIT))
    by_identity = {(entry.quote.sport, entry.quote.fixture_key, entry.market_id): entry for entry in ranked}
    entries = tuple(by_identity[item.quote.sport, item.quote.fixture_key, item.quote.market_id] for item in allocations)
    kept = {entry.row["key"] for entry in entries}
    cutoff_reached = False
    for entry in ranked:
        key = entry.row["key"]
        if key in kept:
            cutoff_reached = len(entries) == LIST_LIMIT and key == entries[-1].row["key"]
        else:
            reasons[key] = ("below_top_five" if cutoff_reached else
                            "stake_unavailable" if account["state"] == "active" else "portfolio_not_initialized")
    incomplete = any(_has_evidence_gap(feed.record(decisions.get(key)), reason)
                     for key, reason in reasons.items())
    cash = account.get("available_cash_fen")
    if cash is not None:
        cash -= sum(item.stake_fen for item in allocations)
    return RoundSelection(entries, allocations, quotes, reasons, cash, account.get("realized_pnl_fen"), incomplete)


def _snapshot(connection, hours):
    from basketball_college import VERSION as college_version
    from sports_runtime import basketball_scan_state

    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        now = store.db_now(connection)
        end = now + timedelta(hours=hours)
        scan = {"football": store.scan_state(connection), "basketball": basketball_scan_state(connection)}
        fixtures = connection.execute(
            """
            SELECT id AS fixture_key, revision, kickoff, fixture_state, info, watch, highlight,
                   legacy_id, last_seen, missing_scans
            FROM fixture WHERE kickoff > %s AND kickoff < %s
            ORDER BY kickoff, id
            """, (now, end),
        ).fetchall()
        installed = connection.execute("SELECT to_regclass('football_notify.college_basketball_forecast') AS ready").fetchone()
        if installed["ready"]:
            records = connection.execute("""
                SELECT fixture_key,revision,kickoff,league,model_version,observed_at,home_probability
                  FROM college_basketball_forecast
                 WHERE model_version=%s AND kickoff>%s AND kickoff<%s AND observed_at<=%s
                """, (college_version, now, end, now)).fetchall()
            college = {(r["fixture_key"], r["revision"], r["kickoff"], r["league"]): r for r in records}
            for fixture in fixtures:
                key = (fixture["fixture_key"], fixture["revision"], fixture["kickoff"], fixture["info"].get("league"))
                if key in college and fixture["info"].get("sport") == "basketball":
                    fixture["college_research"] = college[key]
        jobs = connection.execute(
            """
            SELECT j.key, j.fixture_key, j.stage, j.starts_at, j.deadline, j.state, j.reason, j.accepted_at,
                   j.next_try, j.detail->'pregame_review' AS latest_review
            FROM job j JOIN fixture f ON j.fixture_key = f.id AND j.revision = f.revision
            WHERE f.kickoff > %s AND f.kickoff < %s AND j.stage IN ('t90', 't30')
            """, (now, end),
        ).fetchall()
        legacy_acceptances = connection.execute(
            """
            SELECT a.fixture_id, a.lead, a.confirmed_at
            FROM legacy_acceptance a JOIN fixture f ON a.fixture_id = f.legacy_id
            WHERE f.kickoff > %s AND f.kickoff < %s
            """, (now, end),
        ).fetchall()
    return now, end, scan, fixtures, jobs, legacy_acceptances


def _iso_beijing(value):
    return value.astimezone(feed.BEIJING).isoformat(timespec="seconds") if value is not None else None


def _scan_report(scan, now, end):
    scan = feed.record(scan)
    summary = feed.record(scan.get("summary"))
    scan_start = _timestamp(summary.get("window_start"))
    scan_end = _timestamp(summary.get("window_end"))
    last_finished = _timestamp(scan.get("finished_at"))
    return {
        "result": scan.get("result", "missing"),
        "last_scan_finished_beijing": _iso_beijing(last_finished),
        "last_complete_scan_beijing": _iso_beijing(_timestamp(scan.get("last_complete_at"))),
        "scan_window_start_beijing": _iso_beijing(scan_start),
        "scan_window_end_beijing": _iso_beijing(scan_end),
        "schedule_freshness_cutoff_beijing": _iso_beijing(now - store.FIXTURE_FRESHNESS),
        "horizon_within_last_scan": scan_start is not None and scan_end is not None and scan_start <= now < end <= scan_end,
        "partial": scan.get("result") != "complete" or summary.get("partial") is True,
        "rejected_schedule_observations": summary.get("rejected"),
    }


def _fresh_fixture(row, now):
    last_seen = _timestamp(row.get("last_seen"))
    return last_seen is not None and now - store.FIXTURE_FRESHNESS <= last_seen <= now and row["missing_scans"] < 2


def _model_status(info, legacy, now):
    if legacy:
        return "fixed_source"
    if info.get("sport") == "basketball":
        if info.get("_paper_scopes"):
            return "admitted_model"
        return "shadow_model" if info.get("watch") is True else "unsupported"
    if info.get("league") not in feed.MODELED_LEAGUES:
        models = feed._paper_projections(info, now)
        if not models:
            return "unsupported"
        versions = {model.model_version for model in models}
        return ("admitted_research" if any(scope[4] in versions for scope in info.get("_paper_scopes", ()))
                else "shadow_research")
    model = feed.record(info.get("model"))
    if not model:
        return "missing"
    observed = _timestamp(model.get("observed_at"))
    from football_model import MODEL_AGE

    if observed is None or not timedelta(0) <= now - observed <= MODEL_AGE:
        return "stale"
    return "current" if feed._model_projection(info, now) is not None else "invalid"


def _scheduled_windows(fixture, jobs, legacy_acceptances):
    from football_daily import stage_windows

    kickoff, legacy_id = fixture["kickoff"], fixture["legacy_id"]
    windows = {stage: (opening, deadline) for stage, opening, deadline in stage_windows(kickoff, fixture["watch"], legacy_id)
               if stage in STAGE_LABELS}
    if legacy_id:
        windows["t90"] = kickoff - timedelta(minutes=90), kickoff - timedelta(minutes=30)
        windows["t30"] = kickoff - timedelta(minutes=30), kickoff
    for stage, (opening, deadline) in windows.items():
        job = jobs.get((fixture["fixture_key"], stage))
        row = {**fixture, "stage": stage, "starts_at": opening, "deadline": deadline,
               "key": f"preview:{fixture['fixture_key']}:{fixture['revision']}:{stage}"}
        if job:
            row.update({field: job[field] for field in ("key", "starts_at", "deadline")})
        accepted = job.get("accepted_at") if job else None
        if accepted is None and legacy_id:
            accepted = legacy_acceptances.get((legacy_id, 90 if stage == "t90" else 30))
        for index, (start, cutoff) in enumerate(_paper_windows(row, legacy=bool(legacy_id))):
            yield row, start, cutoff, {
                "stage": "首发公布后" if index else STAGE_LABELS[stage],
                "opening_beijing": _iso_beijing(start),
                "cutoff_beijing": _iso_beijing(cutoff),
                "recorded_job_state": job["state"] if job else None,
                "recorded_job_reason": store.safe_reason(job.get("reason"), None) if job else None,
                "next_automatic_review_beijing": _iso_beijing(job["next_try"])
                if job and job["state"] == "pending" and start <= job["next_try"] < cutoff - store.MIN_HANDOFF else None,
                "previous_review_coverage": feed.record(job.get("latest_review")).get("coverage") if job else None,
                "platform_accepted_beijing": _iso_beijing(_timestamp(accepted)),
            }


def _head_row(fixture):
    info = fixture["info"]
    home, away = _teams(info)
    return {
        "sport": info.get("sport", "football"),
        "home": home,
        "away": away,
        "league": feed.clean_label(info.get("league_name") or info.get("league")),
        "kickoff_beijing": _iso_beijing(fixture["kickoff"]),
        "fixture_state": fixture["fixture_state"],
    }


def _report_fixture(fixture, jobs, legacy_acceptances, now):
    report = _head_row(fixture)
    fixed_owner = _fixed_owner(fixture)
    scan_fresh = _fresh_fixture(fixture, now)
    model = _model_status(fixture["info"], fixed_owner, now)
    if study := fixture.get("college_research"):
        if model == "unsupported":
            model = "shadow_model"
        report["research_forecast"] = {"model_version": study["model_version"],
                                       "observed_at_beijing": _iso_beijing(study["observed_at"]),
                                       "market_kind": "match_winner", "home_probability": study["home_probability"],
                                       "away_probability": 1 - study["home_probability"]}
    report.update(model_state=model, schedule_scan_fresh=scan_fresh, fixed_pin_verified=fixed_owner is not None)
    if _fixture_kickoff(fixture) is None:
        return None, dict(report, category="schedule_changed")
    if fixture["legacy_id"] and not fixed_owner:
        return None, dict(report, category="schedule_changed", reason="invalid_fixed_pin")
    if not scan_fresh and not fixed_owner:
        return None, dict(report, category="schedule_stale")
    if model in {"shadow_model", "shadow_research"}:
        return None, dict(report, category="research_only")
    if model == "unsupported":
        return None, dict(report, category="unmodeled")
    windows = list(_scheduled_windows(fixture, jobs, legacy_acceptances))
    available = [(row, details) for row, start, cutoff, details in windows
                 if start <= now < cutoff - store.MIN_HANDOFF and not details["platform_accepted_beijing"]
                 and details["recorded_job_state"] in {None, "pending", "leased"}]
    next_window = next((details for _, start, cutoff, details in windows
                        if now < start and start < cutoff - store.MIN_HANDOFF), None)
    report["next_review"] = next_window
    if not available:
        return None, dict(report, category="outside_review_window")
    row, details = available[0]
    return row, dict(report, category="review_pending", current_review=details)


def _recheck_snapshot(connection, rows):
    current = connection.execute(
        """
        SELECT f.id AS fixture_key, f.revision, f.kickoff, f.fixture_state, f.last_seen, f.missing_scans,
               f.legacy_id, f.info, j.state AS job_state
        FROM fixture f LEFT JOIN job j ON j.fixture_key = f.id AND j.key = ANY(%s)
        WHERE f.id = ANY(%s)
        """, ([row["key"] for row in rows], [row["fixture_key"] for row in rows]),
    ).fetchall()
    return {row["fixture_key"]: row for row in current}


def _review_report(result, reason):
    raw_reason = store.safe_reason(result.get("reason_code"), "review_unavailable")
    partial = _partial_coverage(result)
    if reason in {"qualified", "below_top_five", "duplicate_fixture", "fixture_already_booked", "stake_unavailable",
                  "portfolio_not_initialized", "window_already_processed", "research_only"}:
        category = reason
    elif reason in SCHEDULE_CHANGES:
        category = "schedule_changed"
    elif reason in WINDOW_REASONS:
        category = "outside_review_window"
    elif reason == "schedule_stale":
        category = reason
    elif reason in GATE_REJECTIONS and not _has_evidence_gap(result, reason):
        category = "gate_rejected"
    else:
        category = "partial_evidence" if partial else "missing_evidence"
    return {
        "category": category,
        "reason": reason,
        "source_reason": raw_reason,
        "coverage_gap": result.get("coverage_gap") is True,
        "partial_coverage": partial,
        "screened_candidates": result["candidates"] if type(result.get("candidates")) is int else None,
        "revised_kickoff_beijing": _iso_beijing(_timestamp(result.get("moved_kickoff"))),
        "source_reasons": [code for code in result.get("reason_codes", []) if store.safe_reason(code, None)]
                          if isinstance(result.get("reason_codes"), list) else [],
    }


def _ranked_report(entry, allocation, rank):
    value = entry.quote.value
    return {
        **_head_row(entry.row),
        "rank": rank,
        "stage": "首发公布后" if entry.signal.get("review_phase") == "confirmed_lineups" else STAGE_LABELS[entry.row["stage"]],
        "direction": entry.direction,
        "market_id": entry.market_id,
        "outcome": entry.outcome,
        "price": entry.price,
        "decimal_odds_including_fee": float(value.odds),
        "model_probability": entry.model_probability,
        "break_even_probability": float(value.break_even_probability),
        "expected_roi": float(value.expected_roi),
        "conservative_probability": float(value.conservative_probability),
        "conservative_probability_basis": "model_bound" if entry.signal.get("min_variant") else "rounded_worst_edge",
        "conservative_roi": float(value.conservative_roi),
        "value_tier": value.tier,
        "value_tier_label": CONFIDENCE_LABELS[value.tier],
        "confidence": allocation.confidence,
        "confidence_label": CONFIDENCE_LABELS[allocation.confidence],
        "stake_fen": allocation.stake_fen,
        "fee_fen": allocation.fee_fen,
        "stake_cny": portfolio.format_cny(allocation.stake_fen),
        "net_edge": entry.net_edge,
        "worst_edge": entry.worst_edge,
        "adjusted_edge": entry.adjusted_edge,
        "book_observed_beijing": _iso_beijing(entry.observed_at),
        "valid_until_beijing": _iso_beijing(entry.valid_until),
    }


def preview(connection, hours=24):
    """Use a read-only schedule snapshot and the current public prematch evidence."""
    from football_daily import pregame_decisions, apply_paper_scopes

    now, end, scan, fixtures, job_rows, acceptance_rows = _snapshot(connection, hours)
    apply_paper_scopes(connection, fixtures)
    jobs = {(row["fixture_key"], row["stage"]): row for row in job_rows}
    legacy_acceptances = {(row["fixture_id"], row["lead"]): row["confirmed_at"] for row in acceptance_rows}
    rows, reports = [], []
    reviews = {}
    for fixture in fixtures:
        row, report = _report_fixture(fixture, jobs, legacy_acceptances, now)
        reports.append(report)
        if row is not None:
            rows.append(row)
            reviews[row["key"]] = report

    decisions = pregame_decisions(None, rows, preview=True, now=now) if rows else {}
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        evaluated_at = store.db_now(connection)
        current_by_key = _recheck_snapshot(connection, rows) if rows else {}
        usable = []
        reasons = {}
        for row in rows:
            current = current_by_key.get(row["fixture_key"])
            if (not current or current["fixture_state"] != "scheduled" or current["kickoff"] != row["kickoff"]
                    or current["revision"] != row["revision"] or _fixed_owner(current) != row.get("legacy_id")):
                reasons[row["key"]] = "fixture_changed"
            elif not row.get("legacy_id") and not _fresh_fixture(current, evaluated_at):
                reasons[row["key"]] = "schedule_stale"
            elif current["job_state"] not in {None, "pending", "leased"}:
                reasons[row["key"]] = "window_already_processed"
            else:
                usable.append(dict(row, info=current["info"]))
        apply_paper_scopes(connection, usable)
        selection = select_round(connection, usable, decisions, evaluated_at)
        reasons.update(selection.reasons)
        for key, report in reviews.items():
            report.update(_review_report(feed.record(decisions.get(key)), reasons[key]))
        account = portfolio.status(connection)
    next_opening = min((report["next_review"]["opening_beijing"] for report in reports if report.get("next_review")),
                       default=None)
    return {
        "state": "preview",
        "window_start_beijing": _iso_beijing(now),
        "window_end_beijing": _iso_beijing(end),
        "evaluated_at_beijing": _iso_beijing(evaluated_at),
        "next_review_opening_beijing": next_opening,
        "scan": _scan_report(scan["football"], now, end),
        "basketball_scan": _scan_report(scan["basketball"], now, end),
        "portfolio": {name: value for name, value in account.items() if name != "started_at"},
        "available_cash_after_round_fen": selection.available_cash_fen,
        "counts": {
            "fixtures_in_schedule": len(fixtures),
            "decision_windows_reviewed": len(rows),
            "categories": dict(sorted(Counter(report["category"] for report in reports).items())),
            "model_states": dict(sorted(Counter(report["model_state"] for report in reports).items())),
            "reviews_with_partial_coverage": sum(report.get("partial_coverage") is True for report in reports),
        },
        "top_five": [_ranked_report(entry, allocation, rank)
                     for rank, (entry, allocation) in enumerate(zip(selection.entries, selection.allocations), 1)],
        "message_valid_until_beijing": _iso_beijing(selection.valid_until),
        "message": selection.body,
        "fixtures": reports,
    }


def main():
    parser = argparse.ArgumentParser(description="Preview the current football and basketball simulation shortlist")
    parser.add_argument("--preview", action="store_true", required=True)
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    if not 1 <= args.hours <= 72:
        parser.error("--hours must be between 1 and 72")
    try:
        with store.connect(read_only=True) as connection:
            result = preview(connection, args.hours)
    except (DatabaseError, store.StoreError):
        print(json.dumps({"state": "error", "reason": "notification_schedule_unavailable"}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
