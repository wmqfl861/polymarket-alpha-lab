"""Opt-in native B2 capture-marks end-to-end proofs (05 section 2 / FX-34).

Runs only inside the disposable native cluster environment (the CI
throwaway papersim partition or an explicitly assembled local cluster).
Never touches a business cluster: every test builds its own private
managed-shaped database. The mark source is the prepared pack (JSON
bytes), so no network is touched.

Proves the full B2 closed loop on a real database: the clean-worktree
commit check feeding every receipt (FX-03), raw pack bytes registered in
``papersim.snapshots`` BEFORE normalization, marks for ALL candidates
(accepted, rejected and construction-failed -- no entry dependency), the
FX-34 classification against the stored ``event_start_at`` (a CLOSE
strictly before the start; at-start, overtime-shaped and NULL-start
observations stored PERIODIC or refused at the writer), the SUCCEEDED
receipt carrying the stable coverage summary, retry idempotency (same
pack re-run inserts zero rows and returns an identical summary), the
conflict boundaries (same key + changed content, at-most-one-CLOSE per
candidate) and the explicit zero/missing counts of an empty or
quote-less pack.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from polymarket_alpha_lab.papersim import capture, migrations as pm, store

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN,
                            reason='explicit native papersim proof is opt-in')

T0 = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.UTC)
START = T0 + datetime.timedelta(hours=2)  # the candidates' event_start_at


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
    name = 'pal_papersim_b2cap_' + uuid.uuid4().hex[:10]
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
    assert pm.apply_pending_migrations(owner, ROOT) == {
        'applied': len(pm.papersim_migration_catalog(ROOT)),
        'pending_before': len(pm.papersim_migration_catalog(ROOT))}
    owner.commit()
    owner.close()
    return owner_dsn.replace('user=pal_owner', 'user=pal_app')


@pytest.fixture
def app():
    from polymarket_alpha_lab.papersim import store_psycopg
    connection = store_psycopg.connect_explicit(_fresh_managed_database(),
                                                label='PAPERSIM_MIGRATIONS_PG_DSN')
    connection.autocommit = True
    yield connection
    connection.close()


@pytest.fixture
def repo(tmp_path):
    """A committed clean git repository (the FX-03 verified-commit source)."""
    root = tmp_path / 'repo'
    root.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "T"]):
        subprocess.run(["git", "-C", str(root), *args], check=True,
                       capture_output=True, text=True)
    (root / "seed.txt").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "seed.txt"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed"], check=True,
                   capture_output=True, text=True)
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()
    return root, head


# ---------------------------------------------------------------------------
# Seeding: one B1-shaped scan with the FX-26 columns already in place
# ---------------------------------------------------------------------------

SCAN_OPERATION_KEY = 'run-scan|pal-b2|seed'


def _decision_evidence(slug, observed_at):
    return store.PriceEvidenceObservation(
        observed_at=observed_at, evidence_role='DECISION', market_ref_kind='market_slug',
        market_ref=slug, side='YES', mid=0.5, source_kind='gamma_close', source_ref='r1')


def _entry():
    return store.EntrySubmission(
        stake=250, price=0.56, shares=446.4286, fee_category='crypto',
        fee_rate=0.07, entry_fee=10.08, fee_source='schedule', tier='standard',
        entered_at=T0 + datetime.timedelta(minutes=1))


def _candidate(key, slug, *, conclusion='REJECTED', event_start_at=START,
               with_entry=False):
    return store.CandidateSubmission(
        candidate_key=key, conclusion=conclusion, side='YES', market_kind='terminal',
        team='pal-b2', model_name='crypto', model_version='r19', input_payload='{}',
        gate_report='{}', evaluated_at=T0 - datetime.timedelta(minutes=1),
        condition_id=None, market_id=None, market_slug=slug, yes_token_id=None,
        no_token_id=None, market_type='terminal', event_start_at=event_start_at,
        binding_version=1, median_probability=0.44, variant_quotes='[]',
        snapshot_refs='[]',
        rejection_reasons='[]' if conclusion == 'ACCEPTED' else '["edge"]',
        order_book=None, book_fetched_at=None, legacy_source_id=None, is_legacy=False,
        price_evidence=(_decision_evidence(slug or key,
                                           T0 - datetime.timedelta(minutes=1)),),
        entry=_entry() if with_entry else None)


def _seed_scan(app):
    """One scan with three candidates: accepted+entered, rejected,
    construction-failed (no market identity, NULL event start)."""
    from polymarket_alpha_lab.papersim import store_psycopg
    result = store_psycopg.record_scan(app, store.ScanSubmission(
        scan=store.ScanRunRecord(
            team='pal-b2', started_at=T0 - datetime.timedelta(minutes=2),
            finished_at=T0 - datetime.timedelta(minutes=1), code_commit='c' * 40,
            schema_version=1, gate_version='g1', operator='unattended',
            params='{"seed": "%s"}' % uuid.uuid4().hex[:8],
            operation_key=SCAN_OPERATION_KEY),
        candidates=(
            _candidate('acc-yes', 'm-acc', conclusion='ACCEPTED', with_entry=True),
            _candidate('rej-yes', 'm-rej', conclusion='REJECTED'),
            _candidate('cons-none', None, conclusion='CONSTRUCTION_FAILED',
                       event_start_at=None),
        )))
    return result['candidate_ids']


def _pack(*observations):
    return capture.MarkPack(name='marks-%s.json' % uuid.uuid4().hex[:6],
                            content=json.dumps(list(observations)).encode('utf-8'))


def _observation(candidate_key, *, sampling_kind='PERIODIC', observed_at,
                 market_ref='m-acc', by_operation_key=True, prices=True, **extra):
    document = {
        'candidate_key': candidate_key,
        'sampling_kind': sampling_kind,
        'observed_at': observed_at.isoformat(),
        'market_ref_kind': 'market_slug', 'market_ref': market_ref,
        'side': 'YES', 'source_kind': 'clob_book',
        'source_ref': extra.pop('source_ref', 'book-%s' % uuid.uuid4().hex[:6]),
    }
    if by_operation_key:
        document['scan_operation_key'] = SCAN_OPERATION_KEY
    else:
        document['scan_id'] = 1
    if prices:
        document.update({'bid': 0.55, 'ask': 0.58, 'mid': 0.565})
    document.update(extra)
    return document


def _run(app, repo_root, packs, *, operation_key='capture-marks|pal-b2|run',
         attempt=1):
    return capture.run_capture(
        capture.CaptureConfig(team='pal-b2'), packs, conn=app,
        repo_root=repo_root, now_utc=lambda: T0,
        operation_key=operation_key, attempt=attempt)


def _summary_evidence(summary) -> str:
    return json.dumps(summary, sort_keys=True, separators=(',', ':'))


# ---------------------------------------------------------------------------
# The full closed loop
# ---------------------------------------------------------------------------

@NATIVE
def test_full_capture_writes_marks_close_evidence_and_receipts(app, repo):
    repo_root, head = repo
    ids = _seed_scan(app)
    pack = _pack(
        # accepted candidate: a periodic mark, the one legal pre-start
        # CLOSE, and a requested CLOSE exactly AT the event start.
        _observation('acc-yes', observed_at=T0 + datetime.timedelta(minutes=10)),
        _observation('acc-yes', sampling_kind='CLOSE', prices=False, last=0.98,
                     observed_at=T0 + datetime.timedelta(minutes=30)),
        _observation('acc-yes', sampling_kind='CLOSE', observed_at=START),
        # rejected candidate: an overtime-shaped CLOSE (hours after the
        # start) and a quote-less observation -- both accounted safely.
        _observation('rej-yes', sampling_kind='CLOSE', market_ref='m-rej',
                     observed_at=START + datetime.timedelta(hours=3),
                     by_operation_key=False),
        _observation('rej-yes', market_ref='m-rej', prices=False,
                     observed_at=T0 + datetime.timedelta(minutes=11),
                     by_operation_key=False),
        # construction-failed candidate (NULL event start): requested
        # CLOSE downgraded to PERIODIC; marks have no entry dependency.
        _observation('cons-none', sampling_kind='CLOSE', market_ref='cons-key',
                     observed_at=T0 + datetime.timedelta(minutes=30)),
    )
    summary = _run(app, repo_root, (pack,))

    assert summary['candidates_seen'] == 3
    assert summary['observations'] == 6
    assert summary['marks_written'] == 5  # the quote-less one is counted, not written
    assert summary['close_eligible'] == 1
    assert summary['close_evidence_written'] == 1
    assert summary['downgraded_to_periodic'] == 3
    assert summary['downgrade_reasons'] == {
        'missing_event_start': 1, 'observed_at_after_event_start': 2}
    assert summary['missing_event_start'] == 1
    assert summary['missing_quote'] == 1
    assert summary['source_kind_counts'] == {'clob_book': 6}
    assert summary['code_commit'] == head
    assert 'close marks over ALL entries' in summary['coverage_terms']

    digest = hashlib.sha256(pack.content).hexdigest()
    assert summary['packs'] == [{'pack': pack.name, 'snapshot_sha256': digest}]

    # Marks: five rows over three candidates; sampling kinds per FX-34.
    rows = app.execute('SELECT candidate_id, sampling_kind, snapshot_sha256 '
                       'FROM papersim.candidate_marks ORDER BY candidate_mark_id').fetchall()
    assert len(rows) == 5
    assert all(row[2] == digest for row in rows)  # every row cites the pack digest
    acc = [row[1] for row in rows if row[0] == ids['acc-yes']]
    assert acc == ['PERIODIC', 'CLOSE', 'PERIODIC']
    rej = [row[1] for row in rows if row[0] == ids['rej-yes']]
    assert rej == ['PERIODIC']  # overtime CLOSE downgraded; quote-less left no row
    cons = [row[1] for row in rows if row[0] == ids['cons-none']]
    assert cons == ['PERIODIC']  # NULL start: no CLOSE, the mark is kept

    # The single CLOSE price evidence row: strictly pre-start, cited.
    close = app.execute("SELECT last, snapshot_sha256 FROM "
                        "papersim.candidate_price_evidence "
                        "WHERE candidate_id = %s AND evidence_role = 'CLOSE'",
                        (ids['acc-yes'],)).fetchall()
    assert [(row[0], row[1]) for row in close] == [(Decimal('0.98'), digest)]

    # Raw pack bytes registered BEFORE normalization; candidates untouched.
    snapshot = app.execute('SELECT kind, source FROM papersim.snapshots '
                           'WHERE content_sha256 = %s', (digest,)).fetchone()
    assert snapshot == ('capture_mark_pack', pack.name)
    assert app.execute('SELECT count(*) FROM papersim.candidates').fetchone()[0] == 3
    # B2 writes candidate marks only; the entry-level marks face is B3's.
    assert app.execute('SELECT count(*) FROM papersim.marks').fetchone()[0] == 0

    # Receipts: STARTED/SUCCEEDED, command capture-marks, verified commit,
    # the SUCCEEDED evidence carries the stable summary verbatim.
    receipts = app.execute('SELECT phase, command, code_commit, error_code, '
                           'output_evidence FROM papersim.command_run_events '
                           'ORDER BY run_event_id').fetchall()
    assert [(row[0], row[1], row[2], row[3]) for row in receipts] == [
        ('STARTED', 'capture-marks', head, None),
        ('SUCCEEDED', 'capture-marks', head, None)]
    assert receipts[1][4] == [_summary_evidence(summary)]


@NATIVE
def test_same_pack_replay_is_idempotent_and_returns_the_identical_summary(app, repo):
    repo_root, _head = repo
    _seed_scan(app)
    pack = _pack(
        _observation('acc-yes', observed_at=T0 + datetime.timedelta(minutes=10)),
        _observation('acc-yes', sampling_kind='CLOSE', prices=False, last=0.97,
                     observed_at=T0 + datetime.timedelta(minutes=30)),
        _observation('rej-yes', market_ref='m-rej',
                     observed_at=T0 + datetime.timedelta(minutes=12)),
    )
    first = _run(app, repo_root, (pack,), operation_key='capture-marks|pal-b2|replay')
    second = _run(app, repo_root, (pack,), operation_key='capture-marks|pal-b2|replay')
    assert second == first  # identical summary, zero content drift
    assert app.execute('SELECT count(*) FROM papersim.candidate_marks').fetchone()[0] == 3
    assert app.execute("SELECT count(*) FROM papersim.candidate_price_evidence "
                       "WHERE evidence_role = 'CLOSE'").fetchone()[0] == 1
    assert app.execute('SELECT count(*) FROM papersim.command_run_events'
                       ).fetchone()[0] == 2  # re-run receipts idempotent (no new rows)
    # A later ATTEMPT of the same operation appends its own receipt pair,
    # keyed operation|attempt|phase, and still returns the same summary.
    third = _run(app, repo_root, (pack,), operation_key='capture-marks|pal-b2|replay',
                 attempt=2)
    assert third == first
    assert app.execute('SELECT count(*) FROM papersim.command_run_events'
                       ).fetchone()[0] == 4


@NATIVE
def test_changed_content_and_second_close_are_conflicts_with_failed_receipts(app, repo):
    repo_root, _head = repo
    ids = _seed_scan(app)
    moment = T0 + datetime.timedelta(minutes=10)
    first = _run(app, repo_root, (_pack(
        _observation('acc-yes', observed_at=moment, source_ref='stable'),
        _observation('acc-yes', sampling_kind='CLOSE', prices=False, last=0.97,
                     source_ref='stable-close',
                     observed_at=T0 + datetime.timedelta(minutes=30)),
    ),), operation_key='capture-marks|pal-b2|conflict')
    assert first['marks_written'] == 2
    assert first['close_eligible'] == 1

    # Same per-mark key (same second), changed content: a conflict, never
    # an update -- the run fails loudly and appends a FAILED receipt.
    with pytest.raises(store.PapersimStoreError,
                       match='papersim_candidate_mark_conflict'):
        _run(app, repo_root, (_pack(
            _observation('acc-yes', observed_at=moment, source_ref='stable',
                         mid=0.7),
        ),), operation_key='capture-marks|pal-b2|conflict')

    # A second CLOSE for the same candidate (a NEW pre-start observation)
    # is refused by the one-close invariant even under retries. Receipts
    # are keyed operation|attempt|phase, so this failed re-run is attempt
    # 2 of the same operation (a retry may never mint a fresh identity,
    # and one attempt holds at most one FAILED receipt).
    with pytest.raises(store.PapersimStoreError,
                       match='papersim_candidate_close_conflict'):
        _run(app, repo_root, (_pack(
            _observation('acc-yes', sampling_kind='CLOSE', source_ref='late-close',
                         observed_at=T0 + datetime.timedelta(minutes=29)),
        ),), operation_key='capture-marks|pal-b2|conflict', attempt=2)
    codes = [row[0] for row in app.execute(
        "SELECT error_code FROM papersim.command_run_events "
        "WHERE phase = 'FAILED' ORDER BY run_event_id").fetchall()]
    assert codes == ['papersim_candidate_mark_conflict',
                     'papersim_candidate_close_conflict']
    # Nothing of the failed attempts survived.
    assert app.execute('SELECT count(*) FROM papersim.candidate_marks '
                       'WHERE candidate_id = %s', (ids['acc-yes'],)).fetchone()[0] == 2


@NATIVE
def test_empty_pack_completes_with_explicit_zero_counts(app, repo):
    repo_root, head = repo
    summary = _run(app, repo_root, (capture.MarkPack(name='empty.json',
                                                     content=b'[]'),))
    assert summary['candidates_seen'] == 0
    assert summary['observations'] == 0
    assert summary['marks_written'] == 0
    assert summary['close_eligible'] == 0
    assert summary['close_evidence_written'] == 0
    assert summary['downgraded_to_periodic'] == 0
    assert summary['missing_event_start'] == 0
    assert summary['missing_quote'] == 0
    assert summary['code_commit'] == head
    assert app.execute('SELECT count(*) FROM papersim.candidate_marks'
                       ).fetchone()[0] == 0
    receipts = app.execute('SELECT phase FROM papersim.command_run_events '
                           'ORDER BY run_event_id').fetchall()
    assert [row[0] for row in receipts] == ['STARTED', 'SUCCEEDED']


@NATIVE
def test_dirty_worktree_refuses_before_any_receipt(app, repo):
    repo_root, _head = repo
    (repo_root / 'dirt.txt').write_text('dirt', encoding='utf-8')
    from polymarket_alpha_lab.papersim.scan import ScanError
    with pytest.raises(ScanError) as error:
        _run(app, repo_root, ())
    assert error.value.code == 'papersim_scan_worktree_dirty'
    assert app.execute('SELECT count(*) FROM papersim.command_run_events'
                       ).fetchone()[0] == 0


@NATIVE
def test_unknown_candidate_fails_loudly_with_a_failed_receipt(app, repo):
    repo_root, _head = repo
    _seed_scan(app)
    with pytest.raises(capture.CaptureError,
                       match='papersim_capture_candidate_missing'):
        _run(app, repo_root, (_pack(
            _observation('ghost-key', observed_at=T0 + datetime.timedelta(minutes=10)),
        ),), operation_key='capture-marks|pal-b2|ghost')
    failed = app.execute("SELECT error_code FROM papersim.command_run_events "
                         "WHERE phase = 'FAILED'").fetchall()
    assert failed == [('papersim_capture_candidate_missing',)]
    assert app.execute('SELECT count(*) FROM papersim.candidate_marks'
                       ).fetchone()[0] == 0
