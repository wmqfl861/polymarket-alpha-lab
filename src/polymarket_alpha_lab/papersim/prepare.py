"""Build a B1 market pack from sealed D1 evidence and a frozen spec view."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from polymarket_alpha_lab import replay
from polymarket_alpha_lab.papersim import crypto_model, gates, pipeline, soccer_model
from polymarket_alpha_lab.papersim.adapters import contracts, polymarket, prices
from polymarket_alpha_lab.papersim.d1compare import acquire, journal, packbuild
from polymarket_alpha_lab.papersim.optimize.families.crypto import CRYPTO_GBM, DATA_CONTRACT, CryptoFamilyHistory
from polymarket_alpha_lab.papersim.optimize.families.soccer_frozen import SOCCER_FROZEN
from polymarket_alpha_lab.papersim.optimize.provider import LineageSpecs
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec


class PreparationError(ValueError):
    """The sealed input is missing, inconsistent, or corrupt."""

    @property
    def code(self) -> str:
        return str(self)


class _Excluded(ValueError):
    """An intact input does not support preparing this target."""


@dataclass(frozen=True)
class BookAgePolicy:
    """Limits from the managed caller's registered interval policy."""

    max_source_age_seconds: int
    max_receipt_age_seconds: int

    def __post_init__(self) -> None:
        for age in (self.max_source_age_seconds, self.max_receipt_age_seconds):
            if type(age) is not int or age < 0:
                raise PreparationError("d1_capture_book_age_policy_invalid")


@dataclass(frozen=True)
class SoccerInputs:
    """Season and fixture projections reconstructed from registered originals."""

    xg_snapshot: Mapping[str, object]
    home_league: str
    away_league: str
    season: str
    evidence: tuple[contracts.FetchEvidence, ...]
    team_side: str | None = None
    bridge: Mapping[str, float] | None = None
    o25_hits: int | None = None
    lineup_confirmation: float | None = None


@dataclass(frozen=True)
class CryptoInputs:
    """Registered A2 projections and the explicit, reviewed contract terms."""

    candles: prices.CandleWindowObservation
    spot: prices.SpotObservation
    barrier: float
    evidence: tuple[contracts.FetchEvidence, ...]
    yes_when: str | None = None
    touch_direction: str | None = None


@dataclass(frozen=True)
class MarketTarget:
    """A sealed selection of a queue, public contract and bought side.

    The caller binds the plan's model selection to the full Gamma terms
    fingerprint, including its exact question and description. ``buy_side``
    names the literal public YES/NO token. ``plan.side`` names the constructor
    proposition (for example ``over``) and must carry the reviewed mapping.
    """

    pack_spec: packbuild.PackSpec
    plan: packbuild.MarketPlan
    condition_id: str
    tag_slug: str
    terms_sha256: str
    buy_side: str
    materials: SoccerInputs | CryptoInputs | None = None
    news: gates.NewsCheck | None = None
    news_evidence: tuple[contracts.FetchEvidence, ...] = ()

    @property
    def candidate_key(self) -> str:
        return f"{self.plan.market_id}|{self.buy_side.lower()}|{self.plan.market_kind}"


@dataclass(frozen=True)
class ExcludedTarget:
    candidate_key: str
    queue: str
    reason_code: str
    source_refs: tuple[dict, ...]


@dataclass(frozen=True)
class PreparedPack:
    """Canonical JSON array bytes and capture context for the scan receipt."""

    content: bytes
    batch_sha256: str
    decision_as_of: datetime
    excluded: tuple[ExcludedTarget, ...]

    @property
    def content_sha256(self) -> str:
        return contracts.sha256_hexdigest(self.content)

    @property
    def byte_length(self) -> int:
        return len(self.content)


def _utc(value: object) -> datetime:
    try:
        stamp = datetime.fromisoformat(value) if type(value) is str else value
        if isinstance(stamp, datetime) and stamp.utcoffset() is not None:
            return stamp.astimezone(UTC)
    except (ValueError, OverflowError):
        pass
    raise PreparationError("papersim_prepare_timestamp_invalid")


@dataclass(frozen=True)
class _Response:
    row: dict
    entry: journal.JournalEntry

    def citation(self, **location: object) -> dict:
        return {
            "source_kind": self.row["source_kind"],
            "sequence": self.row["sequence"],
            "content_sha256": self.entry.content_sha256,
            "byte_length": len(self.entry.content),
            "utc_fetched_at": self.entry.utc_fetched_at.astimezone(UTC).isoformat(),
            "source_observed_at": self.row["source_observed_at"],
            **location,
        }

    def eligible(self, start: datetime, cutoff: datetime) -> bool:
        return (self.row["status"] == "received" and self.row["window_status"] == "in_window"
                and start <= self.entry.utc_fetched_at <= cutoff)


def _capture_entries(manifest: dict, entries: Sequence[journal.JournalEntry]) -> dict[int, _Response]:
    """Match every retained body by request sequence, including repeated bytes."""
    if not journal.verify_journal(entries).ok:
        raise PreparationError("papersim_prepare_capture_journal_invalid")
    by_sequence = {}
    for entry in entries:
        sequence = entry.request_meta.get("sequence")
        if type(sequence) is not int or sequence < 1 or sequence in by_sequence:
            raise PreparationError("papersim_prepare_capture_sequence_invalid")
        by_sequence[sequence] = entry

    responses = {}
    for sequence, row in enumerate(manifest["requests"], 1):
        source, meta = acquire._request_fields(row["url"])
        if (type(row["sequence"]) is not int or row["sequence"] != sequence
                or row["source_kind"] != source or row["request_meta"] != meta):
            raise PreparationError("papersim_prepare_capture_request_invalid")
        digest = row["content_sha256"]
        if digest is None:
            if row["byte_length"] is not None or row["utc_fetched_at"] is not None:
                raise PreparationError("papersim_prepare_capture_request_invalid")
            continue
        entry = by_sequence.pop(sequence, None)
        if entry is None:
            raise PreparationError("papersim_prepare_capture_body_missing")
        fields = ("sequence", "source_kind", "status", "window_status", "source_observed_at",
                  "source_time_status", "reason_code")
        meta.update({key: row[key] for key in fields})
        if "freshness" in row:
            meta["freshness"] = row["freshness"]
        if (entry.url != row["url"] or entry.content_sha256 != digest
                or type(row["byte_length"]) is not int or len(entry.content) != row["byte_length"]
                or entry.request_meta != meta
                or entry.utc_fetched_at != _utc(row["utc_fetched_at"])):
            raise PreparationError("papersim_prepare_capture_body_mismatch")
        responses[sequence] = _Response(row, entry)
    if by_sequence:
        raise PreparationError("papersim_prepare_capture_sequence_invalid")
    return responses


@dataclass
class _Capture:
    manifest: dict
    start: datetime
    cutoff: datetime
    responses: dict[int, _Response]
    markets: dict[str, dict]
    books: dict[tuple[str, str], dict]
    pages: dict[int, dict]

    def page(self, sequence: int) -> dict:
        if sequence not in self.pages:
            raw = journal.decode_public_json(self.responses[sequence].entry.content)
            index = journal._gamma_page_index(raw)
            if index is None:
                raise PreparationError("papersim_prepare_gamma_index_invalid")
            locations = {}
            for row in index["markets"]:
                _, event_index, _, market_index = row["path"].split("/")
                body = raw[int(event_index)]["markets"][int(market_index)]
                locations[row["path"]] = (row, body)
            self.pages[sequence] = locations
        return self.pages[sequence]


def _capture_view(batch_sha256: str, manifest: dict,
                  entries: Sequence[journal.JournalEntry]) -> _Capture:
    try:
        if contracts.sha256_hexdigest(acquire._canonical(manifest)) != batch_sha256:
            raise PreparationError("papersim_prepare_capture_digest_mismatch")
        if type(manifest["capture_version"]) is not int or manifest["capture_version"] != 2:
            raise PreparationError("papersim_prepare_capture_version_unsupported")
        scope = manifest["scope"]
        start, end = _utc(scope["window_start"]), _utc(scope["window_end"])
        started, sealed = _utc(manifest["started_at"]), _utc(manifest["sealed_at"])
        cutoff = _utc(manifest["decision_as_of"])
        if manifest["coverage"]["window_status"] != "sealed_inside" or not start <= started <= cutoff == sealed < end:
            raise PreparationError("papersim_prepare_capture_cutoff_invalid")
        responses = _capture_entries(manifest, entries)
        markets = {row["condition_id"]: row for row in manifest["markets"]}
        books = {(row["condition_id"], row["token_id"]): row for row in manifest["books"]}
        if len(markets) != len(manifest["markets"]) or len(books) != len(manifest["books"]):
            raise PreparationError("papersim_prepare_capture_identity_conflict")
        return _Capture(manifest, start, cutoff, responses, markets, books, {})
    except PreparationError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise PreparationError("papersim_prepare_capture_manifest_invalid") from error


def _gamma_market(capture: _Capture, target: MarketTarget, citations: list[dict]) -> tuple:
    if target.tag_slug not in capture.manifest["scope"]["tag_slugs"]:
        raise _Excluded("papersim_prepare_market_tag_unavailable")
    found = capture.markets.get(target.condition_id)
    if found is None or found["market_id"] != target.plan.market_id:
        raise _Excluded("papersim_prepare_market_identity_unavailable")
    matches = []
    for ref in found["observations"]:
        sequence, path = ref["sequence"], ref["path"]
        response = capture.responses.get(sequence)
        if response is None or response.row["source_kind"] != polymarket.SOURCE_KIND_GAMMA:
            raise PreparationError("papersim_prepare_gamma_location_invalid")
        tag = response.row["request_meta"]["query"]["tag_slug"]
        if tag == target.tag_slug:
            matches.append((response, path))
    if not matches:
        raise _Excluded("papersim_prepare_market_tag_unavailable")
    matches.sort(key=lambda pair: (pair[0].entry.utc_fetched_at, pair[0].row["sequence"], pair[1]), reverse=True)
    if found["binding_status"] != "explicit_yes_no":
        citations.extend(response.citation(path=path) for response, path in matches)
        raise _Excluded(found["binding_status"])
    eligible = [(response, path) for response, path in matches if response.eligible(capture.start, capture.cutoff)]
    if not eligible:
        citations.extend(response.citation(path=path) for response, path in matches)
        raise _Excluded("papersim_prepare_market_after_cutoff")
    appearances = []
    for response, path in eligible:
        try:
            row, payload = capture.page(response.row["sequence"])[path]
        except (KeyError, journal.JournalError) as error:
            raise PreparationError("papersim_prepare_gamma_location_invalid") from error
        observed_sides = journal.gamma_outcomes_by_token(row)
        if (row["condition_id"] != target.condition_id or row["market_id"] != found["market_id"]
                or row["slug"] != found["slug"] or observed_sides != {
                    token: side.lower() for side, token in found["side_tokens"].items()
                }):
            raise PreparationError("papersim_prepare_gamma_identity_mismatch")
        appearances.append((response, row, payload))
    response, row, payload = appearances[0]
    citations.append(response.citation(path=row["path"], tag_slug=target.tag_slug, event_id=row["event_id"]))
    if (not target.terms_sha256 or any(item[1]["terms_sha256"] != target.terms_sha256 for item in appearances)):
        raise _Excluded("papersim_prepare_market_terms_mismatch")
    event_id = row["event_id"]
    if (any(item[1]["event_id"] != event_id for item in appearances)
            or target.plan.gamma_event_id not in (None, event_id)):
        raise _Excluded("papersim_prepare_event_identity_mismatch")
    market, refusal = acquire._parse_gamma_market(payload, response.entry.utc_fetched_at)
    if market is None or refusal is not None:
        raise _Excluded(refusal or "papersim_prepare_market_terms_unavailable")
    tokens = {"YES": market.yes_token_id, "NO": market.no_token_id}
    if target.plan.buy_token not in (None, tokens[target.buy_side]):
        raise _Excluded("papersim_prepare_buy_token_mismatch")
    if market.end_at <= capture.cutoff:
        raise _Excluded("market_expired")
    return market, payload, event_id, tokens


def _book(capture: _Capture, target: MarketTarget, token: str, policy: BookAgePolicy | None,
          citations: list[dict]) -> tuple[_Response, list[tuple[float, float]]]:
    bound = capture.books.get((target.condition_id, token))
    response = capture.responses.get(bound["request_sequence"]) if bound is not None else None
    if response is None:
        raise _Excluded((bound.get("reason_code") if bound else None) or "papersim_prepare_book_missing")
    row = response.row
    if (row["source_kind"] != polymarket.SOURCE_KIND_CLOB
            or row["request_meta"]["query"]["token_id"] != token):
        raise PreparationError("papersim_prepare_book_reference_mismatch")
    citations.append(response.citation(token_id=token))
    if response.entry.utc_fetched_at > capture.cutoff:
        raise _Excluded("papersim_prepare_book_after_cutoff")
    if not response.eligible(capture.start, capture.cutoff) or bound["reason_code"] is not None:
        raise _Excluded(bound["reason_code"] or "papersim_prepare_book_unusable")
    if policy is None:
        raise _Excluded("d1_capture_book_age_policy_unregistered")
    try:
        body = journal.decode_public_json(response.entry.content)
        book = polymarket._parse_order_book(token, body)
    except (ValueError, OverflowError, OSError, polymarket._MarketRejected) as error:
        raise PreparationError("papersim_prepare_book_body_invalid") from error
    if body.get("asset_id") != token or body.get("market") not in (None, target.condition_id):
        raise PreparationError("papersim_prepare_book_identity_mismatch")
    if (row["source_time_status"] != "upstream" or book.book_observed_at is None
            or book.book_observed_at != _utc(row["source_observed_at"])
            or book.book_observed_at > response.entry.utc_fetched_at):
        raise PreparationError("papersim_prepare_book_time_mismatch")
    for stamp, limit in ((book.book_observed_at, policy.max_source_age_seconds),
                         (response.entry.utc_fetched_at, policy.max_receipt_age_seconds)):
        if (capture.cutoff - stamp) // timedelta(microseconds=1) > limit * 1_000_000:
            raise _Excluded("d1_capture_book_stale")
    asks = [(level.price, level.size) for level in book.asks]
    if not any(size > 0.0 for _, size in asks):
        raise _Excluded("papersim_prepare_book_asks_missing")
    return response, asks


def _cite_model_evidence(evidence: Sequence[contracts.FetchEvidence], cutoff: datetime,
                         citations: list[dict]) -> None:
    if not evidence:
        raise _Excluded("papersim_prepare_model_materials_missing")
    refs = []
    future = False
    for item in evidence:
        if item.raw_bytes is None or contracts.sha256_hexdigest(item.raw_bytes) != item.content_sha256:
            raise PreparationError("papersim_prepare_model_body_missing_or_invalid")
        fetched_at = _utc(item.fetched_at)
        observed_at = None if item.source_observed_at is None else _utc(item.source_observed_at)
        future |= fetched_at > cutoff or (observed_at is not None and observed_at > fetched_at)
        refs.append({"source_kind": item.source_kind, "content_sha256": item.content_sha256,
                     "byte_length": len(item.raw_bytes), "utc_fetched_at": fetched_at.isoformat(),
                     "source_observed_at": None if observed_at is None else observed_at.isoformat(),
                     "source_ref": item.source_reference})
    citations.extend(sorted(refs, key=acquire._canonical))
    if future:
        raise _Excluded("papersim_prepare_model_evidence_after_cutoff")


def _validate_xg_cutoff(snapshot: Mapping[str, object], cutoff: datetime) -> None:
    # The Understat wire shape uses naive match timestamps in UTC. The A3
    # validator already uses this interpretation for its completed-match check.
    def check(value: object) -> None:
        try:
            stamp = datetime.fromisoformat(value)
        except (ValueError, TypeError) as error:
            raise _Excluded("papersim_prepare_xg_time_unknown") from error
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        if stamp > cutoff:
            raise _Excluded("papersim_prepare_xg_after_cutoff")

    for block in snapshot.values():
        if not isinstance(block, Mapping):
            continue
        for match in block.get("dates", ()):
            if match.get("isResult"):
                check(match.get("datetime"))
        for team in block.get("teams", {}).values():
            for match in team.get("history", ()):
                check(match.get("date"))


def _soccer_quote(target: MarketTarget, request: pipeline.EntryRequest, materials: SoccerInputs,
                  active: ModelSpec) -> tuple[pipeline.EntryRequest, float, tuple[float, ...]] | dict:
    if active.spec_sha256() != SOCCER_FROZEN.baseline_spec().spec_sha256():
        raise PreparationError("papersim_prepare_soccer_spec_unsupported")
    if not target.plan.home or not target.plan.away or not materials.season:
        raise _Excluded("papersim_prepare_soccer_fixture_inputs_missing")
    _validate_xg_cutoff(materials.xg_snapshot, request.now)
    cross_league = materials.home_league != materials.away_league
    # The B1 construction codec reruns this exact constructor with these
    # inputs. Keep native returns and exceptions in that existing format.
    inputs = dict(
        xg_snapshot=dict(materials.xg_snapshot), home_team=target.plan.home,
        home_league=materials.home_league, away_team=target.plan.away,
        away_league=materials.away_league, market=target.plan.fixture, market_id=request.market_id,
        entry_ask_vwap250=gates.walk_book(request.book).vwap, kind=request.market_kind,
        side=request.side, line=request.line, team_side=materials.team_side, season=materials.season,
        bridge=dict(materials.bridge) if cross_league and materials.bridge is not None else None,
    )
    try:
        envelope = soccer_model.candidate_for_market(**inputs)
    except ValueError:
        envelope = None
    if envelope is None:
        return {"constructor": replay.CONSTRUCTOR_SOCCER, "inputs": inputs}
    quote = envelope.candidate
    bridge = envelope.bridge_source
    return (
        replace(request, model_quote=quote, cross_league=cross_league,
                home_league=materials.home_league, away_league=materials.away_league,
                bridge=bridge, bridge_fingerprint=None if bridge is None else pipeline.bridge_fingerprint(bridge),
                o25_hits=materials.o25_hits,
                lineup_confirmation=materials.lineup_confirmation),
        quote.model_median,
        quote.qs_variants,
    )


def _crypto_input_values(target: MarketTarget, materials: CryptoInputs, cutoff: datetime) -> tuple:
    candles, spot = materials.candles, materials.spot
    asset = target.plan.asset
    if (asset not in prices.SUPPORTED_ASSETS or candles.asset != asset or spot.asset != asset
            or candles.bar_hours != prices.SUPPORTED_BAR_HOURS or candles.gap_count != 0):
        raise _Excluded("papersim_prepare_crypto_series_mismatch")
    if (candles.fetched_at > cutoff or candles.covered_end > candles.fetched_at or spot.fetched_at > cutoff
            or (spot.source_observed_at is not None and spot.source_observed_at > spot.fetched_at)
            or (candles.source_observed_at is not None and candles.source_observed_at > candles.fetched_at)):
        raise _Excluded("papersim_prepare_model_evidence_after_cutoff")
    if not all(math.isfinite(close) for close in candles.closes):
        raise _Excluded("papersim_prepare_crypto_series_invalid")
    spot_bodies = [item for item in materials.evidence if item.source_kind == f"crypto-spot-{spot.source}"]
    candle_bodies = [item for item in materials.evidence if item.source_kind == f"crypto-candles-{candles.source}"]
    if len(spot_bodies) != 1 or spot_bodies[0].fetched_at != spot.fetched_at or not candle_bodies:
        raise _Excluded("papersim_prepare_crypto_source_missing")
    spot_body = spot_bodies[0]
    if min(item.fetched_at for item in candle_bodies) != candles.fetched_at:
        raise PreparationError("papersim_prepare_crypto_source_mismatch")
    try:
        spot_binding, candle_binding = prices._BINDINGS[spot.source], prices._BINDINGS[candles.source]
        for observation, binding in ((spot, spot_binding), (candles, candle_binding)):
            if (observation.venue != binding.venue or observation.currency != binding.currency
                    or observation.pair != binding.pair_for(asset)):
                raise PreparationError("papersim_prepare_crypto_source_mismatch")
        price = prices._parse_spot_price(spot_body.raw_bytes, spot.source, spot_binding, asset)
        start = prices._epoch_seconds(candles.covered_start)
        end = prices._epoch_seconds(candles.covered_end)
        as_of = prices._epoch_seconds(candles.fetched_at)
        if candles.source == "kraken":
            if len(candle_bodies) != 1:
                raise PreparationError("papersim_prepare_crypto_source_mismatch")
            rows, _ = prices._parse_kraken_ohlc(candle_bodies[0].raw_bytes, prices._KRAKEN_RESULT_KEYS[candles.pair])
            bars, gaps, *_ = prices._aggregate_hourly(rows, start, end, as_of)
        else:
            parse = prices._parse_binance_klines if candles.source == "binance" else prices._parse_coinbase_candles
            pages = tuple(parse(item.raw_bytes) for item in candle_bodies)
            bars, gaps, *_ = prices._merge_rows(pages, start, end, as_of)
    except (KeyError, prices._UpstreamFailure) as error:
        raise PreparationError("papersim_prepare_crypto_source_invalid") from error
    if price != spot.price or gaps or bars != candles.bars:
        raise PreparationError("papersim_prepare_crypto_projection_mismatch")
    return asset, price, tuple(bar.close for bar in bars)


def _crypto_quote(target: MarketTarget, request: pipeline.EntryRequest, materials: CryptoInputs,
                  active: ModelSpec, market: polymarket.PolymarketMarket) -> tuple:
    asset, price, closes = _crypto_input_values(target, materials, request.now)
    barrier = materials.barrier
    if type(barrier) not in (int, float) or not math.isfinite(barrier) or barrier <= 0.0:
        raise _Excluded("papersim_prepare_crypto_barrier_invalid")
    terminal = request.market_kind == pipeline.KIND_CRYPTO_TERMINAL
    if terminal:
        if materials.yes_when not in pipeline.YES_WHEN_VALUES or materials.touch_direction is not None:
            raise _Excluded("papersim_prepare_crypto_contract_direction_unknown")
        calculator = crypto_model.terminal_probability
    else:
        if materials.yes_when is not None or materials.touch_direction not in ("up", "down"):
            raise _Excluded("papersim_prepare_crypto_contract_direction_unknown")
        # A touch model needs assessed history from market start. Later closed
        # bars alone cannot resolve whether the contract has already touched.
        raise _Excluded("papersim_prepare_crypto_touch_state_unverified")
    try:
        fitted = CRYPTO_GBM.fit(CryptoFamilyHistory(closes={asset: closes}), active, request.now)
        horizon = (market.end_at - request.now).total_seconds() / 3600.0
        probability = calculator(price, fitted.sigma_daily[asset], barrier, horizon, asset=asset)
    except (ValueError, ArithmeticError) as error:
        raise _Excluded("papersim_prepare_crypto_model_inputs_invalid") from error
    request = replace(request, probability=probability, yes_when=materials.yes_when,
                      contract_direction=materials.yes_when)
    step, quote = pipeline._step_crypto_direction(request, request.market_kind, request.book)
    if quote is None:
        raise _Excluded(step.reasons[0])
    fields, _ = quote
    return request, fields["model_median"], fields["qs_variants"]


def _record(capture: _Capture, target: MarketTarget, active: ModelSpec,
            policy: BookAgePolicy | None, citations: list[dict]) -> dict:
    plan, spec = target.plan, target.pack_spec
    market, raw, event_id, tokens = _gamma_market(capture, target, citations)
    buy_token = tokens[target.buy_side]
    book, asks = _book(capture, target, buy_token, policy, citations)
    best = {target.buy_side: pipeline._best_ask(asks)}
    crypto = plan.market_kind in pipeline.CRYPTO_KINDS
    if crypto:
        if plan.side.lower() != target.buy_side.lower():
            raise _Excluded("papersim_prepare_crypto_buy_side_mismatch")
        other_side = "NO" if target.buy_side == "YES" else "YES"
        _, other_asks = _book(capture, target, tokens[other_side], policy, citations)
        best[other_side] = pipeline._best_ask(other_asks)
        boundary = market.end_at
        market_kind = "terminal" if plan.market_kind == pipeline.KIND_CRYPTO_TERMINAL else "touch"
    else:
        boundary = market.game_start_at
        if boundary is None or boundary <= capture.cutoff:
            raise _Excluded("papersim_prepare_soccer_kickoff_unavailable")
        public_line = raw.get("line")
        if (plan.line is not None and public_line is not None
                and journal._gamma_line(public_line) != journal._gamma_line(str(plan.line))):
            raise _Excluded("papersim_prepare_soccer_line_mismatch")
        market_kind = "other"

    material = target.materials
    if material is None:
        raise _Excluded("papersim_prepare_model_materials_missing")
    _cite_model_evidence(material.evidence, capture.cutoff, citations)
    if target.news is not None:
        if target.news.fixture != plan.fixture:
            raise _Excluded("papersim_prepare_news_fixture_mismatch")
        _cite_model_evidence(target.news_evidence, capture.cutoff, citations)
    request = pipeline.EntryRequest(
        market_id=market.market_id, question=market.question, fixture=plan.fixture,
        market_kind=plan.market_kind, side=plan.side, line=plan.line, book=asks,
        gamma_liquidity=journal._optional_number(raw.get("liquidityNum")),
        fee_category=plan.fee_category,
        market_rate=None if market.fee_rate_bps is None else market.fee_rate_bps / 10_000.0,
        news=target.news, buy_side=target.buy_side.lower(), yes_token=tokens["YES"], no_token=tokens["NO"],
        yes_ask=best.get("YES"), no_ask=best.get("NO"), buy_token=buy_token,
        book_time=book.entry.utc_fetched_at.astimezone(UTC), now=capture.cutoff,
        max_book_age_seconds=policy.max_receipt_age_seconds,
    )
    if crypto and isinstance(material, CryptoInputs):
        quote = _crypto_quote(target, request, material, active, market)
    elif not crypto and isinstance(material, SoccerInputs):
        quote = _soccer_quote(target, request, material, active)
    else:
        raise _Excluded("papersim_prepare_model_materials_incompatible")

    digest = book.entry.content_sha256
    record = {
        "queue": spec.queue, "candidate_key": target.candidate_key, "team": spec.team,
        "side": target.buy_side, "market_kind": market_kind, "lineage": spec.lineage,
        "model_name": spec.model_name, "market_type": spec.market_type,
        "evaluated_at": capture.cutoff.isoformat(), "market_ref_kind": "market_id",
        "market_id": market.market_id, "condition_id": market.condition_id, "market_slug": market.slug,
        "yes_token_id": tokens["YES"], "no_token_id": tokens["NO"],
        "book_snapshot_sha256": digest, "decision_snapshot_sha256": digest,
        "source_kind": spec.source_kind, "source_ref": book.entry.source_ref(),
        "snapshot_refs": list(dict.fromkeys(ref["content_sha256"] for ref in citations)),
        "events": {"market_class": plan.market_class, "gamma_event_id": event_id,
                   "league": plan.league, "home": plan.home, "away": plan.away, "asset": plan.asset,
                   "kickoff_day": None if crypto else boundary.date().isoformat(),
                   "expiry_at": market.end_at.isoformat(), "event_start_at": boundary.isoformat(),
                   "market_ref": market.market_id},
        "prepared_provenance": {"model_spec_sha256": active.spec_sha256(),
                                "model_version": active.model_version(),
                                "terms_sha256": target.terms_sha256,
                                "sources": citations},
    }
    if isinstance(quote, dict):
        return {**record, "construction": quote, "variant_quotes": [], "median_probability": None}
    quoted, median, variants = quote
    return {**record, "request_bundle": replay.replay_bundle_payload(quoted),
            "variant_quotes": list(variants), "median_probability": median}


def prepare_market_pack(*, batch_sha256: str, manifest: dict, entries: Sequence[journal.JournalEntry],
                        targets: Sequence[MarketTarget], specs: Mapping[str, LineageSpecs],
                        book_age_policy: BookAgePolicy | None) -> PreparedPack:
    """Prepare pack bytes offline from the verified managed-loader view.

    The managed caller checks the input-set/config binding, acquisition
    receipt, source registrations, book-age policy registration and target
    terms/model-material mappings. The frozen spec view is shared with scan
    assembly. This boundary rechecks the acquisition bytes before parsing.
    """
    capture = _capture_view(batch_sha256, manifest, entries)
    if book_age_policy is not None and not isinstance(book_age_policy, BookAgePolicy):
        raise PreparationError("d1_capture_book_age_policy_invalid")
    candidate_keys = set()
    records, excluded = [], []
    for target in sorted(targets, key=lambda item: (item.pack_spec.queue, item.condition_id, item.candidate_key)):
        if target.buy_side not in ("YES", "NO"):
            raise PreparationError("papersim_prepare_buy_side_invalid")
        if target.candidate_key in candidate_keys:
            raise PreparationError("papersim_prepare_candidate_key_duplicate")
        candidate_keys.add(target.candidate_key)
        try:
            active = specs[target.pack_spec.lineage].active
        except KeyError as error:
            raise PreparationError("papersim_prepare_lineage_spec_missing") from error
        crypto = target.plan.market_kind in pipeline.CRYPTO_KINDS
        family = CRYPTO_GBM.family_id if crypto else SOCCER_FROZEN.family_id
        market_class = packbuild.MARKET_CLASS_CRYPTO if crypto else packbuild.MARKET_CLASS_SPORTS
        if target.plan.market_class != market_class:
            raise PreparationError("papersim_prepare_market_class_mismatch")
        if active.family != family:
            raise PreparationError("papersim_prepare_lineage_spec_mismatch")
        if crypto and (active.data_contract != DATA_CONTRACT
                       or active.procedure_version != CRYPTO_GBM.procedure_version):
            raise PreparationError("papersim_prepare_crypto_spec_unsupported")
        if target.pack_spec.source_kind != "clob_book":
            raise PreparationError("papersim_prepare_book_source_kind_invalid")
        citations: list[dict] = []
        try:
            record = _record(capture, target, active, book_age_policy, citations)
        except _Excluded as error:
            excluded.append(ExcludedTarget(target.candidate_key, target.pack_spec.queue, str(error), tuple(citations)))
        else:
            records.append(record)
    try:
        # Use the same UTF-8 canonical array as d1compare.packbuild._pack_digest.
        content = json.dumps(records, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PreparationError("papersim_prepare_pack_encoding_invalid") from error
    return PreparedPack(content, batch_sha256, capture.cutoff, tuple(excluded))
