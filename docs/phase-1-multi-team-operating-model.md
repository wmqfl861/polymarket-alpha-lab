# Phase 1 Multi-Team Operating Model

This document is the Phase 1 documentation and test contract for operating Polymarket Alpha Lab with multiple medium-scale specialist research teams. It defines how teams collaborate, how long-term memory is used, where durable evidence may be stored, and which review and redaction rules keep the system inside the Phase 1 boundary.

Polymarket is a probability event market. Phase 1 supports automatic screening, research, and paper execution only; no live trading, no investment advice, no trade instruction, no position advice, and no position sizing.

Phase 1 is paper-only, report-only, and readonly. It supports research, diagnostics, paper evidence, operator review, and readback reports. It does not authorize live trading, wallet use, account authentication, private-key handling, order signing, order submission, order cancellation, order replacement, or any exchange/order mutation path.

## Team Model

The intended Phase 1 operating model uses medium-scale specialist teams, not one giant generalist worker and not tiny single-market agents. The target taxonomy keeps enough specialization for domain-quality research while preserving enough settled samples per team for useful diagnostics and memory readiness.

| Team | Primary Scope | Phase 1 Responsibility |
| --- | --- | --- |
| `politics` | Politics and election-style markets | Research event timelines, resolution criteria, source quality, polling context, and rule interpretation. |
| `crypto_btc` | Bitcoin and BTC-linked markets | Research supplied BTC evidence, market context, probability deltas, and recurring source quality. |
| `crypto_eth` | Ethereum and ETH-linked markets | Research supplied ETH evidence, protocol/news context, market context, and recurring source quality. |
| `macro_rates` | Rates, central banks, inflation, and macro releases | Research official release calendars, policy expectations, macro indicators, and event-definition risk. |
| `equity_indices` | Index and broad equity-market outcomes | Research index-level evidence, macro/equity context, and event timing. |
| `commodities_gold` | Gold and precious-metals markets | Research commodity drivers, macro/rates links, source freshness, and contract/event definitions. |
| `commodities_oil` | Oil and energy-market outcomes | Research supply/demand events, energy-market evidence, source freshness, and event definitions. |
| `sports_soccer` | Soccer markets | Research fixtures, roster/news context, event calendars, and source freshness. |
| `sports_basketball` | Basketball markets | Research fixtures, injuries/rosters, event calendars, and source freshness. |
| `sports_other` | Other sports markets | Research sport-specific context where a dedicated team does not yet exist. |

Team routing assigns one primary team to each market and may include secondary teams for operator context. Teams own research responsibility only. The central layer continues to own market metadata normalization, cost and fee research factors, paper-only diagnostics, outcome scoring, and reporting.

## Long-Term Memory

Long-term team memory is local, durable research evidence from previously persisted Phase 1 rows. It includes team diagnostics snapshots, team forecast and evidence rows, outcome rows, memory readiness digests, assignment history, source freshness summaries, and other local report evidence.

Long-term team memory is not:

- a model cache;
- a wallet or account memory;
- a live trading memory;
- an order history used for execution;
- a recommendation store;
- a position-sizing engine;
- a strategy-weight tuner.

Memory use is policy-gated before it can affect research handoffs:

- `allow`: local memory source gates pass and the team can use memory as research context.
- `throttle`: local memory source gates are watch-level and the team can use memory only with reduced reliance and operator review.
- `block`: local memory source gates are blocked, missing, stale, duplicated, or invalid; the assignment must not rely on long-term memory.

Memory may inform paper-only research context, source reliability notes, known failure modes, and operator-facing diagnostics. It must not approve trades, rank investments, create live recommendations, size positions, tune allocation, or authorize execution.

## Durable Persistence Rule

All durable project data for this operating model must use local Supabase/Postgres only. Local Supabase/Postgres is the only approved durable persistence target for Phase 1 team evidence, diagnostics, memory, assignments, and paper reports.

Allowed durable surfaces include local Supabase/Postgres tables such as:

- `team_profiles`;
- `team_market_routes`;
- `team_forecasts`;
- `team_forecast_evidence`;
- `team_forecast_outcomes`;
- `team_diagnostics_snapshots`;
- `team_memory_readiness_digest_reports`;
- `team_research_assignment_reports`.

The `team_memory_readiness_digest_reports` table is documented separately in
[team-memory-readiness-digest-db-persistence.md](team-memory-readiness-digest-db-persistence.md)
as a local Supabase/Postgres durable report-history exception only. It is not a
live trading mutation, not a default CLI side effect, and must not be replaced
by an alternate durable backend.

Raw DSNs from environment variables, config, CLI plumbing, fixtures, or helper construction must be validated through `validate_local_postgres_dsn` before any connection, psycopg wrapper, store, loader, or persistence adapter can use them.

The operating model must not introduce alternate durable stores or fallback persistence such as SQLite, DuckDB, Redis, MongoDB, hosted database assumptions, SQLAlchemy-managed durable engines, generic database abstraction layers, JSONL durable journals, file-backed durable stores, CSV durable ledgers, or local filesystem substitutes. Legacy file surfaces remain compatibility-only and must not be expanded as durable Phase 1 memory or report storage.

## Phase 1 Boundary

Phase 1 artifacts, commands, handoffs, persisted rows, reports, and tests must preserve these flags where a flag surface exists:

- `paper_only=True`;
- `report_only=True`;
- `readonly=True`.

The boundary permits:

- read-only market research;
- supplied-input team forecasts;
- paper evidence packets;
- research framework consideration of costs and fees as cost and fee research factors, including spread, slippage, liquidity, settlement timing, and paper cost evidence;
- local Supabase/Postgres persistence of internal report rows;
- diagnostics over already-persisted rows;
- memory readiness reports;
- research assignment reports;
- operator review packets;
- read-only paper performance and outcome summaries.

The boundary forbids:

- live trading;
- automated investing;
- live order placement;
- wallet handling;
- private-key handling;
- account authentication;
- hosted account reads;
- order signing;
- order submission;
- order cancellation;
- order replacement;
- exchange mutation;
- account mutation;
- execution authorization.

Any future expansion beyond this boundary must be documented as a later phase with explicit validation gates, risk controls, audit logging, credential-handling design, and user authorization. This Phase 1 operating model must not imply availability of automated investing or live order placement.

## Operating Flow

The intended Phase 1 flow is:

```text
market metadata and read-only evidence
  -> central route assignment
  -> primary specialist team research
  -> optional secondary team context
  -> team forecast and evidence packet
  -> central cost and fee research-factor checks, paper-only scoring inputs, and report assembly
  -> local Supabase/Postgres paper/report persistence
  -> diagnostics, memory readiness, outcome scoring, and operator reports
  -> read-only review gate and redacted handoff
```

Teams should produce structured research packets with source references, confidence, failure modes, memory references, and paper/report/readonly flags. They must consume central market and cost context as supplied inputs rather than independently creating execution or exchange-integration paths.

## Review Workflow

Apply [Native Codex Subagent Routing](../AGENTS.md#native-codex-subagent-routing)
to project planning and dispatch a fresh, independent reviewer for each
plan, code, stage, post-node, and handoff review under Project Iron Rule 4.

Review prompts are read-only. Reviewers may inspect plans, diffs, files, reports, and test output, but must not modify, create, delete, migrate, backfill, submit orders, mutate databases, mutate exchange state, or alter account state.

The gate is blocked when no eligible independent reviewer can be dispatched.
There is no fallback reviewer under the current project rules.

Review packets should include:

- the intended scope and disjoint write ownership;
- the Phase 1 boundary assertions;
- changed paths and focused tests;
- local Supabase/Postgres durable-only implications;
- redaction checks;
- open risks and operator follow-ups.

## Redaction Rules

Operator reports, review packets, logs, CLI output, persisted payloads, and documentation examples must redact or omit sensitive and unsafe values.

Never print or persist unredacted:

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
- order ids tied to a live account;
- credential-like headers;
- operator secrets;
- source payloads that contain credentials or private account data.

Use explicit redaction markers such as `<redacted>` for configuration representations. Prefer aggregate report fields, reason codes, hashes, counts, timestamps, and local row identifiers over raw sensitive payloads. Documentation examples must use placeholder values only and must not include real-looking secrets, keys, DSNs, authorization-header tokens, wallet addresses, or account identifiers.

## Test Contract

The docs contract tests for this file guard the following constraints:

- the medium-scale specialist team taxonomy is present;
- long-term memory is defined as local research context and is gated by allow/throttle/block policy;
- durable persistence is local Supabase/Postgres only;
- alternate durable stores and durable file substitutes remain forbidden;
- Phase 1 stays paper-only, report-only, and readonly;
- the document forbids automated investing and live order placement rather than promising them;
- planning and the independent review gate follow the root subagent routing rule;
- review prompts are read-only;
- redaction rules cover DSNs, secrets, credentials, wallet material, and account/order-sensitive values.
