# PAL_RV05_CONTROL_SAFETY_20260922

继续原RV05和唯一草稿PR67，原总截止不变。承认已完成的151552容量、DST修正、11+2、N5可分发交付和已有CI，不重复派发。此轮先处理已被协调者实证的自动启动合同缺陷，再继续原批准的多日任务；不是为了凑天数新增框架或验收指标。

## 1. 先暂停旧自动衔接，不停止原soak

协调者核对精确b061源码后，复现9条反例、5个合法控制正确：预检中撤销仍进入启动、预检跨窗仍启动、rc7失败回执下次变STARTED、rc0无campaign仍STARTED、argv仅含pinned路径就获准、Bypass获准、超限JSON合法前缀获准、两个Windows查询失败被判dead。全部为合成替身，没有操作本机或真实发射。PR67评论5771446403已记录HOLD。

这不是已证明真实数据被破坏或已经双发射；但旧控制包不能继续无人值守等待发射。第一步只撤销尚未消费的linker，并保留原arm/原长测。原74784/62420/20372/98500都是历史PID，不能仅凭数字杀进程。

使用你已经给出的准确路径，不扫描或重新索要：
- Python：C:\Users\Joyce Gu\pal-fix67-fc555171\runtime\python.exe
- linker：C:\Users\Joyce Gu\pal-rv05-caaa8b5b\n0c\src\tests\support\soak_linker.py
- binding：C:\Users\Joyce Gu\pal-rv05-caaa8b5b\linker-control\linker-binding-rv05-candidate.json

先只读binding指定的state、claim、started receipt以及launch_spec指定的内层claim/receipt。核对：还没进入最早窗口，仍ARMED_WAITING，两层都没有消费claim或启动回执。检查只涉及这些已知文件和其祖先；禁止枚举个人目录。state/stop/各自.tmp必须是该独立linker-control内的不同普通文件路径，不能是campaign路径、链接或重解析点；binding hash应与已记录arm一致。不能用JSON里的一个state字段替代完整条件。

若上述条件不成立，或已CLAIMED/STARTED/UNKNOWN、窗口已打开、路径/权限/文件身份不可信，记录HANDOFF_STATE_CHANGED，只读保全，停止本节快捷撤销/重arm；由N0查清不可逆边界并升级具体处置，其他安全源码工作继续。不得删claim、换task ID或新目录绕过一次性约束。

仅在上述条件已核对时执行原有cancel，不运行launcher：

```powershell
$ErrorActionPreference = 'Stop'
$KnownPy = 'C:\Users\Joyce Gu\pal-fix67-fc555171\runtime\python.exe'
$KnownLinker = 'C:\Users\Joyce Gu\pal-rv05-caaa8b5b\n0c\src\tests\support\soak_linker.py'
$KnownBinding = 'C:\Users\Joyce Gu\pal-rv05-caaa8b5b\linker-control\linker-binding-rv05-candidate.json'
if ([DateTimeOffset]::UtcNow -ge [DateTimeOffset]::Parse('2026-09-24T02:14:50Z')) {
    throw 'HANDOFF_STATE_CHANGED: this pre-window cancellation path no longer applies'
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $KnownLinker).Hash.ToLowerInvariant() -cne '1e1edfd4a90d5f57ab0e4845859e18ed3a30b9f6dd14a4ed3d2d084fe6709965') {
    throw 'HANDOFF_STATE_CHANGED: verify actual control source before execution'
}
& $KnownPy -I -S -B $KnownLinker cancel --binding $KnownBinding
if ($LASTEXITCODE -ne 0) { throw 'CANCEL_NOT_CONFIRMED: preserve evidence, do not retry or force' }
```

cancel子命令退出0不等于持久wait已经退出。按原启动receipt的创建标识核对旧wait结束，保留CANCELLED和STOP回执。只写linker自己的撤销标记与控制状态；不写原/新campaign STOP，不热修活跃脚本，不全局taskkill。若旧wait未退出或身份不确定，保持HOLD，不开第二份衔接器。这里的旧cancel只限预窗口尚未消费的安全路径，不用于证明已经修复预检期间的撤销竞态。

## 2. 固定源码、时限和交付

repository：wmqfl861/polymarket-alpha-lab
PR：67，唯一草稿，不合并main
PR branch：work/pal-parallel-20260921-ad7a9c77be5a497ab908bb33b031a182
修正基线：b061b7d7ad51cd21d7a64c84eba0941a1fd08085
代码树：79ec2d7945f9dd2b492e243521e0c166f8fbebd0
原soak：d929cc35214996a44008d4bbdd643bc8cc3550ea

启动窗口：2026-09-24T02:14:50Z至2026-09-24T12:36:41Z。
台北：09-24 10:14:50至20:36:41。
整批截止：2026-09-28T00:36:41Z，台北09-28 08:36:41。
原72小时/864有效通过轮/100000实际不同合格输入不变，不并发第二份正式长测、不第三次长测、不滚动重计七天。

本轮资产提交：27f5bc3b08e09715c0876b0f76d36f93e0bd5288。
保留分支：handoff/rv05-linker-safety-20260922。
目录：docs/handoffs/2026-09-22-linker-safety/。
manifest.json：833字节；SHA256 c0517d2abfa1759c3ae40956ca5bb4e6adf96c55d0e5676bfeea0ae0fb500b7e。
覆盖plan.md、queue.json、repro_linker.py、coordinator-evidence.json。文档只阅读，不作为脚本执行。

协调者已核验6543源码blob与完整树，已执行有限反例及原20项linker测试（Linux20passed）。实际Windows证据是456passed，其中N5=39，linker=0；因此Windows CI要补linker，而不是要求再做已完成的N5可分发重写。初始系统Python缺pytest的引导失败在记录中保留，没有被算作测试。其他三工作流本轮仅核对status，不能说重新审过所有日志。

## 3. 下载与校验；复用已有状态，不重复初始化

已存在本任务状态就读取接续，保留未提交修改。不因任务号更名复制一整套。首次只在新短路径目录存新文件：

```powershell
$ErrorActionPreference = 'Stop'
$Base = 'b061b7d7ad51cd21d7a64c84eba0941a1fd08085'
$Tree = '79ec2d7945f9dd2b492e243521e0c166f8fbebd0'
if ([DateTimeOffset]::UtcNow -ge [DateTimeOffset]::Parse('2026-09-28T00:36:41Z')) { throw 'STOP: original deadline reached' }
$WorkRoot = Join-Path $env:USERPROFILE ('pal-linkfix-' + [guid]::NewGuid().ToString('N').Substring(0,8))
if (Test-Path -LiteralPath $WorkRoot) { throw 'STOP: attempt exists' }
New-Item -ItemType Directory -Path $WorkRoot | Out-Null
$Assets = Join-Path $WorkRoot 'assets'
New-Item -ItemType Directory -Path $Assets | Out-Null
$Raw = 'https://raw.githubusercontent.com/wmqfl861/polymarket-alpha-lab/27f5bc3b08e09715c0876b0f76d36f93e0bd5288/docs/handoffs/2026-09-22-linker-safety/'
$MF = Join-Path $Assets 'manifest.json'
Invoke-WebRequest -UseBasicParsing -Uri ($Raw+'manifest.json') -OutFile $MF -TimeoutSec 120
$I = Get-Item -LiteralPath $MF
if ($I.PSIsContainer -or ($I.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $I.Length -ne 833 -or (Get-FileHash -Algorithm SHA256 -LiteralPath $MF).Hash.ToLowerInvariant() -cne 'c0517d2abfa1759c3ae40956ca5bb4e6adf96c55d0e5676bfeea0ae0fb500b7e') { throw 'STOP: manifest integrity' }
$M = Get-Content -LiteralPath $MF -Raw -Encoding UTF8 | ConvertFrom-Json
if ($M.task_id -cne 'PAL_RV05_CONTROL_SAFETY_20260922' -or $M.base_commit -cne $Base -or $M.base_tree -cne $Tree) { throw 'STOP: wrong task binding' }
$Names = @('plan.md','queue.json','repro_linker.py','coordinator-evidence.json')
if (@($M.files).Count -ne 4 -or @(Compare-Object ($Names | Sort-Object) (@($M.files.path) | Sort-Object)).Count -ne 0) { throw 'STOP: inventory mismatch' }
foreach ($F in $M.files) {
    $P = Join-Path $Assets $F.path
    if (Test-Path -LiteralPath $P) { throw 'STOP: existing asset' }
    Invoke-WebRequest -UseBasicParsing -Uri ($Raw+$F.path) -OutFile $P -TimeoutSec 120
    $I = Get-Item -LiteralPath $P
    if ($I.PSIsContainer -or ($I.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $I.Length -ne $F.bytes -or (Get-FileHash -Algorithm SHA256 -LiteralPath $P).Hash.ToLowerInvariant() -cne $F.sha256) { throw 'STOP: asset integrity' }
}
Write-Output 'LINKER_REVIEW_MATERIALS_VERIFIED_NOT_REARMED'
```

读完所有文件。静态传输临时超时仅按既有规则最多两次同URL/同匿名身份重试并分别保留首败；哈希/权限/安全拒绝不能改路线或身份绕过。这个许可不适用于失败测试、CI或启动重试。

## 4. 独立副本与有限反例的用法

从既有RV05_STATE读取准确本地Git仓库路径赋给$Repo，不能扫描或再次索要已知路径。只读核验，再独立clone；原运行和旧控制目录不checkout/reset/rebase。

```powershell
if (-not (Get-Variable -Name Repo -ErrorAction SilentlyContinue)) { throw 'KNOWN_REPO_REQUIRED: read recorded state only' }
$Actual = git --no-optional-locks -C $Repo rev-parse --verify ($Base+'^{commit}')
if ($LASTEXITCODE -ne 0 -or $Actual -cne $Base) { throw 'STOP: fixed source absent' }
$ActualTree = git --no-optional-locks -C $Repo rev-parse ($Actual+'^{tree}')
if ($LASTEXITCODE -ne 0 -or $ActualTree -cne $Tree) { throw 'STOP: tree mismatch' }
$Src = Join-Path $WorkRoot 'source'
git -c core.autocrlf=false -c core.eol=lf clone --no-hardlinks --no-checkout -- $Repo $Src
if ($LASTEXITCODE -ne 0) { throw 'STOP: separate clone failed' }
git -C $Src -c core.autocrlf=false -c core.eol=lf checkout --detach $Base
if ($LASTEXITCODE -ne 0) { throw 'STOP: separate checkout failed' }
$CopyTree = git -C $Src rev-parse 'HEAD^{tree}'
if ($LASTEXITCODE -ne 0 -or $CopyTree -cne $Tree) { throw 'STOP: copied source mismatch' }
$Branch = 'work/linker-safety-' + [guid]::NewGuid().ToString('N')
git -C $Src switch -c $Branch
if ($LASTEXITCODE -ne 0) { throw 'STOP: new branch failed' }
$Evidence = Join-Path $WorkRoot 'evidence'
New-Item -ItemType Directory -Path $Evidence | Out-Null
```

使用已经校验且不共享可写缓存的独立runtime。需要复制时只从已知准备包stage/input/python，按原清单核验；不安装、不降级uv、不重新下载环境。$Py为已知Python3.12 x64，-I -S -B，自己的TEMP/HOME；不传真实PG/DSN/API环境。GitHub源码读写和测试环境分开，使用现有正常认证但不得读取/打印凭据。

协调者已有9/5历史证据；同源本地复现只需一次，不必把这当新的长期验收指标。要复核时运行：

```powershell
if (-not (Get-Variable -Name Py -ErrorAction SilentlyContinue)) { throw 'KNOWN_ISOLATED_RUNTIME_REQUIRED' }
$Scratch = Join-Path $WorkRoot 'repro-first'
if (Test-Path -LiteralPath $Scratch) { throw 'STOP: preserve prior reproduction' }
$Lines = @(& $Py -I -S -B (Join-Path $Assets 'repro_linker.py') --source $Src --scratch $Scratch)
$RC = $LASTEXITCODE
$Utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $Evidence 'historical-repro.json'), ($Lines -join "`n"), $Utf8)
$R = ($Lines -join "`n") | ConvertFrom-Json
if ($RC -ne 3 -or $R.reproduced -ne 9 -or $R.controls_passed -ne 5) { throw 'REPRO_DIFFERENT: preserve first evidence and triage' }
```

退出3=原缺陷按预期复现，不是通过；退出5=某反例或setup未匹配。探针不启动进程/厂商CLI，假Win32不等于真实WindowsABI。探针固定历史source hash；不要改它让修复版通过。把反例迁入pytest的正确断言，修复版另测。不要在活跃目录或原campaign指定scratch。

## 5. 六节点分工，避免同文件多writer

N0负责旧arm撤销与进程退出证明、任务队列、版本映射、控制包和PR67整合。记录SUT_SHA、CONTROL_SHA、CONFIG_SHA、RUNTIME身份；所有实际命令、输出和失败一一绑定。先保全已有完整容量结果，不要求重做151552。

N1是soak_linker.py唯一主writer，修L1-L4：预检后与实际启动前的撤销/时间/绑定再次判定，定义claim与cancel的不可逆边界；失败/未知回执不得在下一次wait升级成功；完整claim写入确认，错task/claim/binding/schema/结果拒绝。多加一个if不等于所有竞态已解决，须明确安全点和并发协议。

N2负责tools/soakctl里的launcher/preflight/模板/说明，修L5-L6：实际可执行文件、argv位置、参数集合、SpecPath、cwd/env封闭核对；禁止Bypass及等价别名，不把pinned文件名当无关参数就接受。预检和实际launcher两层都做最后安全点复核；报告绑定本次执行，不读上次残留GO。linker改动给N1补丁，不直接覆盖它。

N3负责有界文件与Windows查询补丁，修L7-L9：max+1超限拒绝、重复JSON键/非标准数值与类型拒绝、WindowsAPI原型与三态结果。OpenProcess NULL先按错误码分类，WAIT_FAILED保持unknown，64位HANDLE不截断，句柄清理不双关。错误不提权。相关linker子补丁交N1，不能另起一个重复实现。

N4负责独立tests/test_soak_linker_review.py、既有Windows专项选择和实际证据。覆盖九反例、合法控制及邻接边界；Windows真实进程加受控错误注入，保留原20项、N5 39项和两个平台用例。不要只用mock声称WindowsABI验证完成。当前workflow没有linker模块必须补入，不删旧测试、不放宽时间/权限/依赖。新增回归文件名也加入路径触发。

N5负责既有ops/DELIVERY_PLAN/最终证据，不新写报告框架。废弃Bypass示例，纠正“ARMED_WAITING不存在”的过时语句到新更正记录，不能修改武装材料。准备同一PR的最终操作与版本清单；保留原容量、失败和修正证据。长测后执行原两类故障合同及分离自审，不重新扫历史kit或重复R1-R7/42组合。

六节点是逻辑职责，只用既有获准能力；不能新建机器、凭据或额外官方CLI。没有六个Agent就轮转，禁止fast mode。初始只新增一个合成worker，源码阅读可并行。N0持续分配READY任务或交叉只读审核；一个节点完成不使全队停工，也不为空闲造新框架。

## 6. 修复的具体通过条件

撤销竞争：在预检运行、预检返回、claim前后、实际启动前用barrier/事件确定性注入；已确认且发生在不可逆边界前的撤销不能启动。确认丢失保留unknown/消费slot，不能新目录重发。提供正常启动和撤销太晚的准确控制，不承诺任意时刻都能撤回已发生动作。

窗口与pin：预检跨过latest、期间binding/pin变化、旧GO文件、非零却残留GO、未知门禁、伪造或混合report均拒绝；不能以一次早先GO替代实时启动边界。实际launcher同样验证，没有仅外层补丁掩盖第二次预检耗时。

回执：启动返回非零保持失败或未知；返回0也需启动receipt/claim/目标目录及必要进程证据一致。STARTED不是DONE/qualified。重新wait不能把旧rc7或缺campaign“治愈”为成功。新证据的人工重新判定与自动补发严格分离。

命令：只允许批准的严格PowerShell命令语法，-File后就是已pin launcher、SpecPath就是已pin spec，不接受-Command/-EncodedCommand或仅含文件名。正式配置不接受--now-utc等合成钩子。测试通过内部注入边界使用无副作用runner，不用真实配置放宽语法来让旧fixture通过。

策略：不得执行-ExecutionPolicy Bypass、Unblock-File、改ACL、stdin脚本或换解释器绕过组织/主机拒绝。策略拒绝如实BLOCKED，源码与CI工作继续。README里存在Bypass不是已有授权。

I/O和Windows：读前有界，不以截断后的合法JSON冒充完整记录；所有state/stop/tmp/claim/receipt独立目标与路径守卫一致。检查短写与fsync/replace失败而不删除旧证据。实际Win32 HANDLE宽度、NULL错误、WAIT_FAILED、已退出、仍运行、访问拒绝均单列；模拟错误映射和真实ABI分别给证据。不得按名字或裸PID全局清理。

新回归完成后，在净化环境仅跑受影响的小集合，再跑原适用最终CI。可以复用已有净化bootstrap，下面是本批小集合的明确入口；只有新增回归文件已经完成时才执行：

```powershell
$Site = Join-Path (Split-Path -Parent $Py) 'Lib\site-packages'
$Out = Join-Path $Evidence 'linker-review-first'
if (Test-Path -LiteralPath $Out) { throw 'STOP: preserve first regression output' }
New-Item -ItemType Directory -Path $Out | Out-Null
$Bootstrap = @'
import os,sys
from pathlib import Path
src,site,out=(Path(v).resolve() for v in sys.argv[1:4])
systemroot=os.environ.get('SystemRoot')
os.environ.clear()
os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1')
if systemroot: os.environ['SystemRoot']=systemroot
for name in ('TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA'):
    d=out/name.lower();d.mkdir(exist_ok=False);os.environ[name]=str(d)
sys.dont_write_bytecode=True
sys.path[:0]=[str(src/'src'),str(src),str(site)]
os.chdir(src)
import pytest
assert Path(pytest.__file__).resolve().is_relative_to(site)
raise SystemExit(pytest.main(sys.argv[4:]))
'@
& $Py -I -S -B -c $Bootstrap $Src $Site $Out -q -p no:cacheprovider tests/test_soak_linker.py tests/test_soak_linker_review.py "--basetemp=$Out\pytest-temp" "--junitxml=$Out\result.xml" 2>&1 | Tee-Object -FilePath (Join-Path $Out 'result.log')
$Code = $LASTEXITCODE
if ($Code -ne 0) { throw 'REGRESSION_FAILED: retain first result and triage, no automatic rerun' }
```

以上bootstrap不包含pytest-site外的用户包，测试必须显式使用需要的系统程序绝对路径，不依靠用户PATH找凭据或工具。新旧测试需要其他已知只读工具时，复用已经审阅的显式路径，不恢复整套宿主环境。XML完整性、跳过、调用分母和真实平台单列；编译数不是测试数。对相同源码的失败不得CI抽奖；修复后的新运行保留与原失败对照。

## 7. 不推翻已完成容量；控制包与被测版本分离

只修改linker/launcher/预检/控制说明时，优先保持被测driver、生成器、审计、三族、manifest、runtime和原b061 SUT目录字节不变。新控制代码从独立控制包执行，分别绑定SUT_SHA和CONTROL_SHA。

N0证明受影响依赖范围后复用已有151552完整容量，不因为新的Git提交号就盲目再跑。新控制包CI和启动演练仍必须针对新代码；不能把它说成旧控制也已修复。

若发现真正需要改被测函数或审计，保留最小反例并明确版本影响，受影响验证与新冻结必需；旧长测时间不能转给新SUT。但不为了长任务主动寻找理由解冻。

N1/N2/N3修复后先在独立目录做有限合成完整衔接演练：旧失败正常结束、旧仍运行、取消、跨窗、双实例、权限未知、启动后ack丢失、完整成功。再分离自审及Windows实际门禁。不是另一轮72小时，不安装厂商程序、不连接业务库。

## 8. 新控制包重新arm与数日工作安排

09-22：N0撤销旧未消费arm并确认wait退出，N1-N4修复与验证，N5准备交付。
09-23：新控制包独立Windows验证、有限端到端演练、同版本自审和适用CI，绑定发布材料。
09-24窗口内：原soak实际退出、原自有孩子清理确认、旧证据固定且稳定、新前置全满足后，最多一次启动已批准修正版72小时。
09-24至09-27：同SUT连续观察；其他节点只读或独立文档审核，不热修活对象。
结束后至09-28截止：原与修正版分开审核、后置两个故障合同、最终自审及PR67交付。

允许在上述证据齐全后按原批准范围部署新的有限衔接，无需重复请求同一离线长测授权；不允许越过未知状态。新arm必须满足：旧wait确已退出，旧两层slot未消费，旧cancel已留证，新控制版本审核和真实门禁通过，绑定无Bypass/合成clock/占位符或身份漂移。旧slot已消费或UNKNOWN绝不能换目录重arm。

原绑定和控制输出保留，新控制目录不同，只有一个活衔接器。N0提供新的准确arm/wait/status/cancel命令、创建标识、配置摘要、首条回执和撤销路径，并把无个人路径模板、使用说明及hash清单放GitHub。本地路径绑定私留，不上传完整环境。

不能只凭时间到点或裸PID消失认定原运行结束；也不必要求旧soak全绿，已发生失败仍是旧版本结果。目标259200秒/864passed/100000实际不同合格输入保持独立判定；unknown不算passed；有意故障在外层合同正确时才是合法通过。

赶不上09-24 20:36:41台北就NOT_STARTED/PARTIAL，交付已经完成工作；不提前并发、不缩短指标、不延长09-28总截止、不第三次长测。新运行遇非预期failed/unknown/interrupted、证据或身份异常安全停止保留首败，不自动重开。有限控制器不自动唤醒语言模型、不自动改代码/推送/合并。

## 9. 资源、禁止范围与最终回执

合计工作集目标2GiB、相关新目录4GiB、证据512MiB、卷可用至少10GiB；复制与失败证据都计入。测不到标未测，不声称硬限额。主任务压力大暂停旁路，不停止原soak腾资源。每2小时检查点、12小时实际进展摘要，复用现有观察，不新建第三个watcher。不改休眠/电源/系统策略，不为凑天数空轮询。

禁止真实CLI/API/OAuth/订阅凭据、真实行情/钱包/订单、用户业务库与本机原生DB验收，禁止生产src/SQL/依赖锁/交易开关修改，禁止Sandbox/BIOS/BCD/VBS/防火墙/ACL/执行策略/服务/计划任务、提权/安装/全局工具改变与厂商下载绕过。GitHub现有隔离测试不等于本机业务操作授权。旧业务根不得读写：
C:\Albert\project\polymarket-alpha-lab-acceptance-4d63b08
C:\Albert\project\polymarket-alpha-lab-kit-startup-4d63b08
C:\Albert\acceptance-assets\runner-temp
既有准备目录只读指定材料，不改input/output/.wsb，不重新下载。

只有N0整合并非强制更新原PR67分支；写前重新核对远端，变化先比较、不覆盖。所有本地修改使用独立副本和单writer，禁止强推/合并main/新建竞争PR/未经授权正式Release。源码和必要脱敏摘要可发布，厂商程序、完整工作区、真实环境与凭据不能上传。

最终先冻结再只读自审，标明同Agent或实际交叉审核，不冒充第三方或零缺陷。自动框架“恰一次成功”的措辞改为“至多一次已接受启动尝试；确认丢失可能UNKNOWN”，不能从绿标推出必然启动或业务验证完成。

下一份回执首先给旧arm撤销及wait退出是否确认，其次N0-N5实质工作、九反例修复证据、真实Windowslinker清单、是否复用原容量、SUT/控制包/配置身份及新arm状态。不是仅给PID存活。

最终分别保留原d929、b061 SUT、旧控制、新控制以及各自CI/容量/长测/失败与unknown。READY不是qualified，编译数不当测试数。全部有用工作完成可提前收尾，达到原截止不派新任务，在途清理时间单列，不无限滚动。

常量：official_cases_run=0、sandbox_started=false、activation_authorized=false。ADMIN-CLI仍WAITING_OWNER，不阻断本批明确修正。G2—G6不因输入数或运行天数自动完成。
