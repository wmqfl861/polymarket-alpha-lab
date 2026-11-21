# Handoff: v0.1.0-linux-preview.1 — Linux engineering preview (final delivery)

This is the complete GitHub-first handoff for the §61/§62 Linux pivot
delivery. Everything referenced below is published and reachable at immutable
revisions. Companion narrative record: DELIVERY_PLAN.md sections 61-62.

The published SHA-256 values and byte counts below apply to the immutable
release downloads. The manifest pin also verifies its original Git blob.
Verify the redacted repository copies of this handoff and the manifest
against the [FX-36 redaction index](../../paper-sim/design/fx-36/REDACTIONS.md).

## Pinned identities

| Item | Value |
|---|---|
| Repository | https://github.com/Lordakee/polymarket-alpha-lab |
| Release (immutable prerelease) | https://github.com/Lordakee/polymarket-alpha-lab/releases/tag/v0.1.0-linux-preview.1 |
| Release ID | 398244797 (isDraft=false, isImmutable=true, isPrerelease=true) |
| Tag | `v0.1.0-linux-preview.1`, annotated object `d9fc6e0131da8499bed2eb4959ea3cd7d1b90b71`, peels to commit `06e49d0fde20c837cda80f0b3e61fcb1f3806ca2` (C) |
| C (release source commit) | `06e49d0fde20c837cda80f0b3e61fcb1f3806ca2` (tree `027996b04f44c0340deb8e73fcf9825ad061c657`) |
| E (publication-receipt commit) | `a1128a65ed09fce11d46b90782393af1e55c35fa` (tree `679894cac498275dcf89d00e87c8de77a2c2d7b9`) |
| Tuple | Ubuntu 26.04.1 x86_64 / kernel 7.0.0-34-generic / Python 3.12.14 / PostgreSQL 18.6 (Ubuntu build 18.6-0ubuntu0.26.04.1) |
| Build/acceptance host | ubuntu@SERVER_HOST_REDACTED_RETIRED (designated) |

## Published assets (six, immutable)

| Asset | sha256 | bytes |
|---|---|---|
| polymarket-alpha-lab-v0.1.0-linux-preview.1-linux-x86_64.zip | a0c9a014e4efdfa5215843af84120831457e27e9e319045b5e365e660aea6ce9 | 40,813,935 |
| PROJECT-BUNDLE.json | 12594f09e5149c91c7132eed7191ed6060baf8615683aaf52fcde53ccd5f396c | 424,902 |
| acceptance-evidence.zip | 9825a48273b8e8d3687cf6c819cd3ec86b6d8514436a6dc038da177b37328558 | 25,419 |
| release-manifest.json | f2a97656f8671dddec0fbda535bd850f44fb325a7cdd88587ffbd162daa9ae82 | 4,586 |
| release-notes.md | c2ebe66bd247a8838ec2e51e0d1c9ceb1444019e05da42494e156a92e8b3334d | 3,257 |
| SHA256SUMS | 427e527a2e989a4ba77d1c9f1c0fde2826ff74fde6a42f7c15b9c38e4eff628e | 474 |

The kit embeds the PostgreSQL 18.6 engine as
`database/postgres-runtime.zip` (engine digest
`d7c09eebab2a27f33f677452447e28cc928c42b88467af73295b939d0ac418e4`).
A database cluster, Python, a venv and the offline dependency collection are
not bundled; `uv.lock` is bundled.

## Download and verify (Linux)

```bash
set -euo pipefail
TAG=v0.1.0-linux-preview.1
REPO=Lordakee/polymarket-alpha-lab
BASE="https://github.com/$REPO/releases/download/$TAG"
mkdir -p ~/pal-preview && cd ~/pal-preview
for f in polymarket-alpha-lab-v0.1.0-linux-preview.1-linux-x86_64.zip \
         PROJECT-BUNDLE.json acceptance-evidence.zip release-manifest.json \
         release-notes.md SHA256SUMS; do
  curl --fail --location --retry 3 -O "$BASE/$f"
done
# Trust ONLY the SHA256SUMS published in this immutable release record.
sha256sum --ignore-missing -c SHA256SUMS
```

Expected: six `OK` lines. A hash mismatch means a corrupted or tampered
download — stop and re-download; do not proceed.

## Install and run (from the kit, offline)

```bash
set -euo pipefail
cd ~/pal-preview
KIT=polymarket-alpha-lab-v0.1.0-linux-preview.1-linux-x86_64.zip
sha256sum --ignore-missing -c SHA256SUMS          # re-verify the archive
mkdir -p ~/pal-v0.1.0 && unzip -q "$KIT" -d ~/pal-v0.1.0
cd ~/pal-v0.1.0/polymarket-alpha-lab
uv sync --locked --offline --python 3.12.14       # uses the bundled uv.lock
# Engine install from the embedded archive, then initialize a PRIVATE root:
.venv/bin/python -I scripts/project_database.py --root "$PWD" \
  install-runtime --archive database/postgres-runtime.zip
.venv/bin/python -I scripts/project_database.py --root "$PWD" init
.venv/bin/python -I scripts/project_database.py --root "$PWD" status
```

Every `project_database.py` invocation above exits non-zero on failure; check
`$?` after each. Full operator guidance: `database/quickstart.md` in the kit
(the §58 eight-step source/root switch procedure is preserved there). The
project runs `paper_only=True, report_only=True, readonly=True`; evidence
persists only to the project-private native PostgreSQL.

## Acceptance limits and stop conditions

- This is an **engineering prerelease**. It completes L6's
  durable-publication and fixed-version engineering subconditions only.
- **G2-G6 remain open**: official Claude Linux artifact provenance and byte
  measurement unmeasured; the six official probe scenarios (success,
  rate_limit, server_error, invalid_action, tool_use, truncated) are 0/6;
  G2 real BTC/ETH research execution requires explicit owner authorization;
  `activation_authorized=false`; real provider execution false.
- Not V1 completion, not statistical strategy validation, not execution
  authorization. Windows remains retired and frozen.
- Stop conditions for the operator: any SHA256 mismatch; any
  `project_database.py` non-zero exit; any unexpected state after `init`
  (never re-run `init` over an existing root — the CLI refuses and that
  refusal is correct); never restore over existing data.
- Correction policy: yank-and-renumber — a dated "do not use" advisory is
  appended; the original release, tag and assets are never deleted or
  rewritten (the repository now enforces immutable releases).

## Evidence index

- DELIVERY_PLAN.md §62 (repo at E): complete delivery record including the
  three-round frozen-asset review loop (FAIL/FAIL/PASS, verdicts external),
  the first-failure records (build attempt 1 timing; first public download
  truncation), and all acceptance numbers.
- acceptance-evidence.zip: distribution/switch/containment/PG proofs,
  tuple-prefix provenance (1,468-line inventory, ldd, binary digests),
  first failures preserved separately.
- Release runbook: docs/releases/linux-preview-release.md (repo at C).
- Server evidence root: ubuntu@SERVER_HOST_REDACTED_RETIRED:~/pal-artifacts/
  (verify-full-C-06e49d0f.log, release-v0.1.0-linux-preview.1/,
  release-v0.1.0-linux-preview.1-attempt1-FAILED-timing/).
