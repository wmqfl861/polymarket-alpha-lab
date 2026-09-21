"""Exact-limit, nested-key and dual-cache counterexamples; synthetic data only.

Every rejection below is paired with a valid control at the same boundary, so a
"reject everything" decoder cannot pass by accident. All callbacks and payloads
are fictional; no provider, credential or database is involved.
"""
from hashlib import sha256
import json

import pytest

from polymarket_alpha_lab import research_codex_exec as codex
from polymarket_alpha_lab import research_claude_exec as claude
from polymarket_alpha_lab import research_dispatch_rotation as rotation
from polymarket_alpha_lab.research_process import ResearchProcessResult
from tests.test_research_claude_exec import MODEL, envelope, wire
from tests.test_research_codex_exec import action, events, output
from tests.test_research_dispatch_rotation import turn
from tests.test_research_uncapped import approval


def padded_messages(total_bytes):
    """An exact-byte ASCII messages_json list; only its size is interesting."""
    prefix, suffix = b'[{"padding":"', b'"}]'
    filler = total_bytes - len(prefix) - len(suffix)
    assert filler >= 0
    return (prefix + b'x' * filler + suffix).decode('ascii')


def raw_lines(rows):
    return ('\n'.join(json.dumps(e, ensure_ascii=False) for e in rows) + '\n').encode('utf-8')


@pytest.mark.parametrize('kind', ['codex', 'claude'])
def test_message_wire_byte_boundary_is_inclusive(kind):
    if kind == 'codex':
        factory, limit = codex.CodexExecInput, codex.MAX_MESSAGE_BYTES
    else:
        factory, limit = claude.ClaudeExecInput, claude.MAX_MESSAGE_BYTES
    exact = factory('model', padded_messages(limit), 10)
    assert len(exact.messages_json.encode('utf-8')) == limit
    assert json.loads(exact.prompt_json)['messages_json'] == exact.messages_json
    with pytest.raises(ValueError):
        factory('model', padded_messages(limit + 1), 10)


def test_event_line_count_boundary_is_inclusive():
    rows = events()
    rows[2:2] = [{'type': 'item.completed', 'item': {'id': f'r{i}', 'type': 'reasoning', 'text': ''}}
                 for i in range(codex.MAX_EVENT_LINES - len(rows))]
    assert len(rows) == codex.MAX_EVENT_LINES
    assert codex.decode_codex_exec_output(output(rows), call_number=1).total_tokens == 120
    rows.insert(3, {'type': 'item.completed', 'item': {'id': 'one-too-many', 'type': 'reasoning', 'text': ''}})
    assert len(rows) == codex.MAX_EVENT_LINES + 1
    with pytest.raises(ValueError, match='response_invalid'):
        codex.decode_codex_exec_output(output(rows), call_number=1)


def test_event_byte_boundary_is_inclusive():
    rows = events()
    rows[2]['item'] = {'id': 'item_0', 'type': 'reasoning', 'text': ''}
    base = len(raw_lines(rows))
    rows[2]['item']['text'] = 'x' * (codex.MAX_EVENT_BYTES - base)
    exact = raw_lines(rows)
    assert len(exact) == codex.MAX_EVENT_BYTES
    assert codex.decode_codex_exec_output(codex.CodexExecOutput(0, exact), call_number=1).total_tokens == 120
    with pytest.raises(ValueError):
        codex.CodexExecOutput(0, exact + b'x')


def test_reported_output_and_sequence_boundaries_are_inclusive():
    request = codex.CodexExecInput('model', '[{}]', 8192)
    assert request.max_output_tokens == 8192
    rows = events(output_tokens=8192)
    reply = codex.decode_codex_exec_output(output(rows), call_number=32, max_output_tokens=8192)
    assert reply.total_tokens == 8192 + 100


@pytest.mark.parametrize('code,stdout', [(True, b''), (0, ''), (0, None)])
def test_codex_output_exact_types(code, stdout):
    with pytest.raises(ValueError):
        codex.CodexExecOutput(code, stdout)


def test_codex_subset_equality_reaches_the_exact_total_ceiling():
    rows = events(input_tokens=999999, cached_input_tokens=999999, cache_write_input_tokens=999999,
                  output_tokens=1, reasoning_output_tokens=1)
    assert codex.decode_codex_exec_output(output(rows), call_number=1).total_tokens == 1000000


def test_claude_single_field_at_domain_max_is_exact_total():
    value = envelope()
    counts = dict(input_tokens=0, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=1000000)
    value['usage'] = dict(counts)
    value['modelUsage'][MODEL] = dict(zip(
        ('inputTokens', 'outputTokens', 'cacheCreationInputTokens', 'cacheReadInputTokens'), counts.values()))
    reply = claude.decode_claude_result(wire(value),
                                        request=claude.ClaudeExecInput(MODEL, '[{}]', 8192), call_number=1)
    assert reply.total_tokens == 1000000


@pytest.mark.parametrize('uncached,cached,emitted', [(100, 0, 20), (100, 100, 20), (90, 10, 20), (0, 999999, 1)])
def test_cache_conventions_are_duals_not_interchangeable(uncached, cached, emitted):
    """Premise: Codex 'input_tokens' INCLUDES cached tokens (subset rule), while
    Anthropic reports uncached input separately from cache reads (additive rule).
    Mapping uncapped = codex_input - cached, both decoders must bill one and the
    same total; a decoder that mixes the two conventions double-counts or drops."""
    rows = events(input_tokens=uncached + cached, cached_input_tokens=cached,
                  cache_write_input_tokens=0, output_tokens=emitted, reasoning_output_tokens=0)
    codex_total = codex.decode_codex_exec_output(output(rows), call_number=1).total_tokens
    value = envelope()
    counts = dict(input_tokens=uncached, output_tokens=emitted,
                  cache_creation_input_tokens=0, cache_read_input_tokens=cached)
    value['usage'] = dict(counts)
    value['modelUsage'][MODEL] = dict(zip(
        ('inputTokens', 'outputTokens', 'cacheCreationInputTokens', 'cacheReadInputTokens'), counts.values()))
    claude_total = claude.decode_claude_result(wire(value),
                                               request=claude.ClaudeExecInput(MODEL, '[{}]', 8192), call_number=1).total_tokens
    assert codex_total == claude_total == uncached + cached + emitted


def test_codex_duplicate_keys_inside_usage_object():
    rows = events()
    lines = [json.dumps(e, ensure_ascii=False) for e in rows]
    lines[-1] = ('{"type": "turn.completed", "usage": {"input_tokens": 100, "input_tokens": 100, '
                 '"cached_input_tokens": 10, "cache_write_input_tokens": 0, "output_tokens": 20, '
                 '"reasoning_output_tokens": 5}}')
    with pytest.raises(ValueError, match='response_invalid'):
        codex.decode_codex_exec_output(codex.CodexExecOutput(0, ('\n'.join(lines) + '\n').encode('utf-8')),
                                       call_number=1)


@pytest.mark.parametrize('needle', ['"input_tokens":3', '"inputTokens":3'])
def test_claude_duplicate_keys_inside_usage_objects(needle):
    raw = json.dumps(envelope(), ensure_ascii=True, separators=(',', ':'))
    raw = raw.replace(needle, needle + ',' + needle, 1)
    with pytest.raises(ValueError, match='response_invalid'):
        claude.decode_claude_result(ResearchProcessResult(raw.encode('utf-8'), 0, 1),
                                    request=claude.ClaudeExecInput(MODEL, '[{}]', 100), call_number=1)


def test_duplicate_keys_inside_action_arguments_are_rejected():
    duplicated = {'name': 'search_evidence', 'arguments_json': '{"query":"a","query":"b"}'}
    with pytest.raises(ValueError, match='response_invalid'):
        codex.decode_codex_exec_output(output(events([duplicated])), call_number=1)
    with pytest.raises(ValueError, match='response_invalid'):
        claude.decode_claude_result(wire(envelope(calls=[duplicated])),
                                    request=claude.ClaudeExecInput(MODEL, '[{}]', 100), call_number=1)


def test_deeply_nested_duplicate_message_keys_are_rejected():
    with pytest.raises(ValueError):
        codex.CodexExecInput('model', '[{"a":{"b":[1,{"c":1,"c":2}]}}]', 10)
    with pytest.raises(ValueError):
        claude.ClaudeExecInput(MODEL, '[{"a":{"b":[1,{"c":1,"c":2}]}}]', 10)


@pytest.mark.parametrize('messages', [r'[{"\ud800":"value"}]', r'[{"deep":{"list":["\udfff"]}}]',
                                     r'[[[{"k":"\ud800"}]]]'])
def test_codex_escaped_surrogates_at_nested_depths_are_rejected(messages):
    with pytest.raises(ValueError):
        codex.CodexExecInput('model', messages, 10)


def test_codex_valid_nested_unicode_is_preserved_verbatim():
    messages = '[{"role":"用户","content":"中文 😀","nested":{"键":["值"]}}]'
    request = codex.CodexExecInput('model', messages, 10)
    assert json.loads(request.prompt_json)['messages_json'] == messages


def test_codex_unicode_action_text_round_trip_and_reasoning_surrogate():
    rows = events([action('search_evidence', {'query': '中文 😀'})])
    reply = codex.decode_codex_exec_output(output(rows), call_number=1)
    assert reply.calls[0].arguments_json == '{"query":"中文 😀"}'
    lines = [json.dumps(e, ensure_ascii=False) for e in events()]
    lines[2] = ('{"type": "item.completed", "item": {"id": "item_0", "type": "reasoning", '
                '"text": "\\ud800"}}')
    with pytest.raises(ValueError, match='response_invalid'):
        codex.decode_codex_exec_output(codex.CodexExecOutput(0, ('\n'.join(lines) + '\n').encode('utf-8')),
                                       call_number=1)


def test_claude_result_at_exact_wire_cap_is_still_valid():
    raw = wire().stdout
    padded = raw + b' ' * (claude.MAX_RESULT_BYTES - len(raw))
    assert len(padded) == claude.MAX_RESULT_BYTES
    reply = claude.decode_claude_result(ResearchProcessResult(padded, 0, 1),
                                        request=claude.ClaudeExecInput(MODEL, '[{}]', 100), call_number=1)
    assert reply.total_tokens == 26
    with pytest.raises(ValueError):
        ResearchProcessResult(padded + b' ', 0, 1)


def test_rotation_duplicate_key_and_surrogate_payloads_fail_closed():
    reference = turn().payload
    duplicated = '{"rotation_id":"rotation",' + reference[1:]
    with pytest.raises(ValueError, match='payload_invalid'):
        rotation.decode_turn(duplicated, sha256(duplicated.encode()).hexdigest())
    data = json.loads(reference)
    data['turn_id'] = 't\ud800'
    surrogate = json.dumps(data, sort_keys=True, separators=(',', ':'))
    with pytest.raises(ValueError, match='payload_invalid'):
        rotation.decode_turn(surrogate, sha256(surrogate.encode()).hexdigest())


def test_uncapped_request_member_arity_boundary_fails_closed():
    data = json.loads(approval().payload)
    data['request_keys'][0] = ['r0', 'a' * 64, 'extra-member']
    raw = json.dumps(data, sort_keys=True, separators=(',', ':'))
    with pytest.raises(ValueError, match='payload_invalid'):
        from polymarket_alpha_lab import research_uncapped
        research_uncapped.decode_authorization(raw, sha256(raw.encode()).hexdigest())
