# 01 对送审材料和三个待审 PR 的复审（2026-11-21）

审核方：云端审计，只读。基线：`main` @ `2bdc4cccacc925ea8e6569c8865b90c0b0c671e6`（核对时 `main` 没有动过）。

请求来源：owner 转发的本地 agent 2026-11-21 20:00 的复审请求。范围是送审材料（交接包分支 `handoff/optimizer-review-2026-11-21` @ `09cf06f8ff57382b121470ad67c18994204db02b`）和三个待审 PR（#114、#116、#118）。2026-11-21 进度审核（`handoff/progress-review-2026-11-21`）的结论沿用。生产环境、凭据和远端写入不在范围内，我也没有碰。

## 0. 结论

送审材料属实。三个 PR 里没有发现已确认的缺陷。#116 通过，可以合入。#114 和 #118 现在补不齐证据，不能合入：

- #114 的三条工作流是绿的，但绿在一个过期的合并结果上；它与现在的 `main` 合并之后的结果没有跑过。它的复审回执不在库内。
- #118 的三条工作流在现在的合并结果上全绿。它和 #114 在 `papersim/store_psycopg.py` 上冲突，后合入的那个必须解冲突并整套重跑；PR 描述里"三条 GitHub 工作流待此 PR 的实际结果"已经过期；复审回执不在库内。
- `main` 上 11-21 的四条 P1（FX-36 至 FX-39）还开着。这三个 PR 都没有改变这一点。

| 对象 | 结论 | 理由 |
| --- | --- | --- |
| 送审材料 `09cf06f8…` | 属实 | 提示词里的固定值全部一致；清单三个文件大小和哈希一致；它指向的都是已有的材料，没有新上传 |
| #116 | 通过 | 只改两个文档文件；四处标记与归档批次状态文件逐条对上；与当前 `main` 合并干净；三条工作流绿 |
| #114 | 补齐后通过 | 合并条件 MC-1 至 MC-3 未满足：当前合并结果没跑过 CI；复审回执不在库内；FX-38、FX-39 第一步要排在它之后 |
| #118 | 补齐后通过 | 合并条件 MC-4、MC-5 未满足：与 #114 冲突，后合入者重跑；PR 描述过期，复审回执不在库内 |
| `main` @ `2bdc4ccc` | 不通过（沿用 11-21） | FX-36 至 FX-39 未改 |

"补齐后通过"的意思：没有发现需要改代码的缺陷，缺的是证据和顺序；条件满足、证据到位后，我的结论就是通过。它不等于现在就可以合入。

总的结论写在文末的 `VERDICT` 一行。

## 1. 做了什么，没做什么

做了：

1. 原样运行本地 agent 请求里的脚本（第 2 节）。
2. 取回三个 PR 的头提交，与固定的 `main` 比较；在临时仓库里造出"PR 并进 `main`"和"两个 PR 互相合并"的结果，报告冲突（`evidence/make_merge_refs.sh`）。
3. 用 GitHub API 取三个头提交上的全部工作流运行，再读十二个作业的日志，核对每个作业检出的是哪个合并提交、汇总行是什么（`evidence/ci_*.tsv`）。
4. 对合并结果跑与前两轮相同的三个扫描：测试削弱、公网地址形态字符串、节点计划引用。
5. 读代码，范围见第 6 节。

没做：

- 没有执行仓库里的任何代码。
- 服务器、数据库和操作员本机上的东西（真实历史运行的归档、本机验证记录、ZCode 的复审记录）接触不到，依赖它们的结论标为"需本地 agent 复核"。
- CI 的原生库产物（含 JUnit）下载不了：存储主机不在网络白名单里。

## 2. 本地 agent 的提示词有没有用

有用，能跑。在空目录里原样运行，退出码 0，固定值逐项一致：`main` 含基线提交；交接包分支含 `09cf06f8…`；11-21 审核分支顶端是 `5e4033c4…`；三个 PR 头是 `26dfa2a0…`、`8c7b5365…`、`f09850ba…`；`MANIFEST.tsv` 252 字节、3 行、SHA-256 `8919f345…60303`，所列三个文件的大小和哈希都对（`evidence/local_agent_prompt_run.txt`）。

它指向的材料不是新的。交接包分支 06:07Z 推送，三个 PR 的头提交在 01:30Z 至 05:03Z 之间推送，`main` 的 `pushed_at` 是 07:10:31Z，此后没有任何推送。本地说的"已经上传了"，就是这批。

它有两处不够：

1. 它固定了 PR 头，没有固定"CI 实际检出的合并提交"。#114 的问题恰恰出在这里：头没变，CI 绿在 `0e9210f0` 上，现在 GitHub 给的合并结果是 `a0086b82`，按它的脚本发现不了。本包的提示词（03 号）加了这一项，脚本是 `evidence/check_ci_merge.sh`，输出见 `evidence/check_ci_merge_output.txt`：#114、#116 报 STALE，#118 报 CURRENT。
2. 交接包里的 `evidence.md` 是本地 ZCode 写的说明，属自述。核对它的办法是回到 GitHub 的运行记录和日志，不是再读一遍说明。

## 3. 开头先查的三件事

| 项目 | 现状 |
| --- | --- |
| 仓库可见性 | 公开（`private=false`）。11-21 的结论是这件事没有决定记录（FX-36、OD-O），今天没有新的记录 |
| `main` 的分支保护 | 没有（`protected=false`），OD-M 仍待 owner |
| 分叉 | 1 个 |
| 推送情况 | `main` 仍是 `2bdc4ccc`；远端 221 个分支、3 个标签；打开的 PR：#34、#35、#41（九月的旧 PR）、#114、#116、#118 |

## 4. 逐 PR 的发现

### 4.1 #116 标记 FX-18 B1 归档批次并登记仍在使用的测试

事实：2 个文件，+287/−4。新增 `docs/handoffs/m3-cleanup/pr-b-20261120/B1-status.json`（`executed=false`、`deleted_path_count=0`、`deletion_gate=PAUSE`，只是标记，没有删除任何东西）；`docs/index/phase1-module-index.md` 改 4 行。

核对：4 行改动与状态文件里的标记按路径逐条对上，4/4，没有错标和漏标。全文读过。

CI：头 `8c7b5365` 上 Offline #333、Research paper bridge #117 是 pull_request 触发、成功；原生库工作流是手动触发的（#202，成功）。检出的合并提交是 `fb69b63d`（`8c7b5365` 并进 `11fb457c`），那时 `main` 还没有 #115 和 #117。`main` 此后的 5 个提交没有碰 #116 改的文件，与现在的 `main` 合并干净（模拟）。读这个索引的三个测试（`test_phase1_docs_module_index_matches_files.py` 等）在 CI 里通过。`check_ci_merge.sh` 对 #116 也报 STALE（合并进 `11fb457c`）；因为它只改文档、`main` 之后的提交没碰它改的文件，我不要求重跑。

结论：通过。没有合并条件，按 `AGENTS.md` 的推送规则合入。

### 4.2 #114 按受管快照完成模拟优化并验证跨进程运行回执

事实：23 个文件，+17,102/−925；GitHub 现在给的合并结果是 `a0086b82`（`26dfa2a0` 并进 `2bdc4ccc`），可合并、无冲突。

**R5-1 CI 绿在过期的合并结果上（合并条件 MC-1）**

- 头 `26dfa2a0` 上有两组三条工作流：02:01Z 一组，03:06Z 一组（Offline #338 = 运行 44859358224，Native #206 = 44859358299，Research paper bridge #120 = 44859358236），全部成功。
- 03:06Z 那组检出的是 `0e9210f0`，也就是 `26dfa2a0` 并进 `8cba9d37`（#115 之后、#117 之前的 `main`）。#117 在这之后合入，现在的合并结果 `a0086b82` 没有跑过。
- 差别是 #117 的 16 个文件，其中 `DELIVERY_PLAN.md` 也被 #114 改过（文本上合并干净）。读 `AGENTS.md`、`DELIVERY_PLAN.md` 和这些文档的测试有好几个，#117 自己还改了其中两个（`tests/test_codex_node_push_policy_rules.py`、`tests/test_phase1_docs_required_terms.py`），见 `evidence/merge_delta_117.txt`。风险不大，但"绿在旧基线、红在新基线"正是这类测试的常见来源，不能凭推断放过。
- PR 描述里的本地全量 `43771 passed, 410 skipped` 是在 PR 头上跑的，CI 的 `43914 passed, 415 skipped` 是在旧合并结果上跑的，两个数字不同，说明跑的不是同一份代码，也都不是现在的合并结果。

**R5-2 复审回执不在库内（P2，FX-42 的延续，合并条件 MC-2）**

- 描述里写"原头 `17dced47…` 的软件复审通过，追加的登记规则单一来源验收失败"，随后修订、"准确受审提交为 `26dfa2a0…`"。当前头的复审结论、复审者、日期和回执路径没有写，也不在库内。计划部分在库内（`DELIVERY_PLAN.md` 新增第 85 节）。
- 三张真实历史记分卡仍归原提交 `e234c336…`，三次均未登记候选。它们不是这个头上的结果，属于 FX-41 要重做的部分。

**R5-3 与 #118 冲突（合并条件 MC-5，见 4.4）**

**R5-4 它没有改 P1-3 和 P1-4 第一处的代码（合并条件 MC-3）**

#114 重写了优化器的 `_cmd_optimize_score`（`cli.py:1672`）、`_cmd_optimize_decide`（1694）、`_decision_roles`（1734）、`_cmd_optimize_apply`（1805）、`_cmd_optimize_rollback`（1875）和 `_optimize_forward_stats`（1366）。这些正是 FX-38 和 FX-39 第一步要改的地方，而它们的行为没变：

- 第 1434 行仍是 `close_judged.append(abs(close - float(row["shadow_p"])))`，"收盘价偏离"仍按收盘价与模型概率的差计算（P1-3，在 `main` 上是第 1531 行）。
- 第 1763 行仍是 `events = [(active, "GUARD"), (candidate, "ACTIVE")]`，晋级判定仍直接换届。
- 第 1518、1552、1595、1608 行仍是 `priced_model_version=state["active_model_version"]`，前向统计按现役版本取行（P1-4 第一处的取数方式）。

所以 #114 没有让这几条 P1 更糟，也没有修它们。11-21 提示词的步骤 3 把 FX-38、FX-39 第一步和 #114 并行放着，会再产生一个冲突。改成：这两项做在 #114 合入之后的 `main` 上。在它们合入之前，11-21 提示词第 4 节的禁令继续有效：不写任何前向判定，不应用任何换届。发布路径本身只写候选角色（`store_psycopg.py:2996`，`role='CANDIDATE'`），不会在发布时换届。

**读过且没有问题的地方**

- 规则数字：`registry.py` 的 `_STAGE1_HOLDOUT_FLOORS`（81 行）、`_FORWARD_ROWS`（107 行）、`_SHADOW_WINDOWS`（143 行）与 `DELIVERY_PLAN.md` 第 80.7 节登记的数字逐项相同；新增的 `tests/test_papersim_optimize_rule_sources.py` 检查它们只有一处定义。
- 第一阶段登记规则：`runner.py` 的 `envelope_status`（2462 行）、`evaluate_stage1_rule`（2484 行）、`_holdout_comparison`（5543 行）按登记的留出期配对差上界、Holm、校准和验证闸门不退步、包络不低于 0.8 倍实现；符号翻转检验在 `metrics.py`（两侧、`(极端数+1)/(次数+1)`）。
- 假阳性：新增的 `tests/test_papersim_optimize_false_positive_200.py` 在 200 个种子上跑，没有跳过标记。
- 校准数值：`metrics.py` 把原来的浮点 IRLS 换成了十进制区间界证明的牛顿法（`calibration_slope_intercept`，719 行起）。我读了驱动、分离检测、步长证明和回溯下降判据；区间算术的底层（`_CalibrationBounds`）没有逐行读，由 CI 里的四组回归锚点覆盖（见 O-5）。
- 无削弱：被修改的测试文件里测试函数 134 → 293；被移除的测试 0，断言变少的测试 0，新增跳过标记 0，新增预期失败标记 0；新增 3 个测试文件、48 个测试函数（`evidence/test_weakening_scan_output.txt`）。
- 冻结内容没动：`tests/fixtures/papersim/` 和迁移文件不在改动清单里；`cli.py` 里自检的六个函数（`_check_*` 和 `_cmd_selfcheck`）没有被任何改动块碰到。
- 公网地址形态的字符串没有新增（`evidence/address_occurrence_output.txt`）；新增的存储代码里没有 DDL、网络和下单类结构。

### 4.3 #118 M2 D1：恢复取价、终局取证和逐笔模拟结算

事实：14 个文件，+5,145/−54（含新增的设计文档 `docs/paper-sim/design/d1-operation-recovery-plan.md` 和 `d1compare/operations.py`）；GitHub 给的合并结果是 `eea87f79`（`f09850ba` 并进 `2bdc4ccc`）。

**CI 在现在的合并结果上全绿。** Offline #339（运行 44869068160）、Native project PostgreSQL #207（44869067934）、Research paper bridge #121（44869068049）都检出 `eea87f79`，成功。原生库的 papersim 分区 760 个测试通过、0 个跳过，三个新增的 D1 原生测试（`test_resolution_capture_cli_seals_atomically_and_recovers_across_processes` 等）出现在耗时榜里，说明确实跑了（`evidence/native_partition_proof_118.txt`）。

**读过且没有问题的地方**

- `capture.py` 的旧路径完好：`run_capture`（890 行）只在 `operations.is_d1_operation_key(operation_key)`（913 行）时进入新的 D1 路径，否则走原来的回执路径；该文件被删掉的行只有一行 `from dataclasses import dataclass`，换成了带 `asdict, replace` 的同一条导入。
- `settle.py`：`run_settle` 对 D1 操作键在没有受管标记时拒绝（`SettleError("d1_operation_managed_required")`，976 行），`_run_d1_settle`（862 行）承担新逻辑；篮球终局、走盘退款、首写绑定的几个分支读过，没有发现问题。
- 无削弱：测试函数 80 → 97，被移除 0，断言变少 0，新增预期失败 0。新增跳过标记 4 个，都是与同文件既有测试相同的条件跳过（Linux 且设置 `POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES=1` 才运行），原生库工作流里已设置，上面的 760/0 说明它们跑了。
- 冻结内容没动；公网地址形态的字符串没有新增。

**R5-2（同 4.2）复审回执不在库内（合并条件 MC-4）。** 描述写"实际 ZCode 独立只读代码、设计及精确头推送门复核已通过"，没有复审者、日期和回执路径。设计文档放进了库（好的一步），但回执仍在库外。

**R5-5 PR 描述过期（P3，合并条件 MC-4）。** 最后一行写"三条 GitHub 工作流待此 PR 的实际结果"；结果已经有了，全部成功。

**R5-3 与 #114 冲突（合并条件 MC-5，见 4.4）。**

### 4.4 两个 PR 的关系

`evidence/merge_simulation_output.txt`：`main + #114`、`main + #116`、`main + #118`、`#114 + #116`、`#116 + #118` 五个合并干净；`#114 + #118` 在 `src/polymarket_alpha_lab/papersim/store_psycopg.py` 上冲突，只有这一个文件。

- 两个 PR 都改了 `.github/workflows/native-postgres.yml` 和 `tests/test_native_postgres_workflow.py`，文本上合并得干净，但这个测试钉住的是工作流里的分区文件清单。两个并在一起之后必须整套重跑，不能只看各自的绿。
- 顺序建议：#114 先，#118 后。理由：FX-38、FX-39 第一步改的函数被 #114 重写，排在 #114 之后做才不会再产生第三个冲突；#118 属于第一阶段出口之后的 M2 线（11-21 提示词步骤 5）。后合入者：把 `main` 合进 PR 分支（不变基、不强推），手工解 `store_psycopg.py` 的冲突（两边的改动都保留），三条工作流整套重跑，PR 描述里的运行编号更新，冲突解决本身是新代码，要做独立只读复审。
- 反过来 #118 先合也行：它现在的绿就是在当前合并结果上，而 #114 反正要重跑，总的重跑次数少一次。代价是 FX-38、FX-39 要等 #114，而且冲突落在一万七千行的 PR 上。我推荐 #114 先。

### 4.5 其他观察（不是合并条件）

- O-1：`optimize/runner.py` 在 #114 头上有 6,461 行（`main` 上 2,420 行），搜索驱动、阶段一规则、留出期比较、登记输入都在一个模块里，读和审的成本高。建议后续拆分。
- O-2：`optimize/policy.py` 在 #114 里开始导入评估模块，层次变了。
- O-3：#114 的 Offline 工作流 23 分 18 秒（#116、#118 的合并结果上都是约 12 分钟），原生库 papersim 分区 18 分 20 秒（#118 上 10 分 32 秒）。CI 成本翻倍，留意工作流的超时余量。
- O-4：`tools/d1_compare.py:110` 调用了 `operations._validate_attempt`（带下划线的私有函数）。建议提成正式接口。
- O-5：`metrics.calibration_slope_intercept` 的行为变了。以前总是返回一对数，极端输入会溢出（`DELIVERY_PLAN.md` 第 85 节说的 NBA 溢出）；现在单一结局类别、logit 全相同或完全分离时返回 `(None, None)`，在预算内找不到经证明的拟合时抛 `ValueError("calibration fit did not converge")`。唯一的调用者 `scorecard.py:273` 没改，也没有 `except`。我没有追到运行器会不会把这个异常变成一条干净的失败记录。建议补一个测试：构造不收敛的输入，确认 `optimize run` 记一条失败而不是崩溃。
- O-6：同一个 PR 在本机和 GitHub 的 Linux 运行器上差一个测试：#116 本机 43483 通过、337 跳过，CI 43482、338；#118 本机 43517、358，CI 43516、359；总数相同。有一个测试在本机通过、在 GitHub 上跳过，没有查是哪个。建议用 `-rs` 查清，确认它不是在 CI 里静默放过的检查。
- O-7：#114 把命令行 `optimize run`、`init`、`status` 的旧入口整段删掉并重写（`cli.py` 差异 946 行）。引用旧用法的脚本和文档会断；新入口有测试，说明文档是否同步没有查，随 FX-46 一起看。

## 5. 仍缺的证据

| 编号 | 缺什么 | 谁来补 |
| --- | --- | --- |
| E-1 | #114 在当前合并结果上的三条工作流运行编号（MC-1） | 本地 agent |
| E-2 | 后合入者解冲突之后整套三条工作流的运行编号（MC-5） | 本地 agent |
| E-3 | #114、#118 当前头的独立复审结论和回执（复审者、日期、被审提交号、结论），放进库内（MC-2、MC-4） | 本地 agent |
| E-4 | #118 描述里的运行结果（MC-4） | 本地 agent |
| E-5 | 真实历史运行的本机归档（88,845 字节，SHA-256 `03c616e1…`）和本机验证记录接触不到：要么把归档的哈希和关键输出入库，要么写明这些只留在本机、本轮没有核（FX-41、FX-42、OD-S） | 本地 agent |
| E-6 | 五个已合入节点（#79、#80、#81、#83、#84）的事后复审回执（FX-37，沿用 11-21） | 本地 agent |
| E-7 | owner 的答复：OD-O（仓库公开）、OD-T（#117 的根规则正文）、OD-M（`main` 分支保护）、OD-S（"复审回执入库"那一句根规则） | owner |

关于 E-3：11-21 提示词第 7 节写的是"每个合入 `main` 的 PR……复审回执随节点入库"。这里澄清范围：改源码的节点要入库；纯文档节点（#116）不要求，PR 描述里写明复审者代号和日期即可。

## 6. 阅读覆盖和局限

| 对象 | 差异 | 逐行读过 | 只做了机械扫描 | 没读 |
| --- | --- | --- | --- | --- |
| #116 | 2 个文件，+287/−4 | 全部 | 无 | 无 |
| #114 | 23 个文件，+17,102/−925 | 约 11%：`cli.py` 优化器命令的差异（946 行）、`metrics.py` 的差异（402 行）、`registry.py` 的规则常量、`runner.py` 的 `envelope_status`、`evaluate_stage1_rule`、`_holdout_comparison`、`policy.py` 与 `scorecard.py` 的差异、`store_psycopg.py` 的发布路径 | 13 个测试文件（只扫了削弱、跳过、预期失败、地址、引用）；`store_psycopg.py` 新增的 1,547 行（只扫了 DDL、网络、下单类结构） | `search.py`（1,224 行）；`runner.py` 的其余部分（适配器、加密货币时钟、历史数据等）；`store_psycopg.py` 其余部分 |
| #118 | 14 个文件，+5,145/−54 | 约 12%：`settle.py` 的差异（449 行）、`capture.py` 的差异、设计文档、工作流和测试的改动、`cli.py` 与 `tools/d1_compare.py` 的差异 | 测试；`store_psycopg.py` 新增的 398 行 | `operations.py`、`acquire.py`、`store_psycopg.py` 其余部分、大部分测试 |

所以我对 #114、#118 的"补齐后通过"只覆盖上面读过的部分。两个 PR 的主体还要等合入之后的增量复审补读（11-21 的 `claude/owner-standing-instructions.md` 第 7 节第 3 条，尤其是 #114 的搜索和运行器）。

需本地 agent 复核的：本机归档和验证记录（E-5）；复审回执（E-3、E-6）；CI 原生库产物里的 JUnit（我下载不了）；O-6 里那个本机通过、CI 跳过的测试。

## 7. 11-21 的结论怎么沿用

| 11-21 发现 | 现状 |
| --- | --- |
| P1-1 仓库公开且没有决定记录（FX-36、OD-O） | 没变：仍公开，没有决定记录，owner 未答复 |
| P1-2 五个已合入节点没有复审回执（FX-37） | 没变；三个待审 PR 也没有库内回执 |
| P1-3 "收盘价偏离"含义实现错了（FX-38） | 没变：#114 头上仍是 `abs(close - shadow_p)`（`cli.py:1434`） |
| P1-4 第二阶段在俱乐部足球上走不通（FX-39） | 没变：#114 头上仍按现役版本取数、晋级仍直接换届（`cli.py:1518`、`1763`） |

`main` 在 11-21 之后没有变化，所以 11-21 的 `VERDICT: FAIL` 原样有效。它在 FX-36 至 FX-39 完成、并经增量复审之前不会变。

VERDICT: FAIL
