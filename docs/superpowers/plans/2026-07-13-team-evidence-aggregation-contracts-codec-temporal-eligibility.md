# Team Evidence Aggregation Contracts, Codec, And Temporal Eligibility Implementation Plan

This July 2026 plan retains its original review instructions for receipt
and implementation history. For a current review, follow
[Native Codex Subagent Routing](../../../AGENTS.md#native-codex-subagent-routing).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Node 2A's pure immutable team-evidence contracts, complete bounded input-graph validation, both canonical record selectors, strict canonical payload/digest codec, and exact temporal eligibility assessment.

**Architecture:** Three module-local standard-library modules divide ownership exactly: `team_evidence_aggregation_types.py` owns all sixteen immutable domain types, scalar normalization, complete input-graph validation, and both canonical selectors; `team_evidence_aggregation_codec.py` owns four closed JSON-ready envelopes plus the config and core-result digests; `team_evidence_aggregation_temporal.py` owns explicit-clock age, lag, availability, freshness, and timeliness assessment. All values express canonical `P(YES)`, all arithmetic uses isolated fixed-six `Decimal`, and the child remains pure with no package-root export, persistence, replay/run identity, or external I/O.

**Tech Stack:** Python 3.12, standard library only, `@dataclass(frozen=True, slots=True)`, exact base types, `Decimal` with `Context(prec=64, rounding=ROUND_HALF_EVEN)`, timezone-aware `datetime`, canonical UTF-8 JSON, SHA-256, pytest, Python `ast`, CodeGraph, Git, and local Claude Code `claude-opus-4-8` at effort `max`.

## Global Constraints

- The approved design in `docs/superpowers/specs/2026-07-13-team-evidence-aggregation-core-design.md` is immutable. Do not change a field, signature, sort key, envelope, digest domain, threshold rule, resource maximum, import allowlist, or ownership boundary while implementing this plan.
- Node 2A creates exactly the six paths in the sorted implementation allowlist below and modifies no existing path, including `src/polymarket_alpha_lab/__init__.py`.
- Every public typed value is an exact, runtime-non-subclassable `@dataclass(frozen=True, slots=True)` with `paper_only=True`, `report_only=True`, and `readonly=True`; all three flags are validated as exact `True`.
- All input and output probabilities are canonical `P(YES)`. There is no selected-side field and no `P(NO)` alias or complement path.
- All policy fields are required immutable configuration. There is no default config, BTC source, BTC threshold, or built-in `0.020000`/`0.980000` publication policy.
- Node 2A is pure over caller-supplied values and performs no database activity, project-data persistence, filesystem I/O, network access, CLI/env access, process execution, logging, ambient-clock read, randomness, or built-in `hash()`.
- Future persistence derived from Node 2 is local Supabase/Postgres only, with no file, cache, journal, alternate database, hosted service, or generic-store fallback. Every future raw DSN from environment, configuration, CLI plumbing, fixture, or helper construction must be passed to `validate_local_postgres_dsn` before any connection, psycopg wrapper, or persistence adapter is constructed. This pure child does not accept a DSN, import that validator, construct an adapter, or persist anything.
- Phase 1 remains `paper_only=True`, `report_only=True`, and `readonly=True`; no live/auth/account/credential/private-key/wallet/signing/order/sizing/execution/exchange-mutation surface is allowed.
- Node 2A owns canonical config, input, core-result fragments and config/core digest support only. It does not own team/market/condition/run identity, complete evaluation-scope provenance, accepted/rejected receipt lists, `tea:v1`/`tfr:v1`/`tfe:v1` IDs, packet construction, legacy projections, DB rows, services, CLI, or package-root exports.
- Rejected evaluator receipts never enter Node 2 records. Node 3 later owns complete evaluation-scope provenance and receipt classification; `core_digest` is not a run, forecast, evidence, replay, or deduplication identity.
- No production dependency is added. Tests use only repository-installed pytest and standard-library inspection.
- Fast mode is forbidden. Claude review is local, read-only, uses model `claude-opus-4-8`, effort `max`, `--safe-mode`, tools `Read,Glob,Grep`, permission mode `dontAsk`, and no session persistence. There is no fallback reviewer.

## Exact Sorted Implementation Allowlist

The implementation commit and the complete `NODE_BASE..HEAD` range must equal this literal `LC_ALL=C`-sorted list:

```text
src/polymarket_alpha_lab/team_evidence_aggregation_codec.py
src/polymarket_alpha_lab/team_evidence_aggregation_temporal.py
src/polymarket_alpha_lab/team_evidence_aggregation_types.py
tests/test_team_evidence_aggregation_codec.py
tests/test_team_evidence_aggregation_temporal.py
tests/test_team_evidence_aggregation_types.py
```

No broad `git add .`, `git add src`, or `git add tests` command is permitted.

## Runtime Base And Immutable Prerequisite Receipt

Node 2A has one predecessor and its SHA is known. The runtime environment must still supply `NODE1_PREREQUISITE_SHA`; it must equal the immutable literal below. There is no unknown predecessor SHA for this child. Future child plans whose predecessor SHA is not yet known must use the same required-environment form (`: "${NAME:?message}"`) and must never insert a guessed SHA.

- Node 1 commit: `46fbd5371782bfc0e654e547d67611ae416c7468`
- Node 1 parent: `5bc403e773176dd23cd6c324f40e67607458ee09`
- Node 1 tree: `a07d496b08f0262353c6c53a4ccd326fea8d47af`
- Node 1 subject: `Document canonical team forecast probability semantics`

- [ ] **Step 1: Fetch, capture `NODE_BASE` from `origin/main`, validate the receipt, and require a clean start**

Run from the repository root:

```bash
set -euo pipefail
: "${NODE1_PREREQUISITE_SHA:?export NODE1_PREREQUISITE_SHA with the reviewed Node 1 commit}"
readonly EXPECTED_NODE1_SHA=46fbd5371782bfc0e654e547d67611ae416c7468
test "$NODE1_PREREQUISITE_SHA" = "$EXPECTED_NODE1_SHA"
export NODE1_PREREQUISITE_SHA
readonly NODE1_PREREQUISITE_SHA

git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main
export NODE_BASE="$(git rev-parse origin/main)"
test "$(git rev-parse HEAD)" = "$NODE_BASE"
test "$(git rev-parse origin/main)" = "$NODE_BASE"
test "$(git merge-base HEAD origin/main)" = "$NODE_BASE"
git merge-base --is-ancestor "$NODE1_PREREQUISITE_SHA" "$NODE_BASE"
readonly NODE_BASE

test "$(git show -s --format='%H' "$NODE1_PREREQUISITE_SHA")" = "$EXPECTED_NODE1_SHA"
test "$(git show -s --format='%P' "$NODE1_PREREQUISITE_SHA")" = \
  5bc403e773176dd23cd6c324f40e67607458ee09
test "$(git show -s --format='%T' "$NODE1_PREREQUISITE_SHA")" = \
  a07d496b08f0262353c6c53a4ccd326fea8d47af
test "$(git show -s --format='%s' "$NODE1_PREREQUISITE_SHA")" = \
  'Document canonical team forecast probability semantics'

test -z "$(git status --porcelain=v1)"
git diff --quiet
git diff --cached --quiet

ALLOWLIST=(
  src/polymarket_alpha_lab/team_evidence_aggregation_codec.py
  src/polymarket_alpha_lab/team_evidence_aggregation_temporal.py
  src/polymarket_alpha_lab/team_evidence_aggregation_types.py
  tests/test_team_evidence_aggregation_codec.py
  tests/test_team_evidence_aggregation_temporal.py
  tests/test_team_evidence_aggregation_types.py
)
for path in "${ALLOWLIST[@]}"; do
  test ! -e "$path"
done
```

Expected: every command exits `0`; `HEAD`, `origin/main`, and their merge base equal the exported `NODE_BASE`; Node 1 is an ancestor with the exact immutable receipt; the worktree and index are clean; none of the six create-only paths exists. If any condition fails, stop without deleting, reverting, or absorbing another worker's change.

---

## Locked Public Interfaces

`src/polymarket_alpha_lab/team_evidence_aggregation_types.py` must define this literal `__all__` tuple in this order:

```python
__all__ = (
    "TeamEvidenceSourceLineage",
    "TeamEvidenceCapture",
    "TeamEvidenceRevision",
    "TeamEvidenceAssessmentRevision",
    "TeamEvidenceAggregationRecord",
    "TeamEvidenceCurrentRevisionSelection",
    "TeamEvidenceAggregationInput",
    "TeamEvidenceRequirement",
    "TeamEvidenceAggregationConfig",
    "TeamEvidenceTemporalAssessment",
    "TeamEvidenceWeightAllocation",
    "TeamEvidenceRequirementWitness",
    "TeamEvidenceRequirementCoverage",
    "TeamEvidenceDiagnosticRow",
    "TeamEvidenceContradictionResult",
    "TeamEvidenceAggregationResult",
    "validate_team_evidence_aggregation_input_contract",
    "select_team_evidence_canonical_current_records",
    "select_team_evidence_canonical_capture_records",
)
```

The three public functions have these exact signatures:

```python
def validate_team_evidence_aggregation_input_contract(
    aggregation_input: TeamEvidenceAggregationInput,
    *,
    config: TeamEvidenceAggregationConfig,
) -> None: ...


def select_team_evidence_canonical_current_records(
    aggregation_input: TeamEvidenceAggregationInput,
    *,
    config: TeamEvidenceAggregationConfig,
) -> tuple[TeamEvidenceAggregationRecord, ...]: ...


def select_team_evidence_canonical_capture_records(
    aggregation_input: TeamEvidenceAggregationInput,
    *,
    config: TeamEvidenceAggregationConfig,
) -> tuple[TeamEvidenceAggregationRecord, ...]: ...
```

`src/polymarket_alpha_lab/team_evidence_aggregation_codec.py` must define this exact ordered surface:

```python
__all__ = (
    "team_evidence_aggregation_config_payload",
    "team_evidence_aggregation_input_payload",
    "team_evidence_aggregation_config_digest",
    "team_evidence_aggregation_core_payload",
    "team_evidence_aggregation_payload",
    "team_evidence_aggregation_core_digest",
    "validate_team_evidence_aggregation_core_digest",
)


def team_evidence_aggregation_config_payload(
    config: TeamEvidenceAggregationConfig,
) -> dict[str, object]: ...


def team_evidence_aggregation_input_payload(
    aggregation_input: TeamEvidenceAggregationInput,
) -> dict[str, object]: ...


def team_evidence_aggregation_config_digest(
    config: TeamEvidenceAggregationConfig,
) -> str: ...


def team_evidence_aggregation_core_payload(
    result: TeamEvidenceAggregationResult,
) -> dict[str, object]: ...


def team_evidence_aggregation_payload(
    result: TeamEvidenceAggregationResult,
) -> dict[str, object]: ...


def team_evidence_aggregation_core_digest(
    result: TeamEvidenceAggregationResult,
) -> str: ...


def validate_team_evidence_aggregation_core_digest(
    result: TeamEvidenceAggregationResult,
) -> None: ...
```

`src/polymarket_alpha_lab/team_evidence_aggregation_temporal.py` must expose only:

```python
__all__ = ("assess_team_evidence_temporal",)


def assess_team_evidence_temporal(
    record: TeamEvidenceAggregationRecord,
    *,
    evaluated_at: datetime,
    config: TeamEvidenceAggregationConfig,
) -> TeamEvidenceTemporalAssessment: ...
```

## Exact Dataclass Fields

All safety flags shown below default to exact `True`; every other field is required. Optional predecessor fields are required arguments whose root value is explicit `None`.

```text
TeamEvidenceSourceLineage:
  source_lineage_id: str
  source_lineage_digest: str
  paper_only: bool
  report_only: bool
  readonly: bool

TeamEvidenceCapture:
  capture_id: str
  capture_digest: str
  source_lineage_id: str
  source_lineage_digest: str
  content_digest: str
  captured_at: datetime
  paper_only: bool
  report_only: bool
  readonly: bool

TeamEvidenceRevision:
  evidence_revision_id: str
  evidence_revision_digest: str
  previous_evidence_revision_id: str | None
  previous_evidence_revision_digest: str | None
  source_lineage_id: str
  source_lineage_digest: str
  content_digest: str
  requirement_ids: tuple[str, ...]
  freshness_anchor_at: datetime
  recorded_at: datetime
  paper_only: bool
  report_only: bool
  readonly: bool

TeamEvidenceAssessmentRevision:
  assessment_revision_id: str
  assessment_revision_digest: str
  previous_assessment_revision_id: str | None
  previous_assessment_revision_digest: str | None
  evidence_revision_id: str
  evidence_revision_digest: str
  assessed_at: datetime
  probability_yes: Decimal
  requested_weight: Decimal
  rationale_digest: str
  independence_key: str
  correlation_key: str
  paper_only: bool
  report_only: bool
  readonly: bool

TeamEvidenceAggregationRecord:
  source_lineage: TeamEvidenceSourceLineage
  capture: TeamEvidenceCapture
  evidence_revision: TeamEvidenceRevision
  assessment_revision: TeamEvidenceAssessmentRevision
  paper_only: bool
  report_only: bool
  readonly: bool

TeamEvidenceCurrentRevisionSelection:
  evidence_revision_id: str
  evidence_revision_digest: str
  assessment_revision_id: str
  assessment_revision_digest: str
  paper_only: bool
  report_only: bool
  readonly: bool

TeamEvidenceAggregationInput:
  evaluated_at: datetime
  records: tuple[TeamEvidenceAggregationRecord, ...]
  current_revisions: tuple[TeamEvidenceCurrentRevisionSelection, ...]
  paper_only: bool
  report_only: bool
  readonly: bool

TeamEvidenceRequirement:
  requirement_id: str
  minimum_witness_count: int
  minimum_effective_weight: Decimal
  unmet_status: str
  paper_only: bool
  report_only: bool
  readonly: bool

TeamEvidenceAggregationConfig:
  config_version: str
  max_evidence_age_seconds: Decimal
  max_capture_lag_seconds: Decimal
  independence_group_weight_cap: Decimal
  correlation_group_weight_cap: Decimal
  max_requirement_assignments_per_evidence: int
  contradiction_no_probability_max: Decimal
  contradiction_yes_probability_min: Decimal
  contradiction_watch_score: Decimal
  contradiction_block_score: Decimal
  publish_probability_floor: Decimal
  publish_probability_ceiling: Decimal
  maximum_records: int
  maximum_requirements: int
  maximum_requirement_memberships: int
  maximum_witness_edges: int
  requirements: tuple[TeamEvidenceRequirement, ...]
  paper_only: bool
  report_only: bool
  readonly: bool

TeamEvidenceTemporalAssessment:
  capture_id: str
  evidence_revision_id: str
  assessment_revision_id: str
  evidence_age_seconds: Decimal
  capture_lag_seconds: Decimal
  effective_at_evaluation: bool
  captured_at_evaluation: bool
  evidence_revision_available_at_evaluation: bool
  assessment_revision_available_at_evaluation: bool
  fresh: bool
  timely: bool
  paper_only: bool
  report_only: bool
  readonly: bool

TeamEvidenceWeightAllocation:
  source_lineage_id: str
  capture_id: str
  evidence_revision_id: str
  assessment_revision_id: str
  independence_key: str
  correlation_key: str
  requested_weight: Decimal
  independence_allocated_weight: Decimal
  effective_weight: Decimal
  independence_cap_applied: bool
  correlation_cap_applied: bool
  paper_only: bool
  report_only: bool
  readonly: bool

TeamEvidenceRequirementWitness:
  requirement_id: str
  source_lineage_id: str
  capture_id: str
  evidence_revision_id: str
  assessment_revision_id: str
  independence_key: str
  effective_weight: Decimal
  paper_only: bool
  report_only: bool
  readonly: bool

TeamEvidenceRequirementCoverage:
  requirement_id: str
  minimum_witness_count: int
  assigned_witness_count: int
  minimum_effective_weight: Decimal
  unmet_status: str
  satisfied: bool
  witnesses: tuple[TeamEvidenceRequirementWitness, ...]
  paper_only: bool
  report_only: bool
  readonly: bool

TeamEvidenceDiagnosticRow:
  source_lineage_id: str
  capture_id: str
  captured_at: datetime
  evidence_revision_id: str
  assessment_revision_id: str
  probability_yes: Decimal
  requested_weight: Decimal
  independence_allocated_weight: Decimal
  effective_weight: Decimal
  evidence_age_seconds: Decimal
  capture_lag_seconds: Decimal
  captured_at_evaluation: bool
  evidence_revision_available_at_evaluation: bool
  assessment_revision_available_at_evaluation: bool
  selected_current_revision: bool
  canonical_capture: bool
  disposition: str
  paper_only: bool
  report_only: bool
  readonly: bool

TeamEvidenceContradictionResult:
  yes_support_weight: Decimal
  no_support_weight: Decimal
  neutral_weight: Decimal
  contradiction_score: Decimal
  status: str
  paper_only: bool
  report_only: bool
  readonly: bool

TeamEvidenceAggregationResult:
  evaluated_at: datetime
  config_version: str
  config_digest: str
  status: str
  diagnostic_record_count: int
  arithmetic_record_count: int
  requested_weight_total: Decimal
  independence_allocated_weight_total: Decimal
  effective_weight_total: Decimal
  arithmetic_probability_yes: Decimal | None
  publishable_probability_yes: Decimal | None
  contradiction: TeamEvidenceContradictionResult
  requirement_coverage: tuple[TeamEvidenceRequirementCoverage, ...]
  diagnostics: tuple[TeamEvidenceDiagnosticRow, ...]
  reason_codes: tuple[str, ...]
  core_digest: str
  paper_only: bool
  report_only: bool
  readonly: bool
```

---

### Task 1: Immutable Types, Canonical Scalars, Configuration, And Test Fixtures

**Files:**
- Create: `tests/test_team_evidence_aggregation_types.py`
- Create: `src/polymarket_alpha_lab/team_evidence_aggregation_types.py`

**Interfaces:**
- Produces: all sixteen exact public dataclasses, private canonical scalar/tuple helpers, exact sort keys, and immutable non-BTC configuration consumed by every later task.
- Does not yet produce: the completed global graph validator/selectors; Task 2 fills those public functions using the private normalized types established here.

- [ ] **Step 1: Write the type, scalar, configuration, and child-scope tests first**

In `tests/test_team_evidence_aggregation_types.py`, define local fixture helpers only. Use `d(value: str) -> Decimal`, `digest(character: str) -> str`, UTC constants, `config(**changes)`, `lineage_graph(**changes)`, and `aggregation_input(**changes)`. The canonical one-lineage fixture uses:

```python
ANCHOR = datetime(2026, 7, 13, 10, 30, tzinfo=UTC)
CAPTURED = datetime(2026, 7, 13, 10, 34, tzinfo=UTC)
RECORDED = datetime(2026, 7, 13, 10, 35, tzinfo=UTC)
ASSESSED = datetime(2026, 7, 13, 10, 36, tzinfo=UTC)
EVALUATED = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)

# IDs and keys
"lineage.alpha"
"capture.alpha.1"
"evidence.alpha.1"
"assessment.alpha.1"
"macro.release"
"desk.alpha"
"macro.shared"

# Canonical assessment values
Decimal("0.640000")
Decimal("0.400000")
```

The config fixture uses the exact non-BTC values later pinned by the codec vector:

```python
TeamEvidenceAggregationConfig(
    config_version="agg-test-v1",
    max_evidence_age_seconds=d("7200.000000"),
    max_capture_lag_seconds=d("300.000000"),
    independence_group_weight_cap=d("0.730000"),
    correlation_group_weight_cap=d("0.610000"),
    max_requirement_assignments_per_evidence=3,
    contradiction_no_probability_max=d("0.210000"),
    contradiction_yes_probability_min=d("0.790000"),
    contradiction_watch_score=d("0.310000"),
    contradiction_block_score=d("0.670000"),
    publish_probability_floor=d("0.110000"),
    publish_probability_ceiling=d("0.890000"),
    maximum_records=8,
    maximum_requirements=4,
    maximum_requirement_memberships=16,
    maximum_witness_edges=12,
    requirements=(
        TeamEvidenceRequirement(
            requirement_id="macro.release",
            minimum_witness_count=1,
            minimum_effective_weight=d("0.120000"),
            unmet_status="blocked",
        ),
    ),
)
```

Create these exact tests and assertions:

1. `test_public_dataclass_fields_and_type_hints_are_exact`: compare `dataclasses.fields()` names and `typing.get_type_hints()` to every field table in this plan; assert only the three hard flags have defaults and each default is `True`.
2. `test_public_dataclasses_are_frozen_slotted_runtime_final_and_exact_typed`: mutation raises `FrozenInstanceError`, every instance lacks `__dict__`, subclass creation raises `TypeError`, direct constructors reject subclass/mocked nested values, and all hard flags reject `False`, `1`, or omitted constructor-bypass state.
3. `test_identifiers_and_sha256_digests_are_strict_and_canonical`: accept only 1-160 UTF-8 bytes of lowercase ASCII `a-z0-9._-:` beginning and ending alphanumeric; reject uppercase, whitespace, slash, URL punctuation, controls, non-ASCII, 161-byte input, and punctuation endpoints; accept exactly 64 lowercase hex digest characters and reject uppercase, wrappers, lengths 63/65, and nonhex text.
4. `test_decimal_inputs_check_exact_type_finiteness_and_raw_bounds_before_quantization`: reject `int`, `bool`, `float`, Decimal subclasses, NaN, sNaN, infinities, `-0.0000004`, and `1.0000004` for `[0,1]` values; assert `0.1234565` rounds HALF_EVEN to `0.123456`, `0.1234575` to `0.123458`, and exact negative zero becomes unsigned `Decimal("0.000000")` with `is_signed() is False`.
5. `test_type_decimal_normalization_ignores_hostile_ambient_context`: replace ambient precision/rounding inside `try/finally`, construct every Decimal-bearing type, and assert byte-for-byte-equivalent fixed-six fields while restoring the original ambient context.
6. `test_datetime_inputs_require_exact_aware_datetime_and_normalize_to_utc`: reject naive values and datetime subclasses; assert `2026-07-13T12:00:00.123456+02:00` becomes exact `2026-07-13T10:00:00.123456+00:00` without losing microseconds.
7. `test_collection_fields_require_exact_tuples_sort_semantic_sets_and_reject_duplicates`: reject list/generator/set/string/bytes collections; assert requirement IDs, requirements, records, current selections, witnesses, coverage, diagnostics, and reason codes use their specified canonical order; reject every duplicate rather than deduplicating.
8. `test_config_requires_all_policy_fields_and_has_no_default_policy`: inspect the signature and assert every field before `paper_only` has no default; omitting each field raises `TypeError`; assert there is no `DEFAULT_CONFIG` export or module attribute.
9. `test_config_validates_relationships_and_nonoverridable_resource_maxima`: assert durations/caps are finite and nonnegative, caps and minimum weights are at most one, exact ints are positive with bool rejected, contradiction bounds satisfy strict canonical `no < yes`, watch score is at most block score, publication floor is at most ceiling, IDs are unique/sorted, requirement count fits the caller ceiling, and caller ceilings cannot exceed `32`, `128`, `32`, `1024`, and `256` respectively.
10. `test_contradiction_probability_bounds_validate_raw_values_before_canonical_order`: reject either raw bound outside `[0,1]`; reject equality/reversal; reject raw `0.2000004 < 0.2000005` because both quantize to `0.200000` under HALF_EVEN.
11. `test_record_constructor_enforces_all_local_cross_layer_relationships`: independently alter capture lineage ID/digest, evidence lineage ID/digest, capture content digest, assessment evidence ID/digest, nested exact types, and nested hard flags; each raises field-specific `ValueError`.
12. `test_derived_types_enforce_exact_scalars_statuses_weights_and_sorted_children`: cover exact IDs/digests/bools/counts, fixed-six values, allocation `0 <= effective <= independence <= requested`, witness/coverage identity and count consistency, the thirteen exact diagnostic dispositions, contradiction statuses `none/watch/blocked`, result statuses `ready/watch/blocked`, optional probabilities, exact sorted child tuples/reason codes, and digest syntax. Per-record probabilities and weights are in `[0,1]`; aggregate result totals and contradiction support weights are finite nonnegative fixed-six values that may exceed one; temporal ages/lags are finite signed fixed-six values.

Also write Node 2A's child-local tests now so they precede all production modules. They intentionally remain red until Tasks 3 and 4 create the other two modules:

13. `test_node_2a_production_imports_equal_exact_allowlists`.
14. `test_node_2a_literal_all_tuples_equal_exact_public_surfaces`.
15. `test_node_2a_ast_rejects_forbidden_references_calls_and_float_surfaces`.
16. `test_node_2a_ast_locks_dataclass_flags_policy_defaults_and_resource_maxima`.
17. `test_node_2a_physical_line_ceilings_are_enforced`.

The exact AST and line assertions are specified in Task 5. Do not weaken them to substring-only production scanning.

- [ ] **Step 2: Run the initial RED subset**

```bash
.venv/bin/python -m pytest -q \
  tests/test_team_evidence_aggregation_types.py \
  -k 'public_dataclass or identifiers or decimal_inputs or type_decimal or datetime_inputs or collection_fields or config_ or contradiction_probability or record_constructor or derived_types'
```

Expected: collection fails because the new test module cannot import `team_evidence_aggregation_types`; no unrelated test is selected.

- [ ] **Step 3: Implement the exact dataclasses and canonical private helpers**

Use only these imports in the types module:

```python
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import re
from typing import Final, final
```

Define private constants with these exact values; do not export them:

```python
_QUANTUM: Final = Decimal("0.000001")
_ZERO: Final = Decimal("0.000000")
_ONE: Final = Decimal("1.000000")
_DECIMAL_CONTEXT: Final = Context(prec=64, rounding=ROUND_HALF_EVEN)
_MAX_ASSIGNMENTS_PER_EVIDENCE: Final = 32
_MAX_RECORDS: Final = 128
_MAX_REQUIREMENTS: Final = 32
_MAX_REQUIREMENT_MEMBERSHIPS: Final = 1024
_MAX_WITNESS_EDGES: Final = 256
```

Implement one private direct-subclass base that permits only the sixteen declared direct public classes and rejects every subclass of those classes at runtime; also decorate each public class with `@final`. Every class uses `@dataclass(frozen=True, slots=True)` and an exact-type check in `__post_init__`. `object.__setattr__` may only install values returned by the canonical private validators.

Private scalar helpers must follow this order:

1. Require exact base type (`type(value) is ...`), rejecting bool where int is expected.
2. Validate raw finiteness and raw bounds before quantization.
3. Perform every Decimal operation inside `localcontext(_DECIMAL_CONTEXT)`.
4. Quantize to `_QUANTUM` with HALF_EVEN.
5. Return `_ZERO` for every quantized zero so signed zero cannot survive.
6. Convert only exact aware datetime values with `value.astimezone(UTC)`; preserve six microsecond digits.
7. Validate identifiers with one full-match ASCII regex plus the 160-byte UTF-8 bound; validate digests as exactly 64 lowercase hex characters.

Canonical collection behavior is fixed:

- `requirement_ids`: exact tuple, each canonical ID, duplicates rejected, sorted lexically.
- `requirements`: exact tuple of exact requirement objects, duplicate `requirement_id` rejected, sorted by `requirement_id`.
- `records`: exact tuple of exact records, duplicate canonical record keys rejected, sorted by `(assessment_revision_id, evidence_revision_id, captured_at, capture_id, source_lineage_id)`.
- `current_revisions`: exact tuple, duplicate `(evidence_revision_id, assessment_revision_id)` rejected, sorted by that pair.
- witnesses: exact tuple, duplicate witness identity rejected, sorted by `(requirement_id, source_lineage_id, evidence_revision_id, assessment_revision_id, capture_id)`.
- coverage: sorted by `requirement_id`; diagnostics: sorted by
  `(assessment_revision_id, evidence_revision_id, captured_at, capture_id,
  source_lineage_id)`, the complete canonical record key projected from each
  row; reason codes: unique and lexically sorted.

Use the exact status sets and the exact thirteen dispositions from the design. Constructors enforce local shape and monotonic weight/count invariants; they do not perform Node 2B allocation, Node 2C result rematerialization, or infer policy.

- [ ] **Step 4: Run the type/scalar GREEN subset**

```bash
.venv/bin/python -m pytest -q \
  tests/test_team_evidence_aggregation_types.py \
  -k 'public_dataclass or identifiers or decimal_inputs or type_decimal or datetime_inputs or collection_fields or config_ or contradiction_probability or record_constructor or derived_types'
```

Expected: all selected tests pass. The five `node_2a_...` child-scope tests remain intentionally unselected until all three production files exist.

---

### Task 2: Complete Input-Graph Validation And Both Canonical Selectors

**Files:**
- Modify: `tests/test_team_evidence_aggregation_types.py`
- Modify: `src/polymarket_alpha_lab/team_evidence_aggregation_types.py`

**Interfaces:**
- Consumes: the exact immutable values and canonical sort keys from Task 1.
- Produces: the complete public input-contract validator, evidence-global canonical-capture selector, and exact-current-pair selector. Node 2C later uses these selectors as its sole validation and identity boundary.

- [ ] **Step 1: Add decisive graph, recapture, selection, and selector tests**

Create these exact tests with direct equality assertions on returned tuples and stable `ValueError` field-path matches:

1. `test_validate_input_contract_returns_exact_none_for_valid_closed_graph`: assert `is None` for one lineage and for two independently valid lineages.
2. `test_validate_input_contract_accepts_empty_records_and_empty_selections`: assert `None`; empty input is structurally valid even though Node 2C later returns blocked.
3. `test_validate_input_contract_enforces_record_requirement_and_distinct_revision_membership_limits`: independently exceed caller and hard maxima; count each distinct evidence-revision projection's requirement IDs once regardless of recaptures/assessments; assert failure occurs before graph traversal by using malformed overflow tails.
4. `test_validate_input_contract_rejects_functional_dependency_conflicts`: parameterize source-lineage, capture, evidence-revision, and assessment-revision IDs reused with a changed owned field; assert whole-call failure.
5. `test_validate_input_contract_rejects_same_layer_digest_aliases`: for each of the four identity layers, map one digest to two IDs and assert failure; distinct captures sharing `content_digest` remain valid.
6. `test_validate_input_contract_rejects_invalid_evidence_revision_graphs`: parameterize missing predecessor, predecessor digest mismatch, second root, fork, merge attempt, disconnected component, cycle, and cross-lineage edge.
7. `test_validate_input_contract_rejects_invalid_assessment_revision_graphs`: parameterize the same failures, including predecessor assessment attached to evidence outside the lineage chain.
8. `test_validate_input_contract_rejects_evidence_noops_and_invalid_anchor_changes`: reject content/requirements/anchor unchanged; reject `recorded_at`-only; reject anchor-only; reject anchor plus requirements without changed content; accept changed content, changed requirements with stable anchor, and changed content plus changed anchor.
9. `test_validate_input_contract_rejects_assessment_noops`: reject `assessed_at`-only; accept a change to any one of evidence reference, `probability_yes`, requested weight, rationale digest, independence key, or correlation key.
10. `test_recapture_may_change_only_capture_identity_and_time`: accept a new capture ID/digest/time with unchanged lineage/content/evidence/assessment projections; under reused revision IDs reject changes to requirement IDs, freshness anchor, evidence `recorded_at`, probability, requested weight, rationale digest, independence/correlation key, or assessment `assessed_at`.
11. `test_validate_input_contract_enforces_nondecreasing_edge_times_and_accepts_equal_times`: reject successor `recorded_at`/`assessed_at` earlier than predecessor; accept equal timestamps because graph edges supply order.
12. `test_validate_input_contract_enforces_local_availability_order`: canonical earliest capture must be at or before evidence `recorded_at`, and evidence `recorded_at` must be at or before every assessment `assessed_at`; later recaptures after either time remain valid duplicates.
13. `test_validate_input_contract_rejects_unresolved_or_mismatched_current_selection`: reject absent exact pair, any selected digest mismatch, selected assessment linked to another evidence revision, and a selected pair represented only on a later recapture.
14. `test_validate_input_contract_rejects_two_current_pairs_in_one_lineage`: cover same evidence/different assessment and different evidence/different assessment.
15. `test_validate_input_contract_accepts_exact_old_pair_while_successors_are_present`: assert an old non-tip pair remains valid and selected; tuple order and newer timestamps do not supersede it.
16. `test_select_canonical_current_records_returns_empty_tuple`: exact `()` for empty selections.
17. `test_select_canonical_current_records_returns_one_exact_record_per_selection_in_canonical_order`: use two lineages in reversed input/selection order and assert exact canonical identities/order.
18. `test_select_canonical_current_records_uses_evidence_global_earliest_capture_across_assessments`: put the earliest capture only with one assessment and the selected assessment on both earliest and later captures; assert the selected result uses the global earliest `(captured_at, capture_id, capture_digest)`.
19. `test_select_canonical_current_records_rejects_missing_canonical_combination`: earliest capture exists with another assessment but not the selected assessment; assert failure instead of choosing the selected assessment's later capture.
20. `test_select_canonical_current_records_has_no_temporal_latest_or_tip_fallback`: selected old pair is returned even when stale/future relative to evaluation and successors exist; no alternate record is returned.
21. `test_select_canonical_capture_records_returns_each_assessment_on_each_revision_global_capture`: assert one record per represented assessment on the earliest capture for each represented evidence revision.
22. `test_select_canonical_capture_records_includes_wholly_noncurrent_revisions`: exact identities include old/nonselected evidence revisions and exclude every later recapture.
23. `test_both_selectors_share_complete_validation_and_agree_on_current_identity`: feed each hostile graph case to both selectors and assert both reject; for valid input, every current-selector result belongs to the canonical-capture-selector result.

Use this exact canonical record key in expected tuples:

```python
def expected_record_key(record: TeamEvidenceAggregationRecord) -> tuple[object, ...]:
    return (
        record.assessment_revision.assessment_revision_id,
        record.evidence_revision.evidence_revision_id,
        record.capture.captured_at,
        record.capture.capture_id,
        record.source_lineage.source_lineage_id,
    )
```

- [ ] **Step 2: Run the graph/selector RED tests**

```bash
.venv/bin/python -m pytest -q tests/test_team_evidence_aggregation_types.py \
  -k 'validate_input_contract or select_canonical or both_selectors'
```

Expected: the new public validator/selectors are absent or incomplete and the selected tests fail; Task 1 tests remain unselected.

- [ ] **Step 3: Implement one shared complete validation/indexing path**

Implement a private `_validated_input_indexes(aggregation_input, *, config)` called independently by all three public functions. It must not accept a `validated=True` shortcut, cache ambient state, or offer a weaker mode. Its exact sequence is:

1. Revalidate exact `TeamEvidenceAggregationInput` and `TeamEvidenceAggregationConfig` types, every nested exact type, every hard flag, and every canonical scalar/tuple. Do not trust prior constructors or constructor-bypassed objects.
2. Check `len(records)` against caller `maximum_records` and hard `128`, and `len(requirements)` against caller `maximum_requirements` and hard `32`, before expensive graph work.
3. Canonically sort records. Reject duplicate `(capture_id, assessment_revision_id)` and duplicate canonical record keys.
4. Build four ID-to-complete-projection maps and four same-layer digest-to-ID maps. Reject one ID mapping to different owned fields and one digest aliasing two IDs. Complete projections include every field owned by that layer, including predecessor references, canonical tuples/times/numbers, and hard flags.
5. Build the set of distinct evidence-revision projections. Sum each projection's `requirement_ids` length once; enforce caller `maximum_requirement_memberships` and hard `1024` before graph traversal.
6. Recheck every record's local references: lineage IDs/digests, content digest, evidence IDs/digests, and nested hard flags.
7. For each lineage, validate evidence revisions as exactly one connected linear chain: predecessor ID/digest both absent or both present, every predecessor represented in that lineage, exactly one root, at most one successor per node, traversal from root visits every node once, and no cycle/fork/disconnected component. Functional-dependency checks already reject merge-shaped conflicting projections.
8. Validate every evidence edge: same lineage, new ID/digest, nondecreasing `recorded_at`, and a semantic change. A valid edge changes content digest or canonical requirement IDs; an anchor change additionally requires changed content. `recorded_at` alone is never semantic.
9. Assign each assessment revision to the lineage of its referenced evidence. Per lineage, validate one complete linear assessment chain with the same root/predecessor/digest/connectivity rules.
10. Validate every assessment edge: predecessor belongs to the same lineage evidence chain, nondecreasing `assessed_at`, and at least one change among evidence reference, canonical probability, requested weight, rationale digest, independence key, or correlation key. `assessed_at` alone is never semantic.
11. Deduplicate capture projections per evidence revision, sort them by `(captured_at, capture_id, capture_digest)`, and select the first as that evidence revision's sole structural canonical capture. Require it at or before the evidence revision's `recorded_at`. Require each evidence `recorded_at` at or before every assessment `assessed_at` that references it. Later recaptures may be later than either audit time.
12. Validate current selections as a semantic set sorted by `(evidence_revision_id, assessment_revision_id)`. Each exact four-ID/digest pair must resolve to supplied records; at most one pair may be selected per lineage.
13. Require each selected assessment/evidence pair to have a supplied record on the evidence-global canonical capture. Do not choose a later capture, chain tip, latest timestamp, lexical maximum, or tuple-position fallback.
14. Return private immutable indexes sufficient for both selectors; do not expose them.

Node 2A validates that `maximum_witness_edges` is a positive exact int no greater than hard `256`, but it does not derive or count candidate witness edges. Candidate edges depend on temporal selection and effective allocation and remain exclusively Node 2B witness-builder work.

Implement public behavior exactly:

```python
def validate_team_evidence_aggregation_input_contract(
    aggregation_input: TeamEvidenceAggregationInput,
    *,
    config: TeamEvidenceAggregationConfig,
):
    _validated_input_indexes(aggregation_input, config=config)
    return None
```

The current selector returns one existing record combining each exact selected pair with its evidence-global canonical capture, sorted by the canonical record key. The capture selector returns every supplied record whose capture projection equals its evidence revision's global canonical capture, across all evidence and assessment revisions, sorted by the same key. Neither selector applies temporal policy, allocation, dispositions, witnesses, status, or source-authority validation.

Every failure raises `ValueError` containing a canonical field/relationship path and a stable contract description, never raw rationale/content, object repr, credentials, or unrestricted input text.

- [ ] **Step 4: Run the graph/selector GREEN tests and complete types tests**

```bash
.venv/bin/python -m pytest -q tests/test_team_evidence_aggregation_types.py \
  -k 'not node_2a_'
```

Expected: every behavioral types/graph/selector test passes. Only the deliberately unselected child-scope tests await Tasks 3-5.

---

### Task 3: Strict Canonical Codec And Literal Digest Vectors

**Files:**
- Create: `tests/test_team_evidence_aggregation_codec.py`
- Create: `src/polymarket_alpha_lab/team_evidence_aggregation_codec.py`

**Interfaces:**
- Consumes: exact canonical Node 2A dataclasses from the defining sibling module.
- Produces: fresh canonical config/input/core/full-result fragments, domain-separated config/core SHA-256 digests, and fail-closed core-digest validation.

The four outer envelopes are closed. Their exact outer key/value contracts are:

- Config payload: outer keys exactly `schema_version` and `config`; `schema_version` is `pal.team_evidence_aggregation.config.v1`; `config` is the direct map with exactly `config_version`, `max_evidence_age_seconds`, `max_capture_lag_seconds`, `independence_group_weight_cap`, `correlation_group_weight_cap`, `max_requirement_assignments_per_evidence`, `contradiction_no_probability_max`, `contradiction_yes_probability_min`, `contradiction_watch_score`, `contradiction_block_score`, `publish_probability_floor`, `publish_probability_ceiling`, `maximum_records`, `maximum_requirements`, `maximum_requirement_memberships`, `maximum_witness_edges`, `requirements`, `paper_only`, `report_only`, and `readonly`.
- Input payload: outer keys exactly `schema_version` and `input`; `schema_version` is `pal.team_evidence_aggregation.input.v1`; `input` is the direct map with exactly `evaluated_at`, `records`, `current_revisions`, `paper_only`, `report_only`, and `readonly`. Each record is the direct map with exactly `source_lineage`, `capture`, `evidence_revision`, `assessment_revision`, `paper_only`, `report_only`, and `readonly`.
- Core payload: outer keys exactly `schema_version` and `result`; `schema_version` is `pal.team_evidence_aggregation.core.v1`; `result` has exactly `evaluated_at`, `config_version`, `config_digest`, `status`, `diagnostic_record_count`, `arithmetic_record_count`, `requested_weight_total`, `independence_allocated_weight_total`, `effective_weight_total`, `arithmetic_probability_yes`, `publishable_probability_yes`, `contradiction`, `requirement_coverage`, `diagnostics`, `reason_codes`, `paper_only`, `report_only`, and `readonly`.
- Full result payload: outer keys exactly `schema_version` and `result`; `schema_version` is `pal.team_evidence_aggregation.result.v1`; `result` has every core-result direct-map key above plus `core_digest` and no other key.

Every nested public dataclass uses exactly its direct field map from the field table in this plan. There are no other wrappers, keys, omission rules, type tags, or schema variants.

- [ ] **Step 1: Write exact envelope, rendering, isolation, and digest tests**

Duplicate the minimal non-BTC config fixture in this test module; do not import helpers from another test module. Construct a semantically plausible zero-arithmetic result with one unmet blocked requirement:

```python
TeamEvidenceAggregationResult(
    evaluated_at=datetime(2026, 7, 13, 12, 0, 0, 123456, tzinfo=UTC),
    config_version="agg-test-v1",
    config_digest=EXPECTED_CONFIG_DIGEST,
    status="blocked",
    diagnostic_record_count=0,
    arithmetic_record_count=0,
    requested_weight_total=Decimal("0.000000"),
    independence_allocated_weight_total=Decimal("0.000000"),
    effective_weight_total=Decimal("0.000000"),
    arithmetic_probability_yes=None,
    publishable_probability_yes=None,
    contradiction=TeamEvidenceContradictionResult(
        yes_support_weight=Decimal("0.000000"),
        no_support_weight=Decimal("0.000000"),
        neutral_weight=Decimal("0.000000"),
        contradiction_score=Decimal("0.000000"),
        status="none",
    ),
    requirement_coverage=(
        TeamEvidenceRequirementCoverage(
            requirement_id="macro.release",
            minimum_witness_count=1,
            assigned_witness_count=0,
            minimum_effective_weight=Decimal("0.120000"),
            unmet_status="blocked",
            satisfied=False,
            witnesses=(),
        ),
    ),
    diagnostics=(),
    reason_codes=("blocking_requirement_unmet", "no_arithmetic_evidence"),
    core_digest="0" * 64,
)
```

Create these exact tests:

1. `test_config_payload_has_exact_closed_envelope_and_direct_field_maps`: assert outer keys are exactly `{"schema_version", "config"}`, literal schema is `pal.team_evidence_aggregation.config.v1`, config keys exactly match the documented fields, requirement keys exactly match its dataclass fields, and every hard flag is present.
2. `test_input_payload_has_exact_closed_envelope_and_every_nested_direct_map`: use a one-record input and assert outer/input/record/source/capture/evidence/assessment/selection key sets exactly, with no class tags, nested schema wrappers, extension keys, or omissions.
3. `test_core_and_full_result_payloads_have_exact_distinct_envelopes`: assert core schema `pal.team_evidence_aggregation.core.v1`, full schema `pal.team_evidence_aggregation.result.v1`, sole wrapper `result`, core result has every result field except `core_digest`, and full result has every field including validated `core_digest`.
4. `test_codec_renders_fixed_six_decimal_microsecond_utc_arrays_ints_bools_and_null`: assert Decimal strings have six places/no exponent/unsigned zero, datetime is exactly `2026-07-13T12:00:00.123456+00:00`, tuples become lists, counts remain JSON ints rather than bool/string, and optionals become `None`.
5. `test_payload_trees_contain_no_float_or_non_json_ready_value`: recursively assert no `float`, Decimal, datetime, tuple, bytes, set, arbitrary object, or non-string mapping key survives.
6. `test_semantic_tuple_permutations_produce_equal_payloads_and_bytes`: reverse records, selections, requirements, requirement IDs, witnesses, coverage, diagnostics, and reason codes at constructor inputs; canonical objects/payloads and `json.dumps(... exact options ...)` bytes remain equal.
7. `test_config_digest_matches_literal_domain_separated_vector`: assert the exact canonical bytes and digest below.
8. `test_core_digest_matches_literal_domain_separated_vector`: replace the provisional digest with the computed literal and assert exact canonical core bytes and digest below.
9. `test_payload_calls_return_fresh_mutation_isolated_dictionaries`: mutate every level of one returned tree and assert a second call and the frozen source object are unchanged; assert no core dict is extended in place to make a full payload.
10. `test_public_codec_functions_reject_wrong_exact_dataclass_types`: pass subclasses/mocks/other Node 2 types to each entry point and assert `ValueError`.
11. `test_codec_rejects_constructor_bypassed_noncanonical_values`: use `object.__new__`/`object.__setattr__` to inject a float, non-fixed-six Decimal exponent, signed zero, naive/non-UTC datetime, list, false hard flag, and unknown object; every applicable payload/digest helper raises.
12. `test_core_payload_covers_every_result_field_except_only_core_digest`: derive expected names from `dataclasses.fields`, assert exact equality, and prove changing each included field changes the digest or fails canonical validation.
13. `test_validate_core_digest_returns_exact_none_and_rejects_tampering`: valid returns `is None`; stored digest mismatch, uppercase/truncated digest, and any post-construction bypassed field tamper raise `ValueError`.
14. `test_full_payload_fails_closed_before_emitting_invalid_core_digest`: invalid result raises and no partial mapping is returned.
15. `test_codec_has_no_decoder_legacy_projection_or_node3_identity`: assert the public surface is exactly `__all__` and recursively assert no keys/schema values for `tea:v1`, `tfr:v1`, `tfe:v1`, run/forecast/evidence IDs, receipts, or legacy projections.

Pin these literal vectors. The canonical config bytes are exactly this single UTF-8 line:

```json
{"config":{"config_version":"agg-test-v1","contradiction_block_score":"0.670000","contradiction_no_probability_max":"0.210000","contradiction_watch_score":"0.310000","contradiction_yes_probability_min":"0.790000","correlation_group_weight_cap":"0.610000","independence_group_weight_cap":"0.730000","max_capture_lag_seconds":"300.000000","max_evidence_age_seconds":"7200.000000","max_requirement_assignments_per_evidence":3,"maximum_records":8,"maximum_requirement_memberships":16,"maximum_requirements":4,"maximum_witness_edges":12,"paper_only":true,"publish_probability_ceiling":"0.890000","publish_probability_floor":"0.110000","readonly":true,"report_only":true,"requirements":[{"minimum_effective_weight":"0.120000","minimum_witness_count":1,"paper_only":true,"readonly":true,"report_only":true,"requirement_id":"macro.release","unmet_status":"blocked"}]},"schema_version":"pal.team_evidence_aggregation.config.v1"}
```

The config digest is SHA-256 over `b"pal.team_evidence_aggregation.config.v1\x00" + canonical_config_bytes` and must equal:

```text
1fda9ece725bedd42782ff9af1e0cbe7105a5a33036609066e023696a0acb675
```

The canonical core bytes are exactly:

```json
{"result":{"arithmetic_probability_yes":null,"arithmetic_record_count":0,"config_digest":"1fda9ece725bedd42782ff9af1e0cbe7105a5a33036609066e023696a0acb675","config_version":"agg-test-v1","contradiction":{"contradiction_score":"0.000000","neutral_weight":"0.000000","no_support_weight":"0.000000","paper_only":true,"readonly":true,"report_only":true,"status":"none","yes_support_weight":"0.000000"},"diagnostic_record_count":0,"diagnostics":[],"effective_weight_total":"0.000000","evaluated_at":"2026-07-13T12:00:00.123456+00:00","independence_allocated_weight_total":"0.000000","paper_only":true,"publishable_probability_yes":null,"readonly":true,"reason_codes":["blocking_requirement_unmet","no_arithmetic_evidence"],"report_only":true,"requested_weight_total":"0.000000","requirement_coverage":[{"assigned_witness_count":0,"minimum_effective_weight":"0.120000","minimum_witness_count":1,"paper_only":true,"readonly":true,"report_only":true,"requirement_id":"macro.release","satisfied":false,"unmet_status":"blocked","witnesses":[]}],"status":"blocked"},"schema_version":"pal.team_evidence_aggregation.core.v1"}
```

The core digest is SHA-256 over `b"pal.team_evidence_aggregation.core.v1\x00" + canonical_core_bytes` and must equal:

```text
d3c968df35caeb7e8ba1b411029e200117bee21f8c9ee14bda6954679cf1e6cb
```

- [ ] **Step 2: Run codec RED**

```bash
.venv/bin/python -m pytest -q tests/test_team_evidence_aggregation_codec.py
```

Expected: collection fails because `team_evidence_aggregation_codec` does not exist.

- [ ] **Step 3: Implement explicit closed-map canonical serialization**

Use only this exact import surface:

```text
__future__, dataclasses, datetime, decimal, hashlib, json, typing,
polymarket_alpha_lab.team_evidence_aggregation_types
```

Implementation is fixed:

1. Define literal schema/domain constants for config, input, core, and result v1 strings.
2. Maintain a private exact-class-to-field-names table covering all sixteen public dataclasses. Field names are taken in declared order, but canonical JSON uses sorted map keys.
3. For each public entry point, require the exact documented top-level type and reconstruct/revalidate direct fields through the corresponding exact dataclass constructor. Additionally check Decimal exponent `-6`, unsigned zero, exact aware UTC datetime, exact tuple/int/bool/string/`None`, and exact registered nested dataclass classes so constructor-bypassed values cannot pass by Decimal numeric equality.
4. Encode a registered dataclass as a newly allocated direct field map with no type/class tag. Encode tuple recursively to a new list. Encode Decimal with `format(value, ".6f")`. Encode UTC datetime with `strftime`-equivalent exact `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`. Preserve exact ints, bools, strings, and `None`.
5. Reject all floats, NaN/infinity, bytes, sets, mappings supplied as dataclass substitutes, unknown objects, non-string keys, and malformed nested values with `ValueError`.
6. Build every outer envelope from scratch. Core payload omits only `core_digest`. Full result payload first calls `validate_team_evidence_aggregation_core_digest`, then independently rematerializes every field including the digest; it never mutates a core payload.
7. Canonical bytes use exactly:

```python
json.dumps(
    payload,
    allow_nan=False,
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
```

8. Config digest hashes `b"pal.team_evidence_aggregation.config.v1\x00" + canonical_config_bytes` with SHA-256. Core digest hashes `b"pal.team_evidence_aggregation.core.v1\x00" + canonical_core_bytes`.
9. `team_evidence_aggregation_core_digest` recomputes from the core payload and does not trust the stored digest. `validate_team_evidence_aggregation_core_digest` exact-compares stored/recomputed lowercase hex, returns `None` on equality, and raises `ValueError` otherwise.
10. Add no decoder, generic serializer, legacy mode, packet projection, input digest, replay digest, or Node 3 identifier.

- [ ] **Step 4: Run codec GREEN**

```bash
.venv/bin/python -m pytest -q tests/test_team_evidence_aggregation_codec.py
```

Expected: all codec tests pass, including both literal digest vectors.

---

### Task 4: Exact Temporal Eligibility And Availability Layers

**Files:**
- Create: `tests/test_team_evidence_aggregation_temporal.py`
- Create: `src/polymarket_alpha_lab/team_evidence_aggregation_temporal.py`

**Interfaces:**
- Consumes: exact `TeamEvidenceAggregationRecord`, explicit `evaluated_at`, and exact immutable config.
- Produces: one exact `TeamEvidenceTemporalAssessment`; it does not decide current/canonical identity, allocation, disposition, witness coverage, or status.

- [ ] **Step 1: Write temporal formula, boundary, replay, and context tests**

Define local fixtures with these exact instants:

```python
freshness_anchor_at = datetime(2026, 7, 13, 10, 0, 0, 125000, tzinfo=UTC)
captured_at = datetime(2026, 7, 13, 10, 4, 30, 375000, tzinfo=UTC)
recorded_at = datetime(2026, 7, 13, 10, 5, 0, 125000, tzinfo=UTC)
assessed_at = datetime(2026, 7, 13, 10, 6, 0, 125000, tzinfo=UTC)
evaluated_at = datetime(2026, 7, 13, 11, 0, 0, 625000, tzinfo=UTC)
```

This yields exact `evidence_age_seconds == Decimal("3600.500000")` and `capture_lag_seconds == Decimal("270.250000")`.

Create these exact tests:

1. `test_temporal_assessment_uses_exact_anchor_age_and_capture_lag_formulas`: assert every ID, both exact deltas, all four availability/effectiveness booleans, `fresh`, `timely`, and hard flags.
2. `test_temporal_age_and_lag_limits_are_inclusive`: set limits exactly equal to `3600.500000` and `270.250000`; assert `fresh is True` and `timely is True`; add one microsecond and assert the applicable gate becomes false.
3. `test_future_anchor_retains_negative_age_and_is_not_fresh`: assert exact negative age, `effective_at_evaluation is False`, `fresh is False`, and no clamping.
4. `test_future_capture_is_independently_unavailable`: assert `captured_at_evaluation is False` while anchor may be effective/fresh; capture lag remains its exact signed diagnostic value.
5. `test_capture_before_anchor_and_excessive_lag_are_not_timely`: negative lag and limit-plus-one-microsecond lag each set `timely is False`; age remains anchor-based.
6. `test_stale_evidence_uses_anchor_not_capture_or_recording_time`: move capture/recorded/assessed times later while retaining the anchor and assert unchanged age/freshness; a recent recapture cannot refresh stale evidence.
7. `test_evidence_and_assessment_revision_availability_are_independent`: at equality each is available; one microsecond in the future independently flips only its own boolean. A future assessment cannot leak into a historical replay even when capture/evidence revision are available.
8. `test_timezone_offsets_are_equivalent_and_microseconds_are_preserved`: equivalent UTC and non-UTC-offset inputs produce equal assessments and exact fixed-six deltas.
9. `test_temporal_assessment_rejects_wrong_exact_types_naive_time_and_false_flags`: wrong record/config classes, datetime subclass/naive value, and constructor-bypassed false flags raise `ValueError`.
10. `test_temporal_assessment_is_invariant_to_hostile_ambient_decimal_context`: set ambient precision low and rounding away from HALF_EVEN inside `try/finally`; typed result and codec-ready values remain exactly equal to baseline, and ambient context is restored.

- [ ] **Step 2: Run temporal RED**

```bash
.venv/bin/python -m pytest -q tests/test_team_evidence_aggregation_temporal.py
```

Expected: collection fails because `team_evidence_aggregation_temporal` does not exist.

- [ ] **Step 3: Implement explicit-clock temporal arithmetic**

Use only this exact normalized import surface:

```text
__future__, datetime, decimal, typing,
polymarket_alpha_lab.team_evidence_aggregation_types
```

At the public boundary, require exact record/config classes, exact true hard flags throughout the record, and an exact base aware `datetime`; normalize `evaluated_at` to UTC. Do not call the global input validator because this function assesses one already structured record and has no aggregation input.

Implement timedelta conversion exactly inside a local `Context(prec=64, rounding=ROUND_HALF_EVEN)`:

```python
seconds = (
    Decimal(delta.days) * Decimal(86400)
    + Decimal(delta.seconds)
    + Decimal(delta.microseconds) / Decimal(1000000)
)
normalized = seconds.quantize(Decimal("0.000001"))
```

Do not call `timedelta.total_seconds()` or `datetime.timestamp()`. Preserve negative ages/lags and canonicalize zero to unsigned fixed-six.

Calculate exactly:

```text
evidence_age_seconds = evaluated_at - freshness_anchor_at
capture_lag_seconds = captured_at - freshness_anchor_at
effective_at_evaluation = freshness_anchor_at <= evaluated_at
captured_at_evaluation = captured_at <= evaluated_at
evidence_revision_available_at_evaluation = recorded_at <= evaluated_at
assessment_revision_available_at_evaluation = assessed_at <= evaluated_at
fresh = effective_at_evaluation and evidence_age_seconds <= max_evidence_age_seconds
timely = capture_lag_seconds >= 0 and capture_lag_seconds <= max_capture_lag_seconds
```

Return one `TeamEvidenceTemporalAssessment`. Do not combine the booleans into a new public `eligible` field. Convert only Decimal-domain exceptions to field-specific `ValueError`; do not catch broad exceptions. Never read an ambient clock or Decimal context.

- [ ] **Step 4: Run temporal GREEN**

```bash
.venv/bin/python -m pytest -q tests/test_team_evidence_aggregation_temporal.py
```

Expected: all temporal tests pass.

---

### Task 5: Complete The Node 2A Child-Local AST, Import, Export, Forbidden-Surface, And Size Gate

**Files:**
- Modify: `tests/test_team_evidence_aggregation_types.py`
- Verify: all six implementation allowlist paths

**Interfaces:**
- Consumes: all three completed Node 2A production modules and all three test files.
- Produces: an executable local safety gate that cannot defer a Node 2A import, export, forbidden-surface, policy-default, hard-maximum, or physical-size failure to Node 2C.

- [ ] **Step 1: Finish the exact AST gate assertions written before production**

The gate parses source using `ast.parse`. It normalizes imports exactly:

- `ast.Import`: compare each `alias.name` as one absolute module.
- `ast.ImportFrom`: require `level == 0`, require nonempty `module`, and compare that absolute module; member names and aliases do not alter the module key.
- Reject every relative import even when it would resolve to an allowed sibling.

Use these literal per-file allowlists:

```python
EXPECTED_IMPORTS = {
    "team_evidence_aggregation_types.py": {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "decimal",
        "re",
        "typing",
    },
    "team_evidence_aggregation_codec.py": {
        "__future__",
        "dataclasses",
        "datetime",
        "decimal",
        "hashlib",
        "json",
        "typing",
        "polymarket_alpha_lab.team_evidence_aggregation_types",
    },
    "team_evidence_aggregation_temporal.py": {
        "__future__",
        "datetime",
        "decimal",
        "typing",
        "polymarket_alpha_lab.team_evidence_aggregation_types",
    },
}
```

Assert each module's literal `__all__` AST assignment is one tuple of string constants exactly equal, including order, to the locked interfaces in this plan. Reject computed, missing, extra, reordered, or duplicate exports. All private validation, graph, projection, canonical JSON, and timedelta helpers remain absent from `__all__`.

AST inspection must reject:

- imports/references for database, Supabase, PostgreSQL, SQL, migration, persistence, filesystem I/O, JSONL, CSV, cache, temporary files, network, HTTP, socket, browser, scraping, external clients, CLI, environment, subprocess/process execution, logging, auth, account, credential, token, wallet, key, signing, order, sizing, capital allocation, execution, exchange mutation, and trading;
- calls to `open`, `print`, `input`, `eval`, `exec`, `compile`, dynamic import, `datetime.now`, `datetime.utcnow`, randomness, or built-in `hash`;
- calls to `timedelta.total_seconds()` or `datetime.timestamp()`;
- float literals, `float(...)`, and float annotations;
- BTC names, a default config, built-in `0.020000`/`0.980000`, Node 3 IDs, packet/legacy projection, receipt, DB-row, store, service, and persistence APIs.
- bare `except`, handlers for `BaseException`/`Exception`, callback parameters or calls through caller-supplied callables, object `repr` in payload/error construction, and error messages that expose values beyond canonical field names and IDs.

Use AST node categories and normalized identifiers/calls, with only a narrow source-text supplement for unsafe module tokens. Do not reject safe identifiers such as `correlation_group_weight_cap` because they contain an incidental substring.

The AST gate also asserts:

- all sixteen public classes are decorated/constructed as frozen and slotted dataclasses, runtime-final, exact-type checked, and carry the three exact-true hard flags;
- the only defaults on config are the three hard flags; every policy field is required;
- private hard maxima are exactly assignments `32`, `128`, `32`, `1024`, `256`, and config validation contains upper-bound checks that prevent caller relaxation;
- exact schema/domain strings are present only in the codec and match the four approved v1 names.

Enforce physical lines using `len(path.read_text(encoding="utf-8").splitlines())`:

```text
team_evidence_aggregation_types.py <= 900
team_evidence_aggregation_codec.py <= 500
team_evidence_aggregation_temporal.py <= 300
Node 2A production aggregate <= 1700

test_team_evidence_aggregation_types.py <= 900
test_team_evidence_aggregation_codec.py <= 600
test_team_evidence_aggregation_temporal.py <= 450
Node 2A test aggregate <= 1950
```

Comments and blank lines count. If a limit cannot be met without unreadable compression, stop for a reviewed contract split; do not add a seventh path.

- [ ] **Step 2: Run the child-local gate directly**

```bash
.venv/bin/python -m pytest -q tests/test_team_evidence_aggregation_types.py \
  -k 'node_2a_'
```

Expected: all five child-local gate tests pass and inspect all three production files plus all three test files.

- [ ] **Step 3: Run the exact Node 2A focused suite**

```bash
.venv/bin/python -m pytest -q \
  tests/test_team_evidence_aggregation_types.py \
  tests/test_team_evidence_aggregation_codec.py \
  tests/test_team_evidence_aggregation_temporal.py
```

Expected: exit `0`; all type, full graph, both selector, codec vector, temporal, and child-local static tests pass.

---

### Task 6: Full Verification, Exact Commit, Full-Range Claude Review, And Non-Force Push

**Files:** All and only the six exact implementation allowlist paths.

- [ ] **Step 1: Re-run focused, compile, full pytest, static Phase 1, CodeGraph, and diff hygiene gates**

```bash
set -euo pipefail
: "${NODE_BASE:?NODE_BASE must remain exported from the fetched preflight}"
: "${NODE1_PREREQUISITE_SHA:?NODE1_PREREQUISITE_SHA must remain exported}"
test "$NODE1_PREREQUISITE_SHA" = 46fbd5371782bfc0e654e547d67611ae416c7468
git merge-base --is-ancestor "$NODE1_PREREQUISITE_SHA" "$NODE_BASE"
git merge-base --is-ancestor "$NODE_BASE" HEAD

.venv/bin/python -m pytest -q \
  tests/test_team_evidence_aggregation_types.py \
  tests/test_team_evidence_aggregation_codec.py \
  tests/test_team_evidence_aggregation_temporal.py
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q \
  tests/test_phase1_live_surface_guard.py \
  tests/test_database_persistence_iron_rule.py \
  tests/test_supabase_durable_only_scope.py
codegraph sync .
git diff --check
```

Expected: every command exits `0`; focused tests include the child-local AST/size gate; compileall reports no syntax error; full pytest and static Phase 1 tests pass; CodeGraph sync succeeds; diff hygiene is clean. The local-Supabase gate passes because this pure node has no persistence or DSN surface, not because it opens any database.

- [ ] **Step 2: Stage the exact literal allowlist and prove staged equality**

```bash
set -euo pipefail
: "${NODE_BASE:?NODE_BASE is required}"
ALLOWLIST=(
  src/polymarket_alpha_lab/team_evidence_aggregation_codec.py
  src/polymarket_alpha_lab/team_evidence_aggregation_temporal.py
  src/polymarket_alpha_lab/team_evidence_aggregation_types.py
  tests/test_team_evidence_aggregation_codec.py
  tests/test_team_evidence_aggregation_temporal.py
  tests/test_team_evidence_aggregation_types.py
)
EXPECTED_PATHS="$(printf '%s\n' "${ALLOWLIST[@]}" | LC_ALL=C sort)"

git add -- "${ALLOWLIST[@]}"
ACTUAL_STAGED_PATHS="$(git diff --cached --name-only | LC_ALL=C sort)"
test "$ACTUAL_STAGED_PATHS" = "$EXPECTED_PATHS"
test "$(git diff --cached --name-only --diff-filter=ACMR | LC_ALL=C sort)" = \
  "$EXPECTED_PATHS"
git diff --cached --check
```

Expected: the literal staged path text equals the literal sorted allowlist and all six paths are additions/modifications in the index; no other path is staged.

- [ ] **Step 3: Run quiet staged secret, readonly-boundary, and persistence scans with exact `rg` status handling**

The high-confidence scan must never print a matched value. It prints only a path before failing. The broader scans print only classified path names. Every `rg` call treats status `0` as classify/fail, `1` as clean, and any status greater than `1` as command failure.

```bash
set -euo pipefail
ALLOWLIST=(
  src/polymarket_alpha_lab/team_evidence_aggregation_codec.py
  src/polymarket_alpha_lab/team_evidence_aggregation_temporal.py
  src/polymarket_alpha_lab/team_evidence_aggregation_types.py
  tests/test_team_evidence_aggregation_codec.py
  tests/test_team_evidence_aggregation_temporal.py
  tests/test_team_evidence_aggregation_types.py
)
SECRET_PATTERN='(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(^|[^[:alnum:]_])sk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|postgres(ql)?://[^[:space:]/]+:[^[:space:]@]+@)'
SENSITIVE_PATTERN='\b(api[_-]?key|secret|token|password|passwd|cookie|authorization|bearer|private[_ -]?key|seed phrase|mnemonic|wallet|account[_ -]?(id|address))\b'
BOUNDARY_PATTERN='\b(live trading|order submission|submit order|cancel order|replace order|sign order|wallet|private key|hosted account|account authentication|exchange mutation|order mutation)\b'
PERSISTENCE_PATTERN='\b(sqlite|duckdb|redis|mongodb|sqlalchemy|jsonl|file-backed|postgres|supabase|dsn|validate_local_postgres_dsn|persistence)\b'

SENSITIVE_PATHS=()
BOUNDARY_PATHS=()
PERSISTENCE_PATHS=()
for path in "${ALLOWLIST[@]}"; do
  ADDED="$(git diff --cached --unified=0 -- "$path" | sed -n '/^+++ /d; /^+/s/^+//p')"

  if rg -q "$SECRET_PATTERN" <<< "$ADDED"; then RG_STATUS=0; else RG_STATUS=$?; fi
  case "$RG_STATUS" in
    0) printf 'credential-shaped staged addition in %s\n' "$path" >&2; exit 1 ;;
    1) ;;
    *) printf 'secret scan command failed for %s with status %s\n' "$path" "$RG_STATUS" >&2; exit "$RG_STATUS" ;;
  esac

  if rg -qi "$SENSITIVE_PATTERN" <<< "$ADDED"; then RG_STATUS=0; else RG_STATUS=$?; fi
  case "$RG_STATUS" in
    0)
      case "$path" in
        tests/test_team_evidence_aggregation_types.py) SENSITIVE_PATHS+=("$path") ;;
        *) printf 'unclassified sensitive-field staged addition in %s\n' "$path" >&2; exit 1 ;;
      esac
      ;;
    1) ;;
    *) printf 'sensitive-field scan failed for %s with status %s\n' "$path" "$RG_STATUS" >&2; exit "$RG_STATUS" ;;
  esac

  if rg -qi "$BOUNDARY_PATTERN" <<< "$ADDED"; then RG_STATUS=0; else RG_STATUS=$?; fi
  case "$RG_STATUS" in
    0)
      case "$path" in
        tests/test_team_evidence_aggregation_types.py) BOUNDARY_PATHS+=("$path") ;;
        *) printf 'unclassified readonly-boundary addition in %s\n' "$path" >&2; exit 1 ;;
      esac
      ;;
    1) ;;
    *) printf 'readonly scan failed for %s with status %s\n' "$path" "$RG_STATUS" >&2; exit "$RG_STATUS" ;;
  esac

  if rg -qi "$PERSISTENCE_PATTERN" <<< "$ADDED"; then RG_STATUS=0; else RG_STATUS=$?; fi
  case "$RG_STATUS" in
    0)
      case "$path" in
        tests/test_team_evidence_aggregation_types.py|tests/test_team_evidence_aggregation_codec.py)
          PERSISTENCE_PATHS+=("$path")
          ;;
        *) printf 'unclassified persistence addition in %s\n' "$path" >&2; exit 1 ;;
      esac
      ;;
    1) ;;
    *) printf 'persistence scan failed for %s with status %s\n' "$path" "$RG_STATUS" >&2; exit "$RG_STATUS" ;;
  esac
done

printf 'sensitive-field paths for read-only negative-test classification:\n'
printf '  %s\n' "${SENSITIVE_PATHS[@]}"
printf 'readonly-boundary paths classified as AST negative tests:\n'
printf '  %s\n' "${BOUNDARY_PATHS[@]}"
printf 'persistence paths classified as AST/absence negative tests:\n'
printf '  %s\n' "${PERSISTENCE_PATHS[@]}"
```

Expected: no high-confidence credential-shaped addition; production paths are clean for broader boundary/persistence patterns; any printed test path is a negative AST/absence assertion and contains no operational surface. Empty arrays are acceptable and print no value. A status above `1` always stops the gate.

- [ ] **Step 4: Commit once, then prove exact committed-range equality and clean worktree**

```bash
set -euo pipefail
: "${NODE_BASE:?NODE_BASE is required}"
ALLOWLIST=(
  src/polymarket_alpha_lab/team_evidence_aggregation_codec.py
  src/polymarket_alpha_lab/team_evidence_aggregation_temporal.py
  src/polymarket_alpha_lab/team_evidence_aggregation_types.py
  tests/test_team_evidence_aggregation_codec.py
  tests/test_team_evidence_aggregation_temporal.py
  tests/test_team_evidence_aggregation_types.py
)
EXPECTED_PATHS="$(printf '%s\n' "${ALLOWLIST[@]}" | LC_ALL=C sort)"

git commit -m "Add team evidence aggregation core contracts"
test "$(git diff --name-only "$NODE_BASE..HEAD" | LC_ALL=C sort)" = "$EXPECTED_PATHS"
test "$(git diff --name-only --diff-filter=ACMR "$NODE_BASE..HEAD" | LC_ALL=C sort)" = \
  "$EXPECTED_PATHS"
test "$(git rev-list --count "$NODE_BASE..HEAD")" -ge 1
git merge-base --is-ancestor "$NODE_BASE" HEAD
git diff --check "$NODE_BASE..HEAD"
git diff --quiet
git diff --cached --quiet
test -z "$(git status --porcelain=v1)"
```

Expected: one focused implementation commit exists above `NODE_BASE`, the complete committed range equals all and only the six allowlisted paths, and worktree/index are clean.

- [ ] **Step 5: Repeat quiet scans over the complete committed range**

```bash
set -euo pipefail
: "${NODE_BASE:?NODE_BASE is required}"
ALLOWLIST=(
  src/polymarket_alpha_lab/team_evidence_aggregation_codec.py
  src/polymarket_alpha_lab/team_evidence_aggregation_temporal.py
  src/polymarket_alpha_lab/team_evidence_aggregation_types.py
  tests/test_team_evidence_aggregation_codec.py
  tests/test_team_evidence_aggregation_temporal.py
  tests/test_team_evidence_aggregation_types.py
)
EXPECTED_PATHS="$(printf '%s\n' "${ALLOWLIST[@]}" | LC_ALL=C sort)"
test "$(git diff --name-only "$NODE_BASE..HEAD" | LC_ALL=C sort)" = "$EXPECTED_PATHS"

SECRET_PATTERN='(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(^|[^[:alnum:]_])sk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|postgres(ql)?://[^[:space:]/]+:[^[:space:]@]+@)'
SENSITIVE_PATTERN='\b(api[_-]?key|secret|token|password|passwd|cookie|authorization|bearer|private[_ -]?key|seed phrase|mnemonic|wallet|account[_ -]?(id|address))\b'
BOUNDARY_PATTERN='\b(live trading|order submission|submit order|cancel order|replace order|sign order|wallet|private key|hosted account|account authentication|exchange mutation|order mutation)\b'
PERSISTENCE_PATTERN='\b(sqlite|duckdb|redis|mongodb|sqlalchemy|jsonl|file-backed|postgres|supabase|dsn|validate_local_postgres_dsn|persistence)\b'

SENSITIVE_PATHS=()
BOUNDARY_PATHS=()
PERSISTENCE_PATHS=()
for path in "${ALLOWLIST[@]}"; do
  ADDED="$(git diff --unified=0 "$NODE_BASE..HEAD" -- "$path" | sed -n '/^+++ /d; /^+/s/^+//p')"

  if rg -q "$SECRET_PATTERN" <<< "$ADDED"; then RG_STATUS=0; else RG_STATUS=$?; fi
  case "$RG_STATUS" in
    0) printf 'credential-shaped range addition in %s\n' "$path" >&2; exit 1 ;;
    1) ;;
    *) printf 'range secret scan failed for %s with status %s\n' "$path" "$RG_STATUS" >&2; exit "$RG_STATUS" ;;
  esac

  if rg -qi "$SENSITIVE_PATTERN" <<< "$ADDED"; then RG_STATUS=0; else RG_STATUS=$?; fi
  case "$RG_STATUS" in
    0)
      case "$path" in
        tests/test_team_evidence_aggregation_types.py) SENSITIVE_PATHS+=("$path") ;;
        *) printf 'unclassified sensitive-field range addition in %s\n' "$path" >&2; exit 1 ;;
      esac
      ;;
    1) ;;
    *) printf 'range sensitive-field scan failed for %s with status %s\n' "$path" "$RG_STATUS" >&2; exit "$RG_STATUS" ;;
  esac

  if rg -qi "$BOUNDARY_PATTERN" <<< "$ADDED"; then RG_STATUS=0; else RG_STATUS=$?; fi
  case "$RG_STATUS" in
    0)
      case "$path" in
        tests/test_team_evidence_aggregation_types.py) BOUNDARY_PATHS+=("$path") ;;
        *) printf 'unclassified readonly-boundary range addition in %s\n' "$path" >&2; exit 1 ;;
      esac
      ;;
    1) ;;
    *) printf 'readonly range scan failed for %s with status %s\n' "$path" "$RG_STATUS" >&2; exit "$RG_STATUS" ;;
  esac

  if rg -qi "$PERSISTENCE_PATTERN" <<< "$ADDED"; then RG_STATUS=0; else RG_STATUS=$?; fi
  case "$RG_STATUS" in
    0)
      case "$path" in
        tests/test_team_evidence_aggregation_types.py|tests/test_team_evidence_aggregation_codec.py)
          PERSISTENCE_PATHS+=("$path")
          ;;
        *) printf 'unclassified persistence range addition in %s\n' "$path" >&2; exit 1 ;;
      esac
      ;;
    1) ;;
    *) printf 'persistence range scan failed for %s with status %s\n' "$path" "$RG_STATUS" >&2; exit "$RG_STATUS" ;;
  esac
done

printf 'range sensitive-field paths for read-only negative-test classification:\n'
printf '  %s\n' "${SENSITIVE_PATHS[@]}"
printf 'range readonly-boundary paths classified as AST negative tests:\n'
printf '  %s\n' "${BOUNDARY_PATHS[@]}"
printf 'range persistence paths classified as AST/absence negative tests:\n'
printf '  %s\n' "${PERSISTENCE_PATHS[@]}"
```

Expected: literal range equality passes. Classification and `rg` exit semantics are `0` classify/fail, `1` clean, and `>1` command failure; high-confidence matches never print values.

- [ ] **Step 6: Obtain the mandatory exact full-range Claude Code verdict**

Run this exact local command from the repository root. It grants only read/search tools and explicitly forbids mutation. The prompt requires review of the complete `NODE_BASE..HEAD` range and the exact child allowlist.

```bash
set -euo pipefail
: "${NODE_BASE:?NODE_BASE is required}"
ALLOWLIST=(
  src/polymarket_alpha_lab/team_evidence_aggregation_codec.py
  src/polymarket_alpha_lab/team_evidence_aggregation_temporal.py
  src/polymarket_alpha_lab/team_evidence_aggregation_types.py
  tests/test_team_evidence_aggregation_codec.py
  tests/test_team_evidence_aggregation_temporal.py
  tests/test_team_evidence_aggregation_types.py
)
REVIEW_HEAD="$(git rev-parse HEAD)"
readonly REVIEW_HEAD
test -z "$(git status --porcelain=v1 --untracked-files=all)"
git diff --quiet
git diff --cached --quiet

REVIEW_PROMPT="$(cat <<EOF
Perform a read-only implementation review of Node 2A over the complete committed range ${NODE_BASE}..HEAD.
Do not edit, create, delete, stage, commit, or run mutation-capable tools. Use only Read, Glob, and Grep.
Read AGENTS.md, docs/superpowers/specs/2026-07-13-team-evidence-aggregation-core-design.md, docs/superpowers/plans/2026-07-13-team-evidence-aggregation-contracts-codec-temporal-eligibility.md, and docs/quality/phase-1-development-node-quality-gates.md.
Inspect exactly the six allowlisted implementation paths and the supplied full-range patch. Verify exact interfaces, all sixteen immutable types, raw Decimal/datetime rules, complete functional-dependency and closed-chain validation, both canonical selectors, exact envelopes and literal digest vectors, temporal formulas, child-local AST/import/export/forbidden-surface/size gates, canonical P(YES), and paper_only/report_only/readonly purity.
Treat any persistence, DSN, file/network/CLI/env/process/logging, auth/account/credential/private-key/wallet/signing/order/execution surface, BTC policy/default, package-root export, Node 3 identity/receipt/legacy projection, noncanonical float/context path, incomplete graph validation, selector fallback, codec extension, missing test, or out-of-allowlist change as blocking.
The pure child performs no persistence. Any future persistence is local Supabase/Postgres only and validates every raw DSN through validate_local_postgres_dsn before constructing a connection, psycopg wrapper, or adapter.
Return concise findings with file and line references. Your exact final nonblank line must be either VERDICT: PASS or VERDICT: REVISE.
EOF
)"

set +e
CLAUDE_OUTPUT="$({
  printf '%s\n\n' "$REVIEW_PROMPT"
  printf '%s\n' 'Exact allowlist:'
  printf '  %s\n' "${ALLOWLIST[@]}"
  printf '%s\n' 'Complete range path manifest:'
  git diff --name-only "$NODE_BASE..HEAD"
  printf '%s\n' 'Complete range diff:'
  git diff --no-ext-diff --find-renames=0 "$NODE_BASE..HEAD" -- "${ALLOWLIST[@]}"
} | claude -p \
  --model claude-opus-4-8 \
  --effort max \
  --safe-mode \
  --tools Read,Glob,Grep \
  --permission-mode dontAsk \
  --no-session-persistence 2>&1)"
CLAUDE_STATUS=$?
set -e

test "$CLAUDE_STATUS" -eq 0
test -n "$(printf '%s\n' "$CLAUDE_OUTPUT" | sed -n '/[^[:space:]]/p')"
FINAL_NONBLANK_LINE="$(printf '%s\n' "$CLAUDE_OUTPUT" | awk 'NF { line=$0 } END { print line }')"
test "$FINAL_NONBLANK_LINE" = 'VERDICT: PASS'
test "$(git rev-parse HEAD)" = "$REVIEW_HEAD"
test -z "$(git status --porcelain=v1 --untracked-files=all)"
git diff --quiet
git diff --cached --quiet
GATED_HEAD="$REVIEW_HEAD"
export GATED_HEAD
readonly GATED_HEAD
printf '%s\n' "$CLAUDE_OUTPUT"
```

Expected: Claude exits `0`, output is nonempty, and the exact final nonblank line is `VERDICT: PASS`. The passing review binds and exports readonly `GATED_HEAD`; Step 8 may publish only that exact SHA. Empty output, missing verdict, `VERDICT: REVISE`, any alternate final line, unavailable Claude Code, or command failure blocks the node; there is no fallback and fast mode must not be enabled.

- [ ] **Step 7: Handle every post-review fix with a new commit and a fresh full-range gate**

If review requires any fix, follow this exact sequence:

1. Add a failing regression test inside the six-path allowlist and run its focused RED command.
2. Make the minimal allowlisted implementation change and run focused GREEN.
3. Re-run Task 6 Steps 1-3 against the staged fix subset, while the complete staged set must equal exactly that explicitly named fix subset and remain inside `ALLOWLIST`.
4. Create a new commit; never amend or rewrite the reviewed commit.
5. Terminate the coordinator shell. Start a fresh shell with the original exported `NODE_BASE` and `NODE1_PREREQUISITE_SHA`, then re-run the full focused suite, compileall, full pytest, static Phase 1 tests, CodeGraph sync, diff hygiene, clean-worktree check, literal complete-range equality, and all complete-range scans over the entire original `NODE_BASE..HEAD`. This invalidates the old readonly `GATED_HEAD`.
6. Run a fresh Claude command from Step 6 over the entire updated `NODE_BASE..HEAD`; require a new exit `0`, nonempty output, exact final `VERDICT: PASS`, and a newly exported readonly `GATED_HEAD`.

No previous test, scan, or review receipt survives a post-review code change.

- [ ] **Step 8: Push without force, verify remote `main`, and publish the immutable Node 2A PASS receipt**

Before this step, the coordinator supplies an absolute destination outside the
repository. The destination must not exist, and its real non-symlink parent
directory must already exist outside `REPO_ROOT`:

```bash
export NODE2A_PASS_RECEIPT_PATH
```

This receipt is external review evidence, not project data persistence. It may
be created only after the reviewed Node 2A commit is verified on remote `main`.

```bash
set -euo pipefail
: "${NODE_BASE:?NODE_BASE is required}"
: "${GATED_HEAD:?GATED_HEAD is required after a passing full-range Claude review}"
: "${NODE2A_PASS_RECEIPT_PATH:?NODE2A_PASS_RECEIPT_PATH is required before push}"
test -z "${NODE2A_PREREQ_SHA+x}"
test -z "${NODE2A_PASS_RECEIPT_SHA256+x}"
readonly NODE2A_PASS_RECEIPT_PATH
test -z "$(git status --porcelain=v1 --untracked-files=all)"
git diff --quiet
git diff --cached --quiet
LOCAL_HEAD="$(git rev-parse HEAD)"
export LOCAL_HEAD
readonly LOCAL_HEAD
test "$LOCAL_HEAD" = "$GATED_HEAD"

COMMON_GIT_DIR="$(git rev-parse --git-common-dir)"
GIT_AUTH=(-c credential.helper= -c "credential.helper=store --file=$COMMON_GIT_DIR/github-credentials")
REMOTE_ENDPOINT="$(git remote get-url --push --all origin)"
test -n "$REMOTE_ENDPOINT"
test "${REMOTE_ENDPOINT//$'\n'/}" = "$REMOTE_ENDPOINT"
readonly REMOTE_ENDPOINT

observe_remote_main() {
  local attempt first_line first_sha first_ref fetched_sha second_line second_sha second_ref
  for attempt in 1 2 3; do
    if ! first_line="$(GIT_TERMINAL_PROMPT=0 git "${GIT_AUTH[@]}" ls-remote --exit-code "$REMOTE_ENDPOINT" refs/heads/main)"; then
      continue
    fi
    if [ -z "$first_line" ] || [ "${first_line//$'\n'/}" != "$first_line" ] || [[ "$first_line" != *$'\t'* ]]; then
      continue
    fi
    first_sha="${first_line%%$'\t'*}"
    first_ref="${first_line#*$'\t'}"
    if [[ ! "$first_sha" =~ ^[0-9a-f]{40}$ ]] || [ "$first_ref" != refs/heads/main ]; then
      continue
    fi

    if ! GIT_TERMINAL_PROMPT=0 git "${GIT_AUTH[@]}" fetch --no-tags --no-recurse-submodules "$REMOTE_ENDPOINT" refs/heads/main >/dev/null; then
      continue
    fi
    if ! fetched_sha="$(git rev-parse 'FETCH_HEAD^{commit}')"; then
      continue
    fi
    if ! second_line="$(GIT_TERMINAL_PROMPT=0 git "${GIT_AUTH[@]}" ls-remote --exit-code "$REMOTE_ENDPOINT" refs/heads/main)"; then
      continue
    fi
    if [ -z "$second_line" ] || [ "${second_line//$'\n'/}" != "$second_line" ] || [[ "$second_line" != *$'\t'* ]]; then
      continue
    fi
    second_sha="${second_line%%$'\t'*}"
    second_ref="${second_line#*$'\t'}"
    if [[ ! "$second_sha" =~ ^[0-9a-f]{40}$ ]] || [ "$second_ref" != refs/heads/main ]; then
      continue
    fi
    if [ "$first_sha" = "$fetched_sha" ] && [ "$fetched_sha" = "$second_sha" ]; then
      printf '%s\n' "$second_sha"
      return 0
    fi
  done
  return 74
}
export -f observe_remote_main
readonly -f observe_remote_main

if ! CURRENT_REMOTE="$(observe_remote_main)"; then
  printf 'could not obtain a stable pre-push remote observation; no push was performed.\n' >&2
  exit 74
fi
if [ "$CURRENT_REMOTE" != "$NODE_BASE" ]; then
  printf 'stable remote main moved before push; no rebase or push was performed. Start a fresh reviewed gate from FETCH_HEAD.\n' >&2
  exit 75
fi

set +e
GIT_TERMINAL_PROMPT=0 git "${GIT_AUTH[@]}" push \
  --no-force --no-force-with-lease --no-force-if-includes --no-follow-tags \
  --no-recurse-submodules \
  "$REMOTE_ENDPOINT" "$LOCAL_HEAD:refs/heads/main"
PUSH_STATUS=$?
set -e

if ! REMOTE_AFTER_PUSH="$(observe_remote_main)"; then
  printf 'post-push remote observation is indeterminate; do not push again.\n' >&2
  exit 74
elif [ "$REMOTE_AFTER_PUSH" = "$LOCAL_HEAD" ]; then
  printf 'stable remote main equals the accepted reviewed HEAD; publication is confirmed, including a possible lost client response.\n'
elif git merge-base --is-ancestor "$LOCAL_HEAD" "$REMOTE_AFTER_PUSH"; then
  printf 'remote main contains but no longer equals the accepted reviewed HEAD; receipt publication is blocked.\n' >&2
  exit 75
elif [ "$REMOTE_AFTER_PUSH" = "$NODE_BASE" ] && [ "$PUSH_STATUS" -ne 0 ]; then
  printf 'non-force push failed with status %s and remote main remains NODE_BASE.\n' "$PUSH_STATUS" >&2
  exit "$PUSH_STATUS"
elif [ "$REMOTE_AFTER_PUSH" = "$NODE_BASE" ]; then
  printf 'push returned success but stable remote main remains NODE_BASE; do not retry automatically.\n' >&2
  exit 1
else
  printf 'stable remote main neither equals nor contains the accepted reviewed HEAD; a fresh base and full review are required.\n' >&2
  exit 1
fi

readonly CURRENT_REMOTE PUSH_STATUS REMOTE_AFTER_PUSH
test "$REMOTE_AFTER_PUSH" = "$LOCAL_HEAD"
test "$(git rev-parse HEAD)" = "$LOCAL_HEAD"
git merge-base --is-ancestor 46fbd5371782bfc0e654e547d67611ae416c7468 "$NODE_BASE"

NODE2A_PREREQ_SHA="$LOCAL_HEAD"
REPO_ROOT="$(git rev-parse --show-toplevel)"
readonly NODE2A_PREREQ_SHA REPO_ROOT
export REPO_ROOT NODE2A_PREREQ_SHA NODE2A_PASS_RECEIPT_PATH

NODE2A_PASS_RECEIPT_SHA256="$(
  .venv/bin/python - <<'PY'
import hashlib
import json
import os
import re
import stat


def resolved_descriptor_path(descriptor: int, *, label: str) -> str:
    descriptor_link = os.readlink(f"/proc/self/fd/{descriptor}")
    if descriptor_link.endswith(" (deleted)"):
        raise SystemExit(f"Node 2A PASS receipt {label} descriptor is deleted")
    return os.path.realpath(descriptor_link)


def require_outside_repo(path: str, *, repo_root: str, label: str) -> None:
    try:
        inside_repo = os.path.commonpath((repo_root, path)) == repo_root
    except ValueError:
        raise SystemExit(
            f"Node 2A PASS receipt {label} path is invalid",
        ) from None
    if inside_repo:
        raise SystemExit(
            f"Node 2A PASS receipt {label} must resolve outside REPO_ROOT",
        )


def open_directory_without_symlinks(path: str) -> int:
    if not os.path.isabs(path):
        raise SystemExit("Node 2A PASS receipt parent must be absolute")
    components = path.split("/")[1:]
    if not components or any(component in {"", ".", ".."} for component in components):
        raise SystemExit("Node 2A PASS receipt parent path is not canonical")

    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in components:
            next_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            try:
                if not stat.S_ISDIR(os.fstat(next_descriptor).st_mode):
                    raise SystemExit(
                        "Node 2A PASS receipt parent component must be a directory",
                    )
            except BaseException:
                os.close(next_descriptor)
                raise
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_exact(descriptor: int, expected_size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = expected_size
    while remaining:
        chunk = os.read(descriptor, remaining)
        if not chunk:
            raise SystemExit("Node 2A PASS receipt readback ended early")
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(descriptor, 1) != b"":
        raise SystemExit("Node 2A PASS receipt readback has trailing bytes")
    return b"".join(chunks)


def create_receipt() -> str:
    receipt_path = os.environ["NODE2A_PASS_RECEIPT_PATH"]
    node2a_head = os.environ["NODE2A_PREREQ_SHA"]
    repo_root = os.path.realpath(os.environ["REPO_ROOT"])

    if re.fullmatch(r"[0-9a-f]{40}", node2a_head) is None:
        raise SystemExit(
            "NODE2A_PREREQ_SHA must be a lowercase 40-character commit SHA",
        )
    if not os.path.isabs(receipt_path):
        raise SystemExit("NODE2A_PASS_RECEIPT_PATH must be absolute")
    if not os.path.isabs(repo_root) or not os.path.isdir(repo_root):
        raise SystemExit("REPO_ROOT must resolve to a directory")

    parent_path = os.path.dirname(receipt_path)
    receipt_name = os.path.basename(receipt_path)
    if not receipt_name or receipt_name in {".", ".."}:
        raise SystemExit("NODE2A_PASS_RECEIPT_PATH must name a file")

    payload = {
        "node": "team-evidence-aggregation-contracts-codec-temporal-eligibility",
        "head_sha": node2a_head,
        "review_model": "claude-opus-4-8",
        "review_effort": "max",
        "review_exit_status": 0,
        "review_final_nonblank_line": "VERDICT: PASS",
        "focused_tests": "pass",
        "full_pytest": "pass",
        "compileall": "pass",
        "codegraph_sync": "pass",
        "static_gates": "pass",
        "clean_worktree": "pass",
        "pushed_remote_main_sha": node2a_head,
    }
    receipt_bytes = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    receipt_sha256 = hashlib.sha256(receipt_bytes).hexdigest()

    parent_descriptor: int | None = None
    receipt_descriptor: int | None = None
    verification_descriptor: int | None = None
    created_stat: os.stat_result | None = None
    try:
        parent_descriptor = open_directory_without_symlinks(parent_path)
        parent_stat = os.fstat(parent_descriptor)
        if not stat.S_ISDIR(parent_stat.st_mode):
            raise SystemExit("Node 2A PASS receipt parent must be a directory")
        if parent_stat.st_uid != os.geteuid():
            raise SystemExit("Node 2A PASS receipt parent must be owned by current user")
        if parent_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise SystemExit(
                "Node 2A PASS receipt parent must not be writable by group or others",
            )
        require_outside_repo(
            resolved_descriptor_path(parent_descriptor, label="parent"),
            repo_root=repo_root,
            label="parent",
        )

        receipt_descriptor = os.open(
            receipt_name,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | os.O_NONBLOCK,
            0o600,
            dir_fd=parent_descriptor,
        )
        created_stat = os.fstat(receipt_descriptor)
        if not stat.S_ISREG(created_stat.st_mode):
            raise SystemExit("Node 2A PASS receipt must be a regular file")
        require_outside_repo(
            resolved_descriptor_path(receipt_descriptor, label="file"),
            repo_root=repo_root,
            label="file",
        )
        os.fchmod(receipt_descriptor, 0o600)

        remaining = memoryview(receipt_bytes)
        while remaining:
            written = os.write(receipt_descriptor, remaining)
            if written <= 0:
                raise SystemExit("Node 2A PASS receipt write did not progress")
            remaining = remaining[written:]

        final_stat = os.fstat(receipt_descriptor)
        if not stat.S_ISREG(final_stat.st_mode):
            raise SystemExit("Node 2A PASS receipt changed file type")
        if final_stat.st_size != len(receipt_bytes):
            raise SystemExit("Node 2A PASS receipt byte count mismatch")
        if stat.S_IMODE(final_stat.st_mode) != 0o600:
            raise SystemExit("Node 2A PASS receipt mode must be 0600")
        os.fsync(receipt_descriptor)

        os.lseek(receipt_descriptor, 0, os.SEEK_SET)
        descriptor_bytes = read_exact(receipt_descriptor, len(receipt_bytes))
        if descriptor_bytes != receipt_bytes:
            raise SystemExit("Node 2A PASS receipt descriptor readback mismatch")
        if hashlib.sha256(descriptor_bytes).hexdigest() != receipt_sha256:
            raise SystemExit("Node 2A PASS receipt descriptor SHA-256 mismatch")

        named_stat = os.stat(
            receipt_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(named_stat.st_mode)
            or (named_stat.st_dev, named_stat.st_ino)
            != (created_stat.st_dev, created_stat.st_ino)
        ):
            raise SystemExit("Node 2A PASS receipt name changed during creation")

        os.fsync(parent_descriptor)

        verification_descriptor = os.open(
            receipt_name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent_descriptor,
        )
        verification_stat = os.fstat(verification_descriptor)
        if (
            not stat.S_ISREG(verification_stat.st_mode)
            or (verification_stat.st_dev, verification_stat.st_ino)
            != (created_stat.st_dev, created_stat.st_ino)
            or verification_stat.st_size != len(receipt_bytes)
            or stat.S_IMODE(verification_stat.st_mode) != 0o600
        ):
            raise SystemExit("Node 2A PASS receipt reopen verification failed")
        verification_bytes = read_exact(verification_descriptor, len(receipt_bytes))
        if verification_bytes != receipt_bytes:
            raise SystemExit("Node 2A PASS receipt pathname readback mismatch")
        if hashlib.sha256(verification_bytes).hexdigest() != receipt_sha256:
            raise SystemExit("Node 2A PASS receipt pathname SHA-256 mismatch")

        os.fsync(parent_descriptor)
        final_named_stat = os.stat(
            receipt_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(final_named_stat.st_mode)
            or (final_named_stat.st_dev, final_named_stat.st_ino)
            != (created_stat.st_dev, created_stat.st_ino)
            or final_named_stat.st_size != len(receipt_bytes)
            or stat.S_IMODE(final_named_stat.st_mode) != 0o600
        ):
            raise SystemExit("Node 2A PASS receipt final pathname verification failed")

        os.close(verification_descriptor)
        verification_descriptor = None
        os.close(receipt_descriptor)
        receipt_descriptor = None
    finally:
        if verification_descriptor is not None:
            os.close(verification_descriptor)
        if receipt_descriptor is not None:
            os.close(receipt_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)

    return receipt_sha256


try:
    print(create_receipt())
except OSError:
    raise SystemExit("Node 2A PASS receipt exclusive creation failed") from None
PY
)"

[[ "$NODE2A_PASS_RECEIPT_SHA256" =~ ^[0-9a-f]{64}$ ]]
export NODE2A_PREREQ_SHA NODE2A_PASS_RECEIPT_PATH NODE2A_PASS_RECEIPT_SHA256
readonly NODE2A_PASS_RECEIPT_SHA256
printf 'NODE2A_PREREQ_SHA=%s\n' "$NODE2A_PREREQ_SHA"
printf 'NODE2A_PASS_RECEIPT_PATH=%s\n' "$NODE2A_PASS_RECEIPT_PATH"
printf 'NODE2A_PASS_RECEIPT_SHA256=%s\n' "$NODE2A_PASS_RECEIPT_SHA256"
```

Expected: three matching direct-remote observations (`ls-remote`, `FETCH_HEAD`,
then `ls-remote`) establish a stable pre-push `main` equal to immutable
`NODE_BASE`. The sole ordinary non-force push names the immutable accepted
`LOCAL_HEAD` SHA explicitly, never symbolic `HEAD`. A stable post-attempt
observation treats remote `main == LOCAL_HEAD` as success even if the client
lost the push response. Any unavailable/unstable observation, remote movement,
or remote descendant blocks receipt publication without rebasing, altering
local `HEAD`, or retrying the push automatically; start a fresh full-range gate
before any later push attempt. If remote remains `NODE_BASE` and push failed,
the push status is propagated; if push reported success but remote remains
`NODE_BASE`, the script fails closed. Only the successful remote-equals-accepted-
reviewed-HEAD path creates the canonical 13-field receipt with exclusive
non-symlink mode `0600`, a private external parent, descriptor and reopened-
pathname byte/SHA/inode/mode verification, and fsync of the file and parent
directory, then emits the three exact Node 2B handoff values. A collision or
receipt-creation failure blocks Node 2B. No dry-run or force push is permitted.
Automatic pathname cleanup is forbidden: a failure after exclusive creation may
leave only that newly created `0600` artifact in place. The operator must not
overwrite or unlink it through this plan; use a new externally managed
destination for any receipt-only retry after re-verifying the unchanged
reviewed/pushed HEAD.

## Completion Record

Record these exact outcomes in the implementation handoff:

```text
Node 2A prerequisite receipt: exact immutable Node 1 SHA/parent/tree/subject validated
NODE_BASE: record the exact value printed by `printf '%s\n' "$NODE_BASE"` after the final pre-push base check
focused Node 2A tests: pass
child-local AST/import/export/forbidden-surface/line-size gate: pass
compileall: pass
full pytest: pass
Phase 1 static tests: pass
CodeGraph sync: pass
git diff --check: pass
literal staged allowlist equality: pass
literal committed-range allowlist equality: pass
readonly/local-Supabase/secret scans: pass with negative-test paths classified and no matched secret values printed
clean worktree: pass
Claude Code full-range review: exit 0, nonempty output, final VERDICT: PASS
push: non-force; pre-push origin/main == NODE_BASE; post-push remote main == HEAD
NODE2A_PREREQ_SHA: exact reviewed and pushed Node 2A HEAD
NODE2A_PASS_RECEIPT_PATH: exact external immutable receipt path for Node 2B
NODE2A_PASS_RECEIPT_SHA256: lowercase SHA-256 of exact canonical receipt bytes
receipt failure handling: descriptors closed; no automatic unlink; retry uses a new external path
```

Node 2A is a valid dependency for Node 2B only after every line above passes,
the reviewed commit is present on remote `main`, and all three outgoing handoff
values are published to the Node 2B coordinator.
