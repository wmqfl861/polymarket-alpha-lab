# Node 5 Plan: Team Forecast Atomic Persistence

This July 2026 plan retains its original review instructions for receipt
and implementation history. For a current review, follow
[Native Codex Subagent Routing](../../../AGENTS.md#native-codex-subagent-routing).

## Goal

Enforce one atomic local-Supabase/Postgres write path for validated Node 3 and future Node 7 team-evaluation attempts. Persist complete Node 3 `evaluation_scope_payload` rows through the Node 4 codec, prove `ready`/`watch`/`blocked` round trips, idempotent retry, detached-run orphan rejection, and rollback with zero partial rows, and keep all legacy/public insert APIs unable to write V1 identifiers.

The implementation starts from local candidate base `e53fcf5c`. A remote-publication gate that remains blocked is recorded as blocked evidence; it is never replaced by local ancestry, an automatic rebase, or an unapproved push.

## Architecture

Add two modules:

- `team_evidence_aggregation_attempt_store.py`: DB-API repository. It owns validated table identifiers, read queries, latest-attempt ordering, the public legacy-insert rejection fence, and one private transaction-scoped batch insertion function.
- `team_evidence_aggregation_attempt_psycopg.py`: lazy psycopg adapter. It validates all service inputs before connection, validates every DSN through `validate_local_postgres_dsn`, opens one owned connection, invokes the private store path for every prepared row, commits exactly once, rolls back on every `BaseException`, and closes the connection.

The sole V1 write path is:

1. Receive one or more already validated `TeamForecastBuildEnvelope` values.
2. Recheck envelope identity and run binding without database activity.
3. Convert each payload with `team_evaluation_attempt_to_db_row`.
4. Convert each row with `team_evaluation_attempt_row_parameters`.
5. Execute all rows on one connection and one transaction using `ON CONFLICT (tea_id) DO NOTHING`.
6. Return one immutable write result per input, preserving input order and exposing `inserted=True` for rowcount `1` and `inserted=False` for duplicate rowcount `0`.

The public compatibility functions `insert_team_evaluation_attempt` and `insert_team_evaluation_attempt_with_psycopg` are legacy surfaces. They reject every `tea:v1:`, `tfr:v1:`, or `tfe:v1:` identifier before cursor or connection activity. They never call the private writer. The new atomic writer is the only function permitted to insert V1 rows.

Because the schema has no run table or foreign key, “orphan” has a deterministic contract: an attempt is orphaned when `tea_id` is not exactly `team_evidence_aggregation_id(evaluation_scope_payload)`, when `run_metadata` is absent or malformed, or when `tfr_id` is not exactly `team_forecast_run_id(tea_id, evaluation_scope_payload["run_metadata"])`. Such input fails before DSN validation, psycopg import, connection construction, or SQL.

Reads expose all rows and a latest-attempt helper ordered strictly by `attempted_at DESC, tea_id DESC` within `tfr_id` or `(scope_version, scope_key)`. The latest row is returned regardless of status; no fallback to an older ready row is allowed.

## Tech Stack

Python 3.12, psycopg 3.3.4, DB-API compatible connections, immutable/slotted dataclasses, Node 3 and Node 4 codecs, local Supabase/Postgres only, pytest fake-connection tests, Python AST/import/line-size guards, `psql --single-transaction`, CodeGraph, and read-only Codex review with `gpt-6-astra` at `model_reasoning_effort=max`.

## Global Constraints

- Preserve `paper_only=True`, `report_only=True`, and `readonly=True` on every write result and persisted row.
- Local Supabase/Postgres is the only durable persistence target. Do not add SQLite, Redis, Mongo, SQLAlchemy, file journals, JSONL, hosted DB assumptions, or generic persistence abstractions.
- Every raw DSN from environment, config, CLI plumbing, fixtures, or helper construction passes through `validate_local_postgres_dsn` before any connection, psycopg wrapper, adapter, repository, or store is constructed. DSNs and secrets are never printed, logged, interpolated into errors, or written to docs.
- Invalid envelopes, rows, table names, status values, identifiers, and orphaned run bindings fail before database activity.
- No update, delete, replacement, upsert, account authentication, wallet, key, signing, order, exchange mutation, or live-trading path is added.
- `scripts/verify_local.py` remains offline. The disposable proof is a separately gated test and is never made a default connection path.
- The existing migration remains unchanged. Operator documentation may describe applying it to a disposable database, but Node 5 does not add a migration.
- The package root is unchanged; no new root exports are added.
- SQL text is confined to the store module. The psycopg adapter may only invoke store functions and transaction methods.
- The private writer owns the transaction boundary indirectly through its caller; it never commits or rolls back itself.

## Exact Sorted Implementation Allowlist

Only these paths may change, in literal `LC_ALL=C` order:

```bash
NODE_PATHS=(
  docs/team-evidence-aggregation-migration-safety.md
  docs/team-evidence-aggregation-supabase-runbook.md
  src/polymarket_alpha_lab/team_evidence_aggregation_attempt_psycopg.py
  src/polymarket_alpha_lab/team_evidence_aggregation_attempt_store.py
  tests/test_team_evidence_aggregation_attempt_disposable.py
  tests/test_team_evidence_aggregation_attempt_psycopg.py
  tests/test_team_evidence_aggregation_attempt_scope.py
  tests/test_team_evidence_aggregation_attempt_store.py
)
EXPECTED_NODE_PATHS="$(printf '%s\n' "${NODE_PATHS[@]}")"
test "$(printf '%s\n' "${NODE_PATHS[@]}" | LC_ALL=C sort)" = "$EXPECTED_NODE_PATHS"
```

No package-root file, migration, configuration module, `scripts/verify_local.py`, or unrelated test may change. The plan document is a separate planning artifact.

## Fixed Predecessor Interfaces

Consume these exact Node 3 and Node 4 interfaces:

```python
TeamForecastBuildEnvelope

team_forecast_evaluation_scope_payload(
    envelope: TeamForecastBuildEnvelope,
) -> dict[str, object]

team_evidence_aggregation_id(
    evaluation_scope_payload: dict[str, object],
) -> str

team_forecast_run_id(
    tea_id: str,
    run_metadata_payload: dict[str, object],
) -> str

team_evaluation_attempt_to_db_row(
    *,
    tea_id: str,
    tfr_id: str,
    evaluation_scope_payload: dict[str, object],
) -> TeamEvaluationAttemptDbRow

team_evaluation_attempt_row_parameters(
    row: TeamEvaluationAttemptDbRow,
) -> dict[str, Any]

validate_local_postgres_dsn(
    value: str,
    *,
    env_var_name: str,
) -> None
```

The Node 4 row codec remains the authority for the exact eight payload keys, promoted columns, status domain, hard-flag agreement, canonical timestamps, scope key, and payload hash.

## Implementation Tasks

- [ ] **Task 1 — Base and handoff checks (RED prerequisite)**
  - Record `NODE_BASE=e53fcf5c`.
  - Verify branch, clean worktree, unchanged Node 3/4 interfaces, and exact allowlist.
  - Record remote-publication or predecessor-receipt gaps as blocked evidence; do not substitute local ancestry.

- [ ] **Task 2 — Store behavior tests (RED)**
  - Add fake cursor/connection tests for exact INSERT columns, parameter order, table-name validation, `ON CONFLICT (tea_id) DO NOTHING`, rowcount-to-outcome mapping, latest-attempt ordering, and read filters.
  - Prove the public DB-API insert rejects V1 IDs before `cursor()` and that the private batch writer is the only SQL insertion path.
  - Prove `ready`, `watch`, and `blocked` rows preserve status and `hard_flag` semantics.

- [ ] **Task 3 — Store implementation (GREEN)**
  - Implement `TeamEvaluationAttemptWriteResult`.
  - Implement public legacy insert rejection, row loaders, latest-attempt loader, and private `_insert_attempt_rows_atomic`.
  - Keep SQL and identifier construction inside this module; never commit or rollback here.

- [ ] **Task 4 — Psycopg adapter tests and implementation (RED → GREEN)**
  - Validate envelope, orphan binding, row conversion, table name, and batch shape before `_connect`.
  - Test validator-before-connect, validator-before-psycopg-import, lazy `psycopg`/`Jsonb` import, DSN redaction, one commit, rollback on store failure, rollback on commit failure, and guaranteed close.
  - Test duplicate retry returns stable `inserted=False` without changing the existing row.
  - Test the public psycopg legacy insert rejects `tea:v1:`, `tfr:v1:`, and `tfe:v1:` before connection activity.

- [ ] **Task 5 — Disposable proof and documentation**
  - Add `tests/test_team_evidence_aggregation_attempt_disposable.py`, gated by `POLYMARKET_ALPHA_LAB_RUN_TEAM_EVIDENCE_AGGREGATION_DISPOSABLE_DB=1`; default is skipped.
  - Update the existing Supabase runbook with the Windows-native create/apply/prove/drop procedure.
  - Update migration safety with atomic batch, retry, orphan, rollback, and dedicated-database cleanup rules.
  - Do not wire the proof into `scripts/verify_local.py`.

- [ ] **Task 6 — Scope guard**
  - Enforce imports, exports, forbidden surfaces, SQL ownership, no package-root changes, and the line ceilings below.
  - Ensure no alternate database, filesystem persistence, secret-bearing text, live execution, or direct psycopg connection exists outside the adapter.

## Line Ceilings

- `team_evidence_aggregation_attempt_store.py`: ≤420 lines
- `team_evidence_aggregation_attempt_psycopg.py`: ≤380 lines
- Each offline behavior test: ≤620 lines
- Disposable proof test: ≤460 lines
- Scope test: ≤320 lines
- Each updated runbook/safety document: ≤260 lines
- Aggregate Node 5 production source: ≤800 lines
- Aggregate Node 5 tests: ≤2,000 lines

## Verification Commands

Default offline gate:

```bash
unset PYTEST_ADDOPTS PYTEST_PLUGINS PYTHONPATH PYTHONHOME
export PYTHONUTF8=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export POLYMARKET_ALPHA_LAB_RUN_SUPABASE_SMOKE=0
export POLYMARKET_ALPHA_LAB_RUN_TEAM_EVIDENCE_AGGREGATION_DISPOSABLE_DB=0

.venv/Scripts/python.exe -m pytest -q \
  tests/test_team_evidence_aggregation_attempt_store.py \
  tests/test_team_evidence_aggregation_attempt_psycopg.py \
  tests/test_team_evidence_aggregation_attempt_scope.py

.venv/Scripts/python.exe -m compileall -q src tests
.venv/Scripts/python.exe -m pytest --collect-only -q
.venv/Scripts/python.exe scripts/verify_local.py --full
git diff --check
```

Authorized disposable proof only, using PowerShell and the dedicated database name `polymarket_alpha_lab_node5_disposable`:

```powershell
$ErrorActionPreference = "Stop"
$dbName = "polymarket_alpha_lab_node5_disposable"
$created = $false

# Inject both DSNs through the approved operator channel; never echo them.
# POLYMARKET_ALPHA_LAB_NODE5_DISPOSABLE_MAINTENANCE_DSN points only to
# the local maintenance database. The target DSN names $dbName.
& .venv\Scripts\python.exe -c "import os; from polymarket_alpha_lab.supabase_local_dsn import validate_local_postgres_dsn as v; v(os.environ['POLYMARKET_ALPHA_LAB_NODE5_DISPOSABLE_MAINTENANCE_DSN'], env_var_name='POLYMARKET_ALPHA_LAB_NODE5_DISPOSABLE_MAINTENANCE_DSN'); v(os.environ['POLYMARKET_ALPHA_LAB_TEAM_EVIDENCE_AGGREGATION_DB_DSN'], env_var_name='POLYMARKET_ALPHA_LAB_TEAM_EVIDENCE_AGGREGATION_DB_DSN')"

try {
  $exists = (& psql -X -qAt -v ON_ERROR_STOP=1 --dbname=$env:POLYMARKET_ALPHA_LAB_NODE5_DISPOSABLE_MAINTENANCE_DSN -c "select 1 from pg_database where datname = '$dbName'").Trim()
  if ($exists -eq "1") { throw "dedicated disposable database already exists; refuse to reuse or drop it" }

  & psql -X -v ON_ERROR_STOP=1 --dbname=$env:POLYMARKET_ALPHA_LAB_NODE5_DISPOSABLE_MAINTENANCE_DSN -c "create database $dbName"
  $created = $true

  & psql -X --single-transaction -v ON_ERROR_STOP=1 `
    --dbname=$env:POLYMARKET_ALPHA_LAB_TEAM_EVIDENCE_AGGREGATION_DB_DSN `
    --file=supabase/migrations/20260714000000_team_evidence_aggregation_tables.sql

  $env:POLYMARKET_ALPHA_LAB_RUN_TEAM_EVIDENCE_AGGREGATION_DISPOSABLE_DB = "1"
  & .venv\Scripts\python.exe -m pytest -q tests/test_team_evidence_aggregation_attempt_disposable.py
}
finally {
  if ($created) {
    & psql -X -v ON_ERROR_STOP=1 --dbname=$env:POLYMARKET_ALPHA_LAB_NODE5_DISPOSABLE_MAINTENANCE_DSN -c "drop database $dbName with (force)"
  }
}
```

The maintenance database is used only for catalog inspection and `CREATE/DROP DATABASE`; no shared database is migrated, reset, or data-mutated. Any create, migration, proof, or drop failure is recorded; cleanup is still attempted in `finally`.

Disposable evidence must record only redacted metadata:

```text
database_name=polymarket_alpha_lab_node5_disposable
migration_apply=pass
status_round_trip=ready,watch,blocked
retry_first_inserted=true
retry_second_inserted=false
retry_row_count_stable=true
orphan_rejected_before_connect=true
rollback_zero_rows=true
cleanup_drop=pass
dsn=redacted
```

The proof must assert that latest-attempt reads use `attempted_at DESC, tea_id DESC`, that no newer watch/blocked row is bypassed, and that rollback after an injected second-row failure leaves zero rows.

After implementation, obtain a read-only Codex review with `gpt-6-astra`, `model_reasoning_effort=max`, over the complete `NODE_BASE..HEAD` range. The exact final nonblank line must be `VERDICT: PASS`. A non-PASS review blocks the node.

## Stop Conditions

Stop immediately for:

- Any shared-database schema/data mutation, database name other than the dedicated disposable name, missing cleanup, secret or DSN disclosure, or unvalidated DSN.
- Any connection or psycopg import before invalid-service-input rejection or DSN validation.
- Any second V1 insertion path, public legacy API accepting V1 IDs, update/delete/upsert behavior, or non-atomic multi-connection write.
- Any orphan binding accepted, status mismatch, partial row after rollback, unstable retry result, or latest-attempt fallback.
- Any migration edit, package-root export, alternate persistence backend, file-backed fallback, line-ceiling breach, forbidden-surface violation, failing focused/full test, compile failure, `git diff --check` failure, or missing CodeGraph/secret-scan evidence.
- Any review result other than exact `VERDICT: PASS`.
- Remote `main` movement or unavailable publication evidence; do not rebase or push automatically.

## Completion Evidence

Record:

```text
NODE_BASE: e53fcf5c
exact sorted allowlist: pass
focused offline tests: pass
disposable proof: pass or skipped-by-default
status round trip: pass
retry idempotency: pass
orphan rejection: pass
rollback zero rows: pass
compileall: pass
full pytest: pass
scripts/verify_local.py offline: pass
CodeGraph sync: pass or documented-unavailable
secret/Phase-1/local-DB scans: pass
git diff --check: pass
clean worktree: pass
Codex gpt-6-astra/max review: VERDICT: PASS
remote publication: pass or blocked, never inferred locally
cleanup evidence: dedicated database dropped in finally
```
