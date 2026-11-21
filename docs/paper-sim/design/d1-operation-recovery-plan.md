# D1 时段内的取价恢复、公开终局登记和结算恢复 v2

供独立计划复审。仓库 `wmqfl861/polymarket-alpha-lab`，Linux，模拟盘。实现对照为 `wmq/main` 的 `11fb457c6e714844ec2ee1c0eb3c90dbd145eaa7`。上位计划 `d1-operational-run-plan-astra-v3.md` 的 SHA256 为 `a23ccaaeb40d4e813090244bd7416db9f4e53ddd7590fdb231acf5e9764af6f2`，原审记录 `d1-operating-plan-reviewed-v2-v3.json` 对 v3 给出 PASS。本稿细化其 §1、§5、§5.1。已核对同目录的 B2、B3、逐笔现金和 D1 计划以及正本文件清单，既有专项验收仍作为基线。

本次修订依据 `d1-operation-recovery-node-plan-v1-original-review.json` 的两项 FAIL。原 v1（`f3a58c18a813666b4b33dbfcc57b935977ceda1720d87c0f554b91fb8f047b9b`）及其原审保留。修订见 §4 的首次取价和 §5–6 的逐笔终局消费，交原独立审查者复核。

## 1. 从哪些正常入口接入

下文 `papersim/` 指 `src/polymarket_alpha_lab/papersim/`。现有四条业务 CLI 分别为 `python -m polymarket_alpha_lab.papersim.cli run-scan --managed --config … --repo-root …`、`capture-marks --managed --config … --repo-root …`、`settle --managed --team … --repo-root …`、`report --managed --repo-root …`。B1 产生可证明归属的候选、开仓及现金；B2、B3 是本稿改动的业务处理器；B4 验证既有报告仍可读取。`tools/d1_compare.py capture-inputs` 是独立的公共原件入口。

| 正本位置 | 已有行为 | 该节点补齐的缺口 |
| --- | --- | --- |
| `capture.run_capture`、`store_psycopg.candidate_capture_target/mark_capture_upsert` | 原 mark pack 逐字节注册，候选按 scan 和候选键定位；单条写候选 mark 及适用的 CLOSE 价格证据。普通 B2 没有传 `entry_id`。收尾统计 `marks_written` 包括相同内容的复用。 | 目标查询目前只返回 `candidate_id/event_start_at`。部分提交后需按原时段清单、实际买入方向和每份观测找回业务行及回执，覆盖无价和降级项。 |
| `settle.run_settle/plan_settlement/select_entry_outcome`、`store_psycopg.open_entries_for_settlement/record_automatic_settlement` | FX-29 在同一只读快照取当下 open entries、候选终局和开仓现金。普通 `--team` 给回执命名，现有 open 查询横跨团队。每笔 settlement 与唯一终端现金同事务。当前 PENDING 在进程列表里，遇后项失败可失去已见差额。 | D1 专用入口冻结目标团队、原候选集、现金读集和顺序。重启能找回已离开 open 集合的原 entry，保留原 PENDING 与锁内复核的异同。 |
| `d1compare.acquire.capture_inputs/read_capture`、`d1compare.journal`、`adapters.polymarket.fetch_resolution` | #112 的 v2 以 UTC 窗口和标签集封存事件发现页及两侧簿，有逐次请求、原件、真实收妥、缺证和受管 SUCCEEDED。同一范围复用原摘要。`_request_fields` 仅接受现有事件页和 book 查询。`fetch_resolution` 可另按 condition 取单市场终局，但没有进入该封存路径。 | 在原采集入口增加只针对已登记市场的取证分支。常规发现页不涵盖全部已关闭目标；发现批次的 SUCCEEDED 或传输齐全也不证明终局与时效。 |
| `store.MarketOutcomeObservation`、`store_psycopg.record_market_outcome`、`papersim/cli.py::_cmd_settle` | 现有 writer 按自然 `evidence_key` 插入、碰撞比对并返回原 `outcome_id`。普通 settle 在已有结果中择一。 | 另行调用 `settle --register-public-outcomes`，以实际受管处理器首次写入或明确复用。普通 settle 独立取证并消费。 |

可立即使用现有正式 B1 支持的包、明确 Yes/No（含标签与 token 同时反序）、可证买入 token 和已有 mark pack，开发及验收三个恢复写者。D1 的 `packbuild.py → replay.bind_candidate → scan.assemble_scan` 修复、尚无证明的 NO 绑定、队名/Over/Under 的规范极性及其他组包问题沿原 §8 裁定。发现历史 row 的 DECISION token 与买入侧冲突时保留冲突，不能改原 row 或换成 YES 簿。该分支获准并实际合入后，增加一组从其正式 `build-packs → run-scan` 到 B2、公开登记、B3 贯穿的相同原 token 验证。现有受支持输入上的恢复验收独立进行。冻结执行器、调度登记、日结、模型、F 裁判及完整运营天数均在各自节点。

## 2. 可并行派发的所有权与依赖

| 工作单 | 独占文件 | 可交接的成果与依赖 |
| --- | --- | --- |
| 取价 C | `papersim/capture.py`、`tests/test_papersim_capture_marks_native.py` | 现有 `capture-marks` 的 D1 处理、逐观测结果与原生红绿；依赖 O 的时段和取价读写合同，可独立于 B3 和终局源验收。 |
| 结算 S | `papersim/settle.py`、`tests/test_papersim_settle_store_native.py` | S1 为消费已有合法终局的恢复；S2 为读取封存原件、重建 typed observation 的登记处理器。两部分同一文件同一持有人顺序落笔。S1 只需 O；S2 的完整绿色需要 A、O 和协调者的 settle 分发。 |
| 受管 adapter 与 operations 单一写者 O | `papersim/store_psycopg.py`、拟增 `papersim/d1compare/operations.py`、拟增 `tests/test_papersim_d1_operations_native.py` | 单持读视图、目标身份、PG 锁、分项写入与找回 SQL、收据/快照编码和跨进程测试。C、S、A 只传各自业务材料，不另写 SQL 或复制 writer。优先交付 C/S1 所需接口和真实事务，另交登记的自然键与归属接口。 |
| 公开采集 A | 原公开采集持有人独占 `papersim/d1compare/acquire.py`、`tests/test_papersim_d1compare_journal.py`，只有现有纯解析能力不足时由此人改 `papersim/d1compare/journal.py` | 只补终局素材。复用已有封存和逐字节读回；公共适配器本身、#112 的通用发现覆盖无需重做。可与 C/S1/O 平行；A 的验收止于原件、来源和采集者，不预置结果。 |
| 协调集成 | 协调者单持 `tools/d1_compare.py` 的窄采集分发、`papersim/cli.py` 的 settle 模式分发、必要的工作流与其清单测试、用法及交付登记 | 合并 S2 时只接 settle 段；与 Phase B 持有人排定共享文件落笔顺序。新原生测试进入 Linux `papersim` 分区与 `tests/test_native_postgres_workflow.py::PAPERSIM_FILES` 相同位置。 |

计划通过即可在独立工作树同时写 C、S1、A 及 O。O 先给上述写者交付版本化的入/出材料和事务边界，再按最先可用的 C 或 S1 正常调用点合入对应最小 adapter 切片。O 可单独在原生库验收锁/回执原子性；发布新模块时须有实际生产调用者。S1 可先用有出处的已存结果验证恢复；S2 与 A 另用空结果表联合验收。无需等待完整 `run-slot` 或先做一个只有测试能到达的通用框架。每个可发布切片与其真实入口、原生测试和必要 CI 清单一同过门，其他文件的补丁由对应唯一写者接入。

## 3. O 的时段、读视图与提交合同

1. **操作身份。** 使用既有 `--operation-key` 和正整数 `--attempt`。D1 父键由已登记的影子实例身份、区间摘要、计划 UTC 时段、腿及实际命令/模式派生，在同一键下核对团队、源/配置摘要、截止、部署提交与目标清单。传入键只作查询，D1 保留命名空间必须命中相应的受管绑定并核同一实例；缺证或变化时记录可证的失败。既有非 D1 调用保持原键和回执语义。完整调度到位前，隔离测试只准备事前区间、时段和允许范围，三种业务读集由正常处理器自行产生。
2. **写前。** 复用 FX-03 干净提交核验。父键用有界生命周期的 PostgreSQL 会话锁串行调度；持锁会话跨短事务存活，在 `finally` 或进程断开时释放。STARTED 单独提交。锁下查原 PREPARED，若存在则从 `papersim.snapshots` 和 `command_run_events` 校验原始字节、裸 SHA、长度、父键和尝试。初次准备时用同一 MVCC 读视图提取确切目标、顺序、原输入、公开规则、真正读开始/完成及业务规划；随后以单独写事务把规范清单和 PREPARED 一起发布，再准许第一笔业务。FX-29 的 `REPEATABLE READ READ ONLY` 必须是独立事务的首条设置；可在持会话锁的连接上依次结束读事务并开始写事务，或使用同实例另一已验证连接。已有读后才能写的约束不靠时间字段倒推。若在 PREPARED 前死掉，数据库应无业务写；已错过窗口且无法证明原视图的尝试保留缺口。
3. **逐项目标。** 目标子键从原父键和规范目标身份派生，同一候选的多条观测分开，多笔 entry 不合并。每项用短外层受管事务，按固定顺序取得业务目标锁，再查原分项证据和业务行。B2 涵盖 candidate 的唯一 CLOSE 和原 mark 自然键；B3 锁到 entry；登记锁到 `MarketOutcomeObservation.evidence_key()`，涵盖两个不同父 operation 对同一观察的竞争。调用既有 writer、复核实际数据库行、写不可变结果见证和分项回执，统一由外层提交。内部 `conn.transaction()` 只是该外层的 savepoint。真实无价、PENDING、排除或已证冲突也有逐项证据；若 writer 抛错，先回滚该项业务，再单独提交该尝试实际见到的错误。进程直接死亡、从未写下的错误不得事后补造时间或代码。
4. **证据与回读。** 父 PREPARED 及分项见证在版本化快照中保存父/子键、attempt、计划/实际时间、应用角色与处理模式、完整部署 SHA、源摘要/身份、原有行与实际新写/复用的业务 ID、自然键和内容摘要。分项可用派生键的既有 phase 行建立可检索索引；其 `command` 应与普通四条 CLI 的父回执可区分，实际执行者另有可验证字段。入口的原摘要、B2 原覆盖字段及 B3 `settled`/`pending` 对外形状保留，分项不能在 B4/F 被多算为独立运行。`record_command_run_event` 按 `(operation_key,attempt,phase)` 去重，重复返回的 `run_event_id` 可为空且不比较 `occurred_at`；必须 SELECT 首行的真实 ID 和时间。PG 时间记录在哪个步骤采样要写清，事务内采样本身不能证明提交完成；已提交以独立连接可见性或实际确认见证为据。
5. **失联和并发。** 同键同时启动，后者获锁后读取首个持有者的已提交绑定。重复同一 attempt 只返回已经证明的原回执；真正重新处理递增 attempt。新 attempt 记录自己的 STARTED、原 PREPARED 引用及真实 RECOVERY_READ，保存首错、原 attempt/actor 和首次业务时间。业务和分项均不存在且仍满足原有时限才写尚缺项；两者齐全且内容全等才复用；孤行、缺配套腿、字节/身份/金额不合或来源冲突即逐项标出。同一 `(operation_key,attempt,phase)` 若已有不同内容，保全原行，另以原尝试及所提交摘要派生的冲突子键登记实况，避免同一个 FAILED 唯一键吞掉首错。父 SUCCEEDED 逐项核对原分母及明确终态；PENDING 是有原因的业务结果，未处理错误与不齐全的时段按原规则保留。已过时才读回原结果，另记迟到恢复的实际时刻。

## 4. C 用三份原观测验收跨进程恢复

从既有 B1 入参生成确有受管归属的候选，可以包含被拒候选；mark 的范围不以是否开仓筛选。以受支持 `scan_id` 或 `scan_operation_key` 加 `candidate_key` 定位，在同一视图一并取确切 scan/candidate ID、候选键、团队、market/condition/slug、side、原双方 token、DECISION 观测以及 `event_start_at`。两种 scan 引用分型、最终按真实 ID 比对。O 提供专用读取或向后兼容的调用；旧调用仍得到其约定字段。C 在 PREPARED 固定原 mark pack 字节和摘要、分项位置、原始市场/买入 token、来源、实际采样/收妥、目标类别和无价项。例如从现有 v2 封存清单按请求序号复核 CLOB 原字节、`asset_id`、可得的 `market`、来源/实收时刻，把已支持 mark pack 的 `snapshot_sha256` 指向该原件，在 PREPARED 另存原 pack SHA 和清单引用。单个 pack 自述时间不够；已有可证观察也只能在已登记的时效合同内使用。D1 专用重试复用原清单；现 CLI 会预读配置所列文件，若调用环境重建，可由调度/测试调用侧按已登记原摘要恢复成临时输入供同一 CLI，再由业务处理器核对 PG 原绑定。无对应原字节即保留缺项。

**首次 PREPARED 核价。** `capture.py` 的 `_snapshot_citation` 接受 pack 所给的 SHA，`capture_from_observation` 和 `close_evidence_from_observation` 直接复制四个价格。D1 首次调用须按已登记来源/观测位置读回原始书，重算 SHA 和长度，核市场、原买入 token、时钟，随后从该原件独立重建这次观察应有的价格。按既有簿规则验证价格/数量档、空侧和报价形状，从有效档取最高 bid、最低 ask；不能按数组首项或未成交的另一侧报价推断。`mid` 只有在同次来源有实值或该观察原约定可从两侧计算时才能取值，`last` 只用该来源中有证的末价。仅提供两侧簿的观察，未提供也未经约定推导的字段应为空。按已固定的来源类型与观测口径逐字段比对存在性及可精确入库的数值，包括空侧、全无价、原值 0、报价范围和交叉盘口。用十进制的实际数值核对可接受的等价表示；拒绝非法类型、非有限数以及需靠舍入或容差才能相等的值。依既有 store 校验区分 mark 与 CLOSE 可写范围，终端参考 last 保留其角色。数据来源及缺值口径不能由待验 pack 自行选择。

在首次业务写前，C 将重建结果同时与原 pack 声明、待传给 `mark_capture_upsert` 的 `CandidateMarkCapture` 以及存在时的 `PriceEvidenceObservation(CLOSE)` 比对 `bid/ask/mid/last`。完整相符的写入材料与来源见证才可列作 PREPARED 的可写项；被拒项仅留原材料、字段、实见值/声称值和原因。未声称价格的观测也走相同的缺值核对，不可把省略本该存在的值计作真实无价。重试从封存原件重建并复核，沿用同一比较。非 D1 的既有手工/历史 mark pack 继续走其原输入合同。

- **首次反例。** 在前一槽已产生身份可证的候选，原簿最高 bid 为 `0.55`，asks 有 `0.63`、`0.58` 两档。给普通 `capture-marks --managed` 的 D1 新键、`attempt=1` 一条合格赛前 CLOSE，market、买入 token、采样/实收时间、原簿 SHA 和其他字段全部正确，单独把 ask 填为 `0.63`。基线可将它写进两表。修订后保留真实拒绝及差异，独立 SQL 按该目标核 `candidate_marks` 增量 **0**、`candidate_price_evidence` 中 CLOSE 增量 **0**（原 DECISION 仍在）。另一新操作用同一簿的 `0.58` 验证两行精确落值；再分别变异 bid、被声明的 mid/last 及应有值/空值，含一侧无价和 0/1 边界。原有“首次成功、重试才换 pack”另测。
- **红。** 原处理器在第二/第三项失败后只保留前面的业务行和父失败；`candidate_capture_target` 无法证实 token。重试换 scan 下同名候选、同市场换买入 token、同秒同自然键换原件或报价、原分母外新候选，均应由新测试揭出。既有 `marks_written` 数值相同不能证明找回了同一行。
- **绿。** 三项目标在第一项之前、每一项外层提交前及提交后未收应答、父 SUCCEEDED 前分别停止。在另一进程同键新 attempt 下，逐条比较 `candidate_marks` 的 ID、自然键、候选/side/token、原时间、bid/ask/mid/last、来源、SHA；CLOSE 同查 `candidate_price_evidence` 的 `evidence_id` 和配套内容，PERIODIC 及无价项核零额外 CLOSE。原首写一次、未写目标至多一次有效提交，读回/新写/无价/失败/延迟及降级在原分母内都有归属。更换 pack 或新增目标要保留冲突与原回执；已存 mark 却无应有 CLOSE、另一 CLOSE 已占用等异常保持原状。
- **时限。** 原 FX-34 分类和 writer 的决策时序检查继续执行。只有在真实源观察和实际收到均严格早于 `event_start_at`，且最初封存/重试满足该时段已登记时限时，才可用原证据补写 CLOSE。恰好开赛、之后、开赛不明、收到已越界的赛前源时钟分别验收为 PERIODIC 或有因缺证；早已提交的 CLOSE 可以晚读回，新的赛后观测不能写成赛前 CLOSE。缺 bid 等字段保留空，真实 0/1 参考价与可执行报价依现行约束处理。

## 5. S1 用普通 settle 验收已登记结果的消费与恢复

S 在 D1 调用上以预定团队及目标范围复用 FX-29。除原 entry/candidate ID、成立时间、side 与 typed market 身份外，封存原视图实际读到的候选 outcome ID/字段与顺序、所选 `source_outcome_id`、`confirmed_at/observed_at`、真实开仓现金行 ID、精确 Decimal 金额/入场费及异常。由 O 复用当前查询并保留必要的未被选行，不能把返回的 `OpenSettleEntry.outcome` 一项当作整个历史读集。现有 loader 只为有效终局查 `_opening_cash`；D1 准备时在同一视图为原目标逐笔读开仓现金及反冲，包括暂缺有效终局者，保全原行与差额，同时保留既有规划选择的 PENDING 原因。真实查无和未取得的事实分列。普通旧调用行为仍由已有测试核对。

**逐笔择证。** 正本 `store_psycopg.py` 的 `_ENTRY_COLUMNS/_CAPTURED_OUTCOME_SELECT` 及 `_validate_automatic_settlement` 只取部分市场身份、规范 outcome、来源名和时间。D1 的普通 `settle --managed` 必须在上述同一个 FX-29 读视图内部完成以下核对，S2 给出的名单只是它要实际读取的证据之一。

1. O 为每笔原 entry 关联确切的 scan/candidate、B1 买入请求/入场 side、DECISION 的实际 `token_ref` 及其原来源/已证买簿、该市场有向的 affirmative/negative token 对。按 typed market/condition、已知 slug、条款和交易发生时的映射重建原买入 token。DECISION 若把 NO 错绑到 YES，保留这笔的冲突；同市场另一笔合法入场仍单独核验。沿用 `select_entry_outcome` 的既有身份、观测/确认、排序和重入场防旧结果规则，保留原候选（含早先终局）及实际选择的 `outcome_id/evidence_key`。对该项实际选择先核源行的双方 token、结果、原观察/确认、来源摘要及取证时限，再按下列来源证据证明对**这笔** entry 适用。若首选有未解冲突，保留 PENDING 及受影响的证据，不私换较有利结果。
2. 对 S2 首次写入的行，逐项 JOIN 到同一个 `outcome_id/evidence_key` 的事务内首写见证、原 capture seal、实际部署/actor/attempt，以及见证中这笔 `entry_id/candidate_id`、买入 token 与原胜方 token 的明确适用关系。后来的复用仅可引用已证首写并交出该笔在当时可证的关联；仅出现在市场目标清单或仅有同市场外键都不足。对**既有合法非 S2 来源**直接查其原生业务证据，例如原存 typed 双方 token、规范 outcome 与该来源原记录/快照或既有有效证明。能用原合同验证入场买入侧、胜方/作废、市场和上述时序者可由 S1 照常消费，不要求补造 S2 的登记回执或首次 actor。按原记录中可核验的来源合同和关联识别旧源，仅有 `source_kind` 名称或查不到 S2 回执不能充当旧源证明。仅缺 F 的首写归属时如实披露，不因此阻断证据充分的业务；实际来源证明不足、S2 链应有而缺失、或任一已知 token/方向矛盾时，该 entry 在 D1 为 PENDING。已知 S2 记录不可通过换报“旧来源”绕过缺失的分项见证。历史账和非 D1 普通调用继续按原业务合同。
3. 实际买入 token 属于该市场正确的一边即可，买入 token 与原胜方 token 相同或不同分别对应自然结算的赢或输。用该关系核 `plan_settlement` 得出的已有分类；VOIDED 按既有作废证据和可适用的参与 token 退款，无胜方。已支持篮子按原条款，UNKNOWN、未证买入方向或互相矛盾的公开结果保留 PENDING。保存两边 token、所选 ID/读视图、原证据键和逐笔结论。
4. **锁内必须再核。** O 与 S 将上述逐笔绑定随 `AutomaticSettlement` 的 D1 调用传入受管写事务。取得 entry 目标锁后，由 O 在调用现有 guarded writer、插入 settlement 和现金前，按原 `entry_id/candidate_id`、DECISION/买簿、原选择的 outcome、来源及 S2 首写/适用见证（或所用旧源原件）从同实例重新读取并逐字段比对，包括真实买入 token、双方 token、原胜方/作废、该 entry 的适用性、来源和时钟。共同使用那一次外层事务，再执行原 `_validate_automatic_settlement` 的开仓现金、手续费、结算类型/金额和单一现金腿保护。已有市场级匹配检查不能替代这次复核。若初读可用、锁内发现绑定不一致或证据不足，只为该项追加原读/复核差异与 PENDING，结算和终端现金增量都为零；不重选刚来的结果。重启核原见证与已提交业务行，只复用同一笔的已证结果。

- **红。** 原 open 查询重试时不再包含已结算 A，新的 entry 或晚来赛果会改变读集；B 的 `entry_cash_mismatch` 仅留进程中，C 报错时父回执不能恢复 B 的差额或 A 的实际读/写关系。
- **绿。** 同一时段三笔，A 已由既有 guarded writer 提交，B 现金不平，C 在其提交前失败。断开连接，在另一进程用原输入重试；A 的 `settlement_id/source_outcome_id`、正确分类与 Decimal 金额、唯一的 `bankroll_events.event_id/event_key`、入场现金和原读写时刻均相同。B 的 PENDING 和 `state/event_ids/expected_amount/actual_amount`（缺值按原义）在 C 失败后仍可独立查到；首次读与锁内 `_validate_automatic_settlement` 重验发现的差额有各自时间和依据。C 只处理原输入，最多产生一笔终端现金。再测已提交未收到应答、同键并发、不同操作同一 entry、后到更正/新入场和同市场再次开仓。对原读集调用 `select_entry_outcome`、`plan_settlement`、`record_automatic_settlement`，保留开仓保护、择证、VOIDED/自然现金和早退边界。新事实留给新时段，有成熟证据前的 entry 保持 PENDING。
- **逐笔回归。** 普通 D1 settle 单测含已证非 S2 来源（可结、首写身份沿原证据）、旧源缺方向或买入证明（PENDING）、同市场两笔各持相反且均能证明的 token（一赢一输、各循既有现金腿），以及相同市场再次入场与旧确认不适用。双向均合格的 S1 专项可用有出处的已存双向入场验证消费者，无需先交付待裁定的新组包。原生故障用例在 PREPARED 与 entry 锁之间用屏障暂停普通处理器；另一隔离会话仅向异常夹具追加一份关联同一入场、同一源簿而买入 token 矛盾的 DECISION 证据，保留原行。恢复进程后，锁内复读实际候选的证据及其身份，能发现与原绑定不符；另设健康 entry 对照。核两次读取的时间、原选择和分项原因，冲突项没有 settlement/终端事件。
- 独立 SQL 对每个已处理 entry 核对原结算与全部候选终端现金腿、入场现金、事件理由、归属、时间、币种、手续费、反冲和行数。只查余额或一个等额流水不足以验收。原 OPEN/PENDING 证据与整日现金平衡由各自回执承载。S1 可以采用清楚标明来历的既存 outcome 作输入；此用例证明消费和恢复。

## 6. A 与 S2 从空结果表验收到可归属的首次写入

1. **A 取原件。** 在 `capture-inputs` 上加终局专用分支，建议以 `--resolution-slot <已登记时段摘要> --operation-key <capture-key> --attempt <n>` 选择，沿用 `--repo-root`、窗口和 `--allow-public-fetch`。当前发现模式的 `--tag-slug` 及 v1/v2 范围键、回执和读回保持兼容。新分支从实例、区间和团队绑定的范围，在正常受管读取中冻结应核验市场、条件、双方原 token、关联 entry 集及来源，逐市场去重，覆盖已列入范围而零持仓的市场。调用真正的 `fetch_resolution(MarketRef(condition_id=…))`，在既有显式预算内走单市场 Gamma condition 查询与两侧 CLOB。这里按 condition 走可记录的 opener，避免 slug 路径另调 `GammaResearchReader`。仅给专用分支放行固定 condition 查询和原 book 参数，识别原 Gamma 行、双方请求 token、CLOB 正文中给出的资产/条件并查证矛盾；适配器把请求 token 填回结果，仅凭解析对象不足以验证正文。体育条款要求的完赛/含加时材料也须有来源和可重放原件，当前公共采集覆盖不到时按缺证处理。
2. 每次请求保留原始字节、序号、长度/SHA、来源时间、实际完整收妥、请求/失败状态、当期截止和所绑定的目标。同一正文两次观察保留两次时钟。逐目标记录 Gamma、两侧书及终态一致性，避免 `fetch_resolution` 在单侧失败后仍返回结构化结果被误判成确认。A 以自己的真实身份在 `papersim.snapshots` 和独立采集回执中原子封存并发布此版清单；仍缺原料的观察也可封存，成功封存与业务就绪分列。来源给出可用时钟时，逐项核来源时间 ≤ 完整收妥 ≤ 该时段截止；只允许实取时钟的来源标明依据，缺证不补时钟。发布后重启，新进程在禁公共传输下读回并验 SHA/长度、真实采集父键/attempt。发布前断连且未留存原件时，只有原时段仍允许才可重新取证，用新 attempt 的真实请求及实收时间；过窗记缺，不能让 S2 消费未发布的半批。既有 v1/v2 标签发现 seal 不能冒充专用终局 seal。A 的数据库增量只含采集证据。
3. **S2 登记。** 拟增正常调用 `python -m polymarket_alpha_lab.papersim.cli settle --managed --team T --repo-root ROOT --register-public-outcomes SEALED_SHA --operation-key WRITER_KEY --attempt N`，专用命令回执名为 `settle-register-public-outcomes`。在新受管进程核实例/窗口、SEAL 的类型和采集者成功回执，核每份原件并从原字节重放同一适配器语义；离线重放只能由已核原件提供响应。对照受管候选/entry 的 typed market_id、condition_id、辅助 slug、原标签与双方 token、原胜方 token；多个已知身份间的矛盾不可由 slug 掩盖。明确 Yes/No 需 closed、准确终端角价和两侧受信 CLOB 对照一致；无价、未封闭/缺一边、冲突、规则或方向无法证明均给出原因。区分实际观察、真实来源确认（未知留空）、收到、截止、登记实际时间；逾截止、早于对应 entry、重开仓、赛事未完/只到常规时间、纯竞猜作废分别核适用性。一市场多 entry 分别核适用，同一胜方 token 只映射一次。VOIDED 只接受条款明确的有效作废来源。无持仓市场保留真实覆盖和实际 writer；如业务范围要求 typed 结果，应如实入账并记录空关联集。
4. S2 构造 `MarketOutcomeObservation`，保存 typed IDs、双方 token、outcome/detail、`observed_at/confirmed_at`、真实 `source_kind/source_ref` 及原决定性源快照裸 SHA；见证中关联其余原件与逐 entry 的关联。由 S2 的 handler 经 O 调用现有 `record_market_outcome`。在同一个目标外层事务中按自然 evidence_key 加锁，核数据库完整 comparison、真实 outcome ID 和首写或复用标记，连同原 capture key/attempt/seal、部署 SHA、真实应用角色/处理器、writer key/attempt 及获证 entry 集写不可变见证。竞争的第二次登记只能引用已证首写。旧行仅有 ID 而无首写见证时，记录既存/归属缺证；同键改来源、时间（含同秒异微秒）、原件或极性则记录冲突。保持原行和原首写身份。
5. **普通 S1 随后从自己的 STARTED/PREPARED 重新读取。** 调用 `python -m polymarket_alpha_lab.papersim.cli settle --managed --team T --repo-root ROOT --operation-key SETTLE_KEY --attempt M`。读完成时刻在实际 attempt 内，而且早于或等于该项实际结算写入。对所选 `source_outcome_id` 逐 entry 执行 §5 的一致性读取、来源分支、真实买入 token 核验和写锁内重验，保留未通过者的 PENDING。登记收据、同一角色、外键或相同时间都不能替代消费回执或首写见证。给 Phase B 的材料是可读回的部署处理器、事务内首写、entry 关联及业务读写时刻。其既有 family 8 仅在真正的 B3 登记模式且 entry 链可证时使用 `settle` 归属；缺证保留 NOT_ASSESSABLE，已有证据显示矛盾/窗内旁路或导入时按原合同 FAIL。D1 不修改 `evaluation_f`，登记器和采集器不裁定 F。

**联合红绿。** 在受管测试库先用普通 `run-scan` 建立同市场的开仓 A 与 B，各有一条可核的开仓借方。公开市场本身的两枚 token、终局及原字节一致。A 的买入请求、side、DECISION 与实际买入 token 相符；B 用已有受支持包形状构成逐笔矛盾，例如买 NO 而该基线的 DECISION 引用 YES，或 B1 为 B 留下的实际买入 token 与同一市场原簿的对应 token 矛盾。将 B 放入原时段待核清单并保留 B1 实际产物。独立观察者先查目标 `market_outcomes=0`、两个 entry 的 `settlements=0`、终端现金均为零。基线的市场级 S1 用同一既有结果会按 side 为两笔规划结算，缺少买入 token 核对；在 S1 专项用这个基线反例保存红证。全链绿色按公开采集 → S2 登记 → 普通 S1 结算三个真实独立进程执行，随后运行既有 B4。S2 按一个市场登记一个 outcome，首写见证列明 A 可用、B 的绑定冲突；S1 必须独立读出两笔目标并按 §5 核验。独立 SQL 和分项收据证明 A 只有一条引用该 `outcome_id` 的 settlement 和一条金额正确的终端现金，B 得到有因 PENDING、**零 settlement、零终端现金行**，两条原开仓借方均各自保持一条。分开存采集者、登记首写者、S1 实际读写及两笔不同结论。分别重启/重试采集、登记、普通结算，并让两个不同登记键竞争同一观察，原 outcome、首写、A 回款 ID 和 B 的零终端现金不变。

| 联合负例 | 逐步预期 |
| --- | --- |
| 源市场、condition、书的 token/方向不符；正、反数组和缺一侧 | 原件与覆盖照录；显式反序且对应关系正确可过，矛盾或不完整不产生可用终局，该入场仍 PENDING。 |
| 截止后才收妥、确认早于对应开仓、重入场、体育完赛/加时或作废依据不足 | 未证项无有效结果/回款；已有同市场的合格旧 entry 与不合格新 entry 分别可查。 |
| 同一键换输入、缺首写见证、另一实际 writer 先写入后被普通 settle 读到 | 身份内容冲突保留原件；既存合格结果的可消费性和首次写入归属分别出证。后来的消费或复用不转移首写。 |
| 多市场混合；在任一项提交前、提交后/父回执前截断，另进程恢复 | 已提交的原 outcome、分项 witness、真实 actor 和现金逐项同 ID；未提交项只在原清单上处理，失败/缺证与成功同时可查。 |

## 7. Linux 原生 PostgreSQL 与发布收件

- 每个并发泳道占用自己的临时实例根、端口、datadir 和测试输入目录。采用 `tests/test_papersim_d1compare_archive_native.py::_isolated_project` 的 `ProjectPostgres`、本地已校验 PG18 前缀、初始化、项目迁移目录及 `migrations.apply_pending_migrations` 路径。该 fixture 目前只建有 Git 对象引用的临时根，没有检出真实 HEAD；B1/B2/B3 的 FX-03 需要在一次性干净检出中初始化同一受管根，并从该受测提交载入模块。包和诊断在检出外，运行时目录按项目约定。不要拿另一 Git 根供应提交号。每次子进程用该根 `--managed → connect_managed`（身份三元组、`pal_app`、已有 `validate_local_postgres_dsn`），owner 仅做临时建库、迁移或受控故障夹具，独立观察者以受管应用只读事务查同一实例。全程保留纸面三个硬标志。原测试的显式 DSN、轻量 Git fixture 和 SQL seed 可保留既有覆盖，不能替代本链正常进程。实例、连接和故障证据仅输出脱敏身份/摘要和行键。
- 运行环境沿用项目锁定依赖，启用 `POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES=1` 和本机校验过的 `POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX`；已有 B2/B3 下层测试所需的 `PAPERSIM_NATIVE_MANAGED_OWNER_DSN` 仅由隔离测试启动器提供，不读取旧项目库或导出连接值。测试只在公开传输边界提供可分发、具真实字段形状的原始字节，保留请求次数、顺序和失败；正例用真正的解析、封存、登记、结算、SQL，禁止以构造好的 typed outcome 或直接写结果表替代。负例中的受控异常行独立建库并标明夹具来源。故障借用测试进程边界、PG 锁/屏障和独立观察连接定位，对写前、提交后未应答及跨进程/一次真实库重启分别取证；产品 CLI 不暴露故障注入开关。
- 写者先在精确父提交保存该缺口的首个红例、断点、退出状态和独立 SQL 行数，再在候选提交跑同例及既有回归。例如各自使用 `python -m pytest -q -rs tests/test_papersim_capture_marks.py tests/test_papersim_capture_marks_native.py`、`python -m pytest -q -rs tests/test_papersim_settle.py tests/test_papersim_settle_store_native.py`、`python -m pytest -q -rs tests/test_papersim_d1compare_journal.py tests/test_papersim_d1_operations_native.py tests/test_native_postgres_workflow.py`；集成复跑 B1/B4 原生、受影响的适配器/入口、报告回执和部署兼容。核原生目标实际执行数、失败、跳过与 JUnit，缺 opt-in 或跳过不能判绿。归还基线与候选完整 SHA、隔离实例的脱敏身份、命令/真实退出、attempt/目标/父子摘要、逐项行键和精确现金对照、首错与真实恢复时间。
- 按根 AGENTS.md，各发布节点另做明确标记的自检、Linux 全量、编译、`git diff --check`、已索引工作树的 CodeGraph 同步及前后静态可达性、冻结边界与跟踪改动的脱敏检查。能从正常入口到达；`audit_static.py` 的不可达数与父树比较。由全新独立只读原生 Sol/max（fast 关）对计划、最终代码和适用阶段分别出明确判词；修订按原审规程回验。协调者按既定节点政策办理聚焦提交、推送与 Linux offline/native/research-paper 检查。此稿仅供计划审；工程红绿、真实前向 slot、D1 计日、D2 切换和 F 窗口各自另取实际证据。
