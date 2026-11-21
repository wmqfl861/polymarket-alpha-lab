# Phase 1 Report Registry

Operator-facing registry for the `report-discovery` CLI node. This document records
only the reports exposed through `report-discovery`; it is not a full inventory of
all Phase 1 report modules.

## Discovery Surface

`polymarket-alpha-lab report-discovery` prints read-only paper/report-only
operator entrypoints grouped into three categories:

- `readiness`: Phase 1 readiness reports for operator go/no-go review.
- `strategy-rollups`: critical strategy rollups for selection, agreement, risk,
  and queue posture.
- `manual-review`: next-step packet commands for operator manual review.

The discovery surface is intentionally narrow. Its help exposes only
`--category` and `--format`; it must not expose persistence, DSN/table, input,
output, live, auth, wallet, account, private-key, order, execution, or trading
flags.

## Registry Columns

- **Command**: CLI entry exposed by `report-discovery`.
- **Operator use**: why an operator would run it.
- **Input object**: already-built report, persisted local report history, or
  aggregate source object consumed by the node.
- **Output status**: status field an operator should read first.
- **Manual next step**: field that tells the operator what to do next, when the
  report exposes one.
- **Tests**: primary tests that pin the command, reducer, payload, or boundary.
- **Read-only boundary**: no live trading, no exchange mutation, and no sensitive
  execution/auth surface.

## Readiness

| Command | Operator use | Input object | Output status | Manual next step | Tests | Read-only boundary |
| --- | --- | --- | --- | --- | --- | --- |
| `paper-autonomous-readiness-digest` | Summarize Phase 1 autonomous paper readiness for operator review. | Local readiness-gate history plus optional pure gate evidence loaded through env-only local Supabase/Postgres readback. | `digest_status` (`pass`, `watch`, `blocked`). | `recommended_next_review_action`. | `tests/test_cli_paper_autonomous_readiness_digest.py`; `tests/test_cli_paper_autonomous_readiness_digest_scope.py`; `tests/test_paper_autonomous_readiness_digest_psycopg.py`. | Default run writes nothing; optional `--persist` stores only the already-built digest using readiness-digest DB env. No DSN/table CLI flags, live/auth/wallet/account/private-key/order/execution flags, or exchange mutation. |
| `team-memory-readiness-digest` | Decide whether long-term team-memory evidence is usable, throttled, or blocked for paper research. | `TeamMemoryReadinessDigestSource` values wrapping team diagnostics snapshot history gate reports. | `digest_status` (`pass`, `watch`, `blocked`). | `recommended_next_step` (`allow_team_memory_readiness_use`, `throttle_team_memory_readiness_use`, `block_team_memory_readiness_use`). | `tests/test_team_memory_readiness_digest.py`; `tests/test_cli_team_memory_readiness_digest.py`; `tests/test_team_memory_readiness_digest_db_source.py`; `tests/test_team_memory_readiness_digest_config.py`. | Reads diagnostics snapshot history through existing env-only local DB config; hard flags stay `paper_only=True`, `report_only=True`, `readonly=True`. No live trading, account reads, wallet/private-key handling, order path, strategy-weight tuning, position sizing, or financial advice. |
| `paper-autonomous-allocation-proposal-db-history-health-trend-gate` | Gate whether allocation-proposal DB-history health trend is stable enough for paper readiness review. | Persisted allocation-proposal DB-history health reports reduced into a health trend, then into a gate report. | `gate_status` (`pass`, `watch`, `blocked`). | `recommended_next_step`. | `tests/test_paper_autonomous_allocation_proposal_db_history_health_trend_gate.py`; `tests/test_paper_autonomous_allocation_proposal_db_history_health_trend_gate_load.py`; `tests/test_docs_paper_autonomous_allocation_proposal_scope.py`. | Env-only, read-only, paper-only/report-only/readonly, and no-write. Accepts only `--limit`; does not read upstream screening/queue tables, place orders, approve execution, read accounts, or mutate exchange state. |

## Strategy Rollups

| Command | Operator use | Input object | Output status | Manual next step | Tests | Read-only boundary |
| --- | --- | --- | --- | --- | --- | --- |
| `probability-selection-scorer-agreement-trend-gate` | Check whether probability selection and scorer agreement trends are stable enough to feed paper readiness evidence. | Persisted aggregate agreement reports reduced into `ProbabilitySelectionScorerAgreementTrendReport`, then `ProbabilitySelectionScorerAgreementTrendGateReport`. | `gate_status` (`pass`, `watch`, `blocked`). | `recommended_next_step`. | `tests/test_probability_selection_scorer_agreement_trend_gate_store.py`; `tests/test_cli_paper_autonomous_readiness_digest.py`; `tests/test_cli_report_discovery.py`. | Env-only readback over persisted aggregate agreement reports; no source rows, market slugs, condition ids, wallets, auth material, private keys, or order-like data. The gate is observability evidence only, not execution authorization. |
| `paper-probability-selection-summary-history-trend-gate` | Review whether probability selection summary history is stable, fresh, and thick enough for paper review. | Persisted probability selection summary history reports reduced into a history trend, then gate report. | `gate_status` (`pass`, `watch`, `blocked`). | `recommended_next_step` such as `throttle_probability_selection_summary_history_trend_review` for watch cases. | `tests/test_cli_paper_probability_selection_summary_history_trend_gate.py`. | Env-only, read-only, paper-only/report-only/readonly. Accepts only `--limit`; no persist, DSN/table/file, wallet, auth, order, live, execution, account, private-key, or fast-mode flags. |
| `paper-recommendation-cycle-action-gate` | Roll up recommendation cycle review output into an action-gated paper queue status. | Recommendation cycle DB review/history report summarized by `PaperRecommendationCycleActionGateReport`. | `action_status` plus `review_status` and `latest_final_status`. | `recommended_next_step` printed as `next_step`. | `tests/test_paper_recommendation_cycle_action_gate.py`; `tests/test_action_gated_strategy_recommendation_queue_decision_support_trend.py`; `tests/test_action_gated_strategy_recommendation_queue_risk.py`. | Paper-only/report-only/readonly decision support. Requires local DB evidence but must not expose live/auth/wallet/account/private-key/order/trading behavior or mutate exchange state. |
| `action-gated-queue-decision-support-trend` | Summarize action-gated queue decision-support snapshots for risk drift and queue posture. | Persisted action-gated queue decision-support source snapshots reduced into `PaperActionGatedStrategyRecommendationQueueDecisionSupportTrendReport`. | `latest_risk_status` with pass/watch/blocked counts. | No direct next-step field on the trend; use `risk_next_step` from source decision-support output when reviewing upstream risk. | `tests/test_action_gated_strategy_recommendation_queue_decision_support_trend.py`; `tests/test_supabase_action_gated_strategy_recommendation_queue_decision_support_trend_config.py`. | Reads already-persisted decision-support reports from local source DB and prints redacted aggregate trend output. Optional persistence is explicit and env-gated; no DSN/table CLI flags, live trading, exchange mutation, or investment instruction. |

## Manual Review

| Command | Operator use | Input object | Output status | Manual next step | Tests | Read-only boundary |
| --- | --- | --- | --- | --- | --- | --- |
| `paper-research-packet-operator-flow` | Build the operator packet flow across packet generation, packet quality, and quality history for manual review triage. | Strategy candidate research queue rows, paper research packet report, packet quality report, and packet quality history report loaded through env-only local DB config. | `flow_status`, plus `quality_status` and `history_status`. | `reason_codes` indicate the manual review reason path; no separate next-step field is exposed by the flow report. | `tests/test_cli_paper_research_packet_operator_flow.py`; `tests/test_cli_paper_research_packet_operator_flow_scope.py`; `tests/test_paper_research_packet_operator_flow_psycopg.py`. | Generates/persists paper evidence only when explicit env-gated persistence is enabled. No DSN/table CLI flags, authentication, wallets/private keys, live trading, order placement/signing/submission/cancellation, trade instructions, or financial advice. |
| `paper-research-packet-quality` | Quality-check the current paper research packet before operator review. | `PaperResearchPacketQualityReport` built from a paper research packet source. | `quality_status` with check counts and pass/watch/blocked counts. | `reason_code_counts` and row-level `reason_codes` drive manual triage; no separate next-step field is exposed. | `tests/test_paper_research_packet_quality.py`; `tests/test_cli_paper_research_packet_quality.py`; `tests/test_cli_paper_research_packet_quality_scope.py`; `tests/test_paper_research_packet_quality_db_row.py`. | Pure quality reducer is paper-only/report-only/readonly. CLI persistence is optional and env-only; no live trading, auth, wallet/account/private-key handling, order path, or exchange mutation. |
| `paper-research-packet-operator-flow-db-history-gate` | Decide whether persisted operator-flow history is stable enough for downstream paper screening. | Persisted operator-flow DB-history reports reduced into a history report, then `PaperResearchPacketOperatorFlowDbHistoryGateReport`. | `gate_status` (`pass`, `watch`, `blocked`). | `recommended_next_step`. | `tests/test_cli_paper_research_packet_operator_flow_db_history_gate.py`; `tests/test_paper_research_packet_operator_flow_db_history_gate_load.py`; `tests/test_paper_autonomous_screening_decision_support_gate_db_history_health.py`. | Env-only, read-only, paper-only/report-only/readonly. Accepts only `--limit`; does not accept DSN/table/persist flags, write reports, place orders, sign messages, read wallets/accounts, or mutate exchange state. |

## Review Notes

Follow [Project Agent Routing](../../AGENTS.md#project-agent-routing)
for current registry changes.

- `tests/test_cli_report_discovery.py` is the registry contract: it asserts the
  three discovery categories, the exposed commands, category filtering, help
  flags, and absence of DB execution/live-trading identifiers in the discovery
  branch.
- All registry entries must remain operator-facing evidence surfaces. A `pass`
  status means "eligible for the next paper review step" only; it is not
  permission to trade, not investment advice, not an approval workflow, and not
  execution authorization.
- Public payloads and stdout should remain aggregate-only and redacted. Do not
  surface market slugs, condition ids, questions, source rows, DB internals,
  wallets, accounts, auth material, private keys, or order-like data.
- Any future registry addition should include: command, category, input object,
  output status field, manual next-step field or explicit absence, test file,
  and the no-live/no-auth/no-execution boundary.
