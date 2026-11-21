# evidence/ 说明

这里是审计方在这一轮自己做的只读检查和输出。没有执行仓库里的任何代码；测试结果来自 GitHub Actions 的实际运行，数字用审计方的脚本从日志和 API 重算。输出里没有凭据，也没有服务器地址（地址类检查只报计数）。

| 文件 | 是什么 | 怎么来的 |
| --- | --- | --- |
| `local_agent_prompt_verify.sh` | 本地 agent 2026-11-21 20:00 给出的复审请求里的校验脚本，原样保存 | 取自 owner 转发的消息 |
| `local_agent_prompt_run.txt` | 上面那个脚本原样运行的结果：退出码 0，所有固定值一致 | 在空目录里运行 |
| `pr_metadata.tsv` | 三个 PR 在审核时 GitHub API 给出的标题、状态、`base.sha`、`head.sha`、行数 | `gh api repos/.../pulls/N` |
| `ci_runs_pr_heads.tsv` | 三个 PR 头提交上的全部工作流运行（编号、事件、结论、时间） | `gh api repos/.../actions/runs?head_sha=…` |
| `ci_jobs_per_run.tsv` | 九次运行（每个 PR 三条工作流）各有哪些作业、结论如何 | `gh api repos/.../actions/runs/ID/jobs` |
| `check_ci_merge.sh`、`check_ci_merge_output.txt` | 每个 PR 最近一次 pull_request 运行实际把头并进了哪个基提交，和现在的 `main` 是不是同一个。输出：#114、#116 是 STALE，#118 是 CURRENT。只读，需要 `gh` | `gh api` 取运行、作业日志里的 `HEAD is now at … Merge … into …` 行 |
| `ci_checkout_merge_commits.tsv` | 读过日志的十个作业各自检出的是哪个合并提交（"Merge 头 into 基"），以及 pytest 汇总行 | 作业日志里的 `HEAD is now at …` 行 |
| `native_partition_proof_118.txt` | #118 的原生库 papersim 分区作业：760 通过 0 跳过，三个新增的 D1 原生测试出现在耗时榜里 | 同上 |
| `merge_delta_117.txt` | #117 改了哪 16 个文件；其中 `DELIVERY_PLAN.md` 也被 #114 改过；哪些测试、脚本、工作流引用了这些文件 | `git diff --name-only`、`git grep` |
| `make_merge_refs.sh` | 在新的临时仓库里把 PR 头并进固定的 `main`，并两两并起来，报告冲突。只读 | 用 `git merge-tree --write-tree` |
| `merge_simulation_output.txt` | 上面脚本的输出：五个干净，`#114 + #118` 在 `store_psycopg.py` 冲突 | 运行脚本 |
| `test_weakening_scan.py`、`test_weakening_scan_output.txt` | 与 11-19、11-21 两轮相同的测试削弱扫描：被移除的测试、断言变少的测试、新增的跳过和预期失败标记 | 对 `m_main+N` 与 `main` 比较 |
| `address_occurrence_diff.py`、`address_occurrence_output.txt` | 公网地址形态的字符串在合并结果里有没有新增（只报计数） | 同上 |
| `plan_reference_scan.py`、`plan_reference_scan_output.txt` | 被改动的文件里引用节点计划或复审编号的行数，和库内设计文档清单 | 同上 |

## 读法和局限

- 扫描脚本的"头"参数是 `make_merge_refs.sh` 造出来的 `refs/audit/m_main+N`，也就是"PR 并进当前 `main` 之后"的样子，不是 PR 头本身。
- `plan_reference_scan` 数的是被改动文件整体里的引用，既有的也算；它只说明"哪些文件里有引用"，不说明 PR 新增了多少。新增行的判断来自审计方对 diff 的阅读，写在 `01-review-report.md`。
- `test_weakening_scan` 对 #118 报告"新增跳过标记 4"。这四个都是与同文件里既有测试相同的条件跳过（只在 Linux 且设置了 `POLYMARKET_ALPHA_LAB_RUN_NATIVE_PROJECT_POSTGRES=1` 时运行）。`native_partition_proof_118.txt` 显示在原生库工作流里这个变量已设置，papersim 分区 760 个测试通过，0 个跳过。
- 原生库工作流的 storage 分区在 #116（只改文档，没动任何测试）、#114、#118 三处的汇总行都是 `320 passed, 2 skipped` 加 `89 passed`（见 `ci_checkout_merge_commits.tsv` 里三个 `linux-native-storage` 作业），所以那两个跳过是既有的，与这三个 PR 无关。它们的名字没核到：CI 的 `native-postgres-linux-proof-*` 产物（含 JUnit 文件）从这个环境下载不了（存储主机不在网络白名单里）。
- #114 和 #118 都改了 `.github/workflows/native-postgres.yml` 和 `tests/test_native_postgres_workflow.py`，两处改动在文本上合并得干净（`merge_simulation_output.txt` 里冲突的只有 `store_psycopg.py`）；但这个测试钉住了工作流里的分区文件清单，所以两个 PR 并在一起之后要整套重跑，不能只看各自的绿。
- CI 日志本身没有放进来（太大）。作业编号都在 `ci_jobs_per_run.tsv` 里，日志可以用 `gh api repos/wmqfl861/polymarket-alpha-lab/actions/jobs/ID/logs` 取回。
