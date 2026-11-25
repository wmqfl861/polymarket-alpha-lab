"""Managed FX-40 capture, B1 recovery, and same-entry B2/B3 proof."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
import runpy
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from polymarket_alpha_lab.papersim import capture, prepared_scan, prepare_inputs, snapshots, store
from polymarket_alpha_lab.papersim import store_psycopg as pg
from polymarket_alpha_lab.papersim.adapters import polymarket, prices
from polymarket_alpha_lab.papersim.d1compare import acquire, frozenrun, journal, operations
from tests.test_papersim_adapter_polymarket import _response
from tests.test_papersim_capture_marks_native import (
    _D1_NATIVE, _D1_TEAM, _d1_capture_cli, _d1_child, _d1_json_path, _d1_register_binding,
    _d1_register_capture_slot, d1_capture_project,
)
from tests.test_papersim_scan_store_native import _seed_initial_cash


pytestmark = _D1_NATIVE
_CRASH = 87
_BOOK_AGE = {"max_source_age_seconds": 600, "max_receipt_age_seconds": 600}


def _drive_capture():
    from pathlib import Path

    fixture = json.loads(Path(sys.argv[1]).read_bytes())

    class PublicFixture:
        def open(self, url, *, timeout_seconds):
            parsed = urlsplit(url)
            query = parse_qs(parsed.query)
            if parsed.path == "/events" and query["tag_slug"] == ["crypto"]:
                body = ([{"id": "fx40-fixture-event", "markets": [fixture["market"]]}]
                        if query["offset"] == ["0"] else [])
            elif parsed.path == "/book":
                token, = query["token_id"]
                body = fixture["books"][token]
                body["timestamp"] = str(int((datetime.now(UTC).timestamp() - 0.1) * 1000))
            else:
                raise AssertionError("unexpected Gamma or CLOB fixture request")
            return _response(json.dumps(body).encode(), url=url)

    def prices_fixture(url, timeout, read_limit):
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/ticker/price"):
            assert query == {"symbol": ["ETHUSDT"]}
            body = {"symbol": "ETHUSDT", "price": "2450.0"}
        else:
            assert parsed.path.endswith("/klines") and query["interval"] == ["6h"]
            start = int(query["startTime"][0])
            step = prices.BAR_SECONDS * 1000
            body = []
            for index in range(int(query["limit"][0])):
                opened = start + index * step
                close = str(2290 + 7 * (index % 13))
                body.append([opened, close, close, close, close, "1", opened + step - 1,
                             close, 0, "0", "0", "0"])
        raw = json.dumps(body).encode()
        assert len(raw) <= read_limit
        return raw, datetime.now(UTC)

    polymarket.BoundedPublicOpener = PublicFixture
    prices._open_public_json = prices_fixture
    script = Path(sys.argv[2])
    sys.argv = [str(script), *sys.argv[3:]]
    runpy.run_path(str(script), run_name="__main__")


def _fault():
    from polymarket_alpha_lab.papersim import cli

    boundary = sys.argv[1]
    original = prepared_scan._receipt

    def receipt(conn, key, attempt, commit, config_sha, refs, phase, at, **kwargs):
        if phase == "SUCCEEDED" and boundary == "after-business":
            os._exit(_CRASH)
        return original(conn, key, attempt, commit, config_sha, refs, phase, at, **kwargs)

    original_writer = pg.record_scan

    def record_scan(conn, submission):
        if boundary == "before-business":
            os._exit(_CRASH)
        return original_writer(conn, submission)

    prepared_scan._receipt = receipt
    pg.record_scan = record_scan
    raise SystemExit(cli.main(sys.argv[2:]))


def _path_n():
    from pathlib import Path

    script = Path(sys.argv[1])
    sys.argv = [str(script), *sys.argv[2:]]
    runpy.run_path(str(script), run_name="__main__")


def _drive_settle():
    from polymarket_alpha_lab.papersim import cli

    scheduled = datetime.fromisoformat(sys.argv[1])
    original = pg.d1_database_time
    pg.d1_database_time = lambda conn: max(scheduled, original(conn))
    raise SystemExit(cli.main(sys.argv[2:]))


def _ref(row):
    return {key: row[source] for key, source in (("sha256", "content_sha256"), ("byte_length", "byte_length"))}


def _capture_setup(project, name, *, news=True, rules=True, cash=None):
    directory = project.temporary / name
    directory.mkdir()
    config = _d1_json_path(directory, "scan.json", {"team": _D1_TEAM, "queues": [{
        "queue": "fx40-crypto", "lineage": "crypto/terminal", "model_key": "crypto",
        "market_type": "terminal", "data_source_version": "fx40-native-fixture",
    }]})
    condition_id = "0x" + sha256(name.encode()).hexdigest()
    tokens = str(int(condition_id[2:14], 16)), str(int(condition_id[2:14], 16) + 1)
    opening = (datetime.now(UTC) + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    expiry = opening + timedelta(minutes=1)
    when = f"{opening:%B} {opening.day}, {opening.year}"
    question = f"Will Ethereum be below $2,000 on {when}?"
    description = ('This market will resolve to "Yes" if the Close price of the Binance ETH/USDT '
                   '1-minute candle at 12:00 PM UTC on the date in the title is below $2,000. '
                   'Otherwise it will resolve to "No".')
    market = {
        "id": name, "slug": name, "conditionId": condition_id, "question": question,
        "description": description, "category": "Crypto", "endDate": expiry.isoformat(),
        "liquidityNum": 6000, "outcomes": '["No","Yes"]', "outcomePrices": '["0.38","0.62"]',
        "clobTokenIds": json.dumps(tokens),
    }
    books = {
        token: {"asset_id": token, "market": condition_id,
                "bids": [{"price": bid, "size": "5000"}], "asks": [{"price": ask, "size": "5000"}]}
        for token, bid, ask in zip(tokens, ("0.34", "0.59"), ("0.38", "0.63"), strict=True)
    }
    raw = _d1_json_path(directory, "capture-fixture.json", {"market": market, "books": books})
    with pg.connect_managed(project.root) as conn:
        if cash is not None:
            _seed_initial_cash(conn, amount=cash)
        at = pg.d1_database_time(conn)
        start = at + timedelta(milliseconds=700)
        end = start + timedelta(seconds=45)
        checks = {}
        if news:
            news_original = snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
                operations.document_bytes({"version": 1, "fixture": name}), "fx40_native_fixture",
                "papersim:fx40:test-original", at))
            document = {
                "version": 1, "condition_id": condition_id, "terms_sha256": journal._gamma_terms_sha256(market),
                "fixture": name, "observed_at": at, "delta": "before decision", "news_delta": "unchanged",
                "blockers": [], "source_refs": [news_original["content_sha256"]],
            }
            checks[condition_id] = _ref(snapshots.register_snapshot(conn, snapshots.SnapshotRegistration(
                operations.document_bytes(document), prepare_inputs.CRYPTO_NEWS_KIND,
                prepare_inputs.CRYPTO_NEWS_SOURCE, at)))
        interval = {"version": 1, "instance_id": project.instance_id, "team": _D1_TEAM,
                    "starts_at": start, "ends_at": expiry + timedelta(hours=1),
                    "preparation_news": checks}
        if rules:
            interval["book_age_policy"] = _BOOK_AGE
        registered = _d1_register_binding(
            conn, document=interval, kind="d1_operation_interval", source="d1:operation:interval:v1",
            command="d1-register-interval", key_for=lambda digest: f"d1:interval-binding:v1:{digest}",
        )
    path = project.root / "tools/d1_compare.py"
    argv = [str(raw), str(path), "capture-inputs", "--repo-root", str(project.root),
            "--tag-slug", "crypto", "--window-start", start.isoformat(), "--window-end", end.isoformat(),
            "--prepare-config", str(config)]
    code = ("from tests.test_papersim_prepared_scan_native import _drive_capture; _drive_capture()")
    remaining = (start - datetime.now(UTC)).total_seconds()
    if remaining > 0:
        import time

        time.sleep(remaining + 0.02)
    receipt = _d1_child(project, [*argv, "--allow-public-fetch"], code=code)
    assert receipt.returncode == 0, receipt.stderr
    receipt = json.loads(receipt.stdout)
    assert receipt["input_set_sha256"] and receipt["input_set_receipt"]["phase"] == "SUCCEEDED"
    reused = _d1_child(project, argv, code=code)
    assert reused.returncode == 0, reused.stderr
    assert json.loads(reused.stdout)["input_set_sha256"] == receipt["input_set_sha256"]
    return SimpleNamespace(
        project=replace(project, interval_sha256=registered), directory=directory, config=config,
        receipt=receipt, market=market, expiry=expiry, tokens=tokens, key=f"fx40-native:{name}")


def _b1(case, attempt, *, fault=None, path_n=False):
    project = case.project
    args = ["--repo-root", str(project.root), "--prepare-from", case.receipt["input_set_sha256"],
            "--operation-key", case.key, "--attempt", str(attempt)]
    if path_n:
        authorization = _d1_json_path(case.directory, "isolated-gates.json",
                                      dict.fromkeys(frozenrun.REQUIRED_RUN_GATES, "isolated-fixture"))
        return _d1_child(project, [str(project.root / "tools/d1_compare.py"), "run-new",
                                   "--scan-config", str(case.config), "--authorization", str(authorization), *args],
                         code="from tests.test_papersim_prepared_scan_native import _path_n; _path_n()")
    args = ["run-scan", "--managed", "--config", str(case.config), *args]
    if fault:
        return _d1_child(project, [fault, *args],
                         code="from tests.test_papersim_prepared_scan_native import _fault; _fault()")
    return _d1_child(project, args)


def _b2(case, candidate_id, scan_id):
    batch = case.receipt["batch_sha256"]
    manifest, _ = acquire.read_capture(repo_root=case.project.root, batch_sha256=batch)
    token = case.tokens[0]
    book, = (row for row in manifest["books"] if row["token_id"] == token)
    quote = manifest["requests"][book["request_sequence"] - 1]
    condition = case.market["conditionId"]
    mark = {
        "scan_operation_key": case.key, "candidate_key": f"{case.market['id']}|no|crypto_terminal",
        "sampling_kind": "PERIODIC", "observed_at": quote["source_observed_at"],
        "market_ref_kind": "condition_id", "market_ref": condition, "token_ref": token, "side": "NO",
        "bid": 0.34, "ask": 0.38, "mid": None, "last": None, "source_kind": "clob_book",
        "source_ref": f"{quote['url']}#{quote['content_sha256']}", "snapshot_sha256": quote["content_sha256"],
    }
    fixture = SimpleNamespace(project=case.project, directory=case.directory, marks=[mark],
                              candidate_ids=(candidate_id,), capture_manifest_sha256=batch)
    slot = _d1_register_capture_slot(fixture, "b2", **_BOOK_AGE)
    result = _d1_capture_cli(case.project, slot)
    assert result.returncode == 0, result.stderr
    item, = json.loads(result.stdout)["items"]
    assert item["status"] == "WRITTEN"
    with pg.connect_managed(case.project.root) as reader:
        assert reader.execute(
            "SELECT candidate_id, token_ref, side FROM papersim.candidate_marks WHERE candidate_mark_id = %s",
            (item["candidate_mark_id"],),
        ).fetchone() == (candidate_id, token, "NO")
        candidate = pg.d1_candidate_capture_target(
            reader, scan_id=scan_id, candidate_key=mark["candidate_key"])
        assert candidate["scan_receipt_witness"]["recovery"]["phase"] == "RECOVERY_READ"


def _b3(case, entry_id, attempt=1):
    project = case.project
    at = case.expiry + timedelta(seconds=20)
    scheduled = at + timedelta(seconds=2)
    document = {"version": 1, "instance_id": project.instance_id, "interval_sha256": project.interval_sha256,
                "scheduled_for": scheduled, "deadline": scheduled + timedelta(seconds=75),
                "write_deadline": scheduled + timedelta(seconds=110), "leg": "new", "command": "settle",
                "team": _D1_TEAM, "code_commit": project.head, "input_evidence": [], "scope": {"entry_ids": [entry_id]}}
    document["config_digest"] = sha256(operations.document_bytes(
        {"team": _D1_TEAM, "operator": "unattended"})).hexdigest()
    key = operations.slot_operation_key(document)
    if attempt == 1:
        body = {**case.market, "closed": True, "closedTime": case.expiry.isoformat(), "outcomePrices": '["1","0"]'}
        url = f"{polymarket.GAMMA_MARKET_PREFIX}{case.market['slug']}"
        with pg.connect_managed(project.root) as app:
            raw = json.dumps(body, sort_keys=True).encode()
            source = snapshots.register_snapshot(app, snapshots.SnapshotRegistration(
                raw, polymarket.SOURCE_KIND_GAMMA, url, at))
            pg.record_market_outcome(app, store.MarketOutcomeObservation(
                condition_id=body["conditionId"], market_id=body["id"], market_slug=body["slug"],
                yes_token_id=case.tokens[1], no_token_id=case.tokens[0], outcome="NO", observed_at=at,
                confirmed_at=at, source_kind="gamma_close", source_ref=url, source_snapshot_sha256=source["content_sha256"],
            ))
            _d1_register_binding(
                app, document=document, kind="d1_operation_slot", source="d1:operation:slot:v1",
                command="d1-register-slot", key_for=lambda digest: f"d1:slot-binding:v1:{key.rsplit(':', 1)[1]}",
                scheduled_for=scheduled, code_commit=project.head, config_digest=document["config_digest"],
            )
    return _d1_child(project, [scheduled.isoformat(), "settle", "--managed", "--repo-root", str(project.root),
                               "--team", _D1_TEAM, "--operation-key", key, "--attempt", str(attempt)],
                     code="from tests.test_papersim_prepared_scan_native import _drive_settle; _drive_settle()")


def test_prepared_scan_recovery_uses_the_same_entry_for_b2_and_b3(d1_capture_project):
    case = _capture_setup(d1_capture_project, "fx40-committed", cash=600.0)
    crash = _b1(case, 1, fault="after-business")
    assert crash.returncode == _CRASH, crash.stderr
    with pg.connect_managed(case.project.root) as reader:
        assert [row["phase"] for row in pg.d1_operation_receipts(reader, case.key)] == ["STARTED", "PREPARED"]
        scan_id, = reader.execute("SELECT scan_id FROM papersim.scan_runs WHERE operation_key = %s",
                                  (case.key,)).fetchone()
        candidate_id, entry_id = reader.execute(
            "SELECT c.candidate_id, e.entry_id FROM papersim.candidates c "
            "JOIN papersim.entries e USING (candidate_id) WHERE c.scan_id = %s", (scan_id,),
        ).fetchone()
    resumed = _b1(case, 2, path_n=True)
    assert resumed.returncode == 0, resumed.stderr
    result = json.loads(resumed.stdout)
    assert result["recovered"] and result["scan_id"] == scan_id
    assert result["entry_ids"] == {f"{case.market['id']}|no|crypto_terminal": entry_id}
    assert result["bankroll_event_keys"] == [f"entry:{entry_id}:cash"]
    assert result["operation_receipt"]["attempt"] == 2
    duplicate = _b1(case, 3)
    assert duplicate.returncode == 0 and json.loads(duplicate.stdout) == result, duplicate.stderr
    replayed = _d1_child(case.project, ["replay", str(candidate_id), "--managed", "--repo-root",
                                        str(case.project.root), "--trusted-commit", case.project.head])
    assert replayed.returncode == 0 and json.loads(replayed.stdout)["matched"], replayed.stderr
    _b2(case, candidate_id, scan_id)
    settled = _b3(case, entry_id)
    assert settled.returncode == 0, settled.stderr
    summary = json.loads(settled.stdout)
    item, = summary["items"]
    final, = summary["settled"]
    assert item["status"] == "SETTLED" and final["entry_id"] == entry_id and summary["pending"] == []
    assert item["cash_event_id"] == final["bankroll_event_id"]
    again = _b3(case, entry_id, attempt=2)
    assert again.returncode == 0, again.stderr
    with pg.connect_managed(case.project.root) as reader:
        assert reader.execute("SELECT entry_id FROM papersim.settlements WHERE settlement_id = %s",
                              (final["settlement_id"],)).fetchone() == (entry_id,)
        assert reader.execute("SELECT reason FROM papersim.bankroll_events WHERE entry_id = %s ORDER BY event_id",
                              (entry_id,)).fetchall() == [("SCAN_ENTRY",), ("SETTLE_NATURAL",)]
