# 整改证据入库清单（docs/remediation-evidence/2026-11-14/MANIFEST.md）

R10 终局文档节点于 2026-11-14（UTC）建立。本目录把 2026-11-13—2026-11-14 M0 整改
收尾所依赖的**库外关键证据**原样复制入库；基线为最终 SHA
`B=f5ca185a50ad6349275cd79dabd5b701b65a2548`（tree
`b9c40393a2e2b9f3ad217b9f01545c7788a89b14`），在分支 `docs/r10-final-20261114` 上新增。
复制方式：字节级原样复制（Windows 侧 `cp` / 服务器侧 `scp`），未做任何换行、编码或
内容转换；入库后逐文件重算 SHA-256 与字节数登记于下表。

原始来源（库外，复制后原件未改动）：

- `PAL-R` = `D:\Projects\pal-remediation-plans-20261113`（Windows 协调侧证据目录）
- `LNX` = Linux 服务器 `/home/ubuntu/pal-m0-startup-20261113-yx2fyoje`（r03-fix 证据）
- `EXP` = Linux 服务器 `/home/ubuntu/r03-next-run-20261114-01`（机制实验 A / 本机对照）

## 1. 敏感检查（入库前逐文件扫描）

规则复用本项目既有 secret-scan 门禁：8 个关键词
（password / passwd / pgpass / private_key / api_key / access_token / secret /
authorization，大小写不敏感）加 dotted-quad IPv4（八位组 0–255 校验，排除
127.0.0.1 / 0.0.0.0 / 255.255.255.255）。

结果（2026-11-14，扫描 35 文件）：

- IPv4 命中：**0**（无公网 IP；唯一 IP 形态为回环 127.0.0.1，已按规则排除）。
- 关键词命中：41 处，**逐条人工核对全部为过程性/否定性文本**——secret-scan
  门禁自身的规则名与流程描述、"password-free DSN"／"No tokens, keys, DSNs, or
  password values" 等否定性复核结论、CLI 参数名/字典键名的文档引用；**无任何
  凭据值、token 值、密钥值或 DSN 值**。
- 读取/解码错误：0。
- 处置：**无需脱敏，未做任何改写**；本目录所有文件与库外原件逐字节一致。
- 字节保真附注（`git diff --check` 口径）：6 个证据文件会触发 git 的
  whitespace 提示——`ci-receipts/r09-final-dual-ci-20261114.json` 与
  `decision/decision-output.log` 原件即 CRLF 行尾（Windows 侧生成，\r 被计为
  行尾空白）；`r03-fix-evidence/focused-fix_state_{g,g2,r,r2}.log` 原件即含真实
  行尾空格（pytest 原始输出，哈希与 Linux 运行侧原件一致）。本目录按逐字节存证
  原样入库，不做行尾/空白归一（同 server-mirror 的 verbatim 存证口径）；本节点
  自写的三份文档（DELIVERY_PLAN 增量、D 交付书、本 MANIFEST）whitespace 检查为
  0 命中。

## 2. 文件清单（35 文件；sha256 / bytes / 相对路径 / 库外原始位置）

### ci-receipts/（CI 回执）

| sha256 | bytes | 相对路径 | 原始位置 |
| --- | ---: | --- | --- |
| `c86e3438354ed66fcb87039a8afeabd8aedd0e669ed3c80be3253bf7d0f13446` | 8471 | `ci-receipts/c3-publication-ci-receipt-99c510e5.json` | `PAL-R\c3-main-prep-166886b66268\` |
| `460dc65aedb9b4617da6339ebff94ebf20dc51cd926ba3073ae5da78e3689495` | 7508 | `ci-receipts/r09-final-dual-ci-20261114.json` | `PAL-R\r09-final-ci-20261114\` |
| `6d02d989afe20d8f86bf629e08ffe75f32680223529bfae0b245e479ad37dc0d` | 2503 | `ci-receipts/version-switch-proof-b-final.log` | `PAL-R\r09-final-ci-20261114\storage\version-switch-proof.log`（B run 43608957129 storage artifact 成员，与回执登记值一致） |

### decision/（owner 委托 codex 的终局裁定记录）

| sha256 | bytes | 相对路径 | 原始位置 |
| --- | ---: | --- | --- |
| `e688b67639ff99f36c5d770000d4978b40d451856fbc5d9be5feb74b1191de2f` | 7163 | `decision/codex-decision-brief-20261114.md` | `PAL-R\` |
| `de726bea2190db7a271560914940f58a09ad57fc0fff1077cbd878008cc703db` | 67413 | `decision/decision-output.log` | `PAL-R\codex-decision-20261114\`（含 DECISION 1–4 与 RATIONALE 原文） |
| `76c8da64feb9e29587b7c6a4392b357e1b5dc440a0c50121e215a2e8c7e75bd1` | 328417 | `decision/m1-rereview-output-20261114.log` | `PAL-R\`（M1 计划复审回执补齐：2026-11-14 codex gpt-6-astra/max 独立复审，VERDICT: PASS＝3 CORRECT-AS-APPLIED＋2 NO-CHANGE-CONFIRMED、0 新发现；原件 CRLF，同 decision-output.log 存证口径原样入库） |
| `d693680ad272a50f20330a965bcdff49d3da15255102544e48894dc3f2251bba` | 2825 | `decision/m1-rereview-brief-20261114.md` | `PAL-R\`（该回执的 owner 委派简报：复审对象 SHA256 锚点、背景锚点与输出要求；原件 CRLF 原样入库） |
| `a4ebe4f17e925b7001c5337f93f67c454bff68467c0bbf5972b4c6f9a9d8296b` | 5509 | `decision/codex-m1-final-brief-20261115.md` | `PAL-R\codex-m1-final-brief-20261115.md`（M1 整体终审 owner 委派简报：终审对象 d83ab064 锚点、核验清单、降级取证条款与整改记录；M1 出口回执节点 2026-11-16 追加入库，敏感扫描 8 词＋dotted-quad 均 0 命中；原件 CRLF 原样入库，索引 i/crlf 逐字节保真） |
| `e4d7a35de67833563d0fa17036291e0141a3d673138373b775b477930e1aa7` | 866313 | `decision/m1-final-review-rerun-20261116.log` | `PAL-R\codex-m1-final-rerun-output.log`（M1 整体终审复跑回执：codex gpt-6-astra/max，VERDICT: PASS＝0 未决 P1/P2＋新增 1 项 P3 报表缺口；含 P2-01/02/03 关闭裁定、铁律/冻结面核验与双 CI 降级取证边界；M1 出口回执节点 2026-11-16 追加入库，索引 i/mixed 逐字节保真。敏感扫描：8 词命中 50 处经逐条核对均为过程性/否定性文本（secret-scan 规则名、"password-free DSN" 类否定性结论、测试函数名、passfile 占位 DSN 模板），无凭据/token/密钥值；IPv4 命中 1 处＝SERVER_HOST_REDACTED，系终审读取 AGENTS.md 时引用的既有 tracked 内容（HEAD 中 10+ 文件已含），非新增披露，其余 3 形态为排除项 127.0.0.1/0.0.0.0/255.255.255.255；原件 CRLF+LF 混合行尾原样入库，不做行尾/空白归一） |

### plans/（r03 三计划 + 复审结论要点 + distribution 计划）

| sha256 | bytes | 相对路径 | 原始位置 |
| --- | ---: | --- | --- |
| `447caac687605328e8ae9a279df43597e7590e0ad2e6dc19085dbb38914dc567` | 6303 | `plans/r03-diag2-plan-20261114.md` | `PAL-R\`（状态行含 READY_FOR_FRESH_PLAN_REVIEW；独立复审 PASS 为协调者派发事实，产物 ad001c77） |
| `bd01921ad1f7f02a55a464bf4d8376bba0992be6fb883c76ce9e64c1ea76f98b` | 10469 | `plans/r03-fix-plan-20261114.md` | `PAL-R\`（REV1→REV3 状态行登记复审链与 REV1 误述事实） |
| `027f877e8c7ce01779c046fd15891b0f4b092416563df86cc467e66c74bf55d5` | 19345 | `plans/r03-next-diagnostic-plan-20261114.md` | `PAL-R\`（状态行：PLAN_REVIEW_PASS，无阻断，P2-1/P2-2/P3 已应用） |
| `2027fbad656549518227dfb498318413ac6f0077bc74953e13ea4dee2350d92e` | 7839 | `plans/r03-next-tools-MANIFEST.md` | `PAL-R\r03-next-tools-20261114\MANIFEST.md`（驱动/观察者脚本复审结论要点：fresh 脚本复审 PASS 0 P1，rev2/rev3 修正项逐条登记） |
| `08bda4f4667e1bd9983a9dd624cd84a533d4fcb10420fa889d4947daaf66944a` | 29482 | `plans/distribution-retry-final-plan-20261114.md` | `PAL-R\`（distribution 节点计划；独立计划复审 PASS 为协调者登记事实，产物 7d993df5） |

### experiments/（根因机制实验 A 与本机对照，复制自 Linux 服务器）

| sha256 | bytes | 相对路径 | 原始位置 |
| --- | ---: | --- | --- |
| `b07a442906397940b68d3c4f95e894eec63b5e9736d119f8df89c8fdbbfa5617` | 6500 | `experiments/control-report.json` | `EXP\control-evidence\report.json`（outcome=CONTROL_EXPECTED_MATCH） |
| `79c50261a391732fa0f089edfc42f6acfe2fd1f2e322f8868cd0a934fb087d5a` | 127 | `experiments/control-evidence-manifest.json` | `EXP\control-evidence\evidence-manifest.json` |
| `2406322b16e62224581c649f205c72b2c75f15f02733c3a0da59f1cacd23e8f2` | 15795 | `experiments/old-evidence-2/report.json` | `EXP\old-evidence-2\report.json`（outcome=NOTREPRODUCED，本机对照通过） |
| `65d8630fa8f58763f1d872448a168dda036e1c3fa854f1a245f5c325a863e344` | 11124 | `experiments/old-evidence-2/port-observations.json` | `EXP\old-evidence-2\` |
| `a7cdb1899944b28b77800a19adae3af4f32148b645a0407619f9a73a77587406` | 50 | `experiments/old-evidence-2/first-failure.sanitized.log` | `EXP\old-evidence-2\`（空首错文件：本次对照未复现失败，如实保留） |
| `fbbbb69384ddbcd5a6ecf971775e90e73eacfc80d2be736cae87eb9f2011fc39` | 1120 | `experiments/old-evidence-2/evidence-manifest.json` | `EXP\old-evidence-2\` |
| `1bb6526f3b95a47bd67561608970e08fb3a5ffe012fecaed81cc84ae45e7afa0` | 1652 | `experiments/old-evidence-attempt1-report.json` | `EXP\old-evidence\report.json`（attempt 1 预检 BLOCKED——工具缺陷，保留为历史，不是实验结果） |

### r03-fix-evidence/（R03-fix 节点门禁证据，9 文件；复制自 Linux 运行侧）

| sha256 | bytes | 相对路径 | 原始位置 |
| --- | ---: | --- | --- |
| `79198f15082666ddd8dd7ae3e16b3c37b6575561c4c9f11dc0b12872ce9d8cf4` | 1411 | `r03-fix-evidence/final-registration.md` | `LNX\evidence\r03-fix-20261114\`（A=8c01798c 重放为 B、A≠B 登记、fresh 代码复审 PASS） |
| `7032ed577e84148d2a41d14cd37befc204111d68c960daa897791e4b1c3bc8e6` | 4868 | `r03-fix-evidence/focused-fix_state_g.log` | 同上（红态保全） |
| `0872e21a663a0b23d825f427195aa1572c2bbebe81a4cfb0f47269303c6edb12` | 2172 | `r03-fix-evidence/focused-fix_state_g2.log` | 同上（恰 1 红 STOP 时点） |
| `9804a0aa1cf485e88ef1ac1f08d3b2612bd50e80bc1559012fa35ad83b2cc1fb` | 190 | `r03-fix-evidence/focused-fix_state_g3.log` | 同上（终态 focused：74 passed / 1 skipped） |
| `3e75f6e29a085238dd9b8b851506361d7b7eced93fa8b63b7f5a9d73b03cb3c5` | 9062 | `r03-fix-evidence/focused-fix_state_r.log` | 同上（红态保全） |
| `b2f2b5548a36a7cff3df98c13c4a45348490aa7e8381d9d0de295830264c9a15` | 9484 | `r03-fix-evidence/focused-fix_state_r2.log` | 同上（红态保全） |
| `4559cb3ace78498be79bb456b96605bf4c47f2a8afc6bf13b2f432b4e61231e4` | 400 | `r03-fix-evidence/secret-scan-manifest.json` | 同上（含 P3-1 字段更正注记） |
| `88c6cbf7673ef410dcf94b173dade5b988e653713085ddf825ed817cf1985f0e` | 372 | `r03-fix-evidence/secret-scan.log` | 同上（新增行 312，8 词+dotted-quad，0 命中，PASS） |
| `1bc7002451cfcd2da527d79cd017cb4ee0e2c373aa7d64609f5323c1b0e7de06` | 57820 | `r03-fix-evidence/verify-full.log` | 同上（state_g 上 `verify_local --full`：41492 passed / 122 skipped，exit 0） |

注：Linux 运行侧另有 `verify-full.pid`（7 字节进程号残留文件），不属证据内容，未入库。

### review-archives/（七项本地复核第 7 条：旧独立复审全文存证）

| sha256 | bytes | 相对路径 | 原始位置 |
| --- | ---: | --- | --- |
| `38fa03c7ed2b0778737d68592256f8828d74744808e7e0c5a637009cad683fcd` | 4357 | `review-archives/README.md` | `PAL-R\review-archives-20261114\`（提取方法/哈希复现/安全扫描记录） |
| `101e8ef4ed469f577f651202db3236d4775f59da0cc66dc47e3ca170c7c69fa7` | 7056 | `review-archives/m0-batch-review-5c3c054d.md` | 同上（M0 批次复审 VERDICT: PASS，1 P2 + 3 P3） |
| `f6f202642b3ce6000e1604315e87cbe8dcbbf6cbc69d03bf5c3a302fcda1efa7` | 8704 | `review-archives/merge-review-716a2ea3.md` | 同上（合并节点 9e761317 复审 VERDICT: PASS，0 P0-P2 + 3 P3） |
| `86093b01f81ea4211ab333898c99c88168b29e3131531fed11ac79083c076e3f` | 1999 | `review-archives/c2-review-696e0ac2.md` | 同上（C2 复审 VERDICT: PASS，范围限定原文保留） |

### q-basis/（Q 底稿与七项复核基础文件）

| sha256 | bytes | 相对路径 | 原始位置 |
| --- | ---: | --- | --- |
| `d536b3ab8d498ebbc0c861c085cc1d3279a91d7820a7fe800585479249dbabe6` | 33151 | `q-basis/q-facts-c3-20261114.md` | `PAL-R\`（Q 事实底稿；其 UNKNOWN/IN-REVIEW 条目按原状态引用，后续事实以本清单及 §77.9 为准） |
| `7f08ecab225e86752e94184a804d162cd9dd1b5ca0d5dac04349252f5f9c4b0b` | 12979 | `q-basis/archive-locators-20261114.md` | `PAL-R\`（七项复核定位：旧复审三份、Windows 历史日志五件、Lordakee 404） |
| `faa18291092686f083e4921817be434ccd35a15fbee110d9f642370444d2a555` | 7392 | `q-basis/mirror-verify-20261114.md` | `PAL-R\`（镜像来源复核：414/415 一致，唯一差异=已记录分页修复） |

## 3. 未入库项与如实说明

1. **`diag2-native-runs` / `release-and-ci` 独立回执 JSON 不存在**：经 Windows
   （PAL-R 全目录、文件名全盘检索）与 Linux 服务器（evidence、codex-tasks、
   r03-next-run 目录）两侧检索，无以该类命名的独立回执文件。ad001c77 的双 run
   （Offline 43590617596 success 41,483/122；Native 43590617533 failure，
   storage version-switch 1 failed/65 passed）与 B 的发布+双 CI 事实分别由
   `decision/codex-decision-brief-20261114.md`（协调者核实输入）与
   `ci-receipts/r09-final-dual-ci-20261114.json`（含 publish/readback 段）承载。
   不构造新回执冒充既有文件。
2. **C3 native 失败明细回执族**（ci-run-43551531643/storage-failure 等 8 件）未逐一
   入库：其结论字段已由 `c3-publication-ci-receipt-99c510e5.json` 的 native_steps
   全表承载；原始明细仍保全于 `PAL-R\c3-main-prep-166886b66268\` 与
   `PAL-R\c3-native-failure-logs\`（库外原件未动）。
3. **distribution 分支 CI artifact**（proof log/xml 与 42MB linux-native-kit zip，
   run 43583373068）体积与性质属 workflow artifact，未入库；库外原件位于
   `PAL-R\dist-artifacts-20261114\`，结论由 §77 distribution 段与
   `plans/distribution-retry-final-plan-20261114.md` 承载。
4. **Windows 历史 full/selfcheck 五件原件**（含失败的 m0_verify.log）仍在
   `D:\Projects\pal-untracked-archive-20261112\`（库外；定位与哈希见
   `q-basis/archive-locators-20261114.md` §2），未复制入库。
5. 本清单自身与 D 交付书（`docs/handoffs/2026-11-13-m0-remediation.md`）不参与
   本清单哈希表（避免自哈希）；其字节由所在提交的 Git 对象固定。

## FX-31 地址脱敏登记（2026-11-20 追加；不改上表既有哈希行）

按 2026-11-19 审计修复清单 FX-31（发现 N-6）：本清单第 60 行与
`decision/m1-final-review-rerun-20261116.log` 第 329 行各含 1 处服务器公网地址原文
（引用根 AGENTS.md Linux-only 段所致），已按字节级单次替换为 `SERVER_HOST_REDACTED`
（占位符沿用 FX-24 server-mirror 约定，表式见 `docs/paper-sim/server-mirror/REDACTIONS.md`
与 `docs/handoffs/m3-cleanup/REDACTIONS.md`）。上表登记的原始 sha256 / 字节数为脱敏前
原件，保持不改；原件字节由 Git 历史对象固定。

| file | original sha256 | redacted sha256 | line |
|---|---|---|---|
| `decision/m1-final-review-rerun-20261116.log` | `e4d7a35de67833563d0fa17036291e0141a3d673138373b775b477930e1aa7` | `2c4e75d7317b90f526e269b47de7b24cee932b7e82aff532f6c22fe8a0ebf83f` | 329 |
| `MANIFEST.md` | edited-in-place-post-redaction (see note) | `7392598ddf1106943d970e6cb79f1981c6a3c04ea187c9c7eb149bc05832f6dd` | 60 |

注（自行自引用，仿 server-mirror `MANIFEST.sha256` 先例）：本清单在记录脱敏后哈希之后
又被本节追加深改，故自行的 original 列置 edited-in-place 标记；其 redacted sha256 覆盖
第 60 行脱敏完成、本节追加之前的字节状态（14013 字节）。脱敏前原件为 14005 字节、
sha256 `8718a20bd5feb68a1b0b562986b6297557102f56b564e43912ce8c3a63c841e5`。文件无法内嵌
自身最终哈希（定点不可达）；最终字节仍由所在提交的 Git 对象固定，与文末既有的
避免自哈希约定一致。守卫测试 `tests/test_tracked_public_address_hygiene.py` 冻结登记后
的全部追踪文件出现计数（当前 42 处，含根 AGENTS.md 保留的 1 处），新增或无故减少任一
处即失败。
