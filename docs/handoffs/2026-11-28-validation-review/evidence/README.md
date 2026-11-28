# 证据目录（2026-11-28 第二次）

基线 `eaf6e8149f83a708d09158529ec774f4fbed8b9d`，被审核的 `main` 是 `2fc69b0560b073e05718ea335c05388872756e66`。

脚本都是只读的，不向仓库写入。输出里不含服务器地址、端口、邮箱、机器标识、收件人标识和派生仓库所有者的名字。

| 文件 | 内容 | 怎么得到 |
| --- | --- | --- |
| `opening_checks.txt`、`pr_states.tsv` | 可见性、`main` 的保护、规则集、派生仓库、服务器上的引用数、四个 PR 的状态；开头和发布前各读一次 | `bash collect_api.sh <仓库目录> <输出目录>` |
| `local_facts.txt` | 报告引用的本地事实：范围统计、owner 的答复和投递条目的去向、小额定价、历史回测、测试、分支规则读回、提交身份的域名 | `bash collect_local.sh <仓库目录> <输出目录>` |
| `published_recomputation_run.txt` | 本地随历史回测发布的三条复算命令，由审计方在独立检出里各跑一次的输出 | 见文件开头 |
| `independent_backtest_analysis.py`、`independent_backtest_analysis_output.txt` | 审计方用五联赛逐场导出另做的八项计算 | `python3 -I independent_backtest_analysis.py <new-five-fixtures.json.gz 的路径>`；只用标准库，随机种子固定，约半分钟 |
| `test_weakening_scan_output.txt` | 测试文件有没有被删、被放宽、被跳过 | `python3 -I test_weakening_scan.py <仓库目录> <基线> <main 提交>` |
| `added_line_sensitive_scan_output.txt` | 新增行里像敏感字符串的命中，只有分类计数 | `python3 -I added_line_sensitive_scan.py <仓库目录> <基线> <main 提交>` |
| `address_occurrence_output.txt` | 像公网地址的字符串在两个提交里的出现次数，不打印值 | `python3 -I address_occurrence_diff.py <仓库目录> <基线> <main 提交>` |
| `offline_suite_head.log`、`offline_suite_results.txt` | 审计方自己跑的离线测试：原始输出，和上午基线结果的逐项比较，编译步骤 | `make_test_env.sh` 建环境，`run_offline_suite.sh` 跑测试，`junit_compare.py` 比较，`run_compile_step.sh` 跑编译 |

## 几点说明

- **另做分析的算法。** 脚本开头的说明列了八个问题。校准斜率和联合检验用逻辑回归的最大似然估计；区间按 ISO 周做整块重采样 400 次；"照分歧下注"按不含水的收盘概率的倒数当赔率，不含任何费用；样本量一栏是让 95% 区间有一半机会不跨过零所需的场数，按单场 Brier 差的标准差算。所有数字都是样本内的，除了第 7 项（前一半拟合、后一半检验）。
- **敏感字符串扫描。** 这一轮只有 10 处通用的家目录写法，不含用户名。没有邮箱、标识或密钥形态的命中。地址类字符串在两个提交里共 4 个不同的值，位置和次数没有变化。
- **测试放宽扫描。** 它只认字面的 `skip`、`xfail` 标记。FX-59 给一个用例加的是仓库自己定义的 `_D1_NATIVE` 标记，扫描器不计入，审计方人工核对过，那正是上一轮要求的改动。
- **`opening_checks.txt` 里的"工作流运行 1 次"。** `main` 上没有工作流文件。那一次是 GitHub 自己的依赖关系图更新（事件类型 `dynamic`，11-28 07:52 UTC），不是项目的 CI。
- **离线测试环境。** 和前两轮相同：Python 3.12，pytest 9.1.1，仓库的 `src` 用路径文件加入，时区数据用操作系统自带的一份，不装仓库的其余依赖，不连数据库。
