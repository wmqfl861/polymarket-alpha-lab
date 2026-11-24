# FX-41 NBA v2 集成与复审范围

本节点以 `ab6edff2d64ac8aa5c2831d748b8730b63f8312d` 为基线，从保留提交 `256f8491c324b04e5dd9789c012ae84f654853f2` 移入 13 个 NBA 文件。支持树合入 `main` 后，按同一合入提交核时钟及执行前置条件，才进入登记、只读预览和唯一正式命令。[单次计划](plan.md) · [独立计划回执](plan-review.json) · [复审补遗](operator-addendum.md) · [原会话补遗回执](operator-rereview.json)

## 已签对象

| 审核范围 | 核过的原附件 | ZCode 退出与结果 |
| --- | --- | --- |
| 科学计划及输入 | [单次计划原审附件](reviews/scientific-plan/approved-scope-plan.md) | [回执](reviews/scientific-plan/receipt.json) · [结果](reviews/scientific-plan/result.txt) |
| 每规格六变体 | [六变体补遗](reviews/six-variant-addendum/author-correction.md) | [回执](reviews/six-variant-addendum/receipt.json) · [结果](reviews/six-variant-addendum/result.txt) |
| 受管登记、认领与预览 | [受管接口计划](reviews/managed-binding/managed-seam-plan.md) | [回执](reviews/managed-binding/receipt.json) · [结果](reviews/managed-binding/result.txt) |
| 隔离合成计时方案 | [计时方案](reviews/timing-plan/synthetic-timing-plan.md) | [回执](reviews/timing-plan/receipt.json) · [结果](reviews/timing-plan/result.txt) |
| #118 后放行 | [受管发布计划](reviews/release-plan/plan.md) · [计时清单](reviews/release-plan/approved-timing-checklist.md) | [回执](reviews/release-plan/receipt.json) · [结果](reviews/release-plan/result.txt) |
| 登记模块及其测试 | [完整源码附件映射](reviews/registration-module/complete-source-pages.json) | [回执](reviews/registration-module/receipt.json) · [结果](reviews/registration-module/result.txt) |
| 私有执行接口与对应测试片段 | [原补丁附件映射](reviews/private-runner-seam/patch-pages.json) | [回执](reviews/private-runner-seam/receipt.json) · [结果](reviews/private-runner-seam/result.txt) |

上表的原回执退出码为 0，原结果的字节数和 SHA-256 均与回执相符，末行为 `VERDICT: PASS`。原件按字节归档。两个历史代码复审回执的 `session_ids` 原为 []。登记模块复用完整源码和测试的原审核范围。私有执行接口复用原附件中在当前候选逐段匹配的 25 个 runner 补丁段和 3 个测试补丁段。[原附件合订](reviews/private-runner-seam/reviewed-patch.txt.gz) 解压后匹配回执附件映射的合订 SHA-256。候选对应区间和逐段字节摘要见[比对表](reviews/private-runner-seam/candidate-comparison.tsv)。最终候选须通过独立 ZCode 审核。

## 4,811 试验的调用链

- CLI 仅为固定 NBA 族和截止路由到 v2，要求受管连接。读取时先核清单快照的类型、版本、长度和摘要。旧 CLI 沿原历史协议组装输入。
- 受管 store 重建固定登记事件、执行提交和实例身份、原件、比较者、清单与物理计划。只有绑定成功，才组装 v2 的 4,811 预算。整个计划逐项匹配 283 折 × 17 个规格的顺序、两个目标的掩码和共享拟合上下文。
- 在操作锁下，第一次持久认领只接受 attempt 1。认领事务复核当前两谱系比较规格。首次认领被拒绝时无写入和推理。已有根仅返回原终态或中断，原进程可核实其提交并接续，进程内私有 claim 只交付一次。
- 私有 runner 重验登记、来源、计划与掩码，公共 Python 入口仍用 2,000 上限。受管发布核完整有序计划、实际执行前缀、物理与目标使用计数、终态关联。只读预览取登记和状态元数据。
- 草稿缺口修正还包括丢失登记提交回复时保留首个不确定错误，覆盖恢复连接不合格或确认预算耗尽的情况。

## 规则来源断言

| 测试 | 基线定位 → 本节点定位 | 延续的断言 |
| --- | --- | --- |
| `test_holdout_tables_project_the_four_registry_rows` | `execute_optimization_run` → `_execute_optimization_run`，`evaluate_stage1_rule` 原定位保留 | `stat.ci95_upper < _STAGE1_CI_UPPER_BOUND` 的右值仍是原规则常量。 |
| `test_statistics_have_one_owner_per_registered_parameter` | `execute_optimization_run` → `_execute_optimization_run` 的工件组装 | `statistical_parameters` 仍引用 `bootstrap_resamples()`、`CI_CONFIDENCE_LEVEL`、`HOLM_ALPHA`、`SIGN_FLIP_REPS`、`SIGN_FLIP_SEED`。 |
| `test_public_and_managed_runner_share_registered_rules` | 新增 | 公共 `execute_optimization_run` 和受管 `_execute_claimed_nba_odr` 的 return 均直接调用 `_execute_optimization_run`，仅受管入口传 `nba_odr_claim=claim`。 |

`test_optimize_help_names_the_four_subcommands` 改名为 `test_optimize_help_names_the_experiment_subcommands`。原 CLI 退出码与 `families`、`init`、`run`、`status` 检查保留，增加 `register-experiment`。这对应削弱扫描唯一的移除报告。原生共用子进程帮助函数新增每次调用可选的等待时间，原 25 秒回复、15 秒等待和屏障的默认值维持原样。

## 单节点逐文件差异

表中的 SHA-256 对应候选文件原字节。相对保留草稿，只有 store 和原生测试进一步修改。规则来源测试采用保留草稿已校正的核心函数定位。

| 文件 | 候选 SHA-256 | 相对草稿与可复用审核范围 |
| --- | --- | --- |
| `src/polymarket_alpha_lab/papersim/cli.py` | `7ea5ca347ab23cb6c25d39d0e9757e0689e20784dc69ca22759e5768904b66be` | 字节相同。需随候选复审。 |
| `src/polymarket_alpha_lab/papersim/nba_odr_registration.py` | `6ea68cd333030d7bf982c4161c717b2701a5623e7b2a372de154d484bb4722fa` | 字节相同。原登记模块完整源码的 SHA-256 相同。 |
| `src/polymarket_alpha_lab/papersim/optimize/nba_odr.py` | `cdb6da71e6e533b37a5b9eee0d27295bd7ebc9adeb2ff12627ad1e8d5b94d196` | 字节相同。需随候选复审。 |
| `src/polymarket_alpha_lab/papersim/optimize/runner.py` | `463bfd1668eeb9ce54fb24bf47b3d2febbddefad402d44da6e3435847e7b1559` | 字节相同。原私有执行接口补丁 25 个候选侧 hunk 匹配。 |
| `src/polymarket_alpha_lab/papersim/optimize/search.py` | `0b1d0f1fe97089c9e5998e32fda19282ed17fd4792cb71eac8395e82181f1bb9` | 字节相同。需随候选复审。 |
| `src/polymarket_alpha_lab/papersim/store_psycopg.py` | `e9f0ee981749ed176ce8c2943475d67aa6d255c8a67d7df7be7cc6c97594f759` | 修正首次认领及登记确认。需随候选复审。 |
| `tests/test_papersim_cli_optimize.py` | `02b1b39c736a4825fc8e3dd0efe312937dcc3b6b24e3133102344bca2954704b` | 字节相同。需随候选复审。 |
| `tests/test_papersim_nba_odr_registration.py` | `4374ad17544d3ad9444e7df15750f8fff037d09398022358819ea488adc31335` | 字节相同。原登记模块整份测试的 SHA-256 相同。 |
| `tests/test_papersim_optimize_nba_odr.py` | `906f34c36e7b4bf85393a6ec73932a5d3c94eb3ed1a5f69dde7d6c6489383b6c` | 字节相同。需随候选复审。 |
| `tests/test_papersim_optimize_registered_runner_native.py` | `945a012845a7f086023de60fe93da2a6a7d19151ad09e7a5e8ec8796ae16ad30` | 新增首次 attempt 2 拒绝且状态零增量断言。需随候选复审。 |
| `tests/test_papersim_optimize_rule_sources.py` | `6f6d5b1073fe00fef627f2cfaf9b5190480210608f071f5afee173698e023bee` | 字节相同。需随候选复审。 |
| `tests/test_papersim_optimize_runner.py` | `67b19ca3f1f5d65fbcfb569cd2f4ea551b0c3ab7b0daf9c30a16fdaa7820ce33` | 字节相同。原私有执行接口补丁 3 个候选侧 hunk 匹配。 |
| `tests/test_papersim_optimize_search.py` | `d1a13597b55a19d4c711db8833717ba9ff5f76857c0c123d448ecbdccfbfab1e` | 字节相同。需随候选复审。 |


## 新增测试定位

以下为新增测试函数和一项改名，包含参数化用例。正式测试由独立 ZCode 按计划执行并以最终树保存回执。

| 文件 | 用例 |
| --- | --- |
| `test_papersim_cli_optimize.py` | `test_optimize_help_names_the_experiment_subcommands`<br>`test_v2_admits_only_the_managed_fixed_request`<br>`test_managed_registration_exports_only_the_committed_pointer`<br>`test_registration_parser_requires_managed_and_has_no_schedule_override`<br>`test_v2_managed_run_routes_to_store_without_legacy_compute` |
| `test_papersim_nba_odr_registration.py` | `test_round_trip_binds_exact_snapshots_manifest_and_first_receipt`<br>`test_manifest_alone_cannot_substitute_for_fixed_event`<br>`test_request_must_match_the_first_fixed_binding`<br>`test_incompatible_reused_snapshot_rolls_back_all_new_evidence`<br>`test_substituted_originals_are_rejected_before_writing`<br>`test_inventory_keeps_its_own_observation_clock`<br>`test_readback_checks_event_and_derived_snapshot_identity`<br>`test_receipt_is_read_back_when_append_helper_returns_no_id`<br>`test_append_failure_rolls_back_evidence_and_receipt_inside_outer_transaction`<br>`test_writer_requires_the_callers_transaction` |
| `test_papersim_optimize_nba_odr.py` | `test_archive_emits_reciprocal_team_rows_and_full_game_contracts`<br>`test_prediction_rest_uses_earlier_same_season_source_dates`<br>`test_source_day_start_and_tipoff_date_follow_new_york_dst`<br>`test_training_barrier_respects_whole_days_and_late_label_evidence`<br>`test_future_scores_do_not_enter_earlier_fixture_or_training_history`<br>`test_archive_reader_rejects_invalid_fields`<br>`test_reader_rejects_duplicate_event_and_ambiguous_team_games`<br>`test_gzip_and_decoded_pins_are_checked_independently`<br>`test_reader_checks_archive_season_provenance_and_retrieval_clock`<br>`test_value_free_metadata_keeps_exact_holdout_membership_without_using_labels`<br>`test_metadata_checks_nested_box_input_field_presence`<br>`test_two_season_loader_keeps_independent_espn_evidence_unknown` |
| `test_papersim_optimize_registered_runner_native.py` | `test_native_nba_v2_registration_is_atomic_and_reentrant`<br>`test_native_nba_v2_store_rejects_forged_sources_callback_and_comparator`<br>`test_native_nba_v2_once_with_preview_and_acknowledgment_recovery`<br>`test_native_nba_v2_interrupted_root_never_allows_a_new_search` |
| `test_papersim_optimize_rule_sources.py` | `test_public_and_managed_runner_share_registered_rules` |
| `test_papersim_optimize_runner.py` | `test_odr_metadata_plan_pins_days_full_specs_and_order_without_opening_results`<br>`test_odr_plan_refuses_changed_comparator_version_grid_or_context`<br>`test_odr_protocol_and_user_supplied_budget_cannot_authorize_execution`<br>`test_odr_scoring_inputs_keep_holdout_lazy_and_require_season_specific_quality`<br>`test_odr_compute_requires_the_store_claim_and_exact_registered_plan`<br>`test_odr_claimed_budget_retains_full_plan_on_expiry` |
| `test_papersim_optimize_search.py` | `test_training_only_days_count_in_each_season_and_never_become_outer_folds`<br>`test_partitioned_folds_preserve_late_and_incomplete_day_reasons` |

## 脱敏与静态验收

日程标识和登记命令名触发了联系人形状检测。runner 中的命中属于 `NBA_ODR_SCHEDULE_ID` 的日程值。登记模块、store 查询和登记测试中的命中是同一数据库命令名，计划和旧回执中的命中同样指向日程或命令。地址出现扫描比对既有值及所在文件。新增的归档来源模板用于静态比较原件来源字段。

最终验收由 ZCode 附聚焦、Native、全量、逐位回归、冻结内容和时钟的对应回执。不可达模块数在同时部署其他节点后的最终支持树复核。此处附原计划及先前已签对象，其余回执按实际执行版本登记。
