"""Pure FX-40 assertions. These vectors represent synthetic sealed captures."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from urllib.parse import urlencode

import pytest

from polymarket_alpha_lab import replay
from polymarket_alpha_lab.papersim import crypto_model, gates, pipeline, prepare, scan, soccer_model
from polymarket_alpha_lab.papersim.adapters import contracts, polymarket, prices, understat
from polymarket_alpha_lab.papersim.d1compare import acquire, journal, packbuild
from polymarket_alpha_lab.papersim.optimize.families import crypto as family
from polymarket_alpha_lab.papersim.optimize.families.soccer_frozen import SOCCER_FROZEN
from polymarket_alpha_lab.papersim.optimize.provider import LineageSpecs

from tests.test_papersim_d1compare_journal import _GAMMA_PAGE
from tests.test_papersim_soccer_model import _mini_snapshot


CUTOFF = datetime(2026, 11, 20, 0, 5, tzinfo=UTC)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
POLICY = prepare.BookAgePolicy(max_source_age_seconds=10, max_receipt_age_seconds=10)


def _json_bytes(item):
    return b" \n" + json.dumps(item, indent=2, allow_nan=False).encode("ascii") + b"\n"


def _book(token, condition_id, source_time, bids, asks):
    return {
        "asset_id": token, "market": condition_id,
        "timestamp": str((source_time - EPOCH) // timedelta(milliseconds=1)),
        "bids": [{"price": price, "size": size} for price, size in bids],
        "asks": [{"price": price, "size": size} for price, size in asks],
    }


def _public_capture(*, soccer=False, repeat_gamma=False, late_buy_book=False, gamma_fields=None):
    # The reviewed FX-40 vector derives from the existing reversed-label
    # journal fixture, retains the nested page and prices YES and NO separately.
    page = json.loads(_GAMMA_PAGE)
    event, = page
    market, = event["markets"]
    no_token, yes_token = json.loads(market["clobTokenIds"])
    condition_id = market["conditionId"]
    tag = "epl" if soccer else "crypto"
    if soccer:
        market.update(question="Will Alpha versus Beta have more than 2.5 goals?",
                      description="Yes pays when the match has more than 2.5 goals in regulation.",
                      gameStartTime=(CUTOFF + timedelta(hours=2)).isoformat(), line="2.5")
    else:
        market.update(question="Will Ethereum be below 2,000 on the settlement date?",
                      description="Ethereum terminal below 2,000.",
                      outcomePrices='["0.85","0.15"]')
    market["liquidityNum"] = 6000.0
    market.update(gamma_fields or {})
    query = {"closed": "false", "limit": "50", "tag_slug": tag}
    gamma = polymarket.GAMMA_EVENTS_URL + "?" + urlencode({**query, "offset": "0"})
    terminal_page = polymarket.GAMMA_EVENTS_URL + "?" + urlencode({**query, "offset": "50"})
    no_time, yes_time = CUTOFF - timedelta(seconds=8), CUTOFF - timedelta(seconds=7)
    no_book = _book(no_token, condition_id, no_time,
                    (("0.60", "180.00"), ("0.64", "490.00")),
                    (("0.71", "1200.00"), ("0.69", "5000.00")))
    yes_book = _book(yes_token, condition_id, yes_time,
                     (("0.29", "1200.00"), ("0.31", "5000.00")),
                     (("0.40", "180.00"), ("0.36", "490.00")))
    responses = [
        (gamma, 12, None, page), (terminal_page, 10, None, []),
        (f"{polymarket.CLOB_BOOK_URL}?token_id={no_token}", -1 if late_buy_book else 6, no_time, no_book),
        (f"{polymarket.CLOB_BOOK_URL}?token_id={yes_token}", 5, yes_time, yes_book),
    ]
    if repeat_gamma:
        responses.extend(((gamma, 4, None, page), (gamma, -1, None, page)))

    start, end = CUTOFF - timedelta(minutes=1), CUTOFF + timedelta(seconds=1)
    rows, entries = [], []
    for sequence, (url, age, observed, payload) in enumerate(responses, 1):
        source, meta = acquire._request_fields(url)
        receipt = CUTOFF - timedelta(seconds=age)
        raw = _json_bytes(payload)
        attempt = acquire._Request(
            sequence=sequence, url=url, source_kind=source, request_meta=meta,
            started_at=receipt - timedelta(milliseconds=5), sent=True, http_status=200,
            fetched_at=receipt, finished_at=receipt, source_observed_at=observed,
            source_time_status="receipt_only" if observed is None else "upstream",
            page=journal.gamma_page_index(raw) if observed is None else None,
            byte_length=len(raw), content_sha256=sha256(raw).hexdigest(),
        )
        row = attempt.document(window_start=start, window_end=end, decision_as_of=CUTOFF)
        fields = ("sequence", "source_kind", "source_observed_at", "status", "source_time_status",
                  "window_status", "reason_code", "freshness")
        entry_meta = {**meta, **{key: row[key] for key in fields if key in row}}
        entries.append(journal.capture(url, receipt, entry_meta, raw, content_sha256=row["content_sha256"]))
        rows.append(row)
    index_row, = rows[0]["page"]["markets"]
    binding = {"condition_id": condition_id, "market_id": market["id"], "slug": market["slug"],
               "side_tokens": {"YES": yes_token, "NO": no_token}, "binding_status": "explicit_yes_no",
               "observations": [{"sequence": number, "path": index_row["path"]}
                                for number in ((1, 5, 6) if repeat_gamma else (1,))]}
    manifest = {
        "capture_version": 2,
        "scope": {"tag_slugs": [tag], "window_start": start.isoformat(), "window_end": end.isoformat()},
        "started_at": start.isoformat(), "sealed_at": CUTOFF.isoformat(), "decision_as_of": CUTOFF.isoformat(),
        "requests": rows, "coverage": {"window_status": "sealed_inside"}, "markets": [binding],
        "books": [{"condition_id": condition_id, "token_id": token, "request_sequence": sequence,
                   "reason_code": None} for token, sequence in ((no_token, 3), (yes_token, 4))],
    }
    return manifest, tuple(entries), index_row["terms_sha256"]


def _crypto_inputs():
    fetched = CUTOFF - timedelta(seconds=20)
    stop = CUTOFF.replace(minute=0)
    start = stop - timedelta(days=30)
    bars, rows = [], []
    for index in range(120):
        opened = start + timedelta(hours=6 * index)
        closed = opened + timedelta(hours=6)
        price = 1900.0 + 7 * (index % 13) + 1.5 * index
        bars.append(prices.ClosedBar(opened, closed, price, price, price, price, 1.0))
        start_ms = (opened - EPOCH) // timedelta(milliseconds=1)
        end_ms = (closed - EPOCH) // timedelta(milliseconds=1)
        rows.append([start_ms, str(price), str(price), str(price), str(price), "1", end_ms - 1,
                     str(price), 0, "0", "0", "0"])
    candles = prices.CandleWindowObservation(
        "ETH", "binance", "binance", "ETHUSDT", "USDT", 6, start, stop, start, stop,
        tuple(bars), 120, 120, 0, 0, 0, 0, fetched,
    )
    spot = prices.SpotObservation("ETH", "binance", "binance", "ETHUSDT", "USDT", 2188.0, fetched)
    candle_url = f"{prices._BINANCE_BASE_URL}/klines?" + urlencode({
        "symbol": "ETHUSDT", "interval": "6h", "limit": len(rows),
        "startTime": rows[0][0], "endTime": rows[-1][6],
    })
    evidence = (
        contracts.FetchEvidence.from_bytes(source_kind="crypto-candles-binance", source_reference=candle_url,
                                           fetched_at=fetched, raw_bytes=_json_bytes(rows)),
        contracts.FetchEvidence.from_bytes(source_kind="crypto-spot-binance",
                                           source_reference=f"{prices._BINANCE_BASE_URL}/ticker/price?symbol=ETHUSDT",
                                           fetched_at=fetched,
                                           raw_bytes=_json_bytes({"symbol": "ETHUSDT", "price": "2188.0"})),
    )
    return prepare.CryptoInputs(candles, spot, 2000.0, evidence, yes_when="below")


def _soccer_inputs():
    snapshot = {key: block for key, block in _mini_snapshot().items() if key.startswith("EPL_")}
    evidence = []
    for key, block in snapshot.items():
        season = key.split("_")[1]
        for match in block["dates"]:
            match["datetime"] = f"{season}-11-01 17:00:00"
        for team in block["teams"].values():
            for index, match in enumerate(team["history"], 1):
                match["date"] = f"{season}-11-{index:02d} 17:00:00"
        evidence.append(contracts.FetchEvidence.from_bytes(
            source_kind=understat.SOURCE_KIND, source_reference=understat._league_source_reference("EPL", season),
            fetched_at=CUTOFF - timedelta(seconds=20), raw_bytes=_json_bytes(block)))
    return prepare.SoccerInputs(snapshot, "EPL", "EPL", "2026", tuple(evidence))


def _target(terms, *, soccer=False, buy_side=None):
    condition = "0x" + "a" * 64
    if soccer:
        spec = packbuild.PackSpec("soccer-queue", "soccer-test", "club-soccer", "soccer")
        plan = packbuild.MarketPlan("market-34", "total", "over", "Alpha v Beta", buy_token="222",
                                    line=2.5, league="EPL", home="Alpha", away="Beta", fee_category="sports")
        return prepare.MarketTarget(spec, plan, condition, "epl", terms, "YES", _soccer_inputs())
    side = buy_side or "NO"
    spec = packbuild.PackSpec("crypto-queue", "crypto-test", "crypto", "crypto")
    plan = packbuild.MarketPlan("market-34", "crypto_terminal", side.lower(), "ETH-2000",
                                buy_token="111" if side == "NO" else "222", asset="ETH",
                                market_class="crypto", fee_category="crypto")
    return prepare.MarketTarget(spec, plan, condition, "crypto", terms, side, _crypto_inputs())


def _prepare(manifest, entries, targets, *, active=None, policy=POLICY, digest=None):
    soccer_spec, crypto_spec = SOCCER_FROZEN.baseline_spec(), family.CRYPTO_GBM.baseline_spec()
    specs = {"crypto": LineageSpecs(active or crypto_spec, crypto_spec),
             "club-soccer": LineageSpecs(soccer_spec, soccer_spec)}
    return prepare.prepare_market_pack(
        batch_sha256=digest or sha256(acquire._canonical(manifest)).hexdigest(), manifest=manifest,
        entries=entries, targets=targets, specs=specs, book_age_policy=policy,
    )


def test_reversed_gamma_uses_no_book_and_writes_ordered_crypto_quotes():
    manifest, entries, terms = _public_capture(repeat_gamma=True)
    target = _target(terms)
    prepared = _prepare(manifest, entries, (target,))
    assert prepared.byte_length == len(prepared.content)
    assert prepared.content_sha256 == sha256(prepared.content).hexdigest()
    assert prepared.excluded == ()
    row, = json.loads(prepared.content)
    assert packbuild.verify_pack_document({"pack_version": 1, "pack_sha256": prepared.content_sha256,
                                          "records": [row]}) == prepared.content_sha256
    parsed, = scan.records_from_pack(prepared.content)
    assert parsed.side == "NO"
    assert (parsed.yes_token_id, parsed.no_token_id) == ("222", "111")
    assert parsed.book_snapshot_sha256 == parsed.decision_snapshot_sha256 == entries[2].content_sha256
    assert parsed.source_ref == entries[2].source_ref()
    assert parsed.snapshot_refs[:3] == tuple(entries[index].content_sha256 for index in (4, 2, 3))
    prov = row["prepared_provenance"]
    gamma, no, yes = prov["sources"][:3]
    assert (gamma["path"], gamma["event_id"], gamma["sequence"]) == ("/0/markets/0", "event-12", 5)
    assert (no["token_id"], no["sequence"], yes["token_id"], yes["sequence"]) == ("111", 3, "222", 4)
    assert gamma["content_sha256"] == entries[0].content_sha256 == entries[5].content_sha256
    assert gamma["utc_fetched_at"] == entries[4].utc_fetched_at.isoformat()

    request = replay.request_from_replay_bundle(parsed.request_bundle)
    assert request.buy_side == "no" and request.buy_token == "111"
    assert request.yes_ask == 0.36 and request.no_ask == 0.69
    assert request.book == [[0.69, 5000.0], [0.71, 1200.0]]
    assert request.book_time == entries[2].utc_fetched_at and request.now == CUTOFF
    assert request.yes_when == "below"
    sigma = crypto_model.realized_vol_daily(target.materials.candles.closes)
    expiry = datetime(2026, 12, 31, tzinfo=UTC)
    reference = crypto_model.terminal_probability(2188.0, sigma, 2000.0,
                                                  (expiry - CUTOFF).total_seconds() / 3600, asset="ETH")
    assert request.probability == reference
    assert parsed.variant_quotes == [1.0 - reference.convention_a, 1.0 - reference.convention_b]
    assert parsed.median_probability == 1.0 - max(reference.convention_a, reference.convention_b)
    assert prov["model_spec_sha256"] == family.CRYPTO_GBM.baseline_spec().spec_sha256()


def test_prepared_bytes_are_stable_over_enumeration(monkeypatch):
    manifest, entries, terms = _public_capture(repeat_gamma=True)
    no, yes = _target(terms), _target(terms, buy_side="YES")
    original = _prepare(manifest, entries, (yes, no))
    assert [row["side"] for row in json.loads(original.content)] == ["NO", "YES"]
    monkeypatch.setattr(acquire, "_now_utc", lambda: CUTOFF + timedelta(days=60))
    unordered = replace(no, materials=replace(no.materials, evidence=no.materials.evidence[::-1]))
    repeated = _prepare(manifest, entries[::-1], (unordered, yes))
    assert repeated == original


def test_crypto_fit_uses_the_selected_spec_and_requires_its_full_window():
    manifest, entries, terms = _public_capture()
    target = _target(terms)
    active = family.spec_with(vol_weights=family.WEIGHT_SINGLE_7D)
    updated = _prepare(manifest, entries, (target,), active=active)
    packed, = json.loads(updated.content)
    request = replay.request_from_replay_bundle(packed["request_bundle"])
    sigma = crypto_model.realized_vol_daily(target.materials.candles.closes[-28:])
    expiry = datetime(2026, 12, 31, tzinfo=UTC)
    reference = crypto_model.terminal_probability(2188.0, sigma, 2000.0,
                                                  (expiry - CUTOFF).total_seconds() / 3600, asset="ETH")
    assert request.probability == reference
    assert packed["prepared_provenance"]["model_spec_sha256"] == active.spec_sha256()
    assert packed["prepared_provenance"]["model_version"] == active.model_version()
    baseline, = scan.records_from_pack(_prepare(manifest, entries, (target,)).content)
    assert request.probability != replay.request_from_replay_bundle(baseline.request_bundle).probability

    needs_more_history = family.spec_with(vol_weights=family.WEIGHT_MIX_30_60)
    incomplete = _prepare(manifest, entries, (target,), active=needs_more_history)
    assert incomplete.content == b"[]"
    assert incomplete.excluded[0].reason_code == "papersim_prepare_crypto_model_inputs_invalid"


def test_terminal_above_bundle_keeps_the_raw_below_probability():
    manifest, entries, terms = _public_capture(gamma_fields={
        "question": "Will Ethereum be above 2,000 on the settlement date?",
        "description": "Ethereum terminal above 2,000.",
    })
    target = _target(terms, buy_side="YES")
    target = replace(target, materials=replace(target.materials, yes_when="above"))
    prepared = _prepare(manifest, entries, (target,))
    record, = scan.records_from_pack(prepared.content)
    request = replay.request_from_replay_bundle(record.request_bundle)
    assert type(request.probability) is crypto_model.TerminalProbability
    assert request.yes_when == request.contract_direction == "above"
    assert record.variant_quotes == [1.0 - request.probability.convention_a,
                                     1.0 - request.probability.convention_b]
    assert record.median_probability == min(record.variant_quotes)


@pytest.mark.parametrize("mode, reason", (
    ("late_book", "papersim_prepare_book_after_cutoff"),
    ("missing_policy", "d1_capture_book_age_policy_unregistered"),
    ("stale_book", "d1_capture_book_stale"),
    ("late_model", "papersim_prepare_model_evidence_after_cutoff"),
    ("future_candles", "papersim_prepare_model_evidence_after_cutoff"),
    ("unproven_terms", "papersim_prepare_market_terms_mismatch"),
    ("wrong_token", "papersim_prepare_buy_token_mismatch"),
))
def test_intact_but_ineligible_evidence_omits_the_target(mode, reason):
    manifest, entries, terms = _public_capture(late_buy_book=mode == "late_book")
    target, policy = _target(terms), POLICY
    if mode == "missing_policy":
        policy = None
    elif mode == "stale_book":
        policy = prepare.BookAgePolicy(max_source_age_seconds=7, max_receipt_age_seconds=10)
    elif mode == "late_model":
        first, second = target.materials.evidence
        evidence = (first, replace(second, fetched_at=CUTOFF + timedelta(seconds=1)))
        target = replace(target, materials=replace(target.materials, evidence=evidence))
    elif mode == "future_candles":
        candles = target.materials.candles
        early = replace(candles, fetched_at=candles.covered_end - timedelta(seconds=1))
        target = replace(target, materials=replace(target.materials, candles=early))
    elif mode == "unproven_terms":
        target = replace(target, terms_sha256="b" * 64)
    elif mode == "wrong_token":
        target = replace(target, plan=replace(target.plan, buy_token="222"))
    result = _prepare(manifest, entries, (target,), policy=policy)
    assert result.content == b"[]"
    refusal, = result.excluded
    assert refusal.reason_code == reason
    assert refusal.source_refs[0]["content_sha256"] == entries[0].content_sha256
    if mode == "late_book":
        book = refusal.source_refs[1]
        assert datetime.fromisoformat(book["source_observed_at"]) < CUTOFF
        assert datetime.fromisoformat(book["utc_fetched_at"]) > CUTOFF


def test_inconsistent_capture_and_spot_projection_fail_before_returning_any_pack():
    manifest, entries, terms = _public_capture()
    target = _target(terms)
    with pytest.raises(prepare.PreparationError, match="papersim_prepare_capture_digest_mismatch"):
        _prepare(manifest, entries, (target,), digest="b" * 64)
    with pytest.raises(prepare.PreparationError, match="papersim_prepare_capture_body_missing"):
        _prepare(manifest, entries[:-1], (target,))
    tampered = (replace(entries[0], content=b"[]"), *entries[1:])
    with pytest.raises(prepare.PreparationError, match="papersim_prepare_capture_journal_invalid"):
        _prepare(manifest, tampered, (target,))
    different_spot = replace(target.materials.spot, price=2300.0)
    target = replace(target, materials=replace(target.materials, spot=different_spot))
    with pytest.raises(prepare.PreparationError, match="papersim_prepare_crypto_projection_mismatch"):
        _prepare(manifest, entries, (target,))


def test_soccer_quote_keeps_all_ten_variants_and_a_real_constructor_refusal_replays():
    manifest, entries, terms = _public_capture(soccer=True)
    target = _target(terms, soccer=True)
    prepared = _prepare(manifest, entries, (target,))
    record, = scan.records_from_pack(prepared.content)
    actual = replay.request_from_replay_bundle(record.request_bundle)
    reference = soccer_model.candidate_for_market(
        target.materials.xg_snapshot, "Alpha", "EPL", "Beta", "EPL", "Alpha v Beta", "market-34",
        gates.walk_book([(0.36, 490.0), (0.40, 180.0)]).vwap,
        kind="total", side="over", line=2.5, season="2026",
    ).candidate
    assert len(record.variant_quotes) == 10
    assert record.variant_quotes == list(reference.qs_variants)
    assert record.median_probability == reference.model_median
    assert replay.replay_bundle_payload(replace(actual, model_quote=reference)) == record.request_bundle
    assert not pipeline.decide_entry(actual).accepted

    unknown_away = replace(target, plan=replace(target.plan, away="Absent"))
    refusal = _prepare(manifest, entries, (unknown_away,))
    failed_record, = scan.records_from_pack(refusal.content)
    failure = scan._bind_construction(failed_record)
    assert failure.conclusion == "CONSTRUCTION_FAILED"
    assert len(json.loads(failure.snapshot_refs)) >= 3
    assert replay._run_constructor(json.loads(failure.input_payload)) == (replay.CONSTRUCTION_RETURNED_NONE, "")


def test_soccer_model_ignores_future_scheduled_rows_and_refuses_future_results():
    manifest, entries, terms = _public_capture(soccer=True)
    target = _target(terms, soccer=True)
    future = {"datetime": (CUTOFF + timedelta(hours=1)).isoformat(), "isResult": False,
              "xG": {"h": 1.0, "a": 1.0}}
    current = target.materials.xg_snapshot["EPL_2026"]
    current["dates"].append(future)
    assert _prepare(manifest, entries, (target,)).excluded == ()
    future["isResult"] = True
    refused = _prepare(manifest, entries, (target,))
    assert refused.content == b"[]"
    assert refused.excluded[0].reason_code == "papersim_prepare_xg_after_cutoff"
