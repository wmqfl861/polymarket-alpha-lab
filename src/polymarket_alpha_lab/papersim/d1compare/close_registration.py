"""Register prospective research intervals in the existing managed D1 evidence ledger."""
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
_BASIS_KIND = "papersim_research_book_age_basis"
_BASIS_SOURCE = "papersim:research:book-age-policy"
_AGE_FIELD = "book_age_policy"
_CLOSE_FIELD = "close_window_policy"


def _verify_basis(conn, digest, expected_bytes, at):
    info = snapshots.verify_snapshot(conn, digest, expected_schema_version=1)
    if (info["kind"] != _BASIS_KIND or info["source"] != _BASIS_SOURCE or info["fetched_at"] > at):
        raise store.PapersimStoreError("d1_book_age_basis_invalid")
    if info["byte_length"] != len(expected_bytes) or snapshots.snapshot_content_sha256(expected_bytes) != digest:
        raise store.PapersimStoreError("d1_book_age_basis_mismatch")


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
    common = {"interval_sha256": digest, "byte_length": snapshot["byte_length"],
              "registered_at": at.isoformat(), "starts_at": start.isoformat(), "ends_at": end.isoformat(),
              "code_commit": receipt["code_commit"]}
    if _AGE_FIELD in sealed:
        return {**common, _AGE_FIELD: sealed[_AGE_FIELD], "book_source": sealed["book_source"],
                "basis_sha256": sealed["basis_sha256"], "basis_byte_length": sealed["basis_byte_length"]}
    return {**common, _CLOSE_FIELD: _RULE}


def _check_overlap(conn, instance, team, start, end, field):
    rows = conn.execute("SELECT output_evidence FROM papersim.command_run_events "
                        "WHERE command = %s AND phase = 'SUCCEEDED' ORDER BY run_event_id", (_COMMAND,))
    for (refs,) in rows.fetchall():
        existing = operations._read_document(conn, refs, kind=operations._INTERVAL_KIND, source=_SOURCE)
        if (existing.get("instance_id") == instance and existing.get("team") == team
                and existing.get(field) is not None
                and operations._utc(existing["starts_at"]) < end and start < operations._utc(existing["ends_at"])):
            code = "d1_book_age_policy_overlap" if field == _AGE_FIELD else "d1_close_window_registration_overlap"
            raise store.PapersimStoreError(code)


def register(*, repo_root: Path, team: str, starts_at, ends_at) -> dict:
    """Record the original OD-P research window."""
    return _register(repo_root, team, starts_at, ends_at, {_CLOSE_FIELD: _RULE}, _CLOSE_FIELD)


def register_book_age(*, repo_root: Path, team: str, starts_at, ends_at,
                      max_source_age_seconds: int, max_receipt_age_seconds: int, basis_sha256: str) -> dict:
    """Register a scoped research decision already preserved in the same managed store."""
    from .. import prepare
    from ..adapters import polymarket

    policy = prepare.BookAgePolicy(max_source_age_seconds, max_receipt_age_seconds)
    digest = snapshots.snapshot_digest_argument(basis_sha256)
    fields = {_AGE_FIELD: {"max_source_age_seconds": policy.max_source_age_seconds,
                           "max_receipt_age_seconds": policy.max_receipt_age_seconds},
              "book_source": polymarket.SOURCE_KIND_CLOB, "basis_sha256": digest}
    return _register(repo_root, team, starts_at, ends_at, fields, _AGE_FIELD)


def _register(repo_root, team, starts_at, ends_at, fields, field):
    if type(team) is not str or not team.strip():
        raise store.PapersimStoreError("d1_close_window_team_invalid")
    start, end = operations._utc(starts_at), operations._utc(ends_at)
    if not start < end:
        raise store.PapersimStoreError("d1_close_window_range_invalid")
    commit = require_clean_worktree(repo_root)
    with closing(store_psycopg.connect_managed(repo_root)) as conn:
        instance = store_psycopg.d1_managed_identity(conn, repo_root)["instance_id"]
        document = {"version": 1, "instance_id": instance, "team": team, "starts_at": start, "ends_at": end, **fields}
        if field == _AGE_FIELD:
            basis_bytes = operations.document_bytes({k: v for k, v in document.items() if k != "basis_sha256"})
            document["basis_byte_length"] = len(basis_bytes)
        digest = snapshots.snapshot_content_sha256(operations.document_bytes(document))
        key = _KEY_PREFIX + digest
        # Both registration commands take the existing same-team session lock before the transaction.
        scope = snapshots.snapshot_content_sha256(operations.document_bytes({"instance": instance, "team": team}))
        with store_psycopg.d1_operation_lock(conn, "d1:close-window-registration:" + scope), conn.transaction():
            receipt = store_psycopg.read_command_run_event(conn, key, 1, "SUCCEEDED")
            at = receipt["occurred_at"] if receipt else store_psycopg.d1_database_time(conn)
            if field == _AGE_FIELD:
                _verify_basis(conn, fields["basis_sha256"], basis_bytes, at)
            if receipt is None:
                _check_overlap(conn, instance, team, start, end, field)
                if at >= start:
                    raise store.PapersimStoreError("d1_close_window_registration_not_prospective")
                refs = operations._append_document(conn, operations._INTERVAL_KIND, _SOURCE, document, at)
                store_psycopg.record_command_run_event(conn, store.CommandRunEventSubmission(
                    operation_key=key, attempt=1, phase="SUCCEEDED", command=_COMMAND, scheduled_for=start,
                    occurred_at=at, code_commit=commit, config_digest=digest, output_evidence=refs))
                receipt = store_psycopg.read_command_run_event(conn, key, 1, "SUCCEEDED")
                if receipt is None:
                    raise store.PapersimStoreError("d1_close_window_registration_missing")
                if store_psycopg.d1_database_time(conn) >= start:
                    raise store.PapersimStoreError("d1_close_window_registration_not_prospective")
            return _verify_registration(conn, document, digest, key, receipt)
