# O-5 原生回归和独立只读复审

本轮被审提交 `551b4adef35fccb5e12e69c618945e316450b27b`，父提交 `d216059339dba9c3ca34d65813e9cf2ba98a0dd5`。实际本机 ZCode 会话 `sess_5318fbe4-6f85-400f-ad4f-80c50238d42a` 于 `2026-11-21T18:53:15.387906+00:00` 签署通过。专项由独立本机 ZCode 会话 `sess_4215bfcb-2fc7-4ddc-8adc-6aa5701378d5` 执行，在隔离 PostgreSQL 18.6 上通过 1 项、跳过 0 项。回归使用受控的零迭代校准失败。首次启动缺少项目测试模块的导入路径，修复执行环境后复验同一提交。对后续合并版本仍运行全量和三条工作流。[验收原件](focused-native.json)

下方复审原文为 5736 字节，SHA-256 `b4354a6e4e2ab1347a6a7447c1f466d2f36c920ee486836eda7a244d4f70d2e9`。

# O-5 源补丁独立只读复审

**复审对象**：`551b4adef35fccb5e12e69c618945e316450b27b`（父 `d216059339dba9c3ca34d65813e9cf2ba98a0dd5`，即已合入的 #114）
**范围**：`tests/test_papersim_optimize_runner_native.py`，diffstat 119 增 / 0 删，单一文件，纯新增测试 `test_native_managed_cli_seals_and_replays_calibration_nonconvergence`；仅凭附件内证据（exact.diff、scope、papersim/summary 回执、两轮启动记录、批准计划摘录）复审，未运行任何命令。

## 逐项核对

**1. 零迭代故障注入是真实校准不收敛，非伪造失败。** 注入点在 `scorecard.calibration_slope_intercept`（记分卡二元分支的实际调用点），包装器委托真实现 `metrics.calibration_slope_intercept(observations, iterations=0)` 并原样重抛捕获的 `ValueError`，随后断言 `reason == "calibration fit did not converge"`（diff:34–55）——异常由真 solver 在零牛顿更新预算下真实产生，未在 CLI、存储或运行器入口伪造。捕获观测断言两类均非空且 logit 区间互重叠（`min(neg)<max(pos)` 且 `min(pos)<max(neg)`，diff:56–59），排除了完全分离返回 `(None, None)` 的退化路径，确认失败是混合不可分数据上的真实预算不收敛。注入条件有源码注释与测试名明示。

**2. 经正常受管 optimize run 的原生 PG 实测通过。** 走完整真实链路：`runner.historical_protocol` 取真实协议，`optimize init --managed --repo-root …` 后以 `optimize run --family … --as-of … --input-manifest … --seed protocol["seed"]` 正常 CLI 形态执行（diff:22–41），清单复用已登记的合成二元 NBA 夹具快照。回执：papersim.json 记录该选择器在原生 PG 18.6 / Python 3.12.3 下 passed（1/1，exit 0，约 20.1 秒），`focus_executed_without_skip: true`，隔离服务器 loopback-only、自有数据目录、unix socket 就绪，head 为 551b4ad。

**3. 实际 binary scorecard。** `[(reason, observations)] = failures` 的单元素解包证明恰好一次命中记分卡二元分支；工件内由 `pure["search_trial_forecasts"]` 对活跃 moneyline 规格重构的观测与捕获观测逐一相等（diff:105–111），证明失败发生在真实搜索评分中的真实二元观测上。

**4. 跨连状态。** 独立第二连接 `connect_managed(root)` + autocommit 下核对：`optimization_runs` 恰一行 `(optimization_id, "RUN_FAILED", evaluated_trials, digest)`；事件恰为 `[("STARTED",1),("FAILED",1)]`——同一 attempt 一条 STARTED、恰好一条 FAILED 终态，无悬挂、无重复；两事件 id/key 与回执字段逐一对应，`started.error_code is None`、终态带 `papersim_optimize_scientific_failure`，终态 input_evidence 回链原 STARTED 事件（diff:62–82）。

**5. 工件摘要与哈希。** 终态 output_evidence 与回执的 run_key/optimization_id/outcome/exit_code/artifacts_sha256/artifact_byte_length 全等；快照 kind 为 `optimizer_run_artifact`，`len(archived) == byte_length == artifact_byte_length` 且 `sha256(archived) == digest`；工件内 `code_commit == head`、`stopping_causes == ["scientific_failure"]`、`pure.issue` 保留确切原因并含 `evaluation_integrity_failure`；`trials/planned/evaluated == counts > 0`、`aborted == 0`，终态工件如实记载已完成工作（diff:83–104）。

**6. 同请求重放。** 以 `forbid_compute` 替换 `runner.historical_run_arguments` 后重发完全相同 argv，重放中一旦进入历史评估即失败；结果为 `{**failed, "replayed": True}`——除重放标志外逐字段相同；重开后归档字节与全量存储事实不变，无重算、无新增持久行（diff:117–125）。

**7. 无其他账本变化与无半发布。** `role_event_ids == {}`、`registered_lineages == []`、`proposed_specs == rejections == {}`、`holdout.callbacks == []`；对照 init 后基线，各表增量恰为 snapshots+1、optimization_runs+1、command_run_events+2，其余被跟踪表增量为零且既有行集合保持子集关系（diff:53, 86, 98–99, 112–115）。

**8. 原断言与冻结数值保留。** 0 删改：前邻 init/重放测试与后邻 restart 测试仅为上下文，`test_papersim_optimize_metrics.py` 的四组数值锚点与退化输入测试未触碰；协议、校准容差、样本门槛与生产预算无源码改动，`--seed` 用协议自带种子。

**9. 已有 native 条件用途。** 新测试复用既有 `POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX` skipif（限定合格 PG18 二进制前缀的原生矩阵环境）与 `@NATIVE` 标记，与相邻测试一致，未新造门控；回执证明其在原生环境中真实执行而未跳过。

**10. 首轮启动故障 ≠ 第二轮实测。** 第一轮 18:46:46 仅存活约 49 毫秒即 exit 2（`ModuleNotFoundError`，setup/receipt 阶段），无任何测试判定，夹具已清理——纯基础设施故障，不构成测试证据。第二轮 18:47:46 起经 `pal-nov21-o5-native-calibration-retry.service`（exit 0），18:48:08–18:48:35 完成，`tracked_worktree_unchanged: true`，两轮回执 head 均为 551b4ad。通过判定归属第二轮实测。

## 保留意见（不阻塞）

- 计划中"非初始最优"未逐字断言；但零更新注入不依赖初值，且重叠断言已锁定混合不可分语义，实质等价。
- summary.json 自述 mode 为"focused native from checked-in Linux matrix; full matrix acceptance is separate"——本复审仅覆盖本地专项实测与源码证据，组合版本完整门禁仍属后续集成义务，与委托方声明一致。

范围内源补丁与批准计划 §O-5 逐条对应，实测回执真实、可追溯、无越权改动。

VERDICT: PASS
