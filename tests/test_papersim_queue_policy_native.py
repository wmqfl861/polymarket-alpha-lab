"""Opt-in native proof of the C1 papersim-tables -> pause decision chain.

Runs only inside the disposable native cluster environment (the CI
throwaway papersim partition or an explicitly assembled local cluster);
never a business cluster. Seeds real ``papersim`` rows through the
single allowlisted adapter (``store_psycopg``), reads the metrics back
through the audited FX-11 store -> evaluation read path
(``cli._evaluation_candidates_from_store`` -> ``evaluate_queues``),
feeds them into ``queue_policy.queue_pause_input`` /
``evaluate_pause`` and proves the FX-17 decision on database-derived
numbers: a cross-league queue with 10 settled deep-negative trades
pauses for review, the identical domestic queue does not use the FX-17
branch, and an all-UNKNOWN (entered, nothing settled, no closes) queue
never pauses.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from pathlib import Path
import uuid

import pytest

from polymarket_alpha_lab.papersim import migrations as pm

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
APP_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_APP_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN or not APP_DSN,
                            reason='explicit native papersim proof is opt-in')


def adapter():
    """The single allowlisted papersim connector (lazy driver import)."""
    if not ENABLED:
        pytest.skip('the papersim connection adapter runs in the native lane only')
    return pytest.importorskip('polymarket_alpha_lab.papersim.store_psycopg')


def at(minutes: int) -> datetime:
    return datetime(2026, 11, 15, 12, 0, tzinfo=UTC) + timedelta(minutes=minutes)


@pytest.fixture(scope='module')
def managed_face():
    """Apply the papersim catalog once for this module's shared face."""
    store_psycopg = adapter()
    owner = store_psycopg.connect_explicit(
        OWNER_DSN, label='PAPERSIM_NATIVE_MANAGED_OWNER_DSN')
    catalog = pm.papersim_migration_catalog(ROOT)
    result = pm.apply_pending_migrations(owner, ROOT)
    assert result['applied'] in range(len(catalog) + 1)
    assert pm.migration_status(owner, ROOT)['pending'] == []
    owner.close()
    return OWNER_DSN, APP_DSN


def evidence(**overrides):
    values = dict(observed_at=at(5), evidence_role='DECISION',
                  model_probability=Decimal('0.44'),
                  market_ref_kind='market_slug', market_ref='fed-hike-jan',
                  token_ref=None, side='YES', bid=None, ask=None, mid=None,
                  last=None, book_sha256=None, snapshot_sha256=None,
                  source_kind='gamma_close', source_ref='r1')
    values.update(overrides)
    from polymarket_alpha_lab.papersim import store
    return store.PriceEvidenceObservation(**values)


def entry(**overrides):
    values = dict(stake=Decimal('250'), price=Decimal('0.56'),
                  shares=Decimal('446.4286'), fee_category='crypto',
                  fee_rate=Decimal('0.07'), entry_fee=Decimal('10.08'),
                  fee_source='schedule', tier='standard', entered_at=at(6),
                  legacy_source_id=None, is_legacy=False)
    values.update(overrides)
    from polymarket_alpha_lab.papersim import store
    return store.EntrySubmission(**values)


def candidate(**overrides):
    values = dict(candidate_key='m1-YES', conclusion='REJECTED', side='YES',
                  market_kind='terminal', team='crypto_eth', model_name='gbm',
                  model_version='r19', input_payload='{}', gate_report='{}',
                  evaluated_at=at(5), condition_id=None, market_id=None,
                  market_slug='fed-hike-jan', yes_token_id=None,
                  no_token_id=None, market_type='terminal', binding_version=1,
                  median_probability=Decimal('0.44'), variant_quotes='[]',
                  snapshot_refs='[]', rejection_reasons='["edge"]',
                  order_book=None, book_fetched_at=None,
                  legacy_source_id=None, is_legacy=False,
                  price_evidence=(), entry=None)
    values.update(overrides)
    from polymarket_alpha_lab.papersim import store
    return store.CandidateSubmission(**values)


def scan_run(finished=6):
    from polymarket_alpha_lab.papersim import store
    return store.ScanRunRecord(
        team='crypto_eth', started_at=at(0), finished_at=at(finished),
        code_commit='c' * 40, schema_version=1, gate_version='g1',
        operator='unattended',
        params='{"run": "%s"}' % uuid.uuid4().hex[:8])


def unique_slug() -> str:
    return 'fx17-' + uuid.uuid4().hex[:12]


def candidates_for_scans(conn, *scans):
    """Read all fixture candidates even when another native test used this face."""
    from polymarket_alpha_lab.papersim import cli
    candidate_ids = {str(candidate_id) for scan in scans
                     for candidate_id in scan['candidate_ids'].values()}
    rows = tuple(row for row in cli._evaluation_candidates_from_store(conn)
                 if row.candidate_id in candidate_ids)
    assert {row.candidate_id for row in rows} == candidate_ids
    return rows


@NATIVE
def test_store_metrics_to_pause_decision_chain(managed_face):
    store_psycopg = adapter()
    from polymarket_alpha_lab.papersim import evaluation as ev
    from polymarket_alpha_lab.papersim import queue_policy as qp
    from polymarket_alpha_lab.papersim import store

    app = store_psycopg.connect_explicit(
        APP_DSN, label='PAPERSIM_NATIVE_MANAGED_APP_DSN')
    owner = store_psycopg.connect_explicit(
        OWNER_DSN, label='PAPERSIM_NATIVE_MANAGED_OWNER_DSN')
    owner.autocommit = True

    # Queue A (gbm:r19): 10 accepted+entered trades, each settled NATURAL
    # loss with recorded gross -250 -- deep negative net for the queue.
    queue_a = []
    for index in range(10):
        slug = unique_slug()
        queue_a.append(candidate(
            candidate_key=f'fx17-a{index}', conclusion='ACCEPTED',
            market_slug=slug, model_version='r19',
            price_evidence=(evidence(market_ref=slug),), entry=entry()))
    result_a = store_psycopg.record_scan(
        app, store.ScanSubmission(scan=scan_run(), candidates=tuple(queue_a)))
    assert result_a['inserted'] is True
    with owner.cursor() as cursor:
        for index in range(10):
            cursor.execute('SELECT entry_id FROM papersim.entries '
                           'WHERE candidate_id = %s',
                           (result_a['candidate_ids'][f'fx17-a{index}'],))
            entry_id = cursor.fetchone()[0]
            cursor.execute(
                'INSERT INTO papersim.settlements (entry_id, classification, '
                'win_loss, proceeds, pnl_gross, fee_total, pnl_net, '
                'confirmed_by, confirmed_at) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
                (entry_id, 'NATURAL', 'loss', 0, -250, Decimal('10.08'),
                 Decimal('-260.08'), 'c1-native', at(80)))

    # Queue B (gbm:r20): 25 entered trades, nothing settled, no close
    # marks -- every pause metric is UNKNOWN on the read path.
    queue_b = []
    for index in range(25):
        slug = unique_slug()
        queue_b.append(candidate(
            candidate_key=f'fx17-b{index}', conclusion='ACCEPTED',
            market_slug=slug, model_version='r20',
            price_evidence=(evidence(market_ref=slug),), entry=entry()))
    result_b = store_psycopg.record_scan(
        app, store.ScanSubmission(scan=scan_run(), candidates=tuple(queue_b)))
    assert result_b['inserted'] is True

    # The audited store -> evaluation read path, then the C1 decision.
    rows = candidates_for_scans(app, result_a, result_b)
    report = ev.evaluate_queues(rows)
    by_model = {queue.queue.model_key: queue for queue in report.queues}
    assert 'gbm:r19' in by_model and 'gbm:r20' in by_model
    settled_queue = by_model['gbm:r19']
    unknown_queue = by_model['gbm:r20']
    assert (settled_queue.counts.entered,
            settled_queue.counts.settled) == (10, 10)
    # Schedule-recomputed fees: net = 10 * (-250 - entry_fee(250, .56, .07)).
    assert settled_queue.roi.net_pnl == pytest.approx(-2577.0, rel=1e-6)
    assert settled_queue.roi.net_pnl < -600
    assert (unknown_queue.counts.entered, unknown_queue.roi.counted) == (25, 0)

    decisions = qp.evaluate_pause([
        qp.queue_pause_input(settled_queue,
                             league_scope=qp.SCOPE_CROSS_LEAGUE,
                             data_source_version='native-dsv1'),
        qp.queue_pause_input(unknown_queue,
                             league_scope=qp.SCOPE_CROSS_LEAGUE,
                             data_source_version='native-dsv1'),
    ])
    cross, unknown = decisions
    assert cross.queue_id == 'CROSS_LEAGUE|gbm:r19|terminal|g1|native-dsv1'
    assert cross.action == qp.ACTION_PAUSE_REVIEW
    assert any('net pnl' in reason for reason in cross.review_reasons)
    assert cross.stop_reasons == ()
    # UNKNOWN metrics through the database chain never pause the queue.
    assert unknown.action == qp.ACTION_CONTINUE
    assert unknown.review_reasons == () and unknown.stop_reasons == ()
    assert unknown.pause_suggestion.fx17_pause_review is False
    # The decision reuses evaluation.pause_recommendation exactly.
    assert cross.pause_suggestion == ev.pause_recommendation(
        entered=10, clv_upper_bound=None,
        max_drawdown=settled_queue.drawdown.max_drawdown,
        settled=10, net_pnl=settled_queue.roi.net_pnl,
    )

    # The identical DOMESTIC queue must not use the FX-17 branch: the
    # FX-17 reasons are reported suppressed, the queue stays open.
    domestic = qp.evaluate_pause([qp.queue_pause_input(
        settled_queue, league_scope=qp.SCOPE_DOMESTIC,
        data_source_version='native-dsv1')])[0]
    assert domestic.action == qp.ACTION_CONTINUE
    assert domestic.review_reasons == ()
    assert domestic.fx17_suppressed_reasons == cross.review_reasons

    app.close()
    owner.close()


@NATIVE
def test_store_metrics_to_auto_stop_recommendation(managed_face):
    """40 entered, 40 settled deep-loss trades through the database
    chain: the universal rule recommends auto-stop for BOTH scopes (it
    is not an FX-17 branch and is not scope-gated)."""
    store_psycopg = adapter()
    from polymarket_alpha_lab.papersim import evaluation as ev
    from polymarket_alpha_lab.papersim import queue_policy as qp
    from polymarket_alpha_lab.papersim import store

    app = store_psycopg.connect_explicit(
        APP_DSN, label='PAPERSIM_NATIVE_MANAGED_APP_DSN')
    owner = store_psycopg.connect_explicit(
        OWNER_DSN, label='PAPERSIM_NATIVE_MANAGED_OWNER_DSN')
    owner.autocommit = True

    # 40 entered trades, every one a NATURAL loss (recorded gross -250):
    # the cumulative net path only declines, so max drawdown is the full
    # |net| of the queue regardless of the settlement ordering.
    rows = []
    for index in range(40):
        slug = unique_slug()
        rows.append(candidate(
            candidate_key=f'fx17-c{index}', conclusion='ACCEPTED',
            market_slug=slug, model_version='r31',
            price_evidence=(evidence(market_ref=slug),), entry=entry()))
    result = store_psycopg.record_scan(
        app, store.ScanSubmission(scan=scan_run(), candidates=tuple(rows)))
    with owner.cursor() as cursor:
        for index in range(40):
            cursor.execute('SELECT entry_id FROM papersim.entries '
                           'WHERE candidate_id = %s',
                           (result['candidate_ids'][f'fx17-c{index}'],))
            entry_id = cursor.fetchone()[0]
            cursor.execute(
                'INSERT INTO papersim.settlements (entry_id, classification, '
                'win_loss, proceeds, pnl_gross, fee_total, pnl_net, '
                'confirmed_by, confirmed_at) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
                (entry_id, 'NATURAL', 'loss', 0, -250, Decimal('10.08'),
                 Decimal('-260.08'), 'c1-native', at(80)))
    candidates = candidates_for_scans(app, result)
    report = ev.evaluate_queues(candidates)
    queue = {q.queue.model_key: q for q in report.queues}['gbm:r31']
    assert (queue.counts.entered, queue.counts.settled) == (40, 40)
    assert queue.drawdown.max_drawdown == pytest.approx(40 * 257.7, rel=1e-6)
    assert queue.drawdown.max_drawdown > 2500.0

    for scope in (qp.SCOPE_CROSS_LEAGUE, qp.SCOPE_DOMESTIC):
        decision = qp.evaluate_pause([qp.queue_pause_input(
            queue, league_scope=scope, data_source_version='native-dsv1')])[0]
        assert decision.action == qp.ACTION_AUTO_STOP_RECOMMEND
        assert any('drawdown' in reason for reason in decision.stop_reasons)

    app.close()
    owner.close()
