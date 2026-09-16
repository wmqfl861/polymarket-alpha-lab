"""Separate same-assistant review of the managed bundle-entry boundary."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from polymarket_alpha_lab.project_postgres import distribution, files
from tests.test_project_postgres_session_bundle import boundary, project


@pytest.mark.parametrize('location', ['source', 'data'])
def test_unreadable_bundle_marker_has_fixed_error_before_private_access(tmp_path, boundary, monkeypatch, location):
    source = project(tmp_path / 'Source')
    target = project(tmp_path / 'Data')
    state = boundary(source, target)
    marker = (source if location == 'source' else target) / distribution.MANIFEST
    original = Path.lstat
    def denied(path, *args, **kwargs):
        if path == marker:
            raise PermissionError('synthetic-private-path-detail')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', denied)
    with pytest.raises(files.ProjectDatabaseError, match='^project_bundle_invalid_or_changed$'):
        with state.db.session():
            pytest.fail('unreadable marker treated as source checkout')
    assert state.events == []


@pytest.mark.parametrize('marker', [distribution.MANIFEST, distribution.ENGINE])
def test_windows_reparse_marker_is_rejected_before_verifier_or_private_access(tmp_path, boundary, monkeypatch, marker):
    root = project(tmp_path / 'Root')
    state = boundary(root, root)
    original = Path.lstat
    def reparse(path, *args, **kwargs):
        if path == root / marker:
            return SimpleNamespace(st_mode=0o100644, st_file_attributes=0x400)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', reparse)
    with pytest.raises(files.ProjectDatabaseError, match='^project_postgres_link_rejected$'):
        with state.db.session():
            pytest.fail('reparse marker followed')
    assert state.events == state.roots == []


@pytest.mark.parametrize('marker', [distribution.MANIFEST, distribution.ENGINE])
def test_directory_is_not_an_absent_kit_marker(tmp_path, boundary, marker):
    root = project(tmp_path / 'Root', bundled=True)
    state = boundary(root, root)
    (root / marker).unlink()
    (root / marker).mkdir()
    with pytest.raises(files.ProjectDatabaseError, match='project_bundle_invalid_or_changed'):
        with state.db.session():
            pytest.fail('directory marker treated as source')
    assert state.events == []


def test_invalid_source_does_not_touch_even_a_valid_different_data_bundle(tmp_path, boundary):
    source = project(tmp_path / 'Source', bundled=True)
    target = project(tmp_path / 'Data', bundled=True)
    state = boundary(source, target)
    (source / distribution.MANIFEST).write_text('{}')
    with pytest.raises(files.ProjectDatabaseError, match='project_bundle_invalid_or_changed'):
        with state.db.session():
            pytest.fail('valid data bundle excused an invalid code bundle')
    assert state.roots == [source] and state.events == []


def test_entry_check_is_not_a_continuous_immutable_filesystem_claim(tmp_path, boundary):
    root = project(tmp_path / 'Root', bundled=True)
    state = boundary(root, root)
    with state.db.session():
        (root / distribution.MANIFEST).write_text('{}')
    # No attempt is made to repair files or to interrupt already-admitted work.
    assert state.events[-3:] == ['close', 'stop', 'unlock']
    state.events.clear()
    with pytest.raises(files.ProjectDatabaseError, match='project_bundle_invalid_or_changed'):
        with state.db.session():
            pytest.fail('later entry accepted changed bundle')
    assert state.events == []
