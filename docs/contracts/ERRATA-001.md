# ERRATA-001 — `pal-soak-subinput-receipt-v1` 接口面澄清 (追加, 不改语义)

- 任务: PAL_RV05_CAPACITY_20260921 · N0 集成波 (wave 3) · 2026-09-21
- 裁决人: N0 (合同 owner), 依据 N1 Q1-Q6 (`lanes/n1-state.md`) 与 N2 五项澄清
  (`lanes/n2-state.md`), 以及 N1/N2 分支交付代码的逐行比对。
- 效力: 本文件是 RECEIPT_CONTRACT (FROZEN_DRAFT_1) 的**勘误/澄清附录**。
  基础合同副本 `docs/contracts/soak-subinput-receipt-v1.md` **字节不变**
  (sha256 `19416d3ad95de42ca9a686ce9e6d6e5b800846773b3c12ba2820fdc48bb64c74`
  仍为 `contract_sha256` 锚); 本勘误只对合同未规定/双方推导不一的点钉死唯一
  规则, 不改任何已定语义。合同状态由本勘误确认生效: **CONFIRMED (with
  ERRATA-001)**。
- 适用基线: 2584f6f2d86ce19e7e2dab6bea6a27a013587753 (tree 702d25b5)。

---

## A. N1 Q1-Q6 裁决

### Q1 — F2 对侧档位 (合同未规定)

**裁决**: 采用 N1/N2 已一致实现的**对称镜像阶梯**。设 `p0 = 100 + (index mod 50)`,
档 `j = 0..4`, `step_j = 0.25*(j+1)`, 档量 `q_j = bounded_decimal(((index + j*13) mod 37) + 1, positive=True)`:

- asks (卖单侧, 升序自 p0 上方) = `(p0 + step_j, q_j)` for j=0..4;
- bids (买单侧, 降序自 p0 下方) = `(p0 - step_j, q_j)` for j=0..4;
- 两侧同 j 同量; 消费侧 = buy 走 asks / sell 走 bids;
- 输入身份 descriptor 只列**消费侧** 5 档 (`levels`), 对侧不进 descriptor。

**理由**: 合同决定 3 只定义了消费侧; N1 实现与 N2 独立实现在此规则上逐字节
一致 (两侧同 j 量、符号翻转), 是唯一被双方共同实现的读法; 其余档位取值会
改变 midpoint/spread/`order_book_snapshot_sha256` 的期望值, 无第三方实现
支持。**两侧实现已一致, 无代码改动。**

### Q2 — entry id 精确白名单值 (N1 `simulate-fill` vs N2 `simulate`)

**裁决**: 注册表 (family, entry) 精确值为:

| family | entry | normalize_rule |
| --- | --- | --- |
| `capture-codec` | `capture-codec/encode` | `norm:capture-codec-v1` |
| `paper-decimal-fill` | `paper-decimal-fill/simulate-fill` | `norm:paper-decimal-fill-v1` |
| `uncapped-authz-codec` | `uncapped-authz-codec/payload` | `norm:uncapped-authz-v1` |

**理由**: 合同只定 `<family>/<verb>` 形状, verb 未枚举; 两侧在 F2 verb 上
推导不一 (`simulate-fill` vs `simulate`)。钉死 N1 的 `simulate-fill`:
(1) 自描述性更好, 与被测入口 `simulate_order_book_fill` 名义对应;
(2) N1 生成器模块字节已冻结, 其 sha256
(`fc99d226721f5c05b0fcdce1c26a5a841619b9c9673a1ee55e721bed40964958`)
是已登记的证据锚 (回执 `generator_sha256` 取值来源); 改 N1 侧会作废该锚。
**修正**: N2 侧对齐 (`soak_audit_subinputs.py` REGISTRY 键 + 测试 2 处),
记录于集成波, 属接口面字符串对齐, 无语义变化。

### Q3 — header 身份字段 argv 通道

**裁决**: payload 五字段保持冻结; `candidate` / `manifest_sha256` /
`contract_sha256` 经 **argv** 传入生成器子进程。驱动 (W2) 传参精确值:

```
<python> -S -m <module>
    --candidate <canonical JSON 串: json.dumps(config.candidate, sort_keys=True,
                separators=(',',':')) — UTF-8 文本, 与 N1 build_identity 校验同式>
    --manifest-sha256 <驱动 manifest 身份 sha256 (hex64)>
    --contract-sha256 <sha256(仓库规范副本 docs/contracts/soak-subinput-receipt-v1.md)>
    --rows <manifest planned_rows (1..4096)>
    --family <family>   (每个启用的族一个, 按 manifest 存储序 = 字典序)
```

- 驱动不使用 `--contract-path` (避免向子进程泄漏宿主路径); 生成器侧对
  `--contract-path` 与"仓库副本回退"的支持保留为其自身容错, 非驱动通道。
- 生成器退出码: 0=全过; 1=族中途失败 (stdout `ok=false`+`failure`);
  2=config/payload 错 (stdout JSON error)。驱动侧: 非零退出沿用现行映射
  (`nonzero_exit` 等); `ok!=true` 的摘要即使伴随退出码 0 也按
  `receipt_invalid` 拒绝 (fail-closed, 防说谎子进程)。
- **stdout 摘要形状 (钉死, 取代 WIRING_PLAN §4 单文件草案)**:

```
{"echo_round":R, "echo_seed":S, "echo_scenario":"ok-compute", "ok":true,
 "subinput_families":[
   {"family":F, "entry":"<F>/<verb>",
    "files":[{"file":"subinputs-<F>.json", "rows":N, "sha256":hex64, "bytes":B}],
    "rows":N, "counts":{planned,generated,attempted,oracle_passed,completed}}],
 "receipt_bytes_total":T,
 "failure":{family,stage,index,reason,detail}}   # 仅失败时
```

  `files` 为该族全部 part 文件的升序列表 (v1 正式 2048 行单文件 = 1 项);
  族级 `rows`/`counts` 必须等于该族各文件聚合。驱动 S4' 校验该形状
  (摘要层面未知键容忍)。

**理由**: payload 无法携带身份字段 (字段集冻结); argv 是唯一不污染 payload
字节与 legacy 复算的通道。N1 main() 已按此实现; 驱动 W2 补传参 (本波)。

### Q4 — part1 语义

**裁决**: 基名文件 `subinputs-<family>.json` 即 part 1, **不携带**
`part`/`part_count` 字段; 编号 part ≥ 2 写 `subinputs-<family>.pNN.json`
(NN 十进制零填充, 从 02 起), 携带 `part`/`part_count` 两字段 (缺一即拒)。
审计侧结构校验接受 `1 <= part <= part_count` 的字段对 (超集读取), 但**生产
侧规范**是: part 字段只出现在编号 part ≥ 2 上。

**理由**: 合同 2.4 "`subinputs-<family>.p02.json` 等" 的字面读法; N1 实现
即此; N2 校验器兼容。两侧一致, 无代码改动。

### Q5 — F3 `authorization_id` (N1 `authz-` vs N2 `rv05-authz-`)

**裁决**: `authorization_id = f"authz-{index:012d}"` (12 位零填充十进制
全局下标), 不入输入身份 descriptor (身份仍由决定 3 的六字段 descriptor
决定), 但进入序列化 payload, 因此参与 `actual_result_sha256`。

**理由**: 合同未规定该字段; 两侧推导不一将导致 F3 行大面积
`result_mismatch` (诚实失败但阻断集成)。钉死 N1 的 `authz-` 前缀, 理由
同 Q2 (生成器模块字节冻结, sha 锚不变); `rv05-authz:%d` 前缀已用于
`adapter_contract_sha256` 的盐, 属不同命名空间, 互不冲突。
**修正**: N2 侧对齐 (`soak_audit_subinputs.py` 1 处 + 测试 2 处), 记录于
集成波。

### Q6 — 多族行配比与域分配

**裁决**: `--rows` 为**轮总量**; 各族均分 `floor(rows/n)`, 余数
`rows mod n` 给**第一个** `--family` (驱动按 manifest 存储序=字典序传参,
故 = 字典序首个启用族); 任一族 <1 行 ⇒ `config_invalid`。每族独立编址:
`index_origin = (round-1) * rows_for_family`, 行对应
`[index_origin, index_origin+rows_for_family)`。v1 正式单族
(capture-codec, 2048 行/轮) 不受影响。

**理由**: N1 实现即此, N2 域复核按 index 重算与分配方式无关; 块划分由
回执 `index_origin` 声明并接受审计复核 (合同决定 4/8 原文)。无代码改动。

---

## B. N2 五项澄清裁决

1. **F3 authorization_id** — 同 Q5: 钉死 `authz-{index:012d}`; N2 侧修正。
2. **entry id verb** — 同 Q2: 钉死 `simulate-fill`; N2 侧修正。
3. **F2 阶梯读法 + 未填满分支不可达** — 对称 5+5 阶梯确认 (同 Q1)。
   另**知情确认**: 该常数族下订单量 `size = (index mod 400 + 1)/8 <= 50.125`,
   而消费侧 5 档可达最小总量 (步长 13 mod 37 的 5 个连续档位量之和) > 60,
   故**未填满分支在全域不可达**, F2 恒为 complete fill (N2 实测 60,000 个
   index, 0 例未填满)。接受该性质: 未填满语义仍由生产 oracle 的结构性
   不变量 (`filled+unfilled==size`, `is_complete ⇔ unfilled==0`) 与审计侧
   填账核对 (`fill_accounting`) 覆盖, 不因不可达而移除检查。
4. **tzdata / -S 环境** — 统一时区源裁决: IANA tz 数据经 `zoneinfo` 加载,
   **固定偏移永不替代**。通道: 驱动 (W2) 子进程 env = `-S` +
   `PYTHONPATH=<repo_root>:<pinned purelib>` (pinned purelib 含 tzdata,
   本机实测; 此即 wave 2 记录的 PYTHONPATH 证据性偏差, 本勘误确认为正式
   规则), 且 N1 模块自带解释器 purelib 回退自举。审计侧在 site 环境用真
   `ZoneInfo`, tzdata 不可用时**失败关闭** (`tzdata_unavailable`)。
   两侧已一致, 无代码改动。
5. **F2 Decimal→文本** — 钉死规范: descriptor 与 dump 中 Decimal 的文本
   形式一律 `format(d, 'f')` (永不产生科学计数法形式)。该常数族下
   `str(d)` 与 `format(d,'f')` 对全部可达值逐字节相同 (无指数形式值;
   N2 全域核对 0 不一致)。**修正**: N2 descriptor 构造从 `str()` 对齐为
   `format(d,'f')` (3 处, 行为零变化), 使两侧逐字实现同一规则。

---

## C. 实施与验证记录 (集成波, 摘要)

- 接线: `tests/support/soak_driver.py` W2 传 Q3 argv; S4' 摘要校验改按
  Q3 形状 (`files[]`, part 文件名, `ok` 检查); S5' 提升/指针不变。
- 对齐: N2 `soak_audit_subinputs.py` (Q2 entry/Q5 authz/B-5 'f') +
  `test_soak_audit_subinputs.py` (4 处字符串)。N1 模块**零改动**
  (sha `fc99d226…` 保持有效)。
- 验证: 集成树真实端到端 (驱动单轮 + N2 独立审计重算) 与全量受影响测试
  回归, 见 `evidence/n0/wave3/`。
- 常量: official_cases_run=0, sandbox_started=false, activation_authorized=false。
