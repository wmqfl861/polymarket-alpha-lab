# PR #114 独立只读复审回执

审阅日期为 2026-11-21 UTC。GitHub 当前合并对象的父提交依次是本轮 `main` 和 PR 实质候选。候选及合并对象的树均为 `241893d7f099d144fdc25a9eb15513ecd49e13bd`。

| 固定对象 | SHA |
| --- | --- |
| `main` 基线 | `cd53ce25f65716e6151238beba253618caca7e98` |
| 受审实质候选 | `c4791fb8448da999ae6a23dae9efef2c9c002406` |
| 工作流实际检出 | `620048a7597d4155f1658fe4750f707df3c94158` |

| GitHub 工作流 | 运行号 | Attempt | 结论 |
| --- | --- | --- | --- |
| Offline verification | 44918091022 | 1 | success |
| Native project PostgreSQL | 44918091078 | 1 | success |
| Research paper bridge | 44918091023 | 1 | success |

三条工作流中实际运行测试的六个作业均检出同一合并对象。Offline CI 通过 43,914 项，本机 Linux 通过 43,915 项。差异来自 `test_papersim_optimize_metrics.py::test_anchor3_wp_p4_paired_comparison`。GitHub 离线日志在第 540 行给出的跳过原因是 `WP-P4 evidence JSON is server-only`，本机回执无此跳过原因。Native papersim 通过 792 项，包括 `test_papersim_optimize_registered_runner_native.py` 的 75 项。第一次工具读取 JUnit 失败后，已按单一 artifact 编号重新读回并验证。Research paper bridge 通过 357 项。[检出及计数](pr-114/ci-checkout.json) · [JUnit 回读](pr-114/native-junit-summary.json) · [首次工具退出](pr-114/junit-reader-first-attempt.json)

| 实际日期和结论 | 复审者 | 范围与处置 | 回执 |
| --- | --- | --- | --- |
| 13:10 FAIL，13:16 PASS | 本机只读 ZCode `sess_5494f412-df8f-4590-a658-1bd586454a5e` | #114 与 #118 的合并条件。第二稿给出 CI 实际检出、顺序及证据入库的做法。 | [首审](pr-114/plan-first-review.md) · [返审](pr-114/plan-rereview.md) · [定稿](pr-114/merge-plan.md) |
| 14:16 PASS，14:43 更正后 PASS | 本机只读 ZCode `sess_a7c62846-3051-47a5-a5f6-3f8be5a6f33f` | 实质候选的集成与推送范围。原回执误读树对象和父提交，原会话按明确标注的 Git 对象更正。 | [首版](pr-114/prepush-first-review.md) · [更正](pr-114/prepush-rereview.md) |
| 15:56 FAIL，16:11 PASS | 本机只读 ZCode `sess_a55c2823-74f0-41c9-b775-0780fe3bfbcc` | 受管快照、运行器、输入登记、时钟与预算、事务和幂等重放、冻结项、测试变更、工作流、§85。首审收到 1,250 行差异。原审者续审实际读取其余 30 个连续分页，覆盖完整 18,933 个 Git 差异行。 | [首审](pr-114/post-ci-first-review.md) · [续审](pr-114/post-ci-rereview.md) · [页范围与摘要](pr-114/diff-coverage.tsv) |

最后复审未发现应修阻断。复审列出两项非阻断观察，涉及跨模块私有符号和 `source_refs` 命名。旧组合头 `17dced4763fadc8505511fcac7b7ed3cf95db62e` 的停止及专项修订记录在 [交付登记 §85.3](../../../DELIVERY_PLAN.md#853-规则-v1-的唯一数值来源及出口复验)。

本文件记录上述实质候选和它的 GitHub 合并对象。归档本文件生成的文档后续头，其三条 CI 和最终头审阅在产生后登记到 #114 PR 描述与同仓库保留证据提交。证据目录内 [校验清单](pr-114/MANIFEST.sha256) 覆盖每份入库材料。
