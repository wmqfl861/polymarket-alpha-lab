"""Native CI inventory/aggregation contracts; no new test runner or database.

The original 51-file selection is pinned to PR48 e1e1df52. Partitioning must
retain every original file exactly once. JSON is valid YAML and lets this test
read the deliberately closed matrix without adding a YAML dependency.

Section 61 (2026-09-27) makes Linux the sole V1 platform: the real Linux
matrix job repeats the identical closed inventory under the pinned PG18 tuple
while the Windows matrix job stays byte-identical as frozen history until the
coordinator's controlled required-context replacement removes it. Both
matrices are pinned here so neither can silently drift.

FX-07 (2026-11-12) makes the real-Postgres matrix actually run: a push-to-main
trigger with database-facing path filters, a weekly (never daily) schedule,
and a fourth Linux-only ``papersim`` partition whose opt-in DB tests receive a
one-shot loopback cluster assembled from the same pinned PG18 prefix.
"""
from collections import Counter
import json
import os
from pathlib import Path
import re
import shlex
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
# PR47 adds two explicit modules; preserve ORIGINAL_FILES as the pinned baseline.
ADMISSION_FILES = (
    'tests/test_research_dispatch_cli_admission.py',
    'tests/test_research_dispatch_cli_admission_review.py',
)


# Explicit WP02 additions; every original selector remains unchanged.
UNCAPPED_FILES = ('tests/test_research_uncapped.py', 'tests/test_research_codex_exec.py', 'tests/test_research_codex_uncapped_review.py', 'tests/test_project_postgres_uncapped_native.py')
AUDIT_FILES = ('tests/test_research_uncapped_audit.py',
               'tests/test_research_uncapped_audit_review.py',
               'tests/test_project_postgres_uncapped_audit_native.py')

PROCESS_FILES = ('tests/test_research_process.py', 'tests/test_research_process_review.py',
                 'tests/test_research_codex_process.py')

PROFILE_FILES = ('tests/test_research_codex_profile.py',
                 'tests/test_research_codex_profile_review.py')

CLAUDE_FILES = ('tests/test_research_claude_exec.py', 'tests/test_research_claude_profile.py', 'tests/test_research_claude_review.py')

CLAUDE_PROBE_FILES = ('tests/test_claude_cli_probe.py', 'tests/test_claude_cli_probe_review.py')

# FX-07 (2026-11-12): the live Linux matrix gains a fourth DB-facing
# partition; the retired Windows matrix stays frozen without it. The push
# paths below mirror the fix-list exactly (audit handoff 03-fix-list.md).
# Final CLI node (2026-11-15): test_papersim_cli.py joins the papersim
# native partition (the evaluate/replay subcommands have opt-in real-
# cluster assertions); the precise one-line matrix append is pinned here.
# M1 final review P2-03 (2026-11-15): the two FX-12 native proof files
# join the same partition (register/verify+append-only guard and the
# full record/replay round trip incl. tampered-snapshot rejection) so
# the four native proofs actually execute on the Linux matrix. Include the
# existing M2 migration, operation, and queue-policy integrations here too.
PAPERSIM_FILES = ('tests/test_papersim_ledger.py', 'tests/test_papersim_perf_view_fix.py',
                   'tests/test_papersim_store.py', 'tests/test_papersim_store_native.py',
                   'tests/test_papersim_migrations.py', 'tests/test_papersim_import_legacy.py',
                   'tests/test_papersim_cli.py', 'tests/test_papersim_snapshots.py',
                   'tests/test_papersim_replay.py', 'tests/test_papersim_m2_migrations.py',
                   'tests/test_papersim_m2_store_native.py',
                   'tests/test_papersim_queue_policy_native.py')
WINDOWS_PARTITIONS = ('storage', 'research', 'dispatch')
LINUX_PARTITIONS = ('storage', 'research', 'dispatch', 'papersim')
PUSH_PATHS = (
    'supabase/migrations/**',
    'database/**',
    'src/polymarket_alpha_lab/project_postgres/**',
    'src/polymarket_alpha_lab/*_psycopg.py',
    'src/polymarket_alpha_lab/papersim/**',
    'tests/test_project_postgres*',
    'tests/test_papersim_*',
    '.github/workflows/native-postgres.yml',
)

EXPECTED_INVENTORY = Counter([*ORIGINAL_FILES, SELF, *ADMISSION_FILES, *UNCAPPED_FILES,
                              *AUDIT_FILES, *PROCESS_FILES, *PROFILE_FILES, *CLAUDE_FILES,
                              *CLAUDE_PROBE_FILES])


def include_block(job):
    """The single closed matrix include of one job, followed by runs-on."""
    _, separator, rest = job.partition('        include: ')
    assert separator
    value, end = json.JSONDecoder().raw_decode(rest)
    assert rest[end:].lstrip().startswith('runs-on:')
    return value


def validated(block, partitions):
    assert type(block) is list and len(block) == len(partitions)
    assert [p['partition'] for p in block] == list(partitions)
    for partition in block:
        assert set(partition) == {'partition', 'files'}
        assert type(partition['files']) is list and partition['files']
        assert all(type(f) is str and re.fullmatch(r'tests/test_[a-z0-9_]+[.]py', f)
                   for f in partition['files'])
    inventory = Counter(EXPECTED_INVENTORY)
    if 'papersim' in partitions:
        inventory.update(PAPERSIM_FILES)
    assert Counter(f for p in block for f in p['files']) == inventory
    return block


def jobs(text):
    """Split the workflow's jobs section into per-job chunks keyed by job id.

    Two-space comment lines belong to the section or the following job, never
    to the tail of the previous job; deeper-indented comments stay inside
    their job. Keys outside `jobs:` (for example `on.pull_request`) are
    ignored.
    """
    chunks: dict[str, list[str]] = {}
    current: list[str] | None = None
    in_jobs = False
    for line in text.splitlines(keepends=True):
        if re.match(r'^jobs:\s*$', line):
            in_jobs, current = True, None
            continue
        if not in_jobs:
            continue
        matched = re.match(r'^  ([A-Za-z0-9_-]+):\s*$', line)
        if matched:
            current = chunks.setdefault(matched.group(1), [])
        elif re.match(r'^  #', line):
            current = None
        elif current is not None:
            current.append(line)
    return {key: ''.join(value) for key, value in chunks.items()}


def test_native_partitions_preserve_exact_original_inventory():
    chunks = jobs(WORKFLOW.read_text())
    windows = validated(include_block(chunks['windows-native-part']), WINDOWS_PARTITIONS)
    linux = validated(include_block(chunks['linux-native-part']), LINUX_PARTITIONS)
    # The frozen Windows matrix and the live Linux matrix must each be
    # complete and closed: no platform may drop or duplicate scenario
    # coverage. Only the Linux matrix carries the FX-07 papersim partition.
    assert len(ORIGINAL_FILES) == len(set(ORIGINAL_FILES)) == 51
    assert [p['files'] for p in windows] == [p['files'] for p in linux[:3]]
    assert linux[3]['partition'] == 'papersim'
    assert linux[3]['files'] == list(PAPERSIM_FILES)
    dispatch = linux[2]['files']
    assert set(ADMISSION_FILES) <= set(dispatch)
    assert set(UNCAPPED_FILES) <= set(dispatch)
    assert set(AUDIT_FILES) <= set(dispatch)
    assert set(PROCESS_FILES) <= set(dispatch)
    assert set(PROFILE_FILES) <= set(dispatch)
    assert set(CLAUDE_FILES) <= set(dispatch)
    assert set(CLAUDE_PROBE_FILES) <= set(dispatch)
    for part in [*windows, *linux]:
        assert all((ROOT / path).is_file() for path in part['files'])
        # Both new unit modules and the REAL backup proof stay together on the
        # storage partition of every platform matrix.
        if part['partition'] == 'storage':
            assert {'tests/test_project_postgres_backup_native.py',
                    'tests/test_project_postgres_backup_catalog.py',
                    'tests/test_project_postgres_backup_catalog_review.py'} <= set(part['files'])


def test_native_partition_limits_and_failure_policy_are_explicit():
    text = WORKFLOW.read_text()
    worker, gate = jobs(text)['windows-native-part'], jobs(text)['windows-native']
    assert '      fail-fast: false\n' in worker
    assert '    timeout-minutes: 20\n' in worker
    assert '    runs-on: windows-2025\n' in worker
    assert 'continue-on-error' not in text
    # Section 61 controlled CI transition (2026-09-28): the Windows matrix and
    # its summary gate are retired as visible frozen history; a skipped input
    # would fail an always() gate, so both carry if: false.
    assert "if: false  # retired 2026-09-28 per DELIVERY_PLAN.md section 61" in worker
    assert gate.startswith(
        '    if: false  # retired 2026-09-28 with windows-native-part')
    assert '    needs: [windows-native-part]\n' in gate
    assert 'NATIVE_RESULT: ${{ needs.windows-native-part.result }}' in gate
    assert '    timeout-minutes: 2\n' in gate
    assert gate.count('run: |') == 1
    assert 'test "$NATIVE_RESULT" = success' in gate


def test_linux_partitions_and_pg18_prefix_are_explicit():
    text = WORKFLOW.read_text()
    worker, gate = jobs(text)['linux-native-part'], jobs(text)['linux-native']
    assert '      fail-fast: false\n' in worker
    assert '    timeout-minutes: 40\n' in worker
    assert '    runs-on: ubuntu-24.04\n' in worker
    assert 'continue-on-error' not in text
    # The qualified package-to-prefix assembly: pinned apt PostgreSQL 18 only,
    # binaries-only, no symlinks, notices preserved, no installed data used.
    assert 'noble-pgdg main' in worker and 'postgresql-18' in worker
    assert '/usr/lib/postgresql/18/bin "$PREFIX/bin"' in worker
    assert '/usr/share/postgresql/18 "$PREFIX/share"' in worker
    assert 'copyright "$PREFIX/COPYRIGHT"' in worker
    assert 'find "$PREFIX" -type l | wc -l' in worker and '-eq 0' in worker
    assert 'POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX="$RUNNER_TEMP/pg18-portable-prefix"' in worker
    assert '    if: ${{ always() }}\n' in gate
    assert '    needs: [linux-native-part]\n' in gate
    assert 'NATIVE_RESULT: ${{ needs.linux-native-part.result }}' in gate
    assert '    timeout-minutes: 2\n' in gate
    assert gate.count('run: |') == 1
    assert 'test "$NATIVE_RESULT" = success' in gate


def linux_key_download_script(workflow, job_id):
    worker = jobs(workflow.read_text())[job_id]
    step = worker.split('      - name: Assemble the qualified PG18 portable prefix\n', 1)[1]
    step = step.split('\n      - name:', 1)[0]
    body = step.split('        run: |\n', 1)[1]
    script = '\n'.join(line[10:] for line in body.splitlines())
    download, separator, _ = script.partition('\nPREFIX=')
    assert separator
    return download


KEY_DOWNLOAD_ARGS = [
    '--fail', '--show-error', '--no-progress-meter',
    '--retry', '3', '--retry-all-errors', '--connect-timeout', '15',
    '--max-time', '60', '--retry-max-time', '180',
    '-o', '/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc',
    'https://www.postgresql.org/media/keys/ACCC4CF8.asc',
]


KEY_DOWNLOAD_WORKFLOWS = (
    (WORKFLOW, 'linux-native-part'),
    (ROOT / '.github/workflows/native-distribution.yml', 'linux-kit'),
)


@pytest.mark.parametrize('workflow,job_id', KEY_DOWNLOAD_WORKFLOWS, ids=('postgres', 'distribution'))
def test_linux_signing_key_download_is_bounded_and_preserves_trust_order(workflow, job_id):
    # curl owns the retries (at most the first attempt plus three retries).
    # retry-max-time bounds retry starts, not the total wall-clock duration.
    lines = linux_key_download_script(workflow, job_id).replace('\\\n', '').splitlines()
    assert lines[:2] == [
        'set -euo pipefail',
        'sudo install -d /usr/share/postgresql-common/pgdg',
    ]
    assert shlex.split(lines[2]) == ['sudo', 'curl', *KEY_DOWNLOAD_ARGS]
    assert lines[3:] == [
        'echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] '
        'https://apt.postgresql.org/pub/repos/apt noble-pgdg main" '
        '| sudo tee /etc/apt/sources.list.d/pgdg.list >/dev/null',
        'sudo apt-get update -qq',
        'sudo apt-get install -y -qq postgresql-18',
    ]


@pytest.mark.parametrize('workflow,job_id', KEY_DOWNLOAD_WORKFLOWS, ids=('postgres', 'distribution'))
@pytest.mark.parametrize('curl_exit', [0, 56, 22, 60])
def test_linux_signing_key_download_shell_preserves_curl_result(
        tmp_path, workflow, job_id, curl_exit):
    # Exercise the actual download/APT segment, never curl, sudo, or APT itself.
    # The stub emits a completed curl result, NOT a simulation of retry logic.
    # This proves argv, diagnostics and fail-closed shell wiring, not networking.
    stubs = r"""
curl() {
    printf '%s\0' "$@" > "$CURL_ARGV"
    printf 'curl: (%s) injected initial failure\n' "$FIRST_ERROR" >&2
    printf 'Warning: injected retry diagnostic\n' >&2
    return "$CURL_EXIT"
}
sudo() {
    local command="$1"
    shift
    case "$command" in
        install) ;;
        curl) curl "$@" ;;
        tee)
            while IFS= read -r line; do :; done
            printf 'KEY_SOURCE_REGISTERED\n' >&2
            ;;
        apt-get) printf 'apt-get %s\n' "$*" ;;
        *) printf 'Unexpected privileged command\n' >&2; return 99 ;;
    esac
}
"""
    argv_file = tmp_path / 'curl.argv'
    done = subprocess.run(
        ['/bin/bash', '--noprofile', '--norc', '-c',
         stubs + linux_key_download_script(workflow, job_id) + '\nprintf "ASSEMBLY_CONTINUES\n"'],
        # No executable search path or inherited shell/auth environment. All
        # permitted operations above are builtins; the real key path is unused.
        env={'PATH': str(tmp_path), 'CURL_ARGV': str(argv_file),
             'CURL_EXIT': str(curl_exit), 'FIRST_ERROR': str(curl_exit or 56)},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
    )
    assert argv_file.read_bytes().decode().split('\0') == [*KEY_DOWNLOAD_ARGS, '']
    assert done.returncode == curl_exit
    diagnostics = (f'curl: ({curl_exit or 56}) injected initial failure\n'
                   'Warning: injected retry diagnostic\n')
    if curl_exit:
        assert done.stdout == ''  # No APT call or continuation sentinel.
        assert done.stderr == diagnostics  # No key registration either.
    else:
        assert done.stdout == ('apt-get update -qq\n'
                               'apt-get install -y -qq postgresql-18\n'
                               'ASSEMBLY_CONTINUES\n')
        assert done.stderr == diagnostics + 'KEY_SOURCE_REGISTERED\n'


# The pre-remediation download command, pinned verbatim from the parent of
# the retry remediation (line 430 of that revision) for the before/after
# recovery comparison in the test below.
ORIGINAL_KEY_DOWNLOAD_COMMAND = (
    'sudo curl --fail --silent --show-error'
    ' -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc'
    ' https://www.postgresql.org/media/keys/ACCC4CF8.asc'
)


def write_fake_curl_and_sudo(tmp_path):
    """Install a fake curl/sudo pair as the only executables on PATH.

    The fake curl is a retry engine driven purely by the argv it receives,
    mirroring curl's documented semantics: ``--retry N`` allows N retries
    after the first attempt, and only ``--retry-all-errors`` makes codes
    outside the default transient set (timeouts) retryable at all. Besides
    recording its argv and one line per transfer attempt it performs no I/O,
    touches no real key path and never reaches the network. The fake sudo
    dispatches the privileged commands of the segment without privilege.
    """
    (tmp_path / 'curl').write_text(r'''#!/bin/sh
printf '%s\0' "$@" > "$CURL_ARGV"
retry_count=0
retry_all=0
while [ $# -gt 0 ]; do
    case "$1" in
        --retry) retry_count="$2"; shift ;;
        --retry-all-errors) retry_all=1 ;;
    esac
    shift
done
code="$SCENARIO_CODE"
attempt=1
while :; do
    printf '%s\n' "$attempt" >> "$CURL_ATTEMPTS"
    if [ "$attempt" -gt "$FAILURES" ]; then
        exit 0
    fi
    if [ "$retry_count" -lt 1 ] || { [ "$retry_all" -ne 1 ] && [ "$code" -ne 28 ]; }; then
        printf 'curl: (%s) error not retryable with this argv\n' "$code" >&2
        exit "$code"
    fi
    if [ "$attempt" -gt "$retry_count" ]; then
        printf 'curl: (%s) retries exhausted\n' "$code" >&2
        exit "$code"
    fi
    printf 'curl: (%s) transient error, retrying\n' "$code" >&2
    attempt=$((attempt + 1))
done
''')
    (tmp_path / 'sudo').write_text(r'''#!/bin/sh
command="$1"
shift
case "$command" in
    install) ;;
    curl) exec curl "$@" ;;
    tee)
        while IFS= read -r line; do :; done
        printf 'KEY_SOURCE_REGISTERED\n' >&2
        ;;
    apt-get) printf 'apt-get %s\n' "$*" ;;
    *) printf 'Unexpected privileged command\n' >&2; exit 99 ;;
esac
''')
    for fake in ('curl', 'sudo'):
        (tmp_path / fake).chmod(0o755)


def run_download_segment(tmp_path, script, scenario_code, failures):
    """Run one download segment against the fakes; nothing else is reachable."""
    write_fake_curl_and_sudo(tmp_path)
    argv_file = tmp_path / 'curl.argv'
    attempts_file = tmp_path / 'curl.attempts'
    attempts_file.write_text('')  # each run records only its own attempts
    done = subprocess.run(
        ['/bin/bash', '--noprofile', '--norc', '-c',
         script + '\nprintf "ASSEMBLY_CONTINUES\n"'],
        # PATH holds only the two fakes and no environment is inherited, so
        # every external operation resolves to an isolated stub. Fake tests
        # prove wiring, bounded retry behavior and exit codes only: no
        # network, no privilege, no real key download.
        env={'PATH': str(tmp_path), 'CURL_ARGV': str(argv_file),
             'CURL_ATTEMPTS': str(attempts_file),
             'SCENARIO_CODE': str(scenario_code), 'FAILURES': str(failures)},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
    return done, argv_file, attempts_file


@pytest.mark.parametrize('workflow,job_id', KEY_DOWNLOAD_WORKFLOWS, ids=('postgres', 'distribution'))
def test_linux_signing_key_download_recovers_transient_56_the_original_command_could_not(
        tmp_path, workflow, job_id):
    # A single mid-transfer CURLE_RECV_ERROR (56) followed by a good
    # transfer: the remediated argv recovers inside curl and the step
    # proceeds, while the pinned pre-remediation argv fails at the first
    # attempt -- the original pattern would NOT recover.
    done, argv_file, attempts_file = run_download_segment(
        tmp_path, linux_key_download_script(workflow, job_id), scenario_code=56, failures=1)
    assert done.returncode == 0
    assert attempts_file.read_text().splitlines() == ['1', '2']
    assert argv_file.read_bytes().decode().split('\0') == [*KEY_DOWNLOAD_ARGS, '']
    assert done.stdout == ('apt-get update -qq\n'
                           'apt-get install -y -qq postgresql-18\n'
                           'ASSEMBLY_CONTINUES\n')
    assert done.stderr == ('curl: (56) transient error, retrying\n'
                           'KEY_SOURCE_REGISTERED\n')
    before, before_argv, before_attempts = run_download_segment(
        tmp_path, 'set -euo pipefail\n' + ORIGINAL_KEY_DOWNLOAD_COMMAND,
        scenario_code=56, failures=1)
    assert before.returncode == 56
    assert before_attempts.read_text().splitlines() == ['1']
    assert before_argv.read_bytes().decode().split('\0') == [
        *shlex.split(ORIGINAL_KEY_DOWNLOAD_COMMAND)[2:], '']
    assert before.stdout == ''  # Fail-closed: no APT call, no sentinel.
    assert before.stderr == 'curl: (56) error not retryable with this argv\n'


@pytest.mark.parametrize('workflow,job_id', KEY_DOWNLOAD_WORKFLOWS, ids=('postgres', 'distribution'))
@pytest.mark.parametrize('curl_exit', [56, 22, 60])
def test_linux_signing_key_download_persistent_errors_exhaust_bounded_retries_and_fail_closed(
        tmp_path, workflow, job_id, curl_exit):
    # Persistent 56/22/60 stay fatal after the bounded retry budget (the
    # first attempt plus the three retries the argv allows): APT and the
    # continuation sentinel are never reached and diagnostics survive.
    done, argv_file, attempts_file = run_download_segment(
        tmp_path, linux_key_download_script(workflow, job_id), scenario_code=curl_exit, failures=99)
    assert done.returncode == curl_exit
    assert attempts_file.read_text().splitlines() == ['1', '2', '3', '4']
    assert argv_file.read_bytes().decode().split('\0') == [*KEY_DOWNLOAD_ARGS, '']
    assert done.stdout == ''
    assert done.stderr == (f'curl: ({curl_exit}) transient error, retrying\n' * 3
                           + f'curl: ({curl_exit}) retries exhausted\n')


def test_triggers_cover_manual_pr_weekly_push_and_papersim_paths():
    head = WORKFLOW.read_text().split('permissions:', 1)[0]
    assert head.startswith('name: Native project PostgreSQL\non:\n')
    assert '\n  workflow_dispatch:\n' in head
    # The pre-existing main-branch pull_request trigger stays intact.
    assert '\n  pull_request:\n    branches: [main]\n    paths:\n' in head
    # FX-07: direct pushes to main (this repository's working mode since late
    # September) must run the real-Postgres matrix for database-facing paths.
    push = head.partition('\n  push:\n')[2].partition('\n  schedule:')[0]
    assert push.startswith('    branches: [main]\n    paths:\n')
    assert tuple(re.findall(r"^      - '([^']+)'$", push, flags=re.M)) == PUSH_PATHS
    # Weekly schedule only: day-of-month and month stay wildcard, so
    # quota-limited private minutes are never spent on daily database runs.
    crons = re.findall(r"- cron: '([^']+)'", head)
    assert len(crons) == 1
    minute, hour, day, month, weekday = crons[0].split(' ')
    assert minute.isdigit() and hour.isdigit() and day == month == '*'
    assert weekday.isdigit()


def test_linux_papersim_partition_boots_throwaway_pg18_cluster():
    worker = jobs(WORKFLOW.read_text())['linux-native-part']
    parts = {p['partition']: p['files'] for p in include_block(worker)}
    # Exactly the two DB-facing papersim modules: the offline papersim face
    # already runs in offline-verification.yml.
    assert parts['papersim'] == list(PAPERSIM_FILES)
    assert "if: matrix.partition == 'papersim'" in worker
    # One-shot loopback cluster assembled from the SAME pinned PG18 prefix
    # (binaries only): no installed cluster, role or password is used.
    assert 'PREFIX="$RUNNER_TEMP/pg18-portable-prefix"' in worker
    assert 'DATA="$RUNNER_TEMP/papersim-pgdata"' in worker
    assert '"$PREFIX/bin/initdb" -U paper_sim -D "$DATA"' in worker
    assert '"$PREFIX/bin/pg_ctl" -D "$DATA"' in worker
    assert '"$PREFIX/bin/createdb" -h 127.0.0.1 -p 55432 -U paper_sim paper_sim' in worker
    # The opt-in ledger integration check needs the real relations: the
    # verbatim checked-in ledger migration is applied, loudly.
    assert 'ON_ERROR_STOP=1' in worker
    assert '-f supabase/migrations/20261101000000_papersim_ledger.sql' in worker
    # FX-09S: alongside the legacy face, the step assembles the papersim
    # MANAGED face (roles, project database, python-generated V1 bootstrap)
    # and exports validated socket trust DSNs; the papersim catalog itself
    # is applied inside the tests through the applier.
    assert 'createuser" -h 127.0.0.1 -p 55432 -U paper_sim pal_owner' in worker
    assert 'createuser" -h 127.0.0.1 -p 55432 -U paper_sim pal_app' in worker
    # M1 CI fix (native run 43741792178): the migration proofs create one
    # throwaway database per test as pal_owner, so the CI role needs
    # CREATEDB (the dev-server face connects as socket-trust superuser).
    assert 'ALTER ROLE pal_owner CREATEDB' in worker
    assert 'createdb" -h 127.0.0.1 -p 55432 -U paper_sim -O pal_owner polymarket_alpha_lab' in worker
    assert 'from polymarket_alpha_lab.project_postgres import sql' in worker
    assert 'sql.bootstrap_sql(' in worker
    assert '-U pal_owner -d polymarket_alpha_lab' in worker
    assert 'PAPERSIM_NATIVE_MANAGED_OWNER_DSN=host=%s port=55432 '
    'dbname=polymarket_alpha_lab user=pal_owner' in worker
    assert 'PAPERSIM_NATIVE_MANAGED_APP_DSN=host=%s port=55432 '
    'dbname=polymarket_alpha_lab user=pal_app' in worker
    # Both opt-in gates receive their environment from the throwaway cluster
    # (the names deliberately survive the job's PG* environment scrub).
    assert 'PAPERSIM_PG_DSN=postgresql://paper_sim@127.0.0.1:55432/paper_sim' in worker
    # FX-09L: the archived legacy history fixture is mounted into the
    # throwaway legacy face (read-only repo CSV files in the checkout)
    # for the importer's re-observation, baseline and fingerprint proofs.
    # decisions needs the explicit legacy column list: the mirrored
    # repo schema adds the three boundary columns the archived CSV
    # predates.
    decisions_copy = (
        f'-c "\\copy public.decisions (id,ts,market_id,question,side,price,shares,stake,fair_low,fair_high,spot,vol,horizon_days,sizing,reason,team,model_prediction,market_type) from '
        f"'docs/paper-sim/ledger-snapshots/2026-11-12/decisions.csv' "
        'with (format csv, header true)"'
    )
    assert decisions_copy in worker
    for relation in ('resolutions', 'bankroll'):
        expected_copy = (
            f'-c "\\copy public.{relation} from '
            f"'docs/paper-sim/ledger-snapshots/2026-11-12/{relation}.csv' "
            'with (format csv, header true)"'
        )
        assert expected_copy in worker
    assert 'WP_P4_READONLY_PG=1' in worker
    assert '>> "$GITHUB_ENV"' in worker


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


def test_linux_native_options_and_cleanliness_remain_required():
    worker = jobs(WORKFLOW.read_text())['linux-native-part']
    assert 'uv sync --locked --extra dev --extra postgres --python 3.12' in worker
    assert '-p no:faulthandler -p tests.ci_thread_dump -q -s -o junit_family=legacy -o faulthandler_timeout=120 $NATIVE_TEST_FILES --durations=20' in worker
    assert "NATIVE_TEST_FILES: ${{ join(matrix.files, ' ') }}" in worker
    for name in ('POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES',
                 'POLYMARKET_ALPHA_LAB_RUN_NATIVE_BACKUP', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD',
                 'PYTHONUTF8', 'PAL_REQUIRE_HANDOFF_SHELLS'):
        assert f'export {name}=1' in worker
    assert 'for variable in $(compgen -e PG); do unset "$variable"; done' in worker
    assert '.venv/bin/python -u -m pytest' in worker
    assert 'test ${PIPESTATUS[0]} -eq 0' in worker
    assert 'git diff --check\n          git diff --exit-code' in worker
    assert 'persist-credentials: false' in worker
    assert 'enable-cache: false' in worker
    assert 'fetch-depth: 0\n' in worker


def test_each_partition_retains_distinct_failure_artifacts():
    text = WORKFLOW.read_text()
    assert 'if: always()' in text
    assert 'name: native-postgres-proof-${{ matrix.partition }}-${{ github.sha }}' in text
    assert 'name: native-postgres-linux-proof-${{ matrix.partition }}-${{ github.sha }}' in text
    assert '${{ runner.temp }}/native-proof.log' in text
    assert '${{ runner.temp }}/native-proof.xml' in text
    assert 'if-no-files-found: error' in text
    assert "      - 'tests/test_native_postgres_workflow*.py'" in text


@pytest.mark.parametrize('gate_job', ['windows-native', 'linux-native'])
@pytest.mark.parametrize('result', ['success', 'failure', 'cancelled', 'skipped', ''])
def test_aggregate_shell_only_accepts_complete_success(gate_job, result):
    # On POSIX execute the actual aggregate body. Both hosted aggregates are
    # Ubuntu; Windows validates its exact body instead of invoking Git
    # Bash/MSYS implicitly.
    gate = jobs(WORKFLOW.read_text())[gate_job]
    script = '\n'.join(line[10:] for line in gate.split('        run: |\n', 1)[1].splitlines())
    assert script == 'set -euo pipefail\ntest "$NATIVE_RESULT" = success'
    if os.name != 'nt':
        done = subprocess.run(['/bin/bash', '-c', script], env={'NATIVE_RESULT': result},
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
        assert done.returncode == (0 if result == 'success' else 1)
        assert done.stdout == done.stderr == b''


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'selector', 'unknown-partition',
                                    'linux-missing', 'linux-unknown-partition'])
def test_partition_inventory_rejects_silent_coverage_changes(change):
    text = WORKFLOW.read_text()
    if change == 'missing':
        text = text.replace('            "tests/test_project_postgres_backup_native.py",\n', '', 1)
    elif change == 'duplicate':
        text = text.replace('"tests/test_project_postgres_backup.py"', '"tests/test_project_postgres_backup_native.py"', 1)
    elif change == 'selector':
        text = text.replace('"tests/test_project_postgres_backup_native.py"', '"tests/test_project_postgres_backup_native.py::test_selected"', 1)
    elif change == 'unknown-partition':
        text = text.replace('"partition": "storage"', '"partition": "unknown"', 1)
    elif change == 'linux-missing':
        head, separator, tail = text.partition('  linux-native-part:')
        assert separator
        text = head + separator + tail.replace(
            '            "tests/test_project_postgres_backup_native.py",\n', '', 1)
    else:
        head, separator, tail = text.partition('  linux-native-part:')
        assert separator
        text = head + separator + tail.replace('"partition": "storage"', '"partition": "unknown"', 1)
    chunks = jobs(text)
    with pytest.raises((AssertionError, ValueError)):
        for job_id, partitions in (('windows-native-part', WINDOWS_PARTITIONS),
                                   ('linux-native-part', LINUX_PARTITIONS)):
            validated(include_block(chunks[job_id]), partitions)
