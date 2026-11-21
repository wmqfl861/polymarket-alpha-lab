# Phase 1 Claude Review Handoff

Date: 2026-07-12
Status: historical handoff for the July 2026 Phase 1 strategy-stack batch
Scope: documentation-only handoff node

## Review Target

This packet records the July 2026 review target and retains its evidence
checklist for reference. Follow
[Project Agent Routing](../../AGENTS.md#project-agent-routing)
for a current independent read-only review.

The reviewer may inspect diffs, source files, tests, docs, and local scratch
review artifacts supplied outside the repository commit scope. The reviewer must
not edit files, run migrations, mutate any database, touch credentials, call live
services, mutate exchange/account/order state, submit/cancel/replace orders, or
create commits.

## Node Goal

The July batch was scoped to paper-only, report-only, readonly screening
and operator review.

The batch appears to add or modify many report modules, tests, and supporting
documentation for probability-event screening, specialist research routing,
source/freshness quality, cost/liquidity/risk diagnostics, manual go/no-go
packets, post-settlement calibration, report discovery, and local
Supabase/Postgres readiness.

This document records the handoff. The review outcome belongs to the reviewed revision.

## Review Inputs For The July Batch

Primary review inputs:

- `docs/review/2026-07-12-operating-review-rules.md`
- `docs/review/phase1-claude-review-handoff.md`
- `docs/index/phase1-module-index.md`
- `docs/phases/2026-07-12-phase-1-capability-baseline.md`
- `docs/quality/phase-1-development-node-quality-gates.md`
- `docs/acceptance/phase-1-development-node-acceptance-checklist.md`
- `docs/phase1/probability-event-readonly-supabase-principles.md`
- `docs/supabase/local-supabase-operations.md`
- `docs/config/phase-1-strategy-screening-schema.md`
- `docs/contracts/phase1-data-field-contracts.md`
Treat local scratch review files as reviewer context only. They are not source,
tests, config, documentation policy, migrations, or durable project artifacts.
They should not be committed unless a later explicit instruction changes that.

## Changed Surface Summary

At handoff, an existing batch outside this documentation node included
modified source and tests, untracked report modules and tests, and
documentation directories. The handoff node was limited to `docs/review`.

The handoff listed these review surfaces:

- the implementation batch under `src/`, `tests/`, docs, and
  `strategy.example.phase1-screening.json`;
- scratch review artifacts, which should remain excluded from commit scope;
- this handoff document under `docs/review/`, which is a documentation-only
  review aid.

Do not treat this handoff as evidence that the implementation batch is ready to
commit. The reviewer must inspect the actual diff and test output.

## Phase 1 Hard Boundary

All reviewed behavior must stay inside Phase 1:

- `paper_only=True` where hard flags exist;
- `report_only=True` where hard flags exist;
- `readonly=True` where hard flags exist;
- research, diagnostics, paper evidence, operator review, local report
  persistence, local readback, and post-resolution learning only.

The batch must not introduce:

- live trading;
- account authentication;
- wallet handling;
- private-key handling;
- hosted account reads;
- automatic credential use;
- order signing;
- order submission;
- order cancellation;
- order replacement;
- order routing;
- exchange mutation;
- account mutation;
- conversion of market scores, candidate status, memory policy, readiness
  status, go/no-go status, Kelly output, or recommendation rank into order
  instructions or live execution approvals.

Any text or payload that sounds like `go`, `approved`, `ready`,
`recommendation`, `Kelly`, `position sizing`, `allocation`, or `execution`
needs extra scrutiny. In Phase 1 these words can describe paper/report
diagnostics or manual review readiness only.

## Strategy-Stack Module Families

Review modules by family rather than as isolated templates. The main question
is whether each module materially supports Polymarket probability-event
screening and manual go/no-go decisions, or whether it is generic report bloat.

| Family | Examples / responsibilities | Review focus |
| --- | --- | --- |
| Market discovery and candidate pool | `market_discovery_candidate_pool`, market event/time/context intake, taxonomy health | Discovery output must remain research triage, not recommendations or order intent. |
| Forecast context and forecast quality | forecast context readiness, confidence drift, ensemble quality, revision quality, source-recency consensus | Forecast values must be source-backed diagnostics with UTC timestamps and Decimal-safe payloads. |
| Source coverage and evidence quality | source discovery coverage, traceability audits, information gaps, source-family divergence, source capture completeness | Missing or conflicting sources should produce research/watch/block status, not silent pass. |
| Freshness SLA and refresh pressure | information freshness, market data freshness guard, refresh failure fallback, event update freshness, context source ladder | Stale data must be visible and fail closed into review states. |
| Cost, EV, Kelly, liquidity, and slippage | cost-aware edge gates, cost-adjusted sizing diagnostics, liquidity/cost curves, break-even and margin-of-safety reports | Sizing and Kelly language must stay paper-only diagnostics and never become live capital allocation. |
| Portfolio, dependency, and collision risk | duplicate exposure, correlation clusters, concentration/exposure conflict checks, settlement cash drag, tail-risk stress | Risk outputs can throttle or block paper/report promotion only. |
| Specialist team routing and memory | team routing taxonomy, category playbooks, cross-team disagreement, domain memory retrieval, memory scorecards | Teams own research context, not execution, allocation, account state, or order authority. |
| Operator and manual go/no-go | manual operator packet, final boundary reports, manual blocker checklists, review queue heatmaps, decision tickets | Operator packets support human review only; no order/request/signing fields. |
| Recommendation/explainability rollups | expected utility, recommendation rank explainability, ready queue rollups, exclusion audits, strategy audit trails | Recommendation language must not imply investment advice or execution approval. |
| Post-settlement calibration and feedback | calibration backlog, specialist calibration evidence, settlement feedback memory queue, forecast residual reports | Settled outcomes feed learning diagnostics only; no live strategy-weight tuning. |
| Report discovery and readiness aggregation | `report_discovery`, strategy readiness aggregators, local Supabase evidence readiness, schema migration safety | Discovery/readiness helpers must not execute reports, migrate databases, or repair evidence. |
| Public payload execution-text safety | public payload safety rollups and manual boundary checks | Payloads must reject or flag unsafe live execution/account/order language. |

## Review Constraints

Reviewer should block the batch for any Critical or Important issue in these
areas:

- a report, row, config, candidate, payload, or DB row lacks hard Phase 1 flags
  where the surrounding family uses them;
- a payload accepts floats for Decimal financial/probability/cost fields;
- a timestamp is naive or can be after its owning report generation time where
  the reducer enforces ordering;
- a report accepts subclassed public dataclasses where nearby modules enforce
  final/frozen dataclass patterns;
- a report silently treats missing, stale, blocked, or throttled evidence as
  passing evidence;
- a reducer collapses unavailable optional evidence to zero instead of `None`;
- a reason-code summary, status count, or row count can disagree with rows;
- a module persists unsafe raw payloads, credentials, account identifiers,
  wallet material, order identifiers, raw DSNs, or private source data;
- a module or doc expands JSONL/filesystem/CSV/SQLite/DuckDB/Redis/MongoDB as
  durable project storage;
- a manual/operator report uses language or fields that can be read as a live
  order instruction;
- a CLI/config/docs change adds execution/auth/live-trading affordances;
- tests are template-only and do not prove the module-specific boundary,
  validation, status, or payload contract.

## Local Supabase/Postgres Iron Rules

Durable project persistence means stored project state that survives process
restart, supports audit/readback, feeds diagnostics, or acts as research/team
memory. It must target local Supabase/Postgres only.

Approved durable target:

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
  research memory, audit history, or project state;
- silent fallback from local Supabase/Postgres to files or hosted services.

Any raw DSN crossing environment, config, CLI plumbing, tests, fixtures, helper
construction, adapter construction, scripts, or smoke checks must pass
`validate_local_postgres_dsn` before a connection or psycopg wrapper opens.
Missing, malformed, hosted, non-Postgres, or non-local DSNs must fail closed.
Raw DSNs must not be printed, logged, persisted, or copied into examples.

Local migration review, if any migration surface is touched, must remain local,
explicit, catalog-verified, and readonly after application. This handoff node
does not authorize any migration.

## Scratch Review Exclusion Rule

Use local scratch review artifacts as reviewer context only. Do not include
scratch review artifacts in the node commit unless the user explicitly requests
committing them. Do not treat their contents as authoritative over source,
tests, docs, or actual `git diff` output. If a scratch diff disagrees with the
worktree, inspect the current worktree directly and report the mismatch.

## Test Matrix For Review

The Phase 1 module index maps modules to focused tests. The reviewer should
verify that the test matrix covers module-specific behavior rather than only
checking template construction.

| Area | Representative tests / checks | Required evidence |
| --- | --- | --- |
| Candidate discovery and screening | `tests/test_market_discovery_candidate_pool.py`, `tests/test_phase1_report_discovery_smoke.py` | deterministic candidate ordering, no execution fields, report discovery does not execute reports |
| Forecast readiness | `tests/test_forecast_context_readiness_report.py` and forecast quality tests listed in the module index | Decimal probability validation, UTC timestamps, stale/missing evidence handling |
| Source coverage | source coverage, source contradiction, source quorum, source traceability tests | missing/conflicting source reason codes, no raw/private source payload leakage |
| Freshness and degradation | `tests/test_information_freshness_refresh_sla_readiness_report.py`, `tests/test_input_failure_degradation_readiness_report.py`, market freshness tests | stale inputs produce watch/block/degraded states, refresh failure does not use unsafe fallback |
| Cost/liquidity/Kelly diagnostics | cost-aware probability, liquidity, slippage, Kelly-bound, break-even, margin-of-safety tests | paper-only sizing diagnostics, no order sizing or live allocation semantics |
| Portfolio and dependency risk | exposure, duplicate, correlation, dependency, settlement-delay tests | risk blocks/throttles paper/report promotion only |
| Specialist routing and memory | specialist routing, playbook, memory retrieval, memory policy, cross-team disagreement tests | team memory policy is explicit; block/throttle cannot silently pass |
| Manual operator packets | manual packet, final boundary, safety interlock, blocker checklist, attestation tests | no signing/submission/cancel/replace/order fields; manual review status only |
| Post-settlement calibration | calibration backlog, feedback memory queue, forecast residual tests | learning diagnostics only; no live strategy weight tuning |
| Local Supabase/Postgres | `tests/test_supabase_local_dsn.py`, `tests/test_phase1_local_supabase_config_contract.py`, local Supabase readiness tests | local DSN validation before connect; no hosted or file-backed durable fallback |
| Boundary and public payload safety | `tests/test_phase1_readonly_boundary_scan.py`, `tests/test_phase1_manual_packet_no_live_execution_terms.py`, `tests/test_phase1_config_no_execution_fields.py`, public payload safety tests | unsafe execution/auth/wallet/order language rejected or documented as forbidden-field checks |
| Documentation contract | `tests/test_phase1_docs_links_and_terms.py` | docs link correctly and do not authorize live execution or alternate durable storage |

Minimum command evidence expected in the final implementation review packet:

```bash
python -m compileall src tests
pytest
pytest tests/test_phase1_readonly_boundary_scan.py tests/test_phase1_config_no_execution_fields.py tests/test_phase1_local_supabase_config_contract.py tests/test_phase1_manual_packet_no_live_execution_terms.py tests/test_phase1_docs_links_and_terms.py
git diff --check
rg -n "live trading|order submission|submit order|cancel order|replace order|sign order|wallet|private key|account auth|account authentication|hosted account|exchange mutation|order mutation" src tests docs strategy.example.phase1-screening.json
rg -n "sqlite|duckdb|redis|mongodb|sqlalchemy|jsonl|csv|filesystem|file-backed|durable store|database_url|postgres|supabase|dsn" src tests docs supabase strategy.example.phase1-screening.json
rg -n "api[_-]?key|secret|token|password|passwd|private[_-]?key|seed phrase|mnemonic|cookie|authorization|bearer|wallet|clob|signature|signed|order_id|live_order|account_id|account_address" src tests docs supabase strategy.example.phase1-screening.json
```

The July handoff allowed compile and pytest to be skipped for that
documentation-only node if its packet recorded the absence of source, tests,
configuration, CLI, Supabase, execution/auth, or live-trading changes.
For a new node, apply the current [quality gates](../quality/phase-1-development-node-quality-gates.md).

## Reviewer Questions

The handoff asked the reviewer these questions:

- Which modules are Critical/Important blockers, and why?
- Which modules are too generic, duplicative, or weakly tied to Polymarket
  probability-event screening and manual go/no-go decisions?
- Do any modules imply live execution, investment advice, capital allocation,
  order sizing, account access, wallet use, credential handling, or exchange
  mutation?
- Do local Supabase/Postgres rules hold across config, adapters, fixtures,
  tests, docs, and any DB-facing helpers?
- Are Decimal, timestamp, digest, hard-flag, reason-code, and payload contracts
  actually tested for the new report families?
- Are local scratch review artifacts excluded from commit scope?

## Expected Review Outcome Format

The July packet requested findings ordered by severity:

```text
Critical
- path:line - issue, impact, required fix

Important
- path:line - issue, impact, required fix

Maintainability
- path:line - issue, impact, suggested fix

Checked
- command or file set reviewed
- residual risks
```

A review of a new batch must identify the exact files, diffs, tests,
boundary checks, local Supabase/Postgres checks, and secret/live-field
scans reviewed, then give the explicit verdict required by Project Iron Rule 4.

## Documentation-Only Node Statement

This handoff document changes only review documentation. It does not modify
source code, tests, configuration, CLI behavior, Supabase migrations,
execution/auth surfaces, or live-trading behavior.
