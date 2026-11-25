# FX-41 NBA v2 单次历史研究计划

11-24 交接 02 的 OD-V 与 03 的步骤 6 规定，本节点在支持代码合入 `main` 后，向已登记的 NBA 日程发出一次正式命令，按实收录其合法终态。先由搜索选择规格，只有被锁定为挑战者的谱系才会打开留出。交接分别记计划留出、实际开放、完成比较和候选处置。11-21 单次批复原文为“批准单次方案，先登记再执行”。本稿记录对接流程，正式登记事件、运行号和新卡以之后的受管回执为准。

## 既有决定和复审

保留草稿 `handoff/sports-fx41-nba-v2-20261123` 的提交为 `256f8491c324b04e5dd9789c012ae84f654853f2`，树为 `30bee4fbe79d897dbadbe45514673112956f308f`。在本机已有证据区按下列指针找到原审目录，核过各 `receipt.json` 的退出码 0、附件计划 SHA-256、结果字节与 `stdout_sha256` 一致，结果末行均为 `VERDICT: PASS`。协调者可用项目沙箱的 `tmp/pal-pr-review-20261121.*/` 定位该目录，以文件摘要消除同名歧义。

| 审核范围 | 原目录指针 | 签字附件 SHA-256 | 回执 SHA-256 |
| --- | --- | --- | --- |
| 单次登记与科学计划 | `fx41-nba-v2-registration-plan-review-path.txt` | `558a5f27f97827d54360b62e71336fe012d56e20e807a893a3894d8318fb321f` | `62130ffc43c6d1bc7daa65cbaeac39bc2f17995745e3861ebadc182b54c62b81` |
| 每规格六变体补遗 | `fx41-nba-six-variant-rereview-path.txt` | `ca3e7eb9606424b510d5dd14c404398a7f8984fbbdd7db7dd3ea3f372dc59f9c` | `43aa4f895b6299616a24bc63cafd017924620e9eeccc7e4bffdef90101d16372` |
| 受管登记、认领、只读预览 | `fx41-nba-managed-binding-plan-review-path.txt` | `e0590ca6c43b2da74b949f920618b3117c1772b23a073bf2ca833a2edf597c7c` | `79535cbbbc67c8f21cca7b588b032de11c454c011961f5a08b9ea9788da153aa` |
| #118 后集成、计时、正式执行 | `fx41-nba-v2-post118-managed-release-review-path.txt` | `137a73862e08c75308c467f3f6fcbf64bbb5d5d88d95f87508cf4b20873cbfe0` | `57fd537183c37abccb0dd2eb69f0661661ff63fc75977d90135cf5589ad9f3d8` |

这些已签计划、补遗及适用的历史模块复审直接引用。交接 01 写明对保留草稿只作清点。收集本节点最终代码、冲突处理与阶段签字时，按对象、范围、附件哈希和结果字节查找已有实际回执，只为新增或实质变动的部分安排项目独立只读 ZCode 复审。保留原 FAIL 和同一原审者的复审结果。维护者将合用原件依 FX-42 登记，公开材料沿用项目脱敏规则。

## 封存的输入、参数与边界

| 输入原件 | 字节 | SHA-256 |
| --- | ---: | --- |
| 证据区 `fx41-nba-single-run-owner-approval.json`，观察时间 2026-11-21 17:13:29.760019 UTC | 1,517 | `bc33ccee3864a6b33116105f8fc373f0acc614eb34b1148d04ccf69e2dfd4313` |
| 同一区 `fx41-nba-source-readback.*/nba-source-inventory.json`，观察时间 2026-11-21 17:09:48.289725 UTC | 1,465 | `f3bf0c46a3596de85353327b8fead7ceed274f7c00ee8aa88648eb9586c5dd48` |
| 仓库 `docs/paper-sim/research/league-admission-20261119/nba/games-2024-25.json.gz`，封存时间 2026-11-19 01:54:32.830906 UTC | 39,916 | `a71489ae2ee8a3afddd4c8520ce4e93b3005c399590cae83791e493f215a902d` |
| 同目录 `games-2025-26.json.gz`，封存时间 2026-11-19 01:50:48.289155 UTC | 39,985 | `893f998ea4cd6ef45b20c575f96675d325ca4b9e1a185f3299d52a125a8eb136` |

四个原件已在工作区逐件按长度和 SHA-256 核过。两份压缩包解压后的原字节分别为 424,531／`8e076559f766cfb1cf03376e143c96c504eda84703e6a1d44f2b3bfcb9090d0d` 和 424,994／`f4930fd430a3c2ca2b45874b7828e15dce992702c8ea5c7c112bcc5fdc2166f8`。登记时用最终干净执行树中的压缩原件和两份证据原件，核来源时钟。独立 ESPN 来源库存显示每季球队原赛程 0/30、逐场原摘要 0/1,230，相应 Gate A 检查保留 `NOT_ASSESSABLE`。若搜索锁定挑战者，可按协议比较，未核实的必需质量证据会阻止相应候选和统计性拒绝判定。使用登记的缺项及原因。

固定日程 `fx41-odr-nba-regular-2024-25-2025-26-v2`，族 `nba-pace-efficiency`，程序 `wp-nba2`，规则 v1，数据截止 `2026-11-19T02:00:00Z`，种子 `20261120`。按 `America/New_York` 的来源比赛日分组，每季 30 队、每队 82 场、1,230 场。两季合计 327 日、2,460 场。搜索为截至 2026-03-04 的 289 日、2,156 场，每季头三个完整比赛日仅训练，合计 6 日、32 场。余下 283 个折外测试日、2,124 场按同季完整因果前缀训练。17 个完整规格各含原登记六变体，网格 SHA-256 `71dd8e754ee83e6b359156da637bdcdfced8724560bb371b6cd668759c44d60d`。283 × 17 为 4,811 个物理试验，每试验共享拟合，评胜负和总分 225.5，合计 9,622 个目标使用。留出为 2026-03-05 至 04-12 连续来源赛程末段的 38 个完整比赛日、304 场，再去一日只剩 295 场。每目标仍需 30 个完整日及 300 场。以具体谱系实际共同可评分比赛核验。

4,811 限额仅随这一固定日程、截止、登记回执、材料、受管实例绑定和首次认领取得。旧公开 Python 入口、旧日程和其他族沿用 2,000 次上限。登记 v2 使用相同 3,600 秒墙钟及 60 秒收尾预留。规则数值继续从原所有者读取，`tests/test_papersim_optimize_rule_sources.py` 的原断言沿实际函数名定位，新增断言核公开和受管两个入口都委派到原核心。固定样本下限、门阈、检验、网格、掩码、种子、时钟和已出版评估锚点。本节点只运行已登记的一次历史纸面研究。

## 集成、测试与当前回执

1. 在届时的当前 `main` 上开隔离工作树并记录基线、候选 SHA 与树，以保留草稿的 13 文件差异为起点。按协调者安排请 FX-47、FX-53 第一项及 NBA v2 先后交还 `src/polymarket_alpha_lab/papersim/store_psycopg.py` 写权。后接者同时保留已落库修正与 NBA 登记、预览、认领及一次发布语义，核冲突处实际 diff。两季数据、原卡、旧迁移均沿原版本。实现或修复由原生开发者承担，测试和独立验收按项目规则交实际 ZCode。
2. 离线聚焦用项目解释器和 `pytest -q` 执行 `test_papersim_nba_odr_registration.py`、`test_papersim_optimize_nba_odr.py`、`test_papersim_cli_optimize.py`、`test_papersim_optimize_runner.py`、`test_papersim_optimize_search.py`、`test_papersim_optimize_rule_sources.py`（均在 `tests/`）。覆盖被替换的输入、丢失登记、伪造快照/比较者、公开入口拒绝超限、4,810/4,811/4,812、等长错序及掩码、固定种子、跨季/同日泄漏、留出短路和现役获胜。保留完整原断言。Linux 全量按 `.venv/bin/python scripts/verify_local.py --full` 执行，保留首败、修正和环境跳过原因。此前支持快照 `077a5e8e` 记录聚焦 461 通过、原生聚焦 7 通过、原生 papersim 分区 852 通过。该快照全量因上述两项规则所有权断言失败。保留草稿已将它们对准实际核心函数，最终集成树须交实测结果。
3. 在一次性、已迁移的 PostgreSQL 18 测试库，按 Native 工作流 papersim 分区准备隔离受管夹具，实跑 `tests/test_papersim_optimize_registered_runner_native.py` 的 `native_nba_v2` 系列及原分区。核登记原子性、幂等和冲突、应用角色与事务绑定、拒绝外部回调、已提交/已回滚断连、锁竞争、先前中断不可再搜、失败及成功封存的只读读回。只读预览零新增行、零成绩读取、零模型和留出调用。核完整有序计划或真实失败前缀。四个冻结夹具逐字节比对，十变体、四闸门、自检十项逐字节及既有评估锚点照常验。记录 `git diff --check`、编译、敏感变更检查和 CodeGraph 同步。
4. 最终候选的完整合成受管计时沿用已审发布清单。本机已有 `fx41-nba-v2-full-managed-driver-sol.py::test_full_managed` 以及纯计算实测。在已定位的完整受管计时准备目录只见启动脚本。先查同一最终支持树的原始计时回执，若缺，使用隔离合成库、可比资源及相同解释器补齐。这项测试须覆盖冷启动登记/预览、计算、组装实际或已界定的双侧工件、写入/提交确认、CLI 返回和独立读回。完整预算上界须同时满足入口至计算结束 < 3,540 秒、入口至返回 < 3,600 秒。只有一条谱系自然开放时，沿已审方法以实测补齐另一侧成本。缺项登记“时钟未证”，触界记 `NO-GO`、实测值及差额。纯段用时及准备脚本各自按原证据等级归档，合成计时不占正式名额。
5. 合并前分别核 `Offline verification`、`Native project PostgreSQL`、`Research paper bridge` 的实际 checkout、PR 头、两父、树、结论及运行编号/attempt。用 11-21 的 `evidence/check_ci_merge.sh` 对该 PR 输出 `CURRENT`，另从每条 workflow 的回执查验其实际树。若 Native 被路径过滤，用同一合并对象做已规定的原生分发。基线变动时在新合并对象刷新三条 CI 与受影响证据。合入后记录实际 `main` SHA 和树，核它与已测合并树、支持代码及所引用合成计时相符。增量终点对 `ab6edff2d64ac8aa5c2831d748b8730b63f8312d` 跑 11-24 的 `test_weakening_scan.py`、`address_occurrence_diff.py`，逐条说明既有断言有据的移动，核无新敏感路径。正式登记、预览、执行都用同一干净的已合入执行 SHA。

## 登记、预览及唯一正式调用

本机历史归档位于项目沙箱的 `projects/pal-mo6-historical-archive-20261120`。本次只读文件清点见其 PostgreSQL 资料目录标志，其源码当前指向三次原运行的 `e234c3364c204db513cc4fbcdcb6d5eb43c7417b`。这项观察不代表实例此刻正在运行，亦不能替代最终受管身份核验。操作时确认指定隔离历史库、相同受管根、应用角色、迁移账本和原三次封存记录。按已审流程让该根使用最终干净支持提交，保留原数据。预防误连用项目受管 API 核验，操作材料对外统一称“历史库”和“合成库”，不导出连接资料。首次登记前确认两谱系基线和现役角色可用，并在认领时再次比对，冲突交回查证。

保留草稿提供实际 CLI `python -m polymarket_alpha_lab.papersim.cli optimize register-experiment`、`optimize run` 及 store 的 `read_managed_nba_odr_registration`。旧工作区另有 `fx41-nba-v2-historical-run-sol/{run.py,checks.py,receipts.py}` 作为私有证据助手，其检查绑定旧支持提交 `077a5e8e`。使用助手前核适用对象和校验语义。可以直接通过下列项目 CLI 和独立受管读回履行协议。下文用 `research_root` 表示上述已核根，`project_python` 表示从该提交加载模块的项目解释器，`approval_file`、`inventory_file` 表示上表两份本机原件，`pointer` 指工作树外的私有临时文件。以下按步骤分别执行，仅第 4 步发起正式研究。

1. **先查既有登记及运行。** 另一受管应用连接按固定操作键 `optimize.register-experiment/fx41-odr-nba-regular-2024-25-2025-26-v2` 查 attempt 1 的 `SUCCEEDED` 事件，核正式实验同科学键的 `STARTED`、终态及 `optimization_runs`。存在登记时，用原提交、同一实例和原清单重建复核，复用第一次登记与指针。若新旧绑定冲突，保留首因。已有正式根立即转第 5 步。首次且所有条件满足时才进行下面的登记，绝不新建另一个日程或实例以绕开已有记录。

   ```bash
   "$project_python" -m polymarket_alpha_lab.papersim.cli optimize register-experiment \
     --managed --repo-root "$research_root" --approval-file "$approval_file" \
     --source-inventory-file "$inventory_file" --write-pointer "$pointer"
   ```

2. **独立证明登记已提交。** 以第二受管连接用 `read_managed_nba_odr_registration` 读回唯一事件和规范原件。核登记 ID/UTC、固定操作键、config digest、科学 run_key、清单、材料、比较者、计划元数据与物理计划、全部来源的种类/版本/SHA-256/长度及适用时钟。指针只含 `schema_version`、`manifest_sha256`、`manifest_byte_length`。重建同季成员、训练日、17 个有序规格、每折每规格双目标掩码和留出前缀。同一科学键没有正式根、结果、候选增量。登记外层提交回复不明时只通过受管确认和原键读回，指针导出失败也从已确认登记恢复。
3. **同一请求做只读预览。** 在项目根所在的干净环境逐一执行，保留返回值。该日程的预览只返回元数据。比对登记/科学键、执行 SHA、`planned_trials=4811`、`target_uses_planned=9622`、`planned_holdout_days=38`、`planned_holdout_matches=304`，核模型与留出回调均为零。

   ```bash
   nba_run=( "$project_python" -m polymarket_alpha_lab.papersim.cli optimize run
     --managed --repo-root "$research_root" --family nba-pace-efficiency
     --as-of 2026-11-19T02:00:00Z --input-manifest "$pointer"
     --seed 20261120 --attempt 1 )
   "${nba_run[@]}" --dry-run
   ```

4. **只在已确认 `not_run` 后发一次。** 由唯一执行者保留一次调用的开始回执，执行同一数组 `"${nba_run[@]}"` 一次，记录实际退出码和时钟。受管层锁定同一科学键，首次 `STARTED` 认领后私有计算器只消费一次。原进程内的受管提交确认按同一认领处理。禁止用新 attempt、变更种子/网格/阈值、第二实验、重复发命令或手动伪造终态。若预览或恢复看到 `in_progress`，只读监测原作业。`interrupted` 或发布不明时仅查原根、原 attempt 与持久回执。`sealed_success` / `sealed_failure` 直接取原件。
5. **按持久证据结案。** 独立连接按同一科学键、代码、配置与登记键核唯一 `STARTED`、终态事件、optimization ID、工件字节/SHA、记分卡 SHA 及退出码。合法结果为 `CANDIDATE_REGISTERED`、`NO_IMPROVEMENT`、`INSUFFICIENT_DATA`、`RUN_FAILED`。正式 `RUN_FAILED` 须有已封存的 optimization 行、相连的失败终态、退出码 1、原始错误码/阶段和结果工件。登记/连接/身份/计时预检失败、已认领后无终态、孤立的失败 attempt、CLI 非零或确认不明分别如实登记，留空不存在的运行号或记分卡，交回具体阻塞与原因。封存失败立即报告并保存已完成与未执行数。一个合规终态即满足 OD-V，留出关闭也按原事实归档。
6. **交接按实际使用量填写。** 报物理计划/已启动/完成/空/中止/未执行、按谱系目标使用、搜索胜出规格、锁定及开放谱系、留出回调/完成、双侧可评分及共同区块/场数、指标区间与 Holm 及质量门、候选写入、耗时、原因。无实际测量写“未测”。实际运行路径与仅合成/原生测试覆盖的路径分别标明。把正式运行与读回附到 `DELIVERY_PLAN.md` 和 `docs/handoffs/2026-11-19-optimizer-phase1.md`，附实测运行编号、SHA、三条 CI 和对应原回执的库内位置，登记簿补到交接的 `main`。FX-52 三项登记与其他出口条件另据各自回执核销。

## 并列保留三张原始记分卡

已复核本机旧证据区 `mo6-historical-archive-operator/formal-e234c336/28-verified-formal-archive.json`，88,845 字节，SHA-256 `03c616e1d94efe5e3a7879e3277645b5d60e4e407bdef6e9d31d9f7eb3366ad5`。下表读自各 `experiments[*].receipt.scorecard_sha256` 及相连运行/事件，与 `DELIVERY_PLAN.md` §§84.2.5、85.2 一致，三次实际均未打开留出。指标按折外预测列自然对数损失 / Brier。

| 原运行、开始/终态事件 | 原留出、试验、结论 | 折外评分 | 原记分卡 SHA-256 |
| --- | --- | --- | --- |
| 俱乐部足球 1，1/2 | 1 周、57 场。65/65，1 规格。`INSUFFICIENT_DATA` | 胜平负 639 场 1.026166 / 0.614938。大小球 639 场、1,917 条线 0.596451 / 0.409710 | `b27ca7855ae147d27c76537289fd904c61f1fdb95be28fb5815cbd5f12a34fb9` |
| 国家队 2，3/4 | 227 日、1,803 场。864/864，36 规格。`NO_IMPROVEMENT` | 大小球 2.5 的 191 场 0.687870 / 0.486797 | `e47b518723c52ac369562af6a11b85ae24cb4c62fb6262e6ae298784a91e3d65` |
| NBA 3，5/6 | 8 日、66 场。评估 10，完成 9，空 1，1 规格。`INSUFFICIENT_DATA` | 胜负、总分 225.5 均为 74 场可评 72 场，分别 0.937356 / 0.576808、0.916230 / 0.585333 | `8d7b2a4f498aa99ae20349d6744657c37a785ea30d097e19f44ddc743d57fe10` |

新增 NBA v2 作为独立一行留档。俱乐部固定 W45 依 §87.2 和 OD-V 以预检登记完成。固定末周身份匹配 64/81、八轮中五轮标签结构完整，每目标至多 3 周、229 场，距 20 周且 300 场仍至少差 17 周、71 场，满足时点未知。新启动、试验、预测、留出调用与比较为零，实际配对未测。只有另行登记的合法输入和日程形成足量、完整、时间可得的因果后缀才具备正式重跑条件。保留三张历史卡、W45 预检行和随后唯一的 NBA v2 结果。
