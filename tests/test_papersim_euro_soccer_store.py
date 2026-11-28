"""Request boundaries, reception times and concurrent seals for European shadow research."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from email.message import Message
from hashlib import sha256
from io import BytesIO
import json
import os
from threading import Barrier
from urllib.request import Request

import pytest

from polymarket_alpha_lab.papersim import euro_soccer_store as store
from polymarket_alpha_lab.papersim import store_psycopg
from polymarket_alpha_lab.papersim.euro_soccer_sources import calendar_url, match_url
from tests.test_papersim_report_store_native import _managed_test_postgres, repo  # noqa: F401


GAMMA = store.pm.GAMMA_MARKETS_BY_CONDITION_URL
BOOKS = store.pm.CLOB_BASE_URL + "/books"
NATIVE = pytest.mark.skipif(os.environ.get("POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES") != "1",
                            reason="explicit native papersim proof is opt-in")


@pytest.mark.parametrize("url,body", [
    (calendar_url("nor", "2026"), None), (match_url("123"), None),
    (GAMMA + "?tag_id=101772&closed=false", None), (BOOKS, [{"token_id": "123"}]),
    (store.pm.GAMMA_EVENTS_URL + "?tag_id=101772", None),
])
def test_study_permits_only_public_research_reads(url, body):
    assert store._request(url, body) == {"method": "GET" if body is None else "POST", "url": url, "body": body}


@pytest.mark.parametrize("url,body", [
    (GAMMA + "?tag_id=101772", []), (match_url("123"), [{"token_id": "1"}]), (BOOKS, None),
    (BOOKS + "?x=y", [{"token_id": "1"}]), (BOOKS.replace("/books", "/x"), [{"token_id": "1"}]),
    (BOOKS, [{"token_id": "1", "extra": "2"}]), (BOOKS, [{"token_id": "x"}]), (BOOKS, []),
    (BOOKS, [{"token_id": "1"}] * 9), (GAMMA + "?tag_id=999", None),
    (GAMMA + "?tag_id=101772&tag_id=101772", None), (GAMMA + "?tag_id=101772&extra=value", None),
    (calendar_url("nor", "2026").replace("=59", "=123"), None), (match_url("123") + "#fragment", None),
    ("http://www.fotmob.com/api/data/matchDetails?matchId=123", None),
    ("https://example.invalid/api/data/matchDetails?matchId=123", None),
    ("https://someone@www.fotmob.com/api/data/matchDetails?matchId=123", None),
    ("https://www.fotmob.com/api/data/matchDetails?matchId=123%0A", None),
])
def test_unregistered_requests_fail_before_transport(url, body):
    with pytest.raises(ValueError):
        store._request(url, body)


def test_network_request_uses_framing_and_cannot_follow_redirects(monkeypatch):
    url = match_url("123")
    payload = b'{ "ok": true }'
    final_url = [url]

    class Response(BytesIO):
        status = 200
        headers = Message()
        headers["Content-Type"] = "application/json; charset=utf-8"
        headers["Content-Length"] = str(len(payload))

        def geturl(self):
            return final_url[0]

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == url
            assert request.get_method() == "GET"
            return Response(payload)

    def opener(proxy, redirect):
        assert proxy.proxies == {}
        assert redirect.redirect_request(Request(url), None, 307, "", Message(), "https://example.invalid/") is None
        return Opener()

    monkeypatch.setattr(store, "build_opener", opener)
    assert store._download(store._request(url, None)) == payload
    final_url[0] = "https://example.invalid/"
    with pytest.raises(ValueError, match="euro_study_public_response_invalid"):
        store._download(store._request(url, None))


@NATIVE
def test_managed_raw_receipts_caching_failure_and_immutable_slots(repo, monkeypatch):
    with _managed_test_postgres(repo[0], monkeypatch) as (_, conn):
        study = store.StudyStore(conn)
        now = [datetime(2026, 11, 28, 12, tzinfo=UTC)]
        body = [b'{ "observation" : 1 }']
        calls = []

        def download(request):
            assert conn.info.transaction_status.name == "IDLE"
            calls.append(request)
            now[0] += timedelta(seconds=1)
            return body[0]

        monkeypatch.setattr(store, "_utc_now", lambda: now[0])
        monkeypatch.setattr(store, "_download", download)
        url = match_url("123")
        first, second = study.fetch(url), study.fetch(url)
        assert first.raw == second.raw == body[0]
        assert first.payload == second.payload == {"observation": 1}
        assert first.sha256 == second.sha256 == sha256(body[0]).hexdigest()
        assert second.received_at == first.received_at + timedelta(seconds=1) == now[0]

        now[0] += timedelta(minutes=2)
        assert study.fetch(url, max_age=timedelta(minutes=2)) == second
        assert len(calls) == 2
        now[0] += timedelta(microseconds=1)
        newer = study.fetch(url, max_age=timedelta(minutes=2))
        assert newer.received_at == now[0] and newer.received_at > second.received_at
        assert len(calls) == 3
        same_bytes = study.fetch(match_url("456"), max_age=timedelta(days=1))
        assert same_bytes.received_at > newer.received_at and same_bytes.sha256 == newer.sha256
        left = study.fetch(BOOKS, [{"token_id": "1"}], max_age=timedelta(days=1))
        right = study.fetch(BOOKS, [{"token_id": "2"}], max_age=timedelta(days=1))
        assert left.received_at < right.received_at
        assert study.fetch(BOOKS, [{"token_id": "1"}], max_age=timedelta(days=1)) == left
        assert len(calls) == 6

        body[0] = b'{"truncated":'
        with pytest.raises(json.JSONDecodeError):
            study.fetch(url)
        assert conn.info.transaction_status.name == "IDLE"
        with conn.transaction():
            receipts = conn.execute("SELECT content FROM papersim.snapshots WHERE kind = %s ORDER BY fetched_at",
                                    (store._RECEIPT,)).fetchall()
            assert conn.execute("SELECT count(*) FROM papersim.snapshots WHERE kind = %s",
                                (store._RAW,)).fetchone() == (2,)
            documents = [json.loads(bytes(row[0])) for row in receipts]
            assert len({r["receipt_id"] for r in documents}) == 7
            assert documents[0]["received_at"] == first.received_at.isoformat()
            assert documents[-1]["body_sha256"] == sha256(body[0]).hexdigest()
            assert documents[-1]["requested_at"] < documents[-1]["received_at"]
            with pytest.raises(ValueError, match="connection_not_idle"):
                study.fetch(url)
        assert len(calls) == 7

        assert study.latest("v1/a") is None
        assert study.save("v1/a", {"values": (1, 2)}) == {"values": [1, 2]}
        assert study.save("v1/a", {"changed": True}) == {"values": [1, 2]}
        assert study.save("v1/a_b", {"exact": True}) == study.save("v1/axb", {"exact": True})
        assert study.keys("v1/a_") == {"v1/a_b"}
        assert study.keys("v1/") == {"v1/a", "v1/a_b", "v1/axb"}
        assert study.records("v1/") == [(k, study.latest(k)) for k in sorted(study.keys("v1/"))]

        # A coarse clock still gives independent reads distinct receipts.
        monkeypatch.setattr(store, "_download", lambda request: b"{}")
        stamp = now[0]
        assert study.fetch(url).received_at == study.fetch(url).received_at == stamp
        with conn.transaction():
            count = conn.execute("SELECT count(*) FROM papersim.snapshots WHERE kind = %s AND fetched_at = %s",
                                 (store._RECEIPT, stamp)).fetchone()[0]
        assert count == 3  # Includes the preceding malformed response at the same instant.

        backwards = iter((stamp + timedelta(hours=1), stamp + timedelta(minutes=30)))
        monkeypatch.setattr(store, "_utc_now", lambda: next(backwards))
        with pytest.raises(ValueError, match="receipt_time_invalid"):
            study.fetch(url)
        monkeypatch.setattr(store, "_utc_now", lambda: stamp + timedelta(hours=1))
        with pytest.raises(ValueError, match="cached_receipt_invalid"):
            study.fetch(url, max_age=timedelta(hours=1))


@NATIVE
def test_managed_session_lock_and_racing_initial_writes(repo, monkeypatch):
    with _managed_test_postgres(repo[0], monkeypatch) as (_, app):
        with store_psycopg.connect_managed(repo[0]) as other:
            first, second = store.StudyStore(app), store.StudyStore(other)
            with pytest.raises(LookupError):
                with first.scan_lock() as acquired:
                    assert acquired and app.info.transaction_status.name == "IDLE"
                    with store.StudyStore(app).scan_lock() as same_session:
                        assert not same_session
                    with second.scan_lock() as busy:
                        assert not busy
                    first.save("v1/test/forward", {"locked": True})
                    with second.scan_lock() as still_busy:
                        assert not still_busy
                    raise LookupError()
            with second.scan_lock() as acquired:
                assert acquired and other.info.transaction_status.name == "IDLE"

            barrier = Barrier(2, timeout=5)
            def race(study, value):
                barrier.wait()
                return study.save("v1/test/closing", {"writer": value})

            with ThreadPoolExecutor(max_workers=2) as pool:
                a = pool.submit(race, first, 1)
                b = pool.submit(race, second, 2)
                assert a.result() == b.result() == first.latest("v1/test/closing")
            assert len(first.records("v1/test/closing")) == 1

            # A caller that leaves the session inside an open transaction cannot
            # return a still-locked session to another owner.
            with pytest.raises(ValueError, match="connection_not_idle"):
                with second.scan_lock() as acquired:
                    assert acquired
                    other.execute("SELECT 1")
            assert other.closed
            with first.scan_lock() as recovered:
                assert recovered
