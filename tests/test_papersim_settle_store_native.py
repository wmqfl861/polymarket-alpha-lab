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
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

import pytest

from polymarket_alpha_lab.papersim import migrations as pm
from polymarket_alpha_lab.papersim import settle, store

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN,
                            reason='explicit native papersim proof is opt-in')


def at(minutes: int) -> datetime.datetime:
    return datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.UTC) \
        + datetime.timedelta(minutes=minutes)


def _connect(dsn, *, autocommit=True):
    psycopg = pytest.importorskip('psycopg')
    connection = psycopg.connect(dsn)
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
    psycopg = pytest.importorskip('psycopg')
    admin = _connect(OWNER_DSN)
    name = 'pal_papersim_b3settle_' + uuid.uuid4().hex[:10]
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
    assert leg == (Decimal('250'), 'VOIDED_REFUND')


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

def _wait_reader_blocked(admin, reader_pid: int, deadline_s: float = 30.0) -> None:
    """Poll until the reader backend is actually waiting on a lock."""
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        with admin.cursor() as cursor:
            cursor.execute('SELECT wait_event_type FROM pg_stat_activity '
                           'WHERE pid = %s', (reader_pid,))
            row = cursor.fetchone()
        if row is not None and row[0] == 'Lock':
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
        store_psycopg.record_scan(writer, store.ScanSubmission(
            scan=_scan_run(), candidates=(_candidate('fx29a-' + uuid.uuid4().hex[:10]),)))

        box, thread = _midflight_reader(
            lambda: store_psycopg.bankroll_daily_check(reader, request))
        try:
            _wait_reader_blocked(admin, reader.info.backend_pid)
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
        store_psycopg.record_scan(writer, store.ScanSubmission(
            scan=_scan_run(), candidates=(_candidate('fx29b-' + uuid.uuid4().hex[:10]),)))
        store_psycopg.record_market_outcome(writer, _outcome_observation(slug, 'YES'))

        box, thread = _midflight_reader(
            lambda: cli._evaluation_candidates_from_store(reader))
        try:
            _wait_reader_blocked(admin, reader.info.backend_pid)
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
        store_psycopg.record_market_outcome(writer, _outcome_observation(slug, 'YES'))

        box, thread = _midflight_reader(
            lambda: _run(reader, repo_root, operation_key='settle|crypto_eth|fx29c'))
        try:
            _wait_reader_blocked(admin, reader.info.backend_pid)
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
