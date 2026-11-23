"""Compare captured pre-kickoff FotMob evidence for the fixed November 22 fixtures."""

import argparse
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path


DATE = "2026-11-22"
CONFIRMED_LINEUPS = ("standard", "confirmed")
BASELINE_LINEUPS = (*CONFIRMED_LINEUPS, "predicted")
ABSENCE_TYPES = ("injury", "suspension", "illness", "internationalDuty")
FORMATION = re.compile(r"[1-9](?:-[1-9]){2,4}")
NEWS_FINDINGS = {
    "morning_lineup_unverified": "上午对应首发资料不全",
    "window_lineup_unconfirmed": "临场首发尚未确认",
    "lineup_positions_unverified": "阵型或站位资料不全",
    "lineup_identity_conflict": "双方首发名单的球员标识互相冲突",
    "lineup_changed": "临场首发人选、阵型或站位与上午不同",
    "availability_unverified": "缺阵资料不全或互相矛盾",
    "availability_changed": "临场缺阵记录与上午不同",
}

# The known market IDs are unique. Two separately listed win contracts are not complements.
FIXTURES = {
    "5781804": ("13:30", "Ajax", "ADO Den Haag", (
        ("6896072", "Yes", "yes", "Both teams score"),
    )),
    "5795521": ("16:30", "Liverpool", "Manchester United", (
        ("6893498", "Over", "over", "Manchester United scores at least one"),
        ("6893502", "Over", "over", "Manchester United scores at least two"),
    )),
    "5802993": ("16:15", "Lorient", "Rennes", (
        ("6891504", "Yes", "home", "Lorient wins"),
        ("6891506", "No", "away_no", "Rennes does not win (Lorient or draw)"),
        ("6893624", "Over", "over", "Match total over 1.5"),
        ("6893628", "Over", "over", "Match total over 2.5"),
        ("6893632", "Over", "over", "Match total over 3.5"),
        ("6893636", "Over", "over", "Match total over 4.5"),
        ("6893656", "Yes", "yes", "Both teams score"),
        ("6893685", "Over", "over", "Lorient scores at least one"),
        ("6893688", "Over", "over", "Lorient scores at least two"),
    )),
    "5802994": ("19:45", "Marseille", "Le Mans", (
        ("6891712", "No", "home_no", "Marseille does not win (Le Mans or draw)"),
        ("6891716", "Yes", "away", "Le Mans wins"),
    )),
    "5868133": ("13:00", "Barcelona", "Villarreal", (
        ("6898398", "Over", "over", "Villarreal scores at least one"),
        ("6898399", "Over", "over", "Villarreal scores at least two"),
    )),
}


class Unavailable(Exception):
    """Fixed code for an unavailable or unverified input."""


def time_at(value):
    if not isinstance(value, str):
        raise ValueError("timestamp")
    at = datetime.fromisoformat(value)
    if at.utcoffset() is None:
        raise ValueError("timestamp")
    return at.astimezone(UTC)


def record(value):
    return value if isinstance(value, dict) else {}


def read_json(path, limit=512_000):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("input size")
    return json.loads(raw)


def verify_market(row, direction, fixture_id, kickoff, home, away):
    market, outcome, side, _ = direction
    official = record(row.get("_official"))
    try:
        return (str(row.get("市场ID")) == str(row.get("_market")) == market
                and row.get("选项") == outcome and row.get("_side") == side
                and row.get("_numeric_ok") is True and row.get("_allowed") is True
                and str(official.get("fixture_id")) == fixture_id
                and (official.get("home"), official.get("away")) == (home, away)
                and time_at(official.get("start")) == time_at(row.get("开赛UTC")) == kickoff)
    except ValueError:
        return False


def fixture_snapshot(directory, manifest, fixture_id, kickoff, home, away):
    capture = record(record(read_json(directory / manifest)).get(fixture_id))
    metadata = record(capture.get("match"))
    at = time_at(capture.get("fetched_utc"))
    raw = record(read_json(directory / f"fotmob-prematch-{fixture_id}.json"))
    general = record(raw.get("general"))
    status = record(record(raw.get("header")).get("status"))
    if (capture.get("error") or str(metadata.get("fixture_id")) != fixture_id
            or (metadata.get("home"), metadata.get("away")) != (home, away)
            or str(general.get("matchId")) != fixture_id
            or (record(general.get("homeTeam")).get("name"),
                record(general.get("awayTeam")).get("name")) != (home, away)
            or time_at(metadata.get("start")) != kickoff
            or time_at(general.get("matchTimeUTCDate")) != kickoff
            or time_at(status.get("utcTime")) != kickoff
            or raw.get("ongoing") is not False
            or general.get("started") is not False or general.get("finished") is not False
            or any(status.get(flag) is not False for flag in ("started", "finished", "cancelled"))):
        raise ValueError("fixture identity")
    lineup = record(record(raw.get("content")).get("lineup"))
    if (str(lineup.get("matchId")) != fixture_id
            or (record(lineup.get("homeTeam")).get("name"),
                record(lineup.get("awayTeam")).get("name")) != (home, away)):
        lineup = {}
    return at, lineup


def public_name(value):
    return (isinstance(value, str) and 0 < len(value) <= 65
            and all(c.isalpha() or c in " '-’‐." for c in value))


def starting_eleven(team):
    players = team.get("starters")
    if not isinstance(players, list) or len(players) != 11:
        return {}
    eleven, slots = {}, set()
    for player in players:
        player = record(player)
        player_id, name, slot = (player.get(key) for key in ("id", "name", "positionId"))
        if (type(player_id) is not int or player_id <= 0 or player_id in eleven
                or not public_name(name) or type(slot) is not int or slot <= 0 or slot in slots):
            return {}
        eleven[player_id] = (name, slot)
        slots.add(slot)
    return eleven


def formation(team):
    shape = team.get("formation")
    if isinstance(shape, str) and FORMATION.fullmatch(shape) and sum(map(int, shape.split("-"))) == 10:
        return shape
    return None


def absences(team, observed):
    players = team.get("unavailable")
    if not isinstance(players, list):
        return None
    listed = {}
    for player in players:
        player = record(player)
        player_id, name = player.get("id"), player.get("name")
        status = record(player.get("unavailability"))
        kind, updated = status.get("type"), status.get("lastUpdated")
        reason, expected, expected_date = (status.get(key) for key in ("injuryId", "expectedReturn", "expectedReturnDate"))
        if (type(player_id) is not int or player_id <= 0 or player_id in listed
                or not public_name(name) or kind not in ABSENCE_TYPES
                or (reason is not None and (type(reason) is not int or reason <= 0))
                or (expected is not None and (not isinstance(expected, str) or not 0 < len(expected) <= 80))):
            return None
        try:
            if updated is not None and time_at(updated) > observed:
                return None
            return_at = time_at(expected_date) if expected_date is not None else None
        except (ValueError, OverflowError):
            return None
        listed[player_id] = (name, kind, reason, expected, return_at)
    return listed


def lineup_source(url, observed_at, team, label, eleven, shape, unavailable):
    findings = []
    if eleven:
        starters = ", ".join(name for name, _ in sorted(eleven.values(), key=lambda player: player[1]))
        findings.append(f"{label}（{shape or '阵型未明'}）为 {starters}")
    if unavailable is not None:
        names = ", ".join(sorted(player[0] for player in unavailable.values()))
        findings.append(f"缺阵记录为 {names}" if names else "缺阵记录为空")
    if findings:
        return {"url": url, "observed_at": observed_at.isoformat(),
                "finding": f"{team}：{'；'.join(findings)}。"}


def fallback(direction, reason):
    market, outcome, _, _ = direction
    return {"market_id": market, "outcome": outcome, "delta": "unknown",
            "basis": "可核实资料不足，无法确认临场变化。", "sources": [], "blockers": [reason]}


def compare_lineups(fixture_id, baseline, current, teams):
    url = f"https://www.fotmob.com/api/data/matchDetails?matchId={fixture_id}"
    morning_at, morning = baseline
    current_at, late = current
    sources, checks = [], set()
    starters_seen = {morning_at: set(), current_at: set()}
    absences_seen = {morning_at: set(), current_at: set()}
    for side, team_name in zip(("homeTeam", "awayTeam"), teams):
        morning_team, current_team = record(morning.get(side)), record(late.get(side))
        morning_xi = starting_eleven(morning_team) if morning.get("lineupType") in BASELINE_LINEUPS else {}
        current_xi = starting_eleven(current_team) if late.get("lineupType") in CONFIRMED_LINEUPS else {}
        morning_shape, current_shape = formation(morning_team), formation(current_team)
        morning_absences = absences(morning_team, morning_at)
        current_absences = absences(current_team, current_at)
        morning_label = "上午预计首发" if morning.get("lineupType") == "predicted" else "上午确认首发"
        for at, label, eleven, shape, unavailable in (
            (morning_at, morning_label, morning_xi, morning_shape, morning_absences),
            (current_at, "临场确认首发", current_xi, current_shape, current_absences),
        ):
            seen, absent = starters_seen[at], absences_seen[at]
            if seen.intersection(eleven):
                checks.add("lineup_identity_conflict")
            seen.update(eleven)
            if unavailable is not None:
                if absent.intersection(unavailable):
                    checks.add("availability_unverified")
                absent.update(unavailable)
            if seen & absent:
                checks.add("availability_unverified")
            source = lineup_source(url, at, team_name, label, eleven, shape, unavailable)
            if source:
                sources.append(source)

        if not morning_xi:
            checks.add("morning_lineup_unverified")
        if not current_xi:
            checks.add("window_lineup_unconfirmed")
        if morning_xi and current_xi:
            if not morning_shape or not current_shape:
                checks.add("lineup_positions_unverified")
            elif morning_xi != current_xi or morning_shape != current_shape:
                checks.add("lineup_changed")
        if morning_absences is None or current_absences is None:
            checks.add("availability_unverified")
        elif morning_absences != current_absences:
            checks.add("availability_changed")

    # Matching lineups and reported availability confirm the earlier expectation.
    # The feed does not establish how a change affects a particular market outcome.
    blockers = [key for key in NEWS_FINDINGS if key in checks]
    if blockers:
        details = "，".join(NEWS_FINDINGS[key] for key in blockers)
        return blockers, f"{details}，无法确认对该方向的临场影响。", sources
    return [], "双方临场确认的首发、站位和缺阵记录与上午资料一致。", sources


def run_window(fixture_id, lead):
    clock, home, away, directions = FIXTURES[fixture_id]
    kickoff = time_at(f"{DATE}T{clock}:00Z")
    window = kickoff - timedelta(minutes=lead)
    result = {"fixture_id": fixture_id, "lead": lead, "verdicts": []}
    failure = None
    eligible = []
    try:
        root = Path(os.environ.get("PAL_LEGACY_WINDOW_DIR", ""))
        morning = Path(os.environ.get("PAL_LEGACY_BASELINE_DIR", ""))
        if not root.is_absolute() or not morning.is_absolute():
            raise Unavailable("legacy_input_path_unconfigured")
        directory = root / f"{fixture_id}-{lead}"
        summary = record(read_json(directory / "model-assessment.json")).get("numeric_pass")
        if not isinstance(summary, list):
            raise Unavailable("market_assessment_invalid")
        for direction in directions:
            matched = [row for row in summary if any(str(record(row).get(field)) == direction[0]
                                                     for field in ("_market", "市场ID"))]
            if len(matched) == 1 and verify_market(matched[0], direction, fixture_id, kickoff, home, away):
                eligible.append(direction)
        if not eligible:
            raise Unavailable("market_assessment_invalid")
        try:
            current = fixture_snapshot(directory, "prematch-source.json", fixture_id, kickoff, home, away)
        except (OSError, ValueError, UnicodeError) as exc:
            raise Unavailable("window_fotmob_unavailable") from exc
        now = datetime.now(UTC)
        if now >= kickoff or not window <= current[0] <= min(window + timedelta(minutes=5), now):
            raise Unavailable("window_fotmob_time_invalid")
        filename = "prematch-additional.json" if fixture_id == "5781804" else "prematch-source.json"
        try:
            baseline = fixture_snapshot(morning, filename, fixture_id, kickoff, home, away)
        except (OSError, ValueError, UnicodeError) as exc:
            raise Unavailable("morning_fotmob_unavailable") from exc
        if not baseline[0].date().isoformat() == DATE or not baseline[0] < window:
            raise Unavailable("morning_fotmob_time_invalid")
        blockers, basis, sources = compare_lineups(fixture_id, baseline, current, (home, away))
        if datetime.now(UTC) >= kickoff:
            failure = "kickoff_during_news_comparison"
    except Unavailable as exc:
        failure = str(exc)
    except (OSError, ValueError, OverflowError, RecursionError, TypeError):
        failure = "news_input_or_runtime_invalid"

    for direction in directions:
        if direction not in eligible:
            verdict = fallback(direction, "market_direction_unverified")
        elif failure:
            verdict = fallback(direction, failure)
        else:
            market, outcome, _, _ = direction
            verdict = {"market_id": market, "outcome": outcome,
                       "delta": "unknown" if blockers else "unchanged",
                       "basis": basis, "sources": sources, "blockers": blockers}
        result["verdicts"].append(verdict)
    if failure:
        result["reason"] = failure
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture_id", choices=FIXTURES)
    parser.add_argument("lead", type=int, choices=(90, 30))
    args = parser.parse_args()
    print(json.dumps(run_window(args.fixture_id, args.lead), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
