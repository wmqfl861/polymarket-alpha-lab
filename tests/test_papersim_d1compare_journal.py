"""D1 journal proofs using public response fixtures and disposable PostgreSQL."""

from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from polymarket_alpha_lab.papersim.d1compare import journal

UTC = datetime.timezone.utc
STAMP = datetime.datetime(2026, 11, 20, 0, 5, tzinfo=UTC)
_CONDITION = "0x" + "a" * 64
_GAMMA_PAGE = (b' \n' + json.dumps([{"id": "event-12", "slug": "event-slug", "markets": [{
    "id": "market-34", "conditionId": _CONDITION, "slug": "fixture-market",
    "question": "Will the match end in a draw?", "description": "Overtime included.",
    "endDate": "2026-12-31T00:00:00Z", "outcomes": '["No", "Yes"]',
    "clobTokenIds": '["111", "222"]', "outcomePrices": '["0.25", "0.75"]',
}]}], indent=2).encode() + b'\n')


def _book_bytes(token: str, source_time: datetime.datetime | None) -> bytes:
    book = {"asset_id": token, "market": _CONDITION, "bids": [],
            "asks": [{"price": "0.25", "size": "5.00"}]}
    if source_time is not None:
        elapsed = source_time - datetime.datetime(1970, 1, 1, tzinfo=UTC)
        book["timestamp"] = str(elapsed.days * 86_400_000 + elapsed.seconds * 1000 + elapsed.microseconds // 1000)
    return b'\n' + json.dumps(book, indent=2).encode() + b' \n'


class _PublicFixtureOpener:
    def __init__(self, book_responses):
        self.books = book_responses
        self.calls = []

    def open(self, url, *, timeout_seconds):
        from tests.test_papersim_adapter_polymarket import _response

        self.calls.append(url)
        split = urlsplit(url)
        query = parse_qs(split.query)
        if split.path == "/events":
            if query["tag_slug"] == ["empty"]:
                return _response(b' [ ] \n', url=url)
            assert query["tag_slug"] == ["soccer"]
            assert query["closed"] == ["false"] and query["limit"] == ["50"]
            responses = {"0": _GAMMA_PAGE, "50": _GAMMA_PAGE, "100": b' [ ] \n'}
            return _response(responses[query["offset"][0]], url=url)
        assert split.path == "/book"
        response = self.books[query["token_id"][0]]
        if isinstance(response, Exception):
            raise response
        return _response(response, url=url)


def _capture(url: str, content: bytes, *, stamp: datetime.datetime = STAMP,
             meta: dict | None = None) -> journal.JournalEntry:
    return journal.capture(url=url, utc_fetched_at=stamp,
                           request_meta=meta or {}, content=content)


# ---------------------------------------------------------------------------
# capture: sanitization, digests, refusals
# ---------------------------------------------------------------------------


def test_capture_computes_digest_and_sanitizes_credentials() -> None:
    entry = _capture(
        "https://clob.polymarket.com/book?token_id=123",
        b'{"bids":[],"asks":[]}',
        meta={"method": "GET",
              "headers": {"User-Agent": "research", "Authorization": "Bearer x",
                          "Cookie": "sid=1", "X-API-Key": "k"},
              "query": {"token_id": "123", "api_key": "z", "sig": "1"}},
    )
    import hashlib

    assert entry.content_sha256 == hashlib.sha256(b'{"bids":[],"asks":[]}').hexdigest()
    assert entry.request_meta["headers"] == {"User-Agent": "research"}
    assert entry.request_meta["query"] == {"token_id": "123", "sig": "1"}
    assert "Authorization" not in json.dumps(entry.request_meta)


def test_capture_refuses_naive_timestamp_empty_url_and_bad_digest() -> None:
    with pytest.raises(journal.JournalError) as error:
        journal.capture("u", datetime.datetime(2026, 1, 1), {}, b"x")
    assert error.value.code == "journal_timestamp_naive"
    with pytest.raises(journal.JournalError) as error:
        journal.capture("  ", STAMP, {}, b"x")
    assert error.value.code == "journal_url_invalid"
    with pytest.raises(journal.JournalError) as error:
        journal.capture("u", STAMP, {}, b"x", content_sha256="0" * 64)
    assert error.value.code == "journal_digest_mismatch"


def test_source_ref_is_url_plus_sha() -> None:
    entry = _capture("https://examplehosts.test/a", b"payload")
    assert entry.source_ref() == f"https://examplehosts.test/a#{entry.content_sha256}"


# ---------------------------------------------------------------------------
# verify_journal / journal_digest
# ---------------------------------------------------------------------------


def test_verify_journal_accepts_clean_entries_and_detects_duplicates() -> None:
    one = _capture("https://h.test/1", b"a")
    two = _capture("https://h.test/2", b"b")
    again = _capture("https://h.test/1", b"a", stamp=STAMP +
                     datetime.timedelta(seconds=5))
    verification = journal.verify_journal([one, two, again])
    assert verification.ok is True
    assert verification.entry_count == 3
    assert verification.duplicate_digests == (one.content_sha256,)
    assert "ok=True" in verification.summary()


def test_verify_journal_recomputes_digest_and_catches_tampering() -> None:
    entry = _capture("https://h.test/1", b"a")
    good = journal.verify_journal([entry])
    assert good.ok is True
    tampered = journal.JournalEntry(
        url=entry.url, utc_fetched_at=entry.utc_fetched_at,
        request_meta=entry.request_meta, content=b"TAMPERED",
        content_sha256=entry.content_sha256)
    verification = journal.verify_journal([tampered])
    assert verification.ok is False
    assert verification.failures[0][1] == "journal_digest_mismatch"


def test_verify_journal_flags_leaked_credential_meta() -> None:
    import hashlib

    leaky = journal.JournalEntry(
        url="https://h.test/1", utc_fetched_at=STAMP,
        request_meta={"Authorization": "Bearer x"},
        content=b"a", content_sha256=hashlib.sha256(b"a").hexdigest())
    verification = journal.verify_journal([leaky])
    assert verification.ok is False
    assert verification.failures[0][1] == "journal_credential_meta_leak"


def test_journal_digest_binds_content_and_order_independent() -> None:
    one = _capture("https://h.test/1", b"a")
    two = _capture("https://h.test/2", b"b")
    assert journal.journal_digest([one, two]) == journal.journal_digest([two, one])
    changed = _capture("https://h.test/2", b"different")
    assert journal.journal_digest([one, two]) != journal.journal_digest([one, changed])


# ---------------------------------------------------------------------------
# staging document round trip
# ---------------------------------------------------------------------------


def test_journal_document_round_trip_and_refusals() -> None:
    entries = [_capture("https://h.test/1", b"a"),
               _capture("https://h.test/2", b"b",
                        meta={"headers": {"User-Agent": "research"}})]
    document = journal.journal_document(entries)
    rebuilt = journal.entries_from_document(document)
    assert [entry.content for entry in rebuilt] == [b"a", b"b"]
    assert journal.journal_digest(rebuilt) == document["journal_sha256"]
    broken = dict(document, journal_sha256="0" * 64)
    with pytest.raises(journal.JournalError) as error:
        journal.entries_from_document(broken)
    assert error.value.code == "journal_digest_mismatch"
    with pytest.raises(journal.JournalError) as error:
        journal.entries_from_document({"journal_version": 2, "entries": []})
    assert error.value.code == "journal_document_invalid"


# ---------------------------------------------------------------------------
# executable odds: book parsing
# ---------------------------------------------------------------------------


def _book_entry(bids: list, asks: list, *, asset: str = "111") -> journal.JournalEntry:
    payload = {"asset_id": asset, "bids": bids, "asks": asks}
    return _capture("https://clob.polymarket.com/book?token_id=" + asset,
                    json.dumps(payload).encode())


def test_book_from_entry_preserves_full_ladder_and_raw_strings() -> None:
    entry = _book_entry(
        [{"price": "0.42", "size": "1200"}, {"price": "0.38", "size": "800"}],
        [{"price": "0.44", "size": "900"}, {"price": "0.47", "size": "0"}])
    snapshot = journal.book_from_entry(entry)
    assert snapshot.token_id == "111"
    assert snapshot.best_bid() == 0.42
    assert snapshot.best_ask() == 0.44
    assert snapshot.ask_ladder() == ((0.44, 900.0), (0.47, 0.0))
    assert snapshot.bid_ladder() == ((0.42, 1200.0), (0.38, 800.0))
    # raw quotes preserved alongside parsed floats; 0 stays 0, absent is None
    assert snapshot.asks[1].raw_price == "0.47"
    assert snapshot.asks[1].size == 0.0
    assert snapshot.asks[0].price == 0.44


def test_book_level_null_zero_unknown_stay_distinct() -> None:
    entry = _book_entry([{"price": None, "size": "5"}, {"price": "x", "size": "5"}],
                        [])
    snapshot = journal.book_from_entry(entry)
    assert snapshot.bids[0].price is None and snapshot.bids[0].raw_price is None
    assert snapshot.bids[0].size == 5.0
    assert snapshot.bids[1].price is None  # unparseable -> unknown parse, raw kept


def test_book_from_entry_refuses_non_book_payload() -> None:
    entry = _capture("https://clob.polymarket.com/book", b'{"foo": 1}')
    with pytest.raises(journal.JournalError) as error:
        journal.book_from_entry(entry)
    assert error.value.code == "journal_book_payload_invalid"
    text_entry = _capture("https://stooq.test/x", b"not json")
    with pytest.raises(journal.JournalError):
        journal.book_from_entry(text_entry)


# ---------------------------------------------------------------------------
# market observation: terms verbatim
# ---------------------------------------------------------------------------


def _market_entry(*, market_id: str = "6237097", question: str | None =
                  "Will the match end in a draw?", outcomes: str =
                  '["Yes","No"]') -> journal.JournalEntry:
    payload = {
        "id": market_id, "question": question, "outcomes": outcomes,
        "outcomePrices": '["0.26","0.74"]',
        "clobTokenIds": '["111","222"]',
        "conditionId": "0xabc", "slug": "fixture-draw",
        "gameStartTime": "2026-11-08T19:45:00Z", "endDate": "2026-11-08T19:45:00Z",
        "liquidityNum": None, "volume24hr": 51000.0,
    }
    return _capture("https://gamma-api.polymarket.com/markets/" + market_id,
                    json.dumps(payload).encode())


def test_market_from_entries_preserves_terms_verbatim() -> None:
    books = [_book_entry([{"price": "0.25", "size": "10"}],
                         [{"price": "0.27", "size": "20"}], asset="111"),
             _book_entry([{"price": "0.72", "size": "30"}],
                         [{"price": "0.74", "size": "40"}], asset="222")]
    observation = journal.market_from_entries(
        _market_entry(), books, league_key="Ligue_1")
    assert observation.question == "Will the match end in a draw?"
    assert observation.outcomes == ("Yes", "No")
    assert observation.clob_token_ids == ("111", "222")
    assert observation.game_start_time_raw == "2026-11-08T19:45:00Z"
    assert observation.league_key == "Ligue_1"
    assert observation.liquidity_num is None      # null stays unknown
    assert observation.volume_24hr == 51000.0
    assert set(observation.books) == {"111", "222"}
    assert observation.as_of() == STAMP
    assert observation.market_source_ref().endswith(observation.market_entry
                                                    .content_sha256)


def test_basketball_terms_carried_verbatim_never_rederived() -> None:
    question = ("Will the Lakers win the game? (overtime included, "
                "unless stated otherwise in the market rules)")
    entry = _market_entry(market_id="77", question=question)
    observation = journal.market_from_entries(entry, [])
    assert observation.question == question  # OT wording carried, not re-derived


def test_market_from_entries_refuses_ambiguous_and_mismatched_books() -> None:
    event_row = _capture("https://gamma-api.polymarket.com/events?x=1",
                         json.dumps({"id": "9", "markets": [{"id": "1"}]}).encode())
    with pytest.raises(journal.JournalError) as error:
        journal.market_from_entries(event_row, [])
    assert error.value.code == "journal_market_payload_ambiguous"
    stray_book = _book_entry([], [], asset="999")
    with pytest.raises(journal.JournalError) as error:
        journal.market_from_entries(_market_entry(), [stray_book])
    assert error.value.code == "journal_book_token_mismatch"
    anonymous = _capture("https://clob.polymarket.com/book", b'{"bids":[],"asks":[]}')
    with pytest.raises(journal.JournalError) as error:
        journal.market_from_entries(_market_entry(), [anonymous])
    assert error.value.code == "journal_book_token_unknown"


def test_book_ladder_side_validation() -> None:
    snapshot = journal.book_from_entry(
        _book_entry([{"price": "0.4", "size": "1"}], [{"price": "0.5", "size": "2"}]))
    assert journal.book_ladder(snapshot, "ask") == ((0.5, 2.0),)
    with pytest.raises(journal.JournalError):
        journal.book_ladder(snapshot, "middle")


def _collect_public_fixture(monkeypatch, fixture, *, clock=lambda: STAMP, tags=("soccer",)):
    from polymarket_alpha_lab.papersim.d1compare import acquire

    start = STAMP - datetime.timedelta(minutes=1)
    end = STAMP + datetime.timedelta(minutes=1)
    stored = {}

    def register(connection, body, kind, source, at, digest):
        assert hashlib.sha256(body).hexdigest() == digest
        assert at is not None and kind == "d1_public_capture_response"
        stored[digest] = body

    monkeypatch.setattr(acquire.polymarket, "BoundedPublicOpener", lambda: fixture)
    monkeypatch.setattr(acquire, "_now_utc", clock)
    monkeypatch.setattr(acquire, "_register", register)
    scope = {"tag_slugs": tags, "window_start": start.isoformat(), "window_end": end.isoformat()}
    return acquire._collect(None, scope, STAMP, start, end), stored


def test_capture_distinguishes_http_error_and_missing_source_time(monkeypatch):
    error = HTTPError("https://example.invalid/?password=do-not-export", 503,
                      "do-not-export", None, BytesIO(b"do-not-export"))
    no_source_time = _book_bytes("222", None)
    fixture = _PublicFixtureOpener({"111": error, "222": no_source_time})
    document, stored = _collect_public_fixture(monkeypatch, fixture)
    http, missing = document["requests"][-2:]
    assert http["http_status"] == 503 and http["status"] == "failed"
    assert http["reason_code"] == "public_http_rejected"
    assert http["content_sha256"] is http["utc_fetched_at"] is http["byte_length"] is None
    assert missing["status"] == "failed" and missing["source_time_status"] == "missing"
    assert missing["reason_code"] == "d1_capture_book_source_time_missing"
    assert missing["source_observed_at"] is None and missing["utc_fetched_at"] == STAMP.isoformat()
    assert stored[missing["content_sha256"]] == no_source_time
    assert document["coverage"]["sources"] == {"polymarket-gamma": "complete", "polymarket-clob": "incomplete"}
    assert document["coverage"]["polymarket_complete"] is False
    assert "do-not-export" not in json.dumps(document)


def test_capture_keeps_bad_json_and_future_source_time_as_evidence(monkeypatch):
    bad_json = b'{"missing-quote:\n'
    fixture = _PublicFixtureOpener({"111": bad_json, "222": _book_bytes("222", STAMP + datetime.timedelta(seconds=1))})
    document, stored = _collect_public_fixture(monkeypatch, fixture)
    malformed, future = document["requests"][-2:]
    assert malformed["reason_code"] == "public_payload_invalid"
    assert stored[malformed["content_sha256"]] == bad_json
    assert future["source_time_status"] == "upstream"
    assert future["reason_code"] == "d1_capture_book_source_time_in_future"
    assert future["source_observed_at"] > future["utc_fetched_at"]
    assert document["coverage"]["books_received"] == 0


def test_capture_keeps_late_body_but_stops_at_exclusive_window_end(monkeypatch):
    time_after_read = STAMP + datetime.timedelta(minutes=1)
    clock = STAMP

    class LateFixture(_PublicFixtureOpener):
        def open(self, url, *, timeout_seconds):
            response = super().open(url, timeout_seconds=timeout_seconds)
            if parse_qs(urlsplit(url).query).get("token_id") != ["111"]:
                return response
            original_read = response.read

            def delayed_read(size):
                nonlocal clock
                body = original_read(size)
                clock = time_after_read
                return body

            response.read = delayed_read
            return response

    responses = {token: _book_bytes(token, STAMP) for token in ("111", "222")}
    fixture = LateFixture(responses)
    document, stored = _collect_public_fixture(monkeypatch, fixture, clock=lambda: clock)
    late, skipped = document["requests"][-2:]
    assert late["status"] == "late" and late["utc_fetched_at"] == time_after_read.isoformat()
    assert stored[late["content_sha256"]] == responses["111"]
    assert skipped["status"] == "not_attempted" and skipped["sent"] is False
    assert len(fixture.calls) == 4
    assert document["decision_as_of"] is None
    assert document["coverage"]["window_status"] == "closed_before_seal"
    assert document["coverage"]["late_responses"] == 1


def test_capture_collects_both_books_for_raw_labels_without_claiming_a_side(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "_GAMMA_PAGE", _GAMMA_PAGE.replace(b"No", b"Over").replace(b"Yes", b"Under"))
    fixture = _PublicFixtureOpener({token: _book_bytes(token, STAMP) for token in ("111", "222")})
    document, _ = _collect_public_fixture(monkeypatch, fixture)
    [market] = document["markets"]
    assert market["binding_status"] == "unresolved_outcomes"
    assert market["side_tokens"] == {}
    assert market["book_tokens"] == ["111", "222"]
    assert document["requests"][0]["page"]["markets"][0]["outcomes"] == ["Over", "Under"]
    assert document["coverage"]["books_received"] == 2


def test_capture_gamma_drift_is_incomplete_book_coverage_not_a_true_empty_tag(monkeypatch):
    from tests.test_papersim_adapter_polymarket import _response

    class DriftingFixture(_PublicFixtureOpener):
        def open(self, url, *, timeout_seconds):
            if parse_qs(urlsplit(url).query).get("offset") == ["50"]:
                self.calls.append(url)
                return _response(_GAMMA_PAGE.replace(b"222", b"333"), url=url)
            return super().open(url, timeout_seconds=timeout_seconds)

    document, stored = _collect_public_fixture(monkeypatch, DriftingFixture({}))
    assert document["discoveries"][0]["rejections"][0]["reason_code"] == "identity_drift"
    assert stored[document["requests"][1]["content_sha256"]] == _GAMMA_PAGE.replace(b"222", b"333")
    assert document["coverage"]["sources"]["polymarket-gamma"] == "complete"
    assert document["coverage"]["sources"]["polymarket-clob"] == "incomplete"
    assert document["coverage"]["polymarket_complete"] is False
    assert document["coverage"]["book_targets"] == 0
    assert document["coverage"]["rejections_without_books"] == 1


def test_capture_scope_validation_precedes_managed_connection(monkeypatch):
    from polymarket_alpha_lab.papersim.d1compare import acquire

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid acquisition used a managed connection")

    monkeypatch.setattr(acquire, "_managed", forbidden)
    base = {"repo_root": Path("."), "window_start": STAMP,
            "window_end": STAMP + datetime.timedelta(minutes=1),
            "tag_slugs": ("soccer",), "allow_public_fetch": True}
    for changes, reason in (
        ({"allow_public_fetch": False}, "public_fetch_not_allowed"),
        ({"window_start": STAMP.replace(tzinfo=None)}, "d1_capture_time_invalid"),
        ({"window_end": STAMP}, "d1_capture_window_invalid"),
        ({"tag_slugs": ("bad?credential=value",)}, "d1_capture_tags_invalid"),
    ):
        with pytest.raises(acquire.AcquisitionError) as error:
            acquire.capture_inputs(**(base | changes))
        assert error.value.code == reason


def _start_fixture_capture_process():
    """Replace the transport only, then execute the checked-in CLI."""
    import runpy
    from polymarket_alpha_lab.papersim.adapters import polymarket

    source = datetime.datetime.now(UTC) - datetime.timedelta(seconds=1)
    fixture = _PublicFixtureOpener({"111": _book_bytes("111", source),
                                   "222": _book_bytes("222", source)})
    polymarket.BoundedPublicOpener = lambda: fixture
    cli = Path(__file__).resolve().parents[1] / "tools" / "d1_compare.py"
    sys.argv[0] = str(cli)
    runpy.run_path(str(cli), run_name="__main__")


@pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES") != "1",
    reason="explicit Linux native PostgreSQL proof is opt-in",
)
def test_capture_cli_native_seals_and_independent_process_reads_exact_bytes(tmp_path):
    from polymarket_alpha_lab.papersim import store_psycopg
    from tests.test_papersim_d1compare_archive_native import _isolated_project, _process_env

    with _isolated_project(tmp_path) as project:
        before = datetime.datetime.now(UTC)
        start = before - datetime.timedelta(minutes=1)
        end = before + datetime.timedelta(minutes=2)

        def process(source, *args):
            result = subprocess.run([sys.executable, "-B", "-c", source, *args],
                                    cwd=Path(__file__).resolve().parents[1], env=_process_env(project),
                                    capture_output=True, timeout=45)
            assert result.returncode == 0, result.stderr.decode()
            assert not result.stderr
            return json.loads(result.stdout)

        def capture(window_start):
            return process(
                "from tests.test_papersim_d1compare_journal import _start_fixture_capture_process; "
                "_start_fixture_capture_process()",
                "capture-inputs", "--repo-root", str(project.root), "--window-start", window_start.isoformat(),
                "--window-end", end.isoformat(), "--tag-slug", "soccer", "--tag-slug", "empty",
                "--allow-public-fetch",
            )

        receipt = capture(start)
        after = datetime.datetime.now(UTC)
        assert receipt["status"] == "sealed" and receipt["reused"] is False
        assert receipt["coverage"]["polymarket_complete"] is True
        assert receipt["coverage"]["uncollected_sources"]
        assert receipt["request_count"] == receipt["response_count"] == 6
        assert before <= datetime.datetime.fromisoformat(receipt["decision_as_of"]) <= after

        restored = process('''
import base64
import json
from pathlib import Path
import sys
from polymarket_alpha_lab.papersim.d1compare import acquire
document, entries = acquire.read_capture(repo_root=Path(sys.argv[1]), batch_sha256=sys.argv[2])
print(json.dumps({"manifest": document, "bodies": [base64.b64encode(e.content).decode() for e in entries],
                  "metadata": [e.request_meta for e in entries]}))
''', str(project.root), receipt["batch_sha256"])
        import base64

        manifest = restored["manifest"]
        records = manifest["requests"]
        bodies = [base64.b64decode(value) for value in restored["bodies"]]
        assert [record["sequence"] for record in records] == list(range(1, 7))
        assert [meta["sequence"] for meta in restored["metadata"]] == list(range(1, 7))
        assert bodies[:4] == [b' [ ] \n', _GAMMA_PAGE, _GAMMA_PAGE, b' [ ] \n']
        assert [record["content_sha256"] for record in records] == [hashlib.sha256(body).hexdigest()
                                                                   for body in bodies]
        assert [record["byte_length"] for record in records] == list(map(len, bodies))
        assert records[1]["utc_fetched_at"] < records[2]["utc_fetched_at"]
        assert records[0]["page"]["event_count"] == 0 and records[3]["page"]["event_count"] == 0
        raw_reference = records[1]["page"]["markets"][0]
        assert raw_reference["path"] == "/0/markets/0"
        assert raw_reference["event_id"] == "event-12" and raw_reference["market_id"] == "market-34"
        assert raw_reference["condition_id"] == _CONDITION
        assert raw_reference["outcomes"] == ["No", "Yes"] and raw_reference["token_ids"] == ["111", "222"]
        assert manifest["markets"][0]["side_tokens"] == {"YES": "222", "NO": "111"}
        assert {record["request_meta"]["query"]["token_id"] for record in records[4:]} == {"111", "222"}
        for record, body in zip(records[4:], bodies[4:], strict=True):
            source = datetime.datetime.fromisoformat(record["source_observed_at"])
            fetched = datetime.datetime.fromisoformat(record["utc_fetched_at"])
            assert source <= fetched <= datetime.datetime.fromisoformat(receipt["decision_as_of"])
            assert body == _book_bytes(record["request_meta"]["query"]["token_id"], source)

        retry = process('''
from datetime import datetime
import json
from pathlib import Path
import sys
from polymarket_alpha_lab.papersim.adapters import polymarket
from polymarket_alpha_lab.papersim.d1compare import acquire
def forbid():
    raise AssertionError("a sealed retry opened the public adapter")
polymarket.BoundedPublicOpener = forbid
print(json.dumps(acquire.capture_inputs(repo_root=Path(sys.argv[1]), window_start=datetime.fromisoformat(sys.argv[2]),
    window_end=datetime.fromisoformat(sys.argv[3]), tag_slugs=("empty", "soccer"), allow_public_fetch=True)))
''', str(project.root), start.isoformat(), end.isoformat())
        assert retry == dict(receipt, reused=True)

        with store_psycopg.connect_managed(project.root) as connection:
            registered = connection.execute("SELECT content_sha256 FROM papersim.snapshots").fetchall()
            assert {row[0] for row in registered} == {receipt["batch_sha256"], *(r["content_sha256"] for r in records)}
            for relation in ("scan_runs", "candidates", "entries", "candidate_marks", "market_outcomes",
                             "settlements", "bankroll_events"):
                assert connection.execute(f"SELECT count(*) FROM papersim.{relation}").fetchone() == (0,)

        next_receipt = capture(start + datetime.timedelta(seconds=1))
        assert next_receipt["batch_sha256"] != receipt["batch_sha256"]
        assert next_receipt["reused"] is False
        from polymarket_alpha_lab.papersim.d1compare import acquire

        _, next_entries = acquire.read_capture(repo_root=project.root, batch_sha256=next_receipt["batch_sha256"])
        assert next_entries[1].url == records[1]["url"] and next_entries[1].content == bodies[1]
        assert next_entries[1].utc_fetched_at > datetime.datetime.fromisoformat(records[1]["utc_fetched_at"])
        with store_psycopg.connect_managed(project.root) as connection:
            assert connection.execute("SELECT fetched_at FROM papersim.snapshots WHERE content_sha256 = %s",
                                      (records[1]["content_sha256"],)).fetchone() == (
                                          datetime.datetime.fromisoformat(records[1]["utc_fetched_at"]),)
