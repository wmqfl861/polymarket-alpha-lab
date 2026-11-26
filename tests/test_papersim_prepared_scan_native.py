"""Managed FX-40 capture, B1 recovery, and same-entry B2/B3 proof."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
import sys
import time
import traceback
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from polymarket_alpha_lab.papersim import capture, fotmob, prepared_scan, prepare_inputs, settle, snapshots, store
from polymarket_alpha_lab.papersim import store_psycopg as pg
from polymarket_alpha_lab.papersim.adapters import polymarket, prices, understat
from polymarket_alpha_lab.papersim.d1compare import acquire, frozenrun, journal, operations
from tests.test_papersim_adapter_polymarket import _response
from tests.test_papersim_adapter_understat import FIXTURE as UNDERSTAT_FIXTURE
from tests.test_papersim_capture_marks_native import (
    _D1_NATIVE, _D1_TEAM, _d1_capture_cli, _d1_child, _d1_json_path, _d1_register_binding,
    _d1_register_capture_slot, d1_capture_project,
)
from tests.test_papersim_fotmob import _details, _league_payload, _match
from tests.test_papersim_scan_store_native import _seed_initial_cash
from tests.test_papersim_settle_store_native import _d1_seed_settlement_entry


pytestmark = _D1_NATIVE
_CRASH = 87
_BOOK_AGE = {"max_source_age_seconds": 600, "max_receipt_age_seconds": 600}


def _print_exception_location(error):
    frames = traceback.extract_tb(error.__traceback__)
    locations = " ".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in frames[-8:])
    print(f"fixture failure {type(error).__name__} {locations}", file=sys.stderr)


def _championship_response(url, fixture):
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    if parsed.path == "/api/data/leagues":
        selected, = query["season"]
        year = selected.split("/")[0]
        kickoff = datetime.fromisoformat(fixture["fotmob_kickoff"]) - timedelta(
            days=365 * (int(fixture["season"]) - int(year)))
        played = (kickoff - timedelta(days=2)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        matches = [_match(year + "01", "1", "t1", "t2", played),
                   _match(year + "02", "2", "t1", "t2", kickoff.isoformat(), finished=False)]
        # Include the rest of the registered 24-club league in the schedule.
        for number in range(3, fotmob.FotMobLeague.CHAMPIONSHIP.team_count, 2):
            matches.append({**matches[1], "id": f"{year}{number:02}",
                            "home": {"id": f"t{number}", "name": f"Club {number}"},
                            "away": {"id": f"t{number + 1}", "name": f"Club {number + 1}"}})
        body = _league_payload(matches, selected)
    else:
        assert parsed.path == "/api/data/matchDetails"
        match_id, = query["matchId"]
        assert match_id.endswith("01")
        body = {**_details(2, 1, "1.50", "0.75"), "general": {"matchId": match_id}}
    return _response(json.dumps(body).encode(), url=url)


def _drive_capture():
    fixture = json.loads(Path(sys.argv[1]).read_bytes())

    class PublicFixture:
        def open(self, url, *, timeout_seconds):
            parsed = urlsplit(url)
            query = parse_qs(parsed.query)
            if parsed.path == "/events" and query["tag_slug"] == [fixture["tag"]]:
                body = ([{"id": "fx40-fixture-event", "title": fixture["title"], "markets": fixture["markets"]}]
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
    if fixture["tag"] == "championship":
        fotmob.urllib.request.urlopen = lambda request, timeout: _championship_response(request.full_url, fixture)

    def understat_fixture(league, season, _timeout):
        body = deepcopy(UNDERSTAT_FIXTURE["leagues"][league]["response"])
        for row in body["dates"]:
            row["datetime"] = season + row["datetime"][4:]
        for team in body["teams"].values():
            for row in team["history"]:
                row["date"] = season + row["date"][4:]
        if league == "EPL" and season == fixture["season"]:
            kickoff = datetime.fromisoformat(fixture["markets"][0]["gameStartTime"])
            body["dates"].append({
                "id": "fx40-soccer-fixture", "datetime": kickoff.strftime("%Y-%m-%d %H:%M:%S"),
                "h": {"id": "1", "title": "EPL FC 1"}, "a": {"id": "2", "title": "EPL FC 2"},
                "isResult": False,
            })
        return _response(json.dumps(body).encode(), url=f"https://{understat._league_source_reference(league, season)}")

    understat._open_league_response = understat_fixture

    @contextmanager
    def managed_fixture(repo_root, *, _read_connection=None):
        if _read_connection is not None:
            yield _read_connection
            return
        with closing(pg.connect_managed(repo_root)) as conn:
            try:
                yield conn
            except Exception as error:
                _print_exception_location(error)
                raise

    acquire._managed = managed_fixture
    script = Path(sys.argv[2])
    sys.argv = [str(script), *sys.argv[3:]]
    runpy.run_path(str(script), run_name="__main__")


def _fault():
    from polymarket_alpha_lab.papersim import cli

    boundary = sys.argv[1]
    _explain_scan_error()
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
    _explain_scan_error()
    script = Path(sys.argv[1])
    sys.argv = [str(script), *sys.argv[2:]]
    runpy.run_path(str(script), run_name="__main__")


def _explain_scan_error():
    old = operations._error_code

    def report(error, fallback):
        if fallback == "papersim_prepare_scan_failed":
            _print_exception_location(error)
        return old(error, fallback)

    operations._error_code = report


def _drive_settle():
    from polymarket_alpha_lab.papersim import cli

    scheduled = datetime.fromisoformat(sys.argv[1])
    original = pg.d1_database_time
    pg.d1_database_time = lambda conn: max(scheduled, original(conn))
    raise SystemExit(cli.main(sys.argv[2:]))


def _ref(row):
    return {key: row[source] for key, source in (("sha256", "content_sha256"), ("byte_length", "byte_length"))}


def _capture_setup(project, name, *, news=True, rules=True, cash=None, tag="crypto", unbound=False, fotmob_clock=None):
    directory = project.temporary / name
    directory.mkdir()
    soccer = tag in ("epl", "championship")
    queue = ({"queue": f"fx40-{tag}", "lineage": "club-soccer/over_under", "model_key": "soccer",
              "market_type": "over_under"} if soccer else
             {"queue": "fx40-crypto", "lineage": "crypto/terminal", "model_key": "crypto",
              "market_type": "terminal"})
    config = _d1_json_path(directory, "scan.json", {"team": _D1_TEAM, "queues": [
        {**queue, "data_source_version": "fx40-native-fixture"},
    ]})
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
    markets = [market]
    if soccer:
        news = False
        title = "Birmingham City versus Blackburn Rovers" if tag == "championship" else "EPL FC 1 versus EPL FC 2"
        kickoff = opening
        expiry = kickoff + timedelta(hours=2)
        for index, line in enumerate(("2.5", "2.0")):
            row = market if index == 0 else {**market}
            row.update(category="Sports", gameStartTime=kickoff.isoformat(), endDate=expiry.isoformat(),
                       question=f"Will {title} have more than {line} goals?",
                       description=f"Yes pays when the match has more than {line} goals in regulation.", line=line)
            if index == 1:
                row["id"] += "-integer"
                row["slug"] += "-integer"
                row["conditionId"] = "0x" + sha256(row["id"].encode()).hexdigest()
                shifted = [str(int(token) + 2) for token in tokens]
                row["clobTokenIds"] = json.dumps(shifted)
                for source, token in zip(tokens, shifted, strict=True):
                    books[token] = {**books[source], "asset_id": token, "market": row["conditionId"]}
                markets.append(row)
    else:
        title = name
    if unbound:
        markets = [{"question": question}]
    raw = _d1_json_path(directory, "capture-fixture.json", {
        "tag": tag, "title": title, "markets": markets, "books": books,
        "season": str(opening.year - (opening.month < 7)),
        "fotmob_kickoff": f"{opening.date()}T{fotmob_clock}" if fotmob_clock else None,
    })
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
            "--tag-slug", tag, "--window-start", start.isoformat(), "--window-end", end.isoformat(),
            "--prepare-config", str(config)]
    code = ("from tests.test_papersim_prepared_scan_native import _drive_capture; _drive_capture()")
    remaining = (start - datetime.now(UTC)).total_seconds()
    if remaining > 0:
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
    return _d1_child(project, [fault or "none", *args],
                     code="from tests.test_papersim_prepared_scan_native import _fault; _fault()")


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
    observation, = capture.observations_from_pack([mark])
    return observation


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


def test_prepared_scan_recovery_uses_the_same_entry_for_b2_and_b3(d1_capture_project, monkeypatch):
    from polymarket_alpha_lab.papersim.optimize.families.crypto import CRYPTO_GBM, spec_with
    from polymarket_alpha_lab.papersim.optimize.provider import RegistryProvider

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
    with pg.connect_managed(case.project.root) as writer:
        time_before_registration = pg.d1_database_time(writer)
        pg.initialize_registry(writer, families=[CRYPTO_GBM], actor="fixture",
                               code_commit=case.project.head, effective_at=time_before_registration)
        changed = spec_with(vol_ew_decay=0.99)
        pg.register_model_spec(writer, changed, source="optimizer", code_commit=case.project.head)
        pg.record_role_events(writer, store.RoleEventBatch(events=(store.RoleEventSubmission(
            lineage="crypto-gbm/terminal", spec_sha256=changed.spec_sha256(), role="CANDIDATE",
            effective_at=pg.d1_database_time(writer), reason="isolated FX-40 registry change",
            actor="auto:rules-v1", code_commit=case.project.head,
            forward_plan={"forward_metric": "log_loss"},
        ),)))
        baseline = CRYPTO_GBM.baseline_spec()
        current = RegistryProvider.from_connection(writer).specs_for_lineage("crypto/terminal")
        assert current.active == baseline and current.candidates == (changed,)
    resumed = _b1(case, 2, path_n=True)
    assert resumed.returncode == 0, resumed.stderr
    result = json.loads(resumed.stdout)
    assert result["recovered"] and result["scan_id"] == scan_id
    assert result["entry_ids"] == {f"{case.market['id']}|no|crypto_terminal": entry_id}
    assert result["shadow_rows"] == 0
    assert result["bankroll_event_keys"] == [f"entry:{entry_id}:cash"]
    assert result["operation_receipt"]["attempt"] == 2
    assert result["model_versions"] == [baseline.model_version()]
    duplicate = _b1(case, 3)
    assert duplicate.returncode == 0 and json.loads(duplicate.stdout) == result, duplicate.stderr
    changed_config = case.directory / "changed-scan.json"
    changed_config.write_bytes(case.config.read_bytes() + b"\n")
    conflicting_calls = (
        SimpleNamespace(**{**vars(case), "config": changed_config}),
        SimpleNamespace(**{**vars(case), "receipt": {
            **case.receipt, "input_set_sha256": sha256(b"another input set").hexdigest(),
        }}),
    )
    for conflicting in conflicting_calls:
        repeated = _b1(conflicting, 4)
        assert repeated.returncode == 1 and "papersim_prepare_operation_conflict" in repeated.stderr
    with pg.connect_managed(case.project.root) as reader:
        assert [row["phase"] for row in pg.d1_operation_receipts(reader, case.key)] == [
            "STARTED", "PREPARED", "STARTED", "RECOVERY_READ", "SUCCEEDED",
        ]
    fresh_case = SimpleNamespace(**{**vars(case), "key": case.key + ":fresh"})
    fresh = _b1(fresh_case, 1)
    assert fresh.returncode == 0, fresh.stderr
    next_scan = json.loads(fresh.stdout)
    assert next_scan["scan_id"] != scan_id and next_scan["model_versions"] == [baseline.model_version()]
    assert next_scan["entry_ids"] == {}
    with pg.connect_managed(case.project.root) as reader:
        reader.autocommit = True
        original = pg.read_command_run_event(reader, case.key, 1, "PREPARED")
        current = pg.read_command_run_event(reader, fresh_case.key, 1, "PREPARED")
        original_binding, _ = prepared_scan._read_bound(reader, original)
        current_binding, _ = prepared_scan._read_bound(reader, current)
        original_specs = original_binding["specs"]["crypto/terminal"]
        assert original_specs["active"] == current_binding["specs"]["crypto/terminal"]["active"]
        assert original_specs["candidates"] == []
        current_specs = prepared_scan._load_specs(current_binding["specs"])["crypto/terminal"]
        assert current_specs.candidates == (changed,)
    replayed = _d1_child(case.project, ["replay", str(candidate_id), "--managed", "--repo-root",
                                        str(case.project.root), "--trusted-commit", case.project.head])
    assert replayed.returncode == 0 and json.loads(replayed.stdout)["matched"], replayed.stderr
    observation = _b2(case, candidate_id, scan_id)
    original_receipts = pg.d1_operation_receipts
    with pg.connect_managed(case.project.root) as reader:
        reader.autocommit = True
        receipts = original_receipts(reader, case.key)
        for variant in ("missing", "cross_key", "wrong_digest"):
            altered = deepcopy(receipts)
            recovery = next(row for row in altered if row["phase"] == "RECOVERY_READ")
            if variant == "missing":
                altered.remove(recovery)
            elif variant == "cross_key":
                recovery["operation_key"] = case.key + ":other"
            else:
                recovery["output_evidence"][0] = "snapshot_sha256:" + "0" * 64
            with monkeypatch.context() as scoped:
                scoped.setattr(pg, "d1_operation_receipts", lambda conn, key: (
                    altered if key == case.key else original_receipts(conn, key)
                ))
                candidate = pg.d1_candidate_capture_target(
                    reader, scan_id=scan_id, candidate_key=observation.candidate_key)
                target, = pg.d1_settlement_targets(reader, team=_D1_TEAM, entry_ids=[entry_id])
                assert candidate["scan_receipt_witness"] is None
                assert target["candidate"]["scan_receipt_witness"] is None
                with pytest.raises(capture.CaptureError, match="d1_capture_b1_receipt_missing"):
                    capture._d1_candidate_token(candidate, observation, _D1_TEAM, {candidate_id})
                with pytest.raises(settle.SettleError, match="d1_settlement_scan_receipt_missing"):
                    settle._d1_buy_binding(reader, target["entry"], target["candidate"])
    settled = _b3(case, entry_id)
    assert settled.returncode == 0, settled.stderr
    summary = json.loads(settled.stdout)
    item, = summary["items"]
    final, = summary["settled"]
    assert item["status"] == "SETTLED" and final["entry_id"] == entry_id and summary["pending"] == []
    assert item["cash_event_id"] == final["bankroll_event_id"]
    again = _b3(case, entry_id, attempt=2)
    assert again.returncode == 0, again.stderr
    repeated, = json.loads(again.stdout)["items"]
    assert (repeated["settlement_id"], repeated["cash_event_id"]) == (
        final["settlement_id"], final["bankroll_event_id"],
    )
    with pg.connect_managed(case.project.root) as reader:
        linked = reader.execute(
            "SELECT s.entry_id, o.condition_id, o.outcome, b.event_id, b.amount, s.proceeds "
            "FROM papersim.settlements s JOIN papersim.bankroll_events b ON b.entry_id = s.entry_id "
            "AND b.reason = 'SETTLE_NATURAL' JOIN papersim.market_outcomes o "
            "ON o.outcome_id = s.source_outcome_id WHERE s.settlement_id = %s",
            (final["settlement_id"],),
        ).fetchone()
        assert linked[:4] == (entry_id, case.market["conditionId"], "NO", final["bankroll_event_id"])
        assert linked[4] == linked[5] > 0
        assert reader.execute("SELECT reason FROM papersim.bankroll_events WHERE entry_id = %s ORDER BY event_id",
                              (entry_id,)).fetchall() == [("SCAN_ENTRY",), ("SETTLE_NATURAL",)]


@pytest.fixture
def separate_project(tmp_path_factory):
    yield from d1_capture_project.__wrapped__(tmp_path_factory)


@pytest.mark.parametrize(("balance", "prior_entries", "reason"), [
    (300.0, 0, "papersim_prepare_cash_changed"),
    (1200.0, 0, "papersim_prepare_correlation_changed"),
    (3000.0, 6, "papersim_prepare_team_cap_changed"),
])
def test_prepared_scan_rejects_an_unwritten_retry_after_competing_entry(
        separate_project, balance, prior_entries, reason):
    case = _capture_setup(separate_project, reason.replace("_", "-"), cash=balance)
    for index in range(prior_entries):
        _d1_seed_settlement_entry(case.project, f"fx40-team-position-{index}", team=_D1_TEAM)
    crash = _b1(case, 1, fault="before-business")
    assert crash.returncode == _CRASH, crash.stderr
    competing = SimpleNamespace(**{**vars(case), "key": case.key + ":competing"})
    application = _b1(competing, 1)
    assert application.returncode == 0, application.stderr
    original = json.loads(application.stdout)
    assert len(original["entry_ids"]) == 1
    retry = _b1(case, 2)
    assert retry.returncode == 1 and reason in retry.stderr
    with pg.connect_managed(case.project.root) as reader:
        receipts = pg.d1_operation_receipts(reader, case.key)
        assert [(row["attempt"], row["phase"]) for row in receipts] == [
            (1, "STARTED"), (1, "PREPARED"), (2, "STARTED"), (2, "FAILED")]
        assert receipts[-1]["error_code"] == reason
        scan_rows = reader.execute("SELECT scan_id, operation_key FROM papersim.scan_runs ORDER BY scan_id").fetchall()
        cash_rows = reader.execute("SELECT event_key FROM papersim.bankroll_events WHERE reason = 'SCAN_ENTRY' "
                                   "ORDER BY event_id").fetchall()
        assert len(scan_rows) == len(cash_rows) == prior_entries + 1
        assert scan_rows[-1] == (original["scan_id"], competing.key)
        assert cash_rows[-1] == (original["bankroll_event_keys"][0],)


def test_direct_scan_and_path_n_share_one_prepared_operation(separate_project):
    case = _capture_setup(separate_project, "fx40-one-winner", cash=1200.0)
    with ThreadPoolExecutor(max_workers=2) as workers:
        one = workers.submit(_b1, case, 1)
        two = workers.submit(_b1, case, 2, path_n=True)
        results = [task.result() for task in (one, two)]
    assert all(run.returncode == 0 for run in results), [run.stderr for run in results]
    first, second = (json.loads(run.stdout) for run in results)
    assert first["scan_id"] == second["scan_id"]
    assert first["entry_ids"] == second["entry_ids"]
    assert len(first["entry_ids"]) == 1
    with pg.connect_managed(case.project.root) as reader:
        assert [row["phase"] for row in pg.d1_operation_receipts(reader, case.key)] == [
            "STARTED", "PREPARED", "SUCCEEDED"]
        assert reader.execute("SELECT count(*) FROM papersim.bankroll_events WHERE reason = 'SCAN_ENTRY'"
                              ).fetchone() == (1,)


def test_prepared_scan_resumes_unwritten_entry_after_opposite_position(separate_project):
    case = _capture_setup(separate_project, "fx40-opposite-position", cash=1200.0)
    crash = _b1(case, 1, fault="before-business")
    assert crash.returncode == _CRASH, crash.stderr
    opposite = _d1_seed_settlement_entry(case.project, "fx40-opposite-entry", side="YES", team=_D1_TEAM)
    with pg.connect_managed(case.project.root) as reader:
        assert pg.committed_prepared_scan(reader, case.key) is None
        initial_receipts = pg.d1_operation_receipts(reader, case.key)
        assert [row["phase"] for row in initial_receipts] == ["STARTED", "PREPARED"]
    resumed = _b1(case, 2, path_n=True)
    assert resumed.returncode == 0, resumed.stderr
    result = json.loads(resumed.stdout)
    entry_id, = result["entry_ids"].values()
    assert result["recovered"] and entry_id != opposite["entry_id"]
    again = _b1(case, 3)
    assert again.returncode == 0 and json.loads(again.stdout) == {**result, "inserted": False}, again.stderr
    assert result["inserted"] is True
    with pg.connect_managed(case.project.root) as reader:
        reader.autocommit = True
        assert pg.d1_operation_receipts(reader, case.key)[:2] == initial_receipts
        assert reader.execute("SELECT started_at FROM papersim.scan_runs WHERE scan_id = %s",
                              (result["scan_id"],)).fetchone() == (initial_receipts[0]["occurred_at"],)
        assert reader.execute("SELECT event_key FROM papersim.bankroll_events WHERE entry_id = %s",
                              (entry_id,)).fetchall() == [(f"entry:{entry_id}:cash",)]
        legacy, = pg.d1_settlement_targets(reader, team=_D1_TEAM, entry_ids=[opposite["entry_id"]])
        candidate = legacy["candidate"]
        assert [(item["attempt"], item["phase"]) for item in candidate["scan_receipts"]] == [
            (1, "STARTED"), (1, "SUCCEEDED"),
        ]
        observation = capture.MarkObservation(
            candidate_key=candidate["candidate_key"], scan_id=candidate["scan_id"],
            market_ref_kind="condition_id", market_ref=opposite["condition_id"],
            token_ref=opposite["yes_token_id"],
        )
        assert capture._d1_candidate_token(
            candidate, observation, _D1_TEAM, {candidate["candidate_id"]},
        ) == opposite["yes_token_id"]
        assert settle._d1_buy_binding(reader, legacy["entry"], candidate)["buy_token_id"] == opposite["yes_token_id"]


def test_unbound_capture_appearance_is_covered_without_a_candidate(separate_project):
    case = _capture_setup(separate_project, "fx40-unbound-appearance", news=False, unbound=True)
    result = _b1(case, 1)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["candidate_ids"] == {} and summary["entry_ids"] == {}
    with pg.connect_managed(case.project.root) as reader:
        reader.autocommit = True
        _, prepared, _ = pg.d1_operation_receipts(reader, case.key)
        binding, _ = prepared_scan._read_bound(reader, prepared)
        assert summary["coverage"] == binding["coverage"]
        coverage = operations._read_document(
            reader, binding["coverage"], kind=prepared_scan._COVERAGE_KIND, source=prepared_scan._COVERAGE_SOURCE,
        )
        assert coverage["input_set_sha256"] == case.receipt["input_set_sha256"]
        assert coverage["batch_sha256"] == case.receipt["batch_sha256"]
        appearance, = coverage["unbound"]
        assert (appearance["sequence"], appearance["path"]) == (1, "/0/markets/0")
        assert reader.execute("SELECT count(*) FROM papersim.candidates WHERE scan_id = %s",
                              (summary["scan_id"],)).fetchone() == (0,)


def test_unregistered_policy_records_supported_refusals_for_managed_replay(separate_project):
    case = _capture_setup(separate_project, "fx40-rule-unavailable", news=False, rules=False)
    completed = _b1(case, 1)
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["entry_ids"] == {} and summary["bankroll_event_keys"] == []
    with pg.connect_managed(case.project.root) as reader:
        rows = reader.execute(
            "SELECT c.candidate_id, c.conclusion, c.rejection_reasons "
            "FROM papersim.candidates c WHERE c.scan_id = %s ORDER BY c.candidate_id",
            (summary["scan_id"],),
        ).fetchall()
    assert len(rows) == 2
    for candidate_id, conclusion, reasons in rows:
        assert conclusion == "CONSTRUCTION_FAILED" and reasons == ["d1_capture_book_age_policy_unregistered"]
        replayed = _d1_child(case.project, ["replay", str(candidate_id), "--managed", "--repo-root",
                                            str(case.project.root), "--trusted-commit", case.project.head])
        assert replayed.returncode == 0 and json.loads(replayed.stdout)["matched"], replayed.stderr


def test_prepared_soccer_persists_ordered_quotes_and_replays_constructor_failures(separate_project):
    from polymarket_alpha_lab import replay
    from polymarket_alpha_lab.papersim import gates, soccer_model

    case = _capture_setup(separate_project, "fx40-soccer", tag="epl")
    completed = _b1(case, 1)
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert len(summary["candidate_ids"]) == 4
    with pg.connect_managed(case.project.root) as reader:
        reader.autocommit = True
        quotes = reader.execute(
            "SELECT candidate_id, candidate_key, conclusion, variant_quotes, input_payload, "
            "rejection_reasons, event_start_at FROM papersim.candidates WHERE scan_id = %s ORDER BY candidate_key",
            (summary["scan_id"],),
        ).fetchall()
        resolved = prepare_inputs.resolve_preparation_inputs(
            reader, repo_root=case.project.root, config_bytes=case.config.read_bytes(),
            input_set_sha256=summary["input_set_sha256"],
        )
    targets = {target.candidate_key: target for target in resolved.targets}
    for identifier, key, conclusion, variants, payload, reasons, kickoff in quotes:
        assert kickoff == datetime.fromisoformat(case.market["gameStartTime"])
        if "-integer|" in key:
            assert conclusion == "CONSTRUCTION_FAILED" and reasons == ["construction_value_error"]
            assert variants == []
        else:
            target = targets[key]
            request = replay.request_from_replay_bundle(payload)
            materials = target.materials
            reference = soccer_model.candidate_for_market(
                materials.xg_snapshot, target.plan.home, materials.home_league,
                target.plan.away, materials.away_league, target.plan.fixture,
                target.plan.market_id, gates.walk_book(request.book).vwap,
                kind="total", side=request.side, line=2.5, season=materials.season,
            )
            assert len(variants) == 10 and variants == list(reference.candidate.qs_variants)
        replayed = _d1_child(case.project, ["replay", str(identifier), "--managed", "--repo-root",
                                            str(case.project.root), "--trusted-commit", case.project.head])
        assert replayed.returncode == 0 and json.loads(replayed.stdout)["matched"], replayed.stderr


@pytest.mark.parametrize(("fixture_clock", "matches_kickoff"), [
    ("14:00:00+02:00", True), ("12:00:00+02:00", False),
])
def test_prepared_fotmob_capture_binds_the_kickoff_instant(separate_project, fixture_clock, matches_kickoff):
    case = _capture_setup(separate_project, "fx44-fotmob-offset", tag="championship", fotmob_clock=fixture_clock)
    completed = _b1(case, 1)
    assert completed.returncode == 0, completed.stderr
    assert len(json.loads(completed.stdout)["candidate_ids"]) == 4
    with pg.connect_managed(case.project.root) as reader:
        reader.autocommit = True
        digest = case.receipt["input_set_sha256"]
        captured = json.loads(snapshots.read_snapshot(reader, digest)["content"])
        resolved = prepare_inputs.resolve_preparation_inputs(
            reader, repo_root=case.project.root, config_bytes=case.config.read_bytes(), input_set_sha256=digest)
    assert len(captured["targets"]) == 4
    if matches_kickoff:
        assert all(item["sources"]["fixture_id"] == item["sources"]["season"] + "02" for item in captured["targets"])
        assert len(resolved.targets) == 4 and not resolved.excluded
        assert all(target.plan.home == "Birmingham City" and target.plan.away == "Blackburn Rovers"
                   for target in resolved.targets)
    else:
        assert all(item["sources"] is None for item in captured["targets"])
        assert not resolved.targets and len(resolved.excluded) == 4
        assert {target.reason_code for target in resolved.excluded} == {"papersim_prepare_model_materials_missing"}
