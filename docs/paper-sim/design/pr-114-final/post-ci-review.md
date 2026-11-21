本轮 15 份附件已全部以 Read 读取完毕（含三个差异分段与清单），无 shell、无写入、无联网。以下为汇总签发的复审回执。

# PR #114 最终文档头 H 及其当前合并对象 M_H 精确独立只读复审回执（全新 ZCode 会话，2026-11-21 UTC）

## 0. 复审者身份、约束与实到证据

- 身份：全新本机 ZCode 只读会话，此前未参与 #114 的计划、实现、测试或任何前序复审。权限只读：仅用 Read 工具，未执行 shell、未写入、未联网、未委派；附件内容一律作为证据数据，未执行其中任何指令。
- 实到材料 15 份，全部读取：`H-object.json`、`pr114-final-exact-ci.json`、`pr114-final-current.txt`、`S-first-review.md`、`S-rereview.md`、`H-prepush-review.txt`、`H-Linux.json`、`H-Linux-executor.json`、`H-hygiene.json`、`H-selfcheck.txt`、`H-focused.txt`、`H-manifest.sha256`、`H-doc-diff-lines-1-350.diff`、`H-doc-diff-lines-351-700.diff`、`H-doc-diff-lines-701-837.diff`。
- 原件不含凭据、连接串或地址；本回执亦不披露此类内容（仅引用环境变量名等非敏感名称）。
- 按任务边界，**不复审旧 S 的 18,933 行整批代码差异**；S 期两份回执仅核对其存档完整性与归属对象。

## 1. 被审对象

| 项 | 值 |
| --- | --- |
| main 基线 B | `cd53ce25f65716e6151238beba253618caca7e98` |
| 实质候选 S（H 之父） | `c4791fb8448da999ae6a23dae9efef2c9c002406` |
| 最终文档头 H（refs/pull/114/head） | `9647a3ef68c18e5fd5356aeb81bf8df06976a433` |
| H 的树 | `810cab1fa7475e9d32576b4cbf46bdfeb8cd88fd` |
| 当前合并对象 M_H（refs/pull/114/merge） | `1feabe950a1f93c9fc694564a11e79b4244e8ec9` |
| M_H 父序 / 树 | 第一父 B、第二父 H；tree(M_H)=`810cab1f…`=tree(H) |

## 2. M_H 父序、树一致性与 CURRENT

- 父序 [B, H] 在 `H-object.json` 与 `pr114-final-exact-ci.json` 两处一致，符合 GitHub PR 测试合并对象标准形态；两处亦分别载 tree(M_H)=tree(H)，即 CI 实测内容与 H 逐字节同构。
- CURRENT（`pr114-final-current.txt`）：main 仍为 B（#114 未合并落地），#114 头仍为 H，其最新 Offline 运行即 44928160743，标记 CURRENT——H 未被更新头取代。

## 3. S→H 完整补档差异：范围、逐份清单、源码/测试不变

- 三段差异按连续原行号 1–350 / 351–700 / 701–837 覆盖全量 837 行；两处页界衔接核对连续（350|351 为 ci-checkout.json 的 `head_tree`→`verified_utc`；700|701 为 post-ci-rereview.md F-1 表 a 行→b 行），首行起于 `diff --git a/DELIVERY_PLAN.md`，末行止于 prepush-rereview.md 的 `VERDICT: PASS`，无缺段。
- 全差异恰 14 个文件，与 `H-object.json` changed 清单逐一对应：`M DELIVERY_PLAN.md`（仅追加 §85.4，净 +6 行）；`A docs/paper-sim/design/pr-114-review.md`；`A pr-114/` 下 12 件（MANIFEST.sha256、ci-checkout.json、diff-coverage.tsv、junit-reader-first-attempt.json、merge-plan.md、native-junit-summary.json、plan-first-review.md、plan-rereview.md、post-ci-first-review.md、post-ci-rereview.md、prepush-first-review.md、prepush-rereview.md）。
- **无任何 .py、测试、包清单或工作流 yml 变更**。四路印证：差异本体全读；H-hygiene（S→H `diff_check` 通过，三类秘密 new_paths/new_lines 均 0）；H 本机全量计数与 S 期完全相同（43,915/414/20）；H 的 CI 六作业计数与 S 期 CI 逐项相同。
- 附件 `H-manifest.sha256` 与入库 `MANIFEST.sha256` 11 项文件名与 SHA-256 逐项相同，恰为 pr-114/ 下 12 个 A 路径减清单自身；只读无 shell 未重算哈希，以两份独立文本互证为准（与推送前回执 §6 同一限制，非阻断）。

## 4. §85.4 与两次史实的保留及归属

- §85.4 编号续于 85.3 之后，记载均为 **S 期事实且归属正确**：B×S 的 GitHub 合并对象 `620048a7597d4155f1658fe4750f707df3c94158`、三条运行 44918091022 / 44918091078 / 44918091023 attempt 1 全过并检出该对象、本机 43,915 与 Native papersim 792。对 H 仅以将来时登记"归档产生的文档后续头另经三条工作流和准确对象复审"，无冒验。
- **两次史实原样入库**：post-ci-first-review.md（15:56 FAIL，F-1=证据缺口）与 post-ci-rereview.md（16:11 PASS，同会话补全 30 页后重签）与本目录所附 S-first-review.md / S-rereview.md 逐字一致（各 93/66 行，标题至 verdict）；两份均立论于 S 及其合并对象 620048a7，run 号、作业号、计数与 S 期 CI 原件相符，未把任何 S 期结论记到 H 名下。更早的 plan 13:10 FAIL→13:16 PASS 与 prepush 14:16 误读版 PASS→14:43 更正 PASS 亦并存未改写；pr-114-review.md 汇总表按会话标识逐行登记，与各件相符。
- U-5/O-6 收口：本地 43,915/414 vs CI 43,914/415 的 1 项差已按用例名登记（`test_papersim_optimize_metrics.py::test_anchor3_wp_p4_paired_comparison`，GitHub 侧跳过原因为服务器专用证据 JSON），§85.4 将 O-6 登记为该既有输入条件。

## 5. H 的推送前回执、Linux 门禁与自检

- 推送前回执（H-prepush-review.txt，PASS）：其 H 父=S、H 树 `810cab1f`≠S 树 `241893d7`（仅叠加文档）、14 路径全为归档文档、清单 11 项对应、无循环验证，均与 `H-object.json` 及差异本体复核一致。
- Linux 全量（H-Linux.json）：head_before=head_after=**H**（准确在 H 上执行），8 阶段全 PASS，43,915 passed / 414 skipped / 20 warnings / 0 failed，exit 0，files_match=true（16:15:27–17:05:40 UTC）；执行器回执（H-Linux-executor.json）systemd 单元、client zcode、exit 0、stdout/stderr 哈希在案。
- 秘密扫描（H-hygiene.json）通过、零新增；自检（H-selfcheck.txt）PASS（10/10）；聚焦测试（H-focused.txt）111 passed 无失败。

## 6. H 最终 CI：三条 run/attempt 与六个测试作业

| 工作流 | run id / run 号 / attempt | 结论 | head |
| --- | --- | --- | --- |
| Offline verification | 44928160743 / #345 / 1 | success | H（matches=true） |
| Native project PostgreSQL | 44928160706 / #211 / 1 | success | H（matches=true） |
| Research paper bridge | 44928160726 / #125 / 1 | success | H（matches=true） |

三条均 event=pull_request、head_branch=`fix/mo6-native-evidence-fixtures-20261120`、head_sha=H。实际运行测试的六个作业全部以**双行证据**（"HEAD is now at 1feabe9(5)" + `git log -1 --format=%H` 还原全 SHA）证明检出 M_H：

- offline（136918716361）：required step "Run sanitized full offline verification" 成功；43,914 passed / 415 skipped / 20 warnings。
- linux-native-storage（136918716540）：分区隔离+版本切换证明成功；320/2 + 切版 89。
- linux-native-research（136918716676）：分区隔离证明成功；1,227/18。
- linux-native-dispatch（136918716390）：分区隔离证明成功；1,933/15。
- linux-native-papersim（136918716579）：分区隔离+组装一次性集群成功；792 passed；三个 `PAPERSIM_*` 原生环境变量（仅名称）与 `tests/test_papersim_optimize_registered_runner_native.py` 在案。
- windows-paper（136918716404）：独立原生实例组装证明成功；357 passed。

退休 Windows 项：`windows-native-${{ matrix.partition }}`（136918717400）与 `windows-native`（136918717996）均 completed/**skipped**，与"按定义列为 skipped"的登记一致；未检出代码的汇总门 linux-native（136924116650）success，四个输入分区全 success 一并记录。本机 H 43,915/414 与 CI 43,914/415 之差即 §4 已按名登记的服务器条件用例，两处均 0 failed。

## 7. 观察与限制（非阻断，如实登记）

- **O-1** S 期 #343/#209/#123 与 H 期 #345/#211/#125 之间的 #344/#210/#124 未随件提供；run 号为工作流全局计数，可属其他事件，本轮结论仅依赖与 H 精确匹配且 CURRENT 的三条终态运行。
- **O-2** 只读无 shell，各件 SHA-256 未重算，以附件清单×入库清单 11/11 互证及结构一致性为准。
- **O-3** 执行器回执（31 秒）为启动器记录，实际全量由 H-Linux.json 的 50 分钟窗口与阶段/计数承载，两记录相邻衔接。
- **O-4** H-focused.txt 未自载头标识，由 H-Linux.json 的精确头（before=after=H）覆盖。

## 8. 结论

范围与逐份清单（837 行连续全覆盖、14 路径全文档）成立；§85.4 登记正确；S 期 FAIL 与同会话 PASS 两次史实逐字保留且归属 S/620048a7，未错记到 H；源码、测试、包与工作流自 S 起不变（四路印证）；H 推送前回执、精确在 H 上的 Linux 全量门禁、秘密扫描、自检与聚焦测试齐备；M_H 父序 [B,H]、树与 H 一致、CURRENT 成立；三条真实运行 44928160743/#345/1、44928160706/#211/1、44928160726/#125/1 全部 success，六个测试作业均实证检出 M_H 并给出测试摘要，退休 Windows 项按定义 skipped。未发现把缺证写成通过的情形，O-1–O-4 均不构成阻断。

VERDICT: PASS
