"""Explicit cold backup and same-root, absent-target physical recovery.

Never stops a running server, deletes existing data, rewrites capture clocks,
replays SQL, changes passwords, relocates a project, or starts a restored server.
The archive includes credentials: keep its directory private and never publish it.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import file_digest
from functools import wraps
import os
from pathlib import Path
import re
import socket
import stat
import zipfile

from . import backup_format as fmt, sql
from .files import Layout, ProjectDatabaseError, fail, no_links, private_directory, write_private
from .runtime import native_command, verify_runtime
from .server import ProjectPostgres
from .backup_zip import validate_zip_layout


def _migrations(layout: Layout) -> str:
    return fmt.fingerprint([(name, digest) for name, digest, _ in sql.migration_catalog(layout)])


def _shutdown(db: ProjectPostgres, info: dict) -> None:
    if (db.layout.cluster / 'postmaster.pid').exists():
        fail('project_postgres_backup_requires_clean_shutdown')
    result = native_command([db._program('pg_controldata'), str(db.layout.cluster)], timeout=10)
    fields = dict(re.findall(r'^(Database system identifier|Database cluster state):\s*(.*?)\s*$', result.stdout, re.M))
    if (result.stderr.strip() or fields.get('Database system identifier') != info['system_identifier']
            or fields.get('Database cluster state') != 'shut down'):
        fail('project_postgres_backup_requires_clean_shutdown')


def _catalog_binding(layout: Layout, expected: str) -> dict:
    """Match a nonempty EXACT prefix of the fully validated effective catalog.

    Old v1 backups retain a catalog fingerprint, not the live database ledger.
    Matching a prefix does not prove how many migrations the snapshot applied.
    """
    catalog = [(name, digest) for name, digest, _ in sql.migration_catalog(layout)]
    for length in range(len(catalog), 0, -1):
        if fmt.fingerprint(catalog[:length]) == expected:
            return dict(migration_catalog=dict(
                match='identical' if length == len(catalog) else 'append_only_extension',
                backup_entries=length, current_entries=len(catalog),
                current_sha256=fmt.fingerprint(catalog),
                additional_entries=len(catalog)-length, database_ledger_checked=False,
                migrations_applied=0))
    fail('project_postgres_backup_migrations_mismatch')


def _catalog_opt_in(value: bool) -> None:
    if type(value) is not bool:
        fail('project_postgres_backup_catalog_opt_in_invalid')


def _bindings(layout: Layout, data: dict, *, allow_catalog_extension: bool = False) -> dict:
    if data['platform'] != fmt.platform_id() or data['instance']['root_sha256'] != layout.root_hash:
        fail('project_postgres_backup_wrong_project_or_platform')
    runtime = verify_runtime(layout)
    if runtime['version'] != data['instance']['version'] or fmt.fingerprint(runtime) != data['runtime_sha256']:
        fail('project_postgres_backup_runtime_mismatch')
    if allow_catalog_extension:
        return _catalog_binding(layout, data['migrations_sha256'])
    if _migrations(layout) != data['migrations_sha256']:
        fail('project_postgres_backup_migrations_mismatch')
    return {}


@contextmanager
def _approved_archive(path: Path, expected_sha256: str, trusted_backup: bool):
    # Approval and the separately retained checksum precede parsing or binaries.
    if trusted_backup is not True:
        fail('project_postgres_backup_trust_required')
    if not fmt.valid_hash(expected_sha256):
        fail('project_postgres_backup_checksum_mismatch')
    path = Path(path).absolute()
    no_links(path)
    private_directory(path.parent)
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_size > fmt.MAX_BYTES + fmt.MAX_MANIFEST_BYTES):
        fmt.invalid()
    if os.name != 'nt' and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        fail('project_postgres_private_permissions_required')
    # Reuse the same open handle for hash checking, validation and extraction.
    with path.open('rb') as raw:
        if file_digest(raw, 'sha256').hexdigest() != expected_sha256:
            fail('project_postgres_backup_checksum_mismatch')
        raw.seek(0)
        validate_zip_layout(raw)
        with zipfile.ZipFile(raw) as archive:
            data = fmt.inspect_archive(archive)
            yield archive, data
            raw.seek(0)
            if file_digest(raw, 'sha256').hexdigest() != expected_sha256:
                fail('project_postgres_backup_changed')


def _public_errors(function):
    @wraps(function)
    def run(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ProjectDatabaseError:
            raise
        except Exception:
            fail('project_postgres_backup_operation_failed')
    return run


@_public_errors
def create_cold_backup(root: Path, *, destination: Path) -> dict:
    """Create a NEW protected directory outside the project. Source must be down.

    Includes the WHOLE native cluster and generated credentials, not engine or
    source files. No live backup, incremental backup, database pruning or reset.
    On failure any new partial backup is retained privately, never a success.
    """
    db = ProjectPostgres(root)
    layout = db.layout
    target = Path(destination).absolute()
    no_links(target)
    target = target.resolve()
    if target.is_relative_to(layout.root) or target.exists() or not target.parent.is_dir():
        fail('project_postgres_backup_new_external_directory_required')
    with layout.lock():
        runtime = verify_runtime(layout)
        info = db._state()
        if runtime['version'] != info['version']:
            fail('project_postgres_backup_runtime_mismatch')
        if db._running(info):
            fail('project_postgres_backup_requires_clean_shutdown')
        _shutdown(db, info)
        data = {'format': fmt.FORMAT, 'created_at': datetime.now(UTC).isoformat(),
                'platform': fmt.platform_id(), 'instance': info,
                'runtime_sha256': fmt.fingerprint(runtime), 'migrations_sha256': _migrations(layout),
                'entries': fmt.inventory(layout.home)}
        manifest = fmt.canonical(data)
        fmt.parse_manifest(manifest)
        private_directory(target, create=True)
        partial = target / 'snapshot.partial'
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as output:
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for name, item in sorted(data['entries'].items()):
                    if item['kind'] == 'directory':
                        entry = zipfile.ZipInfo(name + '/')
                        entry.external_attr = (stat.S_IFDIR | 0o700) << 16
                        archive.writestr(entry, b'')
                    else:
                        archive.write(layout.home / name, name)
                archive.writestr(fmt.MANIFEST, manifest)
            output.flush()
            os.fsync(output.fileno())
        _shutdown(db, info)
        if fmt.inventory(layout.home) != data['entries'] or db._state() != info:
            fail('project_postgres_backup_source_changed')
        # Read/decompress our output too, detecting truncation before publication.
        with partial.open('rb') as checked:
            validate_zip_layout(checked)
            with zipfile.ZipFile(checked) as archive:
                if fmt.inspect_archive(archive) != data:
                    fmt.invalid()
        with partial.open('rb') as stream:
            digest = file_digest(stream, 'sha256').hexdigest()
        partial.rename(target / 'snapshot.palpg.zip')
        write_private(target / 'snapshot.palpg.sha256', digest + '  snapshot.palpg.zip\n')
        return {'status': 'backup_created', 'sha256': digest, 'contains_credentials': True,
                'encrypted': False, 'source_stopped': True, 'files': len(data['entries'])}


@_public_errors
def verify_cold_backup(root: Path, *, archive: Path, expected_sha256: str,
                       trusted_backup: bool = False, allow_catalog_extension: bool = False) -> dict:
    """Verify without starting PostgreSQL; catalog extensions require opt-in.

    This checks an immutable catalog prefix, NOT the snapshot's applied ledger.
    Runtime/platform/root checks remain exact and no migration is performed.
    """
    _catalog_opt_in(allow_catalog_extension)
    layout = Layout(root)
    with _approved_archive(archive, expected_sha256, trusted_backup) as (_, data):
        binding = _bindings(layout, data, allow_catalog_extension=allow_catalog_extension)
    return {'status': 'backup_verified', 'sha256': expected_sha256, 'contains_credentials': True,
            'encrypted': False, 'database_started': False, **binding}


class _StagingLayout(Layout):
    @property
    def home(self):
        return self.private / 'postgres.restoring'


@_public_errors
def restore_cold_backup(root: Path, *, archive: Path, expected_sha256: str,
                        trusted_backup: bool = False, allow_catalog_extension: bool = False) -> dict:
    """Restore ONLY an absent .local/postgres at the original project path.

    Verifies the entire trusted archive before staging; hashes extracted files
    again, checks native clean shutdown/config/identity, then publishes once.
    Never deletes/renames an existing cluster. Failures retain private staging
    for explicit operator diagnosis. Success leaves the recovered DB STOPPED.
    An opted-in catalog extension NEVER applies SQL. A managed session still
    checks the live ledger and blocks pending migrations until explicit migrate.
    """
    _catalog_opt_in(allow_catalog_extension)
    db = ProjectPostgres(root)
    layout = db.layout
    if layout.home.exists():
        fail('project_postgres_restore_existing_data_refused')
    with _approved_archive(archive, expected_sha256, trusted_backup) as (source, data):
        binding = _bindings(layout, data, allow_catalog_extension=allow_catalog_extension)
        with layout.lock():
            staged = ProjectPostgres(root)
            staged.layout = _StagingLayout(layout.root)
            if layout.home.exists() or staged.layout.home.exists():
                fail('project_postgres_restore_existing_data_refused')
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                try:
                    probe.bind(('127.0.0.1', data['instance']['port']))
                except OSError:
                    fail('project_postgres_port_in_use')
            private_directory(staged.layout.home, create=True)
            for name, item in sorted(data['entries'].items()):
                target = staged.layout.home / name
                if item['kind'] == 'directory':
                    # Windows children inherit our explicit owner/SYSTEM ACL;
                    # mkdir(0700) would substitute the admin OWNER RIGHTS ACL.
                    target.mkdir(mode=0o777 if os.name == 'nt' else 0o700)
                else:
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with source.open(name) as input_file, os.fdopen(fd, 'wb') as output:
                        copied = 0
                        for chunk in iter(lambda: input_file.read(fmt.CHUNK), b''):
                            copied += len(chunk)
                            if copied > item['size']:
                                fmt.invalid()
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
            if fmt.inventory(staged.layout.home) != data['entries']:
                fmt.invalid()
            if staged._state() != data['instance']:
                fmt.invalid()
            _shutdown(staged, data['instance'])
            if allow_catalog_extension:
                # Do not publish a receipt based on a catalog/runtime that
                # changed while extracting. External owner changes remain
                # outside the lifecycle lock's coordination boundary.
                if _bindings(layout, data, allow_catalog_extension=True) != binding:
                    fail('project_postgres_backup_catalog_changed')
            # Final recheck while retaining the lifecycle lease and archive FD.
            source.fp.seek(0)
            if file_digest(source.fp, 'sha256').hexdigest() != expected_sha256:
                fail('project_postgres_backup_changed')
            if layout.home.exists():
                fail('project_postgres_restore_existing_data_refused')
            staged.layout.home.rename(layout.home)
    return {'status': 'restored_stopped', 'sha256': expected_sha256,
            'existing_data_overwritten': False, 'database_started': False, **binding}
