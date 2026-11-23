-- FX-44 B2 marks and CLOSE evidence bind to a registered observation snapshot.
-- Each new key uses its table's prefix and the SHA-256 of the occurrence
-- identity. The writer uses the same occurrence and binding for both rows.
-- The binding records the supported capture facts and their applicable rule.
begin;

alter table papersim.candidate_marks
    add column observation_binding_sha256 text references papersim.snapshots(content_sha256),
    add constraint candidate_marks_observation_binding_check check (
        (candidate_mark_key like 'cm2|%') = (observation_binding_sha256 is not null)
        and (observation_binding_sha256 is null or candidate_mark_key ~ '^cm2[|][0-9a-f]{64}$')
    );

alter table papersim.candidate_price_evidence
    add column observation_binding_sha256 text references papersim.snapshots(content_sha256),
    add constraint candidate_price_evidence_observation_binding_check check (
        (evidence_key like 'cpe2|%') = (observation_binding_sha256 is not null)
        and (observation_binding_sha256 is null or (
            evidence_role = 'CLOSE' and evidence_key ~ '^cpe2[|][0-9a-f]{64}$'
        ))
    );

drop index papersim.candidate_marks_one_close_per_candidate_idx;
create unique index candidate_marks_one_close_per_candidate_idx
    on papersim.candidate_marks(candidate_id)
    where sampling_kind = 'CLOSE' and observation_binding_sha256 is null;

commit;
