# 欧洲五个一级联赛的球队与新闻研究交接 · 2026-11-28

葡超、苏超、比甲、挪超和土超已进入持续的赛前研究。初始提交为 `557ff3f857c26eb0f0a43a00b4f6025454f5eeb0`。运行代码为 `93448e267e83cd771f8f4b511e86a8f51f59063a`，起点为 `a56b7d03f73c405685446c475919f63879ed7aaf`。研究版本为 `euro-soccer-venue-lineups-20261128-v1`。观察单位是同一赛程、同一盘口、同一版本和同一采集窗口。每条记录金额为 0 元。准入仍需按项目登记取得前向样本与费后表现证据。[研究登记](../paper-sim/research/euro-soccer-20261128/registration.md) · [球队模型](../paper-sim/research/euro-soccer-20261128/model.md)

## 从一场比赛复核到一条记录

1. FotMob 提供联赛、赛季和阶段、主客球队、开球时间、既往赛果和可用的 xG。主队按过去主场、客队按过去客场估计攻防，近期每方最多补五场的 xG 和首发。苏超分组与比甲旧赛制的附加阶段单列，目前前向模型只对常规赛出数。[赛程和历史](../../src/polymarket_alpha_lab/papersim/euro_soccer_sources.py)
2. 同场赛前详情里的预测首发、确认首发及有明确原因的缺阵，可以改变对历史比赛的权重。上一场的首发仅作历史，球员出战存疑不按确定缺阵处理。球队历史不能支持阵容差异时，该队保留基础估计。输出分别给出基础概率和有依据的新闻情景，覆盖正常比赛时间的主胜、平局、客胜以及大小 2.5。当前新闻源是 FotMob 的结构化球队消息，模型尚未验证球员的个体因果影响。[新闻来源校验](../../src/polymarket_alpha_lab/papersim/euro_soccer_team_news.py) · [概率模型](../../src/polymarket_alpha_lab/papersim/euro_soccer_model.py)
3. Gamma 合约须匹配同一联赛、主客队、开球时间和结算条款。每场一次 CLOB 公共盘口响应分别验证胜平负及大小 2.5。两侧都有效时记录买一、卖一、费用和盘口时间。对五个方向分别计算每份的费后差值，即模型概率减卖一价、再减每份费用。盘口比较基准另取同一批中点归一化后的概率。行情缺失只在相应盘口留下缺口。深度作为研究字段保存，卖一比较以小额单份计。[盘口与费用](../../src/polymarket_alpha_lab/papersim/euro_soccer_markets.py)
4. 赛程、详情、Gamma 和 CLOB 的原始字节先进入原生 PostgreSQL，再做解析。重复的原始字节按摘要去重，每次实际抓取另有当次接收回执。缓存保留原接收时间。观察按赛季、阶段、双方、开球时间、窗口和模型版本封存，多次调用只保留首次记录。到赛后，只用同一身份、正常 90 分钟完赛的比分。市场、基础模型、新闻情景分别算 Brier 分数与对数损失。汇总按市场和时窗配对，基础与市场、情景与市场分别使用同场分母。[证据存储](../../src/polymarket_alpha_lab/papersim/euro_soccer_store.py) · [完赛评分](../../src/polymarket_alpha_lab/papersim/euro_soccer_outcomes.py) · [汇总](../../src/polymarket_alpha_lab/papersim/euro_soccer_report.py)

## 调度和复查

Linux 用户定时器 `pal-euro-soccer-study.timer` 每分钟检查一次未来 48 小时。五个联赛可同时采集。常规预览每个联赛每次最多新收一场，开赛前 30 至 20 分钟以及最后 3 分钟的指定窗口优先。超过时窗的记录不会以较早时间入库。当前赛历每十分钟刷新，完赛后自动对账。遇到数据缺口时可从观察的 `source_gaps`、`forecast.reasons` 和各盘口的 `reasons` 追查。[采集命令](../../src/polymarket_alpha_lab/papersim/euro_soccer_study.py) · [定时器](../../deploy/systemd/user/pal-euro-soccer-study.timer)

下列命令在已部署的独立研究检出中运行，状态命令可用于审核时读回最新数字。主动运行 `scan` 会采集当时实际到达的窗口。

```bash
.venv/bin/python -m polymarket_alpha_lab.papersim.cli euro-soccer status --repo-root .
.venv/bin/python -m polymarket_alpha_lab.papersim.cli euro-soccer scan --repo-root .
systemctl --user is-enabled pal-euro-soccer-study.timer
systemctl --user is-active pal-euro-soccer-study.timer
```

复装用的三个用户单元在 [deploy/systemd/user](../../deploy/systemd/user)。其工作目录为 `%h/.local/share/polymarket-alpha-lab/euro-soccer`，Python 环境需带项目的 `postgres` 依赖。新研究检出使用项目原生 PostgreSQL 18 和现有 papersim 迁移目录。可用 `python -m polymarket_alpha_lab.papersim.migrations status --managed --repo-root .` 查验，现场读回六条迁移已登记、待处理为零。监督服务按程序退出状态报告失败，两个扫描进程重叠时由数据库会话锁拒绝重复工作。[数据库说明](../../database/quickstart.md) · [研究库服务](../../deploy/systemd/user/pal-euro-soccer-db.service)

## 现场与测试

北京时间 11 月 28 日 14:45:50，受管库中已经登记未来 48 小时全部 32 场常规赛的赛前预览。32 场都有球队基础概率，28 场有可计算的新闻情景。每个联赛各抽最早写入的一场，复查其球队、新闻和盘口原件的摘要。现场回执保存读取时间、模型版本、代码版本、逐联赛覆盖数、逐类缺口及抽查记录的内容摘要。[现场回执](../paper-sim/research/euro-soccer-20261128/field-check.json)

| 联赛 | 已存预览 / 常规赛程 | 有新闻情景 | 胜平负报价完整 | 大小 2.5 报价完整 | 两种盘口同批完整 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 葡超 | 6/6 | 5 | 6 | 3 | 3 |
| 苏超 | 6/6 | 6 | 4 | 6 | 4 |
| 比甲 | 8/8 | 5 | 8 | 6 | 6 |
| 挪超 | 7/7 | 7 | 4 | 6 | 4 |
| 土超 | 5/5 | 5 | 3 | 2 | 0 |
| 合计 | 32/32 | 28 | 25 | 23 | 17 |

现场 4 场仅有基础概率。比甲 3 场和葡超 1 场的来源给出上一场名单，两队都缺乏可据以调整概率的本场信息或历史阵容差异。各盘口有独立的完整性判定，原始盘口缺项、陈旧或双边无效归为 `batch_book_missing_stale_or_invalid`。例如土超 5 场都有球队情景，3 场有完整胜平负、2 场有完整大小球，在该次预览里两组没有重合。

回执截止时，32 场均未进入开球前 30 分钟，实际赛前指定窗口、收盘和正常完赛评分均为 0。首场按当时赛程为 11 月 28 日北京时间 21:00 的萨姆松体育对根奇勒比利吉。持续采集将随赛程进入后续窗口。计分采用首次核实的正常完赛比分，后续官方改判需另行复核。前一轮的历史筛选使用冻结比分基线，对五个候选联赛有 1,391 场胜平负、1,151 场同线大小球的博彩公司收盘对照。历史汇总及 13 个公开输入档案的校验值随本次交付入库。[历史筛选回执](../paper-sim/research/euro-soccer-20261128/historical-screen.json)

新增模块共 167 项测试全部通过，含 3 个隔离原生 PostgreSQL 用例。端到端用例固定了公开 HTTP 输入和时钟，同场走过两次自然时窗及 2–1 正常完赛。两时窗、两盘口、三概率来源的 12 个 Brier 值均按独立公式核对，再次扫描只保留一次结算。它验证了赛后接线，现场的模型效果随后续比赛另计。[原生端到端用例](../../tests/test_papersim_euro_soccer_end_to_end_native.py) · [存储并发与时间回执](../../tests/test_papersim_euro_soccer_store.py)

新增专项及其本机复跑命令使用 Python 3.12.3、pytest 9.1.1。原生用例需要设置 `POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES=1` 以及指向隔离测试用 PostgreSQL 18 发行目录的 `POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX`。以下两条命令可复跑新增专项和边界检查。

```bash
.venv/bin/python -m pytest -q tests/test_papersim_euro_soccer_*.py
.venv/bin/python -m pytest -q tests/test_database_persistence_iron_rule.py tests/test_phase1_readonly_boundary_scan.py
```

数据库及只读边界的 20 项检查全部通过。与起点相比，既有 10 项 papersim 自检输出逐字节相同，SHA-256 为 `a4aa971fed26553474e0b69a285bc2937c44789bbeb1ac6c70bb0d827610fcb9`。代码交付新增 8 个测试文件，已有测试、冻结夹具、迁移和模型规则均无改动。

相关回归按测试用例去重，共 607 项通过，失败为 0。既有 CLI、球队模型、费用、闸门、FotMob 和快照模块的普通运行通过 394 项；规则单一来源及迁移普通运行通过 16 项。两组普通运行里依赖专用数据库入口的 10 项，随后在独立临时 PostgreSQL 18 全部通过，含 CLI 1 项、快照 2 项、迁移 7 项。另有本次新增 167 项以及跨项目边界 20 项。这个数字只覆盖上述专项和回归范围。
