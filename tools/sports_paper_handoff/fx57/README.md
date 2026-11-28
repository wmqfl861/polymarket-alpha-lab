# FX-57 小额模拟盘口证据候选

机主对 OD-AE 的决定为“维持卖一价”。本候选供离线复验，尚待部署。正式模拟单整笔按选定方向的卖一价计算份额、手续费和模拟收益。挂量仅用于报表。足球和篮球的零元关注保存同一组盘口证据，并显式记录零元及容量状态 `not_applicable`。研究和正式晋级继续使用原有口径。

每条新观察记录最低有效卖价、该价位所有有效正挂量之和（份）、方向资产和合约、交易所盘口时间，以及完整盘口对象的 SHA-256。摘要的输入为上游解码后的单个盘口 JSON 对象，包含完整的买卖两侧和其余字段，以 UTF-8、键排序、紧凑分隔符编码。批量响应里的每个方向都有自己的摘要。

正式账本的 `status` 输出 `by_best_ask_coverage`，逐类给出笔数、投入、未结敞口和已实现盈亏。`statement` 可查看每笔的挂量、摘要、容量和原因。

| 报表口径 | 算法 |
| --- | --- |
| 主分类 `sufficient` / `insufficient` | 卖一价 × 卖一份数 × 固定的每美元人民币数，与整笔人民币模拟投入比较。等额计为充足。 |
| `fee_inclusive_share_status` | 将人民币投入折成美元，再用每份卖一价加手续费计算可购份数，和卖一份数比较。 |
| `unknown` | 旧单或证据缺失、盘口身份不匹配、缺当时已登记汇率。已入账价格和盈亏保留。 |

新观察固定按 1 美元折 6.654376 元人民币作报告。欧洲央行 2026-11-27 参考值为每欧元 1.0957 美元、7.2912 元人民币。两者相除并保留六位小数。汇率版本为 `ecb-cross-2026-11-27-v1`，原始参考值、摘要及接收时间随证据保存。早于登记时点的历史观察使用 `unknown`。[ECB 参考汇率](https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml)

临时数据库的一个用例用 150 元模拟投入、0.40 卖一价、1 份卖一、0.80 第二档。整笔模拟仍以 0.40 计价，该笔在不足组。另一个用例将卖一改为 55 份，卖一名义额是 22 美元，折合 146.396272 元。该笔仍在不足组，同时含手续费可购的份数小于 55。两个用例均核对 `status` 实际输出、已实现盈亏以及重放后笔数。

## 在干净检出复跑

Linux 下需要 Git、Python 3.12 或以上、pytest、psycopg，以及提供 `pg_config`、`initdb`、`pg_ctl`、`postgres` 的本地 PostgreSQL 软件。模拟账本回归使用临时 Unix socket 实例。复跑从仓库根目录执行。

```bash
git fetch --no-tags https://github.com/wmqfl861/polymarket-alpha-lab.git handoff/sports-runtime-20261123
uv sync --locked --extra dev --extra postgres
fx57_tmp=$(mktemp -d)
.venv/bin/python tools/sports_paper_handoff/restore.py --release 2026-11-28 "$fx57_tmp/candidate"

env -i PATH="$PATH" LANG=C.UTF-8 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  PAPERSIM_MANAGED_REPO_ROOT="$PWD" PYTHONPATH="$fx57_tmp/candidate/docs/paper-sim/runtime/scripts:$PWD/src" \
  .venv/bin/python -m pytest -q -p no:cacheprovider tools/sports_paper_handoff/tests \
  tools/sports_paper_handoff/fx57/tests tests/test_papersim_sports_paper_pricing.py
```

11-27 发布版保留在提交 `a56b7d03f73c405685446c475919f63879ed7aaf`。原还原命令默认选择该版本，显式指定也可。两版都从固定底本 `bcce1b17f7263ab89859febfcb56188c295ab371` 独立还原八个适配器，分别校验八个适配器及一个共享模块。11-27 提交和对应的原始校验清单决定历史共享模块内容。[原交接及复跑命令](../../../docs/handoffs/2026-11-27-sports-paper-pricing.md) · [本候选完整差异](daily-best-ask.patch) · [本候选九个 SHA-256](SHA256SUMS)

```bash
.venv/bin/python tools/sports_paper_handoff/restore.py --release 2026-11-27 "$fx57_tmp/deployed-nov27"
env -i PATH="$PATH" LANG=C.UTF-8 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  PAPERSIM_MANAGED_REPO_ROOT="$PWD" PYTHONPATH="$fx57_tmp/deployed-nov27/docs/paper-sim/runtime/scripts:$PWD/src" \
  .venv/bin/python -m pytest -q -p no:cacheprovider tools/sports_paper_handoff/tests
```
