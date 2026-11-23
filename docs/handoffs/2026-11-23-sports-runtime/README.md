# 足篮模拟运行与研究源码交接

项目仓库为 `wmqfl861/polymarket-alpha-lab`。本交接基于已合并的主线 `ab6edff2d64ac8aa5c2831d748b8730b63f8312d`，源码和使用说明保存在 `handoff/sports-runtime-20261123` 分支。向下一位操作者交接时，同时传递此次发布的完整提交号。PR #123 已完成模拟结算修复、四类日常操作免审规则及 FX-42 证据归档。[主线合并记录](https://github.com/wmqfl861/polymarket-alpha-lab/pull/123)

| 要接手的工作 | 源码与说明 |
| --- | --- |
| 足球和篮球的赛程扫描、模型、赔率筛选、模拟资金与结算、微信重点提醒 | [运行说明](../../paper-sim/runtime/README.md)和[27 个 Python 脚本](../../paper-sim/runtime/scripts/) |
| 定时器、队列与通知库依赖 | [systemd 源文件](../../paper-sim/runtime/systemd/)和运行说明中的本机配置表 |
| 历史足球固定窗口与运行层回归 | [历史适配源码](../../paper-sim/runtime/legacy/)和[离线用例](../../paper-sim/runtime/tests/) |
| 三项仍在研究的工作、原始提交、恢复补丁、未完成的准入条件 | [逐项交接](research/README.md)和[研究源码清单](research/sources.tsv) |
| 全部交付文件的 SHA-256、字节数与本地校验入口 | [SHA-256](MANIFEST.sha256)、[字节数](SIZES.tsv)、[校验程序](verify.py) |

三条研究线以原始提交分别保存在以下远端分支。它们的状态为待开发或验收。模拟策略准入继续使用各研究线在主线交付计划中的条件。

| 研究线 | 保留分支 | 完整 Git 提交 |
| --- | --- | --- |
| FX-40 市场准备 | `handoff/sports-fx40-20261123` | `7f92e0356888785e7b1e1bc88c24ffd1edbe8ac7` |
| FX-44 收盘价观察绑定 | `handoff/sports-fx44-20261123` | `3bc5a234023ee0f322e693e380ed115eb6491547` |
| FX-41 NBA v2 | `handoff/sports-fx41-nba-v2-20261123` | `256f8491c324b04e5dd9789c012ae84f654853f2` |

## 接手与核验

在新的 Linux 工作目录使用 Git、Bash、Python 3.12 和 GNU coreutils。先下载交接分支；将随交接提供的 40 位提交号放入 `EXPECTED_DELIVERY`，再检查代码。单独核验交付文件只需要标准库。

```bash
set -eu
: "${EXPECTED_DELIVERY:?set EXPECTED_DELIVERY to the full delivery commit}"
git clone --no-tags --branch handoff/sports-runtime-20261123 \
  https://github.com/wmqfl861/polymarket-alpha-lab.git pal-sports-handoff
cd pal-sports-handoff
test "$(git rev-parse HEAD)" = "$EXPECTED_DELIVERY"
git merge-base --is-ancestor ab6edff2d64ac8aa5c2831d748b8730b63f8312d HEAD
handoff=docs/handoffs/2026-11-23-sports-runtime
sha256sum --quiet -c "$handoff/MANIFEST.sha256"
python3 "$handoff/verify.py"
bash "$handoff/research/verify.sh"
```

最后一条命令从公开仓库取得各研究分支的基础版本，在临时工作树里校验并应用三份补丁，逐项输出基线及还原后的 Git tree。实际开发时，从表中的保留分支另开工作分支即可。研究分支中尚缺的输入、生产入口和独立验证已逐条列明。[各线验收要求](research/README.md)

运行层回归在 Linux 的普通用户下执行，需要本机 PostgreSQL 工具及仓库锁定的 Python 依赖。在仓库根目录运行以下命令。已有环境可直接使用其解释器执行第二行。测试使用虚构输入和独立的临时数据库。[运行层用例](../../paper-sim/runtime/tests/)

```bash
uv sync --locked --extra dev --extra postgres --python 3.12
.venv/bin/python -B docs/paper-sim/runtime/tests/run_offline.py
```

个人微信收件人和回执、私有数据库、既有模拟账户及其账本、固定窗口的原始现场快照由原部署保管。NCAA 初次重建还需要运行说明所列的冻结比分输入。核验发布包时无需联系收件人、操作线上账户或启动定时器。[本机配置](../../paper-sim/runtime/README.md)

接手人请回传完整交付提交、清单校验退出码、三条研究线打印的目标 tree，以及实际执行的运行层用例结果。任一检查失败时记录第一条错误及命令，保留失败检出供比对；查清原因后再继续部署相关操作。
