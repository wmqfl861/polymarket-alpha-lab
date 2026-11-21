# PR #114 预推送候选独立静态复审回执

复审对象：本地合并候选 `c4791fb8448da999ae6a23dae9efef2c9c002406`（#114 普通并入当前 main）。方法：仅读取四份附件（`identity.txt`、`overlap.txt`、`relative-to-main.txt`、`frozen-and-scope.txt`），未执行任何命令，未写入任何文件。以下为实际静态发现。

## 1. 合并身份与两父序（identity.txt）

- 候选 `c4791fb8` 为双父合并提交：**父 1 = `241893d7`（当前 main 尖），父 2 = `26dfa2a0`（#114 分支头）**。父 1 指向 main，与推送到 main 的普通合并提交形态一致，main 为候选祖先，可直接快进推送，无需改写。父序判定：符合预期。
- 父 2 `26dfa2a0` 本身是 `3f334c2c` 与 `17dced47` 的合并；`17dced47` 与 §85.3 所载“PR #114 首个组合头”完全一致，两份附件互相印证。首个头按交接第 7 节复审判 FAIL 后停止合并，本候选为专项修订后的再合并，谱系清楚。
- 附件另载候选树 `cd53ce25` 及两父树、基线树（`20a13487`、`a3492ea6`、`8c7b5365`），供树级核对；我无法在只读条件下重算树哈希，见 §8 限制。

## 2. 树三方合并一致性（overlap.txt 与 relative-to-main.txt 交叉核对）

三个双侧同改文件（`native-postgres.yml`、`DELIVERY_PLAN.md`、`tests/test_native_postgres_workflow.py`）以两个方向的差异均收敛到**同一终态 blob**：

| 文件 | main→候选 | PR 头→候选 | 终态 blob |
| --- | --- | --- | --- |
| native-postgres.yml | `c82da1b4` | `2c22d0c3` | `479e0a19` ✓ 一致 |
| DELIVERY_PLAN.md | `51f1627a` | `4f3dd2b5` | `c7c3414c` ✓ 一致 |
| test_native_postgres_workflow.py | `4f0645f9` | `8c07e83e` | `ae52ee08` ✓ 一致 |

三个文件均为**纯并集解**，无删行：main 侧的 D1（journal/frozenrun 及 D1/FX-35 章节、根规则链接改写）全部带入；PR 侧的 `registered_runner_native`、§85 全部保留。相对 main 的 swap（`settle_store_native` 移至 `optimize_runner_native` 之后）与 §85.1“按优化器分支既有次序合并”记录吻合。

## 3. 与当前 main 的相对范围（identity.txt + relative-to-main.txt）

- 候选相对 main 共 23 个文件：8 个 `papersim` 源文件、13 个测试（含 3 个新增 `A`：`false_positive_200`、`registered_runner_native`、`rule_sources`）、工作流 yml、工作流测试、DELIVERY_PLAN.md。**无范围外文件**。
- §85.3 记录与文件清单一一对应：使用既有纯注册表 `papersim/optimize/registry.py`（清单中为 M 而非 A，未新增模块，符合“不扩大运行器首次导入模块集合”的复审要求）；`evaluation.py` 不在改动清单，与其“保留原有百分位元组”一致；`rule_sources` 新测试对应跨入口登记事实的验收。

## 4. §85 与登记簿（relative-to-main.txt）

- main 已有 §84（节点登记簿第二波，补记至 PR #95），候选在其后追加 §85 及 85.1/85.2/85.3，编号连续，无重复编号、无改写 main 既有章节。
- §85 内部自洽：三份登记清单的截点与 SHA-256、运行 ID 1/2/3 与回执 1/2、3/4、5/6、222 份快照与业务表零增量、§85.2 的四项原生首红与测试修正、§85.3 的 FAIL→计划二审 PASS（`9d61651f…`）链条完整，且与候选谱系（`17dced47` 停并、修订再合并）互相印证。

## 5. Native 工作流与测试分区清单

- yml 的 papersim 分区与 `test_native_postgres_workflow.py` 的 `PAPERSIM_FILES` 元组收敛为**同一终序**：…、`optimize_runner_native`、`settle_store_native`、`optimize_registered_runner_native`、`report_store_native`、`d1compare_archive_native`、`d1compare_journal`、`d1compare_frozenrun`。两处清单逐项一致。
- 新增项全部位于 papersim 分区；`WINDOWS_PARTITIONS`/`LINUX_PARTITIONS`（papersim 仅 Linux）为上下文未改动。模拟盘角色边界保持。

## 6. 冻结文件与角色边界（frozen-and-scope.txt）

- 8 个冻结源文件（`cli.py`、`store_psycopg.py`、`optimize/` 六模块）及 `tests/fixtures/papersim` 在候选与 PR 头之间**全部 identical**：合并未向 PR 专有冻结文件引入 main 侧漂移；它们相对 main 的 M 状态完全来自 PR 修订本身。
- main 侧 D1 文件不在 23 文件清单中，即候选与 main 逐位一致，未丢失也未改动。

## 7. 静态限制与待办门禁（如实声明）

- 树哈希四方值与 merge-base 只能引用附件所载，未独立重算；本次树一致性结论建立在双侧差异收敛同一终态 blob、冻结文件 identical、清单并集完整这三项可交叉验证的证据上，未发现矛盾。
- 测试执行不在本回执范围：独立测试会话的 Linux 全量与 CodeGraph 尚未完成；GitHub 当前合并结果的三条 CI（`44836339683`/`44836339616`/`44836339688` 归属首个组合头）及后续记录按约定另走门禁。**推送仍须等待上述门禁全部放行。**

静态层面未发现任何矛盾、越界、丢改或谱系错序。

VERDICT: PASS
