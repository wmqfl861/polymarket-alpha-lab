# Node 4 Plan: Team Evidence Aggregation Schema

This July 2026 plan retains its original review instructions for receipt
and implementation history. For a current review, follow
[Native Codex Subagent Routing](../../../AGENTS.md#native-codex-subagent-routing).

**Goal:** Add the Phase 1 persistence contract for immutable team-evidence aggregation attempts. The node delivers one append-only aggregation-attempt table, an offline row codec, fail-closed local configuration, exact-format partial indexes, migration-safety documentation, and an operator runbook.

Node 4 does not add a store, psycopg connection path, writer, account access, live trading, or disposable-database test. Store and database execution work belong to Node 5. The schema must preserve the identities and evaluation-scope payload emitted by the reviewed Node 3 module (commit `938a01b5`).

**Assembly note:** This plan merges the initial Codex draft with its factual revision pass against the committed Node 3 payload; the reviewer-reference line is normalized to the human-directed external Codex `gpt-6-astra` / `model_reasoning_effort=max` review. Table name follows the revision (`team_evaluation_attempts`); the migration filename and environment-variable family follow the initial draft.

## Scope and invariants

Node 4 remains within the Phase 1 boundary:

- `paper_only = true`, `report_only = true`, `readonly = true` — required, default true, with `CHECK (... IS TRUE)` constraints.
- No account authentication, wallet handling, private-key handling, order signing, submission, cancellation, replacement, or live execution.
- Persistence targets local Supabase/Postgres only; every raw DSN passes through `validate_local_postgres_dsn` before connection or adapter construction.
- The complete Node 3 payload is retained before normalization; `null`, `0`, `false`, and unknown values remain distinct.
- No file-backed persistence is added; no store/psycopg/writer code (Node 5 owns those).

## Fixed interfaces

1. The persisted raw payload is the complete Node 3 `evaluation_scope_payload`, with this exact top-level allowlist:

   ```text
   scope_version
   domain_context
   provenance
   run_metadata
   evaluator_receipts
   node2_config
   node2_input
   node2_result
   ```

   Missing or additional top-level keys are rejected by the codec before persistence.

2. `domain_context` and `provenance` are retained exactly as supplied by Node 3; they are not flattened into invented columns.
3. The status domain is exactly `ready`, `watch`, `blocked` (verified: `team_evidence_aggregation_types.py` `_member("status", self.status, ("ready", "watch", "blocked"))`). The database check constraint, codec validation, fixtures, partial-index predicates, and query helpers all use this set; `pass` is rejected.
4. `tea_id` is supplied by the attempt envelope and is the immutable primary key; it is not fabricated from payload content.
5. The canonical database run reference is `tfr_id`; the Python-facing field may be named `team_forecast_run_id`, mapping to the single stored `tfr_id` value. No duplicate alias columns.
6. No per-row `tfe_id` foreign key is invented from evaluator receipts; replay/evidence identifiers remain inside `evaluator_receipts` in the raw payload.
7. `core_digest` is a comparison/value field only; it never participates in a primary key, unique constraint, scope identity, or ordering.

## Canonical payload encoding and hash

`payload_sha256` is the SHA-256 digest of the complete `evaluation_scope_payload`:

```text
Node 3 canonical JSON encoding of the full evaluation_scope_payload, encoded as UTF-8
```

The codec calls the same canonical encoding used by Node 3 (keys sorted recursively, compact separators, arrays keep order, non-finite values rejected). The digest is the lowercase hexadecimal SHA-256 of those bytes. The codec never reconstructs the payload from database columns before hashing; it hashes the original eight-key payload after the top-level allowlist and Node 3 shape checks pass.

## Scope identity

The payload contains no independent lineage or revision ordinal, so `lineage_id`, `revision_no`, and `supersedes_attempt_id` are removed from the design. The scope dimension is:

- `scope_version` — copied from `evaluation_scope_payload["scope_version"]`.
- `scope_key` — `sha256(Node 3 canonical JSON encoding of evaluation_scope_payload["domain_context"]).hexdigest()`, a lowercase 64-character digest. It must never be called `core_digest`, and `node2_result.core_digest` must not be used as the scope key.

History is obtained by ordering attempts by `attempted_at DESC, tea_id DESC` within a run or scope; no revision ordinal or supersession link is persisted.

## Promoted columns

Only fields present in the actual Node 3 payload are promoted:

| Column | Source | Purpose |
|---|---|---|
| `tea_id` | attempt envelope `team_evaluation_attempt_id` | immutable attempt identity, primary key |
| `tfr_id` | attempt envelope `team_forecast_run_id` | natural run reference |
| `attempted_at` | `node2_result.evaluated_at` | evaluation timestamp |
| `status` | `node2_result.status` | `ready`, `watch`, or `blocked` |
| `hard_flag` | `node2_result.contradiction` | hard contradiction flag used by partial indexes |
| `scope_version` | top-level `scope_version` | scope version |
| `scope_key` | SHA-256 of canonical `domain_context` | deterministic scope identity |
| `config_version` | `node2_result.config_version` | evaluated Node 2 configuration version |
| `config_digest` | `node2_result.config_digest` | evaluated Node 2 configuration digest |
| `diagnostic_record_count` | `node2_result.diagnostic_record_count` | diagnostic count |
| `arithmetic_record_count` | `node2_result.arithmetic_record_count` | arithmetic count |
| `payload_sha256` | canonical full-payload hash | integrity and idempotency evidence |
| `evaluation_scope_payload` | complete eight-key Node 3 payload | lossless raw evidence |

Removed as not derivable from a single payload: `lineage_id`, `revision_no`, `supersedes_attempt_id`, `decision`, `evidence_count`, `warning_count`, `scope_kind`. `run_metadata.started_at`/`completed_at` stay in the raw payload; `attempted_at` intentionally maps to `node2_result.evaluated_at` (when the evaluation result was produced, not when the run started).

## Table shape

The migration file is exactly `supabase/migrations/20260714000000_team_evidence_aggregation_tables.sql` and creates `team_evaluation_attempts`:

```sql
tea_id                    text primary key,
tfr_id                    text not null,
attempted_at              timestamptz not null,
status                    text not null,
hard_flag                 boolean not null,
scope_version             text not null,
scope_key                 text not null,
config_version            text not null,
config_digest             text not null,
diagnostic_record_count   integer not null,
arithmetic_record_count   integer not null,
payload_sha256            text not null,
evaluation_scope_payload  jsonb not null,
created_at                timestamptz not null default now()
```

Required checks: `CHECK (status IN ('ready', 'watch', 'blocked'))`, `CHECK (diagnostic_record_count >= 0)`, `CHECK (arithmetic_record_count >= 0)`, `CHECK (length(payload_sha256) = 64)`, plus payload-agreement checks for every promoted payload field (IDs, scope, status, timestamps, counts, hard flags) in the `(payload_json ->> ...)` style used by the repo, and the three Phase 1 hard-flag `CHECK (... IS TRUE)` columns per repo convention (`paper_only`, `report_only`, `readonly` boolean not null default true). The eight-key top-level agreement is enforced in the codec and the schema-facing tests. No `UNIQUE (lineage_id, revision_no)`; `tea_id` is the sole identity key.

The migration contains no forbidden backend or URL tokens (`sqlite`, `redis`, `mongodb`, `mysql`, `postgresql://`, `file:`, SQLAlchemy references, `http://`, `https://`, `supabase_url`, `service_role`, `anon_key`, `create_client`, `database_url`). It is forward-only and append-only; Node 5 writers will use `ON CONFLICT DO NOTHING`. Node 4 implements no update, delete, replacement, or connection code.

## Partial indexes

Exactly two, with pinned names, predicates, and column orders:

```sql
CREATE INDEX team_evaluation_attempts_hard_by_run_idx
ON team_evaluation_attempts (
    tfr_id,
    attempted_at DESC,
    tea_id DESC
)
WHERE hard_flag IS TRUE
  AND status IN ('ready', 'watch', 'blocked');

CREATE INDEX team_evaluation_attempts_hard_by_scope_idx
ON team_evaluation_attempts (
    scope_version,
    scope_key,
    attempted_at DESC,
    tea_id DESC
)
WHERE hard_flag IS TRUE
  AND status IN ('ready', 'watch', 'blocked');
```

Any migration or test changing names, predicates, or column orders fails review. The latest-attempt query is run/scope history ordered by `attempted_at DESC, tea_id DESC`.

## Local configuration

`src/polymarket_alpha_lab/supabase_team_evidence_aggregation_config.py` mirrors the fail-closed `supabase_team_forecast_config.py` pattern:

- `POLYMARKET_ALPHA_LAB_TEAM_EVIDENCE_AGGREGATION_DB_ENABLED`
- `POLYMARKET_ALPHA_LAB_TEAM_EVIDENCE_AGGREGATION_DB_DSN`
- `POLYMARKET_ALPHA_LAB_TEAM_EVIDENCE_AGGREGATION_DB_TABLE` (default `team_evaluation_attempts`; lowercase identifier validation)

Enabled without DSN fails closed; DSNs pass `validate_local_postgres_dsn` at config construction; `__repr__` redacts the DSN; `.env.example` documents the new names with blank values only. No connection attempt is permitted in Node 4.

## Implementation sequence

1. **Contract tests and fixtures — RED first**
   - [ ] Offline fixtures built only through Node 3 identity and payload interfaces.
   - [ ] Failing tests for: exact eight-key payload allowlist; rejection of missing/extra top-level keys; status acceptance for `ready`/`watch`/`blocked` and rejection of `pass`; mapping of `evaluated_at`, `status`, `contradiction`, and both record counts; deterministic `scope_key` from canonical `domain_context`; `scope_key` distinct from `node2_result.core_digest`; deterministic `payload_sha256` over full canonical payload; preservation of nested sections and array order; rejection when any hard flag is false; schema introspection of the promoted column set; absence of the removed invented columns; exact partial-index names/key orders/predicates; exact migration filename/table/column set/required checks; forbidden-token scan of the migration text; configuration blank/missing variables, invalid table names, DSN validation, disabled mode, redacted representation; DSN validation before connection construction (fake); deterministic history ordering.
2. **Migration — GREEN** per the table shape and partial indexes above; migration-text tests pass without opening a database.
3. **Row codec — GREEN** (`src/polymarket_alpha_lab/team_evidence_aggregation_db_row.py`): immutable row type mirroring `team_forecast_db_row.py`; payload-to-row path; canonical `payload_sha256`; promoted scalar extraction with agreement validation; hard-flag validation; JSON-safe parameter output; Decimal values remain strings; absent/null/zero/unknown stay distinct.
4. **Local configuration — GREEN** per the section above.
5. **Operator documentation — GREEN**: `docs/team-evidence-aggregation-supabase-runbook.md` (blank-value setup, local-only DSN handling, migration ordering after `20260713000000`, insert-only `ON CONFLICT DO NOTHING` semantics, latest-attempt query semantics, Phase 1 flags) and `docs/team-evidence-aggregation-migration-safety.md` (preflight review, transaction/lock expectations, forward-only rollback posture, exact index predicates, Node 5 handoff). Update `docs/index.md` only if its module-index test requires rows for the new Python modules; otherwise omit it from the allowlist. No credentials or executable order paths in docs.
6. **Node gate**: focused checks, full suite, compile, diff, migration-token and secret scans; CodeGraph sync recorded `documented-unavailable` while the CLI is absent; read-only external Codex review (`gpt-6-astra`, `model_reasoning_effort=max`, final line `VERDICT: PASS`) before commit.

## Exact Sorted Implementation Allowlist

```bash
NODE_PATHS=(
  "docs/index.md"
  "docs/team-evidence-aggregation-migration-safety.md"
  "docs/team-evidence-aggregation-supabase-runbook.md"
  "src/polymarket_alpha_lab/supabase_team_evidence_aggregation_config.py"
  "src/polymarket_alpha_lab/team_evidence_aggregation_db_row.py"
  "supabase/migrations/20260714000000_team_evidence_aggregation_tables.sql"
  "tests/test_supabase_team_evidence_aggregation_config.py"
  "tests/test_team_evidence_aggregation_db_row.py"
  "tests/test_team_evidence_aggregation_schema.py"
)
```

If `docs/index.md` requires no change per its module-index test, remove it from the materialized allowlist before the first implementation commit. No file outside `NODE_PATHS` may change in this node. The plan file itself is committed separately before implementation.

## Line ceilings

Migration ≤260 lines; row codec ≤260; config ≤180; each focused test file ≤220; each runbook/safety document ≤180; `docs/index.md` change ≤20 lines.

## Verification commands

```bash
unset PYTEST_ADDOPTS PYTEST_PLUGINS PYTHONPATH PYTHONHOME
export PYTHONUTF8=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export POLYMARKET_ALPHA_LAB_RUN_SUPABASE_SMOKE=0

.venv/Scripts/python.exe -m pytest -q \
  tests/test_team_evidence_aggregation_db_row.py \
  tests/test_supabase_team_evidence_aggregation_config.py \
  tests/test_team_evidence_aggregation_schema.py \
  tests/test_phase1_docs_module_index_matches_files.py

.venv/Scripts/python.exe -m compileall -q src tests
.venv/Scripts/python.exe -m pytest --collect-only -q
.venv/Scripts/python.exe scripts/verify_local.py --full
git diff --check
```

The schema test reads the migration as text, lowercases it, rejects every forbidden token, asserts the exact table/constraint clauses, and asserts the exact partial-index predicates and column orders — all without database connectivity.

## Stop conditions

Stop immediately for: Node 3 interface drift; a changed status domain; any `core_digest` identity usage; missing payload-agreement or hard-flag checks; a non-exact partial-index predicate/order; a forbidden migration token; an alternate persistence backend; secret exposure; a store/connection implementation; a disposable-database test; a file outside `NODE_PATHS`; a line-ceiling breach; failing focused/full tests; failed compile or diff checks; or a reviewer result other than `VERDICT: PASS`. Do not push while the publication gate remains blocked; record the blocked gate and preserve the clean local commit.

## Completion evidence

The node is complete only with: focused-test log, full-suite result, compile result, clean `git diff --check`, migration-token and secret-scan results, CodeGraph status recorded honestly, the exact allowlist diff, the recorded read-only Codex `gpt-6-astra`/`max` `VERDICT: PASS`, and a clean granular commit based on local `938a01b5`. Database execution evidence is explicitly deferred to Node 5.
