# FX-47 候选报价的原生库回归与 M2 顺序

状态为待独立计划复审。11-24 的 R6-7 已在既有隔离复现中记录 SQLSTATE `42846`。FX-47 的写入窗口由协调者分配，`papersim/store_psycopg.py` 同时只交一名写者。NBA v2、FX-53 的 MO-2 事务小修与本节点串行，每次交接以刚合入的 `main` 为基线。FX-40 在 FX-47 合入后接手。

## 实现与验收

1. 在 `papersim/store_psycopg.py` 的 `record_scan` 写入边界统一 `variant_quotes` 的表示，让 `ScanSubmission.payload_sha256()` 和 `_candidate_params` 使用同一份候选报价。现有校验接受字符串和数组，现有摘要也包含原始表示。只改 SQL 参数会留下 `papersim_scan_payload_conflict`。保留既有字符串扫描的原样重放、数组元素顺序和真实负载变更的冲突检查。沿用现有 JSON/JSONB 设施和数据库结构。
2. 在 `tests/test_papersim_scan_store_native.py` 复用隔离原生 PostgreSQL。用同一个非空报价市场包，只替换 `variant_quotes` 的字符串或数组表示。固定干净的代码提交、市场包路径、队列、扫描时钟、候选状态和 `--operation-key`。通过 `polymarket_alpha_lab.papersim.cli.main` 的 `run-scan` 子命令实际读取包并写库。测试只控制时钟，数据库和候选写入走生产路径。选用会生成候选且不改变下一次扫描仓位的市场。
3. 在两个全新测试库分别运行「字符串首次写入 → 数组重放」和「数组首次写入 → 字符串重放」，避免幂等预读掩盖某种形态的首次写入。每次命令退出码均为 0。每次运行后读回 `papersim.candidates.variant_quotes`，核对 `jsonb_typeof` 为 `array`、有序内容相同。每组两次返回同一 `scan_key`、`scan_id` 和候选 ID，同一 `scan_payload_sha256`，`inserted` 依次为 `true`、`false`，扫描与候选计数均为一。另验旧文本行重放，以及同键改变一个报价时报原冲突、原行完整。
4. 独立计划 PASS 后实施。聚焦验证覆盖上述原生用例与现有重放测试，全量验证、编译、`git diff --check`、CodeGraph 同步、受跟踪内容敏感信息检查按节点规则执行。独立代码复审覆盖摘要和真实 CLI 路径。按当时 PR 头保留三条绿色工作流编号、`check_ci_merge.sh` 的 `CURRENT` 和所有复审回执，随后普通合入。验收回执记录候选提交、两组返回值、SQL 读回值和计数。

## 后续 M2 的证据顺序

| 顺序 | 可作为下一项输入的证据 |
| --- | --- |
| FX-47 → FX-40 | FX-47 合并提交和双向原生重放回执。FX-40 草稿独立过节点流程，市场准备接到命令入口，四份冻结输入产出基线请求，同批登记快照重放得到逐字节相同的市场包并实际供 `run-scan` 使用。 |
| FX-44 → D1 并跑 | FX-44 登记收盘价的 60 分钟时间窗、版本及 R6-17。分别报告两侧有价行数，登记是否各设下限。D1 的七个连续无人值守日全部在 FX-40 合入之后，逐日留新旧同批比较、每项差异归因、现金恒等式和干净的扫描提交。零手工改库，抽查候选可重放。 |
| FX-45 → FX-46 | D2 前登记标准 F 抽样。全部已入场候选重放，其余按切换日八位日期为种子抽 200 个，不足时全取。FX-46 随已合入的市场准备入口记录完整命令链、读写和调度。D2 由归因处置及既定出口证据支持，登记切换日、仓库命令接管及旧脚本归档，自当日计标准 F 的 30 天。 |
| FX-39 第二步 | 依赖 FX-40。隔离库证明同批候选和实际报价规格有配对，换届和换回后的下一次扫描用正确版本报价，空注册表逐位等同。连同 `decide` 可比性检查和不可比谱系的候选到期行为一起处置。此项还供优化线第二阶段验收。 |

M2 原出口继续核对 B1–B4、D1、D2 和 FX-26、FX-27、FX-29、FX-34、FX-35 的合入记录。并跑天数及切换时间以实际回执填写。顺序和出口依据见 [11-24 整改 §1、§3–4](../../../handoffs/2026-11-24-incremental-review/02-remediation-and-next-steps.md)、[11-24 提示词步骤 7](../../../handoffs/2026-11-24-incremental-review/03-local-agent-prompt.md)、[11-21 整改 §1、§3](../../../handoffs/2026-11-21-progress-review/02-remediation-and-next-steps.md)、[M2 原出口 §6.3](../../../handoffs/2026-11-12-audit/04-development-plan.md) 和 [11-19 M2 补充 §2](../../../handoffs/2026-11-19-audit-optimizer/05-development-plan.md)。
