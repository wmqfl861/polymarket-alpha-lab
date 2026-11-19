"""FX-12 raw-evidence snapshots on the papersim managed PostgreSQL store.

Single responsibility: register and verify the RAW evidence bytes every
replayable decision cites (09D design v2 section 5.2; M1 plan
section 3 FX-12). The managed ``papersim.snapshots`` relation built by
FX-09S stores the original bytes (``bytea``), their SHA-256 digest, the
byte length, a free-form ``kind``/``source`` pair, the fetch time and a
schema version; this module is its recording face and adds nothing to
the schema.

Discipline (FX-12 node spec, item 1):

- Register BEFORE use. Candidates cite snapshots by digest through
  ``candidates.snapshot_refs`` and the FX-09S scan transaction already
  proves every referenced snapshot is registered inside the same
  transaction (``papersim_snapshot_ref_missing``).
- Same hash re-registration reads the stored row back and verifies the
  ORIGINAL BYTES, then returns the original row: the content digest is
  the identity, so equal digests with different bytes fail loudly
  instead of returning silently.
- New content produces a new digest (SHA-256 over the bytes); stored
  rows are never overwritten -- the only write statement here is one
  ``INSERT ... ON CONFLICT DO NOTHING``, everything else is a SELECT.
- A registration without the original bytes does not exist: the content
  argument must be ``bytes`` (or ``bytearray``); there is deliberately
  no path-only or digest-only registration API.
- Legacy files are an EXPLICIT READ-ONLY IMPORT: bytes are read once
  from a caller-named path and land in PostgreSQL. This module never
  writes, creates or appends to any file, so the frozen file-persistence
  surface (Project Iron Rule 2) is not extended.
- Connections are NOT opened here. Callers pass the managed application
  connection obtained through the reviewed papersim connection adapter,
  whose DSN already passed the single audited local validator before
  connecting; every statement is fully parameterized.

Paper boundary: this module is paper-evidence storage only
(``paper_only``/``report_only``/``readonly``); it grants no execution
authorization and never touches an order.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from polymarket_alpha_lab.papersim.store import PapersimStoreError
from polymarket_alpha_lab.project_postgres.files import no_links

__all__ = [
    "SNAPSHOT_DEFAULT_SCHEMA_VERSION",
    "SnapshotRegistration",
    "read_snapshot",
    "register_snapshot",
    "registration_from_legacy_file",
    "snapshot_content_sha256",
    "snapshot_digest_argument",
    "verify_snapshot",
]

#: Schema version stamped on registrations that do not state one.
SNAPSHOT_DEFAULT_SCHEMA_VERSION = 1

#: Column order of the SELECT below, without the leading digest column.
_ROW_COLUMNS = ('content', 'byte_length', 'kind', 'source', 'fetched_at', 'schema_version')

# The only write in this module: INSERT ... ON CONFLICT DO NOTHING, so a
# repeated registration of the same digest never mutates the stored row.
_SNAPSHOT_INSERT = (
    'INSERT INTO papersim.snapshots '
    '(content_sha256, content, byte_length, kind, source, fetched_at, schema_version) '
    'VALUES (%s,%s,%s,%s,%s,%s,%s) '
    'ON CONFLICT (content_sha256) DO NOTHING RETURNING content_sha256'
)

_SNAPSHOT_SELECT = ('SELECT content, byte_length, kind, source, fetched_at, schema_version '
                    'FROM papersim.snapshots WHERE content_sha256 = %s')


def _fail(code: str):
    raise PapersimStoreError(code)


def _require_text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f'papersim_{name}_invalid')
    return value


def _require_timestamp(value, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail(f'papersim_{name}_invalid')
    return value


def snapshot_content_sha256(content) -> str:
    """SHA-256 of the raw bytes -- the snapshot identity (09D v2 5.2)."""
    if not isinstance(content, (bytes, bytearray)):
        # A path or a bare digest can never substitute for the bytes.
        _fail('papersim_snapshot_content_bytes_required')
    return sha256(bytes(content)).hexdigest()


def snapshot_digest_argument(value) -> str:
    """Validate a caller-supplied digest before it reaches any SQL."""
    if not isinstance(value, str) or len(value) != 64 \
            or any(character not in '0123456789abcdef' for character in value):
        _fail('papersim_snapshot_hash_invalid')
    return value


@dataclass(frozen=True)
class SnapshotRegistration:
    """One raw-bytes snapshot to register before any candidate cites it."""

    content: bytes
    kind: str
    source: str
    fetched_at: datetime
    schema_version: int = SNAPSHOT_DEFAULT_SCHEMA_VERSION

    def validate(self) -> 'SnapshotRegistration':
        if not isinstance(self.content, (bytes, bytearray)):
            _fail('papersim_snapshot_content_bytes_required')
        if len(self.content) == 0:
            _fail('papersim_snapshot_empty')
        _require_text(self.kind, 'snapshot_kind')
        _require_text(self.source, 'snapshot_source')
        _require_timestamp(self.fetched_at, 'snapshot_fetched_at')
        if (isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int)
                or self.schema_version < 1):
            _fail('papersim_snapshot_schema_version_invalid')
        return self

    @property
    def content_sha256(self) -> str:
        return snapshot_content_sha256(self.content)

    @property
    def byte_length(self) -> int:
        self.validate()
        return len(self.content)


def registration_from_legacy_file(
    path,
    *,
    kind: str,
    source: str,
    fetched_at: datetime,
    schema_version: int = SNAPSHOT_DEFAULT_SCHEMA_VERSION,
) -> SnapshotRegistration:
    """EXPLICIT READ-ONLY import of one legacy file's bytes.

    Reads the named path once (refusing symlinks, like every private
    project read) and returns a registration carrying those exact bytes.
    This is the only legacy-file touch: nothing is written, moved or
    appended on disk, so durable persistence stays PostgreSQL-only.
    """
    target = Path(path)
    # The audited link guard raises its own explicit error for symlinks;
    # it is deliberately not translated into a papersim code.
    no_links(target)
    if not target.is_file():
        _fail('papersim_snapshot_legacy_path_invalid')
    try:
        content = target.read_bytes()
    except OSError:
        _fail('papersim_snapshot_legacy_read_failed')
    return SnapshotRegistration(content=content, kind=kind, source=source,
                                fetched_at=fetched_at, schema_version=schema_version)


def _as_bytes(content) -> bytes:
    # Managed drivers hand ``bytea`` back as a buffer; bytes() is a no-op
    # for real bytes and never decodes text.
    return content if isinstance(content, bytes) else bytes(content)


def _select_row(cursor, digest: str) -> dict | None:
    cursor.execute(_SNAPSHOT_SELECT, (digest,))
    row = cursor.fetchone()
    if row is None:
        return None
    record = dict(zip(_ROW_COLUMNS, row, strict=True))
    record['content_sha256'] = digest
    record['content'] = _as_bytes(record['content'])
    return record


def register_snapshot(conn, registration: SnapshotRegistration) -> dict:
    """Register raw bytes; idempotent per content digest (09D v2 5.2).

    Same digest: the stored row is read back, its ORIGINAL BYTES are
    compared with the registration and the original row summary is
    returned with ``inserted=False``; the stored row is never updated.
    New content carries a new digest and inserts. An equal digest with
    different bytes fails with ``papersim_snapshot_content_conflict``.
    """
    registration.validate()
    content = bytes(registration.content)
    digest = sha256(content).hexdigest()
    with conn.transaction():
        cursor = conn.cursor()
        cursor.execute(_SNAPSHOT_INSERT, (digest, content, len(content), registration.kind,
                                          registration.source, registration.fetched_at,
                                          registration.schema_version))
        if cursor.fetchone() is not None:
            stored = {'content': content, 'byte_length': len(content),
                      'kind': registration.kind, 'source': registration.source,
                      'fetched_at': registration.fetched_at,
                      'schema_version': registration.schema_version}
            inserted = True
        else:
            stored = _select_row(cursor, digest)
            if stored is None:
                _fail('papersim_snapshot_registration_lost')
            if stored['content'] != content:
                _fail('papersim_snapshot_content_conflict')
            if int(stored['byte_length']) != len(content):
                _fail('papersim_snapshot_length_mismatch')
            inserted = False
    return {'content_sha256': digest, 'inserted': inserted,
            'byte_length': int(stored['byte_length']), 'kind': stored['kind'],
            'source': stored['source'], 'fetched_at': stored['fetched_at'],
            'schema_version': int(stored['schema_version'])}


def read_snapshot(conn, content_sha256: str) -> dict | None:
    """Full stored row for one digest, or ``None`` when unregistered."""
    digest = snapshot_digest_argument(content_sha256)
    with conn.transaction():
        cursor = conn.cursor()
        record = _select_row(cursor, digest)
    return record


def verify_snapshot(conn, content_sha256: str, *,
                    expected_schema_version: int | None = None) -> dict:
    """Forensic read-back: prove hash, length and (optionally) version.

    Recomputes the SHA-256 and the byte length of the stored content and
    compares both with the stored columns, so a tampered or substituted
    row fails loudly instead of feeding replay with unproven bytes.
    """
    digest = snapshot_digest_argument(content_sha256)
    record = read_snapshot(conn, digest)
    if record is None:
        _fail('papersim_snapshot_missing')
    if len(record['content']) != int(record['byte_length']):
        _fail('papersim_snapshot_length_mismatch')
    if sha256(record['content']).hexdigest() != record['content_sha256']:
        _fail('papersim_snapshot_digest_mismatch')
    if expected_schema_version is not None and int(record['schema_version']) != expected_schema_version:
        _fail('papersim_snapshot_schema_version_mismatch')
    return {'content_sha256': record['content_sha256'], 'byte_length': int(record['byte_length']),
            'kind': record['kind'], 'source': record['source'],
            'fetched_at': record['fetched_at'],
            'schema_version': int(record['schema_version'])}
