# 给本地 Agent：PAL_RV05_CAPACITY_20260921

这是既有 V3 / PR #67 的裁决与多节点开发接续，不重新计七天。先执行已批准的源码工作，不再等待相同授权，不以“只剩时间门控”为由跳过容量和证据改造。长期运行必须由实际可用的持久进程完成，不承诺模型会话能自动醒来。

## 1. 三项裁决已经明确

1. **批准最小候选变更**：ok-compute生成器、逐子输入回执、现有驱动接线和独立审计去重可以在新候选中修改；重新测试、预演、自审、CI和冻结。原候选、原回执和正在运行的证据不改、不补写。
2. **批准11个名义稳定性场景，同时强制保留2类故障合同测试**：提交原13→11+2的名称/代码摘要/预期语义映射。两个设计性失败从新名义长测移出，但保留源码、原失败和测试覆盖，预演前与长测后分别验证预期错误、资源边界和清理。不把原failed改passed，不把新11场景结果叫作旧13场景全绿。
3. **kit不按历史失败率豁免**。协调者已经完成本次artifact检查并发起唯一一次诊断复跑，不由你再次触发。该次复跑现在为521passed/0failed/0skipped，源码保全通过；原attempt1的520passed/1failed永久保留，根因未证实。未来新候选仍需自己的适用CI，旧候选绿标不能转移。

固定当前基线：
repository = wmqfl861/polymarket-alpha-lab
PR = 67（唯一草稿，不合并）
branch = work/pal-parallel-20260921-ad7a9c77be5a497ab908bb33b031a182
source = 2584f6f2d86ce19e7e2dab6bea6a27a013587753
tree = 702d25b5aaabbd790bd48a0ebed8d1a79f12c24d
original_soak = d929cc35214996a44008d4bbdd643bc8cc3550ea

kit原run35596758777，原job106323241565，诊断attempt2 job106355705018。
裁决和实际复跑证据在PR评论5761394011、5761566327。
原失败实际发生在研究/模拟/结算完成后的run_packaged_session_drain，内层错误已在log/XML中截断；不能认定本次就是initdb访问冲突或recipe超时。不要再扫93次历史或循环重试。

## 2. 时间和安全边界

原总截止：2026-09-28T00:36:41Z（台北09-28 08:36:41）。
原长测计划结束：2026-09-24T02:14:50Z（台北09-24 10:14:50），不是实际退出证明。
唯一修正版72小时最晚启动：2026-09-24T12:36:41Z（台北09-24 20:36:41），保留至少12小时最终审核。

现有原soak、watcher、closeout、runtime、冻结源、TASK_STATE和证据不动。62420/20372/98500只是历史PID，不足以识别或终止进程。普通发现不打断原长测，不写原STOP、不热修、不重启、不延长、不并行开第二份正式长测。已有确证危险写入的安全停止规则仍有效。

本批允许新副本内test/support工具、生成器、测试、发射/终审接线和必要文档改动；生产src、SQL、依赖锁、公开错误合同、交易权限不改。允许对原获准Windows专项workflow补齐本批测试选择，但不删原门禁、不升时限、不改权限。任何真实生产修复需要另列给协调者，不能静默扩权。

不运行官方CLI，不找密钥/OAuth/订阅登录，不调用真实模型/市场/钱包/订单，不接用户数据库，不运行本机原生DB验收，不启用或启动Sandbox，不改BIOS/BCD/VBS/防火墙/ACL/执行策略/电源或防休眠，不提权、不安装、不改全局Python/uv、不绕过厂商下载限制。原有隔离GitHub CI可运行其自身合成数据库测试，不等于可以连接用户业务库。

禁止读写旧业务根：
C:\Albert\project\polymarket-alpha-lab-acceptance-4d63b08
C:\Albert\project\polymarket-alpha-lab-kit-startup-4d63b08
C:\Albert\acceptance-assets\runner-temp

已知准备材料只读：
%TEMP%\pal-probe-preparation-a0f188d97fe646e684e4d3f2dbe878ca\stage\input
不改其input/output/.wsb、不重下离线包；使用已经校验过的独立runtime副本，缺失时只从上述已知材料复制并按清单核验，不扫描HOME找替代品。

## 3. 下载计划和容量工具；阅读后才运行

固定交付提交58f40e7b0156dd316284ef4d5f6503c728a0ce2f；保留分支handoff/wp02-rv05-capacity-20260921。
目录docs/handoffs/2026-09-21-rv05-capacity/。
manifest.json：939字节；SHA256 e81af886c2ef681fde8eb8bd0ee22cbff3a115a3316895cc4f143ad9f2d9fdd5。
它覆盖plan.md、queue.json、capacity_plan.py、test_capacity_plan.py、coordinator-evidence.json。

若本接续已有RV05_STATE则恢复，不重复新建任务。以下仅下载到新目录，不触碰原运行，Markdown不当脚本执行。

```powershell
$ErrorActionPreference = 'Stop'
$Base = '2584f6f2d86ce19e7e2dab6bea6a27a013587753'
$Tree = '702d25b5aaabbd790bd48a0ebed8d1a79f12c24d'
$Deadline = [DateTimeOffset]::Parse('2026-09-28T00:36:41Z')
if ([DateTimeOffset]::UtcNow -ge $Deadline) { throw 'STOP: deadline reached' }
$WorkRoot = Join-Path $env:USERPROFILE ('pal-rv05-' + [guid]::NewGuid().ToString('N').Substring(0,8))
if (Test-Path -LiteralPath $WorkRoot) { throw 'STOP: task already exists' }
New-Item -ItemType Directory -Path $WorkRoot | Out-Null
$Assets = Join-Path $WorkRoot 'assets'
New-Item -ItemType Directory -Path $Assets | Out-Null
$Raw = 'https://raw.githubusercontent.com/wmqfl861/polymarket-alpha-lab/58f40e7b0156dd316284ef4d5f6503c728a0ce2f/docs/handoffs/2026-09-21-rv05-capacity/'
$ManifestFile = Join-Path $Assets 'manifest.json'
Invoke-WebRequest -UseBasicParsing -Uri ($Raw+'manifest.json') -OutFile $ManifestFile -TimeoutSec 120
$I = Get-Item -LiteralPath $ManifestFile
if ($I.PSIsContainer -or ($I.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $I.Length -ne 939 -or (Get-FileHash -Algorithm SHA256 -LiteralPath $ManifestFile).Hash.ToLowerInvariant() -cne 'e81af886c2ef681fde8eb8bd0ee22cbff3a115a3316895cc4f143ad9f2d9fdd5') { throw 'STOP: manifest integrity' }
$M = Get-Content -LiteralPath $ManifestFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ($M.base_commit -cne $Base -or $M.base_tree -cne $Tree) { throw 'STOP: source binding' }
$Names = @('plan.md','queue.json','capacity_plan.py','test_capacity_plan.py','coordinator-evidence.json')
if (@($M.files).Count -ne 5 -or @(Compare-Object ($Names | Sort-Object) (@($M.files.path) | Sort-Object)).Count -ne 0) { throw 'STOP: file inventory' }
foreach ($F in $M.files) {
    $P = Join-Path $Assets $F.path
    if (Test-Path -LiteralPath $P) { throw 'STOP: destination exists' }
    Invoke-WebRequest -UseBasicParsing -Uri ($Raw+$F.path) -OutFile $P -TimeoutSec 120
    $I = Get-Item -LiteralPath $P
    if ($I.PSIsContainer -or ($I.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $I.Length -ne $F.bytes -or (Get-FileHash -Algorithm SHA256 -LiteralPath $P).Hash.ToLowerInvariant() -cne $F.sha256) { throw 'STOP: file integrity' }
}
Write-Output 'ASSETS_VERIFIED_NOT_EXECUTED'
Write-Output $WorkRoot
```

静态下载暂时超时允许同URL/同匿名身份至多两次传输重试，首败和每次元数据保留，最终完整哈希匹配。哈希、安全、权限或身份问题不绕过。此项不授权测试重试到绿、模型重发或长测重开。
coordinator-evidence.json保留创建时复跑尚在运行的快照；后续成功记录以PR评论5761566327为准，不需要改写原文件。

## 4. 复用独立工作区，不热改冻结目录

读取V3_STATE已有精确源码/runtime、launch-rv05和final-review位置，不重复向用户索要已有路径。只核验本次需要的文件，不搜索磁盘。若已有未提交合法工作，保留并协调，不reset或覆盖。
新工作必须位于非活跃候选的独立副本。需要新clone时从已知本地仓库使用--no-hardlinks --no-checkout，不使用会改原.git的worktree；再固定checkout上述Base和Tree、建立N0/N1等独立分支。运行时也不共享可写缓存。只有N0维护PR67远端，其他节点提交给N0整合。

已有V3独立副本可以复用；已有冻结发射包不能原地改。把其源码和配置模板复制到本批模块目录，保留旧哈希。N0在新manifest绑定新候选，不仅改文档标签。

推送前重新核对远端PR67：draft且未merged，读取实际head；比Base新则比较并接续，不强推、不丢修改。创建本批多个本地开发分支不等于多个PR，只更新现有草稿。
阅读AGENTS.md与DELIVERY_PLAN.md当前覆盖条款；历史外部审核要求已被自审许可覆盖，不能因此阻断任务。

$Py使用你已记录的独立复制Python3.12 x64绝对路径，不靠PATH选择未知python。新测试用已验证的净化启动方式，自己的TEMP/HOME、禁用户site/插件、不传PG/DSN/API凭据。父进程和实际孩子均校验项目、pytest、tzdata来源。
在阅读容量工具代码后，可执行其10项纯stdlib自测与算术检查。这里只设置本会话的临时目录，不改系统环境：

```powershell
if (-not (Get-Variable -Name Py -ErrorAction SilentlyContinue)) { throw 'KNOWN_RUNTIME_PATH_REQUIRED' }
& $Py -I -S -B -c "import sys,struct;assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8"
if ($LASTEXITCODE -ne 0) { throw 'STOP: runtime mismatch' }
$Scratch = Join-Path $WorkRoot 'helper-temp'
if (Test-Path -LiteralPath $Scratch) { throw 'STOP: helper attempt exists' }
New-Item -ItemType Directory -Path $Scratch | Out-Null
$OldTemp = $env:TEMP; $OldTmp = $env:TMP
try {
    $env:TEMP = $Scratch; $env:TMP = $Scratch
    & $Py -I -S -B (Join-Path $Assets 'test_capacity_plan.py')
    if ($LASTEXITCODE -ne 0) { throw 'HELPER_TEST_FAILED_KEEP_FIRST_RESULT' }
} finally { $env:TEMP = $OldTemp; $env:TMP = $OldTmp }
```

将已知launch-rv05里经审查的新11场景manifest复制到本批材料目录，把$NominalManifest赋为该准确路径，不猜文件名或扫描。然后：

```powershell
if (-not (Get-Variable -Name NominalManifest -ErrorAction SilentlyContinue)) { throw 'KNOWN_NOMINAL_MANIFEST_REQUIRED' }
$Report = @(& $Py -I -S -B (Join-Path $Assets 'capacity_plan.py') --manifest $NominalManifest --seed 2026092103 --rounds 864 --compute-name ok-compute --subinputs 2048)
$CapacityExit = $LASTEXITCODE
$Report
if ($CapacityExit -ne 0) { throw 'CAPACITY_PLAN_INVALID_OR_INSUFFICIENT_TRIAGE_WITHOUT_CHANGING_THRESHOLD' }
```

若实际计算场景不是ok-compute，核对原manifest后传真实名称，不随意重命名改变排程。
helper仅校算当前排序/seed规则，不执行manifest代码、不生成或证明100k输入、不判GO。若计划改变选择算法，工具输出不再适用，先明确合同；不要偷偷改seed凑次数。

## 5. 六个逻辑节点和持续调度

先由N0固定一个最小公共回执合同草案，让N1和N2分别确认。然后并行：

N0 集成/调度：独占soak_driver接线、公共合同、最终manifest/config、PR67集成。建立READY/RUNNING/REVIEW/DONE/BLOCKED任务板，每项写owner、文件、依赖、SHA、测试证据和下一任务。节点完成即调度下一项就绪且不冲突的工作，不让所有人等72小时。

N1 生成器：独占已存在ok-compute生成器/fixture及其测试。不编辑audit或公共driver；必要接线交N0。实现“输入真正进入目标函数→实际结果→oracle检查→逐项回执”，随后做中途失败/篡改/重复反例。

N2 独立审计：独占soak_audit及审计测试。不编辑生成器，不用同一个生产计算函数算期望。独立重建输入、结果规则、绑定和去重；随后核验旧记录兼容、限额、去重边界。

N3 场景合同：独占13→11+2映射及故障合同测试。每个旧场景名称、代码摘要、预期语义、去向都列明。最终manifest交N0写入，不与N1争同一文件。

N4 kit诊断：先读取协调者已完成的attempt2，不再rerun或重扫历史。可在既有packaged_session_drain测试助手中补最小封闭字段失败见证和负向测试：阶段/原生命令enum/退出码/固定异常类别，不泄露argv/env/DSN/密码/原始stderr。观察逻辑不改原执行次数、时限、公开异常和控制流，观察失败不能掩盖原异常。不要为本次已通过重复制造kit运行，也不要把诊断增强扩大为框架。

N5 发射/终审：独占新副本中已有launch-rv05和final-review源码及必要soak_closeout接线，先做新旧schema/哈希漂移/重复发射拒绝测试；待N1/N2/N3/N0就绪再绑定新候选，复用已有22项预检，不另造第三层工具。

六个逻辑节点不强制六个物理机器或六个模型会话。使用现有获准agent能力，按容量动态运行；不足则单Agent交替推进，不能去新建凭据/主机/付费客户端。禁fast mode。并行代码审阅可行，同机合成子进程初始仅增加1个以保护旧soak。
共享文件只有一个writer，其他节点提交补丁/接口请求；不得同时写一个driver后靠merge赌不冲突。已完工节点转交叉只读审核或下一独立反例，不机械降低并发也不空转填满并发。

## 6. 逐子输入合同：必须测试真实函数，不是做十万个哈希

逐条身份绑定目标函数/语义域与真正影响行为的规范化输入，排除轮号、segment、临时路径、无关nonce或只用于搬运的seed。相同输入跨轮/场景/机器只计一次。覆盖数量不是统计独立样本或策略有效性证明。
必须复用已有项目协议边界、时间/哈希、cache计数或Decimal等实际不变量。单纯生成随机数再hash不算输入完成校验。不能只有预生成摘要而没有目标调用与检查。

回执新schema，公共header固定候选/tree、生成器/合同/manifest摘要、轮与segment/子seed。每条可用紧凑[input_sha256,actual_result_sha256]，由N2独立的静态白名单规则重建实际输入和expected结果再比较。若有多语义族，明确domain/group；不要从回执eval任意代码。

默认每个ok-compute轮2048条，最多4096条，回执建议上限512KiB；保持现有1MiB stdout、2MiB轮记录上限。序列化真实字节、读取上限、内存、证据副本和日志重复量都实测，不接受“约240KB”的口头预算。
仅同一合格segment内passed轮、逐条验证与输出绑定通过的不同input hash计入100k。中途错误、输出截断、短写/确认未知使该轮不合格；可保留有效前缀但不贡献本次合格计数。旧回执无逐条证据保持可证明下界或UNKNOWN，绝不回填。
分别列planned/generated/attempted/completed/oracle_passed/distinct_qualified，回执声明不是执行证明；信任边界依然包含审核过的测试程序和宿主。

必要回归：正常控制、真实输出篡改、重复输入/行、仅nonce变化、遗漏/截断、错seed/候选/入口/结果、重复键/布尔冒充数值/不支持版本、超限、执行中断、写失败、旧schema、跨segment混用。R1—R7既有修复保持通过，不重做历史RED全套制造新进度。

## 7. 11+2覆盖与容量预演

保留11名义场景及两个故障场景的全部旧历史。两个故障合同以外层断言验证预期底层失败并PASS，不能直接把原failed改为expected/pass；它们不计入新名义864轮或100k。
预演前和长测结束后各执行一次故障合同；名义长测范围写清11场景配置。执行差异必须先冻结，不能看失败再从新manifest删除场景。

当前算法/固定seed/864轮/11排序位置，计划次数为74/82/84/82/83/59/75/75/82/89/79。最低59次计算轮×2048=120832个槽位，已有容量余量，但真实名称需用工具确认排序；槽位不等于不同有效输入。
先完整有限展开候选的100k容量，验证实际目标调用、oracle和去重，再做全部11场景小型验证、两类故障合同、20分钟新候选预演、分离自审和适用CI。有限容量试验不计正式72小时。
不单靠audit扩大数值，不从max_rounds或统计容差“补”数量/时间。预演出现真实失败先保留并修复，不自动反复运行同SHA至绿。

## 8. 连续几天工作的实际顺序

现在至原soak结束前：N0合同/队列，N1/N2生成器与独立审核并行，N3场景映射，N4小型诊断增强，N5发射拒绝测试。按模块提交，N0集成到唯一草稿PR67。
生产源码/数据库/依赖禁止改动，不因“变更很小”跳过验证；新候选的所有适用CI必须真正在最终SHA完成，旧attempt2不能代替。已过往测试不无意义重跑，真正改动后跑相关回归和原适用最终门禁。

新正式72小时只能在以下全部成立后发射一次：
原驱动实际退出、其自有孩子清理已确认、旧证据固定；
新源码/运行时/生成器/manifest/发射包身份固定；
容量、故障合同、20分钟预演、自审和适用门禁通过；
资源健康；未晚于2026-09-24T12:36:41Z。

不是计划时刻到点或某个PID不见就发射。启动先持久保存唯一claim，lost-ack保留UNKNOWN，不补发第二次。复用已做发射控制，必要修改只在新副本；它不调用模型、不自动推送、不建服务/计划任务。没实际启动就写NOT_STARTED，真正启动后提供命令、PID+创建标识、配置SHA、首轮回执和STOP路径。

正式配置：seed2026092103；period300秒；heartbeat60秒；checkpoint7200秒；summary43200秒；maxgap900秒；最低259200秒、864有效passed轮、100000实际已验证不同输入分别判定。最晚启动仍预留12小时终审。
正常负向输入的断言通过属于passed；非预期failed/unknown/interrupted、身份或证据错误、清理不确定即停止新长测，保留首败，不自动第二次长测。睡眠/重启/gap不能拼接；受测代码改变不能借用此前时长。原soak不因新任务抢资源被停止。

全部流程合计工作集目标2GiB、相关新目录4GiB、证据512MiB、剩余卷至少10GiB。能力不可测写未测，不宣称硬限额。必要时暂停旁路而非破坏主任务，禁改休眠策略凑时长。

超过最晚启动或任何前置缺失：RV05=NOT_STARTED/PARTIAL，继续完成可做代码/审核和具体阻断；不缩短72小时、不延长原截止、不改seed/manifest凑绿。

## 9. 接续、审核与最终回执

每2小时保存状态、每12小时总结实际新增工作和证据，不只报“进程还活着”。已知授权不得再次作为WAITING_OWNER；真正系统/凭据/生产范围改变仍单独待批准，不阻断独立任务。
节点会话退出前保存所属文件、完整SHA、测试和待接续项。未知运行不因超时自动另派同任务，先核实旧owner。模型会话断线与持久测试进程存活分开报告，不能声称未实现的自动唤醒。

最终先冻结，再做独立步骤只读自审；交叉审核写明实际审核者，单Agent也可但准确标注分离自审。原候选d929、旧sidecar2584、新候选及各自scope/失败/通过分开，不把代码CI/工具READY/观察时长当V1验收。
上传限源码、测试、配置模板和经审阅脱敏摘要，禁止整工作区/原环境/凭据/厂商程序。N0独占PR67推送；远端变化先合并协调、不强推、不新增竞争PR、不合并main。主节点继续派发就绪独立任务，不因一个节点完工或一次CI通过停止全部开发。

最终返回：三个裁决已执行到哪一步、N0—N5产物与完整SHA/tree、逐条执行/去重证据规则、11+2覆盖映射、kit首败+一次重跑、新候选门禁、实际72小时/有效轮/不同输入/gap/资源、所有失败/未知/跳过/未执行、分离自审和下一唯一关键动作。
到原2026-09-28T00:36:41Z不再派发新测试，清理实际超时单列。可提前完成有意义工作，不空等待或无效重测凑天数。

始终official_cases_run=0、sandbox_started=false、activation_authorized=false。ADMIN-CLI继续WAITING_OWNER，G2—G6不因新增代码或多日测试完成。
现在接续：下载核验、N0固定合同与归属，N1/N2/N3立即并行推进，N4/N5做各自独立工作。无需再请求本提示词已经给出的两项候选/场景授权。
