# Team Forecast Build Envelope Implementation Plan

This July 2026 plan retains its original review instructions for receipt
and implementation history. For a current review, follow
[Native Codex Subagent Routing](../../../AGENTS.md#native-codex-subagent-routing).

**Goal:** Build the pure Phase 1 Node 3 envelope that binds one validated Node 2 aggregation to complete evaluation scope, explicit run metadata, canonical accepted/rejected evaluator receipts, domain-separated `tea:v1`/`tfr:v1`/`tfe:v1` identifiers, and exact legacy packet projections. A non-ready aggregation produces no forecast or forecast-evidence packets.

**Architecture:** `build_team_forecast_build_envelope` first calls the reviewed Node 2 result validator with the exact `result`, `aggregation_input`, and `config`. It then requires `result.config_digest == team_evidence_aggregation_config_digest(config)`, calls the three Node 2 codec payload functions exactly once, and places the returned config, input, and full validated-result payloads into three separate keys in one canonical evaluation-scope preimage. The preimage also contains domain context, run metadata, and one canonical receipt for every accepted or rejected evaluator input. IDs are computed from complete canonical preimages with distinct domain separators. The Node 2 `core_digest` remains a field inside the validated result fragment and is never used as a run, forecast, evidence, replay, or deduplication identity.

The existing `TeamForecastPacket`, `TeamForecastEvidencePacket`, and `team_forecast_packet_payload` APIs remain unchanged. Node 3 owns their construction and compatibility projection by calling those public APIs. V1 evidence is represented by a nested replay record; its nested legacy payload is byte-for-byte the existing payload projection and contains no V1 identifier key.

**Tech Stack:** Python 3.12, exact frozen/slotted dataclasses, UTC-aware caller-supplied datetimes, fixed-six `Decimal` under a local `Context(prec=64, rounding=ROUND_HALF_EVEN)`, canonical JSON, SHA-256, Python `ast`, pytest, Git, `scripts/verify_local.py`, and read-only Codex review with `gpt-6-astra` at `model_reasoning_effort=max`.

## Global Constraints

- Preserve `docs/superpowers/specs/2026-07-13-team-evidence-aggregation-core-design.md`, especially the Node 3 ownership and handoff rules at lines 98–115, 1644–1758, and 2342–2347.
- The first executable operation in every build is:

  ```python
  validate_team_evidence_aggregation_result(
      result,
      aggregation_input=aggregation_input,
      config=config,
  )
  ```

  The next handoff assertion is exact config-digest equality.
- The config, input, and validated full-result fragments are separate canonical objects, each present exactly once. Do not flatten, merge, omit, mutate, or substitute the fragments.
- Include both accepted and rejected evaluator receipts. Rejected receipts remain Node 3 scope evidence and never enter Node 2 records, selections, diagnostics, allocation, witnesses, arithmetic, or packet evidence.
- `core_digest` may occur only as the validated field inside the full result fragment. It must never be passed to an ID function as the identity preimage.
- All hard flags are literal `True`: `paper_only=True`, `report_only=True`, and `readonly=True`. Callers cannot override them.
- The module is pure over explicit values. It performs no database, Supabase, PostgreSQL, SQL, persistence, filesystem, JSONL, cache, network, HTTP, socket, browser, CLI, environment, process, logging, credential, account, wallet, key, signing, order, live-trading, ambient-clock, randomness, or built-in `hash()` operation.
- No package-root export, database row, store, service, migration, decoder, strategy-cycle integration, or BTC-specific default is added.
- Decimal values remain exact fixed-six values. Any arithmetic uses only a local copy of `Context(prec=64, rounding=ROUND_HALF_EVEN)` and never reads or mutates the ambient context. No floats, exponent-form Decimal output, early quantization, NaN, infinity, or signed zero is emitted.
- Canonical JSON uses UTF-8, `allow_nan=False`, `ensure_ascii=True`, `sort_keys=True`, and `separators=(",", ":")`.
- Existing legacy hashes use the exact historical encoding: `json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")`. Do not add `ensure_ascii`, wrappers, V1 keys, or normalization changes to that legacy path.

## Exact Sorted Implementation Allowlist

Only these paths may change, in literal `LC_ALL=C` order:

```text
src/polymarket_alpha_lab/team_forecast_build_envelope.py
tests/test_team_forecast_build_envelope.py
tests/test_team_forecast_build_envelope_scope.py
```

Use this exact gate:

```bash
NODE_PATHS=(
  src/polymarket_alpha_lab/team_forecast_build_envelope.py
  tests/test_team_forecast_build_envelope.py
  tests/test_team_forecast_build_envelope_scope.py
)
EXPECTED_NODE_PATHS="$(printf '%s\n' "${NODE_PATHS[@]}")"
test "$(printf '%s\n' "${NODE_PATHS[@]}" | LC_ALL=C sort)" = "$EXPECTED_NODE_PATHS"
```

Do not extend `tests/test_team_evidence_aggregation_scope.py` to a seventh production module. Node 2 remains governed by its reviewed six-module scope guard and its existing aggregate ceilings.

## Fixed Predecessor Interfaces

Consume only these exact public interfaces:

```python
validate_team_evidence_aggregation_result(
    result: TeamEvidenceAggregationResult,
    *,
    aggregation_input: TeamEvidenceAggregationInput,
    config: TeamEvidenceAggregationConfig,
) -> None

team_evidence_aggregation_config_payload(
    config: TeamEvidenceAggregationConfig,
) -> dict[str, object]

team_evidence_aggregation_input_payload(
    aggregation_input: TeamEvidenceAggregationInput,
) -> dict[str, object]

team_evidence_aggregation_payload(
    result: TeamEvidenceAggregationResult,
) -> dict[str, object]

TeamForecastPacket
TeamForecastEvidencePacket
team_forecast_packet_payload(value: object) -> dict[str, Any]
```

The Node 2 public names and signatures are fixed by the current source. Any drift requires an earlier-node amendment and re-review.

## Locked Node 3 Public Surface

`team_forecast_build_envelope.py` must define exactly this ordered `__all__` tuple:

```python
__all__ = (
    "TeamForecastEvaluationScope",
    "TeamForecastRunMetadata",
    "TeamForecastEvaluatorReceipt",
    "TeamForecastEvidenceReplayRecord",
    "TeamForecastBuildEnvelope",
    "build_team_forecast_build_envelope",
    "validate_team_forecast_build_envelope",
    "team_forecast_evaluation_scope_payload",
    "team_evidence_aggregation_id",
    "team_forecast_run_id",
    "team_forecast_evidence_id",
    "team_forecast_legacy_payload_sha256",
)
```

The immutable types contain:

- `TeamForecastEvaluationScope`: canonical scope version, sorted domain-context key/value pairs, sorted provenance references, and the three hard flags.
- `TeamForecastRunMetadata`: run label, generator version, prompt version, explicit UTC `started_at`, optional explicit UTC `completed_at`, and the three hard flags.
- `TeamForecastEvaluatorReceipt`: evaluator input ID, lowercase SHA-256 input digest, explicit UTC receipt time, `accepted` or `rejected` classification, accepted four-field Node 2 record identity or `None`, sorted reason codes, and the three hard flags.
- `TeamForecastEvidenceReplayRecord`: `tfe:v1` ID, accepted record identity, exact legacy evidence payload, and its legacy payload SHA-256.
- `TeamForecastBuildEnvelope`: `tea:v1` ID, `tfr:v1` ID, canonical evaluation-scope payload, optional legacy forecast packet, legacy evidence packets, nested replay records, and the three hard flags.

The builder signature is:

```python
build_team_forecast_build_envelope(
    result: TeamEvidenceAggregationResult,
    *,
    aggregation_input: TeamEvidenceAggregationInput,
    config: TeamEvidenceAggregationConfig,
    scope: TeamForecastEvaluationScope,
    run_metadata: TeamForecastRunMetadata,
    evaluator_receipts: tuple[TeamForecastEvaluatorReceipt, ...],
    legacy_forecast_packet: TeamForecastPacket | None,
    legacy_evidence_packets: tuple[TeamForecastEvidencePacket, ...],
) -> TeamForecastBuildEnvelope
```

The validator accepts the envelope plus the same exact inputs and returns `None` only after rematerialization and complete equality checks.

IDs use these locked formulas:

```text
tea:v1:<lowercase_sha256(
    b"tea:v1\x00" + canonical_evaluation_scope_bytes
)>

tfr:v1:<lowercase_sha256(
    b"tfr:v1\x00" + canonical_json({
        "tea_id": tea_id,
        "run_metadata": run_metadata_payload
    })
)>

tfe:v1:<lowercase_sha256(
    b"tfe:v1\x00" + canonical_json({
        "tea_id": tea_id,
        "receipt": accepted_receipt_payload,
        "legacy_payload_sha256": legacy_hash
    })
)>
```

The complete scope preimage contains exactly one `domain_context`, one `provenance`, one `run_metadata`, one canonical receipt list, one `node2_config` fragment, one `node2_input` fragment, and one `node2_result` fragment.

## Task 1: Validate the Candidate Base and Predecessor Handoff

- [ ] Record `NODE_BASE=3bec78e3b8e884c8c32ab9f33f8084d4b86d9792` as the local candidate base.
- [ ] Verify the branch, clean worktree, exact allowlist, and unchanged Node 2 predecessor files.
- [ ] Attempt the original Node 1/2A/2B/2C receipt and exact-remote checks. Missing receipts, absent original descriptors, or `origin/main` inequality are recorded as blocked evidence; they are never replaced by local Git ancestry.
- [ ] Record the original CodeGraph CLI as `documented-unavailable` if it remains absent.
- [ ] Stop implementation if a predecessor public signature, Node 2 scope guard, or predecessor source file differs from the reviewed contract.

## Task 2: Red/Green Scope, Run, and Receipt Contracts

- [ ] Add fixtures for one accepted and one rejected evaluator input, with accepted identities exactly matching the supplied Node 2 record identities.
- [ ] Add tests named `test_receipt_list_contains_accepted_and_rejected_inputs`, `test_receipts_are_canonically_sorted_and_unique`, `test_rejected_receipts_never_enter_node2_fragments`, and `test_receipt_identity_and_digest_validation_fails_closed`.
- [ ] Add tests for duplicate evaluator IDs, digest aliases, missing accepted identities, rejected receipts carrying identities, unsorted reason codes, false flags, naive datetimes, and noncanonical IDs.
- [ ] Implement the immutable scope, run metadata, and receipt types with exact types, closed fields, canonical sorting, and stable `ValueError` paths.
- [ ] Run the focused receipt tests until green.

## Task 3: Red/Green Canonical Fragments and Domain-Separated IDs

- [ ] Add `test_validator_is_called_before_any_scope_or_id_work`, using spies to prove the Node 2 validator is first.
- [ ] Add `test_config_digest_must_equal_result_config_digest`.
- [ ] Add `test_three_node2_fragments_are_bound_once_and_separately`.
- [ ] Add `test_domain_separated_ids_are_prefixes_with_exact_preimages`, asserting the three formulas above.
- [ ] Add `test_core_digest_is_never_used_as_an_identity`, changing the complete result fragment and proving IDs are derived from the full scope rather than a standalone core digest.
- [ ] Implement one private materializer that validates first, obtains each Node 2 payload exactly once, creates fresh dictionaries, canonicalizes receipts, and computes IDs without mutating predecessor payloads.
- [ ] Run the fragment and ID tests until green.

## Task 4: Red/Green Legacy Projection and Ready Boundary

- [ ] Add `test_legacy_payload_hash_is_byte_for_byte_compatible`, including the existing signed-zero compatibility fixture and exact historical JSON encoding.
- [ ] Add `test_v1_keys_are_absent_from_legacy_forecast_and_evidence_payloads`.
- [ ] Add `test_ready_result_builds_forecast_and_evidence_packets_with_v1_ids`.
- [ ] Add `test_non_ready_result_produces_no_packets`, covering both `watch` and `blocked`.
- [ ] Add `test_non_ready_packet_arguments_fail_closed`, `test_ready_probability_matches_publishable_probability`, and `test_evidence_replay_records_match_accepted_receipts`.
- [ ] Implement ready-only packet construction. For ready results, the forecast packet uses the `tfr:v1` ID and each evidence packet uses its `tfe:v1` ID. For watch/blocked results, packet fields are `None`/empty and no replay packet is emitted.
- [ ] Rematerialize in `validate_team_forecast_build_envelope`; reject packet, receipt, payload, ID, hard-flag, fragment, and legacy-hash tampering.
- [ ] Run the full behavior test file until green.

## Task 5: Add the Node 3-Owned Scope Guard

- [ ] Create `tests/test_team_forecast_build_envelope_scope.py`.
- [ ] Add `test_node_3_ast_import_export_forbidden_surface_and_line_size_gate`.
- [ ] Add `test_node_3_public_dataclasses_are_frozen_slotted_and_hard_flagged`.
- [ ] Add `test_node_3_package_root_has_no_node_3_exports`.
- [ ] Permit only standard-library imports plus the fixed Node 2 modules and the existing pure `team_forecast_packet` module. Reject relative imports, DB/FS/network/CLI/env/process/auth/wallet/order/trading identifiers, floats, ambient-clock calls, randomness, `hash()`, V1 leakage into legacy payloads, and package-root exports.
- [ ] Enforce these physical-line ceilings: production module `<=900`; behavior tests `<=1100`; scope test `<=500`; Node 3-owned tests `<=1600`. The reviewed Node 2 ceilings remain unchanged at `<=3500` production and `<=4850` tests.
- [ ] Run all three scope tests until green.

## Task 6: Verification, Review, Commit, and Publication Gate

- [ ] Run with the sanitized environment:

```bash
unset PYTEST_ADDOPTS PYTEST_PLUGINS PYTHONPATH PYTHONHOME
export PYTHONUTF8=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export POLYMARKET_ALPHA_LAB_RUN_SUPABASE_SMOKE=0

.venv/Scripts/python.exe -m pytest -q \
  tests/test_team_forecast_build_envelope.py \
  tests/test_team_forecast_build_envelope_scope.py

.venv/Scripts/python.exe -m compileall -q src tests
.venv/Scripts/python.exe -m pytest --collect-only -q
.venv/Scripts/python.exe scripts/verify_local.py --full
git diff --check
```

- [ ] Run high-confidence secret, Phase 1 readonly, persistence, and intended-path scans. Only the scope test may contain forbidden-surface vocabulary.
- [ ] Stage exactly `NODE_PATHS`; prove sorted and native staged equality.
- [ ] Obtain a read-only Codex review using `gpt-6-astra`, `model_reasoning_effort=max`, with final nonblank line `VERDICT: PASS`.
- [ ] Commit only after all local gates pass. Publication remains blocked until exact remote equality and the required historical receipts become verifiable.

## Stop Conditions

Stop and amend the predecessor or plan when any of these occurs:

- Node 2 receipt descriptors or exact remote publication cannot be validated.
- A predecessor signature, payload shape, core digest, hard flag, or six-module scope guard changes.
- The validator is not the first operation, a fragment is omitted/duplicated, or `core_digest` becomes an identity.
- A rejected receipt enters Node 2 arithmetic or packet evidence.
- A non-ready result emits any packet or V1 replay record.
- Any legacy payload byte or hash changes.
- A line ceiling, import gate, purity gate, focused test, full suite, compile, secret scan, review, or diff check fails.
- CodeGraph remains unavailable; record `documented-unavailable` and do not claim synchronization.
- Remote publication is absent or unequal; never claim a completed handoff.

## Completion Evidence

Completion requires the exact three-path range, clean worktree, focused tests, compile success, full sanitized suite, `verify_local.py --full`, diff/secret/purity scans, unchanged Node 2 scope evidence, Codex `VERDICT: PASS`, and a locally recorded candidate commit.

The completion record must separately state whether predecessor receipts, exact remote equality, and CodeGraph synchronization are `pass`, `blocked`, or `documented-unavailable`. Local candidate success must never be reported as remote equality or receipt validation.
