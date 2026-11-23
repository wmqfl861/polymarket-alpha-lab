# evidence/ 说明

这里是审计方在这一轮自己做的只读检查和输出。没有执行仓库里的任何代码：本地事实从 git 对象里读，测试结果来自 GitHub Actions 的实际运行。输出里没有凭据，没有服务器地址，没有邮箱的用户名部分；敏感形态的检查只报类别、文件位置和掩码后的形状。

## 脚本

| 文件 | 做什么 | 怎么跑 |
| --- | --- | --- |
| `collect_local.sh` | 从 git 对象重算本轮用到的本地事实，写出下面带"本地"字样的九个文件。不检出、不执行仓库代码 | `bash collect_local.sh <仓库目录> <输出目录>`，仓库里要有 `origin/main` 和四个 `origin/handoff/sports-*` |
| `ci_merge_audit.sh` | 对基线之后合入 `main` 的每个 PR：列出 PR 头上的全部工作流运行；对 pull_request 触发的运行，读第一个作业的日志，看它实际把头并进了哪个提交，与真实合并提交的第一父比较，给出 CURRENT 或 STALE；再列出合并提交自己的 push 运行 | `bash ci_merge_audit.sh <仓库目录> <基线提交> <终点提交>`，需要 `gh` |
| `ci_main_runs.sh` | 列出 11-21 以来 `main` 上三条工作流的每次运行和结论，以及 Offline 工作流每次运行的整次用时（含排队） | `bash ci_main_runs.sh [owner/repo] [起始日期]`，需要 `gh` |
| `added_line_sensitive_scan.py` | 扫两个提交之间**新增的行**：公网 IPv4、邮箱、常见凭据形态、带密钥的 URL、带口令的连接串、形如口令的赋值、微信类标识、带用户名的绝对路径。永不打印命中的值 | `python3 -I added_line_sensitive_scan.py <仓库> <基> <头> [--show]` |
| `test_weakening_scan.py` | 与前三轮相同：被移除的测试、断言变少的测试、新增的跳过和预期失败标记 | `python3 -I test_weakening_scan.py <仓库> <基> <头>` |
| `address_occurrence_diff.py` | 与前几轮相同：公网地址形态的字符串在两个提交里各出现多少次，只报计数 | 同上 |
| `plan_reference_scan.py` | 与前两轮相同：被改动的源文件里引用节点计划或复审编号的行 | 同上 |

## 输出

| 文件 | 内容 | 来源 |
| --- | --- | --- |
| `opening_checks.txt` | 开头先查的三件事（可见性、`main` 的分支保护、分叉）、远端分支和标签数、打开的 PR、11-21 之后新增或移动的分支 | GitHub API、`git ls-remote` |
| `pr_metadata.tsv` | #114、#116、#118 至 #123 的状态、合并时间、合并提交、头、行数 | GitHub API |
| `main_delta.txt` | 本地：`2bdc4ccc..ab6edff2` 的八个合并提交和各自的规模；被改动的源码和工具文件 | `collect_local.sh` |
| `merge_tree_equivalence.txt` | 本地：每个真实合并提交的树是否等于 PR 头的树（即合并时 PR 头已经包含当时的 `main`） | `collect_local.sh` |
| `ci_merge_audit.tsv` | 八个 PR 头上的全部运行、实际检出的合并提交、CURRENT/STALE、合并提交上的 push 运行 | `ci_merge_audit.sh` |
| `ci_native_dispatch_runs.tsv` | 原生库工作流被路径过滤掉的 PR：本地 agent 把 GitHub 的合并对象推成 `ci/pr-N-merge-<提交>` 分支，在上面手动触发的原生库运行；以及 #119、#123 在 PR 头上的手动运行 | GitHub API |
| `ci_main_tip_jobs.txt` | `main` 顶端 `ab6edff2` 上三次运行的各作业结论和 pytest 汇总行 | GitHub API、作业日志 |
| `ci_main_runs.txt` | 11-21 以来 `main` 上的运行没有失败的（一次 push 运行被紧接着的下一次合并取代而取消）；Offline 工作流成功的 24 次运行整次用时 12.6 到 24.2 分钟，作业上限 30 分钟 | `ci_main_runs.sh` |
| `test_weakening_scan_output.txt` | `2bdc4ccc → ab6edff2`：被移除 3（改名或换成参数化用例，本地在 `docs/paper-sim/design/fx-38-fx-39-step1/tests-before-after.md` 逐个对上了）、断言变少 0、新增跳过 4、新增预期失败 0 | `test_weakening_scan.py` |
| `frozen_content_check.txt` | 本地：冻结夹具、迁移、闸门、评估、各模型文件、费用、队列政策在 `2bdc4ccc → ab6edff2` 之间没有任何改动 | `collect_local.sh` |
| `address_occurrence_output.txt` | `main` 上两个服务器地址值从 18、13 处降到 0；四个新分支没有新增任何地址值 | `address_occurrence_diff.py` |
| `added_line_sensitive_scan_output.txt` | `main` 和六个新分支的新增行：公网地址 0、凭据形态 0、带口令的连接串 0、带用户名的路径 0。27 行命中全部是误报（见下） | `added_line_sensitive_scan.py --show` |
| `commit_identity_domains.txt` | 新提交的作者和提交者身份，只列名字和邮箱域名 | `git log` |
| `plan_reference_scan_output.txt` | `main` 上被改动的源文件里对节点计划和复审编号的引用 | `plan_reference_scan.py` |
| `p1_fix_locations.txt` | 本地：FX-38、FX-39 第一步、O-5 在 `main` 上的代码和测试位置 | `collect_local.sh` |
| `variant_quotes_trace.txt` | 本地：FX-47 的路径——市场包里数组形态的 `variant_quotes` 一路原样传到 `jsonb` 列；FX-40 草稿写的正是数组形态 | `collect_local.sh` |
| `retrospective_followups.txt` | 本地：FX-37 事后复审提出要另开小修的三个读取函数在 `main` 上仍是裸游标，`DELIVERY_PLAN.md` 里没有登记；受管连接不是自动提交模式；三个函数的全部调用点 | `collect_local.sh` |
| `python_version_check.txt` | 本地：`pyproject.toml` 声明 Python ≥ 3.11，库里有一个只能在 3.12 上编译的文件；修复提交和撤回提交 | `collect_local.sh` |
| `sports_runtime_inventory.txt` | 本地：`handoff/sports-runtime-20261123` 的清点和机械扫描——规模、`main` 上有没有登记、数据库怎么连、代码里建了哪些表、有没有下单类代码、联网读哪些站、启动了哪些进程、动态加载、文件写入、收件人绑定的范围字符串、准入政策的数字、定时器、测试函数和用例的静态计数、模块名没有在任何测试文件里出现的脚本 | `collect_local.sh` |
| `sports_handoff_verification.txt` | 本地：那份交接包自己的清单和固定值核对无误；三条研究分支与 `main`、彼此之间都能干净合并；NBA v2 草稿相对登记的试验上限新增的数字 | `collect_local.sh` |

## 读法和局限

- **CURRENT 和 STALE**：#116 是 STALE，这是第五轮明确允许的纯文档例外。其余七个 PR 的 pull_request 运行都是 CURRENT，而且合并时 PR 头已经包含当时的 `main`，所以"PR 头上的绿"就是"合并结果上的绿"。
- **原生库工作流有路径过滤**：#120、#121、#122 的 PR 事件没有触发它。本地 agent 的做法是把 GitHub 生成的合并对象原样推成一个 `ci/pr-N-merge-<提交>` 分支，在上面手动触发。我核对了这些分支的提交号就是 `ci_merge_audit.tsv` 里 pull_request 运行检出的那个合并对象，树也和真实合并提交相同。
- **敏感形态扫描的 27 行命中**：21 行是以小写字母 o 开头的普通标识符里 28 个字符的一段（比如 `optimize-register-experiment`、`operation-recovery-node-plan`），撞上了"微信 openid"的形状；6 行是 `名字@实例.service` 这种 systemd 单元名，撞上了邮箱的形状。我把命中的原文逐个看过，一共八种不同的字符串，没有一个是真实标识。这个脚本只认形状，认不出没有固定形状的秘密；它没报不等于没有。
- **测试削弱扫描的"新增跳过 4"**：是 #118 新增的原生库用例自带的条件跳过（只在 Linux 且打开原生库开关时运行）。`main` 顶端的原生库工作流里 papersim 分区 845 通过、0 跳过，说明它们跑了。
- **运行层的扫描是机械的**。`sports_runtime_inventory.txt` 能说明"没有出现下单、钱包、签名这类字样""只有一处 POST，是公开盘口的批量读取"，不能说明模型和数据逻辑是对的。逐行读过哪些文件见 `01-review-report.md` 第 10 节。
- **接触不到的东西**：服务器、两个数据库、运行层的环境文件和定时器的实际状态、操作员本机的回执、Windows 主机上的一切、CI 的原生库产物（存储主机不在这个环境的网络白名单里）。依赖它们的结论在报告里标为"需本地 agent 复核"。
- CI 日志没有放进来。作业编号在 `ci_merge_audit.tsv` 和 `ci_main_tip_jobs.txt` 里，运行编号在 `ci_main_runs.txt` 里，可以用 `gh api repos/wmqfl861/polymarket-alpha-lab/actions/jobs/<编号>/logs` 取回。
