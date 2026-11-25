# FX-53 事后复审小修实施与验收草案

登记依据为 `97091184db35f4f6fe7d06d0cccb6472bcee7d23` 的 `DELIVERY_PLAN.md` §84.2.3。本地 `owner-replies` 后继检出也保留该节。代码与测试按该对象核对，与 `e65be3e7bfed96d52be1a2c9b392627b0ab2d23b` 对应目录相同。以下路径相对仓库根。三项沿用 R6-10 的小修定性和各自维护窗口。

## 实施顺序与写权

现在可分别准备评估输入和国家队模型族的改动。协调者核实登记正式落地，再按 §84.2.3 的窗口排入对应节点。两项的源码和测试文件互不重叠。MO-2 单独处理，须等 **NBA v2 和 FX-47 均交还 `src/polymarket_alpha_lab/papersim/store_psycopg.py` 写权**，再由一名实施者对照交还后的共同基线落笔。等待共享文件期间，其余两项可继续独立准备。

## MO-4 · 评估输入维护窗口

`src/polymarket_alpha_lab/papersim/optimize/dataset.py` 的 `from_managed_rows` 按最小 `evidence_id` 选 `DECISION`，按最大 `evidence_id` 选 `CLOSE`。既有 `effective_at` 只收 `select_effective_outcome` 选出的 `YES`、`NO`、`YES-basket` 的 UTC `observed_at`。在既有 CLOSE 时序检查之后，直接比较同一标识的 decision 与有效结算时间。仅在 decision 更晚时以 `ValueError` 拒绝，沿用 `dataset_time_inversion` 错误格式。现有 CLOSE 和开赛检查保有原顺序与诊断，`VOIDED`、`UNKNOWN` 和缺结果继续走原语义。来源为 `docs/paper-sim/design/mo-4/reviews/pr-79-retrospective.md` 第 1 条。

在 `tests/test_papersim_optimize_dataset.py` 复用原有行构造器，仅取 DECISION，省去 `event_start_at`，让断言只受结算时间影响。晚于有效结算时须命中新时序原因，早于和等于时均产出原 observation 与 outcome。保留有 CLOSE 的倒置、开赛严格先后、未知结果及未来行注入用例。

## MO-5b · 国家队模型族维护窗口

- **F-1**　在 `tests/test_papersim_optimize_families_intl.py` 的 `test_elo_family_predict_matches_direct_model` 使用该用例自己的可变快照。首次 `fit` 前深拷贝留底，`fit` 返回后立即比较传入对象与留底，第二次拟合和预测后再比较。保留默认和非默认 `home_advantage` 与直接模型逐值相等的原断言。
- **F-2**　在 `src/polymarket_alpha_lab/papersim/optimize/families/intl.py` 的 `IntlEloFamily.fit` 完成 `_fit_params` 和 `_variants_from_envelope` 后、构造 `IntlEloFitted` 前，比对 `checked["slope"]` 和 envelope 中档的实际斜率。家族现行九变体是三档斜率各配三个乘数，中档在默认网格为 `0.47`，两端为 `0.40` 和 `0.55`。按中档数值校验，保留变体原值、名称及顺序。手工构造 `ModelSpec`，用合法参数 `slope=0.40` 搭配中心为 `0.47` 的完整默认 envelope，断言 `fit` 因中心错配抛出 `ValueError`。两者单独均合法，且 `0.40` 在 envelope 外档也出现，能检出误用成员检查的实现。

在同一族测试保留合法默认与 `spec_with` 平移中心的通过路径，重核默认规格身份、全部九变体和预测。`tests/test_papersim_optimize_search.py::test_real_off_grid_international_active_is_scored_and_retained_exactly` 已覆盖合法 `slope=0.48` 的实际拟合与预测。Totals 族继续使用既有空 envelope。来源为 `docs/paper-sim/design/mo-5b/reviews/pr-84-retrospective.md` 的 F-1、F-2。

## MO-2 · 共享文件交还后的独立小修

受管连接默认非自动提交，身份校验后以 `IDLE` 交接。`load_optimization_runs`、`load_promotion_decisions` 使用裸游标，`registry_freeze_state` 经 `_registry_controls` 读取。`src/polymarket_alpha_lab/papersim/optimize/runner.py` 公开导出的 `status_report` 无自身事务，直接调用冻结状态和运行读取入口。该基线的 `papersim`、`tools` 和测试未见它的直接调用。`src/polymarket_alpha_lab/papersim/cli.py` 的四处相关读取沿 `score`、`decide`、`apply`、`rollback` 或状态路径处在调用方事务内。原复审记录的是风险敞口。来源为 `docs/paper-sim/design/mo-2/reviews/pr-83-retrospective.md` 的 R-1 及 `docs/handoffs/2026-11-24-incremental-review/evidence/retrospective_followups.txt`。

沿 `load_model_spec` 的 `with conn.transaction()` 模式，在三个公共入口分别包住查询和取数，冻结入口覆盖 `_registry_controls` 的读取。初始空闲时由入口收束本次读事务，已有调用方事务时由保存点隔离。保留按 family/lineage 过滤、倒序、空结果和冻结折叠，以及调用方对外层提交或回滚的决定。

扩展 `tests/test_papersim_optimize_registry_native.py` 的原生 PostgreSQL 验收。三个入口逐一断言 `IDLE → IDLE`、`INTRANS → INTRANS` 以及自动提交设置。沿用现有 `_expect_read_transaction`、显式和默认非自动提交下的隐式调用方事务、提交/回滚标记行及第二连接可见性。沿现有 `55P03` 读失败用例锁住各入口读取的对应表，确认异常可见、来向状态恢复、外层标记行只随调用方提交或回滚。结果断言覆盖读者原有返回结构。

## 节点验收与既有 R6-13 回执

草案可先交全新、独立的实际 Linux ZCode 以 guarded 只读方式审阅。正式登记落地、计划取得 `VERDICT: PASS` 且所属窗口开放后，由原生 `gpt-6.2-sol` 以 `reasoning_effort=max` 实施。实际 Linux ZCode 分开承担测试和独立只读复审，各角色关闭快速模式。按对应候选 SHA 记录聚焦测试、全套 Linux 验证、编译、`git diff --check`、受跟踪内容敏感信息检查、CodeGraph 同步与既有可达性核对。由协调者登记提交和回执，沿用合入头三条 CI 与 `check_ci_merge.sh` 的 `CURRENT` 条件。四个冻结夹具、四个闸门阈值、十变体集合、自检十项及已发布的回归数字均沿用既有基准。

`tests/test_papersim_optimize_metrics.py::test_anchor3_wp_p4_paired_comparison` 已按 §84.2.3 走服务器专用验收。2026-11-24 00:17:55–00:17:57 UTC，检出 `e65be3e7bfed96d52be1a2c9b392627b0ab2d23b`，实际 Linux ZCode 会话 `sess_c476cae6-b029-4241-925a-c8937aed98f8`，1 通过、0 跳过。固定输入 SHA-256 为 `51d7da0e6dd09176b8c6141ba1fd99dc7e63e0995ea4a354f6b5fc1c41ce7899`。原回执 `docs/paper-sim/design/fx-53/wp-p4-anchor-20261124.txt` 共 1,109 字节，SHA-256 为 `882bf6e65d99ad0763620af7d95fdda35e558f9937b6e1d37388954b0196f786`。原测试、输入的固定摘要和 `WP-P4 evidence JSON is server-only` 跳过条件继续有效。#116 作业 `136680617790` 与 #118 作业 `136739251486` 各多一次既有 CI 跳过，历史本机对照见 `docs/paper-sim/design/pr-114-review.md`。
