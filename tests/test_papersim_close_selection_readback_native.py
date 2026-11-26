"""Check the pre-registration FX-44 readback against the managed evaluation readers."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from functools import partial

import pytest

from polymarket_alpha_lab.papersim import capture, cli, evaluation, store, store_psycopg
from tests.test_papersim_capture_marks_native import (
    NATIVE, ROOT, SCAN_OPERATION_KEY, START, T0, _candidate, _observation, _pack, app, repo,
)

pytestmark = NATIVE
READBACK = ROOT / "docs/paper-sim/design/fx-44/close-selection-readback.sql"


def _quote(key, before, *, side="YES", source="clob_book", **prices):
    return store.PriceEvidenceObservation(
        observed_at=START - timedelta(minutes=before), evidence_role="CLOSE",
        market_ref_kind="market_slug", market_ref=key, side=side,
        source_kind=source, source_ref=key, **prices,
    )


def _row(key, *quotes, boundary=START, entered=True, buy_side="YES"):
    candidate = _candidate(key, key, conclusion="ACCEPTED" if entered else "REJECTED",
                           event_start_at=boundary, with_entry=entered)
    decision, = candidate.price_evidence
    return replace(candidate, side=buy_side, price_evidence=(replace(decision, side=buy_side), *quotes))


def _record(app, *candidates):
    scan = store.ScanRunRecord(
        team="pal-b2", started_at=T0 - timedelta(minutes=2),
        finished_at=T0 - timedelta(minutes=1), code_commit="c" * 40,
        schema_version=1, gate_version="g1", operator="unattended", params="{}",
        operation_key=SCAN_OPERATION_KEY,
    )
    return store_psycopg.record_scan(
        app, store.ScanSubmission(scan=scan, candidates=candidates))["candidate_ids"]


def _read(app):
    from psycopg.rows import dict_row

    with app.transaction():
        app.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with app.cursor(row_factory=dict_row) as cursor:
            rows = cursor.execute(READBACK.read_text(encoding="utf-8")).fetchall()
        candidates = cli._evaluation_candidates_in_snapshot(app)
    return {row["candidate_id"]: row for row in rows}, candidates


@pytest.mark.parametrize("stale_arrives_last", [False, True])
def test_close_readback_uses_quote_time_and_breaks_ties_by_id(app, stale_arrives_last):
    key = "mixed"
    older = _quote(key, 58, mid=.61)
    tied = [_quote(key, 5, last=.88),
            _quote(key, 5, source="gamma_close", last=0, mid=.62)]
    unusable = [_quote(key, 2, side="NO", last=.91), _quote(key, 1, bid=.55)]
    quotes = [*tied, *unusable, older] if stale_arrives_last else [older, *unusable, *tied]
    candidate_id = _record(app, _row(key, *quotes))[key]
    rows, candidates = _read(app)
    row = rows[candidate_id]
    tie_ids = app.execute(
        "SELECT evidence_id FROM papersim.candidate_price_evidence "
        "WHERE candidate_id = %s AND observed_at = %s ORDER BY evidence_id",
        (candidate_id, START - timedelta(minutes=5)),
    ).fetchall()
    assert len(tie_ids) == 2
    assert row["window_evidence_id"] == tie_ids[1][0]
    assert row["window_status"] == "UNVERIFIED_WINDOW_MATCH"
    assert (row["window_last"], row["window_mid"], row["window_price"]) == (0, Decimal(".62"), 0)
    assert row["window_observed_at"] == START - timedelta(minutes=5)

    expected_current = Decimal(".61") if stale_arrives_last else Decimal(0)
    expected_time = START - timedelta(minutes=58 if stale_arrives_last else 5)
    assert (row["legacy_close_price"], row["legacy_price_observed_at"]) == (
        expected_current, expected_time)
    assert candidates[0].close_price == float(expected_current)
    assert cli._evaluation_candidates_from_store(app) == candidates
    b4_times = {}
    state = store_psycopg.read_pipeline_state(app, evaluation_loader=partial(
        cli._evaluation_candidates_in_snapshot, evidence_times=b4_times))
    assert state["evaluation_candidates"] == candidates
    assert b4_times["evaluate"] == expected_time


def test_close_readback_preserves_all_entries_and_distinguishes_missing_evidence(app, repo):
    ids = _record(
        app,
        _row("within", _quote("within", 59, last=1)),
        _row("edge", _quote("edge", 60, mid=.63)),
        _row("early", _quote("early", 61, last=.71)),
        _row("opposite", _quote("opposite", 1, side="NO", last=.77)),
        _row("book-only", _quote("book-only", 1, bid=.55)),
        _row("periodic"), _row("no-boundary", boundary=None), _row("empty"),
        _row("not-entered", _quote("not-entered", 1, last=.82), entered=False),
    )
    periodic = _observation("periodic", market_ref="periodic", sampling_kind="PERIODIC",
                            observed_at=START - timedelta(minutes=1))
    at_start = _observation("periodic", market_ref="periodic", sampling_kind="CLOSE", observed_at=START)
    captured = capture.run_capture(
        capture.CaptureConfig(team="pal-b2"), (_pack(periodic, at_start),),
        conn=app, repo_root=repo[0], now_utc=lambda: START + timedelta(seconds=1),
        operation_key="capture-marks|pal-b2|readback",
    )
    assert (captured["marks_written"], captured["close_evidence_written"]) == (2, 0)

    rows, candidates = _read(app)
    report = evaluation.evaluate_queues(candidates)
    queue, = report.queues
    assert report.promotion_rules_version == "odg-registered-2026-11-13+odk"
    assert ids["not-entered"] not in rows
    assert len(rows) == queue.clv.entries_eligible == 8
    legacy_with_close = sum(r["legacy_close_price"] is not None for r in rows.values())
    assert legacy_with_close == queue.clv.entries_with_close == 4
    assert queue.clv.close_coverage == .5
    criterion_c, = [result for result in queue.promotion if result.key == "C"]
    assert criterion_c.verdict == evaluation.VERDICT_NOT_ASSESSABLE
    assert {r["candidate_id"] for r in rows.values() if r["window_price"] is not None} == {
        ids["within"], ids["edge"]}
    assert (rows[ids["within"]]["window_price"], rows[ids["edge"]]["window_price"]) == (1, Decimal(".63"))
    assert rows[ids["no-boundary"]]["window_status"] == "NO_STORED_BOUNDARY"
    assert rows[ids["opposite"]]["legacy_side"] == "NO"
    assert rows[ids["opposite"]]["buy_side"] == "YES"
    for key in ("early", "opposite", "book-only", "periodic", "empty"):
        assert rows[ids[key]]["window_status"] == "NO_WINDOW_MATCH"
    assert rows[ids["book-only"]]["legacy_evidence_id"] is not None
    assert rows[ids["book-only"]]["legacy_price_observed_at"] is None
    assert rows[ids["empty"]]["legacy_evidence_id"] is None


def test_close_readback_uses_the_no_buy_side_and_excludes_the_stored_boundary(app):
    key = "buy-no"
    candidate = _row(key, _quote(key, 5, side="NO", mid=.42), _quote(key, 1, last=.90), buy_side="NO")
    cid = _record(app, replace(candidate, market_type=None))[key]
    # The historic table permits raw CLOSE rows at or after the boundary.
    # Seed both so this query's half-open window is tested independently of
    # the modern capture writer, which already downgrades such observations.
    for seconds, price in ((0, Decimal(".81")), (1, Decimal(".82"))):
        app.execute(
            "INSERT INTO papersim.candidate_price_evidence "
            "(evidence_key, candidate_id, evidence_role, observed_at, market_ref_kind, "
            "market_ref, side, last, source_kind, source_ref) "
            "VALUES (%s, %s, 'CLOSE', %s, 'market_slug', %s, 'NO', %s, 'clob_book', %s)",
            (f"readback-fixture-{seconds}", cid, START + timedelta(seconds=seconds), key, price, key),
        )

    rows, candidates = _read(app)
    row = rows[cid]
    assert (row["model_key"], row["market_type"], row["gate_version"]) == ("crypto:r19", "terminal", "g1")
    assert row["buy_side"] == "NO"
    assert row["legacy_price_observed_at"] == START + timedelta(seconds=1)
    assert row["legacy_close_price"] == Decimal(".82")
    assert candidates[0].close_price == .82
    assert row["window_price"] == Decimal(".42")
    assert row["window_observed_at"] == START - timedelta(minutes=5)
