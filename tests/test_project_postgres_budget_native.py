"""Actual shared budget, no-refund interruption and bounded batch/rotation proof.

Only a fresh project-native PostgreSQL instance and synthetic provider clients.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from threading import Lock
import time
import uuid

import pytest

from polymarket_alpha_lab.project_postgres import files
from polymarket_alpha_lab.project_postgres.runtime import import_runtime_directory
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_dispatch import ResearchBatch
from polymarket_alpha_lab.research_model_budget import ModelCallBudget
from polymarket_alpha_lab import research_model_budget_store as store
from polymarket_alpha_lab import research_model_budget_runner as guarded
from tests.test_project_postgres_dispatch_native import prepared
from tests.test_team_research_cross_source import Model

ROOT=Path(__file__).resolve().parents[1]
ENABLED=os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES')=='1'
TAIL='20260915010000_research_model_budgets.sql'


def policy(budget_id,requests,**kw):
    fields=dict(budget_id=budget_id,provider_id='synthetic-provider',model_id=requests[0].model_id,
        currency='USD',total_micros=350,per_call_micros=100,max_calls=4,
        max_message_bytes=100000,max_output_tokens=1024,
        expires_at=datetime.now(UTC)+timedelta(hours=1),
        request_keys=tuple((r.record_id,r.content_sha256) for r in requests),
        bound_reference_sha256='a'*64,cost_bound_attested=True)
    fields.update(kw);return ModelCallBudget(**fields)


_CRASH=r'''
import os,sys
from pathlib import Path
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres

def factory(_):os._exit(86)
with ProjectPostgres(Path(sys.argv[1])).session() as research:
    batch=research.inspect_research_batch(batch_id='crash-batch')
    research.run_budgeted_research(request=batch.stored.batch.requests[0],budget_id='crash-budget',
        model_factory=factory,allow_model_calls=True)
raise SystemExit(99)
'''


@pytest.mark.skipif(not ENABLED,reason='explicit native budget reservation proof is opt-in')
def test_budget_upgrade_shared_cap_replay_and_lost_process(tmp_path,monkeypatch):
    prefix=Path(os.environ['POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX'])
    for key in tuple(os.environ):
        if key.upper().startswith('PG'):monkeypatch.delenv(key)
    parent=tmp_path
    if os.name=='nt':
        parent=Path(os.environ['RUNNER_TEMP'])/('pal-budget-'+uuid.uuid4().hex)
        files.private_directory(parent,create=True)
    root=parent/'Budget With Spaces';root.mkdir()
    (root/'pyproject.toml').write_text('[project]\nname="polymarket-alpha-lab"\n')
    shutil.copytree(ROOT/'database',root/'database')
    shutil.copytree(ROOT/'supabase/migrations',root/'supabase/migrations')
    manifest=json.loads((root/'database/migrations.lock.json').read_text())
    # Keep this historical upgrade proof at65->66. New-tail coverage has its
    # own isolated test, not a replacement of this older migration contract.
    for later in manifest['migrations'][66:]:
        (root/'supabase/migrations'/later['name']).unlink()
    manifest=dict(manifest,migrations=manifest['migrations'][:66])
    assert len(manifest['migrations'])==66 and manifest['migrations'][-1]['name']==TAIL
    (root/'supabase/migrations'/TAIL).unlink()
    (root/'database/migrations.lock.json').write_text(json.dumps(dict(manifest,migrations=manifest['migrations'][:-1])))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    import_runtime_directory(root,prefix);db=ProjectPostgres(root)
    try:
        assert db.initialize(port=port)['migrations_applied']==65
        with db.session() as s:
            old=s.run_research(request=prepared(800),model_factory=lambda _:Model())
            assert old.record.run.research.status=='completed'
            identity=db._state()
        shutil.copyfile(ROOT/'supabase/migrations'/TAIL,root/'supabase/migrations'/TAIL)
        (root/'database/migrations.lock.json').write_text(json.dumps(manifest))
        assert db.migrate()['migrations_applied']==1 and db.migrate()['migrations_applied']==0
        made=[];calls=[];lock=Lock()
        class CountedModel(Model):
            def complete(self,**kw):
                with lock:calls.append(1)
                return super().complete(**kw)
        def factory(team):
            with lock:made.append(team)
            return CountedModel()
        def forbidden(_):pytest.fail('model callback must stay unused')
        with db.session() as s:
            assert s.inspect(record_id=old.request.record_id).record==old.record
            requests=(prepared(801),prepared(802,'crypto_btc'))
            p=policy('first-budget',requests)
            with pytest.raises(ValueError):s.create_model_budget(policy=p)
            assert s.inspect_model_budget(budget_id=p.budget_id) is None
            with ThreadPoolExecutor(max_workers=4) as pool:
                copies=list(pool.map(lambda _:s.create_model_budget(policy=p,allow_budget_write=True),range(8)))
            assert all(x==copies[0] for x in copies)
            with pytest.raises(store.db.ResearchCaptureConflict):
                s.create_model_budget(policy=replace(p,max_calls=3),allow_budget_write=True)
            first=s.run_budgeted_research(request=requests[0],budget_id=p.budget_id,model_factory=factory,allow_model_calls=True)
            assert first.record.run.research.status=='completed' and len(calls)==3 and len(made)==1
            snap=s.inspect_model_budget(budget_id=p.budget_id)
            assert snap.reserved_calls==3 and snap.reserved_micros==300
            assert snap.to_dict()['available_call_reservations']==0  # 50 units cannot cover a 100-unit call.
            again=s.run_budgeted_research(request=requests[0],budget_id=p.budget_id,model_factory=forbidden,allow_model_calls=True)
            assert again.record==first.record and len(calls)==3
            with pytest.raises(ValueError,match='not_available'):
                s.run_budgeted_research(request=requests[1],budget_id=p.budget_id,model_factory=forbidden,allow_model_calls=True)
            assert s.inspect(record_id=requests[1].record_id) is None
            assert s.create_model_budget(policy=p,allow_budget_write=True)==copies[0]
            # Multiple original execution loops share one cap through the rotation.
            rs=tuple(prepared(810+i,'crypto_btc' if i%2 else 'crypto_eth') for i in range(4))
            q=policy('shared-budget',rs,total_micros=450,max_calls=4)
            s.create_model_budget(policy=q,allow_budget_write=True)
            a=ResearchBatch('budget-A',rs[::2]);b=ResearchBatch('budget-B',rs[1::2])
            for batch in (a,b):s.enqueue_research_batch(batch=batch,allow_queue_write=True)
            before=len(calls)
            rotated=s.run_research_rotation(rotation_id='budget-rotation',turn_id='t1',
                batch_ids_to_run=('budget-A','budget-B'),model_factory=factory,allow_model_calls=True,
                max_tasks=4,max_workers=4,model_budget_id=q.budget_id)
            assert rotated.status=='dispatched'
            assert s.inspect_model_budget(budget_id=q.budget_id).reserved_calls==4 and len(calls)-before==4
            repeat=s.run_research_rotation(rotation_id='budget-rotation',turn_id='t1',
                batch_ids_to_run=('budget-A','budget-B'),model_factory=forbidden,allow_model_calls=True,
                max_tasks=4,max_workers=4,model_budget_id=q.budget_id)
            assert repeat.status=='turn_already_reserved' and len(calls)-before==4
            # A known commit followed by a lost acknowledgement burns one permit,
            # does not construct the provider, and is not later refunded.
            u=prepared(820);uncertain=policy('uncertain-budget',(u,))
            s.create_model_budget(policy=uncertain,allow_budget_write=True)
            original=guarded._reserve_call
            def commit_then_fail(*a,**kw):
                assert original(*a,**kw) is True
                raise RuntimeError('synthetic-lost-acknowledgement')
            monkeypatch.setattr(guarded,'_reserve_call',commit_then_fail)
            result=s.run_budgeted_research(request=u,budget_id=uncertain.budget_id,model_factory=forbidden,allow_model_calls=True)
            assert result.record.run.research.status=='failed'
            assert s.inspect_model_budget(budget_id=uncertain.budget_id).reserved_calls==1
            monkeypatch.setattr(guarded,'_reserve_call',original)
            # Policy expiry is checked before any NEW task claim or client.
            e=prepared(830);deadline=datetime.now(UTC)+timedelta(seconds=10)
            expiring=policy('expires',(e,),expires_at=deadline)
            s.create_model_budget(policy=expiring,allow_budget_write=True)
            time.sleep(max(0,(deadline-datetime.now(UTC)).total_seconds()+0.05))
            with pytest.raises(ValueError,match='not_available'):
                s.run_budgeted_research(request=e,budget_id='expires',model_factory=forbidden,allow_model_calls=True)
            assert s.inspect(record_id=e.record_id) is None
            assert s.create_model_budget(policy=expiring,allow_budget_write=True).policy==expiring
            # Direct SQL cannot exceed aggregate limits, alter policy, refund,
            # change source hashes or issue permits for unclaimed/completed work.
            for table in ('model_budgets','model_call_reservations'):
                for sql in (f'UPDATE research_capture.{table} SET readonly=true',
                            f'DELETE FROM research_capture.{table}',f'TRUNCATE research_capture.{table}'):
                    for owner in (False,True):
                        with pytest.raises(files.ProjectDatabaseError):db._psql(identity,sql,owner=owner)
            def raw_permit(dsn,**kw):
                return store.db._local_transaction(dsn,lambda c:c.execute(
                    'INSERT INTO research_capture.model_call_reservations '
                    '(budget_id,record_id,request_sha256,call_number,message_sha256,message_bytes,max_output_tokens) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s)',tuple(kw.values())))
            for budget_id,r,number in ((p.budget_id,requests[0],4),(p.budget_id,requests[1],1),
                                      ('expires',e,1),(q.budget_id,rs[0],32)):
                with pytest.raises(RuntimeError):s._call(raw_permit,budget_id=budget_id,record_id=r.record_id,
                    request_sha256=r.content_sha256,call_number=number,message_sha256='b'*64,message_bytes=10,max_output_tokens=1)
            # A static per-call output mismatch must leave the original request
            # unclaimed. The same batch can still execute a compatible peer.
            bad = prepared(850)
            bad = replace(bad, limits=replace(bad.limits, max_output_tokens=2048))
            peer = prepared(851, 'crypto_btc')
            reviewed = policy('output-preflight', (bad, peer), total_micros=1000, max_calls=10)
            stored_preflight = s.create_model_budget(policy=reviewed, allow_budget_write=True)
            assert s.inspect(record_id=bad.record_id) is None
            with pytest.raises(ValueError, match='^research_budget_output_limit_incompatible$'):
                s.run_budgeted_research(request=bad, budget_id=reviewed.budget_id,
                    model_factory=forbidden, allow_model_calls=True)
            assert s.inspect(record_id=bad.record_id) is None
            zero = s.inspect_model_budget(budget_id=reviewed.budget_id)
            assert zero.stored == stored_preflight and (zero.reserved_calls, zero.reserved_micros) == (0, 0)
            created = []
            def probe_factory(team):
                created.append(team)
                return Model()
            s.enqueue_research_batch(batch=ResearchBatch('output-batch', (bad, peer)), allow_queue_write=True)
            mixed = s.run_research_batch(batch_id='output-batch', model_factory=probe_factory,
                allow_model_calls=True, max_workers=1, model_budget_id=reviewed.budget_id)
            assert [a.status for a in mixed.attempts] == ['operation_failed', 'returned']
            assert mixed.attempts[1].execution.record.run.research.status == 'completed'
            assert created == ['crypto_btc'] and s.inspect(record_id=bad.record_id) is None
            used = s.inspect_model_budget(budget_id=reviewed.budget_id)
            assert used.stored == stored_preflight and (used.reserved_calls, used.reserved_micros) == (3, 300)
            original_payload = bad.payload
            approved = replace(reviewed, budget_id='output-compatible', max_output_tokens=2048)
            s.create_model_budget(policy=approved, allow_budget_write=True)
            completed = s.run_budgeted_research(request=bad, budget_id=approved.budget_id,
                model_factory=probe_factory, allow_model_calls=True)
            assert completed.record.run.research.status == 'completed' and bad.payload == original_payload
            assert created == ['crypto_btc', bad.intake.team_id]
            assert s.inspect_model_budget(budget_id=approved.budget_id).reserved_calls == 3
            # The earlier incompatible policy still has capacity, but cannot
            # turn an existing result into a new claim or another model call.
            replay = s.run_budgeted_research(request=bad, budget_id=reviewed.budget_id,
                model_factory=forbidden, allow_model_calls=True)
            assert replay.record == completed.record
            assert s.inspect_model_budget(budget_id=reviewed.budget_id).reserved_calls == 3
            print('native budget output preflight: PASS; no claim or permit on mismatch, compatible peer, same-request explicit budget, immutable replay')
            crash_requests=(prepared(840),prepared(841,'crypto_btc'))
            crash_policy=policy('crash-budget',crash_requests,total_micros=400,max_calls=4)
            s.create_model_budget(policy=crash_policy,allow_budget_write=True)
            s.enqueue_research_batch(batch=ResearchBatch('crash-batch',crash_requests),allow_queue_write=True)
        child=subprocess.run([sys.executable,'-I','-c',_CRASH,str(root)],capture_output=True,text=True,
            encoding='utf-8',env=files.clean_environment(),timeout=120)
        assert child.returncode==86,(child.stdout,child.stderr)
        db.down()
        with db.session() as s:
            observed=s.inspect_model_budget(budget_id='crash-budget')
            assert observed.reserved_calls==1 and observed.reserved_micros==100
            original_claim=s.inspect(record_id=crash_requests[0].record_id)
            assert original_claim.status=='incomplete'
            narrow = policy('incomplete-output-preflight', (crash_requests[0],), max_output_tokens=1)
            s.create_model_budget(policy=narrow, allow_budget_write=True)
            assert s.run_budgeted_research(request=crash_requests[0], budget_id=narrow.budget_id,
                model_factory=forbidden, allow_model_calls=True) == original_claim
            assert s.inspect_model_budget(budget_id=narrow.budget_id).reserved_calls == 0
            replay=s.run_budgeted_research(request=crash_requests[0],budget_id='crash-budget',
                model_factory=forbidden,allow_model_calls=True)
            reread=s.inspect_model_budget(budget_id='crash-budget')
            assert replay==original_claim and reread.stored==observed.stored
            assert (reread.reserved_calls,reread.reserved_micros)==(1,100)
            # A NEW pending request consumes the remaining three permits, not a
            # resubmission of the interrupted call/claim.
            last=s.run_research_batch(batch_id='crash-batch',model_factory=factory,allow_model_calls=True,
                max_workers=1,model_budget_id='crash-budget')
            assert len(last.attempts)==1 and last.attempts[0].execution.record.run.research.status=='completed'
            assert s.inspect_model_budget(budget_id='crash-budget').reserved_calls==4
            assert s.inspect(record_id=crash_requests[0].record_id)==original_claim
            assert s.inspect(record_id=old.request.record_id).record==old.record
            with pytest.raises(store.db.ResearchCaptureConflict,match='history_incomplete'):s.evaluate()
            assert db._psql(identity,'SELECT count(*) FROM project_private.migrations;')=='66'
        assert db.status()['instance_id']==identity['instance_id'] and db.status()['status']=='stopped'
        print('native model budget: PASS; 65-to-66 preservation, shared caps, no refunds, crash remains incomplete')
    finally:
        if db.status()['status']!='stopped':db.down()
