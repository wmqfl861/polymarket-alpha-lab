"""Offline tests for the campaign evidence summary; no PowerShell, HTTP or git."""
import json
import re
import shutil

import pytest

from tests import handoff_campaign_summary as summary

TARGET_CLASSNAME = summary.TARGET_CLASSNAME
TARGET_NAME = summary.TARGET_NAME
RUN = 8831702501
ATTEMPT = 1
SHA = '1f2e3d4c5b6a7f988776655443322110ffeeddcc'


def attr(value):
    """pytest/ElementTree-style attribute escaping (quotes become &quot;)."""
    return (value.replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def make_evidence(**overrides):
    stages = [dict(stage=stage, elapsed_ms=index * 137)
              for index, stage in enumerate(
                  ('script_entered', 'encoding_ready', 'helper_loaded',
                   'invoke_entered', 'manifest_entered', 'manifest_returned',
                   'payload_entered', 'payload_returned', 'invoke_returned',
                   'serialization_entered', 'serialization_returned'))]
    evidence = dict(outcome='returned', process_limit_seconds=30,
                    elapsed_seconds=8.25, stages=stages,
                    last_observed_stage='serialization_returned',
                    trace_truncated=False, malformed_marker_seen=False,
                    retry_count=0, root_cause_established=False)
    evidence.update(overrides)
    return evidence


def junit_xml(*testcases):
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<testsuite errors="0" failures="0" name="pytest" skips="0" tests="{}" '
            'time="61.234">{}</testsuite>'.format(len(testcases), ''.join(testcases)))


def target_testcase(evidence=None, *, junit='pass', properties=None, children=None):
    if properties is None:
        properties = [] if evidence is None else [
            ('handoff_process', json.dumps(evidence, sort_keys=True))]
    body = ''.join('<property name="{}" value="{}"/>'.format(attr(name), attr(value))
                   for name, value in properties)
    if body:
        body = '<properties>{}</properties>'.format(body)
    if children is None:
        children = [] if junit == 'pass' else [
            '<failure message="TimeoutExpired: Command timed out" '
            'type="TimeoutExpired">subprocess timeout trace</failure>']
    return ('<testcase classname="{}" name="{}" time="8.123">{}{}</testcase>'
            .format(attr(TARGET_CLASSNAME), attr(TARGET_NAME), body, ''.join(children)))


def standard_others(*extra):
    windows = json.dumps(make_evidence(outcome='timeout', elapsed_seconds=30.012,
                                       stages=[], last_observed_stage=None),
                         sort_keys=True)
    return [
        ('<testcase classname="tests.test_handoff_process_probe_windows" '
         'name="test_real_timeout_preserves_entered_stage" time="30.012">'
         '<properties><property name="handoff_process" value="{}"/></properties>'
         '</testcase>').format(attr(windows)),
        '<testcase classname="tests.test_handoff_process_probe" '
        'name="test_stderr_text_bytes_and_missing_are_supported[value0]" time="0.002"/>',
        '<testcase classname="tests.test_handoff_process_probe_review" '
        'name="test_offline_evidence_contracts_unchanged" time="0.001"/>',
    ] + list(extra)


def trial_xml(evidence=None, *, junit='pass', others=None):
    if others is None:
        others = standard_others()
    return junit_xml(target_testcase(evidence, junit=junit), *others)


def write_trial(base, trial, xml_text, *, run=RUN, attempt=ATTEMPT, sha=SHA):
    directory = base / 'handoff-capture-{}-{}-{}-{}'.format(trial, run, attempt, sha)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / summary.XML_NAME).write_text(xml_text, encoding='utf-8')
    return directory


def default_campaign(base, *, skip=()):
    base.mkdir(parents=True, exist_ok=True)
    for trial in range(1, summary.EXPECTED_TRIALS + 1):
        if trial not in skip:
            write_trial(base, trial, trial_xml(make_evidence()))


def apply_scenario(base, trial, which):
    directory = base / 'handoff-capture-{}-{}-{}-{}'.format(trial, RUN, ATTEMPT, SHA)
    if which == 'missing-dir':
        shutil.rmtree(directory, ignore_errors=True)
        return
    directory.mkdir(parents=True, exist_ok=True)
    xml = directory / summary.XML_NAME
    if which == 'missing-xml':
        if xml.exists():
            xml.unlink()
        return
    if which == 'oversized-xml':
        xml.write_bytes(b'<?xml version="1.0" encoding="utf-8"?><testsuite><!--'
                        + b'p' * (summary.MAX_XML_BYTES + 1) + b'--></testsuite>')
        return
    if which == 'xml-parse-error':
        xml.write_bytes(b'<?xml version="1.0"?><testsuite><testcase '
                        b'classname="tests.test_handoff_download">')
        return
    if which == 'duplicate-target':
        testcase = target_testcase(make_evidence())
        xml.write_text(junit_xml(testcase, testcase, *standard_others()),
                       encoding='utf-8')
        return
    if which == 'duplicate-property':
        value = attr(json.dumps(make_evidence(), sort_keys=True))
        xml.write_text(junit_xml(
            '<testcase classname="{}" name="{}" time="8.1"><properties>'
            '<property name="handoff_process" value="{}"/>'
            '<property name="handoff_process" value="{}"/></properties></testcase>'
            .format(attr(TARGET_CLASSNAME), attr(TARGET_NAME), value, value),
            *standard_others()), encoding='utf-8')
        return
    if which == 'malformed-property-json':
        xml.write_text(junit_xml(
            '<testcase classname="{}" name="{}" time="8.1"><properties>'
            '<property name="handoff_process" value="{}"/></properties></testcase>'
            .format(attr(TARGET_CLASSNAME), attr(TARGET_NAME), attr('not-json{')),
            *standard_others()), encoding='utf-8')
        return
    if which == 'skipped-target':
        xml.write_text(junit_xml(
            '<testcase classname="{}" name="{}" time="0.001">'
            '<skipped type="pytest.skip" message="real Windows integration"/>'
            '</testcase>'.format(attr(TARGET_CLASSNAME), attr(TARGET_NAME)),
            *standard_others()), encoding='utf-8')
        return
    if which == 'errored-target':
        xml.write_text(junit_xml(
            '<testcase classname="{}" name="{}" time="0.001">'
            '<error message="collection error">fixture setup failed</error>'
            '</testcase>'.format(attr(TARGET_CLASSNAME), attr(TARGET_NAME)),
            *standard_others()), encoding='utf-8')
        return
    if which == 'no-target':
        xml.write_text(junit_xml(*standard_others()), encoding='utf-8')
        return
    if which == 'no-property':
        xml.write_text(junit_xml(
            '<testcase classname="{}" name="{}" time="8.1"/>'
            .format(attr(TARGET_CLASSNAME), attr(TARGET_NAME)),
            *standard_others()), encoding='utf-8')
        return
    evidence = make_evidence()
    if which == 'missing-key':
        del evidence['stages']
    elif which == 'extra-key':
        evidence['unexpected_sentinel'] = 1
    elif which == 'wrong-limit':
        evidence['process_limit_seconds'] = 31
    elif which == 'nonzero-retry':
        evidence['retry_count'] = 2
    elif which == 'root-cause-true':
        evidence['root_cause_established'] = True
    elif which == 'bad-outcome':
        evidence['outcome'] = 'mystery'
    else:
        raise AssertionError('unknown scenario ' + which)
    xml.write_text(trial_xml(evidence), encoding='utf-8')


def run_summary(tmp_path, artifacts):
    out = tmp_path / 'campaign-summary.md'
    code = summary.main(['--artifacts', str(artifacts), '--markdown', str(out)])
    return code, out.read_text(encoding='utf-8')


def trial_rows(text):
    return [line for line in text.splitlines()
            if re.match(r'^\| \d+ \|', line)]


def row_for(text, trial):
    return next(line for line in text.splitlines()
                if line.startswith('| {} |'.format(trial)))


def test_all_30_valid_campaign_exits_0_with_exact_bound_line(tmp_path):
    base = tmp_path / 'artifacts'
    default_campaign(base)
    code, text = run_summary(tmp_path, base)
    assert code == 0
    rows = trial_rows(text)
    assert len(rows) == summary.EXPECTED_TRIALS
    success = 0.05 ** (1.0 / summary.EXPECTED_TRIALS)
    expected = ('0 observed timeouts in 30 trials; one-sided 95% lower bound on '
                'per-trial success probability \u2248 {:.3f} (upper bound on timeout '
                'probability \u2248 {:.3f}), assuming independent comparable trials; '
                'this is not evidence the defect is absent.'
                .format(success, 1 - success))
    assert expected in text
    assert '\u2248 0.905' in text and '\u2248 0.095' in text
    assert '- run identity: run {} attempt {} sha {}'.format(RUN, ATTEMPT, SHA) in text
    assert 'passed=30 failed=0 incomplete=0' in text
    assert all(row.endswith('| false |') for row in rows)
    assert 'incomplete:' not in text


def test_observed_timeout_trial_exits_1_without_bound_line(tmp_path):
    base = tmp_path / 'artifacts'
    default_campaign(base)
    timeout = make_evidence(outcome='timeout', elapsed_seconds=30.012,
                            stages=[], last_observed_stage=None)
    write_trial(base, 7, trial_xml(timeout, junit='fail'))
    code, text = run_summary(tmp_path, base)
    assert code == 1
    row = row_for(text, 7)
    assert '| complete | fail | timeout | 30.012 | none |' in row
    assert 'one-sided 95%' not in text
    assert all(row.endswith('| false |') for row in trial_rows(text))
    assert 'passed=29 failed=1 incomplete=0' in text


def test_returned_process_with_junit_failure_is_exit_1(tmp_path):
    base = tmp_path / 'artifacts'
    default_campaign(base)
    write_trial(base, 12, trial_xml(make_evidence(), junit='fail'))
    code, text = run_summary(tmp_path, base)
    assert code == 1
    row = row_for(text, 12)
    assert '| complete | fail | returned |' in row
    assert '| serialization_returned |' in row
    assert 'one-sided 95%' not in text
    assert 'passed=29 failed=1 incomplete=0' in text


EXIT2_SCENARIOS = [
    pytest.param('missing-dir', 'incomplete: missing artifact directory', id='missing-dir'),
    pytest.param('missing-xml', 'missing handoff-first.xml', id='missing-xml'),
    pytest.param('oversized-xml', 'oversized XML', id='oversized-xml'),
    pytest.param('xml-parse-error', 'XML parse error', id='xml-parse-error'),
    pytest.param('duplicate-target', 'multiple target testcases', id='duplicate-target'),
    pytest.param('duplicate-property', 'multiple handoff_process properties', id='duplicate-property'),
    pytest.param('malformed-property-json', 'malformed property JSON', id='malformed-json'),
    pytest.param('skipped-target', 'JUnit skipped target', id='skipped-target'),
    pytest.param('errored-target', 'JUnit errored target', id='errored-target'),
    pytest.param('no-target', 'no target testcase found', id='no-target'),
    pytest.param('no-property', 'no handoff_process property', id='no-property'),
    pytest.param('missing-key', 'missing key: stages', id='missing-key'),
    pytest.param('extra-key', 'unexpected key', id='extra-key'),
    pytest.param('wrong-limit', 'process_limit_seconds must be 30', id='wrong-limit'),
    pytest.param('nonzero-retry', 'retry_count must be 0', id='nonzero-retry'),
    pytest.param('root-cause-true', 'root_cause_established must be false', id='root-cause-true'),
    pytest.param('bad-outcome', 'outcome must be one of', id='bad-outcome'),
]


@pytest.mark.parametrize('which,reason', EXIT2_SCENARIOS)
def test_incomplete_or_invalid_trial_evidence_exits_2(tmp_path, which, reason):
    base = tmp_path / 'artifacts'
    default_campaign(base)
    apply_scenario(base, 5, which)
    code, text = run_summary(tmp_path, base)
    assert code == 2
    row = row_for(text, 5)
    assert row.startswith('| 5 | incomplete: ')
    assert reason in row
    assert 'passed=29 failed=0 incomplete=1' in text


def test_exit_2_precedence_over_exit_1(tmp_path):
    base = tmp_path / 'artifacts'
    default_campaign(base)
    apply_scenario(base, 3, 'missing-xml')
    write_trial(base, 9, trial_xml(make_evidence(), junit='fail'))
    code, text = run_summary(tmp_path, base)
    assert code == 2
    assert 'passed=28 failed=1 incomplete=1' in text


def test_other_testcases_never_selected_for_defect_count(tmp_path):
    poisoned_windows = (
        '<testcase classname="tests.test_handoff_process_probe_windows" '
        'name="test_real_timeout_preserves_entered_stage" time="30.5">'
        '<properties><property name="handoff_process" value="{}"/></properties>'
        '<failure message="TimeoutExpired: Command timed out" type="TimeoutExpired">'
        'boom</failure></testcase>').format(attr(json.dumps(
            make_evidence(outcome='timeout', elapsed_seconds=30.5, stages=[],
                          last_observed_stage=None), sort_keys=True)))
    poisoned_unit = (
        '<testcase classname="tests.test_handoff_process_probe" '
        'name="test_original_failure_propagates_once_with_fixed_evidence[launch-error]" '
        'time="0.001"><properties><property name="handoff_process" value="{}"/>'
        '</properties><failure message="OSError"/></testcase>').format(attr(json.dumps(
            make_evidence(outcome='launch_failed', elapsed_seconds=0.0, stages=[],
                          last_observed_stage=None), sort_keys=True)))
    others = standard_others(poisoned_windows, poisoned_unit)
    base = tmp_path / 'artifacts'
    base.mkdir(parents=True)
    for trial in range(1, summary.EXPECTED_TRIALS + 1):
        write_trial(base, trial, trial_xml(make_evidence(), others=others))
    code, text = run_summary(tmp_path, base)
    assert code == 0
    assert 'one-sided 95%' in text
    assert 'passed=30 failed=0 incomplete=0' in text
    assert '- other testcases (never selected for the defect count):' in text
    assert '| fail |' not in text


def test_unexpected_directories_are_reported_and_never_selected(tmp_path):
    base = tmp_path / 'artifacts'
    default_campaign(base)
    poison = trial_xml(make_evidence(outcome='timeout', elapsed_seconds=30.0,
                                     stages=[], last_observed_stage=None), junit='fail')
    for name in ('handoff-capture-99-{}-{}-{}'.format(RUN, ATTEMPT, SHA),
                 'handoff-capture-0-{}-{}-{}'.format(RUN, ATTEMPT, SHA),
                 'stray-directory'):
        directory = base / name
        directory.mkdir()
        (directory / summary.XML_NAME).write_text(poison, encoding='utf-8')
    code, text = run_summary(tmp_path, base)
    assert code == 0
    assert len(trial_rows(text)) == summary.EXPECTED_TRIALS
    assert 'passed=30 failed=0 incomplete=0' in text
    assert 'unexpected artifact directory (not selected)' in text
    for name in ('handoff-capture-99-', 'handoff-capture-0-', 'stray-directory'):
        assert name in text
    assert '| fail | timeout |' not in text


def test_duplicate_trial_directory_is_over_bound_incomplete(tmp_path):
    base = tmp_path / 'artifacts'
    default_campaign(base)
    write_trial(base, 5, trial_xml(make_evidence()), sha='a' * 40)
    code, text = run_summary(tmp_path, base)
    assert code == 2
    assert row_for(text, 5).startswith('| 5 | incomplete: multiple artifact directories')
    assert 'artifact directories found: 31' in text
    assert 'bound: 30' in text
    assert '- run identity: mixed/unknown' in text
    assert 'incomplete=1' in text


def test_missing_trial_numbers_get_explicit_incomplete_rows(tmp_path):
    base = tmp_path / 'artifacts'
    base.mkdir(parents=True)
    for trial in (3, 17):
        write_trial(base, trial, trial_xml(make_evidence()))
    code, text = run_summary(tmp_path, base)
    assert code == 2
    assert len(trial_rows(text)) == summary.EXPECTED_TRIALS
    assert row_for(text, 1).startswith('| 1 | incomplete: missing artifact directory |')
    assert row_for(text, 30).startswith('| 30 | incomplete: missing artifact directory |')
    assert 'passed=2 failed=0 incomplete=28' in text


def test_missing_artifacts_root_still_writes_markdown_and_exits_2(tmp_path):
    code, text = run_summary(tmp_path, tmp_path / 'does-not-exist')
    assert code == 2
    rows = trial_rows(text)
    assert len(rows) == summary.EXPECTED_TRIALS
    assert all('incomplete: missing artifact directory' in row for row in rows)
    assert '- run identity: mixed/unknown' in text
    assert 'passed=0 failed=0 incomplete=30' in text


def test_run_identity_is_mixed_when_run_numbers_differ(tmp_path):
    base = tmp_path / 'artifacts'
    base.mkdir(parents=True)
    for trial in range(1, summary.EXPECTED_TRIALS + 1):
        write_trial(base, trial, trial_xml(make_evidence()), run=RUN + trial)
    code, text = run_summary(tmp_path, base)
    assert code == 0
    assert '- run identity: mixed/unknown' in text
