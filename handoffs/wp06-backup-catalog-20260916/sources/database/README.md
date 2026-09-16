# Project-private native PostgreSQL

This is the current database deployment path. **No Docker, Supabase service,
cloud DB, Windows database service, or shared PostgreSQL instance is required.**
The project starts its own native PostgreSQL processes, owns a separate cluster,
and supplies generated project-only credentials. A PostgreSQL process is still
required; this is application-managed server software, not an in-process engine.


A clean Windows source + PostgreSQL distribution and automatic first-start check
are now available. See **[quickstart.md](quickstart.md)** for the two-command
setup, kit checksums, and the boundary that Python/dependencies remain required.

## Layout and one-time setup

```text
runtime/postgres/        # imported native bin/, lib/, share/, runtime.json
.local/postgres/         # private cluster, config, credentials, instance marker
.local/postgres.lock     # OS lifecycle lock; not a PID ownership assertion
database/migrations.lock.json
supabase/migrations/     # historical pathname ONLY; unchanged SQL identities
```

Only source/configuration templates and the migration lock enter Git. Runtime
binaries, generated passwords and PostgreSQL cluster files are ignored. Business
records remain in PostgreSQL, not in an extra JSON/file journal. Native server
logs/configuration and the cluster's own physical files are infrastructure.

Install the existing project environment including its Postgres driver:

```powershell
uv sync --locked --extra dev --extra postgres
```

A source checkout does not include hundreds of megabytes of platform binaries.
For Windows, obtain the official PostgreSQL binary ZIP from the PostgreSQL/EDB
channel and approve its SHA256 independently before import. The importer does
not silently download or execute software. Importing a trusted extracted prefix
is also supported; it must contain `bin`, `lib`, and `share`. Do NOT point at an
existing data directory. The importer copies only the runtime, never any source
cluster, service configuration, account or credential.

```powershell
# Official binary archive; replace both arguments with the approved local file/hash.
.\.venv\Scripts\python.exe scripts/project_database.py install-runtime --archive C:\Downloads\postgresql-binaries.zip --sha256 APPROVED_SHA256

# Alternative: explicitly trusted, already extracted portable native prefix.
.\.venv\Scripts\python.exe scripts/project_database.py install-runtime --from-directory C:\Downloads\pgsql

# Use ONE import method. No PostgreSQL system installation or service registration.
.\.venv\Scripts\python.exe scripts/project_database.py init
.\.venv\Scripts\python.exe scripts/project_database.py up
.\.venv\Scripts\python.exe scripts/project_database.py status
.\.venv\Scripts\python.exe scripts/project_database.py down
```

`init` creates a new cluster, installs all manifest-locked migrations, then
stops the server. Default port is 55432; choose `init --port N` once if necessary.
A busy port blocks; the project will not connect to whatever occupies that port.
A repeated init refuses an existing directory rather than resetting its data.
`up` is repeatable and `down` preserves the entire cluster. There is no `reset`,
`destroy`, force-kill, automatic upgrade, or arbitrary executable/SQL command.

Native prefixes for PostgreSQL majors 16, 17 and 18 are accepted only when all
five required programs agree on an exact version. Acceptance tests, not this
allowlist, determine which OS/version combinations have actually been verified;
see the PR/CI record. Changing a runtime version on an initialized project is
not an upgrade procedure. Keep a supported patched release and perform planned
backup/restore or pg_upgrade outside this initial lifecycle implementation.
The underlying platform's C/C++ runtime dependencies still apply.

Paths containing spaces are included in the native proof. Windows ancestors
must be accessible to the owning non-elevated account: PostgreSQL deliberately
drops administrator privileges. The manager does not alter ancestor ACLs or
disable that restriction; use a project directory owned by your ordinary account. Shell
metacharacters and quotes in the project path are rejected. The project path is
bound to its instance: copying/moving an initialized project is not an automatic
migration procedure. Existing external/Supabase data is NEVER adopted or copied.

## Windows runtime publication contention

Only the final `postgres.installing` -> `postgres` rename may be retried after a
Windows access/share/lock error (codes 5, 32 or 33). There are at most seven
attempts, with delays of 0.1, 0.2, 0.4, 0.8, 1.6 and 2.0 seconds. This caps the
requested sleeps at 5.1 seconds, not total filesystem/validation elapsed time.
The importer holds the same lifecycle lease throughout; it never repeats the
copy, native version probes, database initialization or a research/model call.

Before each attempt it checks directory identity, private permissions, absence
of a destination, engine hashes, import receipt and retained notices. It checks
again after publication. Changed content/identity, invalid permissions, a new
destination or unrelated errors stop immediately. A persistent eligible denial
returns `project_postgres_runtime_publish_blocked`; other publication I/O errors
return `project_postgres_runtime_publish_failed`, without exposing raw paths.

Access denied does not prove antivirus interference: genuine permission errors
can produce the same code. No antivirus exclusion, ACL relaxation, administrator
execution, process kill, overwrite, copy/move fallback or auto-cleanup is used.
An exhausted/failed import keeps its private staging for diagnosis. A subsequent
install refuses that incomplete state; do not delete it to make a test pass.
No automatic recovery/resume command is provided in this change.

## Application integration (no caller DSN)

```python
from pathlib import Path
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres

# prepared_request: existing CapturedResearchRequest from approved market intake.
# model_factory: explicitly configured client; no default live provider is enabled.
db = ProjectPostgres(Path(project_root))
with db.session() as research:
    result = research.run_research(request=prepared_request, model_factory=model_factory)
    # inspect(record_id=...), retry_capture(request=..., run=...), and
    # capture_outcome(...) use the same private, restricted application identity.
    diagnostics = research.evaluate().to_dict()
```

The session starts the server when necessary and stops only the instance it
started. If `up` had already started it explicitly, the session leaves it running.
An OS-held exclusive lifecycle lease prevents another process from stopping or
migrating the instance during the session. A single session may run multiple
research calls concurrently in threads. Its close waits for in-flight calls to
finish; injected model clients must supply their own bounded I/O. A crashed
application releases the OS lease but may leave PostgreSQL running; the next
explicit start/status verifies and reuses only that exact private instance.
There is no claim of perfect parent-death cleanup or automatic incomplete-job
recovery. The existing at-most-once research claim rules remain unchanged.

New managed sessions reject inherited `PG*` overrides, accept no external DSN,
and check the protected instance record on each actual research connection
before business SQL. The shared local DSN validator remains unchanged. The new
`local_postgres_dsn` import refers to the same audited validator as the historical
`supabase_local_dsn` module; there is no alternate weaker validator. Older explicit
DSN APIs and serialized `supabase` labels remain compatibility surfaces. Using
those old APIs directly does not establish project-instance binding.

The manager performs administration through the project's absolute `psql` path,
with `-X`, no prompts, sanitized PostgreSQL environment, short timeouts and SQL
on stdin. Child output is suppressed on errors. The pg_ctl launcher uses DEVNULL instead
of inherited PIPE handles, avoiding waits for its long-lived Windows descendants.
Passwords travel through private
passfiles, not command-line arguments or PGPASSWORD. Public command output contains
status/port/version/instance ID only, never a password or DSN.

## Ownership, permissions and limits

First initialization generates separate random 256-bit owner and application
passwords. Windows protects the private root with an owner/SYSTEM-only inherited
ACL; POSIX requires owner-only permissions. Symlinks and Windows reparse points
are rejected. Permissions and deterministic connection/authentication configs
are checked instead of silently repairing possibly foreign infrastructure.

PostgreSQL listens only on 127.0.0.1. Unix sockets are disabled. HBA allows only
the two project roles and the project database using SCRAM; the owner may also
connect to the administration database. Other database/user/address combinations
are rejected. The application role is not superuser and cannot create databases,
roles or schemas, read password catalogs, or mutate instance/migration metadata.
It receives SELECT/INSERT on project evidence tables, not UPDATE/DELETE/TRUNCATE.
The existing RLS-enabled assignment table keeps RLS, with exact application-only
SELECT/INSERT policies instead of BYPASSRLS. Conflicting policy definitions block.

Both disk and live server identities are checked (data directory, native cluster
system ID, project-path hash, random project instance ID and database identity).
Do not treat an obscure port, source hash, or application_name as authentication.
A native DB authenticates credentials, not the caller's source code. Another
program running as the same OS user may read these credentials; a local admin
can alter files, disable guards or inspect processes. This is a dedicated,
non-shared project instance, NOT a security sandbox against the owning user or
administrator. Stronger isolation needs a separate OS account/sandbox.

## Migrations and failure handling

Migration SQLs are retained byte-for-byte under their historical pathname;
none uses Supabase auth/storage/REST services. The current inventory is defined by
`database/migrations.lock.json`, not a duplicated count in this runbook. A
checked-in manifest binds the entire ordered set and records the two existing outer transaction wrappers.
A closed native bootstrap compatibility repair fixes the invalid historical
`type(@.reason_codes)` JSONPath syntax in migration 20260622000007 to the
PostgreSQL item method `@.reason_codes.type()`. Both the exact original and
effective SQL hashes are pinned in `project_postgres/migration_compat.py`; the
original file stays unchanged. The database ledger records the EFFECTIVE native
SQL hash, not a false claim that the unmodified invalid expression was executed.
Missing-field and non-array rejection remain, with real-engine negative probes.
No other migration receives a rewrite.

Initialization strips only those explicitly declared, hash-checked wrappers,
then applies each migration and its PostgreSQL ledger receipt in ONE transaction.
Applied history must be an exact unchanged prefix; unknown, reordered, missing,
or modified migrations block. The application cannot change the ledger.

For an existing managed instance, `up` reports pending migrations and a research
session refuses to run until explicitly upgraded:

```powershell
.\.venv\Scripts\python.exe scripts/project_database.py migrate
```

Review migration changes first. Adding a migration requires updating the manifest.
A failed migration rolls back its own DDL and ledger insertion; earlier committed
migrations and project records are retained. Native instance initialization that
fails leaves its private directory for diagnosis. There is no destructive retry.
Do not delete a cluster to make a check green. Automatic major upgrades, existing-instance import, OS services and cloud targets
are NOT implemented. The explicit cold backup/recovery commands below do not
provide any of those capabilities. A local data directory is not a backup; retain a separately verified
PostgreSQL backup/recovery procedure before real long-term data collection.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_project_postgres.py
.\.venv\Scripts\python.exe scripts/verify_local.py --full
```

The native integration test is separately opt-in and writes only to a new
temporary project (an explicitly protected unique RUNNER_TEMP child on Windows,
not pytest's potentially administrator-only ancestor). Its explicit prefix is a trusted binary source, not the database
being tested. It exercises initialization of all migrations, private authentication,
limited privileges, concurrent research capture, persisted readback after restart,
instance mismatch rejection, port conflicts, config tampering and DDL rollback.
It does not touch a user's installed DB or call a live model. The GitHub native
workflow uses runner-provided Windows binaries solely as this source and never
starts its installed Windows service. Default offline tests do not start servers.

Official deployment references:
- https://www.postgresql.org/download/windows/
- https://www.postgresql.org/docs/16/app-initdb.html
- https://www.postgresql.org/docs/16/app-pg-ctl.html
- https://www.postgresql.org/docs/16/auth-pg-hba-conf.html
- https://www.postgresql.org/docs/16/libpq-pgpass.html

## Private cold backups and absent-target recovery

The project now has `backup`, `verify-backup` and `restore` commands. These are
**whole PostgreSQL filesystem backups**, not SQL exports, alternate business
stores, distribution kits, migration tools, or cloud services. They retain
original server receipt timestamps, immutable claims, failed attempts, outcomes,
roles and migration history without replaying INSERTs or changing trigger rules.

Stop research sessions and explicitly stop the database first. The backup
command itself NEVER shuts down or interrupts a running server. Choose a NEW
backup directory OUTSIDE the project, under an already existing parent:

```powershell
.\.venv\Scripts\python.exe scripts/project_database.py down
.\.venv\Scripts\python.exe scripts/project_database.py backup --destination "D:\Backups\Polymarket-20260913"
```

The new directory is restricted to its owning account (and SYSTEM on Windows).
It holds `snapshot.palpg.zip` and `snapshot.palpg.sha256`. Save the returned SHA256
independently. **The ZIP is NOT encrypted and contains the generated database
passwords as well as all project records.** Never attach it to an issue, commit it,
put it in a public/shared folder, or confuse it with the public application kit.
The ignore rules prevent accidental staging of these default filenames; they
are not a data-loss-prevention system. Protect any copied/off-machine backup
with private permissions and encrypted storage; source code/runtime copies must
be retained separately. A second folder on the same failing disk is not disaster
recovery. No scheduled backup, media transfer or retention deletion is enabled.

Verification streams the entire archive and checks its inventory, hashes,
format, original project-path identity, current platform, exact engine file
inventory/version, and the effective migration inventory. It does not start a
server. Both verification and restore require explicit trust and a separately
retained checksum; hashes alone cannot authenticate a malicious replacement.
Restore only a backup of your OWN trusted database. Its catalogs/configuration
can affect native server behavior on a later explicit start.

```powershell
.\.venv\Scripts\python.exe scripts/project_database.py verify-backup --archive "D:\Backups\Polymarket-20260913\snapshot.palpg.zip" --sha256 YOUR_RETAINED_SHA256 --trusted-backup
```

Recovery is intentionally limited to the SAME project location with identical
native runtime and, by default, identical migration inventory, and an ABSENT
`.local/postgres` directory. The explicit catalog-extension exception below does
not relax the original path, engine, trust, checksum or absent-target requirements.
It is NOT an in-place rollback or a way to copy/move a working project. The manager
will not rename, delete or overwrite existing data, even an empty/partial target.
Keep an existing damaged/original directory safely preserved through a separately
reviewed operator recovery procedure; do not delete it to make this command pass.

```powershell
# Disaster recovery only, after original files are safely preserved and the target is absent.
.\.venv\Scripts\python.exe scripts/project_database.py restore --archive "D:\Backups\Polymarket-20260913\snapshot.palpg.zip" --sha256 YOUR_RETAINED_SHA256 --trusted-backup
.\.venv\Scripts\python.exe scripts/start_project.py
```

Restore validates all archive content before creating its private staging tree,
rechecks extracted bytes and native clean-shutdown/configuration/identity, then
publishes the complete directory once. A successful restore leaves the DB stopped;
normal startup performs live identity/role/history checks. Incomplete research
claims remain incomplete and can STILL block strict evaluation. Backup is not
model replay, failed-job recovery or evidence of good forecasting performance.
Only data included in the snapshot can be recovered; later changes are not in it.

Backup creation and restore hold the existing OS lifecycle lock; verification
does not start the engine or take that lease. A live PID, unclean control
state, recovery markers, tablespaces, symlinks/reparse points, hard-linked files,
path collisions, occupied port, changed runtime/migrations or existing restore
staging block. Current bounds: 50,000 filesystem entries and 8 GiB uncompressed
content, with bounded reads; this is a compact single-cluster first version, not
large-database streaming replication or point-in-time recovery. Failed backups
or restores retain PRIVATE partial files for diagnosis, never trigger reset.
Checks assume the owning OS user/admin and approved source runtime are trusted;
manual out-of-band native commands bypass application lifecycle coordination.

An interrupted operation may leave `snapshot.partial` in the newly created
backup directory or `.local/postgres.restoring` during recovery. Neither is a
successful backup/restore receipt. Inspect and preserve these through an explicit
operator procedure; automatic cleanup/retry/force recovery is not provided.
There is no promise of atomic multi-device durability against disk/power failure.

Native acceptance runs only on an isolated temporary project. It verifies
original record/outcome timestamps and fixed-time evaluation equality after real
recovery, unchanged credentials/identity, limited-role enforcement, no model
restart for duplicate tasks, and retention of incomplete claims. Only test logs
and JUnit are uploaded, NEVER a database backup. Default offline CI skips this
native proof; its separate successful CI is required before release.

Official physical-backup requirements:
https://www.postgresql.org/docs/17/backup-file.html
https://www.postgresql.org/docs/17/app-pgcontroldata.html

## Durable dispatch schema

The native catalog now includes the append-only research dispatch batch tail
`20260914000000_research_dispatch_batches.sql`. Original migration bytes are
unchanged. Batch admission/run requires this schema; it never migrates implicitly.
See [batch operation and safe upgrade limits](../docs/research-dispatch.md).
Do not overlay or edit an old immutable kit to obtain this table.


## Explicit backup/catalog extension compatibility (not an upgrade)

A previously created v1 cold backup remains immutable. When a reviewed SOURCE
installation has only appended migrations, `verify-backup` and `restore` can now
accept its older catalog with the separate `--allow-catalog-extension` flag.
WITHOUT that flag, the original exact-catalog match is still required. API callers
must supply the actual boolean `allow_catalog_extension=True`, not a truthy value.
The backup format, original archive bytes and stored fingerprint are unchanged.

The current catalog is fully validated first, including the added tail's file
hashes and inventory. The backup fingerprint must equal a NONEMPTY exact prefix
of the ordered effective migration names/hashes. This reuses the existing native
migration compatibility transform. Deleted, renamed, reordered or modified old
migrations, missing/unlisted files, a newer backup with a shorter current catalog,
and even an invalid new tail are refused. The flag cannot override platform,
physical root, runtime version/inventory, archive trust/checksum or existing-data
checks. It does not authenticate an untrusted backup or approve unknown new SQL.

On this opt-in path the existing receipt adds `migration_catalog`: `match`,
`backup_entries`, `current_entries`, `additional_entries`, `current_sha256`,
`database_ledger_checked=false` and `migrations_applied=0`. Counts describe the
CATALOG captured with the backup, not the database's applied migration ledger.
A snapshot could have been made with pending migrations. The full current
fingerprint, not just counts, is rechecked before an opted-in restore publishes;
a changed catalog/runtime leaves private staging rather than reporting success.
This is bounded integrity checking under the existing trusted-owner assumption,
not synchronization with out-of-band file edits by that owner.

For an explicitly reviewed source installation at the ORIGINAL physical root,
a private original backup and its independently retained checksum:

```powershell
.\.venv\Scripts\python.exe scripts/project_database.py verify-backup --archive "D:\Backups\Original\snapshot.palpg.zip" --sha256 YOUR_RETAINED_SHA256 --trusted-backup --allow-catalog-extension
# Restore only under an approved recovery procedure with originals preserved and target ABSENT:
.\.venv\Scripts\python.exe scripts/project_database.py restore --archive "D:\Backups\Original\snapshot.palpg.zip" --sha256 YOUR_RETAINED_SHA256 --trusted-backup --allow-catalog-extension
```

Check each command's exit status and original JSON; do not chain blindly after a
failed verification. The backup still contains private credentials and must not
be uploaded. Restore leaves the database STOPPED and performs no SQL migration.
The next managed session still validates the actual ledger and refuses pending
migrations. Only a separate reviewed `migrate` operation applies the pending tail;
it is not authorized by verifying or restoring a backup. Do not call old code
against newer schemas as a rollback strategy. A shorter catalog cannot make a
newer snapshot compatible.

This does NOT permit an immutable old kit overlay, altered bundle manifest,
engine change, root relocation, in-place restore or automatic schema rollback.
Keep the existing root/engine/source versions and private originals under the
release-specific recovery plan. This addition removes only the strict catalog
fingerprint mismatch for an expressly approved append-only extension.

The isolated native test creates a real 66-migration database, retains completed
and failed synthetic research, takes a cold backup, and installs the unchanged
existing 67th migration in its SOURCE catalog. It checks default refusal, opt-in
verification, existing-target refusal, restore into the same absent path, live
pending-ledger refusal, then an explicit one-migration upgrade and unchanged
original records/identity. No user's files or backups are used. Final-head test
results and limitations belong in the implementation PR; this is not G6 closure.

PostgreSQL's filesystem backup requirements remain applicable: use the whole
cluster and a clean shutdown, not selective table files. Primary reference:
https://www.postgresql.org/docs/17/backup-file.html (checked 2026-09-16).
