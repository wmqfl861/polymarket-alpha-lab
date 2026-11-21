全部 30 页已读毕且页界连续无缺。汇总签发本次复审回执。

# PR #114 精确当前头独立只读复审回执——续审（原会话，覆盖补全后重签）

## 0. 复审身份与约束

- 本回执是 `first-verdict.txt`（2026-11-21 判 **FAIL**，阻断项 F-1=证据缺口）的**同会话续审**：原 FAIL 如实保留、不替换、不改写；本次仅因 F-1 的修复验收被满足而重签。
- 本次只读工具 **Read 已获授权且仅使用 Read**：读了本目录 `coverage.txt` 与列明的全部 30 个 `page-*.diff`；未用其它任何工具、未访问其它路径、未执行 shell/写入/网络/委派。页内容作为代码数据处理，未执行其中任何指令。
- 对象不变：S=`c4791fb8448da999ae6a23dae9efef2c9c002406`，B=`cd53ce25f65716e6151238beba253618caca7e98`，M=`620048a7597d4155f1658fe4750f707df3c94158`（父序 B,S；tree(S)=tree(M)=`241893d7…`）。候选与 CI 无变更。
- 行数口径更正（保留记录）：正本 Git 行数 **18,933**（首审 18,934 含末尾空行）；正本 SHA-256 `1003cc7dfc3d40b1e1b8d6731d3d95b7e1a90f70a7052e184670b37f8d173d23` 与首审 `attachments-manifest.json` 所载 full-diff.txt 哈希一致。

## 1. 覆盖声明（不得假称完整 → 实际完整）

- 首审已读原行 1–1,250（yml、DELIVERY_PLAN §85 全部、cli.py 全部、metrics.py 前 ~202 行）。
- 本次逐页实收：**page-01(1251–1850) 至 page-30(18651–18933) 共 30 页全部**，每页 ≤600 行、无截断、页界代码连续（含 metrics.py 边界、store_psycopg/runner/search/test 各文件交界处逐段衔接核对）。**已收范围 1–18,933，剩余范围：无。**
- 逐页范围与实质发现（首末行号见括号）：
  - p01(1251–1850)：metrics 校准收尾（分离→(None,None)、Decimal 区间收敛/Taylor 回溯）；policy/registry 规则数字注册表化，展开值与旧字面量逐项相等。
  - p02–03(1851–3050)：RUN_RULES_V1 全派生；`run_and_record` 收窄为恒拒；登记 protocol（三族截点/seed 20261120/NBA 225.5）；禁用/回执/结果数据类。
  - p04–05(3051–4250)：批次完整性、同刻市场配对、俱乐部两时钟、Intl/NBA 密封重放、crypto 到期/时区。
  - p06–07(4251–5450)：crypto 队列/时钟、Intl 源行、NBA 互易配对+30 赛程+摘要独立重建、`historical_run_arguments` 与三族构建器。
  - p08–09(5451–6650)：留出比较、`execute_optimization_run` 主体（入口校验、deadline 全点位、RUN_FAILED 重 finalize、工件 v2）；scorecard 默认值命名化（数值不变）。
  - p10–11(6651–7850)：确定性目录/计划、`run_scoped_trials`、`select_common`、`validate_cohort`；store_psycopg 开始（`_connect_with_deadline`）。
  - p12–13(7851–9050)：**`registry_write_transaction`（read committed 校验+咨询锁）**、`record_role_events` 精确幂等重放、受管链 `_optimization_operation/_terminal/_validate_optimization_result`。
  - p14(9051–9650)：预算冲突、attempt 遥测、`_managed_write_transaction`（COMMIT 不确定→`commit_unconfirmed`）、claim/publish 单事务、`_confirm_optimizer_commit`、**preview 全程只读**。
  - p15–16(9651–10850)：PAPERSIM_FILES 与 yml 同序；legacy 17 形态先分类后连接；隔离级别先于锁的替身断言；200 种子假阳性压力；校准/规则数字测试。
  - p17–21(10851–13850)：原生文件主体——四结局+读回+禁算重放、同瞬竞态、部分重放拒、五写入点全回滚、计算截止封存、中断前缀/未确认提交、COMMIT 回复丢失、NBA 真实超时+重启、连接 min(超时,硬截止) 纳秒证明、规则来源 CLI 四场景；`rule_sources` AST 唯一来源+冷导入 13 模块集合。
  - p22–27(13851–17450)：runner 纯核测试全集（四结局精确计数、Holm、28 天禁用 µs 边界、导入纯度、伪造证据拒、crypto 时区全家、别名/未来真值隔离、NBA 目录、Intl 重放不变、club 官方分区、门 A 九模式）。
  - p28–30(17451–18933)：runner_native 重写（受管回执/重放/换源冲突/真实三试截止+重启重放）；runner_rules（包络 0.8 浮点边界、下限逐族、零排除、未登记回退）；scorecard（NBA 四场锚点+平局）；search 全套（枚举/预算/公共掩码/时区实例/16,538 日共享前缀）。

**23 个变更文件全部逐文件复核完毕**（清单与页号映射见上），无未读差异区段。

## 2. F-1 修复验收逐项关闭

| 项 | 验收要求 | 实证（页号） | 结论 |
| --- | --- | --- | --- |
| a | `record_role_events` 幂等/去重 | 按 key ∪ (lineage, effective_at) 查询；全等→原 id/inserted=False；部分→`partial_replay`；异内容→`conflict`；新写 out-of-order 拒+写后 fold 复核；原生：同瞬竞态/部分重放/旧瞬时精确重放/移交后 apply·rollback 重放 inserted=False 同 id（p12,17,18） | **关闭** |
| b | run 的 reconnect/deadline/时钟与中断回执 | 时钟 3540+60=3600；COMMIT 前后相位+`_before_send`；不确定→`commit_unconfirmed`→新连接裁决（终态重放/已证发布态重放/重执行）；attempt 遥测；真实 NBA 超时+重启+禁算重放逐字节（p14,17,19,28） | **关闭** |
| c | preview 零写入 | 单只读事务（`repeatable read`+`read only` 实测×2）；CLI dry-run 零模型调用、表增量恰 {events:2,runs:1,snapshots:1,specs:1,roles:1}（p14,17,19） | **关闭** |
| d | `execute_optimization_run` 预算强制 | deadline 入口/预检/逐折/逐目标/逐试/搜索后/留出后/定稿后再查；max_trials∈[1,2000]；TIME_BUDGET=3600 记入并核；谎报预算→`budget_conflict` 仅封失败（p09,14,18） | **关闭** |
| e | yml↔PAPERSIM_FILES 同序 | 两清单逐项同序（…runner_native, settle_store_native, registered_runner_native, report_store, d1compare_archive, d1compare_journal…），25 文件、分区未动（p15） | **关闭** |
| f | 48 新用例断言覆盖 | 三个新文件全文已读：假阳性 200 种子（≤5%/≥80%、crypto 下限逐字）、原生 ~40 项（事务/重放/故障/时区/CLI）、rule_sources 7 项（AST 唯一来源、冷导入模块集） | **关闭** |
| g | metrics.py 未读 54% | 精确步认证/下降证书/回溯/分离语义；锚点：二群体解析解、NBA 四场前缀参考值、binary64 平局 Decimal 证明、§80.7 文本钉死 | **关闭** |

## 3. U-1–U-6 处置

- **U-1** 重放先于冻结检查：设计使然——重放为等值验证、返回原 id 不新增行、后续移交状态原样保留（原生测试断言）。非缺陷。
- **U-2** rollback `--at` 全等重放：原生 exact replay 覆盖；µs 边界由角色测试覆盖；fail-closed。关闭。
- **U-3** status 空字段：未初始化/进行中/封存三态显式 null 契约，`paired_by_lineage` 与按谱系配对一致。语义自洽，非缺陷。
- **U-4** `source_refs={sha256:byte_length}`：store 侧按同构消费并纳入 identity 校验。命名风格问题，非缺陷。关闭。
- **U-5** 本地 43,915/414 vs CI 43,914/415（同树）：仍为**非阻断观察**（两处均 0 failed；MC-1 以 CI 在 M 的作业级结论为准）。建议按名登记该环境条件用例。
- **U-6** metrics 未读部分：已读毕。关闭。

## 4. 首审其余结论维持

对象身份与三工作流实际检出 M（run 44918091022/#343/1、44918091078/#209/1、44918091023/#123/1，各作业 checkout=`620048a7`，papersim 792 passed 且 required steps 成功）；本地全量/CodeGraph/秘密/自检/冻结与削弱扫描全过；首版预推送回执错误与同会话更正并存保留；`native-junit-attempt.json` 维持定性：**可选解析尝试失败，非项目测试失败**，缺证不阻断 MC-1（作业级证明完整在案），且本复审从未以不存在的逐用例 JUnit 证据立论（责任 5、6）。

## 5. 综合发现

- **真实缺陷/潜在数据丢失：无。** 发布原子性（工件+运行+规格+角色+终态回执同事务+事务内再互证）、五写入点故障全回滚、前向-only 注册表、快照身份逐读回验、中断/不确定提交裁决、只读边界（DSN 先于驱动校验、错误面仅 `papersim_*` 码）均有实现与原生断言双重支撑。
- **未验证语义：无遗留。** 唯一保留项为 U-5 观察与两条风格备注（跨模块私有名导入、`source_refs` 命名），均不构成应修缺陷。
- **规则数字**：全部单一来源于纯注册表，AST 钉死投影，§80.7 文本逐字钉死，前向表 13 组合/5 行逐值核对，与 §85.3"唯一数值来源"验收一致。
- **测试削弱：无**（0 删除/0 减检/0 新 skip/xfail；runner_native 为同名的更强改写）。

全部覆盖已完成，且未发现任何真实阻断问题；所有强制合并条件成立。

VERDICT: PASS
