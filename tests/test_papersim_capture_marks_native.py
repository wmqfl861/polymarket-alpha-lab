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

from dataclasses import dataclass, replace
import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
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


# These tests run the public transport, B1, and B2 entry points as separate
# processes in an isolated project. The existing tests above retain their
# lower-level explicit-DSN fixtures and pack contracts.
_D1_NATIVE = pytest.mark.skipif(sys.platform != "linux" or not ENABLED,
                                reason="explicit Linux native PostgreSQL proof is opt-in")
_D1_TEAM = "d1-b2-fixture"
_D1_QUEUE = "crypto/eth-terminal"
_D1_LINEAGE = "crypto/terminal"
_UTC = datetime.UTC
_SECOND = datetime.timedelta(seconds=1)
_MILLISECOND = datetime.timedelta(milliseconds=1)
_QUOTE = {"bid": 0.55, "ask": 0.58, "mid": None, "last": None}
_QUOTES = {
    "quote": _QUOTE,
    "bid-only": _QUOTE | {"ask": None},
    "last-zero": {"bid": None, "ask": None, "mid": None, "last": 0},
    "last-one": {"bid": None, "ask": None, "mid": None, "last": 1},
    "empty": {"bid": None, "ask": None, "mid": None, "last": None},
    "unavailable": {"bid": None, "ask": None, "mid": None, "last": None},
}


@dataclass(frozen=True)
class _D1CaptureProject:
    root: Path
    postgres: object
    temporary: Path
    head: str
    instance_id: str
    interval_sha256: str


@dataclass(frozen=True)
class _D1CaptureCase:
    project: _D1CaptureProject
    directory: Path
    candidate_ids: tuple[int, ...]
    scan_id: int
    scan_key: str
    scan_config: Path
    capture_manifest_sha256: str
    marks: tuple[dict, ...]
    boundary: datetime.datetime | None


@dataclass(frozen=True)
class _D1CaptureSlot:
    key: str
    config: Path
    pack: Path
    scheduled_for: datetime.datetime
    document: dict


def _d1_json_path(directory, name, value):
    path = directory / name
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False), encoding="utf-8")
    return path


def _d1_register_binding(conn, *, document, kind, source, command, key_for,
                         scheduled_for=None, code_commit=None, config_digest=None):
    from polymarket_alpha_lab.papersim import snapshots, store_psycopg
    from polymarket_alpha_lab.papersim.d1compare import operations

    at = store_psycopg.d1_database_time(conn)
    content = operations.document_bytes(document)
    digest = snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
        content, kind, source, at))["content_sha256"]
    store_psycopg.record_command_run_event(conn, store.CommandRunEventSubmission(
        operation_key=key_for(digest), attempt=1, phase="SUCCEEDED", command=command,
        scheduled_for=scheduled_for, occurred_at=at, code_commit=code_commit,
        config_digest=config_digest, output_evidence=[
            f"snapshot_sha256:{digest}", f"snapshot_byte_length:{len(content)}"]))
    return digest


@pytest.fixture(scope="module")
def d1_capture_project(tmp_path_factory):
    from polymarket_alpha_lab.papersim import store_psycopg
    from polymarket_alpha_lab.project_postgres.runtime import import_runtime_directory
    from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
    from tests.native_port_selection import pick_port

    base = tmp_path_factory.mktemp("b2-managed-pg18")
    with tempfile.TemporaryDirectory(dir=base, prefix="b2-fixture-") as private:
        root, temporary = Path(private) / "checkout", Path(private) / "inputs"
        temporary.mkdir(mode=0o700)
        head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout", "--", str(ROOT), str(root)],
                       check=True, capture_output=True, timeout=90)
        subprocess.run(["git", "-C", str(root), "checkout", "--quiet", "--detach", head],
                       check=True, capture_output=True, timeout=90)
        assert not subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"])
        prefix = Path(os.environ["POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX"])
        assert prefix.is_absolute() and (prefix / "bin/postgres").is_file()
        assert import_runtime_directory(root, prefix).split(".")[0] == "18"
        postgres = ProjectPostgres(root)
        assert postgres.initialize(port=pick_port())["status"] == "initialized"
        try:
            assert postgres.up()["started_here"] is True
            with store_psycopg.connect_owner(root) as owner:
                migration_count = len(pm.papersim_migration_catalog(root))
                assert pm.apply_pending_migrations(owner, root)["applied"] == migration_count
            info = store_psycopg.read_instance(root)
            with store_psycopg.connect_managed(root) as conn:
                assert conn.execute("SELECT current_user").fetchone() == ("pal_app",)
                starts_at = store_psycopg.d1_database_time(conn) + _SECOND
                interval = _d1_register_binding(
                    conn,
                    document={"version": 1, "instance_id": info["instance_id"],
                              "starts_at": starts_at, "ends_at": starts_at + datetime.timedelta(hours=1)},
                    kind="d1_operation_interval", source="d1:operation:interval:v1",
                    command="d1-register-interval", key_for=lambda digest: f"d1:interval-binding:v1:{digest}",
                )
            yield _D1CaptureProject(root, postgres, temporary, head, info["instance_id"], interval)
        finally:
            assert postgres.down()["status"] == "stopped"


def _d1_child(project, args, *, code=None):
    # Use only the verified detached source tree and the managed instance at
    # its normal root. The child inherits no source, provider or PG settings.
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TZ", "USER", "LOGNAME")
           if key in os.environ}
    env.update(PYTHONPATH=f"{project.root / 'src'}:{project.root}",
               PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    invocation = [sys.executable, "-B"]
    invocation.extend(["-m", "polymarket_alpha_lab.papersim.cli"] if code is None else ["-c", code])
    return subprocess.run([*invocation, *args], cwd=project.root, env=env,
                          capture_output=True, text=True, timeout=75)


def _d1_public_transport():
    """Child-process entry. Replace the public transport, then use the real CLI."""
    import runpy
    from urllib.error import URLError
    from urllib.parse import parse_qs, urlsplit

    from polymarket_alpha_lab.papersim.adapters import polymarket
    from tests.test_papersim_adapter_polymarket import _response

    root, inputs = Path(sys.argv[1]), Path(sys.argv[2])
    assert Path(polymarket.__file__).is_relative_to(root)
    document = json.loads(inputs.read_text(encoding="utf-8"))
    epoch = datetime.datetime(1970, 1, 1, tzinfo=_UTC)

    class PublicFixture:
        def open(self, url, *, timeout_seconds):
            split = urlsplit(url)
            query = parse_qs(split.query)
            if split.path == "/events" and query["tag_slug"] == ["crypto"]:
                markets = document["markets"]
                payload = ([{"id": "fixture-event", "slug": "fixture-event", "markets": markets}]
                           if query["offset"] == ["0"] else [])
            elif split.path == "/book":
                token, = query["token_id"]
                payload = document["books"][token]
                if payload is None:
                    raise URLError("fixture public response unavailable")
                payload = dict(payload)
                fixed = payload.pop("fixture_source_at", None)
                stamp = (datetime.datetime.now(_UTC) - 100 * _MILLISECOND
                         if fixed is None else datetime.datetime.fromisoformat(fixed))
                if fixed is not None and stamp >= datetime.datetime.now(_UTC):
                    time.sleep((stamp - datetime.datetime.now(_UTC)) / _SECOND + 0.01)
                payload["timestamp"] = str((stamp - epoch) // _MILLISECOND)
            else:
                raise AssertionError("unexpected public request in local transport fixture")
            return _response((" \n" + json.dumps(payload, indent=2) + "\n").encode(), url=url)

    polymarket.BoundedPublicOpener = PublicFixture
    script = root / "tools" / "d1_compare.py"
    sys.argv = [str(script), *sys.argv[3:]]
    runpy.run_path(str(script), run_name="__main__")


def _d1_seal_fixture(project, path, previous_window_start=None):
    from polymarket_alpha_lab.papersim.d1compare import acquire

    now = datetime.datetime.now(_UTC)
    start = now - datetime.timedelta(seconds=20)
    if previous_window_start is not None:
        assert start > previous_window_start
    result = _d1_child(project, [str(project.root), str(path), "capture-inputs", "--repo-root",
                                str(project.root), "--allow-public-fetch", "--tag-slug", "crypto",
                                "--window-start", start.isoformat(),
                                "--window-end", (now + datetime.timedelta(seconds=25)).isoformat()],
                       code=("from tests.test_papersim_capture_marks_native import _d1_public_transport; "
                             "_d1_public_transport()"))
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["capture_version"] == 2
    assert receipt["coverage"]["window_status"] == "sealed_inside"
    manifest, entries = acquire.read_capture(repo_root=project.root, batch_sha256=receipt["batch_sha256"])
    assert all(market["binding_status"] == "explicit_yes_no" for market in manifest["markets"])
    return receipt, {entry.request_meta["query"]["token_id"]: entry for entry in entries
                     if "token_id" in entry.request_meta["query"]}


def _d1_scan_candidate_ids(scan, candidate_keys):
    by_key = scan["candidate_ids"]
    assert type(by_key) is dict and set(by_key) == set(candidate_keys)
    candidate_ids = tuple(by_key[key] for key in candidate_keys)
    assert all(type(candidate_id) is int and candidate_id > 0 for candidate_id in candidate_ids)
    assert len(set(candidate_ids)) == len(candidate_ids)
    return candidate_ids


def _d1_seed_capture_case(project, label, variants=("quote", "bid-only", "empty"), *, boundary_cases=False,
                          time_to_expiry=datetime.timedelta(days=2)):
    from polymarket_alpha_lab import replay
    from polymarket_alpha_lab.papersim import snapshots, store_psycopg
    from polymarket_alpha_lab.papersim.d1compare import journal
    from tests.test_papersim_scan_store_native import _eth_request

    case_dir = project.temporary / label
    case_dir.mkdir()
    expiry = datetime.datetime.now(_UTC) + time_to_expiry
    b1_books, b2_books, markets = {}, {}, []

    def book(token, condition, bids, asks, *, last=None):
        def levels(prices):
            return [{"price": price, "size": "5000.00"} for price in prices]

        payload = {"asset_id": token, "market": condition, "bids": levels(bids), "asks": levels(asks)}
        if last is not None:
            payload["last_trade_price"] = last
        return payload

    token_base = int(hashlib.sha256(label.encode()).hexdigest()[:12], 16)
    for index, variant in enumerate(variants):
        market_id = f"{label}-market-{index}"
        condition = "0x" + hashlib.sha256(market_id.encode()).hexdigest()
        yes, no = str(token_base + 2 * index + 1), str(token_base + 2 * index)
        markets.append({
            "id": market_id, "conditionId": condition, "slug": market_id,
            "question": "Will Ethereum be below 2,000 on the settlement date?",
            "description": "Ethereum terminal below 2,000.", "endDate": expiry.isoformat(),
            "outcomes": '["No","Yes"]', "clobTokenIds": json.dumps([no, yes]), "outcomePrices": '["0.85","0.15"]',
        })
        b1_books[yes] = book(yes, condition, ["0.12"], ["0.15"])
        b2_books[no] = b1_books[no] = book(no, condition, ["0.71"], ["0.80"])
        quotes = _QUOTES[variant]
        b2_books[yes] = None if variant == "unavailable" else book(
            yes, condition, ["0.51", "0.55"] if quotes["bid"] is not None else [],
            ["0.63", "0.58"] if quotes["ask"] is not None else [],
            last=None if quotes["last"] is None else str(quotes["last"]))
    b1, b1_entries = _d1_seal_fixture(project, _d1_json_path(
        case_dir, "b1-public.json", {"markets": markets, "books": b1_books}))
    b1_at = datetime.datetime.fromisoformat(b1["decision_as_of"])
    boundary = b1_at.replace(microsecond=0) + datetime.timedelta(seconds=10) if boundary_cases else None
    records = []
    with store_psycopg.connect_managed(project.root) as conn:
        for index, market in enumerate(markets):
            no, yes = json.loads(market["clobTokenIds"])
            entry = b1_entries[yes]
            request = replace(_eth_request(), market_id=market["id"], question=market["question"],
                              fixture=market["slug"], book_time=entry.utc_fetched_at, now=b1_at,
                              yes_token=yes, no_token=no, buy_token=yes)
            ladder = snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
                replay.bound_book_bytes(request), "d1_fixture_b1_normalized_asks",
                journal.entry_source_ref(entry), entry.utc_fetched_at))["content_sha256"]
            record = {
                "queue": _D1_QUEUE, "candidate_key": f"buy-yes-{index}", "team": _D1_TEAM,
                "side": "YES", "market_kind": "terminal", "lineage": _D1_LINEAGE, "model_name": "crypto",
                "evaluated_at": b1_at.isoformat(), "condition_id": market["conditionId"], "market_id": market["id"],
                "market_slug": market["slug"], "yes_token_id": yes, "no_token_id": no, "market_type": "terminal",
                "median_probability": 0.2224, "snapshot_refs": [b1["batch_sha256"], entry.content_sha256, ladder],
                "book_snapshot_sha256": ladder, "decision_snapshot_sha256": entry.content_sha256,
                "source_kind": "clob_book", "source_ref": journal.entry_source_ref(entry),
                "request_bundle": replay.replay_bundle_payload(request),
            }
            if not boundary_cases or index < 3:
                start = (boundary or expiry).isoformat()
                record["events"] = {"market_class": "crypto", "asset": "ETH", "expiry_at": start, "event_start_at": start}
            if boundary_cases and index < 3:
                offsets = (0, 1, -1)
                b2_books[yes]["fixture_source_at"] = (boundary + offsets[index] * _MILLISECOND).isoformat()
            records.append(record)
    scan_pack = _d1_json_path(case_dir, "scan-pack.json", records)
    scan_config = _d1_json_path(case_dir, "scan-config.json", {"team": _D1_TEAM, "operator": "native-test", "queues": [
        {"queue": _D1_QUEUE, "lineage": _D1_LINEAGE, "league_scope": "DOMESTIC",
         "data_source_version": "fixture-public-seal-v2", "model_key": "crypto",
         "market_type": "terminal", "pack": scan_pack.name},
    ]})
    scan_key = f"run-scan|{_D1_TEAM}|{label}"
    result = _d1_child(project, ["run-scan", "--managed", "--repo-root", str(project.root),
                                "--config", str(scan_config), "--operation-key", scan_key, "--attempt", "1"])
    assert result.returncode == 0, result.stderr
    scan = json.loads(result.stdout)
    candidate_ids = _d1_scan_candidate_ids(scan, tuple(record["candidate_key"] for record in records))
    assert len(candidate_ids) == len(variants) and scan["code_commit"] == project.head
    with store_psycopg.connect_managed(project.root) as conn:
        for index, candidate_id in enumerate(candidate_ids):
            candidate = store_psycopg.d1_candidate_capture_target(
                conn, scan_id=scan["scan_id"], candidate_key=f"buy-yes-{index}")
            decision, = candidate["decisions"]
            assert candidate["candidate_id"] == candidate_id
            assert decision["side"] == "YES" and decision["token_ref"] == records[index]["yes_token_id"]
            assert decision["snapshot_sha256"] == b1_entries[decision["token_ref"]].content_sha256
    b2, b2_entries = _d1_seal_fixture(project, _d1_json_path(
        case_dir, "b2-public.json", {"markets": markets, "books": b2_books}),
        previous_window_start=datetime.datetime.fromisoformat(b1["window_start"]))
    marks = []
    for index, (record, variant) in enumerate(zip(records, variants, strict=True)):
        token = record["yes_token_id"]
        entry = b2_entries.get(token)
        if entry is None:
            assert variant == "unavailable"
        else:
            assert entry.request_meta["status"] == "received"
        marks.append({
            ("scan_operation_key" if index % 2 == 0 else "scan_id"): (scan_key if index % 2 == 0 else scan["scan_id"]),
            "candidate_key": record["candidate_key"], "sampling_kind": "CLOSE",
            "observed_at": b2["decision_as_of"] if entry is None else entry.request_meta["source_observed_at"],
            "market_ref_kind": "condition_id", "market_ref": record["condition_id"],
            "token_ref": token, "side": "YES", "source_kind": "clob_book",
            "source_ref": None if entry is None else journal.entry_source_ref(entry),
            "snapshot_sha256": None if entry is None else entry.content_sha256,
            **_QUOTES[variant],
        })
    return _D1CaptureCase(project, case_dir, candidate_ids, scan["scan_id"], scan_key, scan_config,
                          b2["batch_sha256"], tuple(marks), boundary)


def _d1_register_capture_slot(case, label, marks=None, *, candidate_ids=None,
                              max_source_age_seconds=120, max_receipt_age_seconds=120,
                              deadline_seconds=90, write_deadline_seconds=120):
    from polymarket_alpha_lab.papersim import store_psycopg
    from polymarket_alpha_lab.papersim.d1compare import operations

    directory = case.directory / label
    directory.mkdir()
    pack = directory / "marks.json"
    if marks is None:
        marks = case.marks
    content = marks if type(marks) is bytes else json.dumps(marks, sort_keys=True).encode()
    pack.write_bytes(content)
    configuration = {"team": _D1_TEAM, "operator": "native-test", "packs": [pack.name]}
    config = _d1_json_path(directory, "capture-config.json", configuration)
    config_digest = hashlib.sha256(operations.document_bytes(configuration)).hexdigest()
    with store_psycopg.connect_managed(case.project.root) as conn:
        scheduled = store_psycopg.d1_database_time(conn) + 250 * _MILLISECOND
        document = {
            "version": 1, "instance_id": case.project.instance_id, "interval_sha256": case.project.interval_sha256,
            "scheduled_for": scheduled, "deadline": scheduled + deadline_seconds * _SECOND,
            "write_deadline": scheduled + write_deadline_seconds * _SECOND,
            "leg": "new", "command": "capture-marks", "team": _D1_TEAM,
            "code_commit": case.project.head, "config_digest": config_digest,
            "input_evidence": [{"pack": pack.name, "snapshot_sha256": hashlib.sha256(content).hexdigest(),
                                "byte_length": len(content)}],
            "scope": {"capture_manifest_sha256": case.capture_manifest_sha256,
                      "candidate_ids": list(case.candidate_ids if candidate_ids is None else candidate_ids),
                      "book_age_policy": {"max_source_age_seconds": max_source_age_seconds,
                                          "max_receipt_age_seconds": max_receipt_age_seconds}},
        }
        key = operations.slot_operation_key(document)
        _d1_register_binding(
            conn, document=document, kind="d1_operation_slot", source="d1:operation:slot:v1",
            command="d1-register-slot", key_for=lambda _digest: f"d1:slot-binding:v1:{key.rsplit(':', 1)[1]}",
            scheduled_for=scheduled, code_commit=case.project.head, config_digest=config_digest,
        )
    return _D1CaptureSlot(key, config, pack, scheduled, document)


def _d1_capture_cli(project, slot, *, attempt=1, code=None, key=None, extra=()):
    wait = (slot.scheduled_for - datetime.datetime.now(_UTC)) / _SECOND
    if wait > 0:
        time.sleep(wait)
    return _d1_child(project, [*extra, "capture-marks", "--managed", "--repo-root", str(project.root),
                               "--config", str(slot.config), "--operation-key", key or slot.key,
                               "--attempt", str(attempt)], code=code)


def _d1_capture_rows(project, candidate_ids):
    from polymarket_alpha_lab.papersim import store_psycopg

    keys = (list(candidate_ids),)
    with store_psycopg.connect_managed(project.root) as reader, reader.transaction():
        reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        assert reader.execute("SELECT current_user").fetchone() == ("pal_app",)
        mark = reader.execute(
            "SELECT candidate_mark_id, candidate_mark_key, candidate_id, sampling_kind, observed_at, "
            "market_ref_kind, market_ref, token_ref, side, bid, ask, mid, last, source_kind, source_ref, "
            "snapshot_sha256, paper_only, report_only, readonly FROM papersim.candidate_marks "
            "WHERE candidate_id = ANY(%s) ORDER BY candidate_mark_id", keys).fetchall()
        prices = reader.execute(
            "SELECT evidence_id, evidence_key, candidate_id, evidence_role, observed_at, "
            "market_ref_kind, market_ref, token_ref, side, bid, ask, mid, last, source_kind, source_ref, "
            "snapshot_sha256, paper_only, report_only, readonly FROM papersim.candidate_price_evidence "
            "WHERE candidate_id = ANY(%s) ORDER BY evidence_id", keys).fetchall()
        return {"mark": mark, "close": [row for row in prices if row[3] == "CLOSE"],
                "decision": [row for row in prices if row[3] == "DECISION"]}


def _d1_capture_receipt(project, slot, phase, *, attempt=1):
    from polymarket_alpha_lab.papersim import store_psycopg

    with store_psycopg.connect_managed(project.root) as reader, reader.transaction():
        reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        row = reader.execute(
            "SELECT run_event_id, occurred_at, error_code, output_evidence, code_commit "
            "FROM papersim.command_run_events WHERE operation_key = %s AND attempt = %s AND phase = %s",
            (slot.key, attempt, phase)).fetchone()
        if row is None:
            return None
        [digest_ref, size_ref] = row[3]
        digest = digest_ref.removeprefix("snapshot_sha256:")
        size = int(size_ref.removeprefix("snapshot_byte_length:"))
        raw, stored_size = reader.execute(
            "SELECT content, byte_length FROM papersim.snapshots WHERE content_sha256 = %s", (digest,)).fetchone()
        assert hashlib.sha256(raw).hexdigest() == digest and len(raw) == size == stored_size
        assert row[4] == project.head
        return {"id": row[0], "occurred_at": row[1], "error": row[2], "sha256": digest, "body": json.loads(raw)}


def _d1_assert_written(row, pack, *, expected_kind):
    assert row[3:16] == (
        expected_kind, datetime.datetime.fromisoformat(pack["observed_at"]),
        pack["market_ref_kind"], pack["market_ref"], pack["token_ref"], pack["side"],
        *(None if pack[name] is None else Decimal(str(pack[name])) for name in ("bid", "ask", "mid", "last")),
        pack["source_kind"], pack["source_ref"], pack["snapshot_sha256"],
    )
    assert row[16:] == (True, True, True)


@_D1_NATIVE
def test_d1_first_attempt_rebuilds_prices_from_the_sealed_buy_book(d1_capture_project, record_property):
    project = d1_capture_project
    case = _d1_seed_capture_case(project, "first-quote",
                                 ("quote", "bid-only", "last-zero", "last-one", "empty"))
    before = _d1_capture_rows(project, case.candidate_ids)
    assert not before["mark"] and not before["close"] and len(before["decision"]) == 5
    book = case.marks[0]
    failures = (
        ("ask-first-level", "ask", 0.63, "0.58", "0.63", "d1_capture_quote_mismatch"),
        ("wrong-bid", "bid", 0.51, "0.55", "0.51", "d1_capture_quote_mismatch"),
        ("inferred-mid", "mid", 0.565, None, "0.565", "d1_capture_quote_mismatch"),
        ("invented-last", "last", 0, None, 0, "d1_capture_quote_mismatch"),
        ("missing-ask", "ask", None, "0.58", None, "d1_capture_quote_mismatch"),
        ("bool-price", "ask", True, "0.58", True, "d1_capture_price_invalid"),
        ("rounded-ask", "ask", None, "0.58", "0.58000000000000000001", "d1_capture_quote_mismatch"),
    )
    first = None
    for label, field, claimed, observed, recorded_claim, reason in failures:
        marks = [book | {field: claimed}]
        if label == "rounded-ask":
            marks = json.dumps([book]).replace('"ask": 0.58', '"ask": 0.58000000000000000001').encode()
        slot = _d1_register_capture_slot(case, label, marks)
        result = _d1_capture_cli(project, slot)
        assert result.returncode == 1 and reason in result.stderr and not result.stdout
        assert _d1_capture_rows(project, case.candidate_ids) == before
        prepared = _d1_capture_receipt(project, slot, "PREPARED")
        item, = prepared["body"]["plan"]["targets"]
        assert item["status"] == "REJECTED" and item["reason"] == reason
        assert item["quote_differences"][field] == {"observed": observed, "claimed": recorded_claim}
        assert item["mark"] is None and item["close"] is None
        assert item["source"]["snapshot_sha256"] == book["snapshot_sha256"]
        failed = _d1_capture_receipt(project, slot, "FAILED")
        assert failed["error"] == reason and _d1_capture_receipt(project, slot, "SUCCEEDED") is None
        if first is None:
            first = {"slot": slot.key, "attempt": 1, "exit": result.returncode, "prepared": prepared["sha256"],
                     "failed": failed["sha256"], "mark_delta": 0, "close_delta": 0,
                     "source": item["source"], "quote_differences": item["quote_differences"]}

    # An equivalent decimal spelling succeeds on a fresh operation. Empty
    # sides and true last=0/1 come from the same book bodies as their marks.
    raw = json.dumps(case.marks).replace('"ask": 0.58', '"ask": 0.58000000000000000000').encode()
    good_slot = _d1_register_capture_slot(case, "correct", raw)
    result = _d1_capture_cli(project, good_slot)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert [item["status"] for item in summary["items"]] == ["WRITTEN"] * 4 + ["MISSING_QUOTE"]
    assert summary["marks_written"] == summary["close_evidence_written"] == 4
    assert summary["missing_quote"] == 1 and summary["candidates_seen"] == summary["observations"] == 5
    after = _d1_capture_rows(project, case.candidate_ids)
    assert after["decision"] == before["decision"] and len(after["mark"]) == len(after["close"]) == 4
    for mark, close, pack in zip(after["mark"], after["close"], case.marks[:4], strict=True):
        assert mark[2] == close[2]
        _d1_assert_written(mark, pack, expected_kind="CLOSE")
        _d1_assert_written(close, pack, expected_kind="CLOSE")
    record_property("first_ask_rejection", json.dumps(first, sort_keys=True))
    record_property("correct_ask", json.dumps({
        "slot": good_slot.key, "exit": result.returncode, "code_commit": project.head,
        "items": summary["items"], "mark_rows": after["mark"], "close_rows": after["close"],
    }, sort_keys=True, default=str))


def _d1_stop_capture_process():
    """Quit the real CLI at a test-local process boundary."""
    from polymarket_alpha_lab.papersim import cli
    from polymarket_alpha_lab.papersim.d1compare import operations

    when, index = sys.argv[1], int(sys.argv[2])
    capture_item = operations._Operation.capture_item
    finish = operations._Operation.finish
    position = 0

    def checked_item(self, *args, **kwargs):
        nonlocal position
        position += 1
        if when == "before" and index == position:
            os._exit(86)
        result = capture_item(self, *args, **kwargs)
        if when == "after" and index == position:
            os._exit(86)
        return result

    def checked_finish(self, *args, **kwargs):
        if when == "finish":
            os._exit(86)
        return finish(self, *args, **kwargs)

    operations._Operation.capture_item = checked_item
    operations._Operation.finish = checked_finish
    raise SystemExit(cli.main(sys.argv[3:]))


@_D1_NATIVE
@pytest.mark.parametrize(("when", "index", "persisted"), (("before", 1, 0), ("after", 1, 1), ("finish", 3, 2)))
def test_d1_three_original_observations_recover_in_another_cli_process(
        d1_capture_project, record_property, when, index, persisted):
    project = d1_capture_project
    case = _d1_seed_capture_case(project, f"resume-{when}")
    marks = [case.marks[0], case.marks[1] | {"sampling_kind": "PERIODIC"}, case.marks[2]]
    slot = _d1_register_capture_slot(case, "original", marks)
    stopped = _d1_capture_cli(project, slot, extra=(when, str(index)), code=(
        "from tests.test_papersim_capture_marks_native import _d1_stop_capture_process; _d1_stop_capture_process()"))
    assert stopped.returncode == 86 and not stopped.stdout
    original = _d1_capture_receipt(project, slot, "PREPARED")
    assert original is not None
    assert _d1_capture_receipt(project, slot, "SUCCEEDED") is None
    assert _d1_capture_receipt(project, slot, "FAILED") is None
    committed = _d1_capture_rows(project, case.candidate_ids)
    assert len(committed["mark"]) == persisted and len(committed["close"]) == (persisted > 0)
    if when == "after":
        original_pack = slot.pack.read_bytes()
        slot.pack.write_bytes(json.dumps([marks[0] | {"ask": 0.63}, *marks[1:]]).encode())
        changed = _d1_capture_cli(project, slot)
        assert changed.returncode == 1 and "d1_operation_binding_conflict" in changed.stderr
        slot.pack.write_bytes(original_pack)
        assert _d1_capture_rows(project, case.candidate_ids) == committed
        assert _d1_capture_receipt(project, slot, "PREPARED") == original
        assert project.postgres.down()["status"] == "stopped"
        assert project.postgres.up()["started_here"] is True
        assert _d1_capture_rows(project, case.candidate_ids) == committed
    recovered = _d1_capture_cli(project, slot, attempt=2)
    assert recovered.returncode == 0, recovered.stderr
    summary = json.loads(recovered.stdout)
    assert summary["observations"] == summary["candidates_seen"] == 3
    assert (summary["marks_written"], summary["close_evidence_written"], summary["missing_quote"]) == (2, 1, 1)
    assert [item["status"] for item in summary["items"]] == (
        ["REUSED"] * persisted + ["WRITTEN"] * (2 - persisted) + ["MISSING_QUOTE"])
    after = _d1_capture_rows(project, case.candidate_ids)
    assert after["mark"][:persisted] == committed["mark"] and after["decision"] == committed["decision"]
    if persisted:
        assert after["close"] == committed["close"]
    assert [row[2] for row in after["mark"]] == list(case.candidate_ids[:2])
    assert [row[2] for row in after["close"]] == [case.candidate_ids[0]]
    _d1_assert_written(after["mark"][0], marks[0], expected_kind="CLOSE")
    _d1_assert_written(after["close"][0], marks[0], expected_kind="CLOSE")
    _d1_assert_written(after["mark"][1], marks[1], expected_kind="PERIODIC")
    assert _d1_capture_receipt(project, slot, "PREPARED") == original
    recovery = _d1_capture_receipt(project, slot, "RECOVERY_READ", attempt=2)
    assert recovery["occurred_at"] > original["occurred_at"]
    succeeded = _d1_capture_receipt(project, slot, "SUCCEEDED", attempt=2)
    assert succeeded["occurred_at"] >= recovery["occurred_at"]
    assert json.loads(_d1_capture_cli(project, slot, attempt=2).stdout) == summary
    assert _d1_capture_rows(project, case.candidate_ids) == after
    record_property("capture_recovery", json.dumps({
        "slot": slot.key, "stop_exit": stopped.returncode, "recovery_exit": recovered.returncode,
        "original": {key: value for key, value in original.items() if key != "body"},
        "recovery": {key: value for key, value in recovery.items() if key != "body"},
        "succeeded": {key: value for key, value in succeeded.items() if key != "body"},
        "items": summary["items"], "before_restart": committed, "after_restart": after,
    }, sort_keys=True, default=str))


@_D1_NATIVE
def test_d1_uses_strict_event_and_receipt_boundaries(d1_capture_project):
    project = d1_capture_project
    case = _d1_seed_capture_case(project, "event-boundaries",
                                 ("quote", "quote", "quote", "quote", "unavailable"), boundary_cases=True)
    slot = _d1_register_capture_slot(case, "boundaries", case.marks[:4])
    result = _d1_capture_cli(project, slot)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["marks_written"] == 4 and summary["close_evidence_written"] == 0
    assert summary["downgrade_reasons"] == {
        "observed_at_after_event_start": 2, "received_at_after_event_start": 1, "missing_event_start": 1}
    prepared = _d1_capture_receipt(project, slot, "PREPARED")
    targets = prepared["body"]["plan"]["targets"]
    assert datetime.datetime.fromisoformat(targets[0]["source"]["source_observed_at"]) == case.boundary
    assert datetime.datetime.fromisoformat(targets[1]["source"]["source_observed_at"]) > case.boundary
    assert (datetime.datetime.fromisoformat(targets[2]["source"]["source_observed_at"]) < case.boundary
            <= datetime.datetime.fromisoformat(targets[2]["source"]["received_at"]))
    assert targets[3]["candidate"]["event_start_at"] is None
    rows = _d1_capture_rows(project, case.candidate_ids)
    assert not rows["close"] and len(rows["decision"]) == 5
    for row, pack in zip(rows["mark"], case.marks[:4], strict=True):
        _d1_assert_written(row, pack, expected_kind="PERIODIC")

    unavailable = _d1_register_capture_slot(case, "unavailable", case.marks[4:])
    refused = _d1_capture_cli(project, unavailable)
    assert refused.returncode == 1 and "d1_capture_source_book_missing" in refused.stderr
    assert _d1_capture_rows(project, case.candidate_ids) == rows


@_D1_NATIVE
def test_d1_rejects_wrong_identity_stale_books_and_unattributed_existing_rows(d1_capture_project):
    project = d1_capture_project
    case = _d1_seed_capture_case(project, "conflicts", ("quote", "bid-only"))
    original = _d1_capture_rows(project, case.candidate_ids)
    assert not original["mark"] and not original["close"]
    first, second = case.marks
    other_scan = _d1_child(project, ["run-scan", "--managed", "--repo-root", str(project.root),
                                    "--config", str(case.scan_config), "--operation-key", case.scan_key + "-other"])
    assert other_scan.returncode == 0, other_scan.stderr
    other = json.loads(other_scan.stdout)
    other_candidate_ids = _d1_scan_candidate_ids(other, tuple(mark["candidate_key"] for mark in case.marks))
    wrong_scan = {key: value for key, value in first.items() if key != "scan_operation_key"}
    wrong_scan["scan_id"] = other["scan_id"]
    failures = (
        ("token", first | {"token_ref": second["token_ref"]}, {}, "d1_capture_buy_token_mismatch"),
        ("market", first | {"market_ref": second["market_ref"]}, {}, "d1_capture_candidate_market_mismatch"),
        ("scan", wrong_scan, {}, "d1_capture_candidate_outside_slot"),
        ("source-age", first, {"max_source_age_seconds": 0}, "d1_capture_book_stale"),
        ("receipt-age", first, {"max_receipt_age_seconds": 0}, "d1_capture_book_stale"),
    )
    for label, pack, policy, reason in failures:
        slot = _d1_register_capture_slot(case, label, (pack,), **policy)
        result = _d1_capture_cli(project, slot)
        assert result.returncode == 1 and reason in result.stderr
        assert _d1_capture_receipt(project, slot, "FAILED")["error"] == reason
        assert _d1_capture_rows(project, case.candidate_ids) == original
        assert not _d1_capture_rows(project, other_candidate_ids)["mark"]

    # A committed D1 target requires its own attributed witness. This
    # ordinary write has the same natural key and values in both tables.
    slot = _d1_register_capture_slot(case, "orphan", (first,))
    ordinary = _d1_capture_cli(project, slot, key="capture-marks|native-ordinary|orphan")
    assert ordinary.returncode == 0, ordinary.stderr
    orphan = _d1_capture_rows(project, case.candidate_ids)
    assert len(orphan["mark"]) == len(orphan["close"]) == 1
    result = _d1_capture_cli(project, slot)
    assert result.returncode == 1, (result.stderr, result.stdout)
    assert _d1_capture_receipt(project, slot, "FAILED") is not None
    assert _d1_capture_receipt(project, slot, "SUCCEEDED") is None
    assert _d1_capture_rows(project, case.candidate_ids) == orphan


@_D1_NATIVE
def test_legacy_ordinary_capture_replays_one_mark_for_two_identical_sealed_quotes(
    d1_capture_project, monkeypatch,
):
    """Before OD-P registration, both acquisition receipts survive the old B2 key."""
    from polymarket_alpha_lab.papersim import snapshots, store_psycopg
    from polymarket_alpha_lab.papersim.adapters import polymarket
    from polymarket_alpha_lab.papersim.d1compare import acquire, journal

    project = d1_capture_project
    case = _d1_seed_capture_case(project, "ordinary-repeat-book", variants=("quote",))
    mark, = case.marks
    token = mark["token_ref"]

    def read_buy_request(batch_sha):
        manifest, entries = acquire.read_capture(repo_root=project.root, batch_sha256=batch_sha)
        book, = (book for book in manifest["books"] if book["token_id"] == token)
        sequence = book["request_sequence"]
        row = manifest["requests"][sequence - 1]
        entry, = (entry for entry in entries if entry.request_meta["sequence"] == sequence)
        assert row["sequence"] == sequence and row["status"] == "received"
        assert row["source_time_status"] == "upstream" and row["window_status"] == "in_window"
        assert row["byte_length"] == len(entry.content)
        assert row["content_sha256"] == hashlib.sha256(entry.content).hexdigest()
        assert entry.request_meta["query"]["token_id"] == token
        assert datetime.datetime.fromisoformat(row["utc_fetched_at"]) == entry.utc_fetched_at
        return manifest, entry

    first_batch = case.capture_manifest_sha256
    first_manifest, first = read_buy_request(first_batch)
    fixture = json.loads((case.directory / "b2-public.json").read_bytes())
    fixture["books"][token]["fixture_source_at"] = first.request_meta["source_observed_at"]
    repeated_fixture = _d1_json_path(case.directory, "repeat-source.json", fixture)
    second_receipt, _ = _d1_seal_fixture(
        project, repeated_fixture, datetime.datetime.fromisoformat(first_manifest["scope"]["window_start"]),
    )
    second_batch = second_receipt["batch_sha256"]
    second_manifest, second = read_buy_request(second_batch)
    assert first_batch != second_batch and first.request_meta["sequence"] == second.request_meta["sequence"]
    assert first.content == second.content and first.content_sha256 == second.content_sha256 == mark["snapshot_sha256"]
    assert first.request_meta["source_observed_at"] == second.request_meta["source_observed_at"] == mark["observed_at"]
    assert first.utc_fetched_at < second.utc_fetched_at
    assert journal.entry_source_ref(first) == journal.entry_source_ref(second) == mark["source_ref"]
    with store_psycopg.connect_managed(project.root) as conn:
        saved = snapshots.read_snapshot(conn, first.content_sha256)
        assert saved["fetched_at"] == first.utc_fetched_at

    scope = second_manifest["scope"]

    def no_transport(*args, **kwargs):
        pytest.fail("sealed retry reached public transport")

    monkeypatch.setattr(polymarket, "BoundedPublicOpener", no_transport)
    replay = acquire.capture_inputs(
        repo_root=project.root, window_start=datetime.datetime.fromisoformat(scope["window_start"]),
        window_end=datetime.datetime.fromisoformat(scope["window_end"]), tag_slugs=scope["tag_slugs"],
        allow_public_fetch=True,
    )
    assert replay["reused"] is True and replay["batch_sha256"] == second_batch
    _, original = read_buy_request(replay["batch_sha256"])
    assert original == second

    pack = _d1_json_path(case.directory, "ordinary-marks.json", [mark])
    config = _d1_json_path(case.directory, "ordinary-config.json", {"team": _D1_TEAM, "packs": [pack.name]})
    outputs = []
    for key in ("ordinary-first", "ordinary-first", "ordinary-second"):
        run = _d1_child(project, ["capture-marks", "--managed", "--repo-root", str(project.root),
                                 "--config", str(config), "--operation-key", f"capture-marks|{_D1_TEAM}|{key}",
                                 "--attempt", "1"])
        assert run.returncode == 0, run.stderr
        outputs.append(run.stdout)
    assert outputs[0] == outputs[1] == outputs[2]
    assert json.loads(outputs[0])["close_evidence_written"] == 1
    rows = _d1_capture_rows(project, case.candidate_ids)
    stored_mark, = rows["mark"]
    stored_evidence, = rows["close"]
    assert stored_mark[4] == stored_evidence[4] == datetime.datetime.fromisoformat(mark["observed_at"])
    assert stored_mark[15] == stored_evidence[15] == first.content_sha256

    # The body digest and upstream quote clock are identical. The manifest and
    # request sequence select the actual receipt, including on a sealed replay.
    inspected = []
    for digest in (first_batch, second_batch, second_batch):
        result = _d1_child(project, ["capture-marks", "--managed", "--repo-root", str(project.root),
                                     "--config", str(config), "--inspect-close-window", digest])
        assert result.returncode == 0, result.stderr
        item, = json.loads(result.stdout)["rows"]
        assert item["sampling_kind"] == "PERIODIC" and item["reason"] == "observed_at_before_close_window"
        assert item["book_sha256"] == first.content_sha256
        assert item["request_sequence"] == first.request_meta["sequence"]
        inspected.append(datetime.datetime.fromisoformat(item["received_at"]))
    assert inspected == [first.utc_fetched_at, second.utc_fetched_at, second.utc_fetched_at]
    assert _d1_capture_rows(project, case.candidate_ids) == rows


@_D1_NATIVE
def test_ordinary_and_registered_close_window_evidence_use_the_same_native_clocks(d1_capture_project):
    from polymarket_alpha_lab.papersim import store_psycopg

    project = d1_capture_project
    case = _d1_seed_capture_case(project, "close-window-readback", ("quote", "empty"),
                                 time_to_expiry=datetime.timedelta(minutes=40))
    slot = _d1_register_capture_slot(case, "inspect")
    with store_psycopg.connect_managed(project.root) as reader:
        event_count = reader.execute("SELECT count(*) FROM papersim.command_run_events").fetchone()
    before = _d1_capture_rows(project, case.candidate_ids)
    args = ["capture-marks", "--managed", "--repo-root", str(project.root), "--config", str(slot.config),
            "--inspect-close-window", case.capture_manifest_sha256]
    ordinary = _d1_child(project, args)
    registered = _d1_child(project, [*args, "--operation-key", slot.key])
    assert ordinary.returncode == registered.returncode == 0, (ordinary.stderr, registered.stderr)
    ordinary_report, registered_report = map(json.loads, (ordinary.stdout, registered.stdout))
    assert ordinary_report["capture_path"] == "ordinary_b2"
    assert registered_report["capture_path"] == "registered_d1"
    assert ordinary_report["rows"] == registered_report["rows"]
    observed, missing = ordinary_report["rows"]
    assert (observed["sampling_kind"], observed["reason"]) == ("CLOSE", None)
    assert observed["token_id"] == case.marks[0]["token_ref"]
    assert observed["book_sha256"] == case.marks[0]["snapshot_sha256"]
    assert datetime.datetime.fromisoformat(observed["source_observed_at"]) <= datetime.datetime.fromisoformat(observed["received_at"])
    assert observed["boundary"]["field"] == "endDate" and observed["boundary"]["sources"]
    assert missing["sampling_kind"] == "PERIODIC" and missing["reason"] == "d1_capture_no_quote"

    # Re-read the same bound Gamma entry as a sports request: the only
    # supplied clock here is the contract expiry, so kickoff stays missing.
    from types import SimpleNamespace
    from polymarket_alpha_lab.papersim import close_window, prepare
    from polymarket_alpha_lab.papersim.d1compare import acquire
    manifest, entries = acquire.read_capture(repo_root=project.root, batch_sha256=case.capture_manifest_sha256)
    view = prepare._capture_view(case.capture_manifest_sha256, manifest, entries)
    with store_psycopg.connect_managed(project.root) as reader:
        candidate = store_psycopg.d1_candidate_capture_target(
            reader, scan_id=case.scan_id, candidate_key=case.marks[0]["candidate_key"])
    request = SimpleNamespace(market_kind="match_winner", market_id=candidate["market_id"],
                              question="Will Ethereum be below 2,000 on the settlement date?")
    with pytest.raises(capture.CaptureError, match="close_window_boundary_missing"):
        close_window._market_boundary(view, candidate, request)

    mismatch = _d1_child(project, [*args[:-1], "0" * 64, "--operation-key", slot.key])
    assert mismatch.returncode == 1 and "d1_capture_manifest_binding_mismatch" in mismatch.stderr
    with store_psycopg.connect_managed(project.root) as reader:
        assert reader.execute("SELECT count(*) FROM papersim.command_run_events").fetchone() == event_count
    wrong_side = _d1_register_capture_slot(case, "wrong-side-readback", [case.marks[0] | {"side": "NO"}])
    invalid = _d1_child(project, ["capture-marks", "--managed", "--repo-root", str(project.root),
                                  "--config", str(wrong_side.config), "--inspect-close-window", case.capture_manifest_sha256])
    assert invalid.returncode == 0, invalid.stderr
    invalid_row, = json.loads(invalid.stdout)["rows"]
    assert (invalid_row["sampling_kind"], invalid_row["reason"]) == ("PERIODIC", "d1_capture_candidate_side_invalid")
    rounded = json.dumps([case.marks[0]]).replace('"ask": 0.58', '"ask": 0.58000000000000000001')
    wrong_side.pack.write_text(rounded)
    invalid_price = _d1_child(project, ["capture-marks", "--managed", "--repo-root", str(project.root),
                                        "--config", str(wrong_side.config), "--inspect-close-window", case.capture_manifest_sha256])
    assert invalid_price.returncode == 0, invalid_price.stderr
    precise_row, = json.loads(invalid_price.stdout)["rows"]
    assert (precise_row["sampling_kind"], precise_row["reason"]) == ("PERIODIC", "d1_capture_quote_mismatch")
    with store_psycopg.connect_managed(project.root) as reader:
        assert reader.execute("SELECT count(*) FROM papersim.command_run_events").fetchone()[0] == event_count[0] + 1
    assert _d1_capture_rows(project, case.candidate_ids) == before


def test_register_close_window_through_managed_cli(d1_capture_project, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from polymarket_alpha_lab.papersim import prepare_capture, snapshots, store_psycopg
    from polymarket_alpha_lab.papersim.d1compare import close_registration, operations

    project = d1_capture_project
    team = "OD-P prospective fixture"
    with store_psycopg.connect_managed(project.root) as connection:
        now = store_psycopg.d1_database_time(connection)
    start = now + datetime.timedelta(minutes=10)
    end = start + datetime.timedelta(hours=1)

    def submit(a, b):
        return subprocess.run(
            [sys.executable, str(project.root / "tools/d1_compare.py"), "register-close-window",
             "--repo-root", str(project.root), "--team", team, "--window-start", a.isoformat(),
             "--window-end", b.isoformat()], cwd=project.root, capture_output=True, text=True, timeout=45)

    # Simultaneous, equivalent requests must return the original clock, code and evidence.
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(pool.map(lambda _: submit(start, end), range(2)))
    assert [result.returncode for result in attempts] == [0, 0]
    first, second = (json.loads(result.stdout) for result in attempts)
    assert first == second
    assert first.keys() == {"interval_sha256", "byte_length", "close_window_policy", "registered_at",
                            "starts_at", "ends_at", "code_commit"}
    assert first["close_window_policy"] == "OD-P" and first["code_commit"] == project.head
    assert now <= datetime.datetime.fromisoformat(first["registered_at"]) < start
    assert first["starts_at"] == start.isoformat() and first["ends_at"] == end.isoformat()
    digest = first["interval_sha256"]
    with store_psycopg.connect_managed(project.root) as connection:
        refs, doc = operations._registration(connection, "d1:interval-binding:v1:" + digest,
                                            "d1-register-interval", operations._INTERVAL_KIND,
                                            "d1:operation:interval:v1")
        snapshot = snapshots.verify_snapshot(connection, digest)
        assert snapshot["fetched_at"] == refs["occurred_at"]
        assert snapshot["byte_length"] == first["byte_length"]
        assert doc["instance_id"] == project.instance_id and doc["team"] == team
        # A close-only interval does not change ordinary B0 discovery or the dual age policy.
        assert prepare_capture._interval_reference(connection, project.instance_id, team, start, end) is None

    overlap = submit(start + _SECOND, end + _SECOND)
    assert overlap.returncode == 2 and overlap.stdout == ""
    assert overlap.stderr.rstrip().endswith("d1_close_window_registration_overlap")
    assert submit(end, end + _SECOND).returncode == 0  # Exclusive end is the next admissible start.

    past = submit(now - _SECOND * 3, now - _SECOND)
    assert past.returncode == 2 and past.stdout == ""
    assert past.stderr.rstrip().endswith("d1_close_window_registration_not_prospective")

    # An already registered interval remains readable (without creating another event) after it starts.
    monkeypatch.setattr(store_psycopg, "d1_database_time", lambda conn: end + _SECOND)
    assert close_registration.register(repo_root=project.root, team=team, starts_at=start, ends_at=end) == first

    # A mid-transaction exception must not leave an unattested interval snapshot or success receipt.
    bad_start, bad_end = end + datetime.timedelta(hours=2), end + datetime.timedelta(hours=3)
    assert bad_start > end + _SECOND
    def interrupt(*args, **kwargs):
        raise RuntimeError("synthetic interruption")
    monkeypatch.setattr(store_psycopg, "record_command_run_event", interrupt)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        close_registration.register(repo_root=project.root, team=team, starts_at=bad_start, ends_at=bad_end)
    rejected_doc = dict(doc, starts_at=bad_start, ends_at=bad_end)
    rejected = snapshots.snapshot_content_sha256(operations.document_bytes(rejected_doc))
    with store_psycopg.connect_managed(project.root) as connection:
        assert snapshots.read_snapshot(connection, rejected) is None
        assert store_psycopg.read_command_run_event(connection, "d1:interval-binding:v1:" + rejected,
                                                     1, "SUCCEEDED") is None
