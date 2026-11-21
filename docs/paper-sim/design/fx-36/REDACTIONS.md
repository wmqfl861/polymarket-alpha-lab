# FX-36 host address redactions

`SERVER_HOST_REDACTED_RETIRED` identifies the retired development host.
`SERVER_HOST_REDACTED` identifies its replacement in the historical records.
Use the locally configured alias for operational access.

## Git blob measurements

Original paths are pinned at `9a38b62827d35d524e05d71f0d2f780c16baa86c`.
The redacted measurements describe the Git tree of the commit that added this
index. Line numbers count from 1 in each measured blob.

| Repository path | Original lines | Original bytes | Original SHA-256 | Redacted lines | Redacted bytes | Redacted SHA-256 |
| --- | --- | ---: | --- | --- | ---: | --- |
| `DELIVERY_PLAN.md` | 1475, 1489, 2319, 2612, 2758, 2835, 2982, 2983, 3051 | 427849 | `ed59f6c4b53ff655568cf08d09fb4d4a99c13c5393ed4ccec365623362ca4ddc` | 1475, 1489, 2319, 2612, 2758, 2835, 2982, 2983, 3051 | 427975 | `135e72befb235fcf06f294d07685b1dffc7db6d91f682941d63442864b9d6dab` |
| `docs/handoffs/g2-activation-readiness.md` | 80, 284, 511, 675 | 52364 | `2893be3f78c4a00ffb01ef1dabcbab0d255368ba9e5505abd320ea51e3125f73` | 80, 284, 511, 675 | 52392 | `2d61afc27c8cf3fe14e40aa761b93a4e29483c3eb41a94819c77fb9b88cc9663` |
| `docs/handoffs/g2-first-real-run-runbook-draft.md` | 192 | 31601 | `ac32fbbc51c30b27a74abd67b0adc6017d03a82137cf2a0d4ac4b61ffd46ce27` | 192 | 31608 | `caf2558ac699a13ba8bc8d60f9a2a6ffa0efb1aad7bf64bd09d22079d4182a30` |
| `docs/handoffs/l7-l8-status.md` | 35, 72, 267 | 24329 | `0c443dc58f085eb500b6b94049f77608120439ccd1486237db721b841e486a2b` | 35, 72, 267 | 24377 | `665ee347ea0376ecaf7e1a6908e449cd8d8ff413aa866ca8d9ad567a2d61626f` |
| `docs/handoffs/l8-l9-status.md` | 28, 30, 64, 65, 201, 203, 424, 453 | 34704 | `685e34210c9118587d9809e24438e5408063b46054038ad5200898a6e0434bc9` | 28, 30, 64, 65, 201, 203, 424, 453 | 34787 | `5290caacfedd597c97c75d0a197c07bd13ab5f067e1be4eb1efdd4ba8b45d780` |
| `docs/releases/linux-preview-release.md` | 25, 549 | 29119 | `0828a38a31ff876a25b15667a2f5951d15e7dda2a1c0ac5e1c4b9be1c10bb319` | 25, 549 | 29151 | `006035d096905b1087d068a7a04acbfd06d275683dab0a420216df66390ef990` |
| `docs/releases/v0.1.0-linux-preview.1/handoff.md` | 18, 109 | 5876 | `31c936f4498b2822333f7ebb9a2b40605649fdf4c7f6ae5f653283451cafaa28` | 23, 114 | 6208 | `2ef5ad501886fc68269ec4b7d3f29fb4bc8930f4fd8a7cbe24c32de4d90c5d65` |
| `docs/releases/v0.1.0-linux-preview.1/release-manifest.json` | 85 | 4586 | `f2a97656f8671dddec0fbda535bd850f44fb325a7cdd88587ffbd162daa9ae82` | 85 | 4602 | `95d97bad28c89208da2b83565e912a886c468e5631e3fb521e635f6e6cbf563f` |
| `docs/reviews/model-review-2026-11-07.md` | 4 | 15188 | `51d2d87e8bc5ad45e9763eaba15b2c29665f23dec0d8a11de969b031c43d674a` | 4 | 15195 | `c91186a81f70db86e2da61061fc514d1586c06709c4e6e6e925e6c7c4974a5bb` |

## Linux preview release verification

The original release handoff and manifest are also pinned at
`c36a76c17ce271bb59fafab0eb44880cc65476ca`. The release's published
`SHA256SUMS` is 474 bytes with SHA-256
`427e527a2e989a4ba77d1c9f1c0fde2826ff74fde6a42f7c15b9c38e4eff628e`.
Use the handoff's download procedure and published checksums for the immutable
release assets. Use the redacted measurements above for the repository copies
of the handoff and manifest.

The example below verifies both Git copies of the manifest. Set
`fx36_path` to another table path to verify that file. The commands print
only the object hashes and byte counts.

```bash
fx36_original=9a38b62827d35d524e05d71f0d2f780c16baa86c
fx36_index=docs/paper-sim/design/fx-36/REDACTIONS.md
fx36_redacted=$(git log -1 --diff-filter=A --format=%H -- "$fx36_index")
fx36_path=docs/releases/v0.1.0-linux-preview.1/release-manifest.json
git cat-file blob "$fx36_original:$fx36_path" | sha256sum
git cat-file blob "$fx36_original:$fx36_path" | wc -c
git cat-file blob "$fx36_redacted:$fx36_path" | sha256sum
git cat-file blob "$fx36_redacted:$fx36_path" | wc -c
```
