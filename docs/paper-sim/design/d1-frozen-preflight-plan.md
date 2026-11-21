# D1 冻结执行节点修订稿 v2（送同一原独立审）

**不计日的普通 CLI 预检可以先行。** 它由项目父端读回两 pin 归档和已登记公共批次，按已见原页封分母，给出可独立查询的执行就绪或拒绝原因。无须 S1 排程记录、计日授权、补齐全部来源或启动冻结脚本。两腿整脚本前向运行的隔离和补源证据仍待实证。当前 D1 为 0 个完整 UTC 日。下文的 L 指冻结旧侧，N 指当前受管纸面侧。

原审 `d1_operating_plan_review_sol_retry` 于 **2026-11-20 22:52:22.391336 UTC** 对首稿 `d1-frozen-execution-platform-plan-astra.md`（SHA-256 `008e694c02225fec91b725803be189f485e80129a549ae71e0d331fc43a29214`）给出 **FAIL**。回执 `d1-frozen-platform-v1-original-verdict.json` 已认可原件读回、批次核验、实际分母封存和可归属的失败回执。两项发现为把字面路径设为平台必要条件，以及普通预检与正式 slot 的五项前置混用。首稿及回执独立留存。本稿依 11 月 19 日交接 `06-local-agent-prompt.md` §8 修订 v3 §§1–3，交回同一原审。

## 1. 已有入口和证据

| 已核对象 | 能直接复用的能力 | 证据边界 |
| --- | --- | --- |
| 已合 #110，合并提交 `5d49666757d520ff55832338f856a91dd761affe` | `tools/d1_compare.py stage --leg all` 以本地两 pin 的 Git 对象和六个指定镜像脚本登记清单。父进程用 `frozenrun.read_staged_archives(manifest_sha256, repo_root=…)` 从项目 PG 验长度、SHA 和绑定。对零散留存的 stage-v1 原件，沿用 `import-original` 的独立原摘要和原 tar。`restore` 在临时叶目录演练还原。 | 库 tar 在受管 PG。脚本字节由 `plan_chain` 从 `docs/paper-sim/server-mirror` 重读，逐个比 `MANIFEST.sha256` 和受管 leg 清单。私有目录由 `_private_work` 建立、加租约和回收。`--staging-root` 为 CLI 兼容参数，实际可持久读回的对象在 PG。 |
| 公开采集候选 `a7c70ba2a15ac90586cd329f7655c12a7d6fc201` 已获原代码审 PASS | `capture-inputs` 的父采集器封存尝试及原始响应。`d1compare.acquire.read_capture(repo_root=…, batch_sha256=…)` 交付已核摘要的 manifest 和按序的 `JournalEntry`。读回包括已保留的迟到、无效正文，失败而没有正文的尝试留在 manifest。既有原生测试从另一进程读回原字节。 | 这里引用该候选的实际接口。实施时核对合入基线与候选的实现关系。当前版本仅接受 Gamma `/events` 和 CLOB `/book` 两种固定 GET。当有目标簿时，即使 `polymarket_transport_complete=true`，年龄政策仍是 `NOT_ASSESSABLE`，v1 的原有结论也需按原版本解释。`read_capture` 核内容。运行归属还要独立核对指定实例、scope、`capture-inputs` 的成功事件及其输出摘要。 |
| 两个 pin 的正式应用接口 | 两个版本的 `papersim.cli` 仅有 `selfcheck/schema/validate-dsn`。`selfcheck` 可在相应可信检出中，用同一 pin 的 `tests/fixtures/papersim` 复核冻结回归锚点。`soccer_model.fixture_variants`、`gates.evaluate_candidate`、`risk_policy.sizing_tier` 与 `crypto_model.{touch_probability,terminal_probability,conservative_edges}` 可按公开函数接口用明确输入独立核算。 | `stage` 只归档 `src`，原版 selfcheck 另读上述 fixtures。逐文件比对发现足球的 `risk_policy.py` 与 #110、a7c70ba2 同字节，而两个 pin 对应的 `gates.py`、`soccer_model.py` 或 `crypto_model.py` 与当前版本有差别。调用证据须写实际载入路径、blob/SHA、依赖来源及入参。当前 `papersim.cli replay` 重算受管 N 候选，优化器的 `club-soccer-frozen` 包装当前模型，均不能证明旧脚本当天调用过哪项函数。 |
| 独立工作树 `/data/pal-verification-20261120/worktrees/pal-d1-frozen-forward-20261120`，基础 `af4ff553262a7ee532e36e1b0d8ba4939522f4ba` | 未提交的三个文件为 `papersim/d1compare/frozenrun.py`、新增 `papersim/d1compare/frozenexec.py` 和 `tests/test_papersim_d1compare_frozenrun.py`。前两者中的 `papersim/` 指仓内 `src/polymarket_alpha_lab/papersim/`。原型有按序读输入、子进程真实结果及两腿阶段观测、PG 封存与读回样例。 | 新主体 1,081 行，SHA-256 为 `3b86ee3ae8d8e9de0becaa360004405337db9c3b19742be72ba7d1954d56f145`。该基础自身没有 `acquire.py`。`run-frozen` 现有 CLI 未传 batch，也未输出 executor 回执。真实调用到底层 driver 的环境结果是 `RTM_NEWADDR … Operation not permitted`，worker 尚未启动。有原生 PG 的试验仍替换了 `_read_capture`。成功的算法用例使用合成批次、测试路径/导入覆盖及 socket 审计钩子。已有的成功分别证明库/原件与算法样例，未完成普通 CLI 的成功端到端运行。 |

本次还用本地 Git 的 #110 原 blob 与工作树逐字节核对六脚本及镜像清单。三棵相关树的镜像 tree 均为 `fb31fa54618a3e191e026c3faf1092620f139c72`，清单文件 SHA-256 为 `37fac2e0416deaf314acd0547d32157d3f62e3652d9c44e0fd611c8471a1862b`。足球 pin `1a2760bc411f1157647be6ec87a57cd368b2eb9a` 的 v2 tar 为 95,262,720 字节、SHA-256 `f4e8dde6e5ec42c5dbb6bbffe632b680e921f5c2651393cdba2e7ddd79e03931`。币 pin `195017196f90287128f27af9ae832ea42864e5dc` 的对应记录为 95,211,520 字节、`cf14de7c221f10558dafc61aa8e6274dd9d6331294a354fe8c77b97b11f95d67`。原始旧 tar 另按其独立原摘要处理。仓内 `MANIFEST-NOT-MIRRORED.md` 已把逐轮响应、榜单、Big Five 合并数据及指针列入未镜像清册。脚本的存在不证明持有当期数据。

## 2. 普通预检、正式入口和共用的业务合同

1. **拟增 `run-frozen --preflight`，作为第一项独立交件。** 从当前普通 `tools/d1_compare.py` 的 `command_run_frozen` 首先按显式 `--preflight` 分派。预检处理函数仅使用项目受管读写与私有 staging，绝不进入 `_refuse_gates`、`execute_designated_chain`、旧脚本导入或子进程启动。以下变量由工程调用者按已有项目根、暂存参数和两份已登记摘要设置。

   ```sh
   python3 tools/d1_compare.py run-frozen --preflight --leg soccer \
     --repo-root "$D1_PROJECT_ROOT" --staging-root "$D1_STAGING_ROOT" \
     --manifest-sha256 "$D1_MANIFEST_SHA" --batch-sha256 "$D1_BATCH_SHA"
   ```

   预检接受受授权使用的项目影子或一次性工程 PG 作为证据存储。先由 `plan_chain` 在租约内经 #110 读清单/原 tar，重新核验镜像脚本。再调用真实 `acquire.read_capture`，用同一实例独立核 `capture-inputs` 的 scope 和成功事件，回算每份已保留字节的裸摘要、长度与观察序号。以该批次自身已登记的时间/标签、已见市场和缺页记录建工程分母。缺正式时段、簿龄政策、模型文件或第三方来源时照样封存已见事实，必需而未知的部分列为 UNKNOWN。无需 `--authorization`、`--interval-sha256` 或 S1。若同时指定正式运行参数，拒绝混用并保持零启动。

   回执独立记录 `mode=preflight`、`qualifying=false`、`day_credit=0`、`child_started=false`、真实读回状态、分母 SHA、实际覆盖和准备判断。已验证的证据若显示准备不全，CLI 以非零退出并打印已持久化的回执摘要及缺项。没有执行能力凭证时记录“能力未证”，有确切旧启动失败时引用其原任务、时间与 errno，当前预检只记录本次未尝试启动。若检查项目均就绪，预检自身可返回 0，进程、旧脚本和正式比较的状态仍为未执行。一项原件失验时明确失败，无法建立可信分母时留空，PG 本身不可用时只报告收据未落库。

   **正式比较保留原入口。** 不带 `--preflight` 时仍由 `_authorization/_refuse_gates → execute_designated_chain` 处理，含同一归档/批次引用并绑定 v3 的已登记区间和真实 slot。五项分别为 B2/B3/B4 合并、G7 修复、D1 泳道已审合并、S1 安装记录和执行基线。核对 `DayRunAuthorization` 每项原始出处及实例，而非工程占位字符串。只在此正式路径要求它们。比赛窗口、标签、币对、合约范围、分页终止、簿龄、版本选择随正式区间事前登记。预检事件有专属模式/操作键，无论提交、重试或状态良好，v3 的 slot 和计日读取都不能认领它。
2. **封分母，再运行 L。** 在读原页之后、任何模型输出之前，登记每个原始观察的序号、URL/参数、SHA/长度、页内 JSON Pointer、事件/市场/condition、原始条款及其摘要、标签与 token 原顺序、玩法/盘口线、经济方向、比赛或到期 UTC。原料全集保留重复观察、跨页身份冲突、未解析行和单边发现。按事前规则建立支持双计、单边可表达、技术未覆盖、仅采集四类，同时列出每个市场/侧/阶段应有的字段。缺页或无法枚举的那部分市场数标未知，保留已见下限和缺的请求。只凭成功行无法推断其数目。币的远期/辅助市场、足球只展示联赛及篮球的归属按已登记范围决定。
3. **应用层可先交件。** 复用 `journal.gamma_page_index/gamma_outcomes_by_token` 处理已支持的数组页和标签配对，沿用 `acquire` 的身份冲突和 `books[].request_sequence`、`journal.book_from_entry` 的原始双边簿阶梯，补核条件 ID、原 asset、原始时间、档位有效性。数组下标并不定义 YES，需以源条款核对 Over/Under、主客队、平局处理、touch/terminal、above/below、线值和实际 token。每次读簿同时记录源观测和真实收妥 UTC。Gamma 无源时刻依已有合同标 receipt-only。核对 `source_observed_at ≤ utc_fetched_at ≤ decision_as_of`，并分别应用已登记的源年龄和接收年龄约束。同正文不同时间仍是两个观察。相同 URL 多版本须按前置选择规则定位原序号，歧义保留全部尝试并停止该项。L/N 共用同一输入清单、同一批次截止和指定原观察。
4. **只给实证阶段记覆盖。** 输出以事前全集左连接 L 和 N，按应有字段统计已知、未知，另报 L/N 应处理/已处理、缺页/单边、候选/拒绝、完整自动判定、最终采纳有证/未知。原型按现有输出行数累计 `fields_expected`，按单个 origin 或单一 ID 配行，且整体 `inputs_complete` 未要求每个应算侧完成。实施改为在分母登记阶段固定应比字段。侧、条款和多次来源核不唯一的一律留原行与冲突。行情为空、脚本跳行、输入未收妥、整页失败、合法零资格、两个独立来源均证的合法空全集分别判定。脚本退出 0 仅证明进程返回。缺必须的市场或来源、关键字段未知，或事实上的完成范围小于登记范围，都无法签整腿成功。

## 3. 冻结脚本实际调用以及补源条件

| 阶段 | 原接口、真实输入和最大可证结论 |
| --- | --- |
| 足球枚举与赛程 | `round43/raw/enum43e.py::sweep/slim_event/in_range` 使用 `/events/pagination`，每页 100，原 offset 按 100 增、每条 sweep 上限 1,500。包括两种起止日期排序查询和十二种带 tag 的查询。保留 query、原页（可能为带 `data` 的对象）、短页、错误与触顶。A1 用 `/events?tag_slug=…&closed=false&limit=50&offset=…`，以原空页及既有限额结束。不可用两个 50 页拼成一个旧页。原 `main` 写死 ESPN 的 20261108/09，还输出带 `43e` 的文件名，`scan43` 读 `43` 文件名。由当期经核的 ESPN 各联赛/日期公共响应形成扫描输入，附每个源页 SHA/路径、时间和派生文件 SHA，单列“原枚举函数已执行、赛程由当期适配、原枚举 main 当期未跑”。缺必须页就记录其影响。 |
| 足球自动决定 | `round43/raw/scan43.py::main` 读指定赛事、赛程、`understat_latest_pointer.json` 与原目标字节、英冠/荷甲 FotMob 原输入、荷甲 `.meta.json` 和 `round31/raw/league_strength_kappa.json`。核对指针目标和指向时间、赛季/球队标识、数据覆盖截止、首个可用 UTC、刷新或验证来源。`fetch_league_snapshot/assemble_xg_snapshot`、`fetch_fotmob_inputs` 可复用其受审验证和转换，保留其 `FetchEvidence` 以及派生版本。文件名相似、固定旧“fresh through”文字或研究样本不能代替来源。确有当期合格的已登记原件才能绑定到旧文件角色。用原 `parse_market_sides/fetch_book/model_quote/league_policy`，核实原库 `fixture_variants/under_risk_check/evaluate_candidate`、适用时 `check_cross_league_mw/sizing_tier` 的实际调用、模型变体、quote、原簿、board、checks、跳行原因及 `passed_numeric/policy_eligible/enterable/stake_usd`。与 N 比同一自动阶段。拟定金额和待新闻项保持原义。 |
| 三个足球历史汇编 | `build_board43c.py` 读 Americas scan、ESPN 及补查。`write_candidates43a.py`、`write_candidates43e.py` 的顶层和 `row_by/cand_row/gate_row` 绑定指定历史市场、核验行、球队或人工新闻。三者均验脚本原件。受支持环境可用当期合法材料记录原始失败、未执行或历史展示。如有独立合法原始历史输入，可另做标明日期的历史复现。冻结库自动通过、板上出现、人工最终采纳各是不同字段。缺真实前向入口时最终采纳为 UNKNOWN。球队、ID、新闻与验证材料不得为使脚本跑通而重写。 |
| 加密自动报告 | 运行 `round33/raw/scan_crypto_33.py` 原顶层。BTCUSDT/ETHUSDT 的 Binance 原 spot、两种币各自的 6h×250，以及 ETH 辅助 1h×240 须保留完整数组和接收时间。旧 `vol_windows` 自行去掉最后一根。`prices.fetch_spot` 在相同市场和请求时可用。`fetch_closed_candles` 指定起止并返回闭合 6h 线，复用其验证能力，不能把派生闭合数组冒充原始三参数 GET。Gamma 对 bitcoin/ethereum/crypto 用 `/events` 的 limit=100、offset 0/100/200/300/400，最多五页。只见满额尾页不能证明发现全集。CLOB 绑定原 token 两枚各自的真实阶梯，保留 `classify/parse_end/add_event_markets/probs_both`、A/B 两种漂移约定、`conservative_edges`、`candidates_table_all/report_only_qualifying`。旧脚本取首 token 当 YES、终端按 above、`no_ask=1−yes_bid`。其计算与原命题/真实 NO 簿并存。固定旧持仓盯市、单市场附页、经济标签分页和 Stooq WTI 属完整顶层的辅助来源。若核心表已产出而随后失败，保留表、原失败和辅助缺项。当期报告资格不登记为新入场。 |

**一个父采集者、一次前瞻封存。** a7c70ba2 的 `_request_fields/_read_entries` 只承认两类请求。其产物没有候选所读的 `frozen_files/frozen_request_sequences`，覆盖显式列出 ESPN、Understat、FotMob、κ、Binance、Stooq 未采集。全量重放需要独立的原采集写者在既有 `capture-inputs` 增加版本化、事前选定的采集范围，沿用同一 `FetchBudget`、原件快照和命令回执。选定范围纳入新正式操作的键和 scope 摘要。老 v1/v2 的原键、同范围重试与读回继续可验。新范围内把 A1 页、旧脚本的实际不同查询、适用模型材料和共享的两枚簿一起封存。每件登记来源、序号、参数、长度、完整 SHA、源/实收/确认时刻及适用角色。派生文件另记父原件及逐项变换。预注册的同一经济市场和共享簿使用同一原观察。两种发现视图取带出处的并集，逐一交代差集。两腿引用同一新总清单和最终截止。已封批次不能续写，新取得的材料不能补进旧 cutoff。上游预算或来源未齐时发布实际 partial，保留旧读回及能证的行。

## 4. 已审的两种路径呈现方式与实际隔离证据

现有 `frozenrun.sandbox_bindings/mount_namespace_plan` 产生路径表和用于存档的命令计划。`ChainPlan.run_child` 提供前台进程、租约与固定环境。只靠这三者不能证明文件系统和网络隔离。`RecordingUrllib` 默认可实取，旧侧只允许从已选原观察离线返回原字节或原错误。已核 V1 的 `research_process_linux.run_linux_contained_process` 要求受钉的 vendor ELF、版本/模型两阶段及专门的运行时合同。它没有接收这组冻结脚本、库树和输出的 D1 入口。V1 的一次成功或 runner 的命令行构造均不能向 D1 转移证明。

**按执行行为验收。** `d1-gprep-plan-v3.md` 第 48 行已经允许在受支持的隔离环境内采用固定路径呈现，或对指定脚本使用留痕的路径/窗口常量覆盖。#110 `frozenrun.apply_overrides` 已提供第二种方式。两者都要证明原 pin、只读代码/输入、私有可写输出、客体无网络/数据库/凭据及完整回收。某台环境满足其中一种呈现方式就可以接受验证，无须同时具备两种。项目不新增第三方依赖，也不把某种隔离工具或两个字面的客体目录写入安装要求。

1. **固定路径呈现。** 若既有受支持作业能呈现原布局，以只读方式提供该腿的原库、脚本及输入，把纸面产物定向到本次私有目录。保留启动时绑定清单、原脚本 SHA、实际模块来源、输入/输出和收尾证据。`mount_namespace_plan` 的 argv 本身仅供核对。
2. **按脚本留痕覆盖。** 在同等已证隔离内，始终先保存并核对镜像原字节。由 `apply_overrides(original_text, overrides, designation)` 只对本腿 `ScriptDesignation.overridable` 中恰好一次的常量赋值生成本次副本。归档原/执行副本 SHA 和长度、脚本/行号、每一项旧值/新值、值的作用及来源，独立依相同映射重建副本并比对逐字节。输入只绑定到本次只读原件，输出仅绑定到私有可写目录。例为 `scan43` 的 `HOME`、输入/输出及登记的 `LO/HI`，币脚本的 `_LIB_SRC/OUT`。币脚本没有可覆盖的窗口常量。联赛范围、端点、模型、闸门、仓位、历史 ID、新闻及非赋值语句不属于一般路径覆盖。原始脚本与执行副本在回执中各有身份。
3. **足球内联导入须单独证明。** `round43/raw/scan43.py:57` 直接执行 `sys.path.insert(0, "/home/ubuntu/polymarket-alpha-lab/src")`，随后导入 `fotmob/gates/league_strength/risk_policy/soccer_model/xg`。该语句不在覆盖清单，不能改写。受支持作业可以在原位置呈现 pin，也可以在该字面目录没有可加载项目包的隔离环境中，经正常导入从另一只读位置装入唯一的核定 pin。两种情形均在新解释器中取证，对实际包、载入模块及用到的项目依赖逐个核 `__spec__.origin` 或 `__file__` 的解析位置、字节 SHA 和 tar/Git blob 绑定，核实际调用的函数所属模块和同一腿。混入当前工作树、其他 pin、预载的错误包，或模块来源无法实证，停止该阶段并保存原因。配置中的路径、测试 `sys.path` 替身和自报 pin 不能替代实测。

当前宿主的真实 `RTM_NEWADDR` 权限拒绝只说明那一条已试启动失败。本轮不重新尝试该动作，也不切换系统工具或权限去运行被拒作业。优先使用现有受支持的作业接口和真实能力记录。只有在既有部署材料无法给出下面的所需事实时，请 operator 提供相应的现成接口、版本和证据。应用层预检及纯函数/原件验收可先完成。

| 执行合同 | 可采信的实际证据 |
| --- | --- |
| 父端与入件 | 父端持有项目受管连接、Git 对象、已核镜像和私有 staging 租约。客体只获得核定单腿的运行时、只读原件及派生副本、带摘要的源输入和本次输出位置。PG 连接/套接字、宿主目录描述符及凭据不进入客体。可用现有操作记录证明固定作业接口、受支持的运行版本与实际执行身份，缺哪项就列哪项。 |
| 客体与输出 | 用专用无敏感哨兵和实际作业证据验证代码/源文件只读、只可写本任务私有输出/临时区、外部网络和宿主 DB/凭据不可达。核环境白名单、实际退出/信号、时限、受控输出与子孙回收。按客体实际可见和可访问的对象判定。导入前固定业务 `decision_as_of`，按 `scan43.generated_utc` 原秒精度、币 `generated_at` 原精度核对，回执保存完整 cutoff。真实启动/结束、材料收妥、提交 UTC 和单调耗时分别保留。 |
| 整腿执行的普通 CLI | 正式 slot 沿用 `tools/d1_compare.py run-frozen --leg … --repo-root … --staging-root … --manifest-sha256 … --batch-sha256 … --authorization …` 并关联登记区间。父端在执行前提交分母，执行后检查真实 worker 启动、调用与模块身份、实际原或批准覆盖副本的 SHA、输出原件及清理。用 `snapshots.register_snapshot/read_snapshot` 和 `store_psycopg.record_command_run_event` 同事务封存终态与产物引用。stdout 报实际 receipt、batch、分母、腿、进程和覆盖。失败/partial 非零退出。PG 无法提交时不发布成功凭证。 |

**持久化与幂等。** 预检使用单独的操作键域，绑定实例、腿、代码/协议版本、归档/公共批次和本次所依据的能力证据摘要（未知有显式标识）。记录实际 handler、attempt、事件键和发生 UTC。先提交已能证明的分母。独立终态事务封存预检/失败正文及 `command_run_events`，无计日 slot 身份。正式键绑定实例、区间、slot 和腿，两种输入摘要、登记政策和完整 cutoff 随首次尝试固定。同键异内容拒绝。提交后丢应答，由新进程按原键与输出 SHA 找回原 writer/attempt。提交前失败只恢复原输入和未完成部分，时间过界只作诊断。空输出登记零长度及空 SHA，业务表由独立连接核对零新增。

## 5. 最小改动和单写者

| 顺序与负责人 | 文件与验收物 |
| --- | --- |
| 原计划复审 | 首稿 FAIL 与四项独立认可保留。本版先回 `d1_operating_plan_review_sol_retry`，确认两种路径呈现、预检入口与正式 slot 间的分派和证据区分，再改相应产品代码。若需重修，回同一原审。 |
| 冻结侧写者 | 独占 `papersim/d1compare/frozenrun.py`、`papersim/d1compare/frozenexec.py` 和 `tests/test_papersim_d1compare_frozenrun.py`。第一小步只接 #110 `plan_chain`、a7c `read_capture`、既有公开页索引/簿身份、分母及可归属的预检终态。从原型挑选必要逻辑，预检调用路径无 launcher，无公开采集、建包或下单分支。独立交付标准是 §6 第 1 项经过真正 CLI 与 PG。第二小步在实证执行条件齐备后才接入旧脚本。复用上游 Gamma/条款/冲突/簿绑定和时间，移除原型 1,081 行中的专用通用沙箱 argv 与重复报价/来源推断，保留必要旧页包装、两腿离线应答、实际调用观测与旧输出投影。 |
| 公开来源写者 | 单持 `papersim/d1compare/acquire.py`、`journal.py` 与 `tests/test_papersim_d1compare_journal.py`。对补源另列具体请求和已验证的数据授权/公开来源，按 §3 走相同正式采集入口。没有合格来源时，先交付 A1 实读和明确缺口。涉及来源范围或获取合同的实质改变先复审。冻结侧不自己采集、不造可读批次。 |
| CLI 与相邻节点 | 协调者单持 `tools/d1_compare.py`，先集成显式 `--preflight` 和 `--batch-sha256`，只在正式分支应用五项 gates。必要的原生测试清单与发布记录由其整合。已有 `read_capture` 候选可在组合基线验接，工程预检不用等扩展采集来源、现场班次、S1 或新的 operator 作业。比较写者持 `papersim/d1compare/diff.py` 与对应测试，沿用三类差异。正式区间、N 组包/B1、B2/B3、现金、B4、FX-35、调度各由原写者处理。冻结侧只在现成隔离事实可验证后接具体作业调用。 |

## 6. 正例、反例、故障与真实原生验收

1. **首个独立原生验收。** 在一次性 Linux 项目 PG 和真实迁移下，以既有 `stage --leg all` 封存两腿 tar，核脚本与实际 Git 原件。通过正式 `capture-inputs` 处理器生成工程批次，只在公共传输边界提供公开形状字节。另起 OS 进程调用 §2 的普通 `run-frozen --preflight`，完全省略授权文件、S1 和区间，无 `--allow-public-fetch`，不替换 `read_capture/plan_chain/快照发布`。第三个进程直接核 `snapshots`、`command_run_events`，重算带空格/重复/反序的原页 SHA、字节数、相同正文的不同时间、页内路径、双簿 token、已见分母、遗漏和首写 actor/attempt。对 A1-only 或无可证隔离的批次，应得到真实预检回执、已封分母、缺项、非零退出及 `child_started=false/day_credit=0`。旧失败关联只作历史证据。对材料齐的独立预检示例，可得到就绪和零执行。所有情形业务表零新增、正式 slot 查询零命中。另以相同入件省略 `--preflight` 和 `--authorization` 验普通执行照旧拒绝，不得靠切换模式进入正式执行。
2. **受支持环境的两腿正例。** 用完整已封工程批次和真实原件走执行 handler。现场工程烟测须使用原有授权/资格语义并标明 0 日。只在父端公开传输边界给 fixture，不替换真实 `read_capture`、`_launch`、`_publish` 或用测试 `_executor`。对现成环境所支持的 §4 呈现方式，独立复核绑定/覆盖逐行回执和实际模块源字节。足球一场含正反原簿且进门槛判定，币一项含 A/B 和真实 NO 簿，另有已证明的空/零资格样例。核实际决策钟、模型值与 quote、输入原件、真实输出和零业务写入。另以独立用例从原件和批准的变更记录重建同一副本，越清单、重复/缺赋值、改足球第 57 行或错 pin 一概拒绝。后者可用隔离工程材料验证，现场单个环境无需同时支持两个布局。
3. **业务反例。** 覆盖页非原形状、拼接 A1 每页 50 条的结果冒充旧每页 100 条的原响应、满页触顶/后续页失败仍有已见市场、反序标签、共享 token 的不同 condition、同名不同条款、相同 URL 多次及同 SHA 不同 UTC、买 NO 的真实簿与补价、touch 与终端 below。另验缺一方簿/来源时间、缺或错指针/荷甲 sidecar/κ、过时模型、跨 cutoff 收妥、临近开赛/到期、历史固定 ID/新闻无前向输入、辅助页失败。逐例断言阶段状态、依据行、应有分母/未知字段、真实 stdout/退出和零入场。历史脚本中的静态文字只可作为原件内容查验。
4. **故障与恢复。** 双腿 tar 或批次哈希/长度错误、清单与成功事件不符、PG 提交前断连、提交后丢应答、同键竞发/换输入分别验证。预检不完整时保留真实失败、可证的部分和未封原因。不生成伪分母或成功。正式执行另验 worker 未启动、缺 trace、非零/超时、输出越限、信号及所有子孙清理。核分母先于任何模型决定、终态与产物同事务、跨进程重试无双记和无残留。已发生的宿主拒绝留原回执。这些测试无须再发起原被拒的操作。最终对集成 SHA 做原独立代码审、CodeGraph 正常命令可达、冻结镜像无差异、Linux 全量、相关原生 PG 分区和桥接 CI。
5. **现场出口。** 现场必须有 operator 对应的原环境收据、事前范围/截止与原采集实物、应用版本和定时器凭证。只有同一真实批次的 L 与 N 正常运行、v3 的 B2/B3/B4 和现金日结、每个强制 slot、差异归因全部可独立核查后，才按 v3 登记完整 UTC 日。三处历史最终采纳及币的比较口径依 v3 在首个计分时段前独立裁定。若坚持比较人工最终采纳，仍须另外有出处且可当时执行的旧前向入口。否则只报告已证自动阶段和未知数。

## 7. 与 v3、首稿的归档差异和剩余条件

v3 §1 的冻结节点拆成普通工程预检、原自动阶段与整脚本现场证据。对 v3 §3 的来源分类，明确 A1 原件、另需前瞻取得的旧请求/模型材料、仅能验历史的证据三类。相对于已 FAIL 的首稿，删去对字面目录/空根布局及预检 S1 的隐含依赖，补入逐脚本白名单覆盖、足球实际 import 证明、普通 CLI 零执行预检及单独 PG 事件。两腿日常完成仍取决于既有或 operator 能证明的隔离能力、同批合格原料、正式时段及五项凭据。自动阶段覆盖与历史人工最终采纳分别列账。

依据为仓根 `docs/handoffs/2026-11-12-audit/04-development-plan.md` §§6.1–6.3、`docs/handoffs/2026-11-19-audit-optimizer/06-local-agent-prompt.md` §§4、8，以及本目录的 `d1-gprep-plan-v3.md` 第 48 行、`d1-frozen-platform-v1-original-verdict.json`、`d1-operational-run-plan-astra-v3.md` §§1–3、6–7、`d1-operating-plan-reviewed-v2-v3.json`、`d1-pr110-merged.json`、`d1-final-original-review-3d65f641.json`、`d1-final-native-3d65f641.json`、`d1-oa-a7c70ba2-independent-code-review.json`。宿主 errno 据本节点上交的实际启动记录。合成成功和 PG 测试替换处可核于独立工作树 `tests/test_papersim_d1compare_frozenrun.py` 中的 `_fixture_worker`、`test_normal_frozen_driver_preserves_actual_child_launch_outcome` 和 `test_frozen_replay_seals_denominator_and_reads_artifacts_from_managed_pg`。
