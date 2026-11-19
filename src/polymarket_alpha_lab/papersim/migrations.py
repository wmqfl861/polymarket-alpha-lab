"""Papersim-managed migration catalog, applier and command line.

This module owns the papersim-dedicated catalog (09D design v2,
section 3): ``database/papersim/migrations/`` plus its parent lock manifest
``database/papersim/migrations.lock.json`` (format
``papersim-migrations-v1``). It deliberately reuses the audited V1
principles with papersim-specific parameters:

- 14-digit timestamp names matching ``[0-9]{14}_[a-z0-9_]+\\.sql``;
- ``sha256`` over the FILE BYTES (papersim has no effective-bytes
  transform: the checked-in bytes are the applied bytes);
- the applied history must be a hash-consistent prefix of the catalog;
- every migration applies inside one transaction that also writes its
  receipt, under the papersim-dedicated advisory lock key ``73198643``
  (the V1 ledger uses 73198642; different keys avoid mutual blocking);
- drift stops loudly: changed bytes, inventory mismatch, broken prefix,
  already-present objects and failures never get hand-patched receipts.

This module never imports a database driver: actual connections come from
``polymarket_alpha_lab.papersim.store_psycopg`` (the single allowlisted
papersim connector), and every raw DSN passes the single audited
``validate_local_postgres_dsn`` there before connecting. Corrections are
always NEW migrations appended with a fresh timestamp; published files and
manifest entries are never edited in place.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

from polymarket_alpha_lab.project_postgres.files import no_links

PAPERSIM_LOCK_KEY = 73198643
MANIFEST_FORMAT = 'papersim-migrations-v1'
MIGRATIONS_DIRECTORY = Path('database/papersim/migrations')
LOCK_MANIFEST = Path('database/papersim/migrations.lock.json')
LEDGER_RELATION = 'papersim_private.migrations'
FORBIDDEN_BODY_TOKENS = re.compile(r'(?im)^\s*\\|\b(?:commit|begin|rollback)\s*;')


class PapersimMigrationError(RuntimeError):
    """Fixed public error codes only; never provider output or credentials."""


def _fail(code: str):
    raise PapersimMigrationError(code) from None


def _migration_source(repo_root: Path, name: str) -> bytes:
    source = Path(repo_root) / MIGRATIONS_DIRECTORY / name
    no_links(source)
    if source.stat().st_size > 2097152:
        _fail('papersim_migration_manifest_invalid')
    return source.read_bytes()


def papersim_migration_catalog(repo_root: Path) -> tuple[tuple[str, str, str], ...]:
    """Read and fully validate the papersim catalog (lock manifest + files).

    Mirrors the V1 ``migration_catalog`` contract with papersim parameters:
    entry shape, name pattern, hash equality against file bytes, exact
    inventory equality with the directory glob, and a transaction-wrapper
    body free of begin/commit/rollback/psql meta commands.
    """
    path = Path(repo_root) / LOCK_MANIFEST
    no_links(path)
    if path.stat().st_size > 262144:
        _fail('papersim_migration_manifest_invalid')
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if set(manifest) != {'format', 'migrations'} or manifest['format'] != MANIFEST_FORMAT:
            raise ValueError
        entries = manifest['migrations']
        if type(entries) is not list or not 1 <= len(entries) <= 1000:
            raise ValueError
        rows = []
        for item in entries:
            if set(item) != {'name', 'sha256', 'transaction_wrapper'}:
                raise ValueError
            name, digest = item['name'], item['sha256']
            if (not re.fullmatch(r'[0-9]{14}_[a-z0-9_]+\.sql', name)
                    or not re.fullmatch(r'[a-f0-9]{64}', digest)
                    or type(item['transaction_wrapper']) is not bool):
                raise ValueError
            raw = _migration_source(repo_root, name)
            if sha256(raw).hexdigest() != digest:
                _fail('papersim_migration_changed')
            body = raw.decode('utf-8')
            if item['transaction_wrapper']:
                body, start_count = re.subn(r'\A(?:\s|--[^\n]*\n)*begin;', '', body, count=1, flags=re.I)
                body, end_count = re.subn(r'commit;\s*\Z', '', body, count=1, flags=re.I)
                if (start_count, end_count) != (1, 1):
                    raise ValueError
            # These are fixed, reviewed scripts, NOT an arbitrary SQL executor.
            if FORBIDDEN_BODY_TOKENS.search(body):
                raise ValueError
            rows.append((name, digest, body))
        names = [row[0] for row in rows]
        actual = sorted(p.name for p in (Path(repo_root) / MIGRATIONS_DIRECTORY).glob('*.sql'))
        if names != sorted(set(names)) or names != actual:
            _fail('papersim_migration_inventory_mismatch')
        return tuple(rows)
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError):
        _fail('papersim_migration_manifest_invalid')


def papersim_pending_migrations(catalog, applied):
    """Return the unapplied tail; refuse any non-prefix history."""
    if type(applied) is not list or len(applied) > len(catalog):
        _fail('papersim_migration_history_conflict')
    expected = [{'name': name, 'sha256': digest} for name, digest, _ in catalog]
    if applied != expected[:len(applied)]:
        _fail('papersim_migration_history_conflict')
    return catalog[len(applied):]


def read_applied_migrations(conn) -> list[dict]:
    """Read the papersim ledger as the V1-shaped name/sha256 list.

    A database whose ``papersim_private.migrations`` does not exist yet is
    a PRE-bootstrap managed face (the bootstrap migration itself creates
    the ledger), so it reads as an empty applied list; anything else is a
    real ledger failure. The read runs inside its own transaction block
    (a SAVEPOINT when called inside one) so the undefined-table probe on a
    fresh face cannot abort the caller's open transaction. The driver
    error type is matched by class name so this module keeps no driver
    import.
    """
    try:
        with conn.transaction():
            cursor = conn.cursor()
            cursor.execute("SELECT coalesce(json_agg(json_build_object('name',name,'sha256',sha256) "
                           "ORDER BY name),'[]'::json) FROM " + LEDGER_RELATION)
            row = cursor.fetchone()
    except Exception as error:
        if type(error).__name__ == 'UndefinedTable':
            return []
        raise PapersimMigrationError('papersim_migration_ledger_missing') from error
    if row is None or row[0] is None:
        return []
    applied = row[0]
    if isinstance(applied, str):  # drivers may deliver json aggregates as text
        applied = json.loads(applied)
    if type(applied) is not list:
        _fail('papersim_migration_ledger_missing')
    return applied


def migration_status(conn, repo_root: Path) -> dict:
    """Report applied and pending migration names after prefix validation."""
    catalog = papersim_migration_catalog(repo_root)
    applied = read_applied_migrations(conn)
    pending = papersim_pending_migrations(catalog, applied)
    return {'applied': [row['name'] for row in applied],
            'pending': [name for name, _, _ in pending]}


def apply_pending_migrations(conn, repo_root: Path, *, lock_key: int = PAPERSIM_LOCK_KEY) -> dict:
    """Apply the verified pending tail; one transaction per migration.

    Per migration (09D v2 section 2.6): BEGIN; statement/lock timeouts;
    ``pg_advisory_xact_lock(PAPERSIM_LOCK_KEY)``; RE-READ the history under
    the lock (READ COMMITTED sees concurrently committed receipts and skips
    them idempotently); execute the body; insert the receipt; COMMIT. Any
    failure rolls back both DDL and receipt; the already-committed prefix is
    never replayed or repaired by hand.
    """
    catalog = papersim_migration_catalog(repo_root)
    applied = read_applied_migrations(conn)
    pending = papersim_pending_migrations(catalog, applied)
    applied_count = 0
    for name, digest, body in pending:
        with conn.transaction():
            cursor = conn.cursor()
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
            remaining = papersim_pending_migrations(catalog, read_applied_migrations(conn))
            if name not in {row[0] for row in remaining}:
                # A concurrent applier committed this migration while we
                # waited for the lock; the receipt is already correct.
                continue
            try:
                cursor.execute(body)
                cursor.execute("INSERT INTO " + LEDGER_RELATION + "(name,sha256) VALUES (%s,%s)",
                               (name, digest))
            except Exception as error:  # rollback happens via the context manager
                raise PapersimMigrationError('papersim_migration_apply_failed') from error
        applied_count += 1
    return {'applied': applied_count, 'pending_before': len(pending)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='python -m polymarket_alpha_lab.papersim.migrations',
        description='Apply or inspect the papersim managed migration catalog. '
                    'Paper evidence storage only; no execution authorization.')
    parser.add_argument('command', choices=('apply', 'status'))
    parser.add_argument('--repo-root', default='.',
                        help='repository root holding database/papersim (default: .)')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--managed', action='store_true',
                        help='owner connection through the project-private managed instance')
    source.add_argument('--dsn',
                        help='explicit owner DSN for an isolated/throwaway cluster '
                             '(validated by the single audited local DSN validator)')
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root)
    from polymarket_alpha_lab.papersim.store_psycopg import connect_explicit, connect_owner
    conn = connect_owner(repo_root) if args.managed else connect_explicit(args.dsn)
    try:
        if args.command == 'apply':
            result = apply_pending_migrations(conn, repo_root)
        else:
            result = migration_status(conn, repo_root)
    except PapersimMigrationError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':  # pragma: no cover - thin CLI shell
    raise SystemExit(main())
