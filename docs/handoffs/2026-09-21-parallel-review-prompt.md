# 给本地 Agent：PAL_PARALLEL_REVIEW_20260921_V2

你继续原 PAL_LONGTASK_20260920_V1，但追加独立工程工作，不再把“等72小时结束”当作全部工作被时间阻断。先读完本提示词，再执行。此任务不保证语言模型会话可以持续数天；长期自动动作必须有实际启动证据。

## 一、保护正在运行的原任务

用户已报告原候选 d929cc35、tree fb716b2f；正式运行从2026-09-21T02:14:50Z开始，目标结束2026-09-24T02:14:50Z；原总截止2026-09-28T00:36:41Z不变。台北时间分别为09-21 10:14:50、09-24 10:14:50、09-28 08:36:41。
启动时报告PID62420、watcher20372；不能只凭这个PID判断当前身份或杀进程。原campaign是原任务已记录的用户路径下pal-soak-formal-01/campaign，其stop文件不能被本批创建。沿用先前精确路径，不扫描磁盘。

不重启、不延长、不另起第二份72小时，不重跑旧四组基线或原20分钟预演，不修改原候选/运行时/驱动/watcher/manifest/轮记录/TASK_STATE/输出。只按既有inspect路径读取有界状态。普通计数疑点保留，不热修、不补写、不自动打断；真实安全事件与用户停止仍按原安全停止规则处理。
已完成LT-00至LT-03和已做自审作为既有报告保留，不重新分配。aa87f5d3的转录更正只写到新旁路更正文件，不在主任务写入期间修原状态。编译6092文件不能算6092测试；定向分组不叫全量四万项验收。

## 二、权限及独立工作目录

本次允许：在新目录进行源码/驱动/证据审核，合成数据单元/性质/组合测试，受控短寿命Python子进程，测试副本内故障变异，可复现缺陷的最小修复，同一Agent分离自审和原批唯一草稿PR。
禁止：真实模型或官方CLI、真实凭据/OAuth/订阅认证、真实行情/钱包/订单、任何业务数据库与原生数据库测试；禁止init/up/down/backup/restore/migrate、下载受限厂商程序、启用或启动Sandbox、修改BIOS/系统/防火墙/ACL/执行策略/电源或防休眠、服务或计划任务、改变全局Python/uv、安装新依赖、force push或合并main。
现有协调会话可以继续完成这些工程任务，不表示你应关闭自己；但不授权你另行寻找认证、启动官方CLI或额外模型会话。子Agent配置不具备则单Agent继续，禁止fast mode。额外并发只能使用既有获准能力且划清文件归属；合成实验默认只新增一个worker。

不得读写三处旧业务根：
C:\Albert\project\polymarket-alpha-lab-acceptance-4d63b08
C:\Albert\project\polymarket-alpha-lab-kit-startup-4d63b08
C:\Albert\acceptance-assets\runner-temp

准备材料只从此前已知的%TEMP%\pal-probe-preparation-a0f188d97fe646e684e4d3f2dbe878ca读取指定清单/工具/runtime，不重新下载离线ZIP，不改原stage/input、stage/output或.wsb。原主任务新目录也只读。新旁路使用独立源码及运行时副本，不能共享可写文件或缓存。

## 三、下载已发布计划和队列

仓库wmqfl861/polymarket-alpha-lab，主线固定199f5a4f629466467f638d7f4a383e4bead19120／树5202e9a8422d99922eb60725b1384f48299d2257。
计划交付提交863a8bc8468515d24a5099c6c9033edcfe7c75c7，保留分支handoff/wp02-parallel-review-20260921。
这两个文件是计划和参数，不是已实现的调度器，也不包含原本地候选源码。

下面代码仅下载文本、校验和创建新旁路目录，不接触原长测。若本追加任务已经初始化，使用自己的原SIDECAR_STATE恢复，不再次执行本段新建同名任务。

```powershell
$ErrorActionPreference = 'Stop'
$Deadline = [DateTimeOffset]::Parse('2026-09-28T00:36:41Z')
if ([DateTimeOffset]::UtcNow -ge $Deadline) { throw 'STOP: original campaign deadline reached' }
$Delivery = '863a8bc8468515d24a5099c6c9033edcfe7c75c7'
$SideRoot = Join-Path $env:TEMP ('pal-parallel-20260921-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $SideRoot) { throw 'STOP: side directory exists' }
New-Item -ItemType Directory -Path $SideRoot | Out-Null
$Raw = 'https://raw.githubusercontent.com/wmqfl861/polymarket-alpha-lab/' + $Delivery + '/docs/handoffs/'
$Files = @(
  [pscustomobject]@{Name='2026-09-21-parallel-review-plan.md'; Bytes=13228; Hash='5ac1d2cbb0ef8cd175910a6337ec9a7735cdf5180ec8d1ebf7f82c8dffddd907'},
  [pscustomobject]@{Name='2026-09-21-parallel-review-queue.json'; Bytes=2967; Hash='5885cfa7c18234c033cb776c373572f083f4c4e02aad2e8aa62c0f5e45f75cc1'}
)
foreach ($f in $Files) {
  $p = Join-Path $SideRoot $f.Name
  if (Test-Path -LiteralPath $p) { throw 'STOP: download destination exists' }
  Invoke-WebRequest -UseBasicParsing -Uri ($Raw + $f.Name) -OutFile $p -TimeoutSec 120
  $i = Get-Item -LiteralPath $p
  if ($i.PSIsContainer -or ($i.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $i.Length -ne $f.Bytes) { throw 'STOP: file type or size mismatch' }
  if ((Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLowerInvariant() -cne $f.Hash) { throw 'STOP: file hash mismatch' }
}
$Queue = Get-Content -LiteralPath (Join-Path $SideRoot $Files[1].Name) -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Queue.task_id -cne 'PAL_PARALLEL_REVIEW_20260921_V2' -or $Queue.unchanged_campaign_deadline_utc -cne '2026-09-28T00:36:41Z') { throw 'STOP: plan binding' }
Write-Output 'SIDECAR_FILES_VERIFIED_ORIGINAL_SOAK_UNCHANGED'
Write-Output $SideRoot
```

读完整计划和JSON。创建SIDECAR_STATE，记录原任务ID、自己的started_at、同一个绝对deadline、工作目录和支线状态。不得借追加任务把窗口重新计七天。文本下载若超时，保留首次错误后，同URL/同匿名身份最多两次传输重试；各次留痕，最终完整哈希必须匹配。哈希错误、安全/权限拒绝不绕过，不改变身份或路线。此许可不适用于失败测试或模型调用重试。

## 四、PX-00：完整身份、源码可见性、独立副本

协调者在GitHub没有读取到d929cc35，因此先从本地原任务已记录的Git仓库和冻结清单解析完整40位提交与树。不要把短号补成猜测的完整SHA。
将你原会话已经知道的Git源码路径赋给$OriginalRepo；不是向用户重复索要路径，也不能搜索HOME。若上下文遗失，只记录该路径未解析并保留原进程，不能编造路径。以下命令不切换原仓库：

```powershell
if (-not (Get-Variable -Name OriginalRepo -ErrorAction SilentlyContinue)) { throw 'LOCAL_PATH_UNRESOLVED: use the recorded original repository path only' }
$Candidate = git --no-optional-locks -C $OriginalRepo rev-parse --verify 'd929cc35^{commit}'
if ($LASTEXITCODE -ne 0 -or $Candidate -cnotmatch '^d929cc35[0-9a-f]{32}$') { throw 'STOP: candidate identity unavailable' }
$CandidateTree = git --no-optional-locks -C $OriginalRepo rev-parse ($Candidate + '^{tree}')
if ($LASTEXITCODE -ne 0 -or $CandidateTree -cnotmatch '^fb716b2f[0-9a-f]{32}$') { throw 'STOP: candidate tree mismatch' }
$ReviewSource = Join-Path $SideRoot 'source'
if (Test-Path -LiteralPath $ReviewSource) { throw 'STOP: review source exists' }
git -c core.autocrlf=false -c core.eol=lf clone --no-hardlinks --no-checkout -- $OriginalRepo $ReviewSource
if ($LASTEXITCODE -ne 0) { throw 'STOP: independent local clone failed' }
git -C $ReviewSource -c core.autocrlf=false -c core.eol=lf checkout --detach $Candidate
if ($LASTEXITCODE -ne 0) { throw 'STOP: independent checkout failed' }
$CopiedTree = git -C $ReviewSource rev-parse 'HEAD^{tree}'
if ($LASTEXITCODE -ne 0 -or $CopiedTree -cne $CandidateTree) { throw 'STOP: copied tree mismatch' }
git -C $ReviewSource remote rename origin frozen-source
if ($LASTEXITCODE -ne 0) { throw 'STOP: local source remote labeling failed' }
git -C $ReviewSource remote add origin https://github.com/wmqfl861/polymarket-alpha-lab.git
if ($LASTEXITCODE -ne 0) { throw 'STOP: GitHub destination setup failed' }
$WorkBranch = 'work/pal-parallel-20260921-' + [guid]::NewGuid().ToString('N')
git -C $ReviewSource switch -c $WorkBranch
if ($LASTEXITCODE -ne 0) { throw 'STOP: new review branch failed' }
Write-Output $Candidate
Write-Output $CandidateTree
Write-Output 'INDEPENDENT_SOURCE_READY_NOT_A_SECOND_SOAK'
```

读新副本AGENTS.md及DELIVERY_PLAN当前覆盖规则。将已校验的原离线Python复制到SideRoot/runtime，按原包清单核对复制文件，禁止共享可写runtime/cache；不安装依赖。新测试使用原来已验证的净化启动方式：只传复制运行时、独立source、自己的TEMP/HOME/证据目录；禁止继承PG/DSN/真实凭据、PYTEST_ADDOPTS或用户插件。确认pytest和项目模块分别来自复制runtime和新source。不要再跑原1126项基线；只执行本次映射出的新增或受影响用例。

在第一次新修改前，通过既有获准GitHub认证把完整冻结候选和必要父提交发布到本批分支。可以从新clone推送，例如git -C $ReviewSource push origin $WorkBranch；先确认其HEAD就是$Candidate，检查新增历史只含允许的源码测试，无运行证据/凭据。不要打印或复制认证材料、禁用钩子或绕过拒绝。
现在就创建或复用原批唯一草稿PR，标记原soak IN_PROGRESS，不必等72小时才能让协调者看到源码。若原批已有PR，继续用那一个；保持原冻结SHA为明确祖先，后续旁路修复另列SHA。发布受阻只标PUBLISH_BLOCKED，其余本地工作继续。不合并、不force push、不动历史PR。

## 五、PX-01至PX-04：在等待窗口内实际开发

PX-01 优先核验长测证据，不新造报告框架：
- 阅读真实soak_driver/inspect/生成器和29项自测，映射输出字段，不能猜schema。
- 复用已有汇总；缺少才加tests/support内的小型纯函数/离线核验命令。
- 分别判定源/依赖身份、轮次闭合、不同输入、有效控制、真实历时/gap、XML清单、资源和清理。
- 864×110=95040测试调用，不等于100000不同输入；一个用例可生成多条输入，所以先核对实现，不直接判失败。不同输入不能靠添加轮号或无关随机标签凑数。
- 第一轮若立即开始，第864轮开始只过863个五分钟间隔，即71h55m。不能只凭轮数认定72小时；也不能凭这一算式直接断定驱动错误。检查实际结束逻辑与时间证据。
- 对副本构造缺轮/重复轮/多终态/丢尾行/混合版本/错hash/时钟回拨/gap/少XML用例/无关nonce/失败吞掉/未测资源写零等反例，并保留合法控制。
- 只能重建生成规则但不能证明实际执行过时，报告下界或UNKNOWN。活跃文件半条快照写SNAPSHOT_INCOMPLETE，不改原文件、不判作通过。

PX-02 检查测试能否真正发现错误：
- 每个不变量映射入口、拒绝反例、正常控制和对应测试，覆盖缺口优先。
- 仅在独立一次性副本或mock中故意引入少量语义错误：错误终态、漏hash比较、缓存错算、None变零、Decimal舍入错、提前完成、漏stop检查。
- 所有输入/客户端虚构；不执行降低安全约束的真实CLI，不修改主候选。
- 未变异控制先通过，变异各执行一次受影响测试。KILLED/SURVIVED/INVALID/EQUIVALENT/NOT_RUN分开，语法/安装错误不算语义变异被测试发现。
- 幸存变异不必然是生产bug；确认等价性或补必要测试。无真实缺陷不制造修复，不安装新变异平台。

PX-03 在副本做确定性故障时序：
- 检查状态保存前后中断、更新未确认、磁盘/权限异常、半条checkpoint、控制器与孩子不同退出、重复启动、PID复用、stop与checkpoint同时到达。
- 用事件/barrier/mock明确控制触发点，而不是靠随机sleep猜覆盖。
- oracle用独立小状态模型，不能调用被测函数算预期答案。
- 只管理自己新建且持有所有权的短寿命合成子进程；不对62420/20372或其他用户进程做故障注入、扫描或清理。
- 真数据库才能证明的性质列NOT_RUN，不连接用户DB。

PX-04 补接口组合，而非又一轮相同单元测试：
- 从现有覆盖中找遗漏：BTC/ETH、两客户端合成协议、成功/失败/未知、停止时序、原样重放/改输入冲突、原审计模拟器、合成结算与成本。
- 来源/时间/原始hash不匹配拒绝；未知不当NO、零费用或零收益；研究失败不产出合法已结算收益。
- 用独立Decimal表达式检查费用/给付/舍入，写明适用条件。
- 留下尝试→结构回复→有效研究→可模拟→可核验结算各阶段分母，不能丢弃失败项。
- mock事务不是原生PostgreSQL事务证明，不宣称真实策略有效。

四支线可交替推进，普通反例只挂起受影响部分。没有缺陷就交付测试有效性和覆盖证据，不为凑代码行数重写生产模块。

## 六、保护主任务性能与证据

初始只新增一个合成worker，新增工作集目标512MiB；主批加旁路仍遵守原2GiB工作集目标、4GiB新目录、512MiB证据预算。剩余盘小于10GiB或主任务告警/心跳异常，先暂停旁路，不停止主soak腾资源。无法测量写未测，不宣称硬限额。
不重复原每分钟watcher，不高频遍历原日志。旁路每2小时记录检查点、每12小时摘要。源码审查可穿插运行，但不对活跃目录做全盘扫描。所有派生报告只写SideRoot；原文件是否存在缺口以稳定最终证据为准。
全局安全异常或用户停止按原规则处理，除此之外不写原stop文件。暂停/崩溃恢复只恢复旁路自己的实例，原运行一律不自动start/resume。

## 七、收尾不能只靠“下次唤醒我”

检查既有watcher究竟只看进度，还是确有有限终态收尾动作。没有实现的能力不要声称存在，不替换或热改它。
在已有纯证据校验函数通过测试后，允许一个旁路终态收尾程序等待原任务明确结束：只读有界状态，写SideRoot里的最终摘要和FINAL_REVIEW_READY；处理早退、deadline、半写、未知与失败，不能自动重启主任务。
它必须先通过合成终态测试，受同一个绝对截止约束；不调用模型、不改源代码、不自动推送PR、不创建服务/计划任务，不等于自动唤醒语言模型。实际启动后返回自己的命令/PID/启动标识/停止路径与首条回执；没启动就写NOT_STARTED，不伪称无人看管可完成语义审查。
有持久执行能力就继续适用工作；会话即将结束先保存SIDECAR_STATE、分支、原/旁路完整身份、未决问题、接续命令。只在原总截止或实际安全/资源/权限边界停止，不因一个小提交完成就退出。全部有用工作已完成可提前结束，不用空轮询凑数天。

## 八、最终审核与交付

原目标2026-09-24T02:14:50Z仅是计划时刻，不是通过证据。以实际稳定终态、源身份、历时、gap、轮次和不同输入分别得出结论。原候选失败/不足则保留，旁路新修复不能借用它的观察时长；不自动另开72小时。
对最终固定旁路源码做单独只读自审，准确写同一Agent分离自审，不冒充第三方。源码/测试改动需要原适用CI针对最终SHA；不删门禁、不改超时/依赖/SQL/工作流来凑通过。CI未跑保持draft。
原批只保留一个草稿PR，完整区分原候选观察与旁路修复；不合并main。上传仅限代码、测试、必要文档和经审阅脱敏摘要，不能上传整目录/原始日志集合/环境/厂商程序/真实敏感信息。协调者取得完整源码和证据后再做最终复审与合并决定。

每次摘要和最终返回至少包含：
ORIGINAL_SOAK状态及完整SHA/tree；SIDECAR各PX状态及自己的SHA/tree；实际执行/通过/失败/跳过/未知；原始输入不同数与测试调用数分开；真实连续时间和gap；变异分类；最小RED→修复→GREEN；资源峰值与未测；原失败和转录更正；原批唯一草稿PR；最终化程序是否真的启动；剩余唯一关键动作。

始终official_cases_run=0、sandbox_started=false、activation_authorized=false。ADMIN-CLI仍WAITING_OWNER但不阻断离线主线，G2—G6不因此完成。
现在开始PX-00并按依赖推进，不重复上一批初始化，不打断原72小时，也不要只回复一份新计划。
