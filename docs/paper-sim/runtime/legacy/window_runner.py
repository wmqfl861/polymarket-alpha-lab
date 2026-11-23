"""Collect one scheduled football assessment for the daily notification round."""

import argparse
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import inf
from pathlib import Path
from urllib.parse import urlsplit


HERE = Path(__file__).resolve().parent
BOOK_MAX_AGE = timedelta(minutes=5)
COLLECT_SECONDS = 120
NEWS_SECONDS = 100
ASSESS_SECONDS = 65

# The fixed November 22 queue, with localized display names and pinned outcomes.
FIXTURES = {
    "5868133": ("巴塞罗那 vs 比利亚雷亚尔", (13, 0), {
        ("6898398", "Over"): "比利亚雷亚尔进球大 0.5",
        ("6898399", "Over"): "比利亚雷亚尔进球大 1.5",
    }),
    "5781804": ("阿贾克斯 vs ADO 海牙", (13, 30), {
        ("6896072", "Yes"): "双方均进球｜是",
    }),
    "5802993": ("洛里昂 vs 雷恩", (16, 15), {
        ("6891504", "Yes"): "洛里昂胜",
        ("6891506", "No"): "雷恩不胜",
        ("6893624", "Over"): "全场进球大 1.5",
        ("6893628", "Over"): "全场进球大 2.5",
        ("6893632", "Over"): "全场进球大 3.5",
        ("6893636", "Over"): "全场进球大 4.5",
        ("6893656", "Yes"): "双方均进球｜是",
        ("6893685", "Over"): "洛里昂进球大 0.5",
        ("6893688", "Over"): "洛里昂进球大 1.5",
    }),
    "5795521": ("利物浦 vs 曼联", (16, 30), {
        ("6893498", "Over"): "曼联进球大 0.5",
        ("6893502", "Over"): "曼联进球大 1.5",
    }),
    "5802994": ("马赛 vs 勒芒", (19, 45), {
        ("6891712", "No"): "马赛不胜",
        ("6891716", "Yes"): "勒芒胜",
    }),
}
SOURCE_NAMES = {
    "www.fotmob.com": "FotMob",
    "www.ajax.nl": "阿贾克斯官网",
    "adodenhaag.nl": "海牙官网",
    "www.liverpoolfc.com": "利物浦官网",
    "www.manutd.com": "曼联官网",
    "villarrealcf.es": "比利亚雷亚尔官网",
    "www.fcbarcelona.es": "巴塞罗那官网",
    "www.om.fr": "马赛官网",
    "www.fclorient.bzh": "洛里昂官网",
    "www.ouest-france.fr": "《法国西部报》",
    "www.letelegramme.fr": "《电讯报》",
    "www.staderennais.com": "雷恩官网",
}
NEWS_VERDICTS = frozenset({"better", "unchanged"})
GATE_REJECTIONS = frozenset({
    "fill_250", "liq", "entry_window", "exec_edge", "worst_edge", "variant_spread",
    "market_kind_suspended", "news_worse_than_model", "news_concordant", "same_fixture_other_direction_selected",
})
REASON_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
MARKET_URL = re.compile(r"https://polymarket\.com/event/[a-z0-9-]+/[a-z0-9-]+")


@dataclass(frozen=True)
class Stage:
    fixture_id: str
    lead: int
    fixture: str
    kickoff: datetime
    starts_at: datetime
    ends_at: datetime
    labels: dict

    @property
    def key(self):
        return f"pal-football-20261122-{self.fixture_id}-{self.lead}"

    @property
    def workdir(self):
        root = Path(os.environ.get("PAL_LEGACY_WINDOW_DIR", ""))
        if not root.is_absolute():
            raise ValueError("legacy_input_path_unconfigured")
        return root / f"{self.fixture_id}-{self.lead}"

    def is_open(self, at):
        return self.starts_at <= at < self.ends_at


def get_stage(fixture_id, lead):
    fixture, (hour, minute), labels = FIXTURES[fixture_id]
    if lead not in (90, 30):
        raise ValueError("lead must be 90 or 30")
    kickoff = datetime(2026, 11, 22, hour, minute, tzinfo=UTC)
    starts_at = kickoff - timedelta(minutes=lead)
    ends_at = kickoff - timedelta(minutes=30) if lead == 90 else kickoff
    return Stage(fixture_id, lead, fixture, kickoff, starts_at, ends_at, labels)


def utcnow():
    return datetime.now(UTC)


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is not None:
            return parsed.astimezone(UTC)
    except (OverflowError, TypeError, ValueError):
        pass
    return None


def bounded_number(value, low, high):
    return type(value) in (float, int) and low <= value <= high and value < inf


def failure_codes(row):
    codes = row.get("failed")
    if not isinstance(codes, list) or any(not isinstance(code, str) or not REASON_CODE.fullmatch(code) for code in codes):
        return {"assessment_unavailable"}
    return set(codes)


def news_sources(rows):
    names = []
    for row in rows:
        sources = row.get("sources")
        if not isinstance(sources, list):
            continue
        for source in sources:
            url = source.get("url") if isinstance(source, dict) else source
            if not isinstance(url, str) or len(url) > 512:
                continue
            try:
                parsed = urlsplit(url)
            except ValueError:
                continue
            name = SOURCE_NAMES.get(parsed.netloc) if parsed.scheme == "https" else None
            if name and name not in names:
                names.append(name)
            if len(names) == 3:
                return names
    return names


def assessment_rows(stage, assessment, now):
    if not isinstance(assessment, dict):
        return [], "assessment_unavailable"
    as_of = parse_time(assessment.get("as_of"))
    if (assessment.get("fixture_id") != stage.fixture_id or assessment.get("lead") != stage.lead
            or parse_time(assessment.get("kickoff_utc")) != stage.kickoff
            or as_of is None or not stage.is_open(as_of) or as_of > now):
        return [], "assessment_unavailable"
    reason = assessment.get("reason")
    status = assessment.get("status")
    if status not in ("ok", "blocked") or not isinstance(reason, str) or not REASON_CODE.fullmatch(reason):
        return [], "assessment_unavailable"
    if status == "ok" and reason not in ("market_entry_conditions_passed", "no_direction_passed"):
        return [], "assessment_unavailable"
    candidates = assessment.get("rows")
    by_key = {}
    if isinstance(candidates, list):
        for row in candidates:
            if not isinstance(row, dict):
                continue
            market_id, outcome = row.get("market_id"), row.get("outcome")
            if not isinstance(market_id, str) or not isinstance(outcome, str):
                continue
            key = market_id, outcome
            if key in by_key:
                return [], "assessment_unavailable"
            if key in stage.labels:
                by_key[key] = row
    rows = [(key, by_key[key]) for key in stage.labels if key in by_key]
    if status == "blocked":
        return rows, reason
    return rows, None if rows else "assessment_unavailable"


def qualifying_quote(row, stage, now, assessed_at):
    if row.get("passed") is not True or row.get("failed") != []:
        return None, "assessment_unavailable"
    delta = row.get("news_delta")
    if not isinstance(delta, str) or delta not in NEWS_VERDICTS or not news_sources([row]):
        return None, "news_source_invalid"
    ask, vwap = row.get("best_ask"), row.get("vwap250")
    bounds = (
        ("model_p", 0, 1), ("min_variant", 0, 1), ("net_edge", 0, 1), ("post_haircut", 0, 1),
        ("worst_edge", -1, 1), ("applied_edge_floor", 0, 1), ("fee_rate", 0, 1),
        ("depth_near_2c_usd", 0, inf), ("gamma_liquidity", 0, inf), ("filled_usd", 0, inf),
    )
    if (not bounded_number(ask, 0, 1) or not bounded_number(vwap, ask - 1e-9, 1)
            or any(not bounded_number(row.get(name), low, high) for name, low, high in bounds)
            or row["net_edge"] == 0):
        return None, "quote_metrics_invalid"
    url = row.get("market_url")
    if not isinstance(url, str) or not MARKET_URL.fullmatch(url):
        return None, "gamma_market_mismatch"
    observed = parse_time(row.get("book_observed_at"))
    if observed is None or observed > assessed_at:
        return None, "quote_metrics_invalid"
    valid_until = min(observed + BOOK_MAX_AGE, stage.ends_at)
    if valid_until <= now:
        return None, "clob_book_stale"
    return (observed, valid_until), None


def paper_signal(stage, key, row, quote):
    from polymarket_alpha_lab.papersim.gates import fee_cost

    observed, expiry = quote
    price, fee = row["vwap250"], row["fee_rate"]
    return {
        "market_id": key[0], "outcome": key[1], "direction": stage.labels[key],
        "price": price, "book_observed_at": observed.isoformat(), "valid_until": expiry.isoformat(),
        "model_probability": row["model_p"], "min_variant": row["min_variant"],
        "net_edge": row["net_edge"], "adjusted_edge": row["post_haircut"], "worst_edge": row["worst_edge"],
        "best_ask": row["best_ask"], "near_two_cent_depth": row["depth_near_2c_usd"], "filled_usd": row["filled_usd"],
        "fee_rate": fee, "fee_per_share": fee_cost(price, fee), "gamma_liquidity": row["gamma_liquidity"],
        "applied_edge_floor": row["applied_edge_floor"], "url": row["market_url"],
    }


def coverage_details(codes, completed):
    result = {"reason_codes": sorted(codes)}
    if codes - GATE_REJECTIONS:
        result["partial_coverage" if completed else "coverage_gap"] = True
    if any(code.startswith("clob_") for code in codes):
        result["partial_books"] = True
    return result


def render(fixture_id, lead, assessment, *, now=None, unavailable_reason=None):
    """Return a machine decision for the fixed window."""
    stage = get_stage(fixture_id, lead)
    if now is not None and now.utcoffset() is None:
        raise ValueError("render clock requires a timezone")
    now = utcnow() if now is None else now.astimezone(UTC)
    result = {
        "fixture_id": stage.fixture_id,
        "lead": stage.lead,
        "as_of": now.isoformat(),
        "kickoff_utc": stage.kickoff.isoformat(),
        "stage_key": stage.key,
        "source": "legacy",
        "status": "watch",
        "text": "",
    }
    if not stage.is_open(now):
        if now < stage.starts_at:
            reason = "window_not_open"
        else:
            reason = "at_or_after_kickoff" if now >= stage.kickoff else "window_expired"
        return dict(result, status="skip", reason_code=reason)

    rows, blocked_reason = assessment_rows(stage, assessment, now)
    codes = set().union(*(failure_codes(row) for _, row in rows))
    reason = unavailable_reason or blocked_reason
    if reason:
        codes.add(reason)
        return dict(result, reason_code=reason, **coverage_details(codes, completed=False))

    has_passed = any(row.get("passed") is True for _, row in rows)
    if has_passed != (assessment["reason"] == "market_entry_conditions_passed"):
        codes.add("assessment_unavailable")
        return dict(result, reason_code="assessment_unavailable", **coverage_details(codes, completed=False))

    assessed_at = parse_time(assessment["as_of"])
    result["assessed_at"] = assessed_at.isoformat()
    if len(rows) != len(stage.labels):
        codes.add("legacy_candidate_missing")
    qualified, quote_errors = [], []
    completed = False
    for key, row in rows:
        if row.get("passed") is True:
            quote, failure = qualifying_quote(row, stage, now, assessed_at)
            if quote:
                qualified.append((key, row, quote))
                completed = True
            else:
                quote_errors.append(failure)
        else:
            failures = failure_codes(row)
            if row.get("passed") is not False or not failures:
                codes.add("assessment_unavailable")
            elif failures <= GATE_REJECTIONS:
                completed = True
    codes.update(quote_errors)
    result.update(coverage_details(codes, completed))
    if qualified:
        key, row, quote = max(qualified, key=lambda item: (item[1]["net_edge"], item[1]["worst_edge"]))
        best = paper_signal(stage, key, row, quote)
        return dict(result, status="paper", reason_code="qualified", signal=best, signals=[best], valid_until=best["valid_until"])
    reason = "no_direction_passed" if completed else "legacy_evidence_incomplete"
    if quote_errors:
        reason = quote_errors[0]
    return dict(result, reason_code=reason)


class WindowClosed(Exception):
    """Raised when the scheduled window or its authorization ends."""


def read_json(path, *, max_bytes=8 * 1024 * 1024, stage=None):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
            raise ValueError("not a bounded JSON file")
        if stage and not stage.starts_at.timestamp() <= metadata.st_mtime < stage.ends_at.timestamp():
            raise ValueError("not a collection for this window")
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("JSON file too large")
    return json.loads(data)


def owner_verified():
    path = Path(os.environ.get("PAL_LEGACY_APPROVAL_FILE", ""))
    if not path.is_absolute():
        return False
    try:
        record = read_json(path, max_bytes=16 * 1024)
    except (OSError, ValueError):
        return False
    return isinstance(record, dict) and record.get("verified") is True


def remaining(stage):
    now = utcnow()
    if not stage.is_open(now) or not owner_verified():
        raise WindowClosed
    return (stage.ends_at - now).total_seconds()


def await_collection(stage):
    pending = {
        stage.workdir / f"fotmob-prematch-{stage.fixture_id}.json": dict,
        stage.workdir / "prematch-source.json": dict,
        stage.workdir / "shortlist-price-recheck.json": list,
    }
    deadline = time.monotonic() + COLLECT_SECONDS
    while pending:
        seconds = remaining(stage)
        for path, expected_type in list(pending.items()):
            try:
                result = read_json(path, stage=stage)
            except (OSError, ValueError):
                continue
            if isinstance(result, expected_type):
                del pending[path]
        wait = min(2, seconds, deadline - time.monotonic())
        if wait <= 0 or not pending:
            break
        time.sleep(wait)
    return not pending


def write_json(path, record):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def invoke(stage, python, script, arguments, timeout):
    try:
        with subprocess.Popen(
            [python, "-B", str(HERE / script), *arguments],
            cwd=stage.workdir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ) as child:
            try:
                output, _ = child.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
                return None
            return child.returncode, output
    except OSError:
        pass
    return None


def component_json(stage, script, args, timeout):
    completed = invoke(stage, sys.executable, script, args, min(timeout, remaining(stage)))
    if completed is None:
        return None
    code, output = completed
    if len(output) > 1024 * 1024:
        return None
    try:
        result = json.loads(output)
        if isinstance(result, dict):
            json.dumps(result, allow_nan=False)
            if code == 0 or (code == 2 and result.get("status") == "blocked"):
                return result
    except ValueError:
        pass
    return None


def collect_assessment(stage):
    args = [stage.fixture_id, str(stage.lead)]
    news = component_json(stage, "window_news.py", args, NEWS_SECONDS)
    if news is None:
        news = {"fixture_id": stage.fixture_id, "lead": stage.lead, "verdicts": [],
                "reason": "news_input_unavailable"}
    news_path = stage.workdir / "window-news.json"
    write_json(news_path, news)
    assessment = component_json(
        stage, "window_assessment.py", [*args, str(news_path)], ASSESS_SECONDS
    )
    if assessment is not None:
        write_json(stage.workdir / "window-assessment.json", assessment)
    return assessment


def report(stage, state):
    write_json(stage.workdir / "window-notice-status.json",
               {"stage_key": stage.key, "state": state, "as_of": utcnow().isoformat()})


def run_window(stage):
    try:
        remaining(stage)
    except WindowClosed:
        return 0
    try:
        directory = stage.workdir
    except ValueError:
        return 1
    if not directory.is_dir() or directory.is_symlink():
        return 1

    try:
        # Each stage is collected once, including direct launches of this runner.
        claim = os.open(directory / "window-notice.claim",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return 0
    os.close(claim)

    try:
        report(stage, "collecting")
        if not await_collection(stage):
            report(stage, "collection_unavailable")
            return 0
        assessment = collect_assessment(stage)
        remaining(stage)
        report(stage, "assessment_ready" if assessment is not None else "assessment_unavailable")
        return 0
    except WindowClosed:
        report(stage, "window_closed")
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture_id", choices=tuple(FIXTURES))
    parser.add_argument("lead", type=int, choices=(90, 30))
    parser.add_argument("--render-only", metavar="ASSESSMENT_JSON", type=Path)
    parser.add_argument("--as-of", metavar="TIMESTAMP", help="Timezone-aware render-only clock")
    args = parser.parse_args()
    if args.render_only:
        now = parse_time(args.as_of) if args.as_of else None
        if args.as_of and now is None:
            parser.error("--as-of requires a timezone-aware ISO timestamp")
        try:
            assessment = read_json(args.render_only)
        except (OSError, ValueError):
            parser.error("could not read assessment JSON")
        decision = render(args.fixture_id, args.lead, assessment, now=now)
        print(json.dumps(decision, ensure_ascii=False, allow_nan=False))
        return 0
    if args.as_of:
        parser.error("--as-of requires --render-only")
    return run_window(get_stage(args.fixture_id, args.lead))


if __name__ == "__main__":
    raise SystemExit(main())
