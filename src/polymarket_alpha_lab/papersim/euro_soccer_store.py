"""Managed PostgreSQL evidence and immutable observation slots for European football."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from http.client import HTTPException
import json
import re
from threading import Lock
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from polymarket_alpha_lab.public_http import read_public_body
from . import euro_soccer_markets as markets
from . import euro_soccer_sources as sources
from . import fotmob, snapshots
from .adapters import contracts, polymarket as pm


_NAMESPACE = "papersim/euro-soccer-study/v1"
_RAW = "euro_soccer_public_body_v1"
_RECEIPT = "euro_soccer_public_receipt_v1"
_DOCUMENT = "euro_soccer_study_document_v1"
_SOURCE_PREFIX = _NAMESPACE + "/documents/"
_SCAN_LOCK = _NAMESPACE + "/scan"
_FOTMOB_LEAGUES = {str(spec.fotmob_id): league for league, spec in sources.LEAGUES.items()}
_GAMMA_PARAMS = frozenset(("tag_id", "sports_market_types", "closed", "active", "limit", "order",
                           "ascending", "end_date_min", "end_date_max", "offset"))


@dataclass(frozen=True)
class ArchivedResponse(markets.PublicResponse):
    raw: bytes


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _request(url, body):
    """Accept only the fixed public study endpoints and the read-only books POST."""
    if not isinstance(url, str) or len(url) > 4096 or any(not 32 < ord(ch) < 127 for ch in url):
        raise ValueError("euro_study_public_request_invalid")
    split = urlsplit(url)
    if split.scheme != "https" or split.fragment or "\\" in url:
        raise ValueError("euro_study_public_request_invalid")
    endpoint = f"{split.scheme}://{split.netloc}{split.path}"
    pairs = parse_qsl(split.query, strict_parsing=True, keep_blank_values=True, max_num_fields=12)
    query = dict(pairs)
    if len(query) != len(pairs):
        raise ValueError("euro_study_public_query_invalid")

    if body is not None:
        if (url != pm.CLOB_BASE_URL + "/books" or not isinstance(body, list) or not 1 <= len(body) <= 8
                or not all(isinstance(row, dict) and row.keys() == {"token_id"}
                           and isinstance(row["token_id"], str)
                           and re.fullmatch(r"[1-9][0-9]{0,77}", row["token_id"]) for row in body)):
            raise ValueError("euro_study_books_request_invalid")
        body = [{"token_id": row["token_id"]} for row in body]
    elif split.netloc == fotmob.HOST:
        if split.path == "/api/data/leagues" and query.keys() == {"id", "season"} and query["id"] in _FOTMOB_LEAGUES:
            expected = sources.calendar_url(_FOTMOB_LEAGUES[query["id"]], query["season"])
        elif split.path == "/api/data/matchDetails" and query.keys() == {"matchId"}:
            expected = sources.match_url(query["matchId"])
        else:
            raise ValueError("euro_study_fixture_request_invalid")
        if url != expected:
            raise ValueError("euro_study_fixture_request_invalid")
    elif endpoint in (pm.GAMMA_MARKETS_BY_CONDITION_URL, pm.GAMMA_EVENTS_URL):
        if (query.get("tag_id") not in markets.LEAGUE_TAGS.values() or not query.keys() <= _GAMMA_PARAMS
                or not all(re.fullmatch(r"[A-Za-z0-9.:+-]{1,48}", value) for value in query.values())):
            raise ValueError("euro_study_gamma_request_invalid")
    else:
        raise ValueError("euro_study_public_request_invalid")
    return {"method": "GET" if body is None else "POST", "url": url, "body": body}


def _download(request) -> bytes:
    data = _encode(request["body"]) if request["body"] is not None else None
    headers = {"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": fotmob.USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    message = Request(request["url"], method=request["method"], data=data, headers=headers)
    opener = build_opener(ProxyHandler({}), pm._NoRedirect())
    try:
        with opener.open(message, timeout=contracts.DEFAULT_REQUEST_TIMEOUT_SECONDS) as response:
            pm._verify_public_response(response, request["url"])
            return read_public_body(response, contracts.HARD_MAX_RESPONSE_BYTES)
    except HTTPError as exc:
        exc.close()
        raise ValueError("euro_study_http_rejected") from None
    except (HTTPException, OSError, ValueError, pm._PublicReadError):
        raise ValueError("euro_study_public_response_invalid") from None


def _document_source(key: str, *, prefix=False) -> str:
    if (not isinstance(key, str) or (not key and not prefix) or len(key) > 1024
            or any(ord(ch) < 32 for ch in key)):
        raise ValueError("euro_study_key_invalid")
    return _SOURCE_PREFIX + key


def _document(key, raw):
    envelope = json.loads(bytes(raw))
    if (envelope.get("namespace") != _NAMESPACE or envelope.get("key") != key
            or not isinstance(envelope.get("document"), dict)):
        raise ValueError("euro_study_document_binding_invalid")
    return envelope["document"]


class StudyStore:
    """One verified application connection. Each database block finishes before network I/O."""

    def __init__(self, conn):
        if getattr(conn, "_papersim_managed_root", None) is None:
            raise ValueError("euro_study_managed_connection_required")
        self.conn = conn
        # PostgreSQL session locks are reentrant. Two users of the same session
        # must report busy too, including two StudyStore wrappers for one connection.
        self._session_guard = vars(conn).setdefault("_euro_soccer_study_guard", Lock())

    def _require_idle(self):
        if self.conn.info.transaction_status.name != "IDLE":
            raise ValueError("euro_study_connection_not_idle")

    @contextmanager
    def scan_lock(self):
        if not self._session_guard.acquire(blocking=False):
            yield False
            return
        acquired = False
        try:
            self._require_idle()
            with self.conn.transaction():
                acquired = self.conn.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (_SCAN_LOCK,)).fetchone()[0]
            yield acquired
        finally:
            try:
                if acquired and not self.conn.closed:
                    self._require_idle()
                    with self.conn.transaction():
                        released = self.conn.execute(
                            "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (_SCAN_LOCK,)).fetchone()[0]
                        if not released:
                            raise RuntimeError("euro_study_session_lock_lost")
            except BaseException:
                self.conn.close()
                raise
            finally:
                self._session_guard.release()

    def _register(self, content, kind, source, fetched_at):
        return snapshots.register_snapshot(self.conn, snapshots.SnapshotRegistration(content, kind, source, fetched_at))

    def _cached(self, source, now, max_age):
        with self.conn.transaction():
            row = self.conn.execute(
                "SELECT content, fetched_at FROM papersim.snapshots "
                "WHERE kind = %s AND source = %s AND fetched_at BETWEEN %s AND %s "
                "ORDER BY fetched_at DESC, content_sha256 DESC LIMIT 1",
                (_RECEIPT, source, now - max_age, now)).fetchone()
            if row is None:
                return None
            receipt = json.loads(bytes(row[0]))
            digest = receipt["body_sha256"]
            registered = snapshots.read_snapshot(self.conn, digest)
            if (receipt["namespace"] != _NAMESPACE or receipt["request_key"] != source
                    or datetime.fromisoformat(receipt["received_at"]) != row[1] or registered is None
                    or datetime.fromisoformat(receipt["requested_at"]) > row[1]
                    or sha256(registered["content"]).hexdigest() != digest):
                raise ValueError("euro_study_cached_receipt_invalid")
            return registered["content"], row[1], digest

    def fetch(self, url, body=None, *, max_age: timedelta | None = None) -> ArchivedResponse:
        """Register the exact response and a distinct network receipt before decoding."""
        self._require_idle()
        request = _request(url, body)
        source = _NAMESPACE + "/requests/" + sha256(_encode(request)).hexdigest()
        requested_at = _utc_now()
        if max_age is not None and (not isinstance(max_age, timedelta) or max_age < timedelta(0)):
            raise ValueError("euro_study_cache_age_invalid")
        archive = None if max_age is None else self._cached(source, requested_at, max_age)
        if archive is None:
            raw = _download(request)
            received_at = _utc_now()
            with self.conn.transaction():
                digest = self._register(raw, _RAW, source, received_at)["content_sha256"]
                receipt = {"namespace": _NAMESPACE, "request_key": source, "receipt_id": str(uuid4()),
                           "request": request, "requested_at": requested_at.isoformat(),
                           "received_at": received_at.isoformat(), "body_sha256": digest}
                registered = self._register(_encode(receipt), _RECEIPT, source, received_at)
                if registered["kind"] != _RECEIPT or registered["source"] != source:
                    raise ValueError("euro_study_receipt_binding_invalid")
            if received_at < requested_at:
                raise ValueError("euro_study_receipt_time_invalid")
        else:
            raw, received_at, digest = archive
        return ArchivedResponse(json.loads(raw), received_at, digest, raw)

    def _latest(self, key, source):
        row = self.conn.execute(
            "SELECT content FROM papersim.snapshots WHERE kind = %s AND source = %s",
            (_DOCUMENT, source)).fetchone()
        return _document(key, row[0]) if row is not None else None

    def latest(self, key: str) -> dict | None:
        self._require_idle()
        source = _document_source(key)
        with self.conn.transaction():
            return self._latest(key, source)

    def save(self, key: str, document: dict) -> dict:
        """Seal a slot once and return the original document to any later callers."""
        self._require_idle()
        source = _document_source(key)
        if not isinstance(document, dict):
            raise ValueError("euro_study_document_invalid")
        raw = _encode({"namespace": _NAMESPACE, "key": key, "document": document})
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            self.conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (source,))
            existing = self._latest(key, source)
            if existing is not None:
                return existing
            registered = self._register(raw, _DOCUMENT, source, _utc_now())
            if registered["kind"] != _DOCUMENT or registered["source"] != source:
                raise ValueError("euro_study_document_binding_invalid")
            return _document(key, raw)

    def keys(self, prefix: str) -> set[str]:
        self._require_idle()
        source_prefix = _document_source(prefix, prefix=True)
        with self.conn.transaction():
            rows = self.conn.execute(
                "SELECT source FROM papersim.snapshots WHERE kind = %s AND starts_with(source, %s)",
                (_DOCUMENT, source_prefix)).fetchall()
        return {source.removeprefix(_SOURCE_PREFIX) for source, in rows}

    def records(self, prefix: str) -> list[tuple[str, dict]]:
        self._require_idle()
        source_prefix = _document_source(prefix, prefix=True)
        with self.conn.transaction():
            rows = self.conn.execute(
                "SELECT source, content FROM papersim.snapshots WHERE kind = %s AND starts_with(source, %s) "
                "ORDER BY source", (_DOCUMENT, source_prefix)).fetchall()
        records = []
        for source, raw in rows:
            key = source.removeprefix(_SOURCE_PREFIX)
            records.append((key, _document(key, raw)))
        return records
