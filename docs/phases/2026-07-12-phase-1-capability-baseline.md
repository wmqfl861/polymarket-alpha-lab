# Phase 1 Capability Baseline

Date: 2026-07-12
Status: current Phase 1 baseline checkpoint
Scope: documentation-only phase node

## Current Boundary

Phase 1 is the current project phase. It is paper-only, report-only, and
readonly.

The current boundary permits research, diagnostics, paper evidence, operator
review, local report persistence, and readback. It forbids live trading,
account authentication, wallet handling, private-key handling, hosted account
reads, order signing, order submission, order cancellation, order replacement,
and exchange/order mutation.

This boundary is a staged delivery boundary. It does not permanently forbid
future execution work, but any future execution work must be introduced through
separate roadmap documentation, validation gates, risk controls, audit logging,
credential-handling design, and explicit user authorization.

## Completed Capability Groups

### Research Foundation

Phase 1 includes project design, research notes, implementation plans, and
read-only Polymarket market research surfaces. The system can screen markets,
assemble research packets, capture rule/source context, and produce
paper-facing candidate evidence.

Audit stance:

- market scores and candidate status are research priority signals;
- they are not trade instructions;
- they do not authorize execution;
- they must stay source-backed and reproducible.

### Paper Evidence And Paper Execution Simulation

Phase 1 includes bid/ask paper-fill simulation, paper trade records,
paper-only ledgers, NAV marks, executable NAV summaries, and outcome-tracking
report logs.

Audit stance:

- paper records are local evidence;
- paper records are not account state;
- paper fills must use executable bid/ask and cost assumptions rather than
  midpoint-only assumptions;
- unresolved or pending settlement states must remain visible in NAV and
  outcome reports.

### Risk, Cost, Readiness, And Observability

Phase 1 includes cost-aware event strategy reports, strategy risk audits,
paper trade cost audits, NAV drawdown reports, exposure summaries, local
observability trends, readiness gates, and source/history readbacks.

Audit stance:

- risk gates can pause or block paper/report use;
- `audit_ready` is not live trading approval;
- readiness reports are evidence quality surfaces;
- trend and health reports must remain report-only and readonly.

### Proposal Review And Decision Support

Phase 1 includes human-review proposal packet artifacts, proposal-review
records, summary reports, quality gates, diagnostics, coverage, dossiers,
evidence comparisons, and batch-health/trend artifacts.

Audit stance:

- proposal artifacts support operator review;
- proposal artifacts are not automatic approval workflows;
- proposal artifacts do not submit, sign, cancel, replace, or route orders;
- proposal artifacts do not select latest approved live decisions.

### Autonomous Paper Report Surfaces

Phase 1 includes paper autonomous allocation proposal artifacts, DB-history
readback, DB-history gates, metrics, health, readiness digests, and investment
ledger-style paper evidence.

Audit stance:

- autonomous means automated paper/report analysis only;
- paper autonomous reports do not handle credentials or live capital;
- reports must preserve hard Phase 1 flags wherever those flags exist.

### Team-Agent Framework

Phase 1 includes a medium-granularity team architecture:

| Team ID | Category | Runnable In Current Slice |
| --- | --- | --- |
| `politics` | politics | No |
| `crypto_btc` | finance.crypto.btc | Yes |
| `crypto_eth` | finance.crypto.eth | No |
| `macro_rates` | finance.macro.rates | No |
| `equity_indices` | finance.equity.indices | No |
| `commodities_gold` | finance.commodities.gold | No |
| `commodities_oil` | finance.commodities.oil | No |
| `sports_soccer` | sports.soccer | No |
| `sports_basketball` | sports.basketball | No |
| `sports_other` | sports.other | No |

The current runnable workflow is `crypto_btc` and is supplied-input only.
Non-runnable teams may appear in taxonomy, route outputs, profiles, persisted
rows, diagnostics, memory readiness, and assignment reports, but they do not
create live team-agent execution.

Audit stance:

- domain teams produce forecasts and evidence only;
- teams do not own execution, allocation, account state, live orders, or
  recommendation authorization;
- the central layer owns microstructure, costs, recommendation reducers, risk,
  paper allocation, outcomes, and comparable reporting.

### Team Memory And Assignment

Phase 1 includes local long-term team-memory surfaces:

- team diagnostics snapshots;
- diagnostics snapshot history readback and gates;
- memory readiness digest reports;
- memory readiness digest history;
- team research assignment reports;
- team research assignment history.

Memory policy is:

- `allow`: passing local memory source can be used as research context;
- `throttle`: watch-level local memory source requires reduced reliance and
  operator review;
- `block`: blocked or missing local memory source must not be used.

Audit stance:

- memory is local research context;
- memory is not a recommendation store;
- memory is not trading/account/wallet/order memory;
- memory does not tune strategy weights, size positions, rank investments, or
  authorize execution.

### Local Supabase/Postgres Persistence

Phase 1 durable project data uses local Supabase/Postgres only. Covered
surfaces include team profiles, routes, forecasts, forecast evidence, outcomes,
diagnostics snapshots, memory readiness digest reports, team assignment
reports, paper evidence, and DB-backed report histories where documented.

Audit stance:

- raw DSNs must be validated through `validate_local_postgres_dsn`;
- no alternate durable database or file-backed substitute may be introduced;
- JSONL/file-backed surfaces are legacy compatibility, export, replay, or
  read-only input boundaries unless specifically migrated.

## Known Incomplete Or Guarded Areas

The following areas are intentionally not complete Phase 1 live capabilities:

- no live trading;
- no authenticated broker gateway;
- no wallet/private-key handling;
- no account reads;
- no live order lifecycle;
- no kill-switch procedure for live orders;
- no exchange reconciliation;
- no automatic conversion of score thresholds into orders;
- no broad runnable domain team set beyond `crypto_btc`;
- no generic long-term memory use without readiness gating.

## Exit Criteria Before Later Execution Design

A later execution-oriented phase should not be designed until the project has
auditable evidence that:

- candidate decision reports remain stable and explainable;
- cost, liquidity, spread, and settlement timing are measured separately from
  probability quality;
- paper fills match executable bid/ask assumptions;
- paper outcomes and false positives are tracked;
- memory readiness gates are reliable across teams;
- local Supabase/Postgres persistence is consistently local-only and DSN
  validated;
- credential handling, broker boundaries, risk limits, audit logging, rollback,
  and kill-switch design are documented as separate later-phase artifacts.
