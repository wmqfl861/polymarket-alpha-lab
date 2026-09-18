"""Actual original forecast -> retained candidate -> reviewed settlement -> evaluation.

Fresh private native PostgreSQL, synthetic original inputs and model; no HTTP.
The original forecast is captured BEFORE a real UTC minute and the proof waits
for that minute to finish. No mocked database clock or backdated capture.
"""
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
import time
import uuid

import pytest

from polymarket_alpha_lab.project_postgres import files
from polymarket_alpha_lab.project_postgres.runtime import import_runtime_directory
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_resolution import IndependentResolutionConfirmation, ResolutionSubmission
from polymarket_alpha_lab.research_resolution_codec import encode_resolution
from polymarket_alpha_lab.research_resolution_confirmation import CryptoSettlementReview
from polymarket_alpha_lab.team_research_agent_types import ResearchModelReply, ResearchToolCall
from polymarket_alpha_lab.team_research_intake import GammaMarketSnapshot
from tests.test_research_resolution_confirmation import request, input_bytes

ROOT=Path(__file__).resolve().parents[1]
ENABLED=os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES')=='1'


class Model:
    def __init__(self):self.n=0
    def complete(self,**kw):
        self.n+=1
        if self.n==1:name,args='read_evidence',dict(source_id='source')
        else:name,args='finish_research',dict(probability_yes='0.5',confidence='0.5',source_ids=['source'],summary='synthetic')
        return ResearchModelReply((ResearchToolCall(str(self.n),name,json.dumps(args)),),1)


@pytest.mark.skipif(not ENABLED,reason='explicit native original-forecast confirmation proof is opt-in')
def test_original_forecasts_manual_confirmation_replay_restart_and_preservation(tmp_path,monkeypatch):
    prefix=Path(os.environ['POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX'])
    for key in tuple(os.environ):
        if key.upper().startswith('PG'):monkeypatch.delenv(key)
    parent=tmp_path
    if os.name=='nt':
        parent=Path(os.environ['RUNNER_TEMP'])/('pal-confirm-'+uuid.uuid4().hex)
        files.private_directory(parent,create=True)
    root=parent/'Confirmation With Spaces';root.mkdir()
    (root/'pyproject.toml').write_text('[project]\nname="polymarket-alpha-lab"\n')
    shutil.copytree(ROOT/'database',root/'database')
    shutil.copytree(ROOT/'supabase/migrations',root/'supabase/migrations')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    import_runtime_directory(root,prefix);db=ProjectPostgres(root)
    try:
        assert db.initialize(port=port)['migrations_applied']==67
        with db.session() as session:
            identity=db._state()
            now=datetime.now(UTC)
            opening=now.replace(second=0,microsecond=0)+timedelta(minutes=1)
            if (opening-now).total_seconds()<25:opening+=timedelta(minutes=1)
            rows=[]
            for n,team in enumerate(('crypto_btc','crypto_eth')):
                req,raw=request(team,at=datetime.now(UTC),opening=opening,
                    cid='0x'+format(2100+n,'064x'),record_id='settlement-'+str(n))
                execution=session.run_research(request=req,model_factory=lambda _:Model())
                assert execution.record.run.research.status=='completed'
                assert execution.record.recorded_at < req.forecast_cutoff_at < opening
                rows.append((req,raw,execution.record))
            # Wait for the actual minute to finish. Do not alter clocks/cutoffs
            # or relabel future observations to reduce the wait.
            time.sleep(max(0,(opening+timedelta(minutes=1)-datetime.now(UTC)).total_seconds())+0.05)
            reviewed=[]
            for n,(req,raw,record) in enumerate(rows):
                yes=n==0
                raw.update(closed=True,acceptingOrders=False,umaResolutionStatus='resolved',
                    outcomePrices=['1','0'] if yes else ['0','1'])
                now=datetime.now(UTC)
                sub=ResolutionSubmission('candidate-'+req.record_id,req.intake.condition_id,
                    GammaMarketSnapshot(req.intake.market_slug,now,json.dumps(raw).encode()),now)
                candidate=session.record_resolution(submission=sub)
                proof=IndependentResolutionConfirmation(req.intake.condition_id,req.intake.market_slug,yes,
                    opening+timedelta(minutes=1),datetime.now(UTC),sub.snapshot.content_sha256,
                    'synthetic-human','https://data.binance.vision/synthetic-proof',
                    'Synthetic independently reviewed source. Not live data.',independently_verified=True)
                instruction=CryptoSettlementReview('confirmed-'+req.record_id,req.record_id,req.content_sha256,
                    sub.review_id,sha256(encode_resolution(sub).encode()).hexdigest(),proof,
                    'binance','BTCUSDT' if n==0 else 'ETHUSDT','1m','close',opening)
                with pytest.raises(ValueError):session.confirm_crypto_resolution(instruction=instruction)
                with pytest.raises(ValueError):session.confirm_crypto_resolution(
                    instruction=replace(instruction,source_interval='1h'),allow_resolution_write=True)
                assert session.inspect_resolution(review_id=instruction.review_id) is None
                reviewed.append((instruction,candidate,None,record))
        # The managed session holds an exclusive lifecycle lease. A child must
        # run AFTER that session releases it; never relax the production lock.
        for n,(instruction,candidate,_,record) in enumerate(reviewed):
            child=subprocess.run([sys.executable,'-I',str(ROOT/'scripts/review_resolution_queue.py'),
                '--root',str(root),'--confirm','--allow-resolution-write'],input=input_bytes(instruction),
                capture_output=True,env=files.clean_environment(),timeout=120)
            assert child.returncode==0,(child.stdout,child.stderr)
            output=json.loads(child.stdout)
            assert output['result']['linked_outcome']['actual_yes'] is (n==0)
            assert 'Synthetic independently reviewed' not in child.stdout.decode()
            with db.session() as session:
                confirmed=session.inspect_resolution(review_id=instruction.review_id)
                assert session.confirm_crypto_resolution(instruction=instruction,allow_resolution_write=True)==confirmed
                assert session.inspect_resolution(review_id=instruction.candidate_review_id)==candidate
                assert session.inspect(record_id=instruction.record_id).record==record
                with pytest.raises(ValueError):session.confirm_crypto_resolution(
                    instruction=replace(instruction,review_id='replacement-'+instruction.record_id),allow_resolution_write=True)
            reviewed[n]=(instruction,candidate,confirmed,record)
        # Replay the already-confirmed ORIGINAL inputs through the real writer.
        # Only adapter-owned arguments are changed AFTER the actual DB returns.
        binding_probe = r"""
import runpy, sys
from polymarket_alpha_lab import research_resolution_confirmation as core
from polymarket_alpha_lab.project_postgres.research import ProjectResearchSession
from polymarket_alpha_lab.research_resolution_codec import encode_resolution
writer = core.record_resolution_review_with_psycopg
confirm = ProjectResearchSession.confirm_crypto_resolution
counts = {'writer': 0, 'adapter': 0}
def changing_writer(dsn, *, submission):
    counts['writer'] += 1
    approved = encode_resolution(submission)
    receipt = writer(dsn, submission=submission)
    assert encode_resolution(receipt.submission) == approved
    object.__setattr__(submission.confirmation, 'reviewer_id', 'synthetic-post-store-change')
    assert encode_resolution(submission) != approved
    return receipt
def changing_adapter(self, *, instruction, allow_resolution_write=False):
    counts['adapter'] += 1
    receipt = confirm(self, instruction=instruction, allow_resolution_write=allow_resolution_write)
    object.__setattr__(instruction, 'review_id', 'synthetic-post-adapter-change')
    object.__setattr__(instruction.confirmation, 'source_text', 'synthetic post-adapter text')
    return receipt
core.record_resolution_review_with_psycopg = changing_writer
ProjectResearchSession.confirm_crypto_resolution = changing_adapter
sys.argv = sys.argv[1:]
try:
    runpy.run_path(sys.argv[0], run_name='__main__')
finally:
    core.record_resolution_review_with_psycopg = writer
    ProjectResearchSession.confirm_crypto_resolution = confirm
    assert counts == {'writer': 1, 'adapter': 1}
"""
        from polymarket_alpha_lab.research_resolution_inspection_cli import resolution_review_summary
        for instruction, candidate, confirmed, record in reviewed:
            replay = subprocess.run([sys.executable, '-I', '-c', binding_probe,
                str(ROOT/'scripts/review_resolution_queue.py'), '--root', str(root),
                '--confirm', '--allow-resolution-write'], input=input_bytes(instruction),
                capture_output=True, env=files.clean_environment(), timeout=120, check=False)
            assert replay.returncode == 0 and replay.stderr == b''
            assert json.loads(replay.stdout)['result'] == resolution_review_summary(
                confirmed, review_id=instruction.review_id)
            assert b'synthetic-post-' not in replay.stdout
            with db.session() as session:
                assert session.inspect_resolution(review_id=instruction.review_id) == confirmed
                assert session.inspect_resolution(review_id=instruction.candidate_review_id) == candidate
                assert session.inspect(record_id=instruction.record_id).record == record
        print('native confirmation approved binding: PASS; BTC/ETH original receipts survive adapter and writer argument changes, one replay each')
        with db.session() as session:
            # Use the ORIGINAL evaluator, not a second scoring implementation.
            evaluation=session.evaluate()
            assert len(evaluation.records)==2 and len(evaluation.outcomes)==2
            assert all(row.reason_code=='scored' for row in evaluation.decisions)
        db.down()
        with db.session() as session:
            for instruction,candidate,confirmed,record in reviewed:
                assert session.confirm_crypto_resolution(instruction=instruction,allow_resolution_write=True)==confirmed
                assert session.inspect_resolution(review_id=instruction.candidate_review_id)==candidate
                assert session.inspect(record_id=instruction.record_id).record==record
            assert db._psql(identity,'SELECT count(*) FROM research_capture.resolution_reviews;')=='4'
            assert db._psql(identity,'SELECT count(*) FROM research_capture.outcomes;')=='2'
            assert db._psql(identity,'SELECT count(*) FROM project_private.migrations;')=='67'
        # Recovery lookups use real isolated storage; inject ONLY a short stdout
        # sink in actual command processes after all prospective captures finish.
        probe = r"""
import json, runpy, sys
from pathlib import Path
source, root, script, flag, identifier = sys.argv[1:]
original = sys.stdout
class ShortOutput:
    def write(self, text):
        value = json.loads(text)
        assert value['status'] == 'inspected' and value['inspection'] is not None
        assert value['business_writes_performed'] is False
        return original.buffer.write(text[:10].encode('ascii'))
    def flush(self): original.flush()
path = Path(source) / 'scripts' / script
sys.argv = [str(path), '--root', root, flag, identifier]
sys.stdout = ShortOutput()
try:
    runpy.run_path(str(path), run_name='__main__')
finally:
    sys.stdout = original
"""
        instruction, candidate, confirmed, record = reviewed[0]
        for script, flag, identifier in (
            ('inspect_project_research.py', '--record-id', instruction.record_id),
            ('inspect_project_resolution.py', '--review-id', instruction.review_id),
        ):
            argv = [sys.executable, '-I', str(ROOT/'scripts'/script),
                    '--root', str(root), flag, identifier]
            before = subprocess.run(argv, capture_output=True,
                env=files.clean_environment(), timeout=120, check=False)
            assert before.returncode == 0 and before.stderr == b''
            original_lookup = json.loads(before.stdout)
            assert original_lookup['status'] == 'inspected'
            failed = subprocess.run([sys.executable, '-I', '-c', probe,
                str(ROOT), str(root), script, flag, identifier],
                capture_output=True, env=files.clean_environment(), timeout=120, check=False)
            assert failed.returncode == 1 and failed.stderr == b''
            assert failed.stdout == (json.dumps(original_lookup, ensure_ascii=True,
                                    allow_nan=False, indent=2)+'\n').encode('ascii')[:10]
            assert db.status()['status'] == 'stopped'
            after = subprocess.run(argv, capture_output=True,
                env=files.clean_environment(), timeout=120, check=False)
            assert after.returncode == 0 and after.stderr == b''
            assert after.stdout == before.stdout
        with db.session() as session:
            assert session.inspect(record_id=instruction.record_id).record == record
            assert session.inspect_resolution(review_id=instruction.review_id) == confirmed
            assert session.inspect_resolution(review_id=instruction.candidate_review_id) == candidate
            assert db._psql(identity,'SELECT count(*) FROM research_capture.resolution_reviews;') == '4'
            assert db._psql(identity,'SELECT count(*) FROM research_capture.outcomes;') == '2'
        assert db.status()['status'] == 'stopped'
        print('native recovery lookup output: PASS; execution/review short writes fail, identical rereads, original records retained')
        assert db.status()['instance_id']==identity['instance_id']
        print('native forecast confirmation: PASS; prospective BTC/ETH, original candidate, manual outcome, replay/restart, no replacement')
    finally:
        if db.status()['status']!='stopped':db.down()
