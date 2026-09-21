# 给本地 Agent：PAL_CORRECTIVE_LONGTASK_20260921_V3

继续同一批长任务与草稿 PR #67。本轮不是“只等结束”：协调者已在f2aff305固定源码上复现7个问题，并准备了有限反例、结果文件和一个Windows专项CI补丁。先修复，再在原运行退出后启动一次新的修正版72小时观察。不要只是重复上一轮摘要或再写一份计划。

## 一、固定事实、权限和时限

仓库：wmqfl861/polymarket-alpha-lab。
PR #67仍为唯一草稿，远端分支：
work/pal-parallel-20260921-ad7a9c77be5a497ab908bb33b031a182

本次修复基线：f2aff305cd30879ff4b15c6a7bbafd82856aaf75
基线树：7cb160f73c96380dc69f9474098e69ae64d83f8a
原长测候选：d929cc35214996a44008d4bbdd643bc8cc3550ea
原树：fb716b2fed62455f242ab643e110d493774ff2b3

原总截止不变：2026-09-28T00:36:41Z（台北09-28 08:36:41）。
原长测计划结束：2026-09-24T02:14:50Z（台北09-24 10:14:50），不等于已经通过。
修正版新72小时最晚启动：2026-09-24T12:36:41Z（台北09-24 20:36:41），预留最后12小时审核。

新增权限仅有两项：
1. 应用本交付的一个只读、无数据库Windows合成测试workflow；不改变原workflow、分支保护、权限或时限。
2. 原正式进程明确退出、自有子进程清理已确认、修复候选前置验收通过后，在新目录启动一次修正版72小时。不与原正式运行并发；不自动失败重开；不重置原历史。
这两项分别取代上一提示词对应的局部禁止，其他保护不变。

允许新副本中的测试工具修复、合成输入/子进程、定向测试、分离自审和更新同一草稿PR。不改生产src、SQL、依赖锁、真实客户端启用与交易权限，不新增通用框架或第三层报告。
禁止真实凭据/OAuth/订阅、官方Claude/Codex等程序、真实行情/模型/钱包/订单、任何业务数据库或原生数据库操作；不启用/启动Sandbox，不改BIOS/BCD/VBS/防火墙/ACL/执行策略/电源，不提权、不安装新软件、不绕过厂商下载限制。全局Python/uv不动，禁止fast mode。
现有协调会话可继续工作，但不获准另找认证或启动新官方CLI。额外获准子Agent不可用时单Agent继续，不整批退出。

以下旧业务根不读写：
C:\Albert\project\polymarket-alpha-lab-acceptance-4d63b08
C:\Albert\project\polymarket-alpha-lab-kit-startup-4d63b08
C:\Albert\acceptance-assets\runner-temp

原62420/20372/98500是历史PID，不能仅凭数字判断身份或杀进程。原campaign、源码/runtime、watcher/closeout、manifest、TASK_STATE及原输出保持不动。只读已有准确路径的有限状态，不扫描主机目录。普通诊断问题不打断原长测。

## 二、下载完整交付，不运行下载文件

固定交付提交：64712264cbee27604556b1eb0c01e901b4a33b4e
保留分支：handoff/wp02-corrective-longtask-20260921
目录：docs/handoffs/2026-09-21-corrective-longtask/
manifest.json：1023字节，SHA256：
7530275983156625a6bc0c5e6529613e549da42670febb7d85490d639b9cf986

下段只建一个新的短路径目录并下载文本文件。不重新下载原离线包，不重复系统查询和旧基线。若V3已开始，读取自己的V3_STATE接续，不再新建一次任务。

```powershell
$ErrorActionPreference = 'Stop'
$Base = 'f2aff305cd30879ff4b15c6a7bbafd82856aaf75'
$Tree = '7cb160f73c96380dc69f9474098e69ae64d83f8a'
$Deadline = [DateTimeOffset]::Parse('2026-09-28T00:36:41Z')
if ([DateTimeOffset]::UtcNow -ge $Deadline) { throw 'STOP: original deadline reached' }
$V3Root = Join-Path $env:USERPROFILE ('pal-fix67-' + [guid]::NewGuid().ToString('N').Substring(0,8))
if (Test-Path -LiteralPath $V3Root) { throw 'STOP: attempt exists' }
New-Item -ItemType Directory -Path $V3Root | Out-Null
$Assets = Join-Path $V3Root 'assets'
New-Item -ItemType Directory -Path $Assets | Out-Null
$Delivery = '64712264cbee27604556b1eb0c01e901b4a33b4e'
$Raw = 'https://raw.githubusercontent.com/wmqfl861/polymarket-alpha-lab/' + $Delivery + '/docs/handoffs/2026-09-21-corrective-longtask/'
$ManifestPath = Join-Path $Assets 'manifest.json'
Invoke-WebRequest -UseBasicParsing -Uri ($Raw+'manifest.json') -OutFile $ManifestPath -TimeoutSec 120
if ((Get-Item -LiteralPath $ManifestPath).Length -ne 1023 -or (Get-FileHash -Algorithm SHA256 -LiteralPath $ManifestPath).Hash.ToLowerInvariant() -cne '7530275983156625a6bc0c5e6529613e549da42670febb7d85490d639b9cf986') { throw 'STOP: manifest integrity' }
$M = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($M.base_commit -cne $Base -or $M.base_tree -cne $Tree) { throw 'STOP: wrong base' }
$Names = @('plan.md','queue.json','pr67_contract_repro.py','coordinator-findings.json','pr67-windows-soak-contract.patch')
if (@($M.files).Count -ne 5 -or @(Compare-Object ($Names | Sort-Object) (@($M.files.path) | Sort-Object)).Count -ne 0) { throw 'STOP: wrong inventory' }
foreach ($F in $M.files) {
    $P = Join-Path $Assets $F.path
    if (Test-Path -LiteralPath $P) { throw 'STOP: existing download' }
    Invoke-WebRequest -UseBasicParsing -Uri ($Raw+$F.path) -OutFile $P -TimeoutSec 120
    $I = Get-Item -LiteralPath $P
    if ($I.PSIsContainer -or ($I.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $I.Length -ne $F.bytes -or (Get-FileHash -Algorithm SHA256 -LiteralPath $P).Hash.ToLowerInvariant() -cne $F.sha256) { throw 'STOP: asset integrity' }
}
Write-Output 'V3_FILES_VERIFIED_NOT_EXECUTED'
Write-Output $V3Root
```

读完plan.md、queue.json、反例程序和补丁。JSON是任务配置，不是已经存在的调度器。静态下载若临时超时，最多两次同URL/同匿名身份重试，分别留首败与传输元数据，最终完整哈希必须匹配；安全/权限拒绝、哈希不符不得换路线。此许可不适用于失败测试、业务调用或长测重开。

## 三、新源码副本、运行时和历史反例

把V2原状态中已经记录的当前源码仓库路径赋给$ExistingRepo；它应包含f2aff305。不要向用户重新索要已经记录的路径，也不要搜索HOME。只在新副本操作Git，原运行目录不checkout/reset/rebase。

```powershell
if (-not (Get-Variable -Name ExistingRepo -ErrorAction SilentlyContinue)) { throw 'LOCAL_PATH_UNRESOLVED: read the existing V2 state only' }
$Actual = git --no-optional-locks -C $ExistingRepo rev-parse --verify ($Base+'^{commit}')
if ($LASTEXITCODE -ne 0 -or $Actual -cne $Base) { throw 'STOP: fixed candidate unavailable' }
$Src = Join-Path $V3Root 'src'
git -c core.autocrlf=false -c core.eol=lf clone --no-hardlinks --no-checkout -- $ExistingRepo $Src
if ($LASTEXITCODE -ne 0) { throw 'STOP: separate clone failed' }
git -C $Src -c core.autocrlf=false -c core.eol=lf checkout --detach $Base
if ($LASTEXITCODE -ne 0) { throw 'STOP: checkout failed' }
$CopiedTree = git -C $Src rev-parse 'HEAD^{tree}'
if ($LASTEXITCODE -ne 0 -or $CopiedTree -cne $Tree) { throw 'STOP: tree mismatch' }
git -C $Src remote rename origin historical-local
if ($LASTEXITCODE -ne 0) { throw 'STOP: remote setup' }
git -C $Src remote add origin https://github.com/wmqfl861/polymarket-alpha-lab.git
if ($LASTEXITCODE -ne 0) { throw 'STOP: remote setup' }
$Branch = 'work/pal-corrective-v3-' + [guid]::NewGuid().ToString('N')
git -C $Src switch -c $Branch
if ($LASTEXITCODE -ne 0) { throw 'STOP: local branch' }
$Evidence = Join-Path $V3Root 'evidence'
$Scratch = Join-Path $V3Root 'scratch'
New-Item -ItemType Directory -Path $Evidence,$Scratch | Out-Null
```

使用已知准备目录：
%TEMP%\pal-probe-preparation-a0f188d97fe646e684e4d3f2dbe878ca\stage\input

只读其中固定清单与python目录，按原已核验清单复制python到$V3Root\runtime并逐文件比较大小/SHA256。可用原固定stdlib verifier校验，不重新跑准备验收、uv、pip或任何安装器。禁止共享可写缓存或把原runtime当新测试输出路径。原输入不完整/不符则只挂起执行，不改原输入凑通过。
$Py设为新复制的$V3Root\runtime\python.exe，$Site设为其Lib\site-packages。用-I -S -B、自己的TEMP/HOME、禁用插件自动加载及用户site；新pytest启动复用已经验证的净化bootstrap，明确加入新$Src和$Site，并检查实际导入来源。不得把全套宿主环境或PG/DSN/真实密钥传给孩子。

先在精确基线和新scratch跑一次历史反例。它只接受钉住代码，只用小型合成目录，不接触真实campaign；程序退出3是预期RED，不能改程序让旧版返回0：

```powershell
$Utf8 = New-Object System.Text.UTF8Encoding($false)
$Repro = Join-Path $Assets 'pr67_contract_repro.py'
$Lines = @(& $Py -I -S -B $Repro --source $Src --scratch $Scratch)
$ReproExit = $LASTEXITCODE
[IO.File]::WriteAllText((Join-Path $Evidence 'baseline-repro.json'), ($Lines -join "`n"), $Utf8)
$R = ($Lines -join "`n") | ConvertFrom-Json
if ($ReproExit -ne 3 -or $R.reproduced -ne 7 -or $R.controls_passed -ne 5) { throw 'REPRO_DIFFERENT: preserve and triage, do not edit the historical probe' }
```

退出5是setup/internal问题，不是缺陷已修复；原平台行为不一致时记录差异再定向分析。后续把反例迁入现有pytest，由新代码满足正确断言，而不是反复跑旧pin的反例程序。

## 四、先补实际缺失的Windows门禁

在任何源码修复前，对精确f2aff副本应用提供的唯一workflow补丁；它不触碰现有workflow或生产代码：

```powershell
$PR = Invoke-RestMethod -Uri 'https://api.github.com/repos/wmqfl861/polymarket-alpha-lab/pulls/67'
if ($PR.merged -or -not $PR.draft -or $PR.head.sha -cne $Base) { throw 'REMOTE_CHANGED: reconcile without overwrite or force' }
$Patch = Join-Path $Assets 'pr67-windows-soak-contract.patch'
git -C $Src apply --check --index $Patch
if ($LASTEXITCODE -ne 0) { throw 'STOP: patch preflight' }
git -C $Src apply --index $Patch
if ($LASTEXITCODE -ne 0) { throw 'STOP: patch application' }
$Patched = git -C $Src write-tree
if ($LASTEXITCODE -ne 0 -or $Patched -cne '5672dc17c19aecdeb610374a72b598817aaef863') { throw 'STOP: unexpected patch tree' }
git -C $Src diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'STOP: patch whitespace' }
```

核对只新增.github/workflows/windows-soak-contract.yml再聚焦提交。使用既有获准GitHub通道向PR67当前分支非强制推送；推送前重读远端，变化则先协调，不覆盖。保持一个草稿，不新开竞争PR、不合并main。身份/权限/钩子拒绝不绕过，标CI_PUBLICATION_BLOCKED，其余本地修复继续。
工作流已做静态/补丁校验及8个合成XML控制，但协调者没有执行新Windows job。必须下载最终job的实际JUnit，确认以下两项恰好执行一次且非跳过/失败：
- tests/test_soak_driver.py::test_abnormal_exit_recovers_into_a_new_segment_with_new_round_numbers
- tests/test_soak_driver_recovery.py::test_controller_killed_mid_child_recovers_per_oracle

Linux的对应skip保留；不通过删除守卫假称跨平台保证。新增的回归文件如不在job选择中，只在这个新增job中补齐列表。旧三条绿标不替代新门禁；旧门禁仍按最终改动实际运行，不每个小修改都浪费重复全量。

## 五、当前就做的修复工作

RV-01：在现有soak_audit.py/soak_closeout.py及必要测试中处理R1—R6。
- distinct-input声明不能直接PASS；有效轮不包括实际failed/unknown/interrupted。
- 观察完成、证据完整、稳定性通过是三个不同结果。原tzdata失败保留，不能宣布原观察稳定性全绿。
- 不从259200秒要求扣259.2秒容差；回执/起止/轮数只绑定同一个闭合segment。
- checkpoint、.tmp、失败/停止输出都受新side-root路径保护；先校验后写入。
- 文件先限量读取，目录/行数/行长/轮号范围有界；半写/超限/未知不能被当PASS。
- READY标记不等于通过，也不能在原进程仍活着或数据未稳定时称已最终稳定。

RV-02：修复实际发现的重复恢复unknown丢失，并验证孩子真正使用固定依赖。
- 第一次unknown=1，再次/第三次恢复仍正确重计1，旧旁记录字节保持、轮号不复用。
- 缺失/损坏/冲突旁记录用独立oracle反例；不自动重跑原未知任务。
- 验证真正合成子进程的sys.executable、pytest、tzdata路径/版本/文件摘要，不只验父进程。
- 所有影子包/缺失依赖/插件对照只建在新测试目录，不访问真实用户site或认证。

RV-03：获得新增Windows job真实证据，保持原门禁与原失败。
RV-04：完成新代码定向回归、20分钟新候选预演和单独只读自审，冻结新SHA/tree、运行时、manifest和生成器。

协调者的7反例与5控制是已执行的有限结果，不是完成你本机的修复。原32项测试全过同时存在这些缺口，说明需要加正确边界覆盖，不能删除原测试或只写注释消除问题。优先复用现有代码，不新增客户端、巨大框架或第三层报表。已完成的九变异、42组合、旧系统查询/准备/基线不重做。

R5只需从原closeout已记录的启动receipt一次只读核对checkpoint目标；若原配置安全，PID98500保持不动。只有确证它正向原campaign写入，才按已知独立sidecar STOP机制停该收尾进程并报告；不要误写主campaign的stop。不能确认身份则暂停新动作并标UNKNOWN，禁止全局按PID/名称kill。

## 六、原运行结束后的一次修正版72小时

原计划到点不是退出证据。必须同时满足：原驱动已明确退出、自有孩子已清理、原证据固定、旧closeout结果作为历史保留；新候选修复/预演/分离自审及最终适用门禁通过；资源充足；时间未晚于2026-09-24T12:36:41Z。

满足时允许在全新短路径目录，以冻结新SHA与新配置启动一次修正版72小时，不借旧时长。使用主种子2026092103，批次300秒、心跳60秒、检查点2小时、摘要12小时、gap最大900秒，至少864有效通过轮和100000实际完成校验的不同逻辑输入。独立输入不能靠轮号/目录/无关nonce增加；启动前先验证容量可达，最终只统计实际执行证据。

新增运行初始一个合成worker；合计资源沿用2GiB工作集目标、4GiB工作目录、512MiB证据、卷余量10GiB。不可测就未测，不声称硬限制；原运行压力大时先暂停新增短测。不要更改宿主电源或睡眠策略。

新运行出现非预期失败、unknown/interrupted、证据或身份错误、清理不确定，安全停止新运行并留首败，不自动再开第二次修正版。有意负向场景断言通过是passed，不误作失败。睡眠/重启/gap中断连续性，不拼接。源或受测驱动改变不能沿用已过时间。

若要在无人新消息时顺序衔接，可实现一个有限纯Python交接控制，复用既有驱动：只读原终态，核对身份/退出清理/新冻结摘要/启动截止和资源；启动前记录唯一claim，只启动一次固定命令。先用正常结束、原失败结束、仍活着、PID复用、半写、重复触发、自身重启、过截止等合成情况测试。启动确认丢失不自动补发。
它不调用模型、不创建服务或计划任务、不自动改代码或推送PR，不等于自动唤醒语言模型。实际部署才报告PID/启动标识/命令/STOP路径和首条回执；没部署就NOT_STARTED，并留下准确待执行命令，不承诺不存在的后台能力。

原运行到启动截止仍未退出、前置不满足或剩余时间不足，保留新72小时NOT_STARTED，交付代码与已做短测。不得抢先并发、缩短门槛、延长截止或反复重开凑通过。

## 七、检查点、最终审核和交付

每2小时更新V3_STATE，每12小时一个实际进展摘要。接近上下文边界写可接续状态；不要另起同名任务丢失原失败。已做的小提交或CI成功不是结束理由；其他独立工作可做就继续，真正权限/资源/时间阻断则准确记录。

最后分别给出原候选、f2aff旧sidecar、修正版的完整SHA/tree和适用证据。旧观察有失败即qualification不通过，即使完成时长、证据完整。修正版未完成/失败/未知如实保留，不把READY或exit0当产品验收。
修复后的新文件进入同一草稿PR67，记录7项RED→GREEN、控制组、Windows两专属用例、子进程依赖身份、新运行实际历时/有效轮/逻辑输入/资源/清理、原失败和未执行项。最终对固定代码与原始证据做分离自审，准确标同一Agent自审。协调者再复核并决定是否合并；你不合并main。

只上传源码/测试/必要文档及审阅后的脱敏证据摘要，不能上传整工作区、厂商程序、环境、认证或原私密日志。原记录和首败不删除、不覆盖、不反写。
总截止2026-09-28T00:36:41Z到达后不发新任务；在途清理按原边界保全，超时部分单列。
始终official_cases_run=0、sandbox_started=false、activation_authorized=false。ADMIN-CLI保持WAITING_OWNER，G2—G6不因测试数量或长测天数而完成。

现在读取交付、建立独立副本、复现并修复；不要在七项实质问题还未处理时报告“无可先行工作”。
