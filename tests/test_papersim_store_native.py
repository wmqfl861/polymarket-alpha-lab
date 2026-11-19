"""Opt-in native papersim store proofs on the assembled managed face.

Runs only inside the disposable native cluster environment (the CI
throwaway papersim partition or an explicitly assembled local cluster).
Never touches a business cluster: the environment DSNs point at the
one-shot assembled managed face (roles ``pal_owner``/``pal_app``,
database ``polymarket_alpha_lab``, V1 bootstrap applied). Covers design
section 8.2 items B7-B20 plus the D-CONN allowlist pin and the managed
DSN equivalence proof.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import md5, sha256
import importlib.util
import os
import sys
from pathlib import Path
import uuid

import pytest

from polymarket_alpha_lab.papersim import migrations as pm
from polymarket_alpha_lab.papersim import store

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
APP_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_APP_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN or not APP_DSN,
                            reason='explicit native papersim store proof is opt-in')


def adapter():
    """Import the connection adapter lazily.

    The offline suite guards a lazy-import invariant elsewhere in the repo
    (``psycopg`` must never linger in ``sys.modules`` after collection), so
    this file never imports the driver-backed adapter at module level.
    """
    if not ENABLED:
        pytest.skip('the papersim connection adapter runs in the native lane only')
    return pytest.importorskip('polymarket_alpha_lab.papersim.store_psycopg')


def at(minutes: int) -> datetime:
    return datetime(2026, 11, 15, 12, 0, tzinfo=UTC) + timedelta(minutes=minutes)


def connect(dsn, *, autocommit=False):
    psycopg = pytest.importorskip('psycopg')
    connection = psycopg.connect(dsn)
    connection.autocommit = autocommit
    return connection


@pytest.fixture(scope='module')
def managed_face():
    """Apply the papersim catalog once for this module's shared face."""
    owner = connect(OWNER_DSN)
    # Cluster-preparation wiring (M2 plan section 6.3): the applied count
    # follows the CURRENT catalog size (another module may have applied it
    # already on this shared throwaway face); the migration-history count
    # assertions live against the frozen M1 catalog fixture.
    catalog = pm.papersim_migration_catalog(ROOT)
    result = pm.apply_pending_migrations(owner, ROOT)
    assert result['applied'] in range(len(catalog) + 1)
    assert pm.migration_status(owner, ROOT)['pending'] == []
    owner.close()
    return OWNER_DSN, APP_DSN


def outcome(**overrides):
    # The design 4.4 idempotency key is condition_id|market_id|source_kind|
    # source_ref|observed_at (no slug): slug-only markets therefore keep a
    # per-market source reference, like a real close-event id.
    identity = (overrides.get('market_slug') or overrides.get('market_id')
                or overrides.get('condition_id') or 'fixed-market')
    values = dict(condition_id=None, market_id=None, market_slug=None, yes_token_id=None,
                  no_token_id=None, outcome='YES', outcome_detail=None, confirmed_at=at(60),
                  observed_at=at(30), source_kind='gamma_close',
                  source_ref='close-' + identity,
                  source_snapshot_sha256=None)
    values.update(overrides)
    return store.MarketOutcomeObservation(**values)


def evidence(**overrides):
    values = dict(observed_at=at(5), evidence_role='DECISION', model_probability=None,
                  market_ref_kind='market_slug', market_ref='fed-hike-jan', token_ref=None,
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
    values = dict(candidate_key='m1-YES', conclusion='REJECTED', side='YES', market_kind='terminal',
                  team='crypto_eth', model_name='gbm', model_version='r19', input_payload='{}',
                  gate_report='{}', evaluated_at=at(5), condition_id=None, market_id=None,
                  market_slug='fed-hike-jan', yes_token_id=None, no_token_id=None,
                  market_type='terminal', binding_version=1, median_probability=Decimal('0.44'),
                  variant_quotes='[]', snapshot_refs='[]', rejection_reasons='["edge"]',
                  order_book=None, book_fetched_at=None, legacy_source_id=None, is_legacy=False,
                  price_evidence=(), entry=None)
    values.update(overrides)
    return store.CandidateSubmission(**values)


def scan_run(finished=6):
    return store.ScanRunRecord(team='crypto_eth', started_at=at(0), finished_at=at(finished),
                               code_commit='c' * 40, schema_version=1, gate_version='g1',
                               operator='unattended', params='{"run": "%s"}' % uuid.uuid4().hex[:8])


def unique_slug() -> str:
    return 'market-' + uuid.uuid4().hex[:10]


# ---------------------------------------------------------------------------
# D-CONN allowlist pin and managed DSN equivalence (8.2 items 13 and 16)
# ---------------------------------------------------------------------------

def test_frozen_connector_allowlist_contains_exactly_the_papersim_adapter():
    from tests.test_database_persistence_iron_rule import FROZEN_CONNECTOR_ALLOWLIST
    assert 'src/polymarket_alpha_lab/papersim/store_psycopg.py' in FROZEN_CONNECTOR_ALLOWLIST
    assert 'src/polymarket_alpha_lab/papersim/store.py' not in FROZEN_CONNECTOR_ALLOWLIST
    assert 'src/polymarket_alpha_lab/papersim/migrations.py' not in FROZEN_CONNECTOR_ALLOWLIST


@NATIVE
def test_managed_dsn_is_byte_equal_to_the_v1_construction():
    store_psycopg = adapter()
    from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
    info = {'port': 55433}
    assert store_psycopg.managed_dsn(info, owner=False, repo_root=ROOT) == \
        ProjectPostgres(ROOT)._dsn(info, owner=False)
    assert store_psycopg.managed_dsn(info, owner=True, repo_root=ROOT) == \
        ProjectPostgres(ROOT)._dsn(info, owner=True)


@NATIVE
def test_binding_verify_accepts_the_instance_triple_and_rejects_drift(managed_face):
    _, app_dsn = managed_face
    owner = connect(OWNER_DSN)
    with owner.cursor() as cursor:
        cursor.execute('SELECT instance_id, root_sha256, system_identifier '
                       'FROM project_private.instance WHERE singleton IS TRUE')
        triple = cursor.fetchone()
    owner.close()
    application = connect(app_dsn)
    from polymarket_alpha_lab.project_postgres import binding
    from polymarket_alpha_lab.project_postgres.files import ProjectDatabaseError
    with binding.bound(*triple):
        binding.verify_connection(application.cursor())
    with pytest.raises(ProjectDatabaseError, match='project_postgres_connection_identity_mismatch'):
        with binding.bound('0' * 32, triple[1], triple[2]):
            binding.verify_connection(application.cursor())
    application.close()


# ---------------------------------------------------------------------------
# Scan transaction (8.2 items 7, 8, 10, 11)
# ---------------------------------------------------------------------------

@NATIVE
def test_full_scan_write_read_and_idempotent_rerecord(managed_face):
    store_psycopg = adapter()
    _, app_dsn = managed_face
    app = connect(app_dsn)
    slug = unique_slug()
    accepted = candidate(candidate_key='k-accepted', conclusion='ACCEPTED', market_slug=slug,
                         price_evidence=(evidence(market_ref=slug),), entry=entry())
    rejected = candidate(candidate_key='k-rejected', market_slug=unique_slug())
    submission = store.ScanSubmission(scan=scan_run(), candidates=(accepted, rejected))
    first = store_psycopg.record_scan(app, submission)
    assert first['inserted'] is True and len(first['candidate_ids']) == 2
    assert len(first['bankroll_event_keys']) == 1
    with app.cursor() as cursor:
        cursor.execute('SELECT team, operator, code_commit FROM papersim.scan_runs '
                       'WHERE scan_id = %s', (first['scan_id'],))
        assert cursor.fetchone() == ('crypto_eth', 'unattended', 'c' * 40)
        cursor.execute('SELECT candidate_key, conclusion FROM papersim.candidates '
                       'WHERE scan_id = %s ORDER BY candidate_id', (first['scan_id'],))
        assert cursor.fetchall() == [('k-accepted', 'ACCEPTED'), ('k-rejected', 'REJECTED')]
        cursor.execute('SELECT count(*) FROM papersim.candidate_price_evidence c '
                       'JOIN papersim.candidates k ON k.candidate_id = c.candidate_id '
                       'WHERE k.scan_id = %s', (first['scan_id'],))
        assert cursor.fetchone()[0] == 1
        cursor.execute('SELECT stake, price, fee_source FROM papersim.entries e '
                       'JOIN papersim.candidates k ON k.candidate_id = e.candidate_id '
                       'WHERE k.scan_id = %s', (first['scan_id'],))
        assert cursor.fetchall() == [(Decimal('250'), Decimal('0.56'), 'schedule')]
        cursor.execute('SELECT event_key, amount, reason FROM papersim.bankroll_events '
                       'WHERE scan_id = %s', (first['scan_id'],))
        cash = cursor.fetchall()
        assert len(cash) == 1 and cash[0][1] == Decimal('-260.08') and cash[0][2] == 'SCAN_ENTRY'
    again = store_psycopg.record_scan(app, submission)
    assert again['inserted'] is False and again['scan_id'] == first['scan_id']
    assert again['candidate_ids'] == first['candidate_ids']
    drifted = store.ScanSubmission(scan=submission.scan, candidates=(
        accepted, candidate(candidate_key='k-rejected', market_slug=rejected.market_slug,
                            rejection_reasons='["changed"]')))
    with pytest.raises(store.PapersimStoreError, match='papersim_scan_payload_conflict'):
        store_psycopg.record_scan(app, drifted)
    with app.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.scan_runs WHERE scan_key = %s',
                       (submission.scan.key(),))
        assert cursor.fetchone()[0] == 1
    app.close()


@NATIVE
def test_all_rejected_zero_entry_scan_joins_outcomes_and_prices(managed_face):
    store_psycopg = adapter()
    _, app_dsn = managed_face
    app = connect(app_dsn)
    resolved = unique_slug()
    token_mismatch = unique_slug()
    missing = unique_slug()
    store_psycopg.record_market_outcome(app, outcome(market_slug=resolved, outcome='YES',
                                                     yes_token_id='tok-a', no_token_id='tok-b'))
    store_psycopg.record_market_outcome(app, outcome(market_slug=token_mismatch, outcome='NO',
                                                     yes_token_id='tok-x', no_token_id='tok-y'))
    rows = (candidate(candidate_key='r1', market_slug=resolved, yes_token_id='tok-a',
                      no_token_id='tok-b',
                      price_evidence=(evidence(market_ref=resolved, mid=Decimal('0.62')),)),
            candidate(candidate_key='r2', market_slug=missing,
                      price_evidence=(evidence(market_ref=missing, mid=Decimal('0.31')),)),
            candidate(candidate_key='r3', market_slug=token_mismatch, yes_token_id='tok-q',
                      no_token_id='tok-r',
                      price_evidence=(evidence(market_ref=token_mismatch, mid=Decimal('0.5')),)))
    result = store_psycopg.record_scan(app, store.ScanSubmission(scan=scan_run(), candidates=rows))
    with app.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.entries e JOIN papersim.candidates k '
                       'ON k.candidate_id = e.candidate_id WHERE k.scan_id = %s',
                       (result['scan_id'],))
        assert cursor.fetchone()[0] == 0  # zero-entry scan still scores below
    effective = {slug: store_psycopg.effective_market_outcome(app, market_slug=slug)
                 for slug in (resolved, missing, token_mismatch)}
    assert (effective[resolved].outcome, effective[resolved].is_effective) == ('YES', True)
    assert (effective[missing].outcome, effective[missing].outcome_id) == ('UNKNOWN', None)
    assert effective[token_mismatch].outcome == 'NO'
    for key in ('r1', 'r2', 'r3'):
        decision = store_psycopg.decision_time_evidence(app, result['candidate_ids'][key])
        assert store.decision_price_status(decision) == 'AVAILABLE'
    # Wrong token family on a matched market is a binding mismatch, not a sample.
    assert store.binding_token_status('tok-q', 'tok-r', 'tok-x', 'tok-y') is False
    assert store.natural_alignment('YES', effective[resolved].outcome) is True
    assert store.natural_alignment('YES', effective[token_mismatch].outcome) is False
    app.close()


@NATIVE
def test_correction_row_wins_tie_break_and_unknown_never_shadows(managed_face):
    store_psycopg = adapter()
    _, app_dsn = managed_face
    app = connect(app_dsn)
    slug = unique_slug()
    store_psycopg.record_market_outcome(app, outcome(market_slug=slug, outcome='YES',
                                                     observed_at=at(30)))
    correction = store_psycopg.record_market_outcome(
        app, outcome(market_slug=slug, outcome='NO', observed_at=at(90), confirmed_at=at(95)))
    effective = store_psycopg.effective_market_outcome(app, market_slug=slug)
    assert (effective.outcome_id, effective.outcome) == (correction['outcome_id'], 'NO')
    tie = unique_slug()
    store_psycopg.record_market_outcome(app, outcome(market_slug=tie, outcome='YES',
                                                     observed_at=at(30)))
    later_id = store_psycopg.record_market_outcome(
        app, outcome(market_slug=tie, outcome='NO', observed_at=at(30),
                     source_ref='close-2-' + tie))['outcome_id']
    tied = store_psycopg.effective_market_outcome(app, market_slug=tie)
    assert (tied.outcome_id, tied.outcome) == (later_id, 'NO')
    shadowed = unique_slug()
    settled = store_psycopg.record_market_outcome(app, outcome(market_slug=shadowed, outcome='YES',
                                                               observed_at=at(30)))
    store_psycopg.record_market_outcome(app, outcome(market_slug=shadowed, outcome='UNKNOWN',
                                                     confirmed_at=None, observed_at=at(200)))
    still = store_psycopg.effective_market_outcome(app, market_slug=shadowed)
    assert (still.outcome_id, still.outcome) == (settled['outcome_id'], 'YES')
    app.close()


@NATIVE
def test_market_outcome_idempotency_and_payload_conflict(managed_face):
    store_psycopg = adapter()
    _, app_dsn = managed_face
    app = connect(app_dsn)
    slug = unique_slug()
    observation = outcome(market_slug=slug, observed_at=at(30))
    first = store_psycopg.record_market_outcome(app, observation)
    repeat = store_psycopg.record_market_outcome(app, observation)
    assert repeat == {'outcome_id': first['outcome_id'], 'inserted': False}
    conflicting = outcome(market_slug=slug, observed_at=at(30), outcome='NO')
    with pytest.raises(store.PapersimStoreError, match='papersim_outcome_evidence_conflict'):
        store_psycopg.record_market_outcome(app, conflicting)
    with app.cursor() as cursor:
        cursor.execute('SELECT outcome FROM papersim.market_outcomes WHERE evidence_key = %s',
                       (observation.evidence_key(),))
        assert cursor.fetchone()[0] == 'YES'
    app.close()


@NATIVE
def test_time_order_violation_rejects_the_whole_scan(managed_face):
    store_psycopg = adapter()
    _, app_dsn = managed_face
    app = connect(app_dsn)
    slug = unique_slug()
    rows = (candidate(candidate_key='bad-order', market_slug=slug, price_evidence=(
        evidence(observed_at=at(3), evidence_role='CLOSE', source_kind='clob_terminal_price',
                 source_ref='t', last=Decimal('1')),
        evidence(observed_at=at(4), evidence_role='DECISION', market_ref=slug),),),)
    submission = store.ScanSubmission(scan=scan_run(), candidates=rows)
    with pytest.raises(store.PapersimStoreError, match='papersim_evidence_time_order_invalid'):
        store_psycopg.record_scan(app, submission)
    with app.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.scan_runs WHERE scan_key = %s',
                       (submission.scan.key(),))
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT count(*) FROM papersim.candidates WHERE candidate_key = 'bad-order'")
        assert cursor.fetchone()[0] == 0
    app.close()


@NATIVE
def test_backend_termination_mid_transaction_leaves_no_half_scan(managed_face):
    psycopg = pytest.importorskip('psycopg')
    _, app_dsn = managed_face
    key = 'terminated-' + uuid.uuid4().hex[:10]
    connection = psycopg.connect(app_dsn)
    try:
        with connection.transaction():
            cursor = connection.cursor()
            cursor.execute(
                'INSERT INTO papersim.scan_runs (scan_key, scan_payload_sha256, team, started_at, '
                'finished_at, code_commit, schema_version, gate_version, operator, params) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) RETURNING scan_id',
                (key, 'd' * 64, 'crypto_eth', at(0), at(6), 'c' * 40, 1, 'g1', 'unattended', '{}'))
            cursor.fetchone()
            cursor.execute('SELECT pg_terminate_backend(pg_backend_pid())')
            cursor.fetchone()
    except psycopg.OperationalError:
        pass
    connection.close()
    app = connect(app_dsn)
    with app.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.scan_runs WHERE scan_key = %s', (key,))
        assert cursor.fetchone()[0] == 0
    app.close()


# ---------------------------------------------------------------------------
# Guard and constraint negatives (8.2 items 12, 15, 19, 20)
# ---------------------------------------------------------------------------

PAPERSIM_TABLES = ('scan_runs', 'snapshots', 'candidates', 'candidate_price_evidence',
                   'market_outcomes', 'entries', 'marks', 'settlements', 'bankroll_events')


def _seed_every_table(owner, app):
    """One row in every guarded table so row triggers have something to hit."""
    store_psycopg = adapter()
    slug = unique_slug()
    result = store_psycopg.record_scan(app, store.ScanSubmission(
        scan=scan_run(),
        candidates=(candidate(candidate_key='seed', conclusion='ACCEPTED', market_slug=slug,
                              price_evidence=(evidence(market_ref=slug),), entry=entry()),)))
    outcome_id = store_psycopg.record_market_outcome(app, outcome(market_slug=slug))['outcome_id']
    candidate_id = result['candidate_ids']['seed']
    with owner.cursor() as cursor:
        cursor.execute('SELECT entry_id FROM papersim.entries WHERE candidate_id = %s',
                       (candidate_id,))
        entry_id = cursor.fetchone()[0]
        content = ('seed-snapshot-' + uuid.uuid4().hex).encode()
        cursor.execute('INSERT INTO papersim.snapshots (content_sha256, content, byte_length, '
                       'kind, source, fetched_at, schema_version) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                       (sha256(content).hexdigest(), content, len(content), 'book',
                        'seed', at(1), 1))
        cursor.execute('INSERT INTO papersim.marks (entry_id, mark_key, marked_at, mid, is_close, '
                       'source_kind) VALUES (%s,%s,%s,%s,%s,%s)',
                       (entry_id, 'seed:' + uuid.uuid4().hex[:8], at(5), Decimal('0.55'), False,
                        'clob_terminal_price'))
        cursor.execute('SELECT mark_id FROM papersim.marks WHERE entry_id = %s', (entry_id,))
        mark_id = cursor.fetchone()[0]
        cursor.execute('INSERT INTO papersim.settlements (entry_id, classification, outcome_label, '
                       'win_loss, proceeds, pnl_gross, fee_total, pnl_net, source_outcome_id, '
                       'exit_mark_id, confirmed_by, confirmed_at) '
                       'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                       (entry_id, 'EXIT', None, None, Decimal('239.29'), Decimal('-10.71'),
                        Decimal('0'), Decimal('-10.71'), outcome_id, mark_id, 'seed', at(70)))
        cursor.execute('INSERT INTO papersim_private.observed_baseline (observed_at, '
                       'relation_kind, relation_name, source_system_identifier, source_cluster, '
                       'registered_by, registered_run) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                       (at(30), 'table', 'decisions', '7695033081798129054',
                        'legacy_paper_sim_loopback', 'seed', 'seed-run'))
    return entry_id, candidate_id


@NATIVE
def test_update_delete_truncate_rejected_on_every_guarded_table(managed_face):
    owner = connect(OWNER_DSN, autocommit=True)
    app = connect(APP_DSN)
    _seed_every_table(owner, app)
    psycopg = pytest.importorskip('psycopg')
    targets = [('papersim.' + name, 'paper_only') for name in PAPERSIM_TABLES]
    targets += [('papersim_private.observed_baseline', 'paper_only'),
                ('papersim_private.migrations', 'sha256')]
    for relation, column in targets:
        # TRUNCATE needs CASCADE on FK-referenced tables so the statement
        # reaches the BEFORE TRUNCATE triggers instead of being refused by
        # the planner's foreign-key shortcut; the trigger then rejects the
        # mutation on every cascaded table and nothing is truncated.
        for statement in (f'UPDATE {relation} SET {column} = {column}',
                          f'DELETE FROM {relation}',
                          f'TRUNCATE {relation} CASCADE'):
            with pytest.raises(psycopg.errors.DatabaseError, match='papersim_mutation_rejected'):
                owner.execute(statement)
    owner.close()
    app.close()


@NATIVE
def test_duplicate_entry_and_settlement_are_rejected(managed_face):
    psycopg = pytest.importorskip('psycopg')
    owner = connect(OWNER_DSN, autocommit=True)
    app = connect(APP_DSN)
    entry_id, candidate_id = _seed_every_table(owner, app)
    with owner.cursor() as cursor:
        cursor.execute('SELECT stake, price, shares, fee_category, fee_rate, entry_fee, '
                       'fee_source, tier, entered_at FROM papersim.entries WHERE entry_id = %s',
                       (entry_id,))
        stake, price, shares, category, rate, fee, source, tier, entered_at = cursor.fetchone()
        with pytest.raises(psycopg.errors.UniqueViolation):
            cursor.execute('INSERT INTO papersim.entries (candidate_id, stake, price, shares, '
                           'fee_category, fee_rate, entry_fee, fee_source, tier, entered_at) '
                           'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                           (candidate_id, stake, price, shares, category, rate, fee, source,
                            tier, entered_at))
        with pytest.raises(psycopg.errors.UniqueViolation):
            cursor.execute('INSERT INTO papersim.settlements (entry_id, classification, proceeds, '
                           'pnl_gross, pnl_net, confirmed_by, confirmed_at) '
                           'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                           (entry_id, 'EXIT', 0, 0, 0, 'dup', at(80)))
    owner.close()
    app.close()


@NATIVE
def test_legacy_gating_and_settlement_classification_negatives(managed_face):
    psycopg = pytest.importorskip('psycopg')
    owner = connect(OWNER_DSN, autocommit=True)
    app = connect(APP_DSN)
    entry_id, candidate_id = _seed_every_table(owner, app)
    with owner.cursor() as cursor:
        with pytest.raises(psycopg.errors.CheckViolation):  # P3-7 on candidates
            cursor.execute('INSERT INTO papersim.candidates (scan_id, candidate_key, side, '
                           'market_kind, team, model_name, model_version, input_payload, '
                           'gate_report, evaluated_at, conclusion, legacy_source_id) '
                           'VALUES ((SELECT scan_id FROM papersim.candidates WHERE candidate_id=%s),'
                           '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                           (candidate_id, 'legacy-' + uuid.uuid4().hex[:6], 'YES', 'terminal',
                            't', 'm', 'v', '{}', '{}', at(5), 'REJECTED', '17'))
        with pytest.raises(psycopg.errors.CheckViolation):  # P3-7 on entries
            cursor.execute('INSERT INTO papersim.entries (candidate_id, stake, price, shares, '
                           'fee_category, fee_rate, entry_fee, tier, entered_at, legacy_source_id) '
                           'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                           (candidate_id, 10, 0.5, 20, 'crypto', 0.07, 0, 't', at(6), '18'))
        with pytest.raises(psycopg.errors.CheckViolation):  # NATURAL without win/loss
            cursor.execute('INSERT INTO papersim.settlements (entry_id, classification, win_loss, '
                           'proceeds, pnl_gross, pnl_net, confirmed_by, confirmed_at) '
                           'VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                           (entry_id, 'NATURAL', None, 0, 0, 0, 'x', at(80)))
        with pytest.raises(psycopg.errors.CheckViolation):  # EXIT with win/loss
            cursor.execute('INSERT INTO papersim.settlements (entry_id, classification, win_loss, '
                           'proceeds, pnl_gross, pnl_net, confirmed_by, confirmed_at) '
                           'VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                           (entry_id, 'EXIT', 'win', 0, 0, 0, 'x', at(81)))
    owner.close()
    app.close()


@NATIVE
def test_numeric_boundary_negatives_accept_and_reject_correctly(managed_face):
    psycopg = pytest.importorskip('psycopg')
    owner = connect(OWNER_DSN, autocommit=True)
    app = connect(APP_DSN)
    entry_id, candidate_id = _seed_every_table(owner, app)
    rejects = (
        # entries: positive stake, open price interval, finite numbers only
        f'INSERT INTO papersim.entries (candidate_id, stake, price, shares, fee_category, '
        f'fee_rate, entry_fee, tier, entered_at) VALUES ({candidate_id}, 0, 0.5, 2, \'c\', 0.07, '
        f'0, \'t\', \'{at(6).isoformat()}\')',
        f'INSERT INTO papersim.entries (candidate_id, stake, price, shares, fee_category, '
        f'fee_rate, entry_fee, tier, entered_at) VALUES ({candidate_id}, 10, 1, 20, \'c\', 0.07, '
        f'0, \'t\', \'{at(6).isoformat()}\')',
        f'INSERT INTO papersim.entries (candidate_id, stake, price, shares, fee_category, '
        f'fee_rate, entry_fee, tier, entered_at) VALUES ({candidate_id}, 10, 0, 20, \'c\', 0.07, '
        f'0, \'t\', \'{at(6).isoformat()}\')',
        f'INSERT INTO papersim.entries (candidate_id, stake, price, shares, fee_category, '
        f'fee_rate, entry_fee, tier, entered_at) VALUES ({candidate_id}, \'NaN\'::numeric, 0.5, '
        f'20, \'c\', 0.07, 0, \'t\', \'{at(6).isoformat()}\')',
        f'INSERT INTO papersim.entries (candidate_id, stake, price, shares, fee_category, '
        f'fee_rate, entry_fee, tier, entered_at) VALUES ({candidate_id}, \'Infinity\'::numeric, '
        f'0.5, 20, \'c\', 0.07, 0, \'t\', \'{at(6).isoformat()}\')',
        # the three-true boundary columns are structural
        f'INSERT INTO papersim.entries (candidate_id, stake, price, shares, fee_category, '
        f'fee_rate, entry_fee, tier, entered_at, paper_only) VALUES ({candidate_id}, 10, 0.5, 20, '
        f'\'c\', 0.07, 0, \'t\', \'{at(6).isoformat()}\', false)',
        # quotes stay open (0,1); a 0/1 bid is not a fillable quote
        f'INSERT INTO papersim.candidate_price_evidence (evidence_key, candidate_id, '
        f'evidence_role, observed_at, market_ref_kind, market_ref, side, bid, source_kind) '
        f'VALUES (\'ev-{uuid.uuid4().hex[:8]}\', {candidate_id}, \'DECISION\', '
        f'\'{at(5).isoformat()}\', \'market_id\', \'m\', \'YES\', 0, \'x\')',
        f'INSERT INTO papersim.candidate_price_evidence (evidence_key, candidate_id, '
        f'evidence_role, observed_at, market_ref_kind, market_ref, side, last, source_kind) '
        f'VALUES (\'ev-{uuid.uuid4().hex[:8]}\', {candidate_id}, \'DECISION\', '
        f'\'{at(5).isoformat()}\', \'market_id\', \'m\', \'YES\', 1, \'x\')',
    )
    for statement in rejects:
        with pytest.raises(psycopg.errors.CheckViolation):
            owner.execute(statement)
    accepts = (
        # marks use the closed interval [0,1]
        f'INSERT INTO papersim.marks (entry_id, mark_key, marked_at, bid, ask, source_kind) '
        f'VALUES ({entry_id}, \'mk-{uuid.uuid4().hex[:6]}\', \'{at(5).isoformat()}\', 0, 1, '
        f'\'clob_terminal_price\')',
        # CLOSE last prices use the closed interval [0,1]
        f'INSERT INTO papersim.candidate_price_evidence (evidence_key, candidate_id, '
        f'evidence_role, observed_at, market_ref_kind, market_ref, side, last, source_kind) '
        f'VALUES (\'ev-{uuid.uuid4().hex[:8]}\', {candidate_id}, \'CLOSE\', '
        f'\'{at(5).isoformat()}\', \'market_id\', \'m\', \'YES\', 1, \'x\')',
        f'INSERT INTO papersim.candidate_price_evidence (evidence_key, candidate_id, '
        f'evidence_role, observed_at, market_ref_kind, market_ref, side, last, source_kind) '
        f'VALUES (\'ev-{uuid.uuid4().hex[:8]}\', {candidate_id}, \'CLOSE\', '
        f'\'{at(5).isoformat()}\', \'market_id\', \'m\', \'YES\', 0, \'x\')',
    )
    for statement in accepts:
        owner.execute(statement)
    owner.close()
    app.close()


@NATIVE
def test_snapshot_references_must_be_registered_before_the_scan(managed_face):
    store_psycopg = adapter()
    _, app_dsn = managed_face
    app = connect(app_dsn)
    content = b'referenced-by-candidate'
    reference = sha256(content).hexdigest()
    dangling = candidate(candidate_key='dangling-ref',
                         snapshot_refs='["' + reference + '"]')
    submission = store.ScanSubmission(scan=scan_run(), candidates=(dangling,))
    with pytest.raises(store.PapersimStoreError, match='papersim_snapshot_ref_missing'):
        store_psycopg.record_scan(app, submission)
    with app.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.scan_runs WHERE scan_key = %s',
                       (submission.scan.key(),))
        assert cursor.fetchone()[0] == 0
    owner = connect(OWNER_DSN, autocommit=True)
    with owner.cursor() as cursor:
        cursor.execute('INSERT INTO papersim.snapshots (content_sha256, content, byte_length, '
                       'kind, source, fetched_at, schema_version) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                       (reference, content, len(content), 'book', 'seed', at(1), 1))
    owner.close()
    result = store_psycopg.record_scan(app, submission)
    assert result['inserted'] is True
    app.close()


@NATIVE
def test_single_direction_fk_and_min_evidence_id_decision_rule(managed_face):
    store_psycopg = adapter()
    psycopg = pytest.importorskip('psycopg')
    _, app_dsn = managed_face
    app = connect(app_dsn)
    slug = unique_slug()
    rows = (candidate(candidate_key='two-decisions', market_slug=slug, price_evidence=(
        evidence(observed_at=at(4), market_ref=slug, mid=Decimal('0.50')),
        evidence(observed_at=at(5), market_ref=slug, mid=Decimal('0.70')))),)
    result = store_psycopg.record_scan(app, store.ScanSubmission(scan=scan_run(), candidates=rows))
    candidate_id = result['candidate_ids']['two-decisions']
    first = store_psycopg.decision_time_evidence(app, candidate_id)
    assert first['mid'] == Decimal('0.50')  # smallest evidence_id, not the appended row
    owner = connect(OWNER_DSN, autocommit=True)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        owner.execute('INSERT INTO papersim.candidate_price_evidence (evidence_key, candidate_id, '
                      'evidence_role, observed_at, market_ref_kind, market_ref, side, '
                      'source_kind) VALUES (%s, 999999999, \'DECISION\', %s, \'market_id\', '
                      '\'m\', \'YES\', \'x\')',
                      ('ev-dangling-' + uuid.uuid4().hex[:6], at(5)))
    owner.close()
    app.close()


# ---------------------------------------------------------------------------
# FX-09L: legacy import DB proofs and settlement cash-ledger proofs
# (design 8.3 items 3-10; the offline dry-run contracts live in
# tests/test_papersim_import_legacy.py)
# ---------------------------------------------------------------------------

LEGACY_DSN = os.environ.get('PAPERSIM_PG_DSN', '')
LEGACY_FACE = pytest.mark.skipif(
    not ENABLED or not LEGACY_DSN,
    reason='the legacy-face proofs need the fixture-loaded throwaway '
           'paper_sim cluster (PAPERSIM_PG_DSN)')
SNAPSHOT_DIR = ROOT / 'docs' / 'paper-sim' / 'ledger-snapshots' / '2026-11-12'
IMPORT_TOOL = ROOT / 'tools' / 'papersim_import_legacy.py'


@pytest.fixture(scope='module')
def importer():
    spec = importlib.util.spec_from_file_location(
        'papersim_import_legacy_native', IMPORT_TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass annotation resolution
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def imported_plan(importer):
    return importer.build_import_plan(importer.load_snapshot(SNAPSHOT_DIR))


@NATIVE
def test_legacy_import_executes_idempotently_and_conserves(
        managed_face, importer, imported_plan):
    _, app_dsn = managed_face
    # Target through the adapter's validated explicit-DSN door.
    store_psycopg = adapter()
    target = store_psycopg.connect_explicit(
        app_dsn, label='PAPERSIM_MIGRATIONS_PG_DSN')
    try:
        first = importer.execute_import(target, imported_plan)
    finally:
        target.close()
    assert first['inserted'] == {'scan_runs': 1, 'candidates': 71, 'entries': 71,
                                 'cash_events': 67, 'market_outcomes': 60,
                                 'settlements': 66}
    owner = connect(OWNER_DSN, autocommit=True)
    with owner.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.candidates '
                       'WHERE is_legacy AND legacy_source_id IS NOT NULL')
        assert cursor.fetchone()[0] == 71
        cursor.execute('SELECT count(*) FROM papersim.entries WHERE is_legacy')
        assert cursor.fetchone()[0] == 71
        cursor.execute('SELECT count(*) FROM papersim.market_outcomes '
                       "WHERE source_kind = 'legacy_import'")
        assert cursor.fetchone()[0] == 60
        cursor.execute('SELECT count(*) FROM papersim.settlements WHERE is_legacy')
        assert cursor.fetchone()[0] == 66
        # 8.3-4: per-cent conservation against the legacy resolutions.
        cursor.execute('SELECT sum(pnl_gross) FROM papersim.settlements '
                       'WHERE is_legacy')  # the 09S seeds are not legacy rows
        assert cursor.fetchone()[0] == Decimal('3768.33')
        cursor.execute('SELECT e.legacy_source_id, s.pnl_gross, s.fee_total, '
                       's.pnl_net FROM papersim.settlements s '
                       'JOIN papersim.entries e ON e.entry_id = s.entry_id '
                       "WHERE e.legacy_source_id IN ('1','14') "
                       'ORDER BY e.legacy_source_id')
        rows = cursor.fetchall()
        assert [(row[1], row[2], row[3]) for row in rows] == [
            (Decimal('706.11'), Decimal('25.46'), Decimal('680.65')),
            (Decimal('599.09'), Decimal('19.08'), Decimal('580.01'))]
        # 8.3-6: label semantics survive the round trip through SQL.
        cursor.execute('SELECT o.outcome, o.outcome_detail FROM papersim.market_outcomes o '
                       'JOIN papersim.settlements s ON s.source_outcome_id = o.outcome_id '
                       "WHERE s.outcome_label IN ('win','loss') LIMIT 1")
        unknown_label = cursor.fetchone()
        assert unknown_label[0] == 'UNKNOWN'
        assert unknown_label[1] in ('recorded_label=win', 'recorded_label=loss')
        cursor.execute('SELECT count(*) FROM papersim.settlements s '
                       'LEFT JOIN papersim.market_outcomes o '
                       'ON o.outcome_id = s.source_outcome_id '
                       'WHERE s.classification = \'EXIT\' AND s.exit_price IS NOT NULL '
                       'AND o.outcome_id IS NULL')
        assert cursor.fetchone()[0] == 5  # exits never mint market rows
        cursor.execute('SELECT count(*) FROM papersim.settlements s '
                       'JOIN papersim.entries e ON e.entry_id = s.entry_id '
                       "WHERE s.classification = 'VOIDED_REFUND' "
                       'AND s.proceeds = e.stake AND s.pnl_gross = 0 '
                       'AND s.fee_total = 0')
        assert cursor.fetchone()[0] == 4
        # The single INITIAL anchor plus per-settle events exist.
        cursor.execute('SELECT count(*) FROM papersim.bankroll_events '
                       "WHERE event_key = 'legacy:bankroll:initial'")
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT count(*) FROM papersim.bankroll_events "
                       "WHERE event_key LIKE 'legacy:settle:%'")
        assert cursor.fetchone()[0] == 66
        # Append-only holds for importer-written rows, not just seeds.
        psycopg = pytest.importorskip('psycopg')
        with pytest.raises(psycopg.errors.DatabaseError,
                           match='papersim_mutation_rejected'):
            owner.execute('UPDATE papersim.settlements SET pnl_net = pnl_net')
    owner.close()
    # Idempotent re-run: nothing new, same mapping (design 7.5).
    target = store_psycopg.connect_explicit(
        app_dsn, label='PAPERSIM_MIGRATIONS_PG_DSN')
    try:
        second = importer.execute_import(target, imported_plan)
    finally:
        target.close()
    assert second['inserted'] == {'scan_runs': 0, 'candidates': 0, 'entries': 0,
                                  'cash_events': 0, 'market_outcomes': 0,
                                  'settlements': 0}
    assert second['scan_id'] == first['scan_id']
    assert second['entry_ids'] == first['entry_ids']
    owner = connect(OWNER_DSN, autocommit=True)
    with owner.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.settlements WHERE is_legacy')
        assert cursor.fetchone()[0] == 66
    owner.close()


@NATIVE
def test_importer_without_authorization_writes_nothing_on_a_writable_face(
        managed_face, importer, imported_plan, capsys):
    _, app_dsn = managed_face
    owner = connect(OWNER_DSN, autocommit=True)
    with owner.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.scan_runs WHERE scan_key = %s',
                       (imported_plan.scan_key,))
        before = cursor.fetchone()[0]
    owner.close()
    # The gate refuses BEFORE any connection: a dead port proves it.
    code = importer.main(['--dsn', 'postgresql://paper_sim@127.0.0.1:1/paper_sim'])
    assert code == 2
    assert 'production insertion is not authorized' in capsys.readouterr().err
    owner = connect(OWNER_DSN, autocommit=True)
    with owner.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim.scan_runs WHERE scan_key = %s',
                       (imported_plan.scan_key,))
        assert cursor.fetchone()[0] == before
    owner.close()


@NATIVE
def test_settlement_net_identity_check_rejects_drift(managed_face):
    psycopg = pytest.importorskip('psycopg')
    owner = connect(OWNER_DSN, autocommit=True)
    app = connect(APP_DSN)
    store_psycopg = adapter()
    slug = unique_slug()
    result = store_psycopg.record_scan(app, store.ScanSubmission(
        scan=scan_run(),
        candidates=(candidate(candidate_key='net-check', conclusion='ACCEPTED',
                              market_slug=slug,
                              price_evidence=(evidence(market_ref=slug),),
                              entry=entry()),)))
    with owner.cursor() as cursor:
        cursor.execute('SELECT entry_id FROM papersim.entries WHERE candidate_id = %s',
                       (result['candidate_ids']['net-check'],))
        entry_id = cursor.fetchone()[0]
        # 8.3-5 CHECK proof: pnl_net must equal pnl_gross - fee_total.
        with pytest.raises(psycopg.errors.CheckViolation):
            cursor.execute(
                'INSERT INTO papersim.settlements (entry_id, classification, '
                'win_loss, proceeds, pnl_gross, fee_total, pnl_net, '
                'confirmed_by, confirmed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (entry_id, 'NATURAL', 'win', 300, 50, 1, 48, 'x', at(80)))
    owner.close()
    app.close()


@NATIVE
def test_reversal_cash_events_reverse_by_reference_only(managed_face):
    psycopg = pytest.importorskip('psycopg')
    owner = connect(OWNER_DSN, autocommit=True)
    marker = uuid.uuid4().hex[:10]
    with owner.cursor() as cursor:
        cursor.execute('INSERT INTO papersim.bankroll_events (event_key, occurred_at, '
                       'currency, amount, reason, note) VALUES (%s,%s,%s,%s,%s,%s) '
                       'RETURNING event_id',
                       ('reversal-src-' + marker, at(10), 'USD', Decimal('12.34'),
                        'ADJUSTMENT', 'fx09l reversal proof'))
        source_id = cursor.fetchone()[0]
        cursor.execute('INSERT INTO papersim.bankroll_events (event_key, occurred_at, '
                       'currency, amount, reason, reverses_event_id) '
                       'VALUES (%s,%s,%s,%s,%s,%s) RETURNING event_id',
                       ('reversal-of-' + marker, at(11), 'USD', Decimal('-12.34'),
                        'REVERSAL', source_id))
        reversal_id = cursor.fetchone()[0]
        assert reversal_id != source_id
        # A REVERSAL without a reference is structurally refused (5.7).
        with pytest.raises(psycopg.errors.CheckViolation):
            cursor.execute('INSERT INTO papersim.bankroll_events (event_key, '
                           'occurred_at, currency, amount, reason) '
                           'VALUES (%s,%s,%s,%s,%s)',
                           ('reversal-dangling-' + marker, at(12), 'USD',
                            Decimal('-1.00'), 'REVERSAL'))
        # One reversal per reversed event (unique reverses_event_id).
        with pytest.raises(psycopg.errors.UniqueViolation):
            cursor.execute('INSERT INTO papersim.bankroll_events (event_key, '
                           'occurred_at, currency, amount, reason, reverses_event_id) '
                           'VALUES (%s,%s,%s,%s,%s,%s)',
                           ('reversal-dup-' + marker, at(13), 'USD', Decimal('-1.00'),
                            'REVERSAL', source_id))
        cursor.execute('SELECT coalesce(sum(amount), 0) FROM papersim.bankroll_events '
                       'WHERE event_id IN (%s, %s)', (source_id, reversal_id))
        assert cursor.fetchone()[0] == Decimal('0.00')  # reversal nets to zero
    owner.close()


@LEGACY_FACE
def test_register_baseline_appends_observed_evidence_and_stays_private(
        managed_face, importer):
    from polymarket_alpha_lab.papersim import ledger, store_psycopg
    _, app_dsn = managed_face
    legacy_dsn = ledger.papersim_dsn()
    owner = connect(OWNER_DSN, autocommit=True)
    with owner.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim_private.observed_baseline')
        before = cursor.fetchone()[0]
    owner.close()

    def connect_legacy():
        return store_psycopg.connect_legacy_readonly(legacy_dsn)

    owner = connect(OWNER_DSN, autocommit=True)
    try:
        result = importer.register_baseline(
            owner, connect_legacy, registered_by='fx09l-native-proof',
            registered_run='fx09l-run-1')
    finally:
        owner.close()
    assert result['registered'] >= 5
    assert {'decisions', 'resolutions', 'bankroll',
            'model_performance', 'forecast_calibration'} <= set(result['relations'])
    owner = connect(OWNER_DSN, autocommit=True)
    with owner.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM papersim_private.observed_baseline')
        after = cursor.fetchone()[0]
        assert after == before + result['registered']
        # Select THIS run's rows: the 09S seed rows carry fixed later
        # timestamps and no row counts, so observed_at ordering must not
        # decide which observation is inspected.
        cursor.execute('SELECT row_count FROM papersim_private.observed_baseline '
                       "WHERE relation_name = 'decisions' AND registered_run = %s",
                       ('fx09l-run-1',))
        assert cursor.fetchone()[0] == 71  # fixture-loaded throwaway face
        cursor.execute('SELECT definition_md5 FROM papersim_private.observed_baseline '
                       "WHERE relation_name = 'model_performance' "
                       "AND registered_run = %s", ('fx09l-run-1',))
        assert cursor.fetchone()[0] is not None
    owner.close()
    # The evidence table stays invisible to the application role.
    app = connect(app_dsn)
    psycopg = pytest.importorskip('psycopg')
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with app.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM papersim_private.observed_baseline')
    app.close()


@LEGACY_FACE
def test_legacy_face_is_unchanged_by_import_and_registration(
        managed_face, importer, imported_plan):
    from polymarket_alpha_lab.papersim import ledger
    store_psycopg = adapter()
    _, app_dsn = managed_face

    def fingerprint(connection):
        with connection.cursor() as cursor:
            cursor.execute('SELECT c.relname, c.relkind FROM pg_class c '
                           'JOIN pg_namespace n ON n.oid = c.relnamespace '
                           "WHERE n.nspname = 'public' AND c.relkind IN ('r','v') "
                           "AND c.relname NOT LIKE 'pg_%' ORDER BY c.relname")
            relations = cursor.fetchall()
            parts = []
            for name, kind in relations:
                if kind == 'v':
                    definition = cursor.execute(
                        'SELECT pg_get_viewdef(%s::regclass, true)', (name,)
                    ).fetchone()[0]
                    parts.append((name, kind, md5(
                        definition.encode('utf-8')).hexdigest()))
                else:
                    count = cursor.execute(
                        'SELECT count(*) FROM public.' + name).fetchone()[0]
                    parts.append((name, kind, int(count)))
        return tuple(parts)

    legacy_dsn = ledger.papersim_dsn()
    read_only = store_psycopg.connect_legacy_readonly(legacy_dsn)
    before = fingerprint(read_only)
    read_only.close()
    # A full import dry run (offline files), an authorized execution against
    # the MANAGED face and a read-only re-observation pass through; the
    # legacy face itself is only ever read (8.3 item 8).
    assert importer.main([]) == 0
    target = store_psycopg.connect_explicit(
        app_dsn, label='PAPERSIM_MIGRATIONS_PG_DSN')
    try:
        importer.execute_import(target, imported_plan)
    finally:
        target.close()
    read_only = store_psycopg.connect_legacy_readonly(legacy_dsn)
    after = fingerprint(read_only)
    read_only.close()
    assert after == before


# ---------------------------------------------------------------------------
# M3/V2 reporting views (design m3-v1-views-design 2026-11-15; M3 plan
# section 7.2 verification matrix). Every proof below builds its OWN
# one-shot managed database, so nothing here depends on the shared
# managed_face fixture or on module ordering; the pre-existing tests in
# this file are untouched (append-only section).
# ---------------------------------------------------------------------------

PAPERSIM_VIEWS = ('natural_settlement_summary', 'candidate_scoring_view',
                  'queue_summary_view')
M1_PAPERSIM_DIGESTS = (
    ('20261115000000_papersim_bootstrap.sql',
     'ef0efa36b4fee775473611f5b73f1d34fe20cb4f13c26608a2aac562d9dcc74c'),
    ('20261115000100_papersim_v2_core.sql',
     '2d5b4f4407d59189d7cfa896c45884efc0c88a24bc3fee403cd18fb070b94945'),
)
REPORTING_VIEWS_MIGRATION = '20261116000010_papersim_reporting_views.sql'


def _rebind_dsn(dsn: str, database: str) -> str:
    return ' '.join(f'dbname={database}' if token.startswith('dbname=') else token
                    for token in dsn.split(' '))


def _rows(connection, statement, params=()):
    with connection.cursor() as cursor:
        cursor.execute(statement, params)
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _fresh_views_database(repo_root=ROOT, expected=None):
    """A private managed-shaped database with the papersim catalog applied."""
    psycopg = pytest.importorskip('psycopg')
    admin = connect(OWNER_DSN, autocommit=True)
    name = 'pal_papersim_v2v_' + uuid.uuid4().hex[:10]
    with admin.cursor() as cursor:
        cursor.execute("SELECT system_identifier::text FROM pg_control_system()")
        system_id = cursor.fetchone()[0]
        cursor.execute(f"CREATE DATABASE {name} OWNER pal_owner")
    admin.close()
    owner_dsn = _rebind_dsn(OWNER_DSN, name)
    app_dsn = owner_dsn.replace('user=pal_owner', 'user=pal_app')
    bootstrap = psycopg.connect(owner_dsn)
    bootstrap.autocommit = True
    with bootstrap.cursor() as cursor:
        from polymarket_alpha_lab.project_postgres import sql
        cursor.execute(sql.bootstrap_sql(uuid.uuid4().hex, 'b' * 64, system_id))
    bootstrap.close()
    owner = connect(owner_dsn, autocommit=False)
    result = pm.apply_pending_migrations(owner, repo_root)
    if expected is not None:
        assert result == expected
    owner.close()
    return owner_dsn, app_dsn


def _entry_id_for(owner, candidate_id):
    with owner.cursor() as cursor:
        cursor.execute('SELECT entry_id FROM papersim.entries WHERE candidate_id = %s',
                       (candidate_id,))
        return cursor.fetchone()[0]


def _insert_settlement(owner, entry_id, classification, *, pnl_gross, fee_total,
                       proceeds, confirmed_at, win_loss=None, outcome_label=None,
                       exit_price=None, source_outcome_id=None):
    pnl_gross = Decimal(str(pnl_gross))
    fee_total = Decimal(str(fee_total))
    with owner.cursor() as cursor:
        cursor.execute(
            'INSERT INTO papersim.settlements (entry_id, classification, outcome_label, '
            'win_loss, exit_price, proceeds, pnl_gross, fee_total, pnl_net, '
            'source_outcome_id, confirmed_by, confirmed_at) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (entry_id, classification, outcome_label, win_loss, exit_price,
             Decimal(str(proceeds)), pnl_gross, fee_total, pnl_gross - fee_total,
             source_outcome_id, 'm3-v2-views', confirmed_at))


def _sized_entry(stake, price, fee_category, rate):
    stake = Decimal(str(stake))
    price = Decimal(str(price))
    shares = (stake / price).quantize(Decimal('0.000001'))
    fee = (stake * Decimal(str(rate)) * (Decimal(1) - price)).quantize(Decimal('0.000001'))
    return entry(stake=stake, price=price, shares=shares, fee_category=fee_category,
                 fee_rate=Decimal(str(rate)), entry_fee=fee, fee_source='schedule')


def _queue_row(app, model_name):
    rows = _rows(app, 'SELECT * FROM papersim.queue_summary_view '
                      'WHERE model_name = %s', (model_name,))
    assert len(rows) == 1, rows
    return rows[0]


def _view_catalog_tree(tree_root):
    """Copy the live papersim migration directory into a synthetic repo tree."""
    import shutil
    directory = ROOT / 'database/papersim/migrations'
    (tree_root / 'database/papersim/migrations').mkdir(parents=True)
    entries = []
    for path in sorted(directory.glob('*.sql')):
        shutil.copyfile(path, tree_root / 'database/papersim/migrations' / path.name)
        entries.append({'name': path.name,
                        'sha256': sha256(path.read_bytes()).hexdigest(),
                        'transaction_wrapper': True})
    return tree_root, entries


def _write_manifest(tree_root, entries):
    import json
    (tree_root / 'database/papersim/migrations.lock.json').write_text(
        json.dumps({'format': pm.MANIFEST_FORMAT, 'migrations': entries}), encoding='utf-8')
    return tree_root


def test_old_migration_and_v1_faces_are_pinned_unchanged():
    import json
    manifest = json.loads(
        (ROOT / 'database/papersim/migrations.lock.json').read_text(encoding='utf-8'))
    entries = manifest['migrations']
    # Manifest pin 3 -> 4 entries after the V2R3 rebase onto integrated
    # main: [M1, M1, S0 operations, V2 reporting views] (the M1 pair and
    # the S0 entry keep their published bytes; the V1 face is untouched).
    assert [item['name'] for item in entries] == [
        '20261115000000_papersim_bootstrap.sql',
        '20261115000100_papersim_v2_core.sql',
        '20261116000000_papersim_m2_operations.sql',
        REPORTING_VIEWS_MIGRATION]
    directory = ROOT / 'database/papersim/migrations'
    for item, (name, pinned) in zip(entries[:2], M1_PAPERSIM_DIGESTS, strict=True):
        assert item['name'] == name and item['sha256'] == pinned
        assert sha256((directory / name).read_bytes()).hexdigest() == pinned
    v1_manifest = json.loads(
        (ROOT / 'database/migrations.lock.json').read_text(encoding='utf-8'))
    assert len(v1_manifest['migrations']) == 70  # the V1 face is untouched


def test_reporting_views_catalog_red_conditions(tmp_path):
    tree, entries = _view_catalog_tree(tmp_path / 'repo')
    _write_manifest(tree, entries)
    # Tampered reporting-views bytes stop the catalog loudly.
    target = tree / 'database/papersim/migrations' / REPORTING_VIEWS_MIGRATION
    target.write_bytes(target.read_bytes() + b'\n-- drift\n')
    with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_changed'):
        pm.papersim_migration_catalog(tree)
    # A lock manifest that omits the trailing reporting-views entry (the
    # last of the four live entries after the V2R3 rebase) is an
    # inventory mismatch.
    _write_manifest(tree, entries[:3])
    with pytest.raises(pm.PapersimMigrationError,
                       match='papersim_migration_inventory_mismatch'):
        pm.papersim_migration_catalog(tree)


@NATIVE
def test_reporting_views_apply_matrix_incremental_and_idempotent(tmp_path):
    import shutil
    owner_dsn, app_dsn = _fresh_views_database(
        expected={'applied': 4, 'pending_before': 4})
    owner = connect(owner_dsn, autocommit=False)
    assert pm.apply_pending_migrations(owner, ROOT) == {'applied': 0, 'pending_before': 0}
    owner.close()
    app = connect(app_dsn)
    listed = [row['table_name'] for row in _rows(
        app, "SELECT table_name FROM information_schema.views "
             "WHERE table_schema = 'papersim' ORDER BY table_name")]
    assert listed == ['candidate_scoring_view', 'natural_settlement_summary',
                      'queue_summary_view']
    for view in PAPERSIM_VIEWS:  # the app role can SELECT every view
        assert _rows(app, f'SELECT count(*) AS n FROM papersim.{view}')[0]['n'] == 0
    granted = [row['table_name'] for row in _rows(
        app, "SELECT table_name FROM information_schema.role_table_grants "
             "WHERE table_schema = 'papersim' AND grantee = 'pal_app' "
             "AND privilege_type = 'SELECT' AND table_name = ANY(%s) "
             "ORDER BY table_name", (list(PAPERSIM_VIEWS),))]
    assert granted == ['candidate_scoring_view', 'natural_settlement_summary',
                       'queue_summary_view']
    app.close()
    # Incremental face: the two M1 migrations first, then the full catalog
    # adds exactly the two post-M1 migrations (the S0 operations entry and
    # the reporting-views entry).
    tree = tmp_path / 'm1-tree'
    (tree / 'database/papersim/migrations').mkdir(parents=True)
    entries = []
    for name, _digest in M1_PAPERSIM_DIGESTS:
        shutil.copyfile(ROOT / 'database/papersim/migrations' / name,
                        tree / 'database/papersim/migrations' / name)
        entries.append({'name': name,
                        'sha256': sha256(
                            (tree / 'database/papersim/migrations' / name).read_bytes()
                        ).hexdigest(),
                        'transaction_wrapper': True})
    _write_manifest(tree, entries)
    inc_owner_dsn, _ = _fresh_views_database(
        repo_root=tree, expected={'applied': 2, 'pending_before': 2})
    owner = connect(inc_owner_dsn, autocommit=False)
    assert pm.apply_pending_migrations(owner, ROOT) == {'applied': 2, 'pending_before': 2}
    assert pm.apply_pending_migrations(owner, ROOT) == {'applied': 0, 'pending_before': 0}
    status = pm.migration_status(owner, ROOT)
    assert status['applied'] == [
        '20261115000000_papersim_bootstrap.sql',
        '20261115000100_papersim_v2_core.sql',
        '20261116000000_papersim_m2_operations.sql',
        REPORTING_VIEWS_MIGRATION]
    assert status['pending'] == []
    owner.close()


@NATIVE
def test_reporting_views_survive_a_failed_fourth_migration(tmp_path):
    tree, entries = _view_catalog_tree(tmp_path / 'repo')
    # Synthetic follow-up migration keeps its 20261117* timestamp so it
    # still appends after the live four-entry catalog tail (M1 pair, S0
    # operations, V2 reporting views).
    failing = tree / 'database/papersim/migrations/20261117000000_views_fail.sql'
    failing.write_text('begin;\ncreate table papersim.boom (x int);\nselect 1 / 0;\ncommit;\n',
                       encoding='utf-8')
    entries.append({'name': failing.name,
                    'sha256': sha256(failing.read_bytes()).hexdigest(),
                    'transaction_wrapper': True})
    _write_manifest(tree, entries)
    owner_dsn, _ = _fresh_views_database()
    owner = connect(owner_dsn, autocommit=False)
    with pytest.raises(pm.PapersimMigrationError, match='papersim_migration_apply_failed'):
        pm.apply_pending_migrations(owner, tree)
    counts = _rows(
        owner,
        "SELECT (SELECT count(*) FROM papersim_private.migrations) AS ledger_rows, "
        "(SELECT count(*) FROM information_schema.tables WHERE table_schema = 'papersim' "
        " AND table_name = 'boom') AS boom_tables, "
        "(SELECT count(*) FROM information_schema.tables WHERE table_schema = 'papersim' "
        " AND table_type = 'BASE TABLE') AS base_tables, "
        "(SELECT count(*) FROM information_schema.views "
        " WHERE table_schema = 'papersim') AS view_rows")[0]
    assert counts == {'ledger_rows': 4, 'boom_tables': 0, 'base_tables': 13,
                      'view_rows': 3}
    for view in PAPERSIM_VIEWS:  # the applied views stay queryable
        assert _rows(owner, f'SELECT count(*) AS n FROM papersim.{view}')[0]['n'] == 0
    owner.close()


@NATIVE
def test_two_entries_one_market_and_outcome_versions_never_double_count():
    import math
    owner_dsn, app_dsn = _fresh_views_database()
    store_psycopg = adapter()
    owner = connect(owner_dsn, autocommit=True)
    app = connect(app_dsn)
    market = '3866000'

    def decisions(ref, first_mid, first_prob):
        # Two DECISION rows plus two CLOSE rows: the representatives are
        # the smallest DECISION evidence_id and the LATEST CLOSE row.
        return (evidence(observed_at=at(4), market_ref_kind='market_id', market_ref=ref,
                         side='YES', mid=Decimal(first_mid),
                         model_probability=Decimal(first_prob)),
                evidence(observed_at=at(5), market_ref_kind='market_id', market_ref=ref,
                         side='YES', mid=Decimal('0.99'),
                         model_probability=Decimal('0.99')),
                evidence(observed_at=at(9), evidence_role='CLOSE',
                         market_ref_kind='market_id', market_ref=ref, side='YES',
                         mid=Decimal('0.80'), last=Decimal('0.80'),
                         source_kind='clob_terminal_price', source_ref='c1'),
                evidence(observed_at=at(10), evidence_role='CLOSE',
                         market_ref_kind='market_id', market_ref=ref, side='YES',
                         mid=Decimal('0.85'), last=Decimal('0.85'),
                         source_kind='clob_terminal_price', source_ref='c2'))

    rows = (
        candidate(candidate_key='e1', conclusion='ACCEPTED', market_slug=None,
                  condition_id=None, market_id=market,
                  price_evidence=decisions(market, '0.515', '0.68'),
                  entry=_sized_entry(750, '0.515', 'crypto', '0.07')),
        candidate(candidate_key='e2', conclusion='ACCEPTED', market_slug=None,
                  condition_id=None, market_id=market,
                  price_evidence=decisions(market, '0.455', '0.70'),
                  entry=_sized_entry(500, '0.455', 'crypto', '0.07')),
    )
    result = store_psycopg.record_scan(app, store.ScanSubmission(scan=scan_run(),
                                                                 candidates=rows))
    base = store_psycopg.record_market_outcome(app, outcome(
        market_id=market, market_slug=None, outcome='YES', observed_at=at(30),
        source_ref='base-' + market))['outcome_id']
    store_psycopg.record_market_outcome(app, outcome(
        market_id=market, market_slug=None, outcome='UNKNOWN', confirmed_at=None,
        observed_at=at(100), source_ref='later-unknown-' + market))
    corrected = store_psycopg.record_market_outcome(app, outcome(
        market_id=market, market_slug=None, outcome='YES', observed_at=at(150),
        confirmed_at=at(160), source_ref='correction-' + market))['outcome_id']
    assert corrected != base
    _insert_settlement(owner, _entry_id_for(owner, result['candidate_ids']['e1']),
                       'NATURAL', win_loss='win', pnl_gross='743.68', fee_total='25.4625',
                       proceeds='1493.68', confirmed_at=at(70))
    _insert_settlement(owner, _entry_id_for(owner, result['candidate_ids']['e2']),
                       'NATURAL', win_loss='win', pnl_gross='561.52', fee_total='19.0750',
                       proceeds='1061.52', confirmed_at=at(71))

    summary = _rows(app, 'SELECT * FROM papersim.natural_settlement_summary')
    assert len(summary) == 1  # one market identity row, no fan-out
    row = summary[0]
    assert (row['market_ref_kind'], row['market_ref']) == ('market_id', market)
    assert row['effective_outcome_id'] == corrected  # later YES wins; UNKNOWN never shadows
    assert row['effective_outcome'] == 'YES'
    assert row['outcome_observed_at'] == at(150)
    assert row['settlements_total'] == 2
    assert (row['natural_settled'], row['natural_hits'],
            row['natural_losses']) == (2, 2, 0)
    assert row['natural_hit_rate'] == Decimal(1)
    assert row['natural_pnl_gross'] == Decimal('1305.20')  # 743.68 + 561.52, no doubling
    assert row['natural_fee_total'] == Decimal('44.5375')
    assert row['natural_pnl_net'] == Decimal('1260.6625')
    assert (row['exit_settled'], row['voided_settled']) == (0, 0)

    scored = _rows(app, 'SELECT * FROM papersim.candidate_scoring_view '
                        'ORDER BY candidate_id')
    first_evidence = _rows(
        app, 'SELECT candidate_id, '
             'min(evidence_id) FILTER (WHERE evidence_role = \'DECISION\') AS first_id, '
             'count(*) FILTER (WHERE evidence_role = \'DECISION\') AS decisions, '
             'count(*) FILTER (WHERE evidence_role = \'CLOSE\') AS closes '
             'FROM papersim.candidate_price_evidence GROUP BY candidate_id '
             'ORDER BY candidate_id')
    assert len(scored) == 2 and len(first_evidence) == 2
    for score, first in zip(scored, first_evidence, strict=True):
        assert first['decisions'] == 2 and first['closes'] == 2  # no cross product
        assert score['decision_evidence_id'] == first['first_id']
        assert score['scoring_status'] == 'OK'
        assert score['natural_hit'] is True
        assert score['effective_outcome'] == 'YES'
        assert score['entry_id'] is not None
    assert scored[0]['model_probability'] == Decimal('0.68')
    assert scored[0]['decision_mid'] == Decimal('0.515')
    assert scored[0]['close_mid'] == Decimal('0.85')  # latest CLOSE row wins
    assert scored[0]['market_mid_side_aligned'] == Decimal('0.515')
    assert scored[0]['model_minus_market'] == Decimal('0.165')
    assert scored[0]['brier_pair_diff'] == Decimal('-0.132825')
    expected_logloss = -math.log(0.68) + math.log(0.515)
    assert abs(float(scored[0]['logloss_pair_diff']) - expected_logloss) < 1e-9
    assert scored[0]['infinite_log_loss_pair'] is False

    queue = _queue_row(app, 'gbm')
    assert (queue['candidates_total'], queue['entered'],
            queue['settled_natural']) == (2, 2, 2)
    assert (queue['natural_hits'], queue['natural_hit_rate']) == (2, Decimal(1))
    assert queue['stake_sum'] == Decimal(1250)
    assert queue['gross_pnl_sum'] == Decimal('1305.20')
    assert queue['net_pnl_sum'] == Decimal('1260.6625')
    assert (queue['entries_fee_source_schedule'],
            queue['entries_fee_source_estimated']) == (2, 0)
    assert queue['top_market_key'] == market
    assert queue['top_market_pnl'] == Decimal('1305.20')
    assert queue['top_market_share'] == Decimal(1)
    assert queue['top_market_net_pnl'] == Decimal('1260.6625')
    assert queue['markets_total'] == 1 and queue['markets_scored'] == 1
    owner.close()
    app.close()


@NATIVE
def test_win_loss_labels_count_as_natural_settlements():
    # Section 78.5 P3 counter-shape: the settlement side carries only
    # win/loss labels (the market outcome row is UNKNOWN). The views must
    # report settled=2 / hits=1 / rate=0.5, never the bug's 0/0/NULL.
    owner_dsn, app_dsn = _fresh_views_database()
    store_psycopg = adapter()
    owner = connect(owner_dsn, autocommit=True)
    app = connect(app_dsn)
    slug = unique_slug()
    rows = (
        candidate(candidate_key='wl-win', conclusion='ACCEPTED', market_slug=slug,
                  side='YES', price_evidence=(evidence(
                      observed_at=at(4), market_ref=slug, mid=Decimal('0.60'),
                      model_probability=Decimal('0.55')),),
                  entry=_sized_entry(100, '0.60', 'crypto', '0.07')),
        candidate(candidate_key='wl-loss', conclusion='ACCEPTED', market_slug=slug,
                  side='YES', price_evidence=(evidence(
                      observed_at=at(4), market_ref=slug, mid=Decimal('0.40'),
                      model_probability=Decimal('0.45')),),
                  entry=_sized_entry(100, '0.40', 'crypto', '0.07')),
    )
    result = store_psycopg.record_scan(app, store.ScanSubmission(scan=scan_run(),
                                                                 candidates=rows))
    store_psycopg.record_market_outcome(app, outcome(
        market_slug=slug, outcome='UNKNOWN', confirmed_at=None, observed_at=at(30)))
    _insert_settlement(owner, _entry_id_for(owner, result['candidate_ids']['wl-win']),
                       'NATURAL', win_loss='win', pnl_gross='66.666667',
                       fee_total='2.800000', proceeds='166.666667', confirmed_at=at(70))
    _insert_settlement(owner, _entry_id_for(owner, result['candidate_ids']['wl-loss']),
                       'NATURAL', win_loss='loss', pnl_gross='-100', fee_total='4.200000',
                       proceeds='0', confirmed_at=at(71))
    row = _rows(app, 'SELECT * FROM papersim.natural_settlement_summary')[0]
    assert row['effective_outcome'] == 'UNKNOWN'
    assert (row['natural_settled'], row['natural_hits'],
            row['natural_losses']) == (2, 1, 1)
    assert row['natural_hit_rate'] == Decimal('0.5')
    queue = _queue_row(app, 'gbm')
    assert (queue['settled_natural'], queue['natural_hits'],
            queue['natural_hit_rate']) == (2, 1, Decimal('0.5'))
    hits = [item['natural_hit'] for item in _rows(
        app, 'SELECT natural_hit FROM papersim.candidate_scoring_view '
             'ORDER BY candidate_id')]
    # The market-level result is UNKNOWN (recorded_label), so the
    # per-candidate natural_hit stays a three-valued gap (design section
    # 4.2 / vector A-3); the settlement-side statistics above carry the
    # 2/1/0.5 truth. An unknown market result is never guessed.
    assert hits == [None, None]
    assert queue['markets_scored'] == 0  # unknown market result stays a gap
    assert queue['markets_missing_outcome'] == 1
    owner.close()
    app.close()


@NATIVE
def test_exit_and_voided_stay_out_of_the_natural_hit_rate():
    owner_dsn, app_dsn = _fresh_views_database()
    store_psycopg = adapter()
    owner = connect(owner_dsn, autocommit=True)
    app = connect(app_dsn)
    natural = unique_slug()
    exit_slug = unique_slug()
    void_slug = unique_slug()
    rows = (
        candidate(candidate_key='mix-win', conclusion='ACCEPTED', market_slug=natural,
                  side='YES', price_evidence=(evidence(
                      observed_at=at(4), market_ref=natural, mid=Decimal('0.60'),
                      model_probability=Decimal('0.55')),),
                  entry=_sized_entry(100, '0.60', 'crypto', '0.07')),
        candidate(candidate_key='mix-loss', conclusion='ACCEPTED', market_slug=natural,
                  side='NO', price_evidence=(evidence(
                      observed_at=at(4), market_ref=natural, side='NO',
                      mid=Decimal('0.40'), model_probability=Decimal('0.45')),),
                  entry=_sized_entry(100, '0.40', 'crypto', '0.07')),
        candidate(candidate_key='mix-exit', conclusion='ACCEPTED', market_slug=exit_slug,
                  price_evidence=(evidence(
                      observed_at=at(4), market_ref=exit_slug, mid=Decimal('0.50'),
                      model_probability=Decimal('0.52')),),
                  entry=_sized_entry(100, '0.50', 'crypto', '0.07')),
        candidate(candidate_key='mix-void', conclusion='ACCEPTED', market_slug=void_slug,
                  price_evidence=(evidence(
                      observed_at=at(4), market_ref=void_slug, mid=Decimal('0.30'),
                      model_probability=Decimal('0.35')),),
                  entry=_sized_entry(120, '0.30', 'crypto', '0.07')),
    )
    result = store_psycopg.record_scan(app, store.ScanSubmission(
        scan=scan_run(), candidates=rows))
    void_outcome = store_psycopg.record_market_outcome(app, outcome(
        market_slug=natural, outcome='YES', observed_at=at(30)))['outcome_id']
    store_psycopg.record_market_outcome(app, outcome(
        market_slug=void_slug, outcome='VOIDED', confirmed_at=None, observed_at=at(30)))
    ids = result['candidate_ids']
    _insert_settlement(owner, _entry_id_for(owner, ids['mix-win']),
                       'NATURAL', win_loss='win', pnl_gross='66.666667',
                       fee_total='2.800000', proceeds='166.666667', confirmed_at=at(70),
                       source_outcome_id=void_outcome)  # the natural market's YES row
    _insert_settlement(owner, _entry_id_for(owner, ids['mix-loss']),
                       'NATURAL', win_loss='loss', pnl_gross='-100',
                       fee_total='4.200000', proceeds='0', confirmed_at=at(71))
    _insert_settlement(owner, _entry_id_for(owner, ids['mix-exit']),
                       'EXIT', pnl_gross='-8', fee_total='3.500000', proceeds='92',
                       confirmed_at=at(72), exit_price='0.46')
    void_entry = _entry_id_for(owner, ids['mix-void'])
    _insert_settlement(owner, void_entry, 'VOIDED_REFUND', pnl_gross='0',
                       fee_total='0', proceeds='120', confirmed_at=at(73))

    queue = _queue_row(app, 'gbm')
    assert (queue['settled_natural'], queue['settled_exit'],
            queue['settled_voided']) == (2, 1, 1)
    # EXIT and VOIDED_REFUND stay out of the hit-rate denominator (2
    # natural settlements, not 4).
    assert (queue['natural_hits'], queue['natural_hit_rate']) == (1, Decimal('0.5'))

    summary = {row['market_ref']: row for row in _rows(
        app, 'SELECT * FROM papersim.natural_settlement_summary')}
    assert set(summary) == {natural, void_slug}  # the EXIT market minted no row
    assert exit_slug not in summary
    void_row = summary[void_slug]
    assert void_row['effective_outcome'] == 'VOIDED'
    assert void_row['voided_settled'] == 1
    assert void_row['voided_pnl_gross'] == Decimal(0)
    assert void_row['voided_pnl_net'] == Decimal(0)  # proceeds = stake
    assert void_row['natural_settled'] == 0
    assert void_row['natural_hit_rate'] is None  # zero denominator is NULL, not 0
    exit_row = _rows(app, 'SELECT scoring_status, natural_hit, effective_outcome '
                          'FROM papersim.candidate_scoring_view WHERE candidate_key = '
                          '%s', ('mix-exit',))[0]
    assert exit_row['effective_outcome'] is None  # an early exit is no natural loss
    assert exit_row['natural_hit'] is None
    assert exit_row['scoring_status'] == 'NO_OUTCOME_OBSERVATION'
    owner.close()
    app.close()


@NATIVE
def test_rejected_candidates_score_and_coverage_gaps_stay_gaps():
    owner_dsn, app_dsn = _fresh_views_database()
    store_psycopg = adapter()
    app = connect(app_dsn)
    ok = unique_slug()
    rejected = unique_slug()
    unknown = unique_slug()
    no_mid = unique_slug()
    no_prob = unique_slug()
    rows = (
        candidate(candidate_key='cov-ok', conclusion='ACCEPTED', market_slug=ok,
                  model_name='cov-model', price_evidence=(evidence(
                      observed_at=at(4), market_ref=ok, mid=Decimal('0.51'),
                      model_probability=Decimal('0.634')),),
                  entry=_sized_entry(200, '0.51', 'sports', '0.05')),
        candidate(candidate_key='cov-same-market', conclusion='REJECTED', market_slug=ok,
                  model_name='cov-model', price_evidence=(evidence(
                      observed_at=at(6), market_ref=ok, mid=Decimal('0.52'),
                      model_probability=Decimal('0.60')),)),
        candidate(candidate_key='cov-rejected', conclusion='REJECTED',
                  market_slug=rejected, model_name='cov-model', side='NO',
                  price_evidence=(evidence(
                      observed_at=at(4), market_ref=rejected, side='NO',
                      mid=Decimal('0.55'), model_probability=Decimal('0.71')),)),
        candidate(candidate_key='cov-construction', conclusion='CONSTRUCTION_FAILED',
                  model_name='cov-model', market_slug=None, market_id=None,
                  condition_id=None),
        candidate(candidate_key='cov-unknown', conclusion='ACCEPTED',
                  market_slug=unknown, model_name='cov-model',
                  price_evidence=(evidence(
                      observed_at=at(4), market_ref=unknown, mid=Decimal('0.50'),
                      model_probability=Decimal('0.50')),),
                  entry=_sized_entry(50, '0.50', 'sports', '0.05')),
        candidate(candidate_key='cov-no-mid', conclusion='ACCEPTED', market_slug=no_mid,
                  model_name='cov-model', price_evidence=(evidence(
                      observed_at=at(4), market_ref=no_mid,
                      model_probability=Decimal('0.50')),),
                  entry=_sized_entry(50, '0.50', 'sports', '0.05')),
        candidate(candidate_key='cov-no-prob', conclusion='ACCEPTED',
                  market_slug=no_prob, model_name='cov-model', price_evidence=(
                      evidence(observed_at=at(4), market_ref=no_prob,
                               mid=Decimal('0.50')),),
                  entry=_sized_entry(50, '0.50', 'sports', '0.05')),
    )
    store_psycopg.record_scan(app, store.ScanSubmission(scan=scan_run(), candidates=rows))
    store_psycopg.record_market_outcome(app, outcome(market_slug=ok, outcome='NO',
                                                     observed_at=at(30)))
    store_psycopg.record_market_outcome(app, outcome(market_slug=rejected, outcome='NO',
                                                     observed_at=at(30)))
    store_psycopg.record_market_outcome(app, outcome(market_slug=unknown,
                                                     outcome='UNKNOWN',
                                                     confirmed_at=None,
                                                     observed_at=at(30)))
    store_psycopg.record_market_outcome(app, outcome(market_slug=no_mid, outcome='YES',
                                                     observed_at=at(30)))
    store_psycopg.record_market_outcome(app, outcome(market_slug=no_prob, outcome='YES',
                                                     observed_at=at(30)))

    status = {item['candidate_key']: item for item in _rows(
        app, 'SELECT candidate_key, market_key, scoring_status, natural_hit, '
             'model_minus_market, brier_pair_diff, logloss_pair_diff, entry_id, stake '
             'FROM papersim.candidate_scoring_view')}
    assert status['cov-ok']['scoring_status'] == 'OK'
    assert status['cov-ok']['natural_hit'] is False  # YES side judged NO
    assert status['cov-ok']['model_minus_market'] == Decimal('0.124')
    assert status['cov-ok']['brier_pair_diff'] == Decimal('0.141856')
    assert status['cov-rejected']['scoring_status'] == 'OK'  # rejected still scores
    assert status['cov-rejected']['natural_hit'] is True
    assert status['cov-rejected']['entry_id'] is None  # a rejection is never a trade
    assert status['cov-rejected']['stake'] is None
    assert status['cov-construction']['scoring_status'] == 'NO_BINDING'
    assert status['cov-construction']['market_key'] is None
    assert status['cov-unknown']['scoring_status'] == 'UNKNOWN_OUTCOME'
    assert status['cov-unknown']['natural_hit'] is None
    assert status['cov-no-mid']['scoring_status'] == 'MISSING_MARKET_MID'
    assert status['cov-no-prob']['scoring_status'] == 'MISSING_MODEL_PROBABILITY'
    for key in ('cov-construction', 'cov-unknown', 'cov-no-mid', 'cov-no-prob'):
        assert status[key]['model_minus_market'] is None
        assert status[key]['brier_pair_diff'] is None
        assert status[key]['logloss_pair_diff'] is None  # gaps are never zero-filled

    queue = _queue_row(app, 'cov-model')
    assert queue['candidates_total'] == 7
    assert queue['markets_missing_binding'] == 1  # construction failure, not a market
    assert queue['markets_total'] == 5
    assert queue['deduped_repeats'] == 1  # six bound candidates over five markets
    assert queue['markets_scored'] == 2  # the ok market rep and the rejected market
    assert queue['markets_missing_outcome'] == 1
    assert queue['markets_missing_market_mid'] == 1
    assert queue['markets_missing_model_probability'] == 1
    assert queue['brier_diff_mean'] == Decimal('0.011728')
    app.close()


@NATIVE
def test_net_concentration_counterexample_and_undefined_when_negative():
    owner_dsn, app_dsn = _fresh_views_database()
    store_psycopg = adapter()
    owner = connect(owner_dsn, autocommit=True)
    app = connect(app_dsn)
    price = Decimal('0.6817148572467722')
    # Unquantized recorded amounts keep the seeded book equal to the
    # P2-02 anchor arithmetic (quantizing here would drift the net share
    # by ~1e-9, outside the 1e-9 design tolerance).
    gross_each = Decimal(250) / price - Decimal(250)
    fee_each = Decimal(250) * Decimal('0.05') * (Decimal(1) - price)
    rows = [candidate(
        candidate_key='conc-top', conclusion='ACCEPTED', market_slug='mkt-top',
        model_name='conc-model', price_evidence=(evidence(
            observed_at=at(4), market_ref='mkt-top', mid=Decimal('0.50'),
            model_probability=Decimal('0.55')),),
        entry=_sized_entry(150, '0.50', 'sports', '0.05'))]
    for index in (2, 3, 4):
        rows.append(candidate(
            candidate_key=f'conc-{index}', conclusion='ACCEPTED',
            market_slug=f'mkt-{index}', model_name='conc-model', price_evidence=(
                evidence(observed_at=at(4), market_ref=f'mkt-{index}', mid=price,
                         model_probability=Decimal('0.75')),),
            entry=_sized_entry(250, price, 'sports', '0.05')))
    loser_rows = [
        candidate(candidate_key='lose-1', conclusion='ACCEPTED', market_slug='lose-1',
                  model_name='loser-model', price_evidence=(evidence(
                      observed_at=at(4), market_ref='lose-1', mid=Decimal('0.50'),
                      model_probability=Decimal('0.55')),),
                  entry=_sized_entry(200, '0.50', 'sports', '0.05')),
        candidate(candidate_key='lose-2', conclusion='ACCEPTED', market_slug='lose-2',
                  model_name='loser-model', price_evidence=(evidence(
                      observed_at=at(4), market_ref='lose-2', mid=Decimal('0.40'),
                      model_probability=Decimal('0.45')),),
                  entry=_sized_entry(100, '0.40', 'sports', '0.05')),
    ]
    result = store_psycopg.record_scan(app, store.ScanSubmission(
        scan=scan_run(), candidates=tuple(rows)))
    loser_result = store_psycopg.record_scan(app, store.ScanSubmission(
        scan=scan_run(), candidates=tuple(loser_rows)))
    for key, slug in (('conc-top', 'mkt-top'), ('conc-2', 'mkt-2'),
                      ('conc-3', 'mkt-3'), ('conc-4', 'mkt-4')):
        store_psycopg.record_market_outcome(app, outcome(market_slug=slug, outcome='YES',
                                                         observed_at=at(30)))
        gross = Decimal('150') if key == 'conc-top' else gross_each
        fee = Decimal('3.750000') if key == 'conc-top' else fee_each
        _insert_settlement(owner, _entry_id_for(owner, result['candidate_ids'][key]),
                           'NATURAL', win_loss='win', pnl_gross=gross, fee_total=fee,
                           proceeds=Decimal(150 if key == 'conc-top' else 250) + gross,
                           confirmed_at=at(70))
    for key, slug, stake in (('lose-1', 'lose-1', 200), ('lose-2', 'lose-2', 100)):
        entry_id = _entry_id_for(owner, loser_result['candidate_ids'][key])
        _insert_settlement(owner, entry_id, 'NATURAL', win_loss='loss',
                           pnl_gross=-stake, fee_total=_sized_entry(
                               stake, '0.50' if key == 'lose-1' else '0.40', 'sports',
                               '0.05').entry_fee, proceeds='0', confirmed_at=at(71))

    conc = _queue_row(app, 'conc-model')
    assert conc['top_market_key'] == 'mkt-top'
    assert conc['top_market_pnl'] == Decimal(150)
    assert abs(float(conc['top_market_share']) - 0.299900) < 5e-6
    assert conc['top_market_net_key'] == 'mkt-top'
    assert conc['top_market_net_pnl'] == Decimal('146.25')
    assert abs(float(conc['top_market_net_share']) - 0.301869404845) < 1e-9
    # The two bases stand side by side; the gross value must never stand
    # in for the net one (OD-G criterion E reads the net column).
    assert conc['top_market_net_share'] != conc['top_market_share']
    assert float(conc['top_market_net_share']) > float(conc['top_market_share'])

    loser = _queue_row(app, 'loser-model')
    assert loser['top_market_pnl'] == Decimal(-100)  # least-negative market
    assert loser['top_market_share'] is None  # total pnl not positive: undefined
    assert loser['top_market_net_share'] is None  # never a fabricated ratio
    owner.close()
    app.close()


@NATIVE
def test_views_reconcile_with_ledger_sums_and_the_evaluator():
    from polymarket_alpha_lab.papersim import evaluation as ev
    owner_dsn, app_dsn = _fresh_views_database()
    store_psycopg = adapter()
    owner = connect(owner_dsn, autocommit=True)
    app = connect(app_dsn)
    market_a = unique_slug()
    market_b = unique_slug()
    market_e = unique_slug()
    model_key = 'rec-model/r1'
    rows = (
        candidate(candidate_key='rec-a1', conclusion='ACCEPTED', market_slug=market_a,
                  model_name='rec-model', price_evidence=(evidence(
                      observed_at=at(4), market_ref=market_a, mid=Decimal('0.50'),
                      model_probability=Decimal('0.62')),),
                  entry=_sized_entry(250, '0.56', 'crypto', '0.07')),
        candidate(candidate_key='rec-a2', conclusion='ACCEPTED', market_slug=market_a,
                  model_name='rec-model', side='NO', price_evidence=(evidence(
                      observed_at=at(5), market_ref=market_a, mid=Decimal('0.49'),
                      model_probability=Decimal('0.634')),),
                  entry=_sized_entry(150, '0.40', 'crypto', '0.07')),
        candidate(candidate_key='rec-b', conclusion='ACCEPTED', market_slug=market_b,
                  model_name='rec-model', price_evidence=(evidence(
                      observed_at=at(4), market_ref=market_b, mid=Decimal('0.52'),
                      model_probability=Decimal('0.55')),),
                  entry=_sized_entry(100, '0.50', 'crypto', '0.07')),
        candidate(candidate_key='rec-e', conclusion='REJECTED', market_slug=market_e,
                  model_name='rec-model', side='NO', price_evidence=(evidence(
                      observed_at=at(4), market_ref=market_e, side='NO',
                      mid=Decimal('0.55'), model_probability=Decimal('0.71')),)),
    )
    result = store_psycopg.record_scan(app, store.ScanSubmission(scan=scan_run(),
                                                                 candidates=rows))
    store_psycopg.record_market_outcome(app, outcome(market_slug=market_a, outcome='YES',
                                                     observed_at=at(30)))
    store_psycopg.record_market_outcome(app, outcome(market_slug=market_e, outcome='NO',
                                                     observed_at=at(30)))
    ids = result['candidate_ids']
    a1_fee = _sized_entry(250, '0.56', 'crypto', '0.07').entry_fee
    a2_fee = _sized_entry(150, '0.40', 'crypto', '0.07').entry_fee
    b_entry = _sized_entry(100, '0.50', 'crypto', '0.07')
    b_exit_fee = (Decimal(200) * Decimal('0.07') * Decimal('0.46')
                  * Decimal('0.54')).quantize(Decimal('0.000001'))
    _insert_settlement(owner, _entry_id_for(owner, ids['rec-a1']), 'NATURAL',
                       win_loss='win', pnl_gross='196.428571', fee_total=a1_fee,
                       proceeds='446.428571', confirmed_at=at(70))
    _insert_settlement(owner, _entry_id_for(owner, ids['rec-a2']), 'NATURAL',
                       win_loss='loss', pnl_gross='-150', fee_total=a2_fee,
                       proceeds='0', confirmed_at=at(71))
    _insert_settlement(owner, _entry_id_for(owner, ids['rec-b']), 'EXIT',
                       pnl_gross='-8', fee_total=b_entry.entry_fee + b_exit_fee,
                       proceeds='92', confirmed_at=at(72), exit_price='0.46')
    with owner.cursor() as cursor:  # one close mark drives the CLV input
        cursor.execute('INSERT INTO papersim.marks (entry_id, mark_key, marked_at, mid, '
                       'is_close, source_kind) VALUES (%s,%s,%s,%s,%s,%s)',
                       (_entry_id_for(owner, ids['rec-a1']), 'rec-close', at(9),
                        Decimal('0.61'), True, 'clob_terminal_price'))

    evaluation_rows = (
        ev.EvaluationCandidate(
            candidate_id='rec-a1', model_key=model_key, market_type='terminal',
            gate_version='g1', market_key=market_a, model_probability=0.62,
            market_mid=0.50, accepted=True, stake=250.0, fill_price=0.56, side='YES',
            fee_category='crypto', outcome='YES', recorded_pnl=196.428571,
            decision_time=at(4), settled_time=at(70), close_price=0.61),
        ev.EvaluationCandidate(
            candidate_id='rec-a2', model_key=model_key, market_type='terminal',
            gate_version='g1', market_key=market_a, model_probability=0.634,
            market_mid=0.51, accepted=True, stake=150.0, fill_price=0.40, side='NO',
            fee_category='crypto', outcome='YES', recorded_pnl=-150.0,
            decision_time=at(5), settled_time=at(71)),
        ev.EvaluationCandidate(
            candidate_id='rec-b', model_key=model_key, market_type='terminal',
            gate_version='g1', market_key=market_b, model_probability=0.55,
            market_mid=0.52, accepted=True, stake=100.0, fill_price=0.50, side='YES',
            fee_category='crypto', outcome='EXITED', recorded_pnl=-8.0,
            decision_time=at(4), settled_time=at(72)),
        ev.EvaluationCandidate(
            candidate_id='rec-e', model_key=model_key, market_type='terminal',
            gate_version='g1', market_key=market_e, model_probability=0.71,
            market_mid=0.55, accepted=False, side='NO', outcome='NO',
            decision_time=at(4)),
    )
    report = ev.evaluate_queues(evaluation_rows)
    assert len(report.queues) == 1
    evaluated = report.queues[0]

    queue = _queue_row(app, 'rec-model')
    counts = evaluated.counts
    assert queue['candidates_total'] == counts.candidates == 4
    assert queue['candidates_accepted'] == counts.accepted == 3
    assert (queue['candidates_rejected'] + queue['candidates_construction_failed']
            == counts.rejected == 1)
    assert queue['entered'] == counts.entered == 3
    assert queue['settled_natural'] + queue['settled_exit'] == counts.settled == 3
    assert queue['entries_unsettled'] == counts.unresolved == 0
    assert queue['settled_voided'] == counts.voided == 0
    fills = evaluated.fills
    assert queue['settled_natural'] == fills.natural_settled == 2
    assert queue['natural_hits'] == fills.hits == 1
    assert abs(float(queue['natural_hit_rate']) - fills.hit_rate) < 1e-9
    roi = evaluated.roi
    assert abs(float(queue['stake_sum']) - roi.stake) < 0.005
    assert abs(float(queue['gross_pnl_sum']) - roi.gross_pnl) < 0.005
    assert abs(float(queue['entry_fee_sum']) - roi.entry_fees) < 0.005
    assert abs(float(queue['exit_fee_sum']) - roi.exit_fees) < 0.005
    assert abs(float(queue['fee_total_sum'])
               - (roi.entry_fees + roi.exit_fees)) < 0.005
    assert abs(float(queue['net_pnl_sum']) - roi.net_pnl) < 0.005
    assert abs(float(queue['gross_roi']) - roi.gross_roi) < 1e-9
    assert abs(float(queue['net_roi']) - roi.net_roi) < 1e-9
    concentration = evaluated.concentration
    assert queue['top_market_key'] == concentration.top_market_key == market_a
    assert abs(float(queue['top_market_pnl'])
               - concentration.top_market_pnl) < 0.005
    assert abs(float(queue['top_market_share'])
               - concentration.top_market_share) < 1e-9
    assert queue['top_market_net_key'] == concentration.top_market_net_key == market_a
    assert abs(float(queue['top_market_net_pnl'])
               - concentration.top_market_net_pnl) < 0.005
    assert abs(float(queue['top_market_net_share'])
               - concentration.top_market_net_share) < 1e-9
    drawdown = evaluated.drawdown
    assert abs(float(queue['max_drawdown_net']) - drawdown.max_drawdown) < 0.005
    assert queue['drawdown_rows'] == drawdown.series_rows == 3
    scoring = evaluated.scoring
    assert queue['markets_total'] == scoring.markets_total == 3
    assert queue['markets_scored'] == scoring.markets_scored == 2
    assert queue['markets_missing_outcome'] == scoring.missing_outcome == 1
    assert queue['markets_missing_model_probability'] == scoring.missing_model_probability == 0
    assert queue['markets_missing_market_mid'] == scoring.missing_market_mid == 0
    assert queue['deduped_repeats'] == scoring.deduped_repeats == 1
    assert abs(float(queue['model_brier_mean']) - scoring.model_brier) < 1e-12
    assert abs(float(queue['market_brier_mean']) - scoring.market_brier) < 1e-12
    assert abs(float(queue['brier_diff_mean']) - scoring.brier_diff_mean) < 1e-12
    assert abs(float(queue['logloss_diff_mean'])
               - scoring.logloss_diff_mean) < 1e-12
    assert queue['infinite_log_loss_pairs'] == scoring.infinite_log_loss_pairs == 0
    clv = evaluated.clv
    assert queue['clv_entries_eligible'] == clv.entries_eligible == 3
    assert queue['clv_entries_with_close_mark'] == 1
    assert queue['clv_entries_with_close_mid'] == clv.entries_with_close == 1
    assert abs(float(queue['clv_mean']) - clv.mean) < 1e-9

    # Same-basis ledger reconciliation: the queue row equals independent
    # SQL sums over the recorded book (entries and settlements).
    ledger = _rows(
        app,
        'SELECT (SELECT coalesce(sum(stake), 0) FROM papersim.entries) AS stake_sum, '
        '(SELECT coalesce(sum(entry_fee), 0) FROM papersim.entries) AS entry_fee_sum, '
        '(SELECT coalesce(sum(pnl_gross), 0) FROM papersim.settlements) AS gross_sum, '
        '(SELECT coalesce(sum(fee_total), 0) FROM papersim.settlements) AS fee_sum, '
        '(SELECT coalesce(sum(pnl_net), 0) FROM papersim.settlements) AS net_sum')[0]
    assert queue['stake_sum'] == ledger['stake_sum']
    assert queue['entry_fee_sum'] == ledger['entry_fee_sum']
    assert queue['gross_pnl_sum'] == ledger['gross_sum']
    assert queue['fee_total_sum'] == ledger['fee_sum']
    assert queue['net_pnl_sum'] == ledger['net_sum']

    scored = {item['candidate_key']: item for item in _rows(
        app, 'SELECT candidate_key, market_mid_side_aligned, model_minus_market, '
             'brier_pair_diff FROM papersim.candidate_scoring_view')}
    assert scored['rec-a2']['market_mid_side_aligned'] == Decimal('0.51')  # mirrored
    assert scored['rec-a2']['model_minus_market'] == Decimal('0.124')
    assert scored['rec-a2']['brier_pair_diff'] == Decimal('0.141856')
    owner.close()
    app.close()
