"""Explicit archive/catalog compatibility; no engine or user backups are opened."""
from copy import deepcopy
import json

import pytest

from polymarket_alpha_lab.project_postgres import backup as b, backup_format as fmt
from polymarket_alpha_lab.project_postgres.cli import main
from polymarket_alpha_lab.project_postgres.files import ProjectDatabaseError
from tests.test_project_postgres_backup import state, make

CATALOG = tuple((f'2026091600000{i}_fixture.sql', str(i)*64, 'SELECT 1;') for i in range(1, 4))


def fingerprint(rows):
    return fmt.fingerprint([(name, digest) for name, digest, _ in rows])


@pytest.fixture
def extended(state, monkeypatch):
    # Create an unchanged v1 archive with its original TWO-entry fingerprint.
    monkeypatch.setattr(b, '_migrations', lambda _: fingerprint(CATALOG[:2]))
    path, digest = make(state)
    monkeypatch.setattr(b, '_migrations', lambda _: fingerprint(CATALOG))
    monkeypatch.setattr(b.sql, 'migration_catalog', lambda _: CATALOG)
    return state, dict(archive=path, expected_sha256=digest, trusted_backup=True)


def test_default_still_refuses_extended_catalog_before_restore_staging(extended):
    (root, layout, data, dest), args = extended
    with pytest.raises(ProjectDatabaseError, match='migrations_mismatch'):
        b.verify_cold_backup(root, **args)
    layout.home.rename(layout.private / 'original-kept')
    with pytest.raises(ProjectDatabaseError, match='migrations_mismatch'):
        b.restore_cold_backup(root, **args)
    assert not layout.home.exists() and not (layout.private / 'postgres.restoring').exists()


def test_explicit_prefix_verification_does_not_change_archive_or_data(extended):
    (root, layout, data, dest), args = extended
    before = fmt.inventory(layout.home)
    archive_before = args['archive'].read_bytes()
    result = b.verify_cold_backup(root, **args, allow_catalog_extension=True)
    assert result['status'] == 'backup_verified' and result['database_started'] is False
    assert result['migration_catalog'] == dict(match='append_only_extension', backup_entries=2,
        current_entries=3, current_sha256=fingerprint(CATALOG), additional_entries=1,
        database_ledger_checked=False, migrations_applied=0)
    assert fmt.inventory(layout.home) == before and args['archive'].read_bytes() == archive_before


def test_explicit_prefix_restore_keeps_every_old_byte_and_never_migrates(extended, monkeypatch):
    (root, layout, data, dest), args = extended
    before = fmt.inventory(layout.home)
    kept = layout.private / 'original-kept'
    layout.home.rename(kept)
    monkeypatch.setattr(b.ProjectPostgres, 'migrate', lambda *_: pytest.fail('implicit migration'))
    monkeypatch.setattr(b.ProjectPostgres, 'up', lambda *_: pytest.fail('implicit engine start'))
    result = b.restore_cold_backup(root, **args, allow_catalog_extension=True)
    assert result['status'] == 'restored_stopped' and result['existing_data_overwritten'] is False
    assert result['migration_catalog']['additional_entries'] == 1
    assert result['migration_catalog']['migrations_applied'] == 0
    assert fmt.inventory(layout.home) == before == fmt.inventory(kept)


@pytest.mark.parametrize('rows', [
    CATALOG[:1],
    CATALOG[1:],
    (CATALOG[1], CATALOG[0], CATALOG[2]),
    ((CATALOG[0][0], 'e'*64, CATALOG[0][2]), *CATALOG[1:]),
    ((CATALOG[0][0].replace('fixture', 'renamed'), CATALOG[0][1], CATALOG[0][2]), *CATALOG[1:]),
    (CATALOG[0], CATALOG[2]),
], ids=['shorter', 'missing-first', 'reordered', 'changed-hash', 'renamed', 'missing-middle'])
def test_opt_in_never_accepts_nonprefix_history(extended, monkeypatch, rows):
    (root, layout, data, dest), args = extended
    monkeypatch.setattr(b.sql, 'migration_catalog', lambda _: rows)
    layout.home.rename(layout.private / 'original-kept')
    with pytest.raises(ProjectDatabaseError, match='migrations_mismatch'):
        b.restore_cold_backup(root, **args, allow_catalog_extension=True)
    assert not layout.home.exists() and not (layout.private / 'postgres.restoring').exists()


def test_equal_catalog_is_explicitly_identical_not_reported_as_extension(extended, monkeypatch):
    (root, layout, data, dest), args = extended
    monkeypatch.setattr(b.sql, 'migration_catalog', lambda _: CATALOG[:2])
    report = b.verify_cold_backup(root, **args, allow_catalog_extension=True)['migration_catalog']
    assert report == dict(match='identical', backup_entries=2, current_entries=2,
        current_sha256=fingerprint(CATALOG[:2]), additional_entries=0,
        database_ledger_checked=False, migrations_applied=0)


@pytest.mark.parametrize('value', [1, 'true', None, [], {}])
@pytest.mark.parametrize('operation', [b.verify_cold_backup, b.restore_cold_backup])
def test_opt_in_requires_actual_boolean_before_opening_any_backup(monkeypatch, operation, value):
    monkeypatch.setattr(b, '_approved_archive', lambda *_: pytest.fail('opened backup'))
    with pytest.raises(ProjectDatabaseError, match='catalog_opt_in_invalid'):
        operation(None, archive=None, expected_sha256='a'*64, trusted_backup=True,
                  allow_catalog_extension=value)


@pytest.mark.parametrize('operation', ['verify-backup', 'restore'])
def test_cli_only_forwards_explicit_catalog_extension_permission(monkeypatch, capsys, operation):
    calls = []
    def verify(root, **kw):
        calls.append(kw)
        return dict(status='fixture_no_io')
    monkeypatch.setattr(b, 'verify_cold_backup', verify)
    monkeypatch.setattr(b, 'restore_cold_backup', verify)
    args = ['--root', 'fixture', operation, '--archive', 'fixture.zip',
            '--sha256', 'a'*64, '--trusted-backup']
    assert main(args) == 0
    assert calls[-1].get('allow_catalog_extension', False) is False
    assert main([*args, '--allow-catalog-extension']) == 0
    assert calls[-1]['allow_catalog_extension'] is True
    assert len(calls) == 2
    assert 'fixture_no_io' in capsys.readouterr().out
