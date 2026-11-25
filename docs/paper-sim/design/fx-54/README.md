# FX-54 Python Install Floor

## Baseline and scope

- The baseline was `ab6edff2d64ac8aa5c2831d748b8730b63f8312d`, the PR #123 merge on `main`.
- Workspace was an isolated Git worktree.
- The implementation branch is `fix/fx54-python-floor-20261124`.
- Implementation commit `c00a50e08f56ab0a03ad7d023a28984c4b2ec973` changes the project floor and generated lock. Documentation candidate `4818bef8f2e63e06ff0974b0fbdaa5081f6684de`, tree `fcd2607b8fe7f30fadeabf73bb2e6075c551f778`, is the source checked by the local verification and initial code review.
- NBA v2 head at node start was `2933a375fdc3c248514968277effb9a699097131`. Its merge base with the FX-54 baseline was `ab6edff2d64ac8aa5c2831d748b8730b63f8312d`. `git diff --name-only <merge-base>..<NBA-head> -- pyproject.toml uv.lock` returned no paths.
- Plan v2 has SHA-256 `474222c711cf92c6e753cee4d29e6881e5a9e475c12d2c711017c0c7608a4a10`. The original plan and FAIL receipt, revised plan, and PASS rereview are retained byte-for-byte in this directory.

| Retained artifact | SHA-256 |
| --- | --- |
| `fx54-plan-v1.md` | `8fdb4481e3b2d0756b8a3d7592784a9e226d817be8225ad054e6bb85bbf6f449` |
| `fx54-plan-v1-review-fail.json` | `5c92408a73fdef49e6b6d0d572b80796ec3e65c903f51196f07dab415c3a91d0` |
| `fx54-plan-v2.md` | `474222c711cf92c6e753cee4d29e6881e5a9e475c12d2c711017c0c7608a4a10` |
| `fx54-plan-v2-rereview-pass.json` | `50d78a5e435e7838099515edb020b59e6dd70e6e3aa169c8f1f954b7f1a54960` |
| `fx54-lock-delta-review.prompt` | `c5fbfe0905fbfb6d9dc3c51fa320d481d814cfd9c2d922cb7f8c15268ce9bdf4` |
| `fx54-lock-delta-review.json` | `2927559499bb3d5ba7021aa7bcb169296062b5e00a87f7cfde490cd824f5a6c8` |

## Lock generation and adjudication

The fixed tool was `fx54-tools/bin/uv`, version `0.10.0`. A single `uv lock` ran without `--upgrade`, using CPython 3.12.3 for resolution. `uv.lock` changed from SHA-256 `114db5230ce1b51130a6f2b246c807f4e142229c5717354edf95c8e44fc22d9f` to `9ed26c40ee1a48ef98cb6c8183db5202bc308eb5f57c46ffcf9f10a97f012b6c`.

The generated delta consists of `requires-python = ">=3.12"`, removal of eleven cp311 `psycopg-binary` wheel entries, and a stable reordering of the three existing `[package.metadata].requires-dist` rows. The original plan reviewer adjudicated this exact lock SHA pair in the same session and signed `VERDICT: PASS` for the ordering change only. The original plan FAIL, plan v2 PASS, and lock-delta PASS receipts are preserved separately. No hand edit or uv version substitution was used.

## Interpreter inventory

- CPython 3.11.15 is installed under the uv-managed user directory, exposed as `~/.local/bin/python3.11`.
- System CPython 3.12.3 is available as `/usr/bin/python3.12`.
- No managed CPython 3.12.14 was present during the inventory. Both available uv executables reported `No download found for request: cpython-3.12.14-linux-x86_64-gnu` when asked to install it.
- The official `Python-3.12.14.tgz` source tarball has SHA-256 `6c6df908d2c3fd24e6d76869e92542abd0f33aec9dfc18df8875f89660286d43`. The `.asc` and `.sigstore` sidecars remain in the isolated tools directory. CPython was built and installed in an isolated prefix using GCC 13.3.0, OpenSSL from the isolated build sysroot, `--with-openssl-rpath=auto`, and `--with-ensurepip=install`.
- The existing isolated PG18 prefix reports PostgreSQL 18.6.

## Local Verification

The [verification receipt](evidence/fx54-local-verification.json) records local verification results for candidate `4818bef8f2e63e06ff0974b0fbdaa5081f6684de` on November 24, 2026 UTC.

| Check | Recorded result |
| --- | --- |
| Lock and supported installation | With uv 0.10.0 on a clean checkout of `4818bef8`, `uv lock --check` selected isolated CPython 3.12.14 and exited 0. The lock stayed unchanged. The earlier check used system CPython 3.12.3 and also exited 0. The locked install with dev and postgres extras used CPython 3.12.14 and exited 0. Metadata readback exited 0 and reported `>=3.12` in the project, lock, and installed distribution. |
| Unsupported installation | uv 0.10.0 checked CPython 3.11.15 in a separate clean, detached candidate checkout in offline mode with Python downloads disabled. The command exited 2 with an explicit incompatibility with `>=3.12`. The check found no resolution, download, or environment creation. |
| v1 session-bundle fixture | `tests/test_project_postgres_session_bundle.py` exited 0 with 29 passed and 0 skipped. |
| Native version switch | The opt-in PG18 run of `tests/test_project_postgres_version_switch_native.py` exited 0 with 89 passed and 0 skipped. The case `test_native_same_root_source_selection_rollback_and_refusals` ran and passed once. |
| Frozen objects | The independent ZCode comparison matched the ladder module and all four fixtures. The receipt gives both blob IDs and sizes. |
| Tracked diff secret scan | The corrected scan of the baseline-to-candidate additions returned 1 from the secret matcher, meaning no matches, with a zero-byte findings file. The original runner recorded the added-line filter's exit 0 under `pattern_status` and stopped with exit 1. |
| CodeGraph | CodeGraph 1.6.3 synchronized a private snapshot of the existing project index to `4818bef8`. Strict status and content verification passed for 6,761 indexed files with zero parse errors. |
| Full test suite | Run 3 used CPython 3.12.3 and passed on a separate clean checkout of `4818bef8`: 43,956 passed, 437 skipped, 20 warnings, pytest exit 0 in 3,201.88 seconds. The receipt SHA-256 is recorded in the verification JSON. |
| Original code review | The original reviewer returned `VERDICT: PASS` on completed-evidence candidate `974887eb`. The earlier FAIL verdicts remain in the evidence history. |

The ZCode turn that recorded the positive install was interrupted after the individual checks. The continuation recorded the 3.11 refusal, both distribution checks, compile verification, and `git diff --check` before it stopped at the scan reporting error. The corrected scan ran separately.

The first full-suite attempt was interrupted after 4,204 passed and 15 skipped, with no recorded pytest exit. The second returned pytest exit 137 and an empty summary. The third run completed at 21:03:01 UTC on a separate clean checkout of `4818bef8` with a complete passing summary.

The [review and execution provenance](evidence/fx54-review-and-execution-provenance.json) binds the public review responses to the retained originals. Local dispatch records keep the execution identities.

## Pending Candidate Gates

- Final registration-lane integration, PR creation, CI receipts bound to the final PR head, `check_ci_merge.sh` status, and merge registration.

Record the native kit status against the final PR paths. The expected path-filter result is `NOT_TRIGGERED / kit proof 未运行`.
