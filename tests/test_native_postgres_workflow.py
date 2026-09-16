"""Native CI inventory/aggregation contracts; no new test runner or database.

The original 51-file selection is pinned to PR48 e1e1df52. Partitioning must
retain every original file exactly once. JSON is valid YAML and lets this test
read the deliberately closed matrix without adding a YAML dependency.
"""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/native-postgres.yml'
ORIGINAL_FILES = """tests/test_project_postgres.py
tests/test_project_postgres_platform.py
tests/test_project_postgres_publication.py
tests/test_project_postgres_publication_native.py
tests/test_project_postgres_migration_compat.py
tests/test_project_postgres_native.py
tests/test_project_postgres_backup.py
tests/test_project_postgres_backup_zip.py
tests/test_project_postgres_backup_native.py
tests/test_project_postgres_resolution_native.py
tests/test_research_resolution.py
tests/test_research_resolution_store.py
tests/test_research_resolution_cli.py
tests/test_research_resolution_inspection_cli.py
tests/test_research_resolution_queue.py
tests/test_research_resolution_poll.py
tests/test_project_postgres_resolution_queue_native.py
tests/test_research_crypto_launch.py
tests/test_crypto_contract_scope.py
tests/test_crypto_observation_time.py
tests/test_research_evidence_timezones.py
tests/test_research_scope_instants.py
tests/test_research_record_timezone_codec.py
tests/test_project_postgres_crypto_launch_native.py
tests/test_public_http.py
tests/test_research_crypto_discovery.py
tests/test_crypto_supported_selection.py
tests/test_handoff_download.py
tests/test_research_evaluation_cli.py
tests/test_research_execution_cli.py
tests/test_research_execution_inventory.py
tests/test_research_inventory_cli.py
tests/test_project_postgres_inventory_native.py
tests/test_research_dispatch.py
tests/test_project_postgres_dispatch_native.py
tests/test_research_dispatch_rotation.py
tests/test_project_postgres_rotation_native.py
tests/test_research_model_budget.py
tests/test_project_postgres_budget_native.py
tests/test_research_dispatch_cli.py
tests/test_research_dispatch_cli_review.py
tests/test_project_postgres_dispatch_cli_native.py
tests/test_research_resolution_confirmation.py
tests/test_research_resolution_confirmation_review.py
tests/test_project_postgres_confirmation_native.py
tests/test_ci_thread_dump.py
tests/test_ci_thread_dump_review.py
tests/test_research_resolution_output.py
tests/test_research_resolution_output_review.py
tests/test_project_postgres_backup_catalog.py
tests/test_project_postgres_backup_catalog_review.py""".splitlines()
SELF = 'tests/test_native_postgres_workflow.py'


def matrix(source):
    value, end = json.JSONDecoder().raw_decode(source.split('        include: ', 1)[1])
    assert source.split('        include: ', 1)[1][end:].lstrip().startswith('runs-on:')
    assert type(value) is list and len(value) == 3
    assert [p['partition'] for p in value] == ['storage', 'research', 'dispatch']
    for partition in value:
        assert set(partition) == {'partition', 'files'}
        assert type(partition['files']) is list and partition['files']
        assert all(type(f) is str and re.fullmatch(r'tests/test_[a-z0-9_]+[.]py', f)
                   for f in partition['files'])
    assert Counter(f for p in value for f in p['files']) == Counter([*ORIGINAL_FILES, SELF])
    return value


def test_native_partitions_preserve_exact_original_inventory():
    parts = matrix(WORKFLOW.read_text())
    assert len(ORIGINAL_FILES) == len(set(ORIGINAL_FILES)) == 51
    for part in parts:
        assert all((ROOT / path).is_file() for path in part['files'])
    # Both new unit modules and the REAL backup proof stay together on Windows.
    assert {'tests/test_project_postgres_backup_native.py',
            'tests/test_project_postgres_backup_catalog.py',
            'tests/test_project_postgres_backup_catalog_review.py'} <= set(parts[0]['files'])


def test_native_partition_limits_and_failure_policy_are_explicit():
    text = WORKFLOW.read_text()
    worker, gate = text.split('  windows-native:\n')
    assert '      fail-fast: false\n' in worker
    assert '    timeout-minutes: 20\n' in worker
    assert '    runs-on: windows-2025\n' in worker
    assert 'continue-on-error' not in text
    assert '    if: ${{ always() }}\n' in gate
    assert '    needs: [windows-native-part]\n' in gate
    assert 'NATIVE_RESULT: ${{ needs.windows-native-part.result }}' in gate
    assert '    timeout-minutes: 2\n' in gate
    assert gate.count('run: |') == 1
    assert 'test "$NATIVE_RESULT" = success' in gate


def test_original_native_options_and_cleanliness_remain_required():
    text = WORKFLOW.read_text()
    assert 'uv sync --locked --extra dev --extra postgres --python 3.12' in text
    assert '-p no:faulthandler -p tests.ci_thread_dump -q -s -o junit_family=legacy -o faulthandler_timeout=120 @testFiles --durations=20' in text
    assert "NATIVE_TEST_FILES: ${{ join(matrix.files, ' ') }}" in text
    assert "$testFiles = $env:NATIVE_TEST_FILES -split ' '" in text
    for name in ('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES',
                 'POLYMARKET_ALPHA_LAB_RUN_NATIVE_BACKUP', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD',
                 'PYTHONUTF8', 'PAL_REQUIRE_HANDOFF_SHELLS'):
        assert f"$env:{name} = '1'" in text
    assert "Remove-Item ('Env:' + $_.Name)" in text
    assert 'git diff --check\n          git diff --exit-code' in text
    assert '$code = $LASTEXITCODE\n          exit $code' in text
    assert 'permissions:\n  contents: read\n' in text
    assert 'persist-credentials: false' in text
    assert 'enable-cache: false' in text


def test_each_partition_retains_distinct_failure_artifacts():
    text = WORKFLOW.read_text()
    assert 'if: always()' in text
    assert 'name: native-postgres-proof-${{ matrix.partition }}-${{ github.sha }}' in text
    assert '${{ runner.temp }}/native-proof.log' in text
    assert '${{ runner.temp }}/native-proof.xml' in text
    assert 'if-no-files-found: error' in text
    assert "      - 'tests/test_native_postgres_workflow*.py'" in text


@pytest.mark.parametrize('result', ['success', 'failure', 'cancelled', 'skipped', ''])
def test_aggregate_shell_only_accepts_complete_success(result):
    # On POSIX execute the actual aggregate body. The hosted aggregate is Ubuntu;
    # Windows validates its exact body instead of invoking Git Bash/MSYS implicitly.
    text = WORKFLOW.read_text().split('  windows-native:\n', 1)[1]
    script = '\n'.join(line[10:] for line in text.split('        run: |\n', 1)[1].splitlines())
    assert script == 'set -euo pipefail\ntest "$NATIVE_RESULT" = success'
    if os.name != 'nt':
        done = subprocess.run(['/bin/bash', '-c', script], env={'NATIVE_RESULT': result},
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
        assert done.returncode == (0 if result == 'success' else 1)
        assert done.stdout == done.stderr == b''


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'selector', 'unknown-partition'])
def test_partition_inventory_rejects_silent_coverage_changes(change):
    text = WORKFLOW.read_text()
    if change == 'missing':
        text = text.replace('            "tests/test_project_postgres_backup_native.py",\n', '', 1)
    elif change == 'duplicate':
        text = text.replace('"tests/test_project_postgres_backup.py"', '"tests/test_project_postgres_backup_native.py"', 1)
    elif change == 'selector':
        text = text.replace('"tests/test_project_postgres_backup_native.py"', '"tests/test_project_postgres_backup_native.py::test_selected"', 1)
    else:
        text = text.replace('"partition": "storage"', '"partition": "unknown"', 1)
    with pytest.raises((AssertionError, ValueError)):
        matrix(text)
