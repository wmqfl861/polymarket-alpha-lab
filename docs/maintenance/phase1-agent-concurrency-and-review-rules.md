# Phase 1 Agent Concurrency and Review Rules

Date: 2026-07-12
Status: active Phase 1 maintenance rule
Scope: multi-agent development coordination, review, local persistence, and
commit readiness

This document records the Phase 1 rules for parallel agent development and
review. It is documentation only. It does not add or authorize source code,
tests, CLI behavior, Supabase migrations, authentication, execution, wallet
handling, account reads, live trading, order signing, order submission, order
cancellation, order replacement, or exchange/order mutation.

Phase 1 remains paper-only, report-only, and readonly. Agents may develop and
review research, diagnostics, paper evidence, operator-review surfaces, local
report persistence, and readback/reporting artifacts only inside the explicitly
assigned file scope.

## Operating Boundary

Every Phase 1 development node must start with an explicit write boundary. The
boundary must name the exact files, directories, or module families that the
node may change. Anything outside that boundary is read-only unless the user
explicitly expands the scope.

For this rule set, allowed maintenance edits are limited to
`docs/maintenance/` unless a later user instruction names another path. Agents
must not use a documentation-only maintenance node to change `src/`, `tests/`,
configuration, CLI behavior, Supabase migrations, execution/auth surfaces, or
live-trading surfaces.

Before dispatching parallel agents, the coordinator must define:

- the node goal;
- the allowed write paths;
- the forbidden paths;
- the Phase 1 paper-only/report-only/readonly assertions;
- the local Supabase/Postgres implications;
- the required review packet and verification commands;
- the no-commit and no-push status when those restrictions apply.

## Multi-Agent Parallel Development Rules

This section implements Project Iron Rule 6 from `AGENTS.md`. Follow
[Native Codex Subagent Routing](../../AGENTS.md#native-codex-subagent-routing)
when dispatching work. While useful, independent work exists, the coordinator
must keep useful collaboration capacity occupied. The project defines no fixed subagent-thread count; the
coordinator must discover usable capacity dynamically from the current runtime.
Nested depth remains capped at **3**. Available capacity may run implementation,
focused tests, documentation, audit, review, or preparation for a subsequent
node. The coordinator may run fewer active threads when independent work is
limited or when conflicts, rate limits, resource contention, or coordination
overhead would reduce quality. Imposing a remembered thread-count ceiling or
leaving useful capacity idle merely to preserve a sequential workflow is not
compliant with this rule.

Parallel work may span multiple modules and multiple development nodes when
their write scopes are disjoint. Separate nodes should use isolated worktrees
when practical, and each node must independently pass its focused tests,
full-suite verification, CodeGraph sync, independent read-only review,
commit, and push gates.

Parallel work is allowed only when the tasks are independent and the write
surfaces do not overlap. Parallelism is a capacity tool, not a reason to blur
ownership or skip review.

Active concurrency rules:

- Assign one primary owner for each file or tightly coupled file family.
- Do not assign two agents to edit the same file at the same time.
- Do not assign overlapping behavior when the implementation must be reviewed
  as a single atomic change.
- Split work by stable responsibility boundaries such as docs section, report
  family, module family, test family, or migration family.
- Keep each agent's write boundary narrow enough that a reviewer can inspect
  the full change without reconstructing another agent's work.
- Require every agent to restate its allowed and forbidden paths before it
  begins edits.
- Require every agent to report changed paths, verification commands, and open
  risks before being considered complete.
- Stop or merge workstreams that begin to require the same file or the same
  unresolved design decision.

The coordinator owns final conflict prevention. If two agents need the same
file, the coordinator must serialize the work, choose one owner, or split the
requirement into non-overlapping files before edits continue.

## Non-Overlapping File Boundaries

File ownership must be explicit and auditable. A valid parallel assignment uses
one of these patterns:

| Boundary type | Valid example | Invalid example |
| --- | --- | --- |
| Single file | Agent A owns `docs/review/phase1-claude-review-handoff.md` | Agent A and Agent B both edit the same handoff file |
| Directory slice | Agent A owns `docs/maintenance/`, Agent B owns `docs/quality/` | Both agents edit mixed files across `docs/` without a path list |
| Module family | Agent A owns source coverage reports, Agent B owns operator packets | Both agents change shared reducers, shared validators, and shared tests independently |
| Test family | Agent A owns focused tests for one report family | Multiple agents rewrite broad test fixtures or shared helpers at once |
| Migration family | One owner writes and verifies one local Supabase migration | Multiple agents alter migrations and DSN handling concurrently |

When a change crosses boundaries, the coordinator must pause parallel edits and
turn the affected files into a single reviewed unit. Shared helpers, shared
fixtures, public payload contracts, DSN validation, persistence adapters,
manual review packets, and boundary scans are high-conflict surfaces and should
not be split casually.

## Completed-Agent Reclamation

Completed agents must be reclaimed quickly. An agent is complete only after it
has reported:

- exact changed paths;
- verification commands run and their results;
- files intentionally left untouched;
- known risks or follow-up items;
- confirmation that no commit or push was created when prohibited.

After completion, the coordinator should close the agent thread or mark the
workstream inactive before assigning new work. Do not leave finished agents
open as ambient reviewers, background editors, or speculative implementers.
Failed or blocked agents must also be reclaimed promptly. Before redeploying
their capacity, inspect the shared worktree for partial edits and preserve any
valid work rather than assuming an interrupted turn left no changes.

Capacity may be redeployed only to a new independent scope. If the next task
touches a file or behavior already edited by a completed agent, run a focused
review first and then assign one owner for the follow-up.

## Independent Review Rules

Dispatch a fresh, independent, read-only reviewer for each plan, code,
stage, post-node, and handoff review under Project Iron Rule 4 and
[Native Codex Subagent Routing](../../AGENTS.md#native-codex-subagent-routing).
Require the explicit verdict for the reviewed revision.

Review prompts must be read-only. Reviewers may inspect plans, diffs, files,
reports, test output, documentation, and redacted review scratch artifacts. Reviewers must not modify files, create files, delete files, run
migrations, backfill data, mutate databases, touch credentials, call live
services, mutate exchange/account/order state, submit orders, cancel orders,
replace orders, sign orders, create commits, or push branches.

The gate is blocked when no eligible independent reviewer can be dispatched.
There is no fallback reviewer under the current project rules.

### Long-Running Review Monitoring

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

Every review packet should include:

- goal and stage boundary;
- changed paths and intentionally untouched paths;
- disjoint file/module ownership used for parallel work;
- Phase 1 paper-only/report-only/readonly assertions;
- local Supabase/Postgres durable-only implications;
- DSN validation implications where relevant;
- focused tests and verification commands;
- `git diff --check` status;
- secret/redaction check status for tracked content;
- open risks and operator follow-ups;
- commit and push status.

## Local Supabase Iron Rules

Durable project persistence means stored project state that survives process
restart, supports audit/readback, feeds diagnostics, or acts as research/team
memory. All durable project persistence must target local Supabase/Postgres
only.

Approved durable targets:

- local Docker Supabase/Postgres;
- localhost or loopback Postgres;
- explicit local Unix-socket Postgres.

Forbidden durable substitutes:

- hosted Supabase or hosted Postgres;
- SQLite or SQLite fallback files;
- DuckDB;
- Redis;
- MongoDB;
- SQLAlchemy-managed durable engines;
- generic durable-store abstractions;
- JSONL durable journals;
- CSV ledgers;
- filesystem-backed durable report history, strategy state, team memory,
  research memory, or audit history.

Every raw DSN crossing environment variables, configuration, CLI plumbing,
tests, fixtures, helper construction, adapter construction, scripts, or smoke
checks must be validated with `validate_local_postgres_dsn` before any
connection or psycopg wrapper is opened.

Invalid, missing, hosted, non-Postgres, or non-local DSNs must fail closed. A
node must not compensate by switching to JSONL, SQLite, CSV, filesystem caches,
hosted databases, or unvalidated local services.

Documentation examples must redact raw DSNs, passwords, tokens, API keys,
cookies, private keys, seed phrases, wallet material, account identifiers,
hosted account details, credential-like headers, and live-account order
identifiers.

## Phase 1 Safety Gates

Agents and reviewers must block any node that introduces or implies:

- live trading;
- automated investing;
- investment advice;
- position advice;
- live order placement;
- order signing;
- order submission;
- order cancellation;
- order replacement;
- order routing;
- exchange mutation;
- account mutation;
- account authentication;
- hosted account reads;
- wallet handling;
- private-key handling;
- automatic credential use;
- conversion of market scores, candidate status, memory policy, readiness
  status, go/no-go status, Kelly output, recommendation rank, or operator
  review status into order instructions or live execution approvals.

Where flag fields exist, changed reports, rows, payloads, and docs must
preserve:

- `paper_only=True`;
- `report_only=True`;
- `readonly=True`.

Manual review language can support human triage, paper diagnostics, and
operator follow-up only. It must not become approval to trade, capital
allocation, position sizing, account access, or order mutation.

## Commit And Push Gates

Subagents must not create commits or push branches unless the primary
coordinator explicitly assigns that responsibility. The primary Codex
coordinator follows the `AGENTS.md` Codex Node Push Policy: after every required
gate passes, it creates focused commits and pushes the completed node unless the
user explicitly pauses, redirects, or forbids that action.

Before any future commit is allowed, the coordinator must verify:

- changed paths are inside the approved scope;
- no unrelated user changes are reverted or swept into the node;
- no forbidden files were edited;
- `git diff --check` passes;
- focused tests or documentation checks for the changed surface pass;
- the review packet includes changed paths, test evidence, open risks, and
  Phase 1 boundary assertions;
- independent read-only review has passed for the current revision;
- tracked content contains no unredacted secrets, raw DSNs, credentials, wallet
  material, hosted account details, live-account order identifiers, or unsafe
  execution payloads.

Before any future push is allowed, the coordinator must additionally verify:

- the branch and remote are correct;
- the pushed commit set contains only approved files and reviewed changes;
- no `.review` scratch artifacts are pushed unless explicitly requested;
- no live trading, execution/auth, Supabase-hosted, wallet, or account access
  surface is introduced.

This maintenance document does not override the active Codex Node Push Policy.

## Definition Of Done

This document is satisfied when the maintenance rules explicitly cover:

- multi-agent parallel development rules;
- non-overlapping file boundaries;
- prompt reclamation of completed agents;
- independent read-only review under Project Iron Rule 4 and the root
  subagent routing rule;
- local Supabase/Postgres durable-only persistence;
- DSN validation before connection;
- Phase 1 paper-only/report-only/readonly safety gates;
- commit and push thresholds;
- documentation-only scope with no `src/`, `tests/`, configuration, CLI,
  Supabase, execution/auth, or live-trading edits.
