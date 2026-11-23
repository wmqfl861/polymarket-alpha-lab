# 2026-11-22 固定足球窗口

`window_runner.py` 保留五场固定赛程的 T−90 与 T−30 时间窗、市场方向和评估格式。`window_news.py` 比对固定赛程临场与上午采集的 FotMob 阵容和缺阵记录。`window_assessment.py` 核验原输入哈希、联赛与队伍、盘口条款和报价，并在原窗口内通过公共 FotMob、Polymarket Gamma 与 CLOB 读接口复核。

在仓库根目录用已安装本项目的 Python 运行离线渲染。样例使用固定赛程里公开的比赛和市场编号，报价、概率和流动性为虚构值。

```sh
python -B docs/paper-sim/runtime/legacy/window_runner.py 5781804 90 \
  --render-only docs/paper-sim/runtime/legacy/example-assessment.json \
  --as-of 2026-11-22T12:02:00Z
```

输出含 `status="paper"`、`reason_code="qualified"` 与有效期 `2026-11-22T12:06:30+00:00`。将 `--as-of` 改为 `2026-11-22T12:07:00Z` 会得到 `status="watch"` 与 `reason_code="clob_book_stale"`。渲染仅打开给定 JSON，可对本机留存的真实 `window-assessment.json` 使用同一入口。

## 历史输入

共享足球脚本通过 `PAL_LEGACY_RUNNER` 读取本目录的 `window_runner.py`。原采集包可通过以下绝对路径提供。

| 环境变量 | 外部数据 |
| --- | --- |
| `PAL_LEGACY_WINDOW_DIR` | 阶段的根目录，各子目录名为 `<fixture_id>-90` 或 `<fixture_id>-30`。包含固定 `model-assessment.json`、`markets-prepared.json`、`prematch-source.json`、`fotmob-prematch-<fixture_id>.json` 和 `shortlist-price-recheck.json`。运行结果为 `window-news.json`、`window-assessment.json` 和 `window-notice-status.json`。 |
| `PAL_LEGACY_BASELINE_DIR` | 原上午 `model-assessment.json`、`xg-verified-sports-window.json`、`fotmob-deltas.json`、`event-details-v2/<event_id>.json` 以及 `prematch-source.json`、`prematch-additional.json` 和对应 FotMob 快照。 |
| `PAL_LEGACY_EREDIVISIE_XG` | 原 Eredivisie 基准 xG JSON，模型重算需要使用原文件哈希。 |
| `PAL_LEGACY_APPROVAL_FILE` | 仅原窗口采集入口读取的本地授权 JSON，检查 `verified` 是否为 `true`。 |

原窗口固定于 2026-11-22，采集和实时评估在窗口结束后自动关闭。完整再现历史评估依赖本机保留的原快照及当时的上游数据。此归档提供源码、固定输入校验和离线渲染样例。原快照、平台回执、授权文件及一次性调度留存于原运行环境。

`correction_recovery.py` 归档 11 月 23 日一次性纠错中使用的状态判断、仅查询的 PostgreSQL 语句、元数据响应解析和请求回调边界。调用方提供时间、纠错键与回调，相关 [离线回归](../tests/README.md) 使用虚构参数。现场轮询入口、收件人、路由连接与会话数据留在原运行环境。
