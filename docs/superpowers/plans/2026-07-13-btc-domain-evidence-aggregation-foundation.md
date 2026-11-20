# BTC Domain Evidence Aggregation Foundation Roadmap

**Status:** Split into independently reviewed Phase 1 development nodes after read-only plan audits found the original 51-path implementation plan was not executable as one unit.

**Goal:** Build a replayable, fail-closed specialist-team evidence pipeline for Polymarket probability events, beginning with BTC, without crossing the Phase 1 paper-only/report-only/readonly boundary.

## Why This Is Split

The original plan combined probability semantics, aggregation arithmetic, replay identity, packet compatibility, schema changes, transaction enforcement, BTC resolution policy, and production service wiring. Independent audits found that one review could not reject or approve those contracts separately and that several interfaces were underspecified.

Each node has its own exact path allowlist, TDD cycle, focused tests, full
suite, CodeGraph sync, independent read-only review, commit, and GitHub push.
The linked July plans retain their historical commands and receipt contracts.
For a new or renewed review, follow [Native Codex Subagent Routing](../../../AGENTS.md#native-codex-subagent-routing).
A later node still needs the accepted commit at exact remote `main` and
every required historical PASS receipt descriptor-validated against it.

## Node Sequence

1. [Canonical Team Forecast P(YES) Contract](2026-07-13-team-forecast-probability-yes-contract.md)
   - Fix the packet, DB, calibration, migration-comment, and operator-documentation semantics.
   - Characterize the existing correct NO-side arithmetic end to end.

2A. `2026-07-13-team-evidence-aggregation-contracts-codec-temporal-eligibility.md`
   - Independently planned, reviewed, committed, and pushed child node for pure core contracts, complete input-graph validation, canonical-current and canonical-capture selectors, strict codec behavior, temporal eligibility, and explicit immutable generic configuration.
   - Generic configuration contains no BTC-specific threshold defaults; BTC source and threshold policy remains exclusively Node 6.
   - Owns the canonical config, input, and core-result fragments, config digest, and core-result digest, but not run identity, complete evaluation-scope provenance, `tea:v1`/`tfr:v1`/`tfe:v1` IDs, legacy projections, DB, CLI, network, package-root export, or persistence.

2B. `2026-07-13-team-evidence-aggregation-independence-correlation-requirement-witness.md`
   - Independently planned, reviewed, committed, and pushed child node for exact independence and correlation allocation arithmetic and the global requirement-witness proof.
   - Consumes only the stable Node 2A core contract after Node 2A is reviewed and pushed, and remains pure: no run identity, complete evaluation-scope provenance, `tea:v1`/`tfr:v1`/`tfe:v1` IDs, legacy projections, DB, CLI, network, package-root export, or persistence.

2C. `2026-07-13-team-evidence-aggregation-status-facade-publishability-scope-guard.md`
   - Independently planned, reviewed, committed, and pushed child node for the end-to-end reducer, one diagnostic per supplied record, exact allocation/witness handoff, canonical arithmetic `P(YES)`, requirement coverage, contradiction and top-level status, result validation, ready-only publishability, and the unified six-module plus package-root import/export/forbidden-surface/size guard.
   - Consumes only stable Node 2A/2B contracts after Nodes 2A and 2B are each reviewed and pushed, and remains pure: no run identity, complete evaluation-scope provenance, `tea:v1`/`tfr:v1`/`tfe:v1` IDs, legacy projections, DB, CLI, network, package-root export, or persistence.

   **Node 2 boundary:** Node 2 may own canonical config, input, and result fragments, the config digest, and the core-result digest. It still may not own an outer evaluation-scope/replay digest, run identity, `tea:v1`/`tfr:v1`/`tfe:v1` IDs, legacy/packet projections, external adapters, package-root exports, or persistence.

## Node 2 Contract And Handoff References

- The normative shared contract is
  `../specs/2026-07-13-team-evidence-aggregation-core-design.md`; child plans
  refine it but may not weaken its locked public interfaces or Phase 1 boundary.
- Node 2A publishes `NODE2A_PREREQ_SHA`, `NODE2A_PASS_RECEIPT_PATH`, and
  `NODE2A_PASS_RECEIPT_SHA256`. Node 2B validates all three before consuming
  Node 2A and then publishes the corresponding `NODE2B_*` values. Node 2C
  descriptor-validates both immutable receipts before consuming either child.
- Receipt validation binds exact node name, reviewed commit, Claude model and
  effort, exact PASS line, focused/full/compile/CodeGraph/static/clean statuses,
  and the stably confirmed remote-main SHA. A receipt path or payload is never
  accepted by filename, path precheck, or trust in coordinator memory.
- The child plans' `Locked Public Interfaces`, `Fixed Predecessor Interfaces`,
  completion receipt/evidence, and publication-state sections are normative.
  Only stable exact remote equality completes a child; a descendant remote is
  classified as superseded and does not satisfy an equality-bound handoff.
- Node 2C is the terminal Node 2 child and does not invent a third receipt
  schema. Once its accepted final SHA is known, the Node 3 plan pins that
  literal prerequisite and verifies commit existence, ancestry, review
  evidence, and exact remote publication before consuming Node 2.

3. `2026-07-13-team-forecast-build-envelope.md`
   - First call `validate_team_evidence_aggregation_result(result, aggregation_input=aggregation_input, config=config)` and require `result.config_digest == team_evidence_aggregation_config_digest(config)`, then bind the separate canonical Node 2 config, input, and validated full-result fragments exactly once together with complete evaluation-scope provenance, run metadata, and a Node-3-owned canonical receipt list for accepted and rejected evaluator inputs. It never treats `core_digest` as replay, run, forecast, evidence, or deduplication identity.
   - Preserve legacy payload hashes byte for byte.

4. `2026-07-13-team-evidence-aggregation-schema.md`
   - Add the aggregation report schema, row codec, local configuration, exact-format partial indexes, and operator migration contract.

5. `2026-07-13-team-forecast-atomic-persistence.md`
   - Enforce the sole atomic write path at DB-API and psycopg boundaries.
   - Prove ready/watch/blocked, retry, orphan rejection, and rollback behavior against disposable local Postgres.

6. `2026-07-13-crypto-btc-evidence-policy.md`
   - Add the immutable approved BTC source registry, trusted source-record catalog, typed resolution contract, pure policy, and evaluator.

7. `2026-07-13-crypto-btc-forecast-service.md`
   - Add the evaluate-and-persist service, exports, final docs, and integration tests.
   - Strategy-cycle consumption remains a separately reviewed next node.

## Dependency Waves

- Node 1 can execute while the Node 2A, Node 2B, and Node 2C plans are finalized.
- Node 2B starts after Node 2A's accepted commit is stably confirmed at exact remote `main` and its immutable receipt is validated; Node 2C starts after the corresponding Node 2A and Node 2B handoffs are both validated.
- Node 3 starts only after Node 1 and all of Nodes 2A, 2B, and 2C have accepted reviews and exact remote publication evidence, Node 2A/2B receipts have been validated by Node 2C, and Node 3's fixed predecessor interface pins the accepted Node 2C SHA.
- Nodes 4 and 6 may be prepared in parallel after Node 3 is reviewed and pushed.
- Node 5 starts after Node 4 is reviewed and pushed.
- Node 7 starts after Nodes 5 and 6 are each reviewed and pushed.
- Pushes are sequential and non-force. If `origin/main` moves before publication,
  the node does not automatically rebase or push; start a fresh gate from the
  newly fetched base and obtain a fresh full-range Claude review. After a push
  attempt, observation-only recovery never authorizes a second push.

## Contracts Fixed By Audit

- `TeamForecastPacket.forecast_probability` always denotes canonical `P(YES)`, regardless of `selected_side`; `selected_side` identifies the paper-review side being evaluated and never reorients the probability.
- Evidence freshness cannot be refreshed by recapture. The pure core uses trusted effective content time; capture lag can only disqualify evidence.
- Release-vintage metadata comes from a trusted source-record catalog, not an evidence claim.
- Every accepted and rejected evaluator input is represented in a canonical evaluation-scope payload and digest; rejected receipts remain Node 3-owned and do not enter Node 2 records.
- V1 evidence uses a nested replay record and an exact legacy projection; legacy serialization omits the V1 key.
- `tea:v1`, `tfr:v1`, and `tfe:v1` IDs are domain-separated and bind canonical provenance.
- Non-ready aggregation produces no forecast or forecast-evidence packets.
- Every successfully validated Node 3/7 service-level persistence command/result is persisted only through Node 5's atomic local-Supabase writer; constructing or validating a pure Node 2 aggregation result is transient and performs no database activity; invalid service input fails before database activity.
- Public legacy store and psycopg insert APIs reject V1 IDs. The atomic writer uses a private transaction-scoped insertion path.
- The approved BTC registry and policy are immutable/version-bound. Callers cannot self-promote a source to official.
- BTC readiness requires an identity-bound resolution contract and incident gates, not only evidence quorum.
- Future strategy readers inspect the latest aggregation attempt before using a forecast and never fall back through a newer watch/blocked attempt.
- The future central cost engine owns final YES/NO/none selection; packet `selected_side` remains an auditable paper-review input but is not final strategy authority.

## Universal Node Gates

Every executable node records immutable `NODE_BASE`, verifies it equals the
fetched remote base, proves exact staged and committed-range allowlists, records
the accepted reviewed `HEAD`, reviews the complete `NODE_BASE..HEAD` range, runs
the exact focused-test command enumerated in its node plan, and then runs:

```bash
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m pytest -q
codegraph sync .
git diff --check
```

Every node also runs the Phase 1 readonly, local-Supabase-only, intended-path, and high-confidence secret scans required by `docs/quality/phase-1-development-node-quality-gates.md`. Claude Code review is read-only, uses `claude-opus-4-8` with effort `max`, has fast mode off, and its exact final nonblank line must be `VERDICT: PASS`. Empty output, a missing verdict, or any other final line fails the gate; unavailable local Claude Code blocks the gate with no fallback reviewer.

Each of Nodes 2A, 2B, and 2C has its own exact path allowlist, focused tests,
full suite, compile, CodeGraph, secret/boundary/persistence scans, and a local
Claude Code review gate using model `claude-opus-4-8` at effort `max`, read-only,
with fast mode off, whose exact final line is `VERDICT: PASS`; empty output is
failure, and unavailable local Claude Code blocks the gate with no fallback.
Node 2A runs its three-module child-local AST/import/export/forbidden-surface
and size gate, Node 2B runs its two-module child-local gate, and Node 2C runs
the final six-module plus package-root gate with the shared aggregate ceilings;
none is deferred to a successor. Each child records its exact committed range,
review markers, push status, stable remote observation, and completion evidence.
Its successor may consume the contract only after exact remote publication and
receipt validation where the handoff requires a receipt.

Every later persistence of project data uses only this host's local Supabase/Postgres; no file, cache, journal, alternate database, hosted service, or generic store is an allowed fallback. Every raw DSN from environment, config, CLI plumbing, fixtures, or helper construction passes through `validate_local_postgres_dsn` before any adapter or connection is constructed. Disposable-database tests may create and drop an isolated database inside the existing local `supabase-db` container; they never reset, migrate, or mutate the host `postgres` database.
