# 06 给本地代理的提示词

"提示词正文"一节可以整段复制给本地代理（项目的协调者）。它是自包含的：目标、不要重复的工作、固定版本、允许与禁止、环境、步骤、验收、失败处理、要返回的证据都在里面。

固定值：

| 项目 | 值 |
| --- | --- |
| 仓库 | `https://github.com/wmqfl861/polymarket-alpha-lab` |
| 交付分支（保留，不删） | `handoff/audit-optimizer-2026-11-19` |
| 被审核的提交（本分支的起点） | `c4367b6b3ce4d957ce79ce645a7ab3425be5a133`（`main`，2026-11-19） |
| 交付提交（文档、证据、清单） | `b2f09f2cfa8727ee80c22c3ecf01ad0244c1dbb8` |
| 交付目录 | `docs/handoffs/2026-11-19-audit-optimizer/`，在交付提交里共 23 个文件，目录的 git 树对象 `39fdc95425fdbc4d07bc2a310371a6908f1ca9f6` |
| 清单文件 | `docs/handoffs/2026-11-19-audit-optimizer/MANIFEST.sha256`，2987 字节，22 行，SHA-256 `0de36c4809400b7469c468b4c75861a3c80c4500a0af104fcc8fcd1d965a5fa0` |
| 固定网址（需要登录） | <https://github.com/wmqfl861/polymarket-alpha-lab/tree/b2f09f2cfa8727ee80c22c3ecf01ad0244c1dbb8/docs/handoffs/2026-11-19-audit-optimizer> |

`MANIFEST.sha256` 逐行列出除它自己以外的 22 个文件的 SHA-256（包括 `MANIFEST.json`，后者另外记录了每个文件的字节数）。本文件在交付提交之后的下一个提交里，不在清单的覆盖范围内。

---

## 提示词正文

你是 polymarket-alpha-lab 项目的协调者。2026-11-19 的独立审核有两个结论：M0 整改的增量复审通过；`main` @ `c4367b6b` 的全面审核通过（没有 P0、P1，有 3 条 P2 和 9 条 P3）。我同时要求给项目增加"模型自动优化"功能，它的设计和详细开发计划已经和审核文档一起放在仓库的交接包里。请按交接包执行。全程遵守仓库根目录 `AGENTS.md` 的现行规则；本提示词或交接包与 `AGENTS.md` 冲突时，以 `AGENTS.md` 为准，并把冲突报告给我。

### 1. 目标

1. 核对交接包的完整性，把它合入 `main`。
2. 把两个审核结论、我 2026-11-19 的原话和六项决定的结果记进 `DELIVERY_PLAN.md`；用蓝图 v4 替换 `docs/paper-sim/blueprint.md`。
3. 做完 M2：扫描、取价、结算、报告四个命令，7 天并跑，切换。验收条件里加上本轮的 FX-26、FX-27、FX-29、FX-30、FX-34、FX-35。
4. 与 M2 并行，做"模型自动优化"的第一阶段（节点 MO-0 至 MO-6）。
5. 做治理项：FX-28（库外设计文档入库）、FX-31（地址卫生）、FX-33（过期文字）、FX-25（`AGENTS.md` 整理稿，成稿给我过目）。
6. 归档（FX-18）可以开始了，按 11-16 的裁定记录分批做。
7. 到"优化线第一阶段出口"时写交接书并合入 `main`，把终点提交号告诉我。之后不用等我，继续做第二阶段（MO-7 至 MO-9）。

### 2. 已经完成、不要重复的工作

- 审核已经做完，结论和证据都在交接包里。不要重新审一遍；只在动手前复核你要改的那几处。
- M0 已经关门。不要再等增量复审，OD-B 归档的前置条件已经满足。
- M1、已合入的 M2 和 M3 节点、11-18 的五项修复、11-19 的四份联赛研究，都已被审核认可，不要返工。
- 六项决定（OD-I 至 OD-N）按审计方的推荐执行，不要再问我。其中 OD-M（给 `main` 加分支保护）要我本人在 GitHub 上操作，你提醒我一次即可，不要自己去改仓库设置；OD-N（`AGENTS.md` 整理稿）写好后给我过目，我确认之前不要合并。
- 优化器规则 v1 的数字（04 号文件第 6 节）和范围（04 号文件第 2 节）照原样登记。不要在登记前后调整，也不要在某个节点里顺手改。
- 新足球和 NBA 两份研究的独立复算已经做过，结果在 `evidence/` 里。欧洲篮球和欧洲国内足球两份没有复算，列在审核报告第 3.8 节，属于你要做的本地复核。
- 2026-11-11 那份已作废的审核和交接仍然不要执行。

### 3. 固定版本与完整性校验

| 项目 | 值 |
| --- | --- |
| 仓库 | `https://github.com/wmqfl861/polymarket-alpha-lab`（命令里写完整地址，不依赖本地 `origin` 指向哪里） |
| 交付分支 | `handoff/audit-optimizer-2026-11-19` |
| 被审核的提交 | `c4367b6b3ce4d957ce79ce645a7ab3425be5a133` |
| 交付提交 | `b2f09f2cfa8727ee80c22c3ecf01ad0244c1dbb8` |
| 交付目录 | `docs/handoffs/2026-11-19-audit-optimizer/`，23 个文件，git 树对象 `39fdc95425fdbc4d07bc2a310371a6908f1ca9f6` |
| 清单 | 该目录下的 `MANIFEST.sha256`：2987 字节，22 行，SHA-256 `0de36c4809400b7469c468b4c75861a3c80c4500a0af104fcc8fcd1d965a5fa0` |

把下面的命令保存成仓库之外的一个脚本文件，在仓库检出的根目录用 `bash` 运行。它只读取，不改动任何东西；任何一步不符都会以非零状态退出，这时停下来把输出报告给我，不要继续，也不要换别的来源代替。校验读的是 git 对象而不是工作区文件，所以不受换行符转换的影响。

```bash
set -euo pipefail
REPO=https://github.com/wmqfl861/polymarket-alpha-lab.git
DELIVERY=b2f09f2cfa8727ee80c22c3ecf01ad0244c1dbb8
BASE=c4367b6b3ce4d957ce79ce645a7ab3425be5a133
DIR=docs/handoffs/2026-11-19-audit-optimizer

# 1. a clean checkout, and the delivery commit is reachable from the retained branch
test -z "$(git status --porcelain)" || { echo "STOP: working tree not clean"; exit 1; }
git fetch "$REPO" handoff/audit-optimizer-2026-11-19
git cat-file -e "$DELIVERY^{commit}"
git merge-base --is-ancestor "$BASE" "$DELIVERY"
git merge-base --is-ancestor "$DELIVERY" FETCH_HEAD

# 2. the delivery touches nothing outside the handoff directory
test -z "$(git diff --name-only "$BASE" "$DELIVERY" | grep -v "^$DIR/" || true)" \
  || { echo "STOP: delivery changes files outside $DIR"; exit 1; }
test "$(git diff --name-only "$DELIVERY" FETCH_HEAD)" = "$DIR/06-local-agent-prompt.md" \
  || { echo "STOP: unexpected changes after the delivery commit"; exit 1; }

# 3. pinned tree, file count and manifest
test "$(git rev-parse "$DELIVERY:$DIR")" = "39fdc95425fdbc4d07bc2a310371a6908f1ca9f6" \
  || { echo "STOP: directory tree mismatch"; exit 1; }
test "$(git ls-tree -r --name-only "$DELIVERY" -- "$DIR" | wc -l)" -eq 23 \
  || { echo "STOP: file count mismatch"; exit 1; }
test "$(git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | wc -c)" -eq 2987 \
  || { echo "STOP: manifest size mismatch"; exit 1; }
test "$(git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | sha256sum | cut -d' ' -f1)" \
  = "0de36c4809400b7469c468b4c75861a3c80c4500a0af104fcc8fcd1d965a5fa0" \
  || { echo "STOP: manifest hash mismatch"; exit 1; }

# 4. every file listed in the manifest matches its hash
git cat-file blob "$DELIVERY:$DIR/MANIFEST.sha256" | while read -r sum path; do
  actual=$(git cat-file blob "$DELIVERY:$path" | sha256sum | cut -d' ' -f1)
  if [ "$actual" != "$sum" ]; then echo "STOP: MISMATCH $path"; exit 1; fi
done
echo "handoff package verified: $DELIVERY"
```

预期的最后一行输出是 `handoff package verified: b2f09f2cfa8727ee80c22c3ecf01ad0244c1dbb8`。

### 4. 允许与禁止

**允许**

- 按 03、04、05 三份文件新增和修改代码、测试、迁移、文档，范围限于各节点写明的文件集合。
- 新增迁移文件；给 `papersim/store.py`、`papersim/store_psycopg.py`、`papersim/cli.py` 增加函数和子命令；给国家队和 NBA 两个影子模型的函数增加带默认值的仅限关键字参数。
- 在 Linux 上运行测试；通过 PR 触发两条工作流；在一次性的隔离数据库上做端到端验证。
- 在运行服务器上做只读清点和只读查询（审核报告第 3.8 节的六项）。
- 把已登记的历史数据作为快照登记进受管库，用于优化运行。
- 各节点满足 `AGENTS.md` 推送规则列出的全部条件之后，经 PR 合入 `main`。

**禁止**

- 任何真实交易、账户认证、钱包、私钥、签名、下单相关的代码或操作。
- 读取、打印、移动或提交任何凭据。
- 对线上旧账本数据库执行任何写操作；执行生产历史导入（它需要单独的操作授权）。
- 修改冻结内容：四个闸门阈值、十变体集合、自检的十项检查、`tests/fixtures/papersim/` 下四个冻结夹具、已发布的评估回归数字。修改、删除或重放已有的迁移文件。
- 修改 `gates.py`、`risk_policy.py`、`crypto_model.py`、`xg.py`、`recalib.py`，以及 `soccer_model.py` 里已有的数值逻辑。优化线只在它们外面包一层。
- 让优化器读写或影响下列任何一项：闸门阈值、仓位、费率、晋级标准 OD-G、入场权限、暂停状态。
- 在登记之后修改优化器规则的数字或扩大搜索空间，除非先报告我并重新登记。
- 为了让 CI 变绿，或者为了让某次优化运行"有产出"，而跳过、删除、放宽测试或规则。
- 新增数据库连接模块；新增第三方依赖；新增文件型的持久化。
- 修改 V1 研究线的源码或启动任何真实的大模型调用。
- 在我确认成稿之前合并 `AGENTS.md` 的任何修改；自行修改 GitHub 的仓库设置。
- 修改 `docs/handoffs/2026-11-12-audit/`、`docs/handoffs/2026-11-13-m0-review/`、`docs/handoffs/2026-11-19-audit-optimizer/` 下的文件。
- 强制推送、改写历史、跳过独立只读复审、使用快速模式。
- 把服务器的公网地址写进任何新文件、日志或提交说明，包括引用规则文件原文时。

### 5. 环境

- 测试结论以 Linux 为准。
- Python 环境：`uv sync --locked --extra dev --extra postgres --python 3.12`
- 全量离线验证：`.venv/bin/python scripts/verify_local.py --full`
- 真实数据库分区在本机跑的环境变量和命令，以 `.github/workflows/native-postgres.yml` 里"Prove isolated native partition"和"Prove native version switch"两步为准。
- 模拟盘自检：`.venv/bin/python -m polymarket_alpha_lab.papersim.cli selfcheck`，最后一行应为 `selfcheck: PASS (10/10 checks)`。
- 合入 `main` 的做法沿用 PR #68：开 PR，PR 头上"Offline verification""Native project PostgreSQL""Research paper bridge"三条工作流都成功之后再合并；用"创建合并提交"的方式，不用压缩合并，也不用变基合并。

### 6. 执行步骤

**步骤 0：把交接包合入 `main`**

第 3 节的校验通过之后执行。同样保存成仓库之外的脚本文件，在仓库检出的根目录用 `bash` 运行。它在一个新的本地分支上做合并，不推送；如果交付提交已经在 `main` 里，它会直接退出。

```bash
set -euo pipefail
REPO=https://github.com/wmqfl861/polymarket-alpha-lab.git
DELIVERY=b2f09f2cfa8727ee80c22c3ecf01ad0244c1dbb8
DIR=docs/handoffs/2026-11-19-audit-optimizer
WORK=integrate/audit-optimizer-2026-11-19

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
git fetch "$REPO" handoff/audit-optimizer-2026-11-19
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

交接包只新增一个目录，不应有冲突；有冲突就停下来报告，不要强行解决。合并是一个节点：做 `git diff --check`、秘密扫描和独立只读复审（复审范围包括这份提示词）。然后按第 5 节的做法把这个分支经 PR 合入 `main`。必须用合并提交：压缩合并或变基合并会让交付提交不在 `main` 的历史里，第 3 节的校验以后就无法通过。合入后不要删除保留分支 `handoff/audit-optimizer-2026-11-19`。

**步骤 1：阅读**

按顺序读 `docs/handoffs/2026-11-19-audit-optimizer/` 下的 `README.md`、`01-audit-report.md`、`02-blueprint-v4.md`、`04-optimizer-design.md`、`05-development-plan.md`、`03-fix-list.md`。行号对应被审核的提交；对不上就以符号名为准。

**步骤 2：登记（节点 MO-0）**

在 `DELIVERY_PLAN.md` 新增一节，原样记入：

- 两个审核结论和审核报告第 3.4 节的发现编号表。
- 我 2026-11-19 的原话："完整审核项目，然后我需要增加模型自动优化的功能，请写好开发计划给我，连同审核文档，更新的蓝图。一起推送仓库。注意修改和开发计划需要详细。然后给我提示词给到本地agent"。
- 六项决定（05 号文件第 8 节的表）及其结果：OD-I 开；OD-J 按建议值登记，登记日就是你写入的那一天；OD-K 采用；OD-L 采用；OD-M 开启，等我操作；OD-N 起草，成稿待我过目。
- 优化线的范围（04 号文件第 2 节）和规则 v1 的数字（04 号文件第 6 节的两张表和"包络不能变窄"一段），原文照抄。
- 05 号文件的节点表。

同一个节点里：用 02 号文件的内容替换 `docs/paper-sim/blueprint.md`，文首注明 v3 保留在 git 历史里；状态板增加"模型自动优化"一行，并改掉审核报告 N-8、N-9 指出的过期文字（FX-33）；把 04 号文件第 10 节的提供者接口写进 `run-scan` 的节点计划。

**步骤 3：并行开工**

按 05 号文件第 4 节的泳道和单写者表派发。每个节点：写计划、过计划复审、实现、在 Linux 上验证、一个聚焦提交、过独立只读复审、经 PR 合入、登记运行编号和结论。

| 泳道 | 先后顺序 |
| --- | --- |
| 数据库 | FX-26 → MO-2 |
| 评估 | FX-27 与 FX-30（同一个节点）→ 把聚簇重采样提成公开函数 → FX-35 |
| 扫描 | `run-scan`（从第一天起经提供者接口取模型规格）→ `capture-marks`（含 FX-34）、`settle`（含 FX-29）、`report` 并行 |
| 优化内核 | MO-1 → MO-3、MO-4 并行 → MO-6 |
| 模型（每族一条） | MO-5 的五个子节点 |
| 协调者 | FX-28、FX-31、FX-25 草稿；合并各泳道的命令行和导出清单 |
| 归档 | FX-18，独立工作树，每批合入前变基到最新的 `main` |

三件事要特别守住：

- 注册表为空时，一切现有行为必须和今天逐位相同。每个 MO 节点都要验这一条。
- MO-3 的四组回归锚点必须原样复现。复现不了就查口径并写明，不要改锚点的数字。
- 一次优化运行的结论是"没有更好的"，属于正常结果，如实登记。

**步骤 4：优化线第一阶段出口**

条件见 05 号文件第 3 节"第一阶段出口"：MO-0 到 MO-6 全部合入；两条 CI 为绿；注册表为空时行为逐位不变；俱乐部足球、国家队、NBA 各完成一次真实历史数据的优化运行并且记分卡入档。达到后写 `docs/handoffs/2026-11-19-optimizer-phase1.md` 并合入 `main`，内容照第 9 节。然后给我一条简短的消息。

**步骤 5：之后**

交回之后不必等复审结论，继续做：M2 的并跑和切换；优化线第二阶段（MO-7 至 MO-9），它依赖 `run-scan` 和 `capture-marks` 已经合入。第二阶段出口再交一次，写法相同。

### 7. 验收标准

- 每个节点的验收标准写在 03 号文件和 05 号文件对应的小节里，逐条满足并留证。
- 每个合入 `main` 的 PR，PR 头上的三条工作流都为绿，运行编号已登记。
- 没有任何测试被跳过、删除、放宽或挪出矩阵；被改的测试都写明了改动前后各证明什么。
- 自检十项的输出逐字节不变；四个冻结夹具不变；评估模块已发布的回归数字不变。
- 优化器规则的数字只在一处定义，与 `DELIVERY_PLAN.md` 登记的逐一相同。
- 在终点提交上重跑两个证据脚本（用法写在各自开头）：`evidence/test_weakening_scan.py`（基线 `c4367b6b`）报告的"被移除的测试""断言变少的测试""新增的预期失败标记"要么为 0，要么在交接书里逐条说明；`docs/handoffs/2026-11-13-m0-review/evidence/address_occurrence_diff.py`（基线 `c4367b6b`）的输出里没有以 `new:` 开头的行。
- 最终是否通过由审计方的增量复审给出。

### 8. 失败处理

- 第 3 节任何一条校验不符：停止，报告实际值，不要用别的来源替代。
- `git fetch` 因为认证方式失败：把 `REPO` 换成你访问这个仓库惯用的地址再试；校验只认提交号和校验值，与传输方式无关。
- 工作区不干净：不要 stash，也不要丢弃已有的改动。先把手头的改动按正常流程提交完，或者换到一个干净的克隆里执行；做不到就停下来报告。
- 设计稿与事实不符（接口、表结构、文件位置、估算）：以事实为准，在节点计划里提出改法并过复审，交回时单列。但 04 号文件第 2 节的范围和第 6 节的规则数字不在此列，要改先报告我。
- 回归锚点复现不了：先分清是口径不同、输入不同，还是实现有错。前两种写明差异；不要为了对上而改锚点或改实现的口径。
- 找不到 FX-28 要入库的某份库外文档原件：如实登记"原件缺失"，把对应的代码注释改为指向实际存在的依据。
- 修好一处之后 CI 又暴露新的失败：先分类——过期的常量、平台差异，还是真缺陷。前两类照同样的纪律修；真缺陷就停下来报告我，不要改测试去迁就。
- 归档和开发改到了同一批文件：归档让路，等开发节点合入后变基再做。
- 复审不通过：按发现修改后重新送审，不要换一个复审者来"重试"同一份改动。
- 交接包里的陈述与你看到的事实不符：以事实为准，写进计划记录并在交回时单列。
- 平台拒绝某次写入或推送：记录障碍，不要换接口或换编码绕过。

### 9. 需要返回的证据

1. `docs/handoffs/2026-11-19-optimizer-phase1.md`（已合入 `main`），含：
   - 固定版本：起点、每个节点的提交号和 PR 编号、终点提交号。
   - 逐节点处置表：节点、做了什么、提交号、三条工作流的运行编号、复审结论和发现摘要。没做的写原因。
   - FX-26 至 FX-35、FX-25 草稿、FX-18 各自的状态。
   - 三次真实历史优化运行的记分卡：数据截点、数据集摘要、比较了多少组参数、结论。
   - "注册表为空时逐位不变"的证据，以及 MO-3 四组回归锚点的复现结果（第 3 组在服务器上做，写明输入的快照校验值）。
   - 审核报告第 3.8 节六项各自的结果。
   - 偏差和未完成事项。
2. 给我的一条简短消息：终点提交号、上面这个文件的路径、三条工作流的运行编号。

任何汇报、日志或提交里都不要出现凭据、数据库密码或服务器的公网地址。
