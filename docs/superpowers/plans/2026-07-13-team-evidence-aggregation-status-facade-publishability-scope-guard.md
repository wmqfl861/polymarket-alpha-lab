# Team Evidence Aggregation Status Facade, Publishability, And Scope Guard Implementation Plan

This July 2026 plan retains its original review instructions for receipt
and implementation history. For a current review, follow
[Native Codex Subagent Routing](../../../AGENTS.md#native-codex-subagent-routing).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pure Node 2C reducer that composes the reviewed Node 2A and Node 2B contracts into deterministic diagnostics, capped arithmetic `P(YES)`, requirement coverage, contradiction, readiness, reasons, ready-only publication, and tamper-evident result validation, then enforce the complete six-module Node 2 boundary with one unified scope guard.

**Architecture:** `team_evidence_aggregation.py` enters through both Node 2A canonical-record selectors, evaluates every supplied input record, passes the exact eligible tuple through Node 2B allocation and witness APIs, and materializes one immutable `TeamEvidenceAggregationResult`. The public builder and validator share one private semantic materializer; the validator rematerializes from the exact input and config and compares every field, while the codec remains the sole owner of config/core digest encoding. `tests/test_team_evidence_aggregation_scope.py` parses all six production modules and the package root, enforces the approved module-import/export and forbidden-surface contract, and applies every individual and aggregate physical-line ceiling.

**Tech Stack:** Python 3.12, exact frozen/slotted dataclasses from Node 2A, fixed-six `Decimal` under an isolated `Context(prec=64, rounding=ROUND_HALF_EVEN)`, Node 2A canonical selectors and temporal API, Node 2B largest-remainder allocation and canonical global witness matching, canonical SHA-256 codec helpers, pytest, Python `ast`, CodeGraph, git, and local Claude Code `claude-opus-4-8` at effort `max`.

## Global Constraints

- Preserve the approved design in `docs/superpowers/specs/2026-07-13-team-evidence-aggregation-core-design.md` exactly. This child resolves implementation mechanics only; it does not revise policy or contracts.
- Every Node 2 value remains `paper_only=True`, `report_only=True`, and `readonly=True`. No hard flag may be weakened, inferred, or caller-overridden.
- Every probability is canonical `P(YES)`. There is no selected-side complement, `P(NO)` alias, hidden prior, market-price term, log-odds pooling, float path, or BTC-specific policy.
- This child is pure over explicit immutable caller values. It performs no database access, Supabase/Postgres access, persistence, filesystem data access, network access, CLI parsing, environment access, process execution, logging, ambient clock read, randomness, or built-in `hash()` use.
- Future project-data persistence derived from this result is local Supabase/Postgres only. In every future persistence node, each raw DSN from environment, config, CLI plumbing, fixture, or helper construction must be passed to `validate_local_postgres_dsn` before constructing a connection, psycopg wrapper, adapter, repository, or store. Node 2C neither accepts a DSN nor constructs any persistence surface.
- Add no live trading, authentication, hosted-account read, credential, private-key, wallet, signing, order submission, order cancellation, order replacement, execution, exchange mutation, sizing, or allocation-to-capital surface.
- Add no run identity, complete evaluation-scope provenance, accepted/rejected evaluator receipts, `tea:v1`, `tfr:v1`, `tfe:v1`, forecast/evidence IDs, packet construction, legacy projection, decoder, service, CLI, DB row, store, or package-root export.
- Node 3 alone will bind config/input/full-result fragments into complete replay scope. Node 2C's `core_digest` is not a run, forecast, evidence, deduplication, or complete replay identity.
- Use only the exact public Node 2A/2B interfaces fixed below. If either predecessor contract differs, stop and amend/re-review that predecessor; do not adapt Node 2C around a drifted interface.
- Production Decimal operations run only inside a local copy of `Context(prec=64, rounding=ROUND_HALF_EVEN)`. Do not read or mutate `decimal.getcontext()`, quantize products or running sums early, or create a float.
- Error messages are stable contract descriptions containing canonical field paths or IDs only. They do not interpolate raw payloads, object representations, source text, rationale text, DSNs, or secret-bearing values.
- No dependency or `pyproject.toml` change is permitted.
- Fast mode is forbidden for implementation and review.

## Exact Sorted Implementation Allowlist

Only these repository paths may change in Node 2C, in this literal `LC_ALL=C` order:

```text
src/polymarket_alpha_lab/team_evidence_aggregation.py
tests/test_team_evidence_aggregation.py
tests/test_team_evidence_aggregation_scope.py
```

Use this exact shell array in every staging/range gate:

```bash
NODE_PATHS=(
  src/polymarket_alpha_lab/team_evidence_aggregation.py
  tests/test_team_evidence_aggregation.py
  tests/test_team_evidence_aggregation_scope.py
)
EXPECTED_NODE_PATHS="$(printf '%s\n' "${NODE_PATHS[@]}")"
test "$(printf '%s\n' "${NODE_PATHS[@]}" | LC_ALL=C sort)" = "$EXPECTED_NODE_PATHS"
```

No other source, test, package-root, configuration, migration, documentation, or generated path is part of the implementation range.

## Fixed Predecessor Interfaces

Node 2C consumes these exact public functions and no predecessor private helper:

```text
select_team_evidence_canonical_current_records(
    aggregation_input: TeamEvidenceAggregationInput,
    *,
    config: TeamEvidenceAggregationConfig,
) -> tuple[TeamEvidenceAggregationRecord, ...]

select_team_evidence_canonical_capture_records(
    aggregation_input: TeamEvidenceAggregationInput,
    *,
    config: TeamEvidenceAggregationConfig,
) -> tuple[TeamEvidenceAggregationRecord, ...]

assess_team_evidence_temporal(
    record: TeamEvidenceAggregationRecord,
    *,
    evaluated_at: datetime,
    config: TeamEvidenceAggregationConfig,
) -> TeamEvidenceTemporalAssessment

allocate_team_evidence_weights(
    records: tuple[TeamEvidenceAggregationRecord, ...],
    *,
    config: TeamEvidenceAggregationConfig,
) -> tuple[TeamEvidenceWeightAllocation, ...]

build_team_evidence_requirement_coverage(
    allocation_input_records: tuple[TeamEvidenceAggregationRecord, ...],
    allocations: tuple[TeamEvidenceWeightAllocation, ...],
    *,
    config: TeamEvidenceAggregationConfig,
) -> tuple[TeamEvidenceRequirementCoverage, ...]

team_evidence_aggregation_config_digest(
    config: TeamEvidenceAggregationConfig,
) -> str

team_evidence_aggregation_core_digest(
    result: TeamEvidenceAggregationResult,
) -> str

team_evidence_aggregation_payload(
    result: TeamEvidenceAggregationResult,
) -> dict[str, object]

validate_team_evidence_aggregation_core_digest(
    result: TeamEvidenceAggregationResult,
) -> None
```

Node 2C does not rematerialize Node 2B's matching algorithm. Every coverage
witness-order check, and any Node 2C helper, assertion, or review statement
that names the inherited candidate-edge sort key, must nevertheless spell out
all five fields in this exact order:
`(requirement_id, source_lineage_id, evidence_revision_id, assessment_revision_id, capture_id)`.
A partial or projected edge key is not a substitute for that ordering contract.

Node 2C produces exactly:

```text
build_team_evidence_aggregation_result(
    aggregation_input: TeamEvidenceAggregationInput,
    *,
    config: TeamEvidenceAggregationConfig,
) -> TeamEvidenceAggregationResult

validate_team_evidence_aggregation_result(
    result: TeamEvidenceAggregationResult,
    *,
    aggregation_input: TeamEvidenceAggregationInput,
    config: TeamEvidenceAggregationConfig,
) -> None
```

The production module's literal export tuple is exactly:

```python
__all__ = (
    "build_team_evidence_aggregation_result",
    "validate_team_evidence_aggregation_result",
)
```

The two selector-derived identity sets are fixed:

```python
CanonicalRecordIdentity = tuple[str, str, str, str]
# (source_lineage_id, capture_id, evidence_revision_id, assessment_revision_id)

SelectedRevisionPairIdentity = tuple[str, str, str]
# (source_lineage_id, evidence_revision_id, assessment_revision_id)
```

- The canonical-capture set contains the exact four-field identities returned by `select_team_evidence_canonical_capture_records`.
- The canonical-current set contains the exact four-field identities returned by `select_team_evidence_canonical_current_records`.
- The selected-current pair set is the canonical-current set projected to the three fields above. `selected_current_revision` is pair-level: every recapture of that exact selected pair is `True`, even when its four-field identity is absent from the canonical-current and canonical-capture sets.
- `canonical_capture` is four-field membership in the canonical-capture set, including canonical records belonging to wholly non-current revisions.

---

### Task 1: Validate Immutable Node 2A/2B Handoffs And Capture The Base

**Files:** No repository files change in this task.

**Interfaces:**
- Consumes: runtime environment values `NODE2A_PREREQ_SHA`, `NODE2B_PREREQ_SHA`, `NODE2A_PASS_RECEIPT_PATH`, `NODE2B_PASS_RECEIPT_PATH`, `NODE2A_PASS_RECEIPT_SHA256`, and `NODE2B_PASS_RECEIPT_SHA256`.
- Produces: canonical non-secret `INITIAL_NODE2A_RECEIPT_EVIDENCE` and `INITIAL_NODE2B_RECEIPT_EVIDENCE`, plus one immutable shell variable `NODE_BASE` captured from fetched `origin/main`, after proving both reviewed predecessor commits and receipts are exact, ancestral, present, and unchanged.

- [ ] **Step 1: Require and validate the runtime handoff values**

The Node 2A receipt has exactly these thirteen keys and values; the two SHA values are the runtime `NODE2A_PREREQ_SHA`:

```json
{
  "node": "team-evidence-aggregation-contracts-codec-temporal-eligibility",
  "head_sha": "$NODE2A_PREREQ_SHA",
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
  "pushed_remote_main_sha": "$NODE2A_PREREQ_SHA"
}
```

The Node 2B receipt has exactly these fifteen keys and values; `read_only` and `fast_mode` are JSON booleans, and both SHA values are the runtime `NODE2B_PREREQ_SHA`:

```json
{
  "node": "team-evidence-aggregation-independence-correlation-requirement-witness",
  "head_sha": "$NODE2B_PREREQ_SHA",
  "review_model": "claude-opus-4-8",
  "review_effort": "max",
  "read_only": true,
  "fast_mode": false,
  "review_exit_status": 0,
  "review_final_nonblank_line": "VERDICT: PASS",
  "focused_tests": "pass",
  "full_pytest": "pass",
  "compileall": "pass",
  "codegraph_sync": "pass",
  "static_gates": "pass",
  "clean_worktree": "pass",
  "pushed_remote_main_sha": "$NODE2B_PREREQ_SHA"
}
```

Run from the repository root in one persistent Bash gate session. The generic function below is the only permitted receipt-validation path. Do not precede or replace it with path-based `test`, `realpath`, `sha256sum`, `Path.read_text`, a second descriptor read, or a second JSON parser:

```bash
set -euo pipefail

: "${NODE2A_PREREQ_SHA:?NODE2A_PREREQ_SHA is required}"
: "${NODE2B_PREREQ_SHA:?NODE2B_PREREQ_SHA is required}"
: "${NODE2A_PASS_RECEIPT_PATH:?NODE2A_PASS_RECEIPT_PATH is required}"
: "${NODE2B_PASS_RECEIPT_PATH:?NODE2B_PASS_RECEIPT_PATH is required}"
: "${NODE2A_PASS_RECEIPT_SHA256:?NODE2A_PASS_RECEIPT_SHA256 is required}"
: "${NODE2B_PASS_RECEIPT_SHA256:?NODE2B_PASS_RECEIPT_SHA256 is required}"

REPO_ROOT="$(git rev-parse --show-toplevel)"
export REPO_ROOT NODE2A_PREREQ_SHA NODE2B_PREREQ_SHA
export NODE2A_PASS_RECEIPT_PATH NODE2B_PASS_RECEIPT_PATH
export NODE2A_PASS_RECEIPT_SHA256 NODE2B_PASS_RECEIPT_SHA256

validate_predecessor_pass_receipt() {
  local evidence_name="$1"
  local expected_node="$2"
  local prereq_sha="$3"
  local receipt_path="$4"
  local expected_sha256="$5"
  local schema_variant="$6"

  RECEIPT_EVIDENCE_NAME="$evidence_name" \
  RECEIPT_EXPECTED_NODE="$expected_node" \
  RECEIPT_PREREQ_SHA="$prereq_sha" \
  RECEIPT_PATH="$receipt_path" \
  RECEIPT_EXPECTED_SHA256="$expected_sha256" \
  RECEIPT_SCHEMA_VARIANT="$schema_variant" \
    .venv/bin/python - <<'PY'
import hashlib
import json
import os
import re
import stat


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


evidence_name = os.environ["RECEIPT_EVIDENCE_NAME"]
expected_node = os.environ["RECEIPT_EXPECTED_NODE"]
prereq_sha = os.environ["RECEIPT_PREREQ_SHA"]
receipt_path = os.environ["RECEIPT_PATH"]
expected_sha256 = os.environ["RECEIPT_EXPECTED_SHA256"]
schema_variant = os.environ["RECEIPT_SCHEMA_VARIANT"]
repo_root = os.path.realpath(os.environ["REPO_ROOT"])

if re.fullmatch(r"NODE2[AB]_RECEIPT_EVIDENCE", evidence_name) is None:
    raise SystemExit("PASS receipt evidence name is invalid")
if expected_node not in {
    "team-evidence-aggregation-contracts-codec-temporal-eligibility",
    "team-evidence-aggregation-independence-correlation-requirement-witness",
}:
    raise SystemExit("PASS receipt expected node is invalid")
if schema_variant not in {"node2a-13", "node2b-15"}:
    raise SystemExit("PASS receipt schema variant is invalid")
if re.fullmatch(r"[0-9a-f]{40}", prereq_sha) is None:
    raise SystemExit("PASS receipt prerequisite SHA must be lowercase hexadecimal")
if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
    raise SystemExit("PASS receipt SHA-256 must be lowercase hexadecimal")
if not os.path.isabs(receipt_path):
    raise SystemExit("PASS receipt path must be absolute")
if not os.path.isabs(repo_root) or not os.path.isdir(repo_root):
    raise SystemExit("REPO_ROOT must resolve to a directory")

try:
    descriptor = os.open(
        receipt_path,
        os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
    )
except OSError:
    raise SystemExit("PASS receipt descriptor open failed") from None

try:
    descriptor_stat = os.fstat(descriptor)
    if not stat.S_ISREG(descriptor_stat.st_mode):
        raise SystemExit("PASS receipt must be a regular file")
    if descriptor_stat.st_size <= 0 or descriptor_stat.st_size > 65_536:
        raise SystemExit("PASS receipt size is invalid")

    descriptor_link = os.readlink(f"/proc/self/fd/{descriptor}")
    if descriptor_link.endswith(" (deleted)"):
        raise SystemExit("PASS receipt descriptor is deleted")
    descriptor_path = os.path.realpath(descriptor_link)
    try:
        inside_repo = os.path.commonpath((repo_root, descriptor_path)) == repo_root
    except ValueError:
        raise SystemExit("PASS receipt descriptor path is invalid") from None
    if inside_repo:
        raise SystemExit("PASS receipt descriptor must resolve outside REPO_ROOT")

    receipt_bytes = os.read(descriptor, descriptor_stat.st_size + 1)
    if len(receipt_bytes) != descriptor_stat.st_size:
        raise SystemExit("PASS receipt changed while being read")
finally:
    os.close(descriptor)

actual_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
if actual_sha256 != expected_sha256:
    raise SystemExit("PASS receipt SHA-256 mismatch")

try:
    payload = json.loads(
        receipt_bytes.decode("utf-8"),
        object_pairs_hook=reject_duplicate_keys,
    )
except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
    raise SystemExit("PASS receipt JSON is invalid or has duplicate keys") from None

expected_payload: dict[str, object] = {
    "node": expected_node,
    "head_sha": prereq_sha,
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
    "pushed_remote_main_sha": prereq_sha,
}
if schema_variant == "node2a-13":
    if expected_node != "team-evidence-aggregation-contracts-codec-temporal-eligibility":
        raise SystemExit("Node 2A receipt descriptor is inconsistent")
else:
    if expected_node != "team-evidence-aggregation-independence-correlation-requirement-witness":
        raise SystemExit("Node 2B receipt descriptor is inconsistent")
    expected_payload["read_only"] = True
    expected_payload["fast_mode"] = False

if type(payload) is not dict or set(payload) != set(expected_payload):
    raise SystemExit("PASS receipt has the wrong closed shape")
if any(type(payload[key]) is not type(value) for key, value in expected_payload.items()):
    raise SystemExit("PASS receipt value types are invalid")
if payload != expected_payload:
    raise SystemExit("PASS receipt values do not match the required handoff")

evidence = {
    "prereq_sha": prereq_sha,
    "receipt_sha256": actual_sha256,
    "validated_payload": payload,
}
print(
    evidence_name
    + "="
    + json.dumps(evidence, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
)
PY
}
export -f validate_predecessor_pass_receipt
readonly -f validate_predecessor_pass_receipt

INITIAL_NODE2A_RECEIPT_EVIDENCE="$(
  validate_predecessor_pass_receipt \
    NODE2A_RECEIPT_EVIDENCE \
    team-evidence-aggregation-contracts-codec-temporal-eligibility \
    "$NODE2A_PREREQ_SHA" \
    "$NODE2A_PASS_RECEIPT_PATH" \
    "$NODE2A_PASS_RECEIPT_SHA256" \
    node2a-13
)"
INITIAL_NODE2B_RECEIPT_EVIDENCE="$(
  validate_predecessor_pass_receipt \
    NODE2B_RECEIPT_EVIDENCE \
    team-evidence-aggregation-independence-correlation-requirement-witness \
    "$NODE2B_PREREQ_SHA" \
    "$NODE2B_PASS_RECEIPT_PATH" \
    "$NODE2B_PASS_RECEIPT_SHA256" \
    node2b-15
)"
test -n "$INITIAL_NODE2A_RECEIPT_EVIDENCE"
test -n "$INITIAL_NODE2B_RECEIPT_EVIDENCE"
export INITIAL_NODE2A_RECEIPT_EVIDENCE INITIAL_NODE2B_RECEIPT_EVIDENCE
```

Expected: every command exits `0`. For each predecessor, exactly one
`os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)` descriptor is
`fstat`-checked as a bounded nonempty regular file, so a FIFO cannot block before
the nonregular-file rejection. The descriptor is resolved through
`/proc/self/fd` outside `REPO_ROOT`, read exactly once, hashed over those same
bytes, parsed with duplicate-key rejection, and compared by exact key set,
exact base value types, and exact values. The canonical JSON evidence contains
no receipt path or free text and binds the node-specific name, prerequisite SHA,
receipt SHA-256, and validated closed payload. A path precheck, symlink,
in-repository target, nonregular/oversized file, partial read, hash mismatch,
duplicate/extra/missing key, wrong boolean type, wrong node/SHA, or non-PASS
value blocks Node 2C.

- [ ] **Step 2: Fetch `origin/main`, capture immutable `NODE_BASE`, and prove ancestry**

Continue in the same shell session:

```bash
COMMON_GIT_DIR="$(git rev-parse --git-common-dir)"
GIT_AUTH=(-c credential.helper= -c "credential.helper=store --file=$COMMON_GIT_DIR/github-credentials")
GIT_TERMINAL_PROMPT=0 git "${GIT_AUTH[@]}" fetch --no-tags origin \
  refs/heads/main:refs/remotes/origin/main

NODE_BASE="$(git rev-parse refs/remotes/origin/main)"
readonly NODE_BASE
export NODE_BASE
test "$(git rev-parse HEAD)" = "$NODE_BASE"
test "$(git rev-parse refs/remotes/origin/main)" = "$NODE_BASE"
git merge-base --is-ancestor "$NODE2A_PREREQ_SHA" "$NODE2B_PREREQ_SHA"
git merge-base --is-ancestor "$NODE2B_PREREQ_SHA" "$NODE_BASE"
git merge-base --is-ancestor "$NODE_BASE" HEAD
git cat-file -e "$NODE2A_PREREQ_SHA^{commit}"
git cat-file -e "$NODE2B_PREREQ_SHA^{commit}"

test -z "$(git status --porcelain --untracked-files=no)"
git diff --quiet
git diff --cached --quiet
test -z "$(git ls-files --others --exclude-standard -- \
  src/polymarket_alpha_lab/team_evidence_aggregation.py \
  tests/test_team_evidence_aggregation.py \
  tests/test_team_evidence_aggregation_scope.py)"

readonly REPO_ROOT NODE2A_PREREQ_SHA NODE2B_PREREQ_SHA
readonly NODE2A_PASS_RECEIPT_PATH NODE2B_PASS_RECEIPT_PATH
readonly NODE2A_PASS_RECEIPT_SHA256 NODE2B_PASS_RECEIPT_SHA256
readonly INITIAL_NODE2A_RECEIPT_EVIDENCE INITIAL_NODE2B_RECEIPT_EVIDENCE
```

Expected: `HEAD == origin/main == NODE_BASE`; Node 2A is an ancestor of Node 2B; Node 2B is an ancestor of `NODE_BASE`; the tracked worktree and index are clean; none of the three owned paths is an unrelated untracked file. `NODE_BASE` is readonly for this gate run and is never reassigned.

- [ ] **Step 3: Validate the exact predecessor path surfaces and immutability**

```bash
NODE2A_PATHS=(
  src/polymarket_alpha_lab/team_evidence_aggregation_codec.py
  src/polymarket_alpha_lab/team_evidence_aggregation_temporal.py
  src/polymarket_alpha_lab/team_evidence_aggregation_types.py
  tests/test_team_evidence_aggregation_codec.py
  tests/test_team_evidence_aggregation_temporal.py
  tests/test_team_evidence_aggregation_types.py
)
NODE2B_PATHS=(
  src/polymarket_alpha_lab/team_evidence_aggregation_allocation.py
  src/polymarket_alpha_lab/team_evidence_aggregation_witness.py
  tests/test_team_evidence_aggregation_allocation.py
  tests/test_team_evidence_aggregation_witness.py
)

test "$(printf '%s\n' "${NODE2A_PATHS[@]}" | LC_ALL=C sort)" = "$(printf '%s\n' "${NODE2A_PATHS[@]}")"
test "$(printf '%s\n' "${NODE2B_PATHS[@]}" | LC_ALL=C sort)" = "$(printf '%s\n' "${NODE2B_PATHS[@]}")"

for path in "${NODE2A_PATHS[@]}"; do
  git cat-file -e "$NODE2A_PREREQ_SHA:$path"
done
for path in "${NODE2B_PATHS[@]}"; do
  git cat-file -e "$NODE2B_PREREQ_SHA:$path"
done

git diff --quiet "$NODE2A_PREREQ_SHA..$NODE_BASE" -- "${NODE2A_PATHS[@]}"
git diff --quiet "$NODE2B_PREREQ_SHA..$NODE_BASE" -- "${NODE2B_PATHS[@]}"
```

Expected: every predecessor path exists at its reviewed commit and is byte-unchanged from that commit through `NODE_BASE`. Any drift requires predecessor amendment and a fresh PASS receipt; Node 2C does not proceed.

---

### Task 2: Drive The Reducer Through Both Selector Identity Sets And All Dispositions

**Files:**
- Create: `tests/test_team_evidence_aggregation.py`
- Create: `src/polymarket_alpha_lab/team_evidence_aggregation.py`

**Interfaces:**
- Consumes: all fixed predecessor interfaces above and exact Node 2A dataclasses.
- Produces: the private canonical reduction path, one diagnostic per supplied input record, exact allocation/witness handoff, all thirteen dispositions, arithmetic universes and totals, and the public builder signature.

- [ ] **Step 1: Add deterministic fixture builders before production code**

In `tests/test_team_evidence_aggregation.py`, import public types from their defining modules, never the package root. Use this fixed fixture policy:

```python
BASE_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
EVALUATED_AT = BASE_TIME + timedelta(seconds=100)


def d(value: str) -> Decimal:
    return Decimal(value)


def digest(label: str) -> str:
    return sha256(label.encode("ascii")).hexdigest()


BASE_CONFIG_VALUES: dict[str, object] = {
    "config_version": "generic-test-v1",
    "max_evidence_age_seconds": d("60.000000"),
    "max_capture_lag_seconds": d("20.000000"),
    "independence_group_weight_cap": d("1.000000"),
    "correlation_group_weight_cap": d("1.000000"),
    "max_requirement_assignments_per_evidence": 2,
    "contradiction_no_probability_max": d("0.250000"),
    "contradiction_yes_probability_min": d("0.750000"),
    "contradiction_watch_score": d("0.250000"),
    "contradiction_block_score": d("0.750000"),
    "publish_probability_floor": d("0.110000"),
    "publish_probability_ceiling": d("0.890000"),
    "maximum_records": 128,
    "maximum_requirements": 32,
    "maximum_requirement_memberships": 1024,
    "maximum_witness_edges": 256,
    "requirements": (),
}
```

Define these exact test helpers:

```python
def config(**changes: object) -> TeamEvidenceAggregationConfig:
    values = dict(BASE_CONFIG_VALUES)
    values.update(changes)
    return TeamEvidenceAggregationConfig(**values)


def root_record(
    stem: str,
    *,
    freshness_anchor_offset: int = 50,
    captured_offset: int = 55,
    recorded_offset: int = 60,
    assessed_offset: int = 65,
    probability_yes: Decimal = d("0.600000"),
    requested_weight: Decimal = d("0.200000"),
    requirement_ids: tuple[str, ...] = (),
    independence_key: str | None = None,
    correlation_key: str | None = None,
) -> TeamEvidenceAggregationRecord:
    source_lineage_id = f"lineage:{stem}"
    source_lineage_digest = digest(f"lineage-digest:{stem}")
    content_digest = digest(f"content:{stem}")
    evidence_revision_id = f"evidence:{stem}"
    evidence_revision_digest = digest(f"evidence-digest:{stem}")
    lineage = TeamEvidenceSourceLineage(
        source_lineage_id=source_lineage_id,
        source_lineage_digest=source_lineage_digest,
    )
    capture = TeamEvidenceCapture(
        capture_id=f"capture:{stem}",
        capture_digest=digest(f"capture-digest:{stem}"),
        source_lineage_id=source_lineage_id,
        source_lineage_digest=source_lineage_digest,
        content_digest=content_digest,
        captured_at=BASE_TIME + timedelta(seconds=captured_offset),
    )
    evidence_revision = TeamEvidenceRevision(
        evidence_revision_id=evidence_revision_id,
        evidence_revision_digest=evidence_revision_digest,
        previous_evidence_revision_id=None,
        previous_evidence_revision_digest=None,
        source_lineage_id=source_lineage_id,
        source_lineage_digest=source_lineage_digest,
        content_digest=content_digest,
        requirement_ids=requirement_ids,
        freshness_anchor_at=BASE_TIME + timedelta(seconds=freshness_anchor_offset),
        recorded_at=BASE_TIME + timedelta(seconds=recorded_offset),
    )
    assessment_revision = TeamEvidenceAssessmentRevision(
        assessment_revision_id=f"assessment:{stem}",
        assessment_revision_digest=digest(f"assessment-digest:{stem}"),
        previous_assessment_revision_id=None,
        previous_assessment_revision_digest=None,
        evidence_revision_id=evidence_revision_id,
        evidence_revision_digest=evidence_revision_digest,
        assessed_at=BASE_TIME + timedelta(seconds=assessed_offset),
        probability_yes=probability_yes,
        requested_weight=requested_weight,
        rationale_digest=digest(f"rationale:{stem}"),
        independence_key=independence_key or f"ind:{stem}",
        correlation_key=correlation_key or f"corr:{stem}",
    )
    return TeamEvidenceAggregationRecord(
        source_lineage=lineage,
        capture=capture,
        evidence_revision=evidence_revision,
        assessment_revision=assessment_revision,
    )


def recapture(
    record: TeamEvidenceAggregationRecord,
    *,
    stem: str,
    captured_offset: int,
) -> TeamEvidenceAggregationRecord:
    capture = TeamEvidenceCapture(
        capture_id=f"capture:{stem}",
        capture_digest=digest(f"capture-digest:{stem}"),
        source_lineage_id=record.capture.source_lineage_id,
        source_lineage_digest=record.capture.source_lineage_digest,
        content_digest=record.capture.content_digest,
        captured_at=BASE_TIME + timedelta(seconds=captured_offset),
    )
    return replace(record, capture=capture)


def selection(
    record: TeamEvidenceAggregationRecord,
) -> TeamEvidenceCurrentRevisionSelection:
    return TeamEvidenceCurrentRevisionSelection(
        evidence_revision_id=record.evidence_revision.evidence_revision_id,
        evidence_revision_digest=record.evidence_revision.evidence_revision_digest,
        assessment_revision_id=record.assessment_revision.assessment_revision_id,
        assessment_revision_digest=record.assessment_revision.assessment_revision_digest,
    )


def aggregation_input(
    records: tuple[TeamEvidenceAggregationRecord, ...],
    *,
    current_records: tuple[TeamEvidenceAggregationRecord, ...],
    evaluated_at: datetime = EVALUATED_AT,
) -> TeamEvidenceAggregationInput:
    return TeamEvidenceAggregationInput(
        evaluated_at=evaluated_at,
        records=records,
        current_revisions=tuple(selection(record) for record in current_records),
    )


def diagnostic_key(row: TeamEvidenceDiagnosticRow) -> tuple[object, ...]:
    return (
        row.assessment_revision_id,
        row.evidence_revision_id,
        row.captured_at,
        row.capture_id,
        row.source_lineage_id,
    )


def diagnostic_row_for_record(
    result: TeamEvidenceAggregationResult,
    record: TeamEvidenceAggregationRecord,
) -> TeamEvidenceDiagnosticRow:
    expected_identity = (
        record.source_lineage.source_lineage_id,
        record.capture.capture_id,
        record.evidence_revision.evidence_revision_id,
        record.assessment_revision.assessment_revision_id,
    )
    matches = tuple(
        row
        for row in result.diagnostics
        if (
            row.source_lineage_id,
            row.capture_id,
            row.evidence_revision_id,
            row.assessment_revision_id,
        )
        == expected_identity
    )
    assert len(matches) == 1
    return matches[0]
```

`recapture` changes only capture ID, capture digest, and `captured_at`; it preserves the exact lineage, evidence revision, and assessment revision objects. `aggregation_input` emits one exact selection per `current_records` item. All helpers rely on Node 2A constructors to canonicalize tuples and validate hard flags.

Expected: fixtures use conspicuously non-BTC publication bounds `0.110000` and `0.890000`, exact aware datetimes, exact `Decimal`, and no ambient time, DB, network, CLI, filesystem data, auth, account, wallet, order, or execution input.

- [ ] **Step 2: Write failing selector-boundary and diagnostic-universe tests**

Add the exact tests `test_builder_calls_both_canonical_selectors_with_exact_input_and_config`, `test_canonical_capture_is_true_for_a_wholly_noncurrent_revision`, `test_selected_current_revision_is_pair_level_across_recaptures`, and `test_every_supplied_record_has_exactly_one_sorted_diagnostic_row`. Their decisive assertions are:

```python
assert calls == [
    ("capture", aggregation_input_value, config_value),
    ("current", aggregation_input_value, config_value),
]
assert [(row.canonical_capture, row.selected_current_revision, row.disposition)] == [
    (True, False, "not_current_revision"),
]
assert [
    (
        row.capture_id,
        row.selected_current_revision,
        row.canonical_capture,
        row.disposition,
    )
    for row in result.diagnostics
] == [
    (canonical.capture.capture_id, True, True, "included"),
    (duplicate.capture.capture_id, True, False, "duplicate_capture"),
]
assert result.diagnostic_record_count == len(input_value.records)
assert len(result.diagnostics) == len(input_value.records)
assert tuple(diagnostic_key(row) for row in result.diagnostics) == tuple(
    sorted(diagnostic_key(row) for row in result.diagnostics)
)
```

The selector spy wrappers must call the real predecessor functions after recording `(selector_name, aggregation_input, config)`. This proves both public selectors remain the validation and identity boundary; do not mock their outputs into invented identities.

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_team_evidence_aggregation.py::test_builder_calls_both_canonical_selectors_with_exact_input_and_config \
  tests/test_team_evidence_aggregation.py::test_canonical_capture_is_true_for_a_wholly_noncurrent_revision \
  tests/test_team_evidence_aggregation.py::test_selected_current_revision_is_pair_level_across_recaptures \
  tests/test_team_evidence_aggregation.py::test_every_supplied_record_has_exactly_one_sorted_diagnostic_row
```

Expected: FAIL during collection because `polymarket_alpha_lab.team_evidence_aggregation` does not exist.

- [ ] **Step 3: Write failing disposition-precedence tests for all thirteen values**

Use one scenario per row in `test_preallocation_dispositions_follow_exact_precedence` so each failure identifies one contract. The parameter IDs and expected disposition tuple are exact:

```python
DISPOSITION_CASES = (
    ("not_current_revision", "not_current_revision"),
    ("duplicate_capture", "duplicate_capture"),
    ("freshness_anchor_after_evaluation", "freshness_anchor_after_evaluation"),
    ("capture_after_evaluation", "capture_after_evaluation"),
    ("evidence_revision_after_evaluation", "evidence_revision_after_evaluation"),
    ("assessment_revision_after_evaluation", "assessment_revision_after_evaluation"),
    ("stale", "stale"),
    ("capture_before_freshness_anchor", "capture_before_freshness_anchor"),
    ("capture_lag_exceeded", "capture_lag_exceeded"),
    ("zero_requested_weight", "zero_requested_weight"),
    ("included", "included"),
)
```

For every parameter row, build the single decisive scenario from the table below and assert:

```python
assert len(result.diagnostics) == 1
assert result.diagnostics[0].disposition == expected_disposition
```

Build the cases with these exact decisive values:

| Case | Exact decisive condition |
| --- | --- |
| `not_current_revision` | canonical root record, `current_records=()` |
| `duplicate_capture` | selected exact pair, later recapture at offset `56`; assert the canonical row separately remains `included` |
| `freshness_anchor_after_evaluation` | anchor `101`, capture `101`, recorded `101`, assessed `101`, evaluated `100` |
| `capture_after_evaluation` | anchor `90`, capture `101`, recorded `101`, assessed `101`, evaluated `100` |
| `evidence_revision_after_evaluation` | anchor `50`, capture `55`, recorded `101`, assessed `101`, evaluated `100` |
| `assessment_revision_after_evaluation` | anchor `50`, capture `55`, recorded `60`, assessed `101`, evaluated `100` |
| `stale` | anchor `39`, capture `40`, recorded `41`, assessed `42`, evaluated `100`, max age `60` |
| `capture_before_freshness_anchor` | anchor `50`, capture `49`, recorded `50`, assessed `51` |
| `capture_lag_exceeded` | anchor `50`, capture `71`, recorded `71`, assessed `72`, max lag `20` |
| `zero_requested_weight` | otherwise eligible with requested weight `0.000000` |
| `included` | the default root record |

Add `test_independence_cap_exhausted_is_distinct_from_zero_request` and `test_correlation_cap_exhausted_requires_positive_stage_one_weight` with these exact assertions:

```python
independence_exhausted = diagnostic_row_for_record(
    result,
    independence_exhausted_record,
)
assert independence_exhausted.requested_weight == d("0.000001")
assert independence_exhausted.independence_allocated_weight == d("0.000000")
assert independence_exhausted.effective_weight == d("0.000000")
assert independence_exhausted.disposition == "independence_cap_exhausted"

correlation_exhausted = diagnostic_row_for_record(
    result,
    correlation_exhausted_record,
)
assert correlation_exhausted.requested_weight > d("0.000000")
assert correlation_exhausted.independence_allocated_weight > d("0.000000")
assert correlation_exhausted.effective_weight == d("0.000000")
assert correlation_exhausted.disposition == "correlation_cap_exhausted"
```

For the independence fixture, bind the `z-independence-exhausted` record as
`independence_exhausted_record`; use stems `a-independence-kept` and
`z-independence-exhausted`, requested weight `0.000001` on both, shared
independence key `ind:shared`, distinct correlation keys, and independence cap
`0.000001`. For the correlation fixture, bind the `z-correlation-exhausted`
record as `correlation_exhausted_record`; use stems `a-correlation-kept` and
`z-correlation-exhausted`, requested weight `0.000001` on both, distinct
independence keys, shared correlation key `corr:shared`, independence cap
`1.000000`, and correlation cap `0.000001`. The exact assessment-ID-first
canonical key gives the `a-` row the one remainder micro-unit and the `z-` row
zero. Assert the companion row is `included` and the applicable group sum is
exactly `0.000001`.

Add one precedence regression with a later recapture that is also future/stale/zero-weight and assert only `duplicate_capture`; add one non-current future/stale record and assert only `not_current_revision`. This proves earlier dispositions dominate all later gates.

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_team_evidence_aggregation.py::test_preallocation_dispositions_follow_exact_precedence \
  tests/test_team_evidence_aggregation.py::test_independence_cap_exhausted_is_distinct_from_zero_request \
  tests/test_team_evidence_aggregation.py::test_correlation_cap_exhausted_requires_positive_stage_one_weight
```

Expected: FAIL because the builder and disposition pipeline are not implemented.

- [ ] **Step 4: Write failing allocation/witness data-flow and historical-selection tests**

Add the exact tests
`test_allocation_and_witness_receive_the_exact_same_candidate_tuple`,
`test_requirement_coverage_witnesses_preserve_exact_five_field_candidate_edge_order`,
`test_old_exact_pair_remains_current_while_successors_are_present`,
`test_recapture_cannot_repair_freshness_lag_or_availability`, and
`test_future_evidence_or_assessment_revision_never_leaks_into_historical_arithmetic`.
Their decisive assertions are:

```python
assert allocation_calls == [(expected_candidates, config_value)]
assert witness_calls == [(expected_candidates, canonical_allocations, config_value)]
assert selected_old_row.selected_current_revision is True
assert selected_old_row.disposition == "included"
assert successor_rows
assert all(row.disposition == "not_current_revision" for row in successor_rows)
assert canonical_row.disposition == "capture_lag_exceeded"
assert duplicate_row.disposition == "duplicate_capture"
assert result.arithmetic_record_count == 0
assert tuple(row.disposition for row in historical_result.diagnostics) == (
    "evidence_revision_after_evaluation",
    "assessment_revision_after_evaluation",
)
assert historical_result.arithmetic_record_count == 0
assert historical_result.effective_weight_total == d("0.000000")

witness_edge_keys = tuple(
    (
        coverage.requirement_id,
        witness.source_lineage_id,
        witness.evidence_revision_id,
        witness.assessment_revision_id,
        witness.capture_id,
    )
    for coverage in result.requirement_coverage
    for witness in coverage.witnesses
)
assert witness_edge_keys == tuple(sorted(witness_edge_keys))
```

`expected_candidates` is the canonical-record-key-sorted tuple of records that are selected-current by the three-field pair identity, canonical by the exact four-field capture identity, effective and available at all four temporal layers, fresh, timely, and strictly positive requested weight. It includes rows later exhausted by either cap. `canonical_allocations` is the complete tuple returned by the real allocation function, including zero-effective rows. The witness wrapper must receive both values unchanged and then invoke the real witness builder. The witness-order assertion uses the inherited exact five-field candidate-edge key `(requirement_id, source_lineage_id, evidence_revision_id, assessment_revision_id, capture_id)`; it does not replace the separate four-field allocation join identity.

Also add exact end-to-end rejection tests named:

```text
test_builder_rejects_conflicting_functional_dependencies_through_selectors
test_builder_rejects_nonclosed_revision_chains_through_selectors
test_builder_rejects_invalid_local_time_order_through_selectors
test_builder_rejects_two_current_pairs_in_one_lineage_through_selectors
test_builder_rejects_anchor_only_noop_successor_through_selectors
test_builder_rejects_recapture_that_changes_revision_owned_fields
```

Use `pytest.raises(ValueError, match=regex)` with these exact regex values, proving each exception occurs before allocation: `functional dependency`, `linear chain`, `canonical capture.*recorded_at`, `one current.*lineage`, `semantic field`, and `functional dependency`, in the test order above. Do not duplicate Node 2A graph or selection validation in Node 2C.

Run the named tests. Expected: FAIL until the reducer composes the predecessor APIs exactly.

- [ ] **Step 5: Implement the minimal canonical diagnostic/allocation/witness pipeline**

Create `src/polymarket_alpha_lab/team_evidence_aggregation.py` with only the approved import modules and literal `__all__`. Use these exact private keys and branch order:

```python
def _record_key(record: TeamEvidenceAggregationRecord) -> tuple[object, ...]:
    return (
        record.assessment_revision.assessment_revision_id,
        record.evidence_revision.evidence_revision_id,
        record.capture.captured_at,
        record.capture.capture_id,
        record.source_lineage.source_lineage_id,
    )


def _record_identity(
    record: TeamEvidenceAggregationRecord,
) -> tuple[str, str, str, str]:
    return (
        record.source_lineage.source_lineage_id,
        record.capture.capture_id,
        record.evidence_revision.evidence_revision_id,
        record.assessment_revision.assessment_revision_id,
    )


def _selected_pair_identity(
    record: TeamEvidenceAggregationRecord,
) -> tuple[str, str, str]:
    return (
        record.source_lineage.source_lineage_id,
        record.evidence_revision.evidence_revision_id,
        record.assessment_revision.assessment_revision_id,
    )
```

Define these child-private arithmetic constants and helper in
`team_evidence_aggregation.py`; they are not imported from a predecessor module
and are not public exports:

```python
_ZERO = Decimal("0.000000")
_SIX_PLACES = Decimal("0.000001")
_ARITHMETIC_CONTEXT = Context(prec=64, rounding=ROUND_HALF_EVEN)


def _quantize_once(value: Decimal) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError("aggregation arithmetic must be finite Decimal")
    try:
        with localcontext(_ARITHMETIC_CONTEXT):
            quantized = value.quantize(_SIX_PLACES)
    except DecimalException:
        raise ValueError("aggregation arithmetic must be quantizable") from None
    return _ZERO if quantized == _ZERO else quantized
```

Every product, running sum, division, ratio, comparison, and call to
`_quantize_once` executes under a local copy of `_ARITHMETIC_CONTEXT`; no code
reads or mutates the ambient Decimal context. The shared private materializer
then performs this exact sequence:

1. Call `select_team_evidence_canonical_capture_records(aggregation_input, config=config)`.
2. Call `select_team_evidence_canonical_current_records(aggregation_input, config=config)`.
3. Sort `aggregation_input.records` by `_record_key` and build both exact four-field sets plus the three-field selected-pair projection.
4. Call `assess_team_evidence_temporal` for every sorted record, including non-current and duplicate rows.
5. Assign each row's first applicable pre-allocation disposition in this exact order:

```python
if selected_pair_identity not in selected_pair_identities:
    disposition = "not_current_revision"
elif record_identity not in canonical_current_identities:
    disposition = "duplicate_capture"
elif not temporal.effective_at_evaluation:
    disposition = "freshness_anchor_after_evaluation"
elif not temporal.captured_at_evaluation:
    disposition = "capture_after_evaluation"
elif not temporal.evidence_revision_available_at_evaluation:
    disposition = "evidence_revision_after_evaluation"
elif not temporal.assessment_revision_available_at_evaluation:
    disposition = "assessment_revision_after_evaluation"
elif not temporal.fresh:
    disposition = "stale"
elif temporal.capture_lag_seconds < _ZERO:
    disposition = "capture_before_freshness_anchor"
elif not temporal.timely:
    disposition = "capture_lag_exceeded"
elif record.assessment_revision.requested_weight == _ZERO:
    disposition = "zero_requested_weight"
else:
    disposition = None
```

6. Pass the exact sorted `disposition is None` record tuple to `allocate_team_evidence_weights`.
7. Join every allocation by `(source_lineage_id, capture_id, evidence_revision_id, assessment_revision_id)`. Reject a missing, duplicate, extra, reordered, or projection-mismatched allocation with `ValueError`; do not silently repair it.
8. Finalize candidate dispositions: stage-one zero is `independence_cap_exhausted`; positive stage one plus stage-two zero is `correlation_cap_exhausted`; positive effective weight is `included`.
9. Call `build_team_evidence_requirement_coverage` with the exact allocation-input tuple and complete canonical allocation tuple, even when both are empty.
10. Build one `TeamEvidenceDiagnosticRow` per sorted input record. Pre-allocation exclusions have zero independence/effective weight. Allocation candidates expose the canonical allocated values. Every row preserves the capture's exact canonical `captured_at`, raw probability, raw requested weight, temporal deltas/availability, the three-field selected flag, and four-field canonical-capture flag. The diagnostic tuple is therefore locally ordered and verifiable by the complete canonical record key.

Totals are exact:

```text
diagnostic_record_count = len(all sorted records)
arithmetic_record_count = count(disposition == "included")
requested_weight_total = sum(requested weight over the allocation-input tuple)
independence_allocated_weight_total = sum(stage-one weight over all allocations)
effective_weight_total = sum(effective weight over included allocations)
```

Use local-context unquantized sums and fixed-six quantization only at the final total boundary. Canonicalize every zero to unsigned `Decimal("0.000000")`. Catch only `DecimalException` subclasses needed to convert invalid Decimal arithmetic into field-specific `ValueError`; do not catch `Exception` or `BaseException`.

At this point the materializer may also compute the final status fields exactly as Task 3 specifies; do not add a temporary public API, alternate materializer, or policy default. The public builder returns the materializer result directly.

- [ ] **Step 6: Run the focused reducer tests**

```bash
.venv/bin/python -m pytest -q tests/test_team_evidence_aggregation.py
```

Expected: selector, diagnostic, disposition, allocation/witness-flow, old-selection, recapture, temporal replay, and malformed-input tests pass. Probability/status tests added in Task 3 remain the next red step, not skipped or weakened.

---

### Task 3: Derive Probability, Contradiction, Status, Reasons, And Ready-Only Publication

**Files:**
- Modify: `tests/test_team_evidence_aggregation.py`
- Modify: `src/polymarket_alpha_lab/team_evidence_aggregation.py`

**Interfaces:**
- Consumes: exact included diagnostics, effective allocations, and requirement coverage from Task 2.
- Produces: exact weighted arithmetic `P(YES)`, contradiction result, strict readiness precedence, complete static reason tuples, and ready-only bounded publication.

- [ ] **Step 1: Write failing arithmetic and universe tests**

Add the exact tests `test_weighted_probability_uses_included_effective_weights_and_one_final_quantization`, `test_diagnostic_and_arithmetic_universes_have_exact_counts_and_weights`, and `test_probability_is_canonical_pyes_without_complement_prior_market_or_log_odds`. Their decisive assertions are:

```python
assert result.requested_weight_total == d("1.000000")
assert result.independence_allocated_weight_total == d("1.000000")
assert result.effective_weight_total == d("1.000000")
assert result.arithmetic_record_count == 2
assert result.arithmetic_probability_yes == d("0.620000")
assert mixed_result.diagnostic_record_count == 5
assert mixed_result.arithmetic_record_count == 2
assert tuple(row.disposition for row in mixed_result.diagnostics) == expected_dispositions
assert mixed_result.effective_weight_total == sum(
    (
        row.effective_weight
        for row in mixed_result.diagnostics
        if row.disposition == "included"
    ),
    d("0.000000"),
)
assert canonical_result.arithmetic_probability_yes == d("0.200000")
assert canonical_result.arithmetic_probability_yes != d("0.800000")
```

The `0.620000` vector is two included records: `P(YES)=0.200000` at effective weight `0.300000` and `P(YES)=0.800000` at effective weight `0.700000`. Add `test_weighted_probability_does_not_quantize_products_before_sum` with two included records, each having `P(YES)=0.000001` and effective weight `0.500000`; assert exact arithmetic probability `0.000001`. Independently quantizing each `0.0000005` product would incorrectly produce zero under HALF_EVEN, so this vector proves one final quantization. Add `test_hostile_ambient_decimal_context_preserves_result_payload_and_digest`; set ambient precision to `6`, rounding to `ROUND_UP`, and the `Inexact` trap to true inside `try/finally`, then assert the typed result, `team_evidence_aggregation_payload`, and `core_digest` equal their baseline values byte for byte.

Run the three tests. Expected: FAIL until arithmetic is derived from included rows.

- [ ] **Step 2: Write failing contradiction boundary tests**

Add `test_one_sided_support_has_no_contradiction_even_when_watch_threshold_is_zero`, parameterized `test_contradiction_thresholds_are_inclusive_and_block_is_checked_first`, and `test_neutral_weight_is_reported_but_does_not_dilute_opposing_support`. Use the exact boundary vectors and assertions:

```python
CONTRADICTION_BOUNDARY_CASES = (
    (d("0.700000"), d("0.100000"), d("0.250000"), "watch"),
    (d("0.500000"), d("0.300000"), d("0.750000"), "blocked"),
)

assert one_sided_result.contradiction == TeamEvidenceContradictionResult(
    yes_support_weight=d("0.700000"),
    no_support_weight=d("0.000000"),
    neutral_weight=d("0.300000"),
    contradiction_score=d("0.000000"),
    status="none",
)
assert boundary_result.contradiction.contradiction_score == expected_score
assert boundary_result.contradiction.status == expected_status
assert neutral_result.contradiction.neutral_weight == d("0.900000")
assert neutral_result.contradiction.contradiction_score == d("1.000000")
```

Use `P(YES)=0.750000` for inclusive yes support, `0.250000` for inclusive no support, and `0.500000` for neutral. Configure per-record cap groups so the asserted effective weights survive unchanged.

Expected: FAIL until contradiction uses `2 * min(Y, N) / (Y + N)`, quantized once, with the explicit both-sides check before thresholds.

- [ ] **Step 3: Write failing readiness, exact-reason, and publication tests**

Add exact one-to-one coverage validation and use this literal parameter value in `test_status_precedence_and_mixed_reason_tuples_are_exact`:

```python
STATUS_REASON_CASES = (
        (
            "no_arithmetic_plus_block_and_watch_requirements",
            "blocked",
            (
                "blocking_requirement_unmet",
                "no_arithmetic_evidence",
                "watch_requirement_unmet",
            ),
        ),
        (
            "contradiction_blocked_plus_watch_requirement",
            "blocked",
            ("contradiction_blocked", "watch_requirement_unmet"),
        ),
        (
            "blocking_requirement_plus_contradiction_watch",
            "blocked",
            ("blocking_requirement_unmet", "contradiction_watch"),
        ),
        (
            "watch_requirement_plus_contradiction_watch",
            "watch",
            ("contradiction_watch", "watch_requirement_unmet"),
        ),
)

assert result.status == expected_status
assert result.reason_codes == expected_reasons
assert result.publishable_probability_yes is None
```

Use this literal parameter value in `test_ready_publication_uses_only_supplied_nonbtc_bounds`:

```python
READY_PUBLICATION_CASES = (
        (
            d("0.050000"),
            d("0.110000"),
            ("aggregation_ready", "publish_probability_floor_applied"),
        ),
        (
            d("0.950000"),
            d("0.890000"),
            ("aggregation_ready", "publish_probability_ceiling_applied"),
        ),
        (d("0.110000"), d("0.110000"), ("aggregation_ready",)),
        (d("0.890000"), d("0.890000"), ("aggregation_ready",)),
        (d("0.500000"), d("0.500000"), ("aggregation_ready",)),
)

assert result.status == "ready"
assert result.arithmetic_probability_yes == probability
assert result.publishable_probability_yes == expected_publishable
assert result.reason_codes == expected_reasons
```

Add:

```text
test_nonready_result_retains_arithmetic_probability_but_never_publishes
test_empty_input_is_typed_blocked_no_arithmetic_result
test_all_excluded_input_is_blocked_without_erasing_diagnostics
test_result_contains_exactly_one_coverage_row_per_config_requirement
test_zero_candidate_requirement_preserves_every_configured_coverage_field
```

The empty-input assertions are exact: zero diagnostic/arithmetic counts, all three weight totals `0.000000`, both probabilities `None`, zero contradiction weights/score with status `none`, one coverage row per configured requirement, top-level status `blocked`, and reason tuple containing `no_arithmetic_evidence` plus each independently applicable static requirement reason.

Run these tests. Expected: FAIL until readiness and publication are derived.

- [ ] **Step 4: Implement exact probability, contradiction, status, and reasons**

Inside the private materializer, sort included record/allocation pairs by `_record_key` and compute under the isolated local context:

```text
numerator = sum(probability_yes * effective_weight without intermediate quantization)
denominator = sum(effective_weight without intermediate quantization)
arithmetic_probability_yes = None when denominator == 0
arithmetic_probability_yes = _quantize_once(numerator / denominator) otherwise
```

Partition included effective weights exactly:

```text
no: probability_yes <= config.contradiction_no_probability_max
yes: probability_yes >= config.contradiction_yes_probability_min
neutral: strict interior
```

If either opposing side is zero, contradiction score/status is `0.000000`/`none`. Otherwise compute `_quantize_once(2 * min(Y, N) / (Y + N))`, classify `blocked` first at `>= contradiction_block_score`, then `watch` at `>= contradiction_watch_score`, else `none`.

Derive top-level status in this exact precedence:

```python
blocked = (
    arithmetic_probability_yes is None
    or any(not row.satisfied and row.unmet_status == "blocked" for row in coverage)
    or contradiction.status == "blocked"
)
watch = (
    any(not row.satisfied and row.unmet_status == "watch" for row in coverage)
    or contradiction.status == "watch"
)
status = "blocked" if blocked else "watch" if watch else "ready"
```

Build a set from only these static codes, then emit `tuple(sorted(codes))`:

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

Add each applicable requirement and contradiction code independently, even when a blocker already determines status. Add `aggregation_ready` only when ready. For ready status, clamp publication to exact supplied bounds without changing arithmetic probability and add a floor/ceiling code only for a strict change. For watch/blocked, publication is always `None`.

- [ ] **Step 5: Run all reducer tests under normal and hostile Decimal contexts**

```bash
.venv/bin/python -m pytest -q tests/test_team_evidence_aggregation.py
```

Expected: every Task 2/3 test passes; no skip, xfail, relaxed membership-only reason assertion, or broad exception assertion remains.

---

### Task 4: Share One Private Materializer And Validate Config/Core Digests

**Files:**
- Modify: `tests/test_team_evidence_aggregation.py`
- Modify: `src/polymarket_alpha_lab/team_evidence_aggregation.py`

**Interfaces:**
- Consumes: `team_evidence_aggregation_config_digest`, `team_evidence_aggregation_core_digest`, and `validate_team_evidence_aggregation_core_digest` from the Node 2A codec.
- Produces: a builder and independent public validator over one private semantic materializer, with exact field equality and digest validation.

- [ ] **Step 1: Write failing rematerialization and tamper tests**

Add:

```python
def test_result_validator_returns_exact_none_for_builder_result() -> None:
    result = build_team_evidence_aggregation_result(input_value, config=config_value)

    assert validate_team_evidence_aggregation_result(
        result,
        aggregation_input=input_value,
        config=config_value,
    ) is None


@pytest.mark.parametrize(
    "field_change",
    (
        {"status": "watch"},
        {"diagnostic_record_count": 999},
        {"arithmetic_probability_yes": d("0.123456")},
        {"publishable_probability_yes": None},
        {"reason_codes": ("aggregation_ready", "contradiction_watch")},
        {"core_digest": "f" * 64},
        {"config_digest": "e" * 64},
    ),
)
def test_result_validator_rejects_every_tampered_top_level_field(
    field_change: dict[str, object],
) -> None:
    result = build_team_evidence_aggregation_result(input_value, config=config_value)
    tampered = replace(result, **field_change)
    with pytest.raises(ValueError, match="aggregation result must equal rematerialized result"):
        validate_team_evidence_aggregation_result(
            tampered,
            aggregation_input=input_value,
            config=config_value,
        )
```

Also add exact tests:

```text
test_result_validator_rejects_tampered_nested_contradiction
test_result_validator_rejects_tampered_diagnostic
test_result_validator_rejects_missing_extra_duplicate_or_reordered_requirement_coverage
test_result_validator_rejects_wrong_exact_result_type_and_constructor_bypass
test_result_validator_rejects_constructor_bypassed_snan_with_stable_mismatch_error
test_builder_config_digest_equals_exact_codec_digest
test_builder_core_digest_equals_exact_codec_digest
test_core_digest_changes_when_any_semantic_result_field_changes
test_semantic_tuple_permutations_produce_equal_results_payloads_and_digests
```

For coverage, compare the exact tuple of requirement IDs with `tuple(requirement.requirement_id for requirement in config.requirements)` and assert zero-candidate rows are present. The validator must reject forged/reordered coverage even if its top-level status and reasons look plausible. For the constructor-bypass regression, create an exact `TeamEvidenceAggregationResult` with `object.__new__`, copy every slot from a valid builder result with `object.__setattr__`, replace one Decimal total with `Decimal("sNaN")`, and require exact `ValueError("aggregation result must equal rematerialized result")`; no `decimal.InvalidOperation` may escape.

Run the named tests. Expected: FAIL until the public validator rematerializes through the shared private path and the builder fills both digests exactly.

- [ ] **Step 2: Implement the shared private materializer and non-recursive final checks**

Use one private function with this exact role:

```python
def _materialize_team_evidence_aggregation_result(
    aggregation_input: TeamEvidenceAggregationInput,
    *,
    config: TeamEvidenceAggregationConfig,
) -> TeamEvidenceAggregationResult:
    """Build the one canonical semantic result from exact input and config."""
```

The final construction sequence is exact:

1. Compute every semantic field, coverage tuple, diagnostic tuple, status, reason, and `config_digest=team_evidence_aggregation_config_digest(config)`.
2. Instantiate a local provisional exact `TeamEvidenceAggregationResult` with `core_digest="0" * 64`.
3. Compute `core_digest=team_evidence_aggregation_core_digest(provisional)`. The core payload omits only `core_digest`, so the provisional zero digest cannot affect the preimage.
4. Create the final frozen result with `dataclasses.replace(provisional, core_digest=core_digest)`.
5. Run a private non-rematerializing invariant checker over exact counts, totals, requirement-ID coverage, status/reasons/publication relationships, config digest equality, and `validate_team_evidence_aggregation_core_digest(final)`.
6. Return the final result. The provisional object never escapes.

Public functions are thin and exact:

```python
def build_team_evidence_aggregation_result(
    aggregation_input: TeamEvidenceAggregationInput,
    *,
    config: TeamEvidenceAggregationConfig,
) -> TeamEvidenceAggregationResult:
    return _materialize_team_evidence_aggregation_result(
        aggregation_input,
        config=config,
    )


def validate_team_evidence_aggregation_result(
    result: TeamEvidenceAggregationResult,
    *,
    aggregation_input: TeamEvidenceAggregationInput,
    config: TeamEvidenceAggregationConfig,
) -> None:
    if type(result) is not TeamEvidenceAggregationResult:
        raise ValueError("result must be exactly TeamEvidenceAggregationResult")
    try:
        _validate_materialized_result_invariants(result, config=config)
        validate_team_evidence_aggregation_core_digest(result)
    except (AttributeError, DecimalException, TypeError, ValueError):
        raise ValueError(
            "aggregation result must equal rematerialized result",
        ) from None
    expected = _materialize_team_evidence_aggregation_result(
        aggregation_input,
        config=config,
    )
    try:
        matches_expected = result == expected
    except DecimalException:
        raise ValueError(
            "aggregation result must equal rematerialized result",
        ) from None
    if not matches_expected:
        raise ValueError("aggregation result must equal rematerialized result")
```

The validator never calls the public builder, and the builder never calls the public validator. Neither implements a second reduction algorithm. The supplied exact result is canonically checked before equality so a constructor-bypassed missing field, wrong field type, invalid digest, or noncomparable Decimal cannot escape a non-contract exception. Exact dataclass equality then covers every nested field and the core digest.

- [ ] **Step 3: Run digest, validator, permutation, and full reducer tests**

```bash
.venv/bin/python -m pytest -q tests/test_team_evidence_aggregation.py
```

Expected: all tests pass; successful validation returns exact `None`; any semantic/config/core tampering fails closed; permutation and hostile-context results, canonical payloads, and digests are equal.

---

### Task 5: Add The Child-Local Unified Six-Module AST And Package-Root Guard

**Files:**
- Create: `tests/test_team_evidence_aggregation_scope.py`
- Verify only: all six Node 2 production modules, all seven Node 2 test modules, and `src/polymarket_alpha_lab/__init__.py`

**Interfaces:**
- Consumes: checked-in source text solely for AST and physical-line inspection.
- Produces: Node 2C's child-local unified import/export/forbidden-surface/size gate over the complete Node 2 implementation and package root.

- [ ] **Step 1: Write the exact production/test path and line-limit tables**

Use literal mappings with these entries:

```python
PRODUCTION_LINE_LIMITS = {
    "team_evidence_aggregation_types.py": 900,
    "team_evidence_aggregation_codec.py": 500,
    "team_evidence_aggregation_temporal.py": 300,
    "team_evidence_aggregation_allocation.py": 450,
    "team_evidence_aggregation_witness.py": 650,
    "team_evidence_aggregation.py": 700,
}
TEST_LINE_LIMITS = {
    "test_team_evidence_aggregation_types.py": 900,
    "test_team_evidence_aggregation_codec.py": 600,
    "test_team_evidence_aggregation_temporal.py": 450,
    "test_team_evidence_aggregation_allocation.py": 600,
    "test_team_evidence_aggregation_witness.py": 900,
    "test_team_evidence_aggregation.py": 900,
    "test_team_evidence_aggregation_scope.py": 500,
}
PRODUCTION_TOTAL_LINE_LIMIT = 3_500
TEST_TOTAL_LINE_LIMIT = 4_850
```

Count exactly with `len(path.read_text(encoding="utf-8").splitlines())`; comments and blank lines count. Assert every individual ceiling, production total `<= 3_500`, test total `<= 4_850`, and this scope file `<= 500`.

- [ ] **Step 2: Encode the exact normalized module allowlists**

Use this literal mapping:

```python
IMPORT_ALLOWLISTS = {
    "team_evidence_aggregation_types": {
        "__future__", "collections", "dataclasses", "datetime", "decimal", "re", "typing",
    },
    "team_evidence_aggregation_codec": {
        "__future__", "dataclasses", "datetime", "decimal", "hashlib", "json", "typing",
        "polymarket_alpha_lab.team_evidence_aggregation_types",
    },
    "team_evidence_aggregation_temporal": {
        "__future__", "datetime", "decimal", "typing",
        "polymarket_alpha_lab.team_evidence_aggregation_types",
    },
    "team_evidence_aggregation_allocation": {
        "__future__", "decimal", "typing",
        "polymarket_alpha_lab.team_evidence_aggregation_types",
    },
    "team_evidence_aggregation_witness": {
        "__future__", "collections", "datetime", "decimal", "typing",
        "polymarket_alpha_lab.team_evidence_aggregation_types",
        "polymarket_alpha_lab.team_evidence_aggregation_allocation",
    },
    "team_evidence_aggregation": {
        "__future__", "dataclasses", "decimal", "typing",
        "polymarket_alpha_lab.team_evidence_aggregation_types",
        "polymarket_alpha_lab.team_evidence_aggregation_codec",
        "polymarket_alpha_lab.team_evidence_aggregation_temporal",
        "polymarket_alpha_lab.team_evidence_aggregation_allocation",
        "polymarket_alpha_lab.team_evidence_aggregation_witness",
    },
}
```

Normalization is exact and module-only. Each `ast.Import` alias contributes
`alias.name`. Every `ast.ImportFrom` requires `level == 0` and a nonempty
`module`, then contributes that absolute `module`; imported member names and
local aliases do not change the normalized module. Reject every relative import
and every normalized module not in the consumer module's allowlist. Independently
reject wildcard syntax in every `ImportFrom`. The gate otherwise enforces no
second member-name or alias allowlist: it does not force sibling modules to use
`ImportFrom`, prohibit `asname`, or restrict an allowed module to an exact
imported-member set. A member is not rejected merely because its spelling begins
with `_`; this import-gate rule does not authorize Node 2C to consume a
predecessor private helper, which remains prohibited by the public-interface
boundary above. Imported members and local aliases remain subject to the
forbidden-name and forbidden-call scans specified below.

- [ ] **Step 3: Encode all six exact literal `__all__` tuples**

Use these exact tuples and order:

```python
PUBLIC_EXPORTS = {
    "team_evidence_aggregation_types": (
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
    ),
    "team_evidence_aggregation_codec": (
        "team_evidence_aggregation_config_payload",
        "team_evidence_aggregation_input_payload",
        "team_evidence_aggregation_config_digest",
        "team_evidence_aggregation_core_payload",
        "team_evidence_aggregation_payload",
        "team_evidence_aggregation_core_digest",
        "validate_team_evidence_aggregation_core_digest",
    ),
    "team_evidence_aggregation_temporal": (
        "assess_team_evidence_temporal",
    ),
    "team_evidence_aggregation_allocation": (
        "allocate_team_evidence_weights",
    ),
    "team_evidence_aggregation_witness": (
        "build_team_evidence_requirement_coverage",
    ),
    "team_evidence_aggregation": (
        "build_team_evidence_aggregation_result",
        "validate_team_evidence_aggregation_result",
    ),
}
```

AST assertions require one direct assignment to `__all__`, an `ast.Tuple` of
string constants, exact tuple equality including order, and no duplicates. Every
exported name must have exactly one definition among the direct children of
`ast.Module.body`, with its documented node kind: `ClassDef` for the sixteen
public types and `FunctionDef` for every public function. No import, assignment,
named-expression target, nested or async definition, or other binding may
satisfy or rebind an exported name; reject every missing, duplicate, rebound, or
wrong-kind export. Private graph, validation, apportionment, materialization,
and serialization helpers must remain absent from `__all__`.

- [ ] **Step 4: Write the unified AST, hard-maxima, and package-root tests**

Create these exact tests:

```text
test_node_2_unified_ast_import_export_forbidden_surface_and_line_size_gate
test_node_2_public_dataclasses_are_frozen_slotted_final_and_hard_flagged
test_node_2_config_policy_fields_have_no_defaults_and_hard_maxima_fail_closed
test_node_2_package_root_has_no_node_2_public_exports
test_node_2_has_no_outer_identity_packet_legacy_or_persistence_surface
```

The unified AST test must reject:

- unlisted and relative imports, `ImportFrom` nodes with an empty module, and
  wildcard imports;
- DB, Supabase, PostgreSQL, SQL, migration, persistence, store, filesystem, JSONL, CSV, cache, temporary-file, network, HTTP, socket, browser, scraper, public-client, external API, CLI, environment, subprocess, process, or logging imports/references;
- auth, hosted-account, credential, token, private-key, wallet, signing, order, sizing, allocation-to-capital, execution, exchange mutation, and live-trading surfaces, using token-aware AST name/attribute checks so legitimate `correlation_group_weight_cap` does not fail;
- calls to `open`, `print`, `input`, `eval`, `exec`, `compile`, `__import__`, dynamic import, `datetime.now`, `datetime.utcnow`, `timedelta.total_seconds`, `datetime.timestamp`, randomness, or built-in `hash`;
- float literals, `float(...)`, and float annotations;
- broad `except Exception`, broad `except BaseException`, callback invocation from input, or object `repr` in payload/error construction;
- embedded `btc`, `bitcoin`, `0.020000`, or `0.980000` production constants;
- `tea:v1`, `tfr:v1`, `tfe:v1`, forecast-packet construction, legacy projection, decoder, persistence API, or outer replay/run/forecast/evidence identity.

Table-driven snippets exercising the six production-module import gates must
prove the module-only boundary directly. An allowlisted absolute module imported
with either `Import` or `ImportFrom` keeps the same normalized module when benign
imported-member names or local aliases change. Exact-prefix extensions, relative
forms, empty `ImportFrom.module` values, and wildcards fail. The exact normalized
module `polymarket_alpha_lab` also fails because it is absent from every
production-module allowlist. Benign member and alias variants include a
leading-underscore member spelling and do not create a second allowlist, while
forbidden semantic identifiers in those same AST positions still fail the scans
below. These assertions do not govern imports in test modules and are separate
from the package-root `__init__.py` assertion below, which verifies that the
documented Node 2 public names are not re-exported there.

Forbidden-name matching is lexical, not substring-based. Normalize identifier
values from `ast.Name.id`, each `ast.Attribute.attr`,
`ast.FunctionDef.name`, `ast.AsyncFunctionDef.name`, `ast.ClassDef.name`,
every `ast.arg.arg`, each non-`None` `ast.keyword.arg`, string-valued
`ast.ExceptHandler.name`, and every `ast.Global.names`/`ast.Nonlocal.names`
entry, plus every `ast.alias.name` and non-`None` `ast.alias.asname`, into
snake-case and CamelCase components. The unified test also parses table-driven
synthetic snippets that place one forbidden identifier in every one of those AST
fields and requires the scanner to reject each snippet. Compare whole components
or an explicitly forbidden adjacent component sequence. The capital-allocation
prohibition matches only
an explicit `capital_allocation` or `allocation_to_capital` sequence;
standalone `allocation` remains valid evidence-aggregation terminology.
Normalized `ast.Import` and `ast.ImportFrom` module strings are governed only by
the exact module allowlist and must not be fed through the forbidden semantic
name scan after that check. For `ast.ImportFrom`, scan every `alias.name` member
and optional `alias.asname` separately as semantic identifiers; for
`ast.Import`, scan every optional `alias.asname` separately while retaining
`alias.name` as the allowlisted module string. Imported members and aliases also
remain inside the ordinary forbidden-call scan; the module-name exemption does
not exempt calls made through them. Consequently, the allowlisted sibling modules
`polymarket_alpha_lab.team_evidence_aggregation_types` and
`polymarket_alpha_lab.team_evidence_aggregation_allocation`, together with
legitimate domain names such as `TeamEvidenceWeightAllocation` and
`allocate_team_evidence_weights`, must pass without a false forbidden-name
finding. Table-driven failures must include a forbidden terminal call component
through both an allowlisted module alias and an imported-member alias, such as
`import datetime as clock; clock.now()` and
`from datetime import datetime as clock; clock.utcnow()`. The ordinary lexical
scan of `ast.Call` targets and their `Name`/`Attribute` components must reject
both without introducing an import-member allowlist: terminal call components
`now`, `utcnow`, `timestamp`, and `total_seconds` are forbidden regardless of
their qualifier.

The dataclass test parses every exported public class in the types module and requires `@final`, `@dataclass(frozen=True, slots=True)`, exact hard-flag fields with literal `True` defaults, and explicit non-subclassability. The config-default test inspects `TeamEvidenceAggregationConfig` and requires no defaults on any policy field; then constructs values above each hard maximum and asserts `ValueError` for `33`, `129`, `33`, `1025`, and `257` respectively, while the exact maxima `32`, `128`, `32`, `1024`, and `256` remain structurally admissible when all other invariants hold. No implementation-max name may appear in any `__all__`.

The package-root test parses `src/polymarket_alpha_lab/__init__.py` and asserts that no name in the union of `PUBLIC_EXPORTS.values()` is imported, assigned, or listed in package-root `__all__`. It does not edit the package root.

- [ ] **Step 5: Run the child-local unified scope gate and fix only owned files**

```bash
.venv/bin/python -m pytest -q \
  tests/test_team_evidence_aggregation_scope.py::test_node_2_unified_ast_import_export_forbidden_surface_and_line_size_gate \
  tests/test_team_evidence_aggregation_scope.py::test_node_2_public_dataclasses_are_frozen_slotted_final_and_hard_flagged \
  tests/test_team_evidence_aggregation_scope.py::test_node_2_config_policy_fields_have_no_defaults_and_hard_maxima_fail_closed \
  tests/test_team_evidence_aggregation_scope.py::test_node_2_package_root_has_no_node_2_public_exports \
  tests/test_team_evidence_aggregation_scope.py::test_node_2_has_no_outer_identity_packet_legacy_or_persistence_surface
```

Expected: all five tests pass. If a predecessor module violates its reviewed child-local contract, stop and amend/re-review that predecessor. Node 2C may only fix its own reducer or scope-test files and may not conceal predecessor drift.

- [ ] **Step 6: Run the exact Node 2C focused command**

```bash
.venv/bin/python -m pytest -q \
  tests/test_team_evidence_aggregation.py \
  tests/test_team_evidence_aggregation_scope.py
```

Expected: both files pass; the run executes reducer behavior plus the final unified six-module/package-root gate, with `team_evidence_aggregation_scope.py <= 500` lines and all seven Node 2 tests `<= 4,850` lines total.

---

### Task 6: Gate, Commit, Review The Full Range, And Publish Without Force

**Files:** Exactly the three paths in `NODE_PATHS`; no others.

**Interfaces:**
- Consumes: immutable `NODE_BASE`, validated predecessor environment values/receipts, completed Node 2C files, and all quality-gate evidence.
- Produces: an exact allowlisted commit range reviewed by local Claude Code and a race-checked non-force update of remote `main`.

- [ ] **Step 1: Run focused, compile, full, CodeGraph, and diff gates before staging**

```bash
set -euo pipefail
: "${NODE_BASE:?run Task 1 in this gate session first}"

.venv/bin/python -m pytest -q \
  tests/test_team_evidence_aggregation.py \
  tests/test_team_evidence_aggregation_scope.py
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m pytest --collect-only -q
.venv/bin/python -m pytest -q
codegraph sync .
git diff --check -- "${NODE_PATHS[@]}"
```

Expected: each command exits `0`; focused tests pass; compilation reports no syntax errors; collection succeeds; the full suite passes without live/network/account/wallet/order dependencies; CodeGraph sync succeeds; file-level diff hygiene is clean.

- [ ] **Step 2: Stage the literal allowlist and prove exact staged equality**

```bash
git add -- "${NODE_PATHS[@]}"
test "$(git diff --cached --name-only | LC_ALL=C sort)" = "$EXPECTED_NODE_PATHS"
test "$(git diff --cached --name-only)" = "$EXPECTED_NODE_PATHS"
git diff --cached --check
```

Expected: the staged path string equals the literal sorted allowlist byte for byte, both before and after sorting. No broad `git add` command is permitted.

- [ ] **Step 3: Run staged high-confidence secret and Phase 1 surface scans**

Run exactly this status-aware scan. The high-confidence scan is quiet and prints only an affected path, never a matched value:

```bash
set -euo pipefail
HIGH_CONFIDENCE_SECRET_PATTERN='(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(^|[^[:alnum:]_])sk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|postgres(ql)?://[^[:space:]/]+:[^[:space:]@]+@)'
SENSITIVE_FIELD_PATTERN='\b(api[_-]?key|secret|token|password|passwd|cookie|authorization|bearer|private[_ -]?key|seed phrase|mnemonic|wallet|account[_ -]?(id|address))\b'
READONLY_PATTERN='\b(live[_ -]?trading|order[_ -]?submission|submit[_ -]?order|cancel[_ -]?order|replace[_ -]?order|sign[_ -]?order|wallet|private[_ -]?key|hosted[_ -]?account|account[_ -]?authentication|exchange[_ -]?mutation|order[_ -]?mutation)\b'
PERSISTENCE_PATTERN='\b(sqlite|duckdb|redis|mongodb|sqlalchemy|jsonl|file[_ -]?backed|postgres|supabase|dsn|validate_local_postgres_dsn|migration|persistence|store)\b'

SENSITIVE_PATHS=()
READONLY_PATHS=()
PERSISTENCE_PATHS=()

for path in "${NODE_PATHS[@]}"; do
  ADDED_STAGED="$({
    git diff --cached --unified=0 -- "$path" |
      sed -n '/^+++ /d; /^+/s/^+//p'
  })"

  if rg -q "$HIGH_CONFIDENCE_SECRET_PATTERN" <<< "$ADDED_STAGED"; then
    RG_STATUS=0
  else
    RG_STATUS=$?
  fi
  case "$RG_STATUS" in
    0)
      printf 'high-confidence credential-shaped addition in %s\n' "$path" >&2
      exit 1
      ;;
    1)
      ;;
    *)
      printf 'high-confidence secret scan command failed for %s with rg status %s\n' "$path" "$RG_STATUS" >&2
      exit "$RG_STATUS"
      ;;
  esac

  for scan_name in sensitive readonly persistence; do
    case "$scan_name" in
      sensitive) pattern="$SENSITIVE_FIELD_PATTERN" ;;
      readonly) pattern="$READONLY_PATTERN" ;;
      persistence) pattern="$PERSISTENCE_PATTERN" ;;
    esac
    if rg -qi "$pattern" <<< "$ADDED_STAGED"; then
      RG_STATUS=0
    else
      RG_STATUS=$?
    fi
    case "$RG_STATUS" in
      0)
        case "$scan_name" in
          sensitive) SENSITIVE_PATHS+=("$path") ;;
          readonly) READONLY_PATHS+=("$path") ;;
          persistence) PERSISTENCE_PATHS+=("$path") ;;
        esac
        ;;
      1)
        ;;
      *)
        printf '%s scan command failed for %s with rg status %s\n' "$scan_name" "$path" "$RG_STATUS" >&2
        exit "$RG_STATUS"
        ;;
    esac
  done
done

test "$(printf '%s\n' "${SENSITIVE_PATHS[@]}" | sed '/^$/d' | LC_ALL=C sort)" = \
  'tests/test_team_evidence_aggregation_scope.py'
test "$(printf '%s\n' "${READONLY_PATHS[@]}" | sed '/^$/d' | LC_ALL=C sort)" = \
  'tests/test_team_evidence_aggregation_scope.py'
test "$(printf '%s\n' "${PERSISTENCE_PATHS[@]}" | sed '/^$/d' | LC_ALL=C sort)" = \
  'tests/test_team_evidence_aggregation_scope.py'
```

Exact `rg` semantics for every scan are mandatory: status `0` means a match and therefore either immediate failure (high-confidence secret) or explicit path classification (sensitive/readonly/persistence); status `1` means clean; any status greater than `1` is a command failure. The sole classified path is the AST scope test because it names forbidden surfaces only as negative assertions. A match in production or reducer behavior tests fails the exact classification equality. No unconditional success fallback may mask `rg`, `git diff`, or `sed` failure.

- [ ] **Step 4: Commit the complete initial implementation and prove the range manifest**

```bash
git commit -m "Add team evidence aggregation status facade"
git merge-base --is-ancestor "$NODE_BASE" HEAD
test "$(git rev-list --count "$NODE_BASE..HEAD")" -ge 1
test -z "$(git rev-list --merges "$NODE_BASE..HEAD")"
test "$(git diff --name-only "$NODE_BASE..HEAD" | LC_ALL=C sort)" = "$EXPECTED_NODE_PATHS"
test "$(git diff --name-only "$NODE_BASE..HEAD")" = "$EXPECTED_NODE_PATHS"
git diff --check "$NODE_BASE..HEAD"
git diff --cached --quiet
test -z "$(git status --porcelain --untracked-files=no)"
test -z "$(git ls-files --others --exclude-standard -- "${NODE_PATHS[@]}")"
```

Expected: at least one non-merge commit exists above immutable `NODE_BASE`; literal committed-range path equality passes in sorted and native output; the index and tracked worktree are clean; no owned path remains untracked.

- [ ] **Step 5: Re-gate the complete committed range**

Run all commands again against the committed state:

```bash
.venv/bin/python -m pytest -q \
  tests/test_team_evidence_aggregation.py \
  tests/test_team_evidence_aggregation_scope.py
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m pytest --collect-only -q
.venv/bin/python -m pytest -q
codegraph sync .
git diff --check "$NODE_BASE..HEAD"
test "$(git diff --name-only "$NODE_BASE..HEAD" | LC_ALL=C sort)" = "$EXPECTED_NODE_PATHS"
test "$(git diff --name-only "$NODE_BASE..HEAD")" = "$EXPECTED_NODE_PATHS"
git diff --cached --quiet
test -z "$(git status --porcelain --untracked-files=no)"

HISTORY_SECRET_PATTERN='(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(^|[^[:alnum:]_])sk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|postgres(ql)?://[^[:space:]/]+:[^[:space:]@]+@)'
NODE2C_HISTORY_COMMITS_TEXT="$(git rev-list --reverse "$NODE_BASE..HEAD")"
test -n "$NODE2C_HISTORY_COMMITS_TEXT"
mapfile -t NODE2C_HISTORY_COMMITS <<< "$NODE2C_HISTORY_COMMITS_TEXT"
HISTORY_PATHS=()
NODE2C_REVIEW_FIX_SHAS=()
COMMIT_INDEX=0
for commit in "${NODE2C_HISTORY_COMMITS[@]}"; do
  COMMIT_INDEX=$((COMMIT_INDEX + 1))
  COMMIT_PARENT_TEXT="$(git rev-list --parents -n 1 "$commit")"
  read -r -a COMMIT_AND_PARENT <<< "$COMMIT_PARENT_TEXT"
  test "${#COMMIT_AND_PARENT[@]}" -eq 2
  test "${COMMIT_AND_PARENT[0]}" = "$commit"

  COMMIT_SUBJECT="$(git log -1 --format=%s "$commit")"
  if [ "$COMMIT_INDEX" -eq 1 ]; then
    test "$COMMIT_SUBJECT" = 'Add team evidence aggregation status facade'
  else
    test "$COMMIT_SUBJECT" = 'Fix Node 2C review findings'
    NODE2C_REVIEW_FIX_SHAS+=("$commit")
  fi

  COMMIT_PATHS_TEXT="$(git diff-tree --no-commit-id --name-only -r "$commit" | LC_ALL=C sort)"
  test -n "$COMMIT_PATHS_TEXT"
  mapfile -t COMMIT_PATHS <<< "$COMMIT_PATHS_TEXT"
  for path in "${COMMIT_PATHS[@]}"; do
    case "$path" in
      src/polymarket_alpha_lab/team_evidence_aggregation.py | \
      tests/test_team_evidence_aggregation.py | \
      tests/test_team_evidence_aggregation_scope.py) ;;
      *) printf 'commit %s changes non-allowlisted path %s\n' "$commit" "$path" >&2; exit 1 ;;
    esac
    HISTORY_PATHS+=("$path")
  done

  COMMIT_ADDED_LINES="$(git show --format= --no-ext-diff --no-textconv --unified=0 "$commit" | sed -n '/^+++ /d; /^+/s/^+//p')"
  COMMIT_MESSAGE="$(git log -1 --format=%B "$commit")"
  if rg -q "$HISTORY_SECRET_PATTERN" <<< "$COMMIT_MESSAGE"$'\n'"$COMMIT_ADDED_LINES"; then
    RG_STATUS=0
  else
    RG_STATUS=$?
  fi
  case "$RG_STATUS" in
    0) printf 'high-confidence credential shape in commit %s\n' "$commit" >&2; exit 1 ;;
    1) ;;
    *) printf 'history secret scan failed for commit %s with rg status %s\n' "$commit" "$RG_STATUS" >&2; exit "$RG_STATUS" ;;
  esac
done

HISTORY_PATH_UNION="$(printf '%s\n' "${HISTORY_PATHS[@]}" | LC_ALL=C sort -u)"
test "$HISTORY_PATH_UNION" = "$EXPECTED_NODE_PATHS"
NODE2C_REVIEW_FIX_COUNT="${#NODE2C_REVIEW_FIX_SHAS[@]}"
test "$NODE2C_REVIEW_FIX_COUNT" -eq "$((${#NODE2C_HISTORY_COMMITS[@]} - 1))"
if [ "$NODE2C_REVIEW_FIX_COUNT" -eq 0 ]; then
  NODE2C_REVIEW_FIX_SHAS_TEXT=''
else
  NODE2C_REVIEW_FIX_SHAS_TEXT="$(printf '%s\n' "${NODE2C_REVIEW_FIX_SHAS[@]}")"
  test "$(printf '%s\n' "$NODE2C_REVIEW_FIX_SHAS_TEXT" | sed '/^$/d' | wc -l)" -eq "$NODE2C_REVIEW_FIX_COUNT"
fi
NODE2C_HISTORY_HEAD="$(git rev-parse HEAD)"
NODE2C_HISTORY_STATUS=pass
NODE2C_FOCUSED_TESTS_STATUS=pass
NODE2C_COMPILEALL_STATUS=pass
NODE2C_COLLECT_STATUS=pass
NODE2C_FULL_PYTEST_STATUS=pass
NODE2C_CODEGRAPH_STATUS=pass
NODE2C_CLEAN_WORKTREE_STATUS=pass
export HISTORY_PATH_UNION NODE2C_REVIEW_FIX_COUNT NODE2C_REVIEW_FIX_SHAS_TEXT \
  NODE2C_HISTORY_HEAD NODE2C_HISTORY_STATUS NODE2C_FOCUSED_TESTS_STATUS \
  NODE2C_COMPILEALL_STATUS NODE2C_COLLECT_STATUS NODE2C_FULL_PYTEST_STATUS \
  NODE2C_CODEGRAPH_STATUS NODE2C_CLEAN_WORKTREE_STATUS
readonly HISTORY_PATH_UNION NODE2C_REVIEW_FIX_COUNT NODE2C_REVIEW_FIX_SHAS_TEXT \
  NODE2C_HISTORY_HEAD NODE2C_HISTORY_STATUS NODE2C_FOCUSED_TESTS_STATUS \
  NODE2C_COMPILEALL_STATUS NODE2C_COLLECT_STATUS NODE2C_FULL_PYTEST_STATUS \
  NODE2C_CODEGRAPH_STATUS NODE2C_CLEAN_WORKTREE_STATUS
```

Run the complete committed-range scan without referring to index state:

```bash
set -euo pipefail
: "${NODE2C_HISTORY_HEAD:?per-commit history-gate HEAD is required}"
: "${NODE2C_HISTORY_STATUS:?per-commit history status is required}"
test "$NODE2C_HISTORY_STATUS" = pass
test "$(git rev-parse HEAD)" = "$NODE2C_HISTORY_HEAD"
HIGH_CONFIDENCE_SECRET_PATTERN='(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(^|[^[:alnum:]_])sk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|postgres(ql)?://[^[:space:]/]+:[^[:space:]@]+@)'
SENSITIVE_FIELD_PATTERN='\b(api[_-]?key|secret|token|password|passwd|cookie|authorization|bearer|private[_ -]?key|seed phrase|mnemonic|wallet|account[_ -]?(id|address))\b'
READONLY_PATTERN='\b(live[_ -]?trading|order[_ -]?submission|submit[_ -]?order|cancel[_ -]?order|replace[_ -]?order|sign[_ -]?order|wallet|private[_ -]?key|hosted[_ -]?account|account[_ -]?authentication|exchange[_ -]?mutation|order[_ -]?mutation)\b'
PERSISTENCE_PATTERN='\b(sqlite|duckdb|redis|mongodb|sqlalchemy|jsonl|file[_ -]?backed|postgres|supabase|dsn|validate_local_postgres_dsn|migration|persistence|store)\b'

SENSITIVE_RANGE_PATHS=()
READONLY_RANGE_PATHS=()
PERSISTENCE_RANGE_PATHS=()

for path in "${NODE_PATHS[@]}"; do
  ADDED_RANGE="$({
    git diff --unified=0 "$NODE_BASE..$NODE2C_HISTORY_HEAD" -- "$path" |
      sed -n '/^+++ /d; /^+/s/^+//p'
  })"

  if rg -q "$HIGH_CONFIDENCE_SECRET_PATTERN" <<< "$ADDED_RANGE"; then
    RG_STATUS=0
  else
    RG_STATUS=$?
  fi
  case "$RG_STATUS" in
    0)
      printf 'high-confidence credential-shaped range addition in %s\n' "$path" >&2
      exit 1
      ;;
    1)
      ;;
    *)
      printf 'high-confidence range secret scan command failed for %s with rg status %s\n' "$path" "$RG_STATUS" >&2
      exit "$RG_STATUS"
      ;;
  esac

  for scan_name in sensitive readonly persistence; do
    case "$scan_name" in
      sensitive) pattern="$SENSITIVE_FIELD_PATTERN" ;;
      readonly) pattern="$READONLY_PATTERN" ;;
      persistence) pattern="$PERSISTENCE_PATTERN" ;;
    esac
    if rg -qi "$pattern" <<< "$ADDED_RANGE"; then
      RG_STATUS=0
    else
      RG_STATUS=$?
    fi
    case "$RG_STATUS" in
      0)
        case "$scan_name" in
          sensitive) SENSITIVE_RANGE_PATHS+=("$path") ;;
          readonly) READONLY_RANGE_PATHS+=("$path") ;;
          persistence) PERSISTENCE_RANGE_PATHS+=("$path") ;;
        esac
        ;;
      1)
        ;;
      *)
        printf '%s range scan command failed for %s with rg status %s\n' "$scan_name" "$path" "$RG_STATUS" >&2
        exit "$RG_STATUS"
        ;;
    esac
  done
done

test "$(printf '%s\n' "${SENSITIVE_RANGE_PATHS[@]}" | sed '/^$/d' | LC_ALL=C sort)" = \
  'tests/test_team_evidence_aggregation_scope.py'
test "$(printf '%s\n' "${READONLY_RANGE_PATHS[@]}" | sed '/^$/d' | LC_ALL=C sort)" = \
  'tests/test_team_evidence_aggregation_scope.py'
test "$(printf '%s\n' "${PERSISTENCE_RANGE_PATHS[@]}" | sed '/^$/d' | LC_ALL=C sort)" = \
  'tests/test_team_evidence_aggregation_scope.py'

git diff --check "$NODE_BASE..$NODE2C_HISTORY_HEAD"
git diff --quiet
git diff --cached --quiet
test -z "$(git status --porcelain --untracked-files=no)"

GATED_HEAD="$(git rev-parse HEAD)"
test "$GATED_HEAD" = "$NODE2C_HISTORY_HEAD"
SENSITIVE_RANGE_PATHS_TEXT="$(printf '%s\n' "${SENSITIVE_RANGE_PATHS[@]}" | sed '/^$/d' | LC_ALL=C sort)"
READONLY_RANGE_PATHS_TEXT="$(printf '%s\n' "${READONLY_RANGE_PATHS[@]}" | sed '/^$/d' | LC_ALL=C sort)"
PERSISTENCE_RANGE_PATHS_TEXT="$(printf '%s\n' "${PERSISTENCE_RANGE_PATHS[@]}" | sed '/^$/d' | LC_ALL=C sort)"
NODE2C_RANGE_SCAN_STATUS=pass
NODE2C_GATE_EVIDENCE="$(
  printf '%s\n' \
    "gated_head=$GATED_HEAD" \
    "history_gate=$NODE2C_HISTORY_STATUS" \
    "focused_tests=$NODE2C_FOCUSED_TESTS_STATUS" \
    "compileall=$NODE2C_COMPILEALL_STATUS" \
    "collect_only=$NODE2C_COLLECT_STATUS" \
    "full_pytest=$NODE2C_FULL_PYTEST_STATUS" \
    "codegraph_sync=$NODE2C_CODEGRAPH_STATUS" \
    "clean_worktree=$NODE2C_CLEAN_WORKTREE_STATUS" \
    "range_scan=$NODE2C_RANGE_SCAN_STATUS" \
    "review_fix_count=$NODE2C_REVIEW_FIX_COUNT" \
    'review_fix_shas_begin' \
    "$NODE2C_REVIEW_FIX_SHAS_TEXT" \
    'review_fix_shas_end' \
    'history_path_union_begin' \
    "$HISTORY_PATH_UNION" \
    'history_path_union_end' \
    'sensitive_paths_begin' \
    "$SENSITIVE_RANGE_PATHS_TEXT" \
    'sensitive_paths_end' \
    'readonly_paths_begin' \
    "$READONLY_RANGE_PATHS_TEXT" \
    'readonly_paths_end' \
    'persistence_paths_begin' \
    "$PERSISTENCE_RANGE_PATHS_TEXT" \
    'persistence_paths_end'
)"
export GATED_HEAD NODE2C_RANGE_SCAN_STATUS NODE2C_GATE_EVIDENCE
readonly GATED_HEAD NODE2C_RANGE_SCAN_STATUS NODE2C_GATE_EVIDENCE
```

Expected: focused/full/static gates all exit `0`; CodeGraph is synced; range hygiene and exact manifest pass; the first range commit has the exact initial subject and every later commit has the exact review-fix subject; the zero-or-more review-fix SHAs are preserved in oldest-first order with an exact count; high-confidence secret scan is clean without printing matched values; the three broader scans classify only the negative scope-test path. As in the staged scan, `rg` status `0` is a match to classify or fail, `1` is clean, and every status greater than `1` is a command failure.

- [ ] **Step 6: Obtain the mandatory local Claude Code full-range PASS**

Run this exact local command. It has no fallback model, no write-capable tool, no session persistence, and no fast mode:

```bash
set -euo pipefail
: "${GATED_HEAD:?complete committed-range GATED_HEAD is required}"
: "${NODE2C_GATE_EVIDENCE:?complete committed-range gate evidence is required}"
: "${NODE2C_HISTORY_STATUS:?per-commit history status is required}"
: "${NODE2C_FOCUSED_TESTS_STATUS:?focused-test status is required}"
: "${NODE2C_COMPILEALL_STATUS:?compileall status is required}"
: "${NODE2C_COLLECT_STATUS:?collect-only status is required}"
: "${NODE2C_FULL_PYTEST_STATUS:?full-pytest status is required}"
: "${NODE2C_CODEGRAPH_STATUS:?CodeGraph status is required}"
: "${NODE2C_CLEAN_WORKTREE_STATUS:?clean-worktree status is required}"
: "${NODE2C_REVIEW_FIX_COUNT:?review-fix count is required}"
test -n "${NODE2C_REVIEW_FIX_SHAS_TEXT+x}"
test "$NODE2C_HISTORY_STATUS" = pass
test "$NODE2C_FOCUSED_TESTS_STATUS" = pass
test "$NODE2C_COMPILEALL_STATUS" = pass
test "$NODE2C_COLLECT_STATUS" = pass
test "$NODE2C_FULL_PYTEST_STATUS" = pass
test "$NODE2C_CODEGRAPH_STATUS" = pass
test "$NODE2C_CLEAN_WORKTREE_STATUS" = pass
test "$NODE2C_REVIEW_FIX_COUNT" -ge 0
test -z "${NODE2C_ACCEPTED_REVIEW_HEAD+x}"
command -v claude >/dev/null
command -v jq >/dev/null
command -v timeout >/dev/null

NODE2A_RECEIPT_EVIDENCE="$(
  validate_predecessor_pass_receipt \
    NODE2A_RECEIPT_EVIDENCE \
    team-evidence-aggregation-contracts-codec-temporal-eligibility \
    "$NODE2A_PREREQ_SHA" \
    "$NODE2A_PASS_RECEIPT_PATH" \
    "$NODE2A_PASS_RECEIPT_SHA256" \
    node2a-13
)"
NODE2B_RECEIPT_EVIDENCE="$(
  validate_predecessor_pass_receipt \
    NODE2B_RECEIPT_EVIDENCE \
    team-evidence-aggregation-independence-correlation-requirement-witness \
    "$NODE2B_PREREQ_SHA" \
    "$NODE2B_PASS_RECEIPT_PATH" \
    "$NODE2B_PASS_RECEIPT_SHA256" \
    node2b-15
)"
test "$NODE2A_RECEIPT_EVIDENCE" = "$INITIAL_NODE2A_RECEIPT_EVIDENCE"
test "$NODE2B_RECEIPT_EVIDENCE" = "$INITIAL_NODE2B_RECEIPT_EVIDENCE"

REVIEW_HEAD="$GATED_HEAD"
test "$(git rev-parse HEAD)" = "$REVIEW_HEAD"
test "$(git diff --name-only "$NODE_BASE..$REVIEW_HEAD" | LC_ALL=C sort)" = "$EXPECTED_NODE_PATHS"
git diff --quiet
git diff --cached --quiet
test -z "$(git status --porcelain --untracked-files=no)"

REVIEW_TMP_DIR="$(mktemp -d /home/ubuntu/test-sandbox/tmp/node2c-claude-review.XXXXXX)"
readonly REVIEW_HEAD REVIEW_TMP_DIR
REVIEW_PROMPT_PATH="$REVIEW_TMP_DIR/prompt.txt"
REVIEW_STREAM_PATH="$REVIEW_TMP_DIR/stream.jsonl"
REVIEW_STDERR_PATH="$REVIEW_TMP_DIR/stderr.txt"
REVIEW_REPORT_PATH="$REVIEW_TMP_DIR/report.md"
readonly REVIEW_PROMPT_PATH REVIEW_STREAM_PATH REVIEW_STDERR_PATH REVIEW_REPORT_PATH

cleanup_node2c_review_tmp() {
  rm -rf -- "$REVIEW_TMP_DIR"
}
trap cleanup_node2c_review_tmp EXIT

{
  printf '%s\n' \
    'Read-only Node 2C full-range review. Do not modify, create, or delete files.' \
    'Fast mode is forbidden.' \
    'Read exactly AGENTS.md and these five normative documents:' \
    'docs/superpowers/specs/2026-07-13-team-evidence-aggregation-core-design.md' \
    'docs/quality/phase-1-development-node-quality-gates.md' \
    'docs/superpowers/plans/2026-07-13-team-evidence-aggregation-contracts-codec-temporal-eligibility.md' \
    'docs/superpowers/plans/2026-07-13-team-evidence-aggregation-independence-correlation-requirement-witness.md' \
    'docs/superpowers/plans/2026-07-13-team-evidence-aggregation-status-facade-publishability-scope-guard.md' \
    'Use only Read, Glob, and Grep. Do not search for unnamed planning documents or use a write-capable tool.' \
    'Inspect the complete unrestricted NODE_BASE..REVIEW_HEAD patch and exact three-path allowlist printed below.' \
    'Verify both selector identity sets, pair-level selected_current_revision, all thirteen disposition precedence branches, exact allocation/witness data flow, weighted canonical P(YES), contradiction boundaries, blocked > watch > ready, complete exact reasons, ready-only supplied-bound publication, shared private rematerializer, config/core digest validation, and tuple/Decimal-context determinism.' \
    'Verify witnesses preserve the exact five-field candidate-edge order: (requirement_id, source_lineage_id, evidence_revision_id, assessment_revision_id, capture_id), while the separate four-field allocation join remains unchanged.' \
    'Verify the unified six-module AST/module-import/export/forbidden-surface/package-root/line-size gate, including exact module-only normalization, both absolute import forms, benign member names and aliases without a second member allowlist, witness datetime/decimal permissions, exact-prefix/package-root/relative/empty-module/wildcard rejection, alias-resistant forbidden-name/call scans, direct export ownership, every individual line ceiling, the 500-line scope-file ceiling, and 4,850 total test-line ceiling.' \
    'Verify paper_only=True, report_only=True, readonly=True and absence of persistence, DB, network, CLI, live/auth/account/private-key/wallet/signing/order/execution surfaces.' \
    'Confirm future persistence wording remains local Supabase/Postgres only and requires validate_local_postgres_dsn before any connection, wrapper, adapter, repository, or store construction; this pure child performs no persistence.' \
    'Actual focused/compile/collect/full/CodeGraph/history/range/clean/classification evidence follows; assess every classified path rather than assuming it is acceptable.' \
    'Return findings first with file:line references. Your exact final nonblank line must be VERDICT: PASS or VERDICT: REVISE.' \
    "NODE_BASE=$NODE_BASE" \
    "REVIEW_HEAD=$REVIEW_HEAD" \
    "NODE2A_PREREQ_SHA=$NODE2A_PREREQ_SHA" \
    "NODE2B_PREREQ_SHA=$NODE2B_PREREQ_SHA" \
    "$NODE2A_RECEIPT_EVIDENCE" \
    "$NODE2B_RECEIPT_EVIDENCE" \
    "$NODE2C_GATE_EVIDENCE" \
    'EXACT_CHANGED_PATHS:' \
    "$EXPECTED_NODE_PATHS" \
    'COMPLETE_PATCH:'
  git diff --no-ext-diff --no-textconv --unified=80 "$NODE_BASE..$REVIEW_HEAD"
} > "$REVIEW_PROMPT_PATH"

set +e
timeout --signal=TERM --kill-after=10s 330s \
  claude --print \
    --input-format text \
    --output-format stream-json \
    --include-partial-messages \
    --verbose \
    --bare \
    --safe-mode \
    --model claude-opus-4-8 \
    --effort max \
    --tools Read,Glob,Grep \
    --permission-mode dontAsk \
    --no-session-persistence \
    --system-prompt 'You are a read-only senior engineering reviewer. Use only Read, Glob, and Grep. Return findings first and the exact required verdict. Do not modify, create, or delete files.' \
    < "$REVIEW_PROMPT_PATH" \
    > "$REVIEW_STREAM_PATH" \
    2> "$REVIEW_STDERR_PATH"
REVIEW_STATUS=$?
set -e

REVIEW_PARSE_STATUS=0
if jq -j '
  select(
    .type == "stream_event"
    and .event.type == "content_block_delta"
    and .event.delta.type == "text_delta"
  ) | .event.delta.text
' "$REVIEW_STREAM_PATH" > "$REVIEW_REPORT_PATH"; then
  REVIEW_PARSE_STATUS=0
else
  REVIEW_PARSE_STATUS=$?
fi

if [ "$REVIEW_PARSE_STATUS" -eq 0 ] && [ ! -s "$REVIEW_REPORT_PATH" ]; then
  if jq -er '
    select(
      .type == "result"
      and .subtype == "success"
      and (.result | type == "string")
      and (.result | length > 0)
    ) | .result
  ' "$REVIEW_STREAM_PATH" > "$REVIEW_REPORT_PATH"; then
    REVIEW_PARSE_STATUS=0
  else
    REVIEW_PARSE_STATUS=$?
  fi
fi

if [ -s "$REVIEW_REPORT_PATH" ]; then
  cat "$REVIEW_REPORT_PATH"
fi
if [ -s "$REVIEW_STDERR_PATH" ]; then
  printf '\nClaude stderr diagnostics (credential shapes redacted):\n' >&2
  sed -E \
    -e 's/(gh[pousr]_|github_pat_|sk-)[A-Za-z0-9_-]{10,}/[REDACTED_TOKEN]/g' \
    -e 's#postgres(ql)?://[^[:space:]]+#[REDACTED_POSTGRES_DSN]#g' \
    -e 's#eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}#[REDACTED_JWT]#g' \
    "$REVIEW_STDERR_PATH" >&2
fi

NONBLANK_REVIEW_OUTPUT="$(sed -n '/[^[:space:]]/p' "$REVIEW_REPORT_PATH")"
REVIEW_FINAL_LINE=''
if [ -n "$NONBLANK_REVIEW_OUTPUT" ]; then
  REVIEW_FINAL_LINE="$(printf '%s\n' "$NONBLANK_REVIEW_OUTPUT" | tail -n 1)"
fi

REVIEW_GATE_RESULT=error
if [ "$REVIEW_STATUS" -ne 0 ]; then
  REVIEW_GATE_RESULT=error
elif [ "$REVIEW_PARSE_STATUS" -ne 0 ]; then
  REVIEW_GATE_RESULT=error
elif [ -z "$NONBLANK_REVIEW_OUTPUT" ]; then
  REVIEW_GATE_RESULT=error
elif [ "$REVIEW_FINAL_LINE" = 'VERDICT: REVISE' ]; then
  REVIEW_GATE_RESULT=revise
elif [ "$REVIEW_FINAL_LINE" != 'VERDICT: PASS' ]; then
  REVIEW_GATE_RESULT=error
elif [ "$(git rev-parse HEAD)" != "$REVIEW_HEAD" ]; then
  REVIEW_GATE_RESULT=error
elif ! git diff --quiet; then
  REVIEW_GATE_RESULT=error
elif ! git diff --cached --quiet; then
  REVIEW_GATE_RESULT=error
elif [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  REVIEW_GATE_RESULT=error
else
  REVIEW_GATE_RESULT=pass
fi

cleanup_node2c_review_tmp
trap - EXIT

NODE2C_REVIEW_GATE_RESULT="$REVIEW_GATE_RESULT"
export NODE2C_REVIEW_GATE_RESULT
if [ "$NODE2C_REVIEW_GATE_RESULT" = pass ]; then
  NODE2C_ACCEPTED_REVIEW_HEAD="$REVIEW_HEAD"
  NODE2C_ACCEPTED_REVIEW_MODEL=claude-opus-4-8
  NODE2C_ACCEPTED_REVIEW_EFFORT=max
  NODE2C_ACCEPTED_REVIEW_READ_ONLY=true
  NODE2C_ACCEPTED_REVIEW_FAST_MODE=false
  NODE2C_ACCEPTED_REVIEW_EXIT_STATUS="$REVIEW_STATUS"
  NODE2C_ACCEPTED_REVIEW_FINAL_LINE="$REVIEW_FINAL_LINE"
  export NODE2C_ACCEPTED_REVIEW_HEAD NODE2C_ACCEPTED_REVIEW_MODEL \
    NODE2C_ACCEPTED_REVIEW_EFFORT NODE2C_ACCEPTED_REVIEW_READ_ONLY \
    NODE2C_ACCEPTED_REVIEW_FAST_MODE NODE2C_ACCEPTED_REVIEW_EXIT_STATUS \
    NODE2C_ACCEPTED_REVIEW_FINAL_LINE
  readonly NODE2C_REVIEW_GATE_RESULT NODE2C_ACCEPTED_REVIEW_HEAD \
    NODE2C_ACCEPTED_REVIEW_MODEL NODE2C_ACCEPTED_REVIEW_EFFORT \
    NODE2C_ACCEPTED_REVIEW_READ_ONLY NODE2C_ACCEPTED_REVIEW_FAST_MODE \
    NODE2C_ACCEPTED_REVIEW_EXIT_STATUS NODE2C_ACCEPTED_REVIEW_FINAL_LINE
  printf 'accepted Node 2C reviewed HEAD: %s\n' "$NODE2C_ACCEPTED_REVIEW_HEAD"
elif [ "$NODE2C_REVIEW_GATE_RESULT" = revise ]; then
  printf 'Claude returned VERDICT: REVISE; Step 7 is required and publication remains blocked.\n' >&2
  exit 2
else
  printf 'Claude review errored or returned an invalid verdict; publication remains blocked.\n' >&2
  exit 1
fi
```

Expected: both descriptor-bound receipt revalidations reproduce their exact
initial canonical evidence. `NODE2C_REVIEW_GATE_RESULT=pass` and the readonly
`NODE2C_ACCEPTED_REVIEW_*` values are published only for exit `0`, valid stream
JSON, nonempty text extracted from streamed `text_delta` events or, only when
that stream is empty, one successful nonempty `result.result`, exact final
`VERDICT: PASS`, unchanged `GATED_HEAD`, and a
clean tracked worktree/index. The report is printed before classification, so
`VERDICT: REVISE` remains actionable without authorization and exits `2`;
review/tool/parse errors exit `1`. Receipt
replacement, timeout, malformed/empty output, any other final line, tool/model
failure, repository movement, or unavailable local Claude blocks publication.
There is no fallback reviewer.

- [ ] **Step 7: Apply any review fix as a new commit, then repeat the entire range gate and review**

For every requested change:

1. Record `PRE_FIX_HEAD="$(git rev-parse HEAD)"`.
2. Add or strengthen a failing focused test first and run it to observe the expected failure. For a non-behavioral finding, first reproduce it with the narrowest executable static/gate command.
3. Make the smallest owned-file fix and run the focused command to green.
4. Derive the exact changed fix subset, prove every path is in `NODE_PATHS`, stage only that subset, and prove staged equality with the subset.
5. Run the dedicated subset-aware staged hygiene and scan block below. Do not reuse Step 3's initial-commit expectation that all broader classifications equal the scope-test path.
6. Commit with a new focused commit; assert `HEAD != PRE_FIX_HEAD`.
7. Terminate the current gate shell. Start a fresh shell, rerun Task 1, then repeat Step 5 over the complete immutable `NODE_BASE..HEAD` range, including focused tests, compileall, collect-only, full pytest, CodeGraph sync, exact endpoint manifest, per-commit path/secret history, diff hygiene, clean-worktree checks, and every range scan.
8. Repeat Step 6 with a fresh full-range Claude invocation. Do not enter Step 8 unless it publishes `NODE2C_REVIEW_GATE_RESULT=pass`, exact `NODE2C_ACCEPTED_REVIEW_HEAD`, exit status `0`, and exact final `VERDICT: PASS`.

Use this exact subset check before each fix commit:

```bash
set -euo pipefail
FIX_PATHS_TEXT="$(git diff --name-only | LC_ALL=C sort)"
test -n "$FIX_PATHS_TEXT"
mapfile -t FIX_PATHS <<< "$FIX_PATHS_TEXT"
test "${#FIX_PATHS[@]}" -gt 0
for path in "${FIX_PATHS[@]}"; do
  case "$path" in
    src/polymarket_alpha_lab/team_evidence_aggregation.py | \
    tests/test_team_evidence_aggregation.py | \
    tests/test_team_evidence_aggregation_scope.py) ;;
    *) printf 'non-allowlisted fix path: %s\n' "$path" >&2; exit 1 ;;
  esac
done
git add -- "${FIX_PATHS[@]}"
STAGED_FIX_PATHS_TEXT="$(git diff --cached --name-only | LC_ALL=C sort)"
test "$STAGED_FIX_PATHS_TEXT" = "$FIX_PATHS_TEXT"
git diff --cached --check

HIGH_CONFIDENCE_SECRET_PATTERN='(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(^|[^[:alnum:]_])sk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|postgres(ql)?://[^[:space:]/]+:[^[:space:]@]+@)'
SENSITIVE_FIELD_PATTERN='\b(api[_-]?key|secret|token|password|passwd|cookie|authorization|bearer|private[_ -]?key|seed phrase|mnemonic|wallet|account[_ -]?(id|address))\b'
READONLY_PATTERN='\b(live[_ -]?trading|order[_ -]?submission|submit[_ -]?order|cancel[_ -]?order|replace[_ -]?order|sign[_ -]?order|wallet|private[_ -]?key|hosted[_ -]?account|account[_ -]?authentication|exchange[_ -]?mutation|order[_ -]?mutation)\b'
PERSISTENCE_PATTERN='\b(sqlite|duckdb|redis|mongodb|sqlalchemy|jsonl|file[_ -]?backed|postgres|supabase|dsn|validate_local_postgres_dsn|migration|persistence|store)\b'
FIX_SENSITIVE_PATHS=()
FIX_READONLY_PATHS=()
FIX_PERSISTENCE_PATHS=()

for path in "${FIX_PATHS[@]}"; do
  STAGED_ADDED_FOR_PATH="$(git diff --cached --unified=0 -- "$path" | sed -n '/^+++ /d; /^+/s/^+//p')"
  if rg -q "$HIGH_CONFIDENCE_SECRET_PATTERN" <<< "$STAGED_ADDED_FOR_PATH"; then
    RG_STATUS=0
  else
    RG_STATUS=$?
  fi
  case "$RG_STATUS" in
    0) printf 'high-confidence credential-shaped fix addition in %s\n' "$path" >&2; exit 1 ;;
    1) ;;
    *) printf 'fix secret scan failed for %s with rg status %s\n' "$path" "$RG_STATUS" >&2; exit "$RG_STATUS" ;;
  esac

  for scan_name in sensitive readonly persistence; do
    case "$scan_name" in
      sensitive) pattern="$SENSITIVE_FIELD_PATTERN" ;;
      readonly) pattern="$READONLY_PATTERN" ;;
      persistence) pattern="$PERSISTENCE_PATTERN" ;;
    esac
    if rg -qi "$pattern" <<< "$STAGED_ADDED_FOR_PATH"; then
      case "$scan_name" in
        sensitive) FIX_SENSITIVE_PATHS+=("$path") ;;
        readonly) FIX_READONLY_PATHS+=("$path") ;;
        persistence) FIX_PERSISTENCE_PATHS+=("$path") ;;
      esac
    else
      RG_STATUS=$?
      test "$RG_STATUS" -eq 1
    fi
  done
done

printf 'fix sensitive classifications:\n  %s\n' "${FIX_SENSITIVE_PATHS[@]}"
printf 'fix readonly classifications:\n  %s\n' "${FIX_READONLY_PATHS[@]}"
printf 'fix persistence classifications:\n  %s\n' "${FIX_PERSISTENCE_PATHS[@]}"
git commit -m "Fix Node 2C review findings"
FIX_COMMIT_SHA="$(git rev-parse HEAD)"
test "$FIX_COMMIT_SHA" != "$PRE_FIX_HEAD"
printf 'created Node 2C review-fix commit: %s\n' "$FIX_COMMIT_SHA"
```

All findings from one Claude report may be corrected in one focused fix commit.
A later report requiring another correction requires another new commit and
fresh-shell full-range gate. No amended commit is allowed after review. Every
post-review change invalidates all prior gate and review evidence until the
complete range passes again.

- [ ] **Step 8: Immediately recheck remote base, push without force, and verify remote `main`**

Run immediately after the final PASS:

```bash
set -euo pipefail
: "${GATED_HEAD:?final GATED_HEAD is required}"
: "${NODE2C_REVIEW_GATE_RESULT:?Node 2C review result is required}"
: "${NODE2C_ACCEPTED_REVIEW_HEAD:?accepted Node 2C reviewed HEAD is required}"
: "${NODE2C_ACCEPTED_REVIEW_MODEL:?accepted review model is required}"
: "${NODE2C_ACCEPTED_REVIEW_EFFORT:?accepted review effort is required}"
: "${NODE2C_ACCEPTED_REVIEW_READ_ONLY:?accepted read-only marker is required}"
: "${NODE2C_ACCEPTED_REVIEW_FAST_MODE:?accepted fast-mode marker is required}"
: "${NODE2C_ACCEPTED_REVIEW_EXIT_STATUS:?accepted review status is required}"
: "${NODE2C_ACCEPTED_REVIEW_FINAL_LINE:?accepted review verdict is required}"
: "${NODE2C_REVIEW_FIX_COUNT:?review-fix count is required}"
test -n "${NODE2C_REVIEW_FIX_SHAS_TEXT+x}"
test "$NODE2C_REVIEW_GATE_RESULT" = pass
test "$NODE2C_ACCEPTED_REVIEW_HEAD" = "$GATED_HEAD"
test "$NODE2C_ACCEPTED_REVIEW_MODEL" = claude-opus-4-8
test "$NODE2C_ACCEPTED_REVIEW_EFFORT" = max
test "$NODE2C_ACCEPTED_REVIEW_READ_ONLY" = true
test "$NODE2C_ACCEPTED_REVIEW_FAST_MODE" = false
test "$NODE2C_ACCEPTED_REVIEW_EXIT_STATUS" = 0
test "$NODE2C_ACCEPTED_REVIEW_FINAL_LINE" = 'VERDICT: PASS'
test "$NODE2C_REVIEW_FIX_COUNT" -ge 0

LOCAL_HEAD="$(git rev-parse HEAD)"
test "$LOCAL_HEAD" = "$NODE2C_ACCEPTED_REVIEW_HEAD"
test "$(git diff --name-only "$NODE_BASE..$LOCAL_HEAD" | LC_ALL=C sort)" = "$EXPECTED_NODE_PATHS"
test "$(git diff --name-only "$NODE_BASE..$LOCAL_HEAD")" = "$EXPECTED_NODE_PATHS"
git diff --cached --quiet
test -z "$(git status --porcelain --untracked-files=no)"
test -z "$(git ls-files --others --exclude-standard -- "${NODE_PATHS[@]}")"

COMMON_GIT_DIR="$(git rev-parse --git-common-dir)"
GIT_AUTH=(-c credential.helper= -c "credential.helper=store --file=$COMMON_GIT_DIR/github-credentials")
REMOTE_ENDPOINT="$(git remote get-url --push --all origin)"
test -n "$REMOTE_ENDPOINT"
test "${REMOTE_ENDPOINT//$'\n'/}" = "$REMOTE_ENDPOINT"
readonly REMOTE_ENDPOINT

observe_node2c_remote_main() {
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
export -f observe_node2c_remote_main
readonly -f observe_node2c_remote_main

if ! CURRENT_REMOTE="$(observe_node2c_remote_main)"; then
  printf 'could not obtain a stable pre-push remote observation; no push was performed.\n' >&2
  exit 74
fi
if [ "$CURRENT_REMOTE" != "$NODE_BASE" ]; then
  printf 'stable remote main moved before push; no rebase or push was performed. Start a fresh gate from a newly fetched base.\n' >&2
  exit 75
fi

set +e
GIT_TERMINAL_PROMPT=0 git "${GIT_AUTH[@]}" push \
  --no-force --no-force-with-lease --no-force-if-includes --no-follow-tags \
  --no-recurse-submodules \
  "$REMOTE_ENDPOINT" "$LOCAL_HEAD:refs/heads/main"
PUSH_STATUS=$?
set -e
PUSH_ATTEMPTED=true

POST_PUSH_OBSERVATION_STATUS=0
if REMOTE_AFTER_PUSH="$(observe_node2c_remote_main)"; then
  POST_PUSH_OBSERVATION_STATUS=0
else
  POST_PUSH_OBSERVATION_STATUS=$?
  REMOTE_AFTER_PUSH=''
fi

if [ "$POST_PUSH_OBSERVATION_STATUS" -ne 0 ]; then
  REMOTE_PUBLICATION_STATUS=indeterminate
  printf 'post-push remote observation is indeterminate; do not push again. Use only the observation-recovery block.\n' >&2
elif [ "$REMOTE_AFTER_PUSH" = "$LOCAL_HEAD" ]; then
  REMOTE_PUBLICATION_STATUS=confirmed_exact
  CONFIRMED_REMOTE_MAIN_SHA="$REMOTE_AFTER_PUSH"
  printf 'stable remote main equals the accepted reviewed HEAD; publication is confirmed, including a possible lost client response.\n'
elif git merge-base --is-ancestor "$LOCAL_HEAD" "$REMOTE_AFTER_PUSH"; then
  REMOTE_PUBLICATION_STATUS=published_but_superseded
  printf 'remote main contains but no longer equals the accepted reviewed HEAD; exact-equality completion is blocked.\n' >&2
elif [ "$REMOTE_AFTER_PUSH" = "$NODE_BASE" ] && [ "$PUSH_STATUS" -ne 0 ]; then
  REMOTE_PUBLICATION_STATUS=not_published_observed
  printf 'remote main remains NODE_BASE after the failed push; do not retry automatically.\n' >&2
elif [ "$REMOTE_AFTER_PUSH" = "$NODE_BASE" ]; then
  REMOTE_PUBLICATION_STATUS=inconsistent_success_response
  printf 'push returned success but stable remote main remains NODE_BASE; use only the observation-recovery block.\n' >&2
else
  REMOTE_PUBLICATION_STATUS=remote_moved_or_diverged
  printf 'stable remote main neither equals nor contains the accepted reviewed HEAD; a fresh base and full review are required.\n' >&2
fi

test "$(git rev-parse HEAD)" = "$LOCAL_HEAD"
export CURRENT_REMOTE LOCAL_HEAD PUSH_STATUS PUSH_ATTEMPTED \
  POST_PUSH_OBSERVATION_STATUS REMOTE_AFTER_PUSH REMOTE_PUBLICATION_STATUS
readonly CURRENT_REMOTE LOCAL_HEAD PUSH_STATUS PUSH_ATTEMPTED
if [ "$REMOTE_PUBLICATION_STATUS" = confirmed_exact ]; then
  export CONFIRMED_REMOTE_MAIN_SHA
  readonly POST_PUSH_OBSERVATION_STATUS REMOTE_AFTER_PUSH \
    REMOTE_PUBLICATION_STATUS CONFIRMED_REMOTE_MAIN_SHA
fi
```

If and only if the status is `indeterminate` or
`inconsistent_success_response`, the same shell may run this observation-only
recovery block. It contains no push and may be repeated until a stable
classification is available:

```bash
set -euo pipefail
: "${PUSH_ATTEMPTED:?push-attempt marker is required}"
: "${REMOTE_PUBLICATION_STATUS:?publication status is required}"
test "$PUSH_ATTEMPTED" = true
test "$NODE2C_ACCEPTED_REVIEW_HEAD" = "$LOCAL_HEAD"
test "$(git rev-parse HEAD)" = "$LOCAL_HEAD"
case "$REMOTE_PUBLICATION_STATUS" in
  indeterminate | inconsistent_success_response) ;;
  *) printf 'observation-only recovery is not permitted from status %s\n' "$REMOTE_PUBLICATION_STATUS" >&2; exit 1 ;;
esac

if RECOVERED_REMOTE="$(observe_node2c_remote_main)"; then
  POST_PUSH_OBSERVATION_STATUS=0
  REMOTE_AFTER_PUSH="$RECOVERED_REMOTE"
  if [ "$REMOTE_AFTER_PUSH" = "$LOCAL_HEAD" ]; then
    REMOTE_PUBLICATION_STATUS=confirmed_exact
    CONFIRMED_REMOTE_MAIN_SHA="$REMOTE_AFTER_PUSH"
    export POST_PUSH_OBSERVATION_STATUS REMOTE_AFTER_PUSH \
      REMOTE_PUBLICATION_STATUS CONFIRMED_REMOTE_MAIN_SHA
    readonly POST_PUSH_OBSERVATION_STATUS REMOTE_AFTER_PUSH \
      REMOTE_PUBLICATION_STATUS CONFIRMED_REMOTE_MAIN_SHA
    printf 'observation recovery confirms exact remote equality; Node 2C completion is now eligible.\n'
  elif git merge-base --is-ancestor "$LOCAL_HEAD" "$REMOTE_AFTER_PUSH"; then
    REMOTE_PUBLICATION_STATUS=published_but_superseded
    export POST_PUSH_OBSERVATION_STATUS REMOTE_AFTER_PUSH REMOTE_PUBLICATION_STATUS
    printf 'observation recovery found a descendant remote; exact-equality completion remains blocked.\n' >&2
  elif [ "$REMOTE_AFTER_PUSH" = "$NODE_BASE" ]; then
    REMOTE_PUBLICATION_STATUS=not_published_observed
    export POST_PUSH_OBSERVATION_STATUS REMOTE_AFTER_PUSH REMOTE_PUBLICATION_STATUS
    printf 'observation recovery confirms that the reviewed commit was not published; do not retry automatically.\n' >&2
  else
    REMOTE_PUBLICATION_STATUS=remote_moved_or_diverged
    export POST_PUSH_OBSERVATION_STATUS REMOTE_AFTER_PUSH REMOTE_PUBLICATION_STATUS
    printf 'observation recovery found unrelated remote movement; a fresh full gate is required.\n' >&2
  fi
else
  POST_PUSH_OBSERVATION_STATUS=$?
  REMOTE_PUBLICATION_STATUS=indeterminate
  export POST_PUSH_OBSERVATION_STATUS REMOTE_PUBLICATION_STATUS
  printf 'remote observation remains indeterminate; no second push is permitted.\n' >&2
fi
```

After the initial classification and any permitted observation-only recovery,
run this mandatory completion assertion. This is the only successful exit from
the publication gate:

```bash
set -euo pipefail
: "${CURRENT_REMOTE:?stable pre-push remote observation is required}"
: "${LOCAL_HEAD:?literal pushed commit SHA is required}"
: "${PUSH_ATTEMPTED:?push-attempt marker is required}"
: "${POST_PUSH_OBSERVATION_STATUS:?post-push observation status is required}"
: "${REMOTE_AFTER_PUSH:?stable post-push remote SHA is required}"
: "${REMOTE_PUBLICATION_STATUS:?remote publication status is required}"
: "${CONFIRMED_REMOTE_MAIN_SHA:?confirmed remote main SHA is required}"
test "$CURRENT_REMOTE" = "$NODE_BASE"
test "$LOCAL_HEAD" = "$NODE2C_ACCEPTED_REVIEW_HEAD"
test "$PUSH_ATTEMPTED" = true
test "$POST_PUSH_OBSERVATION_STATUS" = 0
test "$REMOTE_PUBLICATION_STATUS" = confirmed_exact
[[ "$CONFIRMED_REMOTE_MAIN_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$REMOTE_AFTER_PUSH" = "$LOCAL_HEAD"
test "$CONFIRMED_REMOTE_MAIN_SHA" = "$LOCAL_HEAD"
test "$(git rev-parse HEAD)" = "$LOCAL_HEAD"
printf 'Node 2C publication confirmed at exact remote SHA %s\n' \
  "$CONFIRMED_REMOTE_MAIN_SHA"
```

Expected: immediately before the sole real non-force push attempt, a stable
observation shows remote `main == NODE_BASE`. The push uses the frozen explicit
endpoint and literal reviewed commit SHA, never symbolic `HEAD`, and neither
this block nor recovery performs rebase, force push, or a second push. After the
attempt, only stable exact equality with `LOCAL_HEAD` yields
`REMOTE_PUBLICATION_STATUS=confirmed_exact`; that result is valid even when the
client lost a successful push response. A descendant is classified as
`published_but_superseded`, an unchanged base after a failed attempt as
`not_published_observed`, a success response with an unchanged base as
`inconsistent_success_response`, unrelated movement as
`remote_moved_or_diverged`, and unavailable stable observation as
`indeterminate`. Every status other than `confirmed_exact` fails the mandatory
completion assertion and blocks Node 2C completion without mutating local
`HEAD` or authorizing another push.

## Completion Evidence

The implementation handoff records these exact outcomes:

```text
Node 2A prerequisite SHA and pinned PASS receipt: validated
Node 2B prerequisite SHA and pinned PASS receipt: validated
NODE_BASE from origin/main: recorded and immutable for the gate run
HEAD/origin/main and merge-base ancestry checks: pass
exact staged allowlist equality: pass
focused Node 2C tests: pass
unified six-module/package-root child-local gate: pass
scope file <= 500 physical lines: pass
all seven Node 2 test modules satisfy their individual physical-line ceilings: pass
all seven Node 2 tests <= 4,850 physical lines total: pass
all six Node 2 production modules satisfy their individual physical-line ceilings: pass
all six Node 2 production modules <= 3,500 physical lines total: pass
compileall: pass
pytest collect-only: pass
full pytest: pass
CodeGraph sync: pass
diff hygiene: pass
clean tracked worktree and index: pass
high-confidence secret scan: clean without matched-value output
readonly/persistence/sensitive scans: only negative scope-test assertions classified
exact committed NODE_BASE..HEAD range equality: pass
GATED_HEAD: recorded from the complete committed-range gate
NODE2C_ACCEPTED_REVIEW_HEAD == GATED_HEAD: pass
NODE2C_ACCEPTED_REVIEW_MODEL=claude-opus-4-8
NODE2C_ACCEPTED_REVIEW_EFFORT=max
NODE2C_ACCEPTED_REVIEW_READ_ONLY=true
NODE2C_ACCEPTED_REVIEW_FAST_MODE=false
NODE2C_ACCEPTED_REVIEW_EXIT_STATUS=0
NODE2C_ACCEPTED_REVIEW_FINAL_LINE=VERDICT: PASS
review-fix count and ordered fix-commit SHAs: recorded, including zero fixes
CURRENT_REMOTE == NODE_BASE immediately before the sole push: pass
LOCAL_HEAD == NODE2C_ACCEPTED_REVIEW_HEAD: pass
PUSH_ATTEMPTED=true
PUSH_STATUS: recorded exactly, including a possible lost client response
POST_PUSH_OBSERVATION_STATUS=0
REMOTE_AFTER_PUSH == LOCAL_HEAD: pass
CONFIRMED_REMOTE_MAIN_SHA == LOCAL_HEAD: pass
REMOTE_PUBLICATION_STATUS=confirmed_exact
```

Node 2C is complete only when every line above is true for the same final
committed range and the stable publication classification is exactly
`confirmed_exact`. A nonzero `PUSH_STATUS` is not itself disqualifying when the
stable remote observation proves exact publication, but it must be recorded.
Any durable copy of this completion evidence must use only the local
Supabase/Postgres target and must validate every raw DSN through
`validate_local_postgres_dsn` before connection construction; no file journal
is introduced. The resulting child remains pure and transient: no project data
is persisted by the child, no package-root surface is widened, and no
live/auth/account/private-key/wallet/signing/order/execution authority is
introduced.
