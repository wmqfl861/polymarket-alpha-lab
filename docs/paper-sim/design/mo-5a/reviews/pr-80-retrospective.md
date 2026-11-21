# FX-37　PR #80 独立事后复审回执

- 复审于 2026-11-21 13:13 UTC 完成，采用本机实际 ZCode guarded 只读会话 `sess_88e096a2-601b-408a-a180-78b58c702543`。
- 历史 PR 头为 `905b0c881b11b8c584356840c9fb01afa9601e2b`。原合并时缺少可核对回执的审计记录仍有效。
- ZCode 输出正文共 8,142 字节，SHA-256 为 `4cd1334d74534b69c4ede22ea3bd3944ce0d6aee4dd89b132545466dd16e6d66`。以下保留该输出。

# FX-37 · MO-5a（PR #80）事后复审

**复审性质**：事后复审。于合并落地之后进行，非当时点评审；不重开、不推翻任何已固定的审计结论。

**复审者**：独立 ZCode 只读复审者。与本节点（MO-5a）当时的计划、实施、验证及本轮资料收集均无关。全程未执行任何命令、未改动任何文件，结论仅来自附件。

**主线口径**：项目主线仅模拟盘。

## 一、审阅对象与材料

- PR 头 `905b0c88`，合并基 `d37650ca`，实际合并 `d148e91a`，统一落地复核基线 `2bdc4cc`。
- 附件 1（original-changes.txt）：头相对合并基的差异，共 5 个文件——`optimize/families/__init__.py`（登记一行）、`optimize/families/soccer_recalib.py`（新增 480 行）、`tests/test_papersim_optimize_soccer_recalib.py`（新增 511 行）、`tests/test_papersim_optimize_spec.py`（1 处登记钉更新）、`tools/mo5a_repro_wp_p4.py`（新增 306 行）。
- 附件 2（follow-ups.txt）：审计范围内无针对性纠正补丁。
- 附件 3（audit-excerpt.txt）：P1-4（前向版本错配等三处）、P2-1（市场准备缺口），作为既有审计发现输入。
- 附件中的本地路径与身份信息已按遮蔽口径处理，本文不复述。

## 二、本节点（MO-5a）的具体责任

按差异内自述及其测试锚定，MO-5a 承诺且仅承诺：

1. 将 `papersim.recalib`（WP-P4 影子估计器）逐字包裹为可调族，`recalib.py` 在永不触碰清单内——差异中确无该文件，承诺履行。
2. 六键参数面（原样闭合集）、搜索空间 v1（WP-P4 3×3 网格，声明序）激活、v2 仅声明不激活（`param_space()` 只返回 v1，original-changes.txt:198-221、408-411）。
3. 九变体包络（中心 + 八扰动，固定序），fit 拒绝非机械推导的包络（original-changes.txt:274-299、449-458）。
4. 强制 low-score rho 经 `dataclasses.replace` 双层（逐联赛 + 全局）重写、Config 原样保留（original-changes.txt:371-392）。
5. 过程版本 `p4r1` 钉住 recalib 字节身份；`DATA_CONTRACT` 为占位，附"首次真实 `optimize init` 前可调"的冻结规则；TARGETS 限于 recalib 公开接口（1X2 + 大小球）（original-changes.txt:136-154）。
6. 本道分支自登记族键；跨道三向合并归协调者（original-changes.txt:7-12）。
7. 测试仅用合成数据；65 块对已发布 WP-P4 证据的复现由只读工具 `tools/mo5a_repro_wp_p4.py` 承担。

## 三、关注范围逐项核验

**俱乐部再校准适配器**：默认参数路径位等——baseline 六键即 `Config()` 默认（120/8/None/20/"fit"/3.0），`fit` 无其它变换、`forced_rho=None`（original-changes.txt:651-678）；强制 rho 双层重写、config 保留、未拟合联赛走全局层、极端 λ 下 predict 期可行区间钳制，均有对应用例（original-changes.txt:810-886）。族/过程版本/as_of/包络防篡改检查齐备（original-changes.txt:438-463）。导入纯净性有子进程级用例（original-changes.txt:966-1017）。

**冻结默认路径**：冻结族实现文件未被触碰；唯一触及是两处 `iter_family_ids()` 测试钉（original-changes.txt:1022-1027、641-645），属文件内声明的协调者合并产物。再校准族基线默认 == recalib `Config()` 默认 == WP-P4 回退 120/8，口径自洽。

**版本及样本合同**：`RECALIB_SOURCE_SHA256` 在 docstring、常量、测试三处一致（original-changes.txt:30-34、140-142、617-620）；`model_version()` 无 legacy alias、身份对键序稳定且 int/float 不混同（original-changes.txt:762-797）。`DATA_CONTRACT` 占位与冻结规则已声明。复现合同的比较面明确：逐块状态计数钉 53/12/0、逐块选中配置、逐候选分行位等、逐场 1X2 logloss 位等（`==`），输出路径拒入只读输入根，漂移只记录并随时间戳留痕（original-changes.txt:1094-1104、1185-1197、1302-1303、1323-1333）；判定逻辑为失败即哨，无静默放行路径。

## 四、真实发现（本节点边界内）

**无须另开修复的实质缺陷：无。** 以下为低严重度观察项，均不可由当前激活面（v1 网格、MO-6 基线）触达，建议随归属节点记录或作加固项：

- **O1** `fit` 不校验 `spec.data_contract` 与本族 `DATA_CONTRACT` 一致（original-changes.txt:438-463 检查清单中无此项）。spec 身份仍区分二者（sha 不同），注册准入属 MO-6；伪造 spec 场景之外无实际入口。
- **O2** 包络条目未按 ParamDef 边界复验：中心 `half_life_days∈[1.0,2.0)` 时 x0.5 条目低于校验下限，`envelope_specs` 提升时会拒绝；v1 与 v2 网格最小值均为 60，不可达。
- **O3** `completion_lag_hours` 以 float `lower=3.0` 校验，>3.0 的值在 spec 层合法，"固定 3.0"仅由搜索面成员约束；复现工具另设 `!=3.0` 即抛的硬闸（original-changes.txt:1127-1128）。
- **O4** `predict` 将 recalib 的全部 `ValueError` 映射为 `None`（original-changes.txt:489-496）；未文档化的内部错误会被计为拒答而非报错。复现链路中会以 `family_refused` 显式失败，不产生静默错数。
- **O5** recalib 字节钉为常量 + 测试字面断言，差异内未见对当前文件重算哈希的用例；若 WP-P4 侧测试已按证据清单重算则闭环（见证据限度）。
- **O6** 复现工具 `--league` 批次运行下状态计数必然部分化，verdict 恒为 MISMATCH；该过滤仅宜作进度分批，判定语义按全量运行设计。

## 五、既有审计发现的归属（不转嫁）

- **P1-4 三处**（配对版本串错配、真实扫描取不到影子拟合材料、换届不被扫描消费）所引文件——`papersim/cli.py`、`optimize/provider.py`、`papersim/scan.py`、`optimize/shadow.py`——均不在本 PR 差异内。版本串格式出自先于本节点的共享 `optimize/spec.py` 规则，族侧仅提供 `PROCEDURE_VERSION` 且不带 legacy alias，恰是设计要求的行为；因此 P1-4 不构成 MO-5a 当时未履行的承诺。其中第 1 处审计自注"按代码推断、未执行"，维持原样。
- **P2-1**（市场准备半链缺失）涉及 scan/pipeline/packbuild/capture/settle 及 usage 文档，均不在本 PR 差差内，同属后续节点边界。

## 六、后续修复覆盖情况

附件 2 载明审计范围内无针对性纠正补丁。MO-5a 的 PR 即其边界的完整交付，边界内无未兑现的遗留承诺，故"后续修复覆盖其承诺边界"对本节点为不适用（空集）。P1-4、P2-1 的纠正归属其各自节点，不在本复审附件范围内，其修复进展无法据此评估，亦不由本节点承担。

## 七、证据限度

1. 只读未执行：全部测试的通过与否、65 块复现的实际 verdict（reproduced/MISMATCH）无运行回执附件；行为性结论的置信来自代码路径推读与差异内断言的自洽性，非重跑验证。
2. `recalib.py`、`optimize/spec.py`、`wp_p4_history/walkforward`、冻结族实现不在差异内，其行为按差异内 docstring 与测试引用采信；`family_select_config` 对 `select_config` 的"逐行镜像"无法逐字核对——但候选分行位等 + 逐场 logloss 位等的严格判定可间接覆盖内层入选语义的漂移。
3. 实际合并 `d148e91a` 至基线 `2bdc4cc` 之间，兄弟 MO-5 道并入后的登记并集与测试钉状态不在附件内；两处双元组钉届时需协调者取并更新，属已声明义务，非本节点缺陷。
4. 本节点责任边界的认定依据差异内自述（设计/计划文档原件未随附），若设计原文另含未在差异中体现的验收项，以原文为准。

## 八、结论

MO-5a 在其声明的边界内交付完整且自洽：适配器逐字包裹、冻结路径未被触碰、版本与样本合同按共享规则钉住并有失败即哨的复现合同。边界内未发现须另开修复的实质缺陷；观察项 O1–O6 建议作为低severity记录随归属节点处理。P1-4、P2-1 维持原审计归属，不转记于本节点。

VERDICT: PASS
