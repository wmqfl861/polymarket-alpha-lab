# Project Progress Roadmap Node

Date: 2026-07-12
Status: current project progress and next-phase roadmap checkpoint
Scope: documentation-only roadmap node

## Purpose

This node records the current project phase, the Phase 1 capability baseline,
the next priorities, and the operating rules that future implementation nodes
must preserve. It is intended to be auditable by reviewers without reading the
full project history.

This document does not authorize implementation outside the current boundary.
It is not a live-trading design, not a credential-handling design, not an order
placement plan, and not a request to modify source, tests, Supabase migrations,
CLI behavior, execution/auth surfaces, or live trading infrastructure.

## Current Phase

The project is in a Phase 1 research and paper-evidence stage.

Phase 1 supports:

- read-only Polymarket market research;
- strategy validation and candidate decision support;
- paper-only execution simulation and paper journals;
- paper-only NAV, outcome, cost, risk, and readiness reports;
- specialist team research architecture;
- local Supabase/Postgres durable evidence and readback;
- operator-facing review packets and diagnostics.

Phase 1 remains paper-only, report-only, and readonly. The active boundary is
not a permanent project non-goal; it is the present safety and evidence gate
before any later execution-oriented phase can be designed.

The current runnable team-agent slice is `crypto_btc`. Other teams may appear
in taxonomy, routing, diagnostics, persistence-ready rows, and future-facing
reports, but they are not runnable domain forecast workflows yet.

## Completed Phase 1 Capability Baseline

The project already has a broad paper-research and report foundation. The
completed baseline includes the following capability groups:

| Capability Group | Completed Baseline | Audit Boundary |
| --- | --- | --- |
| Market research and screening | Read-only market scanner, normalized market snapshots, project screening, strategy candidate queues, research packets, and candidate decision support. | Research and report surfaces only; no live execution authorization. |
| Paper execution evidence | Bid/ask paper-fill simulation, paper trade records, paper position ledgers, executable NAV marks, paper NAV reports, paper analytics, and paper outcome tracking. | Paper-only evidence; not account state and not live order history. |
| Cost and risk audit | Cost-aware event strategy reports, strategy risk audits, paper trade cost audits, NAV drawdown reports, exposure reports, readiness gates, and local observability trend reports. | Risk evidence for paper operations; not approval for live orders. |
| Proposal and recommendation artifacts | Human-review proposal packets, review records, summaries, quality gates, diagnostics, coverage, dossiers, evidence comparisons, and history/batch-health trends. | Operator review and audit packaging; not automatic trade instruction. |
| Autonomous paper reports | Paper autonomous allocation proposal artifacts, DB-history/readiness reports, health gates, investment-ledger style paper evidence, and agreement trend-gate evidence. | Paper/report evidence only; no live capital. |
| Team-agent framework | Medium-granularity 10-team taxonomy, primary/secondary routing, generic team forecast/evidence packet contracts, `crypto_btc` supplied-input workflow, and central cost/risk ownership. | Teams produce research and forecasts only; central layer owns cost, risk, recommendation, allocation, and outcomes. |
| Team memory and diagnostics | Team diagnostics snapshots, memory readiness digests, assignment reports, assignment history, and local team-memory policy gates. | Memory is research context only; no recommendation store, account memory, or sizing engine. |
| Persistence hardening | Local Supabase/Postgres-only durable persistence rule, shared local DSN validation expectation, and explicit legacy-file compatibility boundaries. | No alternate durable database backend or file-backed durable substitute. |

## Next-Phase Priorities

The next phase should remain inside the current Phase 1 boundary until the
evidence gates justify a separate later-phase design. Priorities are:

1. Complete local Supabase/Postgres hardening for all DSN-bearing persistence
   surfaces so every raw DSN crosses `validate_local_postgres_dsn` before any
   connection or adapter construction.
2. Continue migrating new durable report/history write paths to local
   Supabase/Postgres while freezing legacy file-backed persistence as explicit
   compatibility, export, replay, or read-only input surfaces.
3. Strengthen the candidate decision engine around probability-event quality:
   event criteria, resolution source, ambiguity risk, executable bid/ask,
   spread/liquidity, fee/cost drag, cash lockup, settlement lag, and team
   memory policy.
4. Expand team-agent capability without fragmenting ownership: keep the 10-team
   taxonomy, keep `crypto_btc` as the only current runnable slice, and add
   other runnable workflows only through reviewed, non-overlapping nodes.
5. Improve long-term team memory quality before broad use: require diagnostics
   history, memory readiness gates, source freshness, settled calibration
   deltas, and assignment-history readback before memory can affect research
   handoffs.
6. Add or harden report-only review gates for parallel team operations:
   capacity pressure, unresolved escalations, SLA age, memory writeback delay,
   and manual escalation urgency.
7. Preserve the execution boundary until a later roadmap explicitly introduces
   credential handling, broker interfaces, audit logging, kill switches,
   reconciliation, rollback, and user authorization.

## Long-Term Team Memory Plan

Long-term team memory is local, durable research evidence from previously
persisted Phase 1 rows. It may include:

- team profiles and team market routes;
- team forecasts and team forecast evidence;
- team forecast outcomes;
- team diagnostics snapshots;
- memory readiness digest reports;
- team research assignment reports;
- assignment history and readback summaries;
- source freshness, calibration, and settled-outcome diagnostics.

Long-term memory is not a model cache, trading memory, wallet memory, account
memory, recommendation store, strategy-weight tuner, position-sizing engine, or
execution authorization surface.

Memory usage must be policy-gated:

| Policy | Meaning | Required Handling |
| --- | --- | --- |
| `allow` | The selected team's local memory source is passing. | Memory may be used as research context in paper-only handoffs. |
| `throttle` | The selected team's local memory source is watch-level. | Memory may be used only with reduced reliance and operator review. |
| `block` | The selected team's memory source is missing, stale, invalid, duplicated, or blocked. | Assignment must not rely on long-term memory. |

Memory can inform known failure modes, source reliability, calibration context,
and operator-facing research notes. It must not rank investments, approve
trades, size positions, tune strategy weights, generate live recommendations,
or authorize execution.

## Local Supabase/Postgres Iron Rule

All durable project data must use the local Supabase/Postgres instance on this
host. Treat local Supabase/Postgres as the only approved durable persistence
target for project data.

Forbidden durable substitutes include:

- SQLite;
- DuckDB;
- Redis;
- MongoDB;
- SQLAlchemy-managed durable engines;
- hosted or remote database assumptions;
- generic database abstraction layers;
- JSONL/file journals as new durable stores;
- CSV or filesystem-backed durable stores.

Legacy JSONL/file-backed surfaces are frozen compatibility surfaces. New work
must not expand them as durable persistence. When touching legacy persistence,
prefer migrating the write path to local Supabase/Postgres or documenting a
read-only compatibility boundary if migration is out of scope.

Every raw DSN from environment, config, CLI plumbing, fixtures, tests, helper
construction, or adapter creation must be validated through
`validate_local_postgres_dsn` before it can open a connection, construct a
psycopg wrapper, or reach a persistence adapter.

## Phase Boundary Checklist

Phase 1 product constraints:

- `paper_only=True`, `report_only=True`, and `readonly=True` are preserved
  wherever those flags exist;
- no live trading path is introduced;
- no wallet, private-key, account, auth, credential, or order-mutation surface
  is introduced;
- no DSN/table CLI flags are added to report-scoped team-memory commands;
- no alternate durable persistence backend is introduced;
- no market score, candidate score, assignment, digest, gate, or memory policy
  is described as a trade instruction.

## Audit Source Map

This node summarizes and cross-references the current project documentation:

- `AGENTS.md` for the product scope and storage constraints;
- `README.md` for Phase 1 scope and current report/CLI surfaces;
- `docs/team-agent-framework.md` for team-agent architecture and `crypto_btc`
  runnable slice;
- `docs/phase-1-team-memory.md` for team-memory docs-to-code contract;
- `docs/phase-1-multi-team-operating-model.md` for multi-team operating,
  redaction, review, and persistence boundaries;
- `docs/strategy-first-roadmap.md` for probability-event decision priorities;
- `docs/superpowers/specs/2026-06-13-automated-investment-roadmap.md` for the
  staged path from research to later controlled execution design;
- `docs/superpowers/specs/2026-07-01-team-agent-architecture-claude-review.md`
  for accepted Claude Code review hardening changes;
- `docs/superpowers/plans/2026-06-29-local-supabase-only-persistence-hardening.md`
  for local Supabase/Postgres hardening batches.
