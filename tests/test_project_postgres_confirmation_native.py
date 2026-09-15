"""Actual original forecast -> retained candidate -> reviewed settlement -> evaluation.

Fresh private native PostgreSQL, synthetic original inputs and model; no HTTP.
The original forecast is captured BEFORE a real UTC minute and the proof waits
for that minute to finish. No mocked database clock or backdated capture.
"""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
from tests.test_research_paper import scenario, input_bytes as paper_input, prepared
from polymarket_alpha_lab.research_capture_psycopg import ResearchCaptureConflict

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
        assert db.initialize(port=port)['migrations_applied']==66
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
            paper_scenarios = tuple(replace(scenario(req, dict(raw, acceptingOrders=True,
                clobTokenIds=['yes-'+req.record_id, 'no-'+req.record_id]), at=datetime.now(UTC)),
                min_confidence=Decimal('0.5')) for req,raw,_ in rows)
            pending = session.evaluate_paper(scenarios=paper_scenarios).to_dict()
            assert pending['simulated_count'] == 2
            assert all(x['accounting']['modeled_settled_net'] is None for x in pending['rows'])
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
        with db.session() as session:
            # Use the ORIGINAL evaluator, not a second scoring implementation.
            evaluation=session.evaluate()
            assert len(evaluation.records)==2 and len(evaluation.outcomes)==2
            assert all(row.reason_code=='scored' for row in evaluation.decisions)
        # Test the exact read-only console AFTER releasing the lifecycle lease.
        child = subprocess.run([sys.executable, '-I', str(ROOT/'scripts/evaluate_project_research.py'),
            '--root', str(root), '--paper-stdin'], input=paper_input(paper_scenarios),
            capture_output=True, env=files.clean_environment(), timeout=120)
        assert child.returncode == 0, (child.stdout, child.stderr)
        paper_result = json.loads(child.stdout)['evaluation']
        assert paper_result['simulated_count'] == 2
        assert [Decimal(x['accounting']['modeled_settled_net']) for x in paper_result['rows']] == [Decimal('1.61'), Decimal('-1.39')]
        assert paper_result['forward_test_provenance_established'] is False
        db.down()
        with db.session() as session:
            for instruction,candidate,confirmed,record in reviewed:
                assert session.confirm_crypto_resolution(instruction=instruction,allow_resolution_write=True)==confirmed
                assert session.inspect_resolution(review_id=instruction.candidate_review_id)==candidate
                assert session.inspect(record_id=instruction.record_id).record==record
            assert db._psql(identity,'SELECT count(*) FROM research_capture.resolution_reviews;')=='4'
            assert db._psql(identity,'SELECT count(*) FROM research_capture.outcomes;')=='2'
            assert db._psql(identity,'SELECT count(*) FROM project_private.migrations;')=='66'
            repeated = session.evaluate_paper(scenarios=paper_scenarios).to_dict()
            assert repeated['rows'] == paper_result['rows']
            # A real extra incomplete claim must block ALL strict evaluation,
            # even when the supplied scenarios refer only to completed records.
            now = datetime.now(UTC)
            unfinished, _ = prepared(2199, at=now, opening=now+timedelta(minutes=10))
            def interrupt(_): raise KeyboardInterrupt('synthetic interruption')
            with pytest.raises(KeyboardInterrupt):
                session.run_research(request=unfinished, model_factory=interrupt)
            with pytest.raises(ResearchCaptureConflict, match='history_incomplete'):
                session.evaluate_paper(scenarios=paper_scenarios)
            for instruction,_,_,record in reviewed:
                assert session.inspect(record_id=instruction.record_id).record == record
            print('native research paper: PASS; pending/settled costs, console, restart, global incomplete denial')
        assert db.status()['instance_id']==identity['instance_id']
        print('native forecast confirmation: PASS; prospective BTC/ETH, original candidate, manual outcome, replay/restart, no replacement')
    finally:
        if db.status()['status']!='stopped':db.down()
