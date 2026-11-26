# evidence/ 说明

这里是审计方在这一轮自己做的检查和输出。和前几轮不同的一点：这一轮**执行了仓库里的测试代码**（离线的 pytest，两个提交各一遍），因为 CI 已经取消，没有别的运行记录可用。其余的本地事实仍然只从 git 对象里读。输出里没有凭据，没有服务器地址，没有邮箱的用户名部分，没有分叉所有者的名字；敏感形态的检查只报类别、文件位置和掩码后的形状。

## 脚本

| 文件 | 做什么 | 怎么跑 |
| --- | --- | --- |
| `collect_local.sh` | 从 git 对象重算这一轮用到的本地事实，写出下面标"本地"的十个文件。不检出，不执行仓库代码 | `bash collect_local.sh <仓库目录> <输出目录>`，仓库里要有 `origin/main`、运行层分支、FX-44 候选分支和那个证据标签 |
| `collect_api.sh` | 开头先查的三件事、#124 至 #138 的元数据、11-26 06:00 UTC 之后的工作流运行次数、最后一次 Offline 作业日志里的版本和汇总行。不写分叉所有者的名字 | `bash collect_api.sh <仓库目录> <输出目录> [owner/repo]`，需要 `gh` |
| `ci_merge_audit.sh` | 与前两轮相同：对合入 `main` 的每个 PR，读 pull_request 运行第一个作业的日志，看它实际把头并进了哪个提交，与真实合并提交的第一父比较，给出 CURRENT 或 STALE；再列出合并提交自己的 push 运行 | `bash ci_merge_audit.sh <仓库目录> <基线提交> <终点提交>`，需要 `gh` |
| `refs_delta.py` | 比较全量拉取前后两份远端引用清单：新增、移动、删除的分支，新增的 PR 头，`handoff/*` 分支有没有被动过 | `python3 -I refs_delta.py <仓库> <之前.txt> <之后.txt> <main 提交>` |
| `added_line_sensitive_scan.py` | 与上一轮相同：扫两个提交之间**新增的行**里的敏感形态。永不打印命中的值 | `python3 -I added_line_sensitive_scan.py <仓库> <基> <头> [--show]` |
| `test_weakening_scan.py` | 与前几轮相同：被移除的测试、断言变少的测试、新增的跳过和预期失败标记 | `python3 -I test_weakening_scan.py <仓库> <基> <头>` |
| `address_occurrence_diff.py` | 与前几轮相同：公网地址形态的字符串在两个提交里各出现多少次，只报计数 | 同上 |
| `make_test_env.sh` | 搭测试环境：用机器上已有的 pytest；用一个 `.pth` 文件让项目可以导入（等同于可编辑安装）；用操作系统的 IANA 时区数据搭一个 `tzdata` 的替身包 | `bash make_test_env.sh <检出> <venv 目录> <pytest 所在目录> <替身包目录>` |
| `run_offline_suite.sh` | 在一个检出里、清空的环境下跑 `python -m pytest -q -ra`，日志和 JUnit 文件写到检出之外，并记录检出里被跟踪的文件有没有变化 | `bash run_offline_suite.sh <检出> <标签> <python> <输出目录>` |
| `run_compile_step.sh` | 仓库 `scripts/verify_local.py` 的编译那一步（`compileall`），字节码缓存指到检出之外 | `bash run_compile_step.sh <检出> <python>` |
| `junit_compare.py`、`junit_summary.py` | 比较两份 JUnit 文件的逐用例结果；汇总一份 JUnit 文件里的失败 | `python3 -I junit_compare.py <基.xml> <头.xml>` |

三个测试脚本里写的是审计环境里的路径（`/home/claude/r7/...`），照搬不能直接运行，换成自己的路径即可。

## 输出

| 文件 | 内容 | 来源 |
| --- | --- | --- |
| `opening_checks.txt` | 可见性、`main` 的保护、规则集、分叉、远端引用数、打开的 PR、`main` 上的工作流文件数。查了两次，第二次在发布之前 | `collect_api.sh` |
| `pr_metadata.tsv` | #124 至 #138 的状态、合并时间、合并提交、基、头、行数 | `collect_api.sh` |
| `ci_after_removal.txt` | 11-26 06:00 UTC 之后的工作流运行次数（0）；`main` 上最后十二次运行；最后一次 Offline 作业的 Python、pytest、tzdata 版本和 pytest 汇总行 | `collect_api.sh` |
| `ci_merge_audit.tsv` | #124 至 #135 每个 PR 头上的全部运行、实际检出的合并提交、CURRENT/STALE、合并提交上的 push 运行。CI 取消之后的五个合并没有运行 | `ci_merge_audit.sh` |
| `main_delta.txt` | 本地：`ab6edff2..9a64415a` 的规模；第一父线上 45 个提交逐个列出（合并还是直接提交、动了哪类文件、是否在最后一次 CI 之后）；最后一次 CI 之后的规模和被改动的源码文件 | `collect_local.sh` |
| `merge_tree_equivalence.txt` | 本地：16 个合并提交的树是否等于第二父的树 | `collect_local.sh` |
| `frozen_content_check.txt` | 本地：冻结夹具、迁移、闸门、评估、各模型文件、费用、队列政策、风险政策、自检、优化器规则在这个范围内没有改动；登记的两个试验上限和较大的那个在哪里生效 | `collect_local.sh` |
| `rules_and_workflows.txt` | 本地：`AGENTS.md` 前后的 blob、大小、行数、SHA-256 和各级标题；工作流文件的删除提交 | `collect_local.sh` |
| `delivery_code_facts.txt` | 本地：`main` 上投递那一块的 7 个文件和哈希；启动的那一个进程；代理客户端和免批准模式在 `main` 与运行层分支上各出现几次；运行层分支和候选标签的状态 | `collect_local.sh` |
| `new_module_reachability.txt` | 本地：这一轮新增的 6 个源码模块各被哪些非测试文件导入，以及到达它们的命令行入口 | `collect_local.sh` |
| `news_binding_trace.txt` | 本地：R7-5 的代码位置 | `collect_local.sh` |
| `crypto_terminal_rule.txt` | 本地：新认得的那种加密货币合约题面，以及它对应的 K 线怎么取 | `collect_local.sh` |
| `commit_identity_domains.txt` | 本地：99 个新提交的作者和提交者邮箱域名，只列域名 | `collect_local.sh` |
| `open_items_in_repo_records.txt` | 本地：本地 11-26 的修复进展和现场状态两个文件的哈希，以及"0/6""0/7""0/4"等字样的出现次数 | `collect_local.sh` |
| `refs_delta.txt` | 分支 237 → 263，PR 头 123 → 138；新增 26 个分支，除 `main` 外没有分支被移动，没有分支被删除；全部 `handoff/*` 分支没有变化 | `refs_delta.py` |
| `test_weakening_scan_output.txt` | `ab6edff2 → 9a64415a`：删除 3 个测试文件，移除 15 个测试，断言变少 0，新增跳过标记 0，新增预期失败 0；新增 11 个测试文件 | `test_weakening_scan.py` |
| `address_occurrence_output.txt` | `main` 和四个没有合入的新引用：公网地址形态的不同取值都是 4 个，全部是数量很少而且前后不变的值（版本号、第三方夹具），没有新增 | `address_occurrence_diff.py` |
| `added_line_sensitive_scan_output.txt` | `main` 和四个没有合入的新引用的新增行：公网地址 0、凭据形态 0、带密钥的 URL 0、带口令的连接串 0、带真实用户名的路径 0（有 2 处是通用名的路径，单列一类） | `added_line_sensitive_scan.py --show` |
| `offline_suite_results.txt` | 审计方自己的测试运行：环境、两次运行的起止时间和汇总行、与 CI 汇总行的对照、两份 JUnit 的逐用例比较、跳过原因分组、编译步骤的结果，以及第一次没搭全环境的那次运行 | `run_offline_suite.sh`、`junit_compare.py`、`run_compile_step.sh` |
| `offline_suite_base.log`、`offline_suite_head.log` | 两次运行的 pytest 原始输出（`eae037aa` 和 `9a64415a`） | `run_offline_suite.sh` |

## 读法和局限

- **测试环境不是锁文件装出来的。** 审计环境不能装包。Python 3.12.3 和 pytest 9.1.1 与 CI 用的版本相同。项目唯一的运行依赖 `tzdata` 用操作系统的 IANA 数据（2026e）搭的替身代替，锁文件钉的是 PyPI 的 2026.3。这个环境在最后一个有 CI 的提交上跑出的通过数和跳过数与 CI 相同（44,063、483），所以我认为它对离线这一部分是可信的。
- **跑的只是离线的那一部分。** `9a64415a` 上跳过的 513 项里，442 项要原生 PostgreSQL 或别的原生环境，48 项要 Windows 或 PowerShell，23 项要服务器上才有的夹具。固定脚本的 19 个用例在跳过之列。
- **JUnit 文件没有放进来**（每份约 8 MB）。逐用例的比较结果在 `offline_suite_results.txt` 里，原始输出在两份 `.log` 里。
- **日志里的告警比 CI 多 18 条**（38 对 20），是因为加了输出 JUnit 的参数，pytest 对 `tests/test_handoff_download.py` 里的 `record_property` 给了 18 条提示。与测试结果无关。
- **测试削弱扫描的"移除 15 个"**：14 个是随工作流和流程文字一起删的，1 个是改名。本地在 `docs/paper-sim/design/optimizer-phase1/2026-11-26/delta-verification.json` 里逐个登记了去向，和我扫出来的一致。
- **敏感形态扫描的命中**：`main` 上"邮箱"形态 12 行，全部是 `名字@实例.service` 这种 systemd 单元名（其中 6 行在审计方上一轮的证据输出里，本身就是掩码后的形状）；"微信标识"形态 36 行，是以小写字母 o 开头的普通标识符里 28 个字符的一段。PR #131 的 2 行是文件名。命中的原文我都看过。
- **`refs_delta.py` 的"之前"清单**是审计方在上一轮拉取后留下的快照，别人无法重新生成；"之后"的清单可以用 `git for-each-ref --format='%(refname) %(objectname)' refs/remotes/origin` 重做。
- **CURRENT**：#124 至 #135 的 11 个 PR，pull_request 运行全部是 CURRENT，合并时 PR 头已经包含当时的 `main`。
- **接触不到的东西**：服务器、数据库、Windows 主机、定时器的实际状态、本地 agent 机器上的回执原件。依赖它们的结论在报告里都写明了引自哪一节。
- CI 日志没有放进来。作业编号在 `ci_merge_audit.tsv` 和 `ci_after_removal.txt` 里，可以用 `gh api repos/wmqfl861/polymarket-alpha-lab/actions/jobs/<编号>/logs` 取回。
