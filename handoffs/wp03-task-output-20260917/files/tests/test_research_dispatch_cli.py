"""Operator entrypoint tests: synthetic typed receipts, no DB, HTTP or provider."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys

import pytest

from polymarket_alpha_lab import research_dispatch_cli as cli
from polymarket_alpha_lab.research_dispatch import ResearchBatch, StoredResearchBatch, ResearchBatchSnapshot
from polymarket_alpha_lab.research_dispatch_rotation import ResearchRotationTurn, StoredResearchRotationTurn
from polymarket_alpha_lab.research_dispatch_rotation_runner import ResearchRotationReport
from polymarket_alpha_lab.research_dispatch_runner import DispatchAttempt, ResearchDispatchStop
from polymarket_alpha_lab.research_execution import CapturedResearchRequest, CapturedResearchExecution
from polymarket_alpha_lab.research_model_budget import ModelCallBudget, StoredModelBudget, ModelBudgetSnapshot
from polymarket_alpha_lab.team_research_agent_types import ResearchEvidence, TeamResearchTask, TeamResearchResult
from polymarket_alpha_lab.team_research_intake import TeamResearchIntake, _receipt
from polymarket_alpha_lab.team_research_market_pipeline import MarketTeamResearchRun
from polymarket_alpha_lab.team_research_evaluation import ResearchEvaluationRecord

NOW = datetime(2026, 9, 15, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]
PRIVATE = 'synthetic-private-text-not-for-output'
RUN = ['run-turn', '--rotation-id', 'rotation', '--turn-id', 'turn', '--batch-id', 'batch',
       '--budget-id', 'budget', '--max-tasks', '1', '--max-workers', '1', '--allow-model-calls']
READS = [('inspect-batch', ['--batch-id', 'batch']),
         ('inspect-turn', ['--rotation-id', 'rotation', '--turn-id', 'turn']),
         ('inspect-budget', ['--budget-id', 'budget'])]


def request():
    evidence = ResearchEvidence('source', 'crypto_btc', 'condition', 'Synthetic', PRIVATE,
                                'fixture:source', NOW)
    task = TeamResearchTask('task', 'crypto_btc', 'condition', 'synthetic-market',
                            'Synthetic question?', PRIVATE, NOW, (evidence,))
    intake = TeamResearchIntake('task', 'crypto_btc', 'condition', 'synthetic-market',
        NOW, NOW, 'a'*64, 'prepared', 'research_intake_prepared', task, (_receipt(evidence),))
    return CapturedResearchRequest('record', 'synthetic-model', 'synthetic-protocol',
                                   NOW+timedelta(minutes=5), intake)


def execution(status='captured', research_status='completed'):
    req = request()
    if status == 'incomplete':
        return CapturedResearchExecution(req, NOW, status)
    fields = dict(task_id='task', team_id='crypto_btc', condition_id='condition',
                  market_slug='synthetic-market', as_of=NOW, status=research_status,
                  reason_code='research_completed' if research_status == 'completed' else 'model_failed')
    if research_status == 'completed':
        fields.update(probability_yes=Decimal('.5'), confidence=Decimal('.5'),
                      source_ids=('source',), summary=PRIVATE)
    run = MarketTeamResearchRun(req.intake, TeamResearchResult(**fields))
    if status == 'capture_failed':
        return CapturedResearchExecution(req, NOW, status, pending_run=run)
    record = ResearchEvaluationRecord(req.record_id, req.model_id, req.protocol_version, NOW, run)
    return CapturedResearchExecution(req, NOW, status, record)


def stored_turn(**changes):
    req = request()
    values = dict(rotation_id='rotation', turn_id='turn', turn_number=1,
        roster=(('batch', 'b'*64, 1),), observed_at=(NOW,), states=('pending',),
        request_keys=((req.record_id, req.content_sha256),), start_slot=0, max_tasks=1, max_workers=1)
    values.update(changes)
    return StoredResearchRotationTurn(ResearchRotationTurn(**values), NOW)


def snapshot():
    req = request()
    policy = ModelCallBudget('budget', 'synthetic-provider', req.model_id, 'USD', 100, 10,
        10, 10000, 1024, NOW+timedelta(hours=1), ((req.record_id, req.content_sha256),),
        'c'*64, cost_bound_attested=True)
    return ModelBudgetSnapshot(StoredModelBudget(policy, NOW), NOW, 0, 0)


def read_value(operation):
    if operation == 'inspect-turn': return stored_turn()
    if operation == 'inspect-budget': return snapshot()
    return ResearchBatchSnapshot(StoredResearchBatch(ResearchBatch('batch', (request(),)), NOW), NOW, (None,))


@pytest.fixture
def managed(monkeypatch):
    state = dict(calls=[], value=None, error=None, cleanup_error=None)
    class Session:
        def __getattr__(self, name):
            def call(**kw):
                state['calls'].append((name, kw))
                if state['error'] is not None: raise state['error']
                return state['value']
            return call
    class Database:
        def __init__(self, root): state['root'] = root
        @contextmanager
        def session(self):
            state['entered'] = True
            try: yield Session()
            finally:
                state['closed'] = True
                if state['cleanup_error'] is not None: raise state['cleanup_error']
    monkeypatch.setattr(cli, 'ProjectPostgres', Database)
    return state


def invoke(capsys, argv, **kwargs):
    code = cli.main(argv, default_root=ROOT, **kwargs)
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err
    return code, json.loads(captured.out)


@pytest.mark.parametrize('operation,arguments', READS)
def test_read_existing_receipts_and_cleanup(managed, capsys, operation, arguments):
    managed['value'] = read_value(operation)
    code, out = invoke(capsys, ['--root', str(ROOT/'Other Root'), operation, *arguments])
    assert code == 0 and out['status'] == 'inspected' and managed['closed']
    assert managed['root'] == ROOT/'Other Root'
    assert out['result'] == managed['value'].to_dict()
    assert out['model_calls_possible'] is out['business_writes_possible'] is False
    assert len(managed['calls']) == 1


@pytest.mark.parametrize('operation,arguments', READS)
def test_missing_is_not_empty_project(managed, capsys, operation, arguments):
    code, out = invoke(capsys, [operation, *arguments])
    assert code == 3 and out['status'] == 'not_found' and out['result'] is None
    assert managed['closed']


@pytest.mark.parametrize('operation,arguments', READS)
@pytest.mark.parametrize('fault', ['wrong-type', 'wrong-id', 'mutated', 'query', 'cleanup'])
def test_invalid_or_failed_reads_do_not_publish_a_receipt(managed, capsys, operation, arguments, fault):
    value = read_value(operation)
    if fault == 'wrong-type': value = {'payload': PRIVATE}
    if fault in ('wrong-id', 'mutated'):
        if operation == 'inspect-batch': target, field = value.stored.batch, 'batch_id'
        elif operation == 'inspect-turn': target, field = value.turn, 'rotation_id'
        else: target, field = value.stored.policy, 'budget_id'
        object.__setattr__(target, field, 'foreign' if fault == 'wrong-id' else 'bad id')
    if fault == 'query': managed['error'] = RuntimeError(PRIVATE)
    if fault == 'cleanup': managed['cleanup_error'] = RuntimeError(PRIVATE)
    managed['value'] = value
    code, out = invoke(capsys, [operation, *arguments])
    assert code == 1 and out['status'] == 'failed' and out['result'] is None
    assert managed['closed']


@pytest.mark.parametrize('argv', [[], ['--unknown', PRIVATE],
    ['inspect-batch', '--batch-id', 'bad id'], ['inspect-batch', '--batch-i', 'batch'],
    ['inspect-budget'], ['--roo', 'x', 'inspect-budget', '--budget-id', 'budget'],
    RUN+['--max-tasks', '0'], RUN+['--max-tasks', '101'], RUN+['--max-tasks', PRIVATE],
    RUN+['--max-workers', '0'], RUN+['--max-workers', '9'], RUN+['--batch-id', 'batch'],
    RUN+sum((['--batch-id', 'b'+str(i)] for i in range(10)), []),
    [x for x in RUN if x not in ('--budget-id', 'budget')], RUN+['--factory', PRIVATE]])
def test_invalid_arguments_stop_before_project_access(managed, capsys, argv):
    with pytest.raises(SystemExit) as error: cli.main(argv, default_root=ROOT)
    assert error.value.code == 2 and 'entered' not in managed and 'root' not in managed
    text = capsys.readouterr()
    assert PRIVATE not in text.out+text.err


@pytest.mark.parametrize('opt_in,factory', [(False, None), (False, lambda _: None),
                                          (True, None), (True, 'module:client')])
def test_no_implicit_model_or_project_access(managed, capsys, opt_in, factory):
    argv = RUN if opt_in else RUN[:-1]
    code, out = invoke(capsys, argv, model_factory=factory)
    assert code == 2 and out['status'] == 'blocked' and out['operation_entered'] is False
    assert 'root' not in managed and not managed['calls']


@pytest.mark.parametrize('status,research_status,code', [
    ('captured', 'completed', 0), ('already_captured', 'completed', 0),
    ('captured', 'failed', 1), ('captured', 'blocked', 1),
    ('incomplete', 'completed', 1), ('capture_failed', 'completed', 1)])
def test_budgeted_run_preserves_failed_or_missing_results(managed, capsys, status, research_status, code):
    managed['value'] = ResearchRotationReport('dispatched', stored_turn(),
        (DispatchAttempt(0, 'returned', execution(status, research_status)),))
    token = ResearchDispatchStop()
    factory = lambda _: pytest.fail('the CLI must not itself construct a client')
    actual, out = invoke(capsys, RUN, model_factory=factory, stop=token)
    assert actual == code and managed['closed']
    name, kw = managed['calls'][0]
    assert name == 'run_research_rotation' and kw['model_budget_id'] == 'budget'
    assert kw['batch_ids_to_run'] == ('batch',) and kw['model_factory'] is factory
    assert kw['stop'] is token and kw['allow_model_calls'] is True
    assert out['result']['attempts'][0]['execution']['status'] == status
    assert out['result']['final_database_state_checked'] is False
    assert out['provider_charge_bound_verified'] is False


@pytest.mark.parametrize('value,code', [
    (ResearchRotationReport('turn_already_reserved', stored_turn()), 0),
    (ResearchRotationReport('dispatched', stored_turn(states=('expired',))), 0),
    (ResearchRotationReport('stopped_before_reservation', stop_requested=True), 130),
    (ResearchRotationReport('dispatched', stored_turn(), (), True), 130),
    (ResearchRotationReport('dispatched', stored_turn(), (DispatchAttempt(0, 'operation_failed'),)), 1)])
def test_replay_stop_empty_and_uncertain_failure_remain_distinct(managed, capsys, value, code):
    managed['value'] = value
    actual, out = invoke(capsys, RUN, model_factory=lambda _: None)
    assert actual == code and out['result'] == value.to_dict()
    assert len(managed['calls']) == 1


@pytest.mark.parametrize('change', [dict(rotation_id='foreign'), dict(turn_id='foreign'),
    dict(roster=(('foreign', 'b'*64, 1),)), dict(max_tasks=2), dict(max_workers=2)])
def test_foreign_run_receipt_fails_closed(managed, capsys, change):
    managed['value'] = ResearchRotationReport('turn_already_reserved', stored_turn(**change))
    code, out = invoke(capsys, RUN, model_factory=lambda _: None)
    assert code == 1 and out['result'] is None


@pytest.mark.parametrize('error', [RuntimeError(PRIVATE), KeyboardInterrupt(PRIVATE)])
@pytest.mark.parametrize('where', ['error', 'cleanup_error'])
def test_run_errors_are_uncertain_not_proof_of_no_writes(managed, capsys, error, where):
    managed['value'] = ResearchRotationReport('turn_already_reserved', stored_turn())
    managed[where] = error
    control = ResearchDispatchStop()
    code, out = invoke(capsys, RUN, model_factory=lambda _: None, stop=control)
    assert code == (130 if isinstance(error, KeyboardInterrupt) else 1)
    assert out['result'] is None and out['business_writes_possible'] is True
    assert out['model_calls_possible'] is True and managed['closed']
    assert control.is_stopped() == isinstance(error, KeyboardInterrupt)
    assert len(managed['calls']) == 1


def test_invalid_stop_precedes_session(managed, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(RUN, default_root=ROOT, model_factory=lambda _: None, stop=object())
    assert error.value.code == 2 and 'root' not in managed


def test_batch_incomplete_is_exposed_not_reclaimed(managed, capsys):
    value = read_value('inspect-batch')
    managed['value'] = replace(value, executions=(execution('incomplete'),))
    code, out = invoke(capsys, ['inspect-batch', '--batch-id', 'batch'])
    assert code == 0 and out['result']['state_counts']['incomplete'] == 1
    assert out['result']['items'][0]['worker_liveness'] == 'unknown'
    assert len(managed['calls']) == 1


def test_script_uses_its_own_source_and_refuses_unconfigured_run(tmp_path):
    script = ROOT/'scripts/manage_research_tasks.py'
    assert script.read_bytes().isascii()
    result = subprocess.run([sys.executable, '-I', str(script), *RUN], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout)['reason_code'] == 'research_dispatch_approved_client_required'
    assert not (tmp_path/'.local').exists()


def test_new_script_is_packaged_without_changing_old_kit_requirements():
    from polymarket_alpha_lab.project_postgres.distribution import selected_source, FIXED
    assert selected_source('scripts/manage_research_tasks.py')
    assert selected_source('src/polymarket_alpha_lab/research_dispatch_cli.py')
    assert 'scripts/manage_research_tasks.py' not in FIXED


@pytest.mark.parametrize('mode', ['budget', 'run', 'blocked'])
@pytest.mark.parametrize('fault', ['short', 'flush', 'exit-zero', 'interrupt', 'write-error'])
def test_task_output_failure_is_nonzero_and_does_not_repeat_work(managed, monkeypatch, mode, fault):
    """Even after a returned budgeted turn, an incomplete receipt is not success."""
    managed['value'] = (read_value('inspect-budget') if mode == 'budget' else
                        ResearchRotationReport('turn_already_reserved', stored_turn()))
    writes, flushes = [], []

    class Output:
        def write(self, text):
            if mode == 'blocked':
                assert 'entered' not in managed
            else:
                assert managed['closed']
            writes.append(text)
            if fault == 'exit-zero':
                raise SystemExit(0)
            if fault == 'interrupt':
                raise KeyboardInterrupt(PRIVATE)
            if fault == 'write-error':
                raise OSError(PRIVATE)
            return len(text) - 1 if fault == 'short' else len(text)

        def flush(self):
            flushes.append(1)
            if fault == 'flush':
                raise OSError(PRIVATE)

    argv = ['inspect-budget', '--budget-id', 'budget'] if mode == 'budget' else RUN
    options = {'model_factory': lambda _: None} if mode == 'run' else {}
    with monkeypatch.context() as patch:
        patch.setattr(sys, 'stdout', Output())
        try:
            code = cli.main(argv, default_root=ROOT, **options)
        except (Exception, SystemExit, KeyboardInterrupt) as error:
            code = ('escaped', type(error).__name__)
    assert code == (130 if fault == 'interrupt' else 1)
    assert len(writes) == 1
    assert len(flushes) == (1 if fault == 'flush' else 0)
    assert len(managed['calls']) == (0 if mode == 'blocked' else 1)
    assert all(PRIVATE not in text for text in writes)


@pytest.mark.parametrize('mode,expected_code', [('budget', 0), ('missing', 3), ('run', 0), ('blocked', 2)])
def test_task_receipt_is_written_once_then_flushed_after_cleanup(managed, monkeypatch, mode, expected_code):
    managed['value'] = (None if mode == 'missing' else read_value('inspect-budget') if mode == 'budget' else
                        ResearchRotationReport('turn_already_reserved', stored_turn()))
    actions = []

    class Output:
        def write(self, text):
            assert ('entered' not in managed) if mode == 'blocked' else managed['closed']
            actions.append(('write', text))
            return len(text)

        def flush(self):
            actions.append(('flush', None))

    argv = ['inspect-budget', '--budget-id', 'budget'] if mode in ('budget', 'missing') else RUN
    options = {'model_factory': lambda _: None} if mode == 'run' else {}
    with monkeypatch.context() as patch:
        patch.setattr(sys, 'stdout', Output())
        code = cli.main(argv, default_root=ROOT, **options)
    assert code == expected_code
    assert [name for name, _ in actions] == ['write', 'flush']
    body = json.loads(actions[0][1])
    assert actions[0][1] == json.dumps(body, ensure_ascii=True, allow_nan=False, indent=2) + '\n'
    assert body['automatic_retry_permitted'] is False
    assert body['business_writes_possible'] is (mode == 'run')
    assert len(managed['calls']) == (0 if mode == 'blocked' else 1)
