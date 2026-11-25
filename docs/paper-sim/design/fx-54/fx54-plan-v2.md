# FX-54 将项目安装下限登记为 Python 3.12（修订 2）

状态为待原独立 ZCode 审者复审。对应 R6-11，源码基线为 `ab6edff2d64ac8aa5c2831d748b8730b63f8312d`。发布解释器是 3.12.14，三条常规 PR 工作流选择 3.12。登记泳道依次为 FX-52、FX-51 登记、FX-53 登记、FX-54；优化泳道可按已有顺序推进。以下实施和验收均在独立计划 PASS 后进行。

## 交接、写入范围与历史（复审第 1、7 项）

1. 从登记泳道刚合入的干净 `main` 建本节点独立 checkout/worktree，记录启动基线和确切 NBA v2 头，以 `git diff --name-only <共同祖先>..<NBA头> -- pyproject.toml uv.lock` 核对两者的写入交集。本次计划核对的草稿头为 `256f8491c324b04e5dd9789c012ae84f654853f2`，相对基线的上述两个路径返回零行；开工时须对届时实际头重做并登记。若出现交集，协调者在两项之间安排单写者、以新基线复核锁差异。改元数据、生成锁及建环境均在 FX-54 的独立工作区进行。
2. 在 `pyproject.toml` 的 `[project].requires-python` 设置 `>=3.12`，以“锁、正例与反例”中固定的 uv 生成 `uv.lock`。将根 README 指向的现行 `docs/development/linux-local-setup.md` 第 18 行项目下限更新为 3.12，保留发布解释器 3.12.14。运行层 README 依下文 FX-48 接手时点处理。在同一 PR 新增节点登记及脱敏回执。源码、测试、CI 配置、旧迁移、已发布评估数字和既有交接包均在写入范围之外。
3. 以下记载的是当时的口径和执行结果，核对其原有文本逐字节保留。`docs/development/windows-local-setup.md:10`（含旧参考工具记录）；`docs/safe-data-version-switching-compat-v0.1.0.md:107,145–146,274,330`；`docs/handoffs/g2-activation-readiness.md` 和 `g2-first-real-run-runbook-draft.md`；`docs/paper-sim/research/league-admission-20261119/europe-basketball/README.md:66`；`DELIVERY_PLAN.md` 约 3134 行的 D-tuple 采用原文和 4449 行 #95 的双版本结果；`docs/superpowers/plans/2026-06-*` 的历史计划。保留原 3.11 测试、固定版本的分发兼容记录、冻结源码及四份夹具。仅对现行声明及新增登记做差异核对，历史 3.11 的字面命中有以上归属。

## 锁、正例与反例（复审第 3、4 项）

- **统一工具。** 在节点隔离工具目录选用与三条工作流一致的固定 `uv 0.10.0`（既有该版本或与 CI 相同的固定发行物）。记录实际 `uv --version`，用同一可执行文件完成 `uv lock`、`uv lock --check` 和两次 `uv sync --locked`；不带 `--upgrade`。在准备阶段选定真实 CPython 3.12.14；使用其已安装路径或在隔离工具目录预置该登记版本，记录精确版本。普通 shell 的 uv 或 `python3.12` 只有版本相符才可用作此项证明。
- **锁差异。** 相对节点父提交，允许的变化只有 `requires-python = ">=3.11"` → `">=3.12"`（原第 3 行），以及 uv 0.10.0 若自动裁去的 `psycopg-binary` 下原 88–98 行全部十一条 cp311 wheel；允许它们全部保留。对照实际父提交定位条目并核对旧行数，所有包名、版本、依赖、marker、来源、哈希、其他 wheel 和锁中其余字节相同。若工具生成此范围以外的改动，保留原生生成差异，先查明工具和基线，再将必要的范围变化送原审者处置，禁止手改锁或换版本掩盖差异。
- **3.12.14 正例。** 在干净的候选检出上，用固定 uv 做 `uv lock --check` 和 `uv sync --locked --extra dev --extra postgres --python <3.12.14解释器>`，环境指向独立临时目录；均须退出 0。用该环境读取实际 `sys.version_info`，用 TOML 分别读项目和锁的下限，并用 `importlib.metadata.metadata("polymarket-alpha-lab")["Requires-Python"]` 读安装产物。三处均须等于 `>=3.12`，同时核对现行 Linux 说明。回执记录解释器和 uv 版本、退出码、三个字段和干净树的提交。
- **3.11 反例。** #95 记录过真实 3.11；本次制订计划时 `command -v python3.11` 找到的既装 CPython 自报 **3.11.15**。实施时先用该真实解释器重新读取版本，并取绝对可执行文件路径。若该入口缺失，查已安装的 uv 管理目录或另一可用隔离执行环境中的真实 3.11，再核验版本。负例从候选的另一干净检出运行，未激活其他 venv；为 `UV_PROJECT_ENVIRONMENT` 选尚不存在的临时路径。设 `UV_PYTHON_DOWNLOADS=never`、`UV_OFFLINE=1`，使用同一 uv 0.10.0 执行 `uv sync --locked --offline --no-python-downloads --extra dev --extra postgres --python <已核实的3.11绝对路径>`。预期退出非零（配置不兼容通常为 2，保存原始退出码），报错同时指出该真实 3.11 与项目 `>=3.12` 的要求。应在依赖解析或任何下载之前拒绝，未创建 venv、未变动项目和锁；缺包、网络失败或找不到解释器均不计。负例不运行 `uv python install`。若所有既装来源均无真实 3.11，登记“未执行，缺真实 3.11”，此项合并验收尚缺；在已有真实 3.11 的隔离执行环境完成并补回执后方可合入。

## v1 分发、运行层及登记去向（复审第 2、5 项）

| 同一 FX-54 PR 写入 `DELIVERY_PLAN.md` 的项目 | 处理及证据 |
| --- | --- |
| R6-11、D-tuple 下限和节点登记 | 在节点登记簿 §84 追加本轮 FX-54 行，并在同一新条目注明自本节点起安装下限 ≥3.12、发布 3.12.14，引用 §68 的 D-tuple 旧记录，保留其原文。登记实施提交、PR 号、候选/PR 头、合并提交、Offline / Research paper bridge / Native project PostgreSQL 三条运行号、计划及代码复审判定/回执路径、正反例和冻结核对的库内位置。按本表之后的回填顺序绑定最终值。 |
| 后继事项“v1 分发清单的 Python 下限” | 在 §84.2.3 下新增有名称的跟踪条目，交原生分发节点维护者。当前 `project_postgres/distribution.py` 的生成和验证都硬编码 v1 `python_requires='>=3.11'`，并把 `pyproject.toml`、`uv.lock` 打进包。说明本节点以后，新造 v1 kit 会同时携带旧标签和要求 ≥3.12 的项目元数据，实际锁定安装受后者约束。后继项的验收是明确新发布格式如何表达实际下限、旧 v1 包保持可验证，并以实际 kit 构建、读回和版本兼容回执关闭。本节点保持历史格式、旧包和验证器原样。 |
| FX-48 引入运行层 README 的批次 | 在 §84 的本轮 FX-48 验收登记追加“运行解释器及 README”条件，并从 FX-54 行链接。审核草稿 `bcce1b17f7263ab89859febfcb56188c295ab371` 的 `docs/paper-sim/runtime/README.md` 仍写“Python 3.11 及以上版本”，源码基线未含此文件。若 FX-54 开工前已正式合入，同 PR 将现行 README 改为“Python 3.12 及以上版本”；若仍在草稿，由实际引入文件的 FX-48 PR 改句并对其运行环境记录真实解释器 ≥3.12、锁定安装及该批次提交/回执。登记 3.11 锁定安装在 FX-54 后会被拒绝，FX-48 验收前须实证，不能从 README 推断。 |
| 本节点的分发覆盖 | `tests/test_project_postgres_session_bundle.py` 使用合成 v1 清单；在受支持版本跑它，登记硬编码标签回归的实际计数。另绑定最终候选的 **Native project PostgreSQL** Linux/PG18 storage 分区 **Prove native version switch** 步骤及其 JUnit，具名用例 `test_native_same_root_source_selection_rollback_and_refusals` 必须执行通过，记录全文件 pass/skip 与该用例 1 pass / 0 skip；本地等价证据须显式设置既有项目 opt-in 并使用符合登记的 PG18 二进制前缀。此项验证 OLD→CURRENT、回退与拒绝。独立的 `native-distribution.yml` 依 PR 路径过滤不会因本计划的元数据、文档、锁或登记文件触发；对最终 PR 路径复核，无独立 kit 构建则在 FX-54 行明确填 `NOT_TRIGGERED / kit proof 未运行`。若实际另有同候选运行，据实列其编号和结论。 |

同一 PR 先提交明确的登记行、实施哈希、可得的真实回执、后继事项及其验收归属，按项目惯例把脱敏证据存于 `docs/paper-sim/design/fx-54/` 并从登记行链接。最终 PR 头的工作流编号、计划/代码复审原件及 `check_ci_merge.sh` 的 `CURRENT` 在合并前绑定最终头和 CI 检出的合并参照。实际合并后，从 PR 已生成的对象核实 merge SHA，按节点登记流程在后继普通登记提交回填最终头、合并提交和三条最终运行号。各阶段均记录当时可核实的值，避免提交自引用。原计划和 FAIL 回执保留原件。

## 冻结校验与节点出口（复审第 6 项）

候选上独立求值并保存两条命令的输出。`git rev-parse 2fbcd0d8:src/polymarket_alpha_lab/research_strategy_resolution_probability_scenario_ladder_report.py` 与 `git rev-parse <candidate>:src/polymarket_alpha_lab/research_strategy_resolution_probability_scenario_ladder_report.py` 必须相同。该历史对象本次只读核对为 `f409b6165f2c7565b0d5ab0e4ff8657ca28adddd`；验收从两次 Git 查询重新导出。四份冻结文件实行同样的历史树 ↔ 候选树逐文件 blob 比较，以 `ab6edff2` 为本次审计锚，列全路径和两侧结果：

- `tests/fixtures/papersim/regression_banned_path.json`
- `tests/fixtures/papersim/regression_gate_arithmetic.json`
- `tests/fixtures/papersim/regression_r19_crypto.json`
- `tests/fixtures/papersim/regression_r23_soccer.json`

以受支持的 3.12 执行聚焦和全量验证及编译，保留实际 pass/fail/skip，完成 `git diff --check`、CodeGraph 同步和受跟踪内容敏感信息检查。独立代码复审批次覆盖变更、安装元数据、原生版本切换回执、历史字节及两项后继登记。最终 PR 头三条工作流均绿、`check_ci_merge.sh` 返回 `CURRENT`、代码复审 PASS 后普通合入并回填。依据为 `docs/handoffs/2026-11-24-incremental-review/01-review-report.md` R6-11、同包 02 的 FX-54 及泳道顺序、03 的步骤 5。
