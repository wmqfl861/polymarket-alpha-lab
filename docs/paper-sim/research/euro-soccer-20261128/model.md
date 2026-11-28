# 五个联赛的球队与赛前新闻模型

版本 `euro-soccer-venue-lineups-20261128-v1` 对常规赛 90 分钟胜平负和大小 2.5 球给出两套概率。基础预测使用球队在相同主客角色下的记录。另一套预测按赛前已知的阵容或伤停重新估计。每条观察的研究金额为 0 元。[联赛和市场登记](registration.md)

## 球队基线

取同一联赛、同一常规阶段、本赛季和上一赛季中，预测时已经取得的正常完赛比分。主队至少有三场主场历史，客队至少有三场客场历史。每场各占一份权重。若双方 xG 都可用，该场的攻守表现取实际进球与相应 xG 的均值。其余场次使用实际进球。目标主队的期望进球取本队历史主场进攻与对手历史客场失球的均值，客队方向相反。然后用项目既有的 Dixon–Coles 比分分布和同一低比分相关系数计算胜平负和大小球概率。[球队模型](../../../../src/polymarket_alpha_lab/papersim/euro_soccer_model.py) · [比分分布](../../../../src/polymarket_alpha_lab/papersim/soccer_model.py)

## 球队新闻如何影响概率

新闻输入来自 FotMob 的当场结构化球队报道。采集与预测都早于开球，两者间隔最多五分钟。校验原文 SHA-256、来源 URL、赛事编号、双方球队、开球时刻和未开赛状态。已公布十一人、预测十一人、上一场名单各有独立标记。明确伤病、停赛、疾病与国际征召按球员编号记录，待定状态单列。[新闻解析](../../../../src/polymarket_alpha_lab/papersim/euro_soccer_team_news.py)

- 有当场完整首发或预测名单时，历史比赛的权重取两场首发中相同球员的人数。权重同时作用于该队历史的进攻和防守表现。预测名单始终保留来源的预测标记。
- 只有明确伤停时，权重取历史十一人中本场未被明确列为缺阵的人数。出场存疑不按确定缺阵处理。上一场的首发仅在对应的历史比赛完赛后才可作为球队样本。
- 默认核对双方最近五场相同主客角色的详情。至少三场历史有正的匹配权重，且各场权重存在差异时，才使用球队情景。再把有首发的历史场次总权重归一到原场数，保留其余比赛的基线贡献。一方证据不足时沿用该队基线，两方都不足时只报告基础预测。[历史数据获取](../../../../src/polymarket_alpha_lab/papersim/euro_soccer_sources.py)

## 实际输入抽验

2026-11-28 06:32 UTC，从五个联赛各选一场临近的未赛常规赛。临时 PostgreSQL 逐份登记并校验了 65 个公开原件，包括 10 个赛历、5 个赛前详情和 50 个历史详情。十支球队各有五场历史取得双方 xG 及完整首发。表内百分比是当时的研究预测，主胜列展示球队新闻对同一场预测的影响。

| 联赛与比赛 | 同主客角色历史场数（主/客） | 本场使用的新闻 | 主胜基线 | 主胜情景 | 当场来源 |
| --- | ---: | --- | ---: | ---: | --- |
| 葡超 Arouca – Rio Ave | 22 / 22 | 预测首发 | 44.90% | 44.68% | [FotMob](https://www.fotmob.com/api/data/matchDetails?matchId=5887682) |
| 苏超 Aberdeen – Dundee FC | 23 / 24 | 明确伤停 | 46.26% | 45.83% | [FotMob](https://www.fotmob.com/api/data/matchDetails?matchId=5793982) |
| 比甲 Westerlo – RAAL La Louvière | 21 / 21 | 明确伤停，仅主队有历史差异 | 43.25% | 43.08% | [FotMob](https://www.fotmob.com/api/data/matchDetails?matchId=5811826) |
| 挪超 Bodø/Glimt – KFUM | 28 / 28 | 预测首发 | 70.74% | 71.18% | [FotMob](https://www.fotmob.com/api/data/matchDetails?matchId=5105057) |
| 土超 Samsunspor – Gençlerbirliği | 23 / 22 | 明确伤停 | 47.33% | 47.54% | [FotMob](https://www.fotmob.com/api/data/matchDetails?matchId=5904820) |

这五场抽验验证了数据读取与模型计算。球队报道仅取上述一个公开结构化来源，名单相似度描述历史相关性，球员个人的因果效应与模型的前向盈利能力尚待验证。受管观察在开球前登记原预测，赛后与对应的正常完赛比分核对。状态报告分别给出赛前与收盘两个窗口的 Brier 误差。每个窗口、每个市场的“基础预测与市场”和“阵容情景与市场”各自只用双方同时有值的同场样本，报告对应样本数、各自均值和模型减市场的差值。数值越低，样本上的概率误差越小。[逐场计分](../../../../src/polymarket_alpha_lab/papersim/euro_soccer_outcomes.py) · [研究报告](../../../../src/polymarket_alpha_lab/papersim/euro_soccer_report.py)
