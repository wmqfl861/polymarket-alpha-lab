# FX-38、FX-39 第一步及 O-5 的独立源码复审

受审提交 `372f9cbebe2b60c84a39237efb62551f77c315dc`，起点 `d216059339dba9c3ca34d65813e9cf2ba98a0dd5`。实际本机 ZCode guarded 会话 `sess_69062d2b-8b7f-407d-a379-dff9a1cd6128` 于 `2026-11-21T19:13:15.365701+00:00` 签署。执行专项的 ZCode 会话为 `sess_1dd2a536-aee8-42f4-8e26-85873ec29a85`。离线专项 251 项通过，隔离 PostgreSQL 18.6 专项 15 项通过、零跳过，另通过模拟盘十项自检。完整六文件变更分十页送审。此回执绑定实质提交，合并前按最终头另做完整门禁。

以下复审原文共 9053 字节，SHA-256 `03013150d88d02834c39a58ec5cc06967c6a4ba20c6e6f984f99c2649c858265`。

全部 10 页差异（6 个文件）已逐页读完并确认连续覆盖。以下是独立源码复审报告。

# FX-38 / FX-39 第一步候选源码复审

**复审者身份**：独立本机 ZCode，只读，未参与本节点规划、实现或测试执行。仅用本目录附件（Read），未执行命令、未写入、未访问附件外资源。
**精确候选**：`372f9cbebe2b60c84a39237efb62551f77c315dc`；父 `d216059339dba9c3ca34d65813e9cf2ba98a0dd5`（已合入 #114）。与 hygiene.json、scope.txt 记载一致。

## 一、覆盖确认（逐页）

| 文件 | 页 | 状态 |
| --- | --- | --- |
| papersim/cli.py | 1–350、351–552 | 两页读毕，hunk 于 `_optimize_score_lineage` 候选循环处连续衔接 |
| optimize/policy.py | 1–172 | 全页读毕 |
| tests/test_papersim_optimize_cli.py | 1–191 | 全页读毕 |
| tests/test_papersim_optimize_policy_native.py | 1–350、351–700、701–916 | 三页读毕，`_run_decide` 写边界与 `classify_threshold` 处连续衔接 |
| tests/test_papersim_optimize_registered_runner_native.py | 1–155 | 全页读毕 |
| tests/test_papersim_optimize_runner_native.py | 1–130 | 全页读毕 |

coverage.json 所列全部 10 页均已直接阅读原文，无以简报或截断代替。源码改动仅 cli.py 与 policy.py，测试改动 4 个文件，与 scope.txt 一致；docs/DELIVERY_PLAN 及设计文档为登记性新增，不属源码/测试差异。

## 二、FX-38 发现

- **独立 CLV 人群**：`_optimize_close_deviation` 单条 SQL 同时取两侧——候选侧经 LATERAL 同候选、`spec_sha256` 匹配且 `gate_pass IS TRUE` 的最新影子；现役侧 `conclusion='ACCEPTED'` 且有入场行。`(sp.gate_pass IS TRUE OR e.entry_id IS NOT NULL)` 使**现役接受且有入场、影子不存在时仍计数**；候选过闸而现役拒绝/暂停未入场仍进候选集合。两侧各自 `dict` 按候选键去重，LATERAL `LIMIT 1` 与 `DISTINCT ON` 防重复计数。
- **真实方向 CLOSE**：`_optimize_close_prices` 以 `p.side = c.side` 匹配候选存储方向，`COALESCE(last, mid)`（last 为空才取 mid），只排除双 NULL，合法 0/1 保留；`ORDER BY observed_at DESC, evidence_id DESC` 实现"最新观察、同刻 evidence_id 最大"。原生测试覆盖 NO/YES-basket 侧、异侧更晚 CLOSE 不混入、迟到旧标记被 observed_at 压制、同刻平局按 evidence_id。
- **时间与规格归属**：`evaluated_at >= since`（窗口外行被排除）、`model_version`（跨版本排除）、`COALESCE(market_type, market_kind) = target`（跨盘口排除）、`scan_runs.params` 中 `provider.specs[scan_lineage].active_spec_sha256` 必须等于现役摘要；**旧摘要缺失（无 provider.specs 键）时放行**，仅靠版本/盘口/窗口约束——该 legacy 放行分支无正向人群实证，但 tests-before-after.md 已如实登记其证据范围，方向保守、不扩大配对人群，不构成阻断。
- **精确一分边界**：`CloseDeviationSample` 用 `Fraction` 对 PostgreSQL NUMERIC 的 Decimal 精确求和；`_close_deviation_check` 在序列化之前以 `Fraction(str(CLOSE_DEVIATION_MIN_GAP))` 精确比较（等价 S_c×n_a−S_a×n_c ≤ T×n_c×n_a）。常量 −0.01 与 30 冻结未变（仅注释改写）。原生四组验证 +0.01、−0.011、恰 −0.01、−0.00999…9（浮点会舍入为同值但精确判定不拦截），并证候选侧分母为零时只标注、旧绝对概率公式会反向拦截。`close_dev_gap` 字符串序列化，`Decimal(report[...])` 可精确复算，均值与差值以分子/分母或字符串输出。
- **缺证计数**：`markets` 为过闸行数（含缺 CLOSE、缺成交价、缺赛果），`missing_close` 与 `missing_fill` 单列、双缺同记两项，有效分母按两价齐全；`close_dev_active_entries`、`close_dev_missing_*` 新键齐备。truth 表以显式 (fill_id, close_id) 对在原生 NUMERIC 上独立复算（candidate 30/3/26/26/0.061、active 7/6/1/0/0.182，我独立复算数额一致，含同刻影子和 CLOSE 平局的取舍）。
- **原配对逻辑不变**：`_optimize_pair_rows` 未改；循环仅删除旧绝对概率 close 分支，配对、赛果、聚簇、CI、水位、`unpaired` 语义保留；`n_markets==1`、`unpaired==31` 证明对数损失输入未被 CLV 人群扩大。`ForwardStats` 保留 float 字段与旧构造（`_close_deviation_gap` 在双 sample 缺省时回落旧 float），`__post_init__` 未触碰。

## 三、FX-39 发现

- **固定拒绝**：`_cmd_optimize_apply` 在决策行读取后、lineage/actor/confirm/config/工作树/折叠/配对/重放之前对 `{PROMOTE, PROMOTE_PENDING_CONFIRM}` 一律 `papersim_optimize_apply_promotion_unavailable`；`_decision_roles` 开头同一拒绝使 replay 与角色分派同拒。单测参数化 fresh/replay × 两结论 × 四种 actor/confirm 组合，`captured == {}` 证角色写者零调用；原生全轮在写边界拦截后用预置历史决策验证两结论拒绝 + 已链接角色历史的重放拒绝，`record_role_events` 被 monkeypatch 为 fail，历史零增；注册运行器 PROMOTE 例改为固定拒绝并 `forbid_role_write`，读回无增。stderr 精确匹配固定原因串。
- **RETIRE/ROLLBACK 保留**：`RETIRE` 无 config 只退候选、无事件结论与未知 ID 错误、frozen/paused 防护断言（转由 ROLLBACK 承担且批次未构建）均保留；ROLLBACK 的真实统计判定、应用、第二连接读回恢复原 active、guard 清空、退役、幂等（inserted False、role_event_ids 相等）在原生全轮后半程以预置 handover 历史起步验证；注册运行器三种过时时序与精确重放用直接 SQL 预置历史，不走业务角色写者。
- **status/score**：`_optimize_forward_comparison` 区分 `available / pricing_spec_mismatch / scan_lineage_unsupported / pricing_identity_unavailable / registry_not_initialized`，比较完整 SHA-256（同版本标签不同摘要由 mismatch 覆盖），显示版本与 fallback 原因；`_optimize_scan_lineage` 经 `_LINEAGE_FAMILIES`/`family_ids_for_prefix` 映射登记谱系到扫描前缀。不可用时 pair 保留 spec/comparator，`stats`、`next_look` 为 null，无伪造样本零与倒计数；可比时以**当前扫描选出的** model_version 计算并输出真零与进度。`score` 显式 REPEATABLE READ READ ONLY（测试逐字断言 SET 语句），全表 status 共用一次 provider 快照（实测 `('repeatable read', 'on')`）；空注册表保留 `initialized=false`、`active=null`、fallback、空 pairs、`forward_sample_count=null`；直接 score 未登记/未知沿用既有错误退出；读取前后业务行、角色、决策计数完全不变。`allow_unavailable_active` 使注册读取降级（两类异常）不再炸掉描述性读取，`active_queue_entries` 为 null。

## 四、O-5

新增原生受管测试以 `iterations=0` 的真实校准在真实 scorecard 二元观测上触发不收敛（并断言正负样本交错，排除平凡不收敛）：退出码 1、单一 `RUN_FAILED`、`papersim_optimize_scientific_failure`、无候选发布、`registered_lineages=[]`、STARTED/FAILED 两事件键 ID 回执链接、快照 sha256 与 counts 一致、`role_event_ids=={}`、业务表零增；重放禁算后返回 `replayed=True` 且存储无新行。生产前向写入维持暂停（临时约束与产品固定拒绝分别记载于计划）。

## 五、测试改动对照、冻结与 skip

七个"原证明 → 本节点证明"行均能在差异中逐一对应：29/30 与一分边界迁移至原生 NUMERIC 且原 float 单位测试经兼容回落继续有效；全轮改写为写边界截获 + 预置历史；club 无配对迁至可比冻结谱系验证真零，再校准谱系改证不可用；apply CLI 两测试替换为合同要求的固定拒绝，RETIRE/未知/解冻断言保留；注册运行器仅 PROMOTE 例换为固定拒绝。新增 skipif 仅新测试自带的既有原生 PG 前缀模式（与相邻既有测试同款），未见任何既有 skip 放宽或删除；`CLOSE_DEVIATION_*` 数值与登记来源未变；`hygiene.json` 三类敏感标记零新增路径/零新增行。

## 六、证据可见性与边界

独立本机实际证据：`focused.txt`（251 passed）、`native-proof.json`（15 个聚焦原生用例全 passed、0 skip，head 与候选一致，隔离一次性原生 PG 18.6，fixture 已清理，受管工作树未变）、`test-executor.json`（独立执行单元、exit 0）。`native-summary.json` 自明标注 "focused native … full matrix acceptance is separate"：**本轮证据仅为隔离库合成历史下的聚焦执行，不构成最终候选的全量与 CI 验收**，后者按规范程序在治理合入后的最终候选另做——本复审结论不越界代认。

## 结论

候选与声明的范围、行为、测试映射和证据边界相符；未发现阻断性缺陷。唯一可注意点（legacy 扫描摘要缺失分支缺正向人群实证）已在交接文档如实登记且行为保守。

VERDICT: PASS
