# WP-02 / WP-06：主机能力查询与离线准备（替代旧 A/B 前置要求）

你是本地 Agent。本次只完成「只读主机能力查询＋新目录离线暂存」，不运行官方 Claude、不运行原六场景、不启动 Windows Sandbox，也不启用系统功能。旧回执为 A/B 均未执行、6/6 未执行；保留原结论，不把 B9G 标题当成其他任务已完成。

## 1. 本次可以在现有主力机做什么

允许在用户现有工作机上：下载下面已发布的项目文件，校验并阅读两个脚本，进行脚本限定的注册表/CIM只读查询，使用已经具备的 Python 3.12 将离线包暂存到一个新目录。这不把主力机重新认定为无秘密宿主。

**不要求先有干净虚拟机、不要求先提供 Claude 文件和哈希、不要求 uv 0.10.0。** 保持用户全局 uv 0.12.13 不变，本任务不调用 uv、pip、Git或安装器。缺少官方 CLI、Sandbox 未启用或查询结果未知，不是拒绝下面准备操作的理由。

禁止扫描 HOME/Downloads/凭据目录或寻找 CLI；不访问/停止/备份/迁移业务数据库，不复制 .local、.claude、.zcode、.env或登录缓存。尤其不修改或清理以下既有路径：

```text
C:\Albert\project\polymarket-alpha-lab-acceptance-4d63b08
C:\Albert\project\polymarket-alpha-lab-kit-startup-4d63b08
C:\Albert\acceptance-assets\runner-temp
```

不重复全量、数据库、行情网络或旧安装包验收，不修改源码、断言、版本、限额或工作流，不创建PR。本次不需要子Agent，禁止fast mode。不运行任何Claude/Codex命令，包括--version；不采用任何替代下载途径取得此前受限的官方程序。

本任务不提权，不改防火墙、ACL、执行策略，不用ExecutionPolicy Bypass、Unblock-File或其他方法绕过策略。能力查询失败保留为未知；不要为获得答案再调用提权接口。系统功能启用、重启和实际来宾启动另需用户明确批准。

## 2. 已完成工作与固定交付

PR #66 已合并：`199f5a4f629466467f638d7f4a383e4bead19120`。
脚本固定源码：`faa915887dff4222b9457087cc6d532ee8bd61b6`，保留分支`feature/wp02-probe-preparation-20260920`。
完整源码树：`5202e9a8422d99922eb60725b1384f48299d2257`。

协调者已完成源码分离自审、本地/托管完整验证、53项Windows准备测试及两份实际搬移环境烟测。不要让本地重复这些工程门禁。它们不证明用户机器的Sandbox实际可用。

已发布的准备专用Release：
`https://github.com/wmqfl861/polymarket-alpha-lab/releases/tag/probe-environment-20260920-faa91588`

离线ZIP是原始已测试字节，没有重打包，包含Python3.12.12、锁定依赖、源码和许可；不含官方CLI、用户凭据或业务库。内部source_commit保留CI测试合并`21d888a0b4db49d92e3d73293ca7a71ec1087783`，与上述脚本提交及正式合并是同一代码树，不要改写清单。

| 文件 | 字节数 | SHA256 |
| --- | ---: | --- |
| inspect_claude_probe_host.ps1 | 2929 | 5e8b827bba6a8b7e93f701b410d9a22ed983a019c135316c2eaaef1492c504b2 |
| claude_probe_environment.py | 18955 | 0cdc4ac365d6dcdd7ef2e45af19563ec86cda213ab12d7d1b5105c41f61458a6 |
| claude-probe-environment.zip | 35497151 | ac1ff95623ce9f20d7a2a6b876554242b277066c952ed9d62bda3b4747fddb57 |

## 3. 下载与校验：先不执行下载的脚本

在新的、非管理员PowerShell 5.1 `-NoProfile`会话中执行。只在本轮新建目录写入文件，不使用原项目目录。一次失败保留原目录和证据，不反复新建目录凑成功。网络被拒绝时停在该下载项，不换代理、镜像或身份。

```powershell
$ErrorActionPreference = 'Stop'
$Source = 'faa915887dff4222b9457087cc6d532ee8bd61b6'
$ExpectedTree = '5202e9a8422d99922eb60725b1384f48299d2257'
$BundleCommit = '21d888a0b4db49d92e3d73293ca7a71ec1087783'
$Raw = 'https://raw.githubusercontent.com/wmqfl861/polymarket-alpha-lab/' + $Source + '/scripts/'
$Release = 'https://github.com/wmqfl861/polymarket-alpha-lab/releases/download/probe-environment-20260920-faa91588/'
$Prep = Join-Path $env:TEMP ('pal-probe-preparation-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $Prep) { throw 'STOP: existing attempt directory' }
New-Item -ItemType Directory -Path $Prep | Out-Null
$Utf8 = New-Object System.Text.UTF8Encoding($false)
$Files = @(
    [pscustomobject]@{ Name='inspect_claude_probe_host.ps1'; Url=($Raw+'inspect_claude_probe_host.ps1'); Bytes=2929; Hash='5e8b827bba6a8b7e93f701b410d9a22ed983a019c135316c2eaaef1492c504b2' },
    [pscustomobject]@{ Name='claude_probe_environment.py'; Url=($Raw+'claude_probe_environment.py'); Bytes=18955; Hash='0cdc4ac365d6dcdd7ef2e45af19563ec86cda213ab12d7d1b5105c41f61458a6' },
    [pscustomobject]@{ Name='claude-probe-environment.zip'; Url=($Release+'claude-probe-environment.zip'); Bytes=35497151; Hash='ac1ff95623ce9f20d7a2a6b876554242b277066c952ed9d62bda3b4747fddb57' }
)
function Assert-PinnedFiles {
    foreach ($f in $Files) {
        $p = Join-Path $Prep $f.Name
        $item = Get-Item -LiteralPath $p
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.Length -ne $f.Bytes) { throw 'STOP: file type or size mismatch' }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLowerInvariant() -cne $f.Hash) { throw 'STOP: file hash mismatch' }
    }
}
foreach ($f in $Files) {
    $p = Join-Path $Prep $f.Name
    if (Test-Path -LiteralPath $p) { throw 'STOP: download target exists' }
    Invoke-WebRequest -UseBasicParsing -Uri $f.Url -OutFile $p -TimeoutSec 120
}
Assert-PinnedFiles
$Inspect = Join-Path $Prep 'inspect_claude_probe_host.ps1'
$Tool = Join-Path $Prep 'claude_probe_environment.py'
$Zip = Join-Path $Prep 'claude-probe-environment.zip'
Write-Output 'DOWNLOADS_VERIFIED_NOT_EXECUTED'
Write-Output $Prep
```

阅读两个已校验脚本，确认只执行本任务允许的功能；不要执行build/selftest子命令。下载与阅读本身不意味着允许启动供应商程序或Sandbox。校验不符立即停止，不执行任何下载文件。

## 4. 只读能力查询，随后暂存，不启动来宾

保留同一会话变量。查询只涉及指定Windows注册表/CIM项，不输出主机名、用户名、完整环境或软件清单。若脚本被执行策略拒绝，记录`failed_or_denied`，不绕过；独立的Python暂存仍可继续。若CIM某项读取被拒绝，保留脚本的unknown/reads_failed，不提权或扫描替代信息。

```powershell
Assert-PinnedFiles
$HostFacts = $null
$QueryExit = $null
$QueryStatus = 'failed_or_denied'
try {
    $PS = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $Observed = @(& $PS -NoProfile -NonInteractive -File $Inspect 2>&1)
    $QueryExit = $LASTEXITCODE
    if ($QueryExit -ne 0) { throw 'query failed' }
    $HostFacts = ($Observed -join "`n") | ConvertFrom-Json
    if ($HostFacts.schema -cne 'claude-probe-host-preparation-v1' -or $HostFacts.vendor_executed -ne $false -or $HostFacts.feature_changed -ne $false -or $HostFacts.global_tools_changed -ne $false -or $HostFacts.activation_authorized -ne $false) { throw 'unexpected query receipt' }
    $QueryStatus = 'reported'
} catch {
    $HostFacts = $null
}
$Query = [ordered]@{ status=$QueryStatus; exit_code=$QueryExit; facts=$HostFacts }
[IO.File]::WriteAllText((Join-Path $Prep 'host-query.json'), ($Query | ConvertTo-Json -Depth 6), $Utf8)
$Query | ConvertTo-Json -Depth 6

# Select only the already installed Python 3.12. No install-on-demand.
Remove-Item -LiteralPath 'Env:PYLAUNCHER_ALLOW_INSTALL' -ErrorAction SilentlyContinue
$Python = (py -3.12 -I -S -B -c "import sys,struct;assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8;print(sys.executable)")
if ($LASTEXITCODE -ne 0 -or -not $Python) { throw 'STOP: existing Python 3.12 x64 unavailable; keep host-query result' }
$Python = $Python.Trim()
Assert-PinnedFiles
$Stage = Join-Path $Prep 'stage'
if (Test-Path -LiteralPath $Stage) { throw 'STOP: stage already exists; do not overwrite or retry' }
$StageLines = @(& $Python -I -S -B $Tool stage --archive $Zip --sha256 $Files[2].Hash --destination $Stage)
$StageExit = $LASTEXITCODE
[IO.File]::WriteAllText((Join-Path $Prep 'stage.json'), ($StageLines -join "`n"), $Utf8)
if ($StageExit -ne 0) { throw 'STOP: staging failed; preserve first result and partial files' }
$Staged = ($StageLines -join "`n") | ConvertFrom-Json
if ($Staged.status -cne 'staged_not_launched' -or $Staged.source_commit -cne $BundleCommit -or $Staged.official_cli_included -ne $false -or $Staged.activation_authorized -ne $false) { throw 'STOP: unexpected staging receipt' }
$Manifest = Get-Content -LiteralPath (Join-Path $Stage 'input\environment-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Manifest.source_commit -cne $BundleCommit -or $Manifest.source_tree -cne $ExpectedTree) { throw 'STOP: staged source binding' }
$Wsb = Join-Path $Stage 'preparation-smoke.wsb'
$WsbHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Wsb).Hash.ToLowerInvariant()
if (@(Get-ChildItem -LiteralPath (Join-Path $Stage 'output') -Force).Count -ne 0) { throw 'STOP: output directory is not empty' }
[ordered]@{
    status='PREPARATION_STAGED_NOT_LAUNCHED'
    host_query_status=$QueryStatus
    stage_exit=$StageExit
    source_tree=$ExpectedTree
    wsb_sha256=$WsbHash
    official_cases_run=0
    sandbox_started=$false
    activation_authorized=$false
} | ConvertTo-Json -Depth 4
```

如果没有`py`启动器，但既往记录已经明确给出本机Python3.12.10的绝对路径，可以直接使用该已知解释器并做相同版本/位数检查；不得扫描磁盘、下载解释器或改变全局安装。解释器确实不可用时保留能力查询结果，只把暂存列为阻断，不谎称两项都未做。

`.wsb`内仅允许本轮`stage/input`只读映射、`stage/output`独立可写映射；网络、剪贴板、音视频、打印和vGPU均为Disable，ProtectedClient为Enable。只阅读核对，不双击、不Start-Process、不改映射。

## 5. 必须在哪里停止

**本次到暂存和回报为止，即使Sandbox已启用也不启动。**

- feature为enabled：回报已准备，可进入下一次明确批准的无供应商Sandbox烟测；不要把功能存在当成实际隔离已验证。
- feature为disabled：回报需用户/管理员决定是否启用`Containers-DisposableClientVM`以及是否重启；本次不执行Enable-WindowsOptionalFeature/DISM。
- edition显示不支持或feature为absent/unknown、读取拒绝：原样回报具体值和reads_failed，列出需要核实的系统支持/管理员权限/虚拟化条件；不猜测、不安装替代组件。
- 固件虚拟化为false：列为后续人工固件设置/重启决定；本次不改BIOS或系统设置。

这不是重新要求用户先提供一台已准备的机器，而是先得到本机具体能力和已经就绪的离线材料，再明确必要的本地审批动作。全局uv保持不动，官方CLI文件缺失不阻止本次工作。

## 6. 返回证据

返回本次任务的三个独立状态：文件下载/校验、能力查询、暂存。提供两个脚本与ZIP实际字节数/SHA256、host-query.json内容、stage.json内容、生成.wsb的SHA256、原样查询值与下一项具体人工决定。目录只说明是本轮新建且未覆盖旧路径，必要时对用户名脱敏。

若出现失败，返回第一条经过脱敏的错误、实际退出码（未运行则无）、已完成项和未执行项，保留原下载/暂存目录，不反复下载、重新暂存、删目录或修改文件凑成功。不要上传整个离线包、目录树、主机个人配置、原始错误堆栈或任何凭据。

本次official_cases_run始终为0，sandbox_started为false，activation_authorized为false；不产生原六场景pytest/XML，也不伪造它们。主机/来宾边界、官方CLI状态写入/网络/身份以及G2-G6仍需后续真实验收，不能由本次准备宣布完成。
