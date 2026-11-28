# 04 给本地 agent 的提示词（2026-11-28 第二次审核）

下面"提示词正文"一节可以整段转发给本地 agent。它写下了交付提交号和清单校验值，所以这个文件不在清单的覆盖范围内，放在交付提交之后的下一个提交里。

两段脚本和那条推送命令在临时克隆里各测过正常和异常的情况。分支推送之后，两段脚本又原样对 GitHub 跑过一遍；那条推送命令只对临时仓库测过，审计方没有对 GitHub 的 `main` 执行它。

## 提示词正文

你是 polymarket-alpha-lab 项目的协调者。2026-11-28 晚上我请云端审计方看了你们当天的工作（上午那一轮的整改，和足篮历史回测），基线是 `eaf6e8149f83a708d09158529ec774f4fbed8b9d`，被审核的 `main` 是 `2fc69b0560b073e05718ea335c05388872756e66`。结论：`main` 的代码没有发现 P0、P1 级缺陷；审计方自己跑的离线测试 44,412 项通过、0 项失败、0 项错误，和你们报告的数字相同；历史回测的三条复算命令审计方复跑通过，"新增正式模拟准入 0 项"的结论是对的。整体仍然不通过：P0 为零，1 条 P1、2 条 P2、2 条 P3。那条 P1 还是微信投递：它没有切换，而第 90 节把它写成了"机主将个人通讯划归本机范围"，库里没有我的这句原话（R9-1）。审计方同时给了一份优化计划。按下面的顺序做。

仍然不要求计划、复审、PR 或 CI。每一步只说要成立的事实，和拿什么证明它成立。

### 1. 目标

1. 核对本包的完整性，把它合入 `main`。
2. 把只有我本人能定的四件事原样列给我，并把第 90 节和状态板里关于微信投递的写法改正（步骤 2）。
3. 现在就能做的：FX-57 候选版本的部署和盘口原文留存；FX-60（回放程序入库）；优化计划第 0 阶段的 OPT-2、OPT-3、OPT-4。
4. 我答复 OD-AF 之后，照我选的那条路办。
5. 第 0 阶段做完之前，不增加新联赛和新盘口，不改任何一个模型。
6. 第 0 阶段做完时告诉我，附两套全量测试的汇总和采集覆盖表。

### 2. 已经完成、不要重复的工作

- 审核做完了，结论和证据在交接包里。不要重新审。历史回测不用重做，只需要照 FX-60 把回放程序入库并复现已发布的导出。
- 已经关闭、不要再动的：R8-2、R8-3、R8-6、R8-7。"一概同意"的范围按我确认的成稿执行。小额模拟维持卖一价，不要再提改入账价。欧洲研究的三个单元和独立库照常。
- 上午那一轮的交接包已经在 `main` 里（合并提交 `a3636744`）。
- 固定脚本 `docs/paper-sim/runtime/scripts/windows/Invoke-PaperWeixin.ps1` 不要改，SHA-256 是 `e86beca8f4073da75a8bc1cd3aeef3e85230417c0b19f9424432e79baa7998c6`。投递相关的源码（中转程序、受保护客户端、通知入口）保留在 `main` 里，不要删。
- `gates.py` 的四个阈值、前向研究和正式晋级的口径不动。
- 2026-11-11 那份已作废的审核和交接仍然不要执行。

### 3. 固定版本与完整性校验

| 项目 | 值 |
| --- | --- |
| 仓库 | `https://github.com/wmqfl861/polymarket-alpha-lab`（命令里写完整地址，不依赖本地 `origin` 指向哪里） |
| 交付分支 | `handoff/validation-review-2026-11-28` |
| 被审核的提交 | `2fc69b0560b073e05718ea335c05388872756e66` |
| 交付提交 | `51c554a0b5c82746d20653486a700f2f354f18e6` |
| 交付目录 | `docs/handoffs/2026-11-28-validation-review/`，在交付提交上是 28 个文件，git 树对象 `19919c17b7001d15406b1bd36a2d09b420f158de` |
| 清单 | 该目录下的 `MANIFEST.sha256`：3757 字节，27 行，SHA-256 `52149e78a06b6cdab689ec6893336133bd40662d66a8885bf4f2e34b05be168d` |

把下面的命令保存成仓库之外的一个脚本文件，在仓库检出的根目录用 `bash` 运行。它只读取，不改动任何东西；任何一步不符都会以非零状态退出，这时停下来把输出报告给我，不要继续，也不要换别的来源代替。校验读的是 git 对象而不是工作区文件，所以不受换行符转换的影响。

```bash
set -euo pipefail
REPO=https://github.com/wmqfl861/polymarket-alpha-lab.git
DELIVERY=51c554a0b5c82746d20653486a700f2f354f18e6
BASE=2fc69b0560b073e05718ea335c05388872756e66
DIR=docs/handoffs/2026-11-28-validation-review

# 1. a clean checkout, and the delivery commit is reachable from the retained branch
test -z "$(git status --porcelain)" || { echo "STOP: working tree not clean"; exit 1; }
git fetch "$REPO" handoff/validation-review-2026-11-28
git cat-file -e "$DELIVERY^{commit}"
git merge-base --is-ancestor "$BASE" "$DELIVERY"
git merge-base --is-ancestor "$DELIVERY" FETCH_HEAD

# 2. the delivery touches nothing outside the handoff directory
test -z "$(git diff --name-only "$BASE" "$DELIVERY" | grep -v "^$DIR/" || true)" \
  || { echo "STOP: delivery changes files outside $DIR"; exit 1; }
test "$(git diff --name-only "$DELIVERY" FETCH_HEAD)" = "$DIR/04-local-agent-prompt.md" \
  || { echo "STOP: unexpected changes after the delivery commit"; exit 1; }

# 3. pinned tree, file count and manifest
test "$(git rev-parse "$DELIVERY:$DIR")" = "19919c17b7001d15406b1bd36a2d09b420f158de" \
  || { echo "STOP: directory tree mismatch"; exit 1; }
test "$(git ls-tree -r --name-only "$DELIVERY" -- "$DIR" | wc -l)" -eq 28 \
  || { echo "STOP: file count mismatch"; exit 1; }
test "$(git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | wc -c)" -eq 3757 \
  || { echo "STOP: manifest size mismatch"; exit 1; }
test "$(git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | sha256sum | cut -d' ' -f1)" \
  = "52149e78a06b6cdab689ec6893336133bd40662d66a8885bf4f2e34b05be168d" \
  || { echo "STOP: manifest hash mismatch"; exit 1; }

# 4. every file listed in the manifest matches its hash
git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | while read -r sum path; do
  actual=$(git cat-file blob "$DELIVERY:$path" | sha256sum | cut -d' ' -f1)
  if [ "$actual" != "$sum" ]; then echo "STOP: MISMATCH $path"; exit 1; fi
done
echo "handoff package verified: $DELIVERY"
```

预期的最后一行输出是 `handoff package verified: 51c554a0b5c82746d20653486a700f2f354f18e6`。

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
- 引用我 11-26 那句"一概同意"去做成稿里排除的那几类事：真实资金、使用凭据、真实下单；仓库设置；根规则原文；对铁律的例外；在服务器或我的电脑上新增常驻服务。

**这一包新增的禁止**

- 用转述代替我的原话，去关闭或挪走一条审核条目，或者改变项目的范围。这类决定在库里放我的原话和时间。我没有说过的，写"owner 尚未答复"。
- 在回放程序入库（FX-60）之前，改任何一个模型的输入、参数或版本。
- 第 0 阶段做完之前增加新联赛、新盘口；让新五联赛进入人民币账本；给欧洲研究加任何投递。
- 把不同批次的报价拼成一场比赛的完整盘口。
- 在新的评分口径（OPT-4）登记之前，再跑优化器去搜参数。
- 为数据来源花钱，除非我单独说了。
- 删除 `main` 里和投递有关的源码。
- 改已经入账的旧模拟单的价格和盈亏。

**这一包允许的**

- 把 FX-57 的候选版本部署到运行层。它只增加记录，不改入账价。同时让盘口原文随摘要一起留存。
- FX-60、OPT-2、OPT-3、OPT-4 的代码、登记和采集。OPT-2 的采集优先加在已有的定时任务里；如果非要新增常驻单元，那属于"在服务器上新增常驻服务"，先问我。
- 照步骤 2 更正状态板和第 90 节。
- 欧洲研究、四类日常运行照常。

### 5. 执行步骤

**步骤 0：核对并合入交接包**

1. 运行第 3 节的校验脚本。任何一步不符就停下，报告失败的那一步、预期值和实际值。
2. 校验通过之后执行下面的合并脚本。同样保存成仓库之外的脚本文件，在仓库检出的根目录用 `bash` 运行。它在一个新的本地分支上做合并，不推送；如果交付提交已经在 `main` 里，它会直接退出。

```bash
set -euo pipefail
REPO=https://github.com/wmqfl861/polymarket-alpha-lab.git
DELIVERY=51c554a0b5c82746d20653486a700f2f354f18e6
DIR=docs/handoffs/2026-11-28-validation-review
WORK=integrate/validation-review-2026-11-28

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
git fetch "$REPO" handoff/validation-review-2026-11-28
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
git push https://github.com/wmqfl861/polymarket-alpha-lab.git integrate/validation-review-2026-11-28:main
```

推送被拒绝说明 `main` 在这期间动了。这时不要强推：先 `git checkout --detach`，再 `git branch -D integrate/validation-review-2026-11-28`，然后重跑合并脚本，再推一次。必须用合并提交，否则交付提交不在 `main` 的历史里，第 3 节的校验以后就无法通过。

4. 合入后，在 `main` 上重跑第 3 节的校验，应当仍然通过。

**步骤 1：阅读**

按顺序读 `docs/handoffs/2026-11-28-validation-review/` 下的 `README.md`、`01-review-report.md`、`02-remediation-and-next-steps.md`、`03-optimization-plan.md`。审计方另做的分析在 `evidence/independent_backtest_analysis.py` 和它的输出里，可以直接复跑。

**步骤 2：把只有我能定的事一次列给我，并更正登记**

先更正登记，不用等我：

- 第 90 节 OD-AC 那一行改成"owner 尚未答复"，写明现场仍是旧路径。
- 状态板恢复"微信投递"一行，写现状和待办。
- 现场变更对照表补一句：Windows 那一侧的准备工作不在本表范围内，依据见 11-26 一节。

然后原样列出下面四条，每条附一句你的现状说明。不要替我选。审计方的推荐照原样写在前面；你如果认为审计方的推荐有问题，把理由写在那一条后面，由我判断。我的每一句答复，原话带时间记入 `DELIVERY_PLAN.md`。

1. OD-AF：微信投递怎么了结。A（审计方推荐）是我把那台 Windows 电脑上绑定微信的账户重新登录一次，你接着把切换做完。B 是我用自己的话说明这一段是我自己电脑上的事、不属于项目、现在的做法我知道并接受。两条路都要我亲口说。
2. OD-AG：优化计划。审计方推荐先做第 0 阶段，做完之前不加新联赛和新盘口。我没有另外答复就按推荐办。
3. OD-AH：FX-57 的候选版本现在部署。审计方推荐部署。我没有另外答复就按推荐办。
4. OD-M：给 `main` 和 `handoff/**` 各加两条规则，禁止强推、禁止删除。这要我自己在 GitHub 上操作。

**步骤 3：现在就能做的**

1. FX-57：部署候选版本；让盘口原文留存；读回部署之后的第一笔新单。
2. FX-60：回放程序入库，逐字节复现 `new-five-fixtures.json.gz`；其余几组回放同样处理。
3. OPT-2：每场比赛三个固定时刻的同批完整盘口和条款原文。先从已经在采的联赛做起，发布按联赛、按周的覆盖表。
4. OPT-3（即 FX-61）：两种市场基准并排。
5. OPT-4：评分口径加上联合检验的模型系数和价格变动分，登记阈值和最少场数；登记之前把建议值和我确认一次。

**步骤 4：我答复 OD-AF 之后**

选 A，次序不要变：读回我重新登录之后拿到的是普通令牌；完成受保护安装，安装目录和文件的属主、访问控制读回；Windows 现场六种情况 6/6，发送那一步用替身程序；服务器端通知服务换成一个登记了提交号的 `main` 检出；做一次经固定入口的自然投递，回执入库，不含收件人标识和正文；现场读回通知服务的工作目录和单元里搜不到代理客户端的调用；把旧路径在 Windows 上留下的东西列给我。读回的不是普通令牌、安装有一项做不到、六种情况里有一种退出码不对、自然投递失败，都停下来报告我，不要退回旧路径去补发。

选 B：我的原话带时间入库；第 90 节 OD-AC 那一行换成我的原话；状态板"微信投递"一行写明它属于我本机范围、现在仍是旧路径、原话在库里的位置；投递相关的源码继续保留在 `main` 里。

**步骤 5：其余**

优化计划的第 1、第 2 阶段在第 0 阶段做完、并且我看过结果之后再开始。FX-48、FX-50 和 M2 照你们自己排的顺序。

### 6. 例外处理

- 校验脚本任何一步不符：停下，报告失败的那一步、预期值和实际值。
- 工作区不干净：不要 stash，也不要丢弃已有的改动。先把手头的改动提交完，或者换到一个干净的克隆里执行；做不到就停下来报告。
- 合并有冲突：停下来报告，不要强行解决。
- 推送被拒绝：照步骤 0 第 3 条重做，不要强推。
- 回放程序复现不出逐字节相同的导出：不要改导出去凑。写明差在哪一场、哪一个数、为什么，报告我。
- 回放用到的某些输入现在取不到了：写明缺哪些，用已登记的原始字节重放；登记里没有的，写"无法复现"。
- OPT-2 的采集在某个联赛拿不到同批完整盘口：照实记为缺口，不要拼。
- 你认为优化计划里某一项的目标值不合适：把理由和你建议的值告诉我，在登记之前定，不要自己改了再做。
- 我的答复和审计方的推荐不同：以我的答复为准，原话入库。
- 你认为这份提示词里有一步做不到或者不该做：停在那一步，把理由告诉我，其余互不依赖的步骤照常做。

### 7. 完成之后告诉我什么

1. 步骤 0 的结果：校验脚本的最后一行，合并提交号。
2. 步骤 2 的四条，我的答复原话在库里的位置，以及更正后的状态板。OD-AF 选 A 的话，新的现场记录在库内的路径和其中切换、现场六种情况、自然投递三项的值；选 B 的话，原话所在的行。
3. FX-57：部署回执；部署后第一笔新单的记录读回；任取一笔用留存原文重算摘要的结果。
4. FX-60：复现命令、输出的 SHA-256、和已发布导出的比对结果。
5. OPT-2：第一份覆盖表（应采、三个时刻各采到、同批齐全）。
6. OPT-3、OPT-4：并排的两套数字；登记文本。
7. 一份全量测试的汇总，离线的一次，原生库的一次，跑在你报告的那个 `main` 提交上：提交号，Python 和 pytest 的版本，通过、跳过、失败、错误各多少，退出码，开始和结束的时间。审计方会自己再跑一遍离线的那部分来对数字。
