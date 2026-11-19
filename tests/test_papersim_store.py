"""Pure papersim store tests: key construction, vocabularies, validation,
UNKNOWN/INSUFFICIENT semantics and the append-only association rules.

Offline by construction: this module imports only the pure transformation
layer (09D design v2 sections 4.4, 4.5, 4.2-7 and 5.x CHECK
mirrors). The native PostgreSQL proofs live in
``tests/test_papersim_store_native.py``.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from polymarket_alpha_lab.papersim import store


def at(minutes: int) -> datetime:
    return datetime(2026, 11, 15, 12, 0, tzinfo=UTC) + timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# Idempotency key construction (09D v2 section 4.4)
# ---------------------------------------------------------------------------

def test_market_outcome_evidence_key_is_the_pinned_writer_form():
    key = store.market_outcome_evidence_key('0xabc', '3866000', 'gamma_close',
                                            'close-event-1', at(0))
    assert key == 'mo|0xabc|3866000|gamma_close|close-event-1|20261115120000'


def test_market_outcome_evidence_key_fills_missing_parts_with_empty_strings():
    key = store.market_outcome_evidence_key(None, None, 'operator_attestation', None, at(0))
    assert key == 'mo|||operator_attestation||20261115120000'


def test_market_outcome_evidence_key_uses_utc_conversion():
    shifted = at(0).astimezone(UTC) + timedelta(hours=5, minutes=30)
    from datetime import timezone
    local = shifted.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert store.market_outcome_evidence_key(None, '7', 'gamma_close', None, local).endswith('20261115173000')


def test_price_evidence_key_is_the_pinned_writer_form():
    key = store.price_evidence_key(41, 'DECISION', 'market_slug', 'fed-hike-jan',
                                   'NO', 'clob_terminal_price', at(5))
    assert key == 'cpe|41|DECISION|market_slug|fed-hike-jan|NO|clob_terminal_price|20261115120500'


def test_price_evidence_key_binds_candidate_id_at_insert_time():
    observation = store.PriceEvidenceObservation(
        observed_at=at(5), evidence_role='DECISION', market_ref_kind='market_id',
        market_ref='3866000', side='YES', source_kind='gamma_close', source_ref='r1')
    assert observation.evidence_key_with(77).startswith('cpe|77|')


def test_price_evidence_key_requires_int_candidate_id():
    with pytest.raises(store.PapersimStoreError, match='papersim_candidate_id_invalid'):
        store.price_evidence_key('41', 'DECISION', 'market_id', 'm', 'YES', 'gamma_close', at(0))


def test_scan_key_is_sha256_of_the_pinned_material():
    params = '{"top_k": 5}'
    key = store.scan_key('crypto_btc', at(0), 'a' * 40, 'gates-v3', 2, params)
    material = f"crypto_btc|20261115120000|{'a' * 40}|gates-v3|2|{store.sha256(params.encode()).hexdigest()}"
    assert key == store.sha256(material.encode()).hexdigest()
    assert key != store.scan_key('crypto_btc', at(0), 'b' * 40, 'gates-v3', 2, params)


# ---------------------------------------------------------------------------
# Vocabularies and row validation (DDL CHECK mirrors)
# ---------------------------------------------------------------------------

def outcome(**overrides):
    values = dict(condition_id='0x1', market_id=None, market_slug=None, yes_token_id=None,
                  no_token_id=None, outcome='YES', outcome_detail=None, confirmed_at=at(10),
                  observed_at=at(5), source_kind='gamma_close', source_ref='ref-1',
                  source_snapshot_sha256=None)
    values.update(overrides)
    return store.MarketOutcomeObservation(**values)


def test_market_outcome_requires_at_least_one_market_identity():
    with pytest.raises(store.PapersimStoreError, match='papersim_market_identity_missing'):
        outcome(condition_id=None).validate()


def test_market_outcome_rejects_unknown_outcome_token():
    with pytest.raises(store.PapersimStoreError, match='papersim_outcome_token_invalid'):
        outcome(outcome='MAYBE').validate()


def test_market_outcome_unknown_never_carries_a_confirmation_time():
    with pytest.raises(store.PapersimStoreError, match='papersim_outcome_unknown_confirmed'):
        outcome(outcome='UNKNOWN').validate()
    outcome(outcome='UNKNOWN', confirmed_at=None).validate()


def test_market_outcome_rejects_unknown_source_kind_and_naive_timestamps():
    with pytest.raises(store.PapersimStoreError, match='papersim_outcome_source_kind_invalid'):
        outcome(source_kind='twitter_rumor').validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_observed_at_invalid'):
        outcome(observed_at=datetime(2026, 11, 15, 12, 0)).validate()


def evidence(**overrides):
    values = dict(observed_at=at(5), evidence_role='DECISION', model_probability=None,
                  market_ref_kind='market_slug', market_ref='fed-hike-jan', token_ref=None,
                  side='YES', bid=None, ask=None, mid=None, last=None, book_sha256=None,
                  snapshot_sha256=None, source_kind='gamma_close', source_ref='r1')
    values.update(overrides)
    return store.PriceEvidenceObservation(**values)


def test_price_evidence_quotes_use_the_open_interval():
    with pytest.raises(store.PapersimStoreError, match='papersim_bid_invalid'):
        evidence(bid=Decimal('0')).validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_ask_invalid'):
        evidence(ask=Decimal('1')).validate()
    evidence(bid=Decimal('0.01'), ask=Decimal('0.99'), mid=Decimal('0.5')).validate()


def test_price_evidence_last_interval_depends_on_the_role():
    evidence(evidence_role='CLOSE', last=Decimal('0'), source_kind='clob_terminal_price',
             source_ref='t1').validate()
    evidence(evidence_role='CLOSE', last=Decimal('1'), source_kind='clob_terminal_price',
             source_ref='t1').validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_last_invalid'):
        evidence(evidence_role='DECISION', last=Decimal('1')).validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_last_invalid'):
        evidence(evidence_role='DECISION', last=Decimal('0')).validate()


def test_price_evidence_close_requires_some_price():
    with pytest.raises(store.PapersimStoreError, match='papersim_close_price_missing'):
        evidence(evidence_role='CLOSE').validate()


def test_price_evidence_crossed_quotes_and_mid_outside_quote_rejected():
    with pytest.raises(store.PapersimStoreError, match='papersim_bid_ask_crossed'):
        evidence(bid=Decimal('0.6'), ask=Decimal('0.4')).validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_mid_outside_quote'):
        evidence(bid=Decimal('0.4'), ask=Decimal('0.6'), mid=Decimal('0.7')).validate()


def test_price_evidence_source_ref_optional_only_for_attested_snapshots():
    with pytest.raises(store.PapersimStoreError, match='papersim_evidence_source_missing'):
        evidence(source_ref=None, snapshot_sha256='a' * 64).validate()
    evidence(source_kind='operator_attestation', source_ref=None,
             snapshot_sha256='a' * 64).validate()


def test_price_evidence_probability_is_closed_interval_and_rejects_nonfinite():
    evidence(model_probability=Decimal('0')).validate()
    evidence(model_probability=Decimal('1')).validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_model_probability_invalid'):
        evidence(model_probability=Decimal('1.01')).validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_model_probability_invalid'):
        evidence(model_probability=float('nan')).validate()


def test_price_evidence_rejects_unknown_vocabularies():
    with pytest.raises(store.PapersimStoreError, match='papersim_evidence_role_invalid'):
        evidence(evidence_role='OPEN').validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_market_ref_kind_invalid'):
        evidence(market_ref_kind='url').validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_side_token_invalid'):
        evidence(side='OVER').validate()


def entry(**overrides):
    values = dict(stake=Decimal('250'), price=Decimal('0.56'), shares=Decimal('446.4286'),
                  fee_category='crypto', fee_rate=Decimal('0.07'), entry_fee=Decimal('10.08'),
                  fee_source='schedule', tier='standard', entered_at=at(6), legacy_source_id=None,
                  is_legacy=False)
    values.update(overrides)
    return store.EntrySubmission(**values)


def test_entry_validation_mirrors_the_check_contract():
    with pytest.raises(store.PapersimStoreError, match='papersim_stake_invalid'):
        entry(stake=Decimal('0')).validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_price_invalid'):
        entry(price=Decimal('1')).validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_shares_invalid'):
        entry(shares=Decimal('0')).validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_entry_stake_mismatch'):
        entry(shares=Decimal('100')).validate()


def test_entry_estimated_fees_are_legacy_only():
    with pytest.raises(store.PapersimStoreError, match='papersim_fee_source_estimated_requires_legacy'):
        entry(fee_source='estimated').validate()
    entry(fee_source='estimated', is_legacy=True, legacy_source_id='17').validate()


def test_entry_legacy_source_requires_legacy_row():
    with pytest.raises(store.PapersimStoreError, match='papersim_legacy_source_invalid'):
        entry(legacy_source_id='17').validate()


def test_settlement_win_loss_iff_natural():
    store.validate_settlement_consistency('NATURAL', 'win')
    store.validate_settlement_consistency('EXIT', None)
    store.validate_settlement_consistency('VOIDED_REFUND', None)
    with pytest.raises(store.PapersimStoreError, match='papersim_settlement_win_loss_invalid'):
        store.validate_settlement_consistency('NATURAL', None)
    with pytest.raises(store.PapersimStoreError, match='papersim_settlement_win_loss_invalid'):
        store.validate_settlement_consistency('EXIT', 'win')


def test_reversal_requires_original_reference():
    store.validate_reversal('REVERSAL', 12)
    with pytest.raises(store.PapersimStoreError, match='papersim_reversal_reference_missing'):
        store.validate_reversal('REVERSAL', None)
    store.validate_reversal('ADJUSTMENT', None)


# ---------------------------------------------------------------------------
# Time-order guards (P3-9)
# ---------------------------------------------------------------------------

def test_close_before_decision_is_rejected():
    with pytest.raises(store.PapersimStoreError, match='papersim_evidence_time_order_invalid'):
        store.validate_evidence_time_order(at(5), close_observed_at=at(4))


def test_decision_after_scan_finish_is_rejected():
    with pytest.raises(store.PapersimStoreError, match='papersim_evidence_time_order_invalid'):
        store.validate_evidence_time_order(at(7), scan_finished_at=at(6))


def test_valid_time_order_passes():
    store.validate_evidence_time_order(at(4), close_observed_at=at(5), scan_finished_at=at(6))


def candidate(**overrides):
    values = dict(candidate_key='m1-YES', conclusion='REJECTED', side='YES', market_kind='terminal',
                  team='crypto_eth', model_name='gbm', model_version='r19', input_payload='{}',
                  gate_report='{}', evaluated_at=at(5), condition_id='0x1', market_id=None,
                  market_slug=None, yes_token_id=None, no_token_id=None, market_type='terminal',
                  binding_version=1, median_probability=Decimal('0.44'), variant_quotes='[]',
                  snapshot_refs='[]', rejection_reasons='["edge_too_thin"]', order_book=None,
                  book_fetched_at=None, legacy_source_id=None, is_legacy=False,
                  price_evidence=(), entry=None)
    values.update(overrides)
    return store.CandidateSubmission(**values)


def test_submission_rejects_close_before_decision_across_rows():
    submission = store.ScanSubmission(
        scan=store.ScanRunRecord(team='crypto_eth', started_at=at(0), finished_at=at(6),
                                 code_commit='b' * 40, schema_version=1, gate_version='g1'),
        candidates=(candidate(price_evidence=(
            evidence(observed_at=at(3), evidence_role='CLOSE', source_kind='clob_terminal_price',
                     source_ref='t', last=Decimal('1')),
            evidence(observed_at=at(4), evidence_role='DECISION', source_kind='gamma_close',
                     source_ref='r'),), ),))
    with pytest.raises(store.PapersimStoreError, match='papersim_evidence_time_order_invalid'):
        submission.validate()


def test_submission_rejects_decision_after_scan_finish():
    submission = store.ScanSubmission(
        scan=store.ScanRunRecord(team='crypto_eth', started_at=at(0), finished_at=at(4),
                                 code_commit='b' * 40, schema_version=1, gate_version='g1'),
        candidates=(candidate(price_evidence=(
            evidence(observed_at=at(5), evidence_role='DECISION', source_kind='gamma_close',
                     source_ref='r'),), ),))
    with pytest.raises(store.PapersimStoreError, match='papersim_evidence_time_order_invalid'):
        submission.validate()


def test_scan_run_requires_commit_for_v2_and_orders_its_times():
    with pytest.raises(store.PapersimStoreError, match='papersim_code_commit_required'):
        store.ScanRunRecord(team='t', started_at=at(0), finished_at=at(1), code_commit=None,
                            schema_version=1, gate_version='g').validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_scan_time_order_invalid'):
        store.ScanRunRecord(team='t', started_at=at(2), finished_at=at(1), code_commit='b' * 40,
                            schema_version=1, gate_version='g').validate()


def test_duplicate_candidate_keys_inside_one_scan_are_rejected():
    submission = store.ScanSubmission(
        scan=store.ScanRunRecord(team='t', started_at=at(0), finished_at=at(1),
                                 code_commit='b' * 40, schema_version=1, gate_version='g'),
        candidates=(candidate(), candidate()))
    with pytest.raises(store.PapersimStoreError, match='papersim_candidate_key_duplicate'):
        submission.validate()


def test_snapshot_refs_must_be_sha256_strings():
    with pytest.raises(store.PapersimStoreError, match='papersim_snapshot_ref_invalid'):
        candidate(snapshot_refs='["not-a-hash"]').validate()
    candidate(snapshot_refs='["' + 'a' * 64 + '"]').validate()


def test_entry_requires_accepted_candidate():
    with pytest.raises(store.PapersimStoreError, match='papersim_entry_requires_accepted_candidate'):
        store.ScanSubmission(
            scan=store.ScanRunRecord(team='t', started_at=at(0), finished_at=at(1),
                                     code_commit='b' * 40, schema_version=1, gate_version='g'),
            candidates=(candidate(conclusion='REJECTED', entry=entry()),)).validate()


def test_payload_digest_changes_when_any_candidate_changes():
    scan = store.ScanRunRecord(team='t', started_at=at(0), finished_at=at(1),
                               code_commit='b' * 40, schema_version=1, gate_version='g')
    first = store.ScanSubmission(scan=scan, candidates=(candidate(),))
    second = store.ScanSubmission(scan=scan, candidates=(candidate(rejection_reasons='["other"]'),))
    assert first.payload_sha256() != second.payload_sha256()
    assert first.payload_sha256() == store.ScanSubmission(
        scan=scan, candidates=(candidate(),)).payload_sha256()


def test_payload_digest_changes_when_event_fields_change():
    # FX-26: the scan idempotency digest covers event_key/event_start_at,
    # so replaying the same scan_key with a drifted key conflicts (the
    # match key is part of the submitted content).
    scan = store.ScanRunRecord(team='t', started_at=at(0), finished_at=at(1),
                               code_commit='b' * 40, schema_version=1, gate_version='g')
    base = store.ScanSubmission(scan=scan, candidates=(candidate(),))
    keyed = store.ScanSubmission(scan=scan, candidates=(candidate(event_key='gamma:4242'),))
    keyed_start = store.ScanSubmission(
        scan=scan, candidates=(candidate(event_start_at=at(1)),))
    assert base.payload_sha256() != keyed.payload_sha256()
    assert base.payload_sha256() != keyed_start.payload_sha256()
    assert keyed.payload_sha256() == store.ScanSubmission(
        scan=scan, candidates=(candidate(event_key='gamma:4242'),)).payload_sha256()


# ---------------------------------------------------------------------------
# UNKNOWN / INSUFFICIENT semantics and association rules (09D v2 sections 4.1-3, 4.5)
# ---------------------------------------------------------------------------

def observation_row(identifier, label, minutes, source_kind='gamma_close'):
    return {'outcome_id': identifier, 'outcome': label, 'observed_at': at(minutes),
            'source_kind': source_kind}


def test_effective_outcome_takes_the_latest_non_unknown_observation():
    effective = store.select_effective_outcome(
        [observation_row(1, 'YES', 5), observation_row(2, 'NO', 30)])
    assert (effective.outcome_id, effective.outcome) == (2, 'NO')
    assert effective.is_effective


def test_effective_outcome_tie_break_prefers_the_larger_outcome_id():
    effective = store.select_effective_outcome(
        [observation_row(7, 'YES', 5), observation_row(3, 'NO', 5)])
    assert (effective.outcome_id, effective.outcome) == (7, 'YES')


def test_later_unknown_does_not_shadow_an_existing_non_unknown():
    effective = store.select_effective_outcome(
        [observation_row(1, 'YES', 5), observation_row(2, 'UNKNOWN', 30)])
    assert (effective.outcome_id, effective.outcome) == (1, 'YES')


def test_only_unknown_observations_yield_the_latest_unknown_gap():
    effective = store.select_effective_outcome(
        [observation_row(1, 'UNKNOWN', 5), observation_row(2, 'UNKNOWN', 30)])
    assert (effective.outcome_id, effective.outcome) == (2, 'UNKNOWN')
    assert not effective.is_effective


def test_no_observations_at_all_is_an_unknown_gap_without_a_row():
    effective = store.select_effective_outcome([])
    assert effective.outcome_id is None and effective.outcome == 'UNKNOWN'
    assert not effective.is_effective


def test_decision_time_observation_is_the_smallest_evidence_id():
    rows = [{'evidence_id': 9, 'evidence_role': 'DECISION', 'mid': None},
            {'evidence_id': 4, 'evidence_role': 'DECISION', 'mid': Decimal('0.5')},
            {'evidence_id': 6, 'evidence_role': 'CLOSE', 'last': Decimal('1')}]
    chosen = store.decision_time_observation(rows)
    assert chosen['evidence_id'] == 4
    assert store.decision_price_status(chosen) == 'AVAILABLE'


def test_missing_decision_row_or_price_is_insufficient_not_zero():
    assert store.decision_price_status(None) == 'INSUFFICIENT'
    assert store.decision_price_status({'evidence_id': 1, 'evidence_role': 'DECISION',
                                        'last': None, 'mid': None, 'bid': None, 'ask': None}) == 'INSUFFICIENT'


def test_natural_alignment_mirrors_the_fixed_calibration_rule():
    assert store.natural_alignment('YES', 'YES')
    assert store.natural_alignment('YES-basket', 'YES-basket')
    assert not store.natural_alignment('YES', 'NO')
    assert store.natural_alignment('NO', 'NO')
    assert store.natural_alignment('NO-basket', 'NO')
    assert not store.natural_alignment('NO', 'YES')
    with pytest.raises(store.PapersimStoreError, match='papersim_side_token_invalid'):
        store.natural_alignment('OVER', 'YES')


def test_binding_token_status_flags_family_mismatch_and_stays_silent_without_tokens():
    assert store.binding_token_status('t-yes', 't-no', 't-yes', 't-no') is True
    assert store.binding_token_status('t-yes', 't-no', 'other-yes', 'other-no') is False
    assert store.binding_token_status(None, None, 't-yes', 't-no') is None
    assert store.binding_token_status('t-yes', None, None, None) is None
