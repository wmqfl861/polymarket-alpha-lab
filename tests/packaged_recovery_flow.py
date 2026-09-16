"""Test-only cold recovery of the completed packaged BTC/ETH fixture.

Injected into the extracted kit's own interpreter; never shipped or called on a
user project. The archive includes synthetic database credentials and stays in
the private disposable runner directory, never an uploaded artifact.
"""
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

from polymarket_alpha_lab.project_postgres import backup_format, distribution, files
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_capture_psycopg import ResearchCaptureConflict

RECORDS = tuple('packaged-' + str(n) for n in range(4))
BUDGETS = (('kit-budget', 7, 700), ('interrupted-budget', 1, 100))


def assert_origins(root):
    modules = [module for name, module in sys.modules.items()
               if name == 'polymarket_alpha_lab' or name.startswith('polymarket_alpha_lab.')]
    assert modules, 'no packaged project modules loaded'
    for module in modules:
        assert Path(module.__file__).resolve().is_relative_to(root / 'src'), 'non-kit source loaded'
    return len(modules)


def database_command(root, arguments, *, reason=None):
    """One real packaged database CLI process; negative receipts use stderr."""
    result = subprocess.run([sys.executable, '-I', str(root / 'scripts/project_database.py'),
        '--root', str(root), *arguments], cwd=root.parent, env=files.clean_environment(),
        stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=False, shell=False)
    assert result.returncode == (1 if reason else 0), 'unexpected database command exit'
    assert (result.stdout if reason else result.stderr) == b'', 'unexpected output channel'
    body = json.loads((result.stderr if reason else result.stdout).decode('utf-8'))
    if reason:
        assert body == dict(status='blocked', reason_code=reason), 'unexpected refusal reason'
    return body


def saved_state(db, historical_at):
    """Keep exact original objects in memory; do not export evidence or secrets."""
    with db.session() as session:
        inventory = session.execution_inventory().to_dict()
        assert (inventory['claim_count'], inventory['captured_result_count'],
                inventory['incomplete_claim_count']) == (5, 4, 1), 'unexpected fixture roster'
        executions = tuple(session.inspect(record_id=rid) for rid in (*RECORDS, 'interrupted-0'))
        assert all(value is not None for value in executions), 'missing original execution'
        assert all(value.record is not None for value in executions[:4]), 'missing captured result'
        assert executions[-1].status == 'incomplete' and executions[-1].record is None
        papers = tuple(session.inspect_paper_research(record_id=rid) for rid in RECORDS)
        assert all(value is not None for value in papers), 'missing original simulation'
        assert [value.to_dict()['result']['status'] for value in papers] == [
            'paper_scenario_ready', 'paper_scenario_ready', 'paper_scenario_rejected', 'not_simulated']
        reviews = tuple(session.inspect_resolution(review_id=prefix + rid)
            for rid in RECORDS[:2] for prefix in ('candidate-', 'confirmed-'))
        assert all(value is not None for value in reviews), 'missing candidate or confirmation'
        batches = tuple(session.inspect_research_batch(batch_id=bid) for bid in ('kit-btc', 'kit-eth'))
        turns = tuple(session.inspect_research_turn(rotation_id='kit-rotation', turn_id=tid)
                      for tid in ('one', 'two'))
        assert all(value is not None for value in (*batches, *turns)), 'missing task history'
        budgets = []
        for bid, calls, micros in BUDGETS:
            value = session.inspect_model_budget(budget_id=bid)
            assert value is not None and (value.reserved_calls, value.reserved_micros) == (calls, micros)
            # Only the new observation timestamp is excluded. Stored policy,
            # creation timestamp and exact reserved accounting must match.
            budgets.append((value.stored, value.reserved_calls, value.reserved_micros))
        history = session.evaluate_settled_paper_research(generated_at=historical_at)
        assert history['attempt_count'] == 4 and history['status_counts']['settled_simulation'] == 2
        assert history['actual_account_pnl'] is None
        try:
            session.evaluate_settled_paper_research()
        except ResearchCaptureConflict as error:
            assert str(error) == 'research_execution_history_incomplete'
        else:
            raise AssertionError('current incomplete history was not blocked')
    return dict(executions=executions, papers=papers, reviews=reviews, batches=batches,
                turns=turns, budgets=tuple(budgets), history=history)


def run_recovery(root, historical_at, expected_instance):
    """Only the fresh second fixture kit is eligible; never delete original data."""
    root = Path(root).resolve()
    historical_at = datetime.fromisoformat(historical_at)
    assert historical_at.tzinfo is not None, 'historical cutoff must be explicit'
    manifest = distribution.verify_distribution(root)
    assert_origins(root)
    db = ProjectPostgres(root)
    assert db.status()['status'] == 'stopped', 'recovery fixture must begin stopped'
    identity = db._state()
    assert identity['instance_id'] == expected_instance, 'wrong fixture instance'
    destination = root.parent / 'Packaged Private Cold Backup'
    preserved = root.parent / 'Packaged Original Preserved'
    for path in (destination, preserved):
        files.no_links(path)
        assert not path.exists(), 'recovery fixture destination already exists'
    before = saved_state(db, historical_at)
    assert db.status()['status'] == 'stopped'
    original_inventory = backup_format.inventory(db.layout.home)
    copied = database_command(root, ['backup', '--destination', str(destination)])
    assert copied['status'] == 'backup_created' and copied['source_stopped'] is True
    assert copied['contains_credentials'] is True and copied['encrypted'] is False
    archive = destination / 'snapshot.palpg.zip'
    checksum = files.digest_file(archive)
    assert copied['sha256'] == checksum, 'backup receipt does not match actual bytes'
    unchanged = backup_format.inventory(db.layout.home) == original_inventory
    assert unchanged, 'cold backup changed original files'
    arguments = ['--archive', str(archive), '--sha256', checksum, '--trusted-backup']
    verified = database_command(root, ['verify-backup', *arguments])
    assert verified['status'] == 'backup_verified' and verified['database_started'] is False
    database_command(root, ['restore', *arguments], reason='project_postgres_restore_existing_data_refused')
    assert backup_format.inventory(db.layout.home) == original_inventory
    assert not (db.layout.private / 'postgres.restoring').exists()
    # Simulate unavailable files in this DISPOSABLE fixture only. Retain the
    # complete original privately; no deletion, copy of .local, or path adoption.
    with db.layout.lock():
        db.layout.home.rename(preserved)
    assert not db.layout.home.exists()
    wrong = ['--archive', str(archive), '--sha256', '0' * 64, '--trusted-backup']
    database_command(root, ['restore', *wrong], reason='project_postgres_backup_checksum_mismatch')
    assert not db.layout.home.exists() and not (db.layout.private / 'postgres.restoring').exists()
    restored = database_command(root, ['restore', *arguments])
    assert restored == dict(status='restored_stopped', sha256=checksum,
                            existing_data_overwritten=False, database_started=False)
    assert db.status()['status'] == 'stopped' and db._state() == identity
    exact_copy = backup_format.inventory(db.layout.home) == original_inventory
    original_kept = backup_format.inventory(preserved) == original_inventory
    assert exact_copy and original_kept, 'recovered or preserved physical bytes differ'
    after = saved_state(db, historical_at)
    assert before == after, 'recovered business history differs'
    def forbidden(_):
        raise AssertionError('recovered replay must not construct a model')
    with db.session() as session:
        for original in before['executions']:
            bid = 'interrupted-budget' if original.status == 'incomplete' else 'kit-budget'
            replay = session.run_budgeted_research(request=original.request, budget_id=bid,
                model_factory=forbidden, allow_model_calls=True)
            assert replay.record == original.record and replay.claimed_at == original.claimed_at
            if original.status == 'incomplete':
                assert replay == original
        for paper in before['papers']:
            assert session.capture_paper_research(scenario=paper.scenario, allow_paper_write=True) == paper
    assert saved_state(db, historical_at) == before, 'replay changed recovered evidence or budget'
    assert db.status()['status'] == 'stopped' and db._state() == identity
    assert files.digest_file(archive) == checksum, 'backup archive changed'
    assert backup_format.inventory(preserved) == original_inventory, 'original copy changed'
    assert distribution.verify_distribution(root) == manifest
    count = assert_origins(root)
    return dict(status='packaged_cold_recovery_verified', source_commit=manifest['source_commit'],
        source_tree=manifest['source_tree'], instance_id=identity['instance_id'],
        physical_bytes_preserved=True, originals_retained=True, stopped=True,
        captured_attempts=4, simulations=4, resolution_reviews=4, settlements=2,
        batches=2, turns=2, reserved_calls=8, incomplete_claims=1,
        history_equal=True, replay_without_model=True, current_history_blocked=True,
        archive_uploaded=False, synthetic_inputs=True, project_modules_checked=count)


def run_recovery_recipe(root, python, cwd, *, historical_at, expected_instance):
    """Inject this fixed TEST recipe, with a separate bounded child, no retry."""
    prefix = ('import sys\nfrom pathlib import Path\nroot=Path(sys.argv[1]).resolve()\n'
        "assert (root/'src/polymarket_alpha_lab/__init__.py').is_file(), 'kit source missing'\n"
        "sys.path.insert(0,str(root/'src'))\n")
    recipe = Path(__file__).read_text(encoding='utf-8')
    return subprocess.run([str(python), '-I', '-c', prefix + recipe +
        '\nprint(json.dumps(run_recovery(root, sys.argv[2], sys.argv[3]),sort_keys=True))\n',
        str(root), historical_at, expected_instance], cwd=cwd, env=files.clean_environment(),
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8',
        timeout=180, check=False, shell=False)
