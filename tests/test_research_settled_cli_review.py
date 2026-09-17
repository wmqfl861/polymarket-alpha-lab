"""Separate same-assistant adversarial console pass, not a third-party audit."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path

import pytest

from polymarket_alpha_lab import research_evaluation_cli as cli
from polymarket_alpha_lab.team_research_evaluation import ResearchEvaluationReport
from tests.test_research_settled_cli import managed, invoke, exported
from tests.test_research_paper_settlement import case, assemble, PRIVATE


def test_different_valid_historical_window_cannot_satisfy_requested_cutoff(managed, capsys):
    at = case()[0].generated_at - timedelta(seconds=1)
    code, out = invoke(capsys, '--as-of', at.isoformat())
    assert code == 1 and out['evaluation'] is None


def test_valid_report_over_requested_record_limit_is_not_accepted(managed, capsys):
    a, b = case(), case(2)
    h = ResearchEvaluationReport((a[0].records[0], b[0].records[0]), (), a[0].generated_at)
    managed['report'] = assemble(h, {}, {})
    code, out = invoke(capsys, '--max-records', '1')
    assert code == 1 and out['evaluation'] is None


@pytest.mark.parametrize('option,value', [('--buckets', '5'), ('--min-sample-count', '31'), ('--min-bin-count', '6')])
def test_valid_report_with_different_diagnostic_options_is_not_accepted(managed, capsys, option, value):
    code, out = invoke(capsys, option, value)
    assert code == 1 and out['evaluation'] is None


def test_contradictory_nested_history_flags_reject(managed, capsys):
    managed['report']['history']['readonly'] = False
    code, out = invoke(capsys)
    assert code == 1 and out['evaluation'] is None


@pytest.mark.parametrize('position', ['error', 'cleanup_error'])
@pytest.mark.parametrize('error', [SystemExit(0), SystemExit(PRIVATE), KeyboardInterrupt(PRIVATE)])
def test_process_exit_cannot_falsely_succeed_or_disclose_private_error(managed, capsys, position, error):
    managed[position] = error
    code, out = invoke(capsys)
    assert code == (130 if isinstance(error, KeyboardInterrupt) else 1)
    assert out['evaluation'] is None and out['history_gate'] == 'not_established'
    assert managed['events'][-1] == ('exit',)
    assert sum(e[0] == 'settled' for e in managed['events']) == 1


@pytest.mark.parametrize('mutation', ['flag', 'count', 'unknown-status', 'foreign-id', 'nonfinite', 'extra'])
def test_corrupt_export_not_reported_as_complete(managed, capsys, mutation):
    r = managed['report']
    if mutation == 'flag': r['single_database_snapshot'] = 1
    elif mutation == 'count': r['status_counts']['settled_simulation'] = 0
    elif mutation == 'unknown-status': r['attempts'][0]['status'] = 'unknown'
    elif mutation == 'foreign-id': r['attempts'][0]['record_id'] = 'foreign'
    elif mutation == 'nonfinite': r['groups'][0]['binary_payout_sum'] = float('nan')
    else: r['raw_source'] = PRIVATE
    code, out = invoke(capsys)
    assert code == 1 and out['evaluation'] is None


def test_success_serialization_occurs_after_managed_cleanup(managed, capsys, monkeypatch):
    original = cli.json.dumps
    def checked(value, **kw):
        if type(value) is dict and value.get('evaluation_kind') == 'settled_paper':
            assert managed['events'][-1] == ('exit',)
        return original(value, **kw)
    monkeypatch.setattr(cli.json, 'dumps', checked)
    code, out = invoke(capsys)
    assert code == 0


def test_unknown_conflict_message_is_sanitized_without_str_override(managed, capsys):
    class EvilConflict(cli.ResearchCaptureConflict):
        def __str__(self): raise SystemExit(PRIVATE)
    managed['error'] = EvilConflict(PRIVATE)
    code, out = invoke(capsys)
    assert code == 1 and out['reason_code'] == 'research_paper_evaluation_operation_failed'


def test_failed_stdout_does_not_retry_or_print_traceback(managed, monkeypatch):
    writes = []
    def broken(value):
        writes.append(value)
        raise BrokenPipeError(PRIVATE)
    import sys
    # Restore the live stream before pytest reports the result under -s.
    with monkeypatch.context() as scoped:
        scoped.setattr(sys.stdout, 'write', broken)
        assert cli.main(['--settled-paper'], default_root=Path('/unused')) == 1
    assert len(writes) == 1 and managed['events'][-1] == ('exit',)


def test_summary_does_not_mutate_original_and_details_can_be_read_again():
    report = exported(); original = deepcopy(report)
    small = cli._settled_paper_summary(report, include_decisions=False)
    small['groups'][0]['binary_payout_sum'] = 'changed'
    full = cli._settled_paper_summary(report, include_decisions=True)
    assert report == original and full['attempts'] == original['attempts']
    assert full['groups'] == original['groups']


@pytest.mark.parametrize('capture', ['no', 'fd'])
def test_output_fault_fixture_restores_stream_before_pytest_reporting(tmp_path, capture):
    """An injected CLI fault must not damage pytest's real terminal writer."""
    import subprocess
    import sys
    import xml.etree.ElementTree as ET
    from polymarket_alpha_lab.project_postgres.files import clean_environment
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / 'output-fixture.xml'
    names = ('test_failed_stdout_does_not_retry_or_print_traceback',
             'test_summary_does_not_mutate_original_and_details_can_be_read_again')
    program = '''
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path[:0] = [str(root / 'src'), str(root)]
from polymarket_alpha_lab import research_evaluation_cli as cli
assert Path(cli.__file__).resolve().is_relative_to(root)
import pytest
raise SystemExit(pytest.main(sys.argv[2:]))
'''
    args = ['-q', '--capture=' + capture,
            *('tests/test_research_settled_cli_review.py::' + name for name in names),
            '--junitxml=' + str(report)]
    env = dict(clean_environment(), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    result = subprocess.run([sys.executable, '-I', '-c', program, str(root), *args],
        cwd=root, env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert b'2 passed' in result.stdout and result.stderr == b''
    xml = ET.fromstring(report.read_bytes())
    cases = list(xml.iter('testcase'))
    assert [(c.get('classname'), c.get('name')) for c in cases] == [
        ('tests.test_research_settled_cli_review', name) for name in names]
    assert all(c.find('failure') is None and c.find('error') is None
               and c.find('skipped') is None for c in cases)
    assert sum(int(s.get('tests', '0')) for s in xml.iter('testsuite')) == 2
