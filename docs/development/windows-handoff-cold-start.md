# Windows handoff first-invocation evidence (WP-06)

## The open defect, not a claimed fix

PR #30 retained a real first-invocation failure in
`test_real_powershell_download_publication[success-powershell.exe]`: its existing
30-second subprocess timeout expired, while later cases passed. That does not
establish whether shell startup, module/assembly discovery, filesystem/hash
work or pipe closure was responsible. The later green runs did not fix it.

This change adds engineering-test observations, NOT another application report,
a downloader replacement or a remedy for that historical failure. The production
`scripts/download_handoff.ps1` is unchanged. No user installation, database,
credentials, network settings, execution policy, antivirus, module path or ACL is
modified. HTTP remains replaced by the existing synthetic fixture reader in these
tests; this is not live download/network acceptance.

## Instrumentation and retained behavior

The existing 18 PowerShell cases retain their cases, order, 30-second limit and
all prior parser, byte/hash, partial-retention, no-overwrite, Unicode and file-handle
assertions. A test-only ASCII prelude writes allowlisted stages and Stopwatch ticks
to a fresh file under that case's pytest temporary directory. The production hash
function is called unchanged through a test wrapper; the existing HTTP fixture shim
adds start/end markers. No preliminary PowerShell invocation or module discovery is
used to collect diagnostics. stdout remains the original result JSON.

`tests/handoff_probe.py` runs the child once with the same `-NoProfile`,
`-NonInteractive`, `-File`, inherited environment, UTF-8 capture and replacement
error decoding used previously. It retains subprocess.run's communicate timeout
and owned-child kill/wait/drain behavior. There is no automatic rerun, replacement
shell, relaxed assertion or timeout extension. The inherited `PSModulePath` is
neither read into logs nor repaired.

On timeout it samples `poll()` and emits a flushed, sanitized `handoff_probe` JSON
line BEFORE cleanup, then propagates the original TimeoutExpired. A broken
reporting sink cannot bypass child cleanup or replace that timeout; it adds only
a fixed diagnostic-failure note. Process creation and cleanup are not covered by
the 30-second communicate limit, just as before. In particular, inherited open
pipes may prolong drain/cleanup; the pre-cleanup record and the unchanged outer CI
job limit remain essential. This is not a general descendant process supervisor.

The file is test evidence, not a project-data persistence path. At most 8 KiB/64
stage records are read. Unknown stage names, invalid clocks and oversized/torn
traces are marked explicitly; only the valid prefix is reported. Paths, raw
stdout/stderr, URLs, arguments, environment values and exception text are excluded
from the new summary. Original synthetic-test assertions are not suppressed.

## Interpreting a record

`handoff_case` identifies the fixed test shell/case; `handoff_probe` contains:

| Field or stage | What it establishes / does not establish |
| --- | --- |
| `event=completed`, `returncode` | Communication and context cleanup completed; a nonzero result is still a test failure. Markers do not certify downloaded bytes. |
| `event=communicate_timeout` | The original communicate limit expired; `cleanup_complete=false` describes the time of emission, not the later cleanup outcome. |
| `process_when_sampled` | Running/exited when sampled AFTER the exception, not exactly at the deadline. An exited sample can indicate pipe draining or a race; it is not a root-cause verdict. |
| `create_ms`, `elapsed_ms` | Parent monotonic durations. Process creation can itself exceed the communicate limit. |
| `events[].since_first_ms` | Child-relative intervals, not parent/child clock synchronization, wall time or a precise startup duration. |
| `harness_enter`, `encoding_ready` | Test script entry followed by output encoding setup. Before entry, initial .NET resolution and trace-file creation are also unobserved. |
| `source_begin`, `source_ready` | Dot-sourcing the unchanged production script. |
| `invoke_begin`, `invoke_end` | Actual invocation and return. Return may be a correctly retained failed download. |
| `receive_begin/end`, `hash_begin/end` | Fixture transfer and original .NET hash boundaries. Work between the manifest hash and next transfer includes JSON parsing, validation and filesystem work; these are not individually timed. |
| `json_begin`, `harness_end` | Final JSON serialization/output was reached and the test script ended. Process teardown or pipe EOF may still be pending. |

A missing trace is `missing`, not proof that the engine never launched. A torn or
invalid final record is not silently treated as a completed stage. File I/O and
instrumentation can perturb the observed timings. This is an instrumented first
invocation, not proof that all host/OS caches were cold or that the original
unmodified timing distribution is unchanged. Use this evidence to localize a
future failure, not to assign causation from one interval alone.

## CI and local developer execution

`handoff-cold-start.yml` runs two distinct jobs: a cmd parent and a pwsh parent.
Both install from the locked environment under cmd, check executable presence
without launching either target, and run the ORIGINAL successful PS5.1 test first.
The pwsh variant deliberately retains the existing parent-shell context. That
variant does not claim its PS7 child is a cold PS7 invocation. No same-machine
retry or prewarm loop is added; matrix fail-fast is disabled to retain both results.
The original full Windows native gate also receives the stage evidence and helper
regressions, with all prior cases, watchdog settings and job deadline unchanged.

For a reviewed SOURCE checkout's existing environment, the focused tests are:

```powershell
.\.venv\Scripts\python.exe -m pytest -v -s tests/test_handoff_download.py tests/test_handoff_probe.py tests/test_handoff_probe_review.py
```

Run this only for a genuinely required developer investigation; it is NOT a new
user-machine handoff or instruction to repeat previously accepted installation.
On non-Windows hosts the existing 18 shell cases remain skipped. The helper tests
use synthetic process doubles plus real disposable Python children; these cannot
substitute for Windows execution. All first failures, JUnit and actual final-head
CI results must remain separate. Do not retry a cold failure merely to obtain green.

## Review and acceptance limit

The separate self-review initially reproduced a diagnostic-sink error that could
replace TimeoutExpired and skip child termination. The final helper protects the
original exception/cleanup; its failing assertion is retained. An intermediate
local edit also produced a syntax/collection error; it is recorded separately,
not counted as a product cold-start defect or a successful test.

Acceptance for THIS subtask means preserved assertions/timeouts, tested diagnostic
boundaries, sanitized output and actual Windows cmd/pwsh-context execution. **The
PR #30 root cause and long-term first-invocation reliability remain OPEN even if
both new jobs pass.** G6, G1-G5 evidence and owner D1-D3 decisions are not waived.

Primary behavior references checked 2026-09-15:
- https://docs.python.org/3/library/subprocess.html
- https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe?view=powershell-5.1
- https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_psmodulepath?view=powershell-7.5
