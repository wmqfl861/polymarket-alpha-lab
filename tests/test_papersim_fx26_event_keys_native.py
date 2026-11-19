"""Opt-in native FX-26 candidate event-key proofs (2026-11-19 fix-list FX-26 / N-1).

Runs only inside the disposable native cluster environment (the CI
throwaway papersim partition or an explicitly assembled local cluster).
Never touches a business cluster: every test builds its own private
managed-shaped database on the throwaway cluster. Proves: the two new
columns round-trip through the full ``bind_candidate`` binding plus
``record_scan``; ``record_scan`` idempotency and the payload-conflict
boundary extend over the new fields; a fully un-keyed store evaluates
bit-for-bit like the frozen market-key behavior (loaded match keys all
``None``, one cluster per market, the report equal to hand-built rows);
and keyed rows cluster the evaluation by the match key.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
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
                            reason='explicit native papersim proof is opt-in')


def at(minutes: int) -> datetime:
    return datetime(2026, 11, 19, 12, 0, tzinfo=UTC) + timedelta(minutes=minutes)


def _connect(dsn, *, autocommit=True):
    psycopg = pytest.importorskip('psycopg')
    connection = psycopg.connect(dsn)
    connection.autocommit = autocommit
    return connection


def _rebind_database(dsn: str, database: str) -> str:
    return ' '.join(f'dbname={database}' if token.startswith('dbname=') else token
                    for token in dsn.split(' '))


def _fresh_managed_database() -> str:
    """A private migrated APP-dsn database inside the throwaway cluster."""
    psycopg = pytest.importorskip('psycopg')
    admin = _connect(OWNER_DSN)
    name = 'pal_papersim_fx26k_' + uuid.uuid4().hex[:10]
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
    owner = _connect(owner_dsn, autocommit=False)
    assert pm.apply_pending_migrations(owner, ROOT) == {'applied': 5, 'pending_before': 5}
    owner.commit()
    owner.close()
    return owner_dsn.replace('user=pal_owner', 'user=pal_app')


def _app(dsn):
    from polymarket_alpha_lab.papersim import store_psycopg
    app = store_psycopg.connect_explicit(dsn, label='PAPERSIM_MIGRATIONS_PG_DSN')
    app.autocommit = True
    return app


@NATIVE
def test_event_columns_round_trip_through_the_full_binding():
    from polymarket_alpha_lab import replay
    from polymarket_alpha_lab.papersim import snapshots, store_psycopg
    from polymarket_alpha_lab.papersim.crypto_model import TerminalProbability
    from polymarket_alpha_lab.papersim.gates import check_news
    from polymarket_alpha_lab.papersim.pipeline import EntryRequest, decide_entry
    app_dsn = _fresh_managed_database()
    app = _app(app_dsn)
    now = at(0)
    scan = replay.bind_scan_run(team='crypto_eth', started_at=now - timedelta(minutes=10),
                                finished_at=now + timedelta(minutes=5), code_commit='c' * 40)
    request = EntryRequest(
        market_id='m-fx26-keyed', question='Ethereum below $2,000 on date',
        fixture='ETH-2000', market_kind='crypto_terminal', side='yes',
        book=[(0.15, 5000.0)], fee_category='crypto',
        probability=TerminalProbability(asset='ETH', barrier=2000.0,
                                        horizon_hours=1595.12,
                                        convention_a=0.2224, convention_b=0.2531),
        yes_when='below', buy_side='yes', yes_ask=0.15, no_ask=0.80,
        gamma_liquidity=6000.0,
        news=check_news('ETH-2000', 'recheck T-1d', news_delta='unchanged'),
        bankroll=10_000.0, cash=10_000.0, reserved=0.0,
        book_time=now, now=now, max_book_age_seconds=300.0)
    decision = decide_entry(request)
    digest = snapshots.register_snapshot(app, snapshots.SnapshotRegistration(
        content=replay.bound_book_bytes(request), kind='clob_book',
        source='clob:m-fx26-keyed', fetched_at=request.book_time))['content_sha256']
    keyed = replay.bind_candidate(
        request, decision, team='crypto_eth', candidate_key='fx26-keyed',
        side='YES', market_kind='terminal', evaluated_at=now,
        market_ref_kind='market_id', market_id='m-fx26-keyed',
        median_probability=replay.bound_median_probability(request, decision),
        snapshot_refs=[digest], book_snapshot_sha256=digest,
        event_key='crypto:ETH|2026-12-31', event_start_at=now,
        entry=replay.bind_entry(decision, entered_at=now) if decision.accepted else None)
    plain = replay.bind_candidate(
        request, decision, team='crypto_eth', candidate_key='fx26-plain',
        side='NO', market_kind='terminal', evaluated_at=now,
        market_ref_kind='market_id', market_id='m-fx26-keyed',
        snapshot_refs=[digest], book_snapshot_sha256=digest)
    store_psycopg.record_scan(
        app, store.ScanSubmission(scan=scan, candidates=(keyed, plain)))
    with app.cursor() as cursor:
        cursor.execute('SELECT event_key, event_start_at FROM papersim.candidates '
                       'WHERE candidate_key = %s', ('fx26-keyed',))
        assert cursor.fetchone() == ('crypto:ETH|2026-12-31', now)
        cursor.execute('SELECT event_key, event_start_at FROM papersim.candidates '
                       'WHERE candidate_key = %s', ('fx26-plain',))
        assert cursor.fetchone() == (None, None)
    app.close()


@NATIVE
def test_record_scan_idempotency_and_conflict_cover_the_new_fields():
    from polymarket_alpha_lab.papersim import store_psycopg
    app_dsn = _fresh_managed_database()
    app = _app(app_dsn)
    scan = store.ScanRunRecord(team='crypto_eth', started_at=at(0), finished_at=at(6),
                               code_commit='c' * 40, schema_version=1, gate_version='g1',
                               operator='unattended',
                               params='{"run": "%s"}' % uuid.uuid4().hex[:8])
    candidate = store.CandidateSubmission(
        candidate_key='idem-1', conclusion='ACCEPTED', side='YES', market_kind='terminal',
        team='crypto_eth', model_name='gbm', model_version='r19', input_payload='{}',
        gate_report='{}', evaluated_at=at(5), market_slug='mkt-idem',
        event_key='gamma:4242', event_start_at=at(1),
        entry=store.EntrySubmission(stake=250, price=0.56, shares=446.4286,
                                    fee_category='crypto', fee_rate=0.07, entry_fee=10.08,
                                    tier='standard', entered_at=at(6)))
    submission = store.ScanSubmission(scan=scan, candidates=(candidate,))
    first = store_psycopg.record_scan(app, submission)
    assert first['inserted'] is True
    # Same version, same key, same payload (new fields included): the
    # idempotent return, ids unchanged (precedent: the store native proof).
    again = store_psycopg.record_scan(app, submission)
    assert again['inserted'] is False and again['scan_id'] == first['scan_id']
    assert again['candidate_ids'] == first['candidate_ids']
    # Same scan_key with ONLY the event key drifted: the payload digest
    # changed, so the write is refused as a conflict (FX-26 contract).
    drifted = store.ScanSubmission(
        scan=scan, candidates=(replace(candidate, event_key='gamma:9999'),))
    with pytest.raises(store.PapersimStoreError, match='papersim_scan_payload_conflict'):
        store_psycopg.record_scan(app, drifted)
    with app.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.scan_runs WHERE scan_key = %s',
                       (scan.key(),))
        assert cursor.fetchone()[0] == 1
    app.close()


def _seed_three_entered_candidates(app, suffix, event_keys):
    """Three accepted+entered candidates with DECISION/CLOSE marks and YES outcomes."""
    from polymarket_alpha_lab.papersim import store_psycopg
    scan = store.ScanRunRecord(team='crypto_eth', started_at=at(0), finished_at=at(30),
                               code_commit='c' * 40, schema_version=1, gate_version='g1',
                               operator='unattended',
                               params='{"run": "%s-%s"}' % (suffix, uuid.uuid4().hex[:4]))
    candidates = []
    for index, key in enumerate(event_keys):
        slug = f'mkt-{suffix}-{index}'
        decision = store.PriceEvidenceObservation(
            observed_at=at(5 + index), evidence_role='DECISION',
            model_probability=Decimal('0.2224'), market_ref_kind='market_slug',
            market_ref=slug, token_ref=None, side='YES',
            bid=None, ask=Decimal('0.15'), mid=Decimal('0.15'), last=None,
            source_kind='clob_book', source_ref=f'market_slug:{slug}')
        close = store.PriceEvidenceObservation(
            observed_at=at(60 + index), evidence_role='CLOSE',
            model_probability=None, market_ref_kind='market_slug',
            market_ref=slug, token_ref=None, side='YES',
            bid=None, ask=None, mid=None, last=Decimal('0.9'),
            source_kind='gamma_close', source_ref=f'close-{slug}')
        candidates.append(store.CandidateSubmission(
            candidate_key=f'{suffix}-{index}', conclusion='ACCEPTED', side='YES',
            market_kind='terminal', team='crypto_eth', model_name='gbm',
            model_version='r19', input_payload='{}', gate_report='{}',
            evaluated_at=at(5 + index), market_slug=slug,
            event_key=key, event_start_at=None,
            entry=store.EntrySubmission(stake=250, price=0.56, shares=446.4286,
                                        fee_category='crypto', fee_rate=0.07,
                                        entry_fee=10.08, tier='standard',
                                        entered_at=at(6 + index)),
            price_evidence=(decision, close)))
    written = store_psycopg.record_scan(
        app, store.ScanSubmission(scan=scan, candidates=tuple(candidates)))
    for index in range(len(event_keys)):
        slug = f'mkt-{suffix}-{index}'
        store_psycopg.record_market_outcome(app, store.MarketOutcomeObservation(
            market_slug=slug, outcome='YES', observed_at=at(120 + index),
            source_kind='gamma_close', source_ref=f'res-{slug}'))
    return written


@NATIVE
def test_unkeyed_rows_evaluate_bit_for_bit_like_the_market_key_behavior(capsys):
    from polymarket_alpha_lab.papersim import cli, evaluation, store_psycopg
    app_dsn = _fresh_managed_database()
    app = _app(app_dsn)
    written = _seed_three_entered_candidates(app, 'plain', (None, None, None))
    app.close()

    code = cli.main(['evaluate', '--dsn', app_dsn, '--pnl-basis', 'recomputed'])
    out = capsys.readouterr().out
    assert code == 0, out
    report = json.loads(out)
    assert report['queues'], 'the recorded scan must feed the evaluation queue'

    connection = store_psycopg.connect_explicit(app_dsn, label='PAPERSIM_EVALUATION_PG_DSN')
    try:
        loaded = cli._evaluation_candidates_from_store(connection)
    finally:
        connection.close()
    assert len(loaded) == 3
    # The frozen fallback: NULL keys load as match_key None, one market
    # identity each -- the pre-FX26 clustering, bit-for-bit.
    assert all(row.match_key is None for row in loaded)
    assert {row.market_key for row in loaded} == {'mkt-plain-0', 'mkt-plain-1',
                                                  'mkt-plain-2'}
    assert len(evaluation.cluster_rows(loaded)) == 3
    # The loaded queue equals the hand-built candidate queue wholesale.
    hand = tuple(
        evaluation.EvaluationCandidate(
            candidate_id=str(written['candidate_ids'][f'plain-{index}']),
            model_key='gbm:r19', market_type='terminal', gate_version='g1',
            market_key=f'mkt-plain-{index}', model_probability=0.2224,
            market_mid=0.15, accepted=True, stake=250.0, fill_price=0.56,
            side='YES', fee_category='crypto', fill_provenance='recorded_price',
            outcome='YES', natural_outcome='YES', recorded_pnl=None,
            decision_time=at(5 + index), settled_time=at(120 + index),
            close_price=0.9, match_key=None)
        for index in range(3))
    assert evaluation.evaluate_queues(loaded, pnl_basis='recomputed') == \
        evaluation.evaluate_queues(hand, pnl_basis='recomputed')


@NATIVE
def test_keyed_rows_evaluate_clustered_by_the_match_key(capsys):
    from polymarket_alpha_lab.papersim import cli, evaluation, store_psycopg
    app_dsn = _fresh_managed_database()
    app = _app(app_dsn)
    _seed_three_entered_candidates(app, 'keyed', ('gamma:77', 'gamma:77', 'gamma:77'))
    app.close()

    connection = store_psycopg.connect_explicit(app_dsn, label='PAPERSIM_EVALUATION_PG_DSN')
    try:
        loaded = cli._evaluation_candidates_from_store(connection)
    finally:
        connection.close()
    assert all(row.match_key == 'gamma:77' for row in loaded)
    # Three markets of ONE match ride one bootstrap cluster (FX-26).
    assert len(evaluation.cluster_rows(loaded)) == 1
    code = cli.main(['evaluate', '--dsn', app_dsn, '--pnl-basis', 'recomputed'])
    out = capsys.readouterr().out
    assert code == 0, out
    report = json.loads(out)
    assert report['queues']
