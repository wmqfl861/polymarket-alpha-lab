"""FX-12 replay tests: binding, same-result replay and tamper forensics.

Before/after proof (M1 plan section 3 FX-12 item 4; DELIVERY_PLAN.md 78.1): a synthetic scan
containing an ACCEPTED, several REJECTED and two CONSTRUCTION_FAILED
candidates is recorded through the real binding helpers and replays
with the SAME result; one tampered byte, a missing snapshot, a wrong
code SHA, tampered fee/book/time/bridge evidence, a tampered policy
fingerprint, a missing decision-time evidence row, an unknown record
type and unknown bundle fields each fail replay explicitly. The
offline suite drives the pipeline with an in-memory stub speaking
exactly the SELECT surface of ``polymarket_alpha_lab.replay`` (plus
the snapshot face); the opt-in native lane at the bottom proves the
full PostgreSQL round trip and that replay writes nothing.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import math
import os
from pathlib import Path

import pytest

from polymarket_alpha_lab import replay
from polymarket_alpha_lab.papersim import gates, pipeline, snapshots, soccer_model, store
from polymarket_alpha_lab.papersim.crypto_model import TerminalProbability
from polymarket_alpha_lab.papersim.pipeline import EntryRequest, decide_entry
from polymarket_alpha_lab.papersim.soccer_model import SoccerCandidate
from polymarket_alpha_lab.replay import PapersimReplayError

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / 'fixtures' / 'papersim'
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'
OWNER_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_OWNER_DSN', '')
APP_DSN = os.environ.get('PAPERSIM_NATIVE_MANAGED_APP_DSN', '')
NATIVE = pytest.mark.skipif(not ENABLED or not OWNER_DSN or not APP_DSN,
                            reason='explicit native papersim replay proof is opt-in')

COMMIT = 'a' * 40
OTHER_COMMIT = 'b' * 40

ASK_LADDER_5856164 = [
    (0.56, 1000.0),
    (0.57, 14000.0),
    (0.58, 12993.0),
    (0.60, 30000.0),
]
NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)

HOF_TEAM_HOME = 'TSG 1899 Hoffenheim'
HOF_LEAGUE = 'bundesliga'
LYO_TEAM_AWAY = 'Olympique Lyonnais'
LYO_LEAGUE = 'ligue_1'
STRONG_BUNDES_BRIDGE = {HOF_LEAGUE: math.log(1.25), LYO_LEAGUE: 0.0}


def _concordant(fixture: str) -> gates.NewsCheck:
    return gates.check_news(fixture, 'recheck T-1d', news_delta='unchanged')


def _gate48_case() -> dict:
    data = json.loads(
        (FIXTURES / 'regression_gate_arithmetic.json').read_text(encoding='utf-8')
    )
    assert data['schema'] == 'papersim.regression.gate_arithmetic.v1'
    return data['case']


def _soccer48_request() -> EntryRequest:
    case = _gate48_case()
    entry = case['entry_r23']
    candidate = SoccerCandidate(
        match='Brentford FC vs. Nottingham Forest FC',
        question=entry['question'],
        kind='total',
        side=0,
        entry_ask_vwap250=entry['entry_ask_vwap250'],
        model_median=entry['model_median'],
        model_range=tuple(entry['model_range']),
        qs_variants=tuple(entry['qs_variants']),
        lambda_home=entry['lambda_home'],
        lambda_away=entry['lambda_away'],
        market_id=case['market_id'],
    )
    return EntryRequest(
        market_id=case['market_id'],
        question=entry['question'],
        fixture='BRE-FOR_O2.5',
        market_kind='total',
        side='Over/Yes(side0)',
        book=ASK_LADDER_5856164,
        fee_category='sports',
        model_quote=candidate,
        gamma_liquidity=case['r24_book_ladder']['liq'],
        news=_concordant('BRE-FOR_O2.5'),
        o25_hits=6,
        line=2.5,
        bankroll=10_000.0,
        cash=10_000.0,
        reserved=0.0,
        book_time=NOW,
        now=NOW,
        max_book_age_seconds=300.0,
    )


def _stale_request() -> EntryRequest:
    request = _soccer48_request()
    return request.__class__(**{**request.__dict__, 'book_time': NOW - timedelta(seconds=600)})


def _eth2000_terminal() -> TerminalProbability:
    return TerminalProbability(
        asset='ETH', barrier=2000.0, horizon_hours=1595.12,
        convention_a=0.2224, convention_b=0.2531,
    )


def _crypto_no_direction_request() -> EntryRequest:
    return EntryRequest(
        market_id='m-eth2000-term',
        question='Ethereum below $2,000 on date',
        fixture='ETH-2000',
        market_kind='crypto_terminal',
        side='yes',
        book=[(0.15, 5000.0)],
        fee_category='crypto',
        probability=_eth2000_terminal(),
        buy_side='yes',
        yes_ask=0.15,
        no_ask=0.80,
        gamma_liquidity=6000.0,
        news=_concordant('ETH-2000'),
        bankroll=10_000.0,
        cash=10_000.0,
        reserved=0.0,
        book_time=NOW,
        now=NOW,
        max_book_age_seconds=300.0,
    )


def _bridged_request() -> EntryRequest:
    return EntryRequest(
        market_id='m-hof-lyo',
        question='TSG 1899 Hoffenheim vs. Olympique Lyonnais: moneyline',
        fixture='HOF-LYO',
        market_kind='1x2',
        side='yes',
        book=[(0.52, 1000.0)],
        fee_category='sports',
        cross_league=True,
        home_league=HOF_LEAGUE,
        away_league=LYO_LEAGUE,
        bridge=STRONG_BUNDES_BRIDGE,
        bridge_fingerprint=pipeline.bridge_fingerprint(STRONG_BUNDES_BRIDGE),
        model_quote={'model_median': 0.60, 'qs_variants': (0.59, 0.61)},
        gamma_liquidity=20_000.0,
        news=_concordant('HOF-LYO'),
        lineup_confirmation=1.0,
        bankroll=10_000.0,
        cash=10_000.0,
        reserved=0.0,
        book_time=NOW,
        now=NOW,
        max_book_age_seconds=300.0,
    )


CONSTRUCTION_INPUTS_NONE = dict(
    xg_snapshot={}, home_team=HOF_TEAM_HOME, home_league=HOF_LEAGUE,
    away_team=LYO_TEAM_AWAY, away_league=LYO_LEAGUE,
    market='HOF vs LYO', market_id='m-hof-lyo', entry_ask_vwap250=0.52,
    kind='total', side='over', line=2.5,
)
CONSTRUCTION_INPUTS_BAD_LINE = dict(
    xg_snapshot={}, home_team='Brentford', home_league='EPL',
    away_team='Nottingham Forest', away_league='EPL',
    market='BRE-FOR', market_id='m-bre-for', entry_ask_vwap250=0.56,
    kind='total', side='over', line=3.0,
)


def _r23_snapshot() -> dict:
    """The frozen r23 xG snapshot (same bytes the soccer suite pins)."""
    from polymarket_alpha_lab.papersim import xg
    from tests.test_papersim_soccer_model import _r23_snapshot_bytes
    return xg.load_xg_payload(json.loads(_r23_snapshot_bytes()))


# ---------------------------------------------------------------------------
# In-memory managed-connection stub (SELECT surface only)
# ---------------------------------------------------------------------------

class _Column:
    def __init__(self, name: str):
        self.name = name


def _columns_of(sql: str) -> list[str]:
    body = sql[sql.index('SELECT ') + len('SELECT '):sql.index(' FROM ')]
    return [part.strip().split('.')[-1] for part in body.split(',')]


_CANDIDATE_COLUMNS = _columns_of(replay._CANDIDATE_SQL)
_EVIDENCE_COLUMNS = _columns_of(replay._EVIDENCE_SQL)


class FakeCursor:
    def __init__(self, conn: 'FakePapersim'):
        self._conn = conn
        self._rows: list = []
        self._columns: list[str] = []

    def execute(self, sql, params=None):
        sql = ' '.join(sql.split())
        self._conn.statements.append(sql)
        self._rows, self._columns = self._conn._execute(sql, params)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    @property
    def description(self):
        return [_Column(name) for name in self._columns]


class _NoTx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakePapersim:
    """In-memory stand-in: the papersim tables replay reads (and snapshots)."""

    def __init__(self):
        self.snapshots: dict[str, dict] = {}
        self.candidates: dict[int, dict] = {}
        self.evidence: dict[int, dict] = {}
        self.statements: list[str] = []

    def _execute(self, sql, params):
        if sql.startswith('SELECT c.candidate_id'):
            (candidate_id,) = params
            row = self.candidates.get(candidate_id)
            if row is None:
                return [], _CANDIDATE_COLUMNS
            return [tuple(row[name] for name in _CANDIDATE_COLUMNS)], _CANDIDATE_COLUMNS
        if sql.startswith('SELECT evidence_id, evidence_role'):
            (candidate_id,) = params
            row = self.evidence.get(candidate_id)
            if row is None:
                return [], _EVIDENCE_COLUMNS
            return [tuple(row[name] for name in _EVIDENCE_COLUMNS)], _EVIDENCE_COLUMNS
        if sql.startswith('SELECT content, byte_length'):
            (digest,) = params
            row = self.snapshots.get(digest)
            columns = ['content', 'byte_length', 'kind', 'source', 'fetched_at',
                       'schema_version']
            if row is None:
                return [], columns
            return [(row['content'], row['byte_length'], row['kind'], row['source'],
                     row['fetched_at'], row['schema_version'])], columns
        if sql.startswith('INSERT INTO papersim.snapshots'):
            digest, content, length, kind, source, fetched_at, schema_version = params
            if digest in self.snapshots:
                return [], ['content_sha256']
            self.snapshots[digest] = {
                'content_sha256': digest, 'content': bytes(content),
                'byte_length': int(length), 'kind': kind, 'source': source,
                'fetched_at': fetched_at, 'schema_version': int(schema_version)}
            return [(digest,)], ['content_sha256']
        raise AssertionError(f'unexpected SQL reached the stub: {sql}')

    def cursor(self):
        return FakeCursor(self)

    def transaction(self):
        return _NoTx()

    # -- recording helpers (the write side the stub deliberately lacks) ----

    def register(self, registration: snapshots.SnapshotRegistration) -> str:
        return snapshots.register_snapshot(self, registration)['content_sha256']

    def persist(self, submission: store.CandidateSubmission, scan: dict,
                scan_id: int, candidate_id: int) -> int:
        row = {
            'candidate_id': candidate_id, 'scan_id': scan_id,
            'candidate_key': submission.candidate_key, 'conclusion': submission.conclusion,
            'gate_report': json.loads(submission.gate_report),
            'rejection_reasons': json.loads(submission.rejection_reasons),
            'input_payload': json.loads(submission.input_payload),
            'snapshot_refs': json.loads(submission.snapshot_refs),
            'order_book': None if submission.order_book is None
            else json.loads(submission.order_book),
            'book_fetched_at': submission.book_fetched_at,
            'evaluated_at': submission.evaluated_at,
            'condition_id': submission.condition_id, 'market_id': submission.market_id,
            'market_slug': submission.market_slug, 'side': submission.side,
            'code_commit': scan['code_commit'], 'schema_version': scan['schema_version'],
            'gate_version': scan['gate_version'], 'finished_at': scan['finished_at'],
        }
        self.candidates[candidate_id] = row
        for index, evidence in enumerate(submission.price_evidence):
            self.evidence[candidate_id] = {
                'evidence_id': index + 1, 'evidence_role': evidence.evidence_role,
                'observed_at': evidence.observed_at,
                'model_probability': evidence.model_probability,
                'market_ref_kind': evidence.market_ref_kind,
                'market_ref': evidence.market_ref, 'token_ref': evidence.token_ref,
                'side': evidence.side, 'bid': evidence.bid, 'ask': evidence.ask,
                'mid': evidence.mid, 'last': evidence.last,
                'book_sha256': evidence.book_sha256,
                'snapshot_sha256': evidence.snapshot_sha256,
                'source_kind': evidence.source_kind, 'source_ref': evidence.source_ref,
            }
        return candidate_id

    def replay(self, candidate_id: int, **kwargs):
        kwargs.setdefault('trusted_code_commit', COMMIT)
        return replay.replay(self, candidate_id, **kwargs)


SCAN = {'code_commit': COMMIT, 'schema_version': replay.REPLAY_BUNDLE_VERSION,
        'gate_version': pipeline.GATE_VERSION,
        'finished_at': NOW + timedelta(minutes=5)}


def bound_set(db: FakePapersim) -> dict[str, int]:
    """Build, bind and persist the full synthetic scan; return candidate ids."""
    ids = {}
    next_id = [1]

    def persist(submission):
        candidate_id = next_id[0]
        next_id[0] += 1
        db.persist(submission, SCAN, scan_id=1, candidate_id=candidate_id)
        ids[submission.candidate_key] = candidate_id
        return candidate_id

    for key, request, team, kind, market_id in (
            ('k-soccer48-accepted', _soccer48_request(), 'soccer_epl', 'other', '5856164'),
            ('k-stale-rejected', _stale_request(), 'soccer_epl', 'other', '5856164'),
            ('k-crypto-rejected', _crypto_no_direction_request(), 'crypto_eth', 'terminal',
             'm-eth2000-term'),
            ('k-bridged-accepted', _bridged_request(), 'soccer_uefa', 'other', 'm-hof-lyo')):
        decision = decide_entry(request)
        book_digest = db.register(snapshots.SnapshotRegistration(
            content=replay.bound_book_bytes(request), kind='clob_book',
            source=f'clob:{market_id}', fetched_at=request.book_time))
        refs = [book_digest]
        book_sha = book_digest
        if request.bridge is not None:
            bridge_digest = db.register(snapshots.SnapshotRegistration(
                content=json.dumps(replay._encode(request.bridge),
                                   separators=(',', ':')).encode('utf-8'),
                kind='league_bridge', source='league_strength:wp-p2a',
                fetched_at=request.book_time))
            refs.append(bridge_digest)
        entry = replay.bind_entry(decision, entered_at=NOW) if decision.accepted else None
        persist(replay.bind_candidate(
            request, decision, team=team, candidate_key=key, side='YES', market_kind=kind,
            evaluated_at=NOW, market_ref_kind='market_id', market_id=market_id,
            median_probability=replay.bound_median_probability(request, decision),
            snapshot_refs=refs, book_snapshot_sha256=book_sha, entry=entry))
    persist(replay.bind_construction_failure(
        team='soccer_uefa', candidate_key='k-construction-none', evaluated_at=NOW,
        inputs=CONSTRUCTION_INPUTS_NONE, market_id='m-hof-lyo'))
    persist(replay.bind_construction_failure(
        team='soccer_epl', candidate_key='k-construction-bad-line', evaluated_at=NOW,
        inputs=CONSTRUCTION_INPUTS_BAD_LINE, market_id='m-bre-for'))
    return ids


def fresh_db() -> tuple[FakePapersim, dict[str, int]]:
    db = FakePapersim()
    return db, bound_set(db)


# ---------------------------------------------------------------------------
# Codec round trip: the bundle reconstructs a bit-identical decision
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('builder', [
    _soccer48_request, _stale_request, _crypto_no_direction_request, _bridged_request,
], ids=['soccer48', 'stale', 'crypto-no-direction', 'bridged'])
def test_bundle_round_trip_reproduces_the_decision(builder):
    request = builder()
    first = decide_entry(request)
    rebuilt = replay.request_from_replay_bundle(json.loads(replay.replay_bundle_payload(request)))
    second = decide_entry(rebuilt)
    assert second.accepted == first.accepted
    assert second.reasons == first.reasons
    assert second.code_sha256 == first.code_sha256
    replay._compare(replay.decision_report(first), replay.decision_report(second), 'report')


def test_bundle_is_canonical_and_version_pinned():
    request = _soccer48_request()
    payload = replay.replay_bundle_payload(request)
    assert json.loads(payload)['version'] == replay.REPLAY_BUNDLE_VERSION
    assert json.loads(payload)['bundle'] == replay.ENTRY_BUNDLE_TOKEN
    assert replay.replay_bundle_payload(request) == payload
    stripped = json.loads(payload)
    stripped['version'] = 99
    with pytest.raises(PapersimReplayError, match='papersim_replay_schema_version_mismatch'):
        replay.request_from_replay_bundle(stripped)


def test_bundle_rejects_unknown_fields_and_record_types():
    payload = json.loads(replay.replay_bundle_payload(_soccer48_request()))
    payload['fields']['rogue_column'] = 'x'
    with pytest.raises(PapersimReplayError, match='papersim_replay_bundle_field_unknown'):
        replay.request_from_replay_bundle(payload)
    payload['fields'].pop('rogue_column')
    payload['fields']['news'] = {'$record': {'$record_type': 'os.system',
                                             'fields': {'fixture': 'x', 'delta': 'y',
                                                        'concordant': True, 'blockers': [],
                                                        'sources': []}}}
    with pytest.raises(PapersimReplayError, match='papersim_replay_payload_record_unknown'):
        replay.request_from_replay_bundle(payload)


def test_bundle_rejects_non_finite_numbers():
    payload = json.loads(replay.replay_bundle_payload(_soccer48_request()))
    payload['fields']['bankroll'] = float('nan')
    with pytest.raises(PapersimReplayError, match='papersim_replay_payload_non_finite'):
        replay.request_from_replay_bundle(payload)


# ---------------------------------------------------------------------------
# Binding shapes
# ---------------------------------------------------------------------------

def test_binding_produces_store_valid_submissions():
    db, _ = FakePapersim(), {}
    for key, request, team, kind, market_id in (
            ('k1', _soccer48_request(), 'soccer_epl', 'other', '5856164'),
            ('k2', _crypto_no_direction_request(), 'crypto_eth', 'terminal', 'm-eth2000-term')):
        decision = decide_entry(request)
        submission = replay.bind_candidate(
            request, decision, team=team, candidate_key=key, side='YES', market_kind=kind,
            evaluated_at=NOW, market_id=market_id,
            median_probability=replay.bound_median_probability(request, decision),
            entry=replay.bind_entry(decision, entered_at=NOW) if decision.accepted else None)
        submission.validate()
        assert submission.conclusion == ('ACCEPTED' if decision.accepted else 'REJECTED')
        assert json.loads(submission.rejection_reasons) == list(decision.reasons)
        assert json.loads(submission.gate_report)['code_sha256'] == decision.code_sha256
        if decision.accepted:
            entry = submission.entry
            price, stake = Decimal(str(decision.actual_walk.vwap)), Decimal(str(decision.notional))
            assert abs(entry.shares * entry.price - entry.stake) < Decimal('0.01')
            assert entry.price == price and entry.stake == stake


def test_bind_entry_refuses_incomplete_decisions():
    request = _crypto_no_direction_request()
    decision = decide_entry(request)
    assert decision.accepted is False
    with pytest.raises(PapersimReplayError, match='papersim_replay_entry_fields_missing'):
        replay.bind_entry(decision, entered_at=NOW)


def test_construction_failure_binding_records_the_observed_refusal():
    none_row = replay.bind_construction_failure(
        team='soccer_uefa', candidate_key='c1', evaluated_at=NOW,
        inputs=CONSTRUCTION_INPUTS_NONE)
    assert none_row.conclusion == 'CONSTRUCTION_FAILED'
    assert json.loads(none_row.rejection_reasons) == [replay.CONSTRUCTION_RETURNED_NONE]
    bad_line = replay.bind_construction_failure(
        team='soccer_epl', candidate_key='c2', evaluated_at=NOW,
        inputs=CONSTRUCTION_INPUTS_BAD_LINE)
    assert json.loads(bad_line.rejection_reasons) == [replay.CONSTRUCTION_VALUE_ERROR]
    with pytest.raises(PapersimReplayError, match='papersim_replay_construction_succeeded'):
        replay.bind_construction_failure(
            team='soccer_epl', candidate_key='c3', evaluated_at=NOW,
            inputs={**CONSTRUCTION_INPUTS_BAD_LINE, 'xg_snapshot': _r23_snapshot(),
                    'line': 2.5})


# ---------------------------------------------------------------------------
# The before/after proof: every synthetic candidate replays identically
# ---------------------------------------------------------------------------

def test_every_candidate_replays_with_the_same_result():
    db, ids = fresh_db()
    expectations = {
        'k-soccer48-accepted': ('entry_request', True),
        'k-stale-rejected': ('entry_request', False),
        'k-crypto-rejected': ('entry_request', False),
        'k-bridged-accepted': ('entry_request', True),
        'k-construction-none': ('construction_failure', None),
        'k-construction-bad-line': ('construction_failure', None),
    }
    for key, (mode, accepted) in expectations.items():
        result = db.replay(ids[key])
        assert result['matched'] is True, key
        assert result['mode'] == mode, key
        if accepted is not None:
            row = db.candidates[ids[key]]
            assert (row['conclusion'] == 'ACCEPTED') is accepted, key
    stale = db.replay(ids['k-stale-rejected'])
    assert 'stale_book' in db.candidates[ids['k-stale-rejected']]['rejection_reasons']
    assert stale['code_commit'] == COMMIT


def test_replay_issues_select_statements_only():
    db, ids = fresh_db()
    db.statements.clear()
    for candidate_id in ids.values():
        db.replay(candidate_id)
    assert db.statements, 'replay must have read evidence'
    assert all(sql.startswith('SELECT') for sql in db.statements)


def test_replay_module_source_has_no_write_statements():
    source = Path(replay.__file__).read_text(encoding='utf-8')
    for forbidden in ('INSERT INTO', 'UPDATE ', 'DELETE FROM', 'TRUNCATE'):
        assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# Tamper forensics: each tamper fails replay explicitly
# ---------------------------------------------------------------------------

def test_one_byte_snapshot_tamper_fails_replay():
    db, ids = fresh_db()
    row = db.candidates[ids['k-soccer48-accepted']]
    digest = row['snapshot_refs'][0]
    stored = db.snapshots[digest]
    tampered = bytearray(stored['content'])
    tampered[4] ^= 0x20
    stored['content'] = bytes(tampered)
    with pytest.raises(store.PapersimStoreError, match='papersim_snapshot_digest_mismatch'):
        db.replay(row['candidate_id'])


def test_missing_snapshot_fails_replay():
    db, ids = fresh_db()
    row = db.candidates[ids['k-soccer48-accepted']]
    del db.snapshots[row['snapshot_refs'][0]]
    with pytest.raises(store.PapersimStoreError, match='papersim_snapshot_missing'):
        db.replay(row['candidate_id'])


def test_wrong_code_sha_fails_replay():
    db, ids = fresh_db()
    candidate_id = ids['k-soccer48-accepted']
    with pytest.raises(PapersimReplayError, match='papersim_replay_code_commit_mismatch'):
        db.replay(candidate_id, trusted_code_commit=OTHER_COMMIT)
    with pytest.raises(PapersimReplayError, match='papersim_replay_trusted_commit_invalid'):
        db.replay(candidate_id, trusted_code_commit='deadbeef')
    with pytest.raises(PapersimReplayError, match='papersim_replay_trusted_commit_invalid'):
        db.replay(candidate_id, trusted_code_commit=None)
    assert db.replay(candidate_id)['matched'] is True


def test_tampered_fee_evidence_fails_replay():
    db, ids = fresh_db()
    candidate_id = ids['k-soccer48-accepted']
    row = db.candidates[candidate_id]
    row['gate_report']['entry_fee'] = row['gate_report']['entry_fee'] + 0.01
    with pytest.raises(PapersimReplayError, match='papersim_replay_decision_mismatch'):
        db.replay(candidate_id)
    row['gate_report'] = json.loads(replay.decision_report_json(decide_entry(_soccer48_request())))
    row['input_payload']['fields']['fee_category'] = 'crypto'
    with pytest.raises(PapersimReplayError, match='papersim_replay_decision_mismatch'):
        db.replay(candidate_id)


def test_tampered_book_fails_replay():
    db, ids = fresh_db()
    candidate_id = ids['k-soccer48-accepted']
    row = db.candidates[candidate_id]
    row['order_book'][0][0] = 0.58
    with pytest.raises(PapersimReplayError, match='papersim_replay_decision_mismatch'):
        db.replay(candidate_id)
    # Consistent tamper of BOTH the column and the bundle: the structural
    # check passes and the recomputed walk still disagrees with the record.
    row['order_book'][0][0] = 0.58
    row['input_payload']['fields']['book'][0][0] = 0.58
    with pytest.raises(PapersimReplayError, match='papersim_replay_decision_mismatch'):
        db.replay(candidate_id)


def test_tampered_decision_clock_fails_replay():
    db, ids = fresh_db()
    candidate_id = ids['k-soccer48-accepted']
    row = db.candidates[candidate_id]
    row['book_fetched_at'] = NOW - timedelta(seconds=900)
    with pytest.raises(PapersimReplayError, match='papersim_replay_book_time_mismatch'):
        db.replay(candidate_id)
    row['book_fetched_at'] = row['input_payload']['fields']['book_time']['$datetime']
    stale_clock = (NOW - timedelta(seconds=900)).isoformat()
    row['input_payload']['fields']['book_time']['$datetime'] = stale_clock
    row['book_fetched_at'] = NOW - timedelta(seconds=900)
    with pytest.raises(PapersimReplayError, match='papersim_replay_decision_mismatch'):
        db.replay(candidate_id)


def test_tampered_bridge_fails_replay():
    db, ids = fresh_db()
    candidate_id = ids['k-bridged-accepted']
    row = db.candidates[candidate_id]
    row['input_payload']['fields']['bridge'] = {
        '$map': [[HOF_LEAGUE, 0.0], [LYO_LEAGUE, 0.0]]}
    with pytest.raises(PapersimReplayError, match='papersim_replay_bridge_mismatch'):
        db.replay(candidate_id)


def test_tampered_policy_fingerprint_fails_replay():
    db, ids = fresh_db()
    candidate_id = ids['k-soccer48-accepted']
    row = db.candidates[candidate_id]
    row['gate_report']['code_sha256'] = '0' * 64
    with pytest.raises(PapersimReplayError, match='papersim_replay_policy_fingerprint_mismatch'):
        db.replay(candidate_id)
    row['gate_report']['code_sha256'] = pipeline.policy_code_sha256()
    row['gate_version'] = 'gates-tampered'
    with pytest.raises(PapersimReplayError, match='papersim_replay_gate_version_mismatch'):
        db.replay(candidate_id)


def test_missing_or_tampered_evidence_fails_replay():
    db, ids = fresh_db()
    candidate_id = ids['k-soccer48-accepted']
    del db.evidence[candidate_id]
    with pytest.raises(PapersimReplayError, match='papersim_replay_evidence_missing'):
        db.replay(candidate_id)
    db2, ids2 = fresh_db()
    db2.evidence[ids2['k-soccer48-accepted']]['observed_at'] += timedelta(seconds=30)
    with pytest.raises(PapersimReplayError, match='papersim_replay_evidence_time_mismatch'):
        db2.replay(ids2['k-soccer48-accepted'])
    db3, ids3 = fresh_db()
    db3.evidence[ids3['k-soccer48-accepted']]['ask'] = Decimal('0.99')
    with pytest.raises(PapersimReplayError, match='papersim_replay_evidence_ask_mismatch'):
        db3.replay(ids3['k-soccer48-accepted'])


def test_tampered_conclusion_and_reasons_fail_replay():
    db, ids = fresh_db()
    stale_id = ids['k-stale-rejected']
    db.candidates[stale_id]['conclusion'] = 'ACCEPTED'
    with pytest.raises(PapersimReplayError, match='papersim_replay_conclusion_mismatch'):
        db.replay(stale_id)
    db2, ids2 = fresh_db()
    db2.candidates[ids2['k-stale-rejected']]['rejection_reasons'] = []
    with pytest.raises(PapersimReplayError, match='papersim_replay_reasons_mismatch'):
        db2.replay(ids2['k-stale-rejected'])


def test_construction_tampering_fails_replay():
    db, ids = fresh_db()
    candidate_id = ids['k-construction-none']
    db.candidates[candidate_id]['input_payload']['failure'] = replay.CONSTRUCTION_VALUE_ERROR
    with pytest.raises(PapersimReplayError, match='papersim_replay_construction_mismatch'):
        db.replay(candidate_id)
    db2, ids2 = fresh_db()
    row = db2.candidates[ids2['k-construction-bad-line']]
    row['input_payload']['inputs']['line'] = 2.5  # valid line: refusal no longer reproduces
    with pytest.raises(PapersimReplayError, match='papersim_replay_construction_mismatch'):
        db2.replay(ids2['k-construction-bad-line'])
    db3, ids3 = fresh_db()
    db3.candidates[ids3['k-construction-bad-line']]['rejection_reasons'] = \
        [replay.CONSTRUCTION_RETURNED_NONE]
    with pytest.raises(PapersimReplayError, match='papersim_replay_reasons_mismatch'):
        db3.replay(ids3['k-construction-bad-line'])


def test_schema_version_gates_fail_replay():
    db, ids = fresh_db()
    candidate_id = ids['k-soccer48-accepted']
    with pytest.raises(PapersimReplayError, match='papersim_replay_schema_version_mismatch'):
        db.replay(candidate_id, expected_scan_schema_version=2)
    with pytest.raises(store.PapersimStoreError,
                       match='papersim_snapshot_schema_version_mismatch'):
        db.replay(candidate_id, expected_snapshot_schema_version=7)


def test_unknown_candidate_and_invalid_id_fail():
    db, _ = fresh_db()
    with pytest.raises(PapersimReplayError, match='papersim_replay_candidate_missing'):
        db.replay(999)
    with pytest.raises(PapersimReplayError, match='papersim_replay_candidate_id_invalid'):
        db.replay('7')


# ---------------------------------------------------------------------------
# Opt-in native lane (one-shot disposable cluster only)
# ---------------------------------------------------------------------------

def adapter():
    if not ENABLED:
        pytest.skip('the papersim connection adapter runs in the native lane only')
    return pytest.importorskip('polymarket_alpha_lab.papersim.store_psycopg')


def connect(dsn, *, autocommit=False):
    psycopg = pytest.importorskip('psycopg')
    connection = psycopg.connect(dsn)
    connection.autocommit = autocommit
    return connection


@pytest.fixture(scope='module')
def managed_face():
    from polymarket_alpha_lab.papersim import migrations as pm
    owner = connect(OWNER_DSN)
    # Cluster-preparation wiring (M2 plan section 6.3): the applied count
    # follows the CURRENT catalog size; migration-history count assertions
    # live against the frozen M1 catalog fixture.
    catalog = pm.papersim_migration_catalog(ROOT)
    result = pm.apply_pending_migrations(owner, ROOT)
    assert result['applied'] in range(len(catalog) + 1)
    assert pm.migration_status(owner, ROOT)['pending'] == []
    owner.close()
    return OWNER_DSN, APP_DSN


TABLES = ('scan_runs', 'snapshots', 'candidates', 'candidate_price_evidence',
          'market_outcomes', 'entries', 'marks', 'settlements', 'bankroll_events')


def table_counts(conn) -> dict:
    with conn.cursor() as cursor:
        counts = {}
        for table in TABLES:
            cursor.execute(f'SELECT count(*) FROM papersim.{table}')
            counts[table] = cursor.fetchone()[0]
    conn.commit()
    return counts


def native_submissions(app) -> tuple:
    scan = replay.bind_scan_run(team='soccer_epl', started_at=NOW - timedelta(minutes=10),
                                finished_at=NOW + timedelta(minutes=5), code_commit=COMMIT)
    submissions = []
    for key, request, team, kind, market_id in (
            ('n-soccer48-accepted', _soccer48_request(), 'soccer_epl', 'other', '5856164'),
            ('n-crypto-rejected', _crypto_no_direction_request(), 'crypto_eth', 'terminal',
             'm-eth2000-term')):
        decision = decide_entry(request)
        book_digest = snapshots.register_snapshot(app, snapshots.SnapshotRegistration(
            content=replay.bound_book_bytes(request), kind='clob_book',
            source=f'clob:{market_id}', fetched_at=request.book_time))['content_sha256']
        submissions.append(replay.bind_candidate(
            request, decision, team=team, candidate_key=key, side='YES', market_kind=kind,
            evaluated_at=NOW, market_ref_kind='market_id', market_id=market_id,
            median_probability=replay.bound_median_probability(request, decision),
            snapshot_refs=[book_digest], book_snapshot_sha256=book_digest,
            entry=replay.bind_entry(decision, entered_at=NOW) if decision.accepted else None))
    submissions.append(replay.bind_construction_failure(
        team='soccer_uefa', candidate_key='n-construction-none', evaluated_at=NOW,
        inputs=CONSTRUCTION_INPUTS_NONE, market_id='m-hof-lyo'))
    return scan, submissions


@NATIVE
def test_native_full_record_and_replay_round_trip_writes_nothing(managed_face):
    store_psycopg = adapter()
    _, app_dsn = managed_face
    app = connect(app_dsn)
    scan, submissions = native_submissions(app)
    result = store_psycopg.record_scan(app, store.ScanSubmission(scan=scan,
                                                                 candidates=tuple(submissions)))
    assert result['inserted'] is True and len(result['candidate_ids']) == 3
    before = table_counts(app)
    for key in ('n-soccer48-accepted', 'n-crypto-rejected', 'n-construction-none'):
        replayed = replay.replay(app, result['candidate_ids'][key], trusted_code_commit=COMMIT)
        assert replayed['matched'] is True, key
    assert table_counts(app) == before, 'replay must not write a single row'
    with pytest.raises(PapersimReplayError, match='papersim_replay_code_commit_mismatch'):
        replay.replay(app, result['candidate_ids']['n-soccer48-accepted'],
                      trusted_code_commit=OTHER_COMMIT)
    app.close()


@NATIVE
def test_native_tampered_snapshot_byte_fails_replay(managed_face):
    store_psycopg = adapter()
    _, app_dsn = managed_face
    owner = connect(OWNER_DSN)
    app = connect(app_dsn)
    scan, submissions = native_submissions(app)
    result = store_psycopg.record_scan(app, store.ScanSubmission(scan=scan,
                                                                 candidates=tuple(submissions)))
    digest = json.loads(
        [s for s in submissions if s.candidate_key == 'n-soccer48-accepted'][0].snapshot_refs)[0]
    # Tamper setup (throwaway cluster): drop the bytea CHECKs, disable the
    # append-only trigger, flip one byte, re-enable the guard.
    with owner.cursor() as cursor:
        cursor.execute('SELECT conname FROM pg_constraint WHERE conrelid = '
                       '\'papersim.snapshots\'::regclass AND contype = \'c\'')
        constraints = [row[0] for row in cursor.fetchall()]
        assert constraints, 'expected the snapshots CHECK constraints'
        for name in constraints:
            cursor.execute(f'ALTER TABLE papersim.snapshots DROP CONSTRAINT {name}')
        cursor.execute('ALTER TABLE papersim.snapshots DISABLE TRIGGER immutable_snapshots')
        cursor.execute('SELECT content FROM papersim.snapshots WHERE content_sha256 = %s',
                       (digest,))
        content = bytearray(bytes(cursor.fetchone()[0]))
        content[4] ^= 0x20
        cursor.execute('UPDATE papersim.snapshots SET content = %s WHERE content_sha256 = %s',
                       (bytes(content), digest))
        cursor.execute('ALTER TABLE papersim.snapshots ENABLE TRIGGER immutable_snapshots')
    owner.commit()
    with pytest.raises(store.PapersimStoreError, match='papersim_snapshot_digest_mismatch'):
        replay.replay(app, result['candidate_ids']['n-soccer48-accepted'],
                      trusted_code_commit=COMMIT)
    owner.close()
    app.close()
