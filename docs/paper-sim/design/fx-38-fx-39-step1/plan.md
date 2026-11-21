# FX-38 与 FX-39 第一步实施计划（#114 合入后的单一节点）

供独立计划复审。2026-11-21 的 PR 审核交接 `2e9bc3dc70a045063c7f4b7d1574f6fe2b6e9c95`，02 号文件 MC-1 至 MC-3 指定顺序为 #116 → #114 → 本节点 → #118。验收定义来自进度审核包 `3b04b3171d6c2240d62191d680dfa99d271a371a` 的 02 号文件 FX-38、FX-39 及提示词 `5e4033c4a7820c0f7bea85c54923f46bc376fdc3` 第 4 节。本稿的源码定位取自 Git 中的 #114 头 `26dfa2a05357121665009981cd600e23c91e093a`。

## 起点和所有权

1. #114 按 MC-1 把当时的 `main` 用合并提交接入分支，在实际合并结果上取得三条工作流的运行编号，按 MC-2 对新头完成独立只读复审并登记。确认 PR 已合入，从包含该合并提交的最新 `origin/main` 建独立工作树。记录 #114 实际合并 SHA、节点基线完整 SHA，以 `git merge-base --is-ancestor` 核对祖先关系，同时核对下列入口、测试及规则。若合并带来影响本合同的差异，更新对应计划条目并送复审。
2. P1-4 第一处的复现可以在前置阶段并行。采用该泳道在固定 `main`、一次性项目原生 PG 上取得的 `run-scan` 加市场包、`optimize score`、角色折叠和影子拒绝的回执。记录登记与出价身份、实际配对数、可用性和观测局限。复用已有有效回执，观察与原推断不合时如实登记。
3. 自 #114 合入前直至本节点合入，操作人员仅评分、读状态及角色，不写任何前向判定，不应用任何换届。候选发布仅登记 `CANDIDATE`。工程验收以合成历史、只读评分、纯规则和受控的写入边界检验前向行为。该临时操作约束与合入后产品对两种晋级的固定拒绝分别记载。FX-39 第二步依赖 FX-40，另行处理报价、晋级生效和解除拒绝。
4. 新计划由原生 `gpt-6.2-astra/max` 修订。新派独立、只读的本地 ZCode 审核计划取得 PASS 后，原生 `gpt-6.2-sol/max` 实施并负责测试代码。同一写者持有 `src/polymarket_alpha_lab/papersim/cli.py` 和 `optimize/policy.py` 的统计合同及说明。指定的测试文件各有唯一写者。可复用的 provider、扫描取证、注册表、原生库写者和规则常量均由既有接口提供。协调者独占 `DELIVERY_PLAN.md`。ZCode 独立执行测试。计划、代码及阶段审核按铁律 4 各用新派只读会话，各角色关闭快速模式。

## FX-38　以收盘价减成交价计算两组均值

| 集合 | 纳入条件 | 逐行的买入方向价差 | 用于均值的分母 |
| --- | --- | --- | --- |
| 候选 | 指定规格在登记窗口内同一候选记录上的 `sp.gate_pass=true` | `CLOSE − sp.hypothetical_fill_price` | 同时有有效 CLOSE 和影子成交价的候选行数 `n_c` |
| 现役 | 该窗口、目标市场及实际出价版本的 `c.conclusion='ACCEPTED'` 且有对应入场 | `CLOSE − entries.price` | 同时有有效 CLOSE 和入场均价的现役行数 `n_a` |

1. 在 `_optimize_forward_stats` 独立计算 CLV。候选记录以 `candidate_id` 关联该次扫描的目标影子和唯一的入场行。现役全集从实际出价候选出发，局部连目标影子，纳入影子拒绝、预算耗尽而无影子行的现役入场。候选过闸、现役拒绝或暂停而未入场的记录仍可进入候选集合。约束登记时刻、出价身份与目标盘口，使用扫描目标和当次可核实的盘口/模型信息，防止同一出价版本的另一盘口混入。保留 `_optimize_pair_rows` 原有同候选匹配、赛果、拒绝、聚簇、置信区间、观察水位和 `unpaired` 的评分语义。扩展 CLV 人群不能扩大对数损失的输入。
2. `_optimize_close_prices` 处理两边所需候选的并集，按候选、买入侧找到有效 CLOSE，以 `observed_at` 最新、同刻 `evidence_id` 最大确定一条。读取同方向 `last`，为空才取 `mid`，保留合法的 0 或 1。YES、NO 及既有 basket 侧以存储方向匹配，已属买入侧的 NO 价格直接使用。单侧一条候选扫描记录最多贡献一次，额外的 CLOSE、影子角色和 JOIN 不得重复计数。标准 C 的收盘与成交价方向作为对照。
3. `close_dev_markets` 是候选过闸门的市场行数，含缺 CLOSE、缺影子成交价、缺赛果的行。另给出现役入场总数、两侧均值的有效分母，分别输出 `close_dev_missing_candidate` 和 `close_dev_missing_active` 表示缺有效 CLOSE 的行数。单列缺成交价的行，双缺可同时记入两项原因，有效分母直接按两价齐全计数。任一侧有效分母为零时 `close_dev_gap=null`，仅标注。提供两均值或其分子、分母供复算。拒绝、未结算、无穷对数损失不妨碍已有两价的 CLV。
4. 设两侧有效价差总和为 `S_c`、`S_a`，报告差额为 `S_c/n_c − S_a/n_a`。沿用 PostgreSQL NUMERIC 的十进制精度，按精确十进制或相同十进制刻度整数求和。政策比较可交叉相乘，用 `S_c × n_a − S_a × n_c ≤ T × n_c × n_a`，其中 `T` 从登记常量 `CLOSE_DEVIATION_MIN_GAP` 的十进制文本取得，在乘减前留足精度。这样分母不同及循环小数也能判清恰一美分。候选市场数达到现有 `CLOSE_DEVIATION_MIN_MARKETS`（30）且差额 ≤ −0.01 才拦截，29 仅标注。规则版本及数字继续使用登记来源。
5. `ForwardStats.__post_init__` 在所读版本只接受 float 类型的差额，`_optimize_stats_dict` 和 `policy._forward_metrics` 的差额以字符串或 null 输出。将精确判定传到 `_close_deviation_check`，序列化放在判定之后，避免输出 Python 的 `Decimal(...)` 表示。保留旧 float 构造、位置参数、新计数默认值和其余字段的 JSON 类型。给 FX-43 的登记文字为“实现更正”。

## FX-39 第一步　状态、评分和角色应用

**读取入口。** #114 的 `_cmd_optimize` 将 `status` 分派给 `_cmd_optimize_experiment → _optimization_status`，后者在同一个 repeatable-read/read-only 快照中枚举谱系，已登记时调用 `_optimize_score_lineage`。`score --lineage` 经 `_cmd_optimize_score` 直达同一函数。在这个公共读取点布置诊断，在无登记的 status 分支填写相应状态。直接 score 同样在一致只读快照中比较角色、报价和样本。全表 status 共用一次 provider 快照。沿用 #114 的 `lineages` 列表、`freeze`、`shadow_predictions`、roles、forward_plans、last_run、initialized、forward_sample_count 和可选暂停状态的既有形状。

用 `run-scan` 同一 `RegistryProvider.from_connection(...).specs_for_lineage(...)` 和 `family_ids_for_prefix` 的扫描命名空间，把登记的 `family_id/target` 精确映射到支持的 `prefix/target`。例如 `club-soccer-recalib/match_winner` 对应 `club-soccer/match_winner`。比较登记角色的完整 SHA-256 和扫描取用的 `LineageSpecs.active.spec_sha256()`。显示可核实的模型版本和 fallback 原因。一次实际扫描以 `scan_runs.params.provider.specs` 的摘要和候选行的版本互证。预制市场包已载入报价，provider 能说明扫描选用的身份，不能据此证明另一次历史报价的计算过程。

| 真实 CLI 场景 | 输出与验收 |
| --- | --- |
| 已登记且身份一致 | 逐谱系输出 `forward_comparison.status=available`。确有候选且尚无可配对证据时，`pairs[].stats.n_markets=0` 和真实下一次观察进度可见。 |
| 登记与实际出价摘要不同 | status 与 score 均成功返回 `unavailable`、稳定原因 `pricing_spec_mismatch`，带登记和出价两份摘要及可证的版本。受影响的 pair 保留 spec/comparator，`stats`、前向 `next_look` 为 null，杜绝伪造样本零及到期倒计数。 |
| 已登记、当前扫描无该谱系或身份无法证实 | 分别返回 `scan_lineage_unsupported` 或 `pricing_identity_unavailable`，保留有据字段，缺失值为 null。跨同一前缀的不同族、同族不同规格、相同版本标签但不同完整摘要、注册读取降级均须覆盖。 |
| 空注册表、过滤单谱系、混合全表 | status 的空谱系保留 `initialized=false`、`roles.active=null`、fallback、空 pairs 和旧的 `forward_sample_count=null`。直接 score 对未登记/未知谱系沿用既有错误退出。混合表逐条报告可比性，单项不可用不影响其他谱系。各读取命令前后业务行、决策及角色历史一致。 |

**写入入口。** `_cmd_optimize_apply` 在 `registry_write_transaction` 内取得决策行，先处理未知 ID。对历史及新记录的 `PROMOTE`、`PROMOTE_PENDING_CONFIRM` 立即返回 1，stderr 给固定原因 `papersim_optimize_apply_promotion_unavailable`。检查放在 actor/confirm、config、工作树、状态折叠、配对、`_replay_decision_roles` 和 `_decision_roles` 调用之前。#114 的 replay 末尾也会调用 `record_role_events`，因此已应用过的晋级重放也须得到相同拒绝，角色写者调用数为零。将同一拒绝原因也用于角色分派，替换 `_decision_roles` 当前构造 `GUARD/ACTIVE[/RETIRED]` 的晋级分支，使首次应用与 replay 的角色规划均只为获准的结论生成事件。

`RETIRE` 仍可在无 config 时仅退候选，维持原判定和谱系配对校验。`CONTINUE/HOLD/BLOCKED`、无效或未知 ID 保留已有错误。`ROLLBACK` 的统计判定、受 config/冻结/暂停约束的首次应用、`_cmd_optimize_rollback` 的人工作者、历史前驱/时间点核验、旧 MANUAL 回执及精确重放沿用 #114 的分派、事务及幂等合同。新诊断位于描述性读取路径，历史回退继续通过原生判定及角色写者。

## 逐例验收与测试写权

| 现有测试文件或用例 | 原目的与这个节点的断言 |
| --- | --- |
| `test_papersim_optimize_policy.py::test_close_deviation_boundary_and_the_thirty_market_flag` | 继续覆盖 29/30、缺数据、阈值等号和稍高于阈值。原生 PG 用实际 NUMERIC 验证候选更好、差 −0.011、恰 −0.01、略高于 −0.01，以及旧绝对概率公式与新价差判断相反。示例可用 30 条候选的 CLOSE 0.53、影子成交 0.51，与两条现役 CLOSE 0.53、入场 0.50。两均值 0.02、0.03，等号应拦截。另用不同且产生循环均值的分母交叉核验。 |
| `test_papersim_optimize_policy_native.py` | 原生表加上两集合可不相交、有现役入场无影子、过闸无现役入场、无赛果但有 CLV、NO 和另一方向的 CLOSE、缺 CLOSE/成交价、迟到旧标记及同刻平局、重复联接、同版本异盘口和登记窗口外行。用独立 SQL 分别核总数、有效数、缺失、均值及差额。 |
| `test_native_full_round_register_shadow_decide_handover_rollback` | 原来前半程写 PROMOTE 并应用以建立 guard。改为纯 `decide_forward` 或在写边界截获到期结论并验证顺序/序号，给被拦申请提供合成历史决策，核两种晋级拒绝及历史零增。后半程从预置的原生活跃/guard 历史起步，保留真实统计 ROLLBACK 判定、应用和新的 PG 连接读回恢复原 active、guard 清空、被撤者退役及幂等结果。前半程的 fixture 数据与真实晋级应用明确区分。 |
| `test_native_club_candidate_without_pairs` 及 status/score 两入口 | 该用例原在再校准谱系中验证无行及全部不可配对时的零样本。保留两种数据，在可比谱系验证真正的零及 `unpaired`，保留未到期/决策与角色零新增。原再校准谱系另验证不可用。经 parser 覆盖单谱系和全表、无扫描映射、空注册表、可比与不可比混合。保留 `test_papersim_cli_optimize.py::test_status_reports_registry_fallback_and_zero_runs`、`test_papersim_optimize_runner_native.py::test_native_registration_write_path_and_status` 的兼容断言。 |
| `test_papersim_optimize_cli.py` | `test_apply_pending_confirm_requires_human_actor_and_flag` 从缺确认得专属错误，改为缺确认、自动 actor 及人工带齐确认均固定拒绝。原成功的 `test_apply_promote_writes_the_handover_batch` 改证 fresh/replay 两路、两种结论均零角色写。将 `test_apply_active_changing_requires_config_fail_closed`、`test_apply_refuses_frozen_and_paused_lineages` 的原防护断言转到首次 ROLLBACK。继续执行单候选 RETIRE、无事件、未知 ID 及 unfreeze 权限。 |
| `test_papersim_optimize_registered_runner_native.py` | #114 新增的 `test_native_cli_cannot_apply_decision_to_substituted_or_unproved_pair` 包含 PROMOTE、RETIRE、ROLLBACK 三例。PROMOTE 的旧错误改为固定拒绝，另两例继续证明错误配对被拒，全部读回历史无增。两种 FORWARD 测试输入使用预置历史或截获决策读取，避免为制造拒绝案例走业务决策写者。保留 `test_native_cli_rejects_stale_rollback_judgment` 的三种时序及 `test_native_cli_exact_rollback_replay_after_later_handover` 的自动/人工两例，包括角色行 ID、再次交接后的折叠、首次中断回滚和再次命中原事件。以上只使用一次性原生 PG 的合成历史，实际作业中只读。 |

O-5 另列。本节点需要写最后一行的原生测试文件。O-5 对应的注册输入、CLI 子进程及终态读回也由该文件持有，待写权交还后由独立任务接续。沿用 `test_papersim_optimize_metrics.py` 中可确定触发校准不收敛的样本，经评分卡与真实 `optimize run` 验证退出码、单个 `RUN_FAILED` 终态、无候选发布、重试读回同一结果。单类或完全分离的 `(None, None)` 属另一条分支。若发现 runner 异常逸出，记录失败、另行修复并送审。第一阶段交接报告 O-5 的实际状态与证据。

## 节点出口

独立 ZCode 在最终候选执行聚焦、全量 Linux 和原生 papersim 分区，记录实际执行的 nodeid、数量、跳过原因、首败及重跑。复核空注册表扫描逐位、四个冻结夹具、provider、自检 10/10、规则单一来源。沿用工作流既有原生文件清单。完成根规则要求的编译、`git diff --check`、敏感信息扫描、可达性比较及已有 CodeGraph 同步。计划和实际独立代码/阶段审阅回执随源码节点入库，各记审阅人、日期、精确 SHA、范围、发现和明确结论。原 FAIL 及原审者复审结果分列。

用普通合并接续最新 `main`，在最终 PR 头和当时实际合并对象核对 Offline verification、Native project PostgreSQL、Research paper bridge 成功，缺触发的 Native 在同一对象补跑。PR 描述和唯一登记写者记录三条运行编号、检出 SHA、测试改动映射和复审路径，合并后登记实际 SHA。届时结束合入前操作约束，产品对晋级的固定拒绝持续到 FX-39 第二步通过并合入。#118 在此节点之后按 MC-4、MC-5 接续。
