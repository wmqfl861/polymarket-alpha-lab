# tools/soakctl — distributable RV-05 launch-preflight and final-review package

Task PAL_RV05_CLOSURE_20260922, node N2 (mobility/closure delivery). This
directory makes the previously WorkRoot-local RV-05 launch package and
final reviewer distributable and reproducible from any checkout of this
repository: every personal host path is parameterized, every template
ships with placeholders only, and the 39-item N5 rejection test suite is
self-contained (fixtures generated in pytest `tmp_path`, no environment
variables required).

What this package is NOT: it is not an execution authorization, not a
launcher, and not a soak. The preflight is a READ-ONLY precondition
checker; the final reviewer is a READ-ONLY terminal-state adjudicator.
Constants everywhere: `official_cases_run=0`, `sandbox_started=false`,
`activation_authorized=false`. Phase 1 paper-only/report-only/readonly.

## Contents

| File | Role |
| --- | --- |
| `preflight-rv05.py` | read-only 21-check (+5 candidate-binding checks when the spec carries a `binding` section) launch precondition checker; byte-identical copy of the PAL_RV05_CAPACITY_20260921 N5 parameterized preflight (sha256 `93542abb…e762f`) |
| `run_final_review.py` | strict-caliber final reviewer with injectable identity (`--identity-json` / `--expected-*` / `--expect-*-schema`); distributable deltas vs the N5 working copy listed in its module docstring (frozen-tree default = this repo root; private runtime auto-candidate removed; placeholder usage text) |
| `make_synthetic_terminal.py` | builds synthetic full-pass / snapshot-incomplete campaign copies for validating the reviewer (paths are explicit CLI args; recipe unchanged from the V3 builder) |
| `launch-spec-template.json` | launch-spec skeleton for binding a future candidate; PLACEHOLDER values only — no real commits, hashes, PIDs or host paths |
| `identity-template.json` | `--identity-json` skeleton for the reviewer; PLACEHOLDER values only |
| `README.md` | this file |
| *(N3 integration slot)* `launch-rv05.ps1` | **NOT in this delivery** — the launcher belongs to node N3 and is expected at this exact path; see "Launcher integration slot" below |

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
two-flag synopsis.

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

## Launcher integration slot (node N3)

`launch-rv05.ps1` is deliberately NOT in this delivery: the launcher
script is owned by node N3 (single-file write ownership; the Windows
workflow yml and everything else here belong to N2). The expected
landing path is `tools/soakctl/launch-rv05.ps1`, next to this README.
The rejection suite's launcher cases discover it there (or via the
optional `PAL_RV05_N5_LAUNCH_DIR` override) and skip individually —
never module-wide — while the slot is unfilled. Once N3 lands the
launcher, a fresh Windows checkout runs all 39 cases with zero
environment setup.

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

## Boundaries

- Read-only tools; no launch, no stop-file writes against real
  campaigns, no process enumeration/kills, no credentials, no business
  databases, no network beyond git metadata of local checkouts.
- No real PIDs, host paths, or credentials are committed; templates
  carry placeholders only.
- The five `PAL_RV05_N5_*` environment variables remain as OPTIONAL
  overrides for pointing the suite at other tool copies; nothing
  requires them.
