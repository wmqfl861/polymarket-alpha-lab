"""Shared reservations and lazy clients with synthetic data; no provider or DB."""
from dataclasses import replace
from datetime import timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from threading import Barrier, Lock
from concurrent.futures import ThreadPoolExecutor

import pytest

from polymarket_alpha_lab import research_model_budget as core
from polymarket_alpha_lab import research_model_budget_store as store
from polymarket_alpha_lab import research_model_budget_runner as runner
from polymarket_alpha_lab import research_dispatch_runner as batch_runner
from polymarket_alpha_lab import research_dispatch_rotation_runner as rotation_runner
from polymarket_alpha_lab.research_dispatch import ResearchBatch, StoredResearchBatch, ResearchBatchSnapshot
from tests.test_research_execution import request, state, Model, record_for, make_run, NOW
from tests.test_research_dispatch import Cursor


def req(n=0, **kw):
    return request(record_id='budget-task-'+str(n),intake=make_run(condition='budget-event-'+str(n),
        task='budget-task-'+str(n),team='crypto_btc' if n%2 else 'crypto_eth').intake,**kw)


def policy(requests=None, **kw):
    requests = (req(),req(1)) if requests is None else requests
    data=dict(budget_id='approved-synthetic-budget',provider_id='synthetic-provider',model_id='synthetic-model',
        currency='USD',total_micros=300,per_call_micros=100,max_calls=3,max_message_bytes=100000,
        max_output_tokens=1024,expires_at=NOW+timedelta(minutes=30),
        request_keys=tuple((r.record_id,r.content_sha256) for r in requests),
        bound_reference_sha256='a'*64,cost_bound_attested=True)
    data.update(kw)
    return core.ModelCallBudget(**data)


def saved(p=None):return core.StoredModelBudget(policy() if p is None else p,NOW)
def snapshot(p=None,n=0):
    s=saved(p)
    return core.ModelBudgetSnapshot(s,NOW+timedelta(seconds=3),n,n*s.policy.per_call_micros)

def row(p=None):
    p=policy() if p is None else p
    return (p.budget_id,p.payload,p.content_sha256,NOW,True,True,True)


def reserve_args(p=None, **kw):
    args=dict(policy=policy() if p is None else p,request=req(),call_number=1,
        messages_json='[{"role":"user","content":"Synthetic input"}]',max_output_tokens=512)
    args.update(kw)
    return args


def tx(monkeypatch,c):
    calls=[]
    def call(dsn,fn,*,readonly=False):calls.append(readonly);return fn(c)
    monkeypatch.setattr(store.db,'_local_transaction',call)
    return calls


def test_exact_canonical_policy_copy_and_inert_accounting():
    p=policy();body=p.payload
    clone=core.decode_budget(body,p.content_sha256)
    assert clone==p and clone is not p and clone.content_sha256==p.content_sha256
    assert clone.bind_request(req()).payload==req().payload
    out=snapshot(p,2).to_dict()
    assert out['reserved_micros']==200 and out['available_call_reservations']==1
    assert out['actual_provider_calls'] is out['actual_billed_micros'] is None
    assert out['provider_charge_bound_verified'] is out['automatic_refunds'] is False
    assert 'SYNTHETIC-PRIVATE' not in json.dumps(out)
    assert 'Synthetic input' not in json.dumps(out)


@pytest.mark.parametrize('field,value',[
 ('budget_id','bad id'),('provider_id','with space'),('model_id',''),('currency','usd'),('currency','US'),
 ('total_micros',True),('total_micros',0),('total_micros',10**15+1),('total_micros',Decimal(300)),
 ('per_call_micros',0),('per_call_micros',301),('per_call_micros',1.0),
 ('max_calls',0),('max_calls',3201),('max_calls',True),
 ('max_message_bytes',0),('max_message_bytes',2000001),('max_output_tokens',0),('max_output_tokens',8193),
 ('expires_at',NOW.replace(tzinfo=None)),('request_keys',[]),('request_keys',()),
 ('request_keys',(('r','x'),)),('request_keys',(('r','a'*64),('r','b'*64))),
 ('bound_reference_sha256','0'*63),('cost_bound_attested',False),('cost_bound_attested',1),
 ('paper_only',False),('report_only',1),('readonly',None),
])
def test_invalid_policy(field,value):
    with pytest.raises(ValueError):policy(**{field:value})


@pytest.mark.parametrize('defect',['hash','version','unknown','missing','noncanonical','flag','bad_time','request_member'])
def test_canonical_decode_fail_closed(defect):
    p=policy();d=json.loads(p.payload)
    if defect=='version':d['schema_version']='other'
    if defect=='unknown':d['extra']='SYNTHETIC-PRIVATE'
    if defect=='missing':d.pop('currency')
    if defect=='flag':d['cost_bound_attested']=False
    if defect=='bad_time':d['expires_at']='bad'
    if defect=='request_member':d['request_keys'][0]='bad'
    body=json.dumps(d,sort_keys=True,separators=(',',':'))
    if defect=='noncanonical':body+='\n'
    digest='0'*64 if defect=='hash' else sha256(body.encode()).hexdigest()
    with pytest.raises(ValueError,match='payload_invalid'):core.decode_budget(body,digest)


def test_copy_and_timezone_binding_revalidated():
    p=policy();offset=timezone(timedelta(hours=8))
    assert replace(p,expires_at=p.expires_at.astimezone(offset)).payload==p.payload
    object.__setattr__(p,'readonly',False)
    with pytest.raises(ValueError):core.copy_budget(p)
    for r in (req(3),replace(req(),model_id='different'),replace(req(),max_start_delay_seconds=4)):
        with pytest.raises(ValueError,match='request_mismatch'):policy().bind_request(r)
    politics=request(intake=make_run(team='politics').intake)
    with pytest.raises(ValueError,match='request_mismatch'):policy((politics,)).bind_request(politics)


@pytest.mark.parametrize('change',[dict(reserved_calls=4),dict(reserved_calls=True),dict(reserved_micros=101),
 dict(observed_at=NOW-timedelta(seconds=1)),dict(stored=object())])
def test_corrupt_snapshot_not_a_valid_budget(change):
    with pytest.raises(ValueError):replace(snapshot(),**change)


def test_expired_snapshot_not_new_capacity_and_rounding_is_integer():
    s=replace(snapshot(),observed_at=policy().expires_at)
    assert s.to_dict()['available_call_reservations']==0 and s.to_dict()['expired']
    assert snapshot(policy(total_micros=299),2).to_dict()['available_call_reservations']==0
    assert snapshot(policy(max_calls=1),1).to_dict()['available_call_reservations']==0


@pytest.mark.parametrize('value',[False,1,None,'yes'])
def test_explicit_write_and_provider_optin_before_database(monkeypatch,value):
    monkeypatch.setattr(store.db,'_local_transaction',lambda *a,**k:pytest.fail('DB reached'))
    with pytest.raises(ValueError):store.create_model_budget_with_psycopg('fake',policy=policy(),allow_budget_write=value)
    with pytest.raises(ValueError):runner.run_budgeted_research_with_psycopg('fake',request=req(),budget_id='b',
        model_factory=lambda _:None,allow_model_calls=value)


def test_create_budget_exact_replay_no_topup(monkeypatch):
    p=policy();c=Cursor([None,None,row(p)]);calls=tx(monkeypatch,c)
    assert store.create_model_budget_with_psycopg('fake',policy=p,allow_budget_write=True)==saved(p)
    assert calls==[False] and 'pg_advisory_xact_lock' in c.calls[0][0]
    c=Cursor([None,(len(p.payload),),row(p)]);tx(monkeypatch,c)
    assert store.create_model_budget_with_psycopg('fake',policy=p,allow_budget_write=True)==saved(p)
    assert all('INSERT' not in q for q,_ in c.calls)
    c=Cursor([None,(len(p.payload),),row(p)]);tx(monkeypatch,c)
    with pytest.raises(store.db.ResearchCaptureConflict):
        store.create_model_budget_with_psycopg('fake',policy=replace(p,max_calls=2),allow_budget_write=True)


def test_read_uses_consistent_snapshot_and_exact_numeric_sum(monkeypatch):
    p=policy();c=Cursor([(NOW+timedelta(seconds=2),),(len(p.payload),),row(p),(2,Decimal(200),2)])
    assert tx(monkeypatch,c)==[]
    out=store.load_model_budget_with_psycopg('fake',budget_id=p.budget_id)
    assert out.reserved_calls==2 and out.reserved_micros==200
    c=Cursor([(NOW+timedelta(seconds=2),),(len(p.payload),),row(p),(2,Decimal('200.1'),2)])
    tx(monkeypatch,c)
    with pytest.raises(ValueError):store.load_model_budget_with_psycopg('fake',budget_id=p.budget_id)


def test_missing_and_oversize_policy_before_read_body(monkeypatch):
    c=Cursor([(NOW,),None]);seen=tx(monkeypatch,c)
    assert store.load_model_budget_with_psycopg('fake',budget_id='missing') is None and seen==[True]
    c=Cursor([(NOW,),(core.MAX_POLICY_BYTES+1,)]);tx(monkeypatch,c)
    with pytest.raises(store.db.ResearchCaptureConflict):store.load_model_budget_with_psycopg('fake',budget_id='huge')


@pytest.mark.parametrize('change',[(0,'wrong'),(2,'b'*64),(4,False),(5,1),(6,None)])
def test_database_row_mirror_is_strict(change):
    values=list(row());values[change[0]]=change[1]
    with pytest.raises(ValueError):store._row(tuple(values))


def test_permit_commits_before_success_and_does_not_save_prompt(monkeypatch):
    a=reserve_args();p=a['policy'];r=a['request'];raw=a['messages_json'].encode()
    expected=(p.budget_id,r.record_id,r.content_sha256,1,sha256(raw).hexdigest(),len(raw),512)
    c=Cursor([None,(len(p.payload),),row(p),None,expected]);seen=tx(monkeypatch,c)
    monkeypatch.setattr(store.execution,'_lookup',lambda *_:state(r))
    assert store._reserve_call('fake',**a) is True and seen==[False] and not c.answers
    assert 'Synthetic input' not in repr(c.calls)
    assert not any('UPDATE' in q or 'DELETE' in q for q,_ in c.calls)


def test_same_call_replay_inert_and_changed_transcript_conflicts(monkeypatch):
    a=reserve_args();p=a['policy'];raw=a['messages_json'].encode()
    prior=(p.budget_id,a['request'].content_sha256,sha256(raw).hexdigest(),len(raw),512)
    c=Cursor([None,(len(p.payload),),row(p),prior]);tx(monkeypatch,c)
    assert store._reserve_call('fake',**a) is False
    c=Cursor([None,(len(p.payload),),row(p),prior]);tx(monkeypatch,c)
    with pytest.raises(store.db.ResearchCaptureConflict):store._reserve_call('fake',**dict(a,messages_json='[1]'))


@pytest.mark.parametrize('change',[{'call_number':0},{'call_number':33},{'max_output_tokens':1025},
 {'messages_json':'[]'},{'messages_json':'{}'},{'messages_json':'[{"x":1,"x":2}]'},
 {'messages_json':'[NaN]'},{'messages_json':'["'+'a'*100001+'"]'}])
def test_invalid_call_before_reservation(monkeypatch,change):
    monkeypatch.setattr(store.db,'_local_transaction',lambda *a,**k:pytest.fail('DB reached'))
    with pytest.raises(ValueError):store._reserve_call('fake',**reserve_args(**change))


def test_utf8_byte_limit_not_character_count(monkeypatch):
    monkeypatch.setattr(store.db,'_local_transaction',lambda *a,**k:pytest.fail('DB reached'))
    with pytest.raises(ValueError):store._reserve_call('fake',**reserve_args(policy(max_message_bytes=10),
        messages_json='["汉汉汉"]'))


@pytest.mark.parametrize('claimed',[None,'captured','wrong'])
def test_reservation_requires_matching_incomplete_claim(monkeypatch,claimed):
    a=reserve_args();p=a['policy'];r=a['request']
    c=Cursor([None,(len(p.payload),),row(p),None]);tx(monkeypatch,c)
    value=None if claimed is None else (replace(state(r),status='already_captured',record=record_for(r,make_run(
        condition=r.intake.condition_id,task=r.intake.task_id,team=r.intake.team_id))) if claimed=='captured' else state(req(5)))
    monkeypatch.setattr(store.execution,'_lookup',lambda *_:value)
    with pytest.raises(store.db.ResearchCaptureConflict):store._reserve_call('fake',**a)
    assert all('INSERT' not in q for q,_ in c.calls)


class Allowance:
    """Synthetic atomic reservation model; not a stand-in for native acceptance."""
    def __init__(self,limit=3):self.limit=limit;self.rows={};self.lock=Lock();self.events=[]
    def reserve(self,dsn,**kw):
        with self.lock:
            k=(kw['request'].record_id,kw['call_number'])
            if k in self.rows:return False
            if len(self.rows)>=self.limit:raise RuntimeError('exhausted')
            self.rows[k]=kw['policy'].per_call_micros;self.events.append(k)
            return True


def test_lazy_factory_only_after_new_commit_and_no_refund(monkeypatch):
    p=policy();ledger=Allowance(1);monkeypatch.setattr(runner,'_reserve_call',ledger.reserve)
    events=[]
    def factory(team):
        assert len(ledger.rows)==1
        events.append(team);raise RuntimeError('SYNTHETIC-PRIVATE-factory')
    m=runner._BudgetedModel('private-dsn',p,req(),factory)
    assert events==[] and 'private-dsn' not in repr(m)
    with pytest.raises(ValueError,match='blocked_or_failed'):m.complete(messages_json='[1]',max_output_tokens=1)
    with pytest.raises(ValueError,match='client_stopped'):m.complete(messages_json='[1]',max_output_tokens=1)
    assert len(ledger.rows)==1 and len(events)==1
    twin=runner._BudgetedModel('private-dsn',p,req(),lambda _:pytest.fail('replay client'))
    with pytest.raises(ValueError):twin.complete(messages_json='[1]',max_output_tokens=1)


@pytest.mark.parametrize('failure',[RuntimeError('SYNTHETIC-PRIVATE'),KeyboardInterrupt(),SystemExit(7)])
def test_uncertain_commit_never_enters_factory_and_latches(monkeypatch,failure):
    ledger=Allowance()
    def uncertain(dsn,**kw):ledger.reserve(dsn,**kw);raise failure
    monkeypatch.setattr(runner,'_reserve_call',uncertain)
    m=runner._BudgetedModel('fake',policy(),req(),lambda _:pytest.fail('factory before known commit'))
    with pytest.raises(ValueError if isinstance(failure,Exception) else type(failure)):
        m.complete(messages_json='[1]',max_output_tokens=1)
    assert len(ledger.rows)==1
    with pytest.raises(ValueError,match='client_stopped'):m.complete(messages_json='[1]',max_output_tokens=1)


def test_concurrent_clients_share_one_finite_allowance(monkeypatch):
    requests=tuple(req(n) for n in range(8));p=policy(requests,max_calls=3)
    ledger=Allowance(3);monkeypatch.setattr(runner,'_reserve_call',ledger.reserve)
    made=[];lock=Lock();barrier=Barrier(8)
    def factory(team):
        with lock:made.append(team)
        return Model()
    def call(r):
        m=runner._BudgetedModel('fake',p,r,factory);barrier.wait()
        try:return m.complete(messages_json='[1]',max_output_tokens=1)
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=8) as pool:out=list(pool.map(call,requests))
    assert sum(x is not None for x in out)==3 and len(made)==3 and len(ledger.rows)==3
    assert sum(ledger.rows.values())==300


def test_existing_execution_loop_captures_denial_and_completed_replay_stays_inert(monkeypatch):
    p=policy();r=req();ledger=Allowance(1);made=[]
    monkeypatch.setattr(runner,'load_model_budget_with_psycopg',lambda *a,**k:snapshot(p))
    monkeypatch.setattr(runner,'_reserve_call',ledger.reserve)
    current=state(r)
    monkeypatch.setattr(runner.execution,'_claim',lambda dsn,request:(True,current))
    def capture(dsn,st,run):return replace(st,status='captured',record=record_for(r,run))
    monkeypatch.setattr(runner.execution,'_capture',capture)
    def factory(team):made.append(team);return Model()
    result=runner.run_budgeted_research_with_psycopg('fake',request=r,budget_id=p.budget_id,
        model_factory=factory,allow_model_calls=True)
    assert result.record.run.research.status=='failed' and result.record.run.research.reason_code=='model_failed'
    assert result.record.run.research.model_calls==2 and len(ledger.rows)==1 and len(made)==1
    monkeypatch.setattr(runner.execution,'_claim',lambda dsn,request:(False,replace(result,status='already_captured')))
    again=runner.run_budgeted_research_with_psycopg('fake',request=r,budget_id=p.budget_id,
        model_factory=lambda _:pytest.fail('replay factory'),allow_model_calls=True)
    assert again.record==result.record and len(ledger.rows)==1


def test_dispatch_and_rotation_forward_one_shared_budget_id(monkeypatch):
    from tests.test_research_dispatch_rotation import Harness, run
    h=Harness(monkeypatch);h.fail=set();seen=[]
    def controlled(dsn,**kw):
        seen.append(kw['budget_id']);return h.execute(dsn,request=kw['request'],model_factory=kw['model_factory'])
    monkeypatch.setattr(runner,'run_budgeted_research_with_psycopg',controlled)
    out=run(max_tasks=4,model_budget_id='budget-bound')
    assert out.status=='dispatched' and seen==['budget-bound']*4
    b=ResearchBatch('batch',tuple(req(n) for n in range(2)))
    s=ResearchBatchSnapshot(StoredResearchBatch(b,NOW),NOW+timedelta(seconds=3),(None,None))
    monkeypatch.setattr(batch_runner,'load_research_batch_with_psycopg',lambda *a,**k:s)
    out=batch_runner.run_research_batch_with_psycopg('fake',batch_id='batch',model_factory=lambda _:None,
        allow_model_calls=True,max_workers=1,model_budget_id='budget-bound')
    assert len(out.attempts)==2 and seen==['budget-bound']*6


def test_managed_methods_bind_private_instance_and_expire(monkeypatch):
    from polymarket_alpha_lab.project_postgres.research import ProjectResearchSession
    from polymarket_alpha_lab.project_postgres import binding,files
    class DB:
        def _dsn(self,_):return 'private-dsn'
    s=ProjectResearchSession(DB(),dict(instance_id='a'*32,root_sha256='b'*64,system_identifier='123'))
    seen=[]
    def op(dsn,**kw):seen.append(binding._EXPECTED.get());return 'ok'
    monkeypatch.setattr(store,'create_model_budget_with_psycopg',op)
    monkeypatch.setattr(store,'load_model_budget_with_psycopg',op)
    monkeypatch.setattr(runner,'run_budgeted_research_with_psycopg',op)
    assert s.create_model_budget(policy=policy(),allow_budget_write=True)=='ok'
    assert s.inspect_model_budget(budget_id='b')=='ok'
    assert s.run_budgeted_research(budget_id='b')=='ok'
    assert seen==[('a'*32,'b'*64,'123')]*3 and binding._EXPECTED.get() is None
    s.close()
    with pytest.raises(files.ProjectDatabaseError):s.inspect_model_budget(budget_id='b')


def test_remote_dsn_rejected_without_database():
    with pytest.raises(ValueError):store.load_model_budget_with_psycopg('postgresql://u@192.0.2.1/db',budget_id='b')


def test_read_requested_budget_identity_cannot_be_substituted(monkeypatch):
    monkeypatch.setattr(runner,'load_model_budget_with_psycopg',lambda *a,**k:snapshot(policy(budget_id='other')))
    monkeypatch.setattr(runner.execution,'run_captured_research_with_psycopg',lambda *a,**k:pytest.fail('foreign policy reached claim'))
    with pytest.raises(ValueError,match='lookup_mismatch'):
        runner.run_budgeted_research_with_psycopg('fake',request=req(),budget_id='expected',
            model_factory=lambda _:None,allow_model_calls=True)


@pytest.mark.parametrize('expired',[True,False])
def test_exhausted_policy_does_not_claim_new_work_but_replays_old_claim(monkeypatch,expired):
    p=policy();s=snapshot(p,0 if expired else 3)
    if expired:s=replace(s,observed_at=p.expires_at)
    monkeypatch.setattr(runner,'load_model_budget_with_psycopg',lambda *a,**k:s)
    monkeypatch.setattr(runner.execution,'inspect_captured_research_with_psycopg',lambda *a,**k:None)
    monkeypatch.setattr(runner.execution,'run_captured_research_with_psycopg',lambda *a,**k:pytest.fail('new claim with no capacity'))
    args=dict(request=req(),budget_id=p.budget_id,model_factory=lambda _:None,allow_model_calls=True)
    with pytest.raises(ValueError,match='not_available'):runner.run_budgeted_research_with_psycopg('fake',**args)
    claim=state(req())
    monkeypatch.setattr(runner.execution,'inspect_captured_research_with_psycopg',lambda *a,**k:claim)
    assert runner.run_budgeted_research_with_psycopg('fake',**args)==claim


@pytest.mark.parametrize('reply',[None,{'total_tokens':1},Model])
def test_invalid_provider_reply_never_refunds_or_retries(monkeypatch,reply):
    ledger=Allowance();monkeypatch.setattr(runner,'_reserve_call',ledger.reserve)
    class Client:
        def complete(self,**kw):return reply
    m=runner._BudgetedModel('fake',policy(),req(),lambda _:Client())
    with pytest.raises(ValueError):m.complete(messages_json='[1]',max_output_tokens=1)
    with pytest.raises(ValueError,match='client_stopped'):m.complete(messages_json='[1]',max_output_tokens=1)
    assert len(ledger.rows)==1


def test_retained_snapshot_defensive_type_and_mutation_checks(monkeypatch):
    monkeypatch.setattr(runner.execution,'run_captured_research_with_psycopg',lambda *a,**k:pytest.fail('claim reached'))
    args=dict(request=req(),budget_id=policy().budget_id,model_factory=lambda _:None,allow_model_calls=True)
    for value in (object(),replace(snapshot(),reserved_calls=0)):
        if type(value) is core.ModelBudgetSnapshot:object.__setattr__(value.stored.policy,'total_micros',1)
        monkeypatch.setattr(runner,'load_model_budget_with_psycopg',lambda *a,**k:value)
        with pytest.raises(ValueError):runner.run_budgeted_research_with_psycopg('fake',**args)


def test_task_factory_not_created_for_blocked_intake(monkeypatch):
    r=request(record_id='blocked',intake=make_run(status='intake_blocked').intake)
    p=policy((r,));monkeypatch.setattr(runner,'load_model_budget_with_psycopg',lambda *a,**k:snapshot(p))
    monkeypatch.setattr(runner,'_reserve_call',lambda *a,**k:pytest.fail('blocked input reserved a call'))
    monkeypatch.setattr(runner.execution,'_claim',lambda *a,**k:(True,state(r)))
    monkeypatch.setattr(runner.execution,'_capture',lambda dsn,st,run:replace(st,status='captured',record=record_for(r,run)))
    out=runner.run_budgeted_research_with_psycopg('fake',request=r,budget_id=p.budget_id,
        model_factory=lambda _:pytest.fail('blocked factory'),allow_model_calls=True)
    assert out.record.run.intake.status=='blocked' and out.record.run.research is None


def test_invalid_request_does_not_open_a_budget_connection(monkeypatch):
    monkeypatch.setattr(runner,'load_model_budget_with_psycopg',lambda *a,**k:pytest.fail('DB read for invalid request'))
    with pytest.raises(ValueError):runner.run_budgeted_research_with_psycopg('fake',request=object(),
        budget_id='b',model_factory=lambda _:None,allow_model_calls=True)


# Prepared requests must fit a fixed per-call output allowance BEFORE a claim.
@pytest.mark.parametrize('number', [0, 1])
@pytest.mark.parametrize('cap', [1, 1023])
def test_incompatible_output_budget_refuses_before_claim(monkeypatch, number, cap):
    r = req(number)
    p = policy((r,), max_output_tokens=cap)
    events = []
    monkeypatch.setattr(runner, 'load_model_budget_with_psycopg', lambda *a, **k: snapshot(p))
    monkeypatch.setattr(runner.execution, 'inspect_captured_research_with_psycopg', lambda *a, **k: None)
    def claim(dsn, request):
        events.append('claim')
        return True, state(request)
    def capture(dsn, claimed, run):
        events.append('capture')
        return replace(claimed, status='captured', record=record_for(r, run))
    monkeypatch.setattr(runner.execution, '_claim', claim)
    monkeypatch.setattr(runner.execution, '_capture', capture)
    def factory(team):
        events.append('factory')
        return Model()
    with pytest.raises(ValueError, match='^research_budget_output_limit_incompatible$'):
        runner.run_budgeted_research_with_psycopg('fake', request=r, budget_id=p.budget_id,
            model_factory=factory, allow_model_calls=True)
    assert events == []
    assert r.limits.max_output_tokens == 1024 and p.max_output_tokens == cap


@pytest.mark.parametrize('number', [0, 1])
@pytest.mark.parametrize('existing', ['incomplete', 'captured'])
def test_output_preflight_preserves_original_execution_replay(monkeypatch, number, existing):
    r = req(number)
    p = policy((r,), max_output_tokens=1)
    original = state(r)
    if existing == 'captured':
        # Reuse the existing valid captured-record fixture for the same request.
        original = replace(original, status='captured', record=record_for(r, make_run(
            condition='budget-event-'+str(number), task='budget-task-'+str(number),
            team='crypto_btc' if number % 2 else 'crypto_eth')))
    monkeypatch.setattr(runner, 'load_model_budget_with_psycopg', lambda *a, **k: snapshot(p))
    monkeypatch.setattr(runner.execution, 'inspect_captured_research_with_psycopg', lambda *a, **k: original)
    entered = []
    def unexpected(*a, **k):
        entered.append('execution')
        return original
    monkeypatch.setattr(runner.execution, 'run_captured_research_with_psycopg', unexpected)
    out = runner.run_budgeted_research_with_psycopg('fake', request=r, budget_id=p.budget_id,
        model_factory=lambda _: pytest.fail('replay factory'), allow_model_calls=True)
    assert out == original and entered == []


@pytest.mark.parametrize('cap', [1024, 2048])
def test_compatible_output_budget_keeps_original_claim_path(monkeypatch, cap):
    r = req()
    p = policy((r,), max_output_tokens=cap)
    seen = []
    monkeypatch.setattr(runner, 'load_model_budget_with_psycopg', lambda *a, **k: snapshot(p))
    monkeypatch.setattr(runner.execution, 'inspect_captured_research_with_psycopg',
        lambda *a, **k: pytest.fail('unnecessary preflight read'))
    def execution(dsn, **kw):
        seen.append(kw['request'])
        return 'original-path'
    monkeypatch.setattr(runner.execution, 'run_captured_research_with_psycopg', execution)
    assert runner.run_budgeted_research_with_psycopg('fake', request=r, budget_id=p.budget_id,
        model_factory=lambda _: None, allow_model_calls=True) == 'original-path'
    assert seen == [r]


# Separate adversarial self-review of the no-new-claim path and compatibility.
@pytest.mark.parametrize('cap', [1, 1023])
def test_preflight_review_keeps_blocked_intake_capture(monkeypatch, cap):
    r = request(record_id='blocked-preflight', intake=make_run(status='intake_blocked').intake)
    p = policy((r,), max_output_tokens=cap)
    monkeypatch.setattr(runner, 'load_model_budget_with_psycopg', lambda *a, **k: snapshot(p))
    monkeypatch.setattr(runner.execution, 'inspect_captured_research_with_psycopg',
        lambda *a, **k: pytest.fail('no-model intake changed to preflight refusal'))
    monkeypatch.setattr(runner.execution, '_claim', lambda *a, **k: (True, state(r)))
    monkeypatch.setattr(runner.execution, '_capture', lambda dsn, st, run:
        replace(st, status='captured', record=record_for(r, run)))
    out = runner.run_budgeted_research_with_psycopg('fake', request=r, budget_id=p.budget_id,
        model_factory=lambda _: pytest.fail('blocked intake constructed a client'), allow_model_calls=True)
    assert out.record.run.intake.status == 'blocked' and out.record.run.research is None


@pytest.mark.parametrize('expired', [False, True])
def test_preflight_review_preserves_empty_budget_error_and_replay(monkeypatch, expired):
    r = req()
    p = policy((r,), max_output_tokens=1)
    snap = snapshot(p, 0 if expired else 3)
    if expired:
        snap = replace(snap, observed_at=p.expires_at)
    monkeypatch.setattr(runner, 'load_model_budget_with_psycopg', lambda *a, **k: snap)
    monkeypatch.setattr(runner.execution, 'inspect_captured_research_with_psycopg', lambda *a, **k: None)
    monkeypatch.setattr(runner.execution, 'run_captured_research_with_psycopg',
        lambda *a, **k: pytest.fail('new execution under unusable budget'))
    kwargs = dict(request=r, budget_id=p.budget_id, model_factory=lambda _: None, allow_model_calls=True)
    with pytest.raises(ValueError, match='^research_budget_not_available$'):
        runner.run_budgeted_research_with_psycopg('fake', **kwargs)
    original = state(r)
    monkeypatch.setattr(runner.execution, 'inspect_captured_research_with_psycopg', lambda *a, **k: original)
    assert runner.run_budgeted_research_with_psycopg('fake', **kwargs) == original


@pytest.mark.parametrize('defect', ['request', 'readonly', 'type'])
def test_preflight_review_rejects_bad_existing_receipt(monkeypatch, defect):
    r = req()
    p = policy((r,), max_output_tokens=1)
    prior = state(replace(r, protocol_version='other') if defect == 'request' else r)
    if defect == 'readonly':
        object.__setattr__(prior, 'readonly', False)
    elif defect == 'type':
        prior = object()
    monkeypatch.setattr(runner, 'load_model_budget_with_psycopg', lambda *a, **k: snapshot(p))
    monkeypatch.setattr(runner.execution, 'inspect_captured_research_with_psycopg', lambda *a, **k: prior)
    entered = []
    monkeypatch.setattr(runner.execution, 'run_captured_research_with_psycopg', lambda *a, **k: entered.append(1))
    with pytest.raises((ValueError, TypeError)):
        runner.run_budgeted_research_with_psycopg('fake', request=r, budget_id=p.budget_id,
            model_factory=lambda _: None, allow_model_calls=True)
    assert entered == []


@pytest.mark.parametrize('error', [OSError('synthetic-private-detail'), KeyboardInterrupt(), SystemExit(0)])
def test_preflight_review_failed_history_read_never_falls_through_to_execution(monkeypatch, error):
    r = req()
    p = policy((r,), max_output_tokens=1)
    monkeypatch.setattr(runner, 'load_model_budget_with_psycopg', lambda *a, **k: snapshot(p))
    def inspect(*args, **kwargs):
        raise error
    monkeypatch.setattr(runner.execution, 'inspect_captured_research_with_psycopg', inspect)
    entered = []
    monkeypatch.setattr(runner.execution, 'run_captured_research_with_psycopg', lambda *a, **k: entered.append(1))
    with pytest.raises(type(error)) as caught:
        runner.run_budgeted_research_with_psycopg('fake', request=r, budget_id=p.budget_id,
            model_factory=lambda _: None, allow_model_calls=True)
    assert caught.value is error and entered == []


def test_preflight_review_never_clamps_request_or_policy(monkeypatch):
    r = req()
    bad = policy((r,), max_output_tokens=1)
    good = replace(bad, budget_id='explicitly-reviewed-compatible-budget', max_output_tokens=r.limits.max_output_tokens)
    originals = r.payload, bad.payload, good.payload
    policies = {p.budget_id: p for p in (bad, good)}
    monkeypatch.setattr(runner, 'load_model_budget_with_psycopg', lambda dsn, budget_id: snapshot(policies[budget_id]))
    monkeypatch.setattr(runner.execution, 'inspect_captured_research_with_psycopg', lambda *a, **k: None)
    seen = []
    monkeypatch.setattr(runner.execution, 'run_captured_research_with_psycopg',
        lambda dsn, **kw: seen.append(kw['request'].payload))
    with pytest.raises(ValueError, match='output_limit_incompatible'):
        runner.run_budgeted_research_with_psycopg('fake', request=r, budget_id=bad.budget_id,
            model_factory=lambda _: None, allow_model_calls=True)
    assert seen == []
    runner.run_budgeted_research_with_psycopg('fake', request=r, budget_id=good.budget_id,
        model_factory=lambda _: None, allow_model_calls=True)
    assert seen == [originals[0]] and (r.payload, bad.payload, good.payload) == originals
