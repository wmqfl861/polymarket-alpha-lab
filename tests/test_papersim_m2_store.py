"""Pure (offline) proofs of the M2 S0 store surface.

Covers the writer-side idempotency keys (M2 plan section 3.2 two-layer
identity), the CandidateMarkCapture / SettlementRecord / BankrollCheck
validation contracts and the M2 plan section 5.3 cash identity arithmetic. No
connection, clock or driver is touched; the native transactional proofs
live in ``tests/test_papersim_m2_store_native.py``.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from polymarket_alpha_lab.papersim import store


def at(minutes: int) -> datetime:
    return datetime(2026, 11, 16, 0, 0, tzinfo=UTC) + timedelta(minutes=minutes)


def capture(**overrides):
    values = dict(candidate_id=11, sampling_kind='PERIODIC', observed_at=at(30),
                  market_ref_kind='market_slug', market_ref='fed-hike-jan', token_ref=None,
                  side='YES', bid=Decimal('0.55'), ask=Decimal('0.58'), mid=Decimal('0.565'),
                  last=None, source_kind='clob_book', source_ref='book-1',
                  snapshot_sha256=None)
    values.update(overrides)
    return store.CandidateMarkCapture(**values)


def settlement(**overrides):
    values = dict(entry_id=7, classification='NATURAL', outcome_label='YES', win_loss='win',
                  exit_price=None, exit_fee=Decimal('0'), proceeds=Decimal('446.43'),
                  pnl_gross=Decimal('196.43'), fee_total=Decimal('10.08'),
                  fee_source='schedule', pnl_net=Decimal('186.35'),
                  source_outcome_id=None, exit_mark_id=None, evidence=None,
                  confirmed_by='settle-op', confirmed_at=at(90), is_legacy=False)
    values.update(overrides)
    return store.SettlementRecord(**values)


# ---------------------------------------------------------------------------
# Idempotency keys (M2 plan section 3.2 two-layer identity)
# ---------------------------------------------------------------------------

def test_command_run_event_key_is_the_stable_scheduling_identity():
    key = store.command_run_event_key('run-scan|crypto_eth|20261116T0000Z|b1', 2, 'STARTED')
    assert key == 'cre|run-scan|crypto_eth|20261116T0000Z|b1|2|STARTED'
    assert store.command_run_event_key('k', 1, 'SUCCEEDED') == 'cre|k|1|SUCCEEDED'
    assert store.command_run_event_key('k', 1, 'FAILED') != store.command_run_event_key(
        'k', 2, 'FAILED')


def test_command_run_event_key_rejects_bad_attempt_and_phase():
    with pytest.raises(store.PapersimStoreError, match='papersim_attempt_invalid'):
        store.command_run_event_key('k', 0, 'STARTED')
    with pytest.raises(store.PapersimStoreError, match='papersim_attempt_invalid'):
        store.command_run_event_key('k', '1', 'STARTED')
    with pytest.raises(store.PapersimStoreError, match='papersim_phase_invalid'):
        store.command_run_event_key('k', 1, 'RETRIED')


def test_candidate_mark_and_entry_mark_keys_pin_the_observation():
    mark_key = store.candidate_mark_key(11, 'market_slug', 'fed-hike-jan', 'YES',
                                        'clob_book', at(30))
    assert mark_key == 'cm|11|market_slug|fed-hike-jan|YES|clob_book|20261116003000'
    assert store.entry_mark_key(4, 'clob_book', at(30)) == 'mk|4|clob_book|20261116003000'
    # Same second, different observation source is a different identity.
    assert mark_key != store.candidate_mark_key(11, 'market_slug', 'fed-hike-jan', 'YES',
                                                'gamma_outcome', at(30))


def test_queue_control_key_and_bankroll_check_key_shapes():
    control = store.queue_control_key('cross|crypto_eth', 'PAUSE', 'fx17_cross_league_loss',
                                      'v1', at(45))
    assert control == 'qc|cross|crypto_eth|PAUSE|fx17_cross_league_loss|v1|20261116004500'
    check = store.bankroll_check_key(at(150), 'a' * 40, 33)
    assert check == 'bc|20261116|' + 'a' * 40 + '|33'
    # The day bucket follows UTC regardless of the input offset.
    offset = datetime(2026, 11, 17, 1, 0, tzinfo=timezone(timedelta(hours=5)))
    assert store.bankroll_check_key(offset, 'a' * 40, 0).startswith('bc|20261116|')
    with pytest.raises(store.PapersimStoreError, match='papersim_control_kind_invalid'):
        store.queue_control_key('q', 'UNPAUSE', 'r', 'v1', at(1))
    with pytest.raises(store.PapersimStoreError, match='papersim_event_watermark_invalid'):
        store.bankroll_check_key(at(1), 'a' * 40, -1)
    with pytest.raises(store.PapersimStoreError, match='papersim_code_commit_invalid'):
        store.bankroll_check_key(at(1), 'a' * 39, 0)


# ---------------------------------------------------------------------------
# CandidateMarkCapture contract
# ---------------------------------------------------------------------------

def test_capture_accepts_terminal_closed_interval_prices():
    assert capture(bid=Decimal('0'), ask=Decimal('1'), mid=Decimal('0.5')).validate() is not None
    assert capture(bid=None, ask=None, mid=None, last=Decimal('1')).validate() is not None


def test_capture_rejects_out_of_range_and_crossed_prices():
    with pytest.raises(store.PapersimStoreError, match='papersim_bid_invalid'):
        capture(bid=Decimal('1.01')).validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_ask_invalid'):
        capture(ask=Decimal('-0.1')).validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_bid_ask_crossed'):
        capture(bid=Decimal('0.6'), ask=Decimal('0.55')).validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_mark_price_missing'):
        capture(bid=None, ask=None, mid=None, last=None).validate()


def test_capture_requires_a_candidate_and_a_sampling_kind():
    with pytest.raises(store.PapersimStoreError, match='papersim_candidate_id_invalid'):
        capture(candidate_id='11').validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_sampling_kind_invalid'):
        capture(sampling_kind='INTRADAY').validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_side_token_invalid'):
        capture(side='OVER').validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_snapshot_sha256_invalid'):
        capture(snapshot_sha256='zz' * 32).validate()


# ---------------------------------------------------------------------------
# SettlementRecord contract (M2 plan section 3.6)
# ---------------------------------------------------------------------------

def test_settlement_natural_win_carries_win_and_exact_pnl_identity():
    record = settlement()
    record.validate()
    assert record.cash_reason() == 'SETTLE_NATURAL'
    assert record.cash_amount() == Decimal('446.43')
    assert record.event_key() == 'settle:7'


def test_settlement_classification_win_loss_pairing_stays_pinned():
    with pytest.raises(store.PapersimStoreError, match='papersim_settlement_win_loss_invalid'):
        settlement(win_loss=None).validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_settlement_win_loss_invalid'):
        settlement(classification='EXIT', win_loss='win').validate()


def test_settlement_exit_requires_price_and_charges_only_the_sell_leg_in_cash():
    record = settlement(classification='EXIT', win_loss=None, exit_price=Decimal('0.585'),
                        exit_fee=Decimal('5.05'), proceeds=Decimal('339.30'),
                        pnl_gross=Decimal('-60.70'), fee_total=Decimal('15.13'),
                        pnl_net=Decimal('-75.83'))
    record.validate()
    assert record.cash_reason() == 'SETTLE_EXIT'
    assert record.cash_amount() == Decimal('339.30') - Decimal('5.05')
    with pytest.raises(store.PapersimStoreError,
                       match='papersim_settlement_exit_price_missing'):
        settlement(classification='EXIT', win_loss=None).validate()
    with pytest.raises(store.PapersimStoreError,
                       match='papersim_settlement_exit_fee_invalid'):
        settlement(exit_fee=Decimal('1')).validate()


def test_settlement_rejects_pnl_mismatch_and_non_monetary_values():
    with pytest.raises(store.PapersimStoreError, match='papersim_settlement_pnl_mismatch'):
        settlement(pnl_net=Decimal('999')).validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_proceeds_invalid'):
        settlement(proceeds=Decimal('-1')).validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_fee_total_invalid'):
        settlement(fee_total=Decimal('-0.01')).validate()
    with pytest.raises(store.PapersimStoreError, match='papersim_exit_price_invalid'):
        settlement(classification='EXIT', win_loss=None, exit_price=Decimal('1.5')).validate()


# ---------------------------------------------------------------------------
# Bankroll check request and the M2 plan section 5.3 identity
# ---------------------------------------------------------------------------

def test_bankroll_check_request_defaults_and_key():
    request = store.BankrollCheckRequest(as_of=at(120), code_commit='b' * 40)
    request.validate()
    assert request.tolerance == store.BANKROLL_TOLERANCE == Decimal('0.005')
    assert request.key(9) == 'bc|20261116|' + 'b' * 40 + '|9'
    with pytest.raises(store.PapersimStoreError, match='papersim_tolerance_invalid'):
        store.BankrollCheckRequest(as_of=at(1), code_commit='b' * 40,
                                   tolerance=Decimal('-1')).validate()


def test_bankroll_identity_difference_holds_for_open_and_settled_positions():
    # Open entry: I=10000, stake=250, entry fee=10.08 paid in SCAN_ENTRY.
    open_case = store.bankroll_identity_difference(
        Decimal('9739.92'), Decimal('250'), Decimal('10000'), Decimal('0'), Decimal('10.08'))
    assert open_case == Decimal('0.00')
    # Same entry naturally settled as a win: proceeds 446.43, pnl_net 186.35.
    settled = store.bankroll_identity_difference(
        Decimal('10186.35'), Decimal('0'), Decimal('10000'), Decimal('186.35'), Decimal('0'))
    assert settled == Decimal('0.00')
    # A half-cent breach is visible, not rounded away.
    breached = store.bankroll_identity_difference(
        Decimal('9739.93'), Decimal('250'), Decimal('10000'), Decimal('0'), Decimal('10.08'))
    assert abs(breached) == Decimal('0.01')
