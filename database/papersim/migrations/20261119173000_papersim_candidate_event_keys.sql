-- FX-26 candidate event keys (audit 2026-11-19 finding N-1, fix-list
-- section 2): two nullable identity columns on papersim.candidates so the
-- evaluation loader can cluster interval estimates by match instead of
-- treating correlated lines of one event as independent markets.
-- event_key: the pure store.candidate_event_key() product -- sports: the
-- Gamma event id (fallback canonical league|home|away|day string), crypto:
-- asset plus UTC expiry date, otherwise the market's own identity; NULL
-- when the caller lacks the inputs (legacy and imported rows stay NULL
-- and evaluation keeps the frozen market-key fallback).
-- event_start_at: the adapter-parsed game start (sports) or the market
-- end date as the crypto expiry; finite when set.
-- Drift must fail loudly: no IF NOT EXISTS, published M1/M2/V2 bytes
-- untouched; the existing immutable_candidates/no_truncate_candidates
-- triggers and the table-level pal_app grants cover the new columns
-- (column additions to an existing table need no re-grant, same as the
-- M2 scan_runs.operation_key precedent).
begin;
alter table papersim.candidates add column event_key text
    check (event_key is null or event_key <> '');
alter table papersim.candidates add column event_start_at timestamptz
    check (event_start_at is null or isfinite(event_start_at));
create index candidates_event_key_idx on papersim.candidates(event_key);
commit;
