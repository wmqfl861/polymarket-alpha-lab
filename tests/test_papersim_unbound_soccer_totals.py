"""Preserve source evidence for observed regulation totals that cannot bind to a position."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from polymarket_alpha_lab.papersim import prepare_capture, queue_policy, scan
from polymarket_alpha_lab.papersim.d1compare import acquire


TOTALS = scan.QueueScanConfig(
    queue="test", lineage="club-soccer/total", market_type="over_under",
    league_scope=queue_policy.SCOPE_DOMESTIC, data_source_version="test",
)
EVENT = {"title": "Home vs. Away - More Markets", "tags": [{"slug": "bundesliga"}]}
MARKET = {
    "sportsMarketType": "totals", "outcomes": '["Over","Under"]',
    "line": 2.5, "question": "Home vs. Away: O/U 2.5",
    "description": ("If the fixture is cancelled, this market will resolve 50-50. "
                    "This market refers only to the outcome within the first 90 minutes of regular play plus stoppage time."),
}


def coverage(*, event=None, market=None, tag="bundesliga", queue=TOTALS, status="unresolved_outcomes"):
    body = {**MARKET, **(market or {})}
    public_event = {**EVENT, **(event or {}), "markets": [body]}
    entry = SimpleNamespace(content_sha256="sample", content=acquire._canonical([public_event]))
    manifest = {
        "requests": [{"sequence": 1, "page": {}, "content_sha256": entry.content_sha256,
                      "request_meta": {"query": {"tag_slug": tag}}}],
        "markets": [{"condition_id": "sample-condition", "binding_status": status,
                     "observations": [{"sequence": 1, "path": "/0/markets/0"}]}],
    }
    targets, unbound = prepare_capture._targets(SimpleNamespace(queues=[queue]), manifest, [entry])
    assert targets == {}
    observation, = unbound
    assert observation["reason_code"] == status
    return observation


@pytest.mark.parametrize("outcomes", ['["Over","Under"]', ["Under", "Over"]])
def test_unbound_domestic_total_retains_its_source_terms_and_tag(outcomes):
    row = coverage(market={"outcomes": outcomes}, tag="soccer")
    assert row["source_market"] == {
        "league": "Bundesliga", "line": "2.5", "market_kind": "total",
        "outcomes": ["Over", "Under"] if isinstance(outcomes, str) else outcomes,
        "period": "regulation_plus_stoppage", "split_payout_mentioned": True,
    }
    assert (row["sequence"], row["path"]) == (1, "/0/markets/0")


@pytest.mark.parametrize("overrides", [
    {"event": {"tags": []}}, {"event": {"tags": [{"slug": []}]}},
    {"event": {"tags": [{"slug": "bundesliga"}, {"slug": "champions-league"}]}},
    {"event": {"tags": [{"slug": "bundesliga"}, {"slug": "epl"}]}}, {"tag": "epl"},
    {"event": {"title": "Away vs. Home - More Markets"}},
    {"market": {"sportsMarketType": "total_corners"}}, {"market": {"sportsMarketType": "first_half_totals"}},
    {"market": {"sportsMarketType": "soccer_team_totals"}}, {"market": {"line": 1.5}},
    {"market": {"description": "Two teams will play a game."}}, {"market": {"outcomes": ["A", "B"]}},
    {"queue": replace(TOTALS, league_scope=queue_policy.SCOPE_CROSS_LEAGUE)},
    {"queue": replace(TOTALS, market_type="btts")}, {"status": "identity_drift"},
])
def test_partial_or_out_of_scope_observations_keep_the_original_refusal(overrides):
    assert "source_market" not in coverage(**overrides)
