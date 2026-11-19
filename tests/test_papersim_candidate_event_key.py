"""FX-26 candidate event key and match-clustered interval tests (N-1).

Pure key shapes (fix-list FX-26): sports keys on the Gamma event id with
the canonical league|home|away|day fallback string; crypto keys on the
asset plus the UTC expiry date; other markets key on their own identity;
insufficient inputs never fabricate a key (``None`` keeps the frozen
market-key clustering). The interval proof builds one queue out of TWO
matches whose three lines each resolve completely co-directionally:
clustering by the match key must yield a strictly WIDER bootstrap ROI
interval than treating the same rows as independent markets. Also proves
the new CandidateSubmission fields validate, ride the scan payload
digest, and pass through ``replay.bind_candidate``.
"""

from datetime import UTC, datetime, timedelta

import pytest

from polymarket_alpha_lab import replay
from polymarket_alpha_lab.papersim import evaluation as ev
from polymarket_alpha_lab.papersim import store

_T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def candidate(**overrides):
    base = dict(
        candidate_key='c1', conclusion='REJECTED', side='YES', market_kind='terminal',
        team='crypto_eth', model_name='gbm', model_version='r19', input_payload='{}',
        gate_report='{}', evaluated_at=_T0, market_slug='mkt-1')
    base.update(overrides)
    return store.CandidateSubmission(**base)


# ---------------------------------------------------------------------------
# The pure key builder
# ---------------------------------------------------------------------------

def test_sports_key_prefers_the_gamma_event_id():
    assert store.candidate_event_key(market_class='sports', gamma_event_id='4242') \
        == 'gamma:4242'
    assert store.candidate_event_key(market_class='sports', gamma_event_id=' 4242 ') \
        == 'gamma:4242'


def test_sports_fallback_is_the_canonical_league_home_away_day_string():
    key = store.candidate_event_key(market_class='sports', league='EPL',
                                    home='Arsenal', away='Chelsea',
                                    kickoff_day='2026-11-21')
    assert key == 'match:EPL|Arsenal|Chelsea|2026-11-21'
    assert store.candidate_event_key(market_class='sports', league=' EPL ',
                                     home='Arsenal', away='Chelsea',
                                     kickoff_day='2026-11-21') == key


def test_sports_without_id_and_without_full_fallback_yields_none():
    assert store.candidate_event_key(market_class='sports') is None
    assert store.candidate_event_key(market_class='sports', league='EPL',
                                     home='Arsenal', away='Chelsea') is None


def test_crypto_key_is_asset_plus_utc_expiry_date():
    expiry = datetime(2026, 12, 31, 23, 0, tzinfo=UTC)
    assert store.candidate_event_key(market_class='crypto', asset='ETH',
                                     expiry_at=expiry) == 'crypto:ETH|2026-12-31'
    assert store.candidate_event_key(market_class='crypto', asset='ETH') is None
    assert store.candidate_event_key(market_class='crypto', expiry_at=expiry) is None
    naive = datetime(2026, 12, 31, 23, 0)
    assert store.candidate_event_key(market_class='crypto', asset='ETH',
                                     expiry_at=naive) is None


def test_other_markets_key_on_their_own_identity():
    assert store.candidate_event_key(market_class='other',
                                     market_ref='0xabc') == 'market:0xabc'
    assert store.candidate_event_key(market_class='other') is None


def test_unknown_market_class_fails_loudly():
    with pytest.raises(store.PapersimStoreError,
                       match='papersim_event_key_market_class_invalid'):
        store.candidate_event_key(market_class='politics')
    with pytest.raises(store.PapersimStoreError,
                       match='papersim_event_key_market_class_invalid'):
        store.candidate_event_key(market_class='')


# ---------------------------------------------------------------------------
# CandidateSubmission validation and the scan payload digest
# ---------------------------------------------------------------------------

def test_candidate_submission_validates_the_new_fields():
    keyed = candidate(event_key='gamma:4242', event_start_at=_T0)
    assert keyed.validate() is keyed
    with pytest.raises(store.PapersimStoreError, match='papersim_event_key_invalid'):
        candidate(event_key='').validate()
    naive = datetime(2026, 11, 20, 12, 0)
    with pytest.raises(store.PapersimStoreError,
                       match='papersim_event_start_at_invalid'):
        candidate(event_start_at=naive).validate()


def _scan():
    return store.ScanRunRecord(team='crypto_eth', started_at=_T0,
                               finished_at=_T0 + timedelta(minutes=6),
                               code_commit='c' * 40, schema_version=1,
                               gate_version='g1', operator='unattended', params='{}')


def test_scan_payload_digest_covers_the_new_fields():
    # Pattern: test_payload_digest_changes_when_any_candidate_changes
    # (tests/test_papersim_store.py) -- the digest must change when either
    # new field changes and stay deterministic for equal inputs.
    scan = _scan()
    base = store.ScanSubmission(scan=scan, candidates=(candidate(),))
    keyed = store.ScanSubmission(
        scan=scan, candidates=(candidate(event_key='gamma:4242'),))
    keyed_start = store.ScanSubmission(
        scan=scan, candidates=(candidate(event_start_at=_T0),))
    assert base.payload_sha256() != keyed.payload_sha256()
    assert base.payload_sha256() != keyed_start.payload_sha256()
    assert keyed.payload_sha256() != keyed_start.payload_sha256()
    once = store.ScanSubmission(
        scan=scan, candidates=(candidate(event_key='gamma:4242', event_start_at=_T0),))
    twice = store.ScanSubmission(
        scan=scan, candidates=(candidate(event_key='gamma:4242', event_start_at=_T0),))
    assert once.payload_sha256() == twice.payload_sha256()


# ---------------------------------------------------------------------------
# replay.bind_candidate carries both values (defaults keep the old shape)
# ---------------------------------------------------------------------------

def test_bind_candidate_carries_both_values():
    from polymarket_alpha_lab.papersim import gates
    from polymarket_alpha_lab.papersim.crypto_model import TerminalProbability
    from polymarket_alpha_lab.papersim.pipeline import EntryRequest, decide_entry
    request = EntryRequest(
        market_id='m-fx26', question='Ethereum below $2,000 on date',
        fixture='ETH-2000', market_kind='crypto_terminal', side='yes',
        book=[(0.15, 5000.0)], fee_category='crypto',
        probability=TerminalProbability(asset='ETH', barrier=2000.0,
                                        horizon_hours=1595.12,
                                        convention_a=0.2224, convention_b=0.2531),
        yes_when='below', buy_side='yes', yes_ask=0.15, no_ask=0.80,
        gamma_liquidity=6000.0,
        news=gates.check_news('ETH-2000', 'recheck T-1d', news_delta='unchanged'),
        bankroll=10_000.0, cash=10_000.0, reserved=0.0,
        book_time=_T0, now=_T0, max_book_age_seconds=300.0)
    decision = decide_entry(request)
    bound = replay.bind_candidate(
        request, decision, team='crypto_eth', candidate_key='fx26-1',
        side='YES', market_kind='terminal', evaluated_at=_T0,
        market_ref_kind='market_id', market_id='m-fx26',
        event_key='crypto:ETH|2026-12-31', event_start_at=_T0)
    assert bound.event_key == 'crypto:ETH|2026-12-31'
    assert bound.event_start_at == _T0
    plain = replay.bind_candidate(
        request, decision, team='crypto_eth', candidate_key='fx26-2',
        side='YES', market_kind='terminal', evaluated_at=_T0,
        market_ref_kind='market_id', market_id='m-fx26')
    assert plain.event_key is None and plain.event_start_at is None


# ---------------------------------------------------------------------------
# Match clustering widens the interval for co-directional lines (N-1)
# ---------------------------------------------------------------------------

def _entry_row(index, match_key, won):
    return ev.EvaluationCandidate(
        candidate_id=f'c{index}', model_key='soccer-v1', market_type='ou_over',
        gate_version='gates-v1', market_key=f'mk{index}',
        model_probability=0.65, market_mid=0.50, accepted=True,
        stake=100.0, fill_price=0.50, side='YES', fee_category='sports',
        fill_provenance=ev.FILL_BOOK_WALK,
        outcome='YES' if won else 'NO',
        recorded_pnl=100.0 if won else -100.0,
        decision_time=_T0, settled_time=_T0 + timedelta(days=1),
        close_price=None, match_key=match_key)


def _two_matches(match_key_of_won, match_key_of_lost):
    # Three lines of each match resolve completely co-directionally: the
    # won match pays +100 on every line, the lost match burns -100 on
    # every line. Six markets, two real events.
    rows = [_entry_row(i, match_key_of_won, True) for i in range(3)]
    rows += [_entry_row(3 + i, match_key_of_lost, False) for i in range(3)]
    return tuple(rows)


def test_same_match_codirectional_lines_yield_a_wider_interval():
    independent = _two_matches(None, None)          # six independent markets
    clustered = _two_matches('match:EPL|Arsenal|Chelsea|2026-11-21',
                             'match:EPL|Liverpool|Everton|2026-11-21')
    assert len(ev.cluster_rows(clustered)) == 2
    assert len(ev.cluster_rows(independent)) == 6
    unkeyed = ev.evaluate_queues(independent).queues[0].roi
    keyed = ev.evaluate_queues(clustered).queues[0].roi
    # Point estimates are identical: clustering never moves the center.
    assert keyed.gross_roi == pytest.approx(unkeyed.gross_roi)
    assert keyed.counted == unkeyed.counted == 6
    # Clustering by the match key must yield a STRICTLY wider interval:
    # treating correlated lines as independent narrows it (N-1).
    width_unkeyed = unkeyed.gross_roi_ci[1] - unkeyed.gross_roi_ci[0]
    width_keyed = keyed.gross_roi_ci[1] - keyed.gross_roi_ci[0]
    assert width_keyed > width_unkeyed
    net_width_unkeyed = unkeyed.net_roi_ci[1] - unkeyed.net_roi_ci[0]
    net_width_keyed = keyed.net_roi_ci[1] - keyed.net_roi_ci[0]
    assert net_width_keyed > net_width_unkeyed
