# FX-41 NBA v2 受管登记与执行接线

业主批准的唯一日程为 `fx41-odr-nba-regular-2024-25-2025-26-v2`。本补记接续已通过的原计划（SHA-256 `558a5f27f97827d54360b62e71336fe012d56e20e807a893a3894d8318fb321f`）及六变体更正（`ca3e7eb9606424b510d5dd14c404398a7f8984fbbdd7db7dd3ea3f372dc59f9c`）。#114 实际合入 `d216059339dba9c3ca34d65813e9cf2ba98a0dd5`，树为 `810cab1fa7475e9d32576b4cbf46bdfeb8cd88fd`。下文 CLI、store 和 snapshots 的行号以此提交为准。纯 API 的行号来自 `pal-nov21-fx41-nba-v2` 尚未提交的工作树。

## 可先分派的文件

一位实施者可独占新增 `src/polymarket_alpha_lab/papersim/nba_odr_registration.py` 及其专属合成单测 `tests/test_papersim_nba_odr_registration.py`。模块组装及核验该日程的规范材料、清单、固定事件和三字段指针，并提供在调用方已持锁事务内使用的私有写入和读回函数。只读函数返回登记事实。执行许可由后续 store 认领产生。这个切片能独立用内存原件和事务替身验收，无须占用 CLI、store、runner、search、共用原生测试、工作流或 FX-42 文档。

直接使用 `optimize/nba_odr.py:291` 的 `load_nba_odr_metadata`、`optimize/runner.py:4333` 的 `build_nba_odr_plan` 及其 `NBAODRPlan.{run_key,plan_bytes,metadata_bytes,plan_ref,metadata_ref}`。用 `nba_odr_historical_protocol`（4162）构造协议。正式执行阶段才使用已有 `_nba_odr_scoring_inputs`（5681）及其双季回调。沿用同季折、17 个全规格、六变体、两个目标和 38 日/304 场的既有实现。

## 登记和读回

1. **运行时固定版本。** 支持代码先用合成身份实现、验证、审核并合入。正式登记时由 `cli.require_clean_worktree_path`（1988，转交 `scan.require_clean_worktree`）取实际干净 HEAD。登记、预览和运行使用该版本。源码可预置既知的批复、库存及 gzip 指纹。实际代码 SHA、派生快照摘要与事件 ID 各在输入齐备后产生，写入库中，后续归档引用该提交。支持提交无须预知自己的 SHA 或收据。
2. **无评分材料。** 校验既定批复、库存、两份 gzip 的原字节和来源时钟。按 `NBA_ODR_ARCHIVE_PINS` 登记两个季别角色及各季两项独立 ESPN 原件缺失原因。两 gzip 是全部评分来源，文书及其 11 月 21 日时钟属登记证据。用 `_registry_comparison_snapshot`（2197）读两个真实现役和基线，复用 `_optimization_policy`（2253）中的角色/规格解码，按实际观察时刻构造登记 `PolicyContext`。由程序根据共享拟合输入指定两目标的同一上下文，沿用 `plan_evaluations` 的 `shared` 语义。登记材料引用批复、库存、归档、登记时比较上下文、`metadata_ref` 与 `plan_ref`，保存对应字节。材料引用进入协议和规范 `{schema_version, protocol, sources}` 清单，清单、材料和 `plan.run_key` 再进入登记回执。
3. **事务与锁。** `_managed_write_transaction`（2790）在已验证的 autocommit、IDLE 应用连接上启动有界 READ COMMITTED 外层事务并取得 `_REGISTRY_LOCK`。再用 `_try_advisory_lock(..., transaction=True)`（1407）取得登记专用命名空间与固定日程派生的两整数锁。登记及其提交确认均按此顺序，在锁后查原行。若先在锁外生成大块字节，锁内须重核其比较输入。运行的 session lock 继续按科学运行键持有。
4. **固定键与内容。** 例如命令 `optimize-register-experiment`、操作键 `optimize.register-experiment/<完整日程ID>`、`attempt=1`、`phase=SUCCEEDED`。用 `store.command_run_event_key`（820）按键查原行并核对命令与阶段。`_optimization_events`（2293）仅查 `optimize-run`。登记 `config_digest` 取固定绑定的规范摘要，排除待生成的事件 ID 与写入时刻。在上述事务中用 `snapshots.register_snapshot/read_snapshot`（196/234）和 `record_command_run_event`（1019）写 `CommandRunEventSubmission`（store.py 第 932 行），内部事务是 savepoint。同散列快照会沿用首写元数据，逐一核实原字节、长度、种类、版本、来源及相应时钟。重入以首次登记上下文复核完整绑定，相同返回原 ID、时间和清单，冲突全部回滚。事件 helper 重复时的 ID 为 `None`，用原行读回。COMMIT 回包丢失时在同实例新连接取相同事务锁，先确认原行。证实原事务已结束且记录缺失才重试。
5. **瞬时指针。** 确认提交后发出 `schema_version=1, manifest_sha256, manifest_byte_length` 三个字段组成的 JSON 对象，可用 stdout 临时重定向至工作树外，或用示例 `--write-pointer` 原子导出。导出失败从原收据重建。`cli._optimization_pointer`（2129）已校验 4,096 字节上限、字段和类型，文件名、空白及键序不参与身份。正式登记、直接 store 调用和重连均使用指定隔离库，经 `connect_managed`（135）、本地 DSN 校验、`binding.bound/verify_connection` 验证实例与应用角色。沿用 #118 验证后设置的受管根标记，保存脱敏实例证明和迁移版本。

## 普通运行入口

| 阶段 | 现行代码与最小变更 |
| --- | --- |
| 请求、验证 | `cli._cmd_optimize_experiment/_registered_optimization_manifest`（2218/2150）及 `store_psycopg._managed_optimizer_request/_optimize_request`（3135/2125）现先按族取旧协议。保留非法族、种子、截止组合和无效 attempt 的提前拒绝，再按验证过的清单字节对族与日程作有限分派。旧协议沿用 `historical_protocol`。v2 在同一 `optimization_read_transaction`（2776）核对固定登记行、清单、材料、计划、两源及缺件角色，复算登记上下文的计划/元数据摘要，核对实际代码、协议与预算。接入 `_verify_optimizer_inputs/_verify_optimizer_source_roles`（2174/2076）。公共 store 收到的 manifest、refs 与 schedule 只作待比对声明。v2 强请求身份加入真实登记事件键/ID、材料及计划引用。科学键继续用 `optimization_search_descriptor` 和 `store.optimization_run_key`（1270），显式操作键仅核对 `_optimize_operation_key`。 |
| 认领、计算 | `run_managed_optimization`（3191）→ `_execute_optimizer_operation`（3045）→ `_optimizer_operation_lock`（2833，科学键 session lock）→ `_optimization_operation`（2316）→ `_optimizer_claim`（2879）。首次认领在注册表写锁内比较 `_optimization_registry_snapshot` 的两目标现役/基线及拟合上下文与登记材料，再写唯一根 `STARTED`。`_optimization_policy` 从根取实际禁用、冻结和策略历史。新认领者在本次调用内取得私有已验计划，由 store 选定内部评分函数。现有公共 `compute=` 回调仅服务旧路径。`runner.historical_run_arguments`（工作树 5388）及 `execute_optimization_run`（6217）背后的共用实现接入已验双季合约、同季折、计划及回调。公开传日程名、字典、`NBAODRPlan` 或 `max_trials=4811` 均不能获此额度。 |
| 额度、恢复 | 仅此绑定使用 4,811。首次预测前核对完整计划字节、283 日 × 17 个规格和每项有序双目标，用 `run_scoped_trials/SearchBudget` 执法。`optimization_command_clock`（1334）从入口计时，计算严格少于 3,540 秒，整命令严格少于 3,600 秒。其余受管日程及公共执行上限 2,000。旧 `_optimizer_claim` 会复用已存在的 STARTED，`_confirm_optimizer_commit`（3095）也能再次进入 compute。v2 仅新建根认领者可启动一次。原命令丢失认领提交回包，可在原时钟内另连取 run lock，证明是自身认领且从未进入计算后继续。已有完整内存发布材料则只补发原发布。另一调用遇到终态只重放。遇到悬空根或仅有尝试失败、实际消耗未知，读回原事件并报告中断。换 attempt 不会再给计算额度。 |
| dry-run | `preview_managed_optimization`（3156）在同一 REPEATABLE READ READ ONLY 视图核验登记。SQL 只投影 run_key、config_digest、原 STARTED 链接、终态 schema/ID 及运行 ID 等状态字段，区分未运行、进行中、封存成功/失败及尝试失败。返回登记/运行标识、状态及计划计数。`_optimization_events` 取完整 evidence，`_optimization_run_row`（2303）取 scorecard，`_optimization_terminal`（2393）取工件，`_optimization_registry_snapshot` 经 `_optimization_rejections` 也读旧成绩。v2 预览用登记时的无评分上下文和专用标量投影。计数器须证明工件/成绩读取、模型/留出回调和增行均为零。旧预览输出及退出码按现有契约。 |
| 发布、重放 | `_validate_optimization_result`（2538）及 `_optimization_budget`（2697）从登记身份核对有序计划、各试验前缀和预算。登记全量恒为 4,811/9,622。`shared_target_uses_planned` 仅计额外共享次数。成功需匹配完整计划。失败保留首因、实际已启动、完成/空/中止及未执行数，含计划构造前的真实截止。`_optimizer_publish`（2940）在原事务内写工件、运行、适用既有处置与终态。正式重放和不明发布提交的另一连接确认重验登记，经 `_optimization_operation/_optimization_terminal` 返回原运行、事件、工件字节和退出码，含已封存失败。 |

## 共享文件交接与实施验收

1. 新文件切片可先行。取得纯侧写权后，一位实施者接 `optimize/runner.py` 的内部有界执行分支及其原单测，复用已实现的搜索与归档接口。4,811 次分支仅接收由 store 新建受管认领时传入的进程内上下文。
2. MC-3 先让 FX-38/39 第一步联合节点实际合入，随后才刷新 #118，完成其受管连接/事务与原生分区合并。按文件交接，先接 `store_psycopg.py` 的受管校验与协调，再从 `cli._build_parser`（2429，optimize 子命令在 2674）、`_cmd_optimize`（2311）与 `_cmd_optimize_experiment` 接登记及通常的 `optimize run`。在现有 `tests/test_papersim_optimize_registered_runner_native.py` 与 `tests/test_papersim_cli_optimize.py` 加集成断言。复用 store、快照、迁移和工作流。O-5 原生测试、FX-42 归档/索引和协调者台账留给各自持有人。独立新模块不等 FX-42 归档。
3. 用隔离合成身份从 parser 到原生事务验证并发重入/冲突回滚、丢失提交回包、仅有清单、换源/比较方、直接 pure/store 冒领、预览零成绩读取、成功/失败重放及中断后累计物理启动数。测试 4,810、4,811、4,812 项和等长错项/错掩码，并回归旧协议。沿用已审六变体同规模合成计时。正式次序为支持版本与时钟就绪、登记提交、第二连接独立读回、元数据预览、同一请求运行一次。正式登记、计时实证和留出成绩各待实际回执。
