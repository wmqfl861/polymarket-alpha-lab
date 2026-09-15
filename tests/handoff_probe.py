"""Test-only cold-launch evidence. Never a runtime helper or business journal.

Keep the original subprocess timeout/kill/wait contract. A tiny stage file is
independent of stdout/stderr pipe closure, so timeout evidence is emitted BEFORE
cleanup. Sampling after TimeoutExpired is not proof of state exactly at deadline.
"""
from __future__ import annotations

from base64 import b64encode
import json
from pathlib import Path
import re
import subprocess
import sys
from time import monotonic

MAX_TRACE_BYTES = 8192
MAX_EVENTS = 64
STAGES = frozenset(('harness_enter', 'encoding_ready', 'source_begin', 'source_ready',
                   'invoke_begin', 'receive_begin', 'receive_end', 'hash_begin',
                   'hash_end', 'invoke_end', 'json_begin', 'harness_end'))
_LINE = re.compile(r'([a-z_]+)\|([0-9]{1,19})\|([0-9]{1,13})')


def trace_prelude(path: Path) -> str:
    """ASCII harness text; no cmdlet/module scan before the first marker.

    The initial .NET type resolution and opening the trace file are themselves
    unobserved work. A missing marker must not be diagnosed as an engine defect.
    """
    encoded = b64encode(str(path).encode('utf-8')).decode('ascii')
    return f"""
$ErrorActionPreference = 'Stop'
$script:HandoffProbePath = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))
$script:HandoffProbeClock = [Diagnostics.Stopwatch]::StartNew()
function Write-HandoffProbe([string]$Stage) {{
    [IO.File]::AppendAllText($script:HandoffProbePath,
        ($Stage + '|' + $script:HandoffProbeClock.ElapsedTicks.ToString([Globalization.CultureInfo]::InvariantCulture) +
         '|' + [Diagnostics.Stopwatch]::Frequency.ToString([Globalization.CultureInfo]::InvariantCulture) + [Environment]::NewLine),
        [Text.Encoding]::ASCII)
}}
Write-HandoffProbe 'harness_enter'
"""


def read_trace(path: Path) -> dict:
    """Only allowlisted stages and relative intervals can enter public CI logs.

    Keep a valid prefix after a torn/invalid final line. Never echo malformed
    input, filenames, environment values, command text or exception strings.
    """
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_TRACE_BYTES + 1)
    except FileNotFoundError:
        return dict(trace_state='missing', events=[], last_stage=None)
    except OSError:
        return dict(trace_state='unreadable', events=[], last_stage=None)
    events = []
    state = 'complete'
    frequency = first = previous = None
    if len(raw) > MAX_TRACE_BYTES:
        raw = raw[:MAX_TRACE_BYTES]
        state = 'over_limit'
    for line in raw.splitlines(keepends=True):
        if len(events) == MAX_EVENTS:
            state = 'over_limit'
            break
        if not line.endswith(b'\n'):
            state = 'partial' if state == 'complete' else state
            break
        match = _LINE.fullmatch(line.rstrip(b'\r\n').decode('ascii', errors='replace'))
        if match is None or match[1] not in STAGES:
            state = 'invalid'
            break
        stage, ticks, hz = match[1], int(match[2]), int(match[3])
        if (hz == 0 or hz > 10**12 or ticks > 2**63-1
                or (frequency is not None and (hz != frequency or ticks < previous))):
            state = 'invalid'
            break
        if frequency is None:
            frequency, first = hz, ticks
        previous = ticks
        events.append(dict(stage=stage, since_first_ms=round((ticks-first)*1000/hz, 3)))
    if not events and state == 'complete':
        state = 'empty'
    return dict(trace_state=state, events=events,
                last_stage=events[-1]['stage'] if events else None)


def run_traced(args: list[str], trace: Path, *, timeout: float = 30) -> tuple[subprocess.CompletedProcess, dict]:
    """Run once, with inherited environment and the original communicate limit.

    Like subprocess.run, process creation and kill/drain cleanup are NOT bounded
    by that communicate timeout. An exited child can still have undrained pipes.
    The timeout report is flushed before cleanup so that fact is not lost. Never
    retry, prewarm a shell, change PSModulePath or suppress the timeout exception.
    """
    started = monotonic()
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding='utf-8', errors='replace') as process:
        created_ms = round((monotonic()-started)*1000, 3)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            try:
                observed_code = process.poll()
                details = dict(event='communicate_timeout', timeout_seconds=timeout,
                    create_ms=created_ms, elapsed_ms=round((monotonic()-started)*1000, 3),
                    process_when_sampled='running' if observed_code is None else 'exited',
                    returncode_when_sampled=observed_code, cleanup_complete=False, **read_trace(trace))
                # Only test-stage data; independent of the child's output/pipe EOF.
                print('handoff_probe ' + json.dumps(details, sort_keys=True, allow_nan=False), flush=True)
            except Exception:
                # Diagnosis must never prevent cleanup or erase the original
                # timeout. Do not echo the sink/trace exception's private text.
                error.add_note('handoff_probe_diagnostic_failed')
            finally:
                # Preserve subprocess.run's existing owned-child cleanup semantics.
                process.kill()
                if sys.platform == 'win32':
                    error.stdout, error.stderr = process.communicate()
                else:
                    process.wait()
            raise
        except BaseException:
            process.kill()
            process.wait()
            raise
        code = process.poll()
    details = dict(event='completed', timeout_seconds=timeout, create_ms=created_ms,
        elapsed_ms=round((monotonic()-started)*1000, 3), returncode=code,
        cleanup_complete=True, **read_trace(trace))
    print('handoff_probe ' + json.dumps(details, sort_keys=True, allow_nan=False), flush=True)
    return subprocess.CompletedProcess(args, code, stdout, stderr), details
