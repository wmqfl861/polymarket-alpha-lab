"""Score archived pre-match research against a separately observed regulation-time result."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
import re

from .euro_soccer_markets import (MARKET_PROBABILITY_KEYS, MODEL_PROBABILITY_KEYS, capture_slot,
                                 news_scenario_issue, probability_vector, valid_source_hashes)
from .euro_soccer_sources import LEAGUES, Fixture
from .optimize.metrics import binary_brier, binary_log_loss, multiclass_brier, multiclass_log_loss


_IDENTITY = ("league", "season", "phase", "fixture_id", "home_id", "away_id")


def observation_id(observation: dict) -> str:
    """Identify the archived pre-match document independently of its database key."""
    canonical = json.dumps(observation, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode()).hexdigest()


def _time(value) -> datetime | None:
    try:
        at = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return at.astimezone(UTC) if at.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _digest(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _research_only(document):
    amount = document.get("amount_cny")
    return document.get("research_only") is True and type(amount) in (float, int) and amount == 0


def _model_reason(model, fixture, cutoff, observation, slot):
    if not isinstance(model, dict):
        return "forecast_missing"
    stamp = _time(model.get("as_of"))
    if (model.get("league") != fixture.league or model.get("fixture_id") != fixture.fixture_id
            or not isinstance(model.get("version"), str) or not model["version"]
            or observation.get("model_version", model["version"]) != model["version"] or not _research_only(model)):
        return "forecast_identity_invalid"
    if stamp is None or stamp > cutoff or (slot is not None and capture_slot(fixture.kickoff, stamp) != slot):
        return "forecast_outside_observation"
    if model.get("status") not in ("team_baseline", "team_news_scenario"):
        return "forecast_unavailable"
    refs = model.get("source_refs")
    if not valid_source_hashes(refs) or ("calendar_sha256" in observation and observation["calendar_sha256"] not in refs):
        return "forecast_source_receipt_missing"
    return None


def _market_reason(markets, fixture, cutoff, observation, slot, forecast_at):
    if not isinstance(markets, dict) or markets.get("status") not in ("partial", "complete"):
        return "market_unavailable"
    identity = markets.get("fixture")
    if (not isinstance(identity, dict) or identity.get("league") != fixture.league
            or identity.get("fixture_id", fixture.fixture_id) != fixture.fixture_id
            or _time(identity.get("kickoff")) != fixture.kickoff
            or identity.get("home") != observation["fixture"].get("home_name", fixture.home_name)
            or identity.get("away") != observation["fixture"].get("away_name", fixture.away_name)):
        return "market_fixture_mismatch"
    stamp = _time(markets.get("observed_at"))
    if stamp is None or stamp > cutoff or (forecast_at is not None and stamp < forecast_at):
        return "market_outside_observation"
    captured_slot = capture_slot(fixture.kickoff, stamp)
    market_window = captured_slot if captured_slot in ("forward", "closing") else "pregame"
    if markets.get("window") != market_window or (slot is not None and captured_slot != slot):
        return "market_window_mismatch"
    digest = markets.get("book_snapshot_sha256")
    if not _digest(digest) or not valid_source_hashes(markets.get("source_sha256s")) or digest not in markets["source_sha256s"]:
        return "market_batch_receipt_missing"
    return None


def _market_distribution(markets, family, reason):
    if reason:
        return None, reason
    families = markets.get("families")
    row = families.get(family) if isinstance(families, dict) else None
    if not isinstance(row, dict) or row.get("status") != "observed":
        return None, "market_family_unavailable"
    if (row.get("book_snapshot_sha256") != markets["book_snapshot_sha256"]
            or _time(row.get("observed_at")) != _time(markets["observed_at"])):
        return None, "market_family_batch_mismatch"
    return probability_vector(row.get("market_probabilities"), MARKET_PROBABILITY_KEYS[family]), None


def _scores(probabilities, family, winner, over):
    if family == "match_winner":
        return {"brier": multiclass_brier(probabilities, winner),
                "log_loss": multiclass_log_loss(probabilities, winner, floor=1e-15)}
    return {"brier": binary_brier(probabilities[0], over),
            "log_loss": binary_log_loss(probabilities[0], over, floor=1e-15, clamp="realized")}


def score_outcome(observation: dict, fixture: Fixture, *, as_of: datetime) -> dict | None:
    """Return six independently available proper scores, with a receipt for the verified FT result.

    The observation is an archived JSON document. Its capture and the result receipt must
    straddle the same kickoff. Later lookups of the same observation produce the same ID.
    """
    if not isinstance(observation, dict) or not isinstance(fixture, Fixture) or not _research_only(observation):
        return None
    identity = observation.get("fixture")
    if (not isinstance(identity, dict) or fixture.league not in LEAGUES or fixture.phase != "regular"
            or fixture.home_id == fixture.away_id
            or any(identity.get(key) != getattr(fixture, key) for key in _IDENTITY)
            or any(key in observation and observation[key] != identity[key] for key in _IDENTITY)
            or _time(identity.get("kickoff")) != fixture.kickoff):
        return None
    if (fixture.status != "finished" or not isinstance(fixture.score, tuple) or len(fixture.score) != 2
            or any(type(n) is not int or n < 0 for n in fixture.score) or not _digest(fixture.source_sha256)):
        return None
    cutoff, kickoff, received, now = map(_time, (observation.get("observed_at"), fixture.kickoff,
                                                  fixture.observed_at, as_of))
    if None in (cutoff, kickoff, received, now) or not cutoff < kickoff < received <= now:
        return None
    slot = observation.get("slot")
    if slot is not None and (slot not in ("preview", "forward", "closing") or capture_slot(kickoff, cutoff) != slot):
        return None
    home, away = fixture.score
    winner, over = (0 if home > away else 1 if home == away else 2), home + away >= 3
    model, markets = observation.get("forecast"), observation.get("markets")
    model_reason = _model_reason(model, fixture, cutoff, observation, slot)
    context_reason = model_reason or news_scenario_issue(model)
    forecast_at = _time(model["as_of"]) if model_reason is None else None
    market_reason = _market_reason(markets, fixture, cutoff, observation, slot, forecast_at)
    scores, missing = {}, {}
    for family, model_keys in MODEL_PROBABILITY_KEYS.items():
        scores[family] = {}
        distributions = {"market": _market_distribution(markets, family, market_reason)}
        for kind, reason in (("baseline", model_reason), ("context", context_reason)):
            values = model.get(kind) if reason is None else None
            if values is None and reason is None:
                reason = kind + "_missing"
            distributions[kind] = probability_vector(values, model_keys), reason
        for source, (probs, reason) in distributions.items():
            scores[family][source] = _scores(probs, family, winner, over) if probs is not None else None
            if probs is None:
                missing.setdefault(family, {})[source] = reason or "invalid_probability_distribution"

    return {"observation_id": observation_id(observation), "research_only": True, "amount_cny": 0,
            "window": slot, "score": list(fixture.score),
            "fixture": {key: getattr(fixture, key) for key in _IDENTITY} | {"kickoff": kickoff.isoformat()},
            "result": {"home_goals": home, "away_goals": away, "match_winner": MARKET_PROBABILITY_KEYS["match_winner"][winner],
                       "total_2_5": "over" if over else "under"},
            "scores": scores, "missing": missing,
            "receipt": {"source": "fotmob_regulation_full_time", "observed_at": cutoff.isoformat(),
                        "managed_observation_id": observation.get("observation_id"),
                        "result_observed_at": received.isoformat(), "result_source_sha256": fixture.source_sha256,
                        "forecast_version": model["version"] if model_reason is None else None,
                        "forecast_as_of": model["as_of"] if model_reason is None else None,
                        "market_window": markets["window"] if market_reason is None else None,
                        "market_observed_at": markets["observed_at"] if market_reason is None else None,
                        "book_snapshot_sha256": markets["book_snapshot_sha256"] if market_reason is None else None}}
