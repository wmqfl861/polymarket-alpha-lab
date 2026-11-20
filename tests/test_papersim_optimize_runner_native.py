"""Opt-in native MO-6 proofs: the optimize write path end to end (T6).

Runs only inside the disposable native cluster environment (the CI
throwaway papersim partition or an explicitly assembled local
cluster).  Never touches a business cluster: every test builds its own
private managed-shaped database on the throwaway cluster.  Proves:

- ``optimize init`` (through the real CLI main) initializes every
  family's lineages and a second invocation is idempotent (all SKIP);
- ``optimize run`` writes the complete receipt/write sequence: two
  receipt phases, input + artifact snapshots, the optimization_runs
  row; re-running the SAME command (bumped attempt) is idempotent
  (same run row, byte-identical artifact, no duplicate anything);
- a budget-exhausted run (fresh seed, tiny trial cap per the
  pre-registered retry discipline) lands RUN_FAILED with a FAILED
  receipt and a partial artifact;
- the registration write path the runner uses (optimizer-sourced spec
  row + CANDIDATE role event with the pre-registered forward plan)
  round-trips through the registry and shows up in ``optimize status``.

The worktree must be clean (run_and_record enforces FX-03); in CI this
file runs post-merge.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from hashlib import sha256
from pathlib import Path
import uuid

import pytest

from polymarket_alpha_lab.papersim import migrations as pm
from polymarket_alpha_lab.papersim import store
from polymarket_alpha_lab.papersim.cli import main as cli_main

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN,
                            reason='explicit native papersim proof is opt-in')

COMMIT = 'd' * 40
AT = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.timezone.utc)
#: A candidate registration AFTER initialization: the registry treats
#: consecutive same-effective_at events as ONE transaction batch, so a
#: registration sharing the initialization stamp would fold into an
#: illegal three-event initialization batch.
LATER = AT + dt.timedelta(hours=2)


def _connect(dsn, *, autocommit=True):
    psycopg = pytest.importorskip('psycopg')
    connection = psycopg.connect(dsn)
    connection.autocommit = autocommit
    return connection


def _rebind_database(dsn: str, database: str) -> str:
    return ' '.join(f'dbname={database}' if token.startswith('dbname=') else token
                    for token in dsn.split(' '))


def _migrated_app() -> str:
    """A fully migrated private database; returns the application DSN."""
    psycopg = pytest.importorskip('psycopg')
    admin = _connect(OWNER_DSN)
    name = 'pal_papersim_mo6run_' + uuid.uuid4().hex[:10]
    with admin.cursor() as cursor:
        cursor.execute("SELECT system_identifier::text FROM pg_control_system()")
        system_id = cursor.fetchone()[0]
        cursor.execute(f"CREATE DATABASE {name} OWNER pal_owner")
    admin.close()
    owner_dsn = _rebind_database(OWNER_DSN, name)
    bootstrap = psycopg.connect(owner_dsn)
    bootstrap.autocommit = True
    with bootstrap.cursor() as cursor:
        from polymarket_alpha_lab.project_postgres import sql
        cursor.execute(sql.bootstrap_sql(uuid.uuid4().hex, 'b' * 64, system_id))
    bootstrap.close()
    owner = _connect(owner_dsn, autocommit=False)
    report = pm.apply_pending_migrations(owner, ROOT)
    assert report['pending_before'] == report['applied']
    assert report['applied'] >= 6  # the six MO-2-era papersim migrations
    owner.commit()
    owner.close()
    return owner_dsn.replace('user=pal_owner', 'user=pal_app')


def _mini_snapshot() -> bytes:
    """Eight games across eight dates straddling the NBA holdout window
    (2026-11-04): walk-forward dates 10-30..11-03, holdout 11-04..11-06."""

    def row(date_s, opponent, is_home, hp, ap):
        return {
            "date": date_s, "home": is_home, "opponent": opponent,
            "poss": 100.0, "pts": hp, "opp_pts": ap, "ortg": 112.0,
            "drtg": 112.0, "rest_days": 1, "b2b": False,
        }

    games = [
        ("2026-10-30", "AAA", "BBB", 113, 110),
        ("2026-10-31", "BBB", "AAA", 111, 108),
        ("2026-11-01", "AAA", "BBB", 112, 109),
        ("2026-11-02", "BBB", "AAA", 108, 104),
        ("2026-11-03", "AAA", "BBB", 120, 100),
        ("2026-11-04", "BBB", "AAA", 105, 99),
        ("2026-11-05", "AAA", "BBB", 114, 111),
        ("2026-11-06", "BBB", "AAA", 103, 101),
    ]
    teams: dict[str, dict] = {}
    for date_s, home, away, hp, ap in games:
        teams.setdefault(home, {"display": home, "games": []})
        teams.setdefault(away, {"display": away, "games": []})
        teams[home]["games"].append(row(date_s, away, True, hp, ap))
        teams[away]["games"].append(row(date_s, home, False, ap, hp))
    return json.dumps({
        "generated_at": "2026-11-07T00:00:00+00:00",
        "season": "2026-27",
        "teams": teams,
        "injuries": {},
    }, sort_keys=True).encode("utf-8")


def _run_optimize(dsn: str, argv, capsys) -> dict:
    assert cli_main(["optimize"] + argv + ["--dsn", dsn,
                                           "--repo-root", str(ROOT)]) == 0
    return json.loads(capsys.readouterr().out)


@NATIVE
def test_native_init_run_receipts_and_idempotent_replay(capsys, tmp_path):
    dsn = _migrated_app()

    # init: every family with targets gets BASELINE+ACTIVE; league-bridge
    # has no lineages; the second call is a full SKIP.
    report = _run_optimize(dsn, [
        "init", "--actor", "op", "--effective-at", AT.isoformat(),
    ], capsys)
    assert report
    assert all(
        item["state"] in ("initialized", "skipped")
        for item in report.values()
    )
    again = _run_optimize(dsn, [
        "init", "--actor", "op", "--effective-at", AT.isoformat(),
    ], capsys)
    assert {item["state"] for item in again.values()} == {"skipped"}
    assert "club-soccer-frozen/over_under" in report
    assert "nba-pace-efficiency/moneyline" in report

    # run: the full write sequence on the frozen NBA profile (1125-point
    # registered grid; the mini sample cannot meet the 300-match floor,
    # so the honest outcome is INSUFFICIENT_DATA or NO_IMPROVEMENT).
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(_mini_snapshot())
    document = tmp_path / "input.json"
    document.write_text(json.dumps({
        "inputs": [{
            "path": str(snapshot),
            "kind": "espn_nba_snapshot",
            "sha256": sha256(snapshot.read_bytes()).hexdigest(),
        }],
    }), encoding="utf-8")
    first = _run_optimize(dsn, [
        "run", "--family", "nba-pace-efficiency",
        "--as-of", "2026-11-07T00:00:00Z",
        "--input", str(document), "--seed", "1",
    ], capsys)
    assert first["outcome"] in ("INSUFFICIENT_DATA", "NO_IMPROVEMENT")
    assert first["inserted"] is True
    artifacts_sha = first["artifacts_sha256"]

    # Same command, bumped attempt: content-idempotent healing.
    second = _run_optimize(dsn, [
        "run", "--family", "nba-pace-efficiency",
        "--as-of", "2026-11-07T00:00:00Z",
        "--input", str(document), "--seed", "1",
        "--attempt", "2",
    ], capsys)
    assert second["inserted"] is False
    assert second["optimization_id"] == first["optimization_id"]
    assert second["artifacts_sha256"] == artifacts_sha

    psycopg = pytest.importorskip('psycopg')
    app = _connect(dsn)
    with app.cursor() as cursor:
        cursor.execute("SELECT outcome, artifacts_sha256, trial_count FROM "
                       "papersim.optimization_runs")
        runs = cursor.fetchall()
        assert len(runs) == 1
        assert runs[0][0] in ("INSUFFICIENT_DATA", "NO_IMPROVEMENT")
        assert runs[0][1] == artifacts_sha
        cursor.execute("SELECT phase, attempt FROM "
                       "papersim.command_run_events "
                       "WHERE command = 'optimize-run' ORDER BY run_event_id")
        phases = cursor.fetchall()
        # Seed-1 run + its idempotent replay (attempt 2).
        assert [phase for phase, _ in phases] == [
            "STARTED", "SUCCEEDED", "STARTED", "SUCCEEDED",
        ]
        assert [attempt for _, attempt in phases] == [1, 1, 2, 2]
        cursor.execute("SELECT count(*) FROM papersim.snapshots "
                       "WHERE kind = 'optimizer_run_artifact'")
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT count(*) FROM papersim.snapshots "
                       "WHERE kind = 'espn_nba_snapshot'")
        assert cursor.fetchone()[0] == 1
    app.close()

    # Budget exhaustion (fresh seed per the pre-registered retry
    # discipline: budget failures change the run content, so a retry
    # takes a new identity): RUN_FAILED row, FAILED receipt, partial
    # artifact preserved.
    starved = _run_optimize(dsn, [
        "run", "--family", "nba-pace-efficiency",
        "--as-of", "2026-11-07T00:00:00Z",
        "--input", str(document), "--seed", "2", "--max-trials", "3",
    ], capsys)
    assert starved["outcome"] == "RUN_FAILED"
    assert starved["error_code"] == "budget_exhausted"
    app = _connect(dsn)
    with app.cursor() as cursor:
        cursor.execute("SELECT outcome FROM papersim.optimization_runs "
                       "ORDER BY optimization_id")
        outcomes = [row[0] for row in cursor.fetchall()]
        assert outcomes.count("RUN_FAILED") == 1
        cursor.execute("SELECT phase FROM papersim.command_run_events "
                       "WHERE command = 'optimize-run' "
                       "ORDER BY run_event_id")
        all_phases = [row[0] for row in cursor.fetchall()]
        # The seed-2 budget failure appends its own STARTED + FAILED
        # receipts under its own operation key (the default key carries
        # the seed).
        assert all_phases == [
            "STARTED", "SUCCEEDED", "STARTED", "SUCCEEDED",
            "STARTED", "FAILED",
        ]
        cursor.execute("SELECT content FROM papersim.snapshots "
                       "WHERE kind = 'optimizer_run_artifact'")
        artifacts = [row[0] for row in cursor.fetchall()]
        failed = [bytes(item) for item in artifacts
                  if b'"budget_exhausted":true' in bytes(item)]
        assert len(failed) == 1
        document_failed = json.loads(failed[0].decode("utf-8"))
        assert document_failed["budget_exhausted"] is True
        assert "failed_phase" in document_failed
    app.close()


@NATIVE
def test_native_registration_write_path_and_status(capsys):
    from polymarket_alpha_lab.papersim import store_psycopg
    from polymarket_alpha_lab.papersim.optimize import runner
    from polymarket_alpha_lab.papersim.optimize.families.intl import INTL_ELO

    dsn = _migrated_app()
    _run_optimize(dsn, [
        "init", "--actor", "op", "--effective-at", AT.isoformat(),
    ], capsys)

    # The exact registration writes run_and_record performs when the
    # stage-1 gates pass: an optimizer-sourced spec row plus a CANDIDATE
    # role event carrying the pre-registered forward plan.
    app = _connect(dsn)
    candidate = INTL_ELO.spec_with({"slope": 0.55})
    assert candidate.spec_sha256() != INTL_ELO.baseline_spec().spec_sha256()
    spec_row = store_psycopg.register_model_spec(
        app, candidate, source="optimizer", code_commit=COMMIT)
    assert spec_row["inserted"] is True
    role = store_psycopg.record_role_events(app, store.RoleEventBatch(events=(
        store.RoleEventSubmission(
            lineage="intl-elo-poisson/match_winner",
            spec_sha256=candidate.spec_sha256(),
            role="CANDIDATE",
            effective_at=LATER,
            reason="native proof: stage-1 gates passed (rules v1)",
            forward_plan=runner._forward_plan(
                "intl-elo-poisson", "match_winner", AT),
            actor="auto:v1",
            code_commit=COMMIT,
        ),
    )))
    assert role["inserted"] is True
    # Idempotent replay of the same batch: the original ids return.
    replay = store_psycopg.record_role_events(app, store.RoleEventBatch(events=(
        store.RoleEventSubmission(
            lineage="intl-elo-poisson/match_winner",
            spec_sha256=candidate.spec_sha256(),
            role="CANDIDATE",
            effective_at=LATER,
            reason="native proof: stage-1 gates passed (rules v1)",
            forward_plan=runner._forward_plan(
                "intl-elo-poisson", "match_winner", AT),
            actor="auto:v1",
            code_commit=COMMIT,
        ),
    )))
    assert replay["inserted"] is False
    # P1-1: the stored plan is the LINEAGE's registered row.
    with app.cursor() as cursor:
        cursor.execute("SELECT forward_plan FROM papersim.model_role_events "
                       "WHERE lineage = 'intl-elo-poisson/match_winner' "
                       "AND role = 'CANDIDATE'")
        plan = cursor.fetchone()[0]
    assert plan["first_look_min_events"] == 60
    assert plan["final_look_min_events"] == 120
    assert plan["max_shadow_days"] == 365
    app.close()

    report = _run_optimize(dsn, ["status", "--lineage",
                                 "intl-elo-poisson/match_winner"], capsys)
    lineage = report["lineages"][0]
    assert lineage["roles"]["candidates"] == [candidate.spec_sha256()]
    assert lineage["frozen"] is False
    assert lineage["forward_sample_count"] is None  # MO-7, honest None
