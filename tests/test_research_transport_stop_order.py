"""Codex transport stop/prepared/process entry ordering; synthetic only.

The Claude client already pins stop -> credential callback -> process ordering;
these mirror the same ordering for the Codex process transport: a requested stop
must keep the inert command builder unentered, and a stop raised during command
preparation must reach process admission before any child exists.
"""
import pytest

from polymarket_alpha_lab import research_codex_process as transport
from polymarket_alpha_lab.research_codex_exec import CodexExecInput
from polymarket_alpha_lab.research_dispatch_runner import ResearchDispatchStop
from tests.test_research_process import executable, spec


@pytest.mark.parametrize('bad', [object(), 'stop', False])
def test_invalid_stop_type_is_rejected_inertly(bad):
    with pytest.raises(ValueError, match='research_codex_process_stop_invalid'):
        transport.CodexProcessTransport(prepare_command=lambda _: pytest.fail('prepare'),
                                        allow_process_start=True, stop=bad)


def test_absent_stop_is_permitted_and_repr_stays_private():
    unit = transport.CodexProcessTransport(prepare_command=lambda _: pytest.fail('unused'),
                                           allow_process_start=True)
    assert repr(unit) == 'CodexProcessTransport(command=<private>)'


def test_stop_before_run_never_enters_prepare_or_process(monkeypatch):
    monkeypatch.setattr(transport, 'run_research_process', lambda **kw: pytest.fail('process'))
    stop = ResearchDispatchStop()
    stop.request_stop()
    entered = []

    def prepare(request):
        entered.append(request)

    unit = transport.CodexProcessTransport(prepare_command=prepare,
                                           allow_process_start=True, stop=stop)
    with pytest.raises(ValueError, match='research_codex_process_failed'):
        unit.run(CodexExecInput('model', '[{}]', 10))
    with pytest.raises(ValueError, match='research_codex_process_stopped'):
        unit.run(CodexExecInput('model', '[{}]', 10))
    assert entered == []


def test_stop_during_prepare_blocks_process_entry(executable, tmp_path):
    stop = ResearchDispatchStop()
    entered = []

    def prepare(request):
        entered.append(request)
        stop.request_stop()
        return spec(executable, tmp_path, 'open("started.marker","w").close()')

    unit = transport.CodexProcessTransport(prepare_command=prepare,
                                           allow_process_start=True, stop=stop)
    with pytest.raises(ValueError, match='research_codex_process_failed'):
        unit.run(CodexExecInput('model', '[{}]', 10))
    assert len(entered) == 1 and not (tmp_path / 'started.marker').exists()
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ValueError, match='research_codex_process_stopped'):
        unit.run(CodexExecInput('model', '[{}]', 10))
    assert len(entered) == 1
