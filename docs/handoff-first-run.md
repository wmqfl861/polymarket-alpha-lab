# WP-06: evidence for the first test-controlled PowerShell invocation

## The defect is still open

PR #30's initial native job 104082175714 (run 34875688580) failed the
unchanged `success-powershell.exe` handoff test at its 30-second deadline.
A later same-head pass did not establish a root cause. This change adds
focused test evidence for that specific release limitation. It does not claim
to repair Windows startup or to complete WP-06/G6 or V1.

The production `scripts/download_handoff.ps1` is byte-identical. No module path,
profile, execution policy, antivirus, ACL, cache, network configuration or timeout
is changed. There is no prewarming or retry. No user-machine command is required.

## What the test records

The existing synthetic PowerShell harness emits fixed stage names and elapsed
milliseconds to stderr using .NET, beginning before its first cmdlet. Stages cover
script entry, encoding setup, helper import, mock manifest/payload reads, helper
return and JSON serialization. The production helper remains uninstrumented and
HTTP remains replaced by the existing fixture bytes; no actual download occurs.
All prior success, hash, path, partial-transfer and preservation assertions remain.
Complete expected stage sequences are now required in addition to those assertions.
The hash test also records its entry/return and checks the released file handle.

The Python test helper publishes a bounded, fixed-vocabulary summary to the test
log and JUnit property `handoff_process`. It accepts both bytes and text from
TimeoutExpired, and never copies unknown stage names, other stderr text, stdout,
command arguments, environment values or raw fixture bodies into that summary.
Original pytest failure objects are retained; this is not a promise that arbitrary
raw pytest output is private-data-safe. Only synthetic tests use this helper.
Both native and first-invocation CI explicitly use pytest
`junit_family=legacy`, whose per-case properties support this evidence. The
initial candidate kept the native default xunit2 and emitted compatibility
warnings; its XML was readable, but no strict-xunit2 schema conformity was
claimed. The explicit format fixes that newly introduced warning without
changing test selection, assertions, process limits or production code.

TimeoutExpired, launch failure and interruption remain failures. Even a final
serialization marker does not override a nonzero exit code or process timeout.
A failure of the diagnostic sink must not replace the primary process exception;
only a fixed diagnostic-unavailable note is added. On an otherwise completed
process, a diagnostic sink error still fails the test rather than claiming complete
evidence. The original 30-second subprocess contract and invocation arguments
remain unchanged. Python notes that process creation itself may not be interruptible
on some platforms; this is not a newly promised universal wall-clock deadline.

## Reading incomplete evidence

| Last observed stage | What is known, not a root-cause finding |
| --- | --- |
| None | No fixture marker was observed. This alone does not prove a Windows startup fault. |
| `script_entered` or `encoding_ready` | The child entered the script; setup/import has not yet been observed complete. |
| `helper_loaded` or `invoke_entered` | The source helper loaded; the reported later operation has not completed. |
| `manifest_returned` | Mock manifest bytes were written; validation/next-stage completion remains unknown. |
| `payload_returned` | Mock payload bytes were written; hash/publication/return may still be outstanding. |
| `invoke_returned` | The helper returned; JSON serialization and process exit are separately observed. |
| `serialization_returned` | Serialization finished in the child; a later timeout still fails and can involve exit/pipe draining. |

Markers are observations, not authenticated tracing or proof of causality.
`root_cause_established=false` is retained on all summaries. Instrumentation can
itself perturb timing. Neither a fast observed run nor three passes closes the
original intermittent defect. Source-faithful failing evidence is required before
choosing a runtime/environment repair; do not guess at certificate checks or
module caches and disable them to obtain green.

## Historical CI experiment

The experiment used three independent Windows runners. Each ran the original
first-invocation regression with the original timeout. Distinct JUnit artifacts
recorded each trial. The observations and their limits are recorded below.
Each runner ran the PS5.1 success test first, followed by the probe contracts
and an injected timeout. The injected case kept the original 30-second limit
and its first marker. It was separate from the intermittent failure. The
native experiment covered the original 18 PS5.1/PS7 cases.

The offline probe contracts and download tests can be run locally. Actual
shell tests require Windows. Setting `PAL_REQUIRE_HANDOFF_SHELLS=1` makes a
missing required shell an error.

```bash
python -m pytest -q tests/test_handoff_process_probe.py tests/test_handoff_process_probe_review.py tests/test_handoff_download.py
```

[Subprocess timeout behavior](https://docs.python.org/3/library/subprocess.html) ·
[Pytest JUnit properties](https://docs.pytest.org/en/stable/_modules/_pytest/junitxml.html)

## Coordinator analysis of fixed historical logs, 2026-09-24; not a root-cause finding

This section preserves the coordinator's analysis of fixed GitHub Actions logs
on wmqfl861/polymarket-alpha-lab, dated 2026-09-24. It is evidence preservation
and bounded description only. It is not a root-cause finding,
`root_cause_established=false` still governs every summary below, no rule
stated earlier in this guide is weakened, and the defect remains open until a
separately reviewed repair lands.

### The three known timeout instances

A bounded historical scan (method and limits below), seeded, per the node
plan, with this guide's own section-1 record of job 104082175714, recovered
three instances of `test_real_powershell_download_publication[*-powershell.exe]
timed out after 30 seconds`, all on wmqfl861/polymarket-alpha-lab workflow
native-postgres.yml:

| # | When (UTC) | Run / job | Head | Instrumentation | What happened |
| --- | --- | --- | --- | --- | --- |
| 1 | 2026-09-14 15:50 | run 34864774728 / job 104045727679 | 6e29e02437bbbc2ae1163d8864bda00547599363 | pre-instrumentation | Both `[success-powershell.exe]` and `[manifest_hash-powershell.exe]` timed out at 30 s in one session; no stages captured (stage instrumentation landed the next day in f3537b27). |
| 2 | 2026-09-14 17:35 | run 34875688580 / job 104082175714 | 972f8143eff906cb5e3617a30bc25fcd3d799791 | pre-instrumentation | `[success-powershell.exe]` timed out at 30 s; the instance already recorded in this guide's section 1. Its run was later re-run to green (run_attempt 2 conclusion success), so listing runs by latest conclusion does not surface it - it is the one confirmed "re-run to green hides the first attempt" case and was recovered from the fixed record and its retained job log. |
| 3 | 2026-09-16 15:19 | run 35114517410 / job 104856403479 | 061a55703197cc526f3d707d9da58b971c582e0b | post-instrumentation | `[success-powershell.exe]` timed out at 30.46 s with complete staged evidence; the decisive sample, never previously preserved or analyzed. |

### Instance 3 staged evidence (job 104856403479)

| Stage | Elapsed ms |
| --- | --- |
| script_entered | 523 |
| encoding_ready | 18397 |
| helper_loaded | 18435 |
| invoke_entered | 18435 |
| manifest_entered | 26871 |
| manifest_returned | 26874 |
| killed at the 30 s deadline, before payload | last_observed_stage=manifest_returned |

The single line `[Console]::OutputEncoding = New-Object
System.Text.UTF8Encoding($false)` cost about 17.9 s (523 to 18397 ms). Summary
fields recorded for the killed case: malformed_marker_seen=false,
retry_count=0, root_cause_established=false.

### Same-session contrast rows (same job 104856403479)

| Case | Result | Notes |
| --- | --- | --- |
| `[success-pwsh]` (PS7) | passed in 2.67 s | encoding_ready @ 58 ms |
| `[manifest_hash-powershell.exe]` (second PS5.1 case in the session) | passed in 20.16 s | 15,000 ms gap invoke_returned to serialization on the ConvertTo-Json path |
| every later powershell.exe case | passed | each took 0.7 s or less |

### Observations (stated as observations, not mechanism proof)

- The 2026-09-16 timeout did NOT hang before script entry: process startup
  reached script entry in about 0.5 s.
- The 30 s was consumed inside the harness by first-use .NET Framework
  operations: the encoding constructor about 17.9 s, the first helper
  invocation path about 8.4 s, and about 3.5 s more before the kill.
- The cost amortizes machine-wide across the first roughly two powershell.exe
  processes: the second process still paid about 15 s on a different cold path
  (ConvertTo-Json serialization), and the third and later ones were
  sub-second. PS7 was unaffected in the same session.

Taken together, this refutes, for this instance, any "blocked before the
script starts" explanation, and it explains instance 1's two consecutive case
timeouts as the same amortizing first-use cost paid by two fresh processes.

### Hypotheses remain open

The observations do not distinguish between .NET Framework native-image (NGen)
regeneration or mismatch on the fresh runner image, security-software
first-scan of framework assemblies, and first-JIT costs. All three remain
unproven hypotheses and none is asserted. Per this guide's existing
no-guessing rule, no marker pattern is a mechanism proof, and no security,
antivirus or module-path setting may be changed to chase green.

### Frequency bound and its caveats

Exposure is one first-powershell.exe-invocation pytest session per
native-postgres.yml run since the test landed (ab7ec1d0, 2026-09-13): 73
completed sessions, plus 6 first-invocation sessions from two
handoff-first-run.yml runs (all passed). That gives 3 timeouts in at most 79
exposed sessions, about 3.8% (3/73, about 4.1%, counting native-postgres
only). Caveats: only failing-job logs were downloaded, so successful runs
could have contained the failing case only where a re-run to green hides the
first attempt; and exactly one such case was confirmed (instance 2,
recovered from the fixed record, not from run-level listing); the denominator
counts sessions, not cold-runner boots; runner image updates during the
window make trials heterogeneous; 4 cancelled runs were excluded; the
Lordakee remote contributed zero exposure (its history starts 2026-09-24).

### Scan method and its limits

The scan used bounded `gh api` run listings capped at 100 runs listed per
workflow, and downloaded and examined 33 failing-job logs; the effective
window is 2026-09-13..2026-09-24. Declared deviations from the node plan's
predeclared scan bounds: the listing cap was 100 runs per workflow instead
of the predeclared 50 (coverage-increasing, and the effective window start
2026-09-13 replaces the predeclared 2026-09-01 because the test only landed
2026-09-13, so earlier runs have no exposure). Limits: the listing cap means
older runs outside the window were not enumerated; log availability depends
on GitHub retention; and no successful-run log was downloaded, as recorded in the
caveats above.

### Campaign decision at the time

The 30-trial campaign was prepared and never dispatched. It produced no
additional observations. The bounded offline JUnit summarizer is
`tests/handoff_campaign_summary.py`.

### Raw log availability

The raw logs remain retrievable at the three job URLs while GitHub retains
them:
https://github.com/wmqfl861/polymarket-alpha-lab/actions/runs/34864774728/job/104045727679
https://github.com/wmqfl861/polymarket-alpha-lab/actions/runs/34875688580/job/104082175714
https://github.com/wmqfl861/polymarket-alpha-lab/actions/runs/35114517410/job/104856403479
