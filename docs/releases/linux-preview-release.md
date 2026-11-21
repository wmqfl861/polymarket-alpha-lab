# Linux engineering preview release runbook (L6, tag `v0.1.0-linux-preview.1`)

Maintainer runbook for the L6 durable publication: build, accept and publish
the Linux engineering preview as an immutable GitHub prerelease from the exact
ZIP bytes the native distribution test accepted on host 166. **Status: PENDING
— no release, tag, draft or asset exists yet.** This document records the
procedure and the pending publication facts only; it is preparation evidence,
not publication evidence. The concrete facts are recorded later in commit `E`
and DELIVERY_PLAN.md section 62 (section 17 lists every pending placeholder).

Completing this runbook closes only the L6 durable-publication and
fixed-version engineering subconditions. **It cannot close G6 or V1 while
G2–G5 remain incomplete**; the exact required wording is in section 16.

No publishing workflow is added for L6. `native-distribution.yml` stays a
read-only build/test workflow with its existing seven-day Actions artifact
retention, and every essential acceptance artifact is copied into the durable
Release before its Actions copy expires.

## 1. Inputs and identity

| Input | Value |
| --- | --- |
| Repository | `wmqfl861/polymarket-alpha-lab` — canonical per OD-C (DELIVERY_PLAN.md §77.3.1) and the section 19 correction; `--repo` is always explicit; the section 2 clone makes local `origin` point there; `Lordakee/polymarket-alpha-lab` is the historical source, currently unverifiable |
| Build host | `ubuntu@SERVER_HOST_REDACTED_RETIRED` (shared, dev-only) |
| Release tuple | Ubuntu 26.04.1 x86_64 / Python 3.12.14 / one precisely identified PostgreSQL 18 build (DELIVERY_PLAN.md section 61) |
| Proposed tag | `v0.1.0-linux-preview.1` — subject to the absence check in section 11 |
| Kit filename | `polymarket-alpha-lab-v0.1.0-linux-preview.1-linux-x86_64.zip` |
| Frozen PG18 prefix | the approved qualified package-to-prefix assembly (`~/pg-runtime-src`, section 3); it must not change between the final full suite, the decisive build and the fixed-pair proof |
| `C` | the final, clean, merged source commit on `main` after L5 and the L6 preparation merges; `T` is its full Git tree |
| `E` | the later documentation commit recording publication and download verification; the release tag continues to identify `C` |

Neither an earlier commit (for example `6326101c` or `e8d2457e`) nor an
existing L2 proof ZIP automatically becomes the release input. Capture `C`
only after the final merges and cleanliness checks, from clean `main`. The
release publishes the exact ZIP produced by the final-commit native
distribution test; a separate "publication copy" is never built afterwards and
`scripts/build_project_bundle.py` is not invoked separately for this release.

## 2. Checkout and environment sanitization on host 166

Build and test on 166. Publication may use the coordinator's already-authorized
GitHub session; verify transferred asset hashes and sizes before upload,
without provisioning or copying credentials onto the shared host.

```bash
set -euo pipefail
REPO='wmqfl861/polymarket-alpha-lab'
TAG='v0.1.0-linux-preview.1'
KIT='polymarket-alpha-lab-v0.1.0-linux-preview.1-linux-x86_64.zip'
PREFIX="$HOME/pg-runtime-src"          # frozen, approved PG18 prefix
OUT=                                   # a NEW directory OUTSIDE any checkout
C=                                     # final merged source commit (full sha)
mkdir -p "$OUT" && [ "$(ls -A "$OUT")" ] && { echo 'OUT must be empty' >&2; exit 1; }

git clone "https://github.com/wmqfl861/polymarket-alpha-lab.git" "$HOME/release-checkout"
cd "$HOME/release-checkout"
git rev-parse --verify "$C^{commit}"          # must print exactly $C
git checkout --detach "$C"
git status --porcelain                        # must be empty
git ls-remote origin refs/heads/main          # read back: origin/main must resolve to $C
```

Sanitize inherited PostgreSQL/Python/pytest overrides before every pytest
invocation in this runbook:

```bash
for variable in $(compgen -e PG); do unset "$variable"; done
unset PYTEST_ADDOPTS PYTEST_PLUGINS PYTHONPATH PYTHONHOME
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONUTF8=1
export POLYMARKET_ALPHA_LAB_RUN_SUPABASE_SMOKE=0
```

Install the locked environment for the tuple's interpreter:

```bash
uv sync --locked --extra dev --extra postgres --python 3.12.14
.venv/bin/python --version    # must report Python 3.12.14
```

## 3. Tuple and prefix provenance preflight

The builder enforces Linux/x86_64 and PostgreSQL major 18, not the full
section 61 tuple; exact-tuple checks therefore belong here, in the release
preflight. Record all of the following into `$OUT/tuple-provenance.txt`:

```bash
{
  cat /etc/os-release
  uname -m
  uname -r
  .venv/bin/python -VV
  readlink -f "$(command -v python3)"
  dpkg-query -W -f='${Package} ${Version}\n' 'postgresql-18*' || true
  "$PREFIX/bin/postgres" --version
  find "$PREFIX" -maxdepth 2 -type f | sort
  sha256sum "$PREFIX/bin/postgres" "$PREFIX/lib/postgresql/18/lib/libpq.so"* 2>/dev/null || true
} > "$OUT/tuple-provenance.txt" 2>&1
```

A package version queried from the host is insufficient unless it is tied to
the actual prefix bytes: record how the prefix was assembled (the qualified
package-to-prefix assembly) and bind the recorded package version to the
prefix inventory above. The prefix assembly uses `cp -aL` dereferencing plus
the residual-symlink rejection guard (commit `b692e2d0`):

```bash
find "$PREFIX" -type l -ls                       # evidence line; must print nothing
test "$(find "$PREFIX" -type l | wc -l)" -eq 0   # the prefix contract is symlink-free
```

Also record the required system libraries observed by the ELF loader (for
example the `ldd "$PREFIX/bin/postgres"` resolved non-prefix libraries).
**Missing provenance or a changed prefix stops the release.**

## 4. Final full suite and focused checks

Run the final full suite, which includes compilation:

```bash
.venv/bin/python scripts/verify_local.py --full
```

`verify_local.py` disables the native opt-ins; it does not replace the
separately dispatched native acceptance in sections 5 and 7. Complete the
focused checks for the changed surface, `git diff --check`, the secret scan,
and the CodeGraph sync (or its recorded blockage) per the section 61 node
requirements, all at `C` before proceeding.

## 5. The decisive native distribution invocation

The existing native distribution acceptance test calls `build_distribution`
once, prints the build receipt, then tests two extractions of that same output
through native startup, restart, packaged synthetic research, draining,
recovery and integrity refusals. It — not a rebuild — produces the release
ZIP at `$OUT/$KIT`:

```bash
set -euo pipefail

POLYMARKET_ALPHA_LAB_RUN_NATIVE_DISTRIBUTION=1 \
POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX="$PREFIX" \
POLYMARKET_ALPHA_LAB_DISTRIBUTION_OUTPUT="$OUT/$KIT" \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONUTF8=1 \
.venv/bin/python -u -m pytest -q -s \
  tests/test_project_postgres_distribution_native.py \
  --junitxml="$OUT/distribution-proof.xml" \
  2>&1 | tee "$OUT/distribution-proof.log"

sha256sum "$OUT/$KIT"
stat --printf='%s\n' "$OUT/$KIT"
```

`PREFIX` is the approved frozen PG18 prefix, `OUT` is a new directory outside
the checkout, and the ZIP is never rebuilt, re-zipped or "repaired" after this
step.

## 6. Post-test measurement, builder-receipt comparison and bundle identity

Independently compare the post-test hash and size printed above with the
builder receipt embedded in `$OUT/distribution-proof.log` (the JSON object
with `status='built'`, `source_commit`, `source_tree`, `postgres_version`,
`file_count`, `archive_sha256`, `archive_bytes`, `database_included`,
`python_included`). Record:

```bash
KIT_SHA256=$(sha256sum "$OUT/$KIT" | cut -d' ' -f1)
KIT_BYTES=$(stat --printf='%s\n' "$OUT/$KIT")
printf 'kit_sha256=%s\nkit_bytes=%s\n' "$KIT_SHA256" "$KIT_BYTES" | tee "$OUT/kit-measurement.txt"
```

Then verify the embedded manifest identifies the release inputs:

```bash
unzip -p "$OUT/$KIT" polymarket-alpha-lab/PROJECT-BUNDLE.json | tee "$OUT/PROJECT-BUNDLE.extracted.json"
```

Check inside it: `source_commit` equals `C`, `source_tree` equals `T`, the
expected PostgreSQL 18 version string, the selected source file hashes, and
the engine archive digest. Any mismatch stops the release.

## 7. Fixed-pair source-version proof

The existing version-switch test pins OLD commit
`2b20002c83ee33acb95fb2ca3de841f3e883ebbf` / tree
`d6f6750b1068a3c75ab6249d5ca4f1ef7e010ee4`; the candidate side is the
integrated HEAD, i.e. `C` / `T`. Run it explicitly on 166 with the same
qualified frozen PG18 prefix and the native opt-in:

```bash
set -euo pipefail
for variable in $(compgen -e PG); do unset "$variable"; done
env -u PYTEST_ADDOPTS -u PYTEST_PLUGINS -u PYTHONPATH -u PYTHONHOME \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONUTF8=1 \
  POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX="$PREFIX" \
  POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES=1 \
  .venv/bin/python -u -m pytest -q -s -o junit_family=legacy \
  tests/test_project_postgres_version_switch_native.py \
  --junitxml="$OUT/version-switch-proof.xml" \
  2>&1 | tee "$OUT/version-switch-proof.log"
```

Retain the resolved revisions, catalog fingerprints, runtime identity and
outcomes. Describe the combined evidence as **"a fixed source-version pair
plus one released candidate kit."** The switch test uses verified Git
archives; its synthetic bundle markers are integrity-test fixtures. It does
not prove a pair of published Linux kits. Bind its candidate source to the
release ZIP through `C`, `T` and the selected-file manifest.

## 8. CI guards

All guards must hold at `C` before asset assembly. Every cited run must
resolve to that exact source revision (record workflow, run URL, run attempt
and `github.sha`); dispatch the native workflows explicitly when path filters
do not schedule them.

Required named jobs:

- `offline` job of the "Offline verification" workflow
  (`.github/workflows/offline-verification.yml`) — the sanitized full offline
  verification with tracked-file cleanliness.
- `linux-kit` job of the "Native project distribution" workflow
  (`.github/workflows/native-distribution.yml`) — corroborating CI only: it
  uses Ubuntu 24.04, `--python 3.12` and unversioned `apt-get install
  postgresql-18`, so its artifact does **not** establish the designated
  release tuple.
- The `linux-native-part` matrix partitions `storage`, `research` and
  `dispatch` of the "Native project PostgreSQL" workflow
  (`.github/workflows/native-postgres.yml`).
- The `linux-native` aggregate job and its "Require every native partition"
  step — success required; a skipped, cancelled or failed partition is not
  acceptance and there is no success fallback.
- The storage partition's "Prove native version switch" step.

Retired Windows jobs (`windows-kit`, `windows-native-part`, `windows-native`,
`windows-paper`) are recorded as retired frozen history per the section 61
controlled CI transition; no new Windows green is required or accepted.

Additional guard conditions:

- Required native test cases are present and executed. Pytest exit zero with
  a required case skipped is insufficient. Expected offline opt-in skips
  remain separately classified.
- L5 containment runs explicitly enable
  `POLYMARKET_ALPHA_LAB_RUN_LINUX_CONTAINMENT=1`; the relevant native audit
  combination also enables `POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES=1`.
  Neither the normal full-suite wrapper nor the inspected workflows supplies
  the containment flag, so those runs are dispatched explicitly.
- Copy every essential acceptance artifact out of Actions (seven-day
  retention) into `$OUT` before assembling evidence.

## 9. Release asset assembly

Assemble exactly six assets, in this acyclic dependency order:
payloads/evidence → manifest → notes → checksums.

1. `$OUT/$KIT` — the unchanged ZIP accepted on 166 (already present; never
   rebuilt).
2. `PROJECT-BUNDLE.json` — a byte-for-byte copy of the manifest inside that
   ZIP:

   ```bash
   unzip -p "$OUT/$KIT" polymarket-alpha-lab/PROJECT-BUNDLE.json > "$OUT/PROJECT-BUNDLE.json"
   cmp "$OUT/PROJECT-BUNDLE.extracted.json" "$OUT/PROJECT-BUNDLE.json"   # must be identical
   ```

3. `acceptance-evidence.zip` — an allowlisted, reviewed collection of build
   receipts, logs, JUnit results, tuple/prefix measurements, fixed-pair
   results and prior applicable reviews. Preserve first failures, internal
   retries, reruns, skipped and unexecuted steps as separate, labeled items;
   never collapse them into the successful attempt. Exclude clusters,
   backups, `.local`, generated credentials, private logs and the official
   Claude image. Record sanitized exports accurately; never describe an
   edited export as the original log.

   ```bash
   # Stage only allowlisted, reviewed items, then:
   (cd "$EVIDENCE_STAGING" && zip -X -r "$OUT/acceptance-evidence.zip" .)
   ```

4. `release-manifest.json` — source identity, tuple, dependency
   restrictions, asset SHA256 values and byte sizes, CI references,
   fixed-pair identity and acceptance limits. It covers the kit ZIP, the
   `PROJECT-BUNDLE.json` copy and `acceptance-evidence.zip`, and **excludes
   itself, `release-notes.md` and `SHA256SUMS`**:

   ```bash
   cd "$OUT"
   BUNDLE_SHA256=$(sha256sum PROJECT-BUNDLE.json | cut -d' ' -f1)
   BUNDLE_BYTES=$(stat --printf='%s\n' PROJECT-BUNDLE.json)
   EVIDENCE_SHA256=$(sha256sum acceptance-evidence.zip | cut -d' ' -f1)
   EVIDENCE_BYTES=$(stat --printf='%s\n' acceptance-evidence.zip)
   ```

   Write `release-manifest.json` with those measured values plus `C`, `T`,
   the exact tuple and PG provenance from section 3, the locked dependency
   identity (`uv.lock` at `C`; Python, a venv and an offline dependency
   collection are not bundled), the section 8 CI references, the fixed pair
   (OLD `2b20002c`/`d6f6750b`, candidate `C`/`T`) and the section 16
   acceptance limits.

5. `release-notes.md` — the exact normal GitHub Release body; it states the
   release facts listed for section 62 in section 15 and matches the
   partial-acceptance wording of section 16.

6. `SHA256SUMS` — checksums of every **other** published asset; it excludes
   itself. Its own SHA256 and byte size are pinned externally in DELIVERY_PLAN
   section 62 and the final review record:

   ```bash
   cd "$OUT"
   : > SHA256SUMS
   for asset in "$KIT" PROJECT-BUNDLE.json acceptance-evidence.zip release-manifest.json release-notes.md; do
     sha256sum "$asset" >> SHA256SUMS
   done
   sha256sum SHA256SUMS && stat --printf='%s\n' SHA256SUMS   # retain externally
   ```

These are static release-provenance files. Research records remain
PostgreSQL-only.

## 10. Self-review and independent frozen-asset review

Complete a separately labeled self-review of the assembled assets. Then a
fresh independent reviewer — one with no role in planning, implementing or
self-reviewing this change — approves the **already-frozen release assets and
body** (the exact bytes in `$OUT`). The review is read-only, ends with an
explicit `VERDICT: PASS`/`VERDICT: FAIL` line, and **its verdict stays
outside those assets** (it is reported in the final handoff and recorded in
DELIVERY_PLAN section 62, never inside `acceptance-evidence.zip`). Changing
any asset afterward invalidates that review.

## 11. Absence and immutability prerequisites; tag, draft, publication

First verify the proposed tag and Release do not already exist. A failed API
request is not evidence of absence, so both checks are recorded:

```bash
git ls-remote --tags origin "refs/tags/$TAG"   # must print nothing
gh release view "$TAG" --repo "$REPO"          # must fail as not found
```

**Require GitHub release immutability to be enabled and verified before
publication.** `gh` 2.96.0 help documents that this repository setting locks
published release tags and assets. Verify the actual repository setting (the
repository's release settings, or the API view of the repository), record the
verification method and result, and require the post-publication readback
`isImmutable=true` below. **If immutability is unavailable or cannot be
verified, publication stops.** A write-once naming convention alone does not
satisfy the immutable-release requirement.

The trigger for this section is one coordinator invocation after the frozen
source and asset reviews pass. After all guards and the final asset review
pass, publish with:

```bash
git tag -a "$TAG" "$C" \
  -m "Linux engineering preview; source=$C tree=$T kit_sha256=$KIT_SHA256"
git push origin "refs/tags/$TAG:refs/tags/$TAG"

gh release create "$TAG" \
  "$OUT/$KIT" \
  "$OUT/PROJECT-BUNDLE.json" \
  "$OUT/acceptance-evidence.zip" \
  "$OUT/release-manifest.json" \
  "$OUT/release-notes.md" \
  "$OUT/SHA256SUMS" \
  --repo "$REPO" --verify-tag --draft --prerelease --latest=false \
  --title "$TAG — Linux research/paper engineering preview" \
  --notes-file "$OUT/release-notes.md"
```

## 12. Draft verification, publication, and public readback

Download every draft asset into a separate new empty directory and verify the
exact asset allowlist (exactly the six names, no others), asset IDs, sizes,
checksums, manifest bindings, tag target and body text against the frozen
local copies. Verify the checksum file itself against its independently
retained measurement from section 9:

```bash
DRAFT="$OUT/../draft-verification-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DRAFT"
gh release download "$TAG" --repo "$REPO" --dir "$DRAFT"
ls -A "$DRAFT"    # exactly the six assets
for asset in "$KIT" PROJECT-BUNDLE.json acceptance-evidence.zip release-manifest.json release-notes.md SHA256SUMS; do
  cmp "$OUT/$asset" "$DRAFT/$asset"    # byte identity with the frozen copy
done
(cd "$DRAFT" && sha256sum -c SHA256SUMS)
gh release view "$TAG" --repo "$REPO" --json body,tagName,isDraft   # body == release-notes.md bytes, isDraft == true
```

Only then publish:

```bash
gh release edit "$TAG" --repo "$REPO" --verify-tag \
  --draft=false --prerelease --latest=false

gh release view "$TAG" --repo "$REPO" \
  --json isDraft,isImmutable,isPrerelease,tagName,assets,url
```

Require `isDraft=false`, `isImmutable=true` and `isPrerelease=true`. Download
all published assets again into another new empty directory and repeat the
byte, size and identity checks above. Re-peel the remote tag to `C`:

```bash
VERIFY="$OUT/../public-verification-$(date -u +%Y%m%dT%H%M%SZ)"
git clone --no-checkout "https://github.com/wmqfl861/polymarket-alpha-lab.git" "$VERIFY/clone"
git -C "$VERIFY/clone" cat-file -t "$TAG"                  # must be 'tag' (annotated)
git -C "$VERIFY/clone" rev-parse "$TAG^{commit}"           # must print exactly $C
git ls-remote --tags origin "refs/tags/$TAG"               # record the tag object id
```

## 13. Failure handling

Never use force-push, `--clobber`, `--skip-existing`, asset replacement or
tag reuse anywhere in this procedure. On a partial failure:

1. Stop. Do not blindly retry and do not recycle an allocated version
   identifier.
2. Inspect the existing remote state (tag refs, draft release, uploaded
   assets) and record what actually exists before taking another action.
3. Retain the failure record and all partial state — the pushed tag, the
   draft release, `$OUT` with its logs and measurements — for inspection.

A failed guard before publication retains the draft/tag and failure evidence
for inspection. An unverifiable immutability setting stops publication
entirely.

## 14. Yank policy after publication

The normal correction is **yank, preserve, replace with a new version**:

- Append a dated, separately reviewed "do not use" advisory to DELIVERY_PLAN
  section 62 and the Release body.
- Retain the original release facts, tag, assets and hashes unchanged.
- Issue a newly numbered preview (for example `v0.1.0-linux-preview.2`) only
  after full re-verification through this runbook.

Ordinary defects do not justify deleting audit history. Exceptional
sensitive-content removal requires a separate incident decision. A release
correction does not authorize changing an installed database, restoring over
existing data, or activating a provider.

## 15. Ledger commit `E`, DELIVERY_PLAN section 62 and handoff

After successful publication readback, commit the actual receipt as `E`,
including byte-identical copies under:

```text
docs/releases/<TAG>/release-manifest.json
docs/releases/<TAG>/release-notes.md
docs/releases/<TAG>/SHA256SUMS
```

DELIVERY_PLAN section 62 additionally records: release classification,
repository, tag, full source commit and tree; the exact tuple, PG provenance
and runtime inventory identity; ZIP filename, SHA256 and byte size; internal
manifest and engine identities; locked dependency and migration-catalog
identities and required system libraries; final CI run URLs, job identities,
source revisions and run attempts; host acceptance commands, results,
log/JUnit references and artifact measurements; L5's synthetic status, exact
revision and containment evidence; the fixed source-version pair and the
precise limits of that evidence; the tag object ID, Release and asset IDs,
fixed download URLs, public-readback results, checksum-file measurement and
the external final asset-review verdict; remaining G2–G6 requirements,
official probe status and activation boundary; and first failures, retries,
skipped and unexecuted checks without collapsing them into the successful
attempt. Large accepted evidence assets stay on the immutable Release, not in
Git.

Give `E` its own fresh independent final-revision review before delivery.
That review's verdict remains external to `E`, reported in the final handoff.
The normal Release body stays identical to the reviewed `release-notes.md`;
do not rebuild or retag `C` to incorporate `E`. If the `E` stage fails, retain
the Release and report **ledger/handoff incomplete**. Deliver the complete
GitHub-first handoff only after `E` is reachable and verified, pinning `C`,
`E`, the tag, all required paths and measurements, exact download commands,
separate installation/use commands, acceptance limits and stop conditions.

## 16. Required acceptance-limits wording

After all publication and ledger checks succeed, DELIVERY_PLAN section 62 and
the frozen Release body must state wording equivalent to:

> **L6 durable-publication result:** The Linux engineering preview `<TAG>`,
> built from source `<C>` / tree `<T>`, passed the recorded acceptance on
> Ubuntu 26.04.1 x86_64 / Python 3.12.14 / `<exact PG18 build>`. The exact
> accepted ZIP was published as an immutable GitHub prerelease and downloaded
> again with matching SHA256 and byte size. The recorded OLD↔C proof
> establishes fixed-source-version engineering behavior on disposable
> PostgreSQL data.
>
> This closes the durable-artifact and recorded fixed-version engineering
> subconditions only. **L6 final acceptance and WP-06/G6 remain PARTIAL**,
> because section 61 requires G1–G5 and final Linux evidence before G6 can
> close.
>
> Official Claude Linux artifact provenance, byte measurement and version
> qualification remain open. The six official probe scenarios — success,
> rate_limit, server_error, invalid_action, tool_use and truncated — remain
> **0/6**. L5 synthetic containment evidence does not satisfy those
> official-artifact checks.
>
> G2 still requires explicitly authorized real research for both BTC and
> ETH. G3 still requires the final qualifying Linux client
> operation/recovery evidence. G4 requires real prospective predictions
> through independently verified settlement; G5 requires the complete real
> engineering samples. Real provider execution remains false and
> `activation_authorized=false`.
>
> G1 remains DONE; G2–G6 remain open. This prerelease is not V1 completion,
> statistical strategy validation or execution authorization. Windows remains
> retired and frozen.

Before execution, DELIVERY_PLAN section 62 must say **pending**, with no
invented hashes, results or publication URLs.

## 17. Pending publication facts (placeholder inventory)

Every concrete value below belongs to the release record and is
`<to-be-recorded-at-publication>` until commit `E` exists. Nothing in this
runbook invents them:

| Fact | Value |
| --- | --- |
| Final source commit `C` / tree `T` | `<to-be-recorded-at-publication>` |
| Exact pinned PostgreSQL 18 build (package + version, bound to prefix bytes) | `<to-be-recorded-at-publication>` |
| Kit ZIP SHA256 / byte size | `<to-be-recorded-at-publication>` |
| `acceptance-evidence.zip` SHA256 / byte size | `<to-be-recorded-at-publication>` |
| `release-manifest.json` SHA256 / byte size | `<to-be-recorded-at-publication>` |
| `release-notes.md` SHA256 / byte size | `<to-be-recorded-at-publication>` |
| `SHA256SUMS` own SHA256 / byte size (pinned externally) | `<to-be-recorded-at-publication>` |
| Final CI run URLs, job identities, run attempts at `C` | `<to-be-recorded-at-publication>` |
| Fixed-pair proof results and retained log paths | `<to-be-recorded-at-publication>` |
| Tag object ID; Release ID; asset IDs; fixed download URLs | `<to-be-recorded-at-publication>` |
| Draft and public readback results | `<to-be-recorded-at-publication>` |
| External final asset-review verdict reference | `<to-be-recorded-at-publication>` |
| Ledger/documentation commit `E` | `<to-be-recorded-at-publication>` |

## 18. Directly forwardable Linux prompt

When this runbook is executed through a local agent on 166, forward the
following; it is the complete objective, and this document is the procedure:

> You are executing the reviewed L6 release runbook
> `docs/releases/linux-preview-release.md` in `wmqfl861/polymarket-alpha-lab`
> on `ubuntu@SERVER_HOST_REDACTED_RETIRED` (release tuple Ubuntu 26.04.1 x86_64 / Python
> 3.12.14 / pinned PostgreSQL 18). Follow its sections in order: sanitize the
> environment, verify the frozen PG18 prefix and tuple provenance, run
> `.venv/bin/python scripts/verify_local.py --full`, run the decisive native
> distribution invocation exactly as written in section 5, then the
> fixed-pair proof in section 7, then the CI guards in section 8, then asset
> assembly in the fixed acyclic order. Already completed and not to be
> repeated: the L5 merges and the L6 preparation changes are in `C`. You must
> not: rebuild or "repair" the accepted ZIP, add a publishing workflow, force
> anything, use `--clobber`/`--skip-existing`, replace assets, retag, or
> recycle the version identifier. Stop and retain partial state plus the
> first failure on any failed guard, unverifiable immutability, or any hash,
> size, identity or tuple mismatch. Publication proceeds only through the
> exact section 11–12 command sequence with
> `--repo wmqfl861/polymarket-alpha-lab`, `--verify-tag`, draft first, and
> requires `isImmutable=true`. Handle no credentials beyond the coordinator's
> already-authorized session; never copy credentials onto the shared host.
> Report the exact commands, exit codes, receipts, measurements and stop
> points; keep first failures, retries, skipped and unexecuted steps
> separately labeled.

## 19. Correction (2026-11-16): canonical repository

**Correction (2026-11-16, codex M3 decision 3):** The canonical repository for
this runbook is `wmqfl861/polymarket-alpha-lab`. `Lordakee/polymarket-alpha-lab`
is the historical source repository and its current state is **not verifiable**
(access currently fails; that failure does not prove the old repository or its
contents no longer exist). This implements owner decision OD-C recorded in
DELIVERY_PLAN.md section 77.3.1 ("wmqfl861 为正本") and the codex M3 decision
receipt of 2026-11-16; it corrects the outdated repository guidance above
without rewriting history.

Scope of this correction:

- Current operational guidance now targets the canonical repository only:
  `REPO` (section 2), both `git clone` URLs (sections 2 and 12), and the
  explicit `--repo` value in the forwardable prompt (section 18) all point to
  `wmqfl861/polymarket-alpha-lab`.
- Commands that rely on `origin` (sections 2, 11 and 12) inherit the canonical
  target only through the section 2 clone. In any pre-existing checkout,
  verify the actual remote target (`git remote -v`) before using `origin`;
  never assume the local remote has switched because this document changed.
- Historical facts are unchanged: every recorded commit, tree, tag, URL and
  checksum value in this runbook and in DELIVERY_PLAN.md stays exactly as
  recorded. No release has been published under this runbook yet (status
  PENDING), so there is no publication record to amend.
- Dual-repository comparison no longer conditions delivery: push verification
  compares the canonical repository's target refs against the expected
  delivery SHAs only. The historical repository's current tip and any
  divergence are recorded as "not verifiable" until access is restored; any
  future comparison is read-only. This correction does not authorize
  overwriting, deleting or force-pushing the historical repository.
