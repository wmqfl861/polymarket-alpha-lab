"""A0 shared adapter contracts: evidence, budgets and failure vocabulary.

Single responsibility (M2 plan section 2.1, FX-15 A0): pure
data types plus one shared budget object that the four M2 data adapters
(A1 Polymarket, A2 BTC/ETH candles, A3 Understat, A4 FotMob/ESPN) use to
express bounded public reads. This module opens no sockets, no database
sessions, no cache files and no JSONL journals; adapters own transport,
and :func:`polymarket_alpha_lab.public_http.read_public_body` remains
the only single-read framing gate.

Contract decisions frozen here for A1-A4:

- Every network entry point takes ``allow_public_fetch: bool = False``
  and calls :func:`require_public_fetch` before building any request.
  The paper-only default is denial (:data:`DEFAULT_ALLOW_PUBLIC_FETCH`);
  tests inject ``True`` together with a fake transport, never real
  sockets.
- One :class:`FetchBudget` instance is shared by every adapter of a
  round. Each actual request, retry, pagination step and detail fetch
  charges the same totals; caps are immutable after construction and no
  reset or top-up path exists, so no adapter can hand itself a fresh
  budget. The budget is not thread-safe; M2 rounds are single-threaded
  by design.
- Response caps: per-response reads default to 2 MiB
  (:data:`DEFAULT_MAX_RESPONSE_BYTES`) and no configuration may exceed
  the 4 MiB hard ceiling that ``read_public_body`` already enforces
  (:data:`HARD_MAX_RESPONSE_BYTES`).
- Failures are machine-readable: exceptions and ``reason_code`` values
  are fixed short strings. Adapters must never embed URLs, query
  parameters, headers, upstream exception text or response bodies in
  them, and callers must not echo them to stdout.
- :class:`AdapterStatus` is a closed vocabulary. A genuinely empty
  result set is a SUCCESS, while insufficient data, unsupported mode,
  upstream error, budget exhaustion, deadline exhaustion and denied
  network are distinct non-success states; the M2 CLI pins a separate
  timeout exit code on deadline exhaustion.
- :class:`FetchEvidence` keeps the local fetch time separate from the
  upstream-observed time (a re-observation is not the first
  registration), digests exact raw bytes with SHA-256, and carries
  either the raw bytes or an already-registered snapshot key - never
  neither, and never both discarded.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import math
import re
import time

# Phase 1 boundary flags (project iron rule 3): these types are paper-only
# evidence and budget bookkeeping; they never authorize execution.
PAPER_ONLY = True
REPORT_ONLY = True
READONLY = True

# Response-size caps (M2 plan section 2.1): 2 MiB default per response;
# nothing may exceed public_http.read_public_body's hard 4 MiB ceiling.
DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
HARD_MAX_RESPONSE_BYTES = 4 * 1024 * 1024

# First-version run limits (M2 plan section 2.1): versioned operational
# limits, not strategy thresholds. D1 re-ratifies them against the real
# request amplification before they are written into runtime config.
FIRST_VERSION_MAX_MARKETS_PER_ROUND = 100
FIRST_VERSION_MAX_REQUESTS_PER_ROUND = 500
FIRST_VERSION_ROUND_DEADLINE_SECONDS = 900.0
DEFAULT_MAX_TOTAL_RESPONSE_BYTES = (
    FIRST_VERSION_MAX_REQUESTS_PER_ROUND * DEFAULT_MAX_RESPONSE_BYTES
)

# Default per-request socket timeout, mirroring the existing public Gamma
# reader's default; the shared deadline always clamps it further.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 15.0

# Paper-only default for every adapter entry point's ``allow_public_fetch``.
DEFAULT_ALLOW_PUBLIC_FETCH = False

# Fixed reason codes (closed vocabulary; never wrap URLs or bodies).
REASON_REQUESTS_EXHAUSTED = "requests_exhausted"
REASON_RESPONSE_BYTES_EXHAUSTED = "response_bytes_exhausted"
REASON_DEADLINE_EXCEEDED = "deadline_exceeded"
REASON_PUBLIC_FETCH_NOT_ALLOWED = "public_fetch_not_allowed"

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


class BudgetExhaustedError(RuntimeError):
    """The shared round budget can no longer serve another request.

    ``str()`` is the fixed reason code only. Adapters must never attach
    URLs, query parameters, headers, upstream exception text or response
    bodies, and callers must not echo them to stdout.
    """

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class FetchPermissionError(RuntimeError):
    """Public fetch attempted without explicit ``allow_public_fetch=True``.

    Raised before any connection is built. Fixed reason code only; never
    carries the endpoint, query parameters or authentication material.
    """

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def require_public_fetch(allow_public_fetch: bool) -> None:
    """Refuse network work before any connection unless explicitly allowed.

    The ``type`` check (not truthiness) means ``1``, ``"true"`` and
    ``None`` cannot pose as permission. Every adapter network entry
    point calls this first, before building a request.
    """
    if type(allow_public_fetch) is not bool:
        raise ValueError("allow_public_fetch_must_be_exact_bool")
    if allow_public_fetch is not True:
        raise FetchPermissionError(REASON_PUBLIC_FETCH_NOT_ALLOWED)


def sha256_hexdigest(raw_bytes: bytes) -> str:
    """SHA-256 over the exact raw response bytes; exact ``bytes`` only."""
    if type(raw_bytes) is not bytes:
        raise ValueError("raw_bytes_invalid")
    return hashlib.sha256(raw_bytes).hexdigest()


@dataclass(slots=True)
class FetchBudget:
    """One shared, fail-closed budget for every adapter of a round.

    Caps are frozen after construction; only the two usage counters
    move, and only forward, through :meth:`charge_request` and
    :meth:`charge_response_bytes`. There is deliberately no reset or
    top-up path: every actual request, retry, pagination step and
    detail fetch of the round charges this same object.
    """

    max_requests: int = FIRST_VERSION_MAX_REQUESTS_PER_ROUND
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    deadline_monotonic: float | None = None
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    max_total_response_bytes: int = DEFAULT_MAX_TOTAL_RESPONSE_BYTES

    _requests_used: int = field(default=0, init=False, repr=False)
    _response_bytes_used: int = field(default=0, init=False, repr=False)
    _initialized: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.max_requests) is not int or self.max_requests < 1:
            raise ValueError("max_requests_invalid")
        if (
            type(self.max_response_bytes) is not int
            or not 1 <= self.max_response_bytes <= HARD_MAX_RESPONSE_BYTES
        ):
            raise ValueError("max_response_bytes_invalid")
        if (
            type(self.max_total_response_bytes) is not int
            or self.max_total_response_bytes < self.max_response_bytes
        ):
            raise ValueError("max_total_response_bytes_invalid")
        if self.deadline_monotonic is None:
            self.deadline_monotonic = (
                time.monotonic() + FIRST_VERSION_ROUND_DEADLINE_SECONDS
            )
        if (
            type(self.deadline_monotonic) not in (int, float)
            or not math.isfinite(self.deadline_monotonic)
        ):
            raise ValueError("deadline_monotonic_invalid")
        if (
            type(self.request_timeout_seconds) not in (int, float)
            or not math.isfinite(self.request_timeout_seconds)
            or self.request_timeout_seconds <= 0
        ):
            raise ValueError("request_timeout_seconds_invalid")
        self._requests_used = 0
        self._response_bytes_used = 0
        self._initialized = True

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_initialized", False) is True and name not in (
            "_requests_used",
            "_response_bytes_used",
        ):
            raise AttributeError("fetch_budget_field_frozen")
        object.__setattr__(self, name, value)

    # -- inspection -----------------------------------------------------

    @property
    def requests_used(self) -> int:
        return self._requests_used

    @property
    def response_bytes_used(self) -> int:
        return self._response_bytes_used

    def remaining_requests(self) -> int:
        return self.max_requests - self._requests_used

    def remaining_response_bytes(self) -> int:
        return self.max_total_response_bytes - self._response_bytes_used

    def remaining_seconds(self, now: float | None = None) -> float:
        """Seconds until the round deadline; negative once expired."""
        return self.deadline_monotonic - self._now(now)

    def effective_timeout_seconds(self, now: float | None = None) -> float:
        """Socket timeout for the next request, clamped by the deadline.

        The result bounds one socket read only; it is not, and must not
        be presented as, the whole-round wall-clock limit - the Linux
        scheduling wrapper owns the hard process deadline.
        """
        remaining = self.remaining_seconds(now)
        if remaining <= 0:
            raise BudgetExhaustedError(REASON_DEADLINE_EXCEEDED)
        return min(float(self.request_timeout_seconds), remaining)

    def next_response_read_limit(self) -> int:
        """Per-read cap for the next ``read_public_body`` call.

        The tighter of the configured per-response cap and the remaining
        total response bytes, so a bounded read can never race past the
        shared budget and fail only after the wire bytes are consumed.
        """
        return min(self.max_response_bytes, self.remaining_response_bytes())

    # -- charging -------------------------------------------------------

    def charge_request(self, now: float | None = None) -> None:
        """Charge one actual request, retry, pagination step or fetch.

        Fail-closed order: the deadline first (never start another page
        after the round clock), then the remaining total response bytes
        (a read that cannot return any byte cannot be served), then the
        request count. Counters never move when a charge is refused.
        """
        current = self._now(now)
        if self.deadline_monotonic - current <= 0:
            raise BudgetExhaustedError(REASON_DEADLINE_EXCEEDED)
        if self._response_bytes_used >= self.max_total_response_bytes:
            raise BudgetExhaustedError(REASON_RESPONSE_BYTES_EXHAUSTED)
        if self._requests_used >= self.max_requests:
            raise BudgetExhaustedError(REASON_REQUESTS_EXHAUSTED)
        self._requests_used += 1

    def charge_response_bytes(self, size: int) -> None:
        """Charge the exact byte count of one response body just read.

        Refuses the whole charge (no partial accumulation) when it would
        exceed the shared total; the caller keeps the refusal evidence
        and stops instead of shrinking the snapshot.
        """
        if type(size) is not int or size < 0:
            raise ValueError("response_size_invalid")
        if self._response_bytes_used + size > self.max_total_response_bytes:
            raise BudgetExhaustedError(REASON_RESPONSE_BYTES_EXHAUSTED)
        self._response_bytes_used += size

    def _now(self, now: float | None) -> float:
        if now is None:
            return time.monotonic()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError("now_invalid")
        return float(now)


@dataclass(frozen=True, slots=True)
class FetchEvidence:
    """Provenance of one raw public read: source, times and digest.

    ``fetched_at`` is this observation's local time; it is recorded
    independently for every fetch and never copied from a previous
    registration of identical bytes. ``source_observed_at`` is the
    upstream timestamp when the source provides one and stays ``None``
    (unknown, not zero, not faked) when it does not. Exactly one of
    ``raw_bytes`` (fresh bytes the caller will register) and
    ``snapshot_registry_key`` (bytes already registered this round) must
    be present; ``content_sha256`` always pins the exact content.
    """

    source_kind: str
    source_reference: str
    fetched_at: datetime
    source_observed_at: datetime | None
    raw_bytes: bytes | None
    content_sha256: str
    snapshot_registry_key: str | None = None

    def __post_init__(self) -> None:
        if type(self.source_kind) is not str or not self.source_kind:
            raise ValueError("source_kind_invalid")
        if type(self.source_reference) is not str or not self.source_reference:
            raise ValueError("source_reference_invalid")
        if not isinstance(self.fetched_at, datetime) or self.fetched_at.tzinfo is None:
            raise ValueError("fetched_at_invalid")
        if self.source_observed_at is not None and (
            not isinstance(self.source_observed_at, datetime)
            or self.source_observed_at.tzinfo is None
        ):
            raise ValueError("source_observed_at_invalid")
        if self.raw_bytes is not None and type(self.raw_bytes) is not bytes:
            raise ValueError("raw_bytes_invalid")
        if (
            type(self.content_sha256) is not str
            or _SHA256_HEX.fullmatch(self.content_sha256) is None
        ):
            raise ValueError("content_sha256_invalid")
        if self.snapshot_registry_key is not None and (
            type(self.snapshot_registry_key) is not str
            or not self.snapshot_registry_key
        ):
            raise ValueError("snapshot_registry_key_invalid")
        if self.raw_bytes is None and self.snapshot_registry_key is None:
            raise ValueError("evidence_payload_missing")
        if self.raw_bytes is not None and self.content_sha256 != sha256_hexdigest(
            self.raw_bytes
        ):
            raise ValueError("content_sha256_mismatch")

    @classmethod
    def from_bytes(
        cls,
        *,
        source_kind: str,
        source_reference: str,
        fetched_at: datetime,
        raw_bytes: bytes,
        source_observed_at: datetime | None = None,
        snapshot_registry_key: str | None = None,
    ) -> "FetchEvidence":
        """Build evidence for fresh bytes, digesting them here."""
        return cls(
            source_kind=source_kind,
            source_reference=source_reference,
            fetched_at=fetched_at,
            source_observed_at=source_observed_at,
            raw_bytes=raw_bytes,
            content_sha256=sha256_hexdigest(raw_bytes),
            snapshot_registry_key=snapshot_registry_key,
        )


class AdapterStatus(str, Enum):
    """Closed result vocabulary shared by every M2 adapter.

    Value strings are stable identifiers for receipts and exit codes:
    SUCCESS (a genuinely empty result set included),
    INSUFFICIENT_DATA, UNSUPPORTED, UPSTREAM_ERROR, BUDGET_EXHAUSTED
    (requests/bytes), DEADLINE_EXCEEDED (its own state because the CLI
    pins a separate timeout exit code) and NETWORK_DENIED (fetch
    attempted without ``allow_public_fetch=True``).
    """

    SUCCESS = "success"
    INSUFFICIENT_DATA = "insufficient_data"
    UNSUPPORTED = "unsupported"
    UPSTREAM_ERROR = "upstream_error"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    NETWORK_DENIED = "network_denied"


@dataclass(frozen=True, slots=True)
class AdapterResult:
    """Normalized value plus raw evidence, or a classified failure.

    ``value`` is present only on SUCCESS - an empty tuple is a valid
    success (empty set), while failures never carry a partial value that
    could be mistaken for a usable snapshot; their evidence tuple keeps
    whatever raw observations exist. ``reason_code`` is a fixed short
    string from the adapter's closed vocabulary, never a URL, header or
    response body.
    """

    value: object
    evidence: tuple[FetchEvidence, ...]
    status: AdapterStatus
    reason_code: str | None

    def __post_init__(self) -> None:
        if type(self.evidence) is not tuple or any(
            type(item) is not FetchEvidence for item in self.evidence
        ):
            raise ValueError("evidence_invalid")
        if not isinstance(self.status, AdapterStatus):
            raise ValueError("status_invalid")
        if self.status is AdapterStatus.SUCCESS:
            if self.reason_code is not None:
                raise ValueError("reason_code_invalid")
        else:
            if type(self.reason_code) is not str or not self.reason_code:
                raise ValueError("reason_code_invalid")
            if self.value is not None:
                raise ValueError("value_not_allowed_on_failure")

    @classmethod
    def success(
        cls,
        value: object = (),
        evidence: tuple[FetchEvidence, ...] = (),
    ) -> "AdapterResult":
        """Wrap a normalized payload; the empty set is a success."""
        return cls(
            value=value,
            evidence=tuple(evidence),
            status=AdapterStatus.SUCCESS,
            reason_code=None,
        )

    @classmethod
    def failure(
        cls,
        status: AdapterStatus,
        reason_code: str,
        evidence: tuple[FetchEvidence, ...] = (),
    ) -> "AdapterResult":
        """Wrap a classified failure; partial evidence is kept, value is not."""
        if not isinstance(status, AdapterStatus) or status is AdapterStatus.SUCCESS:
            raise ValueError("failure_status_required")
        return cls(
            value=None,
            evidence=tuple(evidence),
            status=status,
            reason_code=reason_code,
        )

    @property
    def is_success(self) -> bool:
        return self.status is AdapterStatus.SUCCESS


__all__ = (
    "AdapterResult",
    "AdapterStatus",
    "BudgetExhaustedError",
    "DEFAULT_ALLOW_PUBLIC_FETCH",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_MAX_TOTAL_RESPONSE_BYTES",
    "FetchBudget",
    "FetchEvidence",
    "FetchPermissionError",
    "FIRST_VERSION_MAX_MARKETS_PER_ROUND",
    "FIRST_VERSION_MAX_REQUESTS_PER_ROUND",
    "FIRST_VERSION_ROUND_DEADLINE_SECONDS",
    "HARD_MAX_RESPONSE_BYTES",
    "PAPER_ONLY",
    "READONLY",
    "REASON_DEADLINE_EXCEEDED",
    "REASON_PUBLIC_FETCH_NOT_ALLOWED",
    "REASON_REQUESTS_EXHAUSTED",
    "REASON_RESPONSE_BYTES_EXHAUSTED",
    "REPORT_ONLY",
    "require_public_fetch",
    "sha256_hexdigest",
)
