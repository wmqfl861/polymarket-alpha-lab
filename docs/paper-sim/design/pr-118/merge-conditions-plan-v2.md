# PR #114 与 #118 合并计划（第二稿）

依据为交付提交 `2e9bc3dc70a045063c7f4b7d1574f6fe2b6e9c95` 的 `docs/handoffs/2026-11-21-pr-review/02-merge-conditions-and-next-steps.md`、其后提示词提交 `24b5882de41602b6364295b17b7cf1f2085a77dd` 和仓库根 `AGENTS.md`。本计划待独立只读 ZCode 计划审核。以下 SHA 和运行编号如标明“历史”，只用于辨认既有证据。

## 顺序与分工

1. 在远端 `main` 上确认先前进度包、再本轮审核包的交付提交均为祖先，并核对两个清单。两次本地合并已在 `9a38b62827d35d524e05d71f0d2f780c16baa86c` 排好，先查远端状态，避免重复集成。确认 #116 已按文档专属 STALE 例外经普通合并提交落地，记下其头、实际合并提交和复审记录。
2. **MC-3 由 #114 之后的 FX-38 与 FX-39 第一步联合节点承担。** #114 完成 MC-1、MC-2 后普通合并，按下文核验远端实际合并提交及其树。核验通过后，从包含该提交的 `main` 起建一个节点，将 FX-38 与 FX-39 第一步在同一 PR 内实施、验收并合入。自现在起直到该联合节点合入 `main`，禁止写入任何前向判定，禁止应用任何换届。核验联合节点已经落地后，方可用包含它的 `main` 刷新 #118，执行 MC-4、MC-5 并合入。
3. 实质性修订和解冲突由原生 `gpt-6.2-sol`、`max` 实施，计划实质修订由原生 `gpt-6.2-astra`、`max` 承担。关闭快模式。协调者负责 `DELIVERY_PLAN.md` 定稿。实际本机 ZCode 执行测试，各项独立计划、代码、阶段或交接复审使用新开、只读 guarded ZCode，会话内禁用 shell、编辑、写入及其他变更能力。复审者不得参与所审工作的计划、实施或测试执行。记录会话标识、实际披露的后端、日期、对象、范围、发现和明确结论。原签 FAIL 留存，修正后交回原复审者。适用的推送门包括干净的聚焦提交、专项和全套测试、Python 编译、`git diff --check`、工作树洁净、CodeGraph 同步、秘密扫描、独立推送门 PASS。

## 两个 PR 使用同一套 CI 核验

从规范仓库读取当前 `main` 的完整 SHA（下称 B）、PR 实际 head SHA（H）以及最新 `refs/pull/<号>/merge`（M）。在干净工作树中用 `git fetch` 取回上述 ref。读取合并对象，检查第一父为 B、第二父为 H。刷新过的 PR 还应满足 B 是 H 的祖先，且 M 的树与 H 相同。记录三个 SHA 和树 ID。PR API 的 `head.sha` 与 Actions API 的 `head_sha` 常显示分支头，不能单独证明作业检出了 M。

```bash
set -euo pipefail
git fetch --no-tags "$canonical_remote" "refs/pull/$pr_number/merge"
merge_sha=$(git rev-parse FETCH_HEAD)
test "$(git show -s --format=%P "$merge_sha")" = "$base_sha $pr_head_sha"
git merge-base --is-ancestor "$base_sha" "$pr_head_sha"
test "$(git rev-parse "$merge_sha^{tree}")" = "$(git rev-parse "$pr_head_sha^{tree}")"
```

- **Offline verification、Research paper bridge。** 使用本次 PR 事件。分别验 `offline`、`windows-paper` 作业。两份现行 YAML 都没有 `workflow_dispatch`，Offline 另有 push 触发。登记本次 PR 的当前合并结果。如果没有产生当前事件，先查事件、权限和工作流状态。对已证明指向本次 M 的 run 可重跑同一事件。若需通过正常推送新的受审版本触发 synchronize，重新固定 B、H、M 并执行新版本的全部门禁。重跑旧 run 会沿用旧事件版本。
- **Native project PostgreSQL。** 优先使用同次 PR 的自动运行。路径筛选使某些纯文档推送缺少 Native 运行。此时先获取上述 M，以普通推送发布一个唯一的 CI 分支，如 `ci/pr-114-merge-<完整M>`，只让它指向 M。读回远端 ref 核对 SHA，再用 `gh workflow run native-postgres.yml --ref "$ci_branch" --repo "$repo"`。检查新 run 的 event、分支、ID、attempt、head SHA，人工触发时 head SHA 必须为 M。当前工作流使用默认 checkout，直接 dispatch 到 PR 源分支通常检出 H。核实所有四个 Linux 分区 storage、research、dispatch、papersim 及其 linux-native 汇总门均为 success，退休 Windows Native 作业按定义列为 skipped。
- **读取实际 checkout。** 对每条 workflow 的指定 attempt，从 jobs API 枚举作业 ID 并读取每个实际执行测试的作业 checkout 日志。取 checkout 完成后的 `git log -1` 完整 SHA，或将明确的 detached checkout 行结合已抓取的 Git 对象还原至唯一的完整 SHA，并核验其父提交及树。每个应等于 M。仅看到 fetch 拉取某 ref、同名分支、绿标或截图均不足。汇总门本身未检出代码时记录其输入分区及结论。保存精简的实际 checkout 行、作业 ID、测试计数、运行号、attempt、结论与 UTC 时间。Native 的 papersim 原生用例需有启用环境和运行证据。
- **交接助手。** 从固定交付 Git 对象取得 `evidence/check_ci_merge.sh`，在合并前运行并保存逐 PR 输出。它只抽样最新 PR Offline 运行的一个作业，不检查 Native 或 Research。#114、#118 须得到 CURRENT，并逐条完成上述三项证明。日志暂不可读或 M 缺失时，先等合并 ref 生成、重取同一 attempt，必要时重跑当前版本。任何应运行的测试作业仍缺实际 checkout 证据时，登记缺口并阻塞对应 PR 合并，直至取得当前版本的证明。

## #114 和源码 PR 的两次取证

1. 确认 #114 开放、目标为 `main`，取回当前头并核对预期来源。历史头 `26dfa2a05357121665009981cd600e23c91e093a` 的 44859358224、44859358299、44859358236 检出的是旧基线。把已含交接包和 #116 的 B 普通 `--no-ff` 合入 #114 分支，核对 §85 和节点登记簿各只有一份、历史记分卡仍绑定其原运行提交。命名这个完成实质工作的头为 S。保存两父、树和相对 B 的差异。按本地推送门完成验证与独立审查后，普通推送到原 PR 分支。
2. 在 B＋S 的实际合并对象上跑全三项，确认已测范围。对 S 和这个已测合并对象新做实质性独立只读 ZCode 复审，包含对登记规则唯一数值来源、受管事务、登记输入及安全边界的审查。历史 FAIL 及后续修订按原发生顺序记录。写入源码 PR 内的 `docs/paper-sim/design/pr-114-review.md`，明确 S 的完整 SHA、B、已测合并对象、复审者及会话、UTC 日期、范围、实际发现、处置和原文 verdict。§85 以相对路径引用此回执，写入 S 的三条真实运行号及各自结果，区分历史运行。同时写明 PR 描述提供最终 H 的不可变补充回执。PR 描述同步更新。若本节点还需改可执行内容，先形成新 S 并重复本步骤。
3. 把回执及 §85 的记录组成纯文档后续提交，称最终 PR 头为 H。以 `git diff --name-status S H` 和实际 diff 证明仅有约定文档。对于 #118，同样使用其自己的回执与约定的登记文档。完成适用本地推送门后普通推送 H。在 B＋H 的新 M 上重跑并核验全部三条工作流，包含路径过滤时所需的 Native dispatch。独立只读 ZCode 审 H、S→H 的全部差异、前一份实质审查及这三条实际 checkout 证明，并就精确 H 给出 verdict。每次失败保留原记录。
4. **把最终结果写入同一仓库，同时固定源码 PR 头。** 从 H 创建独立保留分支，将 H 的完整 SHA、B、M、工作流的最终三组 ID/attempt、checkout、独立会话与最终 verdict 写入 `docs/paper-sim/design/pr-114-final-verification.md`，并在该分支的 §85 附上 H 的最终三组 ID 和回执路径。保留分支使用唯一名称（含 PR 号和 H），普通推送并按证据提交的完整 SHA 读回文件核验，PR 描述链接到该不可变提交及回执。这样 S 的实质审查已随源码 PR 入树，H 的精确回执也在同仓库且合并前可取。H 的运行号和 H 本身均在 H 生成之后才可确认。回执始终写清它实际审过的对象。后续经授权的 FX-38/39 PR 将这份最终回执及 §85 的最终行原样同步入 `main`，在其新头上正常验收。协调者并行修改 §85 时只合并相应记录。#118 的最终回执同法进入其后的授权状态/文档 PR，在阶段交回前核对 `main`。纯文档记录 PR 按其适用门禁执行，源码节点回执规则只适用于改源码的节点。
5. 紧邻合并重新获取 B、H、M、CURRENT 输出、三项结论和确切回执。协调同一时段的 `main` 合并，使用 `gh pr merge "$pr_number" --repo "$repo" --merge --match-head-commit "$pr_head_sha"`。该选项约束源头，另行复核基线。若 B 或 H 在落地前变化，先合并最新 `main`、重新查验和对变化重新做相应审核，重新生成当前证据。合并后立即读取远端实际合并提交，核对父序 B、H 和与所测 M 相同的树（GitHub 创建的正式合并 SHA 可不同）。若发生并发基线变化导致事后不符，保留实际对象，报告并安排对实际落地版本的验证，暂停依赖它的后续合并。使用普通 merge 全程保留原分支祖先。

## #118 的冲突、测试和回执

1. 核对已落地的 #114 及 FX-38/39 第一步均为新 B 的祖先。历史头 `f09850ba8ef010895d6a959cbeff401fbc706363` 的三条绿运行和 papersim `760 passed` 属于当时基线。以 #118 的当前头建立干净独立工作树，将 B 普通合入。用 `git diff --name-only --diff-filter=U` 核实实际冲突，保存合并前两头、共同祖先、逐侧 diff。此前模拟的文本冲突在 `src/polymarket_alpha_lab/papersim/store_psycopg.py`，新 B 可能改变冲突范围。
2. 逐处合并 `connect_managed`。保留 #114 的可选 clock、已验证 DSN、有/无时限的两种连接与身份验证、回滚至 IDLE、异常关闭。同时保留 #118 在验证通过并回滚后才设置的 `_papersim_managed_root`。核对显式连接、owner 连接和失败连接的身份边界。检查 D1 的受管身份、锁、获取、终局证据、跨进程回执与逐笔结算，和 #114 的优化器事务、时限与发布路径。检查工作流与 `tests/test_native_postgres_workflow.py` 中 Linux papersim 分区同时包含两个 PR 的测试，尤其是 `test_papersim_optimize_registered_runner_native.py` 与 `test_papersim_d1_operations_native.py`。审查 `cli.py` 是否保住中间 FX 节点的改动。遇到双方语义不能共存，保留所涉符号及双方意图，按交接要求停止该合并并报告。
3. 实际 ZCode 测试先跑 `python3 -m pytest -q tests/test_native_postgres_workflow.py`，再跑两个 PR 涉及的优化器、D1 获取/结算、非 D1 旧入口和受管连接专项。原生验收使用项目现有 Linux 原生 PostgreSQL 配置和隔离的项目测试实例。保存 JUnit/作业证据，核实跨进程读回、原子封存、重试恢复和最终分区中上述两组新增测试均实际运行。完成本地全套与推送门，形成 S 后执行两次完整 CI 与两次对应的复审。把冲突前两侧、实际解决差异、共用工作流清单和中间 FX 保留情况交给独立只读 ZCode。冲突解决本身列入代码审查范围。
4. 随 #118 源码 PR 提交 `docs/paper-sim/design/pr-118-review.md`。在 PR 描述中移除“待此 PR 的实际结果”，列出 S 与最终 H 对应的三项运行、实际 checkout、结果以及不可变最终回执。最后的 S、H、M 和合并核验遵循上面的步骤，保留分支的文件名使用 `pr-118-final-verification.md`。把 MC-1～MC-5 的处置、PR 合并提交号、三条工作流与回执路径纳入阶段交接。

在阶段交接书 `docs/handoffs/2026-11-19-optimizer-phase1.md` 的 MC-3 行，按执行顺序列出 #114、FX-38 与 FX-39 第一步的联合 PR、#118。每项填写实际 PR 号、构建节点时的 `main` 完整 SHA、最终 PR 头、远端实际合并提交完整 SHA、GitHub `mergedAt` 的 UTC 日期及时间。附两次 `git merge-base --is-ancestor` 的实际参数与结果，分别证明 #114 合并提交是联合节点起始基线的祖先，联合节点合并提交是刷新 #118 所用基线的祖先。记录 #114 落地核验时间以及禁令延续至联合节点合入的执行情况。某步尚待执行时保留实际状态，取得提交与时间后再填写。

任何红灯先按实际证据区分常量过期、平台差异、产品缺陷。前两类按常规节点修订，真实缺陷依交接要求停下报告。保留首败、各 attempt 和复审史，维持规则阈值、测试强度、冻结夹具、迁移与纸面运行边界。O-5 的不收敛失败路径测试和 O-6 的 pytest `-rs` 差异属于第一阶段出口，登记负责节点与结果。历史 #116 的 STALE 例外只使用一次于该文档 PR。
