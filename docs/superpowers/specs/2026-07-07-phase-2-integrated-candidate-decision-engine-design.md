# Phase 2 Integrated Candidate Decision Engine

This document defines the Phase 2 design for turning the existing paper-only
Polymarket Alpha Lab reports into an auditable candidate-market decision
engine. The engine is decision support for probability-event markets. It is
not live trading, not automated investing, not order intent, not financial
advice, not position sizing, and not execution authorization.

Phase 2 keeps the project inside the established paper-only, report-only, and
readonly boundary while moving from isolated diagnostics toward one integrated
candidate decision layer.

## Goals

Phase 2 must answer one operator-facing question for each candidate market:

```text
Given the current market context, evidence, costs, liquidity, resolution risk,
and specialist-team memory, should this market be rejected, watched, researched
further, or promoted to a paper recommendation candidate?
```

The engine should produce a structured decision record with:

- the assigned specialist team;
- side-aware forecast and executable-price context when available;
- gross edge, estimated cost drag, and net edge;
- cost, liquidity, evidence, resolution-risk, and team-memory scores;
- deterministic action state;
- reason codes;
- source/report references;
- a safe payload for local Supabase/Postgres persistence;
- paper-only/report-only/readonly flags.

## Non-Goals

Phase 2 must not introduce:

- live trading;
- wallet handling;
- account authentication;
- private-key handling;
- order signing;
- order submission;
- order cancellation;
- order replacement;
- exchange mutation;
- hosted account reads;
- automated investing;
- capital allocation execution;
- durable JSON/CSV/file-backed state as a replacement for local Supabase.

All durable project data remains local Supabase/Postgres only. Temporary test
fixtures may use in-memory objects or temporary files under the test sandbox,
but durable candidate decisions, source traces, team memory, and decision
history must use local Supabase/Postgres.

## Operating Flow

The intended Phase 2 flow is:

```text
read-only Polymarket market candidate
  -> market/event normalization
  -> specialist team assignment
  -> research packet and source trace
  -> cost and liquidity adapter
  -> evidence/source-quality adapter
  -> resolution-risk adapter
  -> team-memory/calibration adapter
  -> integrated candidate decision score
  -> reject/watch/research_more/paper_recommend action
  -> local Supabase/Postgres decision history row
  -> outcome and calibration feedback after resolution
```

Each step consumes explicit inputs and returns immutable dataclasses. Reducers
must be deterministic for the same inputs. The engine may use live read-only
market or public-source retrieval in upstream collection layers, but the
decision reducer itself must not perform network I/O, database writes, or
exchange/client mutation.

## Action States

The Phase 2 engine uses four canonical actions:

| Action | Meaning | Required Follow-Up |
| --- | --- | --- |
| `reject` | The market is not a usable paper candidate because one or more required gates fail. | Record the blocking reason codes and do not reopen until the specific blocker changes. |
| `watch` | The market is not actionable now, but price, liquidity, source, or event updates could create a later decision point. | Record trigger conditions and refresh cadence. |
| `research_more` | The market may have edge, but a concrete evidence, cost, liquidity, resolution, or memory gap is still answerable. | Assign a specialist team and required research tasks. |
| `paper_recommend` | The paper-only evidence set is strong enough to promote the market into the paper recommendation workflow. | Persist the decision, route to operator review, and keep execution disabled. |

`paper_recommend` is not permission to trade. It is a paper workflow state that
means the market is ready for operator review and later paper-trading
evaluation.

## Scoring Model

The first implementation should use explicit Decimal scores and rule-based
gates rather than opaque model weights. A score is a normalized Decimal in the
closed interval `[0.0000, 1.0000]` unless the field name states otherwise.

Required component scores:

- `cost_score`: estimated fee, spread, slippage, settlement drag, and edge
  decay pressure.
- `liquidity_score`: executable depth, spread quality, capacity buffer, and
  rotation risk.
- `evidence_score`: source freshness, authority, redundancy, traceability,
  counterevidence pressure, and SLA status.
- `resolution_score`: question specificity, rule clarity, dependency risk,
  close/readiness status, and source alignment.
- `team_memory_score`: specialist assignment readiness, team calibration,
  domain memory, source-memory reliability, and capacity pressure.

The first integrated score should expose the component scores rather than hide
them behind a single number. A top-level `decision_score` may be computed as a
weighted Decimal average only after hard blockers are applied.

Default hard blockers:

- missing or non-readonly source flags;
- no specialist team assignment;
- evidence score below the blocking threshold;
- resolution score below the blocking threshold;
- negative or missing net edge when edge is required for promotion;
- liquidity below the minimum paper notional threshold;
- blocked team-memory gate;
- stale or internally inconsistent upstream reports;
- any Phase 2 safety-boundary violation.

## Event-Team Model

The medium-scale specialist team model remains the default:

- `politics`;
- `crypto_btc`;
- `crypto_eth`;
- `macro_rates`;
- `equity_indices`;
- `commodities_gold`;
- `commodities_oil`;
- `sports_soccer`;
- `sports_basketball`;
- `sports_other`.

Each candidate decision should include exactly one primary team and may include
secondary teams for operator context. Team memory can improve research quality
only when its readiness gate is `allow` or `throttle`. A `block` memory gate
must prevent `paper_recommend`.

Team memory is long-term project evidence persisted in local Supabase/Postgres.
It is not model hidden state and not an execution memory. It must be
replayable through persisted rows, report IDs, source references, timestamps,
and deterministic reason codes.

## Information Acquisition

Information quality is the most important input. Phase 2 should prefer
primary, authoritative, timestamped, and source-traceable data. Upstream
collectors may use read-only tools such as the local browser/devtools,
agent-reach, Scrapling, official APIs, and public web sources when a task
requires fresh information.

Every acquired source that influences a decision must be represented as a
source trace with:

- source identifier or URL;
- source family;
- retrieval timestamp in UTC;
- authority tier;
- freshness status;
- contradiction status;
- whether the source is primary, secondary, derived, or commentary;
- safe redacted payload metadata.

The integrated decision reducer should consume source traces as supplied data.
It must not scrape, browse, or fetch data directly.

## Data Contracts

The core in-memory contract should be split across small modules so multiple
workers can develop without file conflicts:

- `candidate_decision_score.py`: normalized score dataclasses, action-state
  reducer, hard blocker logic, reason-code aggregation, safe payload.
- `candidate_decision_cost_liquidity_adapter.py`: conversion from existing
  cost, fee, slippage, liquidity, capacity, and edge-decay reports into cost
  and liquidity score inputs.
- `candidate_decision_evidence_adapter.py`: conversion from existing evidence,
  source freshness, authority, redundancy, counterevidence, and traceability
  reports into evidence score inputs.
- `candidate_decision_resolution_risk_adapter.py`: conversion from existing
  resolution, question-specificity, dependency, close-readiness, and outcome
  source-alignment reports into resolution score inputs.
- `candidate_decision_team_memory_adapter.py`: conversion from specialist-team,
  assignment, calibration, memory-readiness, and domain-memory reports into
  team memory score inputs.
- `paper_candidate_decision_engine.py`: orchestration over supplied adapter
  outputs into one paper-only decision report.
- `candidate_decision_db_row.py`: canonical local Supabase/Postgres row
  representation.
- `candidate_decision_store.py`: local Supabase/Postgres insert/read helpers.
- `supabase_candidate_decision_config.py`: env-derived local Supabase/Postgres
  DSN and table configuration.

Each module should have exactly one matching focused test module when committed
under the current project commit rule.

## Supabase Persistence

The durable Phase 2 decision history target is a local Supabase/Postgres table.
The expected table name is configurable through env, validated with the
project's existing table-name validation helper, and defaults to a canonical
candidate-decision history table once the config module is implemented.

Required persisted fields:

- generated timestamp;
- config version;
- market identifier;
- normalized market question;
- primary team;
- secondary teams;
- action;
- component scores;
- decision score;
- gross edge;
- estimated cost drag;
- net edge;
- liquidity status;
- evidence status;
- resolution status;
- team memory status;
- reason codes;
- source/report references;
- safe payload digest;
- paper-only/report-only/readonly flags.

Raw DSNs, secrets, auth tokens, cookies, account identifiers, wallet material,
private keys, order IDs, and unredacted credentials must never be printed,
persisted in payloads, or included in review packets.

## CLI Contract

The first CLI surface should be a paper-only readback command, not an
execution command:

```bash
polymarket-alpha-lab paper-candidate-decision-engine --limit 25
```

The command should read already-persisted upstream paper reports, build
integrated decisions, print aggregate counts and reason-code summaries, and
optionally persist final decision rows when a separate explicit persist command
or env-enabled producer is added. The initial reducer and tests should not
depend on live network access.

The CLI output must make the safety boundary visible with
`paper_only=True`, `report_only=True`, and `readonly=True` markers when it
prints final reports.

## Testing Strategy

The implementation should use test-first development for each module.
Targeted tests must cover:

- invalid action/state values;
- Decimal-only score normalization;
- hard blocker precedence;
- net-edge after costs;
- evidence freshness and authority blockers;
- resolution ambiguity blockers;
- team-memory block/throttle/allow behavior;
- deterministic reason-code ordering;
- safe payload redaction;
- local Supabase DSN validation before DB use;
- no network, wallet, auth, order, or exchange mutation surfaces.

Integration tests should use supplied dataclasses and fake local connections
only. They should not require external network access or a live hosted service.

## Review Workflow

Follow [Native Codex Subagent Routing](../../../AGENTS.md#native-codex-subagent-routing)
for Phase 2 planning and fresh independent reviews under Project Iron Rule 4.

Review prompts are read-only. Reviewers may inspect plans, diffs, files, and
test output, but must not modify files, mutate databases, submit orders, change
account state, or perform exchange mutations.

The gate is blocked when no eligible independent reviewer can be dispatched.

## Parallel Development Plan

After this design is reviewed and committed, the next development wave should
run independent workers on disjoint file pairs:

1. Core score reducer and tests.
2. Cost/liquidity adapter and tests.
3. Evidence adapter and tests.
4. Resolution-risk adapter and tests.
5. Team-memory adapter and tests.
6. Supabase config/db-row/store modules and tests, split into one module pair
   per commit.
7. Engine orchestration and tests.
8. CLI wiring and tests.

The main controller should keep workers on disjoint ownership, close completed
agents promptly, independently verify their changes, and obtain an independent
read-only review for each change. Commit the intended module/test file pair
after approval and follow the root Codex Node Push Policy.
