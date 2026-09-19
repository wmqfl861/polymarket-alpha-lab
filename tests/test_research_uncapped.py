"""Explicit uncapped authorization and original claim/permit ordering, offline."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import traceback

import pytest

from polymarket_alpha_lab import research_uncapped_authorization as core
from polymarket_alpha_lab import research_uncapped_store as store
from polymarket_alpha_lab import research_uncapped_runner as runner
from polymarket_alpha_lab import research_dispatch_runner as drain
from polymarket_alpha_lab.research_codex_protocol import CodexReply, CodexUsage, VERSION
from tests.test_research_codex import profile, encoded, parse, SENTINEL
from tests.test_research_dispatch import Cursor
from tests.test_research_model_budget import req as old_req
from tests.test_research_execution import state, record_for, NOW, Model


def req(n=0, **kw):
    return old_req(n, protocol_version=VERSION, **kw)


def policy(p, requests=None, **kw):
    rs = (req(), req(1)) if requests is None else requests
    fields = dict(authorization_id='synthetic-uncapped', model_id=p.model_id,
        profile_sha256=p.content_sha256, expires_at=NOW+timedelta(hours=1),
        request_keys=tuple((r.record_id,r.content_sha256) for r in rs), uncapped_cost_approved=True)
    fields.update(kw)
    return core.UncappedModelAuthorization(**fields)


def row(p):
    return (p.authorization_id, p.payload, p.content_sha256, NOW, True, True, True)


def snap(p):
    return core.UncappedModelSnapshot(core.StoredUncappedAuthorization(p,NOW), NOW+timedelta(seconds=2),0,0,0,0)


def tx(monkeypatch, cursor):
    events=[]
    def transaction(dsn, operation, *, readonly=False):
        events.append(('transaction',readonly)); result=operation(cursor); events.append('commit'); return result
    monkeypatch.setattr(store.db,'_local_transaction',transaction)
    return events


def test_policy_roundtrip_honest_unknown_cost(tmp_path):
    p=profile(tmp_path); a=policy(p); assert a.bind_request(req())==req()
    assert core.decode_authorization(a.payload,a.content_sha256)==a
    assert len(json.loads(a.payload))==13
    out=snap(a).to_dict()
    assert out['monetary_cap'] is out['global_call_cap'] is out['actual_billed_micros'] is None
    assert out['provider_submission_count'] is None and not out['hard_output_cap_enforced']
    assert str(p.codex_home) not in a.payload
    assert not any(k in json.loads(a.payload) for k in ('currency','per_call_micros','total_micros','cost_bound_attested'))
    assert out['unknown_invocations']==0


@pytest.mark.parametrize('change', [
    {'authorization_id':'bad id'}, {'profile_sha256':'x'}, {'model_id':''},
    {'uncapped_cost_approved':False}, {'uncapped_cost_approved':1}, {'provider_id':'other'},
    {'output_limit_mode':'hard'}, {'max_message_bytes':True}, {'max_message_bytes':0},
    {'max_message_bytes':2000001}, {'expires_at':NOW.replace(tzinfo=None)},
    {'request_keys':()}, {'request_keys':[]}, {'request_keys':(('r','x'),)},
    {'request_keys':(('r','a'*64),('r','b'*64))}, {'readonly':False}, {'report_only':1},
])
def test_invalid_policy(tmp_path,change):
    with pytest.raises(ValueError): policy(profile(tmp_path), **change)


@pytest.mark.parametrize('defect',['hash','extra','missing','duplicate','space','approval','time','roster'])
def test_decoder_fails_closed(tmp_path,defect):
    a=policy(profile(tmp_path));data=json.loads(a.payload)
    if defect=='extra':data['secret']=SENTINEL
    if defect=='missing':data.pop('readonly')
    if defect=='approval':data['uncapped_cost_approved']=False
    if defect=='time':data['expires_at']='bad'
    if defect=='roster':data['request_keys'][0]=3
    body=json.dumps(data,sort_keys=True,separators=(',',':'))
    if defect=='duplicate':body='{"readonly":true,'+body[1:]
    if defect=='space':body+=' '
    with pytest.raises(ValueError):
        core.decode_authorization(body,'0'*64 if defect=='hash' else sha256(body.encode()).hexdigest())


def test_mutation_and_wrong_request_rejected(tmp_path):
    a=policy(profile(tmp_path))
    for r in (old_req(), req(3), replace(req(),model_id='other'),replace(req(),max_start_delay_seconds=2)):
        with pytest.raises(ValueError): a.bind_request(r)
    object.__setattr__(a,'readonly',False)
    with pytest.raises(ValueError):core.copy_authorization(a)


@pytest.mark.parametrize('change',[{'validated_turns':1}, {'failed_invocations':1}, {'reported_total_tokens':1},
    {'reserved_invocations':True}, {'observed_at':NOW-timedelta(seconds=1)}])
def test_snapshot_consistency(tmp_path, change):
    with pytest.raises(ValueError):replace(snap(policy(profile(tmp_path))),**change)


def test_explicit_write_before_db(tmp_path,monkeypatch):
    a=policy(profile(tmp_path))
    monkeypatch.setattr(store.db,'_local_transaction',lambda *a,**k:pytest.fail('DB called'))
    for allowed in (False,None,1,'yes'):
        with pytest.raises(ValueError):store.create_uncapped_authorization_with_psycopg('fake',policy=a,allow_authorization_write=allowed)


def test_create_and_replay_immutable(tmp_path,monkeypatch):
    a=policy(profile(tmp_path));c=Cursor([None,None,row(a)]);events=tx(monkeypatch,c)
    saved=store.create_uncapped_authorization_with_psycopg('fake',policy=a,allow_authorization_write=True)
    assert saved.policy==a and events==[('transaction',False),'commit'] and not c.answers
    c=Cursor([None,(len(a.payload),),row(a)]);tx(monkeypatch,c)
    assert store.create_uncapped_authorization_with_psycopg('fake',policy=a,allow_authorization_write=True)==saved
    assert not any('INSERT' in q for q,_ in c.calls)
    c=Cursor([None,(len(a.payload),),row(a)]);tx(monkeypatch,c)
    with pytest.raises(store.db.ResearchCaptureConflict):
        store.create_uncapped_authorization_with_psycopg('fake',policy=replace(a,max_message_bytes=1),allow_authorization_write=True)


def test_consistent_snapshot_without_fake_bill(tmp_path,monkeypatch):
    a=policy(profile(tmp_path));c=Cursor([(NOW+timedelta(seconds=2),),(len(a.payload),),row(a),(4,2,1,Decimal(240))])
    ev=tx(monkeypatch,c);value=store.inspect_uncapped_authorization_with_psycopg('fake',authorization_id=a.authorization_id)
    assert ev==[('transaction',True),'commit']
    assert value.to_dict()['unknown_invocations']==1 and value.reported_total_tokens==240
    assert value.to_dict()['actual_billed_micros'] is None
    c=Cursor([(NOW+timedelta(seconds=2),),None]);tx(monkeypatch,c)
    assert store.inspect_uncapped_authorization_with_psycopg('fake',authorization_id='missing') is None


def test_reservation_uses_common_key_null_money_and_exact_claim(tmp_path,monkeypatch):
    a=policy(profile(tmp_path));r=req();message='[{"role":"user","content":"'+SENTINEL+'"}]'
    expected=(a.authorization_id,r.record_id,r.content_sha256,1,sha256(message.encode()).hexdigest(),len(message),100)
    c=Cursor([None,(len(a.payload),),row(a),None,(*expected,None,None,None)]);ev=tx(monkeypatch,c)
    monkeypatch.setattr(store.execution,'_lookup',lambda *args:state(r))
    assert store._reserve_invocation('fake',policy=a,request=r,call_number=1,messages_json=message,max_output_tokens=100) is True
    assert ev[-1]=='commit' and SENTINEL not in repr(c.calls)
    c=Cursor([None,(len(a.payload),),row(a),expected]);tx(monkeypatch,c)
    assert store._reserve_invocation('fake',policy=a,request=r,call_number=1,messages_json=message,max_output_tokens=100) is False
    c=Cursor([None,(len(a.payload),),row(a),(None,*expected[1:])]);tx(monkeypatch,c)
    with pytest.raises(store.db.ResearchCaptureConflict):
        store._reserve_invocation('fake',policy=a,request=r,call_number=1,messages_json=message,max_output_tokens=100)


@pytest.mark.parametrize('result',[None,parse(encoded())])
def test_outcome_is_bounded_metadata_and_no_refund(tmp_path,monkeypatch,result):
    a=policy(profile(tmp_path));r=req()
    vals=(None,)*6 if result is None else (*[getattr(result.usage,k) for k in store._USAGE],result.output_sha256)
    saved=(r.record_id,1,'failed' if result is None else 'validated',*vals,NOW+timedelta(seconds=2),True,True,True)
    c=Cursor([None,(a.authorization_id,r.content_sha256,NOW,100),None,saved]);tx(monkeypatch,c)
    assert store._save_outcome('fake',request=r,policy=a,call_number=1,result=result)==saved[9]
    assert not any('UPDATE' in q or 'DELETE' in q for q,_ in c.calls)


def execution_fixture(tmp_path,monkeypatch,**changes):
    p=profile(tmp_path);a=policy(p,**changes);events=[];model=Model()
    monkeypatch.setattr(runner,'inspect_uncapped_authorization_with_psycopg',lambda *a_,**k:snap(a))
    def claim(dsn,request):events.append('claim');return True,state(request)
    def capture(dsn,st,run):events.append('capture');return replace(st,status='captured',record=record_for(st.request,run))
    monkeypatch.setattr(runner.execution,'_claim',claim);monkeypatch.setattr(runner.execution,'_capture',capture)
    def reserve(*args,**kw):events.append('reserve-commit');return True
    def invoke(*args,**kw):
        events.append('invoke');reply=model.complete(messages_json=kw['messages_json'],max_output_tokens=kw['max_output_tokens'])
        return CodexReply(reply,CodexUsage(1,0,0,1,0),'a'*64)
    def save(*args,**kw):events.append('usage-commit' if kw['result'] else 'failure-commit')
    monkeypatch.setattr(runner,'_reserve_invocation',reserve);monkeypatch.setattr(runner,'invoke_codex',invoke)
    monkeypatch.setattr(runner,'_save_outcome',save)
    return p,a,events


def run(p,a,**kw):
    return runner.run_uncapped_research_with_psycopg('fake',request=req(),authorization_id=a.authorization_id,
        profile=p,allow_model_calls=True,**kw)


def test_claim_permit_usage_order_and_original_replay(tmp_path,monkeypatch):
    p,a,events=execution_fixture(tmp_path,monkeypatch)
    result=run(p,a)
    assert result.record.run.research.status=='completed'
    assert events==['claim','reserve-commit','invoke','usage-commit','reserve-commit','invoke','usage-commit','capture']
    events.clear();monkeypatch.setattr(runner.execution,'_claim',lambda *a,**k:(False,result))
    assert run(p,a) is result and not events


@pytest.mark.parametrize('failure',['reservation','invoke','usage','interrupt','replay-slot'])
def test_failures_never_retry_or_refund(tmp_path,monkeypatch,failure):
    p,a,events=execution_fixture(tmp_path,monkeypatch)
    def broken(*args,**kwargs):
        events.append(failure)
        if failure=='interrupt':raise KeyboardInterrupt
        if failure=='replay-slot':return False
        raise RuntimeError(SENTINEL)
    target=('_reserve_invocation' if failure in ('reservation','replay-slot') else
            '_save_outcome' if failure=='usage' else 'invoke_codex')
    monkeypatch.setattr(runner,target,broken)
    if failure=='interrupt':
        with pytest.raises(KeyboardInterrupt):run(p,a)
        assert 'capture' not in events and 'failure-commit' not in events
    else:
        result=run(p,a)
        assert result.record.run.research.reason_code=='model_failed'
        assert result.record.run.research.model_calls==1
        assert 'failure-commit' in events if failure=='invoke' else 'failure-commit' not in events
    assert events.count(failure)==1


def test_expired_policy_preserves_original_incomplete(tmp_path,monkeypatch):
    p,a,events=execution_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(runner,'inspect_uncapped_authorization_with_psycopg',lambda *args,**kw:replace(snap(a),observed_at=a.expires_at))
    monkeypatch.setattr(runner.execution,'inspect_captured_research_with_psycopg',lambda *a,**k:None)
    with pytest.raises(ValueError,match='expired'):run(p,a)
    assert events==[]
    prior=state(req());monkeypatch.setattr(runner.execution,'inspect_captured_research_with_psycopg',lambda *a,**k:prior)
    assert run(p,a)==prior and events==[]


def test_profile_mismatch_and_missing_permission_before_claim(tmp_path,monkeypatch):
    p,a,events=execution_fixture(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='profile_mismatch'):run(replace(p,timeout_seconds=10),a)
    with pytest.raises(ValueError,match='opt_in'):
        runner.run_uncapped_research_with_psycopg('fake',request=req(),authorization_id=a.authorization_id,profile=p)
    assert events==[]


def test_dispatch_modes_mutually_exclusive_before_database(tmp_path,monkeypatch):
    p=profile(tmp_path)
    monkeypatch.setattr(drain,'load_research_batch_with_psycopg',lambda *a,**k:pytest.fail('DB reached'))
    for kw in (dict(codex_profile=p),dict(uncapped_authorization_id='a',codex_profile=p,model_budget_id='b'),
               dict(uncapped_authorization_id='a',codex_profile=p,model_factory=lambda _:None),
               dict(uncapped_authorization_id='a')):
        with pytest.raises(ValueError):drain.run_research_batch_with_psycopg('fake',batch_id='b',allow_model_calls=True,**kw)


def test_database_dsn_validation_reused():
    with pytest.raises(ValueError):
        store.inspect_uncapped_authorization_with_psycopg('postgresql://u@192.0.2.1/db',authorization_id='a')


def test_migration_preserves_shared_ordinals_and_exact_capped_body():
    root=Path(__file__).resolve().parents[1]
    old=(root/'supabase/migrations/20260915010000_research_model_budgets.sql').read_text()
    current=(root/'supabase/migrations/20260919000000_research_uncapped_codex.sql').read_text()
    original=old.split('create function research_capture.stamp_model_call() returns trigger',1)[1].split('create trigger stamp_model_call',1)[0]
    body=original.split('begin\n',1)[1].rsplit('end $$;',1)[0]
    assert body in current
    assert 'security definer' not in current.lower()
    assert 'foreign key (record_id,call_number)' in current
    assert "authorization_id is not null and budget_id is null and reservation_number is null and reserved_micros is null" in current
    manifest=json.loads((root/'database/migrations.lock.json').read_text())['migrations']
    assert len(manifest)==68 and manifest[-1]['sha256']==sha256(current.encode()).hexdigest()
