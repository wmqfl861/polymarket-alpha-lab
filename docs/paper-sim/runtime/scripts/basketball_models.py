"""Run the registered NBA and European basketball research models."""

from __future__ import annotations

from collections import defaultdict, deque
import csv
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
import gzip
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
import math
from pathlib import Path
from statistics import fmean, median
import tarfile
from zipfile import ZipFile
from zoneinfo import ZoneInfo

from polymarket_alpha_lab.papersim import espn_nba, nba_model

import basketball_nba_ratings as ratings
from football_feed import document_digest, number, record, stamp, utc_iso


PROJECT = Path(__file__).resolve().parents[4]
ARCHIVE = PROJECT / "docs/paper-sim/research/league-admission-20261119"
ET = ZoneInfo("America/New_York")
NBA_VERSION = "wp-nba-v1"
NBA_RATINGS_VERSION = ratings.VERSION
EURO_VERSION = "europe-basketball-20261119"
NBA_RETEST_GAMES = 90
NBA_PRIOR_GAMES = 8
EURO_LEAGUES = {
    "euroleague": "EuroLeague", "bkeurocup": "EuroCup", "bkcl": "Basketball Champions League",
    "bkligend": "Spain ACB", "bkbbl": "Germany BBL", "bkseriea": "Italy Serie A",
    "bkfr1": "France Pro A", "bkfr2": "France Pro B", "bkvtb": "VTB United League",
}
EURO_CODES = {"euroleague": "E", "bkeurocup": "U"}
EURO_OT_RESULT_DELAY = timedelta(hours=6)


def nba_season(now):
    current = now.astimezone(ET).date()
    start = current.year if current.month >= 7 else current.year - 1
    return f"{start}-{(start + 1) % 100:02}"


@lru_cache(maxsize=1)
def _wp():
    path = PROJECT / "tools/wp_nba_validate.py"
    spec = spec_from_file_location("basketball_wp_nba_validate", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_nba_archive(prefix, season):
    path = ARCHIVE / "nba" / f"{prefix}-{season}.json.gz"
    if not path.is_file():
        return {}
    with gzip.open(path, mode="rt", encoding="utf-8") as source:
        return json.load(source)


@lru_cache(maxsize=3)
def _archived_nba_games(season):
    return _read_nba_archive("games", season).get("games", [])


@lru_cache(maxsize=2)
def nba_archive(season):
    pm = _read_nba_archive("pm", season)
    return _archived_nba_games(season), {row["event_id"]: row["quotes"] for row in pm.get("rows", [])}


def nba_ratings_history(games, season, previous=None):
    year = int(season.split("-")[0])
    earlier = []
    for start in range(year - 2, year):
        key = f"{start}-{(start + 1) % 100:02}"
        archived = _archived_nba_games(key)
        retained = record(record(previous).get(key))
        merged = {game["event_id"]: game for game in archived} | retained
        earlier.extend(merged.values())
    return ratings.prepare(earlier + list(games))


def nba_summary(payload, fixture):
    """Preserve exact possession inputs and reconcile scoreboard with the box score."""
    header = record(payload.get("header"))
    competitions = header.get("competitions")
    if (str(header.get("id")) != fixture["id"] or not isinstance(competitions, list)
            or len(competitions) != 1):
        raise ValueError("summary_identity")
    competition = competitions[0]
    status = record(record(competition.get("status")).get("type"))
    if (str(competition.get("id")) != fixture["id"] or stamp(competition.get("date")) != stamp(fixture["kickoff"])
            or status.get("completed") is not True or status.get("state") != "post"
            or status.get("name") != "STATUS_FINAL"):
        raise ValueError("summary_status_or_clock")
    if record(header.get("season")).get("type") != 2:
        raise ValueError("non_regular_season")
    ids = {}
    for item in competition.get("competitors", []):
        side, team = item.get("homeAway"), record(item.get("team"))
        if side in ids or side not in {"home", "away"}:
            raise ValueError("summary_team_orientation")
        ids[side] = str(team.get("id")), team.get("abbreviation"), number(item.get("score"))
    if any(ids.get(side) != (fixture[f"{side}_id"], fixture[f"{side}_code"], fixture[f"{side}_score"])
           for side in ("home", "away")):
        raise ValueError("summary_score_conflict")
    tip = stamp(fixture["kickoff"])
    result = {
        "date": tip.astimezone(ET).date().isoformat(), "event_id": fixture["id"],
        "started": utc_iso(tip), "neutral": fixture["neutral"], "dk_proxy": dk_close(payload),
        "overtime": fixture["overtime"],
    }
    lines = espn_nba._box_line(payload, fixture["id"])
    for side in ("home", "away"):
        team = fixture[f"{side}_code"]
        box = lines[team]
        if box["pts"] != fixture[f"{side}_score"]:
            raise ValueError("box_score_conflict")
        result[side] = team
        result[f"{side}_pts"] = box["pts"]
        result[f"{side}_poss"] = espn_nba._possessions(box)
        result[f"{side}_poss_inputs"] = {key: value for key, value in box.items() if key != "pts"}
    return result


def dk_close(payload):
    entries = [entry for entry in payload.get("pickcenter", [])
               if str(record(entry.get("provider")).get("name", "")).lower().replace(" ", "") == "draftkings"]
    if len(entries) != 1:
        return {}
    result = {}
    for kind, market, sides in (("ml", "moneyline", ("home", "away")),
                                ("spread", "pointSpread", ("home", "away")),
                                ("total", "total", ("over", "under"))):
        quotes = [record(record(record(entries[0].get(market)).get(side)).get("close")) for side in sides]
        odds = [number(str(quote.get("odds")).replace("−", "-")) for quote in quotes]
        if any(value is None or abs(value) < 100 for value in odds):
            continue
        if kind == "ml":
            line = 0.0
        else:
            a, b = [number(str(quote.get("line")).lower().lstrip("ou").replace("−", "-")) for quote in quotes]
            if a is None or b is None or (a != (-b if kind == "spread" else b)):
                continue
            line = -a if kind == "spread" else a
        probabilities = [100.0 / (value + 100) if value > 0 else -value / (100 - value) for value in odds]
        result[kind] = {"line": line, "p": probabilities[0] / sum(probabilities), "american": odds}
    return result


def nba_snapshot(games, season, now):
    teams = {team: {"display": name, "games": []} for team, (_, name) in espn_nba.NBA_TEAMS.items()}
    seen = set()
    for game in sorted(games, key=lambda item: (item["date"], item["started"], item["event_id"])):
        if game["event_id"] in seen or stamp(game["started"]) >= now:
            raise ValueError("duplicate_or_future_nba_game")
        seen.add(game["event_id"])
        for side, other in (("home", "away"), ("away", "home")):
            team, opponent = game[side], game[other]
            poss = espn_nba._possessions(game[f"{side}_poss_inputs"])
            if poss != game[f"{side}_poss"]:
                raise ValueError("nba_possession_mismatch")
            pts, opp_pts = game[f"{side}_pts"], game[f"{other}_pts"]
            existing = teams[team]["games"]
            previous = date.fromisoformat(existing[-1]["date"]) if existing else None
            rest = (date.fromisoformat(game["date"]) - previous).days if previous is not None else None
            existing.append({
                "date": game["date"], "home": side == "home", "opponent": opponent,
                "poss": poss, "pts": pts, "opp_pts": opp_pts, "ortg": 100.0 * pts / poss,
                "drtg": 100.0 * opp_pts / poss, "rest_days": rest, "b2b": rest == 1,
            })
    snapshot = {"generated_at": utc_iso(now), "source": espn_nba.SOURCE_KEY,
                "season": season, "teams": teams, "injuries": {}}
    return espn_nba._validate_snapshot(snapshot)


def _prediction(snapshot, home, away, day):
    prior = [list(item for item in snapshot["teams"][side]["games"] if item["date"] < day) for side in (home, away)]
    if min(map(len, prior)) < NBA_PRIOR_GAMES:
        return None
    rest_home, rest_away = [(date.fromisoformat(day) - date.fromisoformat(items[-1]["date"])).days for items in prior]
    fixture = nba_model.NbaFixture(home, away, rest_home, rest_away, rest_home == 1, rest_away == 1)
    return nba_model.fixture_prediction(snapshot, fixture, before_date=day)


def _prediction_values(prediction):
    return [[item.exp_margin, item.sigma_margin, item.exp_total, item.sigma_total]
            for item in prediction.variants]


def _nba_forecast(info, official, snapshot, now):
    if official["neutral"] or any(official[f"{side}_code"] not in snapshot["teams"] for side in ("home", "away")):
        return None
    day = stamp(official["kickoff"]).astimezone(ET).date().isoformat()
    prediction = _prediction(snapshot, official["home_code"], official["away_code"], day)
    if prediction is None:
        return None
    values = {"variant_names": list(nba_model.VARIANT_NAMES), "distributions": _prediction_values(prediction),
              "central_probability": prediction.p_home, "expected_total": prediction.exp_total,
              "expected_margin": prediction.exp_margin}
    return model_document(info, official, NBA_VERSION, values, now)


def nba_ratings_forecast(info, official, history, now, daily):
    if not history or official["neutral"]:
        return None
    tip = stamp(official["kickoff"])
    if tip is None or now >= tip:
        return None
    day = tip.astimezone(ET).date()
    cutoff = ratings.cutoff_for(day, now)
    key = day, cutoff
    if key not in daily:
        prior = tuple(ratings.prior_finals(history, day, cutoff))
        daily[key] = (ratings.fit(prior), document_digest([game.evidence() for game in prior]))
    state, source_digest = daily[key]
    prediction = state.forecast(official["home_code"], official["away_code"], day, False,
                                minimum_games=NBA_PRIOR_GAMES)
    if prediction is None:
        return None
    return model_document(info, official, NBA_RATINGS_VERSION, {
        "variant_names": list(ratings.VARIANT_NAMES), "distributions": prediction.distributions(),
        "central_probability": prediction.home_probability(), "expected_total": prediction.total,
        "expected_margin": prediction.margin, "input_cutoff": utc_iso(cutoff), "input_sha256": source_digest,
    }, now)


def model_document(info, official, version, values, now):
    payload = {"model_version": version, "fixture_key": info["id"], "kickoff": info["kickoff"],
               "official_identity": [official[key] for key in ("provider", "id", "home_id", "away_id", "kickoff")],
               "observed_at": utc_iso(now), **values}
    return payload | {"sha256": document_digest(payload)}


def normal_tail(mean, sigma, threshold):
    return 0.5 * math.erfc((threshold - mean) / (sigma * math.sqrt(2.0)))


def quote(model, kind, line, side):
    if model["model_version"] == NBA_RATINGS_VERSION and kind not in {"match_winner", "total"}:
        return None
    distributions = model["distributions"]
    if kind == "match_winner" and "moneyline" in model:
        probabilities = model["moneyline"]
    elif kind in {"match_winner", "spread"}:
        probabilities = [normal_tail(margin, sigma, line or 0.0) for margin, sigma, _, _ in distributions]
    elif kind == "total":
        probabilities = [normal_tail(total, sigma, line) for _, _, total, sigma in distributions]
    else:
        return None
    if side in {"away", "under"}:
        probabilities = [1.0 - probability for probability in probabilities]
    if not probabilities or any(not 0 <= value <= 1 for value in probabilities):
        return None
    if model["model_version"] == EURO_VERSION:
        centre = probabilities[-1]
    else:
        centre = median(probabilities)
    return {"model_median": centre, "qs_variants": probabilities}


def nba_retest(games, snapshot, strict_pm, diagnostic_pm, cached):
    """Reuse the formal WP-NBA gates and cache only deterministic past forecasts."""
    wp = _wp()
    paired = wp.pair_snapshot_games(snapshot)
    sources = {(row["date"], row["home"], row["away"]): row for row in games}
    forecasts, retained = [], {}
    # A revision to an already-completed score invalidates every later forecast.
    day_keys = sorted({row["date"] for row in games})
    day_hashes = {}
    prior_hash = NBA_VERSION
    for day in day_keys:
        day_hashes[day] = prior_hash
        daily = sorted((row for row in games if row["date"] == day), key=lambda row: row["event_id"])
        prior_hash = document_digest([prior_hash, daily])
    for game in paired:
        raw = sources[(game["date"], game["home"], game["away"])]
        key, day = raw["event_id"], game["date"]
        saved = record(cached.get(key))
        digest = day_hashes[day]
        projections = None
        if saved.get("prior_sha256") == digest:
            projections = saved.get("prediction")
        else:
            prediction = _prediction(snapshot, game["home"], game["away"], day)
            if prediction is not None:
                projections = {"p_home": prediction.p_home, "exp_margin": prediction.exp_margin,
                               "exp_total": prediction.exp_total, "distributions": _prediction_values(prediction)}
        retained[key] = {"prior_sha256": digest, "prediction": projections}
        if projections is None:
            continue
        home_score, away_score = game["home_pts"], game["away_pts"]
        forecasts.append({**projections, "event_id": key, "date": day, "actual_total": home_score + away_score,
                          "actual_margin": home_score - away_score, "home_win": int(home_score > away_score),
                          "dk_proxy": raw.get("dk_proxy", {}), "overtime": raw.get("overtime")})

    metrics = _nba_retest_summary(games, forecasts, NBA_VERSION)
    metrics["A"] = {"passed": len(snapshot["teams"]) == espn_nba.TEAM_COUNT and len(paired) == len(games),
                    "paired_games": len(paired), "teams": len(snapshot["teams"])}
    if forecasts and all(block["games"] for block in snapshot["teams"].values()):
        metrics["C"] = _json_finite({key: value for key, value in wp.gate_c_sanity(snapshot).items() if key != "table"})
    return _nba_score_forecasts(metrics, forecasts, strict_pm, diagnostic_pm), retained


def _nba_retest_summary(games, forecasts, version):
    return {"eligible_games": len(forecasts), "required_games": NBA_RETEST_GAMES,
            "retest_ready": len(forecasts) >= NBA_RETEST_GAMES, "completed_games": len(games),
            "last_completed_date_et": max((row["date"] for row in games), default=None),
            "first_scored_date_et": min((row["date"] for row in forecasts), default=None),
            "last_scored_date_et": max((row["date"] for row in forecasts), default=None),
            "model_version": version}


def nba_ratings_retest(games, snapshot, history, strict_pm, diagnostic_pm, now):
    """Score the candidate on the original cohorts with its fixed 2024–25 parameters."""
    wp = _wp()
    season = int(snapshot["season"].split("-")[0])
    projections = ratings.replay(history, minimum_games=NBA_PRIOR_GAMES, season=season)
    forecasts = []
    for game in games:
        prediction = projections.get(game["event_id"])
        if prediction is None:
            continue
        home, away = game["home_pts"], game["away_pts"]
        forecasts.append({"p_home": prediction.home_probability(), "exp_margin": prediction.margin,
                          "exp_total": prediction.total, "distributions": prediction.distributions(),
                          "event_id": game["event_id"], "date": game["date"], "actual_total": home + away,
                          "actual_margin": home - away, "home_win": int(home > away),
                          "dk_proxy": game.get("dk_proxy", {}), "overtime": game.get("overtime")})

    metrics = _nba_retest_summary(games, forecasts, NBA_RATINGS_VERSION) | {
        "model_status": "research_only", "parameters": asdict(ratings.PARAMETERS),
        "fitted_season": "2024-25",
    }
    official_games = len(wp.pair_snapshot_games(snapshot))
    metrics["A"] = {"passed": len(snapshot["teams"]) == espn_nba.TEAM_COUNT and official_games == len(games),
                    "paired_games": official_games, "teams": len(snapshot["teams"])}
    state = ratings.fit(game for game in history if game.available_at <= now)
    if state.season == season and forecasts and all(block["games"] for block in snapshot["teams"].values()):
        teams = sorted(set(snapshot["teams"]) & state.teams.keys())
        table = [(state.teams[team].attack - state.teams[team].defense,
                  fmean(item["pts"] > item["opp_pts"] for item in snapshot["teams"][team]["games"]))
                 for team in teams]
        rho, p = wp.spearman(*map(list, zip(*table)))
        metrics["C"] = _json_finite({"teams": len(teams), "spearman_net_rating_vs_win_pct": {"rho": rho, "p": p},
                                     "passed": len(teams) == espn_nba.TEAM_COUNT and rho >= wp.GATE_C_MIN_RHO})
    return _nba_score_forecasts(metrics, forecasts, strict_pm, diagnostic_pm)


def _nba_score_forecasts(metrics, forecasts, strict_pm, diagnostic_pm):
    if not forecasts:
        return metrics
    wp = _wp()
    metrics["B"] = _json_finite(wp.gate_b_fit(forecasts))
    ml = wp.ml_metrics(forecasts)
    metrics["D"] = ml | {"passed": len(forecasts) >= wp.GATE_D_MIN_N and all(
        passed for key, passed in ml.items() if key.startswith("model_beats_"))}
    comparisons = {"draftkings_close_proxy": [], "polymarket_strict": []}
    total_comparisons = {"draftkings_close_proxy": [], "polymarket_strict": []}
    diagnostics = defaultdict(list)
    for row in forecasts:
        quotes = record(strict_pm.get(row["event_id"]))
        for cohort, contracts in (("draftkings_close_proxy", row["dk_proxy"]), ("polymarket_strict", quotes)):
            market = record(contracts.get("ml"))
            price = number(market.get("p"))
            if price is not None and 0 < price < 1:
                comparisons[cohort].append((row["p_home"], price, row["home_win"]))
            total = record(contracts.get("total"))
            line, price = number(total.get("line")), number(total.get("p"))
            if line is None or row["actual_total"] == line or not price or not 0 < price < 1:
                continue
            p_over = median(normal_tail(mu, sigma, line) for _, _, mu, sigma in row["distributions"])
            over = int(row["actual_total"] > line)
            total_comparisons[cohort].append((p_over, price, over))
        for kind, contract in record(diagnostic_pm.get(row["event_id"])).items():
            if kind == "spread" and metrics["model_version"] == NBA_RATINGS_VERSION:
                continue
            line, price = number(contract.get("line")), number(contract.get("p"))
            target = row["actual_total"] if kind == "total" else row["actual_margin"]
            if line is None or not price or not 0 < price < 1 or target == line:
                continue
            offset = 2 if kind == "total" else 0
            prediction = median(normal_tail(variant[offset], variant[offset + 1], line) for variant in row["distributions"])
            diagnostics[kind].append((prediction, price, int(target > line), row["overtime"]))
    e_values = {name: _nba_paired_scores(values) | {
                    "mad": fmean(abs(a - b) for a, b, _ in values) if values else None,
                    "favorite_flips": sum((a > 0.5) != (b > 0.5) for a, b, _ in values)}
                for name, values in comparisons.items()}
    # The original Gate E used DK; the later strict exchange check remains separately visible.
    dk = e_values["draftkings_close_proxy"]
    pm = e_values["polymarket_strict"]
    metrics["E"] = e_values | {"mad_max": wp.GATE_E_MAX_MAD,
        "passed": dk["n"] >= wp.GATE_D_MIN_N and dk["mad"] <= wp.GATE_E_MAX_MAD,
        "polymarket_at_bar": pm["n"] >= wp.GATE_D_MIN_N and pm["mad"] <= wp.GATE_E_MAX_MAD}
    total_dk = _nba_paired_scores(total_comparisons["draftkings_close_proxy"])
    total_pm = _nba_paired_scores(total_comparisons["polymarket_strict"])
    metrics["same_line_totals"] = {
        "draftkings_close_proxy_n": total_dk["n"],
        "draftkings_close_proxy_model_log_loss": total_dk["model_log_loss"],
        "polymarket_strict_n": total_pm["n"],
        "polymarket_strict_model_log_loss": total_pm["model_log_loss"],
        "polymarket_strict_market_log_loss": total_pm["market_log_loss"],
        "draftkings_close_proxy": total_dk, "polymarket_strict": total_pm,
        "coin_log_loss": math.log(2.0),
        "model_beats_coin": total_dk["n"] > 0 and total_dk["model_log_loss"] < math.log(2.0),
    }
    metrics["polymarket_final_score_diagnostics"] = {}
    for kind, values in diagnostics.items():
        projections, market, results, overtime = zip(*values)
        metrics["polymarket_final_score_diagnostics"][kind] = {
            "n": len(values), "model_brier": wp.brier_score(projections, results),
            "market_brier": wp.brier_score(market, results), "model_log_loss": wp.log_loss(projections, results)[0],
            "market_log_loss": wp.log_loss(market, results)[0], "official_overtime_known": sum(value is not None for value in overtime),
            "official_overtime_games": sum(value is True for value in overtime),
        }
    return metrics


def _nba_paired_scores(values):
    result = {"n": len(values), "model_brier": None, "market_brier": None,
              "model_log_loss": None, "market_log_loss": None}
    if values:
        wp = _wp()
        projections, markets, outcomes = zip(*values)
        result.update({"model_brier": wp.brier_score(projections, outcomes),
                       "market_brier": wp.brier_score(markets, outcomes),
                       "model_log_loss": wp.log_loss(projections, outcomes)[0],
                       "market_log_loss": wp.log_loss(markets, outcomes)[0]})
    return result


def _json_finite(value):
    if isinstance(value, dict):
        return {key: _json_finite(item) for key, item in value.items()}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


@lru_cache(maxsize=1)
def euro_parameters():
    root = ARCHIVE / "europe-basketball"
    parameters = json.loads((root / "prediction_metrics.json").read_bytes())["leagues"]
    metrics = json.loads((root / "market_metrics.json").read_bytes())["leagues"]
    # Both fitted scoring scales are frozen on the prior tuning split.
    scales = {}
    with (root / "game_predictions.csv").open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            scales.setdefault(row["league"], tuple(float(row[key]) for key in (
                "baseline_margin_sigma", "candidate_margin_sigma", "baseline_total_sigma", "candidate_total_sigma")))
    return parameters, metrics, scales


@lru_cache(maxsize=4)
def euro_archive(league):
    """Use the original frozen match history for providers that still publish fresh fixtures."""
    file = ARCHIVE / "europe-basketball/frozen_inputs.tar.gz"
    games = []
    with tarfile.open(file, "r:gz") as source:
        for item in source:
            parser = None
            if league in EURO_CODES and item.name.startswith(EURO_CODES[league]) and item.name[1:5].isdigit():
                parser = eurocl_fixture
            elif league == "bkvtb" and item.name in {"VTB-api-2026.json", "VTB-api-2027.json"}:
                parser = vtb_fixture
            elif league == "bkvtb" and item.name.startswith("VTB-info-") and item.name[9:13].isdigit():
                year = int(item.name[9:13]) - 1
                games.extend(row for raw in json.load(source.extractfile(item)) if (row := _vtb_archive_row(raw, year)))
            elif league == "bkbbl" and item.name == "european-odds.zip":
                with ZipFile(io.BytesIO(source.extractfile(item).read())) as archived, archived.open("games.csv") as raw:
                    for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8")):
                        if row["competition"] == EURO_LEAGUES[league] and row["season"][:4].isdigit() and int(row["season"][:4]) >= 2018:
                            games.append(_domestic_archive_row(row))
            if parser is None or not item.name.endswith(".json"):
                continue
            payload = json.load(source.extractfile(item))
            for raw in payload["data"]:
                row = parser(raw)
                if row and row["state"] == "final":
                    games.append(row)
    return games


def _vtb_archive_row(raw, year):
    if raw.get("GameStatus") != 1 or raw.get("LeagueNameEn") != "VTB United League":
        return None
    day = datetime.strptime(raw["GameDate"], "%d.%m.%Y").date()
    local = stamp(f"{day}T{raw['GameTimeMsk']}:00+03:00") if raw.get("HasTime") and raw.get("GameTimeMsk") else None
    day_open = datetime(day.year, day.month, day.day, tzinfo=UTC)
    available = local + EURO_OT_RESULT_DELAY if local else day_open + timedelta(days=2)
    return {"id": str(raw["GameID"]), "provider": "vtb", "state": "final", "season_year": year,
            "kickoff": utc_iso(local or day_open), "available_at": utc_iso(available), "neutral": False,
            "home_code": str(raw["TeamAid"]), "away_code": str(raw["TeamBid"]),
            "home_score": int(raw["ScoreA"]), "away_score": int(raw["ScoreB"]),
            "overtime": raw["Periods"] > 4 if raw["Periods"] >= 4 else None}


def _domestic_archive_row(raw):
    tip = stamp(raw["start_time_utc"])
    day = date.fromisoformat(raw["date"])
    day_open = datetime(day.year, day.month, day.day, tzinfo=UTC)
    available = tip + EURO_OT_RESULT_DELAY if tip else day_open + timedelta(days=2)
    return {"id": f"kaggle-{raw['game_id']}", "state": "final", "season_year": int(raw["season"][:4]),
            "kickoff": utc_iso(tip or day_open), "schedule_date": (tip.date() if tip else day).isoformat(),
            "available_at": utc_iso(available), "neutral": False, "home_code": raw["home_team"], "away_code": raw["away_team"],
            "home_score": int(raw["home_score"]), "away_score": int(raw["away_score"]),
            "overtime": {"True": True, "False": False}.get(raw.get("overtime"))}


def _bbl_history(archive, current):
    """Merge the frozen BBL inputs with official rows by team, date and score."""
    history = {(row["schedule_date"], row["home_code"], row["away_code"]): row for row in archive}
    conflicts = set()
    for row in current:
        if row["state"] != "final":
            continue
        day = stamp(row["kickoff"]).date().isoformat()
        key = (row.get("schedule_date", day), row["home_code"], row["away_code"])
        prior = history.get(key)
        if prior is not None and (prior["home_score"], prior["away_score"]) != (row["home_score"], row["away_score"]):
            conflicts.add(key)
        history[key] = row
    return [row for key, row in history.items() if key not in conflicts], bool(conflicts)


def eurocl_fixture(raw):
    if not isinstance(raw, dict):
        return None
    kickoff = stamp(raw.get("utcDate"))
    home, away = record(raw.get("local")), record(raw.get("road"))
    clubs = [record(side.get("club")) for side in (home, away)]
    if (not kickoff or not raw.get("identifier") or not all(club.get("code") and club.get("name") for club in clubs)
            or clubs[0]["code"] == clubs[1]["code"]):
        return None
    status = raw.get("gameStatus")
    state = "cancelled" if status == "Cancelled" else "final" if raw.get("played") is True else (
        "scheduled" if status == "Confirmed" and raw.get("confirmedHour") is True and raw.get("confirmedDate") is True else "unconfirmed")
    hs, aws = home.get("score"), away.get("score")
    if state == "final" and (type(hs) is not int or type(aws) is not int or min(hs, aws) <= 0 or hs == aws):
        return None
    result = {"id": raw["identifier"], "provider": "euroleague", "kickoff": utc_iso(kickoff), "state": state,
              "home_score": hs if state == "final" else None, "away_score": aws if state == "final" else None,
              "neutral": raw.get("isNeutralVenue") is True, "season_year": record(raw.get("season")).get("year"),
              "overtime": _euro_overtime(home, away) if state == "final" else None}
    for side, club in zip(("home", "away"), clubs):
        result[f"{side}_id"] = result[f"{side}_code"] = club["code"]
        result[f"{side}_names"] = sorted({name for key in ("name", "abbreviatedName", "editorialName", "tvCode")
                                          if isinstance((name := club.get(key)), str) and name})
    return result


def _euro_overtime(home, away):
    verified = []
    for side in (home, away):
        scores = record(side.get("partials"))
        extra = scores.get("extraPeriods")
        if not isinstance(extra, dict):
            return None
        quarters = [scores.get(f"partials{i}") for i in range(1, 5)]
        values = quarters + list(extra.values())
        if any(type(value) is not int or value < 0 for value in values) or sum(values) != side.get("score"):
            return None
        verified.append(set(extra))
    return bool(verified[0]) if verified[0] == verified[1] else None


def vtb_fixture(raw):
    kickoff = stamp(raw.get("matchTimeUTC"))
    sides = raw.get("competitors")
    if (not kickoff or not raw.get("matchId") or raw.get("isTimeSet") is not True or not isinstance(sides, list)
            or len(sides) != 2 or {item.get("isHomeCompetitor") for item in sides} != {True, False}):
        return None
    home, away = sorted(sides, key=lambda item: not item["isHomeCompetitor"])
    if not all(item.get("teamId") for item in (home, away)) or home["teamId"] == away["teamId"]:
        return None
    status = raw.get("matchStatus")
    state = {"COMPLETE": "final", "CANCELLED": "cancelled", "SCHEDULED": "scheduled",
             "POSTPONED": "postponed", "IN_PROGRESS": "started"}.get(status, "unconfirmed")
    hs, aws = home.get("scoreString"), away.get("scoreString")
    if state == "final" and (type(hs) is not int or type(aws) is not int or min(hs, aws) <= 0 or hs == aws):
        return None
    result = {"id": str(raw["matchId"]), "provider": "vtb", "kickoff": utc_iso(kickoff), "state": state,
              "home_score": hs if state == "final" else None, "away_score": aws if state == "final" else None,
              "neutral": raw.get("hostingTeamId") not in (home["teamId"], away["teamId"]),
              "season_year": raw.get("season", 0) - 1, "overtime": raw.get("extraPeriodsUsed")}
    for side, team in zip(("home", "away"), (home, away)):
        result[f"{side}_id"] = result[f"{side}_code"] = str(team["teamId"])
        result[f"{side}_names"] = sorted({name for key in ("teamName", "teamCode")
                                          for name in record(team.get(key)).values() if isinstance(name, str) and name})
    return result


def _logistic(x):
    return 1.0 / (1.0 + math.exp(-max(-36.0, min(36.0, x))))


def _euro_forecast(info, official, history, now):
    league = EURO_LEAGUES[info["league"]]
    configs, _, scales = euro_parameters()
    spec = configs[league]
    kickoff = stamp(official["kickoff"])
    cutoff = kickoff.replace(hour=0, minute=0, second=0, microsecond=0)
    # The archived candidate's prediction time is day-open UTC, including the six-hour result delay.
    if cutoff > now:
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    config = spec["winner_candidate"]
    states = [defaultdict(float), defaultdict(float)]
    setups = [(20, 65, 0.65, False, 1.0),
              (config["step"], config["home_adv"], config["carry"], config["margin_bonus"], config["scale"])]
    eloscale = math.log(10) / 400
    recent = defaultdict(lambda: deque(maxlen=20))
    league_totals = deque(maxlen=250)
    current_season = None
    history = {row["id"]: row for row in history if row.get("state") == "final"}

    def ready_at(item):
        return stamp(item.get("available_at")) or stamp(item["kickoff"]) + EURO_OT_RESULT_DELAY

    for row in sorted(history.values(), key=lambda item: (ready_at(item), stamp(item["kickoff"]), item["id"])):
        played = stamp(row["kickoff"])
        if ready_at(row) > cutoff or type(row.get("season_year")) is not int:
            continue
        if current_season != row["season_year"]:
            for ratings, (_, _, carry, _, _) in zip(states, setups):
                for key in ratings:
                    ratings[key] *= carry
            recent.clear()
            current_season = row["season_year"]
        home, away = row["home_code"], row["away_code"]
        hs, aws = row["home_score"], row["away_score"]
        for ratings, (step, hca, _, bonus, _) in zip(states, setups):
            probability = _logistic((ratings[home] - ratings[away] + hca * (not row["neutral"])) * eloscale)
            delta = step * (int(hs > aws) - probability) * (math.log(abs(hs - aws) + 1) / 2.5 if bonus else 1)
            ratings[home] += delta
            ratings[away] -= delta
        recent[home].append((hs, aws, played.date()))
        recent[away].append((aws, hs, played.date()))
        league_totals.append(hs + aws)
    home, away = official["home_code"], official["away_code"]
    if not league_totals or current_season != official["season_year"] or min(len(recent[home]), len(recent[away])) < 3:
        return None
    elo = [ratings[home] - ratings[away] + hca * (not official["neutral"])
           for ratings, (_, hca, _, _, _) in zip(states, setups)]
    moneyline = [_logistic(value * eloscale * settings[-1]) for value, settings in zip(elo, setups)]
    mean_total = fmean(league_totals)
    short_mean = fmean(list(league_totals)[-50:])

    def inputs(window):
        out = []
        for side in (home, away):
            entries = list(recent[side])[-window:]
            attack, defence = [sum(entry[index] - mean_total / 2 for entry in entries) / (len(entries) + 4)
                               for index in (0, 1)]
            rest = min(14, (cutoff.date() - entries[-1][2]).days)
            out.extend((attack, defence, rest))
        return out

    tc, mc = spec["total_candidate"], spec["margin_candidate"]
    ha, hd, hr, aa, ad, ar = inputs(tc["window"])
    total_features = [short_mean - mean_total, ha + aa, hd + ad, min(hr, ar), abs(ha - aa), abs(hd - ad)]
    ha, hd, hr, aa, ad, ar = inputs(mc["window"])
    margin_features = [ha - aa, ad - hd, hr - ar, int(not official["neutral"]), elo[1] / 100]
    candidate_total = mean_total + tc["intercept"] + sum(a * b for a, b in zip(total_features, tc["coefficients"]))
    candidate_margin = mc["intercept"] + sum(a * b for a, b in zip(margin_features, mc["coefficients"]))
    b_margin_sigma, c_margin_sigma, b_total_sigma, c_total_sigma = scales[league]
    values = {"variant_names": ["baseline", "registered-candidate"], "moneyline": moneyline,
              "expected_total": candidate_total, "expected_margin": candidate_margin,
              "training_cutoff": utc_iso(cutoff), "distributions": [
                  [elo[0] * 0.045, b_margin_sigma, mean_total, b_total_sigma],
                  [candidate_margin, c_margin_sigma, candidate_total, c_total_sigma],
              ]}
    return model_document(info, official, EURO_VERSION, values, now)


def forecast(info, official, cache, now, snapshot=None):
    if info["league"] == "nba":
        return _nba_forecast(info, official, snapshot, now) if snapshot is not None else None
    league = info["league"]
    if league in {*EURO_CODES, "bkvtb", "bkbbl"}:
        archived = euro_archive(league)
        board = record(record(cache.get("boards")).get(league))
        retained = record(record(cache.get("europe_finals")).get(league))
        current = list((retained | {item["id"]: item for item in board.get("fixtures", [])}).values())
        if league == "bkbbl":
            training = record(cache.get("bbl_training"))
            loaded = stamp(training.get("observed_at"))
            if (training.get("complete") is not True or training.get("season_year") != official["season_year"]
                    or loaded is None or not timedelta(0) <= now - loaded <= timedelta(hours=2)):
                return None
            history, conflict = _bbl_history(archived, training.get("fixtures", []) + current)
            if conflict:
                return None
        else:
            history = archived + current
        return _euro_forecast(info, official, history, now)
    return None
