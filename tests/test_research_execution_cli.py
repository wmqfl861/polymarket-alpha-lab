"""Read-only claim inspection and redaction, with synthetic persisted states."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from polymarket_alpha_lab import research_execution_cli as cli
from polymarket_alpha_lab.research_execution import CapturedResearchExecution
from polymarket_alpha_lab.team_taxonomy import TEAM_IDS
from tests.test_research_execution import NOW, make_run, record_for, request, state

ROOT = Path(__file__).resolve().parents[1]


def persisted(status="completed", team="crypto_eth"):
    run = make_run(status="failed" if status == "blocked" else status, team=team)
    if status == "blocked":
        run = replace(run, research=replace(run.research, status="blocked", reason_code="invalid_model_action"))
    req = request(intake=run.intake, required_source_ids=() if status == "intake_blocked" else ("s",))
    return replace(state(req), status="already_captured", record=record_for(req, run))


@pytest.mark.parametrize("team", TEAM_IDS)
@pytest.mark.parametrize("status", ["completed", "failed", "blocked", "intake_blocked"])
def test_all_team_record_states_preserve_metadata_not_source_text(team, status):
    saved = persisted(status, team)
    before = saved.to_dict()
    output = cli.execution_summary(saved, record_id="r1")
    assert output["inspection_status"] == "captured_" + status
    assert all(output[k] == v for k, v in before.items())
    assert saved.to_dict() == before
    assert output["team_id"] == team and output["worker_liveness"] == "unknown"
    assert output["this_claim_has_no_captured_result"] is False
    assert output["eligible_source_count"] == (0 if status == "intake_blocked" else 1)
    assert output["required_source_count"] == (0 if status == "intake_blocked" else 1)
    assert output["entire_history_checked"] is output["scoring_performed"] is False
    assert output["automatic_retry_permitted"] is output["forecast_approval_performed"] is False
    encoded = json.dumps(output, allow_nan=False)
    for forbidden in ("SYNTHETIC-PRIVATE", "Synthetic rules.", "synthetic:source", "probability_yes",
                      "confidence", "source_ids", "tool_trace", "raw_json", "question", "summary"):
        assert forbidden not in encoded
    if status == "intake_blocked":
        assert output["stored_research"] is None
    else:
        research = saved.record.run.research
        assert output["stored_research"] == dict(status=status, reason_code=research.reason_code,
            model_calls=research.model_calls, tool_calls=research.tool_calls,
            total_tokens=research.total_tokens, cited_source_count=len(research.source_ids))


def test_incomplete_is_unknown_not_zero_or_failed_and_not_recoverable_from_lookup():
    output = cli.execution_summary(state(), record_id="r1")
    assert output["inspection_status"] == "result_not_captured"
    assert output["this_claim_has_no_captured_result"] is True
    assert output["stored_research"] is output["recorded_at"] is output["record_sha256"] is None
    assert output["worker_liveness"] == "unknown" and output["automatic_retry_permitted"] is False
    assert "failed" not in output["inspection_status"]


@pytest.mark.parametrize("kind", ["foreign_id", "live_captured", "capture_failed", "dict", "none"])
def test_nonlookup_or_mismatched_states_refused(kind):
    saved = persisted()
    if kind == "live_captured": saved = replace(saved, status="captured")
    elif kind == "capture_failed": saved = replace(state(), status="capture_failed", pending_run=make_run())
    elif kind == "dict": saved = {"status": "already_captured"}
    elif kind == "none": saved = None
    with pytest.raises(ValueError):
        cli.execution_summary(saved, record_id="foreign" if kind == "foreign_id" else "r1")


@pytest.mark.parametrize("where", ["state", "request", "evidence", "record", "result"])
def test_changed_nested_flags_are_revalidated(where):
    saved = persisted()
    target = {"state": saved, "request": saved.request, "evidence": saved.request.intake.task.evidence[0],
              "record": saved.record, "result": saved.record.run.research}[where]
    object.__setattr__(target, "readonly", False)
    with pytest.raises(ValueError): cli.execution_summary(saved, record_id="r1")


def test_time_representation_and_output_mutation_do_not_change_original_state():
    saved = persisted()
    canonical = cli.execution_summary(saved, record_id="r1")
    shifted = replace(saved, claimed_at=saved.claimed_at.astimezone(timezone(timedelta(hours=8))))
    assert cli.execution_summary(shifted, record_id="r1") == canonical
    canonical["limits"]["max_model_calls"] = 999
    canonical["stored_research"]["model_calls"] = 999
    assert cli.execution_summary(saved, record_id="r1")["stored_research"]["model_calls"] != 999
    assert saved.request.limits.max_model_calls != 999


@pytest.fixture
def managed(monkeypatch, capsys):
    events = []
    values = dict(state=persisted(), error=None, exit_error=None)
    class Session:
        def inspect(self, **kwargs):
            events.append(("inspect", kwargs))
            if values["error"] is not None: raise values["error"]
            return values["state"]
        def run_research(self, **kwargs): pytest.fail("read must not launch")
        def retry_capture(self, **kwargs): pytest.fail("read must not repair")
        def evaluate(self, **kwargs): pytest.fail("single claim is not complete history")
    class DB:
        def __init__(self, root): events.append(("root", root))
        @contextmanager
        def session(self):
            events.append(("enter",))
            try: yield Session()
            finally:
                assert capsys.readouterr() == ("", "")  # Never publish prior to successful close.
                events.append(("exit",))
                if values["exit_error"] is not None: raise values["exit_error"]
    monkeypatch.setattr(cli, "ProjectPostgres", DB)
    return values, events


def invoke(args=None):
    return cli.main(["--record-id", "r1"] if args is None else args, default_root=Path("/unused/project"))


def test_cli_inspects_once_and_prints_only_after_successful_session_exit(managed, capsys):
    assert invoke(["--record-id", "r1", "--root", "/unused/explicit"]) == 0
    _, events = managed
    assert events == [("root", Path("/unused/explicit")), ("enter",), ("inspect", {"record_id": "r1"}), ("exit",)]
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "inspected" and result["inspection"]["inspection_status"] == "captured_completed"
    assert result["lookup_scope"] == "execution_claim_and_matching_attempt"
    for flag in ("public_network_called", "live_model_called", "business_writes_performed"):
        assert result[flag] is False


@pytest.mark.parametrize("saved", [None, state()])
def test_absent_claim_and_incomplete_are_distinct_successful_reads(managed, capsys, saved):
    values, _ = managed
    values["state"] = saved
    assert invoke() == (3 if saved is None else 0)
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == ("claim_not_found" if saved is None else "inspected")
    if saved is None: assert output["inspection"] is None
    else: assert output["inspection"]["inspection_status"] == "result_not_captured"


@pytest.mark.parametrize("kind", ["read", "close", "not_found_close", "bad_type", "foreign_id", "memory_result"])
def test_failure_redacted_no_success_fallback_or_retry(managed, capsys, kind):
    values, events = managed
    error = RuntimeError("PRIVATE-DSN-PATH-FIXTURE")
    if kind == "read": values["error"] = error
    elif kind in ("close", "not_found_close"):
        values["exit_error"] = error
        if kind == "not_found_close": values["state"] = None
    elif kind == "bad_type": values["state"] = {"secret": "PRIVATE-DSN-PATH-FIXTURE"}
    elif kind == "foreign_id": values["state"] = state(request(record_id="other"))
    else: values["state"] = replace(persisted(), status="captured")
    assert invoke() == 1
    out = capsys.readouterr()
    assert "PRIVATE" not in out.out and out.err == ""
    result = json.loads(out.out)
    assert result["status"] == "failed" and result["inspection"] is None
    assert result["reason_code"] == "research_execution_inspection_failed"
    assert sum(event[0] == "inspect" for event in events) == 1


@pytest.mark.parametrize("args", [[], ["--record-id", ""], ["--record-id", "bad id"],
    ["--record-id", "../escape"], ["--record-id", "x" * 129], ["--rec", "r1"],
    ["--record-id", "r1", "--as-of", "2026-09-14"], ["--record-id", "r1", "--retry"],
    ["--record-id", "r1", "--dsn", "unused"], ["--record-id", "r1", "--allow-public-fetch"]])
def test_bad_arguments_fail_before_manager(managed, args):
    _, events = managed
    with pytest.raises(SystemExit) as error: invoke(args)
    assert error.value.code == 2 and events == []


def test_help_needs_no_manager(managed):
    with pytest.raises(SystemExit) as error: invoke(["--help"])
    assert error.value.code == 0 and managed[1] == []


@pytest.mark.parametrize("phase", ["read", "close"])
def test_interrupts_are_not_relabelled(managed, capsys, phase):
    values, _ = managed
    values["error" if phase == "read" else "exit_error"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): invoke()
    assert capsys.readouterr() == ("", "")


def test_actual_script_help_from_installed_package():
    result = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/inspect_project_research.py"), "--help"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "--record-id" in result.stdout


@pytest.mark.parametrize('defect', ['receipt_digest', 'task_id', 'result_reason', 'negative_usage'])
def test_corrupt_nonflag_fields_fail_instead_of_exporting_plausible_metadata(defect):
    saved = persisted()
    if defect == 'receipt_digest':
        object.__setattr__(saved.request.intake.source_receipts[0], 'content_sha256', '0' * 64)
    elif defect == 'task_id':
        object.__setattr__(saved.record.run.research, 'task_id', 'another-task')
    elif defect == 'result_reason':
        object.__setattr__(saved.record.run.research, 'reason_code', 'PRIVATE-UNTRUSTED-ERROR')
    else:
        object.__setattr__(saved.record.run.research, 'total_tokens', -1)
    with pytest.raises(ValueError): cli.execution_summary(saved, record_id='r1')


# Single-ID recovery lookups must not report success after internal/output failure.
@pytest.mark.parametrize('stage', ['read', 'close', 'missing-close', 'summary', 'construct', 'enter'])
@pytest.mark.parametrize('exit_code', [0, 7])
def test_internal_exit_is_a_failed_lookup_not_process_success(managed, capsys, monkeypatch, stage, exit_code):
    values, events = managed
    error = SystemExit(exit_code)
    def fail(*a, **k):
        raise error
    if stage == 'read':
        values['error'] = error
    elif stage in ('close', 'missing-close'):
        values['exit_error'] = error
        if stage == 'missing-close':
            values['state'] = None
    elif stage == 'summary':
        monkeypatch.setattr(cli, 'execution_summary', fail)
    elif stage == 'construct':
        monkeypatch.setattr(cli, 'ProjectPostgres', fail)
    else:
        class Database:
            def __init__(self, root): pass
            def session(self): raise error
        monkeypatch.setattr(cli, 'ProjectPostgres', Database)
    try:
        code = invoke()
    except SystemExit as escaped:
        code = ('escaped', escaped.code)
    assert code == 1
    output = capsys.readouterr()
    envelope = json.loads(output.out)
    assert envelope['status'] == 'failed' and envelope['inspection'] is None
    assert envelope['reason_code'] == 'research_execution_inspection_failed'
    assert envelope['business_writes_performed'] is envelope['live_model_called'] is False
    assert output.err == ''
    assert sum(e[0] == 'inspect' for e in events) <= 1


@pytest.mark.parametrize('outcome', ['found', 'missing', 'failed'])
@pytest.mark.parametrize('fault', ['short', 'write-exit', 'flush-error', 'flush-exit',
                                  'write-interrupt', 'flush-interrupt'])
def test_lookup_output_failure_is_nonzero_one_write_after_close(
        managed, monkeypatch, outcome, fault):
    values, events = managed
    if outcome == 'missing': values['state'] = None
    if outcome == 'failed': values['error'] = ValueError('PRIVATE-LOOKUP-ERROR')
    writes, flushes = [], []
    class Output:
        def write(self, text):
            assert events[-1] == ('exit',)
            writes.append(text)
            if fault == 'write-exit': raise SystemExit(0)
            if fault == 'write-interrupt': raise KeyboardInterrupt('PRIVATE-OUTPUT')
            return len(text) - int(fault == 'short')
        def flush(self):
            flushes.append(1)
            if fault == 'flush-error': raise OSError('PRIVATE-OUTPUT')
            if fault == 'flush-exit': raise SystemExit(0)
            if fault == 'flush-interrupt': raise KeyboardInterrupt('PRIVATE-OUTPUT')
    with monkeypatch.context() as patch:
        patch.setattr(sys, 'stdout', Output())
        try:
            code = invoke()
        except (Exception, SystemExit, KeyboardInterrupt) as escaped:
            code = ('escaped', type(escaped).__name__)
    assert code == (130 if fault.endswith('interrupt') else 1)
    assert len(writes) == 1 and len(flushes) == int(fault.startswith('flush'))
    assert writes[0].endswith('\n')
    assert 'PRIVATE' not in writes[0]
    assert sum(e[0] == 'inspect' for e in events) == 1


@pytest.mark.parametrize('outcome', ['found', 'missing', 'failed'])
def test_lookup_success_envelope_keeps_exact_json_and_one_checked_flush(
        managed, monkeypatch, capsys, outcome):
    values, events = managed
    if outcome == 'missing': values['state'] = None
    if outcome == 'failed': values['error'] = OSError('PRIVATE-LOOKUP-ERROR')
    # Capture the original contract payload; actual metadata validation is unchanged.
    expected_code = invoke()
    expected = capsys.readouterr().out
    events.clear()
    writes, flushed = [], []
    class Output:
        def write(self, text):
            assert events[-1] == ('exit',)
            writes.append(text)
            return len(text)
        def flush(self): flushed.append(1)
    with monkeypatch.context() as patch:
        patch.setattr(sys, 'stdout', Output())
        assert invoke() == expected_code
    assert writes == [expected] and flushed == [1]
    assert expected_code == {'found': 0, 'missing': 3, 'failed': 1}[outcome]


# Separate review: output serialization and real interpreter/import ordering.
@pytest.mark.parametrize('error', [ValueError('PRIVATE-SERIALIZE'), OSError('PRIVATE-SERIALIZE'),
                                 SystemExit(0), KeyboardInterrupt('PRIVATE-SERIALIZE')])
def test_lookup_review_serialization_failure_never_writes_a_fallback(managed, monkeypatch, error):
    from polymarket_alpha_lab import research_resolution_confirmation_cli as output
    original = output.json.dumps
    writes = []
    def dumps(value, *args, **kwargs):
        if type(value) is dict and 'lookup_scope' in value:
            assert managed[1][-1] == ('exit',)
            raise error
        return original(value, *args, **kwargs)
    class Stream:
        def write(self, text): writes.append(text); return len(text)
        def flush(self): pytest.fail('flush without serialization')
    with monkeypatch.context() as patch:
        patch.setattr(output.json, 'dumps', dumps)
        patch.setattr(sys, 'stdout', Stream())
        assert invoke() == (130 if isinstance(error, KeyboardInterrupt) else 1)
    assert writes == []
    assert sum(e[0] == 'inspect' for e in managed[1]) == 1


@pytest.mark.parametrize('order', ['confirmation-first', 'inspection-first'])
@pytest.mark.parametrize('fault', ['exit', 'short', 'flush'])
def test_lookup_review_fresh_interpreter_reports_nonzero(monkeypatch, order, fault):
    from polymarket_alpha_lab.project_postgres.files import clean_environment
    body = r"""
import importlib, json, runpy, sys
from pathlib import Path
from contextlib import contextmanager
root, order, fault = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
name = 'polymarket_alpha_lab.research_execution_cli'
confirmation = 'polymarket_alpha_lab.research_resolution_confirmation_cli'
for item in ((confirmation, name) if order == 'confirmation-first' else (name, confirmation)):
    importlib.import_module(item)
module = importlib.import_module(name)
calls = []
class Session:
    def inspect(self, **kwargs):
        calls.append(kwargs)
        if fault == 'exit': raise SystemExit(0)
        return None
    inspect_resolution = inspect
class Database:
    def __init__(self, root): pass
    @contextmanager
    def session(self): yield Session()
module.ProjectPostgres = Database
original = sys.stdout
class Stream:
    def write(self, text):
        if fault == 'short': return 0
        return original.write(text)
    def flush(self):
        if fault == 'flush': raise SystemExit(0)
        original.flush()
sys.stdout = Stream()
path = root / 'scripts/inspect_project_research.py'
sys.argv = [str(path), '--record-id', 'r1']
try:
    runpy.run_path(str(path), run_name='__main__')
except SystemExit as result:
    code = result.code
finally:
    sys.stdout = original
assert len(calls) == 1
raise SystemExit(code)
"""
    result = subprocess.run([sys.executable, '-I', '-c', body, str(ROOT), order, fault],
        capture_output=True, env=clean_environment(), timeout=30, check=False)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stderr == b''
    if fault == 'short':
        assert result.stdout == b''
    else:
        envelope = json.loads(result.stdout)
        assert envelope['inspection'] is None and envelope['business_writes_performed'] is False
        assert envelope['status'] == ('failed' if fault == 'exit' else 'claim_not_found')
