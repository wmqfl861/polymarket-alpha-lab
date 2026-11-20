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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
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

    config.validate()
    code_commit = require_clean_worktree(repo_root, git_bin=git_bin)
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
