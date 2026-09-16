"""Synthetic whole-flow recipe executed by the EXTRACTED kit's interpreter.

Not shipped application code, a client plugin or a user-data operation. All
project imports must come from the selected immutable kit, not the checkout.
"""
from contextlib import redirect_stdout
from dataclasses import asdict, replace
import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time

from polymarket_alpha_lab.cost_aware_event_strategy import (
    PaperCostAwareEventCostAssumptions as Costs, PaperCostAwareEventStrategyConfig as Gates,
)
from polymarket_alpha_lab.project_postgres import distribution, files
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab import research_dispatch_cli
from polymarket_alpha_lab.research_dispatch import ResearchBatch
from polymarket_alpha_lab.research_execution import CapturedResearchRequest
from polymarket_alpha_lab.research_model_budget import ModelCallBudget
from polymarket_alpha_lab.research_paper_capture_codec import encode_paper_scenario
from polymarket_alpha_lab.research_paper_inputs import ResearchPaperBook, ResearchPaperScenario
from polymarket_alpha_lab.research_resolution import IndependentResolutionConfirmation, ResolutionSubmission
from polymarket_alpha_lab.research_resolution_codec import encode_resolution
from polymarket_alpha_lab.research_resolution_confirmation import CryptoSettlementReview
from polymarket_alpha_lab.team_research_agent_types import ResearchEvidence, ResearchModelReply, ResearchToolCall
from polymarket_alpha_lab.team_research_intake import GammaMarketSnapshot, prepare_team_research_from_gamma

PRIVATE = 'synthetic-packaged-source-do-not-export'
BATCHES = ('kit-btc', 'kit-eth')


def prepared(at, opening, *, namespace='packaged', condition_base=8800):
    """Four distinct original requests, fixed UTC terminal-price contracts."""
    rows = []
    months = ('January', 'February', 'March', 'April', 'May', 'June',
              'July', 'August', 'September', 'October', 'November', 'December')
    for number, team in enumerate(('crypto_btc', 'crypto_eth') * 2):
        rid = namespace + '-' + str(number)
        cid = '0x' + format(condition_base + number, '064x')
        asset, ticker = ('Bitcoin', 'BTC') if team == 'crypto_btc' else ('Ethereum', 'ETH')
        title = f'Will the price of {asset} be above $2,000 on {months[opening.month-1]} {opening.day}, {opening.year}?'
        clock = f'{opening.hour % 12 or 12}:{opening.minute:02d} ' + ('AM' if opening.hour < 12 else 'PM')
        rules = (f'This market will resolve to "Yes" if the Close price of the Binance {ticker}/USDT '
                 f'1-minute candle at {clock} UTC on the date in the title is above $2,000. '
                 'Otherwise it will resolve to "No". Synthetic fixture only.')
        raw = dict(conditionId=cid, slug=rid+'-market', question=title, description=rules,
            active=True, closed=False, acceptingOrders=True, enableOrderBook=True,
            orderMinSize='1', orderPriceMinTickSize='0.001', outcomes=['Yes', 'No'],
            clobTokenIds=['101', '102'], endDate=(opening+timedelta(hours=1)).isoformat())
        evidence = ResearchEvidence('source', team, cid, 'Synthetic', PRIVATE, 'fixture:packaged', at)
        intake = prepare_team_research_from_gamma(
            GammaMarketSnapshot(raw['slug'], at, json.dumps(raw).encode()), task_id=rid,
            team_id=team, condition_id=cid, as_of=at, evidence=(evidence,))
        request = CapturedResearchRequest(rid, 'synthetic-model', 'packaged-flow-v1',
            opening-timedelta(seconds=1), intake, required_source_ids=('source',))
        rows.append((request, raw))
    return tuple(rows)


class Model:
    def __init__(self, team, *, fail=False):
        self.team, self.fail, self.calls = team, fail, 0

    def complete(self, **configuration):
        self.calls += 1
        if self.fail:
            raise RuntimeError('synthetic packaged model failure')
        if self.calls == 1:
            name, args = 'read_evidence', dict(source_id='source')
        else:
            name, args = 'finish_research', dict(
                probability_yes='0.7' if self.team == 'crypto_btc' else '0.1',
                confidence='0.9', source_ids=['source'], summary=PRIVATE)
        return ResearchModelReply((ResearchToolCall(str(self.calls), name, json.dumps(args)),), 1)


def scenario(execution, raw, at, *, rejected=False):
    delta = at - datetime(1970, 1, 1, tzinfo=UTC)
    millis = delta.days*86400000 + delta.seconds*1000 + delta.microseconds//1000
    def book(token, bid, ask):
        return ResearchPaperBook(at, json.dumps(dict(asset_id=token,
            market=execution.request.intake.condition_id, timestamp=str(millis),
            bids=[dict(price=bid, size='10')], asks=[dict(price=ask, size='10')])).encode())
    yes = book('101', '0.39', '0.4')
    if rejected:
        yes = replace(yes, raw_json=b'{synthetic malformed')
    return ResearchPaperScenario(execution.request.record_id, execution.record.content_sha256, at,
        GammaMarketSnapshot(raw['slug'], at, json.dumps(raw).encode()), yes,
        book('102', '0.59', '0.6'), D('5'), Costs(D('.02'), D('.001'), D('0'), D('0'), D('0'), D('0')),
        Gates('packaged-test', min_confidence=D('.7'), max_spread=D('.05'), max_resolution_risk=D('.2'),
              min_ask_size=D('1'), min_net_edge=D('.01')), D('.1'), 'synthetic-costs', 300)


def assert_origins(root):
    modules = [module for name, module in sys.modules.items()
               if name == 'polymarket_alpha_lab' or name.startswith('polymarket_alpha_lab.')]
    assert modules, 'no project modules loaded'
    for module in modules:
        assert Path(module.__file__).resolve().is_relative_to(root / 'src'), 'checkout code used'
    return len(modules)


# The only injected fault is the operator output stream, not its database or
# confirmation handler. A success-status witness precedes the short write; the
# parent separately checks durable readback and exact original-input replay.
_CONFIRM_SHORT_OUTPUT = r'''
import json, runpy, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
assert (root / 'src/polymarket_alpha_lab/__init__.py').is_file()
sys.path.insert(0, str(root / 'src'))
original = sys.stdout
class ShortOutput:
    def write(self, text):
        assert json.loads(text)['status'] == 'recorded_operator_confirmation'
        # Emit ten ASCII bytes regardless of the host's text newline policy.
        # The injected text sink still reports ten characters to the real CLI.
        return original.buffer.write(text[:10].encode('ascii'))
    def flush(self):
        original.flush()
sys.stdout = ShortOutput()
script = root / 'scripts/review_resolution_queue.py'
sys.argv = [str(script), '--root', str(root), '--confirm', '--allow-resolution-write']
runpy.run_path(str(script), run_name='__main__')
'''


def run_confirmation_output_failure(root, payload):
    """One actual kit command with a test-only short sink, never a retry loop.

    This validates process/output behavior only. run_packaged_flow must also
    prove prior absence, saved review/outcome and immutable explicit replay.
    """
    root = Path(root)
    result = subprocess.run([sys.executable, '-I', '-c', _CONFIRM_SHORT_OUTPUT, str(root)],
        input=payload, cwd=root.parent, env=files.clean_environment(),
        capture_output=True, timeout=60, check=False, shell=False)
    assert result.returncode == 1, 'short output was not reported as failure'
    assert result.stdout == b'{\n  "opera', 'successful confirmation output was not witnessed'
    assert result.stderr == b'', 'unexpected confirmation child stderr'
    return result


def run_packaged_flow(root):
    root = Path(root).resolve()
    manifest = distribution.verify_distribution(root)
    assert_origins(root)
    db = ProjectPostgres(root)
    assert db.status()['status'] == 'stopped'
    identity = db._state()
    models, originals, saved, specifications = [], [], [], []

    def command(script, arguments=(), payload=None, expected=0):
        # Every operator process uses this KIT's Python and absolute script path.
        result = subprocess.run([sys.executable, '-I', str(root/'scripts'/script),
            '--root', str(root), *arguments], input=payload, cwd=root.parent,
            env=files.clean_environment(), capture_output=True, timeout=60, check=False)
        assert result.returncode == expected, (script, result.returncode, result.stdout, result.stderr)
        assert result.stderr == b'', result.stderr
        assert PRIVATE.encode() not in result.stdout and b'raw_base64' not in result.stdout
        return json.loads(result.stdout)

    def capture(value, *, expected=0, allow=True):
        payload = encode_paper_scenario(value).encode()
        args = ['capture-paper', '--record-id', value.record_id, '--input-sha256', sha256(payload).hexdigest()]
        if allow:
            args.append('--allow-paper-write')
        return command('manage_research_tasks.py', args, payload, expected)

    def forbidden(_):
        raise AssertionError('replay must not construct a client')

    try:
        # Borrow an explicitly started engine across children; never hold a
        # managed lifecycle lease while asking another process to enter it.
        assert db.up()['status'] == 'running'
        now = datetime.now(UTC)
        opening = now.replace(second=0, microsecond=0) + timedelta(minutes=2)
        rows = prepared(now, opening)
        requests = tuple(row[0] for row in rows)
        policy = ModelCallBudget('kit-budget', 'synthetic', requests[0].model_id, 'USD',
            700, 100, 7, 100000, 1024, opening,
            tuple((r.record_id, r.content_sha256) for r in requests), 'a'*64, cost_bound_attested=True)
        def factory(team):
            fail = team == 'crypto_eth' and any(m.team == team for m in models)
            value = Model(team, fail=fail)
            models.append(value)
            return value
        def dispatch(turn, model_factory, expected=0):
            output = io.StringIO()
            with redirect_stdout(output):
                code = research_dispatch_cli.main(['--root', str(root), 'run-turn',
                    '--rotation-id', 'kit-rotation', '--turn-id', turn,
                    '--batch-id', BATCHES[0], '--batch-id', BATCHES[1], '--budget-id', 'kit-budget',
                    '--max-tasks', '2', '--max-workers', '1', '--allow-model-calls'],
                    default_root=root, model_factory=model_factory)
            assert code == expected, output.getvalue()
            assert PRIVATE not in output.getvalue()
            return json.loads(output.getvalue())['result']
        with db.session() as s:
            assert s.execution_inventory().to_dict()['claim_count'] == 0
            for name, batch in zip(BATCHES, (requests[::2], requests[1::2]), strict=True):
                s.enqueue_research_batch(batch=ResearchBatch(name, batch), allow_queue_write=True)
            s.create_model_budget(policy=policy, allow_budget_write=True)
        first = dispatch('one', factory)
        assert first['execution_invocations'] == 2
        assert all(a['execution']['research_status'] == 'completed' for a in first['attempts'])
        with db.session() as s:
            assert s.inspect_model_budget(budget_id='kit-budget').reserved_calls == 4
        db.down()
        assert db.status()['status'] == 'stopped'
        db.up()
        repeat = dispatch('one', forbidden)
        assert repeat['status'] == 'turn_already_reserved' and repeat['execution_invocations'] == 0
        assert len(models) == 2
        second = dispatch('two', factory, expected=1)
        assert [a['execution']['research_status'] for a in second['attempts']] == ['completed', 'failed']
        assert [m.calls for m in models] == [2, 2, 2, 1]
        with db.session() as s:
            assert s.inspect_model_budget(budget_id='kit-budget').reserved_calls == 7
            for number, (request, raw) in enumerate(rows):
                original = s.inspect(record_id=request.record_id)
                originals.append(original.record)
                specifications.append(scenario(original, raw, datetime.now(UTC), rejected=number == 2))
        denied = capture(specifications[0], allow=False, expected=2)
        assert denied['operation_entered'] is False
        assert command('manage_research_tasks.py', ['inspect-paper', '--record-id', requests[0].record_id],
                       expected=3)['result'] is None
        for value in specifications:
            saved.append(capture(value)['result'])
        assert [r['result']['status'] for r in saved] == [
            'paper_scenario_ready', 'paper_scenario_ready', 'paper_scenario_rejected', 'not_simulated']
        assert capture(specifications[0])['result'] == saved[0]
        conflict = capture(replace(specifications[0], requested_size=D('6')), expected=1)
        assert conflict['result'] is None and conflict['business_writes_possible'] is True
        with db.session() as s:
            before = s.evaluate_settled_paper_research()
            assert before['attempt_count'] == before['paper_evidence_count'] == 4
            assert before['status_counts']['outcome_pending'] == 2
            assert all(g['settled_pnl_lower_bound_sum'] is None for g in before['groups'])
        # Wait for the declared REAL minute to close. Do not patch time, extend
        # cutoffs, retry a late capture or backdate an original forecast.
        time.sleep(max(0, (opening+timedelta(minutes=1)-datetime.now(UTC)).total_seconds()) + .05)
        instructions = []
        with db.session() as s:
            for request, raw in rows[:2]:
                raw = dict(raw, closed=True, acceptingOrders=False, umaResolutionStatus='resolved',
                           outcomePrices=['1', '0'])
                now = datetime.now(UTC)
                candidate = s.record_resolution(submission=ResolutionSubmission('candidate-'+request.record_id,
                    request.intake.condition_id, GammaMarketSnapshot(raw['slug'], now, json.dumps(raw).encode()), now))
                proof = IndependentResolutionConfirmation(request.intake.condition_id, raw['slug'], True,
                    opening+timedelta(minutes=1), datetime.now(UTC), candidate.submission.snapshot.content_sha256,
                    'synthetic-reviewer', 'https://data.binance.vision/synthetic-packaged', PRIVATE,
                    independently_verified=True)
                instructions.append(CryptoSettlementReview('confirmed-'+request.record_id, request.record_id,
                    request.content_sha256, candidate.submission.review_id,
                    sha256(encode_resolution(candidate.submission).encode()).hexdigest(), proof,
                    'binance', 'BTCUSDT' if request.intake.team_id == 'crypto_btc' else 'ETHUSDT', '1m', 'close', opening))
        output_failed_reviews = []
        for instruction in instructions:
            value = asdict(instruction)
            for flag in ('paper_only', 'report_only', 'readonly'):
                value.pop(flag); value['confirmation'].pop(flag)
            for clock in ('resolved_at', 'confirmed_at'):
                value['confirmation'][clock] = value['confirmation'][clock].isoformat()
            value['source_candle_open_at'] = value['source_candle_open_at'].isoformat()
            payload = json.dumps(value).encode()
            original_receipt = None
            if not output_failed_reviews:
                # A real database COMMIT precedes the injected short output.
                # A generic pre-operation failure must not satisfy this proof.
                absent = command('inspect_project_resolution.py',
                    ['--review-id', instruction.review_id], expected=3)
                assert absent['inspection'] is None
                run_confirmation_output_failure(root, payload)
                original_receipt = command('inspect_project_resolution.py',
                    ['--review-id', instruction.review_id])['inspection']
                assert original_receipt['inspection_status'] == 'recorded_operator_confirmed'
                assert original_receipt['linked_outcome']['actual_yes'] is True
                output_failed_reviews.append(instruction.review_id)
            # Explicit SAME-input replay, not automatic recovery with a new ID.
            confirmed = command('review_resolution_queue.py', ['--confirm', '--allow-resolution-write'], payload)
            assert confirmed['result']['linked_outcome']['actual_yes'] is True
            if original_receipt is not None:
                assert confirmed['result'] == original_receipt
                assert command('inspect_project_resolution.py',
                    ['--review-id', instruction.review_id])['inspection'] == original_receipt
        with db.session() as s:
            after = s.evaluate_settled_paper_research()
            assert after['status_counts'] == dict(paper_evidence_missing=0, research_not_selected=1,
                paper_not_selected=1, outcome_pending=0, crypto_confirmation_required=0, settled_simulation=2)
            by_id = {r['record_id']: r for r in after['attempts']}
            assert D(by_id['packaged-0']['amounts']['settled_pnl_lower_bound']) == D('2.971')
            assert D(by_id['packaged-1']['amounts']['settled_pnl_lower_bound']) == D('-3.029')
            assert after['actual_account_pnl'] is None
            assert s.inspect_model_budget(budget_id='kit-budget').reserved_calls == 7
            for request, original, receipt in zip(requests, originals, saved, strict=True):
                assert s.inspect(record_id=request.record_id).record == original
                assert s.inspect_paper_research(record_id=request.record_id).to_dict() == receipt
        db.down()
        # New processes after restart see the same historical and settled views.
        for view in (before, after):
            read = command('evaluate_project_research.py', ['--settled-paper', '--include-decisions',
                           '--as-of', view['history']['generated_at']])
            assert read['evaluation'] == dict(view, decisions_included=True)
        assert command('manage_research_tasks.py', ['inspect-paper', '--record-id', requests[0].record_id])['result'] == saved[0]
        assert db.status()['status'] == 'stopped' and db.status()['instance_id'] == identity['instance_id']
        assert distribution.verify_distribution(root) == manifest
        # A real child dies AFTER its original claim and one permit commit.
        # All input stays in memory/DB; no crash journal or replacement task.
        db.up()
        now = datetime.now(UTC)
        interrupted, _ = prepared(now, now.replace(second=0, microsecond=0)+timedelta(minutes=2),
                                 namespace='interrupted', condition_base=8900)[0]
        cap = ModelCallBudget('interrupted-budget', 'synthetic', interrupted.model_id, 'USD',
            100, 100, 1, 100000, 1024, interrupted.forecast_cutoff_at,
            ((interrupted.record_id, interrupted.content_sha256),), 'b'*64, cost_bound_attested=True)
        with db.session() as s:
            s.create_model_budget(policy=cap, allow_budget_write=True)
        child_code = """
import os, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / 'src'))
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_execution import decode_execution_request
request = decode_execution_request(sys.stdin.buffer.read().decode('utf-8'), sys.argv[2])
def die(_): os._exit(86)
with ProjectPostgres(root).session() as session:
    session.run_budgeted_research(request=request, budget_id='interrupted-budget',
                                 model_factory=die, allow_model_calls=True)
raise SystemExit(99)
"""
        died = subprocess.run([sys.executable, '-I', '-c', child_code, str(root), interrupted.content_sha256],
            input=interrupted.payload.encode(), cwd=root.parent, env=files.clean_environment(),
            capture_output=True, timeout=60, check=False, shell=False)
        assert died.returncode == 86 and died.stdout == died.stderr == b''
        db.down()
        with db.session() as s:
            incomplete = s.inspect(record_id=interrupted.record_id)
            assert incomplete.status == 'incomplete' and incomplete.record is None
            assert s.inspect_model_budget(budget_id='interrupted-budget').reserved_calls == 1
            assert s.run_budgeted_research(request=interrupted, budget_id='interrupted-budget',
                model_factory=forbidden, allow_model_calls=True) == incomplete
            assert s.inspect_model_budget(budget_id='interrupted-budget').reserved_calls == 1
            assert s.inspect_model_budget(budget_id='kit-budget').reserved_calls == 7
            inventory = s.execution_inventory().to_dict()
            assert (inventory['claim_count'], inventory['captured_result_count'],
                    inventory['incomplete_claim_count']) == (5, 4, 1)
        blocked = command('evaluate_project_research.py', ['--settled-paper'], expected=1)
        assert blocked['evaluation'] is None and blocked['reason_code'] == 'research_execution_history_incomplete'
        historical = command('evaluate_project_research.py', ['--settled-paper', '--include-decisions',
                             '--as-of', after['history']['generated_at']])
        assert historical['evaluation'] == dict(after, decisions_included=True)
        assert db.status()['status'] == 'stopped' and db.status()['instance_id'] == identity['instance_id']
        assert distribution.verify_distribution(root) == manifest
        count = assert_origins(root)
        return dict(status='packaged_flow_verified', source_commit=manifest['source_commit'],
            source_tree=manifest['source_tree'], instance_id=identity['instance_id'],
            attempts=4, simulations=4, settlements=2, reserved_calls=7,
            incomplete_claims=1, interrupted_reserved_calls=1, project_modules_checked=count,
            actual_account_pnl=None, synthetic_inputs=True,
            confirmation_output_failures=len(output_failed_reviews),
            same_confirmation_replayed=True)
    finally:
        # This recipe runs only on the test's fresh second extraction.
        if db.status()['status'] != 'stopped':
            db.down()


def run_packaged_recipe(root, python, cwd):
    """Inject only this reviewed TEST recipe; never import checkout application code."""
    prefix = ('import sys\nfrom pathlib import Path\nroot=Path(sys.argv[1]).resolve()\n'
        "assert (root/'src/polymarket_alpha_lab/__init__.py').is_file(), 'kit source missing'\n"
        "sys.path.insert(0,str(root/'src'))\n")
    recipe = Path(__file__).read_text(encoding='utf-8')
    return subprocess.run([str(python), '-I', '-c', prefix + recipe +
        '\nprint(json.dumps(run_packaged_flow(root),sort_keys=True))\n', str(root)],
        cwd=cwd, env=files.clean_environment(), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding='utf-8', timeout=300, check=False, shell=False)
