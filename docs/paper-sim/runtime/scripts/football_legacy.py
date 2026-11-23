"""Read the fixed November 22 assessments for the shared pregame round."""

import os
import time
from datetime import UTC, date, datetime, timedelta
from functools import cache
from importlib.util import module_from_spec, spec_from_file_location
from math import isclose
from pathlib import Path


RUNNER_PATH = Path(os.environ.get("PAL_LEGACY_RUNNER", ""))
FIXED_DATE = date(2026, 11, 22)
# The fixed runner budgets 120 seconds for capture, 100 for news and 65 for assessment.
COLLECTION_GRACE = timedelta(seconds=295)
POLL_SECONDS = 2
TERMINAL_FAILURES = ("collection_unavailable", "assessment_unavailable", "window_closed")


def _window_module(name, path):
    spec = spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("fixed_window_profile_unavailable")
    module = module_from_spec(spec)
    source = spec.loader.get_source(spec.name)
    if source is None:
        raise ImportError("fixed_window_profile_unavailable")
    exec(compile(source, str(path), "exec"), vars(module))
    return module


@cache
def load_runner():
    """Load the frozen window profile only on demand and without writing bytecode."""
    if not RUNNER_PATH.is_absolute():
        raise ImportError("fixed_window_profile_unavailable")
    runner = _window_module("_football_fixed_windows", RUNNER_PATH)
    if not all(callable(getattr(runner, name, None)) for name in ("get_stage", "parse_time", "read_json", "render")):
        raise ImportError("fixed_window_profile_unavailable")
    return runner


@cache
def _pinned_contracts(fixture_id, lead, kickoff):
    assessor = _window_module("_football_fixed_assessment", RUNNER_PATH.with_name("window_assessment.py"))
    title, _, fixture_ids, model_hash, market_hash = assessor.FIXTURES[fixture_id]
    official, _, rows = assessor.read_inputs(fixture_id, lead, title, kickoff, model_hash, market_hash)
    return official, fixture_ids, model_hash, {assessor.identity(market): (model, market) for model, market, _ in rows}


def _verified_signals(runner, stage, assessment, assessed_at, now):
    from football_feed import _paper_signal_rank
    from football_forward import FIXED_MODEL_VERSION
    from polymarket_alpha_lab.papersim import gates

    official, fixture_ids, model_hash, contracts = _pinned_contracts(stage.fixture_id, stage.lead, stage.kickoff)
    rows, failure = runner.assessment_rows(stage, assessment, now)
    if failure or not any(row.get("passed") is True and row.get("failed") == [] for _, row in rows):
        return []
    qualified = []
    for key, row in rows:
        if row.get("passed") is False and row.get("failed") == ["same_fixture_other_direction_selected"]:
            # The producer emits this only after all checks pass. The shared round ranks them again.
            row = dict(row, passed=True, failed=[])
        quote, _ = runner.qualifying_quote(row, stage, now, assessed_at)
        if not quote or key not in contracts:
            continue
        model, market = contracts[key]
        signal = runner.paper_signal(stage, key, row, quote)
        probability, price, fee = signal["model_probability"], signal["price"], signal["fee_rate"]
        gross = round(probability - price, 4)
        net = round(gross - round(gates.fee_cost(price, fee), 4), 4)
        haircut = gates.haircut_edge(net, probability)
        model_low, model_high = model["变体低值"], model["变体高值"]
        url = f"https://polymarket.com/event/{market['event_slug']}/{market['market_slug']}"
        if (not 0 <= model_low <= probability <= model_high <= 1
                or not gates.ENTRY_WINDOW[0] <= price <= gates.ENTRY_WINDOW[1]
                or signal["filled_usd"] < gates.DEFAULT_NOTIONAL_USD - 1e-6
                or signal["gamma_liquidity"] < gates.GATES["liq_min"]
                or round(model_high - model_low, 4) > gates.GATES["variant_spread_max"] + gates.GATE_EPSILON
                or signal["worst_edge"] < gates.GATES["worst_edge_min"] - gates.GATE_EPSILON
                or signal["adjusted_edge"] < signal["applied_edge_floor"] - gates.GATE_EPSILON
                or not all(isclose(actual, expected, rel_tol=0, abs_tol=1e-9) for actual, expected in (
                (probability, model["模型中位概率"]), (signal["min_variant"], model_low),
                (net, signal["net_edge"]), (haircut, signal["adjusted_edge"]),
                (round(model_low - price, 4), signal["worst_edge"]))) or signal["url"] != url):
            continue
        kind, side = model["_kind"], model["_side"]
        team_side = None
        if kind == "team_total":
            suffix = market["market_slug"].removeprefix(market["fixture_key"] + "-team-total-")
            team_side = suffix.split("-", 1)[0]
            if team_side not in {"home", "away"}:
                continue
        qualified.append(dict(signal, model_version=FIXED_MODEL_VERSION, model_digest=model_hash,
                              max_variant=model_high, conservative_probability=model_low,
                              market_kind=kind, line=market["line"], team_side=team_side,
                              side=side, token_id=market["token_id"], condition_id=market["condition_id"],
                              fixed_fixture={"base_slug": market["fixture_key"], "fixture_id": stage.fixture_id,
                                             "fotmob_league_id": str(fixture_ids[0]), "fotmob_home_id": str(fixture_ids[1]),
                                             "fotmob_away_id": str(fixture_ids[2]), "league": official["league"]}))
    return sorted(qualified, key=_paper_signal_rank)


def unavailable(reason="assessment_unavailable"):
    return {"status": "watch", "source": "legacy", "text": "", "reason_code": reason,
            "reason_codes": [reason], "coverage_gap": True}


def skip(reason):
    return {"status": "skip", "source": "legacy", "text": "", "reason_code": reason}


def read_window_file(runner, stage, filename):
    try:
        return runner.read_json(stage.workdir / filename, stage=stage)
    except (OSError, ValueError, RecursionError):
        return None


def collection_failure(runner, stage, now):
    status = read_window_file(runner, stage, "window-notice-status.json")
    if not isinstance(status, dict) or status.get("stage_key") != stage.key:
        return None
    saved = runner.parse_time(status.get("as_of"))
    state = status.get("state")
    if saved is not None and stage.starts_at <= saved <= now and state in TERMINAL_FAILURES:
        return state
    return None


def render_window(runner, stage, assessment, now, reason=None):
    result = runner.render(stage.fixture_id, stage.lead, assessment, now=now, unavailable_reason=reason)
    if not isinstance(result, dict) or result.get("source") != "legacy" or result.get("status") not in ("paper", "watch", "skip"):
        return unavailable()
    if result["status"] == "paper":
        try:
            signals = _verified_signals(runner, stage, assessment, runner.parse_time(result.get("assessed_at")), now)
        except Exception:
            return unavailable("legacy_evidence_incomplete")
        if not signals:
            return unavailable("legacy_evidence_incomplete")
        result.update(signal=signals[0], signals=signals, valid_until=signals[0]["valid_until"])
    return result


def review(row, now, *, wait: bool):
    """Read the scheduled assessment, optionally waiting for the in-window producer."""
    stage_name, fixture_id = row.get("stage"), row.get("legacy_id")
    if not fixture_id or stage_name not in ("t90", "t30"):
        return skip("legacy_stage_unsupported")
    if not isinstance(now, datetime) or now.utcoffset() is None:
        return skip("invalid_review")
    if row.get("fixture_state", "scheduled") != "scheduled":
        return skip("fixture_changed")
    kickoff = row.get("kickoff")
    try:
        if isinstance(kickoff, str):
            kickoff = datetime.fromisoformat(kickoff)
        if not isinstance(kickoff, datetime) or kickoff.utcoffset() is None or kickoff.astimezone(UTC).date() != FIXED_DATE:
            return skip("legacy_fixture_mismatch")
    except (OverflowError, ValueError):
        return skip("legacy_fixture_mismatch")
    if now >= kickoff:
        return skip("at_or_after_kickoff")

    try:
        runner = load_runner()
        stage = runner.get_stage(str(fixture_id), int(stage_name[1:]))
    except KeyError:
        return skip("legacy_fixture_mismatch")
    except (OSError, ImportError, SyntaxError):
        return unavailable()
    if stage.kickoff != kickoff:
        return skip("legacy_fixture_mismatch")
    if not stage.is_open(now):
        return render_window(runner, stage, None, now)

    deadline = min(stage.ends_at, stage.starts_at + COLLECTION_GRACE)
    opened = now
    started = time.monotonic() if wait else None
    while True:
        assessment = read_window_file(runner, stage, "window-assessment.json")
        if wait:
            now = opened + timedelta(seconds=time.monotonic() - started)
        if assessment is not None:
            return render_window(runner, stage, assessment, now)
        reason = collection_failure(runner, stage, now)
        delay = min(POLL_SECONDS, (deadline - now).total_seconds())
        if reason or not wait or delay <= 0:
            return render_window(runner, stage, None, now, reason or "assessment_unavailable")
        time.sleep(delay)
