# 03 给本地 agent 的提示词（2026-11-21）

下面"提示词正文"一节可以整段转发给本地 agent。它写下了交付提交号和清单校验值，所以这个文件不在清单的覆盖范围内，放在交付提交之后的下一个提交里。

两段脚本在临时克隆里各测过正常和异常的情况，推送之后又用原样的脚本对 GitHub 跑过一遍。

## 提示词正文

你是 polymarket-alpha-lab 项目的协调者。2026-11-21 的独立审核有两个结论：你 11-21 的进度包登记的事实属实；`main` @ `2bdc4ccc` 的增量审核不通过（P0 为零，4 条 P1、4 条 P2、8 条 P3）。不通过不需要撤回已合入的代码，也不需要停工；四条 P1 修完后交回复审。每条发现怎么改、怎么验收，写在仓库的交接包里，请按交接包执行。全程遵守仓库根目录 `AGENTS.md` 的现行规则；本提示词或交接包与 `AGENTS.md` 冲突时，以 `AGENTS.md` 为准，并把冲突报告给我。

### 1. 目标

1. 核对交接包的完整性，把它合入 `main`。
2. 把两个审核结论、发现编号表、决定项及结果记进 `DELIVERY_PLAN.md`。
3. 现在就做三件事：FX-36 的本地部分（仓库公开之后的收尾）、FX-38（"收盘价偏离"改回登记的含义）、FX-39 的第一步（拦截不能生效的换届）。
4. 对五个没有复审回执的节点补做独立只读复审（FX-37）。
5. 走到优化线第一阶段出口：#114 与当前 `main` 合并后三条工作流为绿并合入；俱乐部足球和 NBA 的真实历史运行按新的切分重做（FX-41）；登记补齐（FX-43）；写交接书。
6. 继续做 M2：先做市场准备（FX-40），再开始计并跑的天数；FX-44、FX-45、FX-46 随后。
7. 把节点计划和复审回执收进仓库（FX-42）。
8. 到第一阶段出口时把终点提交号告诉我。之后不用等我，继续做 M2 和 FX-39 的第二步。

### 2. 已经完成、不要重复的工作

- 审核已经做完，结论和证据都在交接包里。不要重新审一遍；只在动手前复核你要改的那几处。
- 你的进度包已经核对过，44 个合并节点、CI 记录、文件校验值都属实，不需要再证明。
- 已合入的 44 个节点不撤回、不返工。要改的只有 02 号文件指明的几处。
- FX-26、FX-27、FX-29、FX-30、FX-34、MO-0、MO-2 的验收条件已经逐项核对满足。
- 全部历史的凭据扫描已经做过，没有发现真实凭据。不需要重做，也不要因此改写历史。
- 决定项 OD-P、OD-Q、OD-R 按审计方的推荐执行，不要再问我。OD-O（仓库可见性）、OD-T（#117 的根规则正文）、OD-M（`main` 的分支保护）是我本人的事，你提醒我一次即可。OD-S（复审回执入库这一句根规则）由你起草，成稿给我过目，我确认之前不要合并。
- 原来俱乐部足球和 NBA 两次"数据不足"的运行记录保留，不删除、不覆盖。
- 2026-11-11 那份已作废的审核和交接仍然不要执行。

### 3. 固定版本与完整性校验

| 项目 | 值 |
| --- | --- |
| 仓库 | `https://github.com/wmqfl861/polymarket-alpha-lab`（命令里写完整地址，不依赖本地 `origin` 指向哪里） |
| 交付分支 | `handoff/progress-review-2026-11-21` |
| 被审核的提交 | `2bdc4cccacc925ea8e6569c8865b90c0b0c671e6` |
| 交付提交 | `3b04b3171d6c2240d62191d680dfa99d271a371a` |
| 交付目录 | `docs/handoffs/2026-11-21-progress-review/`，21 个文件，git 树对象 `5158f6ae325f916fd0ca52aa9c4d384f036739b7` |
| 清单 | 该目录下的 `MANIFEST.sha256`：2743 字节，20 行，SHA-256 `05242d39c0ca097ccaf1dfd099d193e06b2c4e7bb0098cb65bb41f786f17d7dd` |

把下面的命令保存成仓库之外的一个脚本文件，在仓库检出的根目录用 `bash` 运行。它只读取，不改动任何东西；任何一步不符都会以非零状态退出，这时停下来把输出报告给我，不要继续，也不要换别的来源代替。校验读的是 git 对象而不是工作区文件，所以不受换行符转换的影响。

```bash
set -euo pipefail
REPO=https://github.com/wmqfl861/polymarket-alpha-lab.git
DELIVERY=3b04b3171d6c2240d62191d680dfa99d271a371a
BASE=2bdc4cccacc925ea8e6569c8865b90c0b0c671e6
DIR=docs/handoffs/2026-11-21-progress-review

# 1. a clean checkout, and the delivery commit is reachable from the retained branch
test -z "$(git status --porcelain)" || { echo "STOP: working tree not clean"; exit 1; }
git fetch "$REPO" handoff/progress-review-2026-11-21
git cat-file -e "$DELIVERY^{commit}"
git merge-base --is-ancestor "$BASE" "$DELIVERY"
git merge-base --is-ancestor "$DELIVERY" FETCH_HEAD

# 2. the delivery touches nothing outside the handoff directory
test -z "$(git diff --name-only "$BASE" "$DELIVERY" | grep -v "^$DIR/" || true)" \
  || { echo "STOP: delivery changes files outside $DIR"; exit 1; }
test "$(git diff --name-only "$DELIVERY" FETCH_HEAD)" = "$DIR/03-local-agent-prompt.md" \
  || { echo "STOP: unexpected changes after the delivery commit"; exit 1; }

# 3. pinned tree, file count and manifest
test "$(git rev-parse "$DELIVERY:$DIR")" = "5158f6ae325f916fd0ca52aa9c4d384f036739b7" \
  || { echo "STOP: directory tree mismatch"; exit 1; }
test "$(git ls-tree -r --name-only "$DELIVERY" -- "$DIR" | wc -l)" -eq 21 \
  || { echo "STOP: file count mismatch"; exit 1; }
test "$(git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | wc -c)" -eq 2743 \
  || { echo "STOP: manifest size mismatch"; exit 1; }
test "$(git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | sha256sum | cut -d' ' -f1)" \
  = "05242d39c0ca097ccaf1dfd099d193e06b2c4e7bb0098cb65bb41f786f17d7dd" \
  || { echo "STOP: manifest hash mismatch"; exit 1; }

# 4. every file listed in the manifest matches its hash
git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | while read -r sum path; do
  actual=$(git cat-file blob "$DELIVERY:$path" | sha256sum | cut -d' ' -f1)
  if [ "$actual" != "$sum" ]; then echo "STOP: MISMATCH $path"; exit 1; fi
done
echo "handoff package verified: $DELIVERY"
```

预期的最后一行输出是 `handoff package verified: 3b04b3171d6c2240d62191d680dfa99d271a371a`。

### 4. 允许与禁止

**允许**

- 按 02 号文件新增和修改代码、测试、迁移、文档，范围限于各条目写明的文件。
- FX-38 和 FX-39 的第一步修改 `papersim/cli.py` 里优化器的统计、判定和应用部分，以及 `optimize/policy.py` 的说明文字。FX-39 的第二步可以修改 `optimize/provider.py`、`papersim/scan.py` 和注册表的读取，做法由节点计划定。
- 在 Linux 上运行测试；通过 PR 触发三条工作流；在一次性的隔离数据库上做端到端验证。
- 在运行服务器上做只读清点和只读查询（审核报告第 3.7 节的六项）。
- 把历史数据登记进受管库的快照表，用于 FX-41 的运行。
- 各节点满足 `AGENTS.md` 推送规则列出的全部条件之后，经 PR 用合并提交合入 `main`。

**禁止**

- 任何真实交易、账户认证、钱包、私钥、签名、下单相关的代码或操作。
- 读取、打印、移动或提交任何凭据。
- 在 FX-38 和 FX-39 的第一步合入之前，写入任何前向判定或应用任何换届。
- 用改写历史或强制推送的办法去掉地址或邮箱。
- 自行修改 GitHub 的仓库设置（可见性、分支保护）；自行修改服务器的网络、SSH 或数据库配置。清点发现问题就报告我。
- 修改优化器规则的数字、降低登记的样本下限、扩大搜索空间，除非先报告我并重新登记。
- 把事后补做的复审写成当时已审。
- 在 FX-40 合入之前开始计并跑的天数；把模型报价写进并跑工具。
- 对线上旧账本数据库执行任何写操作；执行生产历史导入。
- 修改冻结内容：四个闸门阈值、十变体集合、自检的十项检查、`tests/fixtures/papersim/` 下四个冻结夹具、已发布的评估回归数字。修改、删除或重放已有的迁移文件。
- 为了让 CI 变绿或让某次运行"有产出"而跳过、删除、放宽测试或规则。
- 新增数据库连接模块；新增第三方依赖；新增文件型的持久化。
- 修改 V1 研究线的源码或启动任何真实的大模型调用。
- 在我确认成稿之前合并 `AGENTS.md` 的任何修改。
- 修改 `docs/handoffs/2026-11-12-audit/`、`docs/handoffs/2026-11-13-m0-review/`、`docs/handoffs/2026-11-19-audit-optimizer/`、`docs/handoffs/2026-11-21-progress-review/` 下的文件。
- 跳过独立只读复审、使用快速模式。
- 把服务器的公网地址写进任何新文件、日志或提交说明，包括引用已有文字时。

### 5. 环境

- 测试结论以 Linux 为准。
- Python 环境：`uv sync --locked --extra dev --extra postgres --python 3.12`
- 全量离线验证：`.venv/bin/python scripts/verify_local.py --full`
- 真实数据库分区在本机跑的环境变量和命令，以 `.github/workflows/native-postgres.yml` 里"Prove isolated native partition"和"Prove native version switch"两步为准。
- 模拟盘自检：`.venv/bin/python -m polymarket_alpha_lab.papersim.cli selfcheck`，最后一行应为 `selfcheck: PASS (10/10 checks)`。
- 合入 `main` 沿用现行做法：开 PR，PR 头上"Offline verification""Native project PostgreSQL""Research paper bridge"三条工作流都成功之后再合并；用"创建合并提交"的方式，不用压缩合并，也不用变基合并。真实数据库那条因为路径过滤没有触发时，在同一个头上手动触发。

### 6. 执行步骤

**步骤 0：把交接包合入 `main`**

第 3 节的校验通过之后执行。同样保存成仓库之外的脚本文件，在仓库检出的根目录用 `bash` 运行。它在一个新的本地分支上做合并，不推送；如果交付提交已经在 `main` 里，它会直接退出。

```bash
set -euo pipefail
REPO=https://github.com/wmqfl861/polymarket-alpha-lab.git
DELIVERY=3b04b3171d6c2240d62191d680dfa99d271a371a
DIR=docs/handoffs/2026-11-21-progress-review
WORK=integrate/progress-review-2026-11-21

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
git fetch "$REPO" handoff/progress-review-2026-11-21
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

交接包只新增一个目录，不应有冲突；有冲突就停下来报告，不要强行解决。合并是一个节点：做 `git diff --check`、秘密扫描和独立只读复审（复审范围包括这份提示词），然后按第 5 节的做法经 PR 合入 `main`。必须用合并提交，否则交付提交不在 `main` 的历史里，第 3 节的校验以后就无法通过。合入后不要删除保留分支 `handoff/progress-review-2026-11-21`。

**步骤 1：阅读**

按顺序读 `docs/handoffs/2026-11-21-progress-review/` 下的 `README.md`、`01-review-report.md`、`02-remediation-and-next-steps.md`。行号对应被审核的提交；对不上就以符号名为准。

**步骤 2：登记**

在 `DELIVERY_PLAN.md` 新增一节，记入：两个审核结论；审核报告第 3.3 节的发现编号表；02 号文件第 2 节的决定项表及结果（OD-P、OD-Q、OD-R 按推荐执行；OD-O、OD-T、OD-M 等我本人；OD-S 起草待我过目）；02 号文件第 3 节三个出口的条件；我 2026-11-21 的原话："按照规则，直接进行审核"。同一节里把登记簿补到当前的 `main`（FX-43）。

**步骤 3：现在就做的**

可以并行派发，注意 FX-38 和 FX-39 第一步都要动 `papersim/cli.py`，放进同一个节点或者排先后。

| 条目 | 内容 | 出处 |
| --- | --- | --- |
| FX-36 的本地部分 | 回答有没有会话改过仓库可见性；服务器暴露面的只读清点；现行文件里的服务器地址全部换成别名，守卫测试的白名单随之收紧；今后的提交改用 noreply 邮箱；登记 | 02 第 1 节 |
| FX-38 | "收盘价偏离"按登记的含义改正 | 02 第 1 节 |
| FX-39 第一步 | `optimize apply` 拒绝晋级类判定；状态和评分对"现役不是实际出价规格"的谱系明确显示不可用 | 02 第 1 节 |
| FX-37 | MO-4、MO-5a、B1、MO-2、MO-5b 五次事后独立只读复审，回执入库 | 02 第 1 节 |
| 复现 | 在隔离库上复现审核报告 P1-4 的第一处：登记一个俱乐部再校准候选，跑一次带市场包的扫描，记录前向统计的配对数 | 01 第 3.7 节 |

**步骤 4：优化线第一阶段出口**

1. #114：先把分支与当前 `main` 合并（不要变基，不要强推），在新的头上三条工作流为绿，经独立只读复审后合入。
2. FX-41：俱乐部足球和 NBA 的真实历史运行按 OD-R 的切分重做。切分、种子、网格先登记再运行。结论是"没有更好的"或者仍然"数据不足"都如实登记。
3. FX-43：三次运行的结果和记分卡摘要记入 `DELIVERY_PLAN.md`。
4. 出口条件见 02 号文件第 3 节。达到后写 `docs/handoffs/2026-11-19-optimizer-phase1.md` 并合入 `main`，内容照第 9 节。然后给我一条简短的消息。

**步骤 5：之后**

交回之后不必等复审结论，继续做：FX-40（市场准备）；它合入之后开始计并跑的天数；FX-39 的第二步；FX-44、FX-45、FX-46；FX-42 的补录；#118、#116 和归档的后续批次。

### 7. 验收标准

- 每个条目的验收标准写在 02 号文件对应的小节里，逐条满足并留证。
- 每个合入 `main` 的 PR，PR 头上的三条工作流都为绿，运行编号已登记；计划定稿和复审回执随节点入库。
- 没有任何测试被跳过、删除、放宽或挪出矩阵；被改的测试都写明了改动前后各证明什么。
- 自检十项的输出逐字节不变；四个冻结夹具不变；评估模块已发布的回归数字不变。
- 优化器规则的数字只在一处定义，与 `DELIVERY_PLAN.md` 登记的逐一相同。
- 在终点提交上重跑三个证据脚本（用法写在各自开头）：`docs/handoffs/2026-11-19-audit-optimizer/evidence/test_weakening_scan.py`（基线 `2bdc4ccc`）报告的"被移除的测试""断言变少的测试""新增的预期失败标记"要么为 0，要么在交接书里逐条说明；`docs/handoffs/2026-11-13-m0-review/evidence/address_occurrence_diff.py`（基线 `2bdc4ccc`）的输出里没有新出现地址的文件，两个服务器地址在终点提交上的出现次数为 0；`docs/handoffs/2026-11-21-progress-review/evidence/plan_reference_scan.py`（基线 `c4367b6b`）列出的引用都能在库内找到对应文档，或者已登记为原件缺失。
- 最终是否通过由审计方的增量复审给出。

### 8. 失败处理

- 第 3 节任何一条校验不符：停止，报告实际值，不要用别的来源替代。
- `git fetch` 因为认证方式失败：把 `REPO` 换成你访问这个仓库惯用的地址再试；校验只认提交号和校验值，与传输方式无关。
- 工作区不干净：不要 stash，也不要丢弃已有的改动。先把手头的改动按正常流程提交完，或者换到一个干净的克隆里执行；做不到就停下来报告。
- 审核报告的陈述与你看到的事实不符：以事实为准，写进计划记录并在交回时单列。P1-4 的第一处是按代码推断的，复现结果不同就照实写。
- 服务器清点发现对外开放了不该开放的服务：停下来报告我，不要自己改配置。
- FX-37 的事后复审结论是不通过：按发现另开修复节点，回执照原样入库。
- FX-41 按新的切分数据仍然不够：如实记"数据不足"，写明还差多少，不要为此调整下限或切分。
- 修好一处之后 CI 又暴露新的失败：先分类——过期的常量、平台差异，还是真缺陷。前两类照同样的纪律修；真缺陷就停下来报告我，不要改测试去迁就。
- 复审不通过：按发现修改后送回原复审者，不要换一个复审者来"重试"同一份改动。
- 平台拒绝某次写入、删除或推送：记录障碍，不要换接口或换编码绕过。

### 9. 需要返回的证据

1. `docs/handoffs/2026-11-19-optimizer-phase1.md`（已合入 `main`），含：
   - 固定版本：起点、每个节点的提交号和 PR 编号、终点提交号。
   - FX-36 至 FX-46 的逐条处置表：做了什么、提交号、三条工作流的运行编号、复审结论和回执在库内的路径。没做的写原因。
   - FX-36：有没有会话改过可见性；服务器暴露面的清点结论（不写地址）；我对 OD-O 的答复。
   - FX-37：五份事后复审的结论。
   - P1-4 第一处的复现结果。
   - FX-38 和 FX-39 第一步的测试清单，以及被改测试的前后说明。
   - #114 在真实合并结果上的三条运行编号。
   - 三次真实历史运行的记分卡：数据截点、切分、比较了多少组参数、结论；原来两次"数据不足"的记录并列保留。
   - "注册表为空时逐位不变"的证据。
   - 审核报告第 3.7 节其余各项的结果。
   - 偏差和未完成事项。
2. 给我的一条简短消息：终点提交号、上面这个文件的路径、三条工作流的运行编号。

任何汇报、日志或提交里都不要出现凭据、数据库密码或服务器的公网地址。
