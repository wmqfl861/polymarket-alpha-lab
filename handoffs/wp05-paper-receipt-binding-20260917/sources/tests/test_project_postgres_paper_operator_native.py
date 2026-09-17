"""Actual task CLI processes over isolated native PostgreSQL; synthetic inputs."""
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import uuid

import pytest

from polymarket_alpha_lab.project_postgres import files
from polymarket_alpha_lab.project_postgres.runtime import import_runtime_directory
from polymarket_alpha_lab.project_postgres.server import ProjectPostgres
from polymarket_alpha_lab.research_paper_capture_codec import checksum, encode_paper_scenario
from tests.test_research_paper_capture import inputs, scenario
from tests.test_project_postgres_paper_capture_native import Model

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES') == '1'


@pytest.mark.skipif(not ENABLED, reason='explicit native paper operator proof is opt-in')
def test_paper_operator_capture_replay_inspect_and_rejection(tmp_path, monkeypatch):
    prefix = Path(os.environ['POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX'])
    for key in tuple(os.environ):
        if key.upper().startswith('PG'):
            monkeypatch.delenv(key)
    parent = tmp_path
    if os.name == 'nt':
        parent = Path(os.environ['RUNNER_TEMP']) / ('pal-paper-cli-' + uuid.uuid4().hex)
        files.private_directory(parent, create=True)
    root = parent / 'Paper Operator With Spaces'
    root.mkdir()
    (root / 'pyproject.toml').write_text('[project]\nname="polymarket-alpha-lab"\n')
    shutil.copytree(ROOT / 'database', root / 'database')
    shutil.copytree(ROOT / 'supabase/migrations', root / 'supabase/migrations')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    import_runtime_directory(root, prefix)
    db = ProjectPostgres(root)

    def command(args, payload=None):
        child = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/manage_research_tasks.py'),
            '--root', str(root), *args], input=payload, cwd=parent, env=files.clean_environment(),
            capture_output=True, timeout=120)
        assert not child.stderr, child.stderr
        output = json.loads(child.stdout)
        assert b'raw_base64' not in child.stdout and b'Synthetic' not in child.stdout
        return child.returncode, output

    def capture(s, *, allow=True):
        payload = encode_paper_scenario(s)
        args = ['capture-paper', '--record-id', s.record_id, '--input-sha256', checksum(payload)]
        if allow:
            args.append('--allow-paper-write')
        return command(args, payload.encode() + b'\r\n')

    def replay_with_post_store_argument_mutation(spec):
        # Only the adapter argument is deliberately corrupted AFTER the actual
        # original database replay. The stored receipt and SQL path are real.
        payload = encode_paper_scenario(spec)
        program = (
            'import runpy, sys\n'
            'from decimal import Decimal\n'
            'from polymarket_alpha_lab.project_postgres.research import ProjectResearchSession\n'
            'from polymarket_alpha_lab.research_paper_capture_codec import checksum, encode_paper_scenario\n'
            'original = ProjectResearchSession.capture_paper_research\n'
            'calls = 0\n'
            'def capture(self, *, scenario, allow_paper_write=False):\n'
            '    global calls\n'
            '    calls += 1\n'
            '    reviewed = checksum(encode_paper_scenario(scenario))\n'
            '    receipt = original(self, scenario=scenario, allow_paper_write=allow_paper_write)\n'
            '    assert receipt.to_dict()["input_sha256"] == reviewed\n'
            '    object.__setattr__(scenario, "requested_size", Decimal("6"))\n'
            '    assert checksum(encode_paper_scenario(scenario)) != reviewed\n'
            '    return receipt\n'
            'ProjectResearchSession.capture_paper_research = capture\n'
            'sys.argv = sys.argv[1:]\n'
            'try:\n'
            '    runpy.run_path(sys.argv[0], run_name="__main__")\n'
            'finally:\n'
            '    assert calls == 1\n'
        )
        child = subprocess.run([sys.executable, '-I', '-c', program,
            str(ROOT / 'scripts/manage_research_tasks.py'), '--root', str(root),
            'capture-paper', '--record-id', spec.record_id, '--input-sha256', checksum(payload),
            '--allow-paper-write'], input=payload.encode()+b'\r\n', cwd=parent,
            env=files.clean_environment(), capture_output=True, timeout=120)
        assert child.returncode == 0 and child.stderr == b''
        return json.loads(child.stdout)

    try:
        assert db.initialize(port=port)['migrations_applied'] == 67
        with db.session() as session:
            identity = db._state()
            originals, scenarios = [], []
            for number, team in ((5100, 'crypto_btc'), (5101, 'crypto_eth')):
                req, raw = inputs(number, team, at=datetime.now(UTC))
                record = session.run_research(request=req, model_factory=lambda _: Model())
                originals.append(record)
                prepared = replace(scenario(record, raw, at=datetime.now(UTC)), max_age_seconds=300)
                if team == 'crypto_eth':
                    prepared = replace(prepared, yes_book=replace(prepared.yes_book, raw_json=b'{bad json'))
                scenarios.append(prepared)
        # Child invocations deliberately occur OUTSIDE the managed parent lease.
        code, no_permission = capture(scenarios[0], allow=False)
        assert code == 2 and no_permission['operation_entered'] is False
        code, missing = command(['inspect-paper', '--record-id', scenarios[0].record_id])
        assert code == 3 and missing['result'] is None
        code, saved = capture(scenarios[0])
        assert code == 0 and saved['result']['result']['status'] == 'paper_scenario_ready'
        assert saved['result']['paper_trades_created'] == 0
        code, replay = capture(scenarios[0])
        assert code == 0 and replay == saved
        code, conflict = capture(replace(scenarios[0], requested_size=Decimal('6')))
        assert code == 1 and conflict['business_writes_possible'] is True and conflict['result'] is None
        code, rejected = capture(scenarios[1])
        assert code == 0 and rejected['result']['result']['status'] == 'paper_scenario_rejected'
        assert rejected['result']['result']['reason_code'] == 'malformed_market_or_book'
        for spec, expected in zip(scenarios, (saved, rejected), strict=True):
            assert replay_with_post_store_argument_mutation(spec) == expected
        print('native paper approved-input binding: PASS; original BTC/ETH receipts survive adapter argument mutation, single replay')
        db.down()
        code, inspected = command(['inspect-paper', '--record-id', scenarios[0].record_id])
        assert code == 0 and inspected['result'] == saved['result']
        assert inspected['business_writes_possible'] is False
        with db.session() as session:
            for original, spec in zip(originals, scenarios, strict=True):
                assert session.inspect(record_id=spec.record_id).record == original.record
                assert session.inspect_paper_research(record_id=spec.record_id).scenario == spec
            assert db._psql(identity, 'SELECT count(*) FROM research_capture.paper_simulations;') == '2'
            assert db._psql(identity, 'SELECT count(*) FROM research_capture.attempts;') == '2'
            assert db._psql(identity, 'SELECT count(*) FROM project_private.migrations;') == '67'
        assert db.status()['instance_id'] == identity['instance_id']
        print('native paper operator: PASS; explicit capture, replay/conflict, rejection, restart/readback, unchanged originals')
    finally:
        if db.status()['status'] != 'stopped':
            db.down()
