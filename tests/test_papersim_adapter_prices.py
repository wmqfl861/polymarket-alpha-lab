"""M2 A2 tests: bounded BTC/ETH candle and spot adapter (M2 plan 6.2).

Pins (M2 plan sections 2.3 and 6.2, A2 row):

- closed input vocabulary: only exact ``BTC``/``ETH`` assets and the
  explicit ``binance``/``coinbase``/``kraken`` sources; venue, pair and
  quote currency are bound per source (BTC-USD vs BTCUSDT series can
  never be mixed or cross-requested); unknown values are ``UNSUPPORTED``
  with fixed reason codes and zero network;
- unpermitted fetches are refused before any connection with zero
  transport calls;
- output contract: ascending, deduplicated, UTC-aligned complete
  six-hour bars with per-window annotations (actual bar count, gaps,
  excluded unfinished bars, source and fetch time); unfinished bars are
  excluded and counted; coverage shortfalls are ``INSUFFICIENT_DATA``,
  never a partial success value;
- Coinbase 48-bar window slicing into a finite, explicit sub-window
  sequence with verified complete coverage;
- Kraken has no 6h interval: bars are aggregated from complete hourly
  rows on UTC boundaries, the uncommitted final row is dropped, the
  recent-history limit is refused up front, and a recent-only response
  is never re-paged into fake full history;
- only complete bars reach the frozen ``crypto_model`` estimators:
  ``closes_for_vol_window`` refuses short histories so the frozen
  "slice what exists" compatibility behavior cannot masquerade as a
  complete 30/60/87-day window, and the frozen RMS/30d anchors
  reproduce bit-exactly from the synthetic fixture;
- request/byte/deadline budget exhaustion are three distinct states;
  truncation and framing errors are rejected with fixed codes;
- every response yields one ``FetchEvidence`` whose SHA-256 pins the
  exact served bytes; spot and candles are independent observations.

All tests are offline and synthetic: fixture bodies plus fake transport
and fake clocks. No network, no database, no writes.

Out-of-repo design/plan references (09D design v2 / M1 / M2 / M3 plans) are
original-missing unless a receipt is cited in-line; the reference map with
in-repo basis is docs/paper-sim/design/README.md (FX-28).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from http.client import HTTPResponse
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

from polymarket_alpha_lab.papersim.adapters import prices
from polymarket_alpha_lab.papersim.adapters.contracts import AdapterStatus
from polymarket_alpha_lab.papersim.crypto_model import (
    SIGNATURE_WINDOW,
    VOL_WINDOW_BARS,
    realized_vol_daily,
    realized_vol_windows,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "papersim" / "m2" / "prices.json").read_text(
        encoding="utf-8"
    )
)

T0 = 1000.0
NOW = datetime(2026, 11, 15, tzinfo=UTC)
BTC_START = datetime(2026, 10, 12, tzinfo=UTC)
KRAKEN_START = datetime(2026, 10, 31, 12, tzinfo=UTC)
BAR = 21600

BINANCE_ROWS = FIXTURE["responses"]["binance_btc_klines_30d"]
COINBASE_ROWS = FIXTURE["responses"]["coinbase_btc_candles_30d"]
KRAKEN_ENVELOPE = FIXTURE["responses"]["kraken_eth_ohlc_31_rows"]

BTC_CLOSES = [float(row[4]) for row in BINANCE_ROWS]


# ---------------------------------------------------------------------------
# fake transport: real stdlib HTTPResponse over in-memory wire bytes
# ---------------------------------------------------------------------------


class _Socket:
    def __init__(self, wire: bytes) -> None:
        self.wire = wire

    def makefile(self, *args: object, **kwargs: object) -> BytesIO:
        return BytesIO(self.wire)


def _response(
    url: str,
    body: bytes,
    *,
    status: int = 200,
    content_type: str = "application/json",
    declared_length: int | None | str = "auto",
    transfer_encoding: str | None = None,
    content_encoding: str | None = None,
    geturl: str | None = None,
) -> HTTPResponse:
    if declared_length == "auto":
        declared_length = len(body)
    lines = [f"HTTP/1.1 {status} {'OK' if status == 200 else 'Status'}"]
    if content_type is not None:
        lines.append(f"Content-Type: {content_type}")
    if declared_length is not None:
        lines.append(f"Content-Length: {declared_length}")
    if transfer_encoding is not None:
        lines.append(f"Transfer-Encoding: {transfer_encoding}")
    if content_encoding is not None:
        lines.append(f"Content-Encoding: {content_encoding}")
    wire = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body
    response = HTTPResponse(_Socket(wire))
    response.begin()
    response.url = url if geturl is None else geturl
    return response


class _Server:
    """Fake transport recording every request and every served body."""

    def __init__(self, handler) -> None:
        self._handler = handler
        self.calls: list[tuple[str, float]] = []
        self.served: list[tuple[str, bytes]] = []

    def urlopen(self, request, timeout: float):
        url = request.full_url
        self.calls.append((url, timeout))
        body, kwargs = self._handler(url)
        self.served.append((url, body))
        return _response(url, body, **kwargs)

    @property
    def urls(self) -> list[str]:
        return [url for url, _timeout in self.calls]


def _install(monkeypatch, handler) -> _Server:
    server = _Server(handler)
    monkeypatch.setattr(prices, "_urlopen", server.urlopen)
    return server


def _json_handler(payload, **response_kwargs):
    if isinstance(payload, bytes):
        body = payload
    else:
        body = json.dumps(payload).encode("utf-8")

    def handler(url: str):
        return body, response_kwargs

    return handler


def _install_clocks(monkeypatch, *, mono=T0, utc=NOW, utc_seq=None) -> None:
    monkeypatch.setattr(prices, "_monotonic", lambda: mono)
    if utc_seq is None:
        monkeypatch.setattr(prices, "_utcnow", lambda: utc)
    else:
        iterator = iter(utc_seq)

        def next_utc():
            return next(iterator)

        monkeypatch.setattr(prices, "_utcnow", next_utc)


def _budget(**overrides: object):
    base: dict[str, object] = {
        "max_requests": 10,
        "max_response_bytes": 1 << 20,
        "deadline_monotonic": T0 + 600.0,
        "request_timeout_seconds": 15.0,
    }
    base.update(overrides)
    return prices.FetchBudget(**base)


def _candles(asset, source, start, end, budget, *, bar_hours=6, allow=True):
    return prices.fetch_closed_candles(
        asset,
        source,
        start,
        end,
        bar_hours=bar_hours,
        budget=budget,
        allow_public_fetch=allow,
    )


def _spot(asset, source, budget, *, allow=True):
    return prices.fetch_spot(
        asset, source, budget=budget, allow_public_fetch=allow
    )


# source-aware fixture servers ------------------------------------------------


def _binance_server(monkeypatch, rows=BINANCE_ROWS, mutate=None) -> _Server:
    def handler(url: str):
        query = parse_qs(urlparse(url).query)
        start_ms = int(query["startTime"][0])
        end_ms = int(query["endTime"][0])
        selected = [row for row in rows if start_ms <= row[0] <= end_ms]
        if mutate is not None:
            selected = mutate(selected)
        return json.dumps(selected).encode("utf-8"), {}

    return _install(monkeypatch, handler)


def _coinbase_server(monkeypatch, rows=COINBASE_ROWS, mutate=None) -> _Server:
    def handler(url: str):
        query = parse_qs(urlparse(url).query)
        start = datetime.fromisoformat(query["start"][0])
        end = datetime.fromisoformat(query["end"][0])
        start_s = int(start.timestamp())
        end_s = int(end.timestamp())
        selected = [row for row in rows if start_s <= row[0] < end_s]
        if mutate is not None:
            selected = mutate(selected, start_s, end_s)
        return json.dumps(selected).encode("utf-8"), {}

    return _install(monkeypatch, handler)


def _kraken_server(monkeypatch, *, ignore_since=False, drop_hour=None) -> _Server:
    def handler(url: str):
        query = parse_qs(urlparse(url).query)
        since = int(query["since"][0])
        assert query["interval"][0] == "60"
        rows = list(KRAKEN_ENVELOPE["result"]["XETHZUSD"])
        if drop_hour is not None:
            rows = [
                row
                for index, row in enumerate(rows)
                if index != drop_hour
            ]
        if not ignore_since:
            kept = [row for row in rows if row[0] >= since]
        else:
            kept = rows
        payload = {
            "error": [],
            "result": {"XETHZUSD": kept, "last": KRAKEN_ENVELOPE["result"]["last"]},
        }
        return json.dumps(payload).encode("utf-8"), {}

    return _install(monkeypatch, handler)


# ---------------------------------------------------------------------------
# module boundary and surface
# ---------------------------------------------------------------------------


def test_module_boundary_flags():
    assert prices.PAPER_ONLY is True
    assert prices.REPORT_ONLY is True
    assert prices.READONLY is True


def test_import_brings_no_psycopg_into_a_fresh_interpreter():
    code = (
        "import sys\n"
        "import polymarket_alpha_lab.papersim.adapters.prices\n"
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


def test_closed_reason_codes_never_carry_url_material():
    assert type(prices.REASON_CODES) is frozenset
    for code in prices.REASON_CODES:
        assert isinstance(code, str)
        assert code == code.strip()
        assert all(ch.isalnum() or ch == "_" for ch in code), code
    for required in (
        "requests_exhausted",
        "response_bytes_exhausted",
        "deadline_exceeded",
        "public_fetch_not_allowed",
    ):
        assert required in prices.REASON_CODES


# ---------------------------------------------------------------------------
# input vocabulary and permission gate (zero network)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "asset", ["SOL", "sol", "BTC-USD", "ETH-USD", "BTCUSDT", "ETHUSDT", "XBT"]
)
def test_unknown_asset_is_unsupported_with_zero_network(monkeypatch, asset):
    server = _install(monkeypatch, _json_handler([]))
    _install_clocks(monkeypatch)
    result = _candles(asset, "binance", BTC_START, NOW, _budget())
    assert result.status is AdapterStatus.UNSUPPORTED
    assert result.reason_code == "unsupported_asset"
    assert result.value is None and result.evidence == ()
    assert server.calls == []
    spot = _spot(asset, "coinbase", _budget())
    assert spot.status is AdapterStatus.UNSUPPORTED
    assert spot.reason_code == "unsupported_asset"


@pytest.mark.parametrize("source", ["bitstamp", "Binance", "binance ", ""])
def test_unknown_source_is_unsupported_with_zero_network(monkeypatch, source):
    server = _install(monkeypatch, _json_handler([]))
    _install_clocks(monkeypatch)
    result = _candles("BTC", source, BTC_START, NOW, _budget())
    assert result.status is AdapterStatus.UNSUPPORTED
    assert result.reason_code == "unsupported_source"
    assert server.calls == []


def test_bar_hours_other_than_six_is_unsupported(monkeypatch):
    server = _install(monkeypatch, _json_handler([]))
    _install_clocks(monkeypatch)
    for bar_hours in (1, 4, 12, 24, 3600):
        result = _candles(
            "BTC", "binance", BTC_START, NOW, _budget(), bar_hours=bar_hours
        )
        assert result.status is AdapterStatus.UNSUPPORTED
        assert result.reason_code == "unsupported_bar_interval"
    assert server.calls == []


def test_invalid_window_bounds_are_unsupported(monkeypatch):
    server = _install(monkeypatch, _json_handler([]))
    _install_clocks(monkeypatch)
    start = datetime(2026, 10, 12, 3, tzinfo=UTC)
    end = datetime(2026, 10, 13, tzinfo=UTC)
    result = _candles("BTC", "binance", start, end, _budget())
    assert (result.status, result.reason_code) == (
        AdapterStatus.UNSUPPORTED,
        "window_bounds_invalid",
    )
    unaligned_end = _candles(
        "BTC",
        "binance",
        datetime(2026, 10, 12, tzinfo=UTC),
        datetime(2026, 10, 12, 5, tzinfo=UTC),
        _budget(),
    )
    assert (unaligned_end.status, unaligned_end.reason_code) == (
        AdapterStatus.UNSUPPORTED,
        "window_bounds_invalid",
    )
    empty_window = _candles(
        "BTC",
        "binance",
        datetime(2026, 10, 13, tzinfo=UTC),
        datetime(2026, 10, 13, tzinfo=UTC),
        _budget(),
    )
    assert (empty_window.status, empty_window.reason_code) == (
        AdapterStatus.UNSUPPORTED,
        "window_bounds_invalid",
    )
    assert server.calls == []


@pytest.mark.parametrize(
    "start,end",
    [
        (datetime(2026, 10, 12), datetime(2026, 10, 13)),
        (datetime(2026, 10, 12, tzinfo=UTC).timestamp(), datetime(2026, 10, 13, tzinfo=UTC).timestamp()),
        (None, datetime(2026, 10, 13, tzinfo=UTC)),
    ],
)
def test_naive_or_non_datetime_bounds_raise(monkeypatch, start, end):
    _install(monkeypatch, _json_handler([]))
    _install_clocks(monkeypatch)
    with pytest.raises(ValueError):
        _candles("BTC", "binance", start, end, _budget())


def test_type_violations_raise(monkeypatch):
    _install(monkeypatch, _json_handler([]))
    _install_clocks(monkeypatch)
    with pytest.raises(ValueError):
        _candles("BTC", "binance", BTC_START, NOW, budget=None)
    with pytest.raises(ValueError):
        _candles(1, "binance", BTC_START, NOW, _budget())
    with pytest.raises(ValueError):
        _candles("BTC", 2, BTC_START, NOW, _budget())
    with pytest.raises(ValueError):
        _candles("BTC", "binance", BTC_START, NOW, _budget(), bar_hours=6.0)
    with pytest.raises(ValueError):
        _spot("BTC", "binance", budget=None)


def test_unpermitted_fetch_is_denied_before_any_connection(monkeypatch):
    server = _install(monkeypatch, _json_handler([]))
    _install_clocks(monkeypatch)
    candles = _candles("BTC", "binance", BTC_START, NOW, _budget(), allow=False)
    assert candles.status is AdapterStatus.NETWORK_DENIED
    assert candles.reason_code == "public_fetch_not_allowed"
    spot = _spot("ETH", "kraken", _budget(), allow=False)
    assert spot.status is AdapterStatus.NETWORK_DENIED
    assert spot.reason_code == "public_fetch_not_allowed"
    assert server.calls == []


def test_permission_gate_precedes_validation(monkeypatch):
    _install(monkeypatch, _json_handler([]))
    _install_clocks(monkeypatch)
    result = _candles("SOL", "bitstamp", BTC_START, NOW, _budget(), allow=False)
    assert result.status is AdapterStatus.NETWORK_DENIED


@pytest.mark.parametrize("sneaky", [1, "true", None])
def test_non_bool_permission_is_rejected(monkeypatch, sneaky):
    _install(monkeypatch, _json_handler([]))
    _install_clocks(monkeypatch)
    with pytest.raises(ValueError):
        prices.fetch_closed_candles(
            "BTC",
            "binance",
            BTC_START,
            NOW,
            budget=_budget(),
            allow_public_fetch=sneaky,
        )
    with pytest.raises(ValueError):
        prices.fetch_spot(
            "BTC", "binance", budget=_budget(), allow_public_fetch=sneaky
        )


# ---------------------------------------------------------------------------
# complete six-hour bars, UTC alignment, dedup
# ---------------------------------------------------------------------------


def test_binance_full_30d_window_returns_complete_ascending_bars(monkeypatch):
    server = _binance_server(monkeypatch)
    _install_clocks(monkeypatch)
    end = BTC_START + timedelta(seconds=120 * BAR)
    result = _candles("BTC", "binance", BTC_START, end, _budget())
    assert result.is_success
    observation = result.value
    assert observation.asset == "BTC"
    assert (observation.venue, observation.pair, observation.currency) == (
        "binance",
        "BTCUSDT",
        "USDT",
    )
    assert observation.bar_hours == 6
    assert observation.actual_bar_count == observation.expected_bar_count == 120
    assert observation.gap_count == 0
    assert observation.duplicate_count == 0
    assert observation.excluded_unfinished_count == 0
    assert observation.excluded_out_of_window_count == 0
    assert observation.covered_start == BTC_START
    assert observation.covered_end == end
    assert observation.requested_start == BTC_START
    assert observation.requested_end == end
    assert observation.fetched_at.tzinfo is not None
    previous = None
    for bar in observation.bars:
        assert int(bar.open_time.timestamp()) % BAR == 0
        assert int(bar.close_time.timestamp()) - int(bar.open_time.timestamp()) == BAR
        assert bar.low <= min(bar.open, bar.close)
        assert bar.high >= max(bar.open, bar.close)
        if previous is not None:
            assert bar.open_time > previous
        previous = bar.open_time
    assert len(server.calls) == 1
    assert observation.closes == tuple(BTC_CLOSES)


def test_binance_month_boundary_window_is_utc_aligned(monkeypatch):
    _binance_server(monkeypatch)
    _install_clocks(monkeypatch)
    start = datetime(2026, 10, 31, 12, tzinfo=UTC)
    end = datetime(2026, 11, 1, 6, tzinfo=UTC)
    result = _candles("BTC", "binance", start, end, _budget())
    assert result.is_success
    observation = result.value
    assert observation.actual_bar_count == 3
    months = {bar.open_time.month for bar in observation.bars}
    assert months == {10, 11}
    days = {bar.open_time.day for bar in observation.bars}
    assert days == {31, 1}


def test_coinbase_96_bar_window_is_two_explicit_sub_windows(monkeypatch):
    server = _coinbase_server(monkeypatch)
    _install_clocks(
        monkeypatch,
        utc_seq=[NOW + timedelta(seconds=i) for i in range(6)],
    )
    start = BTC_START
    end = BTC_START + timedelta(seconds=96 * BAR)
    result = _candles("BTC", "coinbase", start, end, _budget())
    assert result.is_success
    observation = result.value
    assert (observation.venue, observation.pair, observation.currency) == (
        "coinbase",
        "BTC-USD",
        "USD",
    )
    assert observation.actual_bar_count == 96
    assert len(server.calls) == 2
    spans = []
    for url, _timeout in server.calls:
        query = parse_qs(urlparse(url).query)
        assert query["granularity"] == ["21600"]
        assert "/products/BTC-USD/candles" in url
        chunk_start = datetime.fromisoformat(query["start"][0])
        chunk_end = datetime.fromisoformat(query["end"][0])
        assert (int(chunk_end.timestamp()) - int(chunk_start.timestamp())) == 48 * BAR
        spans.append((chunk_start, chunk_end))
    assert spans[0][0] == start
    assert spans[0][1] == spans[1][0]
    assert spans[1][1] == end
    assert len(result.evidence) == 2
    fetched = [item.fetched_at for item in result.evidence]
    assert len(set(fetched)) == 2
    assert observation.fetched_at == fetched[0]


def test_coinbase_120_bar_window_slices_into_three_bounded_chunks(monkeypatch):
    server = _coinbase_server(monkeypatch)
    _install_clocks(monkeypatch)
    end = BTC_START + timedelta(seconds=120 * BAR)
    result = _candles("BTC", "coinbase", BTC_START, end, _budget())
    assert result.is_success
    assert result.value.actual_bar_count == 120
    assert len(server.calls) == 3
    bar_counts = []
    for url, _timeout in server.calls:
        query = parse_qs(urlparse(url).query)
        chunk_start = datetime.fromisoformat(query["start"][0])
        chunk_end = datetime.fromisoformat(query["end"][0])
        bar_counts.append(
            (int(chunk_end.timestamp()) - int(chunk_start.timestamp())) // BAR
        )
    assert bar_counts == [48, 48, 24]
    assert result.value.closes == tuple(BTC_CLOSES)


def test_identical_duplicate_rows_are_deduplicated_and_counted(monkeypatch):
    def mutate(selected):
        return selected + [selected[3]]

    _binance_server(monkeypatch, mutate=mutate)
    _install_clocks(monkeypatch)
    end = BTC_START + timedelta(seconds=120 * BAR)
    result = _candles("BTC", "binance", BTC_START, end, _budget())
    assert result.is_success
    observation = result.value
    assert observation.duplicate_count == 1
    assert observation.actual_bar_count == 120
    assert len({bar.open_time for bar in observation.bars}) == 120


def test_conflicting_duplicate_rows_fail_closed(monkeypatch):
    def mutate(selected):
        conflicting = list(selected[3])
        # Swap open and close: still a self-consistent candle, so the
        # parser accepts it and only the merge can detect the conflict.
        conflicting[1], conflicting[4] = conflicting[4], conflicting[1]
        return selected + [conflicting]

    _binance_server(monkeypatch, mutate=mutate)
    _install_clocks(monkeypatch)
    end = BTC_START + timedelta(seconds=120 * BAR)
    result = _candles("BTC", "binance", BTC_START, end, _budget())
    assert result.status is AdapterStatus.UPSTREAM_ERROR
    assert result.reason_code == "duplicate_bar_conflict"
    assert result.value is None
    assert len(result.evidence) == 1


def test_unfinished_bar_is_excluded_and_window_clamps_to_last_closed(monkeypatch):
    # The series ends 2026-11-11T00:00Z; at "now" 2026-11-10T21:00Z the
    # last closed boundary is 2026-11-10T18:00Z and the 18:00 bar is
    # still open. An upstream that overshoots and returns it anyway must
    # see that bar excluded and counted, never merged into bars.
    open_ms = int(datetime(2026, 11, 10, 18, tzinfo=UTC).timestamp()) * 1000

    def handler(url: str):
        query = parse_qs(urlparse(url).query)
        start_ms = int(query["startTime"][0])
        end_ms = int(query["endTime"][0])
        selected = [row for row in BINANCE_ROWS if start_ms <= row[0] <= end_ms]
        selected.append(
            [
                open_ms,
                "1.00",
                "2.00",
                "0.50",
                "1.50",
                "10.00",
                open_ms + BAR * 1000 - 1,
                "0.00",
                1,
                "0.00",
                "0.00",
                "0",
            ]
        )
        return json.dumps(selected).encode("utf-8"), {}

    _install(monkeypatch, handler)
    _install_clocks(monkeypatch, utc=datetime(2026, 11, 10, 21, tzinfo=UTC))
    start = datetime(2026, 11, 10, 6, tzinfo=UTC)
    end = datetime(2026, 11, 11, 0, tzinfo=UTC)
    result = _candles("BTC", "binance", start, end, _budget())
    assert result.is_success
    observation = result.value
    assert observation.actual_bar_count == 2
    assert observation.excluded_unfinished_count == 1
    assert observation.covered_end == datetime(2026, 11, 10, 18, tzinfo=UTC)
    assert observation.requested_end == end
    assert observation.covered_end < observation.requested_end
    assert observation.bars[-1].close_time == observation.covered_end


def test_window_not_yet_closed_is_insufficient_data_zero_network(monkeypatch):
    server = _install(monkeypatch, _json_handler([]))
    _install_clocks(monkeypatch)
    start = datetime(2026, 11, 15, 6, tzinfo=UTC)
    end = datetime(2026, 11, 15, 12, tzinfo=UTC)
    result = _candles("BTC", "binance", start, end, _budget())
    assert (result.status, result.reason_code) == (
        AdapterStatus.INSUFFICIENT_DATA,
        "window_not_closed",
    )
    assert server.calls == []


def test_coinbase_gap_is_insufficient_data_not_partial_success(monkeypatch):
    def mutate(selected, start_s, end_s):
        if start_s == int(BTC_START.timestamp()):
            return [row for index, row in enumerate(selected) if index != 7]
        return selected

    server = _coinbase_server(monkeypatch, mutate=mutate)
    _install_clocks(monkeypatch)
    end = BTC_START + timedelta(seconds=96 * BAR)
    result = _candles("BTC", "coinbase", BTC_START, end, _budget())
    assert (result.status, result.reason_code) == (
        AdapterStatus.INSUFFICIENT_DATA,
        "window_incomplete",
    )
    assert result.value is None
    assert len(result.evidence) == 2
    assert len(server.calls) == 2


def test_coinbase_empty_chunk_is_insufficient_data(monkeypatch):
    def mutate(selected, start_s, end_s):
        if start_s != int(BTC_START.timestamp()):
            return []
        return selected

    _coinbase_server(monkeypatch, mutate=mutate)
    _install_clocks(monkeypatch)
    end = BTC_START + timedelta(seconds=96 * BAR)
    result = _candles("BTC", "coinbase", BTC_START, end, _budget())
    assert result.status is AdapterStatus.INSUFFICIENT_DATA
    assert result.reason_code == "window_incomplete"


# ---------------------------------------------------------------------------
# Kraken: hourly aggregation, uncommitted row, history limits
# ---------------------------------------------------------------------------


def test_kraken_aggregates_hourly_rows_into_utc_six_hour_bars(monkeypatch):
    server = _kraken_server(monkeypatch)
    _install_clocks(monkeypatch)
    end = KRAKEN_START + timedelta(hours=36)
    result = _candles("ETH", "kraken", KRAKEN_START, end, _budget())
    assert result.is_success
    observation = result.value
    assert (observation.venue, observation.pair, observation.currency) == (
        "kraken",
        "ETHUSD",
        "USD",
    )
    assert observation.actual_bar_count == 6
    committed = KRAKEN_ENVELOPE["result"]["XETHZUSD"][:-1]
    for index, bar in enumerate(observation.bars):
        hours = committed[index * 6 : (index + 1) * 6]
        assert bar.open == float(hours[0][1])
        assert bar.close == float(hours[-1][4])
        assert bar.high == max(float(hour[2]) for hour in hours)
        assert bar.low == min(float(hour[3]) for hour in hours)
        assert bar.volume == pytest.approx(sum(float(hour[6]) for hour in hours))
        assert bar.open_time == KRAKEN_START + timedelta(hours=6 * index)
    assert observation.bars[1].open_time.month == 10
    assert observation.bars[2].open_time.month == 11
    query = parse_qs(urlparse(server.urls[0]).query)
    assert query["pair"] == ["ETHUSD"]
    assert query["interval"] == ["60"]
    assert query["since"] == [str(int(KRAKEN_START.timestamp()))]


def test_kraken_uncommitted_final_row_is_dropped(monkeypatch):
    _kraken_server(monkeypatch)
    _install_clocks(monkeypatch)
    end = KRAKEN_START + timedelta(hours=36)
    result = _candles("ETH", "kraken", KRAKEN_START, end, _budget())
    assert result.is_success
    uncommitted = KRAKEN_ENVELOPE["result"]["XETHZUSD"][-1]
    for bar in result.value.bars:
        assert bar.high < float(uncommitted[2])
        assert bar.low < float(uncommitted[3])
        assert bar.close != float(uncommitted[4])


def test_kraken_missing_hour_is_insufficient_data(monkeypatch):
    server = _kraken_server(monkeypatch, drop_hour=5)
    _install_clocks(monkeypatch)
    end = KRAKEN_START + timedelta(hours=36)
    result = _candles("ETH", "kraken", KRAKEN_START, end, _budget())
    assert (result.status, result.reason_code) == (
        AdapterStatus.INSUFFICIENT_DATA,
        "window_incomplete",
    )
    assert result.value is None
    assert len(server.calls) == 1


def test_kraken_window_beyond_recent_history_limit_is_refused_upfront(monkeypatch):
    server = _install(monkeypatch, _json_handler([]))
    _install_clocks(monkeypatch)
    end = BTC_START + timedelta(seconds=120 * BAR)
    result = _candles("BTC", "kraken", BTC_START, end, _budget())
    assert (result.status, result.reason_code) == (
        AdapterStatus.INSUFFICIENT_DATA,
        "recent_history_limit",
    )
    assert result.value is None
    assert server.calls == []


def test_kraken_recent_only_response_is_not_paged_into_full_history(monkeypatch):
    server = _kraken_server(monkeypatch, ignore_since=True)
    _install_clocks(monkeypatch)
    start = datetime(2026, 10, 30, tzinfo=UTC)
    end = datetime(2026, 11, 2, tzinfo=UTC)
    result = _candles("ETH", "kraken", start, end, _budget())
    assert result.status is AdapterStatus.INSUFFICIENT_DATA
    assert result.reason_code == "window_incomplete"
    assert len(server.calls) == 1
    assert len(result.evidence) == 1


# ---------------------------------------------------------------------------
# source/currency isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source,asset,pair,currency,price",
    [
        ("binance", "BTC", "BTCUSDT", "USDT", 80342.38),
        ("binance", "ETH", "ETHUSDT", "USDT", 2326.84),
        ("coinbase", "BTC", "BTC-USD", "USD", 80342.38),
        ("coinbase", "ETH", "ETH-USD", "USD", 2326.84),
        ("kraken", "BTC", "XBTUSD", "USD", 80342.38),
        ("kraken", "ETH", "ETHUSD", "USD", 2326.84),
    ],
)
def test_spot_binding_table_is_explicit_per_source(
    monkeypatch, source, asset, pair, currency, price
):
    payload_by_source = {
        "binance": {
            "symbol": pair,
            "price": f"{price:.2f}",
        },
        "coinbase": {"trade_id": 1, "price": f"{price:.2f}"},
        "kraken": {
            "error": [],
            "result": {
                "XXBTZUSD": {"c": ["80342.380000", "0.010000000"]},
                "XETHZUSD": {"c": ["2326.840000", "0.010000000"]},
            },
        },
    }
    _install(monkeypatch, _json_handler(payload_by_source[source]))
    _install_clocks(monkeypatch)
    result = _spot(asset, source, _budget())
    assert result.is_success
    observation = result.value
    assert (observation.asset, observation.source) == (asset, source)
    assert (observation.venue, observation.pair, observation.currency) == (
        source,
        pair,
        currency,
    )
    assert observation.price == pytest.approx(price)
    assert result.evidence[0].source_kind == f"crypto-spot-{source}"


def test_usd_and_usdt_series_stay_isolated(monkeypatch):
    def handler(url: str):
        if "binance.com" in url:
            query = parse_qs(urlparse(url).query)
            start_ms = int(query["startTime"][0])
            end_ms = int(query["endTime"][0])
            selected = [row for row in BINANCE_ROWS if start_ms <= row[0] <= end_ms]
            return json.dumps(selected).encode("utf-8"), {}
        query = parse_qs(urlparse(url).query)
        start_s = int(datetime.fromisoformat(query["start"][0]).timestamp())
        end_s = int(datetime.fromisoformat(query["end"][0]).timestamp())
        selected = [row for row in COINBASE_ROWS if start_s <= row[0] < end_s]
        return json.dumps(selected).encode("utf-8"), {}

    _install(monkeypatch, handler)
    _install_clocks(monkeypatch)
    binance = _candles(
        "BTC",
        "binance",
        BTC_START,
        BTC_START + timedelta(seconds=8 * BAR),
        _budget(),
    )
    assert binance.value.currency == "USDT"
    assert binance.value.pair == "BTCUSDT"
    assert "binance.com" in binance.evidence[0].source_reference
    coinbase = _candles(
        "BTC",
        "coinbase",
        BTC_START,
        BTC_START + timedelta(seconds=8 * BAR),
        _budget(),
    )
    assert coinbase.value.currency == "USD"
    assert coinbase.value.pair == "BTC-USD"
    assert "coinbase.com" in coinbase.evidence[0].source_reference
    assert binance.evidence[0].source_kind != coinbase.evidence[0].source_kind
    assert binance.value.pair != coinbase.value.pair
    assert binance.evidence[0].raw_bytes != coinbase.evidence[0].raw_bytes


# ---------------------------------------------------------------------------
# frozen 30d/RMS regression and the complete-bar-only bridge
# ---------------------------------------------------------------------------


def test_frozen_30d_rms_anchor_reproduces_bitexactly(monkeypatch):
    _binance_server(monkeypatch)
    _install_clocks(monkeypatch)
    end = BTC_START + timedelta(seconds=120 * BAR)
    result = _candles("BTC", "binance", BTC_START, end, _budget())
    observation = result.value
    assert realized_vol_daily(observation.closes) == float(
        FIXTURE["expected"]["btc_realized_vol_daily_30d"]
    )
    windows = realized_vol_windows(observation.closes)
    assert windows["30d"] == float(FIXTURE["expected"]["btc_realized_vol_daily_30d"])
    assert windows["7d"] == float(FIXTURE["expected"]["btc_realized_vol_daily_7d"])
    assert SIGNATURE_WINDOW == "30d"
    assert dict(VOL_WINDOW_BARS) == {"7d": 28, "30d": 120, "60d": 240, "87d": 348}


def test_short_history_cannot_masquerade_as_full_window(monkeypatch):
    _binance_server(monkeypatch)
    _install_clocks(monkeypatch)
    end = BTC_START + timedelta(seconds=119 * BAR)
    result = _candles("BTC", "binance", BTC_START, end, _budget())
    observation = result.value
    assert observation.actual_bar_count == 119
    assert observation.gap_count == 0
    assert prices.closes_for_vol_window(observation, "30d") is None
    assert prices.closes_for_vol_window(observation, "60d") is None
    full_end = BTC_START + timedelta(seconds=120 * BAR)
    full = _candles("BTC", "binance", BTC_START, full_end, _budget()).value
    assert prices.closes_for_vol_window(full, "30d") == full.closes[-120:]
    assert prices.closes_for_vol_window(full, "7d") == full.closes[-28:]
    with pytest.raises(ValueError):
        prices.closes_for_vol_window(full, "91d")
    with pytest.raises(ValueError):
        prices.closes_for_vol_window("not-an-observation", "30d")


# ---------------------------------------------------------------------------
# budget exhaustion: requests, bytes and deadline stay distinct
# ---------------------------------------------------------------------------


def test_request_budget_exhaustion_keeps_first_evidence(monkeypatch):
    server = _coinbase_server(monkeypatch)
    _install_clocks(monkeypatch)
    end = BTC_START + timedelta(seconds=96 * BAR)
    result = _candles("BTC", "coinbase", BTC_START, end, _budget(max_requests=1))
    assert (result.status, result.reason_code) == (
        AdapterStatus.BUDGET_EXHAUSTED,
        "requests_exhausted",
    )
    assert result.value is None
    assert len(result.evidence) == 1
    assert len(server.calls) == 1


def test_response_byte_budget_exhaustion_is_distinct(monkeypatch):
    _coinbase_server(monkeypatch)
    _install_clocks(monkeypatch)
    first = json.dumps(COINBASE_ROWS[:48]).encode("utf-8")
    second = json.dumps(COINBASE_ROWS[48:96]).encode("utf-8")
    budget = _budget(
        max_response_bytes=len(first),
        max_total_response_bytes=len(first) + len(second) - 10,
    )
    end = BTC_START + timedelta(seconds=96 * BAR)
    result = _candles("BTC", "coinbase", BTC_START, end, budget)
    assert (result.status, result.reason_code) == (
        AdapterStatus.BUDGET_EXHAUSTED,
        "response_bytes_exhausted",
    )
    assert result.value is None
    assert len(result.evidence) == 1


def test_deadline_exhaustion_is_its_own_state_and_clamps_timeout(monkeypatch):
    server = _coinbase_server(monkeypatch)
    _install_clocks(monkeypatch)
    clock = {"mono": T0}

    def advance():
        value = clock["mono"]
        clock["mono"] = value + 200.0
        return value

    monkeypatch.setattr(prices, "_monotonic", advance)
    end = BTC_START + timedelta(seconds=96 * BAR)
    result = _candles(
        "BTC", "coinbase", BTC_START, end, _budget(deadline_monotonic=T0 + 150.0)
    )
    assert (result.status, result.reason_code) == (
        AdapterStatus.DEADLINE_EXCEEDED,
        "deadline_exceeded",
    )
    assert result.value is None
    assert len(result.evidence) == 1
    assert len(server.calls) == 1
    assert server.calls[0][1] == pytest.approx(15.0)


def test_already_expired_budget_denies_before_network(monkeypatch):
    server = _install(monkeypatch, _json_handler({"symbol": "BTCUSDT", "price": "1.00"}))
    _install_clocks(monkeypatch)
    result = _spot(
        "BTC", "binance", _budget(deadline_monotonic=T0 - 1.0)
    )
    assert (result.status, result.reason_code) == (
        AdapterStatus.DEADLINE_EXCEEDED,
        "deadline_exceeded",
    )
    assert server.calls == []
    assert result.evidence == ()


def test_effective_timeout_is_clamped_by_remaining_deadline(monkeypatch):
    server = _binance_server(monkeypatch)
    _install_clocks(monkeypatch)
    end = BTC_START + timedelta(seconds=120 * BAR)
    result = _candles(
        "BTC", "binance", BTC_START, end, _budget(deadline_monotonic=T0 + 5.0)
    )
    assert result.is_success
    assert server.calls[0][1] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# framing, truncation and transport failures
# ---------------------------------------------------------------------------


def test_declared_length_longer_than_body_is_rejected(monkeypatch):
    body = json.dumps(BINANCE_ROWS).encode("utf-8")

    def handler(url: str):
        return body, {"declared_length": len(body) + 64}

    _install(monkeypatch, handler)
    _install_clocks(monkeypatch)
    result = _candles("BTC", "binance", BTC_START, NOW, _budget())
    assert (result.status, result.reason_code) == (
        AdapterStatus.UPSTREAM_ERROR,
        "public_response_incomplete",
    )


def test_truncated_chunked_body_is_rejected(monkeypatch):
    truncated_chunks = b"5\r\nhello\r\nA\r\nworl"

    def handler(url: str):
        return truncated_chunks, {
            "declared_length": None,
            "transfer_encoding": "chunked",
        }

    _install(monkeypatch, handler)
    _install_clocks(monkeypatch)
    result = _candles("BTC", "binance", BTC_START, NOW, _budget())
    assert (result.status, result.reason_code) == (
        AdapterStatus.UPSTREAM_ERROR,
        "public_response_incomplete",
    )


def test_encoded_response_is_rejected(monkeypatch):
    _install(monkeypatch, _json_handler(BINANCE_ROWS, content_encoding="gzip"))
    _install_clocks(monkeypatch)
    result = _candles("BTC", "binance", BTC_START, NOW, _budget())
    assert (result.status, result.reason_code) == (
        AdapterStatus.UPSTREAM_ERROR,
        "public_response_framing_invalid",
    )


def test_empty_body_is_rejected(monkeypatch):
    _install(monkeypatch, _json_handler(b"", declared_length=0))
    _install_clocks(monkeypatch)
    result = _candles("BTC", "binance", BTC_START, NOW, _budget())
    assert (result.status, result.reason_code) == (
        AdapterStatus.UPSTREAM_ERROR,
        "public_response_empty",
    )


def test_oversized_response_within_configured_cap_is_rejected(monkeypatch):
    body = json.dumps(BINANCE_ROWS).encode("utf-8")

    def handler(url: str):
        return body, {}

    _install(monkeypatch, handler)
    _install_clocks(monkeypatch)
    result = _candles(
        "BTC", "binance", BTC_START, NOW, _budget(max_response_bytes=64)
    )
    assert (result.status, result.reason_code) == (
        AdapterStatus.UPSTREAM_ERROR,
        "public_response_too_large",
    )


def test_bad_status_origin_and_content_type_are_rejected(monkeypatch):
    body = json.dumps(BINANCE_ROWS).encode("utf-8")
    cases = [
        ({"status": 404}, "upstream_status_invalid"),
        ({"geturl": "https://elsewhere.example/klines"}, "upstream_origin_invalid"),
        ({"content_type": "text/html"}, "upstream_content_type_invalid"),
    ]
    for kwargs, expected in cases:
        _install(monkeypatch, _json_handler(BINANCE_ROWS, **kwargs))
        _install_clocks(monkeypatch)
        result = _candles("BTC", "binance", BTC_START, NOW, _budget())
        assert (result.status, result.reason_code) == (
            AdapterStatus.UPSTREAM_ERROR,
            expected,
        ), kwargs


def test_transport_exceptions_map_to_unreachable(monkeypatch):
    def raiser(url: str):
        raise URLError("no route to host")

    _install(monkeypatch, raiser)
    _install_clocks(monkeypatch)
    result = _candles("BTC", "binance", BTC_START, NOW, _budget())
    assert (result.status, result.reason_code) == (
        AdapterStatus.UPSTREAM_ERROR,
        "upstream_unreachable",
    )

    def http_error_raiser(url: str):
        raise HTTPError(url, 503, "unavailable", None, None)

    _install(monkeypatch, http_error_raiser)
    result = _candles("BTC", "binance", BTC_START, NOW, _budget())
    assert (result.status, result.reason_code) == (
        AdapterStatus.UPSTREAM_ERROR,
        "upstream_status_invalid",
    )


# ---------------------------------------------------------------------------
# malformed payloads
# ---------------------------------------------------------------------------


def test_malformed_candle_payloads_are_rejected(monkeypatch):
    bad_payloads = [
        "not json",
        {"unexpected": "shape"},
        [[*BINANCE_ROWS[0][:11]]],
        [["x", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1"]],
    ]
    for payload in bad_payloads:
        _install(monkeypatch, _json_handler(payload))
        _install_clocks(monkeypatch)
        result = _candles("BTC", "binance", BTC_START, NOW, _budget())
        assert (result.status, result.reason_code) == (
            AdapterStatus.UPSTREAM_ERROR,
            "invalid_candle_payload",
        ), payload


def test_kraken_error_channel_is_upstream_error(monkeypatch):
    _install(
        monkeypatch,
        _json_handler(
            {"error": ["EQuery:Unknown asset pair"], "result": {"XETHZUSD": [], "last": 0}}
        ),
    )
    _install_clocks(monkeypatch)
    end = KRAKEN_START + timedelta(hours=36)
    result = _candles("ETH", "kraken", KRAKEN_START, end, _budget())
    assert (result.status, result.reason_code) == (
        AdapterStatus.UPSTREAM_ERROR,
        "kraken_api_error",
    )


# ---------------------------------------------------------------------------
# spot: independent observation
# ---------------------------------------------------------------------------


def test_spot_happy_paths_parse_prices(monkeypatch):
    server = _install(
        monkeypatch, _json_handler(FIXTURE["spot"]["binance_btc_ticker"])
    )
    _install_clocks(monkeypatch)
    result = _spot("BTC", "binance", _budget())
    assert result.is_success
    observation = result.value
    assert observation.price == pytest.approx(80342.38)
    assert observation.source_observed_at is None
    assert observation.fetched_at.tzinfo is not None
    assert observation.price > 0
    assert len(server.calls) == 1


def test_spot_coinbase_and_kraken_parse(monkeypatch):
    _install(monkeypatch, _json_handler(FIXTURE["spot"]["coinbase_eth_ticker"]))
    _install_clocks(monkeypatch)
    coinbase = _spot("ETH", "coinbase", _budget())
    assert coinbase.is_success
    assert coinbase.value.price == pytest.approx(2326.84)
    assert coinbase.value.pair == "ETH-USD"
    _install(monkeypatch, _json_handler(FIXTURE["spot"]["kraken_btc_ticker"]))
    kraken = _spot("BTC", "kraken", _budget())
    assert kraken.is_success
    assert kraken.value.price == pytest.approx(80342.38)
    assert kraken.value.pair == "XBTUSD"


@pytest.mark.parametrize(
    "payload",
    [
        {"symbol": "BTCUSDT", "price": "-1.00"},
        {"symbol": "BTCUSDT", "price": "0"},
        {"symbol": "ETHUSDT", "price": "1.00"},
        {"symbol": "BTCUSDT"},
        {"price": "1.00"},
        ["not", "a", "dict"],
    ],
)
def test_invalid_spot_payloads_are_rejected(monkeypatch, payload):
    _install(monkeypatch, _json_handler(payload))
    _install_clocks(monkeypatch)
    result = _spot("BTC", "binance", _budget())
    assert (result.status, result.reason_code) == (
        AdapterStatus.UPSTREAM_ERROR,
        "invalid_spot_payload",
    )


def test_spot_and_candles_are_independent_observations(monkeypatch):
    def handler(url: str):
        if "/ticker/price" in url:
            return json.dumps(FIXTURE["spot"]["binance_btc_ticker"]).encode(), {}
        query = parse_qs(urlparse(url).query)
        start_ms = int(query["startTime"][0])
        end_ms = int(query["endTime"][0])
        selected = [row for row in BINANCE_ROWS if start_ms <= row[0] <= end_ms]
        return json.dumps(selected).encode("utf-8"), {}

    server = _install(monkeypatch, handler)
    _install_clocks(monkeypatch)
    budget = _budget()
    spot = _spot("BTC", "binance", budget)
    candles = _candles(
        "BTC",
        "binance",
        BTC_START,
        BTC_START + timedelta(seconds=8 * BAR),
        budget,
    )
    assert spot.is_success and candles.is_success
    assert budget.requests_used == 2
    assert len(spot.evidence) == 1 and len(candles.evidence) == 1
    assert spot.evidence[0].source_kind == "crypto-spot-binance"
    assert candles.evidence[0].source_kind == "crypto-candles-binance"
    assert not hasattr(spot.value, "bars")
    assert not hasattr(candles.value, "price")
    spot_urls = [url for url in server.urls if "ticker" in url]
    candle_urls = [url for url in server.urls if "klines" in url]
    assert len(spot_urls) == 1 and len(candle_urls) == 1


# ---------------------------------------------------------------------------
# evidence integrity: one FetchEvidence per response, digesting exact bytes
# ---------------------------------------------------------------------------


def test_every_response_yields_evidence_with_exact_sha256(monkeypatch):
    server = _coinbase_server(monkeypatch)
    _install_clocks(monkeypatch)
    end = BTC_START + timedelta(seconds=120 * BAR)
    result = _candles("BTC", "coinbase", BTC_START, end, _budget())
    assert result.is_success
    assert len(result.evidence) == len(server.served) == 3
    for evidence, (url, body) in zip(result.evidence, server.served, strict=True):
        assert evidence.source_reference == url
        assert evidence.raw_bytes == body
        assert evidence.content_sha256 == sha256(body).hexdigest()
        assert evidence.fetched_at.tzinfo is not None
        assert evidence.source_kind == "crypto-candles-coinbase"
        assert evidence.snapshot_registry_key is None


def test_candles_observation_rejects_bad_shapes():
    good_time = datetime(2026, 10, 12, tzinfo=UTC)
    bar = prices.ClosedBar(
        open_time=good_time,
        close_time=good_time + timedelta(seconds=BAR),
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=10.0,
    )
    base = {
        "asset": "BTC",
        "source": "binance",
        "venue": "binance",
        "pair": "BTCUSDT",
        "currency": "USDT",
        "bar_hours": 6,
        "requested_start": good_time,
        "requested_end": good_time + timedelta(seconds=BAR),
        "covered_start": good_time,
        "covered_end": good_time + timedelta(seconds=BAR),
        "bars": (bar,),
        "expected_bar_count": 1,
        "actual_bar_count": 1,
        "gap_count": 0,
        "duplicate_count": 0,
        "excluded_unfinished_count": 0,
        "excluded_out_of_window_count": 0,
        "fetched_at": NOW,
    }
    prices.CandleWindowObservation(**base)
    with pytest.raises(ValueError):
        prices.CandleWindowObservation(**{**base, "gap_count": 1})
    with pytest.raises(ValueError):
        prices.CandleWindowObservation(**{**base, "actual_bar_count": 2})
    with pytest.raises(ValueError):
        prices.CandleWindowObservation(**{**base, "bars": ()})
    with pytest.raises(ValueError):
        prices.ClosedBar(
            open_time=good_time,
            close_time=good_time + timedelta(seconds=BAR),
            open=2.0,
            high=1.0,
            low=0.5,
            close=1.5,
            volume=10.0,
        )
    with pytest.raises(ValueError):
        prices.SpotObservation(
            asset="BTC",
            source="binance",
            venue="binance",
            pair="BTCUSDT",
            currency="USDT",
            price=0.0,
            fetched_at=NOW,
        )
