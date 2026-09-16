"""Offline checks of the packaged-flow fixtures, not native execution evidence."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import packaged_research_flow as flow
from tests.test_research_paper_capture import captured
from polymarket_alpha_lab import research_paper_capture as store
from polymarket_alpha_lab.research_execution import decode_execution_request
from polymarket_alpha_lab.research_paper_capture_codec import encode_paper_scenario, decode_paper_scenario, checksum
from polymarket_alpha_lab.team_research_evaluation import ResearchEvaluationReport

AT = datetime(2026, 9, 16, 10, tzinfo=UTC)
OPEN = AT + timedelta(minutes=2)


@pytest.mark.parametrize('number', range(4))
def test_exact_fixture_bindings_and_original_scenario_decisions(number):
    rows = flow.prepared(AT, OPEN)
    request, raw = rows[number]
    assert len({r.record_id for r, _ in rows}) == len({r.intake.condition_id for r, _ in rows}) == 4
    assert decode_execution_request(request.payload, request.content_sha256) == request
    assert request.forecast_cutoff_at == OPEN-timedelta(seconds=1)
    execution = captured(request, 'failed' if number == 3 else 'completed')
    if request.intake.team_id == 'crypto_eth' and number != 3:
        record = execution.record
        execution = replace(execution, record=replace(record, run=replace(record.run,
            research=replace(record.run.research, probability_yes=D('.1')))))
    scenario = flow.scenario(execution, raw, AT+timedelta(seconds=1), rejected=number == 2)
    payload = encode_paper_scenario(scenario)
    assert decode_paper_scenario(payload, checksum(payload)) == scenario
    history = ResearchEvaluationReport((execution.record,), (), scenario.decision_at)
    result = store._result_for(history, scenario, execution)
    assert result['status'] == ('paper_scenario_ready', 'paper_scenario_ready',
                               'paper_scenario_rejected', 'not_simulated')[number]
    if number < 2:
        assert result['selected_side'] == ('yes', 'no')[number]
        assert D(result['assumed_totals']['assumed_total_cost_upper_bound']) == (D('2.029'), D('3.029'))[number]
    if number == 2:
        assert scenario.yes_book.raw_json == b'{synthetic malformed'


@pytest.mark.parametrize('team,p', [('crypto_btc', '0.7'), ('crypto_eth', '0.1')])
def test_synthetic_client_has_only_two_explicit_tool_responses(team, p):
    client = flow.Model(team)
    first = client.complete(messages_json='{}', max_output_tokens=1024)
    second = client.complete(messages_json='{}', max_output_tokens=1024)
    assert first.calls[0].name == 'read_evidence'
    assert second.calls[0].name == 'finish_research'
    assert json.loads(second.calls[0].arguments_json)['probability_yes'] == p
    assert client.calls == 2


def test_synthetic_failed_request_is_not_replaced_by_a_prediction():
    client = flow.Model('crypto_eth', fail=True)
    with pytest.raises(RuntimeError, match='synthetic packaged model failure'):
        client.complete(messages_json='{}', max_output_tokens=1024)
    assert client.calls == 1


def test_recipe_launch_uses_supplied_interpreter_and_no_checkout_path(monkeypatch, tmp_path):
    calls = []
    result = SimpleNamespace(returncode=23, stdout='', stderr='failure')
    def launch(args, **kw):
        calls.append((args, kw)); return result
    monkeypatch.setattr(flow.subprocess, 'run', launch)
    root = tmp_path / 'kit'
    python = root / '.venv/Scripts/python.exe'
    assert flow.run_packaged_recipe(root, python, tmp_path) is result
    args, kw = calls[0]
    assert args[:3] == [str(python), '-I', '-c'] and args[-1] == str(root)
    assert 'tests.packaged_research_flow' not in args[3]
    assert str(Path(flow.__file__).parent) not in args[3]
    assert kw['timeout'] == 300 and kw['shell'] is False and kw['check'] is False
    assert len(calls) == 1
