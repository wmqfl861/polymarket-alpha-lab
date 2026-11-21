# reviewer-prompt.md — 可直接转发的独立只读审核提示词（2026-11-21 进度交接包）

> 协调方使用说明：以下全文可直接转发给独立审核会话。转发前仅替换 §2 中的 `$R`、`$manifest_bytes`、`$manifest_sha256` 三个占位符为交付回执实值。交付提交与清单校验值不可能写进被交付的文件自身（不可自指），必须随回执传递；除此之外不得改动本提示词。

## 1. 目标

对正本仓库 `wmqfl861/polymarket-alpha-lab` 保留分支 `handoff/optimizer-review-2026-11-21` 上的进度交接包 `docs/handoffs/2026-11-21-optimizer-review/`（四文件：`README.md`、`evidence.md`、`reviewer-prompt.md`、`MANIFEST.tsv`）做**增量独立只读审核**：逐文件验真、复核 `evidence.md` 的可复算项、指出差异与缺证，并给出明确结论行。这是进度包审核：不是正式第一阶段出口审核，不是 MO-6 合入审核，不授权任何变更或补做未完成工作。

## 2. 版本与协调方随回执传递的值（不可自指部分）

- 公开仓库：https://github.com/wmqfl861/polymarket-alpha-lab
- 保留分支：`handoff/optimizer-review-2026-11-21`
- 交付提交：`$R`（完整 40 位 SHA；须为该分支上的真实提交；分支续更时 `$R` 仍可从分支历史可达）
- 清单预期：`MANIFEST.tsv` 共 3 行、无表头、Tab 分隔三列（相对路径 / 字节数 / SHA-256），每行以 LF 结尾且末行也有 LF；行序 `README.md`、`evidence.md`、`reviewer-prompt.md`；整文件字节数 `$manifest_bytes`；整文件 SHA-256 `$manifest_sha256`

## 3. 环境

Linux，`git`、`gh`、`sha256sum`、`diff`、`wc`（coreutils）。在新空目录操作。取证执行者可准备命令输出，再交独立只读审核会话。对正本保持只读：不向任何远端推送、不修改正本仓库、不连接任何数据库、不读取或请求任何凭据。

## 4. 下载与逐文件验真（任一步失败即按 §8 停止）

```bash
set -euo pipefail
work="$(mktemp -d)"; cd "$work"
git clone --quiet https://github.com/wmqfl861/polymarket-alpha-lab.git repo
cd repo
git fetch --quiet https://github.com/wmqfl861/polymarket-alpha-lab.git refs/heads/handoff/optimizer-review-2026-11-21
retained_head="$(git rev-parse FETCH_HEAD)"
git checkout --quiet --detach "$R"
test "$(git rev-parse HEAD)" = "$R"
git merge-base --is-ancestor "$R" "$retained_head"
package=docs/handoffs/2026-11-21-optimizer-review
diff -u <(printf '%s\n' MANIFEST.tsv README.md evidence.md reviewer-prompt.md) <(git ls-tree --full-tree -r --name-only "$R:$package" | LC_ALL=C sort)
cd "$package"
test "$(wc -c < MANIFEST.tsv)" -eq "$manifest_bytes"
printf '%s  MANIFEST.tsv\n' "$manifest_sha256" | sha256sum --strict -c -
test "$(wc -l < MANIFEST.tsv)" -eq 3
test "$(tail -c 1 MANIFEST.tsv | wc -l)" -eq 1
diff -u <(printf '%s\n' README.md evidence.md reviewer-prompt.md) <(cut -f1 MANIFEST.tsv)
while IFS=$'\t' read -r path bytes digest; do
  test "$(wc -c < "$path")" -eq "$bytes"
  printf '%s  %s\n' "$digest" "$path" | sha256sum --strict -c -
done < MANIFEST.tsv
```

通过标准：包目录恰含上述四文件；清单三行的字节数与 SHA-256 与实测一致；行序如 §2；逐文件哈希全部通过。

## 5. 材料复核（只读取证，按 evidence.md 章节）

1. **第一父链（evidence.md §1）**：
   `git log --first-parent --reverse --format='%H %P %s' c4367b6b3ce4d957ce79ce645a7ab3425be5a133..2bdc4cccacc925ea8e6569c8865b90c0b0c671e6` 应输出 44 行合并提交，与 §1 表 merge 列逐一一致；抽查 `git show -s --format='%P' <merge>` 第二父等于同行集成头。
2. **开放 PR（evidence.md §2）**：执行 §2.1/§2.2 给出的两端固定 `git diff --name-status` 命令（对象已随正本克隆可达）；将所得文件级清单与登记效果对照。同时核对 evidence.md §5 的 PR #118 和 14 项变更路径。
3. **CI 检出事实（evidence.md §2.1）**：使用 evidence.md §2.1 的 GitHub commit API 只读查询，应给出登记的树与双亲 [`8cba9d37…`, `26dfa2a0…`]；`git fetch https://github.com/wmqfl861/polymarket-alpha-lab.git refs/pull/114/head` 后，确认原头 `26dfa2a05357121665009981cd600e23c91e093a` 的 Git 对象仍可取。作业日志指纹为抓取时点值，活体日志哈希变化单独记录，不判失败。
4. **固定文件（evidence.md §6）**：下载 README §6 的欧洲篮球/足球两个固定 blob（提交 `c4367b6b…`）并计算 SHA-256，对照 evidence.md §6 发表值（篮球 `d0ffe021…635`；足球 `cbed4276…388`）。逐一打开 README §6 全部固定链接，确认可解析且指向声明的对象类型。
5. **可复现测试（可选，evidence.md §4；具备环境时执行）**：在隔离检出 `26dfa2a0…` 上运行六个锚点测试与相关套件，例如：
   ```bash
   git fetch https://github.com/wmqfl861/polymarket-alpha-lab.git refs/pull/114/head
   git checkout --detach 26dfa2a05357121665009981cd600e23c91e093a
   # 以仓库自带说明/工作流为准，例如：
   uv sync --locked --extra dev --extra postgres --python 3.12
   .venv/bin/python -m polymarket_alpha_lab.papersim.cli selfcheck
   .venv/bin/python -m pytest -q tests/test_papersim_optimize_metrics.py tests/test_papersim_optimize_runner.py tests/test_papersim_evaluation.py
   ```
   自检末行应为 `selfcheck: PASS (10/10 checks)`。WP-P4 锚需要已登记的服务器快照，缺数据时记录未执行。native/papersim 617、version-switch 89、focused-pair 79 为操作员对照值，你的复跑结果独立成立并单独回传。原生 PG 套件仅在具备隔离本地环境时运行，禁止连接任何生产或受管数据库。
6. **原样自检**：核对 README/evidence 声明与对象一致；确认包内不含公网 IP、凭据、服务器内部绝对路径、数据库连接串或原始日志（发现任一即按 §8 列阻断项）。核对 D1 已推送的固定提交，记录正式第一阶段交接仍待真实端点。

## 6. 已完成工作（勿重复）

- 固定主线范围内共 44 个合并提交；MO-7/8 在 main；MO-9 计划过审。
- 三族真实历史卡已按指纹登记；原审 §3.8 六项已登记（观察时点 2026-11-20）。
- 本包文档由独立文档准备会话编写；推送前验证与门禁由协调侧另行记录并以交付回执为准。你只审核，不重写文档。
- 未完成（只登记缺证，不要尝试补做）：#114、#116、#118 处于送审阶段；S2 公开赛果登记与三进程联验未完成、七个真实并跑日未交付、正式第一阶段交接未交付。

## 7. 允许动作

只读：clone/fetch/checkout/log/diff/show/ls-tree、哈希与文本比对、GitHub 页面与 API 只读查询、在隔离检出内运行仓库自带测试（如具备环境；仅限检出与临时目录内写入）。允许在你自己的工作目录写复核回执。

## 8. 停止条件（故障即停）

- §4 任一校验失败（文件集、行数、字节数、任一 SHA-256 不符）。
- 任一固定 SHA 在正本不可达，或固定链接 404/指向与声明不符的对象。
- 复算输出与 evidence.md 登记存在实质冲突（CI 活体日志哈希漂移除外，单独记录）。
- 发现禁出内容：公网 IP、凭据、服务器内部绝对路径、数据库连接串、原始日志。

停止后：不要修改仓库或交付文件；完整保存失败输出；按 §10 回报，阻断未解除时给 `VERDICT: FAIL`。

## 9. 明确不授权

真实交易或下单；读取、请求或转移任何凭据；连接生产或受管数据库；重开留出（holdout）；重跑历史优化；向任何分支推送；修改交付文件或清单；将本包当作正式第一阶段出口、MO-6 合入证明或任何执行授权。

## 10. 回执格式（返回协调方）

1. 所审交付提交 `$R` 与逐文件实测（路径、字节数、SHA-256）及清单校验结论。
2. 已复现项清单（命令 + 关键输出摘要或其哈希；含你复算的开放 PR 文件级清单）。
3. 差异与缺证清单（逐项：evidence.md 章节、现象、影响、建议补证条件）。
4. 阻断项（如有）与停止点。
5. 结论行（必填，单独一行，二选一）：`VERDICT: PASS` 或 `VERDICT: FAIL: <要点>`。

结论仅覆盖本进度包的文件验真与登记事实复核，不构成对任何未交付项的认可或合入授权。
