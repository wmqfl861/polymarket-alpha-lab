"""Opt-in native proofs of the M2 S0 store operations.

Runs only inside the disposable native cluster environment. Per M2 plan
section 6.3 item 4 every test builds its OWN one-shot managed database
(never sharing rows with the M1 module faces or sibling tests), applies
the current complete catalog and proves the transactional contracts of
``mark_capture_upsert``, ``settlement_record`` and ``bankroll_daily_check``
against real PostgreSQL: atomic cash legs, idempotent re-runs,
changed-content conflicts, the one-close rules, the P3-9 close time-order
guard and the section 5.3 identity.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from pathlib import Path
import uuid

import pytest

from polymarket_alpha_lab.papersim import migrations as pm
from polymarket_alpha_lab.papersim import store

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN,
                            reason='explicit native papersim store proof is opt-in')


def adapter():
    if not ENABLED:
        pytest.skip('the papersim connection adapter runs in the native lane only')
    return pytest.importorskip('polymarket_alpha_lab.papersim.store_psycopg')


def at(minutes: int) -> datetime:
    return datetime(2026, 11, 16, 12, 0, tzinfo=UTC) + timedelta(minutes=minutes)


def connect(dsn, *, autocommit=True):
    psycopg = pytest.importorskip('psycopg')
    connection = psycopg.connect(dsn)
    connection.autocommit = autocommit
    return connection


def _rebind_database(dsn: str, database: str) -> str:
    return ' '.join(f'dbname={database}' if token.startswith('dbname=') else token
                    for token in dsn.split(' '))


@pytest.fixture
def m2_face():
    """One PRIVATE managed database per test (M2 plan section 6.3-4)."""
    psycopg = pytest.importorskip('psycopg')
    admin = connect(OWNER_DSN)
    name = 'pal_papersim_m2s_' + uuid.uuid4().hex[:10]
    with admin.cursor() as cursor:
        cursor.execute("SELECT system_identifier::text FROM pg_control_system()")
        system_id = cursor.fetchone()[0]
        cursor.execute(f"CREATE DATABASE {name} OWNER pal_owner")
    admin.close()
    owner_dsn = _rebind_database(OWNER_DSN, name)
    bootstrap = psycopg.connect(owner_dsn)
    bootstrap.autocommit = True
    with bootstrap.cursor() as cursor:
        from polymarket_alpha_lab.project_postgres import sql
        cursor.execute(sql.bootstrap_sql(uuid.uuid4().hex, 'b' * 64, system_id))
    bootstrap.close()
    owner = connect(owner_dsn)
    result = pm.apply_pending_migrations(owner, ROOT)
    assert result['applied'] == len(pm.papersim_migration_catalog(ROOT))
    assert pm.migration_status(owner, ROOT)['pending'] == []
    owner.close()
    app_dsn = owner_dsn.replace('user=pal_owner', 'user=pal_app')
    return owner_dsn, app_dsn


def outcome(**overrides):
    identity = overrides.get('market_slug') or 'm2-market'
    values = dict(condition_id=None, market_id=None, market_slug=None, yes_token_id=None,
                  no_token_id=None, outcome='YES', outcome_detail=None, confirmed_at=at(60),
                  observed_at=at(30), source_kind='gamma_close',
                  source_ref='close-' + identity, source_snapshot_sha256=None)
    values.update(overrides)
    return store.MarketOutcomeObservation(**values)


def evidence(**overrides):
    values = dict(observed_at=at(5), evidence_role='DECISION', model_probability=None,
                  market_ref_kind='market_slug', market_ref='m2-market', token_ref=None,
                  side='YES', bid=None, ask=None, mid=None, last=None, book_sha256=None,
                  snapshot_sha256=None, source_kind='gamma_close', source_ref='r1')
    values.update(overrides)
    return store.PriceEvidenceObservation(**values)


def entry(**overrides):
    values = dict(stake=Decimal('250'), price=Decimal('0.56'), shares=Decimal('446.4286'),
                  fee_category='crypto', fee_rate=Decimal('0.07'), entry_fee=Decimal('10.08'),
                  fee_source='schedule', tier='standard', entered_at=at(6), legacy_source_id=None,
                  is_legacy=False)
    values.update(overrides)
    return store.EntrySubmission(**values)


def candidate(**overrides):
    values = dict(candidate_key='m2-YES', conclusion='REJECTED', side='YES',
                  market_kind='terminal', team='crypto_eth', model_name='gbm',
                  model_version='r19', input_payload='{}', gate_report='{}', evaluated_at=at(5),
                  condition_id=None, market_id=None, market_slug='m2-market',
                  yes_token_id=None, no_token_id=None, market_type='terminal',
                  binding_version=1, median_probability=Decimal('0.44'),
                  variant_quotes='[]', snapshot_refs='[]', rejection_reasons='["edge"]',
                  order_book=None, book_fetched_at=None, legacy_source_id=None,
                  is_legacy=False, price_evidence=(), entry=None)
    values.update(overrides)
    return store.CandidateSubmission(**values)


def scan_run():
    return store.ScanRunRecord(team='crypto_eth', started_at=at(0), finished_at=at(6),
                               code_commit='c' * 40, schema_version=1, gate_version='g1',
                               operator='unattended',
                               params='{"run": "%s"}' % uuid.uuid4().hex[:8])


def capture(**overrides):
    values = dict(candidate_id=0, sampling_kind='PERIODIC', observed_at=at(30),
                  market_ref_kind='market_slug', market_ref='m2-market', token_ref=None,
                  side='YES', bid=Decimal('0.55'), ask=Decimal('0.58'), mid=Decimal('0.565'),
                  last=None, source_kind='clob_book', source_ref='book-1',
                  snapshot_sha256=None)
    values.update(overrides)
    return store.CandidateMarkCapture(**values)


def settlement(**overrides):
    values = dict(entry_id=0, classification='NATURAL', outcome_label='YES', win_loss='win',
                  exit_price=None, exit_fee=Decimal('0'), proceeds=Decimal('446.43'),
                  pnl_gross=Decimal('196.43'), fee_total=Decimal('10.08'),
                  fee_source='schedule', pnl_net=Decimal('186.35'), source_outcome_id=None,
                  exit_mark_id=None, evidence=None, confirmed_by='settle-op',
                  confirmed_at=at(90), is_legacy=False)
    values.update(overrides)
    return store.SettlementRecord(**values)


def _seed_accepted_entry(store_psycopg, app_dsn):
    """One scan with an accepted entry; returns (app, candidate_id, entry_id, slug).

    The adapter connection runs in autocommit mode so every store
    operation commits its own single transaction (the ops wrap themselves
    in ``conn.transaction()``) and bare reads never strand an open
    transaction across operations.
    """
    app = store_psycopg.connect_explicit(app_dsn, label='PAPERSIM_MIGRATIONS_PG_DSN')
    app.autocommit = True
    slug = 'm2-' + uuid.uuid4().hex[:10]
    result = store_psycopg.record_scan(app, store.ScanSubmission(
        scan=scan_run(), candidates=(candidate(
            candidate_key='k-' + slug, conclusion='ACCEPTED', market_slug=slug,
            price_evidence=(evidence(market_ref=slug),), entry=entry()),)))
    candidate_id = result['candidate_ids']['k-' + slug]
    with app.cursor() as cursor:
        cursor.execute('SELECT entry_id FROM papersim.entries WHERE candidate_id = %s',
                       (candidate_id,))
        entry_id = cursor.fetchone()[0]
    return app, candidate_id, entry_id, slug


# ---------------------------------------------------------------------------
# mark_capture_upsert (M2 plan section 3.5)
# ---------------------------------------------------------------------------

@NATIVE
def test_periodic_mark_capture_for_an_entryless_candidate(m2_face):
    store_psycopg = adapter()
    _, app_dsn = m2_face
    app = store_psycopg.connect_explicit(app_dsn, label='PAPERSIM_MIGRATIONS_PG_DSN')
    app.autocommit = True
    slug = 'm2-' + uuid.uuid4().hex[:10]
    result = store_psycopg.record_scan(app, store.ScanSubmission(
        scan=scan_run(), candidates=(candidate(candidate_key='rej', market_slug=slug),)))
    rejected_id = result['candidate_ids']['rej']
    first = store_psycopg.mark_capture_upsert(app, capture(candidate_id=rejected_id,
                                                           market_ref=slug))
    assert first['candidate_mark_inserted'] is True and first['entry_mark_id'] is None
    again = store_psycopg.mark_capture_upsert(app, capture(candidate_id=rejected_id,
                                                           market_ref=slug))
    assert again['candidate_mark_id'] == first['candidate_mark_id']
    assert again['candidate_mark_inserted'] is False
    drifted = capture(candidate_id=rejected_id, market_ref=slug, mid=Decimal('0.7'))
    with pytest.raises(store.PapersimStoreError, match='papersim_candidate_mark_conflict'):
        store_psycopg.mark_capture_upsert(app, drifted)
    with app.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.candidate_marks')
        assert cursor.fetchone()[0] == 1
        cursor.execute('SELECT count(*) FROM papersim.marks')
        assert cursor.fetchone()[0] == 0  # no entry: no marks row
    app.close()


@NATIVE
def test_mark_capture_with_entry_writes_both_faces_idempotently(m2_face):
    store_psycopg = adapter()
    _, app_dsn = m2_face
    app, candidate_id, entry_id, slug = _seed_accepted_entry(store_psycopg, app_dsn)
    mark = capture(candidate_id=candidate_id, market_ref=slug)
    first = store_psycopg.mark_capture_upsert(app, mark, entry_id=entry_id)
    assert first['candidate_mark_inserted'] is True
    assert first['entry_mark_inserted'] is True
    again = store_psycopg.mark_capture_upsert(app, mark, entry_id=entry_id)
    assert again['candidate_mark_id'] == first['candidate_mark_id']
    assert again['entry_mark_id'] == first['entry_mark_id']
    assert again['candidate_mark_inserted'] is False and again['entry_mark_inserted'] is False
    with app.cursor() as cursor:
        cursor.execute('SELECT bid, ask, mid, is_close FROM papersim.marks '
                       'WHERE mark_id = %s', (first['entry_mark_id'],))
        assert cursor.fetchone() == (Decimal('0.55'), Decimal('0.58'), Decimal('0.565'),
                                     False)
        cursor.execute('SELECT count(*) FROM papersim.marks')
        assert cursor.fetchone()[0] == 1
    drifted = capture(candidate_id=candidate_id, market_ref=slug, bid=Decimal('0.10'))
    with pytest.raises(store.PapersimStoreError, match='papersim_candidate_mark_conflict'):
        store_psycopg.mark_capture_upsert(app, drifted, entry_id=entry_id)
    app.close()


@NATIVE
def test_close_capture_is_unique_and_binds_close_evidence(m2_face):
    store_psycopg = adapter()
    _, app_dsn = m2_face
    app, candidate_id, entry_id, slug = _seed_accepted_entry(store_psycopg, app_dsn)
    close_evidence = evidence(evidence_role='CLOSE', observed_at=at(40), market_ref=slug,
                              source_kind='clob_terminal_price', source_ref='term-1',
                              last=Decimal('1'))
    close = capture(candidate_id=candidate_id, sampling_kind='CLOSE', observed_at=at(40),
                    market_ref=slug, source_kind='clob_terminal_price', source_ref='term-1',
                    bid=None, ask=None, mid=None, last=Decimal('1'))
    first = store_psycopg.mark_capture_upsert(app, close, entry_id=entry_id,
                                              close_evidence=close_evidence)
    assert first['close_evidence_inserted'] is True
    with app.cursor() as cursor:
        cursor.execute('SELECT is_close FROM papersim.marks WHERE mark_id = %s',
                       (first['entry_mark_id'],))
        assert cursor.fetchone()[0] is True
        cursor.execute("SELECT evidence_role, last FROM papersim.candidate_price_evidence "
                       "WHERE evidence_id = %s", (first['close_evidence_id'],))
        assert cursor.fetchone() == ('CLOSE', Decimal('1'))
    again = store_psycopg.mark_capture_upsert(app, close, entry_id=entry_id,
                                              close_evidence=close_evidence)
    assert again['close_evidence_id'] == first['close_evidence_id']
    assert again['close_evidence_inserted'] is False
    # A SECOND, later close of the same candidate is a conflict (one close).
    later = capture(candidate_id=candidate_id, sampling_kind='CLOSE', observed_at=at(50),
                    market_ref=slug, source_kind='clob_terminal_price', source_ref='term-2',
                    bid=None, ask=None, mid=None, last=Decimal('0'))
    with pytest.raises(store.PapersimStoreError, match='papersim_candidate_close_conflict'):
        store_psycopg.mark_capture_upsert(app, later, entry_id=entry_id)
    with app.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.candidate_marks '
                       'WHERE candidate_id = %s', (candidate_id,))
        assert cursor.fetchone()[0] == 1
    app.close()


@NATIVE
def test_close_evidence_before_the_decision_time_rejects_whole_capture(m2_face):
    store_psycopg = adapter()
    _, app_dsn = m2_face
    app, candidate_id, entry_id, slug = _seed_accepted_entry(store_psycopg, app_dsn)
    early_close = evidence(evidence_role='CLOSE', observed_at=at(4), market_ref=slug,
                           source_kind='clob_terminal_price', source_ref='early',
                           last=Decimal('0.9'))
    close = capture(candidate_id=candidate_id, sampling_kind='CLOSE', observed_at=at(4),
                    market_ref=slug, source_kind='clob_terminal_price', source_ref='early',
                    bid=None, ask=None, mid=None, last=Decimal('0.9'))
    with pytest.raises(store.PapersimStoreError,
                       match='papersim_evidence_time_order_invalid'):
        store_psycopg.mark_capture_upsert(app, close, entry_id=entry_id,
                                          close_evidence=early_close)
    with app.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.candidate_marks WHERE candidate_id = %s',
                       (candidate_id,))
        assert cursor.fetchone()[0] == 0  # nothing of the capture survived
        cursor.execute("SELECT count(*) FROM papersim.candidate_price_evidence "
                       "WHERE candidate_id = %s AND evidence_role = 'CLOSE'",
                       (candidate_id,))
        assert cursor.fetchone()[0] == 0
        cursor.execute('SELECT count(*) FROM papersim.marks WHERE entry_id = %s',
                       (entry_id,))
        assert cursor.fetchone()[0] == 0
    with pytest.raises(store.PapersimStoreError,
                       match='papersim_close_evidence_role_invalid'):
        store_psycopg.mark_capture_upsert(
            app, capture(candidate_id=candidate_id, sampling_kind='CLOSE', observed_at=at(40),
                         market_ref=slug, source_kind='clob_terminal_price',
                         source_ref='late', bid=None, ask=None, mid=None, last=Decimal('1')),
            entry_id=entry_id, close_evidence=evidence(evidence_role='DECISION',
                                                       observed_at=at(40), market_ref=slug,
                                                       last=Decimal('0.5')))
    app.close()


# ---------------------------------------------------------------------------
# settlement_record (M2 plan section 3.6)
# ---------------------------------------------------------------------------

@NATIVE
def test_natural_settlement_writes_settlement_and_cash_leg_atomically(m2_face):
    store_psycopg = adapter()
    _, app_dsn = m2_face
    app, candidate_id, entry_id, slug = _seed_accepted_entry(store_psycopg, app_dsn)
    outcome_id = store_psycopg.record_market_outcome(
        app, outcome(market_slug=slug))['outcome_id']
    record = settlement(entry_id=entry_id, source_outcome_id=outcome_id)
    first = store_psycopg.settlement_record(app, record)
    assert first['inserted'] is True
    with app.cursor() as cursor:
        cursor.execute('SELECT classification, win_loss, pnl_net FROM papersim.settlements '
                       'WHERE settlement_id = %s', (first['settlement_id'],))
        assert cursor.fetchone() == ('NATURAL', 'win', Decimal('186.35'))
        cursor.execute('SELECT event_key, amount, reason, entry_id FROM papersim.bankroll_events '
                       'WHERE event_id = %s', (first['bankroll_event_id'],))
        assert cursor.fetchone() == (f'settle:{entry_id}', Decimal('446.43'),
                                     'SETTLE_NATURAL', entry_id)
    again = store_psycopg.settlement_record(app, record)
    assert again['inserted'] is False
    assert again['settlement_id'] == first['settlement_id']
    assert again['bankroll_event_id'] == first['bankroll_event_id']
    with app.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.settlements WHERE entry_id = %s',
                       (entry_id,))
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT count(*) FROM papersim.bankroll_events "
                       "WHERE event_key = %s", (f'settle:{entry_id}',))
        assert cursor.fetchone()[0] == 1
    drifted = settlement(entry_id=entry_id, source_outcome_id=outcome_id,
                         proceeds=Decimal('999'), pnl_gross=Decimal('749'),
                         fee_total=Decimal('0'), pnl_net=Decimal('749'))
    with pytest.raises(store.PapersimStoreError, match='papersim_settlement_conflict'):
        store_psycopg.settlement_record(app, drifted)
    app.close()


@NATIVE
def test_broken_evidence_reference_rolls_back_settlement_and_cash(m2_face):
    store_psycopg = adapter()
    _, app_dsn = m2_face
    app, _, entry_id, _slug = _seed_accepted_entry(store_psycopg, app_dsn)
    record = settlement(entry_id=entry_id, source_outcome_id=987654321)
    psycopg = pytest.importorskip('psycopg')
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        store_psycopg.settlement_record(app, record)
    with app.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.settlements WHERE entry_id = %s',
                       (entry_id,))
        assert cursor.fetchone()[0] == 0  # no half settlement
        cursor.execute('SELECT count(*) FROM papersim.bankroll_events '
                       'WHERE event_key = %s', (f'settle:{entry_id}',))
        assert cursor.fetchone()[0] == 0  # and no orphan cash leg
    app.close()


@NATIVE
def test_exit_settlement_charges_only_the_sell_leg_in_cash(m2_face):
    store_psycopg = adapter()
    _, app_dsn = m2_face
    app, candidate_id, entry_id, slug = _seed_accepted_entry(store_psycopg, app_dsn)
    mark = capture(candidate_id=candidate_id, sampling_kind='CLOSE', observed_at=at(40),
                   market_ref=slug, source_kind='clob_terminal_price', source_ref='exit-book',
                   bid=Decimal('0.58'), ask=Decimal('0.59'), mid=Decimal('0.585'), last=None)
    close = store_psycopg.mark_capture_upsert(app, mark, entry_id=entry_id)
    record = settlement(entry_id=entry_id, classification='EXIT', outcome_label='EXITED',
                        win_loss=None, exit_price=Decimal('0.585'), exit_fee=Decimal('5.05'),
                        proceeds=Decimal('339.30'), pnl_gross=Decimal('-60.70'),
                        fee_total=Decimal('15.13'), pnl_net=Decimal('-75.83'),
                        exit_mark_id=close['entry_mark_id'])
    result = store_psycopg.settlement_record(app, record)
    with app.cursor() as cursor:
        cursor.execute('SELECT amount, reason FROM papersim.bankroll_events '
                       'WHERE event_id = %s', (result['bankroll_event_id'],))
        assert cursor.fetchone() == (Decimal('334.25'), 'SETTLE_EXIT')
        cursor.execute('SELECT exit_price, win_loss FROM papersim.settlements '
                       'WHERE settlement_id = %s', (result['settlement_id'],))
        assert cursor.fetchone() == (Decimal('0.585'), None)
    app.close()


# ---------------------------------------------------------------------------
# bankroll_daily_check (M2 plan section 5.3)
# ---------------------------------------------------------------------------

def _seed_initial(store_psycopg, app_dsn, amount=Decimal('10000')):
    app = store_psycopg.connect_explicit(app_dsn, label='PAPERSIM_MIGRATIONS_PG_DSN')
    app.autocommit = True
    with app.cursor() as cursor:
        cursor.execute('INSERT INTO papersim.bankroll_events (event_key, occurred_at, '
                       'currency, amount, reason) VALUES (%s,%s,%s,%s,%s) RETURNING event_id',
                       ('initial-' + uuid.uuid4().hex[:8], at(0), 'USD', amount, 'INITIAL'))
        initial_id = cursor.fetchone()[0]
    return app, initial_id


@NATIVE
def test_daily_check_recomputes_the_identity_and_is_idempotent(m2_face):
    store_psycopg = adapter()
    owner_dsn, app_dsn = m2_face
    app, initial_id = _seed_initial(store_psycopg, app_dsn)
    _, candidate_id, entry_id, _slug = _seed_accepted_entry(store_psycopg, app_dsn)
    request = store.BankrollCheckRequest(as_of=at(120), code_commit='c' * 40)
    first = store_psycopg.bankroll_daily_check(app, request)
    assert first.inserted is True and first.passed is True
    assert first.event_watermark == initial_id + 1
    assert first.cash_total == Decimal('9739.92')
    assert first.deployed_total == Decimal('250')
    assert first.initial_and_adjustments == Decimal('10000')
    assert first.open_entry_fees == Decimal('10.08')
    assert first.identity_difference == Decimal('0.00')
    again = store_psycopg.bankroll_daily_check(app, request)
    assert again.inserted is False and again.check_id == first.check_id
    # Settling the entry moves the watermark: the next check APPENDS a row.
    store_psycopg.settlement_record(app, settlement(entry_id=entry_id))
    later = store_psycopg.bankroll_daily_check(app, request)
    assert later.inserted is True and later.check_id != first.check_id
    assert later.event_watermark > first.event_watermark
    assert later.deployed_total == 0 and later.passed is True
    with app.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.bankroll_checks')
        assert cursor.fetchone()[0] == 2
    # A stored row with the SAME computed key but different totals is a
    # conflict: the key is derived from the real watermark under a DISTINCT
    # code commit (so it collides with no genuine check row), then the row
    # is pre-inserted with a wrong cash total through the owner connection.
    probe = store.BankrollCheckRequest(as_of=at(200), code_commit='d' * 40)
    with app.cursor() as cursor:
        cursor.execute('SELECT coalesce(max(event_id), 0) FROM papersim.bankroll_events '
                       'WHERE occurred_at <= %s', (probe.as_of,))
        watermark = int(cursor.fetchone()[0])
    forged_key = store.bankroll_check_key(probe.as_of, probe.code_commit, watermark)
    owner = connect(owner_dsn)
    with owner.cursor() as cursor:
        cursor.execute('INSERT INTO papersim.bankroll_checks (check_key, as_of, '
                       'event_watermark, cash_total, deployed_total, initial_and_adjustments, '
                       'realized_net, open_entry_fees, identity_difference, tolerance, passed, '
                       'code_commit) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                       (forged_key, probe.as_of, watermark, Decimal('1'), 0, 0, 0, 0, 1,
                        Decimal('0.005'), False, 'd' * 40))
    owner.close()
    with pytest.raises(store.PapersimStoreError, match='papersim_bankroll_check_conflict'):
        store_psycopg.bankroll_daily_check(app, probe)
    app.close()


@NATIVE
def test_daily_check_excludes_post_as_of_rows(m2_face):
    store_psycopg = adapter()
    _, app_dsn = m2_face
    app, initial_id = _seed_initial(store_psycopg, app_dsn)
    request = store.BankrollCheckRequest(as_of=at(10), code_commit='c' * 40)
    first = store_psycopg.bankroll_daily_check(app, request)
    assert first.cash_total == Decimal('10000')
    # An event occurring AFTER as_of never mixes into the check.
    with app.cursor() as cursor:
        cursor.execute('INSERT INTO papersim.bankroll_events (event_key, occurred_at, '
                       'currency, amount, reason) VALUES (%s,%s,%s,%s,%s)',
                       ('late-' + uuid.uuid4().hex[:8], at(300), 'USD', Decimal('-50'),
                        'ADJUSTMENT'))
    app.commit()
    same = store_psycopg.bankroll_daily_check(app, request)
    assert same.inserted is False and same.check_id == first.check_id
    assert same.cash_total == Decimal('10000')
    wider = store.BankrollCheckRequest(as_of=at(400), code_commit='c' * 40)
    moved = store_psycopg.bankroll_daily_check(app, wider)
    assert moved.cash_total == Decimal('9950')
    assert moved.initial_and_adjustments == Decimal('9950')
    assert moved.identity_difference == 0 and moved.passed is True
    app.close()
