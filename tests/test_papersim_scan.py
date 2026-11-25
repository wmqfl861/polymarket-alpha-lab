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


@pytest.mark.parametrize("prepared_second", [False, True], ids=["legacy", "prepared"])
def test_portfolio_correlation_applies_across_markets_in_a_prepared_scan(prepared_second):
    first = _crypto_record()
    second = _crypto_record(key="other-market", sports_materials=True)
    specs = scan.consult_provider(PROVIDER, (first, second))
    active = specs[first.lineage].active
    provenance = {"model_spec_sha256": active.spec_sha256(), "model_version": active.model_version()}

    def prepared(record, yes_token, no_token):
        request = dataclasses.replace(replay.request_from_replay_bundle(record.request_bundle),
                                      market_id=record.market_id, yes_token=yes_token,
                                      no_token=no_token, buy_token=yes_token)
        book_digest = "b" * 64
        return dataclasses.replace(
            record, yes_token_id=yes_token, no_token_id=no_token, snapshot_refs=(book_digest,),
            book_snapshot_sha256=book_digest, decision_snapshot_sha256=book_digest,
            request_bundle=replay.replay_bundle_payload(request), prepared_provenance=provenance,
        )

    first = prepared(first, "123", "456")
    second = dataclasses.replace(second, market_id="m-other-eth")
    request = dataclasses.replace(replay.request_from_replay_bundle(second.request_bundle),
                                  market_id=second.market_id)
    second = dataclasses.replace(second, request_bundle=replay.replay_bundle_payload(request))
    if prepared_second:
        second = prepared(second, "789", "101")

    result = scan.assemble_scan(
        _config(), (first, second), admissions=scan.AdmissionReport(),
        portfolio=scan.PortfolioState(bankroll=10_000.0, cash=10_000.0),
        started_at=NOW, finished_at=NOW + datetime.timedelta(minutes=5),
        code_commit="a" * 40, specs=specs, preparation={"input_set_sha256": "c" * 64},
    )
    entered, blocked = result.submission.candidates
    assert entered.event_key != blocked.event_key
    assert entered.entry is not None
    assert blocked.entry is None
    assert "held_fixture_blocked" in json.loads(blocked.rejection_reasons)


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


# ---------------------------------------------------------------------------
# MO-7 shadow parallel (plan 05 node MO-7; the five registered tests of
# the node row, the review P3 items -- no shadow params section without
# shadow roles, canonical texts only on registry-resolved lineages --
# and the offline halves of the match_winner / fit-materials matrix).
# ---------------------------------------------------------------------------

from polymarket_alpha_lab.papersim.optimize import shadow as optimize_shadow  # noqa: E402
from polymarket_alpha_lab.papersim.optimize.provider import (  # noqa: E402
    LineageSpecs,
    RegistryProvider,
)
from polymarket_alpha_lab.papersim.optimize.shadow import (  # noqa: E402
    ShadowFitMaterials,
    SnapshotShadowSource,
    StaticShadowSource,
)
from polymarket_alpha_lab.papersim.optimize.spec import ModelSpec  # noqa: E402
from hashlib import sha256  # noqa: E402


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _shadow_spec() -> ModelSpec:
    return ModelSpec(family="crypto-gbm", procedure_version="crypto-r19e1",
                     params={"vol_ew_decay": 0.99}, envelope=(),
                     data_contract="crypto-frozen-v1")


def _crypto_baseline_spec() -> ModelSpec:
    from polymarket_alpha_lab.papersim.optimize.families import crypto
    return crypto.CRYPTO_GBM.baseline_spec()


class _StubProvider:
    """One answered lineage, counting consults (the role-freeze proof)."""

    def __init__(self, mapping):
        self._mapping = dict(mapping)
        self.consults = []

    def specs_for_lineage(self, lineage):
        self.consults.append(lineage)
        if lineage in self._mapping:
            return self._mapping[lineage]
        return PROVIDER.specs_for_lineage(lineage)


def _assemble_with(records, specs, **overrides):
    admissions = overrides.pop("admissions", None) or scan.AdmissionReport()
    return scan.assemble_scan(
        overrides.pop("config", None) or _config(queues=tuple({r.queue for r in records})),
        records,
        admissions=admissions,
        portfolio=scan.PortfolioState(bankroll=10_000.0, cash=10_000.0),
        started_at=NOW,
        finished_at=NOW + datetime.timedelta(minutes=5),
        code_commit="a" * 40,
        specs=specs,
        **overrides)


def _candidate_roles():
    baseline = _crypto_baseline_spec()
    candidate = _shadow_spec()
    roles = LineageSpecs(
        active=baseline, baseline=baseline, candidates=(candidate,), guard=None,
        fallback_reason=None,
        registry=(("crypto-gbm/terminal", baseline.spec_sha256(),
                   (candidate.spec_sha256(),)),))
    return roles, candidate


def _static_source(candidate, median=0.4, variants=(0.38, 0.42)):
    materials = ShadowFitMaterials(
        family_id="crypto-gbm", spec_sha256=candidate.spec_sha256(),
        snapshot_refs=(_digest("kline-snapshot"),), as_of=NOW, history={})

    def _quote(_state, _record, _request):
        return optimize_shadow.ShadowQuote(median, variants, None)

    return StaticShadowSource({candidate.spec_sha256(): (materials, object(), _quote)})


def test_registry_provider_empty_registry_is_bit_identical_to_b1():
    records = (_crypto_record(), _crypto_record(queue="crypto/eth-stale",
                                                key="eth-2000-stale", stale_book=True),
               _construction_record())
    frozen = _assemble_with(records, scan.consult_provider(PROVIDER, records))
    registry = _assemble_with(
        records, scan.consult_provider(RegistryProvider(roles={}, specs={}), records))
    assert len(frozen.submission.candidates) == len(registry.submission.candidates) == 3
    for frozen_candidate, registry_candidate in zip(frozen.submission.candidates,
                                                    registry.submission.candidates,
                                                    strict=True):
        assert store.ScanSubmission._candidate_payload(frozen_candidate) \
            == store.ScanSubmission._candidate_payload(registry_candidate)
    assert frozen.submission.scan.params == registry.submission.scan.params
    assert frozen.submission.scan.key() == registry.submission.scan.key()
    params = json.loads(registry.submission.scan.params)
    assert "shadow" not in params
    assert registry.shadows == ()


def test_registered_candidate_shadows_without_touching_the_active_path():
    records = (_crypto_record(), _crypto_record(queue="crypto/eth-stale",
                                                key="eth-2000-stale", stale_book=True),
               _construction_record())
    roles, candidate = _candidate_roles()
    provider = _StubProvider({"crypto/terminal": roles})
    source = _static_source(candidate)
    b1 = _assemble_with(records, scan.consult_provider(PROVIDER, records))
    mo7 = _assemble_with(records, scan.consult_provider(provider, records),
                         shadow_source=source)
    assert len(b1.submission.candidates) == len(mo7.submission.candidates) == 3
    for b1_candidate, mo7_candidate in zip(b1.submission.candidates,
                                           mo7.submission.candidates, strict=True):
        assert store.ScanSubmission._candidate_payload(b1_candidate) \
            == store.ScanSubmission._candidate_payload(mo7_candidate)
    assert b1.model_versions == mo7.model_versions
    b1_entries = [c.entry for c in b1.submission.candidates]
    mo7_entries = [c.entry for c in mo7.submission.candidates]
    assert b1_entries == mo7_entries  # no entry, no cash leg from shadows
    assert len(mo7.shadows) == 2  # the construction record never shadows
    first = mo7.shadows[0]
    assert first.candidate_key == "eth-2000-term-yes"
    assert first.role_at_prediction == "CANDIDATE"
    assert first.spec_sha256 == candidate.spec_sha256()
    assert first.probability == 0.4
    assert first.variant_quotes == (0.38, 0.42)
    assert first.refusal_reason is None
    assert first.predicted_at == NOW
    assert type(first.gate_pass) is bool
    assert mo7.shadow_stats == {"fits": 1, "predicts": 2, "refusals": 0,
                                "skipped_no_source": 0, "skipped_budget": 0}
    params = json.loads(mo7.submission.scan.params)
    assert params["shadow"]["materials"]["crypto/terminal"][
        candidate.spec_sha256()]["snapshot_refs"] == [_digest("kline-snapshot")]
    assert "canonical" in params["provider"]["specs"]["crypto/terminal"]
    assert params["provider"]["specs"]["crypto/terminal"]["registry_lineages"] == [
        {"lineage": "crypto-gbm/terminal",
         "active": _crypto_baseline_spec().spec_sha256(),
         "candidates": [candidate.spec_sha256()]}]
    b1_params = json.loads(b1.submission.scan.params)
    assert "canonical" not in b1_params["provider"]["specs"]["crypto/terminal"]
    assert "shadow" not in b1_params
    # determinism: the same source reproduces the same bindings
    again = _assemble_with(records, scan.consult_provider(provider, records),
                           shadow_source=source)
    assert again.shadows == mo7.shadows


def test_roles_are_frozen_within_one_scan():
    records = (_crypto_record(), _construction_record())
    roles, _candidate = _candidate_roles()
    provider = _StubProvider({"crypto/terminal": roles})
    _assemble_with(records, scan.consult_provider(provider, records))
    assert provider.consults == ["club-soccer/over_under", "crypto/terminal"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        roles.candidates = ()  # type: ignore[misc]


def test_role_spec_pairs_deduplicates_by_spec_digest():
    spec_a = _shadow_spec()
    assert scan.role_spec_pairs(LineageSpecs(active=spec_a, baseline=spec_a)) \
        == (("active", spec_a),)
    spec_b = _shadow_spec()
    spec_b = ModelSpec(family="crypto-gbm", procedure_version="crypto-r19e1",
                       params={"vol_ew_decay": 0.98}, envelope=(),
                       data_contract="crypto-frozen-v1")
    spec_c = ModelSpec(family="crypto-gbm", procedure_version="crypto-r19e1",
                       params={"vol_mode": "ew"}, envelope=(),
                       data_contract="crypto-frozen-v1")
    pairs = scan.role_spec_pairs(LineageSpecs(active=spec_b, baseline=spec_a,
                                              candidates=(spec_c,), guard=spec_a))
    assert [role for role, _spec in pairs] == ["active", "baseline", "candidate"]


def test_shadow_budget_drops_the_shadow_never_the_active():
    records = (_crypto_record(),)
    roles, candidate = _candidate_roles()
    provider = _StubProvider({"crypto/terminal": roles})
    b1 = _assemble_with(records, scan.consult_provider(PROVIDER, records))
    mo7 = _assemble_with(records, scan.consult_provider(provider, records),
                         shadow_source=_static_source(candidate),
                         shadow_budget_seconds=-1.0)
    assert mo7.shadows == ()
    assert mo7.shadow_stats["skipped_budget"] == 1
    for b1_candidate, mo7_candidate in zip(b1.submission.candidates,
                                           mo7.submission.candidates, strict=True):
        assert store.ScanSubmission._candidate_payload(b1_candidate) \
            == store.ScanSubmission._candidate_payload(mo7_candidate)


def test_missing_fit_materials_record_refusals_not_silence():
    records = (_crypto_record(),)
    roles, _candidate = _candidate_roles()
    provider = _StubProvider({"crypto/terminal": roles})
    empty_source = SnapshotShadowSource(lambda digest: None)
    mo7 = _assemble_with(records, scan.consult_provider(provider, records),
                         shadow_source=empty_source)
    assert len(mo7.shadows) == 1
    binding = mo7.shadows[0]
    assert binding.refusal_reason == "fit_materials_missing"
    assert binding.probability is None and binding.variant_quotes is None
    assert binding.gate_pass is False and binding.gate_reason is None
    assert mo7.shadow_stats["refusals"] == 1
    params = json.loads(mo7.submission.scan.params)
    assert "shadow" not in params  # nothing fitted, no materials section


def test_absent_source_skips_the_shadow_and_records_the_count():
    records = (_crypto_record(),)
    roles, _candidate = _candidate_roles()
    provider = _StubProvider({"crypto/terminal": roles})
    mo7 = _assemble_with(records, scan.consult_provider(provider, records),
                         shadow_source=None)
    assert mo7.shadows == ()
    assert mo7.shadow_stats["skipped_no_source"] == 1
    assert "shadow" not in json.loads(mo7.submission.scan.params)


def test_frozen_match_winner_shadow_quote_uses_fixture_variants():
    snapshot = {
        "EPL_2025": {
            "dates": [{"isResult": True, "xG": {"h": 1.5, "a": 1.5}}],
            "teams": {
                "1": {"title": "Alpha", "history": [{"xG": 1.5, "xGA": 1.5}]},
                "2": {"title": "Beta", "history": [{"xG": 2.0, "xGA": 1.0}]},
            },
        },
        "EPL_2026": {
            "dates": [],
            "teams": {
                "1": {"title": "Alpha",
                      "history": [{"xG": 2.0, "xGA": 1.0}, {"xG": 1.0, "xGA": 2.0}]},
                "2": {"title": "Beta",
                      "history": [{"xG": 3.0, "xGA": 0.0}, {"xG": 1.0, "xGA": 2.0}]},
            },
        },
    }
    content = json.dumps(snapshot, separators=(",", ":")).encode("utf-8")
    digest = _digest("xg-snapshot")
    reader = {digest: {"kind": "understat_xg", "content": content}}
    request = EntryRequest(
        market_id="m-mw-alpha-beta", question="Alpha vs Beta match winner",
        fixture="ALP-BET", market_kind="match_winner", side="home",
        book=[(0.5, 100.0)], fee_category="sports",
        gamma_liquidity=6000.0, news=_news("ALP-BET"),
        bankroll=10_000.0, cash=10_000.0, reserved=0.0,
        book_time=NOW, now=NOW, max_book_age_seconds=300.0)
    record = scan.MarketRecord(
        queue="club-soccer/epl-mw", candidate_key="mw-alpha-home", team="soccer_desk",
        side="home", market_kind="match_winner", lineage="club-soccer/match_winner",
        model_name="soccer", evaluated_at=NOW, market_ref_kind="market_id",
        market_id="m-mw-alpha-beta", market_type="match_winner",
        snapshot_refs=(digest,),
        events=scan.EventKeyMaterials(market_class="sports", league="EPL",
                                      home="Alpha", away="Beta",
                                      kickoff_day="2026-11-21"),
        request_bundle=replay.replay_bundle_payload(request))
    source = SnapshotShadowSource(lambda sha: reader.get(sha))
    fitted = source.fitted_for("club-soccer/match_winner",
                               _soccer_frozen_baseline_spec(), (record,))
    assert fitted is not None
    quote = source.quote_for(fitted, record,
                             replay.request_from_replay_bundle(record.request_bundle))
    assert quote.refusal_reason is None
    assert 0.0 < quote.model_median < 1.0
    assert len(quote.qs_variants) == 10  # the ten sweep variants (r23)


def _soccer_frozen_baseline_spec():
    from polymarket_alpha_lab.papersim.optimize.families import soccer_frozen
    return soccer_frozen.SOCCER_FROZEN.baseline_spec()
