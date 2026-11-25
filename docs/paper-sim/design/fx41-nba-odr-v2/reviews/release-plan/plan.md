# FX-41 NBA v2 在 #118 后的受管接线与单次执行交接

本稿供 Linux 协调者分派剩余写权，由新派实际 Linux ZCode 在 guarded、禁 shell/写入模式下独立审核本稿。业主已批准先登记、后执行一次。采用同一 17 个完整规格及各自六变体，两季各前三个比赛日只训练，283 个测试日共 4,811 次物理试验、9,622 次目标使用，冻结留出 38 日/304 场。通用上限 2,000。完整规格、原件指纹、来源时钟、因果折和质量判据直接沿用已审文书。[业主原文](./fx41-nba-single-run-owner-approval.json) · [登记方案与审核](./fx41-nba-v2-registration-plan-review.AQS6tX5e/result.txt) · [六变体更正与审核](./fx41-nba-six-variant-rereview.UwABkoLo/result.txt) · [登记方案原文](./fx41-nba-v2-owner-registration-plan-astra.md)

## 可直接接收的证据

| 已有交付 | 下一位持有者的输入 |
| --- | --- |
| 登记与纯运行器 | 八文件未提交候选树 `e2a4388244b9a00059e3ee975350a995bfe0bc0c`，原父提交是 #114 实际合并 `d216059339dba9c3ca34d65813e9cf2ba98a0dd5`。清单给出各文件 SHA-256、原基线和两文件补丁。登记模块独立合成测试 20 passed，私有运行器集成树聚焦 368 passed，测试会话 `sess_226bf1ad-33c7-4d81-8879-1b456855a220`。私有入口测试使用替身认领，实际认领留给 store。 [八文件清单](./fx41-nba-v2-runner-integration-sol.n1xhk6cq.json) · [登记模块审核](./fx41-nba-registration-module-review.kvj0V8ui/result.txt) · [运行器补丁审核](./fx41-nba-v2-runner-private-review.dajYh6U0/result.txt) · [聚焦实测](/data/pal-verification-20261120/pr-review-validation.Hhpgik2k/nba-v2-private-runner-focus.hbl_hgbs/focused.txt) |
| 无评分形状与合成耗时 | 归档身份预检 2,460 场、模型及留出回调均零。冷启动合成纯段 1,885.937281 秒，4,811/4,811、566 次嵌套、2 次最终选择。两目标双侧成本由自然开放与合成样例拼齐，规范分段 406,636,497 字节。夹具已独立生成两份合成归档，0 次模型调用、0 次 DB 操作。整件序列化、受管写入/确认和 CLI 返回仍待计时。[元数据验收](./fx41-nba-v2-metadata-acceptance.USekx6UA/result.txt) · [纯段实测与受管验收](./fx41-nba-v2-managed-timing-acceptance-astra.OQDca37T.md) · [夹具接口](./fx41-nba-v2-managed-synthetic-inputs-sol.md) · [夹具实测](/data/pal-verification-20261120/pr-review-validation.Hhpgik2k/nba-v2-synthetic-archive-adapter.trk93dgs/fixture-receipt.json) |

## #118 释放写权之前

2026-11-22 00:42 UTC 协调者读回 `main = 01a3d6d208fb7a22886f3cc0b54f4b6499d83400`（#121 实际合并），#118 远端头为 `f09850ba8ef010895d6a959cbeff401fbc706363`。单一发布作业负责推进已审本地头 `b89120f2fa79c6ecf5f371376fc00f1ccd10809c`、树 `e7b2335c0f9baea51a774eacc709f878b0875ab9`。00:35 UTC 首轮 `verify_remote` 退出 2 已留存。以下交接以发布者交回的**实际 #118 合并 SHA、UTC、已测合并对象/树、三项 CI 运行号与结论、冲突复审和保留证据提交**为起点。[#121 合并](./joint-fx-landed.json) · [#118 候选](./pr118-final-local-candidate.json) · [发布状态记录](./pr118-final-ci.gbigmupw/status.json)

- 当班 Sol/max 继续独占控制目录的合成计时仪表文件，交回文件名、SHA-256 与阶段/计数接口。复用已有 `prepare_managed_timing_inputs`、驱动和纯计时回执。交回后可由独立 ZCode 用合成样例核对仪表阶段、计数和签名。CLI/原生测试写者可先列 argv、预期 SQL 列及竞争/断连断言。#118 仅由原作业持有发布及共享文件。[合成计时方案](./fx41-nba-v2-synthetic-timing-execution-plan-astra.md) · [审核](./fx41-nba-v2-timing-plan-review.yZKbPHyG/result.txt)
- FX-40 可在独立控制目录整理冻结四夹具及独立构造器、参考字节/缺项、已封存 Gamma/CLOB 与模型原件的映射、买入 token/有序报价断言，以及既有 SQLSTATE `42846` 两次失败和文本成功对照。FX-40 实施以 #118 实际合入为起点。此后其独占准备模块及专属测试可与 NBA 并行。对 `papersim/cli.py` 和 `store_psycopg.py::_candidate_params` 的改动排在 NBA 相应文件写者交还之后。FX-40 的 `tests/test_papersim_scan_store_native.py` 归 FX-40。七日计数始于 FX-40 合并后的首个合格运营日。[FX-40 原计划](./fx40-market-preparation-plan.md) · [数组增补](./fx40-array-shape-plan-addendum-astra.md) · [增补复审](./fx40-variant-array-plan-rereview.4D4Paw4A/result.txt)

## #118 合入后的文件归属

协调者在干净后继工作树以 `git merge-base --is-ancestor` 逐段核实 #114 → #121 → #118 → 实施父提交，记录实测树与实际合并树。按八文件清单将已审增量移植到该父提交，核对文件/新增差异。原清单双文件补丁已经包含在候选树。遇到上游改动按符号合并并交回实际差异。每份共享文件只有一名写者。实施与测试代码使用原生 `gpt-6.2-sol`、`reasoning_effort=max`，全员关闭快速模式。[已审受管接线方案](./fx41-nba-v2-managed-binding-seam-plan-astra.md) · [审核](./fx41-nba-managed-binding-plan-review.NZTQF0DI/result.txt)

| 写权 | 输入、输出与验收接口 |
| --- | --- |
| Store 写者只改 `src/polymarket_alpha_lab/papersim/store_psycopg.py` | 提供登记服务。接收已核验受管根、批复/库存原字节，按固定季别路径取得归档。沿用 `connect_managed` 的根标记、应用角色、绑定核验、干净代码 SHA 和迁移账本，派生实例指纹。已验证的 IDLE 应用连接切到 autocommit，外层 `_managed_write_transaction` 取 `_REGISTRY_LOCK`，再取登记日程事务锁。锁内以 `_registry_comparison_snapshot` 及角色解码调用既有 `_write_nba_odr_registration`。复核从 `_read_nba_odr_registration` 取得 `NBAODRRegistrationFacts`，外层提交获确认才交三字段指针。登记 COMMIT 回包丢失在同实例新连接按同次序取锁查原键。 |
| 同一 store 写者 | 在 `_managed_optimizer_request/_verify_optimizer_inputs` 按已核验清单绑定两源、日程、登记事件键/ID、材料及 plan/metadata 引用。预览同一个只读一致视图用标量 SQL 提取状态/ID/计划数。科学键 session lock 下，在首个 `STARTED` 以前重核两谱系基线、现役及共享拟合上下文。提交后仅本次首次认领者生成 `_NBAODRComputeClaim`，其 `_take_for_runner()` 只交付一次 `runner._NBAODRClaimInputs`，调用 `runner._execute_claimed_nba_odr(claim)`。store 选择 v2 计算器。旧调用保留 `compute=` 约定。`_validate_optimization_result/_optimization_budget/_optimizer_publish` 以登记有序计划验全量或失败前缀，原子发布。对已有终态重放。悬空根/耗用不明保留中断。同次认领回包丢失，只在原时钟内证实自己的认领、且尚未进入计算才可继续。发布不明只确认或补发进程内同一份发布材料。 |
| CLI 与集成测试写者独占 `src/polymarket_alpha_lab/papersim/cli.py`、`tests/test_papersim_cli_optimize.py`、`tests/test_papersim_optimize_registered_runner_native.py` | 在 `_build_parser/_cmd_optimize/_cmd_optimize_experiment` 接 `optimize register-experiment --managed` 与普通 `optimize run`。登记原子导出指针至工作树外。`_registered_optimization_manifest` 从已核验规范字节有限分派旧协议或指定 v2，直接 store 调用独立复核。保留入口单调时钟及旧协议的结果/退出码，v2 预览在元数据结果上正常返回。联调前由 store 写者交出登记参数、已提交指针及预览/运行回执的字段。原生用例覆盖并发同键/冲突、快照未登记、伪造来源/比较者/回调、4,810/4,811/4,812 与等长错序/错掩码、终态成功和失败重放、各状态预览零成绩读取/零回调/零增行、断连和跨 attempt 的累计启动。复用现有分区。`tests/test_papersim_optimize_runner_native.py` 继续由 O-5 持有，工作流清单如确有收集缺口交其单一持有者。 |
| 同一测试写者接计时驱动 | 在控制目录现有 `fx41-nba-v2-synthetic-timing-sol.py` 补显式 `test_full_managed`，接收在途仪表和已实测适配器。用合成季别/成员/库存/批复/事件键贯穿每个子进程。返回合成登记、第二连接读回、只读预览、冷启动普通 CLI、封存后第二连接读回与零计算重放的独立回执。合成身份安装限于测试进程。 |

## 唯一真实命令的放行与读回

1. **交付提交。** 独立实际 Linux ZCode 测试执行者在精确候选过聚焦、全量离线及原生 PostgreSQL 分区。记录首败/修复/重跑、跳过原因、编译、自检、差异和敏感扫描、项目 CodeGraph 同步、代码/阶段及交接各由独立于实施和测试的新派实际只读 ZCode 签 `VERDICT: PASS`，记录会话与受审对象，首败交原审核者复审。核对 Offline verification、Native project PostgreSQL、Research paper bridge 三条 CI 实际检出的合并对象、树和结论。归档支持提交/最终干净执行 HEAD 的完整 SHA 与树、#118 祖先关系、八文件及接线关键文件的 Git blob。正式登记、预览和执行都使用此干净执行 HEAD。记录实际项目解释器及来源。真实实例与合成库有各自的脱敏身份。
2. **合成受管时钟。** 在最终支持代码、可比 Linux 资源和一次性原生库，ZCode 执行上表完整冷启动。用同一个 `optimization_command_clock` 逐项记录入口、计算结束、规范工件字节/SHA、数据库发布/提交确认、CLI 返回，以及事后读回。若自然仅一谱系开放，按已审办法用实测双开成本替换重叠阶段，并计入双开工件实际/有界的组装与发布。记录观测原值、保守总数及余量。两条同时满足才进入真实登记，**入口至计算结束 < 3,540 秒，入口至命令返回 < 3,600 秒**。遗漏成本记“时钟未证”，触界记 `NO-GO` 与差额。[计时放行清单](./fx41-nba-v2-managed-timing-acceptance-astra.OQDca37T.md)
3. **历史库登记。** 在指定隔离项目原生历史库核对应用角色、脱敏实例指纹、迁移版本、实际 HEAD、两谱系现役/基线、两个固定 gzip 和批复/库存原件的 SHA、长度、对应时钟。用项目解释器 `$py` 和干净根 `$research_root` 执行下方登记。`$approval_file` 和 `$inventory_file` 分别指控制目录的 `fx41-nba-single-run-owner-approval.json` 和 `fx41-nba-source-readback.vVjTuT3k/nba-source-inventory.json`，`$pointer` 指工作树外的瞬时文件。[四份原件的完整指纹](./fx41-nba-v2-owner-registration-plan-astra.md)

~~~bash
"$py" -m polymarket_alpha_lab.papersim.cli optimize register-experiment \
  --managed --repo-root "$research_root" --approval-file "$approval_file" \
  --source-inventory-file "$inventory_file" --write-pointer "$pointer"
~~~

4. **开跑前同一请求的两份 DB 证据。** 另一独立应用连接按固定操作键 `optimize.register-experiment/fx41-odr-nba-regular-2024-25-2025-26-v2`、attempt 1、`SUCCEEDED` 核对唯一登记原行。回交真实事件键/ID/UTC、config digest、科学 run_key、三字段指针、规范清单/材料/比较上下文/metadata/有序计划和两源的种类、版本、来源、原字节 SHA 与长度，重建每季三日训练、每测试日 17 个有序完整规格和每项 `moneyline, totals` 双目标掩码，核对来源时钟与计划摘要。确认两季各自独立 ESPN 原球队赛程 0/30、逐场摘要 0/1,230，Gate A `NOT_ASSESSABLE`。登记、预览前后对同科学键证明运行根、终态、结果与候选增量均零，模型和留出回调均零。再取同根同指针的 `--dry-run`，要求 `not_run`、同一提交/登记/运行身份、4,811/9,622、38/304。遇到已有根、身份冲突或持久结果，直接读回并报告实际状态。

~~~bash
nba_run=( "$py" -m polymarket_alpha_lab.papersim.cli optimize run
  --managed --repo-root "$research_root" --family nba-pace-efficiency
  --as-of 2026-11-19T02:00:00Z --input-manifest "$pointer" --seed 20261120 )
"${nba_run[@]}" --dry-run
~~~

5. **执行者核齐上述回执后，只发一次。** 在同一 Bash 环境运行 `"${nba_run[@]}"`。后续独立连接按原科学键取唯一 `STARTED`、有则终态/运行 ID、退出码、耗时、工件字节/SHA。按已审方案分别写物理开始/完成/空/中止/未启动、各谱系实际留出调用/完成、双侧共同比赛与区块、质量门和候选状态。现役胜出或来源/覆盖不足均按实际处置。独立 ESPN 未评定本身允许符合条件的留出比较，阻止相应候选或统计性拒绝。中断读原记录并保留首因。协调者据原回执更新 `DELIVERY_PLAN.md` 及现有 Phase 1 交接。本稿的正式登记 ID、实际支持提交和运行结果待该节点取得。[正式收尾口径](./fx41-nba-v2-owner-registration-plan-astra.md)
