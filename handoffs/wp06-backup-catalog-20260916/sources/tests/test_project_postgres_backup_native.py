"""Real cold-backup/recovery proof on a NEW private native Windows cluster.

Only synthetic records are written. Original directories are retained during
simulated loss; no user's database or backup is opened, changed or uploaded.
"""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import shutil
import socket
import time
import uuid

import pytest

from polymarket_alpha_lab.project_postgres.backup import (
    create_cold_backup, restore_cold_backup, verify_cold_backup,
)
from polymarket_alpha_lab.project_postgres.files import ProjectDatabaseError, digest_file, private_directory
from polymarket_alpha_lab.project_postgres.runtime import import_runtime_directory
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from tests.test_project_postgres_native import Model, request, ROOT

ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_BACKUP') == '1'


@pytest.mark.skipif(not ENABLED, reason='explicit private native backup proof is opt-in')
def test_native_cold_backup_recovery_preserves_prospective_history(tmp_path, monkeypatch):
    prefix = Path(os.environ['POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX'])
    for key in tuple(os.environ):
        if key.upper().startswith('PG'):
            monkeypatch.delenv(key)
    if os.name == 'nt':
        base = Path(os.environ['RUNNER_TEMP']) / ('pal-backup-' + uuid.uuid4().hex)
        private_directory(base, create=True)
    else:
        base = tmp_path
    root = base / 'Project With Spaces'
    root.mkdir()
    (root / 'pyproject.toml').write_text('[project]\nname="polymarket-alpha-lab"\n')
    shutil.copytree(ROOT / 'database', root / 'database')
    shutil.copytree(ROOT / 'supabase/migrations', root / 'supabase/migrations')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    import_runtime_directory(root, prefix)
    db = ProjectPostgres(root)
    db.initialize(port=port)
    print('native backup: initialized private cluster with all historical migrations')
    try:
        with db.session() as session:
            cutoff = datetime.now(UTC) + timedelta(seconds=12)
            first = replace(request(101), forecast_cutoff_at=cutoff)
            captured = session.run_research(request=first, model_factory=lambda _: Model())
            assert captured.status == 'captured'
            second = request(102)
            def failed_model(_):
                raise RuntimeError('synthetic model factory failure')
            failed = session.run_research(request=second, model_factory=failed_model)
            assert failed.status == 'captured' and failed.record.run.research.status == 'failed'
            while datetime.now(UTC) <= cutoff:
                time.sleep(0.05)
            session.capture_outcome(condition_id=first.intake.condition_id,
                market_slug=first.intake.market_slug, resolved_at=datetime.now(UTC), actual_yes=True,
                source_reference='synthetic:confirmed-native-backup', source_content_sha256='a'*64)
            at = datetime.now(UTC)
            before = session.evaluate(generated_at=at).to_dict()
        identity = db._state()
        credentials = tuple(digest_file(db.layout.home / n) for n in ('owner.pgpass', 'app.pgpass'))
        destination = base / 'Private Backup'
        receipt = create_cold_backup(root, destination=destination)
        args = dict(archive=destination / 'snapshot.palpg.zip', expected_sha256=receipt['sha256'], trusted_backup=True)
        assert verify_cold_backup(root, **args)['status'] == 'backup_verified'
        assert db.status()['status'] == 'stopped'
        assert db.up()['status'] == 'running'
        with pytest.raises(ProjectDatabaseError, match='requires_clean_shutdown'):
            create_cold_backup(root, destination=base / 'Must Not Exist')
        assert not (base / 'Must Not Exist').exists()
        assert db.status()['status'] == 'running'
        db.down()
        with pytest.raises(ProjectDatabaseError, match='existing_data'):
            restore_cold_backup(root, **args)
        # Simulate unavailable original files WITHOUT deleting any original data.
        db.layout.home.rename(base / 'Original Preserved')
        bad = dict(args, expected_sha256='0'*64)
        with pytest.raises(ProjectDatabaseError, match='checksum_mismatch'):
            restore_cold_backup(root, **bad)
        assert not db.layout.home.exists()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', port)); sock.listen()
            with pytest.raises(ProjectDatabaseError, match='port_in_use'):
                restore_cold_backup(root, **args)
        assert not (db.layout.private / 'postgres.restoring').exists()
        assert restore_cold_backup(root, **args)['status'] == 'restored_stopped'
        assert db.status()['status'] == 'stopped' and db._state() == identity
        credentials_preserved = tuple(digest_file(db.layout.home / n) for n in ('owner.pgpass', 'app.pgpass')) == credentials
        assert credentials_preserved  # No generated credential text in assertions/logs.
        with db.session() as session:
            assert session.inspect(record_id=first.record_id).record == captured.record
            assert session.inspect(record_id=second.record_id).record == failed.record
            assert session.evaluate(generated_at=at).to_dict() == before
            def forbidden(_):
                pytest.fail('completed replay must not call the model')
            assert session.run_research(request=first, model_factory=forbidden).record == captured.record
            with pytest.raises(ProjectDatabaseError):
                db._psql(identity, 'DELETE FROM research_capture.attempts;', owner=False)
            third = request(103)
            def interrupted(_):
                raise KeyboardInterrupt('synthetic interruption')
            with pytest.raises(KeyboardInterrupt):
                session.run_research(request=third, model_factory=interrupted)
            assert session.inspect(record_id=third.record_id).status == 'incomplete'
            with pytest.raises(Exception, match='incomplete'):
                session.evaluate()
        print('native backup: records, outcome, original clocks, score and role restrictions preserved')
        destination2 = base / 'Incomplete History Backup'
        receipt2 = create_cold_backup(root, destination=destination2)
        db.layout.home.rename(base / 'Incomplete Original Preserved')
        restore_cold_backup(root, archive=destination2 / 'snapshot.palpg.zip', expected_sha256=receipt2['sha256'], trusted_backup=True)
        with db.session() as session:
            assert session.inspect(record_id=third.record_id).status == 'incomplete'
            with pytest.raises(Exception, match='incomplete'):
                session.evaluate()
        print('native backup: PASS; incomplete claims remain visible; no reset, SQL replay or Docker')
    finally:
        if db.layout.home.exists():
            db.down()


@pytest.mark.skipif(not ENABLED, reason='explicit private native backup proof is opt-in')
def test_native_old_backup_restore_requires_explicit_catalog_extension_and_migration(tmp_path, monkeypatch):
    """Real 66-entry snapshot -> 67-entry catalog; SQL still requires migrate.

    The original synthetic cluster is preserved, not deleted to force recovery.
    No backup archive, credential or user record is published as CI evidence.
    """
    import json
    import subprocess
    import sys
    from polymarket_alpha_lab.project_postgres.files import clean_environment

    prefix = Path(os.environ['POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX'])
    for key in tuple(os.environ):
        if key.upper().startswith('PG'):
            monkeypatch.delenv(key)
    if os.name == 'nt':
        base = Path(os.environ['RUNNER_TEMP']) / ('pal-backup-catalog-' + uuid.uuid4().hex)
        private_directory(base, create=True)
    else:
        base = tmp_path
    root = base / 'Original Project With Spaces'
    root.mkdir()
    (root / 'pyproject.toml').write_text('[project]\nname="polymarket-alpha-lab"\n')
    shutil.copytree(ROOT / 'database', root / 'database')
    shutil.copytree(ROOT / 'supabase/migrations', root / 'supabase/migrations')
    manifest_file = root / 'database/migrations.lock.json'
    manifest_bytes = manifest_file.read_bytes()
    manifest = json.loads(manifest_bytes)
    tail = '20260915020000_research_paper_simulations.sql'
    assert len(manifest['migrations']) == 67 and manifest['migrations'][-1]['name'] == tail
    (root / 'supabase/migrations' / tail).unlink()
    manifest_file.write_text(json.dumps(dict(manifest, migrations=manifest['migrations'][:-1])))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    import_runtime_directory(root, prefix)
    db = ProjectPostgres(root)
    try:
        assert db.initialize(port=port)['migrations_applied'] == 66
        first, second = request(1801), request(1802)
        with db.session() as session:
            completed = session.run_research(request=first, model_factory=lambda _: Model())
            def fail(_):
                raise RuntimeError('synthetic original failed research')
            failed = session.run_research(request=second, model_factory=fail)
            assert completed.record.run.research.status == 'completed'
            assert failed.record.run.research.status == 'failed'
            before = session.evaluate().to_dict()
        identity = db._state()
        original_credentials = tuple(digest_file(db.layout.home / n) for n in ('owner.pgpass', 'app.pgpass'))
        destination = base / 'Private Original Backup'
        receipt = create_cold_backup(root, destination=destination)
        archive = destination / 'snapshot.palpg.zip'
        args = dict(archive=archive, expected_sha256=receipt['sha256'], trusted_backup=True)
        assert verify_cold_backup(root, **args)['status'] == 'backup_verified'
        # Add the REAL existing 67th migration; no fabricated DDL or timestamp.
        shutil.copyfile(ROOT / 'supabase/migrations' / tail, root / 'supabase/migrations' / tail)
        manifest_file.write_bytes(manifest_bytes)

        def command(action, *, allow=False, expected=0):
            argv = [sys.executable, '-I', str(ROOT / 'scripts/project_database.py'),
                '--root', str(root), action, '--archive', str(archive),
                '--sha256', receipt['sha256'], '--trusted-backup']
            if allow:
                argv.append('--allow-catalog-extension')
            result = subprocess.run(argv, env=clean_environment(), cwd=base,
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                encoding='utf-8', timeout=90, check=False, shell=False)
            assert result.returncode == expected, (result.stdout, result.stderr)
            assert not (result.stderr if expected == 0 else result.stdout)
            return json.loads(result.stdout if expected == 0 else result.stderr)

        assert command('verify-backup', expected=1)['reason_code'] == 'project_postgres_backup_migrations_mismatch'
        verified = command('verify-backup', allow=True)
        assert verified['migration_catalog']['backup_entries'] == 66
        assert verified['migration_catalog']['current_entries'] == 67
        assert verified['migration_catalog']['additional_entries'] == 1
        assert verified['database_started'] is False
        assert db.status()['status'] == 'stopped'
        assert command('restore', allow=True, expected=1)['reason_code'] == 'project_postgres_restore_existing_data_refused'
        # Simulate unavailable originals only within our disposable test root.
        kept = base / 'Original Synthetic Data Preserved'
        db.layout.home.rename(kept)
        assert command('restore', expected=1)['reason_code'] == 'project_postgres_backup_migrations_mismatch'
        assert not db.layout.home.exists() and not (db.layout.private / 'postgres.restoring').exists()
        restored = command('restore', allow=True)
        assert restored['status'] == 'restored_stopped' and restored['database_started'] is False
        assert restored['migration_catalog'] == verified['migration_catalog']
        assert db._state() == identity and kept.is_dir()
        unchanged = tuple(digest_file(db.layout.home / n) for n in ('owner.pgpass', 'app.pgpass')) == original_credentials
        assert unchanged  # Never render credential contents or their hashes.
        # Prefix compatibility is NOT evidence that DDL was already applied.
        with pytest.raises(ProjectDatabaseError, match='migrations_pending'):
            with db.session():
                pytest.fail('research session admitted without explicit migration')
        assert db.status()['status'] == 'stopped'
        pending = db.up()
        assert pending['status'] == 'migrations_pending' and pending['pending_migrations'] == 1
        assert db._psql(identity, 'SELECT count(*) FROM project_private.migrations;') == '66'
        assert db._psql(identity, "SELECT to_regclass('research_capture.paper_simulations') IS NULL;") == 't'
        db.down()
        assert db.migrate()['migrations_applied'] == 1
        assert db.migrate()['migrations_applied'] == 0
        with db.session() as session:
            assert session.inspect(record_id=first.record_id).record == completed.record
            assert session.inspect(record_id=second.record_id).record == failed.record
            assert session.evaluate(generated_at=datetime.fromisoformat(before['generated_at'])).to_dict() == before
            def forbidden(_):
                pytest.fail('restored original task retried model')
            assert session.run_research(request=first, model_factory=forbidden).record == completed.record
            assert db._psql(identity, 'SELECT count(*) FROM project_private.migrations;') == '67'
            assert db._psql(identity, "SELECT to_regclass('research_capture.paper_simulations') IS NOT NULL;") == 't'
        assert db.status()['status'] == 'stopped' and db._state() == identity
        assert digest_file(archive) == receipt['sha256']
        print('native backup catalog: PASS;66->67,explicit trust/prefix,absent-target restore,explicit migration,history retained')
    finally:
        if db.layout.home.exists():
            db.down()
