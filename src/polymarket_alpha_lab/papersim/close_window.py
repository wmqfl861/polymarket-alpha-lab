"""Read the prospective OD-P classification from a sealed capture and its original receipts."""

from __future__ import annotations

from . import capture, pipeline, prepare, prepare_inputs, snapshots, store, store_psycopg
from .d1compare import acquire, journal, operations
from .. import replay


def _market_boundary(view, candidate, request):
    market = view.markets.get(candidate["condition_id"])
    field = "endDate" if request.market_kind in pipeline.CRYPTO_KINDS else "gameStartTime"
    if market is None or market["binding_status"] != "explicit_yes_no":
        raise capture.CaptureError("close_window_market_unverified")
    sides = {candidate["yes_token_id"]: "yes", candidate["no_token_id"]: "no"}
    saved = candidate["event_start_at"]
    citations = []
    for ref in market["observations"]:
        sequence, path = ref["sequence"], ref["path"]
        response = view.responses.get(sequence)
        if response is None or not response.eligible(view.start, view.cutoff):
            continue
        match = view.page(sequence).get(path)
        if match is None:
            raise capture.CaptureError("close_window_market_unverified")
        row, raw = match
        if (row["condition_id"] != candidate["condition_id"] or row["market_id"] != request.market_id
                or row["slug"] != candidate["market_slug"] or journal.gamma_outcomes_by_token(row) != sides
                or raw.get("question") != request.question):
            raise capture.CaptureError("close_window_market_unverified")
        try:
            original = prepare._utc(raw.get(field))
        except prepare.PreparationError:
            raise capture.CaptureError("close_window_boundary_missing") from None
        if saved is None or original != saved:
            raise capture.CaptureError("close_window_boundary_unverified")
        citations.append({"request_sequence": sequence, "json_pointer": path,
                          "snapshot_sha256": response.entry.content_sha256})
    if not citations:
        raise capture.CaptureError("close_window_boundary_missing")
    return {"field": field, "boundary_utc": saved.isoformat(), "sources": citations}


def _inspect_row(conn, view, mark, raw, team, allowed_ids, slot_sources, batch_digest):
    candidate = store_psycopg.d1_candidate_capture_target(
        conn, scan_id=mark.scan_id, scan_operation_key=mark.scan_operation_key, candidate_key=mark.candidate_key)
    if candidate is None:
        raise capture.CaptureError("papersim_capture_candidate_missing")
    token = capture._d1_candidate_token(candidate, mark, team, allowed_ids)
    source = {}
    if slot_sources is not None:
        preparation = candidate.get("scan_preparation")
        if preparation is not None and candidate["candidate_key"] in preparation["candidate_keys"] and (
                preparation["batch_sha256"] != batch_digest
                or preparation["policy_interval_sha256"] != slot_sources.interval_sha256):
            raise capture.CaptureError("d1_capture_book_age_policy_binding_invalid")
        prices, received = capture._d1_quote(conn, slot_sources, candidate, mark, token, source)
    else:
        prices, received = capture._verified_buy_quote(conn, view.manifest, view.cutoff, candidate, mark, token, source)
    for name, price in prices.items():
        claim = raw.get(name)
        if claim is not None:
            claim = capture._exact_price(capture._book_number(claim, name, allow_text=False), name)
        if price != claim:
            raise capture.CaptureError("d1_capture_quote_mismatch")
    if all(price is None for price in prices.values()):
        raise capture.CaptureError("d1_capture_no_quote")
    request = replay.request_from_replay_bundle(candidate["input_payload"])
    boundary = _market_boundary(view, candidate, request)
    kind, reason = capture._classify_odp_observation(
        mark.sampling_kind, mark.observed_at, candidate["event_start_at"], received_at=received)
    return {"sampling_kind": kind, "reason": reason, "candidate_id": candidate["candidate_id"],
            "side": mark.side, "market_id": candidate["market_id"], "token_id": token,
            "boundary": boundary, "source_observed_at": mark.observed_at.isoformat(),
            "received_at": received.isoformat(), "request_sequence": source["request_sequence"],
            "book_sha256": source["snapshot_sha256"], "book_byte_length": source["byte_length"]}


def inspect(config, packs, *, conn, repo_root, batch_digest, operation_key=None):
    """Return read-only window evidence for ordinary B2 or a registered D1 operation."""
    snapshots.snapshot_digest_argument(batch_digest)
    code_commit = capture.require_clean_worktree(repo_root)
    identity = store_psycopg.d1_managed_identity(conn, repo_root)
    pack_refs = [{"pack": p.name, "snapshot_sha256": snapshots.snapshot_content_sha256(p.content),
                  "byte_length": len(p.content)} for p in packs]
    with store_psycopg.d1_read_view(conn):
        slot_sources, allowed_ids = None, None
        if operations.is_d1_operation_key(operation_key):
            operations._validate_attempt(operation_key, 1)
            configuration = {"team": config.team, "operator": config.operator, "packs": list(config.packs)}
            context = {**identity, "operation_key": operation_key, "team": config.team,
                       "command": capture.CAPTURE_COMMAND, "code_commit": code_commit,
                       "config_digest": snapshots.snapshot_content_sha256(operations.document_bytes(configuration))}
            slot = operations._load_slot(conn, context, pack_refs)
            if slot.scope["capture_manifest_sha256"] != batch_digest:
                raise capture.CaptureError("d1_capture_manifest_binding_mismatch")
            slot_sources = capture._d1_book_sources(conn, slot)
            allowed_ids = set(slot.scope["candidate_ids"])
        manifest, entries = acquire.read_capture(repo_root=repo_root, batch_sha256=batch_digest, _connection=conn)
        view = prepare._capture_view(batch_digest, manifest, entries)
        scope = manifest["scope"]
        receipt = prepare_inputs._success_receipt(conn, acquire.discovery_operation_key(scope), acquire._COMMAND,
                                                 code="d1_capture_manifest_receipt_invalid")
        if (receipt["config_digest"] != acquire._digest(acquire._canonical(scope))
                or receipt["output_evidence"] != [batch_digest] or receipt["scheduled_for"] != view.start
                or receipt["occurred_at"] < view.cutoff):
            raise capture.CaptureError("d1_capture_manifest_receipt_invalid")
        prepare_inputs._verify_capture_requests(conn, view, prepare._utc(scope["window_end"]), scope["tag_slugs"])
        rows = []
        for pack, ref in zip(packs, pack_refs, strict=True):
            raw_marks = journal.decode_public_json(pack.content)
            if not isinstance(raw_marks, list):
                raise capture.CaptureError("papersim_capture_pack_invalid")
            for index, raw in enumerate(raw_marks):
                if not isinstance(raw, dict):
                    raise capture.CaptureError("papersim_capture_pack_invalid")
                mark = capture._observation_from_mapping({k: v for k, v in raw.items() if k not in capture._PRICE_FIELDS})
                try:
                    status = _inspect_row(conn, view, mark, raw, config.team, allowed_ids, slot_sources, batch_digest)
                except (capture.CaptureError, prepare.PreparationError, journal.JournalError, store.PapersimStoreError) as exc:
                    status = {"sampling_kind": "PERIODIC", "reason": exc.code}
                rows.append({"pack_sha256": ref["snapshot_sha256"], "observation_index": index,
                             "requested_kind": mark.sampling_kind, **status})
    return {"purpose": "read_only_close_window_evidence", "code_commit": code_commit,
            "capture_path": "registered_d1" if slot_sources is not None else "ordinary_b2",
            "capture_manifest_sha256": batch_digest, "capture_receipt_utc": receipt["occurred_at"].isoformat(),
            "rows": rows}
