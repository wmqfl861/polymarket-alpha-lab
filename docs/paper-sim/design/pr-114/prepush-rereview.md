# PR #114 预推送候选独立静态复审回执（第二版：对象身份复核后重签）

复核说明：本回执在原会话中对第一版回执（已存档为 `first-verdict.txt`，保留不改）进行更正重签。方法：仅使用本次所附五份材料（`object-identity.md`、`first-verdict.txt`、`overlap.txt`、`relative-to-main.txt`、`frozen-and-scope.txt`）及本会话首轮已附的 `identity.txt` 记录；未调用任何工具，未写入，未访问其他文件。

## 1. 第一版回执的错误认定

对照 `object-identity.md` 的命名字段，第一版第 1 节把 `identity.txt` 第 1 行的四段哈希按"提交、父1、父2、树"错位解读，实为标准对象身份顺序"提交、树、父…"。具体三处错误：

1. **把树对象误判为第一父**：`241893d7` 是候选 H 的**树对象（非提交）**，我却称其为"父 1 = 当前 main 尖"。
2. **遗漏真实第二父**：`cd53ce25` 是 H 的**第二父，即当前 main**（已合入交接包与 #116 的 main，其树为 `20a13487`，与 object-identity.md 第 7 行互证），我却把它当作"候选树"。
3. **推送结论错误**：由此推断"父 1 指向 main……可直接快进推送 [main]"。实际待执行的写入是**普通推送到原 #114 PR 源分支**；合入 main 走独立的 GitHub PR 合并门禁。

同一错位还波及对第 2 行的解读：第一版称 `26dfa2a0` "本身是 `3f334c2c` 与 `17dced47` 的合并"，实为 `26dfa2a0` 的**树是 `3f334c2c`、唯一父是 `17dced47`**——即专项修订提交直接落在首个组合头之上，这与 §85.3 的记载反而更吻合。

## 2. 更正后的对象身份（object-identity.md × identity.txt 逐字段核对）

| 字段 | 哈希 | 两份材料互证 |
| --- | --- | --- |
| 候选提交 H | `c4791fb8` | object-identity.md 第 3 行 = identity.txt 第 1 行第 1 段 |
| H 的树对象 | `241893d7` | object-identity.md 第 4 行 = 第 1 行第 2 段 ✓ |
| H 的第一父（合并前 #114 分支头） | `26dfa2a0` | object-identity.md 第 5 行 = 第 1 行第 3 段 ✓；其单父 `17dced47` 与 §85.3"首个组合头"一致 |
| H 的第二父（当前 main，已合入交接包与 #116） | `cd53ce25` | object-identity.md 第 6 行 = 第 1 行第 4 段 ✓；identity.txt 第 3 行按同序解为"提交 `cd53ce25`、树 `20a13487`、父 `a3492ea6`/`8c7b5365`"，其树字段与 object-identity.md"main 的树对象 `20a13487`"完全吻合 |

四段哈希在更正后的字段序下全部对位、无游离值。两父序判定更正为：**父 1 = #114 分支头、父 2 = main**，即在 #114 分支上普通并入 main 的标准形态，保持了 PR 线的第一父连续性——对该写入目标而言父序正确；第一版引用的"GitHub 合并提交惯例（父 1 = main）"理由作废。

## 3. 上述错误对其余结论的影响评估

- **树三方合并一致（第一版 §2）：不受影响。** 该结论锚定在 blob 哈希而非父序解读：main→候选与 PR 头→候选两个方向的差异在三个双侧同改文件上收敛于同一终态 blob（`479e0a19`/`c7c3414c`/`ae52ee08`），纯并集、无删行。方向标注（main 侧带入 D1 journal/frozenrun 与 FX-35/根规则改写；PR 侧保留 `registered_runner_native`、§85 及 `settle_store` 位次按 §85.1 优化器分支既有次序）基于内容与 §85.1 记载，随更正后的父序（父 1 = PR、父 2 = main）语义不变、更趋标准。
- **相对范围（§3）：不受影响。** 23 文件清单（8 个 papersim 源、13 个测试含 3 个新增、yml、DELIVERY_PLAN）为候选相对 main 的差异，与 `relative-to-main.txt` 展示的 3 个共享文件吻合，其余 20 个文件不在该差异附件中但由清单覆盖；范围判定不依赖父序。`object-identity.md` 另证 `git diff --check` 通过、工作树洁净。
- **§85 与登记簿（§4）：不受影响。** 纯内容核对：main 已有 §84，候选追加 §85/85.1–85.3，编号连续；运行 ID、回执、清单 SHA-256、FAIL→二审 PASS 链条自洽，且 `17dced47` 谱系引用在更正后仍成立。
- **Native 工作流与分区清单（§5）：不受影响。** yml papersim 分区与 `PAPERSIM_FILES` 收敛同序（…、`optimize_runner_native`、`settle_store_native`、`optimize_registered_runner_native`、`report_store_native`、`d1compare_archive_native`、`d1compare_journal`、`d1compare_frozenrun`）；`WINDOWS_PARTITIONS`/`LINUX_PARTITIONS` 未动，模拟盘角色边界保持。
- **冻结文件（§6）：不受影响。** "候选与 PR 头 identical"的锚点本就是 `26dfa2a0`，而它在更正后恰为第一父（#114 分支头），结论原样成立：8 个冻结源文件与 `tests/fixtures/papersim` 未被 main 侧（含交接包与 #116）的并入污染；main 侧 D1 文件不在 23 文件清单中，逐位一致。
- **推送结论（原 §1、§7 尾注）：撤回并更正。** 详见下节。

## 4. 更正后的推送结论与门禁范围

- 本回执覆盖的唯一本地写入门禁：将候选 `c4791fb8` **普通推送到原 #114 PR 源分支**。非强制推送要求远端 #114 分支头位于第一父 `26dfa2a0`（object-identity.md 所载"合并前的 #114 分支头"），满足即为快进，无需改写。
- 第一版"可直接推送 main"的说法**作废**；main 是候选的祖先（经第二父 `cd53ce25`）这一事实虽仍成立，但合入 main 须走独立的 GitHub PR 合并门禁，不属于本次签署范围。
- GitHub 当前合并结果的三条 CI 及后续记录按约定在推送后另行验证；独立测试会话的 Linux 全量与 CodeGraph 仍在别处执行，不计入本静态回执。

## 5. 残余限制（如实声明）

只读条件下仍无法重算树哈希或 `merge-tree`；本版的树级信心来自命名字段互证（§2 表）加三项可交叉验证证据：双侧差异收敛同一终态 blob、冻结文件相对第一父 identical、23 文件清单与差异附件互相吻合。静态层面未发现矛盾、越界、丢改或谱系错序；第一版的错误均为回执解读错误，候选对象本身经更正核对无恙。

VERDICT: PASS
