"""Offline contracts for test-only cold-start evidence; no PowerShell required."""
from base64 import b64decode
import io
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import pytest

from tests import handoff_probe as probe


def test_ascii_prelude_does_not_preload_cmdlets_or_print_paths(tmp_path):
    path = tmp_path/'space \u6d4b\u8bd5'/'trace.txt'
    source = probe.trace_prelude(path)
    source.encode('ascii')
    encoded = source.split("FromBase64String('",1)[1].split("')",1)[0]
    assert b64decode(encoded).decode('utf-8') == str(path)
    assert "Write-HandoffProbe 'harness_enter'" in source
    for forbidden in ('Import-Module', 'Get-Module', 'Get-Command', 'ConvertTo-Json',
                      'PSModulePath', 'ExecutionPolicy', 'Write-Host', 'Start-Process'):
        assert forbidden not in source


def test_trace_records_only_relative_intervals(tmp_path):
    path=tmp_path/'trace';path.write_bytes(b'harness_enter|100|1000\r\nsource_begin|150|1000\r\nharness_end|200|1000\r\n')
    result=probe.read_trace(path)
    assert result==dict(trace_state='complete',last_stage='harness_end',events=[
        dict(stage='harness_enter',since_first_ms=0),dict(stage='source_begin',since_first_ms=50),
        dict(stage='harness_end',since_first_ms=100)])
    assert str(path) not in json.dumps(result)


@pytest.mark.parametrize('raw,state',[(b'', 'empty'), (b'source_begin|1|1','partial'),
    (b'private=/some/secret\n','invalid'),(b'source_begin|1|0\n','invalid'),
    (b'source_begin|-1|1\n','invalid'),(b'source_begin|1|1000000000001\n','invalid'),
    (b'source_begin|9999999999999999999|1\n','invalid'),(b'source_begin|1|true\n','invalid'),
    (b'\xff\n','invalid')],ids=['empty','torn','unknown','zero-frequency','negative-ticks',
                            'huge-frequency','huge-ticks','not-numeric','bad-ascii'])
def test_invalid_trace_is_never_echoed(tmp_path,raw,state):
    p=tmp_path/'trace';p.write_bytes(raw)
    assert probe.read_trace(p)==dict(trace_state=state,events=[],last_stage=None)


def test_missing_trace_is_not_assumed_startup_failure(tmp_path):
    assert probe.read_trace(tmp_path/'missing')==dict(trace_state='missing',events=[],last_stage=None)


def test_over_limit_trace_preserves_only_bounded_valid_prefix(tmp_path):
    p=tmp_path/'trace';p.write_bytes(b'harness_enter|0|1\n'*1000)
    result=probe.read_trace(p)
    assert result['trace_state']=='over_limit' and len(result['events'])==probe.MAX_EVENTS


@pytest.fixture
def child(monkeypatch,tmp_path):
    state=dict(calls=[],poll_code=None,timeout=False,exception=None)
    path=tmp_path/'trace';path.write_bytes(b'harness_enter|1|1000\nsource_ready|2|1000\n')
    state['trace']=path
    class Process:
        def __enter__(self):return self
        def __exit__(self,*args):state['calls'].append('context_exit')
        def communicate(self,**kwargs):
            state['calls'].append(('communicate',kwargs))
            if state['exception'] is not None:raise state['exception']
            if state['timeout'] and 'timeout' in kwargs:
                raise state['timeout_error']
            return 'synthetic-private-stdout','synthetic-private-stderr'
        def poll(self):state['calls'].append('poll');return state['poll_code']
        def kill(self):state['calls'].append('kill')
        def wait(self):state['calls'].append('wait')
    def spawn(args,**kwargs):state['calls'].append(('spawn',args,kwargs));return Process()
    monkeypatch.setattr(probe.subprocess,'Popen',spawn)
    return state


@pytest.mark.parametrize('code',[0,1,27])
def test_completion_keeps_original_output_and_nonzero_exit(child,capsys,code):
    child['poll_code']=code
    run,details=probe.run_traced(['synthetic-child','--flag'],child['trace'])
    assert (run.returncode,run.stdout,run.stderr)==(code,'synthetic-private-stdout','synthetic-private-stderr')
    assert details['event']=='completed' and details['returncode']==code
    output=capsys.readouterr().out
    assert 'synthetic-private' not in output and '--flag' not in output
    assert len([c for c in child['calls'] if type(c)is tuple and c[0]=='spawn'])==1
    assert ('communicate',dict(timeout=30)) in child['calls']
    spawn=child['calls'][0]
    assert spawn[2]==dict(stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace')
    assert 'kill' not in child['calls']


@pytest.mark.parametrize('platform',['win32','linux'])
@pytest.mark.parametrize('poll_code',[None,0])
def test_timeout_keeps_exception_and_samples_before_cleanup(child,monkeypatch,capsys,platform,poll_code):
    monkeypatch.setattr(probe,'sys',SimpleNamespace(platform=platform))
    error=subprocess.TimeoutExpired('synthetic-command',30,output=b'private-value')
    child.update(timeout=True,timeout_error=error,poll_code=poll_code)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        probe.run_traced(['synthetic-command'],child['trace'])
    assert caught.value is error
    details=json.loads(capsys.readouterr().out.removeprefix('handoff_probe '))
    assert details['event']=='communicate_timeout' and details['cleanup_complete'] is False
    assert details['process_when_sampled']==('running' if poll_code is None else 'exited')
    assert details['last_stage']=='source_ready' and details['timeout_seconds']==30
    assert child['calls'].index('poll')<child['calls'].index('kill')
    assert child['calls'].count('kill')==1
    assert len([c for c in child['calls'] if type(c)is tuple and c[0]=='spawn'])==1
    if platform=='win32':assert ('communicate',{}) in child['calls']
    else:assert 'wait' in child['calls']


def test_interruption_still_cleans_owned_child_and_propagates(child):
    child['exception']=KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):probe.run_traced(['child'],child['trace'])
    assert 'kill' in child['calls'] and 'wait' in child['calls']


def test_real_process_keeps_payload_separate_from_stage_evidence(tmp_path,capsys):
    path=tmp_path/'trace'
    source='from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b"harness_enter|1|1\\nharness_end|2|1\\n"); print("synthetic-output")'
    run,details=probe.run_traced([sys.executable,'-I','-c',source,str(path)],path)
    assert run.returncode==0 and run.stdout.strip()=='synthetic-output'
    assert details['last_stage']=='harness_end'
    assert 'synthetic-output' not in capsys.readouterr().out


def test_real_timeout_retains_stage_file_without_rerun(tmp_path,capsys):
    path=tmp_path/'trace'
    source='from pathlib import Path; import sys,time; Path(sys.argv[1]).write_bytes(b"harness_enter|1|1\\n"); time.sleep(10)'
    with pytest.raises(subprocess.TimeoutExpired):
        probe.run_traced([sys.executable,'-I','-c',source,str(path)],path,timeout=1)
    details=json.loads(capsys.readouterr().out.removeprefix('handoff_probe '))
    assert details['event']=='communicate_timeout' and details['cleanup_complete'] is False
    assert details['process_when_sampled']=='running'
    # Startup may itself exceed one second under contention. Do not invent an
    # executed marker; mocked boundary cases check the exact completed-prefix case.
    assert details['trace_state'] in ('complete','missing','partial')
