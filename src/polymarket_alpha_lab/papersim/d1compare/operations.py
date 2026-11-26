"""Bound D1 operation planning, item recovery and public resolution scope."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
import re

from polymarket_alpha_lab.papersim import settle, snapshots as evidence, store, store_psycopg as pg

_VERSION = 1
_OPERATION_PREFIX = "d1:operation:"
_KEY_PREFIX = _OPERATION_PREFIX + "v1:"
_ITEM_PREFIX = "d1:operation-item:v1:"
_ITEM_COMMAND = "d1-capture-item"
_SETTLEMENT_ITEM_COMMAND = "d1-settlement-item"
_CAPTURE_COMMAND = "capture-marks"
_SETTLE_COMMAND = "settle"
_RESOLUTION_COMMAND = "capture-inputs-resolution"
_TERMINAL = {"WRITTEN", "REUSED", "MISSING_QUOTE"}
_SETTLEMENT_TERMINAL = {"SETTLED", "REUSED", "PENDING"}
_EMPTY_CAPTURE_ROWS = {"marks": [], "close_evidence": []}
_EMPTY_SETTLEMENT_ROWS = {"settlements": [], "cash": [], "reversals": []}
_SLOT_KIND = "d1_operation_slot"
_INTERVAL_KIND = "d1_operation_interval"
_PREPARED_KIND = "d1_operation_prepared"
_RECOVERY_KIND = "d1_operation_recovery"
_ITEM_KIND = "d1_capture_item_result"
_SETTLEMENT_ITEM_KIND = "d1_settlement_item_result"
_FINISHED_KIND = "d1_operation_finished"
_FAILURE_KIND = "d1_operation_failure"


def _fail(code: str):
    raise store.PapersimStoreError(code) from None


def _utc(value) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            _fail("d1_operation_time_invalid")
    if not isinstance(value, datetime) or value.utcoffset() is None:
        _fail("d1_operation_time_invalid")
    return value.astimezone(UTC)


def canonical_document(value):
    """Encode an evidence document without shortening timestamps or prices."""
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            _fail("d1_operation_document_invalid")
        return str(value)
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            _fail("d1_operation_document_invalid")
        return {key: canonical_document(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [canonical_document(item) for item in value]
    return value


def document_bytes(value) -> bytes:
    """Canonical versioned JSON used for identity and stored byte comparisons."""
    try:
        return json.dumps(canonical_document(value), ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError):
        _fail("d1_operation_document_invalid")


def _digest(value) -> str:
    return evidence.snapshot_content_sha256(document_bytes(value))


def is_d1_operation_key(key: str | None) -> bool:
    return isinstance(key, str) and key.startswith(_OPERATION_PREFIX)


def slot_operation_key(document: Mapping) -> str:
    """Name one actual command and leg at the registered interval instant."""
    identity = {name: document[name] for name in
                ("instance_id", "interval_sha256", "scheduled_for", "leg", "command")}
    identity["scheduled_for"] = _utc(identity["scheduled_for"])
    return _KEY_PREFIX + _digest(identity)


@dataclass(frozen=True)
class _Slot:
    scope: dict
    scheduled_for: datetime
    deadline: datetime
    write_deadline: datetime
    registered_at: datetime
    reference: tuple[str, ...]
    interval_sha256: str
    leg: str


def _refs(sha256: str, byte_length: int) -> list[str]:
    return [f"snapshot_sha256:{sha256}", f"snapshot_byte_length:{byte_length}"]


def _append_document(conn, kind: str, source: str, document: dict, at: datetime) -> list[str]:
    registration = evidence.SnapshotRegistration(document_bytes(document), kind, source, at, _VERSION)
    row = evidence.register_snapshot(conn, registration)
    if row["kind"] != kind or row["source"] != source or row["schema_version"] != _VERSION:
        _fail("d1_operation_snapshot_binding_conflict")
    return _refs(row["content_sha256"], row["byte_length"])


def _read_document(conn, refs, *, kind: str, source: str) -> dict:
    if (type(refs) is not list or len(refs) != 2
            or any(type(ref) is not str for ref in refs)
            or not refs[0].startswith("snapshot_sha256:")):
        _fail("d1_operation_snapshot_reference_invalid")
    digest = evidence.snapshot_digest_argument(refs[0].removeprefix("snapshot_sha256:"))
    row = evidence.read_snapshot(conn, digest)
    if row is None:
        _fail("d1_operation_snapshot_missing")
    raw = row["content"]
    if (refs != _refs(digest, len(raw)) or row["byte_length"] != len(raw)
            or evidence.snapshot_content_sha256(raw) != digest
            or (row["kind"], row["source"], row["schema_version"]) != (kind, source, _VERSION)):
        _fail("d1_operation_snapshot_binding_conflict")
    try:
        document = json.loads(raw)
    except (UnicodeError, ValueError):
        _fail("d1_operation_document_invalid")
    if (type(document) is not dict or type(document.get("version")) is not int
            or document["version"] != _VERSION or document_bytes(document) != raw):
        _fail("d1_operation_document_invalid")
    return document


def _registration(conn, key: str, command: str, kind: str, source: str) -> tuple[dict, dict]:
    receipt = pg.read_command_run_event(conn, key, 1, "SUCCEEDED")
    if receipt is None or receipt["command"] != command or receipt["error_code"] is not None:
        _fail("d1_operation_binding_missing")
    return receipt, _read_document(conn, receipt["output_evidence"], kind=kind, source=source)


def _slot_registration(conn, key: str) -> tuple[dict, dict]:
    return _registration(conn, "d1:slot-binding:v1:" + key[len(_KEY_PREFIX):],
                         "d1-register-slot", _SLOT_KIND, "d1:operation:slot:v1")


def _scope_ids(value) -> list[int]:
    if (type(value) is not list or any(type(identifier) is not int or not 0 < identifier < 2**63
                                     for identifier in value)
            or len(value) != len(set(value))):
        _fail("d1_operation_scope_invalid")
    return value


def _load_slot(conn, context: dict, input_evidence: list, *, registration=None) -> _Slot:
    operation_key = context["operation_key"]
    receipt, slot = registration if registration is not None else _slot_registration(conn, operation_key)
    fields = ("instance_id", "command", "team", "code_commit", "config_digest")
    if (any(slot.get(field) != context[field] for field in fields)
            or slot.get("input_evidence") != input_evidence
            or type(slot.get("scope")) is not dict
            or type(slot.get("leg")) is not str or not slot["leg"]):
        _fail("d1_operation_binding_conflict")
    scope = slot["scope"]
    if context["command"] == _CAPTURE_COMMAND:
        _scope_ids(scope.get("candidate_ids"))
        evidence.snapshot_digest_argument(scope.get("capture_manifest_sha256"))
    elif context["command"] == _SETTLE_COMMAND:
        _scope_ids(scope.get("entry_ids"))
    digest = evidence.snapshot_digest_argument(slot.get("interval_sha256"))
    registered, interval = _registration(conn, "d1:interval-binding:v1:" + digest,
                                         "d1-register-interval", _INTERVAL_KIND,
                                         "d1:operation:interval:v1")
    starts_at, ends_at = _utc(interval.get("starts_at")), _utc(interval.get("ends_at"))
    scheduled_for = _utc(slot.get("scheduled_for"))
    deadline = _utc(slot.get("deadline"))
    write_deadline = _utc(slot.get("write_deadline"))
    if (registered["output_evidence"][0] != _refs(digest, 0)[0]
            or interval.get("instance_id") != context["instance_id"]
            or ("basis_sha256" in interval and "book_age_policy" in interval and interval.get("team") != context["team"])
            or slot_operation_key(slot) != operation_key
            or (receipt["scheduled_for"], receipt["code_commit"], receipt["config_digest"])
            != (scheduled_for, slot["code_commit"], slot["config_digest"])
            or not registered["occurred_at"] < starts_at <= scheduled_for < deadline <= write_deadline <= ends_at
            or not registered["occurred_at"] <= receipt["occurred_at"] <= scheduled_for):
        _fail("d1_operation_binding_conflict")
    return _Slot(scope, scheduled_for, deadline, write_deadline, receipt["occurred_at"],
                 tuple(receipt["output_evidence"]), digest, slot["leg"])


def _validate_attempt(operation_key, attempt) -> None:
    if (type(operation_key) is not str
            or not re.fullmatch(re.escape(_KEY_PREFIX) + r"[0-9a-f]{64}", operation_key)):
        _fail("d1_operation_key_invalid")
    if type(attempt) is not int or not 0 < attempt <= 2147483647:
        _fail("d1_operation_attempt_invalid")


def _resolution_targets(conn, scope: dict, team: str) -> list[dict]:
    targets = scope.get("targets")
    if type(targets) is not list:
        _fail("d1_resolution_scope_invalid")
    fields = {"market_id", "condition_id", "slug", "side_tokens", "side_labels", "entry_ids", "terminal_rule"}
    markets, conditions, tokens, entry_ids = set(), set(), set(), set()
    for target in targets:
        if type(target) is not dict or target.keys() != fields:
            _fail("d1_resolution_scope_invalid")
        market, condition, slug = (target[name] for name in ("market_id", "condition_id", "slug"))
        if (any(type(value) is not str or not value.strip() for value in (market, condition))
                or (slug is not None and (type(slug) is not str or not slug.strip()))
                or market in markets or condition in conditions):
            _fail("d1_resolution_scope_invalid")
        markets.add(market)
        conditions.add(condition)
        for name in ("side_tokens", "side_labels"):
            pair = target[name]
            if (type(pair) is not dict or pair.keys() != {"YES", "NO"}
                    or any(type(value) is not str or not value.strip() for value in pair.values())
                    or pair["YES"] == pair["NO"]):
                _fail("d1_resolution_scope_invalid")
        market_tokens = set(target["side_tokens"].values())
        if tokens.intersection(market_tokens):
            _fail("d1_resolution_scope_invalid")
        tokens.update(market_tokens)
        rule = target["terminal_rule"]
        if (rule not in ("gamma_clob_binary", "additional_evidence_required")
                or (rule == "gamma_clob_binary"
                    and {key: label.casefold() for key, label in target["side_labels"].items()}
                    != {"YES": "yes", "NO": "no"})):
            _fail("d1_resolution_scope_invalid")
        nominal_entries = _scope_ids(target["entry_ids"])
        if entry_ids.intersection(nominal_entries):
            _fail("d1_resolution_scope_invalid")
        entry_ids.update(nominal_entries)
    entries = pg.d1_resolution_entries(conn, team=team, entry_ids=sorted(entry_ids))
    if entries.keys() != entry_ids:
        _fail("d1_resolution_entry_scope_conflict")
    for target in targets:
        for entry_id in target["entry_ids"]:
            entry = entries[entry_id]
            if (entry["condition_id"] != target["condition_id"]
                    or entry["market_id"] not in (None, target["market_id"])
                    or (target["slug"] is not None and entry["market_slug"] not in (None, target["slug"]))
                    or entry["yes_token_id"] != target["side_tokens"]["YES"]
                    or entry["no_token_id"] != target["side_tokens"]["NO"]):
                _fail("d1_resolution_entry_scope_conflict")
    return targets


def read_resolution_capture_binding(conn, *, instance_id, slot_sha256, operation_key, code_commit,
                                    window_start, window_end) -> dict:
    """Verify the registered source scope in the caller's managed read view."""
    _validate_attempt(operation_key, 1)
    evidence.snapshot_digest_argument(slot_sha256)
    role = pg.d1_resolution_identity(conn, instance_id=instance_id)
    read_started = pg.d1_database_time(conn)
    receipt, document = _slot_registration(conn, operation_key)
    scope, team = document.get("scope"), document.get("team")
    if type(scope) is not dict or type(team) is not str or not team.strip():
        _fail("d1_resolution_scope_invalid")
    config_digest = _digest({"team": team, "scope": scope})
    if receipt["output_evidence"][0] != f"snapshot_sha256:{slot_sha256}":
        _fail("d1_resolution_slot_snapshot_conflict")
    context = {"instance_id": instance_id, "operation_key": operation_key, "command": _RESOLUTION_COMMAND,
               "team": team, "code_commit": code_commit, "config_digest": config_digest}
    slot = _load_slot(conn, context, [], registration=(receipt, document))
    start, end = _utc(window_start), _utc(window_end)
    if ((_utc(scope.get("window_start")), _utc(scope.get("window_end"))) != (start, end)
            or not start <= slot.scheduled_for < slot.deadline <= end):
        _fail("d1_resolution_window_conflict")
    targets = _resolution_targets(conn, scope, team)
    read_finished = pg.d1_database_time(conn)
    if read_finished < read_started:
        _fail("d1_operation_clock_conflict")
    return {**context, "application_role": role, "slot_sha256": slot_sha256,
            "interval_sha256": slot.interval_sha256, "leg": slot.leg, "scheduled_for": slot.scheduled_for,
            "deadline": slot.deadline, "write_deadline": slot.write_deadline, "registered_at": slot.registered_at,
            "window_start": start, "window_end": end, "targets": targets,
            "scope_read_started_at": read_started, "scope_read_finished_at": read_finished}


@contextmanager
def open_resolution_capture(conn, *, repo_root, slot_sha256, operation_key, attempt, window_start, window_end):
    """Hold one resolution capture's parent lock across collection and publication."""
    from polymarket_alpha_lab.papersim.scan import require_clean_worktree

    _validate_attempt(operation_key, attempt)
    code_commit = require_clean_worktree(repo_root)
    identity = pg.d1_managed_identity(conn, repo_root)
    with pg.d1_operation_lock(conn, operation_key):
        with pg.d1_read_view(conn):
            registered = read_resolution_capture_binding(
                conn, instance_id=identity["instance_id"], slot_sha256=slot_sha256,
                operation_key=operation_key, code_commit=code_commit,
                window_start=window_start, window_end=window_end)
        yield registered


def _receipt_ref(row: dict) -> dict:
    return canonical_document({name: row[name] for name in
                               ("run_event_id", "run_event_key", "attempt", "phase", "occurred_at")})


def _error_code(error: Exception, fallback: str) -> str:
    code = getattr(error, "code", None)
    if type(code) is str and re.fullmatch(r"[A-Za-z0-9_]{1,120}", code):
        return code
    state = getattr(error, "sqlstate", None)
    if type(state) is str and re.fullmatch(r"[A-Z0-9]{5}", state):
        return f"d1_operation_postgres_{state}"
    return fallback


def _receipt(conn, context: dict, *, key: str, command: str, phase: str, at: datetime,
             scheduled_for: datetime | None, inputs=(), outputs=(), error_code=None) -> dict:
    attempt = context["attempt"]
    pg.record_command_run_event(conn, store.CommandRunEventSubmission(
        operation_key=key, attempt=attempt, phase=phase, command=command,
        scheduled_for=scheduled_for, occurred_at=at, code_commit=context["code_commit"],
        config_digest=context["config_digest"], input_evidence=list(inputs),
        output_evidence=list(outputs), error_code=error_code))
    stored = pg.read_command_run_event(conn, key, attempt, phase)
    if stored is None:
        _fail("d1_operation_receipt_missing")
    return stored


def _binding_failure(conn, context: dict, input_evidence: list, *, reason: str) -> None:
    """Keep first refusals and preserve changed submissions under a conflict key."""
    operation_key, attempt = context["operation_key"], context["attempt"]
    with conn.transaction():
        history = pg.d1_operation_receipts(conn, operation_key)
        existing = next((row for row in history if row["attempt"] == attempt
                         and row["phase"] == "FAILED"), None)
        if existing is None and not any(row["attempt"] == attempt for row in history):
            key, command = operation_key, context["command"]
        else:
            changed = {**context, "input_evidence": input_evidence, "reason": reason}
            key = "d1:operation-conflict:v1:" + _digest(changed)
            command = "d1-operation-conflict"
        # A repeated refusal does not replace the clock of the first one.
        if pg.read_command_run_event(conn, key, attempt, "FAILED") is not None:
            return
        at = pg.d1_database_time(conn)
        document = {**context, "version": _VERSION, "recorded_at": at, "reason": reason,
                    "input_evidence": input_evidence, "original": [_receipt_ref(row) for row in history
                                                                  if row["attempt"] == attempt]}
        refs = _append_document(conn, _FAILURE_KIND, key, document, at)
        _receipt(conn, context, key=key, command=command, phase="FAILED", at=at,
                 scheduled_for=None, outputs=refs, error_code=reason)


@contextmanager
def open_operation(conn, *, repo_root, operation_key: str, attempt: int, command: str,
                   team: str, code_commit: str, config_digest: str, input_evidence: list):
    """Hold the bound operation's session lock for one actual CLI attempt."""
    _validate_attempt(operation_key, attempt)
    if command not in (_CAPTURE_COMMAND, _SETTLE_COMMAND) or type(team) is not str or not team.strip():
        _fail("d1_operation_command_invalid")
    if type(code_commit) is not str or not re.fullmatch(r"[0-9a-f]{40}", code_commit):
        _fail("d1_operation_commit_invalid")
    evidence.snapshot_digest_argument(config_digest)
    if type(input_evidence) is not list:
        _fail("d1_operation_input_invalid")
    if command == _SETTLE_COMMAND and input_evidence:
        _fail("d1_operation_input_invalid")
    inputs = canonical_document(input_evidence)
    document_bytes(inputs)
    identity = pg.d1_managed_identity(conn, repo_root)
    context = {**identity, "operation_key": operation_key, "attempt": attempt,
               "command": command, "team": team, "code_commit": code_commit,
               "config_digest": config_digest, "input_sha256": _digest(inputs)}
    with pg.d1_operation_lock(conn, operation_key):
        try:
            slot = _load_slot(conn, context, inputs)
        except store.PapersimStoreError as error:
            _binding_failure(conn, context, inputs, reason=error.code)
            raise
        operation = _Operation(conn, context, slot, inputs)
        try:
            operation.begin()
            yield operation
        except Exception as error:
            if not conn.closed:
                operation.fail(_error_code(error, "d1_operation_failed"))
            raise


class _Operation:
    def __init__(self, conn, context: dict, slot: _Slot, input_evidence: list):
        self.conn = conn
        self.context = {**context, "scheduled_for": slot.scheduled_for, "deadline": slot.deadline,
                        "write_deadline": slot.write_deadline, "interval_sha256": slot.interval_sha256,
                        "slot_reference": slot.reference, "leg": slot.leg}
        self.key = context["operation_key"]
        self.attempt = context["attempt"]
        self.slot = slot
        self.is_capture = context["command"] == _CAPTURE_COMMAND
        self.item_command = _ITEM_COMMAND if self.is_capture else _SETTLEMENT_ITEM_COMMAND
        self.item_kind = _ITEM_KIND if self.is_capture else _SETTLEMENT_ITEM_KIND
        self.terminal = _TERMINAL if self.is_capture else _SETTLEMENT_TERMINAL
        self.input_evidence = input_evidence
        self.inputs = (*slot.reference, f"input_sha256:{context['input_sha256']}")
        self.started = None
        self.prepared = None
        self.attempt_read = None
        self.plan = None
        self.history = []
        self.targets = {}
        self.results = {}
        self.completed_summary = None

    def _parent_receipt(self, phase: str, at: datetime, *, outputs=(), inputs=(), reason=None) -> dict:
        return _receipt(self.conn, self.context, key=self.key, command=self.context["command"],
                        phase=phase, at=at, scheduled_for=self.slot.scheduled_for,
                        inputs=(*self.inputs, *inputs), outputs=outputs, error_code=reason)

    def _document(self, at: datetime, **values) -> dict:
        return {**self.context, "version": _VERSION, "recorded_at": at, **values}

    def _read_attempt_document(self, row: dict, kind: str, *, source: str) -> dict:
        document = _read_document(self.conn, row["output_evidence"], kind=kind, source=source)
        expected = canonical_document(self._document(row["occurred_at"], attempt=row["attempt"]))
        if (row["scheduled_for"] != self.slot.scheduled_for
                or row["code_commit"] != self.context["code_commit"]
                or row["config_digest"] != self.context["config_digest"]
                or any(document.get(name) != value for name, value in expected.items())):
            _fail("d1_operation_witness_binding_conflict")
        return document

    def _verify_started(self, row: dict) -> None:
        if (row["phase"] != "STARTED" or row["command"] != self.context["command"]
                or row["scheduled_for"] != self.slot.scheduled_for
                or row["code_commit"] != self.context["code_commit"]
                or row["config_digest"] != self.context["config_digest"]
                or row["input_evidence"] != list(self.inputs)
                or row["error_code"] is not None
                or not self.slot.registered_at <= row["occurred_at"]):
            _fail("d1_operation_started_binding_conflict")

    def begin(self) -> None:
        self.history = pg.d1_operation_receipts(self.conn, self.key)
        if any(row["run_event_key"] != store.command_run_event_key(self.key, row["attempt"], row["phase"])
               for row in self.history):
            _fail("d1_operation_receipt_binding_conflict")
        preparations = [row for row in self.history if row["phase"] == "PREPARED"]
        if len(preparations) > 1:
            _fail("d1_operation_prepared_conflict")
        if preparations:
            self.prepared = preparations[0]
        same = {row["phase"]: row for row in self.history if row["attempt"] == self.attempt}
        if same:
            if "STARTED" in same:
                self._verify_started(same["STARTED"])
            if "FAILED" in same:
                _fail(same["FAILED"]["error_code"] or "d1_operation_attempt_failed")
            if "STARTED" in same and "SUCCEEDED" in same:
                self.completed_summary = self._completed(same["STARTED"], same["SUCCEEDED"])
                return
            _fail("d1_operation_new_attempt_required")
        if self.history and self.attempt <= max(row["attempt"] for row in self.history):
            _fail("d1_operation_attempt_out_of_order")
        with self.conn.transaction():
            at = pg.d1_database_time(self.conn)
            if at < self.slot.registered_at:
                _fail("d1_operation_clock_conflict")
            self.started = self._parent_receipt("STARTED", at)

    def _read_prepared(self, registrations: tuple | None = None) -> dict:
        row = self.prepared
        document = self._read_attempt_document(row, _PREPARED_KIND, source=self.key)
        original_start = pg.read_command_run_event(self.conn, self.key, row["attempt"], "STARTED")
        if original_start is None:
            _fail("d1_operation_started_binding_conflict")
        self._verify_started(original_start)
        if (row["command"] != self.context["command"] or row["input_evidence"] != list(self.inputs)
                or row["error_code"] is not None
                or document.get("started") != _receipt_ref(original_start)
                or document.get("input_evidence") != self.input_evidence
                or not max(self.slot.scheduled_for, original_start["occurred_at"])
                <= _utc(document.get("read_started_at")) <= _utc(document.get("read_finished_at"))
                <= row["occurred_at"] < self.slot.deadline):
            _fail("d1_operation_prepared_conflict")
        plan = document.get("plan")
        self._check_evidence_clock(plan, _utc(document["read_started_at"]), _utc(document["read_finished_at"]))
        if document.get("packs") != self.input_evidence:
            _fail("d1_operation_prepared_pack_conflict")
        for index, pack in enumerate(self.input_evidence):
            digest = pack["snapshot_sha256"]
            stored = evidence.read_snapshot(self.conn, digest)
            if (stored is None or stored["byte_length"] != pack["byte_length"]
                    or len(stored["content"]) != pack["byte_length"]
                    or evidence.snapshot_content_sha256(stored["content"]) != digest
                    or (registrations is not None and
                        stored["content"] != bytes(registrations[index].content))):
                _fail("d1_operation_prepared_pack_conflict")
        return plan

    def prepare(self, build, *, snapshots=()) -> dict:
        """Publish the original read set before business, or recover that exact set."""
        if self.started is None or self.plan is not None:
            _fail("d1_operation_prepare_state_invalid")
        registrations = tuple(snapshots)
        pack_refs = _pack_refs(registrations)
        if pack_refs != self.input_evidence:
            _fail("d1_operation_prepared_pack_conflict")
        first_item_failure = None
        with pg.d1_read_view(self.conn):
            read_started = pg.d1_database_time(self.conn)
            if self.prepared is None:
                self._check_prepare_window(read_started)
                plan = canonical_document(build())
            else:
                plan = self._read_prepared(registrations)
            self.targets = self._target_index(plan)
            if self.prepared is not None and self.targets:
                first_item_failure = pg.d1_first_failed_item(
                    self.conn, self.targets, command=self.item_command, before_attempt=self.attempt)
            read_finished = pg.d1_database_time(self.conn)
            if read_finished < read_started:
                _fail("d1_operation_clock_conflict")
            if self.prepared is None:
                self._check_evidence_clock(plan, read_started, read_finished)
        prepared = self.prepared
        with self.conn.transaction():
            at = pg.d1_database_time(self.conn)
            if at < read_finished:
                _fail("d1_operation_clock_conflict")
            common = self._document(at, started=_receipt_ref(self.started),
                                    read_started_at=read_started, read_finished_at=read_finished)
            if self.prepared is None:
                self._check_prepare_window(at)
                for registration in registrations:
                    evidence.register_snapshot(self.conn, registration)
                document = {**common, "input_evidence": self.input_evidence, "packs": pack_refs,
                            "plan": plan}
                refs = _append_document(self.conn, _PREPARED_KIND, self.key, document, at)
                prepared = self._parent_receipt("PREPARED", at, outputs=refs)
                attempt_read = prepared
                self._check_prepare_window(pg.d1_database_time(self.conn))
            else:
                first_failure = next((row for row in self.history if row["phase"] == "FAILED"), None)
                document = {**common, "prepared": _receipt_ref(self.prepared),
                            "prepared_reference": self.prepared["output_evidence"],
                            "late": at >= self.slot.write_deadline,
                            "first_failure": _receipt_ref(first_failure) if first_failure else None,
                            "first_item_failure": _receipt_ref(first_item_failure) if first_item_failure else None}
                refs = _append_document(self.conn, _RECOVERY_KIND, self.key, document, at)
                attempt_read = self._parent_receipt("RECOVERY_READ", at, outputs=refs)
        self.prepared = prepared
        self.attempt_read = attempt_read
        self.plan = plan
        return canonical_document(plan)

    def _check_prepare_window(self, at: datetime) -> None:
        if at < self.slot.scheduled_for:
            _fail("d1_operation_not_scheduled")
        if at >= self.slot.deadline:
            _fail("d1_operation_preparation_expired")

    def _check_evidence_clock(self, plan: dict, read_started: datetime, read_finished: datetime) -> None:
        if not self.is_capture and (type(plan) is not dict
                                    or not read_started <= _utc(plan.get("evidence_as_of")) <= read_finished):
            _fail("d1_settlement_prepared_clock_invalid")

    def _target_index(self, plan: dict) -> dict:
        if type(plan) is not dict or type(plan.get("targets")) is not list:
            _fail("d1_operation_plan_invalid")
        targets = {}
        entry_ids = []
        for target in plan["targets"]:
            key = _target_key(self.key, target)
            identity = target["identity"]
            if not self.is_capture:
                entry_id = identity.get("entry_id")
                if (key in targets or identity.keys() != {"entry_id"} or type(entry_id) is not int
                        or target.get("status") not in ("READY", "PENDING")):
                    _fail("d1_operation_target_invalid")
                targets[key] = target
                entry_ids.append(entry_id)
                continue
            index = identity.get("pack_index")
            snapshot = identity.get("pack_sha256")
            position = identity.get("observation_index")
            if (key in targets or type(index) is not int or not 0 <= index < len(self.input_evidence)
                    or type(position) is not int or position < 0
                    or snapshot != self.input_evidence[index]["snapshot_sha256"]
                    or target.get("status") not in ("READY", "MISSING_QUOTE", "REJECTED")):
                _fail("d1_operation_target_invalid")
            targets[key] = target
        if not self.is_capture and entry_ids != self.slot.scope["entry_ids"]:
            _fail("d1_operation_target_invalid")
        return targets

    def _item_inputs(self, target: dict) -> list[str]:
        return [*self.inputs, *self.prepared["output_evidence"], f"target_sha256:{_digest(target)}"]

    def _record_item(self, key: str, target: dict, status: str, *, reason=None, before=None,
                     business=None, ids=None, origin=None, write_started=None, write_finished=None,
                     locked_read=None, cash_discrepancy=None):
        at = pg.d1_database_time(self.conn)
        if at < self.attempt_read["occurred_at"]:
            _fail("d1_operation_clock_conflict")
        if status in ("WRITTEN", "SETTLED") and at >= self.slot.write_deadline:
            _fail("d1_operation_write_expired")
        if ids is None:
            ids = ({"candidate_mark_id": None, "close_evidence_id": None} if self.is_capture
                   else {"settlement_id": None, "cash_event_id": None})
        document = canonical_document(self._document(
            at, item_operation_key=key, identity=target["identity"], target_sha256=_digest(target),
            prepared=_receipt_ref(self.prepared), prepared_reference=self.prepared["output_evidence"],
            attempt_read=_receipt_ref(self.attempt_read), started=_receipt_ref(self.started),
            status=status, reason=reason, before=before, business=business, business_ids=ids,
            business_sha256=None if business is None else _digest(business),
            origin=origin, write_started_at=write_started, write_finished_at=write_finished,
        ))
        if not self.is_capture:
            document.update(locked_read=canonical_document(locked_read),
                            cash_discrepancy=canonical_document(cash_discrepancy))
        refs = _append_document(self.conn, self.item_kind, key, document, at)
        row = _receipt(self.conn, self.context, key=key, command=self.item_command,
                       phase="SUCCEEDED" if status in self.terminal else "FAILED", at=at,
                       scheduled_for=self.slot.scheduled_for, inputs=self._item_inputs(target),
                       outputs=refs, error_code=None if status in self.terminal else reason)
        return _item_result(row, document), row

    def _read_for_attempt(self, started: dict) -> dict:
        attempt = started["attempt"]
        if attempt == self.prepared["attempt"]:
            return self.prepared
        row = pg.read_command_run_event(self.conn, self.key, attempt, "RECOVERY_READ")
        if row is None:
            _fail("d1_operation_recovery_receipt_missing")
        document = self._read_attempt_document(row, _RECOVERY_KIND, source=self.key)
        if (row["command"] != self.context["command"] or row["input_evidence"] != list(self.inputs)
                or row["error_code"] is not None or document.get("started") != _receipt_ref(started)
                or document.get("prepared") != _receipt_ref(self.prepared)
                or document.get("prepared_reference") != self.prepared["output_evidence"]
                or document.get("late") is not (row["occurred_at"] >= self.slot.write_deadline)
                or not max(started["occurred_at"], self.prepared["occurred_at"])
                <= _utc(document.get("read_started_at")) <= _utc(document.get("read_finished_at"))
                <= row["occurred_at"]):
            _fail("d1_operation_recovery_receipt_conflict")
        return row

    def _read_item(self, key: str, target: dict, row: dict) -> dict:
        document = self._read_attempt_document(row, self.item_kind, source=key)
        started = pg.read_command_run_event(self.conn, self.key, row["attempt"], "STARTED")
        if started is None:
            _fail("d1_operation_started_binding_conflict")
        self._verify_started(started)
        read = self._read_for_attempt(started)
        if (row["operation_key"] != key or row["phase"] != "SUCCEEDED"
                or row["command"] != self.item_command or row["input_evidence"] != self._item_inputs(target)
                or row["run_event_key"] != store.command_run_event_key(key, row["attempt"], "SUCCEEDED")
                or row["error_code"] is not None or document.get("status") not in self.terminal
                or document.get("item_operation_key") != key or document.get("identity") != target["identity"]
                or document.get("target_sha256") != _digest(target)
                or document.get("prepared") != _receipt_ref(self.prepared)
                or document.get("prepared_reference") != self.prepared["output_evidence"]
                or document.get("started") != _receipt_ref(started)
                or document.get("attempt_read") != _receipt_ref(read)
                or row["occurred_at"] < read["occurred_at"]):
            _fail("d1_operation_item_witness_conflict")
        return document

    def _check_item_business(self, target: dict, document: dict, rows: dict,
                             capture, close_evidence) -> dict:
        if document.get("business") != canonical_document(rows) or document.get("business_sha256") != _digest(rows):
            _fail("d1_capture_witness_rows_conflict")
        if target["status"] == "MISSING_QUOTE":
            ids = {"candidate_mark_id": None, "close_evidence_id": None}
            if (document.get("status") != "MISSING_QUOTE" or rows != _EMPTY_CAPTURE_ROWS
                    or document.get("reason") != target["reason"]
                    or document.get("write_started_at") is not None
                    or document.get("write_finished_at") is not None):
                _fail("d1_capture_witness_rows_conflict")
        else:
            if target["status"] != "READY" or document.get("status") not in ("WRITTEN", "REUSED"):
                _fail("d1_capture_witness_rows_conflict")
            ids = pg.d1_compare_capture_rows(rows, capture, close_evidence)
            if (document.get("reason") is not None
                    or not self.prepared["occurred_at"] <= _utc(document.get("write_started_at"))
                    <= _utc(document.get("write_finished_at")) <= _utc(document["recorded_at"])
                    or _utc(document.get("write_finished_at")) >= self.slot.write_deadline):
                _fail("d1_capture_witness_rows_conflict")
        if document.get("business_ids") != ids:
            _fail("d1_capture_witness_rows_conflict")
        return ids

    def _item_origin(self, key: str, target: dict, before_attempt: int, rows: dict,
                     capture, close_evidence):
        origin = self._original_item_receipt(key, before_attempt)
        if origin is None:
            return None, None
        document = self._read_item(key, target, origin)
        self._check_item_business(target, document, rows, capture, close_evidence)
        if (document["origin"] is not None or document["before"] != _EMPTY_CAPTURE_ROWS
                or document["status"] != ("WRITTEN" if capture is not None else "MISSING_QUOTE")
                or (capture is not None and origin["occurred_at"] >= self.slot.write_deadline)):
            _fail("d1_capture_first_witness_conflict")
        return _receipt_ref(origin), document

    def _original_item_receipt(self, key: str, before_attempt: int) -> dict | None:
        return next((row for row in pg.d1_operation_receipts(self.conn, key)
                     if row["phase"] == "SUCCEEDED" and row["attempt"] < before_attempt), None)

    def _process_capture(self, key, target, capture, close_evidence, address, before):
        origin, original = self._item_origin(key, target, self.attempt, before, capture, close_evidence)
        if origin is not None:
            status = "REUSED" if capture is not None else "MISSING_QUOTE"
            return self._record_item(
                key, target, status, before=before, business=before, ids=original["business_ids"],
                origin=origin, reason=original["reason"], write_started=original["write_started_at"],
                write_finished=original["write_finished_at"],
            )
        if before != _EMPTY_CAPTURE_ROWS:
            return self._record_item(key, target, "CONFLICT", before=before,
                                     reason="d1_capture_unattributed_rows")
        if capture is None:
            return self._record_item(key, target, "MISSING_QUOTE", reason=target["reason"],
                                     before=before, business=before)

        candidate = target["candidate"]
        latest = pg.d1_candidate_capture_target(
            self.conn, scan_id=candidate["scan_id"], candidate_key=candidate["candidate_key"])
        if canonical_document(latest) != candidate:
            return self._record_item(key, target, "CONFLICT", before=before,
                                     reason="d1_capture_candidate_binding_changed")
        write_started = pg.d1_database_time(self.conn)
        if write_started >= self.slot.write_deadline:
            return self._record_item(key, target, "EXPIRED", before=before,
                                     reason="d1_operation_write_expired")
        written = pg.mark_capture_upsert(self.conn, capture, close_evidence=close_evidence)
        business = pg.d1_capture_rows(self.conn, **address)
        ids = pg.d1_compare_capture_rows(business, capture, close_evidence)
        if (any(written[name] != value for name, value in ids.items())
                or not written["candidate_mark_inserted"]
                or written["close_evidence_inserted"] != (close_evidence is not None)):
            _fail("d1_capture_write_conflict")
        write_finished = pg.d1_database_time(self.conn)
        if not self.attempt_read["occurred_at"] <= write_started <= write_finished:
            _fail("d1_operation_clock_conflict")
        outcome = self._record_item(key, target, "WRITTEN", before=before, business=business, ids=ids,
                                    write_started=write_started, write_finished=write_finished)
        # This sample and the item clock precede commit. A new connection's
        # visibility of both the business rows and this witness proves commit.
        if pg.d1_database_time(self.conn) >= self.slot.write_deadline:
            _fail("d1_operation_write_expired")
        return outcome

    def capture_item(self, target: dict, *, capture, close_evidence) -> dict:
        """Commit one frozen B2 target with its matching business witness."""
        key = _target_key(self.key, target)
        if self.attempt_read is None or key in self.results or target != self.targets.get(key):
            _fail("d1_operation_target_invalid")
        target_status = target["status"]
        if (target.get("mark") != (None if capture is None else canonical_document(capture.comparison()))
                or target.get("close") != (None if close_evidence is None else
                                          canonical_document(asdict(close_evidence)))
                or (target_status == "READY") != (capture is not None)):
            _fail("d1_capture_material_conflict")
        before = None
        try:
            with self.conn.transaction():
                self.conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE")
                self.conn.execute("SET LOCAL lock_timeout = '30s'")
                if target_status == "REJECTED":
                    outcome = self._record_item(key, target, "REJECTED", reason=target["reason"])
                else:
                    address = _capture_address(target, close_evidence)
                    pg.d1_lock_capture_target(self.conn, address["candidate_id"], address["mark_key"])
                    before = pg.d1_capture_rows(self.conn, **address)
                    outcome = self._process_capture(key, target, capture, close_evidence, address, before)
        except Exception as error:
            reason = _error_code(error, "d1_capture_item_failed")
            if not self.conn.closed:
                with self.conn.transaction():
                    outcome = self._record_item(key, target, "ERROR", reason=reason, before=before)
                self.results[key] = outcome
            _fail(reason)
        self.results[key] = outcome
        return outcome[0]

    def _check_settlement_proof(self, target: dict, row: dict, proof: dict) -> None:
        status = proof["status"]
        started, finished = proof["write_started_at"], proof["write_finished_at"]
        locked = proof["locked_read"]
        if locked is not None and not (
                self.prepared["occurred_at"] <= _utc(locked.get("read_started_at"))
                <= _utc(locked.get("read_finished_at")) <= row["occurred_at"]):
            _fail("d1_settlement_witness_conflict")
        if status == "PENDING":
            if (type(proof["reason"]) is not str or not proof["reason"]
                    or proof["business"] is not None or proof["business_sha256"] is not None
                    or proof["business_ids"] != {"settlement_id": None, "cash_event_id": None}
                    or started is not None or finished is not None
                    or (target["status"] == "PENDING" and proof["reason"] != target["reason"])
                    or (target["status"] == "READY" and locked is None)):
                _fail("d1_settlement_witness_conflict")
            return
        if (target["status"] != "READY" or locked is None or proof["reason"] is not None
                or not _utc(locked["read_finished_at"]) <= _utc(started) <= _utc(finished) <= row["occurred_at"]
                or _utc(finished) >= self.slot.write_deadline):
            _fail("d1_settlement_witness_conflict")
        record, opening = _settlement_material(target)
        rows = pg.d1_settlement_rows(self.conn, record)
        ids = pg.d1_compare_settlement_rows(rows, record, opening)
        opening_rows = pg.d1_settlement_opening_rows(self.conn, record.entry_id)
        if (proof["business"] != canonical_document(rows) or proof["business_sha256"] != _digest(rows)
                or proof["business_ids"] != ids or locked["cash_rows"] != canonical_document(opening_rows)):
            _fail("d1_settlement_witness_rows_conflict")

    def _first_settlement_proof(self, target: dict, item: dict, proof: dict) -> None:
        self._check_settlement_proof(target, item, proof)
        if (proof["origin"] is not None or proof["status"] == "REUSED"
                or (proof["status"] == "SETTLED" and
                    (proof["before"] != _EMPTY_SETTLEMENT_ROWS or item["occurred_at"] >= self.slot.write_deadline))):
            _fail("d1_settlement_first_witness_conflict")

    def _verify_settlement_replay(self, key: str, target: dict, item: dict, proof: dict) -> None:
        prior = self._original_item_receipt(key, item["attempt"])
        if prior is None:
            self._first_settlement_proof(target, item, proof)
            return
        original = self._read_item(key, target, prior)
        self._first_settlement_proof(target, prior, original)
        expected = "PENDING" if original["status"] == "PENDING" else "REUSED"
        fields = ("reason", "business", "business_sha256", "business_ids", "cash_discrepancy", "locked_read",
                  "write_started_at", "write_finished_at")
        if (proof["origin"] != _receipt_ref(prior) or proof["status"] != expected
                or proof["before"] != original["business"]
                or any(proof[field] != original[field] for field in fields)):
            _fail("d1_settlement_first_witness_conflict")

    def _write_settlement(self, key, target, record, automatic, revalidate, before):
        read_started = pg.d1_database_time(self.conn)
        entry_id = record.entry_id
        source_id = automatic.outcome.outcome_id
        latest, = pg.d1_settlement_targets(
            self.conn, team=self.context["team"], entry_ids=[entry_id], source_outcome_id=source_id)
        check = revalidate(latest)
        if (type(check) is not dict or type(check.get("binding")) is not dict or "reason" not in check
                or (check["reason"] is not None and
                    (type(check["reason"]) is not str or not check["reason"]))):
            _fail("d1_settlement_recheck_invalid")
        read_finished = pg.d1_database_time(self.conn)
        if not self.attempt_read["occurred_at"] <= read_started <= read_finished:
            _fail("d1_operation_clock_conflict")
        observed_cash = latest["opening_cash"]
        discrepancy = observed_cash.as_dict() if isinstance(observed_cash, settle.EntryCashDiscrepancy) else None
        locked = {"read_started_at": read_started, "read_finished_at": read_finished,
                  "binding": canonical_document(check["binding"]), "source_outcome_id": source_id,
                  "selected_outcomes": latest["outcomes"], "cash_rows": latest["cash_rows"],
                  "opening_cash": None if observed_cash is None else asdict(observed_cash)}
        if check["reason"] is not None:
            return self._record_item(key, target, "PENDING", reason=check["reason"], before=before,
                                     locked_read=locked, cash_discrepancy=discrepancy)
        if (latest["state"] != "OPEN" or not latest["outcomes"]
                or canonical_document(check["binding"].get("verified")) != target["binding"]):
            return self._record_item(key, target, "PENDING", reason="d1_settlement_binding_changed",
                                     before=before, locked_read=locked, cash_discrepancy=discrepancy)
        write_started = pg.d1_database_time(self.conn)
        if write_started >= self.slot.write_deadline:
            return self._record_item(key, target, "EXPIRED", reason="d1_operation_write_expired",
                                     before=before, locked_read=locked)
        try:
            written = pg.record_automatic_settlement(self.conn, record, automatic)
        except settle.EntryCashMismatch as error:
            locked["cash_guard_at"] = pg.d1_database_time(self.conn)
            return self._record_item(key, target, "PENDING", reason=error.code, before=before,
                                     locked_read=locked, cash_discrepancy=error.discrepancy.as_dict())
        business = pg.d1_settlement_rows(self.conn, record)
        ids = pg.d1_compare_settlement_rows(business, record, automatic.opening_cash)
        if (not written["inserted"] or written["settlement_id"] != ids["settlement_id"]
                or written["bankroll_event_id"] != ids["cash_event_id"]):
            _fail("d1_settlement_write_conflict")
        if pg.d1_settlement_opening_rows(self.conn, entry_id) != latest["cash_rows"]:
            _fail("d1_settlement_opening_rows_changed")
        write_finished = pg.d1_database_time(self.conn)
        if not read_finished <= write_started <= write_finished:
            _fail("d1_operation_clock_conflict")
        outcome = self._record_item(key, target, "SETTLED", before=before, business=business, ids=ids,
                                    locked_read=locked, write_started=write_started, write_finished=write_finished)
        if pg.d1_database_time(self.conn) >= self.slot.write_deadline:
            _fail("d1_operation_write_expired")
        return outcome

    def settlement_item(self, target: dict, *, record, automatic, revalidate) -> dict:
        """Resolve one frozen entry and commit the guarded writer with its witness."""
        key = _target_key(self.key, target)
        if self.is_capture or self.attempt_read is None or key in self.results or target != self.targets.get(key):
            _fail("d1_operation_target_invalid")
        entry_id = target["identity"]["entry_id"]
        if target["status"] == "READY":
            if (not isinstance(record, store.SettlementRecord) or not isinstance(automatic, settle.AutomaticSettlement)
                    or not callable(revalidate) or record.entry_id != entry_id
                    or type(target.get("source_outcome_id")) is not int
                    or target["source_outcome_id"] != record.source_outcome_id
                    or target["source_outcome_id"] != automatic.outcome.outcome_id
                    or target["binding"] != canonical_document(automatic.d1_binding)
                    or target["record"] != canonical_document(asdict(record))
                    or target["automatic"] != canonical_document(asdict(automatic))):
                _fail("d1_settlement_material_conflict")
        elif (record is not None or automatic is not None or type(target.get("reason")) is not str
              or not target["reason"]):
            _fail("d1_settlement_material_conflict")

        before = None
        try:
            with self.conn.transaction():
                self.conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE")
                self.conn.execute("SET LOCAL lock_timeout = '30s'")
                pg.d1_lock_settlement_entry(self.conn, entry_id)
                prior = self._original_item_receipt(key, self.attempt)
                if prior is not None:
                    original = self._read_item(key, target, prior)
                    self._first_settlement_proof(target, prior, original)
                    status = "PENDING" if original["status"] == "PENDING" else "REUSED"
                    outcome = self._record_item(
                        key, target, status, reason=original["reason"], before=original["business"],
                        business=original["business"], ids=original["business_ids"], origin=_receipt_ref(prior),
                        write_started=original["write_started_at"], write_finished=original["write_finished_at"],
                        locked_read=original["locked_read"], cash_discrepancy=original["cash_discrepancy"],
                    )
                elif record is None:
                    outcome = self._record_item(key, target, "PENDING", reason=target["reason"],
                                                cash_discrepancy=target.get("cash_discrepancy"))
                else:
                    before = pg.d1_settlement_rows(self.conn, record)
                    if before != _EMPTY_SETTLEMENT_ROWS:
                        outcome = self._record_item(key, target, "CONFLICT", before=before,
                                                    reason="d1_settlement_unattributed_rows")
                    else:
                        outcome = self._write_settlement(key, target, record, automatic, revalidate, before)
        except Exception as error:
            reason = _error_code(error, "d1_settlement_item_failed")
            if not self.conn.closed:
                with self.conn.transaction():
                    self.results[key] = self._record_item(key, target, "ERROR", reason=reason, before=before)
            _fail(reason)
        self.results[key] = outcome
        return outcome[0]

    def _verify_summary(self, summary: dict, results: dict) -> None:
        items = [{"identity": target["identity"], **results[key][0]}
                 for key, target in self.targets.items()]
        if type(summary) is not dict or summary.get("items") != items:
            _fail("d1_operation_summary_conflict")
        if not self.is_capture:
            settled = [item for item in items if item["status"] in ("SETTLED", "REUSED")]
            pending = [item for item in items if item["status"] == "PENDING"]
            if (summary.get("snapshot_entries") != len(items)
                    or summary.get("settled_count") != len(settled)
                    or summary.get("pending_count") != len(pending)
                    or [row["entry_id"] for row in summary.get("settled", [])]
                    != [item["identity"]["entry_id"] for item in settled]
                    or [row["entry_id"] for row in summary.get("pending", [])]
                    != [item["identity"]["entry_id"] for item in pending]):
                _fail("d1_operation_summary_conflict")
            for output, item in zip(summary["settled"], settled, strict=True):
                if (output.get("settlement_id"), output.get("bankroll_event_id"), output.get("inserted")) != (
                        item["settlement_id"], item["cash_event_id"], item["status"] == "SETTLED"):
                    _fail("d1_operation_summary_conflict")
            for output, item in zip(summary["pending"], pending, strict=True):
                if output.get("reason") != item["reason"] or output.get("cash_discrepancy") != item["cash_discrepancy"]:
                    _fail("d1_operation_summary_conflict")
            return
        if (summary.get("observations") != len(items)
                or summary.get("marks_written") != sum(item["candidate_mark_id"] is not None for item in items)
                or summary.get("close_evidence_written") != sum(item["close_evidence_id"] is not None
                                                                for item in items)
                or summary.get("missing_quote") != sum(item["status"] == "MISSING_QUOTE" for item in items)):
            _fail("d1_operation_summary_conflict")

    def finish(self, summary: dict) -> dict:
        """Publish success only when the frozen denominator has durable results."""
        if self.attempt_read is None or self.targets.keys() != self.results.keys():
            _fail("d1_operation_targets_incomplete")
        for key in self.targets:
            result, _ = self.results[key]
            if result["status"] not in self.terminal:
                _fail(result["reason"] or "d1_operation_target_failed")
        summary = canonical_document(summary)
        self._verify_summary(summary, self.results)
        items = [_receipt_ref(self.results[key][1]) for key in self.targets]
        with self.conn.transaction():
            at = pg.d1_database_time(self.conn)
            if at < max(_utc(item["occurred_at"]) for item in [_receipt_ref(self.attempt_read), *items]):
                _fail("d1_operation_clock_conflict")
            document = self._document(at, started=_receipt_ref(self.started),
                                      prepared=_receipt_ref(self.prepared), prepared_reference=self.prepared["output_evidence"],
                                      attempt_read=_receipt_ref(self.attempt_read), items=items, summary=summary)
            refs = _append_document(self.conn, _FINISHED_KIND, self.key, document, at)
            self._parent_receipt("SUCCEEDED", at, outputs=refs)
        self.completed_summary = summary
        return summary

    def _completed(self, started: dict, row: dict) -> dict:
        if self.prepared is None:
            _fail("d1_operation_prepared_conflict")
        with pg.d1_read_view(self.conn):
            plan = self._read_prepared()
            self.targets = self._target_index(plan)
            read = self._read_for_attempt(started)
            document = self._read_attempt_document(row, _FINISHED_KIND, source=self.key)
            if (row["command"] != self.context["command"] or row["input_evidence"] != list(self.inputs)
                    or row["error_code"] is not None or document.get("started") != _receipt_ref(started)
                    or document.get("prepared") != _receipt_ref(self.prepared)
                    or document.get("prepared_reference") != self.prepared["output_evidence"]
                    or document.get("attempt_read") != _receipt_ref(read)
                    or row["occurred_at"] < read["occurred_at"]):
                _fail("d1_operation_finished_conflict")
            results = {}
            for key, target in self.targets.items():
                item = pg.read_command_run_event(self.conn, key, row["attempt"], "SUCCEEDED")
                if item is None or row["occurred_at"] < item["occurred_at"]:
                    _fail("d1_operation_targets_incomplete")
                proof = self._read_item(key, target, item)
                if not self.is_capture:
                    self._verify_settlement_replay(key, target, item, proof)
                    results[key] = (_item_result(item, proof), item)
                    continue
                capture = _capture_material(target.get("mark"), store.CandidateMarkCapture)
                close_evidence = _capture_material(target.get("close"), store.PriceEvidenceObservation)
                rows = pg.d1_capture_rows(self.conn, **_capture_address(target, close_evidence))
                self._check_item_business(target, proof, rows, capture, close_evidence)
                origin, prior = self._item_origin(key, target, row["attempt"], rows, capture, close_evidence)
                if origin is None:
                    if (proof.get("origin") is not None or proof.get("before") != _EMPTY_CAPTURE_ROWS
                            or (capture is not None and (proof["status"] != "WRITTEN"
                                or item["occurred_at"] >= self.slot.write_deadline))):
                        _fail("d1_capture_first_witness_conflict")
                elif (proof.get("origin") != origin or proof.get("before") != canonical_document(rows)
                      or proof["status"] != ("REUSED" if capture is not None else "MISSING_QUOTE")
                      or any(proof.get(name) != prior.get(name)
                             for name in ("write_started_at", "write_finished_at"))):
                    _fail("d1_capture_first_witness_conflict")
                results[key] = (_item_result(item, proof), item)
            if document.get("items") != [_receipt_ref(item) for _, item in results.values()]:
                _fail("d1_operation_finished_conflict")
            summary = document.get("summary")
            self._verify_summary(summary, results)
        return summary

    def fail(self, reason: str) -> None:
        if self.started is None or self.completed_summary is not None:
            return
        with self.conn.transaction():
            if pg.read_command_run_event(self.conn, self.key, self.attempt, "FAILED") is not None:
                return
            at = pg.d1_database_time(self.conn)
            document = self._document(at, reason=reason, started=_receipt_ref(self.started),
                                      prepared=_receipt_ref(self.prepared) if self.prepared else None,
                                      items=[_receipt_ref(row) for _, row in self.results.values()])
            refs = _append_document(self.conn, _FAILURE_KIND, self.key, document, at)
            self._parent_receipt("FAILED", at, outputs=refs, reason=reason)


def _pack_refs(registrations: tuple) -> list[dict]:
    return [{"pack": registration.source, "snapshot_sha256": registration.content_sha256,
             "byte_length": registration.byte_length} for registration in registrations]


def _target_key(parent: str, target: dict) -> str:
    if type(target) is not dict or type(target.get("identity")) is not dict:
        _fail("d1_operation_target_invalid")
    return _ITEM_PREFIX + _digest({"parent": parent, "identity": target["identity"]})


def _capture_address(target: dict, close_evidence) -> dict:
    observation = target["observation"]
    candidate_id = target["identity"]["candidate_id"]
    market = (observation["market_ref_kind"], observation["market_ref"], observation["side"],
              observation["source_kind"], _utc(observation["observed_at"]))
    return {"candidate_id": candidate_id, "mark_key": store.candidate_mark_key(candidate_id, *market),
            "close_key": store.price_evidence_key(candidate_id, "CLOSE", *market),
            "planned_close": close_evidence is not None}


def _capture_material(value: dict | None, row_type):
    if value is None:
        return None
    try:
        columns = dict(value)
        columns["observed_at"] = _utc(columns["observed_at"])
        for name in ("bid", "ask", "mid", "last", "model_probability"):
            if columns.get(name) is not None:
                columns[name] = Decimal(columns[name])
        return row_type(**columns).validate()
    except (InvalidOperation, KeyError, TypeError, ValueError):
        _fail("d1_capture_material_conflict")


def _settlement_material(target: dict) -> tuple[store.SettlementRecord, settle.OpeningCash]:
    try:
        columns = dict(target["record"])
        columns["confirmed_at"] = _utc(columns["confirmed_at"])
        for name in ("exit_price", "exit_fee", "proceeds", "pnl_gross", "fee_total", "pnl_net"):
            if columns[name] is not None:
                columns[name] = Decimal(columns[name])
        record = store.SettlementRecord(**columns).validate()
        cash = target["automatic"]["opening_cash"]
        opening = settle.OpeningCash(cash["event_id"], Decimal(cash["amount"]), Decimal(cash["paid_fee"]))
    except (InvalidOperation, KeyError, TypeError, ValueError):
        _fail("d1_settlement_material_conflict")
    return record, opening


def _item_result(row: dict, document: dict) -> dict:
    status = document["status"]
    result = {"status": status, "reason": document["reason"], **document["business_ids"],
              "receipt": _receipt_ref(row), "witness": row["output_evidence"],
              "first_write": _receipt_ref(row) if status in ("WRITTEN", "SETTLED") else
              document["origin"] if status == "REUSED" else None,
              "first_write_at": document["write_finished_at"]}
    if "locked_read" in document:
        result.update(locked_read=document["locked_read"], cash_discrepancy=document["cash_discrepancy"])
    return result
