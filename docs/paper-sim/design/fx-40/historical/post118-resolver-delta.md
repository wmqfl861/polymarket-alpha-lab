# FX-40　#118 合入后的受管输入接线增补

Linux 正常模式。下一名原生 `gpt-6.2-sol`、`reasoning_effort=max` 实施者只写 `src/polymarket_alpha_lab/papersim/prepare_inputs.py` 与 `tests/test_papersim_prepare_inputs.py` 两个新文件。基线工作树为 `/data/pal-verification-20261120/worktrees/pal-post118-fx40-prep-00199b24`。本增补由原生 Astra/max 拟订，新契约经实际 Linux ZCode 独立 guarded 只读审核，取得 `VERDICT: PASS` 后派工。实施、测试执行和独立源审分人，均关闭快模。

## 可以直接使用的证据

- #114 `d216059339dba9c3ca34d65813e9cf2ba98a0dd5` → #121（FX-38 与 FX-39 第一步）`01a3d6d208fb7a22886f3cc0b54f4b6499d83400` → #118 `00199b24d38d5d9334ec7eb07ca8141db33852da`。两段祖先检查通过。#118 于 2026-11-22 02:07:39 UTC 合入，树为 `e7b2335c0f9baea51a774eacc709f878b0875ab9`。直接采用该主线父提交与已审的本地纯切片，无须预先重发 #118、发布纯切片或发布 NBA 候选。[#118 合入回执](./pr118-retained-publication.zjsh40hl/landed.json)
- 原方案和数组增补都已获 guarded `VERDICT: PASS`。两份待提交文件的实测 SHA-256 为 `prepare.py` 的 `fa68e8f682b8c7396bb46e43a0f785b99827d6f2e147383fef9178744f27f907` 和 `test_papersim_prepare.py` 的 `bc9415fa42d77af7f574e36cdac5193acb718ff0a7fa39efbc0914951e7ae297`。实际 ZCode 测试会话 `sess_bcdb9895-090a-42a8-8128-f7cf797fc10e` 记录 14 passed in 3.26s。独立 guarded 源审末行为 `VERDICT: PASS`，其 `result.txt` 的 SHA-256 是 `e9c174efd8756a4004d3620e71e60437be44fee60b520e3b120abf9a8488d98f`。沿用受审 `prepare.prepare_market_pack`。14 项覆盖合成纯计算，正式入口与四冻结对照待集成验收。[原计划](./fx40-market-preparation-plan.md) · [原计划审核](./fx40-market-preparation-review/result.txt) · [数组增补](./fx40-array-shape-plan-addendum-astra.md) · [增补审核](./fx40-variant-array-plan-rereview.4D4Paw4A/result.txt) · [纯审原件指针](./fx40-pure-source-review-path.txt) · [测试原件指针](./fx40-prepare-first-focus-path.txt)

## 两个新文件的交回条件

1. **读取与返回。** 提供一个窄入口，建议 `resolve_preparation_inputs(connection, *, repo_root, config_bytes, input_set_sha256)`。只读隔离项目库事先登记的材料。用 `store_psycopg.connect_managed` 认证回环应用连接、项目根及实例身份，在同一只读可重复读事务中查快照和回执，传 `_connection=connection` 给 `acquire.read_capture` 复核绑定。返回清单/配置摘要、时段/截点、批次/日志、谱系、类型化目标、政策身份和排除证据。入口只收校验值、配置字节和受管上下文，拒绝任意 DSN、外部地址及自填报价、证据或模型字典。
2. **清单与真正的 #118 身份。** 校验登记 kind、生产者、schema、允许字段、清单/原配置/各正文的 SHA-256 与长度，核对队列、时段、目标及 UTC 截点。对 v2 `d1_public_capture_manifest`，按 scope 摘要查 `capture-inputs` 第 1 次 `SUCCEEDED`，核对调度起点、配置摘要、唯一输出批次、封存/登记时序和 `paper_only/report_only/readonly`。封存后的回执证明登记，原请求实收与来源时钟证明截点可用性。用 Gamma 页序号和事件/市场位置复核 event、condition、market、slug、标签与原 YES/NO token 对，用买入 token 对应的 CLOB 请求、正文资产、簿 SHA 和档位取价。按请求保留时间和序号，避免同字节多次到达被快照去重合并。
3. **只接能从原件证明的目标。** 以登记的有限类型/字段映射匹配配置和 Gamma 条款指纹，逐项证明队列、市场种类、比赛/球队或资产、模型侧、公众买入侧、开赛/到期和结算口径。凡需盘口/障碍，须从原条款或逐字节绑定的结构提取同一数值。新闻、费项、阵容、桥接及对应时钟按有出处的材料填写，模糊名称、数组位置或自报条款摘要无法单独证明映射。

   - 足球以 Understat 联赛/赛季原响应，经适配器解析、校验及 `assemble_xg_snapshot` 重建冻结规格的 `SoccerInputs`。另一条路径须由独立登记的生产者映射相互绑定原件 SHA/位置、双方球队及所用阵容、赛季/比赛、投影版本、规范投影字节的 SHA/长度和截点。适用的 FotMob 投影也须核对这些绑定。核对构造器实际消费的 xG、完赛和球队历史时钟，能检出更换球队、原件或投影。
   - 终值加密用登记的现货及闭合 K 线原页、资产/交易对、时段、障碍与 `yes_when` 重建 `CryptoInputs`，由纯切片复核并只换向一次。触线保留 `papersim_prepare_crypto_touch_state_unverified`。有从开市到截点的完整可评估路径、合同触发口径及原件身份后另接 `touch_probability`。篮球/NBA 属 FX-41 优化泳道，FX-40 对篮球和其他未支持目标返回来源、身份及原因。
4. **簿龄与失败默认值。** 沿用 `capture._d1_book_sources` 所消费的登记契约。验证同实例、时段和捕获摘要上的政策原件、适用时钟与登记回执，再取 `slot.scope["book_age_policy"]` 的 `max_source_age_seconds`、`max_receipt_age_seconds`。同一清单可引用适用的既有登记。B0 用封存的决策截点判断。#118 的 `NOT_ASSESSABLE` 仍允许另有有效政策的真实目标。找不到该时段获准的登记时传 `None`，仅对受影响目标记录 `d1_capture_book_age_policy_unregistered` 及批次/簿引用，交运行方查明。无数值默认、CLI 覆盖或补造政策，旧文件包沿既有入口。
5. **区分完整性错误与个别缺项。** 声明的原件丢失、正文被换、错误实例、成功回执冲突或已声明身份互斥，整次失败。完整原件不支持某目标时，按稳定顺序交回队列/目标、原因、原件与请求位置，其他目标继续。缺候选身份时记观察覆盖。已可重放的构造失败保留 `construction`，有报价但闸门拒绝的保留普通候选。需入 B1 而缺编解码的交后续分派，缺失值不得补造。
6. **交回两文件与专属用例。** 交回父 SHA、两文件 SHA-256 和调用签名。专属用例从登记原件走到 `prepare_market_pack`，覆盖足球/终值正例、倒序标签/买 NO、同 SHA 不同实收、替换投影/球队/线/时钟、合法与缺政策、混合目标、错误实例及篡改回执/正文。另派实际 ZCode 在隔离数据上记录父提交首个失败、候选结果和重放零外部读取，再按两文件精确内容独立源审。

## 正式接线的持有人与顺序

| 后续写权 | 实际入口与验收 |
| --- | --- |
| 上游采集与登记 | 对缺少生产者的输入集/模型原件沿现有采集入口补登记。显式启用既有公共只读选项时才补采。通过来源、状态、大小和共享 `FetchBudget` 后、首次解析前，经 `snapshots.register_snapshot` 提交并核验原字节和时钟，逐次记录请求，再封存清单。登记失败时解析次数为零。已登记材料直接离线重放。 |
| B1 接线与写者 | NBA 当前独占 `papersim/cli.py`、`papersim/store_psycopg.py`。等相应文件正式交还，在确切父提交整合双方改动，接 `_cmd_run_scan`、`scan.run_scan/assemble_scan`、`replay.bind_candidate` 及失败分派。先查同键绑定和成功结果。首次只载入一次 `RegistryProvider.from_connection`，沿原 provider 的空表/降级行为，每谱系仅取一次 `specs_for_lineage`，同一 `LineageSpecs` 供报价和 B1。回执绑定输入/配置/批次、截点、政策/原件引用、包 SHA/长度、真实规格 SHA/版本及可恢复角色视图。B1 核对报价/规格，DECISION 记买入 token。同键同绑定的并发、断连重试与角色变化复用业务行和现金分录，同键换输入报冲突，下次独立操作才重选。 |
| B1 数组 | FX-40 单独持有 `tests/test_papersim_scan_store_native.py`。准备包交有序 JSON 数值数组。按已签增补，仅在 `_candidate_params` 的 `variant_quotes` 参数绑定处用 `_json_text` 或等价接口处理列表/合法文本，排在既有验证和 payload 哈希之后。用不同扫描身份跑非空列表、等值文本、空列表、历史文本 `"[]"`，另一个受管应用连接核对 JSONB 数组类型与逐项值。保留旧摘要、同键换形态冲突及非法对象拒绝。 |
| 受管命令与 D1 | 默认 `run-scan` 按原配置的 `queues[].pack` 读取。新模式用下列命令，仅对输入集选择的队列准备，同一队列同时供文件包和输入集时明确报歧义。内存包沿 `records_from_pack → run_scan` 进入现有 B1 写者。父提交的 `tools/d1_compare.py command_run_new` 通过门禁后仅打印 argv 并返回 2。其持有人在既有 Path N 门禁内执行相同命令，传递真实退出码与 B1 回执。报价计算在受管 papersim，报价持久化仅由 B1 完成。D1 保存比较所需的引用。 |

```bash
python -m polymarket_alpha_lab.papersim.cli run-scan \
  --managed --config "$SCAN_CONFIG" --repo-root "$PROJECT_ROOT" \
  --prepare-from "$INPUT_SET_SHA256" --operation-key "$SCAN_KEY" --attempt 1
```

**出口。** 专属模块可与 NBA 在不同文件上准备。正式 M2 推进遵循 11-21 执行提示词，先完成优化线第一阶段的各项出口，交付已合入的 `docs/handoffs/2026-11-19-optimizer-phase1.md` 及终点提交，随后继续，无需等待外部增量复审。FX-40 的最终候选从登记的原始响应出发，在隔离项目库经上述 CLI、Path N、独立连接回读至少一例足球十变体和一例加密 A/B、实际买入 token、模型版本和报价。另证 B2 共用同一批次，B3 使用后来登记的独立终局批次，及同键并发/重试。[11-21 执行提示词](/data/pal-verification-20261120/worktrees/pal-post118-fx40-prep-00199b24/docs/handoffs/2026-11-21-progress-review/03-local-agent-prompt.md) · [#118 后并行写权](./fx41-nba-v2-post118-managed-release-astra.GS8QaujB.md)

在空注册表下，对 `regression_r23_soccer`、`regression_r19_crypto`、`regression_gate_arithmetic`、`regression_banned_path` 分别以独立的历史构造器与原输入生成期望请求/构造失败规范字节，比较实际 SHA、长度、首处差异及十项自检完整输出。按原计划进行两进程同封存输入的完整包逐字节比较，并报告缺失哪一份历史材料。完成正式候选的原生分区、全量、编译、自检、独立复审和三项 Linux 工作流后归档。只有 FX-40 的实际主线合并时刻之后的合格真实双路径时段计入 D1 七日。FX-39 第二步随后按独立节点验证换届、换回和空表回归，第一步晋级拦截在其门禁通过前有效。
