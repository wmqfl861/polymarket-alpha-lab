# 优化器第一阶段交接书

2026-11-25，NBA v2 在登记的唯一一次正式运行中完成 4,811 个试验，胜负和大小分均保留现役规格。俱乐部足球按 §87.2 的预检收束。优化线本轮覆盖的主线终点是 #129 `631259a22a50ac5d06be7512ed6bbc32c87e3fbf`，NBA 执行版本是 #125 `9acfc3ad07f446415d092153effbc5b81e6b0e77`。R6-1、R6-2 的关闭及项目最终验收由审计方复审。[NBA 终态](../paper-sim/design/optimizer-phase1/2026-11-25/nba-v2-terminal.json) · [#129 合入](../paper-sim/design/optimizer-phase1/2026-11-25/pr129-landed.json)

## 版本和节点

独立增量审核起点为 `2bdc4cccacc925ea8e6569c8865b90c0b0c671e6`，被审版本和终点比较基线为 `ab6edff2d64ac8aa5c2831d748b8730b63f8312d`。#124 用普通合并保留了交付提交 `b3e6f6d3eb3615b74b677658065de43d84147c94`。合入后的固定脚本核对了 34 个文件及清单的 32 项摘要。[原始交付清单](2026-11-24-incremental-review/MANIFEST.sha256) · [合入后核验](../paper-sim/design/fx-36/2026-11-25-pr124-public-identity.json)

| PR | 范围 | 落地合并提交 | CI 测试的 PR 合并对象 | Offline / Native PostgreSQL / Research |
| --- | --- | --- | --- | --- |
| #124 | 接入 | `525d2cbe527c7588eb0ec0e53570dd4f03b1f36c` | `86a317b8` | `45476687574` / `45524997219` / `45476687554` |
| #125 | NBA v2 | `9acfc3ad07f446415d092153effbc5b81e6b0e77` | `35d1812e` | `45533942383` / `45534415758` / `45533942354` |
| #126 | FX-51、FX-52、FX-53 登记 | `56a6b59df074a319b57555fdeec334423d012327` | `5888804c` | `45556953685` / `45557220997` / `45556953646` |
| #127 | 机主决定与受限入口观察 | `9ce8290e90d82ae58928cea3eabca96d20d66217` | `76f62353` | `45568274535` / `45568555979` / `45568274603` |
| #128 | FX-47 `variant_quotes` | `a7dbd8a3e14f5f86f9f26e2f05b76a46927cf4ee` | `260c986035ac81f5ac9e93b3710d9501feaf1794` | `45581744123` / `45581744116` / `45581744049` |
| #129 | FX-54 Python 安装下限 | `631259a22a50ac5d06be7512ed6bbc32c87e3fbf` | `2c46f19952c6ab53ca0f442dff335180eb77d314` | `45601610469` / `45601610520` / `45601610600` |

三条工作流均检出了各 PR 的双父合并对象。GitHub 普通合并产生的提交和 CI 实际检出的对象具有相同的树。每次合入前，固定脚本均报 `CURRENT`。本交接书自身的普通合并提交、实际 CI 检出及运行编号由合并 PR 的记录固定，机主在交接合入时收到同一组编号。[#124](../paper-sim/design/optimizer-phase1/2026-11-25/pr124-exact.json) · [#125](../paper-sim/design/optimizer-phase1/2026-11-25/pr125-post124-exact.json) · [#126](../paper-sim/design/optimizer-phase1/2026-11-25/pr126-exact-ci.json) · [#127](../paper-sim/design/optimizer-phase1/2026-11-25/pr127-exact-ci.json) · [#128](../paper-sim/design/optimizer-phase1/2026-11-25/pr128-exact-ci.json) · [#129](../paper-sim/design/optimizer-phase1/2026-11-25/pr129-exact-ci.json)

## NBA 唯一正式运行

运行号 **4**，`attempt 1`。2026-11-25 09:07:02.412339 UTC 的登记事件为 7。数据库在 **09:07:09.720491 UTC** 记录启动事件 8，在 **09:49:41.610054 UTC** 记录成功事件 9，历时 2,551.889563 秒。CLI 退出 0，输出与数据库封存结果 `NO_IMPROVEMENT` 一致。4,811 次登记试验全部完成，已知重复启动、未执行试验和留出回调均为 0。两个目标都选回原规格，候选登记数为 0。该上限只适用于本次已登记的日程。[终态原件](../paper-sim/design/optimizer-phase1/2026-11-25/nba-v2-terminal.json) · [封存记分卡](../paper-sim/design/optimizer-phase1/2026-11-25/nba-v2-scorecard.json) · [登记回读](../paper-sim/design/optimizer-phase1/2026-11-25/nba-v2-registration-time.json)

以下指标来自 283 个搜索折的 4,248 条预测。两季官方日程各有 1,230 场，两季均缺用于 ESPN 独立重建的逐场摘要和球队日程，两个赛季对应的 A 门必需检查均为 `NOT_ASSESSABLE`。两条目标均未开启留出，没有留出表现或晋级结论。[逐门诊断与数据覆盖](../paper-sim/design/optimizer-phase1/2026-11-25/nba-v2-terminal.json)

| 搜索期诊断 | 场次 | Log loss | Brier | ECE |
| --- | ---: | ---: | ---: | ---: |
| NBA 胜负 | 2,124 | 0.629882 | 0.432240 | 0.045697 |
| NBA 大小分 | 2,124 | 0.666150 | 0.468055 | 0.051737 |

#125 早期归档的 42 项在不可变提交 `b0807e322f8705548e80f3b67ac61a836a5864ef` 上读回。软件补包按 23 份文件的长度和 24 项校验核对，包括阶段和交接的首次 FAIL、原审补证后的 PASS，以及独立执行回执。时钟补包的公开复审覆盖公开投影。本次真实运行另由运行 4 的封存记录证实。[早期归档](../paper-sim/design/fx41-nba-odr-v2/MANIFEST.sha256) · [补包清单](../paper-sim/design/fx41-nba-odr-v2/post125-support-release/MANIFEST.json) · [补包复审](../paper-sim/design/optimizer-phase1/2026-11-25/nba-support-archive-review.json)

## FX-36 至 FX-54 处置

表内工作流顺序为 Offline、Native PostgreSQL、Research。历史 PR 的完整合并提交和运行状态见文末。尚无 PR 的行保留当前状态和原因。

| 项目 | 提交或 PR | 三条工作流 | 处置及回执 |
| --- | --- | --- | --- |
| FX-36 | #120、#127 `9ce8290e` | 45568274535 / 45568555979 / 45568274603 | 机主确认“保持公开，是我有意公开”和“已开启”。两处连接池经分别授权限制后，原测点握手超时，阻断计数增加。原生数据库握手超时。登录入口认证方式尚无有效观测，分支保护待机主启用。R6-1 仍开。[授权原话与外部复查](../../DELIVERY_PLAN.md#8424-fx-36-本地清点) |
| FX-37 | #120 | 44941245957 / 44941382116 / 44941245960 | 五个历史节点的事后复审已完成，原审缺失按实际登记。[清点台账](../paper-sim/design/fx-42/documents.tsv) · [#79](../paper-sim/design/mo-4/reviews/pr-79-retrospective.md) · [#80](../paper-sim/design/mo-5a/reviews/pr-80-retrospective.md) · [#81](../paper-sim/design/b1/reviews/pr-81-retrospective.md) · [#83](../paper-sim/design/mo-2/reviews/pr-83-retrospective.md) · [#84](../paper-sim/design/mo-5b/reviews/pr-84-retrospective.md) |
| FX-38 | #121 | 44947875366 / 44947987844 / 44947875309 | 已修复收盘偏离计算。[源码复审](../paper-sim/design/fx-38-fx-39-step1/source-review.md) · [原生专项](../paper-sim/design/fx-38-fx-39-step1/focused-native.json) |
| FX-39 | #121（第一步） | 44947875366 / 44947987844 / 44947875309 | 第一步阻止未应用规格晋级。第二步等待 M2 切换核对。[源码复审](../paper-sim/design/fx-38-fx-39-step1/source-review.md) · [原生专项](../paper-sim/design/fx-38-fx-39-step1/focused-native.json) |
| FX-40 | 以 #128 `a7dbd8a3` 为基线 | 待发布 | 原计划审者签收 v3 后开始实施。Git 预检覆盖四个冻结夹具和七个固定提交，尚未取得完整历史请求及受管快照的保管证据。[原计划签收](../paper-sim/design/optimizer-phase1/2026-11-24/plan-review-receipts.json) · [预检范围与缺项](../paper-sim/design/optimizer-phase1/2026-11-25/fx40-history-inventory-summary.json) |
| FX-41 | #125 `9acfc3ad` | 45533942383 / 45534415758 / 45533942354 | NBA 运行 4 完成，俱乐部足球以 §87.2 的 W45 预检结案。[NBA 封存记录](../paper-sim/design/optimizer-phase1/2026-11-25/nba-v2-terminal.json) · [俱乐部签收](../paper-sim/design/fx-41/club-w45-review-receipt.json) |
| FX-42 | #120 | 44941245957 / 44941382116 / 44941245960 | 127 项历史材料清点结束，36 件原件、1 件脱敏件、90 件具名原件未找回。[清点台账](../paper-sim/design/fx-42/documents.tsv) |
| FX-43 | #122 | 44980320797 / 44980585979 / 44980320763 | 登记簿已补到 #122，#123 在 FX-52 登记。[第 84 和 87 节](../../DELIVERY_PLAN.md) |
| FX-44 | 计划 v2 | 待产生 | 原计划审者通过 v2，计划摘要 `82955f0bba461c8ef7f45363da153fab9765646a33e424ec9f13436ef1ec0846`。按 R6-17 分别登记两侧有价行，排在 FX-40 后。 [计划原件摘要和原审历次判词](../paper-sim/design/optimizer-phase1/2026-11-24/plan-review-receipts.json) |
| FX-45 | 待前置节点 | 待产生 | 待 FX-44 和七个合格日并跑，再办理切换登记。 |
| FX-46 | 待 FX-45 | 待产生 | 待 FX-45 后登记市场准备入口、切换时间和标准 F 的 30 天观测。 |
| FX-47 | #128 `a7dbd8a3` | 45581744123 / 45581744116 / 45581744049 | 字符串及数组报价经同一市场包走原生 PostgreSQL，读回一致且重扫幂等。23 个原生用例在修正实现 `14f516ca` 上通过，#128 的整合复审按相同的实现与测试对象承接该证据。[整合原审 PASS](../paper-sim/design/optimizer-phase1/2026-11-25/pr128-integration-review.json) · [原生执行回执](../paper-sim/design/fx-47/native-scan-replay.jsonl) |
| FX-48 | 计划 v2 | 待产生 | 原计划审者通过 v2，摘要 `b4cff1e3f92214144d0258e117363ca9a6df3b322cb07fe1137b8b357c0d8f34`。首 PR 位于 FX-49 之后。运行定时器的启用日期及部署提交待现场登记。 [计划原件摘要和原审历次判词](../paper-sim/design/optimizer-phase1/2026-11-24/plan-review-receipts.json) |
| FX-49 | 现场登记 #127 `9ce8290e` | 45568274535 / 45568555979 / 45568274603 | 固定脚本 `docs/paper-sim/runtime/scripts/windows/Invoke-PaperWeixin.ps1`，SHA-256 `e86beca8f4073da75a8bc1cd3aeef3e85230417c0b19f9424432e79baa7998c6`。Windows 当前策略 `Restricted`，约定位置未安装固定脚本。六类现场验收 0/6，部署提交与自然投递回执待取得。R6-2 仍开。[脚本指纹](../paper-sim/design/optimizer-phase1/2026-11-24/fx49-direct-script-objects.json) · [现场回执](../paper-sim/design/fx-49/2026-11-25-owner-fixed-entry-observation.json) |
| FX-50 | OD-X 已选 A | 待产生 | 在 FX-49 后登记例外和迁移台账，M2 切换时再评估归并。实际数据库尚未变更。 |
| FX-51 | #126 `56a6b59d` | 45556953685 / 45557220997 / 45556953646 | 足篮准入、模拟下注规则、模型版本和来源已登记。人民币账本用于模拟盈亏，正式晋级按登记的验证门槛判定。[终审](../paper-sim/design/fx-51/registration-review-final.json) |
| FX-52 | #126 `56a6b59d` | 45556953685 / 45557220997 / 45556953646 | 节点登记簿已纳入 #123、11 月 22 日原话和完整根文件哈希。OD-U 完整正文待机主确认。[初审](../paper-sim/design/fx-52/pr-123-review-initial.txt) · [终审](../paper-sim/design/fx-52/pr-123-review-final.txt) |
| FX-53 | #126 `56a6b59d` | 45556953685 / 45557220997 / 45556953646 | 四个小修已登记，按单写者顺序排期。[小修登记及证据](../paper-sim/design/fx-53/wp-p4-anchor-20261124.txt) · [§84.2.3 台账](../../DELIVERY_PLAN.md) |
| FX-54 | #129 `631259a2` | 45601610469 / 45601610520 / 45601610600 | Python 和锁文件下限均为 `>=3.12`。原审对候选 `e94756c8` 签署 PASS。专项 318 项通过，全套 44,016 项通过，既有离线条件跳过 464 项。托管版本切换 89 项通过且零跳过。早期两次源码复审 FAIL、三次全套尝试和旧同级证据提交 `472fbc19` 保留在历史记录中。[原审](../paper-sim/design/optimizer-phase1/2026-11-25/pr129-original-review.json) · [专项](../paper-sim/design/optimizer-phase1/2026-11-25/pr129-focused.json) · [全套](../paper-sim/design/optimizer-phase1/2026-11-25/pr129-full.json) · [托管验收](../paper-sim/design/optimizer-phase1/2026-11-25/pr129-hosted-supplement.json) · [历次验证](../paper-sim/design/optimizer-phase1/2026-11-25/local-lanes.json) |

## 真实数据和现场验收

NBA 的登记、预览、搜索、原规格选择及结果封存走过真实历史数据。候选晋级、留出期比较和登记后的替换路径只有隔离测试证据。俱乐部足球只执行 W45 预检。每个目标能取得的因果上界为 3 周、229 场，与原门槛 20 周、300 场相比，至少还差 **17 个合格周、71 场**。满足赛前可用的数据要求并登记新日程后可重跑。[§87.2](../../DELIVERY_PLAN.md#872-fx-41-与-fx-43-俱乐部足球固定-w45-预检) · [预检原审](../paper-sim/design/fx-41/club-w45-review-receipt.json)

FX-47 有真实 PostgreSQL 引擎上的合成市场包证据。FX-49 的人工 HC → Hermes 投递曾获机主确认。固定脚本在 Linux 的替身与隔离库验证覆盖代码路径；Windows 现场的六种替身验收均待执行，一次自然自动投递、部署检出及仅准固定入口的约束仍待核实。机主选择满足固定入口条件后切换。[现场核对](../paper-sim/design/fx-49/2026-11-25-owner-fixed-entry-observation.json) · [机主原话及各项决定](../../DELIVERY_PLAN.md#8424-fx-36-本地清点)

## 历史三张记分卡

三次运行的原件来自提交 `e234c3364c204db513cc4fbcdcb6d5eb43c7417b`，历史归档 88845 字节，SHA-256 是 `03c616e1d94efe5e3a7879e3277645b5d60e4e407bdef6e9d31d9f7eb3366ad5`。2026-11-21T18:07:57.789647+00:00 的核对状态是 `MATCH`，三份回读、精确重放等 11 个文件按字节与摘要匹配，核对没有新建数据库连接，也没有重算。 [原件、Git 对象和比较器字段](../paper-sim/design/optimizer-phase1/2026-11-24/historical-evidence-origins.json)

| 项 | 俱乐部足球再校准 | 国家队进球残差 | NBA 节奏与效率 |
|---|---|---|---|
| `run_id` | 1 | 2 | 3 |
| 模型 | `club-soccer-recalib` | `intl-elo-goal-residuals` | `nba-pace-efficiency` |
| 原始记分卡 SHA-256 | `b27ca7855ae147d27c76537289fd904c61f1fdb95be28fb5815cbd5f12a34fb9` | `e47b518723c52ac369562af6a11b85ae24cb4c62fb6262e6ae298784a91e3d65` | `8d7b2a4f498aa99ae20349d6744657c37a785ea30d097e19f44ddc743d57fe10` |
| 结果文件 | `09-1-club-soccer-recalib.result.json`，18050 字节，`d8f806dfa2fe048a547d992c084fea8ae1f3e47077c3ba8d77c2e18f99a5bdd8` | `09-2-intl-elo-goal-residuals.result.json`，205757 字节，`d4514a169811f2d0e1e98215e449826618f4c24a339140c8bf7976c0e7968d18` | `09-3-nba-pace-efficiency.result.json`，5167 字节，`01f7f287ca33f2e8fc8cb0fe87d0ec034abfd125acbb76680e3ab295fae11a35` |
| 重放文件 | `14-club-soccer-recalib.replay.json`，1457 字节，`174ab54c54d248e4fec1c60e771db4760ef1f91b63d39fe1a9f72d92270d845d` | `14-intl-elo-goal-residuals.replay.json`，1409 字节，`15501933f607f565ccea2010697bdbb49905cff29e78b15b4041be1b40c56f99` | `14-nba-pace-efficiency.replay.json`，1448 字节，`706921dc6f558210b7d221033342c36969f0a2d05af54af3c87d185cf144e251` |
| 截止时间与规格标识 | 2026-11-10 10:33:51，`mo6-club-official-round-suffix-v1` | 2026-10-07 00:00:00，`mo6-intl-complete-day-suffix-v1` | 2026-11-10 05:00:00，`mo6-nba-espn-november-suffix-v1` |
| 登记行计数 | 1/57 对 20/300，65/65 与 1 规格 | 227/1803 对 0/300，864/864 与 36 规格 | 8/66 对 30/300，10/9 与 1 空试验、1 规格 |
| 指标，斜线前后是 log_loss 与 brier | 胜平负 639 场 1.026166 / 0.614938，大小球同 639 场共 1,917 条盘口线 0.596451 / 0.409710 | 大小球 2.5 共 191 场 0.687870 / 0.486797 | 胜负及 225.5 大小分均为 74 场中可评分的 72 场，分别是 0.937356 / 0.576808 和 0.916230 / 0.585333 |
| 结论与留出 | `INSUFFICIENT_DATA`，留出 57 场、1 个比赛周 | `NO_IMPROVEMENT`，保留现役规格，封存的 1,803 场留出未开启 | `INSUFFICIENT_DATA`，留出 66 场、8 个比赛日，搜索中 1 个空试验 |
| 诊断卡里 FAIL 的必需门 | `independent_score_join_coverage` | `mean_total_bias` | `expected_vs_actual_total_pearson_p`、`expected_vs_actual_total_pearson_r`、`net_rating_vs_win_rate_spearman`、`walk_forward_beats_coin_flip` |

三张卡均未触发留出回调，历史原件留在已登记的受管实例和操作员归档。投影展示各项原始观测值，文件为 65,443 字节，SHA-256 `8fdf3a314059410830a4ddd426f7f8610db4ecd88da85867390d625918064e33`。上表的三项原始记分卡摘要见逐件回读。[三卡逐项投影](../paper-sim/design/optimizer-phase1/2026-11-24/historical-scorecards-projection.json) · [原件匹配及三次回读](../paper-sim/design/optimizer-phase1/2026-11-24/historical-scorecards-readback.json)

运行 4 封存后，再从受管历史库以只读事务提取四张完整记分卡的规范字节。四个文件逐一匹配原封存 SHA-256，三张历史原卡按上述列顺序保留。[俱乐部原卡](../paper-sim/design/optimizer-phase1/2026-11-25/historical-run-1-scorecard.json) · [国家队原卡](../paper-sim/design/optimizer-phase1/2026-11-25/historical-run-2-scorecard.json) · [NBA 原卡](../paper-sim/design/optimizer-phase1/2026-11-25/historical-run-3-scorecard.json) · [四卡字节和摘要](../paper-sim/design/optimizer-phase1/2026-11-25/scorecard-readback.json)

## 空注册表和冻结基线

来源回执的目标检出 `h` 是 `26dfa2a05357121665009981cd600e23c91e093a`，来源回执 56675 字节，SHA-256 是 `1951b0f62edcbbcc64dda256ee28058d612ef0f70b60daa8ae91e5124f6ad5f7`。监督回执的 SHA-256 是 `edc8c8496533f75f457be4992241f26fe56e486b07b03003b50b687cf3763eec`，自检 1663 字节，SHA-256 是 `a4aa971fed26553474e0b69a285bc2937c44789bbeb1ac6c70bb0d827610fcb9`。[逐入口对照](../paper-sim/design/optimizer-phase1/2026-11-24/empty-registry-comparison.json)。 [原件、Git 对象和比较器字段](../paper-sim/design/optimizer-phase1/2026-11-24/historical-evidence-origins.json)

| 冻结夹具 | blob 值 |
|---|---|
| [`regression_banned_path.json`](../../tests/fixtures/papersim/regression_banned_path.json) | `a4cedf029fba95222a411856484e9d98449329a8` |
| [`regression_gate_arithmetic.json`](../../tests/fixtures/papersim/regression_gate_arithmetic.json) | `c9841b860f3389bcedcfcb805b9086888a903b26` |
| [`regression_r19_crypto.json`](../../tests/fixtures/papersim/regression_r19_crypto.json) | `5b1deeab1aa39cd648c71f678ebb1a9f86ce8053` |
| [`regression_r23_soccer.json`](../../tests/fixtures/papersim/regression_r23_soccer.json) | `4bfccb907890253078e1aee0a39b51ec45f590af` |

| CLI | 对 `af4ff553262a7ee532e36e1b0d8ba4939522f4ba` 的原始字节 | 对 `e234c3364c204db513cc4fbcdcb6d5eb43c7417b` 的原始字节 | `different_json_pointers` | 稳定的业务投影 |
|---|---|---|---|---|
| `run-scan` | 不相等，参考 578 字节，目标 578 字节 | 不相等，参考 579 字节，目标 578 字节 | `/code_commit`、`/scan_key`、`/shadow_seconds` | 365 字节，`a594c0328f3ce1d623eb925cb99f3518b58fb9c4b268fc5e509582f4985c2271` |
| `replay` | 不相等，参考 336 字节，目标 336 字节 | 不相等，参考 336 字节，目标 336 字节 | `/code_commit` | 255 字节，`5d318ab5b088f7711414e4ca1f18665dff7e152b12bbbe5ebc3d2bfd3cb28b50` |
| `settle` | 不相等，参考 363 字节，目标 363 字节 | 不相等，参考 363 字节，目标 363 字节 | `/code_commit` | 279 字节，`6de1743935aaf9cc711bda2049a947d0ce25ac2fde2dd00cedc071ff22377b83` |
| `evaluate` | 相等，参考与目标都是 4407 字节 | 相等，参考与目标都是 4407 字节 | 无 | 4059 字节，`9c18ecbb6b8c69d9ee61574cbc2607da6822619591309e7a8b906c9eb5b880ba` |
| `report` | 不相等，参考 6351 字节，目标 6351 字节 | 不相等，参考 6351 字节，目标 6351 字节 | 10 个，见下文 | 5838 字节，`5568d91bb7d8ca47b579c6cd7b9fac32b140b47d9f5357c5ac7388695bad432a` |

五个入口分别对两个基线比较，共十组，只有 `evaluate` 的 2 项原始字节相等，其余 8 项不相等。`report` 的 10 个差异指针是 `/as_of`、`/receipts/last_phase_by_operation/0/last_occurred_at`、`/receipts/last_phase_by_operation/1/last_occurred_at`、`/stages/outcomes/as_of`、`/stages/scan/as_of`、`/stages/scan/last_scan/code_commit`、`/stages/scan/last_scan/finished_at`、`/stages/scan/last_scan/scan_key`、`/stages/scan/last_scan/started_at`、`/stages/settle/as_of`。目标侧五个 SHA-256 依次是 `db1380c39a1a5018a1abde4ce97a91f910cd80a3f677fcb563d37c1a54975549`、`4f381d3df9319cf5b9a759fb0ef29838709825e019e31dbbdca27f5d3245decf`、`02a4ef21dec532e03ea2aa9a78562e9d25147e011333a17f89436a19d07dfbb4`、`280f12c48d8443d80c31781529851a3ed3a790f2567e3dc2d0884c0b7f328455`、`50c9ab8f56a0edf9bbcb6294ea638a060fb8c15c0653735d455049de70a9ab1f`。数据库业务行在两个基线下相同。`model_specs`、`model_role_events`、`shadow_predictions` 都是 0 字节 0 行，SHA-256 同为 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。其余六类的字节数与行数是 `candidates` 6037 与 1、`candidate_price_evidence` 623 与 1、`entries` 354 与 1、`bankroll_events` 958 与 3、`market_outcomes` 643 与 1、`settlements` 507 与 1。

比较器对每个 CLI 去除上表及本段列出的差异指针，`evaluate` 保留所有字段，再排序对象键并以紧凑 JSON 编码，保留数组顺序和数字写法。原件的目标提交、两个基线以及各入口排除的完整字段集见[比较来源及投影字段](../paper-sim/design/optimizer-phase1/2026-11-24/historical-evidence-origins.json)。

**2026-11-24 的 FX-41 补充核验。** 基线 `ab6edff2d64ac8aa5c2831d748b8730b63f8312d` 和候选 `2933a375fdc3c248514968277effb9a699097131` 分别在干净检出、CPython 3.12.3 下自检通过 10/10。两份 stdout 逐字节相同，各 1,663 字节，SHA-256 同为 `a4aa971fed26553474e0b69a285bc2937c44789bbeb1ac6c70bb0d827610fcb9`。stderr 两边均为空。候选的 `tests/test_papersim_optimize_metrics.py::test_anchor2_nba_group_logloss` 使用 31 个历史夹具分组，1 项通过，1.74 秒，退出 0。另有 base/candidate 标签目录各两份静态报告输出记录。每对完整报告和摘要逐字节相同，四份退出记录均为 0 且 stderr 为空。运行记录没有绑定检出提交、树状态或所调用脚本。事后读取的两检出当前分别在上述基线和候选提交且工作树干净；审计脚本在两提交上的 Git blob 相同，但不能据此绑定此前运行。两份重复报告均与已归档报告字节不同。下述静态摘要数字仅按 base/candidate 目录标签报告。自检使用合成输入；logloss 锚点使用 31 组历史夹具。不含根 `__init__` 的可达模块为 468 与 470，不可达模块均为 2,442。次日的正式运行已封存为运行 4，#125 的合并检出见本书节点表。[锚点、自检及重复静态审计](../paper-sim/design/optimizer-phase1/2026-11-24/nba-anchor-selfcheck-and-repeat-static.json)

## 终点核对及偏差

本轮两项扫描的基线均为 `ab6edff2d64ac8aa5c2831d748b8730b63f8312d`。下列是已知测试改名的逐项证明。交付候选及其实际合入对象分别执行交接包原脚本；最终回执随 PR 固定实际提交和输出摘要。

这 1 项来自 #125 的源码提交 `2933a375`。`tests/test_papersim_cli_optimize.py` 将 `test_optimize_help_names_the_four_subcommands` 改名为 `test_optimize_help_names_the_experiment_subcommands`。新用例继续验证 `families`、`init`、`run` 和 `status` 出现在帮助中，并增加 `register-experiment`。两版各有 2 个 `assert` 和 1 个 `pytest.raises`。逐节点比对确认，仅函数名和新增的命令标记有差异。改名后的用例在独立检出中单独运行通过。[源码比较及 pytest 回执](../paper-sim/design/optimizer-phase1/2026-11-25/pr125-cli-help-rename.json)

机主选择维持公开并已确认邮箱隐私。分支保护仍待本人启用，OD-U、OD-S 的完整根规则正文仍待本人确认。OD-W 已按机主建议登记个人足篮重点关注及模拟单，OD-X 已登记路 A。四类日常运行照常。M2 顺序为 FX-40、FX-44、七个合格日并跑、FX-45、FX-46、FX-39 第二步。FX-44 将按 R6-17 分别登记两侧有价行。FX-48 和 FX-50 的首批实施等待 FX-49 部署。[整改次序](2026-11-24-incremental-review/02-remediation-and-next-steps.md) · [决定登记](../../DELIVERY_PLAN.md)

## 历史 PR 及三条工作流

| PR | 合并提交 | head 提交 | Offline verification | Native project PostgreSQL | Research paper bridge |
|---|---|---|---|---|---|
| #114 | `d216059339dba9c3ca34d65813e9cf2ba98a0dd5` | `9647a3ef68c18e5fd5356aeb81bf8df06976a433` | 44928160743 `pull_request` `CURRENT` | 44928160706 `pull_request` `CURRENT` | 44928160726 `pull_request` `CURRENT` |
| #116 | `cd53ce25f65716e6151238beba253618caca7e98` | `8c7b536514eda6dfc772bf7fc6a4fc597892a294` | 44850543464 `pull_request` `STALE` | 44850545864 `workflow_dispatch` `-` | 44850542816 `pull_request` `STALE` |
| #118 | `00199b24d38d5d9334ec7eb07ca8141db33852da` | `b89120f2fa79c6ecf5f371376fc00f1ccd10809c` | 44968419459 `pull_request` `CURRENT` | 44968419447 `pull_request` `CURRENT` | 44968419472 `pull_request` `CURRENT` |
| #119 | `a3492ea66d3d9e18d62d1e8c4919582710124416` | `9a38b62827d35d524e05d71f0d2f780c16baa86c` | 44911055691 `pull_request` `CURRENT` | 44911081568 `workflow_dispatch` `-` | 44911055726 `pull_request` `CURRENT` |
| #120 | `0c64a98946dcbdb5ed550c5a57c0f6127837fb76` | `50267f7e46e44295c7d5613c125e81a0befd0bd0` | 44941245957 `pull_request` `CURRENT` | 44941382116 `workflow_dispatch` 在匹配的合成合并上派发成功 | 44941245960 `pull_request` `CURRENT` |
| #121 | `01a3d6d208fb7a22886f3cc0b54f4b6499d83400` | `d362b51067f58f7ec3416ccea702859f63cf3c57` | 44947875366 `pull_request` `CURRENT` | 44947987844 `workflow_dispatch` 在匹配的合成合并上派发成功 | 44947875309 `pull_request` `CURRENT` |
| #122 | `6d880008fc183a259298b6065ff6d5cb19b66fc3` | `8bb0dce7cff89d162b0aed8ab8498c73cd5fd328` | 44980320797 `pull_request` `CURRENT` | 44980585979 `workflow_dispatch` 在匹配的合成合并上派发成功 | 44980320763 `pull_request` `CURRENT` |
| #123 | `ab6edff2d64ac8aa5c2831d748b8730b63f8312d` | `2fbcd0d858bd1511c454edd65f568394642f08d8` | 45169585443 `pull_request` `CURRENT` | 45170610241 `workflow_dispatch` `-` | 45169585610 `pull_request` `CURRENT` |

24 条历史运行有各自的事件类型和核对结果。#116 为已归档的例外。#119 和 #123 的原生 PostgreSQL 是手动派发，表内保留其原始标记。历史运行号仅归属于相应 PR。证据来自[合并检出核对](2026-11-24-incremental-review/evidence/ci_merge_audit.tsv)、[原生派发记录](2026-11-24-incremental-review/evidence/ci_native_dispatch_runs.tsv)和 [PR 登记](2026-11-24-incremental-review/evidence/pr_metadata.tsv)。
