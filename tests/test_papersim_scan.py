"""B1 run-scan offline orchestration proofs (plan 05 section 2 / 80.8).

Everything here runs without a database and without a network: the
market source is the prepared pack (the adapter stand-in), the provider
is the frozen-baseline implementation, and the pause metrics are
injected ``evaluation.EvaluationCandidate`` rows.  The native lane
(tests/test_papersim_scan_store_native.py) proves the transactional
writes on a disposable cluster.

The central case is the registered bit-identity proof: the provider-
wired assembly and the unwired reference assembly go through the SAME
``replay.bind_*`` calls and differ ONLY in where ``model_version``
comes from, and their candidate/entry payloads must be equal.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_alpha_lab import replay
from polymarket_alpha_lab.papersim import evaluation, pipeline, queue_policy, scan, store
from polymarket_alpha_lab.papersim.crypto_model import TerminalProbability
from polymarket_alpha_lab.papersim.gates import NewsCheck
from polymarket_alpha_lab.papersim.optimize.provider import FrozenBaselineProvider
from polymarket_alpha_lab.papersim.pipeline import EntryRequest
from polymarket_alpha_lab.replay import CONSTRUCTOR_SOCCER

NOW = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.UTC)
EXPIRY = datetime.datetime(2026, 12, 31, 0, 0, tzinfo=datetime.UTC)
PROVIDER = FrozenBaselineProvider()


def _news(fixture: str) -> NewsCheck:
    return NewsCheck(fixture=fixture, delta="48h", concordant=True,
                     blockers=(), sources=(f"operator-attestation:{fixture}",))


def _eth_request() -> EntryRequest:
    """The frozen accepted ETH-2000 crypto_terminal request (pipeline suite)."""
    return EntryRequest(
        market_id="m-eth2000-term",
        question="Ethereum below $2,000 on date",
        fixture="ETH-2000",
        market_kind="crypto_terminal",
        side="yes",
        book=[(0.15, 5000.0)],
        fee_category="crypto",
        probability=TerminalProbability(
            asset="ETH", barrier=2000.0, horizon_hours=1595.12,
            convention_a=0.2224, convention_b=0.2531,
        ),
        yes_when="below",
        buy_side="yes",
        yes_ask=0.15,
        no_ask=0.80,
        gamma_liquidity=6000.0,
        news=_news("ETH-2000"),
        bankroll=10_000.0,
        cash=10_000.0,
        reserved=0.0,
        book_time=NOW,
        now=NOW,
        max_book_age_seconds=300.0,
    )


def _crypto_record(*, queue="crypto/eth-terminal", key="eth-2000-term-yes",
                   evaluated_at=NOW, sports_materials=False, stale_book=False) -> scan.MarketRecord:
    """One prepared crypto market record (adapter stand-in pack entry)."""
    request = _eth_request()
    if stale_book:
        stale = NOW - datetime.timedelta(seconds=3600)
        request = dataclasses.replace(request, book_time=stale)
    events = (
        scan.EventKeyMaterials(
            market_class="sports",
            league="epl",
            home="Brentford FC",
            away="Nottingham Forest FC",
            kickoff_day="2026-11-21",
            event_start_at=NOW + datetime.timedelta(hours=28),
        )
        if sports_materials else
        scan.EventKeyMaterials(
            market_class="crypto",
            asset="ETH",
            expiry_at=EXPIRY,
            event_start_at=EXPIRY,
        )
    )
    return scan.MarketRecord(
        queue=queue,
        candidate_key=key,
        team="crypto-desk",
        side="YES",
        market_kind="terminal",
        lineage="crypto/terminal",
        model_name="crypto",
        evaluated_at=evaluated_at,
        market_ref_kind="market_id",
        market_id="m-eth2000-term",
        median_probability=0.2224,
        events=events,
        request_bundle=replay.replay_bundle_payload(request),
    )


def _construction_record(*, queue="club-soccer/over_under", key="k-construction-none"):
    return scan.MarketRecord(
        queue=queue,
        candidate_key=key,
        team="soccer_uefa",
        side="YES",
        market_kind="other",
        lineage="club-soccer/over_under",
        model_name="soccer",
        evaluated_at=NOW,
        market_ref_kind="market_id",
        market_id="m-hof-lyo",
        construction={
            "constructor": CONSTRUCTOR_SOCCER,
            "inputs": {
                "xg_snapshot": {},
                "home_team": "TSG 1899 Hoffenheim",
                "home_league": "bundesliga",
                "away_team": "Olympique Lyonnais",
                "away_league": "ligue_1",
                "market": "HOF vs LYO",
                "market_id": "m-hof-lyo",
                "entry_ask_vwap250": 0.52,
                "kind": "total",
                "side": "over",
                "line": 2.5,
            },
        },
    )


def _config(queues=("crypto/eth-terminal",)) -> scan.ScanConfig:
    return scan.ScanConfig(
        team="pal-b1",
        operator="unattended",
        queues=tuple(
            scan.QueueScanConfig(
                queue=queue,
                lineage="crypto/terminal" if queue.startswith("crypto") else "club-soccer/over_under",
                league_scope=queue_policy.SCOPE_DOMESTIC,
                data_source_version="gamma+prices-v1",
                model_key="crypto",
                market_type="terminal",
                pack="",
            )
            for queue in queues
        ),
    )


def _assemble(records, *, specs="provider", admissions=None, config=None):
    admissions = admissions or scan.AdmissionReport()
    return scan.assemble_scan(
        config or _config(queues=tuple({r.queue for r in records})),
        records,
        admissions=admissions,
        portfolio=scan.PortfolioState(bankroll=10_000.0, cash=10_000.0),
        started_at=NOW,
        finished_at=NOW + datetime.timedelta(minutes=5),
        code_commit="a" * 40,
        specs=(scan.consult_provider(PROVIDER, records) if specs == "provider" else specs),
    )


# ---------------------------------------------------------------------------
# The registered bit-identity proof (05 section 2 B1 row)
# ---------------------------------------------------------------------------

def test_provider_wired_assembly_is_bit_identical_to_the_unwired_reference():
    records = (_crypto_record(), _crypto_record(queue="crypto/eth-stale",
                                                key="eth-2000-stale", stale_book=True),
               _construction_record())
    wired = _assemble(records, specs="provider")
    reference = _assemble(records, specs=None)

    assert wired.model_versions == reference.model_versions
    assert set(wired.model_versions) == {pipeline.MODEL_VERSION_CRYPTO}
    assert len(wired.submission.candidates) == len(reference.submission.candidates) == 3
    for wired_candidate, reference_candidate in zip(wired.submission.candidates,
                                                    reference.submission.candidates,
                                                    strict=True):
        wired_payload = store.ScanSubmission._candidate_payload(wired_candidate)
        reference_payload = store.ScanSubmission._candidate_payload(reference_candidate)
        assert wired_payload == reference_payload
        assert (json.dumps(wired_payload, sort_keys=True, separators=(",", ":"))
                == json.dumps(reference_payload, sort_keys=True, separators=(",", ":")))
    # The wired path still enters through the same frozen gate: the
    # accepted record keeps its entry and the stale book is rejected
    # with the pipeline's own reason alphabet.  The second (same-side)
    # record additionally collects the frozen held-fixture correlation
    # refusal -- the in-scan portfolio sequencing doing its job AFTER
    # the first entry, identical on both paths.
    conclusions = [candidate.conclusion for candidate in wired.submission.candidates]
    assert conclusions == ["ACCEPTED", "REJECTED", "CONSTRUCTION_FAILED"]
    assert wired.submission.candidates[0].entry is not None
    stale_reasons = json.loads(wired.submission.candidates[1].rejection_reasons)
    assert "stale_book" in stale_reasons
    assert "held_fixture_blocked" in stale_reasons
    # The scan PARAMETERS differ by design: 04 section 10 step 1
    # requires the fallback registration there (and nowhere else).
    wired_params = json.loads(wired.submission.scan.params)
    reference_params = json.loads(reference.submission.scan.params)
    assert "provider" in wired_params and "provider" not in reference_params
    assert wired.submission.scan.key() != reference.submission.scan.key()


def test_provider_params_are_canonical_and_time_free():
    records = (_crypto_record(), _construction_record())
    specs = scan.consult_provider(PROVIDER, records)
    first = scan.provider_params(specs)
    second = scan.provider_params(scan.consult_provider(PROVIDER, records))
    assert first == second
    text_one = json.dumps(first, sort_keys=True, separators=(",", ":"))
    text_two = json.dumps(second, sort_keys=True, separators=(",", ":"))
    assert text_one == text_two
    assert "fallback_reason" in first["provider"]["specs"]["crypto/terminal"]
    assert first["provider"]["specs"]["crypto/terminal"]["fallback_reason"] == "registry_empty"


# ---------------------------------------------------------------------------
# FX-17 admission wiring
# ---------------------------------------------------------------------------

def _settled_row(index: int, *, net: float = -70.0, model_key: str = "crypto",
                 market_type: str = "terminal") -> evaluation.EvaluationCandidate:
    return evaluation.EvaluationCandidate(
        candidate_id=f"c{index}",
        model_key=model_key,
        market_type=market_type,
        gate_version=pipeline.GATE_VERSION,
        market_key=f"m{index}",
        accepted=True,
        stake=50.0,
        fill_price=0.5,
        side="YES",
        fee_category="crypto",
        outcome="loss",
        recorded_pnl=net,
        decision_time=NOW - datetime.timedelta(days=2),
        settled_time=NOW - datetime.timedelta(days=1),
    )


def test_pause_admissions_trip_on_cross_league_losses():
    config = _config(("crypto/eth-terminal",))
    report = scan.pause_admissions(
        [_settled_row(i) for i in range(10)],
        config.queues,
        {"crypto/eth-terminal": (False, False)},
    )
    # league_scope is DOMESTIC in _config, so the FX-17 branch is
    # suppressed and the queue continues.
    assert report.paused_queues == frozenset()
    cross = scan.ScanConfig(
        team="pal-b1",
        queues=(scan.QueueScanConfig(
            queue="crypto/eth-terminal", lineage="crypto/terminal",
            league_scope=queue_policy.SCOPE_CROSS_LEAGUE,
            data_source_version="gamma+prices-v1", model_key="crypto",
            market_type="terminal"),),
    )
    report = scan.pause_admissions([_settled_row(i) for i in range(10)],
                                   cross.queues, {})
    assert "crypto/eth-terminal" in report.paused_queues
    assert report.escalations and report.escalations[0].review_reasons


def test_pause_admissions_unknown_metrics_never_pause_and_latches_survive():
    config = _config(("crypto/eth-terminal",))
    report = scan.pause_admissions((), config.queues, {})
    assert report.paused_queues == frozenset() and report.escalations == ()
    latched = scan.pause_admissions((), config.queues,
                                    {"crypto/eth-terminal": (True, False)})
    assert "crypto/eth-terminal" in latched.paused_queues


def test_assembled_scan_suppresses_entries_but_not_candidates_on_paused_queues():
    records = (_crypto_record(),)
    admissions = scan.AdmissionReport(
        decisions=(),
        paused_queues=frozenset({"crypto/eth-terminal"}),
        escalations=(),
    )
    assembly = _assemble(records, admissions=admissions)
    candidate = assembly.submission.candidates[0]
    assert candidate.conclusion == "ACCEPTED"
    assert candidate.entry is None
    assert assembly.suppressed_entries == ("eth-2000-term-yes",)
    assert json.loads(assembly.submission.scan.params)["admission"] == {
        "paused_queues": ["crypto/eth-terminal"]}


# ---------------------------------------------------------------------------
# FX-26 event-key binding (D8)
# ---------------------------------------------------------------------------

def test_crypto_records_bind_asset_plus_expiry_event_key():
    assembly = _assemble((_crypto_record(),))
    candidate = assembly.submission.candidates[0]
    assert candidate.event_key == f"crypto:ETH|{EXPIRY.date().isoformat()}"
    assert candidate.event_start_at == EXPIRY


def test_sports_records_bind_the_canonical_match_fallback():
    assembly = _assemble((_crypto_record(sports_materials=True),))
    candidate = assembly.submission.candidates[0]
    assert candidate.event_key == ("match:epl|Brentford FC|Nottingham Forest FC|2026-11-21")


def test_missing_materials_leave_the_event_key_null():
    record = _crypto_record()
    incomplete = dataclasses.replace(
        record, events=dataclasses.replace(record.events, asset=None))
    assembly = _assemble((incomplete,))
    assert assembly.submission.candidates[0].event_key is None
    assert assembly.submission.candidates[0].event_start_at == EXPIRY
    assembly = _assemble((dataclasses.replace(record, events=None),))
    assert assembly.submission.candidates[0].event_key is None
    assert assembly.submission.candidates[0].event_start_at is None


# ---------------------------------------------------------------------------
# Packs, construction failures, portfolio sequencing
# ---------------------------------------------------------------------------

def test_records_from_pack_round_trips_the_prepared_market():
    record = _crypto_record()
    document = {
        "queue": record.queue, "candidate_key": record.candidate_key,
        "team": record.team, "side": record.side, "market_kind": record.market_kind,
        "lineage": record.lineage, "model_name": record.model_name,
        "evaluated_at": record.evaluated_at.isoformat(),
        "market_ref_kind": record.market_ref_kind, "market_id": record.market_id,
        "median_probability": 0.2224,
        "events": {"market_class": "crypto", "asset": "ETH",
                   "expiry_at": EXPIRY.isoformat(), "event_start_at": EXPIRY.isoformat()},
        "request_bundle": record.request_bundle,
    }
    parsed = scan.records_from_pack(json.dumps([document]))
    assert parsed == (record,)


def test_pack_validation_rejects_missing_and_conflicting_sources():
    with pytest.raises(scan.ScanError):
        scan.records_from_pack([{"queue": "q", "candidate_key": "k"}])
    record = _crypto_record()
    document = {"queue": "q", "candidate_key": "k", "team": "t", "side": "YES",
                "market_kind": "terminal", "lineage": "crypto/terminal",
                "model_name": "crypto", "evaluated_at": NOW.isoformat(),
                "request_bundle": record.request_bundle, "construction": {}}
    with pytest.raises(scan.ScanError):
        scan.records_from_pack([document])


def test_construction_failure_records_the_observed_refusal():
    assembly = _assemble((_construction_record(),))
    candidate = assembly.submission.candidates[0]
    assert candidate.conclusion == "CONSTRUCTION_FAILED"
    assert json.loads(candidate.rejection_reasons) == [replay.CONSTRUCTION_RETURNED_NONE]


def test_portfolio_state_advances_within_one_scan():
    rich = scan.PortfolioState(bankroll=100.0, cash=100.0, team_committed={"a": 10.0})
    spent = rich.with_entry(team="a", side="Over/Yes(side0)", stake=25.0, fee=1.0)
    assert spent.cash == 74.0
    assert spent.team_committed["a"] == 35.0
    assert spent.held_sides == ("Over/Yes(side0)",)


# ---------------------------------------------------------------------------
# FX-03 clean-worktree guard
# ---------------------------------------------------------------------------

def _git_repo(path: Path) -> Path:
    path.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "T"]):
        subprocess.run(["git", "-C", str(path), *args], check=True,
                       capture_output=True, text=True)
    (path / "seed.txt").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "seed.txt"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "seed"], check=True,
                   capture_output=True, text=True)
    return path


def test_require_clean_worktree_returns_head_on_a_clean_repo(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    head = scan.require_clean_worktree(repo)
    assert len(head) == 40


def test_require_clean_worktree_refuses_any_dirty_state(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    (repo / "untracked.txt").write_text("dirt", encoding="utf-8")
    with pytest.raises(scan.ScanError) as untracked:
        scan.require_clean_worktree(repo)
    assert untracked.value.code == "papersim_scan_worktree_dirty"
    (repo / "untracked.txt").unlink()
    (repo / "seed.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(scan.ScanError) as modified:
        scan.require_clean_worktree(repo)
    assert modified.value.code == "papersim_scan_worktree_dirty"


def test_run_scan_refuses_a_dirty_worktree_before_touching_the_connection(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    (repo / "dirt.txt").write_text("dirt", encoding="utf-8")
    with pytest.raises(scan.ScanError) as error:
        scan.run_scan(_config(), (), conn=None, repo_root=repo, provider=PROVIDER,
                      now_utc=lambda: NOW)
    assert error.value.code == "papersim_scan_worktree_dirty"


# ---------------------------------------------------------------------------
# Module hygiene
# ---------------------------------------------------------------------------

def test_scan_module_never_imports_the_cli(tmp_path):
    script = (
        "import sys\n"
        "from polymarket_alpha_lab.papersim import scan\n"
        "assert 'polymarket_alpha_lab.papersim.cli' not in sys.modules, 'scan pulled cli'\n"
        "assert 'polymarket_alpha_lab.papersim.store_psycopg' not in sys.modules,"
        " 'assemble path pulled the driver'\n"
        "print('ok')\n"
    )
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True,
                               text=True, check=True)
    assert completed.stdout.strip().endswith("ok")
