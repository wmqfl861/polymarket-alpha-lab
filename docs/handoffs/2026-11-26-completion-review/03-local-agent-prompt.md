# 03 给本地 agent 的提示词（2026-11-26，验收审核之后）

下面"提示词正文"一节可以整段转发给本地 agent。它写下了交付提交号和清单校验值，所以这个文件不在清单的覆盖范围内，放在交付提交之后的下一个提交里。

两段脚本和那条推送命令在临时克隆里各测过正常和异常的情况。分支推送之后，两段脚本又原样对 GitHub 跑过一遍；那条推送命令只对临时仓库测过，审计方没有对 GitHub 的 `main` 执行它。

## 提示词正文

你是 polymarket-alpha-lab 项目的协调者。2026-11-26 我请云端审计方做了一次验收审核，基线是 `ab6edff2d64ac8aa5c2831d748b8730b63f8312d`，被审核的 `main` 是 `9a64415ae8a191f4c0abc5276c661240e1920e8c`。结论：`main` 的代码没有发现 P0、P1 级缺陷；审计方自己跑的离线测试 44,102 项通过、0 项失败；优化线第一阶段达到出口。整体仍然不通过：P0 为零，1 条 P1、3 条 P2、2 条 P3。那条 P1 是 FX-49 还没有在现场切换（R7-1）：生产环境的微信提醒仍由免批准的通用代理在我的 Windows 电脑上执行。不通过不需要撤回任何已合入的代码，也不需要停掉运行层。按下面的顺序做。

我在 11-26 取消了开发流程规定，所以这份提示词不要求计划、复审、PR 或 CI。每一步只说要成立的事实，和拿什么证明它成立。

### 1. 目标

1. 核对本包的完整性，把它合入 `main`。
2. 把只有我本人能定的四件事一次列给我（步骤 2）。
3. 我答复之后做 FX-49（续）：把生产环境的投递切到固定脚本，并验收。
4. FX-36（续）的只读部分现在就可以做：登录入口的两项检查，第二条限制的加载次序说明。
5. 其余照 `02` 号文件：FX-48、FX-50 排在 FX-49（续）之后；M2 这条线不等它们；FX-55；FX-23。
6. FX-49（续）验收完成时告诉我，并附一份全量测试的汇总。

### 2. 已经完成、不要重复的工作

- 审核做完了，结论和证据在交接包里。不要重新审。
- 已经关闭、不要再动的：11-24 的 R6-5、R6-6、R6-7、R6-8、R6-9、R6-10、R6-11、R6-13、R6-17；FX-47、FX-51、FX-52、FX-53、FX-54；第一阶段交接书。NBA v2 不要重跑。
- 11-24 的交接包已经在 `main` 里（#124）。
- 固定脚本 `docs/paper-sim/runtime/scripts/windows/Invoke-PaperWeixin.ps1` 不要改。它的 SHA-256 是 `e86beca8f4073da75a8bc1cd3aeef3e85230417c0b19f9424432e79baa7998c6`。要改的只是服务器端调用它的参数（步骤 3）。
- 运行层不要因为这次审核停掉，也不要扩大。四类日常运行照常。
- 2026-11-11 那份已作废的审核和交接仍然不要执行。

### 3. 固定版本与完整性校验

| 项目 | 值 |
| --- | --- |
| 仓库 | `https://github.com/wmqfl861/polymarket-alpha-lab`（命令里写完整地址，不依赖本地 `origin` 指向哪里） |
| 交付分支 | `handoff/completion-review-2026-11-26` |
| 被审核的提交 | `9a64415ae8a191f4c0abc5276c661240e1920e8c` |
| 交付提交 | `57e3fb5463dd4a171658299df34aaa0b2b69dfb5` |
| 交付目录 | `docs/handoffs/2026-11-26-completion-review/`，在交付提交上是 39 个文件，git 树对象 `36757231588b92d45db2cefbf3143c812d323603` |
| 清单 | 该目录下的 `MANIFEST.sha256`：5280 字节，38 行，SHA-256 `23e6046201fedc83581370c6a6f7b68925d087e2efffcaf8782864a7cc5b6640` |

把下面的命令保存成仓库之外的一个脚本文件，在仓库检出的根目录用 `bash` 运行。它只读取，不改动任何东西；任何一步不符都会以非零状态退出，这时停下来把输出报告给我，不要继续，也不要换别的来源代替。校验读的是 git 对象而不是工作区文件，所以不受换行符转换的影响。

```bash
set -euo pipefail
REPO=https://github.com/wmqfl861/polymarket-alpha-lab.git
DELIVERY=57e3fb5463dd4a171658299df34aaa0b2b69dfb5
BASE=9a64415ae8a191f4c0abc5276c661240e1920e8c
DIR=docs/handoffs/2026-11-26-completion-review

# 1. a clean checkout, and the delivery commit is reachable from the retained branch
test -z "$(git status --porcelain)" || { echo "STOP: working tree not clean"; exit 1; }
git fetch "$REPO" handoff/completion-review-2026-11-26
git cat-file -e "$DELIVERY^{commit}"
git merge-base --is-ancestor "$BASE" "$DELIVERY"
git merge-base --is-ancestor "$DELIVERY" FETCH_HEAD

# 2. the delivery touches nothing outside the handoff directory
test -z "$(git diff --name-only "$BASE" "$DELIVERY" | grep -v "^$DIR/" || true)" \
  || { echo "STOP: delivery changes files outside $DIR"; exit 1; }
test "$(git diff --name-only "$DELIVERY" FETCH_HEAD)" = "$DIR/03-local-agent-prompt.md" \
  || { echo "STOP: unexpected changes after the delivery commit"; exit 1; }

# 3. pinned tree, file count and manifest
test "$(git rev-parse "$DELIVERY:$DIR")" = "36757231588b92d45db2cefbf3143c812d323603" \
  || { echo "STOP: directory tree mismatch"; exit 1; }
test "$(git ls-tree -r --name-only "$DELIVERY" -- "$DIR" | wc -l)" -eq 39 \
  || { echo "STOP: file count mismatch"; exit 1; }
test "$(git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | wc -c)" -eq 5280 \
  || { echo "STOP: manifest size mismatch"; exit 1; }
test "$(git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | sha256sum | cut -d' ' -f1)" \
  = "23e6046201fedc83581370c6a6f7b68925d087e2efffcaf8782864a7cc5b6640" \
  || { echo "STOP: manifest hash mismatch"; exit 1; }

# 4. every file listed in the manifest matches its hash
git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | while read -r sum path; do
  actual=$(git cat-file blob "$DELIVERY:$path" | sha256sum | cut -d' ' -f1)
  if [ "$actual" != "$sum" ]; then echo "STOP: MISMATCH $path"; exit 1; fi
done
echo "handoff package verified: $DELIVERY"
```

预期的最后一行输出是 `handoff package verified: 57e3fb5463dd4a171658299df34aaa0b2b69dfb5`。

### 4. 允许与禁止

**禁止**（前几轮的各条照旧，全文重述如下）

- 任何真实交易、账户认证、钱包、私钥、签名、下单相关的代码或操作；读取、打印、移动或提交任何凭据。
- 自行修改 GitHub 的仓库设置（可见性、分支保护、规则集）；在我确认成稿之前修改 `AGENTS.md`。
- 用改写历史或强制推送的办法去掉地址或邮箱；删除或强推任何 `handoff/` 开头的分支。
- 修改优化器规则的数字、降低登记的样本下限、扩大搜索空间，除非先报告我并重新登记。
- 修改冻结内容：四个闸门阈值、十变体集合、自检的十项检查、`tests/fixtures/papersim/` 下四个冻结夹具、已发布的评估回归数字；修改、删除或重放已有的迁移文件。
- 为了让测试通过而跳过、删除、放宽测试或规则。
- 修改 `main` 上 `docs/handoffs/` 下已有交接包的文件。
- 把服务器的地址、端口、机器标识、微信收件人的标识，或者我的邮箱，写进任何文件、日志、提交说明或汇报。

**这一包新增的禁止**

- 在我答复 OD-Z、OD-AA 之前，改我那台 Windows 电脑上的任何设置，或者切换生产环境的投递。
- 没有我的话就改 Windows 上持久的执行策略设置。
- 为了绕开执行策略去改变固定脚本的运行方式：从标准输入喂脚本正文，拆成内联命令，或者换一个没有验过的脚本。
- 把"远程通道不能收窄到一个入口"当作不切换的理由。
- 没有我的授权就改服务器的配置；为了测量加载窗口去重启服务。
- 切换之前扩大运行层：不新增消息类型、收件人、正文里的外部文字来源；不扩大它覆盖的联赛、市场和下注规则；不放宽现有的核对，不增加重试。
- 为了凑并跑日放宽"合格"的定义；把人民币模拟账本的成绩写成正式晋级的证据。

**这一包允许的**

- 登录入口的两项只读检查：从原来的外部测点做一次 TCP 连接；在服务器本机只读地读出登录服务的生效配置。不登录，不提交任何口令或密钥。
- 我答复之后：改 `launcher()` 的调用参数和对应的用例；把固定脚本和绑定文件放到 Windows 上约定的位置（放之前告诉我文件名和位置）；在 Windows 现场演练六种情况（发送那一步用替身程序）；做一次自然投递。
- 四类日常运行照常。

### 5. 执行步骤

**步骤 0：核对并合入交接包**

1. 运行第 3 节的校验脚本。任何一步不符就停下，报告失败的那一步、预期值和实际值。
2. 校验通过之后执行下面的合并脚本。同样保存成仓库之外的脚本文件，在仓库检出的根目录用 `bash` 运行。它在一个新的本地分支上做合并，不推送；如果交付提交已经在 `main` 里，它会直接退出。

```bash
set -euo pipefail
REPO=https://github.com/wmqfl861/polymarket-alpha-lab.git
DELIVERY=57e3fb5463dd4a171658299df34aaa0b2b69dfb5
DIR=docs/handoffs/2026-11-26-completion-review
WORK=integrate/completion-review-2026-11-26

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
git fetch "$REPO" handoff/completion-review-2026-11-26
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
git push https://github.com/wmqfl861/polymarket-alpha-lab.git integrate/completion-review-2026-11-26:main
```

推送被拒绝说明 `main` 在这期间动了。这时不要强推：先 `git checkout --detach`，再 `git branch -D integrate/completion-review-2026-11-26`，然后重跑合并脚本，再推一次。必须用合并提交，否则交付提交不在 `main` 的历史里，第 3 节的校验以后就无法通过。

4. 合入后，在 `main` 上重跑第 3 节的校验，应当仍然通过。

**步骤 1：阅读**

按顺序读 `docs/handoffs/2026-11-26-completion-review/` 下的 `README.md`、`01-review-report.md`、`02-remediation-and-next-steps.md`。行号对应 `9a64415a`；对不上就以符号名为准。

**步骤 2：把只有我能定的事一次列给我**

原样列出下面四条，每条附一句你的现状说明。不要替我选。审计方的推荐照原样写在前面；你如果认为审计方的推荐有问题，把理由写在那一条后面，由我判断。我的每一句答复，原话记入 `DELIVERY_PLAN.md`。

1. OD-Z：投递的切换要不要等"远程通道能收窄到固定入口"。审计方推荐不等，先切换，通道的事单独登记为剩余风险。这会改掉 11-25 登记的"只在能限制为固定入口时切换"。我回"同意先切换"或者"不同意"。
2. OD-AA：固定脚本在我那台 Windows 电脑上怎么获准运行。A 是服务器每次调用时带一个只对这一次调用生效的执行策略参数（进程级，`RemoteSigned`），电脑上的设置一个字不改；B 是把那台电脑当前用户的策略设为 `RemoteSigned`，是持久设置；C 是代码签名。审计方推荐 A。我回一个字母。
3. OD-M：给 `main` 加两条保护，禁止强推、禁止删除，不要求经 PR 合入；同样两条也加到 `handoff/**` 上。这要我自己在 GitHub 上操作，你把操作的位置告诉我即可。
4. OD-AB：`AGENTS.md` 末尾要不要补回审计交接的三句话。成稿在 `02` 号文件第 2 节。我回"确认这份成稿"或者"不补"。

**步骤 3：FX-49（续）**（我答复 OD-Z、OD-AA 之后）

照 `02` 号文件 FX-49（续）的八步做，次序不要变：

1. 我选 A 时改 `weixin_delivery.py` 的 `launcher()`，在 `-File` 之前加进程级的执行策略参数，值用 `RemoteSigned`；对应的用例一起改。固定脚本不改。我选 B 时代码不用改，等我设置好或者明确授权你去设，然后把读回的值登记下来。
2. 把固定脚本和绑定文件放到 Windows 上约定的位置，放之前告诉我文件名和位置。安装照我 11-24 说的"受保护安装"来做；其中有哪一项在现场做不到，列出来告诉我，等我的话再继续。放完核对脚本的哈希等于第 2 节写的那个值。
3. 在 Windows 现场把六种情况各走一遍，发送用替身程序。输出入库，去掉收件人标识。
4. 服务器端的运行目录换成 `main` 的一个检出，登记提交号。
5. 做一次经固定入口的自然投递，回执入库，不含收件人标识和正文。
6. 现场读回一次：通知服务的工作目录里搜不到代理客户端的调用。
7. 把旧路径在 Windows 上留下的东西列出来报告我，由我决定清不清理。
8. 在 `DELIVERY_PLAN.md` 里登记剩余风险：远程通道能做的事比这一个入口宽，工具没有按命令限制的能力。

**步骤 4：FX-36（续）**（现在就可以做）

1. 登录入口：可达性沿用测数据库入口的那个外部测点和办法，做一次 TCP 连接；认证方式在服务器本机只读地读生效配置，只记"对外是否接受口令登录"这一条结论。两样登记到第 84.2.4 节，不写地址和端口。
2. 第二条限制的加载次序：只读地写清楚现状，给我一个调整方案。我授权之后再改，改完从外面复查一次。
3. OD-M 是我的事，你不要代做。

**步骤 5：其余**

照 `02` 号文件：FX-48、FX-50 在 FX-49（续）之后；M2 这条线照你们自己排的顺序继续，不等前面几项；FX-55 在写第一个真实的足球新闻记录生产者之前做完；FX-23 随手做。

### 6. 例外处理

- 校验脚本任何一步不符：停下，报告失败的那一步、预期值和实际值。
- 工作区不干净：不要 stash，也不要丢弃已有的改动。先把手头的改动提交完，或者换到一个干净的克隆里执行；做不到就停下来报告。
- 合并有冲突：停下来报告，不要强行解决。
- 推送被拒绝：照步骤 0 第 3 条重做，不要强推。
- 在 `RemoteSigned` 下固定脚本仍然起不来：停下，把退出码和现象报告我。不要自行换成更宽的值，不要自行去掉文件上的标记。
- Windows 现场六种情况里有一种的退出码不符合预期：停下，不要部署，把输出（去掉收件人标识）报告我。
- 自然投递失败：不要超出固定脚本自己的规则去重试，不要退回代理那条路径去补发，报告我。
- 登录入口的结论是"从外面能连上，并且接受口令登录"：当即报告我，等我的话。
- 现有的权限读不到登录服务的生效配置：登记为读不到，写明差什么权限，报告我。不要为此提权或改配置。
- 我的答复和审计方的推荐不同：以我的答复为准，原话入库。
- 你认为这份提示词里有一步做不到或者不该做：停在那一步，把理由告诉我，其余互不依赖的步骤照常做。

### 7. 完成之后告诉我什么

1. 步骤 0 的结果：校验脚本的最后一行，合并提交号。
2. 步骤 2 的四条，以及我的答复原话在库里的位置。
3. FX-49（续）：`main` 的提交号；Windows 现场六种情况的输出和自然投递回执在库内的路径；服务器检出的提交号；现场读回的结果；剩余风险那一段的位置。
4. FX-36（续）：登录入口的两个结论在第 84.2.4 节的位置；第二条限制的现状说明。
5. 一份全量测试的汇总，离线的一次，原生库的一次，跑在你报告的那个 `main` 提交上：提交号，Python 和 pytest 的版本，通过、跳过、失败各多少，退出码，开始和结束的时间。放进库里一个固定的位置。审计方会自己再跑一遍离线的那部分来对数字。
