"""Managed research must not bypass existing bundle integrity; no real engine."""
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from polymarket_alpha_lab.project_postgres import distribution, files, research, server


def project(root, *, bundled=False):
    root.mkdir()
    payloads = {name: b'# synthetic fixture\n' for name in distribution.FIXED}
    payloads.update({'pyproject.toml': b'[project]\nname="polymarket-alpha-lab"\n',
        'database/migrations.lock.json': b'{}',
        'src/polymarket_alpha_lab/__init__.py': b'# inert fixture\n',
        'supabase/migrations/20200101000000_fixture.sql': b'SELECT 1;\n'})
    for name, raw in payloads.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    if bundled:
        (root / distribution.ENGINE).write_bytes(b'synthetic seed; never executed')
        payloads[distribution.ENGINE] = (root / distribution.ENGINE).read_bytes()
        manifest = dict(format=distribution.FORMAT, source_commit='a'*40, source_tree='b'*40,
            target='windows-x86_64', postgres_version='17.11', python_requires='>=3.11',
            files={name: sha256(raw).hexdigest() for name, raw in payloads.items()})
        (root / distribution.MANIFEST).write_text(json.dumps(manifest), encoding='utf-8')
        distribution.verify_distribution(root)
    return root


@pytest.fixture
def boundary(monkeypatch):
    def prepare(source, target):
        events = []
        state = SimpleNamespace(events=events, started=True, pending=(), roots=[])
        monkeypatch.setattr(server, '__file__', str(source / 'src/polymarket_alpha_lab/project_postgres/server.py'))
        db = server.ProjectPostgres(target)
        @contextmanager
        def lock(layout):
            events.append('lock')
            try:
                yield
            finally:
                events.append('unlock')
        monkeypatch.setattr(files.Layout, 'lock', lock)
        def runtime(layout):
            events.append('runtime')
            return {'version': '17.11'}
        monkeypatch.setattr(server, 'verify_runtime', runtime)
        def info():
            events.append('state')
            return {'version': '17.11'}
        monkeypatch.setattr(db, '_state', info)
        def start(info):
            events.append('start')
            return state.started
        monkeypatch.setattr(db, '_start', start)
        monkeypatch.setattr(db, '_pending', lambda info: state.pending)
        monkeypatch.setattr(db, '_stop', lambda info: events.append('stop'))
        session = SimpleNamespace(close=lambda: events.append('close'))
        monkeypatch.setattr(research, 'ProjectResearchSession', lambda *args: session)
        original = distribution.verify_distribution
        def verify(root):
            state.roots.append(root)
            assert events == [], 'bundle verification after private lifecycle access'
            return original(root)
        monkeypatch.setattr(distribution, 'verify_distribution', verify)
        state.db, state.session = db, session
        return state
    return prepare


def damage(root, kind):
    if kind in ('manifest-missing', 'seed-missing'):
        (root / (distribution.MANIFEST if kind == 'manifest-missing' else distribution.ENGINE)).unlink()
    elif kind == 'manifest-json':
        (root / distribution.MANIFEST).write_bytes(b'{broken')
    elif kind == 'unlisted-code':
        (root / 'src/polymarket_alpha_lab/extra.py').write_bytes(b'# inert\n')
    elif kind == 'unlisted-sql':
        (root / 'supabase/migrations/20990101000000_extra.sql').write_bytes(b'SELECT 2;\n')
    else:
        path = {'source': 'src/polymarket_alpha_lab/__init__.py', 'seed': distribution.ENGINE,
                'script': 'scripts/project_database.py', 'catalog': 'database/migrations.lock.json'}[kind]
        with (root / path).open('ab') as stream:
            stream.write(b'\n# changed fixture\n')


@pytest.mark.parametrize('location', ['source', 'data'])
@pytest.mark.parametrize('kind', ['manifest-missing', 'seed-missing', 'manifest-json',
    'unlisted-code', 'unlisted-sql', 'source', 'seed', 'script', 'catalog'])
def test_invalid_bundle_blocks_before_private_lifecycle(tmp_path, boundary, location, kind):
    source = project(tmp_path / 'Chosen Source', bundled=location == 'source')
    target = project(tmp_path / 'Data Root', bundled=location == 'data')
    state = boundary(source, target)
    damage(source if location == 'source' else target, kind)
    with pytest.raises(files.ProjectDatabaseError, match='project_bundle_invalid_or_changed'):
        with state.db.session():
            pytest.fail('invalid bundle reached research')
    assert state.events == []
    assert not (source / '.local').exists() and not (target / '.local').exists()


@pytest.mark.parametrize('source_bundle,data_bundle', [(False, False), (True, False), (False, True), (True, True)])
def test_source_and_data_root_are_checked_independently(tmp_path, boundary, source_bundle, data_bundle):
    source = project(tmp_path / 'Source', bundled=source_bundle)
    target = project(tmp_path / 'Data', bundled=data_bundle)
    state = boundary(source, target)
    with state.db.session() as session:
        assert session is state.session
    assert state.roots == ([source] if source_bundle else []) + ([target] if data_bundle else [])
    assert state.events == ['lock', 'runtime', 'state', 'start', 'close', 'stop', 'unlock']


def test_same_code_data_bundle_is_verified_once_per_entry_not_cached(tmp_path, boundary):
    root = project(tmp_path / 'Same Root', bundled=True)
    state = boundary(root, root)
    with state.db.session():
        pass
    assert state.roots == [root]
    state.events.clear()
    damage(root, 'source')
    with pytest.raises(files.ProjectDatabaseError, match='project_bundle_invalid_or_changed'):
        with state.db.session():
            pytest.fail('previous verification was reused after bytes changed')
    assert state.roots == [root, root] and state.events == []


@pytest.mark.parametrize('started', [False, True])
@pytest.mark.parametrize('failure', ['none', 'pending', 'body'])
def test_valid_bundle_preserves_borrowed_engine_and_cleanup(tmp_path, boundary, started, failure):
    root = project(tmp_path / 'Kit', bundled=True)
    state = boundary(root, root)
    state.started = started
    state.pending = ('pending',) if failure == 'pending' else ()
    def operation():
        with state.db.session() as session:
            assert session is state.session
            if failure == 'body':
                raise ValueError('synthetic body failure')
    if failure == 'none':
        operation()
    else:
        with pytest.raises(files.ProjectDatabaseError if failure == 'pending' else ValueError):
            operation()
    assert state.events == ['lock', 'runtime', 'state', 'start', 'close'] + (['stop'] if started else []) + ['unlock']
