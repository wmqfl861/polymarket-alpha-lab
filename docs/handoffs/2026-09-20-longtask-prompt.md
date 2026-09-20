# 给本地 Agent：PAL_LONGTASK_20260920_V1

你现在执行一批最长七天的自主开发与稳定性验证，不是再做一次环境查询后结束。默认总窗口 168 小时，核心目标为同一冻结候选连续 72 小时观察；有实际新增覆盖目标才能延长至最多 120 小时观察，仍受总窗口约束。已有可做工作就继续，不因一个小提交/一次推送完成而停止，不用重复全量、空等待或制造改动凑时长。

## 1. 已知事实与本次权限

此前下载/校验、只读系统查询、离线暂存已完成。主力机 Enterprise x64，Sandbox disabled，virtualization_firmware=false，hypervisor_present=true；这些字段原样保留，不重复查询，不推断必须改 BIOS。旧 ZIP 首次超时和一次同 URL 匿名续传如实保留，最终校验通过不写成首次无错。

本次允许在全新工作目录做源码阅读、最小缺陷修复、离线单元/性质测试、自己编写的合成 Python 子进程实验、长测及自审。旧任务“到暂存就停止”只约束上一批，不阻止本批明确允许的离线工程工作。

禁止连接任何用户业务数据库，禁止执行 init/up/down/backup/restore/migrate 或原生数据库测试；禁止运行官方 Claude/Codex/Grok/ZCode/OpenCode 程序、读取真实 API/OAuth/订阅凭据、访问真实行情/模型/钱包/订单。你正在使用的协调会话不授权另外寻找密钥或启动新模型客户端。不要因为新子 Agent 配置不可用而退出，可以单 Agent 顺序执行；有既有获准能力才按项目模型/推理规则并行，文件归属不重叠，禁止 fast mode。

不提权、不启用 Sandbox、不启动 .wsb、不重启、不改 BIOS/UEFI/BCD/VBS/Memory Integrity/防火墙/ACL/执行策略/电源/防休眠/计划任务；不绕过受限厂商下载。不改全局 Python/uv，不跑安装器。系统/官方 CLI 支线标为 WAITING_OWNER，但不阻断下面的主工作线。

不读写以下旧业务根：
- C:\Albert\project\polymarket-alpha-lab-acceptance-4d63b08
- C:\Albert\project\polymarket-alpha-lab-kit-startup-4d63b08
- C:\Albert\acceptance-assets\runner-temp

只读取已知准备根 `%TEMP%\pal-probe-preparation-a0f188d97fe646e684e4d3f2dbe878ca` 内指定的工具、ZIP/清单和 stage/input；不枚举其他用户目录，不改原准备输入/output/.wsb，不重新下载离线 ZIP。实际运行解释器先复制到本批新目录，避免 Python 子进程写缓存污染原准备包。

## 2. 固定计划、源码与下载

仓库 wmqfl861/polymarket-alpha-lab。
源码基线 199f5a4f629466467f638d7f4a383e4bead19120，完整树 5202e9a8422d99922eb60725b1384f48299d2257。
计划/队列交付提交 bf0c83d26f86493de6d1c9db273619048b885fa1，保留分支 handoff/wp02-seven-day-campaign-20260920。

下面只下载计划与队列到新目录。随后读完二者；JSON 是任务配置而非已存在的可执行调度器。计划内含必须实现的驱动/资源/恢复验收要求。它们不表示长测已运行。

```powershell
$ErrorActionPreference = 'Stop'
$Base = '199f5a4f629466467f638d7f4a383e4bead19120'
$Tree = '5202e9a8422d99922eb60725b1384f48299d2257'
$Delivery = 'bf0c83d26f86493de6d1c9db273619048b885fa1'
$Root = Join-Path $env:TEMP ('pal-longtask-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $Root) { throw 'STOP: attempt exists' }
New-Item -ItemType Directory -Path $Root | Out-Null
$Evidence = Join-Path $Root 'evidence'
New-Item -ItemType Directory -Path $Evidence | Out-Null
$Raw = 'https://raw.githubusercontent.com/wmqfl861/polymarket-alpha-lab/' + $Delivery + '/docs/handoffs/'
$Downloads = @(
    [pscustomobject]@{Name='2026-09-20-longtask-plan.md'; Bytes=12050; Hash='17732a4d98dd4a7671fdf54780ba0a007cc93316d8c231aff5b8dcf4ce0d6553'},
    [pscustomobject]@{Name='2026-09-20-longtask-queue.json'; Bytes=4136; Hash='d7498c4d9f04c046d6ed98013b06628a0a51875626da6696426943bd6b7d692b'}
)
foreach ($f in $Downloads) {
    $p = Join-Path $Root $f.Name
    Invoke-WebRequest -UseBasicParsing -Uri ($Raw+$f.Name) -OutFile $p -TimeoutSec 120
    if ((Get-Item -LiteralPath $p).Length -ne $f.Bytes -or (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLowerInvariant() -cne $f.Hash) { throw 'STOP: handoff integrity' }
}
$QueuePath = Join-Path $Root '2026-09-20-longtask-queue.json'
$Queue = Get-Content -LiteralPath $QueuePath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Queue.base_commit -cne $Base -or $Queue.base_tree -cne $Tree -or $Queue.maximum_window_hours -ne 168) { throw 'STOP: task binding' }
Write-Output $Root
```

在此 Root 写入 started_at/deadline_at 和 TASK_STATE，截止时间不能随会话/重试滚动延后。上面新下载若暂时超时，只允许记录首次结果后，对同一静态 URL、同一匿名身份最多两次传输重试；每次独立保留元数据，最终完整哈希必须一致。哈希不符、权限/安全拒绝、身份问题不重试、不换路线。此项只授权静态材料传输重试，不授权模型调用、失败测试或业务操作重试。

## 3. 复用材料并建立独立源码工作区

先阅读 AGENTS.md、DELIVERY_PLAN.md 的当前覆盖规则；历史 Supabase/强制外部审查条款不能覆盖当前自审许可。正式交付前仍需适用门禁。此轮本地仅创建新分支/草稿 PR，不合并 main。

在同一会话执行。完整性校验是首次执行前的只读核验，不是重新暂存/重新跑主机查询。现有已知 Python 不可用时，只使用此前已记录的解释器绝对路径，不扫描或安装。

```powershell
$Prepared = Join-Path $env:TEMP 'pal-probe-preparation-a0f188d97fe646e684e4d3f2dbe878ca'
$VerifyTool = Join-Path $Prepared 'claude_probe_environment.py'
$Input = Join-Path $Prepared 'stage\input'
if ((Get-Item -LiteralPath $VerifyTool).Length -ne 18955 -or (Get-FileHash -Algorithm SHA256 -LiteralPath $VerifyTool).Hash.ToLowerInvariant() -cne '0cdc4ac365d6dcdd7ef2e45af19563ec86cda213ab12d7d1b5105c41f61458a6') { throw 'STOP: original verifier changed' }
Remove-Item -LiteralPath 'Env:PYLAUNCHER_ALLOW_INSTALL' -ErrorAction SilentlyContinue
$HostPy = (py -3.12 -I -S -B -c "import sys,struct;assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8;print(sys.executable)")
if ($LASTEXITCODE -ne 0 -or -not $HostPy) { throw 'STOP: recorded host Python unavailable' }
$HostPy = $HostPy.Trim()
& $HostPy -I -S -B $VerifyTool verify --root $Input
if ($LASTEXITCODE -ne 0) { throw 'STOP: prepared payload changed' }
$Manifest = Get-Content -LiteralPath (Join-Path $Input 'environment-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Manifest.source_tree -cne $Tree -or $Manifest.source_commit -cne '21d888a0b4db49d92e3d73293ca7a71ec1087783') { throw 'STOP: prepared source binding' }
$Runtime = Join-Path $Root 'runtime'
if (Test-Path -LiteralPath $Runtime) { throw 'STOP: runtime destination exists' }
Copy-Item -LiteralPath (Join-Path $Input 'python') -Destination $Runtime -Recurse
foreach ($f in $Manifest.files) {
    if ($f.path.StartsWith('python/')) {
        $p = Join-Path $Runtime $f.path.Substring(7)
        if ((Get-Item -LiteralPath $p).Length -ne $f.bytes -or (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLowerInvariant() -cne $f.sha256) { throw 'STOP: runtime copy mismatch' }
    }
}
$Py = Join-Path $Runtime 'python.exe'
$Site = Join-Path $Runtime 'Lib\site-packages'
$Src = Join-Path $Root 'source'
$env:GIT_TERMINAL_PROMPT = '0'
git -c credential.helper= -c core.autocrlf=false -c core.eol=lf clone --no-checkout --single-branch --branch main https://github.com/wmqfl861/polymarket-alpha-lab.git $Src
if ($LASTEXITCODE -ne 0) { throw 'STOP: source download failed; preserve attempt' }
git -C $Src -c core.autocrlf=false -c core.eol=lf checkout --detach $Base
if ($LASTEXITCODE -ne 0) { throw 'STOP: source checkout' }
$Actual = git -C $Src rev-parse 'HEAD^{tree}'
if ($LASTEXITCODE -ne 0 -or $Actual -cne $Tree) { throw 'STOP: source tree mismatch' }
$WorkBranch = 'work/longtask-20260920-' + [guid]::NewGuid().ToString('N')
git -C $Src switch -c $WorkBranch
if ($LASTEXITCODE -ne 0) { throw 'STOP: new branch' }
Write-Output 'WORKSPACE_READY_NOT_SOAK_STARTED'
```

这是下载/新工作区准备，不是许可更改既有安装。所有路径绑定到本次 Root；脚本拒绝非普通文件/链接和重解析点时不能放宽。即使正常网络可用，测试进程也不应接触外网。GitHub 获取/推送源码与工程测试分开，既有业务凭据不得传给测试子进程。

## 4. 首次针对性基线：四组各一次，不循环全量

使用任务 JSON 的 protocol/audit/process/paper 白名单。排除官方程序测试、原生数据库测试、旧 downloader/执行策略探针。用新环境变量和本批 TEMP/HOME 启动测试；不可沿用用户插件、PG/DSN、真实凭据或 PYTEST_ADDOPTS。以下代码没有安装依赖，仅指定复制运行时的已锁定依赖和新 checkout。

```powershell
$Bootstrap = @'
import os,sys
from pathlib import Path
src,site,out = (Path(x).resolve() for x in sys.argv[1:4])
args=sys.argv[4:]
root=os.environ.get('SystemRoot')
os.environ.clear()
os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
if root: os.environ['SystemRoot']=root
for name in ('TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA'):
    folder=out/name.lower();folder.mkdir(parents=True,exist_ok=False);os.environ[name]=str(folder)
sys.dont_write_bytecode=True
sys.path[:0]=[str(src/'src'),str(src),str(site)]
os.chdir(src)
import pytest,polymarket_alpha_lab
assert Path(pytest.__file__).resolve().is_relative_to(site)
assert Path(polymarket_alpha_lab.__file__).resolve().is_relative_to(src/'src')
raise SystemExit(pytest.main(args))
'@
foreach ($Name in @('protocol','audit','process','paper')) {
    $Out = Join-Path $Evidence ('baseline-' + $Name)
    if (Test-Path -LiteralPath $Out) { throw 'STOP: baseline attempt exists' }
    New-Item -ItemType Directory -Path $Out | Out-Null
    $Selected = @($Queue.baseline_test_groups.$Name)
    & $Py -I -S -B -c $Bootstrap $Src $Site $Out -q -p no:cacheprovider @Selected "--basetemp=$Out\pytest-temp" "--junitxml=$Out\result.xml" 2>&1 | Tee-Object -FilePath (Join-Path $Out 'result.log')
    $Code = $LASTEXITCODE
    if ($Code -ne 0) { throw ('BASELINE_FAILED_PRESERVE_AND_TRIAGE_' + $Name) }
}
```

此循环任一失败立即退出该轮基线，保留日志/XML。随后先分类，不默认整批退出：受影响模块转 LT-02 最小复现，其他独立分组可在记录后执行一次；安全问题则全停。不静默重试失败分组。以上引导命令由协调者静态审阅；不是已经在这台 Windows 机器执行成功的声明。

## 5. 连续开发任务，必须产出实际实现与证据

先完成 LT-00。接着 LT-01、LT-02、LT-03 按互不冲突的文件/工作区推进：

A. 协议和执行：设计有效/无效两类有种子反例，检查重复键、嵌套 Unicode、数值边界、缓存计数、停止/取钥/进程顺序、回执绑定、不完整状态和相同请求重放；回调全部虚构。复用现有测试夹具，先 RED 再最小修复，不新增适配器/大框架。

B. 结算和模拟：合成 BTC/ETH、Yes/No、边界时刻/时区、来源与原始哈希、Decimal 费用/滑点/损益对照。计算 oracle 不直接再调用被测函数。保留适用条件，不编造真实市场、人工结算或收益样本。

C. 长测驱动：优先复用现有 harness，必要时增加测试专用 driver，不新增业务账本。必须支持种子、候选 hash、每轮开始/终态、资源采样、STOP、Ctrl+C、非连续 segment、只清理自有成功临时目录，以及日志上限。为这些控制行为本身写反例测试。用项目既有进程所有权层，不复制全局杀进程逻辑。

驱动完成后先做 20 分钟预演，确认确实在跑、故障会留下证据、停止可回收自有进程。预演通过后冻结代码和依赖再启动正式长测，不让开发编辑穿透正在观察的候选。不要只输出计划或一句“已启动”就结束。

## 6. 正式多日观察与不中断工作规则

正式目标：同一候选连续 72 小时，至少 864 批、100,000 个不同生成输入。每 5 分钟启动一个定向场景批次，不每批重跑全套已有测试。主种子 2026092001，加唯一批次编号派生子种子。正常/异常/停止/并发/空闲后恢复均覆盖，重复执行数不当作独立新覆盖。

每分钟心跳；每 2 小时保存 TASK_STATE/PROGRESS；每 12 小时形成摘要。摘要内容为已做工作、代码 SHA、实际批次数、通过/失败/未知、资源趋势和下一任务，不只是“继续运行”。有其他安全工作就同时推进，管理员/官方程序支线 WAITING_OWNER 不阻断整个任务。

读取并执行计划中的资源/恢复细则：初始合成测试 worker 至多 2，默认本批工作集目标 2 GiB，新目录总量 4 GiB、日志/种子 512 MiB，目标卷剩余低于 10 GiB 暂停新轮；只监测自身进程。不可测则未测，不能保证硬上限。不要影响用户机器到不可用，不改变防休眠/电源策略来满足天数。

睡眠、重启、驱动退出或超过 15 分钟观察缺口中断连续性。保留 interrupted/unknown，不自动重跑原未完成轮；先确认本批旧实例退出，再开新 segment、新轮号。改了受测代码必须新建候选并重启有效观察窗口；不同版本/不同片段不能拼成“连续 72 小时通过”。总窗口不足就如实部分交付。

普通反例：保留首败、暂停相关候选，在独立开发区最小复现并修复；其他独立工作继续。安全异常、碰到旧数据/真实凭据/外网、清理不能确认、用户要求停：整批停止。不用同 SHA 重跑至绿、不删除失败、不放宽旧时限。最多保留 100 个不同最小复现，达到证据上限停写停测，不删首败腾空间。

持续运行要靠本地真正支持的持久进程/终端和测试驱动，不靠语言模型连续输出。必须记录实际启动 PID/启动标识、启动命令、首轮回执与 STOP 路径；不能保证会话不断线，也不创建计划任务/服务/自动登录。若执行器不支持持续进程，完成可做的开发和短测，明确“长测尚未启动”，不要伪称后台。上下文接近上限先写可接续状态；接续必须读原状态和 Git diff，不能另起失去历史的同名任务。

## 7. 分离自审和最终交付

开发完成后先冻结，再做单独只读自审：原始失败、修复 diff、测试清单/计数、种子复现、时间/费用前提、停止与资源清理、日志脱敏与未执行项。不能边改边声称已经审完。没有外部审核/CodeGraph 就直说未做，使用项目当前允许的同一 Agent 分离自审。

允许在本批新分支提交聚焦修改、通过现有获准 GitHub 通道创建至多一个新草稿 PR。不得 force push、合并 main、修改历史 PR。推送使用现有正常认证但不得读取/打印/复制凭据；拒绝则只挂起发布，继续本地可做工作。适用的最终源码 CI 必须针对最终 SHA，不能挪用旧绿标；未完成门禁保留 draft。不为了凑交付改代码，没有缺陷可交付覆盖与稳定性证据。

本轮可修改必要测试、上述既有模块中的可复现缺陷和 DELIVERY_PLAN.md 的对应状态追加；不得改依赖、SQL、真实客户端启用开关、交易权限或删历史断言。需要这些范围的变更先记 WAITING_OWNER，继续独立任务。管理员/官方 CLI 支线不能自行恢复执行。

最后返回：实际工作总时段与连续观察时段、最终 SHA/tree、代码/测试文件、缺陷与 RED→GREEN 对照、生成输入与重复次数分开统计、每组 pass/fail/skip/unknown、资源峰值和不可测项、睡眠/中断缺口、日志/种子哈希、草稿 PR、待审批项和下一唯一关键任务。不得上传整目录、个人配置、原始环境、厂商程序或真实敏感内容。

始终保持 official_cases_run=0、sandbox_started=false、activation_authorized=false；本批不是 G2—G6 或 V1 完成声明。最长 168 小时或用户停止/安全边界先到即停止；有意义目标完成可提前收尾，但 72 小时未实测就不能标通过。
