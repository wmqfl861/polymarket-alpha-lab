# PR #114 最终头核验

| 对象 | 完整提交 | 树 | 父序 |
| --- | --- | --- | --- |
| `B` | `cd53ce25f65716e6151238beba253618caca7e98` | `20a13487c1a91b3f8cc5a8bf385186f974db21b3` | `a3492ea66d3d9e18d62d1e8c4919582710124416`、`8c7b536514eda6dfc772bf7fc6a4fc597892a294` |
| `S` | `c4791fb8448da999ae6a23dae9efef2c9c002406` | `241893d7f099d144fdc25a9eb15513ecd49e13bd` | `26dfa2a05357121665009981cd600e23c91e093a`、`cd53ce25f65716e6151238beba253618caca7e98` |
| `M_S` | `620048a7597d4155f1658fe4750f707df3c94158` | `241893d7f099d144fdc25a9eb15513ecd49e13bd` | `cd53ce25f65716e6151238beba253618caca7e98`、`c4791fb8448da999ae6a23dae9efef2c9c002406` |
| `H` | `9647a3ef68c18e5fd5356aeb81bf8df06976a433` | `810cab1fa7475e9d32576b4cbf46bdfeb8cd88fd` | `c4791fb8448da999ae6a23dae9efef2c9c002406` |
| `M_H` | `1feabe950a1f93c9fc694564a11e79b4244e8ec9` | `810cab1fa7475e9d32576b4cbf46bdfeb8cd88fd` | `cd53ce25f65716e6151238beba253618caca7e98`、`9647a3ef68c18e5fd5356aeb81bf8df06976a433` |

B 是 H 的祖先，`tree(M_H) = tree(H)`。取证开始于 `2026-11-21T17:25:55.118229+00:00`，完成于 `2026-11-21T17:26:12.649760+00:00`。固定助手报告 #114 的 Offline 为 `CURRENT`。

| 工作流 | 运行号 | Attempt | 事件 | 结论 |
| --- | --- | --- | --- | --- |
| Offline verification | 44928160743 | 1 | `pull_request` | `success` |
| Native project PostgreSQL | 44928160706 | 1 | `pull_request` | `success` |
| Research paper bridge | 44928160726 | 1 | `pull_request` | `success` |

| 测试作业 | 作业 ID | 实际检出 | 必需步骤 | 测试计数 |
| --- | --- | --- | --- | --- |
| `offline` | 136918716361 | `1feabe950a1f93c9fc694564a11e79b4244e8ec9` | `Run sanitized full offline verification` 为 `success` | 43914 passed, 415 skipped, 20 warnings |
| `linux-native-storage` | 136918716540 | `1feabe950a1f93c9fc694564a11e79b4244e8ec9` | `Prove isolated native partition` 为 `success`、`Prove native version switch` 为 `success` | 320 passed, 2 skipped；89 passed |
| `linux-native-research` | 136918716676 | `1feabe950a1f93c9fc694564a11e79b4244e8ec9` | `Prove isolated native partition` 为 `success` | 1227 passed, 18 skipped |
| `linux-native-dispatch` | 136918716390 | `1feabe950a1f93c9fc694564a11e79b4244e8ec9` | `Prove isolated native partition` 为 `success` | 1933 passed, 15 skipped |
| `linux-native-papersim` | 136918716579 | `1feabe950a1f93c9fc694564a11e79b4244e8ec9` | `Prove isolated native partition` 为 `success`、`Assemble throwaway papersim cluster` 为 `success` | 792 passed |
| `windows-paper` | 136918716404 | `1feabe950a1f93c9fc694564a11e79b4244e8ec9` | `Prove research-to-paper assembly on an isolated native instance` 为 `success` | 357 passed |

四个 Linux 分区均成功。Native 的汇总门及退休 Windows 项为 `windows-native-${{ matrix.partition }}`（136918717400）为 `skipped`、`windows-native`（136918717996）为 `skipped`、`linux-native`（136924116650）为 `success`。

## 独立只读复审

本机实际 ZCode guarded 会话 `sess_16210cc8-92b9-47b1-b69f-824caf3fee11` 于 `2026-11-21T17:32:48.162698+00:00` 完成，对精确 H 签署 `VERDICT: PASS`。复审范围和发现见原文。

S 的原会话于 15:56 UTC 签 FAIL、16:11 UTC 续审签 PASS，续审覆盖了全部 18,933 个差异行。[实质复审回执](pr-114-review.md) · [首审原件](pr-114/post-ci-first-review.md) · [续审原件](pr-114/post-ci-rereview.md)

| 原件 | 字节 | SHA-256 |
| --- | --- | --- |
| [三条 CI 检出](pr-114-final/ci-checkout.json) | 8374 | `1adeaebcca04f846e544e002ac916532241469c1b78f13bfe3e11e08438c5f39` |
| [H 的 CI 后签署](pr-114-final/post-ci-review.md) | 9082 | `5416b178042236a03afbba5232bdab8082e335dfcc67b4365a65748b3b935159` |

保留 ref 为 `refs/heads/evidence/pr-114-final-9647a3ef68c18e5fd5356aeb81bf8df06976a433`。从 #114 正文取得不可变证据提交和文件链接。按该提交读取本页、两份原件和交付登记 §85.4，并按表比较原件字节数和 SHA-256。交付登记回读直接计算字节数和 SHA-256，与该提交的完整 blob 比较。验收同时核对保留 ref、证据提交及其树，并确认该提交的唯一父为 H。
