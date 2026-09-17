"""Console projection and strict managed read path, with no actual DB or HTTP."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys

import pytest

from polymarket_alpha_lab import research_evaluation_cli as cli
from tests.test_team_research_evaluation import Record, Report, LATER, NOW, record, outcome, unsuccessful


def test_empty_history_is_not_a_zero_error_perfect_model():
    value = cli.evaluation_summary(Report((), (), LATER))
    assert value['evaluation_status'] == 'no_visible_attempts'
    assert value['visible_attempt_count'] == value['selected_attempt_count'] == value['scored_decision_count'] == 0
    assert value['groups'] == [] and 'decisions' not in value
    assert not value['pooled_score_computed'] and not value['forecast_approval_performed']
    assert set(value['decision_counts']) == set(cli.REASONS)


@pytest.mark.parametrize('state', ['pending', 'failed', 'blocked', 'intake_blocked', 'late'])
def test_no_scores_remains_different_from_empty(state):
    item = record()
    if state in ('failed', 'blocked', 'intake_blocked'):
        item = unsuccessful(item, state)
    if state == 'late':
        item = replace(item, recorded_at=outcome().forecast_cutoff_at)
    value = cli.evaluation_summary(Report((item,), () if state == 'pending' else (outcome(),), LATER))
    assert value['evaluation_status'] == 'no_scored_forecasts'
    assert value['visible_attempt_count'] == 1
    assert value['groups'][0]['scores']['mean_brier_score'] is None
    assert value['groups'][0]['scores']['sample_status'] == 'empty'


@pytest.mark.parametrize('threshold', [1, 2, 30, 10000])
def test_available_diagnostics_preserve_sample_warning_and_exact_scores(threshold):
    source = Report((record(),), (outcome(),), LATER, min_sample_count=threshold)
    output = cli.evaluation_summary(source)
    assert output['evaluation_status'] == 'diagnostics_available'
    assert output['groups'] == source.to_dict()['groups']
    scores = output['groups'][0]['scores']
    assert scores['mean_brier_score'] == '0.090000000000'
    assert scores['sample_status'] == ('descriptive_only' if threshold == 1 else 'insufficient_sample')
    assert not output['forecast_approval_performed']


def test_multiple_models_same_event_not_reported_as_independent_events():
    data = (record('a'), record('b', model_id='other'), record('c', team='crypto_btc'))
    output = cli.evaluation_summary(Report(data, (outcome(),), LATER))
    assert output['scored_decision_count'] == 3 and output['scored_condition_count'] == 1
    assert output['group_count'] == 3 and not output['pooled_score_computed']
    assert 'mean_brier_score' not in output


@pytest.mark.parametrize('state', ['failed', 'blocked', 'intake_blocked'])
def test_successful_retry_cannot_hide_failed_first_attempt(state):
    a = unsuccessful(record('a'), state)
    b = record('b', p='0.99', recorded_at=NOW + timedelta(seconds=2))
    output = cli.evaluation_summary(Report((b, a), (outcome(),), LATER), include_decisions=True)
    assert output['visible_attempt_count'] == 2 and output['selected_attempt_count'] == 1
    assert output['scored_decision_count'] == 0 and output['decision_counts']['later_attempt'] == 1
    assert output['groups'][0]['failed_or_blocked_count'] == 1
    assert output['decisions'][1]['reason_code'] == 'later_attempt'


def test_future_and_orphan_counts_not_silently_reclassified():
    future = replace(record(), recorded_at=LATER + timedelta(seconds=1))
    result = cli.evaluation_summary(Report((future,), (outcome(),), LATER))
    assert result['evaluation_status'] == 'no_visible_attempts' and result['record_count'] == 1
    assert result['decision_counts']['not_yet_recorded'] == 1
    assert result['orphan_outcome_count'] == 1 and result['outcome_count'] == 1
    assert cli.evaluation_summary(Report((), (outcome(),), LATER))['orphan_outcome_count'] == 1


@pytest.mark.parametrize('detailed', [False, True])
def test_no_raw_source_summary_or_observation_catalog_is_exported(detailed):
    report = Report((record(),), (outcome(),), LATER)
    output = cli.evaluation_summary(report, include_decisions=detailed)
    text = json.dumps(output, allow_nan=False)
    for banned in ('PRIVATE-EVIDENCE-SENTINEL', 'PRIVATE-SUMMARY-SENTINEL', 'synthetic:source', 'raw_json', 'observations'):
        assert banned not in text
    assert ('decisions' in output) is detailed
    assert output['decisions_included'] is detailed
    if detailed:
        assert output['decisions'] == report.to_dict()['decisions']


def test_wrong_certainty_preserves_infinite_marker_not_finite_or_nan():
    output = cli.evaluation_summary(Report((record(p='0'),), (outcome(),), LATER))
    scores = output['groups'][0]['scores']
    assert scores['infinite_log_loss_count'] == 1
    assert scores['mean_log_loss'] is None and scores['log_loss_status'] == 'infinite'
    json.dumps(output, allow_nan=False)


def test_derived_output_is_recomputed_not_trusted_and_input_stays_unchanged():
    report = Report((record(),), (outcome(),), LATER)
    object.__setattr__(report.groups[0].scores, 'mean_brier_score', Decimal('0'))
    output = cli.evaluation_summary(report)
    assert output['groups'][0]['scores']['mean_brier_score'] == '0.090000000000'
    output['groups'][0]['scores']['mean_brier_score'] = 'fake'
    assert cli.evaluation_summary(report)['groups'][0]['scores']['mean_brier_score'] == '0.090000000000'
    assert report.groups[0].scores.mean_brier_score == 0  # Formatter did not mutate input.


@pytest.mark.parametrize('bad', [None, {}, (), object()])
def test_wrong_report_type_rejected(bad):
    with pytest.raises(ValueError): cli.evaluation_summary(bad)


@pytest.mark.parametrize('bad', [None, 1, 'true'])
def test_exact_details_flag(bad):
    with pytest.raises(ValueError): cli.evaluation_summary(Report((), (), LATER), include_decisions=bad)


def test_mutated_input_flags_are_not_hidden_by_formatter():
    report = Report((record(),), (), LATER)
    object.__setattr__(report.records[0].run.research, 'readonly', False)
    with pytest.raises(ValueError): cli.evaluation_summary(report)


@pytest.fixture
def managed(monkeypatch):
    events = []
    state = {'report': Report((), (), LATER), 'error': None, 'exit_error': None}
    class Session:
        def evaluate(self, **kwargs):
            events.append(('evaluate', kwargs))
            if state['error'] is not None: raise state['error']
            return state['report']
    class DB:
        def __init__(self, root): events.append(('root', root))
        @contextmanager
        def session(self):
            events.append(('enter',))
            try: yield Session()
            finally:
                events.append(('exit',))
                if state['exit_error'] is not None: raise state['exit_error']
    monkeypatch.setattr(cli, 'ProjectPostgres', DB)
    return state, events


def invoke(args=()):
    return cli.main(list(args), default_root=Path('/unused/default'))


def test_cli_uses_exact_strict_session_once_and_closes_before_output(managed, capsys):
    state, events = managed
    assert invoke() == 0
    output = json.loads(capsys.readouterr().out)
    assert [row[0] for row in events] == ['root', 'enter', 'evaluate', 'exit']
    assert events[2][1] == dict(generated_at=None, max_records=10000, bucket_count=10, min_sample_count=30, min_bin_count=5)
    assert output['history_gate'] == 'complete_visible_execution_claims'
    assert output['evaluation']['evaluation_status'] == 'no_visible_attempts'
    assert all(output[name] is False for name in ('public_network_called', 'live_model_called', 'business_writes_performed'))


def test_explicit_historical_configuration_forwarded_unchanged(managed, capsys):
    assert invoke(['--root', '/unused/other', '--as-of', '2026-09-12T08:00:00+08:00',
        '--max-records', '12', '--buckets', '5', '--min-sample-count', '50', '--min-bin-count', '8', '--include-decisions']) == 0
    _, events = managed
    assert events[0] == ('root', Path('/unused/other'))
    assert events[2][1] == dict(generated_at=NOW, max_records=12, bucket_count=5, min_sample_count=50, min_bin_count=8)
    assert json.loads(capsys.readouterr().out)['evaluation']['decisions'] == []


@pytest.mark.parametrize('reason', sorted(cli._BLOCKS))
def test_known_block_has_no_diagnostics_no_legacy_fallback(managed, capsys, reason):
    state, events = managed
    state['error'] = cli.ResearchCaptureConflict(reason)
    assert invoke() == 1
    output = json.loads(capsys.readouterr().out)
    assert output['status'] == 'blocked' and output['reason_code'] == reason
    assert output['history_gate'] == 'not_established' and output['evaluation'] is None
    assert sum(row[0] == 'evaluate' for row in events) == 1


@pytest.mark.parametrize('kind', ['runtime', 'unknown_conflict', 'scope', 'exit', 'invalid_report'])
def test_failure_is_fixed_redacted_and_never_prints_success(managed, capsys, kind):
    state, _ = managed
    error = RuntimeError('PRIVATE-DSN-FIXTURE')
    if kind == 'exit': state['exit_error'] = error
    elif kind == 'invalid_report': state['report'] = {'secret': 'PRIVATE-DSN-FIXTURE'}
    else: state['error'] = {'runtime': error, 'unknown_conflict': cli.ResearchCaptureConflict('PRIVATE-DSN-FIXTURE'),
                           'scope': ValueError('PRIVATE-DSN-FIXTURE')}[kind]
    assert invoke() == 1
    captured = capsys.readouterr()
    assert captured.err == '' and 'PRIVATE-DSN-FIXTURE' not in captured.out
    value = json.loads(captured.out)
    assert value['status'] == 'failed' and value['evaluation'] is None
    assert value['reason_code'] == 'research_evaluation_operation_failed'


@pytest.mark.parametrize('args', [
    ['--as-of', '2026-09-12'], ['--as-of', 'not-a-time'], ['--as-of', '9999-12-31T23:59:59-23:59'],
    ['--max-records', '0'], ['--max-records', '10001'], ['--max-records', 'oops'],
    ['--buckets', '3'], ['--min-sample-count', '0'], ['--min-bin-count', '10001'],
    ['--dsn', 'not-supported'], ['--allow-public-fetch'], ['--collect'], ['--skip-incomplete']])
def test_bad_configuration_never_constructs_manager(managed, args):
    _, events = managed
    with pytest.raises(SystemExit) as e: invoke(args)
    assert e.value.code == 2 and events == []


def test_keyboard_interrupt_not_converted_to_a_database_failure(managed):
    managed[0]['error'] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): invoke()
    assert managed[1][-1] == ('exit',)


def test_actual_script_help_has_no_database_initialization(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'scripts/evaluate_project_research.py'
    result = subprocess.run([sys.executable, str(path), '--help'], cwd=tmp_path,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and '--as-of' in result.stdout
    assert '--include-decisions' in result.stdout and not (tmp_path / '.local').exists()


# Read-only recovery views share the already-reviewed checked JSON emitter.
@pytest.fixture(params=['execution', 'execution-missing', 'inventory', 'resolution',
                        'resolution-missing', 'probability', 'settled'])
def readonly_view(request, monkeypatch):
    from types import SimpleNamespace
    from polymarket_alpha_lab import research_execution_cli, research_inventory_cli
    from polymarket_alpha_lab import research_resolution_inspection_cli
    from tests.test_research_execution_cli import persisted
    from tests.test_research_resolution_inspection_cli import stored
    from tests.test_research_execution_inventory import AT, saved
    from tests.test_research_settled_cli import exported
    name = request.param
    if name.startswith('execution'):
        module, args, method = research_execution_cli, ['--record-id', 'r1'], 'inspect'
        value = None if name.endswith('missing') else persisted()
    elif name.startswith('resolution'):
        module, args, method = research_resolution_inspection_cli, ['--review-id', 'review-1'], 'inspect_resolution'
        value = None if name.endswith('missing') else stored()
    elif name == 'inventory':
        module, args, method = research_inventory_cli, [], 'execution_inventory'
        value = research_inventory_cli.ResearchExecutionInventory(AT, (saved(),))
    else:
        module, args = cli, ['--settled-paper'] if name == 'settled' else []
        method = 'evaluate_settled_paper_research' if name == 'settled' else 'evaluate'
        value = exported() if name == 'settled' else Report((), (), LATER)
    view = SimpleNamespace(name=name, module=module, args=args, value=value,
        events=[], fault=None, phase=None, expected_code=3 if name.endswith('missing') else 0)
    def hit(phase):
        view.events.append(phase)
        if view.phase == phase:
            raise view.fault
    def read(**kw):
        hit('read')
        return view.value
    class Database:
        def __init__(self, root): hit('manager')
        @contextmanager
        def session(self):
            hit('enter')
            try: yield SimpleNamespace(**{method: read})
            finally: hit('close')
    monkeypatch.setattr(module, 'ProjectPostgres', Database)
    view.invoke = lambda: module.main(args, default_root=Path('/synthetic/readonly'))
    return view


@pytest.mark.parametrize('phase', ['manager', 'enter', 'read', 'close'])
def test_readonly_internal_zero_exit_is_never_success(readonly_view, capsys, phase):
    view = readonly_view
    view.phase, view.fault = phase, SystemExit(0)
    try:
        code = view.invoke()
    except SystemExit as escaped:
        code = ('escaped', escaped.code)
    assert code == 1
    out = capsys.readouterr()
    assert out.err == ''
    envelope = json.loads(out.out)
    assert envelope['status'] == 'failed'
    assert envelope.get('inspection', envelope.get('inventory', envelope.get('evaluation'))) is None
    assert envelope['business_writes_performed'] is False
    assert view.events.count('read') == int(phase in ('read', 'close'))


@pytest.mark.parametrize('fault', ['none', 'short', 'bool', 'write-exit', 'flush-error', 'flush-interrupt'])
def test_readonly_output_requires_one_complete_write_and_flush(readonly_view, monkeypatch, fault):
    view = readonly_view
    writes, flushes = [], []
    class Output:
        def write(self, text):
            assert view.events == ['manager', 'enter', 'read', 'close']
            writes.append(text)
            if fault == 'write-exit': raise SystemExit(0)
            if fault == 'short': return len(text) - 1
            if fault == 'bool': return True
            return len(text)
        def flush(self):
            flushes.append(1)
            if fault == 'flush-error': raise OSError('PRIVATE-output-details')
            if fault == 'flush-interrupt': raise KeyboardInterrupt('PRIVATE-output-details')
    with monkeypatch.context() as patch:
        patch.setattr(sys, 'stdout', Output())
        try:
            code = view.invoke()
        except (Exception, SystemExit, KeyboardInterrupt) as escaped:
            code = ('escaped', type(escaped).__name__)
    expected = view.expected_code if fault == 'none' else 130 if fault == 'flush-interrupt' else 1
    assert code == expected
    assert len(writes) == 1 and writes[0].endswith('\n')
    assert len(flushes) == int(fault in ('none', 'flush-error', 'flush-interrupt'))
    assert 'PRIVATE' not in writes[0]
    assert view.events == ['manager', 'enter', 'read', 'close']


# Separate same-assistant review: error formatting and output must fail closed.
@pytest.mark.parametrize('args', [(), ('--settled-paper',)])
def test_readonly_review_conflict_does_not_execute_untrusted_str(monkeypatch, capsys, args):
    class Conflict(cli.ResearchCaptureConflict):
        def __str__(self): raise SystemExit(0)
    class DB:
        def __init__(self, root): pass
        @contextmanager
        def session(self):
            raise Conflict('PRIVATE-conflict')
            yield
    monkeypatch.setattr(cli, 'ProjectPostgres', DB)
    try:
        code = cli.main(list(args), default_root=Path('/synthetic'))
    except SystemExit as error:
        code = ('escaped', error.code)
    assert code == 1
    output = capsys.readouterr()
    assert output.err == '' and 'PRIVATE' not in output.out
    assert json.loads(output.out)['status'] == 'failed'


@pytest.mark.parametrize('error', [ValueError('PRIVATE-render'), SystemExit(0), KeyboardInterrupt()])
def test_readonly_review_serialization_failure_writes_nothing(readonly_view, monkeypatch, error):
    view = readonly_view
    original = json.dumps
    writes = []
    def render(value, **kwargs):
        if type(value) is dict and ('lookup_scope' in value or 'history_gate' in value):
            assert view.events[-1] == 'close'
            raise error
        return original(value, **kwargs)
    class Output:
        def write(self, text): writes.append(text); return len(text)
        def flush(self): pytest.fail('flush without serialized result')
    with monkeypatch.context() as patch:
        patch.setattr(json, 'dumps', render)
        patch.setattr(sys, 'stdout', Output())
        try:
            code = view.invoke()
        except (Exception, SystemExit, KeyboardInterrupt) as escaped:
            code = ('escaped', type(escaped).__name__)
    assert code == (130 if isinstance(error, KeyboardInterrupt) else 1)
    assert writes == [] and view.events == ['manager', 'enter', 'read', 'close']


@pytest.mark.parametrize('first', ['research_resolution_inspection_cli', 'research_resolution_confirmation_cli',
                                  'research_execution_cli', 'research_inventory_cli', 'research_evaluation_cli'])
def test_readonly_review_fresh_import_order_and_help(first, tmp_path):
    root = Path(__file__).resolve().parents[1]
    program = '''
import importlib, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / 'src'))
first = importlib.import_module('polymarket_alpha_lab.' + sys.argv[2])
confirmation = importlib.import_module('polymarket_alpha_lab.research_resolution_confirmation_cli')
inspection = importlib.import_module('polymarket_alpha_lab.research_resolution_inspection_cli')
assert callable(confirmation._emit) and callable(inspection.resolution_review_summary)
assert Path(first.__file__).resolve().is_relative_to(root)
try:
    inspection.main(['--help'], default_root=root)
except SystemExit as error:
    assert error.code == 0
else:
    raise AssertionError('help did not exit')
'''
    run = subprocess.run([sys.executable, '-I', '-c', program, str(root), first],
        cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0 and run.stderr == '' and '--review-id' in run.stdout
    assert not (tmp_path / '.local').exists()
