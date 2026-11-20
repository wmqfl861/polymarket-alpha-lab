"""D1 capture-journal offline proofs (synthetic fixtures only).

Everything here verifies CODE over synthetic byte fixtures: no network
call, no database, no timer, no managed write, and no frozen/mirror
script execution ever happens.  Synthetic fixtures are code
verification only -- no synthetic or historical cycle counts as a real
operation, and nothing here claims a positive D1 day.
"""

from __future__ import annotations

import datetime
import json

import pytest

from polymarket_alpha_lab.papersim.d1compare import journal

UTC = datetime.timezone.utc
STAMP = datetime.datetime(2026, 11, 20, 0, 5, tzinfo=UTC)


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
