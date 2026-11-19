-- Papersim model registry (MO-2 of the model auto-optimization line,
-- docs/handoffs/2026-11-19-audit-optimizer/04-optimizer-design.md section 9
-- and 05-development-plan.md node MO-2): six insert-only tables for the
-- optimizer's durable evidence -- optimization runs, model specs,
-- promotion decisions, role events, shadow predictions and optimizer
-- freeze/unfreeze control events. Same invariants as every managed
-- papersim table: append-only (reject_mutation triggers below), the
-- three-true paper boundary columns, writer-side idempotency keys and
-- explicit pal_app select+insert grants. Paper evidence storage only;
-- this schema grants no order, account or execution authorization.
-- MO-2 note: shadow_predictions is CREATED HERE but written by MO-7
-- (the shadow-parallel node); MO-2 registers no writer for it.
-- Cross-table topology: model_specs.spec_sha256 is the identity anchor;
-- specs reference runs (optimization_id FK), never the reverse -- a
-- run's active_specs mapping stays plain jsonb (hash-checked shape,
-- proven to exist by the application inside the write transaction, the
-- same pattern as candidate_marks.snapshot_sha256 and the record_scan
-- snapshot_refs proof), so no run<->spec FK cycle exists.
-- Drift must fail loudly: no IF NOT EXISTS, published bytes untouched.
begin;

-- One optimization run on a fixed data cutoff (04 section 3): the
-- outcome is exactly one of the four closed tokens; per-trial scores
-- and per-match predictions are megabyte payloads registered in
-- papersim.snapshots and referenced here by digest only.
create table papersim.optimization_runs (
  optimization_id bigint generated always as identity primary key,
  optimization_key text not null unique,
  family         text not null,
  lineages       jsonb not null default '[]'::jsonb
    check (jsonb_typeof(lineages) = 'array'),
  data_cutoff    timestamptz not null check (isfinite(data_cutoff)),
  dataset_summary jsonb not null default '{}'::jsonb,
  snapshot_refs  jsonb not null default '[]'::jsonb
    check (jsonb_typeof(snapshot_refs) = 'array'),
  search_space   jsonb not null default '{}'::jsonb,
  rule_version   text not null,
  random_seed    bigint not null,
  trial_count    integer not null check (trial_count >= 0),
  budget         jsonb not null default '{}'::jsonb,
  active_specs   jsonb not null default '{}'::jsonb,
  outcome        text not null check (outcome in ('CANDIDATE_REGISTERED',
      'NO_IMPROVEMENT', 'INSUFFICIENT_DATA', 'RUN_FAILED')),
  scorecard      jsonb not null default '{}'::jsonb,
  artifacts_sha256 text check (artifacts_sha256 is null
      or artifacts_sha256 ~ '^[a-f0-9]{64}$'),
  code_commit    text not null check (code_commit ~ '^[0-9a-f]{40}$'),
  paper_only  boolean not null default true check (paper_only is true),
  report_only boolean not null default true check (report_only is true),
  readonly    boolean not null default true check (readonly is true)
);
create index optimization_runs_family_idx on papersim.optimization_runs(family, data_cutoff);

-- One model spec: identity is the MO-1 canonical-text SHA-256. The
-- database re-derives the digest (convert_to ... 'UTF8' mirrors the
-- Python .encode('utf-8') byte stream) so a forged or drifted pair of
-- (spec_sha256, canonical_text) cannot land. params/envelope are the
-- rebuild payload (plain JSON, sorted keys) and never the identity.
create table papersim.model_specs (
  spec_sha256 text primary key check (spec_sha256 ~ '^[a-f0-9]{64}$'),
  canonical_text text not null
    check (encode(sha256(convert_to(canonical_text, 'UTF8')), 'hex') = spec_sha256),
  family             text not null,
  procedure_version  text not null,
  model_version      text not null,
  legacy_model_version text,
  params             jsonb not null,
  envelope           jsonb not null
    check (jsonb_typeof(envelope) = 'array'),
  data_contract      text not null,
  source             text not null
    check (source in ('frozen_baseline', 'optimizer', 'manual')),
  parent_spec_sha256 text references papersim.model_specs(spec_sha256),
  optimization_id    bigint references papersim.optimization_runs(optimization_id),
  code_commit        text not null check (code_commit ~ '^[0-9a-f]{40}$'),
  paper_only  boolean not null default true check (paper_only is true),
  report_only boolean not null default true check (report_only is true),
  readonly    boolean not null default true check (readonly is true)
);

-- One promotion decision (04 section 3): the rule-governed judgement of
-- candidate -> active or active -> guard rollback, with its evidence
-- watermark and metrics. A BLOCKED conclusion must carry its reason.
create table papersim.promotion_decisions (
  decision_id bigint generated always as identity primary key,
  decision_key text not null unique,
  lineage      text not null,
  decision_class text not null
    check (decision_class in ('FORWARD', 'ROLLBACK', 'MANUAL')),
  candidate_spec_sha256 text references papersim.model_specs(spec_sha256),
  active_spec_sha256 text not null references papersim.model_specs(spec_sha256),
  rule_version text not null,
  look_number  integer not null check (look_number >= 1),
  evidence_watermark jsonb not null default '{}'::jsonb,
  metrics      jsonb not null default '{}'::jsonb,
  conclusion   text not null check (conclusion in ('PROMOTE',
      'PROMOTE_PENDING_CONFIRM', 'CONTINUE', 'RETIRE', 'ROLLBACK', 'HOLD',
      'BLOCKED')),
  block_reason text
    check ((conclusion = 'BLOCKED') = (block_reason is not null)),
  decided_by   text not null,
  decided_at   timestamptz not null check (isfinite(decided_at)),
  paper_only  boolean not null default true check (paper_only is true),
  report_only boolean not null default true check (report_only is true),
  readonly    boolean not null default true check (readonly is true)
);
create index promotion_decisions_lineage_idx
    on papersim.promotion_decisions(lineage, decided_at);

-- One role change event of one lineage (04 section 3 roles): the event
-- log the pure registry folds into the current active/baseline/
-- candidates/guard view. A CANDIDATE registration must carry its
-- forward plan (pre-registration, design section 5 measure 7). A
-- promotion's two handover events (old active -> GUARD, candidate ->
-- ACTIVE) are one transaction at the writer; the database keeps them
-- as two rows sharing effective_at.
create table papersim.model_role_events (
  role_event_id bigint generated always as identity primary key,
  role_event_key text not null unique,
  lineage     text not null,
  spec_sha256 text not null references papersim.model_specs(spec_sha256),
  role        text not null
    check (role in ('ACTIVE', 'BASELINE', 'CANDIDATE', 'GUARD', 'RETIRED')),
  effective_at timestamptz not null check (isfinite(effective_at)),
  reason      text not null,
  forward_plan jsonb
    check (role <> 'CANDIDATE' or forward_plan is not null),
  decision_id bigint references papersim.promotion_decisions(decision_id),
  actor       text not null,
  code_commit text not null check (code_commit ~ '^[0-9a-f]{40}$'),
  paper_only  boolean not null default true check (paper_only is true),
  report_only boolean not null default true check (report_only is true),
  readonly    boolean not null default true check (readonly is true)
);
create index model_role_events_lineage_idx
    on papersim.model_role_events(lineage, effective_at, role_event_id);

-- One shadow prediction of a non-active spec against one ACTIVE
-- candidate row (04 section 9 closing paragraph): the shadow hangs on
-- the row the active spec wrote for the same market in the same scan,
-- so market identity, decision/close prices and the final outcome are
-- reused from the existing three tables and never duplicated here.
-- Written by MO-7; MO-2 only creates the table.
create table papersim.shadow_predictions (
  shadow_prediction_id bigint generated always as identity primary key,
  shadow_key   text not null unique,
  candidate_id bigint not null references papersim.candidates(candidate_id),
  spec_sha256  text not null references papersim.model_specs(spec_sha256),
  role_at_prediction text not null
    check (role_at_prediction in ('BASELINE', 'CANDIDATE', 'GUARD')),
  probability numeric check (probability is null
      or (probability >= 0 and probability <= 1)),
  variant_quotes jsonb not null default '[]'::jsonb
    check (jsonb_typeof(variant_quotes) = 'array'),
  refusal_reason text,
  gate_pass   boolean not null,
  gate_reason text,
  hypothetical_fill_price numeric check (hypothetical_fill_price is null
      or (hypothetical_fill_price > 0 and hypothetical_fill_price < 1)),
  predicted_at timestamptz not null check (isfinite(predicted_at)),
  paper_only  boolean not null default true check (paper_only is true),
  report_only boolean not null default true check (report_only is true),
  readonly    boolean not null default true check (readonly is true)
);
create index shadow_predictions_candidate_idx
    on papersim.shadow_predictions(candidate_id, spec_sha256);

-- One optimizer freeze or unfreeze (04 section 11 one-switch freeze):
-- scope is global or one lineage; an UNFREEZE can never be decided by
-- an automatic rule, enforced here at the row level in addition to the
-- application-side registry check.
create table papersim.optimizer_control_events (
  optimizer_control_id bigint generated always as identity primary key,
  optimizer_control_key text not null unique,
  scope        text not null check (scope in ('GLOBAL', 'LINEAGE')),
  lineage      text check ((scope = 'LINEAGE') = (lineage is not null)),
  control_kind text not null check (control_kind in ('FREEZE', 'UNFREEZE')),
  reason       text not null,
  actor        text not null
    check (control_kind <> 'UNFREEZE' or actor not like 'auto:%'),
  occurred_at  timestamptz not null check (isfinite(occurred_at)),
  paper_only  boolean not null default true check (paper_only is true),
  report_only boolean not null default true check (report_only is true),
  readonly    boolean not null default true check (readonly is true)
);
create index optimizer_control_events_scope_idx
    on papersim.optimizer_control_events(scope, lineage, occurred_at);

-- The same uniform append-only proof surface as every managed table.
create trigger immutable_optimization_runs before update or delete on papersim.optimization_runs
  for each row execute function papersim_private.reject_mutation();
create trigger immutable_model_specs before update or delete on papersim.model_specs
  for each row execute function papersim_private.reject_mutation();
create trigger immutable_promotion_decisions before update or delete on papersim.promotion_decisions
  for each row execute function papersim_private.reject_mutation();
create trigger immutable_model_role_events before update or delete on papersim.model_role_events
  for each row execute function papersim_private.reject_mutation();
create trigger immutable_shadow_predictions before update or delete on papersim.shadow_predictions
  for each row execute function papersim_private.reject_mutation();
create trigger immutable_optimizer_control_events before update or delete on papersim.optimizer_control_events
  for each row execute function papersim_private.reject_mutation();
create trigger no_truncate_optimization_runs before truncate on papersim.optimization_runs
  for each statement execute function papersim_private.reject_mutation();
create trigger no_truncate_model_specs before truncate on papersim.model_specs
  for each statement execute function papersim_private.reject_mutation();
create trigger no_truncate_promotion_decisions before truncate on papersim.promotion_decisions
  for each statement execute function papersim_private.reject_mutation();
create trigger no_truncate_model_role_events before truncate on papersim.model_role_events
  for each statement execute function papersim_private.reject_mutation();
create trigger no_truncate_shadow_predictions before truncate on papersim.shadow_predictions
  for each statement execute function papersim_private.reject_mutation();
create trigger no_truncate_optimizer_control_events before truncate on papersim.optimizer_control_events
  for each statement execute function papersim_private.reject_mutation();

-- Explicit grants for the application role on the new objects only
-- (same style as the M1/M2 grant blocks; the V1 post-migration GRANTS
-- never touch this schema).
grant select, insert on papersim.optimization_runs, papersim.model_specs,
    papersim.promotion_decisions, papersim.model_role_events,
    papersim.shadow_predictions, papersim.optimizer_control_events to pal_app;
grant usage, select on all sequences in schema papersim to pal_app;
commit;
