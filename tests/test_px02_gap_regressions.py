"""PX-02 gap regressions: mutations that survived the candidate test assets.

Three high-risk invariants had no rejecting counterexample inside the frozen
candidate assets (see evidence/px02-r2/coverage-map.md GAP-1/2/3):

- GAP-2 receipt binding: the driver must reject a child receipt whose echoed
  sub_seed does not match this round's derived sub_seed, even when every other
  field is valid (a swapped-input child must never be recorded as passed).
- GAP-1 observation completeness: reaching minimum_valid_rounds is not enough;
  with max_wall_seconds configured the driver keeps rounding until the wall
  budget actually elapses (71h55m must not be able to pose as 72h).
- GAP-3 cache accounting (two nets): Anthropic's cache_creation sub-object must
  reconcile with cache_creation_input_tokens, and Codex's cached tokens are a
  SUBSET of input_tokens — an inconsistent usage block is invalid either way.
- GAP-5 none-as-number: a side without an executable ask reports every cost
  field as None; a missing fee must never become a zero.
- GAP-6 rounding direction: the per-share fee upper bound rounds UP (CEILING)
  at 1e-6; the existing oracle fixtures all quantize exactly, so a FLOOR
  mutation survived until a rounding-sensitive price was added here.

Everything here is a controlled synthetic Python child or an in-memory
envelope/book fixture: no provider, network, credential, database or real
market data. Expected fee/bound values are recomputed in this file from the
raw synthetic inputs with decimal.Decimal (same first-principles discipline as
the oracle suite; the functions under test are never the expectation source).
"""
from decimal import Decimal as D

import pytest

from polymarket_alpha_lab import research_claude_exec as cli
from polymarket_alpha_lab import research_codex_exec as codex
from tests.test_research_claude_exec import MODEL, envelope, wire
from tests.test_research_codex_exec import events, output
from tests.test_research_paper_settlement_decimal_oracle import ceil6, floor6, run_scenario, synthetic_case
from tests.support import soak_driver as drv
from tests.test_soak_driver import base_config, manifest_with, round_record, run_in_process

ECHO = ('import sys,json\n'
        'p=json.loads(sys.stdin.buffer.read())\n'
        'sys.stdout.write(json.dumps({"echo_round":p["round"],"echo_seed":p["sub_seed"]}))\n')

WRONG_SEED = ('import sys,json\n'
              'p=json.loads(sys.stdin.buffer.read())\n'
              'sys.stdout.write(json.dumps({"echo_round":p["round"],"echo_seed":p["sub_seed"]+1}))\n')


def test_driver_rejects_receipt_bound_to_a_foreign_seed(tmp_path):
    """A receipt echoing another round's sub_seed is receipt_invalid, not passed."""
    manifest = manifest_with(tmp_path, [{'name': 'wrongseed', 'kind': 'process',
                                         'code': WRONG_SEED}])
    code, campaign = run_in_process(tmp_path, manifest, minimum_valid_rounds=1)
    assert code == drv.EXIT_OK
    record = round_record(campaign, 1, 1)
    assert record['final'] == 'failed' and record['reason'] == 'receipt_invalid'
    assert record['receipt'] is None
    # Legal control at the same boundary: the correctly echoing child passes.
    (tmp_path / 'control').mkdir()
    control_manifest = manifest_with(tmp_path / 'control', [{'name': 'echo', 'kind': 'process',
                                                             'code': ECHO}])
    code, control_campaign = run_in_process(
        tmp_path / 'control', control_manifest, minimum_valid_rounds=1)
    assert code == drv.EXIT_OK
    assert round_record(control_campaign, 1, 1)['final'] == 'passed'


def test_driver_completion_requires_elapsed_wall_time(tmp_path):
    """min_rounds alone never completes a max_wall_seconds campaign.

    With a 3-second wall budget and one-round minimum, the driver must still be
    rounding well past round 1 when it closes with reason 'complete'; closing at
    minimum_valid_rounds would let 71h55m pose as 72h (coverage-map GAP-1).
    """
    manifest = manifest_with(tmp_path, [{'name': 'echo', 'kind': 'process', 'code': ECHO}])
    code, campaign = run_in_process(tmp_path, manifest, minimum_valid_rounds=1,
                                    max_wall_seconds=3.0, round_period_seconds=0.2)
    assert code == drv.EXIT_OK
    close = drv._read_json(campaign / 'segments' / 'segment-000001' / 'segment-close.json')
    assert close['reason'] == 'complete'
    assert close['rounds_total']['passed'] >= 3  # kept rounding past min_rounds
    started = drv._read_json(campaign / 'segments' / 'segment-000001' / 'segment.json')['started_wall']
    assert close['stopped_wall'] - started >= 2.0  # the wall budget really elapsed


@pytest.mark.parametrize('five_m,one_h,declared,valid', [(3, 9, 7, False), (3, 4, 7, True)])
def test_claude_cache_creation_subitems_must_reconcile(five_m, one_h, declared, valid):
    """5m+1h ephemeral writes must equal cache_creation_input_tokens (GAP-3)."""
    value = envelope()
    value['usage'] = dict(input_tokens=3, output_tokens=5,
                          cache_creation_input_tokens=declared, cache_read_input_tokens=11,
                          cache_creation={'ephemeral_5m_input_tokens': five_m,
                                          'ephemeral_1h_input_tokens': one_h})
    value['modelUsage'][MODEL] = dict(inputTokens=3, outputTokens=5,
                                      cacheCreationInputTokens=declared, cacheReadInputTokens=11)
    if valid:
        reply = cli.decode_claude_result(wire(value),
                                         request=cli.ClaudeExecInput(MODEL, '[{}]', 100),
                                         call_number=1)
        assert reply.total_tokens == 3 + 5 + declared + 11
    else:
        with pytest.raises(ValueError, match='invalid'):
            cli.decode_claude_result(wire(value),
                                     request=cli.ClaudeExecInput(MODEL, '[{}]', 100),
                                     call_number=1)

@pytest.mark.parametrize('cached,valid', [(11, False), (10, True)])
def test_codex_cached_tokens_must_be_a_subset_of_input(cached, valid):
    """Codex convention: cached_input_tokens <= input_tokens (GAP-3, codex net)."""
    rows = events(input_tokens=10, cached_input_tokens=cached, cache_write_input_tokens=0,
                  output_tokens=20, reasoning_output_tokens=0)
    if valid:
        assert codex.decode_codex_exec_output(output(rows), call_number=1).total_tokens == 30
    else:
        with pytest.raises(ValueError, match='response_invalid'):
            codex.decode_codex_exec_output(output(rows), call_number=1)


def test_incomplete_side_fee_stays_none_not_zero():
    """A partially filled side has no executable price, hence no fee number (GAP-5)."""
    request, execution, scenario, _raw, _opening = synthetic_case(
        n=42, team='crypto_btc', p='0.7', yes_asks=(('0.4', '2'),))
    row = run_scenario(request, execution, scenario)
    yes_result = row['strategy']['yes_result']
    assert yes_result['executable_price'] is None  # 2 of 5 filled: never a trade
    assert yes_result['fee_cost_per_share'] is None  # missing is not zero
    assert yes_result['net_edge_per_share'] is None
    assert D(row['book_walks']['yes']['filled_size']) == 2


def test_fee_upper_bound_rounds_up_not_down():
    """assumed_fee_upper_bound is the CEILING at 1e-6, with a nonzero tail (GAP-6)."""
    request, execution, scenario, _raw, _opening = synthetic_case(
        n=41, team='crypto_btc', p='0.7',
        yes_asks=(('0.417', '10'),), yes_bids=(('0.416', '10'),))
    row = run_scenario(request, execution, scenario)
    assert row['selected_side'] == 'yes'
    rate, peak = scenario.costs.taker_fee_rate, D('0.417')
    raw_fee = rate * peak * (D(1) - peak)  # 0.02 * 0.417 * 0.583 = 0.00486222
    fee_up, fee_down = ceil6(raw_fee), floor6(raw_fee)
    assert fee_up != fee_down  # precondition: this price is rounding-sensitive
    size = scenario.requested_size
    assert D(row['assumed_totals']['assumed_fee_upper_bound']) == size * fee_up
    assert D(row['assumed_totals']['assumed_fee_upper_bound']) != size * fee_down
