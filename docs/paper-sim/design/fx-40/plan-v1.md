# FX-40　从已登记原件到受管扫描

2026-11-24 节点计划。审计锚为 `ab6edff2d64ac8aa5c2831d748b8730b63f8312d`，#118 已经普通合入 `00199b24d38d5d9334ec7eb07ca8141db33852da`。本计划的开工点是 FX-47 **实际普通合入后的最新干净主线**。FX-47 合并号、当前计划审核结论、FX-40 候选及并跑日数均须用各自的回执填写。

## 已有工作和本轮差异

| 已核对象 | 本节点如何使用 |
| --- | --- |
| 11-21 进度包 02 的 FX-40、M2 出口，PR 复审包 02 的 MC-1 至 MC-5 及 03 的继续执行项，11-24 增量包 02 与 03 步骤 7 | #114、#121、#118 及 MC-1 至 MC-5 已关闭。流程采用 FX-47 → FX-40 → FX-44 → 七个连续合格并跑日 → FX-45 → FX-46 → FX-39 第二步。保留 FX-39 第一步的晋级拦截。 |
| 11-21 计划、数组补件、#118 后的受管输入解析补件 | 三份历史计划 SHA-256 依次为 `d3e613096b7b0b6ad480e75ba85aa2bbe8f371eddc8ad07461762ca55bcad226`、`164a6e8ebf8f649e2ac09557ec391ad987315a5d355b2ce3e08bb15c144f49a6`、`6636c4384a03b1b4affdd02219cadd5bb6d290a27724ed64d0351975ef1d861b`。各自的实际 ZCode PASS 原件为 `a42a30bd039275f0656cc16dfb808ef649a2f6675e47f6b2a0a4d42c5f5fd55e`、`2e92d69ad2a79b676cec236280700a04e515cb80b1d1e8f12af2690543682a5a`、`c5b7e21122f67a3125d22ecdf70c3a60523a06d4ca348ffbf6f7d4e9e89a4c82`。原审链为 `sess_e4ebd643-9d64-4ad2-be8d-38e1232d1d21`。历史数组补件曾要求换形态报冲突，现以 FX-47 最终合入的兼容和幂等契约为准。 |
| 公开草稿 `7f92e0356888785e7b1e1bc88c24ffd1edbe8ac7`，父为 #118 合并 | 只有 `papersim/prepare.py`、`prepare_inputs.py` 与两份专测。已有纯计算和受管输入解析可供逐段复用。固定主线没有这四个文件，草稿也没有生产者、正式 CLI、B1 绑定、D1 调用或 FX-46 使用说明。历史纯计算的 14 项聚焦通过仅覆盖当时的局部代码。四条公开分支均作对照，禁止直接合并或整提交拣入。 |

## 开工与改动

协调者取得 FX-47 实际合并 SHA 与原生双向重放证据，在最新主线核对实际源码、父 SHA、PR 状态和已有登记。下列命令供后续实施者在独立干净检出中使用，变量均为实物引用。登记更新由协调者单写。

```bash
git merge-base --is-ancestor ab6edff2d64ac8aa5c2831d748b8730b63f8312d "$FX47_MERGE"
git merge-base --is-ancestor "$FX47_MERGE" "$MAIN_AT_OPEN"
git worktree add -b feature/fx40-managed-market-preparation "$NODE_TREE" "$MAIN_AT_OPEN"
cd "$NODE_TREE"
git status --porcelain
git diff --name-status 00199b24d38d5d9334ec7eb07ca8141db33852da 7f92e0356888785e7b1e1bc88c24ffd1edbe8ac7
```

要求前两条成功、工作树状态为空。针对已索引的仓库先用 CodeGraph 定位实际调用方，再复核 FX-47、NBA、MO-2 对共享文件的改动。若实际父提交上的接口、绑定、规则或测试要求与本稿有实质差异，原生 Astra/max 修订并交原计划审核链复核。实施写权按顺序移交 `cli.py`、`store_psycopg.py`、迁移与工作流清单。

1. **封存输入。** 在现有 `d1compare.acquire.capture_inputs/read_capture` 和所需 `papersim.adapters` 内接一个可由受管命令调用的输入集登记入口。建议在已有 `tools/d1_compare.py capture-inputs` 上增加可选 `--prepare-config`，沿用 UTC 窗口、标签、受管根及显式公共读取开关。成功返回 `input_set_sha256`。输入集绑定扫描配置原字节、固定决策截点、采集批次的成功回执、每次 Gamma 页／CLOB 买入簿／模型原件的来源时刻、实收时刻、序号、长度与 SHA-256。按既有共享请求数、大小、时限和目标预算，通过传输检查即以现有快照登记原字节，登记成功后才解析。可用的既有原件直接重放。类型、原件、曾声明的来源或回执冲突时整次失败。不得增加连接模块、第三方依赖或文件持久化。
2. **计算及失败。** 从参考代码中逐段核对 `resolve_preparation_inputs` 与 `prepare_market_pack`。从一次一致的注册表视图为每个谱系只取一次 `specs_for_lineage`，同一 `LineageSpecs` 同时供实际计算和 B1。俱乐部足球由 `soccer_model.candidate_for_market` 消费有出处的 xG，保留十变体；加密由既有 `crypto_model`、实际活动规格以及已收妥的现货、闭合 K 线出价。终值只按合约方向换算一次；触价在完整路径和条款未经证实前保留草稿的 `papersim_prepare_crypto_touch_state_unverified`。买 NO、倒序标签和同一原件两次采集按真实标的、买入 token 及请求序号区分。足球以开球、篮球以开球／开场、加密以合约到期字段为界，跨入口修正 `d1compare.packbuild.events_materials` 的 `kickoff or expiry` 回退，向 B2 留原页位置与有时区的准确字段。缺模型、未登记簿龄、未来数据或不支持的目标保留原因与原件引用。已有稳定候选身份和可复算拒绝材料时生成现有 `construction`，仅身份也不能核实的观察计覆盖。合法报价遭闸门拒绝按普通候选记账；不补价格、概率、时钟、阵容或政策。
3. **正式扫描。** 在既有 `run-scan --managed` 加 `--prepare-from`，按输入集选择队列。老配置 `queues[].pack` 继续走原入口，单个队列同时指定两种来源时报歧义。内存市场包经 `scan.records_from_pack → run_scan → assemble_scan → replay.bind_*` 和现有 B1 写者入库。当前解析会丢弃草稿的 `prepared_provenance`，当前 `src/polymarket_alpha_lab/replay.py::bind_candidate` 将 DECISION 的 token 固定为 YES，当前 `run_scan` 又会取一次 provider。对准备模式把经验证的来源、真正买入 token、包 SHA／长度及已选规格 SHA／版本传到底，对照真正用于计算的规格。原操作首次绑定输入集、配置、时段、截点及规格视图；同键重试、提交后断连与并发优先回读首次绑定／成功结果，实际内容冲突明确失败，下一独立扫描才重取现役。准备成功的有序变体数组直接交 FX-47 写者，复用它的新旧行兼容，不再修改其摘要语义。B1 是唯一写扫描、候选、入场及现金行的入口。
4. **并跑新路径接线。** 固定主线的 `tools/d1_compare.py::command_run_new` 经过原有授权门禁后只打印 argv 并返回 2。用受管分派实际执行同一 `run-scan`，只传核实过的输入集、配置、操作键和 attempt，透传退出码与 B1 回执。D1 工具不计算模型报价。B2 的原记录与 B0 按批次／请求序号联结。B3 后来的终局使用不同真实时段的登记和同市场／买入方向，不能成为早先 B0 的输入。固定主线 `settle` 未提供旧计划示例的 `--register-public-outcomes`，按实际父提交核对受管终局生产者；缺接口时交回 B3 持有人并如实列该联测待验，禁止用手工插入或设计示例代签。Path L 的普通 `run-frozen` 在固定主线没有 executor 时会报 `frozen_run_executor_not_wired`，其预检只能证明预检。D1 执行者在开始计日前单独核实旧路真实回执及现有时段门禁，必要接线按独立 D1 节点送审。

正式 B1 和 Path N 的下列参数是本次拟接契约。在隔离测试环境由实际 ZCode 执行者先以候选的 `--help` 核对，再各走正式入口。注册收据、摘要、操作键及 D1 原门禁材料按实取值。

```bash
.venv/bin/python -m polymarket_alpha_lab.papersim.cli run-scan --managed --repo-root "$NODE_TREE" --config "$SCAN_CONFIG" --prepare-from "$INPUT_SET_SHA256" --operation-key "$SCAN_KEY" --attempt 1
.venv/bin/python tools/d1_compare.py run-new --repo-root "$NODE_TREE" --scan-config "$SCAN_CONFIG" --prepare-from "$INPUT_SET_SHA256" --operation-key "$SCAN_KEY" --attempt 1 --authorization "$D1_GATE_RECORD"
```

## 验收及登记

* 空注册表下，从四个回归夹具各自对应的原始输入经过准备器，用独立历史构造器的原请求／失败包比长度、SHA-256 与全部字节。记录 `regression_r23_soccer`、`regression_r19_crypto`、`regression_gate_arithmetic`、`regression_banned_path` 每例的参考版本和首个差异。仅验证夹具自身哈希不足以证明请求一致。输入或参考字节缺失列作未验证，不能结项。相同封存输入及固定规格在两个离线进程、不同枚举次序和实际时钟复算完整包，字节一致、额外联网数为零。
* B1、Path N 在隔离原生测试从登记原件到至少一个足球十变体、一个加密 A/B 报价。独立连接读回候选与扫描 ID、实算模型与规格 SHA、买入 token、DECISION 价格、簿来源、变体有序内容、成功回执；同键重试只有一组业务及现金行。负例覆盖注册失败时解析为零、错误实例／原件、未来数据、缺簿龄、模型不足、买 NO、拒绝可重放、操作键改内容及跨角色切换重试。缺运行资料保留失败和缺口。模型参数或闸门不得为取得正例而修改。
* 由不同于开发和复审的**实际 Linux ZCode** 运行聚焦与全量。命令包括 `.venv/bin/python -m pytest -q -rs tests/test_papersim_prepare.py tests/test_papersim_prepare_inputs.py tests/test_papersim_scan.py tests/test_papersim_d1compare_packbuild.py`、按 Linux 原生工作流启用隔离库运行 `tests/test_papersim_scan_store_native.py` 和新增的 CLI／D1 联测、`.venv/bin/python scripts/verify_local.py --full`、`.venv/bin/python -m compileall -q src tools tests`、`.venv/bin/python -m polymarket_alpha_lab.papersim.cli selfcheck`、`git diff --check "$MAIN_AT_OPEN" HEAD`。按固定主线与候选逐字节比十项自检，四个冻结夹具、四项闸门、十变体、已发布评估数字和优化器数字单源均冻结。同一命令留初次失败、修后结果及实跑／跳过数。新增原生文件纳入 Linux 原生与测试清单核对。
* 节点登记在 `docs/paper-sim/design/fx-40/` 保存计划、适用历史证据引用、本次每份原审与复审回执（含 FAIL）、测试修订与结果，索引写长度与 SHA-256。协调者在 `DELIVERY_PLAN.md` 登记前置合并、输入与输出契约、正常入口、实际测试和剩余 D1/B3 依赖。起草与实质修订用原生 Astra/max，实施及测试代码用原生 Sol/max，均关闭 fast。本计划先取独立实际本机 ZCode guarded 只读 PASS；测试执行者与计划、代码、阶段独立审核者分开。审核禁 shell 和写工具，绑定精确计划摘要或候选 SHA，记录会话和明确结论。有 FAIL 保留原件并交原审返审。
* 推送前在真实父和候选核对可达性，使用已索引仓库的 CodeGraph 同步与蓝图 `audit_static.py`，新增模块从正式 CLI 可达；已跟踪内容仅作脱敏扫描。11-24 的 `test_weakening_scan.py`、`address_occurrence_diff.py`、`added_line_sensitive_scan.py` 按审计锚与最终头运行，禁止跳过、删除、放宽测试或记录任何机器、地址、端口及收件人。取得候选独立代码／阶段 PASS、干净提交与三项 Linux 工作流 Offline verification、Native project PostgreSQL、Research paper bridge 的最终头和实际合并检出证据。运行 `bash docs/handoffs/2026-11-21-pr-review/evidence/check_ci_merge.sh "$PR_NUMBER"`，须读到 CURRENT；缺日志或主线已移动就刷新普通合并、重验当前树。登记运行号、回执路径及普通合并 SHA，不强推、不变基、不改根规则。上述命令均属未来节点验收计划；本次起草不运行扫描、不触及任何数据库、认证、凭据、真实下注或 Windows。
* FX-44 的 OD-P 登记生效后，操作方才开始验收连续七个真实完整 UTC 无人值守影子并跑日。两路径使用同批市场和同期登记原件，按冻结时段逐项给候选差异归因，同日证明现金恒等式、干净提交、零手工改库和抽查可重放。只计日结证据完整的实际连续日，缺天、测试、事后回填及运行层人民币账本不能计入。七日后为 FX-45，OD-Q 取全部入场及其他至多 200 个，种子为实际切换日八位日期，须在 D2 前登记。再做 FX-46 与 FX-39 第二步；正式标准 F 的三十天自真实 D2 起算。
