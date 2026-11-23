# 11 月 23 日足球和篮球运行回归

在仓库根目录使用已安装 `polymarket-alpha-lab[dev,postgres]` 的 Python。运行账户需要能在临时目录启动本地 PostgreSQL，并能通过 `pg_config --bindir` 定位 `initdb` 和 `postgres`。可使用 `--postgres-bin` 显式指定这一目录。

```sh
python -B docs/paper-sim/runtime/tests/run_offline.py
```

运行器启动只接受私有 Unix socket 的临时 PostgreSQL，创建测试数据库并在退出时删除集群。运行时使用虚构账本和已模拟的市场、阵容、路由与发送响应。临时目录可用 `TMPDIR` 指定，需符合 Unix socket 的路径长度限制。

| 来源文件 | 原始命名用例 | pytest 实例 | 覆盖 |
| --- | ---: | ---: | --- |
| `test_football_focus.py` | 9 | 9 | 足球关注筛选、已确认首发后的时间窗口、账本和并发幂等 |
| `test_weekend_football_model.py` | 10 | 10 | 七个联赛赛季模型、比赛匹配、进球概率校准和前瞻验证 |
| `test_sports_value_producers.py` | 6 | 6 | 足球和篮球报价、费后收益率排序、NBA 和 NCAA 压力情景 |
| `test_focus_early.py` | 5 | 5 | 足球提前观察、阵容冲突与正式盘口审核边界 |
| `test_lineup_history.py` | 8 | 30 | 历史首发证据、缓存时效、缺阵、球员身份和正式审核 |
| `test_correction_recovery.py` | 9 | 9 | 一次性纠错等待条件、重复提交防护和仅查询的账本状态判断 |

前五份测试保留 38 个原始命名用例。其中一项阵容测试展开为 23 个参数实例，加入一次性纠错回归后合计 69 个 pytest 实例。七个实例使用临时 PostgreSQL。使用 pytest 9 验证时另报告 49 个 `unittest` 子测试。
