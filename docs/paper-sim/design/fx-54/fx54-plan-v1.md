# FX-54 声明项目的 Python 下限

状态为待独立计划复审。登记泳道按 FX-52、FX-51 登记、FX-53 登记、FX-54 排期。项目的发布解释器是 Python 3.12.14，三条 PR 工作流使用 3.12。R6-11 要求将项目最低版本声明为 `>=3.12`。

1. 从届时合入的 `main` 新开普通节点，只将 `pyproject.toml` 的 `[project].requires-python` 改为 `>=3.12`，用 `uv lock` 刷新 `uv.lock`，检查差异来自最低版本改变，保留可继续使用的锁定依赖版本。待退役的 `src/polymarket_alpha_lab/research_strategy_resolution_probability_scenario_ladder_report.py` 沿用 `2fbcd0d8` 的逐字节内容，Git blob 为 `f409b6165f2c7565b0d5ab0e4ff8657ca28adddd`。
2. 在隔离环境运行 `uv lock --check` 和 `uv sync --locked --extra dev --extra postgres --python 3.12.14`，核对项目和锁中的两个下限相同。另用可用的真实 Python 3.11 解释器和新的临时环境，显式选择该解释器做锁定安装，验收预期是因 `requires-python >=3.12` 拒绝。回执记录实际解释器、退出码和原因。保留 3.11 原有测试和历史结果。
3. `project_postgres/distribution.py` 生成和校验的 v1 分发清单另有固定 `python_requires='>=3.11'`。在受支持的 3.12 环境执行 `tests/test_project_postgres_session_bundle.py` 和 `tests/test_project_postgres_version_switch_native.py` 的既有验证，保留历史格式的回归及硬编码范围记录。本节点的授权范围是项目安装声明，分发清单格式的兼容演进单列。
4. 截至交接基线，`docs/paper-sim/runtime/README.md` 只在 `handoff/sports-runtime-20261123`（审计提交 `bcce1b17f7263ab89859febfcb56188c295ab371`）。将“Python 3.11 及以上版本”改为“Python 3.12 及以上版本”交给引入这份 README 的 FX-48 批次，随该 PR 核对和登记。若它在 FX-54 开工前已合入，则在本节点改现有 README。`main` 已入库的交接包保留审计原文，避免提前引入运行层。
5. 独立计划 PASS 后实施。用 3.12 运行聚焦和全量验证、编译，检查冻结文件与归档 blob 相同、四个冻结夹具逐字节一致，并走 `git diff --check`、CodeGraph、敏感内容检查、独立候选复审和三条 PR 工作流。入库保存正反例结果、冻结 blob、实际 PR 头和三条运行编号、`check_ci_merge.sh` 的 `CURRENT`。运行层 README 若尚待 FX-48，单列其当前状态和后续合并回执。

依据见 [R6-11](../../../handoffs/2026-11-24-incremental-review/01-review-report.md)、[FX-54 与登记泳道](../../../handoffs/2026-11-24-incremental-review/02-remediation-and-next-steps.md) 和 [11-24 提示词步骤 5](../../../handoffs/2026-11-24-incremental-review/03-local-agent-prompt.md)。
