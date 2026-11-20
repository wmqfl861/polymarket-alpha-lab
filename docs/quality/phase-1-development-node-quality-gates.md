# Phase 1 Development Node Quality Gates

Date: 2026-07-12
Status: active Phase 1 pre-submit requirement
Scope: quality gate and acceptance checklist documentation

## Purpose

Every Phase 1 development node must pass this checklist before it is treated as
ready for review, commit, or GitHub push. The checklist applies to code,
tests, configuration, migrations, docs, and generated artifacts that are part
of a development node.

Phase 1 remains paper-only, report-only, readonly, and local
Supabase/Postgres-only for durable project persistence. Passing this checklist
does not authorize live trading, credential use, account access, wallet access,
or order mutation.

## Required Command Gates

Run the gates from the repository root unless a node-specific plan gives a
narrower path. Record the exact commands and outcomes in the handoff or review
packet.

### 1. Compile Gate

Required command:

```bash
python -m compileall src tests
```
Acceptance requirement:

- command exits with status `0`;
- no syntax errors are reported;
- no generated cache or bytecode file is intentionally committed unless the
  repository already tracks it for a documented reason.

### 2. Targeted Test Gate

Run the smallest test set that directly covers the changed behavior.

Examples:

```bash
pytest tests/test_changed_surface.py
pytest tests/test_changed_surface.py::test_specific_behavior
pytest -k "changed_surface or related_contract"
```

Acceptance requirement:

- every new or changed behavior has at least one targeted test or an explicit
  documented reason why the node is documentation-only;
- targeted tests pass with status `0`;
- failures are fixed at the source rather than bypassed with broad skips,
  xfails, relaxed assertions, or unreviewed fixture changes.

### 3. Full Pytest Gate

Required command:

```bash
pytest
```

Acceptance requirement:

- full test suite exits with status `0`;
- no unrelated failures are ignored;
- any pre-existing failure must be documented with evidence that it predates
  the node and does not invalidate the changed surface;
- no live network, hosted account, wallet, credential, or exchange mutation
  dependency is required for the suite.

### 4. Whitespace And Patch Hygiene Gate

Required command:

```bash
git diff --check
```

Acceptance requirement:

- command exits with status `0`;
- no trailing whitespace, conflict marker, or whitespace error remains;
- generated diffs are reviewable and do not include unrelated formatting churn.

## Boundary And Safety Gates

These gates are required even when command tests pass.

### 5. Phase 1 Readonly Boundary Scan

Scan changed files for new or expanded behavior that could breach the Phase 1
boundary.

Minimum command pattern:

```bash
git diff --name-only
git diff -- src tests docs pyproject.toml strategy.example.json strategy.example.phase1-screening.json
rg -n "live trading|order submission|submit order|cancel order|replace order|sign order|wallet|private key|account auth|account authentication|hosted account|exchange mutation|order mutation" .
```

Acceptance requirement:

- changed behavior remains paper-only, report-only, and readonly;
- no account authentication, wallet handling, private-key handling, hosted
  account reads, order signing, order submission, order cancellation, order
  replacement, exchange mutation, or live trading behavior is introduced;
- report payloads preserve `paper_only=True`, `report_only=True`, and
  `readonly=True` wherever the surface supports those flags;
- market scores, candidate statuses, memory policies, go/no-go statuses, and
  readiness flags do not become order instructions, allocation instructions, or
  live execution approvals.

### 6. Local Supabase/Postgres Persistence Gate

Any node touching durable storage, database helpers, persistence adapters,
fixtures, report histories, or configuration must prove that durable project
persistence remains local Supabase/Postgres-only.

Minimum checks:

```bash
rg -n "sqlite|duckdb|redis|mongodb|sqlalchemy|jsonl|csv|filesystem|file-backed|durable store|database_url|postgres|supabase|dsn" src tests docs supabase
rg -n "validate_local_postgres_dsn|LOCAL_SUPABASE|SUPABASE|POSTGRES|DATABASE_URL" src tests docs supabase
```

Acceptance requirement:

- raw DSNs are validated through `validate_local_postgres_dsn` before any
  connection or psycopg wrapper is opened;
- missing, malformed, hosted, or non-local DSNs fail closed;
- no new SQLite, DuckDB, Redis, MongoDB, SQLAlchemy-managed durable engine,
  generic durable-store abstraction, JSONL journal, CSV ledger, or
  filesystem-backed durable substitute is introduced;
- legacy file-backed surfaces are not expanded as new durable project
  persistence unless a separate migration node explicitly documents and reviews
  that transition;
- docs and examples use placeholders such as `<redacted>` rather than raw DSNs.

### 7. Secret And Live-Trading Field Gate

Changed files and generated outputs must be scanned for secrets and unsafe
live-trading fields before review or push.

Minimum command pattern:

```bash
git diff --cached --name-only
git diff --name-only
rg -n "api[_-]?key|secret|token|password|passwd|private[_-]?key|seed phrase|mnemonic|cookie|authorization|bearer|wallet|clob|signature|signed|order_id|live_order|account_id|account_address" .
```

Acceptance requirement:

- no raw DSN, password, API key, auth token, cookie, private key, seed phrase,
  wallet material, account identifier, hosted account detail, credential-like
  header, live-account order id, or private source payload is committed;
- examples use non-sensitive placeholders;
- live-trading-like fields are absent unless they are explicitly documented as
  forbidden-field tests, redaction examples, or readonly boundary assertions;
- no output logs or review packets expose secrets or live-account details.

## Review Gate

### 8. Independent Read-Only Review Requirement

Before a Phase 1 development node is considered complete, submit a read-only
review packet to a fresh, independent reviewer under Project Iron Rule 4
and [Native Codex Subagent Routing](../../AGENTS.md#native-codex-subagent-routing).

The review packet must include:

- node goal and explicit Phase 1 boundary;
- changed paths;
- implementation summary;
- targeted test command and result;
- full `pytest` command and result;
- `python -m compileall src tests` result;
- `git diff --check` result;
- readonly boundary scan result;
- local Supabase/Postgres persistence check result, when applicable;
- secret and no-live-trading-field scan result;
- known risks, open questions, and any reviewer follow-ups.

Acceptance requirement:

- the current revision receives `VERDICT: PASS` after any blocking findings
  are fixed and independently re-reviewed;
- the gate is blocked if no eligible independent reviewer can be dispatched;
- any change to the review requirement requires explicit user instruction;
  project rule;
- the reviewer is not allowed to mutate files, databases, exchange state,
  account state, credentials, wallets, or orders.

## GitHub Push Gate

### 9. Pre-Push Conditions

Do not push a Phase 1 node to GitHub until all of the following are true:

- compile gate passed;
- targeted tests passed;
- full `pytest` passed;
- `git diff --check` passed;
- readonly boundary scan passed;
- local Supabase/Postgres persistence gate passed for any persistence-related
  change;
- secret and no-live-trading-field gate passed;
- independent read-only review passed for the current revision;
- the diff contains only intended files for the node;
- unrelated user or parallel-worker changes are excluded from the commit;
- no live trading, credential, wallet, account, or order-mutation behavior is
  included.

Push is blocked when any required gate is failing, skipped without a documented
reason, unavailable, or contradicted by the diff.

## Documentation-Only Node Exception

Documentation-only nodes still require:

- `git diff --check`;
- boundary scan for unsafe language that appears to authorize live execution;
- secret scan for examples and copied output;
- independent read-only review when the document changes project rules,
  acceptance criteria, phase boundaries, persistence policy, or operator
  workflow;
- a clear statement that no code, tests, configuration, CLI, Supabase,
  execution/auth, or live trading behavior was changed.

Documentation-only nodes may skip `compileall`, targeted tests, full `pytest`,
and persistence command checks only when the review packet explicitly states
that no executable or persistence behavior changed.

## Completion Statement Template

Use this template in handoff notes before review or push:

```text
Phase 1 quality gates:
- compileall: pass | skipped because documentation-only
- targeted tests: pass | skipped because documentation-only
- full pytest: pass | skipped because documentation-only
- git diff --check: pass
- readonly boundary scan: pass
- local Supabase/Postgres persistence check: pass | not applicable
- secret/no-live-trading-field scan: pass
- independent read-only review: VERDICT: PASS | VERDICT: FAIL | blocked
- GitHub push: allowed only after all required gates pass; not authorized by this note alone
```
