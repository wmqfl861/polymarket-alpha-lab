# FX-56 现场变更与授权对照

以下时间均为 UTC。核对区间从 2026-11-26 14:24 到 2026-11-28 08:06，范围为项目研究、日常模拟定价及其运行环境。

| 时间 | 现场动作和证据 | 当时的依据 | 本轮确认 |
| --- | --- | --- | --- |
| 11-26 | M2 研究将 ETH、英超的公开材料保存在隔离的项目 PostgreSQL 研究库，并重新启动该隔离库核对原件。正式 OD-P 生效登记 0 次，合格并跑日增加 0 天。[ETH 回执](../fx-44/2026-11-26-eth-directed-books-readback.json) · [英超回执](../fx-44/2026-11-26-epl-directed-books-readback.json) · [登记回执](../fx-44/2026-11-26-prospective-registration.json) | 11-26 机主交办“M2 这条线照你们自己排的顺序继续，不等前面几项”。 | 沿用已登记的研究范围。 |
| 11-27 11:54:18.642 | 足篮小额模拟定价的 14 个现场文件完成安装及哈希核对。原命令退出 0，输出 `installed_and_hash_verified 14 files`。日常零元关注和人民币模拟单按有正数挂量的卖一价取价，前向研究和正式晋级仍用原口径。[定价发布交接](../../../handoffs/2026-11-27-sports-paper-pricing.md) | 11-27 11:09:11 机主说“你现在模型不要考虑深度，我现在注额小，不用管，这个功能先冻结。另外手法阵容是如何影响赔率的？现在模型有不同阵容胜率不一样的转换能力么？” 当时明确要求冻结日常盘口深度，未指定换汇参数或逐档均价。 | 本轮选择“维持卖一价”。[历史入账对照](../fx-57/2026-11-28-historical-best-ask.json) |
| 11-28 06:22:12 | 启动静态单元 `pal-euro-soccer-db.service`，建立独立项目研究实例。06:22:14.732 安装、启动及在新实例应用已登记迁移的命令退出 0，原现场读回 6 条迁移、待处理 0 条。[研究库说明及交接](../../../handoffs/2026-11-28-european-football-expansion.md) | 11-28 06:14:09 机主说“好的，那开始把这些联赛都开始纳入项目吧，你可以多子代理并行开发，结束后推送，然后我会让claude审核。注意，需要结合新闻和球队信息建模。” 这句话指定了研究和交付范围，当时未逐项点名独立数据库和常驻单元。 | 本轮确认“确认沿用现有三个欧洲研究单元及独立研究库”。 |
| 11-28 06:37:58 | 安装扫描单元 `pal-euro-soccer-study.service`，首次单独启动一次。它是按需运行的 `oneshot`，依赖研究数据库。 | 同上。 | 同上。 |
| 11-28 06:37:58 | 安装 `pal-euro-soccer-study.timer` 并执行 `enable --now`。06:37:58.948 部署命令退出 0。只有这个定时器登记为 `enabled`，每分钟触发一次扫描。[用户单元](../../../../deploy/systemd/user/pal-euro-soccer-study.timer) | 同上。 | 同上。 |
| 11-28 06:37 至 06:47 | 研究检出先使用 `557ff3f8` 开始采集，随后切到 `93448e26`。06:47:55 现场检出与已发布的 `eaf6e814` 一致，该次提交新增交接及采集回执。[现场回执](../../research/euro-soccer-20261128/field-check.json) | 机主在上述研究指令中明确要求“结束后推送”。 | 三个单元及独立库继续按本轮确认运行。 |

11-28 08:01:55 的现场只读状态覆盖全部五个联赛。赛前预览 32 场，28 场有新闻情景，17 场同时具备胜平负与大小球两类完整报价。已采前向窗口 0 个、收盘窗口 0 个、已评分赛程 0 场，研究金额 0 元。研究检出为 `eaf6e8149f83a708d09158529ec774f4fbed8b9d`。定时器为 `enabled/active`，数据库服务为 `static/active`，扫描服务为 `static`，当时最近一次扫描成功退出。现场读回命令为 `python -m polymarket_alpha_lab.papersim.cli euro-soccer status --repo-root .`。 [研究登记](../../research/euro-soccer-20261128/registration.md)

停止自动采集可运行 `systemctl --user disable --now pal-euro-soccer-study.timer`。已在执行的单次扫描可自然结束。已存证据保存在独立研究检出的项目 PostgreSQL 数据目录 `%h/.local/share/polymarket-alpha-lab/euro-soccer/.local/postgres/data`。本次是状态核对，暂停命令没有执行。
