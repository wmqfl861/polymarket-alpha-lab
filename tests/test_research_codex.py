"""Pinned protocol and process boundaries; synthetic values, no provider login."""
from dataclasses import asdict, replace
from hashlib import sha256
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

import pytest

from polymarket_alpha_lab import research_codex_local as local
from polymarket_alpha_lab import research_codex_protocol as wire

SENTINEL = 'SYNTHETIC-PRIVATE-SENTINEL-NOT-A-KEY'


def events(action=None, usage=None):
    return [{'type': 'thread.started', 'thread_id': 'synthetic-thread'},
        {'type': 'turn.started'},
        {'type': 'item.completed', 'item': {'id': 'item0', 'type': 'agent_message',
            'text': json.dumps({'calls': [action or {'name': 'search_evidence', 'arguments_json': '{"query":"*"}'}]})}},
        {'type': 'turn.completed', 'usage': usage or {
            'input_tokens': 100, 'cached_input_tokens': 20, 'cache_write_input_tokens': 10,
            'output_tokens': 30, 'reasoning_output_tokens': 10}}]


def encoded(rows=None):
    return ('\n'.join(json.dumps(e) for e in (events() if rows is None else rows)) + '\n').encode()


def parse(raw):
    return wire.parse_output(raw, call_number=1, max_output_tokens=100)


def profile(tmp_path):
    exe = tmp_path / 'codex.exe'
    exe.write_bytes(b'SYNTHETIC-INERT-EXECUTABLE')
    home, work = tmp_path / 'auth', tmp_path / 'scratch'
    home.mkdir(mode=0o700); work.mkdir(mode=0o700)
    return local.CodexLocalProfile(exe, sha256(exe.read_bytes()).hexdigest(), home, work, 'synthetic-model')


def test_usage_subsets_not_counted_twice():
    value = parse(encoded())
    assert value.reply.total_tokens == value.usage.total_tokens == 130
    assert value.reply.calls[0].call_id == 'codex-1-0'
    assert value.output_sha256 == sha256(encoded()).hexdigest()
    assert wire.parse_output(encoded(), call_number=2, max_output_tokens=100).reply.calls[0].call_id == 'codex-2-0'


def test_native_reasoning_discarded():
    rows = events()
    rows.insert(2, {'type': 'item.completed', 'item': {'id': 'reason', 'type': 'reasoning', 'text': SENTINEL}})
    value = parse(encoded(rows))
    assert SENTINEL not in repr(value)
    assert value.reply.total_tokens == 130


@pytest.mark.parametrize('field', ['input_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_tokens', 'reasoning_output_tokens'])
@pytest.mark.parametrize('bad', [True, -1, 1000001, '1', 1.2, None])
def test_usage_values_strict(field, bad):
    rows = events(); rows[-1]['usage'][field] = bad
    with pytest.raises(ValueError, match='research_codex_output_invalid'):
        parse(encoded(rows))


@pytest.mark.parametrize('edit', [
    lambda e: e.append(e[-1]),
    lambda e: e.insert(2, {'type': 'turn.started'}),
    lambda e: e.pop(),
    lambda e: e[0].update(extra=SENTINEL),
    lambda e: e[-1].update(type='turn.failed'),
    lambda e: e[-1]['usage'].update(cached_input_tokens=101),
    lambda e: e[-1]['usage'].update(reasoning_output_tokens=31),
    lambda e: e[-1]['usage'].update(output_tokens=101),
    lambda e: e[2]['item'].update(type='command_execution'),
    lambda e: e[2]['item'].update(type='mcp_tool_call'),
    lambda e: e[2]['item'].update(text='{bad '+SENTINEL),
    lambda e: e[2]['item'].update(text='{"calls":[],"calls":[]}'),
    lambda e: e[2]['item'].update(text='{"calls":[]}'),
    lambda e: e[2]['item'].update(text='{"calls":[],"usage":0}'),
    lambda e: e.insert(2, e[2]),
    lambda e: e[-1]['usage'].update(unknown=1),
])
def test_invalid_and_partial_streams_fail_without_echo(edit):
    rows = events(); edit(rows)
    with pytest.raises(ValueError) as error:
        parse(encoded(rows))
    assert SENTINEL not in ''.join(traceback.format_exception(error.value))


@pytest.mark.parametrize('action', [
    {'name': 'shell', 'arguments_json': '{}'},
    {'name': 'search_evidence', 'arguments_json': '{"query":"*","extra":true}'},
    {'name': 'search_evidence', 'arguments_json': '{"query":"*"}', 'call_id': 'external'},
    {'name': 'finish_research', 'arguments_json': '{"probability_yes":0.5}'},
])
def test_closed_action_schema_reused(action):
    with pytest.raises(ValueError):
        parse(encoded(events(action)))


@pytest.mark.parametrize('raw', [b'', b'\xff', b'x' * (wire.MAX_STDOUT_BYTES+1), encoded()+b'\n'])
def test_output_byte_and_line_limits(raw):
    with pytest.raises(ValueError):
        parse(raw)


def test_prompt_preserves_original_text_and_definitions():
    original = '[ { "role": "system", "content": "中文 ' + SENTINEL + '" } ]'
    prompt = json.loads(wire.build_prompt(original, 200))
    assert prompt['messages_json'] == original
    assert prompt['requested_output_tokens'] == 200
    assert [x['function']['name'] for x in prompt['allowed_actions']] == ['search_evidence', 'read_evidence', 'finish_research']
    assert wire.output_schema()['additionalProperties'] is False


@pytest.mark.parametrize('input', ['', '[]', '{}', '[1]', '[{"a":NaN}]', '[{"a":1,"a":2}]', 1, 'x'*2000001])
def test_bad_prompt(input):
    with pytest.raises(ValueError, match='research_codex_input_invalid'):
        wire.build_prompt(input, 200)


def test_profile_is_inert_and_private_in_repr(tmp_path):
    p = profile(tmp_path)
    assert str(p.codex_home) not in repr(p)
    assert replace(p).content_sha256 == p.content_sha256
    assert replace(p, model_id='other').content_sha256 != p.content_sha256
    for options in ({'timeout_seconds': True}, {'timeout_seconds': 0}, {'timeout_seconds': 601},
                    {'model_id': '--inject'}, {'executable': Path('relative')},
                    {'workspace_parent': p.codex_home}, {'codex_home': p.workspace_parent/'nested'}):
        with pytest.raises(ValueError):
            replace(p, **options)


def test_profile_read_explicit_file_and_no_secrets(tmp_path):
    p = profile(tmp_path)
    data = asdict(p)
    for key in ('executable', 'codex_home', 'workspace_parent'):
        data[key] = str(data[key])
    target = tmp_path/'profile.json'; target.write_text(json.dumps(data))
    assert local.read_profile(target) == p
    data['token'] = SENTINEL; target.write_text(json.dumps(data))
    with pytest.raises(ValueError) as e:
        local.read_profile(target)
    assert SENTINEL not in ''.join(traceback.format_exception(e.value))
    with pytest.raises(ValueError):
        local.read_profile(tmp_path)


def test_invocation_is_pinned_isolated_and_not_implicitly_retried(tmp_path, monkeypatch):
    p = profile(tmp_path); seen = []
    monkeypatch.setenv('OPENAI_API_KEY', SENTINEL)
    monkeypatch.setenv('HTTPS_PROXY', SENTINEL)
    monkeypatch.setattr(local, 'private_directory', lambda path: None)
    def fake(args, **kw):
        seen.append((args, kw))
        assert SENTINEL not in repr(kw)
        assert kw['env']['CODEX_HOME'] == str(p.codex_home)
        assert Path(kw['cwd']).parent.parent == p.workspace_parent
        if args[-1] == '--version':
            return b'codex-cli 0.155.1\n'
        assert '--ephemeral' in args and '--ignore-user-config' in args and '--strict-config' in args
        assert '--dangerously-bypass-approvals-and-sandbox' not in args
        assert '--ignore-rules' not in args
        assert 'features.shell_tool=false' in args and 'features.view_image=false' in args
        assert 'model_providers.pal_codex.request_max_retries=0' in args
        assert json.loads(kw['data'])['messages_json'] == '[{"role":"user","content":"test"}]'
        catalog = json.loads((Path(kw['cwd']).parent/'models.json').read_text())
        assert catalog['models'][0]['shell_type'] == 'disabled'
        assert catalog['models'][0]['experimental_supported_tools'] == []
        return encoded()
    monkeypatch.setattr(local, '_run', fake)
    value = local.invoke_codex(p, messages_json='[{"role":"user","content":"test"}]', max_output_tokens=100, call_number=1)
    assert value.reply.total_tokens == 130 and len(seen) == 2
    assert list(p.workspace_parent.iterdir()) == []
    assert list(p.codex_home.iterdir()) == []


@pytest.mark.parametrize('mode', ['version', 'failure', 'invalid', 'hash', 'ordinal'])
def test_invocation_fails_closed_no_second_exec(tmp_path, monkeypatch, mode):
    p = profile(tmp_path); seen = []
    monkeypatch.setattr(local, 'private_directory', lambda path: None)
    if mode == 'hash':
        p.executable.write_bytes(b'changed')
    def fake(args, **kw):
        seen.append(args)
        if args[-1] == '--version':
            return b'codex-cli unknown' if mode == 'version' else b'codex-cli 0.155.1'
        if mode == 'failure':
            raise RuntimeError(SENTINEL)
        return SENTINEL.encode()
    monkeypatch.setattr(local, '_run', fake)
    with pytest.raises(ValueError) as e:
        local.invoke_codex(p, messages_json='[{"role":"user","content":"test"}]', max_output_tokens=100,
                           call_number=0 if mode == 'ordinal' else 1)
    assert SENTINEL not in ''.join(traceback.format_exception(e.value))
    assert len(seen) == (0 if mode in ('hash','ordinal') else 1 if mode=='version' else 2)
    assert list(p.workspace_parent.iterdir()) == []


def process(code, tmp_path, **kwargs):
    return local._run([sys.executable, '-I', '-c', code], data=b'synthetic input',
                      env=local._environment(profile(tmp_path), tmp_path), cwd=tmp_path, **kwargs)


def test_real_process_roundtrip(tmp_path):
    out = process('import sys;print(sys.stdin.buffer.read().decode())', tmp_path, timeout=5)
    assert out.strip() == b'synthetic input'


@pytest.mark.parametrize('code', [
    'import time;time.sleep(20)',
    'import sys;sys.stderr.write("x"*70000);sys.stderr.flush()',
    'import sys;sys.stdout.write("x"*20000);sys.stdout.flush()',
    'import sys;sys.exit(7)',
])
def test_real_process_deadline_and_output_bounds(tmp_path, code):
    start = time.monotonic()
    with pytest.raises((ValueError, TimeoutError, BrokenPipeError, ConnectionResetError)):
        process(code, tmp_path, timeout=0.4, stdout_limit=10000)
    assert time.monotonic() - start < 8


def test_descendant_pipe_holder_is_cleaned(tmp_path):
    marker = tmp_path/'descendant-survived'
    child = 'import time,pathlib;time.sleep(2);pathlib.Path('+repr(str(marker))+').write_text("unexpected")'
    code = 'import subprocess,sys;subprocess.Popen([sys.executable,"-I","-c",'+repr(child)+'])'
    with pytest.raises((TimeoutError, ValueError, BrokenPipeError, ConnectionResetError)):
        process(code, tmp_path, timeout=0.4)
    time.sleep(2.1)
    assert not marker.exists()


def test_async_context_is_not_silently_nested():
    async def go():
        with pytest.raises(ValueError, match='sync_context'):
            local._run(['not-an-executable'])
    asyncio.run(go())
