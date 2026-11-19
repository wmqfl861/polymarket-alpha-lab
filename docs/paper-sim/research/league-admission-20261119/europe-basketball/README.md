# 欧洲篮球赛前模型与盘口验收

**九个联赛已完成赛果模型回测。EuroCup 总分和德国 BBL 总分可继续作为纸面观察候选。九个联赛的胜负、让分和总分均缺少交易层验收证据。** 已核对 19,388 场独立赛果，包括 2025–26 赛季留出集 2,532 场和后续赛季 551 场。Polymarket 严口径价格比较覆盖 1,600 份合约、961 场独立比赛。价格资料包含两边 token 的历史采样值。当前资料缺少逐场历史可成交卖价、深度和成交执行结果，因此无法给出扣费净收益。

## 独立赛果留出测试

每个联赛独立建模。2018–19 为热身，2019–20 至 2021–22 训练，2022–23 至 2024–25 选择参数，固定后再跑 2025–26。2026–27 是单列的后续观察期，数据窗口截于 2026-11-19 00:00 UTC。过去赛果可逐步更新 Elo 和球队状态，模型系数和调参选择保持冻结。日级预测在档案比赛日 00:00 UTC 生成。准确开球时间的赛果在开球六小时后可进模型，只有比赛日期的赛果在该日期后第二天 00:00 UTC 可进模型。这两项是回放使用的可用时点假设。赛果标签用官方或档案全场最终比分，包含加时。国内联赛第三方比分未提供中立场或逐场加时标签时，比赛按档案主客场字段处理。

胜负基线为固定 Elo，总分基线为最近 250 场联赛平均，让分基线为固定 Elo 换算主客分差。候选胜负在调参段选择 Elo K 值、主场优势、跨赛季衰减、分差权重和概率斜率。候选总分和让分用联赛近期得分、各队近期得失分和休息天数进行岭回归。总分和分差转为盘口概率时，以早期模型在调参段的残差估计误差分布。来源于联赛赛果，未使用赛后修订的赔率、阵容、伤病或盘口结果作模型输入。误差区间为按比赛周配对自助抽样的 95% 区间，未做多重比较校正。

| 联赛 | 2025–26 留出 | 胜负 Brier 基线 → 候选 | 总分 MAE 基线 → 候选 | 分差 MAE 基线 → 候选 |
| --- | ---: | ---: | ---: | ---: |
| EuroLeague | 402 | 0.2219 → 0.2197 | 14.66 → 14.27 | 9.15 → 9.07 |
| EuroCup | 195 | 0.2219 → 0.2146 | 15.95 → 14.42 | 12.13 → 11.76 |
| Basketball Champions League | 96 | 0.2252 → 0.2045 | 15.75 → 15.53 | 11.85 → 10.93 |
| 西班牙 ACB | 327 | 0.2071 → 0.2053 | 13.79 → 13.40 | 10.21 → 10.17 |
| 德国 BBL | 338 | 0.2226 → 0.2208 | 14.09 → 13.51 | 11.42 → 11.20 |
| 意大利 Serie A | 253 | 0.2107 → 0.2045 | 13.32 → 13.83 | 8.80 → 8.88 |
| 法国 Pro A | 266 | 0.2048 → 0.2025 | 13.44 → 12.94 | 11.40 → 11.02 |
| 法国 Pro B | 401 | 0.2219 → 0.2224 | 12.85 → 12.74 | 9.91 → 9.89 |
| VTB United League | 254 | 0.1559 → 0.1409 | 13.16 → 13.16 | 11.25 → 10.17 |

EuroCup 总分的候选减基线 MAE 为 −1.54 分，95% 区间为 [−2.41, −0.61]。德国 BBL 同一指标为 −0.59 分，区间为 [−1.14, −0.02]。意大利总分误差上升 0.51 分，区间为 [+0.12, +0.88]，应保留基线对照。BCL、意大利和 VTB 的胜负 Brier 有留出改善，三者在各自匹配的 Polymarket 样本中仍高于市场。

BCL 历史 FIBA 档案使用常规赛，2025–26 留出从 2025-10-07 至 2025-12-17。2026–27 增补 64 场 BetExplorer 主赛程比分，历史淘汰赛和当前资格赛未纳入。西班牙、法国、德国、意大利赛果来自 Kaggle v3，2026–27 收录截止分别为 10 月 18 日、10 月 18 日（Pro B 为 17 日）、10 月 11 日、10 月 18 日。EuroLeague、EuroCup 和 VTB 的联赛主办数据收录至 11 月 18 日。 [EuroLeague / EuroCup 接口说明](https://hoopr.sportsdataverse.org/reference/euroleague_games.html) · [FIBA BCL 档案](https://www.fiba.basketball/en/history/112-fiba-mens-european-club-competitions-tier-1) · [BCL 当前比分](https://www.betexplorer.com/basketball/europe/champions-league/results/) · [Kaggle 篮球数据 v3](https://www.kaggle.com/datasets/archangel0x01/european-club-basketball-results-and-odds) · [VTB 新版接口说明](https://hoopr.sportsdataverse.org/reference/vtb_league_matches.html) · [VTB 历史接口说明](https://py.sportsdataverse.org/docs/vtb/reference/vtb_infobasket/other)

## Polymarket 赛前实际盘口价格比较

用 Gamma 的联赛主标签查询已经结算且结算时间早于截止时点的合约。赛事连接按联赛、比赛日期和双方队名，确认精确赛果方向。每份合约按当时已上架的二元市场和确切的半分线计算概率。价格锚点为 Gamma 赛事开赛、合约开赛及独立比分源开赛时间中最早一个的赛前 60 分钟。三个时点之差大于 15 分钟时排除。比分档案只有日期时，补用独立逐场档案明确列出的开球时刻，并核对日期、球队和最终比分。仅有平台时间的样本保留在诊断中。需两个 token 各自在锚点前 15 分钟内有价格点，时间相差至多 5 分钟，两价之和在 [0.90, 1.10]，以相对两价之和归一。严格样本采用所存市场条款中明确写入全场结果包含加时的合约。比分和结算发生歧义的场次在逐行文件中列明，最终匹配样本独立比分方向与结算均一致。采样有时间戳和 token ID，盘口线来自该合约。 [Polymarket Gamma 赛事](https://docs.polymarket.com/api-reference/events/list-events) · [CLOB 批量价格历史](https://docs.polymarket.com/api-reference/markets/get-batch-prices-history)

各格为**严格匹配场次 / Gamma 对应盘口份数**，随后为 **Brier 基线 / 候选 / 市场**。每一盘口内严格匹配份数等于独立场次，合计时同一场的胜负、总分和让分分别计数。`—` 表示该查询未见这一盘口。完整留出段与后续段的分项在 `market_metrics.json`。

| 联赛 | 胜负 | 总分 | 让分 | 独立开球来源 |
| --- | --- | --- | --- | --- |
| EuroLeague | 0/101，存档规则的加时措辞不明 | 78/80 · .2728/.2500/.2354 | 78/80 · .2622/.2646/.2512 | 联赛数据 |
| EuroCup | 128/128 · .2233/.2162/.1813 | 127/128 · .2584/.2545/.2532 | 127/128 · .2775/.2641/.2386 | 联赛数据 |
| BCL | 61/144 · .2135/.2040/.1941 | — | — | BetExplorer 逐场档案 |
| 西班牙 ACB | 172/268 · .2202/.2220/.2183 | 36/71 · .2521/.2235/.2472 | 36/71 · .2834/.2643/.2514 | BetExplorer 逐场档案 |
| 德国 BBL | 134/188 · .2100/.2062/.2054 | 41/80 · .2193/.2127/.2428 | 41/80 · .2505/.2405/.2365 | BetExplorer 逐场档案 |
| 意大利 Serie A | 119/205 · .2120/.2087/.1997 | 35/64 · .2213/.2201/.2245 | 36/65 · .2191/.2107/.2356 | BetExplorer 逐场档案 |
| 法国 Pro A | 183/222 · .2044/.2023/.1837 | 41/76 · .2762/.2522/.2381 | 41/76 · .2559/.2584/.2525 | BetExplorer 逐场档案 |
| 法国 Pro B | — | — | — | — |
| VTB United League | 86/119 · .1283/.1148/.1060 | — | — | 联赛数据 |

- EuroLeague 78 场总分与让分严格样本均在 2026–27 段，各含 6 场确认加时。胜负 101 份合约全部缺少明确的计加时条款，97 份有可对齐价格的合约仅供诊断，其中 91 场常规结束、6 场加时。结算规则字段为准，个别赛事页面的 Market Context 问答虽提及加时，亦存在比赛日期与正式规则字段不一致。Besiktas–Barcelona 的 Gamma 合约原列 10 月 1 日 17:00 UTC，联赛比分档案 E2026_25 记录 10 月 2 日 17:00 UTC，故退出严格价格样本。 [Gamma 赛事页](https://polymarket.com/event/euroleague-besiktas-barcelon-2026-10-01) · [EuroLeague 2026–27 比分接口](https://api-live.euroleague.net/v2/competitions/E/seasons/E2026/games) · [EuroLeague 决赛页面与问答](https://polymarket.com/sports/euroleague/euroleague-olympiac-madrid-2026-05-24)
- EuroCup 的 128 场赛事覆盖 2026–27 当前段。128 场胜负有 4 场加时，127 场总分和 127 场让分各有 3 场加时。总分候选对市场 Brier 差 +0.0013，95% 区间 [−0.0099, +0.0139]，可继续观察校准。胜负较市场 Brier 差 +0.0350，[+0.0083, +0.0574]。 [EuroCup 比分接口](https://api-live.euroleague.net/v2/competitions/U/seasons/U2026/games) · [Polymarket EuroCup](https://polymarket.com/sports/bkeurocup)
- 德国 BBL 总分 41 场通过独立时钟确认，留出段 8 场，后续段 33 场。候选减市场 Brier 差 −0.0301，95% 区间为 [−0.0696, −0.0013]。一场 ALBA Berlin–Bamberg 因 BetExplorer 18:30 UTC 与 Gamma 16:30 UTC 相差两小时而排除。西班牙 ACB 总分与意大利让分的候选 Brier 点估计也低于市场，区间跨零。 [德国 BBL 逐场档案](https://www.betexplorer.com/basketball/germany/bbl/results/) · [ALBA–Bamberg 开球档案](https://www.betexplorer.com/basketball/germany/bbl-2025-2026/alba-berlin-bamberg-baskets/dKAx3pAd/)
- BCL 的 61 场胜负均来自 2026–27 常规赛。144 份胜负合约有 80 场欠缺可匹配的独立比分，主要涉及资格赛、历史附加赛及淘汰赛。VTB 的 86 场分为留出段 49 场和后续段 37 场，13 份胜负因两个开球来源相差超过 15 分钟排除。 [FIBA 档案](https://www.fiba.basketball/en/history/112-fiba-mens-european-club-competitions-tier-1) · [VTB 2025–26 比分接口](https://api.vtb-league.com/v2/leagues/vtb/seasons/2026/matches?limit=1000)
- 意甲 2026-05-10 的七场终轮比赛在 Gamma 标为 18:00 UTC，独立赛前日程和赛后逐场档案均给出 15:00 UTC。以 Gamma 的名义 T−60 取价会落在独立开球后两小时，七场均排除。西班牙 ACB Zaragoza–Granada 的 Gamma 19:00 UTC 与独立 16:00 UTC 同样相差三小时。另有 16 场意甲胜负只有 Gamma 时钟，留在诊断中。 [意甲 5 月 9 日赛前日程](https://www.vunerebologna.it/calendario/lba-serie-a-2025-26-30a-giornata-calendario-e-programma/09/05/2026/) · [Napoli–Udine 逐场档案](https://www.betexplorer.com/basketball/italy/lega-a-2025-2026/basket-napoli-udine/t6dYYK0m/) · [Zaragoza–Granada 逐场档案](https://www.betexplorer.com/basketball/spain/acb-2025-2026/zaragoza-fundacion-granada/MP5CEou0/)


## 独立博彩公司资料

- Kaggle v3 的平均胜负收盘赔率来自赛后整理的历史结果页面，无逐场单笔报价时钟。按 2025–26 留出场次与比分对齐，EuroLeague 364 场的候选和收盘参考 Brier 为 0.2196 / 0.2168，EuroCup 195 场为 0.2146 / 0.1983。西班牙 ACB、德国 BBL、意大利和法国有同类对照，详见逐行文件。 [Kaggle 数据和字典](https://www.kaggle.com/datasets/archangel0x01/european-club-basketball-results-and-odds)
- 法国 Pro B 的博彩公司总分有 23 场留出和 5 场后续在赛前 60 至 75 分钟之间更新，覆盖 28 条确切盘口线和 28 场独立比赛，其中 3 场加时。档案缺少总分计加时的条款，所有 28 条只作定向诊断。Gamma 本次查询未见已经关闭的法国 Pro B 比赛。 [Kaggle 历史盘口文件](https://www.kaggle.com/datasets/archangel0x01/european-club-basketball-results-and-odds) · [Polymarket 体育分类](https://gamma-api.polymarket.com/sports)
- 先前赛前快照有 EuroLeague Valencia–Virtus Bologna 184.5 的单一 BetMGM 同线参考，来源页面标注发布时间为 11 月 18 日 23:00 UTC，两侧均为 −110。合约在此次截止窗口之后开球，保留为单场观察。11 月 18 日的 EuroCup Bourg–Napoli 样本页面可核对赛事，早期快照的独立总分页没有解析出可用的同时点两边报价。 [Valencia–Virtus 参考页](https://agentbets.ai/odds/euroleague/virtus-segafredo-bologna-vs-valencia-basket/) · [Bourg–Napoli 总分页](https://www.flashscore.com/match/basketball/basket-napoli-CMsg9VYk/jl-bourg-A5BNuIF5/odds/over-under/including-overtime/?mid=lU73FqWb)

## 文件与复现

- `game_predictions.csv` 为独立比分留出和后续段的逐场预测、最后可用输入的时间和真实比分。`prediction_metrics.json` 保留调参配置、Brier、对数损失、固定概率区间校准误差、MAE、RMSE 和区间。
- `polymarket_market_replay.csv` 含全部 2,374 份盘口记录、时间、token、独立比分源、规则标记和剔除理由。严格价格比较使用 `status = matched price history`，其他行对应诊断、无时点价格或不具备可比条件。`market_metrics.json` 单列规则不明的样本及两种开赛时钟来源。
- `sportsbook_closing_moneyline_comparison.csv` 是无单笔时钟的胜负收盘对照。`sportsbook_france_pro_b_timed_totals.csv` 是有时间戳、总分规则未验证的单独诊断。
- `frozen_inputs.tar.gz` 含离线复跑所需的 71 份已冻结输入，包括比赛档案、Gamma 清单、价格历史和两份已核验的独立开球表。Kaggle 压缩包保留实际读取的 `games.csv` 和 `closing_odds.csv` 原始字节。完整 Kaggle 原包与逐场独立时钟源网页由研究服务器留存。
- `source_manifest.json` 给出文件 SHA-256 和来源。验证记录 `verification.json` 确认所有 71 份输入的哈希、严格匹配时序、意甲七场与 ACB 一场时钟冲突，以及六份回放输出用冻结输入离线生成后逐字节相同。德国 BBL 和 BCL 的核验源逐场列于 `germany_bbl_bcl_verified_clocks.csv`。

在本目录执行以下命令可离线复跑。已验证环境为 Python 3.11.15、NumPy 2.4.6、requests 2.34.2。直接重新下载会受到赛事状态更新、页面时间显示和历史盘口快照变化的影响。

```bash
(
set -e
replay_dir=$(mktemp -d)
mkdir "$replay_dir/input" "$replay_dir/output"
cp -R . "$replay_dir/reference"
tar -xzf frozen_inputs.tar.gz -C "$replay_dir/input"
python3 -B backtest.py "$replay_dir/input" "$replay_dir/output"
python3 -B replay_markets.py "$replay_dir/input" "$replay_dir/output" \
  --verified-clocks "$replay_dir/input/clock_inputs/germany_bbl_bcl_verified_clocks.csv" \
  --verified-clocks "$replay_dir/input/clock_inputs/club_basketball_verified_clocks.csv"
python3 -B verify_outputs.py "$replay_dir/reference" "$replay_dir/output"
cmp verification.json "$replay_dir/reference/verification.json"
)
```
