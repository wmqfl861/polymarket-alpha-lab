-- Explicit uncapped local CLI authorization. No fabricated money or provider count.
-- Preserve all original67 migration files, capped records and ordinal identities.
create table research_capture.uncapped_model_authorizations (
  authorization_id text primary key check (authorization_id ~ '^[A-Za-z0-9_.:-]{1,128}$'),
  payload text not null check (octet_length(payload) between 1 and 32768),
  payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
  created_at timestamptz not null check (isfinite(created_at)),
  paper_only boolean not null default true check (paper_only is true),
  report_only boolean not null default true check (report_only is true),
  readonly boolean not null default true check (readonly is true),
  check (encode(sha256(convert_to(payload,'UTF8')),'hex') = payload_sha256)
);
create function research_capture.stamp_uncapped_authorization() returns trigger
language plpgsql set search_path = pg_catalog as $$
declare body jsonb; item jsonb; ids text[] := '{}'; expiry timestamptz;
begin
  perform pg_advisory_xact_lock(hashtextextended('polymarket/uncapped/' || new.authorization_id,0));
  new.created_at := clock_timestamp();
  body := new.payload::jsonb;
  expiry := (body ->> 'expires_at')::timestamptz;
  if (jsonb_typeof(body) = 'object'
      and (select count(*) from jsonb_object_keys(body)) = 13
      and body ->> 'schema_version' = 'research-uncapped-authorization-v1'
      and body ->> 'authorization_id' = new.authorization_id
      and body ->> 'provider_id' = 'codex-cli'
      and body ->> 'output_limit_mode' = 'observed_after_response'
      and jsonb_typeof(body -> 'model_id') = 'string'
      and body ->> 'model_id' ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
      and jsonb_typeof(body -> 'profile_sha256') = 'string'
      and jsonb_typeof(body -> 'expires_at') = 'string'
      and body ->> 'profile_sha256' ~ '^[a-f0-9]{64}$'
      and jsonb_typeof(body -> 'max_message_bytes') = 'number'
      and body ->> 'max_message_bytes' ~ '^[0-9]+$'
      and (body ->> 'max_message_bytes')::integer between 1 and 2000000
      and body -> 'uncapped_cost_approved' = 'true'::jsonb
      and body -> 'paper_only' = 'true'::jsonb and body -> 'report_only' = 'true'::jsonb
      and body -> 'readonly' = 'true'::jsonb and isfinite(expiry) and expiry > new.created_at
      and jsonb_typeof(body -> 'request_keys') = 'array'
      and jsonb_array_length(body -> 'request_keys') between 1 and 100) is not true then
    raise exception 'research_uncapped_authorization_invalid';
  end if;
  for item in select value from jsonb_array_elements(body -> 'request_keys') loop
    if (jsonb_typeof(item) = 'array' and jsonb_array_length(item) = 2
        and jsonb_typeof(item -> 0) = 'string' and jsonb_typeof(item -> 1) = 'string'
        and item ->> 0 ~ '^[A-Za-z0-9_.:-]{1,128}$' and item ->> 1 ~ '^[a-f0-9]{64}$') is not true
        or item ->> 0 = any(ids) then
      raise exception 'research_uncapped_roster_invalid';
    end if;
    ids := array_append(ids,item ->> 0);
  end loop;
  return new;
end $$;
create trigger stamp_uncapped_authorization before insert on research_capture.uncapped_model_authorizations
  for each row execute function research_capture.stamp_uncapped_authorization();

-- The original (record_id,call_number) key remains the shared uniqueness authority.
-- A null charge is explicitly unknown/not capped. Zero is never a fake price.
alter table research_capture.model_call_reservations
  add column authorization_id text references research_capture.uncapped_model_authorizations(authorization_id),
  alter column budget_id drop not null,
  alter column reservation_number drop not null,
  alter column reserved_micros drop not null,
  add constraint model_call_exact_mode check (
    (authorization_id is null and budget_id is not null and reservation_number is not null and reserved_micros is not null)
    or (authorization_id is not null and budget_id is null and reservation_number is null and reserved_micros is null));

create table research_capture.codex_invocation_outcomes (
  record_id text not null,
  call_number integer not null,
  completed_at timestamptz not null check (isfinite(completed_at)),
  status text not null check (status in ('validated','failed')),
  input_tokens integer,
  cached_input_tokens integer,
  cache_write_input_tokens integer,
  output_tokens integer,
  reasoning_output_tokens integer,
  output_sha256 text,
  paper_only boolean not null default true check (paper_only is true),
  report_only boolean not null default true check (report_only is true),
  readonly boolean not null default true check (readonly is true),
  primary key (record_id,call_number),
  foreign key (record_id,call_number) references research_capture.model_call_reservations(record_id,call_number),
  check ((status='failed' and input_tokens is null and cached_input_tokens is null
      and cache_write_input_tokens is null and output_tokens is null
      and reasoning_output_tokens is null and output_sha256 is null)
    or (status='validated' and input_tokens is not null and cached_input_tokens is not null
      and cache_write_input_tokens is not null and output_tokens is not null
      and reasoning_output_tokens is not null and output_sha256 is not null
      and input_tokens between 0 and 1000000 and output_tokens between 0 and 1000000
      and cached_input_tokens between 0 and input_tokens
      and cache_write_input_tokens between 0 and input_tokens
      and reasoning_output_tokens between 0 and output_tokens
      and input_tokens+output_tokens between 1 and 1000000
      and output_sha256 ~ '^[a-f0-9]{64}$'))
);

create or replace function research_capture.stamp_model_call() returns trigger
language plpgsql set search_path = pg_catalog as $$
declare policy jsonb; claimed research_capture.execution_claims%rowtype;
        count_so_far integer; last_time timestamptz; last_call integer;
begin
  if new.authorization_id is not null then
    perform pg_advisory_xact_lock(hashtextextended('polymarket/uncapped/' || new.authorization_id,0));
    select payload::jsonb into policy from research_capture.uncapped_model_authorizations
      where authorization_id=new.authorization_id;
    select * into claimed from research_capture.execution_claims where record_id=new.record_id;
    new.reserved_at := clock_timestamp();
    if new.budget_id is not null or new.reserved_micros is not null or new.reservation_number is not null
      or (claimed.request_sha256 = new.request_sha256
        and claimed.model_id = policy ->> 'model_id'
        and claimed.protocol_version = 'codex-cli-0.155.1-pal-v1'
        and claimed.team_id in ('crypto_btc','crypto_eth')
        and new.reserved_at >= claimed.claimed_at
        and new.reserved_at < (claimed.request_payload::jsonb ->> 'forecast_cutoff_at')::timestamptz
        and new.reserved_at < (policy ->> 'expires_at')::timestamptz
        and new.message_bytes <= (policy ->> 'max_message_bytes')::integer
        and new.max_output_tokens <= (claimed.request_payload::jsonb #>> '{limits,max_output_tokens}')::integer
        and new.call_number <= (claimed.request_payload::jsonb #>> '{limits,max_model_calls}')::integer) is not true
      or not exists (select 1 from jsonb_array_elements(policy -> 'request_keys') k(value)
        where k.value ->> 0=new.record_id and k.value ->> 1=new.request_sha256)
      or exists (select 1 from research_capture.attempts where record_id=new.record_id)
      or exists (select 1 from research_capture.model_call_reservations r
        where r.record_id=new.record_id and r.authorization_id is distinct from new.authorization_id) then
      raise exception 'research_uncapped_call_not_allowed';
    end if;
    select coalesce(max(call_number),0),max(reserved_at) into last_call,last_time
      from research_capture.model_call_reservations where record_id=new.record_id;
    if new.call_number <> last_call+1 or (last_time is not null and new.reserved_at < last_time)
      or (last_call>0 and not exists (select 1 from research_capture.codex_invocation_outcomes
        where record_id=new.record_id and call_number=last_call and status='validated')) then
      raise exception 'research_uncapped_prior_call_incomplete';
    end if;
    return new;
  end if;
  if exists (select 1 from research_capture.model_call_reservations
      where record_id=new.record_id and authorization_id is not null) then
    raise exception 'research_uncapped_capped_mode_conflict';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('polymarket/model_budget/' || new.budget_id,0));
  select payload::jsonb into policy from research_capture.model_budgets where budget_id=new.budget_id;
  select * into claimed from research_capture.execution_claims where record_id=new.record_id;
  new.reserved_at := clock_timestamp();
  if (claimed.request_sha256 = new.request_sha256
      and claimed.model_id = policy ->> 'model_id'
      and claimed.team_id in ('crypto_btc','crypto_eth')
      and new.reserved_at >= claimed.claimed_at
      and new.reserved_at < (claimed.request_payload::jsonb ->> 'forecast_cutoff_at')::timestamptz
      and new.reserved_at < (policy ->> 'expires_at')::timestamptz
      and new.message_bytes <= (policy ->> 'max_message_bytes')::integer
      and new.max_output_tokens <= (policy ->> 'max_output_tokens')::integer
      and new.max_output_tokens <= (claimed.request_payload::jsonb #>> '{limits,max_output_tokens}')::integer
      and new.call_number <= (claimed.request_payload::jsonb #>> '{limits,max_model_calls}')::integer) is not true
      or not exists (select 1 from jsonb_array_elements(policy -> 'request_keys') k(value)
        where k.value ->> 0 = new.record_id and k.value ->> 1 = new.request_sha256)
      or exists (select 1 from research_capture.attempts where record_id=new.record_id) then
    raise exception 'research_budget_call_not_allowed';
  end if;
  select coalesce(max(reservation_number),0),max(reserved_at) into count_so_far,last_time
    from research_capture.model_call_reservations where budget_id=new.budget_id;
  select coalesce(max(call_number),0) into last_call
    from research_capture.model_call_reservations where record_id=new.record_id;
  new.reservation_number := count_so_far+1;
  new.reserved_micros := (policy ->> 'per_call_micros')::bigint;
  if new.call_number <> last_call+1
      or new.reservation_number > (policy ->> 'max_calls')::integer
      or new.reserved_micros * new.reservation_number > (policy ->> 'total_micros')::bigint
      or (last_time is not null and new.reserved_at < last_time) then
    raise exception 'research_budget_allowance_exhausted';
  end if;
  -- The unique budget/sequence constraint also rejects concurrent stale-snapshot
  -- insertions; there is no mutable/refundable counter or lease to reclaim.
  return new;
end $$;

create function research_capture.stamp_codex_outcome() returns trigger
language plpgsql set search_path = pg_catalog as $$
declare reserved research_capture.model_call_reservations%rowtype;
begin
  select * into reserved from research_capture.model_call_reservations
    where record_id=new.record_id and call_number=new.call_number;
  new.completed_at := clock_timestamp();
  if reserved.authorization_id is null or new.completed_at < reserved.reserved_at
      or (new.status='validated' and new.output_tokens > reserved.max_output_tokens) then
    raise exception 'research_codex_outcome_not_allowed';
  end if;
  return new;
end $$;
create trigger stamp_codex_outcome before insert on research_capture.codex_invocation_outcomes
  for each row execute function research_capture.stamp_codex_outcome();
create trigger immutable_uncapped_authorizations before update or delete on research_capture.uncapped_model_authorizations
  for each row execute function research_capture.reject_mutation();
create trigger no_truncate_uncapped_authorizations before truncate on research_capture.uncapped_model_authorizations
  for each statement execute function research_capture.reject_mutation();
create trigger immutable_codex_outcomes before update or delete on research_capture.codex_invocation_outcomes
  for each row execute function research_capture.reject_mutation();
create trigger no_truncate_codex_outcomes before truncate on research_capture.codex_invocation_outcomes
  for each statement execute function research_capture.reject_mutation();
revoke all on research_capture.uncapped_model_authorizations,research_capture.codex_invocation_outcomes from public;
revoke all on function research_capture.stamp_uncapped_authorization(),research_capture.stamp_codex_outcome() from public;
comment on table research_capture.codex_invocation_outcomes is
  'Reported usage from validated CLI turns, not a provider-request count, invoice or hard output bound. Missing outcome means unknown, never a refund.';
