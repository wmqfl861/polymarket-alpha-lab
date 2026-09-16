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
