"""Offline fixture contracts, not native recovery or access to private backups."""
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests import packaged_recovery_flow as flow


@pytest.fixture
def state():
    at = datetime(2026, 9, 16, tzinfo=UTC)
    executions = {rid: SimpleNamespace(record=object(), status='already_captured') for rid in flow.RECORDS}
    executions['interrupted-0'] = SimpleNamespace(record=None, status='incomplete')
    statuses = ('paper_scenario_ready', 'paper_scenario_ready', 'paper_scenario_rejected', 'not_simulated')
    papers = {rid: SimpleNamespace(to_dict=lambda status=status: {'result': {'status': status}})
              for rid, status in zip(flow.RECORDS, statuses, strict=True)}
    reviews = {prefix + rid: object() for rid in flow.RECORDS[:2] for prefix in ('candidate-', 'confirmed-')}
    budgets = {bid: SimpleNamespace(stored=object(), observed_at=at, reserved_calls=calls, reserved_micros=micros)
               for bid, calls, micros in flow.BUDGETS}
    inventory = dict(claim_count=5, captured_result_count=4, incomplete_claim_count=1)
    history = dict(attempt_count=4, status_counts={'settled_simulation': 2}, actual_account_pnl=None)
    # Use the real snapshot class so observation clocks are not hidden by mocks.
    from tests.test_research_dispatch import batch, receipt, NOW
    from polymarket_alpha_lab.research_dispatch import ResearchBatchSnapshot, StoredResearchBatch
    batches = {}
    for bid in ('kit-btc', 'kit-eth'):
        b = replace(batch(2), batch_id=bid)
        batches[bid] = ResearchBatchSnapshot(StoredResearchBatch(b, NOW),
            NOW + timedelta(seconds=3),
            tuple(replace(receipt(r), status='already_captured') for r in b.requests))
    turns = {'one': object(), 'two': object()}
    reads = []
    class Session:
        def execution_inventory(self): return SimpleNamespace(to_dict=lambda: inventory)
        def inspect(self, *, record_id): reads.append(record_id); return executions[record_id]
        def inspect_paper_research(self, *, record_id): return papers[record_id]
        def inspect_resolution(self, *, review_id): return reviews[review_id]
        def inspect_research_batch(self, *, batch_id): return batches[batch_id]
        def inspect_research_turn(self, *, rotation_id, turn_id):
            assert rotation_id == 'kit-rotation'
            return turns[turn_id]
        def inspect_model_budget(self, *, budget_id): return budgets[budget_id]
        def evaluate_settled_paper_research(self, **kwargs):
            if not kwargs: raise flow.ResearchCaptureConflict('research_execution_history_incomplete')
            assert kwargs == dict(generated_at=at)
            return history
    @contextmanager
    def session(): yield Session()
    return SimpleNamespace(db=SimpleNamespace(session=session), executions=executions, papers=papers,
        reviews=reviews, budgets=budgets, inventory=inventory, history=history, batches=batches,
        turns=turns, at=at, reads=reads)


def test_complete_fixture_snapshot_retains_every_category_and_original_objects(state):
    actual = flow.saved_state(state.db, state.at)
    assert state.reads == [*flow.RECORDS, 'interrupted-0']
    assert actual['executions'] == tuple(state.executions.values())
    assert actual['papers'] == tuple(state.papers.values())
    assert actual['reviews'] == tuple(state.reviews.values())
    assert actual['batches'] == tuple((value.stored, value.executions) for value in state.batches.values())
    assert actual['turns'] == tuple(state.turns.values())
    assert actual['budgets'] == tuple((v.stored, v.reserved_calls, v.reserved_micros) for v in state.budgets.values())
    assert actual['history'] is state.history


@pytest.mark.parametrize('category,key', [('executions', 'packaged-0'), ('papers', 'packaged-1'),
    ('reviews', 'confirmed-packaged-0'), ('batches', 'kit-btc'), ('turns', 'one'), ('budgets', 'kit-budget')])
def test_missing_fixture_evidence_does_not_make_an_empty_recovery_pass(state, category, key):
    getattr(state, category)[key] = None
    with pytest.raises(AssertionError): flow.saved_state(state.db, state.at)


def test_no_matching_fixture_roster_is_not_permission_to_recover_another_project(state):
    state.inventory['claim_count'] = 6
    with pytest.raises(AssertionError, match='fixture roster'): flow.saved_state(state.db, state.at)


@pytest.mark.parametrize('reason', [None, 'project_postgres_restore_existing_data_refused'])
def test_database_command_checks_exact_exit_channel_and_one_invocation(monkeypatch, tmp_path, reason):
    body = dict(status='blocked', reason_code=reason) if reason else dict(status='restored_stopped')
    raw = json.dumps(body).encode()
    done = subprocess.CompletedProcess([], 1 if reason else 0, b'' if reason else raw, raw if reason else b'')
    calls = []
    monkeypatch.setattr(flow.subprocess, 'run', lambda *args, **kw: calls.append((args, kw)) or done)
    assert flow.database_command(tmp_path, ['restore', 'synthetic-arguments'], reason=reason) == body
    assert len(calls) == 1
    args, options = calls[0]
    assert args[0] == [sys.executable, '-I', str(tmp_path / 'scripts/project_database.py'),
                       '--root', str(tmp_path), 'restore', 'synthetic-arguments']
    assert options['stdin'] == subprocess.DEVNULL and options['timeout'] == 60
    assert options['shell'] is options['check'] is False
    assert options['cwd'] == tmp_path.parent


def test_recovery_recipe_uses_own_python_explicit_identity_and_bounded_process(monkeypatch, tmp_path):
    calls = []
    done = subprocess.CompletedProcess([], 13, '', 'synthetic failure')
    monkeypatch.setattr(flow.subprocess, 'run', lambda *args, **kw: calls.append((args, kw)) or done)
    python = tmp_path / '.venv/Scripts/python.exe'
    at, identity = '2026-09-16T00:00:00+00:00', 'a' * 32
    assert flow.run_recovery_recipe(tmp_path, python, tmp_path.parent, historical_at=at, expected_instance=identity) is done
    args, options = calls[0]
    assert args[0][:3] == [str(python), '-I', '-c']
    assert args[0][-3:] == [str(tmp_path), at, identity]
    assert str(Path(flow.__file__).parent) not in args[0][3]
    assert options['timeout'] == 180 and options['stdin'] == subprocess.DEVNULL
    assert options['shell'] is options['check'] is False
    assert len(calls) == 1


def test_missing_kit_stops_before_any_recovery_file_is_created(tmp_path):
    root = tmp_path / 'missing-kit'; root.mkdir()
    done = flow.run_recovery_recipe(root, sys.executable, tmp_path,
        historical_at='2026-09-16T00:00:00+00:00', expected_instance='a' * 32)
    assert done.returncode != 0 and 'kit source missing' in done.stderr
    assert done.stdout == '' and list(tmp_path.iterdir()) == [root] and list(root.iterdir()) == []
