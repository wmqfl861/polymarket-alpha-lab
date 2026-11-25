"""Resolve registered FX-40 originals in one managed read-only snapshot.

The v1 input set pins the original scan config and D1 batch, the selection of
queues and markets, and ordered model-source observations. JSON references
use {"sha256", "byte_length"}. Targets select public identities and adapter
request sequences. Their terms and model data are verified against the raw
responses before use by prepare_market_pack.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
import math
import json
from pathlib import Path
import re
from types import MappingProxyType
from urllib.parse import parse_qsl, urlencode, urlsplit

from polymarket_alpha_lab.papersim import capture, fotmob, gates, league_strength, pipeline, prepare, queue_policy, scan
from polymarket_alpha_lab.papersim import snapshots, store, store_psycopg, xg
from polymarket_alpha_lab.papersim.adapters import contracts, polymarket, prices, understat
from polymarket_alpha_lab.papersim.d1compare import acquire, journal, operations, packbuild
from polymarket_alpha_lab.papersim.optimize.families import crypto, soccer_frozen
from polymarket_alpha_lab.papersim.optimize.provider import LineageSpecs, RegistryProvider, scan_prefix_of
from polymarket_alpha_lab.project_postgres.files import Layout, ProjectDatabaseError
from polymarket_alpha_lab.research_crypto_contract_scope import _QUESTION, _fold
from polymarket_alpha_lab.research_crypto_observation import assess_crypto_observation_time


SCHEMA_VERSION = 1
INPUT_SET_KIND = "papersim_prepare_input_set"
INPUT_SET_SOURCE = "papersim:prepare-inputs:v1"
INPUT_SET_COMMAND = "capture-prepare-inputs"
CONFIG_KIND = "papersim_prepare_scan_config"
CONFIG_SOURCE = "papersim:prepare-inputs:config:v1"
MODEL_RESPONSE_KIND = "papersim_prepare_model_response"
SOCCER_PROJECTION_BINDING_KIND = "papersim_prepare_soccer_projection_binding"
SOCCER_PROJECTION_BINDING_SOURCE = "papersim:prepare-inputs:soccer-projection:v1"
SOCCER_BRIDGE_KIND = "papersim_prepare_soccer_bridge"
SOCCER_BRIDGE_SOURCE = "papersim:prepare-inputs:soccer-bridge:v1"
SOCCER_NEWS_KIND = "papersim_prepare_soccer_news"
SOCCER_NEWS_SOURCE = "papersim:prepare-inputs:soccer-news:v1"

_INPUT_FIELDS = {
    "version", "instance_id", "config", "capture", "window_start", "window_end",
    "decision_as_of", "interval", "queues", "model_responses", "targets", "coverage",
}
_TARGET_FIELDS = {
    "queue", "condition_id", "market_id", "slug", "gamma_event_id", "tag_slug",
    "terms_sha256", "market_kind", "buy_side", "model_side", "event_start_at",
    "expires_at", "line", "sources",
}
_RESPONSE_FIELDS = {"sequence", "source_kind", "source_ref", "status", "received_at", "body",
                    "source_observed_at", "source_time_status", "target", "budget", "reason_code"}
_CAPTURE_FIELDS = {
    "capture_version", "scope", "started_at", "sealed_at", "decision_as_of", "requests",
    "discoveries", "markets", "books", "unbound_market_appearances", "coverage",
}
_REQUEST_FIELDS = {
    "sequence", "url", "source_kind", "request_meta", "sent", "http_status", "started_at",
    "finished_at", "utc_fetched_at", "source_observed_at", "source_time_status",
    "window_status", "status", "reason_code", "byte_length", "content_sha256", "page",
}
_FLAGS = ("paper_only", "report_only", "readonly")
_POLICY_FIELDS = {"max_source_age_seconds", "max_receipt_age_seconds"}
_SOCCER_TYPES = {
    "total": "over_under", "btts": "btts", "team_total": "team_total",
    "match_winner": "match_winner", "spread": "spread",
}
_CRYPTO_TYPES = {pipeline.KIND_CRYPTO_TERMINAL: "terminal", pipeline.KIND_CRYPTO_TOUCH: "touch"}
_SOCCER_TAGS = {
    "EPL": {"epl"}, "La_liga": {"laliga", "la-liga"}, "Serie_A": {"serie-a"},
    "Bundesliga": {"bundesliga"}, "Ligue_1": {"ligue-1"}, "Championship": {"championship", "efl-championship"},
    "Eredivisie": {"eredivisie"},
}
_SOCCER_CROSS_TAGS = {"champions-league", "europa-league", "conference-league", "ucl", "uel", "uecl"}
_CRYPTO_TAGS = {"BTC": {"crypto", "bitcoin"}, "ETH": {"crypto", "ethereum"}}
_PATH = re.compile(r"/(0|[1-9][0-9]*)/markets/(0|[1-9][0-9]*)")
_SEASON = re.compile(r"[0-9]{4}")
_NUMBER = r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?"
_TERMINAL_RULE = re.compile(
    r'this market will resolve to "yes" if the close price of the '
    r'(?P<venue>binance|coinbase|kraken) (?P<pair>btc/usdt?|eth/usdt?) '
    r'1[- ]minute candle at [0-9]{1,2}:[0-9]{2} [ap]m (?:et|utc) '
    r'on the date in the title is (?P<direction>above|below) \$(?P<price>' + _NUMBER +
    r')\. otherwise,? (?:it|this market) will resolve to "no"\.'
)


@dataclass(frozen=True)
class ResolvedPreparationInputs:
    input_set_sha256: str
    config_sha256: str
    batch_sha256: str
    window_start: datetime
    window_end: datetime
    decision_as_of: datetime
    capture_receipt: Mapping[str, object]
    config: scan.ScanConfig
    queues: tuple[str, ...]
    manifest: dict
    entries: tuple[journal.JournalEntry, ...]
    targets: tuple[prepare.MarketTarget, ...]
    specs: Mapping[str, LineageSpecs]
    book_age_policy: prepare.BookAgePolicy | None
    policy_slot_sha256: str | None
    model_source_refs: Mapping[str, tuple[dict, ...]]
    excluded: tuple[prepare.ExcludedTarget, ...]
    unbound_observations: tuple[dict, ...]
    failure_records: Mapping[str, dict]

    def prepare_market_pack(self) -> prepare.PreparedPack:
        """Calculate from the frozen inputs and preserve resolver omissions."""
        pack = prepare.prepare_market_pack(
            batch_sha256=self.batch_sha256, manifest=self.manifest, entries=self.entries,
            targets=self.targets, specs=self.specs, book_age_policy=self.book_age_policy,
        )
        excluded = self.excluded + tuple(
            replace(item, source_refs=({"batch_sha256": self.batch_sha256}, *item.source_refs))
            for item in pack.excluded
        )
        return replace(pack, excluded=tuple(sorted(excluded, key=lambda item: (item.queue, item.candidate_key))))


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise prepare.PreparationError(code)


def _fields(value: object, keys: set[str], code: str) -> dict:
    _require(type(value) is dict and value.keys() == keys, code)
    return value


def _text(value: object) -> bool:
    return type(value) is str and bool(value) and value == value.strip() and value.isprintable()


def _utc(value: object) -> datetime:
    result = prepare._utc(value)
    if type(value) is str:
        _require(datetime.fromisoformat(value).utcoffset() == timedelta(0),
                 "papersim_prepare_timestamp_invalid")
    return result


def _digest(content: bytes) -> str:
    return snapshots.snapshot_content_sha256(content)


def _read_snapshot(connection, digest: str, *, kind: str, source: str,
                   length: int | None = None, version: int = SCHEMA_VERSION) -> dict:
    snapshots.snapshot_digest_argument(digest)
    row = snapshots.read_snapshot(connection, digest)
    _require(row is not None, "papersim_prepare_snapshot_missing")
    raw = row["content"]
    _require(type(raw) is bytes and bool(raw) and len(raw) == row["byte_length"]
             and (length is None or length == len(raw))
             and row["content_sha256"] == _digest(raw) == digest,
             "papersim_prepare_snapshot_integrity_failed")
    _require((row["kind"], row["source"], row["schema_version"]) == (kind, source, version)
             and type(row["schema_version"]) is int, "papersim_prepare_snapshot_registration_conflict")
    _utc(row["fetched_at"])
    return row


def _reference(value: object) -> tuple[str, int]:
    row = _fields(value, {"sha256", "byte_length"}, "papersim_prepare_snapshot_reference_invalid")
    length = row["byte_length"]
    _require(type(length) is int and length > 0, "papersim_prepare_snapshot_reference_invalid")
    return snapshots.snapshot_digest_argument(row["sha256"]), length


def _read_reference(connection, reference: object, **metadata) -> dict:
    digest, length = _reference(reference)
    return _read_snapshot(connection, digest, length=length, **metadata)


def _document(raw: bytes, fields: set[str], code: str) -> dict:
    result = _fields(json.loads(raw), fields, code)
    _require(operations.document_bytes(result) == raw and type(result["version"]) is int
             and result["version"] == SCHEMA_VERSION, code)
    return result


def _success_receipt(connection, operation_key: str, command: str, *, code: str) -> dict:
    receipt = store_psycopg.read_command_run_event(connection, operation_key, 1, "SUCCEEDED")
    _require(receipt is not None and receipt["command"] == command
             and (receipt["operation_key"], receipt["attempt"], receipt["phase"])
             == (operation_key, 1, "SUCCEEDED")
             and receipt["run_event_key"] == store.command_run_event_key(operation_key, 1, "SUCCEEDED")
             and all(receipt[name] is None for name in ("error_code", "queue_id", "scan_id"))
             and receipt["input_evidence"] == [] and all(receipt[flag] is True for flag in _FLAGS), code)
    return receipt


def input_set_operation_key(config_sha256: str, scope: dict) -> str:
    identity = _digest(operations.document_bytes({"config_sha256": config_sha256, "scope": scope}))
    return f"papersim:prepare-inputs:v1:{identity}"


def _read_capture(connection, repo_root: Path, document: dict, registered_at: datetime):
    batch_row = _read_reference(
        connection, document["capture"], kind=acquire._BATCH_KIND,
        source=acquire._BATCH_SOURCES[2], version=2,
    )
    digest = batch_row["content_sha256"]
    manifest, entries = acquire.read_capture(repo_root=repo_root, batch_sha256=digest, _connection=connection)
    _fields(manifest, _CAPTURE_FIELDS, "papersim_prepare_capture_manifest_invalid")
    _require(acquire._canonical(manifest) == batch_row["content"], "papersim_prepare_capture_manifest_invalid")
    view = prepare._capture_view(digest, manifest, entries)
    scope = _fields(manifest["scope"], {"window_start", "window_end", "tag_slugs"},
                    "papersim_prepare_capture_scope_invalid")
    start, end, cutoff = (_utc(document[key]) for key in ("window_start", "window_end", "decision_as_of"))
    tags = scope["tag_slugs"]
    _require((start, end, cutoff) == (view.start, _utc(scope["window_end"]), view.cutoff)
             and _utc(batch_row["fetched_at"]) == cutoff and type(tags) is list and bool(tags)
             and tags == sorted({polymarket.DiscoveryQuery(tag).tag_slug for tag in tags}),
             "papersim_prepare_capture_scope_invalid")
    scope_sha = _digest(acquire._canonical(scope))
    receipt = _success_receipt(connection, f"{acquire._OPERATION_PREFIX}:{scope_sha}", acquire._COMMAND,
                               code="d1_capture_manifest_receipt_invalid")
    _require(receipt["config_digest"] == scope_sha and receipt["scheduled_for"] == start
             and receipt["code_commit"] is None
             and receipt["output_evidence"] == [digest]
             and cutoff <= _utc(receipt["occurred_at"]) <= registered_at,
             "d1_capture_manifest_receipt_invalid")
    _verify_capture_requests(connection, view, end, tags)
    return view, entries, receipt, end


def _verify_capture_requests(connection, view, end: datetime, tags: list[str]) -> None:
    """Check each attempt and its deduplicated body registration."""
    registrations = {}
    for row in view.manifest["requests"]:
        source = row["source_kind"]
        fields = _REQUEST_FIELDS | ({"freshness"} if source == polymarket.SOURCE_KIND_CLOB else set())
        _fields(row, fields, "papersim_prepare_capture_request_invalid")
        query = row["request_meta"]["query"]
        if source == polymarket.SOURCE_KIND_GAMMA:
            limit = polymarket.GAMMA_EVENTS_PAGE_LIMIT
            _require(query["tag_slug"] in tags and query["closed"] == "false"
                     and query["limit"] == str(limit) and re.fullmatch(r"[0-9]+", query["offset"])
                     and int(query["offset"]) % limit == 0, "papersim_prepare_capture_request_invalid")
        else:
            _require(acquire._TOKEN_ID.fullmatch(query["token_id"]) is not None,
                     "papersim_prepare_capture_request_invalid")
        response = view.responses.get(row["sequence"])
        fetched = None if response is None else response.entry.utc_fetched_at
        observed = None if row["source_observed_at"] is None else _utc(row["source_observed_at"])
        started, finished = _utc(row["started_at"]), _utc(row["finished_at"])
        _require(type(row["sent"]) is bool
                 and (fetched is None or row["sent"] and type(row["http_status"]) is int
                      and row["http_status"] == 200 and fetched == finished)
                 and (source != polymarket.SOURCE_KIND_GAMMA or response is None
                      or (observed is None and row["source_time_status"] == "receipt_only")),
                 "papersim_prepare_capture_request_invalid")
        expected = acquire._Request(
            row["sequence"], row["url"], source, row["request_meta"], started, sent=row["sent"],
            http_status=row["http_status"], fetched_at=fetched, finished_at=finished,
            source_observed_at=observed, source_time_status=row["source_time_status"],
            reason_code=row["reason_code"], page=row["page"], byte_length=row["byte_length"],
            content_sha256=row["content_sha256"],
        ).document(window_start=view.start, window_end=end, decision_as_of=view.cutoff)
        _require(expected == row, "papersim_prepare_capture_request_invalid")
        if response is None:
            continue
        digest = response.entry.content_sha256
        if digest not in registrations:
            stored = acquire._snapshot(connection, digest)
            _require(stored["kind"] == acquire._BODY_KIND and stored["content"] == response.entry.content
                     and type(stored["schema_version"]) is int and stored["schema_version"] in acquire._BATCH_SOURCES,
                     "papersim_prepare_capture_response_registration_conflict")
            # A different request may have registered the same bytes first.
            try:
                acquire._request_fields(stored["source"])
            except acquire.AcquisitionError:
                acquire._request_fields(stored["source"], resolution=True)
            registrations[digest] = _utc(stored["fetched_at"])
        _require(registrations[digest] <= fetched, "papersim_prepare_capture_response_registration_conflict")


def _book_age_policy(connection, reference: object, start: datetime, end: datetime,
                     *, instance_id: str, team: str):
    if reference is None:
        return None, None
    row = _read_reference(connection, reference, kind=operations._INTERVAL_KIND,
                          source="d1:operation:interval:v1")
    digest = row["content_sha256"]
    code = "d1_capture_book_age_policy_binding_invalid"
    interval_receipt = _success_receipt(connection, "d1:interval-binding:v1:" + digest,
                                        "d1-register-interval", code=code)
    interval = operations._read_document(
        connection, interval_receipt["output_evidence"],
        kind=operations._INTERVAL_KIND, source="d1:operation:interval:v1",
    )
    _require(interval_receipt["output_evidence"] == operations._refs(digest, row["byte_length"])
             and interval.get("instance_id") == instance_id and interval.get("team") == team
             and _utc(row["fetched_at"]) == interval_receipt["occurred_at"]
             < _utc(interval["starts_at"]) <= start < end <= _utc(interval["ends_at"]), code)
    policy = interval.get("book_age_policy")
    if policy is None:
        return None, None
    _fields(policy, _POLICY_FIELDS, "d1_capture_book_age_policy_invalid")
    return prepare.BookAgePolicy(**policy), digest


@dataclass(frozen=True)
class _ModelResponse:
    sequence: int
    source_kind: str
    source_ref: str
    status: str
    received_at: datetime | None
    evidence: contracts.FetchEvidence | None

    def citation(self) -> dict:
        evidence = self.evidence
        return {
            "sequence": self.sequence, "source_kind": self.source_kind, "source_ref": self.source_ref,
            "status": self.status,
            "utc_fetched_at": None if self.received_at is None else self.received_at.isoformat(),
            "content_sha256": None if evidence is None else evidence.content_sha256,
            "byte_length": None if evidence is None else len(evidence.raw_bytes),
        }

    def usable(self, cutoff: datetime) -> contracts.FetchEvidence:
        if self.status != "received" or self.evidence is None:
            raise prepare._Excluded("papersim_prepare_model_source_unavailable")
        if self.received_at > cutoff:
            raise prepare._Excluded("papersim_prepare_model_evidence_after_cutoff")
        return self.evidence


def _url_query(source_ref: str) -> tuple[str, dict[str, str]]:
    split = urlsplit(source_ref)
    pairs = parse_qsl(split.query, keep_blank_values=True, strict_parsing=True)
    query = dict(pairs)
    _require(split.scheme == "https" and len(pairs) == len(query) and not split.fragment
             and all(query.values()), "papersim_prepare_model_source_invalid")
    return f"{split.scheme}://{split.netloc}{split.path}", query


def _source_request(kind: str, source_ref: str) -> None:
    """Allow the existing public endpoints and query keys."""
    code = "papersim_prepare_model_source_invalid"
    if kind == understat.SOURCE_KIND:
        leagues = "|".join(understat.UNDERSTAT_LEAGUES)
        _require(re.fullmatch(r"understat\.com/getLeagueData/(?:" + leagues + r")/[0-9]{4}", source_ref)
                 is not None, code)
        return
    endpoint, query = _url_query(source_ref)
    if kind == "fotmob":
        prefix = f"https://{fotmob.HOST}/api/data/"
        _require((endpoint == prefix + "leagues" and query.keys() == {"id", "season"}
                  and query["id"] in {str(league.league_id) for league in fotmob.FotMobLeague}
                  and re.fullmatch(r"[0-9]{4}/[0-9]{4}", query["season"]))
                 or (endpoint == prefix + "matchDetails" and query.keys() == {"matchId"}
                     and re.fullmatch(r"[0-9]+", query["matchId"])), code)
        return
    match = re.fullmatch(r"crypto-(candles|spot)-(binance|coinbase|kraken)", kind)
    _require(match is not None, code)
    role, provider = match.groups()
    pairs = prices._BINDINGS[provider].pairs.values()
    if provider == "binance":
        path = "/klines" if role == "candles" else "/ticker/price"
        keys = {"symbol", "interval", "limit", "startTime", "endTime"} if role == "candles" else {"symbol"}
        _require(endpoint == prices._BINANCE_BASE_URL + path and query.keys() == keys
                 and query["symbol"] in pairs, code)
    elif provider == "coinbase":
        path = "candles" if role == "candles" else "ticker"
        keys = {"start", "end", "granularity"} if role == "candles" else set()
        _require(endpoint in {f"{prices._COINBASE_BASE_URL}/products/{pair}/{path}" for pair in pairs}
                 and query.keys() == keys, code)
    else:
        path = "/OHLC" if role == "candles" else "/Ticker"
        keys = {"pair", "interval", "since"} if role == "candles" else {"pair"}
        _require(endpoint == prices._KRAKEN_BASE_URL + path and query.keys() == keys
                 and query["pair"] in pairs, code)


def _model_responses(connection, values: object, registered_at: datetime) -> dict[int, _ModelResponse]:
    _require(type(values) is list, "papersim_prepare_model_requests_invalid")
    by_sequence = {}
    for sequence, item in enumerate(values, 1):
        code = "papersim_prepare_model_request_invalid"
        row = _fields(item, _RESPONSE_FIELDS, code)
        kind, ref, status = row["source_kind"], row["source_ref"], row["status"]
        _require(type(row["sequence"]) is int and row["sequence"] == sequence
                 and _text(kind) and _text(ref) and status in ("received", "failed"), code)
        _source_request(kind, ref)
        observed, evidence = None, None
        if row["body"] is not None:
            observed = _utc(row["received_at"])
            digest, length = _reference(row["body"])
            saved = snapshots.read_snapshot(connection, digest)
            _require(saved is not None, "papersim_prepare_snapshot_missing")
            raw = saved["content"]
            _require(_digest(raw) == digest and len(raw) == saved["byte_length"] == length
                     and saved["kind"] == MODEL_RESPONSE_KIND and saved["schema_version"] == SCHEMA_VERSION, code)
            _require(_utc(saved["fetched_at"]) <= observed <= registered_at
                     and saved["byte_length"] <= contracts.HARD_MAX_RESPONSE_BYTES, code)
            # Identical bodies can be returned by a different allowed endpoint.
            # The receipt-pinned request row retains this occurrence's source.
            _require(row["budget"] == "charged" and isinstance(row["target"], dict)
                     and (row["source_time_status"], row["source_observed_at"]) == ("receipt_only", None), code)
            evidence = contracts.FetchEvidence.from_bytes(
                source_kind=kind, source_reference=ref, fetched_at=observed, raw_bytes=saved["content"],
            )
        else:
            _require(status == "failed" and row["received_at"] is None, code)
        by_sequence[sequence] = _ModelResponse(sequence, kind, ref, status, observed, evidence)
    return by_sequence


def _model_selection(responses: Mapping[int, _ModelResponse], sequences: object,
                     citations: list[dict]) -> tuple[_ModelResponse, ...]:
    code = "papersim_prepare_model_request_reference_invalid"
    _require(type(sequences) is list and all(type(seq) is int and seq in responses for seq in sequences)
             and len(set(sequences)) == len(sequences), code)
    selected = tuple(responses[seq] for seq in sorted(sequences))
    citations.extend(item.citation() for item in selected)
    return selected


def _find_event(view, cache: dict, citation: dict) -> dict:
    sequence = citation["sequence"]
    path = _PATH.fullmatch(citation["path"])
    _require(path is not None, "papersim_prepare_gamma_location_invalid")
    if sequence not in cache:
        cache[sequence] = journal.decode_public_json(view.responses[sequence].entry.content)
    return cache[sequence][int(path[1])]


def _provenance(row: dict) -> dict:
    return {"source_kind": row["kind"], "source_ref": row["source"],
            "content_sha256": row["content_sha256"], "byte_length": row["byte_length"],
            "utc_fetched_at": _utc(row["fetched_at"]).isoformat()}


def _selection(target: dict, config: scan.ScanConfig, queue: scan.QueueScanConfig) -> prepare.MarketTarget:
    kind, side = target["market_kind"], target["buy_side"]
    plan = packbuild.MarketPlan(
        target["market_id"], kind, target["model_side"], target["slug"],
        gamma_event_id=target["gamma_event_id"],
        market_class=packbuild.MARKET_CLASS_CRYPTO if kind in pipeline.CRYPTO_KINDS else packbuild.MARKET_CLASS_SPORTS,
    )
    spec = packbuild.PackSpec(queue.queue, config.team, queue.lineage, queue.model_key, queue.market_type)
    return prepare.MarketTarget(spec, plan, target["condition_id"], target["tag_slug"], target["terms_sha256"], side)


def _queue_binding(queue: scan.QueueScanConfig, kind: str) -> None:
    crypto_kind = kind in pipeline.CRYPTO_KINDS
    if not crypto_kind and kind not in _SOCCER_TYPES:
        raise prepare._Excluded("papersim_prepare_market_kind_unsupported")
    prefix, lineage_target = scan_prefix_of(queue.lineage)
    expected = _CRYPTO_TYPES[kind] if crypto_kind else _SOCCER_TYPES[kind]
    queue_types = {kind, expected} if crypto_kind else {kind, expected, "other"}
    _require(prefix == ("crypto" if crypto_kind else "club-soccer")
             and lineage_target in (None, expected, kind) and queue.market_type in queue_types
             and _text(queue.model_key), "papersim_prepare_target_queue_conflict")


def _exact_number(value: object) -> Decimal:
    _require(type(value) in (Decimal, int, str) and re.fullmatch(r"[+-]?" + _NUMBER, str(value)),
             "papersim_prepare_target_number_invalid")
    return Decimal(str(value).replace(",", ""))


def _compatible_float(number: Decimal) -> float:
    value = float(number)
    _require(math.isfinite(value) and Decimal(str(value)) == number, "papersim_prepare_target_number_invalid")
    return value


def _crypto_contract(target: dict, market: polymarket.PolymarketMarket, raw: dict, cutoff: datetime):
    description = market.description
    rule = _TERMINAL_RULE.fullmatch(_fold(description)) if description else None
    question = _QUESTION.fullmatch(_fold(market.question))
    if rule is None or question is None:
        raise prepare._Excluded("papersim_prepare_crypto_contract_terms_unproved")
    asset = {"bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "eth": "ETH"}[question["asset"]]
    venue, direction, pair = rule["venue"], rule["direction"], rule["pair"]
    binding = prices._BINDINGS[venue]
    if target["tag_slug"] not in _CRYPTO_TAGS[asset]:
        raise prepare._Excluded("papersim_prepare_crypto_asset_scope_unproved")
    barrier = _exact_number(question["price"])
    _require(barrier == _exact_number(rule["price"]) and barrier > 0
             and pair == f"{asset.lower()}/{binding.currency.lower()}"
             and direction == question["direction"], "papersim_prepare_crypto_contract_conflict")
    _require(target["model_side"] == target["buy_side"].lower()
             and target["line"] is not None and _exact_number(target["line"]) == barrier
             and (raw.get("line") is None or _exact_number(raw["line"]) == barrier)
             and raw.get("handicap") is None,
             "papersim_prepare_crypto_contract_conflict")
    observation = assess_crypto_observation_time(
        team_id=f"crypto_{asset.lower()}", question=market.question, resolution_criteria=description,
        market_slug=market.slug, as_of=cutoff, forecast_cutoff_at=cutoff, scheduled_end_at=market.end_at,
    )
    opening = observation.candle_open_at
    if opening is None or not cutoff < opening or market.end_at != opening + timedelta(minutes=1):
        raise prepare._Excluded("papersim_prepare_crypto_terminal_time_unproved")
    _require(_utc(target["event_start_at"]) == market.end_at, "papersim_prepare_target_clock_conflict")
    return asset, _compatible_float(barrier), direction, binding.currency


def _spot_reference(provider: str, pair: str) -> str:
    if provider == "binance":
        return f"{prices._BINANCE_BASE_URL}/ticker/price?" + urlencode({"symbol": pair})
    if provider == "coinbase":
        return f"{prices._COINBASE_BASE_URL}/products/{pair}/ticker"
    return f"{prices._KRAKEN_BASE_URL}/Ticker?" + urlencode({"pair": pair})


def _candle_rows(provider: str, pair: str, pages: tuple, start: int, end: int, as_of: int):
    if provider == "kraken":
        if len(pages) != 1 or (end - start) // prices.KRAKEN_HOUR_SECONDS > prices.KRAKEN_USABLE_ROWS:
            raise prepare._Excluded("papersim_prepare_crypto_window_incomplete")
        source = f"{prices._KRAKEN_BASE_URL}/OHLC?" + urlencode({
            "pair": pair, "interval": prices.KRAKEN_INTERVAL_MINUTES, "since": start,
        })
        _require(pages[0].source_reference == source, "papersim_prepare_crypto_request_conflict")
        rows, _ = prices._parse_kraken_ohlc(pages[0].raw_bytes, prices._KRAKEN_RESULT_KEYS[pair])
        return prices._aggregate_hourly(rows, start, end, as_of)

    raw_rows, cursor = [], start
    for page in pages:
        _require(cursor < end, "papersim_prepare_crypto_request_conflict")
        if provider == "binance":
            count = min(prices._BINANCE_MAX_BARS_PER_REQUEST, (end - cursor) // prices.BAR_SECONDS)
            next_cursor = cursor + count * prices.BAR_SECONDS
            ref = f"{prices._BINANCE_BASE_URL}/klines?" + urlencode({
                "symbol": pair, "interval": prices._BINANCE_INTERVAL, "limit": count,
                "startTime": 1000 * cursor, "endTime": 1000 * next_cursor - 1,
            })
            parse = prices._parse_binance_klines
        else:
            count = min(prices.COINBASE_MAX_CANDLES_PER_WINDOW, (end - cursor) // prices.BAR_SECONDS)
            next_cursor = cursor + count * prices.BAR_SECONDS
            ref = prices.CryptoCandleWindow(
                pair, datetime.fromtimestamp(cursor, UTC), datetime.fromtimestamp(next_cursor, UTC),
                prices.COINBASE_GRANULARITY_SECONDS,
            ).source_reference
            parse = prices._parse_coinbase_candles
        _require(page.source_reference == ref, "papersim_prepare_crypto_request_conflict")
        raw_rows.append(parse(page.raw_bytes))
        cursor = next_cursor
    if cursor != end:
        raise prepare._Excluded("papersim_prepare_crypto_window_incomplete")
    return prices._merge_rows(tuple(raw_rows), start, end, as_of)


def _crypto_materials(sources: dict, selected: tuple[_ModelResponse, ...], view,
                      contract, active) -> prepare.CryptoInputs:
    spot_request = next(item for item in selected if item.sequence == sources["spot"])
    pages = tuple(item for item in selected if item.sequence != spot_request.sequence)
    if not pages:
        raise prepare._Excluded("papersim_prepare_crypto_window_incomplete")
    spot_source, candle_source = spot_request.source_kind, pages[0].source_kind
    _require(spot_source.startswith("crypto-spot-") and candle_source.startswith("crypto-candles-")
             and all(item.source_kind == candle_source for item in pages),
             "papersim_prepare_crypto_request_conflict")
    spot = spot_request.usable(view.cutoff)
    evidence = tuple(item.usable(view.cutoff) for item in pages)
    if spot.fetched_at < view.start:
        raise prepare._Excluded("papersim_prepare_crypto_spot_outside_window")
    asset, barrier, direction, currency = contract
    spot_provider = spot_source.removeprefix("crypto-spot-")
    candle_provider = candle_source.removeprefix("crypto-candles-")
    spot_binding, candle_binding = prices._BINDINGS[spot_provider], prices._BINDINGS[candle_provider]
    spot_pair, candle_pair = spot_binding.pair_for(asset), candle_binding.pair_for(asset)
    _require(spot.source_reference == _spot_reference(spot_provider, spot_pair)
             and spot_binding.currency == candle_binding.currency == currency,
             "papersim_prepare_crypto_request_conflict")
    start, end = _utc(sources["window_start"]), _utc(sources["window_end"])
    first_receipt = min(page.fetched_at for page in evidence)
    try:
        start_s, end_s = prices._validate_window(start, end, prices.SUPPORTED_BAR_HOURS)
        if end > first_receipt:
            raise prepare._Excluded("papersim_prepare_crypto_window_unclosed")
        bars, gaps, duplicates, unfinished, outside = _candle_rows(
            candle_provider, candle_pair, evidence, start_s, end_s, prices._epoch_seconds(first_receipt),
        )
        price = prices._parse_spot_price(spot.raw_bytes, spot_provider, spot_binding, asset)
    except prices._Unsupported as error:
        raise prepare._Excluded("papersim_prepare_crypto_window_invalid") from error
    except prices._UpstreamFailure as error:
        raise prepare._Excluded(error.reason_code) from error
    if gaps or not bars:
        raise prepare._Excluded("papersim_prepare_crypto_window_incomplete")
    closed_at_decision = prices._epoch_seconds(view.cutoff)
    closed_at_decision -= closed_at_decision % prices.BAR_SECONDS
    if end_s != closed_at_decision:
        raise prepare._Excluded("papersim_prepare_crypto_window_outside_cutoff")
    # The model counts equality as below at zero volatility. A strict
    # below contract at that exact deterministic price cannot use it.
    if direction == "below" and price == barrier:
        try:
            fitted = crypto.CRYPTO_GBM.fit(crypto.CryptoFamilyHistory(
                closes={asset: tuple(bar.close for bar in bars)}), active, view.cutoff)
        except ValueError as error:
            raise prepare._Excluded("papersim_prepare_crypto_model_inputs_invalid") from error
        if fitted.sigma_daily[asset] == 0:
            raise prepare._Excluded("papersim_prepare_crypto_terminal_equality_unproved")
    candle_observation = prices.CandleWindowObservation(
        asset, candle_provider, candle_binding.venue, candle_pair, currency, prices.SUPPORTED_BAR_HOURS,
        start, end, start, end, bars, (end_s - start_s) // prices.BAR_SECONDS, len(bars),
        gaps, duplicates, unfinished, outside, first_receipt,
    )
    spot_observation = prices.SpotObservation(
        asset, spot_provider, spot_binding.venue, spot_pair, currency, price, spot.fetched_at,
    )
    return prepare.CryptoInputs(candle_observation, spot_observation, barrier, (*evidence, spot),
                                yes_when=direction)


def _understat_snapshot(sources: tuple[contracts.FetchEvidence, ...], season: str) -> tuple[dict, dict]:
    blocks, priors = [], []
    seen = set()
    for source in sources:
        league, year = source.source_reference.split("/")[-2:]
        _require((league, year) not in seen and year in (season, xg.prior_season(season)),
                 "papersim_prepare_soccer_source_conflict")
        seen.add((league, year))
        try:
            data = understat._parse_league_payload(source.raw_bytes)
            validated = understat._validate_league_payload(league, year, data, now=source.fetched_at)
            if any(type(row.get("isResult")) is not bool for row in data["dates"]):
                raise prepare._Excluded("papersim_prepare_soccer_source_invalid")
        except understat._ValidationError as error:
            raise prepare._Excluded(error.reason_code) from error
        if year == season:
            value = {
                "schema": understat.LEAGUE_BLOCK_SCHEMA, "source_kind": understat.SOURCE_KIND,
                "league": league, "season": year, "data": data,
                "source": {"source_reference": source.source_reference, "content_sha256": source.content_sha256,
                           "fetched_at": source.fetched_at.isoformat()},
                "summary": {**validated, "team_count": len(validated["team_ids"])},
            }
            blocks.append(contracts.AdapterResult.success(value, (source,)))
        else:
            priors.append(understat.UnderstatPriorBlock(
                league, year, data, source.content_sha256, source.source_reference,
            ))
    result = understat.assemble_xg_snapshot(blocks, priors)
    if not result.is_success:
        raise prepare._Excluded(result.reason_code)
    return result.value, {
        league: result.value[xg.season_key(league, season)]["dates"] for league in understat.UNDERSTAT_LEAGUES
    }


def _fotmob_snapshot(sources: tuple[contracts.FetchEvidence, ...], season: str, league_key: str) -> tuple[dict, dict]:
    league = next((item for item in fotmob.FotMobLeague if item.league_key == league_key), None)
    if league is None:
        raise prepare._Excluded("papersim_prepare_soccer_league_unsupported")
    by_season, details = {}, {}
    for source in sources:
        endpoint, query = _url_query(source.source_reference)
        try:
            body = journal.decode_public_json(source.raw_bytes)
        except journal.JournalError as error:
            raise prepare._Excluded("papersim_prepare_soccer_source_invalid") from error
        if type(body) is not dict:
            raise prepare._Excluded("papersim_prepare_soccer_source_invalid")
        if endpoint.endswith("/leagues"):
            year = query["season"].split("/")[0]
            _require(year in (season, xg.prior_season(season)) and year not in by_season
                     and query == {"id": str(league.league_id), "season": fotmob._expected_season(year)},
                     "papersim_prepare_soccer_source_conflict")
            by_season[year] = source, body
        else:
            mid = query["matchId"]
            _require(mid not in details, "papersim_prepare_soccer_source_conflict")
            general = body.get("general")
            _require(type(general) is dict and str(general.get("matchId")) == mid,
                     "papersim_prepare_soccer_source_conflict")
            details[mid] = source, body
    snapshot, fixtures = {}, {}
    used_details = set()
    for year in (xg.prior_season(season), season):
        if year not in by_season:
            raise prepare._Excluded("papersim_prepare_soccer_season_unavailable")
        source, league_body = by_season[year]
        try:
            matches = fotmob._parse_league_payload(league_body, year)
            kept = fotmob._partition_matches(matches, league.max_round)["kept"]
            detail_bodies = {}
            for match in kept:
                mid = str(match["id"])
                if mid in used_details:
                    raise prepare.PreparationError("papersim_prepare_soccer_source_conflict")
                used_details.add(mid)
                if mid in details:
                    item, body = details[mid]
                    kickoff = prepare._utc(match["status"]["utcTime"])
                    if kickoff > min(item.fetched_at, source.fetched_at):
                        raise prepare._Excluded("papersim_prepare_xg_after_cutoff")
                    detail_bodies[mid] = body
            snapshot.update(fotmob.fotmob_to_understat(
                {"fixtures": league_body, "details": detail_bodies}, league, year,
            ))
        except fotmob.FotMobSchemaError as error:
            raise prepare._Excluded("papersim_prepare_soccer_source_invalid") from error
        if year == season:
            fixtures[league_key] = [
                {"id": str(match["id"]), "h": {"id": str(match["home"]["id"])},
                 "a": {"id": str(match["away"]["id"])}, "datetime": match["status"]["utcTime"],
                 "isResult": match["status"].get("finished")}
                for match in matches
            ]
    _require(details.keys() <= used_details, "papersim_prepare_soccer_source_conflict")
    return snapshot, fixtures


def _soccer_leagues(sources: dict) -> tuple[str, str]:
    home = sources["league"]
    return home, sources.get("away_league", home)


def _soccer_clubs(sources: dict, snapshot: dict, fixtures: dict, kickoff: datetime, event: dict) -> tuple[str, str]:
    leagues, season = _soccer_leagues(sources), sources["season"]
    rosters = []
    for league in leagues:
        block = snapshot.get(xg.season_key(league, season))
        prior = snapshot.get(xg.season_key(league, xg.prior_season(season)))
        if block is None or prior is None or not any(row["isResult"] for row in prior["dates"]):
            raise prepare._Excluded("papersim_prepare_soccer_season_unavailable")
        rosters.append((block["teams"], prior["teams"]))
    team_ids = sources["home_team_id"], sources["away_team_id"]
    names = []
    for team_id, (roster, prior) in zip(team_ids, rosters, strict=True):
        _require(team_id in roster, "papersim_prepare_soccer_fixture_conflict")
        title = roster[team_id]["title"]
        _require(sum(row["title"] == title for row in roster.values()) == 1
                 and sum(row["title"] == title for row in prior.values()) <= 1,
                 "papersim_prepare_soccer_fixture_conflict")
        names.append(title)
    home, away = names
    _require(home != away and team_ids[0] != team_ids[1], "papersim_prepare_soccer_fixture_conflict")
    if leagues[0] != leagues[1]:
        if str(event.get("gameId")) != sources["fixture_id"]:
            raise prepare._Excluded("papersim_prepare_soccer_fixture_inputs_missing")
        return home, away
    matches = [item for item in fixtures[leagues[0]] if str(item["id"]) == sources["fixture_id"]]
    if not matches:
        raise prepare._Excluded("papersim_prepare_soccer_fixture_inputs_missing")
    _require(len(matches) == 1, "papersim_prepare_soccer_fixture_conflict")
    fixture = matches[0]
    fixture_clock = datetime.fromisoformat(fixture["datetime"])
    if fixture_clock.tzinfo is None:
        fixture_clock = fixture_clock.replace(tzinfo=UTC)
    _require(fixture_clock == kickoff and fixture["isResult"] is not True,
             "papersim_prepare_soccer_fixture_clock_conflict")
    _require(team_ids == (str(fixture["h"]["id"]), str(fixture["a"]["id"])),
             "papersim_prepare_soccer_fixture_conflict")
    _require(all(side.get("title", title) == title for side, title in zip((fixture["h"], fixture["a"]), (home, away))),
             "papersim_prepare_soccer_fixture_conflict")
    return home, away


def _soccer_terms(target: dict, market: polymarket.PolymarketMarket, event: dict, raw: dict,
                  home: str, away: str) -> tuple[float | None, str | None]:
    """Match a literal binary regulation proposition to the frozen constructor."""
    fixture = f"{home} versus {away}"
    if event.get("title") not in (fixture, f"{home} vs. {away}", f"{home} vs {away}"):
        raise prepare._Excluded("papersim_prepare_soccer_fixture_terms_unproved")
    kind = target["market_kind"]
    question, description = market.question, market.description
    affirmative, negative, team_side = None, None, None
    line = target["line"]
    contract = None
    for candidate in ([None] if kind in ("total", "btts") else [home, away]):
        whose = "the match" if candidate is None else candidate
        if kind == "btts":
            contract = re.fullmatch(re.escape(f"Will both teams score in {fixture}?"), question)
            if contract:
                affirmative, negative = "yes", "no"
                expected_description = "Yes pays when both teams score in regulation."
        elif kind == "match_winner":
            term = f"Will {candidate} win against {away if candidate == home else home}?"
            contract = re.fullmatch(re.escape(term), question)
            if contract:
                affirmative = "home" if candidate == home else "away"
                negative = f"{affirmative}_no"
                expected_description = f"Yes pays when {candidate} wins in regulation."
        elif kind == "spread":
            term = re.escape(f"Will {candidate} cover ") + r"(?P<line>[+-]?" + _NUMBER + r")\?"
            contract = re.fullmatch(term, question)
            if contract:
                affirmative, negative = "cover", "nocover"
                expected_description = f"Yes pays when {candidate} covers {contract['line']} in regulation."
        else:
            subject = fixture if candidate is None else candidate
            term = re.escape(f"Will {subject} have more than ") + r"(?P<line>" + _NUMBER + r") goals\?"
            contract = re.fullmatch(term, question)
            if contract:
                affirmative, negative = "over", "under"
                expected_description = f"Yes pays when {whose} has more than {contract['line']} goals in regulation."
        if contract:
            break
    if kind == "match_winner" and not contract and question == f"Will {fixture} end in a draw?":
        affirmative, negative = "draw", "draw_no"
        expected_description = "Yes pays when the match ends in a draw in regulation."
        contract = re.fullmatch(re.escape(question), question)
    if not contract or description != expected_description:
        raise prepare._Excluded("papersim_prepare_soccer_contract_terms_unproved")
    if kind in ("team_total", "spread"):
        team_side = "home" if candidate == home else "away"
    expected_side = affirmative if target["buy_side"] == "YES" else negative
    _require(target["model_side"] == expected_side, "papersim_prepare_soccer_side_conflict")
    if "line" not in contract.groupdict():
        _require(line is None and raw.get("line") is None and raw.get("handicap") is None,
                 "papersim_prepare_soccer_line_conflict")
        return None, team_side
    expected_line = _exact_number(contract["line"])
    _require(line is not None and _exact_number(line) == expected_line
             and all(_exact_number(raw[key]) == expected_line for key in ("line", "handicap")
                     if raw.get(key) is not None), "papersim_prepare_soccer_line_conflict")
    return _compatible_float(expected_line), team_side


def _soccer_projection(connection, reference: object, expected: dict, provider: str, registered_at: datetime) -> dict:
    binding = _read_reference(
        connection, reference, kind=SOCCER_PROJECTION_BINDING_KIND, source=SOCCER_PROJECTION_BINDING_SOURCE,
    )
    document = _document(binding["content"], set(expected) | {"projection", "version"},
                         "papersim_prepare_soccer_projection_binding_invalid")
    _require(all(document[key] == value for key, value in expected.items()),
             "papersim_prepare_soccer_projection_binding_conflict")
    projection = _read_reference(
        connection, document["projection"], kind=f"{provider}_xg", source=SOCCER_PROJECTION_BINDING_SOURCE,
    )
    _require(_utc(projection["fetched_at"]) <= _utc(binding["fetched_at"]) <= registered_at,
             "papersim_prepare_soccer_projection_binding_conflict")
    return projection


def _soccer_source_metadata(connection, sources: dict, target: dict, response_rows, ctx: dict,
                            registered_at: datetime) -> dict | None:
    expected_keys = {"provider", "league", "season", "fixture_id", "home_team_id", "away_team_id",
                     "responses", "projection_binding"}
    code = "papersim_prepare_soccer_sources_invalid"
    _require(expected_keys <= sources.keys() <= expected_keys | {"away_league", "bridge", "news"}, code)
    provider, league, season = (sources[key] for key in ("provider", "league", "season"))
    _require(provider in (understat.SOURCE_KIND, "fotmob") and _text(league)
             and type(season) is str and _SEASON.fullmatch(season) and int(season) > 1
             and all(_text(sources[key]) for key in ("fixture_id", "home_team_id", "away_team_id")),
             code)
    _require(all(_text(league) for league in _soccer_leagues(sources)), code)
    reference = sources["projection_binding"]
    if reference is None:
        return None
    evidence = [
        {"sequence": item.sequence, "source_kind": item.source_kind, "source_ref": item.source_ref,
         "received_at": item.received_at, "snapshot_sha256": item.evidence.content_sha256}
        for item in response_rows if item.evidence is not None
    ]
    expected = {
        **ctx, **{key: target[key] for key in ("condition_id", "market_id", "gamma_event_id", "terms_sha256",
                                              "event_start_at")},
        **{key: sources[key] for key in ("provider", "league", "season", "fixture_id", "home_team_id", "away_team_id")},
        "responses": operations.canonical_document(evidence),
    }
    if "away_league" in sources:
        expected["away_league"] = sources["away_league"]
    return _soccer_projection(connection, reference, expected, provider, registered_at)


def _soccer_news(row: dict, target: dict, sources: dict, fixture: str, cutoff: datetime):
    code = "papersim_prepare_soccer_news_binding_invalid"
    document = _document(
        row["content"], {"version", "condition_id", "gamma_event_id", "fixture", "home_team_id",
                         "away_team_id", "kickoff_at", "observed_at", "delta", "concordant",
                         "blockers", "source_refs", "lineup_confirmation"}, code,
    )
    _require((document["condition_id"], document["gamma_event_id"], document["fixture"],
              document["home_team_id"], document["away_team_id"], _utc(document["kickoff_at"]))
             == (target["condition_id"], target["gamma_event_id"], fixture,
                 sources["home_team_id"], sources["away_team_id"], _utc(target["event_start_at"])), code)
    fetched_at, observed_at = _utc(row["fetched_at"]), _utc(document["observed_at"])
    _require(observed_at <= fetched_at, code)
    if fetched_at > cutoff:
        raise prepare._Excluded("papersim_prepare_model_evidence_after_cutoff")
    _require(_text(document["delta"]) and type(document["concordant"]) is bool
             and type(document["blockers"]) is list and all(_text(item) for item in document["blockers"])
             and type(document["source_refs"]) is list and bool(document["source_refs"])
             and all(_text(item) for item in document["source_refs"]), code)
    lineup = document["lineup_confirmation"]
    if lineup is not None:
        lineup = _compatible_float(_exact_number(lineup))
        _require(0 <= lineup <= 1, code)
    evidence = contracts.FetchEvidence.from_bytes(
        source_kind="soccer-news", source_reference=row["source"], fetched_at=fetched_at,
        source_observed_at=observed_at, raw_bytes=row["content"],
    )
    return gates.NewsCheck(fixture, document["delta"], document["concordant"],
                           tuple(document["blockers"]), tuple(document["source_refs"])), evidence, lineup


def _soccer_materials(sources: dict, selected: tuple[_ModelResponse, ...], view, projected: dict | None,
                      kickoff: datetime, event: dict, bridge: dict | None) -> tuple[prepare.SoccerInputs, str, str]:
    provider, league, season = (sources[key] for key in ("provider", "league", "season"))
    home_league, away_league = _soccer_leagues(sources)
    _require(all(item.source_kind == provider for item in selected), "papersim_prepare_soccer_source_conflict")
    evidence = tuple(item.usable(view.cutoff) for item in selected)
    if not evidence:
        raise prepare._Excluded("papersim_prepare_model_materials_missing")
    if provider == understat.SOURCE_KIND:
        if home_league not in understat.UNDERSTAT_LEAGUES or away_league not in understat.UNDERSTAT_LEAGUES:
            raise prepare._Excluded("papersim_prepare_soccer_league_unsupported")
        snapshot, fixtures = _understat_snapshot(evidence, season)
    else:
        if home_league != away_league:
            raise prepare._Excluded("papersim_prepare_soccer_projection_unregistered")
        snapshot, fixtures = _fotmob_snapshot(evidence, season, league)
    if projected is not None:
        _require(operations.document_bytes(snapshot) == projected["content"],
                 "papersim_prepare_soccer_projection_mismatch")
        _require(max(item.fetched_at for item in evidence) <= _utc(projected["fetched_at"]),
                 "papersim_prepare_soccer_projection_binding_conflict")
    home, away = _soccer_clubs(sources, snapshot, fixtures, kickoff, event)
    prepare._validate_xg_cutoff(snapshot, view.cutoff)
    # Recent form comes from the same reconstructed originals and the r23
    # context helper. Leave the 10-match statistic unknown if it lacks rows.
    forms = []
    for title, team_league in ((home, home_league), (away, away_league)):
        keys = (xg.season_key(team_league, season), xg.season_key(team_league, xg.prior_season(season)))
        forms.append(xg.recent_form({key: snapshot[key] for key in keys}, title, season=season))
    hits = sum(form.over_2_5_hits for form in forms) if all(form and form.games == 5 for form in forms) else None
    inputs = prepare.SoccerInputs(snapshot, home_league, away_league, season, evidence, o25_hits=hits)
    if bridge is not None:
        if _utc(bridge["fetched_at"]) > view.cutoff:
            raise prepare._Excluded("papersim_prepare_model_evidence_after_cutoff")
        body = journal.decode_public_json(bridge["content"])
        kappa = league_strength.kappa_from_report(
            {"kappa": {key: float(value) if type(value) is Decimal else value for key, value in body["kappa"].items()}}
        )
        _require(dict(kappa.kappa) == dict(league_strength.KAPPA.kappa), "papersim_prepare_soccer_bridge_conflict")
        citation = contracts.FetchEvidence.from_bytes(
            source_kind="soccer-kappa", source_reference=bridge["source"], fetched_at=_utc(bridge["fetched_at"]),
            raw_bytes=bridge["content"],
        )
        inputs = replace(inputs, bridge=kappa.kappa, evidence=inputs.evidence + (citation,))
    return inputs, home, away


def _scan_configuration(config_bytes: bytes) -> scan.ScanConfig:
    code = "papersim_prepare_config_invalid"
    raw = journal.decode_public_json(config_bytes)
    _require(type(raw) is dict and raw.keys() <= {"team", "operator", "queues"}
             and _text(raw.get("team")) and ("operator" not in raw or _text(raw["operator"]))
             and type(raw.get("queues")) is list, code)
    _require(all(type(queue) is dict and all(type(value) is str for value in queue.values())
                 for queue in raw["queues"]), code)
    config = scan.config_from_mapping(raw)
    _require(len({queue.queue for queue in config.queues}) == len(config.queues), code)
    return config


def _validate_targets(values: object, queues: dict) -> tuple[dict, ...]:
    code = "papersim_prepare_target_invalid"
    _require(type(values) is list, code)
    seen, aliases = set(), {}
    for target in values:
        _fields(target, _TARGET_FIELDS, code)
        identity = ("queue", "condition_id", "market_id", "slug", "gamma_event_id", "tag_slug",
                    "market_kind", "model_side", "buy_side")
        _require(all(_text(target[key]) for key in identity) and target["queue"] in queues
                 and target["buy_side"] in ("YES", "NO") and re.fullmatch(r"0x[0-9a-f]{64}", target["condition_id"]),
                 code)
        _utc(target["event_start_at"])
        _utc(target["expires_at"])
        snapshots.snapshot_digest_argument(target["terms_sha256"])
        if target["line"] is not None:
            _exact_number(target["line"])
        key = target["market_id"], target["buy_side"], target["market_kind"]
        _require(key not in seen, "papersim_prepare_candidate_key_duplicate")
        seen.add(key)
        market_identity = tuple(target[key] for key in ("market_id", "slug", "gamma_event_id", "terms_sha256",
                                                        "market_kind", "event_start_at", "expires_at", "line"))
        _require(aliases.setdefault(target["condition_id"], market_identity) == market_identity,
                 "papersim_prepare_target_identity_conflict")
    return tuple(sorted(values, key=lambda row: (row["queue"], row["condition_id"], row["buy_side"])))


def _resolve_target(connection, target: dict, selection: prepare.MarketTarget, queue, view, policy,
                    responses: dict, event_cache: dict, citations: list[dict], ctx: dict,
                    providers: Mapping[str, LineageSpecs], registered_at: datetime):
    sources, selected, projection, bridge, news = target["sources"], (), None, None, None
    kind = target["market_kind"]
    if sources is not None:
        if kind == pipeline.KIND_CRYPTO_TERMINAL:
            _fields(sources, {"spot", "candles", "window_start", "window_end"},
                    "papersim_prepare_crypto_sources_invalid")
            _require(type(sources["candles"]) is list, "papersim_prepare_crypto_sources_invalid")
            selected = _model_selection(responses, [sources["spot"], *sources["candles"]], citations)
        elif kind in _SOCCER_TYPES:
            _require(type(sources) is dict, "papersim_prepare_soccer_sources_invalid")
            selected = _model_selection(responses, sources.get("responses"), citations)
            projection = _soccer_source_metadata(connection, sources, target, selected, ctx, registered_at)
            if sources.get("bridge") is not None:
                bridge = _read_reference(connection, sources["bridge"], kind=SOCCER_BRIDGE_KIND,
                                         source=SOCCER_BRIDGE_SOURCE)
                _require(_utc(bridge["fetched_at"]) <= registered_at, "papersim_prepare_soccer_bridge_conflict")
            if sources.get("news") is not None:
                news = _read_reference(connection, sources["news"], kind=SOCCER_NEWS_KIND, source=SOCCER_NEWS_SOURCE)
                _require(_utc(news["fetched_at"]) <= registered_at, "papersim_prepare_soccer_news_binding_invalid")
            citations.extend(_provenance(row) for row in (projection, bridge, news) if row is not None)
        else:
            _require(sources == [], "papersim_prepare_target_sources_invalid")
    try:
        market, raw, event_id, tokens = prepare._gamma_market(view, selection, citations)
    except prepare._Excluded as error:
        if str(error) in {"papersim_prepare_market_terms_mismatch", "papersim_prepare_event_identity_mismatch"}:
            raise prepare.PreparationError(str(error)) from error
        condition = view.markets.get(selection.condition_id)
        if condition is not None and condition["market_id"] != selection.plan.market_id:
            raise prepare.PreparationError("papersim_prepare_target_identity_conflict") from error
        raise
    _require(market.slug == target["slug"] and event_id == target["gamma_event_id"]
             and market.end_at == _utc(target["expires_at"]), "papersim_prepare_target_identity_conflict")
    event = _find_event(view, event_cache, citations[-1])
    tags = {item["slug"] for item in event.get("tags") or ()
            if type(item) is dict and _text(item.get("slug"))}
    if {"nba", "basketball"}.intersection(tags | {selection.tag_slug}) or queue.lineage.startswith("nba/"):
        raise prepare._Excluded("papersim_prepare_market_kind_unsupported")
    _queue_binding(queue, kind)
    if kind == pipeline.KIND_CRYPTO_TOUCH:
        raise prepare._Excluded("papersim_prepare_crypto_touch_state_unverified")
    buy_token = tokens[selection.buy_side]
    plan = replace(selection.plan, buy_token=buy_token)
    other_side = "NO" if selection.buy_side == "YES" else "YES"
    book_tokens = (buy_token, tokens[other_side]) if kind in pipeline.CRYPTO_KINDS else (buy_token,)
    for token in book_tokens:
        book, _ = prepare._book(view, selection, token, policy, citations)
        payload = journal.decode_public_json(book.entry.content)
        if payload.get("market") is None:
            raise prepare._Excluded("papersim_prepare_book_condition_unproved")
    if sources is None:
        raise prepare._Excluded("papersim_prepare_model_materials_missing")
    if kind in pipeline.CRYPTO_KINDS:
        contract = _crypto_contract(target, market, raw, view.cutoff)
        materials = _crypto_materials(sources, selected, view, contract, providers[queue.lineage].active)
        asset = contract[0]
        fee_category = "crypto" if market.category in ("crypto", "Crypto") else None
        plan = replace(plan, asset=asset, fee_category=fee_category)
    else:
        kickoff = market.game_start_at
        if kickoff is None or kickoff <= view.cutoff:
            raise prepare._Excluded("papersim_prepare_soccer_kickoff_unavailable")
        _require(kickoff == _utc(target["event_start_at"]), "papersim_prepare_target_clock_conflict")
        home_league, away_league = _soccer_leagues(sources)
        cross_league = home_league != away_league
        scope = queue_policy.SCOPE_CROSS_LEAGUE if cross_league else queue_policy.SCOPE_DOMESTIC
        _require(queue.league_scope == scope and (bridge is None or cross_league),
                 "papersim_prepare_target_queue_conflict")
        if cross_league and projection is None:
            raise prepare._Excluded("papersim_prepare_soccer_fixture_binding_unregistered")
        eligible_tags = _SOCCER_CROSS_TAGS if cross_league else _SOCCER_TAGS.get(home_league, set())
        if (selection.tag_slug not in eligible_tags
                and (selection.tag_slug != "soccer" or not tags.intersection(eligible_tags))):
            raise prepare._Excluded("papersim_prepare_soccer_league_scope_unproved")
        event_start = event.get("startTime")
        if event_start is not None:
            _require(prepare._utc(event_start) == kickoff, "papersim_prepare_target_clock_conflict")
        materials, home, away = _soccer_materials(sources, selected, view, projection, kickoff, event, bridge)
        line, team_side = _soccer_terms(target, market, event, raw, home, away)
        materials = replace(materials, team_side=team_side)
        fee_category = "sports" if market.category in ("sports", "Sports") else None
        plan = replace(plan, fixture=event["title"], home=home, away=away, league=materials.home_league,
                       line=line, fee_category=fee_category)
        if news is not None:
            check, evidence, lineup = _soccer_news(news, target, sources, plan.fixture, view.cutoff)
            materials = replace(materials, lineup_confirmation=lineup)
            selection = replace(selection, news=check, news_evidence=(evidence,))
    sources_used = tuple(item.citation() for item in selected)
    sources_used += tuple(_provenance(row) for row in (projection, bridge, news) if row is not None)
    return replace(selection, plan=plan, materials=materials), sources_used


def _specs(connection, lineages: set[str]) -> Mapping[str, LineageSpecs]:
    if not lineages:
        return MappingProxyType({})
    provider = RegistryProvider.from_connection(connection)
    result = {}
    for lineage in sorted(lineages):
        found = provider.specs_for_lineage(lineage)
        prefix, _ = scan_prefix_of(lineage)
        if prefix == "crypto":
            _require(found.active.family == crypto.FAMILY_ID and found.active.data_contract == crypto.DATA_CONTRACT
                     and found.active.procedure_version == crypto.PROCEDURE_VERSION,
                     "papersim_prepare_crypto_spec_unsupported")
        else:
            _require(found.active.spec_sha256() == soccer_frozen.SOCCER_FROZEN.baseline_spec().spec_sha256(),
                     "papersim_prepare_soccer_spec_unsupported")
        result[lineage] = found
    return MappingProxyType(result)


def _unbound_observations(view, digest: str) -> tuple[dict, ...]:
    rows = []
    for item in view.manifest["unbound_market_appearances"]:
        response = view.responses.get(item["sequence"])
        _require(response is not None and response.row["source_kind"] == polymarket.SOURCE_KIND_GAMMA,
                 "papersim_prepare_gamma_location_invalid")
        indexed, _ = view.page(item["sequence"])[item["path"]]
        _require(acquire._condition_identity(indexed) is None, "papersim_prepare_gamma_identity_mismatch")
        rows.append(response.citation(path=item["path"], event_id=indexed["event_id"], batch_sha256=digest))
    return tuple(sorted(rows, key=lambda item: (item["sequence"], item["path"])))


def _candidate_identity(view, selection, queue, target):
    citations = []
    try:
        market, _, event_id, tokens = prepare._gamma_market(view, selection, citations)
    except prepare._Excluded:
        return None
    if market.slug != target["slug"] or event_id != target["gamma_event_id"]:
        raise prepare.PreparationError("papersim_prepare_target_identity_conflict")
    return {
        "queue": queue.queue, "team": selection.pack_spec.team, "candidate_key": selection.candidate_key,
        "side": selection.buy_side, "market_kind": selection.plan.market_kind,
        "lineage": queue.lineage, "model_name": queue.model_key, "market_type": queue.market_type,
        "evaluated_at": view.cutoff.isoformat(), "condition_id": market.condition_id,
        "market_id": market.market_id, "market_slug": market.slug,
        "yes_token_id": tokens["YES"], "no_token_id": tokens["NO"],
        "events": {"market_class": selection.plan.market_class, "gamma_event_id": event_id,
                   "event_start_at": target["event_start_at"], "expiry_at": target["expires_at"],
                   "market_ref": market.market_id},
    }


def _resolve(connection, repo_root: Path, config_bytes: bytes, config: scan.ScanConfig,
             input_set_sha256: str, instance_id: str, *, pinned_specs=None) -> ResolvedPreparationInputs:
    row = _read_snapshot(connection, input_set_sha256, kind=INPUT_SET_KIND, source=INPUT_SET_SOURCE)
    document = _document(row["content"], _INPUT_FIELDS, "papersim_prepare_input_set_invalid")
    _require(document["instance_id"] == instance_id, "papersim_prepare_instance_mismatch")
    registered_at = _utc(row["fetched_at"])
    stored_config = _read_reference(connection, document["config"], kind=CONFIG_KIND, source=CONFIG_SOURCE)
    _require(stored_config["content"] == config_bytes and _utc(stored_config["fetched_at"]) <= registered_at,
             "papersim_prepare_config_binding_conflict")
    queues_by_id = {queue.queue: queue for queue in config.queues}
    queue_ids = document["queues"]
    _require(type(queue_ids) is list and all(_text(key) and key in queues_by_id for key in queue_ids)
             and len(set(queue_ids)) == len(queue_ids), "papersim_prepare_queue_scope_invalid")
    queues = {key: queues_by_id[key] for key in queue_ids}
    _require(all(not queue.pack for queue in queues.values()), "papersim_prepare_queue_pack_ambiguous")
    targets = _validate_targets(document["targets"], queues)
    view, entries, receipt, end = _read_capture(connection, repo_root, document, registered_at)
    input_receipt = _success_receipt(
        connection, input_set_operation_key(stored_config["content_sha256"], view.manifest["scope"]),
        INPUT_SET_COMMAND, code="papersim_prepare_input_set_receipt_invalid",
    )
    _require(input_receipt["output_evidence"] == operations._refs(input_set_sha256, row["byte_length"])
             and input_receipt["config_digest"] == stored_config["content_sha256"]
             and input_receipt["scheduled_for"] == view.start
             and receipt["occurred_at"] <= registered_at == input_receipt["occurred_at"]
             and type(document["coverage"]) is dict,
             "papersim_prepare_input_set_receipt_invalid")
    digest = receipt["output_evidence"][0]
    policy, policy_digest = _book_age_policy(connection, document["interval"], view.start, end,
                                             instance_id=instance_id, team=config.team)
    responses = _model_responses(connection, document["model_responses"], registered_at)
    lineage_ids = {queues[target["queue"]].lineage for target in targets
                   if target["market_kind"] in _SOCCER_TYPES or target["market_kind"] in _CRYPTO_TYPES}
    specs = _specs(connection, lineage_ids) if pinned_specs is None else pinned_specs
    _require(lineage_ids <= specs.keys(), "papersim_prepare_lineage_spec_missing")
    accepted, excluded, cache, source_refs, identities = [], [], {}, {}, {}
    ctx = {"instance_id": instance_id, "batch_sha256": digest, "decision_as_of": view.cutoff.isoformat()}
    for target in targets:
        queue = queues[target["queue"]]
        selection = _selection(target, config, queue)
        if queue.lineage in specs:
            identity = _candidate_identity(view, selection, queue, target)
            if identity is not None:
                identities[selection.candidate_key] = identity
        citations = [{"batch_sha256": digest, "condition_id": selection.condition_id,
                      "market_id": selection.plan.market_id, "tag_slug": selection.tag_slug}]
        try:
            verified, selected_refs = _resolve_target(connection, target, selection, queue, view, policy, responses,
                                                       cache, citations, ctx, specs, registered_at)
            accepted.append(verified)
            source_refs[verified.candidate_key] = selected_refs
        except prepare._Excluded as error:
            excluded.append(prepare.ExcludedTarget(selection.candidate_key, queue.queue, str(error), tuple(citations)))
    return ResolvedPreparationInputs(
        input_set_sha256, stored_config["content_sha256"], digest, view.start, end, view.cutoff,
        MappingProxyType(receipt), config, tuple(sorted(queues)), view.manifest, entries, tuple(accepted), specs,
        policy, policy_digest, MappingProxyType(source_refs), tuple(excluded),
        (*_unbound_observations(view, digest), *document["coverage"].get("unbound", [])),
        MappingProxyType(identities),
    )


def resolve_preparation_inputs(connection=None, *, repo_root: Path, config_bytes: bytes,
                               input_set_sha256: str) -> ResolvedPreparationInputs:
    """Load one input set from an idle managed app connection or open one here."""
    try:
        snapshots.snapshot_digest_argument(input_set_sha256)
        _require(type(config_bytes) is bytes, "papersim_prepare_config_invalid")
        config = _scan_configuration(config_bytes)
        layout = Layout(repo_root)
        root = layout.root
        if connection is None:
            with closing(store_psycopg.connect_managed(root)) as managed:
                return _resolve_managed(managed, layout, config_bytes, config, input_set_sha256)
        return _resolve_managed(connection, layout, config_bytes, config, input_set_sha256)
    except prepare.PreparationError:
        raise
    except (acquire.AcquisitionError, capture.CaptureError, store.PapersimStoreError) as error:
        raise prepare.PreparationError(error.code) from None
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError, InvalidOperation, IndexError):
        raise prepare.PreparationError("papersim_prepare_input_set_invalid") from None
    except Exception:
        # Connection and driver diagnostics may contain private data.
        raise prepare.PreparationError("papersim_prepare_managed_read_failed") from None


def _resolve_managed(connection, layout: Layout, config_bytes: bytes, config: scan.ScanConfig,
                     input_set_sha256: str) -> ResolvedPreparationInputs:
    root = layout.root
    _require(getattr(connection, "_papersim_managed_root", None) == root and not connection.closed,
             "papersim_prepare_managed_required")
    info = store_psycopg.read_instance(root)
    _require(info["root_sha256"] == layout.root_hash, "papersim_prepare_instance_mismatch")
    with store_psycopg.d1_read_view(connection):
        try:
            store_psycopg.d1_resolution_identity(connection, instance_id=info["instance_id"])
        except ProjectDatabaseError:
            raise prepare.PreparationError("papersim_prepare_instance_mismatch") from None
        return _resolve(connection, root, config_bytes, config, input_set_sha256, info["instance_id"])
