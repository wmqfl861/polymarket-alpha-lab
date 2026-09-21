# 给本地 Agent：PAL_RV05_CLOSURE_20260922

继续原RV05、同一个草稿PR67和同一个绝对截止，不新计七天。承认已经完成的R1—R7、三族回执、11+2和原CI，不从头再做。现在补齐现有计划的完整容量、可分发启动包、单次无人消息衔接；不得只回复“到点请叫我发射”。

## 1. 固定身份与已核对事实

repository = wmqfl861/polymarket-alpha-lab
PR = 67（唯一草稿，不合并）
branch = work/pal-parallel-20260921-ad7a9c77be5a497ab908bb33b031a182
candidate = 15f24d246493cdc114a127c366da537659d8c5d8
tree = d388d113c0a75bb297bf1d60b13a9237fb31a07a
original = d929cc35214996a44008d4bbdd643bc8cc3550ea

上一份回执中的候选长SHA重复了尾部；以这里的40位GitHub值为准，只在新更正记录注明，不改旧证据。

协调者已经核对四工作流success，并下载Windows-soak包核验SHA256/CRC和XML：416passed/1skipped；唯一skip是整个tests.test_rv05_n5_launch_review_rejection模块。两个Windows专属回收用例确已通过。N5文件需要5个环境变量和本地脚本/fixture，缺失时整模块skip；39项本地证据不否定，但不能说干净CI已经跑了39项。原启动脚本不在当前PR清单中。

已完整展开排程：864轮中74轮ok-compute，三族每轮684/682/682，计划50616/50468/50468，共151552，索引域无重叠。协调者没执行这些目标函数；你报告6144行只证明那些行。若已有未上报的完整同版本容量结果，先复用核对，不重跑；否则补原RV05要求的有限完整执行。

## 2. 时间及批准范围

最早顺序启动：2026-09-24T02:14:50Z（台北09-24 10:14:50），且原进程实际退出/清理。
最晚新启动：2026-09-24T12:36:41Z（台北09-24 20:36:41）。
整批截止：2026-09-28T00:36:41Z（台北09-28 08:36:41）。

本提示词确认：原批准的一次修正版72小时，在全部条件满足后可以由经过测试、已武装的有限衔接程序自动启动，不需要用户到点再说“发射”。这不是批准真实模型、系统服务或任意后台任务。

旧soak、watcher、closeout、runtime、候选、campaign、TASK_STATE、STOP和证据保持不动。62420/20372/98500只是历史PID，不能凭数字判断所有权或杀进程。原失败继续是原结果；旧观察不必全绿才能启动修正版，但必须已退出、孩子清理确认、失败证据固定且可解释。不能反写原failed。

允许新副本里的测试支持/启动与审核控制、合成fixture、相关测试和必要文档；允许在现有Windows-soak-contract中补本批测试选择/路径触发/合成fixture配置，不删原门禁、不加超时/权限/依赖。

禁止改生产src、SQL、依赖锁、交易权限、真实客户端启用；禁止真实CLI/API/OAuth/订阅凭据、真实行情/钱包/订单、用户数据库/本机原生DB验收；禁止Sandbox/BIOS/BCD/VBS/防火墙/ACL/执行策略/电源/防休眠、提权、安装、全局工具更改、服务/计划任务、厂商下载绕过。真实范围变更单列WAITING_OWNER，其余任务继续。

三处旧业务根不读写：
C:\Albert\project\polymarket-alpha-lab-acceptance-4d63b08
C:\Albert\project\polymarket-alpha-lab-kit-startup-4d63b08
C:\Albert\acceptance-assets\runner-temp

原准备材料只读指定目录：
%TEMP%\pal-probe-preparation-a0f188d97fe646e684e4d3f2dbe878ca\stage\input
不改其output/.wsb，不重下ZIP。使用已核验独立runtime副本；需要新副本只从已知材料复制校验，不扫描用户目录。

## 3. 下载固定计划、队列与协调者证据

资产提交dec959d4640d1101e1a0584814609836ae752b10。
保留分支handoff/rv05-closure-20260922。
路径docs/handoffs/2026-09-22-rv05-closure/。
manifest.json：674字节，SHA256 09cfe2aef8f7f00cefda266704d0bdb89e3c97717ed059ddda38143c8a5bbc22。
覆盖plan.md、queue.json、coordinator-evidence.json。它们是说明/状态，不是可执行脚本。

已有本接续状态则恢复，不再建重复任务；否则在非管理员PowerShell 5.1 -NoProfile会话运行：

```powershell
$ErrorActionPreference = 'Stop'
$Base = '15f24d246493cdc114a127c366da537659d8c5d8'
$Tree = 'd388d113c0a75bb297bf1d60b13a9237fb31a07a'
$Deadline = [DateTimeOffset]::Parse('2026-09-28T00:36:41Z')
if ([DateTimeOffset]::UtcNow -ge $Deadline) { throw 'STOP: original deadline reached' }
$OrderRoot = Join-Path $env:TEMP ('pal-rv05-close-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $OrderRoot) { throw 'STOP: attempt exists' }
New-Item -ItemType Directory -Path $OrderRoot | Out-Null
$Raw = 'https://raw.githubusercontent.com/wmqfl861/polymarket-alpha-lab/dec959d4640d1101e1a0584814609836ae752b10/docs/handoffs/2026-09-22-rv05-closure/'
$ManifestPath = Join-Path $OrderRoot 'manifest.json'
Invoke-WebRequest -UseBasicParsing -Uri ($Raw+'manifest.json') -OutFile $ManifestPath -TimeoutSec 120
$I = Get-Item -LiteralPath $ManifestPath
if ($I.PSIsContainer -or ($I.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $I.Length -ne 674 -or (Get-FileHash -Algorithm SHA256 -LiteralPath $ManifestPath).Hash.ToLowerInvariant() -cne '09cfe2aef8f7f00cefda266704d0bdb89e3c97717ed059ddda38143c8a5bbc22') { throw 'STOP: manifest integrity' }
$M = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($M.task_id -cne 'PAL_RV05_CLOSURE_20260922' -or $M.base_commit -cne $Base -or $M.base_tree -cne $Tree) { throw 'STOP: task binding' }
$Names = @('plan.md','queue.json','coordinator-evidence.json')
if (@($M.files).Count -ne 3 -or @(Compare-Object ($Names | Sort-Object) (@($M.files.path) | Sort-Object)).Count -ne 0) { throw 'STOP: inventory mismatch' }
foreach ($F in $M.files) {
    $P = Join-Path $OrderRoot $F.path
    if (Test-Path -LiteralPath $P) { throw 'STOP: destination exists' }
    Invoke-WebRequest -UseBasicParsing -Uri ($Raw+$F.path) -OutFile $P -TimeoutSec 120
    $I = Get-Item -LiteralPath $P
    if ($I.PSIsContainer -or ($I.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $I.Length -ne $F.bytes -or (Get-FileHash -Algorithm SHA256 -LiteralPath $P).Hash.ToLowerInvariant() -cne $F.sha256) { throw 'STOP: asset integrity' }
}
Get-Content -LiteralPath (Join-Path $OrderRoot 'plan.md') -Raw -Encoding UTF8
Write-Output 'CLOSURE_PLAN_VERIFIED_NOT_ARMED'
Write-Output $OrderRoot
```

临时静态传输超时按原规则至多两次同URL/同匿名身份重试，首败独立保留；哈希/权限/安全拒绝不绕过。这不授权失败CI再抽一次、未知launch补发或正式长测重开。

## 4. 复用工作区并核对完整身份

读取既有RV05_STATE的精确源码、runtime、launch-final、final-review位置。已有同版本容量或arm证据先核验，避免重复。不要重新索要已知路径，不搜索磁盘，不reset未提交工作，不解冻正在运行的原候选。

把已知非活跃RV05源码仓库赋给$Repo；代码修改只能在独立副本。以下只读核对：

```powershell
if (-not (Get-Variable -Name Repo -ErrorAction SilentlyContinue)) { throw 'KNOWN_REPO_PATH_REQUIRED: read existing RV05 state only' }
$Actual = git --no-optional-locks -C $Repo rev-parse --verify ($Base+'^{commit}')
if ($LASTEXITCODE -ne 0 -or $Actual -cne $Base) { throw 'STOP: fixed source absent' }
$ActualTree = git --no-optional-locks -C $Repo rev-parse ($Actual+'^{tree}')
if ($LASTEXITCODE -ne 0 -or $ActualTree -cne $Tree) { throw 'STOP: tree mismatch' }
$Pr = Invoke-RestMethod -Uri 'https://api.github.com/repos/wmqfl861/polymarket-alpha-lab/pulls/67'
if ($Pr.merged -or -not $Pr.draft) { throw 'RECONCILE: PR no longer active draft' }
if ($Pr.head.sha -cne $Base) { Write-Output 'REMOTE_ADVANCED_COMPARE_AND_CONTINUE_NO_RESET_OR_FORCE' }
Write-Output $Actual
Write-Output $ActualTree
```

需要新源码副本时，从准确已知仓库git clone --no-hardlinks --no-checkout到新目录，再固定checkout；不用共享可写worktree/cache。已建立的合格独立车道可以继续，不为任务号再复制一套。runtime按原清单核验；既有净化bootstrap、-I -S -B、独立TEMP/HOME与明确pytest/tzdata来源不变，不安装新依赖。

所有后续命令由N0记录准确argv、cwd、允许env、源码/运行时/控制包摘要，不输出完整宿主环境。新或旧命令的--help与测试入口必须先核对实际源码，不凭本任务凭空发明一个已存在的命令。

## 5. 六节点持续推进，文件单writer

N0：集成/身份/队列/PR。先登记已完成产物与这次缺口，不要求再次证明全部历史。读AGENTS和DELIVERY_PLAN当前覆盖规则。维护READY/RUNNING/REVIEW/DONE/BLOCKED，每项有owner、文件、SHA、测试与下一任务。只有N0整合/推送PR67，遇远端前进先协调，不force。控制包身份与受测候选分开，不能用提交标题或简写代替hash。

N1：完整有限容量。独占现有容量运行器，不改N2审核。先检查已有结果是否完整覆盖固定74个round与151552槽位；有则复用。没有则在新capacity目录复用现有生成器和审计接口顺序运行这74个计算轮，不插300秒等待，不运行完整soak/其他pytest轮。使用真实round/sub_seed、三族和2048轮总量，不能任选74轮或改seed。保存实际六计数、各族/全局去重、目标/审核调用和字节/耗时证据。索引不重叠不等于实际结果通过，6144样本不能冒充全部。有限容量结果不折抵正式72小时。失败留首败、暂停相关工作、不同SHA修复结果单列；不跑同版失败至绿。

N2：可搬移交付。独占review源码、清单和N5合成fixture/tests；launcher写入归N3。只读复制现有preflight/launch/review/README作为起点，不重写第四套。将路径参数化、真实PID/用户路径留在本地私有绑定文件；GitHub只发源码、合成fixture和模板。让39项拒绝测试在新checkout产生自己的tmp_path布局，环境缺失不再整模块skip；Windows专属案例在Windows真实运行。原Windows专项workflow只补必要选择/路径触发/合成设置，保留旧门禁、超时、权限、依赖。代码先发布固定commit并读回，附大小/SHA256、完整下载和使用命令；不能只提交引用本地缺失文件的测试。

N3：单次自动衔接。独占现有launcher的有限等待/claim路径，不改原主driver。先查已有启动状态：真有ARMED进程则验证并复用，不开第二份；只有文件就NOT_STARTED。复用现有控制，不新建通用队列、额外报告层或模型代理。按下一节完成有限测试、部署和arm，不再要求用户到点喊“发射”。

N4：真实业务边界，不刷测试量。只读对照已有paper/confirmation测试，检查三族没有执行到的partial/empty fill、expired/错源确认、失败研究不能进入合法结算是否已有独立控制。F2固定输入恒为完整成交，不证明未成交路径。已覆盖则记录真实用例ID、直接DONE；缺口才在现有测试中补最小合成反例，不复制旧42组合、不再扫九变异。生产问题超出本批修改范围时保留RED并升级，不越权改src。这条额外覆盖不自动成为新长测门槛。

N5：操作交付/终审。独占现有文档与最终只读审核，不新建报告框架。对照实际脚本帮助和既有合成fixture，整理用户流程中的输入审批、运行、失败/未知、停止、查询、结算/模拟界限；不能声称真实模型/DB已运行。检查DELIVERY_PLAN仍准确，按原WP-02/03/05/06追加本次证据，不改G2—G6状态为DONE。原和新终态后分别复核；两个故障合同第二次是在新长测结束后执行，不是刚启动后。

节点完工转下一READY任务或交叉只读审核。可以用已有获准多Agent能力，少于六个则按逻辑节点轮转；不新建主机/账户/付费客户端，禁fast mode。一个文件一个writer：跨域修正交给owner，N0整合。逻辑Agent并发与合成测试进程并发分开控制。

## 6. 现在可以arm；窗口到来才允许最多一次start

沿用原批准的时间、阈值和独占运行条件，不再等待重复口头授权。自动动作必须由实际持久进程实现，而不是一句“到时继续”。

状态至少区分UNARMED、ARMED_WAITING、PRECHECK、CLAIMED、STARTED、FINAL_REVIEW_READY，以及CANCELLED/EXPIRED/NO_GO/UNKNOWN。只有原进程还活着或窗口未开属于正常等待，不能把身份错误、清理未知、证据损坏等统统每5分钟重试至通过。

绑定：受测SHA/tree、控制包摘要、合同与ERRATA摘要、生成器、manifest、实际config、runtime/依赖、已知原/新campaign路径、唯一claim、启动窗口、截止、固定argv、STOP路径。拒绝任何BIND占位符/未知版本/错误类型。配置中的时限与原获准参数逐项一致；发现未说明的放宽先列差异，不自动接受。

先做有限合成测试：未到时刻、原存活、原失败但已正常退出、子进程未清理、读权限未知、PID复用、半写快照、候选/依赖漂移、双实例抢claim、claim后中断、启动后ack丢失、操作者撤销、时钟跳变和睡眠越过窗口。

等待程序不能只用历史PID判死；无法确认时UNKNOWN，不接管/杀旧进程。启动先持久保存唯一claim，确认丢失也不得补发；退出重启控制程序不能自动清除claim。撤销仅作用于本次衔接器自己的STOP，不触碰原soak的STOP。

满足以下全部条件才start：原驱动明确退出且孩子清理确认；原证据稳定固定（原失败保留）；完整容量、启动控制测试、自审和适用最终门禁完成；身份无漂移；资源健康；位于允许窗口。计划到点不等于原退出。

实际arm后给出：控制包commit/hash、准确命令/cwd、PID+创建标识、ARMED_WAITING首条回执、下一次有界检查时间、独立撤销路径。生成脚本或干跑通过不等于ARMED。没有持久进程能力时如实CAPABILITY_BLOCKED并给待执行准确命令，不伪造后台。

不修改已武装对象。需变更时，先确认仍未CLAIMED、撤销旧arm并留记录，再核验新版本；CLAIMED/STARTED/UNKNOWN不得擅自重arm。实际已启动就保持冻结，不再把新代码写进去。

## 7. 多日工作与结束方式

09-22至09-23：N1全容量、N2可复現交付、N3控制测试/arm，N4/N5低负载并行；原长测保持。
09-24允许窗口：已武装控制重验条件后一次启动新72小时；无需等用户发消息。未满足条件或越窗则NOT_STARTED，不抢跑。
09-24至09-27：固定候选连续观察，各节点只做独立目录的必要审核/说明；不热修改、不加第三次长测、不循环全量凑几天。
结束后至09-28 08:36:41台北：复核新证据、运行后置两个故障合同、冻结后的分离自审、更新同一草稿PR67及最终报告。

原72小时259200秒、864有效passed轮、100000实际完成校验的不同输入分别判定。计划151552/有限预检/正式结果三者分开。新运行非预期failed/unknown/interrupted、身份或证据异常、清理不确定即按原规则安全停、保留首败，不自动重开。睡眠或gap破坏连续性，不拼接不同segment或版本。

默认合计工作集目标2GiB、相关新目录4GiB、证据512MiB、目标卷可用至少10GiB；新增合成测试worker初始1个。读写副本都计资源，不可测标未测。旧任务压力大先暂停旁路，不停原任务腾资源。不改宿主电源策略，不使用服务/计划任务。

每2小时保存状态、每12小时实际进展摘要；现有心跳复用，不新增第三份高频watcher。会话退出前保存owner/分支/未决/接续方法；有限程序能做的观察与总结照常，但它不能自动唤醒语言模型或代替语义审核。用户要求停或安全越界立即按对应边界停止。

全部有用目标完成可提前收尾，不用最大天数为KPI。到原截止不派新任务，在途清理超时单列，不重新计七天。未完成就PARTIAL/NOT_STARTED，不能缩短门槛、刷新样本或要求用户重复已给授权。

## 8. 回执和交付

下一次回执不要只给三进程存活：给N1完整执行计数或精确缺口、N2已发布控制包commit+真实Windows N5案例数、N3是否真的ARMED及撤销方法、N4具体覆盖结论、N5操作文档差异。不能完成的条目写真实原因。

最后分别列原d929、冻结15f24、新测试候选（如确实改变）、控制包四种身份；实际CI、容量、观察、失败/未知/跳过与未执行分开。控制包READY、CI绿标、时间完成不自动等于qualified。

只推代码/测试/模板/脱敏摘要，不上传整个工作区、原回执集合、私有路径、完整环境、厂商程序或凭据。只有N0向现有PR67非强制更新；最终源码适用门禁针对最终SHA，旧通过不能转移，不再rerun旧kit抽奖。没有新实现缺陷不要仅为交付标题解冻被测候选。

最终先冻结再独立步骤只读自审，准确注明同Agent或实际交叉审阅，不能冒充第三方。协调者另行复审后决定合并；本地Agent不合并main。

始终official_cases_run=0、sandbox_started=false、activation_authorized=false；ADMIN-CLI仍WAITING_OWNER但不阻塞允许工作，G2—G6仍未完整验收。

现在读取固定交付、复用既有状态、分配单writer，推进N1/N2/N3；不重做已完成工程，也不把尚未部署的自动衔接称为纯时间门控。
