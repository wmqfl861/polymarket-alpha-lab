# PR #118 源码候选本机验收与冲突复审

本机实质候选 S 为 a3cfc09ed0cf6fae88845500fc6ddf2155aafb7c，树为 09643735652b406977d4ac3bfc7edd0209b7dad9。父提交依次为原 #118 头 O（f09850ba8ef010895d6a959cbeff401fbc706363）和核验过的 main 基线 B（01a3d6d208fb7a22886f3cc0b54f4b6499d83400，#121 落地提交）。

Git 只报告 store_psycopg.py 一处冲突。解决保留当前 main 的两路受管连接、身份校验、时限与失败关闭，在两路验证和回滚成功后写入 D1 所需的已验证根标记。D1 的结算参数和工作流清单与联合 FX-38/FX-39 第一步的控制相容。两侧 blob、冲突阶段和实际候选树见 [处置记录](pr-118/semantic-disposition.md)、[静态核验](pr-118/blob-and-inventory-verification.json) 与 [候选差异清单](pr-118/candidate-paths-and-tree.txt)。

## 计划与复审原件

原独立本机 ZCode 计划会话 sess_5494f412-df8f-4590-a658-1bd586454a5e 于 2026-11-21 UTC 审 [首稿](pr-118/merge-conditions-plan.md) 签 [FAIL](pr-118/merge-conditions-review-first.txt)，复核 [第二稿](pr-118/merge-conditions-plan-v2.md) 签 [PASS](pr-118/merge-conditions-review-v2.txt)，随后审 [#118 刷新补记](pr-118/pr118-joint-fx-refresh-plan-addendum-astra.md) 签 [PASS](pr-118/pr118-joint-fx-refresh-plan-review.txt)。同一会话对 [单轮最终头补页](pr-118/pr118-final-head-ci-plan-astra.5k4cc36p.md) 签 [PASS](pr-118/pr118-final-head-plan-original-rereview.txt)，批准仅在可执行 blob 等价的最终 H 的真实合并对象 M 上执行三条远端工作流。

独立本机 ZCode guarded 只读复审会话 sess_077a1f26-359e-4f3e-a678-332077d02d51 于 2026-11-21T23:36:32.392367+00:00 审精确 S 并签 PASS。范围为双方冲突语义、源码候选差异以及实跑的本机证据。[复审原文](pr-118/source-conflict-review.txt)、[启动回执](pr-118/source-conflict-review-receipt.json) 与 [送审清单](pr-118/packet-manifest.json) 固定结论与范围。会话来源为 coordinator-supplied ZCode session identity。

## 本机门

- 工作流清单 42 通过。离线专项 479 通过、1 项因原生开关跳过，跳过项已在完整原生分区运行。
- 一次性 PG18 原生专项 35 通过。另一隔离实例按 28 文件顺序执行完整 papersim，845 通过、零跳过、零失败。完整分区实际覆盖全部 35 个专项具体 nodeid。两套实例均已清理。
- Linux 全量与编译通过。pytest 汇总为 43951 passed, 437 skipped, 20 warnings in 2699.92s。Papersim 自检 10/10 通过。CodeGraph、差异及敏感信息检查通过。
- 原生专项首轮、完整分区首轮分别遇到磁盘空间不足。后续 launcher 对脱敏后的参数名进行核对时在预检退出。修正核对后沿用同一 S 的原始已通过专项，重跑完整分区。三次失败的阶段、计数或诊断与原件摘要见 [本机回执](pr-118/local-verification.json)。

S 留在本机。远端发布及实际三条工作流验证在 H/M 上进行，最终推送门、CI 后独立复审与保留回执仍待取得。[归档清单](pr-118/manifest.json) 记录各文件字节数和 SHA-256。
