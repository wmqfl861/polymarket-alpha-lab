# Team Evidence Aggregation Core Design

**Date:** 2026-07-13
**Status:** Approved design; Node 2A/2B/2C plans reviewed; implementation pending
**Repository base:** `46fbd5371782bfc0e654e547d67611ae416c7468`
**Parent roadmap:** `docs/superpowers/plans/2026-07-13-btc-domain-evidence-aggregation-foundation.md`

## Purpose

Node 2 is the pure, deterministic core that reduces caller-supplied specialist
team evidence into one diagnostic aggregation result. It establishes the
generic arithmetic, temporal, revision, witness, contradiction, and readiness
contracts needed by later BTC-specific and persistence nodes.

The former monolithic Node 2 is split into three independently reviewed child
nodes:

1. **Node 2A:** immutable types, strict canonical codecs, temporal rules, and
   exact input-contract validation;
2. **Node 2B:** exact weight allocation and global requirement witnesses;
3. **Node 2C:** end-to-end aggregation, contradiction/status derivation, and
   one scope/AST guard over every Node 2 module.

The result always expresses canonical `P(YES)`. This follows the Node 1
contract: no selected side can reorient a probability, and `P(NO)` is never an
input or output alias for `P(YES)`.

## Phase 1 Boundary

All six Node 2 production modules are:

- `paper_only=True`;
- `report_only=True`;
- `readonly=True`;
- pure over explicit caller-supplied immutable values;
- deterministic for the same semantic input and configuration;
- module-local, with no package-root export.

The six production modules must not contain or call:

- database, Supabase, PostgreSQL, SQL, migration, or persistence code;
- filesystem reads or writes, including JSONL, CSV, cache, or temporary-file
  persistence;
- network, HTTP, socket, browser, scraping, public-client, or external API
  access code;
- CLI parsing, environment-variable access, process execution, or logging;
- authentication, account, credential, token, wallet, key, signing, order,
  sizing, allocation-to-capital, execution, or exchange-mutation paths;
- ambient clock reads, randomness, or Python's process-randomized `hash()`;
- BTC source names, BTC thresholds, or a built-in `0.020000` to `0.980000`
  publication policy.

Node 2 performs no project-data persistence. Any later node that persists data
derived from Node 2 may use only this host's local Supabase/Postgres, with no
file, cache, journal, alternate database, hosted service, or generic-store
fallback. Every raw DSN from environment, config, CLI plumbing, fixtures, or
helper construction must pass through `validate_local_postgres_dsn` before it
is used to open a connection, construct a psycopg wrapper, or reach any
persistence adapter, repository, or store. Node 2 accepts no DSN and does not
import that validator or construct any persistence surface. In-memory tuples
and fresh JSON-ready dictionaries returned by codec functions are transient
values, not stores.

## Ownership Boundaries

### Node 2 owns

- exact immutable generic input, configuration, intermediate, and result
  types;
- structural validation of relationships among caller-supplied identity
  layers, without validating source authority;
- fixed-six `Decimal` normalization and arithmetic;
- explicit current-revision selection, with no inferred latest revision;
- freshness, availability, and capture-lag classification;
- deterministic recapture deduplication;
- two-stage independence-group and correlation-group weight caps;
- global requirement-witness assignment;
- diagnostic and arithmetic universes;
- arithmetic `P(YES)`, contradiction, readiness status, reason codes, and
  ready-only publication;
- canonical config, input, and result fragments, the config digest, and the
  core-result digest.

### Node 2 does not own

- source retrieval, raw source bytes, source trust promotion, or source
  registry policy;
- BTC requirement definitions, thresholds, weights, caps, time windows,
  contradiction bands, or probability publication bounds;
- team, condition, market, event, or forecast run identity;
- complete evaluation-scope provenance or replay identity;
- `tea:v1`, `tfr:v1`, or `tfe:v1` identifier construction;
- `TeamForecastPacket` or `TeamForecastEvidencePacket` construction;
- legacy packet payloads, legacy hashes, or legacy projections;
- database rows, schema, stores, transactions, configuration from env, CLI,
  services, or strategy-cycle consumption.

Node 3 alone binds the complete evaluation scope, including a canonical receipt
list with exactly one receipt for every accepted or rejected evaluator input,
domain context, configuration payload, Node 2 core digest, and run metadata.
The receipt list and its accepted/rejected classification are Node 3-owned;
rejected receipts never enter Node 2 `records`, current selections, diagnostics,
allocation, witnesses, or arithmetic. Node 3 also owns the `tea:v1`, `tfr:v1`,
and `tfe:v1` IDs and every exact legacy projection. A Node 2 `core_digest` is
therefore a digest of the pure reduction result, not a run ID, forecast ID,
evidence ID, or complete replay identity.

At handoff, Node 3 must call
`validate_team_evidence_aggregation_result` with the exact `result`,
`aggregation_input`, and `config` being bound. It must also require
`result.config_digest == team_evidence_aggregation_config_digest(config)` and
include the outputs of `team_evidence_aggregation_config_payload(config)`,
`team_evidence_aggregation_input_payload(aggregation_input)`, and the validated
`team_evidence_aggregation_payload(result)` as separate fragments in its outer
evaluation-scope preimage. Node 3 must never use `core_digest` as a
deduplication key, a run/forecast/evidence ID, or a substitute for the complete
replay scope. Node 2 adds no V1 IDs or input digest for this handoff.

## Child Nodes And Exact Paths

### Node 2A: Types, codec, and temporal contract

Create only:

```text
src/polymarket_alpha_lab/team_evidence_aggregation_types.py
src/polymarket_alpha_lab/team_evidence_aggregation_codec.py
src/polymarket_alpha_lab/team_evidence_aggregation_temporal.py
tests/test_team_evidence_aggregation_types.py
tests/test_team_evidence_aggregation_codec.py
tests/test_team_evidence_aggregation_temporal.py
```

Node 2A starts only after the completed Node 1 canonical `P(YES)` contract is
reviewed, committed, and pushed. It may use only the closed standard-library
and direct sibling-module import surface specified under Security And Purity
Controls; it must not import existing team forecast packet, DB-row, store, CLI,
or package-root modules.
`team_evidence_aggregation_types.py` owns the public pure aggregation
input-contract validator, canonical-current-record selector, and
canonical-capture-record selector specified below; their graph, validation,
and capture-selection helpers remain private.

### Node 2B: Allocation and witnesses

Create only:

```text
src/polymarket_alpha_lab/team_evidence_aggregation_allocation.py
src/polymarket_alpha_lab/team_evidence_aggregation_witness.py
tests/test_team_evidence_aggregation_allocation.py
tests/test_team_evidence_aggregation_witness.py
```

Node 2B starts only after Node 2A is reviewed, committed, and pushed and its
contracts are fixed.
Allocation depends only on Node 2A types. Witness matching depends on Node 2A
types and Node 2B allocation results. Neither module may import the Node 2C
orchestrator.

### Node 2C: Reducer and whole-node scope guard

Create only:

```text
src/polymarket_alpha_lab/team_evidence_aggregation.py
tests/test_team_evidence_aggregation.py
tests/test_team_evidence_aggregation_scope.py
```

Node 2C starts only after Nodes 2A and 2B are each reviewed, committed, and
pushed. It composes their public module-local APIs and calls Node 2A's public
canonical-record selectors, which each perform the complete input-contract
validation before returning canonical records. The scope test parses all six
Node 2 source modules, not only the orchestrator.

No child modifies `src/polymarket_alpha_lab/__init__.py`. Imports in tests and
later nodes use the defining modules directly. If a later child discovers that
an earlier public contract must change, the earlier child is amended and
re-reviewed explicitly; Node 2C does not hide contract changes in its own diff.
Each production module defines `__all__` equal to its complete documented
public surface. Empty, partial, extra, reordered, or duplicate exports fail the
scope test; validation, graph, apportionment, and serialization helpers remain
private. The exact tuples are:

```text
team_evidence_aggregation_types:
  TeamEvidenceSourceLineage, TeamEvidenceCapture, TeamEvidenceRevision,
  TeamEvidenceAssessmentRevision, TeamEvidenceAggregationRecord,
  TeamEvidenceCurrentRevisionSelection, TeamEvidenceAggregationInput,
  TeamEvidenceRequirement, TeamEvidenceAggregationConfig,
  TeamEvidenceTemporalAssessment, TeamEvidenceWeightAllocation,
  TeamEvidenceRequirementWitness, TeamEvidenceRequirementCoverage,
  TeamEvidenceDiagnosticRow, TeamEvidenceContradictionResult,
  TeamEvidenceAggregationResult,
  validate_team_evidence_aggregation_input_contract,
  select_team_evidence_canonical_current_records,
  select_team_evidence_canonical_capture_records
team_evidence_aggregation_codec:
  team_evidence_aggregation_config_payload,
  team_evidence_aggregation_input_payload,
  team_evidence_aggregation_config_digest,
  team_evidence_aggregation_core_payload,
  team_evidence_aggregation_payload,
  team_evidence_aggregation_core_digest,
  validate_team_evidence_aggregation_core_digest
team_evidence_aggregation_temporal:
  assess_team_evidence_temporal
team_evidence_aggregation_allocation:
  allocate_team_evidence_weights
team_evidence_aggregation_witness:
  build_team_evidence_requirement_coverage
team_evidence_aggregation:
  build_team_evidence_aggregation_result,
  validate_team_evidence_aggregation_result
```

### Child review-receipt handoffs

The child dependency receipts are operational review evidence, not evaluator
receipts and not project-data persistence. They are created and consumed only
by the implementation gate scripts outside the six production modules. Each
receipt is an immutable canonical JSON file stored outside the repository; its
path travels out of band and is never included in the receipt payload. Creation
is exclusive and no-follow at mode `0600` under a pre-existing real
outside-repository parent, followed by file and parent-directory `fsync`; a
collision is never overwritten, truncated, appended to, or automatically
unlinked.

Node 2A consumes the required `NODE1_PREREQUISITE_SHA`, which must equal the
immutable Node 1 commit `46fbd5371782bfc0e654e547d67611ae416c7468`
pinned in Node 2A's `Fixed Predecessor Interface` section. After Node 2A's reviewed commit is
verified on remote `main`, its gate publishes exactly these three Node 2B
handoff values:

```text
NODE2A_PREREQ_SHA
NODE2A_PASS_RECEIPT_PATH
NODE2A_PASS_RECEIPT_SHA256
```

The Node 2A receipt has exactly thirteen keys: `node`, `head_sha`,
`review_model`, `review_effort`, `review_exit_status`,
`review_final_nonblank_line`, `focused_tests`, `full_pytest`, `compileall`,
`codegraph_sync`, `static_gates`, `clean_worktree`, and
`pushed_remote_main_sha`. Its node value is
`team-evidence-aggregation-contracts-codec-temporal-eligibility`;
`head_sha == pushed_remote_main_sha == NODE2A_PREREQ_SHA`; the review fields
bind `claude-opus-4-8`, effort `max`, exit status `0`, and final line
`VERDICT: PASS`; and every named gate value is `pass`.

The thirteen-key Node 2A shape is an intentionally frozen handoff interface.
Its review command and acceptance gate still enforce read-only operation and
fast mode off, but those two markers were not fields in the approved Node 2A
receipt schema. Successors validate the explicit `node2a-13` variant rather
than assuming all child receipts share one shape; changing it retroactively
would invalidate the pinned Node 2A producer/consumer contract.

After the same reviewed, pushed, and remote-equality gates, Node 2B publishes
exactly:

```text
NODE2B_PREREQ_SHA
NODE2B_PASS_RECEIPT_PATH
NODE2B_PASS_RECEIPT_SHA256
```

The Node 2B receipt uses the same thirteen-key contract, changes `node` to
`team-evidence-aggregation-independence-correlation-requirement-witness`, binds
both SHA fields to `NODE2B_PREREQ_SHA`, and adds exactly the JSON booleans
`read_only: true` and `fast_mode: false`, for fifteen keys total. Both receipt
byte strings use exactly
`json.dumps(payload, ensure_ascii=True, separators=(",", ":"),
sort_keys=True).encode("utf-8")`; the corresponding handoff SHA-256 is over
those exact bytes.

The `node2b-15` variant deliberately strengthens later operational evidence by
encoding the two review-mode markers. This is a versioned schema distinction,
not permission for Node 2A to run with different review settings and not a
generic optional-field receipt format.

Node 2C consumes all six Node 2A/2B handoff values. It validates each receipt
through one no-follow descriptor read, regular-file and outside-repository
checks, SHA-256 equality, duplicate-key rejection, and exact closed
key/type/value equality before accepting either predecessor. It emits only the
canonical non-secret `INITIAL_NODE2A_RECEIPT_EVIDENCE` and
`INITIAL_NODE2B_RECEIPT_EVIDENCE` gate values. Receipt creation or validation
does not authorize a project-data file store and does not weaken the local
Supabase/Postgres-only persistence rule.

Node 2C is the terminal child inside Node 2 and therefore does not publish a
third child-to-child receipt variant. Node 3 is planned only after the final
Node 2C accepted SHA is known; its fixed predecessor interface must pin that
literal SHA and verify commit existence, ancestry, accepted review evidence,
and exact remote publication before consuming Node 2, just as Node 2A pins the
already-known Node 1 SHA. This deliberate terminal handoff must not be replaced
with remote-tip trust or a generic receipt parser.

## Core Model

### Public type conventions

Every public typed Node 2 domain value is an exact, non-subclassable
`@dataclass(frozen=True, slots=True)`. Every public dataclass carries and
validates the hard flags `paper_only=True`, `report_only=True`, and
`readonly=True`. Exact class checks reject subclasses, mocks, duck types, and
constructor-bypassed objects at public boundaries. Every public function
revalidates the exact-true hard flags on the outer and nested domain values it
consumes rather than trusting prior construction, and every emitted public
dataclass preserves all three exact flags. No public boundary infers, weakens,
or accepts caller overrides for them. Codec functions are not domain-value
constructors: they return fresh, caller-owned, transient JSON-ready
dictionaries with the exact closed shapes specified below.

Collection fields are tuples. Builders accept exact tuples, not lists,
generators, sets, mappings, strings, or bytes. Constructors may sort semantic
sets into canonical tuples, but they reject duplicates rather than silently
discarding them. Returned tuples use the deterministic sort keys specified in
this document.

Discrete graph cardinalities and resource ceilings use exact base `int`, with
`bool` rejected and values checked before use. All real-valued domain numbers,
including probabilities, weights, ratios, durations, ages, and lags, use exact
base `Decimal`. No public or private Node 2 arithmetic path accepts or creates a
`float`.

### Identity and input types

`team_evidence_aggregation_types.py` defines these exact public names and
fields. Optional predecessor fields are required constructor arguments; a root
revision passes explicit `None`. Policy fields have no defaults. Only the three
hard safety flags default to `True`.

#### `TeamEvidenceSourceLineage`

```text
source_lineage_id: str
source_lineage_digest: str
paper_only: bool
report_only: bool
readonly: bool
```

This is an opaque reference to the upstream source-lineage manifest. Node 2
does not decide that a source is official, independent, primary, or trusted.
Within one aggregation call, one `source_lineage_id` denotes exactly one
semantic evidence series. It is not a source-record catalog, an origin-capture
identifier, or an authority claim; Node 2 enforces the series structure
described below but does not validate the caller's source semantics.

#### `TeamEvidenceCapture`

```text
capture_id: str
capture_digest: str
source_lineage_id: str
source_lineage_digest: str
content_digest: str
captured_at: datetime
paper_only: bool
report_only: bool
readonly: bool
```

A capture is one acquisition event. `captured_at` records availability; it is
never a freshness anchor. `content_digest` identifies the captured content
without placing raw content in the core.

#### `TeamEvidenceRevision`

```text
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
```

An evidence revision owns content identity, requirement applicability, and the
trusted effective/freshness-anchor time. `recorded_at` is the caller-supplied
availability/audit time for the evidence revision; it is distinct from both
`freshness_anchor_at` and every capture time. These fields cannot vary among
captures of the same evidence revision.

#### `TeamEvidenceAssessmentRevision`

```text
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
```

An assessment revision owns canonical `P(YES)`, requested weight, rationale,
and cap-group classification. `assessed_at` is its caller-supplied
availability/audit time, separate from evidence freshness and capture time. It
never contains selected-side probability. Raw rationale is outside Node 2;
only its caller-supplied digest is admitted.

#### `TeamEvidenceAggregationRecord`

```text
source_lineage: TeamEvidenceSourceLineage
capture: TeamEvidenceCapture
evidence_revision: TeamEvidenceRevision
assessment_revision: TeamEvidenceAssessmentRevision
paper_only: bool
report_only: bool
readonly: bool
```

The nested layers are intentional. The record constructor checks local
cross-layer references immediately; the Node 2A input-contract validator
performs global functional-dependency and closed-chain validation across all
records.

#### `TeamEvidenceCurrentRevisionSelection`

```text
evidence_revision_id: str
evidence_revision_digest: str
assessment_revision_id: str
assessment_revision_digest: str
paper_only: bool
report_only: bool
readonly: bool
```

This type is the only way a revision becomes current for one reduction. It does
not select a capture. The builder chooses the canonical capture for the exact
selected evidence/assessment pair.

#### `TeamEvidenceAggregationInput`

```text
evaluated_at: datetime
records: tuple[TeamEvidenceAggregationRecord, ...]
current_revisions: tuple[TeamEvidenceCurrentRevisionSelection, ...]
paper_only: bool
report_only: bool
readonly: bool
```

`evaluated_at` is supplied explicitly and normalized to UTC. Empty records and
empty selections are valid and produce a blocked diagnostic result. A nonempty
selection that does not resolve to a supplied exact revision pair is invalid.

### Configuration types

#### `TeamEvidenceRequirement`

```text
requirement_id: str
minimum_witness_count: int
minimum_effective_weight: Decimal
unmet_status: str  # exactly "watch" or "blocked"
paper_only: bool
report_only: bool
readonly: bool
```

`minimum_witness_count` is at least one. `minimum_effective_weight` is in
`[0, 1]`. A requirement's policy for an unmet witness count is explicit; the
generic core never guesses whether a domain requirement blocks or watches.

#### `TeamEvidenceAggregationConfig`

```text
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
```

Every field above is required. Node 2 provides no default production policy and
no `DEFAULT_CONFIG`. A later BTC policy node constructs the immutable approved
configuration, including any BTC-specific freshness windows, caps,
requirements, contradiction thresholds, and publication floor/ceiling.

Every assessment `probability_yes` and `requested_weight` must pass a raw
finite bound check in `[0, 1]` before fixed-six quantization. A zero requested
weight is structurally valid and receives the diagnostic disposition defined
below; it never enters allocation.

`contradiction_no_probability_max` and
`contradiction_yes_probability_min` each independently pass the same raw
finite `[0, 1]` check before quantization. Their canonical values are then
validated together; a strict raw order that collapses to equality after
fixed-six quantization is invalid.

Configuration invariants are:

- duration fields and group caps normalize to finite, nonnegative fixed-six
  values;
- group caps and requirement minimum weights are no greater than `1.000000`;
- `max_requirement_assignments_per_evidence`, `maximum_records`,
  `maximum_requirements`, `maximum_requirement_memberships`, and
  `maximum_witness_edges` are positive exact ints;
- `0.000000 <= contradiction_no_probability_max <
  contradiction_yes_probability_min <= 1.000000` after quantization;
- `0.000000 <= contradiction_watch_score <=
  contradiction_block_score <= 1.000000`;
- `0.000000 <= publish_probability_floor <=
  publish_probability_ceiling <= 1.000000`;
- requirement IDs are unique and canonically sorted;
- `len(requirements) <= maximum_requirements`.

Caller-supplied ceilings may tighten but never relax these domain-neutral,
non-exported implementation maxima:

```text
max_requirement_assignments_per_evidence <= 32
maximum_records <= 128
maximum_requirements <= 32
maximum_requirement_memberships <= 1024
maximum_witness_edges <= 256
```

The sum of `requirement_ids` lengths across distinct evidence-revision
projections must not exceed `maximum_requirement_memberships` or the absolute
`1024` limit. At the witness boundary, each raw `requirement_ids` value must
also be an exact tuple whose individual length does not exceed either of those
aggregate ceilings before any record reconstruction or element traversal. The
per-tuple check is a cheap necessary resource preflight; the distinct-revision
sum remains the authoritative aggregate check.

At the pure Node 2B direct-call boundary,
`max_requirement_assignments_per_evidence` is not a requirement-membership
ceiling and is used only as the per-record witness-flow capacity defined below.
The Node 2A aggregate-input validator separately retains its historical use of
that same configuration field as the per-revision `requirement_ids` tuple
bound. In both boundaries, the distinct-membership and edge bounds constrain
canonicalization and graph work even when a hostile caller supplies permissive
configuration values; they are resource-safety limits, not BTC evidence
policy.

Tests use conspicuously non-BTC example bounds, such as `0.110000` and
`0.890000`, to prove the core obeys supplied values instead of embedding the
later BTC policy.

### Public input-contract validation and canonical selection

`team_evidence_aggregation_types.py` exposes exactly:

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

This public pure validator returns `None` on success and raises `ValueError` on
failure. It owns global functional-dependency validation, closed linear
revision-chain validation, local time-order validation, configuration hard
maxima, raw record/requirement/membership ceilings, and exact current-selection
validation. Derived candidate-edge counting remains a Node 2B witness-builder
responsibility because it depends on temporal selection and effective
allocation. The validator revalidates exact types and hard flags rather than
trusting constructor calls. It validates caller-supplied structure, not source
authority, source trust, or the truth of upstream audit timestamps. Node 2C
must enter through the selectors and remains the sole owner of dispositions,
allocation inputs, temporal reduction, status, and aggregation.

Both selectors reuse one private complete validation and canonical-indexing
path also used by the public validator; neither provides a weaker validation
mode, trusts a prior call, or uses ambient cache state. Each public call
recomputes that pure bounded path from its exact arguments.

`select_team_evidence_canonical_current_records` returns exactly one
`TeamEvidenceAggregationRecord` for every exact current selection, sorted by
the canonical record key. Each returned record combines the
evidence-revision-global canonical capture with the selected assessment
revision. It raises `ValueError` when that exact combination is absent. Empty
current selections return an empty tuple. Node 2C uses these returned record
identities as its sole canonical-current identity set, keyed exactly by
`(source_lineage_id, capture_id, evidence_revision_id,
assessment_revision_id)`.

`select_team_evidence_canonical_capture_records` returns every supplied record
whose capture equals the evidence-revision-global canonical capture, across
every represented evidence revision and assessment revision, sorted by the
canonical record key. A canonical capture represented with multiple assessment
revisions therefore produces one returned record per represented assessment;
an evidence revision need not be current to have canonical-capture records.
Node 2C sets `canonical_capture=True` if and only if a diagnostic record's exact
four-field identity belongs to this returned set; every other record receives
`False`.

The selectors perform no temporal filtering, allocation, witness construction,
disposition assignment, status derivation, or latest/tip/timestamp fallback.
Node 2C does not duplicate Node 2A's private capture-selection logic.

### Derived types

The same types module defines these exact derived names.

#### `TeamEvidenceTemporalAssessment`

```text
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
```

#### `TeamEvidenceWeightAllocation`

```text
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
```

#### `TeamEvidenceRequirementWitness`

```text
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
```

#### `TeamEvidenceRequirementCoverage`

```text
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
```

#### `TeamEvidenceDiagnosticRow`

```text
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
```

Every accepted input record produces exactly one diagnostic row. No arithmetic
filter is permitted to erase a structurally valid supplied record.

Diagnostic rows are sorted by the exact canonical record key using their
`(assessment_revision_id, evidence_revision_id, captured_at, capture_id,
source_lineage_id)` fields. Carrying `captured_at` in the row makes that order
locally verifiable without consulting the original input record. This exact
five-field sort key is distinct from the four-field selector/allocation join
identity `(source_lineage_id, capture_id, evidence_revision_id,
assessment_revision_id)` and the three-field selected-pair identity; neither
identity projection may replace or shorten the canonical sort key.

`selected_current_revision=True` if and only if the row's exact
`(source_lineage_id, evidence_revision_id, assessment_revision_id)` projection
belongs to the canonical-current selector output projected to the same three
fields. This flag is pair-level and independent of capture identity: every
later recapture of a selected exact revision pair remains
`selected_current_revision=True` while receiving `canonical_capture=False` and
the applicable `duplicate_capture` disposition.

#### `TeamEvidenceContradictionResult`

```text
yes_support_weight: Decimal
no_support_weight: Decimal
neutral_weight: Decimal
contradiction_score: Decimal
status: str  # exactly "none", "watch", or "blocked"
paper_only: bool
report_only: bool
readonly: bool
```

#### `TeamEvidenceAggregationResult`

```text
evaluated_at: datetime
config_version: str
config_digest: str
status: str  # exactly "ready", "watch", or "blocked"
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

The result deliberately has no team ID, market ID, condition ID, forecast ID,
run ID, evaluation-scope ID, `tea`, `tfr`, or `tfe` field.

## Canonical Identifiers, Digests, And Revision Rules

### Canonical syntax

Caller-supplied IDs, keys, requirement IDs, reason codes, and config versions
are opaque lowercase ASCII identifiers. They must:

- use only `a-z`, `0-9`, `.`, `_`, `-`, and `:`;
- start and end with an ASCII letter or digit;
- contain no whitespace, path separator, URL syntax, control character, or
  secret-bearing free text;
- be no longer than 160 bytes in UTF-8.

All digest fields are exactly 64 lowercase hexadecimal SHA-256 characters.
Node 2 rejects uppercase, `sha256:` wrappers, truncated values, and arbitrary
free-text digests. Raw content and rationale are not admitted.

Source-lineage, capture, evidence-revision, and assessment-revision IDs and
digests are supplied by the caller. Node 2 validates their syntax and
relationships but does not mint, replace, or infer them. Their digests may bind
upstream manifests or raw bytes that Node 2 intentionally does not receive, so
Node 2 does not pretend to recompute those upstream digests from its reduced
view.

### Cross-layer relationships

For every record:

- capture source-lineage ID/digest equals the nested source lineage;
- evidence-revision source-lineage ID/digest equals the nested source lineage;
- capture `content_digest` equals evidence-revision `content_digest`;
- assessment evidence-revision ID/digest equals the nested evidence revision;
- all nested hard flags are true.

Across the supplied diagnostic universe, each ID is a functional dependency:

- one source-lineage ID maps to one source-lineage digest;
- one capture ID maps to one complete capture projection;
- one evidence-revision ID maps to one complete evidence-revision projection;
- one assessment-revision ID maps to one complete assessment-revision
  projection.

Reusing an ID with a different digest or owned field is a hard validation
failure. Reusing one digest as an alias for two different IDs at the same
identity layer is also rejected. Distinct captures may share a content digest;
that is the supported recapture case.

### Append-only revision graphs

Predecessor ID and predecessor digest are either both `None` or both present.
Every referenced predecessor must be represented in the supplied records so
the core can validate the complete supplied chain.

Within each `source_lineage_id`, all supplied evidence revisions form exactly
one connected linear chain: exactly one root, no second root, no fork, no
merge, and no cycle. Every non-root has exactly one supplied predecessor, and
every non-tip has exactly one successor. All assessment revisions whose
referenced evidence belongs to that lineage likewise form exactly one
connected linear chain under their assessment predecessor fields. Conflicting
complete projections that attempt to give one revision ID two predecessors
are merge attempts and fail functional-dependency validation before graph
validation. These closed-chain rules establish one semantic evidence series
per lineage within the call; they do not establish source authority.

An evidence-revision edge must:

- retain the same source lineage;
- point to an existing evidence revision;
- use a new evidence-revision ID and digest;
- have `recorded_at` no earlier than its predecessor;
- change at least one of content digest or canonical requirement IDs; and
- change `content_digest` whenever `freshness_anchor_at` changes.

An assessment-revision edge must:

- point to an existing assessment revision in the same source-lineage evidence
  chain;
- use a new assessment-revision ID and digest;
- have `assessed_at` no earlier than its predecessor; and
- change at least one of evidence-revision reference, canonical `P(YES)`,
  requested weight, rationale digest, independence key, or correlation key.

No-op revisions are rejected. Equal successor audit timestamps are valid
because predecessor edges provide total order within one clock instant; an
earlier successor timestamp is invalid. Changing only `freshness_anchor_at` is invalid,
as is changing the anchor together with requirements but without changing
`content_digest`; an anchor change must accompany changed content. Changing
only `recorded_at` or only `assessed_at` never makes a semantic successor
valid. A content, requirement, or freshness-anchor change requires a new
evidence revision. A probability, weight, rationale, or cap-group change
requires a new assessment revision. A change spanning both ownership sets
requires both new revision IDs.

### Local availability order

The Node 2A input-contract validator groups distinct capture projections by
evidence revision and orders each group by
`(captured_at, capture_id, capture_digest)`. The first projection is the
earliest canonical capture for that evidence revision for this structural
check only. It must satisfy:

```text
earliest_canonical_capture.captured_at <= evidence_revision.recorded_at
```

Every assessment revision must independently satisfy:

```text
evidence_revision.recorded_at <= assessment_revision.assessed_at
```

Together with the nondecreasing successor-time rules above, these are local
availability/audit ordering checks. Node 2 does not validate the authority or
truth of the timestamps. A later recapture may occur after evidence-revision
recording or assessment creation and remains a duplicate; it does not fail
these checks. Computing the earliest projection here establishes the structural
canonical capture for the evidence revision. Node 2C consumes that validated
choice for the exact current pair; it does not independently reselect a later
capture.

### Recapture invariants

A recapture uses a new capture ID/digest and `captured_at`, but retains the same
evidence and assessment revisions. Therefore a recapture cannot change:

- source lineage;
- content digest;
- requirement IDs;
- freshness anchor;
- evidence-revision `recorded_at`;
- canonical `P(YES)`;
- requested weight;
- rationale digest;
- independence key or correlation key;
- assessment-revision `assessed_at`.

Any such change under the old revision IDs fails the whole call. The caller
must append explicit revisions instead.

Global functional-dependency validation covers the complete supplied call.
Cross-call append-only enforcement belongs to the later local Supabase schema
and atomic persistence nodes, which persist the same canonical identities.
Node 2 remains stateless and does not add a hidden cache to simulate durable
identity history.

### Explicit current selection

`current_revisions` is a semantic set sorted by
`(evidence_revision_id, assessment_revision_id)`. Each selection must resolve
to at least one record with all four selected IDs/digests equal. At most one
exact evidence/assessment pair may be current for each `source_lineage_id`.
Two current pairs in one lineage are invalid even when they select different
evidence revisions or assessment revisions.

Selection is exact, not tip-oriented. An old exact evidence/assessment pair
remains selectable when valid successors for that lineage are also supplied.
The selection need not identify either chain's tip, and successor presence
does not supersede it implicitly.

For each evidence revision, Node 2A computes one globally earliest capture
projection across all records and all assessment revisions using
`(captured_at, capture_id, capture_digest)`. Every current selection must
resolve to a record that combines this exact canonical capture with the
selected assessment revision. A selected assessment represented only with a
later recapture is invalid; it cannot use that recapture to repair freshness,
timeliness, or availability. Node 2 cannot detect captures omitted from a
single caller-supplied history, so Node 3 and the later local-Supabase history
reader must provide the complete capture history for each selected evidence
revision and bind that history in the evaluation scope.

`select_team_evidence_canonical_current_records` exposes only the validated
canonical combinations described above. It returns them in canonical record
key order; it never selects a later capture, an assessment-local capture, a
revision tip, or a timestamp-derived replacement.

`select_team_evidence_canonical_capture_records` exposes the canonical-capture
records for every represented evidence revision, including revisions with no
current selection. This is the sole source of every diagnostic row's
`canonical_capture` flag.

Records whose exact pair is absent from `current_revisions` remain in the
diagnostic universe with disposition `not_current_revision`; they never enter
arithmetic. The core never chooses a revision by timestamp, tuple position,
revision depth, maximum capture time, or lexical ID. There is no latest-row,
latest-valid, or latest-ready fallback.

## Decimal Contract

### Exact values and bounds

All Decimal inputs must have exact base type `Decimal` and be finite. Decimal
subclasses, ints, bools, floats, NaN, signaling NaN, and infinities are
rejected.

For every bounded value, the unquantized raw Decimal is checked first. A value
such as `Decimal("1.0000004")` cannot become valid by rounding to
`1.000000`. A value such as `Decimal("-0.0000004")` cannot become valid by
rounding to zero.

Public in-range Decimal inputs need not already have six fractional places or
the canonical exponent. After raw bounds validation, they may be quantized to
`Decimal("0.000001")`; Node 2's stored and emitted real-valued domain values
are fixed-six. Node 2 canonicalizes every zero to unsigned
`Decimal("0.000000")`; this new core has no signed-zero legacy payload to
preserve. Discrete count and resource-ceiling fields are never quantized: they
remain exact base `int` values and encode as JSON integers.

### Isolated context

Every Decimal operation, including products, sums, division, proportional
allocation, contradiction, and quantization, executes inside a local copy of:

```python
Context(prec=64, rounding=ROUND_HALF_EVEN)
```

No code reads or mutates `decimal.getcontext()`. Tests deliberately replace the
ambient context with low precision and a different rounding mode; outputs must
remain byte-identical.

Inputs are canonically sorted before arithmetic. Products and running sums are
not quantized early. Allocation is the one exception: it works in exact
integer micro-units as specified below. Weighted probability and contradiction
ratios are quantized once, after the exact numerator and denominator are
complete.

## Temporal Contract

`team_evidence_aggregation_temporal.py` exposes:

```python
def assess_team_evidence_temporal(
    record: TeamEvidenceAggregationRecord,
    *,
    evaluated_at: datetime,
    config: TeamEvidenceAggregationConfig,
) -> TeamEvidenceTemporalAssessment: ...
```

Exact base `datetime` values must be timezone-aware. They are normalized to
UTC without dropping microseconds. Naive datetimes and datetime subclasses are
rejected. The codec emits one canonical UTC representation.

The temporal calculations are:

```text
evidence_age_seconds = evaluated_at - freshness_anchor_at
capture_lag_seconds  = captured_at - freshness_anchor_at

effective_at_evaluation = freshness_anchor_at <= evaluated_at
captured_at_evaluation  = captured_at <= evaluated_at
evidence_revision_available_at_evaluation = recorded_at <= evaluated_at
assessment_revision_available_at_evaluation = assessed_at <= evaluated_at
fresh = effective_at_evaluation
        and evidence_age_seconds <= max_evidence_age_seconds
timely = capture_lag_seconds >= 0
         and capture_lag_seconds <= max_capture_lag_seconds
```

Both limits are inclusive. Ages and lags are exact fixed-six Decimal seconds;
negative diagnostic values are retained rather than clamped.

Timedelta seconds are constructed inside the isolated Decimal context as
`Decimal(delta.days) * Decimal(86400) + Decimal(delta.seconds) +
Decimal(delta.microseconds) / Decimal(1000000)`. Calls to
`timedelta.total_seconds()` and `datetime.timestamp()` are forbidden because
they introduce binary floating-point values before Decimal conversion.

A record is temporally eligible only when its freshness anchor is effective,
its capture is available, its evidence revision is available, its assessment
revision is available, and it is both fresh and timely. Availability is thus
evaluated independently at all three caller-supplied audit layers; a future
revision or future assessment cannot leak into a historical replay merely
because its capture already existed.

The freshness anchor is the trusted effective/content time supplied by the
evidence revision. `captured_at`, a recapture time, an ingestion time, and
`evaluated_at` can never replace it. Capture lag is only an exclusion gate. It
never reduces evidence age, increases weight, advances the anchor, or refreshes
evidence.

## Diagnostic And Arithmetic Universes

### Canonical record key

Every record is sorted by this exact key before validation and reduction:

```text
(
  assessment_revision_id,
  evidence_revision_id,
  captured_at,
  capture_id,
  source_lineage_id,
)
```

The pair `(capture_id, assessment_revision_id)` must be unique. This still
allows one capture to support distinct explicit assessment revisions and one
assessment revision to appear in distinct recaptures.

### Canonical capture for a current revision

For each evidence revision across every represented assessment revision,
capture projections are deduplicated and ordered by:

```text
(captured_at, capture_id, capture_digest)
```

The first distinct capture projection is canonical for that evidence revision.
For a current exact pair, the record combining that canonical capture and the
selected assessment revision is the only capture eligible to continue; records
for that pair using later captures receive `duplicate_capture`. A selected pair
without the canonical-capture combination is rejected by Node 2A. Since the
canonical capture has the minimum possible lag, recapture cannot repair
freshness, timeliness, or availability through a later assessment revision.

### One disposition per supplied record

Each structurally valid supplied record receives exactly one of thirteen
dispositions using this precedence:

1. `not_current_revision`;
2. `duplicate_capture`;
3. `freshness_anchor_after_evaluation`;
4. `capture_after_evaluation`;
5. `evidence_revision_after_evaluation`;
6. `assessment_revision_after_evaluation`;
7. `stale`;
8. `capture_before_freshness_anchor`;
9. `capture_lag_exceeded`;
10. `zero_requested_weight`;
11. `independence_cap_exhausted`;
12. `correlation_cap_exhausted`;
13. `included`.

Rows stopped at an earlier stage have zero allocated/effective weight. Their
raw requested weight, probability, temporal values, identity, current flag,
and canonical-capture flag remain visible in diagnostics.

The **diagnostic universe** is all accepted records and therefore has
`len(result.diagnostics) == len(input.records)`.

The **arithmetic universe** is exactly the diagnostic rows with disposition
`included`. Such a row is current, the canonical capture, effective, available
at the capture, evidence-revision, and assessment-revision layers, fresh,
timely, positive-weight, deduplicated, and positive after both caps.

Excluded rows do not independently force `watch` or `blocked`. Their effects
are expressed through a zero arithmetic universe or unmet configured
requirements. This prevents the generic core from inventing a domain policy
about redundant stale or superseded history.

## Two-Stage Weight Allocation

`team_evidence_aggregation_allocation.py` exposes:

```python
def allocate_team_evidence_weights(
    records: tuple[TeamEvidenceAggregationRecord, ...],
    *,
    config: TeamEvidenceAggregationConfig,
) -> tuple[TeamEvidenceWeightAllocation, ...]: ...
```

The caller passes only selected canonical records that have passed temporal
gates and have positive requested weight. The function revalidates exact types,
unique canonical keys, hard flags, and configured resource bounds. It returns
one allocation per input, sorted by the canonical record key.

### Micro-unit apportionment

Fixed-six weights are converted exactly to nonnegative integer micro-units.
For a group with weights `w_i`, total `W`, and cap `C`:

- if `W <= C`, allocations equal the input weights;
- otherwise compute each exact rational quota `q_i = w_i * C / W`;
- assign `floor(q_i)` micro-units first;
- distribute the remaining `C - sum(floor(q_i))` micro-units by descending
  fractional remainder;
- break equal remainders by the canonical record key.

This is deterministic largest-remainder apportionment. It preserves
nonnegativity, never allocates more than requested, and makes the capped group
sum exactly equal to the fixed-six cap. It avoids independent HALF_EVEN
rounding that could exceed a cap or make results depend on input order.
Largest remainder is not per-record monotone in the cap: increasing a cap may
reduce one row by one micro-unit while preserving exact group conservation.
For requests `(0.000001, 0.000003, 0.000003)`, caps `0.000003` and `0.000004`
produce allocations `(0.000001, 0.000001, 0.000001)` and
`(0.000000, 0.000002, 0.000002)`, respectively. The smallest request loses one
micro-unit when the cap increases, while the applicable group sums remain
exactly the respective caps.

### Required stage order

Allocation runs in exactly two stages:

1. Partition requested weights by `independence_key` and apply
   `independence_group_weight_cap` with the apportionment above. The result is
   `independence_allocated_weight`.
2. Partition stage-one weights by `correlation_key` and apply
   `correlation_group_weight_cap` with the same apportionment. The result is
   `effective_weight`.

The stages are never reversed, merged, or implemented as per-record `min()`.
Stage two can only preserve or reduce stage-one weight. A zero at stage one
maps to `independence_cap_exhausted`; a positive stage-one value reduced to zero
at stage two maps to `correlation_cap_exhausted`.

The cap flags report group-level cap activation:

- `independence_cap_applied` is true on every row in an independence group if
  and only if that group's pre-cap requested-weight total strictly exceeds
  `independence_group_weight_cap`;
- `correlation_cap_applied` is true on every row in a correlation group if and
  only if that group's stage-one weight total strictly exceeds
  `correlation_group_weight_cap`.

Equality is false for the entire group. Whether one row was reduced is inferred
by comparing `independence_allocated_weight` with `requested_weight`, or
`effective_weight` with `independence_allocated_weight`; the flags do not encode
that per-row comparison.

Independence-then-correlation is a substantive ordered attenuation policy. It
guarantees tuple-permutation determinism and satisfaction of both cap families,
but it does not promise cap-family order invariance or a globally
maximum-total cap-feasible allocation. Witness coverage consumes this one
canonical allocation and never searches for an alternate cap-feasible vector.
The two stages must not be replaced by a simultaneous optimizer.

## Global Requirement Witnesses

`team_evidence_aggregation_witness.py` exposes:

```python
def build_team_evidence_requirement_coverage(
    allocation_input_records: tuple[TeamEvidenceAggregationRecord, ...],
    allocations: tuple[TeamEvidenceWeightAllocation, ...],
    *,
    config: TeamEvidenceAggregationConfig,
) -> tuple[TeamEvidenceRequirementCoverage, ...]: ...
```

### Resource-first witness validation

The public witness boundary treats every argument and dataclass slot as
untrusted even when the outer object has an exact public type. It performs the
following bounded preflight in this exact order, before canonical
reconstruction, unsafe sorting/hashing/equality, generic
`Decimal.as_tuple()`, allocation recomputation, candidate generation, or graph
construction:

1. Require exact base tuples for `allocation_input_records` and `allocations`
   and exact `TeamEvidenceAggregationConfig` for `config`. No tuple element is
   visited before all three top-level type checks pass.
2. Read the five resource slots from the exact config and require exact base
   `int` values in their documented positive implementation ranges. Validate
   the config hard flags, then use only exact-tuple `len` calls to require first
   that records are at most `min(config.maximum_records, 128)`, and next that
   allocations are independently at most that bound and have the same length
   as records. Then require `config.requirements` to be an exact tuple and at
   most `min(config.maximum_requirements, 32)`. None of these checks iterates or
   reconstructs an element.
3. Within those established bounds, require every record, allocation, and
   requirement element to have its exact documented dataclass type. Require
   all four nested layers of every record to have their exact documented types
   before validating any record/layer, allocation, or requirement hard flag by
   identity with `True`. Slot access on those exact slotted dataclasses is
   permitted, but no caller-controlled comparison, ordering, hashing, iterator,
   timezone hook, or representation method is.
4. For every raw record, require
   `record.evidence_revision.requirement_ids` to be an exact tuple and check
   its length against both `config.maximum_requirement_memberships` and `1024`
   before visiting any requirement ID or reconstructing any record. After that
   O(1) length gate, visit at most 1024 members and require each to be an exact
   base string in the 1..160 representation envelope; this prevents nested
   tuple recursion or caller-defined member behavior. This is a per-raw-
   projection resource preflight only; it does not use
   `max_requirement_assignments_per_evidence` and does not replace the later
   deduplicated aggregate-membership check.
5. Preflight every scalar representation across the entire bounded input before
   reconstructing any element. Exact strings have a cheap 160-code-point
   envelope before UTF-8 encoding or canonical-ID matching. An exact-base
   Decimal must be finite, share the fixed-six quantum through the quiet
   `same_quantum(Decimal("0.000001"))` operation, and have an adjusted exponent
   bounded by the 64-digit local context before it can reach `as_tuple`,
   quantization, comparison, or a constructor. An exact-base `datetime` must
   have `tzinfo is timezone.utc` before any datetime comparison or constructor.
   An exact tuple occupying a scalar slot, and all other wrong scalar types, are
   carried only by identity through this callback-free representation walk;
   tuple members are traversed here only for already bounded, schema-authorized
   tuple slots. The existing exact constructors then reject wrong scalar types
   without invoking a caller-defined equality, ordering, representation,
   timezone, or iterator hook. Those constructors retain the authoritative
   field-specific ID/digest/status, Decimal range and unsigned-zero checks after
   the representation envelope has made that work bounded.
6. After the representation walk, hash only the already exact-base,
   160-code-point-bounded `evidence_revision_id` strings. Track the maximum raw
   membership-tuple length observed for each ID, increment the distinct-ID sum
   by only that maximum's increase, and fail at either aggregate ceiling before
   reconstruction. Then canonically reconstruct config requirements, records,
   allocations, and config; compare canonical representations; sort canonical
   scalar values; and require every repeated evidence-revision ID and every
   repeated assessment-revision ID to carry one exact projection before
   allocation recomputation. The assessment check prevents one immutable
   assessment identity from forking its independence/correlation projection
   across multiple record joins. For inputs that survive projection validation,
   the bounded raw aggregate is the same authoritative
   distinct-revision membership sum; no second aggregate traversal is needed.

`minimum_witness_count` is the deliberate exception to scalar-size bounding:
the Node 2A contract permits an arbitrarily large positive exact base `int`,
the witness output preserves it exactly, and matching clamps every derived
demand, objective bound, loop, and graph capacity to candidate degree or the
256-edge ceiling before arithmetic work. The representation walk carries that
exact integer only by object identity; it never converts it to text, hashes it,
or sizes a graph from its bit length.

Thus constructor-bypassed objects cannot trigger a dataclass constructor,
Decimal representation operation, timezone callback, user-defined dunder, or
downstream allocation/graph callback before cheap cardinality and exact-leaf
validation. The preflight is fail-closed: `AttributeError`, `TypeError`,
Decimal signaling/invalid-operation conditions, and object representations do
not escape the stable public `ValueError` mapping specified below.

After that validation, the witness builder recomputes
`allocate_team_evidence_weights(allocation_input_records, config=config)` and
requires the caller-supplied allocation tuple to equal that canonical result
exactly. Under the Node 2C production caller contract, the record argument is
the complete temporally eligible, selected-current, canonical-capture,
positive-requested-weight tuple originally passed to allocation, not its
post-cap positive-effective subset. Those selection, capture, and temporal
properties are established by Node 2C before the call; the pure public witness
boundary neither derives nor validates them and also supports structurally
valid bounded direct calls that do not represent a Node 2C reduction. In both
call modes, the builder rejects missing, duplicate, extra, reordered, or
altered allocation rows before candidate generation. The exact
record/allocation join key is:

```text
(source_lineage_id, capture_id, evidence_revision_id, assessment_revision_id)
```

The joined allocation's `independence_key`, `correlation_key`, and
`requested_weight` must also equal the corresponding assessment fields. Only
paired rows whose canonical allocation has positive `effective_weight`
participate. A candidate edge from paired record `e` and allocation `a` to
requirement `r` exists exactly when:

- `r.requirement_id` appears in `e.evidence_revision.requirement_ids`; and
- `a.effective_weight >= r.minimum_effective_weight`.

At the pure direct-call boundary, every requirement ID on a joined
positive-effective row must exist in config; the witness builder does not
classify a row as historical, current, selected, canonical, or temporally
eligible. A joined zero-effective row may retain a retired requirement ID
because it creates no candidate edge. Under the Node 2C production caller
contract, positive-effective rows correspond to `included` arithmetic records,
zero-effective rows correspond to current cap-exhausted diagnostics, and
historical or non-current records never enter the witness call at all. They may
retain retired IDs elsewhere in the diagnostic input without weakening the
pure witness rule. The candidate edge count is checked against
`maximum_witness_edges` before graph construction.

### B-matching network

The assignment is one global capacitated bipartite matching, represented as a
max-flow network:

```text
source
  -> evidence node (record join key)        capacity max assignments per record
  -> (requirement_id, independence_key)    capacity 1 per candidate edge
  -> requirement node                      capacity 1 per independence group
  -> sink                                  capacity minimum witness count
```

The intermediate node enforces that one independence group can contribute at
most one witness to a particular requirement. The evidence-node capacity is
`max_requirement_assignments_per_evidence`, shared globally across all
requirements for one exact four-field record/allocation join key
`(source_lineage_id, capture_id, evidence_revision_id,
assessment_revision_id)`. Despite the historical field name, this flow
capacity is per record node, not shared globally by `evidence_revision_id`.
Multiple assessment records carrying the same exact evidence-revision
projection each receive independent capacity when passed directly to this
public API. Production Node 2C current-selection semantics provide one selected
record per lineage for an evaluation, but the pure witness function preserves
its wider direct-call contract. Within that pure direct-call boundary,
`max_requirement_assignments_per_evidence` is only the per-record flow capacity;
the child is bounded instead by exact aggregate-membership and candidate-edge
ceilings and does not reapply the Node 2A tuple check. The Node 2A aggregate
boundary separately uses the same historical field as its per-revision tuple
bound. A direct-call record may therefore list more distinct requirements than
its assignment capacity while remaining within the aggregate membership
ceilings, even though that fixture would fail the narrower Node 2A aggregate
boundary; matching may select no more than the per-record flow capacity. A
record cannot list the same requirement twice because requirement tuples are
unique.

The implementation uses a deterministic standard-library integer max-flow
algorithm. No graph dependency is added to `pyproject.toml`. A per-requirement
greedy algorithm is forbidden because it can consume flexible evidence before
a constrained requirement is considered.

The global objective respects status severity. It first maximizes the number of
assignments to requirements whose `unmet_status` is `blocked`. Subject to that
fixed optimum, it maximizes assignments to `watch` requirements. Thus a watch
edge can never consume capacity that could increase blocked-requirement
coverage. If all blocked demand is jointly feasible, every optimum must
saturate it; lexical requirement names cannot turn an avoidable watch into a
block.

This objective maximizes severity-partitioned assignment counts, not the number
of fully satisfied requirements. When all blocked demand is not jointly
feasible, a lexical optimum may assign scarce evidence partially to a
high-demand blocked requirement even when assigning it elsewhere would fully
satisfy a lower-demand blocked requirement. Both outcomes remain blocked under
the status contract because at least one blocked requirement is unsatisfied;
the canonical coverage rows retain the exact partial-assignment evidence.

### Canonical optimum matching

First compute the optimum objective vector `(F_blocked, F_watch)` described
above. Candidate witness edges use this exact lexical key:

```text
(
  requirement_id,
  source_lineage_id,
  evidence_revision_id,
  assessment_revision_id,
  capture_id,
)
```

Then iterate edges in that order. Tentatively force an edge and ask whether a
feasible residual b-matching can still attain exactly
`(F_blocked, F_watch)`, accounting for all already forced and forbidden edges.
Keep the edge when feasibility is preserved; otherwise forbid it. This
produces the lexicographically smallest edge set among all matchings with the
severity-optimal vector. Incidental adjacency order, dict order, input tuple
order, requirement naming, and augmenting-path order cannot select a matching
with a worse readiness consequence.

Coverage may be partial when total flow is less than total configured demand.
For each requirement:

```text
assigned_witness_count = number of canonical assigned edges
satisfied = assigned_witness_count >= minimum_witness_count
```

Witness tuples and coverage tuples are canonically sorted. The requirement's
explicit `unmet_status` determines the readiness effect of an unsatisfied
coverage row.

`requirement_coverage` contains exactly one row for every item in
`config.requirements`, sorted by `requirement_id`, with no missing, duplicate,
or extra IDs. A requirement with zero candidate edges still emits a row with
`assigned_witness_count=0`, `witnesses=()`, and `satisfied=False`, while
preserving its configured `minimum_witness_count`, `minimum_effective_weight`,
and `unmet_status` exactly.

## Aggregation, Contradiction, And Status

`team_evidence_aggregation.py` exposes only:

```python
def build_team_evidence_aggregation_result(
    aggregation_input: TeamEvidenceAggregationInput,
    *,
    config: TeamEvidenceAggregationConfig,
) -> TeamEvidenceAggregationResult: ...


def validate_team_evidence_aggregation_result(
    result: TeamEvidenceAggregationResult,
    *,
    aggregation_input: TeamEvidenceAggregationInput,
    config: TeamEvidenceAggregationConfig,
) -> None: ...
```

Validation rematerializes the expected semantic result through a private pure
implementation and compares every field, including the core digest. It does
not trust a directly constructed or `object.__new__`-bypassed result. The
builder and validator share that one private semantic materializer; the builder
does not recursively call the public validator, and the validator is an
independent public entry point rather than a second reduction algorithm.
Validation also asserts that `requirement_coverage` has exact one-to-one
requirement-ID coverage of `config.requirements`, including zero-candidate
requirements.

### Arithmetic probability

For included arithmetic records sorted by canonical record key:

```text
numerator   = sum(probability_yes_i * effective_weight_i)
denominator = sum(effective_weight_i)
arithmetic_probability_yes = quantize_once(numerator / denominator)
```

The arithmetic mean is chosen instead of log-odds pooling because it is exact
under the fixed-six Decimal contract and requires no transcendental or float
operation. Caps determine influence; the core does not add a hidden prior,
intercept, confidence transform, or market-price term.

If the denominator is zero, `arithmetic_probability_yes` is `None`. Otherwise
the unquantized ratio is checked to remain in `[0, 1]` before fixed-six
quantization.

`requested_weight_total` and `independence_allocated_weight_total` cover the
positive, current, canonical, temporally eligible allocation candidates.
`effective_weight_total` and `arithmetic_record_count` cover included rows.

### Contradiction

Included evidence is partitioned using caller-supplied config:

```text
no support:  probability_yes <= contradiction_no_probability_max
yes support: probability_yes >= contradiction_yes_probability_min
neutral:     probability lies strictly between the two bounds
```

Let `Y`, `N`, and `U` be summed effective weights for yes, no, and neutral
support. If either `Y` or `N` is zero, contradiction score is `0.000000` and
status is `none`. Otherwise:

```text
contradiction_score = quantize_once(2 * min(Y, N) / (Y + N))
```

Neutral weight is reported but does not dilute opposing support. Equal opposing
weight yields `1.000000`; a small minority yields a proportionally lower score.
Classification is inclusive and ordered:

1. score at or above `contradiction_block_score` -> `blocked`;
2. otherwise score at or above `contradiction_watch_score` -> `watch`;
3. otherwise -> `none`.

The explicit both-sides check occurs before threshold comparison, so a
configured watch threshold of zero does not manufacture contradiction when
only one side is represented.

### Readiness precedence

Top-level status is derived, never accepted from the caller:

1. `blocked` when there is no arithmetic probability, any unsatisfied
   requirement has `unmet_status="blocked"`, or contradiction is `blocked`;
2. otherwise `watch` when any unsatisfied requirement has
   `unmet_status="watch"` or contradiction is `watch`;
3. otherwise `ready`.

This is strict `blocked > watch > ready` precedence. An unmet watch requirement
cannot downgrade a simultaneous blocker to watch, and a ready-looking
probability cannot override requirements or contradiction.

The canonical, lexically sorted reason-code set uses only these static codes:

```text
aggregation_ready
blocking_requirement_unmet
contradiction_blocked
contradiction_watch
no_arithmetic_evidence
publish_probability_ceiling_applied
publish_probability_floor_applied
watch_requirement_unmet
```

Each applicable non-ready signal contributes its code independently. A blocked
result can therefore retain `watch_requirement_unmet` or
`contradiction_watch` alongside a blocking code. `aggregation_ready` appears
only for a ready result. Publication-bound codes appear only for a ready result
whose arithmetic probability was strictly outside the applicable bound; they
never replace `aggregation_ready`. No-arithmetic, requirement, and
contradiction codes are omitted when their stated condition is false.

Requirement IDs and witness details live in typed coverage rows rather than in
dynamically generated reason-code strings.

### Ready-only publication

`arithmetic_probability_yes` is diagnostic and remains present whenever the
arithmetic universe is nonempty, including `watch` and `blocked` results.

`publishable_probability_yes` is:

- `None` for every `watch` or `blocked` result;
- the arithmetic probability clamped to the exact caller-supplied
  `[publish_probability_floor, publish_probability_ceiling]` for `ready`.

Clamping never changes `arithmetic_probability_yes`. A floor or ceiling reason
code is added only when the ready value is actually changed. Node 2 contains no
hard-coded `2%` to `98%` policy; the later BTC policy node may supply those
values through config after its own review.

## Canonical Codec And Core Digest

`team_evidence_aggregation_codec.py` exposes:

```python
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

Each payload call returns a new JSON-ready dictionary. The input payload is a
canonical fragment for Node 3 to include in its larger evaluation scope; Node
2 does not hash it into a run identity.

The four payload envelopes have these exact closed shapes:

```text
config payload:
{
  "schema_version": "pal.team_evidence_aggregation.config.v1",
  "config": {direct field map of TeamEvidenceAggregationConfig}
}

input payload:
{
  "schema_version": "pal.team_evidence_aggregation.input.v1",
  "input": {direct field map of TeamEvidenceAggregationInput}
}

core payload:
{
  "schema_version": "pal.team_evidence_aggregation.core.v1",
  "result": {direct field map of TeamEvidenceAggregationResult except core_digest}
}

full result payload:
{
  "schema_version": "pal.team_evidence_aggregation.result.v1",
  "result": {direct field map of TeamEvidenceAggregationResult including validated core_digest}
}
```

The config field map contains exactly `config_version`,
`max_evidence_age_seconds`, `max_capture_lag_seconds`,
`independence_group_weight_cap`, `correlation_group_weight_cap`,
`max_requirement_assignments_per_evidence`,
`contradiction_no_probability_max`, `contradiction_yes_probability_min`,
`contradiction_watch_score`, `contradiction_block_score`,
`publish_probability_floor`, `publish_probability_ceiling`, `maximum_records`,
`maximum_requirements`, `maximum_requirement_memberships`,
`maximum_witness_edges`, `requirements`, `paper_only`, `report_only`, and
`readonly`.

The input field map contains exactly `evaluated_at`, `records`,
`current_revisions`, `paper_only`, `report_only`, and `readonly`. Every record
element contains exactly `source_lineage`, `capture`, `evidence_revision`,
`assessment_revision`, `paper_only`, `report_only`, and `readonly`.

The result field map contains exactly `evaluated_at`, `config_version`,
`config_digest`, `status`, `diagnostic_record_count`,
`arithmetic_record_count`, `requested_weight_total`,
`independence_allocated_weight_total`, `effective_weight_total`,
`arithmetic_probability_yes`, `publishable_probability_yes`, `contradiction`,
`requirement_coverage`, `diagnostics`, `reason_codes`, `paper_only`,
`report_only`, `readonly`, and, only in the full result payload, `core_digest`.
The core result field map omits only `core_digest`; it does not replace it with
null or a sentinel. Every diagnostic direct field map includes its exact
`captured_at`; omitting that field violates both the diagnostic type and the
closed core/full payload contract.

Every nested public dataclass is encoded as its direct field map using exactly
the field names documented under Core Model. Nested maps have no schema
wrapper, type tag, class name, omission rule, or extension keys. All fields,
including every hard flag, are always present. Tuples become arrays, optional
values become null, and mapping keys remain exact and closed. The four outer
envelopes above are the only schema wrappers.

Canonical encoding rules are:

- emitted dictionaries contain exactly the closed envelope and direct-map keys
  above, and every public
  codec function rejects a value whose exact dataclass type is wrong;
- semantic sets and records are emitted in their specified canonical order;
- Decimal values are strings with exactly six fractional digits and no
  exponent;
- all zero Decimal values encode as `"0.000000"`;
- UTC datetimes use exactly `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`;
- tuples encode as arrays;
- discrete exact ints encode as JSON integers;
- booleans and `None` encode as JSON booleans and null;
- no float, NaN, infinity, bytes, set, arbitrary object, or non-string mapping
  key is encodable;
- canonical bytes use UTF-8 JSON with `allow_nan=False`, `ensure_ascii=True`,
  `sort_keys=True`, and `separators=(",", ":")`.

`canonical_config_bytes` is the canonical JSON byte encoding of the entire
config payload envelope, including `schema_version` and the `config` wrapper.
The configuration digest is lowercase SHA-256 over:

```text
b"pal.team_evidence_aggregation.config.v1\x00" + canonical_config_bytes
```

`canonical_core_bytes` is the canonical JSON byte encoding of the entire core
payload envelope, including `schema_version` and the `result` wrapper. The core
digest is lowercase SHA-256 over:

```text
b"pal.team_evidence_aggregation.core.v1\x00" + canonical_core_bytes
```

The full aggregation payload uses its distinct result schema version and
rematerializes the complete direct result map with the validated `core_digest`;
it does not mutate or extend a returned core-payload dictionary in place.
`team_evidence_aggregation_payload(result)` first validates that digest and
raises `ValueError` rather than emitting a payload when it is invalid. The
result's `config_digest` binds the exact config payload envelope, not merely
`config_version`.

`validate_team_evidence_aggregation_core_digest` returns `None` when the
stored digest exactly matches the recomputed digest and raises `ValueError` on
any mismatch. It never returns a boolean or treats mismatch as a false-valued
normal result.

The codec owns no decoder, deserializer, legacy mode, legacy omission rule, or
packet projection in Node 2. Node 3 may use these canonical fragments while
owning the complete replay envelope and exact compatibility projections.

## End-To-End Data Flow

The Node 2C builder performs this exact sequence:

1. Call `select_team_evidence_canonical_capture_records` and
   `select_team_evidence_canonical_current_records` with the exact input and
   config. Their shared Node 2A private path performs complete input-contract
   validation and returns both exact canonical identity sets. Node 2A owns exact
   types and hard flags, canonical scalar values, tuple uniqueness, global
   functional dependencies, closed linear revision chains, local time order,
   raw-input resource ceilings, exact current-selection validation, and
   evidence-global canonical-capture selection.
2. Canonically sort records and current selections.
3. Materialize the exact canonical-capture and canonical-current identity sets
   from the selectors' returned records, each keyed only by the four-field
   `(source_lineage_id, capture_id, evidence_revision_id,
   assessment_revision_id)` identity. Derive the separate selected-current
   pair set only by projecting the canonical-current set to
   `(source_lineage_id, evidence_revision_id, assessment_revision_id)`; do
   not retain `capture_id` in that pair-level set or substitute either
   projection for the five-field canonical record sort key. Do not duplicate
   capture-selection logic or infer chain tips or latest revisions.
4. Evaluate temporal deltas for every supplied input record and create one diagnostic
   work row per supplied record, including capture, evidence-revision, and
   assessment-revision availability at `evaluated_at`.
5. Set every diagnostic row's `canonical_capture` flag exclusively from the
   evidence-revision-global canonical-capture identity set returned by Node 2A;
   mark later recaptures according to disposition precedence.
6. Apply temporal eligibility only to canonical current captures; temporal
   values for all other rows remain diagnostic and cannot affect arithmetic.
7. Send only positive, current, canonical, effective, fresh, timely records
   whose capture, evidence revision, and assessment revision were all available
   at evaluation to two-stage allocation. That allocation applies the
   independence-key cap first and the correlation-key cap second; it never
   reverses, merges, or simultaneously optimizes those stages.
8. Apply stage-one `independence_cap_exhausted` and then stage-two
   `correlation_cap_exhausted` zero dispositions and finalize the arithmetic
   universe.
9. Call `build_team_evidence_requirement_coverage` with the exact allocation-
   input record tuple from step 7 and the complete canonical allocation tuple.
   The witness builder derives the positive-`effective_weight` included subset
   internally before candidate-edge construction and builds one global
   canonical requirement b-matching.
10. Compute weight totals and diagnostic arithmetic `P(YES)`.
11. Derive contradiction, requirement state, top-level status, and reason
    codes with blocked/watch/ready precedence.
12. Populate `publishable_probability_yes` only when ready, applying only the
    supplied publication bounds.
13. Compute the exact config digest. Instantiate one internal provisional
    exact-shape result with an all-zero syntactically valid digest, compute the
    core payload that excludes that field, replace it with the computed digest,
    run non-recursive internal field/digest invariants on the final frozen
    result, and return it. The provisional value is a local construction detail
    and never escapes the builder. The public rematerializing validator remains
    an independent caller-facing entry point over the shared private semantic
    materializer.

The same semantic records in any tuple order must produce equal typed results,
equal canonical payload bytes, and equal digests.

## Failure Handling

Public boundaries fail closed with `ValueError` carrying the field path and a
stable contract description. They do not silently drop, coerce, clip, repair,
or replace malformed input. Specifically:

- wrong exact classes, tuple types, hard flags, scalar types, formats, bounds,
  or resource ceilings fail before reduction;
- conflicting identity projections, digest aliases, missing predecessors,
  multiple roots, forks, merges, graph cycles, no-op revisions, decreasing
  revision audit times, invalid local availability order, invalid selection
  relationships, and duplicate record keys fail the whole call;
- current arithmetic evidence with a requirement absent from config fails
  instead of silently ignoring the requirement;
- noncanonical or forged allocation tuples, allocation/result identity
  mismatches, and witness-edge overflow fail before max-flow;
- Decimal `InvalidOperation`, `DivisionByZero`, or overflow is converted to a
  field-specific `ValueError`; no partial result is returned;
- canonical payload or digest mismatch fails validation;
- an empty but structurally valid input returns a typed blocked result with
  `no_arithmetic_evidence`; it is not an exception.

There is no retry, network fallback, DB fallback, latest-valid fallback, prior
forecast fallback, or default BTC configuration fallback in this core.

Node 2A public validation failures use canonical field or relationship paths
and stable contract descriptions. The Node 2B and Node 2C plans additionally
lock the spelling of the following literal public `ValueError` descriptions.
The two Node 2B catalogs are explicitly non-exhaustive: nested canonical
allocation recomputation may propagate any stable allocation-boundary error,
and canonical reconstruction may map a malformed nested field to its stable
enclosing path. Listing a description here freezes its spelling; omission does
not authorize an unstable exception or a value-dependent message. `index`,
`field_path`, and `resource_field` below are placeholders replaced only by the
canonical sorted index or canonical public field path/name; rejected values and
object representations are never interpolated.

Allocation failures include:

```text
records must be an exact tuple
config must be exactly TeamEvidenceAggregationConfig
config.maximum_records must be an exact int in 1..128
records exceeds config.maximum_records
records[index] must be exactly TeamEvidenceAggregationRecord
records contains duplicate canonical record key
field_path must preserve paper_only=True, report_only=True, readonly=True
field_path must be an exact finite canonical fixed-six Decimal in [0, 1]
records[index].assessment_revision.requested_weight must be an exact finite canonical fixed-six Decimal in (0, 1]
records[index].assessment_revision.independence_key must be an exact canonical identifier
records[index].assessment_revision.correlation_key must be an exact canonical identifier
```

Witness-specific failures include:

```text
allocation_input_records must be an exact tuple
allocations must be an exact tuple
config must be exactly TeamEvidenceAggregationConfig
config.resource_field must be an exact int within its implementation maximum
records exceeds config.maximum_records
allocation_input_records[index] must be exactly TeamEvidenceAggregationRecord
allocations[index] must be exactly TeamEvidenceWeightAllocation
config.requirements must contain exact unique requirements sorted by requirement_id
config.requirements[index] must be exactly TeamEvidenceRequirement
field_path must preserve paper_only=True, report_only=True, readonly=True
field_path must be canonical
repeated evidence revision identity must have one exact projection
repeated assessment revision identity must have one exact projection
distinct evidence revision requirement memberships exceed config.maximum_requirement_memberships or 1024
allocations must equal canonical recomputation for allocation_input_records
record/allocation join keys must match exactly
allocation.independence_key must match assessment_revision.independence_key
allocation.correlation_key must match assessment_revision.correlation_key
allocation.requested_weight must match assessment_revision.requested_weight
positive-effective requirement ID must exist in config.requirements
candidate witness edge count exceeds config.maximum_witness_edges
internal witness matching invariant failed
```

Moving validation earlier changes only deterministic precedence when one call
contains multiple independent defects; it does not create new public error
descriptions. A record-count overflow maps to `records exceeds
config.maximum_records`. An allocation length overflow or record/allocation
length mismatch maps to `allocations must equal canonical recomputation for
allocation_input_records` without invoking recomputation. An invalid or
oversized requirements envelope maps to the existing
`config.requirements must contain exact unique requirements sorted by
requirement_id` description. A non-tuple raw `requirement_ids` or malformed
Decimal/datetime/scalar representation maps to `field_path must be canonical`
at the enclosing config, requirement, record, or allocation path. A raw
`requirement_ids` tuple over either aggregate membership ceiling maps to the
existing distinct-membership overflow description. No preflight failure leaks
an implementation exception or interpolates the rejected value.

The Node 2C result validator uses exactly:

```text
result must be exactly TeamEvidenceAggregationResult
aggregation result must equal rematerialized result
```

## Security And Purity Controls

The design limits the core's data surface to canonical IDs, digests, typed
numbers, UTC times, and policy labels. It does not admit source URLs, raw source
text, raw rationale, prompts, SQL, DSNs, credentials, account identifiers, or
order data.

The five caller-supplied resource ceilings are mandatory and are themselves
bounded by the non-overridable implementation maxima in the configuration
contract. Node 2A checks record, requirement, and distinct-revision membership
counts before expensive graph validation where possible. Node 2B checks its
exact top-level types, hard config resources, all top-level tuple lengths, and
each raw membership-tuple length before element traversal or reconstruction;
it completes exact scalar preflight before constructors, generic equality,
sorting, hashing, or allocation callbacks. It then enforces the deduplicated
distinct-revision membership sum before allocation recomputation and counts
post-threshold candidate edges incrementally before residual-graph construction
or max-flow. These layered bounds constrain diagnostic, canonicalization,
force/forbid, and witness-graph work without embedding BTC evidence thresholds
in the generic core.

The whole-node scope test uses these exact normalized import allowlists; every
unlisted import is rejected:

```text
team_evidence_aggregation_types:
  __future__, collections, dataclasses, datetime, decimal, re, typing
team_evidence_aggregation_codec:
  __future__, dataclasses, datetime, decimal, hashlib, json, typing,
  polymarket_alpha_lab.team_evidence_aggregation_types
team_evidence_aggregation_temporal:
  __future__, datetime, decimal, typing,
  polymarket_alpha_lab.team_evidence_aggregation_types
team_evidence_aggregation_allocation:
  __future__, decimal, typing,
  polymarket_alpha_lab.team_evidence_aggregation_types
team_evidence_aggregation_witness:
  __future__, collections, datetime, decimal, typing,
  polymarket_alpha_lab.team_evidence_aggregation_types,
  polymarket_alpha_lab.team_evidence_aggregation_allocation
team_evidence_aggregation:
  __future__, dataclasses, decimal, typing,
  polymarket_alpha_lab.team_evidence_aggregation_types,
  polymarket_alpha_lab.team_evidence_aggregation_codec,
  polymarket_alpha_lab.team_evidence_aggregation_temporal,
  polymarket_alpha_lab.team_evidence_aggregation_allocation,
  polymarket_alpha_lab.team_evidence_aggregation_witness
```

Import normalization is exact and shared by the child-local and unified gates.
For `ast.Import`, each `alias.name` is compared as one absolute module name. For
`ast.ImportFrom`, `level` must equal zero, `module` must be nonempty, and that
absolute `module` value is compared; imported member names and local aliases do
not change the module name. Every relative import is rejected, including one
that would resolve to an otherwise allowed sibling module. The gates enforce
module allowlists, not a second undocumented member-name or alias allowlist.

Package-root, forecast-packet, DB-row, store, CLI, persistence, network,
filesystem, and process imports are forbidden.

Production code must not catch broad exceptions, execute callbacks from input,
use object repr in payloads, or emit input values in error messages beyond
canonical field names and IDs.

## Test Design

Tests are deterministic and use no DB, network, CLI, auth, account, wallet,
order, filesystem persistence, or ambient current time. No test weakens the
Phase 1 hard flags. The scope test may read the six checked-in production
source files solely for AST and physical-line-count inspection; it may not
create, change, or persist project data.

### Node 2A tests

`tests/test_team_evidence_aggregation_types.py` covers:

- every public type name and exact field type;
- frozen/slots/final behavior and exact-type rejection;
- exact tuple-only collections, canonical sorting, and duplicate rejection;
- canonical ID and strict lowercase SHA-256 validation;
- raw Decimal bounds before quantization, fixed-six normalization, unsigned
  zero, Decimal subclass rejection, and no float acceptance;
- aware datetime normalization and subclass/naive rejection;
- required config arguments and absence of policy defaults;
- local cross-layer relationships and hard flags;
- direct success/failure coverage for
  `validate_team_evidence_aggregation_input_contract`, including exact
  `None` return on success, implementation hard maxima, and raw
  record/requirement/membership ceiling enforcement;
- direct coverage for `select_team_evidence_canonical_current_records`,
  including its shared full-validation path, empty result, one exact result per
  current selection, canonical record-key order, evidence-global capture across
  assessments, missing canonical-combination rejection, and absence of
  temporal/latest/tip fallback;
- direct coverage for `select_team_evidence_canonical_capture_records`,
  including all represented evidence revisions, multiple assessments sharing
  one canonical capture, non-current revisions, exact identity membership,
  canonical record-key order, and agreement with the current-record selector;
- global functional dependencies and digest-alias rejection;
- exactly one closed linear evidence chain and assessment chain per lineage,
  with multiple roots, second roots, forks, merge attempts, cycles, and missing
  predecessors rejected;
- no-op rejection, including anchor-only, anchor-plus-requirements without new
  content, `recorded_at`-only, and `assessed_at`-only successors, plus
  nondecreasing predecessor audit times with graph ordering for equal times;
- earliest evidence capture at or before `recorded_at`, evidence `recorded_at`
  at or before each `assessed_at`, later recaptures remaining valid, and every
  selected pair requiring the evidence-global canonical-capture combination;
- at most one exact current pair per lineage, with two-pair rejection, an old
  exact pair remaining selectable while successors are present, and no latest
  or chain-tip fallback;
- raw `[0, 1]` contradiction-band validation followed by strict canonical
  `no_max < yes_min` validation, including raw strictly ordered pairs that
  collapse to equality after fixed-six quantization.

The same test module contains Node 2A's child-local AST/import/size gate. Before
Node 2A may be reviewed or pushed, that gate parses all three Node 2A production
modules, enforces their exact normalized import allowlists, exact literal
`__all__` tuples, individual physical-line ceilings, and the exact aggregate
Node 2A production ceiling of 1,700 lines. It also enforces the three Node 2A
test-file ceilings and their exact 1,950-line aggregate, and rejects the
forbidden surfaces listed under Security And Purity Controls. This child-local
gate supplements, but does not replace, Node 2C's later unified six-module
scope guard.

`tests/test_team_evidence_aggregation_codec.py` covers:

- the four exact closed envelopes, their literal schema versions and sole
  wrapper keys, every direct nested dataclass field map, mandatory hard flags,
  tuple arrays, optional nulls, and assertions that every emitted dictionary
  has exactly its closed envelope or direct-map key set with no omitted or
  additional output keys;
- six-place Decimal and microsecond UTC rendering;
- no numeric floats anywhere in payload trees;
- tuple permutation invariance for semantic sets and records;
- exact domain-separated config/core digest vectors whose canonical bytes
  include their complete outer envelopes;
- fresh dictionary return values and mutation isolation;
- wrong exact dataclass types and constructor-bypassed noncanonical field
  values rejected by canonical helpers;
- exact core-payload field coverage: every
  `TeamEvidenceAggregationResult` field except `core_digest` is present, and
  field deletion, addition, or tampering fails validation or changes the
  digest;
- core digest tampering detection, `None` on successful digest validation, and
  fail-closed full-payload construction for an invalid digest;
- absence of a legacy or Node 3 identifier projection.

`tests/test_team_evidence_aggregation_temporal.py` covers:

- exact freshness and lag formulas;
- exact integer/microsecond Decimal timedelta conversion and rejection by the
  scope gate of `timedelta.total_seconds()` and `datetime.timestamp()` calls;
- inclusive age and capture-lag boundaries;
- stale, future-anchor, future-capture, pre-anchor capture, and excessive-lag
  cases;
- exact capture, evidence-revision, and assessment-revision availability
  booleans at the evaluation boundary;
- timezone-offset equivalence and microsecond preservation;
- recapture never changing the freshness anchor or age;
- hostile ambient Decimal context producing identical output.

### Node 2B tests

`tests/test_team_evidence_aggregation_allocation.py` covers:

- uncapped groups;
- independence cap only, correlation cap only, and both caps;
- mandatory independence-then-correlation stage order;
- largest-remainder allocation with exact sum and lexical remainder ties;
- the one-micro-unit non-monotonic cap regression for requests
  `(0.000001, 0.000003, 0.000003)` at caps `0.000003` then `0.000004`, with
  exact group-total conservation at both caps;
- a cap smaller than the number of positive micro-weight records;
- zero stage-one and zero stage-two allocations;
- group-level cap flags on every row of an over-cap group, with equality false,
  independently of whether a particular row's weight changed;
- group invariants, no weight increase, and input permutation invariance;
- raw cap/weight bounds and exact type failures;
- hostile ambient Decimal context and no float path.

`tests/test_team_evidence_aggregation_witness.py` covers:

- threshold-inclusive candidate edges;
- evidence assignment capacity and per-requirement independence capacity;
- a greedy counterexample where only global matching satisfies all demand;
- partial coverage and explicit watch/blocked requirement policy;
- exactly one sorted coverage row per configured requirement, including exact
  zero-candidate rows that preserve all configured fields;
- blocked-before-watch objective priority and lexical tie-breaking across
  multiple severity-optimal matchings;
- input, requirement, and adjacency permutation invariance;
- exact allocation recomputation, record/allocation join and projection
  mismatch rejection, and edge-resource failure;
- resource-first precedence for exact top-level tuple/config types, all five
  hard config resources, bounded record/requirement/allocation lengths, and
  allocation-length equality before any element traversal or reconstruction;
- exact raw `requirement_ids` tuples and per-raw-tuple aggregate-ceiling
  preflight plus exact short-string members before record reconstruction,
  followed by the deduplicated
  distinct-revision aggregate check before allocation recomputation;
- Option B capacity separation: a raw requirement tuple may exceed
  `max_requirement_assignments_per_evidence` while remaining within membership
  ceilings, and that field limits only assignments from each exact record join;
- constructor-bypassed hostile scalar representations proving every exact
  Decimal is finite, fixed-six, and coefficient-bounded and every exact
  datetime uses the UTC singleton before constructors, generic `as_tuple`,
  timezone hooks, sorting/hashing, or monkeypatched downstream callbacks; the
  constructors then retain exact type/range/unsigned-zero validation and the
  literal stable `ValueError` mapping;
- config-resident requirement IDs enforced for positive-effective direct-call
  rows while zero-effective rows may retain retired requirement IDs; the
  separate production mapping remains deferred to Node 2C and its own tests;
- conflicting repeated evidence and assessment revision identities rejected
  before allocation recomputation, including assessment independence forks;
- a brute-force small-graph oracle.

The same test module contains Node 2B's child-local AST/import/size gate. Before
Node 2B may be reviewed or pushed, that gate parses both Node 2B production
modules, enforces their exact normalized import allowlists, exact literal
`__all__` tuples, individual physical-line ceilings, and the exact aggregate
Node 2B production ceiling of 1,100 lines. It also enforces the two Node 2B
test-file ceilings and their exact 1,500-line aggregate, and rejects the
forbidden surfaces listed under Security And Purity Controls. This child-local
gate supplements, but does not replace, Node 2C's later unified six-module
scope guard.

The oracle exhaustively enumerates bounded graphs up to three evidence nodes by
two requirement nodes across every adjacency matrix, evidence capacities one
and two, every independence-key partition, requirement demands one and two
within graph bounds, and every blocked/watch requirement assignment. For each
graph it enumerates all edge subsets, filters them by b-matching constraints,
and computes the severity-optimal
`(blocked_assignment_count, watch_assignment_count)` vector followed by the
lexicographically smallest edge set at that vector. Additional three-by-three
unit-capacity cases exercise wider lexical ties. The production solver must
match both the objective vector and exact edge set. The oracle is test-only and
cannot be imported by production code.

### Node 2C tests

`tests/test_team_evidence_aggregation.py` covers:

- end-to-end data flow and all thirteen dispositions;
- exactly one diagnostic row per supplied record;
- use of both Node 2A canonical-record selectors as the validation and identity
  boundary, including `canonical_capture=True` for canonical records of wholly
  non-current revisions, with no duplicated private capture-selection
  algorithm;
- pair-level `selected_current_revision=True` on canonical and duplicate
  captures of an exact selected revision pair, independently of the
  canonical-capture flag;
- exact old-pair current selection while successors are present, with no
  latest/timestamp/chain-tip/tuple-order fallback;
- end-to-end rejection assertions for malformed functional dependencies,
  closed chains, local time order, and two current pairs in one lineage, while
  decisive hostile graph and selection cases remain owned by Node 2A tests;
- anchor-only/no-op rejection and recapture invariants;
- earliest canonical capture and inability of recapture to repair a gate;
- later evidence-revision availability excluding a row from historical
  arithmetic and a future assessment never leaking into replay arithmetic;
- diagnostic versus arithmetic universe counts and weights;
- weighted `P(YES)` with one final quantization;
- no hidden prior, market term, selected-side complement, or log-odds path;
- configurable non-BTC publication bounds and unchanged arithmetic
  probability;
- contradiction none/watch/blocked threshold boundaries;
- requirement watch/block and strict `blocked > watch > ready` precedence;
- exact one-to-one result requirement-ID coverage, including zero-candidate
  rows, under the rematerializing result validator;
- complete exact lexically sorted reason-code tuples, not membership-only
  checks, for every mixed blocked/watch/no-arithmetic combination and every
  ready floor/ceiling-clamping combination;
- ready-only `publishable_probability_yes`;
- arithmetic probability retained for non-ready results;
- empty and all-excluded inputs;
- result rematerialization and core/config digest tamper rejection;
- complete tuple permutation and hostile Decimal-context invariance.

`tests/test_team_evidence_aggregation_scope.py` is one AST-based scope test
module spanning all six production files and one physical-size gate spanning
all six production and seven test files. It must:

- parse every production file with `ast`, enforce every production/test
  physical-line ceiling below, and enforce the 3,500-production-line and
  4,850-test-line aggregate ceilings;
- enforce an explicit per-file allowlist of exact normalized imports across all
  six modules and reject package-root, forecast-packet, DB-row, store, CLI,
  persistence, network, filesystem, and process imports;
- reject imports or references for DB, Supabase, SQL, filesystem I/O, network,
  HTTP, socket, CLI, environment, subprocess, auth, account, credential,
  wallet, signing, order, execution, and trading surfaces;
- reject `open`, `print`, `input`, `eval`, `exec`, `compile`, dynamic import,
  `datetime.now`, `datetime.utcnow`, randomness, and built-in `hash` calls;
- reject `timedelta.total_seconds()` and `datetime.timestamp()` calls;
- reject float literals, `float(...)`, and float annotations;
- verify config policy fields have no defaults and no BTC/2%-98% constants are
  embedded in production AST;
- verify the five caller resource ceilings cannot exceed their exact
  non-exported implementation maxima;
- verify all public dataclasses are frozen, slotted, final, and preserve all
  three hard flags;
- verify each module's literal `__all__` tuple equals its complete exact public
  surface above, including order, with no missing, extra, or duplicate names;
- verify no Node 2 public name is exported from
  `polymarket_alpha_lab.__init__`;
- verify no `tea:v1`, `tfr:v1`, `tfe:v1`, forecast-packet construction,
  legacy projection, or persistence API appears in Node 2.

AST inspection is primary. A narrow source-text scan may supplement it for
unsafe import/module tokens, but substring checks alone are not the purity
gate because identifiers such as `correlation_group_weight_cap` must not fail
on incidental text.

## Size Ceilings

Ceilings count physical lines using `len(path.read_text().splitlines())`, so
they are objective and enforced by the Node 2C scope test.

| File | Maximum lines |
| --- | ---: |
| `team_evidence_aggregation_types.py` | 900 |
| `team_evidence_aggregation_codec.py` | 500 |
| `team_evidence_aggregation_temporal.py` | 300 |
| `team_evidence_aggregation_allocation.py` | 450 |
| `team_evidence_aggregation_witness.py` | 650 |
| `team_evidence_aggregation.py` | 700 |
| `test_team_evidence_aggregation_types.py` | 900 |
| `test_team_evidence_aggregation_codec.py` | 600 |
| `test_team_evidence_aggregation_temporal.py` | 450 |
| `test_team_evidence_aggregation_allocation.py` | 600 |
| `test_team_evidence_aggregation_witness.py` | 900 |
| `test_team_evidence_aggregation.py` | 900 |
| `test_team_evidence_aggregation_scope.py` | 500 |

Production Node 2 code must also stay at or below 3,500 total physical lines.
Tests must stay at or below 4,850 total physical lines. Comments and blank lines
count. A child that cannot meet its ceiling must stop for a reviewed contract
split; it must not add an unapproved helper module or compress logic into
unreadable one-liners.

## Child Acceptance Gates

Each child node runs its focused tests, then the repository quality gates:

```bash
# Node 2A
.venv/bin/python -m pytest -q \
  tests/test_team_evidence_aggregation_types.py \
  tests/test_team_evidence_aggregation_codec.py \
  tests/test_team_evidence_aggregation_temporal.py

# Node 2B
.venv/bin/python -m pytest -q \
  tests/test_team_evidence_aggregation_allocation.py \
  tests/test_team_evidence_aggregation_witness.py

# Node 2C
.venv/bin/python -m pytest -q \
  tests/test_team_evidence_aggregation.py \
  tests/test_team_evidence_aggregation_scope.py

.venv/bin/python -m compileall -q src tests
.venv/bin/python -m pytest -q
codegraph sync .
git diff --check
```

Each child also runs the active Phase 1 readonly-boundary, local-Supabase-only,
intended-path, and high-confidence secret scans from
`docs/quality/phase-1-development-node-quality-gates.md`. The local-Supabase
gate is satisfied by proving this pure node performs no project-data
persistence, not by opening a database.

Node 2A's focused test run must execute its child-local three-module AST/import/
size checks from `test_team_evidence_aggregation_types.py`. Node 2B's focused
test run must execute its child-local two-module checks from
`test_team_evidence_aggregation_witness.py`. Node 2C retains and executes the
final unified six-module/package-root scope guard. A child cannot defer a
local import, export, forbidden-surface, or physical-line failure to Node 2C.

The July 2026 child plans record the review procedure used for their
historical receipts. Follow [Native Codex Subagent Routing](../../../AGENTS.md#native-codex-subagent-routing)
for a current child review. The fresh, independent reviewer inspects the
exact child allowlist, reads only, keeps fast mode off, and must give the
final line `VERDICT: PASS` to pass. Missing or alternate verdicts fail the
gate. If no eligible reviewer can be dispatched, the gate is blocked.
A dependency still requires passing focused tests, full suite, CodeGraph
sync, static gates, diff hygiene, review, focused commit, and non-force push.

## Resolved Design Decisions

The following decisions are fixed:

- **Probability orientation:** every input and output probability is canonical
  `P(YES)`.
- **Policy injection:** all thresholds, caps, requirements, resource limits,
  and publication bounds are required immutable config; BTC values arrive only
  in the later BTC policy node.
- **Revision choice:** callers select exact current revision IDs/digests; the
  core has no latest fallback.
- **Recapture:** earliest capture is canonical, later recaptures are diagnostic
  duplicates, and no recapture refreshes or repairs evidence.
- **Freshness:** trusted `freshness_anchor_at` controls age; capture lag only
  excludes.
- **Diagnostics:** every accepted record remains visible with exactly one
  disposition.
- **Arithmetic:** only current, canonical evidence whose capture, evidence
  revision, and assessment revision are all available at evaluation, and that
  is fresh, timely, positive-weight, and surviving both caps, contributes.
- **Pooling:** probability is an exact capped weighted arithmetic mean, with no
  hidden prior or float/log-odds transform.
- **Caps:** deterministic largest-remainder independence caps run before
  deterministic largest-remainder correlation caps.
- **Witnesses:** one global severity-prioritized maximum b-matching is used,
  with canonical lexical tie-break and a brute-force test oracle.
- **Contradiction:** opposing configured probability bands are weighted by
  effective evidence and classified by supplied thresholds.
- **Status:** `blocked > watch > ready`; only ready exposes a publishable
  probability, while non-ready results retain diagnostic arithmetic `P(YES)`.
- **Digest ownership:** Node 2 owns canonical config, input, and result fragments,
  the config digest, and the core-result digest; Node 3 alone owns complete
  scope/replay identity, the canonical accepted/rejected evaluator receipt
  list, and `tea`/`tfr`/`tfe` IDs. Rejected receipts never enter Node 2 records.
- **Persistence:** Node 2 performs no project-data persistence. Any later node
  persisting derived data may use only this host's local Supabase/Postgres,
  with no file, cache, journal, alternate database, hosted service, or
  generic-store fallback. Every raw DSN must pass through
  `validate_local_postgres_dsn` before it is used to open a connection,
  construct a psycopg wrapper, or reach any adapter, repository, or store.
- **Package surface:** no package-root export is added until a later reviewed
  node explicitly owns it.

No policy value or implementation choice is left for Nodes 2A, 2B, or 2C to
infer.
