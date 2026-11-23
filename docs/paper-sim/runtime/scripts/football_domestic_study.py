"""Replay the original WP-P4 study on the rolling verified domestic xG archive."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from functools import lru_cache
from hashlib import sha256
from importlib import import_module
import json
from pathlib import Path
from statistics import fmean
import re
import sys
from types import ModuleType

from polymarket_alpha_lab.papersim import recalib
from polymarket_alpha_lab.papersim.optimize import dataset, metrics
from polymarket_alpha_lab.papersim.optimize.families import soccer_recalib
from polymarket_alpha_lab.papersim.pipeline import MODEL_VERSION_SOCCER

import football_model as model
import football_totals_calibration as totals
from football_feed import document_digest, numeric_id, record, stamp, team_key, utc_iso
from football_research import ARCHIVE_ZONE, _forward_consensus, _forward_market_rows


PROJECT = Path(__file__).resolve().parents[4]
PROTOCOL_SHA256 = "df70542fef6bfddce2e8b57f25222c1a88824f160e429fa6fc14aeeb9530da0d"
REPORT_VERSION = "wp-p4-domestic-rolling-v1"
LAG = timedelta(hours=3)
CODE_BY_LEAGUE = {spec.model_league: league for league, spec in model.COMPETITIONS.items()}
REQUIRED_PROSPECTIVE_MATCHWEEKS = 2


@lru_cache(maxsize=1)
def _protocol():
    """Load the registered original research tool through its sibling namespace."""
    tools = PROJECT / "tools"
    expected = {
        tools / "wp_p4_walkforward.py": PROTOCOL_SHA256,
        Path(recalib.__file__): soccer_recalib.RECALIB_SOURCE_SHA256,
    }
    for path, digest in expected.items():
        if sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("wp_p4_registered_protocol_digest_mismatch")
    package = ModuleType("_football_wp_p4_tools")
    package.__path__ = [str(tools)]
    sys.modules[package.__name__] = package
    return import_module(package.__name__ + ".wp_p4_walkforward")


def _seed_index(rows):
    by_pair, by_id = defaultdict(list), {}
    for value in rows:
        row = record(value)
        if row.get("isResult") is not True:
            continue
        goals, expected = record(row.get("goals")), record(row.get("xG"))
        values = model._result(expected.get("h"), expected.get("a"), goals.get("h"), goals.get("a"))
        kickoff = stamp(row.get("datetime"), naive_utc=True)
        home, away = record(row.get("h")), record(row.get("a"))
        if values is None or kickoff is None:
            continue
        by_pair[team_key(home.get("title")), team_key(away.get("title"))].append((kickoff, values))
        if row_id := numeric_id(row.get("id")):
            by_id[row_id] = (kickoff, values, (numeric_id(home.get("id")), numeric_id(away.get("id"))))
    return by_pair, by_id


def _seed_kickoff(calendar, score, kickoff, match_id, seed):
    if seed is None:
        return kickoff
    by_pair, by_id = seed
    direct = by_id.get(match_id)
    if direct == (kickoff, score, (calendar["home_id"], calendar["away_id"])):
        return kickoff
    names = (team_key(calendar.get("home")), team_key(calendar.get("away")))
    candidates = {at for at, expected in by_pair[names] if expected == score and abs(at - kickoff) <= timedelta(hours=2)}
    if len(candidates) > 1:
        return None
    return next(iter(candidates), kickoff)


def _source(code, season, block, now):
    snapshot = record(block.get("calendar"))
    observed_at = stamp(block.get("calendar_at"))
    digest = block.get("calendar_sha256")
    expected_url = model._calendar_url(model.COMPETITIONS[code], season)
    if not snapshot:
        return None, "calendar_missing"
    if observed_at is None or observed_at > now or block.get("source") != expected_url:
        return None, "calendar_provenance_unverified"
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None, "calendar_digest_missing"
    return {"league": code, "season": str(season), "source": expected_url, "observed_at": utc_iso(observed_at),
            "sha256": digest, "seed_sha256": block.get("seed_sha256"),
            "verified_xg_cache_sha256": document_digest(record(block.get("results")))}, None


def _league_history(code, current, now, source_cache, seeds):
    spec = model.COMPETITIONS[code]
    matches, schedule, sources, rosters = [], [], [], set()
    seasons = record(record(source_cache).get("seasons"))
    coverage = {}
    for season in (current - 1, current):
        key = f"{code}:{season}"
        block = record(seasons.get(key))
        source, error = _source(code, season, block, now)
        stats = {"season": str(season), "status": error or "verified_calendar",
                 "fixtures": 0, "official_round_fixtures": 0, "xg_results": 0}
        coverage[str(season)] = stats
        if source is None:
            continue
        sources.append(source)
        expected_seed = source["seed_sha256"]
        archived_seed = seeds.get(f"{spec.model_league}_{season}")
        if expected_seed and (archived_seed is None or archived_seed[0] != expected_seed):
            stats["status"] = "xg_seed_clock_provenance_unavailable"
            continue
        seed = _seed_index(archived_seed[1]) if expected_seed else None
        results = record(block.get("results"))
        seen = set()
        missing, excluded = Counter(), Counter()
        for match_id, raw in sorted(block["calendar"].items()):
            fixture = record(raw)
            kickoff = stamp(fixture.get("kickoff"))
            home, away = (numeric_id(fixture.get(f"{name}_id")) for name in ("home", "away"))
            round_value = numeric_id(fixture.get("round"))
            matchweek = int(round_value) if round_value is not None else None
            if not all((numeric_id(match_id), kickoff, home, away)) or home == away:
                excluded["invalid_fixture_identity"] += 1
                continue
            fixture_state = fixture.get("state")
            if fixture_state not in {"finished", "scheduled", "started", "cancelled", "postponed", "awarded"}:
                excluded["invalid_fixture_state"] += 1
                continue
            stats["fixtures"] += 1
            rosters.update((spec.model_league, str(season), id_, id_) for id_ in (home, away))
            finished = fixture_state == "finished" and kickoff + LAG < now
            if matchweek is not None and 1 <= matchweek <= spec.rounds:
                stats["official_round_fixtures"] += 1
                schedule.append(dataset.ScheduleFixture(
                    match_id, spec.model_league, str(season), matchweek, kickoff, home, away, finished,
                    fixture_state == "cancelled", fixture_state == "awarded",
                    "-".join(map(str, fixture.get("score", ()))) or None,
                ))
            elif season == current:
                excluded["current_fixture_without_official_round"] += 1
                continue
            else:
                matchweek = None
            if not finished:
                continue
            score = model._known_result(results.get(match_id), fixture)
            if score is None:
                missing[matchweek] += 1
                continue
            source_kickoff = _seed_kickoff(fixture, score, kickoff, match_id, seed)
            if source_kickoff is None:
                excluded["ambiguous_seed_clock"] += 1
                continue
            safe_kickoff = max(source_kickoff, kickoff)
            if safe_kickoff + LAG >= now:
                continue
            natural = (safe_kickoff, home, away)
            if natural in seen:
                excluded["duplicate_team_and_kickoff"] += 1
                continue
            seen.add(natural)
            matches.append(dataset.HistoricalMatch(
                f"{spec.model_league}:{season}:{match_id}", spec.model_league, str(season), matchweek,
                safe_kickoff, home, away, *score, source_match_id=match_id,
                schedule_match_id=match_id if matchweek is not None else None,
                source_path=source["source"], source_kickoff=source_kickoff,
            ))
            stats["xg_results"] += 1
        stats["missing_xg_by_round"] = {str(round_): count for round_, count in missing.items()}
        stats["exclusions"] = dict(excluded)
        if season == current and not stats["official_round_fixtures"]:
            stats["status"] = "official_round_ids_missing"
        elif season != current and not stats["official_round_fixtures"] and stats["xg_results"]:
            stats["status"] = "verified_prior_season_warmup"
    audit = dataset.HistoryAudit(
        tuple(sorted(matches, key=lambda item: (item.season, item.kickoff, item.match_id))),
        tuple(schedule), tuple(sources), (), tuple(sorted(rosters)),
    )
    return audit, coverage


def _folds_fingerprint(folds):
    def history(item):
        return (item.match_id, utc_iso(item.kickoff), utc_iso(item.source_kickoff),
                item.home_team, item.away_team, item.home_xg, item.away_xg, item.home_goals, item.away_goals)

    return document_digest([
        (fold.block_id, utc_iso(fold.cutoff), [history(match) for match in fold.train],
         [history(match) for match in fold.test]) for fold in folds
    ])


def _retrospective(protocol, league, audit, folds):
    selections, predictions, cache = [], [], {}
    for fold in folds:
        config, selection = protocol.select_config(audit, fold, cache=cache, folds=folds)
        spec = soccer_recalib.spec_for_params({"half_life_days": config.half_life_days,
                                               "team_prior_matches": config.team_prior_matches})
        selections.append({"block_id": fold.block_id, "matchweek": fold.matchweek, "cutoff": utc_iso(fold.cutoff),
                           "spec_sha256": spec.spec_sha256(), "spec": dict(spec.params),
                           "status": selection["status"], "inner_fold_count": selection["inner_fold_count"],
                           "inner_fixture_count": selection["inner_fixture_count"],
                           "train_count": len(fold.train), "holdout_count": len(fold.test)})
        predictions.extend(protocol.evaluate_fold(audit, fold, config))
    summary = protocol.summarize(predictions)[league]
    replay = [[row["match_id"].rsplit(":", 1)[-1], row["kickoff"], row["home_goals"], row["away_goals"],
               row["block_id"], {name: [item["raw_1x2"], item["totals_probabilities"]]
                                 for name, item in row["forecasts"].items()}] for row in predictions]
    return {"fold_selections": selections, "forecast_count": len(replay), "summary": summary, "rows": replay}


def _market_replay(code, predicted, markets):
    paired = defaultdict(list)
    for fixture_id, kickoff, home, away, _, forecasts in predicted:
        day = stamp(kickoff).astimezone(ARCHIVE_ZONE).date()
        for market in ("1x2", "ou1.5", "ou2.5", "ou3.5"):
            price = markets.get((code, "fotmob:" + fixture_id, market))
            if price is None or price[:2] != (day, (home, away)):
                continue
            if market == "1x2":
                outcome = 0 if home > away else 1 if home == away else 2
                values = {name: tuple(p / sum(vector) for p in vector)
                          for name, (vector, _) in forecasts.items()}
            else:
                line = market[2:]
                outcome = int(home + away <= float(line))
                values = {name: (totals[line], 1 - totals[line]) for name, (_, totals) in forecasts.items()}
            paired[market].append((outcome, values | {"polymarket": price[2]}))
    scores = {}
    for market in ("1x2", "ou1.5", "ou2.5", "ou3.5"):
        samples = paired[market]
        scored_models = {}
        for name in ("recalibrated", "production10", "league_average_lambda", "polymarket"):
            exact = [(actual, values[name]) for actual, values in samples if name in values]
            if exact:
                brier = fmean(metrics.multiclass_brier(vector, actual) for actual, vector in exact)
                scored_models[name] = {
                    "matches": len(exact), "brier": brier if market == "1x2" else brier / 2,
                    "log_loss": fmean(metrics.multiclass_log_loss(vector, actual, floor=1e-12)
                                      for actual, vector in exact),
                }
        scores[market] = {"paired_fixtures": len(samples), "models": scored_models}
    return scores


def _admission_evidence(connection, now):
    if connection is None:
        return {}, {}
    ready = connection.execute(
        """SELECT to_regclass('football_notify.football_admission_study') IS NOT NULL AS ready"""
    ).fetchone()["ready"]
    if not ready:
        return {}, {}
    study_rows = connection.execute(
        """SELECT study_key, league, model_version, registered_at, plan, last_kickoff
           FROM football_notify.football_admission_study
           WHERE league = ANY(%s) AND registered_at <= %s ORDER BY registered_at""",
        (list(model.COMPETITIONS), now),
    ).fetchall()
    decisions = connection.execute(
        """SELECT decision FROM football_notify.football_admission_state
           WHERE decision->'scope'->>'league'=ANY(%s) AND updated_at <= %s""",
        (list(model.COMPETITIONS), now),
    ).fetchall()
    by_league, current = defaultdict(list), defaultdict(list)
    for row in study_rows:
        by_league[row["league"]].append({
            "study_key": row["study_key"], "model_version": row["model_version"],
            "registered_at": utc_iso(row["registered_at"]), "last_kickoff": utc_iso(row["last_kickoff"]),
            "plan_sha256": document_digest(row["plan"]),
        })
    for row in decisions:
        decision = record(row["decision"])
        scope = record(decision.get("scope"))
        if scope.get("market_kind") == "match_winner":
            current[scope["league"]].append({
                "model_version": scope["model_version"], "status": decision["status"],
                "reasons": decision.get("reasons", []), "study": decision.get("study"),
            })
    return by_league, current


def _prospective_status(studies, decisions):
    trials = [study for study in studies
              if study["model_version"].startswith(soccer_recalib.FAMILY_ID + "-")]
    qualified_versions = {row["model_version"] for row in decisions if row["status"] == "market_admitted"}
    exact_decisions = [row for row in decisions if row["model_version"] in {s["model_version"] for s in trials}]
    completed = max((record(row.get("study")).get("complete_matchweeks", 0) for row in exact_decisions), default=0)
    ready = any(row["model_version"] in qualified_versions for row in exact_decisions)
    return {"required_complete_shadow_matchweeks": REQUIRED_PROSPECTIVE_MATCHWEEKS,
            "registered_candidate_studies": trials, "full_candidate_shadow_matchweeks": completed,
            "candidate_admission_decisions": exact_decisions, "related_production_studies": [
                study for study in studies if study["model_version"] == MODEL_VERSION_SOCCER
            ], "related_production_decisions": [
                row for row in decisions if row["model_version"] == MODEL_VERSION_SOCCER
            ], "recommendation_version_status": "candidate_scope_admitted" if ready else "retrospective_shadow",
            "missing_candidate_evidence": (["predeclared_out_of_time_candidate_study", "two_complete_shadow_matchweeks",
                                           "gate_e_and_a_through_d_exact_candidate_evidence"] if not trials else
                                           ["candidate_scope_admission"] if not ready else [])}


def run_daily(now, source_cache, *, connection=None, previous=None, seed_paths=None, include_totals_rows=False,
              evaluate_independent_tail=False, historical_prices=None):
    """Produce an auditable WP-P4 report using the normal domestic feed cache."""
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("domestic WP-P4 study requires an aware timestamp")
    protocol = _protocol()
    season = model._season(now)
    desired = {f"{league}_{start}" for league in CODE_BY_LEAGUE for start in (season - 1, season)}
    seeds = model._seed_rows(model.PUBLIC_SEEDS if seed_paths is None else seed_paths, desired)
    markets = _forward_consensus(_forward_market_rows(connection, now, leagues=model.COMPETITIONS))
    studies, admission = _admission_evidence(connection, now)
    earlier = record(previous)
    earlier_leagues = record(earlier.get("leagues")) if earlier.get("version") == REPORT_VERSION else {}
    leagues, totals_rows = {}, []
    for code, current in model.COMPETITIONS.items():
        audit, coverage = _league_history(code, season, now, source_cache, seeds)
        folds, skipped = dataset.build_folds(audit, league=current.model_league, season=str(season))
        fingerprint = _folds_fingerprint(folds)
        earlier_league = record(earlier_leagues.get(code))
        old = record(earlier_league.get("retrospective"))
        reused = earlier_league.get("fold_inputs_sha256") == fingerprint and bool(old)
        if reused:
            replay = old
        else:
            replay = _retrospective(protocol, current.model_league, audit, folds)
        if season == totals.SEASON:
            totals_rows.extend(totals.replay(code, audit, source_cache, replay["rows"], now))
        leagues[code] = {
            "model_league": current.model_league, "target_season": str(season), "inputs": coverage,
            "sources": list(audit.sources), "source_history_matches": len(audit.matches),
            "fold_inputs_sha256": fingerprint, "eligible_matchweeks": len(folds),
            "skipped_rounds": list(skipped),
            "selection_status": "unchanged_inputs" if reused and folds else "walk_forward_completed" if folds else "no_complete_folds",
            "retrospective": replay, "polymarket_replay": _market_replay(code, replay["rows"], markets),
            "prospective": _prospective_status(studies.get(code, ()), admission.get(code, ())),
        }
    report = {"version": REPORT_VERSION, "as_of": utc_iso(now),
              "protocol": {"family": soccer_recalib.FAMILY_ID, "procedure": soccer_recalib.PROCEDURE_VERSION,
                           "sha256": PROTOCOL_SHA256, "input_mode": "event_time_replay",
                           "half_life_days": list(protocol.HALF_LIVES), "team_prior_matches": list(protocol.TEAM_PRIORS),
                           "completion_lag_hours": 3, "bootstrap_repetitions": 2000,
                           "target_lines": list(protocol.TOTAL_LINES)},
              "comparison_limits": [
                  "Final xG and schedule snapshots support event-time replay with a three-hour completion assumption.",
                  "The historical inner folds select parameters, and each outer fold uses an earlier complete official prefix.",
                  "WP-P4 recommendation changes require a predeclared out-of-time sample, two complete prospective shadow matchweeks, Gate E and A–D evidence for the same model.",
              ], "leagues": leagues}
    if season == totals.SEASON:
        earlier_totals = record(earlier.get("totals_calibration"))
        report["totals_calibration"] = totals.research(
            totals_rows, protocol.paired_block_interval, frozen=earlier_totals.get("model"), include_rows=include_totals_rows
        )
    if evaluate_independent_tail:
        archived, archived_seeds, provenance = totals.archived_history(now)
        independent = []
        for code in totals.BOOKMAKER_FILES:
            audit, _ = _league_history(code, totals.INDEPENDENT_SEASON, now, archived, archived_seeds)
            independent.extend(totals.replay(
                code, audit, archived, (), now, season_start=totals.INDEPENDENT_SEASON,
                start=totals.INDEPENDENT_START, end=totals.INDEPENDENT_END, training_split=None,
            ))
        report["league_tail_calibration"] = totals.research_tail(
            totals_rows, independent, protocol.paired_block_interval, sources=provenance,
            book_dir=historical_prices, include_rows=include_totals_rows,
        )
    if connection is not None and (registered := totals.read_registered(connection)) is not None:
        report["league_tail_prospective"] = totals.forward_progress(connection, registered, now,
                                                                   protocol.paired_block_interval)
    return report | {"sha256": document_digest(report)}


def main():
    import football_store as store

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument("--score-totals", action="store_true")
    commands.add_argument("--score-league-tail", action="store_true")
    commands.add_argument("--register-totals", action="store_true")
    commands.add_argument("--amend-totals-schedule", action="store_true")
    parser.add_argument("--include-totals-rows", action="store_true")
    parser.add_argument("--historical-prices-dir", type=Path)
    args = parser.parse_args()
    with store.connect(read_only=not (args.register_totals or args.amend_totals_schedule)) as connection:
        if args.amend_totals_schedule:
            print(json.dumps(totals.amend_schedule(connection), ensure_ascii=False, allow_nan=False))
            return
        if args.register_totals and (registered := totals.read_registered(connection)) is not None:
            print(json.dumps({"registration": registered}, ensure_ascii=False, allow_nan=False))
            return
        previous = connection.execute(
            "SELECT report FROM football_domestic_study ORDER BY study_day DESC LIMIT 1"
        ).fetchone()
        source_cache = store.source_cache(connection)
        report = run_daily(store.db_now(connection), source_cache, connection=connection,
                           previous=previous["report"] if previous else None,
                           include_totals_rows=args.include_totals_rows or args.register_totals,
                           evaluate_independent_tail=args.score_league_tail or args.register_totals,
                           historical_prices=args.historical_prices_dir)
        result = (report["totals_calibration"] if args.score_totals else report["league_tail_calibration"])
        if args.register_totals:
            result["registration"] = totals.register(connection, source_cache, result, store.db_now(connection))
        if not args.include_totals_rows:
            result.pop("holdout_rows", None)
            for key in ("independent_scores", "recent_exploratory_scores"):
                if key in result:
                    result[key].pop("holdout_rows", None)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
