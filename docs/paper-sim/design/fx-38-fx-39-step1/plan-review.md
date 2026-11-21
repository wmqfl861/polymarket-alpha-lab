### 独立只读计划复审记录

实际本机 ZCode 会话 `sess_840df9d3-1971-4ad7-830b-9af9af102bb3` 于 `2026-11-21T13:38:51.245331+00:00` 签署。受审计划的 SHA-256 为 `1e6779aad4b1a37d7a617c9f325ba096564d9e48ce1f15e541fedec8501f306a`。下方为复审文字，SHA-256 为 `0ef628e53e7fc3913e29472d68fa9b1b3f4349de9c5c3c7b9f933a69ac588196`。

# FX-38/39 第一步方案复审（只读，仅依据附件）

## 逐项核对

**合并次序**：方案头部按 MC-1 至 MC-3 固定 #116 → #114 → 本节点 → #118；第 1 条要求确认 #114 实际合入、记录合并 SHA 与基线 SHA、用 `merge-base --is-ancestor` 核对祖先后才建工作树，源码定位取自固定 #114 头。出口用普通合并接续 `main` 并在最终 PR 头与实际合并对象分别核对三条工作流。前置条件与执行门槛编码完整。

**统计口径（已登记）**：FX-38 表格把两集合的纳入条件、买入方向价差（`CLOSE − sp.hypothetical_fill_price` / `CLOSE − entries.price`）与各自分母 `n_c`、`n_a` 一次登记；第 4 条给出报告差额 `S_c/n_c − S_a/n_a` 与拦截条件（候选市场数 ≥ `CLOSE_DEVIATION_MIN_MARKETS`=30 且差额 ≤ −0.01，29 仅标注）；常量沿用登记来源；第 5 条为 FX-43 登记"实现更正"。口径闭环。

**各自集合**：候选集以 `gate_pass=true` 同候选记录界定，现役全集自实际出价候选出发、纳入影子拒绝与预算耗尽无影子行的入场，并约束登记时刻、出价身份、目标盘口防同版本异盘口混入；`_optimize_close_prices` 处理两侧并集；单侧一条候选扫描记录最多贡献一次（JOIN 扇出防护）；"扩展 CLV 人群不能扩大对数损失的输入"防止口径外溢。两集合可不相交由原生测试覆盖。

**恰一美分**：PostgreSQL NUMERIC 精确十进制求和，政策比较交叉相乘 `S_c × n_a − S_a × n_c ≤ T × n_c × n_a`，`T` 取登记常量十进制文本，乘减前留足精度。示例（候选均值 0.02、现役 0.03）代入恰得 −0.01 且"等号应拦截"，与 ≤ 判定一致；原生测试覆盖恰 −0.01、−0.011、略高于 −0.01 及循环小数分母。数学与登记一致。

**缺失样本**：`close_dev_missing_candidate`/`close_dev_missing_active` 分别计数，单缺成交价单列、双缺可双记，有效分母按两价齐全计；任一侧分母为零 → `close_dev_gap=null` 仅标注；拒绝、未结算、无穷对数损失不阻碍有两价的 CLV。FX-39 侧把真实零样本（`n_markets=0` 且进度可见）与不可用（`stats`、`next_look` 为 null）严格区分，杜绝伪造零。

**评分不可用**：三种稳定原因（`pricing_spec_mismatch`、`scan_lineage_unsupported`、`pricing_identity_unavailable`）各有触发条件与保留字段，覆盖跨族同前缀、同族异规格、同标签异摘要、注册降级；混合表逐条报告互不影响；直接 score 仅对未登记/未知谱系沿用既有错误退出；对预制市场包的证明边界如实限定。

**旧判定不换届**：对历史及新记录的 `PROMOTE`、`PROMOTE_PENDING_CONFIRM` 立即返回 1、固定原因 `papersim_optimize_apply_promotion_unavailable`，检查先于 actor/confirm、config、折叠、配对及两个角色调用；replay 末路的晋级同样拒绝、角色写者调用数为零；`_decision_roles` 仅替换晋级分支。合入前操作约束（只评分读状态、候选仅 `CANDIDATE`）与合入后产品固定拒绝分别记载。

**回退证据**：ROLLBACK 全链沿用 #114 分派、事务与幂等合同；测试保留陈旧判定三时序、精确重放自动/人工两例（含角色行 ID、二次交接折叠、中断重滚、再命中原事件）、新 PG 连接读回恢复 active、guard 清空、被撤者退役与幂等；被拦晋级的前半程用合成历史，与真实晋级应用明确区分。

**测试保留**：逐文件映射保留/改造断言，点名保留 `test_status_reports_registry_fallback_and_zero_runs`、`test_native_registration_write_path_and_status` 的兼容断言，fail-closed/冻结/暂停防护转到首次 ROLLBACK，单候选 RETIRE、未知 ID、unfreeze 权限延续；O-5 写权归属与后续交接明确。

## 代码适配的必要发现

1. **拦截门槛计数器须钉死**：30 家门槛用 `close_dev_markets`（候选过闸市场行数，含缺 CLOSE/成交价/赛果），与均值的有效分母 `n_c`/`n_a` 是两个不同计数器，实现不可混用；任一有效分母为零时 `close_dev_gap=null` 且不得拦截。
2. **恰一美分不得经过 float**：判定只在精确十进制（或同刻度整数）上做交叉乘减，`T` 从 `CLOSE_DEVIATION_MIN_GAP` 的十进制文本解析；`ForwardStats.__post_init__` 维持只收 float，字符串/null 仅出现在 `_optimize_stats_dict` 与 `policy._forward_metrics`，序列化置于判定之后，不得让 `Decimal(...)` 表示漏出。
3. **晋级拒绝的谓词按记录自身结论**：只对被应用记录自身结论 ∈ {PROMOTE, PROMOTE_PENDING_CONFIRM} 拒绝，不得因前驱是晋级而拒绝 ROLLBACK——回退后半程测试依赖对预置晋级历史的 ROLLBACK 仍可应用。
4. **`_decision_roles` 只动晋级分支**：为获准结论（RETIRE、ROLLBACK）保留 `GUARD/ACTIVE[/RETIRED]` 规划，否则回退的角色恢复会被误伤；replay 与首次应用两路都须验证角色写者调用数为零。
5. **CLOSE 去重与方向**：按候选+买入侧取一条，`observed_at` 最新、同刻 `evidence_id` 最大；同方向 `last` 优先、为空才取 `mid`，合法 0/1 不得当缺失；NO 已是买入侧的直接使用。
6. **现役连影子三重约束**：登记时刻、出价身份、目标盘口同时生效，防同一出价版本另一盘口混入；单侧单候选扫描记录至多一次，防 JOIN 重复计数。
7. **真零与不可用的输出区分**：可比谱系 `n_markets=0` 带真实下次观察进度；不可用 pair 保留 spec/comparator 而 `stats`、`next_look` 为 null——两者不得互相退化；全表 status 共用一次 provider 快照。

方案九项核对全部落实且内部一致，前置合入门槛已编码。

VERDICT: PASS
