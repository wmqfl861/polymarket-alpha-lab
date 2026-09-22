# N3 -> N1 linker integration: L7-L9 subpatch

Task PAL_RV05_CONTROL_SAFETY_20260922, node N3 (Windows helpers + bounded
records). This document is the complete, executable integration handoff for
N1 (sole integrator of `tests/support/soak_linker.py`). N3 did NOT modify
any live file and did not push anything; the patch below was generated,
verified against a disposable copy, and the copy was restored pristine
(hash-verified).

## 1. Identity

| item | value |
|---|---|
| repository | wmqfl861/polymarket-alpha-lab (PR 67 workspace) |
| implementation base commit | `b061b7d7ad51cd21d7a64c84eba0941a1fd08085` |
| implementation base tree | `79ec2d7945f9dd2b492e243521e0c166f8fbebd0` |
| patch path | `C:\Users\Joyce Gu\pal-linkfix-fd1c1be7\evidence\n3\linker-subpatch-L7-L9.patch` |
| patch sha256 | `f9887735e2b7066ec9892cf012587e788be920147176607c373f8ec345cba3b5` |
| patch bytes | 18522 (433 lines, 11 hunks, unified diff of `tests/support/soak_linker.py` only) |
| pristine linker sha256 (base) | `1e1edfd4a90d5f57ab0e4845859e18ed3a30b9f6dd14a4ed3d2d084fe6709965` |
| patched linker sha256 (result) | `ab38b0f7bd9ce66c3e3e9e4fddac975bbf401f0f3cb781a7d5262200f00220b7` |
| diff stat | 263 insertions(+), 55 deletions(-), 1 file |

Before applying, N1 must re-verify: repo at the base commit, pristine
linker sha256 equals the base value above, and the patch sha256 equals
the value above. Stop on any mismatch (do not force-apply; the handoff
rule forbids partial application).

## 2. How to apply and verify (exact commands)

From the repo root at the base commit (Git Bash):

```bash
sha256sum tests/support/soak_linker.py   # must equal the pristine value
sha256sum "<WorkRoot>/evidence/n3/linker-subpatch-L7-L9.patch"
git apply --check "<WorkRoot>/evidence/n3/linker-subpatch-L7-L9.patch"   # must print nothing
git apply --index "<WorkRoot>/evidence/n3/linker-subpatch-L7-L9.patch"
sha256sum tests/support/soak_linker.py   # must equal the patched value
```

Compile + regression verification (sanitized bootstrap mirrors
prompt.md section 6; runtime python is the isolated read-only one):

```bash
cp "<WorkRoot>/evidence/n3/test_soak_linker_l7_l9.py" tests/
mkdir -p "<WorkRoot>/evidence/n3/<new-run-dir>"
"<RuntimePy>" -I -S -B "<WorkRoot>/evidence/n3/pytest_bootstrap.py" \
  "<RepoRoot>" "<RuntimeRoot>/Lib/site-packages" \
  "<WorkRoot>/evidence/n3/<new-run-dir>" \
  -q -p no:cacheprovider tests/test_soak_linker.py tests/test_soak_linker_l7_l9.py \
  "--basetemp=<WorkRoot>/evidence/n3/<new-run-dir>/pytest-temp" \
  "--junitxml=<WorkRoot>/evidence/n3/<new-run-dir>/result.xml"
# expected: 54 passed, 0 failed, 0 skipped (Windows; on Linux the 4
# Windows-real seed cases skip -- add platform expectations in N4's file)
```

N3's own execution of exactly this verification (patched disposable
copy) is preserved at
`evidence/n3/patched-tree-run-20260922T060157Z/` (result.xml:
tests=54 failures=0 errors=0 skipped=0; 104.25s, real Windows).

Whether the seed file `test_soak_linker_l7_l9.py` lands as-is or is
absorbed into N4's `tests/test_soak_linker_review.py` is N4's call; the
patch itself contains only the linker change.

## 3. Hunk map (what the patch changes and why)

| # | context (function) | change | fixes |
|---|---|---|---|
| 1 | import block | add `import math` (isfinite check) | L7 |
| 2 | `_read_capped` | read `max_bytes + 1` (one probe byte); over-cap -> new `'OVER_LIMIT'` sentinel; never returns truncated data | L7 |
| 3 | new helpers + `_read_json_record` | `_reject_record_duplicate_keys` (any depth), `_reject_nonstandard_constant` (NaN/Infinity/-Infinity), `_reject_nonfinite_floats` (1e999 overflow), non-object top level -> 'invalid'; new reason `'over_limit'`; RecursionError guarded | L7 |
| 4 | new Windows block: `_WIN_*` constants, `_win_kernel32()`, `_win_classify_openprocess_error()`, `_win_liveness()`, `_win_start_utc()`; rewritten `_pid_alive` | full ctypes ABI (OpenProcess/WaitForSingleObject/CloseHandle/GetProcessTimes/GetLastError; HANDLE `c_void_p`, wait status `c_uint32`); tri-state alive/dead/unknown; OpenProcess NULL classified by GetLastError, all shapes -> unknown, no elevation; WAIT_FAILED -> unknown; single-close handle ownership; dll/get_error injection seam | L8/L9 + same-source ABI |
| 5 | `_process_start_utc` | Windows branch delegates to `_win_start_utc` (ABI + unknown-on-failure); POSIX branch unchanged | same-source ABI |
| 6 | `load_binding` | `'OVER_LIMIT'` sentinel -> BindingError (was an AttributeError crash path); `parse_constant` + non-finite walk added to the binding parse | L7 |
| 7 | `Linker.load_state` | explicit `'over_limit'` -> Refused (was falling through to "not armed", which would permit a re-arm over an oversized state file) | L7 |
| 8 | `classify_original` lock membership | `('unreadable', 'invalid')` -> `+ 'over_limit'` (UNKNOWN) | L7 |
| 9 | segment-close membership | `+ 'over_limit'` (corrupted) | L7 |
| 10 | round.json membership | `+ 'over_limit'` (corrupted) | L7 |
| 11 | `valid_started_receipt` membership | `+ 'over_limit'` (receipt_over_limit) | L7 |
| 12 | preflight report gate | `+ 'over_limit'` (UNKNOWN, no launch) | L7 |
| 13 | stop-marker read | comment only: existence is the operative conservative fact; `isinstance(raw, bytes)` already excludes sentinels, so an over-limit marker still cancels | L7 (no behavior change) |

## 4. Code provenance

The implementation is EMBEDDED in `soak_linker.py` (single file, stdlib
only) on purpose: the linker is a pinned file whose sha256 is itself
bound in the binding manifest (`PINNED_FILE_FIELDS` includes `linker`),
so adding a sibling import would escape the pinned-identity model. The
same logic was developed and independently harness-verified as two
reference modules first:

- `evidence/n3/bounded_read.py` -- bounded read + strict JSON record
  parser (read_capped / strict_json_loads / read_json_record).
- `evidence/n3/win_probe.py` -- ABI table, tri-state liveness, start
  time, error classification (win_liveness / win_pid_alive /
  win_process_start_utc / _apply_abi).

Harness: `evidence/n3/harness_n3.py` -- 27 cases, three lanes
(A real FS/parser, B fake-DLL injection, C real Windows ABI on
self-owned processes) plus a RED lane reproducing the historical
defect against the fixed reader. Results:

- `evidence/n3/harness-results-20260922T055541Z/` -- first run, 26/27:
  C3 first FAILURE preserved (test-premise error: a freshly reaped
  exited pid on Windows is still open-able and correctly classifies
  DEAD via trusted wait_object_0; the probe was right, the test
  expectation was wrong). Root cause + fix recorded in the rerun dir.
- `evidence/n3/harness-results-20260922T055635Z/` -- 27/27 PASS after
  the C3 premise fix.

## 5. Behavior contract after the patch (for N1/N4 reviews)

Bounded reads (`_read_capped`) return `bytes | None | 'UNREADABLE' |
'OVER_LIMIT'`; a file of exactly the cap still reads; cap+1 or more is
rejected. `_read_json_record` reasons are
`{'', 'absent', 'unreadable', 'over_limit', 'invalid'}` with strict
parse rules (duplicate keys at any depth, NaN/Infinity/-Infinity,
non-finite floats, non-object top levels, pathological nesting ->
'invalid'). Consumers updated per hunk map above; the stop marker keeps
existence-based cancel semantics.

Windows liveness tri-state table (only trusted evidence proves exit):

| API shape | detail | tri-state |
|---|---|---|
| OpenProcess NULL, gle=5 | access denied: permission unknown, never elevated, exactly one open attempt (SYNCHRONIZE only) | unknown |
| OpenProcess NULL, gle=87 | invalid parameter (nonexistent-pid shape): NOT exit evidence | unknown |
| OpenProcess NULL, other gle | recorded in reason | unknown |
| WaitForSingleObject = 258 | not signaled within 0 ms | alive |
| WaitForSingleObject = 0 | process object signaled | dead (the ONLY dead source) |
| WaitForSingleObject = 0xFFFFFFFF | WAIT_FAILED: gle recorded | unknown |
| other wait codes / OSError | recorded | unknown |
| invalid pid arg (<=0, non-int) | | unknown |

Handle ownership: the handle OpenProcess returned is closed exactly
once per probe (finally); NULL is never closed; WAIT_FAILED does not
free the handle so the close still runs; CloseHandle failure does not
retry and does not alter the tri-state. Errors are read via
`WinDLL(..., use_last_error=True)` + `ctypes.get_last_error()` (race
free); GetLastError is ABI-declared but not relied upon.

64-bit handles: OpenProcess restype is `c_void_p` (full pointer width,
never truncated to the ctypes default C int); WaitForSingleObject
restype `c_uint32` so WAIT_FAILED compares as 0xFFFFFFFF (not -1);
verified structurally + by fake full-width handle pass-through
(B8/handle test) + real round-trip (C4).

Injection seam kept for N4: `_win_liveness(pid, dll=None,
get_error=None)` and `_win_start_utc(pid, dll=None, get_error=None)`
accept fake DLL objects; the shipped `_pid_alive` calls them with the
real DLL. N4 can also monkeypatch `_win_liveness` for `_pid_alive`
mapping tests.

## 6. Integration consequences N1/N0 must handle

- The linker file sha256 CHANGES (see identity table). The linker is
  CONTROL-side code: per the capacity-reuse rule the SUT target
  (b061b7d7 soak candidate bytes) stays untouched; N0 binds SUT_SHA and
  CONTROL_SHA separately, and any binding created before integration
  pins the OLD linker sha and must be re-pinned/rebuilt, never edited.
- Windows CI selection must add the linker module tests (N4); today's
  456-passed Windows evidence has linker_cases=0 and does not transfer.
- N1's own L1-L4 hunks touch the same file: apply this subpatch first
  on the pristine base (or coordinate hunks -- all 11 hunks here are in
  helper/consumer regions; `_read_json_record` region and the win block
  do not overlap L1-L4 areas: preflight/claim/receipt logic), then
  re-run the full regression selection.
- Repo-side publication: per the cross-project handoff rule the patch
  file must be committed to the PR branch before handoff delivery; N3 is
  forbidden from writing the live repo or pushing, so committing this
  patch file (and the N4 seed if adopted) is part of N1/N0 integration.

## 7. Boundaries honored by N3

No live linker/launcher/audit/generator file modified (disposable
clone only, restored and hash-verified pristine; branch
`work/linker-safety-n3-648bc8a7` left as read-only baseline at the base
commit). No original-campaign/PID operations (only self-owned child
processes, one at a time). No arming, no real model/CLI/credentials/
market/orders/business DB/native DB, no privilege elevation, no
sandbox/system changes, no dependency installs, no push, no fast mode.
official_cases_run=0, sandbox_started=false, activation_authorized=false.
