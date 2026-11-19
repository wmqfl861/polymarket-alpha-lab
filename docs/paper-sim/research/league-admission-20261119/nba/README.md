# NBA 回测文件

[验收报告](report.md) 包含历史区间、冻结的时间留出、市场口径、门槛与复现命令。

- 严格赛前组 `PM-explicit-full-game-OT` 有双边赛前价格，且合同明文按含加时的最终比分结算。
- 终场比分诊断组 `PM-NBA-final-score-diagnostic` 有双边赛前价格，合同未逐笔明示是否含加时。整组用 NBA 全场终场比分。
- DraftKings 收盘代理组 `DK-postfinal-close-proxy` 保留 ESPN 赛后接口显示的收盘档，历史报价时间不可核验。

两组 Polymarket 合约均核对队伍、比赛、盘口阈值、方向、Gamma 结果和 token 顺序，以及最终价格与 NBA 终场比分。完整合同原文、赛前观测和终态保存在 `pm-*.json.gz`。球队比赛输入保存在 `games-*.json.gz`。

在本目录运行 `python3 -B verify_results.py --wp-reference wp-reference.json` 可复核归档哈希、赛前时间窗口、同盘口配对、终态方向和原报告的 30 场胜负概率。原结果的 `walk_forward_records` 提取日期、主客队和主胜概率四列，随附原结果 SHA-256。 [原验证报告](wp-reference-report.md) · [30 场复算参照](wp-reference.json)

[结果 JSON](results.json) · [逐行预测](predictions.csv) · [回测脚本](backtest.py) · [验证脚本](verify_results.py) · [验证结果](verification.json)
