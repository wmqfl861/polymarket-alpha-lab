# FX-47 在 NBA v2 普通合入后的补充计划（待原计划审者复核）

请签署修订 3 的原独立 ZCode 计划审者裁定，能否在旧实施前独占写权仍然缺证的情况下，允许 FX-47 凭本次真实交接，自本次复核通过起，在 #125 的实际 `main` 上接纳既有候选的确切改动并重新验收。协调者将本补篇的 SHA-256、下列输入及原审答复一并归档。

拟文使用原生 Codex `gpt-6.2-astra`、`reasoning_effort=max`，fast mode 关闭。

## 写权事实与裁决范围

修订 3 第 42 行要求先取得独占写权再实施，第 44 行规定最终候选验收与实际交还。修订 3 的 SHA-256 为 `21deb01e5a6a8c446919fae5a1703e4a9f946debcd956aae43edceb61768bc92`，原审 PASS 回执为 `a54a93ad2f659625fd6d0bc3ab7cf72254456b808c1aeb1c645ca4b5db860bd2`。初版计划的 FAIL 仍绑定原文 SHA-256 `969126ff22bed38da4aa45923a8d60501582d29115aae25536c4bd7373ad7e1f`。限定取证范围内仍缺旧实施前写权原件。此前持有人、其交还及 FX-47 当时首次独占接手时间均记为未知。原计划、初审 FAIL、历次复审、旧候选失败见证及 Git 提交分别按原内容、对象和时间保留。[初审 FAIL](../fx47-reviewed-node/docs/paper-sim/design/fx-47/plan-review-initial.json) · [修订 3](../fx47-reviewed-node/docs/paper-sim/design/fx-47/legacy-empty-list-plan.md) · [原审答复](../fx47-plan-v3-original-rereview.json) · [查证范围](../fx47-release-prep/publication-conditions.md)

协调者在三方可查的同一记录中，于 2026-11-25 02:48:14 UTC 登记 FX-41 NBA v2 当期独占。#125 普通合入并核实后，于 03:08:33 UTC 登记 NBA v2 实际释放、FX-47 同时取得 `src/polymarket_alpha_lab/papersim/store_psycopg.py` 唯一写权。此次接手的效力始于该时刻。两项事件的逐行哈希已在范围回执中固定。只有协调者可按实际发生时间追加裁决、持有、暂停、释放与接手记录。[写权原件](../fx47-release-prep/shared-store-ownership.jsonl) · [落地范围回执](landed-scope.json)

## 本次接纳的确切对象

| 对象 | Git 绑定 |
| --- | --- |
| #125 已落地 `main` | `9acfc3ad07f446415d092153effbc5b81e6b0e77`。双父依次为 `525d2cbe527c7588eb0ec0e53570dd4f03b1f36c`、`f60a6acf03022acb9c57c964718c2c18c651b68f`。树为 `b348aa8af022e430d91ae78ae02143db6c4716aa`，与 #125 的 CI 合并检出 `35d1812e1b13ed5f2fa7e432749420082dff5ff4` 同树。 |
| 已审 FX-47 | `fe24776feddcda8bad752c3aaac25ceaf23384ab`，树 `c13ef1a3ec14d30cea90b3caef3c9850f58602ea`。与已落地主线的共同祖先为 `ab6edff2d64ac8aa5c2831d748b8730b63f8312d`。 |
| 自动合并预演 | 两头合并得树 `cf99096a9702f053dc858600cbf9229f0694ee7d`，零冲突。相对已落地 `main` 的差异与原 FX-47 路径清单一致，共 18 条。实际新提交的 SHA 待形成候选后登记。 |

本机实际 ZCode 运行固定脚本所得 `landed-scope.json` 为 16,464 字节，SHA-256 `55b890e635e36e057faa3b3c15ed31d7cf000c32d0c50e38a3151a35231d8bc3`。两泳道唯一共同改动的路径是共享 store。其落地、已审和预演 blob 依次为 `e9922326093415afcd16ef58aa414979a7ff33af`、`8ba41e376291252d13b70f59a8f8c5e38243518d`、`c1c67e7b84bbe5b4e93a820fd775f3deb8492ab0`。40 项定义或导入绑定各自通过两组比较，即 NBA 合入前后和已审 FX-47 到预演树。预演还逐项保留 NBA 在共享模块改动的 48 个顶层符号。其余两个源码或测试路径是 `tests/test_papersim_scan_store_native.py` 与 `tests/test_research_process_linux.py`，两者均与已审 FX-47 的测试 blob 相同。其余 15 条为交付计划和 FX-47 归档。[对象与逐项哈希](landed-scope.json) · [核查原答及限制](landed-scope-investigation.json) · [#125 合并回执](../ci-nov25/pr125-post124-landed.json)

正常扫描从 `cli.main` 的 `run-scan` 解析、分派至 `_cmd_run_scan`。CLI 整文件采用 NBA 落地版本，扫描参数解析和分派片段在四个对象上的哈希均为 `5b0175f924acb70cd30082bfa8a3577dc58a51cb526ab21728be8e933ec137d9`。正常入口读取市场包、组合和注册表，`scan.run_scan` 经装配调用真实 `record_scan`，并以 `record_command_run_event` 登记 STARTED 以及 SUCCEEDED 或 FAILED。写者沿用 `ScanSubmission.payload_sha256`、`_candidate_payload`、报价规范化、`_candidate_params`、`_CANDIDATE_INSERT` 及候选 ID、现金事件键读回。首次写入以规范报价文本绑定 JSONB 数组。历史 `{}` 仅选择空列表重建形式，每个成功重试须命中完整提交摘要。`READ COMMITTED` 下同键竞争采用 `ON CONFLICT (scan_key) DO NOTHING RETURNING scan_id`，未得新 ID 的写者重读赢家并执行同一比较。[分派、写者和绑定明细](landed-scope.json)

三份完整 SQL 在原基线、已审候选、落地和预演对象上逐字相同。核心迁移保留唯一 `scan_key`、摘要格式、JSONB 非空及数组默认值、`(scan_id, candidate_key)` 唯一性和业务表不可变及防截断触发器，允许读回历史 `{}`。操作迁移保留独立的 `operation_key` 唯一索引及命令回执保护，事件迁移保留 `event_key`、`event_start_at`。`database/`、`supabase/migrations/` 及冻结的 `tests/fixtures/papersim/` 在两个相关基线区间的路径差异均为空。[SQL 全文件哈希及约束行号](landed-scope.json)

## 原审通过后的候选与验收

1. **在当前独占窗口内构造候选。** 协调者先将本次原审回执与实际写权记录对应，复核实际 `main`、两头、预演树及干净工作区。在独立检出中从 `9acfc3ad` 新建本地分支，用 `git merge --no-ff --no-commit fe24776feddcda8bad752c3aaac25ceaf23384ab` 生成索引。只有零冲突、索引树等于 `cf99096a…` 才提交。实际提交以 `9acfc3ad` 为第一父、`fe24776f` 为第二父，两条历史均可快进到候选。记录新头、双父、树、共享 blob，以及 `main` 到新头的完整补丁、18 路径和 `git diff --check`。以另一父核对 NBA 所有已落地改动。自动合并须原样采用。遇到冲突、需要手工改动、祖先或预演树不符，停止构造并将实际差异和修订计划交同一原审者。主线有移动时先重做范围核对，涉及第 42 行所列路径或绑定时先重新取得该审者 PASS。
2. **在新头上验证正常入口和真实数据库行为。** 由本机实际 ZCode 测试执行者核对干净的候选 SHA 和树，在隔离的项目原生 PostgreSQL 18、psycopg-binary 3.3.4 上运行修订 2、3 全部聚焦原生用例（清单原有 10 项、新增 13 项，逐例执行、零跳过）。用磁盘市场包、固定 UTC、作用域时钟代理及恢复断言、相同配置及 operation/attempt，经正常 CLI 并以同一输入直接调用 `record_scan`。用旧绑定独立造数，核验旧 `{}` 与新 `[]` 的 SQL 类型和字节，混合报价、双向及高精度重试、整包冲突、并发提交或回滚、独立 `operation_key` 冲突 `23505`。保存成功与失败前后的摘要、候选映射、证据、入场、现金和命令回执。对测试依赖运行受影响的 FD 用例及整文件，核对退出与清理。测试输入和补丁沿用已审 blob。[原生验收清单](../fx47-reviewed-node/docs/paper-sim/design/fx-47/legacy-empty-list-plan.md) · [FD 测试计划](../fx47-reviewed-node/docs/paper-sim/design/fx-47/test-fd-ownership-plan.md)
3. **在同一新头完成本地门禁。** 在既有串行锁下，按新 SHA 与树生成独立清单并执行完整离线套件。回执记录执行会话、起止 UTC、清单及结果 SHA-256、逐组和文件覆盖、通过与跳过数、失败及退出码，核对全清单与终态。并行可做编译、静态可达性与测试弱化核查、`git diff --check`、CodeGraph 同步及解析验证、受跟踪内容敏感信息扫描。命中须按新旧行字节和来源核验。旧头 `fe24776f` 的全量沿原执行续至终态，只收其原件，不另起相同运行。其 23 项 PG18、独立代码 PASS、静态回执和较早的 `a77a67e7` 全量各归原提交。本地门禁全部通过后，协调者将新补丁、源树、全部结果及初始失败交由未参与拟文、实施或测试的实际 ZCode 独立只读复审。取得绑定新头和树的 `VERDICT: PASS` 并关闭阻塞发现，才推送该头并开普通 PR。已签 FAIL 的修订回原审者复核。[发布条件](../fx47-release-prep/publication-conditions.md)
4. **按实际 PR 检出发布与交还。** 核对最终 PR 头、当时 `main`、CI 合并对象的双父及树。`Offline verification`、`Native project PostgreSQL`、`Research paper bridge` 逐条核实成功，保存各自 run ID、attempt 和检出绑定。`check_ci_merge.sh` 须返回 `CURRENT`，该回执覆盖 Offline 检出的基线。遇到基线变化按第 1 步复核，并在新的实际检出上确认三条工作流。协调者普通合并后对照 CI 树核实实际 `main`、合并 SHA、双父和 UTC，再按真实时刻登记 FX-47 释放、下一持有人和接手 UTC。暂停时登记实际持有或显式交还。FX-53/MO-2 等 NBA v2、FX-47 均实际释放且协调者登记接手后，方可写共享文件。FX-40 按已审依赖引用实际 FX-47 合并和原生重放回执。[发布条件](../fx47-release-prep/publication-conditions.md) · [MO-2 接手条件](../fx53-maintenance-plan.md)

## 请同一原计划审者明确裁定

- **准许向前继续**时，请在答复中明确接受旧实施前独占证据缺失、当时持有人和交接时间未知，仅批准从本次裁决起由已实际接手的 FX-47 在 `9acfc3ad…` 上按上述范围接纳 `fe24776f…`，以 `cf99096a…` 为候选树并履行全部门禁。请绑定本补篇 SHA-256 和落地范围回执，末行写 `VERDICT: PASS`。该裁决只对今后的整合和验收生效。
- **维持阻塞**时，请指出缺证或范围、程序上的具体阻项，说明可被接受的凭据或应由谁作进一步处置，末行写 `VERDICT: FAIL`。协调者保留原答、当前真实写权状态及全部历史原件，在同一原审者签认继续前暂停候选构造与发布。
