# FX-25 规则对照表（2026-11-20）

本表配合同目录 `AGENTS.proposed.md` 的审阅正文。基准是 PR #98 合并后的根规则，包含 PR #94 已获确认的原生 Codex 派工规则。上一稿的独立复审、owner 对七项取舍的决定和本次成稿的确认分开登记。最终正文按 OD-N 再交 owner 过目。

## 七项决定

| 项 | 成稿中的处置 | 依据 |
| --- | --- | --- |
| 1. 模拟盘主线 | “模拟盘主线与 V1 备线”指定 papersim 为主线，V1 六包未激活，并保留获准的原生测试维护。 | `DELIVERY_PLAN.md` §77.3.1 OD-A，owner 本轮采纳。 |
| 2. 协调和授权 | “协调、复审与授权”指定协调者、文件写权、独立复审及代为裁定的边界。沿用 #94 的 Astra/max 计划和 Sol/max 其余分工。真实资金、凭据和真实下单由 owner 本人单独确认。 | 11-19 审核报告 §3.7 第 1、5 条，§77.3.1，11-20 派工决定及本轮采纳。 |
| 3. 云端审核流程 | “云端审计交接”明确每轮向保留分支交付文档及本地执行提示词，登记审计方推荐的默认处理和本地的独立合入复审。 | §77.3.1 中 11-13 的两条原话，11-19 审核报告 §3.7 第 5 条，owner 本轮采纳。 |
| 4a. 节点完成条件 | 铁律 6 末尾加入模块的可达性、影子登记及到期处置，以及合并后不可达模块数的核验。 | 蓝图 v2 §8.2，§77.3.1 OD-E，owner 本轮采纳。 |
| 4b. 设计和计划入库 | “Engineering Defaults”要求节点引用的设计或计划文档与该节点一同入库。 | 11-19 整改清单 §2 FX-28 及 §3 第 4 项，owner 本轮采纳。 |
| 5. 输入快照 | “Engineering Defaults”要求在受管 PostgreSQL 保存快照原始字节，清单和引用保存校验值。 | 11-19 整改清单 §3 第 5 项，owner 本轮采纳。 |
| 6. 开发平台 | “开发与验收平台”规定项目只在 Linux 开发，本机测试和验收使用 Linux，发布结论以 Linux CI 为准。 | 2026-09-27 根规则和 2026-11-20 owner 的本轮裁定。11-13 OD-E 记录及 11-19 整改清单 §3 第 6 项中拟开放 Windows 开发的历史提议，按本轮裁定处理。 |
| 7. 主机称谓 | “Engineering Defaults”要求使用主机别名，在本机配置维护对应关系，新文件、日志、提交说明以及转引的原文均使用别名。 | 11-19 整改清单 §0 第 8 条、§2 FX-31 和 §3 第 7 项，owner 本轮采纳。 |

owner 本轮原话为“保留只在 Linux 开发，其余六项采纳，修订成稿再给我看”。合并根规则须取得对这份正文的确认。原审计交接包与历史决定记录各保留其原文。

## 现行规则逐条映射

| 编号 | 根文件章节或条文 | 正文处理 |
| --- | --- | --- |
| 1 | Project Instructions | 沿用标题。其前的 owner 审阅说明另列于正文外。 |
| 2 | Native Codex Subagent Routing | 逐字保留 #94 的逐次显式模型、推理深度和独立复审分工。 |
| 3 | Current owner override · Linux-only V1 platform (2026-09-27) | 逐字保留。新增“开发与验收平台”将 owner 本轮明确的 Linux 口径用于项目。 |
| 4 | Current owner instruction · freeze V1 and close six work packages | 逐字保留。新增“模拟盘主线与 V1 备线”列明主备优先级。 |
| 5 | Current owner instruction · complete GitHub-first handoffs | 逐字保留。“云端审计交接”补充每轮交付及本地复审职责。 |
| 6 | Current owner override · native project-private PostgreSQL | 逐字保留。快照原始字节按本节使用受管 PostgreSQL。 |
| 7 | Scope | 逐字保留。 |
| 8 | Project Iron Rules（引言） | 逐字保留。 |
| 9 | 铁律 1 · 数据持久化 | 逐字保留。 |
| 10 | 铁律 2 · 遗留文件持久化 | 逐字保留。 |
| 11 | 铁律 3 · 模拟、只读与报告范围 | 逐字保留。 |
| 12 | 铁律 4 · 独立只读复审 | 逐字保留。新增的两段协调与交接职责援引本条。 |
| 13 | 铁律 5 · 关闭快速模式 | 逐字保留。 |
| 14 | 铁律 6 · 持续并行开发 | 现行正文逐字保留，在条末增加第 4a 项节点完成条件。 |
| 15 | 铁律 7 · 模型和推理深度 | 逐字保留。 |
| 16 | CodeGraph | 逐字保留。 |
| 17 | Data Source Priority | 逐字保留。 |
| 18 | Engineering Defaults | 五项现行要求逐字保留，在条末增加第 4b、5、7 项。 |
| 19 | Agent Coordination Defaults | 逐字保留。 |
| 20 | Model Defaults | 逐字保留。 |
| 21 | Review / Audit Defaults | 逐字保留。 |
| 22 | Codex Node Push Policy | 逐字保留。 |
| 23 | OMO / Sisyphus Session Workflow (historical) | 沿用原文及历史标记。 |
| 24 | Authority and handoff | 历史子节，逐字保留。 |
| 25 | Git strategy | 历史子节，逐字保留。 |
| 26 | Historical model configuration | 历史子节，逐字保留。 |
| 27 | Workflow summary per stage | 历史子节，逐字保留。 |

## 依据与地址卫生

- `DELIVERY_PLAN.md` §77.3.1 记载 11-13 的原话、OD-A、OD-E 和本人确认范围。§80.5 记载 OD-N 的成稿确认要求。
- `docs/handoffs/2026-11-19-audit-optimizer/01-audit-report.md` §3.7 记载 11-14 授权与本地独立复审的依据。该交接包的 `03-fix-list.md` §3 列出七项要求。
- `docs/handoffs/2026-11-12-audit/02-blueprint-v2.md` §8.2 给出两项节点完成条件。该交接包内的 `evidence/audit_static.py` 提供核验程序。
- 地址卫生涉及上一稿所标的 Linux 开发主机位置。当前根规则在该处使用“designated Linux host”。成稿以主机别名 `pal-dev` 为例，地址对应关系由本机配置维护。
