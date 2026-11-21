# PR #114 精确当前头独立只读复审回执（全新 ZCode 会话，2026-11-21）

## 0. 复审约束与诚实声明

- 本复审仅使用工作目录内 12 份附件（清单及 SHA-256 以 `attachments-manifest.json` 为准，12 件全部送达）。未访问仓库、网络、凭据或任何外部数据；未修改任何状态；未运行任何命令。
- 过程披露：曾尝试一次对 `full-diff.txt` 分页读取的工具调用，因参数校验失败未返回任何内容；此后未再调用任何工具。本回执全部结论仅来自已送达上下文的附件内容。
- **关键限制**：`full-diff.txt` 共 18,934 行（975,722 字节），本会话实际仅送达第 1–1,250 行（分页提示存在，但本会话无可用文件读取工具且任务禁止调用工具）。此限制直接决定本回执结论，见 §6。

## 1. 被审对象

| 项 | 值 |
| --- | --- |
| 本地实现头 S | `c4791fb8448da999ae6a23dae9efef2c9c002406` |
| main 基线 B | `cd53ce25f65716e6151238beba253618caca7e98` |
| GitHub merge object M | `620048a7597d4155f1658fe4750f707df3c94158` |
| M 的父序 | 第一父 B、第二父 S（`review-object.json` = `github-ci-checkout-proof.json`，两件互证） |
| 树 | tree(S) = tree(M) = `241893d7f099d144fdc25a9eb15513ecd49e13bd`（M/H 树相同成立） |
| S 自身父序 | 父 1 = `26dfa2a0`（#114 分支头，单父 `17dced47`），父 2 = `cd53ce25`（B）——与 `prepush-correction.txt` 更正版一致 |
| 范围 | 23 个文件，+17,102 / −925 行（`diff-stat.txt`）；差异正本 SHA-256 `1003cc7d…73d23` |
| 复审日期 | 2026-11-21（证据观测 2026-11-21T15:32Z） |

## 2. 对象身份与三条工作流实际检出（责任 2）——通过

| 工作流 | run id / 号 / attempt | 结论 | 实际检出 |
| --- | --- | --- | --- |
| Offline verification | 44918091022 / #343 / 1 | success | `620048a7…`（HEAD is now at + `git log -1` 双行），offline 作业 136887932107，required step 成功，43,914 passed / 415 skipped / 20 warnings |
| Native project PostgreSQL | 44918091078 / #209 / 1 | success | storage 320/2 + 切版 89；research 1227/18；dispatch 1933/15；papersim 792 passed（required steps：分区隔离证明、组装一次性 papersim 集群，均成功）；Windows 作业按配置 skipped；聚合输入四项全 success |
| Research paper bridge | 44918091023 / #123 / 1 | success | `620048a7…`，windows-paper 357 passed |

所有列出作业的 checkout 行均为 M；event 均为 `pull_request`，head_sha 均为 S，head_branch `fix/mo6-native-evidence-fixtures-20261120`。`review-object.json` 的 `ci_proof_sha256` 与清单中该件 SHA-256 一致。**通过**。

## 3. 本地门禁复核（责任 3）——通过，附一项待解释观察

- **全量 Linux（local-linux.json）**：在 S 上，43,915 passed / 414 skipped / 20 warnings / 0 failed，exit 0，耗时 3,722s，head 前后均为 S，`files_match: true`；8 阶段（含 editable 安装、模块/console help 与 report discovery、编译自检）全 PASS。
- **本机 CodeGraph（codegraph.json）**：head S，pending 0/0/0，status complete，sync/status exit 0。通过。
- **秘密扫描（hygiene.json）**：B→S，`diff_check` 通过；service_token / private_key / database_credentials 三类 new_paths、new_lines 均为 0（243 个既有 database_credentials 路径为存量）。通过。
- **冻结与测试削弱（frozen-and-weakening.json）**：removed tests=0、fewer checks=0、新 skip=0、新 xfail=0；新增 3 文件 48 个测试函数；`frozen_paths_pass` 于 S。无削弱。通过。
- **观察 U-5（非阻断）**：同树之下（tree(S)=tree(M)），本地 43,915/414 与 CI offline 43,914/415 相差恰 1 项 passed/skip。两处均 0 failed，差异应为环境条件项；建议按用例名登记备查。

## 4. 历史：第一次预推送回执错误及同会话修正（责任 4）——已保留，未改写

- **首版**（存于 `local-gates.json`，判 PASS）三处实质错误：把树对象 `241893d7` 误读为"父 1 = main 尖"；遗漏真实第二父 `cd53ce25`；由此得出"可直接快进推送 main"的错误推送结论。另把 `26dfa2a0` 误述为"两提交的合并"（实为其树 `3f334c2c`、单父 `17dced47`）。
- **更正版**（`prepush-correction.txt`，同会话、同批只读材料重签 PASS）：按标准对象序（提交/树/父…）逐字段更正；父序更正为父 1=`26dfa2a0`（#114 分支头）、父 2=`cd53ce25`（main）；推送结论收窄为"普通推送 #114 源分支"，main 合并另走 GitHub PR 门禁；对树合并一致、范围、§85、Native 清单、冻结五节逐项复核为不受影响。
- **本复审核对**：更正版的对象字段与 `review-object.json` / CI 证明件完全一致；两份回执并存、首版原样保留，失败/错误历史未被改写。符合责任 4 要求。

## 5. `native-junit-attempt.json` 定性与 MC-1 阻断判定（责任 5、6）

- 事实：systemd 单元退出 1，未产出解析回执。它是**可选 JUnit 工件读取尝试的失败**，不是项目测试失败；不存在任何项目级 JUnit 结论。
- 唯一已证的 Native papersim 结论：run 44918091078（attempt 1）作业成功、所列 required steps 成功、该分区作业日志计 792 passed。此为作业级证据。
- **缺证不阻断 MC-1，理由**：MC-1 的证明要求是"三条工作流在实际检出 M 的提交上成功"，run/attempt/结论/检出/required steps 在 §2 已完整成立；JUnit 仅是可选的粒度增强，其解析失败既不产生也不推翻任何项目测试结论；证据链中没有哪一项结论依赖逐用例 JUnit 数据（`native_test_modules` 所列模块名来自作业定义，非 JUnit 产物）。
- 按责任 6：本复审**不**将"Native JUnit 个别用例已通过"当作日志证据——该证据不存在；也不从这份未成功获取的 JUnit 推断任何 testcase/module。

## 6. 可读差异部分的逐文件复核（责任 1 之已达部分）

**6.1 `.github/workflows/native-postgres.yml`（全文已读）**：papersim 分区净 +1（新增 `test_papersim_optimize_registered_runner_native.py`；`settle_store_native` 移至 `optimize_runner_native` 之后），与 §85.1"两支各 24、合并后 25、无移除"及更正版预推送回执 §5 的收敛序一致；分区与模拟盘角色边界未动。未见缺陷。

**6.2 `DELIVERY_PLAN.md`（+72 行全读）**：§84 未改动，§85/85.1–85.3 编号连续追加。规则数字统一锚定 §80.7；三份清单截点/SHA-256、运行 ID 1/2/3 与回执 1/2、3/4、5/6、222 份快照与业务表零增量、§85.2 首红 4 项失败与启动器退出 2 的**失败历史保留**、§85.3 首头 `17dced47` 判 FAIL→计划二审 PASS（`9d61651f…`）链条自洽；e234c336 时点 43,462/406 与终值 43,915/414 的增长无矛盾。未见缺陷。

**6.3 `src/.../papersim/cli.py`（816 行变更，diff 区段完整送达，已读）**：
- **入口**：旧 `_cmd_optimize_mo6` 删除，新 `_cmd_optimize_experiment`；families 不建连；init/status 的 `--managed`/`--dsn` 互斥且必选；run 的 `--input-manifest`/`--input` 互斥必选，legacy `--input` 恒拒但保留旧错误码次序（`inputs_empty`/`archive_root` 校验在前，终报 `input_manifest_required`）；`main()` 对 run+`--input-manifest` 再校验来源存在。
- **事务**：decide/apply/rollback/control 均包入 `registry_write_transaction`；status 用 REPEATABLE READ READ ONLY 事务；run 的清单读取在 `optimization_read_transaction(stage="manifest_read", closeout=True)` 内。
- **时钟与预算**：`optimization_command_clock(start_ns)` 注入连接与请求；`--budget-seconds`/`--max-trials` 已移除，seed/截点/attempt 以登记 protocol 钉死（不一致即 `papersim_optimize_protocol_mismatch`）；dry-run 语义改为"验请求或读封存结果"。
- **原子发布与重放**：apply 先走 `_replay_decision_roles`（角色/actor/reason/effective_at 全等才放行，否则 `..._replay_conflict`；用 original 的 code_commit），否则冻结/暂停检查后按 `_decision_roles` 配对签发；legacy `{"reason"}` 水印走 legacy_manual 兼容分支；rollback 以显式 `--at`+actor+reason 幂等重放，新写把 spec/comparator/role_event_ids 记入 evidence_watermark 并在同一事务内写决定与角色事件；decision/score 现传 `stats_spec`/`stats_comparator` 并输出 `min_complete_blocks`/`min_span_days`/`calendar_due`——对应 §85.3"唯一数值来源"修复。
- **只读/安全边界**：错误面仅输出 `papersim_*` 码与受控 proven 字段（stage/retryable/indeterminate/事件 id），不泄 DSN/原始输入；`_optimization_document` 拒重复键与 NaN/Infinity；manifest 校验严格（canonical JSON 逐字节往返、posix 相对键、(role,key) 唯一、每 digest 的 (length,kind,schema) 一致、缺源必须 required=false+非空 absent_reason、全缺源清单拒绝）；`run` 的 `RUN_FAILED` 退出码经 `and` 短路正确。
- **在此可读范围内未发现可证缺陷**；但 store 层实现（事务/时钟/发布/重放的实际语义）不在本文件，见 §7。

**6.4 `metrics.py`（部分送达，约前 202/375 变更行）**：Decimal 外舍区间算术框架（精度 34/68/136；`_CalibrationBounds` 的加/减/乘/除/平方/绝对值，exp 经 `next_minus`/`next_plus` 外推，softplus 双端）、`_calibration_newton_step`（按信息量选原点、加权中心化、信息为 0 返回 None）——与 §85 NBA 校准溢出修复方向一致。可读部分未见缺陷；**其余约 54%（锚点复验、精度升级、binary_calibration 重写主体）未送达**。

## 7. 阻断发现与修复验收

**F-1（阻断，属复审验收缺口，非对 S 代码的缺陷指控）**：S 相对 B 的完整差异未送达本只读复审者。18,934 行中仅送达 1–1,250 行；23 个变更文件仅 3 个全文可读（yml、DELIVERY_PLAN、cli.py）、1 个部分（metrics.py），**19 个未读**——按 18,027 变更行计约 94% 未复核，且恰为责任 1 点名的核心：
- `store_psycopg.py`（1,665 行）：`registry_write_transaction`、`optimization_command_clock`、`optimization_read_transaction`、`run_managed_optimization`/`preview_managed_optimization`、`record_role_events` 的幂等性；
- `runner.py`（4,567 行）与 `search.py`（1,227 行）：登记 protocol、`execute_optimization_run` 的预算/截止/原子发布执行层；
- 全部 11 个测试文件（约 8,700 行，含 3 个新文件 48 用例的断言）及 `test_native_postgres_workflow.py` 的 3 行清单改动（后者目前仅有更正版预推送回执 §5 的既有交叉核对为旁证）。

**实际影响**：责任 1 中"存储/优化器事务/时钟与原子发布、重放失败、预算、测试断言"无法由本复审者亲自证明；"不存在应修缺陷"不能在未审约 94% 差异的情况下签认。可重现来源：`attachments-manifest.json`（full-diff.txt 尺寸/哈希）× 本会话实际送达范围（1–1,250 行）。

**修复验收**：以只读可分页方式将剩余第 1,251–18,934 行送达独立复审者（或重新打包为 ≤25,000 token/页的附件），完成完整逐文件复核并另签回执；该复审须至少确认：(a) `record_role_events` 在 `_replay_decision_roles` 二次记录下的幂等/去重；(b) `run_managed_optimization` 的 reconnect/deadline/clock 与中断回执语义（STARTED 保留、已提交工件返回原工件）；(c) `preview_managed_optimization` 零写入；(d) `execute_optimization_run` 内部预算/截止强制（CLI 已无预算参数）；(e) yml 与 `PAPERSIM_FILES` 逐项同序；(f) 48 个新用例对上述语义的断言覆盖；(g) metrics.py 未读部分（四组锚点、精度升级、binary_calibration 主体）。此前各轮结论（含 `17dced47` 的 FAIL、专项修订、更正版预推送 PASS）原样保留，本 FAIL 不替换、不改写任何历史结论。

## 8. 非阻断 / 未验证项清单

- **U-1** apply 的重放路径先于冻结/暂停检查执行：若 `record_role_events` 幂等则属设计（重放不改变状态），待 F-1(a) 确认。
- **U-2** rollback 重放要求 `decided_at` 与 `--at` 微秒级全等，不等即报冲突：fail-closed、无数据丢失风险；时区/精度边界待测试断言核验。
- **U-3** `_optimization_status` 恒置 `"forward_sample_count": None` 并标注 `paired_by_lineage`：公开契约含义待测试断言核验。
- **U-4** `source_refs` 实为 {来源 sha256: byte_length} 映射，命名与内容不完全一致：消费端语义待 runner/store 段核验。
- **U-5** 本地 43,915/414 与 CI 43,914/415 的 1 项差（同树）：非阻断，建议按名登记。
- **U-6** metrics.py 约 54% 变更行未读（见 F-1(g)）。

## 9. 结论

对象身份与三工作流实际检出：通过（§2）。本地全量/CodeGraph/秘密/自检/冻结与削弱扫描：通过，附 U-5（§3）。首版回执错误与同会话修正：已完整保留（§4）。JUnit 缺证：不阻断 MC-1，且不以不存在的逐用例证据立论（§5）。可读差异（yml、DELIVERY_PLAN、cli.py 全部，metrics.py 部分）：未发现可证缺陷（§6）。但**完整逐文件复核因附件仅送达 1,250/18,934 行而未完成（F-1，阻断）**——按签发规则，"不存在应修缺陷"与"所有强制合并条件（含精确当前头的完整复审）均成立"均无法签认，故逐项判 FAIL，修复验收见 §7。本判定不替换任何历史复审结论。

VERDICT: FAIL
