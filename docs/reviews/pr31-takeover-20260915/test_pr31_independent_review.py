"""Fresh-session PR #31 review probes; synthetic boundaries, never real billing.

Run against the pinned unmodified source snapshot with PYTHONPATH=<root>/src.
This is NOT a native SQL test, a full-suite run, or provider certification.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import product
from threading import Lock
from types import SimpleNamespace

import pytest

from polymarket_alpha_lab import research_model_budget_runner as runner
from polymarket_alpha_lab.research_execution import CapturedResearchExecution, CapturedResearchRequest
from polymarket_alpha_lab.research_model_budget import (
    ModelBudgetSnapshot, ModelCallBudget, StoredModelBudget, decode_budget,
)
from polymarket_alpha_lab.team_research_agent_types import (
    ResearchAgentLimits, ResearchEvidence, ResearchModelReply, ResearchToolCall, TeamResearchTask,
)
from polymarket_alpha_lab.team_research_intake import (
    ResearchSourceReceipt, TeamResearchIntake, evidence_content_sha256,
)

from polymarket_alpha_lab.local_postgres_dsn import validate_local_postgres_dsn

DSN = 'postgresql://postgres@127.0.0.1:54322/postgres'
validate_local_postgres_dsn(DSN, env_var_name='OFFLINE_REVIEW_DSN')
AT = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
MESSAGES = '[{"role":"user","content":"synthetic audit only"}]'


def request(index=0, team='crypto_btc'):
    evidence = ResearchEvidence('source', team, f'condition-{index}', 'Synthetic source',
        'Offline fixture, not a market observation.', 'synthetic:review-only', AT)
    task = TeamResearchTask(f'task-{index}', team, f'condition-{index}', f'audit-market-{index}',
        'Synthetic terminal event?', 'Synthetic terminal rule.', AT, (evidence,))
    receipt = ResearchSourceReceipt('source', evidence_content_sha256(evidence), evidence.reference, AT)
    intake = TeamResearchIntake(task.task_id, team, task.condition_id, task.market_slug,
        AT, AT, 'a'*64, 'prepared', 'research_intake_prepared', task, (receipt,))
    return CapturedResearchRequest(f'record-{index}', 'synthetic-model', 'audit-v1',
        AT+timedelta(hours=1), intake, ResearchAgentLimits(max_model_calls=3))


def policy(requests=None, **changes):
    requests = (request(),) if requests is None else requests
    initial = ModelCallBudget('audit-budget', 'synthetic-provider', 'synthetic-model',
        'USD', 1000, 10, 100, 10000, 1024, AT+timedelta(hours=2),
        tuple((r.record_id, r.content_sha256) for r in requests), 'b'*64, True)
    return replace(initial, **changes)


def reply():
    return ResearchModelReply((ResearchToolCall('read', 'read_evidence', '{"source_id":"source"}'),), 1)


def test_integer_accounting_exhaustive_small_domain():
    checked = 0
    base = policy()
    for total, charge, cap in product((1, 2, 3, 7, 19, 101), (1, 2, 3, 7), (1, 2, 5, 17)):
        if charge > total:
            continue
        p = replace(base, total_micros=total, per_call_micros=charge, max_calls=cap)
        stored = StoredModelBudget(p, AT)
        for used in range(min(cap, total//charge)+1):
            for expired in (False, True):
                now = p.expires_at if expired else AT+timedelta(seconds=1)
                result = ModelBudgetSnapshot(stored, now, used, used*charge).to_dict()
                expected = 0 if expired else min(cap-used, (total-used*charge)//charge)
                assert result['available_call_reservations'] == expected
                assert result['unreserved_micros'] == total-used*charge
                assert result['actual_provider_calls'] is None
                assert result['actual_billed_micros'] is None
                assert result['provider_charge_bound_verified'] is False
                checked += 1
    assert checked == 598


@pytest.mark.parametrize('field', ('total_micros', 'per_call_micros', 'max_calls',
                                   'max_message_bytes', 'max_output_tokens'))
@pytest.mark.parametrize('bad', (True, 1.0, '1', 0, -1))
def test_exact_positive_integer_policy_fields(field, bad):
    with pytest.raises(ValueError):
        replace(policy(), **{field: bad})


def test_policy_binding_and_roundtrip():
    requests = tuple(request(i, 'crypto_eth' if i%2 else 'crypto_btc') for i in range(100))
    p = policy(requests)
    assert decode_budget(p.payload, p.content_sha256) == p
    assert len(p.payload.encode()) <= 32768
    for r in requests:
        assert p.bind_request(r).payload == r.payload
    with pytest.raises(ValueError):
        p.bind_request(replace(requests[0], model_id='changed-model'))
    with pytest.raises(ValueError):
        p.bind_request(replace(requests[0], max_start_delay_seconds=299))


@pytest.mark.parametrize('reserved', (False, None, 1, 'true'))
def test_non_new_or_non_boolean_permit_never_enters_factory(monkeypatch, reserved):
    calls = []
    monkeypatch.setattr(runner, '_reserve_call', lambda *a, **k: reserved)
    model = runner._BudgetedModel(DSN, policy(), request(), lambda team: calls.append(team))
    with pytest.raises(ValueError, match='research_budget_call_blocked_or_failed'):
        model.complete(messages_json=MESSAGES, max_output_tokens=1)
    with pytest.raises(ValueError, match='research_budget_client_stopped'):
        model.complete(messages_json=MESSAGES, max_output_tokens=1)
    assert calls == []


@pytest.mark.parametrize('failure_site', ('reservation', 'factory', 'client'))
@pytest.mark.parametrize('error_type', (RuntimeError, KeyboardInterrupt, SystemExit))
def test_failure_latches_wrapper_without_resend(monkeypatch, failure_site, error_type):
    events = []
    error = error_type('private-error-marker')
    def reserve(*a, **k):
        events.append('reserve')
        if failure_site == 'reservation':
            raise error
        events.append('commit-returned')
        return True
    def complete(**kwargs):
        events.append('client')
        if failure_site == 'client':
            raise error
        return reply()
    def factory(team):
        events.append('factory')
        if failure_site == 'factory':
            raise error
        return SimpleNamespace(complete=complete)
    monkeypatch.setattr(runner, '_reserve_call', reserve)
    model = runner._BudgetedModel(DSN, policy(), request(), factory)
    expected = ValueError if error_type is RuntimeError else error_type
    with pytest.raises(expected) as captured:
        model.complete(messages_json=MESSAGES, max_output_tokens=1)
    if error_type is RuntimeError:
        assert str(captured.value) == 'research_budget_call_blocked_or_failed'
    else:
        assert captured.value is error
    expected_events = ['reserve']
    if failure_site != 'reservation':
        expected_events += ['commit-returned', 'factory']
    if failure_site == 'client':
        expected_events += ['client']
    assert events == expected_events
    with pytest.raises(ValueError, match='research_budget_client_stopped'):
        model.complete(messages_json=MESSAGES, max_output_tokens=1)
    assert events == expected_events


@pytest.mark.parametrize('invalid_reply', (None, True, 'reply'))
def test_malformed_reply_burns_permit_and_stops(monkeypatch, invalid_reply):
    events = []
    def reserve(*a, **k):
        events.append('reserve')
        return True
    def complete(**kwargs):
        events.append('client')
        return invalid_reply
    monkeypatch.setattr(runner, '_reserve_call', reserve)
    model = runner._BudgetedModel(DSN, policy(), request(), lambda _: SimpleNamespace(complete=complete))
    with pytest.raises(ValueError, match='research_budget_call_blocked_or_failed'):
        model.complete(messages_json=MESSAGES, max_output_tokens=1)
    with pytest.raises(ValueError, match='research_budget_client_stopped'):
        model.complete(messages_json=MESSAGES, max_output_tokens=1)
    assert events == ['reserve', 'client']


@pytest.mark.parametrize('cap', (1, 7, 31, 100))
def test_parallel_synthetic_reservation_boundary_and_recreated_clients(monkeypatch, cap):
    requests = tuple(request(i, 'crypto_eth' if i%2 else 'crypto_btc') for i in range(100))
    p = policy(requests, total_micros=cap*10, max_calls=cap)
    lock = Lock()
    seen, entries = set(), []
    def reserve(dsn, *, request, call_number, **kwargs):
        with lock:
            key = (request.record_id, call_number)
            if key in seen:
                return False
            if len(seen) >= cap:
                raise ValueError('synthetic-exhausted')
            seen.add(key)
            return True
    def factory(team):
        with lock:
            entries.append(team)
        return SimpleNamespace(complete=lambda **k: reply())
    monkeypatch.setattr(runner, '_reserve_call', reserve)
    def invoke(r):
        model = runner._BudgetedModel(DSN, p, r, factory)
        try:
            model.complete(messages_json=MESSAGES, max_output_tokens=1)
        except ValueError:
            return False
        return True
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(invoke, requests)) == cap
    assert len(seen) == len(entries) == cap
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert not any(pool.map(invoke, requests))
    assert len(seen) == len(entries) == cap


def test_expired_allowance_replays_original_incomplete_without_factory(monkeypatch):
    r, p = request(), policy()
    snapshot = ModelBudgetSnapshot(StoredModelBudget(p, AT), p.expires_at, 0, 0)
    original = CapturedResearchExecution(r, AT, 'incomplete')
    monkeypatch.setattr(runner, 'load_model_budget_with_psycopg', lambda *a, **k: snapshot)
    monkeypatch.setattr(runner.execution, 'inspect_captured_research_with_psycopg', lambda *a, **k: original)
    def forbidden(*a, **k):
        pytest.fail('expired replay attempted a new claim or client')
    monkeypatch.setattr(runner.execution, 'run_captured_research_with_psycopg', forbidden)
    actual = runner.run_budgeted_research_with_psycopg(DSN, request=r,
        budget_id=p.budget_id, model_factory=forbidden, allow_model_calls=True)
    assert actual == original


def test_empty_allowance_does_not_consume_new_claim(monkeypatch):
    p = policy(max_calls=1)
    snapshot = ModelBudgetSnapshot(StoredModelBudget(p, AT), AT, 1, 10)
    monkeypatch.setattr(runner, 'load_model_budget_with_psycopg', lambda *a, **k: snapshot)
    monkeypatch.setattr(runner.execution, 'inspect_captured_research_with_psycopg', lambda *a, **k: None)
    def forbidden(*a, **k):
        pytest.fail('empty budget attempted a new claim or client')
    monkeypatch.setattr(runner.execution, 'run_captured_research_with_psycopg', forbidden)
    with pytest.raises(ValueError, match='research_budget_not_available'):
        runner.run_budgeted_research_with_psycopg(DSN, request=request(),
            budget_id=p.budget_id, model_factory=forbidden, allow_model_calls=True)
