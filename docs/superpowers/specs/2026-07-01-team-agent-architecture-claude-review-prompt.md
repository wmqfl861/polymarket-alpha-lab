# Claude Code Review Prompt: Team-Agent Architecture

This prompt records the July 2026 review request. Use
[Native Codex Subagent Routing](../../../AGENTS.md#native-codex-subagent-routing)
to assign a current independent review.

The original target report was

`docs/superpowers/specs/2026-07-01-team-agent-architecture-review-report.md`

Context:

- Repository: `/home/ubuntu/polymarket-alpha-lab`
- Project: paper-only Polymarket alpha research and recommendation lab.
- Current boundary: Phase 1 remains paper-only, report-only, readonly.
- Durable data rule: local Supabase/Postgres only.
- No live trading, no auth, no private keys, no wallet/account reads, no order signing/submission/cancel/replace, no exchange/order mutation.
- The user wants medium-granularity domain teams with long-term memory:
  - politics
  - crypto_btc
  - crypto_eth
  - macro_rates
  - equity_indices
  - commodities_gold
  - commodities_oil
  - sports_soccer
  - sports_basketball
  - sports_other

Please review the report as an architecture and implementation-plan reviewer.

Focus on:

1. Team granularity and whether any teams should be merged/split.
2. Agent role boundaries and likely duplication or gaps.
3. Long-term memory schema sufficiency.
4. Phase 1 safety/compliance with paper-only, readonly, Supabase-only constraints.
5. Integration boundary with existing cost-aware/recommendation pipeline.
6. Minimal viable implementation slice.
7. Tests/invariants that should be required.
8. Risks of overbuilding, sample-size fragmentation, memory contamination, leakage, or stale lessons.

Return:

- Critical issues, if any.
- Important improvements.
- Minor suggestions.
- A recommended revised first implementation slice.
- A clear verdict: approve as-is, approve with changes, or reject and redesign.

Do not edit files. This is a review only.
