# PR #118 刷新计划补充

本稿补充已签署的 `merge-conditions-plan-v2.md` 第「#118 的冲突、测试和回执」节，作者负责计划，独立复审按原计划派工。依据是本地 CodeGraph、下列 Git 对象及联合节点已审计划。此次只读核对，未创建分支、执行合并、启动数据库或运行测试。

| 对象 | 完整 SHA |
| --- | --- |
| #114 已落地合并提交 A | `d216059339dba9c3ca34d65813e9cf2ba98a0dd5` |
| 联合 FX-38 / FX-39 第一步本地候选 J，唯一父为 A | `372f9cbebe2b60c84a39237efb62551f77c315dc` |
| #118 历史头 O | `f09850ba8ef010895d6a959cbeff401fbc706363` |
| J 与 O 的共同祖先 | `2bdc4cccacc925ea8e6569c8865b90c0b0c671e6` |

**刷新起点。** 等联合节点经独立审核、CI、普通合并落到规范仓库的 `main`，并核实实际合并提交的两父和树。再取当时的 `main` 为 B、#118 实际头为 O₀。J 上已有的 251 项聚焦离线和 15 项原生结果属于联合候选。#118 的源码测试与回执绑定其自己的新提交。合入前的操作约束仍依原计划执行。

## 双方需要保留的源码

- 在上述共同祖先上，两侧都触及的文件只有 `src/polymarket_alpha_lab/papersim/store_psycopg.py`、同目录的 `cli.py`、`.github/workflows/native-postgres.yml` 和 `tests/test_native_postgres_workflow.py`。实际冲突由刷新当天的 B、O₀ 和 Git 的未合并索引确定。
- J 的 `store_psycopg.py:135–166` 与 A 相同。保留 `connect_managed(repo_root, *, info=None, clock=None)`，先用 `managed_dsn(..., owner=False)` 验证 DSN，在 `binding.bound(instance_id, root_sha256, system_identifier)` 内开应用连接。无 clock 走 `psycopg.connect`，有 clock 走 `_connect_with_deadline`，握手上限取连接时限与命令硬时限的较早者。有 clock 时，身份查询与回滚都在 `optimization_database_phase(..., stage='connection_identity', closeout=True)` 内完成。两路均依次运行 `binding.verify_connection(connection.cursor())` 和 `connection.rollback()`，交出 IDLE 的连接。
- O 的 `store_psycopg.py:128` 给出唯一赋值 `connection._papersim_managed_root = repo_root.resolve()`。把这一行接在两路共同成功出口，位于有 clock 的上下文退出之后、外层 `try` 之内、`except BaseException` 之前。身份、回滚、时限或标记赋值失败时，已取得的应用连接按原逻辑关闭并抛出。标记只缓存该物理连接已验证的根。保留 `connect_owner`、`connect_explicit` 和 legacy 的各自构造路径。
- #118 的 `d1_managed_identity` 比较解析后的根、要求 IDLE 并重新核对身份。`d1_resolution_identity` 在同连接的 INTRANS、repeatable-read/read-only 视图内复核实例和应用身份。`_record_settlement` 用该标记决定是否取得逐笔结算锁。复核 D1 操作锁跨 PREPARED 和逐笔事务、resolution 批次与 SUCCEEDED 原子封存、逐笔业务与其成功回执同事务，并从新进程验证竞争和重试后的实际业务行。检查 #114 的 optimizer 发布时限、提交确认与重连仍使用原事务边界。
- O 对共同祖先的 `papersim/cli.py` 全部差异仅一行，位于 `_cmd_settle` 调用 `settle.run_settle(..., managed=args.managed)`。在 J 的 CLI 上接入这一行。仅 D1 operation key 走受管结算，原 B2/B3 入口继续可用。保留 J 的 `_optimize_close_deviation` 和政策精确比较，候选过闸与现役入场独立取买入侧 CLOSE 减各自成交价，候选数 29 只标注、30 起按含等号的一美分边界判断。保留 `score` / `status` 的 `forward_comparison`、扫描出价身份及不可用原因。`_cmd_optimize_apply` 在尝试回放、校验 actor/config 或写角色之前对 `PROMOTE` 和 `PROMOTE_PENDING_CONFIRM` 返回 `papersim_optimize_apply_promotion_unavailable`，保留 RETIRE / ROLLBACK 及原回放。

## 分支与测试顺序

1. 后续执行者取得实际联合合并 SHA `joint_merge`、本轮 B 和 O₀，保留 `git merge-base --all "$B" "$O0"` 及该共同祖先到两侧的差异。执行 `git merge-base --is-ancestor "$joint_merge" "$B"`，按已签计划另核对 A 是联合起点的祖先。从 O₀ 建干净独立工作树及刷新分支，在其中 `git merge --no-ff --no-commit "$B"`。用 `git diff --name-only --diff-filter=U` 和 `git ls-files --unmerged` 记录实际冲突，正常解决并提交。首个集成提交 I 应满足 `git show -s --format=%P "$I"` 为 `$O0 $B`。最终实质头 S 可含后续聚焦修正。记录 `git merge-base --is-ancestor "$B" "$S"`、`git merge-base --is-ancestor "$O0" "$S"`、`git diff --check "$B" "$S"` 和 `git status --porcelain`。
2. 在当前所见输入上，`git diff --unified=0 "$B" "$S" -- src/polymarket_alpha_lab/papersim/cli.py` 应只见上述结算参数。`git diff --exit-code "$B" "$S" -- src/polymarket_alpha_lab/papersim/optimize/policy.py tests/test_papersim_optimize_cli.py tests/test_papersim_optimize_policy_native.py tests/test_papersim_optimize_runner_native.py tests/test_papersim_optimize_registered_runner_native.py` 检查联合节点的政策和测试内容。实际 B 或 O₀ 若已另有变更，以新两侧差异逐项解释。
3. J 的 Linux `linux-native-part` / `papersim` 固定 27 个文件。O 把 `test_papersim_d1_operations_native.py` 插在 `test_papersim_capture_marks_native.py` 与 `test_papersim_mo7_shadow_native.py` 之间。按两侧现状，分别在 YAML 和 `PAPERSIM_FILES` 同位置各加这一项，总计 28。保留 A 已排定的 `optimize_policy_native → optimize_runner_native → settle_store_native → optimize_registered_runner_native → report_store_native` 顺序。用 `git diff --unified=0 "$B" "$S" -- .github/workflows/native-postgres.yml tests/test_native_postgres_workflow.py` 对照。两个固定断言分别为 `test_native_partitions_preserve_exact_original_inventory` 和 `test_linux_papersim_partition_boots_throwaway_pg18_cluster`。
4. 指定本机 ZCode 在同一干净提交 S 上执行下列命令。D1 的 `d1_capture_project` 会共享克隆当前仓库并检出 `git rev-parse HEAD`，resolution journal 的进程级用例也显式检出 HEAD。未提交的解冲突内容不会进入这些子进程。令 `py` 为本项目已锁定的 Python，`proof` 为工作树外已建的本次证据目录。

```bash
"$py" -m pytest -q -rs tests/test_native_postgres_workflow.py
"$py" -m pytest -q -rs --junitxml="$proof/118-offline-focused.xml" \
  tests/test_papersim_store_native.py::test_connect_managed_hands_off_a_verified_idle_connection \
  tests/test_papersim_store_native.py::test_connect_managed_identity_mismatch_fails_closed \
  tests/test_papersim_cli.py tests/test_papersim_capture_marks.py tests/test_papersim_settle.py \
  tests/test_papersim_optimize_cli.py tests/test_papersim_optimize_policy.py \
  tests/test_papersim_optimize_runner.py tests/test_papersim_optimize_rule_sources.py
```

两条既有 fake 交接用例覆盖无 clock 时验证/回滚、身份不符关闭。O 的测试没有直接断言 `_papersim_managed_root`。在最终树核对这一覆盖缺口，必要时在上述 fake 组内扩展两种 clock 路径的断言，观察回滚前无标记、成功后根等于 `root.resolve()`、身份/回滚异常时关闭且无标记，并核对显式和 owner 连接。这样可以检出只给其中一路标记、把赋值提早的解冲突错误。

在工作流等价的 Linux 一次性 PG18 环境运行下一组。沿用 `POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES=1`、已验证的 `POLYMARKET_ALPHA_LAB_NATIVE_PG_PREFIX`、项目原有测试连接及纸面夹具配置。记录原生开关是否启用和实际 nodeid，连接配置值不入回执。

```bash
"$py" -m pytest -q -rs -o junit_family=legacy --junitxml="$proof/118-native-focused.xml" \
  tests/test_papersim_store_native.py::test_native_connect_managed_hands_off_idle_and_writes_persist \
  tests/test_papersim_capture_marks_native.py::test_d1_three_original_observations_recover_in_another_cli_process \
  tests/test_papersim_d1_operations_native.py \
  tests/test_papersim_optimize_policy_native.py \
  tests/test_papersim_optimize_runner_native.py::test_native_managed_cli_seals_and_replays_calibration_nonconvergence \
  tests/test_papersim_settle_store_native.py::test_d1_settle_recovers_committed_pending_and_failed_items_in_a_new_cli \
  tests/test_papersim_settle_store_native.py::test_d1_settle_books_completed_basketball_including_overtime_and_explicit_push \
  tests/test_papersim_optimize_registered_runner_native.py::test_native_optimizer_handshake_uses_earlier_deadline \
  tests/test_papersim_optimize_registered_runner_native.py::test_native_completed_handshake_after_deadline_is_closed_without_delivery \
  tests/test_papersim_optimize_registered_runner_native.py::test_native_optimizer_commit_confirmation_reuses_earlier_handshake_deadline \
  tests/test_papersim_optimize_registered_runner_native.py::test_native_cli_cannot_apply_decision_to_substituted_or_unproved_pair \
  tests/test_papersim_optimize_registered_runner_native.py::test_native_cli_exact_rollback_replay_after_later_handover \
  tests/test_papersim_d1compare_journal.py::test_resolution_capture_cli_seals_atomically_and_recovers_across_processes
```

5. 在新的一次性原生环境按工作流顺序跑完整 `papersim` 分区。核对 JUnit 中 D1 operations、registered runner 的握手/发布、CLV 数值边界、D1 进程恢复和 O-5 的必运行 nodeid 有实际执行，核对单例后果、重复执行、提交失败回滚及兼容入口的断言。逐项登记跳过原因、首败及重跑。

```bash
set -euo pipefail
inventory=$("$py" -c 'from tests.test_native_postgres_workflow import PAPERSIM_FILES; print(*PAPERSIM_FILES)')
read -r -a papersim_files <<< "$inventory"
test "${#papersim_files[@]}" -gt 0
"$py" -u -m pytest -p no:faulthandler -p tests.ci_thread_dump -q -rs -s \
  -o junit_family=legacy -o faulthandler_timeout=120 "${papersim_files[@]}" \
  --durations=20 --junitxml="$proof/118-papersim-full.xml"
```

6. 按已签计划完成全量离线 `"$py" scripts/verify_local.py --full`，其中实际执行 `-m compileall -q src tests scripts` 和全套 `pytest -q -ra`。完成 CodeGraph、敏感信息与推送门。S 的实质审核涵盖实际冲突解决及测试证据。按原计划生成文档头 H，分别对 B＋S 与 B＋H 的实际合并对象核验 Offline、Native、Research 三条工作流及 checkout。最终回执、精确 H 的独立审核、普通合并与实际父/树核验继续依原计划。
