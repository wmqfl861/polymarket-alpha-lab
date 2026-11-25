"""Registered synthetic originals at the managed FX-40 read boundary."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from polymarket_alpha_lab import replay
from polymarket_alpha_lab.papersim import crypto_model, prepare, prepare_inputs, scan, snapshots, soccer_model
from polymarket_alpha_lab.papersim import store, store_psycopg
from polymarket_alpha_lab.papersim.adapters import polymarket, prices, understat
from polymarket_alpha_lab.papersim.d1compare import acquire, journal, operations
from polymarket_alpha_lab.project_postgres.files import Layout

from tests.test_papersim_adapter_understat import FIXTURE as UNDERSTAT_FIXTURE
from tests.test_papersim_prepare import CUTOFF, POLICY, _crypto_inputs, _json_bytes, _public_capture


ROOT = Path(__file__).resolve().parents[1]
INSTANCE = "fx40-resolver-fixture"
RECEIPT_AT = CUTOFF + timedelta(milliseconds=100)
REGISTERED_AT = CUTOFF + timedelta(seconds=2)
CRYPTO_CLOSE = datetime(2026, 12, 31, 12, 1, tzinfo=UTC)
SOCCER_KICKOFF = CUTOFF + timedelta(hours=2)
SOCCER_EXPIRY = SOCCER_KICKOFF + timedelta(hours=2)
QUESTION = "Will Ethereum be below $2,000 on December 31, 2026?"
DESCRIPTION = ('This market will resolve to "Yes" if the Close price of the Binance ETH/USDT '
               '1-minute candle at 12:00 PM UTC on the date in the title is below $2,000. '
               'Otherwise it will resolve to "No".')


class _ManagedFixture:
    """A transaction-aware reader over fixture rows using the existing APIs."""

    def __init__(self, monkeypatch):
        self._papersim_managed_root = ROOT
        self.info = SimpleNamespace(transaction_status=SimpleNamespace(name="IDLE"))
        self.closed = self.readonly = False
        self.snapshots, self.receipts = {}, {}
        self.connection_reads = self.provider_reads = self.transactions = 0
        self._result = []
        self.instance = {"instance_id": INSTANCE, "root_sha256": Layout(ROOT).root_hash,
                         "system_identifier": "synthetic-system"}
        self.actual_identity = (*self.instance.values(), "polymarket_alpha_lab", "pal_app")
        monkeypatch.setattr(store_psycopg, "read_instance", self.read_instance)
        monkeypatch.setattr(store_psycopg, "connect_managed", self.connect)
        monkeypatch.setattr(snapshots, "read_snapshot", self.read_snapshot)
        monkeypatch.setattr(store_psycopg, "read_command_run_event", self.read_receipt)
        monkeypatch.setattr(store_psycopg, "load_all_role_events", self.roles)

    def connect(self, root):
        assert root == ROOT
        self.connection_reads += 1
        return self

    def read_instance(self, root):
        assert root == ROOT
        return dict(self.instance)

    def close(self):
        self.closed = True

    @contextmanager
    def transaction(self):
        outer = self.info.transaction_status.name == "IDLE"
        if outer:
            self.transactions += 1
            self.info.transaction_status.name = "INTRANS"
        try:
            yield
        finally:
            if outer:
                self.readonly = False
                self.info.transaction_status.name = "IDLE"

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, sql):
        assert self.info.transaction_status.name == "INTRANS"
        if sql == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY":
            self.readonly = True
        elif sql.startswith("SELECT current_setting('transaction_isolation')"):
            assert self.readonly
            self._result = [("repeatable read", "on")]
        elif sql.endswith("FROM project_private.instance WHERE singleton IS TRUE"):
            assert self.readonly
            self._result = [self.actual_identity]
        else:
            raise AssertionError(sql)
        return self

    def fetchone(self):
        return self._result[0]

    def fetchall(self):
        return self._result

    def read_snapshot(self, connection, digest):
        assert connection is self and self.readonly
        return self.snapshots.get(digest)

    def read_receipt(self, connection, key, attempt, phase):
        assert connection is self and self.readonly and (attempt, phase) == (1, "SUCCEEDED")
        return self.receipts.get(key)

    def roles(self, connection):
        assert connection is self and self.readonly
        self.provider_reads += 1
        return {}

    def register(self, raw, kind, source, at, *, version=1):
        digest = sha256(raw).hexdigest()
        self.snapshots.setdefault(digest, {
            "content_sha256": digest, "content": raw, "byte_length": len(raw), "kind": kind, "source": source,
            "fetched_at": at, "schema_version": version,
        })
        return {"sha256": digest, "byte_length": len(raw)}

    def receipt(self, command, key, at, output, *, scheduled=None, config=None, commit=None):
        self.receipts[key] = {
            "command": command, "operation_key": key, "attempt": 1, "phase": "SUCCEEDED",
            "run_event_key": store.command_run_event_key(key, 1, "SUCCEEDED"),
            "input_evidence": [], "output_evidence": output,
            "error_code": None, "queue_id": None, "scan_id": None, "scheduled_for": scheduled,
            "occurred_at": at, "config_digest": config, "code_commit": commit,
            "paper_only": True, "report_only": True, "readonly": True,
        }
        return self.receipts[key]


def _queue(name, kind):
    lineage = "club-soccer/over_under" if kind == "total" else f"crypto/{kind.removeprefix('crypto_')}"
    return {"queue": name, "lineage": lineage, "data_source_version": "fx40-fixture",
            "model_key": "soccer" if kind == "total" else "crypto",
            "market_type": kind.removeprefix("crypto_")}


def _edit_response(manifest, rows, sequence, update):
    row = manifest["requests"][sequence - 1]
    old = rows[sequence - 1]
    raw = json.loads(old.content)
    update(raw)
    content = _json_bytes(raw)
    row.update(content_sha256=sha256(content).hexdigest(), byte_length=len(content))
    if row["source_kind"] == polymarket.SOURCE_KIND_GAMMA:
        row["page"] = journal.gamma_page_index(content)
    rows[sequence - 1] = journal.capture(old.url, old.utc_fetched_at, old.request_meta, content)


def _capture(db, *, soccer=False, repeated=False, gamma_updates=None):
    if soccer:
        fields = {"question": "Will EPL FC 1 versus EPL FC 2 have more than 2.5 goals?",
                  "description": "Yes pays when the match has more than 2.5 goals in regulation.",
                  "category": "Sports", "gameStartTime": SOCCER_KICKOFF.isoformat(),
                  "endDate": SOCCER_EXPIRY.isoformat()}
    else:
        fields = {"question": QUESTION, "description": DESCRIPTION, "category": "Crypto",
                  "endDate": CRYPTO_CLOSE.isoformat()}
    fields.update(gamma_updates or {})
    manifest, entries, terms = _public_capture(soccer=soccer, repeat_gamma=repeated, gamma_fields=fields)
    rows = list(entries)
    if soccer:
        _edit_response(manifest, rows, 1, lambda page: page[0].update(title="EPL FC 1 versus EPL FC 2"))
    manifest.update(discoveries=[], unbound_market_appearances=[])
    manifest["coverage"].update(book_freshness={"status": "NOT_ASSESSABLE", "policy_ref": None,
                                                "reason_code": "d1_capture_book_age_policy_unregistered"})
    for entry in sorted(rows, key=lambda item: item.utc_fetched_at):
        db.register(entry.content, acquire._BODY_KIND, entry.url, entry.utc_fetched_at, version=2)
    scope = manifest["scope"]
    scope_sha = sha256(acquire._canonical(scope)).hexdigest()
    reference = db.register(acquire._canonical(manifest), acquire._BATCH_KIND,
                            acquire._BATCH_SOURCES[2], CUTOFF, version=2)
    receipt = db.receipt("capture-inputs", f"{acquire._OPERATION_PREFIX}:{scope_sha}", RECEIPT_AT,
                         [reference["sha256"]], scheduled=datetime.fromisoformat(scope["window_start"]),
                         config=scope_sha)
    return manifest, rows, terms, reference, receipt


def _policy(db, manifest, *, limits=POLICY):
    start = datetime.fromisoformat(manifest["scope"]["window_start"])
    end = datetime.fromisoformat(manifest["scope"]["window_end"])
    interval = {"version": 1, "instance_id": INSTANCE, "team": "fixture-team",
                "starts_at": start, "ends_at": end + timedelta(minutes=5)}
    if limits is not None:
        interval["book_age_policy"] = {"max_source_age_seconds": limits.max_source_age_seconds,
                                       "max_receipt_age_seconds": limits.max_receipt_age_seconds}
    interval_ref = db.register(operations.document_bytes(interval), operations._INTERVAL_KIND,
                               "d1:operation:interval:v1", start - timedelta(seconds=1))
    db.receipt("d1-register-interval", f"d1:interval-binding:v1:{interval_ref['sha256']}",
               start - timedelta(seconds=1), operations._refs(interval_ref["sha256"], interval_ref["byte_length"]))
    return interval_ref


def _record_model(db, sequence, source_kind, source_ref, raw, *, at=CUTOFF - timedelta(seconds=20)):
    return {"sequence": sequence, "source_kind": source_kind, "source_ref": source_ref, "status": "received",
            "received_at": at, "body": db.register(raw, prepare_inputs.MODEL_RESPONSE_KIND, source_ref, at),
            "source_observed_at": None, "source_time_status": "receipt_only", "target": {"fixture": True},
            "budget": "charged", "reason_code": None}


def _soccer_originals(db, *, future=False):
    model_requests = []
    for year in ("2026", "2025"):
        for league in understat.UNDERSTAT_LEAGUES:
            if year == "2025" and league != "EPL":
                continue
            fixture = UNDERSTAT_FIXTURE["leagues"][league]
            block = deepcopy(fixture["response"])
            if year == "2025":
                for row in block["dates"]:
                    row["datetime"] = row["datetime"].replace("2026-", "2025-", 1)
                for team in block["teams"].values():
                    for row in team["history"]:
                        row["date"] = row["date"].replace("2026-", "2025-", 1)
            if league == "EPL" and year == "2026":
                block["dates"].append({
                    "id": "fx40-ep1", "datetime": SOCCER_KICKOFF.strftime("%Y-%m-%d %H:%M:%S"),
                    "h": {"id": "1", "title": "EPL FC 1"}, "a": {"id": "2", "title": "EPL FC 2"},
                    "isResult": False,
                })
                if future:
                    completed = next(row for row in block["dates"] if row["isResult"])
                    old_date = completed["datetime"]
                    completed["datetime"] = SOCCER_KICKOFF.strftime("%Y-%m-%d %H:%M:%S")
                    for team in block["teams"].values():
                        for match in team["history"]:
                            if match["date"] == old_date:
                                match["date"] = completed["datetime"]
            raw = _json_bytes(block)
            ref = understat._league_source_reference(league, year)
            model_requests.append(_record_model(db, len(model_requests) + 1, understat.SOURCE_KIND, ref, raw))
    return model_requests


def _case(monkeypatch, *, soccer=False, repeated=False, policy=True, gamma_updates=None, future_xg=False,
          kind=None):
    db = _ManagedFixture(monkeypatch)
    manifest, journal_rows, terms, capture_ref, receipt = _capture(
        db, soccer=soccer, repeated=repeated, gamma_updates=gamma_updates,
    )
    if soccer:
        kind = "total"
        model_responses = _soccer_originals(db, future=future_xg)
        source_selector = {"provider": "understat", "league": "EPL", "season": "2026", "fixture_id": "fx40-ep1",
                           "home_team_id": "1", "away_team_id": "2", "projection_binding": None,
                           "responses": [item["sequence"] for item in model_responses]}
    else:
        kind = kind or "crypto_terminal"
        materials = _crypto_inputs()
        model_responses = [_record_model(db, index, item.source_kind, item.source_reference, item.raw_bytes)
                           for index, item in enumerate(materials.evidence, 1)]
        source_selector = {"candles": [1], "spot": 2, "window_start": materials.candles.covered_start,
                           "window_end": materials.candles.covered_end}
    gamma = json.loads(journal_rows[0].content)[0]["markets"][0]
    expiry = datetime.fromisoformat(gamma["endDate"])
    target = {
        "queue": "prepared", "condition_id": gamma["conditionId"], "market_id": gamma["id"],
        "slug": gamma["slug"], "gamma_event_id": "event-12", "tag_slug": manifest["scope"]["tag_slugs"][0],
        "terms_sha256": terms, "market_kind": kind, "buy_side": "YES" if soccer else "NO",
        "model_side": "over" if soccer else "no", "event_start_at": SOCCER_KICKOFF if soccer else expiry,
        "expires_at": expiry, "line": "2.5" if soccer else "2000",
        "sources": None if kind == "crypto_touch" else source_selector,
    }
    config = {"team": "fixture-team", "queues": [_queue(target["queue"], kind)]}
    config_bytes = _json_bytes(config)
    config_ref = db.register(config_bytes, prepare_inputs.CONFIG_KIND, prepare_inputs.CONFIG_SOURCE,
                             CUTOFF - timedelta(minutes=5))
    doc = {"version": 1, "instance_id": INSTANCE, "config": config_ref, "capture": capture_ref,
           "window_start": manifest["scope"]["window_start"], "window_end": manifest["scope"]["window_end"],
           "decision_as_of": CUTOFF, "interval": _policy(db, manifest) if policy else None,
           "queues": [target["queue"]], "model_responses": model_responses, "targets": [target],
           "coverage": {"unbound": [], "model_fetches": [], "market": manifest["coverage"]}}
    return SimpleNamespace(db=db, manifest=manifest, rows=journal_rows, receipt=receipt, doc=doc,
                           target=target, config_bytes=config_bytes, config=config)


def _seal(case):
    ref = case.db.register(operations.document_bytes(case.doc), prepare_inputs.INPUT_SET_KIND,
                           prepare_inputs.INPUT_SET_SOURCE, REGISTERED_AT)
    config_sha = case.doc["config"]["sha256"]
    scope = case.manifest["scope"]
    case.db.receipt(
        prepare_inputs.INPUT_SET_COMMAND, prepare_inputs.input_set_operation_key(config_sha, scope),
        REGISTERED_AT, operations._refs(ref["sha256"], ref["byte_length"]),
        scheduled=datetime.fromisoformat(scope["window_start"]), config=config_sha,
    )
    return ref["sha256"]


def _resolve(case, *, managed=None, config_bytes=None):
    return prepare_inputs.resolve_preparation_inputs(
        case.db if managed is None else managed, repo_root=ROOT,
        config_bytes=case.config_bytes if config_bytes is None else config_bytes,
        input_set_sha256=_seal(case),
    )


def test_registered_terminal_resolves_no_ladder_and_repeated_bytes_without_network(monkeypatch):
    case = _case(monkeypatch, repeated=True)

    def forbidden(*args, **kwargs):
        pytest.fail("registered replay attempted a public read")

    monkeypatch.setattr(polymarket.BoundedPublicOpener, "open", forbidden)
    monkeypatch.setattr(prices, "_urlopen", forbidden)
    monkeypatch.setattr(understat, "_urlopen", forbidden)
    resolved = _resolve(case)
    assert case.db.transactions == case.db.provider_reads == 1
    assert resolved.book_age_policy == POLICY
    assert resolved.policy_slot_sha256 == case.doc["interval"]["sha256"]
    assert resolved.capture_receipt["occurred_at"] == RECEIPT_AT
    assert resolved.decision_as_of == CUTOFF
    assert [source["sequence"] for source in resolved.model_source_refs["market-34|no|crypto_terminal"]] == [1, 2]
    assert resolved.entries[0].content_sha256 == resolved.entries[4].content_sha256 == resolved.entries[5].content_sha256
    assert len({resolved.entries[index].utc_fetched_at for index in (0, 4, 5)}) == 3
    pack = resolved.prepare_market_pack()
    assert pack.excluded == ()
    record, = scan.records_from_pack(pack.content)
    assert record.side == "NO" and record.no_token_id == "111" and record.yes_token_id == "222"
    request = replay.request_from_replay_bundle(record.request_bundle)
    assert request.buy_token == "111" and request.yes_when == "below"
    assert request.yes_ask == 0.36 and request.no_ask == 0.69
    sigma = crypto_model.realized_vol_daily(_crypto_inputs().candles.closes)
    expected = crypto_model.terminal_probability(2188.0, sigma, 2000.0,
                                                 (CRYPTO_CLOSE - CUTOFF).total_seconds() / 3600, asset="ETH")
    assert request.probability == expected
    assert record.variant_quotes == [1 - expected.convention_a, 1 - expected.convention_b]
    proof = json.loads(pack.content)[0]["prepared_provenance"]
    assert proof["sources"][0]["sequence"] == 5
    assert proof["sources"][0]["path"] == "/0/markets/0"


def test_registered_understat_rebuilds_frozen_soccer(monkeypatch):
    case = _case(monkeypatch, soccer=True)

    def forbidden(*args, **kwargs):
        pytest.fail("registered replay attempted an Understat read")

    monkeypatch.setattr(understat, "_urlopen", forbidden)
    resolved = _resolve(case)
    target, = resolved.targets
    assert target.materials.season == "2026" and target.plan.line == 2.5
    snapshot = target.materials.xg_snapshot
    prior_source = next(item for item in target.materials.evidence if item.source_reference.endswith("/EPL/2025"))
    assert snapshot["EPL_2025"] == json.loads(prior_source.raw_bytes)
    assert snapshot["_provenance"]["status"] == "complete"
    pack = resolved.prepare_market_pack()
    assert pack.excluded == ()
    record, = scan.records_from_pack(pack.content)
    assert len(record.variant_quotes) == 10
    quote = soccer_model.candidate_for_market(
        snapshot, "EPL FC 1", "EPL", "EPL FC 2", "EPL", "EPL FC 1 versus EPL FC 2", "market-34", 0.36,
        kind="total", side="over", line=2.5, season="2026",
    )
    assert record.variant_quotes == list(quote.candidate.qs_variants)
    assert record.median_probability == quote.candidate.model_median


def test_unregistered_policy_returns_batch_and_bought_book_gap(monkeypatch):
    case = _case(monkeypatch, policy=False)
    resolved = _resolve(case)
    assert resolved.book_age_policy is None and resolved.policy_slot_sha256 is None
    pack = resolved.prepare_market_pack()
    assert pack.content == b"[]"
    excluded, = pack.excluded
    assert excluded.reason_code == "d1_capture_book_age_policy_unregistered"
    assert excluded.source_refs[0]["batch_sha256"] == case.doc["capture"]["sha256"]
    assert any(ref.get("token_id") == "111" and ref["sequence"] == 3 for ref in excluded.source_refs)


def test_policy_receipt_must_predate_the_registered_interval(monkeypatch):
    case = _case(monkeypatch)
    interval_receipt = next(receipt for receipt in case.db.receipts.values()
                            if receipt["command"] == "d1-register-interval")
    interval_receipt["occurred_at"] = CUTOFF - timedelta(seconds=1)
    with pytest.raises(prepare.PreparationError, match="d1_capture_book_age_policy_binding_invalid"):
        _resolve(case)


def test_present_but_unregistered_policy_limits_remain_unknown(monkeypatch):
    case = _case(monkeypatch, policy=False)
    case.doc["interval"] = _policy(case.db, case.manifest, limits=None)
    resolved = _resolve(case)
    assert resolved.book_age_policy is None
    assert resolved.prepare_market_pack().excluded[0].reason_code == "d1_capture_book_age_policy_unregistered"


def test_touch_is_a_per_target_evidence_gap(monkeypatch):
    case = _case(monkeypatch, kind="crypto_touch")
    result = _resolve(case)
    assert result.targets == ()
    excluded, = result.prepare_market_pack().excluded
    assert excluded.reason_code == "papersim_prepare_crypto_touch_state_unverified"
    assert any(ref.get("path") == "/0/markets/0" for ref in excluded.source_refs)


def test_unsupported_mixed_target_keeps_ready_market(monkeypatch):
    case = _case(monkeypatch)
    case.config["queues"].append({"queue": "nba", "lineage": "nba/moneyline", "data_source_version": "fixture",
                                  "model_key": "nba", "market_type": "moneyline"})
    case.config_bytes = _json_bytes(case.config)
    case.doc["config"] = case.db.register(case.config_bytes, prepare_inputs.CONFIG_KIND,
                                          prepare_inputs.CONFIG_SOURCE, CUTOFF - timedelta(minutes=5))
    case.doc["queues"].append("nba")
    case.doc["targets"].append({
        **case.target, "queue": "nba", "market_kind": "nba_moneyline", "sources": None,
        "condition_id": "0x" + "d" * 64, "market_id": "not-in-this-batch",
    })
    resolved = _resolve(case)
    assert len(resolved.targets) == 1
    assert len(scan.records_from_pack(resolved.prepare_market_pack().content)) == 1
    skipped, = resolved.excluded
    assert skipped.queue == "nba"
    assert skipped.reason_code == "papersim_prepare_market_identity_unavailable"


@pytest.mark.parametrize(("change", "reason"), [
    ({"line": "3.5"}, "papersim_prepare_soccer_line_conflict"),
    ({"event_start_at": SOCCER_KICKOFF + timedelta(seconds=1)}, "papersim_prepare_target_clock_conflict"),
    ({"model_side": "under"}, "papersim_prepare_soccer_side_conflict"),
])
def test_soccer_declared_terms_conflict_with_raw_market(monkeypatch, change, reason):
    case = _case(monkeypatch, soccer=True)
    case.target.update(change)
    with pytest.raises(prepare.PreparationError, match=reason):
        _resolve(case)


def test_soccer_replaced_team_id_and_future_completed_row_are_detected(monkeypatch):
    case = _case(monkeypatch, soccer=True)
    case.target["sources"]["home_team_id"] = "3"
    with pytest.raises(prepare.PreparationError, match="papersim_prepare_soccer_fixture_conflict"):
        _resolve(case)

    case = _case(monkeypatch, soccer=True, future_xg=True)
    result = _resolve(case)
    skipped, = result.excluded
    assert skipped.reason_code == understat.REASON_FUTURE_COMPLETED_MATCH


def _bind_projection(case, data):
    sources = case.target["sources"]
    projection = case.db.register(
        operations.document_bytes(data), "understat_xg", prepare_inputs.SOCCER_PROJECTION_BINDING_SOURCE, CUTOFF,
    )
    binding = {
        "version": 1, "instance_id": INSTANCE, "batch_sha256": case.doc["capture"]["sha256"],
        "decision_as_of": CUTOFF,
        **{key: case.target[key] for key in ("condition_id", "market_id", "gamma_event_id", "terms_sha256",
                                             "event_start_at")},
        **{key: sources[key] for key in ("provider", "league", "season", "fixture_id", "home_team_id", "away_team_id")},
        "responses": [{"sequence": row["sequence"], "source_kind": row["source_kind"],
                       "source_ref": row["source_ref"], "received_at": row["received_at"],
                       "snapshot_sha256": row["body"]["sha256"]}
                      for row in case.doc["model_responses"]],
        "projection": projection,
    }
    sources["projection_binding"] = case.db.register(
        operations.document_bytes(binding), prepare_inputs.SOCCER_PROJECTION_BINDING_KIND,
        prepare_inputs.SOCCER_PROJECTION_BINDING_SOURCE, CUTOFF,
    )


def test_independently_registered_projection_must_match_recomputed_xg(monkeypatch):
    case = _case(monkeypatch, soccer=True)
    snapshot = _resolve(case).targets[0].materials.xg_snapshot
    _bind_projection(case, snapshot)
    assert _resolve(case).targets[0].materials.xg_snapshot == snapshot
    changed = deepcopy(snapshot)
    changed["EPL_2026"]["teams"]["1"]["history"][0]["xG"] = 12.0
    _bind_projection(case, changed)
    with pytest.raises(prepare.PreparationError, match="papersim_prepare_soccer_projection_mismatch"):
        _resolve(case)


@pytest.mark.parametrize("tamper", ["instance", "receipt", "body", "model", "config", "sequence"])
def test_input_corruption_stops_the_entire_resolution(monkeypatch, tamper):
    case = _case(monkeypatch)
    if tamper == "instance":
        case.doc["instance_id"] = "other-project"
        code = "papersim_prepare_instance_mismatch"
    elif tamper == "receipt":
        case.receipt["output_evidence"] = ["1" * 64]
        code = "d1_capture_manifest_receipt_invalid"
    elif tamper == "body":
        case.db.snapshots[case.rows[0].content_sha256]["content"] = b"[]"
        code = "d1_capture_snapshot_integrity_failed"
    elif tamper == "model":
        case.db.snapshots.pop(case.doc["model_responses"][0]["body"]["sha256"])
        code = "papersim_prepare_snapshot_missing"
    elif tamper == "config":
        case.config_bytes += b" "
        code = "papersim_prepare_config_binding_conflict"
    else:
        case.doc["model_responses"][1]["sequence"] = 1
        code = "papersim_prepare_model_request_invalid"
    with pytest.raises(prepare.PreparationError, match=code):
        _resolve(case)


def test_wrong_application_identity_refuses_before_snapshot_reads(monkeypatch):
    case = _case(monkeypatch)
    case.db.actual_identity = ("other-project", *case.db.actual_identity[1:])
    with pytest.raises(prepare.PreparationError, match="papersim_prepare_instance_mismatch"):
        _resolve(case)
    assert case.db.provider_reads == 0


def test_listing_end_after_observation_is_not_the_terminal_horizon(monkeypatch):
    case = _case(monkeypatch, gamma_updates={"endDate": (CRYPTO_CLOSE + timedelta(days=1)).isoformat()})
    resolved = _resolve(case)
    omitted, = resolved.excluded
    assert omitted.reason_code == "papersim_prepare_crypto_terminal_time_unproved"


def test_selecting_a_later_copy_of_identical_model_bytes_uses_its_own_clock(monkeypatch):
    case = _case(monkeypatch)
    spot = case.doc["model_responses"][1]
    case.doc["model_responses"].append({**spot, "sequence": 3, "received_at": CUTOFF + timedelta(seconds=1)})
    case.target["sources"]["spot"] = 3
    result = _resolve(case)
    omitted, = result.excluded
    assert omitted.reason_code == "papersim_prepare_model_evidence_after_cutoff"
    ref, = (ref for ref in omitted.source_refs if ref.get("source_kind") == spot["source_kind"])
    assert ref["sequence"] == 3 and ref["content_sha256"] == spot["body"]["sha256"]


def test_opened_or_caller_supplied_managed_connection_has_one_read_view(monkeypatch):
    case = _case(monkeypatch)
    digest = _seal(case)
    result = prepare_inputs.resolve_preparation_inputs(
        repo_root=ROOT, config_bytes=case.config_bytes, input_set_sha256=digest,
    )
    assert result.input_set_sha256 == digest
    assert case.db.transactions == case.db.connection_reads == 1
    assert case.db.closed
