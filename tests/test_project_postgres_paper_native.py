"""Real native PostgreSQL read-only paper replay after synthetic confirmations."""
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
from polymarket_alpha_lab.research_capture_psycopg import ResearchCaptureConflict
from polymarket_alpha_lab.research_resolution import IndependentResolutionConfirmation, ResolutionSubmission
from polymarket_alpha_lab.research_resolution_codec import encode_resolution
from polymarket_alpha_lab.research_resolution_confirmation import CryptoSettlementReview
from polymarket_alpha_lab.team_research_agent_types import ResearchModelReply, ResearchToolCall
from polymarket_alpha_lab.team_research_intake import GammaMarketSnapshot
from tests.test_research_paper_evaluation import request, scenario, raw_input, PRIVATE

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'


class Model:
    def __init__(self, probability): self.n, self.probability = 0, probability
    def complete(self, **kw):
        self.n += 1
        if self.n == 1: name, args = 'read_evidence', dict(source_id='source')
        else: name, args = 'finish_research', dict(probability_yes=self.probability,
            confidence='0.9', source_ids=['source'], summary='synthetic')
        return ResearchModelReply((ResearchToolCall(str(self.n), name, json.dumps(args)),), 1)


@pytest.mark.skipif(not ENABLED, reason='explicit isolated native paper replay proof is opt-in')
def test_replay_original_research_costs_outcomes_no_writes_and_incomplete_gate(tmp_path, monkeypatch):
    prefix = Path(os.environ['POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX'])
    for key in tuple(os.environ):
        if key.upper().startswith('PG'): monkeypatch.delenv(key)
    parent = tmp_path
    if os.name == 'nt':
        parent = Path(os.environ['RUNNER_TEMP']) / ('pal-paper-' + uuid.uuid4().hex)
        files.private_directory(parent, create=True)
    root = parent / 'Paper Replay With Spaces'; root.mkdir()
    (root/'pyproject.toml').write_text('[project]\nname="polymarket-alpha-lab"\n')
    shutil.copytree(ROOT/'database', root/'database')
    shutil.copytree(ROOT/'supabase/migrations', root/'supabase/migrations')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    import_runtime_directory(root, prefix); db = ProjectPostgres(root)
    try:
        assert db.initialize(port=port)['migrations_applied'] == 66
        with db.session() as session:
            identity = db._state()
            now = datetime.now(UTC)
            opening = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            if (opening-now).total_seconds() < 30: opening += timedelta(minutes=1)
            originals, scenarios, markets = [], [], []
            for n, team, probability in ((10, 'crypto_btc', '.8'), (11, 'crypto_eth', '.1')):
                req, raw = request(n, team, at=datetime.now(UTC), opening=opening)
                e = session.run_research(request=req, model_factory=lambda _, p=probability: Model(p))
                assert e.record.run.research.status == 'completed'
                originals.append(e); markets.append(raw)
                scenarios.append(scenario(e, raw, at=max(datetime.now(UTC), e.record.recorded_at)))
            req, _ = request(12, at=datetime.now(UTC), opening=opening)
            def failed(_): raise RuntimeError('synthetic model failure')
            failed_record = session.run_research(request=req, model_factory=failed).record
            assert failed_record.run.research.status == 'failed'
            scenarios = tuple(scenarios)
            before = db._psql(identity, 'SELECT (SELECT count(*) FROM research_capture.attempts), '
                '(SELECT count(*) FROM research_capture.execution_claims), '
                '(SELECT count(*) FROM research_capture.outcomes);')
            pending = session.evaluate_paper(scenarios=scenarios).to_dict()
            assert pending['counts'] == {'excluded_research': 1, 'simulated_pending_outcome': 2}
            assert all(row['scenario']['payout'] is None for row in pending['scenarios'] if row['scenario'])
            assert before == db._psql(identity, 'SELECT (SELECT count(*) FROM research_capture.attempts), '
                '(SELECT count(*) FROM research_capture.execution_claims), '
                '(SELECT count(*) FROM research_capture.outcomes);')
            # Real wall-clock ordering. No fabricated early settlement, backdated
            # record, mocked database time or shortened observation minute.
            time.sleep(max(0, (opening+timedelta(minutes=1)-datetime.now(UTC)).total_seconds()) + .05)
            for n, (e, raw) in enumerate(zip(originals, markets)):
                yes = n == 0
                raw = dict(raw, closed=True, acceptingOrders=False, umaResolutionStatus='resolved',
                    outcomePrices=['1','0'] if yes else ['0','1'])
                now = datetime.now(UTC)
                sub = ResolutionSubmission('paper-candidate-'+str(n), e.request.intake.condition_id,
                    GammaMarketSnapshot(e.request.intake.market_slug, now, json.dumps(raw).encode()), now)
                candidate = session.record_resolution(submission=sub)
                proof = IndependentResolutionConfirmation(sub.condition_id, sub.snapshot.market_slug, yes,
                    opening+timedelta(minutes=1), max(datetime.now(UTC), candidate.recorded_at),
                    sub.snapshot.content_sha256, 'synthetic-human', 'https://example.org/synthetic-settlement',
                    'Synthetic evidence, not a real venue observation.', independently_verified=True)
                instruction = CryptoSettlementReview('paper-confirmed-'+str(n), e.request.record_id,
                    e.request.content_sha256, sub.review_id, sha256(encode_resolution(sub).encode()).hexdigest(),
                    proof, 'binance', 'BTCUSDT' if n == 0 else 'ETHUSDT', '1m', 'close', opening)
                session.confirm_crypto_resolution(instruction=instruction, allow_resolution_write=True)
            evaluated = session.evaluate_paper(scenarios=scenarios).to_dict()
            assert evaluated['counts'] == {'excluded_research': 1, 'simulated_with_outcome': 2}
            samples = [row['scenario'] for row in evaluated['scenarios'] if row['scenario']]
            assert [row['selected_side'] for row in samples] == ['yes','no']
            assert [row['payout'] for row in samples] == ['3','3']
            old = session.inspect(record_id=originals[0].request.record_id).record
            assert old == originals[0].record
            assert session.inspect(record_id=failed_record.record_id).record == failed_record
            assert session.evaluate_paper(scenarios=()).to_dict()['counts'] == {'excluded_research':1,'missing_scenario':2}
        # No parent managed lease remains while the actual console child opens.
        payload = json.dumps([json.loads(raw_input(s))[0] for s in scenarios]).encode()
        child = subprocess.run([sys.executable,'-I',str(ROOT/'scripts/evaluate_project_research.py'),
            '--root',str(root),'--paper-scenarios'],input=payload,capture_output=True,
            env=files.clean_environment(),timeout=120,cwd=parent)
        assert child.returncode == 0, (child.stdout,child.stderr)
        result = json.loads(child.stdout)
        assert result['history_gate'] == 'complete_visible_execution_claims'
        assert result['evaluation']['counts'] == evaluated['counts']
        assert PRIVATE not in child.stdout.decode() and result['business_writes_performed'] is False
        db.down()
        with db.session() as session:
            replayed = session.evaluate_paper(scenarios=scenarios).to_dict()
            assert replayed['scenarios'] == evaluated['scenarios']
            assert session.inspect(record_id=originals[0].request.record_id).record == old
            assert db._psql(identity,'SELECT count(*) FROM research_capture.resolution_reviews;') == '4'
            assert db._psql(identity,'SELECT count(*) FROM research_capture.attempts;') == '3'
            assert db._psql(identity,'SELECT count(*) FROM research_capture.outcomes;') == '2'
            # A genuinely committed new claim loses its result. The same strict
            # history gate blocks this new evaluation, even with no scenarios.
            future = datetime.now(UTC) + timedelta(minutes=5)
            req, _ = request(13, at=datetime.now(UTC), opening=future.replace(second=0,microsecond=0))
            def interrupted(_): raise KeyboardInterrupt()
            with pytest.raises(KeyboardInterrupt): session.run_research(request=req, model_factory=interrupted)
            assert session.inspect(record_id=req.record_id).status == 'incomplete'
            with pytest.raises(ResearchCaptureConflict, match='history_incomplete'):
                session.evaluate_paper(scenarios=())
        child = subprocess.run([sys.executable,'-I',str(ROOT/'scripts/evaluate_project_research.py'),
            '--root',str(root),'--paper-scenarios'],input=b'[]',capture_output=True,
            env=files.clean_environment(),timeout=120,cwd=parent)
        assert child.returncode == 1 and json.loads(child.stdout)['history_gate'] == 'not_established'
        assert db.status()['instance_id'] == identity['instance_id']
        print('native paper replay: PASS; two-team depth/cost/outcome, strict history, no replay writes, preserved originals')
    finally:
        if db.status()['status'] != 'stopped': db.down()
