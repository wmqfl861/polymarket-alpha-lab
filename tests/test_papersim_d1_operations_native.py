"""Native fault and concurrency tests for the managed D1 operation adapter."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
import sys
import time

import pytest

from polymarket_alpha_lab.papersim import store_psycopg as pg
from tests.test_papersim_capture_marks_native import (
    _D1_NATIVE,
    _d1_capture_cli,
    _d1_capture_receipt,
    _d1_capture_rows,
    _d1_register_capture_slot,
    _d1_seed_capture_case,
    d1_capture_project,
)

pytestmark = _D1_NATIVE
_FAULT_CODE = "d1_native_fixture_writer_failed"
_EXIT_DURING_TRANSACTION = 87
_INTERRUPT = (
    "from tests.test_papersim_d1_operations_native import _interrupt; _interrupt()"
)


def _interrupt():
    """Interrupt the real subprocess inside its open PostgreSQL transaction."""
    from polymarket_alpha_lab.papersim import cli, store
    from polymarket_alpha_lab.papersim.d1compare import operations

    fault = sys.argv[1]
    if fault == "preparing":
        append = operations._append_document

        def after_snapshot(conn, kind, source, document, at):
            refs = append(conn, kind, source, document, at)
            if kind == "d1_operation_prepared":
                os._exit(_EXIT_DURING_TRANSACTION)
            return refs

        operations._append_document = after_snapshot
    else:
        writer = pg.mark_capture_upsert
        position = 0

        def after_writer(conn, capture, **kwargs):
            nonlocal position
            result = writer(conn, capture, **kwargs)
            position += 1
            if position == 2:
                if fault == "writer-error":
                    raise store.PapersimStoreError(_FAULT_CODE)
                os._exit(_EXIT_DURING_TRANSACTION)
            return result

        pg.mark_capture_upsert = after_writer
    raise SystemExit(cli.main(sys.argv[2:]))


def _read_witnesses(project, *, command, keys=None):
    with pg.connect_managed(project.root) as reader, reader.transaction():
        reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        assert reader.execute("SELECT current_user").fetchone() == ("pal_app",)
        rows = reader.execute(
            "SELECT r.operation_key, r.attempt, r.phase, r.run_event_id, r.occurred_at, "
            "r.error_code, r.output_evidence, r.code_commit, r.paper_only, r.report_only, r.readonly, "
            "s.content, s.content_sha256, s.byte_length "
            "FROM papersim.command_run_events r JOIN papersim.snapshots s "
            "ON (r.output_evidence->>0) = ('snapshot_sha256:' || s.content_sha256) "
            "WHERE r.command = %s AND (%s::text[] IS NULL OR r.operation_key = ANY(%s::text[])) "
            "ORDER BY r.run_event_id", (command, keys, keys),
        ).fetchall()
    witnesses = []
    for key, attempt, phase, receipt_id, at, error, refs, commit, paper, report, readonly, raw, digest, length in rows:
        assert refs == [f"snapshot_sha256:{digest}", f"snapshot_byte_length:{length}"]
        assert sha256(raw).hexdigest() == digest and len(raw) == length
        assert (commit, paper, report, readonly) == (project.head, True, True, True)
        document = json.loads(raw)
        assert document["attempt"] == attempt and datetime.fromisoformat(document["recorded_at"]) == at
        witnesses.append({"key": key, "attempt": attempt, "phase": phase, "receipt_id": receipt_id,
                          "at": at, "error": error, "document": document})
    return witnesses


def _items(project, slot, prepared):
    keys = []
    for target in prepared["body"]["plan"]["targets"]:
        raw = json.dumps({"parent": slot.key, "identity": target["identity"]}, sort_keys=True,
                         separators=(",", ":")).encode("ascii")
        keys.append(f"d1:operation-item:v1:{sha256(raw).hexdigest()}")
    rows = _read_witnesses(project, command="d1-capture-item", keys=keys)
    for row in rows:
        proof = row["document"]
        assert proof["operation_key"] == slot.key and proof["item_operation_key"] == row["key"]
        assert proof["prepared"]["run_event_id"] == prepared["id"]
        assert proof["prepared_reference"][0] == f"snapshot_sha256:{prepared['sha256']}"
    return rows


def _parent_phases(project, slot):
    with pg.connect_managed(project.root) as reader, reader.transaction():
        reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return reader.execute(
            "SELECT attempt, phase FROM papersim.command_run_events "
            "WHERE operation_key = %s ORDER BY run_event_id", (slot.key,),
        ).fetchall()


@pytest.mark.parametrize("fault", ("preparing", "before-item-commit", "writer-error"))
def test_d1_native_retries_preserve_atomic_business_and_attempt_evidence(
        d1_capture_project, record_property, fault):
    project = d1_capture_project
    case = _d1_seed_capture_case(project, f"operation-{fault}")
    slot = _d1_register_capture_slot(case, "original")
    original_rows = _d1_capture_rows(project, case.candidate_ids)
    stopped = _d1_capture_cli(project, slot, code=_INTERRUPT, extra=(fault,))
    expected_exit = 1 if fault == "writer-error" else _EXIT_DURING_TRANSACTION
    assert stopped.returncode == expected_exit and not stopped.stdout
    partial = _d1_capture_rows(project, case.candidate_ids)
    prepared = _d1_capture_receipt(project, slot, "PREPARED")
    first_failed = _d1_capture_receipt(project, slot, "FAILED")
    if fault == "preparing":
        assert partial == original_rows and prepared is None and first_failed is None
        assert _parent_phases(project, slot) == [(1, "STARTED")]
        pack_sha256 = sha256(slot.pack.read_bytes()).hexdigest()
        with pg.connect_managed(project.root) as reader, reader.transaction():
            reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            assert reader.execute("SELECT 1 FROM papersim.snapshots WHERE content_sha256 = %s",
                                  (pack_sha256,)).fetchone() is None
    else:
        assert prepared is not None
        assert [mark[2] for mark in partial["mark"]] == [case.candidate_ids[0]]
        assert [close[2] for close in partial["close"]] == [case.candidate_ids[0]]
        assert partial["decision"] == original_rows["decision"]
        receipts = _items(project, slot, prepared)
        assert receipts[0]["document"]["status"] == "WRITTEN"
        assert receipts[0]["document"]["business_ids"] == {
            "candidate_mark_id": partial["mark"][0][0], "close_evidence_id": partial["close"][0][0],
        }
        if fault == "writer-error":
            assert _FAULT_CODE in stopped.stderr
            assert first_failed["error"] == _FAULT_CODE
            assert [item["phase"] for item in receipts] == ["SUCCEEDED", "FAILED"]
            assert receipts[1]["error"] == _FAULT_CODE
            assert receipts[1]["document"]["before"] == {"marks": [], "close_evidence": []}
            assert receipts[1]["document"]["business"] is None
            assert first_failed["body"]["items"][-1]["run_event_id"] == receipts[1]["receipt_id"]

            pack = slot.pack.read_bytes()
            try:
                slot.pack.write_bytes(pack + b"\n")
                conflict = _d1_capture_cli(project, slot)
            finally:
                slot.pack.write_bytes(pack)
            assert conflict.returncode == 1 and "d1_operation_binding_conflict" in conflict.stderr
            conflicts = [row for row in _read_witnesses(project, command="d1-operation-conflict")
                         if row["document"]["operation_key"] == slot.key]
            assert len(conflicts) == 1 and conflicts[0]["error"] == "d1_operation_binding_conflict"
            assert first_failed["id"] in [row["run_event_id"] for row in conflicts[0]["document"]["original"]]
            assert _d1_capture_receipt(project, slot, "FAILED") == first_failed
        else:
            assert first_failed is None and len(receipts) == 1

    resumed = _d1_capture_cli(project, slot, attempt=2)
    assert resumed.returncode == 0, resumed.stderr
    summary = json.loads(resumed.stdout)
    status = "WRITTEN" if fault == "preparing" else "REUSED"
    assert [item["status"] for item in summary["items"]] == [status, "WRITTEN", "MISSING_QUOTE"]
    final_rows = _d1_capture_rows(project, case.candidate_ids)
    assert [row[2] for row in final_rows["mark"]] == list(case.candidate_ids[:2])
    assert [row[2] for row in final_rows["close"]] == list(case.candidate_ids[:2])
    assert final_rows["decision"] == original_rows["decision"]
    if prepared is None:
        prepared = _d1_capture_receipt(project, slot, "PREPARED", attempt=2)
        assert _d1_capture_receipt(project, slot, "RECOVERY_READ", attempt=2) is None
    else:
        assert _d1_capture_receipt(project, slot, "PREPARED") == prepared
        assert final_rows["mark"][:1] == partial["mark"] and final_rows["close"][:1] == partial["close"]
        recovery = _d1_capture_receipt(project, slot, "RECOVERY_READ", attempt=2)
        assert recovery["body"]["prepared"]["run_event_id"] == prepared["id"]
        if first_failed is not None:
            assert recovery["body"]["first_failure"]["run_event_id"] == first_failed["id"]
            assert recovery["body"]["first_item_failure"]["run_event_id"] == receipts[1]["receipt_id"]
        assert summary["items"][0]["first_write"]["run_event_id"] == receipts[0]["receipt_id"]
        assert summary["items"][0]["first_write_at"] == receipts[0]["document"]["write_finished_at"]
    original_receipts = _items(project, slot, prepared)
    assert json.loads(_d1_capture_cli(project, slot, attempt=2).stdout) == summary
    assert _items(project, slot, prepared) == original_receipts
    assert _d1_capture_rows(project, case.candidate_ids) == final_rows
    record_property("atomic_recovery", json.dumps({
        "slot": slot.key, "attempt_1_exit": stopped.returncode, "attempt_2_exit": resumed.returncode,
        "prepared": prepared["sha256"], "items": summary["items"],
        "partial": partial, "final": final_rows,
    }, sort_keys=True, default=str))


def _wait_for(check, description):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    pytest.fail(f"Timed out waiting for {description}")


def _advisory_waiters(project):
    with pg.connect_managed(project.root) as reader, reader.transaction():
        reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return reader.execute(
            "SELECT count(*) FROM pg_catalog.pg_locks "
            "WHERE locktype = 'advisory' AND NOT granted "
            "AND database = (SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database())",
        ).fetchone()[0]


@pytest.mark.parametrize("same_parent", (True, False), ids=("same-attempt", "competing-close"))
def test_d1_native_concurrent_attempts_keep_the_first_close(
        d1_capture_project, record_property, same_parent):
    project = d1_capture_project
    label = "same" if same_parent else "different"
    case = _d1_seed_capture_case(project, f"operation-concurrent-{label}", ("quote",))
    first = _d1_register_capture_slot(case, "first")
    other = first
    if not same_parent:
        # A different natural key on the same candidate still competes for
        # the candidate's only CLOSE mark and CLOSE evidence.
        alternate = case.marks[0] | {"market_ref_kind": "token_id", "market_ref": case.marks[0]["token_ref"]}
        other = _d1_register_capture_slot(case, "other", (alternate,))
    candidate_id, = case.candidate_ids
    with ThreadPoolExecutor(max_workers=2) as workers:
        with pg.connect_managed(project.root) as gate, gate.transaction():
            gate.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                         (f"papersim/mark-candidate/{candidate_id}",))
            left = workers.submit(_d1_capture_cli, project, first)
            _wait_for(lambda: _d1_capture_receipt(project, first, "PREPARED"), "the first frozen read set")
            right = workers.submit(_d1_capture_cli, project, other)
            _wait_for(lambda: _advisory_waiters(project) == 2, "both CLI processes to reach their locks")
            rows = _d1_capture_rows(project, case.candidate_ids)
            assert not rows["mark"] and not rows["close"]
        results = [left.result(), right.result()]
    final_rows = _d1_capture_rows(project, case.candidate_ids)
    assert len(final_rows["mark"]) == len(final_rows["close"]) == 1
    assert final_rows["decision"] == rows["decision"]
    if same_parent:
        assert [result.returncode for result in results] == [0, 0]
        assert results[0].stdout == results[1].stdout
        successes = [(first, json.loads(results[0].stdout))]
    else:
        assert sorted(result.returncode for result in results) == [0, 1]
        successes = []
        for slot, result in zip((first, other), results, strict=True):
            if result.returncode == 0:
                successes.append((slot, json.loads(result.stdout)))
                continue
            assert "d1_capture_unattributed_rows" in result.stderr
            failed = _d1_capture_receipt(project, slot, "FAILED")
            prepared = _d1_capture_receipt(project, slot, "PREPARED")
            refused, = _items(project, slot, prepared)
            assert failed["error"] == refused["error"] == "d1_capture_unattributed_rows"
            proof = refused["document"]
            assert proof["status"] == "CONFLICT"
            assert proof["before"]["marks"][0]["candidate_mark_id"] == final_rows["mark"][0][0]
            assert proof["before"]["close_evidence"][0]["evidence_id"] == final_rows["close"][0][0]
    [(winner, result)] = successes
    assert _parent_phases(project, winner) == [(1, "STARTED"), (1, "PREPARED"), (1, "SUCCEEDED")]
    prepared = _d1_capture_receipt(project, winner, "PREPARED")
    published, = _items(project, winner, prepared)
    item, = result["items"]
    assert item["status"] == "WRITTEN" and item["first_write"]["run_event_id"] == published["receipt_id"]
    assert item["candidate_mark_id"] == final_rows["mark"][0][0]
    assert item["close_evidence_id"] == final_rows["close"][0][0]
    record_property("concurrent_close", json.dumps({
        "slots": [first.key, other.key], "winner": winner.key, "exit": [result.returncode for result in results],
        "item": item, "mark": final_rows["mark"][0], "close": final_rows["close"][0],
    }, sort_keys=True, default=str))


def test_d1_native_late_recovery_proves_committed_rows_and_expires_unwritten_rows(d1_capture_project):
    project = d1_capture_project
    case = _d1_seed_capture_case(project, "operation-late", ("quote", "bid-only"))
    slot = _d1_register_capture_slot(case, "original", deadline_seconds=8, write_deadline_seconds=10)
    stopped = _d1_capture_cli(project, slot, extra=("after", "1"), code=(
        "from tests.test_papersim_capture_marks_native import _d1_stop_capture_process; _d1_stop_capture_process()"))
    assert stopped.returncode == 86
    partial = _d1_capture_rows(project, case.candidate_ids)
    assert len(partial["mark"]) == len(partial["close"]) == 1
    deadline = slot.document["write_deadline"]
    time.sleep(max(0, (deadline - datetime.now(UTC)).total_seconds()) + 0.05)

    late = _d1_capture_cli(project, slot, attempt=2)
    assert late.returncode == 1 and "d1_operation_write_expired" in late.stderr
    assert _d1_capture_rows(project, case.candidate_ids) == partial
    recovered = _d1_capture_receipt(project, slot, "RECOVERY_READ", attempt=2)
    assert recovered["occurred_at"] >= deadline and recovered["body"]["late"] is True
    prepared = _d1_capture_receipt(project, slot, "PREPARED")
    written, reused, expired = _items(project, slot, prepared)
    assert [item["document"]["status"] for item in (written, reused, expired)] == ["WRITTEN", "REUSED", "EXPIRED"]
    assert reused["document"]["origin"]["run_event_id"] == written["receipt_id"]
    assert reused["document"]["write_finished_at"] == written["document"]["write_finished_at"]
    assert expired["document"]["business_ids"] == {"candidate_mark_id": None, "close_evidence_id": None}
    assert _d1_capture_receipt(project, slot, "SUCCEEDED", attempt=2) is None
