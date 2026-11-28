"""Coverage and paired forecast-error reports for the five zero-stake research leagues."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import math
from statistics import fmean

from .euro_soccer_markets import FAMILIES
from .euro_soccer_model import MODEL_VERSION
from .euro_soccer_outcomes import observation_id
from .euro_soccer_sources import LEAGUES


SLOTS = ("preview", "forward", "closing")
SCORED_SLOTS = ("forward", "closing")
MODEL_SOURCES = ("baseline", "context")


def _schedule(doc):
    try:
        kickoff = datetime.fromisoformat(doc["kickoff"])
        identity = (doc["season"], doc["fixture_id"])
    except (KeyError, ValueError, TypeError):
        return None
    if kickoff.utcoffset() is None or not all(isinstance(v, str) and v for v in identity):
        return None
    teams = (doc.get("home_id"), doc.get("away_id"))
    if any(not isinstance(v, str) or not v for v in teams) or teams[0] == teams[1]:
        return None
    return (*identity, kickoff, *teams)


def _valid_score(score):
    return (isinstance(score, (list, tuple)) and len(score) == 2
            and all(type(n) is int and n >= 0 for n in score))


def _observed_families(markets, slot):
    # A pair of successful single-family requests is still two different batches.
    if not isinstance(markets, dict) or markets.get("window") != ("pregame" if slot == "preview" else slot):
        return set()
    digest, instant = markets.get("book_snapshot_sha256"), markets.get("observed_at")
    if not digest or not instant or not isinstance(markets.get("families"), dict):
        return set()
    return {name for name, item in markets["families"].items()
            if name in FAMILIES and isinstance(item, dict) and item.get("status") == "observed"
            and item.get("book_snapshot_sha256") == digest and item.get("observed_at") == instant}


def _brier(row, name):
    value = row.get(name)
    if isinstance(value, dict):
        n = value.get("brier")
        if type(n) in (float, int) and math.isfinite(n) and n >= 0:
            return n
    return None


def _collect_pairs(pairs, scored, observation, slot, score):
    if (not isinstance(scored, dict) or scored.get("window") != slot
            or scored.get("observation_id") != observation_id(observation)
            or scored.get("score") != list(score)):
        return
    receipt = scored.get("receipt")
    if not isinstance(receipt, dict) or receipt.get("market_window") != slot:
        return
    families = _observed_families(observation.get("markets"), slot)
    for family in families:
        row = scored.get("scores", {}).get(family)
        if not isinstance(row, dict):
            continue
        market = _brier(row, "market")
        if market is None:
            continue
        for source in MODEL_SOURCES:
            model = _brier(row, source)
            if model is not None:
                pairs[slot, family, source].append((model, market))


def _means(rows):
    if not rows:
        return {"pairs": 0, "model_mean": None, "market_mean": None, "delta_model_minus_market": None}
    model, market = zip(*rows, strict=True)
    return {"pairs": len(rows), "model_mean": fmean(model), "market_mean": fmean(market),
            "delta_model_minus_market": fmean(m - q for m, q in rows)}


def _league_report(documents):
    slots = {s: dict.fromkeys(("observations", *MODEL_SOURCES, *FAMILIES, "both_markets"), 0) for s in SLOTS}
    observed, results, baselines, contexts, complete = set(), set(), set(), set(), set()
    pairs = defaultdict(list)
    finals = Counter(sched[:2] for (sched, kind), doc in documents.items()
                     if kind == "result" and doc is not None and _valid_score(doc.get("score")))
    for (schedule, kind), doc in documents.items():
        if doc is None:  # Conflicting documents for the same fixture, kickoff and slot.
            continue
        fixture = schedule[:2]  # A rescheduled kickoff still belongs to one provider fixture.
        if kind == "result":
            if _valid_score(doc.get("score")):
                results.add(fixture)
                if finals[fixture] != 1:
                    continue
                for slot in SCORED_SLOTS:
                    observation = documents.get((schedule, slot))
                    if observation is not None:
                        _collect_pairs(pairs, doc.get(slot), observation, slot, doc["score"])
            continue
        observed.add(fixture)
        count = slots[kind]
        count["observations"] += 1
        forecast = doc.get("forecast")
        if isinstance(forecast, dict):
            for source, fixtures in (("baseline", baselines), ("context", contexts)):
                if isinstance(forecast.get(source), dict) and forecast[source]:
                    count[source] += 1
                    fixtures.add(fixture)
        families = _observed_families(doc.get("markets"), kind)
        for family in families:
            count[family] += 1
        if families == FAMILIES.keys():
            count["both_markets"] += 1
            complete.add(fixture)
    paired = {slot: {family: {source + "_vs_market": _means(pairs[slot, family, source])
                              for source in MODEL_SOURCES} for family in FAMILIES} for slot in SCORED_SLOTS}
    return {"distinct_fixtures": len(observed | results), "observed_fixtures": len(observed),
            "result_fixtures": len(results), "baseline_fixtures": len(baselines),
            "context_fixtures": len(contexts), "both_market_fixtures": len(complete),
            "conflicting_records": sum(doc is None for doc in documents.values()),
            "conflicting_result_schedules": sum(n > 1 for n in finals.values()),
            "slots": slots, "paired_brier": paired}


def summarize(records):
    """Aggregate current-version, regular-season records; Brier means share a pairwise denominator."""
    by_league = {league: {} for league in LEAGUES}
    for _, doc in records:
        if (not isinstance(doc, dict) or doc.get("league") not in LEAGUES or doc.get("phase") != "regular"
                or doc.get("model_version") != MODEL_VERSION or doc.get("kind") not in ("result", "observation")
                or doc.get("research_only") is not True or type(doc.get("amount_cny")) not in (float, int)
                or doc["amount_cny"] != 0):
            continue
        schedule = _schedule(doc)
        kind = "result" if doc["kind"] == "result" else doc.get("slot")
        if schedule is None or (doc["kind"] == "observation" and kind not in SLOTS):
            continue
        group = by_league[doc["league"]]
        key = (schedule, kind)
        if key not in group:
            group[key] = doc
        elif group[key] != doc:
            group[key] = None
    return {"research_only": True, "amount_cny": 0, "model_version": MODEL_VERSION,
            "leagues": {code: _league_report(group) for code, group in by_league.items()}}
