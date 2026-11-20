# Canonical Team Forecast P(YES) Contract Implementation Plan

This July 2026 plan retains its original review instructions for receipt
and implementation history. For a current review, follow
[Native Codex Subagent Routing](../../../AGENTS.md#native-codex-subagent-routing).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing canonical `P(YES)` semantics explicit and testable across team packets, DB rows, calibration, side-edge economics, Supabase metadata, and operator documentation without changing the correct arithmetic.

**Architecture:** `TeamForecastPacket.forecast_probability`, its DB selector column, and `PaperProbabilitySideEdgeInput.forecast_probability` all carry canonical `P(YES)`. `selected_side` or `side` identifies the paper-review side being evaluated but never reorients that probability. The downstream side-edge reducer derives `P(NO) = 1 - P(YES)` at this reducer's local `_side_probability` complement boundary.

**Tech Stack:** Python 3.12 virtualenv, frozen dataclasses, fixed-six `Decimal`, pytest, append-only PostgreSQL migration comments, local Supabase disposable-database verification, CodeGraph, Claude Code `claude-opus-4-8` at effort `max`.

## Global Constraints

- Phase 1 stays structurally `paper_only=True`, `report_only=True`, and `readonly=True`.
- No live trading, wallet/account access, auth, private keys, signing, order mutation, or automated execution.
- All durable project data uses local Supabase/Postgres only. This node adds no alternate persistence.
- Do not modify the applied baseline migration or rewrite any forecast row.
- Do not change `team_forecast_to_side_edge_input` arithmetic: it must copy canonical `P(YES)` unchanged.
- Packet, DB-row, and side-edge probability helpers must require a raw finite `Decimal` in `[0, 1]` before fixed-six quantization while preserving the sign of exact fixed-six zero for legacy payload and digest compatibility; `Decimal("-0.000000")` must remain signed.
- `_side_probability` remains this reducer's local side-complement boundary; its docstring must not claim that it is the globally sole complement boundary.
- Packet/DB/docs use: `forecast_probability always denotes canonical Decimal P(YES), regardless of selected_side; selected_side identifies the paper-review side being evaluated and never reorients forecast_probability; P(NO) is 1 - P(YES).`
- Side-edge input uses the type-specific equivalent with `side` in place of `selected_side`.
- Claude review is read-only, model `claude-opus-4-8`, effort `max`, fast mode off, with no fallback reviewer.

## Exact Node Allowlist

Only these paths may change in this node:

```text
docs/superpowers/plans/2026-07-13-btc-domain-evidence-aggregation-foundation.md
docs/superpowers/plans/2026-07-13-team-forecast-probability-yes-contract.md
src/polymarket_alpha_lab/team_forecast_packet.py
src/polymarket_alpha_lab/team_forecast_db_row.py
src/polymarket_alpha_lab/paper_probability_side_edge.py
supabase/migrations/20260713000000_team_forecast_probability_yes_contract.sql
tests/test_team_forecast_packet.py
tests/test_paper_probability_side_edge.py
tests/test_team_forecast_calibration.py
tests/test_calibration_drift_monitor.py
tests/test_team_forecast_db_row.py
tests/test_team_forecast_schema.py
tests/test_team_forecast_supabase_runbook.py
tests/test_team_forecast_migration_safety.py
tests/test_team_diagnostics_docs.py
tests/test_phase1_docs_required_terms.py
tests/test_team_event_template_performance.py
tests/test_team_memory_synthesis.py
docs/team-forecast-supabase-runbook.md
docs/team-forecast-migration-safety.md
docs/team-agent-operating-model.md
docs/team-agent-framework.md
docs/team-diagnostics-readonly.md
docs/playbooks/phase1-specialist-team-playbooks.md
docs/operators/phase1-strategy-stack-walkthrough.md
docs/operators/phase1-probability-event-go-no-go-runbook.md
docs/strategy/phase1-probability-event-filtering-workflow.md
docs/strategy-first-roadmap.md
docs/strategy-candidate-decision-matrix.md
docs/phase1/probability-event-readonly-supabase-principles.md
docs/data_dictionary/phase1-research-decision-objects.md
```

Capture the authoritative base before implementation:

```bash
set -euo pipefail
NODE=team-forecast-probability-yes-contract
NODE_BASE="$(git rev-parse HEAD)"
COMMON_GIT_DIR="$(git rev-parse --git-common-dir)"
GIT_AUTH=(-c credential.helper= -c "credential.helper=store --file=$COMMON_GIT_DIR/github-credentials")
REMOTE_BASE="$(GIT_TERMINAL_PROMPT=0 git "${GIT_AUTH[@]}" ls-remote --exit-code origin refs/heads/main | cut -f1)"
test "$NODE_BASE" = "$REMOTE_BASE"
```

---

### Task 1: Characterize Probability Normalization And The NO-Side Boundary With TDD

**Files:**
- Modify: `src/polymarket_alpha_lab/team_forecast_packet.py`
- Modify: `src/polymarket_alpha_lab/paper_probability_side_edge.py`
- Modify: `tests/test_team_forecast_packet.py`
- Modify: `tests/test_paper_probability_side_edge.py`

**Interfaces:**
- Consumes: `TeamForecastPacket`, `team_forecast_to_side_edge_input`, and `build_paper_probability_side_edge_report`.
- Produces: packet and side-edge probability helpers that reject raw out-of-range values before quantization, preserve exact signed zero, and preserve `P(YES)` until this reducer applies its local complement.

- [ ] **Step 1: Write failing raw-boundary, signed-zero, and end-to-end characterization tests**

In `tests/test_team_forecast_packet.py`, prove that values which would round into the valid interval are still rejected as raw inputs, and characterize the backward-compatible requirement that exact signed zero remains `-0.000000` in both the packet and its durable payload:

```python
@pytest.mark.parametrize(
    "forecast_probability",
    (d("-0.0000004"), d("1.0000004")),
)
def test_forecast_probability_rejects_raw_out_of_range_values_before_quantization(
    forecast_probability: Decimal,
) -> None:
    with pytest.raises(
        ValueError,
        match="forecast_probability must be between zero and one",
    ):
        btc_forecast_packet(forecast_probability=forecast_probability)


def test_forecast_probability_preserves_signed_zero_for_legacy_payload_compatibility() -> None:
    forecast = btc_forecast_packet(forecast_probability=d("-0.000000"))

    assert forecast.forecast_probability == d("0.000000")
    assert forecast.forecast_probability.is_signed() is True
    assert team_forecast_packet_payload(forecast)["forecast_probability"] == "-0.000000"
```

In `tests/test_paper_probability_side_edge.py`, add the same raw boundary values against `PaperProbabilitySideEdgeInput`, require exact signed zero to retain its negative sign for compatibility, and pin the local-boundary docstring without making a repository-wide uniqueness claim:

```python
@pytest.mark.parametrize(
    "forecast_probability",
    (d("-0.0000004"), d("1.0000004")),
)
def test_side_edge_rejects_raw_out_of_range_probability_before_quantization(
    forecast_probability: Decimal,
) -> None:
    with pytest.raises(ValueError, match="forecast_probability"):
        edge_input(forecast_probability=forecast_probability)


def test_side_edge_preserves_signed_zero_probability_for_legacy_compatibility() -> None:
    value = edge_input(forecast_probability=d("-0.000000"))

    assert value.forecast_probability == d("0.000000")
    assert value.forecast_probability.is_signed() is True


def test_side_probability_docstring_scopes_complement_to_this_reducer() -> None:
    assert (_side_probability.__doc__ or "") == (
        "Convert canonical P(YES) to the evaluated side probability at this "
        "reducer's boundary."
    )
```

Extend the side-edge imports in `tests/test_team_forecast_packet.py` and add this end-to-end case using the existing helpers:

```python
def test_no_side_adapter_preserves_pyes_and_reducer_derives_pno() -> None:
    adapted = team_forecast_to_side_edge_input(
        btc_forecast_packet(
            selected_side="no",
            forecast_probability=d("0.320000"),
        ),
        cost_input=btc_cost_input(
            side_price=d("0.600000"),
            fee_cost_per_share=d("0.010000"),
            spread_cost_per_share=d("0.000000"),
            slippage_cost_per_share=d("0.000000"),
            risk_cost_per_share=d("0.000000"),
            capital_cost_per_share=d("0.000000"),
        ),
    )

    assert adapted.side == "no"
    assert adapted.forecast_probability == d("0.320000")

    report = build_paper_probability_side_edge_report(
        (adapted,),
        config=PaperProbabilitySideEdgeConfig(
            config_version="probability-side-edge-pyes-contract-v1",
            min_net_probability_edge=d("0.010000"),
        ),
        generated_at=GENERATED_AT,
    )
    assert report.rows[0].side_probability == d("0.680000")
```

In separate tests, assert the exact `selected_side` contract appears in `TeamForecastPacket.__doc__` and `team_forecast_to_side_edge_input.__doc__`, assert the type-specific contract with `side` appears in `PaperProbabilitySideEdgeInput.__doc__`, and retain the existing `0.640000 -> 0.360000` NO-side characterization.

- [ ] **Step 2: Run the RED test**

```bash
.venv/bin/python -m pytest -q tests/test_team_forecast_packet.py tests/test_paper_probability_side_edge.py
```

Expected: the pre-fix helpers wrongly accept `-0.0000004` and `1.0000004` after rounding, while the signed-zero cases characterize existing compatibility behavior and must pass. The contract/docstring cases fail, and existing side-edge arithmetic characterizations continue to pass. Keep packet and adapter docstring assertions as separate tests so both RED failures are observed before GREEN.

- [ ] **Step 3: Validate raw probabilities, preserve signed zero, and add scoped code contracts**

In both `team_forecast_packet.py` and `paper_probability_side_edge.py`, update `_normalize_probability` in this exact order: require exact `Decimal` type, reject non-finite input, reject the raw value when it is below zero or above one, and then quantize to six decimal places. Return the quantized value unchanged so exact `Decimal("-0.000000")` retains its sign. Do not perform the range check on the quantized value, do not call `copy_abs()` or otherwise rewrite zero, and do not broaden this change to non-probability decimal helpers.

Add concise docstrings to `TeamForecastPacket`, `PaperProbabilitySideEdgeInput`, and `team_forecast_to_side_edge_input`. Task 2 exclusively owns the `TeamForecastDbRow` docstring. The adapter must retain:

```python
forecast_probability=forecast.forecast_probability,
```

Document `_side_probability` only as this reducer's conversion boundary:

```python
def _side_probability(value: PaperProbabilitySideEdgeInput) -> Decimal:
    """Convert canonical P(YES) to the evaluated side probability at this reducer's boundary."""

    if value.side == "yes":
        return value.forecast_probability
    return _subtract_decimal(ONE, value.forecast_probability)
```

Do not use `only`, `sole`, or any equivalent global uniqueness claim in this docstring.

- [ ] **Step 4: Run the focused GREEN tests**

```bash
.venv/bin/python -m pytest -q tests/test_team_forecast_packet.py tests/test_paper_probability_side_edge.py
```

Expected: all pass.

### Task 2: Lock DB And Calibration Semantics

**Files:**
- Modify: `tests/test_team_forecast_db_row.py`
- Modify: `tests/test_team_forecast_calibration.py`
- Modify: `tests/test_calibration_drift_monitor.py`
- Modify: `tests/test_team_event_template_performance.py`
- Modify: `tests/test_team_memory_synthesis.py`
- Modify: `src/polymarket_alpha_lab/team_forecast_db_row.py`

**Interfaces:**
- Produces: a DB-row probability helper with raw bounds and signed-zero preservation, a legacy payload/hash compatibility regression, and scoring tests proving that `selected_side="no"` does not complement the stored/scored forecast.

- [ ] **Step 1: Add RED cases for NO-selected forecasts**

Add a DB row round-trip case with `selected_side="no"` and `forecast_probability=Decimal("0.200000")`. Assert the selector column, `payload_json`, and recovered packet all retain `0.200000`. Also assert this exact contract string appears in `TeamForecastDbRow.__doc__` so production documentation has a RED state:

```text
Store canonical Decimal P(YES), regardless of selected_side; selected_side identifies the paper-review side being evaluated and never reorients forecast_probability; P(NO) is 1 - P(YES).
```

Construct `TeamForecastDbRow` directly from the existing row helper values so packet normalization cannot hide DB-row behavior. Require both raw values that would round into range to fail before quantization, and require exact signed zero to retain its sign:

```python
@pytest.mark.parametrize(
    "forecast_probability",
    (d("-0.0000004"), d("1.0000004")),
)
def test_forecast_db_row_rejects_raw_out_of_range_probability_before_quantization(
    forecast_probability: Decimal,
) -> None:
    values = _row_values(team_forecast_to_db_row(_forecast_packet()))
    values["forecast_probability"] = forecast_probability

    with pytest.raises(
        ValueError,
        match="forecast_probability must be between zero and one",
    ):
        TeamForecastDbRow(**values)


def test_forecast_db_row_preserves_signed_zero_probability() -> None:
    packet = replace(_forecast_packet(), forecast_probability=d("0.000000"))
    values = _row_values(team_forecast_to_db_row(packet))
    values["forecast_probability"] = d("-0.000000")

    row = TeamForecastDbRow(**values)

    assert row.forecast_probability == d("0.000000")
    assert row.forecast_probability.is_signed() is True
```

Add a legacy hashed-payload regression that starts from `payload_json["forecast_probability"] == "-0.000000"` and the digest computed over those original serialized bytes. Reconstruction must preserve the signed `Decimal`; converting the recovered packet back to a DB row must reproduce the identical payload bytes and SHA-256:

```python
def test_forecast_db_row_round_trips_legacy_signed_zero_payload_and_hash() -> None:
    packet = replace(_forecast_packet(), forecast_probability=d("0.000000"))
    base_row = team_forecast_to_db_row(packet)
    payload_json = _payload_copy(base_row)
    payload_json["forecast_probability"] = "-0.000000"
    original_payload_bytes = json.dumps(
        payload_json,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    values = _row_values(base_row)
    values.update(
        forecast_probability=d("-0.000000"),
        payload_json=payload_json,
        payload_sha256=hashlib.sha256(original_payload_bytes).hexdigest(),
    )

    row = TeamForecastDbRow(**values)
    recovered = team_forecast_from_db_row(row)
    round_tripped = team_forecast_to_db_row(recovered)
    round_tripped_payload_bytes = json.dumps(
        round_tripped.payload_json,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert recovered.forecast_probability.is_signed() is True
    assert row.payload_json["forecast_probability"] == "-0.000000"
    assert round_tripped_payload_bytes == original_payload_bytes
    assert round_tripped.payload_json == row.payload_json
    assert round_tripped.payload_sha256 == row.payload_sha256
```

Add calibration and drift cases that build otherwise identical YES-selected and NO-selected forecasts, both with canonical `P(YES)=0.200000` and actual outcome NO. Assert helper rows preserve the same `0.040000` Brier input, then build the production reports and assert their output metrics are identical. The single-row calibration report must expose:

```text
observed_yes_rate = 0.000000
average_forecast_probability = 0.200000
average_brier_score = 0.040000
calibration_error = 0.200000
```

The corresponding single-slice drift report must expose `average_brier_score=0.040000` and `expected_calibration_error=0.200000`. These assertions target the reducers' returned report objects, not only `_outcome_row(...)`.

Both calibration fixtures currently hard-code `selected_side="yes"`. After observing the RED call-signature failure, add `selected_side: str = "yes"` to each test-only `_forecast_row(...)` helper and forward it to `TeamForecastPacket`; do not change calibration production formulas.

Add NO-selected `P(YES)=0.200000` characterization cases to `tests/test_team_event_template_performance.py` and `tests/test_team_memory_synthesis.py`. Assert each production report's `average_forecast_probability` is `0.200000`, not `0.800000`. Extend only test helper signatures; do not change either production reducer.

- [ ] **Step 2: Run the RED/characterization set**

```bash
.venv/bin/python -m pytest -q tests/test_team_forecast_db_row.py tests/test_team_forecast_calibration.py tests/test_calibration_drift_monitor.py tests/test_team_event_template_performance.py tests/test_team_memory_synthesis.py tests/test_team_performance_summary.py
```

Expected: the DB-row raw-boundary cases fail against the pre-fix probability helper; signed-zero and legacy hash cases pass and lock backward compatibility. New helper-signature and docstring assertions also fail. Existing scoring characterizations show that production code does not complement the canonical forecast merely because `selected_side="no"`.

- [ ] **Step 3: Add the DB row contract and raw probability validation, then run GREEN**

Add this exact `TeamForecastDbRow` docstring without changing selectors or codec values:

```python
"""Store canonical Decimal P(YES), regardless of selected_side; selected_side identifies the paper-review side being evaluated and never reorients forecast_probability; P(NO) is 1 - P(YES)."""
```

Update `team_forecast_db_row.py::_normalize_probability` to require exact finite `Decimal` input and reject the raw value outside `[Decimal("0"), Decimal("1")]` before calling `_normalize_decimal`. Return the fixed-six result unchanged, including the sign on exact zero; do not call `copy_abs()` or regenerate legacy payload text. Rerun the command from Step 2.

Expected: all pass. Replace any stale WIP assertion or docstring that says `independent of selected_side`; orientation is `regardless of selected_side`, while `selected_side` still identifies the side under paper review.

### Task 3: Add The Append-Only Supabase Column Contract

**Files:**
- Create: `supabase/migrations/20260713000000_team_forecast_probability_yes_contract.sql`
- Modify: `tests/test_team_forecast_schema.py`
- Modify: `tests/test_team_forecast_supabase_runbook.py`
- Modify: `tests/test_team_forecast_migration_safety.py`
- Modify: `docs/team-forecast-supabase-runbook.md`
- Modify: `docs/team-forecast-migration-safety.md`

**Interfaces:**
- Produces: PostgreSQL column comments only; no table rewrite, DML, row rewrite, or baseline migration edit.

- [ ] **Step 1: Write failing migration contract tests**

Add a second migration constant and assert the exact two statements below exist. Also assert the migration contains no `INSERT`, `UPDATE`, `DELETE`, `ALTER TABLE`, `CREATE TABLE`, `DROP`, or transaction-control statement.

In `tests/test_team_forecast_supabase_runbook.py`, add focused section tests that require the comment verification to be an executable local `sudo -n docker exec supabase-db psql -v ON_ERROR_STOP=1 ...` catalog command without DSNs or secret values. Require the missing-table recovery section to present two manual `psql -v ON_ERROR_STOP=1` applies, baseline first and comment migration second.

- [ ] **Step 2: Run migration/runbook RED**

```bash
.venv/bin/python -m pytest -q tests/test_team_forecast_schema.py tests/test_team_forecast_supabase_runbook.py tests/test_team_forecast_migration_safety.py
```

Expected: three failures for the missing comment migration, runbook contract, and migration-safety inventory.

- [ ] **Step 3: Add the migration**

```sql
COMMENT ON COLUMN public.team_forecasts.forecast_probability IS
    'Canonical Decimal P(YES) for the event; never P(selected_side).';
COMMENT ON COLUMN public.team_forecasts.selected_side IS
    'Paper-review side being evaluated; does not reorient forecast_probability.';
```

- [ ] **Step 4: Add executable read-only verification and ordered manual recovery**

Document baseline-then-comment migration order and provide this executable local catalog command, not a detached SQL fragment:

```bash
sudo -n docker exec supabase-db psql -v ON_ERROR_STOP=1 -X -U postgres -d postgres -c "
SELECT
    col_description('public.team_forecasts'::regclass, a.attnum) AS column_comment
FROM pg_attribute AS a
WHERE a.attrelid = 'public.team_forecasts'::regclass
  AND a.attname IN ('forecast_probability', 'selected_side')
ORDER BY a.attname;
"
```

The command must remain a catalog read against the local `supabase-db` container and must not contain a DSN, password, token, or key. The runbook must state that migrations are operator prerequisites and must not auto-reset or auto-apply to the host database.

In the `Recovery` section, require the operator to manually reapply both migrations in this order with fail-fast `psql`; do not replace this with an automatic reset/apply command:

```bash
sudo -n docker exec -i supabase-db psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  < supabase/migrations/20260701000000_team_forecast_tables.sql
sudo -n docker exec -i supabase-db psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  < supabase/migrations/20260713000000_team_forecast_probability_yes_contract.sql
```

Add the new migration path to `docs/team-forecast-migration-safety.md` and its contract test so the append-only migration inventory names both the immutable baseline and this corrective comment migration.

- [ ] **Step 5: Run schema/runbook GREEN tests**

```bash
.venv/bin/python -m pytest -q tests/test_team_forecast_schema.py tests/test_team_forecast_supabase_runbook.py tests/test_team_forecast_migration_safety.py
```

- [ ] **Step 6: Verify the migration in a disposable local database**

Create a unique disposable database inside the running local `supabase-db` container, apply `20260701000000_team_forecast_tables.sql` and the new comment migration with `ON_ERROR_STOP=1`, query both comments, and drop the disposable database in a shell `trap`. Never target the `postgres` database schema for migration application:

```bash
set -euo pipefail
DB="pal_pyes_contract_$(tr -d '-' < /proc/sys/kernel/random/uuid)"
CREATED_BY_THIS_RUN=0
cleanup() {
  status=$?
  trap - EXIT
  if [ "$CREATED_BY_THIS_RUN" -eq 1 ]; then
    if ! sudo -n docker exec supabase-db \
        psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
        -c "DROP DATABASE IF EXISTS \"$DB\" WITH (FORCE);"; then
      if [ "$status" -eq 0 ]; then
        status=1
      fi
    fi
  fi
  exit "$status"
}
trap cleanup EXIT
sudo -n docker exec supabase-db \
  psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -c "CREATE DATABASE \"$DB\";"
CREATED_BY_THIS_RUN=1
sudo -n docker exec -i supabase-db \
  psql -U postgres -d "$DB" -v ON_ERROR_STOP=1 \
  < supabase/migrations/20260701000000_team_forecast_tables.sql
sudo -n docker exec -i supabase-db \
  psql -U postgres -d "$DB" -v ON_ERROR_STOP=1 \
  < supabase/migrations/20260713000000_team_forecast_probability_yes_contract.sql
ACTUAL_COMMENTS="$(sudo -n docker exec supabase-db \
  psql -U postgres -d "$DB" -v ON_ERROR_STOP=1 -At \
  -c "SELECT a.attname || '=' || col_description(a.attrelid, a.attnum) FROM pg_attribute AS a WHERE a.attrelid = 'public.team_forecasts'::regclass AND a.attname IN ('forecast_probability', 'selected_side') ORDER BY a.attname;")"
EXPECTED_COMMENTS="$(printf '%s\n' \
  'forecast_probability=Canonical Decimal P(YES) for the event; never P(selected_side).' \
  'selected_side=Paper-review side being evaluated; does not reorient forecast_probability.')"
test "$ACTUAL_COMMENTS" = "$EXPECTED_COMMENTS"
```

Expected comments:

```text
forecast_probability=Canonical Decimal P(YES) for the event; never P(selected_side).
selected_side=Paper-review side being evaluated; does not reorient forecast_probability.
```

### Task 4: Align Living Documentation Contracts

**Files:**
- Modify: `docs/team-agent-operating-model.md`
- Modify: `docs/team-agent-framework.md`
- Modify: `docs/team-diagnostics-readonly.md`
- Modify: `docs/playbooks/phase1-specialist-team-playbooks.md`
- Modify: `docs/operators/phase1-strategy-stack-walkthrough.md`
- Modify: `docs/operators/phase1-probability-event-go-no-go-runbook.md`
- Modify: `docs/strategy/phase1-probability-event-filtering-workflow.md`
- Modify: `docs/strategy-first-roadmap.md`
- Modify: `docs/strategy-candidate-decision-matrix.md`
- Modify: `docs/phase1/probability-event-readonly-supabase-principles.md`
- Modify: `docs/data_dictionary/phase1-research-decision-objects.md`
- Modify: `tests/test_team_diagnostics_docs.py`
- Modify: `tests/test_phase1_docs_required_terms.py`

**Interfaces:**
- Produces: one consistent operator meaning for packet, diagnostics, calibration, and edge formulas.

- [ ] **Step 1: Add failing exact-term assertions**

Require the relevant forecast/diagnostic documents to contain:

```text
forecast_probability always denotes canonical Decimal P(YES), regardless of selected_side; selected_side identifies the paper-review side being evaluated and never reorients forecast_probability; P(NO) is 1 - P(YES).
```

In `tests/test_team_diagnostics_docs.py`, add exactly one isolated function named `test_team_probability_docs_freeze_canonical_pyes_orientation`. It reads only this explicit allowlisted tuple and asserts the contract sentence in every file:

```python
(
    Path("docs/team-agent-operating-model.md"),
    Path("docs/team-agent-framework.md"),
    Path("docs/team-diagnostics-readonly.md"),
    Path("docs/operators/phase1-strategy-stack-walkthrough.md"),
    Path("docs/strategy-first-roadmap.md"),
    Path("docs/strategy-candidate-decision-matrix.md"),
)
```

Do not extend existing README or `.env.example` assertions and do not modify either out-of-allowlist file.

In `tests/test_phase1_docs_required_terms.py`, add exactly one isolated function named `test_probability_event_docs_freeze_canonical_pyes_orientation`. It reads only this explicit allowlisted tuple and asserts the same contract sentence in every file:

```python
(
    Path("docs/operators/phase1-probability-event-go-no-go-runbook.md"),
    Path("docs/phase1/probability-event-readonly-supabase-principles.md"),
    Path("docs/data_dictionary/phase1-research-decision-objects.md"),
    Path("docs/playbooks/phase1-specialist-team-playbooks.md"),
    Path("docs/strategy/phase1-probability-event-filtering-workflow.md"),
)
```

Do not assert over the full `PHASE1_NEW_DOC_PATHS` corpus and do not modify any out-of-allowlist Phase 1 document.

The two tests also enforce the supporting contract instead of checking only one sentence:

- the operator stack/go-no-go/filtering documents contain `YES side probability = forecast_probability` and `NO side probability = 1 - forecast_probability`;
- diagnostics/playbook text states actual YES target `1` and actual NO target `0` for calibration;
- calibration-facing living docs state that persisted `actual_outcome` is the string enum `yes` or `no`, mapped as `yes -> Decimal target 1` and `no -> Decimal target 0`, independently of `selected_side`;
- `strategy-first-roadmap.md`, `strategy-candidate-decision-matrix.md`, and `probability-event-readonly-supabase-principles.md` no longer describe the canonical event forecast itself as `side-aware`;
- side-aware executable prices, side probability, costs, and edge remain valid and are not globally banned.

Pin the persisted-outcome wording exactly. `tests/test_team_diagnostics_docs.py` requires it in `docs/team-diagnostics-readonly.md` and `docs/operators/phase1-strategy-stack-walkthrough.md`; `tests/test_phase1_docs_required_terms.py` requires it in `docs/playbooks/phase1-specialist-team-playbooks.md`:

```text
Persisted `actual_outcome` uses the string enum `yes` or `no`; calibration maps `yes` to Decimal target `1` and `no` to Decimal target `0`; `selected_side` never changes that mapping.
```

- [ ] **Step 2: Run documentation RED**

```bash
.venv/bin/python -m pytest -q tests/test_team_diagnostics_docs.py::test_team_probability_docs_freeze_canonical_pyes_orientation tests/test_phase1_docs_required_terms.py::test_probability_event_docs_freeze_canonical_pyes_orientation
```

Expected: both tests fail against the pre-change documents.

- [ ] **Step 3: Correct the living documentation**

Replace statements that call `forecast_probability` side-aware. Keep executable-side formulas explicit:

```text
YES side probability = forecast_probability
NO side probability = 1 - forecast_probability
```

State that calibration compares canonical `P(YES)` with an actual YES target of `1` and actual NO target of `0`, regardless of which paper-review side was selected. In the three calibration-facing living docs named in Step 1, state explicitly that persisted `actual_outcome` is string `yes`/`no`, maps to Decimal targets `yes -> 1` and `no -> 0`, and is independent of `selected_side`. Retain valid `side-aware` wording for executable prices, costs, side probability, and edge; only the event forecast itself is always `P(YES)`.

- [ ] **Step 4: Run documentation GREEN tests**

```bash
.venv/bin/python -m pytest -q tests/test_team_diagnostics_docs.py tests/test_phase1_docs_required_terms.py tests/test_team_forecast_supabase_runbook.py
```

### Task 5: Full Node Verification, Claude Review, Commit, And Push

**Files:** All exact allowlisted paths above; no others.

- [ ] **Step 1: Run focused invariants**

```bash
.venv/bin/python -m pytest -q \
  tests/test_team_forecast_packet.py \
  tests/test_paper_probability_side_edge.py \
  tests/test_team_forecast_calibration.py \
  tests/test_calibration_drift_monitor.py \
  tests/test_team_forecast_db_row.py \
  tests/test_team_forecast_schema.py \
  tests/test_team_forecast_supabase_runbook.py \
  tests/test_team_forecast_migration_safety.py \
  tests/test_team_diagnostics_docs.py \
  tests/test_phase1_docs_required_terms.py \
  tests/test_team_event_template_performance.py \
  tests/test_team_memory_synthesis.py \
  tests/test_team_performance_summary.py \
  tests/test_phase1_live_surface_guard.py \
  tests/test_phase1_no_review_scratch_references.py \
  tests/test_database_persistence_iron_rule.py \
  tests/test_supabase_durable_only_scope.py
```

- [ ] **Step 2: Run repository-wide gates**

```bash
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m pytest --collect-only -q
.venv/bin/python -m pytest -q
codegraph sync .
git diff --check
```

- [ ] **Step 3: Stage an exact manifest and commit**

Define the exact manifest, then stage only it:

```bash
set -euo pipefail
NODE_BASE=5bc403e773176dd23cd6c324f40e67607458ee09
NODE_PATHS=(
  docs/superpowers/plans/2026-07-13-btc-domain-evidence-aggregation-foundation.md
  docs/superpowers/plans/2026-07-13-team-forecast-probability-yes-contract.md
  src/polymarket_alpha_lab/team_forecast_packet.py
  src/polymarket_alpha_lab/team_forecast_db_row.py
  src/polymarket_alpha_lab/paper_probability_side_edge.py
  supabase/migrations/20260713000000_team_forecast_probability_yes_contract.sql
  tests/test_team_forecast_packet.py
  tests/test_paper_probability_side_edge.py
  tests/test_team_forecast_calibration.py
  tests/test_calibration_drift_monitor.py
  tests/test_team_forecast_db_row.py
  tests/test_team_forecast_schema.py
  tests/test_team_forecast_supabase_runbook.py
  tests/test_team_forecast_migration_safety.py
  tests/test_team_diagnostics_docs.py
  tests/test_phase1_docs_required_terms.py
  tests/test_team_event_template_performance.py
  tests/test_team_memory_synthesis.py
  docs/team-forecast-supabase-runbook.md
  docs/team-forecast-migration-safety.md
  docs/team-agent-operating-model.md
  docs/team-agent-framework.md
  docs/team-diagnostics-readonly.md
  docs/playbooks/phase1-specialist-team-playbooks.md
  docs/operators/phase1-strategy-stack-walkthrough.md
  docs/operators/phase1-probability-event-go-no-go-runbook.md
  docs/strategy/phase1-probability-event-filtering-workflow.md
  docs/strategy-first-roadmap.md
  docs/strategy-candidate-decision-matrix.md
  docs/phase1/probability-event-readonly-supabase-principles.md
  docs/data_dictionary/phase1-research-decision-objects.md
)
git add -- "${NODE_PATHS[@]}"
diff -u \
  <(printf '%s\n' "${NODE_PATHS[@]}" | LC_ALL=C sort) \
  <(git diff --cached --name-only | LC_ALL=C sort)
git diff --cached --check

SENSITIVE_STAGED_PATHS=()
for path in "${NODE_PATHS[@]}"; do
  ADDED_STAGED="$(git diff --cached --unified=0 -- "$path" \
    | sed -n '/^+++ /d; /^+/s/^+//p')"
  if rg -q '(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(^|[^[:alnum:]_])sk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|postgres(ql)?://[^[:space:]/]+:[^[:space:]@]+@)' <<< "$ADDED_STAGED"; then
    RG_STATUS=0
  else
    RG_STATUS=$?
  fi
  case "$RG_STATUS" in
    0)
      printf 'credential-shaped staged addition in %s\n' "$path" >&2
      exit 1
      ;;
    1)
      ;;
    *)
      printf 'credential-shaped staged scan failed for %s with rg status %s\n' "$path" "$RG_STATUS" >&2
      exit "$RG_STATUS"
      ;;
  esac

  if rg -qi '\b(api[_-]?key|secret|token|password|passwd|cookie|authorization|bearer|private[_ -]?key|seed phrase|mnemonic|wallet|account[_ -]?(id|address))\b' <<< "$ADDED_STAGED"; then
    RG_STATUS=0
  else
    RG_STATUS=$?
  fi
  case "$RG_STATUS" in
    0)
      SENSITIVE_STAGED_PATHS+=("$path")
      ;;
    1)
      ;;
    *)
      printf 'sensitive-field staged scan failed for %s with rg status %s\n' "$path" "$RG_STATUS" >&2
      exit "$RG_STATUS"
      ;;
  esac
done
printf 'staged sensitive-field paths requiring redacted classification:\n'
printf '  %s\n' "${SENSITIVE_STAGED_PATHS[@]}"

STAGED_ADDITIONS="$(git diff --cached --unified=0 \
  | sed -n '/^+++ /d; /^+/s/^+//p')"
if rg -ni '\b(live trading|order submission|submit order|cancel order|replace order|sign order|wallet|private key|hosted account|account authentication|exchange mutation|order mutation)\b' <<< "$STAGED_ADDITIONS"; then
  RG_STATUS=0
else
  RG_STATUS=$?
fi
case "$RG_STATUS" in
  0)
    printf 'Classify every printed staged readonly-boundary match as a negative assertion before committing.\n'
    ;;
  1)
    ;;
  *)
    printf 'staged readonly-boundary scan failed with rg status %s\n' "$RG_STATUS" >&2
    exit "$RG_STATUS"
    ;;
esac

if rg -ni '\b(sqlite|duckdb|redis|mongodb|sqlalchemy|jsonl|file-backed|postgres|supabase|dsn|validate_local_postgres_dsn)\b' <<< "$STAGED_ADDITIONS"; then
  RG_STATUS=0
else
  RG_STATUS=$?
fi
case "$RG_STATUS" in
  0)
    printf 'Classify every printed staged persistence match; only local Supabase/Postgres and negative alternate-backend assertions are allowed.\n'
    ;;
  1)
    ;;
  *)
    printf 'staged persistence scan failed with rg status %s\n' "$RG_STATUS" >&2
    exit "$RG_STATUS"
    ;;
esac

.venv/bin/python -m pytest -q \
  tests/test_phase1_live_surface_guard.py \
  tests/test_phase1_no_review_scratch_references.py \
  tests/test_database_persistence_iron_rule.py \
  tests/test_supabase_durable_only_scope.py

git commit -m "Document canonical team forecast probability semantics"
diff -u \
  <(printf '%s\n' "${NODE_PATHS[@]}" | LC_ALL=C sort) \
  <(git diff --name-only "$NODE_BASE..HEAD" | LC_ALL=C sort)
```

No broad `git add tests`, `git add docs`, or `git add .` is allowed.

- [ ] **Step 4: Run range-based safety scans**

Run the exact committed-path and added-line scans:

```bash
set -euo pipefail
NODE_BASE=5bc403e773176dd23cd6c324f40e67607458ee09
RANGE_PATHS_TEXT="$(git diff --name-only "$NODE_BASE..HEAD")"
test -n "$RANGE_PATHS_TEXT"
mapfile -t RANGE_PATHS <<< "$RANGE_PATHS_TEXT"
test "${#RANGE_PATHS[@]}" -gt 0

SENSITIVE_FIELD_PATHS=()
for path in "${RANGE_PATHS[@]}"; do
  ADDED_FOR_PATH="$(
    git diff --unified=0 "$NODE_BASE..HEAD" -- "$path" |
      sed -n '/^+++ /d; /^+/s/^+//p'
  )"
  if rg -q '(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(^|[^[:alnum:]_])sk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|postgres(ql)?://[^[:space:]/]+:[^[:space:]@]+@)' <<< "$ADDED_FOR_PATH"; then
    RG_STATUS=0
  else
    RG_STATUS=$?
  fi
  case "$RG_STATUS" in
    0)
      printf 'credential-shaped range addition in %s\n' "$path" >&2
      exit 1
      ;;
    1)
      ;;
    *)
      printf 'credential-shaped range scan failed for %s with rg status %s\n' "$path" "$RG_STATUS" >&2
      exit "$RG_STATUS"
      ;;
  esac

  if rg -qi '\b(api[_-]?key|secret|token|password|passwd|cookie|authorization|bearer|private[_ -]?key|seed phrase|mnemonic|wallet|account[_ -]?(id|address))\b' <<< "$ADDED_FOR_PATH"; then
    RG_STATUS=0
  else
    RG_STATUS=$?
  fi
  case "$RG_STATUS" in
    0)
      SENSITIVE_FIELD_PATHS+=("$path")
      ;;
    1)
      ;;
    *)
      printf 'sensitive-field range scan failed for %s with rg status %s\n' "$path" "$RG_STATUS" >&2
      exit "$RG_STATUS"
      ;;
  esac
done
printf 'sensitive-field paths requiring redacted classification:\n'
printf '  %s\n' "${SENSITIVE_FIELD_PATHS[@]}"

READONLY_ADDITIONS="$(
  git diff --unified=0 "$NODE_BASE..HEAD" -- src docs tests |
    sed -n '/^+++ /d; /^+/s/^+//p'
)"
if rg -ni '\b(live trading|order submission|submit order|cancel order|replace order|sign order|wallet|private key|hosted account|account authentication|exchange mutation|order mutation)\b' <<< "$READONLY_ADDITIONS"; then
  RG_STATUS=0
else
  RG_STATUS=$?
fi
case "$RG_STATUS" in
  0)
    printf 'Classify every printed readonly-boundary match as a negative assertion or fix it.\n'
    ;;
  1)
    ;;
  *)
    printf 'readonly-boundary range scan failed with rg status %s\n' "$RG_STATUS" >&2
    exit "$RG_STATUS"
    ;;
esac

PERSISTENCE_ADDITIONS="$(
  git diff --unified=0 "$NODE_BASE..HEAD" -- src docs tests supabase |
    sed -n '/^+++ /d; /^+/s/^+//p'
)"
if rg -ni '\b(sqlite|duckdb|redis|mongodb|sqlalchemy|jsonl|file-backed|postgres|supabase|dsn|validate_local_postgres_dsn)\b' <<< "$PERSISTENCE_ADDITIONS"; then
  RG_STATUS=0
else
  RG_STATUS=$?
fi
case "$RG_STATUS" in
  0)
    printf 'Classify every printed persistence match; only local Supabase/Postgres and negative alternate-backend assertions are allowed.\n'
    ;;
  1)
    ;;
  *)
    printf 'persistence range scan failed with rg status %s\n' "$RG_STATUS" >&2
    exit "$RG_STATUS"
    ;;
esac
```

Each `git diff | sed` added-line stream is captured by a standalone assignment before `rg`, so `set -euo pipefail` stops on either upstream failure. Every `rg` invocation treats status `0` as a match and `1` as no match; any other status is a scan failure. Credential-shaped and sensitive-field scans remain quiet and print only the affected path, never the matched value. Do not use unconditional success fallbacks to mask findings. Documentation/test boundary matches outside these high-confidence patterns are classified explicitly in the Claude packet.

- [ ] **Step 5: Obtain mandatory Claude Code PASS**

Review the plan and exact committed range `NODE_BASE..HEAD`, including the changed-path manifest, full patch, focused/full test results, compile result, migration smoke result, CodeGraph sync, diff check, readonly boundary scan, persistence scan, and secret scan.

Required settings:

```text
model: claude-opus-4-8
effort: max
permission: read-only
fast mode: off
verdict: explicit PASS or REVISE
```

An empty result is not approval. Every `REVISE` is fixed with TDD, all gates are rerun, and the complete `NODE_BASE..HEAD` range is reviewed again.

After a `REVISE`, stage only the allowlisted fix subset, rerun staged diff/credential/boundary/persistence gates, commit the fix, rerun focused and full verification, regenerate the complete `NODE_BASE..HEAD` packet, and obtain a new explicit `PASS`. The full-range changed-path manifest must still equal the original exact node allowlist.

- [ ] **Step 6: Push without force**

Immediately verify remote `main` still equals `NODE_BASE`, run an authenticated dry run with the absolute common-git-dir credential helper, push `HEAD:refs/heads/main`, and verify remote `main` equals local `HEAD`:

```bash
set -euo pipefail
NODE_BASE=5bc403e773176dd23cd6c324f40e67607458ee09
test -z "$(git status --porcelain --untracked-files=no)"
git diff --cached --quiet
test -z "$(git ls-files --others --exclude-standard -- src tests docs supabase)"
COMMON_GIT_DIR="$(git rev-parse --git-common-dir)"
GIT_AUTH=(-c credential.helper= -c "credential.helper=store --file=$COMMON_GIT_DIR/github-credentials")
GIT_TERMINAL_PROMPT=0 git "${GIT_AUTH[@]}" fetch --no-tags origin refs/heads/main
CURRENT_REMOTE="$(git rev-parse FETCH_HEAD)"
if [ "$CURRENT_REMOTE" != "$NODE_BASE" ]; then
  git rebase FETCH_HEAD
  printf 'Remote main moved. Stop before push and rerun migration smoke, focused/full tests, compile, CodeGraph, scans, commit-range review, and Claude PASS from the new base %s.\n' "$CURRENT_REMOTE" >&2
  exit 75
fi
test "$(GIT_TERMINAL_PROMPT=0 git "${GIT_AUTH[@]}" ls-remote --exit-code origin refs/heads/main | cut -f1)" = "$NODE_BASE"
GIT_TERMINAL_PROMPT=0 git "${GIT_AUTH[@]}" push --dry-run origin HEAD:refs/heads/main
GIT_TERMINAL_PROMPT=0 git "${GIT_AUTH[@]}" push origin HEAD:refs/heads/main
test "$(GIT_TERMINAL_PROMPT=0 git "${GIT_AUTH[@]}" ls-remote --exit-code origin refs/heads/main | cut -f1)" = "$(git rev-parse HEAD)"
```

After push, reclaim completed workers and continue to the pure evidence aggregation core node.
