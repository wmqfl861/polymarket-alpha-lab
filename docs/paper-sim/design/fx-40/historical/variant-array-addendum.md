# FX-40 计划增补草案　扫描候选的变体报价数组

**待原计划审核者复审。** 原 FX-40 计划的 SHA-256 为 `d3e613096b7b0b6ad480e75ba85aa2bbe8f371eddc8ad07461762ca55bcad226`。计划第 18、29、52–55 行已要求复用 B1 写者、保留有序变体、用正式受管入口写入并独立读回足球和加密报价。包的外层是 JSON 数组。嵌套字段 `variant_quotes` 可按两种既有形式通过解析。现有 SQL 对照也可用 JSON 文本承载同样的变体值，因此无法单独检出列表绑定错误。

## 已有证据

| 来源 | 已核实的事实 |
| --- | --- |
| P1-4 三轮原件，基线 `2bdc4cccacc925ea8e6569c8865b90c0b0c671e6` | 第 1、2 轮用同一非空数值数组包，均记录 `STARTED → FAILED`、`scan_id=null`、SQLSTATE `42846`。第 2 轮定位到候选 INSERT 的 `double precision[]` 转 `jsonb`。第 3 轮采用 JSON 文本，配置字节相同，两种形态解析后的包摘要均为 `9744dabebce6351eb13498517bf1fa1a1c77fe10f9ff64e15ec7dbf145b95467`。扫描成功，候选数 1。后来记录的 `fit_materials_missing` 影子拒绝、版本关联 1/0、两口径可评分配对均为零，各有独立含义。控制目录的 `archive-nov21-receipts.py` 第 63 行列出三份原件的路径。实读字节数和完整哈希与治理提交 `19103ccde07f2b36dbeb8a24a8b0037a317a2cfd` 的 `docs/paper-sim/design/fx-39/reviews/p1-4-baseline-reproduction.md` 一致。 |
| #114 的 `9647a3ef68c18e5fd5356aeb81bf8df06976a433` 和 #118 的 `f09850ba8ef010895d6a959cbeff401fbc706363` | 与基线逐段比对，两个头的 `scan._record_from_mapping / assemble_scan / run_scan`、`replay.bind_candidate`、`store_psycopg.record_scan / _candidate_params / _CANDIDATE_INSERT` 均保留同一传值和绑定。#114 的 `scan.py` 第 403、986、1130 行和 `replay.py` 第 481 行传递原值。`store.py` 第 639 行校验数组。`store_psycopg.py` 的写者在第 417 行仍把列表原样绑定到第 313 行的 `%s::jsonb`。两头的旧 `d1compare.packbuild.build_pack_record` 均写入文本 `"[]"` 和空中位数。 |

**对正式入口的影响是有条件的。** 原实验通过显式的一次性项目库连接调用普通 `run-scan`。在两个受审头，CLI 均尚无 `--prepare-from`。按已签方案让准备器向同一写者交付非空浮点列表时，上述错误会阻断 B1 候选提交。等值 JSON 文本已有基线成功对照。最终前置合并对象和准备器实际输出形式尚待核对，当前没有新模式的执行回执。

## 拟补入 FX-40 的实现和验收

1. **字段与写入边界。** 新准备模式的已报价候选在包内用 JSON 数值数组承载实际有序变体，按原模型保持精度和次序。旧包继续接受数组及包含数组的 JSON 文本。由 B1 写者在候选 SQL 参数绑定时处理这一个字段，复用 `store_psycopg._json_text` 或实施头的等效接口，列表只编码一次，合法文本原样绑定。适配放在 `submission.validate()` 和 `payload_sha256()` 之后。`ScanSubmission._candidate_payload` 按原表示计算摘要，所以等值列表和文本用不同扫描身份比较库内结果。既有扫描键的载荷冲突语义保留。冻结的请求字节、旧包、四份锚、既有摘要和账本数据均沿用原契约。范围限于准备器该字段、现有写者的单一参数及对应测试。
2. **原生 B1 回归。** 在既有 `tests/test_papersim_scan_store_native.py` 的隔离 PostgreSQL 夹具内，经 `records_from_pack → run_scan` 各跑一例非空有序数值列表、等值 JSON 文本、空列表、历史文本 `"[]"`。各例使用独立扫描身份，验 `SUCCEEDED`、一个预期候选、`jsonb_typeof(variant_quotes) = 'array'` 及逐项有序等值。重试同一固定输入，确认复用扫描与候选 ID，候选数和现金分录数稳定。在存储层用固定时钟、代码提交和其他字段，对照父提交的旧文本摘要，并验证同一扫描键换表示触发 `papersim_scan_payload_conflict`。原有校验用一个非数组对象断言 `papersim_variant_quotes_invalid`。基线两次失败直接引用上述原件。
3. **正式入口。** 在原计划第 53 行已安排的足球和加密正例里，从封存原始响应经 `run-scan --managed --prepare-from` 核对准备包中该字段确为数组。另开受管应用连接读相同扫描的候选。足球 r23 的十项有序报价、加密正例按其冻结模型实际产出的变体，与独立重算结果逐项相等，数据库类型为 JSON 数组。随原验收记录命令回执及每个样本的候选键、字段类型、元素个数和逐项比较结果。原第 42 行冻结字节对照和原节点验证照常执行。
4. **责任与交接。** FX-40 计划作者将本增补交原计划独立 ZCode 审核者。既定前置合并齐备后，在实际父提交复核上述写者。届时如已有等效修复，引用其精确提交和覆盖两种形态的原生证据，只补缺少的断言。由 FX-40 的 Sol/max 实施者负责准备器、B1 写者的单一改动点和测试代码。实际本机 ZCode 执行隔离测试，独立只读审核者按原计划验收。历史失败留在 P1-4 证据中，前置 PR 节点和七日运营计时沿用既有计划。
