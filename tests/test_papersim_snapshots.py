"""FX-12 snapshot tests: registration, idempotency, verification.

Offline by construction: the in-memory fake below speaks exactly the
SQL surface of ``polymarket_alpha_lab.papersim.snapshots`` (one INSERT
... ON CONFLICT DO NOTHING plus SELECTs), so every discipline rule --
register before use, same-hash byte verification, no overwrite, no
path-only registration, read-only legacy import -- is proven without a
cluster. The opt-in native lane at the bottom proves the same surface
against the real managed schema (append-only trigger included).

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import os
from pathlib import Path
import re

import pytest

from polymarket_alpha_lab.papersim import snapshots
from polymarket_alpha_lab.papersim.store import PapersimStoreError

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
APP_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_APP_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN or not APP_DSN,
                            reason='explicit native papersim store proof is opt-in')

SOURCE = Path(snapshots.__file__).read_text(encoding='utf-8')


def at(minutes: int) -> datetime:
    return datetime(2026, 11, 15, 12, 0, tzinfo=UTC) + timedelta(minutes=minutes)


class _Column:
    def __init__(self, name: str):
        self.name = name


class FakeCursor:
    def __init__(self, conn: 'FakeConn'):
        self._conn = conn
        self._rows: list = []
        self._columns: list[str] = []

    def execute(self, sql, params=None):
        sql = ' '.join(sql.split())
        self._conn.statements.append(sql)
        self._rows, self._columns = self._conn._execute(sql, params)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    @property
    def description(self):
        return [_Column(name) for name in self._columns]


class FakeConn:
    """In-memory managed-connection stand-in (papersim.snapshots only)."""

    def __init__(self):
        self.snapshots: dict[str, dict] = {}
        self.statements: list[str] = []

    def _execute(self, sql, params):
        if sql.startswith('INSERT INTO papersim.snapshots'):
            digest, content, length, kind, source, fetched_at, schema_version = params
            assert isinstance(content, bytes), 'raw bytes must reach SQL, not a path'
            if digest in self.snapshots:
                return [], ['content_sha256']
            self.snapshots[digest] = {
                'content_sha256': digest, 'content': bytes(content),
                'byte_length': int(length), 'kind': kind, 'source': source,
                'fetched_at': fetched_at, 'schema_version': int(schema_version)}
            return [(digest,)], ['content_sha256']
        if sql.startswith('SELECT content, byte_length'):
            (digest,) = params
            row = self.snapshots.get(digest)
            if row is None:
                return [], ['content', 'byte_length', 'kind', 'source', 'fetched_at',
                            'schema_version']
            return [(row['content'], row['byte_length'], row['kind'], row['source'],
                     row['fetched_at'], row['schema_version'])], \
                ['content', 'byte_length', 'kind', 'source', 'fetched_at', 'schema_version']
        raise AssertionError(f'unexpected SQL reached the fake: {sql}')

    def cursor(self):
        return FakeCursor(self)

    def transaction(self):
        return _NoTx()


class _NoTx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def registration(content: bytes = b'{"market": "raw-payload"}', **overrides):
    values = dict(content=content, kind='gamma_market', source='gamma:3866000',
                  fetched_at=at(0), schema_version=1)
    values.update(overrides)
    return snapshots.SnapshotRegistration(**values)


# ---------------------------------------------------------------------------
# Pure digest and validation semantics
# ---------------------------------------------------------------------------

def test_digest_is_sha256_of_the_raw_bytes():
    content = b'raw-api-bytes'
    assert snapshots.snapshot_content_sha256(content) == sha256(content).hexdigest()
    assert snapshots.snapshot_content_sha256(content) != \
        snapshots.snapshot_content_sha256(b'raw-api-bytez')


def test_registration_requires_the_original_bytes():
    with pytest.raises(PapersimStoreError, match='papersim_snapshot_content_bytes_required'):
        registration(content='/var/data/payload.json').validate()
    with pytest.raises(PapersimStoreError, match='papersim_snapshot_content_bytes_required'):
        registration(content=None).validate()
    with pytest.raises(PapersimStoreError, match='papersim_snapshot_empty'):
        registration(content=b'').validate()


@pytest.mark.parametrize('overrides', [
    {'kind': '  '}, {'kind': 7}, {'source': ''}, {'source': None},
    {'fetched_at': datetime(2026, 11, 15, 12, 0)}, {'fetched_at': '2026-11-15'},
    {'schema_version': 0}, {'schema_version': True}, {'schema_version': '1'},
])
def test_registration_validation_rejects_garbage(overrides):
    with pytest.raises(PapersimStoreError):
        registration(**overrides).validate()


def test_digest_argument_is_validated_before_sql():
    with pytest.raises(PapersimStoreError, match='papersim_snapshot_hash_invalid'):
        snapshots.snapshot_digest_argument('deadbeef')
    with pytest.raises(PapersimStoreError, match='papersim_snapshot_hash_invalid'):
        snapshots.snapshot_digest_argument('Z' * 64)


# ---------------------------------------------------------------------------
# Registration semantics on the fake managed connection
# ---------------------------------------------------------------------------

def test_register_inserts_the_bytes_and_is_idempotent_per_digest():
    conn = FakeConn()
    first = snapshots.register_snapshot(conn, registration())
    assert first['inserted'] is True
    assert first['content_sha256'] == sha256(b'{"market": "raw-payload"}').hexdigest()
    assert first['byte_length'] == len(b'{"market": "raw-payload"}')
    again = snapshots.register_snapshot(conn, registration())
    assert again['inserted'] is False
    assert again['content_sha256'] == first['content_sha256']
    assert len(conn.snapshots) == 1
    # The only write statement ever issued is the conflict-free insert
    # (the idempotent re-registration attempts it again, hits the
    # conflict and reads the original row back).
    writes = [sql for sql in conn.statements if not sql.startswith('SELECT')]
    assert writes == [' '.join(snapshots._SNAPSHOT_INSERT.split())] * 2


def test_new_content_gets_a_new_digest_and_old_rows_are_never_overwritten():
    conn = FakeConn()
    first = snapshots.register_snapshot(conn, registration(kind='first'))
    second = snapshots.register_snapshot(conn, registration(b'other bytes', kind='second'))
    assert second['inserted'] is True
    assert second['content_sha256'] != first['content_sha256']
    # Same bytes re-registered under different metadata: identity is the
    # digest, the ORIGINAL row is returned untouched (09D v2 5.2).
    reread = snapshots.register_snapshot(conn, registration(kind='changed-label'))
    assert reread['inserted'] is False
    assert reread['kind'] == 'first'
    assert conn.snapshots[first['content_sha256']]['kind'] == 'first'


def test_same_digest_with_different_bytes_fails_loudly():
    conn = FakeConn()
    digest = sha256(b'{"market": "raw-payload"}').hexdigest()
    conn.snapshots[digest] = {'content_sha256': digest, 'content': b'different bytes!',
                              'byte_length': len(b'different bytes!'), 'kind': 'gamma_market',
                              'source': 'gamma:3866000', 'fetched_at': at(0),
                              'schema_version': 1}
    with pytest.raises(PapersimStoreError, match='papersim_snapshot_content_conflict'):
        snapshots.register_snapshot(conn, registration())


def test_read_returns_full_row_and_none_when_missing():
    conn = FakeConn()
    stored = snapshots.register_snapshot(conn, registration())
    row = snapshots.read_snapshot(conn, stored['content_sha256'])
    assert row['content'] == b'{"market": "raw-payload"}'
    assert row['byte_length'] == len(b'{"market": "raw-payload"}')
    assert snapshots.read_snapshot(conn, 'a' * 64) is None


def test_verify_proves_hash_length_and_version():
    conn = FakeConn()
    stored = snapshots.register_snapshot(conn, registration())
    summary = snapshots.verify_snapshot(conn, stored['content_sha256'],
                                        expected_schema_version=1)
    assert summary['byte_length'] == len(b'{"market": "raw-payload"}')
    with pytest.raises(PapersimStoreError, match='papersim_snapshot_missing'):
        snapshots.verify_snapshot(conn, 'b' * 64)
    with pytest.raises(PapersimStoreError,
                       match='papersim_snapshot_schema_version_mismatch'):
        snapshots.verify_snapshot(conn, stored['content_sha256'], expected_schema_version=2)


def test_verify_detects_a_one_byte_tamper():
    conn = FakeConn()
    stored = snapshots.register_snapshot(conn, registration())
    row = conn.snapshots[stored['content_sha256']]
    tampered = bytearray(row['content'])
    tampered[0] ^= 0x01
    row['content'] = bytes(tampered)
    with pytest.raises(PapersimStoreError, match='papersim_snapshot_digest_mismatch'):
        snapshots.verify_snapshot(conn, stored['content_sha256'])
    row['byte_length'] = row['byte_length'] + 1
    with pytest.raises(PapersimStoreError, match='papersim_snapshot_length_mismatch'):
        snapshots.verify_snapshot(conn, stored['content_sha256'])


# ---------------------------------------------------------------------------
# Legacy file import is an explicit read-only surface
# ---------------------------------------------------------------------------

def test_legacy_file_import_reads_the_bytes_once(tmp_path):
    legacy = tmp_path / 'legacy-book.json'
    legacy.write_bytes(b'{"legacy": "ladder-bytes"}')
    imported = snapshots.registration_from_legacy_file(
        legacy, kind='clob_book', source='legacy:paper_sim', fetched_at=at(3))
    assert imported.content == b'{"legacy": "ladder-bytes"}'
    assert imported.content_sha256 == sha256(b'{"legacy": "ladder-bytes"}').hexdigest()
    conn = FakeConn()
    assert snapshots.register_snapshot(conn, imported)['inserted'] is True
    # No file was created, moved or appended anywhere by the import.
    assert [path.name for path in tmp_path.iterdir()] == ['legacy-book.json']


def test_legacy_file_import_rejects_missing_paths(tmp_path):
    with pytest.raises(PapersimStoreError, match='papersim_snapshot_legacy_path_invalid'):
        snapshots.registration_from_legacy_file(
            tmp_path / 'absent.json', kind='clob_book', source='legacy',
            fetched_at=at(0))
    with pytest.raises(PapersimStoreError, match='papersim_snapshot_legacy_path_invalid'):
        snapshots.registration_from_legacy_file(
            tmp_path, kind='clob_book', source='legacy', fetched_at=at(0))


# ---------------------------------------------------------------------------
# Structural discipline: the module's SQL surface
# ---------------------------------------------------------------------------

def test_module_sql_surface_is_one_conflict_free_insert_plus_selects():
    assert snapshots._SNAPSHOT_INSERT.count('INSERT') == 1
    assert 'ON CONFLICT (content_sha256) DO NOTHING' in snapshots._SNAPSHOT_INSERT
    forbidden = (r'\bUPDATE\b', r'\bDELETE\b', r'\bTRUNCATE\b', r'\bDROP\b')
    for statement in (snapshots._SNAPSHOT_INSERT, snapshots._SNAPSHOT_SELECT):
        for pattern in forbidden:
            assert re.search(pattern, statement) is None, statement
    # The word "path" never appears as a registration content source.
    assert 'content: str' not in SOURCE


# ---------------------------------------------------------------------------
# Opt-in native lane (one-shot disposable cluster only)
# ---------------------------------------------------------------------------

def adapter():
    if not ENABLED:
        pytest.skip('the papersim connection adapter runs in the native lane only')
    return pytest.importorskip('polymarket_alpha_lab.papersim.store_psycopg')


def connect(dsn, *, autocommit=False):
    psycopg = pytest.importorskip('psycopg')
    connection = psycopg.connect(dsn)
    connection.autocommit = autocommit
    return connection


@pytest.fixture(scope='module')
def managed_face():
    from polymarket_alpha_lab.papersim import migrations as pm
    owner = connect(OWNER_DSN)
    # Cluster-preparation wiring (M2 plan section 6.3): the applied count
    # follows the CURRENT catalog size; migration-history count assertions
    # live against the frozen M1 catalog fixture.
    catalog = pm.papersim_migration_catalog(ROOT)
    result = pm.apply_pending_migrations(owner, ROOT)
    assert result['applied'] in range(len(catalog) + 1)
    assert pm.migration_status(owner, ROOT)['pending'] == []
    owner.close()
    return OWNER_DSN, APP_DSN


@NATIVE
def test_native_register_verify_and_no_overwrite(managed_face):
    _, app_dsn = managed_face
    app = connect(app_dsn)
    content = b'{"native": "raw-gamma-payload"}'
    reg = snapshots.SnapshotRegistration(content=content, kind='gamma_market',
                                         source='gamma:n1', fetched_at=at(1))
    first = snapshots.register_snapshot(app, reg)
    assert first['inserted'] is True
    again = snapshots.register_snapshot(app, reg)
    assert again['inserted'] is False and again['content_sha256'] == first['content_sha256']
    with app.cursor() as cursor:
        # The CI papersim partition shares one disposable cluster across
        # nine files, so this table legitimately holds rows from earlier
        # files; idempotency is proven on THIS digest only (exactly one
        # row, original bytes untouched -- the overwrite check below).
        cursor.execute('SELECT count(*) FROM papersim.snapshots '
                       'WHERE content_sha256 = %s', (first['content_sha256'],))
        assert cursor.fetchone()[0] == 1
        cursor.execute('SELECT octet_length(content) FROM papersim.snapshots '
                       'WHERE content_sha256 = %s', (first['content_sha256'],))
        assert cursor.fetchone()[0] == len(content)
    assert snapshots.verify_snapshot(app, first['content_sha256'],
                                     expected_schema_version=1)['byte_length'] == len(content)
    app.close()


@NATIVE
def test_native_mutation_is_rejected_by_the_append_only_guard(managed_face):
    _, app_dsn = managed_face
    app = connect(app_dsn)
    reg = snapshots.SnapshotRegistration(content=b'{"native": "guarded"}', kind='gamma_market',
                                         source='gamma:n2', fetched_at=at(2))
    digest = snapshots.register_snapshot(app, reg)['content_sha256']
    with pytest.raises(Exception):
        with app.transaction():
            with app.cursor() as cursor:
                cursor.execute('UPDATE papersim.snapshots SET kind = %s '
                               'WHERE content_sha256 = %s', ('tampered', digest))
    with pytest.raises(Exception):
        with app.transaction():
            with app.cursor() as cursor:
                cursor.execute('DELETE FROM papersim.snapshots WHERE content_sha256 = %s',
                               (digest,))
    app.rollback()
    assert snapshots.verify_snapshot(app, digest)['kind'] == 'gamma_market'
    app.close()
