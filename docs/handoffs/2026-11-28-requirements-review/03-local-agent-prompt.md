# 03 给本地 agent 的提示词（2026-11-28，新增需求之后的审核）

下面"提示词正文"一节可以整段转发给本地 agent。它写下了交付提交号和清单校验值，所以这个文件不在清单的覆盖范围内，放在交付提交之后的下一个提交里。

两段脚本和那条推送命令在临时克隆里各测过正常和异常的情况。分支推送之后，两段脚本又原样对 GitHub 跑过一遍；那条推送命令只对临时仓库测过，审计方没有对 GitHub 的 `main` 执行它。

## 提示词正文

你是 polymarket-alpha-lab 项目的协调者。2026-11-28 我请云端审计方按规则又审了一次，基线是 `9a64415ae8a191f4c0abc5276c661240e1920e8c`，被审核的 `main` 是 `eaf6e8149f83a708d09158529ec774f4fbed8b9d`。结论：`main` 的代码没有发现 P0、P1 级缺陷；审计方自己跑的离线测试 44,410 项通过、0 项失败、1 项错误，那 1 项错误是一个新用例在没有装 PostgreSQL 的环境里没有被跳过。整体仍然不通过：P0 为零，1 条 P1、3 条 P2、3 条 P3。那条 P1 还是 FX-49 没有在现场切换（R8-1）。不通过不需要撤回任何已合入的代码，也不需要停掉欧洲研究。按下面的顺序做。

我在 11-26 取消了开发流程规定，所以这份提示词不要求计划、复审、PR 或 CI。每一步只说要成立的事实，和拿什么证明它成立。

### 1. 目标

1. 核对本包的完整性，把它合入 `main`。
2. 把只有我本人能定的四件事和要我补的两句原话，一次列给我（步骤 2）。
3. 我重新登录那台 Windows 电脑并告诉你之后，做 FX-49（续），并验收。
4. 现在就能做、和第 3 条互不依赖的：FX-56 的对照表，FX-57 的第 1、2、4、5 项，FX-58，FX-59。
5. FX-57 第 3 项等我答复 OD-AE。
6. FX-48、FX-50 排在 FX-49（续）之后。M2 照你们自己排的顺序，不等前面几项。
7. FX-49（续）验收完成时告诉我，并附一份全量测试的汇总。

### 2. 已经完成、不要重复的工作

- 审核做完了，结论和证据在交接包里。不要重新审。
- 已经关闭、不要再动的：R7-5（FX-55），R7-6（#131 和分支清理），OD-AB（`AGENTS.md` 已经补回，不要再改），登录入口和第二条限制的两项收紧。
- 11-26 的交接包已经在 `main` 里（合并提交 `0a3faf09`）。
- 固定脚本 `docs/paper-sim/runtime/scripts/windows/Invoke-PaperWeixin.ps1` 不要改。它的 SHA-256 是 `e86beca8f4073da75a8bc1cd3aeef3e85230417c0b19f9424432e79baa7998c6`。中转程序、受保护客户端和通知入口的源码已经在 `main` 里，不要为了赶切换去重写。
- 欧洲五联赛研究照常运行。不要因为这次审核停掉，也不要扩大。
- `gates.py` 的四个阈值、前向研究和正式晋级的口径不动。
- 2026-11-11 那份已作废的审核和交接仍然不要执行。

### 3. 固定版本与完整性校验

| 项目 | 值 |
| --- | --- |
| 仓库 | `https://github.com/wmqfl861/polymarket-alpha-lab`（命令里写完整地址，不依赖本地 `origin` 指向哪里） |
| 交付分支 | `handoff/requirements-review-2026-11-28` |
| 被审核的提交 | `eaf6e8149f83a708d09158529ec774f4fbed8b9d` |
| 交付提交 | `cf61d445fa62dd8f802c96c75ddd8c5244f35255` |
| 交付目录 | `docs/handoffs/2026-11-28-requirements-review/`，在交付提交上是 28 个文件，git 树对象 `4b33775cd6899bc179ae223d39932e3746bada2e` |
| 清单 | 该目录下的 `MANIFEST.sha256`：3782 字节，27 行，SHA-256 `96b683f2d998fdbb5506babfbb276483e0d9676cc9f6acb3f8d7871b362d4977` |

把下面的命令保存成仓库之外的一个脚本文件，在仓库检出的根目录用 `bash` 运行。它只读取，不改动任何东西；任何一步不符都会以非零状态退出，这时停下来把输出报告给我，不要继续，也不要换别的来源代替。校验读的是 git 对象而不是工作区文件，所以不受换行符转换的影响。

```bash
set -euo pipefail
REPO=https://github.com/wmqfl861/polymarket-alpha-lab.git
DELIVERY=cf61d445fa62dd8f802c96c75ddd8c5244f35255
BASE=eaf6e8149f83a708d09158529ec774f4fbed8b9d
DIR=docs/handoffs/2026-11-28-requirements-review

# 1. a clean checkout, and the delivery commit is reachable from the retained branch
test -z "$(git status --porcelain)" || { echo "STOP: working tree not clean"; exit 1; }
git fetch "$REPO" handoff/requirements-review-2026-11-28
git cat-file -e "$DELIVERY^{commit}"
git merge-base --is-ancestor "$BASE" "$DELIVERY"
git merge-base --is-ancestor "$DELIVERY" FETCH_HEAD

# 2. the delivery touches nothing outside the handoff directory
test -z "$(git diff --name-only "$BASE" "$DELIVERY" | grep -v "^$DIR/" || true)" \
  || { echo "STOP: delivery changes files outside $DIR"; exit 1; }
test "$(git diff --name-only "$DELIVERY" FETCH_HEAD)" = "$DIR/03-local-agent-prompt.md" \
  || { echo "STOP: unexpected changes after the delivery commit"; exit 1; }

# 3. pinned tree, file count and manifest
test "$(git rev-parse "$DELIVERY:$DIR")" = "4b33775cd6899bc179ae223d39932e3746bada2e" \
  || { echo "STOP: directory tree mismatch"; exit 1; }
test "$(git ls-tree -r --name-only "$DELIVERY" -- "$DIR" | wc -l)" -eq 28 \
  || { echo "STOP: file count mismatch"; exit 1; }
test "$(git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | wc -c)" -eq 3782 \
  || { echo "STOP: manifest size mismatch"; exit 1; }
test "$(git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | sha256sum | cut -d' ' -f1)" \
  = "96b683f2d998fdbb5506babfbb276483e0d9676cc9f6acb3f8d7871b362d4977" \
  || { echo "STOP: manifest hash mismatch"; exit 1; }

# 4. every file listed in the manifest matches its hash
git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | while read -r sum path; do
  actual=$(git cat-file blob "$DELIVERY:$path" | sha256sum | cut -d' ' -f1)
  if [ "$actual" != "$sum" ]; then echo "STOP: MISMATCH $path"; exit 1; fi
done
echo "handoff package verified: $DELIVERY"
```

预期的最后一行输出是 `handoff package verified: cf61d445fa62dd8f802c96c75ddd8c5244f35255`。

### 4. 允许与禁止

**禁止**（前几轮的各条照旧，全文重述如下）

- 任何真实交易、账户认证、钱包、私钥、签名、下单相关的代码或操作；读取、打印、移动或提交任何凭据。
- 自行修改 GitHub 的仓库设置（可见性、分支保护、规则集）；没有我确认成稿就修改 `AGENTS.md`。
- 用改写历史或强制推送的办法去掉地址或邮箱；删除或强推任何 `handoff/` 开头的分支。
- 修改优化器规则的数字、降低登记的样本下限、扩大搜索空间，除非先报告我并重新登记。
- 修改冻结内容：四个闸门阈值、十变体集合、自检的十项检查、`tests/fixtures/papersim/` 下四个冻结夹具、已发布的评估回归数字；修改、删除或重放已有的迁移文件。
- 为了让测试通过而跳过、删除、放宽测试或规则。
- 修改 `main` 上 `docs/handoffs/` 下已有交接包的文件。
- 把服务器的地址、端口、机器标识、微信收件人的标识，或者我的邮箱，写进任何文件、日志、提交说明或汇报。

**这一包新增的禁止**

- 引用我 11-26 那句"一概同意"去做下面这些事：真实资金、使用凭据、真实下单；仓库设置；改 `AGENTS.md`；对铁律的例外；在服务器或我的电脑上新增常驻服务。这些仍然要我逐次说一句。在我答复 OD-AD 之前，照这一条执行。
- 为了不等我重新登录，换一个账户去绑定微信，降低受保护安装的任何一项，或者在没有重新登录的那个会话里安装。
- FX-49 切换之前再往运行层加新东西：新的消息类型，新的联赛进入人民币账本，新的规则。FX-57 的改动在切换之前只准备和发布，不部署，除非我另外说。
- 给欧洲研究加任何投递；把它的预览或影子成绩写进微信提醒或人民币账本。
- 改已经入账的旧模拟单的价格。
- 为了让运行层的套件全部通过，删掉失败的用例而不写原因。
- 没有我的话就停掉两路自动提醒。

**这一包允许的**

- 只读地整理 11-26 14:24 UTC 之后现场变更的对照表（FX-56 第 2 项）。
- FX-57 第 2、4、5 项的代码和回归，照 11-27 的办法发布：对固定底本的完整差异、还原工具、逐文件校验值、复跑命令。
- FX-59：给 `test_register_close_window_through_managed_cli` 补上和同一个夹具另外八个用例一致的运行条件。这是补漏掉的条件，不算放宽测试；提供了 PostgreSQL 软件目录时它必须照常运行并通过。
- FX-58：登记和状态板的文字。
- 我重新登录并告诉你之后：FX-49（续）第 2 至 8 项。
- 欧洲研究、四类日常运行照常。

### 5. 执行步骤

**步骤 0：核对并合入交接包**

1. 运行第 3 节的校验脚本。任何一步不符就停下，报告失败的那一步、预期值和实际值。
2. 校验通过之后执行下面的合并脚本。同样保存成仓库之外的脚本文件，在仓库检出的根目录用 `bash` 运行。它在一个新的本地分支上做合并，不推送；如果交付提交已经在 `main` 里，它会直接退出。

```bash
set -euo pipefail
REPO=https://github.com/wmqfl861/polymarket-alpha-lab.git
DELIVERY=cf61d445fa62dd8f802c96c75ddd8c5244f35255
DIR=docs/handoffs/2026-11-28-requirements-review
WORK=integrate/requirements-review-2026-11-28

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
git fetch "$REPO" handoff/requirements-review-2026-11-28
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

3. 这个交接包只新增一个目录，不应有冲突；有冲突就停下来报告，不要强行解决。合并成功之后做 `git diff --check` 和你们惯用的秘密扫描，然后用普通推送把这个合并提交推到 `main`：

```bash
git push https://github.com/wmqfl861/polymarket-alpha-lab.git integrate/requirements-review-2026-11-28:main
```

推送被拒绝说明 `main` 在这期间动了。这时不要强推：先 `git checkout --detach`，再 `git branch -D integrate/requirements-review-2026-11-28`，然后重跑合并脚本，再推一次。必须用合并提交，否则交付提交不在 `main` 的历史里，第 3 节的校验以后就无法通过。

4. 合入后，在 `main` 上重跑第 3 节的校验，应当仍然通过。

**步骤 1：阅读**

按顺序读 `docs/handoffs/2026-11-28-requirements-review/` 下的 `README.md`、`01-review-report.md`、`02-remediation-and-next-steps.md`。行号对应 `eaf6e814`；对不上就以符号名为准。

**步骤 2：把只有我能定的事一次列给我**

原样列出下面六条，每条附一句你的现状说明。不要替我选。审计方的推荐照原样写在前面；你如果认为审计方的推荐有问题，把理由写在那一条后面，由我判断。我的每一句答复，原话记入 `DELIVERY_PLAN.md`。

1. OD-AC：我什么时候在那台 Windows 电脑上把绑定微信的账户注销再登录一次。审计方推荐 12 月 1 日之前。你把登录之后我要做的事写清楚：重新打开微信，确认在线，然后告诉你可以继续。到期我还没有登录的话，审计方推荐先停两路自动提醒，等切换完成再开；停不停要我说一句。
2. OD-AD："一概同意"的范围。成稿在 `02` 号文件第 2 节。我回"确认这份成稿"，或者给出自己的说法。
3. OD-AE：小额模拟按什么价入账。审计方推荐按这笔模拟金额在卖盘上逐档成交的均价，并把卖一档的挂量留在记录里。我回"同意"或者"维持卖一价"。
4. OD-M：给 `main` 和 `handoff/**` 各加两条规则，禁止强推、禁止删除，不要求经 PR 合入。这要我自己在 GitHub 上操作，位置你上次已经告诉过我，再提醒一次即可。
5. 请我补一句原话：11-27 的小额模拟定价，我当时要的是什么。
6. 请我补一句原话：11-28 的欧洲五联赛研究，我当时要的是什么；在服务器上启用它的三个单元，我是怎么说的。

**步骤 3：FX-49（续）**（我重新登录并告诉你之后）

照 `02` 号文件 FX-49（续）的第 2 至 8 项做，次序不要变：读回令牌，受保护安装，Windows 现场六种情况，服务器端换成登记了提交号的 `main` 检出，一次自然投递，现场读回，旧路径遗留清单。每一项的结果入库，不含收件人标识和正文。

**步骤 4：现在就能做的**

1. FX-56 第 2 项：对照表。
2. FX-57 第 1 项等我的原话；第 2、4、5 项准备代码、回归和差异并发布，不部署。
3. FX-58：第 89 节、状态板、欧洲研究怎么停。
4. FX-59：补那个用例的运行条件，给出两次运行的汇总。

**步骤 5：其余**

照 `02` 号文件：FX-57 第 3 项等 OD-AE；FX-48、FX-50 在 FX-49（续）之后；M2 照你们自己排的顺序继续。

### 6. 例外处理

- 校验脚本任何一步不符：停下，报告失败的那一步、预期值和实际值。
- 工作区不干净：不要 stash，也不要丢弃已有的改动。先把手头的改动提交完，或者换到一个干净的克隆里执行；做不到就停下来报告。
- 合并有冲突：停下来报告，不要强行解决。
- 推送被拒绝：照步骤 0 第 3 条重做，不要强推。
- 我重新登录之后读回的仍然不是普通令牌：停下，把诊断的退出码报告我，不要安装。
- 受保护安装有哪一项在现场做不到：列出来告诉我，等我的话再继续。
- Windows 现场六种情况里有一种的退出码不符合预期：停下，不要部署，把输出（去掉收件人标识）报告我。
- 自然投递失败：不要超出固定脚本自己的规则去重试，不要退回代理那条路径去补发，报告我。
- 留存的原始订单簿算不出 11-27 以来各笔的卖一档名义额：写明算不出来，不要估。
- 遇到一件你认为该问我、又觉得"一概同意"已经覆盖的事：在我答复 OD-AD 之前，照第 4 节新增禁止的第一条办。
- 我的答复和审计方的推荐不同：以我的答复为准，原话入库。
- 你认为这份提示词里有一步做不到或者不该做：停在那一步，把理由告诉我，其余互不依赖的步骤照常做。

### 7. 完成之后告诉我什么

1. 步骤 0 的结果：校验脚本的最后一行，合并提交号。
2. 步骤 2 的六条，以及我的答复原话在库里的位置。
3. FX-49（续）：新的现场记录在库内的路径；`production_switched`、现场六种情况、自然投递三项的值；服务器检出的提交号；现场读回的结果。
4. FX-56 的对照表、FX-58 的三处文字在库里的位置。
5. FX-57：差异和回归在库里的位置；11-27 以来各笔里，卖一档不够这笔模拟金额的有几笔，算不出来的有几笔。
6. FX-59：两次运行的汇总。
7. 一份全量测试的汇总，离线的一次，原生库的一次，跑在你报告的那个 `main` 提交上：提交号，Python 和 pytest 的版本，通过、跳过、失败、错误各多少，退出码，开始和结束的时间。离线那一次请另外注明有没有提供 PostgreSQL 软件目录。审计方会自己再跑一遍离线的那部分来对数字。
