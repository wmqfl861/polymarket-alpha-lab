# 证据说明

这里的脚本都是审计方自己写的只读脚本：只读 git 对象，不导入也不运行仓库代码，不联网，不写任何文件。输出里不出现任何地址、凭据或邮箱的原文。

下面的命令在仓库检出的根目录运行。`B` 是 `c4367b6b3ce4d957ce79ce645a7ab3425be5a133`，`M` 是 `2bdc4cccacc925ea8e6569c8865b90c0b0c671e6`，`E` 是本目录。

| 输出文件 | 生成命令 | 前提 |
| --- | --- | --- |
| `check_package_claims_output.txt` | `python3 -I E/check_package_claims.py .` | 克隆里要有进度包的提交 `09cf06f8` 和三个开放 PR 的头（`git fetch origin handoff/optimizer-review-2026-11-21 refs/pull/114/head refs/pull/116/head refs/pull/118/head`） |
| `check_registration_output.txt` | `python3 -I E/check_registration.py . M` | — |
| `plan_reference_scan_output.txt` | `python3 -I E/plan_reference_scan.py . B M` | — |
| `history_secret_scan_output.txt` | `python3 -I E/history_secret_scan.py <完整镜像目录> M` | 需要一份带全部文件版本的克隆，例如 `git clone --mirror`；在按需取文件的部分克隆上也能跑，但会逐个下载 |
| `test_weakening_scan_output.txt` | `python3 -I docs/handoffs/2026-11-19-audit-optimizer/evidence/test_weakening_scan.py . B M` | 脚本在上一轮的交接包里 |
| `scan_changed_files_output.txt` | `python3 -I docs/handoffs/2026-11-19-audit-optimizer/evidence/scan_changed_files.py . B M` | 同上 |
| `address_occurrence_output.txt` | `python3 -I docs/handoffs/2026-11-13-m0-review/evidence/address_occurrence_diff.py . B M` | 脚本在 11-13 的交接包里 |

以下四个文件来自 GitHub 的只读接口，查询时间写在各文件开头，不能离线重算：

| 文件 | 来源 |
| --- | --- |
| `ci_runs.tsv` | 对 B 到 M 第一父链上的 44 个合并提交和它们的第二父，逐个查询 `actions/runs?head_sha=<提交>`。列依次是：PR 号、类别（merge 是合并提交，head 是 PR 头）、提交、工作流、触发事件、状态、结论、运行编号、第几次尝试、创建时间 |
| `ci_summary.txt` | 由 `ci_runs.tsv` 汇总；测试总数和检出对象读自作业日志 |
| `repo_visibility.txt` | 仓库、`main` 分支、分叉、事件流四个接口 |
| `pull_requests.txt` | 开放 PR 列表；合并参照取自 `refs/pull/<编号>/merge` |

关于 `history_secret_scan_output.txt` 里列出的"需要人看一眼"的条目：我逐个看过，全部是测试夹具或占位值。私钥头、GitHub 令牌、AWS 和 Slack 令牌出现在两个专门测试"能不能检出凭据"的测试文件里；带密码的连接串的主机都是 `example.invalid` 这一类测试主机或本机；`*_TOKEN`、`*_SECRET_DSN` 这类赋值是代码常量的名字。
