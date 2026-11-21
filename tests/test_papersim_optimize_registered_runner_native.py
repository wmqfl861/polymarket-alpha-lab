"""Independent-process MO-6 proofs on the disposable PG18 papersim partition."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import re
from selectors import DefaultSelector, EVENT_READ
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlencode

import pytest

from tests.test_papersim_optimize_registry_native import (
    NATIVE, OWNER_DSN, ROOT, _connect, _migrated_app, _rebind_database,
)
from tests.test_papersim_optimize_runner import Harness, SyntheticFamily
from tests.test_papersim_report_store_native import (
    _managed_test_postgres, pytestmark as MANAGED_NATIVE, repo as managed_repo,
)

AT = datetime(2026, 11, 19, 0, tzinfo=UTC)
COMMIT = 'b' * 40
SEED = 20261120
LINEAGE = 'runner-synth/synthetic'
SOURCE_KIND = 'optimizer_native_fixture'
NS = 1_000_000_000
LEDGER = ('scan_runs', 'candidates', 'entries', 'bankroll_events', 'promotion_decisions',
          'optimizer_control_events')
TABLES = ('snapshots', 'optimization_runs', 'model_specs', 'model_role_events', 'command_run_events')
WRITERS = ('artifact', 'run', 'spec', 'role', 'terminal')


def _adapter():
    from polymarket_alpha_lab.papersim import store_psycopg

    return store_psycopg


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def _register(conn, directory, *, schedule='native-fixture-v1', cutoff=AT, seed=SEED,
              targets=('synthetic',), variant='original'):
    from polymarket_alpha_lab.papersim import snapshots

    content = _json({'research_fixture': 'managed optimizer integration', 'identity': schedule,
                     'source_variant': variant})
    digest = sha256(content).hexdigest()
    document = {
        'schema_version': 1,
        'protocol': {
            'family': SyntheticFamily.family_id, 'rule_version': 'v1',
            'procedure_version': SyntheticFamily.procedure_version, 'schedule_id': schedule,
            'targets': list(targets), 'seed': seed, 'data_cutoff': cutoff.isoformat(),
            'source_calendar': 'test UTC', 'cohort': 'witnessed fixture schedule',
        },
        'sources': [{'role': 'evidence', 'key': 'native-fixture.json',
                     'sha256': digest, 'byte_length': len(content),
                     'kind': SOURCE_KIND, 'schema_version': 1}],
    }
    manifest_bytes = _json(document)
    with conn.transaction():
        for raw, kind in ((content, SOURCE_KIND), (manifest_bytes, 'optimizer_experiment_manifest')):
            snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
                content=raw, kind=kind, source='native-fixture', fetched_at=AT,
            ))
    request = directory / f'{schedule}-{variant}-request.json'
    request.write_bytes(_json({'manifest': document, 'digest': sha256(manifest_bytes).hexdigest(),
                               'size': len(manifest_bytes), 'refs': {digest: len(content)}}))
    return request


def _worker_connection(name):
    return _connect(_rebind_database(OWNER_DSN, name).replace('user=pal_owner', 'user=pal_app'))


@pytest.fixture
def archive(tmp_path):
    from psycopg import sql

    _, app = _migrated_app()
    name = next(token.split('=', 1)[1] for token in app.split(' ') if token.startswith('dbname='))
    try:
        with _worker_connection(name) as conn:
            request = _register(conn, tmp_path)
        yield name, request
    finally:
        with _connect(OWNER_DSN) as admin:
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


@pytest.fixture
def managed_runtime(managed_repo, monkeypatch):
    root, _ = managed_repo
    with _managed_test_postgres(root, monkeypatch) as connections:
        yield connections
    for name in ('.local', 'runtime'):
        transient = root / name
        if transient.is_dir():
            shutil.rmtree(transient)


def _call(action, archive, **options):
    name, request = archive
    return [sys.executable, '-m', 'tests.test_papersim_optimize_registered_runner_native',
            action, name, str(request), json.dumps(options)]


@contextmanager
def _process(action, archive, **options):
    with subprocess.Popen(_call(action, archive, **options), cwd=ROOT, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True) as process:
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


def _reply(process, code=0):
    output, stderr = process.communicate(timeout=25)
    assert output, stderr
    reply = json.loads(output)
    assert process.returncode == code, {name: reply.get(name) for name in ('error_code', 'stage', 'outcome', 'counts')}
    return reply


def _invocation(action, archive, *, code=0, **options):
    with _process(action, archive, **options) as process:
        return _reply(process, code)


def _wait_path(path: Path, process=None):
    end = time.monotonic() + 15
    while not path.exists():
        if process is not None and process.poll() is not None:
            raise AssertionError(process.communicate(timeout=5))
        assert time.monotonic() < end, f'no test barrier at {path.name}'
        time.sleep(0.01)


def _barrier(path):
    path.touch()
    _wait_path(path.with_suffix('.release'))


def _counts(conn):
    return {table: conn.execute(f'SELECT count(*) FROM papersim.{table}').fetchone()[0]
            for table in (*TABLES, *LEDGER)}


def _preview_storage_facts(conn):
    tables = (*TABLES, *LEDGER, 'shadow_predictions', 'bankroll_checks', 'settlements')
    # Row digests include identity, metadata and the exact bytea content.
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        return {
            table: [sha256(content.encode()).hexdigest() for (content,) in conn.execute(
                f'SELECT to_jsonb(stored)::text FROM papersim.{table} AS stored ORDER BY 1')]
            for table in tables
        }


def _seed_candidate(conn, theta, instant, *, target='synthetic', actor='operator'):
    from polymarket_alpha_lab.papersim import store

    adapter = _adapter()
    spec = replace(SyntheticFamily().baseline_spec(), params={'theta': theta})
    if adapter.load_model_spec(conn, spec.spec_sha256()) is None:
        adapter.register_model_spec(conn, spec, source='manual', code_commit=COMMIT)
    event = store.RoleEventSubmission(
        lineage=f'runner-synth/{target}', spec_sha256=spec.spec_sha256(), role='CANDIDATE',
        effective_at=instant, reason='native integration fixture', forward_plan={},
        actor=actor, code_commit=COMMIT,
    )
    return adapter.record_role_events(conn, store.RoleEventBatch(events=(event,)))


def _role_state(conn, lineage):
    from polymarket_alpha_lab.papersim.optimize.registry import derive_lineage_roles

    adapter = _adapter()
    roles = adapter.load_lineage_roles(conn, lineage)
    grouped = adapter.load_all_role_events(conn).get(lineage)
    assert roles == (derive_lineage_roles(grouped) if grouped else None)
    return roles


def _compute(conn, args, options, trace):
    from polymarket_alpha_lab.papersim.optimize import policy, runner
    from psycopg.pq import TransactionStatus

    protocol = args['manifest']['protocol']
    harness = Harness(seed=SEED, search_effect=options.get('search_effect', 0.15),
                      holdout_effect=options.get('holdout_effect', 0.15),
                      holdout_blocks=options.get('holdout_blocks', 20),
                      best_theta=options.get('best_theta', 0.4))
    harness.family = SyntheticFamily(tuple(protocol['targets']))
    # The synthetic family keeps the concurrent registration proof deterministic.
    # Its forward evidence uses the registered crypto threshold row.
    policy.FORWARD_THRESHOLDS = {**policy.FORWARD_THRESHOLDS,
        ('runner-synth', 'synthetic'): policy.classify_threshold('crypto-gbm', 'touch')}

    def compute(*, policy_context, deadline, source_artifacts):
        assert not options.get('forbid_compute'), 'terminal replay entered inference'
        assert conn.info.transaction_status == TransactionStatus.IDLE
        assert {digest: len(raw) for digest, raw in source_artifacts.items()} == args['refs']
        calls = 0

        def search(spec, fold, targets):
            nonlocal calls
            calls += 1
            # The seen prefix finishes this callback, including its forecasts.
            # A barrier in the next real deadline predicate prevents a sixth start.
            trace.append(('search', spec.spec_sha256(), fold.block_id))
            if options.get('crash_trial') == calls:
                with Path(options['trace']).open('w') as output:
                    json.dump(trace, output)
                os._exit(87)
            return harness.evaluate(spec, fold, targets)

        signaled = False

        def bounded():
            nonlocal signaled
            if calls == options.get('barrier_trial') and not signaled:
                signaled = True
                Path(options['trace']).write_bytes(_json(trace))
                _barrier(Path(options['barrier']))
            return deadline()

        def holdout(spec, target, folds):
            trace.append(('holdout', spec.spec_sha256(), target))
            return harness.evaluate_holdout(spec, target, folds)

        result = harness.run(
            evaluate=search, evaluate_holdout=holdout, source_artifacts=source_artifacts,
            snapshot_refs=tuple(args['refs']), schedule_id=protocol['schedule_id'],
            data_cutoff=datetime.fromisoformat(protocol['data_cutoff']),
            random_seed=protocol['seed'], code_commit=COMMIT,
            policy_context=policy_context, deadline=bounded, max_trials=runner.MAX_TRIALS,
        )
        if 'reported_budget' in options:
            result = replace(result, submission=replace(
                result.submission, budget={**result.submission.budget, **options['reported_budget']}))
        if options.get('trace'):
            Path(options['trace']).write_bytes(_json(trace))
        if options.get('publish_barrier'):
            path = Path(options['publish_barrier'])
            path.with_suffix('.observed').write_bytes(_json({
                'compute_finished_ns': time.monotonic_ns(), 'pure_outcome': result.outcome,
                'trials': len(result.trials),
            }))
            _barrier(path)
        return result

    return harness.family, compute


def _inject_faults(options):
    from polymarket_alpha_lab.papersim import snapshots

    adapter = _adapter()
    if options.get('precommit'):
        target, path = options['precommit']
        if target == 'claim':
            original_event_writer = adapter._optimizer_write_event

            def wait_before_claim_commit(conn, identity, **kwargs):
                result = original_event_writer(conn, identity, **kwargs)
                if kwargs['phase'] == 'STARTED':
                    _barrier(Path(path))
                return result

            adapter._optimizer_write_event = wait_before_claim_commit
        else:
            original_terminal_reader = adapter._optimization_terminal

            def wait_before_terminal_commit(*args, replayed):
                result = original_terminal_reader(*args, replayed=replayed)
                if not replayed:
                    _barrier(Path(path))
                return result

            adapter._optimization_terminal = wait_before_terminal_commit
    if options.get('crash_after_commit'):
        commit_context = adapter._managed_write_transaction
        crash_stage = options['crash_after_commit']

        @contextmanager
        def crash_after_commit(conn, clock, *, stage, closeout):
            with commit_context(conn, clock, stage=stage, closeout=closeout):
                yield
            if stage == crash_stage or crash_stage is True and stage in {'publication', 'failure_publication'}:
                os._exit(88)

        adapter._managed_write_transaction = crash_after_commit
    target = options.get('fault')
    if not target:
        return
    module, attribute = {
        'artifact': (snapshots, 'register_snapshot'),
        'run': (adapter, 'record_optimization_run'),
        'spec': (adapter, 'register_model_spec'),
        'role': (adapter, 'record_role_events'),
        'terminal': (adapter, 'record_command_run_event'),
        'claim': (adapter, 'record_command_run_event'),
    }[target]
    original = getattr(module, attribute)

    def fail_after_write(conn, *args, **kwargs):
        returned = original(conn, *args, **kwargs)
        if target in {'claim', 'terminal'}:
            phase = args[0].phase
            if (target == 'claim' and phase != 'STARTED' or
                    target == 'terminal' and phase not in {'SUCCEEDED', 'FAILED'}):
                return returned
        if options.get('fault_wait'):
            _barrier(Path(options['fault_wait']))
            return returned
        if options.get('fault_kill'):
            os._exit(89)
        raise OSError('test injected write failure')

    setattr(module, attribute, fail_after_write)


def _use_fixture_profile(family, protocol, options):
    """Install this process's synthetic experiment in the registry under test."""
    from polymarket_alpha_lab.papersim.optimize import runner

    resolve_family, historical_protocol = runner._historical_family, runner.historical_protocol
    runner._historical_family = lambda name: family if name == family.family_id else resolve_family(name)
    runner.historical_protocol = lambda requested: protocol if requested is family else historical_protocol(requested)
    if 'trial_cap' in options:
        runner.MAX_TRIALS = options['trial_cap']


def _shorten_test_budget(compute, hard):
    """Shorten only an isolated process's fixed allocation while using the real clock."""
    from polymarket_alpha_lab.papersim.optimize import runner

    runner.TIME_BUDGET_SECONDS = hard
    _adapter()._OPTIMIZATION_CLOSEOUT_NS = round(hard * NS) - round(compute * NS)


def _lose_commit_reply(conn):
    execute = conn.execute
    commits = 0

    def interrupt_reply(query, *args, **kwargs):
        nonlocal commits
        result = execute(query, *args, **kwargs)
        if query == 'COMMIT':
            commits += 1
            if commits == 2:
                conn.close()
                raise OSError('test result transport loss after commit')
        return result

    conn.execute = interrupt_reply


def _work(action, name, request_path, options):
    from polymarket_alpha_lab.papersim import snapshots

    adapter = _adapter()
    with _worker_connection(name) as conn:
        if action == 'init':
            family = SyntheticFamily(tuple(options.get('targets', ['synthetic'])))
            if 'barrier' in options:
                _barrier(Path(options['barrier']))
            return adapter.initialize_registry(conn, families=[family], actor='operator',
                                               code_commit=COMMIT, effective_at=AT), 0
        if action == 'role':
            if 'barrier' in options:
                _barrier(Path(options['barrier']))
            return _seed_candidate(conn, options['theta'], datetime.fromisoformat(options['at']),
                                   target=options.get('target', 'synthetic'),
                                   actor=options.get('actor', 'operator')), 0
        if action == 'read':
            row = adapter.latest_optimization_for_lineage(conn, LINEAGE)
            roles = _role_state(conn, LINEAGE)
            events = conn.execute('SELECT phase, attempt, error_code, run_event_id FROM '
                                  'papersim.command_run_events ORDER BY run_event_id').fetchall()
            result = {'row': row, 'roles': None if roles is None else roles.as_dict(),
                      'events': events, 'counts': _counts(conn)}
            if row is not None:
                digest = row['artifacts_sha256']
                summary = snapshots.verify_snapshot(conn, digest, expected_schema_version=2)
                readback = snapshots.read_snapshot(conn, digest)
                assert sha256(readback['content']).hexdigest() == digest
                assert len(readback['content']) == summary['byte_length'] == row['artifact_byte_length']
                if 'archive_path' in options:
                    Path(options['archive_path']).write_bytes(readback['content'])
            return result, 0
        if action == 'preview-facts':
            return _preview_storage_facts(conn), 0
        if action == 'preview-legacy':
            from polymarket_alpha_lab.papersim.optimize import runner

            def crossed_legacy_boundary(*args, **kwargs):
                raise AssertionError('legacy request reached registry or inference')

            args = json.loads(request_path.read_bytes())
            source = args['manifest']['sources'][0]
            content = snapshots.read_snapshot(conn, source['sha256'])['content']
            runner._read_registry = crossed_legacy_boundary
            runner.run_optimize_core = crossed_legacy_boundary
            adapter.load_optimization_runs = crossed_legacy_boundary
            try:
                runner.run_and_record(
                    conn=conn, repo_root=options['root'], family_id='nba-pace-efficiency',
                    as_of=AT, inputs=(runner.RegisteredInput(source['key'], source['kind'], content),),
                    seed=SEED, now_utc=lambda: AT, dry_run=options['dry_run'],
                    operation_key=options['operation_key'], attempt=2, max_trials=2000, budget_seconds=3600,
                )
            except ValueError as error:
                return {'error_code': str(error)}, 1
            raise AssertionError('legacy request was admitted')
        assert action == 'run'
        args = json.loads(request_path.read_bytes())
        protocol = args['manifest']['protocol']
        family, callback = _compute(conn, args, options, [])
        _use_fixture_profile(family, protocol, options)
        _inject_faults(options)
        if options.get('lose_commit_reply'):
            _lose_commit_reply(conn)
        if 'session_path' in options:
            Path(options['session_path']).write_bytes(_json(conn.info.backend_pid))
        start = time.monotonic_ns()
        if options.get('budget'):
            _shorten_test_budget(*options['budget'])
        clock = adapter.optimization_command_clock(start)
        if options.get('clock_path'):
            Path(options['clock_path']).write_bytes(_json({'compute': clock.compute_ns, 'hard': clock.hard_ns}))
        result = adapter.run_managed_optimization(
            conn, clock=clock, family=family, attempt=options.get('attempt', 1),
            data_cutoff=datetime.fromisoformat(protocol['data_cutoff']), random_seed=protocol['seed'],
            code_commit=COMMIT, targets=protocol['targets'], schedule_id=protocol['schedule_id'],
            manifest_sha256=args['digest'], manifest_byte_length=args['size'],
            manifest=args['manifest'], source_refs=args['refs'], compute=callback,
            reconnect=lambda: _worker_connection(name),
        )
        artifact = result.pop('artifact_bytes')
        if 'archive_path' in options:
            Path(options['archive_path']).write_bytes(artifact)
        result['finished_monotonic_ns'] = time.monotonic_ns()
        return result, result['exit_code']


def _main():
    from polymarket_alpha_lab.papersim.store import PapersimStoreError

    action, name, request, args = sys.argv[1:]
    if action in {'nba-cli', 'nba-read', 'nba-facts'}:
        return _nba_cli_worker(action, Path(name), Path(request), json.loads(args))
    if action == 'policy-cli':
        return _policy_cli_worker(name, Path(request), json.loads(args))
    if action == 'preview-cli':
        return _preview_cli_worker(name, Path(request), json.loads(args))
    if action == 'connection-cli':
        return _connection_cli_worker(Path(name), Path(request), json.loads(args))
    try:
        result, code = _work(action, name, Path(request), json.loads(args))
    except PapersimStoreError as error:
        result = {'error_code': error.code}
        result.update({name: getattr(error, name) for name in ('stage', 'retryable', 'started_event_id',
                       'attempt_event_id', 'indeterminate') if hasattr(error, name)})
        code = 1
    print(json.dumps(result, sort_keys=True, default=str))
    return code


@pytest.mark.parametrize(('run_options', 'outcome'), [
    ({}, 'CANDIDATE_REGISTERED'),
    ({'search_effect': 0.0}, 'NO_IMPROVEMENT'),
    ({'holdout_blocks': 5}, 'INSUFFICIENT_DATA'),
    ({'trial_cap': 3}, 'RUN_FAILED'),
])
@NATIVE
def test_native_managed_outcomes_and_verified_readback(archive, tmp_path, run_options, outcome):
    initialized = _invocation('init', archive)
    assert initialized[LINEAGE]['state'] == 'initialized'
    assert _invocation('init', archive)[LINEAGE]['state'] == 'skipped'
    before = _invocation('read', archive)
    archive_path = tmp_path / 'published.json'
    code = int(outcome == 'RUN_FAILED')
    published = _invocation('run', archive, code=code, archive_path=str(archive_path), **run_options)
    assert published['outcome'] == outcome
    assert published['registered_lineages'] == ([LINEAGE] if outcome == 'CANDIDATE_REGISTERED' else [])
    read_path = tmp_path / 'readback.json'
    after = _invocation('read', archive, archive_path=str(read_path))
    assert archive_path.read_bytes() == read_path.read_bytes()
    assert after['row']['optimization_id'] == published['optimization_id']
    assert after['row']['terminal_event_id'] == published['terminal_event_id']
    assert [event[0] for event in after['events']] == ['STARTED', 'FAILED' if code else 'SUCCEEDED']
    assert all(before['counts'][table] == after['counts'][table] for table in LEDGER)
    if run_options.get('trial_cap'):
        payload = json.loads(archive_path.read_bytes())
        assert payload['budget']['stopping_causes'] == ['trial_cap']
        assert payload['pure']['counts']['evaluated_trials'] == 3
    replay_path = tmp_path / 'replay.json'
    replay_options = {'trial_cap': run_options['trial_cap']} if 'trial_cap' in run_options else {}
    for attempt in (1, 2):
        replay = _invocation('run', archive, code=code, archive_path=str(replay_path),
                             attempt=attempt, forbid_compute=True, **replay_options)
        assert replay['replayed'] is True
        for field in ('optimization_id', 'started_event_id', 'terminal_event_id',
                      'artifacts_sha256', 'registered_lineages', 'outcome', 'dispositions'):
            assert replay[field] == published[field]
        assert replay_path.read_bytes() == archive_path.read_bytes()
    assert _invocation('read', archive) == after


@NATIVE
def test_native_same_instant_race_and_exact_older_replay(archive, tmp_path):
    name, _ = archive
    _invocation('init', archive, targets=['synthetic', 'peer'])
    instant = AT + timedelta(days=2, milliseconds=100)
    arguments = {'at': instant.isoformat()}
    barriers = [tmp_path / 'role.one', tmp_path / 'role.two']
    with _process('role', archive, theta=0.1, barrier=str(barriers[0]), **arguments) as one, \
            _process('role', archive, theta=0.3, barrier=str(barriers[1]), **arguments) as two:
        for path, process in zip(barriers, (one, two), strict=True):
            _wait_path(path, process)
        for path in barriers:
            path.with_suffix('.release').touch()
        results = []
        for process in (one, two):
            output, stderr = process.communicate(timeout=10)
            assert process.returncode in (0, 1), stderr
            results.append((process.returncode, json.loads(output)))
    assert sorted(code for code, _ in results) == [0, 1], results
    winner = (0.1, 0.3)[next(i for i, (code, _) in enumerate(results) if code == 0)]
    original = next(row for code, row in results if code == 0)
    loser = next(row for code, row in results if code == 1)
    assert loser['error_code'] == 'papersim_role_event_out_of_order'
    assert _invocation('role', archive, target='peer', theta=0.4, **arguments)['inserted'] is True
    for offset, expected in ((-1, 'papersim_role_event_out_of_order'), (0.1, 'papersim_role_event_conflict')):
        stamp = instant + timedelta(seconds=offset)
        theta = winner if offset > 0 else 0.4
        reason = _invocation('role', archive, code=1, theta=theta, at=stamp.isoformat())
        assert reason['error_code'] == expected
    later = _invocation('role', archive, theta=0.4, at=(instant + timedelta(seconds=1)).isoformat())
    replay = _invocation('role', archive, theta=winner, **arguments)
    assert later['inserted'] and not replay['inserted']
    assert replay['role_event_ids'] == original['role_event_ids']
    conflict = _invocation('role', archive, code=1, theta=winner, actor='changed-actor', **arguments)
    assert conflict['error_code'] == 'papersim_role_event_conflict'
    with _worker_connection(name) as conn:
        assert len(_role_state(conn, LINEAGE).candidates) == 2
        assert len(_role_state(conn, 'runner-synth/peer').candidates) == 1


@NATIVE
def test_native_control_scope_chronology_and_partial_role_replay(archive):
    from polymarket_alpha_lab.papersim import store

    name, _ = archive
    adapter = _adapter()
    _invocation('init', archive)
    with _worker_connection(name) as conn:
        event = adapter.load_all_role_events(conn)[LINEAGE][0][0]
        request = store.RoleEventSubmission(
            lineage=event.lineage, spec_sha256=event.spec_sha256, role=event.role,
            effective_at=event.effective_at, reason=event.reason, actor=event.actor, code_commit=event.code_commit,
        )
        with pytest.raises(store.PapersimStoreError, match='papersim_role_event_partial_replay'):
            adapter.record_role_events(conn, store.RoleEventBatch(events=(request,)))
        freeze = store.OptimizerControlEventSubmission(
            scope='LINEAGE', lineage=LINEAGE, control_kind='FREEZE',
            reason='test freeze', actor='operator', occurred_at=AT,
        )
        unfreeze = replace(freeze, control_kind='UNFREEZE', reason='test unfreeze')
        first = adapter.record_optimizer_control_event(conn, freeze)
        second = adapter.record_optimizer_control_event(conn, unfreeze)
        assert second['optimizer_control_id'] > first['optimizer_control_id']
        with pytest.raises(store.PapersimStoreError, match='papersim_optimizer_control_out_of_order'):
            adapter.record_optimizer_control_event(conn, replace(unfreeze, occurred_at=AT - timedelta(seconds=1)))
        global_freeze = replace(freeze, scope='GLOBAL', lineage=None, occurred_at=AT - timedelta(seconds=1))
        adapter.record_optimizer_control_event(conn, global_freeze)
        assert not adapter.record_optimizer_control_event(conn, freeze)['inserted']
    with _worker_connection(name) as fresh:
        assert adapter.registry_freeze_state(fresh) == {'global_frozen': True, 'frozen_lineages': []}
        assert _role_state(fresh, LINEAGE).batches == 1


@NATIVE
def test_native_repeatable_read_snapshot_cannot_append_stale_role(archive):
    from polymarket_alpha_lab.papersim.store import PapersimStoreError

    name, _ = archive
    adapter = _adapter()
    _invocation('init', archive)
    with _worker_connection(name) as stale, _worker_connection(name) as writer:
        baseline = SyntheticFamily().baseline_spec()
        for theta in (0.1, 0.3, 0.4):
            adapter.register_model_spec(writer, replace(baseline, params={'theta': theta}),
                                        source='manual', code_commit=COMMIT)
        with stale.transaction():
            stale.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            assert _role_state(stale, LINEAGE).candidates == ()
            for minute, theta in enumerate((0.1, 0.3), 1):
                _seed_candidate(writer, theta, AT + timedelta(minutes=minute))
            with pytest.raises(PapersimStoreError, match='papersim_registry_write_requires_read_committed'):
                _seed_candidate(stale, 0.4, AT + timedelta(minutes=3))
            assert _role_state(stale, LINEAGE).candidates == ()
    with _worker_connection(name) as fresh:
        assert len(_role_state(fresh, LINEAGE).candidates) == 2


@pytest.mark.parametrize('fault', WRITERS)
@NATIVE
def test_native_publication_write_failure_rolls_back_every_statement(archive, fault):
    _invocation('init', archive)
    before = _invocation('read', archive)
    result = _invocation('run', archive, code=1, fault=fault)
    assert result['stage'] == 'publication'
    assert result['retryable'] is True
    after = _invocation('read', archive)
    assert after['row'] is None
    assert after['roles'] == before['roles']
    assert all(after['counts'][table] == before['counts'][table]
               for table in (*TABLES[:-1], *LEDGER))
    recovered = _invocation('run', archive, attempt=2)
    assert recovered['outcome'] == 'CANDIDATE_REGISTERED'
    assert recovered['started_event_id'] == result['started_event_id']


@NATIVE
def test_native_compute_deadline_seals_and_replays_completed_prefix(archive, tmp_path):
    from polymarket_alpha_lab.papersim.optimize.search import TRIAL_COMPLETED

    _invocation('init', archive)
    trace = tmp_path / 'trace.json'
    barrier = tmp_path / 'compute.hold'
    clock_path = tmp_path / 'clock.json'
    artifact_path = tmp_path / 'published.json'
    before = _invocation('read', archive)
    with _process('run', archive, budget=[1.8, 6], barrier_trial=5, trace=str(trace), barrier=str(barrier),
                  clock_path=str(clock_path), archive_path=str(artifact_path)) as process:
        _wait_path(barrier, process)
        times = json.loads(clock_path.read_bytes())
        while time.monotonic_ns() <= times['compute']:
            time.sleep(0.01)
        barrier.with_suffix('.release').touch()
        result = _reply(process, 1)
    assert result['outcome'] == 'RUN_FAILED'
    assert result['error_code'] == 'papersim_optimize_budget_exhausted'
    assert result['finished_monotonic_ns'] < times['hard']
    pure_artifact = json.loads(artifact_path.read_bytes())
    pure = pure_artifact['pure']
    observed = json.loads(trace.read_bytes())
    assert pure_artifact['budget']['stopping_causes'] == ['compute_time']
    assert len(observed) == len(pure['trials']) == 5
    assert [row['status'] for row in pure['trials']] == [TRIAL_COMPLETED] * 5
    assert all(row[0] == 'search' for row in observed)
    assert [(row['spec_sha256'], row['fold_block_id']) for row in pure['trials']] == [
        tuple(row[1:]) for row in observed]
    assert len(pure['search_trial_forecasts']) > 0
    assert pure['counts']['holdout_callbacks'] == 0
    assert pure_artifact['rejections'] == {}
    read_path = tmp_path / 'readback.json'
    readback = _invocation('read', archive, archive_path=str(read_path))
    assert read_path.read_bytes() == artifact_path.read_bytes()
    assert [row[0] for row in readback['events']] == ['STARTED', 'FAILED']
    assert readback['row']['outcome'] == 'RUN_FAILED'
    assert all(readback['counts'][table] == before['counts'][table]
               for table in ('model_specs', 'model_role_events', *LEDGER))
    for attempt in (1, 2):
        replay = _invocation('run', archive, code=1, budget=[1.8, 6], attempt=attempt, forbid_compute=True)
        for field in ('optimization_id', 'started_event_id', 'terminal_event_id',
                      'artifacts_sha256', 'dispositions', 'outcome'):
            assert replay[field] == result[field]


@NATIVE
def test_native_interrupted_prefix_and_unacknowledged_commit(archive, tmp_path):
    _invocation('init', archive)
    trace = tmp_path / 'first-trace.json'
    with _process('run', archive, crash_trial=5, trace=str(trace)) as process:
        process.communicate(timeout=15)
        assert process.returncode == 87
    start = _invocation('read', archive)
    assert start['row'] is None
    assert [row[0] for row in start['events']] == ['STARTED']
    with _process('run', archive, attempt=2, crash_after_commit=True, trace=str(tmp_path / 'second-trace.json')) as process:
        process.communicate(timeout=15)
        assert process.returncode == 88
    published = _invocation('read', archive)
    assert [row[0] for row in published['events']] == ['STARTED', 'STARTED', 'SUCCEEDED']
    result = _invocation('run', archive, attempt=3, forbid_compute=True)
    assert result['started_event_id'] == start['events'][0][3]
    assert result['terminal_event_id'] == published['events'][-1][3]
    telemetry = result['attempt_telemetry']
    assert telemetry['has_unknown_predecessor_progress']
    assert telemetry['physical_starts_lower_bound'] == telemetry['distinct_trial_ids_observed'] == 24
    prior = json.loads(trace.read_bytes())
    replayed = json.loads((tmp_path / 'second-trace.json').read_bytes())
    assert replayed[:len(prior)] == prior
    sample = [('search', digest, fold) for kind, digest, fold in replayed if kind == 'search']
    physical = [*prior, *map(list, sample)]
    assert len(physical) == 29 and len({tuple(row) for row in physical}) == 24


@pytest.mark.parametrize('point', ['claim', 'role'])
@NATIVE
def test_native_process_death_recovers_only_the_original_claim(archive, tmp_path, point):
    _invocation('init', archive)
    before = _invocation('read', archive)
    fault = {'crash_after_commit': 'claim'} if point == 'claim' else {'fault': 'role', 'fault_kill': True}
    with _process('run', archive, **fault) as process:
        process.communicate(timeout=20)
        assert process.returncode == (88 if point == 'claim' else 89)
    interrupted = _invocation('read', archive)
    assert [row[0] for row in interrupted['events']] == ['STARTED']
    assert interrupted['row'] is None
    assert all(interrupted['counts'][table] == before['counts'][table] for table in (*TABLES[:-1], *LEDGER))
    resumed = _invocation('run', archive, attempt=2, archive_path=str(tmp_path / 'resumed.json'))
    assert resumed['started_event_id'] == interrupted['events'][0][3]
    assert resumed['attempt_telemetry']['has_unknown_predecessor_progress']
    assert resumed['attempt_telemetry']['physical_starts_lower_bound'] == 24


@NATIVE
def test_native_failed_commit_reply_loss_verifies_with_new_connection(archive, tmp_path):
    _invocation('init', archive)
    output = tmp_path / 'confirmed.json'
    confirmed = _invocation('run', archive, code=1, trial_cap=3, lose_commit_reply=True, archive_path=str(output))
    assert confirmed['outcome'] == 'RUN_FAILED' and confirmed['replayed'] is True
    archive_output = tmp_path / 'readback.json'
    durable = _invocation('read', archive, archive_path=str(archive_output))
    assert [row[0] for row in durable['events']] == ['STARTED', 'FAILED']
    assert confirmed['terminal_event_id'] == durable['events'][-1][3]
    assert archive_output.read_bytes() == output.read_bytes()
    replay = _invocation('run', archive, code=1, attempt=2, forbid_compute=True, trial_cap=3)
    assert replay['terminal_event_id'] == confirmed['terminal_event_id']
    assert replay['artifacts_sha256'] == confirmed['artifacts_sha256']


def _advance_past(bound):
    while time.monotonic_ns() <= bound:
        time.sleep(0.01)


def _registry_lock(conn):
    assert conn.execute('SELECT pg_try_advisory_xact_lock(%s, %s)', _adapter()._REGISTRY_LOCK).fetchone()[0]


def test_managed_production_command_clock_allocation():
    start = 25 * NS
    clock = _adapter().optimization_command_clock(start)
    assert clock.start_ns == start
    assert clock.compute_ns - start == 3540 * NS
    assert clock.hard_ns - clock.compute_ns == 60 * NS
    assert clock.hard_ns - start == 3600 * NS


@NATIVE
def test_native_preview_requires_a_verified_read_only_snapshot(archive, monkeypatch):
    from polymarket_alpha_lab.papersim.optimize import runner

    name, request = archive
    adapter = _adapter()
    args = json.loads(request.read_bytes())
    protocol = args['manifest']['protocol']
    family = SyntheticFamily(tuple(protocol['targets']))
    monkeypatch.setattr(runner, '_historical_family', lambda name: family)
    monkeypatch.setattr(runner, 'historical_protocol', lambda value: protocol)

    observed = []
    verify_inputs = adapter._verify_optimizer_inputs
    read_operation = adapter._optimization_operation

    def observe(reader):
        def observed_reader(conn, *args):
            observed.append(conn.execute('SELECT current_setting(%s), current_setting(%s)',
                                         ('transaction_isolation', 'transaction_read_only')).fetchone())
            return reader(conn, *args)

        return observed_reader

    monkeypatch.setattr(adapter, '_verify_optimizer_inputs', observe(verify_inputs))
    monkeypatch.setattr(adapter, '_optimization_operation', observe(read_operation))
    with _worker_connection(name) as conn:
        receipt = adapter.preview_managed_optimization(
            conn, clock=adapter.optimization_command_clock(time.monotonic_ns()),
            family=family, data_cutoff=AT, random_seed=SEED, code_commit=COMMIT, attempt=1,
            targets=protocol['targets'], schedule_id=protocol['schedule_id'],
            manifest_sha256=args['digest'], manifest_byte_length=args['size'],
            manifest=args['manifest'], source_refs=args['refs'],
        )
    assert observed == [('repeatable read', 'on')] * 2
    assert receipt['status'] == 'not_run' and receipt['dry_run'] is True
    for field in ('outcome', 'run_scorecard', 'trial_count', 'artifacts_sha256', 'artifact_byte_length'):
        assert receipt[field] is None


def _preview_cli_worker(name, request, options):
    from polymarket_alpha_lab.papersim import cli
    from polymarket_alpha_lab.papersim.optimize import policy, runner

    registration = json.loads(Path(options['registration']).read_bytes())
    protocol = registration['manifest']['protocol']
    harness = Harness(seed=SEED, search_effect=0.15, holdout_effect=options.get('holdout_effect', 0.15),
                      best_theta=options.get('best_theta', 0.3))
    family = harness.family
    _use_fixture_profile(family, protocol, options)
    resolve_family = runner.family_singleton
    runner.family_singleton = lambda name: family if name == family.family_id else resolve_family(name)
    policy.FORWARD_THRESHOLDS = {**policy.FORWARD_THRESHOLDS,
        ('runner-synth', 'synthetic'): policy.classify_threshold('crypto-gbm', 'touch')}
    counters = dict.fromkeys(('compute', 'search', 'fit', 'predict', 'candidate_holdout', 'incumbent_holdout'), 0)
    incumbent = family.baseline_spec().spec_sha256()

    def forecast(spec, fold, target):
        counters['fit'] += 1
        _, fitted_spec, _ = family.fit(fold.train_keys, spec, fold.cutoff)
        counters['predict'] += len(fold.test_keys)
        return harness._events(fitted_spec, fold.test_keys, target)

    def search(spec, fold, targets):
        counters['search'] += 1
        return {target: runner.HoldoutEvaluation(forecast(spec, fold, target)) for target in targets}

    def holdout(spec, target, folds):
        side = 'incumbent' if spec.spec_sha256() == incumbent else 'candidate'
        counters[f'{side}_holdout'] += 1
        return runner.HoldoutEvaluation(tuple(row for fold in folds for row in forecast(spec, fold, target)))

    def arguments(*, family, manifest, source_artifacts, deadline):
        counters['compute'] += 1
        assert family is harness.family and manifest == registration['manifest']
        assert {key: len(value) for key, value in source_artifacts.items()} == registration['refs']
        if options.get('barrier'):
            _barrier(Path(options['barrier']))
        lines = tuple(runner.FixtureLine(row.event_key, target, 'line', row.event_key, ('no', 'yes'), 1, 'binary')
                      for row in harness.records for target in family.targets())
        return dict(family=family, records=harness.records, fixture_lines=lines, floor_class='crypto',
                    schedule_id=protocol['schedule_id'], holdout_group_ids=harness.holdout_group_ids,
                    evaluate=search, evaluate_holdout=holdout)

    runner.historical_run_arguments = arguments
    adapter = _adapter()
    if options['dry_run']:
        def unexpected_write(*args, **kwargs):
            raise AssertionError('dry-run entered the writer')

        adapter._optimizer_operation_lock = unexpected_write
        adapter._optimizer_claim = unexpected_write
        adapter._optimizer_publish = unexpected_write
    dsn = _rebind_database(OWNER_DSN, name).replace('user=pal_owner', 'user=pal_app')
    argv = ['optimize', 'run', '--dsn', dsn, '--repo-root', options['root'],
            '--family', family.family_id, '--as-of', protocol['data_cutoff'],
            '--input-manifest', str(request), '--attempt', str(options.get('attempt', 1))]
    if options['dry_run']:
        argv.append('--dry-run')
    if 'operation_key' in options:
        argv.extend(('--operation-key', options['operation_key']))
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = cli.main(argv)
    reason = re.search(r'FAIL: (papersim_[a-z0-9_]+)', stderr.getvalue())
    print(json.dumps({'output': json.loads(stdout.getvalue() or 'null'),
                      'error_code': None if reason is None else reason[1], 'counters': counters}))
    return code


@NATIVE
def test_native_registered_cli_previews_without_model_calls_or_writes(archive, managed_repo, tmp_path):
    from psycopg import sql

    name, registration = archive
    root, _ = managed_repo
    _invocation('init', archive)
    with _worker_connection(name) as conn:
        previous = _register(conn, tmp_path, schedule='native-preview-rejection-v1')

    def pointer(path, filename):
        registered = json.loads(path.read_bytes())
        target = tmp_path / filename
        target.write_bytes(_json({'schema_version': 1, 'manifest_sha256': registered['digest'],
                                  'manifest_byte_length': registered['size']}))
        return name, target

    registered = pointer(registration, 'preview.json')
    renamed = pointer(registration, 'same-manifest.json')
    previous_rejection = _invocation(
        'preview-cli', pointer(previous, 'previous.json'), root=str(root), registration=str(previous),
        dry_run=False, best_theta=0.4, holdout_effect=-0.15)['output']
    assert previous_rejection['outcome'] == 'NO_IMPROVEMENT'
    assert previous_rejection['scorecard']['stage1_rejections'][0]['lineage'] == LINEAGE

    def invoke(run=registered, *, dry_run=True, code=0, **options):
        return _invocation('preview-cli', run, code=code, root=str(root), registration=str(registration),
                           dry_run=dry_run, **options)

    def facts():
        return _invocation('preview-facts', archive)

    def unchanged(before, *args, **options):
        result = invoke(*args, **options)
        assert all(value == 0 for value in result['counters'].values())
        assert facts() == before
        return result

    before = facts()
    first = unchanged(before)['output']
    assert first['status'] == 'not_run' and first['dry_run'] is True
    empty_fields = ('outcome', 'run_scorecard', 'trial_count', 'artifacts_sha256', 'artifact_byte_length')
    assert all(first[field] is None for field in empty_fields)
    key = first['operation_key']
    assert unchanged(before, renamed, attempt=9, operation_key=key)['output'] == first
    for dry_run in (True, False):
        conflict = unchanged(before, dry_run=dry_run, operation_key='different', code=1)
        assert conflict['output'] is None and conflict['error_code'] == 'papersim_optimize_request_conflict'
        legacy = _invocation('preview-legacy', archive, root=str(root), dry_run=dry_run,
                             operation_key=key, code=1)
        assert legacy['error_code'] == 'papersim_optimize_input_manifest_required'
        assert facts() == before

    barrier = tmp_path / 'preview-in-flight'
    with _process('preview-cli', registered, root=str(root), registration=str(registration),
                  dry_run=False, operation_key=key, barrier=str(barrier)) as process:
        _wait_path(barrier, process)
        in_flight = facts()
        assert len(in_flight['command_run_events']) == len(before['command_run_events']) + 1
        assert all(in_flight[table] == before[table] for table in before if table != 'command_run_events')
        observed = unchanged(in_flight, renamed, attempt=6)['output']
        assert observed['status'] == 'in_progress' and observed['run_key'] == first['run_key']
        assert all(observed[field] is None for field in empty_fields)
        barrier.with_suffix('.release').touch()
        completed = _reply(process)

    calls, published = completed['counters'], completed['output']
    assert calls['compute'] == calls['candidate_holdout'] == calls['incumbent_holdout'] == 1
    assert all(calls[name] > 0 for name in ('search', 'fit', 'predict'))
    assert published['outcome'] == 'CANDIDATE_REGISTERED'
    assert published['started_event_id'] == observed['started_event_id']
    assert published['registered_lineages'] == [LINEAGE]
    after = facts()
    added = {'command_run_events': 2, 'optimization_runs': 1, 'snapshots': 1,
             'model_specs': 1, 'model_role_events': 1}
    for table, original_rows in before.items():
        assert set(original_rows) <= set(after[table])
        assert len(after[table]) - len(original_rows) == added.get(table, 0)

    read = _invocation('read', archive)['row']
    fields = ('run_key', 'optimization_id', 'started_event_id', 'terminal_event_id',
              'started_event_key', 'terminal_event_key', 'artifacts_sha256', 'artifact_byte_length', 'outcome')
    for attempt in (1, 2):
        preview = unchanged(after, renamed, attempt=attempt, operation_key=key)['output']
        replay = unchanged(after, dry_run=False, attempt=attempt, operation_key=key)['output']
        assert preview['status'] == 'sealed' and preview['replayed'] is True
        assert preview['run_scorecard'] == published['scorecard'] == read['scorecard']
        assert preview['trial_count'] == read['trial_count'] > 0
        for field in fields:
            assert preview[field] == replay[field] == published[field]
        assert preview['artifacts_sha256'] == read['artifacts_sha256']
        assert preview['artifact_byte_length'] == read['artifact_byte_length']

    digest = published['artifacts_sha256']
    with _connect(_rebind_database(OWNER_DSN, name)) as owner:
        # Introduce corruption in this disposable database while leaving the terminal linked.
        with owner.transaction():
            constraint, = owner.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'papersim.snapshots'::regclass "
                "AND pg_get_constraintdef(oid) LIKE '%sha256(content)%'").fetchone()
            owner.execute(sql.SQL('ALTER TABLE papersim.snapshots DROP CONSTRAINT {}').format(sql.Identifier(constraint)))
            owner.execute('ALTER TABLE papersim.snapshots DISABLE TRIGGER immutable_snapshots')
            owner.execute('UPDATE papersim.snapshots SET content = set_byte(content, 0, 0) '
                          'WHERE content_sha256 = %s', (digest,))
            owner.execute('ALTER TABLE papersim.snapshots ENABLE TRIGGER immutable_snapshots')
        damaged = unchanged(facts(), code=1)
        assert damaged['output'] is None and damaged['error_code'] == 'papersim_optimize_snapshot_identity_invalid'
        with owner.transaction():
            owner.execute('ALTER TABLE papersim.snapshots DISABLE TRIGGER immutable_snapshots')
            owner.execute('DELETE FROM papersim.snapshots WHERE content_sha256 = %s', (digest,))
            owner.execute('ALTER TABLE papersim.snapshots ENABLE TRIGGER immutable_snapshots')
        missing = unchanged(facts(), code=1)
        assert missing['output'] is None and missing['error_code'] == 'papersim_optimize_snapshot_identity_invalid'


@NATIVE
def test_native_public_store_rejects_unregistered_request_before_claim(archive):
    from copy import deepcopy
    from polymarket_alpha_lab.papersim import snapshots
    from polymarket_alpha_lab.papersim.optimize import runner
    from polymarket_alpha_lab.papersim.optimize.families.nba import NBA_PACE_EFFICIENCY

    adapter = _adapter()
    family = NBA_PACE_EFFICIENCY
    protocol = runner.historical_protocol('nba-pace-efficiency')
    source = _nba_fixture_snapshot()
    digest = sha256(source).hexdigest()
    kind = 'optimizer_native_espn_fixture'
    document = {'schema_version': 1, 'protocol': protocol, 'sources': [
        {'role': 'nba_snapshot', 'key': 'snapshot', 'kind': kind, 'schema_version': 1,
         'sha256': digest, 'byte_length': len(source)},
    ]}
    issues = {
        'seed': ('papersim_optimize_request_conflict', 'request'),
        'cutoff': ('papersim_optimize_request_conflict', 'request'),
        'scope': ('papersim_optimize_request_conflict', 'request'),
        'schedule': ('papersim_optimize_request_conflict', 'request'),
        'cohort': ('papersim_optimize_request_conflict', 'request'),
        'grid': ('papersim_optimize_request_conflict', 'request'),
        'family': ('papersim_optimize_family_unregistered', 'request'),
        'hard_clock': ('papersim_optimize_clock_invalid', 'request'),
        'closeout_clock': ('papersim_optimize_clock_invalid', 'request'),
        'source_role': ('papersim_optimize_manifest_source_unregistered', 'verify_inputs'),
        'source_duplicate': ('papersim_optimize_manifest_invalid', 'request'),
    }
    name, _ = archive
    with _worker_connection(name) as conn:
        snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
            content=source, kind=kind, source='native-espn-fixture', fetched_at=AT,
        ))

    def forbidden(**_):
        pytest.fail('unregistered public request entered computation')

    for issue, expected in issues.items():
        manifest = deepcopy(document)
        profile = manifest['protocol']
        start = time.monotonic_ns()
        clock = adapter.optimization_command_clock(start)
        requested_family = family
        if issue == 'seed':
            profile['seed'] += 1
        elif issue == 'cutoff':
            profile['data_cutoff'] = (datetime.fromisoformat(profile['data_cutoff']) + timedelta(days=1)).isoformat()
        elif issue == 'scope':
            profile['targets'].pop()
        elif issue == 'schedule':
            profile['schedule_id'] += '-unregistered'
        elif issue == 'cohort':
            profile['cohort']['first_holdout_date'] = '2026-11-02'
        elif issue == 'grid':
            profile['continuous_grid_points'] = 11
        elif issue == 'family':
            requested_family = type(family)()
        elif issue == 'hard_clock':
            clock = replace(clock, compute_ns=start + 7140 * NS, hard_ns=start + 7200 * NS)
        elif issue == 'closeout_clock':
            clock = replace(clock, compute_ns=start + 3550 * NS)
        elif issue == 'source_role':
            manifest['sources'][0]['role'] = 'unregistered_history'
        elif issue == 'source_duplicate':
            manifest['sources'].append(manifest['sources'][0].copy())
        content = _json(manifest)
        with _worker_connection(name) as conn:
            stored = snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
                content=content, kind='optimizer_experiment_manifest', source='native-manifest-test', fetched_at=AT,
            ))
            before = _counts(conn)
            with pytest.raises(adapter.ManagedOptimizationError) as rejected:
                adapter.run_managed_optimization(
                    conn, clock=clock, family=requested_family, targets=profile['targets'],
                    data_cutoff=datetime.fromisoformat(profile['data_cutoff']), random_seed=profile['seed'],
                    code_commit=COMMIT, attempt=7, schedule_id=profile['schedule_id'],
                    manifest_sha256=stored['content_sha256'], manifest_byte_length=len(content),
                    manifest=manifest, source_refs={digest: len(source)}, compute=forbidden,
                )
        assert (rejected.value.code, rejected.value.stage) == expected, issue
        assert rejected.value.started_event_id is None and rejected.value.attempt_event_id is None
        with _worker_connection(name) as independent:
            assert _counts(independent) == before


@pytest.mark.parametrize(('reported_budget', 'violation'), [
    ({'max_trials': 3000}, 'reported_max_trials'),
    ({'max_trials': 1999}, 'reported_max_trials'),
    ({'time_budget_seconds': 7200}, 'reported_time_budget_seconds'),
])
@NATIVE
def test_native_callback_with_conflicting_budget_seals_only_failure(archive, tmp_path, reported_budget, violation):
    _invocation('init', archive)
    before = _invocation('read', archive)
    output = tmp_path / 'budget-conflict.json'
    result = _invocation('run', archive, code=1, reported_budget=reported_budget, archive_path=str(output))
    assert result['outcome'] == 'RUN_FAILED' and result['error_code'] == 'papersim_optimize_budget_conflict'
    assert result['registered_lineages'] == []
    managed = json.loads(output.read_bytes())
    assert managed['pure']['outcome'] == 'CANDIDATE_REGISTERED'
    assert managed['pure']['counts']['evaluated_trials'] == 24
    assert managed['pure']['counts']['holdout_callbacks'] == 2
    assert managed['budget']['violations'] == [violation]
    assert managed['budget']['max_trials'] == 2000
    assert managed['budget']['command_nanoseconds'] == 3600 * NS
    for key, value in reported_budget.items():
        assert managed['budget']['reported_limits'][key] == value
    assert not managed['rejections']
    assert managed['dispositions'][LINEAGE]['status'] == 'registered_budget_conflict'
    independent_output = tmp_path / 'independent.json'
    archived = _invocation('read', archive, archive_path=str(independent_output))
    assert [event[0] for event in archived['events']] == ['STARTED', 'FAILED']
    assert archived['row']['outcome'] == 'RUN_FAILED'
    assert archived['row']['optimization_id'] == result['optimization_id']
    assert archived['row']['terminal_event_id'] == result['terminal_event_id']
    assert all(archived['counts'][table] == before['counts'][table]
               for table in ('model_specs', 'model_role_events', *LEDGER))
    assert independent_output.read_bytes() == output.read_bytes()
    replay_output = tmp_path / 'replayed.json'
    for attempt in (1, 2):
        replay = _invocation('run', archive, code=1, attempt=attempt, forbid_compute=True, archive_path=str(replay_output))
        assert replay['replayed'] is True and replay_output.read_bytes() == output.read_bytes()
        for key in ('optimization_id', 'started_event_id', 'terminal_event_id', 'artifacts_sha256', 'error_code'):
            assert replay[key] == result[key]
    assert _invocation('read', archive) == archived


@NATIVE
def test_native_registry_claim_and_session_lock_exhaustion(archive):
    from polymarket_alpha_lab.papersim.store import optimization_run_key
    from polymarket_alpha_lab.papersim.optimize.runner import optimization_search_descriptor

    _invocation('init', archive)
    before = _invocation('read', archive)
    name, _ = archive
    adapter = _adapter()
    family = SyntheticFamily()
    search = optimization_search_descriptor(family, schedule_id='native-fixture-v1', data_cutoff=AT)
    run_key = optimization_run_key(family.family_id, AT, 'v1', SEED, search)
    operation = (adapter._OPERATION_NAMESPACE, adapter._advisory_lock_number(run_key))
    with _worker_connection(name) as locker:
        with locker.transaction():
            _registry_lock(locker)
            failed = _invocation('run', archive, code=1, budget=[0.7, 2], forbid_compute=True)
            assert failed['stage'] == 'claim_lock'
        assert locker.execute('SELECT pg_try_advisory_lock(%s, %s)', operation).fetchone()[0]
        failed = _invocation('run', archive, code=1, budget=[0.7, 2], forbid_compute=True)
        assert failed['stage'] == 'operation_lock'
    assert _invocation('read', archive) == before


@pytest.mark.parametrize(('target', 'trial_cap', 'phase'), [
    ('claim', 2000, 'claim'), ('terminal', 2000, 'publication'), ('terminal', 3, 'failure_publication'),
])
@NATIVE
def test_native_expiry_after_staging_rolls_back(archive, tmp_path, target, trial_cap, phase):
    name, _ = archive
    _invocation('init', archive)
    before = _invocation('read', archive)
    barrier = tmp_path / 'precommit.hold'
    clock_path = tmp_path / 'clock.json'
    budget = [7, 10] if phase == 'publication' else [2.8, 5.5]
    with _process('run', archive, budget=budget, trial_cap=trial_cap,
                  precommit=[target, str(barrier)], clock_path=str(clock_path)) as process:
        _wait_path(barrier, process)
        times = json.loads(clock_path.read_bytes())
        _advance_past(times['compute' if target == 'claim' else 'hard'])
        barrier.with_suffix('.release').touch()
        failed = _reply(process, 1)
    assert failed['stage'] == f'{phase}_precommit'
    assert failed['retryable'] and not failed['indeterminate']
    after = _invocation('read', archive)
    assert after['row'] is None
    assert [row[0] for row in after['events']] == ([] if target == 'claim' else ['STARTED'])
    assert all(after['counts'][table] == before['counts'][table] for table in (*TABLES[:-1], *LEDGER))
    with _worker_connection(name) as conn:
        assert _role_state(conn, LINEAGE).batches == 1


@pytest.mark.parametrize(('timeout_result', 'block_hard', 'candidate'), [
    (False, False, False), (False, False, True), (False, True, True), (True, True, False),
])
@NATIVE
def test_native_publication_wait_uses_hard_deadline(archive, tmp_path, timeout_result, block_hard, candidate):
    name, _ = archive
    _invocation('init', archive)
    before = _invocation('read', archive)
    barrier = tmp_path / 'evaluation.hold'
    clock_path = tmp_path / 'clock.json'
    if timeout_result:
        options = {'barrier_trial': 5, 'barrier': str(barrier), 'trace': str(tmp_path / 'trace.json')}
    else:
        options = {'publish_barrier': str(barrier), 'search_effect': 0.15 if candidate else 0.0}
    budget = [7, 10] if candidate else [2.8, 5.5]
    with _process('run', archive, budget=budget, clock_path=str(clock_path), **options) as process:
        _wait_path(barrier, process)
        times = json.loads(clock_path.read_bytes())
        expected = 'CANDIDATE_REGISTERED' if candidate else 'NO_IMPROVEMENT'
        if not timeout_result:
            observed = json.loads(barrier.with_suffix('.observed').read_bytes())
            assert observed['compute_finished_ns'] < times['compute']
            assert observed['pure_outcome'] == expected and observed['trials'] == 24
        with _worker_connection(name) as locker, locker.transaction():
            _registry_lock(locker)
            if block_hard:
                if timeout_result:
                    _advance_past(times['compute'])
                barrier.with_suffix('.release').touch()
                failed = _reply(process, 1)
                stage = 'failure_publication_lock' if timeout_result else 'publication_lock'
                assert failed['stage'] == stage
            else:
                barrier.with_suffix('.release').touch()
                _advance_past(times['compute'])
        if not block_hard:
            result = _reply(process)
            assert result['outcome'] == expected
            assert times['compute'] < result['finished_monotonic_ns'] < times['hard']
    after = _invocation('read', archive)
    if block_hard:
        assert after['row'] is None
        assert [row[0] for row in after['events']] == ['STARTED']
        assert all(after['counts'][table] == before['counts'][table] for table in (*TABLES[:-1], *LEDGER))


@pytest.mark.parametrize('control', ['candidate_capacity', 'freeze', 'changed_active', 'future_role'])
@NATIVE
def test_native_publication_rechecks_registry_after_search(archive, tmp_path, control):
    from polymarket_alpha_lab.papersim import store

    name, _ = archive
    adapter = _adapter()
    _invocation('init', archive)
    old = AT + timedelta(minutes=1)
    if control == 'candidate_capacity':
        with _worker_connection(name) as conn:
            _seed_candidate(conn, 0.1, old)
    barrier = tmp_path / 'publication.hold'
    with _process('run', archive, publish_barrier=str(barrier)) as process:
        _wait_path(barrier, process)
        with _worker_connection(name) as conn:
            if control == 'candidate_capacity':
                _seed_candidate(conn, 0.3, datetime.now(UTC))
            elif control == 'freeze':
                adapter.record_optimizer_control_event(conn, store.OptimizerControlEventSubmission(
                    scope='LINEAGE', lineage=LINEAGE, control_kind='FREEZE', reason='publication race',
                    actor='operator', occurred_at=datetime.now(UTC),
                ))
            elif control == 'future_role':
                _seed_candidate(conn, 0.1, datetime.now(UTC) + timedelta(days=1))
            else:
                _seed_candidate(conn, 0.3, datetime.now(UTC))
                prior = adapter.load_lineage_roles(conn, LINEAGE)
                handover = tuple(store.RoleEventSubmission(lineage=LINEAGE, spec_sha256=digest, role=role,
                    effective_at=instant, reason='publication race', actor='operator', code_commit=COMMIT)
                    for instant in [datetime.now(UTC)]
                    for digest, role in ((prior.active, 'GUARD'), (prior.candidates[0], 'ACTIVE')))
                adapter.record_role_events(conn, store.RoleEventBatch(events=handover))
        changed = _invocation('read', archive)
        barrier.with_suffix('.release').touch()
        result = _reply(process, 1)
    expected = {'candidate_capacity': 'registry_candidate_capacity', 'freeze': 'registry_frozen',
                'changed_active': 'registry_active_changed', 'future_role': 'registry_role_chronology_veto'}[control]
    assert result['outcome'] == 'RUN_FAILED'
    assert result['dispositions'][LINEAGE]['status'] == expected
    after = _invocation('read', archive)
    assert after['roles'] == changed['roles']
    assert all(after['counts'][table] == changed['counts'][table]
               for table in ('model_specs', 'model_role_events', *LEDGER))


@NATIVE
def test_native_distinct_operations_race_for_last_candidate_slot(archive, tmp_path):
    name, _ = archive
    _invocation('init', archive)
    with _worker_connection(name) as conn:
        second = (name, _register(conn, tmp_path, schedule='native-second-v1'))
        _seed_candidate(conn, 0.1, AT + timedelta(minutes=1))
    paths = [tmp_path / 'first.publish', tmp_path / 'second.publish']
    with _process('run', archive, publish_barrier=str(paths[0])) as one, \
            _process('run', second, best_theta=0.3, publish_barrier=str(paths[1])) as two:
        for path, process in zip(paths, (one, two), strict=True):
            _wait_path(path, process)
        for path in paths:
            path.with_suffix('.release').touch()
        works = []
        for process in (one, two):
            output, stderr = process.communicate(timeout=15)
            assert process.returncode in (0, 1), stderr
            works.append((process.returncode, json.loads(output)))
    assert {row['outcome'] for _, row in works} == {'CANDIDATE_REGISTERED', 'RUN_FAILED'}
    failed = next(row for code, row in works if code == 1)
    assert failed['dispositions'][LINEAGE]['status'] == 'registry_candidate_capacity'
    readback = _invocation('read', archive)
    assert len(readback['roles']['candidates']) == 2
    assert readback['counts']['optimization_runs'] == 2


@pytest.mark.parametrize('competing_attempt', [1, 2])
@NATIVE
def test_native_same_operation_race_replays_terminal(archive, tmp_path, competing_attempt):
    _invocation('init', archive)
    barrier = tmp_path / 'publication.hold'
    with _process('run', archive, publish_barrier=str(barrier)) as writer:
        _wait_path(barrier, writer)
        with _process('run', archive, attempt=competing_attempt, forbid_compute=True) as waiting:
            barrier.with_suffix('.release').touch()
            initial = _reply(writer)
            replay = _reply(waiting)
    assert replay['replayed'] and replay['terminal_event_id'] == initial['terminal_event_id']
    assert replay['started_event_id'] == initial['started_event_id']
    readback = _invocation('read', archive)
    assert [row[0] for row in readback['events']] == ['STARTED', 'SUCCEEDED']
    assert readback['counts']['optimization_runs'] == 1


@NATIVE
def test_native_concurrent_initialization_reuses_original_baselines(archive, tmp_path):
    name, _ = archive
    release = tmp_path / 'init.go'
    with _process('init', archive, barrier=str(release)) as one:
        with _worker_connection(name) as locker, locker.transaction(), _process('init', archive) as two:
            _registry_lock(locker)
            _wait_path(release, one)
            release.with_suffix('.release').touch()
            locker.close()
            initialized = [_reply(process)[LINEAGE]['state'] for process in (one, two)]
    assert sorted(initialized) == ['initialized', 'skipped']
    readback = _invocation('read', archive)
    assert readback['counts']['model_specs'] == 1 and readback['counts']['model_role_events'] == 2


@NATIVE
def test_native_unavailable_database_does_not_seal_timeout(archive, tmp_path):
    name, _ = archive
    _invocation('init', archive)
    barrier, trace, clock, session = (tmp_path / part for part in ('compute.hold', 'trace.json', 'clock.json', 'pid.json'))
    with _process('run', archive, budget=[2.8, 7], barrier_trial=5, trace=str(trace), barrier=str(barrier),
                  clock_path=str(clock), session_path=str(session)) as process:
        _wait_path(barrier, process)
        timing = json.loads(clock.read_bytes())
        _advance_past(timing['compute'])
        pid = json.loads(session.read_bytes())
        with _worker_connection(name) as controller:
            terminated = controller.execute(
                'SELECT pg_terminate_backend(pid, 500) FROM pg_catalog.pg_stat_activity '
                'WHERE pid = %s AND pid <> pg_backend_pid() '
                'AND datname = %s AND datname = current_database() '
                "AND current_user = 'pal_app' AND usename = current_user "
                "AND backend_type = 'client backend'",
                (pid, name),
            ).fetchall()
            assert terminated == [(True,)]
        barrier.with_suffix('.release').touch()
        failed = _reply(process, 1)
    assert failed['error_code'] == 'papersim_optimize_transport_failure'
    assert failed['stage'] == 'failure_publication' and failed['retryable'] and not failed['indeterminate']
    durable = _invocation('read', archive)
    assert durable['row'] is None and [row[0] for row in durable['events']] == ['STARTED']


@NATIVE
def test_native_same_key_conflict_is_detected_after_success(archive, tmp_path):
    name, _ = archive
    _invocation('init', archive)
    committed = _invocation('run', archive)
    with _worker_connection(name) as conn:
        wrong_input = _register(conn, tmp_path, variant='changed-source')
    before = _invocation('read', archive)
    replay = _invocation('run', (name, wrong_input), code=1, forbid_compute=True, attempt=2)
    assert replay['error_code'] == 'papersim_optimize_request_conflict'
    assert _invocation('read', archive) == before
    assert committed['terminal_event_id'] == _invocation('run', archive, forbid_compute=True)['terminal_event_id']


@NATIVE
def test_native_mixed_empty_and_corrupt_registry_preserves_declared_scope(archive, tmp_path):
    from polymarket_alpha_lab.papersim.store import RoleEventSubmission

    name, _ = archive
    adapter = _adapter()
    _invocation('init', archive, targets=['synthetic', 'corrupt'])
    with _worker_connection(name) as conn:
        request = _register(conn, tmp_path, schedule='native-mixed-v1',
                            targets=('synthetic', 'empty', 'corrupt'))
        spec = replace(SyntheticFamily().baseline_spec(), params={'theta': 0.1})
        adapter.register_model_spec(conn, spec, source='manual', code_commit=COMMIT)
        corruption = RoleEventSubmission(lineage='runner-synth/corrupt', spec_sha256=spec.spec_sha256(),
            role='CANDIDATE', effective_at=AT, reason='existing invalid batch fixture', forward_plan={},
            actor='operator', code_commit=COMMIT)
        conn.execute(adapter._ROLE_EVENT_INSERT, (corruption.key(), corruption.lineage, corruption.spec_sha256,
            corruption.role, corruption.effective_at, corruption.reason, '{}', corruption.decision_id,
            corruption.actor, corruption.code_commit))
        before = _counts(conn)
    output = tmp_path / 'mixed.json'
    run = _invocation('run', (name, request), code=1, archive_path=str(output))
    assert run['outcome'] == 'RUN_FAILED' and run['registered_lineages'] == []
    payload = json.loads(output.read_bytes())
    scope = payload['original_started']['registry']['lineages']
    assert set(scope) == {LINEAGE, 'runner-synth/empty', 'runner-synth/corrupt'}
    assert scope[LINEAGE]['issue'] is None and scope[LINEAGE]['roles'] is not None
    assert scope['runner-synth/empty']['issue'] is None and scope['runner-synth/empty']['roles'] is None
    assert scope['runner-synth/corrupt']['issue'] == 'registry_role_history_invalid'
    assert payload['pure']['active_specs'].keys() == {LINEAGE}
    assert list(payload['pure']['holm_hypotheses']) and payload['pure']['holdout']['callbacks'] == []
    with _worker_connection(name) as conn:
        after = _counts(conn)
    assert all(before[table] == after[table] for table in ('model_specs', 'model_role_events', *LEDGER))


@pytest.mark.parametrize('legacy', [False, True])
@NATIVE
def test_native_rejection_linkage_and_legacy_quarantine(archive, tmp_path, legacy):
    from polymarket_alpha_lab.papersim import store

    name, _ = archive
    _invocation('init', archive)
    adapter = _adapter()
    with _worker_connection(name) as conn:
        second = (name, _register(conn, tmp_path, schedule='native-submission-v1'))
        if legacy:
            spec = SyntheticFamily().baseline_spec()
            winner = replace(spec, params={'theta': 0.4})
            adapter.record_optimization_run(conn, store.OptimizationRunSubmission(
                family=spec.family, data_cutoff=AT, lineages=[LINEAGE], rule_version='old-native-fixture',
                random_seed=SEED, search_space={}, code_commit=COMMIT, outcome='NO_IMPROVEMENT',
                scorecard={'lineages': {LINEAGE: {'winner_spec': winner.spec_sha256(),
                    'incumbent_spec': spec.spec_sha256(), 'registered': False}}},
            ))
    if not legacy:
        original = _invocation('run', archive, holdout_effect=-0.15)
        assert original['outcome'] == 'NO_IMPROVEMENT'
        assert len(original['scorecard']['stage1_rejections']) == 1
    sealed = tmp_path / 'closed.json'
    next_run = _invocation('run', second, code=int(legacy), archive_path=str(sealed))
    evidence = json.loads(sealed.read_bytes())
    assert evidence['pure']['holdout']['callbacks'] == []
    assert evidence['rejections'] == {}
    assert next_run['registered_lineages'] == []
    cooldown = evidence['pure']['cooldown'][LINEAGE]
    if legacy:
        assert next_run['outcome'] == 'RUN_FAILED'
        assert cooldown['reason'] == 'stage1_rejection_quarantined'
    else:
        assert next_run['outcome'] == 'NO_IMPROVEMENT'
        assert cooldown['evidence_kind'] == 'verified_rejection_receipt'
        assert cooldown['receipt_id'] == str(original['terminal_event_id'])
        assert cooldown['artifact_sha256'] == original['artifacts_sha256']


@NATIVE
def test_native_existing_spec_reuse_keeps_original_provenance(archive):
    name, _ = archive
    _invocation('init', archive, targets=['synthetic', 'peer'])
    with _worker_connection(name) as conn:
        _seed_candidate(conn, 0.4, AT + timedelta(minutes=1), target='peer')
        digest = _role_state(conn, 'runner-synth/peer').candidates[0]
        before = conn.execute('SELECT source, parent_spec_sha256, optimization_id, code_commit '
                              'FROM papersim.model_specs WHERE spec_sha256 = %s', (digest,)).fetchone()
    initial = _invocation('read', archive)
    result = _invocation('run', archive)
    assert result['dispositions'][LINEAGE]['spec_sha256'] == digest
    final = _invocation('read', archive)
    assert final['counts']['model_specs'] == initial['counts']['model_specs']
    with _worker_connection(name) as conn:
        assert conn.execute('SELECT source, parent_spec_sha256, optimization_id, code_commit '
                            'FROM papersim.model_specs WHERE spec_sha256 = %s', (digest,)).fetchone() == before
        assert _role_state(conn, 'runner-synth/peer').candidates == (digest,)


def _policy_cli_worker(database, root, options):
    from polymarket_alpha_lab.papersim import cli

    dsn = _rebind_database(OWNER_DSN, database).replace('user=pal_owner', 'user=pal_app')
    if options.get('fault') == 'manual_role':
        adapter = _adapter()
        original = adapter.record_role_events

        def abort_manual_role(conn, batch):
            original(conn, batch)
            raise OSError('test interruption during manual role publication')

        adapter.record_role_events = abort_manual_role
    return cli.main(['optimize', *options['argv'], '--dsn', dsn, '--repo-root', str(root)])


def _policy_cli(archive, *argv, expected_failure=None, fault=None):
    with _process('policy-cli', archive, argv=argv, fault=fault) as process:
        if expected_failure is None:
            return _reply(process)
        stdout, stderr = process.communicate(timeout=25)
        assert process.returncode == 1 and stdout == ''
        assert expected_failure in stderr


def _policy_batch(conn, lineage, moment, roles):
    from polymarket_alpha_lab.papersim import store

    return _adapter().record_role_events(conn, store.RoleEventBatch(events=tuple(
        store.RoleEventSubmission(lineage=lineage, spec_sha256=digest, role=role,
            effective_at=moment, reason='policy native fixture', actor='operator', code_commit=COMMIT,
            forward_plan={} if role == 'CANDIDATE' else None)
        for digest, role in roles
    )))


@pytest.fixture
def judged_rollback(archive, managed_repo, tmp_path):
    from tests.test_papersim_optimize_policy_native import (
        INIT, LINEAGE as lineage, SOCCER_FROZEN, _seed_pair, _seed_scan, candidate_spec,
    )

    name, _ = archive
    root, _ = managed_repo
    config = tmp_path / 'policy-scan-config.json'
    config.write_bytes(_json({'team': 'paper', 'operator': 'unattended', 'queues': [
        {'queue': 'q1', 'lineage': lineage, 'data_source_version': 'v1'},
    ]}))
    adapter = _adapter()
    guard = SOCCER_FROZEN.baseline_spec()
    active = candidate_spec()
    standby = replace(active, params={'kappa': 0.25})
    extra = replace(active, params={'kappa': 0.35})
    with _worker_connection(name) as conn:
        adapter.initialize_registry(conn, families=[SOCCER_FROZEN], actor='operator',
                                    code_commit=COMMIT, effective_at=INIT)
        for spec in (active, standby, extra):
            adapter.register_model_spec(conn, spec, source='manual', code_commit=COMMIT)
        _policy_batch(conn, lineage, INIT + timedelta(days=1), ((active.spec_sha256(), 'CANDIDATE'),))
        _policy_batch(conn, lineage, INIT + timedelta(days=2), ((standby.spec_sha256(), 'CANDIDATE'),))
        _policy_batch(conn, lineage, INIT + timedelta(days=3), (
            (guard.spec_sha256(), 'GUARD'), (active.spec_sha256(), 'ACTIVE'),
        ))
    with _connect(_rebind_database(OWNER_DSN, name), autocommit=False) as owner:
        scan_id = _seed_scan(owner, scan_key='native-judged-rollback', day=INIT + timedelta(days=18))
        dates = [datetime(2026, 11, day, 18, tzinfo=UTC) for day in (10, 11, 12, 17, 18, 19)]
        for index in range(156):
            starts = dates[index % len(dates)]
            _seed_pair(owner, scan_id=scan_id, index=index, start=starts,
                       observed=starts + timedelta(days=5), yes_won=(index % 2 == 0),
                       shadow_spec=guard.spec_sha256(), priced_version=active.model_version(),
                       priced_probability=0.5, shadow_is_better=True, role_at_prediction='GUARD')
        # These distinct, real pairs make the ordinary status/decide readers
        # measurable while leaving the other proposed comparisons below a look.
        late = datetime(2026, 11, 26, 18, tzinfo=UTC)
        for index, (shadow, priced, role) in enumerate((
            (standby, active, 'CANDIDATE'), (extra, active, 'CANDIDATE'), (active, standby, 'GUARD'),
        ), start=156):
            _seed_pair(owner, scan_id=scan_id, index=index, start=late,
                       observed=late + timedelta(days=2), yes_won=True,
                       shadow_spec=shadow.spec_sha256(), priced_version=priced.model_version(),
                       priced_probability=0.5, shadow_is_better=True, role_at_prediction=role)
    cli_archive = (name, root)
    decided = _policy_cli(cli_archive, 'decide', '--lineage', lineage, '--config', str(config))
    judgments = [row for row in decided['written'] if row['class'] == 'ROLLBACK']
    assert len(judgments) == 1 and judgments[0]['conclusion'] == 'ROLLBACK'
    with _worker_connection(name) as conn:
        decision = next(row for row in adapter.load_promotion_decisions(conn, lineage)
                        if row['decision_id'] == judgments[0]['decision_id'])
        assert decision['active_spec_sha256'] == decision['candidate_spec_sha256'] == active.spec_sha256()
        assert decision['evidence_watermark']['spec'] == active.spec_sha256()
        assert decision['evidence_watermark']['comparator'] == guard.spec_sha256()
    return cli_archive, config, lineage, decision, active, guard, standby, extra


@pytest.mark.parametrize('change', ['pair', 'later_candidate', 'equal_candidate'])
@NATIVE
def test_native_cli_rejects_stale_rollback_judgment(judged_rollback, change):
    archive, config, lineage, decision, active, guard, standby, extra = judged_rollback
    name, _ = archive
    adapter = _adapter()
    at = decision['decided_at']
    with _worker_connection(name) as conn:
        if change == 'pair':
            _policy_batch(conn, lineage, at - timedelta(hours=1), (
                (active.spec_sha256(), 'GUARD'), (standby.spec_sha256(), 'ACTIVE'),
                (guard.spec_sha256(), 'RETIRED'),
            ))
            expected = 'papersim_optimize_decision_pair_mismatch'
        else:
            boundary = at + timedelta(seconds=int(change == 'later_candidate'))
            _policy_batch(conn, lineage, boundary, ((extra.spec_sha256(), 'CANDIDATE'),))
            expected = 'papersim_role_event_out_of_order'
        before = _role_state(conn, lineage)
        histories = adapter.load_all_role_events(conn)
        decisions = adapter.load_promotion_decisions(conn, lineage)
    _policy_cli(archive, 'apply', '--decision-id', str(decision['decision_id']), '--config', str(config),
                expected_failure=expected)
    with _worker_connection(name) as fresh:
        assert _role_state(fresh, lineage) == before
        assert adapter.load_all_role_events(fresh) == histories
        assert adapter.load_promotion_decisions(fresh, lineage) == decisions
    status = _policy_cli(archive, 'status', '--lineage', lineage, '--config', str(config))
    rows = status['lineages']
    assert isinstance(rows, list)
    assert [row['lineage'] for row in rows] == [lineage]
    assert rows[0]['initialized'] is True


@pytest.mark.parametrize('manual', [False, True])
@NATIVE
def test_native_cli_exact_rollback_replay_after_later_handover(judged_rollback, manual):
    archive, config, lineage, decision, active, guard, standby, extra = judged_rollback
    name, _ = archive
    adapter = _adapter()
    at = decision['decided_at']
    if manual:
        command = ('rollback', '--lineage', lineage, '--reason', 'operator confirmed', '--actor', 'operator', '--at')
        with _worker_connection(name) as conn:
            histories = adapter.load_all_role_events(conn)
            decisions = adapter.load_promotion_decisions(conn, lineage)
        _policy_cli(archive, *command, (at - timedelta(days=40)).isoformat(),
                    expected_failure='papersim_optimize_decision_pair_mismatch')
        command = (*command, at.isoformat())
        _policy_cli(archive, *command, fault='manual_role',
                    expected_failure='test interruption during manual role publication')
        with _worker_connection(name) as fresh:
            assert adapter.load_all_role_events(fresh) == histories
            assert adapter.load_promotion_decisions(fresh, lineage) == decisions
    else:
        command = ('apply', '--decision-id', str(decision['decision_id']), '--config', str(config))
    applied = _policy_cli(archive, *command)
    assert applied['applied']['inserted'] is True
    with _worker_connection(name) as conn:
        rolled = _role_state(conn, lineage)
        assert rolled.active == guard.spec_sha256() and rolled.guard is None
        assert active.spec_sha256() in rolled.retired
        _policy_batch(conn, lineage, at + timedelta(seconds=1), (
            (guard.spec_sha256(), 'GUARD'), (standby.spec_sha256(), 'ACTIVE'),
        ))
        history_after = adapter.load_all_role_events(conn)
        decisions_after = adapter.load_promotion_decisions(conn, lineage)
    replay = _policy_cli(archive, *command)
    assert replay['applied']['inserted'] is False
    assert replay['applied']['role_event_ids'] == applied['applied']['role_event_ids']
    with _worker_connection(name) as fresh:
        final = _role_state(fresh, lineage)
        assert final.active == standby.spec_sha256() and final.guard == guard.spec_sha256()
        assert adapter.load_all_role_events(fresh) == history_after
        assert adapter.load_promotion_decisions(fresh, lineage) == decisions_after


@pytest.mark.parametrize('conclusion', ['PROMOTE', 'RETIRE', 'ROLLBACK'])
@NATIVE
def test_native_cli_cannot_apply_decision_to_substituted_or_unproved_pair(judged_rollback, conclusion):
    from polymarket_alpha_lab.papersim.store import PromotionDecisionSubmission

    archive, config, lineage, judgment, active, guard, standby, extra = judged_rollback
    name, _ = archive
    adapter = _adapter()
    instant = judgment['decided_at'] + timedelta(seconds=1)
    rollback = conclusion == 'ROLLBACK'
    candidate = active if rollback else standby
    evidence = {'spec': candidate.spec_sha256()}
    if not rollback:
        evidence['comparator'] = active.spec_sha256()
    with _worker_connection(name) as conn:
        row = adapter.record_promotion_decision(conn, PromotionDecisionSubmission(
            lineage=lineage, decision_class='ROLLBACK' if rollback else 'FORWARD',
            candidate_spec_sha256=candidate.spec_sha256(), active_spec_sha256=active.spec_sha256(),
            rule_version=judgment['rule_version'], look_number=2, decided_at=instant, decided_by='operator',
            evidence_watermark=evidence, metrics={'reason': 'paired_performance'}, conclusion=conclusion,
        ))
        if not rollback:
            _policy_batch(conn, lineage, instant - timedelta(hours=2), ((extra.spec_sha256(), 'CANDIDATE'),))
            _policy_batch(conn, lineage, instant - timedelta(hours=1), (
                (active.spec_sha256(), 'GUARD'), (extra.spec_sha256(), 'ACTIVE'), (guard.spec_sha256(), 'RETIRED'),
            ))
        before = adapter.load_all_role_events(conn)
        decisions = adapter.load_promotion_decisions(conn, lineage)
    _policy_cli(archive, 'apply', '--decision-id', str(row['decision_id']), '--config', str(config),
                expected_failure='papersim_optimize_decision_pair_mismatch')
    with _worker_connection(name) as fresh:
        assert adapter.load_all_role_events(fresh) == before
        assert adapter.load_promotion_decisions(fresh, lineage) == decisions


def _nba_fixture_snapshot():
    from polymarket_alpha_lab.papersim.espn_nba import NBA_TEAMS, SOURCE_KEY

    names = sorted(NBA_TEAMS)
    teams = {name: {'display': name, 'games': []} for name in names}
    for day_index in range(15):
        day = (datetime(2026, 10, 20, tzinfo=UTC) + timedelta(days=day_index)).date().isoformat()
        for pair_index, (home, away) in enumerate(zip(names[::2], names[1::2], strict=True)):
            if day_index % 2:
                home, away = away, home
            if pair_index == 14 and day_index < 2:
                continue
            home_points = 104 + (9 * pair_index + 3 * day_index) % 24
            away_points = 103 + (7 * pair_index + 5 * day_index) % 22
            if home_points == away_points:
                away_points += 1
            for team, opponent, is_home, points, other in ((home, away, True, home_points, away_points),
                                                          (away, home, False, away_points, home_points)):
                possessions = float(95 + (pair_index + day_index) % 11)
                teams[team]['games'].append({'date': day, 'home': is_home, 'opponent': opponent,
                    'pts': points, 'opp_pts': other, 'poss': possessions, 'ortg': 100 * points / possessions,
                    'drtg': 100 * other / possessions, 'rest_days': 1, 'b2b': False})
    return _json({'generated_at': '2026-11-10T07:10:08Z', 'source': SOURCE_KEY,
                  'season': '2026-27', 'teams': teams, 'injuries': {}})


def _register_nba_fixture(conn, request):
    from polymarket_alpha_lab.papersim import snapshots
    from polymarket_alpha_lab.papersim.optimize.runner import historical_protocol

    raw = _nba_fixture_snapshot()
    source_kind = 'optimizer_native_espn_fixture'
    document = {'schema_version': 1, 'protocol': historical_protocol('nba-pace-efficiency'), 'sources': [
        {'role': 'nba_snapshot', 'key': 'snapshot', 'kind': source_kind, 'schema_version': 1,
         'sha256': sha256(raw).hexdigest(), 'byte_length': len(raw)},
    ]}
    manifest_bytes = _json(document)
    with conn.transaction():
        for data, kind in ((raw, source_kind), (manifest_bytes, 'optimizer_experiment_manifest')):
            snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
                content=data, kind=kind, source='native-espn-fixture', fetched_at=AT,
            ))
    request.write_bytes(_json({'schema_version': 1, 'manifest_sha256': sha256(manifest_bytes).hexdigest(),
                               'manifest_byte_length': len(manifest_bytes)}))


def _nba_manifest_sources(conn):
    from polymarket_alpha_lab.papersim import snapshots
    from tests.test_papersim_espn_nba import _schedule_event, _schedule_payload, _summary_payload

    game = ('4011', '2026-10-22', 'ATL', 'BOS', 118, 113)
    schedule = _schedule_payload('ATL', 'Atlanta Hawks', [_schedule_event(*game)])
    summary = _summary_payload(*game, (90, 10, 15, 25), (85, 8, 12, 20))
    summary['header'].update(id='4011', season={'year': 2027, 'type': 2})
    summary['header']['competitions'][0]['date'] = '2026-10-22T17:00Z'
    kind = 'registered_research_input'
    positive = []
    with conn.transaction():
        for role, key, raw in (
            ('nba_snapshot', 'snapshot', _nba_fixture_snapshot()),
            ('team_schedule', 'team_schedule/schedule_ATL_2027.json', _json(schedule)),
            ('event_summary', 'event_summary/summary_4011.json', _json(summary)),
        ):
            stored = snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
                content=raw, kind=kind, source='native-nba-manifest-proof', fetched_at=AT,
            ))
            positive.append({'role': role, 'key': key, 'kind': kind, 'schema_version': 1,
                             'sha256': stored['content_sha256'], 'byte_length': len(raw)})
    absent = [
        {'role': 'event_summary', 'key': 'original_nov10_event_summaries',
         'absent_reason': 'matching_original_nov10_event_summary_bundle_unavailable'},
        {'role': 'team_schedule', 'key': 'original_nov10_team_schedules',
         'absent_reason': 'matching_original_nov10_team_schedule_bundle_unavailable'},
    ]
    for source in absent:
        source.update(kind=kind, schema_version=1, required=False, sha256=None, byte_length=None)
    return positive, absent


def _register_nba_manifest(conn, request, protocol, sources):
    from polymarket_alpha_lab.papersim import snapshots

    manifest = {'schema_version': 1, 'protocol': protocol, 'sources': sources}
    raw = _json(manifest)
    stored = snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
        content=raw, kind='optimizer_experiment_manifest', source='native-nba-manifest-proof', fetched_at=AT,
    ))
    pointer = {'schema_version': 1, 'manifest_sha256': stored['content_sha256'],
               'manifest_byte_length': len(raw)}
    request.write_bytes(_json(pointer))
    return manifest, pointer


def _run_nba_manifest_cli(name, request, *options):
    from polymarket_alpha_lab.papersim.cli import main

    app = _rebind_database(OWNER_DSN, name).replace('user=pal_owner', 'user=pal_app')
    return main(['optimize', 'run', '--dsn', app, '--repo-root', str(ROOT),
                 '--family', 'nba-pace-efficiency', '--as-of', '2026-11-10T05:00:00Z',
                 '--input-manifest', str(request), *options])


@pytest.mark.parametrize('with_absences', [True, False], ids=['original-absences', 'raw-sources'])
@NATIVE
def test_native_nba_manifests_accept_exact_absences_and_registered_positive_sources(
        archive, tmp_path, capsys, with_absences):
    from polymarket_alpha_lab.papersim import snapshots
    from polymarket_alpha_lab.papersim.optimize.runner import historical_protocol

    name, _ = archive
    request = tmp_path / 'nba-request.json'
    with _worker_connection(name) as conn:
        positive, absent = _nba_manifest_sources(conn)
        sources = [positive[0], *absent] if with_absences else positive
        _, pointer = _register_nba_manifest(conn, request, historical_protocol('nba-pace-efficiency'), sources)
        before = _preview_storage_facts(conn)
    assert _run_nba_manifest_cli(name, request, '--dry-run') == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview['status'] == 'not_run' and preview['manifest_sha256'] == pointer['manifest_sha256']
    assert preview['run_scorecard'] is None and preview['trial_count'] is None
    with _worker_connection(name) as conn:
        assert _preview_storage_facts(conn) == before
    assert _run_nba_manifest_cli(name, request) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed['outcome'] in {'INSUFFICIENT_DATA', 'NO_IMPROVEMENT'}
    with _worker_connection(name) as conn:
        payload = json.loads(snapshots.read_snapshot(conn, completed['artifacts_sha256'])['content'])
        assert conn.execute('SELECT phase FROM papersim.command_run_events ORDER BY run_event_id').fetchall() == [
            ('STARTED',), ('SUCCEEDED',)]
    assert payload['request']['manifest']['sha256'] == pointer['manifest_sha256']
    assert payload['request']['sources'] == {
        row['sha256']: [row['byte_length'], row['kind'], row['schema_version']]
        for row in sources if row['sha256'] is not None
    }
    assert payload['pure']['dataset_summary']['absent_sources'] == (
        [{'role': row['role'], 'key': row['key'], 'reason': row['absent_reason']} for row in absent]
        if with_absences else [])
    integrity = payload['pure']['nba_source_integrity']
    assert set(integrity['schedules']) == (set() if with_absences else {'ATL'})
    assert set(integrity['summaries']) == (set() if with_absences else {'4011'})


@NATIVE
def test_native_nba_manifest_mutations_reject_before_claim_and_inference(archive, tmp_path, capsys, monkeypatch):
    from polymarket_alpha_lab.papersim import snapshots
    from polymarket_alpha_lab.papersim.optimize import runner
    from polymarket_alpha_lab.papersim.optimize.families.nba import NBA_PACE_EFFICIENCY

    name, _ = archive
    adapter = _adapter()
    protocol = runner.historical_protocol('nba-pace-efficiency')
    request = tmp_path / 'nba-request.json'
    with _worker_connection(name) as conn:
        (base, schedule, summary), (no_summary, no_schedule) = _nba_manifest_sources(conn)
        original_summary = snapshots.read_snapshot(conn, summary['sha256'])['content'] + b'\n'
        stored = snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
            content=original_summary, kind=summary['kind'], source='native-nba-manifest-proof', fetched_at=AT,
        ))
    damaged_source = {**summary, 'sha256': stored['content_sha256'], 'byte_length': len(original_summary)}
    read_snapshot = snapshots.read_snapshot

    def read_with_wrong_bytes(conn, digest):
        row = read_snapshot(conn, digest)
        if digest == damaged_source['sha256']:
            assert row['content'] == original_summary
            return {**row, 'content': b'x' + original_summary[1:]}
        return row

    monkeypatch.setattr(snapshots, 'read_snapshot', read_with_wrong_bytes)

    invalid = 'papersim_optimize_manifest_invalid'
    unregistered = 'papersim_optimize_manifest_source_unregistered'
    bad_snapshot = 'papersim_optimize_snapshot_identity_invalid'
    cases = [
        ('wrong-role', {**no_summary, 'role': 'team_schedule'}, unregistered),
        ('wrong-key', {**no_summary, 'key': 'another_event_summary'}, unregistered),
        ('swapped-reason', {**no_summary, 'absent_reason': no_schedule['absent_reason']}, unregistered),
        ('positive-digest', {**no_summary, 'sha256': summary['sha256']}, invalid),
        ('positive-length', {**no_summary, 'byte_length': 1}, invalid),
        ('schema-version', {**no_summary, 'schema_version': 2}, unregistered),
        ('boolean-schema', {**no_summary, 'schema_version': True}, invalid),
        ('different-kind', {**no_summary, 'kind': 'espn_nba_snapshot'}, unregistered),
        ('required', {**no_summary, 'required': True}, invalid),
        ('without-required', {key: value for key, value in no_summary.items() if key != 'required'}, invalid),
        ('without-null-digest', {key: value for key, value in no_summary.items() if key != 'sha256'}, invalid),
        ('without-null-length', {key: value for key, value in no_summary.items() if key != 'byte_length'}, invalid),
        ('without-reason', {key: value for key, value in no_summary.items() if key != 'absent_reason'}, invalid),
        ('positive-provenance', {**no_summary, 'provenance': {'clock_basis': 'snapshot_generated_at'}}, unregistered),
        ('unregistered-absent-summary', {**no_summary, 'key': summary['key']}, unregistered),
        ('unregistered-absent-schedule', {**no_schedule, 'key': schedule['key']}, unregistered),
        ('positive-at-negative-key', {**summary, 'key': no_summary['key']}, unregistered),
    ]
    mutations = [(label, [base, modified, no_schedule], protocol, code, code)
                 for label, modified, code in cases]
    mutations.extend([
        ('duplicate', [base, no_summary, no_schedule, no_summary.copy()], protocol, invalid, invalid),
        ('extra-missing-key', [base, no_summary, no_schedule, {**no_summary, 'key': 'unlisted'}],
         protocol, unregistered, unregistered),
        ('missing-positive-snapshot', [schedule, no_summary, no_schedule], protocol, unregistered, unregistered),
        ('unregistered-source-bytes', [base, {**summary, 'sha256': sha256(b'other bytes').hexdigest()}],
         protocol, bad_snapshot, bad_snapshot),
        ('damaged-source-bytes', [base, damaged_source], protocol, bad_snapshot, bad_snapshot),
        ('positive-source-kind', [base, {**summary, 'kind': 'nba_wrong_kind'}], protocol, bad_snapshot, bad_snapshot),
        ('positive-source-schema', [base, {**summary, 'schema_version': 2}], protocol, bad_snapshot, bad_snapshot),
        ('positive-source-length', [base, {**summary, 'byte_length': summary['byte_length'] + 1}],
         protocol, bad_snapshot, bad_snapshot),
        ('another-protocol-pointer', [base, no_summary, no_schedule],
         {**protocol, 'schedule_id': 'a-different-fixture-experiment'}, invalid, 'papersim_optimize_request_conflict'),
    ])

    def forbidden(**_):
        pytest.fail('an invalid manifest reached inference')

    monkeypatch.setattr(runner, 'historical_run_arguments', forbidden)
    for label, sources, profile, cli_code, store_code in mutations:
        with _worker_connection(name) as conn:
            manifest, pointer = _register_nba_manifest(conn, request, profile, sources)
            before = _preview_storage_facts(conn)
        for flags in (('--dry-run',), ()):
            assert _run_nba_manifest_cli(name, request, *flags) == 1, label
            output = capsys.readouterr()
            assert not output.out and f'FAIL: {cli_code} ' in output.err, (label, output.err)
            with _worker_connection(name) as conn:
                assert _preview_storage_facts(conn) == before, label
        direct = dict(
            clock=adapter.optimization_command_clock(time.monotonic_ns()),
            family=NBA_PACE_EFFICIENCY, targets=protocol['targets'],
            data_cutoff=datetime.fromisoformat(protocol['data_cutoff']), random_seed=protocol['seed'],
            code_commit=COMMIT, attempt=1, schedule_id=protocol['schedule_id'],
            manifest_sha256=pointer['manifest_sha256'], manifest_byte_length=pointer['manifest_byte_length'],
            manifest=manifest, source_refs={row['sha256']: row['byte_length']
                                            for row in sources if row.get('sha256') is not None},
        )
        for action, kwargs in ((adapter.preview_managed_optimization, {}),
                               (adapter.run_managed_optimization, {'compute': forbidden})):
            with _worker_connection(name) as conn, pytest.raises(adapter.ManagedOptimizationError) as failure:
                action(conn, **direct, **kwargs)
            assert failure.value.code == store_code, (label, failure.value.code)
            assert failure.value.started_event_id is None and failure.value.attempt_event_id is None, label
            with _worker_connection(name) as conn:
                assert _preview_storage_facts(conn) == before, label


def _nba_cli_worker(action, root, request, options):
    from polymarket_alpha_lab.papersim import cli, snapshots
    from polymarket_alpha_lab.papersim.optimize import runner
    from polymarket_alpha_lab.papersim.optimize.families.nba import NbaPaceEfficiencyFamily

    adapter = _adapter()
    if action == 'nba-facts':
        with adapter.connect_managed(root) as conn:
            conn.autocommit = True
            print(json.dumps(_preview_storage_facts(conn)))
        return 0
    if action == 'nba-read':
        with adapter.connect_managed(root) as conn:
            conn.autocommit = True
            row = adapter.latest_optimization_for_lineage(conn, 'nba-pace-efficiency/moneyline')
            if row is not None:
                digest = row['artifacts_sha256']
                summary = snapshots.verify_snapshot(conn, digest, expected_schema_version=2)
                content = snapshots.read_snapshot(conn, digest)['content']
                assert summary['byte_length'] == len(content) and sha256(content).hexdigest() == digest
                Path(options['artifact_output']).write_bytes(content)
            events = conn.execute('SELECT phase, attempt, run_event_id FROM papersim.command_run_events '
                                  'ORDER BY run_event_id').fetchall()
            print(json.dumps({'row': row, 'counts': _counts(conn), 'events': events}, default=str))
        return 0

    original_clock = adapter.optimization_command_clock
    fit = NbaPaceEfficiencyFamily.fit
    predict = NbaPaceEfficiencyFamily.predict
    execute = runner.execute_optimization_run
    historical_arguments = runner.historical_run_arguments
    model_calls, callbacks, batches = [], [], []
    witnessed = {'model_calls': model_calls, 'callbacks': callbacks, 'batches': batches}
    default_clock = original_clock(time.monotonic_ns())
    assert default_clock.compute_ns - default_clock.start_ns == 3540 * NS
    assert default_clock.hard_ns - default_clock.start_ns == 3600 * NS
    _shorten_test_budget(5, 12)

    def clock_for_test(start):
        clock = original_clock(start)
        assert clock.compute_ns - start == 5 * NS and clock.hard_ns - start == 12 * NS
        if options.get('clock'):
            Path(options['clock']).write_bytes(_json({'compute': clock.compute_ns, 'hard': clock.hard_ns}))
        return clock

    def observed_fit(self, *args):
        assert not options.get('forbid_compute'), 'terminal replay entered fit'
        model_calls.append('fit')
        return fit(self, *args)

    def observed_prediction(self, *args):
        assert not options.get('forbid_compute'), 'terminal replay entered predict'
        result = predict(self, *args)
        model_calls.append('refusal' if result is None else 'prediction')
        return result

    def observed_arguments(**kwargs):
        assert not options.get('forbid_compute'), 'terminal replay entered data preparation'
        return historical_arguments(**kwargs)

    def observed_execute(**kwargs):
        assert not options.get('forbid_compute'), 'terminal replay entered search'
        evaluate, clock_check = kwargs['evaluate'], kwargs['deadline']
        paused = False

        def evaluate_and_observe(spec, fold, targets):
            evaluated = evaluate(spec, fold, targets)
            callbacks.append((spec.spec_sha256(), fold.block_id, targets))
            for target, side in sorted(evaluated.items()):
                batches.append({'spec': spec.spec_sha256(), 'fold': fold.block_id, 'target': target,
                                'predictions': sorted([row.event_key, row.line_key, row.probabilities, row.class_order]
                                                      for row in side.events),
                                'refusals': sorted([row.event_key, row.line_key, row.reason] for row in side.refusals)})
            return evaluated

        def deadline():
            nonlocal paused
            trial = options.get('barrier_trial')
            ready = (len(callbacks) == trial if trial is not None else
                     len(batches) >= 4 and any(row['predictions'] for row in batches))
            if ready and not paused:
                paused = True
                Path(options['trace']).write_bytes(_json(witnessed))
                _barrier(Path(options['barrier']))
            return clock_check()

        def holdout(*_):
            raise AssertionError('the genuine partial replay opened holdout')

        return execute(**{**kwargs, 'evaluate': evaluate_and_observe, 'deadline': deadline,
                            'evaluate_holdout': holdout})

    adapter.optimization_command_clock = clock_for_test
    NbaPaceEfficiencyFamily.fit = observed_fit
    NbaPaceEfficiencyFamily.predict = observed_prediction
    runner.execute_optimization_run = observed_execute
    runner.historical_run_arguments = observed_arguments
    if options.get('lose_commit_reply'):
        open_connection = cli._open_readonly_connection

        def connection_with_reply_loss(*args, **kwargs):
            conn = open_connection(*args, **kwargs)
            _lose_commit_reply(conn)
            return conn

        cli._open_readonly_connection = connection_with_reply_loss
    command = options.get('command', 'run')
    argv = ['optimize', command, '--managed', '--repo-root', str(root)]
    if command == 'run':
        argv += ['--family', 'nba-pace-efficiency', '--as-of', '2026-11-10T05:00:00Z',
                 '--input-manifest', str(request), '--seed', str(SEED), '--attempt', str(options.get('attempt', 1))]
        if options.get('dry_run'):
            argv.append('--dry-run')
    code = cli.main(argv)
    if options.get('finished'):
        Path(options['finished']).write_bytes(_json({**witnessed, 'acknowledged_ns': time.monotonic_ns()}))
    return code


def _nba_archived_forecasts(pure):
    trials = {row['trial_index']: row for row in pure['trials']}
    archived = {}
    for group in pure['search_trial_forecasts']:
        trial = trials[group['trial_index']]
        archived[trial['fold_block_id'], trial['spec_sha256'], group['target']] = (
            sorted([row['event_key'], row['line_key'], row['probabilities'], row['class_order']]
                   for row in group['predictions']),
            sorted(group['refusals']),
        )
    return archived


@pytest.mark.skipif(not os.environ.get('POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX'),
                    reason='native managed restart proof requires the qualified PG18 binary prefix')
@NATIVE
def test_native_normal_managed_cli_seals_real_nba_timeout_and_restarts(managed_repo, managed_runtime, tmp_path):
    from polymarket_alpha_lab.project_postgres.server import ProjectPostgres

    root, head = managed_repo
    request = tmp_path / 'nba-request.json'
    archive = (str(root), request)
    trace, barrier, clock = (tmp_path / name for name in ('observed.json', 'compute.hold', 'clock.json'))
    finished, artifact_path = (tmp_path / name for name in ('finished.json', 'archived.json'))
    owner, conn = managed_runtime
    _register_nba_fixture(conn, request)
    _invocation('nba-cli', archive, command='init')
    before = _invocation('nba-read', archive)
    before_facts = _invocation('nba-facts', archive)
    preview = _invocation('nba-cli', archive, dry_run=True, forbid_compute=True)
    assert preview['status'] == 'not_run' and preview['outcome'] is None
    assert _invocation('nba-facts', archive) == before_facts
    with _process('nba-cli', archive, trace=str(trace), barrier=str(barrier), clock=str(clock),
                  finished=str(finished)) as process:
        _wait_path(barrier, process)
        times = json.loads(clock.read_bytes())
        _advance_past(times['compute'])
        barrier.with_suffix('.release').touch()
        published = _reply(process, 1)
    assert published['outcome'] == 'RUN_FAILED' and published['error_code'] == 'papersim_optimize_budget_exhausted'
    observed = json.loads(trace.read_bytes())
    completion = json.loads(finished.read_bytes())
    assert completion['acknowledged_ns'] < times['hard']
    assert 'fit' in observed['model_calls'] and 'prediction' in observed['model_calls']
    assert completion['model_calls'] == observed['model_calls'] and completion['batches'] == observed['batches']
    after = _invocation('nba-read', archive, artifact_output=str(artifact_path))
    payload = json.loads(artifact_path.read_bytes())
    assert payload['request']['code_commit'] == head and payload['budget']['stopping_causes'] == ['compute_time']
    assert [event[0] for event in after['events']] == ['STARTED', 'FAILED']
    assert after['row']['optimization_id'] == published['optimization_id']
    assert after['row']['terminal_event_id'] == published['terminal_event_id']
    witnessed = {(row['fold'], row['spec'], row['target']): (row['predictions'], row['refusals'])
                 for row in observed['batches']}
    archived = _nba_archived_forecasts(payload['pure'])
    assert archived == witnessed and payload['pure']['holdout']['callbacks'] == []
    assert payload['rejections'] == {}
    assert all(after['counts'][table] == before['counts'][table] for table in (
        'model_specs', 'model_role_events', *LEDGER))
    after_facts = _invocation('nba-facts', archive)
    owner.close()
    conn.close()
    postgres = ProjectPostgres(root)
    assert postgres.down()['status'] == 'stopped'
    assert postgres.up()['status'] == 'running'
    for attempt in (1, 2):
        replay = _invocation('nba-cli', archive, code=1, attempt=attempt, forbid_compute=True)
        assert replay['replayed'] is True
        for field in ('optimization_id', 'started_event_id', 'terminal_event_id',
                      'artifacts_sha256', 'dispositions', 'outcome'):
            assert replay[field] == published[field]
        preview = _invocation('nba-cli', archive, code=1, dry_run=True, attempt=attempt, forbid_compute=True)
        assert preview['status'] == 'sealed' and preview['exit_code'] == 1
        assert preview['run_scorecard'] == after['row']['scorecard']
        assert preview['trial_count'] == after['row']['trial_count']
        for field in ('optimization_id', 'started_event_id', 'terminal_event_id', 'started_event_key',
                      'terminal_event_key', 'artifacts_sha256', 'artifact_byte_length', 'outcome', 'error_code'):
            assert preview[field] == published[field]
        assert _invocation('nba-facts', archive) == after_facts
    restarted = tmp_path / 'restarted.json'
    assert _invocation('nba-read', archive, artifact_output=str(restarted)) == after
    assert restarted.read_bytes() == artifact_path.read_bytes()


def _connection_dsn(database, port, timeout, *, uri=False):
    from psycopg.conninfo import make_conninfo

    params = dict(sslmode='disable', gssencmode='disable')
    if timeout is not None:
        params['connect_timeout'] = timeout
    if uri:
        return f'postgresql://pal_app@127.0.0.1:{port}/{database}?{urlencode(params)}'
    return make_conninfo('', host='127.0.0.1', port=port, user='pal_app', dbname=database, **params)


def _connection_cli_worker(root, request, options):
    from polymarket_alpha_lab.papersim import cli
    from polymarket_alpha_lab.papersim.optimize import runner

    adapter = _adapter()
    observed = {'connections': [], 'compute_calls': 0}
    descriptors_by_wait = []
    completed_handshakes = []
    make_clock = adapter.optimization_command_clock
    open_connection = cli._open_readonly_connection
    wait_for_database = adapter._wait_for_database
    historical_arguments = runner.historical_run_arguments
    default_selector = adapter.DefaultSelector
    command_clock = None
    _shorten_test_budget(*options['budget'])

    def clock_for_test(start):
        nonlocal command_clock
        command_clock = make_clock(start)
        observed.update(start_ns=start, compute_ns=command_clock.compute_ns, hard_ns=command_clock.hard_ns)
        if options.get('expire_before_connect'):
            _advance_past(command_clock.hard_ns)
        return command_clock

    def observe_connection(*args, **kwargs):
        if observed['connections'] and options.get('reconnect_remaining'):
            _advance_past(observed['hard_ns'] - round(options['reconnect_remaining'] * NS))
        observed['connections'].append({'entered_ns': time.monotonic_ns(), 'waits': [],
                                        'same_clock': kwargs['clock'] is command_clock})
        return open_connection(*args, **kwargs)

    def observe_wait(generator, cutoff_ns, stage, **kwargs):
        if stage in {'publication_precommit', 'failure_publication_precommit'} and 'commit' in options:
            Path(options['commit']).touch()
        if stage != 'connect':
            return wait_for_database(generator, cutoff_ns, stage, **kwargs)

        wait = {'entered_ns': time.monotonic_ns(), 'cutoff_ns': cutoff_ns, 'selects': []}
        observed['connections'][-1]['waits'].append(wait)
        descriptors, selectors = set(), []

        class ObservedSelector(default_selector):
            def __init__(self):
                super().__init__()
                selectors.append(self)

            def register(self, fileobj, events, data=None):
                descriptors.add(fileobj)
                return super().register(fileobj, events, data)

            def select(self, timeout=None):
                wait['selects'].append({'at_ns': time.monotonic_ns(),
                                        'timeout_ns': round(timeout * NS)})
                return super().select(timeout)

        adapter.DefaultSelector = ObservedSelector
        try:
            pgconn = wait_for_database(generator, cutoff_ns, stage, **kwargs)
            if options.get('delay_handoff'):
                completed_handshakes.append(pgconn)
                _advance_past(cutoff_ns)
            return pgconn
        finally:
            adapter.DefaultSelector = default_selector
            wait['selectors_closed'] = bool(selectors) and all(s.get_map() is None for s in selectors)
            wait['descriptors'] = len(descriptors)
            descriptors_by_wait.append((wait, descriptors))

    def observe_compute(**kwargs):
        observed['compute_calls'] += 1
        return historical_arguments(**kwargs)

    adapter.optimization_command_clock = clock_for_test
    cli._open_readonly_connection = observe_connection
    adapter._wait_for_database = observe_wait
    runner.historical_run_arguments = observe_compute
    if options.get('managed'):
        source = ['--managed']
    else:
        dsn = _connection_dsn(options['database'], options['port'], options.get('connect_timeout'),
                              uri=options.get('uri', False))
        source = ['--dsn', dsn]
    argv = ['optimize', 'run', '--repo-root', str(root), *source, '--family', 'nba-pace-efficiency',
            '--as-of', '2026-11-10T05:00:00Z', '--input-manifest', str(request),
            '--attempt', str(options.get('attempt', 1))]
    if options.get('dry_run'):
        argv.append('--dry-run')
    with redirect_stdout(StringIO()) as output, redirect_stderr(StringIO()) as errors:
        code = cli.main(argv)
        observed['returned_ns'] = time.monotonic_ns()
    for wait, descriptors in descriptors_by_wait:
        wait['closed_descriptors'] = sum(not _fd_is_open(fd) for fd in descriptors)
    if options.get('delay_handoff'):
        from psycopg.pq import ConnStatus

        observed['finished_unreleased'] = len(completed_handshakes) == 1 and (
            completed_handshakes[0].status == ConnStatus.BAD)
    reason = re.fullmatch(r'optimize run: FAIL: (papersim_[a-z0-9_]+) (\{.*\})\n', errors.getvalue())
    if reason:
        observed.update(reason=reason[1], details=json.loads(reason[2]))
    if output.getvalue():
        observed['receipt'] = json.loads(output.getvalue())
    observed['exit_code'] = code
    print(json.dumps(observed, sort_keys=True), flush=True)
    if 'returned' in options:
        _barrier(Path(options['returned']))
    return code


def _fd_is_open(fd):
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


def _silent_handshake(listener):
    with listener.accept()[0] as client:
        accepted_ns = time.monotonic_ns()
        client.settimeout(12)
        startup = client.makefile('rb')
        with startup:
            header = startup.read(4)
            assert len(header) == 4, 'expected startup packet length'
            length = int.from_bytes(header, 'big')
            assert 8 <= length <= 8192 and len(startup.read(length - 4)) == length - 4
            assert startup.read(1) == b'', 'client wrote past the startup packet without a response'
        return accepted_ns, time.monotonic_ns()


def _finish_connection_process(process, returned, code):
    _wait_path(returned, process)
    assert process.poll() is None
    returned.with_suffix('.release').touch()
    return _reply(process, code)


def _assert_handshake_deadline(result, observed_peer, *, timeout, winner):
    accepted_ns, disconnected_ns = observed_peer
    connection = result['connections'][-1]
    hard = result['hard_ns']
    assert connection['same_clock']
    assert result['start_ns'] <= connection['entered_ns'] <= accepted_ns < hard
    if winner == 'hard':
        assert max(disconnected_ns, result['returned_ns']) < hard + NS // 4, {
            'hard_ns': hard, 'accepted_ns': accepted_ns, 'disconnected_ns': disconnected_ns,
            'returned_ns': result['returned_ns'], 'reason': result.get('reason'),
        }
    wait, = connection['waits']
    cutoff = wait['cutoff_ns']
    lower = connection['entered_ns'] + timeout * NS
    upper = wait['entered_ns'] + timeout * NS
    assert min(hard, lower) <= cutoff <= min(hard, upper)
    if winner == 'hard':
        assert hard < lower and cutoff == hard
    elif winner == 'connect':
        assert cutoff < hard and lower <= cutoff <= upper
    else:
        assert abs(lower - hard) < NS // 4
    assert wait['selects'] and wait['selectors_closed']
    for selected in wait['selects']:
        assert abs(selected['at_ns'] + selected['timeout_ns'] - cutoff) < NS // 20
    assert wait['descriptors'] == wait['closed_descriptors'] == 1
    assert cutoff - NS // 100 <= disconnected_ns <= cutoff + NS // 4
    assert cutoff - NS // 100 <= result['returned_ns'] <= cutoff + NS // 4
    return {'command_start_ns': result['start_ns'], 'compute_ns': result['compute_ns'], 'hard_ns': hard,
            'connection_entry_ns': connection['entered_ns'], 'wait_entry_ns': wait['entered_ns'],
            'effective_timeout_ns': timeout * NS, 'effective_cutoff_ns': cutoff,
            'accepted_ns': accepted_ns, 'disconnected_ns': disconnected_ns,
            'returned_ns': result['returned_ns'], 'timeout_winner': 'hard' if cutoff == hard else 'connect',
            'reason': result['reason'],
            'selects': wait['selects'], 'selectors_closed': wait['selectors_closed'],
            'closed_descriptors': wait['closed_descriptors'], 'compute_calls': result['compute_calls'],
            'exit_code': result['exit_code']}


@pytest.fixture
def connection_archive(archive, managed_repo, tmp_path):
    from polymarket_alpha_lab.papersim.optimize.runner import family_singleton

    database, _ = archive
    root, head = managed_repo
    request = tmp_path / 'connection-pointer.json'
    with _worker_connection(database) as conn:
        _register_nba_fixture(conn, request)
        port = int(conn.execute('SHOW port').fetchone()[0])
        with _adapter().registry_write_transaction(conn):
            _adapter().initialize_registry(conn, families=[family_singleton('nba-pace-efficiency')],
                                           actor='fixture', code_commit=head, effective_at=AT)
    return (root, request), database, port


def _connection_database_facts(database):
    with _worker_connection(database) as fresh:
        return _preview_storage_facts(fresh)


@pytest.mark.parametrize(('source', 'given', 'effective', 'hard', 'winner'), [
    pytest.param('explicit', 2, 2, 0.8, 'hard', id='command-hard'),
    pytest.param('explicit', 1, 2, 4, 'connect', id='normalized-connect-timeout'),
    pytest.param('explicit', 2, 2, 2.03, 'near', id='nearby-deadlines'),
    pytest.param('managed', None, 5, 0.8, 'hard', id='managed-command-hard'),
    pytest.param('managed', None, 5, 7, 'connect', id='managed-connect-timeout'),
])
@NATIVE
def test_native_optimizer_handshake_uses_earlier_deadline(connection_archive, tmp_path, source, given,
                                                        effective, hard, winner, record_property):
    from tests.test_papersim_store_native import _write_managed_instance

    target, database, _ = connection_archive
    before = _connection_database_facts(database)
    returned = tmp_path / 'connection-returned'
    with socket.create_server(('127.0.0.1', 0)) as listener:
        listener.settimeout(15)
        port = listener.getsockname()[1]
        if source == 'managed':
            _write_managed_instance(target[0], ('a' * 32, 'b' * 64, '1234567890123456'), port=port)
        with _process('connection-cli', target, database=database, port=port, managed=source == 'managed',
                      budget=(hard / 2, hard), connect_timeout=given, dry_run=winner != 'near',
                      returned=str(returned)) as process:
            observed_peer = _silent_handshake(listener)
            result = _finish_connection_process(process, returned, 1)
    observed = _assert_handshake_deadline(result, observed_peer, timeout=effective, winner=winner)
    assert result['compute_calls'] == 0
    assert result['reason'] == 'papersim_optimize_deadline'
    assert result['details'] == {'stage': 'connect', 'retryable': True, 'indeterminate': False,
                                 'started_event_id': None, 'attempt_event_id': None}
    assert before['command_run_events'] == [] and _connection_database_facts(database) == before
    record_property('handshake', _json({**observed, 'command_events': 0, 'storage_changes': 0}).decode())


@NATIVE
def test_native_expired_optimizer_clock_stops_before_handshake(connection_archive, tmp_path):
    target, database, _ = connection_archive
    before = _connection_database_facts(database)
    returned = tmp_path / 'connection-returned'
    with socket.create_server(('127.0.0.1', 0)) as listener:
        with _process('connection-cli', target, database=database, port=listener.getsockname()[1],
                      budget=(0.1, 0.2), expire_before_connect=True, returned=str(returned)) as process:
            _wait_path(returned, process)
            listener.settimeout(0.1)
            with pytest.raises(TimeoutError):
                listener.accept()
            result = _finish_connection_process(process, returned, 1)
    connection, = result['connections']
    assert connection['entered_ns'] >= result['hard_ns'] and connection['waits'] == []
    assert result['reason'] == 'papersim_optimize_deadline' and result['details']['stage'] == 'connect'
    assert result['compute_calls'] == 0
    assert _connection_database_facts(database) == before


def test_explicit_optimizer_connection_validates_before_loading_driver(monkeypatch):
    import builtins

    adapter = _adapter()
    import_module = builtins.__import__

    def reject_driver(name, *args, **kwargs):
        assert not name.startswith('psycopg'), 'invalid DSN reached the driver'
        return import_module(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', reject_driver)
    clock = adapter.optimization_command_clock(time.monotonic_ns())
    for active_clock in (None, clock):
        with pytest.raises(ValueError, match='must point to local Postgres'):
            adapter.connect_explicit('host=unapproved.invalid', clock=active_clock)


def _cut_commit_reply(client, server, data, *, committed):
    request = b'Q\0\0\0\x0bCOMMIT\0'
    while len(data) < len(request):
        chunk = client.recv(len(request) - len(data))
        assert chunk, 'incomplete COMMIT request'
        data += chunk
    assert data == request, 'expected only the publication COMMIT at the fault boundary'
    if committed:
        server.sendall(data)
        with server.makefile('rb') as messages:
            assert messages.read(5) == b'C\0\0\0\x0b'
            assert messages.read(7) == b'COMMIT\0'
            assert messages.read(6) == b'Z\0\0\0\x05I'


def _relay_postgres(listener, port, *, pause=0, commit=None, committed=False):
    """Relay one trust-authenticated loopback session, optionally losing its COMMIT reply."""
    with listener.accept()[0] as client:
        accepted = time.monotonic_ns()
        client.settimeout(12)
        if pause:
            time.sleep(pause)
        with socket.create_connection(('127.0.0.1', port), timeout=12) as server:
            resumed = time.monotonic_ns()
            with DefaultSelector() as selector:
                selector.register(client, EVENT_READ, server)
                selector.register(server, EVENT_READ, client)
                while True:
                    readable = selector.select(12)
                    assert readable, 'no traffic on the fixture relay'
                    for key, _ in readable:
                        data = key.fileobj.recv(65536)
                        if not data:
                            assert commit is None, 'connection closed before the fixture COMMIT boundary'
                            return accepted, resumed, time.monotonic_ns()
                        if key.fileobj is client and commit is not None and commit.exists():
                            _cut_commit_reply(client, server, data, committed=committed)
                            return accepted, resumed, time.monotonic_ns()
                        key.data.sendall(data)


@pytest.mark.parametrize('timeout', [None, 0, 1], ids=['default-keyword', 'zero-uri', 'normalized-short'])
@NATIVE
def test_native_explicit_handshake_recovers_before_earlier_deadline(connection_archive, timeout, record_property):
    from psycopg.conninfo import timeout_from_conninfo
    from psycopg.pq import TransactionStatus

    target, database, backend_port = connection_archive
    before = _connection_database_facts(database)
    effective = timeout_from_conninfo({} if timeout is None else {'connect_timeout': timeout})
    with socket.create_server(('127.0.0.1', 0)) as listener, ThreadPoolExecutor(max_workers=1) as relay:
        listener.settimeout(15)
        first_connection = relay.submit(_relay_postgres, listener, backend_port, pause=0.15)
        result = _invocation('connection-cli', target, database=database, port=listener.getsockname()[1],
                             budget=(1, effective + 1), connect_timeout=timeout, uri=timeout == 0, dry_run=True)
        accepted, resumed, _ = first_connection.result(timeout=15)
    connection, = result['connections']
    wait, = connection['waits']
    cutoff = wait['cutoff_ns']
    assert connection['same_clock']
    assert result['start_ns'] < accepted < resumed < result['returned_ns'] < cutoff < result['hard_ns']
    assert connection['entered_ns'] + effective * NS <= cutoff <= wait['entered_ns'] + effective * NS
    assert resumed - accepted >= 150_000_000
    assert wait['selectors_closed']
    assert wait['descriptors'] == wait['closed_descriptors'] == 1
    assert result['receipt']['status'] == 'not_run' and result['compute_calls'] == 0
    assert _connection_database_facts(database) == before
    with _adapter().connect_explicit(_connection_dsn(database, backend_port, timeout, uri=timeout == 0)) as conn:
        assert conn.info.transaction_status == TransactionStatus.IDLE and not conn.autocommit
        assert conn.execute('SELECT 1').fetchone() == (1,)
    record_property('recovery', _json({'accepted_ns': accepted, 'resumed_ns': resumed,
                                       'effective_timeout_ns': effective * NS, 'effective_cutoff_ns': cutoff,
                                       'hard_ns': result['hard_ns'], 'returned_ns': result['returned_ns'],
                                       'compute_calls': result['compute_calls'], 'storage_changes': 0,
                                       'exit_code': result['exit_code']}).decode())


@NATIVE
def test_native_completed_handshake_after_deadline_is_closed_without_delivery(connection_archive):
    target, database, port = connection_archive
    before = _connection_database_facts(database)
    result = _invocation('connection-cli', target, code=1, database=database, port=port,
                         budget=(0.4, 0.8), delay_handoff=True)
    wait, = result['connections'][0]['waits']
    assert result['returned_ns'] >= result['hard_ns'] == wait['cutoff_ns']
    assert result['reason'] == 'papersim_optimize_deadline' and result['details']['stage'] == 'connect'
    assert result['finished_unreleased'] and result['compute_calls'] == 0
    assert wait['selectors_closed'] and wait['descriptors'] == wait['closed_descriptors'] == 1
    assert _connection_database_facts(database) == before


@pytest.mark.parametrize(('committed', 'connect_timeout', 'winner'), [
    pytest.param(True, 1, 'connect', id='confirmed-in-database-connect-timeout'),
    pytest.param(False, 5, 'hard', id='commit-not-delivered-command-hard'),
])
@NATIVE
def test_native_optimizer_commit_confirmation_reuses_earlier_handshake_deadline(
        connection_archive, tmp_path, committed, connect_timeout, winner, record_property):
    target, database, backend_port = connection_archive
    before = _connection_database_facts(database)
    boundary, returned = (tmp_path / name for name in ('publication-commit', 'connection-returned'))
    with socket.create_server(('127.0.0.1', 0)) as listener, ThreadPoolExecutor(max_workers=1) as relay:
        listener.settimeout(15)
        first_connection = relay.submit(_relay_postgres, listener, backend_port, commit=boundary,
                                        committed=committed)
        with _process('connection-cli', target, database=database, port=listener.getsockname()[1],
                      budget=(6, 8), connect_timeout=connect_timeout, commit=str(boundary),
                      reconnect_remaining=0.7 if winner == 'hard' else None,
                      returned=str(returned)) as process:
            _, _, severed = first_connection.result(timeout=15)
            observed_peer = _silent_handshake(listener)
            result = _finish_connection_process(process, returned, 1)
    first, reconnect = result['connections']
    assert first['entered_ns'] < severed < reconnect['entered_ns']
    assert first['same_clock'] and reconnect['same_clock']
    observed = _assert_handshake_deadline(result, observed_peer, timeout=max(2, connect_timeout), winner=winner)
    assert result['reason'] == 'papersim_optimize_commit_unconfirmed'
    details = result['details']
    assert details['stage'] == 'publication_acknowledgment'
    assert details['indeterminate'] and details['retryable'] and details['attempt_event_id'] is None
    assert result['compute_calls'] == 1 and 'receipt' not in result

    with _worker_connection(database) as fresh:
        event_rows = fresh.execute('SELECT phase, attempt, run_event_id, input_evidence, output_evidence '
                                   'FROM papersim.command_run_events ORDER BY run_event_id').fetchall()
        runs = fresh.execute('SELECT optimization_id, artifacts_sha256 FROM papersim.optimization_runs').fetchall()
        after = _preview_storage_facts(fresh)
        if committed:
            (optimization_id, digest), = runs
            content, size = fresh.execute('SELECT content, byte_length FROM papersim.snapshots '
                                          'WHERE content_sha256 = %s', (digest,)).fetchone()
            assert len(content) == size and sha256(content).hexdigest() == digest
    started = details['started_event_id']
    events = [event[:3] for event in event_rows]
    assert started is not None and events[0] == ('STARTED', 1, started)
    if committed:
        _, _, terminal_id, inputs, outputs = event_rows[1]
        assert events == [('STARTED', 1, started), ('SUCCEEDED', 1, terminal_id)]
        assert inputs[0]['original_started_event_id'] == started
        assert (outputs[0]['optimization_id'], outputs[0]['artifacts_sha256'],
                outputs[0]['artifact_byte_length']) == (optimization_id, digest, size)
        expected = {'optimization_id': optimization_id, 'started_event_id': started, 'terminal_event_id': terminal_id,
                    'artifacts_sha256': digest, 'artifact_byte_length': size}
        for attempt in (1, 2):
            replay = _invocation('connection-cli', target, database=database, port=backend_port,
                                 budget=(6, 8), connect_timeout=connect_timeout, attempt=attempt)
            assert replay['receipt']['replayed'] and replay['compute_calls'] == 0
            assert {key: replay['receipt'][key] for key in expected} == expected
            with _worker_connection(database) as fresh:
                assert fresh.execute('SELECT content FROM papersim.snapshots WHERE content_sha256 = %s',
                                     (digest,)).fetchone() == (content,)
        assert _connection_database_facts(database) == after
    else:
        assert events == [('STARTED', 1, started)] and runs == []
        assert {k: v for k, v in after.items() if k != 'command_run_events'} == {
            k: v for k, v in before.items() if k != 'command_run_events'}
    record_property('confirmation', _json({**observed, 'severed_ns': severed,
                                           'stage': details['stage'], 'command_events': len(events),
                                           'runs': len(runs)}).decode())


@MANAGED_NATIVE
@pytest.mark.parametrize(('scenario', 'outcome'), [
    ('registered', 'CANDIDATE_REGISTERED'),
    ('unregistered', 'NO_IMPROVEMENT'),
    ('rejected', 'NO_IMPROVEMENT'),
    ('budget', 'RUN_FAILED'),
])
def test_native_rule_source_cli_and_readback(managed_runtime, managed_repo, monkeypatch, tmp_path, capsysbinary,
                                             record_property, scenario, outcome):
    """Run the real CLI/store and crypto model over a sealed synthetic experiment."""
    from polymarket_alpha_lab.papersim import cli, snapshots, store_psycopg
    from polymarket_alpha_lab.papersim.optimize import runner
    from polymarket_alpha_lab.papersim.optimize.families.crypto import CRYPTO_GBM
    from tests.test_papersim_optimize_runner import _actual_crypto_inputs, _crypto_holdout, _crypto_search_records

    _, app = managed_runtime
    repo, _ = managed_repo
    cutoff = datetime(2026, 11, 20, 6, tzinfo=UTC)
    at = cutoff + timedelta(hours=1)
    lineage = 'crypto-gbm/touch'
    evidence_commit = os.environ.get('PAL_MO6_RULE_EVIDENCE_COMMIT')
    if evidence_commit is not None:
        assert cli.require_clean_worktree_path(ROOT) == evidence_commit
    code_commit = evidence_commit or COMMIT
    receipt_dir = os.environ.get('PAL_MO6_RULE_EVIDENCE_DIR')
    sources = {'scenario': scenario, 'settled_markets': 150, 'expiry_days': 20}
    content = _json(sources)
    content_sha = sha256(content).hexdigest()
    protocol = {
        'family': CRYPTO_GBM.family_id, 'rule_version': 'v1', 'procedure_version': CRYPTO_GBM.procedure_version,
        'schedule_id': 'rule-source-crypto-fixture-v1', 'targets': ['touch'], 'seed': SEED,
        'data_cutoff': cutoff.isoformat(), 'source_calendar': 'UTC expiry days', 'cohort': 'synthetic',
    }
    manifest = _json({'schema_version': 1, 'protocol': protocol, 'sources': [{
        'role': 'evidence', 'key': 'rule-source-crypto.json', 'kind': SOURCE_KIND, 'schema_version': 1,
        'sha256': content_sha, 'byte_length': len(content),
    }]})
    manifest_sha = sha256(manifest).hexdigest()
    with app.transaction():
        for raw, kind in ((content, SOURCE_KIND), (manifest, 'optimizer_experiment_manifest')):
            snapshots.register_snapshot(app, snapshots.SnapshotRegistration(
                content=raw, kind=kind, source='rule-source-native-fixture', fetched_at=at,
            ))
    pointer = {'schema_version': 1, 'manifest_sha256': manifest_sha, 'manifest_byte_length': len(manifest)}
    pointer_path = tmp_path / 'rule-source-crypto-pointer.json'
    pointer_path.write_bytes(_json(pointer))

    holdout = _crypto_holdout(sources['settled_markets'], sources['expiry_days'])
    records = _crypto_search_records() + holdout
    arguments, calls = _actual_crypto_inputs(
        records, {row.group_id for row in holdout}, dataset_summary=sources, schedule_id=protocol['schedule_id'],
    )
    if scenario == 'rejected':
        holdout_keys = {row.event_key for row in holdout}
        arguments['fixture_lines'] = tuple(
            replace(row, outcome_index=0) if row.event_key in holdout_keys else row
            for row in arguments['fixture_lines']
        )
    if scenario == 'unregistered':
        evaluate = arguments['evaluate']

        def equal_search(spec, fold, targets):
            return evaluate(CRYPTO_GBM.baseline_spec(), fold, targets)

        arguments['evaluate'] = equal_search
    if scenario == 'budget':
        monkeypatch.setattr(runner, 'MAX_TRIALS', 3)
        arguments['max_trials'] = 3

    def synthetic_inputs(*, family, manifest, source_artifacts, deadline):
        assert family is CRYPTO_GBM and _json(manifest['protocol']) == _json(protocol)
        assert source_artifacts == {content_sha: content} and not deadline()
        return arguments

    class FixedDatetimeMeta(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)

    class FixedDatetime(datetime, metaclass=FixedDatetimeMeta):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz)

    monkeypatch.setattr(store_psycopg, 'datetime', FixedDatetime)
    monkeypatch.setattr(runner, '_historical_family', lambda _: CRYPTO_GBM)
    monkeypatch.setattr(runner, 'historical_protocol', lambda family: protocol)
    monkeypatch.setattr(runner, 'historical_run_arguments', synthetic_inputs)
    monkeypatch.setattr(cli, 'require_clean_worktree_path', lambda _: code_commit)
    assert cli.main(['optimize', 'init', '--families', 'crypto-gbm', '--actor', 'native-rule-proof',
                     '--effective-at', cutoff.isoformat(), '--managed', '--repo-root', str(repo)]) == 0
    initialized = capsysbinary.readouterr()
    assert initialized.err == b''
    assert json.loads(initialized.out)[lineage]['state'] == 'initialized'

    run = ['optimize', 'run', '--family', 'crypto-gbm', '--as-of', cutoff.isoformat(),
           '--input-manifest', str(pointer_path), '--managed', '--repo-root', str(repo)]
    exit_code = int(outcome == 'RUN_FAILED')
    assert cli.main(run) == exit_code
    published = capsysbinary.readouterr()
    assert published.err == b''
    receipt = json.loads(published.out)
    assert receipt['outcome'] == outcome and receipt['exit_code'] == exit_code and receipt['replayed'] is False
    assert receipt['registered_lineages'] == ([lineage] if scenario == 'registered' else [])
    assert receipt['counts']['completed_holdout_callbacks'] == (2 if scenario in {'registered', 'rejected'} else 0)
    assert len(calls['holdout']) == receipt['counts']['completed_holdout_callbacks']

    digest = receipt['artifacts_sha256']
    with store_psycopg.connect_managed(repo) as fresh:
        row = store_psycopg.latest_optimization_for_lineage(fresh, lineage)
        blob = snapshots.read_snapshot(fresh, digest)
        roles = fresh.execute(
            "SELECT lineage, spec_sha256, effective_at, forward_plan, code_commit, reason, actor, decision_id "
            "FROM papersim.model_role_events WHERE role = 'CANDIDATE' ORDER BY lineage"
        ).fetchall()
        events = fresh.execute(
            "SELECT phase, occurred_at, operation_key, run_event_key, config_digest, error_code, "
            "code_commit, input_evidence, output_evidence "
            "FROM papersim.command_run_events WHERE command = 'optimize-run' ORDER BY run_event_id"
        ).fetchall()
        rejections = store_psycopg._optimization_rejections(fresh, CRYPTO_GBM.family_id)
    assert blob['schema_version'] == 2
    artifact = blob['content']
    assert sha256(artifact).hexdigest() == digest == row['artifacts_sha256']
    assert len(artifact) == blob['byte_length'] == row['artifact_byte_length'] == receipt['artifact_byte_length']
    assert row['scorecard'] == receipt['scorecard']
    assert row['code_commit'] == code_commit
    assert [event[0] for event in events] == ['STARTED', 'FAILED' if exit_code else 'SUCCEEDED']
    assert all(event[1] == at for event in events)
    payload = json.loads(artifact)
    assert payload['request']['code_commit'] == code_commit
    assert payload['pure']['code_commit'] == code_commit
    assert sha256(_json(payload['pure'])).hexdigest() == payload['pure_sha256']
    assert payload['pure']['statistical_parameters'] == {
        'bootstrap_resamples': 20000, 'ci_confidence_level': 0.95, 'holm_alpha': 0.05,
        'sign_flip_reps': 2000, 'sign_flip_seed': 20261119,
    }
    old_plan = runner._forward_plan('crypto-gbm', 'touch', at)
    assert old_plan == {
        'primary_metric': 'mean_log_loss', 'start': at.isoformat(), 'first_look_min_events': 60,
        'final_look_min_events': 120, 'max_shadow_days': 182, 'rule_version': 'v1',
    }
    plan = None
    if scenario == 'registered':
        candidate, = payload['pure']['proposed_specs'].values()
        incumbent = CRYPTO_GBM.baseline_spec().spec_sha256()
        plan = {
            **old_plan, 'start': at.isoformat().replace('+00:00', 'Z'), 'rule_version': 'promo-rule-v1',
            'stage1_rule_version': 'v1', 'source_run_key': receipt['run_key'],
            'source_manifest': {'sha256': manifest_sha, 'byte_length': len(manifest)},
            'active_spec_sha256': incumbent, 'candidate_spec_sha256': candidate,
            'min_complete_blocks': None, 'min_span_days': 28,
            'first_look_ci_percentiles': [0.5, 99.5], 'final_look_ci_percentiles': [2.5, 97.5],
            'paired_mean_max': 0.0, 'ci_upper_max': 0.005,
            'close_deviation_min_gap': -0.01, 'close_deviation_min_markets': 30,
        }
        assert receipt['dispositions'][lineage]['forward_plan'] == payload['dispositions'][lineage]['forward_plan'] == plan
        assert roles == [(lineage, candidate, at, plan, code_commit,
                          f"stage-one eligibility for {receipt['run_key']}", 'auto:v1', None)]
    else:
        assert roles == []
    if scenario == 'rejected':
        rejection = rejections[0]
        assert len(rejections) == 1
        assert rejection['lineage'] == lineage and rejection['artifact_sha256'] == digest
        assert payload['rejections'][lineage]['reasons'] == ['holdout_ci_upper_nonnegative', 'calibration_regression']
        assert payload['pure']['decisions'][0]['status'] == 'statistically_rejected'
    else:
        assert not rejections and not payload['rejections']
    if scenario == 'budget':
        assert receipt['budget']['stopping_causes'] == ['trial_cap']
        assert receipt['counts']['evaluated_trials'] == 3

    calls_before_replay = {name: list(history) for name, history in calls.items()}
    assert cli.main(run) == exit_code
    replay = capsysbinary.readouterr()
    assert replay.err == b''
    assert json.loads(replay.out) == {**receipt, 'replayed': True}
    assert calls == calls_before_replay

    evidence = {
        'source': sources, 'source_sha256': content_sha, 'manifest': pointer, 'code_commit': code_commit,
        'run_key': receipt['run_key'], 'outcome': outcome, 'exit_code': exit_code, 'artifact_sha256': digest,
        'artifact_length': len(artifact), 'pure_sha256': payload['pure_sha256'], 'legacy_forward_plan': old_plan,
        'managed_forward_plan': plan, 'readback_counts': row['trial_count'], 'registered_roles': len(roles),
        'verified_rejections': len(rejections),
    }
    record_property('registered_rule_evidence', _json(evidence).decode())
    if receipt_dir:
        directory = Path(receipt_dir) / scenario
        directory.mkdir(parents=True)
        for name, data in (
            ('evidence.json', _json(evidence)), ('init.stdout', initialized.out),
            ('run.stdout', published.out), ('run.stderr', published.err), ('replay.stdout', replay.out),
            ('artifact.json', artifact), ('source.json', content), ('manifest.json', manifest),
            ('readback.json', json.dumps(row, sort_keys=True, default=str).encode()),
            ('roles.json', json.dumps(roles, sort_keys=True, default=str).encode()),
            ('command-events.json', json.dumps(events, sort_keys=True, default=str).encode()),
        ):
            (directory / name).write_bytes(data)


if __name__ == '__main__':
    sys.exit(_main())
