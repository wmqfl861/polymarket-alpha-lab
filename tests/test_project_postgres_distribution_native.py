"""Actual Windows kit build -> extraction -> first startup -> retained research.

Uses only a trusted runner binary prefix and fresh, owned disposable directories.
The tested ZIP contains no pre-initialized instance or credentials. No Docker,
Windows database service, paid models or public API calls are made by the proof.
"""
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import uuid
import zipfile

import pytest

from polymarket_alpha_lab.project_postgres import distribution
from polymarket_alpha_lab.project_postgres.files import clean_environment, private_directory
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_execution import CapturedResearchRequest
from polymarket_alpha_lab.team_research_agent_types import ResearchEvidence, ResearchModelReply, ResearchToolCall
from polymarket_alpha_lab.team_research_intake import GammaMarketSnapshot, prepare_team_research_from_gamma

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_DISTRIBUTION') == '1'


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def extract(archive, parent):
    parent.mkdir()
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            distribution.safe_name(item.filename)
            assert item.filename.startswith(distribution.TOP + '/') and not item.is_dir()
            target = parent / item.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(item))
    return parent / distribution.TOP


def install_environment(root):
    uv = shutil.which('uv')
    assert uv is not None
    # All required packages were cached by the workflow's locked setup. Prove
    # that the extracted metadata can create its OWN venv, without network.
    result = subprocess.run([uv, 'sync', '--locked', '--offline', '--extra', 'postgres',
        '--python', sys.executable], cwd=root, env=clean_environment(),
        stdin=subprocess.DEVNULL, capture_output=True, timeout=120, text=True,
        encoding='utf-8', check=False, shell=False)
    if result.returncode:
        pytest.fail('extracted dependency installation failed: ' + result.stderr[-3000:])
    assert (root / '.venv/Scripts/python.exe').is_file()


_SCOPE_BINDING_PROBE = r'''
from polymarket_alpha_lab.research_dispatch import ResearchBatch
from polymarket_alpha_lab.research_dispatch_store import load_research_batch_with_psycopg
from polymarket_alpha_lab.research_dispatch_runner import ResearchDispatchStop, run_research_batch_with_psycopg
from polymarket_alpha_lab.research_model_budget import ModelCallBudget, decode_budget
from polymarket_alpha_lab.research_model_budget_runner import run_budgeted_research_with_psycopg
from datetime import UTC, datetime, timedelta
budget = ModelCallBudget('packaged-budget','synthetic','not-a-selected-model','USD',100,10,10,
    1000,32,datetime(2030,1,1,tzinfo=UTC),(('task','a'*64),),'b'*64,cost_bound_attested=True)
assert decode_budget(budget.payload,budget.content_sha256) == budget
from polymarket_alpha_lab.research_dispatch_rotation import selection
from polymarket_alpha_lab.research_dispatch_rotation_runner import run_research_rotation_with_psycopg
assert selection(('pending','pending','pending'), 2, 2) == ((2,0),1,2)
stop = ResearchDispatchStop()
assert not stop.is_stopped()
stop.request_stop()
assert stop.is_stopped()
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from polymarket_alpha_lab.research_crypto_observation import _new_york
from polymarket_alpha_lab.team_research_agent_types import ResearchEvidence, TeamResearchResult
from polymarket_alpha_lab.team_research_intake import GammaMarketSnapshot, prepare_team_research_from_gamma
from polymarket_alpha_lab.team_research_market_pipeline import MarketTeamResearchRun
from polymarket_alpha_lab.research_capture_codec import encode_research_capture
z, _ = _new_york()
at = datetime(2026, 11, 1, 5, 30, tzinfo=UTC).astimezone(z)
raw = json.dumps(dict(slug='scope-fixture', conditionId='scope-fixture', question='Synthetic?',
    description='Synthetic fixture only.', active=True, closed=False, outcomes=['Yes','No'],
    endDate=(at.astimezone(UTC)+timedelta(days=1)).isoformat())).encode()
ev = ResearchEvidence('s','crypto_eth','scope-fixture','Synthetic','Synthetic','synthetic:source',at)
i = prepare_team_research_from_gamma(GammaMarketSnapshot('scope-fixture',at,raw),
    task_id='scope-fixture',team_id='crypto_eth',condition_id='scope-fixture',as_of=at,evidence=(ev,))
r = TeamResearchResult(i.task_id,i.team_id,i.condition_id,i.market_slug,at,'failed','model_failed')
original = MarketTeamResearchRun(i,r)
for target in ('task','receipt','result'):
    def build(when):
        if target == 'task':
            return MarketTeamResearchRun(replace(i,task=replace(i.task,as_of=when)),r)
        if target == 'receipt':
            return MarketTeamResearchRun(replace(i,source_receipts=(replace(i.source_receipts[0],observed_at=when),)),r)
        return MarketTeamResearchRun(i,replace(r,as_of=when))
    valid = build(at.astimezone(UTC))
    def wire(run):
        return encode_research_capture(record_id='scope-fixture',model_id='not-called',protocol_version='fixture',run=run)
    assert wire(valid) == wire(original)
    try:
        build(at.replace(fold=1))
    except ValueError:
        pass
    else:
        raise AssertionError('different instant accepted')
print('packaged scope bindings: PASS; three edges, canonical wire retained, no model or DB')
'''

_DIAGNOSTIC_START = r'''
import json, pathlib, re, runpy, sys
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / 'src'))
from polymarket_alpha_lab.project_postgres import files
original = files.subprocess.run

def safe(value):
    return re.sub(r'[a-fA-F0-9]{64}', '<redacted>', value or '').replace(str(root), '<project>').replace(root.as_posix(), '<project>')[-2500:]

def probe(args, **kwargs):
    result = original(args, **kwargs)
    name = pathlib.Path(args[0]).stem
    if result.returncode not in (0, 3) and name in ('postgres', 'initdb', 'pg_ctl', 'psql', 'pg_controldata'):
        print(json.dumps({'program': name, 'exit': result.returncode, 'stdout': safe(result.stdout), 'stderr': safe(result.stderr)}), file=sys.stderr)
    return result
files.subprocess.run = probe
sys.argv = [str(root / 'scripts/start_project.py'), *sys.argv[2:]]
runpy.run_path(sys.argv[0], run_name='__main__')
'''


def start(root, *args, expected=0):
    # The wrapper only diagnoses failed child commands in this synthetic project;
    # it runs the packaged entry point unchanged and never prints SQL/stdin/argv.
    result = subprocess.run([str(root / '.venv/Scripts/python.exe'), '-I', '-c', _DIAGNOSTIC_START, str(root), *args],
        env=clean_environment(), stdin=subprocess.DEVNULL, capture_output=True,
        timeout=240, text=True, encoding='utf-8', check=False, shell=False)
    # Production startup emits only fixed public codes, never child stderr/DSNs.
    if result.returncode != expected:
        pytest.fail(f'startup exit={result.returncode}; stdout={result.stdout}; stderr={result.stderr}')
    return json.loads(result.stdout if expected == 0 else result.stderr)


class Model:
    def __init__(self): self.calls = 0
    def complete(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            name, args = 'read_evidence', {'source_id': 's'}
        else:
            name, args = 'finish_research', dict(probability_yes='0.6', confidence='0.2',
                summary='Synthetic distribution acceptance only.', source_ids=['s'])
        return ResearchModelReply((ResearchToolCall(f'c-{self.calls}', name, json.dumps(args)),), 1)


def request():
    now = datetime.now(UTC)
    snapshot = GammaMarketSnapshot('kit-fixture', now, json.dumps(dict(slug='kit-fixture',
        conditionId='kit-condition', question='Synthetic?', description='Synthetic criterion',
        outcomes=['Yes', 'No'], active=True, closed=False, endDate=(now + timedelta(days=1)).isoformat())).encode())
    evidence = ResearchEvidence('s', 'crypto_eth', 'kit-condition', 'Synthetic source',
        'Synthetic text', 'synthetic:kit-proof', now)
    intake = prepare_team_research_from_gamma(snapshot, task_id='kit-task', team_id='crypto_eth',
        condition_id='kit-condition', as_of=now, evidence=(evidence,))
    return CapturedResearchRequest('kit-record', 'synthetic-model', 'kit-proof-v1',
        now + timedelta(hours=1), intake, required_source_ids=('s',))


@pytest.mark.skipif(not ENABLED, reason='explicit native Windows distribution acceptance is opt-in')
def test_build_and_run_actual_relocatable_kit(monkeypatch):
    assert os.name == 'nt'
    prefix = Path(os.environ['POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX'])
    output = Path(os.environ['POLYMARKET_ALPHA_LAB_DISTRIBUTION_OUTPUT'])
    for key in tuple(os.environ):
        if key.upper().startswith('PG'): monkeypatch.delenv(key)
    proof = Path(os.environ['RUNNER_TEMP']) / ('pal-kit-' + uuid.uuid4().hex)
    private_directory(proof, create=True)
    print('native distribution: build clean committed source and native engine', flush=True)
    receipt = distribution.build_distribution(ROOT, prefix, output)
    print(json.dumps(receipt), flush=True)
    first = extract(output, proof / 'First Extraction With Spaces')
    second = extract(output, proof / 'Second Extraction With Spaces')
    for root in (first, second):
        distribution.verify_distribution(root)
        assert not (root / '.local').exists() and not (root / 'runtime').exists()
        assert not (root / '.env').exists() and not (root / '.git').exists()
        install_environment(root)
        # Use the packaged script/source, an unrelated cwd and a foreign
        # editable-path decoy; --help must not initialize either private root.
        from tests.project_entry_root_probe import ENTRYPOINTS, probe_entry
        for entry in ENTRYPOINTS:
            own = probe_entry(root, root / '.venv/Scripts/python.exe',
                root.parent / ('source-probe-' + entry), script=entry)
            assert own.returncode == 0, own.stdout + own.stderr
            assert 'PROJECT_ENTRY_SOURCE_OK' in own.stderr and '--root' in own.stdout
            assert not (root / '.local').exists()
        # The shipped operator CLI is inert without public opt-in and needs no DB.
        cli = root / 'scripts/discover_crypto_research.py'
        assert cli.is_file() and (root / 'scripts/download_handoff.ps1').is_file()
        disabled = subprocess.run([str(root / '.venv/Scripts/python.exe'), '-I', str(cli),
            '--team', 'crypto_btc', '--preview'], capture_output=True, text=True,
            encoding='utf-8', timeout=30, check=True, env=clean_environment())
        assert json.loads(disabled.stdout)['status'] == 'disabled'
        selected = subprocess.run([str(root / '.venv/Scripts/python.exe'), '-I', str(cli),
            '--team', 'crypto_btc', '--select-supported', '--max-candidates', '2'],
            capture_output=True, text=True, encoding='utf-8', timeout=30, check=True, env=clean_environment())
        inert = json.loads(selected.stdout)
        assert inert['status'] == 'disabled' and inert['public_gets_upper_bound'] == 0
        assert inert['model_called'] is inert['database_written'] is False
        assert (root / 'src/polymarket_alpha_lab/research_crypto_selection.py').is_file()
        assert not (root / '.local').exists()
        evaluation_cli = root / 'scripts/evaluate_project_research.py'
        assert evaluation_cli.is_file() and (root / 'docs/research-evaluation-console.md').is_file()
        help_result = subprocess.run([str(root / '.venv/Scripts/python.exe'), '-I', str(evaluation_cli), '--help'],
            capture_output=True, text=True, encoding='utf-8', timeout=30, check=True, env=clean_environment())
        assert '--include-decisions' in help_result.stdout
        assert '--settled-paper' in help_result.stdout
        inspection_cli = root / 'scripts/inspect_project_research.py'
        assert inspection_cli.is_file() and (root / 'docs/research-execution-inspection.md').is_file()
        inspection_help = subprocess.run([str(root / '.venv/Scripts/python.exe'), '-I', str(inspection_cli), '--help'],
            capture_output=True, text=True, encoding='utf-8', timeout=30, check=True, env=clean_environment())
        assert '--record-id' in inspection_help.stdout
        inventory_cli = root / 'scripts/list_project_research.py'
        assert inventory_cli.is_file() and (root / 'docs/research-execution-inventory.md').is_file()
        inventory_help = subprocess.run([str(root / '.venv/Scripts/python.exe'), '-I', str(inventory_cli), '--help'],
            capture_output=True, text=True, encoding='utf-8', timeout=30, check=True, env=clean_environment())
        assert '--max-records' in inventory_help.stdout
        resolution_cli = root / 'scripts/inspect_project_resolution.py'
        assert resolution_cli.is_file() and (root / 'docs/research-resolution-inspection.md').is_file()
        resolution_help = subprocess.run([str(root / '.venv/Scripts/python.exe'), '-I', str(resolution_cli), '--help'],
            capture_output=True, text=True, encoding='utf-8', timeout=30, check=True, env=clean_environment())
        assert '--review-id' in resolution_help.stdout and '--confirm' not in resolution_help.stdout

        # The extracted package owns its timezone dependency; this must work on
        # Windows with no system IANA data and no source-worktree import fallback.
        probe = subprocess.run([str(root / '.venv/Scripts/python.exe'), '-I', '-c',
            'from datetime import datetime; '
            'from polymarket_alpha_lab.research_crypto_observation import _new_york; '
            'z, version = _new_york(); '
            'assert datetime(2026, 1, 15, 12, tzinfo=z).utcoffset().total_seconds() == -18000; '
            'assert datetime(2026, 7, 15, 12, tzinfo=z).utcoffset().total_seconds() == -14400; '
            'print("packaged timezone:", version)'], capture_output=True, text=True,
            encoding='utf-8', timeout=30, check=True, env=clean_environment())
        assert 'packaged timezone:' in probe.stdout
        binding = subprocess.run([str(root / '.venv/Scripts/python.exe'), '-I', '-c',
            _SCOPE_BINDING_PROBE], capture_output=True, text=True, encoding='utf-8',
            timeout=30, check=True, env=clean_environment())
        assert 'packaged scope bindings: PASS' in binding.stdout
        assert not (root / '.local').exists()
    with zipfile.ZipFile(first / distribution.ENGINE) as seed:
        assert all(not name.lower().endswith(distribution.FONT_SUFFIXES) for name in seed.namelist())
        assert not any(name.startswith('pgsql/data/') for name in seed.namelist())
    print('native distribution: launch extracted code; import/init automatic', flush=True)
    created = start(first, '--port', str(port()))
    assert created['status'] == 'ready' and created['initialized_here'] is True
    assert created['recorded_attempts'] == 0 and created['live_model_called'] is False
    db = ProjectPostgres(first)
    credentials = sha256((db.layout.home / 'app.pgpass').read_bytes()).hexdigest()
    try:
        with db.session() as research:
            captured = research.run_research(request=request(), model_factory=lambda _: Model())
            assert captured.status == 'captured'
        repeated = start(first)
        assert repeated['initialized_here'] is False
        assert repeated['instance_id'] == created['instance_id'] and repeated['recorded_attempts'] == 1
        assert sha256((db.layout.home / 'app.pgpass').read_bytes()).hexdigest() == credentials
        assert db.status()['status'] == 'stopped'
        evaluation = subprocess.run([str(first / '.venv/Scripts/python.exe'), '-I',
            str(first / 'scripts/evaluate_project_research.py')], capture_output=True, text=True,
            encoding='utf-8', timeout=120, check=True, env=clean_environment())
        value = json.loads(evaluation.stdout)
        assert value['status'] == 'evaluated' and value['live_model_called'] is False
        assert value['evaluation']['evaluation_status'] == 'no_scored_forecasts'
        assert value['evaluation']['visible_attempt_count'] == 1
        assert value['evaluation']['decision_counts']['outcome_pending'] == 1
        assert db.status()['status'] == 'stopped'
        inspection = subprocess.run([str(first / '.venv/Scripts/python.exe'), '-I',
            str(first / 'scripts/inspect_project_research.py'), '--record-id', 'kit-record'],
            capture_output=True, text=True, encoding='utf-8', timeout=120, check=True, env=clean_environment())
        inspected = json.loads(inspection.stdout)
        assert inspected['status'] == 'inspected' and inspected['live_model_called'] is False
        assert inspected['inspection']['inspection_status'] == 'captured_completed'
        assert inspected['inspection']['record_sha256'] == captured.record.content_sha256
        assert inspected['inspection']['recorded_at'] == captured.record.recorded_at.isoformat()
        assert 'Synthetic distribution acceptance only.' not in inspection.stdout
        assert db.status()['status'] == 'stopped'

        inventory_read = subprocess.run([str(first / '.venv/Scripts/python.exe'), '-I',
            str(first / 'scripts/list_project_research.py')], capture_output=True, text=True,
            encoding='utf-8', timeout=120, check=True, env=clean_environment())
        inventory_report = json.loads(inventory_read.stdout)
        assert inventory_report['status'] == 'listed' and inventory_report['live_model_called'] is False
        listing = inventory_report['inventory']
        assert listing['claim_count'] == listing['captured_result_count'] == 1
        assert listing['incomplete_claim_count'] == listing['unclaimed_attempt_count'] == 0
        assert listing['executions'][0]['record_sha256'] == captured.record.content_sha256
        assert listing['executions'][0]['recorded_at'] == captured.record.recorded_at.isoformat()
        assert 'Synthetic distribution acceptance only.' not in inventory_read.stdout
        assert db.status()['status'] == 'stopped'
        resolution_read = subprocess.run([str(first / '.venv/Scripts/python.exe'), '-I',
            str(first / 'scripts/inspect_project_resolution.py'), '--review-id', 'not-recorded'],
            capture_output=True, text=True, encoding='utf-8', timeout=120, check=False, env=clean_environment())
        assert resolution_read.returncode == 3, (resolution_read.stdout, resolution_read.stderr)
        assert resolution_read.stderr == ''
        resolution_view = json.loads(resolution_read.stdout)
        assert resolution_view['status'] == 'review_not_found' and resolution_view['inspection'] is None
        assert resolution_view['public_network_called'] is resolution_view['live_model_called'] is False
        assert resolution_view['business_writes_performed'] is resolution_view['outcome_confirmation_performed'] is False
        assert db.status()['status'] == 'stopped' and db.status()['instance_id'] == created['instance_id']
        print('native distribution: same kit yields independent project database', flush=True)
        other = start(second, '--port', str(port()))
        assert other['recorded_attempts'] == 0 and other['instance_id'] != created['instance_id']
        other_db = ProjectPostgres(second)
        assert sha256((other_db.layout.home / 'app.pgpass').read_bytes()).hexdigest() != credentials
        assert other_db.status()['status'] == 'stopped'
        # Both extracted environments execute the new option, not just --help.
        for kit_root,expected_count in ((first,1),(second,0)):
            child=subprocess.run([str(kit_root / '.venv/Scripts/python.exe'),'-I',
                str(kit_root / 'scripts/evaluate_project_research.py'),'--settled-paper'],
                cwd=proof,capture_output=True,text=True,encoding='utf-8',timeout=120,
                check=True,env=clean_environment())
            view=json.loads(child.stdout)
            assert child.stderr=='' and view['status']=='evaluated'
            assert view['evaluation']['attempt_count']==expected_count
            assert view['evaluation']['status_counts']['paper_evidence_missing']==expected_count
            assert view['evaluation']['groups']==[] and view['evaluation']['actual_account_pnl'] is None
            assert 'attempts' not in view['evaluation'] and 'decisions' not in view['evaluation']['history']
            assert ProjectPostgres(kit_root).status()['status']=='stopped'
        # Compose the budgeted research -> retained simulation -> reviewed
        # settlement route inside the EMPTY second kit, with its own interpreter.
        # This reviewed recipe is test code only; application imports may NOT
        # come from this checkout. It waits for a real UTC observation close.
        from tests.packaged_research_flow import run_packaged_recipe
        completed = run_packaged_recipe(second, second / '.venv/Scripts/python.exe', proof)
        if completed.returncode != 0:
            # Keep the small synthetic child's actual diagnostic text readable;
            # pytest's tuple repr can truncate the nested failure and its clock.
            pytest.fail(f'packaged recipe exit={completed.returncode}; '
                        f'stdout={completed.stdout}; stderr={completed.stderr}')
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        assert completed.stderr == ''
        composed = json.loads(completed.stdout)
        assert composed['status'] == 'packaged_flow_verified'
        assert composed['source_commit'] == receipt['source_commit']
        assert composed['source_tree'] == receipt['source_tree']
        assert composed['instance_id'] == other['instance_id']
        assert (composed['attempts'], composed['simulations'], composed['settlements'],
                composed['reserved_calls']) == (4, 4, 2, 7)
        assert composed['project_modules_checked'] > 0 and composed['synthetic_inputs'] is True
        assert composed['actual_account_pnl'] is None
        assert composed['incomplete_claims'] == composed['interrupted_reserved_calls'] == 1
        assert composed['confirmation_output_failures'] == 1
        assert composed['same_confirmation_replayed'] is True
        assert composed['admission_receipts'] == 3
        print('packaged research composition: ' + json.dumps(composed, sort_keys=True), flush=True)
        # Only the test's existing first kit: actual lock, PostgreSQL and
        # packaged source; inject a Python close exception, not an OS signal.
        from tests.packaged_session_drain import run_packaged_session_drain
        drained = run_packaged_session_drain(first, first / '.venv/Scripts/python.exe', proof)
        assert drained.returncode == 0, (drained.stdout, drained.stderr)
        assert drained.stderr == ''
        drain_proof = json.loads(drained.stdout)
        assert drain_proof['status'] == 'packaged_session_drain_verified'
        assert drain_proof['cases'] == [dict(borrowed=value, admitted_reads=1,
            original_record_preserved=True) for value in (False, True)]
        assert drain_proof['database_mocked'] is drain_proof['os_signal_sent'] is drain_proof['model_called'] is False
        print('packaged session drain: ' + json.dumps(drain_proof, sort_keys=True), flush=True)
        # Extend this same disposable kit's completed business fixture through
        # its existing cold-backup/restore commands. Originals remain private.
        from tests.packaged_recovery_flow import run_recovery_recipe
        recovery = run_recovery_recipe(second, second / '.venv/Scripts/python.exe', proof,
            historical_at=composed['historical_at'], expected_instance=composed['instance_id'])
        assert recovery.returncode == 0, (recovery.stdout, recovery.stderr)
        assert recovery.stderr == ''
        recovered = json.loads(recovery.stdout)
        assert recovered['status'] == 'packaged_cold_recovery_verified'
        assert recovered['source_tree'] == receipt['source_tree']
        assert recovered['source_commit'] == receipt['source_commit']
        assert recovered['instance_id'] == other['instance_id']
        assert (recovered['captured_attempts'], recovered['simulations'], recovered['settlements'],
                recovered['reserved_calls'], recovered['incomplete_claims']) == (4, 4, 2, 8, 1)
        assert recovered['physical_bytes_preserved'] is recovered['originals_retained'] is True
        assert recovered['history_equal'] is recovered['replay_without_model'] is True
        assert recovered['current_history_blocked'] is recovered['stopped'] is True
        assert recovered['archive_uploaded'] is False and recovered['synthetic_inputs'] is True
        assert recovered['project_modules_checked'] > 0
        print('packaged cold recovery: ' + json.dumps(recovered, sort_keys=True), flush=True)
        # A changed immutable package blocks without modifying stored research.
        target = first / 'src/polymarket_alpha_lab/local_postgres_dsn.py'
        original = target.read_bytes()
        target.write_bytes(original + b'\n# synthetic tamper probe\n')
        try:
            blocked = start(first, expected=1)
            assert blocked['reason_code'] == 'project_bundle_invalid_or_changed'
        finally:
            target.write_bytes(original)
        with db.session() as research:
            assert research.inspect(record_id='kit-record').record == captured.record
        assert start(first)['recorded_attempts'] == 1
    finally:
        db.down()
    print('native distribution: PASS; two fresh instances, restart retention, no models or Docker', flush=True)
