# D1 普通预检同实例读回补遗（待原计划审复核）

基线 `2d6dbb199f836cc57d3dc45b6191c5d41d29ee75`。已审 `d1-frozen-execution-platform-plan-astra-v2.md` 的 SHA-256 为 `be238ef5c0e42eb51574b492f93c6d6bd39d8ed7f97dca6953f4858ffe8db2a0`，原计划审 `d1_operating_plan_review_sol_retry` 的 PASS 见 `d1-frozen-platform-v2-original-verdict.json`。原代码审 `d1_preflight_independent_code_review_sol` 在 `d1-preflight-2d6dbb19-original-review.json` 留下四项阻断和四项证据缺口。本补遗只申请跨两位原写者的读接口衔接。协调者先送原计划审只读复核，取得明确 PASS 后实施。

## 改动归属

| 单写者 | 涉及文件与边界 |
| --- | --- |
| 原 A1 公开来源写者，或由协调者亲自独占 | 在已发布的 `d1compare/acquire.py::read_capture` 增加私有、仅关键字参数 `_connection=None`，就近在 `tests/test_papersim_d1compare_journal.py` 增补连接借用及旧入口断言。协调者在派工时选定一人，交接结束后另一人才可编辑。范围仅限读回及其相邻断言。 |
| 原冻结侧写者 | 继续独占 `d1compare/frozenrun.py`、`d1compare/frozenexec.py`、`tests/test_papersim_d1compare_frozenrun.py`。贯通 `plan_chain → read_staged_archives` 和预检父连接，在原测试处补原生验收并修正模块头的数据库适用范围。表中 `d1compare/` 位于 `src/polymarket_alpha_lab/papersim/`。 |
| 协调者 | 合入两端后验普通 CLI，整理同节点 FX-28 计划出处和记录。应用改动限于上述现成模块，使用既有受管 PG、快照和命令事件。产品连接模块、迁移、新 CLI 参数、生产写入及演示均不在本次范围。 |

## 一次调用的连接、快照与回执

1. 基线 `frozenexec.preflight` 先在父连接取实例，再在同一事务内等会话锁并查历史。`plan_chain` 经 `read_staged_archives` 重开项目根，`acquire.read_capture` 又重开一次。改为入口固定项目根的物理路径和既有实例描述中的 `instance_id/root_sha256/system_identifier`，以 `Layout(物理根).root_hash` 核根。用既有 `store_psycopg.connect_managed` 建立并持有唯一实际应用连接。对照固定三元组、预期数据库及应用角色核验数据库实际身份，沿用 `binding.bound(...)` 内的 `verify_connection`。脱离该上下文单独调用校验会直接返回。普通预检的所有数据库读写沿用这一个后端会话。
2. 保留按现有 binding 生成的操作键和会话级锁。允许在事务内读身份并等待锁，得锁后**先结束该事务**，会话继续持锁。由同一会话在新事务的第一个历史查询重读 `_existing`，再按历史决定 STARTED 或原件复核。这样即使会话默认为 REPEATABLE READ，等待者也能看到持锁者刚提交的终态。会话锁跨越 STARTED、原件读取、PREPARED、终态和复用判断，在所有出口由父端释放。
3. 原件读取使用父端**独立开启**的一笔 `REPEATABLE READ READ ONLY` 事务，设置在其第一条数据查询前。固定根、同一 `connection` 传至 `plan_chain(..., _connection=connection) → read_staged_archives(..., _connection=connection) → _read_registered_staging`，再传至 `read_capture(..., _connection=connection) → _load_batch/_read_entries`。在这笔事务内核归档清单、两腿原 tar、对应镜像脚本、批次清单、各响应字节及 `_capture_event` 的成功事件和 scope。镜像按既有清单与实际脚本字节重验，继续使用私有暂存租约。父端在读回事务结束后分事务封存已有的分母/PREPARED、回执/终态。
4. 借用分支先查连接状态，要求调用方已经开启有效的一致只读事务，再在该事务内核实际隔离级别、只读设置和固定根对应的实例身份。拒绝闲置、已失败、已关闭、弱隔离、错根或错实例连接，拒绝后不得自动另连。适当的内部 savepoint 可承接读错，外层提交、回滚、关闭、隔离级别和会话设置均由持有人控制。已有 `snapshots.read_snapshot` 的嵌套事务在此外层内只形成 savepoint。默认省略新参数的 `read_capture(repo_root=…, batch_sha256=…)`、归档读回、`plan_chain` 和既有调用者沿用原建连及校验，保留 v1/v2 结果与错误语义。
5. 固定根贯穿全部子读。入件验证及返回原回执前，复核调用路径仍指向同一物理根，相应受管描述仍绑定已持会话。同根的稳定别名可用。别名改指另一根、描述与连接错配或数据库身份变化时，当前调用给出脱敏拒绝，绝不从另一实例补齐。即便在文件检查间隙改配置，已持连接也只能读原实例。终态重用先在当前镜像和同实例快照重验，再比原分母 SHA。成功复用保留首写 attempt、终态键、时刻、分母、回执 SHA 和全部原行。当前镜像失效或重验不符时非零退出，不宣称本次已重验，不改旧终态或另开同键成功记录。未完成尝试仍按 v2 的既有恢复合同处理。

## 四项修复与原生验收

| 项目 | 最小可判定结果 |
| --- | --- |
| 原审四项 | ① Gamma 的数字 line/handicap 用可序列化、保精度的类型投影，CLOB 畸形数字 market/asset_id 保留类型错误和证据指针，原字节、位置和 SHA 可回查，分母能落库。② 已终结同键改坏或移走镜像时按上节第 5 项拒绝。③ 对照 phase、capability、missing、读回、PREPARED、输出和错误码核历史。当前 `unproved` 或仍有必需缺项的 SUCCEEDED/ready 须拒绝，CLI 非零。④ 对照得锁前后两笔事务的真实竞争结果。 |
| 两实例与路径 | 复用 `_isolated_project` 启动两套独立、已迁移的临时 A/B。先验 A 上实件可读，再构造 A 缺归档而 B 独有该归档、A 缺批次或某一响应而 B 独有该项的反例。保留可相同的摘要及 A 本地采集回执，使摘要本身不能证明归属。通过进程/数据库屏障在父连接已取得后原子改指测试根别名，再单测描述更换及 A 根搭配 B 连接。独立读取 A、B 的事件与全部相关快照，确认当前拒绝、外来原件贡献为零、B 的预检新增为零。 |
| 借用事务与竞争 | 在 A 的既有一致只读事务内先取快照和 savepoint，独立会话随后提交一份新原件。借用读可见原先已提交的原件，对后提交的原件仍应报缺。核调用前后同一后端、相同快照和事务设置、原 savepoint 可用，调用方自己结束后以新快照可读。成功、缺件和错连接均应验证。另以两个真正 CLI 进程，在库端默认 REPEATABLE READ 下运行同键，让第一进程持锁尚未提交终态，用 PG 锁等待关系确认第二进程已阻塞后再放行。独立查询仅一组 STARTED/PREPARED/终态，二者返回同一首写凭据。 |
| 普通入口与故障 | 真 `stage --leg all → capture-inputs → run-frozen --preflight` 分进程运行，仅在公共传输边界给测试字节。独立进程检查原件、分母、回执与实际退出码，覆盖旧式读回、同键复用及普通执行五闸拒绝。提交前断开时直接按 kind/source 清点所有相关 snapshots 并比前后 SHA 集，查含无事件引用的孤行。提交后丢应答由新进程找回首写。报告实际首跑、重跑、计数、退出码，保留历史候选原件与首次 FAIL。全部测试只用一次性工程库，冻结脚本启动、正式 slot、业务行增量及计日均为零。 |

FX-28 由协调者在同一节点把实际采用的受审 v2 与获批补遗的脱敏文本登记到仓内文档（建议 `docs/paper-sim/d1-frozen-preflight-plan.md`），记录源稿、归档文本各自摘要、基线、两次计划审结论和文件归属，以仓内相对路径关联实现与测试。发布材料仅留脱敏结果。集成提交按根规则跑 focused CLI 汇总、Linux 全量、相关原生门禁、静态与 CodeGraph 检查，再由原代码审复核集成提交。此稿没有实施结论。
