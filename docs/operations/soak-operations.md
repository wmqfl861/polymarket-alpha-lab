# RV05 soak 操作边界与生命周期文档（soak-operations）

任务: PAL_RV05_CLOSURE_20260922 / 节点 N5（操作交付与终审准备）。
编写基线: 本文档写在 `work/rv05c-n5-e911360c` 分支，基点为终冻提交
`15f24d246493cdc114a127c366da537659d8c5d8`（tree `d388d113c0a75bb297bf1d60b13a9237fb31a07a`）。

标注约定: 【实测】= 本节点 2026-09-22 亲自运行 `--help` / 读源码 / 只读查询得到；
【引用】= 引用 SETUP / 协调者 / 前一任务(PAL_RV05_CAPACITY_20260921)既有记录，本节点未重算。

本文档只整理**已存在**工具的真实接口与操作边界，不新增报告框架，不改变任何验收门槛。

---

## 1. 固定身份与路径（本节全部为操作前置事实）

| 项 | 值 | 来源 |
| --- | --- | --- |
| 冻结候选 | `15f24d246493cdc114a127c366da537659d8c5d8` / tree `d388d113...` | 【引用】CLOSURE_STATE §0；【实测】`git -C n0\src rev-parse` 一致 |
| 原 soak 候选（不动） | `d929cc35214996a44008d4bbdd643bc8cc3550ea`，campaign 位于 `C:\Users\Joyce Gu\pal-soak-formal-01\campaign` | 【引用】launch-spec `paths.original_campaign` |
| 原 soak 进程 | PID 62420（driver）/ 20372 / 98500（closeout） | 【实测】2026-09-22 `Get-Process -Id 62420,20372,98500` 三者全部存活，StartTime 与 CLOSURE_STATE §5 逐秒一致（02:14:47Z/02:19:15Z/05:23:36Z） |
| 运行时 Python | `C:\Users\Joyce Gu\pal-fix67-fc555171\runtime\python.exe`（sha256 `bd99dd53...`，只读使用） | 【引用】launch-spec `frozen.python_sha256` |
| 发射包 | `C:\Users\Joyce Gu\pal-rv05-caaa8b5b\launch-final\`（preflight/launcher 与 `n5\launch` 副本关系见 §3.6） | 【实测】diff |
| 新 campaign 计划根 | `C:\Users\Joyce Gu\pal-soak-rv05-candidate`（**当前不存在**，preflight `target_campaign_absent` 检查项要求其不存在才可发射） | 【引用】launch-spec `paths.planned_campaign_root` |
| 发射窗口 | 最早 `2026-09-24T02:14:50Z`（原 soak 目标结束时刻，**不是实际退出证明**）.. 最晚 `2026-09-24T12:36:41Z` | 【引用】launch-spec `launch_window_utc` |
| 总截止 | `2026-09-28T00:36:41Z` | 【引用】launch-spec `gates.closeout_deadline_utc` |
| 正式门槛 | 259200s / 864 有效 passed 轮 / 100000 实际不同合格子输入，三者**分别判定** | 【引用】launch-spec `gates` |

净化启动约定（所有命令通用，【实测】各脚本真实 argv）:

- Python 工具一律 `<python.exe> -I -S -B <script> ...`（隔离模式、无 site、不写字节码）。
- 不向任何命令传递 PG/DSN/API 凭据类环境变量；子进程环境由驱动/启动器自建
  （subinput 子进程 = `SystemRoot` + `PYTHONPATH=<仓库根>+<pinned purelib>` +
  `PYTHONUTF8=1` + `PYTHONDONTWRITEBYTECODE=1`；pytest 子进程 = `PYTHONPATH=<pinned purelib>` 等，
  见 §3.1）【实测】`soak_driver.py:905-958`。
- 启动器把子进程 `TEMP/TMP` 指到 `launch-final\work-temp`【实测】`launch-rv05.ps1:263-265`。
- 资源约定: 一次一个子进程；单命令预计超过 7 分钟时后台运行并以 ≤60 秒间隔轮询。

---

## 2. 生命周期状态与操作映射

闭包计划(plan.md §6)定义的状态机:
`UNARMED → ARMED_WAITING → PRECHECK → CLAIMED → STARTED → FINAL_REVIEW_READY`，
并列 `CANCELLED / EXPIRED / NO_GO / UNKNOWN`。下表把每个状态映射到**当前真实存在**的
机制与命令；不存在的机制明确标注（尤其 ARMED_WAITING，见 §2.2）。

| 状态 | 当前真实机制 | 操作/命令 | 备注 |
| --- | --- | --- | --- |
| UNARMED（当前实态） | `launch-final\` 无 `launch-claim-candidate.json`、无 `launch-receipt-candidate.json`【实测】目录清单 | 无（等待窗口与 N3 衔接件） | 计划到点 ≠ 原 soak 退出；发射前置见 §2.3 |
| ARMED_WAITING | **不存在**。无持久等待进程、无已部署等待模式（闭包 N3 待办；CLOSURE_STATE §2.3 = NOT_STARTED） | 无命令可进入该状态 | 见 §2.2 边界声明 |
| PRECHECK | `preflight-rv05.py`（只读，26 项检查，`overall: GO` = 全 PASS） | §3.6 命令 | 最近一次报告 NO_GO，红项全部为 5 个时间门类【实测】`launch-final\preflight-last.json` |
| CLAIMED | 启动器在 GO + campaign-absent 之后、任何目录创建/进程启动之前，原子写入唯一 claim（tmp+move） | §3.7 真实发射（非 -DryRun）自动完成 | claim schema `pal-rv05-launch-claim-v1`，含 claim_id/frozen 身份/窗口 |
| STARTED | `Start-Process` 启动 driver（隐藏窗口，cwd=冻结仓库根），8 秒存活探测后写回执 v2（PID/命令/STOP 路径/全套哈希） | 同上，退出码 0 | 回执 `pal-rv05-launch-receipt-v2` |
| FINAL_REVIEW_READY | closeout `wait` 自然结束 → 独立 N2 审计 → 长测后故障合同 → 严格终审写 `FINAL_REVIEW_STRICT` | §3.4 / §3.2 / §3.8 | 注意: 原 closeout(98500) 的旧宽松门会写 `FINAL_REVIEW_READY` 标记；在修正口径下那只是"待审材料"，严格裁决以 `run_final_review.py` 为准【引用】run_final_review.py 文档串 |
| CANCELLED | 发射前操作者放弃: 不写 claim、不建目录（-DryRun 不留任何这些痕迹）；已有 claim 未启动则按 lost-ack 处理，不删不改 | 无（保留现场） | 撤销已部署等待模式的记录要求见 plan.md §6 |
| EXPIRED | 窗口 `2026-09-24T12:36:41Z` 已过仍未发射 | 无 | RV05 记 NOT_STARTED/PARTIAL，不缩短 72h、不延长截止【引用】plan.md §6 |
| NO_GO | preflight 存在非 PASS 项（启动器退出码 2） | 见 §2.4 分类 | 非每个 NO_GO 都终结 |
| UNKNOWN | lost-ack: 有 claim 无完整回执 / 回执半写不可读（退出码 6）；终审 UNKNOWN=快照不完整或 schema 混用 | 保持 UNKNOWN，人工裁决 | 禁止二次发射、禁止改写 claim/回执 |

### 2.1 状态记录义务

真实启动（非 DryRun）后必须记录: PID+创建标识、完整命令、配置摘要、首轮回执、
STOP 路径【引用】plan.md §6 / prompt.md §8。回执文件本身即承载这些字段
（`launch-rv05.ps1:277-315`【实测】）；操作者职责是把回执内容登记进 RV05_STATE
（README-LAUNCH 步骤 1【引用】）。

### 2.2 ARMED_WAITING 不存在——边界声明

本包**没有**任何持久等待/自动唤醒能力: 没有服务、没有计划任务、没有 watcher 进程，
`launch-rv05.ps1` 只在被显式运行时做事。因此"到窗口自动重验旧退出/清理并单次启动"
（闭包计划对 N3 的要求）当前**不可用**；在 N3 交付并通过其 13 项有限合成测试之前，
到点操作是**人工**执行 §2.3。若到窗口时 N3 等待模式仍未部署，发射需要操作者在窗口内
亲自运行唯一发射命令；这**不是**缺陷免责，而是当前实态。只有文件（无进程）= NOT_STARTED，
不得声称已 arm【引用】plan.md §6。

### 2.3 发射前置（全部成立才可执行唯一发射命令）

【引用】README-LAUNCH.md / plan.md §6 / prompt.md §8:

1. 原 soak **实际退出**（`original_driver_exited` PASS），其自有孩子清理确认
   （`original_children_cleaned` PASS）、关段回执与证据稳定 ≥900s
   （`original_segment_close_present` / `original_evidence_stable` PASS）。
2. 时间窗口内（`launch_window` PASS）。
3. 冻结身份未漂移: `frozen_src_head` / `frozen_src_clean` / 各绑定摘要检查 PASS。
4. 卷空闲 ≥10GiB（`volume_free`）、计划 campaign 根不存在（`target_campaign_absent`）、
   无本包遗留进程（`no_leftover_own_processes`）、`windows_kit_adjudicated` 读取
   RV05_STATE §16 显式标记。
5. 预检本身即最终门: 启动器只在 `overall: GO`（26 项全 PASS）时继续。

### 2.4 NO_GO 分类与处理边界

【实测】preflight 检查项构造（`preflight-rv05.py:122-620`）+【引用】README-LAUNCH:

- **可等待类**（NO_GO 但不终结，到点/原退出后自然转绿）:
  `original_driver_exited`、`original_segment_close_present`、`original_evidence_stable`、
  `original_children_cleaned`、`launch_window`。当前最近报告的 5 个红项全属此类【实测】。
- **明确阻断类**（STOP 并人工裁决，不得循环刷新成 GO）:
  `frozen_src_head`/`frozen_src_clean`/`frozen_*_sha`（身份漂移——包已漂移）、
  `binding_complete`/`binding_config_digest`/`binding_manifest_digest`/
  `binding_receipt_contract`/`binding_generator_sha`（绑定不符）、
  `spec_load`/`driver_module_import`/`config_parses`/`manifest_parses`（材料损坏）、
  `volume_free`（空间不足）、`no_leftover_own_processes`（遗留进程）、
  `target_campaign_absent`（根已存在→启动器退出码 4）、
  `windows_kit_adjudicated`（裁决标记缺失）。
- `capacity_inputs_reachable` 为**排程算术**检查（74×2048=151552 ≥100000），
  PASS ≠ 执行证明；真实不同子输入由长测后独立 N2 审计判定（§3.2）。

---

## 3. 工具真实接口一览（逐项 --help 实跑或源码确认）

除注明外，全部命令的 Python 形态为 `<python.exe> -I -S -B <script> <子命令/参数>`；
`--help` 输出原件存档于 WorkRoot `evidence\closure-n5\`。

### 3.1 soak_driver.py — 正式驱动（run / inspect）

【实测】`--help` / `run --help` / `inspect --help`（存档 help-soak-driver*.txt）:

```
soak_driver run    --campaign <dir> --config <config.json>
soak_driver inspect --campaign <dir>
```

- 正式发射形态由启动器组装【实测】`launch-rv05.ps1:175`:
  `<python.exe> -I -S -B soak_driver.py run --campaign <计划campaign目录> --config <绑定config>`，
  cwd = 冻结仓库根（`WorkingDirectory $SrcRoot`）。
- 三类场景的子进程规范【实测】`soak_driver.py:905-958`:
  - `process`: `python -I -S -c <code>`，cwd=轮 tmp；
  - `subinput`（ok-compute）: `python -S -m tests.support.soak_okcompute --candidate <canonical JSON>
    --manifest-sha256 <hex> --contract-sha256 <hex> --rows <planned_rows> --family <f1> --family <f2> --family <f3>`，
    cwd=轮 tmp，stdin=五字段轮 payload（round/segment/scenario/sub_seed/tmp_dir）；
  - `pytest`: `python -S -m pytest -q -p no:cacheprovider --basetemp <tmp> --junitxml <tmp>\pytest.xml <files>`，
    cwd=仓库根。
- `inspect` 为只读查询（campaign 快照），不写任何文件。
- STOP 语义见 §5。

### 3.2 soak_audit.py — 独立审计（audit）

【实测】`--help` / `audit --help`（存档 help-soak-audit*.txt）:

```
soak_audit audit --campaign <dir> [--config <cfg>] [--manifest <m>] [--master-seed <s>]
                    [--baseline <b>] [--expect <e>] [--json-out <path>]
                    [--clock-tolerance <s>] [--min-distinct-inputs <n>]
                    [--min-distinct-qualified-subinputs <n>]
```

- 100000 子输入门的**唯一**正式判定入口（冻结 closeout 的内嵌审计与终审工具不判
  `min_distinct_qualified_subinputs`【引用】README-LAUNCH 步骤 4）。
  长测后按 README-LAUNCH 步骤 4 的完整 argv 执行（`--min-distinct-qualified-subinputs 100000`）。
- 审计白名单核验由 `soak_audit_subinputs.py` 提供（§3.3）；W4a 有限容量试验即以本工具
  判定 PASS: distinct_qualified=6144（三族 2052/2046/2046，9 份回执，0 去重折叠）
  【引用】`evidence\w4a\capacity-trial\audit-report.json`。

### 3.3 soak_audit_subinputs.py — 审计侧白名单库（**无 CLI**）

【实测】`--help` 运行输出为空、退出码 0——本文件没有 argparse、没有 `main`/`__main__`
入口，是供 `soak_audit.py` 导入的纯库（`verify_row` / 三族白名单重建）。
**操作上不存在可直接执行的命令**；不得虚构其 CLI。

### 3.4 soak_closeout.py — 收尾监督（wait / settle / status）

【实测】`--help` + 三个子命令 `--help`（存档 help-soak-closeout*.txt）:

```
soak_closeout wait   --campaign <dir> --out <jsonl> --stop-file <stop> [--driver-pid <n>]
                     --deadline <utc> [--target-end <utc>] [--audit-config <c>] [--audit-manifest <m>]
                     [--audit-baseline <b>] [--label <s>] [--checkpoint-dir <d>]
                     [--interval <s>] [--stability <s>]     # interval 有最小值强制(高频轮询拒绝)
soak_closeout settle --campaign <dir> --out <path> --stop-file <stop> [--driver-pid <n>]
                     --deadline <utc> [--target-end <utc>] ... (同上公共参数)
soak_closeout status --campaign <dir>                      # 只读快照
```

- 启动后第一动作（README-LAUNCH 步骤 2）: 用回执字段 `closeout_next_command` 记录的
  **原样命令**启动 closeout watcher（不自动启动）。该命令由启动器在发射时生成并写入回执
  【实测】`launch-rv05.ps1:181-186`（`--interval 300 --stability 900`，
  deadline=2026-09-28T00:36:41Z，label=rv05-candidate-closeout）。
- `wait` 等待 campaign 结束后运行一次只读内嵌审计并写收尾门；`settle` 立即结算一次。

### 3.5 soak_okcompute.py — 子输入生成器（stdin 合同，**无 --help**）

【实测】`--help` 运行退出码 2、`config_invalid: unknown argument --help`（存档
help-soak-okcompute.txt）——本脚本使用手写参数解析（`_parse_args`, :957），**没有帮助面**。
真实接口【实测】`soak_okcompute.py:957-1051`:

- stdin: 五字段 JSON payload（`round/segment/scenario/sub_seed/tmp_dir`，字段集必须精确
  匹配，上限 1 MiB）；
- argv: `--family <名>`（可重复；不给默认 capture-codec）、`--rows <1..4096>`（默认 2048）、
  `--candidate <label>`（必填）、`--manifest-sha256 <hex>`（必填）、
  `--contract-sha256 <hex>` 或 `--contract-path <p>`（二选一，后者哈希后等价）；
- 退出码: 0 正常 / 1 汇总 ok=false / 2 配置错（含未知参数）。
- **操作者不直接运行本脚本**——正式链路由 driver 按 manifest（`kind=subinput`,
  `module=tests.support.soak_okcompute`, `planned_rows=2048`, 三族）自动调用
  【实测】`launch/manifest-rv05-final.json` + `soak_driver.py:912-934`。

### 3.6 preflight-rv05.py — 只读预检

【实测】`--help`（存档 help-preflight-rv05.txt）:

```
preflight-rv05 [--spec <spec.json>] [--json-out <report.json>]
```

- 默认 spec 为脚本旁 `launch-spec-rv05.json`；launch-final 副本与 `n5\launch` 副本
  **字节一致**（sha256 `93542abb...`【实测】）。
- 运行形态（启动器内【实测】`launch-rv05.ps1:162-163`）:
  `<python.exe> -I -S -B preflight-rv05.py --spec <spec路径> --json-out <报告路径>`。
- 检查项与 NO_GO 分类见 §2.4；最近报告 `launch-final\preflight-last.json` = NO_GO，
  5 红项全为时间门类【实测】。

### 3.7 launch-rv05.ps1 — 唯一发射入口（一次性，claim 优先）

【实测】源码全文（n5 副本 sha `0fc919a1...`；launch-final 副本 sha `f4b63a52...`，
**唯一差异 = 默认 `-SpecPath`** 指向 rebound spec，其余逐行一致）:

```
powershell -NoProfile -ExecutionPolicy Bypass -File <launch-rv05.ps1> [-DryRun] [-SpecPath <spec>]
```

流程: 一次性守卫（读回执/claim 证据）→ 只读 preflight → GO 门 → 计划根不存在门 →
**原子写唯一 claim** → 建目录/work-temp（TEMP/TMP 净化）→ `Start-Process` driver →
8s 存活探测 → 原子写回执 v2。

退出码【实测】脚本头注释 + 控制流:

| 码 | 含义 | 后续操作 |
| --- | --- | --- |
| 0 | 已发射 / DryRun OK | 登记 §2.1，执行 §3.4 watcher |
| 2 | preflight 非 GO 或无报告 | 按 §2.4 分类处置 |
| 3 | 重复发射拒绝（已有 dry_run=false 回执） | 终态，无第二次 |
| 4 | 计划 campaign 根已存在 | 人工裁决 |
| 5 | 发射进程 8s 内退出（回执已写） | 保留现场裁决 |
| 6 | lost-ack（有 claim 无完整回执 / 回执不可读） | **保持 UNKNOWN**，人工裁决，禁止二次发射 |

`-DryRun`: preflight 照跑（只读）、打印精确发射命令/STOP/回执路径，不建目录、不写
claim、不启进程；preflight 非 GO 时 DryRun 也以退出码 2 结束。

### 3.8 run_final_review.py — 严格终审（review 子命令）

【实测】`--help` / `review --help`（存档 help-run-final-review*.txt）:

```
run_final_review review --campaign <dir> --config <cfg> [--manifest <m>] [--baseline <b>]
                    [--driver-file <f>] [--python-file <f>] [--driver-pid <n>] [--target-end <utc>]
                    [--min-rounds <n>] [--max-wall-seconds <s>] [--min-distinct-inputs <n>]
                    [--allow-loose] [--frozen-tree <dir>] [--identity-json <binding.json>]
                    [--expected-commit <hex>] [--expected-tree <hex>]
                    [--expected-module-sha256 NAME=HEX] [--expected-python-sha256 <hex>]
                    [--expected-config-sha256 <hex>] [--expected-manifest-sha256 <hex>]
                    [--expect-campaign-schema <s>] [--expect-receipt-schema <s>]
                    --out <dir> [--checkpoint-dir <d>] [--label <s>]
```

- 退出码: 0 PASS / 3 FAIL / 4 UNKNOWN / 2 NOT_RUN（拒绝/缺件）/ 1 internal / 5 配置无效
  （如 identity-json 核心身份不完整）【实测】源码头 + 常量区。
- 裁决语义【实测】文档串: 活锁/PID 存活 → NOT_RUN 拒审（绝不把活 campaign 当终态审）；
  半写终态 → UNKNOWN（SNAPSHOT_INCOMPLETE，不判损坏也不判通过）；schema 混用 →
  UNKNOWN（绑定外 receipt schema 永不 PASS）；绑定哈希漂移 → FAIL。
  输出 `final-review.json` + `FINAL_REVIEW_STRICT` 标记（仅真实执行裁决时写），
  绝不写进被审 campaign。
- **`--frozen-tree` 必须显式传**（README-LAUNCH 步骤 6【引用】）: 工具默认解析到自身副本
  位置，该默认-vs-分支漂移正是历史 2 个拒绝测试不绿的已知原因（RV05_STATE §16）。
- 完整正式 argv 见 README-LAUNCH 步骤 6（绑 15f24d24 identity-rv05-candidate.json）。

### 3.9 make_synthetic_terminal.py — 合成终态 fixture 生成器（**固定路径，无参数**）

【实测】`--help` 运行即失败: `FileNotFoundError: ...\n5\rehearsal-v3\config-rv04.json`，
退出码 1（存档 help-make-synthetic-terminal.txt，首败保留）。真实接口【实测】源码:
**无任何 CLI 参数**，全部路径硬编码为脚本相对布局（`parents[1]\rehearsal-v3\...`），
其历史源布局已不存在；等价合成 fixture 现位于 WorkRoot `n5\fixtures\`
（campaign-rv04a / config-rv04.json / manifest-rv04.json），供 N5 拒绝测试使用。
操作边界: 该脚本**不再是可分发包的入口**；如需重建合成终态，使用 N2 已发布的
pytest 内 fixture 路径（tmp_path 生成），不要在新布局下运行本历史脚本。

### 3.10 N5 拒绝测试入口（39 项本地证据）

【实测】`tests/test_rv05_n5_launch_review_rejection.py:33-100`: 模块级 skip 契约需要
5 个环境变量（缺失即整模块 skip，CI 中即因此 1 skipped）:

```
PAL_RV05_N5_LAUNCH_DIR      # 含 preflight-rv05.py + launch-rv05.ps1 的目录
PAL_RV05_N5_REVIEW_DIR      # 含 run_final_review.py 的目录
PAL_RV05_N5_FROZEN_TREE     # 只读冻结仓库根（含 tests/support）
PAL_RV05_N5_PYTHON          # 净化 python.exe
PAL_RV05_N5_REHEARSAL_DIR   # 含 campaign-rv04a/ + config-rv04.json + manifest-rv04.json
```

本地 39 项通过 = 本地证据（依赖本地目录）；**不是**"干净 Windows 环境已运行 39 项"。
可分发包（新 checkout + 合成 fixture 即可跑、CI 零 N5 跳过）是闭包 N2 待办，
未完成【引用】plan.md §1/§5。

---

## 4. 失败 / 未知分支的处理边界（汇总）

1. **发射层**: 见 §3.7 退出码表。核心不变量: 一次性（有效回执或裸 claim 永久封锁
   第二次发射）；lost-ack = UNKNOWN，禁止自动补发/改写证据，人工裁决。
2. **长测层**【引用】prompt.md §8: 非预期 failed/unknown/interrupted、身份或证据错误、
   清理不确定 → 停止新长测、保留首败、不自动第二次长测；睡眠/重启/gap 不拼接；
   受测代码改变不借用此前时长。
3. **审计层**: 审计 FAIL/UNKNOWN 按报告逐项处置；`min_distinct_inputs` 与
   `min_distinct_qualified_subinputs` 是两个不同门（legacy 前者在新配置仅作记录）。
4. **终审层**: NOT_RUN（仍在跑）→ 等待，不改判；UNKNOWN（半写/schema）→ 保持未知；
   FAIL → 保留，不重跑旧 campaign 求绿。
5. **每一步首败保留**，重跑结果单列，不以重试覆盖首败。

## 5. STOP 路径语义（严格区分两个 STOP）

| STOP | 路径 | 语义 | 操作边界 |
| --- | --- | --- | --- |
| **新 campaign STOP** | `C:\Users\Joyce Gu\pal-soak-rv05-candidate\campaign\stop`（发射后由回执 `stop_path` 精确给出） | 协作停止**新** driver: 写入一条短注即触发 | STOP 关闭的 campaign **不满足**审计 R4 合格段规则（distinct_qualified 只计最近 `reason='complete'` 关段）——100000 门只能靠自然 complete 关闭达成【引用】README-LAUNCH 步骤 3 |
| **原 campaign STOP** | `C:\Users\Joyce Gu\pal-soak-formal-01\campaign\stop` | 原 soak（d929cc35）自有停止通道 | **永不写入**。原 campaign/PID 只读；本包所有工具"never touches the original campaign…does not stop, kill or scan processes"【实测】launch-rv05.ps1:35-36 |

停止发射控制 ≠ 停止旧 soak；两者物理隔离。

## 6. 不能声称的事项（边界声明，逐条）

以下各项**未验收/未执行**，任何报告不得声称相反:

1. **真实模型**: 未运行任何真实模型调用; `official_cases_run=0`。
2. **业务数据库**: 未连接/未写入用户业务库; 未运行本机原生 DB 验收。
3. **人工结算**: WP-04/G4 的人工核验结算未发生; 合成结算不冒充真实结算。
4. **完整容量**: 计划 151552 槽位仅有算术证明与 W4a 有限试验 6144 行实测
   （N2 审计 PASS）; **完整 74 轮执行未做**（闭包 N1 待办，以 CLOSURE_STATE 为准）。
5. **G2—G6**: 全部未关闭; V1 仍 1/6（仅 WP-01/G1）。本包的任何测试/文档不改变六包状态。
6. **ARMED_WAITING / 单次自动衔接**: 未实现未部署（闭包 N3 待办）; 没有"到点自动发射"能力。
7. **可分发包**: preflight-rv05.py / launch-rv05.ps1 / run_final_review.py /
   README-LAUNCH.md 不在 PR 文件清单中; CI N5 模块整模块 skip（闭包 N2 待办）。
8. **长测后故障合同**: 预演前(预验证期)执行已记录（4 passed + 44 聚焦回归），
   **长测结束后**的第二次执行仍欠（README-LAUNCH 步骤 5）。
9. **发射本身**: 截至本文档时刻 corrected-72h = NOT_STARTED（无 claim/回执文件）。
10. 原 soak 时长/绿标不可转移到新候选; 旧候选 CI 绿不代替新冻结头适用门禁。

## 7. 证据指针

- `--help`/首败存档: WorkRoot `evidence\closure-n5\`（本文档 §3 各条实测的原始输出）。
- 发射包与绑定: `launch-final\`（spec/config/identity/preflight-last）。
- 容量: `evidence\w4a\capacity-trial\audit-report.json`（6144 判定）。
- 11+2 映射与故障合同: 仓库内 `docs/scenario-map-13-11-2.md`;
  WorkRoot `n3\SCENARIO_MAP_13_11_2.md` + `evidence\n3\`（含首败台账）。
- 子输入合同: 仓库内 `docs/contracts/soak-subinput-receipt-v1.md`（sha `19416d3a...`）
  与 `docs/contracts/ERRATA-001.md`。
- 终态判定历史: RV05_STATE §16（windows_kit_adjudicated 标记）。
