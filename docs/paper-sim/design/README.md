# 代码引用的设计与计划文档索引（FX-28，2026-11-20）

本目录是 FX-28（对应 2026-11-19 审核 N-3）的登记处：代码（源码、测试、
工具、迁移注释）引用过的设计与计划文档，原件已不在任何可达位置；本文件
逐条登记"原件缺失"，并给每一处代码引用指出**仓库内实际存在的依据**。
此后新节点的规矩：代码引用某份设计或计划，这份文档必须在同一个节点里
入库（03-fix-list FX-28 第四条，待 FX-25 整理稿收录）。

- 清点基线：origin/main `81eb20e3`（实现当天 fetch 的远端 main）。基线
  之上仅本节点的注释改写；本表行号是**本提交之后**的行号。改写只增加
  注释行，冻结面（迁移、常量、断言）一字未动。
- 迁移正文里的引用**不改**（03-fix-list 第 0 节第 3 条"已有的迁移文件
  不改、不删、不重放"；`database/papersim/migrations.lock.json` 逐文件
  固定 SHA-256），全部在本文件第 3 节登记。

## 1. 原件缺失登记

2026-11-20 只读复核结论（登记于 `DELIVERY_PLAN.md:4188`）：四类原件在
服务器与本机均未找到，登记的库外目录 PAL-R（`D:\Projects\
pal-remediation-plans-20261113\`，服务器同名目录）不存在。逐条登记：

| 文档 | 原登记名与位置 | 字节数 | SHA-256（登记值，照录不改） | 状态 |
| --- | --- | ---: | --- | --- |
| 09D 设计 v2 | `m1-09d-design-20261114.md`（PAL-R；版本链登记见 `DELIVERY_PLAN.md` §78.1，3928-3930 行） | 81,005 | `b88161a12925dce18b91157d7f3734c54276c2b3638efbd0ff5f2f1ee4e310c8` | **原件缺失** |
| M1 节点计划 | `M1-next-nodes-plan.md`（PAL-R；登记见 `docs/remediation-evidence/2026-11-14/q-basis/q-facts-c3-20261114.md` F6.3，133 行；校验值同文件 196 行） | 28,092 | `0a48b7bc6e000f3df693431815a842291079bc7498f57248c97727031e55e47f` | **原件缺失** |
| M2 节点计划 | `m2-plan-formal.md`（PAL-R；文件名登记见 `docs/handoffs/m3-cleanup/codex-b0-pause-decisions-20261116.log`:103） | 未登记 | 未登记 | **原件缺失** |
| M3 计划 | `m3-plan-formal.md`（PAL-R；§3 摘录见同 log:20） | 未登记 | 未登记 | **原件缺失** |
| 报告视图设计 | `m3-v1-views-design.md`（PAL-R；文件名登记见同 log:98） | 未登记 | 未登记 | **原件缺失** |
| M1 各节点复审回执 | 各节点 fresh 复审回执为协调者会话记录，未存档（`DELIVERY_PLAN.md` §78.3 第 7 项自登记此缺口） | — | — | **原件缺失**（存活例外见第 4 节） |

两份有登记校验值的原件（09D v2、M1 计划）无法重新计算比对——原件不在，
登记值照录、不改写、不冒充复验。09D v2 的版本链（v1 fresh 复审
FAIL＝P1-1 阻断＋P3-2..P3-10 → v2 修订 → v2 重审 PASS）登记于
`DELIVERY_PLAN.md` §78.1（3930 行）；v2 的 §0R 修订记录与 v1 复审条目
处置表有**摘录**保存在仓库内回执里（见第 2 节 F/G 类依据），摘录不是
原件。

## 2. 代码引用映射表

### 2.1 计数口径（复审 P2/P3 落实）

清点用 `git grep -n -i -E`，模式清单（宽松口径，本表全集）：

1. `design ?2026-11-15`（含 `design 2026-11-15 v2`）与中文 `M[123] ?节点?计划`、`外部文档|库外`（中文模式 0 命中，代码内全部为英文写法）；
2. `M[123] plan`、`m2-plan(\.md)?`、`m3-v1-views-design`（显式文档名/节点计划名）；
3. 隐含式（同文件上下文已限定所指文档）：`design section`、`design [0-9]`、`(section [0-9]`、`per section [0-9]`、`plan section`、`the plan forbids`；
4. `final review P[23]`（M1 终审回执条目）；
5. 条目缩写（09D 设计/其 v1 复审的条目号）：`P[123]-[0-9]`、`B7-B20`、`D-[45]`、`D-CONN`、`vector A-`。

严格口径（审计 N-3 的"29 处"）只取模式 1、2 中带显式文档名/日期前缀的
写法加迁移内引用。**审计 29 处中 store.py 8 处与 m2 迁移 6 处可按严格
口径精确重建，总数无唯一解**（隐含式写法与同一注释块内多重引用的切分
因人而异），故本表取全集超集：迁移 14 行（第 3 节）＋ src/tests/tools
主引用 112 行＋ M1 终审回执行 13 行＋条目缩写 16 行＋ 扫描复查补登记
2 行（`test_init_lazy_imports.py` 原在清点文件范围之外，见下）＝
**登记 157 行**（`snapshots.py:4` 一行同时引用 09D v2 与 M1 计划，按
"处"计为 158 处）。
表中 ● 标记严格口径命中的行（审计口径的可重建子集）。

### 2.2 库内依据总表（按被引文档）

| 类 | 被引文档 | 仓库内实际存在的依据 |
| --- | --- | --- |
| A | 09D 设计 v2（§2.4/2.5/2.6/3/3.3/3.4/4.x/5.2/5.8/5.9/6.2 L3/7.x/8.2/8.3/9.1） | `DELIVERY_PLAN.md` §78.1（版本链与校验值）、§78.5（终审核验）；`docs/remediation-evidence/2026-11-14/decision/m1-final-review-rerun-20261116.log` 内嵌 09D v2 §0R 修订记录摘录（约 8644 行起）与 v1 复审条目处置摘录（约 2881-2948 行）；各引用处所在模块的 CHECK 契约与对应测试即语义自证（迁移/模块 docstring 已描述契约） |
| B | M1 节点计划（§3 各节点、§5 B-condition、FX-08/FX-10/FX-11/FX-12、lane A） | `DELIVERY_PLAN.md` §78.1 九节点版本链逐节点行、§78.2（M1 计划 §6 合成出口逐项核对表）、§77.3.2（OD-G 数字）、§77.7（FX-08 验收承接）、§77.9/3912 行（启动门）；`docs/handoffs/2026-11-13-m0-remediation.md` §9；`q-facts-c3-20261114.md` F6.3；计划复审回执 `decision/m1-rereview-output-20261114.log` |
| C | M2 节点计划（§2.1-2.5 适配器、§3.1-3.8 操作、§5.3 恒等式、§6.2/6.3 测试） | `docs/handoffs/2026-11-19-audit-optimizer/05-development-plan.md`:27（B1-B4/D1/D2 节点划分沿用 M2 计划）；`DELIVERY_PLAN.md` §79.1（FX-15/queue_policy/原生测试范围）、§77.3.2（OD-G）；`database/papersim/migrations/20261116000000_papersim_m2_operations.sql`（表定义即 §3.1 契约的实现）；`papersim/store.py` M2 段与 `tests/test_papersim_m2_*`；`tests/papersim_m1_catalog.py` 与 `test_native_postgres_workflow.py` 的 `PAPERSIM_FILES`（§6.3）；`m3-cleanup/codex-b0-pause-decisions-20261116.log`:20,103 |
| D | M3 计划 §7.1/7.2 与报告视图设计 | `m3-cleanup/codex-b0-pause-decisions-20261116.log`:20,98；`DELIVERY_PLAN.md` §78.3 第 1 项与 §78.5 开放项 3（§7.5 视图延后条件接受）、§79（M3 视图已合入正本）；`20261116000010_papersim_reporting_views.sql` 与 `tests/test_papersim_store_native.py:919`（§7.2 验证矩阵的实现） |
| E | M1 终审回执 P2-01/P2-02/P2-03 | **回执在仓库内**：`docs/remediation-evidence/2026-11-14/decision/m1-final-review-rerun-20261116.log`（P2 关闭裁定表约 11670-11672 行、摘要 55-57 行）；`decision/codex-m1-final-brief-20261115.md`:42-44；`DELIVERY_PLAN.md` §78.5（4013-4015 行） |
| F/G | 09D v1 复审条目（P1-1(b)、P3-2/7/8/9/10、P2-1）与设计条目（D-4/D-5/D-CONN、B7-B20、vector A-3、N3） | `m1-final-review-rerun-20261116.log` 内嵌条目处置摘录（约 2881-2948、8644 行起）；`DELIVERY_PLAN.md` §78.1 3930 行（v1 FAIL=P1-1+P3-2..P3-10→v2 PASS）；条目所在行注释即契约自证 |

### 2.3 逐文件行号（本提交后行号；● = 严格口径命中）

**09D 设计 v2（A/F 类）**

| 文件 | 行号 |
| --- | --- |
| `src/polymarket_alpha_lab/papersim/store.py` | ●5, ●11, 180, 242, 324 |
| `src/.../papersim/store_psycopg.py` | ●8, 166, 203, 208, 272, 307 |
| `src/.../papersim/ledger.py` | ●164, 203 |
| `src/.../papersim/migrations.py` | ●3, 170 |
| `src/.../papersim/snapshots.py` | 4（同行亦引 M1 计划）, 102, 197 |
| `src/.../papersim/cli.py` | 549（F，已加回执指向） |
| `tools/papersim_import_legacy.py` | ●3, 13, 109, 600, 713, 873 |
| `tools/papersim_ledger_report.py` | ●212 |
| `tests/test_papersim_store.py` | ●5, 27, 321 |
| `tests/test_papersim_store_native.py` | 7, 78, 611, 711, 1322, 1516 |
| `tests/test_papersim_ledger.py` | 418, 497, 1072 |
| `tests/test_papersim_snapshots.py` | 184 |
| `tests/test_papersim_import_legacy.py` | 5, 153, 406 |

**M1 节点计划（B 类，逐条 T1/T2 判定——全部 T1：每一处都能在
`DELIVERY_PLAN.md` §78.1 的对应节点行或 §77.3.2 找到等价登记）**

| 位置 | 条目 | 判定与锚点 |
| --- | --- | --- |
| `papersim/pipeline.py:13` | M1 plan lane A（FX-08） | T1：§78.1 FX-08 行、§77.7；同行已引 2026-11-12 audit 与 DELIVERY_PLAN 77.7 |
| `papersim/snapshots.py:4` | M1 plan §3 FX-12 | T1：§78.1 FX-12 行 |
| `papersim/soccer_model.py:555` | M1 plan §3（FX-10） | T1：§78.1 FX-10 行（注释已加锚点） |
| `papersim/store.py:482` | M1 plan FX-08 | T1：§78.1 FX-08 行、§77.7 |
| `src/.../replay.py:3` | M1 plan §3 FX-12 items 2-3 | T1：§78.1 FX-12 行（注释已加锚点） |
| `papersim/evaluation.py:431` | M1 plan §5 B-condition | T1：§77.3.2（OD-G） |
| `papersim/evaluation.py:645` | M1 plan §5 B-condition | T1：§77.3.2（OD-G） |
| `tests/test_papersim_evaluation.py:4` | M1 计划 FX-11 节名（净指标、全候选评分与已批准规则） | T1+T2：节点链 T1（§78.1 FX-11 行，注释已加锚点）；节名原文 T2（原件缺失） |
| `tests/test_papersim_evaluation.py:631` | M1 plan §5 B-condition semantics | T1：§77.3.2（OD-G） |
| `tests/test_papersim_soccer_model.py:713` | M1 plan §3（FX-10） | T1：§78.1 FX-10 行（注释已加锚点） |
| `tests/test_papersim_replay.py:3` | M1 plan §3 FX-12 item 4 | T1：§78.1 FX-12 行（注释已加锚点） |

**M2 节点计划（C 类）**

| 文件 | 行号 |
| --- | --- |
| `papersim/adapters/__init__.py` | 3 |
| `papersim/adapters/contracts.py` | 3, 64, 69 |
| `papersim/adapters/polymarket.py` | 3, 13, 29, 33, 124, 753 |
| `papersim/adapters/prices.py` | 3 |
| `papersim/adapters/sports.py` | 3 |
| `papersim/adapters/understat.py` | 3, 31, 128 |
| `papersim/espn_nba.py` | 31, 338, 360, 495 |
| `papersim/fotmob.py` | 29, 251, 273 |
| `papersim/queue_policy.py` | 3（同行已引 DELIVERY_PLAN 77.3.2） |
| `papersim/store.py` | 57, 686, 687, 761, 931 |
| `papersim/store_psycopg.py` | 368, 425 |
| `tests/papersim_m1_catalog.py` | 1, 66（改写未触碰其 `M1_MIGRATIONS` pin 常量） |
| `tests/test_papersim_adapter_contracts.py` | 3 |
| `tests/test_papersim_adapter_polymarket.py` | 1, 9 |
| `tests/test_papersim_adapter_prices.py` | 1, 3 |
| `tests/test_papersim_adapter_sports.py` | 3 |
| `tests/test_papersim_adapter_understat.py` | 1 |
| `tests/test_papersim_cli.py` | 922 |
| `tests/test_papersim_m2_migrations.py` | 1 |
| `tests/test_papersim_m2_store.py` | 3, 5, 48, 127, 173 |
| `tests/test_papersim_m2_store_native.py` | 3, 10, 59, 178, 312, 397 |
| `tests/test_papersim_migrations.py` | 14, 61, 238, 280 |
| `tests/test_papersim_queue_policy.py` | 3 |
| `tests/test_papersim_replay.py` | 723 |
| `tests/test_papersim_snapshots.py` | 301 |
| `tests/test_papersim_store_native.py` | 65 |

**M3 计划与报告视图设计（D 类）**：`tests/test_papersim_store_native.py:919`
（M3 plan §7.2 验证矩阵与视图设计）；`tests/test_init_lazy_imports.py:27`
（M3 plan §9.2 启动加载测量纪律；该文件不在首轮清点文件范围内，扫描
复查时补登记并就地规范化为 "M3 plan"）。

**M1 终审回执（E 类，全部有库内回执，T1）**：`papersim/cli.py` 48, 553,
650；`papersim/evaluation.py` 26, 430, 644, 1003, 1245；
`tests/test_native_postgres_workflow.py` 115；`tests/test_papersim_cli.py`
427, 478；`tests/test_papersim_evaluation.py` 442, 630。每文件首现处注释
已直接指向 `decision/m1-final-review-rerun-20261116.log` 与
`DELIVERY_PLAN.md` §78.5。

**条目缩写（G 类，登记不改写——缩写所在注释的契约由代码与测试自证，
条目处置摘录见 F/G 行依据）**：`papersim/store.py` 19, 22, 215, 223,
271, 297, 313；`papersim/store_psycopg.py` 6, 237；
`papersim/pipeline.py` 1171；`tools/papersim_import_legacy.py` 109, 371；
`tests/test_papersim_import_legacy.py` 133；`tests/test_papersim_pipeline.py`
508, 545；`tests/test_papersim_store.py` 224；
`tests/test_papersim_store_native.py` 8, 134, 1323, 1515；
`tests/test_papersim_m2_store_native.py` 9（P3-9）。

## 3. 迁移内引用登记（14 处，冻结不改）

以下引用位于已发布迁移正文内，按 03-fix-list 第 0 节第 3 条与
`database/papersim/migrations.lock.json` 的字节锁定，**保持原样**；其库内
依据同第 2.2 节（A/C/D 类）。行号为迁移文件内行号（未改动，基线即当前）：

| 迁移文件 | 行号 | 引用 | 类 |
| --- | --- | --- | --- |
| `database/papersim/migrations/20261115000000_papersim_bootstrap.sql` | 1, 19 | design 2026-11-15 v2 §2.4；design §2.5 | A |
| `database/papersim/migrations/20261115000100_papersim_v2_core.sql` | 1, 177 | design 2026-11-15 v2 §4/§5；design P3-10 | A/F |
| `database/papersim/migrations/20261116000000_papersim_m2_operations.sql` | 1-2, 12, 39, 76, 100, 135 | M2 plan §3.1/3.2/5.3（文件头一处＋五处 plan section） | C |
| `database/papersim/migrations/20261116000010_papersim_reporting_views.sql` | 1-2, 17, 187, 414 | m3-v1-views-design 与 M3 plan §7.1；design §2.3；design 5.2 B | D |

## 4. 存活证据与超出项

### 4.1 server-evidence/（收入 44 个文件，观测日期 2026-11-20）

来源：服务器 m0 startup 工作区 `~/pal-m0-startup-20261113-yx2fyoje/evidence/`
（主机以别名称呼，地址不进仓库；服务器目录如与观测有出入，以实测为准）。
`cp -a` 原样收入，混合行尾不归一（`.gitattributes` 对本目录设
`-text -whitespace`，字节由 `MANIFEST.sha256` 锁定）：

| 子目录 | 文件数 | 对应登记 |
| --- | ---: | --- |
| `m1-fx08/` | 22 | `DELIVERY_PLAN.md` §78.1 FX-08 行引用的 delta 复审证据（`p2-1-delta.diff`）与红绿输出、secret-scan、selfcheck |
| `m1-fx10/` | 13 | §78.1 FX-10 行的 `selfreview.txt`（自评单，非独立复审）等 |
| `m1-09l/` | 1 | §78.1/78.5 的 09L native 重跑日志 |
| `m1-synthesis/` | 8 | §78 合成验证（`synthesis-evidence.json` 五步全 PASS、`attempts.txt` 首错保留、`cleanup.json`） |

**入库前扫描结论（2026-11-20）**：44 文件，公网 IPv4 命中 0，凭据/密钥
模式命中 0，服务器地址出现 0 次；email 模式 2 处命中经逐条核对均为
pytest 装饰器行 `+@pytest.mark.parametrize("case_id", ...)` 的正则误报
（`m1-fx10/added-lines.txt` 319/370 行）。**无真实命中，无需打码，无替换
表**（`server-mirror/REDACTIONS.md` 的做法本次未触发）。M1 终审回执本体
（`m1-final-review-rerun-20261116.log` 等）已在
`docs/remediation-evidence/2026-11-14/decision/`，不重复收入。

### 4.2 超出 FX-28 收入清单的库外文档（登记，未收入）

代码另引用两份**服务器上仍存活**的研究设计文档，不属于 FX-28 的五类
收入清单（09D/M1/M2/M3 计划、视图设计、M1 复审回执），本次不收入，
仅登记；是否入库由协调者/owner 另行决定：

| 文档 | 服务器路径 | 字节 | SHA-256（2026-11-20 实测） | 代码引用 |
| --- | --- | ---: | --- | --- |
| NBA 可行性研究 | `~/paper-sim/round46/nba_feasibility.md` | 15,875 | `351a269188a6ab37f1ed3f852db1a676e72af5c27aedfbdf0de2d5be16805cec` | `papersim/nba_model.py`:9,28,53,149 |
| 国家队 Elo 可行性研究 | `~/paper-sim/round47/nl_elo_feasibility.md` | 18,530 | `4602d336aafb534792413f3c80c152e3ee73fc9cddbbccb327ef8302eae6064b` | `papersim/intl_model.py`:29、`papersim/intl_data.py`:49,68,218、`tools/wp_intl_validate.py`:109,2848 |

（其部分配套脚本已随 `docs/paper-sim/server-mirror/round46/`、`round47/`
镜像入库，镜像内无这两份文档本体。）

另有一处 **V1 项目线**的库外计划引用超出 FX-28（模拟盘线）范围，未处置
仅登记：`tests/test_project_postgres_version_switch_native.py:2644` 的
"M3 H1 (plan section 7.3)"（OLD CLI bind 的 readiness wait），仓库内无
对应登记可查（`git grep "M3 H1"` 于 docs/ 与 DELIVERY_PLAN.md 均 0 命中），
建议随 V1/归档线（FX-18 或 FX-25）另行清理。

### 4.3 核对为库内、不属本登记的引用

- `papersim/league_strength.py:14` → `docs/paper-sim/review-2026-10-30/FIXES.md`（库内）；
- `papersim/soccer_model.py` 的 §75 → `DELIVERY_PLAN.md` 第 75 节（库内）；
- `papersim/evaluation.py` 的 09D/09S 节点名 → `DELIVERY_PLAN.md` §78.1 版本链（库内登记的节点身份）。
