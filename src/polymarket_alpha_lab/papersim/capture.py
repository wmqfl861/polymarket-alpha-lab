"""B2 capture-marks orchestration (plan 05 section 2 / DELIVERY_PLAN 80.8).

One capture run is: a clean-worktree code-commit check (FX-03 pattern,
reusing :func:`polymarket_alpha_lab.papersim.scan.require_clean_worktree`;
every receipt row records the verified HEAD), registration of each mark
pack's RAW bytes in ``papersim.snapshots`` BEFORE any normalization
(FX-12 discipline), per-observation resolution of the stored candidate
through ``store_psycopg.candidate_capture_target`` (FX-26 event identity:
the pack names the producing scan plus the candidate key, never a
guess), FX-34 classification of every observation PERIODIC vs
CLOSE-eligible against the candidate's stored ``event_start_at`` (a CLOSE
must be observed STRICTLY before the event start; at/after the start --
for example an NBA full-game mark observed during overtime, hours after
tip-off -- is stored as PERIODIC only, and a candidate without an event
start produces no CLOSE at all), and one
:func:`polymarket_alpha_lab.papersim.store_psycopg.mark_capture_upsert`
write per usable observation (marks for ALL candidates -- rejected and
construction-failed candidates keep PERIODIC marks; there is no entry
dependency and no entry-level mark is written here: that is B3's settle
lane). Receipts land in ``papersim.command_run_events`` with command
``capture-marks`` (STARTED/SUCCEEDED/FAILED) keyed
operation|attempt|phase.

Causality (plan section 3): ``run-scan`` writes candidates including
``event_start_at`` FIRST; ``capture-marks`` appends marks and CLOSE price
evidence only and never mutates scan, candidate or entry rows.
``observed_at`` is always the pack's SOURCE observation timestamp, never
the write clock; the receipt ``occurred_at`` is the write clock. Every
capture row cites ``snapshot_sha256`` (its own when the pack provides
one, else the raw pack digest).

Coverage (05 section 2, B2 row "coverage figures go into the command's
own report output"): the SUCCEEDED receipt ``output_evidence`` and the
stdout JSON carry the SAME stable summary -- candidates seen, marks
written, CLOSE eligible, downgraded-to-periodic, missing event start,
missing quote -- plus the per-source-kind provenance counts and the raw
pack digests. The summary deliberately contains only pack- and
store-derived values (no write-clock content, no per-attempt "inserted"
flags), so re-running the same pack inserts zero rows and returns an
IDENTICAL summary, and the SUCCEEDED receipt stays idempotent under its
scheduling key. The coverage terms cite the evaluation module's FX-27
wording verbatim so the report cannot drift from standard C.

Cadence note (plan section 6 deviation): the original M2 plan original
(``m2-plan-formal.md``) is registered original-missing (FX-28), so the
original mark cadence is unknowable from the repo; B2 is deliberately
cadence-agnostic -- marks arrive as prepared packs exactly like run-scan
and scheduling belongs to the server cron recorded in the receipts.

A registered D1 operation freezes the pack, B1 candidate and sealed CLOB
observation before writing. Each target is then attributed to its own
managed result so interrupted attempts can verify and resume the original
denominator.

Purity profile: the config/pack parsers, the FX-34 classifier and the
record builders are pure over their explicit inputs; ``run_capture`` is
the only stateful driver and takes every external dependency as an
explicit argument (packs, clock, connection), so the offline suite
exercises everything except the transactional writes, which the native
lane proves on a disposable cluster. The command itself opens no network
session: prices arrive as prepared packs (adapter fetch budgeting stays
in the adapter lane).

Paper boundary: paper-only / report-only / readonly research evidence
storage; no order, account, credential or signing surface anywhere.
"""

from __future__ import annotations

import datetime
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from polymarket_alpha_lab.papersim import store
from polymarket_alpha_lab.papersim.scan import require_clean_worktree

__all__ = [
    "CAPTURE_COMMAND",
    "CAPTURE_PACK_SNAPSHOT_KIND",
    "COVERAGE_TERMS",
    "CaptureConfig",
    "CaptureError",
    "MarkObservation",
    "MarkPack",
    "capture_from_observation",
    "classify_observation",
    "close_evidence_from_observation",
    "config_from_mapping",
    "load_mark_packs",
    "observations_from_pack",
    "run_capture",
]

CAPTURE_COMMAND = "capture-marks"

#: ``papersim.snapshots`` kind stamped on a registered mark-pack's raw bytes.
CAPTURE_PACK_SNAPSHOT_KIND = "capture_mark_pack"

#: The FX-27/OD-K criterion C coverage terms, cited verbatim from the
#: evaluation module (``ClvMetrics`` commentary and ``criterion_c``): the
#: capture summary reports against the SAME words standard C judges with,
#: so the B2 report cannot drift from the B4 evaluation lane.
COVERAGE_TERMS = ("criterion C coverage: close marks over ALL entries; "
                  "close-mark coverage = entries_with_close / entries_eligible; "
                  "entries that lack a close mark count as missing, never zero-filled")


class CaptureError(ValueError):
    """One capture-level refusal with a fixed code (never a silent skip)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Configuration and the prepared-mark pack
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CaptureConfig:
    """The capture-wide configuration (one capture-marks invocation)."""

    team: str = ""
    operator: str = "unattended"
    packs: tuple[str, ...] = ()

    def validate(self) -> "CaptureConfig":
        if type(self.team) is not str or not self.team.strip():
            raise CaptureError("papersim_capture_team_invalid")
        if type(self.operator) is not str or not self.operator.strip():
            raise CaptureError("papersim_capture_operator_invalid")
        for pack in self.packs:
            if type(pack) is not str or not pack.strip():
                raise CaptureError("papersim_capture_pack_invalid")
        return self


def config_from_mapping(document: Mapping[str, Any]) -> CaptureConfig:
    """Parse one capture config JSON document (validated, no file IO)."""
    if not isinstance(document, Mapping):
        raise CaptureError("papersim_capture_config_invalid",
                           "document must be an object")
    known = {"team", "operator", "packs"}
    unknown = set(document) - known
    if unknown:
        raise CaptureError("papersim_capture_config_invalid",
                           ",".join(sorted(unknown)))
    packs = document.get("packs", ())
    if not isinstance(packs, Sequence) or isinstance(packs, (str, bytes)):
        raise CaptureError("papersim_capture_config_invalid", "packs must be a list")
    return CaptureConfig(
        team=str(document.get("team", "")),
        operator=str(document.get("operator", "unattended")),
        packs=tuple(str(pack) for pack in packs),
    ).validate()


@dataclass(frozen=True)
class MarkPack:
    """One prepared mark pack: its config-relative name and RAW bytes."""

    name: str
    content: bytes


@dataclass(frozen=True)
class MarkObservation:
    """One prepared price observation of ONE stored candidate.

    ``observed_at`` is the SOURCE observation timestamp from the pack
    (never the write clock). Exactly one of ``scan_id`` /
    ``scan_operation_key`` names the producing scan: a candidate key is
    unique only inside its scan. Prices are the pack's raw values; a row
    where all four price fields are None carries no usable quote data and
    is counted as ``missing_quote`` (explicitly reported, never silently
    skipped and never zero-filled). ``sampling_kind`` is the REQUESTED
    kind; :func:`classify_observation` decides what may actually be
    stored under FX-34.
    """

    candidate_key: str
    scan_id: int | None = None
    scan_operation_key: str | None = None
    sampling_kind: str = "PERIODIC"
    observed_at: datetime.datetime | None = None
    market_ref_kind: str = "market_slug"
    market_ref: str = ""
    token_ref: str | None = None
    side: str = "YES"
    bid: float | int | None = None
    ask: float | int | None = None
    mid: float | int | None = None
    last: float | int | None = None
    source_kind: str = "clob_book"
    source_ref: str | None = None
    snapshot_sha256: str | None = None


def _pack_datetime(value: Any, name: str) -> datetime.datetime:
    if isinstance(value, datetime.datetime):
        parsed = value
    elif type(value) is str:
        try:
            parsed = datetime.datetime.fromisoformat(value)
        except ValueError as error:
            raise CaptureError("papersim_capture_pack_invalid",
                               f"{name}: bad iso datetime") from error
    else:
        raise CaptureError("papersim_capture_pack_invalid", f"{name}: datetime required")
    if parsed.tzinfo is None:
        raise CaptureError("papersim_capture_pack_invalid",
                           f"{name}: tz-aware datetime required")
    return parsed


def _pack_text(value: Any, name: str, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise CaptureError("papersim_capture_pack_invalid",
                               f"{name}: non-empty text required")
        return None
    if type(value) is not str or not value.strip():
        raise CaptureError("papersim_capture_pack_invalid",
                           f"{name}: non-empty text required")
    return value


def _pack_price(value: Any, name: str):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CaptureError("papersim_capture_pack_invalid", f"{name}: number required")
    return value


def _observation_from_mapping(raw: Any) -> MarkObservation:
    if not isinstance(raw, Mapping):
        raise CaptureError("papersim_capture_pack_invalid",
                           "observations must be objects")
    known = {"candidate_key", "scan_id", "scan_operation_key", "sampling_kind",
             "observed_at", "market_ref_kind", "market_ref", "token_ref", "side",
             "bid", "ask", "mid", "last", "source_kind", "source_ref",
             "snapshot_sha256"}
    unknown = set(raw) - known
    if unknown:
        raise CaptureError("papersim_capture_pack_invalid",
                           ",".join(sorted(unknown)))
    scan_id = raw.get("scan_id")
    scan_operation_key = raw.get("scan_operation_key")
    if (scan_id is None) == (scan_operation_key is None):
        raise CaptureError("papersim_capture_pack_invalid",
                           "exactly one of scan_id/scan_operation_key is required")
    if scan_id is not None and (isinstance(scan_id, bool) or type(scan_id) is not int):
        raise CaptureError("papersim_capture_pack_invalid", "scan_id: integer required")
    sampling_kind = str(raw.get("sampling_kind", "PERIODIC"))
    if sampling_kind not in store.MARK_SAMPLING_KINDS:
        raise CaptureError("papersim_capture_pack_invalid",
                           f"sampling_kind: {'/'.join(store.MARK_SAMPLING_KINDS)}")
    market_ref_kind = str(raw.get("market_ref_kind", "market_slug"))
    if market_ref_kind not in store.MARKET_REF_KINDS:
        raise CaptureError("papersim_capture_pack_invalid",
                           "market_ref_kind: " + "/".join(store.MARKET_REF_KINDS))
    side = str(raw.get("side", "YES"))
    if side not in store.SIDE_TOKENS:
        raise CaptureError("papersim_capture_pack_invalid",
                           "side: " + "/".join(store.SIDE_TOKENS))
    snapshot = raw.get("snapshot_sha256")
    if snapshot is not None and (type(snapshot) is not str or len(snapshot) != 64
                                 or any(c not in "0123456789abcdef" for c in snapshot)):
        raise CaptureError("papersim_capture_pack_invalid",
                           "snapshot_sha256: 64-hex digest required")
    return MarkObservation(
        candidate_key=_pack_text(raw.get("candidate_key"), "candidate_key",
                                 required=True),
        scan_id=scan_id,
        scan_operation_key=_pack_text(scan_operation_key, "scan_operation_key",
                                      required=False),
        sampling_kind=sampling_kind,
        observed_at=_pack_datetime(raw.get("observed_at"), "observed_at"),
        market_ref_kind=market_ref_kind,
        market_ref=_pack_text(raw.get("market_ref"), "market_ref", required=True),
        token_ref=_pack_text(raw.get("token_ref"), "token_ref", required=False),
        side=side,
        bid=_pack_price(raw.get("bid"), "bid"),
        ask=_pack_price(raw.get("ask"), "ask"),
        mid=_pack_price(raw.get("mid"), "mid"),
        last=_pack_price(raw.get("last"), "last"),
        source_kind=_pack_text(raw.get("source_kind", "clob_book"), "source_kind",
                               required=True),
        source_ref=_pack_text(raw.get("source_ref"), "source_ref", required=False),
        snapshot_sha256=snapshot,
    )


def observations_from_pack(payload) -> tuple[MarkObservation, ...]:
    """Parse one mark pack (JSON text/bytes or an already-loaded list).

    Format drift fails loudly with the fixed ``papersim_capture_pack_invalid``
    code -- never a silent skip.
    """
    if isinstance(payload, (str, bytes, bytearray)):
        try:
            document = json.loads(payload)
        except ValueError as error:
            raise CaptureError("papersim_capture_pack_invalid",
                               "not valid JSON") from error
    elif isinstance(payload, Sequence):
        document = payload
    else:
        raise CaptureError("papersim_capture_pack_invalid",
                           "pack must be JSON text or a list")
    if not isinstance(document, list):
        raise CaptureError("papersim_capture_pack_invalid",
                           "pack must be a JSON array")
    return tuple(_observation_from_mapping(raw) for raw in document)


def load_mark_packs(config: CaptureConfig, *, base_dir) -> tuple[MarkPack, ...]:
    """Read every configured pack file's RAW bytes relative to ``base_dir``.

    No globs (the run-scan ``load_pack_records`` pattern); unreadable
    paths fail with ``papersim_capture_pack_unreadable``.
    """
    config.validate()
    packs: list[MarkPack] = []
    for name in config.packs:
        path = Path(base_dir) / name
        try:
            content = path.read_bytes()
        except OSError as error:
            raise CaptureError("papersim_capture_pack_unreadable", str(path)) from error
        packs.append(MarkPack(name=name, content=content))
    return tuple(packs)


# ---------------------------------------------------------------------------
# FX-34 classification and record construction (pure)
# ---------------------------------------------------------------------------

#: Downgrade reasons reported in the coverage summary (fixed vocabulary).
DOWNGRADE_MISSING_EVENT_START = "missing_event_start"
DOWNGRADE_OBSERVED_AT_AFTER_EVENT_START = "observed_at_after_event_start"


def classify_observation(mark_kind: str, observed_at: datetime.datetime,
                         event_start_at: datetime.datetime | None) -> tuple[str, str | None]:
    """FX-34 classification: the stored sampling kind of one observation.

    A CLOSE is eligible only when the observation is STRICTLY before the
    candidate's ``event_start_at``; observed AT or after the start
    (equality refused -- for example an NBA full-game mark during
    overtime, hours after tip-off) it is stored PERIODIC only. A
    candidate without an event start produces no CLOSE at all (FX-34:
    counted as missing in criterion C coverage). No rule here uses game
    duration or a "scheduled end": ``event_start_at`` is the only
    boundary, and the final score including overtime is settled later by
    B3 outcome observations, never by capture.
    """
    if mark_kind != "CLOSE":
        return ("PERIODIC", None)
    if event_start_at is None:
        return ("PERIODIC", DOWNGRADE_MISSING_EVENT_START)
    if observed_at >= event_start_at:
        return ("PERIODIC", DOWNGRADE_OBSERVED_AT_AFTER_EVENT_START)
    return ("CLOSE", None)


def _snapshot_citation(observation: MarkObservation, pack_snapshot_sha256: str) -> str:
    return observation.snapshot_sha256 or pack_snapshot_sha256


def capture_from_observation(observation: MarkObservation, *, candidate_id: int,
                             sampling_kind: str,
                             pack_snapshot_sha256: str) -> store.CandidateMarkCapture:
    """Build the candidate-mark row (validated by the store layer at write)."""
    return store.CandidateMarkCapture(
        candidate_id=candidate_id,
        sampling_kind=sampling_kind,
        observed_at=observation.observed_at,
        market_ref_kind=observation.market_ref_kind,
        market_ref=observation.market_ref,
        token_ref=observation.token_ref,
        side=observation.side,
        bid=observation.bid,
        ask=observation.ask,
        mid=observation.mid,
        last=observation.last,
        source_kind=observation.source_kind,
        source_ref=observation.source_ref,
        snapshot_sha256=_snapshot_citation(observation, pack_snapshot_sha256),
    )


def close_evidence_from_observation(observation: MarkObservation, *,
                                    pack_snapshot_sha256: str
                                    ) -> store.PriceEvidenceObservation:
    """Build the CLOSE price-evidence row of one CLOSE-eligible observation.

    Values pass through EXACTLY as the pack states them: terminal 0/1
    extremes must be expressed as ``last`` (the M2 migration's own
    comment -- a terminal price is a reference last, never a fillable
    quote), because bid/ask/mid stay open-interval executable quotes in
    ``candidate_price_evidence`` and out-of-interval values fail loudly
    in the store validation instead of being silently dropped here.
    """
    return store.PriceEvidenceObservation(
        observed_at=observation.observed_at,
        evidence_role="CLOSE",
        market_ref_kind=observation.market_ref_kind,
        market_ref=observation.market_ref,
        token_ref=observation.token_ref,
        side=observation.side,
        bid=observation.bid,
        ask=observation.ask,
        mid=observation.mid,
        last=observation.last,
        snapshot_sha256=_snapshot_citation(observation, pack_snapshot_sha256),
        source_kind=observation.source_kind,
        source_ref=observation.source_ref,
    )


def has_any_price(observation: MarkObservation) -> bool:
    """Whether the observation carries ANY usable price datum.

    All-four-absent is the documented "no usable book/quote data" case:
    counted as ``missing_quote`` in the coverage summary (explicit zero/
    missing accounting) instead of failing the run or being zero-filled.
    Every OTHER malformed value still fails loudly at the store layer.
    """
    return any(value is not None
               for value in (observation.bid, observation.ask,
                             observation.mid, observation.last))


# PostgreSQL numeric has no typmod on the quote columns. It can store at
# most 16,383 fractional places, including any leading fractional zeros.
_NUMERIC_FRACTIONAL_PLACES = 16_383
_DECIMAL_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_PRICE_FIELDS = ("bid", "ask", "mid", "last")
_CLOB_MARK_SOURCE = "clob_book"
_DOWNGRADE_LATE_RECEIPT = "received_at_after_event_start"
_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
_MICROSECOND = datetime.timedelta(microseconds=1)


def _book_number(value: Any, name: str, *, allow_text: bool) -> Decimal:
    if type(value) in (int, Decimal):
        number = Decimal(value)
    elif allow_text and type(value) is str and _DECIMAL_TEXT.fullmatch(value):
        try:
            number = Decimal(value)
        except InvalidOperation:
            raise CaptureError("d1_capture_price_invalid", name) from None
    else:
        raise CaptureError("d1_capture_price_invalid", name)
    if not number.is_finite():
        raise CaptureError("d1_capture_price_invalid", name)
    return number


def _exact_price(number: Decimal, name: str) -> Decimal:
    if not 0 <= number <= 1:
        raise CaptureError("d1_capture_price_invalid", name)
    if not number:
        return Decimal(0)
    sign, digits, exponent = number.as_tuple()
    excess = -exponent - _NUMERIC_FRACTIONAL_PLACES
    if excess <= 0:
        return number
    if excess >= len(digits) or any(digits[-excess:]):
        raise CaptureError("d1_capture_price_not_exact", name)
    return Decimal((sign, digits[:-excess], exponent + excess))


def _best_book_price(levels: Any, name: str, *, best: Callable) -> Decimal | None:
    # The public adapter accepts empty sides and nonnegative sizes. Executable
    # levels require prices in (0, 1), including when the size is zero.
    if type(levels) is not list:
        raise CaptureError("d1_capture_book_levels_invalid", name)
    prices = []
    for level in levels:
        if type(level) is not dict:
            raise CaptureError("d1_capture_book_levels_invalid", name)
        price = _book_number(level.get("price"), f"{name}.price", allow_text=True)
        size = _book_number(level.get("size"), f"{name}.size", allow_text=True)
        if not 0 < price < 1 or size < 0:
            raise CaptureError("d1_capture_book_levels_invalid", name)
        prices.append(price)
    price = best(prices, default=None)
    return None if price is None else _exact_price(price, name)


def _d1_book_prices(body: dict) -> dict[str, Decimal | None]:
    bid = _best_book_price(body.get("bids"), "bid", best=max)
    ask = _best_book_price(body.get("asks"), "ask", best=min)
    if bid is not None and ask is not None and bid > ask:
        raise CaptureError("papersim_bid_ask_crossed")
    # The /book contract supplies a ladder and may include last_trade_price.
    # There is no midpoint observation in this source contract.
    last = body.get("last_trade_price")
    if last is not None:
        last = _exact_price(_book_number(last, "last", allow_text=True), "last")
    return {"bid": bid, "ask": ask, "mid": None, "last": last}


@dataclass(frozen=True)
class _D1BookSources:
    manifest_sha256: str
    manifest: dict
    receipt: dict
    cutoff: datetime.datetime
    freshness_as_of: datetime.datetime
    max_source_age_seconds: int
    max_receipt_age_seconds: int


def _d1_book_sources(conn, slot) -> _D1BookSources:
    from polymarket_alpha_lab.papersim import snapshots, store_psycopg
    from polymarket_alpha_lab.papersim.d1compare import acquire

    digest = slot.scope.get("capture_manifest_sha256")
    policy = slot.scope.get("book_age_policy")
    if (not isinstance(policy, dict)
            or any(type(policy.get(name)) is not int or policy[name] < 0
                   for name in ("max_source_age_seconds", "max_receipt_age_seconds"))):
        raise CaptureError("d1_capture_book_age_policy_unregistered")
    try:
        if type(digest) is not str or snapshots.snapshot_digest_argument(digest) != digest:
            raise CaptureError("d1_capture_manifest_invalid")
        manifest = acquire._load_batch(conn, digest)
        scope = manifest["scope"]
        scope_sha = acquire._digest(acquire._canonical(scope))
        start = _pack_datetime(scope["window_start"], "window_start")
        end = _pack_datetime(scope["window_end"], "window_end")
        started = _pack_datetime(manifest["started_at"], "started_at")
        sealed = _pack_datetime(manifest["sealed_at"], "sealed_at")
        cutoff = _pack_datetime(manifest["decision_as_of"], "decision_as_of")
        window_status = manifest["coverage"]["window_status"]
    except (KeyError, TypeError, ValueError) as error:
        raise CaptureError("d1_capture_manifest_invalid") from error
    receipt = store_psycopg.read_command_run_event(
        conn, f"{acquire._OPERATION_PREFIX}:{scope_sha}", 1, "SUCCEEDED")
    if (manifest["capture_version"] != 2 or window_status != "sealed_inside"
            or not (start <= started <= cutoff == sealed < end)
            or receipt is None or receipt["command"] != "capture-inputs"
            or receipt["config_digest"] != scope_sha or receipt["output_evidence"] != [digest]
            or receipt["scheduled_for"] != start
            or not sealed <= receipt["occurred_at"] <= slot.registered_at < slot.deadline
            or any(receipt[flag] is not True for flag in ("paper_only", "report_only", "readonly"))):
        raise CaptureError("d1_capture_manifest_receipt_invalid")
    # An earlier capture can supply a later slot only while its source and
    # receipt remain fresh at that slot's fixed decision point.
    return _D1BookSources(digest, manifest, receipt, cutoff, max(cutoff, slot.scheduled_for),
                          policy["max_source_age_seconds"], policy["max_receipt_age_seconds"])


def _d1_candidate_token(candidate: dict, observation: MarkObservation, team: str,
                        allowed_ids: set[int]) -> str:
    from polymarket_alpha_lab import replay

    if candidate["candidate_id"] not in allowed_ids:
        raise CaptureError("d1_capture_candidate_outside_slot")
    if candidate["is_legacy"] or candidate["team"] != team:
        raise CaptureError("d1_capture_candidate_team_invalid")
    receipts = {receipt["phase"]: receipt for receipt in candidate["scan_receipts"]}
    started = receipts.get("STARTED")
    succeeded = receipts.get("SUCCEEDED")
    if (started is None or succeeded is None
            or started["attempt"] != succeeded["attempt"]
            or started["code_commit"] != candidate["scan_code_commit"]
            or succeeded["code_commit"] != candidate["scan_code_commit"]
            or succeeded["scan_id"] != candidate["scan_id"]
            or not (candidate["scan_started_at"] <= started["occurred_at"]
                    <= candidate["scan_finished_at"] <= succeeded["occurred_at"])):
        raise CaptureError("d1_capture_b1_receipt_missing")
    side = candidate["side"]
    yes_token, no_token = candidate["yes_token_id"], candidate["no_token_id"]
    if (side not in ("YES", "NO") or side != observation.side or not yes_token or not no_token
            or yes_token == no_token):
        raise CaptureError("d1_capture_candidate_side_invalid")
    token = yes_token if side == "YES" else no_token
    if observation.token_ref != token:
        raise CaptureError("d1_capture_buy_token_mismatch")
    refs = {"condition_id": candidate["condition_id"], "market_id": candidate["market_id"],
            "market_slug": candidate["market_slug"], "token_id": token}
    if refs[observation.market_ref_kind] != observation.market_ref:
        raise CaptureError("d1_capture_candidate_market_mismatch")
    decisions = candidate["decisions"]
    if not decisions or any(
            row["token_ref"] != token or row["side"] != side
            or refs[row["market_ref_kind"]] != row["market_ref"]
            for row in decisions):
        raise CaptureError("d1_capture_decision_binding_mismatch")
    try:
        request = replay.request_from_replay_bundle(candidate["input_payload"])
    except replay.PapersimReplayError as error:
        raise CaptureError("d1_capture_b1_request_invalid") from error
    request_side = request.buy_side or request.side
    if type(request_side) is not str:
        raise CaptureError("d1_capture_b1_buy_binding_mismatch")
    request_side = request_side.upper()
    if (request.market_id != candidate["market_id"] or request.buy_token not in (None, token)
            or request.yes_token not in (None, yes_token) or request.no_token not in (None, no_token)
            or (request_side != side and (request_side in ("YES", "NO") or request.buy_token is None))):
        raise CaptureError("d1_capture_b1_buy_binding_mismatch")
    return token


def _d1_book_clock(value: Any) -> datetime.datetime:
    if type(value) is str and re.fullmatch(r"[0-9]{1,14}", value):
        value = int(value)
    if type(value) is not int:
        raise CaptureError("d1_capture_book_source_time_missing")
    try:
        return _EPOCH + datetime.timedelta(milliseconds=value)
    except OverflowError as error:
        raise CaptureError("d1_capture_book_source_time_invalid") from error


def _d1_quote(conn, sources: _D1BookSources, candidate: dict,
              observation: MarkObservation, token: str, evidence: dict) -> tuple[dict, datetime.datetime]:
    from polymarket_alpha_lab.papersim.adapters import polymarket
    from polymarket_alpha_lab.papersim.d1compare import acquire, journal

    markets = [market for market in sources.manifest["markets"]
               if all(candidate[key] in (None, market[field]) for key, field in (
                   ("condition_id", "condition_id"), ("market_id", "market_id"), ("market_slug", "slug")))
               and market["side_tokens"] == {"YES": candidate["yes_token_id"], "NO": candidate["no_token_id"]}]
    if len(markets) != 1:
        raise CaptureError("d1_capture_source_market_mismatch")
    condition = markets[0]["condition_id"]
    books = [book for book in sources.manifest["books"]
             if book["token_id"] == token and book["condition_id"] == condition]
    if len(books) != 1 or books[0]["request_sequence"] is None:
        raise CaptureError("d1_capture_source_book_missing")
    book = books[0]
    sequence = book["request_sequence"]
    requests = sources.manifest["requests"]
    if type(sequence) is not int or not 1 <= sequence <= len(requests):
        raise CaptureError("d1_capture_manifest_request_invalid")
    row = requests[sequence - 1]
    digest = row["content_sha256"]
    evidence.update({"condition_id": condition, "token_id": token, "request_sequence": sequence,
                     "url": row["url"], "snapshot_sha256": digest, "byte_length": row["byte_length"],
                     "source_observed_at": row["source_observed_at"], "received_at": row["utc_fetched_at"],
                     "request_status": row["status"], "request_reason": row["reason_code"]})
    try:
        source, meta = acquire._request_fields(row["url"])
    except acquire.AcquisitionError as error:
        raise CaptureError("d1_capture_manifest_request_invalid") from error
    if (source != polymarket.SOURCE_KIND_CLOB or row["source_kind"] != source
            or row["sequence"] != sequence or row["request_meta"] != meta
            or meta["query"].get("token_id") != token):
        raise CaptureError("d1_capture_manifest_request_invalid")
    if digest is None:
        raise CaptureError("d1_capture_source_book_missing")
    try:
        raw = acquire._snapshot(conn, digest)["content"]
        body = journal.decode_public_json(raw)
    except (acquire.AcquisitionError, journal.JournalError) as error:
        raise CaptureError(error.code) from error
    if len(raw) != row["byte_length"] or not isinstance(body, dict):
        raise CaptureError("d1_capture_source_book_invalid")
    if body.get("asset_id") != token or body.get("market") not in (None, condition):
        raise CaptureError("d1_capture_source_book_identity_mismatch")
    if (observation.source_kind != _CLOB_MARK_SOURCE or observation.snapshot_sha256 != digest
            or observation.source_ref != f"{row['url']}#{digest}"):
        raise CaptureError("d1_capture_source_citation_mismatch")
    if (book["reason_code"] is not None or row["status"] != "received"
            or row["window_status"] != "in_window" or row["source_time_status"] != "upstream"):
        raise CaptureError("d1_capture_source_book_unusable")
    observed_at = _d1_book_clock(body.get("timestamp"))
    received_at = _pack_datetime(row["utc_fetched_at"], "received_at")
    if (observed_at != _pack_datetime(row["source_observed_at"], "source_observed_at")
            or observed_at != observation.observed_at or not observed_at <= received_at <= sources.cutoff):
        raise CaptureError("d1_capture_book_time_mismatch")
    for stamp, limit in ((observed_at, sources.max_source_age_seconds),
                         (received_at, sources.max_receipt_age_seconds)):
        if (sources.freshness_as_of - stamp) // _MICROSECOND > limit * 1_000_000:
            raise CaptureError("d1_capture_book_stale")
    return _d1_book_prices(body), received_at


def _d1_capture_materials(conn, sources: _D1BookSources, target: dict, raw: dict,
                          observation: MarkObservation, team: str, allowed_ids: set[int]):
    candidate = target["candidate"]
    if candidate is None:
        raise CaptureError("papersim_capture_candidate_missing")
    token = _d1_candidate_token(candidate, observation, team, allowed_ids)
    prices, received_at = _d1_quote(conn, sources, candidate, observation, token, target["source"])
    target["observed_prices"] = {name: None if value is None else str(value) for name, value in prices.items()}
    claims = {}
    differences = {}
    for name, value in prices.items():
        claim = raw.get(name)
        try:
            claims[name] = None if claim is None else _exact_price(_book_number(claim, name, allow_text=False), name)
        except CaptureError as error:
            differences[name] = {"observed": target["observed_prices"][name], "claimed": target["claimed_prices"][name]}
            target["quote_differences"] = differences
            raise error
        if claims[name] != value:
            differences[name] = {"observed": target["observed_prices"][name], "claimed": target["claimed_prices"][name]}
    if differences:
        target["quote_differences"] = differences
        raise CaptureError("d1_capture_quote_mismatch")
    priced = replace(observation, **claims)
    event_start = candidate["event_start_at"]
    boundary = None if event_start is None else _pack_datetime(event_start, "event_start_at")
    kind, downgrade = classify_observation(priced.sampling_kind, priced.observed_at, boundary)
    if kind == "CLOSE" and received_at >= boundary:
        kind, downgrade = "PERIODIC", _DOWNGRADE_LATE_RECEIPT
    target.update(sampling_kind=kind, downgrade=downgrade)
    if not has_any_price(priced):
        target.update(status="MISSING_QUOTE", reason="d1_capture_no_quote")
        return None, None
    capture = capture_from_observation(priced, candidate_id=candidate["candidate_id"],
                                       sampling_kind=kind, pack_snapshot_sha256=target["identity"]["pack_sha256"])
    close = None
    if kind == "CLOSE":
        close = close_evidence_from_observation(priced, pack_snapshot_sha256=target["identity"]["pack_sha256"])
        store.validate_evidence_time_order(
            _pack_datetime(candidate["decisions"][0]["observed_at"], "decision_observed_at"),
            close.observed_at, event_start_at=boundary)
        close.validate()
    capture.validate()
    for material in (capture, close) if close is not None else (capture,):
        if any(getattr(material, name) != value for name, value in prices.items()):
            raise CaptureError("d1_capture_write_quote_mismatch")
    return capture, close


def _d1_capture_plan(conn, slot, packs, input_evidence, *, team: str, frozen=None):
    from polymarket_alpha_lab.papersim import store_psycopg
    from polymarket_alpha_lab.papersim.d1compare import journal, operations

    sources = _d1_book_sources(conn, slot)
    allowed_ids = slot.scope.get("candidate_ids")
    if (type(allowed_ids) is not list or any(type(cid) is not int or cid < 1 for cid in allowed_ids)
            or len(allowed_ids) != len(set(allowed_ids))):
        raise CaptureError("d1_capture_candidate_scope_invalid")
    allowed = set(allowed_ids)
    targets, materials = [], []
    reads = {}
    for pack_index, (pack, record) in enumerate(zip(packs, input_evidence, strict=True)):
        try:
            document = journal.decode_public_json(pack.content)
        except journal.JournalError as error:
            raise CaptureError("papersim_capture_pack_invalid", "not valid JSON") from error
        if type(document) is not list:
            raise CaptureError("papersim_capture_pack_invalid", "pack must be a JSON array")
        for observation_index, raw in enumerate(document):
            target = {
                "identity": {"candidate_id": None, "pack_sha256": record["snapshot_sha256"],
                             "pack_index": pack_index, "observation_index": observation_index},
                "candidate": None, "status": "REJECTED", "reason": None,
                "source": {"manifest_sha256": sources.manifest_sha256},
                "mark": None, "close": None,
            }
            pair = (None, None)
            try:
                if type(raw) is not dict:
                    raise CaptureError("papersim_capture_pack_invalid", "observations must be objects")
                target["claimed_prices"] = {}
                for name in _PRICE_FIELDS:
                    value = raw.get(name)
                    if type(value) is Decimal:
                        value = str(value)
                    elif type(value) not in (str, bool, int, type(None)):
                        value = {"invalid_type": type(value).__name__}
                    target["claimed_prices"][name] = value
                observation = _observation_from_mapping({key: value for key, value in raw.items()
                                                         if key not in _PRICE_FIELDS})
                target.update(observation=operations.canonical_document(asdict(observation)),
                              requested_sampling_kind=observation.sampling_kind)
                reference = (observation.scan_id, observation.scan_operation_key, observation.candidate_key)
                if frozen is not None:
                    if len(targets) >= len(frozen):
                        raise CaptureError("d1_capture_prepared_plan_mismatch")
                    candidate = frozen[len(targets)]["candidate"]
                else:
                    if reference not in reads:
                        reads[reference] = operations.canonical_document(
                            store_psycopg.d1_candidate_capture_target(
                                conn, scan_id=observation.scan_id, scan_operation_key=observation.scan_operation_key,
                                candidate_key=observation.candidate_key))
                    candidate = reads[reference]
                target["candidate"] = candidate
                if candidate is not None:
                    target["identity"]["candidate_id"] = candidate["candidate_id"]
                pair = _d1_capture_materials(conn, sources, target, raw, observation, team, allowed)
                mark, close = pair
                if mark is not None:
                    target.update(status="READY", reason=None, mark=operations.canonical_document(mark.comparison()),
                                  close=None if close is None else operations.canonical_document(asdict(close)))
            except (CaptureError, store.PapersimStoreError) as error:
                target.update(status="REJECTED", reason=error.code)
                if isinstance(error, CaptureError) and error.detail:
                    target["reason_detail"] = error.detail
            targets.append(target)
            materials.append(pair)
    plan = operations.canonical_document({
        "capture_manifest_sha256": sources.manifest_sha256,
        "capture_receipt": sources.receipt, "decision_as_of": sources.cutoff,
        "freshness_as_of": sources.freshness_as_of, "targets": targets,
    })
    return plan, materials


def _d1_capture_summary(config: CaptureConfig, code_commit: str, input_evidence, targets, items) -> dict:
    source_kinds: dict[str, int] = {}
    downgrades = {DOWNGRADE_MISSING_EVENT_START: 0, DOWNGRADE_OBSERVED_AT_AFTER_EVENT_START: 0,
                  _DOWNGRADE_LATE_RECEIPT: 0}
    for target in targets:
        observation = target.get("observation")
        if observation is not None:
            source = observation["source_kind"]
            source_kinds[source] = source_kinds.get(source, 0) + 1
        if target["status"] == "READY" and target.get("downgrade") is not None:
            downgrades[target["downgrade"]] += 1
    return {
        "command": CAPTURE_COMMAND, "team": config.team, "operator": config.operator, "code_commit": code_commit,
        "packs": [{"pack": item["pack"], "snapshot_sha256": item["snapshot_sha256"]} for item in input_evidence],
        "candidates_seen": len({target["identity"]["candidate_id"] for target in targets
                                if target["candidate"] is not None}),
        "observations": len(targets),
        "marks_written": sum(item["candidate_mark_id"] is not None for item in items),
        "close_eligible": sum(target["close"] is not None for target in targets),
        "close_evidence_written": sum(item["close_evidence_id"] is not None for item in items),
        "downgraded_to_periodic": sum(downgrades.values()), "downgrade_reasons": downgrades,
        "missing_event_start": len({target["identity"]["candidate_id"] for target in targets
                                    if target["candidate"] is not None
                                    and target["candidate"]["event_start_at"] is None}),
        "missing_quote": sum(item["status"] == "MISSING_QUOTE" for item in items),
        "source_kind_counts": source_kinds, "coverage_terms": COVERAGE_TERMS,
        "items": [{"identity": target["identity"], **item} for target, item in zip(targets, items, strict=True)],
    }


def _run_d1_capture(config: CaptureConfig, packs: Sequence[MarkPack], *, conn, repo_root,
                    now_utc: Callable, operation_key: str, attempt: int, code_commit: str) -> dict:
    from polymarket_alpha_lab.papersim import snapshots, store_psycopg
    from polymarket_alpha_lab.papersim.d1compare import operations

    configuration = {"team": config.team, "operator": config.operator, "packs": list(config.packs)}
    config_digest = snapshots.snapshot_content_sha256(operations.document_bytes(configuration))
    input_evidence = [{"pack": pack.name, "snapshot_sha256": snapshots.snapshot_content_sha256(pack.content),
                       "byte_length": len(pack.content)} for pack in packs]
    with operations.open_operation(
            conn, repo_root=repo_root, operation_key=operation_key, attempt=attempt, command=CAPTURE_COMMAND,
            team=config.team, code_commit=code_commit, config_digest=config_digest,
            input_evidence=input_evidence) as operation:
        if operation.completed_summary is not None:
            return operation.completed_summary
        materials = None

        def build() -> dict:
            nonlocal materials
            plan, materials = _d1_capture_plan(conn, operation.slot, packs, input_evidence, team=config.team)
            return plan

        registrations = [snapshots.SnapshotRegistration(
            content=pack.content, kind=CAPTURE_PACK_SNAPSHOT_KIND, source=pack.name, fetched_at=now_utc())
            for pack in packs]
        plan = operation.prepare(build, snapshots=registrations)
        targets = plan["targets"]
        if materials is None:
            with store_psycopg.d1_read_view(conn):
                rebuilt, materials = _d1_capture_plan(
                    conn, operation.slot, packs, input_evidence, team=config.team, frozen=targets)
            if rebuilt != plan:
                raise CaptureError("d1_capture_prepared_plan_mismatch")
        items = [operation.capture_item(target, capture=mark, close_evidence=close)
                 for target, (mark, close) in zip(targets, materials, strict=True)]
        return operation.finish(_d1_capture_summary(config, code_commit, input_evidence, targets, items))


# ---------------------------------------------------------------------------
# The stateful driver
# ---------------------------------------------------------------------------

def run_capture(config: CaptureConfig, packs: Sequence[MarkPack], *,
                conn,
                repo_root,
                now_utc: Callable[[], datetime.datetime],
                operation_key: str | None = None,
                attempt: int = 1,
                git_bin: str = "git") -> dict:
    """Run one complete mark capture against the store (receipts + writes).

    Order: clean-worktree commit check (FX-03, refuses before ANY write),
    STARTED receipt, raw pack bytes registered in ``papersim.snapshots``
    BEFORE normalization, candidate resolution, FX-34 classification and
    per-observation ``mark_capture_upsert`` writes, SUCCEEDED receipt
    carrying the stable coverage summary. A failure appends a FAILED
    receipt and re-raises. The returned summary is a pure function of the
    pack bytes and the immutable stored candidates, so an identical re-run
    returns an identical summary while inserting zero rows.
    """
    from polymarket_alpha_lab.papersim import snapshots, store_psycopg
    from polymarket_alpha_lab.papersim.d1compare import operations

    config.validate()
    code_commit = require_clean_worktree(repo_root, git_bin=git_bin)
    if operations.is_d1_operation_key(operation_key):
        return _run_d1_capture(config, packs, conn=conn, repo_root=repo_root, now_utc=now_utc,
                               operation_key=operation_key, attempt=attempt, code_commit=code_commit)
    started_at = now_utc()
    receipt_key = operation_key or (
        f"{CAPTURE_COMMAND}|{config.team}|{started_at.astimezone(datetime.UTC):%Y%m%dT%H%M%SZ}|b2"
    )

    def _receipt(phase: str, *, error_code: str | None = None,
                 output_evidence: Sequence[str] = ()) -> None:
        store_psycopg.record_command_run_event(conn, store.CommandRunEventSubmission(
            operation_key=receipt_key,
            attempt=attempt,
            phase=phase,
            command=CAPTURE_COMMAND,
            occurred_at=now_utc(),
            code_commit=code_commit,
            output_evidence=list(output_evidence),
            error_code=error_code,
        ))

    _receipt("STARTED")
    try:
        # 1. Raw pack bytes FIRST (FX-12: register before normalization).
        pack_provenance: list[dict] = []
        prepared: list[tuple[MarkObservation, str]] = []
        for pack in packs:
            registration = snapshots.register_snapshot(
                conn, snapshots.SnapshotRegistration(
                    content=pack.content,
                    kind=CAPTURE_PACK_SNAPSHOT_KIND,
                    source=pack.name,
                    fetched_at=now_utc()))
            digest = registration["content_sha256"]
            pack_provenance.append({"pack": pack.name, "snapshot_sha256": digest})
            for observation in observations_from_pack(pack.content):
                prepared.append((observation, digest))

        # 2. Resolve every referenced candidate once (FX-26 event identity).
        targets: dict[tuple[str, str], dict] = {}
        for observation, _digest in prepared:
            ref = (str(observation.scan_id if observation.scan_id is not None
                       else observation.scan_operation_key),
                   observation.candidate_key)
            if ref in targets:
                continue
            target = store_psycopg.candidate_capture_target(
                conn,
                scan_id=observation.scan_id,
                scan_operation_key=observation.scan_operation_key,
                candidate_key=observation.candidate_key)
            if target is None:
                raise CaptureError("papersim_capture_candidate_missing",
                                   f"{observation.candidate_key}@{ref[0]}")
            targets[ref] = target

        # 3. Classify (FX-34) and write; counts are content-derived only.
        source_kind_counts: dict[str, int] = {}
        downgrade_reasons = {DOWNGRADE_MISSING_EVENT_START: 0,
                             DOWNGRADE_OBSERVED_AT_AFTER_EVENT_START: 0}
        marks_written = 0
        close_eligible = 0
        close_evidence_written = 0
        missing_quote = 0
        for observation, digest in prepared:
            ref = (str(observation.scan_id if observation.scan_id is not None
                       else observation.scan_operation_key),
                   observation.candidate_key)
            target = targets[ref]
            source_kind_counts[observation.source_kind] = \
                source_kind_counts.get(observation.source_kind, 0) + 1
            if not has_any_price(observation):
                missing_quote += 1  # explicit missing count, never a silent skip
                continue
            stored_kind, downgrade = classify_observation(
                observation.sampling_kind, observation.observed_at,
                target["event_start_at"])
            if downgrade is not None:
                downgrade_reasons[downgrade] += 1
            capture = capture_from_observation(
                observation, candidate_id=target["candidate_id"],
                sampling_kind=stored_kind, pack_snapshot_sha256=digest)
            close_evidence = None
            if stored_kind == "CLOSE":
                close_eligible += 1
                close_evidence = close_evidence_from_observation(
                    observation, pack_snapshot_sha256=digest)
            written = store_psycopg.mark_capture_upsert(conn, capture,
                                                        close_evidence=close_evidence)
            # "written" counts rows ENSURED by this run (inserted or already
            # present under the same key with identical content): stable
            # across re-runs by design, unlike a first-attempt insert flag.
            marks_written += 1
            if close_evidence is not None and written["close_evidence_id"] is not None:
                close_evidence_written += 1

        summary = {
            "command": CAPTURE_COMMAND,
            "team": config.team,
            "operator": config.operator,
            "code_commit": code_commit,
            "packs": pack_provenance,
            # Distinct RESOLVED candidates (a pack may address one
            # candidate through both reference styles).
            "candidates_seen": len({target["candidate_id"]
                                    for target in targets.values()}),
            "observations": len(prepared),
            "marks_written": marks_written,
            "close_eligible": close_eligible,
            "close_evidence_written": close_evidence_written,
            "downgraded_to_periodic": sum(downgrade_reasons.values()),
            "downgrade_reasons": downgrade_reasons,
            "missing_event_start": len({
                target["candidate_id"] for target in targets.values()
                if target["event_start_at"] is None}),
            "missing_quote": missing_quote,
            "source_kind_counts": source_kind_counts,
            "coverage_terms": COVERAGE_TERMS,
        }
        # The SUCCEEDED receipt stays idempotent under its scheduling key:
        # the evidence carries the stable summary only.
        _receipt("SUCCEEDED", output_evidence=(
            json.dumps(summary, sort_keys=True, separators=(",", ":")),))
        return summary
    except Exception as error:  # noqa: BLE001 - the receipt is the record
        try:
            _receipt("FAILED", error_code=str(getattr(error, "code", type(error).__name__)))
        finally:
            raise
