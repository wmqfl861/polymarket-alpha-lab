# 证据目录（2026-11-28）

基线 `9a64415ae8a191f4c0abc5276c661240e1920e8c`，被审核的 `main` 是 `eaf6e8149f83a708d09158529ec774f4fbed8b9d`。

脚本都是只读的：不检出、不执行仓库里的任何东西，不向仓库写入。输出里不含服务器地址、端口、邮箱、机器标识、收件人标识和派生仓库所有者的名字。

| 文件 | 内容 | 怎么得到 |
| --- | --- | --- |
| `opening_checks.txt`、`pr_states.tsv` | 可见性、`main` 的保护、规则集、派生仓库、服务器上的引用数、四个 PR 的状态；开头查一次，发布前再查一次 | `bash collect_api.sh <仓库目录> <输出目录>` |
| `local_facts.txt` | 报告引用的本地事实：范围统计、`AGENTS.md`、登记的闸门、小额定价、欧洲研究、投递现状、访问限制、分支清理、本地全量测试、提交身份的域名 | `bash collect_local.sh <仓库目录> <输出目录>` |
| `refs_delta.txt` | 两次拉取之间分支和 PR 引用的变化 | `python3 -I refs_delta.py <仓库目录> <之前的列表> <之后的列表> <main 提交>`；两份列表是审计方拉取前后各存的一份引用清单 |
| `removed_branch_reachability.txt` | 被删的 89 个分支，其提交是否仍能从留下的引用到达 | `python3 -I removed_branch_reachability.py <仓库目录> refs_delta.txt <main 提交>`；需要一个在删除之前拉取过的克隆 |
| `test_weakening_scan_output.txt` | 测试文件有没有被删、被放宽、被跳过 | `python3 -I test_weakening_scan.py <仓库目录> <基线> <main 提交>` |
| `added_line_sensitive_scan_output.txt` | 新增行里像敏感字符串的命中，只有分类计数 | `python3 -I added_line_sensitive_scan.py <仓库目录> <基线> <main 提交>` |
| `address_occurrence_output.txt` | 像公网地址的字符串在两个提交里的出现次数，不打印值 | `python3 -I address_occurrence_diff.py <仓库目录> <基线> <main 提交>` |
| `offline_suite_head.log`、`offline_suite_results.txt` | 审计方自己跑的离线测试：原始输出，和上一轮基线结果的逐项比较，编译步骤 | `make_test_env.sh` 建环境，`run_offline_suite.sh` 跑测试，`junit_compare.py` 比较，`run_compile_step.sh` 跑编译 |

## 敏感字符串扫描的命中怎么归类

审计方逐条看过，没有一条是真实的地址、邮箱或标识。

| 分类 | 计数 | 是什么 |
| --- | ---: | --- |
| `email` | 13 | 12 处在上一轮审计方自己发布的扫描输出里，是打过码的形状，不是地址；1 处在欧洲研究的测试里，是一条"带用户名的网址应当被拒绝"的用例 |
| `wechat_id` | 5 | 都是 fx-44、fx-49 记录里普通的下划线字段名，长度恰好撞上了规则 |
| `assigned_secret` | 1 | 补丁回归里的一个合成代币标识 |
| `home_path_generic` | 17 | 通用的家目录写法，不含用户名 |
| `email_benign` | 2 | 规则本来就归为无害的写法 |

地址类字符串：两个提交里共有 4 个不同的值，出现的位置和次数没有变化，没有新增。

## 离线测试环境的说明

和上一轮相同。Python 3.12，pytest 9.1.1；仓库的 `src` 用一个路径文件加入；时区数据用操作系统自带的一份代替锁文件里固定的那一版；不安装仓库的其余依赖，不连接任何数据库。依赖 PostgreSQL 的用例在这个环境里是跳过的。上一轮用这个环境在最后一个有 CI 记录的提交上跑出了和 CI 相同的计数。
