# Operating Review Rules

Date: 2026-07-12
Status: current review and coordination rule checkpoint
Scope: documentation-only review node

## Purpose

This document records the review, parallel-development, memory, and local
Supabase/Postgres rules that future project nodes must treat as active
constraints. It is designed for stage reviewers and implementation workers who
need a short, auditable checklist.

## Review Routing

Dispatch a fresh, independent, read-only reviewer for each plan, code,
stage, post-node, and handoff review under Project Iron Rule 4 and
[Project Agent Routing](../../AGENTS.md#project-agent-routing).
The gate is blocked when no eligible independent reviewer can be dispatched.
There is no fallback reviewer under the current project rules.

Review prompts must be read-only. Reviewers may inspect plans, diffs, files,
reports, test output, and documentation. Reviewers must not modify, create,
delete, migrate, backfill, mutate databases, mutate exchange state, alter
account state, handle credentials, or submit/cancel/replace orders.

## Long-Running Review Monitoring

Review subagents must not be wrapped in a fixed elapsed-time
timeout. Run long reviews in an inspectable session and check them about every
30 seconds. At each check, observe process/session liveness and, when available,
stream growth or event count, stderr or terminal events, CPU, and network
activity. Elapsed time alone or a quiet interval is not evidence of a stall.
While the review remains alive and no concrete terminal failure or stall is
proven, do not interrupt, terminate, restart, duplicate, or replace it, and do
not route around it; keep waiting and monitoring. Act only on an explicit result
or error, confirmed process/session exit, concrete auth/permission/provider
failure, proven stall, or a newer user instruction.

## Required Review Packet Contents

Every implementation-stage review packet should include:

- goal and explicit stage boundary;
- changed paths;
- disjoint file/module ownership when parallel work was used;
- Phase 1 boundary assertions;
- local Supabase/Postgres durable-only implications;
- DSN validation implications where any DB config, adapter, fixture, or helper
  is touched;
- focused tests and verification commands;
- `git diff --check` status;
- secret/redaction check status for tracked content;
- open risks and operator follow-ups.

## Parallel Development Rules

Parallel work is allowed and expected when tasks are independent, but it must
not create write conflicts or dilute review quality.

Active rules:

- Keep multiple workers active only when work is independent and useful.
- Split write ownership by non-overlapping files, modules, or responsibility
  boundaries before dispatch.
- Do not assign two workers to edit the same file, the same batch of files, or
  the same tightly coupled behavior at the same time.
- Close completed workers promptly and redeploy capacity only to independent
  follow-up work.
- The project sets no fixed subagent concurrency count; discover usable
  capacity dynamically from the current runtime. Nested depth remains capped
  at 3.
- Use lower concurrency when rate limits, memory pressure, shared test
  resources, or write-scope overlap would reduce quality.
- Every worker must preserve the Phase 1 paper-only/report-only/readonly
  boundary.

## Local Supabase/Postgres Review Checklist

All durable project persistence must target local Supabase/Postgres only.

Reviewers must reject new durable persistence that uses or implies:

- SQLite;
- DuckDB;
- Redis;
- MongoDB;
- SQLAlchemy-managed durable engines;
- hosted remote database assumptions;
- generic durable-store abstractions;
- JSONL/file journals as new durable project storage;
- CSV or filesystem-backed durable project storage.

Reviewers must verify that every raw DSN crossing environment, config, CLI
plumbing, tests, fixtures, helper construction, or adapter construction is
validated with `validate_local_postgres_dsn` before any connection or psycopg
wrapper is opened.

Legacy file-backed surfaces are compatibility-only unless explicitly migrated.
Reviewers should reject any node that expands legacy JSONL/file surfaces as new
durable persistence.

## Phase 1 Boundary Review Checklist

Reviewers must confirm that the changed surface does not add:

- live trading;
- account authentication;
- wallet handling;
- private-key handling;
- hosted account reads;
- order signing;
- order submission;
- order cancellation;
- order replacement;
- exchange/order mutation;
- automatic credential use;
- market score to order instruction conversion;
- candidate status to order instruction conversion;
- memory policy to recommendation or sizing conversion.

Where flag fields exist, reviewers must confirm the changed reports, rows, and
payloads preserve:

- `paper_only=True`;
- `report_only=True`;
- `readonly=True`.

## Team Memory Review Checklist

Team memory is local research context and must remain policy-gated.

Reviewers must verify:

- memory reads use local Supabase/Postgres durable evidence only;
- `allow`, `throttle`, and `block` policies remain explicit;
- blocked or missing memory cannot be silently treated as passing memory;
- watch-level memory requires reduced reliance and operator review;
- memory does not rank investments, recommend trades, tune strategy weights,
  size positions, approve proposals, or authorize execution;
- cross-team memory use is explicit, lower-weighted, and reviewed when added;
- settled-sample gates and diagnostics remain visible before memory is trusted.

## Redaction And Output Rules

Review packets, documentation examples, CLI examples, logs, and persisted
payloads must not expose:

- raw DSNs;
- passwords;
- API keys;
- auth tokens;
- cookies;
- private keys;
- seed phrases;
- wallet material;
- account identifiers;
- hosted account details;
- live-account order identifiers;
- credential-like headers;
- source payloads containing private account or credential data.

Use placeholders such as `<redacted>` for configuration examples. Prefer
aggregate counts, statuses, reason codes, timestamps, hashes, and local row
identifiers over raw sensitive payloads.

## Review Outcome Meanings

Record the review findings and use the explicit `VERDICT: PASS` or
`VERDICT: FAIL` line required by Project Iron Rule 4. Use these
meanings for the handoff status:

- `approved`: scope is clear, tests and verification are adequate, boundaries
  hold, and no blocking issues remain.
- `approved with changes`: the direction is acceptable, but the listed changes
  must be made before the node is considered complete.
- `blocked`: an eligible reviewer is unavailable, Phase 1 boundaries
  are breached, local Supabase/Postgres persistence rules are breached, tests
  or verification are insufficient for the changed surface, or unresolved
  findings remain.

No review outcome authorizes live trading, credential use, account access, or
order mutation in Phase 1.
