"""B2 capture-marks offline orchestration proofs (plan 05 section 2 / FX-34).

Everything here runs without a database and without a network: the mark
source is the prepared pack (the adapter stand-in), the FX-34 classifier
is pure, and the CLI boundary is driven against the argument/error
surface only.  The native lane
(tests/test_papersim_capture_marks_native.py) proves the transactional
writes, receipts, idempotency and conflict boundaries on a disposable
cluster.

The central proofs are the FX-34 acceptance table (fix-list section 2):
a mark 1 second before the event start may be a CLOSE, 1 second after
may not, at the start may not (equality refused), during overtime (hours
after tip-off) may not, and a candidate without an event start produces
no CLOSE at all -- no rule uses game duration or a "scheduled end".
"""

from __future__ import annotations

import datetime
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_alpha_lab.papersim import capture, store

NOW = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.UTC)
EXPIRY = datetime.datetime(2026, 12, 31, 0, 0, tzinfo=datetime.UTC)
PACK_DIGEST = "b" * 64
HOSTED_DSN = "postgresql://paper_sim@db.internal.example.com:5432/paper_sim"


def observation(**overrides):
    values = dict(candidate_key="eth-2000-term-yes", scan_id=7,
                  scan_operation_key=None, sampling_kind="PERIODIC",
                  observed_at=NOW + datetime.timedelta(minutes=10),
                  market_ref_kind="market_id", market_ref="m-eth2000-term",
                  token_ref=None, side="YES", bid=0.55, ask=0.58, mid=0.565,
                  last=None, source_kind="clob_book", source_ref="book-1",
                  snapshot_sha256=None)
    values.update(overrides)
    return capture.MarkObservation(**values)


def pack_document(**overrides):
    document = {
        "candidate_key": "eth-2000-term-yes", "scan_id": 7,
        "observed_at": (NOW + datetime.timedelta(minutes=10)).isoformat(),
        "market_ref_kind": "market_id", "market_ref": "m-eth2000-term",
        "side": "YES", "bid": 0.55, "ask": 0.58, "mid": 0.565,
        "source_kind": "clob_book", "source_ref": "book-1",
    }
    document.update(overrides)
    return document


# ---------------------------------------------------------------------------
# Configuration and pack loading
# ---------------------------------------------------------------------------

def test_config_from_mapping_parses_and_rejects_drift():
    config = capture.config_from_mapping(
        {"team": "pal-b2", "operator": "unattended", "packs": ["marks.json"]})
    assert config.team == "pal-b2" and config.packs == ("marks.json",)
    with pytest.raises(capture.CaptureError, match="papersim_capture_team_invalid"):
        capture.config_from_mapping({"team": "", "packs": []})
    with pytest.raises(capture.CaptureError, match="papersim_capture_config_invalid"):
        capture.config_from_mapping({"team": "t", "packs": [], "surprise": 1})
    with pytest.raises(capture.CaptureError, match="papersim_capture_config_invalid"):
        capture.config_from_mapping({"team": "t", "packs": "marks.json"})


def test_load_mark_packs_reads_raw_bytes_and_refuses_missing_files(tmp_path):
    (tmp_path / "marks.json").write_bytes(b"[]")
    packs = capture.load_mark_packs(
        capture.CaptureConfig(team="t", packs=("marks.json",)), base_dir=tmp_path)
    assert packs == (capture.MarkPack(name="marks.json", content=b"[]"),)
    with pytest.raises(capture.CaptureError, match="papersim_capture_pack_unreadable"):
        capture.load_mark_packs(
            capture.CaptureConfig(team="t", packs=("missing.json",)), base_dir=tmp_path)


# ---------------------------------------------------------------------------
# Pack parsing: format drift fails loudly with fixed codes, never a skip
# ---------------------------------------------------------------------------

def test_observations_from_pack_round_trips_the_prepared_observation():
    parsed = capture.observations_from_pack(json.dumps([pack_document()]))
    assert len(parsed) == 1
    record = parsed[0]
    assert record.candidate_key == "eth-2000-term-yes"
    assert record.scan_id == 7 and record.scan_operation_key is None
    assert record.observed_at == NOW + datetime.timedelta(minutes=10)
    assert record.sampling_kind == "PERIODIC" and record.bid == 0.55


def test_observations_from_pack_accepts_operation_key_references():
    parsed = capture.observations_from_pack(json.dumps(
        [pack_document(scan_id=None, scan_operation_key="run-scan|pal-b1|20261120T1200Z|b1")]))
    assert parsed[0].scan_id is None
    assert parsed[0].scan_operation_key == "run-scan|pal-b1|20261120T1200Z|b1"


@pytest.mark.parametrize("raw,code", [
    ("{not json}", "papersim_capture_pack_invalid"),
    ('{"observations": []}', "papersim_capture_pack_invalid"),        # not an array
    ('["text"]', "papersim_capture_pack_invalid"),                    # not an object
    ('[{"scan_id": 7, "observed_at": "2026-11-20T12:10:00+00:00",'
     '  "market_ref": "m"}]', "papersim_capture_pack_invalid"),       # candidate_key
    ('[{"candidate_key": "k", "observed_at": "2026-11-20T12:10:00+00:00",'
     '  "market_ref": "m"}]', "papersim_capture_pack_invalid"),       # neither scan ref
    ('[{"candidate_key": "k", "scan_id": 7, "scan_operation_key": "s",'
     '  "observed_at": "2026-11-20T12:10:00+00:00", "market_ref": "m"}]',
     "papersim_capture_pack_invalid"),                                 # both scan refs
    ('[{"candidate_key": "k", "scan_id": 7, "market_ref": "m"}]',
     "papersim_capture_pack_invalid"),                                 # observed_at
    ('[{"candidate_key": "k", "scan_id": 7,'
     '  "observed_at": "2026-11-20T12:10:00", "market_ref": "m"}]',
     "papersim_capture_pack_invalid"),                                 # naive datetime
    ('[{"candidate_key": "k", "scan_id": 7,'
     '  "observed_at": "2026-11-20T12:10:00+00:00"}]',
     "papersim_capture_pack_invalid"),                                 # market_ref
    ('[{"candidate_key": "k", "scan_id": 7,'
     '  "observed_at": "2026-11-20T12:10:00+00:00", "market_ref": "m",'
     '  "sampling_kind": "INTRADAY"}]', "papersim_capture_pack_invalid"),
    ('[{"candidate_key": "k", "scan_id": 7,'
     '  "observed_at": "2026-11-20T12:10:00+00:00", "market_ref": "m",'
     '  "side": "OVER"}]', "papersim_capture_pack_invalid"),
    ('[{"candidate_key": "k", "scan_id": 7,'
     '  "observed_at": "2026-11-20T12:10:00+00:00", "market_ref": "m",'
     '  "snapshot_sha256": "zz" }]', "papersim_capture_pack_invalid"),
    ('[{"candidate_key": "k", "scan_id": 7,'
     '  "observed_at": "2026-11-20T12:10:00+00:00", "market_ref": "m",'
     '  "bid": "0.55"}]', "papersim_capture_pack_invalid"),
    ('[{"candidate_key": "k", "scan_id": 7,'
     '  "observed_at": "2026-11-20T12:10:00+00:00", "market_ref": "m",'
     '  "unknown_field": 1}]', "papersim_capture_pack_invalid"),
])
def test_pack_format_drift_fails_loudly_with_fixed_codes(raw, code):
    with pytest.raises(capture.CaptureError, match=code):
        capture.observations_from_pack(raw)


# ---------------------------------------------------------------------------
# FX-34 classification (the acceptance table)
# ---------------------------------------------------------------------------

def test_fx34_classification_table():
    start = NOW + datetime.timedelta(hours=2)
    classify = capture.classify_observation
    # 1 second before the start: the only CLOSE-eligible form.
    assert classify("CLOSE", start - datetime.timedelta(seconds=1), start) == ("CLOSE", None)
    # AT the start: refused (equality refused).
    assert classify("CLOSE", start, start) == \
        ("PERIODIC", capture.DOWNGRADE_OBSERVED_AT_AFTER_EVENT_START)
    # 1 second after the start: PERIODIC only.
    assert classify("CLOSE", start + datetime.timedelta(seconds=1), start) == \
        ("PERIODIC", capture.DOWNGRADE_OBSERVED_AT_AFTER_EVENT_START)
    # NBA full-game overtime shape: hours after tip-off, still PERIODIC;
    # no rule uses game duration or a "scheduled end" anywhere.
    assert classify("CLOSE", start + datetime.timedelta(hours=3), start) == \
        ("PERIODIC", capture.DOWNGRADE_OBSERVED_AT_AFTER_EVENT_START)
    # Missing event start: no CLOSE at all (counted missing in C coverage).
    assert classify("CLOSE", start - datetime.timedelta(seconds=1), None) == \
        ("PERIODIC", capture.DOWNGRADE_MISSING_EVENT_START)
    # A PERIODIC request stays PERIODIC with nothing to report.
    assert classify("PERIODIC", start + datetime.timedelta(hours=3), start) == \
        ("PERIODIC", None)
    assert classify("PERIODIC", NOW, None) == ("PERIODIC", None)


def test_fx34_crypto_boundary_is_the_market_expiry():
    # Crypto event_start_at IS the market end/expiry (FX-26 migration
    # comment): an observation before expiry may CLOSE, at/after may not.
    before = EXPIRY - datetime.timedelta(seconds=1)
    assert capture.classify_observation("CLOSE", before, EXPIRY) == ("CLOSE", None)
    assert capture.classify_observation("CLOSE", EXPIRY, EXPIRY)[0] == "PERIODIC"
    assert capture.classify_observation("CLOSE", EXPIRY + datetime.timedelta(seconds=1),
                                        EXPIRY)[0] == "PERIODIC"


# ---------------------------------------------------------------------------
# Record construction and missing-quote accounting
# ---------------------------------------------------------------------------

def test_capture_and_close_evidence_cite_the_pack_digest_by_default():
    record = observation(snapshot_sha256=None)
    mark = capture.capture_from_observation(
        record, candidate_id=41, sampling_kind="PERIODIC",
        pack_snapshot_sha256=PACK_DIGEST)
    assert isinstance(mark, store.CandidateMarkCapture)
    assert mark.candidate_id == 41 and mark.sampling_kind == "PERIODIC"
    assert mark.snapshot_sha256 == PACK_DIGEST
    # An observation-level citation wins over the pack digest.
    own = capture.capture_from_observation(
        observation(snapshot_sha256="c" * 64), candidate_id=41,
        sampling_kind="CLOSE", pack_snapshot_sha256=PACK_DIGEST)
    assert own.snapshot_sha256 == "c" * 64


def test_capture_from_observation_keeps_terminal_closed_interval_prices():
    mark = capture.capture_from_observation(
        observation(bid=None, ask=None, mid=None, last=1), candidate_id=41,
        sampling_kind="CLOSE", pack_snapshot_sha256=PACK_DIGEST)
    mark.validate()  # mark semantics: closed interval [0,1] is legal
    evidence = capture.close_evidence_from_observation(
        observation(bid=None, ask=None, mid=None, last=1),
        pack_snapshot_sha256=PACK_DIGEST)
    evidence.validate()  # terminal 0/1 travels as a reference `last`


def test_has_any_price_counts_only_all_absent_as_missing_quote():
    assert capture.has_any_price(observation()) is True
    assert capture.has_any_price(observation(bid=None, ask=None, mid=None, last=None)) is False
    assert capture.has_any_price(observation(bid=None, ask=None, mid=None, last=0.5)) is True


def test_coverage_terms_quote_the_fx27_wording_verbatim():
    assert "close marks over ALL entries" in capture.COVERAGE_TERMS
    assert "entries_with_close / entries_eligible" in capture.COVERAGE_TERMS
    assert "lack a close mark count as missing" in capture.COVERAGE_TERMS


# ---------------------------------------------------------------------------
# FX-03 clean-worktree guard (refusal precedes any connection use)
# ---------------------------------------------------------------------------

def _git_repo(path: Path) -> Path:
    path.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "T"]):
        subprocess.run(["git", "-C", str(path), *args], check=True,
                       capture_output=True, text=True)
    (path / "seed.txt").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "seed.txt"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "seed"], check=True,
                   capture_output=True, text=True)
    return path


def test_run_capture_refuses_a_dirty_worktree_before_touching_the_connection(tmp_path):
    from polymarket_alpha_lab.papersim.scan import ScanError

    repo = _git_repo(tmp_path / "repo")
    (repo / "dirt.txt").write_text("dirt", encoding="utf-8")
    with pytest.raises(ScanError) as error:
        capture.run_capture(capture.CaptureConfig(team="t"), (),
                            conn=None, repo_root=repo, now_utc=lambda: NOW)
    assert error.value.code == "papersim_scan_worktree_dirty"


# ---------------------------------------------------------------------------
# CLI boundary (argument surface and the audited DSN validator)
# ---------------------------------------------------------------------------

def test_capture_marks_requires_config_and_exactly_one_source(tmp_path):
    from polymarket_alpha_lab.papersim import cli

    config = tmp_path / "capture.json"
    config.write_text(json.dumps({"team": "t", "packs": []}), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["capture-marks"])
    assert excinfo.value.code == 2
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["capture-marks", "--config", str(config)])
    assert excinfo.value.code == 2
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["capture-marks", "--config", str(config),
                  "--managed", "--dsn", HOSTED_DSN])
    assert excinfo.value.code == 2


def test_capture_marks_rejects_unauthorized_dsn_before_any_connection(tmp_path):
    # A valid config and pack must not mask the audited validator: the
    # hosted DSN is rejected before any connection is constructed, the
    # failure is reported under the command prefix, and the rejected
    # value is never echoed back.
    (tmp_path / "marks.json").write_text("[]", encoding="utf-8")
    config = tmp_path / "capture.json"
    config.write_text(json.dumps({"team": "t", "packs": ["marks.json"]}),
                      encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "polymarket_alpha_lab.papersim.cli",
         "capture-marks", "--config", str(config), "--dsn", HOSTED_DSN],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 1
    assert "capture-marks: FAIL" in result.stderr
    assert "must point to local Postgres" in result.stderr
    assert "db.internal.example.com" not in result.stderr


# ---------------------------------------------------------------------------
# Module hygiene
# ---------------------------------------------------------------------------

def test_capture_module_never_imports_the_cli_or_the_driver():
    script = (
        "import sys\n"
        "from polymarket_alpha_lab.papersim import capture\n"
        "assert 'polymarket_alpha_lab.papersim.cli' not in sys.modules, 'capture pulled cli'\n"
        "assert 'polymarket_alpha_lab.papersim.store_psycopg' not in sys.modules,"
        " 'module import pulled the driver'\n"
        "print('ok')\n"
    )
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True,
                               text=True, check=True)
    assert completed.stdout.strip().endswith("ok")


_ODP_EVENT_BOUNDARIES = (
    pytest.param(NOW, id="sport-start"),
    pytest.param(EXPIRY, id="crypto-expiry"),
)


@pytest.mark.parametrize("event_start_at", _ODP_EVENT_BOUNDARIES)
def test_odp_legacy_classifier_keeps_close_at_61_minutes(event_start_at):
    observed_at = event_start_at - datetime.timedelta(minutes=61)
    assert capture.classify_observation("CLOSE", observed_at, event_start_at) == (
        "CLOSE", None,
    )


@pytest.mark.parametrize("event_start_at", _ODP_EVENT_BOUNDARIES)
@pytest.mark.parametrize(
    ("mark_kind", "observed_minutes", "received_minutes", "expected"),
    [
        pytest.param(
            "CLOSE", -61, None, ("PERIODIC", "observed_at_before_close_window"),
            id="before-window",
        ),
        pytest.param(
            "CLOSE", -61, 0, ("PERIODIC", "observed_at_before_close_window"),
            id="before-window-received-at-event",
        ),
        pytest.param(
            "CLOSE", -61, 1, ("PERIODIC", "observed_at_before_close_window"),
            id="before-window-received-after-event",
        ),
        pytest.param("CLOSE", -60, None, ("CLOSE", None), id="window-start"),
        pytest.param("CLOSE", -59, None, ("CLOSE", None), id="inside-window"),
        pytest.param(
            "CLOSE", 0, None, ("PERIODIC", "observed_at_after_event_start"),
            id="observed-at-event",
        ),
        pytest.param(
            "CLOSE", 1, None, ("PERIODIC", "observed_at_after_event_start"),
            id="observed-after-event",
        ),
        pytest.param(
            "CLOSE", 0, 0, ("PERIODIC", "observed_at_after_event_start"),
            id="observed-and-received-at-event",
        ),
        pytest.param(
            "CLOSE", -59, 0, ("PERIODIC", "received_at_after_event_start"),
            id="received-at-event",
        ),
        pytest.param(
            "CLOSE", -59, 1, ("PERIODIC", "received_at_after_event_start"),
            id="received-after-event",
        ),
        pytest.param("PERIODIC", 0, None, ("PERIODIC", None), id="requested-periodic"),
    ],
)
def test_odp_observation_timing(
    event_start_at, mark_kind, observed_minutes, received_minutes, expected,
):
    observed_at = event_start_at + datetime.timedelta(minutes=observed_minutes)
    received_at = (
        None if received_minutes is None
        else event_start_at + datetime.timedelta(minutes=received_minutes)
    )
    assert capture._classify_odp_observation(
        mark_kind, observed_at, event_start_at, received_at=received_at,
    ) == expected


@pytest.mark.parametrize("event_start_at", _ODP_EVENT_BOUNDARIES)
def test_odp_missing_event_start(event_start_at):
    observed_at = event_start_at - datetime.timedelta(minutes=59)
    assert capture._classify_odp_observation("CLOSE", observed_at, None) == (
        "PERIODIC", "missing_event_start",
    )
    assert capture._classify_odp_observation(
        "CLOSE", observed_at, event_start_at.replace(tzinfo=None),
    ) == ("PERIODIC", "missing_event_start")
    assert capture._classify_odp_observation("PERIODIC", observed_at, None) == (
        "PERIODIC", None,
    )


def test_odp_uses_utc_instants_across_folds():
    class RepeatedHour(datetime.tzinfo):
        def utcoffset(self, value):
            if value is None:
                return None
            return datetime.timedelta(hours=-4 - value.fold)

    first_fold = datetime.datetime(2026, 11, 1, 1, 30, tzinfo=RepeatedHour(), fold=0)
    second_fold = first_fold.replace(fold=1)
    assert capture._classify_odp_observation("CLOSE", first_fold, second_fold) == (
        "CLOSE", None,
    )
    assert capture._classify_odp_observation(
        "CLOSE", first_fold - datetime.timedelta(minutes=59), first_fold,
        received_at=first_fold.replace(minute=29, fold=1),
    ) == ("PERIODIC", "received_at_after_event_start")
    assert capture._classify_odp_observation(
        "CLOSE", first_fold.replace(minute=29), second_fold, received_at=second_fold,
    ) == ("PERIODIC", "observed_at_before_close_window")
