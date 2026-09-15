"""Adversarial same-assistant self-review of engineering-only timeout evidence."""
import io
import json
from types import SimpleNamespace
import subprocess

import pytest

from tests import handoff_probe as probe
from tests.test_handoff_probe import child


def test_diagnostic_sink_failure_must_not_replace_timeout_or_skip_kill(child,monkeypatch):
    error=subprocess.TimeoutExpired('fixture',30)
    child.update(timeout=True,timeout_error=error)
    def broken(*args,**kwargs):raise BrokenPipeError('synthetic-private-output-failure')
    monkeypatch.setattr('builtins.print',broken)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        probe.run_traced(['fixture'],child['trace'])
    assert caught.value is error and child['calls'].count('kill')==1
    assert error.__notes__==['handoff_probe_diagnostic_failed']


@pytest.mark.parametrize('tail,state',[(b'hash_end|3|10','partial'),
    (b'unknown-secret-value|3|10\n','invalid'),(b'hash_end|0|10\n','invalid'),
    (b'hash_end|3|11\n','invalid')],ids=['torn','untrusted-name','backward-clock','changed-frequency'])
def test_prefix_survives_without_promoting_corrupt_tail(tmp_path,tail,state):
    p=tmp_path/'trace';p.write_bytes(b'harness_enter|1|10\nhash_begin|2|10\n'+tail)
    result=probe.read_trace(p)
    assert result['trace_state']==state and result['last_stage']=='hash_begin'
    assert len(result['events'])==2 and 'secret-value' not in json.dumps(result)


def test_trace_reader_never_reads_an_unbounded_file():
    class Source:
        def open(self,mode):
            assert mode=='rb'
            class Stream(io.BytesIO):
                def read(self,n):
                    assert n==probe.MAX_TRACE_BYTES+1
                    return b'x'*n
            return Stream()
    assert probe.read_trace(Source())['trace_state']=='over_limit'


def test_permission_error_is_not_exposed_or_called_missing():
    class Source:
        def open(self,mode):raise PermissionError('synthetic-private-path')
    result=probe.read_trace(Source())
    assert result==dict(trace_state='unreadable',events=[],last_stage=None)


def test_no_trace_after_timeout_does_not_invent_a_stage(child,capsys):
    child['trace'].unlink()
    child.update(timeout=True,timeout_error=subprocess.TimeoutExpired('fixture',30))
    with pytest.raises(subprocess.TimeoutExpired):probe.run_traced(['fixture'],child['trace'])
    details=json.loads(capsys.readouterr().out.removeprefix('handoff_probe '))
    assert details['last_stage'] is None and details['trace_state']=='missing'


def test_timeout_report_is_emitted_before_a_potentially_blocked_cleanup(child,monkeypatch):
    events=[]
    error=subprocess.TimeoutExpired('fixture',30)
    child.update(timeout=True,timeout_error=error)
    def emit(*args,**kwargs):
        assert kwargs['flush'] is True
        assert 'kill' not in child['calls']
        events.append(args[0])
    monkeypatch.setattr('builtins.print',emit)
    with pytest.raises(subprocess.TimeoutExpired):probe.run_traced(['fixture'],child['trace'])
    assert len(events)==1 and child['calls'].count('kill')==1
