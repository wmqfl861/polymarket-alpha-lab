# NBA 历史回测与验收复核

**胜负、让分、大小分均维持影子观察。** 原 WP-NBA 口径下，本季 86 场满足回测资格，总分拟合 Gate B 的 Pearson r=0.2046、p=0.0587。胜负 Gate E 相对 Polymarket 赛前价的平均绝对概率差为 10.73 个百分点，相对 ESPN 留存的 DraftKings 收盘代理为 11.04 个百分点，两项均高于 6 个百分点。

冻结截止为 2026-11-19 02:25:00 UTC（美东 11-18 21:25:00，北京 11-19 10:25:00），ESPN 请求发出于美东 21:25:00。美东 11 月 18 日赛程共 12 场，0 场终场、9 场进行中（含中场）、3 场未开赛。快照含 210 场已完赛，覆盖 2026-10-20 至 2026-11-17。归档的 194 条旧预测仅 86 条满足两队各有至少 8 场同赛季严格在先的比赛。 [ESPN 当日赛程](https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=20261118&limit=100)

## 样本与原验收口径

| 赛季 | 常规赛覆盖 | 可评分场次 | 训练 / 调参 / 留出 | 留出日期（美东） | Gate B 总分 r | Gate E 对严格 PM 胜负的 MAD |
|---|---|---:|---:|---|---:|---:|
| 2026-27 | 2026-10-20 至 2026-11-17，210 场 | 86 | 30 / 20 / 36 | 2026-11-13 至 2026-11-17 | 0.2046 | 10.73 个百分点（86 场） |
| 2025-26 | 2025-10-21 至 2026-04-12，1,230 场 | 1,106 | 372 / 323 / 411 | 2026-02-19 至 2026-04-12 | 0.3161 | 8.46 个百分点（156 场） |
| 2024-25 | 2024-10-22 至 2025-04-13，1,230 场 | 1,104 | 358 / 330 / 416 | 2025-02-19 至 2025-04-13 | 0.3956 | 严格 0 场。另有 160 场诊断 |

Gate A 核对数据完整性。Gate B 要求预期总分与实际总分的 Pearson r≥0.25、p<0.05、n≥20。Gate C 要求 30 队评级与胜率的 Spearman ρ≥0.50。Gate D 要求胜负在 n≥20 时同时用 Brier 和 log-loss 胜过掷币和恒选主队。Gate E 用胜负概率的 MAD≤6 个百分点。交付计划另要求同赛季达到约 90 场时复测。两个完整旧赛季提供独立的历史证据。大小分原报告列出同盘口对掷币的 log-loss。全场让分可根据下表做诊断，原脚本未为该盘口单列放行阈值。 [交付计划第 74 节](../../../../../DELIVERY_PLAN.md#74-wp-nba-篮球影子模型2026-11-10未达入场标准) · [原验证报告](wp-reference-report.md)

本季 A 的数据完整性通过同一解析器的离线重建核实，原验证命令在读取完整缓存时返回 `public_fetch_not_allowed`。本季 C、D 通过。C 的 ρ=0.9349。D 的全样本基线胜负 Brier/log-loss 为 0.2054 / 0.6144，掷币为 0.2500 / 0.6931。下面本季留出胜负的 log-loss 高于掷币。两个旧完整赛季的 B、C、D 通过。2025–26 的严格赛前胜负 MAD 高于 6 个百分点。2024–25 的胜负合约均按终场比分做诊断。

## 严格赛前同盘口留出

严格样本的合约明确以含加时的全场最终比分结算。逐笔核对双方队名、结果及 token 顺序、最终 0/1 赔付方向和 NBA 终场比分。每项显示 Brier / log-loss，越低越好。胜负取主胜，让分取主队赢盘，大小分取大分，走盘从二元评分剔除。

| 赛季 | 盘口 | 配对 n | WP-NBA 基线 | 校准后 | Polymarket 赛前价 |
|---|---|---:|---:|---:|---:|
| 2026-27 | 胜负 | 36 | 0.2480 / 0.7460 | 0.2480 / 0.7460 | 0.2353 / 0.6675 |
| 2025-26 | 胜负 | 156 | 0.1661 / 0.5098 | 0.1804 / 0.5465 | 0.1513 / 0.4673 |

## 按 NBA 全场终场比分诊断

下列合约同样通过赛前报价和终态方向核对。合约未逐笔明示是否含加时，且本次核对未取得发布时已生效并适用于这些国际站合约的统一条款。整组用官方终场比分，组别依据合约文字划分。

| 赛季 | 盘口 | 配对 n | WP-NBA 基线 | 校准后 | Polymarket 赛前价 |
|---|---|---:|---:|---:|---:|
| 2026-27 | 让分 | 36 | 0.3058 / 0.8220 | 0.3058 / 0.8220 | 0.2510 / 0.6952 |
| 2026-27 | 大小分 | 33 | 0.2836 / 0.7695 | 0.2450 / 0.6854 | 0.2534 / 0.6999 |
| 2025-26 | 让分 | 157 | 0.2619 / 0.7191 | 0.2810 / 0.7606 | 0.2463 / 0.6868 |
| 2025-26 | 大小分 | 156 | 0.2483 / 0.6897 | 0.2467 / 0.6865 | 0.2424 / 0.6782 |
| 2024-25 | 胜负 | 160 | 0.1974 / 0.5781 | 0.2012 / 0.5880 | 0.1798 / 0.5402 |

本季调参对分差分布选了零权重，因此胜负、让分保留原模型。大小分先用早期数据校准，在 33 场诊断样本中相对赛前市场的 log-loss 差为 -0.0145，按连续两个比赛日分块、2,000 次配对自助法的区间约 [-0.0834, +0.0544]。2025–26 大小分校准后 log-loss 0.6865，市场为 0.6782。2024–25 取样缺少可核验的全场让分和大小分盘口。

三季全部配对合约规则覆盖如下。

| 赛季 | 明文含加时 胜负 / 让分 / 大小分 | 未明示 胜负 / 让分 / 大小分 |
|---|---:|---:|
| 2026-27 | 86 / 0 / 0 | 0 / 85 / 81 |
| 2025-26 | 156 / 0 / 0 | 0 / 157 / 156 |
| 2024-25 | 0 / 0 / 0 | 160 / 0 / 0 |

## 数据与复现

- 本季用保留的 30 份 ESPN 队赛程和对应总结页，通过适配器的纯解析函数重建原快照，球队比赛和伤病表完全一致。工作树的原验证命令在缓存重建环节报 `public_fetch_not_allowed`，本次直接用保留的本地原始缓存运行该项。原 WP-NBA 30 场逐场胜负概率最大误差 1.11e-16。 两个旧季用 ESPN 赛程核对 Basketball Reference 两边分数和比赛身份，每季 30 队各 82 场。排除不计常规赛的 NBA Cup 决赛及季后赛。 两季各按时间抽 6 场，ESPN 总结页的 FGA、FTA、OREB、TOV 与 Basketball Reference 双方逐项一致。 [ESPN 总结页示例](https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=401809234) · [Basketball Reference 球队日志示例](https://www.basketball-reference.com/teams/BOS/2026/gamelog/) · [NBA Cup 规则](https://www.nba.com/news/nba-cup-101)
- WP-NBA 保留 6 个变体及原逐日截断。拟合阶段仅使用训练段模型预测与实际分差、总分，在接续的调参段为校准挑选固定权重，再做留出评分。模型在各预测日只更新当天之前的同赛季信息。赛季汇总和市场价格均未进入特征。留出以新日期完整划分，美东日期跨度和精确参数见结果文件。
- 大小分与让分使用完整比赛比分。近季按 ESPN 的 DraftKings 留存档位寻找完全相同的 Polymarket 合约，DraftKings 两边的去水概率单独保留为缺少报价时间的收盘代理。旧季从每个留出段按赛程等距抽 160 场，取各合约最早明确发布的全场盘口，市场和两个模型评分使用同一条线。两队身份在 ESPN 与 Gamma 逐场对齐。2024–25 两份误标为大小分的合约按胜负结算说明和球队结果重识别。 [Polymarket Gamma](https://gamma-api.polymarket.com)
- 市场概率来自 Polymarket CLOB 的双边历史价格，取 ESPN 与 Gamma 较早的开球时间前 5–25 分钟且两边观测时间相差不超过 5 分钟的最后可用价格。原 WP 工具使用接近开球时最后一笔，这里设置五分钟缓冲。逐行保存时间戳、合约 ID、盘口点位、价格、真实结算与预测。全部 881 张合约留有完整原文、NBA 结算来源、Gamma 结果和 token 原始顺序、最终价格及闭市时间。可成交净收益和盘口深度仍待验证。 [Polymarket CLOB 价格历史](https://docs.polymarket.com/api-reference/markets/get-prices-history)

从本目录运行以下命令，使用 Python 3.12 和 requests。模型基线固定为 `80475c850c410b0efaa2e4387fa8532677e5a317`，该提交应存在于本地仓库历史。复算结果写入临时目录。JSON 中的 `repo` 记录本次运行路径，其余值需与归档一致。

```bash
(
set -e
repo=$(git rev-parse --show-toplevel)
replay_dir=$(mktemp -d)
baseline="$replay_dir/baseline"
git clone --quiet --shared --no-checkout "$repo" "$baseline"
git -C "$baseline" checkout --quiet --detach 80475c850c410b0efaa2e4387fa8532677e5a317
PYTHONPATH="$baseline/src:$baseline/tools" \
  python3 -B backtest.py --repo "$baseline" --output "$replay_dir/output"
cmp predictions.csv "$replay_dir/output/predictions.csv"
python3 -B verify_results.py --wp-reference wp-reference.json > "$replay_dir/verification.json"
python3 -B - "$replay_dir" <<'PYTHON'
import json
from pathlib import Path
import sys

replay = Path(sys.argv[1])
archived = json.loads(Path('results.json').read_text())
replayed = json.loads((replay / 'output' / 'results.json').read_text())
archived.pop('repo')
replayed.pop('repo')
if archived != replayed:
    raise SystemExit('NBA replay results differ from the archive')

archived_checks = json.loads(Path('verification.json').read_text())
for prior_check in ('predictions_byte_equal_to_rule_checked_prior_run',
                    'all_metrics_equal_to_prior_rule_checked_run',
                    'repo_nba_model_and_espn_adapter_tests_passed'):
    archived_checks.pop(prior_check)
if archived_checks != json.loads((replay / 'verification.json').read_text()):
    raise SystemExit('NBA replay verification differs from the archive')
PYTHON
)
```

验证涵盖逐行时戳与同盘口断言、结算方向、归档哈希核对和原报告复算。30 场主胜概率参照随归档提供，Gate A 当时使用的完整 ESPN 原始缓存由研究服务器留存。归档验证摘要还保留原运行的两项重跑比对记录和工作树原 NBA 模型、适配器的 99 项测试结果。 [30 场复算参照](wp-reference.json)

[完整结果](results.json) · [逐行预测](predictions.csv) · [验证摘要](verification.json) · [回测脚本](backtest.py)
