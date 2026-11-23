"""Train and score a separate, monotone calibration of domestic match totals."""

from __future__ import annotations

from collections import defaultdict
import csv
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from hashlib import sha256
from math import exp, log
import gzip
import json
from pathlib import Path
from statistics import fmean
from zoneinfo import ZoneInfo

from polymarket_alpha_lab.papersim import soccer_model
from polymarket_alpha_lab.papersim.optimize import dataset
from polymarket_alpha_lab.papersim.pipeline import MODEL_VERSION_SOCCER

import football_feed as feed
import football_model as model


LINES = (1.5, 2.5, 3.5)
SEASON = 2026
TRAIN_END = datetime(2026, 10, 1, tzinfo=UTC)
HOLDOUT_END = datetime(2026, 11, 22, 22, tzinfo=UTC)
INDEPENDENT_SEASON = 2025
INDEPENDENT_START = datetime(2025, 10, 1, tzinfo=UTC)
INDEPENDENT_END = datetime(2026, 5, 31, tzinfo=UTC)
COMPLETION_LAG = timedelta(hours=3)
RIDGE = 12.0
EPSILON = 1e-12
VERSION_PREFIX = "domestic-totals-logit-v1-"
TAIL_CONFIG = {
    "baseline": MODEL_VERSION_SOCCER, "lines": list(LINES), "method": "league_tail",
    "blend_weight": 0.25, "half_life_days": 60, "prior_matches": 30, "completion_lag_hours": 3,
}
TAIL_VERSION_PREFIX = "domestic-totals-league-tail-v1-"
REGISTRATION_KEY = "football/domestic-totals/1"
SCHEDULE_AMENDMENT_KEY = REGISTRATION_KEY + "/schedule-amendment/1"
SCHEDULE_PRIORITY = "last_scheduled_kickoff_then_first_kickoff_then_matchweek"
ARCHIVE = Path(__file__).resolve().parents[4] / (
    "docs/paper-sim/research/league-admission-20261119/"
    "europe-football/comparison-inputs.json.gz"
)
ARCHIVE_SHA256 = "d045b252675b7246880f8bc412114c36d33e5523db33ca03db22a67ab2d7d086"
INDEPENDENT_ARCHIVE = model.SEED_ROOT / "fotmob_backfill"
BOOKMAKER_FILES = {"epl": "E0", "lal": "SP1", "sea": "I1", "bun": "D1", "fl1": "F1"}
BOOKMAKER_ZONE = ZoneInfo("Europe/London")
BOOKMAKER_BASE = "https://www.football-data.co.uk/mmz4281/2526/"
BOOKMAKER_NAMES = {
    "ath madrid": "atletico madrid", "ath bilbao": "athletic club",
    "b m gladbach": "borussia m gladbach", "celta": "celta vigo", "dortmund": "borussia dortmund",
    "ein frankfurt": "eintracht frankfurt", "espanol": "espanyol", "hamburg": "hamburger",
    "leipzig": "rasenballsport leipzig", "man united": "manchester united", "man city": "manchester city",
    "nott m forest": "nottingham forest", "st pauli": "st pauli", "vallecano": "rayo vallecano",
}
FORWARD_PROTOCOL = {
    "references": ["DELIVERY_PLAN.md §71 and §73", "docs/paper-sim/usage.md WP-P3 validation",
                   "polymarket_alpha_lab.papersim.optimize.scorecard.gate_e_market"],
    "holdout": "Two wholly unstarted official matchweeks per league with the earliest scheduled last "
               "kickoff. Ties sort by first kickoff and then official matchweek number.",
    "primary_line": 2.5,
    "secondary_lines": [1.5, 3.5],
    "forecast": "Same verified regulation contract and T-30 to T-20 snapshot as soccer-r23.",
    "market": "Normalize the simultaneous Over and Under two-sided CLOB midpoints to unit mass.",
    "settlement": "Verify the official 90-minute plus stoppage result, identity, and contract settlement.",
    "A": "Match the official fixture, clock and teams, require intact finite xG coverage and ten variants.",
    "B": "Audit the xG/goal-fit and regulation score integrity on the same league and years.",
    "C": "Audit the original team xG and realized-goal correlation in the same input population.",
    "D": "Pair Brier and log loss versus soccer-r23 by fixture and exact line, resample whole matchweeks.",
    "E": "Collect same-time binary market MAD and favorite flips per exact line. The original 6pp and "
         "zero-flip rule is defined for Championship and Eredivisie 1X2, so the binary diagnostics "
         "cannot return a formal totals admission verdict.",
    "admission": "WP-P4 requires a predeclared out-of-time sample, two completed shadow matchweeks, "
                 "Gate E and no A–D regression for the same version. This registration is research only.",
}


def _log_odds(probability):
    bounded = min(1 - EPSILON, max(EPSILON, probability))
    return log(bounded / (1 - bounded))


def _logistic(value):
    return 1 / (1 + exp(-value))


def _calibrate(probability, parameters):
    return _logistic(parameters["intercept"] + parameters["slope"] * _log_odds(probability))


def _distribution(outputs):
    return tuple(tuple(soccer_model.over_probability(variant, line) for variant in outputs) for line in LINES)


@dataclass(frozen=True)
class Match:
    league: str
    fixture_id: str
    block_id: str
    cutoff: datetime
    kickoff: datetime
    block_available_at: datetime
    names: tuple[str, str]
    score: tuple[int, int]
    variant_overs: tuple[tuple[float, ...], ...]
    wp_p4_overs: dict
    league_overs: tuple[float, ...] = ()

    def base(self, line):
        return soccer_model.median(self.variant_overs[LINES.index(line)])

    def candidate(self, line, parameters):
        return soccer_model.median([
            _calibrate(p, parameters) for p in self.variant_overs[LINES.index(line)]
        ])

    def league_tail(self, line):
        index = LINES.index(line)
        return soccer_model.median([_tail_blend(p, self.league_overs[index]) for p in self.variant_overs[index]])


def _tail_blend(probability, league_probability):
    weight = TAIL_CONFIG["blend_weight"]
    return (1 - weight) * probability + weight * league_probability


def league_tail_inputs(competition, current, prior, as_of):
    """Smooth recent regulation goal tails toward the complete prior season."""
    safe = as_of - COMPLETION_LAG
    seasons = []
    for block in (prior, current):
        complete = model._finished(feed.record(block.get("calendar")), safe)
        results = feed.record(block.get("results"))
        seasons.append([(mid, row["kickoff"], sum(result[2:]))
                        for mid, row in sorted(complete.items())
                        if (result := model._known_result(results.get(mid), row)) is not None])
    previous, recent = seasons
    if len(previous) != competition.matches_per_season:
        return None
    half_life = timedelta(days=TAIL_CONFIG["half_life_days"])
    prior_weight = TAIL_CONFIG["prior_matches"]
    weighted = [(2 ** (-(as_of - feed.stamp(at)) / half_life), goals) for _, at, goals in recent]
    effective = sum(weight for weight, _ in weighted)
    overs = [(prior_weight * fmean(goals > line for _, _, goals in previous)
              + sum(weight for weight, goals in weighted if goals > line)) / (prior_weight + effective)
             for line in LINES]
    return {"overs": overs, "recent_matches": len(recent), "prior_matches": len(previous),
            "sha256": feed.document_digest([feed.utc_iso(as_of), previous, recent])}


def replay(league, audit, source_cache, wp_p4_rows, now, *, season_start=SEASON, start=None,
           end=HOLDOUT_END, training_split=TRAIN_END):
    """Rebuild production inputs before the first kickoff of each complete round."""
    competition = model.COMPETITIONS[league]
    label, season = competition.model_league, str(season_start)
    seasons = feed.record(feed.record(source_cache).get("seasons"))
    current = feed.record(seasons.get(f"{league}:{season_start}"))
    previous = feed.record(seasons.get(f"{league}:{season_start - 1}"))
    calendar = feed.record(current.get("calendar"))
    prior = tuple(m for m in audit.matches if m.season == str(season_start - 1))
    if not dataset._complete_season(audit, label, str(season_start - 1), prior):
        return ()
    prior_by_id = {m.source_match_id: [m.home_xg, m.away_xg, m.home_goals, m.away_goals] for m in prior}
    prior_block = previous | {"results": prior_by_id}
    prior_ready = max(m.kickoff for m in prior) + COMPLETION_LAG
    fixtures, results, complete = dataset._round_rows(audit, label, season)
    history = {m.schedule_match_id: m for m in audit.matches if m.season == season}
    seasons_finished = [(mid, row, history[mid].kickoff if mid in history else feed.stamp(row["kickoff"]))
                        for mid, row in calendar.items() if row["state"] == "finished"]
    wp_p4 = {row[0]: row for row in wp_p4_rows if "recalibrated" in row[5]}
    rows = []
    for matchweek in sorted(fixtures):
        if matchweek < 3 or not complete[matchweek]:
            continue
        block_id = f"{label}:{season}:round-{matchweek}"
        matches = results[matchweek]
        available_at = max(m.kickoff for m in matches) + COMPLETION_LAG
        if available_at >= min(end, now):
            continue
        cutoff = min(min(f.kickoff for f in fixtures[matchweek]),
                     min(m.source_kickoff or m.kickoff for m in matches))
        if prior_ready >= cutoff or (start is not None and cutoff < start):
            continue
        if training_split is not None and cutoff < training_split <= available_at:
            continue
        # Match the deployed fixture model, including its requirement for xG
        # on every prior completed match involving either team. The historical
        # archive lacks publication timestamps, so allow three hours after the
        # later of the two independently recorded kickoff clocks.
        observed = {mid: row for mid, row, at in seasons_finished
                    if at is not None and at + COMPLETION_LAG < cutoff}
        evidence = {mid: [game.home_xg, game.away_xg, game.home_goals, game.away_goals]
                    for mid in observed if (game := history.get(mid)) is not None}
        at_cutoff = current | {"calendar": observed, "results": evidence}
        tail = league_tail_inputs(competition, at_cutoff, prior_block, cutoff)
        block = []
        for m in sorted(matches, key=lambda item: (item.kickoff, item.match_id)):
            mid = m.schedule_match_id
            official = calendar[mid]
            kickoff = feed.stamp(official["kickoff"])
            data = model._fixture_model({"fixture_id": mid, "kickoff": official["kickoff"]}, official,
                                        (season_start, competition), at_cutoff, prior_block, cutoff)
            if data is None:
                break
            outputs = [soccer_model.variant_outputs(*pair, rho=spec.rho)
                       for pair, spec in zip(data["lambdas"], soccer_model.SWEEPS, strict=True)]
            archived = wp_p4.get(mid)
            score = (m.home_goals, m.away_goals)
            comparison = {}
            if archived and tuple(archived[2:4]) == score and archived[4] == block_id:
                comparison = archived[5]["recalibrated"][1]
            block.append(Match(league, mid, block_id, cutoff, kickoff, available_at,
                               (official["home"], official["away"]), score, _distribution(outputs), comparison,
                               tuple(tail["overs"]) if tail else ()))
        else:
            rows.extend(block)
    return tuple(rows)


def _coordinate(values, intercept, slope, *, fit_slope):
    low, high = (0.25, 2.0) if fit_slope else (-2.0, 2.0)
    for _ in range(32):
        mid = (low + high) / 2
        if fit_slope:
            gradient = RIDGE * (mid - 1) + sum(
                (_logistic(intercept + mid * odd) - target) * odd for odd, target in values
            ) / len(LINES)
        else:
            gradient = RIDGE * mid + sum(
                _logistic(mid + slope * odd) - target for odd, target in values
            ) / len(LINES)
        if gradient > 0:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def _version(parameters):
    return VERSION_PREFIX + feed.document_digest(parameters)[:16]


def fit(rows):
    """Learn a regularized logit intercept and positive slope on the early cohort."""
    ids = {(row.league, row.fixture_id) for row in rows}
    if len(ids) != len(rows) or any(row.block_available_at >= TRAIN_END for row in rows):
        raise ValueError("the calibration fit requires distinct pre-holdout fixtures")
    if len(rows) < 60 or len({row.block_id for row in rows}) < 6:
        return None
    training = sorted(rows, key=lambda row: (row.cutoff, row.league, row.fixture_id))
    values = [(_log_odds(row.base(line)), sum(row.score) > line) for row in training for line in LINES]
    intercept, slope = 0.0, 1.0
    for _ in range(25):
        updated = _coordinate(values, intercept, slope, fit_slope=False)
        next_slope = _coordinate(values, updated, slope, fit_slope=True)
        if abs(updated - intercept) + abs(next_slope - slope) < 1e-8:
            intercept, slope = updated, next_slope
            break
        intercept, slope = updated, next_slope
    freeze = feed.document_digest([
        (row.league, row.fixture_id, row.block_id, feed.utc_iso(row.cutoff),
         feed.utc_iso(row.block_available_at), row.score, row.variant_overs)
        for row in training
    ])
    config = {"training_sha256": freeze, "season": SEASON, "ridge": RIDGE, "intercept": intercept,
              "slope": slope, "baseline": MODEL_VERSION_SOCCER, "lines": list(LINES)}
    return {"model_version": _version(config), "parameters": config}


@lru_cache(maxsize=1)
def _historical_market():
    try:
        archive = ARCHIVE.read_bytes()
    except OSError:
        return {}
    if sha256(archive).hexdigest() != ARCHIVE_SHA256:
        return {}
    by_key = defaultdict(list)
    labels = {spec.model_league: code for code, spec in model.COMPETITIONS.items()}
    for row in json.loads(gzip.decompress(archive))["fixtures"]:
        market = row["markets"].get("O2.5", {})
        if (row["timing"] != "eligible" or market.get("status") != "ok"
                or market.get("settlement") != "90min+stoppage" or market.get("label") != "Over"):
            continue
        kickoff = feed.stamp(row["kickoff"])
        if kickoff is None or row["league"] not in labels or not 0 < market["p"] < 1:
            continue
        price_at = datetime.fromtimestamp(market["t"], tz=UTC)
        key = (labels[row["league"]], kickoff, feed.team_key(row["home_team"]), feed.team_key(row["away_team"]))
        by_key[key].append((tuple(row["score"]), price_at, market["p"]))
    return {key: values[0] for key, values in by_key.items() if len(values) == 1}


def _market_price(row, market):
    key = (row.league, row.kickoff, *(feed.team_key(name) for name in row.names))
    quote = market.get(key)
    target = row.kickoff - timedelta(minutes=60)
    if quote and quote[0] == row.score and max(row.cutoff, target - timedelta(minutes=10)) <= quote[1] <= target:
        return quote[2]
    return None


def _score(probability, over):
    expected = probability if over else 1 - probability
    return {"log_loss": -log(max(EPSILON, expected)), "brier": (probability - over) ** 2}


def _sample(rows):
    return {"matches": len(rows), "matchweeks": len({row.block_id for row in rows}),
            "first_kickoff": feed.utc_iso(min(row.kickoff for row in rows)) if rows else None,
            "last_kickoff": feed.utc_iso(max(row.kickoff for row in rows)) if rows else None}


def _paired(rows, comparisons, paired_interval):
    summaries = {}
    for comparator in comparisons:
        sample = [(row, scores) for row, scores in rows if comparator in scores]
        result = _sample([row for row, _ in sample])
        if sample:
            names = ("calibrated", comparator)
            actual = [{"block_id": row.block_id, "forecasts": {name: scores[name] for name in names}}
                      for row, scores in sample]
            result["mean"] = {
                name: {metric: fmean(scores[name][metric] for _, scores in sample)
                       for metric in ("log_loss", "brier")} for name in names
            }
            result["calibrated_minus_comparator"] = {
                metric: paired_interval(actual, *names, metric) for metric in ("log_loss", "brier")
            }
        summaries[comparator] = result
    return summaries


def _comparisons(held, candidate, paired_interval, *, market_name, market_price, include_rows):
    comparisons, evidence = {}, []
    for line in LINES:
        scored = []
        for row in sorted(held, key=lambda item: (item.kickoff, item.league, item.fixture_id)):
            probability = {"production10": row.base(line), "calibrated": candidate(row, line)}
            wp = row.wp_p4_overs.get(str(line))
            if wp is not None:
                probability["wp_p4"] = wp
            if line == 2.5 and (quote := market_price(row)) is not None:
                probability[market_name] = quote
            over = sum(row.score) > line
            scored.append((row, {name: _score(value, over) for name, value in probability.items()}))
            if include_rows:
                evidence.append({"league": row.league, "fixture_id": row.fixture_id, "block_id": row.block_id,
                                 "cutoff": feed.utc_iso(row.cutoff), "kickoff": feed.utc_iso(row.kickoff),
                                 "score": row.score, "line": line, "probabilities": probability})
        names = ["production10"]
        if any(row.wp_p4_overs for row in held):
            names.append("wp_p4")
        if line == 2.5:
            names.append(market_name)
        comparisons[str(line)] = {
            "pooled": _paired(scored, names, paired_interval),
            "leagues": {league: _paired([(row, losses) for row, losses in scored if row.league == league],
                                        names, paired_interval) for league in model.COMPETITIONS},
        }
    result = {"comparisons": comparisons}
    if include_rows:
        result["holdout_rows"] = evidence
    return result


def research(rows, paired_interval, *, frozen=None, include_rows=False):
    """Score the fixed time holdout with one matched probability per game and line."""
    training = [row for row in rows if row.block_available_at < TRAIN_END]
    held = [row for row in rows if TRAIN_END <= row.cutoff and row.block_available_at < HOLDOUT_END]
    trial = frozen or fit(training)
    result = {
        "method": "ridge_logit_of_production_total_probabilities",
        "information_mode": "event_time_replay",
        "completion_lag_hours": 3,
        "training_cutoff_exclusive": feed.utc_iso(TRAIN_END),
        "holdout_round_completion_cutoff_exclusive": feed.utc_iso(HOLDOUT_END),
        "primary_line": "2.5",
        "archive_sha256": ARCHIVE_SHA256,
        "market_definition": "original Polymarket Over 2.5 history at T-60, at most ten minutes old",
        "training": _sample(training),
        "holdout": _sample(held),
        "model": trial,
    }
    if not trial:
        return result | {"status": "insufficient_training_fixtures"}
    market = _historical_market()
    metrics = _comparisons(held, lambda row, line: row.candidate(line, trial["parameters"]), paired_interval,
                           market_name="polymarket_t60", market_price=lambda row: _market_price(row, market),
                           include_rows=include_rows)
    primary = metrics["comparisons"]["2.5"]["pooled"]["production10"].get("calibrated_minus_comparator", {})
    status = "historical_shadow"
    if primary and any(primary[metric]["mean_delta"] > 0 for metric in ("log_loss", "brier")):
        status = "retrospective_weaker_than_production"
    return result | {"status": status} | metrics


def archived_history(now):
    """Load the earlier, independent season from the existing FotMob research archive."""
    base = INDEPENDENT_ARCHIVE
    years = (INDEPENDENT_SEASON - 1, INDEPENDENT_SEASON)
    desired = {f"{model.COMPETITIONS[code].model_league}_{year}" for code in BOOKMAKER_FILES for year in years}
    seeds = model._seed_rows((base / f"big5_{year}.json" for year in years), desired)
    seasons, provenance = {}, {}
    for code in BOOKMAKER_FILES:
        competition = model.COMPETITIONS[code]
        for year in years:
            path = base / "cache" / "fixtures" / f"leagues_{competition.fotmob_id}_{year}.json"
            seed = seeds.get(f"{competition.model_league}_{year}")
            if seed is None:
                continue
            try:
                raw = path.read_bytes()
                digest = sha256(raw).hexdigest()
                block = model._calendar_payload(competition, year, json.loads(raw), digest, now)
            except (OSError, ValueError, TypeError):
                continue
            matched = {}
            model._seed_verified(seed[1], block["calendar"], matched)
            seasons[f"{code}:{year}"] = block | {"results": matched, "seed_sha256": seed[0]}
            provenance[f"{code}:{year}"] = {"calendar_path": str(path), "calendar_sha256": digest,
                                            "xg_path": str(base / f"big5_{year}.json"), "xg_sha256": seed[0],
                                            "xg_matches": len(matched)}
    return {"version": 1, "seasons": seasons}, seeds, provenance


def _book_name(value):
    name = feed.team_key(value)
    return BOOKMAKER_NAMES.get(name, name)


def _book_key(league, kickoff, home, away, score):
    return (league, kickoff, _book_name(home), _book_name(away), score)


def bookmaker_market(directory):
    market, sources = defaultdict(list), {}
    if directory is None:
        return {}, {}
    for code, div in BOOKMAKER_FILES.items():
        filename = div + ".csv"
        try:
            content = (directory / filename).read_bytes()
            reader = csv.DictReader(content.decode("utf-8-sig").splitlines())
            for row in reader:
                if row["Div"] != div:
                    continue
                at = datetime.strptime(row["Date"] + " " + row["Time"], "%d/%m/%Y %H:%M")
                at = at.replace(tzinfo=BOOKMAKER_ZONE).astimezone(UTC)
                score = (int(row["FTHG"]), int(row["FTAG"]))
                over, under = (feed.number(row[name]) for name in ("AvgC>2.5", "AvgC<2.5"))
                if over is not None and under is not None and over > 1 and under > 1:
                    key = _book_key(code, at, row["HomeTeam"], row["AwayTeam"], score)
                    market[key].append(under / (over + under))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        sources[code] = {"url": BOOKMAKER_BASE + filename, "sha256": sha256(content).hexdigest()}
    return {key: quotes[0] for key, quotes in market.items()
            if key[0] in sources and len(quotes) == 1}, sources


def research_tail(rows, independent_rows, paired_interval, *, sources, book_dir=None, include_rows=False):
    independent = [row for row in independent_rows if row.league_overs]
    recent = [row for row in rows if row.league_overs and TRAIN_END <= row.cutoff]
    market = _historical_market()
    book, book_sources = bookmaker_market(book_dir)
    comparison = _comparisons(independent, Match.league_tail, paired_interval, market_name="bookmaker_close",
                              market_price=lambda row: book.get(_book_key(row.league, row.kickoff,
                                                                      *row.names, row.score)),
                              include_rows=include_rows)
    recent_comparison = _comparisons(recent, Match.league_tail, paired_interval, market_name="polymarket_t60",
                                     market_price=lambda row: _market_price(row, market),
                                     include_rows=include_rows)
    return {
        "model": {"model_version": _tail_version(), "parameters": TAIL_CONFIG}, "primary_line": "2.5",
        "information_mode": "event_time_replay", "independent_season": INDEPENDENT_SEASON,
        "independent_start": feed.utc_iso(INDEPENDENT_START), "independent_end": feed.utc_iso(INDEPENDENT_END),
        "independent_sample": _sample(independent), "independent_sources": sources,
        "market_sources": book_sources,
        "market_definition": "football-data.co.uk bookmaker average closing implied over probability, vig-normalized",
        "independent_scores": comparison, "recent_exploratory_sample": _sample(recent),
        "recent_exploratory_scores": recent_comparison,
    }


def _read_document(connection, key):
    row = connection.execute("SELECT payload FROM football_notify.source_cache WHERE cache_key=%s",
                             (key,)).fetchone()
    return row["payload"] if row else None


def read_registered(connection):
    registration = _read_document(connection, REGISTRATION_KEY)
    if registration is None:
        return None
    amendment = _read_document(connection, SCHEDULE_AMENDMENT_KEY)
    if amendment is None:
        return registration
    published = feed.stamp(amendment.get("amended_at"))
    registered_at = feed.stamp(registration.get("registered_at"))
    opens = feed.stamp(amendment.get("earliest_planned_capture_opens"))
    if (amendment.get("model_version") != registration["model_version"]
            or amendment.get("initial_registration_sha256") != feed.document_digest(registration)
            or amendment.get("sha256") != feed.document_digest({k: v for k, v in amendment.items() if k != "sha256"})
            or not all((published, registered_at, opens)) or not registered_at <= published < opens):
        return registration
    return registration | {
        "planned_matchweeks": amendment["planned_matchweeks"],
        "protocol": registration["protocol"] | {"holdout": amendment["holdout"]},
        "schedule_amendment": amendment,
    }


def _tail_version():
    return TAIL_VERSION_PREFIX + feed.document_digest(TAIL_CONFIG)[:16]


def _planned_rounds(source_cache, now):
    current = model._season(now)
    seasons = feed.record(feed.record(source_cache).get("seasons"))
    studies = {}
    for code, competition in model.COMPETITIONS.items():
        block = feed.record(seasons.get(f"{code}:{current}"))
        calendar = feed.record(block.get("calendar"))
        by_round = defaultdict(list)
        for fixture_id, row in calendar.items():
            by_round[row["round"]].append((fixture_id, row))
        required = competition.matches_per_season // competition.rounds
        eligible = []
        for number, rows in by_round.items():
            teams = {row[f"{name}_id"] for _, row in rows for name in ("home", "away")}
            if len(rows) != required or len(teams) != required * 2:
                continue
            fixtures = []
            for fixture_id, row in rows:
                kickoff = feed.stamp(row["kickoff"])
                if row["state"] != "scheduled" or kickoff is None or kickoff - timedelta(minutes=30) <= now:
                    break
                fixtures.append({"fixture_id": fixture_id, "kickoff": row["kickoff"],
                                 "team_ids": [row["home_id"], row["away_id"]]})
            else:
                fixtures.sort(key=lambda row: (row["kickoff"], row["fixture_id"]))
                eligible.append({"matchweek": number, "fixtures": fixtures})
        eligible.sort(key=lambda round_: (round_["fixtures"][-1]["kickoff"], round_["fixtures"][0]["kickoff"],
                                          round_["matchweek"]))
        studies[code] = eligible[:2]
    return studies


def register(connection, source_cache, report, now):
    """Freeze one research version and its prospective matchweeks without changing a daily study."""
    from psycopg.types.json import Jsonb

    registered = read_registered(connection)
    if registered is not None:
        return registered
    candidate = report["model"]
    if candidate != {"model_version": _tail_version(), "parameters": TAIL_CONFIG}:
        raise ValueError("only the fixed league-tail model is eligible for forward research")
    independent = report["independent_scores"]["comparisons"]["2.5"]["pooled"]["production10"]
    deltas = independent.get("calibrated_minus_comparator", {})
    if not deltas or any(deltas[metric]["mean_delta"] >= 0 for metric in ("log_loss", "brier")):
        raise ValueError("the independent totals study lacks an improvement in both primary scores")
    evidence = report | {
        name: {key: item for key, item in report[name].items() if key != "holdout_rows"}
        for name in ("independent_scores", "recent_exploratory_scores")
    }
    registration = candidate | {
        "status": "forward_shadow", "registered_at": feed.utc_iso(now), "season": model._season(now),
        "planned_matchweeks": _planned_rounds(source_cache, now),
        "protocol": FORWARD_PROTOCOL,
        "historic_evidence_sha256": feed.document_digest(report), "historical_evidence": evidence,
    }
    created = connection.execute(
        """INSERT INTO football_notify.source_cache (cache_key,payload) VALUES (%s,%s)
           ON CONFLICT (cache_key) DO NOTHING RETURNING payload""",
        (REGISTRATION_KEY, Jsonb(registration)),
    ).fetchone()
    return created["payload"] if created else read_registered(connection)


def amend_schedule(connection):
    """Append a schedule-only correction while observation and both plans are still in the future."""
    from psycopg.types.json import Jsonb

    from football_forward import COLLECT_LOCK
    import football_store as store

    with connection.transaction():
        locked = connection.execute("SELECT pg_try_advisory_xact_lock(%s,%s) AS locked",
                                    (store.LOCK_NAMESPACE, COLLECT_LOCK)).fetchone()["locked"]
        if not locked:
            raise ValueError("forward collection in progress")
        prior = _read_document(connection, SCHEDULE_AMENDMENT_KEY)
        if prior is not None:
            return prior
        registration = _read_document(connection, REGISTRATION_KEY)
        if registration is None:
            raise ValueError("no registered totals model")
        version = registration["model_version"]
        observations = connection.execute(
            "SELECT count(*) AS count FROM football_notify.football_forward_observation WHERE model_version=%s",
            (version,),
        ).fetchone()["count"]
        now = store.db_now(connection)
        source_cache = store.source_cache(connection)
        planned = _planned_rounds(source_cache, now)
        previous = registration["planned_matchweeks"]
        clocks = [feed.stamp(fixture["kickoff"]) for plan in (previous, planned)
                  for rounds in plan.values() for round_ in rounds for fixture in round_["fixtures"]]
        if observations or not clocks or min(clocks) - timedelta(minutes=30) <= now:
            raise ValueError("totals schedule is frozen after the first observation or planned opening")
        if any(len(rounds) != 2 for rounds in planned.values()):
            raise ValueError("two scheduled official matchweeks per league are required")
        season = registration["season"]
        calendars = feed.record(source_cache.get("seasons"))
        sources = {code: {key: calendars[f"{code}:{season}"][key]
                          for key in ("calendar_at", "calendar_sha256", "source")} for code in planned}
        amendment = {
            "model_version": version, "amended_at": feed.utc_iso(now), "selection_rule": SCHEDULE_PRIORITY,
            "holdout": FORWARD_PROTOCOL["holdout"], "prior_observations": observations,
            "initial_registration_sha256": feed.document_digest(registration),
            "earliest_planned_capture_opens": feed.utc_iso(min(clocks) - timedelta(minutes=30)),
            "reason": "Sort by projected round completion because early or split fixtures can put the "
                      "first kickoff of a widely dispersed matchweek ahead of ordinarily scheduled rounds.",
            "source_calendars": sources, "planned_matchweeks": planned,
        }
        amendment["sha256"] = feed.document_digest(amendment)
        connection.execute("INSERT INTO football_notify.source_cache (cache_key,payload) VALUES (%s,%s)",
                           (SCHEDULE_AMENDMENT_KEY, Jsonb(amendment)))
        return amendment


def forward_progress(connection, registration, now, paired_interval):
    """Score immutable forward observations from the registered exact fixture plan."""
    from polymarket_alpha_lab.papersim.optimize import scorecard

    planned = registration["planned_matchweeks"]
    registered_at = feed.stamp(registration["registered_at"])
    amendment = feed.record(registration.get("schedule_amendment"))
    effective_at = feed.stamp(amendment.get("amended_at")) or registered_at
    expected = {(league, item["fixture_id"]): (round_["matchweek"], item)
                for league, rounds in planned.items() for round_ in rounds for item in round_["fixtures"]}
    rows = connection.execute(
        """SELECT c.league,c.fixture_id,c.team_ids,c.kickoff,c.line,c.side,c.matchweek,
                  o.market_id,o.fixture_revision,o.observed_at,o.model_observed_at,o.book_observed_at,
                  o.model_probability,o.baseline_probability,o.market_price,o.market_price_basis,
                  o.bid_price,o.ask_price,o.evidence,
                  r.state AS result_state,r.home_goals,r.away_goals,r.won,r.observed_at AS result_at
           FROM football_notify.football_forward_observation o
           JOIN football_notify.football_forward_contract c
                USING (fixture_key,fixture_revision,market_id,outcome,kickoff)
           LEFT JOIN football_notify.football_forward_result r
                USING (fixture_key,fixture_revision,market_id,outcome,model_version,kickoff)
           WHERE o.model_version=%s AND o.cohort='shadow' AND c.market_kind='total'
                 AND c.team_side IS NULL AND c.rule='regulation_90_plus_stoppage'
                 AND c.fixture_id=ANY(%s) AND %s <= o.observed_at AND o.observed_at<=%s
                 AND NOT o.reason_codes && ARRAY['direct_contract_unverified','independent_fixture_unverified']""",
        (registration["model_version"], sorted({fixture for _, fixture in expected}), effective_at, now),
    ).fetchall()
    pairs, scopes = defaultdict(list), defaultdict(dict)
    for row in rows:
        fixture = expected.get((row["league"], row["fixture_id"]))
        research = feed.record(row["evidence"].get("research"))
        if (fixture is None or row["line"] not in LINES or row["side"] not in ("over", "under")
                or row["matchweek"] != fixture[0] or list(row["team_ids"]) != fixture[1]["team_ids"]
                or row["kickoff"] != feed.stamp(fixture[1]["kickoff"])
                or row["evidence"].get("direct_contract_verified") is not True
                or research.get("baseline_model_version") != MODEL_VERSION_SOCCER
                or research.get("registered_at") != registration["registered_at"]
                or research.get("schedule_amendment_sha256") != amendment.get("sha256")
                or row["model_observed_at"] is None or not registered_at <= row["model_observed_at"] <= row["observed_at"]
                or not row["kickoff"] - timedelta(minutes=30) <= row["observed_at"] < row["kickoff"] - timedelta(minutes=20)):
            continue
        key = (row["league"], row["fixture_id"], row["line"])
        pairs[key].append(row)
    for (league, fixture_id, line), legs in pairs.items():
        if len(legs) != 2 or {row["side"] for row in legs} != {"over", "under"}:
            continue
        over, under = sorted(legs, key=lambda row: row["side"])
        if any(over[field] != under[field] for field in ("market_id", "fixture_revision", "observed_at",
                                                        "model_observed_at", "matchweek")):
            continue
        candidate, baseline = over["model_probability"], over["baseline_probability"]
        if (None in (candidate, baseline, under["model_probability"], under["baseline_probability"])
                or abs(candidate + under["model_probability"] - 1) > EPSILON
                or abs(baseline + under["baseline_probability"] - 1) > EPSILON):
            continue
        market = None
        if all(row["market_price_basis"] == "clob_midpoint" and row["book_observed_at"] is not None
               and None not in (row["bid_price"], row["ask_price"], row["market_price"])
               and 0 < row["bid_price"] < row["ask_price"] < 1
               and abs((row["bid_price"] + row["ask_price"]) / 2 - row["market_price"]) <= EPSILON
               and timedelta(0) <= row["observed_at"] - row["book_observed_at"] <= feed.BOOK_AGE for row in legs):
            market = over["market_price"] / (over["market_price"] + under["market_price"])
        scores = None
        if (all(row["result_state"] == "resolved" and row["kickoff"] < row["result_at"] <= now for row in legs)
                and (over["home_goals"], over["away_goals"]) == (under["home_goals"], under["away_goals"])
                and over["home_goals"] is not None and over["away_goals"] is not None
                and over["won"] is (over["home_goals"] + over["away_goals"] > line)
                and under["won"] is (not over["won"])):
            probabilities = {"calibrated": candidate, "production10": baseline}
            if market is not None:
                probabilities["polymarket_live"] = market
            scores = {"block_id": f"{league}:{over['matchweek']}",
                      "forecasts": {key: _score(value, over["won"]) for key, value in probabilities.items()}}
        scopes[league, line][fixture_id] = (candidate, market, scores)
    reports = {}
    for league, rounds in planned.items():
        reports[league] = {}
        for line in LINES:
            observations = scopes[league, line]
            scored = [record[2] for record in observations.values() if record[2] is not None]
            market = [(candidate, price) for candidate, price, _ in observations.values() if price is not None]
            required = sum(len(round_["fixtures"]) for round_ in rounds)
            distinction = scorecard.gate_e_market(
                [(p, 1 - p) for p, _ in market], [(q, 1 - q) for _, q in market],
                mad_max=0.06, flips_max=0, min_events=required,
            ) if required else None
            reports[league][str(line)] = {
                "expected_fixtures": required, "captured_pairs": len(observations), "market_pairs": len(market),
                "scored_fixtures": len(scored),
                "complete_matchweeks": sum(all(
                    item["fixture_id"] in observations and observations[item["fixture_id"]][2] is not None
                    for item in round_["fixtures"]) for round_ in rounds),
                "paired_deltas": {opponent: {metric: paired_interval(scored, "calibrated", opponent, metric)
                                            for metric in ("log_loss", "brier")}
                                  for opponent in ("production10", "polymarket_live")},
                "binary_market_diagnostic": dict(distinction.numbers) if distinction else {},
                "formal_admission": "shadow_pending_totals_scope_validation",
            }
    return {"model_version": registration["model_version"], "registered_at": registration["registered_at"],
            "protocol": registration["protocol"], "schedule_amendment": amendment, "leagues": reports}


@dataclass(frozen=True)
class TotalsShadow:
    model_version: str
    model_observed_at: datetime
    projection: tuple
    evidence: dict
    league_overs: tuple[float, ...]

    @staticmethod
    def _supported(market, option):
        return (market.kind == "total" and market.team_side is None and market.line in LINES
                and option.side in ("over", "under"))

    def baseline_quote(self, market, option):
        if self._supported(market, option):
            return feed._model_quote(market, option, self.projection[1])
        return None

    def market_quote(self, market, option):
        if not self._supported(market, option):
            return None
        league_probability = self.league_overs[LINES.index(market.line)]
        overs = tuple(_tail_blend(soccer_model.over_probability(variant, market.line), league_probability)
                      for variant in self.projection[1])
        values = tuple(1 - p for p in overs) if option.side == "under" else overs
        return {"model_median": soccer_model.median(values), "qs_variants": values}


def make_shadow_context(info, now, registration, *, projection=None):
    """Return a dedicated forecast limited to the registered full-match goal lines."""
    if info.get("league") not in model.COMPETITIONS or info.get("state") != "scheduled":
        return None
    registration = feed.record(registration)
    version = registration.get("model_version")
    registered = feed.stamp(registration.get("registered_at"))
    kickoff = feed.stamp(info.get("kickoff"))
    if (registered is None or kickoff is None or not registered <= now < kickoff
            or version != _tail_version() or registration.get("parameters") != TAIL_CONFIG
            or registration.get("status") != "forward_shadow" or registration.get("season") != model._season(kickoff)):
        return None
    if projection is None:
        projection = feed._model_projection(info, now)
    if projection is None:
        return None
    data = projection[0]
    totals = feed.record(data.get("league_totals"))
    observed = feed.stamp(totals.get("observed_at"))
    overs = totals.get("overs")
    if (observed is None or not timedelta(0) <= now - observed <= model.MODEL_AGE
            or not isinstance(overs, list) or len(overs) != len(LINES)
            or not isinstance(totals.get("sha256"), str) or len(totals["sha256"]) != 64):
        return None
    overs = tuple(feed.number(value) for value in overs)
    if any(value is None or not 0 <= value <= 1 for value in overs) or list(overs) != sorted(overs, reverse=True):
        return None
    observed_at = max(observed, registered)
    model_digest = feed.document_digest({"production_input": data["sha256"], "model_version": version,
                                         "league_input": totals["sha256"]})
    evidence = {"sha256": model_digest, "registered_at": registration["registered_at"],
                "league_input_sha256": totals["sha256"], "calendar_source": data["source"],
                "baseline_model_version": MODEL_VERSION_SOCCER, "registered_season": registration["season"],
                "prior_matches": totals.get("prior_matches"), "recent_matches": totals.get("recent_matches")}
    amendment = feed.record(registration.get("schedule_amendment"))
    if amendment:
        evidence["schedule_amendment_sha256"] = amendment["sha256"]
    shadow_projection = (data | {"sha256": model_digest}, *projection[1:])
    return TotalsShadow(version, observed_at, shadow_projection, evidence, overs)
