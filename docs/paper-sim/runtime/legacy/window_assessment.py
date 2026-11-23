"""Read-only football window assessment for the five fixtures on 2026-11-22."""

import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit

sys.dont_write_bytecode = True

from polymarket_alpha_lab.papersim import fees, fotmob, gates, soccer_model, xg
from polymarket_alpha_lab.papersim.adapters import polymarket as pm
from polymarket_alpha_lab.team_research_agent_types import strict_json


XG_NAME = "xg-verified-sports-window.json"
XG_HASH = "2613ff8691502a114c4397ec0a437aace9da60b80f8ddf8be9e39f88b2a7c52a"
MORNING_MODEL_HASH = "885fa74130dd3bd68dbf71fc0d409c49b66014895408ec38e7063bc8764ef85b"
ERE_BASE_HASH = "54464dc8b13c357960b9cf46465db9a9aa5d0c98063c06598b7413716f6e6ebc"
DELTA_HASH = "e298fa3652e1f289bec6abefddebcb399191183ac94b2a05111687cf53c495b1"
BOOK_MAX_AGE = timedelta(minutes=5)
REGULATION = "first 90 minutes of regular play plus stoppage time"

# Fixture title, kickoff UTC, FotMob IDs for the league and both teams, and input hashes.
FIXTURES = {
    "5781804": ("AFC Ajax vs. ADO Den Haag", "13:30", (937276, 8593, 10217),
                "0581d3a8aba4db6d0aae9126fc07517f8ef98827e200e3c6fff9b17d3b19787a",
                "0c23cc5846c14bc00f4b1c0095dc45d01c2bcd37043cb8433e9f64cef064390e"),
    "5795521": ("Liverpool FC vs. Manchester United FC", "16:30", (47, 8650, 10260),
                "e68d8e0ff1ad5385bae53f545f39b04df4c3712995482063019110c2190ef103",
                "c9eb88bb41277f71219d836bf221160004b7213d95806b2db4ac2fb9ddd85690"),
    "5802993": ("FC Lorient vs. Stade Rennais FC 1901", "16:15", (53, 8689, 9851),
                "d2b033ce1b9862e399df8615418d320b9e68c16b515f85dce16d7074da8071e6",
                "c54dbbbc059d9d4a52475146c4955bb844b37894cbcf2b7c87030c437c74e95d"),
    "5802994": ("Olympique de Marseille vs. Le Mans FC", "19:45", (53, 8592, 8682),
                "ab54a1de90d3ef49749e6bb2747884c86062c911d1e6f1309681fba9ecff433e",
                "1df38b7b3e08f0a408583060c33c6c4b3db1864e6dda4ec3f462bf4e7bd2ca45"),
    "5868133": ("FC Barcelona vs. Villarreal CF", "13:00", (87, 8634, 10205),
                "d6780c31eb01204f9580b296839a077773ae8f4ba1b1a01687bd492107beb0a7",
                "dc4dea5ab708bb2e8954f426ac9751871e886c20c864b57be5531ee7686ccdbe"),
}


class Blocked(Exception):
    """One fixed reason code suitable for assessment output."""


def require(condition, reason):
    if not condition:
        raise Blocked(reason)


def now():
    return datetime.now(UTC)


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("missing timezone")
    return result.astimezone(UTC)


def window(kickoff, lead, as_of):
    begin = kickoff - timedelta(minutes=lead)
    end = kickoff - timedelta(minutes=30) if lead == 90 else kickoff
    require(as_of < kickoff, "at_or_after_kickoff")
    require(as_of >= begin, "window_not_open")
    require(as_of < end, "window_expired")
    return end


def fixed_json(path, digest):
    try:
        raw = path.read_bytes()
    except OSError:
        raise Blocked("pinned_input_unavailable") from None
    require(hashlib.sha256(raw).hexdigest() == digest, "pinned_input_sha256_mismatch")
    return json.loads(raw)


def input_path(name):
    path = Path(os.environ.get(name, ""))
    require(path.is_absolute(), "legacy_input_path_unconfigured")
    return path


def identity(row):
    return row["market_id"], row["outcome"]


def number(value):
    if type(value) not in (str, float, int):
        raise ValueError("invalid number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite number")
    return result


def option_pairs(market):
    outcomes, tokens = (json.loads(value) if isinstance(value, str) else value
                        for value in (market["outcomes"], market["clobTokenIds"]))
    require(isinstance(outcomes, list) and isinstance(tokens, list)
            and len(outcomes) == len(tokens) == 2 and len(set(outcomes)) == len(set(tokens)) == 2
            and all(isinstance(token, str) and token.isascii() and token.isdecimal() for token in tokens),
            "gamma_market_mismatch")
    return tuple(zip(outcomes, tokens))


def members(event):
    markets = event["markets"]
    by_id = {str(market["id"]): market for market in markets}
    require(len(markets) == len(by_id), "gamma_event_mismatch")
    return by_id


def read_inputs(fixture_id, lead, fixture, kickoff, model_hash, market_hash):
    part = input_path("PAL_LEGACY_WINDOW_DIR") / f"{fixture_id}-{lead}"
    morning_dir = input_path("PAL_LEGACY_BASELINE_DIR")
    morning = fixed_json(part / "model-assessment.json", model_hash)["numeric_pass"]
    prepared = fixed_json(part / "markets-prepared.json", market_hash)
    by_direction = {identity(item): item for item in prepared}
    require(len(morning) == len(prepared) == len(by_direction) > 0, "pinned_fixture_mismatch")
    official = morning[0]["_official"]
    require(official["fixture_id"] == fixture_id and timestamp(official["start"]) == kickoff,
            "pinned_fixture_mismatch")

    events, contracts, rows = {}, {}, []
    for model in morning:
        key = (model["市场ID"], model["选项"])
        market = by_direction.pop(key)
        require(model["_official"] == official and model["对阵"] == fixture
                and model["盘口描述"] == market["question"] and model["盘口线"] == market["line"]
                and model["_numeric_ok"] is True and model["_allowed"] is True
                and market["timing_ok"] is True and timestamp(market["kickoff_utc"]) == kickoff
                and timestamp(market["market_kickoff_utc"]) == kickoff and REGULATION in market["resolution"],
                "pinned_fixture_mismatch")
        event_id = market["event_id"]
        if event_id not in events:
            archive = fixed_json(morning_dir / "event-details-v2" / f"{event_id}.json", market["gamma_sha256"])
            require(str(archive["id"]) == event_id and archive["slug"] == market["event_slug"]
                    and archive["title"] in (fixture, fixture + " - More Markets")
                    and timestamp(archive["startTime"]) == kickoff, "pinned_fixture_mismatch")
            events[event_id] = archive
            contracts[event_id] = members(archive)
        contract = contracts[event_id][key[0]]
        require(dict(option_pairs(contract)).get(key[1]) == market["token_id"], "pinned_fixture_mismatch")
        rows.append((model, market, contract))
    return official, events, rows


def model_snapshot(league, kickoff):
    morning_dir = input_path("PAL_LEGACY_BASELINE_DIR")
    provenance = fixed_json(morning_dir / "model-assessment.json", MORNING_MODEL_HASH)["football_source"][league]
    require(provenance["fresh_verified"] is True and timestamp(provenance["last_completed"]) < kickoff,
            "model_unavailable")
    snapshot = fixed_json(morning_dir / XG_NAME, XG_HASH)
    if league != "Eredivisie":
        return snapshot

    # The morning scan's Eredivisie refresh ran in memory after writing XG_NAME.
    originals = fixed_json(input_path("PAL_LEGACY_EREDIVISIE_XG"), ERE_BASE_HASH)
    season = "Eredivisie_2026"
    previous = originals[season]
    recent = fixed_json(morning_dir / "fotmob-deltas.json", DELTA_HASH)[season]
    require(set(recent["teams"]).issubset(previous["teams"]), "model_baseline_mismatch")
    added = 0
    for team, refresh in recent["teams"].items():
        history = previous["teams"][team]["history"]
        existing = {game["date"] for game in history}
        additions = [game for game in refresh["history"] if game["date"] not in existing]
        added += len(additions)
        history.extend(additions)
        history.sort(key=lambda game: game["date"])
    require(added == 2 * provenance["accepted"] and added > 0, "model_baseline_mismatch")
    existing = {str(game["id"]) for game in previous["dates"]}
    previous["dates"].extend(game for game in recent["dates"] if str(game["id"]) not in existing)
    previous["dates"].sort(key=lambda game: game["datetime"])
    snapshot.update(originals)
    return snapshot


def build_candidate(snapshot, official, morning, market, form_hits):
    home, away, league = official["home"], official["away"], official["league"]
    kind, side, line = morning["_kind"], morning["_side"], market["line"]
    require(gates.suspension_policy(kind).eligible, "market_kind_suspended")
    team_side = None
    if kind == "team_total":
        suffix = market["market_slug"].removeprefix(market["fixture_key"] + "-team-total-")
        team_side = suffix.split("-", 1)[0]
        require(team_side in ("home", "away"), "pinned_fixture_mismatch")
    # The constructor stores the book price as metadata. The gate below walks the live asks itself.
    envelope = soccer_model.candidate_for_market(
        snapshot, home, league, away, league, market["question"], market["market_id"], morning["整笔买入VWAP"],
        kind=kind, side=side, line=line, team_side=team_side, season="2026",
    )
    require(envelope is not None, "model_unavailable")
    candidate = envelope.candidate
    variants = candidate.qs_variants
    require(len(variants) == len(soccer_model.SWEEPS) == 10 and all(0 <= p <= 1 for p in variants),
            "model_unavailable")
    expected = tuple(morning[field] for field in ("模型中位概率", "变体低值", "变体高值"))
    actual = (candidate.model_median, min(variants), max(variants))
    require(all(math.isclose(a, b, rel_tol=0, abs_tol=1e-12) for a, b in zip(actual, expected)),
            "model_baseline_mismatch")
    if (kind, side) in (("total", "over"), ("btts", "yes")):
        require(form_hits is not None, "form_unavailable")
    home_lambda, away_lambda = candidate.lambda_home, candidate.lambda_away
    risk, _ = gates.under_risk_check(kind=kind, side=side, o25_hits=form_hits or 0,
                                     lambda_home=home_lambda, lambda_away=away_lambda,
                                     lambda_total=home_lambda + away_lambda, line=line or 2.5)
    return candidate, risk


def read_news(path, fixture_id, lead):
    try:
        if not path.is_file():
            return {}, "news_input_unavailable"
        with path.open("rb") as source:
            raw = source.read(262_145)
        require(len(raw) <= 262_144, "news_verdict_invalid")
        data = strict_json(raw.decode("utf-8"))
        require(data["fixture_id"] == fixture_id and type(data["lead"]) is int and data["lead"] == lead
                and isinstance(data["verdicts"], list), "news_verdict_invalid")
        verdicts = {}
        for verdict in data["verdicts"]:
            key = identity(verdict)
            require(all(isinstance(value, str) for value in key), "news_verdict_invalid")
            verdicts[key] = None if key in verdicts else verdict
        return verdicts, None
    except OSError:
        return {}, "news_input_unavailable"
    except (Blocked, ValueError, TypeError, KeyError, AttributeError):
        return {}, "news_verdict_invalid"


def news_source(item, as_of):
    url, observed_at, finding = item["url"], timestamp(item["observed_at"]), item["finding"]
    parsed = urlsplit(url)
    require(parsed.scheme in ("http", "https") and parsed.hostname and parsed.username is None
            and not any(char.isspace() or ord(char) < 32 for char in url)
            and observed_at <= as_of and isinstance(finding, str) and finding.strip(), "news_source_invalid")
    return {"url": url, "observed_at": observed_at.isoformat(), "finding": finding.strip()}


def apply_news(row, verdicts, input_error, fixture, lead, as_of):
    key = identity(row)
    entry = verdicts.get(key)
    problems, sources, blockers = [], [], []
    delta, basis = gates.NEWS_UNKNOWN, ""
    if input_error or key not in verdicts:
        problems.append(input_error or "news_verdict_missing")
    else:
        try:
            delta, basis = entry["delta"], entry["basis"]
            blockers, inputs = entry["blockers"], entry["sources"]
            require(delta in gates.NEWS_DELTAS and isinstance(basis, str)
                    and isinstance(inputs, list) and isinstance(blockers, list)
                    and all(isinstance(item, str) and item.strip() for item in blockers), "news_verdict_invalid")
            for item in inputs:
                try:
                    sources.append(news_source(item, as_of))
                except (Blocked, ValueError, TypeError, KeyError, AttributeError, OverflowError):
                    problems.append("news_source_invalid")
            if delta != gates.NEWS_UNKNOWN and (not basis.strip() or not sources):
                problems.append("news_source_invalid")
            if problems:
                delta = gates.NEWS_UNKNOWN
        except (Blocked, KeyError, TypeError):
            delta, basis, blockers = gates.NEWS_UNKNOWN, "", []
            problems.append("news_verdict_invalid")
    if blockers:
        problems.append("news_blockers_present")
    if delta == gates.NEWS_WORSE:
        problems.append("news_worse_than_model")
    if delta == gates.NEWS_UNKNOWN:
        problems.append("news_unknown")
    check = gates.check_news(fixture, f"T-{lead}", news_delta=delta, blockers=blockers + problems,
                             sources=[item["url"] for item in sources])
    row.update(news_delta=delta, basis=basis.strip(), sources=sources, news_blockers=blockers)
    row["failed"].extend(problems)
    if not check.concordant:
        row["failed"].append("news_concordant")
    return check


def check_prematch(fixture_id, official, fixture_ids, kickoff, budget):
    try:
        data = fotmob.fetch_match_details(fixture_id, allow_public_fetch=True, timeout=8, budget=budget)
    except Exception:
        raise Blocked("fotmob_unavailable") from None
    try:
        match, status = data["general"], data["header"]["status"]
        home, away = match["homeTeam"], match["awayTeam"]
        observed = (str(match["leagueId"]), str(home["id"]), str(away["id"]))
        require(str(match["matchId"]) == fixture_id and observed == tuple(map(str, fixture_ids))
                and (home["name"], away["name"]) == (official["home"], official["away"])
                and timestamp(status["utcTime"]) == kickoff, "fotmob_fixture_mismatch")
        require(all(status.get(flag) is False for flag in ("started", "finished", "cancelled"))
                and status.get("postponed") is not True and data.get("ongoing") is False,
                "fotmob_not_prematch")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise Blocked("fotmob_fixture_mismatch") from None


def public_json(url, budget, cutoff, error_code):
    require(now() < cutoff, "window_expired")
    try:
        raw, fetched = pm._fetch_bytes(url, opener=pm.BoundedPublicOpener(), budget=budget, now_utc=now)
        result = json.loads(raw)
    except Exception:
        raise Blocked(error_code) from None
    require(fetched < cutoff, "window_expired")
    require(isinstance(result, dict), error_code)
    return result, fetched


def open_market(record):
    return (record.get("active") is True and record.get("closed") is False
            and all(record.get(flag) is None or record[flag] is False for flag in ("archived", "live", "ended")))


def event_markets(archive, kickoff, budget, cutoff):
    def teams(event):
        return sorted((str(team["id"]), team["ordering"], team["league"], team["name"]) for team in event["teams"])

    live, _ = public_json(f"{pm.GAMMA_BASE_URL}/events/{archive['id']}", budget, cutoff, "gamma_event_unavailable")
    try:
        fields = ("id", "slug", "title", "gameId", "parentEventId")
        require(all(str(live.get(field)) == str(archive.get(field)) for field in fields)
                and timestamp(live["startTime"]) == kickoff and teams(live) == teams(archive), "gamma_event_mismatch")
        require(open_market(live), "gamma_event_unavailable")
        return members(live)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise Blocked("gamma_event_mismatch") from None


def verify_contract(live, market, archive, kickoff):
    try:
        keys = {"id": "market_id", "slug": "market_slug", "conditionId": "condition_id", "question": "question",
                "sportsMarketType": "kind", "groupItemTitle": "group", "resolutionSource": "rules_source"}
        line = None if live.get("line") is None else number(live["line"])
        require(all(str(live.get(field)) == str(market[expected]) for field, expected in keys.items())
                and line == market["line"] and timestamp(live["gameStartTime"]) == kickoff
                and option_pairs(live) == option_pairs(archive)
                and REGULATION in live["description"]
                and live["description"].split() == market["resolution"].split(), "gamma_market_mismatch")
        require(open_market(live) and live.get("acceptingOrders") is True and live.get("enableOrderBook") is True,
                "gamma_market_unavailable")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise Blocked("gamma_market_mismatch") from None


def market_rate(live):
    try:
        schedule = live["feeSchedule"]
        require(live.get("feesEnabled") is True and schedule.get("takerOnly") is True
                and number(schedule["exponent"]) == 1, "gamma_fee_unverified")
        return fees.resolve_taker_fee_rate("sports", market_rate=number(schedule["rate"]))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise Blocked("gamma_fee_unverified") from None


def order_book(token, condition_id, budget, cutoff):
    endpoint = pm.CLOB_BOOK_URL + "?" + urlencode({"token_id": token})
    raw, fetched = public_json(endpoint, budget, cutoff, "clob_book_unavailable")
    require(raw.get("asset_id") == token and raw.get("market") == condition_id, "clob_book_mismatch")
    try:
        book = pm._parse_order_book(token, raw)
    except Exception:
        raise Blocked("clob_book_mismatch") from None
    observed = book.book_observed_at
    require(observed is not None and timedelta(0) <= fetched - observed <= BOOK_MAX_AGE, "clob_book_stale")
    bid = next((level.price for level in book.bids if level.size > 0), None)
    ask = next((level.price for level in book.asks if level.size > 0), None)
    require(bid is None or ask is None or bid <= ask, "clob_book_crossed")
    return book


def evaluate_market(row, market, archive, event, candidate, risk, news, kickoff, cutoff, budget):
    try:
        market_id = row["market_id"]
        require(market_id in event, "gamma_market_mismatch")
        verify_contract(event[market_id], market, archive, kickoff)
        live, _ = public_json(f"{pm.GAMMA_BASE_URL}/markets/{market_id}", budget, cutoff, "gamma_market_unavailable")
        verify_contract(live, market, archive, kickoff)

        token = market["token_id"]
        condition = market["condition_id"]
        chosen = order_book(token, condition, budget, cutoff)
        asks = [(level.price, level.size) for level in chosen.asks if level.size > 0]
        walk = gates.walk_book(asks)
        row.update(best_ask=walk.best, vwap250=walk.vwap if walk.filled else None,
                   book_observed_at=chosen.book_observed_at.isoformat(),
                   depth_near_2c_usd=walk.depth_near2c, filled_usd=walk.filled_notional)

        rate = market_rate(live)
        liq = live.get("liquidityNum")
        try:
            liq = number(live.get("liquidity") if liq is None else liq)
            require(liq >= 0, "gamma_liquidity_unverified")
        except (ValueError, TypeError):
            raise Blocked("gamma_liquidity_unverified") from None
        row.update(gamma_liquidity=liq, fee_rate=rate)
        if candidate is None:
            return False
        result = gates.evaluate_candidate(candidate, asks, gamma_liquidity=liq, fee_rate=rate,
                                          under_risk=risk, news=news)
        row["failed"].extend(result.decision.failed)
        if walk.filled:
            row.update(net_edge=result.exec_edge, worst_edge=result.worst_variant_edge,
                       post_haircut=result.exec_edge_haircut)
        return True
    except Blocked as error:
        row["failed"].append(str(error))
        return False
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        row["failed"].append("market_evaluation_error")
        return False


def assess(out, kickoff, news_path, inputs):
    fixture, _, fixture_ids, model_hash, market_hash = inputs
    fixture_id, lead = out["fixture_id"], out["lead"]
    started = now()
    cutoff = window(kickoff, lead, started)
    official, archives, rows = read_inputs(fixture_id, lead, fixture, kickoff, model_hash, market_hash)
    snapshot = model_snapshot(official["league"], kickoff)
    forms = [xg.recent_form(snapshot, official[side], season="2026") for side in ("home", "away")]
    hits = sum(form.over_2_5_hits for form in forms) if all(form and form.games == 5 for form in forms) else None
    verdicts, news_error = read_news(news_path, fixture_id, lead)
    news_cutoff = now()
    window(kickoff, lead, news_cutoff)
    deadline = time.monotonic() + min(45, (cutoff - news_cutoff).total_seconds())

    def budget(requests):
        return pm.FetchBudget(max_requests=requests, request_timeout_seconds=8, deadline_monotonic=deadline)

    def evaluate(row, market, archive, event_task, candidate, risk, news):
        try:
            event = event_task.result()
        except Blocked as error:
            row["failed"].append(str(error))
            return False
        return evaluate_market(row, market, archive, event, candidate, risk, news, kickoff, cutoff, budget(2))

    with ThreadPoolExecutor(max_workers=6) as pool:
        fixture_task = pool.submit(check_prematch, fixture_id, official, fixture_ids, kickoff, budget(2))
        event_tasks = {key: pool.submit(event_markets, archive, kickoff, budget(1), cutoff)
                       for key, archive in archives.items()}
        assessments = []
        for baseline, market, archive in rows:
            market_id, outcome = identity(market)
            row = {"market_id": market_id, "outcome": outcome, "label": f"{market['question']} ({outcome})",
                   "best_ask": None, "vwap250": None, "model_p": None, "net_edge": None, "worst_edge": None,
                   "passed": False, "failed": [], "book_observed_at": None,
                   "market_url": f"https://polymarket.com/event/{market['event_slug']}/{market['market_slug']}"}
            out["rows"].append(row)
            news = apply_news(row, verdicts, news_error, fixture, lead, news_cutoff)
            candidate, risk = None, False
            try:
                candidate, risk = build_candidate(snapshot, official, baseline, market, hits)
                row.update(model_p=candidate.model_median, min_variant=min(candidate.qs_variants),
                           applied_edge_floor=gates.UNDER_RISK_EXEC_EDGE_MIN if risk else gates.GATES["exec_edge_min"])
            except Blocked as error:
                row["failed"].append(str(error))
            except (ValueError, TypeError, KeyError):
                row["failed"].append("model_unavailable")
            assessments.append(pool.submit(evaluate, row, market, archive, event_tasks[market["event_id"]], candidate, risk, news))
        computed = [task.result() for task in assessments]
        fixture_task.result()

    as_of = now()
    out["as_of"] = as_of.isoformat()
    window(kickoff, lead, as_of)
    eligible = []
    verified = False
    for row, complete in zip(out["rows"], computed):
        if row["book_observed_at"] is not None and not timedelta(0) <= as_of - timestamp(row["book_observed_at"]) <= BOOK_MAX_AGE:
            row["failed"].append("clob_book_stale")
            row.update(best_ask=None, vwap250=None, net_edge=None, worst_edge=None, post_haircut=None)
            complete = False
        if complete and not row["failed"]:
            eligible.append(row)
        verified |= complete
        row["failed"] = list(dict.fromkeys(row["failed"]))
    require(verified, "no_verified_market")
    if eligible:
        chosen = max(eligible, key=lambda row: (row["post_haircut"], row["worst_edge"]))
        chosen["passed"] = True
        for row in eligible:
            if row is not chosen:
                row["failed"].append("same_fixture_other_direction_selected")
    out.update(status="ok", reason="market_entry_conditions_passed" if eligible else "no_direction_passed")


def main(argv):
    fixture_id = argv[0] if argv else ""
    lead = int(argv[1]) if len(argv) > 1 and argv[1] in ("90", "30") else None
    inputs = FIXTURES.get(fixture_id)
    kickoff = timestamp(f"2026-11-22T{inputs[1]}:00+00:00") if inputs else None
    out = {"fixture_id": fixture_id, "lead": lead, "fixture": inputs[0] if inputs else None,
           "kickoff_utc": kickoff.isoformat() if kickoff else None, "as_of": now().isoformat(),
           "status": "blocked", "reason": None, "rows": [], "paper_booked": 0,
           "paper_only": True, "report_only": True, "readonly": True}
    try:
        require(len(argv) == 3 and inputs is not None and lead is not None, "fixture_or_lead_invalid")
        assess(out, kickoff, Path(argv[2]), inputs)
    except Exception as error:
        reason = str(error) if isinstance(error, Blocked) else "assessment_internal_error"
        out.update(status="blocked", reason=reason, as_of=now().isoformat())
        for row in out["rows"]:
            row["passed"] = False
            row["failed"] = list(dict.fromkeys(row["failed"] + [reason]))
    print(json.dumps(out, allow_nan=False, separators=(",", ":")))
    return 0 if out["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
