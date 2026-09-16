"""Separate self-review: retain exact catalog provenance and strict old guards."""
from hashlib import sha256
import json
from pathlib import Path

import pytest

from polymarket_alpha_lab.project_postgres import backup as b, backup_format as fmt, sql
from polymarket_alpha_lab.project_postgres.files import Layout, ProjectDatabaseError
from tests.test_project_postgres_backup_catalog import CATALOG, extended, fingerprint
from tests.test_project_postgres_backup import state


def test_changed_tail_with_same_length_during_restore_is_not_published(extended, monkeypatch):
    (root, layout, data, dest), args = extended
    rows = list(CATALOG)
    monkeypatch.setattr(b.sql, 'migration_catalog', lambda _: tuple(rows))
    def change_while_checking_staging(*_):
        rows[-1] = (rows[-1][0], 'e'*64, 'SELECT 2;')
    monkeypatch.setattr(b, '_shutdown', change_while_checking_staging)
    kept = layout.private / 'original-kept'
    layout.home.rename(kept)
    before = fmt.inventory(kept)
    with pytest.raises(ProjectDatabaseError, match='catalog_changed'):
        b.restore_cold_backup(root, **args, allow_catalog_extension=True)
    assert not layout.home.exists()
    assert (layout.private / 'postgres.restoring').exists()
    assert fmt.inventory(kept) == before


@pytest.mark.parametrize('reason', ['trust', 'checksum', 'runtime', 'root', 'platform', 'existing'])
def test_extension_permission_does_not_bypass_old_recovery_guards(extended, monkeypatch, reason):
    (root, layout, data, dest), args = extended
    args = dict(args, allow_catalog_extension=True)
    if reason != 'existing':
        layout.home.rename(layout.private / 'original-kept')
    if reason == 'trust': args['trusted_backup'] = False
    elif reason == 'checksum': args['expected_sha256'] = 'f'*64
    elif reason == 'runtime': monkeypatch.setattr(b, 'verify_runtime', lambda _: dict(version='17.99'))
    elif reason == 'root': monkeypatch.setattr(Layout, 'root_hash', property(lambda _: 'a'*64))
    elif reason == 'platform': monkeypatch.setattr(fmt, 'platform_id', lambda: 'foreign')
    with pytest.raises(ProjectDatabaseError):
        b.restore_cold_backup(root, **args)
    assert not (layout.private / 'postgres.restoring').exists()
    assert layout.home.exists() is (reason == 'existing')


def local_catalog(tmp_path):
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="polymarket-alpha-lab"\n')
    (tmp_path / 'database').mkdir()
    folder = tmp_path / 'supabase/migrations'; folder.mkdir(parents=True)
    entries = []
    for i in range(1, 4):
        name = f'2026091600000{i}_fixture.sql'
        body = f'SELECT {i};\n'.encode()
        (folder / name).write_bytes(body)
        entries.append(dict(name=name, sha256=sha256(body).hexdigest(), transaction_wrapper=False))
    manifest = dict(format='native-postgres-migrations-v1', migrations=entries)
    path = tmp_path / 'database/migrations.lock.json'
    path.write_text(json.dumps(manifest))
    layout = Layout(tmp_path)
    expected = fingerprint(sql.migration_catalog(layout)[:2])
    return layout, manifest, path, folder, expected


@pytest.mark.parametrize('change', ['tail-bytes', 'prefix-bytes-and-lock', 'extra-file', 'delete-prefix', 'reorder-lock'])
def test_entire_real_catalog_is_validated_before_matching_a_prefix(tmp_path, change):
    layout, manifest, path, folder, expected = local_catalog(tmp_path)
    entries = manifest['migrations']
    if change == 'tail-bytes':
        (folder / entries[-1]['name']).write_bytes(b'SELECT 999;\n')
    elif change == 'prefix-bytes-and-lock':
        value = b'SELECT 999;\n'
        (folder / entries[0]['name']).write_bytes(value)
        entries[0]['sha256'] = sha256(value).hexdigest()
    elif change == 'extra-file':
        (folder / '20260916000004_unlisted.sql').write_bytes(b'SELECT 4;\n')
    elif change == 'delete-prefix':
        (folder / entries[0]['name']).unlink()
        del entries[0]
    elif change == 'reorder-lock':
        entries[0], entries[1] = entries[1], entries[0]
    path.write_text(json.dumps(manifest))
    with pytest.raises(ProjectDatabaseError):
        b._catalog_binding(layout, expected)


def test_effective_native_prefix_fingerprint_matches_existing_backup_format():
    root = Path(__file__).resolve().parents[1]
    rows = sql.migration_catalog(Layout(root))
    report = b._catalog_binding(Layout(root), fingerprint(rows[:-1]))['migration_catalog']
    assert report['backup_entries'] == len(rows)-1
    assert report['additional_entries'] == 1 and report['database_ledger_checked'] is False
    # The legacy migration codec has a reviewed effective digest. Never use a
    # fingerprint of the raw lock file as if it were the archive's fingerprint.
    source = json.loads((root / 'database/migrations.lock.json').read_text())
    raw_hash = fmt.fingerprint([(r['name'], r['sha256']) for r in source['migrations'][:-1]])
    assert raw_hash != fingerprint(rows[:-1])
    with pytest.raises(ProjectDatabaseError, match='migrations_mismatch'):
        b._catalog_binding(Layout(root), raw_hash)
