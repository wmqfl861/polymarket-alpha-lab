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


# Integration with the already-merged real Condition/thread drain contract.
from tests.test_project_postgres_session_drain import exercise, managed


def bundled_source(tmp_path, monkeypatch, state):
    """Real kit verifier plus the existing test-owned native lifecycle fixture."""
    from polymarket_alpha_lab.project_postgres import server
    root = project(tmp_path / 'Verified Source With Spaces', bundled=True)
    monkeypatch.setattr(server, '__file__', str(root / 'src/polymarket_alpha_lab/project_postgres/server.py'))
    calls = []
    original = distribution.verify_distribution
    state['bundle_verifier'] = original

    def verify(path):
        assert state['lease'] is False and state['operation_calls'] == 0
        result = original(path)
        calls.append(path)
        return result

    monkeypatch.setattr(distribution, 'verify_distribution', verify)
    return root, calls


@pytest.mark.parametrize('started', [False, True])
@pytest.mark.parametrize('kind', ['none', 'interrupt', 'exit-zero'])
@pytest.mark.parametrize('body_failed', [False, True])
def test_integrated_bundle_entry_preserves_drain_and_original_exception(
        tmp_path, monkeypatch, managed, started, kind, body_failed):
    root, calls = bundled_source(tmp_path, monkeypatch, managed)
    interruption = {'none': None, 'interrupt': KeyboardInterrupt('during close'),
                    'exit-zero': SystemExit(0)}[kind]
    body = ValueError('original body') if body_failed else None
    managed.update(started=started, body_error=body,
                   close_errors=[] if interruption is None else [interruption])
    exercise(managed)
    assert calls == [root]
    assert managed['checkpoint_lease'] is True and managed['checkpoint_in_flight'] == 1
    assert managed['in_flight_at_unlock'] == [0]
    assert managed['error'] is (body if interruption is None else interruption)
    assert managed['events'].count('stop') == int(started)
    if body is not None and interruption is not None:
        assert interruption.__context__ is body


@pytest.mark.parametrize('started', [False, True])
@pytest.mark.parametrize('interrupted', [False, True])
def test_after_entry_bundle_change_does_not_skip_drain_or_allow_next_entry(
        tmp_path, monkeypatch, managed, started, interrupted):
    root, calls = bundled_source(tmp_path, monkeypatch, managed)
    interruption = KeyboardInterrupt('after admission') if interrupted else None
    managed.update(started=started, close_errors=[] if interruption is None else [interruption])

    def pending(identity):
        # Deliberately AFTER entry verification: this is not a filesystem
        # snapshot or continuous integrity promise. Existing work must drain.
        (root / distribution.MANIFEST).write_text('{}', encoding='utf-8')
        return ()

    monkeypatch.setattr(managed['db'], '_pending', pending)
    exercise(managed)
    assert calls == [root] and managed['error'] is interruption
    assert managed['checkpoint_lease'] is True and managed['in_flight_at_unlock'] == [0]
    assert managed['events'].count('stop') == int(started)
    events = list(managed['events'])
    # Use the real verifier again, rather than a spy asserting zero *historical*
    # operations. The next refusal must not take a new lease or start/stop.
    monkeypatch.setattr(distribution, 'verify_distribution', managed['bundle_verifier'])
    with pytest.raises(files.ProjectDatabaseError, match='^project_bundle_invalid_or_changed$'):
        with managed['db'].session():
            pytest.fail('later entry reused integrity success')
    assert managed['events'] == events and not (managed['db'].layout.root / '.local').exists()


@pytest.mark.parametrize('kind', ['regular', 'interrupt', 'exit'])
def test_verified_bundle_does_not_mask_stop_failure_after_draining(
        tmp_path, monkeypatch, managed, kind):
    root, calls = bundled_source(tmp_path, monkeypatch, managed)
    interruption = KeyboardInterrupt('close interruption')
    error = {'regular': files.ProjectDatabaseError('project_postgres_stop_failed'),
             'interrupt': KeyboardInterrupt('stop interruption'), 'exit': SystemExit(9)}[kind]
    managed.update(close_errors=[interruption], stop_error=error)
    exercise(managed)
    assert calls == [root] and managed['in_flight_at_unlock'] == [0]
    assert managed['error'] is error and error.__cause__ is interruption
    assert managed['events'].count('stop') == 1


@pytest.mark.parametrize('kind', ['os-error', 'interrupt', 'exit-zero'])
def test_bundle_verification_failure_precedes_any_drain_lifecycle_access(
        tmp_path, monkeypatch, managed, kind):
    root, calls = bundled_source(tmp_path, monkeypatch, managed)
    failure = {'os-error': OSError('private marker detail'),
               'interrupt': KeyboardInterrupt(), 'exit-zero': SystemExit(0)}[kind]

    def fail_verification(path):
        assert path == root
        raise failure

    monkeypatch.setattr(distribution, 'verify_distribution', fail_verification)
    with pytest.raises(files.ProjectDatabaseError if kind == 'os-error' else type(failure)) as caught:
        with managed['db'].session():
            pytest.fail('verification failure admitted session')
    if kind == 'os-error':
        assert str(caught.value) == 'project_bundle_invalid_or_changed'
    else:
        assert caught.value is failure
    assert calls == managed['events'] == [] and managed['operation_calls'] == 0
    assert not (managed['db'].layout.root / '.local').exists()
