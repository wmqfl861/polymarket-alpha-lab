"""Real67->68 DB upgrade + pinned CLI/mock HTTP + original-ID recovery.

Disposable CI project only. No real account, provider, market, order or user DB.
"""
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import uuid

import pytest

from polymarket_alpha_lab import research_dispatch_cli as cli
from polymarket_alpha_lab import research_uncapped_store as store
from polymarket_alpha_lab.project_postgres import files
from polymarket_alpha_lab.project_postgres.runtime import import_runtime_directory
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_codex_protocol import VERSION
from polymarket_alpha_lab.research_dispatch import ResearchBatch
from polymarket_alpha_lab.research_dispatch_runner import ResearchDispatchStop
from polymarket_alpha_lab.research_uncapped_authorization import UncappedModelAuthorization
from tests.test_codex_wire_native import native_profile, synthetic_server
from tests.test_project_postgres_dispatch_native import prepared
from tests.test_project_postgres_budget_native import policy as capped_policy
from tests.test_team_research_cross_source import Model

ROOT=Path(__file__).resolve().parents[1]
ENABLED=(os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES')=='1'
         and bool(os.environ.get('POLYMARKET_ALPHA_LAB_TEST_CODEX_BINARY')))
TAIL='20260919000000_research_uncapped_codex.sql'

_CRASH=r'''
import os,sys
from pathlib import Path
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_codex_local import read_profile
from polymarket_alpha_lab import research_uncapped_runner as runner

def crash(*a,**kw):os._exit(86)
runner.invoke_codex=crash
with ProjectPostgres(Path(sys.argv[1])).session() as s:
    request=s.inspect_research_batch(batch_id='crash-batch').stored.batch.requests[0]
    s.run_uncapped_research(request=request,authorization_id='crash-auth',profile=read_profile(sys.argv[2]),allow_model_calls=True)
raise SystemExit(99)
'''


def authorization(p, rs, identifier='first-uncapped'):
    return UncappedModelAuthorization(identifier,p.model_id,p.content_sha256,
        datetime.now(UTC)+timedelta(hours=1),tuple((r.record_id,r.content_sha256) for r in rs),uncapped_cost_approved=True)


@pytest.mark.skipif(not ENABLED,reason='explicit native DB and pinned synthetic Codex proof')
def test_uncapped_upgrade_native_cli_rotation_failure_and_crash(tmp_path,monkeypatch,capsys):
    prefix=Path(os.environ['POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX'])
    for key in tuple(os.environ):
        if key.upper().startswith('PG'):monkeypatch.delenv(key)
    parent=tmp_path
    if os.name=='nt':
        parent=Path(os.environ['RUNNER_TEMP'])/('pal-uncapped-'+uuid.uuid4().hex)
        files.private_directory(parent,create=True)
    root=parent/'Uncapped Project';root.mkdir()
    (root/'pyproject.toml').write_text('[project]\nname="polymarket-alpha-lab"\n')
    shutil.copytree(ROOT/'database',root/'database');shutil.copytree(ROOT/'supabase/migrations',root/'supabase/migrations')
    manifest_path=root/'database/migrations.lock.json';manifest=json.loads(manifest_path.read_text())
    assert len(manifest['migrations'])==68 and manifest['migrations'][-1]['name']==TAIL
    (root/'supabase/migrations'/TAIL).unlink()
    manifest_path.write_text(json.dumps(dict(manifest,migrations=manifest['migrations'][:-1])))
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    import_runtime_directory(root,prefix);db=ProjectPostgres(root)
    profile_root=parent/'Local CLI';profile_root.mkdir();p=native_profile(profile_root,'synthetic-function-model')
    body=asdict(p)
    for key in ('executable','codex_home','workspace_parent'):body[key]=str(body[key])
    profile_file=profile_root/'profile.json';files.write_private(profile_file,json.dumps(body))
    def command(args, input_text=None, stop=None):
        capsys.readouterr()
        with monkeypatch.context() as m:
            if input_text is not None:
                m.setattr(sys,'stdin',io.TextIOWrapper(io.BytesIO(input_text.encode()),encoding='utf-8'))
            code=cli.main(['--root',str(root),*args],default_root=root,stop=stop)
        output=json.loads(capsys.readouterr().out)
        return code,output
    try:
        assert db.initialize(port=port)['migrations_applied']==67
        with db.session() as s:
            old_request=prepared(9700);budget=capped_policy('old-capped',(old_request,))
            s.create_model_budget(policy=budget,allow_budget_write=True)
            old=s.run_budgeted_research(request=old_request,budget_id=budget.budget_id,
                                       model_factory=lambda _:Model(),allow_model_calls=True)
            assert old.record.run.research.status=='completed'
            old_budget=s.inspect_model_budget(budget_id=budget.budget_id)
        shutil.copyfile(ROOT/'supabase/migrations'/TAIL,root/'supabase/migrations'/TAIL)
        manifest_path.write_text(json.dumps(manifest))
        assert db.migrate()['migrations_applied']==1 and db.migrate()['migrations_applied']==0
        rs=tuple(replace(prepared(9710+i,'crypto_btc' if i%2==0 else 'crypto_eth'),protocol_version=VERSION) for i in range(3))
        a=authorization(p,rs);batch=ResearchBatch('uncapped-batch',rs)
        with db.session() as s:
            assert s.inspect(record_id=old_request.record_id).record==old.record
            assert s.inspect_model_budget(budget_id=budget.budget_id).reserved_micros==old_budget.reserved_micros==300
            s.enqueue_research_batch(batch=batch,allow_queue_write=True)
        code,out=command(['create-uncapped','--authorization-id',a.authorization_id,'--input-sha256',a.content_sha256,
                          '--allow-authorization-write'],a.payload)
        assert code==0 and out['result']['monetary_cap'] is None
        created=out['result']['created_at']
        with db.session() as s:
            assert s.inspect_uncapped_authorization(authorization_id=a.authorization_id).reserved_invocations==0
        common=['run-turn','--rotation-id','uncapped-rotation','--batch-id',batch.batch_id,'--authorization-id',a.authorization_id,
                '--codex-profile',str(profile_file),'--max-tasks','1','--max-workers','1','--allow-model-calls']
        with synthetic_server(monkeypatch) as seen:
            first_code,first=command([*common,'--turn-id','one'])
            assert first_code==0 and first['status']=='dispatched' and len(seen)==3
            second_code,second=command([*common,'--turn-id','two'])
            assert second_code==0 and second['status']=='dispatched' and len(seen)==6
            again_code,again=command([*common,'--turn-id','one'])
            assert again_code==0 and again['status']=='turn_already_reserved' and len(seen)==6
            stopped=ResearchDispatchStop();stopped.request_stop()
            stop_code,_=command([*common,'--turn-id','not-reserved'],stop=stopped)
            assert stop_code==130 and len(seen)==6
            assert all(method=='POST' and route=='/v1/responses' and b['tools']==[] for method,route,b in seen)
        with synthetic_server(monkeypatch,'500') as failures:
            failure_code,failure=command([*common,'--turn-id','three'])
            assert failure_code==1 and failure['status']=='dispatched' and len(failures)==1
        code,out=command(['inspect-uncapped','--authorization-id',a.authorization_id])
        assert code==0
        assert (out['result']['reserved_invocations'],out['result']['validated_turns'],out['result']['failed_invocations'])==(7,6,1)
        assert out['result']['reported_total_tokens']==720 and out['result']['actual_billed_micros'] is None
        with db.session() as s:
            first_records=[s.inspect(record_id=r.record_id) for r in rs]
            assert [x.record.run.research.status for x in first_records]==['completed','completed','failed']
            assert s.inspect_research_turn(rotation_id='uncapped-rotation',turn_id='not-reserved') is None
            for r,record in zip(rs,first_records):
                assert s.run_uncapped_research(request=r,authorization_id=a.authorization_id,profile=p,allow_model_calls=True).record==record.record
            for table in ('uncapped_model_authorizations','codex_invocation_outcomes','model_call_reservations'):
                for sql in (f'DELETE FROM research_capture.{table}',f'TRUNCATE research_capture.{table}'):
                    with pytest.raises(RuntimeError):
                        s._call(lambda dsn:store.db._local_transaction(dsn,lambda c:c.execute(sql)))
            cr=replace(prepared(9720),protocol_version=VERSION);ca=authorization(p,(cr,),'crash-auth')
            s.enqueue_research_batch(batch=ResearchBatch('crash-batch',(cr,)),allow_queue_write=True)
            s.create_uncapped_authorization(policy=ca,allow_authorization_write=True)
        child=subprocess.run([sys.executable,'-c',_CRASH,str(root),str(profile_file)],cwd=ROOT,
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
        assert child.returncode==86, 'owned synthetic crash did not reach reserved invocation'
        with db.session() as s:
            incomplete=s.inspect(record_id=cr.record_id)
            assert incomplete.status=='incomplete'
            assert s.run_uncapped_research(request=cr,authorization_id=ca.authorization_id,profile=p,allow_model_calls=True)==incomplete
            snapshot=s.inspect_uncapped_authorization(authorization_id=ca.authorization_id)
            assert snapshot.reserved_invocations==snapshot.to_dict()['unknown_invocations']==1
            # A different priced budget cannot overwrite/use the same original ordinal.
            cb=capped_policy('cannot-refund',(cr,));s.create_model_budget(policy=cb,allow_budget_write=True)
            from polymarket_alpha_lab.research_model_budget_store import _reserve_call
            with pytest.raises(store.db.ResearchCaptureConflict):
                s._call(_reserve_call,policy=cb,request=cr,call_number=1,messages_json='[{"role":"user","content":"test"}]',max_output_tokens=100)
            with pytest.raises(RuntimeError):
                s._call(lambda dsn:store.db._local_transaction(dsn,lambda c:c.execute(
                    'INSERT INTO research_capture.model_call_reservations '
                    '(budget_id,record_id,request_sha256,call_number,message_sha256,message_bytes,max_output_tokens) '
                    'VALUES (%s,%s,%s,2,%s,10,100)',(cb.budget_id,cr.record_id,cr.content_sha256,'a'*64))))
            identity=db._state()
            assert db._psql(identity,'SELECT count(*) FROM project_private.migrations;')=='68'
            assert s.inspect(record_id=old_request.record_id).record==old.record
            assert s.inspect_model_budget(budget_id=budget.budget_id).reserved_micros==300
        code,out=command(['create-uncapped','--authorization-id',a.authorization_id,'--input-sha256',a.content_sha256,
                          '--allow-authorization-write'],a.payload)
        assert code==0 and out['result']['created_at']==created
        assert list(p.workspace_parent.iterdir())==[]
        print('uncapped native: PASS;67->68,original capped history,real CLI/mock HTTP,two rounds,replay,stop,500,no refund,incomplete')
    finally:
        try:db.down()
        except Exception:pass
