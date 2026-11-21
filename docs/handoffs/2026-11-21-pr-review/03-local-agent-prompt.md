# 03 给本地 agent 的提示词（2026-11-21，对送审材料和三个待审 PR 的复审之后）

下面"提示词正文"一节可以整段转发给本地 agent。它写下了交付提交号和清单校验值，所以这个文件不在清单的覆盖范围内，放在交付提交之后的下一个提交里。

两段脚本在临时克隆里各测过正常和异常的情况，推送之后又用原样的脚本对 GitHub 跑过一遍。

## 提示词正文

你是 polymarket-alpha-lab 项目的协调者。2026-11-21 有两次独立审核。进度审核（`handoff/progress-review-2026-11-21`）的结论没变：`main` @ `2bdc4cccacc925ea8e6569c8865b90c0b0c671e6` 不通过（P0 为零，4 条 P1、4 条 P2、8 条 P3）。这一次是对你 20:00 送审材料的复审：材料属实，你提示词里的固定值全部一致；#116 通过；#114、#118 补齐后通过，没有发现需要改代码的缺陷，缺的是证据和顺序。你按 11-21 提示词第 8 节暂停了开发和推送：先按步骤 0 核对并解除暂停，再按下面的顺序做。不通过不需要撤回已合入的节点。

### 1. 目标

1. 核对本包的完整性，把它合入 `main`。11-21 的进度审核包如果还没有合入，先合它。
2. 解除暂停：写明暂停的原因，确认校验通过。
3. #116 合入。
4. #114：满足 MC-1（当前合并结果上的三条工作流）和 MC-2（当前头的复审回执入库），合入。
5. FX-38 和 FX-39 第一步：#114 合入之后，做成一个节点，合入。
6. #118：满足 MC-4 和 MC-5（PR 描述与回执；解冲突并整套重跑），合入。
7. 现在就能做、不碰 `papersim/cli.py` 和 `papersim/store_psycopg.py` 的事：两个交接包合入、`DELIVERY_PLAN.md` 登记、FX-36 的本地部分、FX-37 五次事后复审、P1-4 第一处的复现。
8. 其余照 11-21 提示词的步骤 4 和步骤 5。到第一阶段出口时把终点提交号告诉我。

### 2. 已经完成、不要重复的工作

- 两次审核都做完了，结论和证据在两个交接包里。不要重新审三个 PR；只做 `02-merge-conditions-and-next-steps.md` 的合并条件。
- 11-21 提示词第 2 节列的"已经完成"各项照旧有效。
- #116 已经通过，不需要再证明，按 `AGENTS.md` 的推送规则合入。
- 决定项 OD-P、OD-Q、OD-R 按审计方的推荐执行，不要再问我。OD-O（仓库可见性）、OD-T（#117 的根规则正文）、OD-M（`main` 的分支保护）、OD-S（"复审回执入库"那一句根规则）是我本人的事，你提醒我一次即可。
- 不要把事后补做的复审写成当时已审；回执如实写日期和被审提交号。
- 2026-11-11 那份已作废的审核和交接仍然不要执行。

### 3. 固定版本与完整性校验

| 项目 | 值 |
| --- | --- |
| 仓库 | `https://github.com/wmqfl861/polymarket-alpha-lab`（命令里写完整地址，不依赖本地 `origin` 指向哪里） |
| 交付分支 | `handoff/pr-review-2026-11-21` |
| 被审核的提交 | `2bdc4cccacc925ea8e6569c8865b90c0b0c671e6` |
| 交付提交 | `2e9bc3dc70a045063c7f4b7d1574f6fe2b6e9c95` |
| 交付目录 | `docs/handoffs/2026-11-21-pr-review/`，24 个文件，git 树对象 `0e7cb5f967e087ae9b2dd49a12cbc21109c19505` |
| 清单 | 该目录下的 `MANIFEST.sha256`：3036 字节，23 行，SHA-256 `0bfd41f85118628b3de743ad94bdf6f281576c0383bbf7c17112de28321166ac` |

把下面的命令保存成仓库之外的一个脚本文件，在仓库检出的根目录用 `bash` 运行。它只读取，不改动任何东西；任何一步不符都会以非零状态退出，这时停下来把输出报告给我，不要继续，也不要换别的来源代替。校验读的是 git 对象而不是工作区文件，所以不受换行符转换的影响。

```bash
set -euo pipefail
REPO=https://github.com/wmqfl861/polymarket-alpha-lab.git
DELIVERY=2e9bc3dc70a045063c7f4b7d1574f6fe2b6e9c95
BASE=2bdc4cccacc925ea8e6569c8865b90c0b0c671e6
DIR=docs/handoffs/2026-11-21-pr-review

# 1. a clean checkout, and the delivery commit is reachable from the retained branch
test -z "$(git status --porcelain)" || { echo "STOP: working tree not clean"; exit 1; }
git fetch "$REPO" handoff/pr-review-2026-11-21
git cat-file -e "$DELIVERY^{commit}"
git merge-base --is-ancestor "$BASE" "$DELIVERY"
git merge-base --is-ancestor "$DELIVERY" FETCH_HEAD

# 2. the delivery touches nothing outside the handoff directory
test -z "$(git diff --name-only "$BASE" "$DELIVERY" | grep -v "^$DIR/" || true)" \
  || { echo "STOP: delivery changes files outside $DIR"; exit 1; }
test "$(git diff --name-only "$DELIVERY" FETCH_HEAD)" = "$DIR/03-local-agent-prompt.md" \
  || { echo "STOP: unexpected changes after the delivery commit"; exit 1; }

# 3. pinned tree, file count and manifest
test "$(git rev-parse "$DELIVERY:$DIR")" = "0e7cb5f967e087ae9b2dd49a12cbc21109c19505" \
  || { echo "STOP: directory tree mismatch"; exit 1; }
test "$(git ls-tree -r --name-only "$DELIVERY" -- "$DIR" | wc -l)" -eq 24 \
  || { echo "STOP: file count mismatch"; exit 1; }
test "$(git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | wc -c)" -eq 3036 \
  || { echo "STOP: manifest size mismatch"; exit 1; }
test "$(git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | sha256sum | cut -d' ' -f1)" \
  = "0bfd41f85118628b3de743ad94bdf6f281576c0383bbf7c17112de28321166ac" \
  || { echo "STOP: manifest hash mismatch"; exit 1; }

# 4. every file listed in the manifest matches its hash
git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | while read -r sum path; do
  actual=$(git cat-file blob "$DELIVERY:$path" | sha256sum | cut -d' ' -f1)
  if [ "$actual" != "$sum" ]; then echo "STOP: MISMATCH $path"; exit 1; fi
done
echo "handoff package verified: $DELIVERY"
```

预期的最后一行输出是 `handoff package verified: 2e9bc3dc70a045063c7f4b7d1574f6fe2b6e9c95`。

### 4. 允许与禁止

11-21 提示词第 4 节整节仍然有效。要点重述如下，以那一节的全文为准。

**禁止**

- 任何真实交易、账户认证、钱包、私钥、签名、下单相关的代码或操作；读取、打印、移动或提交任何凭据。
- 自行修改 GitHub 的仓库设置（可见性、分支保护）；在我确认成稿之前合并 `AGENTS.md` 的任何修改。
- 用改写历史或强制推送的办法去掉地址或邮箱。
- 修改优化器规则的数字、降低登记的样本下限、扩大搜索空间，除非先报告我并重新登记。
- 修改冻结内容：四个闸门阈值、十变体集合、自检的十项检查、`tests/fixtures/papersim/` 下四个冻结夹具、已发布的评估回归数字；修改、删除或重放已有的迁移文件。
- 为了让 CI 变绿而跳过、删除、放宽测试或规则。
- 修改 `docs/handoffs/` 下已有交接包的文件。
- 把服务器的公网地址写进任何新文件、日志或提交说明。

**这一包新增**

- 允许：把 `main` 合进 PR 分支（合并提交）；往 PR 分支推新提交；更新 PR 描述。
- 禁止：变基或强推 #114、#118 的分支。
- 禁止：在 #114 的当前合并结果上三条工作流没有全绿之前合入 #114。
- 禁止：在 FX-38 和 FX-39 第一步合入之前，写入任何前向判定或应用任何换届。
- 禁止：把 #118 的冲突解决当成机械操作而不复审。

### 5. 执行步骤

**步骤 0：核对、解除暂停、合入交接包**

1. 运行第 3 节的校验脚本。任何一步不符就停下，报告失败的那一步、预期值和实际值。
2. 用一句话告诉我你按第 8 节暂停的原因：是 11-21 包的哪一条校验、预期值和实际值是什么，还是别的原因。校验都通过的话，暂停到此解除。
3. 校验通过之后执行下面的合并脚本。同样保存成仓库之外的脚本文件，在仓库检出的根目录用 `bash` 运行。它在一个新的本地分支上做合并，不推送；如果交付提交已经在 `main` 里，它会直接退出。

```bash
set -euo pipefail
REPO=https://github.com/wmqfl861/polymarket-alpha-lab.git
DELIVERY=2e9bc3dc70a045063c7f4b7d1574f6fe2b6e9c95
DIR=docs/handoffs/2026-11-21-pr-review
WORK=integrate/pr-review-2026-11-21

test -z "$(git status --porcelain)" || { echo "STOP: working tree not clean"; exit 1; }
git fetch "$REPO" main
MAIN=$(git rev-parse FETCH_HEAD)
if git merge-base --is-ancestor "$DELIVERY" "$MAIN"; then
  echo "already merged into main: nothing to do"; exit 0
fi
if git show-ref --verify --quiet "refs/heads/$WORK"; then
  echo "STOP: local branch $WORK already exists; inspect it instead of running this again"; exit 1
fi
git checkout -b "$WORK" "$MAIN"
git fetch "$REPO" handoff/pr-review-2026-11-21
git merge --no-ff --no-commit FETCH_HEAD
if [ -n "$(git diff --cached --name-only "$MAIN" | grep -v "^$DIR/" || true)" ]; then
  git merge --abort
  git checkout -
  git branch -D "$WORK"
  echo "STOP: merge would change files outside $DIR; aborted, nothing committed"; exit 1
fi
git commit --no-edit
git merge-base --is-ancestor "$DELIVERY" HEAD
echo "merged on local branch $WORK, not pushed: $(git rev-parse HEAD)"
```

这个交接包只新增一个目录，不应有冲突，也和 11-21 的进度审核包没有重叠的文件；有冲突就停下来报告，不要强行解决。合并是一个节点：做 `git diff --check`、秘密扫描和独立只读复审（复审范围包括这份提示词），然后按 `AGENTS.md` 的推送规则经 PR 合入 `main`。必须用合并提交，否则交付提交不在 `main` 的历史里，第 3 节的校验以后就无法通过。两个交接包可以放在同一个 PR 里（先 11-21 包，后本包，各一个合并提交），也可以分开。合入后，在 `main` 上重跑第 3 节的校验，应当仍然通过。

**步骤 1：阅读**

按顺序读 `docs/handoffs/2026-11-21-pr-review/` 下的 `README.md`、`01-review-report.md`、`02-merge-conditions-and-next-steps.md`。行号对应各 PR 的头提交；对不上就以符号名为准。

**步骤 2：看 CI 跑在哪个合并结果上**

在合并之前，每个 PR 都要做这一步；这个脚本只读，需要 `gh` 能读这个仓库：

```bash
git show 2e9bc3dc70a045063c7f4b7d1574f6fe2b6e9c95:docs/handoffs/2026-11-21-pr-review/evidence/check_ci_merge.sh > ../check_ci_merge.sh
bash ../check_ci_merge.sh 114 116 118
```

我复审时的输出是：#114 STALE（CI 合并进了 `8cba9d37`）、#116 STALE（合并进了 `11fb457c`）、#118 CURRENT。#116 只改文档，我已经按 STALE 判了通过，不要求重跑。你看到的结果与此不同就以现状为准，交回时单列。

**步骤 3：#116**

照 `AGENTS.md` 的推送规则合入（头上三条工作流已经是绿的），用合并提交。

**步骤 4：#114**

1. 把 `main` 合进 #114 的分支：合并提交，不变基，不强推。`DELIVERY_PLAN.md` 两边都改过，文本上合并干净；合并后检查节号没有重复、登记簿没有两份。
2. 新头上 Offline verification、Native project PostgreSQL、Research paper bridge 三条工作流都成功；再运行步骤 2 的脚本，#114 应当是 CURRENT。运行编号写进 PR 描述和 `DELIVERY_PLAN.md` 第 85 节。（MC-1）
3. 对这个新头做独立只读复审，回执入库：复审者、日期、被审提交号、范围、结论、发现。放在库内 `docs/` 下与现有复审记录同类的位置；没有同类位置就放在 `docs/handoffs/2026-11-21-pr-reviews/`，并在 `DELIVERY_PLAN.md` 第 85 节引用。复审不通过就按发现修改后送回原复审者，不要换复审者重试。（MC-2）
4. 回答审核报告 O-5（不是 #114 的合并条件）：给 `metrics.calibration_slope_intercept` 不收敛时抛出的 `ValueError` 补一个测试，构造不收敛的输入，确认 `optimize run` 记一条失败而不是崩溃。可以放在这个节点里，也可以放在后面的节点里，第一阶段交接书里写明在哪个节点。
5. 合入。

**步骤 5：FX-38 和 FX-39 第一步**

#114 合入之后，在 `main` 上作为同一个节点做。做法和验收标准见 11-21 的 `02-remediation-and-next-steps.md` 里 FX-38 和 FX-39 两节。位置：`papersim/cli.py` 里 `_optimize_forward_stats` 的收盘价偏离（#114 头上约 1434 行）、`_decision_roles`（约 1734 行）、`_cmd_optimize_apply`（约 1805 行）；以符号名为准。这个节点合入之前，不写任何前向判定，不应用任何换届。

**步骤 6：#118**

1. 把 `main`（此时含 #114 和步骤 5 的节点）合进 #118 的分支：合并提交，不变基，不强推。
2. 解 `papersim/store_psycopg.py` 的冲突：两边的改动都保留。两个 PR 还都改了 `.github/workflows/native-postgres.yml` 和 `tests/test_native_postgres_workflow.py`，文本上合并得干净，但那个测试钉住工作流里的分区文件清单，合并后先单独跑它。
3. 三条工作流整套重跑并全绿；再运行步骤 2 的脚本，#118 应当是 CURRENT。更新 PR 描述：去掉"三条 GitHub 工作流待此 PR 的实际结果"，写明运行编号和结果。（MC-4、MC-5）
4. 对冲突解决和当前头做独立只读复审，回执入库，格式同步骤 4 的第 3 条。（MC-4、MC-5）
5. 合入。

**步骤 7：之后**

照 11-21 提示词的步骤 4 和步骤 5：FX-41、FX-43、FX-40、FX-42 的补录、FX-39 的第二步、FX-44 至 FX-46，以及归档的后续批次。审核报告 O-6 里那个"本机通过、GitHub 的运行器上跳过"的测试，用 `-rs` 查清是哪一个，写进第一阶段交接书。

### 6. 验收标准

- MC-1 至 MC-5 逐条满足，每条写明提交号、三条工作流的运行编号、回执在库内的路径。
- 每个合入 `main` 的 PR，合入的那个头上三条工作流都为绿；步骤 2 的脚本对它报 CURRENT（#116 除外）。
- 11-21 提示词第 7 节的验收标准全部照旧，包括：没有任何测试被跳过、删除或放宽；自检十项的输出逐字节不变；四个冻结夹具不变；优化器规则的数字只在一处定义。
- 第一阶段出口之前：O-5 的失败路径测试在库内且通过；O-6 查清并写明。
- 最终是否通过由审计方的增量复审给出。

### 7. 失败处理

- 第 3 节任何一条校验不符：停止，报告失败的那一步、预期值和实际值，不要用别的来源替代。`git fetch` 因为认证方式失败时，把 `REPO` 换成你访问这个仓库惯用的地址再试；校验只认提交号和校验值，与传输方式无关。
- 工作区不干净：不要 stash，也不要丢弃已有的改动。先把手头的改动按正常流程提交完，或者换到一个干净的克隆里执行；做不到就停下来报告。
- 步骤 2 的输出与本包写的不一致：以现状为准，交回时单列。
- #114 合并 `main` 之后三条工作流有红：先分类，是过期的常量、平台差异，还是真缺陷。前两类照同样的纪律修；真缺陷就停下来报告我，不要改测试去迁就。
- 解 #118 冲突时两边的语义对不上：停下来报告，不要取一边丢另一边。
- 复审不通过：按发现修改后送回原复审者，不要换一个复审者来"重试"同一份改动。
- 平台拒绝某次写入、删除或推送：记录障碍，不要换接口或换编码绕过。
- 其余照 11-21 提示词第 8 节。

### 8. 需要返回的证据

1. `docs/handoffs/2026-11-19-optimizer-phase1.md`（已合入 `main`）：内容照 11-21 提示词第 9 节，另外加：
   - 暂停原因（一句话）和步骤 0 的校验结果。
   - MC-1 至 MC-5 的处置表：做了什么、提交号、三条工作流的运行编号、回执在库内的路径；#116 合入的提交号。
   - O-5 的测试清单；O-6 是哪个测试。
2. 给我的一条简短消息：终点提交号、上面这个文件的路径、三条工作流的运行编号。

任何汇报、日志或提交里都不要出现凭据、数据库密码或服务器的公网地址。
