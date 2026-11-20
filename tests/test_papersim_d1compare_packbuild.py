"""D1 journal-to-pack offline proofs (synthetic fixtures only).

Everything here verifies CODE over synthetic journal fixtures: no
network call, no database, no timer, no managed write and no
frozen/mirror script execution.  Synthetic fixtures are code
verification only -- no synthetic or historical cycle counts as a real
operation, and nothing here claims a positive D1 day.
"""

from __future__ import annotations

import datetime
import json

import pytest

from polymarket_alpha_lab import replay
from polymarket_alpha_lab.papersim import scan
from polymarket_alpha_lab.papersim.d1compare import journal, packbuild
from polymarket_alpha_lab.papersim.pipeline import EntryRequest

UTC = datetime.timezone.utc
STAMP = datetime.datetime(2026, 11, 20, 0, 5, tzinfo=UTC)


def _entry(url: str, payload: dict) -> journal.JournalEntry:
    return journal.capture(url=url, utc_fetched_at=STAMP, request_meta={},
                           content=json.dumps(payload).encode())


def _soccer_journal_entries() -> list[journal.JournalEntry]:
    market = _entry("https://gamma-api.polymarket.com/markets/6237097", {
        "id": "6237097",
        "question": "Will Lens vs. Marseille end in a draw?",
        "outcomes": '["Yes","No"]',
        "clobTokenIds": '["111","222"]',
        "conditionId": "0xabc",
        "slug": "lens-marseille-draw",
        "gameStartTime": "2026-11-08T19:45:00Z",
        "endDate": "2026-11-08T19:45:00Z",
        "liquidityNum": 51000.0,
    })
    yes_book = _entry("https://clob.polymarket.com/book?token_id=111", {
        "asset_id": "111",
        "bids": [{"price": "0.25", "size": "1000"}],
        "asks": [{"price": "0.27", "size": "2000"}],
    })
    no_book = _entry("https://clob.polymarket.com/book?token_id=222", {
        "asset_id": "222",
        "bids": [{"price": "0.72", "size": "30000"}],
        "asks": [{"price": "0.74", "size": "29463"}],
    })
    return [market, yes_book, no_book]


def _soccer_observation() -> journal.MarketObservation:
    market, yes_book, no_book = _soccer_journal_entries()
    return journal.market_from_entries(market, [yes_book, no_book],
                                       league_key="Ligue_1")


def _soccer_plan() -> packbuild.MarketPlan:
    return packbuild.MarketPlan(
        market_id="6237097", market_kind="ml", side="no",
        fixture="Lens vs. Marseille", league="Ligue_1",
        home="Lens", away="Marseille", line=None,
        gamma_event_id="99001", event_title="Lens vs. Marseille",
        market_class=packbuild.MARKET_CLASS_SPORTS)


SPEC = packbuild.PackSpec(queue="club-soccer/match_winner", team="pal-d1",
                          lineage="club-soccer/match_winner", model_name="soccer",
                          market_type="match_winner")


# ---------------------------------------------------------------------------
# EntryRequest construction (existing field set, existing codec)
# ---------------------------------------------------------------------------


def test_build_entry_request_uses_existing_fields_and_ladder() -> None:
    request = packbuild.build_entry_request(_soccer_observation(), _soccer_plan())
    assert isinstance(request, EntryRequest)
    assert request.market_id == "6237097"
    assert request.question == "Will Lens vs. Marseille end in a draw?"
    assert request.fixture == "Lens vs. Marseille"
    assert request.market_kind == "ml"
    assert request.side == "no"
    # the RAW ask ladder of the bought NO token, not a midpoint summary
    assert list(request.book) == [(0.74, 29463.0)]
    assert request.book_time == STAMP
    assert request.now == STAMP
    assert request.gamma_liquidity == 51000.0
    # model/news evidence is unknown at pack time, never fabricated
    assert request.model_quote is None
    assert request.news is None
    assert request.probability is None


def test_build_entry_request_refusals() -> None:
    observation = _soccer_observation()
    wrong_id = packbuild.MarketPlan(market_id="other", market_kind="ml",
                                    side="no", fixture="f")
    with pytest.raises(packbuild.PackBuildError) as error:
        packbuild.build_entry_request(observation, wrong_id)
    assert error.value.code == "pack_market_id_mismatch"
    no_book = packbuild.MarketPlan(market_id="6237097", market_kind="ml",
                                   side="yes", fixture="f")
    observation_without_books = journal.market_from_entries(
        observation.market_entry, [], league_key="Ligue_1")
    with pytest.raises(packbuild.PackBuildError) as error:
        packbuild.build_entry_request(observation_without_books, no_book)
    assert error.value.code in ("pack_buy_token_ambiguous", "pack_book_missing")
    ambiguous = packbuild.MarketPlan(market_id="6237097", market_kind="ml",
                                     side="maybe", fixture="f")
    with pytest.raises(packbuild.PackBuildError) as error:
        packbuild.build_entry_request(observation, ambiguous)
    assert error.value.code == "pack_buy_token_ambiguous"


def test_events_materials_sports_and_crypto() -> None:
    materials = packbuild.events_materials(_soccer_observation(), _soccer_plan())
    assert materials["market_class"] == "sports"
    assert materials["gamma_event_id"] == "99001"
    assert materials["league"] == "Ligue_1"
    assert materials["home"] == "Lens" and materials["away"] == "Marseille"
    assert materials["kickoff_day"] == "2026-11-08"
    assert materials["expiry_at"] == "2026-11-08T19:45:00+00:00"
    assert materials["market_ref"] == "6237097"

    market = _entry("https://gamma-api.polymarket.com/markets/m-eth", {
        "id": "m-eth", "question": "Ethereum below $2,000?",
        "outcomes": '["Yes","No"]', "clobTokenIds": '["31","32"]',
        "endDate": "2026-12-31T00:00:00Z"})
    book = _entry("https://clob.polymarket.com/book?token_id=31", {
        "asset_id": "31", "bids": [{"price": "0.14", "size": "500"}],
        "asks": [{"price": "0.15", "size": "5000"}]})
    observation = journal.market_from_entries(market, [book])
    plan = packbuild.MarketPlan(
        market_id="m-eth", market_kind="crypto_terminal", side="yes",
        fixture="ETH-2000", asset="ETH", buy_token="31",
        market_class=packbuild.MARKET_CLASS_CRYPTO, fee_category="crypto")
    materials = packbuild.events_materials(observation, plan)
    assert materials["market_class"] == "crypto"
    assert materials["asset"] == "ETH"
    assert materials["expiry_at"] == "2026-12-31T00:00:00+00:00"
    assert materials["kickoff_day"] is None


# ---------------------------------------------------------------------------
# pack record contract (scan.py authority) and provenance
# ---------------------------------------------------------------------------


def test_build_pack_record_conforms_to_scan_contract() -> None:
    record = packbuild.build_pack_record(_soccer_observation(), _soccer_plan(),
                                         SPEC)
    assert record["source_ref"] == (
        "https://gamma-api.polymarket.com/markets/6237097#"
        + _soccer_observation().market_entry.content_sha256)
    assert record["book_snapshot_sha256"] == (
        _soccer_observation().books["222"].content_sha256)
    assert record["events"]["market_class"] == "sports"
    assert record["median_probability"] is None
    # the B1 loader is the contract authority: the record must parse
    parsed = scan.records_from_pack(json.dumps([record]))
    assert len(parsed) == 1
    assert parsed[0].market_id == "6237097"
    assert parsed[0].queue == SPEC.queue
    assert parsed[0].book_snapshot_sha256 == record["book_snapshot_sha256"]
    assert parsed[0].source_ref == record["source_ref"]
    assert parsed[0].events is not None and parsed[0].events.league == "Ligue_1"
    # the bundle decodes through the existing codec (tuples encode as
    # JSON arrays: the ladder comes back as lists)
    request = replay.request_from_replay_bundle(record["request_bundle"])
    assert request.market_id == "6237097"
    assert list(request.book) == [[0.74, 29463.0]]


def test_build_pack_provenance_and_document_verification() -> None:
    observation = _soccer_observation()
    entries = _soccer_journal_entries()
    records, provenance = packbuild.build_pack(
        [observation], [_soccer_plan()], SPEC, journal_entries=entries)
    assert provenance.record_count == 1
    assert provenance.record_source_refs == (records[0]["source_ref"],)
    assert provenance.journal_sha256 == journal.journal_digest(entries)
    document = packbuild.pack_document(records, provenance)
    assert packbuild.verify_pack_document(document) == provenance.pack_sha256
    tampered = dict(document, pack_sha256="0" * 64)
    with pytest.raises(packbuild.PackBuildError) as error:
        packbuild.verify_pack_document(tampered)
    assert error.value.code == "pack_digest_mismatch"
    # a tampered record fails the digest check (any content change moves
    # the digest); the B1 loader authority fires on loader-invalid rows
    bad_record = dict(records[0], queue="")
    with pytest.raises(packbuild.PackBuildError) as error:
        packbuild.verify_pack_document(
            packbuild.pack_document([bad_record], provenance))
    assert error.value.code == "pack_digest_mismatch"
    with pytest.raises(scan.ScanError):
        scan.records_from_pack(json.dumps([bad_record]))


def test_observations_from_journal_joins_markets_and_books() -> None:
    entries = _soccer_journal_entries()
    noise = journal.capture(url="https://stooq.test/dl", utc_fetched_at=STAMP,
                            request_meta={}, content=b"DATE,CLOSE")
    entries = [noise, *entries]
    joined = packbuild.observations_from_journal(entries, ["6237097"])
    assert len(joined) == 1
    assert joined[0].market_id == "6237097"
    assert set(joined[0].books) == {"111", "222"}
    with pytest.raises(packbuild.PackBuildError) as error:
        packbuild.observations_from_journal(entries, ["missing"])
    assert error.value.code == "pack_market_not_captured"
    league = packbuild.observations_from_journal(entries, ["6237097"],
                                                 league_keys={"6237097": "Ligue_1"})
    assert league[0].league_key == "Ligue_1"


def test_missing_question_capture_is_refused_not_defaulted() -> None:
    market = _entry("https://gamma-api.polymarket.com/markets/q", {
        "id": "q", "question": None, "outcomes": '["Yes","No"]',
        "clobTokenIds": '["111","222"]'})
    book = _entry("https://clob.polymarket.com/book?token_id=111", {
        "asset_id": "111", "bids": [], "asks": [{"price": "0.5", "size": "1"}]})
    observation = journal.market_from_entries(market, [book])
    plan = packbuild.MarketPlan(market_id="q", market_kind="total", side="yes",
                                fixture="f", buy_token="111")
    with pytest.raises(packbuild.PackBuildError) as error:
        packbuild.build_entry_request(observation, plan)
    assert error.value.code == "pack_question_missing"
