# FX-41 NBA v2 受管端到端计时验收清单

已通过独立审核的计时方案足以执行。当前还差最终支持版本在隔离原生 PostgreSQL 上的一次完整合成受管命令。正式登记和历史评分均待后续步骤。[合成计时方案](./fx41-nba-v2-synthetic-timing-execution-plan-astra.md) · [计时方案审核](./fx41-nba-v2-timing-plan-review.yZKbPHyG/result.txt) · [受管接线及审核](./fx41-nba-managed-binding-plan-review.NZTQF0DI/result.txt)

- [x] **沿用已完成的纯计算实测。** 实际 Linux ZCode 会话 `sess_b0d321f4-bae6-4e86-a2f4-79e3e574737f` 的冷启动合成结果为 4,811/4,811 次物理试验、9,622 个目标用途、566 次嵌套选择及 2 次最终选择。每个完整规格使用六个变体。胜负目标自然开启留出，大小分由合成成本样例补齐，两目标各调用挑战者和现役一次，共 4 次顶层回调、152 次逐日评估、1,216 次逐场预测。纯段耗时 1,885.937281 秒，扣去重复判定后的双目标组件耗时 1,885.937082 秒。已编码的规范分段共 406,636,497 字节，最终数据库工件大小和写入时间仍待实测。原始回执 SHA-256 为 `7a47da17ef45d927e479fc8e69dda8fb634804206f4bc266c597cd1e5eddb215`。[计时回执](/data/pal-verification-20261120/pr-review-validation.Hhpgik2k/nba-v2-timing-full-pure.SuFUivqu/test-summary.json)
- [ ] **#118 与 v2 接线合入并通过既有验证后，在合成库计时。** 现有驱动已提供 `test_full_pure`。若接线时仍缺受管用例，按原计时方案仅补 `test_full_managed`。实际 Linux ZCode 测试执行者复用同一驱动、种子和两份合成归档，在可比环境以最终支持提交冷启动。经 `optimize register-experiment --managed` 登记合成身份，第二连接读回，再用 `optimize run --managed` 的 `--dry-run` 只读核对元数据。首次正常合成命令使用同一指针，走真实输入校验、锁和 `STARTED`、模型与统计、整件组装与序列化、终态写入、提交确认及 CLI 返回。采集实际提交、运行标识、各段单调时刻、工件字节数与摘要。命令返回后第二连接核对封存结果，并核对同请求重放的科学计算增量为零。登记、预览、事后读回与重放各自另计。这次合成运行使用以下入口，两变量分别指向隔离测试根与其登记指针。

  ```bash
  python -m polymarket_alpha_lab.papersim.cli optimize run \
    --managed --repo-root "$synthetic_root" --family nba-pace-efficiency \
    --as-of 2026-11-19T02:00:00Z --input-manifest "$synthetic_pointer" \
    --seed 20261120
  ```

- [ ] **同时核对两条严格时限。** 用该命令的 `optimization_command_clock` 从同一入口起算，至计算结束须 **< 3,540 秒**，至正常返回须 **< 3,600 秒**，记录终态提交和收尾实耗。自然双开时取真实整程。若只开部分目标，用同输入、同模型与规格、同统计设置、可比环境的双开实测，替换各时段中已计入的对应自然留出及统计，再计入双开工件实际或有实测依据的额外组装、写入与确认成本。保留实测原值、阶段替换依据及保守总数，两界分别判读。纯段的 1,885.94 秒仅作已覆盖成本依据。缺阶段或成本界限不明即记“时钟未证”。任一保守总数触界即按原案记 `NO-GO` 及差额，保留首因和完成、中止、未启动数。[判读与测量边界](./fx41-nba-v2-synthetic-timing-execution-plan-astra.md)
- [ ] **技术就绪后才进入获批的一次历史日程。** 在指定研究库登记 `fx41-odr-nba-regular-2024-25-2025-26-v2`，第二连接读回绑定、来源、比较者与计划，确认评分启动为零，随后仅元数据预览，再用同一身份运行一次并独立读回原终态。核对两季各前三日只训练、283 个测试日 × 17 个规格、38 日/304 场留出以及既有质量门。两季在所查缓存中原始球队赛程均为 0/30、逐场摘要均为 0/1,230，依原案保留 Gate A `NOT_ASSESSABLE` 并按实际回调报告各目标结果。[业主批复](./fx41-nba-single-run-owner-approval.json) · [正式命令及读回顺序](./fx41-nba-v2-owner-registration-plan-astra.md) · [六变体更正](./fx41-nba-v2-six-variant-plan-addendum-astra.md) · [接线计划](./fx41-nba-v2-managed-binding-seam-plan-astra.md)
