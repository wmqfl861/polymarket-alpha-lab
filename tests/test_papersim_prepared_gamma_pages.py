"""Prepared Gamma pagination retains the existing public fetch limits and scope replay."""

from datetime import timedelta
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from polymarket_alpha_lab.papersim import prepare, prepare_inputs
from polymarket_alpha_lab.papersim.adapters import contracts, polymarket
from polymarket_alpha_lab.papersim.d1compare import acquire
from tests.test_papersim_adapter_polymarket import _response
from tests.test_papersim_d1compare_journal import STAMP


def _pages(monkeypatch, page_size, events, **budget_limits):
    calls = []

    class Pages:
        def open(self, url, *, timeout_seconds):
            query = parse_qs(urlsplit(url).query)
            size, offset = int(query["limit"][0]), int(query["offset"][0])
            calls.append((size, offset))
            return _response(json.dumps(events[offset:offset + size]).encode(), url=url)

    monkeypatch.setattr(acquire, "_now_utc", lambda: STAMP)
    monkeypatch.setattr(polymarket, "BoundedPublicOpener", Pages)
    transport = acquire._Transport(STAMP - timedelta(seconds=1), STAMP + timedelta(minutes=1))
    budget = contracts.FetchBudget(max_response_bytes=256, **budget_limits)
    discovery, _ = acquire._discovery("bundesliga", budget, transport, page_size=page_size)
    return discovery, calls


def test_small_pages_resolve_an_oversized_aggregate_without_a_retry(monkeypatch):
    events = [{"markets": [], "padding": "x" * 90} for _ in range(3)]
    default, old_calls = _pages(monkeypatch, polymarket.GAMMA_EVENTS_PAGE_LIMIT, events)
    assert old_calls == [(polymarket.GAMMA_EVENTS_PAGE_LIMIT, 0)]
    assert default["reason_code"] == "public_response_too_large"
    assert not default["pagination"]["coverage_complete"]

    small, calls = _pages(monkeypatch, 1, events)
    assert calls == [(1, 0), (1, 1), (1, 2), (1, 3)]
    assert small["pagination"]["stopped_reason"] == "last_page"
    assert small["pagination"]["coverage_complete"]

    still_oversized, calls = _pages(monkeypatch, 1, [{"markets": [], "padding": "x" * 500}])
    assert calls == [(1, 0)]
    assert still_oversized["reason_code"] == "public_response_too_large"
    assert not still_oversized["pagination"]["coverage_complete"]


def test_small_pages_exhaust_the_existing_request_and_page_caps(monkeypatch):
    events = [{"markets": []}] * (polymarket.DISCOVERY_MAX_PAGES + 1)
    limited, calls = _pages(monkeypatch, 1, events, max_requests=2)
    assert calls == [(1, 0), (1, 1)]
    assert limited["pagination"]["stopped_reason"] == "budget_exhausted"
    assert not limited["pagination"]["coverage_complete"]
    capped, calls = _pages(monkeypatch, 1, events)
    assert len(calls) == polymarket.DISCOVERY_MAX_PAGES
    assert capped["pagination"]["stopped_reason"] == "page_cap"
    assert not capped["pagination"]["coverage_complete"]


def test_page_size_pins_the_prepared_scope_and_preserves_one_raw_window_key():
    args = (STAMP, STAMP + timedelta(minutes=5), ["bundesliga"])
    old_scope = acquire.discovery_scope(*args)
    assert old_scope == acquire.discovery_scope(*args, gamma_page_size=polymarket.GAMMA_EVENTS_PAGE_LIMIT)
    assert set(old_scope) == {"window_start", "window_end", "tag_slugs"}
    smaller = acquire.discovery_scope(*args, gamma_page_size=3)
    assert smaller == {**old_scope, "gamma_page_size": 3}
    assert acquire.discovery_operation_key(old_scope) == (
        f"{acquire._OPERATION_PREFIX}:{acquire._digest(acquire._canonical(old_scope))}")
    assert acquire.discovery_operation_key(smaller) == acquire.discovery_operation_key(old_scope)
    assert prepare_inputs.input_set_operation_key("c" * 64, old_scope) != (
        prepare_inputs.input_set_operation_key("c" * 64, smaller))


@pytest.mark.parametrize("size", [0, -1, True, "1", 2.5, polymarket.GAMMA_EVENTS_PAGE_LIMIT + 1])
def test_invalid_page_size_fails_before_any_capture(size):
    with pytest.raises(acquire.AcquisitionError, match="d1_capture_gamma_page_size_invalid"):
        acquire.discovery_scope(STAMP, STAMP + timedelta(minutes=1), ["bundesliga"], gamma_page_size=size)


def test_cli_only_accepts_page_size_with_preparation(capsys):
    from tools import d1_compare

    result = d1_compare.main(["capture-inputs", "--tag-slug", "bundesliga", "--gamma-page-size", "2",
                              "--window-start", STAMP.isoformat(),
                              "--window-end", (STAMP + timedelta(minutes=1)).isoformat()])
    assert result != 0
    assert "d1_capture_gamma_page_size_requires_preparation" in capsys.readouterr().err


@pytest.mark.parametrize(("limit", "offset"), [("50", "0"), ("3", "1"), ("3", "3")])
def test_reader_checks_request_size_and_exact_first_offset(limit, offset):
    row = dict.fromkeys(prepare_inputs._REQUEST_FIELDS)
    row.update(source_kind=polymarket.SOURCE_KIND_GAMMA, request_meta={"query": {
        "tag_slug": "bundesliga", "closed": "false", "limit": limit, "offset": offset}})
    view = SimpleNamespace(manifest={"scope": {"gamma_page_size": 3}, "requests": [row]})
    with pytest.raises(prepare.PreparationError, match="papersim_prepare_capture_request_invalid"):
        prepare_inputs._verify_capture_requests(None, view, STAMP, ["bundesliga"])
