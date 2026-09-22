# RV05 ok-compute 接线设计 — WIRING_PLAN v1 (本波不改代码)

- 任务: PAL_RV05_CAPACITY_20260921 · N0
- 依据: RECEIPT_CONTRACT (`pal-soak-subinput-receipt-v1`) + soak_driver.py /
  soak_audit.py @ 2584f6f2 (tree 702d25b5) 现行实现逐点分析
- 状态: DESIGN_FROZEN_FOR_REVIEW; 所有改动属后续波次, 由 N0 在其独占文件上实施;
  本波未改任何源码

---

## 1. 现行实现位置与数据流 (基线 2584f6f2 实测)

### 1.1 ok-compute 场景定义

- 场景不是仓库文件, 而是 manifest 内联 `code` (assets\manifest-rv05-11.json 条目
  `ok-compute`, kind=process): 读 stdin JSON → 2 线程 × 1500 次
  `sha256(b'ok-compute:%d:%d' % (sub_seed, i))` 折叠 → 输出
  `{'echo_round','echo_seed','inputs':3000,'threads':2,'digest'}`。
- 现状问题 (计划 §4 点名): "随机数→hash" 型负载; `digest` 由驱动/审计**均不重算**;
  `inputs:3000` 只是声明 (审计只累进 `receipt_declared_subinputs_sum`)。

### 1.2 驱动侧数据流 (tests/support/soak_driver.py, 行号为 2584f6f2 处)

| 步骤 | 位置 | 现行为 |
| --- | --- | --- |
| S1 选场景 | `_run_round` L890-891 | `sub_seed = derive_sub_seed(master_seed, round_no)`; `scenario = scenarios[sub_seed % len]` |
| S2 构造 payload | L896-899 | `_canonical({'round','segment','sub_seed','scenario','tmp_dir'})+'\n'` → `input_sha256` (整轮一个) |
| S3 起子进程 | `_scenario_spec` L831-857 + `run_research_process` L910 | process 场景: `argv=(python,'-I','-S','-c',code)`, cwd=round_tmp, env 仅 SystemRoot; stdin=payload |
| S4 回执校验 | `_validate_receipt` L859-885 | process 分支仅查 `echo_round==round_no` 且 `echo_seed==sub_seed` |
| S5 落 round.json | L936-955 | `input_sha256`(整轮 payload 哈希)、`receipt`(stdout JSON)、final/reason |
| S6 去重 | L957 `self._distinct_inputs.add(input_sha)` | 每轮 1 个键; 74 轮上限 74 — **100k 缺口的根因** |
| S7 清理 | `_cleanup_passed_tmp` L963 | passed 轮删除 round_tmp |
| S8 汇总 | `_progress` L813 / `_close_segment` L1066 | 报 `distinct_inputs`(legacy 语义) |

### 1.3 审计侧消费点 (tests/support/soak_audit.py)

- 轮记录收集: L243-311 (round.json 遍历, `receipt['inputs']` 累加 L284-287)。
- payload 复算: `payload_hash_matches` L115-131 — 规范 payload 五字段重哈希 (新方案
  **不改 payload 字节**, 此检查继续成立)。
- `verified_keys` (scenario,sub_seed): L358-363 — 每轮至多一键。
- 门: `min_distinct_inputs` L392-406 (R1: 声明不算证明)。
- 读取上限: `drv._read_json` 复用驱动 RECORD/MANIFEST/CONFIG/JUNIT 上限; 无回执
  文件类。

---

## 2. 目标数据流 (合同接线后)

```
_run_round(S1/S2 不变: payload 字节保持五字段, 旧审计 payload 复算兼容)
  └─ S3' kind='subinput': argv=(python,'-S','-m',module); env=pytest 式
       (SystemRoot + PYTHONPATH=<repo_root> + PYTHONUTF8=1 +
        PYTHONDONTWRITEBYTECODE=1); cwd=round_tmp; stdin=payload(不变)
       子进程 = N1 模块 tests/support/soak_okcompute.py (名称 N1 可改, 见 §4)
       子进程职责: 按 (round_no → index_origin=(round_no-1)*planned_rows) 推导
       输入 → 真实调用项目入口(合同 F1/F2/F3) → 生产 oracle → 写
       round_tmp/subinputs-<family>.json (≤512KiB 实测) → stdout 只回
       紧凑摘要 JSON (legacy echo_round/echo_seed + families 摘要)
  └─ S4' `_validate_receipt` 不改 (摘要仍含 echo_round/echo_seed, 原样通过);
       新增 subinput 专属校验 `_validate_subinput_receipts`:
       524288 上限+探针读 tmp 下回执 → 结构校验(合同 2.2/2.3/7 轻量子集:
       schema/必填集/hex64/行连续/counts 不变量) → stdout 摘要 sha256 对照
  └─ S5' 提升(promotion): 校验通过后、S7 清理前, os.replace
       round_tmp/subinputs-<family>.json → round_dir/subinputs-<family>.json
       (失败 ⇒ EvidenceWriteFailure); round.json 新增字段 (旧字段不动):
       `subinput_receipts`=[{family,entry,file,rows,sha256}...],
       `subinput_counts`=合同五计数聚合
  └─ S6' 驱动不改 legacy `_distinct_inputs`; 汇总新增 `subinput_rows_completed`
       (声明值, 不叫 distinct_qualified — 该名审计独占)
  └─ S7' 清理照旧 (回执已提升出 tmp, 不受 rmtree 影响)
```

失败词表 (新 reason, final='failed'): `receipt_missing` / `receipt_invalid` /
`receipt_over_limit` / `receipt_sha_mismatch` / `counts_inconsistent`;
子进程自身失败沿用现行 ResearchProcessError 映射, 不改。

## 3. N0 拥有的改动点清单 (后续波次; 均在 N0 独占文件)

| # | 文件 | 改动 | 波次 |
| --- | --- | --- | --- |
| W1 | tests/support/soak_driver.py `_load_manifest` L316-357 | kind 枚举加 `'subinput'`; 校验 `module`(导入名)、`planned_rows`(1..4096)、`families`(白名单子集) | N1 模块可用后 |
| W2 | 同上 `_scenario_spec` L831-857 | subinput 分支: `-S -m <module>` + pytest 式 env (不用 `-I`: 需 PYTHONPATH) | 同上 |
| W3 | 同上 `_run_round` L887-963 | 插入 S4' 校验 + S5' 提升 + round.json 新字段 + 失败词 | 同上 |
| W4 | 同上 `_progress`/`_close_segment`/`inspect_campaign` | 新增只读计数字段 (不改 legacy `distinct_inputs`) | 同上 |
| W5 | 最终 manifest (N0 独占) | ok-compute 条目改 kind=subinput + module/planned_rows=2048/families; 场景身份哈希随 manifest 自然更新; N3 的 13→11+2 映射不受影响 (名称不变) | N1/N3 就绪后 |
| W6 | 配置模板 | `minimum_completed_distinct_inputs: 100000` 等正式参数 + planned_rows | 与 W5 同波 |
| W7 | tests/test_soak_driver.py | 接线单测: 摘要/提升/失败词/上限/清理保护 | 随 W1-W4 |

原则: payload 五字段与字节**不变** (S2), `_validate_receipt` **不改** (S4),
`_distinct_inputs` legacy 语义**不改** (S6) — 三处保持使旧审计对旧轮的复算与
语义完全兼容; 新证据全部走新文件与新字段名。

## 4. N1 生成器模块 — 建议文件名与 import 面 (N1 独占, 名称可自定后报板)

- 建议文件: `tests/support/soak_okcompute.py` (tests 与 tests/support 均有
  `__init__.py`, `-S -m` 导入面成立; PYTHONPATH=repo_root 时
  `import tests.support.soak_okcompute` 可用)。
- `__main__` 入口: `main()` 读 stdin payload JSON (round/sub_seed/...) → 逐族
  `run_family(family, index_origin, planned_rows_for_family)` → 写回执文件到
  cwd(round_tmp) → stdout 打印摘要 JSON:
  `{'echo_round':R,'echo_seed':S,'subinput_families':[{family,entry,file,rows,
  sha256,counts:{planned,generated,attempted,completed,oracle_passed}}],
  'receipt_bytes_total':N}`。
- 可测纯函数面 (N1 单测导入): `derive_input(family, index)`, `normalized_input_bytes
  (family, index)`, `target_call(family, index)`, `producer_oracle(family, index,
  actual)`, `serialize_receipt(doc)`, `run_family(..., recorder=None)`。
- 依赖面: 仅 stdlib + `src/polymarket_alpha_lab/*`(合同三族入口) +
  `tests.test_research_capture_codec.make_run` (F1 构造器复用); 禁改
  soak_audit.py / soak_driver.py; 禁网络/DB/凭据。

## 5. N2 审计消费点 (N2 独占 tests/support/soak_audit.py + 审计单测)

- 读取: `round_dir/subinputs-<family>.json` — 新专用上限 524288+1 探针 (驱动
  `_read_capped` 同型), 在解析**之前**执行 (计划 §4: 先限额再解析)。
- 校验链: 决定 1(schema 精确匹配)/2.2/2.3(形状、hex64、连续、counts)/3(白名单
  独立重算: N2 自实现三族推导与期望规则, 禁 import 生成器)/7(未知字段拒绝+旧记录
  忽略)/8(去重与两类重复处置)。
- 输出: `subinputs` 报告块 + `min_distinct_qualified_subinputs` 新门; 旧
  `min_distinct_inputs` 与旧指标并存 (legacy 语义冻结)。
- 与 closeout (N5) 的接面: soak_closeout 读审计 JSON 时透传 `subinputs` 块与
  `distinct_qualified`, 不重复实现; 新旧 schema/哈希漂移/重复发射拒绝测试由 N5 在
  其副本先行, 最终以 N2 输出为准。

## 6. 时序与依赖

- 本波 (已做): 合同 + 接线设计 + 任务板; N1/N2 由 BLOCKED_ON_CONTRACT → READY。
- 波次 2: N1 生成器 + 单测 ‖ N2 审计扩展 + 单测 ‖ N3 映射 ‖ N4 诊断 ‖ N5 拒绝测试。
- 波次 3 (N0): W1-W4 接线 + W7; 有限容量试验 (真实展开 100k 容量, 不计正式时长)。
- 波次 4 (N0): W5/W6 最终 manifest/config → N5 绑定 → 20 分钟预演 → 门禁 → 冻结。
- 阻塞关系: W1-W4 仅依赖 N1 模块的导入名与摘要 JSON 形状 (§4 已定), N2 仅依赖
  合同; 两侧无需互相等待。

## 7. 保护与边界 (本设计全程遵守)

- 不改生产 src/SQL/依赖锁/公开错误合同; 不删门禁不提时限; 1MiB stdout / 2MiB 轮
  记录上限不放宽。
- 子进程一次一个 (驱动本身串行; 生成器内部线程与否由 N1 决定, 受 scenario_timeout
  与资源预算约束)。
- 原 soak / PID 62420/20372/98500 / V3 冻结目录: 未接触。
- official_cases_run=0; sandbox_started=false; activation_authorized=false。
