"""A0 tests: shared adapter contracts for budgets, evidence and failure states.

Pins (M2 plan section 2.1, FX-15 A0):
- one shared ``FetchBudget`` instance tracks requests and accumulated
  response bytes for every adapter of a round; caps are immutable and
  there is no reset or top-up path an adapter could hand itself;
- exhaustion is fail-closed with fixed machine-readable reason codes;
  exception ``str()`` is the code only - never URLs, query parameters,
  headers or response bodies;
- response-size caps stay inside ``public_http.read_public_body`` limits:
  2 MiB default, 4 MiB hard ceiling, cross-checked against the real
  framing reader rather than only this module's own constants;
- the network permission gate refuses before any connection unless
  ``allow_public_fetch`` is exactly ``True`` (paper-only default False,
  exact-bool so ``1``/``"true"``/``None`` cannot pose as permission);
- ``AdapterStatus`` is a closed vocabulary: success (including a
  genuinely empty result set), insufficient data, unsupported mode,
  upstream error, budget exhausted, deadline exceeded, network denied;
  deadline exhaustion stays distinct from request/byte exhaustion
  because the M2 CLI pins a separate timeout exit code on it;
- ``FetchEvidence`` digests the exact raw bytes with SHA-256, keeps the
  local fetch time separate from the upstream-observed time (unknown
  upstream time stays ``None``, never faked), and holds either raw
  bytes or an already-registered snapshot key - never neither.

All tests are offline and synthetic: no network, no database, no writes.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
import hashlib
from http.client import HTTPResponse
from io import BytesIO
import subprocess
import sys
import time

import pytest

from polymarket_alpha_lab import public_http
from polymarket_alpha_lab.papersim.adapters import contracts


def _budget(**overrides: object) -> contracts.FetchBudget:
    """One valid budget, with per-test overrides applied last."""
    base: dict[str, object] = {
        "max_requests": 3,
        "max_response_bytes": 64,
        "deadline_monotonic": time.monotonic() + 60.0,
        "request_timeout_seconds": 5.0,
    }
    base.update(overrides)
    return contracts.FetchBudget(**base)


def _evidence_kwargs(**overrides: object) -> dict[str, object]:
    """Keyword arguments for a valid FetchEvidence, overridden per test."""
    raw = overrides.pop("raw_bytes", b"{}")
    if type(raw) is bytes:
        digest = hashlib.sha256(raw).hexdigest()
    else:
        digest = hashlib.sha256(b"{}").hexdigest()
    kwargs: dict[str, object] = {
        "source_kind": "polymarket-gamma",
        "source_reference": "https://gamma-api.polymarket.com/fixture",
        "fetched_at": datetime.now(UTC),
        "source_observed_at": None,
        "raw_bytes": raw,
        "content_sha256": digest,
        "snapshot_registry_key": None,
    }
    kwargs.update(overrides)
    return kwargs


def _evidence(raw: bytes = b"{}") -> contracts.FetchEvidence:
    return contracts.FetchEvidence(**_evidence_kwargs(raw_bytes=raw))


class _Socket:
    """Minimal socket double feeding wire bytes to stdlib HTTPResponse."""

    def __init__(self, wire: bytes) -> None:
        self.wire = wire

    def makefile(self, *args: object) -> BytesIO:
        return BytesIO(self.wire)


def _framed_response(body: bytes = b"{}") -> HTTPResponse:
    """Real stdlib HTTPResponse over in-memory wire bytes; no network."""
    headers = b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
    wire = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        + headers + b"\r\n" + body
    )
    response = HTTPResponse(_Socket(wire))
    response.begin()
    return response


# ---------------------------------------------------------------------------
# module boundary
# ---------------------------------------------------------------------------


def test_module_keeps_paper_only_boundary_flags():
    assert contracts.PAPER_ONLY is True
    assert contracts.REPORT_ONLY is True
    assert contracts.READONLY is True


def test_contracts_import_brings_no_psycopg_into_a_fresh_interpreter():
    code = (
        "import sys\n"
        "import polymarket_alpha_lab.papersim.adapters.contracts\n"
        "leaked = [name for name in sys.modules if name.startswith('psycopg')]\n"
        "assert not leaked, leaked\n"
        "print('psycopg-free')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "psycopg-free"


def test_contract_public_surface_is_frozen():
    assert set(contracts.__all__) == {
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
    }


def test_adapters_package_reexports_the_contract_surface():
    from polymarket_alpha_lab.papersim.adapters import AdapterResult, FetchBudget

    assert FetchBudget is contracts.FetchBudget
    assert AdapterResult is contracts.AdapterResult


def test_adapter_names_are_not_reexported_by_the_papersim_package():
    from polymarket_alpha_lab import papersim

    for name in contracts.__all__:
        assert getattr(papersim, name, None) is None, name


# ---------------------------------------------------------------------------
# response-size caps vs public_http.read_public_body
# ---------------------------------------------------------------------------


def test_response_caps_match_public_http_framing_reader_limits():
    assert contracts.DEFAULT_MAX_RESPONSE_BYTES == 2 * 1024 * 1024
    assert contracts.HARD_MAX_RESPONSE_BYTES == 4 * 1024 * 1024
    with _framed_response(b"{}") as response:
        assert (
            public_http.read_public_body(response, contracts.HARD_MAX_RESPONSE_BYTES)
            == b"{}"
        )
    with pytest.raises(ValueError, match="public_body_limit_invalid"):
        public_http.read_public_body(
            _framed_response(b"{}"), contracts.HARD_MAX_RESPONSE_BYTES + 1
        )
    budget = _budget(max_response_bytes=contracts.HARD_MAX_RESPONSE_BYTES)
    assert budget.max_response_bytes == contracts.HARD_MAX_RESPONSE_BYTES


def test_budget_rejects_response_cap_above_the_hard_ceiling():
    with pytest.raises(ValueError, match="max_response_bytes_invalid"):
        _budget(max_response_bytes=contracts.HARD_MAX_RESPONSE_BYTES + 1)


# ---------------------------------------------------------------------------
# FetchBudget construction and defaults
# ---------------------------------------------------------------------------


def test_budget_defaults_are_the_first_version_run_limits():
    budget = contracts.FetchBudget()
    assert contracts.FIRST_VERSION_MAX_MARKETS_PER_ROUND == 100
    assert budget.max_requests == contracts.FIRST_VERSION_MAX_REQUESTS_PER_ROUND == 500
    assert (
        budget.max_response_bytes
        == contracts.DEFAULT_MAX_RESPONSE_BYTES
        == 2 * 1024 * 1024
    )
    assert (
        budget.max_total_response_bytes
        == contracts.DEFAULT_MAX_TOTAL_RESPONSE_BYTES
        == 500 * 2 * 1024 * 1024
    )
    assert contracts.FIRST_VERSION_ROUND_DEADLINE_SECONDS == 900.0
    assert budget.request_timeout_seconds == 15.0
    assert budget.deadline_monotonic is not None
    assert budget.deadline_monotonic > time.monotonic()


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"max_requests": 0}, "max_requests_invalid"),
        ({"max_requests": -1}, "max_requests_invalid"),
        ({"max_requests": True}, "max_requests_invalid"),
        ({"max_requests": 2.5}, "max_requests_invalid"),
        ({"max_requests": "5"}, "max_requests_invalid"),
        ({"max_response_bytes": 0}, "max_response_bytes_invalid"),
        ({"max_response_bytes": -64}, "max_response_bytes_invalid"),
        ({"max_response_bytes": True}, "max_response_bytes_invalid"),
        ({"max_response_bytes": 1024.0}, "max_response_bytes_invalid"),
        ({"max_total_response_bytes": 0}, "max_total_response_bytes_invalid"),
        ({"max_total_response_bytes": 32}, "max_total_response_bytes_invalid"),
        ({"max_total_response_bytes": True}, "max_total_response_bytes_invalid"),
        ({"deadline_monotonic": float("nan")}, "deadline_monotonic_invalid"),
        ({"deadline_monotonic": float("inf")}, "deadline_monotonic_invalid"),
        ({"deadline_monotonic": "soon"}, "deadline_monotonic_invalid"),
        ({"deadline_monotonic": True}, "deadline_monotonic_invalid"),
        ({"request_timeout_seconds": 0}, "request_timeout_seconds_invalid"),
        ({"request_timeout_seconds": -5.0}, "request_timeout_seconds_invalid"),
        ({"request_timeout_seconds": float("nan")}, "request_timeout_seconds_invalid"),
        ({"request_timeout_seconds": float("inf")}, "request_timeout_seconds_invalid"),
    ],
)
def test_budget_rejects_invalid_configuration(overrides, code):
    with pytest.raises(ValueError, match=code):
        _budget(**overrides)


# ---------------------------------------------------------------------------
# shared-budget tracking and exhaustion
# ---------------------------------------------------------------------------


def test_budget_tracks_requests_on_one_shared_instance():
    budget = _budget(max_requests=3)
    assert budget.requests_used == 0
    assert budget.remaining_requests() == 3
    budget.charge_request()  # adapter A: first actual request
    budget.charge_request()  # adapter B, same round budget: pagination step
    assert budget.requests_used == 2
    assert budget.remaining_requests() == 1


def test_requests_exhaustion_is_fail_closed():
    budget = _budget(max_requests=1)
    budget.charge_request()
    with pytest.raises(contracts.BudgetExhaustedError) as excinfo:
        budget.charge_request()
    assert str(excinfo.value) == contracts.REASON_REQUESTS_EXHAUSTED
    assert excinfo.value.reason_code == "requests_exhausted"
    assert budget.requests_used == 1


def test_budget_accumulates_response_bytes_fail_closed():
    budget = _budget(max_requests=10, max_response_bytes=8, max_total_response_bytes=10)
    assert budget.next_response_read_limit() == 8
    budget.charge_response_bytes(6)
    assert budget.response_bytes_used == 6
    assert budget.remaining_response_bytes() == 4
    assert budget.next_response_read_limit() == 4
    with pytest.raises(contracts.BudgetExhaustedError) as excinfo:
        budget.charge_response_bytes(5)
    assert str(excinfo.value) == contracts.REASON_RESPONSE_BYTES_EXHAUSTED
    assert budget.response_bytes_used == 6  # no partial charge on failure
    with pytest.raises(ValueError, match="response_size_invalid"):
        budget.charge_response_bytes(-1)
    with pytest.raises(ValueError, match="response_size_invalid"):
        budget.charge_response_bytes(True)
    budget.charge_response_bytes(4)
    assert budget.remaining_response_bytes() == 0
    with pytest.raises(contracts.BudgetExhaustedError) as excinfo:
        budget.charge_request()
    assert str(excinfo.value) == contracts.REASON_RESPONSE_BYTES_EXHAUSTED


def test_next_response_read_limit_is_the_tighter_cap():
    budget = _budget(max_response_bytes=20, max_total_response_bytes=100)
    assert budget.next_response_read_limit() == 20
    budget.charge_response_bytes(90)
    assert budget.next_response_read_limit() == 10


def test_deadline_exhaustion_checked_before_serving_a_request():
    now = time.monotonic()
    budget = _budget(deadline_monotonic=now - 1.0, max_requests=5)
    assert budget.remaining_seconds(now) < 0
    with pytest.raises(contracts.BudgetExhaustedError) as excinfo:
        budget.charge_request(now=now)
    assert str(excinfo.value) == contracts.REASON_DEADLINE_EXCEEDED
    assert budget.requests_used == 0


def test_effective_timeout_is_clamped_by_remaining_deadline():
    now = time.monotonic()
    soon = _budget(deadline_monotonic=now + 5.0, request_timeout_seconds=30.0)
    assert soon.effective_timeout_seconds(now=now) == pytest.approx(5.0)
    later = _budget(deadline_monotonic=now + 300.0, request_timeout_seconds=30.0)
    assert later.effective_timeout_seconds(now=now) == pytest.approx(30.0)
    expired = _budget(deadline_monotonic=now - 1.0, request_timeout_seconds=30.0)
    with pytest.raises(contracts.BudgetExhaustedError):
        expired.effective_timeout_seconds(now=now)
    with pytest.raises(ValueError, match="now_invalid"):
        soon.effective_timeout_seconds(now=float("nan"))


def test_budget_caps_are_immutable_after_construction():
    budget = _budget()
    with pytest.raises(AttributeError, match="fetch_budget_field_frozen"):
        budget.max_requests = 10_000
    with pytest.raises(AttributeError, match="fetch_budget_field_frozen"):
        budget.max_total_response_bytes = 10 * 1024 * 1024


def test_budget_exposes_no_reset_or_top_up_path():
    budget = _budget()
    public_callables = [
        name
        for name in dir(budget)
        if not name.startswith("_") and callable(getattr(budget, name))
    ]
    forbidden = [
        name
        for name in public_callables
        if "reset" in name or "top_up" in name or "extend" in name
    ]
    assert forbidden == []


# ---------------------------------------------------------------------------
# explicit network permission gate
# ---------------------------------------------------------------------------


def test_permission_gate_defaults_to_deny_and_demands_exact_bool():
    assert contracts.DEFAULT_ALLOW_PUBLIC_FETCH is False
    assert contracts.require_public_fetch(True) is None
    for impostor in (1, 0, "true", None, 1.0):
        with pytest.raises(ValueError, match="allow_public_fetch_must_be_exact_bool"):
            contracts.require_public_fetch(impostor)


def test_permission_denial_raises_fixed_code_before_any_connection():
    with pytest.raises(contracts.FetchPermissionError) as excinfo:
        contracts.require_public_fetch(contracts.DEFAULT_ALLOW_PUBLIC_FETCH)
    assert str(excinfo.value) == contracts.REASON_PUBLIC_FETCH_NOT_ALLOWED
    assert excinfo.value.reason_code == "public_fetch_not_allowed"


# ---------------------------------------------------------------------------
# AdapterResult and the closed status vocabulary
# ---------------------------------------------------------------------------


def test_adapter_status_vocabulary_is_closed():
    assert {status.name for status in contracts.AdapterStatus} == {
        "SUCCESS",
        "INSUFFICIENT_DATA",
        "UNSUPPORTED",
        "UPSTREAM_ERROR",
        "BUDGET_EXHAUSTED",
        "DEADLINE_EXCEEDED",
        "NETWORK_DENIED",
    }
    # Deadline exhaustion must stay distinguishable from request/byte
    # exhaustion: the M2 CLI pins a separate timeout exit code on it.
    assert (
        contracts.AdapterStatus.DEADLINE_EXCEEDED
        is not contracts.AdapterStatus.BUDGET_EXHAUSTED
    )


def test_success_result_with_empty_set_is_success():
    empty = contracts.AdapterResult.success(())
    assert empty.status is contracts.AdapterStatus.SUCCESS
    assert empty.is_success is True
    assert empty.value == ()
    assert empty.reason_code is None
    assert empty.evidence == ()
    loaded = contracts.AdapterResult.success(
        ({"market": "fixture"},), evidence=(_evidence(),)
    )
    assert loaded.is_success is True
    assert loaded.value == ({"market": "fixture"},)
    assert len(loaded.evidence) == 1


def test_failure_result_requires_failure_status_and_fixed_reason():
    denied = contracts.AdapterResult.failure(
        contracts.AdapterStatus.NETWORK_DENIED,
        contracts.REASON_PUBLIC_FETCH_NOT_ALLOWED,
    )
    assert denied.is_success is False
    assert denied.value is None
    assert denied.reason_code == "public_fetch_not_allowed"
    with pytest.raises(ValueError, match="failure_status_required"):
        contracts.AdapterResult.failure(
            contracts.AdapterStatus.SUCCESS, "not_a_failure"
        )


def test_result_rejects_wrong_payload_shapes():
    with pytest.raises(ValueError, match="status_invalid"):
        contracts.AdapterResult(
            value=(), evidence=(), status="success", reason_code=None
        )
    with pytest.raises(ValueError, match="evidence_invalid"):
        contracts.AdapterResult(
            value=(), evidence=[_evidence()], status=contracts.AdapterStatus.SUCCESS,
            reason_code=None,
        )
    with pytest.raises(ValueError, match="reason_code_invalid"):
        contracts.AdapterResult(
            value=(), evidence=(), status=contracts.AdapterStatus.SUCCESS,
            reason_code="late_code",
        )
    with pytest.raises(ValueError, match="value_not_allowed_on_failure"):
        contracts.AdapterResult(
            value=({"partial": True},), evidence=(),
            status=contracts.AdapterStatus.INSUFFICIENT_DATA,
            reason_code="candles_incomplete",
        )


# ---------------------------------------------------------------------------
# FetchEvidence
# ---------------------------------------------------------------------------


def test_evidence_sha256_matches_hashlib_over_exact_bytes():
    raw = b'{"markets":[]}'
    evidence = _evidence(raw)
    assert evidence.content_sha256 == hashlib.sha256(raw).hexdigest()
    assert evidence.raw_bytes == raw
    assert evidence.source_kind == "polymarket-gamma"
    assert contracts.sha256_hexdigest(b"abc") == hashlib.sha256(b"abc").hexdigest()


def test_evidence_rejects_digest_mismatch_and_bad_hex():
    with pytest.raises(ValueError, match="content_sha256_mismatch"):
        contracts.FetchEvidence(**_evidence_kwargs(content_sha256="0" * 64))
    with pytest.raises(ValueError, match="content_sha256_invalid"):
        contracts.FetchEvidence(
            **_evidence_kwargs(content_sha256=hashlib.sha256(b"{}").hexdigest().upper())
        )
    with pytest.raises(ValueError, match="content_sha256_invalid"):
        contracts.FetchEvidence(**_evidence_kwargs(content_sha256="not-hex"))
    with pytest.raises(ValueError, match="raw_bytes_invalid"):
        contracts.sha256_hexdigest('{"not": "bytes"}')


def test_evidence_holds_raw_bytes_or_snapshot_key_never_neither():
    with pytest.raises(ValueError, match="evidence_payload_missing"):
        contracts.FetchEvidence(
            **_evidence_kwargs(raw_bytes=None, snapshot_registry_key=None)
        )
    raw = b'{"fixture":true}'
    digest = hashlib.sha256(raw).hexdigest()
    registered = contracts.FetchEvidence(
        source_kind="polymarket-clob",
        source_reference="https://clob.polymarket.com/book",
        fetched_at=datetime.now(UTC),
        source_observed_at=None,
        raw_bytes=None,
        content_sha256=digest,
        snapshot_registry_key=f"papersim.snapshots/sha256/{digest}",
    )
    assert registered.raw_bytes is None
    assert registered.snapshot_registry_key == f"papersim.snapshots/sha256/{digest}"


def test_evidence_timestamps_must_be_aware_and_unknown_stays_none():
    with pytest.raises(ValueError, match="fetched_at_invalid"):
        contracts.FetchEvidence(**_evidence_kwargs(fetched_at=datetime(2026, 11, 15, 12)))
    with pytest.raises(ValueError, match="source_observed_at_invalid"):
        contracts.FetchEvidence(
            **_evidence_kwargs(source_observed_at=datetime(2026, 11, 15, 12))
        )
    upstream = datetime.now(UTC)
    evidence = contracts.FetchEvidence(**_evidence_kwargs(source_observed_at=upstream))
    assert evidence.source_observed_at == upstream
    assert _evidence().source_observed_at is None  # unknown stays unknown


def test_evidence_and_result_are_frozen_value_types():
    evidence = _evidence()
    with pytest.raises(dataclasses.FrozenInstanceError):
        evidence.raw_bytes = b"tampered"
    result = contracts.AdapterResult.success(())
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = contracts.AdapterStatus.UPSTREAM_ERROR
