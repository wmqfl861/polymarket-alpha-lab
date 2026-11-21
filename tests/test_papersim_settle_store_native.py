"""Opt-in native B3 settle end-to-end proofs (plan 05 section 2 / 80.8).

Runs only inside the disposable native cluster environment (the CI
throwaway papersim partition or an explicitly assembled local throwaway
cluster).  Never touches a business cluster: every test builds its own
private managed-shaped database.  No network is touched -- the captured
outcome evidence is seeded through the audited
``store_psycopg.record_market_outcome`` and the entries through
``record_scan``.

Proves the full B3 settlement loop on a real database: the clean
worktree commit check feeding every receipt (FX-03), the FX-29
consistent-snapshot open-entries loader (ONE repeatable-read read-only
transaction), the per-entry settlement write (settlement row + exactly
one cash leg through ``store_psycopg.settlement_record``), identical
re-run idempotency (inserted=False, unchanged row counts, idempotent
receipts), the late-capture causality (a previously pending entry
settles on the run after the capture lands) and the VOIDED stake
refund.

The snapshot loader exercised everywhere is the PRODUCTION
``store_psycopg.open_entries_for_settlement`` (promoted verbatim from
the B3 unique-file pass; the CLI passes exactly this callable as the
``open_entries`` loader of ``settle.run_settle``).

FX-29 two-reader/one-writer proofs (fix-list 03 section 2, FX-29
acceptance): a writer connection commits an entry + cash event BETWEEN
the statements of the production readers -- ``bankroll_daily_check``,
the evaluate loader (``cli._evaluation_candidates_from_store``) and the
settle snapshot loader -- and every mid-flight reader returns the exact
PRE-write state while a fresh reader afterwards sees the write.  (The
writer binds the database OWNER role: the choreography's ACCESS
EXCLUSIVE lock -- the only lock mode that blocks a plain SELECT --
requires UPDATE privilege, which the least-privilege production app
role deliberately lacks (the papersim migrations grant it SELECT +
INSERT only); the readers under proof stay on the production app
face.)  The
interleaving is real, not simulated: the writer's transaction takes an
ACCESS EXCLUSIVE lock on a table the reader's LATER statement reads
(``settlements`` / ``candidate_price_evidence`` / ``market_outcomes``
respectively) while the production write (``record_scan`` /
``record_market_outcome``) stays uncommitted inside the same still-open
transaction (a savepoint of it); the test waits until the reader
backend is actually blocked on that lock, then commits the writer, so
the commit provably lands between the reader's statements.  Under the
previous per-statement default isolation the same interleaving would
mix snapshots (the mid-flight reader would see the write); under
REPEATABLE READ it cannot.
"""

from __future__ import annotations

import datetime
from dataclasses import asdict, replace
from decimal import Decimal, localcontext
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

import pytest

from polymarket_alpha_lab.papersim import migrations as pm
from polymarket_alpha_lab.papersim import settle, store
from tests.test_papersim_capture_marks_native import (
    _D1_NATIVE,
    _d1_child,
    _d1_register_binding,
    d1_capture_project,
)

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN,
                            reason='explicit native papersim proof is opt-in')


def at(minutes: int) -> datetime.datetime:
    return datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.UTC) \
        + datetime.timedelta(minutes=minutes)


def _connect(dsn, *, autocommit=True):
    from polymarket_alpha_lab.papersim import store_psycopg
    connection = store_psycopg.connect_explicit(
        dsn, label=store_psycopg.EXPLICIT_DSN_LABEL)
    connection.autocommit = autocommit
    return connection


def _rebind_database(dsn: str, database: str) -> str:
    return ' '.join(f'dbname={database}' if token.startswith('dbname=') else token
                    for token in dsn.split(' '))


def _fresh_managed_database() -> tuple[str, str]:
    """A private migrated database inside the throwaway cluster.

    Returns ``(owner_dsn, app_dsn)``: the OWNER binding is the test
    infrastructure / FX-29 choreography-writer face, the APP binding is
    the production runtime role every proved reader connects as."""
    admin = _connect(OWNER_DSN)
    name = 'pal_papersim_b3settle_' + uuid.uuid4().hex[:10]
    with admin.cursor() as cursor:
        cursor.execute("SELECT system_identifier::text FROM pg_control_system()")
        system_id = cursor.fetchone()[0]
        cursor.execute(f"CREATE DATABASE {name} OWNER pal_owner")
    admin.close()
    owner_dsn = _rebind_database(OWNER_DSN, name)
    bootstrap = _connect(owner_dsn, autocommit=True)
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
    return owner_dsn, owner_dsn.replace('user=pal_owner', 'user=pal_app')


@pytest.fixture
def app():
    from polymarket_alpha_lab.papersim import store_psycopg
    connection = store_psycopg.connect_explicit(_fresh_managed_database()[1],
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
# Seeding: one scan with an accepted entry (the frozen m2-store shapes)
# ---------------------------------------------------------------------------

def _scan_run():
    return store.ScanRunRecord(team='crypto_eth', started_at=at(0), finished_at=at(6),
                               code_commit='c' * 40, schema_version=1, gate_version='g1',
                               operator='unattended',
                               params='{"run": "%s"}' % uuid.uuid4().hex[:8])


def _evidence(slug: str):
    return store.PriceEvidenceObservation(
        observed_at=at(5), evidence_role='DECISION', model_probability=None,
        market_ref_kind='market_slug', market_ref=slug, token_ref=None, side='YES',
        bid=None, ask=None, mid=None, last=None, book_sha256=None,
        snapshot_sha256=None, source_kind='gamma_close', source_ref='r1')


def _entry_submission():
    return store.EntrySubmission(
        stake=Decimal('250'), price=Decimal('0.56'), shares=Decimal('446.4286'),
        fee_category='crypto', fee_rate=Decimal('0.07'), entry_fee=Decimal('7.70'),
        fee_source='schedule', tier='standard', entered_at=at(6),
        legacy_source_id=None, is_legacy=False)


def _candidate(slug: str):
    return store.CandidateSubmission(
        candidate_key='k-' + slug, conclusion='ACCEPTED', side='YES',
        market_kind='terminal', team='crypto_eth', model_name='gbm',
        model_version='r19', input_payload='{}', gate_report='{}', evaluated_at=at(5),
        condition_id=None, market_id=None, market_slug=slug,
        yes_token_id=None, no_token_id=None, market_type='terminal',
        binding_version=1, median_probability=Decimal('0.44'),
        variant_quotes='[]', snapshot_refs='[]', rejection_reasons='[]',
        order_book=None, book_fetched_at=None, legacy_source_id=None,
        is_legacy=False, price_evidence=(_evidence(slug),),
        entry=_entry_submission())


def _seed_accepted_entry(store_psycopg, app):
    """One scan with one accepted crypto entry; returns (candidate_id, entry_id, slug)."""
    slug = 'b3-' + uuid.uuid4().hex[:10]
    result = store_psycopg.record_scan(app, store.ScanSubmission(
        scan=_scan_run(),
        candidates=(_candidate(slug),)))
    candidate_id = result['candidate_ids']['k-' + slug]
    with app.cursor() as cursor:
        cursor.execute('SELECT entry_id FROM papersim.entries WHERE candidate_id = %s',
                       (candidate_id,))
        entry_id = cursor.fetchone()[0]
    return candidate_id, entry_id, slug


def _outcome_observation(slug: str, outcome: str):
    return store.MarketOutcomeObservation(
        market_slug=slug, outcome=outcome, outcome_detail=None,
        confirmed_at=at(80), observed_at=at(60), source_kind='gamma_close',
        source_ref='close-' + slug, source_snapshot_sha256=None)


# ---------------------------------------------------------------------------
# The FX-29 snapshot loader: the PRODUCTION store_psycopg helper (promoted
# verbatim by the B3 shared-file pass; the CLI passes exactly this
# callable), exercised end-to-end by every settle run below.
# ---------------------------------------------------------------------------


def _run(conn, repo_root, *, operation_key, moment=None, attempt=1):
    from polymarket_alpha_lab.papersim import store_psycopg
    return settle.run_settle(
        'crypto_eth', conn=conn, repo_root=repo_root,
        now_utc=lambda: moment or at(120),
        open_entries=store_psycopg.open_entries_for_settlement,
        operation_key=operation_key,
        attempt=attempt,
    )


# ---------------------------------------------------------------------------
# The proofs
# ---------------------------------------------------------------------------

@NATIVE
def test_settle_writes_settlement_and_exactly_one_cash_leg(app, repo):
    repo_root, head = repo
    from polymarket_alpha_lab.papersim import store_psycopg
    _candidate_id, entry_id, slug = _seed_accepted_entry(store_psycopg, app)
    outcome_id = store_psycopg.record_market_outcome(
        app, _outcome_observation(slug, 'YES'))['outcome_id']

    result = _run(app, repo_root, operation_key='settle|crypto_eth|t1')

    assert result['code_commit'] == head
    assert result['settled_count'] == 1 and result['pending_count'] == 0
    written = result['settled'][0]
    assert written['entry_id'] == entry_id and written['inserted'] is True
    row = app.execute('SELECT classification, outcome_label, win_loss, proceeds, '
                      'pnl_gross, fee_total, pnl_net, source_outcome_id, confirmed_by, '
                      'confirmed_at FROM papersim.settlements '
                      'WHERE settlement_id = %s', (written['settlement_id'],)).fetchone()
    assert row[0] == 'NATURAL' and row[1] == 'YES' and row[2] == 'win'
    assert row[3] == pytest.approx(Decimal(250) / Decimal('0.56'))
    assert row[4] == pytest.approx(Decimal(250) / Decimal('0.56') - 250)
    assert row[5] == pytest.approx(Decimal('7.70'))  # 250 * 0.07 * (1 - 0.56)
    assert row[5] == Decimal('7.70')
    assert row[6] == row[4] - row[5]  # the exact decimal identity
    assert row[7] == outcome_id
    assert row[8] == 'unattended' and row[9] == at(80)  # evidence confirmed_at only

    legs = app.execute("SELECT event_key, amount, reason, entry_id FROM "
                       "papersim.bankroll_events WHERE entry_id = %s "
                       "AND reason = 'SETTLE_NATURAL'", (entry_id,)).fetchall()
    assert len(legs) == 1
    assert legs[0][0] == f'settle:{entry_id}'
    assert legs[0][1] == pytest.approx(Decimal(250) / Decimal('0.56'))

    receipts = app.execute('SELECT phase, code_commit, command, operation_key, '
                           'scan_id FROM papersim.command_run_events '
                           'ORDER BY run_event_id').fetchall()
    assert [(r[0], r[1], r[2], r[3], r[4]) for r in receipts] == [
        ('STARTED', head, 'settle', 'settle|crypto_eth|t1', None),
        ('SUCCEEDED', head, 'settle', 'settle|crypto_eth|t1', None)]


@NATIVE
def test_rerun_with_identical_content_is_a_safe_noop(app, repo):
    repo_root, _head = repo
    from polymarket_alpha_lab.papersim import store_psycopg
    _candidate_id, entry_id, slug = _seed_accepted_entry(store_psycopg, app)
    store_psycopg.record_market_outcome(app, _outcome_observation(slug, 'YES'))

    first = _run(app, repo_root, operation_key='settle|crypto_eth|t2')
    assert first['settled'][0]['inserted'] is True

    # The driver's write primitive is idempotent on identical content:
    # re-submitting the exact settlement record the run produced is a
    # no-op (inserted=False, same ids) and adds no rows anywhere.
    record = app.execute('SELECT classification, outcome_label, win_loss, exit_price, '
                         'proceeds, pnl_gross, fee_total, fee_source, pnl_net, '
                         'source_outcome_id, exit_mark_id, evidence, confirmed_by, '
                         'confirmed_at, is_legacy FROM papersim.settlements '
                         'WHERE entry_id = %s', (entry_id,)).fetchone()
    replay = store_psycopg.settlement_record(app, store.SettlementRecord(
        entry_id=entry_id, classification=record[0], outcome_label=record[1],
        win_loss=record[2], exit_price=record[3], proceeds=record[4],
        pnl_gross=record[5], fee_total=record[6], fee_source=record[7],
        pnl_net=record[8], source_outcome_id=record[9], exit_mark_id=record[10],
        evidence=record[11], confirmed_by=record[12], confirmed_at=record[13],
        is_legacy=record[14]))
    assert replay['inserted'] is False
    assert replay['settlement_id'] == first['settled'][0]['settlement_id']

    # The command-level re-run (a RETRY advances the attempt number; the
    # SUCCEEDED evidence describes what the run found, so the same
    # attempt may not be replayed against changed store state) finds no
    # open entries and writes nothing.
    second = settle.run_settle(
        'crypto_eth', conn=app, repo_root=repo_root, now_utc=lambda: at(180),
        open_entries=store_psycopg.open_entries_for_settlement,
        operation_key='settle|crypto_eth|t2', attempt=2)
    assert second['settled_count'] == 0 and second['pending_count'] == 0
    assert second['snapshot_entries'] == 0

    assert app.execute('SELECT count(*) FROM papersim.settlements').fetchone()[0] == 1
    assert app.execute('SELECT count(*) FROM papersim.bankroll_events '
                       'WHERE event_key = %s', (f'settle:{entry_id}',)).fetchone()[0] == 1
    # two receipt pairs: attempt 1 and attempt 2 of the same operation
    rows = app.execute('SELECT attempt, phase FROM papersim.command_run_events '
                       'ORDER BY run_event_id').fetchall()
    assert rows == [(1, 'STARTED'), (1, 'SUCCEEDED'), (2, 'STARTED'), (2, 'SUCCEEDED')]


@NATIVE
def test_late_capture_settles_a_previously_pending_entry(app, repo):
    repo_root, _head = repo
    from polymarket_alpha_lab.papersim import store_psycopg
    _candidate_id, entry_id, slug = _seed_accepted_entry(store_psycopg, app)

    # No captured outcome yet: the entry stays open, pending with reason.
    first = _run(app, repo_root, operation_key='settle|crypto_eth|t3a')
    assert first['settled_count'] == 0
    assert first['pending'] == [{'entry_id': entry_id,
                                 'reason': 'no_captured_outcome'}]
    assert app.execute('SELECT count(*) FROM papersim.settlements').fetchone()[0] == 0
    evidence = app.execute('SELECT output_evidence FROM papersim.command_run_events '
                           "WHERE phase = 'SUCCEEDED'").fetchone()[0]
    assert list(evidence) == [f'pending:{entry_id}:no_captured_outcome']

    # The capture lands (the B2 lane's write, seeded here through the
    # audited observation writer); the NEXT run settles the entry.
    store_psycopg.record_market_outcome(app, _outcome_observation(slug, 'YES'))
    second = _run(app, repo_root, operation_key='settle|crypto_eth|t3b')
    assert second['settled_count'] == 1 and second['pending_count'] == 0
    assert second['settled'][0]['entry_id'] == entry_id
    assert app.execute('SELECT count(*) FROM papersim.settlements').fetchone()[0] == 1
    assert app.execute('SELECT count(*) FROM papersim.bankroll_events '
                       'WHERE event_key = %s', (f'settle:{entry_id}',)).fetchone()[0] == 1


@NATIVE
def test_voided_outcome_refunds_the_stake(app, repo):
    repo_root, _head = repo
    from polymarket_alpha_lab.papersim import store_psycopg
    _candidate_id, entry_id, slug = _seed_accepted_entry(store_psycopg, app)
    store_psycopg.record_market_outcome(app, _outcome_observation(slug, 'VOIDED'))

    result = _run(app, repo_root, operation_key='settle|crypto_eth|t4')

    assert result['settled'][0]['classification'] == 'VOIDED_REFUND'
    row = app.execute('SELECT classification, win_loss, proceeds, pnl_gross, fee_total, '
                      'pnl_net FROM papersim.settlements WHERE entry_id = %s',
                      (entry_id,)).fetchone()
    assert row[0] == 'VOIDED_REFUND' and row[1] is None
    assert row[2] == Decimal('250')
    assert (row[3], row[4], row[5]) == (Decimal('0'), Decimal('0'), Decimal('0'))
    leg = app.execute('SELECT amount, reason FROM papersim.bankroll_events '
                      'WHERE event_key = %s', (f'settle:{entry_id}',)).fetchone()
    assert leg == (Decimal('257.70'), 'VOIDED_REFUND')
    assert app.execute(
        'SELECT event_key, amount, reason, entry_id, occurred_at FROM papersim.bankroll_events '
        'WHERE entry_id = %s ORDER BY event_id', (entry_id,)).fetchall() == [
        (f'entry:{entry_id}:cash', Decimal('-257.70'), 'SCAN_ENTRY', entry_id, at(6)),
        (f'settle:{entry_id}', Decimal('257.70'), 'VOIDED_REFUND', entry_id, at(80)),
    ]
    from polymarket_alpha_lab.papersim import cli, evaluation
    queue = evaluation.evaluate_queues(cli._evaluation_candidates_from_store(app)).queues[0]
    assert queue.counts.voided == 1
    assert queue.roi.counted == 0 and queue.fills.natural_settled == 0
    assert queue.fills.hit_rate is None


# ---------------------------------------------------------------------------
# FX-29 two-reader / one-writer proofs (fix-list 03 section 2 acceptance):
# a second connection commits an entry + cash event BETWEEN the statements
# of the production readers; each mid-flight reader must return exactly
# the PRE-write state and a fresh reader afterwards must see the write.
#
# The interleaving is choreographed, never manufactured: the writer's
# still-open transaction holds an ACCESS EXCLUSIVE lock on a table the
# reader's LATER statement reads while the production write
# (record_scan / record_market_outcome) sits uncommitted inside it; the
# test waits until the reader backend is really blocked on that lock,
# THEN commits, so the commit provably lands between the statements.
# ---------------------------------------------------------------------------

def _wait_reader_blocked(admin, reader_pid: int, *, blocked_relation_oid: int,
                         deadline_s: float = 30.0) -> None:
    """Poll until the reader backend is actually waiting on the writer lock.

    The block is observed through ``pg_locks`` -- an UNGRANTED relation
    lock request by the reader backend for the exact relation the writer
    locked -- NOT through ``pg_stat_activity.wait_event_type``:
    PostgreSQL masks another role's wait_event/wait_event_type/state
    columns unless the observer holds ``pg_read_all_stats``, and the
    reader under proof must run as the least-privilege production
    ``pal_app`` role (the core papersim migration grants ``pal_app``
    SELECT and INSERT on the application tables).  ``pg_locks``
    carries no query text and is not role-masked, and the
    relation-pinned ungranted request is stronger evidence than a bare
    wait-event label: the backend is provably queued on THIS table's
    ACCESS EXCLUSIVE lock."""
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        with admin.cursor() as cursor:
            cursor.execute('SELECT 1 FROM pg_locks WHERE pid = %s '
                           'AND relation = %s AND NOT granted',
                           (reader_pid, blocked_relation_oid))
            if cursor.fetchone() is not None:
                return
        time.sleep(0.02)
    raise AssertionError('the reader never blocked on the writer lock '
                         '(choreography failed; no snapshot was proven)')


def _two_connections():
    """Reader (autocommit) + writer (default explicit transaction) on one
    fresh private database, plus the cluster-admin poller.

    The READER is the production app face (pal_app through the audited
    connect_explicit -- exactly the managed runtime role).  The WRITER
    binds the database OWNER role: its ACCESS EXCLUSIVE lock -- the
    only lock mode that can block the reader's plain SELECT -- requires
    UPDATE privilege, which the least-privilege app role deliberately
    lacks; the writes themselves still go through the audited
    ``store_psycopg`` functions, so the committed rows and constraints
    are exactly the production ones."""
    from polymarket_alpha_lab.papersim import store_psycopg
    owner_dsn, app_dsn = _fresh_managed_database()
    reader = store_psycopg.connect_explicit(app_dsn, label='PAPERSIM_MIGRATIONS_PG_DSN')
    reader.autocommit = True
    writer = store_psycopg.connect_explicit(owner_dsn, label='PAPERSIM_MIGRATIONS_PG_DSN')
    admin = _connect(OWNER_DSN)
    return reader, writer, admin


def _midflight_reader(callable_):
    """Run the production reader on its own thread; return the result box."""
    box: dict = {}

    def _call():
        try:
            box['result'] = callable_()
        except BaseException as error:  # re-raised by the caller's assert
            box['error'] = error

    thread = threading.Thread(target=_call)
    thread.start()
    return box, thread


@NATIVE
def test_fx29_bankroll_check_reads_one_snapshot_across_a_midflight_commit():
    from polymarket_alpha_lab.papersim import store_psycopg
    reader, writer, admin = _two_connections()
    try:
        _candidate_id, _entry_id, _slug = _seed_accepted_entry(store_psycopg, reader)
        request = store.BankrollCheckRequest(as_of=at(300), code_commit='c' * 40)

        # Reader #1 (pre-write): the seeded entry is open, identity holds.
        pre = store_psycopg.bankroll_daily_check(reader, request)
        assert pre.passed is True and pre.inserted is True

        # The writer blocks the check's entries/settlements statement while
        # its production scan write (entry + cash leg) stays uncommitted.
        with writer.cursor() as cursor:
            cursor.execute('LOCK TABLE papersim.settlements IN ACCESS EXCLUSIVE MODE')
            cursor.execute("SELECT 'papersim.settlements'::regclass::oid")
            blocked_oid = cursor.fetchone()[0]
        store_psycopg.record_scan(writer, store.ScanSubmission(
            scan=_scan_run(), candidates=(_candidate('fx29a-' + uuid.uuid4().hex[:10]),)))

        box, thread = _midflight_reader(
            lambda: store_psycopg.bankroll_daily_check(reader, request))
        try:
            _wait_reader_blocked(admin, reader.info.backend_pid,
                                 blocked_relation_oid=blocked_oid)
            writer.commit()  # the entry + cash event land mid-read
        finally:
            writer.close()
            thread.join(timeout=60)
        assert not thread.is_alive() and 'error' not in box, box.get('error', '')
        mid = box['result']

        # The mid-flight reader saw EXACTLY the pre-write state (one
        # repeatable-read snapshot: no mixed sums) ...
        assert mid.event_watermark == pre.event_watermark
        assert mid.cash_total == pre.cash_total
        assert mid.deployed_total == pre.deployed_total
        assert mid.initial_and_adjustments == pre.initial_and_adjustments
        assert mid.realized_net == pre.realized_net
        assert mid.open_entry_fees == pre.open_entry_fees
        assert mid.identity_difference == pre.identity_difference
        assert mid.passed is True
        # ... and its identical recomputation read back the PRE receipt.
        assert mid.inserted is False and mid.check_id == pre.check_id

        # A fresh reader after the commit sees the new entry + cash event
        # (the write really landed between the statements).
        post = store_psycopg.bankroll_daily_check(reader, request)
        assert post.inserted is True and post.event_watermark > pre.event_watermark
        assert post.cash_total == pre.cash_total - Decimal('250') - Decimal('7.70')
        assert post.deployed_total == pre.deployed_total + Decimal('250')
        assert post.open_entry_fees == pre.open_entry_fees + Decimal('7.70')
        assert post.passed is True
    finally:
        reader.close()
        admin.close()


@NATIVE
def test_fx29_evaluate_loader_reads_one_snapshot_across_a_midflight_commit():
    from polymarket_alpha_lab.papersim import cli, store_psycopg
    reader, writer, admin = _two_connections()
    try:
        _candidate_id, _entry_id, slug = _seed_accepted_entry(store_psycopg, reader)

        # Reader #1 (pre-write): the entered candidate, no captured outcome.
        pre = cli._evaluation_candidates_from_store(reader)
        assert len(pre) == 1 and pre[0].accepted and pre[0].fill_price is not None
        assert pre[0].natural_outcome is None

        # The writer blocks the loader's evidence statement while its
        # production writes stay uncommitted: a NEW scan (entry + cash
        # event, per the FX-29 acceptance wording) plus the market-outcome
        # capture for the seeded market -- the write that would leak into
        # the third SELECT under per-statement default isolation.
        with writer.cursor() as cursor:
            cursor.execute('LOCK TABLE papersim.candidate_price_evidence '
                           'IN ACCESS EXCLUSIVE MODE')
            cursor.execute("SELECT 'papersim.candidate_price_evidence'::regclass::oid")
            blocked_oid = cursor.fetchone()[0]
        store_psycopg.record_scan(writer, store.ScanSubmission(
            scan=_scan_run(), candidates=(_candidate('fx29b-' + uuid.uuid4().hex[:10]),)))
        store_psycopg.record_market_outcome(writer, _outcome_observation(slug, 'YES'))

        box, thread = _midflight_reader(
            lambda: cli._evaluation_candidates_from_store(reader))
        try:
            _wait_reader_blocked(admin, reader.info.backend_pid,
                                 blocked_relation_oid=blocked_oid)
            writer.commit()  # entry + cash + outcome capture land mid-load
        finally:
            writer.close()
            thread.join(timeout=60)
        assert not thread.is_alive() and 'error' not in box, box.get('error', '')
        mid = box['result']

        # The mid-flight load equals the PRE-write queue exactly: the new
        # candidate is absent AND the just-captured outcome token stays
        # invisible -- all three SELECTs came from ONE snapshot.
        assert mid == pre
        assert [candidate.candidate_id for candidate in mid] == [pre[0].candidate_id]
        assert mid[0].natural_outcome is None

        # A fresh load after the commit sees BOTH writes.
        post = cli._evaluation_candidates_from_store(reader)
        assert len(post) == 2
        seeded = next(candidate for candidate in post
                      if candidate.candidate_id == str(_candidate_id))
        assert seeded.natural_outcome == 'YES'
    finally:
        reader.close()
        admin.close()


@NATIVE
def test_fx29_settle_loader_snapshot_races_a_late_capture(repo):
    """The production settle loader under a mid-flight capture.

    A settle run racing the outcome capture must NOT settle on it (the
    snapshot discipline keeps the run's read consistent); the capture
    settles on the NEXT run -- the late-capture causality even under the
    race, with replay-safe receipts for both runs.
    """
    repo_root, _head = repo
    from polymarket_alpha_lab.papersim import store_psycopg
    reader, writer, admin = _two_connections()
    try:
        _candidate_id, entry_id, slug = _seed_accepted_entry(store_psycopg, reader)

        # The writer blocks the loader's effective-outcome read while the
        # capture for the seeded market sits uncommitted.
        with writer.cursor() as cursor:
            cursor.execute('LOCK TABLE papersim.market_outcomes IN ACCESS EXCLUSIVE MODE')
            cursor.execute("SELECT 'papersim.market_outcomes'::regclass::oid")
            blocked_oid = cursor.fetchone()[0]
        store_psycopg.record_market_outcome(writer, _outcome_observation(slug, 'YES'))

        box, thread = _midflight_reader(
            lambda: _run(reader, repo_root, operation_key='settle|crypto_eth|fx29c'))
        try:
            _wait_reader_blocked(admin, reader.info.backend_pid,
                                 blocked_relation_oid=blocked_oid)
            writer.commit()  # the capture lands inside the run's snapshot read
        finally:
            writer.close()
            thread.join(timeout=60)
        assert not thread.is_alive() and 'error' not in box, box.get('error', '')
        raced = box['result']

        # The racing run saw NO captured outcome: pending with reason, no
        # settlement, no cash movement.
        assert raced['settled_count'] == 0 and raced['snapshot_entries'] == 1
        assert raced['pending'] == [{'entry_id': entry_id,
                                     'reason': 'no_captured_outcome'}]
        assert reader.execute('SELECT count(*) FROM papersim.settlements'
                              ).fetchone()[0] == 0

        # The NEXT run (after the capture committed) settles the entry.
        settled = _run(reader, repo_root, operation_key='settle|crypto_eth|fx29c2')
        assert settled['settled_count'] == 1 and settled['pending_count'] == 0
        assert settled['settled'][0]['entry_id'] == entry_id
        assert settled['settled'][0]['classification'] == 'NATURAL'
        assert reader.execute('SELECT count(*) FROM papersim.bankroll_events '
                              "WHERE event_key = %s AND reason = 'SETTLE_NATURAL'",
                              (f'settle:{entry_id}',)).fetchone()[0] == 1
        phases = reader.execute("SELECT phase FROM papersim.command_run_events "
                                "WHERE command = 'settle' ORDER BY run_event_id"
                                ).fetchall()
        assert [row[0] for row in phases] == ['STARTED', 'SUCCEEDED',
                                              'STARTED', 'SUCCEEDED']
    finally:
        reader.close()
        admin.close()


@pytest.fixture
def cash_faces():
    _, app_dsn = _fresh_managed_database()
    with _connect(app_dsn) as writer, _connect(app_dsn) as observer:
        assert writer.info.backend_pid != observer.info.backend_pid
        yield writer, observer, app_dsn


def _prepare_scan(slug, *, entered=6, price='0.56', fee='7.70', shares=None,
                  stake='250', condition=None, market=None, legacy=False, entry=True):
    with localcontext(prec=60):
        price, stake = Decimal(price), Decimal(stake)
        shares = stake / price if shares is None else Decimal(shares)
        submission = replace(
            _entry_submission(), entered_at=at(entered), stake=stake, price=price, shares=shares,
            entry_fee=Decimal(fee), fee_source='estimated' if legacy else 'schedule',
            legacy_source_id=f'historical-entry-{slug}' if legacy else None, is_legacy=legacy)
    candidate = replace(
        _candidate(slug), condition_id=condition, market_id=market, evaluated_at=at(entered - 1),
        price_evidence=(replace(_evidence(slug), observed_at=at(entered - 1),
                                model_probability=Decimal('0.8'), mid=Decimal('0.5')),),
        legacy_source_id=f'historical-candidate-{slug}' if legacy else None, is_legacy=legacy,
        entry=submission if entry else None)
    scan = replace(_scan_run(), started_at=at(entered - 6), finished_at=at(entered),
                   is_legacy=legacy)
    return store.ScanSubmission(scan=scan, candidates=(candidate,))


def _accepted(app, slug, **kwargs):
    from polymarket_alpha_lab.papersim import store_psycopg
    submission = _prepare_scan(slug, **kwargs)
    result = store_psycopg.record_scan(app, submission)
    candidate_id = result['candidate_ids'][submission.candidates[0].candidate_key]
    entry_id = app.execute('SELECT entry_id FROM papersim.entries WHERE candidate_id = %s',
                           (candidate_id,)).fetchone()[0]
    return candidate_id, entry_id


def _publish(app, outcome, *, slug=None, condition=None, market=None,
             observed=60, confirmed=80):
    from polymarket_alpha_lab.papersim import store_psycopg

    def time_at(value):
        return at(value) if type(value) is int else value

    return store_psycopg.record_market_outcome(app, store.MarketOutcomeObservation(
        condition_id=condition, market_id=market, market_slug=slug, outcome=outcome,
        observed_at=time_at(observed), confirmed_at=time_at(confirmed),
        source_kind='gamma_close', source_ref=uuid.uuid4().hex))['outcome_id']


def _insert_event(conn, key, *, minute, amount, reason, currency='USD',
                  entry_id=None, scan_id=None, reverses=None, legacy=False, note=None):
    return conn.execute(
        'INSERT INTO papersim.bankroll_events '
        '(event_key, occurred_at, currency, amount, reason, entry_id, scan_id, '
        'reverses_event_id, is_legacy, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
        'RETURNING event_id',
        (key, at(minute), currency, Decimal(amount), reason, entry_id, scan_id,
         reverses, legacy, note)).fetchone()[0]


def _as_of_oracle(observer, cut, code_commit, expected):
    """Independent as-of cash/event and entry/settlement reads on a second backend."""
    from polymarket_alpha_lab.papersim import store_psycopg
    events = observer.execute(
        'SELECT event_id, amount, reason FROM papersim.bankroll_events '
        'WHERE occurred_at <= %s ORDER BY event_id', (cut,)).fetchall()
    entries = observer.execute(
        'SELECT entry_id, stake, entry_fee FROM papersim.entries WHERE entered_at <= %s',
        (cut,)).fetchall()
    settlements = observer.execute(
        'SELECT entry_id, pnl_net FROM papersim.settlements WHERE confirmed_at <= %s',
        (cut,)).fetchall()
    closed = {row[0] for row in settlements}
    with localcontext(prec=100):
        parts = (
            sum(row[1] for row in events),
            sum(stake for entry, stake, _fee in entries if entry not in closed),
            sum(amount for _id, amount, reason in events if reason == 'INITIAL'),
            sum(row[1] for row in settlements),
            sum(fee for entry, _stake, fee in entries if entry not in closed),
        )
        cash, deployed, base, net, fees = parts
        assert cash + deployed - base - net + fees == 0
    assert (*parts, len(events), len(entries), len(settlements)) == expected
    watermark = max((row[0] for row in events), default=0)
    req = store.BankrollCheckRequest(as_of=cut, code_commit=code_commit)
    event_count = observer.execute('SELECT count(*) FROM papersim.bankroll_events').fetchone()
    check = store_psycopg.bankroll_daily_check(observer, req)
    assert (check.cash_total, check.deployed_total, check.initial_and_adjustments,
            check.realized_net, check.open_entry_fees) == parts
    assert check.passed and check.inserted and check.identity_difference == 0
    assert check.tolerance == Decimal('0.005') and check.event_watermark == watermark
    assert observer.execute(
        'SELECT as_of, cash_total, deployed_total, initial_and_adjustments, realized_net, '
        'open_entry_fees, identity_difference, tolerance, passed, event_watermark '
        'FROM papersim.bankroll_checks WHERE check_id = %s',
        (check.check_id,)).fetchone() == (cut, *parts, 0, Decimal('0.005'), True, watermark)
    assert observer.execute('SELECT count(*) FROM papersim.bankroll_events').fetchone() == event_count


@NATIVE
@pytest.mark.parametrize(('token', 'price', 'booked_fee', 'payout', 'gross', 'net', 'final_cash'), [
    ('VOIDED', '0.56', '7.70', '250', '0', '0', '10000'),
    ('NO', '0.56', '8.20', '0', '-250', '-258.20', '9741.80'),
    ('YES', '0.56', '8.20', '446.4285714285714', '196.4285714285714',
     '188.2285714285714', '10188.2285714285714'),
    ('YES', '0.50', '8.20', '500', '250', '241.80', '10241.80'),
    ('YES', '0.50', '0', '500', '250', '250', '10250'),
    ('VOIDED', '0.50', '0', '250', '0', '0', '10000'),
    ('YES', '0.0015', '0.1234567890123456789012345', '166666.66666666666',
     '166416.66666666666', '166416.5432098776476543210987655',
     '176416.5432098776476543210987655'),
    ('VOIDED', '0.50', '0.1234567890123456789012345', '250', '0', '0', '10000'),
    ('VOIDED', '0.50', '0.1234567890123456789012345678901234567', '250', '0', '0', '10000'),
])
def test_b3_physical_cash_uses_booked_fees_at_all_as_of_cuts(
        cash_faces, repo, token, price, booked_fee, payout, gross, net, final_cash):
    app, reader, _app_dsn = cash_faces
    repo_root, _head = repo
    _insert_event(app, 'initial', minute=1, amount='10000', reason='INITIAL')
    with localcontext(prec=2):
        _candidate_id, entry_id = _accepted(app, 'b3-physical', price=price, fee=booked_fee)
        observed_id = _publish(app, token, slug='b3-physical')
        outcome = _run(app, repo_root, operation_key='b3-physical')
    assert outcome['settled_count'] == 1 and outcome['pending_count'] == 0
    proceeds, gross, net, final_cash, booked_fee = map(
        Decimal, (payout, gross, net, final_cash, booked_fee))
    void = token == 'VOIDED'
    performance_fee = Decimal(0) if void else booked_fee
    reason = 'VOIDED_REFUND' if void else 'SETTLE_NATURAL'
    classification = 'VOIDED_REFUND' if void else 'NATURAL'
    opening, opened_cash, refund, pg_net = reader.execute(
        'SELECT -(e.stake + e.entry_fee), 10000 - e.stake - e.entry_fee, '
        'e.stake + e.entry_fee, s.pnl_gross - s.fee_total FROM papersim.entries e '
        'JOIN papersim.settlements s USING (entry_id) WHERE e.entry_id = %s',
        (entry_id,)).fetchone()
    assert reader.execute(
        'SELECT classification, proceeds, pnl_gross, fee_total, pnl_net, '
        'source_outcome_id, confirmed_at, win_loss FROM papersim.settlements WHERE entry_id = %s',
        (entry_id,)).fetchone() == (classification, proceeds, gross, performance_fee, net,
                                   observed_id, at(80), None if void else
                                   ('win' if token == 'YES' else 'loss'))
    assert pg_net == net
    if price == '0.0015':
        assert opening == Decimal('-250.1234567890123456789012345')
    if booked_fee == Decimal('0.1234567890123456789012345678901234567'):
        assert opening == Decimal('-250.1234567890123456789012345678901234567')
    assert reader.execute(
        'SELECT event_key, amount, reason, occurred_at, currency FROM papersim.bankroll_events '
        'WHERE entry_id = %s ORDER BY event_id', (entry_id,)).fetchall() == [
        (f'entry:{entry_id}:cash', opening, 'SCAN_ENTRY', at(6), 'USD'),
        (f'settle:{entry_id}', refund if void else proceeds, reason, at(80), 'USD'),
    ]
    expected = (
        (10000, 0, 10000, 0, 0, 1, 0, 0),
        (opened_cash, 250, 10000, 0, booked_fee, 2, 1, 0),
        (opened_cash, 250, 10000, 0, booked_fee, 2, 1, 0),
        (final_cash, 0, 10000, net, 0, 3, 1, 1),
        (final_cash, 0, 10000, net, 0, 3, 1, 1),
    )
    for index, (minute, parts) in enumerate(zip((5, 6, 79, 80, 120), expected, strict=True)):
        with localcontext(prec=2):
            _as_of_oracle(reader, at(minute), str(index) * 40, parts)
    assert reader.execute('SELECT count(*) FROM papersim.bankroll_checks').fetchone() == (5,)
    assert reader.execute('SELECT count(*) FROM papersim.bankroll_events').fetchone() == (3,)


def _business_counts(conn):
    return conn.execute(
        'SELECT (SELECT count(*) FROM papersim.scan_runs), '
        '(SELECT count(*) FROM papersim.candidates), (SELECT count(*) FROM papersim.entries), '
        '(SELECT count(*) FROM papersim.bankroll_events), '
        '(SELECT count(*) FROM papersim.shadow_predictions), '
        '(SELECT count(*) FROM papersim.settlements)').fetchone()


@NATIVE
def test_record_scan_checks_the_exact_share_product_before_any_write(cash_faces):
    from polymarket_alpha_lab.papersim import store_psycopg
    app, reader, _app_dsn = cash_faces
    cases = (
        ('502', '250', '1', False), ('502', '251', '0', True),
        ('500.019998', '250', '0.009999', True), ('500.02', '250', '0.010', False),
        ('500.020002', '250', '0.010001', False), ('499.98', '250', '0.010', False),
        ('499.980002', '250', '0.009999', True),
    )
    for index, (shares, stake, gap, valid) in enumerate(cases):
        candidate = _prepare_scan(f'shares-{index}', price='0.5', shares=shares, stake=stake)
        difference = reader.execute(
            'SELECT abs(%s::numeric * 0.5 - %s::numeric)',
            (Decimal(shares), Decimal(stake))).fetchone()[0]
        assert difference == Decimal(gap)
        previous = _business_counts(reader)
        with localcontext(prec=2):
            if valid:
                assert store_psycopg.record_scan(app, candidate)['inserted']
            else:
                with pytest.raises(store.PapersimStoreError,
                                   match='papersim_entry_stake_mismatch'):
                    store_psycopg.record_scan(app, candidate)
        if valid:
            assert _business_counts(reader) == tuple(
                n + (position < 4) for position, n in enumerate(previous))
            assert reader.execute(
                'SELECT e.stake, e.shares, e.price, b.amount FROM papersim.entries e '
                'JOIN papersim.bankroll_events b USING (entry_id) ORDER BY e.entry_id DESC LIMIT 1'
            ).fetchone() == (Decimal(stake), Decimal(shares), Decimal('0.5'),
                             Decimal('-258.70' if stake == '251' else '-257.70'))
        else:
            assert _business_counts(reader) == previous


def _entry_without_debit(app, slug, fee):
    from polymarket_alpha_lab.papersim import store_psycopg
    submission = _prepare_scan(slug, fee=fee, entry=False)
    scan = store_psycopg.record_scan(app, submission)
    candidate_id = scan['candidate_ids'][submission.candidates[0].candidate_key]
    entry_id = app.execute(
        'INSERT INTO papersim.entries '
        '(candidate_id, stake, price, shares, fee_category, fee_rate, entry_fee, tier, entered_at) '
        "VALUES (%s, 250, 0.5, 500, 'crypto', 0.07, %s, 'standard', %s) RETURNING entry_id",
        (candidate_id, Decimal(fee), at(6))).fetchone()[0]
    return entry_id, scan['scan_id']


def _entry_rows(conn):
    return conn.execute(
        'SELECT event_id, event_key, amount, occurred_at, currency, reason, entry_id, scan_id, '
        "reverses_event_id, is_legacy FROM papersim.bankroll_events WHERE event_key NOT LIKE 'settle:%' "
        'ORDER BY event_id').fetchall()


@NATIVE
@pytest.mark.parametrize('token', ['YES', 'VOIDED'])
def test_b3_leaves_disputed_opening_debits_open_and_settles_healthy_peer(cash_faces, repo, token):
    app, reader, _app_dsn = cash_faces
    repo_root, _head = repo
    _, healthy = _accepted(app, 'healthy', fee='8.20')
    _publish(app, token, slug='healthy')
    expected = []
    variants = (
        ('short', '8.20', '-257.70', 'amount'), ('high', '7.70', '-258.20', 'amount'),
        ('missing', '8.20', None, 'missing'), ('duplicate', '8.20', '-258.20', 'ambiguous'),
        ('wrong-time', '8.20', '-258.20', 'metadata'),
        ('wrong-currency', '8.20', '-258.20', 'metadata'),
        ('wrong-scan', '8.20', '-258.20', 'metadata'),
        ('wrong-reason', '8.20', '-258.20', 'metadata'),
        ('wrong-legacy', '8.20', '-258.20', 'metadata'),
        ('wrong-entry', '8.20', '-258.20', 'metadata'),
        ('reversed', '8.20', '-258.20', 'metadata'),
    )
    for slug, fee, amount, state in variants:
        entry_id, scan_id = _entry_without_debit(app, slug, fee)
        event_ids = []
        if amount is not None:
            opening = _insert_event(
                app, f'entry:{entry_id}:cash', minute=7 if slug == 'wrong-time' else 6,
                amount=amount, reason='ADJUSTMENT' if slug == 'wrong-reason' else 'SCAN_ENTRY',
                entry_id=None if slug == 'wrong-entry' else entry_id,
                scan_id=None if slug == 'wrong-scan' else scan_id,
                currency='EUR' if slug == 'wrong-currency' else 'USD',
                legacy=slug == 'wrong-legacy')
            event_ids.append(opening)
            if slug == 'duplicate':
                event_ids.append(_insert_event(
                    app, f'other-opening:{entry_id}', minute=6, amount=amount,
                    reason='SCAN_ENTRY', entry_id=entry_id, scan_id=scan_id))
            if slug == 'reversed':
                _insert_event(app, f'reversal:{entry_id}', minute=7, amount='258.20',
                              reason='REVERSAL', reverses=opening)
        _publish(app, token, slug=slug)
        expected.append({
            'entry_id': entry_id, 'reason': 'entry_cash_mismatch', 'cash_discrepancy': {
                'state': state, 'event_ids': event_ids,
                'expected_amount': '-257.70' if fee == '7.70' else '-258.20',
                'actual_amount': None if state in {'missing', 'ambiguous'} else amount,
            },
        })

    baseline_openings = _entry_rows(reader)
    before = _business_counts(reader)
    first = _run(app, repo_root, operation_key='b3-cash-guard')
    assert first['pending'] == expected
    assert [written['entry_id'] for written in first['settled']] == [healthy]
    retry = _run(app, repo_root, operation_key='b3-cash-guard', attempt=2)
    assert retry['pending'] == expected and retry['settled_count'] == 0
    assert _entry_rows(reader) == baseline_openings
    assert _business_counts(reader) == (before[0], before[1], before[2], before[3] + 1,
                                       before[4], before[5] + 1)
    assert reader.execute(
        "SELECT entry_id, amount, reason FROM papersim.bankroll_events WHERE event_key LIKE 'settle:%'"
    ).fetchall() == [(healthy, Decimal('258.20' if token == 'VOIDED'
                                      else '446.4285714285714'),
                     'VOIDED_REFUND' if token == 'VOIDED' else 'SETTLE_NATURAL')]
    assert reader.execute('SELECT entry_id FROM papersim.settlements').fetchall() == [(healthy,)]


@NATIVE
def test_b3_reentry_excludes_old_terminals_and_posts_the_new_confirmation(cash_faces, repo):
    app, reader, _app_dsn = cash_faces
    repo_root, _head = repo
    _insert_event(app, 'initial', minute=1, amount='10000', reason='INITIAL')
    _, first = _accepted(app, 'reused', price='0.50')
    _publish(app, 'NO', slug='reused', observed=8, confirmed=8)
    first_result = _run(app, repo_root, operation_key='b3-reused-initial')
    assert [row['entry_id'] for row in first_result['settled']] == [first]

    _, later = _accepted(app, 'reused', price='0.50', entered=20)
    assert first != later
    _as_of_oracle(reader, at(15), 'c' * 40,
                  (Decimal('9742.30'), 0, 10000, Decimal('-257.70'), 0, 3, 1, 1))
    captures = [(None, None, None), ('YES', 24, 8), ('YES', 30, None), ('YES', 18, 23)]
    for attempt, (token, observed, confirmed) in enumerate(captures, 1):
        if token is not None:
            _publish(app, token, slug='reused', observed=observed, confirmed=confirmed)
        result = _run(app, repo_root, operation_key='b3-reused', attempt=attempt)
        assert result['pending'] == [{'entry_id': later, 'reason': 'no_captured_outcome'}]
        assert result['settled_count'] == 0
        assert reader.execute('SELECT entry_id FROM papersim.settlements').fetchall() == [(first,)]
    _as_of_oracle(reader, at(45), 'd' * 40,
                  (Decimal('9484.60'), 250, 10000, Decimal('-257.70'),
                   Decimal('7.70'), 4, 2, 1))

    valid = _publish(app, 'YES', slug='reused', observed=34, confirmed=25)
    _publish(app, 'NO', slug='reused', observed=60, confirmed=10)
    done = _run(app, repo_root, operation_key='b3-reused', attempt=5)
    assert [row['entry_id'] for row in done['settled']] == [later]
    assert reader.execute(
        'SELECT s.source_outcome_id, s.confirmed_at, b.occurred_at, b.amount '
        'FROM papersim.settlements s JOIN papersim.bankroll_events b USING (entry_id) '
        "WHERE s.entry_id = %s AND b.reason = 'SETTLE_NATURAL'", (later,)
    ).fetchall() == [(valid, at(25), at(25), Decimal('500'))]
    _as_of_oracle(reader, at(24), 'e' * 40,
                  (Decimal('9484.60'), 250, 10000, Decimal('-257.70'),
                   Decimal('7.70'), 4, 2, 1))
    _as_of_oracle(reader, at(25), 'f' * 40,
                  (Decimal('9984.60'), 0, 10000, Decimal('-15.40'), 0, 5, 2, 2))


@NATIVE
def test_b3_typed_matching_ranks_eligible_observations(cash_faces, repo):
    app, reader, _app_dsn = cash_faces
    repo_root, _head = repo
    stale_alias = _accepted(app, 'alias', condition='condition-alias', market='market-alias')[1]
    _publish(app, 'NO', condition='condition-alias', observed=90, confirmed=5)
    alias_id = _publish(app, 'YES', market='market-alias', observed=20, confirmed=20)
    contradiction = _accepted(app, 'slug-contradiction', condition='own-condition')[1]
    _publish(app, 'NO', slug='slug-contradiction', condition='foreign-condition')
    cross = _accepted(app, 'same-text-slug', condition='typed-text')[1]
    _publish(app, 'YES', market='typed-text')
    strong = _accepted(app, 'priority', condition='condition-priority', market='market-priority')[1]
    strong_id = _publish(app, 'YES', condition='condition-priority', observed=20, confirmed=20)
    _publish(app, 'NO', market='market-priority', observed=40, confirmed=40)
    weak = _accepted(app, 'unknown-strong', condition='condition-unknown', market='market-known')[1]
    _publish(app, 'UNKNOWN', condition='condition-unknown', observed=80, confirmed=None)
    weak_id = _publish(app, 'YES', market='market-known', observed=20, confirmed=20)
    unknown = _accepted(app, 'unknown-only')[1]
    _publish(app, 'UNKNOWN', slug='unknown-only', observed=60, confirmed=None)
    tied = _accepted(app, 'equal-utc')[1]
    _publish(app, 'NO', slug='equal-utc', observed=at(6), confirmed=None)
    offset = datetime.timezone(datetime.timedelta(hours=-7))
    tie_id = _publish(app, 'YES', slug='equal-utc', observed=at(6).astimezone(offset), confirmed=None)

    result = _run(app, repo_root, operation_key='b3-typed')
    assert result['pending'] == [
        {'entry_id': contradiction, 'reason': 'no_captured_outcome'},
        {'entry_id': cross, 'reason': 'no_captured_outcome'},
        {'entry_id': unknown, 'reason': 'outcome_unknown'},
    ]
    assert [row['entry_id'] for row in result['settled']] == [stale_alias, strong, weak, tied]
    assert reader.execute(
        'SELECT entry_id, source_outcome_id, confirmed_at, win_loss '
        'FROM papersim.settlements ORDER BY entry_id'
    ).fetchall() == [(stale_alias, alias_id, at(20), 'win'), (strong, strong_id, at(20), 'win'),
                     (weak, weak_id, at(20), 'win'), (tied, tie_id, at(6), 'win')]


@NATIVE
def test_b3_market_scoring_is_independent_of_unrecorded_and_recorded_trade_results(
        cash_faces, repo, tmp_path):
    from polymarket_alpha_lab.papersim import cli, evaluation, fees
    app, reader, _app_dsn = cash_faces
    repo_root, _head = repo
    candidate, entry_id = _accepted(
        app, 'contrasting', entered=10, fee='8.20', condition='contrasting-condition')
    _publish(app, 'NO', condition='contrasting-condition', observed=40, confirmed=5)
    gap_candidate, gap_entry = _accepted(
        app, 'gap', entered=10, condition='gap-condition', market='eligible-alias')
    _publish(app, 'UNKNOWN', condition='gap-condition', observed=40, confirmed=None)

    def load(candidate_id):
        return next(row for row in cli._evaluation_candidates_from_store(reader)
                    if row.candidate_id == str(candidate_id))

    def report(row, basis=evaluation.PNL_BASIS_RECORDED):
        return evaluation.evaluate_queues((row,), pnl_basis=basis).queues[0]

    stale = load(candidate)
    assert (stale.outcome, stale.settled_time, stale.natural_outcome) == (None, None, 'NO')
    before = report(stale)
    assert before.counts.unresolved == 1 and before.roi.counted == 0
    assert before.scoring.markets_scored == 1 and before.scoring.model_brier == 0.64
    actual = _publish(app, 'YES', condition='contrasting-condition', observed=28, confirmed=24)
    gap_actual = _publish(app, 'YES', market='eligible-alias', observed=28, confirmed=24)
    expected = (
        (candidate, entry_id, actual, 'NO', 1, 0, 0.64, 0.25),
        (gap_candidate, gap_entry, gap_actual, None, 0, 1, None, None),
    )
    now = _business_counts(reader)
    for candidate_id, _entry, _observation, global_outcome, scored, missing, model, mid in expected:
        row = load(candidate_id)
        assert row.market_outcome_authoritative
        assert (row.outcome, row.settled_time, row.recorded_pnl, row.natural_outcome) == (
            'YES', at(24), None, global_outcome)
        with pytest.raises(ValueError, match="require a recorded 'pnl'"):
            report(row)
        projected = report(row, evaluation.PNL_BASIS_RECOMPUTED)
        assert projected.fills.hits == projected.roi.counted == 1
        assert (projected.scoring.markets_scored, projected.scoring.missing_outcome,
                projected.scoring.model_brier, projected.scoring.market_brier) == (
                    scored, missing, model, mid)

    file = tmp_path / 'legacy-input.json'
    legacy_json = asdict(load(candidate))
    del legacy_json['market_outcome_authoritative']
    file.write_text(json.dumps([legacy_json], default=lambda time: time.isoformat()), encoding='utf-8')
    legacy_row, = cli._evaluation_candidates_from_json(file)
    assert report(legacy_row, evaluation.PNL_BASIS_RECOMPUTED).scoring.model_brier == 0.04
    managed_gap = load(gap_candidate)
    legacy_gap = replace(legacy_row, candidate_id='legacy-row', market_key=managed_gap.market_key)
    mixed = evaluation.evaluate_queues(
        (managed_gap, legacy_gap), pnl_basis=evaluation.PNL_BASIS_RECOMPUTED).queues[0]
    assert mixed.scoring.missing_outcome == 1 and mixed.scoring.markets_scored == 0
    assert mixed.scoring.model_brier is None and mixed.scoring.deduped_repeats == 1
    assert _business_counts(reader) == now

    result = _run(app, repo_root, operation_key='b3-scoring')
    assert result['settled_count'] == 2 and result['pending_count'] == 0
    settled_counts = _business_counts(reader)
    for candidate_id, affected, observation, global_outcome, scored, missing, model, mid in expected:
        row = load(candidate_id)
        assert (row.outcome, row.settled_time, row.natural_outcome) == ('win', at(24), global_outcome)
        recorded = report(row)
        assert recorded.fills.hits == recorded.roi.counted == 1
        assert (recorded.scoring.markets_scored, recorded.scoring.missing_outcome,
                recorded.scoring.model_brier, recorded.scoring.market_brier) == (
                    scored, missing, model, mid)
        persisted = reader.execute(
            'SELECT source_outcome_id, pnl_gross, fee_total, pnl_net FROM papersim.settlements '
            'WHERE entry_id = %s', (affected,)).fetchone()
        assert persisted[0] == observation and Decimal(str(row.recorded_pnl)) == persisted[1]
        assert recorded.roi.entry_fees == pytest.approx(7.70)
        assert recorded.roi.fee_schedule_as_of == fees.FEE_SCHEDULE_AS_OF
        assert recorded.roi.net_pnl == pytest.approx(188.7285714285714)
        if affected == entry_id:
            assert persisted[2:] == (Decimal('8.20'), Decimal('188.2285714285714'))
    assert _business_counts(reader) == settled_counts


@NATIVE
def test_automatic_write_rechecks_cash_and_keeps_the_chosen_capture(cash_faces, repo):
    from polymarket_alpha_lab.papersim import store_psycopg
    app, writer, _app_dsn = cash_faces
    repo_root, _head = repo
    _, sound = _accepted(app, 'stable-capture')
    source = _publish(app, 'YES', slug='stable-capture')
    _, tainted = _accepted(app, 'tainted-after-plan')
    _publish(app, 'VOIDED', slug='tainted-after-plan')
    scan_id = writer.execute(
        'SELECT scan_id FROM papersim.bankroll_events WHERE event_key = %s',
        (f'entry:{tainted}:cash',)).fetchone()[0]

    def load_then_commit(connection):
        rows = store_psycopg.open_entries_for_settlement(connection)
        assert [row.entry_id for row in rows] == [sound, tainted]
        assert rows[0].outcome.outcome_id == source
        _publish(writer, 'NO', slug='stable-capture', observed=100, confirmed=90)
        _insert_event(writer, f'contradictory:{tainted}', minute=6, amount='-257.70',
                      reason='SCAN_ENTRY', entry_id=tainted, scan_id=scan_id)
        return rows

    result = settle.run_settle(
        'crypto_eth', conn=app, repo_root=repo_root, now_utc=lambda: at(120),
        open_entries=load_then_commit, operation_key='b3-between-plan-and-write')
    assert [row['entry_id'] for row in result['settled']] == [sound]
    assert [(row['entry_id'], row['reason'], row['cash_discrepancy']['state'])
            for row in result['pending']] == [(tainted, 'entry_cash_mismatch', 'ambiguous')]
    assert writer.execute(
        'SELECT s.entry_id, s.source_outcome_id, s.outcome_label, b.occurred_at, b.amount '
        'FROM papersim.settlements s JOIN papersim.bankroll_events b USING (entry_id) '
        "WHERE b.reason = 'SETTLE_NATURAL'").fetchall() == [
            (sound, source, 'YES', at(80), Decimal('446.4285714285714'))]
    assert writer.execute(
        'SELECT reason FROM papersim.bankroll_events WHERE entry_id = %s ORDER BY event_id',
        (tainted,)).fetchall() == [('SCAN_ENTRY',), ('SCAN_ENTRY',)]


@NATIVE
def test_guarded_settlement_rejects_wrong_typed_source_token_side_or_time(cash_faces):
    from polymarket_alpha_lab.papersim import store_psycopg
    app, reader, _app_dsn = cash_faces
    _accepted(app, 'expected', condition='expected-key')
    _publish(app, 'YES', condition='expected-key')
    foreign = _publish(app, 'YES', market='expected-key')
    pending, = store_psycopg.open_entries_for_settlement(app)
    decision = settle.plan_settlement(pending)
    record, guard = decision.record, decision.automatic
    foreign_capture = replace(
        guard.outcome, outcome_id=foreign, identity=settle.MarketIdentity(market_id='expected-key'))
    before = _business_counts(reader)
    variants = (
        (replace(record, source_outcome_id=foreign), replace(guard, outcome=foreign_capture),
         'papersim_automatic_settlement_evidence_mismatch'),
        (record, replace(guard, outcome=replace(guard.outcome, confirmed_at=at(70))),
         'papersim_automatic_settlement_evidence_mismatch'),
        (replace(record, outcome_label='NO', win_loss='loss'), guard,
         'papersim_automatic_settlement_record_mismatch'),
        (replace(record, confirmed_at=at(69)), guard, 'papersim_automatic_settlement_record_mismatch'),
        (replace(record, fee_total=Decimal('0'), pnl_net=record.pnl_gross), guard,
         'papersim_automatic_settlement_record_mismatch'),
    )
    for settlement, context, code in variants:
        with pytest.raises(store.PapersimStoreError, match=code):
            store_psycopg.record_automatic_settlement(app, settlement, context)
        assert _business_counts(reader) == before
    result = store_psycopg.record_automatic_settlement(app, record, guard)
    assert result['inserted'] is True


def _wait_settlement_conflict(observer, holder_pid, contender_pid):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        blockers, ungranted = observer.execute(
            'SELECT pg_blocking_pids(%s), EXISTS (SELECT 1 FROM pg_locks WHERE pid = %s '
            "AND locktype = 'transactionid' AND NOT granted)",
            (contender_pid, contender_pid)).fetchone()
        if holder_pid in blockers and ungranted:
            return
        time.sleep(0.02)
    pytest.fail('contender never blocked on the held settlement transaction ID')


@NATIVE
@pytest.mark.parametrize('scenario', ['identical', 'divergent', 'repeatable-read'])
def test_automatic_settlement_writers_converge_under_the_unique_key(cash_faces, scenario):
    import psycopg

    from polymarket_alpha_lab.papersim import store_psycopg
    holder, observer, dsn = cash_faces
    _, entry_id = _accepted(holder, 'same-entry')
    source = _publish(holder, 'YES', slug='same-entry')
    entry, = store_psycopg.open_entries_for_settlement(holder)
    first = settle.plan_settlement(entry)
    proposed = (replace(first.record, confirmed_by='other-operator')
                if scenario == 'divergent' else first.record)
    with _connect(dsn) as contender:
        if scenario == 'repeatable-read':
            contender.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        assert len({holder.info.backend_pid, observer.info.backend_pid,
                    contender.info.backend_pid}) == 3
        with holder.transaction():
            written = store_psycopg.record_automatic_settlement(holder, first.record, first.automatic)
            assert written['inserted']
            assert observer.execute(
                'SELECT count(*) FROM papersim.settlements WHERE entry_id = %s',
                (entry_id,)).fetchone() == (0,)
            box, thread = _midflight_reader(lambda: store_psycopg.record_automatic_settlement(
                contender, proposed, first.automatic))
            _wait_settlement_conflict(observer, holder.info.backend_pid, contender.info.backend_pid)
        thread.join(timeout=30)
        assert not thread.is_alive()
        if scenario == 'repeatable-read':
            assert isinstance(box.get('error'), psycopg.errors.SerializationFailure)
            assert contender.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
            assert store_psycopg.record_automatic_settlement(
                contender, first.record, first.automatic) == {**written, 'inserted': False}
    if scenario == 'identical':
        assert box == {'result': {**written, 'inserted': False}}
    elif scenario == 'divergent':
        assert isinstance(box.get('error'), store.PapersimStoreError)
        assert box['error'].code == 'papersim_settlement_conflict'
    assert observer.execute(
        'SELECT s.settlement_id, b.event_id, s.source_outcome_id, s.proceeds, '
        's.fee_total, s.pnl_net, s.confirmed_at, b.amount, b.occurred_at, b.reason '
        'FROM papersim.settlements s JOIN papersim.bankroll_events b USING (entry_id) '
        'WHERE s.entry_id = %s AND b.event_key = %s',
        (entry_id, f'settle:{entry_id}')).fetchall() == [
        (written['settlement_id'], written['bankroll_event_id'], source,
         Decimal('446.4285714285714'), Decimal('7.70'), Decimal('188.7285714285714'),
         at(80), Decimal('446.4285714285714'), at(80), 'SETTLE_NATURAL')]
    assert _business_counts(observer)[3:] == (2, 0, 1)


def _operator_record(entry_id, source=None, **fields):
    record = store.SettlementRecord(
        entry_id=entry_id, classification='NATURAL', outcome_label='YES', win_loss='win',
        proceeds=Decimal('446.43'), pnl_gross=Decimal('196.43'), fee_total=Decimal('7.70'),
        pnl_net=Decimal('188.73'), source_outcome_id=source, evidence='operator note',
        confirmed_by='operator', confirmed_at=at(90))
    return replace(record, **fields)


@NATIVE
def test_direct_writes_keep_operator_time_rounding_and_legacy_metadata(cash_faces, repo):
    from polymarket_alpha_lab.papersim import store_psycopg
    app, reader, _app_dsn = cash_faces
    repo_root, _head = repo
    _, selected = _accepted(app, 'operator')
    source = _publish(app, 'YES', slug='operator', observed=30, confirmed=60)
    _, unsourced = _accepted(app, 'unsourced')
    _, void = _accepted(app, 'direct-void', fee='8.20')
    _, legacy = _accepted(app, 'estimated-history', price='0.5', fee='8.20', legacy=True)
    legacy_source = _publish(app, 'YES', slug='estimated-history')
    records = (_operator_record(selected, source), _operator_record(unsourced))
    for record in records:
        result = store_psycopg.settlement_record(app, record)
        assert store_psycopg.settlement_record(app, record) == {**result, 'inserted': False}
        assert reader.execute(
            'SELECT b.amount, b.occurred_at, b.note, s.source_outcome_id, s.confirmed_by, '
            's.proceeds FROM papersim.bankroll_events b JOIN papersim.settlements s USING (entry_id) '
            'WHERE b.event_id = %s', (result['bankroll_event_id'],)).fetchone() == (
                Decimal('446.43'), at(90), 'operator note', record.source_outcome_id,
                'operator', Decimal('446.43'))
    direct_refund = _operator_record(
        void, classification='VOIDED_REFUND', outcome_label='VOIDED', win_loss=None,
        proceeds=Decimal('250'), pnl_gross=Decimal(0), fee_total=Decimal(0), pnl_net=Decimal(0))
    with pytest.raises(store.PapersimStoreError, match='papersim_voided_proceeds_mismatch'):
        store_psycopg.settlement_record(app, replace(direct_refund, proceeds=Decimal('249')))
    refund = store_psycopg.settlement_record(app, direct_refund)
    assert store_psycopg.settlement_record(app, direct_refund) == {**refund, 'inserted': False}
    assert reader.execute(
        'SELECT s.proceeds, s.pnl_net, b.amount, b.occurred_at, b.reason '
        'FROM papersim.settlements s JOIN papersim.bankroll_events b USING (entry_id) '
        'WHERE b.event_id = %s', (refund['bankroll_event_id'],)
    ).fetchone() == (Decimal('250'), Decimal(0), Decimal('258.20'), at(90), 'VOIDED_REFUND')
    outcome = _run(app, repo_root, operation_key='b3-legacy-booked-fee')
    assert [row['entry_id'] for row in outcome['settled']] == [legacy]
    assert reader.execute(
        'SELECT e.entry_fee, e.fee_source, e.is_legacy, s.fee_total, s.fee_source, '
        's.is_legacy, s.pnl_net, s.source_outcome_id, b.amount, b.is_legacy '
        'FROM papersim.entries e JOIN papersim.settlements s USING (entry_id) '
        "JOIN papersim.bankroll_events b USING (entry_id) WHERE b.entry_id = %s "
        "AND b.reason = 'SETTLE_NATURAL'", (legacy,)).fetchone() == (
            Decimal('8.20'), 'estimated', True, Decimal('8.20'), 'estimated',
            True, Decimal('241.80'), legacy_source, Decimal('500'), True)


@NATIVE
@pytest.mark.parametrize('defect', ['short-refund', 'missing', 'duplicate', 'metadata'])
def test_historical_cash_discrepancy_reports_original_rows_without_repair(
        cash_faces, defect):
    from polymarket_alpha_lab.papersim import store_psycopg
    app, reader, _app_dsn = cash_faces
    _, entry_id = _accepted(app, 'historical-cash', fee='8.20', legacy=True)
    key = f'settle:{entry_id}'
    record = _operator_record(
        entry_id, classification='VOIDED_REFUND', outcome_label='VOIDED',
        win_loss=None, proceeds=Decimal('250'), pnl_gross=Decimal(0), fee_total=Decimal(0),
        pnl_net=Decimal(0), fee_source='estimated', is_legacy=True)
    settlement_id = app.execute(
        'INSERT INTO papersim.settlements (entry_id, classification, outcome_label, proceeds, '
        'pnl_gross, fee_total, fee_source, pnl_net, evidence, confirmed_by, confirmed_at, is_legacy) '
        "VALUES (%s, 'VOIDED_REFUND', 'VOIDED', 250, 0, 0, 'estimated', 0, %s, %s, %s, true) "
        'RETURNING settlement_id',
        (entry_id, record.evidence, record.confirmed_by, record.confirmed_at)).fetchone()[0]
    historical_amount = '250' if defect == 'short-refund' else '258.20'
    event_id = None if defect == 'missing' else _insert_event(
        app, key, minute=90, amount=historical_amount, reason='VOIDED_REFUND',
        entry_id=entry_id, legacy=True, note=None if defect == 'metadata' else record.evidence)
    event_ids = () if event_id is None else (event_id,)
    if defect == 'duplicate':
        event_ids += (_insert_event(
            app, f'other-cash:{entry_id}', minute=90, amount=historical_amount,
            reason='VOIDED_REFUND', entry_id=entry_id, legacy=True, note=record.evidence),)
    counts = _business_counts(reader)
    for _ in range(2):
        with pytest.raises(store_psycopg.SettlementCashDiscrepancy) as caught:
            store_psycopg.settlement_record(app, record)
        error = caught.value
        assert (error.entry_id, error.settlement_id, error.cash_event_id) == (
            entry_id, settlement_id, event_id)
        assert error.expected_amount == Decimal('258.20')
        assert error.event_ids == event_ids
        if defect == 'missing':
            assert error.code == 'papersim_settlement_cash_missing'
            assert error.actual_amount is None and error.occurred_at is None
        else:
            assert error.code == 'papersim_settlement_cash_mismatch'
            assert error.actual_amount == Decimal(historical_amount) and error.occurred_at == at(90)
        assert _business_counts(reader) == counts
        assert reader.execute(
            'SELECT settlement_id, confirmed_at, is_legacy FROM papersim.settlements'
        ).fetchall() == [(settlement_id, at(90), True)]
        assert reader.execute(
            'SELECT event_id, amount, occurred_at FROM papersim.bankroll_events WHERE event_key = %s',
            (key,)).fetchall() == ([] if event_id is None else
                                  [(event_id, Decimal(historical_amount), at(90))])


@NATIVE
def test_later_cash_insert_failure_rolls_back_its_settlement_and_keeps_receipts(cash_faces, repo):
    import psycopg

    app, reader, _app_dsn = cash_faces
    repo_root, _head = repo
    _, healthy = _accepted(app, 'before-conflict')
    _publish(app, 'YES', slug='before-conflict')
    _, conflict = _accepted(app, 'cash-key-conflict')
    _publish(app, 'YES', slug='cash-key-conflict')
    conflicting_key = f'settle:{conflict}'
    sentinel = _insert_event(
        app, conflicting_key, minute=85, amount='1', reason='ADJUSTMENT', entry_id=conflict)
    baseline = _business_counts(reader)
    for attempt in (1, 2):
        with pytest.raises(psycopg.errors.UniqueViolation):
            _run(app, repo_root, operation_key='b3-failed-cash', attempt=attempt)
        assert reader.execute(
            'SELECT entry_id FROM papersim.settlements').fetchall() == [(healthy,)]
        assert reader.execute('SELECT event_id, amount, reason, occurred_at '
                              'FROM papersim.bankroll_events WHERE event_key = %s',
                              (conflicting_key,)).fetchall() == [
                                  (sentinel, Decimal(1), 'ADJUSTMENT', at(85))]
        assert _business_counts(reader) == tuple(
            count + (index in (3, 5)) for index, count in enumerate(baseline))
    assert reader.execute(
        'SELECT attempt, phase, error_code FROM papersim.command_run_events ORDER BY run_event_id'
    ).fetchall() == [(1, 'STARTED', None), (1, 'FAILED', 'UniqueViolation'),
                     (2, 'STARTED', None), (2, 'FAILED', 'UniqueViolation')]


@NATIVE
def test_active_cash_enforcement_matches_the_released_migrations(cash_faces):
    app, _reader, _dsn = cash_faces
    assert len(pm.papersim_migration_catalog(ROOT)) == 6
    protected = ('bankroll_events', 'entries', 'market_outcomes', 'settlements')
    assert app.execute(
        'SELECT c.relname, t.tgtype, t.tgenabled, p.proname '
        'FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid '
        'JOIN pg_namespace n ON n.oid = c.relnamespace '
        'JOIN pg_proc p ON p.oid = t.tgfoid '
        'WHERE n.nspname = %s AND c.relname = ANY(%s) AND NOT t.tgisinternal '
        'ORDER BY c.relname, t.tgtype',
        ('papersim', list(protected))).fetchall() == [
            (rel, trigger, 'O', 'reject_mutation') for rel in protected for trigger in (27, 34)]
    assert app.execute(
        "SELECT has_table_privilege(current_user, 'papersim.settlements', 'SELECT'), "
        "has_table_privilege(current_user, 'papersim.settlements', 'INSERT'), "
        "has_table_privilege(current_user, 'papersim.settlements', 'UPDATE'), "
        "current_user").fetchone() == (True, True, False, 'pal_app')


def _d1_seed_settlement_entry(project, slug, *, side="YES", entered_at=None, team="crypto_eth",
                               mismatch_token=False):
    from polymarket_alpha_lab import replay
    from polymarket_alpha_lab.papersim import snapshots, store_psycopg
    from polymarket_alpha_lab.papersim.adapters import polymarket
    from tests.test_papersim_scan_store_native import _eth_request

    # These are completed, historical B1 fixtures with their original book.
    entered = entered_at or datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=30)
    decided = entered - datetime.timedelta(seconds=1)
    received = entered - datetime.timedelta(seconds=2)
    source_at = entered - datetime.timedelta(seconds=3)
    condition = "0x" + hashlib.sha256(slug.encode()).hexdigest()
    book_id = int(condition[2:16], 16)
    yes, no = str(2 * book_id + 1), str(2 * book_id + 2)
    token = yes if side == "YES" else no
    question = "Will Ethereum be below 2,000 at the end of this period?"
    epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
    book = {"asset_id": token, "market": condition,
            "timestamp": str((source_at - epoch) // datetime.timedelta(milliseconds=1)),
            "bids": [{"price": "0.54", "size": "5000"}],
            "asks": [{"price": "0.56", "size": "5000"}]}
    url = f"{polymarket.CLOB_BOOK_URL}?token_id={token}"
    scan_key = f"run-scan|{team}|s1-fixture-{uuid.uuid4().hex}"
    with store_psycopg.connect_managed(project.root) as app:
        app.autocommit = True
        digest = snapshots.register_snapshot(app, snapshots.SnapshotRegistration(
            json.dumps(book, sort_keys=True).encode(), polymarket.SOURCE_KIND_CLOB, url,
            received))["content_sha256"]
        request = replace(_eth_request(), market_id=slug, question=question, book=[(0.56, 5000.0)],
                          side=side.lower(), buy_side=side.lower(), book_time=received, now=decided,
                          yes_token=yes, no_token=no, buy_token=token)
        scan = replace(_scan_run(), team=team, code_commit=project.head, operation_key=scan_key,
                       started_at=received, finished_at=entered)
        decision = replace(_evidence(slug), observed_at=decided, market_ref_kind="condition_id",
                           market_ref=condition, side=side,
                           token_ref=(no if side == "YES" else yes) if mismatch_token else token,
                           bid=Decimal("0.54"), ask=Decimal("0.56"), source_kind="clob_book",
                           source_ref=f"{url}#{digest}", snapshot_sha256=digest)
        candidate = replace(_candidate(slug), team=team, side=side, condition_id=condition,
                            market_id=slug, yes_token_id=yes, no_token_id=no, evaluated_at=decided,
                            input_payload=replay.replay_bundle_payload(request), snapshot_refs=[digest],
                            entry=replace(_entry_submission(), entered_at=entered), price_evidence=(decision,))
        store_psycopg.record_command_run_event(app, store.CommandRunEventSubmission(
            operation_key=scan_key, phase="STARTED", command="run-scan", occurred_at=received,
            code_commit=project.head))
        result = store_psycopg.record_scan(app, store.ScanSubmission(scan=scan, candidates=(candidate,)))
        candidate_id = result["candidate_ids"][candidate.candidate_key]
        store_psycopg.record_command_run_event(app, store.CommandRunEventSubmission(
            operation_key=scan_key, phase="SUCCEEDED", command="run-scan", occurred_at=entered,
            code_commit=project.head, scan_id=result["scan_id"]))
        entry_id, = app.execute("SELECT entry_id FROM papersim.entries WHERE candidate_id = %s",
                                (candidate_id,)).fetchone()
    return {"entry_id": entry_id, "candidate_id": candidate_id, "scan_id": result["scan_id"],
            "condition_id": condition, "market_id": slug, "market_slug": slug, "yes_token_id": yes,
            "no_token_id": no, "question": question, "entered_at": entered}


def _d1_seed_settlement_outcome(project, entry, outcome, *, observed_at=None, confirmed_at=True,
                                proven=True, source_prefix=""):
    from polymarket_alpha_lab.papersim import snapshots, store_psycopg
    from polymarket_alpha_lab.papersim.adapters import polymarket

    observed = observed_at or entry["entered_at"] + datetime.timedelta(minutes=2)
    confirmed = observed + datetime.timedelta(seconds=5) if confirmed_at is True else confirmed_at
    # Like the original integration, public Gamma lists No first. Independent
    # outcomes must reconstruct the side map using labels, not array position.
    prices = {"YES": ["0", "1"], "NO": ["1", "0"]}[outcome]
    closed = entry["entered_at"] + datetime.timedelta(seconds=30)
    body = {"id": entry["market_id"], "conditionId": entry["condition_id"], "slug": entry["market_slug"],
            "question": entry["question"], "outcomes": '["No","Yes"]', "outcomePrices": json.dumps(prices),
            "clobTokenIds": json.dumps([entry["no_token_id"], entry["yes_token_id"]]), "closed": True,
            "endDate": closed.isoformat(), "closedTime": closed.isoformat()}
    url = f"{polymarket.GAMMA_MARKET_PREFIX}{entry['market_slug']}"
    with store_psycopg.connect_managed(project.root) as app:
        digest = None
        if proven:
            digest = snapshots.register_snapshot(app, snapshots.SnapshotRegistration(
                json.dumps(body, sort_keys=True).encode(), polymarket.SOURCE_KIND_GAMMA, url,
                observed))["content_sha256"]
        observation = store.MarketOutcomeObservation(
            **{field: entry[field] for field in (
                "condition_id", "market_id", "market_slug", "yes_token_id", "no_token_id")},
            outcome=outcome, observed_at=observed, confirmed_at=confirmed, source_kind="gamma_close",
            source_ref=source_prefix + url, source_snapshot_sha256=digest)
        return store_psycopg.record_market_outcome(app, observation)["outcome_id"]


def _d1_register_settle_slot(project, entry_ids, *, team="crypto_eth", operator="unattended"):
    from polymarket_alpha_lab.papersim import snapshots, store_psycopg
    from polymarket_alpha_lab.papersim.d1compare import operations

    config = hashlib.sha256(operations.document_bytes({"team": team, "operator": operator})).hexdigest()
    with store_psycopg.connect_managed(project.root) as app:
        interval = json.loads(snapshots.read_snapshot(app, project.interval_sha256)["content"])
        scheduled = max(datetime.datetime.fromisoformat(interval["starts_at"]),
                        store_psycopg.d1_database_time(app) + datetime.timedelta(milliseconds=250))
        document = {"version": 1, "instance_id": project.instance_id, "interval_sha256": project.interval_sha256,
                    "scheduled_for": scheduled, "deadline": scheduled + datetime.timedelta(seconds=75),
                    "write_deadline": scheduled + datetime.timedelta(seconds=110), "leg": "new",
                    "command": "settle", "team": team, "code_commit": project.head, "config_digest": config,
                    "input_evidence": [], "scope": {"entry_ids": list(entry_ids)}}
        key = operations.slot_operation_key(document)
        _d1_register_binding(
            app, document=document, kind="d1_operation_slot", source="d1:operation:slot:v1",
            command="d1-register-slot", key_for=lambda _digest: f"d1:slot-binding:v1:{key.rsplit(':', 1)[1]}",
            scheduled_for=scheduled, code_commit=project.head, config_digest=config)
    return key, scheduled


def _d1_settle_cli(project, slot, *, attempt=1, team="crypto_eth", operator="unattended"):
    key, scheduled = slot
    wait = (scheduled - datetime.datetime.now(datetime.UTC)).total_seconds()
    if wait > 0:
        time.sleep(wait)
    return _d1_child(project, ["settle", "--managed", "--repo-root", str(project.root), "--team", team,
                               "--operator", operator, "--operation-key", key, "--attempt", str(attempt)])


def _d1_witness(conn, refs):
    from polymarket_alpha_lab.papersim import snapshots

    digest, length = (ref.split(":", 1)[1] for ref in refs)
    row = snapshots.read_snapshot(conn, digest)
    body = row["content"]
    assert hashlib.sha256(body).hexdigest() == digest
    assert len(body) == row["byte_length"] == int(length)
    return json.loads(body)


def _d1_receipts(conn, key):
    return conn.execute(
        "SELECT run_event_id, attempt, phase, occurred_at, error_code, output_evidence "
        "FROM papersim.command_run_events WHERE operation_key = %s ORDER BY run_event_id", (key,)).fetchall()


def _d1_entry_sql(conn, entry_ids):
    selected = (list(entry_ids),)
    rows = conn.execute(
        "SELECT entry_id, settlement_id, source_outcome_id, classification, outcome_label, win_loss, "
        "proceeds, pnl_gross, fee_total, fee_source, pnl_net, confirmed_by, confirmed_at, is_legacy, "
        "paper_only, report_only, readonly FROM papersim.settlements WHERE entry_id = ANY(%s) ORDER BY entry_id",
        selected).fetchall()
    cash = conn.execute(
        "SELECT event_id, event_key, entry_id, scan_id, reason, currency, amount, occurred_at, reverses_event_id, "
        "is_legacy, paper_only, report_only, readonly FROM papersim.bankroll_events "
        "WHERE entry_id = ANY(%s) OR reverses_event_id IN "
        "(SELECT event_id FROM papersim.bankroll_events WHERE entry_id = ANY(%s)) ORDER BY event_id",
        (*selected, *selected)).fetchall()
    return rows, cash


def _d1_cash_failure(project, entry_id, *, install):
    from polymarket_alpha_lab.papersim import store_psycopg

    with store_psycopg.connect_owner(project.root) as owner, owner.transaction():
        if install:
            owner.execute("""
                CREATE FUNCTION papersim.fixture_s1_abort_cash() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.entry_id::text = TG_ARGV[0] AND NEW.reason = 'SETTLE_NATURAL' THEN
                        RAISE EXCEPTION 'fixture_s1_abort_cash';
                    END IF;
                    RETURN NEW;
                END;
                $$
            """)
            assert type(entry_id) is int and entry_id > 0
            owner.execute("CREATE TRIGGER fixture_s1_abort_cash BEFORE INSERT ON papersim.bankroll_events "
                          f"FOR EACH ROW EXECUTE FUNCTION papersim.fixture_s1_abort_cash('{entry_id}')")
        else:
            owner.execute("DROP TRIGGER fixture_s1_abort_cash ON papersim.bankroll_events")
            owner.execute("DROP FUNCTION papersim.fixture_s1_abort_cash()")


@_D1_NATIVE
def test_d1_settle_recovers_committed_pending_and_failed_items_in_a_new_cli(d1_capture_project):
    from polymarket_alpha_lab.papersim import store_psycopg
    from polymarket_alpha_lab.papersim.d1compare import operations

    project = d1_capture_project
    entries = [_d1_seed_settlement_entry(project, f"s1-recovery-{label}") for label in "abc"]
    a, b, c = (entry["entry_id"] for entry in entries)
    first_outcomes = [_d1_seed_settlement_outcome(project, entry, token)
                      for entry, token in zip(entries, ("NO", "YES", "YES"), strict=True)]
    with store_psycopg.connect_managed(project.root) as app:
        b_open, b_debit = app.execute(
            "SELECT event_id, amount FROM papersim.bankroll_events WHERE event_key = %s", (f"entry:{b}:cash",)
        ).fetchone()
        b_reversal = _insert_event(app, f"s1-reverse:{b}", minute=7, amount=b_debit.copy_negate(),
                                   reason="REVERSAL", reverses=b_open)
    slot = _d1_register_settle_slot(project, [a, b, c])
    key, _scheduled = slot
    _d1_cash_failure(project, c, install=True)
    try:
        first = _d1_settle_cli(project, slot)
        assert (first.returncode, first.stdout, first.stderr.strip()) == (
            1, "", "settle: FAIL: d1_operation_postgres_P0001")
        with store_psycopg.connect_managed(project.root) as reader, reader.transaction():
            reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            assert reader.execute("SELECT current_user").fetchone() == ("pal_app",)
            settlements, initial_cash = _d1_entry_sql(reader, (a, b, c))
            settlement_a, = settlements
            assert settlement_a[2:12] == (first_outcomes[0], "NATURAL", "NO", "loss", Decimal("0"),
                                           Decimal("-250"), Decimal("7.70"), "schedule", Decimal("-257.70"),
                                           "unattended")
            first_terminal, = [row for row in initial_cash if row[1].startswith("settle:")]
            assert first_terminal[1:9] == (f"settle:{a}", a, None, "SETTLE_NATURAL", "USD", Decimal(0),
                                           settlement_a[12], None)
            assert first_terminal[9:] == (False, True, True, True)
            first_receipts = _d1_receipts(reader, key)
            assert [(row[1], row[2], row[4]) for row in first_receipts] == [
                (1, "STARTED", None), (1, "PREPARED", None), (1, "FAILED", "d1_operation_postgres_P0001")]
            prepared = _d1_witness(reader, first_receipts[1][5])
            assert datetime.datetime.fromisoformat(prepared["read_finished_at"]) <= first_receipts[1][3]
            frozen = prepared["plan"]["targets"]
            assert [row["identity"]["entry_id"] for row in frozen] == [a, b, c]
            assert [row["source_outcome_id"] for row in frozen] == first_outcomes
            assert frozen[1]["cash_discrepancy"] == {"state": "metadata", "event_ids": [b_open],
                                                      "actual_amount": str(b_debit), "expected_amount": str(b_debit)}
            assert [row["event_id"] for row in frozen[1]["cash_rows"]] == [b_open, b_reversal]
            item_keys = [operations._target_key(key, target) for target in frozen]
            item_rows = [_d1_receipts(reader, item_key) for item_key in item_keys]
            assert [[(row[1], row[2]) for row in rows] for rows in item_rows] == [
                [(1, "SUCCEEDED")], [(1, "SUCCEEDED")], [(1, "FAILED")]]
            original_a, original_b, first_error = [_d1_witness(reader, rows[0][-1]) for rows in item_rows]
            assert original_a["status"] == "SETTLED" and original_a["business_ids"] == {
                "settlement_id": settlement_a[1], "cash_event_id": first_terminal[0]}
            assert original_b["status"] == "PENDING" and original_b["cash_discrepancy"] == frozen[1]["cash_discrepancy"]
            assert first_error["reason"] == "d1_operation_postgres_P0001"
    finally:
        _d1_cash_failure(project, c, install=False)

    late = _d1_seed_settlement_outcome(project, entries[2], "NO",
                                       observed_at=entries[2]["entered_at"] + datetime.timedelta(minutes=3))
    outsider = _d1_seed_settlement_entry(project, "s1-recovery-new")["entry_id"]
    second = _d1_settle_cli(project, slot, attempt=2)
    assert second.returncode == 0, second.stderr
    summary = json.loads(second.stdout)
    assert summary["snapshot_entries"] == 3
    assert [(row["entry_id"], row["inserted"]) for row in summary["settled"]] == [(a, False), (c, True)]
    assert summary["pending"] == [{"entry_id": b, "reason": "entry_cash_mismatch",
                                   "cash_discrepancy": frozen[1]["cash_discrepancy"]}]
    assert summary["items"][0]["first_write"]["run_event_id"] == item_rows[0][0][0]
    assert summary["items"][0]["first_write_at"] == original_a["write_finished_at"]
    with store_psycopg.connect_managed(project.root) as reader, reader.transaction():
        reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        settlements, cash = _d1_entry_sql(reader, (a, b, c, outsider))
        assert [row[0] for row in settlements] == [a, c]
        assert settlements[0] == settlement_a and first_terminal in cash
        c_result = settlements[1]
        assert c_result[2:10] == (first_outcomes[2], "NATURAL", "YES", "win", Decimal("446.4285714285714"),
                                  Decimal("196.4285714285714"), Decimal("7.70"), "schedule")
        assert c_result[2] != late and c_result[10] == Decimal("188.7285714285714")
        c_terminal, = [row for row in cash if row[1] == f"settle:{c}"]
        assert c_terminal[2:9] == (c, None, "SETTLE_NATURAL", "USD", c_result[6], c_result[12], None)
        assert [row for row in cash if row[1] != f"entry:{outsider}:cash" and row != c_terminal] == initial_cash
        retry_receipts = _d1_receipts(reader, key)
        assert retry_receipts[:3] == first_receipts
        assert [(row[1], row[2]) for row in retry_receipts[3:]] == [
            (2, "STARTED"), (2, "RECOVERY_READ"), (2, "SUCCEEDED")]
        recovery = _d1_witness(reader, retry_receipts[4][5])
        assert recovery["prepared"]["run_event_id"] == first_receipts[1][0]
        assert recovery["first_failure"]["run_event_id"] == first_receipts[2][0]
        assert recovery["first_item_failure"]["run_event_id"] == item_rows[2][0][0]
    repeat = _d1_settle_cli(project, slot, attempt=2)
    assert repeat.returncode == 0, repeat.stderr
    assert json.loads(repeat.stdout) == summary


@_D1_NATIVE
def test_d1_settle_rechecks_each_token_and_opening_cash_after_prepared(d1_capture_project):
    from polymarket_alpha_lab.papersim import store_psycopg
    from polymarket_alpha_lab.papersim.d1compare import operations

    project = d1_capture_project
    entries = [_d1_seed_settlement_entry(project, f"s1-lock-{label}", side=side)
               for label, side in (("token", "NO"), ("cash", "YES"), ("healthy", "YES"))]
    b, c, a = (entry["entry_id"] for entry in entries)
    outcomes = [_d1_seed_settlement_outcome(project, entry, "YES") for entry in entries]
    slot = _d1_register_settle_slot(project, [b, c, a])
    key, _scheduled = slot

    with store_psycopg.connect_managed(project.root) as writer, store_psycopg.connect_managed(project.root) as observer:
        writer.autocommit = observer.autocommit = True
        with writer.transaction():
            store_psycopg.d1_lock_settlement_entry(writer, b)
            box, thread = _midflight_reader(lambda: _d1_settle_cli(project, slot))
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                receipts = _d1_receipts(observer, key)
                blocked = observer.execute(
                    "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND NOT granted "
                    "AND %s = ANY(pg_blocking_pids(pid))", (writer.info.backend_pid,)).fetchall()
                if any(row[2] == "PREPARED" for row in receipts) and blocked:
                    break
                assert thread.is_alive(), box
                time.sleep(0.02)
            else:
                pytest.fail("managed settle never waited for the entry lock after PREPARED")

            original = store_psycopg.decision_time_evidence(writer, entries[0]["candidate_id"])
            poison, = writer.execute(
                "INSERT INTO papersim.candidate_price_evidence (evidence_key, candidate_id, evidence_role, "
                "observed_at, market_ref_kind, market_ref, token_ref, side, bid, ask, snapshot_sha256, "
                "source_kind, source_ref) VALUES (%s, %s, 'DECISION', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING evidence_id",
                ("fixture-s1-contradictory-decision", original["candidate_id"], original["observed_at"],
                 original["market_ref_kind"], original["market_ref"], entries[0]["yes_token_id"], original["side"],
                 original["bid"], original["ask"], original["snapshot_sha256"], original["source_kind"],
                 original["source_ref"])).fetchone()
            opening, delta = writer.execute(
                "SELECT event_id, amount FROM papersim.bankroll_events WHERE event_key = %s", (f"entry:{c}:cash",)
            ).fetchone()
            reversal = _insert_event(writer, f"s1-lock-reversal:{c}", minute=7, amount=delta.copy_negate(),
                                     reason="REVERSAL", reverses=opening)
        thread.join(75)
        assert not thread.is_alive()
        assert "error" not in box, box
        completed = box["result"]
        assert completed.returncode == 0, completed.stderr
        summary = json.loads(completed.stdout)
        assert [row["entry_id"] for row in summary["settled"]] == [a]
        assert [(row["entry_id"], row["reason"]) for row in summary["pending"]] == [
            (b, "d1_settlement_decision_binding_conflict"), (c, "entry_cash_mismatch")]
        prepared_row = next(row for row in _d1_receipts(observer, key) if row[2] == "PREPARED")
        plan = _d1_witness(observer, prepared_row[5])["plan"]["targets"]
        assert [row["source_outcome_id"] for row in plan] == outcomes
        assert [row["status"] for row in plan] == ["READY", "READY", "READY"]
        original_token, actual_cash, _healthy = summary["items"]
        assert plan[0]["binding"]["buy"]["buy_token_id"] == entries[0]["no_token_id"]
        assert [row["evidence_id"] for row in plan[0]["candidate"]["decisions"]] == [original["evidence_id"]]
        token_read = original_token["locked_read"]
        assert [row["evidence_id"] for row in token_read["binding"]["candidate"]["decisions"]] == [
            original["evidence_id"], poison]
        assert prepared_row[3] <= datetime.datetime.fromisoformat(token_read["read_started_at"])
        assert token_read["read_started_at"] <= token_read["read_finished_at"]
        assert [row["event_id"] for row in plan[1]["cash_rows"]] == [opening]
        assert [row["event_id"] for row in actual_cash["locked_read"]["cash_rows"]] == [opening, reversal]
        assert actual_cash["cash_discrepancy"] == {"state": "metadata", "event_ids": [opening],
                                                   "expected_amount": str(delta), "actual_amount": str(delta)}
        assert actual_cash["locked_read"]["read_finished_at"] <= actual_cash["locked_read"]["cash_guard_at"]
        for target in plan[:2]:
            item, = _d1_receipts(observer, operations._target_key(key, target))
            proof = _d1_witness(observer, item[5])
            assert proof["status"] == "PENDING"
            assert proof["business_ids"] == {"cash_event_id": None, "settlement_id": None}
        settlements, cash = _d1_entry_sql(observer, (a, b, c))
        assert len(settlements) == 1 and settlements[0][:6] == (
            a, summary["settled"][0]["settlement_id"], outcomes[2], "NATURAL", "YES", "win")
        assert [row[2] for row in cash if row[4] in store.SETTLEMENT_CASH_REASONS.values()] == [a]


@_D1_NATIVE
def test_d1_settle_validates_opposite_purchases_and_preexisting_source_per_entry(d1_capture_project):
    from polymarket_alpha_lab.papersim import store_psycopg

    project = d1_capture_project
    entered = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=30)
    yes = _d1_seed_settlement_entry(project, "s1-same-market", entered_at=entered)
    no = _d1_seed_settlement_entry(project, "s1-same-market", entered_at=entered, side="NO")
    contradictory = _d1_seed_settlement_entry(project, "s1-same-market", side="NO", mismatch_token=True,
                                               entered_at=entered)
    no_original = _d1_seed_settlement_entry(project, "s1-no-original", entered_at=entered)
    new_without_origin = _d1_seed_settlement_entry(project, "s1-no-first-witness", entered_at=entered)
    reentry = _d1_seed_settlement_entry(project, "s1-same-market", entered_at=entered + datetime.timedelta(minutes=5))
    foreign = _d1_seed_settlement_entry(project, "s1-foreign", entered_at=entered, team="other_team")
    outcome = _d1_seed_settlement_outcome(project, yes, "YES")
    unproven = _d1_seed_settlement_outcome(project, no_original, "YES", proven=False)
    no_witness = _d1_seed_settlement_outcome(project, new_without_origin, "YES",
                                            source_prefix="d1:public-resolution:v1:")
    _d1_seed_settlement_outcome(project, foreign, "YES")
    ids = [row["entry_id"] for row in (yes, no, contradictory, no_original, new_without_origin, reentry, foreign)]
    slot = _d1_register_settle_slot(project, ids)
    result = _d1_settle_cli(project, slot)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert [row["entry_id"] for row in summary["settled"]] == ids[:2]
    assert [(row["entry_id"], row["reason"]) for row in summary["pending"]] == list(zip(ids[2:], (
        "d1_settlement_decision_binding_conflict", "d1_settlement_snapshot_missing",
        "d1_settlement_first_write_unproven", "no_captured_outcome", "d1_settlement_entry_missing"), strict=True))
    with store_psycopg.connect_managed(project.root) as reader, reader.transaction():
        reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        settlements, cash = _d1_entry_sql(reader, ids)
        assert [(row[0], row[2], row[5], row[6]) for row in settlements] == [
            (ids[0], outcome, "win", Decimal("446.4285714285714")), (ids[1], outcome, "loss", Decimal(0))]
        terminal = [row for row in cash if row[4] in store.SETTLEMENT_CASH_REASONS.values()]
        assert [(row[1], row[2], row[4], row[5], row[6], row[8]) for row in terminal] == [
            (f"settle:{ids[0]}", ids[0], "SETTLE_NATURAL", "USD", settlements[0][6], None),
            (f"settle:{ids[1]}", ids[1], "SETTLE_NATURAL", "USD", Decimal(0), None)]
        prepared = next(row for row in _d1_receipts(reader, slot[0]) if row[2] == "PREPARED")
        targets = _d1_witness(reader, prepared[5])["plan"]["targets"]
        assert [target["source_outcome_id"] for target in targets] == [outcome, outcome, outcome, unproven,
                                                                      no_witness, None, None]
        assert targets[0]["binding"]["buy"]["buy_token_id"] == yes["yes_token_id"]
        assert targets[1]["binding"]["buy"]["buy_token_id"] == no["no_token_id"]
        assert all(target["binding"]["winning_token_id"] == yes["yes_token_id"] for target in targets[:2])
        assert all(target["binding"]["outcome_first_write"] is None for target in targets[:2])
        assert targets[5]["outcomes"][0]["outcome_id"] == outcome
        assert targets[5]["cash_rows"][0]["event_key"] == f"entry:{ids[5]}:cash"
