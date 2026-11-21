# FX-37　PR #79 独立事后复审回执

- 复审于 2026-11-21 13:10 UTC 完成，采用本机实际 ZCode guarded 只读会话 `sess_dca2e5f6-aecd-4cff-8530-d484daebf3c5`。
- 历史 PR 头为 `2f2cd5b3bd3ed577fd76b9c2a8f0432f2dbf0243`。原合并时缺少可核对回执的审计记录仍有效。
- ZCode 输出正文共 7,427 字节，SHA-256 为 `c8e61097be9910466f2dd1f3b98b2eebd184aacf588ed095264a58f71934223e`。以下保留该输出。

# 事后复审：MO-4（PR #79）数据集层（FX-37）

## 身份与独立性声明

本报告由独立 ZCode 复审者出具（事后复审）。复审者与 MO-4 当时的计划、实施、验证以及本轮资料收集均无关，仅以只读方式审阅附件，未执行任何命令，未改变任何文件。项目主线为仅模拟盘，此前已固定的审计结论保持原样，本复审不重开、不改写。

## 所审提交与范围

- PR 头：`2f2cd5b3bd3ed577fd76b9c2a8f0432f2dbf0243`（相对合并基 `e5c8ae8a875dece6169c6491eece90bdafc5451a` 的完整差异，即附件 original-changes.txt，共 3 个文件：新增 `src/.../papersim/optimize/dataset.py`（1033 行）、新增 `tests/test_papersim_optimize_dataset.py`（428 行）、`tools/wp_p4_history.py` 改为薄封装）。
- 实际合并提交 `4e6ffa871e9a0062e3b752855c6515dcffb8575c` 与统一落地复核基线 `2bdc4cccacc925ea8e6569c8865b90c0b0c671e6`：仅提供提交号，未附对应差异，本复审未核对这两处树状态（见证据限度）。
- 限定路径后续修复：附件 follow-ups.txt 明示"No targeted corrective patch in the audited range"，即所审范围内无针对性修复补丁。

## 本节点的具体责任边界

MO-4 的承诺是且仅是：（1）将 `tools/wp_p4_history.py` 的纯解析、编年与折叠逻辑作为**一个原样单元**迁入 `optimize/dataset.py`，工具端保留 `load_history` 文件读取与全量再导出；（2）新增数据集契约与三个构造器——`from_snapshot_bytes`（已登记快照字节）、`from_managed_rows`（托管库行）、`from_frozen_research`（冻结研究回归锚，仅锚定用途）；（3）泄漏防护（时间反演拒绝、future-row 注入不变性）、空输入与样本身份的失败关闭语义及对应测试。快照登记、runner 侧 origin 拒绝（MO-6）、intl/NBA/kraken 行级装配（MO-5b/5c/MO-10）均为文内**显式移交**，不是 MO-4 的交付项。

**与既有审核发现的关系**：P1-4（前向配对的模型版本错配）所涉位置在 `papersim/cli.py`、`optimize/provider.py`、`papersim/scan.py`；P2-1（市场准备缺口）所涉位置在市场包生成与扫描链路。经逐文件核对，本 PR 差异未触及上述任何路径，两项发现按原归属继续挂在扫描/评估节点名下，**不折算为 MO-4 的缺陷，也不得解读为 MO-4 当时的承诺**。另注意编号撞车：diff 注释中的"plan review P2-1/P2-2"（证据列、泄漏防护）是 plan-05 评审编号，与本轮审计摘录的 P2-1（市场准备）不是同一事项，本复审已分别对待。

## 核查结果（按关注范围）

**时间边界**：`history_at` 强制 `kickoff + lag < cutoff`（严格小于，lag 有限且 ≥3 小时，original-changes.txt:417-435）；`build_folds` 取官方与源时钟的最早点为 cutoff，前缀轮次不完备或越界即整折跳过并记录原因（:459-477）；训练安全钟取 `max(源, 官方)`、预测 cutoff 取较早源钟，重 reconciled 的场次逐条记 note（:216-237, :368-370）。托管行侧 DECISION≤CLOSE≤结算结果的时间反演拒绝、`event_start_at` 存在时 decision/close 严格先于开赛，均有对应失败关闭测试（:939-954, :1287-1310）。冻结 new-soccer 每行强制 `forecast_as_of < kickoff`（:992-996）。结论：时间边界在承诺范围内成立。

**泄漏防护**：三个构造器均有 future-row 注入验收（快照 ：1201-1221；托管行 ：1342-1352；冻结 ：1407-1416）；事件时回放的 `available_at=None` 是显式声明而非填充；FX-26 过渡期"无法判断 close 是否先于开赛"以结构化 `dataset_close_barrier_pending_event_start` note 暴露，不静默（:953-954, :967-968, 测试 ：1279-1284）。发现一处残余缺口见下。

**空输入**：冻结构造器对空表显式报 `dataset_frozen_rows_missing`（:984-986）；xG 快照零完成行合法返回 0 行数据集（kickoffs 为空时 first/last 为 None，:789-793）——两者严苛度不对称，但方向均为失败关闭或显式空，属可辩护的设计差异而非缺陷。

**样本身份**：赛程联接按 provider ID 或"唯一有向对 + 精确比分 + ≤48h 漂移"，轮次绝不按均分推断；重复 ID/对、比分冲突、身份冲突均抛错（:199-252）；托管行按 candidate_id 去重、身份取 condition_id/market_id/market_slug 首个非空，与 outcome 侧同序联接，有效结果复用 `store.select_effective_outcome`（:863-920）；冻结行键分别为 (league,id,market) 与 (espn_event_id,market,line,outcome,split,source)，source 维度区分多源同赛（:997-1007）。

**搬迁保真**：删除侧与新增侧的函数体逐段比对一致（utc/iso、parse_matches、history_at、build_folds、production_snapshot、inventory 及归档读取次序），封装保留路径逃逸防护与可选 meta/fixture 探测语义，再导出绑定同一对象并有测试钉住（:1474-1986, :1102-1114）。

## 后续修复是否覆盖承诺边界

附件范围内**不存在**针对 MO-4 路径的后续修复补丁，因此不存在"已承诺—未覆盖"的修复缺口可核；MO-4 自身文内移交事项（MO-5b/5c/10 行装配、MO-6 runner 拒绝、FX-26 列落地后启用开赛前屏障）在代码注释与 Dataset notes 中均有显式记载，属跨节点待办而非本节点违约。

## 真实发现

1. **低危（建议另开小修，不阻断）**：`from_managed_rows` 的"decision 不得晚于结算结果"仅经 CLOSE 中继传递性成立——CLOSE 缺失时，`decision.observed_at > 结算 observed_at` 不设防（outcome 检查整体位于 `if close is not None` 分支内，original-changes.txt:939-945）。结案后补录的 DECISION 行配上已结算 outcome 可绕过该屏障。鉴于：需特定上游行形态才可触发、有 CLOSE 时方向已闭、系统性拒绝由 MO-6 runner 承担，本复审不将其定为须阻断合并的实质缺陷，建议随 FX-26 列落地一并补一行直接校验。
2. **记录性**：托管行清单 `match_rows` 记全部候选数（含被排除者），与 observation_rows 的产出语义不对称（:964）；孤儿 evidence 行（无对应候选）被静默丢弃、跨候选同 identity+side 重复不拒绝——均受上游表约束保护，未见可独立致错路径。
3. **记录性**：`load_history` 注释称缺文件报错晚于指针完整性检查，实际 big5 缺失时先报缺文件；行为仍失败关闭，仅注释与实现顺序不符（:1866-1871）。

## 证据限度

本复审仅基于附件差异静态进行：未复跑任何测试（含 6900/3435 行锚点计数与固定摘要的声明）；未见合并提交 `4e6ffa8` 与基线 `2bdc4cc` 的实际树状态，无法排除合并残留差异；未见托管库表结构/约束原文，托管行侧的身份唯一性与 NOT NULL 假设以 diff 注释为据；行号均指附件 original-changes.txt。

## 结论

MO-4 在其承诺边界内（原样搬迁 + 三构造器 + 契约/清单 + 失败关闭语义 + 注入验收）实现与声明相符，时间边界、泄漏防护、空输入、样本身份四个关注点均未发现须另开修复的实质缺陷；残余问题为一条低危加固建议与若干记录性事项。P1-4、P2-1 属其他节点职责，与本节点无涉。仅模拟盘主线不受任何残余问题影响。

VERDICT: PASS
