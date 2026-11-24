# FX-54 Python Install Floor

## Baseline and scope

- The baseline was `ab6edff2d64ac8aa5c2831d748b8730b63f8312d`, the PR #123 merge on `main`.
- Workspace was an isolated Git worktree.
- The implementation branch is `fix/fx54-python-floor-20261124`.
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
- No managed CPython 3.12.14 was present during the inventory. Both available uv executables reported `No download found for request: cpython-3.12.14-linux-x86_64-gnu` when asked to install it. No Python download or build has yet been performed.
- The exact 3.12.14 positive installation and metadata proof remains pending. CPython 3.12.3 can exercise the declared `>=3.12` floor but does not satisfy that exact-version gate.
- The existing isolated PG18 prefix reports PostgreSQL 18.6.

## Pending candidate gates

The coordinator will bind these checks to the clean candidate and use the local ZCode test executor and independent reviewer.

- `uv lock --check`, locked installation with dev and postgres extras on CPython 3.12.14, and the `Requires-Python` value in project, lock, and installed metadata.
- Offline locked installation refusal using the verified CPython 3.11.15 executable before resolution, download, or environment creation. Both installation attempts use uv 0.10.0.
- Focused `tests/test_project_postgres_session_bundle.py` results and the Native PG18 storage opt-in case `test_native_same_root_source_selection_rollback_and_refusals`, including full-file pass/skip counts.
- Full test suite, Python compile check, `git diff --check`, CodeGraph sync, and tracked-content secret scan.
- Independent read-only ZCode code review, PR creation, final PR-head CI, and `check_ci_merge.sh` status.

Record the native kit status against the final PR paths. The expected path-filter result is `NOT_TRIGGERED / kit proof 未运行`.
