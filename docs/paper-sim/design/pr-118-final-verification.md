# PR #118 最终头核验

| 对象 | 完整提交 | 父序 |
| --- | --- | --- |
| 规范基线 B | `01a3d6d208fb7a22886f3cc0b54f4b6499d83400` | |
| 本机实质候选 S | `a3cfc09ed0cf6fae88845500fc6ddf2155aafb7c` | `f09850ba8ef010895d6a959cbeff401fbc706363`、`01a3d6d208fb7a22886f3cc0b54f4b6499d83400` |
| PR 最终头 H | `b89120f2fa79c6ecf5f371376fc00f1ccd10809c` | `a3cfc09ed0cf6fae88845500fc6ddf2155aafb7c` |
| GitHub 当前合并检出 M | `6c7f9e7726ffcc6d2b026ad4433df4a1c52d949e` | `01a3d6d208fb7a22886f3cc0b54f4b6499d83400`、`b89120f2fa79c6ecf5f371376fc00f1ccd10809c` |

B 是 H 的祖先，H 与 M 的树均为 `e7b2335c0f9baea51a774eacc709f878b0875ab9`。取证于 `2026-11-22T01:12:01.710919+00:00` 完成。
[固定助手的 CURRENT 回执](pr-118-final/current.txt) 将 Offline 运行绑定到该基线。S 保留本机验收，远端三组运行登记在 H/M。

| 工作流 | Run | Attempt | 事件 | 结论 | 创建 UTC | 启动 UTC | 更新 UTC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Offline verification | 44968419459 | 1 | `pull_request` | `success` | `2026-11-22T00:48:33Z` | `2026-11-22T00:48:33Z` | `2026-11-22T01:04:01Z` |
| Native project PostgreSQL | 44968419447 | 1 | `pull_request` | `success` | `2026-11-22T00:48:33Z` | `2026-11-22T00:48:33Z` | `2026-11-22T01:11:18Z` |
| Research paper bridge | 44968419472 | 1 | `pull_request` | `success` | `2026-11-22T00:48:33Z` | `2026-11-22T00:48:33Z` | `2026-11-22T00:54:28Z` |

| 实测作业 | 作业 ID | 实际检出 | 必需步骤 | 测试计数 |
| --- | --- | --- | --- | --- |
| `offline` | 137042576191 | `6c7f9e7726ffcc6d2b026ad4433df4a1c52d949e` | Run sanitized full offline verification `success` | 43950 passed, 438 skipped, 20 warnings |
| `linux-native-storage` | 137042576229 | `6c7f9e7726ffcc6d2b026ad4433df4a1c52d949e` | Prove isolated native partition `success`、Prove native version switch `success` | 320 passed, 2 skipped · 89 passed |
| `linux-native-research` | 137042576378 | `6c7f9e7726ffcc6d2b026ad4433df4a1c52d949e` | Prove isolated native partition `success` | 1227 passed, 18 skipped |
| `linux-native-dispatch` | 137042576516 | `6c7f9e7726ffcc6d2b026ad4433df4a1c52d949e` | Prove isolated native partition `success` | 1933 passed, 15 skipped |
| `linux-native-papersim` | 137042576448 | `6c7f9e7726ffcc6d2b026ad4433df4a1c52d949e` | Prove isolated native partition `success`、Assemble throwaway papersim cluster `success` | 845 passed |
| `windows-paper` | 137042576204 | `6c7f9e7726ffcc6d2b026ad4433df4a1c52d949e` | Prove research-to-paper assembly on an isolated native instance `success` | 357 passed |

Native 的四个 Linux 分区及汇总作业 `linux-native`（137048234329）均为 `success`。
Native 退役的 Windows 项为 `windows-native-${{ matrix.partition }}`（137042577336）`skipped`、`windows-native`（137042577630）`skipped`。

Native papersim 的 JUnit artifact `14733306892` 记录 D1 8 项通过，registered runner 75 项通过。原 XML 为 334188 字节，SHA-256 为 `e2960d1e206f267e66bcaa5f5330d92e9619055a65c420ad08d5fc3f5709cb00`。[JUnit 核验回执](pr-118-final/native-papersim-junit.json)

## 独立只读复审

H 推送前会话 `sess_8bbe96ea-b2e7-44c5-93a9-cc7c5763e3af` 于 `2026-11-22T00:35:25.540544+00:00` 对精确 H 签署 `VERDICT: PASS`。[推送前复审原文](pr-118-final/h-pre-push-review.txt) · [执行回执](pr-118-final/h-pre-push-receipt.json)
CI 后会话 `sess_397092a6-c2ce-43e9-ac1c-bf27264c54e8` 于 `2026-11-22T01:15:46.160357+00:00` 对精确 H/M 签署 `VERDICT: PASS`。[复审范围、发现及原文](pr-118-final/post-ci-review.txt) · [执行回执](pr-118-final/post-ci-review-receipt.json)

[S 的本机与冲突审查](pr-118-review.md) · [完整 S→H 路径及模式](pr-118-final/source-to-head.raw) · [三条 CI 原件](pr-118-final/exact-ci.json) · [运行时间原件](pr-118-final/ci-progress.json) · [原件字节数与 SHA-256 清单](pr-118-final/manifest.json)

保留分支为 `evidence/pr-118-final-b89120f2fa79c6ecf5f371376fc00f1ccd10809c`。本页记录合并前对 GitHub 合并检出的核验。
