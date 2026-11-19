# Polymarket Alpha Lab

> **当前主线是模拟盘（paper-sim）**。M1 工程出口已验收，M2 按 [蓝图 v3](docs/paper-sim/blueprint.md) 继续接线。V1 六个工作包中 WP-01 已完整验收，真实模型研究按 OD-A 处于未激活状态。当前交付状态和证据见 [DELIVERY_PLAN.md](DELIVERY_PLAN.md)。

Polymarket Alpha Lab is a research-first project for finding, scoring, and validating Polymarket markets before any capital is committed.

**Current delivery status:** [DELIVERY_PLAN.md](DELIVERY_PLAN.md) is the single
authoritative status and acceptance plan for V1-Research-Paper and its six work
packages; see DELIVERY_PLAN.md for current acceptance counts and remaining gaps.
V1 uses project-private native PostgreSQL; older capability notes below are historical where they conflict with the plan.

Linux is the sole V1 development and delivery platform (DELIVERY_PLAN.md section 61; pinned release tuple: Ubuntu 26.04.1 x86_64, Python 3.12.14, an exactly pinned PostgreSQL 18 build). For local development, start with the [Linux local setup guide](docs/development/linux-local-setup.md) for locked installation, Bash commands, and offline verification. The historical [Windows local setup guide](docs/development/windows-local-setup.md) is retained unchanged for reference; Windows setup, verification and acceptance are no longer active V1 obligations.

The long-term research direction is a system that can screen markets, research candidates, and prepare proposals while treating execution as a later, separately validated phase. The first version is intentionally not a trading bot. It is a planning and research workspace for:

- market discovery and metadata normalization
- order book and liquidity quality scoring
- strategy research around measurable edges
- paper-trading journals, risk gates, and rejection review
- future backtesting and signal validation

## Phase 1 Scope

This repository currently contains the project design, research notes, implementation plans, a read-only market scanner, research packet assembly, paper research packet generation and DB-history readback, pure paper research packet quality reports and quality-history trend gates, bid/ask paper-fill simulation, mandatory project-private native PostgreSQL paper-trade record write persistence for `strategy-cycle --paper-execute`, DB-backed paper-trade read sources for the one-shot `portfolio-nav` CLI, continuous-run NAV source handling, `check-outcomes` source handling, and `cost-audit` source handling when the local paper trade journal DB env is enabled, a read-only DB helper for paper cost-audit source records, legacy JSONL compatibility/export/replay for not-yet-migrated JSONL-reader consumers, paper-only risk gates, rejected-candidate logs, paper position ledgers, executable NAV marks, paper-only portfolio analytics, exposure reports, executable-NAV drawdown reports, paper-only analytics history validation, paper-only forecast evidence reports, paper-only outcome-tracking report logs, local-only strategy risk audit CLI reports with a cost discipline gate over paper logs, optional local Strategy Risk Audit logs and history summaries, local-only strategy evidence snapshot summaries over paper logs and local reports, local-only paper trade cost audit reports over paper logs, paper-only cost-aware event strategy reports, paper-only project screening research queues, paper-only manual-review queues, paper-only strategy recommendation bundle and bundle-log artifacts with a read-only history CLI summary, human-review proposal packet artifacts, append-only proposal-review record artifacts, proposal-review summary report artifacts, proposal-review quality gate artifacts, proposal-review diagnostic artifacts, proposal-review coverage report artifacts, proposal-review dossier artifacts, proposal-review dossier batch health artifacts, proposal evidence comparison artifacts, proposal evidence comparison history artifacts, proposal evidence comparison history batch-health artifacts, proposal evidence comparison history batch-health trend artifacts, proposal evidence comparison history batch-health trend-batch artifacts, proposal evidence comparison history batch-health trend-batch health artifacts, proposal evidence comparison history batch-health trend-batch health trend artifacts, paper autonomous allocation proposal artifacts, paper autonomous allocation proposal DB-history artifacts, paper autonomous allocation proposal DB-history gate artifacts, paper autonomous allocation proposal DB-history metrics artifacts, paper autonomous allocation proposal DB-history health artifacts, paper autonomous allocation proposal DB-history health trend artifacts, paper autonomous allocation proposal DB-history health-trend gate artifacts, paper autonomous investment ledger artifacts, paper autonomous investment ledger DB-history artifacts, paper autonomous readiness digest CLI/readback with optional agreement trend-gate evidence, and local DB persistence for DB-history health reports through project-private native PostgreSQL plus explicitly documented DB-backed paper evidence surfaces.

## Team Agent Framework

See [docs/team-agent-framework.md](docs/team-agent-framework.md) for the Phase 1 team-agent framework. The current V1 runnable slice covers `crypto_btc` and `crypto_eth` for supported fixed-timepoint Yes/No contracts; it remains paper-only, report-only, and readonly, with project-private native PostgreSQL persistence for team profiles, routes, forecasts, forecast evidence, and outcomes.

Polymarket is a probability event market. In Phase 1, Polymarket Alpha Lab performs automatic screening, research, and paper execution only; no live trading, no investment advice, no trade instruction, no position advice, and no position sizing. Research frameworks must consider costs and fees as research factors, including spread, slippage, liquidity, settlement timing, and other paper cost evidence, before any paper-only report is used downstream.

See [docs/phase-1-team-memory.md](docs/phase-1-team-memory.md) for the Phase 1 team-memory docs-to-code contract. It defines politics, finance, and sports research-team ownership, long-term team memory policy, and local Supabase/Postgres durable-only storage for team diagnostics, memory readiness, and assignment report evidence. This contract is research operations only: no live trading, auth, wallet, account, order, recommendation, investment advice, trade instruction, financial advice, strategy-weight tuning, position advice, or position sizing.

See [docs/team-memory-readiness-digest-db-persistence.md](docs/team-memory-readiness-digest-db-persistence.md) for the `team_memory_readiness_digest_reports` persistence clarification. That table is a local Supabase/Postgres durable report-history exception only; it is not a live trading mutation, not a default CLI side effect, preserves `paper_only=True`, `report_only=True`, and `readonly=True`, and must not be replaced by SQLite, JSONL, file-backed, hosted, generic, or other alternate durable backends.

See [docs/phase-1-multi-team-operating-model.md](docs/phase-1-multi-team-operating-model.md) for the Phase 1 multi-team operating-model contract. It defines the medium-scale specialist research teams, long-term team memory, local Supabase/Postgres durable evidence, and Phase 1 review/redaction boundaries.

Phase 1 team command discovery uses the real runnable help surfaces: `polymarket-alpha-lab team-memory-readiness-digest --help`, `polymarket-alpha-lab team-memory-readiness-digest-history --help`, `polymarket-alpha-lab team-research-assignment --help`, and `polymarket-alpha-lab team-research-assignment-history --help`. These help invocations document Phase 1 paper-only/report-only/readonly local Supabase/Postgres readback and explicitly state no live trading, no auth, no wallet, no order, no account, no investment advice, no trade instruction, and no position advice.

For team diagnostics over those persisted rows, see [docs/team-diagnostics-readonly.md](docs/team-diagnostics-readonly.md). Diagnostics are a local Supabase/Postgres read-only Phase 1 paper/report-only path; live trading, auth, wallet, account, order, and exchange mutation surfaces remain forbidden.

Team diagnostics snapshot history is also a Phase 1 paper-only/report-only/readonly local Supabase/Postgres surface. The `team_diagnostics_snapshots` table is internal report persistence for generated diagnostics snapshots only, configured with `POLYMARKET_ALPHA_LAB_TEAM_DIAGNOSTICS_SNAPSHOT_DB_ENABLED`, `POLYMARKET_ALPHA_LAB_TEAM_DIAGNOSTICS_SNAPSHOT_DB_DSN`, and `POLYMARKET_ALPHA_LAB_TEAM_DIAGNOSTICS_SNAPSHOT_DB_TABLE`; it is not live trading, auth, wallet, account, order, or exchange mutation infrastructure.

Read persisted local Supabase/Postgres snapshot history with `polymarket-alpha-lab team-diagnostics-snapshot-history --limit 100`. The command reads `team_diagnostics_snapshots` through the snapshot env vars only, performs readback over generated diagnostics snapshots, and reports long-term team-memory deltas such as `evidence_quality_average_delta=`, `memory_eligible_delta=`, and `settled_calibration_delta=`. Sample output fields include `status=`, `snapshot_count=`, `required_snapshot_count=`, `earliest_generated_at=`, `latest_generated_at=`, `span_seconds=`, `status_counts=`, `duplicate_latest_generated_at=`, `reason_codes=`, `paper_only=True`, `report_only=True`, and `readonly=True`. It stays paper-only/report-only/readonly with no live trading, auth, wallet, account, order, exchange mutation, or DSN/table CLI flags.

Gate persisted diagnostics snapshot history with `polymarket-alpha-lab team-diagnostics-snapshot-history-gate --limit 100`. This is a local Supabase/Postgres readback over persisted diagnostics snapshot history using the same snapshot env vars and `team_diagnostics_snapshots`; it is a pass/watch/blocked gate for the local history surface. Gate status `pass` means readback succeeds with enough persisted history and no blocking reason codes. Gate status `watch` means readback succeeds but has non-blocking deterioration or source reason observations, such as evidence quality, memory coverage, settled calibration, or source history warning signals. Gate status `blocked` means required local DB configuration is missing or invalid, readback fails, hard Phase 1 flags are missing or false, or the persisted history is insufficient, stale, or duplicate-latest history. The gate status `pass`, gate status `watch`, and gate status `blocked` outputs are the only documented gate outcomes. Sample output fields include `gate_status=`, `recommended_next_step=`, `source_config_version=`, `source_generated_at=`, `source_snapshot_count=`, `source_required_snapshot_count=`, `source_status=`, `source_span_seconds=`, `source_status_counts=`, `source_reason_codes=`, `latest_snapshot_age_seconds=`, `reason_code_counts=`, `evidence_quality_average_delta=`, `memory_eligible_delta=`, `settled_calibration_delta=`, `duplicate_latest_generated_at=`, `reason_codes=`, `paper_only=True`, `report_only=True`, and `readonly=True`. It remains Phase 1 read-only/report-only and paper-only/report-only/readonly with no live trading, auth, wallet, account, order path, exchange mutation, or DSN/table CLI flags.

Summarize team readiness for long-term team-memory with `polymarket-alpha-lab team-memory-readiness-digest --team-id crypto_btc --limit 100`. This command is a Phase 1 read-only/report-only/paper-only local Supabase/Postgres readback that builds per-team digest sources backed by diagnostics snapshot history gate results. It reads `team_diagnostics_snapshots` only through the existing snapshot env vars `POLYMARKET_ALPHA_LAB_TEAM_DIAGNOSTICS_SNAPSHOT_DB_ENABLED`, `POLYMARKET_ALPHA_LAB_TEAM_DIAGNOSTICS_SNAPSHOT_DB_DSN`, and `POLYMARKET_ALPHA_LAB_TEAM_DIAGNOSTICS_SNAPSHOT_DB_TABLE`; configuration stays in environment variables, with no DSN/table CLI flags. The command wraps one diagnostics snapshot history gate result per selected team as a `TeamMemoryReadinessDigestSource` and composes those sources into a pass/watch/blocked digest for downstream paper-only research use of long-term team-memory. The digest status `pass` means every selected team's source gate passed and recommends `allow_team_memory_readiness_use`. The digest status `watch` means no selected team is blocked but at least one source gate is `watch`, and recommends `throttle_team_memory_readiness_use`. The digest status `blocked` means at least one source gate is blocked or no sources are available, and recommends `block_team_memory_readiness_use`; empty source sets are blocked with `team_memory_readiness_digest_empty_sources`. Passing sources emit `team_memory_readiness_digest_passed`; watched sources emit `team_memory_readiness_digest_watch_sources_present`; blocked sources emit `team_memory_readiness_digest_blocked_sources_present`. Sample output fields include `digest_status=`, `recommended_next_step=`, `team_count=`, `pass_count=`, `watch_count=`, `blocked_count=`, `source_statuses=crypto_btc:pass:1200:12/3`, `source_config_versions=crypto_btc:team-diagnostics-snapshot-history-v0`, `reason_code_counts=team_memory_readiness_digest_passed:1`, `reason_codes=`, `paper_only=True`, `report_only=True`, and `readonly=True`. It remains local evidence only, with no live trading, no order path, no exchange mutation, no ranking, no recommendations, no trade instructions, no financial advice, no strategy-weight tuning, and no position sizing.

## Project Iron Rules

- Persistent project data must use only the local Supabase/Postgres instance on this host. Do not add SQLite, Redis, Mongo, SQLAlchemy, hosted database assumptions, generic database abstraction layers, or file-backed database substitutes as durable persistence.
- Any raw DSN from environment variables, config objects, CLI plumbing, tests, fixtures, or helper constructors must pass through `validate_local_postgres_dsn` before a connection, psycopg wrapper, or persistence adapter uses it.
- Phase 1 is paper-only, report-only, and readonly. It must not add live trading, account authentication, wallet/private-key handling, account reads, order signing, order submission, order cancellation, order replacement, or exchange/order mutation.
- DB-backed features in Phase 1 are local paper evidence storage and readback only. Persisted and recovered report surfaces must preserve `paper_only=True`, `report_only=True`, and `readonly=True` wherever those flags exist.

Continuous run as a whole is not fully DB-backed; only its NAV paper-trade source handling can use `paper_trade_journal_records` when the local paper trade journal DB env is enabled. `check-outcomes` and `cost-audit` can also use `paper_trade_journal_records` as their paper-trade source when that env is enabled. JSONL remains the legacy compatibility/export/replay path and the still-current input for history/performance summary, strategy audit, and observability/trend consumers until their separate migrations land.

It also includes an optional local-only Strategy Risk Audit preflight for continuous paper runs, optional Strategy Risk Audit logging, and a local history summary over that optional log; the preflight reads existing paper logs and can pause the next paper run before any public client is constructed, audit logging is explicit opt-in local append-only JSONL evidence, and the history summary reads that evidence without changing run behavior.

A separate `strategy-evidence` summary is local evidence observability only: it reads caller-selected local paper logs and existing local reports to describe evidence presence, gaps, and risk flags without changing run behavior.

The `observability-trends` command is a local-only trend summary over the same paper artifacts. It reads caller-selected cycle, paper-trade, NAV, optional outcome, and optional Strategy Risk Audit logs, builds the existing strategy-evidence, outcome-freshness, NAV-risk, and paper-trade-cost trend reports, and prints a compact read-only summary without changing strategy behavior. By default it performs no DB write and mutates no local logs or artifacts; an optional paper-only/report-only/readonly DB insert is allowed only when `--persist` is passed and the local observability DB environment config is enabled.

The current phase does not contain:

- account authentication
- private key handling
- automated order placement
- live trading logic
- compliance or legal analysis

These are Phase 1 scope boundaries. They are not permanent non-goals. Future execution work is tracked in the automated investment roadmap and must pass documented validation gates before live capital is introduced.

## Team Research Assignment

The team research assignment report is a Phase 1 read-only operations handoff for assigning existing strategy candidate research queue rows to specialist teams. It consumes the candidate research queue, team market routes, and team memory readiness digest, then records whether each assigned team may use long-term memory with an `allow`, `throttle`, or `block` policy. See [docs/team-research-assignment.md](docs/team-research-assignment.md). The report preserves queue order and does not rank investments, recommend trades, tune strategy weights, size positions, or create any live trading, auth, wallet, account, order, or exchange-mutation path. When any source input is read from durable storage, it must come from existing env-driven local Supabase/Postgres report or team tables only.

The env-only local Supabase/Postgres DB-source and CLI composition for team research assignment is `polymarket-alpha-lab team-research-assignment --team-id crypto_btc --queue-limit 1 --route-limit 25 --memory-limit 100`. The command fails closed unless the queue, team forecast route, and diagnostics snapshot DB env families are enabled with DSNs, then reads exactly one research-ready queue report, merges persisted one-row route reports from `team_market_routes` into one route report, and builds team-memory readiness through diagnostics snapshot DB env only. Empty route readback is not converted into an assignment report. The relevant env families are `POLYMARKET_ALPHA_LAB_STRATEGY_CANDIDATE_RESEARCH_QUEUE_DB_ENABLED`, `POLYMARKET_ALPHA_LAB_STRATEGY_CANDIDATE_RESEARCH_QUEUE_DB_DSN`, `POLYMARKET_ALPHA_LAB_STRATEGY_CANDIDATE_RESEARCH_QUEUE_DB_TABLE`; `POLYMARKET_ALPHA_LAB_TEAM_FORECAST_DB_ENABLED`, `POLYMARKET_ALPHA_LAB_TEAM_FORECAST_DB_DSN`, `POLYMARKET_ALPHA_LAB_TEAM_ROUTE_DB_TABLE`; and `POLYMARKET_ALPHA_LAB_TEAM_DIAGNOSTICS_SNAPSHOT_DB_ENABLED`, `POLYMARKET_ALPHA_LAB_TEAM_DIAGNOSTICS_SNAPSHOT_DB_DSN`, `POLYMARKET_ALPHA_LAB_TEAM_DIAGNOSTICS_SNAPSHOT_DB_TABLE`. CLI selectors are report-scoped only: `--team-id`, `--queue-source-config-version`, `--memory-config-version`, `--queue-limit`, `--route-limit`, and `--memory-limit`. Defaults are queue limit `1`, route limit `500`, and memory limit `100`; omitting `--team-id` selects all known teams. There are no `--config-version`, `--market-slug`, `--forecast-id`, DSN/table CLI flags, or `--persist`, `--live`, auth, wallet, account, order, trade, execute, or submit flags.

Generated team research assignment reports can be persisted as internal Phase 1 report evidence in local Supabase/Postgres table `team_research_assignment_reports` when `POLYMARKET_ALPHA_LAB_TEAM_RESEARCH_ASSIGNMENT_DB_ENABLED=true` and `POLYMARKET_ALPHA_LAB_TEAM_RESEARCH_ASSIGNMENT_DB_DSN` are set. The optional table env var is `POLYMARKET_ALPHA_LAB_TEAM_RESEARCH_ASSIGNMENT_DB_TABLE`, defaulting to `team_research_assignment_reports`. This is an env-only report sink attached to `team-research-assignment`, not a CLI persistence toggle; the command still exposes no DSN/table/persist/live/auth/wallet/account/order/trade/execute/submit flags. Persisted rows store a canonical payload with hard `paper_only=True`, `report_only=True`, and `readonly=True` flags plus materialized report counts/status for readback integrity.

Read persisted assignment history with `polymarket-alpha-lab team-research-assignment-history --limit 100`. This command reads only the assignment report table through the assignment DB env vars and prints aggregate history fields such as `history_status=`, `report_count=`, `required_report_count=`, `status_rows=ready:1,watch:1,blocked:1`, latest assignment counts, count deltas, `duplicate_latest_generated_at=`, `reason_codes=`, and hard flags. It does not print market slugs or questions, does not rank markets or teams, and does not create recommendations, trade instructions, sizing, execution, account, wallet, auth, order, or exchange mutation paths.

## Phase 1 Operational Freeze

Phase 1 decision behavior is frozen while markets are pending settlement. Do not tune screening weights or change strategy-cycle behavior while markets are pending settlement, and do not change forecast prompts/providers, paper-execution triggers, or sizing rules from unresolved NAV marks.

Allowed additions during the freeze are limited to `paper_only`, `report_only`, `readonly` observability over existing local artifacts. Boundary shorthand: no fetch, no auth, no wallet, no order, no new ranking or recommendation decision behavior, no trade instruction, no financial advice.

Within this freeze, the recommendation-layer artifacts are allowed only as paper-only report packaging over existing local paper reports. They must not tune screening weights, change strategy-cycle behavior, create new sizing rules, place orders, or present live trade instructions.

## Phase 2 Direction

Phase 2 starts with two pure local report modules exposed only through module-local APIs: `forecast_calibration` summarizes resolved forecast evidence from caller-supplied `PaperForecastEvidenceObservation` rows that include both `predicted_probability` and `actual_outcome_value`, and `strategy_segment_summary` groups caller-supplied paper evidence by local strategy and risk-tag segments.

This direction is still paper-only/report-only/read-only. The new modules remain module-level library APIs with exact module-local `__all__` values rather than package-root exports, CLI commands, readers, writers, or live-data wrappers. Calibration is descriptive resolved-evidence reporting only: edge-only or unresolved rows do not become calibration observations. Segment summaries keep probability observations and paper-return observations as separate roles, so return-only evidence remains descriptive and thin probability samples stay visibly incomplete.

This is not live trading, not a later-phase transition signal, not a ranking or recommendation engine, not a trade instruction surface, and not financial advice. Before any later phase can move beyond reporting, cost, slippage, liquidity, settlement lag, and rule risk must be measured separately from calibration quality.

Boundary shorthand: no live trading, no auth, no wallet/private key, no order placement/signing/submission/cancellation, no account reads, no recommendations/ranking/trade instruction/financial advice.

## NAV Risk Metrics v0 Status

NAV Risk Metrics v0 is a paper-only/report-only risk summary over existing paper NAV logs. It consumes typed `PaperNavSnapshot` values normally read through the existing `PaperNavLog.read(...)`; it computes NAV time-series risk, latest mark-status counts, pending notional, and latest exposure concentration for audit visibility only.

The `nav-risk` command is separate from strategy-cycle, NAV marking, outcome tracking, and paper execution. It does not fetch market/account/order data, authenticate, handle wallets or private keys, place/sign/submit/cancel orders, rank investments, recommend trades, provide trade instruction, provide financial advice, or alter any strategy behavior.

## NAV Risk Metrics v0 Python API

- Configure the report with `PaperNavRiskMetricsConfig(config_version="nav-risk-metrics-v0")`.
- Build risk summaries with `build_paper_nav_risk_metrics_report(nav_snapshots, config=config, generated_at=...)`, which returns `PaperNavRiskMetricsReport`.
- Inspect latest exposure concentration with `PaperNavRiskExposureRow` values on `report.exposure_rows`.
- Print local metrics from the CLI with `polymarket-alpha-lab nav-risk --nav-log <path>`; the command reads local NAV JSONL logs only and preserves the same `paper_only`, `report_only`, `readonly` boundary: no fetch, no auth, no wallet, no order, no rank, no recommend, no trade instruction, no financial advice.

## Strategy Risk Audit v0 Status

Strategy Risk Audit v0 is a pure, paper-only/report-only/read-only audit over caller-supplied typed reports: `PerformanceSummary`, `PaperNavRiskMetricsReport`, optional `OutcomeTrackingReport`, optional `PaperTradeCostAuditReport`, and optional `PaperNavSettlementRiskOverlayReport`. It reduces existing paper history, settlement evidence, nested forecast probability quality, paper trade cost evidence, NAV drawdown, and open-exposure observations into the six legacy gates: `paper_history`, `settlement_evidence`, `forecast_quality`, `cost_discipline`, `nav_drawdown`, and `open_exposure`. When a settlement/NAV overlay report is supplied, the audit appends the optional `settlement_nav_risk` gate.

The report emits one status: `audit_ready` when every gate passes, `blocked_by_risk` when any risk gate fails, and `insufficient_evidence` when evidence is incomplete and no gate fails. `paper_only is True` and `report_only is True` are hard-enforced on every report.

The `cost_discipline` gate uses the local paper trade cost audit evidence: paper trade count, mean edge cost drag, and negative cost-adjusted edge count. It is incomplete when cost evidence is absent, when mean edge cost drag is unavailable, or when the trade count is below the configured floor; it fails when cost drag or negative cost-adjusted edge counts breach configured limits; and it otherwise passes as report-only evidence.

The optional `settlement_nav_risk` gate consumes only an already-built paper settlement/NAV overlay report. This source is paper-only/report-only/readonly; it does not add live trading; does not add auth or wallet handling; does not add order submission, cancellation, or replacement; and does not add persistence, DB loaders, env reads, or CLI flags.

Optional local Supabase/Postgres persistence for `PaperStrategyRiskAuditReport` snapshots is available as a DB foundation only. It is default-off, env-driven, has no DSN CLI flags, stores canonical `payload_json` plus audit status/count scalars, and preserves `paper_only`, `report_only`, and row-level `readonly` flags. See `docs/strategy-risk-audit-db-persistence.md`.

Phase 1 boundary: this module is pure local report math. It does not score markets, select projects, tune strategy weights, size positions, read files, write logs, fetch, authenticate, handle wallets, handle private keys, read account data, construct or use API clients, perform live trading, place/sign/submit/cancel orders, make recommendations, rank investments, provide trade instruction, or provide financial advice.

## Strategy Risk Audit v0 Python API

- Configure the audit with `PaperStrategyRiskAuditConfig(...)`, including the minimum paper-history, settlement-evidence, forecast-probability-observation, and cost-audit trade-count thresholds plus cost-drag, NAV drawdown, and open-exposure limits.
- Build the audit with `build_paper_strategy_risk_audit_report(performance_summary=..., nav_risk_report=..., outcome_report=..., cost_audit_report=..., config=..., generated_at=...)`, with optional `settlement_nav_risk_report=...` when the caller already has a `PaperNavSettlementRiskOverlayReport`; the builder returns `PaperStrategyRiskAuditReport` from caller-supplied typed reports only.
- Inspect deterministic gate rows with `PaperStrategyRiskAuditGateResult` values on `report.gate_results`; legacy gate names are `paper_history`, `settlement_evidence`, `forecast_quality`, `cost_discipline`, `nav_drawdown`, and `open_exposure`, with optional `settlement_nav_risk` appended only when the settlement/NAV overlay source is supplied, and the final `report.status` is one of `audit_ready`, `blocked_by_risk`, or `insufficient_evidence`.
- There is no file reader, log writer, fetch path, live-trading path, auth path, wallet path, private-key path, account-read path, API-client path, order placement/signing/submission/cancellation path, recommendation path, ranking path, trade-instruction path, strategy-weight tuning, project selection, market scoring, position sizing, or financial-advice surface.

## Strategy Risk Audit v0 CLI

- Print a Strategy Risk Audit report from local paper artifacts with `polymarket-alpha-lab strategy-audit --cycle-log <path> --trade-log <path> --nav-log <path> [--outcome-log <path>] [--strategy-audit-log <path>]`.
- The command reads existing local paper JSONL logs only, builds the typed performance, NAV-risk, paper trade cost audit, and optional outcome reports needed by `build_paper_strategy_risk_audit_report(...)`, and prints a paper-only/report-only six-gate summary.
- The `cost_discipline` gate is driven by a `PaperTradeCostAuditReport` built from the local `--trade-log`; empty or thin trade logs leave the gate incomplete.
- `--strategy-audit-log <path>` is explicit opt-in persistence: when supplied, the command appends the same already-built report it prints to a caller-selected local JSONL evidence artifact; when omitted, no audit log file is created or updated.
- The optional audit log is append-only local evidence, not an approval workflow, recommendation, ranking, trade instruction, financial advice, or live execution.
- With or without the optional audit log, the command does not fetch data, authenticate, handle wallets or private keys, read account data, use account/order APIs, place/sign/submit/cancel orders, rank investments, recommend trades, provide trade instruction, provide financial advice, tune strategy behavior, or alter strategy-cycle decisions.
- When `--outcome-log` is omitted or empty, the settlement-evidence and forecast-quality gates remain incomplete by design.

## Strategy Risk Audit History Summary v0

- Summarize an explicitly selected local Strategy Risk Audit JSONL artifact with `polymarket-alpha-lab strategy-audit-history --strategy-audit-log <path>`.
- The command reads reports previously written by `PaperStrategyRiskAuditLog.read(...)`, preserves append order, and prints status counts, latest append-order status, latest failed/incomplete audit-check names, and per-check status summaries.
- Empty logs produce an `empty_audit_history` report with zero counts and no latest status. Non-empty logs report one of `latest_audit_ready`, `latest_insufficient_evidence`, or `latest_blocked_by_risk` from the latest appended report.
- This is paper-only/report-only/read-only local observability over already-written audit evidence. It does not fetch data, authenticate, handle wallets or private keys, read account data, use account/order APIs, place/sign/submit/cancel orders, rank investments, recommend trades, provide trade instruction, provide financial advice, tune strategy behavior, alter strategy-cycle decisions, or write new audit artifacts.

## Strategy Recommendation History Summary v0

- Summarize an explicitly selected local recommendation bundle JSONL artifact with `polymarket-alpha-lab strategy-recommendation-history --recommendation-log <path>`.
- The command reads bundle reports with `read_paper_strategy_recommendation_bundle_log(...)`, passes each bundle's nested `recommendation_report` into the readonly history reducer, and prints source recommendation counts plus the latest bundle's selected count and selected notional.
- Empty logs produce a zero-count summary. The command does not construct clients, fetch data, execute paper trades, append to logs, write artifacts, tune strategy behavior, alter strategy-cycle decisions, rank investments, provide trade instruction, or provide financial advice.

## Recommendation Reducer CLI Status

The current paper recommendation reducer CLI surfaces are available local report
commands:

- Available CLI: `polymarket-alpha-lab paper-probability-side-edge-report --input <path>`.
- Available CLI: `polymarket-alpha-lab paper-recommendation-queue-report --input <path> [--persist]`.
- Available CLI: `polymarket-alpha-lab paper-recommendation-risk-budget-report --input <path> [--nav-notional <amount>] [--persist]`.
- Available CLI: `polymarket-alpha-lab paper-recommendation-reason-trend --recommendation-log <path> [--persist]`.

`--persist` is default-off, env-driven, local Supabase/Postgres report-only DB
persistence for the queue, risk-budget, and reason-trend report commands. It is
not live trading and does not create clients, read accounts or wallets, sign or
submit orders, approve trades, size exchange orders, or mutate exchange state.
The side-edge report command is print-only and does not expose `--persist`.

`paper_capital_cost` is an available module-local reducer for explicit paper
capital-cost estimates and has no standalone CLI in this phase.
`paper_capital_cost_side_edge_adapter` is also available as a module-local
adapter that converts capital-cost-aware rows into canonical side-edge inputs
with computed `capital_cost_per_share`; it has no standalone CLI in this phase.
Planned-only reducer families remain docs/planning names until implemented: the
generic `paper_recommendation_queue`. The available queue CLI uses
`paper_probability_recommendation_queue`.

## Strategy Evidence Snapshot v0

- Print a local evidence snapshot with `polymarket-alpha-lab strategy-evidence --cycle-log <path> --trade-log <path> --nav-log <path> [--outcome-log <path>] [--strategy-audit-log <path>]`.
- The command reads caller-supplied local paper logs only, builds existing local summaries for performance, NAV risk, paper-trade cost audit, optional outcomes, and optional Strategy Risk Audit history, then prints a paper-only/report-only/read-only evidence snapshot.
- Snapshot statuses describe local evidence state only: `no_local_evidence`, `local_evidence_gaps`, `local_risk_flags`, and `local_evidence_observed`.
- `no_local_evidence` means the supplied local logs contain no observed cycle, paper-trade, NAV, outcome, or audit-history evidence. `local_evidence_gaps` means at least one local evidence family is absent or thin. `local_risk_flags` means local evidence exists and at least one descriptive risk flag is present. `local_evidence_observed` means the configured local evidence families are present and no configured descriptive risk flag is present.
- Optional outcomes and Strategy Risk Audit history are treated as local inputs only; absent optional inputs are surfaced as local gaps rather than filled from external data.
- Boundary: read-only local evidence observability. The command does not write, append, repair, or mutate logs; construct clients; fetch data; authenticate; read accounts; touch wallets or private keys; place, sign, submit, or cancel orders; interact with live trading surfaces; tune strategy behavior; or alter strategy-cycle decisions.

## Local Observability Trends v0

- Print local trend summaries with `polymarket-alpha-lab observability-trends --cycle-log <path> --trade-log <path> --nav-log <path> [--outcome-log <path>] [--strategy-audit-log <path>] [--outcome-stale-after-seconds <seconds>] [--persist]`.
- The command reads caller-supplied local JSONL logs only. It builds four existing read-only trend reports: strategy evidence trend, outcome freshness, NAV risk trend, and paper trade cost trend.
- Default is no DB write. `--persist` is the only persistence opt-in; when present, the CLI reads local observability DB environment config and, only if enabled, inserts the generated paper-only/report-only/readonly row. There are no DSN CLI flags.
- Required cycle, trade, and NAV histories use source/append-order prefixes from their typed readers as the only local trend ordering authority; the command does not timestamp-sort prefix inputs, including the NAV inputs used for the NAV risk trend. Optional outcome and Strategy Risk Audit logs are local evidence inputs only and are never refreshed from external services.
- Empty required logs produce deterministic empty trend states where supported by the child reducers. Invalid or missing local logs fail the command without constructing a client.
- Boundary: paper-only/report-only/read-only local observability. The command does not write, append, repair, or mutate local logs or artifacts; construct exchange/API clients; fetch data; authenticate; read accounts; touch wallets or private keys; place, sign, submit, or cancel orders; interact with live trading surfaces; rank investments; recommend trades; provide trade instruction; provide financial advice; tune strategy behavior; or alter strategy-cycle decisions.

## Paper Trade Cost Audit v0

- Build a paper-only/report-only/readonly cost audit from paper trade history with `build_paper_trade_cost_audit_report(...)` or `polymarket-alpha-lab cost-audit --trade-log <path>`.
- The CLI cost-audit path reads explicit JSONL compatibility inputs restored by `PaperTradeJournal.read(...)` by default, or typed paper-trade records from local Supabase/Postgres `paper_trade_journal_records` when the local paper trade journal DB env is enabled. It measures filled size, requested size, fill rate, theoretical edge, cost-adjusted edge, per-share edge cost drag, filled-size-weighted total cost drag, research slippage, fill slippage, partial fills, and negative cost-adjusted edge counts. The durable paper trade record write path for `strategy-cycle --paper-execute` requires the local paper trade journal DB env; `load_paper_trade_journal_db_cost_audit_report(...)` is the read-only local Supabase/Postgres helper for building the same paper cost audit from `paper_trade_journal_records`.
- Optional local Supabase/Postgres persistence for `PaperTradeCostAuditReport` snapshots is available as a DB foundation only. It is default-off, env-driven, has no DSN CLI flags, stores canonical `payload_json` plus cost evidence scalars, and preserves `paper_only`, `report_only`, and `readonly` flags. See `docs/paper-trade-cost-audit-db-persistence.md`.
- It is local observability over already-written paper records. It does not fetch market/account/order data, authenticate, handle wallets or private keys, place/sign/submit/cancel orders, rank investments, recommend trades, provide trade instruction, provide financial advice, tune strategy behavior, alter strategy-cycle decisions, or change paper execution/NAV behavior.
- Boundary shorthand: no fetch, no auth, no wallet, no order, no rank, no recommend, no trade instruction, no financial advice.

## Recommended Direction

The strongest first product is a market-quality and edge-scanning system:

1. Use official Polymarket APIs for market, event, order book, historical price, and trade data.
2. Rank markets by tradability, liquidity, rule clarity, activity, and time structure.
3. Start with measurable edges: spread quality, liquidity rewards, multi-outcome pricing inconsistencies, related-market constraints, and post-fill drift.
4. Validate everything through paper trading before execution automation is considered.

## Level 0 Usage

Start with the [Linux local setup guide](docs/development/linux-local-setup.md) to install from `uv.lock` and verify the editable package, CLI entrypoints, tests, and compilation. Onboarding uses only CLI help and `report-discovery`; it does not require market-data access or a database.

### Legacy Scanner Example

The existing scanner example below is outside the offline onboarding flow:

```bash
.venv/bin/polymarket-alpha-lab scan --limit 25 --output artifacts/market-scores.json
```

The scan uses public Polymarket market-data endpoints only. It does not authenticate, handle private keys, place orders, or trade. Raw API payloads are archived under `data/raw/`, and ranked candidate output is written to `artifacts/market-scores.json`; both directories are ignored by git. These file outputs are legacy surfaces and must not be extended as durable persistence substitutes.

## Level 1A Status

Level 1A adds research packets, bid/ask order-book-walk paper-fill simulation, and paper-trade records. Strategy-cycle paper execution must persist records to local Supabase/Postgres through `paper_trade_journal_records`; `--paper-execute` requires the local paper trade journal DB env and does not accept a JSONL journal as the durable write path. `PaperTradeJournal` JSONL is the legacy compatibility/export/replay path. The one-shot `portfolio-nav` CLI, continuous-run NAV source handling, `check-outcomes` source handling, and `cost-audit` source handling can use `paper_trade_journal_records` as their paper-trade source when the local paper trade journal DB env is enabled. Continuous run as a whole is not fully DB-backed; this migration covers only its NAV paper-trade source handling. JSONL remains the current input for history/performance summary, strategy audit, and observability/trend commands until separate read-source migrations land. It remains paper-only: no account authentication, no private-key handling, no order placement, no order cancellation, no user WebSocket, no heartbeat, no live trading, and no compliance/legal/geographic-access analysis.

## Level 1A Python API

Node 3 is exposed through Python APIs rather than new CLI commands:

- Build research packets with `build_research_packet(...)`, which returns `ResearchPacket`.
- Simulate bid/ask paper fills with `PaperOrder(...)` and `simulate_order_book_fill(...)`, which returns `PaperFill`.
- Build paper trade records with `PaperTradeRecord.from_packet_and_fill(...)`. For one-shot `strategy-cycle --paper-execute`, persist records to local Supabase/Postgres by enabling `POLYMARKET_ALPHA_LAB_PAPER_TRADE_JOURNAL_DB_ENABLED=true` plus a local `POLYMARKET_ALPHA_LAB_PAPER_TRADE_JOURNAL_DB_DSN`; `--paper-journal <path>` is rejected with `--paper-execute` because JSONL is not a durable paper-execution write path. `PaperTradeJournal` JSONL remains a legacy compatibility/export/replay input for consumers that have not been migrated to DB-backed paper-trade reads, such as history, strategy audit, and observability trends. The one-shot `portfolio-nav` CLI, continuous-run NAV source handling, `check-outcomes` source handling, and `cost-audit` source handling can read `paper_trade_journal_records` when that same local DB env is enabled.

## Paper Research Packet Operator Commands

Current paper research packet surfaces are Phase 1 paper-only, report-only, and readonly. They do not authenticate, handle wallets or private keys, read accounts, place/sign/submit/cancel orders, trade live, provide trade instructions, or provide financial advice. DB-backed commands read env-driven local Supabase/Postgres config; there are no DSN CLI flags.

Generate a packet from the latest persisted strategy candidate research queue report:

```bash
.venv/bin/polymarket-alpha-lab paper-research-packet --limit 100
```

- Required source env: `POLYMARKET_ALPHA_LAB_STRATEGY_CANDIDATE_RESEARCH_QUEUE_DB_ENABLED=true` and `POLYMARKET_ALPHA_LAB_STRATEGY_CANDIDATE_RESEARCH_QUEUE_DB_DSN`; optional table override: `POLYMARKET_ALPHA_LAB_STRATEGY_CANDIDATE_RESEARCH_QUEUE_DB_TABLE`.
- Useful filters: `--source-config-version <version>`, `--action-status research_ready|watch|blocked`, `--research-status ready|watch|blocked`, `--packet-config-version <version>`, `--max-packet-rows <count>`, and `--min-score <decimal>`.
- Default behavior prints the generated packet summary and top packet only. `--persist` additionally requires `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_DB_ENABLED=true` and `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_DB_DSN`; optional table override: `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_DB_TABLE`.

Read persisted packet history:

```bash
.venv/bin/polymarket-alpha-lab paper-research-packet-db-history --limit 100
```

- Required env: `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_DB_ENABLED=true` and `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_DB_DSN`; optional table override: `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_DB_TABLE`.
- The command prints report count, first/latest report times, duplicate timestamp count, latest packet counts, and the latest top packet.

Inspect the latest persisted packet's quality:

```bash
.venv/bin/polymarket-alpha-lab paper-research-packet-quality
```

- Required env is the same persisted packet DB config: `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_DB_ENABLED=true` and `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_DB_DSN`; optional table override: `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_DB_TABLE`.
- The command intentionally reads only the latest persisted packet report and builds one `PaperResearchPacketQualityReport`. It prints `quality_status`, source freshness, included/skipped shares, check statuses, and top reason codes.
- There is no `--limit` for this command because the quality reducer evaluates one source packet report at a time.
- `--persist` additionally requires `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_QUALITY_DB_ENABLED=true` and `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_QUALITY_DB_DSN`; optional table override: `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_QUALITY_DB_TABLE`. When enabled, the derived quality report is appended to the quality reports table after it is built.

Packet quality persistence also ships as Python/DB-API infrastructure. Use `build_paper_research_packet_quality_report(packet_report, config=..., generated_at=...)` to create the report, `paper_research_packet_quality_report_to_db_row(...)` for canonical payload/hash encoding, and `insert_paper_research_packet_quality_report(...)` / `load_paper_research_packet_quality_reports(...)` for DB-API persistence. The optional quality DB env config uses `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_QUALITY_DB_ENABLED`, `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_QUALITY_DB_DSN`, and `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_QUALITY_DB_TABLE`.

Read persisted packet-quality history:

```bash
.venv/bin/polymarket-alpha-lab paper-research-packet-quality-db-history --limit 100
```

- Required env is the quality DB config: `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_QUALITY_DB_ENABLED=true` and `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_QUALITY_DB_DSN`; optional table override: `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_QUALITY_DB_TABLE`.
- The command prints aggregate quality history: history status, source report count, first/latest source timestamps, latest quality status, source age, included/skipped shares, status rows, duplicate timestamp count, recurring reason rows, and reason codes.

Quality history is also available through the pure reducer `build_paper_research_packet_quality_history_report(...)` and the DB-API helper `load_paper_research_packet_quality_history_report(...)`. Pure trend reducers `build_paper_research_packet_quality_history_trend_report(...)` and `build_paper_research_packet_quality_history_trend_gate_report(...)` summarize caller-supplied quality-history reports into frozen paper-only/report-only/readonly trend and gate evidence. They do not read files, write files, read env, access Supabase/Postgres, fetch network data, authenticate, handle wallets/private keys, place/sign/submit/cancel orders, rank investments, or provide financial advice.

Run the persisted packet operator flow:

```bash
.venv/bin/polymarket-alpha-lab paper-research-packet-operator-flow --limit 100 --quality-history-limit 100
```

- Required env combines the three configs above: strategy candidate research queue DB, paper research packet DB, and paper research packet quality DB. All DSN/table settings remain env-driven; the command does not expose DSN or table CLI flags.
- The flow generates and persists a paper research packet, builds and persists the latest packet-quality report, then reads aggregate packet-quality history. It prints one combined operator summary plus the existing packet, quality, and quality-history summaries.
- This remains paper-only/report-only operator evidence. It does not authenticate, handle wallets or private keys, place/sign/submit/cancel orders, trade live, or provide trade instructions or financial advice.

Operator-flow report persistence is optional, default-disabled, and env-driven. Set
`POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_OPERATOR_FLOW_DB_ENABLED=true`
and `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_OPERATOR_FLOW_DB_DSN` to persist
the final operator-flow report after packet, quality, and quality-history steps
complete. `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_OPERATOR_FLOW_DB_TABLE`
can override the default table. There are still no DSN/table CLI flags, and the
command's summary stdout line is unchanged.

Operator-flow DB history is read-only and uses the same env-only operator-flow DB configuration as persistence:

```bash
POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_OPERATOR_FLOW_DB_ENABLED=true \
POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_OPERATOR_FLOW_DB_DSN=postgresql://... \
.venv/bin/polymarket-alpha-lab paper-research-packet-operator-flow-db-history --limit 25
```

The command prints persisted operator-flow stability signals such as pass/watch/blocked counts, duplicate report timestamps, latest consecutive status streaks, latest reason codes, and threshold reason codes. It does not accept DSN/table/persist flags and does not perform live trading or DB writes.

Operator-flow DB history gate is read-only and uses the same env-only operator-flow DB configuration as operator-flow DB history:

```bash
POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_OPERATOR_FLOW_DB_ENABLED=true \
POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_OPERATOR_FLOW_DB_DSN=postgresql://... \
.venv/bin/polymarket-alpha-lab paper-research-packet-operator-flow-db-history-gate --limit 25
```

The command prints aggregate gate signals such as `gate_status`, `recommended_next_step`, source report count/history status, latest operator-flow status, duplicate timestamp count, latest source age, reason-code counts, and reason codes.

The gate is a paper-only/read-only decision-support signal. It does not place orders, sign messages, read wallets/accounts, or mutate exchange state. Only a `pass` gate should be treated by downstream paper automation as eligible to advance.

Operator scope details: [Paper Autonomous Screening Decision Support Gate](docs/paper-autonomous-screening-decision-support-gate.md).

It does not accept DSN/table/persist flags; DB access remains env-only through `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_OPERATOR_FLOW_DB_ENABLED`, `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_OPERATOR_FLOW_DB_DSN`, and optional `POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_OPERATOR_FLOW_DB_TABLE`.

Paper Autonomous Screening Decision Support Gate combines the operator-flow DB
history gate and action-gated queue decision-support DB into a final paper-only
read-only screening signal:

```bash
POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_OPERATOR_FLOW_DB_ENABLED=true \
POLYMARKET_ALPHA_LAB_ACTION_GATED_QUEUE_DECISION_SUPPORT_DB_ENABLED=true \
.venv/bin/polymarket-alpha-lab paper-autonomous-screening-decision-support-gate --limit 25
```

The operator-flow DB and action-gated queue decision-support DB are required.
The rank-stability DB is optional; when it is enabled, the default single
connection read helper expects it to use the same DSN as the required upstream
DBs. The command is env-only and accepts only `--limit`; it does not accept
DSN/table/persist flags and does not write reports.

To produce the persisted final screening gate consumed by the allocation
proposal stage, use the sibling env-only producer command:

```bash
POLYMARKET_ALPHA_LAB_PAPER_RESEARCH_PACKET_OPERATOR_FLOW_DB_ENABLED=true \
POLYMARKET_ALPHA_LAB_ACTION_GATED_QUEUE_DECISION_SUPPORT_DB_ENABLED=true \
POLYMARKET_ALPHA_LAB_PAPER_AUTONOMOUS_SCREENING_DECISION_SUPPORT_GATE_DB_ENABLED=true \
.venv/bin/polymarket-alpha-lab paper-autonomous-screening-decision-support-gate-persist --limit 25
```

The producer first builds the same paper-only/report-only/readonly screening
gate report, then writes that final report to the autonomous screening gate DB.
It remains env-only, accepts only `--limit`, prints aggregate status plus
`persisted=True/False`, does not create live instructions, and does not mutate
exchange state.

Paper Probability Selection Summary History:

```bash
.venv/bin/polymarket-alpha-lab paper-probability-selection-summary-history --limit 25
```

The command is env-only and reads source selection-summary reports from the DB
configured by `POLYMARKET_ALPHA_LAB_PAPER_PROBABILITY_SELECTION_SUMMARY_DB_*`.
It accepts only `--limit` and optional default-off `--persist`; it does not
accept DSN/table CLI flags.

By default it builds one paper-only/report-only/readonly aggregate history
report from the latest source selection-summary reports and writes nothing. The
source reports are read newest-first from the source DB window and then reduced
chronologically inside the history reducer.

With `--persist`, the command writes only the derived history report through the
separate history DB configured by
`POLYMARKET_ALPHA_LAB_PAPER_PROBABILITY_SELECTION_SUMMARY_HISTORY_DB_*`, then
prints `persisted=True/False`.

It prints aggregate history fields at a high level: source report count,
first/latest source timestamps and source age, latest and aggregate queue
selection counts, selected-share summaries, distinct config versions, history
status, recommended next step, and reason-code summaries.

Boundary: no live trading, no auth, no wallet handling, no account reads, no
private-key handling, no order construction/signing/submission/cancellation, no
DSN/table flags, and no exchange mutation.

Paper Probability Selection Summary History Trend Gate:

```bash
.venv/bin/polymarket-alpha-lab paper-probability-selection-summary-history-trend-gate --limit 25
```

The command is env-only, read-only, paper-only/report-only/readonly, and uses
the existing local Supabase/Postgres probability selection summary history DB
configuration:
`POLYMARKET_ALPHA_LAB_PAPER_PROBABILITY_SELECTION_SUMMARY_HISTORY_DB_*`. It
accepts only `--limit`; it does not accept persist, DSN, table, file, wallet,
auth, order, live, execution, account, private-key, or fast-mode flags.

It reads persisted history reports newest-first from the local DB, reduces them
chronologically into the existing probability selection summary history trend,
then converts that trend into a pure gate report with `pass`, `watch`, or
`blocked` status. Stable selection trends pass. Insufficient, stale, or
deteriorating trends block. Repeated watch streaks, thin source history,
recurring source reasons, and aged trend evidence watch unless a blocking
condition is present.

The gate CLI writes nothing and adds no durable table. Output is aggregate-only:
gate status, recommended next step, source history count, trend status,
latest-history status/streak, selected-share metrics, stale/thin counts, and
sanitized reason-code summaries. It must not print source rows, payloads,
report hashes, DB internals, market slugs, questions, condition ids, wallets,
accounts, auth material, private keys, or order-like data.

The pure gate report can be supplied as optional
`selection_summary_trend_gate` evidence to the paper autonomous readiness digest
reducer. That evidence remains paper observability only: it is not permission to
trade, not financial advice, not investment ranking, not an order instruction,
not execution authorization, and not an approval workflow.

Probability Selection Scorer Agreement:

```bash
.venv/bin/polymarket-alpha-lab probability-selection-scorer-agreement --limit 25
```

The command is env-only and reads the latest local Supabase/Postgres paper
probability selection summary reports plus autonomous market scorer reports. It
accepts only `--limit`; it does not accept persist, DSN, table, file, wallet,
auth, order, or live flags.

Required source env:
`POLYMARKET_ALPHA_LAB_PAPER_PROBABILITY_SELECTION_SUMMARY_DB_ENABLED=true`,
`POLYMARKET_ALPHA_LAB_PAPER_PROBABILITY_SELECTION_SUMMARY_DB_DSN`,
`POLYMARKET_ALPHA_LAB_AUTONOMOUS_MARKET_SCORER_DB_ENABLED=true`, and
`POLYMARKET_ALPHA_LAB_AUTONOMOUS_MARKET_SCORER_DB_DSN`. Optional source table
overrides use `POLYMARKET_ALPHA_LAB_PAPER_PROBABILITY_SELECTION_SUMMARY_DB_TABLE`
and `POLYMARKET_ALPHA_LAB_AUTONOMOUS_MARKET_SCORER_DB_TABLE`.

By default it builds and prints one aggregate paper-only/report-only/readonly
agreement report and writes nothing. Optional local Supabase persistence is
enabled only by `POLYMARKET_ALPHA_LAB_PROBABILITY_SELECTION_SCORER_AGREEMENT_DB_ENABLED=true`
plus `POLYMARKET_ALPHA_LAB_PROBABILITY_SELECTION_SCORER_AGREEMENT_DB_DSN`; the
optional table override is
`POLYMARKET_ALPHA_LAB_PROBABILITY_SELECTION_SCORER_AGREEMENT_DB_TABLE`.

The persisted agreement report is aggregate-only: generated/source timestamps,
counts, statuses, recommended next step, reason codes, canonical payload/hash,
and hard safety flags. It does not store market slugs, questions, condition ids,
orders, wallets, auth data, private keys, or source row details.

Probability Selection Scorer Agreement Trend:

```bash
.venv/bin/polymarket-alpha-lab probability-selection-scorer-agreement-trend --limit 25
```

The command is env-only, read-only, paper-only/report-only/readonly, and reads
persisted aggregate agreement reports from the local Supabase/Postgres DB
configured by
`POLYMARKET_ALPHA_LAB_PROBABILITY_SELECTION_SCORER_AGREEMENT_DB_*`. It accepts
only `--limit`; it does not accept persist, DSN, table, file, wallet, auth,
order, or live flags.

It reads the selected agreement reports newest-first from DB, reduces them
chronologically in memory, prints aggregate trend fields and sanitized
reason-code summaries, and writes nothing. It does not fetch data, rank markets,
recommend trades, handle wallets or private keys, place/sign/submit/cancel
orders, or mutate exchange state.

Probability Selection Scorer Agreement Trend Gate:

The pure reducer callable
`build_probability_selection_scorer_agreement_trend_gate_report` in the
`probability_selection_scorer_agreement_trend_gate` module converts an
already-built `ProbabilitySelectionScorerAgreementTrendReport` into a
paper-only/report-only/readonly gate report with `pass`, `watch`, or `blocked`
status. It has no CLI, runner, loader, DB connection, environment-variable read,
Supabase/Postgres access, SQLite, JSONL durable history, Redis, Mongo,
SQLAlchemy, hosted DB assumption, generic durable store, file-backed cache,
insert/update/delete/DDL/sink path, trend-gate persistence, DSN/table/file
inputs, live trading, auth, private keys, wallets, accounts, order
construction/signing/submission, cancellation, replacement, or exchange
mutation.

Stable aligned agreement trends pass. Insufficient history, any latest
`gate_blocked` agreement status, and repeated scorer-gate blockers block. Stale
trend evidence, latest non-aligned non-blocking statuses such as low overlap,
missing inputs, or insufficient identifiers, and repeated source reason codes
watch unless a blocking condition also applies.

The gate report is only paper observability evidence. It is not permission to
trade, financial advice, investment ranking, order instruction, execution
authorization, or an approval workflow. It is available as optional
`agreement_trend_gate` evidence for the paper autonomous readiness digest pure
reducer and dependency-injected loader; it is not wired into the readiness gate
or strategy policy.

Probability Selection Scorer Agreement Trend Gate CLI/readback:

```bash
polymarket-alpha-lab probability-selection-scorer-agreement-trend-gate --limit 25
```

The command is env-only, read-only, paper-only/report-only/readonly, and
composes existing local pieces only: the existing local Supabase/Postgres
persisted aggregate agreement report loader, the chronological agreement trend
reducer, the pure agreement trend-gate reducer, and aggregate-only stdout.

It reads only persisted aggregate reports from the existing local
Supabase/Postgres table
`probability_selection_scorer_agreement_reports` through the existing
`POLYMARKET_ALPHA_LAB_PROBABILITY_SELECTION_SCORER_AGREEMENT_DB_*`
environment/config boundary and loader. It accepts only `--limit`; it does not
accept DSN, table, persist, file, live, auth, private-key, wallet, account,
order, signing, submission, cancellation, replacement, or exchange-mutation
flags.

The command writes nothing, adds no durable table, and does not persist trend or
gate reports. There is no new durable gate table, JSONL/SQLite/file durable
store, Redis, Mongo, SQLAlchemy, generic durable-store abstraction, hosted DB
assumption, or file-backed cache. All durable data for this node remains in
local Supabase/Postgres only.

The gate CLI output is aggregate-only. It prints gate status, recommended next
step, source report count, trend status, latest agreement status/streak,
timestamp/span fields, aggregate status counts, aggregate averages, and
sanitized reason-code summaries. It must not print source rows, payloads,
report hashes, DB internals, market slugs, questions, condition ids, wallets,
accounts, auth material, private keys, or order-like data.

This readback command is not permission to trade, not financial advice, not an
investment ranking, not a trade recommendation, not an order instruction, not
execution authorization, and not an approval workflow. Its report shape is
accepted as optional `agreement_trend_gate` evidence by the paper autonomous
readiness digest pure reducer, dependency-injected loader, and CLI readback. It
is not wired into the readiness gate or strategy policy.

Paper Autonomous Allocation Proposal combines a passed autonomous screening
gate, latest action-gated queue decision-support reports, and source queue
reports into a paper-only/report-only/read-only allocation proposal:

```bash
.venv/bin/polymarket-alpha-lab paper-autonomous-allocation-proposal --limit 25
```

Operator scope details: [Paper Autonomous Allocation Proposal](docs/paper-autonomous-allocation-proposal.md).

The command is env-only and reads already-persisted upstream reports. It
accepts only `--limit`; it does not accept DSN/table/persist flags, does not
write reports, and stays no-write. It is not a live-execution signal, not
execution authorization, not order instruction, and not financial advice.

To persist the final paper allocation proposal for later local review, use the
sibling env-only producer command:

```bash
POLYMARKET_ALPHA_LAB_PAPER_AUTONOMOUS_ALLOCATION_PROPOSAL_DB_ENABLED=true \
.venv/bin/polymarket-alpha-lab paper-autonomous-allocation-proposal-persist --limit 25
```

The producer builds the same paper-only/report-only/readonly proposal, writes
only that final proposal report to the autonomous allocation proposal DB, prints
the usual aggregate summary plus `persisted=True/False`.
It does not create live instructions or mutate exchange state.

DB History Readback:

```bash
.venv/bin/polymarket-alpha-lab paper-autonomous-allocation-proposal-db-history --limit 25
```

The DB history readback is env-only, read-only, paper-only/report-only/readonly,
and accepts only `--limit`. It reads the final allocation proposal DB configured
by env and reads only persisted final allocation proposal reports.

It does not write reports and does not read upstream tables.
It does not place orders, approve execution, read accounts, or mutate exchange state.
It prints aggregate history status, proposal-status counts, latest aggregate
allocation counts, duplicate timestamp count, and reason-code summaries.

DB History Gate:

```bash
.venv/bin/polymarket-alpha-lab paper-autonomous-allocation-proposal-db-history-gate --limit 25
```

The DB history gate is env-only, read-only, paper-only/report-only/readonly,
and no-write. It accepts only `--limit`. It reads the final allocation proposal
DB configured by env and reads only persisted final allocation proposal reports
through the DB history readback.

It does not write reports and does not read upstream screening/queue tables.
It does not place orders, approve execution, read accounts, or mutate exchange state.
The gate status is not permission to trade.
It is not financial advice, not investment ranking, and not an approval workflow.
It prints aggregate gate status, recommended next step, source history status,
latest aggregate allocation counts, duplicate timestamp count, latest source
age, and reason-code counts.

DB History Metrics:

```bash
.venv/bin/polymarket-alpha-lab paper-autonomous-allocation-proposal-db-history-metrics --limit 25
```

The DB history metrics command is env-only, read-only,
paper-only/report-only/readonly, and no-write. It accepts only `--limit`. It
reads the final allocation proposal DB configured by env and reads only
persisted final allocation proposal reports.

It computes aggregate paper allocation risk/performance metrics: budget
utilization, fill ratio, concentration, churn, cap reasons, edge coverage, and
expected edge notional where edge data exists.

The v0 aggregates across all persisted proposal statuses and config versions
selected by the table and `--limit`.

It does not write reports and does not read upstream screening/queue tables.
It does not place orders, approve execution, read accounts, or mutate exchange state.
It is not financial advice, not investment ranking, not automatic live investing,
not order instruction, and not execution authorization.

DB History Metrics Evaluation:

```bash
.venv/bin/polymarket-alpha-lab paper-autonomous-allocation-proposal-db-history-metrics-evaluation --limit 25
```

The DB history metrics evaluation command is env-only, read-only,
paper-only/report-only/readonly, and no-write. It accepts only `--limit`. It
reads the final allocation proposal DB configured by env, reads only persisted
final allocation proposal reports, and builds metrics from them before
evaluating aggregate allocation diagnostics.

In v0 it evaluates persisted metrics built from the same DB-selected proposal
history selected by the table and `--limit`.

It does not write reports and does not read upstream screening/queue tables.
It does not place orders, approve execution, read accounts, or mutate exchange state.
It is not financial advice, not investment ranking, not automatic live investing,
not order instruction, and not execution authorization.

DB History Health:

```bash
.venv/bin/polymarket-alpha-lab paper-autonomous-allocation-proposal-db-history-health --limit 25
```

The DB history health command is env-only, read-only by default, and
paper-only/report-only/readonly. It accepts only `--limit` and optional
default-off `--persist`; it does not accept DSN/table flags. It reads the final
allocation proposal DB configured by env and reads only persisted final
allocation proposal history through DB history readback.

Without `--persist`, it does not write reports and does not read upstream
screening/queue tables. With `--persist`, it requires the separate DB-history
health DB environment config, stores only the already-built health report, and
prints `persisted=True/False`:

```bash
.venv/bin/polymarket-alpha-lab paper-autonomous-allocation-proposal-db-history-health --limit 25 --persist
```

It does not place orders, approve execution, read accounts, or mutate exchange state.
The health status is not permission to trade.
It is not financial advice, not investment ranking, and not an approval workflow.
It prints aggregate health status, source history status, latest aggregate
allocation counts, duplicate timestamp count, and reason-code counts.

DB History Health Trend:

```bash
.venv/bin/polymarket-alpha-lab paper-autonomous-allocation-proposal-db-history-health-trend --limit 25
```

The DB history health trend command is env-only, read-only,
paper-only/report-only/readonly, and no-write. It accepts only `--limit`. It
reads the separate DB-history health DB configured by env and reads only
persisted DB-history health reports before building one read-only trend report
over that caller-selected window.

When no persisted health reports are available, the loader builds an empty
trend report. Boundary health snapshots are produced by the health command,
then optionally persisted with `--persist`.

It does not write reports and does not read upstream screening/queue tables.
It does not place orders, approve execution, read accounts, or mutate exchange state.
The trend status is not permission to trade.
It is not financial advice, not investment ranking, and not an approval workflow.
It prints aggregate health-status trend counts, latest health status, delta
summaries, duplicate timestamp count, streak counts, and latest reason-code
counts.

DB History Health Trend Gate:

Command: `.venv/bin/polymarket-alpha-lab paper-autonomous-allocation-proposal-db-history-health-trend-gate --limit 25`

The DB history health trend gate command is env-only, read-only,
paper-only/report-only/readonly, and no-write. It accepts only `--limit`.
It reads the separate DB-history health DB configured by env, derives the DB
history health trend from persisted DB-history health reports, and prints one
aggregate gate report.

It does not write reports and does not read upstream screening/queue tables.
It does not place orders, approve execution, read accounts, or mutate exchange state.
The health-trend gate status is not permission to trade.
It is not financial advice, not investment ranking, and not an approval workflow.
It prints aggregate health-trend gate status, recommended next step, latest
health status, sample counts, duplicate timestamp count, latest streak counts,
health-delta signals, and reason-code counts.

Boundary: no live trading, no auth, no key handling, no wallet handling, no account handling, no account reads, no order construction, no order signing, no order submission, no order cancellation, no order replacement, no exchange mutation, no investment ranking, not automatic live investing, and not an approval workflow.

Paper Autonomous Investment Ledger turns persisted paper broker records into a paper-only/report-only/readonly investment ledger summary:

```bash
.venv/bin/polymarket-alpha-lab paper-autonomous-investment-ledger --limit 25
```

Required paper broker DB env: `POLYMARKET_ALPHA_LAB_PAPER_BROKER_DB_ENABLED`,
`POLYMARKET_ALPHA_LAB_PAPER_BROKER_DB_DSN`, and optional
`POLYMARKET_ALPHA_LAB_PAPER_BROKER_DB_TABLE`. The command is env-only,
read-only, paper-only/report-only/readonly, and accepts only local report
filters plus `--limit`. Read-only boundary: the command reads already-persisted paper broker execution records only.

To persist the already-built ledger report for later local review, use explicit
default-off persistence:

```bash
.venv/bin/polymarket-alpha-lab paper-autonomous-investment-ledger --limit 25 --persist
```

Required paper autonomous investment ledger DB env:
`POLYMARKET_ALPHA_LAB_PAPER_AUTONOMOUS_INVESTMENT_LEDGER_DB_ENABLED`,
`POLYMARKET_ALPHA_LAB_PAPER_AUTONOMOUS_INVESTMENT_LEDGER_DB_DSN`, and optional
`POLYMARKET_ALPHA_LAB_PAPER_AUTONOMOUS_INVESTMENT_LEDGER_DB_TABLE`. Persistence
is env-only and stores only the already-built investment ledger report in the
paper autonomous investment ledger DB, then prints `persisted=True/False`.

Investment Ledger DB History Readback:

```bash
.venv/bin/polymarket-alpha-lab paper-autonomous-investment-ledger-db-history --limit 25
```

The DB history readback is env-only, read-only, paper-only/report-only/readonly,
and accepts only `--limit`, `--config-version`, and `--ledger-status`. It reads
the paper autonomous investment ledger DB configured by env and does not accept
DSN/table CLI flags, does not accept `--persist`, does not persist in db-history,
does not write reports, and does not read upstream paper broker tables.

Investment Ledger DB History Health:

```bash
.venv/bin/polymarket-alpha-lab paper-autonomous-investment-ledger-db-history-health --limit 25
```

The DB history health readback is env-only, read-only,
paper-only/report-only/readonly, and accepts only `--limit`. It reads already
persisted paper autonomous investment ledger DB-history reports from the paper
autonomous investment ledger DB configured by env, builds one health report, does
not accept DSN/table CLI flags, does not accept `--persist`, does not persist
health rows, does not write reports, and does not read upstream paper broker
tables.

Boundary: no live trading, no auth, no key handling, no wallet handling,
no account reads, no order construction, no order signing, no order submission,
no order cancellation, no exchange mutation, not financial advice,
not investment ranking, not order instruction, and not execution authorization.

Investment Ledger DB History Health Trend:

```bash
.venv/bin/polymarket-alpha-lab paper-autonomous-investment-ledger-db-history-health-trend --limit 25
```

Investment Ledger DB History Health Trend Gate:

```bash
.venv/bin/polymarket-alpha-lab paper-autonomous-investment-ledger-db-history-health-trend-gate --limit 25
```

The trend and trend-gate commands read persisted investment-ledger DB-history
health reports, not raw investment-ledger reports. Required DB-history health DB
env: `POLYMARKET_ALPHA_LAB_PAPER_AUTONOMOUS_INVESTMENT_LEDGER_DB_HISTORY_HEALTH_DB_ENABLED`,
`POLYMARKET_ALPHA_LAB_PAPER_AUTONOMOUS_INVESTMENT_LEDGER_DB_HISTORY_HEALTH_DB_DSN`,
and optional
`POLYMARKET_ALPHA_LAB_PAPER_AUTONOMOUS_INVESTMENT_LEDGER_DB_HISTORY_HEALTH_DB_TABLE`.
The default table is
`paper_autonomous_investment_ledger_db_history_health_reports`. Both commands
are env-only, read-only, paper-only/report-only/readonly, and accept only
`--limit`. They do not accept DSN/table CLI flags, `--persist`, live/auth/wallet
or order/trade/execution flags, and they do not write reports or authorize
trading.

Paper Autonomous Readiness Gate:

The paper autonomous readiness gate is a pure Python reducer that combines
required sources plus optional pure sources from already-built Phase 1 health
gates.

Required sources:

- screening decision-support gate DB-history health
- allocation proposal DB-history health trend gate
- investment-ledger DB-history health trend gate

Optional pure sources:

- strategy-cycle report history gate
- strategy-risk-audit history gate

Operator notes: `docs/paper-autonomous-readiness-gate.md`.

It emits a paper-only/report-only/readonly readiness report with `pass`,
`watch`, or `blocked` status for operator-facing paper review.

- does not alter strategy behavior
- does not trigger allocation
- does not stop execution paths
- does not promote a paper status into a trading decision

Boundary:

- does not connect to Supabase or Postgres
- does not read env
- does not expose CLI flags
- does not persist reports
- does not authorize trading
- does not alter strategy behavior
- does not trigger allocation
- does not stop execution paths
- does not handle private keys, wallets, accounts, orders, or trades
- is not financial advice
- is not investment ranking
- is not order instruction
- is not execution authorization

## Level 1B Node 1 Status

Level 1B Node 1 adds configurable paper-only risk gates and append-only rejected-candidate logs. It does not place orders, authenticate, handle private keys, cancel orders, open user WebSockets, run heartbeat logic, or create live-trading proposals.

Paper Autonomous Readiness Digest CLI/readback:

```bash
polymarket-alpha-lab paper-autonomous-readiness-digest --limit 25
```

The command is env-only, read-only, paper-only/report-only/readonly, and reads
local Supabase/Postgres readiness-gate history through the existing readiness
gate DB environment boundary. By default it accepts only `--limit` and writes
nothing.

Optional local Supabase/Postgres persistence stores only the final already-built
digest report:

```bash
polymarket-alpha-lab paper-autonomous-readiness-digest --limit 25 --persist
```

`--persist` reads only the readiness digest DB environment config:
`POLYMARKET_ALPHA_LAB_PAPER_AUTONOMOUS_READINESS_DIGEST_DB_ENABLED`,
`POLYMARKET_ALPHA_LAB_PAPER_AUTONOMOUS_READINESS_DIGEST_DB_DSN`, and optional
`POLYMARKET_ALPHA_LAB_PAPER_AUTONOMOUS_READINESS_DIGEST_DB_TABLE`. The default
table is `paper_autonomous_readiness_digest_reports`. There are no digest
DSN/table/file CLI flags; DB selection stays env-only and local
Supabase/Postgres-only. Details: `docs/paper-autonomous-readiness-digest-db-persistence.md`.

Boundary:

- does not accept DSN, table, file, auth, private-key, wallet, account, or order flags
- does not accept signing, submission, cancellation, replacement, or exchange-mutation flags

The readback builds a compact digest from the latest readiness-gate report plus
optional `agreement_trend_gate` evidence when that local evidence is available
through the readback path. The pure digest reducer also accepts optional
`selection_summary_trend_gate` evidence in canonical source order after
`agreement_trend_gate` and before `ledger` when a caller supplies an already
built local report. It prints digest status, recommended review action,
evidence statuses, and sanitized reason-code counts. With `--persist`, it also
prints `persisted=True` for a new digest row or `persisted=False` for an
idempotent duplicate insert after the current digest summary.

Without `--persist`, the command does not read the digest DB env config,
connect to the digest DB, insert a digest row, or print `persisted=`.
Persistence never writes trend-gate reports. All durable data for this readback
remains in local Supabase/Postgres only; there is no JSONL/SQLite/file durable
store, Redis, Mongo, SQLAlchemy, generic durable-store abstraction, hosted DB
assumption, or file-backed cache.

The persisted digest is paper observability history only. It is not trade
permission, not financial advice, not investment ranking, not an order
instruction, and not execution authorization.

Boundary:

- does not change readiness-gate policy
- does not alter strategy behavior
- does not trigger allocation
- does not stop execution paths
- does not rank investments
- does not provide trade instructions
- does not provide financial advice

## Level 1B Node 1 Python API

Node 1 is exposed through Python APIs:

- Configure entry gates with `RiskGateConfig(...)`.
- Evaluate packets with `evaluate_research_packet_risk(packet, config)`.
- Persist rejected candidates with `RejectedCandidateRecord.from_packet_and_decision(...)` and `RejectedCandidateLog(path).append(record)`.

## Level 1B Node 2 Status

Level 1B Node 2 adds a paper-only position ledger and executable NAV marks derived from accepted paper-trade journal records and supplied public order book snapshots. It does not fetch order books, place orders, authenticate, handle private keys, cancel orders, open user WebSockets, run heartbeat logic, reconcile exchange account positions, or create live-trading proposals.

## Level 1B Node 2 Python API

Node 2 is exposed through Python APIs:

- Build paper portfolios with `build_paper_portfolio(records, starting_cash=Decimal("10000"))`, which returns `PaperPortfolio`.
- Mark open positions with `mark_paper_nav(portfolio, books_by_token_id, marked_at=datetime.now(UTC))`, which returns `PaperNavSnapshot`.
- Persist executable NAV snapshots with `PaperNavLog(path).append(snapshot)`.

## Level 1B Node 3 Status

Level 1B Node 3 adds paper-only portfolio analytics, exposure concentration, liquidity-risk, report-only threshold breaches, and executable-NAV drawdown reports derived from paper portfolio and NAV artifacts. It does not fetch market, order-book, or account data, place or cancel orders, authenticate, handle private keys, open user WebSockets, run heartbeat logic, use a trading SDK, create trade proposals, reconcile exchange accounts, scrape websites, or perform compliance/legal/geographic analysis.

## Level 1B Node 3 Python API

Node 3 is exposed through Python APIs:

- Configure report thresholds with `PaperAnalyticsConfig(...)`.
- Build paper analytics reports with `build_paper_analytics_report(portfolio, snapshot, config=..., generated_at=...)`, which returns `PaperAnalyticsReport`.
- Build executable-NAV drawdown points with `build_paper_drawdown_points(snapshots)`.
- Persist report snapshots with `PaperAnalyticsLog(path).append(report)`.

## Level 1B Node 4 Status

Level 1B Node 4 adds paper-only analytics history validation over existing `PaperAnalyticsReport` values, including validation-gate summaries, trend extrema, and evidence-readiness status for later human review. Its `paper_review_ready` status means the history artifact is ready for manual review only; it is not a proposal-generation, promotion, or live-execution signal. It does not fetch historical market, order-book, or account data, use external loaders, scrape websites, authenticate, handle private keys, place or cancel orders, open user WebSockets, run heartbeat logic, use a trading SDK, create trade proposals, reconcile exchange accounts, or perform compliance/legal/geographic analysis.

## Level 1B Node 4 Python API

Node 4 is exposed through Python APIs:

- Configure history thresholds with `PaperAnalyticsHistoryConfig(...)`.
- Build paper analytics history reports with `build_paper_analytics_history_report(reports, config=..., generated_at=...)`, which returns `PaperAnalyticsHistoryReport`.
- Inspect validation rows with `PaperAnalyticsHistoryGateResult` and trend rows with `PaperAnalyticsHistoryTrend`.
- Persist history snapshots with `PaperAnalyticsHistoryLog(path).append(report)`.

## Level 1B Node 5 Status

Level 1B Node 5 adds paper-only forecast evidence reports over supplied `PaperForecastEvidenceObservation` values, including probability bucket quality, executable-edge gap and hit-rate checks, residual exposure checks, and append-only JSONL snapshots. Its `paper_review_ready` status means the evidence artifact is ready for manual review only; it is not a promotion, trade, or live-execution signal. It does not fetch market, order-book, price history, or account data, use external loaders, scrape websites, authenticate, handle private keys, place or cancel orders, open user WebSockets, run heartbeat logic, use a trading SDK, reconcile exchange accounts, or perform compliance/legal/geographic analysis.

## Level 1B Node 5 Python API

Node 5 is exposed through Python APIs:

- Configure evidence thresholds with `PaperForecastEvidenceConfig(...)`.
- Create evidence rows with `PaperForecastEvidenceObservation(...)`.
- Build paper forecast evidence reports with `build_paper_forecast_evidence_report(observations, config=..., generated_at=...)`, which returns `PaperForecastEvidenceReport`.
- Inspect evidence gates with `PaperForecastEvidenceGateResult` and probability buckets with `PaperForecastEvidenceBucket`.
- Persist forecast evidence snapshots with `PaperForecastEvidenceLog(path).append(report)`.

## Level 1B Node 6 Status

Level 1B Node 6 adds paper-only manual-review queues over supplied `PaperManualReviewCandidate` values, `MarketScore` rows, `PaperAnalyticsHistoryReport`, and `PaperForecastEvidenceReport`. Its `paper_review_ready` status means a queue item is ready for human inspection only; it is not proposal generation, strategy promotion, a trade instruction, an approval workflow, or a live-execution signal. It does not fetch market, order-book, price-history, outcome, or account data, read external history, scrape websites, authenticate, handle private keys, place or cancel orders, open user WebSockets, run heartbeat logic, use a trading SDK, reconcile exchange accounts, or perform compliance/legal/geographic analysis.

## Level 1B Node 6 Python API

Node 6 is exposed through Python APIs:

- Configure review thresholds and boundary text with `PaperManualReviewConfig(...)`.
- Create queue candidates with `PaperManualReviewCandidate(...)`.
- Build paper manual-review queues with `build_paper_manual_review_queue(candidates, market_scores=..., analytics_history=..., forecast_evidence=..., config=..., generated_at=...)`, which returns `PaperManualReviewQueue`.
- Inspect ranked rows with `PaperManualReviewQueueItem`.
- Persist manual-review snapshots with `PaperManualReviewLog(path).append(queue)`.

## Cost-Aware Event Strategy v0 Status

Cost-Aware Event Strategy v0 adds a paper-only and report-only event-contract evaluator over caller-supplied fair probability, confidence, YES/NO bid/ask, ask depth, spread, resolution risk, and cost assumptions. It treats Polymarket markets as event probability YES/NO outcome-token contracts and evaluates buy-side research against executable YES/NO ask prices, not midpoint, last price, displayed probability, or chart price. Supplied bids are preserved in the report for audit context, but entry-edge math uses asks.

The report models taker fees and explicit non-fee costs for slippage, funding, finalization, time, and risk before computing net paper edge. A selected side only means the supplied inputs create a paper-review candidate under the configured thresholds; it is not a trade instruction, investment ranking, recommendation, financial advice, or live-execution signal.

It does not fetch market data, read account data, authenticate, handle wallets, private keys, or credentials, use API clients, use browser automation, place, submit, sign, or cancel orders, rank investments, recommend trades, provide financial advice, or perform compliance/legal/geographic analysis.

Boundary shorthand: no fetch, no auth, no wallet, no order, no rank, no recommend, no financial advice.

## Cost-Aware Event Strategy v0 Python API

Cost-Aware Event Strategy v0 is exposed through Python APIs:

- Create paper market snapshots with `PaperCostAwareEventMarketSnapshot(...)`.
- Configure explicit research costs with `PaperCostAwareEventCostAssumptions(...)`.
- Configure paper-review thresholds with `PaperCostAwareEventStrategyConfig(...)`.
- Build cost-aware event strategy reports with `build_paper_cost_aware_event_strategy_report(...)`, which returns `PaperCostAwareEventStrategyReport`.
- Inspect side results and gates with `PaperCostAwareEventSideResult` and `PaperCostAwareEventStrategyGateResult`.
- Optionally append already-built reports with `PaperCostAwareEventStrategyLog(path).append(report)`; there is no market fetcher, account reader, order API, JSONL reader, loader, replay, or from-file API.

## Project Screening v0 Status

Project Screening v0 adds a paper-only and report-only screening layer over already-built `PaperCostAwareEventStrategyReport` values. It turns supplied cost-aware event strategy reports into a deterministic human research queue for review workflow ergonomics. Queue sequence and screening scores are research-triage artifacts only; they are not investment rankings, not trade recommendations, not trade instructions, not financial advice, not approval workflow outputs, and not live-execution signals.

It consumes only caller-supplied cost-aware reports. It does not fetch market data, read account data, authenticate, handle wallets, private keys, or credentials, use API clients, use browser automation, place, submit, sign, or cancel orders, rank investments, recommend trades, provide financial advice, or perform compliance/legal/geographic analysis.

Boundary shorthand: no fetch, no auth, no wallet, no order, no rank, no recommend, no trade, no financial advice.

## Project Screening v0 Python API

Project Screening v0 is exposed through Python APIs:

- Configure screening thresholds and weights with `PaperProjectScreeningConfig(config_version="project-screening-v1")`.
- Build project screening reports from already-built `PaperCostAwareEventStrategyReport` values with `build_paper_project_screening_report(reports, config=config, generated_at=datetime.now(UTC))`, which returns `PaperProjectScreeningReport`.
- Inspect source candidate details with `PaperProjectScreeningCandidate`, queue rows with `PaperProjectScreeningQueueItem`, and screening gates with `PaperProjectScreeningGateResult`.
- Optionally append already-built screening reports with `PaperProjectScreeningLog(path).append(report)`; the only persistence surface is append-only JSONL for already-built reports. There are no data-fetch, exchange-state read, JSONL-read, replay, external-load, capital-action, or exchange-action helpers.

## Forecast Provider v0 Status

Forecast Provider v0 adds a paper-only and report-only naive forecast primitive that produces the `fair_probability_yes` and `confidence` values the Cost-Aware Event Strategy consumes but no module previously produced. The naive v0 baseline uses the executable YES ask as the fair probability and buckets confidence from book depth and spread; the `basis` field names the model (`yes_ask_naive_v0`) so downstream audits know this is a placeholder, not a real forecast model.

The forecast is research-triage only; it is not a trade instruction, investment ranking, recommendation, financial advice, or live-execution signal.

It consumes only caller-supplied markets and order books. It does not fetch market data, read account data, authenticate, handle wallets, private keys, or credentials, use API clients, use browser automation, place, submit, sign, or cancel orders, rank investments, recommend trades, provide financial advice, or perform compliance/legal/geographic analysis.

Boundary shorthand: no fetch, no auth, no wallet, no order, no rank, no recommend, no financial advice.

## Forecast Provider v0 Python API

Forecast Provider v0 is exposed through Python APIs:

- Configure naive forecast thresholds with `PaperForecastConfig(config_version="naive-forecast-v1")`.
- Build naive forecasts from caller-supplied markets and YES/NO order books with `build_paper_naive_forecast(market, yes_book, no_book, config=config, generated_at=datetime.now(UTC))`, which returns `PaperForecast`.
- Inspect the fair probability, confidence, basis, and reason codes via `PaperForecast`.
- Optionally append already-built forecasts with `PaperForecastLog(path).append(forecast)`; there is no market fetcher, account reader, order API, JSONL reader, loader, replay, or from-file API.

## Book-Imbalance Forecast v0 Status

Book-Imbalance Forecast v0 adds a paper-only and report-only improved forecast primitive that derives `fair_probability_yes` from YES order-book depth imbalance rather than from the executable ask alone. It quantizes the YES bid-vs-ask size imbalance first, then derives a bounded `nudge` off the executable YES ask, so the screening research queue carries non-zero-edge candidates instead of the naive baseline (`yes_ask_naive_v0`) whose stored edge is exactly 0. The `basis` field names the model (`book_imbalance_v0`) so downstream audits know this is a book-imbalance heuristic, not a real forecast model.

The forecast is research-triage only; it is not a trade instruction, investment ranking, recommendation, financial advice, or live-execution signal. `paper_only is True` and `report_only is True` are hard-enforced on every report.

It consumes only caller-supplied markets and order books. It does not fetch market data, read account data, authenticate, handle wallets, private keys, or credentials, use API clients, use browser automation, place, submit, sign, or cancel orders, rank investments, recommend trades, provide financial advice, or perform compliance/legal/geographic analysis.

Boundary shorthand: no fetch, no auth, no wallet, no order, no rank, no recommend, no financial advice.

## Book-Imbalance Forecast v0 Python API

Book-Imbalance Forecast v0 is exposed through Python APIs:

- Configure the imbalance heuristic thresholds with `PaperBookImbalanceForecastConfig(config_version="book-imbalance-v1")` (imbalance strength, max nudge, min book depth, and low/high confidence cutoffs).
- Build book-imbalance forecasts from caller-supplied markets and YES/NO order books with `build_paper_book_imbalance_forecast(market, yes_book, no_book, config=config, generated_at=datetime.now(UTC))`, which returns `PaperBookImbalanceForecast`. `no_book` is accepted for signature parity with the naive provider and for future cross-side sanity checks; it is not used by the v0 nudge math.
- Inspect the fair probability, confidence, `basis`, audit fields `yes_best_ask`, `yes_bid_size`, `yes_ask_size`, `imbalance`, and `nudge`, plus reason codes via `PaperBookImbalanceForecast`.
- Optionally append already-built forecasts with `PaperBookImbalanceForecastLog(path).append(forecast)`; there is no market fetcher, account reader, order API, JSONL reader, loader, replay, or from-file API.

## LLM Forecast v0 Status

LLM Forecast v0 adds a paper-only and report-only forecast primitive that produces a real epistemic P(YES) estimate from a probability model (Zhipu GLM-4-flash via stdlib urllib), rather than a microstructure heuristic. Unlike the naive (`yes_ask_naive_v0`) and book-imbalance (`book_imbalance_v0`) baselines, this estimate can pass calibration evidence gates, so it is the project's first "self-judge" primitive. It is split into two modules: `llm_research_transport` (the read-only outbound HTTPS network layer) and `llm_forecast` (the pure, network-free transform leaf). The `basis` field names the model (`llm_glm_v0`) so downstream audits know this is an LLM estimate, not a book heuristic.

The forecast is research-triage only; it is not a trade instruction, investment ranking, recommendation, financial advice, or live-execution signal. `paper_only is True` and `report_only is True` are hard-enforced on every report.

The transform leaf consumes only a caller-supplied market and an already-fetched probability-model result (dependency-injected); it does not fetch market data, read account data, authenticate, handle wallets, private keys, or credentials, use API clients, use browser automation, place, submit, sign, or cancel orders, rank investments, recommend trades, provide financial advice, or perform compliance/legal/geographic analysis. The transport layer performs a single read-only outbound HTTPS estimate to the public Zhipu endpoint (the same research-fetch class as `api.py`'s public Polymarket reads) and never authenticates to an exchange, reads accounts or positions, handles wallets or private keys, or places orders. The caller-supplied API token stays on the transport only and never reaches the forecast, logs, or archives.

Boundary shorthand: no fetch of private, account, wallet, credential, or order data; no auth, no wallet, no order, no rank, no recommend, no financial advice.

## LLM Forecast v0 Python API

LLM Forecast v0 is exposed through Python APIs:

- Configure the LLM forecast thresholds with `PaperLLMForecastConfig(config_version="llm-forecast-v1")` (model name, low/high confidence cutoffs, max question length).
- Build LLM forecasts from a caller-supplied market and an already-fetched probability-model result with `build_paper_llm_forecast(market, result=result, config=config, generated_at=datetime.now(UTC))`, which returns `PaperLLMForecast`. The `result` is typed against a leaf-local `_ProbabilityModelResult` Protocol so the leaf stays network-free; the transport's concrete result satisfies it structurally.
- Inspect the fair probability, confidence, `basis`, audit field `raw_p_yes` (pre-clamp), `model_name`, and reason codes via `PaperLLMForecast`. A `None` `raw_p_yes` falls back to `low_confidence_value` and records `("llm_parse_failed",)`; otherwise the value is clamped to `[0, 1]` and `("llm_probability_estimated",)` is recorded.
- Optionally append already-built forecasts with `PaperLLMForecastLog(path).append(forecast)`; there is no market fetcher, account reader, order API, JSONL reader, loader, replay, or from-file API.

## Cost-Aware Snapshot Builder v0 Status

Cost-Aware Snapshot Builder v0 adds a paper-only and report-only extractor that converts a caller-supplied `NormalizedMarket`, YES/NO order books, and a `PaperForecast` into the already-built `PaperCostAwareEventMarketSnapshot` that the Cost-Aware Event Strategy consumes. It resolves YES/NO by outcome name (with outcome index fallback) and derives spread and a v0 resolution-risk heuristic from market metadata.

The snapshot attempt envelope is research-triage only; it is not a trade instruction, investment ranking, recommendation, financial advice, or live-execution signal.

It consumes only caller-supplied markets, order books, and forecasts. It does not fetch market data, read account data, authenticate, handle wallets, private keys, or credentials, use API clients, use browser automation, place, submit, sign, or cancel orders, rank investments, recommend trades, provide financial advice, or perform compliance/legal/geographic analysis.

Boundary shorthand: no fetch, no auth, no wallet, no order, no rank, no recommend, no financial advice.

## Cost-Aware Snapshot Builder v0 Python API

Cost-Aware Snapshot Builder v0 is exposed through Python APIs:

- Configure snapshot extraction thresholds with `PaperCostAwareSnapshotConfig(config_version="snapshot-builder-v1")`.
- Build cost-aware market snapshot attempts from caller-supplied markets, YES/NO books, and a forecast with `build_paper_cost_aware_event_market_snapshot(market, yes_book, no_book, forecast, config=config, generated_at=datetime.now(UTC))`, which returns `PaperCostAwareSnapshotAttempt` whose `snapshot` field holds the already-built `PaperCostAwareEventMarketSnapshot` when `status == "snapshot_ready"`.
- Inspect per-attempt status and reason codes via `PaperCostAwareSnapshotAttempt`.
- Optionally append already-built attempts with `PaperCostAwareSnapshotLog(path).append(attempt)`; there is no market fetcher, account reader, order API, JSONL reader, loader, replay, or from-file API.

## Strategy Cycle v0 Status

Strategy Cycle v0 adds the first live-layer orchestrator: a paper-only and report-only research envelope that runs a self-contained read-only scan of the public Polymarket market universe. For each cycle it fetches the public Gamma market list, normalizes each market, archives the raw payloads, optionally ranks by the deterministic Level 0 scorer, truncates to a per-cycle budget, then evaluates each binary market through paired YES/NO order books, the frozen Stage 1a naive forecast, the cost-aware snapshot builder, and the cost-aware event strategy, finally aggregating into a deterministic project-screening research queue. Per-market isolation is mandatory: every market's fetch/normalize/snapshot path is wrapped in try/except, so one bad market is recorded as a cycle-layer or snapshot-builder `blocked_*` status and the cycle continues. This resolves the NormalizedMarket pipeline end to end.

The cycle's output `PaperStrategyCycleReport` is paper-only and report-only: it is a research audit envelope, never a trade instruction, investment ranking, recommendation, financial advice, or live-execution signal. `paper_only is True` and `report_only is True` are hard-enforced on the report.

It performs read-only public Gamma `/markets` and CLOB `/book` fetches only. It does not place, submit, sign, send, create, or cancel orders; does not authenticate; does not handle wallets, private keys, or credentials; does not read account, position, or exchange state; does not rank investments, recommend trades, or provide financial advice; and does not perform compliance/legal/geographic analysis.

Boundary shorthand: paper-only, report-only; read-only public Gamma/CLOB fetch only — no fetch of private, account, wallet, credential, or order data; no auth, no wallet, no order, no rank, no recommend, no financial advice.

## Strategy Cycle v0 Python API

Strategy Cycle v0 is exposed through Python APIs:

- Configure a cycle with `PaperStrategyCycleConfig(config_version=..., forecast_config=..., snapshot_config=..., strategy_config=..., screening_config=..., cost_assumptions=..., max_markets_per_cycle=50, prefilter_by_score=True)`.
- Run a paper-only strategy cycle with `run_strategy_cycle(*, client, scan_config, cycle_config, generated_at=None)`, which returns a `PaperStrategyCycleReport`. `client` is a caller-injected `MarketDataClient` (a local Protocol; this module does not import `api` — `cli.py` constructs the concrete public Gamma/CLOB reader and injects it).
- Inspect cycle counts, deterministic `blocked_counts`, and the nested `PaperProjectScreeningReport` via `PaperStrategyCycleReport`.
- Optionally append already-built cycle reports with `PaperStrategyCycleLog(path).append(report)`; there is no account reader, order API, wallet signer, JSONL reader, loader, replay, or from-file API.

## Paper Execution v0 Status

Paper Execution v0 closes the self-invest half of Phase 1: a paper-only and report-only leaf that turns each screening_ready candidate produced by the strategy cycle into an auditable paper trade. It runs INLINE inside `run_strategy_cycle` (never from replayed JSONL), where every snapshot_ready market's full in-memory context is available: the NormalizedMarket, both YES/NO OrderBookSnapshots, each book's RawArchiveEntry, the cost-aware event strategy report, and the project screening candidate. For each screening_ready candidate it walks `simulate_order_book_fill` against the chosen side's executable ask depth and emits a `PaperTradeRecord`. `strategy-cycle --paper-execute` requires `POLYMARKET_ALPHA_LAB_PAPER_TRADE_JOURNAL_DB_ENABLED=true` plus a local `POLYMARKET_ALPHA_LAB_PAPER_TRADE_JOURNAL_DB_DSN`, sends records only to the local Supabase/Postgres paper trade journal sink, and does not create the legacy `artifacts/paper-trades.jsonl` file. The evidence-gate chain (manual_review_queue / proposal_packet) is deliberately bypassed; paper-executed records carry a marker `strategy_type` (default `book_imbalance_screening_paper`) so they are filterable downstream.

The result is paper-only and report-only: it is a research/audit envelope, never a trade instruction, investment ranking, recommendation, financial advice, or live-execution signal. `paper_only is True` and `report_only is True` are hard-enforced on every result. Default-off: when `paper_execution_config is None` the cycle behaves byte-identically to Stage 1b/2/3 (no paper pass, no journal writes), and one bad paper execution never aborts the cycle (per-candidate try/except isolation, mirroring per-market isolation).

Phase 1 boundary: it only simulates a fill against the in-memory order book and persists paper-only records to the configured local paper history sink. It does not fetch private, account, wallet, credential, or order data; no auth; no wallet; no order placement, submission, signing, sending, creation, or cancellation; no account, position, or exchange-state reads; no rank, no recommend, no financial advice; and no compliance/legal/geographic analysis.

Boundary shorthand: paper-only, report-only; simulate + local paper-history persistence only -- no fetch of private/account/wallet/credential data, no auth, no wallet, no order, no rank, no recommend, no financial advice.

## Paper Execution v0 Python API

Paper Execution v0 is exposed through Python APIs:

- Configure the paper lane with `PaperExecutionConfig(config_version="paper-execution-v1", strategy_type="book_imbalance_screening_paper", paper_budget_size=Decimal("10.0000"), sizing_limiter="screening_book_depth", planned_exit_rule="hold_to_resolution", account_equity_before_trade=Decimal("10000.0000"), thesis_template=..., invalidating_conditions_template=..., rule_text=..., resolution_source_fallback="polymarket_event_resolution")` (frozen; all defaults paper-only/report-only).
- Execute one paper trade from a screening-ready candidate with `execute_paper_trade_from_screening(*, candidate, cost_aware_report, market, book, raw_book_archive_entry, market_raw_archive_entry, config, generated_at)`, which returns a `PaperExecutionResult` (paper-only/report-only; `fill` and `record` are populated on execution, otherwise a canonical `skipped_reason`).
- Inspect the per-attempt outcome via `PaperExecutionResult` (generated_at, market_slug, condition_id, token_id, side, fill, record, skipped_reason) and append results to a `PaperExecutionLog(path)`; the inline strategy-cycle pass requires an injected paper-trade record sink when `paper_execution_config` is enabled and never appends paper-execution writes to `PaperTradeJournal`. There is no account reader, order API, wallet signer, live client, loader, replay, or from-file API.

## Paper Portfolio NAV v0 Status

Paper Portfolio NAV v0 gives paper trades an observable mark-to-market P&L. The reusable NAV module can still read an explicit `PaperTradeJournal` JSONL path, and the one-shot `portfolio-nav` CLI plus continuous-run NAV source handling can instead load typed paper trade records from local Supabase/Postgres `paper_trade_journal_records` when `POLYMARKET_ALPHA_LAB_PAPER_TRADE_JOURNAL_DB_ENABLED=true`. DB-loaded paper trades are a source input only; NAV marking still rebuilds the paper portfolio, fetches the current public order book for every held token, and runs pure local NAV math. An empty source yields an empty portfolio, no order-book fetches, and NAV equal to `starting_cash`.

The result is paper-only and report-only: it is a research/audit NAV mark, never a trade instruction, investment ranking, recommendation, financial advice, or live-execution signal. `paper_only is True` is hard-enforced on every `PaperNavSnapshot`.

Phase 1 boundary: it reads either an explicit local JSONL paper-trade journal or, for the one-shot CLI and continuous-run NAV source handling when enabled, local Supabase/Postgres paper trade journal rows; then it performs read-only public `get_order_book` fetches and pure local NAV math. This does not migrate continuous `run` as a whole, outcome tracking, history/performance summary, strategy audit, observability trends, account state, wallet state, or exchange state. It does not fetch private, account, wallet, credential, or order data; no auth; no wallet; no order placement, submission, signing, sending, creation, or cancellation; no account, position, or exchange-state reads; no rank, no recommend, no financial advice; and no compliance/legal/geographic analysis.

Boundary shorthand: paper-only, report-only; read local paper trade source + read-only book fetch + pure NAV only -- no fetch of private/account/wallet/credential data, no auth, no wallet, no order, no rank, no recommend, no financial advice.

## Paper Portfolio NAV v0 Python API

- Read a paper-trade journal back into fully-typed records with `PaperTradeJournal.read(path)`, a static JSONL reader that reverses `PaperTradeJournal.append`'s serialization keyed to each field's resolved annotation (`Decimal` str -> `Decimal`, ISO str -> `datetime`, `list` -> `tuple` for `risk_tags`; `None` passes through for the optional `fill_*` fields). It skips blank lines, raises `ValueError` (with a line number) on non-JSON lines, and returns an empty tuple for an empty file. `build_paper_portfolio` re-validates every record.
- Mark the portfolio NAV end-to-end with `mark_paper_portfolio_nav(journal_path, *, starting_cash, client, marked_at, nav_log_path=None)`, which returns a `PaperNavSnapshot`. It reads the journal, builds the portfolio, fetches one book per held token via the injected client, marks NAV, and optionally appends the snapshot to a `PaperNavLog`. `starting_cash` must be a positive `Decimal` (`> 0`); the client is a local `MarketNavClient` Protocol (only `get_order_book(token_id=)`) so this module never imports `api` -- the concrete `PolymarketPublicClient` is constructed in `cli.py` and injected. `marked_at` is caller-supplied (deterministic for tests).
- The one-shot CLI and continuous-run DB source branches reuse the existing record-based NAV composition seam after loading typed `PaperTradeRecord` values; the NAV module remains free of psycopg, env reads, Supabase config, and DB clients.
- Mark NAV from the CLI with `polymarket-alpha-lab portfolio-nav --journal <path> --starting-cash <Decimal> [--nav-log <path>]`, which prints a NAV summary (starting_cash, cash_balance, realized_pnl, exit_nav, unrealized_pnl, position_count). When the paper trade journal DB env is enabled, the one-shot CLI loads source trades from local Supabase/Postgres instead of legacy JSONL; otherwise `--journal` is the explicit JSONL source. There is no account reader, order API, wallet signer, live-execution client, scheduler, or time-series replay.

## Performance Summary v0 Status

Performance Summary v0 closes the cumulative-history loop. The system is in a mixed persistence migration state: selected write paths have local Supabase/Postgres sinks, but this performance-summary consumer still reads paper-trade, cycle-report, and NAV histories through the existing JSONL readers until equivalent DB-backed sources are added. The one-shot `portfolio-nav` and continuous-run NAV DB paper-trade sources do not change the `history` command or performance-summary read sources. Stage 5 added the paper-trade JSONL reader; Stage 6 adds cycle-report and NAV JSONL readers plus a pure performance-summary aggregator so the user can see cumulative system performance over time (cycles run, markets scanned, candidates ready, paper trades executed, realized P&L, last NAV, time span).

The hard part is the reader. Unlike Stage 5's flat `PaperTradeRecord` (no `__post_init__`), `PaperStrategyCycleReport` and `PaperNavSnapshot` have validating `__post_init__` methods that check nested fields via `isinstance` and normalize nested tuples. A shared recursive helper, `json_recovery.from_jsonable(cls, row)`, reconstructs the FULL nested dataclass tree (resolving types via `get_type_hints`, recursing into nested dataclasses, deep-converting list to tuple at every level for `tuple[tuple[str, int], ...]`-style fields, and coercing `Decimal`/`datetime`). Each reader constructs `cls(**reconstructed)` so `__post_init__` re-validates.

The summary is paper-only and report-only: it is a research/audit aggregate, never a trade instruction, investment ranking, recommendation, financial advice, or live-execution signal. `paper_only is True` is hard-enforced on every `PerformanceSummary`.

Phase 1 boundary: read-only data analysis over local typed tuples only. It does not fetch market, order-book, price-history, account, wallet, or credential data; does not authenticate; does not handle private keys or credentials; does not place, submit, sign, send, create, or cancel orders; does not open WebSockets; does not use a trading SDK, broker client, or execution client; does not reconcile exchange accounts; and does not perform compliance/legal/geographic analysis.

Boundary shorthand: paper-only, report-only; local typed-tuple arithmetic only -- no fetch, no auth, no wallet, no order, no rank, no recommend, no financial advice.

## Performance Summary v0 Python API

- Read a strategy-cycle JSONL log back into fully-typed reports with `PaperStrategyCycleLog.read(path)`, a static JSONL reader that reverses `PaperStrategyCycleLog.append`'s serialization via the shared recursive `json_recovery.from_jsonable(PaperStrategyCycleReport, row)` helper. It reconstructs the full nested report tree (including the `PaperProjectScreeningReport` subtree and the deep `tuple[tuple[str, int], ...]` `blocked_counts`) so each report's validating `__post_init__` re-runs. It skips blank lines, raises `ValueError` (with a line number) on non-JSON lines, and returns an empty tuple for an empty file.
- Read a NAV JSONL log back into fully-typed snapshots with `PaperNavLog.read(path)`, a static JSONL reader using the same recursive helper to reconstruct each `PaperPositionMark` (Decimal/datetime/optional fields) so `PaperNavSnapshot.__post_init__` re-validates each snapshot. Same blank-line/empty/non-JSON handling.
- Aggregate the three history streams with `build_performance_summary(cycle_reports, trade_records, nav_snapshots, *, config, generated_at) -> PerformanceSummary`. Pure arithmetic: cycle count, summed scan/snapshot-ready/cost-aware counts, paper-trade count, NAV-snapshot count, the most-recent NAV snapshot's exit NAV / starting cash / cumulative realized P&L, and the min/max cycle `generated_at` span. Empty inputs collapse to zeros and `None`. `PerformanceSummaryConfig` carries only a canonical `config_version` string.
- Print a cumulative summary from the CLI with `polymarket-alpha-lab history --cycle-log <path> --trade-log <path> --nav-log <path>`, which reads the three JSONL logs, builds the summary, and prints cycle/market/candidate/trade/NAV counts plus the time span. There is no account reader, order API, wallet signer, live-execution client, scheduler, or time-series replay.

## Continuous Run v0 Status

Continuous Run v0 adds a synchronous loop orchestrator that chains strategy-cycle (with optional paper execution), cycle-report persistence/export, and portfolio NAV marking, repeating for a configurable number of iterations with an optional sleep interval. Each iteration is wrapped in try/except so one bad cycle never kills the loop (on-cycle-error log-and-continue is the default). This is the operationalization primitive: it manufactures the longitudinal observations (cycles, trades, NAV snapshots) needed for Level 2 promotion gates.

The optional `run --strategy-audit-preflight` mode reads existing local logs, builds the Strategy Risk Audit report, optionally appends it to a caller-selected local JSONL evidence artifact when `--strategy-audit-log <path>` or `strategy_audit_log` is supplied, prints its local summary, and blocks before public client construction unless the status is `audit_ready`. It writes no audit log by default. It is a paper-only/report-only/read-only safety pause, not a default behavior change, strategy-promotion signal, approval workflow, trade instruction, investment ranking, recommendation, live execution, or financial advice.

The summary is paper-only and report-only: it is a research/audit aggregate, never a trade instruction, investment ranking, recommendation, financial advice, or live-execution signal. It does not place, submit, sign, or cancel orders; does not authenticate; does not handle wallets, private keys, or credentials; does not read account state; and does not perform compliance/legal/geographic analysis.

Boundary shorthand: paper-only, report-only, no auth, no wallet, no order, no rank, no recommend, no financial advice.

## Continuous Run v0 Python API

- Run a single-shot or repeating loop with `run_strategy_loop(*, client, scan_config, cycle_config, starting_cash, nav_log_path, cycle_report_log_path, cycle_report_sink=None, repeat_mode="once", interval_seconds=0, max_iterations=1, on_cycle_error="log_and_continue", paper_trade_record_source=None)`, which returns a paper-only/report-only `RunLoopSummary`. Per iteration it (a) calls `run_strategy_cycle`, (b) persists the report through an injected `cycle_report_sink` when supplied, (c) appends a JSONL compatibility export only when `cycle_report_log_path` is supplied, and (d) marks paper portfolio NAV from an injected paper-trade record source when supplied, otherwise from the configured paper journal path when that JSONL file exists. At least one of `cycle_report_sink` or `cycle_report_log_path` is required. When both are supplied, the sink runs before the JSONL export; sink failure leaves no JSONL export for that report. `starting_cash` must be a positive `Decimal`; `client` is the injected `MarketDataClient` Protocol (this module never imports `api`); `interval_seconds >= 0`; `max_iterations >= 1`. The `run` loop can use local Supabase/Postgres `paper_trade_journal_records` for NAV source handling through CLI-owned env wiring when the local paper trade journal DB env is enabled, but continuous run as a whole is not fully DB-backed. History/performance summary, strategy audit, and observability/trend paper-trade DB read sources remain separate migration surfaces.
- First-run journal skip: on the first iteration the paper-trade journal may not exist yet (paper execution default-off, or the cycle produced no screening-ready candidate), and `mark_paper_portfolio_nav` reads the journal via `PaperTradeJournal.read` which raises `FileNotFoundError` on a missing file. The runner pre-checks the path and defensively catches `FileNotFoundError`, skipping the NAV mark and counting it in `RunLoopSummary.nav_marks_skipped`. The iteration still completes (the cycle ran and was logged); the skip is a benign first-run condition, never a cycle failure.
- Isolate per-iteration failures with `on_cycle_error`: `"log_and_continue"` (default) increments `iterations_failed`, records `last_error`, and continues to the next iteration (a failed cycle skips the inter-iteration sleep); `"raise"` propagates the exception immediately. When `repeat_mode="interval"`, `time.sleep(interval_seconds)` runs between iterations only (never after the last, never after a failed one). The loop is synchronous by design (v0); async/scheduler is a later stage.
- Inspect the run with `RunLoopSummary` (frozen): `iterations_completed`, `iterations_failed`, `first_iteration_at`/`last_iteration_at`, `last_error`, `nav_marks_skipped`, with `paper_only is True` / `report_only is True` hard-enforced.
- Run from the CLI with `polymarket-alpha-lab run --starting-cash <Decimal> [--cycle-log <path>] [--nav-log <path>] [--repeat-interval <seconds>] [--max-iterations <n>] [--paper-execute]`, which reuses the strategy-cycle scan/cycle args, maps `--repeat-interval 0` to single-shot, and prints completed/failed/skipped counts plus the time span. Continuous `run` requires either local Supabase/Postgres strategy-cycle report DB persistence (`POLYMARKET_ALPHA_LAB_PAPER_STRATEGY_CYCLE_REPORT_DB_ENABLED=true` with a local DSN) or an explicit `--cycle-log <path>` JSONL compatibility export; there is no implicit `artifacts/strategy-cycle.jsonl` durable default. `run --paper-execute` requires the local paper trade journal DB env, writes paper trade records only through `paper_trade_journal_records`, and rejects `--paper-journal` because JSONL is not an execution write sink. This is not a fully DB-backed continuous run: optional audit logs, history/performance summary, strategy audit, and observability/trend inputs remain on their existing local JSONL/report paths until separate migrations land. There is no account reader, order API, wallet signer, live-execution client, scheduler daemon, or async runtime.
- Add `--strategy-audit-preflight --nav-log <path> [--paper-journal <legacy-trade-log>] [--outcome-log <path>] [--strategy-audit-log <path>]` when a paper run should be gated by the latest local Strategy Risk Audit. The gate reuses `--cycle-log` and `--paper-journal` as local audit read inputs, requires `audit_ready`, and blocks before public client construction when evidence is incomplete or a risk gate fails. The optional audit log appends the preflight report to a caller-selected local JSONL evidence artifact only when explicitly supplied.
- `run --config strategy.example.json` can set the same local preflight fields with `strategy_audit_preflight`, `outcome_log`, and `strategy_audit_log`; the checked-in example keeps the preflight disabled by default and omits `paper_journal`, so copying it does not write audit logs or configure a JSONL paper-execution sink.

## Outcome Tracker v0 Status

Outcome Tracker v0 (Stage 9) closes the self-judge verification loop. It reads paper-traded markets from the active paper-trade source (legacy JSONL by default, or local Supabase/Postgres `paper_trade_journal_records` for `check-outcomes` when that DB env is enabled), re-lists the CLOSED slice from Gamma, and for each resolved market derives the winning side from `outcomePrices` (the resolved payout array paired with `outcomes`), then builds one `PaperForecastEvidenceObservation` per resolved trade LEG and feeds the forecast-evidence calibration report. This tells the user whether the LLM forecast's probability estimates are actually accurate over time. It is paper-only and report-only: it is a research/audit calibration aggregate, never a trade instruction, investment ranking, recommendation, financial advice, or live-execution signal.

Phase 1 boundary: read-only Gamma `/markets` re-list + pure Decimal computation. It does not place, submit, sign, or cancel orders; does not authenticate; does not handle wallets, private keys, or credentials; does not read account state; and does not perform compliance/legal/geographic analysis. The winning outcome is read from `outcomePrices` on the RAW Gamma payload (NEVER from `resolutionStatus`, which is unreliable); outcome labels are matched case-insensitively through the SAME `YES_NAMES`/`NO_NAMES` alias sets as the cost-aware snapshot builder (never direct string equality); and YES/NO legs are independent calibration points, so one resolved trade record produces exactly one observation (never deduplicated by `condition_id`).

Boundary shorthand: paper-only, report-only, no auth, no wallet, no order, no rank, no recommend, no financial advice.

## Outcome Tracker v0 Python API

- Configure the tracker with `OutcomeTrackingConfig(config_version="outcome-tracker-v1")` (frozen); its nested `forecast_evidence_config` defaults to a synced `PaperForecastEvidenceConfig`.
- Run a check with `check_outcomes(*, client, journal_path, config, generated_at, paper_trade_record_source=None)`, which returns a paper-only/report-only `OutcomeTrackingReport`. `client` is the injected `OutcomeTrackerClient` Protocol (this module never imports `api`; `cli.py` constructs the concrete `PolymarketPublicClient` and injects it). It reads typed records from `paper_trade_record_source` when supplied, otherwise reads `PaperTradeJournal.read(journal_path)` (a missing journal is treated as zero records only on the JSONL path), lists closed markets via `client.list_markets(active=False, closed=True, limit=500)`, and for each closed market with a parseable `outcomePrices` winner builds one observation (`predicted_probability = research_fair_value_estimate`, `actual_outcome_value = Decimal("1")` if the traded side won else `Decimal("0")`).
- Inspect the result with `OutcomeTrackingReport` (frozen): `generated_at`, `config_version`, `total_markets_checked`, `resolved_count`, `pending_count`, `observations` (tuple of `PaperForecastEvidenceObservation`), and `forecast_evidence_report` (`PaperForecastEvidenceReport | None`, None iff zero observations). Hard-enforced invariants: `resolved_count == len(observations)`, `resolved_count + pending_count == total_markets_checked`, and `paper_only is True` / `report_only is True`.
- Persist full outcome reports with `OutcomeTrackingLog(path).append(report)` and restore them with `OutcomeTrackingLog.read(path)`, which returns typed `OutcomeTrackingReport` values for local audit replay. The log reader skips blank lines and rejects invalid JSON with a line number; missing files propagate normally.
- Run from the CLI with `polymarket-alpha-lab check-outcomes --journal <path> [--evidence-log <path>] [--outcome-log <path>]`, which builds the report and prints checked/resolved/pending/observation counts plus the forecast-evidence status. When the local paper trade journal DB env is enabled, `check-outcomes` loads source trades from local Supabase/Postgres `paper_trade_journal_records` and reverses the newest-first DB loader output into oldest-first deterministic replay order; otherwise `--journal` is the explicit JSONL source. When `--evidence-log` is supplied and at least one observation resolved, the `PaperForecastEvidenceReport` is appended to that JSONL log via `PaperForecastEvidenceLog`; when `--outcome-log` is supplied, the full `OutcomeTrackingReport` is appended even if no markets resolved yet. There is no account reader, order API, wallet signer, live-execution client, scheduler daemon, or async runtime.

## Level 2 Node 1 Status

Level 2 Node 1 adds reviewable proposal-packet artifacts over supplied paper-trading, analytics, forecast-evidence, and manual-review artifacts. A proposal packet is for human review only; it is not an approval workflow, trade instruction, order instruction, broker request, strategy-promotion signal, or live-execution signal. No order may leave the system without explicit human approval.

It does not fetch market, order-book, price-history, outcome, or account data; scrape websites; authenticate; handle private keys or credentials; place, submit, sign, or cancel orders; open user WebSockets; run heartbeat logic; use a trading SDK, broker client, or execution client; reconcile exchange accounts; import manual executions; or perform compliance/legal/geographic analysis.

## Level 2 Node 1 Python API

Node 1 is exposed through Python APIs:

- Configure proposal packet limits and boundary text with `TradeProposalPacketConfig(config_version="proposal-v1")`.
- Build proposal packets with `build_trade_proposal_packet(queue_item, side="buy", intended_order_type="limit", maximum_size=Decimal("25"), exposure_after_trade=Decimal("0.1200"), exit_rule="Exit if executable price reaches fair value or thesis invalidates.", reason_trade_could_be_wrong="Liquidity could disappear before exit.", config=config, generated_at=datetime.now(UTC))`, which returns `TradeProposalPacket`.
- Inspect proposal-only review fields with `TradeProposalPacket`.
- Persist proposal packet snapshots with `TradeProposalPacketLog(path).append(packet)`.

## Level 2 Node 2 Status

Level 2 Node 2 adds append-only proposal-review record artifacts over supplied `TradeProposalPacket` values. A proposal-review record captures a reviewer decision, rationale, reviewed packet identity, attestation, and boundary text for audit only; it is not an approval workflow, trade instruction, order instruction, broker request, order request, account action, strategy-promotion signal, or live-execution signal.

It does not fetch market, order-book, price-history, outcome, account, credential, or identity data; scrape websites; authenticate; handle private keys or credentials; place, submit, sign, send, create, or cancel orders; open user WebSockets; run heartbeat logic; use a trading SDK, broker client, execution client, or transport client; build broker or order request payloads; reconcile exchange accounts; import manual executions; or perform compliance/legal/geographic analysis.

## Level 2 Node 2 Python API

Node 2 is exposed through Python APIs:

- Configure proposal-review boundaries with `TradeProposalReviewConfig(config_version="review-v1")`.
- Build proposal-review records with `build_trade_proposal_review_record(packet, decision="approved", reviewer_label="human-reviewer", review_rationale="Reviewed packet fields and supporting evidence.", review_reason_codes=(), human_attestation=config.required_human_attestation, config=config, recorded_at=datetime.now(UTC))`, which returns `TradeProposalReviewRecord`.
- Inspect review-only audit fields with `TradeProposalReviewRecord`.
- Persist proposal-review snapshots with `TradeProposalReviewLog(path).append(record)`.

## Level 2 Node 3 Status

Level 2 Node 3 adds report-only proposal-review summary artifacts over supplied `TradeProposalReviewRecord` values. It tracks review volume, approved/rejected decision counts, rejected reason-code concentration, duplicate source proposal review volume, and market/strategy/risk-tag breakdowns for audit only; it is not an approval workflow, trade instruction, order instruction, broker request, order request, account action, strategy-promotion signal, or live-execution signal.

It does not fetch market, order-book, price-history, outcome, account, credential, or identity data; read external history or JSONL logs; scrape websites; authenticate; handle private keys or credentials; place, submit, sign, send, create, or cancel orders; open user WebSockets; run heartbeat logic; use a trading SDK, broker client, execution client, or transport client; build broker or order request payloads; reconcile exchange accounts; import manual executions; or perform compliance/legal/geographic analysis.

## Level 2 Node 3 Python API

Node 3 is exposed through Python APIs:

- Configure proposal-review summary boundaries with `TradeProposalReviewSummaryConfig(config_version="summary-v1")`.
- Build proposal-review summary reports with `build_trade_proposal_review_summary_report(records, config=config, generated_at=datetime.now(UTC))`, which returns `TradeProposalReviewSummaryReport`.
- Inspect reason-code rows with `TradeProposalReviewReasonCodeSummary` and grouped market/strategy/risk-tag rows with `TradeProposalReviewBucketSummary`.
- Persist proposal-review summary snapshots with `TradeProposalReviewSummaryLog(path).append(report)`.

## Level 2 Node 4 Status

Level 2 Node 4 adds report-only proposal-review quality gate artifacts over supplied `TradeProposalReviewSummaryReport` values. It tracks summary volume, review decision volume, rejection-rate stability, rejected reason-code concentration, and duplicate source proposal review volume for audit only; it is not an approval workflow, trade instruction, order instruction, broker request, order request, account action, strategy-promotion signal, or live-execution signal.

It does not fetch market, order-book, price-history, outcome, account, credential, or identity data; read external history or JSONL logs; scrape websites; authenticate; handle private keys or credentials; place, submit, sign, send, create, or cancel orders; open user WebSockets; run heartbeat logic; use a trading SDK, broker client, execution client, or transport client; build broker or order request payloads; reconcile exchange accounts; import manual executions; or perform compliance/legal/geographic analysis.

## Level 2 Node 4 Python API

Node 4 is exposed through Python APIs:

- Configure proposal-review quality gates with `TradeProposalReviewQualityConfig(config_version="quality-v1")`.
- Build proposal-review quality reports with `build_trade_proposal_review_quality_report(summaries, config=config, generated_at=datetime.now(UTC))`, which returns `TradeProposalReviewQualityReport`.
- Inspect quality gate rows with `TradeProposalReviewQualityGateResult` and rejected reason-code trends with `TradeProposalReviewQualityReasonTrend`.
- Persist proposal-review quality snapshots with `TradeProposalReviewQualityLog(path).append(report)`.

## Level 2 Node 5 Status

Level 2 Node 5 adds report-only proposal-review diagnostic artifacts over supplied `TradeProposalReviewRecord` values. It treats rejected human-review decisions as a proposal-quality investigation proxy only; it does not confirm realized false positives, import outcomes, compare fills, reconcile positions, settle decisions, or route proposals.

It does not fetch market, order-book, price-history, outcome, account, credential, or identity data; read external history or JSONL logs; scrape websites; authenticate; handle private keys or credentials; place, submit, sign, send, create, or cancel orders; open user WebSockets; run heartbeat logic; use a trading SDK, broker client, execution client, or transport client; build broker or order request payloads; reconcile exchange accounts; review settlement; import manual executions; or perform compliance/legal/geographic analysis.

## Level 2 Node 5 Python API

Node 5 is exposed through Python APIs:

- Configure proposal-review diagnostics with `TradeProposalReviewDiagnosticConfig(config_version="diagnostic-v1")`.
- Build proposal-review diagnostic reports with `build_trade_proposal_review_diagnostic_report(records, config=config, generated_at=datetime.now(UTC))`, which returns `TradeProposalReviewDiagnosticReport`.
- Inspect rejected reason-code proxy rows with `TradeProposalReviewDiagnosticReasonRow`, bucket rows with `TradeProposalReviewDiagnosticBucketRow`, and rejected source proposal rows with `TradeProposalReviewDiagnosticSourceRow`.
- Persist proposal-review diagnostic snapshots with `TradeProposalReviewDiagnosticLog(path).append(report)`.

## Level 2 Node 6 Status

Level 2 Node 6 adds report-only proposal-review coverage artifacts over supplied `TradeProposalPacket` and `TradeProposalReviewRecord` values. It treats proposal packets as the coverage denominator and review records as observed review evidence, reporting reviewed, unreviewed, duplicate-reviewed, conflicting-decision, and orphan-review coverage without selecting a winning decision or routing proposals.

It does not fetch market, order-book, price-history, outcome, account, credential, or identity data; read external history or JSONL logs; scrape websites; authenticate; handle private keys or credentials; place, submit, sign, send, create, or cancel orders; open user WebSockets; run heartbeat logic; use a trading SDK, broker client, execution client, or transport client; build broker or order request payloads; reconcile exchange accounts; review settlement; import manual executions; run approval workflows; select latest decisions; or perform compliance/legal/geographic analysis.

## Level 2 Node 6 Python API

Node 6 is exposed through Python APIs:

- Configure proposal-review coverage with `TradeProposalReviewCoverageConfig(config_version="coverage-v1")`.
- Build proposal-review coverage reports with `build_trade_proposal_review_coverage_report(proposals, records, config=config, generated_at=datetime.now(UTC))`, which returns `TradeProposalReviewCoverageReport`.
- Inspect coverage gates with `TradeProposalReviewCoverageGateResult`, bucket rows with `TradeProposalReviewCoverageBucketRow`, and packet rows with `TradeProposalReviewCoveragePacketRow`.
- Persist proposal-review coverage snapshots with `TradeProposalReviewCoverageLog(path).append(report)`.

## Level 2 Node 7 Status

Level 2 Node 7 adds report-only proposal-review dossier artifacts over supplied `TradeProposalReviewSummaryReport`, `TradeProposalReviewQualityReport`, `TradeProposalReviewDiagnosticReport`, and `TradeProposalReviewCoverageReport` values. It assembles review evidence, gaps, gate rows, source rows, and finding rows for audit only; it is not an approval workflow, trade instruction, order instruction, broker request, order request, account action, strategy-promotion signal, or live-execution signal.

It does not fetch market, order-book, price-history, outcome, account, credential, or identity data; read external history or JSONL logs; scrape websites; authenticate; handle private keys or credentials; place, submit, sign, send, create, or cancel orders; open user WebSockets; run heartbeat logic; use a trading SDK, broker client, execution client, or transport client; build broker or order request payloads; reconcile exchange accounts; perform reconciliation; review settlement; import manual executions; run approval workflows; select latest decisions; resolve conflicting reviews; rank investments; recommend trades; or perform compliance/legal/geographic analysis.

## Level 2 Node 7 Python API

Node 7 is exposed through Python APIs:

- Configure proposal-review dossiers with `TradeProposalReviewDossierConfig(config_version="dossier-v1")`.
- Build proposal-review dossier reports with `build_trade_proposal_review_dossier_report(summary=summary, quality=quality, diagnostics=diagnostics, coverage=coverage, config=config, generated_at=datetime.now(UTC))`, which returns `TradeProposalReviewDossierReport`.
- Inspect dossier gates with `TradeProposalReviewDossierGateResult`, source rows with `TradeProposalReviewDossierSourceRow`, and finding rows with `TradeProposalReviewDossierFindingRow`.
- Persist proposal-review dossier snapshots with `TradeProposalReviewDossierLog(path).append(report)`.

## Level 2 Node 8 Status

Level 2 Node 8 adds report-only proposal-review dossier batch health artifacts over supplied `TradeProposalReviewDossierReport` values. It treats supplied dossier reports as caller-provided audit inputs and summarizes dossier counts, status ratios, config-version rows, duplicate dossier fingerprints, finding summaries, source summaries, and gate rows for audit only; it is not an approval workflow, proposal approval step, approved-proposal selector, latest-decision selector, decision-resolution process, investment ranking, trade recommendation, trade instruction, order instruction, broker request, order request, account action, strategy-promotion signal, or live-execution signal.

It does not fetch market, order-book, price-history, outcome, account, credential, or identity data; read external history or JSONL logs; scrape websites; authenticate; handle credentials/private keys; place, submit, sign, send, create, or cancel orders; open user WebSockets; run heartbeat logic; use trading SDK/broker/execution/transport clients; build broker or order request payloads; reconcile exchange accounts; perform reconciliation; review settlement; approve proposals; select latest decisions; resolve conflicting reviews; rank investments; recommend trades; or perform compliance/legal/geographic analysis.

## Level 2 Node 8 Python API

Node 8 is exposed through Python APIs:

- Configure proposal-review dossier batches with `TradeProposalReviewDossierBatchConfig(config_version="dossier-batch-v1")`.
- Build proposal-review dossier batch health reports with `build_trade_proposal_review_dossier_batch_report(dossiers, config=config, generated_at=datetime.now(UTC))`, which returns `TradeProposalReviewDossierBatchReport`.
- Inspect batch gates with `TradeProposalReviewDossierBatchGateResult`, config rows with `TradeProposalReviewDossierBatchConfigVersionSummary`, duplicate rows with `TradeProposalReviewDossierBatchDuplicateSummary`, finding rows with `TradeProposalReviewDossierBatchFindingSummary`, and source rows with `TradeProposalReviewDossierBatchSourceSummary`.
- Persist proposal-review dossier batch snapshots with `TradeProposalReviewDossierBatchLog(path).append(report)`.

## Level 2 Node 9 Status

Level 2 Node 9 adds report-only proposal evidence comparison artifacts over supplied `PaperForecastEvidenceReport` and `TradeProposalReviewDossierBatchReport` values. It compares forecast evidence health and proposal-review dossier batch health for audit only; it is not an approval workflow, proposal approval step, approved-proposal selector, latest-decision selector, decision-resolution process, investment ranking, trade recommendation, strategy-promotion signal, trade instruction, order instruction, broker request, order request, account action, or live-execution signal.

It does not fetch market, order-book, price-history, outcome, account, credential, or identity data; read external history or JSONL logs; scrape websites; authenticate; handle credentials/private keys; place, submit, sign, send, create, or cancel orders; open user WebSockets; run heartbeat logic; use trading SDK/broker/execution/transport clients; build broker or order request payloads; reconcile exchange accounts; perform reconciliation; review settlement; import manual executions; approve proposals; select latest decisions; resolve conflicting reviews; rank investments; recommend trades; or perform compliance/legal/geographic analysis.

## Level 2 Node 9 Python API

Node 9 is exposed through Python APIs:

- Configure evidence comparisons with `TradeProposalEvidenceComparisonConfig(config_version="comparison-v1")`.
- Build evidence comparison reports with `build_trade_proposal_evidence_comparison_report(forecast_evidence=forecast, dossier_batch=batch, config=config, generated_at=datetime.now(UTC))`, which returns `TradeProposalEvidenceComparisonReport`.
- Inspect comparison gates with `TradeProposalEvidenceComparisonGateResult`, source rows with `TradeProposalEvidenceComparisonSourceRow`, metric rows with `TradeProposalEvidenceComparisonMetricRow`, and finding rows with `TradeProposalEvidenceComparisonFindingRow`.
- Persist comparison snapshots with `TradeProposalEvidenceComparisonLog(path).append(report)`.

## Level 2 Node 10 Status

Level 2 Node 10 adds report-only proposal evidence comparison history summaries over caller-supplied, in-memory `TradeProposalEvidenceComparisonReport` values from Node 9. It summarizes comparison statuses, divergence proxy rates, finding-code frequencies, config-version coverage, and source-status transitions for audit only; it is not an approval workflow, proposal approval step, approved-proposal selector, latest-decision selector, decision-resolution process, investment ranking, trade recommendation, strategy-promotion signal, trade instruction, order instruction, broker request, order request, account action, outcome loader, realized false-positive analysis, profitability analysis, compliance surface, geographic surface, or live-execution signal.

It rejects loader-shaped inputs such as paths, mappings, strings, and bytes. It does not fetch market, order-book, price-history, outcome, account, credential, identity, or settlement data; read external history or JSONL logs; provide JSONL readers, loaders, replay, or from-file APIs; scrape websites; use browsers or browser sessions; authenticate; handle wallets, credentials, or private keys; place, submit, sign, send, create, or cancel orders; open user WebSockets; run heartbeat logic; use trading SDK/broker/execution/transport clients; build broker or order request payloads; reconcile exchange accounts; perform reconciliation; review settlement; import manual executions; approve proposals; select latest decisions; resolve conflicting reviews; rank investments; recommend trades; or perform compliance/legal/geographic analysis.

## Level 2 Node 10 Python API

Node 10 is exposed through Python APIs:

- Configure comparison-history reports with `TradeProposalEvidenceComparisonHistoryConfig(config_version="comparison-history-v1")`.
- Build comparison-history reports from supplied, in-memory Node 9 `TradeProposalEvidenceComparisonReport` values with `build_trade_proposal_evidence_comparison_history_report(comparisons, config=config, generated_at=datetime.now(UTC))`, which returns `TradeProposalEvidenceComparisonHistoryReport`.
- Inspect history gates with `TradeProposalEvidenceComparisonHistoryGateResult`, status rows with `TradeProposalEvidenceComparisonHistoryStatusRow`, finding summaries with `TradeProposalEvidenceComparisonHistoryFindingSummary`, config-version summaries with `TradeProposalEvidenceComparisonHistoryConfigVersionSummary`, and source transitions with `TradeProposalEvidenceComparisonHistorySourceTransition`.
- Optionally append already-built comparison-history snapshots with `TradeProposalEvidenceComparisonHistoryLog(path).append(report)`; there is no JSONL reader, loader, replay, or from-file API.

## Level 2 Node 11 Status

Level 2 Node 11 adds report-only proposal evidence comparison history batch health artifacts over supplied `TradeProposalEvidenceComparisonHistoryReport` values. It summarizes history status frequencies, duplicate generated-at indicators, duplicate fingerprint indicators, config-version coverage, finding-code frequencies, source-transition coverage, and append-only JSONL persistence for audit only; it is not an approval workflow, proposal approval step, approved-proposal selector, latest-decision selector, decision-resolution process, investment ranking, trade recommendation, strategy-promotion signal, trade instruction, order instruction, broker request, order request, account action, outcome loader, realized false-positive analysis, profitability analysis, or live-execution signal.

It does not fetch market, order-book, price-history, outcome, account, credential, identity, or settlement data; read external history or JSONL logs; scrape websites; authenticate; handle credentials/private keys; place, submit, sign, send, create, or cancel orders; open user WebSockets; run heartbeat logic; use trading SDK/broker/execution/transport clients; build broker or order request payloads; reconcile exchange accounts; perform reconciliation; review settlement; import manual executions; approve proposals; select latest decisions; resolve conflicting reviews; rank investments; recommend trades; or perform compliance/legal/geographic analysis.

## Level 2 Node 11 Python API

Node 11 is exposed through Python APIs:

- Configure batch-health reports with `TradeProposalEvidenceComparisonHistoryBatchHealthConfig(config_version="history-batch-health-v1")`.
- Build batch-health reports with `build_trade_proposal_evidence_comparison_history_batch_health_report(history_reports, config=config, generated_at=datetime.now(UTC))`, which returns `TradeProposalEvidenceComparisonHistoryBatchHealthReport`.
- Inspect batch-health gates with `TradeProposalEvidenceComparisonHistoryBatchHealthGateResult`, status rows with `TradeProposalEvidenceComparisonHistoryBatchHealthStatusRow`, config-version summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthConfigVersionSummary`, duplicate-generated-at summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthDuplicateGeneratedAtSummary`, duplicate-fingerprint summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthDuplicateFingerprintSummary`, finding summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthFindingSummary`, and source-transition summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthSourceTransitionSummary`.
- Persist batch-health snapshots with `TradeProposalEvidenceComparisonHistoryBatchHealthLog(path).append(report)`.

## Level 2 Node 12 Status

Level 2 Node 12 adds report-only proposal evidence comparison history batch-health trend artifacts over supplied `TradeProposalEvidenceComparisonHistoryBatchHealthReport` values. It summarizes batch-health status frequencies, gate-status frequencies, config-version coverage, duplicate generated-at indicators, duplicate fingerprint indicators, and first/last report time bounds for audit only, and it supports append-only JSONL persistence; it is not an approval workflow, proposal approval step, approved-proposal selector, latest-decision selector, decision-resolution process, investment ranking, trade recommendation, strategy-promotion signal, trade instruction, order instruction, broker request, order request, account action, outcome loader, realized false-positive analysis, profitability analysis, automatic order-placement authorization, or live-execution signal.

It rejects loader-shaped inputs such as paths, mappings, strings, bytes, generators, arbitrary iterables, and log-shaped objects. It does not fetch market, order-book, price-history, outcome, account, credential, identity, or settlement data; read external history or JSONL logs; provide JSONL readers, loaders, replay, or from-file APIs; scrape websites; use browsers or browser sessions; authenticate; handle wallets, credentials/private keys; place, submit, sign, send, create, or cancel orders; open user WebSockets; run heartbeat logic; use trading SDK/broker/execution/transport clients; build broker or order request payloads; reconcile exchange accounts; perform reconciliation; review settlement; import manual executions; approve proposals; select latest decisions; resolve conflicting reviews; rank investments; recommend trades; or perform compliance/legal/geographic analysis.

## Level 2 Node 12 Python API

Node 12 is exposed through Python APIs:

- Configure batch-health trend reports with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendConfig(config_version="batch-health-trend-v1")`.
- Build batch-health trend reports from supplied, in-memory Node 11 `TradeProposalEvidenceComparisonHistoryBatchHealthReport` values with `build_trade_proposal_evidence_comparison_history_batch_health_trend_report(batch_health_reports, config=config, generated_at=datetime.now(UTC))`, which returns `TradeProposalEvidenceComparisonHistoryBatchHealthTrendReport`.
- Inspect trend gates with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendGateResult`, status rows with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendStatusRow`, config-version summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendConfigVersionSummary`, gate-status summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendGateStatusSummary`, duplicate-generated-at summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendDuplicateGeneratedAtSummary`, and duplicate-fingerprint summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendDuplicateFingerprintSummary`.
- Optionally append already-built batch-health trend snapshots with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendLog(path).append(report)`; there is no JSONL reader, loader, replay, or from-file API.

## Level 2 Node 13 Status

Level 2 Node 13 adds report-only proposal evidence comparison history batch-health trend-batch artifacts over supplied `TradeProposalEvidenceComparisonHistoryBatchHealthTrendReport` values. It summarizes trend status frequencies, gate status frequencies, config-version coverage, duplicate generated-at indicators, duplicate fingerprint indicators, and first/last trend-report time bounds for audit only, and it supports append-only JSONL persistence. This is no-read, no-fetch, no-outcome, no-settlement, no-ranking, no-recommendation, no-approval, and no-execution scope; it is not an approval workflow, proposal approval step, approved-proposal selector, latest-decision selector, decision-resolution process, investment ranking, trade recommendation, strategy-promotion signal, trade instruction, order instruction, broker request, order request, account action, credential workflow, external-history loader, JSONL reader, scraping workflow, outcome loader, settlement review, reconciliation process, compliance/legal/geographic analysis, realized false-positive analysis, profitability analysis, automatic order-placement authorization, or live-execution signal.

It rejects loader-shaped inputs such as paths, mappings, strings, bytes, generators, arbitrary iterables, and log-shaped objects. It does not fetch data; read logs; scrape; authenticate; handle credentials/private keys; place or cancel orders; open WebSockets; run heartbeat logic; use trading SDK/broker/execution clients; build request payloads; approve proposals; select latest decisions; resolve conflicts; rank investments; recommend trades; perform outcome/settlement/reconciliation/profitability analysis; import manual executions; or perform compliance/legal/geographic analysis.

## Level 2 Node 13 Python API

Node 13 is exposed through Python APIs:

- Configure trend-batch reports with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchConfig(config_version="batch-health-trend-batch-v1")`.
- Build trend-batch reports from supplied, in-memory Node 12 `TradeProposalEvidenceComparisonHistoryBatchHealthTrendReport` values with `build_trade_proposal_evidence_comparison_history_batch_health_trend_batch_report(trend_reports, config=config, generated_at=datetime.now(UTC))`, which returns `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchReport`.
- Inspect trend-batch gates with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchGateResult`, status rows with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchStatusRow`, config-version summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchConfigVersionSummary`, gate-status summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchGateStatusSummary`, duplicate-generated-at summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchDuplicateGeneratedAtSummary`, and duplicate-fingerprint summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchDuplicateFingerprintSummary`.
- Optionally append already-built trend-batch snapshots with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchLog(path).append(report)`; there is no JSONL reader, loader, replay, or from-file API.

## Level 2 Node 14 Status

Level 2 Node 14 adds report-only proposal evidence comparison history batch-health trend-batch health artifacts over supplied `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchReport` values. It summarizes trend-batch status frequencies, Node 13 gate-status frequencies, config-version coverage, duplicate generated-at indicators, duplicate fingerprint indicators, and first/last trend-batch report time bounds for audit only, and it supports append-only JSONL persistence. This is no-read, no-fetch, no-outcome, no-settlement, no-ranking, no-recommendation, no-approval, and no-execution scope; it is not an approval workflow, proposal approval step, approved-proposal selector, latest-decision selector, decision-resolution process, investment ranking, trade recommendation, strategy-promotion signal, trade instruction, order instruction, broker request, order request, account action, credential workflow, external-history loader, JSONL reader, scraping workflow, outcome loader, settlement review, reconciliation process, compliance/legal/geographic analysis, realized false-positive analysis, profitability analysis, automatic order-placement authorization, or live-execution signal.

It rejects loader-shaped inputs such as paths, mappings, strings, bytes, generators, arbitrary iterables, and log-shaped objects. It does not fetch data; read logs; scrape; authenticate; handle credentials/private keys; place or cancel orders; open WebSockets; run heartbeat logic; use trading SDK/broker/execution clients; build request payloads; approve proposals; select latest decisions; resolve conflicts; rank investments; recommend trades; perform outcome/settlement/reconciliation/profitability analysis; import manual executions; or perform compliance/legal/geographic analysis.

## Level 2 Node 14 Python API

Node 14 is exposed through Python APIs:

- Configure trend-batch health reports with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthConfig(config_version="batch-health-trend-batch-health-v1")`.
- Build trend-batch health reports from supplied, in-memory Node 13 `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchReport` values with `build_trade_proposal_evidence_comparison_history_batch_health_trend_batch_health_report(trend_batch_reports, config=config, generated_at=datetime.now(UTC))`, which returns `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthReport`.
- Inspect trend-batch health gates with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthGateResult`, status rows with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthStatusRow`, config-version summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthConfigVersionSummary`, gate-status summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthGateStatusSummary`, duplicate-generated-at summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthDuplicateGeneratedAtSummary`, and duplicate-fingerprint summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthDuplicateFingerprintSummary`.
- Optionally append already-built trend-batch health snapshots with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthLog(path).append(report)`; there is no JSONL reader, loader, replay, or from-file API.

## Level 2 Node 15 Status

Level 2 Node 15 adds report-only proposal evidence comparison history batch-health trend-batch health trend artifacts over supplied `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthReport` values. It summarizes trend-batch health status frequencies, Node 14 gate-status frequencies, config-version coverage, duplicate generated-at indicators, duplicate fingerprint indicators, and first/last trend-batch health report time bounds for audit only, and it supports append-only JSONL persistence. This is no-read, no-fetch, no-scrape, no-browser automation, no-account automation, no-API clients, no-outcome, no-settlement, no-reconciliation, no-ranking, no-recommendation, no-financial advice, no-approval, no-execution, no-order placement, no-JSONL readers, and no-external loaders scope; it is not an approval workflow, proposal approval step, approved-proposal selector, latest-decision selector, decision-resolution process, investment ranking, trade recommendation, strategy-promotion signal, trade instruction, order instruction, broker request, order request, account action, credential workflow, private-key handling, external-history loader, JSONL reader, scraping workflow, outcome loader, settlement review, reconciliation process, compliance/legal/geographic analysis, realized false-positive analysis, profitability analysis, automatic order-placement authorization, or live-execution signal.

It rejects loader-shaped inputs such as paths, mappings, strings, bytes, generators, arbitrary iterables, and log-shaped objects. It does not fetch data; read logs; scrape; use browser automation; use account automation; authenticate; handle credentials/private keys; place or cancel orders; open WebSockets; run heartbeat logic; use trading SDK/broker/execution clients; build request payloads; approve proposals; select latest decisions; resolve conflicts; rank investments; recommend trades; perform outcome/settlement/reconciliation/profitability analysis; import manual executions; or perform compliance/legal/geographic analysis.

## Level 2 Node 15 Python API

Node 15 is exposed through Python APIs:

- Configure trend-batch health trend reports with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthTrendConfig(config_version="batch-health-trend-batch-health-trend-v1")`.
- Build trend-batch health trend reports from supplied, in-memory Node 14 `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthReport` values with `build_trade_proposal_evidence_comparison_history_batch_health_trend_batch_health_trend_report(trend_batch_health_reports, config=config, generated_at=datetime.now(UTC))`, which returns `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthTrendReport`.
- Inspect trend-batch health trend gates with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthTrendGateResult`, status rows with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthTrendStatusRow`, config-version summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthTrendConfigVersionSummary`, gate-status summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthTrendGateStatusSummary`, duplicate-generated-at summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthTrendDuplicateGeneratedAtSummary`, and duplicate-fingerprint summaries with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthTrendDuplicateFingerprintSummary`.
- Optionally append already-built trend-batch health trend snapshots with `TradeProposalEvidenceComparisonHistoryBatchHealthTrendBatchHealthTrendLog(path).append(report)`; there is no JSONL reader, loader, replay, or from-file API.

## Proposal Evidence Comparison Artifact Registry

The proposal evidence comparison artifact registry is a static report-only registry with string metadata only. It describes the existing proposal evidence comparison artifact chain, builder names, report class names, upstream report class names, append-only-log availability, and report-only forbidden surfaces. It does not import artifact implementation modules and does not create reports.

The registry does not fetch, does not read JSONL, does not load, does not replay, does not scrape, does not use browser automation, does not use account automation, does not use API clients, does not place orders, does not rank investments, does not recommend trades, and does not provide financial advice.

Use `list_proposal_evidence_comparison_artifacts()` to inspect the static tuple and `get_proposal_evidence_comparison_artifact(artifact_id)` to look up one registry row by canonical artifact ID.

## Team Evaluation Attempt Latest Read Node 8 Status

Node 8 delivers the freeze-compliant first slice of strategy-cycle consumption: a pure read-only report over the latest persisted team-evaluation attempt. `read_latest_team_evaluation_attempt_report` and `read_latest_team_evaluation_attempt_with_psycopg` delegate to the Node 5 latest-attempt loaders, which order strictly by `attempted_at DESC, tea_id DESC` and never fall back through a newer `watch`/`blocked` attempt. `TeamEvaluationAttemptLatestReadReport` surfaces identity, status, hard-flag, publication-gate evidence, and audit-only packet projection: the row schema never persists packets, `packet_presence` is a contract-derived `projected`/`suppressed`/`None` state, `audit_packet_selected_side` is always `None`, and `audit_packet_forecast_probability_yes` exposes the Node 2 publishable probability only when projected and is never a side recommendation. Strategy-cycle provider wiring and central cost-engine side selection remain post-freeze future work.

## Priority 1 DSN-Hardening Finish Audit Status

The DSN-hardening finish audit closes the local-Supabase/Postgres DSN validation enforcement debt: every direct `psycopg.connect` operand across the 35 CLI connection sites is validated in-function through `validate_local_postgres_dsn` before connection setup, the three intermediary store/loader modules validate before sink construction and callback dispatch, and the repo-wide static gate pins that DSN environment reads occur only inside `supabase_*config.py` modules, that every discovered config module validates its DSN, and that connector references are confined to a frozen literal 60-path allowlist. The per-function AST gate enforces exact-operand, path-dominating validation across the CLI and intermediary boundaries, including definition-time regions, alias fixpoint propagation, capture timing, indirect write detection (setattr/dunder/update/IIFE/expression-callee forms), and loop-carry checks. The final external closure review for this node was blocked by reviewer-provider session failures after 30+ incremental review rounds; all coordinator-side verifications and every reviewer-supplied probe reproduction pass. See the handoff record for the exact gate status.

## Crypto BTC Forecast Service Node 7 Status

Crypto BTC Forecast Service Node 7 adds a paper-only, report-only, and readonly BTC forecast service. It evaluates Node 6 BTC policy evidence, reduces it through the Node 2C team evidence aggregation, applies the combined BTC-policy and aggregation readiness publication gate, builds the Node 3 team forecast build envelope, and persists every representable validated attempt through Node 5's sole atomic local Supabase/Postgres writer into `team_evaluation_attempts`. The service-level `publication_status` is `blocked` when either the policy or the aggregation status is blocked, `watch` when neither is blocked and either is watch, and `ready` only when both are ready; `reason_codes` is the sorted, duplicate-free union of the Node 6 policy and Node 2 aggregation reason codes. The persisted Node 5 row keeps exposing the canonical Node 2 aggregation status; the service-level combined status is returned separately.

The service is orchestration only: no SQL, no direct database adapter, no environment reads, no loaders, and no alternate persistence path. Persistence is local paper-attempt evidence storage only: the service requires a supplied `SupabaseTeamEvidenceAggregationConfig` with `enabled=True` and a DSN, forwards only that DSN, a one-envelope tuple, and the table name to `insert_team_evaluation_attempts_with_psycopg`, and never authorizes execution. It does not fetch market data, read accounts, authenticate, handle wallets or private keys, place, sign, submit, cancel, or replace orders, open user WebSockets, or mutate exchange state; `paper_only=True`, `report_only=True`, and `readonly=True` are hard-enforced on the input and result. There is no CLI command for this service in Node 7. Strategy-cycle consumption of persisted attempts, latest-attempt reading, and central cost-engine side selection remain separately reviewed future work; this section is not execution authorization, remote publication, or full program release closure.

## Crypto BTC Forecast Service Node 7 Python API

The service is exposed through Python APIs:

- Build one service input with `CryptoBtcForecastServiceInput(...)` covering the market identifiers, the Node 6 resolution contract and incident gates, evidence inputs, evaluation scope, run metadata, evaluator receipts, legacy packet templates, and the `SupabaseTeamEvidenceAggregationConfig` persistence config.
- Evaluate, gate, envelope, and persist exactly one paper attempt with `evaluate_and_persist_crypto_btc_forecast(service_input)`, which returns `CryptoBtcForecastServiceResult`.
- Inspect the combined gate with the result's `publication_status` and `reason_codes`, alongside the preserved Node 6 `policy_evaluation`, Node 2 `aggregation_result`, Node 3 `envelope`, and Node 5 `write_results`.
- Tests may inject deterministic `evaluator` and `writer` callables; injection never creates an alternate production persistence path.

## Automation Roadmap

The recommended staged path is:

1. Level 0: read-only data ingestion, normalization, and market scoring.
2. Level 1: automated research packets and bid/ask paper trading.
3. Level 2: AI-generated trade proposals with explicit human approval.
4. Level 3: small, risk-limited live pilots for whitelisted strategies.
5. Level 4: strategy-specific execution research only after separate pilot gates are met.

See:

- `docs/superpowers/specs/2026-06-13-automated-investment-roadmap.md`
- `docs/research/validation-gates.md`
- `docs/handoff-2026-09-11.md` for the current delivered-node inventory, verification evidence, and recorded gate blockers.

## Repository Layout

```text
.
├── AGENTS.md
├── README.md
├── docs
│   ├── research
│   │   ├── data-api-research.md
│   │   ├── risk-system-research.md
│   │   ├── strategy-research.md
│   │   └── validation-gates.md
│   ├── sources.md
│   └── superpowers
│       ├── plans
│       │   ├── 2026-06-13-level-0-market-intelligence.md
│       │   ├── 2026-06-13-level-1-research-packets-paper-trading.md
│       │   ├── 2026-06-13-level-1b-paper-analytics-risk-exposure.md
│       │   ├── 2026-06-13-level-1b-paper-forecast-evidence.md
│       │   ├── 2026-06-13-level-1b-paper-analytics-history-validation.md
│       │   ├── 2026-06-13-level-1b-rejections-risk-gates.md
│       │   ├── 2026-06-13-level-1b-positions-nav.md
│       │   ├── 2026-06-14-level-1b-paper-manual-review-queue.md
│       │   ├── 2026-06-14-level-2-proposal-packets.md
│       │   ├── 2026-06-15-level-2-proposal-evidence-comparison.md
│       │   ├── 2026-06-15-level-2-proposal-evidence-comparison-history.md
│       │   ├── 2026-06-15-level-2-proposal-evidence-comparison-history-batch-health.md
│       │   ├── 2026-06-15-level-2-proposal-evidence-comparison-history-batch-health-trend.md
│       │   ├── 2026-06-15-level-2-proposal-evidence-comparison-history-batch-health-trend-batch.md
│       │   ├── 2026-06-15-level-2-proposal-evidence-comparison-history-batch-health-trend-batch-health.md
│       │   ├── 2026-06-15-level-2-proposal-evidence-comparison-history-batch-health-trend-batch-health-trend.md
│       │   ├── 2026-06-15-level-2-proposal-review-coverage.md
│       │   ├── 2026-06-15-level-2-proposal-review-dossier-batch-health.md
│       │   ├── 2026-06-15-level-2-proposal-review-dossier.md
│       │   ├── 2026-06-14-level-2-proposal-review-diagnostics.md
│       │   ├── 2026-06-14-level-2-proposal-review-records.md
│       │   ├── 2026-06-14-level-2-proposal-review-quality-gates.md
│       │   ├── 2026-06-14-level-2-proposal-review-summary-reports.md
│       │   ├── 2026-06-16-cost-aware-event-strategy-v0.md
│       │   ├── 2026-06-17-phase-2-calibration-segmentation-v0.md
│       │   ├── 2026-06-16-project-screening-v0.md
│       │   └── 2026-06-13-project-bootstrap.md
│       └── specs
│           ├── 2026-06-13-automated-investment-roadmap.md
│           ├── 2026-06-16-cost-aware-event-strategy-v0.md
│           ├── 2026-06-17-phase-2-calibration-segmentation-v0.md
│           ├── 2026-06-16-project-screening-v0.md
│           └── 2026-06-13-polymarket-alpha-lab-design.md
├── pyproject.toml
├── src
│   └── polymarket_alpha_lab
│       ├── __init__.py
│       ├── analytics.py
│       ├── analytics_history.py
│       ├── api.py
│       ├── archive.py
│       ├── cli.py
│       ├── cost_aware_event_strategy.py
│       ├── domain.py
│       ├── forecast_evidence.py
│       ├── journal.py
│       ├── local_observability_trends.py
│       ├── manual_review_queue.py
│       ├── normalize.py
│       ├── paper.py
│       ├── pipeline.py
│       ├── positions.py
│       ├── project_screening.py
│       ├── proposal_evidence_comparison.py
│       ├── proposal_evidence_comparison_history.py
│       ├── proposal_evidence_comparison_history_batch_health.py
│       ├── proposal_evidence_comparison_history_batch_health_trend.py
│       ├── proposal_evidence_comparison_history_batch_health_trend_batch.py
│       ├── proposal_evidence_comparison_history_batch_health_trend_batch_health.py
│       ├── proposal_evidence_comparison_history_batch_health_trend_batch_health_trend.py
│       ├── proposal_packet.py
│       ├── proposal_review.py
│       ├── proposal_review_coverage.py
│       ├── proposal_review_dossier_batch.py
│       ├── proposal_review_dossier.py
│       ├── proposal_review_diagnostics.py
│       ├── proposal_review_quality.py
│       ├── proposal_review_summary.py
│       ├── rejections.py
│       ├── research.py
│       ├── risk.py
│       └── scoring.py
└── tests
    ├── test_analytics.py
    ├── test_analytics_history.py
    ├── test_analytics_history_scope.py
    ├── test_analytics_scope.py
    ├── test_api.py
    ├── test_archive.py
    ├── test_cli.py
    ├── test_cost_aware_event_strategy.py
    ├── test_cost_aware_event_strategy_scope.py
    ├── test_domain.py
    ├── test_forecast_evidence.py
    ├── test_forecast_evidence_scope.py
    ├── test_init.py
    ├── test_journal.py
    ├── test_local_observability_trends.py
    ├── test_local_observability_trends_report_validation.py
    ├── test_local_observability_trends_runner.py
    ├── test_local_observability_trends_scope.py
    ├── test_manual_review_queue.py
    ├── test_manual_review_queue_scope.py
    ├── test_normalize.py
    ├── test_paper.py
    ├── test_pipeline.py
    ├── test_positions.py
    ├── test_project_screening.py
    ├── test_project_screening_scope.py
    ├── test_proposal_evidence_comparison.py
    ├── test_proposal_evidence_comparison_scope.py
    ├── test_proposal_evidence_comparison_history.py
    ├── test_proposal_evidence_comparison_history_scope.py
    ├── test_proposal_evidence_comparison_history_batch_health.py
    ├── test_proposal_evidence_comparison_history_batch_health_scope.py
    ├── test_proposal_evidence_comparison_history_batch_health_trend.py
    ├── test_proposal_evidence_comparison_history_batch_health_trend_scope.py
    ├── test_proposal_evidence_comparison_history_batch_health_trend_batch.py
    ├── test_proposal_evidence_comparison_history_batch_health_trend_batch_scope.py
    ├── test_proposal_evidence_comparison_history_batch_health_trend_batch_health.py
    ├── test_proposal_evidence_comparison_history_batch_health_trend_batch_health_scope.py
    ├── test_proposal_evidence_comparison_history_batch_health_trend_batch_health_trend.py
    ├── test_proposal_evidence_comparison_history_batch_health_trend_batch_health_trend_scope.py
    ├── test_proposal_packet.py
    ├── test_proposal_packet_scope.py
    ├── test_proposal_review.py
    ├── test_proposal_review_coverage.py
    ├── test_proposal_review_coverage_scope.py
    ├── test_proposal_review_dossier_batch.py
    ├── test_proposal_review_dossier_batch_scope.py
    ├── test_proposal_review_dossier.py
    ├── test_proposal_review_dossier_scope.py
    ├── test_proposal_review_diagnostics.py
    ├── test_proposal_review_diagnostics_scope.py
    ├── test_proposal_review_quality.py
    ├── test_proposal_review_quality_scope.py
    ├── test_proposal_review_summary.py
    ├── test_proposal_review_summary_scope.py
    ├── test_proposal_review_scope.py
    ├── test_rejections.py
    ├── test_research.py
    ├── test_risk_gates.py
    └── test_scoring.py
```

## Useful Commands

For Bash commands and setup details, see the [Linux local setup guide](docs/development/linux-local-setup.md). From the repository root:

```bash
uv sync --locked --extra dev --extra postgres --python 3.12.14
uv pip check --python .venv/bin/python
.venv/bin/python -I -m polymarket_alpha_lab --help
.venv/bin/polymarket-alpha-lab report-discovery --category readiness
.venv/bin/python scripts/verify_local.py --quick
```

Use `--full` instead of `--quick` for the complete offline pytest suite. The `postgres` extra is optional for offline setup; the reference environment includes it. Real database smoke stays disabled during verification.

Where the CodeGraph CLI is installed:

```bash
codegraph status .
codegraph files
```

After CodeGraph is initialized, use it before text search when locating source:

```bash
codegraph explore "MarketSnapshot MarketScore"
codegraph node src/polymarket_alpha_lab/domain.py
```
