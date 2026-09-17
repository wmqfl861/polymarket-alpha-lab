"""Real native inventory queries and concurrent visibility; synthetic research only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import uuid

import pytest

from polymarket_alpha_lab import research_capture_psycopg as capture
from polymarket_alpha_lab import research_execution_psycopg as execution
from polymarket_alpha_lab import research_execution_inventory as inventory
from polymarket_alpha_lab.project_postgres import files
from polymarket_alpha_lab.project_postgres.runtime import import_runtime_directory
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_execution import CapturedResearchRequest
from polymarket_alpha_lab.team_research_agent_types import ResearchEvidence
from polymarket_alpha_lab.team_research_intake import GammaMarketSnapshot, prepare_team_research_from_gamma
from tests.test_research_execution import Model

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'


@pytest.mark.skipif(not ENABLED, reason='explicit native execution inventory proof is opt-in')
def test_native_inventory_snapshot_limits_legacy_and_incomplete_visibility(tmp_path, monkeypatch):
    prefix = Path(os.environ['POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX'])
    for key in tuple(os.environ):
        if key.upper().startswith('PG'): monkeypatch.delenv(key)
    parent = tmp_path
    if os.name == 'nt':
        parent = Path(os.environ['RUNNER_TEMP']) / ('pal-inventory-' + uuid.uuid4().hex)
        files.private_directory(parent, create=True)
    root = parent / 'Execution Inventory Project'; root.mkdir()
    (root / 'pyproject.toml').write_text('[project]\nname="polymarket-alpha-lab"\n')
    shutil.copytree(ROOT / 'database', root / 'database')
    shutil.copytree(ROOT / 'supabase/migrations', root / 'supabase/migrations')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    import_runtime_directory(root, prefix)
    db = ProjectPostgres(root); db.initialize(port=port)
    cutoff = datetime.now(UTC) + timedelta(hours=1)
    calls = []

    def request(rid):
        now = datetime.now(UTC)
        body = json.dumps(dict(slug='inventory-fixture', conditionId='inventory-condition',
            question='Synthetic?', description='Synthetic inventory proof only.', active=True,
            closed=False, outcomes=['Yes', 'No'], endDate=(now+timedelta(days=1)).isoformat())).encode()
        evidence = ResearchEvidence('s', 'crypto_eth', 'inventory-condition', 'Synthetic',
            'PRIVATE-SYNTHETIC-INVENTORY-TEXT', 'synthetic:inventory', now)
        intake = prepare_team_research_from_gamma(GammaMarketSnapshot('inventory-fixture', now, body),
            task_id=rid, team_id='crypto_eth', condition_id='inventory-condition', as_of=now, evidence=(evidence,))
        return CapturedResearchRequest(rid, 'synthetic-model', 'inventory-v1', cutoff, intake)

    def factory(team): calls.append(team); return Model()
    def failed_factory(team): calls.append(team); raise RuntimeError('synthetic-private-factory-error')

    try:
        with db.session() as session:
            assert session.execution_inventory().to_dict()['inventory_status'] == 'no_claims'
            ready = session.run_research(request=request('complete'), model_factory=factory)
            failed = session.run_research(request=request('failed'), model_factory=failed_factory)
            assert ready.status == failed.status == 'captured' and len(calls) == 2
            # A legacy standalone attempt is counted, not silently included as a claim.
            standalone = session._call(capture.capture_research_with_psycopg, record_id='standalone',
                model_id='synthetic-model', protocol_version='standalone-v1', run=ready.record.run)
            pending_request = request('pending')
            original_stats = inventory._stats
            written = []

            def interleave(cursor, query, at):
                result = original_stats(cursor, query, at)
                if not written and 'request_payload' in query:
                    # Commit a new claim on another connection after the read's
                    # count query; the remaining inventory must keep its snapshot.
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        written.append(pool.submit(session._call, execution._claim,
                            request=pending_request).result(timeout=30)[1])
                return result

            with monkeypatch.context() as scoped:
                scoped.setattr(inventory, '_stats', interleave)
                earlier = session.execution_inventory().to_dict()
            assert len(written) == 1 and written[0].status == 'incomplete'
            assert earlier['claim_count'] == earlier['captured_result_count'] == 2
            assert earlier['incomplete_claim_count'] == 0 and earlier['unclaimed_attempt_count'] == 1
            current = session.execution_inventory().to_dict()
            assert current['claim_count'] == 3 and current['captured_result_count'] == 2
            assert current['incomplete_claim_count'] == 1 and current['unclaimed_attempt_count'] == 1
            assert current['inventory_status'] == 'incomplete_claims_present'
            assert [r['record_id'] for r in current['executions']] == ['complete', 'failed', 'pending']
            with pytest.raises(capture.ResearchCaptureConflict, match='inventory_limit'):
                session.execution_inventory(max_records=2)
            with monkeypatch.context() as scoped:
                scoped.setattr(capture, 'MAX_READ_BYTES', 1)
                with pytest.raises(capture.ResearchCaptureConflict, match='inventory_limit'):
                    session.execution_inventory()
            with pytest.raises(capture.ResearchCaptureConflict, match='history_incomplete'):
                session.evaluate()
            info = db._state()
        assert db.status()['status'] == 'stopped'
        # The real CLI also reads all tasks with no caller ID, preserves records,
        # and refuses to emit a truncated success under a smaller bound.
        script = ROOT / 'scripts/list_project_research.py'
        for cap, code in ((1000, 0), (2, 1)):
            run = subprocess.run([sys.executable, '-I', str(script), '--root', str(root),
                '--max-records', str(cap)], capture_output=True, text=True, encoding='utf-8',
                timeout=120, env=files.clean_environment())
            assert run.returncode == code, run.stdout + run.stderr
            assert run.stderr == '' and 'PRIVATE-SYNTHETIC' not in run.stdout
            output = json.loads(run.stdout)
            assert output['public_network_called'] is output['live_model_called'] is output['business_writes_performed'] is False
            if code:
                assert output['status'] == 'blocked' and output['inventory'] is None
            else:
                listed = output['inventory']; listed.pop('generated_at')
                expected = dict(current); expected.pop('generated_at')
                assert listed == expected
            assert db.status()['status'] == 'stopped' and db.status()['instance_id'] == info['instance_id']
        # Real read-only commands must not report successful delivery after a
        # short stdout write. Only the output sink is injected; DB/lifecycle and
        # the selected historical snapshot are real and unchanged.
        probe = r'''
import json, runpy, sys
script, expected = sys.argv[1:3]
arguments = sys.argv[3:]
original = sys.stdout
class ShortOutput:
    def write(self, text):
        assert json.loads(text)['status'] == expected
        return original.buffer.write(text[:8].encode('ascii'))
    def flush(self):
        original.flush()
sys.stdout = ShortOutput()
sys.argv = [script, *arguments]
runpy.run_path(script, run_name='__main__')
'''
        historical = ['--as-of', earlier['generated_at']]
        for name, options, expected_status in (
            ('list_project_research.py', [], 'listed'),
            ('inspect_project_research.py', ['--record-id', 'complete'], 'inspected'),
            ('inspect_project_resolution.py', ['--review-id', 'output-probe-missing'], 'review_not_found'),
            ('evaluate_project_research.py', historical, 'evaluated'),
            ('evaluate_project_research.py', ['--settled-paper', *historical], 'evaluated'),
        ):
            result = subprocess.run([sys.executable, '-I', '-c', probe,
                str(ROOT / 'scripts' / name), expected_status, '--root', str(root), *options],
                capture_output=True, timeout=120, env=files.clean_environment(),
                cwd=parent, stdin=subprocess.DEVNULL)
            assert result.returncode == 1, (name, result.returncode, result.stdout, result.stderr)
            assert len(result.stdout) == 8 and result.stdout.startswith(b'{\n  "')
            assert result.stderr == b''
            assert db.status()['status'] == 'stopped' and db.status()['instance_id'] == info['instance_id']
        with db.session() as session:
            assert session.inspect(record_id='complete').record == ready.record
            assert session.inspect(record_id='failed').record == failed.record
            assert session.inspect(record_id='pending').status == 'incomplete'
            assert db._psql(info, 'SELECT count(*) FROM research_capture.execution_claims;', owner=False) == '3'
            assert db._psql(info, 'SELECT count(*) FROM research_capture.attempts;', owner=False) == '3'
            assert db._psql(info, 'SELECT count(*) FROM research_capture.outcomes;', owner=False) == '0'
            with pytest.raises(capture.ResearchCaptureConflict, match='history_incomplete'): session.evaluate()
        assert len(calls) == 2 and db.status()['status'] == 'stopped'
        print('native readonly output: PASS; five real commands, short output refused, original records and stopped engine retained')
        print('native execution inventory: PASS; one snapshot across concurrent commit, bounded reads and unchanged records')
    finally:
        if db.status()['status'] != 'stopped': db.down()
