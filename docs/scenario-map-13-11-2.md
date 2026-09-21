# SCENARIO_MAP_13_11_2 — 原 13 场景 → 11 名义 + 2 故障合同 逐项映射

- 任务: PAL_RV05_CAPACITY_20260921 / 节点 N3(场景与故障合同)
- 日期: 2026-09-21(UTC)
- 开发基线(冻结): commit `2584f6f2d86ce19e7e2dab6bea6a27a013587753`, tree `702d25b5aaabbd790bd48a0ebed8d1a79f12c24d`
- 本文档副本: WorkRoot `n3\SCENARIO_MAP_13_11_2.md` 与分支内 `docs/scenario-map-13-11-2.md`(同一内容)
- 标注约定: 【实测】= 本节点亲自只读读取/运行得到;【引用】= 引用 SETUP/V3/协调者既有记录, 本节点未重算

## 0. 结论一览

| # | 场景名 | 类型 | 去向 |
| --- | --- | --- | --- |
| 1 | pytest-protocol | pytest | 11 名义(逐字保留) |
| 2 | pytest-audit | pytest | 11 名义(逐字保留) |
| 3 | pytest-process | pytest | 11 名义(逐字保留) |
| 4 | pytest-paper | pytest | 11 名义(逐字保留) |
| 5 | pytest-lt01-driver | pytest | 11 名义(逐字保留) |
| 6 | pytest-lt02-new | pytest | 11 名义(逐字保留) |
| 7 | pytest-lt03-new | pytest | 11 名义(逐字保留) |
| 8 | ok-echo | process | 11 名义(逐字保留) |
| 9 | ok-compute | process | 11 名义(逐字保留) |
| 10 | ok-unicode | process | 11 名义(逐字保留) |
| 11 | ok-sleep-2s | process | 11 名义(逐字保留) |
| 12 | fail-nonzero | process | **2 故障合同之一**(退出名义轮转, 保留源码/历史/测试) |
| 13 | fail-output-limit | process | **2 故障合同之一**(退出名义轮转, 保留源码/历史/测试) |

13 = 11 + 2, 无遗漏、无新增、无删除后冒充。两个设计性失败保留源码、历史失败记录与
测试覆盖; 不把原 failed 改 passed; 不宣称原 13 场景全部通过; 新长测结论只覆盖 11 场景配置。

## 1. 输入与核验方式

| 材料 | 位置 | 核验 |
| --- | --- | --- |
| 已批准 11 场景清单 | `WorkRoot\assets\manifest-rv05-11.json` (3575B, SHA256 `0555dbd12f2f558d626a83578c8ddeec06fec3bb895ec58513066ea744ec94e0`) | 【实测】与 `V3\launch-rv05\manifest-rv05.json` 字节一致(同哈希, 只读比对) |
| 原 13 场景 manifest 身份 | 原 campaign `campaign.json`: `manifest_sha256=df6a20809fb109720a22cfcd7b7853542912b0aa8cedf15e5d9815b7d1bd1d69`, candidate `d929cc35...`(label lt04-formal-72h) | 【实测】只读读取 |
| 原 13 场景逐名历史 | 原 campaign `campaign\driver.log`(读取时点快照) + `segments\...\rounds\round-*/round.json` + `failures\rep-*.json` | 【实测】只读聚合, 见 §4 |
| 13→11 派生说明 | `launch-rv05\README-rv05.md` / `launch-spec-rv05.json`("original formal 13-scenario manifest minus the two designed-failure process scenarios, verbatim names/codes/files") | 【引用】 |
| 场景执行/故障映射代码 | 冻结树 `tests/support/soak_driver.py`、`src/polymarket_alpha_lab/research_process.py` | 【实测】本节点分支内读取 |

**诚实声明(原始内联代码不可逐字节核验)**: 原 13 场景 manifest 文件本体(sha
`df6a2080...`)在可访问只读位置(V3 工作区、原 campaign 目录、evidence 目录; 按内容
`fail-output-limit` 全文扫描与按哈希扫描)未找到副本。因此两个故障场景的**原始内联
代码无法逐字节引用**; 本文档与其测试核验的是: 名称、类型(process)、以及原 campaign
轮记录中 100% 稳定的行为形态(reason、无 stderr、无回执、耗时量级), 并以 N3 规范化
重建形态写入故障合同测试(见 §3), **不声称重建代码逐字节等于原码**。11 个保留场景
"verbatim names/codes/files" 的说法引自 launch-rv05 材料(其 2026-09-21 预验证曾逐项跑绿,
见 `evidence/launch-rv05/scenario-prevalidate-summary.json`【引用】)。

## 2. 保留的 11 个名义场景(逐项)

场景选择公式(冻结代码【实测】): `_load_manifest` 按名称排序(`soak_driver.py:354`),
每轮 `sub_seed = SHA256("soak:{seed}:{round}")[:16]` 取整(`derive_sub_seed`, :110-113),
场景索引 = `sub_seed % len(scenarios)`(:891)。

### 2.1 pytest 组(kind=pytest, 7 组)

执行形态(【实测】`soak_driver.py:839-847`): 子进程 `python -S -m pytest -q -p
no:cacheprovider --junitxml <round_tmp>/pytest.xml <files>`, cwd=仓库根, PYTHONPATH=本
解释器 pinned purelib。预期语义: JUnit XML 可读且 `failures=0 and errors=0` →
`final=passed`(:870-885); 有 failures/errors → `failed/nonzero_exit`; XML 不可读/超读上限
→ `failed/receipt_invalid`。

| 场景 | 文件(manifest 逐字) | 代码摘要(做什么) |
| --- | --- | --- |
| pytest-protocol | tests/test_research_codex_exec.py, test_research_codex_profile.py, test_research_codex_profile_review.py, test_research_claude_exec.py, test_research_claude_profile.py, test_research_claude_review.py | 研究协议执行/档案/复核门(codex 与 claude 两族)的合同测试 |
| pytest-audit | tests/test_research_uncapped.py, test_research_uncapped_audit.py, test_research_uncapped_audit_review.py, test_research_dispatch_rotation.py | uncapped 研究路径审计与调度轮换测试 |
| pytest-process | tests/test_research_process.py, test_research_process_review.py, test_research_codex_process.py | 自有进程监督层(ResearchProcessSpec/run_research_process)的反例与复核测试 |
| pytest-paper | tests/test_research_paper.py, test_research_paper_review.py, test_research_paper_settlement.py, test_research_paper_settlement_review.py, test_research_resolution_confirmation.py, test_research_resolution_confirmation_review.py, test_research_scope_instants.py, test_research_record_timezone_codec.py | 纸面交易/结算/决议确认/范围快照/时区编解码(含 tzdata 敏感组) |
| pytest-lt01-driver | tests/test_soak_driver.py | soak 驱动自身反例测试(即本长测的驱动) |
| pytest-lt02-new | tests/test_research_exec_boundaries.py, tests/test_research_transport_stop_order.py | 执行边界与传输停止顺序测试 |
| pytest-lt03-new | tests/test_research_paper_settlement_decimal_oracle.py | 纸面结算 Decimal oracle 对照测试 |

原 campaign 中这 7 组的历史(【实测】driver.log 快照, 见 §4): 除 pytest-paper 与
pytest-lt03-new 两组曾在原运行中**非设计性**失败(tzdata/环境问题, 已在 11 清单
预验证重跑全绿: paper 431 passed / lt03 53 passed【引用 prevalidate 摘要】), 其余全部
通过。设计性失败只有 §3 的两个 fail-* 场景——两类失败严格区分。

### 2.2 process 组(kind=process, 4 个)

执行形态(【实测】`soak_driver.py:836-838`): 子进程 `python -I -S -c <code>`,
cwd=round tmp, stdin=规范化轮 payload; 预期语义: stdout 为合法 JSON 回执且
`echo_round==round`、`echo_seed==sub_seed` → `passed`; 否则 `failed/receipt_invalid`(:859-869)。

| 场景 | 代码摘要(据 11 清单内嵌代码, 【实测】读) | 预期语义 |
| --- | --- | --- |
| ok-echo | 读 stdin payload; 输出 `{echo_round, echo_seed, stdin_sha256, stdin_bytes}` | 输入→回执回显即通过; 验证管道与回执合同 |
| ok-compute | 读 payload; 2 线程各 1500 次 `sha256('ok-compute:{sub_seed}:{i}')` 折叠取模累加, 汇总再 sha256; 输出 `{echo_round, echo_seed, inputs:3000, threads:2, digest}` | 有界并行计算负载, 每轮声明 3000 子输入(RV05 计划口径 2048/轮为 N0/N1 回执改造目标, 冻结代码现值 3000) |
| ok-unicode | 构造 CJK/emoji/组合音标/RTL/几何形状样本, `json.dumps(ensure_ascii=False)` 编码再解码断言相等; 输出 `unicode_roundtrip:true, chars:...` | Unicode 全程无损往返 |
| ok-sleep-2s | `time.sleep(2)` 后输出 `{...,slept_seconds:2}` | 超时边界裕度探针(显著低于 scenario_timeout_ms) |

## 3. 两个故障合同场景(逐项)

公共机制(【实测】冻结代码): 唯一进程启动/终止路径是
`run_research_process`(`research_process.py:303`; Windows Job Object
KILL_ON_JOB_CLOSE)。`research_process_nonzero_exit`(:257, 子进程退出码≠0 且流已关闭)、
`research_process_output_limit`(:248-249, 单流读累计超过上限即拒, 每次读至多
cap+1 字节, 先拒后清理)。驱动映射(`soak_driver.py:920-927`): 错误文本尾缀
`nonzero_exit`/`output_limit` → `final='failed'` + 对应 reason; `_cleanup`(:263-300)
在 finally 中终止自有子进程并等待其退出。这些是设计性失败, 外层合同测试 PASS 的含义是
"故障被正确产生并正确处置", **不是**把失败改称通过。

### 3.1 fail-nonzero

- 名称/类型: fail-nonzero / process(【实测】原 campaign 轮记录; 【引用】launch-spec "designed-failure process scenarios")
- 预期语义: 子进程消费轮 payload 后以固定非零码退出, 不产出回执、不写 stderr →
  监督层 `research_process_nonzero_exit` → 轮终态 `failed/nonzero_exit`, 回执 null。
- 原历史(【实测】只读快照): 10 轮, 10/10 `final=failed reason=nonzero_exit`,
  `stderr_bytes=0`, `receipt=null`, elapsed_ms 46/132/358(min/median/max); 首个观测轮
  round 28, 末个观测轮 round 134。`failures/rep-033cd0bd17.json`(round 28)为其实测
  复现实录。
- N3 合同形态(`tests/test_soak_fault_contracts.py::FAIL_NONZERO`): 子进程持
  `child.lock` 排他锁(存活证明)、向 `child.marker` 写入固定形态串
  `fail-nonzero:designed-exit-code=3`、`sys.exit(3)`、无 stdout 回执、无 stderr——与全部
  10 条历史记录的行为形态一致(固定错误形态、静默、亚秒)。
- 四要素断言(测试 `test_fail_nonzero_fault_contract_four_elements`):
  - (a) 预期底层错误: 监督层直接断言 `str(err)=='research_process_nonzero_exit'`(固定
    码, 无路径/argv/环境泄漏); 驱动轮 `reason=='nonzero_exit'`、`stderr_bytes==0`、
    `receipt is None`; marker 内容证明到达的是设计形态。
  - (b) 终态正确: `status=='final' and final=='failed'`、记录无 `expected` 字段、
    `totals['passed']==0`、`totals['failed']==1`——失败以失败记账, 永不改标签。
  - (c) 资源受限: `elapsed_ms < scenario_timeout_ms`(远在超时边界内); round.log 体积
    ≤ per_round_log_bytes(有界证据)。
  - (d) 自有进程已清理: 轮结束后对 round tmp 的 `child.lock` 可立即排他加锁(进程已死
    才可能), `driver._in_flight is False`; 监督层直接运行后同样锁证明。

### 3.2 fail-output-limit

- 名称/类型: fail-output-limit / process(同上来源)。
- 预期语义: 子进程向 stdout 洪写超过 `max_stdout_bytes`(生产默认 1 MiB = 1048576,
  本合同不放宽) → 监督层在 cap+1 读处拒绝 `research_process_output_limit`(先拒、
  绝不截断成成功), 随即终止该子进程 → 轮终态 `failed/output_limit`, 回执 null。
- 原历史(【实测】只读快照): 8 轮, 8/8 `final=failed reason=output_limit`,
  `receipt=null`, elapsed_ms 61/101/328(min/median/max); 首轮 round 21,
  `failures/rep-3d6187e04a.json`(round 21)为其实测复现实录。
- N3 合同形态(`FAIL_OUTPUT_LIMIT`): 子进程持锁、写 marker
  `fail-output-limit:designed-stdout-flood`、然后 64KiB 块循环洪写 stdout(安全阀
  8 MiB 远在 cap 之后), 自身不主动退出——监督层必须在 cap 处提前拒绝并击杀。
- 四要素断言(测试 `test_fail_output_limit_fault_contract_four_elements`):
  - (a) 预期底层错误: 监督层 `str(err)=='research_process_output_limit'`(拒绝而非截断
    返回); 驱动轮 `reason=='output_limit'`、`receipt is None`。
  - (b) 终态正确: `final=='failed'` + reason 精确匹配、无 `expected` 字段、passed 计数 0。
  - (c) 资源受限: `reason != 'timeout'` 且 `elapsed_ms < scenario_timeout_ms`——触发的是
    输出上限而非计时器(洪写子进程若不被 cap 拦截将持续到超时); round.log 只含截断头,
    体积 ≤ per_round_log_bytes, 洪写内容从不落盘。
  - (d) 自有进程已清理: 被击杀的洪写子进程的 `child.lock` 立即可加锁(必死才可能),
    marker 保全于被保留的失败轮 tmp 中。

### 3.3 边界与轮转对照测试(辅助)

- `test_output_cap_boundary_is_exact_at_supervision_layer`: cap=4096 时, 恰 4096 字节
  被完整接受(`len(stdout)==4096`), 4097 字节被拒——上限精确、无近似误伤(要素 (c) 边界)。
- `test_nominal_passes_while_both_faults_fail_in_shared_rotation`: ok-echo 与两个故障
  场景同清单轮转, 名义轮 passed、两个故障轮各自 failed+对应 reason、失败轮 tmp 保留/
  通过轮 tmp 清理——11+2 分区语义在同一旋转下成立, 无任何设计失败被计入通过轮。

## 4. 原 campaign 只读历史快照(区分设计失败与非设计失败)

【实测】原 campaign `driver.log` 读取时点聚合(round_final 共 149 轮快照):

| 场景 | 轮数 | 终态分布 |
| --- | --- | --- |
| pytest-process | 19 | 19 passed |
| pytest-lt02-new | 16 | 16 passed |
| pytest-protocol | 16 | 16 passed |
| ok-unicode | 14 | 14 passed |
| ok-compute | 12 | 12 passed |
| ok-echo | 12 | 12 passed |
| pytest-audit | 8 | 8 passed |
| ok-sleep-2s | 8 | 8 passed |
| pytest-lt01-driver | 7 | 7 passed |
| **pytest-paper** | 9 | **9 failed/nonzero_exit(非设计性: tzdata/环境缺陷)** |
| **pytest-lt03-new** | 10 | **10 failed/nonzero_exit(非设计性: 同上)** |
| **fail-nonzero** | 10 | **10 failed/nonzero_exit(设计性)** |
| **fail-output-limit** | 8 | **8 failed/output_limit(设计性)** |

- 两个 fail-* 场景合计 18 轮, reason 100% 稳定于各自设计值——历史与合同一致。
- pytest-paper / pytest-lt03-new 的失败是**非设计性**的(环境缺陷), 与设计失败严格区分;
  该缺陷已在 11 清单预验证重跑全绿(paper 431p / lt03 53p【引用】), 但原历史记录保持
  failed 不回填、不改写。
- 快照为读取时点值; 原 campaign 只读, 未触碰、未重启、未修改任何记录。

轮转容量口径(均【引用】, 非执行证明):
- 原 13 场景、seed 2026092103、前 864 轮计划分布含 fail-nonzero 69 + fail-output-limit
  79 = 148 个设计失败轮(V3 inputs-analysis 精确模拟)——设计失败轮永不计入 ≥864 有效
  passed 轮门, 故修正版名义清单必须移除二者。
- 11 场景清单、同 seed 前 864 轮: 全部 864 轮为可 passed 轮; ok-compute 74 轮 × 计划
  2048 子输入/轮 = 151552 槽位 ≥ 100000(ARITHMETIC_ONLY; SETUP 实跑 capacity_plan 所得,
  见 RV05_STATE §3)。

## 5. 红线与保护声明(N3 执行情况)

1. 不写最终 manifest(由 N0 唯一写入); 不改驱动/审计/生成器(N3 分支零改动
   `tests/support/*` 与 `src/*`)。
2. 不删两个故障场景的源码形态: 原始行为在原 campaign 记录中永久保全(只读);
   设计形态在 `tests/test_soak_fault_contracts.py` 中以可执行源码+断言留存。
3. 不把旧 failed 改 passed / 改名 expected: 新测试全部为**新运行**; 原记录零接触。
4. 不宣称原 13 场景全部绿: 11 名义 + 2 故障合同分开陈述; 新长测结论只覆盖 11 场景配置。
5. 两个故障场景的轮数不贡献 864 轮门或 100k 输入门。
6. 净化环境真实子进程(env -i + SystemRoot + 自有 TEMP/TMP/HOME; `-I -S`/`-S` 子进程;
   所有子进程为 N3 自建自有; 一次一个子进程); 未运行真实模型/官方 CLI/凭据/行情/订单/
   业务库/原生 DB; 未装依赖; 未推送; 常量 official_cases_run=0 / sandbox_started=false /
   activation_authorized=false。

## 6. 证据文件清单(WorkRoot `evidence\n3\`)

- `fault-contracts-run.txt` — 正式证据运行全文(4 passed in 4.55s, exit 0, 逐用例 PASSED)
- `focused-regression-run.txt` — 聚焦回归(test_soak_driver.py 40 + 新 4, 44 passed in 38.83s, exit 0)
- `fault-contracts-summary.json` — 机器可读汇总(含四要素逐项 PASS 状态、环境、首败台账指针)
- `first-failure-dev-run1.txt` — 首败保留(开发运行 1: msvcrt 字节锁导致持锁读同文件
  PermissionError; 修复=锁/内容分离; 重跑全绿)

分支提交: 见 `lanes\n3-state.md` 与检查点 `checkpoints\n3\`(SHA 记录于此, 不推送)。
