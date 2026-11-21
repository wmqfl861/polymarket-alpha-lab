# FX-37　PR #84 独立事后复审回执

- 复审于 2026-11-21 13:20 UTC 完成，采用本机实际 ZCode guarded 只读会话 `sess_fe8bf848-b0ed-4ab0-a888-6fd407427906`。
- 历史 PR 头为 `21a5870cdfdd8144ad70f1958d83a63f17ac5bd4`。原合并时缺少可核对回执的审计记录仍有效。
- ZCode 输出正文共 12,403 字节，SHA-256 为 `cf57d92d5b04646cfacd7bd85e579d78a92f3e4d55369c383a1c2fe50ee376bc`。以下保留该输出。

附件集已确认（差异、审计摘录、后续修复清单三份，哈希与任务元数据一致）。以下为可直接入库的复审正文。

---

# 事后复审：FX-37 / MO-5b（PR #84）国家队两族适配器

**复审性质**：事后复审（2026-11-21 之后），属 FX-37。项目主线为仅模拟盘；此前已固定的审计结论原样保留，本复审不重开、不改写。
**复审者**：独立 ZCode 只读复审者。与本节点（MO-5b）当时的计划、实施、验证及本轮资料收集均无关；未参与 PR #84 的任何环节。
**复审方式**：仅依据附件静态审读（PR 头相对合并基的完整差异、既有审计发现摘录、限定路径后续修复清单），未执行任何命令或测试，未改动任何文件。公共地址与身份信息已遮盖，本复审不依赖被遮蔽内容。

## 一、所审提交与范围

| 项 | 值 |
|---|---|
| PR 头 | `21a5870cdfdd8144ad70f1958d83a63f17ac5bd4` |
| 合并基 | `647b81784465b6ce3352f601b5a8124dec0c8b26` |
| 实际合并提交 | `3e52516f71fdadeb86ef0d521521e6384ad9bb68` |
| 统一落地复核基线 | `2bdc4cccacc925ea8e6569c8865b90c0b0c671e6` |

差异触及的文件（全部在本车道内）：

- `src/polymarket_alpha_lab/papersim/intl_model.py` — 模型常量参数化；
- `src/polymarket_alpha_lab/papersim/intl_totals.py` — `IntlTotalsResearch` 构造器参数化；
- `src/polymarket_alpha_lab/papersim/optimize/families/__init__.py` — 注册表新增 `"intl-elo-poisson"`、`"intl-elo-goal-residuals"` 两键；
- `src/polymarket_alpha_lab/papersim/optimize/families/intl.py` — 新增 523 行，两族适配器；
- `tests/test_papersim_optimize_families_intl.py` — 新增 590 行测试；
- `tests/test_papersim_optimize_soccer_recalib.py`、`tests/test_papersim_optimize_spec.py` — 注册表并集 pin 的必要同步更新。

不触及 `scan.py`、`cli.py`、`capture.py`、`settle.py`、`optimize/provider.py`、`optimize/shadow.py`、`d1compare/packbuild.py`——这与下文的边界归属核对直接相关。

## 二、本节点（MO-5b）的具体责任

按其模块文档与测试自述，MO-5b 当时的承诺边界是：

1. **模型层参数化**：`intl_model` / `intl_totals` 的每个可调常量改为默认等于模块常量的 keyword-only 参数，显式默认调用与参数化前位级一致（模块常量仍是冻结值的唯一来源）；
2. **两族适配器**：`intl-elo-poisson`（slope ∈ [0.40, 0.55]、home_advantage ∈ {60, 80, 100, 120}）与 `intl-elo-goal-residuals`（recent_days / max_recent_matches / prior_matches 各自的离散选择集），含 `fit`/`predict`、规格身份、拒绝与回退语义的透传；
3. **懒加载注册表接入**：两键入表，导入纯净性不破；
4. **显式移交**（文档明示、非未兑现承诺）：数据契约 `intl-snapshot-v1` 待 MO-4 定稿；空 envelope 族系的门/记分语义由 MO-6 定义；`optimize init` 写基线由 MO-6 接线。

扫描谱系接入、市场包携带模型输入快照、配对查询与换届消费，均不在其差异内，也未见其以任何形式认领（见第四节）。

## 三、逐项复审结论

### 3.1 国家队两族适配器 — 通过

- **IntlEloFamily**（`families/intl.py:292` 起）：`spec_with` 对默认参数短路回 `baseline_spec()`（一倍标准误规则的前提，测试钉住）；基线 envelope 逐字复制 `intl_model.VARIANTS`、不做 `0.47-0.07` 型浮点运算（避免位级漂移，测试以 `SWEEP_SLOPES`/`SWEEP_TOTAL_MULTIPLIERS` 展开钉住）；非默认中心按 `_SLOPE_OFFSETS` 平移九变体，顺序与命名格式一致；`fit` 严格校验族属与参数完备性、把快照经 `freeze_value` 深冻结进 `IntlEloFitted`；`predict` 把 `home_advantage` 与重建的 variants 透传给 `fixture_prediction`。跨族类型守卫（错 fitted / 错 fixture / 错 family / 缺参数 / 空 envelope）均有拒绝测试。
- **IntlTotalsFamily**（`families/intl.py:407` 起）：三参数构造 `IntlTotalsResearch`；基线保留 `intl_totals.SHADOW_VERSION` 作为 `legacy_model_version`，非基线派生 `procedure_version+sha12`；空 envelope 为文档化的显式移交；`predict` 永不返回 `None`，不充分性由报价上的 `fallback_reason` 表达，供记分层消费——这是模型既有语义的忠实包装，不是本节点新造的洞。
- **注册表**：两键入表；两处既有测试的并集 pin 更新与协调者所有的三路合并注释一致，车道纪律干净（无越界文件）。

### 3.2 默认参数 — 通过

逐读点核对：`intl_model` 中 `ELO_BASE`（`_replay_elos` 全部四处）、`HOME_ADVANTAGE`（`_elo_delta`、`expected_gd` 调用点、`pair_lambdas_from_elo`、`_aggregate` 的 `home_advantage_applied`）、`GLOBAL_FALLBACK_TOTAL`（`_resolve_total` 两分支）、`LAMBDA_FLOOR`、`VARIANTS` 循环；`intl_totals` 中 `RECENT_DAYS`/`MAX_RECENT_MATCHES`（`_recent`）、`MIN_RECENT_MATCHES`（回退判据）、`PRIOR_MATCHES`（四个收缩除法）、`ELO_BASE`/`HOME_ADVANTAGE`/`DEFAULT_SLOPE`/`GLOBAL_FALLBACK_TOTAL`/`LAMBDA_FLOOR`（回放与报价路径）——全部改为经校验的参数透传，默认值即模块常量，无遗漏读点。新增校验（`_finite`、`_int_at_least`、`_validate_variants`）在默认值上恒通过，不改变行为。位级一致性的最强在证证据是测试中的冻结 dataclass 相等断言（显式默认 == 省略），以及针对计划评审发现 P2-3 的载荷性断言：非默认 `home_advantage` 以手工算术同时钉住回放层与桥接层、`variants`/`recent_days`/`prior_matches`/`max_recent_matches` 均有"必改变输出"断言——这正是"参数被静默丢弃也能通过相等测试"的堵法。文档提到的 334 场回放不在差异内，见证据限度。

### 3.3 评分漏洞 — 边界内无漏洞，两项移交点如实记录

本层与评分相关的面只有三处，均核查过：(a) totals 空 envelope 族系的"变体离散度/包络不得收窄"语义——模块文档明示由 MO-6 落地时定义，本节点留空而不臆造变体，是声明式移交而非漏洞；(b) `TARGETS_ELO=("match_winner",)` 的 research-only 限制在家族层以文档与 `intl_model.INTL_MW_RESEARCH_ONLY` 常量为凭，入场资格的机器强制属门控层职责（见 F-3）；(c) 报价级 `fallback_reason` 已透传供记分层过滤。记分本体的漏洞面在 MO-6，不在本次差异内。

### 3.4 数据不足边界 — 通过

Elo 族：任一队缺严格先行比赛时 `fixture_prediction` 返回 `None`（拒绝原因在模型层 `refusal_reason`），族层忠实透传且有测试（未知队、且注明拒绝路径参数无关）；`_validate_variants` 的非空校验使 Elo 族不可能带空 envelope 进入 `fit`。Totals 族：构造器合法性下限（`recent_days ≥ 1` 等，含 bool 拒绝）有专测；`zero_tournament_mean` / `insufficient_recent_matches` 两条回退路径在参数化后语义不变，且 `recent_days=365` 收窄触发回退有正测。

## 四、既有审核发现的归属核对（P1-4、P2-1）

**P1-4（前向版本错配）**：错配本体是 club-soccer 再校准谱系的派生版本串（`p4r1+<sha12>` 型）与扫描侧现役 `soccer-r23` 永不相等，属俱乐部车道与扫描/提供者侧的问题；其第 2 点所述"国家队和 NBA 的扫描谱系没有接入"引用的是 `optimize/provider.py`、`optimize/shadow.py`——两文件均不在 MO-5b 差异内。MO-5b 的模块文档如实陈述国际模型"从未进入扫描管道"，并未承诺接入；审计文本自身亦把换届消费归为 MO-8 的明确义务。事实性观察：intl 两族同样使用派生版本串（`intl-elo-v1+<sha12>`、`intl-elo-goal-residuals-v1+<sha12>`），且 totals 基线走 `legacy_model_version` 直返——**当**未来某节点把 intl 扫描谱系接入时，将面对与 P1-4 同形的配对语义问题；由于当前 intl 侧根本不存在扫描谱系与候选行，该错配在 intl 上无法显形，接入义务属当时的接线节点。此为前置提示，**不构成 MO-5b 当时的承诺，亦非其违约**。

**P2-1（市场准备缺口）**：发现市场、取盘口、取模型输入、算模型报价、组装请求这一段完全不在 MO-5b 差异内；两族以 `IntlHistory` 为拟合输入并做类型与快照封套校验，国际快照的供给侧属 MO-4/P2-1 的范围。与 P1-4 同理，归属不变。

## 五、后续修复覆盖情况

限定路径后续修复清单明示：**审计范围内无定向纠正补丁**。故不存在"后续修复是否覆盖承诺边界"的待核对象；MO-5b 承诺的交付物全部包含在头提交差异内——包括其在自身分支内即已消化的两轮评审意见：P2-3（非默认参数的载荷性断言）与 P3（构造器合法性下限），均在测试中有标注、有实现。统一落地复核基线 `2bdc4cc` 处，就附件所示范围，无未兑现的后续修复承诺。

## 六、真实发现（均不阻塞）

- **F-1（低，测试证据力）**：`tests/test_papersim_optimize_families_intl.py` 中 `copy.deepcopy(NL_SNAP) == NL_SNAP`（`test_elo_family_predict_matches_direct_model` 末行）为恒真断言，不能证明 `fit` 未改动调用方快照；要钉住该不变量需 fit 前留副本、fit 后比较。产品行为无恙（fit 走 `freeze_value` 深冻结，模型构造为只读），仅测试证据力问题。
- **F-2（低，加固项）**：`IntlEloFamily.fit`（`families/intl.py:350` 附近）不交叉校验 `spec.envelope` 的斜率中心与 `spec.params["slope"]` 一致；经 `spec_with` 产出的规格必然一致，但手工构造的 `ModelSpec` 可使二者脱钩，此时 slope 参数被静默忽略、预测仅随 envelope 变。建议后续在 `fit` 内加一致性校验；当前设计链路（规格均经 `spec_with` 产出）下不构成实质缺陷。
- **F-3（信息，移交观察）**：评分相关两个移交点——空 envelope 族系门/记分语义由 MO-6 定义（模块文档明示）；`match_winner` 的 research-only 限制在家族 API 上无机器可读的资格元数据，仅凭文档与 `intl_model.INTL_MW_RESEARCH_ONLY` 常量。二者若在 MO-6 落地时未被接住，缺口记在 MO-6，不记本节点。
- **F-4（信息）**：`spec_with` 的默认短路依赖浮点相等（`0.47` 与 `0.4700000000000001` 不短路、envelope 走平移路径）。同一表达式重算必得同一浮点、规格 sha 一致，确定性和可复现性无虞，仅语义说明。
- **F-5（信息）**：totals 族参数空间不含 `min_recent_matches`（构造器可调、族不调），数据不足阈值冻结为 4，与模块文档声明的三参数族一致；因设计文档未随附，空间与设计表的一致性只能以测试自洽为证。

**须另开修复的实质缺陷：未发现。** F-1/F-2 可并入后续任一触及该目录的车道顺手处理，不必单开。

## 七、证据限度

1. 仅凭三份附件静态审读，未执行命令/测试，未访问仓库全文；行号以差异附件的新文件行号推算，允许 ±数行误差。
2. 设计文档（optimizer-design 第 3/7/8 节）与开发计划（MO-5 行 5b）未随附：参数空间边界、choice 集、research-only 规则与设计原文的一致性未做原文核对，仅以模块文档与测试的自洽性为证。
3. "334 场回放"与完整 CI 结果不可由附件验证；默认位级一致性的最强在证证据是测试中的冻结 dataclass 相等断言。
4. 后续修复清单仅覆盖限定路径且报告为空；不能排除限定路径之外的后续变更，那超出本次复审授权。
5. 本轮不重开已固定的审计结论（P1-4、P2-1 原文照引，仅做归属核对）。

## 八、结论

MO-5b 在其声明边界内的交付（两族适配器、全读点参数化、默认位级一致的测试钉住、懒加载注册表接入、向 MO-4/MO-6 的文档化移交）完整、自洽、车道干净。P1-4 与 P2-1 按审计文本与模块文档的归属属于扫描/提供者/市场准备/消费侧节点，不是本节点当时的承诺，不应记为其缺口。后续修复范围内无待核补丁。真实发现 F-1–F-5 均为低危或信息级，无须另开修复的实质缺陷。仅模拟盘主线不受任何影响。

VERDICT: PASS
