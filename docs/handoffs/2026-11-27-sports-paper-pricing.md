# 2026-11-27 足篮小额模拟定价交接

足球与篮球的人民币正式模拟单、零元重点关注，从本次部署起使用有正数挂量的最低有效卖价。报价记为 `price_basis=best_ask`。每份模拟成本为 `卖价 + 费率 × 卖价 × (1 − 卖价)`。整笔模拟份额按该成本估算，实际可成交的数量取决于挂量。

### 已部署的范围

| 场景 | 取价及复核 |
| --- | --- |
| 足球正式模拟单、零元关注 | 核对合约、方向、报价时效和费用，用最低有效卖价筛选。正式单另走准入和临场核验。 |
| 篮球正式模拟单、零元关注 | 临场模拟单和可核对两侧报价的关注项采用同一取价口径。费用、保守模型条件和球员可用性核验继续参与决策。 |
| 人民币模拟账本 | 按含费成本计算份额、盈亏平衡胜率及预期回报，先按保守回报排序。旧单使用各自入账时的价格与份额。 |
| 前向研究与正式晋级 | 足球前向、篮球前向及收盘报价继续按各自登记的走盘、深度和流动性规则计量。联赛解冻和晋级使用受管研究证据。 |

日常模拟已停用 250 美元走盘、近两美分挂量和 5,000 美元总流动性门槛。足球的球队预期进球模型和 NBA 的球队评级仍给出球队层面的预测。临场可因关键缺阵或来源冲突暂停方向。对指定两套球员名单分别计算并验证胜率，仍是尚缺的模型能力。

### 接手源码

共享模块的 SHA-256 为 `af17ad229c88073184ca3ba3abb8e270c0ae0bc148293e142f627bf38c51fc87`，与部署文件逐字节一致。八个调用端的完整差异以保留分支 `handoff/sports-runtime-20261123` 的 `bcce1b17f7263ab89859febfcb56188c295ab371` 为底本。本次发布前的项目主线为 `4a4ab7c9a78cb002257d5eaa3936c80e3f3ce51c`。[共享定价源码](../../src/polymarket_alpha_lab/papersim/sports_paper_pricing.py) · [八个调用端的差异](../../tools/sports_paper_handoff/daily-best-ask.patch) · [九个还原文件的 SHA-256](../../tools/sports_paper_handoff/SHA256SUMS)

还原后的六个调用端与已部署文件逐字节一致。`football_shortlist.py` 的差异仅为文件尾空行。`sports_portfolio.py` 沿用公开版本从环境取得模拟账本标识和配置缺失检查，份额、分配和入账算法与部署版一致。个人微信传输和现场数据由机主本机管理。运行层纳管进度见项目登记。[运行层登记](../../DELIVERY_PLAN.md#88-fx-51-足篮运行层的准入金额和模型来源)

发布前逐项核对了五个旧工作树中的本地改动。NBA 既有扫描命中的来源记录、FX-49 测试的时钟修正、NBA 和德篮甲的数据来源说明均已有主线记录。尚存的旧 Windows 诊断草稿和已暂停的 M2 写入原型保留在原工作树。[运行层来源登记](../paper-sim/design/fx-48/2026-11-26-source-and-timer-followup.json) · [M2 生效条件](../paper-sim/usage.md)

### 从干净检出复跑

在 Linux 仓库根目录以普通用户运行。需要 Python 3.12 或以上、Git、uv 及提供 `pg_config`、`initdb`、`pg_ctl` 和 `postgres` 的本地 PostgreSQL 安装。浅克隆或仅取了 `main` 的检出可用下面第一条命令取得固定底本。还原工具只写新目录。回归使用合成行情、仓库公开研究资料和临时 Unix socket 数据库。[还原工具](../../tools/sports_paper_handoff/restore.py) · [跨模块回归](../../tools/sports_paper_handoff/tests)

```bash
git fetch --no-tags https://github.com/wmqfl861/polymarket-alpha-lab.git handoff/sports-runtime-20261123
uv sync --locked --extra dev --extra postgres
pricing_tmp=$(mktemp -d)
.venv/bin/python tools/sports_paper_handoff/restore.py "$pricing_tmp/restored"

env -i PATH="$PATH" LANG=C.UTF-8 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_papersim_sports_paper_pricing.py tests/test_papersim_gates.py \
  tests/test_papersim_fees.py tests/test_papersim_optimize_rule_sources.py \
  tests/test_papersim_adapter_prices.py

env -i PATH="$PATH" LANG=C.UTF-8 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  PAPERSIM_MANAGED_REPO_ROOT="$PWD" PYTHONPATH="$pricing_tmp/restored/docs/paper-sim/runtime/scripts:$PWD/src" \
  .venv/bin/python -m pytest -q -p no:cacheprovider tools/sports_paper_handoff/tests
```

### 验证记录

| 范围 | 通过 | 失败 | 跳过 | 核验内容 |
| --- | ---: | ---: | ---: | --- |
| 本次仓库定价及既有闸门、费用、报价适配器与规则来源用例 | 246 | 0 | 0 | 含共享模块的 7 项新用例。 |
| 从公开底本还原的跨模块回归 | 14 | 0 | 0 | 覆盖足篮四条日常路径、过期和缺盘、研究深度口径以及临时 PostgreSQL 实际模拟入账。 |
| 此前部署后的针对性回归 | 49 | 0 | 0 | 包括日常定价、小额账本、前向隔离与不利阵容。 |
| 此前本机完整运行层套件，改动前 | 102 | 22 | 2 | 22 个失败用例与候选轮逐项相同。 |
| 此前本机完整运行层套件，部署候选 | 111 | 22 | 2 | 此轮早于最后补入的账本用例。旧投递替身及旧首发扫描字段相关失败仍有记录。 |

部署后的只读预览返回 5 个零元重点关注，5 个均带 `best_ask`，足篮两路均成功返回。该预览没有写入正式模拟单。独立账本回归用卖价 0.399、费率 5%、模拟金额 150 元，核对了实际入账份额和定价依据。表中各套件存在覆盖重叠，各自计数。
