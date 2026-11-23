"""Project football paper-admission policy and prospective evidence evaluation."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
from statistics import fmean

from psycopg import Error as PostgresError
from psycopg.types.json import Jsonb

import football_feed as feed
import football_forward as forward
import football_model as model
import football_store as store
from polymarket_alpha_lab.papersim import evaluation, gates, soccer_model
from polymarket_alpha_lab.papersim.optimize import metrics, scorecard
from polymarket_alpha_lab.papersim.pipeline import GATE_VERSION, MODEL_VERSION_SOCCER


Scope = tuple[str, str, Decimal | None, str | None, str]

POLICY_VERSION = "football-admission-2026-11-19/wp-p3-wp-p4-v1"
PROJECT_ROOT = Path(__file__).resolve().parents[4]
ADMISSION_LOCK = 10
DOMESTIC = frozenset({"epl", "lal", "sea", "bun", "fl1", "elc", "ere"})
CLUB_CONTINENTAL = frozenset({"ucl", "uel"})
NATIONAL = frozenset({"unl", "uefa-nations-league", "nations-league", "fifwc", "wc", "wwc", "uefaeuro", "afc"})
GATE_E_FIXTURES = {"elc": 12, "ere": 8}
GATE_E_MAX_MAD = 0.06
FULL_MATCHWEEKS = 2
OUTCOME_ORDER = ("home", "draw", "away")
RULE = "regulation_90_plus_stoppage"
SHA256 = re.compile(r"[a-f0-9]{64}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS football_notify.football_admission_study (
    study_key text PRIMARY KEY,
    league text NOT NULL,
    model_version text NOT NULL,
    season integer NOT NULL,
    registered_at timestamptz NOT NULL,
    last_kickoff timestamptz NOT NULL,
    calendar_source text NOT NULL,
    calendar_sha256 text NOT NULL,
    calendar_observed_at timestamptz NOT NULL,
    plan jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS football_notify.football_admission_state (
    scope_key text PRIMARY KEY,
    updated_at timestamptz NOT NULL,
    decision_sha256 text NOT NULL,
    decision jsonb NOT NULL,
    CHECK (decision->>'status' IN
        ('existing_approved', 'market_admitted', 'shadow', 'conditional', 'restricted'))
);
CREATE TABLE IF NOT EXISTS football_notify.football_admission_history (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope_key text NOT NULL REFERENCES football_notify.football_admission_state,
    evaluated_at timestamptz NOT NULL,
    decision_sha256 text NOT NULL,
    decision jsonb NOT NULL
);
"""


def scope_key(league, kind, line, team_side, version) -> Scope | None:
    """Canonical identity for the evaluator and the live paper filter."""
    if not all(isinstance(value, str) and value for value in (league, kind, version)):
        return None
    if kind in {"match_winner", "btts", "spread"}:
        return (league, kind, None, None, version) if line is None and team_side is None else None
    if kind == "team_total":
        if team_side not in ("home", "away"):
            return None
    elif kind != "total" or team_side is not None:
        return None
    try:
        half_line = Decimal(str(line))
    except (ValueError, InvalidOperation):
        return None
    # The feed's exact half-goal contract grammar.
    if (not half_line.is_finite() or not Decimal("0.5") <= half_line <= Decimal("19.5")
            or half_line % 1 != Decimal("0.5")):
        return None
    return league, kind, half_line.quantize(Decimal("0.1")), team_side, version


def _approved_domestic() -> frozenset[Scope]:
    result = set()
    for league in DOMESTIC:
        result.add((league, "btts", None, None, MODEL_VERSION_SOCCER))
        if league not in GATE_E_FIXTURES:
            result.add((league, "match_winner", None, None, MODEL_VERSION_SOCCER))
        for goals in range(20):
            line = Decimal(goals) + Decimal("0.5")
            result.add((league, "total", line, None, MODEL_VERSION_SOCCER))
            for side in ("home", "away"):
                result.add((league, "team_total", line, side, MODEL_VERSION_SOCCER))
    return frozenset(result)


EXISTING_SCOPES = _approved_domestic()


def _scope_document(scope):
    league, kind, line, side, version = scope
    return {"league": league, "market_kind": kind, "line": str(line) if line is not None else None,
            "team_side": side, "model_version": version}


def _decode_scope(document):
    return scope_key(document.get("league"), document.get("market_kind"), document.get("line"),
                     document.get("team_side"), document.get("model_version"))


def _exists(connection, relation):
    return connection.execute("SELECT to_regclass(%s) IS NOT NULL AS ready",
                              (f"football_notify.{relation}",)).fetchone()["ready"]


def active_scopes(connection) -> frozenset[Scope]:
    """Read currently available exact versions from the notification database."""
    if not _exists(connection, "football_admission_state"):
        return EXISTING_SCOPES
    rows = connection.execute(
        """SELECT decision FROM football_notify.football_admission_state
           WHERE decision->>'policy_version' = %s AND decision->>'status' = 'market_admitted'""",
        (POLICY_VERSION,),
    ).fetchall()
    decisions = [row["decision"] for row in rows]
    score_methods = any(row.get("admission_rule") == evaluation.PROMOTION_RULES_VERSION for row in decisions)
    registered = _score_scopes(connection, store.db_now(connection)) if score_methods else {}
    active = set(EXISTING_SCOPES)
    for decision in decisions:
        scope = _decode_scope(decision["scope"])
        if (scope and scope[0] in GATE_E_FIXTURES and scope[1:4] == ("match_winner", None, None)
                and scope[4] == MODEL_VERSION_SOCCER and decision.get("admission_rule") == POLICY_VERSION):
            active.add(scope)
        elif (scope in registered and decision.get("admission_rule") == evaluation.PROMOTION_RULES_VERSION
              and decision["formal_promotion"]["status"] == evaluation.VERDICT_PASS):
            active.add(scope)
    return frozenset(active)


def _previous_scopes(connection):
    rows = connection.execute(
        "SELECT decision->'scope' AS scope FROM football_notify.football_admission_state"
    ).fetchall()
    return {scope for row in rows if (scope := _decode_scope(row["scope"]))}


def _score_scopes(connection, now):
    """Track archived and prospectively registered score-model versions."""
    import football_research as research

    cache = {}
    if _exists(connection, "source_cache"):
        row = connection.execute(
            "SELECT payload FROM football_notify.source_cache WHERE cache_key = 'research/1'"
        ).fetchone()
        cache = row["payload"] if row else {}
    _, research_state = research._cache(cache)
    definitions = {
        league: [(research.model_version(league), None),
                 *((item.version, registered) for item, registered in
                   research._registered_league_models(league, research_state, now))]
        for league in research.COMPETITIONS
    }
    scopes = {}
    for league, kind, line, team_side in research.registered_scopes():
        history = research.historical_evidence(league, kind, line)
        if not history:
            continue
        for version, registered in definitions[league]:
            scope = scope_key(league, kind, line, team_side, version)
            if scope:
                scopes.setdefault(scope, {"history": history if registered is None else None, "registered": registered})
    return scopes


def _contract_scopes(connection, registered):
    if not _exists(connection, "football_forward_contract"):
        return {}
    registered_markets = {scope[:4] for scope in registered}
    rows = connection.execute(
        """SELECT DISTINCT condition_id, market_id, kickoff, league, market_kind, line, team_side
           FROM football_notify.football_forward_contract
           WHERE league = ANY(%s) AND rule = %s AND evidence->>'direct_contract_verified' = 'true'""",
        (sorted({scope[0] for scope in registered_markets}), RULE),
    ).fetchall()
    by_id = defaultdict(set)
    for row in rows:
        identity = scope_key(row["league"], row["market_kind"], row["line"], row["team_side"], "contract")
        if identity and identity[:4] in registered_markets:
            by_id[(row["condition_id"], row["market_id"], row["kickoff"])].add(identity[:4])
    return {key: next(iter(scopes)) for key, scopes in by_id.items() if len(scopes) == 1}


def _native_operations_report(cursor, now):
    """Use the latest verified canonical project report and its managed receipt."""
    from polymarket_alpha_lab.papersim import cli, report, snapshots

    rows = cli._snapshot_rows(cursor, """
        SELECT operation.operation_key, operation.occurred_at, snapshot.content_sha256,
               snapshot.content, snapshot.byte_length, snapshot.fetched_at, snapshot.schema_version
        FROM papersim.command_run_events operation
        JOIN papersim.snapshots snapshot ON snapshot.content_sha256 = (operation.output_evidence->>0)
        WHERE operation.command = 'report' AND operation.phase = 'SUCCEEDED'
          AND jsonb_array_length(operation.output_evidence) = 1
          AND snapshot.kind = 'papersim-report' AND snapshot.source = 'papersim:report'
          AND EXISTS (SELECT FROM papersim.command_run_events start
                      WHERE start.operation_key = operation.operation_key AND start.command = operation.command
                        AND start.attempt = operation.attempt AND start.phase = 'STARTED'
                        AND start.occurred_at <= snapshot.fetched_at)
        ORDER BY operation.occurred_at DESC, operation.run_event_id DESC LIMIT 1
    """)
    if not rows:
        return {}
    row = rows[0]
    body = bytes(row["content"])
    if (len(body) != row["byte_length"] or sha256(body).hexdigest() != row["content_sha256"]
            or row["schema_version"] != snapshots.SNAPSHOT_DEFAULT_SCHEMA_VERSION
            or not row["fetched_at"] <= row["occurred_at"] <= now):
        return {}
    try:
        document = feed.record(json.loads(body))
        evaluated = feed.record(document.get("evaluation"))
        report_at = feed.stamp(document.get("as_of"))
        if (document.get("report_command") != report.REPORT_COMMAND
                or document.get("algorithm_version") != report.REPORT_ALGORITHM_VERSION
                or evaluated.get("algorithm_version") != evaluation.ALGORITHM_VERSION
                or evaluated.get("promotion_rules_version") != evaluation.PROMOTION_RULES_VERSION
                or not all(document.get(flag) is True for flag in ("paper_only", "report_only", "readonly"))
                or report_at is None or report_at > row["fetched_at"]
                or report.canonical_report_json(document) != body
                or report.report_operation_key(document.get("scope"), report_at, row["content_sha256"])
                != row["operation_key"]):
            return {}
    except (ValueError, TypeError):
        return {}
    return {"sha256": row["content_sha256"], "document": document, "as_of": report_at}


def _managed_queue_key(candidate):
    return candidate.model_key, candidate.market_type, candidate.gate_version


def _native_criterion_f(archive, queue_key):
    if archive:
        managed = archive["document"]["evaluation"]
        for queue in feed.rows(managed.get("queues")):
            identity = feed.record(queue.get("queue"))
            if tuple(identity.get(key) for key in ("model_key", "market_type", "gate_version")) != queue_key:
                continue
            matches = [check for check in feed.rows(queue.get("promotion")) if check.get("key") == "F"]
            if len(matches) == 1:
                check = matches[0]
                return evaluation.CriterionResult("F", check.get("verdict"),
                                                  tuple(feed.rows(check.get("reasons"))))
    return evaluation.criterion_f()


def _assess_managed_queue(candidates, archive, latch, latest_in_scope, unbound):
    outcome = evaluation.evaluate_queues(candidates).queues[0]
    criterion_f = _native_criterion_f(archive, _managed_queue_key(outcome.queue))
    checks = (*outcome.promotion[:-1], criterion_f)
    missing_coverage = unbound != 0
    # A reported operations pass applies to evidence visible to that report.
    report_covers_scope = bool(archive and archive["as_of"] >= latest_in_scope)
    paused = outcome.pause.stop_new_entries or outcome.pause.fx17_pause_review or latch
    status = evaluation.overall_verdict(checks)
    if status == evaluation.VERDICT_PASS and (missing_coverage or paused or not report_covers_scope):
        status = evaluation.VERDICT_NOT_ASSESSABLE
    return {
        "status": status, "rules_version": evaluation.PROMOTION_RULES_VERSION,
        "scope_counts": asdict(outcome.counts), "criteria": [asdict(check) for check in checks],
        "unbound_managed_candidates": unbound, "paused": bool(paused),
        "managed_report_sha256": archive.get("sha256"),
        "managed_report_covers_scope": report_covers_scope,
        "queue": asdict(outcome.queue),
    }


def _read_managed_promotion(native, bindings, registered, now):
    """Attribute only actual managed ledger rows to the exact verified contract."""
    from polymarket_alpha_lab.papersim import cli, store_psycopg

    with native.transaction():
        cursor = native.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor.execute("""
            SELECT c.candidate_id, c.event_start_at, c.model_version, c.market_id,
                   c.condition_id, c.evaluated_at, c.conclusion, s.params
            FROM papersim.candidates c JOIN papersim.scan_runs s USING (scan_id)
            WHERE NOT c.is_legacy AND NOT s.is_legacy AND c.model_version = ANY(%s)
              AND s.gate_version = %s AND c.evaluated_at <= %s
        """, (sorted({scope[4] for scope in registered}), GATE_VERSION, now))
        columns = [column.name for column in cursor.description]
        metadata = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        if not metadata:
            return {}
        records = {item["candidate_id"]: item for item in metadata}
        raw_candidates, raw_prices, raw_results = cli._read_evaluation_rows(cursor)
        raw_candidates = [item for item in raw_candidates if item["candidate_id"] in records]
        archive = _native_operations_report(cursor, now)
        latches = store_psycopg.latched_pauses(native)
    pending = set()
    evaluations = cli._evaluation_candidates_from_rows(
        raw_candidates, raw_prices, raw_results, pending_settlements=pending)
    unbound = Counter()
    groups = defaultdict(list)
    for candidate in evaluations:
        row = records[int(candidate.candidate_id)]
        kickoff = row["event_start_at"]
        observed = row["evaluated_at"]
        market_scope = bindings.get((row["condition_id"], row["market_id"], kickoff))
        scope = (*market_scope, row["model_version"]) if market_scope else None
        if scope not in registered or kickoff is None or observed >= kickoff:
            if not candidate.construction_failed:
                unbound[row["model_version"]] += 1
            continue
        registration = registered[scope]["registered"]
        if registration is not None and observed < registration:
            continue
        if candidate.candidate_id in pending:
            candidate = replace(candidate, outcome=None, settled_time=None)
        groups[scope, _managed_queue_key(candidate)].append((candidate, row))

    assessments = defaultdict(list)
    for (scope, queue_key), rows in groups.items():
        candidates = [candidate for candidate, _ in rows]
        recorded_pauses = set()
        for _, row in rows:
            scan_admission = feed.record(feed.record(row["params"]).get("admission"))
            recorded_pauses.update(queue for queue in feed.rows(scan_admission.get("paused_queues"))
                                  if isinstance(queue, str))
        queue_prefix = "|".join(("DOMESTIC", *queue_key, ""))
        latch = any(any(flags) for queue, flags in latches.items()
                    if queue in recorded_pauses or queue.startswith(queue_prefix))
        latest = max(max(filter(None, (candidate.decision_time, candidate.settled_time)))
                     for candidate in candidates)
        assessments[scope].append(_assess_managed_queue(candidates, archive, latch, latest, unbound[scope[4]]))
    # A candidate model/scope with ambiguous native queue identity cannot inherit another queue's verdict.
    return {scope: verdicts[0] for scope, verdicts in assessments.items() if len(verdicts) == 1}


def _formal_promotions(connection, registered, now):
    configured_root = os.environ.get("PAPERSIM_MANAGED_REPO_ROOT")
    project_root = Path(configured_root).expanduser() if configured_root else PROJECT_ROOT
    if not project_root.is_absolute():
        return {}, "managed_papersim_project_root_invalid"
    if not (project_root / ".local" / "postgres" / "instance.json").is_file():
        return {}, "managed_papersim_database_unavailable"
    bindings = _contract_scopes(connection, registered)
    if not bindings:
        return {}, "managed_scope_contract_bindings_unavailable"
    from polymarket_alpha_lab.papersim import store_psycopg

    try:
        with store_psycopg.connect_managed(project_root) as native:
            assessments = _read_managed_promotion(native, bindings, registered, now)
    except (OSError, ValueError, PostgresError, store_psycopg.PapersimStoreError):
        return {}, "managed_papersim_evidence_unavailable"
    return assessments, "exact_managed_paper_queue_unverified"


def _study_plan(league, season, cached, now, used_rounds):
    """Freeze complete, future official rounds and the exact Gate E cohort."""
    competition = model.COMPETITIONS[league]
    calendar_at = feed.stamp(cached.get("calendar_at"))
    calendar_source = cached.get("source")
    calendar_sha = cached.get("calendar_sha256")
    if (calendar_at is None or calendar_at > now or calendar_source != model._calendar_url(competition, season)
            or not isinstance(calendar_sha, str) or SHA256.fullmatch(calendar_sha) is None):
        return None
    rounds = defaultdict(list)
    for fixture_id, raw in feed.record(cached.get("calendar")).items():
        item = feed.record(raw)
        if type(item.get("round")) is int:
            rounds[item["round"]].append((fixture_id, item))
    required_fixtures = competition.matches_per_season // competition.rounds
    complete = []
    for number, matches in rounds.items():
        if number in used_rounds or not 1 <= number <= competition.rounds or len(matches) != required_fixtures:
            continue
        fixtures = []
        teams = set()
        for fixture_id, item in matches:
            kickoff = feed.stamp(item.get("kickoff"))
            home_id, away_id = (feed.numeric_id(item.get(field)) for field in ("home_id", "away_id"))
            if (not feed.numeric_id(fixture_id) or item.get("state") != "scheduled" or kickoff is None
                    or kickoff - forward.FORECAST_OPEN <= now or home_id is None or away_id is None
                    or home_id == away_id or home_id in teams or away_id in teams):
                break
            teams.update((home_id, away_id))
            fixtures.append({"fixture_id": fixture_id, "kickoff": feed.utc_iso(kickoff),
                             "team_ids": [home_id, away_id], "round": number})
        if len(fixtures) == required_fixtures:
            complete.append(sorted(fixtures, key=lambda item: (item["kickoff"], item["fixture_id"])))
    complete.sort(key=lambda fixtures: (fixtures[0]["kickoff"], fixtures[0]["round"]))
    if len(complete) < FULL_MATCHWEEKS:
        return None
    selected = complete[:FULL_MATCHWEEKS]
    all_fixtures = [fixture for fixtures in selected for fixture in fixtures]
    plan = {"rounds": [fixtures[0]["round"] for fixtures in selected], "fixtures": all_fixtures,
            "gate_e_ids": [fixture["fixture_id"] for fixture in selected[0][:GATE_E_FIXTURES[league]]]}
    return {
        "study_key": feed.document_digest({"league": league, "season": season,
                                           "model_version": MODEL_VERSION_SOCCER, "plan": plan}),
        "league": league, "model_version": MODEL_VERSION_SOCCER, "season": season, "registered_at": now,
        "last_kickoff": max(feed.stamp(fixture["kickoff"]) for fixture in all_fixtures),
        "calendar_source": calendar_source, "calendar_sha256": calendar_sha,
        "calendar_observed_at": calendar_at, "plan": plan,
    }


def _declare_studies(connection, studies, now):
    if not _exists(connection, "source_cache"):
        return
    cache = feed.record(store.source_cache(connection))
    if cache.get("version") != 1:
        return
    seasons = feed.record(cache.get("seasons"))
    for league in GATE_E_FIXTURES:
        candidates = sorted(
            int(key.split(":")[1]) for key in seasons
            if re.fullmatch(rf"{league}:[0-9]{{4}}", key)
        )
        if not candidates:
            continue
        season = candidates[-1]
        prior = [study for study in studies if study["league"] == league and study["season"] == season]
        if any(study["last_kickoff"] >= now for study in prior):
            continue
        used_rounds = {number for study in prior for number in study["plan"]["rounds"]}
        study = _study_plan(league, season, feed.record(seasons[f"{league}:{season}"]), now, used_rounds)
        if study is None:
            continue
        connection.execute(
            """INSERT INTO football_notify.football_admission_study
               (study_key, league, model_version, season, registered_at, last_kickoff,
                calendar_source, calendar_sha256, calendar_observed_at, plan)
               VALUES (%(study_key)s, %(league)s, %(model_version)s, %(season)s, %(registered_at)s, %(last_kickoff)s,
                       %(calendar_source)s, %(calendar_sha256)s, %(calendar_observed_at)s, %(plan)s)
               ON CONFLICT (study_key) DO NOTHING""",
            study | {"plan": Jsonb(study["plan"])},
        )
        studies.append(study)


def _study_observations(connection, studies, now):
    if not studies or not _exists(connection, "football_forward_observation"):
        return {}
    fixture_ids = sorted({item["fixture_id"] for study in studies for item in study["plan"]["fixtures"]})
    rows = connection.execute(
        """SELECT o.*, c.fixture_id AS independent_id, c.team_ids, c.provider_league_id,
                  c.condition_id, c.token_id, c.rule, c.kickoff AS contract_kickoff,
                  f.revision AS latest_fixture_revision, f.kickoff AS latest_fixture_kickoff,
                  f.fixture_state AS latest_fixture_state, f.info AS latest_fixture_info,
                  r.state AS result_state, r.won, r.home_goals, r.away_goals,
                  r.kickoff AS result_kickoff, r.observed_at AS result_observed_at,
                  r.source_url AS result_source_url, r.evidence AS result_evidence
           FROM football_notify.football_forward_observation o
           JOIN football_notify.football_forward_contract c
             USING (fixture_key, fixture_revision, market_id, outcome)
           LEFT JOIN football_notify.football_forward_result r
             ON (r.fixture_key, r.fixture_revision, r.market_id, r.outcome, r.model_version) =
                (o.fixture_key, o.fixture_revision, o.market_id, o.outcome, o.model_version)
             AND r.observed_at <= %s
           LEFT JOIN football_notify.fixture f ON f.id = o.fixture_key
           WHERE c.fixture_id = ANY(%s) AND o.model_version = %s
                 AND o.market_kind = 'match_winner' AND lower(o.outcome) = 'yes'
                 AND o.side = ANY(%s) AND o.observed_at <= %s
           ORDER BY o.observed_at, o.market_id""",
        (now, fixture_ids, MODEL_VERSION_SOCCER, list(OUTCOME_ORDER), now),
    ).fetchall()
    groups = defaultdict(list)
    for row in rows:
        groups[(row["league"], row["independent_id"])].append(row)
    return groups


def _model_probability(row):
    evidence = row["evidence"]
    variants = evidence.get("variants")
    if not isinstance(variants, list) or len(variants) != len(soccer_model.SWEEPS):
        return None
    numbers = [feed.number(item) for item in variants]
    if any(value is None or not 0 <= value <= 1 for value in numbers):
        return None
    probability = feed.number(row["model_probability"])
    if probability is None or not math.isclose(probability, soccer_model.median(numbers), abs_tol=1e-12):
        return None
    return probability


def _two_way_midpoint(row):
    bid, ask, market = (feed.number(row[field]) for field in ("bid_price", "ask_price", "market_price"))
    observed = row["book_observed_at"]
    if (row["market_price_basis"] != "clob_midpoint" or bid is None or ask is None or market is None
            or not 0 < bid < ask < 1 or not math.isclose((bid + ask) / 2, market, abs_tol=1e-12)
            or observed is None or not timedelta(0) <= row["observed_at"] - observed <= feed.BOOK_AGE):
        return None
    return market


def _valid_study_row(study, fixture, row):
    kickoff = feed.stamp(fixture["kickoff"])
    evidence = row["evidence"]
    model_at = row["model_observed_at"]
    digest = evidence.get("model_digest")
    fixture_at = feed.stamp(evidence.get("fixture_observed_at"))
    market_at = feed.stamp(evidence.get("market_observed_at"))
    latest_ids = feed.record(row["latest_fixture_info"])
    normal_start_update = (
        row["latest_fixture_state"] == "started" and row["latest_fixture_revision"] == row["fixture_revision"] + 1
        and all(feed.numeric_id(latest_ids.get(field)) == value for field, value in zip(
            ("fixture_id", "fotmob_home_id", "fotmob_away_id"), (fixture["fixture_id"], *fixture["team_ids"])))
    )
    return (
        row["model_version"] == study["model_version"] and row["league"] == study["league"]
        and row["line"] is None and row["team_side"] is None
        and row["team_ids"] == fixture["team_ids"]
        and row["provider_league_id"] == str(model.COMPETITIONS[study["league"]].fotmob_id)
        and row["rule"] == RULE and evidence.get("rule") == RULE
        and row["kickoff"] == row["contract_kickoff"] == row["latest_fixture_kickoff"] == kickoff
        and (row["fixture_revision"] == row["latest_fixture_revision"] or normal_start_update)
        and row["matchweek"] == fixture["round"]
        and row["condition_id"] == evidence.get("condition_id")
        and row["token_id"] == evidence.get("token_id")
        and evidence.get("fixture_source_url") == model.details_url(fixture["fixture_id"])
        and evidence.get("model_source_url") == study["calendar_source"]
        and evidence.get("direct_contract_verified") is True
        and isinstance(digest, str) and SHA256.fullmatch(digest)
        and model_at is not None and fixture_at is not None and market_at is not None
        and study["registered_at"] < row["observed_at"]
        and kickoff - forward.FORECAST_OPEN <= row["observed_at"] < kickoff - forward.FORECAST_END
        and timedelta(0) <= row["observed_at"] - model_at <= model.MODEL_AGE
        and fixture_at <= row["observed_at"] and market_at <= row["observed_at"]
    )


def _fixture_triplet(study, fixture, rows):
    eligible = [row for row in rows if _valid_study_row(study, fixture, row)]
    # Exactly one affirmative market per outcome from the same captured snapshot.
    if len(eligible) != 3 or {row["side"] for row in eligible} != set(OUTCOME_ORDER):
        return None
    if (len({row["market_id"] for row in eligible}) != 3 or len({row["condition_id"] for row in eligible}) != 3
            or len({(row["fixture_key"], row["fixture_revision"], row["observed_at"],
                     row["model_observed_at"], row["evidence"]["model_digest"]) for row in eligible}) != 1):
        return None
    by_side = {row["side"]: row for row in eligible}
    triplet = [by_side[side] for side in OUTCOME_ORDER]
    probabilities = [_model_probability(row) for row in triplet]
    if any(value is None for value in probabilities):
        return None
    prices = [_two_way_midpoint(row) for row in triplet]
    board = None
    if all(price is not None for price in prices):
        total = sum(prices)
        board = [price / total for price in prices]
    sources = {row["result_source_url"] for row in triplet}
    result_values = {(row["home_goals"], row["away_goals"]) for row in triplet}
    winner = None
    if (len(result_values) == 1 and len(sources) == 1
            and triplet[0]["result_source_url"] == model.details_url(fixture["fixture_id"])
            and all(_verified_result(row, fixture) for row in triplet)):
        home, away = next(iter(result_values))
        if type(home) is int and type(away) is int and min(home, away) >= 0:
            outcome = 0 if home > away else 2 if home < away else 1
            if all(row["won"] is (index == outcome) for index, row in enumerate(triplet)):
                winner = outcome
    return probabilities, board, winner


def _verified_result(row, fixture):
    if (row["result_state"] != "resolved" or row["result_kickoff"] != row["kickoff"]
            or row["result_observed_at"] <= row["kickoff"]):
        return False
    evidence = feed.record(row["result_evidence"])
    return (evidence.get("rule") == RULE and evidence.get("source_status") == "FT"
            and evidence.get("fixture_id") == fixture["fixture_id"] and evidence.get("team_ids") == fixture["team_ids"]
            and evidence.get("condition_id") == row["condition_id"] and evidence.get("token_id") == row["token_id"])


def _evaluate_study(study, groups):
    plan = study["plan"]
    by_fixture = {fixture["fixture_id"]: _fixture_triplet(study, fixture, groups.get(
        (study["league"], fixture["fixture_id"]), ())) for fixture in plan["fixtures"]}
    gate_e_ids = plan["gate_e_ids"]
    comparisons = [triplet for fixture_id in gate_e_ids if (triplet := by_fixture[fixture_id]) and triplet[1]]
    gate_e = scorecard.gate_e_market(
        [row[0] for row in comparisons], [row[1] for row in comparisons],
        mad_max=GATE_E_MAX_MAD, flips_max=0, min_events=GATE_E_FIXTURES[study["league"]],
    )
    completed_rounds = sum(
        all(by_fixture[item["fixture_id"]] is not None and by_fixture[item["fixture_id"]][2] is not None
            for item in plan["fixtures"] if item["round"] == number)
        for number in plan["rounds"]
    )
    holdout = [row for row in by_fixture.values() if row is not None and row[2] is not None]
    matched_holdout = [row for row in holdout if row[1] is not None]
    missing_ids = [fixture_id for fixture_id, triplet in by_fixture.items() if not triplet or triplet[2] is None]
    reasons = []
    if completed_rounds < FULL_MATCHWEEKS:
        reasons.append("wp_p4_complete_matchweeks_missing")
    if gate_e.verdict != "PASS":
        reasons.append("wp_p3_gate_e_failed" if gate_e.verdict == "FAIL" else "wp_p3_gate_e_incomplete")
    stats = {"study_key": study["study_key"], "registered_at": feed.utc_iso(study["registered_at"]),
             "calendar_sha256": study["calendar_sha256"], "expected_fixtures": len(by_fixture),
             "gate_e": gate_e.verdict, "gate_e_evidence": dict(gate_e.numbers),
             "complete_matchweeks": completed_rounds, "missing_fixtures": missing_ids,
             "holdout_fixtures": len(holdout), "holdout_market_pairs": len(matched_holdout),
             "holdout_model_log_loss": fmean(metrics.multiclass_log_loss(row[0], row[2], floor=1e-12) for row in holdout)
             if holdout else None,
             "holdout_model_brier": fmean(metrics.multiclass_brier(row[0], row[2]) for row in holdout)
             if holdout else None,
             "holdout_paired_model_log_loss": fmean(
                 metrics.multiclass_log_loss(row[0], row[2], floor=1e-12) for row in matched_holdout)
             if matched_holdout else None,
             "holdout_market_log_loss": fmean(
                 metrics.multiclass_log_loss(row[1], row[2], floor=1e-12) for row in matched_holdout)
             if matched_holdout else None}
    return reasons, stats


def _observation_counts(connection):
    if not _exists(connection, "football_forward_observation"):
        return {}
    rows = connection.execute(
        """SELECT league, market_kind, line, team_side, model_version,
                  count(*) AS observations,
                  count(*) FILTER (WHERE decision = 'unavailable') AS unavailable,
                  count(*) FILTER (WHERE decision = 'rejected') AS rejected,
                  count(*) FILTER (WHERE cohort = 'shadow' AND counterfactual_pass) AS shadow_screen_passes,
                  count(*) FILTER (WHERE decision = 'accepted') AS passing_official
           FROM football_notify.football_forward_observation
           GROUP BY league, market_kind, line, team_side, model_version"""
    ).fetchall()
    return {scope: row for row in rows if (scope := _decode_scope(row))}


def _decisions(studies, groups, observation_counts, score_scopes, promotions, missing_promotion_reason, previous):
    scope_studies = defaultdict(list)
    for study in studies:
        scope = study["league"], "match_winner", None, None, study["model_version"]
        scope_studies[scope].append(_evaluate_study(study, groups))
    shadow_scopes = {(league, "match_winner", None, None, MODEL_VERSION_SOCCER) for league in GATE_E_FIXTURES}
    conditional_scopes = {(league, "match_winner", None, None, MODEL_VERSION_SOCCER) for league in CLUB_CONTINENTAL}
    scopes = set(observation_counts) | shadow_scopes | conditional_scopes | set(score_scopes) | previous
    for scope in sorted(scopes, key=str):
        league, kind, line, _, version = scope
        count = observation_counts.get(scope, {})
        counters = {key: count.get(key, 0) for key in ("observations", "unavailable", "rejected",
                                                        "shadow_screen_passes", "passing_official")}
        reasons = []
        admission_rule = None
        statuses = scope_studies.get(scope, [])
        qualifying = [entry for entry in statuses if not entry[0]]
        stats = qualifying[-1][1] if qualifying else statuses[-1][1] if statuses else None
        formal = promotions.get(scope, {
            "status": "not_assessable",
            "rules_version": evaluation.PROMOTION_RULES_VERSION,
            "reason": (missing_promotion_reason if scope in score_scopes
                       else "managed_paper_entries_and_unattended_reconciliation_evidence_unavailable"),
        })
        if not gates.suspension_policy(kind, cross_league=league in CLUB_CONTINENTAL).eligible:
            status, reasons = "restricted", ["project_market_kind_restricted"]
        elif scope in EXISTING_SCOPES:
            status, reasons = "existing_approved", ["project_domestic_r23_approved"]
        elif scope in shadow_scopes:
            if qualifying:
                admission_rule = POLICY_VERSION
                status, reasons = "market_admitted", ["wp_p3_gate_e_pass", "wp_p4_prospective_holdout_complete",
                                                       "wp_p3_a_to_d_same_validated_model"]
            else:
                status = "shadow"
                reasons = statuses[-1][0] if statuses else ["prospective_matchweeks_not_registered"]
        elif league in CLUB_CONTINENTAL and kind == "match_winner":
            status, reasons = "conditional", ["od_f_ucl_uel_md5_resume", "wp_p5_bridge_and_od_f_scan_evidence_required"]
        elif league in NATIONAL and kind == "match_winner":
            status, reasons = "restricted", ["wp_intl_match_winner_research_only"]
        elif league in NATIONAL and kind == "total" and line == Decimal("2.5"):
            status, reasons = "shadow", ["wp_intl_march_2027_at_earliest", "wp_intl_over_2_5_market_gate_unverified"]
        elif league in DOMESTIC and version != MODEL_VERSION_SOCCER:
            status, reasons = "shadow", ["wp_p4_validation_and_optimizer_role_unverified"]
        elif scope in score_scopes:
            if formal["status"] == evaluation.VERDICT_PASS:
                admission_rule = evaluation.PROMOTION_RULES_VERSION
                status, reasons = "market_admitted", ["od_g_six_criteria_pass"]
            elif formal.get("paused"):
                status, reasons = "restricted", ["managed_queue_paused"]
            else:
                status, reasons = "shadow", ["od_g_exact_scope_not_passed"]
        else:
            status, reasons = "shadow", ["exact_scope_paper_admission_evidence_unverified"]
        registration = score_scopes.get(scope, {}).get("registered")
        yield {
            "scope": _scope_document(scope), "status": status, "reasons": reasons, "observation_counts": counters,
            "study": stats, "policy_version": POLICY_VERSION,
            "admission_rule": admission_rule, "formal_promotion": formal,
            "historical_research": score_scopes.get(scope, {}).get("history"),
            "registered_at": feed.utc_iso(registration) if registration else None,
        }


def _record_decision(connection, decision, now):
    scope_sha = feed.document_digest(decision["scope"])
    sha = feed.document_digest(decision)
    connection.execute(
        """WITH changed AS (
             INSERT INTO football_notify.football_admission_state
                (scope_key, updated_at, decision_sha256, decision) VALUES (%s,%s,%s,%s)
             ON CONFLICT (scope_key) DO UPDATE SET updated_at = excluded.updated_at,
                 decision_sha256 = excluded.decision_sha256, decision = excluded.decision
             WHERE football_admission_state.decision_sha256 <> excluded.decision_sha256
             RETURNING scope_key, updated_at, decision_sha256, decision
           )
           INSERT INTO football_notify.football_admission_history
             (scope_key, evaluated_at, decision_sha256, decision)
           SELECT scope_key, updated_at, decision_sha256, decision FROM changed""",
        (scope_sha, now, sha, Jsonb(decision)),
    )


def evaluate(connection):
    """Declare prospective studies and update idempotent paper decisions."""
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(%s, %s)", (store.LOCK_NAMESPACE, ADMISSION_LOCK))
        if not _exists(connection, "football_admission_state"):
            connection.execute(SCHEMA)
        now = store.db_now(connection)
        previously_active = active_scopes(connection)
        studies = connection.execute(
            """SELECT * FROM football_notify.football_admission_study WHERE model_version = %s
               ORDER BY registered_at, study_key""", (MODEL_VERSION_SOCCER,),
        ).fetchall()
        _declare_studies(connection, studies, now)
        observations = _study_observations(connection, studies, now)
        score_scopes = _score_scopes(connection, now)
        promotions, missing_promotion_reason = _formal_promotions(connection, score_scopes, now)
        decisions = tuple(_decisions(studies, observations, _observation_counts(connection),
                                     score_scopes, promotions, missing_promotion_reason, _previous_scopes(connection)))
        for decision in decisions:
            _record_decision(connection, decision, now)
    admitted = [decision["scope"] for decision in decisions if decision["status"] == "market_admitted"]
    return {"policy_version": POLICY_VERSION, "evaluated_at": feed.utc_iso(now),
            "counts": dict(Counter(decision["status"] for decision in decisions)),
            "admitted": admitted,
            "newly_admitted": [scope for scope in admitted if _decode_scope(scope) not in previously_active],
            "studies": len(studies), "decisions": decisions}
