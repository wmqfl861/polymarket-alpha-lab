# RV05 最小公共回执合同 — `pal-soak-subinput-receipt-v1`

- 任务: PAL_RV05_CAPACITY_20260921 · 节点 N0 (集成与调度)
- 状态: **FROZEN_DRAFT_1** (N1/N2 各自确认后转 CONFIRMED; 确认只允许对"接口面澄清"提问, 改语义须走 v2)
- 适用基线: 2584f6f2d86ce19e7e2dab6bea6a27a013587753 (tree 702d25b5aaabbd790bd48a0ebed8d1a79f12c24d)
- 范围: 新候选的 ok-compute 逐子输入回执 + 独立审计。旧候选 d929cc35、旧回执、旧运行证据不改不补写; 旧字段语义保持。
- 仓库内规范副本: `docs/contracts/soak-subinput-receipt-v1.md` (本文件的逐字副本; `contract_sha256` 锚定该副本内容字节)
- 度量环境: `pal-fix67-fc555171\runtime\python.exe` 3.12 x64, `-I -S -B`, 独立 TEMP/HOME, 纯 stdlib 单次运行实测

---

## 0. 背景一句话

现行驱动把每轮 payload(含 round/segment/tmp_dir)的哈希当 `input_sha256`, 审计
`verified_keys` 每轮最多记一个 `(scenario, sub_seed)`, 因此 74 个 ok-compute 轮最多
"验证" 74 个逻辑输入, 与 100k 目标差三个数量级。本合同定义新的**逐子输入回执**及其
**独立审计规则**, 让 2048×74 个槽位成为可逐条重算、去重、拒绝伪造的证据。

---

## 决定 1 — 新 schema 版本号与名称

- 新回执文档 schema 字段值: **`pal-soak-subinput-receipt-v1`** (精确字符串, 必填)。
- 载体: 每轮目录下**独立文件** `round-<N>/subinputs-<family>.json` (每语义族一个文件,
  见决定 4)。不写入 `round.json` 主体 (其 ≤2MiB 读上限与既有字段语义不动)。
- 与旧回执的区分: 旧 process 回执 = 子进程 stdout JSON (`echo_round`/`echo_seed`/
  `inputs`/`digest`/...), **无 schema 字段**, 含义原样保留; 本合同不重定义、不复用、
  不升级其中任何字段。旧 `receipt['inputs']` 永远是声明 (审计侧
  `receipt_declared_subinputs_sum` 原语义不变), 不自动变成 completed/oracle_passed。
- 版本策略: 任何字段增删改 ⇒ 新名称 `...-v2`, 旧版本文件按其自身版本解析;
  `schema` 缺失或不认识 ⇒ 整文件拒绝 (见决定 7), 不做 best-effort 解析。

**N1 接口面**: 生成器产出的每个回执文件顶层必须有 `schema="pal-soak-subinput-receipt-v1"`。
**N2 接口面**: 审计读取时首查该字段; 值不匹配 ⇒ 该文件全部行不计入且记
`receipt_schema_rejected` (新候选中出现 = FAIL; 旧候选根本无此文件 = 走旧规则)。

---

## 决定 2 — header 字段、序列化、rows 编码与字节预算

### 2.1 序列化 (规范性)

- 字节 = `json.dumps(doc, sort_keys=True, separators=(',', ':'))` 编码 UTF-8, 再追加
  一个 `b'\n'` (与驱动 `_canonical`+写盘模式一致)。
- "字段顺序"由 sort_keys 规范化决定 (字典序); 读者**不得**依赖插入顺序。
- 字节预算按上述真实字节 `len()` 实测; 禁止口头估算。

### 2.2 header 字段 (全部必填; 类型错 = 文件拒绝)

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `schema` | str | `"pal-soak-subinput-receipt-v1"` |
| `family` | str | 语义族 id, 白名单注册表键 (决定 3), `[a-z0-9-]{1,32}` |
| `entry` | str | 入口 id `<family>/<verb>`, 白名单注册表键 |
| `normalize_rule` | str | 规范化规则版本 id, 如 `norm:capture-codec-v1` |
| `candidate` | str | config.candidate 的规范 JSON 串 (字节稳定回显) |
| `manifest_sha256` | str | hex64; 须等于 campaign.json 的 manifest_sha256 |
| `generator_sha256` | str | hex64; 生成器模块源文件字节 sha256 |
| `contract_sha256` | str | hex64; 仓库内规范副本 `docs/contracts/soak-subinput-receipt-v1.md` 字节 sha256 |
| `round` / `segment` / `sub_seed` | int | 传输绑定 (echo 一致性), **不参与输入身份** |
| `scenario` | str | `"ok-compute"` (传输绑定) |
| `index_origin` | int | 本文件首行的全局输入下标 (见决定 8) |
| `oracle` | str | 生产侧 oracle id, 如 `producer:capture-codec-v1` (声明, 审计不信) |
| `counts` | obj | 六计数中的前五项 (决定 5) |
| `rows` | list | 行数组 (下述) |

header 序列化后 ≤ **2048 字节** (`receipt_header_over_limit` 拒绝)。

### 2.3 rows 编码

- 每行 = 恰好 2 元素数组 `["<input_sha256>","<actual_result_sha256>"]`, 均
  `[0-9a-f]{64}` 小写。
- 行按全局下标升序、连续排列: 第 k 行对应 `index_origin + k`; `len(rows)` 必须等于
  `counts.completed`。
- 文件内 input_sha256 不得重复 (重复键 = 文件拒绝, 见决定 8)。
- 实测每行 136 字节 (`["64hex","64hex"],`); 空 rows 骨架 header 576 字节。

### 2.4 字节预算 (实测, 2026-09-21, 独立 runtime)

| 行数 | 实测总字节 (含骨架 header) | 对 512KiB=524288 |
| --- | --- | --- |
| 2048 | 279101 | 通过 (余 245187) |
| 3800 | 517373 | 通过 |
| 3840 | 522813 | 通过 (余 1475) |
| 4096 | 557629 | **超 33341, 拒绝** |

- 单文件预算: 序列化字节 ≤ **524288** (512KiB), 实测判定; 超限 ⇒ 回执无效
  (`receipt_over_limit`), 该轮不合格。
- 由预算推导的单文件行上限: `floor((524288 − 2048(header上限) − 1(换行))/136)` =
  **3839 行** (3840 行在最坏 header 下 = 524289 > 524288, 数学上不可行)。
- 每轮规划: 默认 **2048** 行 (单文件, 实测 279101 字节); 绝对上限 **4096** 行/轮 —
  配置 3840..4096 时必须拆分编号 part 文件 (`subinputs-<family>.p02.json` 等), 每
  part 行数 ≤3839 且每 part 独立满足 512KiB; part header 增加 `part`/`part_count`
  (v1 允许, 字节计入同预算)。
- 既有上限不动: 子进程 stdout ≤1MiB (`max_stdout_bytes`)、轮记录读 ≤2MiB
  (`RECORD_MAX_BYTES`) 均不放宽; 回执文件是**新增**工件类, 审计读取用 524288 字节
  上限 + 1 字节超限探针 (与驱动 `_read_capped` 同型, 先拒后读)。
- 正式 RV05 配置即 2048 行 ⇒ 单文件路径; part 路径只为让 4096 绝对上限可表达,
  不为放宽任何预算。

**N1 接口面**: `serialize_receipt(doc) -> bytes` 必须按 2.1 产出; 写盘前实测长度并
在 >524288 时以 `receipt_over_limit` 失败 (不截断、不换紧凑编码续写)。
**N2 接口面**: 读取端 524288 上限 + 探针; 解析后复核 2.2 必填集合/类型/hex64、
2.3 行形状与连续性、header ≤2048 字节。

---

## 决定 3 — 白名单重算规则表 (入口→规范化→期望函数)

注册表 **v1** (三个族, 覆盖计划点名的"时间哈希绑定 / Decimal 对照 / 协议边界";
"cache 计数对照"列为可选扩展模式, v1 不承诺实现)。每族: 输入是**全局下标 index 的
纯函数** (与轮/段/机器/候选无关); N1 与 N2 **各自独立实现**推导 (N2 禁止 import N1
模块), 以本节规范文字为共同源。

### F1 `capture-codec` — 时间/哈希绑定 (规范规则 `norm:capture-codec-v1`)

- 推导: `zones = ['America/New_York','Australia/Lord_Howe','Europe/Berlin','Asia/Tokyo']`;
  `statuses = ['completed','failed','intake_blocked']`;
  `instant = datetime(2026,1,1,tzinfo=UTC) + timedelta(minutes=index)`;
  `zone_key = zones[index % 4]`; `status = statuses[(index // 4) % 3]`;
  `run = tests.test_research_capture_codec.make_run(now=instant.astimezone(zone(zone_key)), status=status)`
  (复用既有 fixture 作构造器, 不新造记录形状)。
- 规范化输入身份: `input_sha256 = sha256(canonical_json({"family":"capture-codec",
  "zone":zone_key,"instant_utc":instant.astimezone(UTC).isoformat(),"status":status}))`。
- 被测入口: `polymarket_alpha_lab.research_capture_codec.encode_research_capture(
  record_id=f"si-{index:012d}", model_id="synthetic", protocol_version="codec-v1", run=run)`。
- 实际结果: `actual_result_sha256 = sha256(payload.encode('utf-8'))` (payload 为
  encode 返回的规范串)。
- N1 生产 oracle (每行, 全过才写行): (a) 时区不变性 — 固定偏移等价 run 的 encode
  结果与原 run 逐字节相等 (既有不变量); (b) `decode_research_capture(payload,
  recorded_at=instant, expected_sha256=payload_sha256(payload))` 成功且再 encode
  与 payload 相等。
- N2 独立期望规则 (**不与 N1 同一 oracle 路径**): 按上表独立重建输入; 调被测入口得
  payload 后, 用 **decode 路径**验证: 解码记录的规范字段 (UTC 化时间、status、
  record_id、payload_sha256 绑定) 与推导描述符逐一相等; 不使用 N1 的固定偏移等价
  比较。禁 eval 回执内任何代码。

### F2 `paper-decimal-fill` — Decimal 对照 (`norm:paper-decimal-fill-v1`)

- 推导: `side = 'buy' if index % 2 == 0 else 'sell'`;
  `token_id = f'tok-{index % 7}'`;
  `size = bounded_decimal(Decimal(index % 400 + 1) / Decimal(8), positive=True)`
  (复用 `research_paper_inputs.bounded_decimal` 作规范化);
  `p0 = Decimal('100') + Decimal(index % 50)`; 阶梯 5 档: 档 j 价
  `p0 ± Decimal('0.25')*(j+1)` (buy 取 asks 升序/sell 取 bids 降序), 档量
  `bounded_decimal(Decimal((index + j*13) % 37 + 1), positive=True)`;
  `captured_at = datetime(2026,1,1,tzinfo=UTC) + timedelta(seconds=index)`。
- 规范化输入身份: descriptor = canonical_json({family, token_id, side, `size:str`,
  levels:[[`price:str`,`size:str`]...], captured_at iso}) 的 sha256。
- 被测入口: `polymarket_alpha_lab.paper.simulate_order_book_fill(
  PaperOrder(token_id, side, size), OrderBookSnapshot(token_id, captured_at, bids, asks))`。
- 实际结果: PaperFill 全字段的 canonical dump (Decimal→str, datetime→UTC iso) 的
  sha256。
- N1 生产 oracle: `filled + unfilled == size`; `is_complete ⇔ unfilled == 0`;
  平均价/最差价/中间价/价差的 Decimal 关系与 `PRICE_QUANTUM` 量化一致性。
- N2 独立期望规则: 按 LT-03 既有 oracle 文档模型**从原始档位重走一遍** best-first
  吃单 (不调用 simulate_order_book_fill): 最差价=最后消耗档; 未填满⇒不可执行;
  平均价按 `PRICE_CONTEXT` 重算并比较全部可独立推导字段; 记录哪些字段仅做结构性
  关系核验。

### F3 `uncapped-authz-codec` — 协议边界 + 校验和绑定 (`norm:uncapped-authz-v1`)

- 推导: `model_id='synthetic'`;
  `request_keys = [(f'rec-{index*3+k}', f'{((index*3+k)**31) % 2**256:064x}') for k in range(3)]`;
  `approved_at = datetime(2026,1,1,tzinfo=UTC)+timedelta(minutes=index)`;
  `expires_at = approved_at + timedelta(hours=1+(index % 48))`;
  `adapter_contract_sha256 = sha256(b'rv05-authz:%d' % index).hexdigest()`;
  构造 `UncappedResearchAuthorization` (经 `copy_authorization` 规范化/边界校验)。
- 规范化输入身份: descriptor = canonical_json({family, model_id, request_keys,
  approved_at, expires_at, adapter_contract_sha256}) 的 sha256。
- 被测入口: `authz.payload` 属性 + `authz.content_sha256` +
  `decode_authorization(payload, checksum)` 往返。
- 实际结果: `actual_result_sha256 = sha256(payload.encode('utf-8'))`。
- N1 生产 oracle: `sha256(payload)==content_sha256`; decode 往返再 `payload` 相等。
- N2 独立期望规则: **不经 dataclass decode**: `json.loads(payload)` 后按
  `json.dumps(sort_keys=True, separators=(',',':'))` 重序列化必须逐字节等于
  payload; 字段集合与 `schema_version` 精确匹配; sha256 相等。不同路径核同 payload。

### 注册表形式与扩展

- 注册表 = `{(family, entry): {normalize_rule, target, oracle_n1, oracle_n2_spec,
  derivation_spec}}`; 回执引用未注册 family/entry ⇒ `whitelist_unknown` 拒绝。
- v1 正式配置至少启用 1 个族 (2048 行/轮在该族内分配); 启用多族由 N1 在任务板记录
  后按决定 4 拆文件。新增族 ⇒ 合同修订: 新 `normalize_rule` id + 新表行 + 新
  `contract_sha256`; 不允许无版本变化悄悄加族。
- 排除项 (规范): 推导与身份**禁止**依赖 round/segment/scenario 名/sub_seed/tmp
  路径/测试名/nonce/搬运 seed/机器/候选。全局下标 index 是唯一域分配参数 (决定 8)。

---

## 决定 4 — 多语义族 group/type 标注形式

- **一个回执文件 = 一个族**。族标注只在 header: `family` + `entry` +
  `normalize_rule` (均必填, 见 2.2)。
- **禁止逐行 type 标签**: rows 严格保持 2 元素 (字节可预测、行形状可校验)。
- 多族一轮 ⇒ 每族一个文件 `subinputs-<family>.json`, 互不嵌套; 单族也用同名
  (统一路径规则)。全局下标在各族内独立编址 (index 从 0 起, 按族分配块; 块划分由
  生成器在 header `index_origin` 声明并接受审计复核)。
- 域不重叠证明责任: 同族内各行 index 区间 `[index_origin, index_origin+len(rows))`
  不得与同族其他文件 (同轮或同段其他轮) 相交 — 相交 ⇒ 重复输入, 去重只计一次并记
  `domain_overlap` (决定 8)。

**N1 接口面**: 每族一个 `run_family(family, index_block) -> (doc, counts)` 产物。
**N2 接口面**: 按 family 分组解析、重算、去重; 报告按族分列 + 总计。

---

## 决定 5 — 六计数的字段命名与位置 (回执 vs 审计输出分开)

### 回执 header `counts` (生产侧声明, 审计不信任, 只交叉核)

| 字段 | 定义 |
| --- | --- |
| `planned` | 本文件配置规划行数 (来自配置分配) |
| `generated` | 已构造规范化输入并**实际传入**被测入口的行数 |
| `attempted` | 被测入口**返回**并被捕获实际结果的行数 |
| `oracle_passed` | 生产侧 oracle 比较通过的行数 |
| `completed` | 实际序列化进 rows 的行数; **必须 == len(rows) == oracle_passed** |

- 必须满足 `planned ≥ generated ≥ attempted ≥ oracle_passed == completed`; 任一不
  成立 ⇒ 回执无效 (`counts_inconsistent`), 该轮不合格。
- 中途失败 (入口异常/oracle 失败/写失败): 计数冻结于失败点, `completed < planned`,
  轮 `final=failed`、reason 见接线文档; 有效前缀行保留在文件中但不贡献合格计数。

### 审计输出 (N2 `soak_audit` 报告新增 `subinputs` 块 — 只在审计侧存在)

- 按族+总计: `receipt_files`, `rows_read`, `rows_whitelist_verified`,
  `rows_rejected`, `planned_sum`, `generated_sum`, `attempted_sum`,
  `completed_sum`, `oracle_passed_sum`, `declared_vs_rows_consistent`,
  `domain_overlaps`, **`distinct_qualified`**, `dedup_collapsed`。
- **`distinct_qualified` 是审计独占字段**: 回执中出现名为 `distinct_qualified` 的
  字段 ⇒ 文件拒绝 (`producer_claim_reserved_field`) — 声明与证明不可混装。
- 旧指标并存不改名: `receipt_declared_subinputs_sum` (旧声明和)、
  `verified_distinct_inputs` (旧 (scenario,sub_seed) 指标, 更名报告标签为
  `legacy_verified_round_inputs`, 数值语义不变) 与 `distinct_qualified` 分列,
  永不相加、永不互转 (legacy 不自动升级)。
- 100k 门: 新检查 `min_distinct_qualified_subinputs` (PASS/FAIL/UNKNOWN), 只数
  `distinct_qualified`; 旧检查 `min_distinct_inputs` 原样保留服务旧候选。

**N1 接口面**: counts 五字段+不变量; 不得输出 distinct_qualified。
**N2 接口面**: `subinputs` 块 + 新门; 六计数在审计侧完整出现 (前五为交叉核后的
对照值)。

---

## 决定 6 — barrier/Spy 执行顺序验证机制 (选型)

选型: **进程内注入式顺序 Spy + 门控 barrier 测试** (否决: 子进程 strace/日志序
分析 — 平台相关且捕获无界; 否决: 纯时间戳断言 — 有竞态假阳/假阴)。

- 生成器模块提供**仅测试用**的顺序记录 seam: 依赖注入的 `recorder` 回调, 按序发出
  事件 `input_constructed → target_entered → target_returned(actual) →
  oracle_checked(verdict) → row_appended`。生产路径 recorder=None, 零行为差异。
- N1 单测用 `unittest.mock` 包装被测入口与 oracle 比较器, 断言: (a) 任一
  `row_appended` 之前必有对应 `oracle_checked(True)` 与 `target_returned`;
  (b) oracle 收到的就是目标函数实际返回值 (spy 捕获同一对象/相等值);
  (c) oracle 判 False ⇒ 无 `row_appended` 且轮失败;
  (d) 写行不先于 oracle 裁决 — 用人为延迟的 oracle (threading.Event 门控) 验证
  barrier: 裁决未放行前序列化缓冲不得出现该行。
- 生产行为不被 seam 改变: recorder 存在时不改变计数、不改变失败语义 (测试断言两
  路径产物逐字节一致)。
- 该机制只证明**顺序**, 不证明宿主诚实 (信任边界: 已审核测试程序 + 宿主, 见计划
  §4)。

**N1 接口面**: recorder seam + 上述 (a)-(d) 用例属 N1 验收项。
**N2 接口面**: 不复用该 seam; N2 的对应义务是其重算对每行独立成立 (行级失败隔
离: 单行重算不符 ⇒ 该行 rows_rejected, 不连坐其他行, 也不静默跳过)。

---

## 决定 7 — 未知字段兼容策略 (legacy 计数不自动升级)

三层策略, 逐层明确:

1. **新回执文件: 严格拒绝**。顶层字段集合必须精确等于 2.2 ∪ (part 文件的
   `part`/`part_count`); 多余字段 ⇒ `receipt_unknown_field` 拒绝 (防走私语义;
   新候选无历史包袱, 严格零成本)。未来加字段 = v2 新 schema 名。
2. **round.json / campaign.json 等旧记录: 忽略但不解释**。驱动新增字段 (如
   `subinput_receipts` 指针, 见接线文档) 对旧审计 = 未知即忽略 (现行行为); 审计
   永不从未知字段推导任何计数。**legacy 计数不自动升级**: 无 subinputs 文件的轮
   → 对新门贡献 0, 旧指标按旧规则照报; 旧 `receipt['inputs']` 声明永远不换算成
   completed/oracle_passed/distinct_qualified。
3. **旧候选/旧证据**: 完全不走本合同; 审计对其输出保持可证明下界或 UNKNOWN, 不
   回填 100k。

**N1 接口面**: 不得依赖"审计会容忍多余字段"写作行为; 回执字段集封闭。
**N2 接口面**: 实现三层: 拒绝(新文件)/忽略(旧记录)/隔离(旧候选); 未知字段名单
计入报告 (`unknown_fields_seen`) 供人工复核, 但不影响判定。

---

## 决定 8 — `distinct_qualified` 去重键规范化

- **去重键**: `dedup_key = entry_id + '\x00' + input_sha256`
  (input_sha256 = 决定 3 的规范化输入身份哈希; 族由 entry 唯一蕴含; 同一输入进
  不同入口 = 不同逻辑输入, 分别计)。
- **键排除项** (构造性排除, 非过滤排除): round、segment、scenario 名、sub_seed、
  tmp 路径、测试名、nonce、搬运 seed、机器、候选、part 号、`index_origin`、文件
  名。全局下标 index 只是**域分配器** (同 index ⇒ 同规范化输入, 任何机器/候选/
  轮重现同值); index 本身不进键 — 两个不同 index 若规范化后同输入, 仍只计一次。
- **计数资格** (全部满足才进 distinct_qualified 集合):
  1. 所属轮 `final == 'passed'` 且属于**同一合格 segment** (沿用审计 R4 完成绑定:
   latest 完整闭合段; 跨段拼接不算);
  2. 行所在回执文件通过结构校验 (决定 1/2/7) 且 ≤512KiB;
  3. 该行经 N2 白名单独立重算通过 (输入重建一致 + 期望规则通过);
  4. 无 2.3 违例。
- **去重作用域**: 合格行的全局集合 (跨轮、跨场景、跨文件); 同键首现计数, 后续
  出现折叠并计 `dedup_collapsed`。
- **两类重复的区分处理**:
  - 同一文件内重复键 ⇒ 结构违规, **整文件拒绝** (计划 §4 "拒绝重复键")。
  - 跨文件/跨轮重复键 ⇒ 合法的域重叠/重放表现: 折叠计一次, 记
    `domain_overlap` (N5 重复发射拒绝测试的审计侧呼应; 不失败, 因为证据诚实)。
- **100k 判定**: `distinct_qualified ≥ 100000` 且 864 有效 passed 轮、259200 秒等
  门分别独立判定 (互相不可替代)。

**N1 接口面**: 推导纯函数化 (仅 index 决定输入); index 块分配可证不重叠
  (`index_origin` 机制); 同输入重复生成时自行折叠不重写行。
**N2 接口面**: 独立重建键; 全局去重; 两类重复分别处置; 输出
  `distinct_qualified` 与折叠计数。

---

## 附 A — N1/N2 对齐检查单 (无需互相等待)

N1 (生成器, 独占 tests/support 下生成器模块 + 新单测):
1. 按 2.1-2.4 产出/度量回执; 3. 按决定 3 实现至少 1 族 (推导+入口+生产 oracle);
4. counts 五字段+不变量; 5. recorder seam + 顺序用例 (a)-(d); 6. 反例测试: 篡改
actual_result、重复行、仅 nonce 变化、截断/短写、错 seed/候选/入口、超限、中断、
写失败、旧 schema、跨 segment 混用 (计划 §6 列表)。
7. 不改 soak_audit.py / soak_driver.py; 接线需求提交给 N0。

N2 (独立审计, 独占 tests/support/soak_audit.py + 审计单测):
1. 524288 上限+探针读取与 2.2/2.3/7 校验; 2. 白名单注册表独立实现 (三族期望规则,
禁 import 生成器); 3. `subinputs` 报告块 + `min_distinct_qualified_subinputs` 门;
4. 去重键/两类重复处置; 5. 旧记录兼容三层 (决定 7); 6. 限额核验 (读取字节/记录数/
层级先行); 7. 单测覆盖旧记录 UNKNOWN、限额、去重边界 (计划 §3 N2 结束条件)。

## 附 B — 未决/边界

- 正式配置启用族数与每族行配比: N1 实现后在其任务板登记, 不改本合同。
- part 文件 (3840..4096 行) 仅在需要时由 N1 实现, v1 正式路径 2048 单文件。
- 本合同不授权任何执行: 不启动长测、不改生产 src/SQL/依赖锁; official_cases_run=0,
sandbox_started=false, activation_authorized=false。
