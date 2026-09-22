# tools/soakctl — distributable RV-05 launch-preflight and final-review package

Task PAL_RV05_CLOSURE_20260922, node N2 (mobility/closure delivery). This
directory makes the previously WorkRoot-local RV-05 launch package and
final reviewer distributable and reproducible from any checkout of this
repository: every personal host path is parameterized, every template
ships with placeholders only, and the 39-item N5 rejection test suite is
self-contained (fixtures generated in pytest `tmp_path`, no environment
variables required).

What this package is NOT: it is not an execution authorization and not a
soak. The preflight is a READ-ONLY precondition checker; the final reviewer
is a READ-ONLY terminal-state adjudicator; the launcher is a one-shot
claim-first wrapper that refuses every second launch. Constants everywhere:
`official_cases_run=0`, `sandbox_started=false`,
`activation_authorized=false`. Phase 1 paper-only/report-only/readonly.

## Contents

| File | Role |
| --- | --- |
| `preflight-rv05.py` | read-only 21-check (+5 candidate-binding checks when the spec carries a `binding` section) launch precondition checker; descendant of the PAL_RV05_CAPACITY_20260921 N5 parameterized preflight (historical byte-identical copy sha256 `93542abb…e762f`). Control-safety wave (2026-09-22): the argument surface is now exactly `--spec/--json-out/--execution-id` (abbreviations and unknown flags are hard usage errors) and every report carries an additive `execution` provenance block (execution id, spec file digest, argv echo); the 21+5 check ids and their semantics are unchanged |
| `run_final_review.py` | strict-caliber final reviewer with injectable identity (`--identity-json` / `--expected-*` / `--expect-*-schema`); distributable deltas vs the N5 working copy listed in its module docstring (frozen-tree default = this repo root; private runtime auto-candidate removed; placeholder usage text) |
| `make_synthetic_terminal.py` | builds synthetic full-pass / snapshot-incomplete campaign copies for validating the reviewer (paths are explicit CLI args; recipe unchanged from the V3 builder) |
| `launch-rv05.ps1` | one-shot claim-first launcher for the corrected 72h run (fix-wave placement; see "Launcher (one-shot, claim-first)" below) |
| `launch-spec-template.json` | launch-spec skeleton for binding a future candidate; PLACEHOLDER values only — no real commits, hashes, PIDs or host paths |
| `identity-template.json` | `--identity-json` skeleton for the reviewer; PLACEHOLDER values only |
| `README.md` | this file |

Related tracked files (already in the repository, not part of this
directory): `launch/config-rv05-final-template.json` (bound-config
template with `BIND:` placeholders) and `launch/manifest-rv05-final.json`
(nominal 11-scenario manifest).

## Download (fixed-commit raw URLs)

After the delivery commit is pushed, each file is reachable at:

```
https://raw.githubusercontent.com/wmqfl861/polymarket-alpha-lab/<DELIVERY-COMMIT>/tools/soakctl/<FILE>
```

`<DELIVERY-COMMIT>` is the 40-hex SHA recorded for this delivery in the
task evidence (`evidence/closure-n2/` on the controlling host; the commit
exists on branch `work/rv05c-n2-*` and is integrated into PR #67 by node
N0). Download example:

```
curl -fsSL -o preflight-rv05.py \
  https://raw.githubusercontent.com/wmqfl861/polymarket-alpha-lab/<DELIVERY-COMMIT>/tools/soakctl/preflight-rv05.py
```

Remote read-back verification (download the file back and compare its
SHA256 against the delivery manifest) is performed by node N0 after the
push; this README was authored on the pre-push commit, so it cannot
contain its own commit's SHA.

## Safety preflight (run before anything else)

The preflight never writes anything except its optional `--json-out`
report. Running it against the AS-SHIPPED template must refuse — the
placeholders fail the binding checks (verified, see "Verification"
below):

```
python -I -S -B tools/soakctl/preflight-rv05.py \
  --spec tools/soakctl/launch-spec-template.json \
  --json-out preflight-last.json
```

Expected: exit 1, `overall: NO_GO`, `protections: {read_only: true,
original_campaign_touched: false, processes_killed: 0,
launch_performed: false}`. Any launch attempt with unfilled placeholders
stays NO_GO; only a fully bound, hash-matching spec can reach GO.

## Usage

### 1. Preflight a bound candidate (read-only)

Fill a copy of `launch-spec-template.json` (identity hashes from the
candidate repo, paths from the invoking host), then:

```
python -I -S -B tools/soakctl/preflight-rv05.py --spec <bound-spec.json> --json-out preflight-last.json
```

Exit 0 = GO (every check PASS), exit 1 = NO_GO (`unmet_ids` lists the
failing checks). 21 legacy checks; a spec with a `binding` section adds
`binding_complete`, `binding_config_digest`, `binding_manifest_digest`,
`binding_receipt_contract`, `binding_generator_sha`. `--help` prints the
three-flag synopsis (`--spec`, `--json-out`, `--execution-id`;
abbreviations are rejected).

### 2. Final-review a finished campaign (read-only)

```
python -I -B tools/soakctl/run_final_review.py review \
  --campaign <campaign dir> \
  --config <soak config.json> \
  --manifest <soak manifest.json> \
  --driver-file <repo>/tests/support/soak_driver.py \
  --python-file <the python.exe the soak ran under> \
  --identity-json <identity.json bound to the candidate> \
  --min-rounds 864 --min-distinct-inputs 100000 \
  --out <evidence out dir> --label <label>
```

Exit 0 PASS / 3 FAIL / 4 UNKNOWN / 2 NOT_RUN (refused: still running or
missing) / 5 invalid invocation (e.g. incomplete `--identity-json`) /
1 internal error. `--frozen-tree` defaults to this repository root and
can point elsewhere. Without `--python-file` the interpreter dimension
stays UNKNOWN (never an optimistic PASS). `review --help` prints the
full flag surface.

### 3. Build synthetic terminal states (validation helper)

```
python -I -B tools/soakctl/make_synthetic_terminal.py \
  --campaign <finished campaign dir> --config <its config.json> \
  --out <out dir> --min-rounds 5 --max-wall-seconds <span of that campaign>
```

Writes `synth-b-fullpass/` and `synth-b2-incomplete/` copies; exit 0 when
the full-pass copy passes its integrity spot checks.

## Launcher (one-shot, claim-first; fix-wave placement 2026-09-22)

`tools/soakctl/launch-rv05.ps1` is the distributable placement of the
corrected-72h one-shot launcher (previously WorkRoot-local only). The
claim/receipt one-shot semantics are unchanged from the frozen V3/
launch-final lineage: after the read-only preflight reaches GO and the
planned campaign root is confirmed absent, a UNIQUE claim is written
atomically BEFORE any directory creation or process start; if no completed
receipt follows the claim, the launch state stays LOST-ACK/UNKNOWN (exit 6)
— no automatic second launch, no claim recovery, no receipt rewrite. A
valid completed receipt (v1 or v2, dry_run=false) refuses any second real
launch (exit 3) before the preflight even runs. The script never touches
the original campaign or any historical PID, never stops/kills/scans
processes, and never runs the closeout (it only records the follow-up
command in the receipt).

Distributable parameter surface (the only deltas vs the launch-final copy):

- `-SpecPath` is REQUIRED — there is no default (no host/personal path
  ships in the file). Fill a copy of `launch-spec-template.json` per
  candidate and pass it explicitly.
- A RELATIVE `-SpecPath` resolves against this repository's root (the
  script's grandparent directory), not the caller's working directory.
- A preflight that cannot even be spawned (e.g. an unfilled template spec
  with placeholder `python_exe`) fails closed as `PREFLIGHT_FAILED`,
  exit 2 — same exit as any non-GO preflight.

Usage (verified by really running it — see "N2 control-safety
verification" below). COMPLIANT INVOCATION FORM (the old
`-ExecutionPolicy Bypass` example from earlier waves is REMOVED and the
launcher now refuses it — see "Invocation contract (L6)"):

```
powershell -NoProfile -ExecutionPolicy RemoteSigned -File tools/soakctl/launch-rv05.ps1 `
  -SpecPath <bound-launch-spec.json> -DryRun
```

`-DryRun` performs and prints everything except the launch: the read-only
preflight still runs, the exact launch command / campaign / STOP / receipt
paths are printed, nothing is created under the planned campaign root, no
claim is written, no process is started. Against the AS-SHIPPED template
the same command exits 2 at the preflight spawn (placeholders), which is
the expected fail-closed behavior for an unfilled spec.

Exit codes: 0 launched / dry-run OK; 2 preflight not GO, no report,
invalid invocation (missing `-SpecPath`), policy-bypass invocation
BLOCKED, spec carries synthetic-hook/policy-bypass tokens, stale/foreign
preflight report, or a launch-boundary recheck refusal (spec drift /
pins / window); 3 duplicate launch refused; 4 planned campaign root
exists; 5 launched process exited within 8s; 6 lost-ack (UNKNOWN launch
state) refused.

## Invocation contract (L6) and launch-boundary rechecks (control-safety
wave, PAL_RV05_CONTROL_SAFETY_20260922 node N2)

- **No policy bypass, in any form.** `powershell.exe -ExecutionPolicy <X>`
  sets `$env:PSExecutionPolicyPreference` for the process; the launcher
  checks it FIRST and exits 2 with a `BLOCKED: policy-bypass invocation
  rejected` message for `Bypass` and `Unrestricted` before reading or
  writing anything. Approved invocation forms are: no `-ExecutionPolicy`
  switch at all (the host-configured policy governs), or
  `-ExecutionPolicy RemoteSigned` / `AllSigned`. Equivalent bypass forms
  (Unblock-File, -EncodedCommand, stdin scripts, alternate interpreters)
  are equally forbidden. **If the host execution policy rejects this
  script file, the invocation fails with an execution-policy error before
  the script body runs: record that BLOCKED outcome as-is and configure
  an approved policy (an operator action outside this repository) — do
  not bypass.**
- **Fresh-result binding (old-GO rejection).** Every invocation deletes
  any pre-existing preflight report, generates a unique execution id,
  passes it to the preflight via `--execution-id`, and verifies the
  consumed report is bound to THIS execution: matching `execution_id`,
  matching `spec_sha256` (the digest of the exact spec file bytes this
  invocation read), known report schema, and `generated_at_utc` at/after
  this invocation's preflight start. A stale GO residue — or a
  "preflight" that replays a canned GO report — is refused with `REFUSED:
  preflight report is not bound to this execution` (exit 2).
- **Launch-boundary rechecks (the last safe point before the irreversible
  claim).** After the GO gate and the campaign-absent gate, immediately
  before the unique claim is written, the launcher re-verifies: the spec
  file is still byte-identical to what this invocation read (no swap
  between read and use), the pinned toolchain file hashes
  (python/driver/audit/closeout) still match `spec.frozen`, and the
  launch window (earliest/latest) is still open at the boundary itself —
  preflight duration can no longer carry an expired window into a launch
  (counterexample L2's launcher layer). A dry run prints all three as a
  `RECHECK PREVIEW` line; a real launch refuses (exit 2) on any
  mismatch.
- **Spec hygiene.** A FORMAL spec must not carry synthetic test hooks or
  bypass forms: `--now-utc`, `--stop-after`, `-executionpolicy bypass` or
  `-encodedcommand` anywhere in the spec file refuses the invocation
  (exit 2) before the preflight runs.
- The preflight's argument surface is exactly
  `--spec/--json-out/--execution-id`; abbreviated flags (`--exec`) and
  unknown flags (e.g. a smuggled `--now-utc`) are hard usage errors.

The rejection suite's nine launcher cases now find the launcher at this
path (or via the optional `PAL_RV05_N5_LAUNCH_DIR` override) and really
execute on Windows; on non-Windows they skip individually with a platform
reason (never module-wide).

## Reproducible verification (what N2 actually ran, 2026-09-22)

All of the following were really executed on the delivery worktree
(Windows, Git Bash, sanitized environment without any `PAL_RV05_N5_*`
variables); outputs are archived in the task evidence under
`evidence/closure-n2/`:

1. `python -m py_compile` on all four delivered Python files — OK.
2. `python -m pytest -q tests/test_rv05_n5_launch_review_rejection.py`
   from the fresh checkout: **30 passed, 9 skipped** (the nine launcher
   cases skip with the explicit N3-slot reason above), 2m33s.
3. The same command with `PAL_RV05_N5_LAUNCH_DIR` pointing at a
   directory containing the launcher: **39 passed**, 0 skipped, 2m15s.
4. `--help` on preflight (`preflight-rv05.py --help`), reviewer
   (`run_final_review.py review --help`) and synthetic builder
   (`make_synthetic_terminal.py --help`) — all print their synopsis.
5. Safety dry-run of the preflight against the as-shipped template —
   exit 1, NO_GO, read-only protections asserted (see above).
6. End-to-end tool exercise on a real tiny driver-produced campaign in a
   temp dir: `make_synthetic_terminal.py` exit 0 with all six
   `BUILD_CHECKS` true; `run_final_review.py review` on the full-pass
   copy — exit 0 PASS with `FINAL_REVIEW_STRICT` marker; on the
   snapshot-incomplete copy — exit 4 UNKNOWN with audit_overall
   `SNAPSHOT_INCOMPLETE`.

The Windows CI workflow (`.github/workflows/windows-soak-contract.yml`)
runs this suite on every PR touching it or `tools/soakctl/**` and asserts
the module never skips wholesale (≥ 30 executed N5 cases, four named
cases must run unskipped).

## Fix-wave verification (2026-09-22, launcher placement + H1/M1)

Really executed on the integration worktree (Windows, Git Bash, sanitized
environment without any `PAL_RV05_N5_*` variables); outputs archived under
`evidence/closure-fixwave/` on the controlling host:

1. `launch-rv05.ps1` with no `-SpecPath` — USAGE message, exit 2, nothing
   else runs.
2. `launch-rv05.ps1 -SpecPath tools/soakctl/launch-spec-template.json
   -DryRun`, invoked from the repo root AND from a different working
   directory with the same relative path — both resolve the spec against
   the repository root (identical absolute `--spec` path printed), fail
   closed at the placeholder preflight spawn, exit 2, zero side effects.
3. `launch-rv05.ps1 -SpecPath <filled NO_GO synthetic spec> -DryRun` —
   read-only preflight runs and reports NO_GO, the full dry-run plan is
   printed, exit 2, and only the spec-directed `preflight-last.json`
   exists afterwards (no claim, no receipt, nothing under the planned
   campaign root).
4. `pytest -q tests/test_rv05_n5_launch_review_rejection.py` — 39 passed,
   0 skipped, three consecutive sanitized runs green (control-fixture
   timing margin hardened; see the fix-wave evidence for the arithmetic).

## N2 control-safety verification (2026-09-22, PAL_RV05_CONTROL_SAFETY_20260922)

Really executed on the N2 worktree (Windows, Git Bash, sanitized
environment, branch `work/linker-safety-n2-*`); outputs archived under
`evidence/n2/` on the controlling host:

1. Compliant live invocations of the launcher:
   `powershell -NoProfile -ExecutionPolicy RemoteSigned -File
   tools/soakctl/launch-rv05.ps1` with no `-SpecPath` — USAGE, exit 2;
   with the AS-SHIPPED template spec + `-DryRun` — fail-closed at the
   placeholder preflight spawn, exit 2, zero side effects.
2. Bypass rejection live: the same invocation with
   `-ExecutionPolicy Bypass` — `BLOCKED: policy-bypass invocation
   rejected (PSExecutionPolicyPreference=Bypass)`, exit 2, before
   anything is read or written. (`Unrestricted` is refused the same
   way; verified in the test suite.)
3. `pytest -q tests/test_rv05_n2_launch_safety.py` — **16 passed**
   (policy-bypass refusal for Bypass/Unrestricted; spec synthetic-hook
   and bypass-token refusal; planted-residue deletion; replayed-GO
   rejection with everything correct except the execution id; legacy
   no-execution-block report rejection; compliant RemoteSigned and
   no-switch dry runs on GO and NO_GO synthetic specs; preflight argv
   strictness and execution provenance).
4. `python -m py_compile` on the delivered Python files — OK.
5. Cross-node note (first failure preserved verbatim in
   `evidence/n2/n4-suite-cross-impact-first-failure.txt`):
   `pytest -q tests/test_rv05_n5_launch_review_rejection.py` — **30
   passed, 9 failed**. The 30 self-contained preflight/reviewer/builder
   cases pass unchanged; the nine launcher cases in that N4-owned file
   still invoke the launcher with `-ExecutionPolicy Bypass` and now
   correctly receive the BLOCKED refusal (exit 2) instead of their old
   expected outcomes. Those nine cases are N4's to convert to the
   compliant invocation form in this wave (plan node N4); the identical
   duplicate-launch / lost-ack semantics are re-proven under the
   compliant form by the N2 suite above.

## Boundaries

- Read-only tools; no launch, no stop-file writes against real
  campaigns, no process enumeration/kills, no credentials, no business
  databases, no network beyond git metadata of local checkouts.
- No real PIDs, host paths, or credentials are committed; templates
  carry placeholders only.
- The five `PAL_RV05_N5_*` environment variables remain as OPTIONAL
  overrides for pointing the suite at other tool copies; nothing
  requires them.
