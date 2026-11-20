"""Managed D1 frozen-input preflight, with its own nonqualifying operation."""

from collections import defaultdict
from datetime import UTC, datetime
import json
from pathlib import Path

from polymarket_alpha_lab.papersim import snapshots, store, store_psycopg
from polymarket_alpha_lab.papersim.adapters import polymarket
from polymarket_alpha_lab.papersim.d1compare import acquire, frozenrun, journal


_VERSION = 1
_COMMAND = "run-frozen-preflight"
_HANDLER = "d1compare.frozenrun.preflight_designated_chain"
_NAMESPACE = "d1:frozen-preflight:v1"
_REQUIREMENTS_UNPROVED = "frozen_preflight_requirements_unproved"
_FLAGS = {"mode": "preflight", "qualifying": False, "day_credit": 0, "child_started": False}
_CONTRACT_FIELDS = (
    "question", "description", "category", "umaResolutionStatus", "resolutionSource",
    "groupItemTitle", "marketType", "sportsMarketType", "line", "handicap",
    "startDate", "gameStartTime", "endDate", "closed", "active", "archived",
    "acceptingOrders", "enableOrderBook",
)
_STAGE_FIELDS = {
    "soccer": ["passed_numeric", "policy_eligible", "enterable", "stake_usd", "quote", "checks"],
    "crypto": ["pA_30d", "pB_30d", "best_edge_side", "best_edge", "yes_ask", "no_ask",
               "report_only_qualifying"],
}
_MISSING_SOURCES = {
    "soccer": ["frozen_gamma_pagination_limit_100", "espn-soccer-schedule",
               "understat-xg", "fotmob-xg", "soccer-kappa"],
    "crypto": ["frozen_gamma_events_limit_100", "binance-btc-eth", "stooq-wti",
               "frozen_crypto_auxiliary_pages"],
}
_EVENT_FIELDS = (
    "run_event_key, attempt, phase, command, queue_id, scheduled_for, occurred_at, "
    "config_digest, input_evidence, output_evidence, error_code, scan_id"
)
_ERROR = frozenrun.FrozenRunError


def _timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    moment = datetime.fromisoformat(value)
    if moment.utcoffset() is None:
        raise ValueError("preflight_naive_clock")
    return moment.astimezone(UTC)


def _digest(document: dict) -> str:
    return snapshots.snapshot_content_sha256(frozenrun._canonical_json(document))


def _capture_event(connection, document: dict, batch: str) -> dict:
    """Verify the public writer's event in this managed instance."""
    scope = document["scope"]
    scope_sha = snapshots.snapshot_content_sha256(acquire._canonical(scope))
    operation = f"{acquire._OPERATION_PREFIX}:{scope_sha}"
    key = store.command_run_event_key(operation, 1, "SUCCEEDED")
    row = connection.execute(
        f"SELECT operation_key, {_EVENT_FIELDS} FROM papersim.command_run_events WHERE run_event_key = %s",
        (key,),
    ).fetchone()
    if row is None:
        raise _ERROR("frozen_preflight_capture_event_missing")
    (recorded_operation, event, attempt, phase, command, queue, scheduled, occurred,
     config, inputs, outputs, error, scan) = row
    sealed = _timestamp(document["sealed_at"])
    version = document["capture_version"]
    if ((recorded_operation, event, attempt, phase, command) !=
            (operation, key, 1, "SUCCEEDED", "capture-inputs")
            or any(value is not None for value in (queue, error, scan))
            or config != scope_sha or inputs != [] or outputs != [batch]
            or scheduled != _timestamp(scope["window_start"])
            or sealed is None or occurred < sealed or (version == 1 and occurred != sealed)):
        raise _ERROR("frozen_preflight_capture_event_conflict")
    registered = frozenrun._snapshot_record(connection, batch, "public batch")
    if registered["content"] != acquire._canonical(document):
        raise _ERROR("frozen_preflight_capture_snapshot_conflict")
    return {
        "operation_key": operation, "run_event_key": key, "attempt": attempt,
        "command": command, "occurred_at": occurred.isoformat(), "scope_sha256": scope_sha,
    }


def _observations(document: dict, entries: tuple[journal.JournalEntry, ...]) -> dict[int, journal.JournalEntry]:
    """Recheck byte and clock references without collapsing repeated responses."""
    bodies = {}
    iterator = iter(entries)
    for sequence, row in enumerate(document["requests"], start=1):
        if row["sequence"] != sequence:
            raise _ERROR("frozen_preflight_capture_observation_mismatch")
        if row["content_sha256"] is None:
            continue
        entry = next(iterator, None)
        if (entry is None or entry.request_meta["sequence"] != sequence or entry.url != row["url"]
                or entry.utc_fetched_at != _timestamp(row["utc_fetched_at"])
                or len(entry.content) != row["byte_length"]
                or snapshots.snapshot_content_sha256(entry.content) != row["content_sha256"]):
            raise _ERROR("frozen_preflight_capture_observation_mismatch")
        bodies[sequence] = entry
    if next(iterator, None) is not None:
        raise _ERROR("frozen_preflight_capture_observation_mismatch")
    return bodies


def _gamma_observations(request: dict, body: journal.JournalEntry,
                        cutoff: datetime | None) -> tuple[dict, list[dict]]:
    indexed = journal.gamma_page_index(body.content)
    result = {"request_sequence": request["sequence"], "page_status": "unparseable", "events": []}
    if indexed is None:
        return result, []
    events = journal.decode_public_json(body.content)
    result["events"] = indexed["events"]
    valid = (all(event["market_count"] is not None for event in indexed["events"])
             and request["status"] == "received" and cutoff is not None
             and request["source_time_status"] == "receipt_only"
             and request["source_observed_at"] is None and body.utc_fetched_at <= cutoff)
    result["page_status"] = "indexed" if valid else "partly_indexed"
    rows = []
    tag = request["request_meta"]["query"]["tag_slug"]
    for indexed_market in indexed["markets"]:
        _, event_index, _, market_index = indexed_market["path"].split("/")
        event = events[int(event_index)]
        raw = event["markets"][int(market_index)]
        fields = {field: raw.get(field) for field in _CONTRACT_FIELDS} if isinstance(raw, dict) else {}
        raw_tags = event.get("tags")
        tags = [tag["slug"] for tag in (raw_tags if isinstance(raw_tags, list) else ())
                if isinstance(tag, dict) and isinstance(tag.get("slug"), str)]
        captured_only = bool({"nba", "basketball"}.intersection([*tags, tag]))
        rows.append({
            **indexed_market, "request_sequence": request["sequence"],
            "page_sha256": body.content_sha256, "contract": fields, "event_tags": tags,
            "outcomes_by_token": journal.gamma_outcomes_by_token(indexed_market),
            "scope_class": "captured_only" if captured_only else "unknown",
            "economic_direction": None,
            "expected_comparison_sides": None,
            "final_adoption": "unknown",
        })
    return result, rows


def _book_target(target: dict, requests: dict, bodies: dict, cutoff: datetime | None) -> dict:
    seq = target["request_sequence"]
    token = target["token_id"]
    result = {
        **target, "readback_status": "missing", "raw_condition_id": None,
        "raw_asset_id": None, "content_sha256": None,
        "source_observed_at": None, "utc_fetched_at": None,
        "source_age_seconds": None, "receipt_age_seconds": None,
        "freshness_policy_ref": None, "usable_for_decision": None,
    }
    if seq is None or seq not in requests:
        return result
    request = requests[seq]
    result.update({key: request[key] for key in (
        "source_observed_at", "utc_fetched_at", "content_sha256",
    )})
    if seq not in bodies:
        return result
    try:
        entry = bodies[seq]
        raw = journal.decode_public_json(entry.content)
        book = journal.book_from_entry(entry)
        adapter_book = polymarket._parse_order_book(token, raw)
    except (journal.JournalError, polymarket._MarketRejected, OverflowError, ValueError):
        result["readback_status"] = "invalid_book_payload"
        return result
    result.update(raw_condition_id=raw.get("market"), raw_asset_id=raw.get("asset_id"))
    if (request["source_kind"] != polymarket.SOURCE_KIND_CLOB or
            request["request_meta"]["query"].get("token_id") != token or
            raw.get("asset_id") != token or book.token_id != token or
            raw.get("market") not in (None, target["condition_id"])):
        result["readback_status"] = "book_identity_mismatch"
        return result
    if any(level.size <= 0 for side in (adapter_book.bids, adapter_book.asks) for level in side):
        result["readback_status"] = "invalid_book_levels"
        return result
    observed = _timestamp(request["source_observed_at"])
    received = entry.utc_fetched_at
    if cutoff is not None and observed is not None:
        result.update(source_age_seconds=(cutoff - observed).total_seconds(),
                      receipt_age_seconds=(cutoff - received).total_seconds())
    if observed is None or observed != adapter_book.book_observed_at:
        result["readback_status"] = "book_source_clock_unproved"
    elif cutoff is None or not observed <= received <= cutoff:
        result["readback_status"] = "book_outside_usable_cutoff"
    elif request["status"] != "received" or target["reason_code"] is not None:
        result["readback_status"] = "book_capture_rejected"
    elif raw.get("market") is None:
        result["readback_status"] = "book_condition_unproved"
    else:
        ladder = "two_sided" if book.bids and book.asks else (
            "one_side_empty" if book.bids or book.asks else "empty")
        result.update(readback_status="verified", bid_level_count=len(book.bids),
                      ask_level_count=len(book.asks), ladder_status=ladder)
    return result


def _decision_as_of(document: dict) -> datetime | None:
    start = _timestamp(document["scope"]["window_start"])
    end = _timestamp(document["scope"]["window_end"])
    sealed = _timestamp(document["sealed_at"])
    cutoff = _timestamp(document["decision_as_of"])
    if not start < end or (cutoff is not None and not start <= cutoff <= sealed < end):
        raise _ERROR("frozen_preflight_capture_window_conflict")
    return cutoff


def _denominator(binding: dict, stage: dict, capture_event: dict, document: dict,
                 entries: tuple[journal.JournalEntry, ...]) -> dict:
    bodies = _observations(document, entries)
    requests = {row["sequence"]: row for row in document["requests"]}
    pages, markets = [], []
    queries = defaultdict(list)
    cutoff = _decision_as_of(document)
    for sequence, request in requests.items():
        queries[request["url"]].append(sequence)
        if request["source_kind"] != polymarket.SOURCE_KIND_GAMMA:
            continue
        if sequence in bodies:
            page, observed = _gamma_observations(request, bodies[sequence], cutoff)
            pages.append(page)
            markets.extend(observed)
        else:
            pages.append({"request_sequence": sequence, "page_status": "missing", "events": []})
    books = [_book_target(target, requests, bodies, cutoff) for target in document["books"]]
    by_market = defaultdict(list)
    for target in books:
        by_market[target["condition_id"]].append(target)
    for market in markets:
        market["book_request_sequences"] = {
            book["token_id"]: book["request_sequence"]
            for book in by_market.get(market["condition_id"], ())
            if book["token_id"] in (market["token_ids"] or ())
        }
    version = document["capture_version"]
    tags = document["coverage"]["tags"]
    incomplete_tags = [row["tag_slug"] for row in tags if not row["coverage_complete"]]
    scopes = {row["tag_slug"] for row in tags}
    incomplete_tags.extend(sorted(set(document["scope"]["tag_slugs"]) - scopes))
    missing_pages = [page["request_sequence"] for page in pages if page["page_status"] != "indexed"]
    unresolved = sorted(url for url, sequences in queries.items() if len(sequences) > 1)
    # v1 lacked the v2 term/identity conflict pass and cannot prove a complete denominator.
    a1_complete = (version == 2 and cutoff is not None and not incomplete_tags and not missing_pages
                   and document["coverage"]["sources"][polymarket.SOURCE_KIND_GAMMA] == "complete")
    binding_status = {row["condition_id"]: row["binding_status"] for row in document["markets"]}
    identities = {identity for row in markets if (identity := acquire._condition_identity(row))}
    trusted_identities = {
        identity for identity in identities
        if binding_status.get(identity) not in {None, "identity_drift", "token_condition_conflict"}
    }
    coverage_lower_bound = len(trusted_identities) or int(
        any(row["identity_fields_valid"] or row["market_id"] for row in markets))
    coverage = {
        "request_count": len(requests), "response_count": len(bodies),
        "gamma_pages_seen": sum(page["page_status"] != "missing" for page in pages),
        "missing_or_unusable_gamma_sequences": missing_pages, "incomplete_gamma_tags": incomplete_tags,
        "market_observation_count": len(markets), "observed_distinct_condition_ids": len(identities),
        "observed_market_lower_bound": coverage_lower_bound,
        "unseen_a1_market_count": 0 if (a1_complete and all(
            row["identity_fields_valid"] and acquire._condition_identity(row) for row in markets)) else None,
        "unseen_frozen_market_count": None,
        "a1_empty_proven": a1_complete and not markets,
        "book_target_count": len(books),
        "book_readback_verified_count": sum(book["readback_status"] == "verified" for book in books),
        "duplicate_request_url_count": len(unresolved), "automatic_decisions": 0,
        "known_comparison_fields": 0, "expected_comparison_field_count": None,
        "final_adoption_known": 0, "final_adoption_unknown_lower_bound": coverage_lower_bound,
    }
    leg = binding["leg"]
    missing = ["frozen_execution_capability_unproved", "registered_comparison_scope_missing",
               *_MISSING_SOURCES[leg]]
    if cutoff is None:
        missing.append("decision_as_of_missing")
    if not a1_complete:
        missing.append("a1_gamma_coverage_incomplete")
    if version == 1:
        missing.append("capture_v1_binding_evidence_incomplete")
    if not coverage["a1_empty_proven"] or books:
        missing.append("registered_book_age_policy_missing")
    if coverage["book_readback_verified_count"] < len(books):
        missing.append("book_evidence_unresolved")
    if unresolved:
        missing.append("original_observation_selection_unregistered")
    return {
        "denominator_version": _VERSION, "binding": binding, "stage": stage,
        "scope": document["scope"], "decision_as_of": document["decision_as_of"],
        "capture_version": version, "capture_event": capture_event,
        "discovery_coverage": tags,
        "requests": [{key: value for key, value in row.items() if key != "page"} for row in requests.values()],
        "gamma_pages": pages, "market_observations": markets, "book_targets": books,
        "market_bindings": document["markets"], "unbound_market_appearances": document.get(
            "unbound_market_appearances", []),
        "unselected_request_versions": {url: queries[url] for url in unresolved},
        "automatic_stage_fields": _STAGE_FIELDS[leg], "coverage": coverage, "missing": missing,
    }


def _register(connection, document: dict, role: str, occurred: datetime) -> str:
    content = frozenrun._canonical_json(document)
    kind, source = f"d1_frozen_preflight_{role}", f"{_NAMESPACE}:{role}"
    record = snapshots.register_snapshot(connection, snapshots.SnapshotRegistration(
        content=content, kind=kind, source=source, fetched_at=occurred, schema_version=_VERSION))
    expected = snapshots.snapshot_content_sha256(content), len(content), kind, source, _VERSION
    fields = ("content_sha256", "byte_length", "kind", "source", "schema_version")
    if tuple(record[key] for key in fields) != expected:
        raise _ERROR("frozen_preflight_snapshot_conflict")
    return record["content_sha256"]


def _read_snapshot(connection, digest: str, role: str) -> dict:
    if not frozenrun._valid_sha256(digest):
        raise _ERROR("frozen_preflight_receipt_conflict")
    record = frozenrun._snapshot_record(connection, digest, role)
    if (record["kind"], record["source"], record["schema_version"]) != (
            f"d1_frozen_preflight_{role}", f"{_NAMESPACE}:{role}", _VERSION):
        raise _ERROR("frozen_preflight_receipt_conflict")
    body = record["content"]
    try:
        document = json.loads(body)
        if not isinstance(document, dict) or frozenrun._canonical_json(document) != body:
            raise ValueError
    except (ValueError, RecursionError):
        raise _ERROR("frozen_preflight_receipt_conflict") from None
    return document


def _event(connection, operation: str, binding: dict, attempt: int, phase: str,
           when: datetime, *, outputs: list[str], error: str | None = None) -> None:
    result = store_psycopg.record_command_run_event(connection, store.CommandRunEventSubmission(
        operation_key=operation, attempt=attempt, phase=phase, command=_COMMAND,
        occurred_at=when, config_digest=_digest(binding),
        input_evidence=[binding["manifest_sha256"], binding["batch_sha256"]],
        output_evidence=outputs, error_code=error,
    ))
    if not result["inserted"]:
        raise _ERROR("frozen_preflight_receipt_conflict")


def _existing(connection, operation: str, binding: dict):
    expected = _digest(binding), [binding["manifest_sha256"], binding["batch_sha256"]]
    rows = connection.execute(
        f"SELECT {_EVENT_FIELDS} FROM papersim.command_run_events "
        "WHERE operation_key = %s ORDER BY run_event_id",
        (operation,),
    ).fetchall()
    last_attempt = 0
    prepared = None
    terminal = None
    phase_seen = set()
    for (key, attempt, phase, command, queue, scheduled, occurred, config, inputs,
         outputs, error, scan) in rows:
        if ((terminal is not None and (
                    terminal[2] == "SUCCEEDED" or terminal[-1] == _REQUIREMENTS_UNPROVED))
                or command != _COMMAND or (config, inputs) != expected
                or any(value is not None for value in (queue, scheduled, scan))
                or key != store.command_run_event_key(operation, attempt, phase)
                or not isinstance(outputs, list) or not all(frozenrun._valid_sha256(sha) for sha in outputs)
                or (attempt, phase) in phase_seen):
            raise _ERROR("frozen_preflight_receipt_conflict")
        phase_seen.add((attempt, phase))
        if phase == "STARTED" and attempt == last_attempt + 1 and not outputs and error is None:
            last_attempt = attempt
            terminal = None
        elif (phase == "PREPARED" and terminal is None and attempt == last_attempt and len(outputs) == 1
              and error is None):
            if prepared is not None and prepared != outputs[0]:
                raise _ERROR("frozen_preflight_receipt_conflict")
            prepared = outputs[0]
        elif phase in {"FAILED", "SUCCEEDED"} and terminal is None and attempt == last_attempt and outputs:
            terminal = (key, attempt, phase, occurred, outputs, error)
        else:
            raise _ERROR("frozen_preflight_receipt_conflict")
    if prepared is not None:
        denominator = _read_snapshot(connection, prepared, "denominator")
        if denominator.get("binding") != binding or denominator.get("denominator_version") != _VERSION:
            raise _ERROR("frozen_preflight_receipt_conflict")
    if terminal is not None:
        key, attempt, phase, occurred, outputs, error = terminal
        receipt = _read_snapshot(connection, outputs[0], "receipt")
        required = {
            **_FLAGS, "receipt_version": _VERSION, "binding": binding, "operation_key": operation,
            "terminal_event_key": key, "attempt": attempt, "denominator_sha256": prepared,
            "occurred_at": occurred.isoformat(), "error_code": error,
        }
        if (any(receipt.get(name) != value for name, value in required.items())
                or (phase == "SUCCEEDED") != (receipt.get("status") == "ready")
                or receipt.get("status") not in {"ready", "incomplete", "failed"}
                or outputs != ([outputs[0], prepared] if prepared is not None else [outputs[0]])):
            raise _ERROR("frozen_preflight_receipt_conflict")
        if receipt["status"] == "failed":
            if receipt.get("denominator_revalidated") is not False or receipt.get("missing") != [error]:
                raise _ERROR("frozen_preflight_receipt_conflict")
            return last_attempt, prepared, None
        if (prepared is None or receipt.get("denominator_revalidated") is not True
                or any(receipt.get(field) != denominator.get(field) for field in ("coverage", "missing"))):
            raise _ERROR("frozen_preflight_receipt_conflict")
        return last_attempt, prepared, _summary(receipt, outputs[0], reused=True)
    return last_attempt, prepared, None


def _summary(receipt: dict, digest: str, *, reused: bool) -> dict:
    return {
        **receipt["binding"], **_FLAGS, "receipt_sha256": digest,
        **{key: receipt[key] for key in (
            "operation_key", "attempt", "terminal_event_key", "occurred_at",
            "status", "denominator_sha256", "denominator_revalidated",
            "coverage", "missing", "readback", "error_code",
        )},
        "reused": reused,
    }


def preflight(leg: str, *, mirror_root: Path, staging_root: Path, repo_root: Path,
              manifest_sha256: str, batch_sha256: str) -> dict:
    """Publish engineering evidence using only the verified project instance."""
    frozenrun.designated_leg(leg)
    if not all(frozenrun._valid_sha256(sha) for sha in (manifest_sha256, batch_sha256)):
        raise _ERROR("frozen_preflight_input_digest_invalid")
    code = {module.__name__: snapshots.snapshot_content_sha256(Path(module.__file__).read_bytes())
            for module in (frozenrun, acquire, journal)}
    code[__name__] = snapshots.snapshot_content_sha256(Path(__file__).read_bytes())
    with frozenrun._managed_archive(repo_root, "preflight") as connection:
        with connection.transaction():
            instance, actor = connection.execute(
                "SELECT instance_id, current_user FROM project_private.instance WHERE singleton IS TRUE"
            ).fetchone()
            binding = {
                "preflight_version": _VERSION, "instance_id": instance, "actor": actor,
                "handler": _HANDLER, "leg": leg, "manifest_sha256": manifest_sha256,
                "batch_sha256": batch_sha256, "code_sha256": _digest(code),
                "capability_evidence_sha256": None, "capability_status": "unproved",
            }
            operation = f"{_NAMESPACE}:{_digest(binding)}"
            # Session lock spans the independent evidence read and the two
            # commits. Closing or losing this managed connection releases it.
            connection.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (operation,))
            attempt, prepared, terminal = _existing(connection, operation, binding)
            if terminal is not None:
                return terminal
            attempt += 1
            _event(connection, operation, binding, attempt, "STARTED", datetime.now(UTC), outputs=[])

        verified = {"archive": "unverified", "capture": "unverified"}
        denominator = None
        reason = None
        try:
            with frozenrun.plan_chain(
                leg, mirror_root=mirror_root, staging_root=staging_root, repo_root=repo_root,
                manifest_sha256=manifest_sha256,
            ) as staged:
                verified["archive"] = "verified"
                document, entries = acquire.read_capture(repo_root=repo_root, batch_sha256=batch_sha256)
                with connection.transaction():
                    capture_event = _capture_event(connection, document, batch_sha256)
                denominator = _denominator(binding, staged.as_mapping(), capture_event, document, entries)
                verified["capture"] = "verified"
        except (_ERROR, acquire.AcquisitionError) as error:
            reason = error.code
        except OSError:
            reason = "frozen_preflight_local_input_unreadable"
        except (KeyError, TypeError, ValueError):
            reason = "frozen_preflight_capture_structure_invalid"
        if denominator is not None:
            with connection.transaction():
                now = datetime.now(UTC)
                denom_sha = _register(connection, denominator, "denominator", now)
                if prepared is not None and denom_sha != prepared:
                    raise _ERROR("frozen_preflight_receipt_conflict")
                prepared = denom_sha
                _event(connection, operation, binding, attempt, "PREPARED", now, outputs=[prepared])

        # A failed input check is a durable attempt. It can be tried again
        # with the same bytes after an unavailable input or PG read recovers.
        status = "failed" if denominator is None else "incomplete"
        reason = reason if denominator is None else _REQUIREMENTS_UNPROVED
        now = datetime.now(UTC)
        receipt = {
            **_FLAGS, "receipt_version": _VERSION, "binding": binding,
            "operation_key": operation, "attempt": attempt, "occurred_at": now.isoformat(),
            "terminal_event_key": store.command_run_event_key(operation, attempt, "FAILED"),
            "status": status, "error_code": reason, "denominator_sha256": prepared,
            "denominator_revalidated": denominator is not None,
            "coverage": denominator["coverage"] if denominator else {},
            "missing": denominator["missing"] if denominator else [reason],
            "readback": verified,
        }
        with connection.transaction():
            sha = _register(connection, receipt, "receipt", now)
            outputs = [sha, prepared] if prepared is not None else [sha]
            _event(connection, operation, binding, attempt, "FAILED", now, outputs=outputs, error=reason)
        return _summary(receipt, sha, reused=False)
