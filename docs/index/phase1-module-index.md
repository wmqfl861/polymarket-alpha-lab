# Phase 1 Module Index

Date: 2026-07-12
Status: documentation index for current Phase 1 added capabilities
Scope: docs-only index node

## Purpose

This index maps the current Phase 1 module surface to the tests that document
and protect each capability area. It is an operator and reviewer navigation
aid. It does not authorize source changes, database migrations, CLI behavior
changes, Supabase changes, execution/auth work, or live-trading work.

Phase 1 remains paper-only, report-only, and readonly. The modules listed here
may assemble research evidence, score paper/report readiness, format operator
packets, and expose local persistence/readback boundaries where already
implemented. They must not read accounts, handle credentials, sign orders,
submit orders, cancel orders, replace orders, route orders, mutate exchange
state, or convert paper evidence into automated live execution.

Durable project persistence remains local Supabase/Postgres only. Any module
with durable DB read/write behavior must preserve the local Supabase principle:
raw DSNs are validated through `validate_local_postgres_dsn`, local Supabase is
the only approved durable project-data target, and legacy file-backed surfaces
remain compatibility, export, replay, or read-only input boundaries unless a
reviewed migration explicitly moves them to local Supabase/Postgres.

## Market Discovery

Market discovery modules identify candidate markets and event context for
research triage. They are research intake surfaces only; discovery output is
not a trade recommendation, order intent, or execution queue.

| Module | Test | Phase 1 role |
| --- | --- | --- |
| `src/polymarket_alpha_lab/market_discovery_candidate_pool.py` | `tests/test_market_discovery_candidate_pool.py` | Builds a normalized candidate pool for market research and screening. |
| `src/polymarket_alpha_lab/market_event_time_decay_priority.py` | `tests/test_market_event_time_decay_priority.py` | Scores time-to-resolution pressure for candidate prioritization. |
| `src/polymarket_alpha_lab/market_event_time_decay_priority_report.py` | `tests/test_market_event_time_decay_priority_report.py` | Produces report-only time-decay priority rows. |
| `src/polymarket_alpha_lab/market_probability_context_refresh_pressure_report.py` | `tests/test_market_probability_context_refresh_pressure_report.py` | Flags markets whose probability context needs readonly refresh review. |
| `src/polymarket_alpha_lab/event_taxonomy_health.py` | `tests/test_event_taxonomy_health.py` | Reports taxonomy health for event classification and routing readiness. |
| `src/polymarket_alpha_lab/event_archetype_registry_health.py` | `tests/test_event_archetype_registry_health.py` | Reports archetype registry completeness for recurring market types. |

## Forecast Context

Forecast context modules validate whether supplied market evidence, forecast
inputs, and confidence metadata are ready for paper/report review. They do not
size positions, approve execution, or fetch authenticated account state.

| Module | Test | Phase 1 role |
| --- | --- | --- |
| `src/polymarket_alpha_lab/forecast_context_readiness_report.py` | `tests/test_forecast_context_readiness_report.py` | Reports whether forecast context is complete enough for operator review. |
| `src/polymarket_alpha_lab/forecast_confidence_backtest_readiness.py` | `tests/test_forecast_confidence_backtest_readiness.py` | Checks whether confidence evidence is backtest-ready. |
| `src/polymarket_alpha_lab/forecast_confidence_drift_monitor.py` | `tests/test_forecast_confidence_drift_monitor.py` | Monitors forecast confidence drift as a paper/report diagnostic. |
| `src/polymarket_alpha_lab/forecast_ensemble_quality.py` | `tests/test_forecast_ensemble_quality.py` | Summarizes ensemble forecast quality and reason-code pressure. |
| `src/polymarket_alpha_lab/forecast_revision_quality_gate.py` | `tests/test_forecast_revision_quality_gate.py` | Gates forecast revisions for traceability and quality. |
| `src/polymarket_alpha_lab/market_forecast_confidence_recheck_digest.py` | `tests/test_market_forecast_confidence_recheck_digest.py` | Digest for markets needing forecast confidence recheck. |
| `src/polymarket_alpha_lab/market_forecast_evidence_alignment_digest.py` | `tests/test_market_forecast_evidence_alignment_digest.py` | Checks alignment between market forecast and evidence. |
| `src/polymarket_alpha_lab/market_forecast_source_recency_consensus_digest.py` | `tests/test_market_forecast_source_recency_consensus_digest.py` | Combines source recency and forecast consensus diagnostics. |

## Source Coverage

Source coverage modules make evidence completeness auditable before a candidate
can move into cost, risk, or manual review. Missing sources and weak source
classes create research gaps, not execution instructions.

| Module | Test | Phase 1 role |
| --- | --- | --- |
| `src/polymarket_alpha_lab/research_source_discovery_coverage_sla_report.py` | `tests/test_research_source_discovery_coverage_sla_report.py` | Reports source-class discovery coverage, missing-class pressure, parse readiness, retry backlog, and manual review urgency. [B1 · deprecated-pending-prune](../handoffs/m3-cleanup/pr-b-20261120/B1-status.json) |
| `src/polymarket_alpha_lab/evidence_queue_traceability_audit.py` | `tests/test_evidence_queue_traceability_audit.py` | Audits evidence queue traceability for review readiness. |
| `src/polymarket_alpha_lab/evidence_traceability_sla_monitor.py` | `tests/test_evidence_traceability_sla_monitor.py` | Monitors evidence traceability SLA status. |
| `src/polymarket_alpha_lab/market_candidate_due_diligence_depth_report.py` | `tests/test_market_candidate_due_diligence_depth_report.py` | Reports due-diligence depth for candidate markets. |
| `src/polymarket_alpha_lab/market_candidate_information_gap_report.py` | `tests/test_market_candidate_information_gap_report.py` | Surfaces candidate information gaps requiring readonly research. |
| `src/polymarket_alpha_lab/market_event_source_family_divergence_report.py` | `tests/test_market_event_source_family_divergence_report.py` | Reports divergence across source families. |
| `src/polymarket_alpha_lab/market_outcome_evidence_quality_digest.py` | `tests/test_market_outcome_evidence_quality_digest.py` | Summarizes outcome evidence quality for settled or closing markets. |
| `src/polymarket_alpha_lab/outcome_resolution_evidence_readiness_report.py` | `tests/test_outcome_resolution_evidence_readiness_report.py` | Verifies that outcome-resolution evidence is complete enough for readonly review. |
| `src/polymarket_alpha_lab/source_scraping_tool_coverage_readiness_report.py` | `tests/test_source_scraping_tool_coverage_readiness_report.py` | Reports public-source tool coverage and manual research gaps. |

## Freshness SLA

Freshness SLA modules expose stale market, source, outcome, and settlement
evidence before operator review. Freshness failures should route to refresh,
watch, research, or block status; they must not trigger live exchange mutation.

| Module | Test | Phase 1 role |
| --- | --- | --- |
| `src/polymarket_alpha_lab/market_context_source_freshness_ladder_report.py` | `tests/test_market_context_source_freshness_ladder_report.py` | Builds a ladder of source freshness status from public payloads or observations. |
| `src/polymarket_alpha_lab/research_market_cost_input_refresh_sla_report.py` | `tests/test_research_market_cost_input_refresh_sla_report.py` | Reports whether cost inputs are fresh enough for paper cost review. [B1 · deprecated-pending-prune](../handoffs/m3-cleanup/pr-b-20261120/B1-status.json) |
| `src/polymarket_alpha_lab/research_market_settlement_cost_refresh_report.py` | `tests/test_research_market_settlement_cost_refresh_report.py` | Reports settlement-cost refresh age and stale settlement-cost pressure. [B1 · deprecated-pending-prune](../handoffs/m3-cleanup/pr-b-20261120/B1-status.json) |
| `src/polymarket_alpha_lab/information_freshness_refresh_sla_readiness_report.py` | `tests/test_information_freshness_refresh_sla_readiness_report.py` | Checks readiness of information refresh SLAs. |
| `src/polymarket_alpha_lab/market_data_freshness_guard.py` | `tests/test_market_data_freshness_guard.py` | Guards market data freshness for paper/report calculations. |
| `src/polymarket_alpha_lab/market_data_refresh_failure_fallback_report.py` | `tests/test_market_data_refresh_failure_fallback_report.py` | Reports safe fallback status when market data refresh fails. |
| `src/polymarket_alpha_lab/market_event_update_freshness_digest.py` | `tests/test_market_event_update_freshness_digest.py` | Digests event update freshness pressure. |
| `src/polymarket_alpha_lab/market_candidate_information_freshness_score_report.py` | `tests/test_market_candidate_information_freshness_score_report.py` | Scores candidate information freshness for review. |

## Cost, EV, And Kelly

Cost, EV, and Kelly modules keep edge analysis side-aware and cost-aware for
paper/report use. In Phase 1, Kelly-style sizing output is a diagnostic or
position-sizing report surface only; it is not capital allocation, live order
sizing, or execution authorization.

| Module | Test | Phase 1 role |
| --- | --- | --- |
| `src/polymarket_alpha_lab/cost_aware_probability_edge_decision_gate_report.py` | `tests/test_cost_aware_probability_edge_decision_gate_report.py` | Gates probability edge after cost, liquidity, and uncertainty review. |
| `src/polymarket_alpha_lab/cost_aware_probability_position_sizing_report.py` | `tests/test_cost_aware_probability_position_sizing_report.py` | Reports paper-only cost-aware probability sizing diagnostics. |
| `src/polymarket_alpha_lab/candidate_decision_ev_sensitivity_score.py` | `tests/test_candidate_decision_ev_sensitivity_score.py` | Scores EV sensitivity for candidate decision review. |
| `src/polymarket_alpha_lab/candidate_decision_cost_liquidity_adapter.py` | `tests/test_candidate_decision_cost_liquidity_adapter.py` | Adapts cost/liquidity evidence into candidate decision inputs. |
| `src/polymarket_alpha_lab/candidate_decision_cost_thresholds.py` | `tests/test_candidate_decision_cost_thresholds.py` | Encodes cost threshold diagnostics for candidate decisions. |
| `src/polymarket_alpha_lab/market_event_liquidity_cost_slippage_surface_v2.py` | `tests/test_market_event_liquidity_cost_slippage_surface_v2.py` | Reports event-level liquidity, cost, and slippage surface. |
| `src/polymarket_alpha_lab/market_microstructure_execution_cost_readiness_report.py` | `tests/test_market_microstructure_execution_cost_readiness_report.py` | Reports execution-cost readiness as paper microstructure evidence. |
| `src/polymarket_alpha_lab/market_microstructure_slippage_stress_readiness_report.py` | `tests/test_market_microstructure_slippage_stress_readiness_report.py` | Reports slippage stress readiness for paper review. |
| `src/polymarket_alpha_lab/cost_adjusted_edge_decay_monitor.py` | `tests/test_cost_adjusted_edge_decay_monitor.py` | Monitors decay in cost-adjusted edge. |
| `src/polymarket_alpha_lab/cost_edge_decay_response_queue.py` | `tests/test_cost_edge_decay_response_queue.py` | Queues paper/report responses to cost-edge decay. |
| `src/polymarket_alpha_lab/probability_event_cost_adjusted_position_recommendation_report.py` | `tests/test_probability_event_cost_adjusted_position_recommendation_report.py` | Produces a cost-adjusted paper position recommendation for operator review. |
| `src/polymarket_alpha_lab/probability_event_recommendation_rank_explainability_report.py` | `tests/test_probability_event_recommendation_rank_explainability_report.py` | Explains deterministic recommendation ranking without execution behavior. |

## Portfolio Risk

Portfolio risk modules describe paper exposure, concentration, dependency, and
settlement/cash-drag pressure. They can throttle paper/report promotion or
raise operator review needs, but they do not manage live portfolios or mutate
accounts.

| Module | Test | Phase 1 role |
| --- | --- | --- |
| `src/polymarket_alpha_lab/candidate_portfolio_exposure_throttle.py` | `tests/test_candidate_portfolio_exposure_throttle.py` | Throttles candidate promotion based on paper exposure pressure. |
| `src/polymarket_alpha_lab/market_event_cluster_exposure_report.py` | `tests/test_market_event_cluster_exposure_report.py` | Reports clustered event exposure. |
| `src/polymarket_alpha_lab/exposure_concentration_monitor.py` | `tests/test_exposure_concentration_monitor.py` | Monitors concentration pressure in paper exposure. |
| `src/polymarket_alpha_lab/concentration_response_queue.py` | `tests/test_concentration_response_queue.py` | Queues paper/report responses to concentration pressure. |
| `src/polymarket_alpha_lab/candidate_settlement_cash_drag_score.py` | `tests/test_candidate_settlement_cash_drag_score.py` | Scores settlement cash drag as candidate risk evidence. |
| `src/polymarket_alpha_lab/market_event_dependency_cluster_digest.py` | `tests/test_market_event_dependency_cluster_digest.py` | Digests dependency clusters across market events. |
| `src/polymarket_alpha_lab/market_event_resolution_dependency_digest.py` | `tests/test_market_event_resolution_dependency_digest.py` | Digests resolution dependency pressure. |
| `src/polymarket_alpha_lab/market_liquidity_exit_risk_readiness_report.py` | `tests/test_market_liquidity_exit_risk_readiness_report.py` | Reports exit-liquidity risk readiness. |
| `src/polymarket_alpha_lab/portfolio_probability_event_readiness_report.py` | `tests/test_portfolio_probability_event_readiness_report.py` | Aggregates probability-event portfolio readiness and paper risk pressure. |
| `src/polymarket_alpha_lab/probability_event_market_signal_risk_readiness_report.py` | `tests/test_probability_event_market_signal_risk_readiness_report.py` | Reduces market-signal evidence into readonly risk readiness. |

## Team Routing

Team routing modules assign research responsibility and specialist context.
Teams own research, forecasts, evidence, memory notes, and diagnostics only.
The central layer continues to own cost, risk, recommendation reducers, paper
allocation evidence, outcomes, and comparable reporting.

| Module | Test | Phase 1 role |
| --- | --- | --- |
| `src/polymarket_alpha_lab/team_market_router.py` | `tests/test_team_market_router.py` | Routes markets to primary and secondary specialist teams. |
| `src/polymarket_alpha_lab/market_category_specialist_assignment_packet.py` | `tests/test_market_category_specialist_assignment_packet.py` | Packages specialist assignment context for market categories. |
| `src/polymarket_alpha_lab/market_category_specialist_signal_capacity_v2.py` | `tests/test_market_category_specialist_signal_capacity_v2.py` | Reports specialist signal capacity by market category. |
| `src/polymarket_alpha_lab/category_research_capacity_heatmap.py` | `tests/test_category_research_capacity_heatmap.py` | Produces category research capacity heatmap diagnostics. |
| `src/polymarket_alpha_lab/domain_specialist_research_queue_health_report.py` | `tests/test_domain_specialist_research_queue_health_report.py` | Reports specialist research queue health. |
| `src/polymarket_alpha_lab/domain_specialist_signal_blend_report.py` | `tests/test_domain_specialist_signal_blend_report.py` | Blends specialist signals for report-only comparison. |
| `src/polymarket_alpha_lab/cross_team_disagreement_escalation_report.py` | `tests/test_cross_team_disagreement_escalation_report.py` | Escalates cross-team disagreement for operator review. |
| `src/polymarket_alpha_lab/domain_team_memory_scorecard.py` | `tests/test_domain_team_memory_scorecard.py` | Scores team memory quality as research context. |
| `src/polymarket_alpha_lab/category_playbook_category_threshold_domain_policy_readiness.py` | `tests/test_category_playbook_category_threshold_domain_policy_readiness.py` | Checks category thresholds and domain-policy readiness for specialist routing. |
| `src/polymarket_alpha_lab/specialist_team_routing_taxonomy_readiness_report.py` | `tests/test_specialist_team_routing_taxonomy_readiness_report.py` | Reports taxonomy readiness for specialist-team routing. |
| `src/polymarket_alpha_lab/crypto_btc_forecast_service.py` | `tests/test_crypto_btc_forecast_service.py` | Orchestrates the paper-only crypto BTC forecast service: policy evaluation, combined publication gate, and local Supabase/Postgres paper-attempt persistence. |
| `src/polymarket_alpha_lab/team_evaluation_attempt_latest_read.py` | `tests/test_team_evaluation_attempt_latest_read.py` | Reports the latest persisted team-evaluation attempt with no-fallback latest-row ordering, publication-gate evidence, and audit-only packet projection. |

## Manual Go/No-Go

Manual go/no-go modules assemble operator-facing packets and safety checks.
They support human review and attestation. They must not submit, sign, cancel,
replace, route, or otherwise mutate orders.

| Module | Test | Phase 1 role |
| --- | --- | --- |
| `src/polymarket_alpha_lab/manual_decision_go_no_go_gate_report.py` | `tests/test_manual_decision_go_no_go_gate_report.py` | Reports whether a candidate is ready for manual go/no-go review. |
| `src/polymarket_alpha_lab/manual_operator_decision_packet.py` | `tests/test_manual_operator_decision_packet.py` | Assembles the manual operator packet from paper/report evidence. |
| `src/polymarket_alpha_lab/manual_operator_packet_final_boundary_report.py` | `tests/test_manual_operator_packet_final_boundary_report.py` | Verifies final packet boundary flags and manual-only status. |
| `src/polymarket_alpha_lab/manual_decision_reasoning_trace_report.py` | `tests/test_manual_decision_reasoning_trace_report.py` | Records reasoning trace for manual decision review. |
| `src/polymarket_alpha_lab/manual_decision_sla_breach_readiness_report.py` | `tests/test_manual_decision_sla_breach_readiness_report.py` | Reports readiness impact from manual decision SLA breaches. |
| `src/polymarket_alpha_lab/manual_execution_decision_ticket_report.py` | `tests/test_manual_execution_decision_ticket_report.py` | Formats a manual decision ticket while preserving Phase 1 boundaries. |
| `src/polymarket_alpha_lab/manual_execution_safety_interlock_report.py` | `tests/test_manual_execution_safety_interlock_report.py` | Reports safety interlock status before any later execution-oriented phase. |
| `src/polymarket_alpha_lab/manual_review_attestation_completeness_report.py` | `tests/test_manual_review_attestation_completeness_report.py` | Checks completeness of manual review attestation evidence. |
| `src/polymarket_alpha_lab/manual_trade_preflight_readiness_report.py` | `tests/test_manual_trade_preflight_readiness_report.py` | Reports manual preflight readiness without authorizing live trade actions. |
| `src/polymarket_alpha_lab/operator_final_go_no_go_packet_readiness_report.py` | `tests/test_operator_final_go_no_go_packet_readiness_report.py` | Checks final operator packet readiness for manual go/no-go review. |

## Post-Settlement Calibration

Post-settlement calibration modules turn settled outcomes into team and
strategy learning evidence. They can update report-only queues and calibration
evidence surfaces, but memory remains research context and must not tune live
strategy weights, size positions, rank investments, or authorize execution.

| Module | Test | Phase 1 role |
| --- | --- | --- |
| `src/polymarket_alpha_lab/research_strategy_post_settlement_calibration_queue_report.py` | `tests/test_research_strategy_post_settlement_calibration_queue_report.py` | Builds a queue of settled candidates needing calibration review. [B1 · deprecated-pending-prune](../handoffs/m3-cleanup/pr-b-20261120/B1-status.json) |
| `src/polymarket_alpha_lab/specialist_team_post_settlement_calibration_evidence_report.py` | `tests/test_specialist_team_post_settlement_calibration_evidence_report.py` | Reports specialist-team calibration evidence after settlement. |
| `src/polymarket_alpha_lab/forecast_calibration.py` | `tests/test_forecast_calibration.py` | Computes forecast calibration diagnostics for paper/report evidence. |
| `src/polymarket_alpha_lab/forecast_calibration_trend.py` | `tests/test_forecast_calibration_trend.py` | Reports calibration trends over persisted local evidence. |
| `src/polymarket_alpha_lab/calibration_drift_monitor.py` | `tests/test_calibration_drift_monitor.py` | Monitors calibration drift as a readiness risk. |
| `src/polymarket_alpha_lab/candidate_decision_team_memory_calibration.py` | `tests/test_candidate_decision_team_memory_calibration.py` | Connects team memory calibration evidence to candidate decision diagnostics. |
| `src/polymarket_alpha_lab/candidate_similar_event_memory_digest.py` | `tests/test_candidate_similar_event_memory_digest.py` | Digests similar settled-event memory as research context. |
| `src/polymarket_alpha_lab/post_settlement_calibration_experience_feedback_report.py` | `tests/test_post_settlement_calibration_experience_feedback_report.py` | Converts settled experience into readonly calibration feedback evidence. |

## Report Discovery

Report discovery modules make report surfaces easier to locate and verify.
They are navigation and metadata helpers; they do not execute reports, mutate
databases, change CLI behavior, or repair missing upstream evidence.

| Module | Test | Phase 1 role |
| --- | --- | --- |
| `src/polymarket_alpha_lab/report_discovery.py` | `tests/test_cli_report_discovery.py` | Discovers registered report surfaces for operator and CLI navigation. |
| `src/polymarket_alpha_lab/cli.py` | `tests/test_cli_report_discovery.py` | Exposes the readonly report-discovery command surface. |
| `src/polymarket_alpha_lab/local_observability_trends.py` | `tests/test_local_observability_trends_report_validation.py` | Summarizes local observability trends across report evidence. |
| `src/polymarket_alpha_lab/local_supabase_evidence_persistence_readiness_report.py` | `tests/test_local_supabase_evidence_persistence_readiness_report.py` | Reports readiness of local Supabase evidence persistence. |
| `src/polymarket_alpha_lab/local_supabase_schema_migration_plan_safety_report.py` | `tests/test_local_supabase_schema_migration_plan_safety_report.py` | Reports local schema migration plan safety before reviewed DB work. |
| `src/polymarket_alpha_lab/input_failure_degradation_readiness_report.py` | `tests/test_input_failure_degradation_readiness_report.py` | Reports readiness under input failure or degraded evidence. |
| `src/polymarket_alpha_lab/strategy_phase1_readiness_aggregator.py` | `tests/test_strategy_phase1_readiness_aggregator.py` | Aggregates current strategy signals into a Phase 1 readiness report. |
| `src/polymarket_alpha_lab/supabase_local_dsn.py` | `tests/test_supabase_local_dsn.py` | Validates the local Supabase/Postgres-only DSN boundary. |

## Boundary Checklist

Every listed capability should preserve these Phase 1 constraints:

- `paper_only=True`, `report_only=True`, and `readonly=True` where those hard
  flags exist.
- No live trading, account authentication, wallet handling, private-key
  handling, hosted account reads, order signing, order submission, order
  cancellation, order replacement, or exchange/order mutation paths.
- Cost, EV, Kelly, liquidity, settlement, and exposure outputs are paper/report
  diagnostics, not live capital allocation or order sizing.
- Team routing and team memory provide research responsibility and context
  only; central modules remain responsible for cost, risk, paper allocation
  evidence, outcomes, and comparable reporting.
- Durable project data persists to local Supabase/Postgres only; no new
  alternate database backend or file-backed durable substitute is introduced.
- Legacy JSONL/file-backed surfaces stay frozen as compatibility, export,
  replay, or read-only input boundaries unless separately migrated through a
  reviewed local Supabase/Postgres node.
