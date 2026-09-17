"""Separate adversarial review of the test-only packaged recovery proof."""
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests import packaged_recovery_flow as flow
from tests.test_packaged_recovery_flow import state


@pytest.mark.parametrize('done,reason', [
    (subprocess.CompletedProcess([], 0, b'{"status":"blocked"}', b''), 'refused'),
    (subprocess.CompletedProcess([], 1, b'ignored', b'{"status":"blocked","reason_code":"refused"}'), 'refused'),
    (subprocess.CompletedProcess([], 1, b'', b'{"status":"blocked","reason_code":"wrong"}'), 'refused'),
    (subprocess.CompletedProcess([], 0, b'{}', b'private-detail'), None),
])
def test_false_process_witness_cannot_certify_recovery(monkeypatch, tmp_path, done, reason):
    calls = []
    monkeypatch.setattr(flow.subprocess, 'run', lambda *a, **k: calls.append(1) or done)
    with pytest.raises(AssertionError): flow.database_command(tmp_path, ['restore'], reason=reason)
    assert calls == [1]


@pytest.mark.parametrize('operation', ['database', 'recipe'])
def test_timeout_is_preserved_not_retried_or_interpreted_as_restored(monkeypatch, tmp_path, operation):
    original = subprocess.TimeoutExpired('synthetic recovery', 60 if operation == 'database' else 180)
    calls = []
    def fail(*args, **kwargs): calls.append(1); raise original
    monkeypatch.setattr(flow.subprocess, 'run', fail)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        if operation == 'database': flow.database_command(tmp_path, ['restore'])
        else: flow.run_recovery_recipe(tmp_path, sys.executable, tmp_path,
                    historical_at='2026-09-16T00:00:00+00:00', expected_instance='a' * 32)
    assert caught.value is original and calls == [1]


@pytest.mark.parametrize('case', ['no-modules', 'foreign-module'])
def test_source_origin_checks_cannot_pass_on_empty_or_foreign_package(monkeypatch, tmp_path, case):
    modules = {} if case == 'no-modules' else {
        'polymarket_alpha_lab': SimpleNamespace(__file__=str(tmp_path.parent / 'foreign/__init__.py'))}
    monkeypatch.setattr(flow, 'sys', SimpleNamespace(modules=modules))
    with pytest.raises(AssertionError): flow.assert_origins(tmp_path)


def test_command_line_remains_bounded_for_representative_windows_paths(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(flow.subprocess, 'run', lambda args, **kw: captured.append(args))
    root = tmp_path / ('k' * 160)
    flow.run_recovery_recipe(root, root / '.venv/Scripts/python.exe', tmp_path,
        historical_at='2026-09-16T00:00:00+00:00', expected_instance='a' * 32)
    units = len(subprocess.list2cmdline(captured[0]).encode('utf-16-le')) // 2 + 1
    assert units < 32767


def test_snapshot_ignores_only_budget_observation_time_not_original_policy(state):
    before = flow.saved_state(state.db, state.at)
    for value in state.budgets.values(): value.observed_at += timedelta(hours=1)
    assert flow.saved_state(state.db, state.at) == before
    state.budgets['kit-budget'].stored = object()
    assert flow.saved_state(state.db, state.at) != before


@pytest.mark.parametrize('calls,micros', [(6, 600), (7, 600), (8, 800)])
def test_budget_refund_or_duplicate_reservation_is_not_a_recovery_success(state, calls, micros):
    state.budgets['kit-budget'].reserved_calls = calls
    state.budgets['kit-budget'].reserved_micros = micros
    with pytest.raises(AssertionError): flow.saved_state(state.db, state.at)


def test_recovery_recipe_is_not_shipped_as_application_code():
    from polymarket_alpha_lab.project_postgres.distribution import selected_source
    assert not selected_source('tests/packaged_recovery_flow.py')


@pytest.mark.parametrize('batch_id', ['kit-btc', 'kit-eth'])
def test_fresh_batch_observation_does_not_change_durable_recovery_evidence(state, batch_id):
    """Two real snapshot instances of identical stored rows differ only in read time."""
    from dataclasses import replace
    before = flow.saved_state(state.db, state.at)
    old = state.batches[batch_id]
    state.batches[batch_id] = replace(old, generated_at=old.generated_at + timedelta(seconds=1))
    assert old != state.batches[batch_id]
    assert flow.saved_state(state.db, state.at) == before


@pytest.mark.parametrize('batch_id', ['kit-btc', 'kit-eth'])
@pytest.mark.parametrize('change', ['batch-id', 'enqueued-at', 'claimed-at', 'recorded-at', 'missing-result'])
def test_batch_comparison_keeps_every_durable_field(state, batch_id, change):
    from dataclasses import replace
    before = flow.saved_state(state.db, state.at)
    value = state.batches[batch_id]
    if change == 'batch-id':
        changed = replace(value, stored=replace(value.stored,
            batch=replace(value.stored.batch, batch_id='different-batch')))
    elif change == 'enqueued-at':
        changed = replace(value, stored=replace(value.stored,
            enqueued_at=value.stored.enqueued_at + timedelta(microseconds=1)))
    else:
        execution = value.executions[0]
        if change == 'claimed-at':
            replacement = replace(execution, claimed_at=execution.claimed_at + timedelta(microseconds=1))
        elif change == 'recorded-at':
            replacement = replace(execution, record=replace(execution.record,
                recorded_at=execution.record.recorded_at + timedelta(microseconds=1)))
        else:
            replacement = replace(execution, status='incomplete', record=None)
        changed = replace(value, executions=(replacement, *value.executions[1:]))
    state.batches[batch_id] = changed
    assert flow.saved_state(state.db, state.at) != before


@pytest.mark.parametrize('value', [None, object(), SimpleNamespace(stored=1, executions=(), generated_at=1)])
def test_batch_projection_does_not_accept_a_shape_only_imitation(value):
    with pytest.raises(AssertionError, match='invalid batch snapshot type'):
        flow.batch_history(value)


def test_invalid_observation_clock_is_not_ignored_by_projection(state):
    value = state.batches['kit-btc']
    object.__setattr__(value, 'generated_at', value.stored.enqueued_at - timedelta(seconds=1))
    with pytest.raises(ValueError, match='clock_regression'):
        flow.batch_history(value)


def test_new_snapshot_field_requires_explicit_recovery_review(state, monkeypatch):
    original = flow.fields
    monkeypatch.setattr(flow, 'fields', lambda value: (*original(value), SimpleNamespace(name='new_durable_field')))
    with pytest.raises(AssertionError, match='batch snapshot schema changed'):
        flow.batch_history(state.batches['kit-btc'])


@pytest.mark.parametrize('batch_id', ['kit-btc', 'kit-eth'])
def test_elapsed_time_does_not_hide_an_entire_lost_claim(state, batch_id):
    from dataclasses import replace
    before = flow.saved_state(state.db, state.at)
    value = state.batches[batch_id]
    state.batches[batch_id] = replace(value, generated_at=value.generated_at + timedelta(days=1),
                                    executions=(None, *value.executions[1:]))
    assert flow.saved_state(state.db, state.at) != before


@pytest.mark.parametrize('field', ['recorded_at', 'claimed_at'])
def test_previous_batch_evidence_is_not_aliased_to_a_later_snapshot_mutation(state, field):
    before = flow.saved_state(state.db, state.at)
    execution = state.batches['kit-btc'].executions[0]
    target = execution.record if field == 'recorded_at' else execution
    object.__setattr__(target, field, getattr(target, field) + timedelta(microseconds=1))
    assert flow.saved_state(state.db, state.at) != before


def test_mutated_bound_request_is_rejected_not_normalized_into_equal_history(state):
    value = state.batches['kit-btc']
    object.__setattr__(value.stored.batch.requests[0], 'protocol_version', 'mutated-protocol')
    with pytest.raises(ValueError, match='execution_mismatch'):
        flow.batch_history(value)


def test_partial_cold_inventory_is_not_the_recovery_comparison_contract():
    from dataclasses import fields
    from polymarket_alpha_lab.research_dispatch import ResearchBatchSnapshot
    # This pins the complete current shape, not a count or selected row digest.
    assert {field.name for field in fields(ResearchBatchSnapshot)} == {
        'stored', 'generated_at', 'executions'}
