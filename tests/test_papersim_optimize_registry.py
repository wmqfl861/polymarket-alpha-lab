"""MO-2 pure registry proofs (05 node MO-2; 04 sections 3, 9 and 11).

Everything here is offline and pure: the idempotency-key contracts of
``papersim.store`` (including the plan-review P2 rule that key
material -- time fields included -- comes from the submitted record,
never the writing clock), the six submission validators, the registry
fold's five legal batch shapes and every checked invariant (exactly
one active, permanent baseline, candidate cap, guard cap, handover
pairs in one batch, unfreeze never automatic), the lineage range
invariant (plan-review P2: one family never spans ranges), the
import-purity contract and the jsonb round-trip normalization
(plan-review P3-4).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import subprocess
import sys

import pytest

from polymarket_alpha_lab.papersim import store
from polymarket_alpha_lab.papersim.optimize.registry import (
    ControlEventRecord,
    LineageRoles,
    RegistryInvariantError,
    RoleEvent,
    derive_lineage_roles,
    group_events_by_lineage,
    lineage_id,
    validate_control_event,
    validate_role_event_batch,
)
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec

AT = datetime(2026, 11, 19, 21, 0, tzinfo=UTC)
LATER = AT + timedelta(hours=1)
COMMIT = 'a' * 40
LINEAGE = 'club-soccer-frozen/over_under'


def digest_of(text: str) -> str:
    return sha256(text.encode('utf-8')).hexdigest()


BASELINE = digest_of('baseline-spec')
CANDIDATE = digest_of('candidate-spec')
CANDIDATE2 = digest_of('candidate-spec-two')


def spec(family: str = 'club-soccer-frozen', **params) -> ModelSpec:
    return ModelSpec(family=family, procedure_version='r23',
                     params=dict(sorted(params.items())),
                     envelope=({'name': 'confs', 'weight': 0.5},),
                     data_contract='understat-xg-snapshot-v1')


def event(role: str, spec_sha256: str, *, at=AT, lineage=LINEAGE,
          reason: str = 'test', forward_plan=None, actor: str = 'op',
          event_id: int | None = None) -> RoleEvent:
    return RoleEvent(lineage=lineage, spec_sha256=spec_sha256, role=role,
                     effective_at=at, reason=reason, forward_plan=forward_plan,
                     actor=actor, code_commit=COMMIT, role_event_id=event_id)


def initialized(**kwargs) -> LineageRoles:
    return derive_lineage_roles([[event('BASELINE', BASELINE, reason='init'),
                                  event('ACTIVE', BASELINE, reason='init')]], **kwargs)


# ---------------------------------------------------------------------------
# Idempotency keys (plan-review P2: deterministic key material)
# ---------------------------------------------------------------------------

def test_optimization_run_key_is_deterministic_and_clock_free():
    space = '{"grid":[1,2]}'
    first = store.optimization_run_key('club-soccer-frozen', AT, 'rules-v1', 7, space)
    second = store.optimization_run_key('club-soccer-frozen', AT, 'rules-v1', 7,
                                        '{"grid":[2,1]}')
    assert first == store.optimization_run_key('club-soccer-frozen', AT, 'rules-v1', 7,
                                               space)
    # The SAME identity inputs -- cutoff and space INCLUDED -- produce the
    # SAME key on every retry: nothing here reads a clock.
    assert first.startswith('or|club-soccer-frozen|')
    # A different search space is a different run identity.
    assert first != second
    with pytest.raises(store.PapersimStoreError):
        store.optimization_run_key('club-soccer-frozen', AT, 'rules-v1', '7', space)


def test_promotion_decision_key_ignores_decision_time_but_pins_identity():
    base = store.promotion_decision_key(LINEAGE, 'FORWARD', CANDIDATE, 'rules-v1', 1)
    assert base == 'pd|' + '|'.join((LINEAGE, 'FORWARD', CANDIDATE, 'rules-v1', '1'))
    # decided_at never enters the key: a re-submitted identical judgement
    # at any later time finds the original row.
    assert base == store.promotion_decision_key(LINEAGE, 'FORWARD', CANDIDATE,
                                                'rules-v1', 1)
    assert base != store.promotion_decision_key(LINEAGE, 'FORWARD', CANDIDATE,
                                                'rules-v1', 2)
    assert base != store.promotion_decision_key(LINEAGE, 'FORWARD', CANDIDATE2,
                                                'rules-v1', 1)
    with pytest.raises(store.PapersimStoreError):
        store.promotion_decision_key(LINEAGE, 'NOPE', CANDIDATE, 'rules-v1', 1)


def test_model_role_event_key_uses_the_events_own_effect_time():
    key = store.model_role_event_key(LINEAGE, CANDIDATE, 'CANDIDATE', AT)
    assert key == 'mre|' + LINEAGE + '|' + CANDIDATE + '|CANDIDATE|20261119210000'
    # The two handover events of one promotion share effective_at but
    # differ in spec/role, so both original rows are found on replay.
    guard = store.model_role_event_key(LINEAGE, BASELINE, 'GUARD', AT)
    active = store.model_role_event_key(LINEAGE, CANDIDATE, 'ACTIVE', AT)
    assert guard != active and guard.split('|')[4] == active.split('|')[4]
    with pytest.raises(store.PapersimStoreError):
        store.model_role_event_key(LINEAGE, CANDIDATE, 'KING', AT)


def test_shadow_prediction_key_and_optimizer_control_key_contracts():
    key = store.shadow_prediction_key(11, CANDIDATE, AT)
    assert key == 'sp|11|' + CANDIDATE + '|20261119210000'
    control = store.optimizer_control_key('GLOBAL', 'FREEZE', 'op', AT)
    assert control == 'oce|GLOBAL|FREEZE|op|20261119210000'
    assert control != store.optimizer_control_key(LINEAGE, 'FREEZE', 'op', AT)
    with pytest.raises(store.PapersimStoreError):
        store.optimizer_control_key('GLOBAL', 'PAUSE', 'op', AT)


# ---------------------------------------------------------------------------
# Submission validators
# ---------------------------------------------------------------------------

def spec_submission(**overrides):
    model = spec(half_life=8)
    base = dict(spec_sha256=model.spec_sha256(),
                canonical_text=model.canonical_json(), family=model.family,
                procedure_version=model.procedure_version,
                model_version=model.model_version(),
                legacy_model_version=None,
                params=json.dumps(dict(sorted(model.params.items())),
                                  sort_keys=True, separators=(',', ':')),
                envelope=json.dumps([dict(sorted(item.items()))
                                     for item in model.envelope],
                                    sort_keys=True, separators=(',', ':')),
                data_contract=model.data_contract, source='frozen_baseline',
                parent_spec_sha256=None, optimization_id=None, code_commit=COMMIT)
    base.update(overrides)
    return store.ModelSpecSubmission(**base)


def test_model_spec_submission_requires_digest_text_agreement():
    submission = spec_submission()
    assert submission.validate() is submission
    # A forged digest (same key material, wrong pairing) is rejected --
    # the same agreement the database re-derives with convert_to UTF8.
    with pytest.raises(store.PapersimStoreError):
        spec_submission(spec_sha256=digest_of('other')).validate()
    # model_version must be the derivation of its own fields.
    with pytest.raises(store.PapersimStoreError):
        spec_submission(model_version='hand-written').validate()
    with pytest.raises(store.PapersimStoreError):
        spec_submission(source='guess').validate()


def test_model_spec_submission_model_version_accepts_legacy_alias():
    model = spec()
    legacy = 'soccer-r23'
    submission = spec_submission(
        spec_sha256=model.spec_sha256(), canonical_text=model.canonical_json(),
        model_version=legacy, legacy_model_version=legacy)
    assert submission.validate() is submission


def test_optimization_run_submission_validator():
    good = store.OptimizationRunSubmission(
        family='club-soccer-frozen', data_cutoff=AT, lineages=['club-soccer-frozen/over_under'],
        rule_version='rules-v1', random_seed=7, trial_count=3,
        active_specs={'club-soccer-frozen/over_under': BASELINE},
        outcome='CANDIDATE_REGISTERED', code_commit=COMMIT)
    assert good.validate() is good
    with pytest.raises(store.PapersimStoreError):
        store.OptimizationRunSubmission(family='f', data_cutoff=AT, lineages=[],
                                        code_commit=COMMIT).validate()
    with pytest.raises(store.PapersimStoreError):
        store.OptimizationRunSubmission(
            family='f', data_cutoff=AT,
            lineages=['f/over_under'],
            active_specs={'f/btts': BASELINE},  # not among the run's lineages
            code_commit=COMMIT).validate()
    with pytest.raises(store.PapersimStoreError):
        store.OptimizationRunSubmission(family='f', data_cutoff=AT,
                                        lineages=['f/over_under'],
                                        outcome='IMPROVED', code_commit=COMMIT).validate()


def test_promotion_decision_submission_block_reason_pairing():
    good = store.PromotionDecisionSubmission(
        lineage=LINEAGE, active_spec_sha256=BASELINE, candidate_spec_sha256=CANDIDATE,
        rule_version='rules-v1', decided_by='auto:rules-v1', decided_at=AT,
        conclusion='PROMOTE')
    assert good.validate() is good
    blocked = store.PromotionDecisionSubmission(
        lineage=LINEAGE, active_spec_sha256=BASELINE, rule_version='rules-v1',
        conclusion='BLOCKED', block_reason='lineage paused',
        decided_by='auto:rules-v1', decided_at=AT)
    assert blocked.validate() is blocked
    with pytest.raises(store.PapersimStoreError):
        store.PromotionDecisionSubmission(
            lineage=LINEAGE, active_spec_sha256=BASELINE, rule_version='rules-v1',
            conclusion='BLOCKED', decided_by='auto:rules-v1', decided_at=AT).validate()
    with pytest.raises(store.PapersimStoreError):
        store.PromotionDecisionSubmission(
            lineage=LINEAGE, active_spec_sha256=BASELINE, rule_version='rules-v1',
            conclusion='HOLD', block_reason='stray reason',
            decided_by='auto:rules-v1', decided_at=AT).validate()


def test_role_event_and_batch_validators():
    candidate = store.RoleEventSubmission(
        lineage=LINEAGE, spec_sha256=CANDIDATE, role='CANDIDATE', effective_at=AT,
        reason='register', forward_plan={'look_at': 150}, actor='auto:rules-v1',
        code_commit=COMMIT)
    assert candidate.validate() is candidate
    # A CANDIDATE without its forward plan is invalid at the store layer
    # too (mirrored by the database CHECK).
    with pytest.raises(store.PapersimStoreError):
        store.RoleEventSubmission(
            lineage=LINEAGE, spec_sha256=CANDIDATE, role='CANDIDATE',
            effective_at=AT, reason='register', actor='auto:rules-v1',
            code_commit=COMMIT).validate()
    batch = store.RoleEventBatch(events=(
        store.RoleEventSubmission(lineage=LINEAGE, spec_sha256=BASELINE,
                                  role='BASELINE', effective_at=AT, reason='init',
                                  actor='op', code_commit=COMMIT),
        store.RoleEventSubmission(lineage=LINEAGE, spec_sha256=BASELINE,
                                  role='ACTIVE', effective_at=AT, reason='init',
                                  actor='op', code_commit=COMMIT)))
    assert batch.validate() is batch
    # One batch = one lineage + one effective_at (the transaction unit).
    with pytest.raises(store.PapersimStoreError):
        store.RoleEventBatch(events=(
            store.RoleEventSubmission(lineage=LINEAGE, spec_sha256=BASELINE,
                                      role='BASELINE', effective_at=AT, reason='i',
                                      actor='op', code_commit=COMMIT),
            store.RoleEventSubmission(lineage='other/btts', spec_sha256=BASELINE,
                                      role='ACTIVE', effective_at=AT, reason='i',
                                      actor='op', code_commit=COMMIT))).validate()
    with pytest.raises(store.PapersimStoreError):
        store.RoleEventBatch(events=(
            store.RoleEventSubmission(lineage=LINEAGE, spec_sha256=BASELINE,
                                      role='BASELINE', effective_at=AT, reason='i',
                                      actor='op', code_commit=COMMIT),
            store.RoleEventSubmission(lineage=LINEAGE, spec_sha256=BASELINE,
                                      role='ACTIVE', effective_at=LATER, reason='i',
                                      actor='op', code_commit=COMMIT))).validate()


def test_shadow_and_control_submission_validators():
    shadow = store.ShadowPredictionSubmission(
        candidate_id=5, spec_sha256=BASELINE, role_at_prediction='BASELINE',
        probability=Decimal('0.42'), gate_pass=True, predicted_at=AT)
    assert shadow.validate() is shadow
    # A refusal carries no probability.
    with pytest.raises(store.PapersimStoreError):
        store.ShadowPredictionSubmission(
            candidate_id=5, spec_sha256=BASELINE, role_at_prediction='BASELINE',
            probability=0.5, refusal_reason='no teams', gate_pass=False,
            predicted_at=AT).validate()
    # A fill price must stay in the open interval.
    with pytest.raises(store.PapersimStoreError):
        store.ShadowPredictionSubmission(
            candidate_id=5, spec_sha256=BASELINE, role_at_prediction='GUARD',
            hypothetical_fill_price=1, gate_pass=False, predicted_at=AT).validate()
    freeze = store.OptimizerControlEventSubmission(scope='GLOBAL',
                                                   control_kind='FREEZE',
                                                   reason='ops', actor='op',
                                                   occurred_at=AT)
    assert freeze.validate() is freeze
    with pytest.raises(store.PapersimStoreError):
        store.OptimizerControlEventSubmission(scope='GLOBAL', control_kind='UNFREEZE',
                                              reason='ops', actor='auto:rules-v1',
                                              occurred_at=AT).validate()
    with pytest.raises(store.PapersimStoreError):
        store.OptimizerControlEventSubmission(scope='LINEAGE', control_kind='FREEZE',
                                              reason='ops', actor='op',
                                              occurred_at=AT).validate()


# ---------------------------------------------------------------------------
# Registry fold: the five legal batch shapes and the invariants
# ---------------------------------------------------------------------------

def test_empty_registry_is_none_and_initialization_makes_baseline_active():
    # Empty event sequence -> None: the registry-empty signal the MO-7
    # provider turns into "fall back to the family baseline" (04 s11).
    assert derive_lineage_roles([]) is None
    view = initialized()
    assert view.active == view.baseline == BASELINE
    assert view.candidates == () and view.guard is None


def test_initialization_shape_is_the_only_first_batch():
    for bad in ([event('ACTIVE', BASELINE)],
                [event('BASELINE', BASELINE)],
                [event('BASELINE', BASELINE), event('ACTIVE', CANDIDATE)],
                [event('BASELINE', BASELINE), event('ACTIVE', BASELINE),
                 event('CANDIDATE', CANDIDATE, forward_plan={})]):
        with pytest.raises(RegistryInvariantError):
            derive_lineage_roles([bad])


def test_candidate_registration_and_cap():
    view = initialized()
    with pytest.raises(RegistryInvariantError):
        derive_lineage_roles([[event('CANDIDATE', BASELINE, forward_plan={})]])
    first = validate_role_event_batch(view, [event('CANDIDATE', CANDIDATE,
                                                   forward_plan={'look': 1})])
    second = validate_role_event_batch(first, [event('CANDIDATE', CANDIDATE2,
                                                     forward_plan={'look': 1})])
    assert second.candidates == (CANDIDATE, CANDIDATE2)
    # Cap: at most two candidates at any time (04 s3).
    with pytest.raises(RegistryInvariantError):
        validate_role_event_batch(
            second, [event('CANDIDATE', digest_of('third'), forward_plan={})])


def test_handover_requires_both_events_in_one_batch():
    view = initialized()
    registered = validate_role_event_batch(view, [event('CANDIDATE', CANDIDATE,
                                                        forward_plan={})])
    # A lone ACTIVE flip (the handover's second event without its GUARD
    # partner) is illegal: the promotion pair must share ONE batch/one
    # transaction (05 node MO-2).
    with pytest.raises(RegistryInvariantError):
        validate_role_event_batch(registered, [event('ACTIVE', CANDIDATE)])
    handed = validate_role_event_batch(
        registered, [event('GUARD', BASELINE), event('ACTIVE', CANDIDATE)])
    assert handed.active == CANDIDATE and handed.guard == BASELINE
    assert handed.candidates == ()
    # Splitting the pair across batches is equally illegal.
    with pytest.raises(RegistryInvariantError):
        derive_lineage_roles([
            [event('BASELINE', BASELINE), event('ACTIVE', BASELINE)],
            [event('CANDIDATE', CANDIDATE, forward_plan={})],
            [event('GUARD', BASELINE)],
            [event('ACTIVE', CANDIDATE)]])


def test_second_handover_retires_the_old_guard_in_the_same_batch():
    view = initialized()
    step = validate_role_event_batch(view, [event('CANDIDATE', CANDIDATE,
                                                  forward_plan={})])
    handed = validate_role_event_batch(
        step, [event('GUARD', BASELINE), event('ACTIVE', CANDIDATE)])
    again = validate_role_event_batch(
        handed, [event('CANDIDATE', CANDIDATE2, forward_plan={})])
    # Guard cap stays at one: the old guard must retire INSIDE the new
    # handover batch.
    with pytest.raises(RegistryInvariantError):
        validate_role_event_batch(
            again, [event('GUARD', CANDIDATE), event('ACTIVE', CANDIDATE2)])
    twice = validate_role_event_batch(
        again, [event('RETIRED', BASELINE), event('GUARD', CANDIDATE),
                event('ACTIVE', CANDIDATE2)])
    assert twice.guard == CANDIDATE and twice.active == CANDIDATE2
    assert BASELINE in twice.retired


def test_first_handover_rejects_a_stray_retired_event():
    # MO-8 tightening (MO-2 code-review P3 follow-up): with no prior
    # guard the handover batch has no legal RETIRED event. The old fold
    # silently dropped a stray target; it now fails with the fixed code.
    view = initialized()
    registered = validate_role_event_batch(view, [event('CANDIDATE', CANDIDATE,
                                                        forward_plan={})])
    with pytest.raises(RegistryInvariantError) as stray:
        validate_role_event_batch(
            registered, [event('RETIRED', CANDIDATE2), event('GUARD', BASELINE),
                         event('ACTIVE', CANDIDATE)])
    assert 'papersim_registry_handover_stray_retired' in str(stray.value)
    # The legal two-event handover is unchanged and stays silent-proof.
    handed = validate_role_event_batch(
        registered, [event('GUARD', BASELINE), event('ACTIVE', CANDIDATE)])
    assert handed.retired == ()


def test_second_handover_retired_event_must_be_exactly_the_old_guard():
    # With a prior guard present the single RETIRED event must retire
    # exactly that guard; a bystander digest fails the guard-cap rule.
    view = initialized()
    step = validate_role_event_batch(view, [event('CANDIDATE', CANDIDATE,
                                                  forward_plan={})])
    handed = validate_role_event_batch(
        step, [event('GUARD', BASELINE), event('ACTIVE', CANDIDATE)])
    again = validate_role_event_batch(
        handed, [event('CANDIDATE', CANDIDATE2, forward_plan={})])
    with pytest.raises(RegistryInvariantError):
        validate_role_event_batch(
            again, [event('RETIRED', CANDIDATE2), event('GUARD', CANDIDATE),
                    event('ACTIVE', CANDIDATE2)])


def test_retirement_only_currently_held_specs():
    view = initialized()
    with pytest.raises(RegistryInvariantError):
        validate_role_event_batch(view, [event('RETIRED', CANDIDATE)])
    registered = validate_role_event_batch(view, [event('CANDIDATE', CANDIDATE,
                                                        forward_plan={})])
    retired = validate_role_event_batch(registered, [event('RETIRED', CANDIDATE)])
    assert retired.candidates == () and CANDIDATE in retired.retired


def test_rollback_batch_shape():
    view = initialized()
    registered = validate_role_event_batch(view, [event('CANDIDATE', CANDIDATE,
                                                        forward_plan={})])
    handed = validate_role_event_batch(
        registered, [event('GUARD', BASELINE), event('ACTIVE', CANDIDATE)])
    rolled = validate_role_event_batch(
        handed, [event('RETIRED', CANDIDATE), event('ACTIVE', BASELINE)])
    assert rolled.active == BASELINE and rolled.guard is None
    assert rolled.baseline == BASELINE  # the baseline event survives forever
    # A rollback without a guard, or to a spec that is not the guard,
    # is illegal.
    with pytest.raises(RegistryInvariantError):
        validate_role_event_batch(handed, [event('ACTIVE', BASELINE)])


def test_baseline_is_permanent_and_exactly_one_active():
    view = initialized()
    # A second BASELINE event never lands, in any batch shape.
    with pytest.raises(RegistryInvariantError):
        validate_role_event_batch(view, [event('BASELINE', CANDIDATE)])
    # The handover guard must be the CURRENT active (never a bystander).
    registered = validate_role_event_batch(view, [event('CANDIDATE', CANDIDATE,
                                                        forward_plan={})])
    with pytest.raises(RegistryInvariantError):
        validate_role_event_batch(
            registered, [event('GUARD', CANDIDATE2), event('ACTIVE', CANDIDATE)])
    # The promoted spec must be a registered candidate.
    with pytest.raises(RegistryInvariantError):
        validate_role_event_batch(
            view, [event('GUARD', BASELINE), event('ACTIVE', CANDIDATE)])


def test_group_events_by_lineage_rebuilds_writer_batches():
    events = [
        event('BASELINE', BASELINE, at=AT, event_id=1),
        event('ACTIVE', BASELINE, at=AT, event_id=2),
        event('CANDIDATE', CANDIDATE, at=LATER, forward_plan={}, event_id=3),
        event('BASELINE', BASELINE, at=AT, lineage='club-soccer-frozen/btts',
              event_id=4),
        event('ACTIVE', BASELINE, at=AT, lineage='club-soccer-frozen/btts',
              event_id=5),
    ]
    grouped = group_events_by_lineage([[e] for e in events])
    assert set(grouped) == {LINEAGE, 'club-soccer-frozen/btts'}
    assert len(grouped[LINEAGE]) == 2  # init batch + candidate batch
    view = derive_lineage_roles(grouped[LINEAGE])
    assert view.candidates == (CANDIDATE,)
    assert derive_lineage_roles(grouped['club-soccer-frozen/btts']).active == BASELINE


def test_control_event_validation_three_layers():
    validate_control_event('FREEZE', 'auto:rules-v1')  # freezing may be automatic
    with pytest.raises(RegistryInvariantError):
        validate_control_event('UNFREEZE', 'auto:rules-v1')
    validate_control_event('UNFREEZE', 'owner')
    ControlEventRecord(scope='GLOBAL', lineage=None, control_kind='FREEZE',
                       reason='r', actor='op', occurred_at=AT)
    with pytest.raises(RegistryInvariantError):
        ControlEventRecord(scope='GLOBAL', lineage=LINEAGE, control_kind='FREEZE',
                           reason='r', actor='op', occurred_at=AT)


# ---------------------------------------------------------------------------
# Lineage identity invariant (plan-review P2)
# ---------------------------------------------------------------------------

def test_lineage_id_shape_and_family_range_invariant():
    assert lineage_id('club-soccer-frozen', 'over_under') == LINEAGE
    with pytest.raises(RegistryInvariantError):
        lineage_id('', 'over_under')
    # P2 pinned rule: ONE FAMILY NEVER SPANS RANGES -- a new range means
    # a NEW family id, never a third path segment or a shared family.
    # Structural fact, asserted so any deviation is a deliberate change:
    # EVERY registered family targets exactly one range (no family id
    # carries a range suffix or is shared between ranges; targets differ
    # only by market type). The registered list itself grows with the
    # MO-5 family adapters (frozen at MO-2-v2 land time:
    # club-soccer-frozen, club-soccer-recalib) and is NOT pinned here.
    from polymarket_alpha_lab.papersim.optimize.families import iter_family_ids

    families = iter_family_ids()
    assert families  # at least one family is always registered
    for family_id in families:
        assert '/' not in family_id  # no range smuggled into the family id
        assert family_id == family_id.strip()


# ---------------------------------------------------------------------------
# jsonb round-trip normalization (plan-review P3-4)
# ---------------------------------------------------------------------------

def test_jsonb_roundtrip_normalization_keeps_spec_identity():
    from polymarket_alpha_lab.papersim.store import normalize_json_number

    model = spec(half_life=8.0, min_games=300, shrink='strong')
    # jsonb storage: Python floats/ints -> JSON text -> jsonb numeric ->
    # back as int/float (or Decimal under a custom loads hook); the
    # normalization must fold Decimal back to int/float BEFORE the
    # ModelSpec rebuild so the canonical text (decimal-string numbers,
    # i: vs f: tags) cannot flip spelling.
    params = json.loads(json.dumps(dict(sorted(model.params.items())),
                                   sort_keys=True, separators=(',', ':')))
    decimalized = {'half_life': Decimal('8.0'), 'min_games': Decimal('300'),
                   'shrink': 'strong'}
    normalized = normalize_json_number(decimalized)
    assert normalized == params
    assert isinstance(normalized['half_life'], float)
    assert isinstance(normalized['min_games'], int)
    rebuilt = ModelSpec(family=model.family,
                        procedure_version=model.procedure_version,
                        params=normalized, envelope=model.envelope,
                        data_contract=model.data_contract,
                        legacy_model_version=model.legacy_model_version)
    assert rebuilt.spec_sha256() == model.spec_sha256()
    assert rebuilt.canonical_json() == model.canonical_json()
    # Containers recurse.
    nested = normalize_json_number({'grid': [Decimal('0.5'), {'w': Decimal('2')}]})
    assert nested == {'grid': [0.5, {'w': 2}]}


def test_registry_module_import_purity():
    # registry.py adds NO database driver and NO store/family imports.
    # The papersim package baseline itself already loads soccer_model
    # (papersim/__init__.py:218), so the assertion is differential: the
    # project-module delta of importing registry over the papersim
    # baseline is exactly optimize + optimize.registry and nothing else
    # (same two-step snapshot method as the MO-1 purity proof).
    code = ('import sys, json\n'
            'import polymarket_alpha_lab.papersim as base_pkg  # baseline\n'
            'baseline = set(sys.modules)\n'
            'import polymarket_alpha_lab.papersim.optimize.registry as r\n'
            'delta = sorted(m for m in set(sys.modules) - baseline\n'
            '              if m.startswith("polymarket_alpha_lab"))\n'
            'print(json.dumps({"delta": delta,\n'
            '                  "psycopg": any("psycopg" in m\n'
            '                                 for m in set(sys.modules) - baseline),\n'
            '                  "registry_ok": r.derive_lineage_roles([]) is None}))')
    result = subprocess.run([sys.executable, '-c', code], capture_output=True,
                            text=True, check=True)
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    # optimize.spec joins the delta because optimize/__init__ re-exports
    # it (MO-1 fact); nothing else -- no store, no family module, no
    # driver -- enters through registry.
    assert payload == {"delta": ["polymarket_alpha_lab.papersim.optimize",
                                 "polymarket_alpha_lab.papersim.optimize.registry",
                                 "polymarket_alpha_lab.papersim.optimize.spec"],
                       "psycopg": False, "registry_ok": True}
