"""Closed codec with actual Agent-produced reports and synthetic evidence."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json

import pytest

from polymarket_alpha_lab.research_capture_codec import (
    decode_research_capture, encode_research_capture, payload_sha256,
)
from polymarket_alpha_lab.team_research_agent_types import ResearchEvidence, ResearchModelReply, ResearchToolCall
from polymarket_alpha_lab.team_research_intake import GammaMarketSnapshot
from polymarket_alpha_lab.team_research_market_pipeline import run_team_research_from_market_snapshot
from polymarket_alpha_lab.team_taxonomy import TEAM_IDS

NOW = datetime(2026, 9, 12, tzinfo=UTC)


def make_run(condition="fixture", task="task-1", team="crypto_eth", status="completed", now=NOW):
    class Model:
        calls = 0
        def complete(self, **kwargs):
            if status == "failed":
                raise RuntimeError("SYNTHETIC-PRIVATE-ERROR")
            self.calls += 1
            if self.calls == 1:
                tool, args = "read_evidence", {"source_id": "s"}
            else:
                tool, args = "finish_research", dict(probability_yes="0.7", confidence="0.2",
                    summary="SYNTHETIC-PRIVATE-SUMMARY", source_ids=["s"])
            return ResearchModelReply((ResearchToolCall(f"c{self.calls}", tool, json.dumps(args)),), 2)
    snapshot = GammaMarketSnapshot("market-" + condition, now, json.dumps(dict(
        slug="market-" + condition, conditionId=condition, active=True, closed=False,
        question="Synthetic?", description="Synthetic rules.", outcomes=["Yes", "No"],
        # endDate must be a pure function of the snapshot instant: naive inputs
        # keep their offset-less text, aware inputs are normalized to UTC first
        # so wall-clock +1 day can never cross a DST transition and re-resolve
        # the offset (zone-attached and fixed-offset runs then encode identical
        # bytes; see the round-182/625 producer-oracle regression test).
        endDate=((now if now.tzinfo is None else now.astimezone(UTC))
                 + timedelta(days=1)).isoformat(),
    )).encode())
    evidence = () if status == "intake_blocked" else (ResearchEvidence("s", team, condition,
        "Synthetic source", "SYNTHETIC-PRIVATE-EVIDENCE", "synthetic:source", now),)
    return run_team_research_from_market_snapshot(snapshot, task_id=task, team_id=team,
        condition_id=condition, as_of=now, evidence=evidence, model_factory=lambda _: Model())


def encode(run=None, **changes):
    args = dict(record_id="r1", model_id="synthetic-model", protocol_version="v1", run=run or make_run())
    args.update(changes)
    return encode_research_capture(**args)


def decode(payload, **changes):
    args = dict(recorded_at=NOW + timedelta(seconds=1), expected_sha256=payload_sha256(payload))
    args.update(changes)
    return decode_research_capture(payload, **args)


@pytest.mark.parametrize("team", TEAM_IDS)
@pytest.mark.parametrize("status", ("completed", "failed", "intake_blocked"))
def test_all_team_status_round_trips(team, status):
    run = make_run(team=team, status=status)
    payload = encode(run)
    record = decode(payload)
    assert record.run == run and record.run is not run
    assert record.recorded_at == NOW + timedelta(seconds=1)
    assert record.paper_only is record.report_only is record.readonly is True
    if status == "completed":
        assert type(record.run.research.probability_yes) is Decimal
        assert record.run.intake.task.evidence[0] is not run.intake.task.evidence[0]
    assert encode(record.run) == payload
    assert "recorded_at" not in json.loads(payload)
    assert "SYNTHETIC-PRIVATE" not in repr(record)


@pytest.mark.parametrize("location", ("envelope", "run", "intake", "task", "evidence", "receipt", "result"))
@pytest.mark.parametrize("extra", (True, False))
def test_unknown_and_missing_fields_are_rejected(location, extra):
    value = json.loads(encode())
    targets = dict(envelope=value, run=value["run"], intake=value["run"]["intake"],
                   task=value["run"]["intake"]["task"], evidence=value["run"]["intake"]["task"]["evidence"][0],
                   receipt=value["run"]["intake"]["source_receipts"][0], result=value["run"]["research"])
    target = targets[location]
    if extra:
        target["unrecognized"] = True
    else:
        del target[next(iter(target))]
    with pytest.raises(ValueError, match="invalid stored"):
        decode(json.dumps(value))


@pytest.mark.parametrize("defect", ("version", "flag", "probability_float", "nan", "wrong_receipt", "missing_source", "scope", "naive", "duplicate_json", "oversize", "malformed"))
def test_untrusted_payload_fails_closed(defect):
    value = json.loads(encode())
    if defect == "version": value["schema_version"] = "unrecognized"
    elif defect == "flag": value["run"]["readonly"] = 1
    elif defect == "probability_float": value["run"]["research"]["probability_yes"] = 0.7
    elif defect == "nan": value["run"]["research"]["probability_yes"] = "NaN"
    elif defect == "wrong_receipt": value["run"]["intake"]["source_receipts"][0]["content_sha256"] = "0"*64
    elif defect == "missing_source": value["run"]["research"]["source_ids"] = ["invented"]
    elif defect == "scope": value["run"]["research"]["condition_id"] = "other"
    elif defect == "naive": value["run"]["intake"]["as_of"] = "2026-09-12T00:00:00"
    payload = json.dumps(value)
    if defect == "duplicate_json": payload = '{"x":1,"x":2}'
    elif defect == "oversize": payload = " "*2097153
    elif defect == "malformed": payload = "{"
    with pytest.raises(ValueError, match="invalid stored"):
        decode(payload)


def test_hash_and_canonical_text_are_checked():
    payload = encode()
    with pytest.raises(ValueError): decode(payload, expected_sha256="0"*64)
    with pytest.raises(ValueError): decode(" " + payload)
    with pytest.raises(ValueError): decode(payload, recorded_at=NOW-timedelta(seconds=1))


@pytest.mark.parametrize("changes", ({"record_id":"bad id"}, {"model_id":""}, {"protocol_version":" "}, {"run":object()}))
def test_bad_capture_input_is_generic(changes):
    with pytest.raises(ValueError, match="^invalid research capture input$"):
        encode(**changes)


def test_tampered_input_flags_do_not_serialize():
    run = make_run()
    object.__setattr__(run.research, "paper_only", False)
    with pytest.raises(ValueError): encode(run)
