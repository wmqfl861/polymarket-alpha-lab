# 足球和篮球模拟运行源码

[scripts/](scripts/) 收录现有运行的 27 个 Python 文件，包括足球和篮球的赛前采集、模型研究、盘口核验、准入、共享人民币模拟账本和微信投递。`systemd/` 收录 7 个用户服务模板和 9 个定时器。启动时从本机读取数据库和收件人绑定，研究代码从仓库读取已经归档的公开输入。运行需要 Linux 用户级 systemd、Python 3.11 及以上版本，以及能读取原数据目录的 PostgreSQL 发行版。

27 个业务脚本中，15 个与原运行源码逐字节相同，11 个调整了部署值、配置缺失处理或仓库路径，`football_shortlist.py` 只移除文件末尾的空行。9 个定时器与原文件逐字节相同，7 个服务文件使用本机配置模板。

## 交接到本机

1. 在仓库根目录建立 Python 环境，或使用本机已有的项目环境。通过该环境安装仓库及 PostgreSQL 依赖，例如 `python -m pip install -e '.[postgres]'`。运行目录在仓库内固定为 `docs/paper-sim/runtime`，以便脚本定位随库发布的研究档案和 `tools/` 工具。
2. 在服务账户的 `~/.config/pal-sports-paper/runtime.env` 中写入已有部署的配置，权限设为 `0600`。应用服务从这个文件加载环境变量。手动调用 Python 时，由同一服务账户通过本机环境提供同样的值。
3. 将定时器及填入本机值的服务单元放到服务账户的 `~/.config/systemd/user/`，执行 `systemctl --user daemon-reload`。表中的 `@…@` 是模板占位符，逐项替换为本机值后再加载单元。
4. 交接已有运行时，用 `systemctl --user list-timers --all` 核对原定时器启用状态。用 `systemctl --user start pal-football-notify-pg.service` 启动私有数据库，用 `systemctl --user enable --now <原启用的定时器>` 恢复原启用的调度。用同一账户运行 `python -B docs/paper-sim/runtime/scripts/sports_runtime.py status` 查看账本、篮球研究和结算状态。

| 服务模板占位符 | 本机值 |
| --- | --- |
| `@PAL_PYTHON@` | 安装了本项目及 psycopg 的 Python 解释器绝对路径 |
| `@PAL_RUNTIME_ROOT@` | 本文件所在目录的绝对路径 |
| `@PAL_WORKING_DIR@` | 服务工作目录的绝对路径 |
| `@PAL_POSTGRES@` | 与既有集群数据版本匹配、支持 systemd 通知的 `postgres` 可执行文件绝对路径 |
| `@PAL_PG_MOUNT_DIR@` | PostgreSQL 数据所在挂载目录的绝对路径，用于 `RequiresMountsFor` |
| `@PAL_PG_DATA_DIR@` | 已有集群数据目录的绝对路径，须与环境文件中的 `PAL_PG_DATA_DIR` 一致 |
| `@PAL_PG_PORT@` | 既有实例的端口编号，须与环境文件中的 `PAL_PG_PORT` 一致 |

| 环境变量 | 本机值 |
| --- | --- |
| `PAL_PG_SOCKET_DIR` | `%t/pal-football-notify-pg` 展开后的绝对路径，其中 `%t` 是服务账户的运行目录 |
| `PAL_PG_DATA_DIR`、`PAL_PG_PORT` | 与 PostgreSQL 单元一致的数据目录和端口 |
| `PAL_PG_DATABASE`、`PAL_PG_ROLE`、`PAL_PG_OWNER_UID` | 已有数据库名、PostgreSQL 角色和 Linux 数字 UID |
| `PAL_PAPER_ACCOUNT_KEY` | 已有模拟账本的 `restart_key` |
| `PAL_HERDR_CORE` | Linux 端 Herdr Core 可执行文件的绝对路径 |
| `PAL_HERDR_HUB_FINGERPRINT`、`PAL_HERDR_WINDOWS_FINGERPRINT` | 各自机器 ID 的小写 SHA-256 十六进制摘要 |
| `PAL_WINDOWS_HOST` | 已验证的 Windows 主机名，与 Herdr 机器记录和 Windows `COMPUTERNAME` 一致 |
| `PAL_OWNER_PROOF_ROUTE` | 原平台验证凭据中的 `route` 字符串。在从原验证文件初始化绑定时使用 |
| `PAL_LEGACY_STATE_DIR` | 原历史轮次验证文件和候选回执目录的绝对路径 |
| `PAL_LEGACY_RUNNER` | 可选的历史 `window_runner.py` 绝对路径 |
| `PAL_FOOTBALL_SEED_ROOT` | 可选的足球公开历史数据根目录，默认 `~/paper-sim` |
| `PAL_COLLEGE_SCORES_FILE` | 可选的 NCAA 已冻结公开赛果压缩文件，用原 SHA-256 校验 |
| `PAPERSIM_MANAGED_REPO_ROOT` | 可选的托管 PaperSim 工作树绝对路径，默认为本仓库根目录 |

PostgreSQL 服务通过权限为 `0700` 的 Unix socket 提供连接，`listen_addresses` 为空。应用检查 Unix 连接和数据库、角色、数据目录、socket 目录、端口是否与配置一致。接续现有运行使用原集群、原账本键、原队列和原收件人绑定。微信目标摘要和固定会话从该数据库读取。需要从既有凭据初始化绑定时，`football_daily.py init` 会校验原 `weixin-owner-verified.json` 和 `weixin-test-platform.json`。验证文件由原运行账户持有，权限为 `0600`。`sports_portfolio.py restart` 是创建指定账本代次的显式操作。

微信转发依赖已有的 Herdr 控制链路、Windows PowerShell、ZCode 的 `hermes-wechat-send` skill 及 Hermes CLI。程序在发起投递前校验绑定目标、机器指纹与 Windows 主机名。缺少数据库作用域配置会中止数据库操作，缺少有效路由或目标绑定会中止投递。

## 定时入口

| 定时器 | Python 入口 | 周期 |
| --- | --- | --- |
| `pal-football-notify-scan.timer` | `football_daily.py scan` | UTC 每 10 分钟，从整点开始 |
| `pal-football-notify-tick.timer` | `football_daily.py tick` | UTC 每分钟第 00 秒 |
| `pal-football-notify-digest.timer` | `football_daily.py digest` | 北京时间每天 08:00 |
| `pal-football-learning-study.timer` | `football_learning.py study` | UTC 每 10 分钟，从每小时第 02 分钟开始 |
| `pal-football-learning-admission.timer` | `football_learning.py admission` | UTC 每 10 分钟，从每小时第 04 分钟开始 |
| `pal-football-learning-evidence.timer` | `football_learning.py evidence` | UTC 每分钟第 12 秒 |
| `pal-sports-paper-scan.timer` | `sports_runtime.py basketball-scan` | UTC 每 10 分钟，从每小时第 08 分钟开始 |
| `pal-sports-paper-study.timer` | `sports_runtime.py basketball-study` | UTC 每小时第 04 分 20 秒 |
| `pal-sports-paper-settle.timer` | `sports_runtime.py settle` | UTC 每分钟第 43 秒 |

`pal-football-notify-worker@.service` 由队列按需启动，入口为 `football_daily.py worker <worker_id>`。`digest` 和 `send-note` 会返回 `schedule_notifications_disabled`。`sports_runtime.py basketball-scan` 也可在满足现有条件时入队微信提醒。

## 手动入口与历史依赖

- 状态与记录使用 `football_daily.py status`、`football_learning.py status`、`sports_runtime.py status`、`sports_portfolio.py status` 和 `sports_portfolio.py statement`。
- `football_daily.py scan --preview`、`football_shortlist.py --preview`、`football_focus.py --hours 24` 与 `sports_focus.py preview` 用于检查赛程、候选与消息预览。部分预览查询公开数据源。
- `football_daily.py route-check` 从数据库读取目标摘要并核验外部路由。`weixin_delivery.py --route-check` 接受由标准输入传入的绑定摘要。
- `sports_focus.py publish` 或 `football_daily.py send-focus --key <幂等键> --until <带时区的截止时间> --fixture <已核验的比赛标识> --body-file <UTF-8 正文文件>` 使用原入队流程。`send-focus` 支持 `--preview`。
- `football_domestic_study.py --help` 列出国内联赛进球研究及准入登记命令。`basketball_nba_ratings_replay.py` 使用已有 NBA 公开档案进行回放。`basketball_college.py` 执行 NCAA 研究注册，已有有效注册时沿用该注册。重建 NCAA 固定训练输入需提供与原哈希一致的赛果压缩文件，原公开下载链接可能已更新。

可选历史适配入口是 `legacy/window_runner.py`，其同目录包含 `window_assessment.py` 和 `window_news.py`。固定轮次的市场与模型快照、阶段评估、回执和验证文件从本机提供。缺少原证据时，历史回放按原规则降级为观察。 [历史轮次配置](legacy/README.md)

足球和篮球的正式范围读取还可用到工作树内既有的 `.local/postgres/instance.json` 和对应托管 PostgreSQL 实例。
