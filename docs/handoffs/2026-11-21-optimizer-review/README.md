# 2026-11-21 优化线进度审核交接包（README）

- 快照时刻【事实】：2026-11-21T04:50:20+00:00（底稿冻结，D1 发布状态补记至 05:03 UTC）。
- 正本仓库（公开）：`wmqfl861/polymarket-alpha-lab`；保留分支：`handoff/optimizer-review-2026-11-21`；包目录：`docs/handoffs/2026-11-21-optimizer-review/`。
- 交付提交 R 与 `MANIFEST.tsv` 校验值不在本包文件内（交付物不可自指其所属提交），由协调方在交付回执中随发，占位与用法见 `reviewer-prompt.md` §2。

## 1. 审核范围

本包供审核员核对 GitHub 上的代码、交付证据和剩余验收项。第一阶段的正式交接书待功能合入和真实端点取证完成后提交。

## 2. 覆盖范围

- 审计基线 B=`c4367b6b3ce4d957ce79ce645a7ab3425be5a133` 至冻结 main M=`2bdc4cccacc925ea8e6569c8865b90c0b0c671e6`（#117 合并）的第一父链，共 44 个合并节点（evidence.md §1）。
- 开放 PR：#114（MO-6，公开头 `26dfa2a05357121665009981cd600e23c91e093a`）、#116（FX-18，头 `8c7b536514eda6dfc772bf7fc6a4fc597892a294`）（evidence.md §2）。
- D1 取价、公开终局取证与逐笔结算恢复已推送。[PR #118](https://github.com/wmqfl861/polymarket-alpha-lab/pull/118) · [固定提交](https://github.com/wmqfl861/polymarket-alpha-lab/commit/f09850ba8ef010895d6a959cbeff401fbc706363)
- 三族真实历史记分卡（evidence.md §3）、原审 §3.8 六项本地复核登记（evidence.md §6）、现行派工与角色（evidence.md §7）、阶段与可复现边界（evidence.md §8）。

## 3. 已交功能（摘要；逐项证据见 evidence.md）

【事实】

- 固定范围内共 44 个合并提交。主要节点包括固定审核交接包；MO-0、登记与蓝图 v4（#70）；FX-28 库外依据；MO-1 模型规格；FX-26 比赛键和开赛时间；MO-4 数据集；MO-5a/5b/5c/5d（俱乐部足球、国家队、NBA、加密）；M2 B1 扫描；MO-2 受管注册表；MO-3 评估和回归；B2 取价与赛前 CLOSE；B3 初次结算；MO-7 影子；MO-8 判定和角色；B3 逐笔费用与赛果；MO-6 运行器和搜索、MO-6 运行边界；B4 报告快照及加时终场；D1 冻结归档；FX-35 逐笔读序与预登记重放；D1 公开市场与价格取证；D1 同一实例预检；现行 ZCode 审核规则（#117）。
- 优化线第二阶段：MO-7、MO-8 软件变更已在 main；MO-9 计划已过审（冻结登记 `phase2_checkpoint`）。
- 三族真实历史卡已按指纹登记（原运行代码提交 `e234c3364c204db513cc4fbcdcb6d5eb43c7417b`）；正式历史运行档案 88,845 字节、SHA-256 `03c616e1d94efe5e3a7879e3277645b5d60e4e407bdef6e9d31d9f7eb3366ad5`（evidence.md §3）。

## 4. 未完成事项（事实与缺证分列）

| 事项 | 状态 | 证据位置 |
| --- | --- | --- |
| #114（MO-6）合入 | 开放；最新三条绿作业实际检出旧对象 `0e9210f0…`（基于 `8cba9d37…` 的旧合并）；当前 GitHub 合并参照 `a0086b82…` 无更新结果；原审允许正常集成重试 | evidence.md §2.1 |
| #116（FX-18） | 开放；两个文档/状态文件标记 B1 候选待清理 | evidence.md §2.2 |
| D1 候选发布 | C/S1/O/A 由 PR #118 送审，已通过本机验证及独立只读复审 | evidence.md §5 |
| S2 公共结果登记与三进程集成 | 未完成（D1 范围声明） | evidence.md §5、§8 |
| M2/D1 七个真实并跑日 | 未交付 | evidence.md §8 |
| 稳定的模拟盘前向周期与两份周报 | 未交付（`phase2_checkpoint`） | evidence.md §8 |
| 正式第一阶段出口 | 待真实端点证据 | evidence.md §8 |
| D2 | 本快照无任何进度证据（缺证；补证条件：下一节点登记其计划与执行证据后再评） | — |

## 5. 下一步取证（计划项，非已完成）

1. #114 以真实头重跑三条 CI 并取得合格 checkout 证明；空注册表逐位一致与三族真卡归档核对后按已审计划普通合并，并核验双亲与集成差异。
2. 协调者更新 `DELIVERY_PLAN.md`；独立文档头完成规定本地门、独立复审与三条真实 CI 后，提交正式 `docs/handoffs/2026-11-19-optimizer-phase1.md` 并普通合入。
3. 核对 PR #118 的固定代码与工作流。S2、七天模拟并跑和 D2 分别留证。

## 6. 固定链接（仅真正已存在的 GitHub 对象）

- 正本仓库：https://github.com/wmqfl861/polymarket-alpha-lab
- 原审包目录（固定提交 `b2f09f2cfa8727ee80c22c3ecf01ad0244c1dbb8`，树 `39fdc95425fdbc4d07bc2a310371a6908f1ca9f6`；登记：固定版 23 个文件、保留分支版 24 个文件，保留分支多出 `06-local-agent-prompt.md`）：https://github.com/wmqfl861/polymarket-alpha-lab/tree/b2f09f2cfa8727ee80c22c3ecf01ad0244c1dbb8/docs/handoffs/2026-11-19-audit-optimizer
- 原审问题 §3.8：https://github.com/wmqfl861/polymarket-alpha-lab/blob/b2f09f2cfa8727ee80c22c3ecf01ad0244c1dbb8/docs/handoffs/2026-11-19-audit-optimizer/01-audit-report.md#38-需本地-agent-复核
- 原 06 提示词所属提交（随 #69 合入）：https://github.com/wmqfl861/polymarket-alpha-lab/blob/27c66cee11725fcf8d0b212e4633016b11e48835/docs/handoffs/2026-11-19-audit-optimizer/06-local-agent-prompt.md
- 蓝图 v4 引入合并（#70）：https://github.com/wmqfl861/polymarket-alpha-lab/blob/2bdc4cccacc925ea8e6569c8865b90c0b0c671e6/docs/paper-sim/blueprint.md
- `DELIVERY_PLAN.md` §80.9 六项登记（#111 合并时点）：https://github.com/wmqfl861/polymarket-alpha-lab/blob/af4ff553262a7ee532e36e1b0d8ba4939522f4ba/DELIVERY_PLAN.md
- `DELIVERY_PLAN.md`（冻结 main）：https://github.com/wmqfl861/polymarket-alpha-lab/blob/2bdc4cccacc925ea8e6569c8865b90c0b0c671e6/DELIVERY_PLAN.md
- #117 规则提交：https://github.com/wmqfl861/polymarket-alpha-lab/commit/8d41cba5504b0737bd2af457584ac520ff8e7c45 ；合并提交：https://github.com/wmqfl861/polymarket-alpha-lab/commit/2bdc4cccacc925ea8e6569c8865b90c0b0c671e6
- 两端固定的开放 PR 差异：#114（重开基）https://github.com/wmqfl861/polymarket-alpha-lab/compare/2bdc4cccacc925ea8e6569c8865b90c0b0c671e6...26dfa2a05357121665009981cd600e23c91e093a ；#116 https://github.com/wmqfl861/polymarket-alpha-lab/compare/11fb457c6e714844ec2ee1c0eb3c90dbd145eaa7...8c7b536514eda6dfc772bf7fc6a4fc597892a294
- 合并 PR #69–#117 与开放 #114/#116 链接逐行见 evidence.md §1、§2；提交 URL 统一模式 `https://github.com/wmqfl861/polymarket-alpha-lab/commit/<完整SHA>`
- 欧洲篮球固定验证：https://github.com/wmqfl861/polymarket-alpha-lab/blob/c4367b6b3ce4d957ce79ce645a7ab3425be5a133/docs/paper-sim/research/league-admission-20261119/europe-basketball/verification.json
- 欧洲国内足球原摘要：https://github.com/wmqfl861/polymarket-alpha-lab/blob/c4367b6b3ce4d957ce79ce645a7ab3425be5a133/docs/paper-sim/research/league-admission-20261119/europe-football/summary.json

MO-6 和 D1 的本机回执按原始指纹登记。[证据](evidence.md)

## 7. 包文件与阅读顺序

1. `README.md`（本文件）→ 2. `evidence.md`（逐项证据与复核命令）→ 3. `reviewer-prompt.md`（可直接转发的审核提示词）。`MANIFEST.tsv` 由协调方在三份文件定稿后机械生成：无表头、Tab 分隔三列（相对路径/字节数/SHA-256）、共 3 行、每行以 LF 结尾，行序 `README.md`、`evidence.md`、`reviewer-prompt.md`。

## 8. 标注与安全约定

- 结论按【事实】/【推断】/【缺证】标注并给来源；库外草稿仅作写作素材，每项结论逐项溯源，待填字段一律改写为具体缺证与补证条件。
- 本包不含公网 IP、凭据、服务器内部绝对路径、数据库连接串或原始日志；操作员本机材料仅给脱敏摘要与指纹；包制作辅助程序仅本机保留，可复现边界见 evidence.md §8。
