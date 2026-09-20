"""Synthetic evidence-harness tests, never an official-CLI success claim."""
from dataclasses import replace
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
import sys

import pytest

from polymarket_alpha_lab import research_process as core
from tests import claude_cli_probe as probe
from tests.test_research_claude_exec import envelope


def image():
    path = Path(sys.executable).resolve()
    return str(path), sha256(path.read_bytes()).hexdigest(), path.stat().st_size


def simulated_runner(events, *, version=None, fault=None):
    def run(*, spec, stdin, allow_process_start, **kwargs):
        assert allow_process_start is True
        assert set(dict(spec.environment)).isdisjoint({'PATH', 'HTTP_PROXY', 'HTTPS_PROXY'})
        if spec.argv[1:] == ('--version',):
            events.append('version')
            return core.ResearchProcessResult((version or probe.VERSION_OUTPUT).encode(), 0, 1)
        events.append('message')
        env = dict(spec.environment)
        assert env['ANTHROPIC_API_KEY'] == probe.TEST_KEY
        assert env['ANTHROPIC_BASE_URL'].startswith('http://127.0.0.1:')
        assert '--no-session-persistence' in spec.argv and '--restricted' in spec.argv
        assert stdin.decode() == probe.test_input().prompt_json
        address = env['ANTHROPIC_BASE_URL'].removeprefix('http://')
        conn = http.client.HTTPConnection(address, timeout=2)
        body = {'model': probe.MODEL_ID, 'max_tokens': 1024, 'stream': True,
                'tools': [], 'messages': [{'role': 'user', 'content': stdin.decode()}]}
        if fault == 'context':
            body['system'] = probe.UNRELATED
        if fault == 'tools':
            body['tools'] = [{'name': 'Read'}]
        if fault == 'output_cap':
            body['max_tokens'] = 1025
        if fault == 'key_body':
            body['system'] = probe.TEST_KEY
        headers = {'Content-Type': 'application/json', 'x-api-key': probe.TEST_KEY}
        if fault == 'auth': headers['x-api-key'] = 'NOT-THE-TEST-KEY'
        for _ in range(2 if fault == 'retry' else 1):
            conn.request('POST', '/v1/messages?beta=true', json.dumps(body), headers)
            response = conn.getresponse(); raw = response.read(); status = response.status
        conn.close()
        if fault == 'write': (Path(spec.cwd)/'new-state.sqlite').write_bytes(b'SQLite format 3\x00')
        if fault == 'persist_prompt': (Path(env['HOME'])/'transcript').write_bytes(stdin)
        if fault == 'persist_response': (Path(env['CLAUDE_CONFIG_DIR'])/'response').write_text(probe.response_text('success'))
        if status != 200: raise core.ResearchProcessError('research_process_nonzero_exit')
        if b'"name": "Read"' in raw or raw.endswith(b'event: message_delta\n'):
            raise core.ResearchProcessError('research_process_nonzero_exit')
        value = envelope()
        if b'{invalid' in raw: value['result'] = '{invalid'
        else: value['result'] = probe.response_text('success')
        return core.ResearchProcessResult(json.dumps(value).encode(), 0, 1)
    return run


@pytest.mark.parametrize('mode', probe.MODES)
def test_each_scenario_observes_actual_mock_request_and_fixed_response(tmp_path, mode):
    events = []
    result = probe.run_probe(*image(), root=tmp_path/'probe', mode=mode,
                             allow_probe=True, process_runner=simulated_runner(events))
    assert events == ['version', 'message']
    assert result['request_count'] == 1 and result['request_contract_matches'] is True
    assert result['version_matches'] is True and result['state']['changed_entries'] == 0
    assert probe.observation_passed(result) is True
    assert result['activation_authorized'] is False
    assert result['outside_root_verified'] is result['external_egress_verified'] is False
    assert probe.TEST_KEY not in json.dumps(result) and probe.APPROVED not in json.dumps(result)


@pytest.mark.parametrize('fault', ['context', 'tools', 'output_cap', 'key_body', 'auth', 'retry',
                                    'write', 'persist_prompt', 'persist_response'])
def test_bad_behavior_cannot_pass_even_with_a_success_result(tmp_path, fault):
    result = probe.run_probe(*image(), root=tmp_path/'probe', mode='success',
        allow_probe=True, process_runner=simulated_runner([], fault=fault))
    assert probe.observation_passed(result) is False
    text = json.dumps(result)
    assert probe.TEST_KEY not in text and probe.UNRELATED not in text and str(tmp_path) not in text


def test_wrong_version_stops_before_message(tmp_path):
    events = []
    result = probe.run_probe(*image(), root=tmp_path/'probe', mode='success', allow_probe=True,
        process_runner=simulated_runner(events, version='2.1.277 (Claude Code)\n'))
    assert events == ['version'] and result['request_count'] == 0
    assert result['version_matches'] is False and probe.observation_passed(result) is False


def test_missing_optin_creates_nothing_and_starts_nothing(tmp_path):
    with pytest.raises(ValueError, match='probe_opt_in_required'):
        probe.run_probe(*image(), root=tmp_path/'probe', mode='success',
            process_runner=lambda **_: pytest.fail('launched'))
    assert list(tmp_path.iterdir()) == []


def test_existing_root_is_never_scanned_or_overwritten(tmp_path):
    root = tmp_path/'existing'; root.mkdir(); private = root/'private'; private.write_text('keep')
    with pytest.raises(ValueError, match='probe_root_unavailable'):
        probe.run_probe(*image(), root=root, mode='success', allow_probe=True,
            process_runner=lambda **_: pytest.fail('launched'))
    assert private.read_text() == 'keep' and list(root.iterdir()) == [private]


@pytest.mark.parametrize('value', ['../relative', '', '0'*63, 'g'*64])
def test_bad_digest_cannot_enter_runner(tmp_path, value):
    path, _, size = image()
    with pytest.raises(ValueError, match='probe_image_invalid'):
        probe.run_probe(path, value, size, root=tmp_path/'probe', mode='success', allow_probe=True,
            process_runner=lambda **_: pytest.fail('launched'))
    assert list(tmp_path.iterdir()) == []


def test_environment_optin_absent_is_inert():
    assert probe.configured_image({}) is None


@pytest.mark.parametrize('env', [
    {'POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_IMAGE': '/anything'},
    {'POLYMARKET_ALPHA_LAB_CLAUDE_PROBE': '1'},
    {'POLYMARKET_ALPHA_LAB_CLAUDE_PROBE': 'true'},
])
def test_partial_or_malformed_optin_is_failure_not_silent_skip(env):
    with pytest.raises(ValueError, match='probe_configuration_invalid'):
        probe.configured_image(env)


def test_snapshot_detects_changed_same_size_content(tmp_path):
    root = tmp_path/'state'; root.mkdir(); path = root/'file'; path.write_text('aaaa')
    before = probe.state_snapshot(root)
    path.write_text('bbbb')
    after = probe.state_snapshot(root)
    assert probe.state_delta(before, after)['changed_entries'] == 1


def test_snapshot_does_not_follow_links_outside_root(tmp_path):
    root = tmp_path/'state'; root.mkdir()
    outside = tmp_path/'outside'; outside.write_text(probe.TEST_KEY)
    try: (root/'link').symlink_to(outside)
    except OSError: pytest.skip('symlink privilege unavailable')
    result = probe.state_snapshot(root)
    assert result['complete'] is False and result['unsafe_entries'] == 1
    assert result['sentinel_files'] == 0


def test_snapshot_large_file_is_incomplete_not_no_write(tmp_path):
    root = tmp_path/'state'; root.mkdir(); (root/'large').write_bytes(b'x'*(probe.MAX_FILE_BYTES+1))
    result = probe.state_snapshot(root)
    assert result['complete'] is False and result['limit_reached'] is True


def test_interrupt_is_preserved_and_server_stopped(tmp_path):
    original = KeyboardInterrupt()
    def run(**_): raise original
    with pytest.raises(KeyboardInterrupt) as caught:
        probe.run_probe(*image(), root=tmp_path/'probe', mode='success', allow_probe=True,process_runner=run)
    assert caught.value is original


# This child is deliberately a stdlib Python stand-in, not an official binary.
# It exercises the real supervisor, sockets, SSE and original result decoder.
_STAND_IN = r'''
import http.client, json, os, sys
if '--version' in sys.argv:
    sys.stdout.write('2.1.278 (Claude Code)\n')
    raise SystemExit(0)
prompt = sys.stdin.buffer.read().decode('utf-8')
url = os.environ['ANTHROPIC_BASE_URL']
assert url.startswith('http://127.0.0.1:')
connection = http.client.HTTPConnection(url.removeprefix('http://'), timeout=2)
body = {'model':'claude-opus-5', 'max_tokens':1024, 'stream':True, 'tools':[],
        'messages':[{'role':'user','content':prompt}]}
connection.request('POST','/v1/messages',json.dumps(body),
    {'Content-Type':'application/json','x-api-key':os.environ['ANTHROPIC_API_KEY']})
reply = connection.getresponse(); data=reply.read(); connection.close()
if reply.status != 200: raise SystemExit(3)
events = [json.loads(line[6:]) for line in data.decode().splitlines() if line.startswith('data: ')]
if events[-1]['type'] != 'message_stop': raise SystemExit(4)
if any(e.get('content_block',{}).get('type')=='tool_use' for e in events): raise SystemExit(5)
text = ''.join(e['delta'].get('text','') for e in events if e['type']=='content_block_delta')
output = dict(type='result',subtype='success',is_error=False,num_turns=1,
    session_id='11111111-1111-4111-8111-111111111111',duration_ms=4,duration_api_ms=3,
    stop_reason='end_turn',result=text,permission_denials=[],
    usage=dict(input_tokens=3,output_tokens=5,cache_creation_input_tokens=7,cache_read_input_tokens=11),
    modelUsage={'claude-opus-5':dict(inputTokens=3,outputTokens=5,cacheCreationInputTokens=7,cacheReadInputTokens=11)})
print(json.dumps(output))
'''


@pytest.mark.parametrize('mode', probe.MODES)
def test_real_synthetic_processes_exercise_probe_without_official_cli(tmp_path, mode):
    launched = []
    def run(**kwargs):
        spec = kwargs['spec']
        launched.append(spec.argv[1:])
        kwargs['spec'] = replace(spec, argv=(spec.argv[0], '-I', '-S', '-c', _STAND_IN, *spec.argv[1:]))
        return core.run_research_process(**kwargs)
    result = probe.run_probe(*image(), root=tmp_path/'probe', mode=mode,
                             allow_probe=True, process_runner=run)
    assert len(launched) == 2 and launched[0] == ('--version',)
    assert probe.observation_passed(result) is True, result
    assert result['vendor_provenance_verified'] is False
