"""Register a future OD-P window in the existing managed D1 evidence ledger."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import re

from . import operations
from .. import snapshots, store, store_psycopg
from ..scan import require_clean_worktree


_RULE = "OD-P"
_COMMAND = "d1-register-interval"
_SOURCE = "d1:operation:interval:v1"
_KEY_PREFIX = "d1:interval-binding:v1:"


def _verify_registration(conn, document, digest, key, receipt):
    expected = operations.canonical_document(document)
    refs = receipt["output_evidence"]
    sealed = operations._read_document(conn, refs, kind=operations._INTERVAL_KIND, source=_SOURCE)
    snapshot = snapshots.verify_snapshot(conn, digest)
    start, end = operations._utc(sealed["starts_at"]), operations._utc(sealed["ends_at"])
    at = receipt["occurred_at"]
    if (sealed != expected or refs != operations._refs(digest, snapshot["byte_length"])
            or receipt["operation_key"] != key or receipt["phase"] != "SUCCEEDED" or receipt["attempt"] != 1
            or receipt["run_event_key"] != store.command_run_event_key(key, 1, "SUCCEEDED")
            or receipt["command"] != _COMMAND or any(receipt[k] is not None for k in ("scan_id", "queue_id", "error_code"))
            or receipt["scheduled_for"] != start or receipt["config_digest"] != digest
            or receipt["input_evidence"] != [] or any(receipt[k] is not True for k in ("paper_only", "report_only", "readonly"))
            or not isinstance(receipt["code_commit"], str) or not re.fullmatch("[0-9a-f]{40}", receipt["code_commit"])
            or snapshot["fetched_at"] != at or not at < start < end):
        raise store.PapersimStoreError("d1_close_window_registration_conflict")
    return {"interval_sha256": digest, "byte_length": snapshot["byte_length"], "close_window_policy": _RULE,
            "registered_at": at.isoformat(), "starts_at": start.isoformat(), "ends_at": end.isoformat(),
            "code_commit": receipt["code_commit"]}


def _check_overlap(conn, instance, team, start, end):
    rows = conn.execute("SELECT output_evidence FROM papersim.command_run_events "
                        "WHERE command = %s AND phase = 'SUCCEEDED' ORDER BY run_event_id", (_COMMAND,))
    for (refs,) in rows.fetchall():
        existing = operations._read_document(conn, refs, kind=operations._INTERVAL_KIND, source=_SOURCE)
        if (existing.get("instance_id") == instance and existing.get("team") == team
                and existing.get("close_window_policy") is not None
                and operations._utc(existing["starts_at"]) < end and start < operations._utc(existing["ends_at"])):
            raise store.PapersimStoreError("d1_close_window_registration_overlap")


def register(*, repo_root: Path, team: str, starts_at, ends_at) -> dict:
    """Keep one immutable, prospectively received interval. No capture, schedule or rule activation."""
    if type(team) is not str or not team.strip():
        raise store.PapersimStoreError("d1_close_window_team_invalid")
    start, end = operations._utc(starts_at), operations._utc(ends_at)
    if not start < end:
        raise store.PapersimStoreError("d1_close_window_range_invalid")
    commit = require_clean_worktree(repo_root)
    with closing(store_psycopg.connect_managed(repo_root)) as conn:
        instance = store_psycopg.d1_managed_identity(conn, repo_root)["instance_id"]
        document = {"version": 1, "instance_id": instance, "team": team, "close_window_policy": _RULE,
                    "starts_at": start, "ends_at": end}
        content = operations.document_bytes(document)
        digest = snapshots.snapshot_content_sha256(content)
        key = _KEY_PREFIX + digest
        # The team lock serializes overlapping requests with different content hashes.
        scope = snapshots.snapshot_content_sha256(operations.document_bytes({"instance": instance, "team": team}))
        with store_psycopg.d1_operation_lock(conn, "d1:close-window-registration:" + scope), conn.transaction():
            receipt = store_psycopg.read_command_run_event(conn, key, 1, "SUCCEEDED")
            if receipt is None:
                _check_overlap(conn, instance, team, start, end)
                at = store_psycopg.d1_database_time(conn)
                if at >= start:
                    raise store.PapersimStoreError("d1_close_window_registration_not_prospective")
                refs = operations._append_document(conn, operations._INTERVAL_KIND, _SOURCE, document, at)
                store_psycopg.record_command_run_event(conn, store.CommandRunEventSubmission(
                    operation_key=key, attempt=1, phase="SUCCEEDED", command=_COMMAND, scheduled_for=start,
                    occurred_at=at, code_commit=commit, config_digest=digest, output_evidence=refs))
                receipt = store_psycopg.read_command_run_event(conn, key, 1, "SUCCEEDED")
                if receipt is None:
                    raise store.PapersimStoreError("d1_close_window_registration_missing")
                # Re-check after acquiring snapshot/receipt locks, before publishing either row.
                if store_psycopg.d1_database_time(conn) >= start:
                    raise store.PapersimStoreError("d1_close_window_registration_not_prospective")
            return _verify_registration(conn, document, digest, key, receipt)
