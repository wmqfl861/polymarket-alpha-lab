# 03 给本地 agent 的提示词（2026-11-24，增量审核之后）

下面"提示词正文"一节可以整段转发给本地 agent。它写下了交付提交号和清单校验值，所以这个文件不在清单的覆盖范围内，放在交付提交之后的下一个提交里。

这条分支上有两个交付提交。第一个（`4242bb7c35e32f9e1aa9df478cd7341e98f93d07`）里有一份证据输出的一行被截断在一个多字节字符中间，从 GitHub 的网页接口读回时字节对不上；第二个交付提交更正了它，结论和发现没有变化（见 `README.md` 的"更正记录"）。下面固定的是第二个交付提交，这份提示词是它之后的那个提交。

两段脚本在临时克隆里各测过正常和异常的情况，推送之后又用原样的脚本对 GitHub 跑过一遍。

## 提示词正文

你是 polymarket-alpha-lab 项目的协调者。2026-11-24 有一次独立的增量审核，基线是 `2bdc4cccacc925ea8e6569c8865b90c0b0c671e6`，被审核的 `main` 是 `ab6edff2d64ac8aa5c2831d748b8730b63f8312d`，另外看了 11-23 推上来的四条未合入分支。结论：11-21 的四条 P1 里代码层面的三条已经关闭，五个合并条件全部满足，`main` 的代码没有新的 P0、P1。整体仍然不通过：P0 为零，2 条 P1、6 条 P2、9 条 P3。两条 P1 都不在 `main` 的代码里：仓库公开之后的收尾没有了结（R6-1）；足篮运行层发微信的最后一步，是在我的 Windows 电脑上启动一个免批准的 AI 代理去发（R6-2）。不通过不需要撤回任何已合入的节点，也不需要停掉运行层。按下面的顺序做。

### 1. 目标

1. 核对本包的完整性，把它合入 `main`。
2. 把只有我本人能做的事一次列给我（步骤 2 的清单）。
3. FX-36（续）：做对外可达性检查并登记；拿到我的授权之后，关闭 `DELIVERY_PLAN.md` 第 84.2.4 节里没有关闭的几项。
4. FX-49：把微信投递的最后一步换成固定脚本，部署。
5. 登记：FX-52（#123 和 11-22 的免审指令）、FX-51（运行层的规则）、FX-53、FX-54。
6. 优化线：NBA v2 走完节点流程，按登记执行一次（FX-41，按 OD-V 理解），然后交第一阶段交接书。这条线不等运行层。
7. M2：FX-47，然后 FX-40，其余照 11-21 的顺序。
8. 运行层纳管：FX-48、FX-50，都排在 FX-49 之后。
9. 第一阶段交接书合入，或者 FX-49 部署完成，哪个先到就先告诉我。

### 2. 已经完成、不要重复的工作

- 审核做完了，结论和证据在交接包里。不要重新审。
- 已经关闭、不要再动的：FX-37、FX-38、FX-39 第一步、MC-1 至 MC-5、O-5、O-6、FX-43（补到 #122）、OD-T、现行文件里的地址清零、FX-42 的清点。
- 11-21 的两个交接包已经在 `main` 里（#119）。
- 决定项 OD-V、OD-Y 按审计方的推荐执行，不要再问我。OD-P、OD-Q、OD-R 照旧。
- OD-O、OD-M、OD-S、OD-U、OD-W、OD-X 是我本人的事，你提醒我一次即可。我答复之前一律维持现状：不改仓库设置，不改根规则，不改运行层的数据库，不改微信绑定的范围。
- 运行层不要因为这次审核停掉。四类日常运行（扫描、建模、模拟下注、推荐）照常，照旧免审。
- 2026-11-11 那份已作废的审核和交接仍然不要执行。

### 3. 固定版本与完整性校验

| 项目 | 值 |
| --- | --- |
| 仓库 | `https://github.com/wmqfl861/polymarket-alpha-lab`（命令里写完整地址，不依赖本地 `origin` 指向哪里） |
| 交付分支 | `handoff/incremental-review-2026-11-24` |
| 被审核的提交 | `ab6edff2d64ac8aa5c2831d748b8730b63f8312d` |
| 交付提交 | `b3e6f6d3eb3615b74b677658065de43d84147c94` |
| 交付目录 | `docs/handoffs/2026-11-24-incremental-review/`，在交付提交上是 34 个文件（其中一个是这份提示词的第一版，不在清单里），git 树对象 `5b8640f0b83ae67d1015a0e800fc5b69f7d07b4e` |
| 清单 | 该目录下的 `MANIFEST.sha256`：4516 字节，32 行，SHA-256 `d3054cfe2fef5e3ec321551bafc19bfdbbad5f12bfe3f5d545d895002c109884` |

把下面的命令保存成仓库之外的一个脚本文件，在仓库检出的根目录用 `bash` 运行。它只读取，不改动任何东西；任何一步不符都会以非零状态退出，这时停下来把输出报告给我，不要继续，也不要换别的来源代替。校验读的是 git 对象而不是工作区文件，所以不受换行符转换的影响。

```bash
set -euo pipefail
REPO=https://github.com/wmqfl861/polymarket-alpha-lab.git
DELIVERY=b3e6f6d3eb3615b74b677658065de43d84147c94
BASE=ab6edff2d64ac8aa5c2831d748b8730b63f8312d
DIR=docs/handoffs/2026-11-24-incremental-review

# 1. a clean checkout, and the delivery commit is reachable from the retained branch
test -z "$(git status --porcelain)" || { echo "STOP: working tree not clean"; exit 1; }
git fetch "$REPO" handoff/incremental-review-2026-11-24
git cat-file -e "$DELIVERY^{commit}"
git merge-base --is-ancestor "$BASE" "$DELIVERY"
git merge-base --is-ancestor "$DELIVERY" FETCH_HEAD

# 2. the delivery touches nothing outside the handoff directory
test -z "$(git diff --name-only "$BASE" "$DELIVERY" | grep -v "^$DIR/" || true)" \
  || { echo "STOP: delivery changes files outside $DIR"; exit 1; }
test "$(git diff --name-only "$DELIVERY" FETCH_HEAD)" = "$DIR/03-local-agent-prompt.md" \
  || { echo "STOP: unexpected changes after the delivery commit"; exit 1; }

# 3. pinned tree, file count and manifest
test "$(git rev-parse "$DELIVERY:$DIR")" = "5b8640f0b83ae67d1015a0e800fc5b69f7d07b4e" \
  || { echo "STOP: directory tree mismatch"; exit 1; }
test "$(git ls-tree -r --name-only "$DELIVERY" -- "$DIR" | wc -l)" -eq 34 \
  || { echo "STOP: file count mismatch"; exit 1; }
test "$(git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | wc -c)" -eq 4516 \
  || { echo "STOP: manifest size mismatch"; exit 1; }
test "$(git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | sha256sum | cut -d' ' -f1)" \
  = "d3054cfe2fef5e3ec321551bafc19bfdbbad5f12bfe3f5d545d895002c109884" \
  || { echo "STOP: manifest hash mismatch"; exit 1; }

# 4. every file listed in the manifest matches its hash
git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | while read -r sum path; do
  actual=$(git cat-file blob "$DELIVERY:$path" | sha256sum | cut -d' ' -f1)
  if [ "$actual" != "$sum" ]; then echo "STOP: MISMATCH $path"; exit 1; fi
done
echo "handoff package verified: $DELIVERY"
```

预期的最后一行输出是 `handoff package verified: b3e6f6d3eb3615b74b677658065de43d84147c94`。

### 4. 允许与禁止

**禁止**（前几轮的各条照旧，全文重述如下）

- 任何真实交易、账户认证、钱包、私钥、签名、下单相关的代码或操作；读取、打印、移动或提交任何凭据。
- 自行修改 GitHub 的仓库设置（可见性、分支保护）；在我确认成稿之前合并 `AGENTS.md` 的任何修改。
- 用改写历史或强制推送的办法去掉地址或邮箱。
- 修改优化器规则的数字、降低登记的样本下限、扩大搜索空间，除非先报告我并重新登记。
- 修改冻结内容：四个闸门阈值、十变体集合、自检的十项检查、`tests/fixtures/papersim/` 下四个冻结夹具、已发布的评估回归数字；修改、删除或重放已有的迁移文件。
- 为了让 CI 变绿而跳过、删除、放宽测试或规则。
- 修改 `main` 上 `docs/handoffs/` 下已有交接包的文件。
- 把服务器的公网地址写进任何新文件、日志或提交说明。

**这一包新增的禁止**

- 没有我的授权就改服务器的配置。对外可达性只查不改。
- 把检查用到的地址、端口、机器标识，或者微信收件人的标识，写进任何文件、日志、提交说明或汇报。
- FX-49 部署之前扩大运行层：不新增消息类型、收件人、正文里的外部文字来源；不扩大它覆盖的联赛、市场和下注规则；不为了提高送达率放宽现有的核对或增加重试。
- 把运行层的代码改动当作免审。免审的是四类运行，代码改动照旧走分支、PR、CI 和独立复审。
- 把人民币模拟账本的成绩写成正式晋级的证据。
- 为了让 NBA v2 "走到留出期"或者"登记一个候选"而调整网格、种子，或者重复运行。
- 把运行层分支和三条草稿分支直接合入 `main`。它们各自走完整的节点流程。
- 再给 `AGENTS.md` 加任何例外，而库里没有我的原话和我对完整正文的确认。

**这一包允许的**

- 对外可达性检查：只看端口通不通、登录入口对外提供哪些认证方式。不登录，不提交任何口令或密钥。
- 在 Windows 主机上手动演练固定脚本（发送那一步用替身程序），以及一次真实投递的验证。动 Windows 主机之前，把要放的文件和位置告诉我，等我回一句。
- 四类日常运行照常。

### 5. 执行步骤

**步骤 0：核对并合入交接包**

1. 运行第 3 节的校验脚本。任何一步不符就停下，报告失败的那一步、预期值和实际值。
2. 校验通过之后执行下面的合并脚本。同样保存成仓库之外的脚本文件，在仓库检出的根目录用 `bash` 运行。它在一个新的本地分支上做合并，不推送；如果交付提交已经在 `main` 里，它会直接退出。

```bash
set -euo pipefail
REPO=https://github.com/wmqfl861/polymarket-alpha-lab.git
DELIVERY=b3e6f6d3eb3615b74b677658065de43d84147c94
DIR=docs/handoffs/2026-11-24-incremental-review
WORK=integrate/incremental-review-2026-11-24

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
git fetch "$REPO" handoff/incremental-review-2026-11-24
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

这个交接包只新增一个目录，不应有冲突；有冲突就停下来报告，不要强行解决。合并是一个节点：做 `git diff --check`、秘密扫描和独立只读复审（复审范围包括这份提示词），然后按 `AGENTS.md` 的推送规则经 PR 合入 `main`。必须用合并提交，否则交付提交不在 `main` 的历史里，第 3 节的校验以后就无法通过。合入后，在 `main` 上重跑第 3 节的校验，应当仍然通过。

**步骤 1：阅读**

按顺序读 `docs/handoffs/2026-11-24-incremental-review/` 下的 `README.md`、`01-review-report.md`、`02-remediation-and-next-steps.md`。`main` 的行号对应 `ab6edff2`，运行层的行号对应 `bcce1b17`；对不上就以符号名为准。

**步骤 2：把只有我能做的事一次列给我**

原样列出下面八条，每条附一句你的现状说明。我的每一句答复，原话记入 `DELIVERY_PLAN.md`。

1. OD-O：仓库公开是不是我本人的决定。我回"公开"或"私有"就行。
2. 授权处理第 84.2.4 节里没有关闭的几项，或者我自己处理。
3. 在 GitHub 账号设置的 Emails 页打开"Keep my email addresses private"。
4. 给 `main` 开分支保护（OD-M）。
5. OD-U：确认 `AGENTS.md` 里 11-22 免审指令的那三处文字。
6. OD-W：说一句微信提醒的长期范围。
7. OD-X：运行层的数据库走路 A 还是路 B。审计方推荐路 A。
8. OD-S：过目"复审回执入库"那一句根规则的成稿。

**步骤 3：FX-36（续）**

照 `02-remediation-and-next-steps.md` 的 FX-36（续）。先做对外可达性检查并登记。任何一项可达，当即告诉我，等我授权；由我决定在关闭之前要不要先断开服务器到 Windows 主机的那条链路。拿到授权之后才改，改完从外面复查。

**步骤 4：FX-49**

照 `02` 号文件的 FX-49，走完整的节点流程：计划、计划复审、实现、测试、代码复审、部署。四条必须成立的条件和六种验收情况都在那一节。发送命令确实离不开代理时，走那一节写的退路，并告诉我。部署之后，在登记里写明服务器检出的提交号。

**步骤 5：登记**

可以和步骤 3、步骤 4 并行，都不碰源码：

1. FX-52：#123 进节点登记簿，回执原件入库；等我回了 OD-U，照 OD-T 的做法记下我的原话和文件的哈希。
2. FX-51：把运行层的准入政策、下注规则、各模型的版本和来源照现状登记；写明人民币账本不是晋级证据。
3. FX-53：四条小修先登记，再排期。
4. FX-54：`requires-python` 改成 `>=3.12`。这一条改 `pyproject.toml`，走正常节点。

**步骤 6：优化线**

1. NBA v2（`handoff/sports-fx41-nba-v2-20261123`）走完整的节点流程后合入。复审时核对：4,811 的试验上限只对已登记的这一个日程生效；原入口的上限和规则数字不变；规则单一来源测试的改动只是跟着函数改名。
2. 按登记执行一次。到达任何一个合法终态都算完成：候选登记、没有更好的、数据不足、运行失败并记下原因。不重跑。
3. 俱乐部足球以第 87.2 节的预检登记为完成形态，不执行正式重跑。
4. 写第一阶段交接书（第 8 节）。

**步骤 7：M2**

FX-47 排在 FX-40 之前。其余照 11-21 的顺序：FX-40、FX-44、并跑、FX-45、FX-46、FX-39 第二步。FX-44 登记时写明审核报告的 R6-17。每个 PR 合入之前照旧核对 CI 检出的合并提交：

```bash
git show ab6edff2d64ac8aa5c2831d748b8730b63f8312d:docs/handoffs/2026-11-21-pr-review/evidence/check_ci_merge.sh > ../check_ci_merge.sh
bash ../check_ci_merge.sh "$PR_NUMBER"   # set PR_NUMBER to the pull request about to be merged
```

**步骤 8：运行层纳管**

FX-48 和 FX-50，照 `02` 号文件。FX-48 的第一个 PR 排在 FX-49 之后。FX-50 等我回了 OD-X 再动。

### 6. 验收标准

- `02-remediation-and-next-steps.md` 里每个条目的"怎么验收"逐条满足，每条写明提交号和回执在库内的路径。
- 前几轮的验收标准全部照旧：没有任何测试被跳过、删除或放宽；自检十项的输出逐字节不变；四个冻结夹具不变；优化器规则的数字只在一处定义；每个合入 `main` 的 PR，合入的那个头上三条工作流都为绿，`check_ci_merge.sh` 对它报 CURRENT。
- 在终点提交上重跑本包的两个证据脚本（用法写在各自开头，基线都取 `ab6edff2d64ac8aa5c2831d748b8730b63f8312d`）：`docs/handoffs/2026-11-24-incremental-review/evidence/test_weakening_scan.py` 报告的"被移除的测试""断言变少的测试""新增的预期失败标记"要么为 0，要么在交接书里逐条说明；同一目录下的 `address_occurrence_diff.py` 的输出里没有新出现地址的文件。
- 运行层纳管完成时，服务器上跑的是 `main` 上某个提交的检出，登记里有提交号。
- 最终是否通过由审计方的复审给出。要变成通过，R6-1 和 R6-2 必须关闭。

### 7. 失败处理

- 第 3 节任何一条校验不符：停止，报告失败的那一步、预期值和实际值，不要用别的来源替代。`git fetch` 因为认证方式失败时，把 `REPO` 换成你访问这个仓库惯用的地址再试；校验只认提交号和校验值，与传输方式无关。
- 工作区不干净：不要 stash，也不要丢弃已有的改动。先把手头的改动按正常流程提交完，或者换到一个干净的克隆里执行；做不到就停下来报告。
- 对外可达性检查发现任何一项可达：当即告诉我，不要自己改。
- 远程控制工具不能把服务器在 Windows 主机上能执行的命令收窄到一个入口：写明做不到和原因，报告我，不要因此放弃 FX-49 的其余三条。
- NBA v2 的运行失败：记 `RUN_FAILED` 和原因，不重跑，报告我。
- 发现运行层里有审核报告没有写到的对外写入、账户认证或下单类的路径：立即停掉对应的定时器，报告我。
- 复审不通过：按发现修改后送回原复审者，不要换一个复审者来"重试"同一份改动。
- 平台拒绝某次写入、删除或推送：记录障碍，不要换接口或换编码绕过。

### 8. 需要返回的证据

1. `docs/handoffs/2026-11-19-optimizer-phase1.md`（已合入 `main`），含：
   - 固定版本：起点、每个节点的提交号和 PR 编号、终点提交号。
   - 逐条处置表：FX-36 至 FX-54 各做了什么、提交号、三条工作流的运行编号、复审结论和回执在库内的路径。没做的写原因。
   - FX-36（续）：我对 OD-O 的答复原话；对外可达性的结论（不写地址和端口）；已关闭项的复查结果。
   - FX-49 做到了哪一步：固定脚本的路径和哈希、六种情况的输出、一次真实投递的回执。
   - NBA v2 的运行号、终态和记分卡摘要；俱乐部足球还差多少、什么条件下可以重跑；三张原始记分卡并列保留。
   - 哪些路径在真实数据上走过，哪些只在测试里走过。
   - "注册表为空时逐位不变"的证据。
   - 登记簿补到交接时的 `main`，包括 #123。
   - 偏差和未完成事项。
2. FX-49 部署完成时给我一条简短消息：提交号、回执在库内的路径、第 84.2.4 节下面对外可达性结果的位置。
3. 运行层的现状，随 FX-48 的登记：哪些定时器启用着、从哪天开始、服务器检出的提交号。
4. 第一阶段交接书合入时给我一条简短消息：终点提交号、交接书的路径、三条工作流的运行编号。

任何汇报、日志或提交里都不要出现凭据、数据库密码、服务器的公网地址和端口、机器标识、微信收件人标识。
