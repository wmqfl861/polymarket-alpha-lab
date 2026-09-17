"""Metadata-only resolution readback with synthetic stored evidence and no I/O."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from polymarket_alpha_lab import research_resolution_inspection_cli as cli
from polymarket_alpha_lab.research_resolution import assess_resolution
from polymarket_alpha_lab.research_resolution_codec import encode_resolution
from polymarket_alpha_lab.research_resolution_store import StoredResolutionReview
from polymarket_alpha_lab.team_research_evaluation import ResearchEvaluationOutcome
from tests.test_research_resolution import NOW, CID, SLUG, submission

ROOT = Path(__file__).resolve().parents[1]


def stored(status="ready", yes=True, *, confirmation=None):
    data = {"outcomePrices": ["1", "0"] if yes else ["0", "1"], "question": "PRIVATE-QUESTION",
            "description": "PRIVATE-RULES"}
    if status == "pending": data["closed"] = False
    if status == "blocked": data["outcomePrices"] = ["0.5", "0.5"]
    item = submission(data=data, confirmation=True)
    provided = status == "ready" if confirmation is None else confirmation
    item = replace(item, confirmation=replace(item.confirmation, actual_yes=yes,
        reviewer_id="PRIVATE-REVIEWER", source_reference="https://example.invalid/PRIVATE-REFERENCE",
        source_text="PRIVATE-SOURCE-TEXT 秘密") if provided else None)
    payload = encode_resolution(item)
    outcome = None
    if assess_resolution(item).status == "ready":
        outcome = ResearchEvaluationOutcome(CID, SLUG, NOW-timedelta(seconds=10),
            item.confirmation.resolved_at, NOW+timedelta(seconds=2), yes,
            "urn:polymarket-alpha-lab:resolution-review:" + item.review_id,
            sha256(payload.encode()).hexdigest())
    return StoredResolutionReview(item, NOW+timedelta(seconds=1), outcome)


def summary(review):
    return cli.resolution_review_summary(review, review_id="review-1")


@pytest.mark.parametrize("status", ["pending", "blocked", "needs_confirmation", "ready"])
@pytest.mark.parametrize("yes", [True, False])
def test_all_states_preserve_original_hashes_and_unknowns_without_exporting_raw_fields(status, yes):
    review = stored(status, yes)
    payload = encode_resolution(review.submission)
    output = summary(review)
    assert output["inspection_status"] == cli._INSPECTIONS[status]
    assert output["assessment"]["candidate_yes"] is (yes if status in ("needs_confirmation", "ready") else None)
    assert output["checked_at"] == NOW.isoformat()
    assert output["recorded_at"] == review.recorded_at.isoformat()
    assert output["payload_sha256"] == sha256(payload.encode()).hexdigest()
    assert output["payload_bytes"] == len(payload.encode())
    assert output["snapshot"] == dict(fetched_at=NOW.isoformat(),
        content_sha256=review.submission.snapshot.content_sha256, bytes=len(review.submission.snapshot.raw_json))
    assert encode_resolution(review.submission) == payload
    assert output["confirmation_provided"] is (status == "ready")
    if status != "ready":
        assert output["linked_outcome"] is output["submitted_confirmation"] is None
    else:
        assert output["linked_outcome"]["actual_yes"] is yes
        assert output["linked_outcome"]["source_content_sha256"] == output["payload_sha256"]
        assert output["submitted_confirmation"]["asserted_yes"] is yes
        assert output["submitted_confirmation"]["source_bytes"] == len("PRIVATE-SOURCE-TEXT 秘密".encode())
        assert output["submitted_confirmation"]["source_content_sha256"] == review.submission.confirmation.source_content_sha256
    assert output["assessment_basis"] == "stored_checked_at"
    for flag in ("current_freshness_checked", "other_reviews_checked", "market_settlement_status_checked",
                 "independent_verification_performed", "scoring_performed", "forecast_approval_performed"):
        assert output[flag] is False
    encoded = json.dumps(output, allow_nan=False)
    for private in ("PRIVATE", "raw_json", "raw_base64", "source_reference", "reviewer_id", "source_text",
                    "question", "description", "probability_yes", "confidence", "tool_trace"):
        assert private not in encoded


@pytest.mark.parametrize("status", ["pending", "blocked"])
def test_submitted_confirmation_is_not_a_confirmed_outcome(status):
    output = summary(stored(status, False, confirmation=True))
    assert output["confirmation_provided"] is True
    assert output["submitted_confirmation"]["asserted_yes"] is False
    assert output["submitted_confirmation"]["independently_verified_assertion"] is True
    assert output["linked_outcome"] is None and output["assessment"]["candidate_yes"] is None
    assert output["inspection_status"] == "recorded_" + status


def test_disagreeing_confirmation_is_preserved_as_blocked_not_false_outcome():
    item = submission()
    item = replace(item, confirmation=replace(item.confirmation, actual_yes=False))
    report = summary(StoredResolutionReview(item, NOW))
    assert report["assessment"]["reason_code"] == "confirmation_disagrees"
    assert report["submitted_confirmation"]["asserted_yes"] is False
    assert report["linked_outcome"] is None


@pytest.mark.parametrize("raw", [b"bad-json-PRIVATE", b"\xff\x00", b"null", b'{"n":NaN}'])
def test_malformed_raw_evidence_stays_inspectable_without_raw_export(raw):
    item = submission(confirmation=False)
    item = replace(item, snapshot=replace(item.snapshot, raw_json=raw))
    output = summary(StoredResolutionReview(item, NOW))
    assert output["inspection_status"] == "recorded_blocked"
    assert output["snapshot"]["bytes"] == len(raw)
    assert output["snapshot"]["content_sha256"] == sha256(raw).hexdigest()
    assert "PRIVATE" not in str(output)


@pytest.mark.parametrize("kind", ["wrong_id", "submission_only", "dict", "none", "outcome_dict"])
def test_wrong_scope_or_nonreceipt_refused(kind):
    value = stored()
    if kind == "submission_only": value = value.submission
    elif kind == "dict": value = {"status": "ready"}
    elif kind == "none": value = None
    elif kind == "outcome_dict": object.__setattr__(value, "outcome", {})
    with pytest.raises(ValueError):
        cli.resolution_review_summary(value, review_id="foreign" if kind == "wrong_id" else "review-1")


@pytest.mark.parametrize("where", ["receipt", "submission", "snapshot", "confirmation", "outcome"])
@pytest.mark.parametrize("value", [False, 1])
def test_nested_flags_revalidated(where, value):
    receipt = stored()
    target = {"receipt": receipt, "submission": receipt.submission, "snapshot": receipt.submission.snapshot,
              "confirmation": receipt.submission.confirmation, "outcome": receipt.outcome}[where]
    object.__setattr__(target, "readonly", value)
    with pytest.raises(ValueError): summary(receipt)


@pytest.mark.parametrize("field,value", [("condition_id", "foreign"), ("market_slug", "foreign"),
    ("actual_yes", False), ("source_content_sha256", "0"*64), ("source_reference", "synthetic:other"),
    ("recorded_at", NOW), ("resolved_at", NOW), ("actual_yes", 1)])
def test_corrupted_outcome_links_cannot_look_verified(field, value):
    receipt = stored()
    object.__setattr__(receipt.outcome, field, value)
    with pytest.raises(ValueError): summary(receipt)


@pytest.mark.parametrize("seconds", [-1, 601])
def test_receipt_time_bounds_remain_enforced(seconds):
    receipt = stored("pending")
    object.__setattr__(receipt, "recorded_at", NOW+timedelta(seconds=seconds))
    with pytest.raises(ValueError): summary(receipt)


def test_detached_revalidation_and_output_mutation_do_not_change_input():
    receipt = stored()
    expected = summary(receipt)
    offset = timezone(timedelta(hours=-4))
    shifted = receipt.outcome.resolved_at.astimezone(offset)
    # Adversarial frozen-object mutation: legal equal instant but noncanonical
    # representation must be normalized on a COPY, never on the input outcome.
    object.__setattr__(receipt.outcome, "resolved_at", shifted)
    assert summary(receipt) == expected
    assert receipt.outcome.resolved_at is shifted
    expected["snapshot"]["bytes"] = 999
    expected["linked_outcome"]["actual_yes"] = False
    assert summary(receipt)["linked_outcome"]["actual_yes"] is True
    assert summary(receipt)["snapshot"]["bytes"] != 999


@pytest.fixture
def managed(monkeypatch, capsys):
    values = dict(review=stored(), error=None, exit_error=None)
    events = []
    class Session:
        def inspect_resolution(self, **kw):
            events.append(("inspect", kw))
            if values["error"] is not None: raise values["error"]
            return values["review"]
        def record_resolution(self, **kw): pytest.fail("inspection must not confirm")
        def evaluate(self, **kw): pytest.fail("inspection must not score")
        def collect_resolution_candidates(self, **kw): pytest.fail("inspection must not fetch")
    class Database:
        def __init__(self, root): events.append(("manager", root))
        @contextmanager
        def session(self):
            events.append(("enter",))
            try: yield Session()
            finally:
                assert capsys.readouterr() == ("", "")
                events.append(("exit",))
                if values["exit_error"] is not None: raise values["exit_error"]
    monkeypatch.setattr(cli, "ProjectPostgres", Database)
    return values, events


def invoke(args=None):
    return cli.main(["--review-id", "review-1"] if args is None else args, default_root=Path("project"))


def test_exact_managed_call_and_success_only_after_cleanup(managed, capsys):
    assert invoke(["--root", "explicit-project", "--review-id", "review-1"]) == 0
    assert managed[1] == [("manager", Path("explicit-project")), ("enter",),
                           ("inspect", {"review_id": "review-1"}), ("exit",)]
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "inspected"
    assert report["lookup_scope"] == "resolution_review_and_its_linked_outcome"
    for flag in ("public_network_called", "live_model_called", "business_writes_performed", "outcome_confirmation_performed"):
        assert report[flag] is False


@pytest.mark.parametrize("status", [None, "pending", "blocked", "needs_confirmation"])
def test_missing_review_not_confused_with_unconfirmed_states(managed, capsys, status):
    managed[0]["review"] = None if status is None else stored(status)
    assert invoke() == (3 if status is None else 0)
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == ("review_not_found" if status is None else "inspected")
    if status is None: assert report["inspection"] is None
    else: assert report["inspection"]["linked_outcome"] is None


@pytest.mark.parametrize("phase", ["read", "close", "not_found_close", "wrong_type", "wrong_id", "corrupt_link"])
def test_errors_redacted_without_partial_data_or_retry(managed, capsys, phase):
    values, events = managed
    if phase == "read": values["error"] = RuntimeError("PRIVATE-DSN-PATH")
    elif phase in ("close", "not_found_close"):
        values["exit_error"] = RuntimeError("PRIVATE-DSN-PATH")
        if phase == "not_found_close": values["review"] = None
    elif phase == "wrong_type": values["review"] = {"PRIVATE": 1}
    elif phase == "wrong_id": values["review"] = replace(stored("pending"), submission=replace(submission(confirmation=False), review_id="other"))
    else: object.__setattr__(values["review"].outcome, "source_content_sha256", "0"*64)
    assert invoke() == 1
    out = capsys.readouterr()
    assert out.err == "" and "PRIVATE" not in out.out
    report = json.loads(out.out)
    assert report["status"] == "failed" and report["inspection"] is None
    assert report["reason_code"] == "research_resolution_inspection_failed"
    assert sum(event[0] == "inspect" for event in events) == 1


@pytest.mark.parametrize("args", [[], ["--review-id", ""], ["--review-id", "bad id"],
    ["--review-id", "../path"], ["--review-id", "x"*129], ["--rev", "review-1"],
    ["--review-id", "r", "--confirm"], ["--review-id", "r", "--raw"],
    ["--review-id", "r", "--as-of", "2026-09-14"], ["--review-id", "r", "--dsn", "none"],
    ["--review-id", "r", "--allow-public-fetch"]])
def test_invalid_or_unauthorized_arguments_fail_before_manager(managed, args):
    with pytest.raises(SystemExit) as error: invoke(args)
    assert error.value.code == 2 and managed[1] == []


def test_help_constructs_no_manager(managed):
    with pytest.raises(SystemExit) as error: invoke(["--help"])
    assert error.value.code == 0 and managed[1] == []


@pytest.mark.parametrize("phase", ["read", "close"])
def test_interrupts_propagate_without_false_report(managed, capsys, phase):
    managed[0]["error" if phase == "read" else "exit_error"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): invoke()
    assert capsys.readouterr() == ("", "")


def test_actual_installed_script_help():
    result = subprocess.run([sys.executable, "-I", str(ROOT/"scripts/inspect_project_resolution.py"), "--help"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "--review-id" in result.stdout and "--confirm" not in result.stdout


@pytest.mark.parametrize("delta,reason", [(-1, "snapshot_from_future"), (601, "snapshot_stale")])
def test_stored_timing_rejection_is_not_reclassified_using_current_clock(delta, reason):
    item = submission(confirmation=False)
    item = replace(item, checked_at=NOW+timedelta(seconds=delta))
    receipt = StoredResolutionReview(item, item.checked_at)
    result = summary(receipt)
    assert result["inspection_status"] == "recorded_blocked"
    assert result["assessment"]["reason_code"] == reason
    assert result["current_freshness_checked"] is False


def test_old_candidate_remains_candidate_not_automatically_stale_or_promoted():
    # All dates are deliberately historical. There is no lookup-time freshness,
    # nor a consultation of unrelated later outcomes in this single-row view.
    receipt = stored("needs_confirmation", False)
    result = summary(receipt)
    assert result["inspection_status"] == "recorded_needs_confirmation"
    assert result["assessment"]["candidate_yes"] is False
    assert result["linked_outcome"] is None
    assert result["market_settlement_status_checked"] is False


def test_revalidation_refuses_a_confirmed_row_without_its_original_outcome():
    receipt = stored()
    object.__setattr__(receipt, "outcome", None)
    with pytest.raises(ValueError): summary(receipt)


def test_nonready_row_cannot_adopt_an_outcome_from_another_review():
    receipt = stored("needs_confirmation")
    object.__setattr__(receipt, "outcome", stored().outcome)
    with pytest.raises(ValueError): summary(receipt)


def test_output_confirmation_mutation_does_not_modify_original_proof():
    receipt = stored()
    before = encode_resolution(receipt.submission)
    report = summary(receipt)
    report["submitted_confirmation"]["asserted_yes"] = False
    report["submitted_confirmation"]["source_content_sha256"] = "0"*64
    assert encode_resolution(receipt.submission) == before
    assert summary(receipt)["submitted_confirmation"]["asserted_yes"] is True


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
            values['review'] = None
    elif stage == 'summary':
        monkeypatch.setattr(cli, 'resolution_review_summary', fail)
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
    assert envelope['reason_code'] == 'research_resolution_inspection_failed'
    assert envelope['business_writes_performed'] is envelope['live_model_called'] is False
    assert output.err == ''
    assert sum(e[0] == 'inspect' for e in events) <= 1


@pytest.mark.parametrize('outcome', ['found', 'missing', 'failed'])
@pytest.mark.parametrize('fault', ['short', 'write-exit', 'flush-error', 'flush-exit',
                                  'write-interrupt', 'flush-interrupt'])
def test_lookup_output_failure_is_nonzero_one_write_after_close(
        managed, monkeypatch, outcome, fault):
    values, events = managed
    if outcome == 'missing': values['review'] = None
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
    if outcome == 'missing': values['review'] = None
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
name = 'polymarket_alpha_lab.research_resolution_inspection_cli'
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
path = root / 'scripts/inspect_project_resolution.py'
sys.argv = [str(path), '--review-id', 'review-1']
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
        assert envelope['status'] == ('failed' if fault == 'exit' else 'review_not_found')
