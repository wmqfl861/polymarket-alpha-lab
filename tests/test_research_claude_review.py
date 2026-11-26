"""Separate adversarial review: precise refusal before secrets or result acceptance."""
from dataclasses import replace
import json

import pytest

from polymarket_alpha_lab import research_claude_exec as cli
from polymarket_alpha_lab import research_claude_profile as profile
from polymarket_alpha_lab import research_process as process
from polymarket_alpha_lab.research_dispatch_runner import ResearchDispatchStop
from tests.test_research_claude_exec import MODEL, wire
from tests.test_research_claude_profile import candidate, bound, SENTINEL


@pytest.mark.parametrize('stderr_bytes', [1, 17, 65536])
def test_unclassified_stderr_cannot_certify_a_clean_single_result(stderr_bytes):
    result = replace(wire(),stderr_bytes=stderr_bytes)
    with pytest.raises(ValueError,match='response_invalid'):
        cli.decode_claude_result(result,request=cli.ClaudeExecInput(MODEL,'[{}]',100),call_number=1)


@pytest.mark.parametrize('limit', [1,100])
def test_known_payload_limit_failure_precedes_credential_supplier(monkeypatch,tmp_path,limit):
    p = candidate(tmp_path)
    p = replace(p,process=replace(p.process,max_stdin_bytes=limit))
    entered=[]
    def key(): entered.append('key'); return SENTINEL
    def runner(**kwargs):
        entered.append('runner')
        if len(kwargs['stdin']) > kwargs['spec'].max_stdin_bytes:
            raise ValueError('oversize input')
        return wire()
    monkeypatch.setattr(cli,'run_research_process',runner)
    model = bound(p,api_key_supplier=key)('crypto_btc')
    with pytest.raises(ValueError): model.complete(messages_json='[{}]',max_output_tokens=100)
    assert entered==[]


def test_profile_digest_includes_original_prompt_and_action_protocol(monkeypatch,tmp_path):
    p = candidate(tmp_path); original=p.contract_sha256
    monkeypatch.setattr(cli.ClaudeExecInput,'prompt_json',property(lambda self:'changed protocol'))
    assert p.contract_sha256 != original


def test_stop_during_explicit_supplier_never_starts_image(monkeypatch,tmp_path):
    p=candidate(tmp_path);stop=ResearchDispatchStop();calls=[]
    def key():calls.append(1);stop.request_stop();return SENTINEL
    monkeypatch.setattr(process,'_spawn',lambda *a:pytest.fail('stopped launch'))
    model=bound(p,stop=stop,api_key_supplier=key)('crypto_btc')
    with pytest.raises(ValueError):model.complete(messages_json='[{}]',max_output_tokens=100)
    assert calls==[1]


@pytest.mark.parametrize('field,value', [('input_tokens',True),('output_tokens',False),
                                        ('cache_read_input_tokens',2.0),('cache_creation_input_tokens',-1)])
def test_model_usage_cannot_hide_wrong_scalar_type_by_equal_value(field,value):
    from tests.test_research_claude_exec import envelope
    raw=envelope()
    names=dict(zip(cli._USAGE,cli._MODEL_USAGE,strict=True))
    raw['usage'][field]=value
    raw['modelUsage'][MODEL][names[field]]=value
    with pytest.raises(ValueError):
        cli.decode_claude_result(wire(raw),request=cli.ClaudeExecInput(MODEL,'[{}]',100),call_number=1)


def test_two_factory_clients_never_duplicate_a_shared_supplier_slot(monkeypatch,tmp_path):
    """Adversarial regression: both team clients ride ONE supplier; concurrent
    admissions consume distinct permanent slots and the stop cannot double-kill."""
    from concurrent.futures import ThreadPoolExecutor
    from polymarket_alpha_lab.research_claude_profile import FiniteInMemoryApiKeySupplier
    from tests.test_research_process_linux import fixed_launch
    stop=ResearchDispatchStop()
    supplier=FiniteInMemoryApiKeySupplier(stop=stop,
        crypto_btc=tuple('BTC-%d'%n for n in range(8)),
        crypto_eth=tuple('ETH-%d'%n for n in range(8)))
    process=replace(candidate(tmp_path).process,argv=(candidate(tmp_path).process.argv[0],))
    p=profile.ClaudeExecProfile(process=process,endpoint_url='https://gateway.example.invalid',
                                linux_launch=fixed_launch())
    factory=bound(p,api_key_supplier=supplier,stop=stop)
    seen=[]
    def runner(**kwargs):
        seen.append(dict(kwargs['spec'].environment)['ANTHROPIC_API_KEY'])
        return wire()
    monkeypatch.setattr(cli,'run_research_process',runner)
    def drain(team):
        model=factory(team)
        for _ in range(8):
            model.complete(messages_json='[{}]',max_output_tokens=100)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pool.submit(drain,'crypto_btc');pool.submit(drain,'crypto_eth')
    assert len(seen)==16 and len(set(seen))==16
    assert supplier.remaining('crypto_btc')==0==supplier.remaining('crypto_eth')
    stop.request_stop()
    with pytest.raises(ValueError,match='call_failed'):
        factory('crypto_btc').complete(messages_json='[{}]',max_output_tokens=100)
    assert supplier.remaining('crypto_btc')==0


def test_changed_prompt_contract_after_binding_never_enters_key_supplier(monkeypatch,tmp_path):
    p=candidate(tmp_path)
    model=bound(p,api_key_supplier=lambda:pytest.fail('supplier after contract drift'))('crypto_btc')
    monkeypatch.setattr(cli.ClaudeExecInput,'prompt_json',property(lambda self:'changed contract'))
    with pytest.raises(ValueError):model.complete(messages_json='[{}]',max_output_tokens=100)


def test_simultaneous_calls_have_unique_ids_and_never_exceed_client_limit(monkeypatch,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    p=candidate(tmp_path);entered=[]
    def run(**kwargs):entered.append(1);return wire()
    monkeypatch.setattr(cli,'run_research_process',run)
    model=bound(p)('crypto_eth')
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies=tuple(pool.map(lambda _:model.complete(messages_json='[{}]',max_output_tokens=100),range(32)))
    assert {r.calls[0].call_id for r in replies}=={f'claude-{n}-0' for n in range(1,33)}
    with pytest.raises(ValueError):model.complete(messages_json='[{}]',max_output_tokens=100)
    with pytest.raises(ValueError,match='stopped'):model.complete(messages_json='[{}]',max_output_tokens=100)
    assert len(entered)==32


@pytest.mark.parametrize('stage', ['prompt', 'profile_digest'])
@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
def test_stop_during_preparation_blocks_credential_callback(monkeypatch, tmp_path, stage, team):
    """A stop arriving after the model's first check still precedes key access."""
    p = candidate(tmp_path)
    stop = ResearchDispatchStop()
    entered = []
    def key():
        entered.append('key')
        return SENTINEL
    model = bound(p, stop=stop, api_key_supplier=key)(team)
    owner, name = ((cli.ClaudeExecInput, 'prompt_json') if stage == 'prompt'
                   else (profile.ClaudeExecProfile, 'contract_sha256'))
    original = getattr(owner, name).fget
    def interrupted_preparation(value):
        result = original(value)
        stop.request_stop()
        return result  # No change to approved input or configuration bytes.
    monkeypatch.setattr(owner, name, property(interrupted_preparation))
    monkeypatch.setattr(process, '_spawn', lambda _: pytest.fail('stopped process launched'))
    with pytest.raises(ValueError, match='call_failed'):
        model.complete(messages_json='[{"content":"approved 中文"}]', max_output_tokens=100)
    assert stop.is_stopped() and entered == []
    with pytest.raises(ValueError, match='stopped'):
        model.complete(messages_json='[{}]', max_output_tokens=100)
    assert entered == []


@pytest.mark.parametrize('number', [0, 1])
def test_preparation_stop_preserves_audit_and_original_replay(monkeypatch, tmp_path, number):
    from tests.test_research_claude_profile import permission
    from tests.test_research_uncapped_audit import AuditHarness, req, run
    h = AuditHarness(monkeypatch)
    request = replace(req(number), model_id=MODEL)
    p = candidate(tmp_path)
    authorization = permission(p, request)
    stop = ResearchDispatchStop()
    entered = []
    def supplier():
        entered.append('key')
        return SENTINEL
    factory = bound(p, authorization, api_key_supplier=supplier, stop=stop)
    original = cli.ClaudeExecInput.prompt_json.fget
    def preparation(value):
        result = original(value)
        stop.request_stop()
        return result
    monkeypatch.setattr(cli.ClaudeExecInput, 'prompt_json', property(preparation))
    monkeypatch.setattr(process, '_spawn', lambda _: pytest.fail('stopped process launched'))
    result = run(h, request=request, authorization=authorization, model_factory=factory,
                 stop=stop, require_durable_audit=True)
    assert entered == []
    assert h.events == ['authorization', 'claim', 'start_commit', 'outcome_failed', 'capture']
    assert len(h.starts) == len(h.outcomes) == 1
    assert h.outcomes[0].status == 'failed'
    assert h.outcomes[0].reported_total_tokens is h.outcomes[0].reply_sha256 is None
    assert result.record.run.research.reason_code == 'model_failed'
    assert result.request.content_sha256 == request.content_sha256
    h.events.clear()
    replay = run(h, request=request, authorization=authorization,
        model_factory=lambda _: pytest.fail('replayed factory'), require_durable_audit=True)
    assert replay == result and h.events == ['authorization', 'replay']
    assert entered == [] and len(h.starts) == len(h.outcomes) == 1


@pytest.mark.parametrize('team', ['crypto_btc', 'crypto_eth'])
def test_unstopped_preparation_enters_supplier_once(monkeypatch, tmp_path, team):
    stop = ResearchDispatchStop()
    entered = []
    def supplier():
        entered.append('key')
        return SENTINEL
    def runner(**kwargs):
        entered.append('process')
        assert dict(kwargs['spec'].environment)['ANTHROPIC_API_KEY'] == SENTINEL
        assert SENTINEL not in kwargs['stdin'].decode('utf-8')
        assert kwargs['stop'] is stop
        return wire()
    monkeypatch.setattr(cli, 'run_research_process', runner)
    model = bound(candidate(tmp_path), stop=stop, api_key_supplier=supplier)(team)
    assert model.complete(messages_json='[{}]', max_output_tokens=100).total_tokens == 26
    assert entered == ['key', 'process'] and not stop.is_stopped()
