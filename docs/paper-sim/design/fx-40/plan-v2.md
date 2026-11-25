# FX-40 市场准备节点计划 v2

2026-11-24。修订对象为 v1 的独立 ZCode FAIL。审计主线锚为 `ab6edff2d64ac8aa5c2831d748b8730b63f8312d`，11-24 增量包见 `docs/handoffs/2026-11-24-incremental-review/{02-remediation-and-next-steps,03-local-agent-prompt}.md`。#118 普通合并提交为 `00199b24d38d5d9334ec7eb07ca8141db33852da`。FX-40 从 **FX-47 实际普通合入后的最新干净主线** 开工。当前计划 PASS、FX-47 合并号、FX-40 测试及候选、D1 实际日数都由各自的实物回执登记。

## 一、基线、写权与开工

11-21 进度包 02 的 FX-40、三个出口，11-21 PR 复审包 02 的 MC-1 至 MC-5、03 的继续执行项，以及 11-24 增量包 03 第 5 节步骤 7 共同约束此节点。#114、#121、#118 和五项 MC 已关闭。执行顺序为 FX-47 → FX-40 → FX-44 → 连续七个合格并跑日 → FX-45 → FX-46 → FX-39 第二步。在第二步合入之前继续执行 FX-39 第一步的晋级拦截。

参考草稿 `7f92e0356888785e7b1e1bc88c24ffd1edbe8ac7` 以 #118 合并为父，仅有 `papersim/prepare.py`、`prepare_inputs.py` 及两份专测。锚点没有这四个文件。草稿已能局部计算并解析受管输入，历史 14 项聚焦通过只对应当时纯计算，草稿缺少输入集生产、正式扫描入口及持久绑定、D1 实际分派和 FX-46 的使用说明。四条公开分支仅供逐段核对。旧数组补件的形态冲突建议已由 FX-47 的新旧行兼容与双向幂等验收取代。

协调者领取 FX-47 的合并 SHA 与双向原生重放证明，在独立干净检出确认实际父、登记和 PR 状态。以下命令中的变量均取自实际证据。

```bash
git merge-base --is-ancestor ab6edff2d64ac8aa5c2831d748b8730b63f8312d "$FX47_MERGE"
git merge-base --is-ancestor "$FX47_MERGE" "$MAIN_AT_OPEN"
git worktree add -b feature/fx40-managed-market-preparation "$NODE_TREE" "$MAIN_AT_OPEN"
cd "$NODE_TREE"
git status --porcelain
git diff --name-status 00199b24d38d5d9334ec7eb07ca8141db33852da 7f92e0356888785e7b1e1bc88c24ffd1edbe8ac7
```

前两项应成功，工作树应为空。在有索引的仓库先用 CodeGraph 定位调用方，再核对实际父里的 FX-47、NBA 和 MO-2 共享文件差异。实施写权按文件排队交接，范围含准备器、解析器、用到的现有适配器和采集边界、`papersim/{cli,scan,store_psycopg,capture}.py`、`src/polymarket_alpha_lab/replay.py`、`papersim/d1compare/packbuild.py`、`tools/d1_compare.py` 与对应测试及两处工作流清单。注册、快照、事务、锁、回执均复用现有受管工程 PostgreSQL。此次设计不需要迁移。历史迁移、已有清单项、校验摘要保持逐字节，迁移目录若在其他节点施工沿用单写者。账本由协调者单写。实际父改变接口或规则时，Astra/max 修订并交本轮原审复核。

## 二、两个正式入口及采集边界

1. **封存入口定为** `tools/d1_compare.py capture-inputs --prepare-config "$SCAN_CONFIG"` 的发现批次分支，沿用现有 `--repo-root`、`--window-start`、`--window-end`、可重复的 `--tag-slug`；新采公开数据沿用显式 `--allow-public-fetch`。终局 `--resolution-slot` 保留其独立入口。没有准备参数的既有调用保持原协议。准备模式成功另返回 `input_set_sha256` 和输入集封存回执。已有输入集按摘要离线读回。
2. **维持原批次协议。** 固定 `d1compare.acquire._request_fields` 的三种查询为 Gamma 事件发现页、CLOB 盘口，以及仅用于终局的 Gamma 按 condition 查询。既有发现操作键仍按 UTC 窗口和标签集生成。已封存 v2 清单、原响应、原 SHA 和成功回执保持字节相同并可由 `read_capture` 验证，已有 v1 按原合同只读。禁止扩写旧批次或把模型源塞进其白名单。保存原页、事件及市场位置、每次请求序号和真实收妥时间。同内容多次取得复用内容摘要，保留每次取得的证词。缺页、晚到、未绑定出现均保留。
3. **独立的准备输入清单。** 以受管快照和单独操作键/成功回执封存扫描配置原字节和摘要、队列、窗口、固定决策截点、发现批次与其成功回执、规则登记、模型原件清单及明确的缺证/覆盖。逐响应保留来源种类和用途、市场/球队/资产、顺序、预算结果、来源时刻及其可信状态、实收时刻、长度和 SHA-256。模型适配器已有 `understat.fetch_league_snapshot`、受支持联赛的 `sports.fetch_fotmob_inputs` 与 `prices.fetch_closed_candles/fetch_spot`。固定发现批次仍将 xG 和加密模型来源列为未采集。准备生产者及上述模型原件独立登记需要实施；特定时段是否已收妥逐件核实。需要在同一新窗口取模型原件时，在决策截点封闭前取得，与该窗市场采集共享请求数、目标数、单项/总字节、超时与总时限，分页和重试同样扣减。若引用已封存市场批次，只接纳截点前已有真实取得证词的材料；新采的材料只能用于适用的新时段。
4. **先登记原字节再解析。** 固定 `acquire._collect` 在发现/解析后才批量 `_save_responses`，模型适配器也可在返回 evidence 前解析。为新采准备输入在现有受限传输和解析之间接受管登记回调，校验允许来源、传输状态、正文边界及预算，短事务提交 `SnapshotRegistration` 并核实长度/摘要之后才调用内容解析器。封存时复核各引用、批次成功回执、同实例和时序。登记失败时解析次数为零；已登记但语义无效的原件仍可验。声明的原件找不到、哈希/来源/回执相互矛盾使此次封存或读取失败；可证的未取得记录保留为缺口。只持久化证据和绑定，市场包保持内存中的 UTF-8 有序 JSON 数组。无需第三方依赖、连接模块或文件型持久化。
5. **扫描入口定为** `papersim.cli run-scan --managed --prepare-from "$INPUT_SET_SHA256"`。按输入集指定队列，原 `queues[].pack` 继续工作，单个队列两种来源同时指定时报歧义。`resolve_preparation_inputs` 从受管快照核验来源和截止，在同一一致视图对每个谱系调用一次 `specs_for_lineage`。空注册表走原冻结规格；同一份 `LineageSpecs` 的实际参数、角色、版本、规格 SHA 同时交 `prepare_market_pack` 与 B1。稳定排序按队列、候选身份和原记录位置，不重排盘口档或变体。引用实际买入 token 的簿，区分买 NO、倒序标签及同摘要的不同请求。俱乐部足球用有来源的 xG 调 `soccer_model.candidate_for_market`，保留十个有序变体。加密用登记现货、已闭合 K 线和选定规格运行现有 `crypto_model`，终值合约的高低及买入方向恰好换算一次。触价按第四节处理。准确登记主客/赛事、市场及 token 来历，足球、篮球取真实开球/开场，加密取合约到期。去掉 `packbuild.events_materials` 的跨资产 `kickoff or expiry` 回退，向 B2 提供原页位置及有时区的字段。

### 盘口时效的正式来源

`DELIVERY_PLAN.md` 的 D1 公开输入登记、`acquire._book_ages` 和 `d1compare.frozenexec` 的 `registered_book_age_policy_missing` 都表明固定基线尚无已登记的正式区间限额。v2 报告 `NOT_ASSESSABLE`。B2 的 `capture._d1_book_sources` 读经受管登记的 D1 区间所关联的 `capture-marks` 槽 `scope.book_age_policy`，字段是 `max_source_age_seconds`、`max_receipt_age_seconds`。`pipeline._step_book_freshness` 缺 `max_book_age_seconds` 时阻断。FX-44 的收盘价窗口有独立用途。

准备输入以该同一区间/槽策略为唯一出处，绑定区间及槽快照 SHA、长度、真实成功回执、适用窗口、批次、实例、队列或团队及生效时间。B0 的决策截点只选当时已知可用的规则和证据。本计划采用事前登记区间规则、B2 取价槽逐值引用及核验的交付方式。固定基线上 `operations._load_slot` / `_registration` 能校验 `d1-register-interval`、`d1-register-slot` 及其受管快照，取价槽须带已封存批次摘要，源码入口中未见写这两种正式登记的命令。若实际父已有提前生效的正式规则证词，可绑定同一证词。B2 槽需要候选 ID 时，不能倒用后建槽证明先前已有政策。实施先核实实际父的区间/槽登记生产入口及外部调度契约。若仍缺，D1 持有人按本节点交出的两个字段与校验合同补接真实登记入口，作为真实计日的明确前置；已有受审测试可在隔离库通过快照和回执接口登记专用测试范围。同一登记下分别检验真实来源时刻和收妥时刻。B0 以固定决策截点计算来源龄、实收龄，按各自限额检验，等于上限仍有效；`EntryRequest.book_time` 取实收、`now` 取截点、`max_book_age_seconds` 取实收龄限额。B2 按自己已登记的取价时点复核两项。未来、缺时钟、过期、错槽、换批或错生效期均不得产生可信报价。

计日前由协调者按已授研究范围从正式规则记录取得两个值及决策出处、安排事前登记并让独立连接读回。固定包没有给出任何正式秒数。本轮不指定生产值；现有授权内可定的取值及依据由协调者登记，若确需新增未授权的数据源或改变已登记验收口径，再将具体缺项交对应决定人。隔离测试**先登记一个明确标为测试用途的作用域及两项限额**，通过相同读取路径做正例、各自的边界与超界、缺登记和错作用域负例。测试常量及 B2 的 60 分钟收盘窗口都不能成为生产默认值。正式策略验证前七天累计不能开始。

## 三、候选失败、覆盖与受管重放

在固定实现中，`replay._CONSTRUCTORS` 仅允许 `soccer_model.candidate_for_market`；`bind_construction_failure` 和 `_run_constructor` 都只重跑该函数。草稿 `construction` 字段中的普通报价对象、`ExcludedTarget` 内存记录均不能直接作为其他失败的业务行。`scan._record_from_mapping` 会丢弃 `prepared_provenance`，`replay.bind_candidate` 的 DECISION token 固定为 YES，现有 `run_scan` 会重新咨询 provider。实施须在显式准备模式把同一绑定、原买入 token、包 SHA 与长度、实算规格传过 `records_from_pack → run_scan → assemble_scan → replay.bind_*`，验证价格证据、模型和规格吻合。对旧包和已有候选维持原合同。成功的变体数组直接由 FX-47 写者处理。

| 在决策截点可证明的情形 | B1 持久记录与验证 |
| --- | --- |
| 完整足球输入使原足球构造器返回 `None` 或其自身的 `ValueError`，含有证据的禁走跨联赛路径 | 沿原构造失败包和既有重放分支写一条 `CONSTRUCTION_FAILED`。保留模型版本、实际输入、构造器和具体失败。 |
| 已能从原页证明受管队列中的市场、方向/买入 token、谱系及候选键，但已封存的采集结果表明缺模型、xG/赛季/映射/适用桥接不足，或加密缺现货/有效闭合 K 线/条款、无法构造 A/B | B1 写 `CONSTRUCTION_FAILED`。新增窄的、显式版本化的准备器失败重放分支，绑定原始观察/缺项清单、适用规格和固定截点。重放重新执行同一纯构造或完整性检查，没有可验证引用的“缺失”字符串不能单独成行。 |
| 已有上述候选身份，缺该时段正式簿龄政策，或盘口/模型原料晚于截点、过期、未收妥，无可用买入簿；或触价状态未经证实 | 写 `CONSTRUCTION_FAILED`，逐例重算政策缺口、双时钟、缺档或 `papersim_prepare_crypto_touch_state_unverified`。截点以后观察只作为解释排除/缺证的证词，不参加当时报价；后来新增的材料不得改变原结论。 |
| 目标类型/方向未受支持 | 已有受管队列、谱系、真实市场及买入方向且可重算“不支持”时写 `CONSTRUCTION_FAILED`。没有可信的市场/方向/谱系绑定、市场页未发现、标签/双方 token 冲突而无法归属的，只计覆盖观察。 |
| 已能形成合法报价，随后交易闸门拒绝，或入场受当前队列暂停抑制 | 走原 `decide_entry` 与 B1 对正常候选/暂停的既有处理，保留有序报价、DECISION、闸门理由或抑制记录。 |
| 操作级完整性错误，例如错实例、伪称已登记、绑定或原件哈希冲突 | 拒绝整个操作并留错误/操作回执，业务行零写入。 |

准备失败只允许预定义的足球及加密准备检查标识及经校验的结构，不从包动态加载代码。按输入集、批次/原页或请求序号、队列、候选键及真实买入侧固定失败证词；存在已验证 token/事件绑定时同时落原记录，模型版本取实际选中的规格。缺项通过当时封存的这种来源记录或适用政策登记视图证明。新路径的 binder 和 `replay.replay` 在独立受管读视图验证同一组原件 SHA、字节长度、规格与截点，运行相同纯函数再比结论、固定原因及细节。失败行不伪造概率、成交档、DECISION、入场或现金。旧版本足球 bundle 用原实现回放。

单纯覆盖观察持久存在现有 `papersim.snapshots` 内的准备覆盖清单，逐项记录输入集 SHA、批次、原页/序号、当时可证的属性、原因、计数；其摘要由 B1 的首次 PREPARED 绑定引用，已无可信输入集的操作级失败由自身回执追踪。用独立连接从回执→清单按项读回；不为凑数生成候选键。协调者在 `DELIVERY_PLAN.md` 对照 11-21“算不出来的写成构造失败记录”，注明可以识别的足球/加密候选全部走上述行，仅不能归属的发现计观察，记录分母与原因。任何进一步缩小范围须按原验收流程明确登记裁定。

## 四、四个冻结夹具与 11-21 的逐位验收

固定目录为 `tests/fixtures/papersim/`。下表“已有”是锚点上逐件能读到的内容，SHA-256 和长度对应冻结文件本身。它们可以登记为夹具快照，并有各自的冻结精度、负例或守卫。仓库的 `docs/paper-sim/server-mirror/MANIFEST-NOT-MIRRORED.md` 说明各轮原 API 响应和候选档未镜像。本表没有替未提交的归档内容或受管快照签发存在证明。表中镜像脚本、库入口和测试入口均固定在提交 `ab6edff2d64ac8aa5c2831d748b8730b63f8312d`；此提交只证明镜像/比较代码版本，原历史运行提交及其输出按真实归档核认。

| 夹具、长度与 SHA-256 | 已有输入及所对照的历史构造/输出 | 正式请求对照尚缺的材料 |
| --- | --- | --- |
| `regression_r23_soccer.json` · 38115 · `d9914c23645a54f405ea246aa7ea574afe7515cd176e321d1536db8b60f689f9` | 四案 BRE–FOR、HUL–IPS、CHE–MUN、BOU–LEE，有当季 xG 球队行摘录、VWAP、十个报价、已舍入的中位数和拒绝原因。夹具引用 r23 的 `epl_candidates.json`、`epl_scan.json` 和 `xg_epl_23.json`。现存独立库入口 `soccer_model.candidate_for_market` 产候选封套或返回空，`tests/test_papersim_soccer_model.py::_r23_snapshot_bytes` 用摘录及上季测试常数组装输入。镜像 `round23/raw/model23.py` 产法甲的 `ligue_model23.json` / `ligue_shortlist23.json`。 | 对应英格兰四案的实际历史构造器版本/入口或原输出、完整当季/先验模型原件、逐市场 Gamma 与买入 CLOB 档、当时的取证时钟、政策及其余请求字段。保留 BOU–LEE 的真实分歧拒绝。 |
| `regression_r19_crypto.json` · 1902 · `7e07f02424a9c37cfb518c2ad997ecb88efe6d422b5afe558b1be96174b2a2c3` | 三例 ETH-2000、ETH-2250、BTC-65k 均为向下触价。文件含现货、30 日波动率、两个真实时间锚、期限及已舍入 A/B。`tests/test_papersim_crypto_model.py` 另有从原行情摘出的两组 360 个六小时收盘值。`round19/model19.py::horizon_block` 调 `probs_dn` 产 `crypto_scan.json` 的 `touch_horizons`，其中 A/B 取四位小数；`round19/finalize19.py` 产 `crypto_recommendations.json`。受管基线 `crypto_model.touch_probability` 输出 `TouchProbability`。 | 缺决定时现货及原 K 线响应、从合约起算到截点的可核验触价路径（包括适用的高低点/未闭合段证据）、交易场所/观察口径/合约条款、原市场与买入簿及历史请求上下文。收盘值序列足以验证相应波动率运算，不能证明从未触价。 |
| `regression_gate_arithmetic.json` · 2763 · `4f19389c921444e94e8845d1759d942d9a13c99969362ab49293be40193b83d6` | #48 的 r23 入场、r24 复核、边际公式及聚合档（最优档、250 美元 VWAP、填满标志、近档深度）；`round24/raw/books24.py` 产 `books24.json`，`finalize24.py` / `assemble24.py` 分别产 `epl_scan.json` / `epl_candidates.json`。`tests/test_papersim_pipeline.py::test_48_gate_arithmetic_original_behavior` 调 `gates.evaluate_candidate` 产 GateReport；同文件 `_accepted_soccer_request` 产生重构的 `EntryRequest`。 | 缺两次相应原件和不同取证时刻，特别是买入簿逐档价量、来源与收妥、对应市场和决策材料。测试用的代表性梯形簿无法从聚合摘要唯一反推。 |
| `regression_banned_path.json` · 2618 · `0a8acce166cc92d744751b29d3c0a6abb8abb8ac67a4d1b30d2eef584255245d` | HOF–LYO，无有效跨联赛桥接，冻结守卫 `expected_match_lambdas = null` / `expected_result = null`。`round5/tools/model_round5c.py::model_game` 不同联赛返回 Python `None`；`round10/tools/model10.py` 的 `model_round10.json` 是曾放宽守卫导致方向反转的事故资料。已有固定 `docs/paper-sim/review-2026-10-30/FIXES.md`。受管独立入口为 `soccer_model.candidate_for_market` 配 `replay.bind_construction_failure`。 | 缺可绑定原场次、方向和两侧联赛并产生同一规范失败包的输入。事故原档 `model_round10.json` 亦未镜像，只用于复核事故数字。验证守卫只需它实际依赖的事实，事故原件不构成额外门槛。 |

**比哪一段字节。** 11-21 的原文是“注册表为空时，用四个冻结夹具对应的输入，产出的请求与夹具逐位相同”。历史 JSON 的模型/候选/梯形簿或 `null` 没有编码整份现代 `EntryRequest`。逐例标记原有字段及其真实格式和精度，分别验证这些锚；然后在空注册表下，用同一批可证的对应输入，独立固定基线组装基准 `EntryRequest`，由原 `replay.replay_bundle_payload` 导出完整规范 JSON 文本的 UTF-8 字节。基线可用 `d1compare.packbuild.build_entry_request` 从真实买入档构造基础请求，按已证当时事实补齐它明确留空的模型、政策等字段，经原足球/加密库入口计算。对禁走案例用原 `replay.bind_construction_failure` / `construction_payload` 输出规范失败包。可直接使用有出处的同格式原件；原运行若未产生现代包，记录独立基线生成步骤、提交、真实输入引用及输出格式即可，无须寻找一份历史上从未定义的序列化文件。基线在独立检出产生，禁止用候选准备器反推。记录每案参考/实际字节长度、SHA-256、全字节比较与首处差异。舍入过的 A/B 比较、旧 selfcheck、容差断言、代表性梯形簿、合成 `NOW`/`300.0` 时效值、`m2/prices.json` 都只证明各自的测试含义。

**历史材料如何进来。** 先按夹具 `provenance.source_files` 和镜像遗漏清单列出每个缺件、所属轮次及责任方。在实施后的隔离验收中，来源持有人经已授权的只读归档或既有受管快照交付原始字节及独立清单；协调者核对原名/相对位置、实际版本或可证明的发布身份、时间出处、长度与摘要，缺真实来源时刻明示缺失。已在库的用 `snapshots.read_snapshot/verify_snapshot` 回读；归档原件按既有 `registration_from_legacy_file → register_snapshot` 明确导入隔离实例，历史原件、当时抓取证词、今日导入/审核时间分列，再通过已审的历史夹具输入清单读回给基线与候选。历史轮次早于 v2 采集协议。隔离回归驱动从原件及可核实的历史元数据生成带来源类型的只读输入，调用与正式准备器共用的纯组包函数；这是拟交付的测试入口。两个驱动只在解析来源上有别，候选端都必须运行同一个准备函数，参考端用锚点独立构造器。归档缺少决定请求的任何字段，就在该例停在缺口。正式 `--prepare-from` 始终复核真实 v2/受管回执。历史导入不能伪造成当时已登记的正式 v2 批次或在线回执。新增的正常采集仅适用于当下场次。需要归档保管人提供副本就登记所缺具体原件，不以今日补抓、文件修改时间、重建先验/梯形簿或手工 SQL 补造事实。

**触价三案的分流。** 先保留 `touch_probability` 三案 A/B 冻结回归。仅在取得对应合约原文、观测源/起止定义以及覆盖起点至决策截点的真实路径，能逐项证明当时尚未触及后，准备器方可按已审的纯检查与原 `crypto_model` 计算历史触价请求。已触及按真实条款处理，不能假设“尚未触及”；证词不够保留 `papersim_prepare_crypto_touch_state_unverified`，有候选身份时按第三节写失败。条件式支持只复用现有模型与条款，涉及尚未获准的新合约/行情范围时按既有授权边界另定。隔离的加密**终值** A/B 正例用于证明正式链路，不能代替这三个触价案例。历史预期有报价而实际只能拒绝时，该例正式逐位比较为“未验证（触价路径缺失）”，记明双方输出差异。

**退出处理。** 逐件收集、开发、工程用例和不依赖缺件的工作可继续。协调者把能通过的案例、缺失物、取证责任、真实差异以及下一步列在 `DELIVERY_PLAN.md`。原材料齐备按原四项逐位准则验收；若特定材料永久不可得，提交逐案影响和可核验的替代比较（例如冻结构件输出及语义守卫）供验收口径的有权决定者裁定并由本轮审核链复核。协调者处理授权内的证据等价，包括独立基线可确定重建的规范字节；实质放宽原四项验收须获相应明确授权和修订登记。待补证或待裁定按原条件记 FX-40 验收未完成、M2 出口未满足，不能以工程 PR、缺证拒绝或自检替签。登记给出补证或正式裁定两条可执行去向。

## 五、首次绑定、操作锁与故障恢复

`store.scan_key` 包含 `started_at`，`ScanSubmission.payload_sha256` 还含 `finished_at`、params 与候选内容。现有 `scan.run_scan` 每次读实时时钟，`store_psycopg.record_scan` 以扫描键及载荷摘要去重，操作收据以 `operation_key + attempt + phase` 幂等；既有原生重试测试使用同一个注入时钟。准备模式按以下顺序补齐 B1，保留普通历史包的调用约定。

1. 调用方给稳定 `operation_key`。在同一受管连接上取 `store_psycopg.d1_operation_lock` 的操作级会话锁，覆盖首次读取、绑定提交、业务提交、成功收据或恢复；断连释放锁。直接 B1 和 D1 Path N 都通过同一个 B1 锁和操作身份，Path N 不另持同键锁。先按操作键读取全部历史阶段及已有关联扫描，核对首次 STARTED、PREPARED 及 SUCCEEDED。调用显式提供的输入集、配置、时段或目标变更即报内容冲突，禁止因 attempt 变化开新业务；首次绑定后的角色变动不算调用方换内容。
2. 首次在一个可重复读视图取真实 UTC 扫描开始时间 `scan.started_at`、输入集/配置字节、窗口和决策截点、规则出处、每个谱系的角色、规格内容/参数/版本及 SHA。编排只取这次规格，B0/B1 共用。用原证据、首次选定的有效来源、当时的输入选配和可重建的同一状态准备扫描，冻结实际装配时用于载荷的结束时刻及其他会改变相同载荷的首次读值（含组合/暂停、有效模型/影子材料），保存足以逐字重建的引用。包仅存 SHA/长度；保存预期 scan_key、业务载荷 SHA 与查回依据。快照和 PREPARED 收据在同一事务提交于业务写入前，用 `papersim.snapshots` 加 `papersim.command_run_events` 中该 B1 操作的 PREPARED，其输出证据指向版本化的 B1 准备绑定文档。首次 STARTED 绑定操作及输入身份，若在 PREPARED 前中断则从首次 STARTED 取回原始开始时间；首次成功产生的 PREPARED 横跨所有 attempt 唯一。复用 `d1compare.operations._Operation.prepare/_read_prepared` 的快照加回执模式；B1 有自己的对象种类和校验，不冒充 B2/B3 收据。
3. 每次先比原绑定，再在锁内按 `scan_runs.operation_key` 及原 scan_key 查业务。原扫描已提交则以原载荷 SHA、开始/结束时刻、关联候选/入场/现金键核对原结果并直接读回原 ID，在合法恢复 attempt 写可追溯的 SUCCEEDED 或读回已有成功收据。多行、已绑定冲突或遗留业务归属无法证明均报明确冲突，绝不试探性重写。只有确证未提交才从 PREPARED 重建、核对包与业务摘要，经唯一的 B1 `record_scan` 原子写入；恢复重建采用冻结状态，写入前按原暂停/现金约束确认仍可合法提交。若其他操作已使资金或准入不再合法，保留该次冲突，既不沿用失效状态入账，也不在原身份下重新定价。提交之后的角色切换、当前余额和真实重试时钟不能重新定价或生成第二组行。原 attempt 的失败与真实发生时间保留；新操作键才重新取现役。成功回执保留原 scan ID 和 scan_key。锚点的 `store_psycopg._d1_scan_receipts` 只取首次成功 attempt 内的收据，`capture._d1_candidate_token` 要求同 attempt 的 STARTED 不晚于扫描结束。恢复成功时后一个 attempt 的真实开始可能已晚于原扫描结束。为有 PREPARED 的 B1 恢复补窄的 B2 读回分支，读并验证原 STARTED→PREPARED、业务行及其原结束时刻→新 attempt 的 RECOVERY_READ/SUCCEEDED 关系、操作键、提交、摘要和真实时间顺序；原 STARTED 与 SUCCEEDED 各保留真实 attempt 和时间。原普通包/老收据仍须满足原来的同 attempt 检查。用一次断连恢复后的候选再走正式 B2 取价验证此链。
4. 两个明确的原生反例用**不同的实时时刻/注入时钟**、独立连接和可进入候选触发。一例强制 `record_scan` 已提交但尚未写 SUCCEEDED 就断开，随后另一 attempt 重新连接，并在中间改变注册表角色；另一例两个进程同时从 B1 与 Path N 提交同键同内容。独立 SQL 读回同一首次 `started_at`、同一准备绑定、scan_key、scan_id、candidate/entry ID、相同现金键及金额，只有一组扫描业务、一次现金扣记。分别覆盖业务前中断、成功后重复、同键变内容报错、下个独立操作用新规格。新收据仅反映真实 attempt，已提交业务不再装配或写第二次。

## 六、D1 接线、验证及移交

`tools/d1_compare.py::command_run_new` 在审计锚只做原授权门禁、打印 argv 并返回 2。准备模式由它执行同一个受管 B1 命令，透传退出码，返回可由独立连接按操作键关联的真实 B1 回执。模型计算留在 papersim。B0/B2 同批、同请求序号、同真实买入 token。B3 从后来真实时段已登记的终局批次关联同市场/买入方向，不能回灌决策截点。锚点的 `settle` 无旧计划示例的 `--register-public-outcomes`；由 B3 持有人核对实际父上的正常终局生产/消费路径并联测。无现成入口就登记缺口和其负责节点，禁止手插或把示例记成结果。Path L 的 `run-frozen` 缺 executor 时会报 `frozen_run_executor_not_wired`，预检只记预检。D1 在计日前核实实际旧路回执和原完整时段门禁，必要接线归独立 D1 节点。

以下为候选应支持的正常入口。执行者先核对其实际 `--help`，在隔离库使用真实登记引用，测试传输用可控的离线原字节。

```bash
.venv/bin/python tools/d1_compare.py capture-inputs --repo-root "$NODE_TREE" --window-start "$WINDOW_START" --window-end "$WINDOW_END" --tag-slug "$TAG" --prepare-config "$SCAN_CONFIG" --allow-public-fetch
.venv/bin/python -m polymarket_alpha_lab.papersim.cli run-scan --managed --repo-root "$NODE_TREE" --config "$SCAN_CONFIG" --prepare-from "$INPUT_SET_SHA256" --operation-key "$SCAN_KEY" --attempt 1
.venv/bin/python tools/d1_compare.py run-new --repo-root "$NODE_TREE" --scan-config "$SCAN_CONFIG" --prepare-from "$INPUT_SET_SHA256" --operation-key "$SCAN_KEY" --attempt 1 --authorization "$D1_GATE_RECORD"
.venv/bin/python -m polymarket_alpha_lab.papersim.cli replay "$CANDIDATE_ID" --managed --repo-root "$NODE_TREE" --trusted-commit "$CANDIDATE_SHA"
```

- **确定性与来源。** 两个离线进程用同一封存输入/截点/规格视图，改变枚举顺序与实时时钟，核对市场包全部字节、长度、SHA 和重放新增联网数为零。覆盖嵌套页、重复原件的不同请求、错实例/错原件、注册失败零解析、买 NO、倒序标签、未来 xG/K 线、缺簿龄、来源/回执冲突、模型不足、未归属覆盖。证明封存 v2 与旧入口原清单/正文/回执可读且同字节，同一批次另建输入集可复用，不改变 v2。
- **受管闭环。** B1 和 Path N 从登记原件得到至少一个足球十变体及一个加密终值 A/B 报价。独立连接核对真实市场、谱系、扫描及候选 ID、计算使用的规格 SHA/版本、买入侧 token、DECISION 价格、来源簿和有序变体、成功回执及资金；用选定的实际参数独立重算。走 B2 对同批原件和 B3 后续终局的正常入口，保留尚不可联测的责任与证据缺口。每类实现的构造失败在隔离原生库用正式 B1 落行，读出候选 ID，通过上列 `replay` 的受管只读入口逐项重算 `matched=true` 与原原因，至少明确展示一条足球拒绝和一条加密/准备拒绝。无身份的覆盖清单独立读回、核对计数及候选零行。第五节两个不同钟的失败注入须查原生业务/现金的实际行数和 ID。
- **执行与冻结。** 计划/实质修订由原生 Astra/max，开发/修复/测试代码由原生 Sol/max。计划先取独立实际本机 Linux ZCode guarded 只读 PASS，再实施；真实测试另由与开发、计划/代码/阶段复审分离的 Linux ZCode 执行，各角色关闭 fast。审核禁 shell 与写工具，按精确计划摘要或候选 SHA 给结论，会话及 FAIL 和复审均保存。聚焦包括 `tests/test_papersim_{prepare,prepare_inputs,scan,d1compare_packbuild,replay,pipeline,soccer_model,crypto_model}.py`、新增 CLI/D1 来源、恢复和覆盖用例，另跑 `tests/test_papersim_scan_store_native.py` 及新增的隔离 PG 分区。运行 `.venv/bin/python -m pytest -q -rs` 搭配实际文件清单、`.venv/bin/python scripts/verify_local.py --full`、`.venv/bin/python -m compileall -q src tools tests`、`.venv/bin/python -m polymarket_alpha_lab.papersim.cli selfcheck` 和 `git diff --check "$MAIN_AT_OPEN" HEAD`，保留首个失败、修后复验、实际通过/跳过数。锚点/父与候选逐字节对照十项自检、四个冻结夹具及历史迁移。足球十变体、既有四项门槛（差幅 ≤6c、最差边际 ≥4c、扣费后的中位执行边际 ≥5c、流动性 ≥5000），以及原 250 美元盘口深度/VWAP、0.10 至 0.92 价格范围、已发布评估数字和优化器数字单源均维持原合同；数字以源定义/冻结文件为准。测试覆盖不得删减、跳过或放宽。
- **审计与合并。** 在真实父与候选上同步已索引仓库的 CodeGraph，跑蓝图 `audit_static.py`，验证新增代码可从正式 CLI 到达；新增原生文件进 Linux 工作流及测试清单。按 11-24 包从审计锚到最终头运行 `test_weakening_scan.py`、`address_occurrence_diff.py`、`added_line_sensitive_scan.py`；登记只含脱敏后的必要证据。三项 Linux 工作流为 Offline verification、Native project PostgreSQL、Research paper bridge，核对最终 PR 头和实际检出的合并对象。按 11-24 包 03 第 5 节步骤 7，从审计锚提取检查脚本再执行。

```bash
git show ab6edff2d64ac8aa5c2831d748b8730b63f8312d:docs/handoffs/2026-11-21-pr-review/evidence/check_ci_merge.sh > ../check_ci_merge.sh
bash ../check_ci_merge.sh "$PR_NUMBER"
```

读取实报 CURRENT、三条成功运行号及实际合并检出证据。缺日志或主线变动则刷新普通合并并重验最终树。独立代码与阶段双审批、干净提交后普通合入，记录 PR、最终头与普通合并 SHA。不变基、不强推、不改根规则。

**给后续节点。** FX-40 实际合并时间、FX-44 OD-P 登记生效、事前登记的正式盘口双龄及适用来源验证、Path L 与 N 现行门禁都齐后，才计连续七个真实完整 UTC 无人值守影子并跑日。OD-P 为开赛/到期前 60 分钟内最后一次符合条件的观察，只对登记后取价生效。两条路径用同一批市场及同时期已登记原件，逐候选解释差异，每日核对现金恒等式、干净提交、零手工改库及抽查重放。缺日、测试、事后回填和运行层人民币模拟账贡献零天。七日后做 FX-45，OD-Q 在 D2 前登记，全部已入场均重放，其余以实际切换日八位日期为种子抽 200 个，不足 200 全取。再做 FX-46 的正式使用说明及 FX-39 第二步；它验证配对、晋级、下一次真正使用新规格及换回。M2 仍需原定 B1 至 B4、D1/D2 和既定修复/节点出口。正式标准 F 的 30 天从真实 D2 起算。

## 七、历史原件迁入与本次回执

以下六份外置原件已逐字节核过长度和 SHA-256，三个 PASS 均取 `result.txt` 的实文。四个夹具的原始采集材料与这六份计划/回执是不同证据。历史原审会话为 `sess_e4ebd643-9d64-4ad2-be8d-38e1232d1d21`；本轮 v1 原审会话为 `sess_d149e0d3-3541-48ec-8f30-e6d319ba6dfe`，已给 FAIL，v2 由协调者回送后者审核。

| 物件与可定位的外置文件名 | 字节 | SHA-256 |
| --- | ---: | --- |
| 11-21 计划，`fx40-market-preparation-review/plan.md` | 18078 | `d3e613096b7b0b6ad480e75ba85aa2bbe8f371eddc8ad07461762ca55bcad226` |
| 该计划的原审 PASS，同行目录 `result.txt` | 6738 | `a42a30bd039275f0656cc16dfb808ef649a2f6675e47f6b2a0a4d42c5f5fd55e` |
| 数组补件，`fx40-variant-array-plan-rereview.4D4Paw4A/array-plan-addendum.md` | 5295 | `164a6e8ebf8f649e2ac09557ec391ad987315a5d355b2ce3e08bb15c144f49a6` |
| 数组补件原审 PASS，同行目录 `result.txt` | 7511 | `2e92d69ad2a79b676cec236280700a04e515cb80b1d1e8f12af2690543682a5a` |
| #118 后输入解析补件，`fx40-post118-resolver-plan-review.cn78fo_k/resolver-delta.md` | 10886 | `6636c4384a03b1b4affdd02219cadd5bb6d290a27724ed64d0351975ef1d861b` |
| 输入解析补件原审 PASS，同行目录 `result.txt` | 6497 | `c5b7e21122f67a3125d22ecdf70c3a60523a06d4ca348ffbf6f7d4e9e89a4c82` |
| 本轮 v1，`fx40-node-plan.md` | 12365 | `bd097a8bfa2a3e171b6d3179d18c4c928bd430c30ed87a44517476d970379740` |
| 本轮 v1 原审 FAIL，`fx40-plan-review.json` | 10550 | `5b85a62109870253095ab0d2e3d82f4be7fa853444f08c969e225cdb599a8029` |

实施节点由协调者在地址/凭据及其他敏感内容检查后，把上述六件分别作为原件逐字节迁入 `docs/paper-sim/design/fx-40/`，连同 v1、FAIL、v2、本次原审后续每份回执、实际测试修订/结果与裁定入库，提交索引逐件列路径、字节数、SHA。复制后用 Git blob 逐件复核上述定值。若某原件届时确实找不到，按 FX-42 在索引注明“原件缺失”及所寻物件；有原件但不能公开则按实列为待处理，保留原哈希和限制，避免把脱敏改写件当原件。协调者同批在 `DELIVERY_PLAN.md` 写开工父、前置普通合并、正常入口、失败与覆盖口径、四夹具对照状态、盘口策略来源、独立实跑/复审、D1/B3 未决依赖及实际合并。此版交付物为计划；上述入口、存储接线、运行结果和退出状态在后续节点凭实物核实。
