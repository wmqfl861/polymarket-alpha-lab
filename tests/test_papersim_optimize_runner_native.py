"""Opt-in native MO-6 proofs: the optimize write path end to end (T6).

Runs only inside the disposable native cluster environment (the CI
throwaway papersim partition or an explicitly assembled local
cluster).  Never touches a business cluster: every test builds its own
private managed-shaped database on the throwaway cluster.  Proves:

- ``optimize init`` (through the real CLI main) initializes every
  family's lineages and a second invocation is idempotent (all SKIP);
- ``optimize run`` writes two receipt phases, input and artifact snapshots
  and one optimization run. The same command replays its IDs and bytes.
  Substituting a different manifest under the same experiment is a conflict;
- on a separate fresh archive, a real compute deadline seals the observed
  three-trial prefix and replays the failed result after a restart;
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
from tests.test_papersim_optimize_registered_runner_native import managed_repo, managed_runtime

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
    from polymarket_alpha_lab.papersim.store_psycopg import connect_explicit

    pytest.importorskip('psycopg')
    connection = connect_explicit(dsn)
    connection.autocommit = autocommit
    return connection


def _rebind_database(dsn: str, database: str) -> str:
    return ' '.join(f'dbname={database}' if token.startswith('dbname=') else token
                    for token in dsn.split(' '))


def _migrated_app() -> str:
    """A fully migrated private database; returns the application DSN."""
    admin = _connect(OWNER_DSN)
    name = 'pal_papersim_mo6run_' + uuid.uuid4().hex[:10]
    with admin.cursor() as cursor:
        cursor.execute("SELECT system_identifier::text FROM pg_control_system()")
        system_id = cursor.fetchone()[0]
        cursor.execute(f"CREATE DATABASE {name} OWNER pal_owner")
    admin.close()
    owner_dsn = _rebind_database(OWNER_DSN, name)
    bootstrap = _connect(owner_dsn)
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


def _registered_request(conn, directory, *, source_bytes, filename):
    from polymarket_alpha_lab.papersim import snapshots
    from polymarket_alpha_lab.papersim.optimize.runner import historical_protocol

    family = "nba-pace-efficiency"
    source = {"role": "nba_snapshot", "key": "snapshot", "kind": "espn_nba_snapshot",
              "sha256": sha256(source_bytes).hexdigest(), "byte_length": len(source_bytes),
              "schema_version": 1}
    manifest = {"schema_version": 1, "protocol": historical_protocol(family), "sources": [source]}
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    with conn.transaction():
        snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
            content=source_bytes, kind=source["kind"], source="test-nba-archive", fetched_at=AT))
        snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
            content=encoded, kind="optimizer_experiment_manifest", source="test-nba-protocol", fetched_at=AT))
    request = directory / filename
    request.write_text(json.dumps({
        "schema_version": 1, "manifest_sha256": sha256(encoded).hexdigest(),
        "manifest_byte_length": len(encoded),
    }), encoding="utf-8")
    return request


def _run_optimize(dsn: str, argv, capsys) -> dict:
    assert cli_main(["optimize"] + argv + ["--dsn", dsn,
                                           "--repo-root", str(ROOT)]) == 0
    return json.loads(capsys.readouterr().out)


@NATIVE
def test_native_init_run_receipts_and_idempotent_replay(capsys, tmp_path, monkeypatch):
    from polymarket_alpha_lab.papersim import snapshots
    from polymarket_alpha_lab.papersim.optimize import runner
    from tests.test_papersim_optimize_registered_runner_native import _nba_fixture_snapshot, _preview_storage_facts

    dsn = _migrated_app()
    init = ["init", "--actor", "op", "--effective-at", AT.isoformat()]
    report = _run_optimize(dsn, init, capsys)
    assert report
    assert all(item["state"] in ("initialized", "skipped") for item in report.values())
    with _connect(dsn) as conn:
        initialized = _preview_storage_facts(conn)
    again = _run_optimize(dsn, init, capsys)
    assert {item["state"] for item in again.values()} == {"skipped"}
    assert "club-soccer-frozen/over_under" in report
    assert "nba-pace-efficiency/moneyline" in report
    with _connect(dsn) as conn:
        assert _preview_storage_facts(conn) == initialized

    raw = _nba_fixture_snapshot()
    with _connect(dsn) as conn:
        request = _registered_request(conn, tmp_path, source_bytes=raw, filename="request.json")
    registered = request.read_bytes()
    argv = ["run", "--family", "nba-pace-efficiency", "--as-of", "2026-11-10T05:00:00Z",
            "--seed", "20261120", "--input-manifest", str(request)]
    execute = runner.execute_optimization_run
    observed = []

    def observe_search(**options):
        evaluate = options["evaluate"]

        def observe(spec, fold, targets):
            result = evaluate(spec, fold, targets)
            observed.append((spec.spec_sha256(), fold.block_id))
            return result

        return execute(**{**options, "evaluate": observe})

    monkeypatch.setattr(runner, "execute_optimization_run", observe_search)
    first = _run_optimize(dsn, argv, capsys)
    assert first["outcome"] in ("INSUFFICIENT_DATA", "NO_IMPROVEMENT")
    assert first["replayed"] is False
    assert observed
    artifact = first["artifacts_sha256"]
    with _connect(dsn) as conn:
        runs = conn.execute("SELECT optimization_id, outcome, artifacts_sha256, trial_count FROM "
                            "papersim.optimization_runs").fetchall()
        assert len(runs) == 1
        assert runs[0][:3] == (first["optimization_id"], first["outcome"], artifact)
        assert runs[0][3] == len(observed)
        events = conn.execute("SELECT phase, attempt, run_event_id FROM papersim.command_run_events "
                              "ORDER BY run_event_id").fetchall()
        assert events == [("STARTED", 1, first["started_event_id"]),
                          ("SUCCEEDED", 1, first["terminal_event_id"])]
        assert conn.execute("SELECT count(*) FROM papersim.snapshots "
                            "WHERE kind = 'optimizer_run_artifact'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM papersim.snapshots "
                            "WHERE kind = 'espn_nba_snapshot'").fetchone()[0] == 1
        original = snapshots.read_snapshot(conn, artifact)["content"]
        assert sha256(original).hexdigest() == artifact
        assert len(original) == first["artifact_byte_length"]
        sealed = _preview_storage_facts(conn)

    def forbidden(**_):
        pytest.fail("replay or conflicting manifest reached computation")

    monkeypatch.setattr(runner, "historical_run_arguments", forbidden)
    sealed_fields = ("optimization_id", "started_event_id", "terminal_event_id", "started_event_key",
                     "terminal_event_key", "artifacts_sha256", "artifact_byte_length", "outcome")

    def prove_replay(attempt, facts):
        replay = _run_optimize(dsn, [*argv, "--attempt", str(attempt)], capsys)
        assert replay["replayed"] is True
        assert {key: replay[key] for key in sealed_fields} == {key: first[key] for key in sealed_fields}
        assert len(observed) == runs[0][3]
        with _connect(dsn) as conn:
            assert snapshots.read_snapshot(conn, replay["artifacts_sha256"])["content"] == original
            assert _preview_storage_facts(conn) == facts

    for attempt in (1, 2):
        prove_replay(attempt, sealed)

    # Registration is test setup. Swapping the pointer still names the same run key.
    with _connect(dsn) as conn:
        other = _registered_request(conn, tmp_path, source_bytes=raw + b"\n", filename="other.json")
        before_conflict = _preview_storage_facts(conn)
    assert len(before_conflict["snapshots"]) == len(sealed["snapshots"]) + 2
    assert set(sealed["snapshots"]) <= set(before_conflict["snapshots"])
    assert all(before_conflict[table] == sealed[table] for table in sealed if table != "snapshots")
    assert other.read_bytes() != registered
    request.write_bytes(other.read_bytes())
    for extra in (["--dry-run"], []):
        assert cli_main(["optimize", *argv, "--dsn", dsn, "--repo-root", str(ROOT), *extra]) == 1
        captured = capsys.readouterr()
        assert not captured.out
        assert "FAIL: papersim_optimize_request_conflict " in captured.err
        assert len(observed) == runs[0][3]
        with _connect(dsn) as conn:
            assert _preview_storage_facts(conn) == before_conflict
    request.write_bytes(registered)
    prove_replay(2, before_conflict)


@pytest.mark.skipif(not os.environ.get("POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX"),
                    reason="native managed restart proof requires the qualified PG18 binary prefix")
@pytest.mark.parametrize("lost_reply", [False, True], ids=["acknowledged", "commit-reply-lost"])
@NATIVE
def test_native_cli_seals_three_completed_trials_after_real_deadline(
        managed_repo, managed_runtime, tmp_path, lost_reply):
    import time

    from polymarket_alpha_lab.papersim.optimize.search import TRIAL_COMPLETED
    from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
    from tests.test_papersim_optimize_registered_runner_native import (
        _advance_past, _invocation, _nba_archived_forecasts, _nba_fixture_snapshot, _process, _reply, _wait_path,
    )

    root, head = managed_repo
    owner, app = managed_runtime
    request = _registered_request(app, tmp_path, source_bytes=_nba_fixture_snapshot(), filename="request.json")
    archive = str(root), request
    trace, barrier, clock = (tmp_path / filename for filename in ("observed.json", "compute.hold", "clock.json"))
    finished, artifact_path = (tmp_path / filename for filename in ("finished.json", "archived.json"))
    _invocation("nba-cli", archive, command="init")
    before_facts = _invocation("nba-facts", archive)
    before = _invocation("nba-read", archive)
    assert before["row"] is None and before["events"] == []
    with _process("nba-cli", archive, trace=str(trace), barrier=str(barrier), clock=str(clock),
                  finished=str(finished), barrier_trial=3, lose_commit_reply=lost_reply) as process:
        _wait_path(barrier, process)
        times = json.loads(clock.read_bytes())
        assert time.monotonic_ns() < times["compute"]
        _advance_past(times["compute"])
        barrier.with_suffix(".release").touch()
        failed = _reply(process, 1)
    assert failed["outcome"] == "RUN_FAILED"
    assert failed["error_code"] == "papersim_optimize_budget_exhausted"
    assert failed["replayed"] is lost_reply
    observed = json.loads(trace.read_bytes())
    completion = json.loads(finished.read_bytes())
    assert times["compute"] < completion.pop("acknowledged_ns") < times["hard"]
    assert completion == observed
    assert "fit" in observed["model_calls"] and "prediction" in observed["model_calls"]
    assert len(observed["callbacks"]) == 3

    after = _invocation("nba-read", archive, artifact_output=str(artifact_path))
    assert after["events"] == [["STARTED", 1, failed["started_event_id"]],
                               ["FAILED", 1, failed["terminal_event_id"]]]
    assert after["row"]["optimization_id"] == failed["optimization_id"]
    assert after["row"]["outcome"] == "RUN_FAILED" and after["row"]["trial_count"] == 3
    archived = artifact_path.read_bytes()
    assert len(archived) == failed["artifact_byte_length"] == after["row"]["artifact_byte_length"]
    assert sha256(archived).hexdigest() == failed["artifacts_sha256"] == after["row"]["artifacts_sha256"]
    payload = json.loads(archived)
    assert payload["request"]["code_commit"] == head
    assert payload["outcome"] == "RUN_FAILED"
    assert payload["budget"]["stopping_causes"] == ["compute_time"]
    assert payload["budget"]["stopping_stage"] == "search"
    assert payload["rejections"] == {}
    pure = payload["pure"]
    assert pure["holdout"]["callbacks"] == []
    assert [row["status"] for row in pure["trials"]] == [TRIAL_COMPLETED] * 3
    assert [[row["spec_sha256"], row["fold_block_id"], row["targets"]]
            for row in pure["trials"]] == observed["callbacks"]
    witnessed = {(row["fold"], row["spec"], row["target"]): (row["predictions"], row["refusals"])
                 for row in observed["batches"]}
    forecasts = _nba_archived_forecasts(pure)
    assert forecasts == witnessed
    assert any(predictions for predictions, _ in forecasts.values())
    after_facts = _invocation("nba-facts", archive)
    added = {"snapshots": 1, "optimization_runs": 1, "command_run_events": 2}
    for table, original in before_facts.items():
        assert set(original) <= set(after_facts[table])
        assert len(after_facts[table]) - len(original) == added.get(table, 0)

    owner.close()
    app.close()
    postgres = ProjectPostgres(root)
    assert postgres.down()["status"] == "stopped"
    assert postgres.up()["status"] == "running"
    for attempt in (1, 2):
        replay_trace = tmp_path / f"replay-{attempt}.json"
        replay = _invocation("nba-cli", archive, code=1, attempt=attempt,
                             forbid_compute=True, finished=str(replay_trace))
        assert replay == {**failed, "replayed": True}
        replayed = json.loads(replay_trace.read_bytes())
        assert replayed["model_calls"] == replayed["callbacks"] == replayed["batches"] == []
        readback = tmp_path / f"readback-{attempt}.json"
        assert _invocation("nba-read", archive, artifact_output=str(readback)) == after
        assert readback.read_bytes() == archived
        assert _invocation("nba-facts", archive) == after_facts


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
