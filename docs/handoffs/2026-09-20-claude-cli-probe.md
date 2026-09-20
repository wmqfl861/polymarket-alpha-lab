# WP-02：固定 Claude 官方二进制验收提示词

你接手的是一次只读源码、无真实业务数据的验收任务，不是继续开发或运行真实研究。
目标：用已经独立核验发布者来源的 Claude Code 2.1.278 原生程序，执行固定六场景，
返回协议、请求次数及测试根目录内存留状态的证据；不得宣称通过后已获准真实研究。

本提示词替代旧版未通过 Windows 验收的 a64e 候选交接；旧提交只作失败历史，不执行。

## 已完成，不要重做

PR #64 的适配器和原调用审计已经交付。本次 PR #65 的测试装置由协调者完成源码自审、合成程序和云端工程验证后交付；
下面命令仍要求先确认该固定版本已经合并。不要重新跑全量、数据库、市场网络、安装包或旧下载检查；
不要改源码、迁移、权限、超时、CLI版本、测试断言或测试选择，也不要创建或合并 PR。
不启用 fast mode；本任务不需要再创建子 Agent。

本任务源码固定为仓库 `wmqfl861/polymarket-alpha-lab`：
- 保留分支：`feature/wp02-claude-cli-probe-20260920`
- 源码提交：`b0bd04a246b664a0be4e79fe259a964d6ff32c3a`
- 完整树：`b33bfcd4e3a2e2ad20d77e581e5ca1136d9590dc`
- 校验清单提交：`54928b345558e98433dc82ff3d3e8a39b443677a`
- 清单路径：`docs/handoffs/2026-09-20-claude-cli-probe.manifest.json`
- 清单：1526 字节，SHA256 `60af794588687f39255307d201b4af96918989c086ff6eb8266e733a6e052d23`

## 必须先具备的条件

只在操作者已有的、获准用于此任务的 Windows x64 独立一次性测试宿主中执行。
宿主不能含真实密钥、登录状态、个人文件或业务数据库；不要复制用户 HOME、`.claude`、
`.env`、`.local`、数据库、备份或浏览器配置。必须已经有来源可验证的 2.1.278 原生文件、
Git、uv 0.10.0 和可用的 Python 3.12 测试环境／依赖。执行 Claude 前，外部出站必须
由宿主现有隔离机制阻断，且仅允许测试所需的本机 loopback；不是把环境变量设成1就有隔离。

任一条件缺失，直接返回 BLOCKED 和缺失项；不猜测、不扫描凭据或用户目录。
尤其不得为补齐此任务去绕过先前受限的官方二进制下载：不换镜像、软件包、CI代下载、
代理或身份，不临时安装、升级、登录或采纳订阅凭据。不要关闭防火墙、放宽 ACL 或执行策略。
不得在日常业务安装中凑环境。下面命令不创建隔离；是否具备隔离须操作者明确确认。

源码／依赖准备与 Claude 执行分开。准备阶段不运行任何 Claude 命令（包括 --version）。
准备可以使用测试宿主已有的获准网络策略；若宿主已禁外网，不得临时解除：仅使用事先
校验并准备好的相同源码和锁定环境，否则 BLOCKED。后面的执行阶段不需要外网。

## A. 下载、校验并准备源码（PowerShell 5.1；此时不启动 Claude）

在全新的 `powershell.exe -NoProfile` 会话中执行。不要运行文档之外的安装脚本。
下面代码为 ASCII。任何命令失败立即停止；不得重复运行整段以掩盖第一次失败。

```powershell
$ErrorActionPreference = 'Stop'
$Source = 'b0bd04a246b664a0be4e79fe259a964d6ff32c3a'
$Tree = 'b33bfcd4e3a2e2ad20d77e581e5ca1136d9590dc'
$ManifestCommit = '54928b345558e98433dc82ff3d3e8a39b443677a'
$ManifestHash = '60af794588687f39255307d201b4af96918989c086ff6eb8266e733a6e052d23'
$Repo = 'https://github.com/wmqfl861/polymarket-alpha-lab.git'
$Ref = 'feature/wp02-claude-cli-probe-20260920'
$RunRoot = Join-Path $env:TEMP ('pal-claude-probe-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $RunRoot) { throw 'BLOCKED: existing run root' }
New-Item -ItemType Directory -Path $RunRoot | Out-Null
$Src = Join-Path $RunRoot 'source'
$ManifestPath = Join-Path $RunRoot 'manifest.json'
$pr = Invoke-RestMethod -Uri 'https://api.github.com/repos/wmqfl861/polymarket-alpha-lab/pulls/65'
if (-not $pr.merged -or $pr.head.sha -cne $Source) { throw 'BLOCKED: source PR not accepted' }
$env:GIT_TERMINAL_PROMPT = '0'
git -c credential.helper= -c core.autocrlf=false clone --no-checkout --single-branch --branch $Ref $Repo $Src
if ($LASTEXITCODE -ne 0) { throw 'BLOCKED: clone failed' }
git -C $Src -c core.autocrlf=false checkout --detach $Source
if ($LASTEXITCODE -ne 0) { throw 'BLOCKED: checkout failed' }
$ActualHead = git -C $Src rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $ActualHead -cne $Source) { throw 'BLOCKED: wrong commit' }
$ActualTree = git -C $Src rev-parse 'HEAD^{tree}'
if ($LASTEXITCODE -ne 0 -or $ActualTree -cne $Tree) { throw 'BLOCKED: wrong tree' }
$ManifestUrl = 'https://raw.githubusercontent.com/wmqfl861/polymarket-alpha-lab/' + $ManifestCommit + '/docs/handoffs/2026-09-20-claude-cli-probe.manifest.json'
Invoke-WebRequest -UseBasicParsing -Uri $ManifestUrl -OutFile $ManifestPath
if ((Get-Item -LiteralPath $ManifestPath).Length -ne 1526) { throw 'BLOCKED: manifest size' }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $ManifestPath).Hash.ToLowerInvariant() -cne $ManifestHash) { throw 'BLOCKED: manifest hash' }
$m = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($m.source_commit -cne $Source -or $m.source_tree -cne $Tree) { throw 'BLOCKED: manifest binding' }
foreach ($f in $m.files) {
    $p = Join-Path $Src $f.path
    if ((Get-Item -LiteralPath $p).Length -ne $f.bytes) { throw 'BLOCKED: source file size' }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLowerInvariant() -cne $f.sha256) { throw 'BLOCKED: source file hash' }
}
Set-Location -LiteralPath $Src
$UvVersion = uv --version
if ($LASTEXITCODE -ne 0 -or $UvVersion -cne 'uv 0.10.0') { throw 'BLOCKED: pinned uv unavailable' }
uv sync --locked --extra dev --python 3.12
if ($LASTEXITCODE -ne 0) { throw 'BLOCKED: locked environment unavailable' }
$Py = Join-Path $Src '.venv\Scripts\python.exe'
& $Py -c "import sys, struct; from pathlib import Path; import polymarket_alpha_lab as p; assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8; assert Path(p.__file__).resolve()==Path('src/polymarket_alpha_lab/__init__.py').resolve(); print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw 'BLOCKED: Python or source origin' }
$Dirty = git -C $Src status --porcelain --untracked-files=all
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'BLOCKED: source not clean' }
Write-Output ('Prepared only: ' + $RunRoot)
```

源码已经合并，不应用任何历史补丁。此阶段只准备源码与锁定依赖，不验收官方程序。
若用事先准备的相同源码替代在线准备，仍须完成相同 HEAD、tree、清单和源码来源校验，
不自动接受另一个 editable 安装。Git下载／依赖安装失败不是允许放宽宿主隔离的理由。

## B. 外网已阻断后，执行一次固定六场景

由操作者确认是上述秘密为空的隔离宿主后，继续同一 PowerShell 会话（保留 A 的变量）。
若必须重开会话，只恢复该次新建 RunRoot/Src/Py 和固定 H/T；不要重跑 A 或复用已执行的
pytest base temp。不得为此关闭安全策略。提供既有官方二进制的绝对路径，以及独立记录的
SHA256 和准确字节数；不要用对任意文件临时算出的哈希冒充发布者来源证明。

```powershell
if ((Read-Host 'Confirm disposable secret-free host, external egress denied, loopback allowed: type ISOLATED') -cne 'ISOLATED') { throw 'BLOCKED: isolation prerequisite' }
$Image = Read-Host 'Existing publisher-verified Claude 2.1.278 native image absolute path'
$ImageHash = Read-Host 'Independently verified lowercase SHA256'
$ImageBytesText = Read-Host 'Independently recorded exact positive byte count'
if ($Image -cnotmatch '^[A-Za-z]:[\\/]') { throw 'BLOCKED: local absolute image path required' }
if ($ImageHash -cnotmatch '^[0-9a-f]{64}$' -or $ImageBytesText -cnotmatch '^[1-9][0-9]{0,8}$') { throw 'BLOCKED: image metadata' }
$ImageBytes = [long]$ImageBytesText
if ($ImageBytes -gt 536870912) { throw 'BLOCKED: image size limit' }
$item = Get-Item -LiteralPath $Image
if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.Length -ne $ImageBytes) { throw 'BLOCKED: image type or size' }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Image).Hash.ToLowerInvariant() -cne $ImageHash) { throw 'BLOCKED: image hash' }
$Proof = Join-Path $RunRoot 'proof'
$BaseTemp = Join-Path $RunRoot 'pytest-first-batch'
if ((Test-Path -LiteralPath $Proof) -or (Test-Path -LiteralPath $BaseTemp)) { throw 'BLOCKED: batch already attempted' }
New-Item -ItemType Directory -Path $Proof | Out-Null
$Log = Join-Path $Proof 'claude-probe.log'
$Xml = Join-Path $Proof 'claude-probe.xml'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:PYTHONUTF8 = '1'
foreach ($name in @('PYTEST_ADDOPTS','PYTEST_PLUGINS','PYTHONPATH','PYTHONHOME')) {
    Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue
}
$Keys = @('POLYMARKET_ALPHA_LAB_CLAUDE_PROBE','POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_IMAGE','POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_SHA256','POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_BYTES','POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_ISOLATED_HOST')
$env:POLYMARKET_ALPHA_LAB_CLAUDE_PROBE = '1'
$env:POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_IMAGE = $Image
$env:POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_SHA256 = $ImageHash
$env:POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_BYTES = $ImageBytesText
$env:POLYMARKET_ALPHA_LAB_CLAUDE_PROBE_ISOLATED_HOST = '1'
$Code = $null
try {
    & $Py -u -m pytest -q -s --tb=short -o junit_family=legacy tests/test_research_claude_profile_native.py "--junitxml=$Xml" "--basetemp=$BaseTemp" 2>&1 | Tee-Object -FilePath $Log
    $Code = $LASTEXITCODE
} finally {
    foreach ($name in $Keys) { Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue }
}
$Dirty = git -C $Src status --porcelain --untracked-files=all
$GitCode = $LASTEXITCODE
foreach ($p in @($Log,$Xml)) {
    if (Test-Path -LiteralPath $p) {
        Write-Output ((Get-Item -LiteralPath $p).Name + ' bytes=' + (Get-Item -LiteralPath $p).Length + ' sha256=' + (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLowerInvariant())
    }
}
Write-Output ('pytest_exit=' + $Code)
if (-not (Test-Path -LiteralPath $Xml)) { throw 'BLOCKED: no complete XML; preserve first log, do not rerun' }
$Report = New-Object System.Xml.XmlDocument
$Report.XmlResolver = $null
$Report.Load($Xml)
$Cases = @($Report.SelectNodes('//testcase'))
$Modes = @('success','rate_limit','server_error','invalid_action','tool_use','truncated')
$Expected = @($Modes | ForEach-Object { 'test_supplied_claude_profile_loopback_and_surviving_state[' + $_ + ']' })
$Names = @($Cases | ForEach-Object { $_.GetAttribute('name') })
$Mismatch = @(Compare-Object ($Expected | Sort-Object) ($Names | Sort-Object))
$WrongClass = @($Cases | Where-Object { $_.GetAttribute('classname') -cne 'tests.test_research_claude_profile_native' }).Count
$Bad = @($Report.SelectNodes('//testcase/failure | //testcase/error | //testcase/skipped')).Count
$Properties = @($Report.SelectNodes('//testcase/properties/property[@name="claude_cli_probe"]'))
if ($Code -ne 0 -or $GitCode -ne 0 -or $Dirty -or $Cases.Count -ne 6 -or $Mismatch.Count -ne 0 -or $WrongClass -ne 0 -or $Bad -ne 0 -or $Properties.Count -ne 6) { throw 'BLOCKED: fixed batch did not fully pass; preserve evidence, no retry' }
Write-Output 'COMPATIBILITY_SUBSET_PASSED_NOT_ACTIVATION_AUTHORIZATION'
```

`--basetemp` 必须是上面首次不存在的目录，因为 pytest 会清理它指向的目录。
不要修改为项目根、用户 TEMP 总目录、现有测试目录或任何业务路径。
六场景组成一次完整批次；默认会继续记录各场景失败，不追加第二批、不挑成功项上报。
任何异常、缺失XML、跳过、超时、状态文件、权限拒绝或错误版本都保留为失败／BLOCKED。
不要清空测试根、删除日志、换版本、增大限额、移除状态检查或修改返回格式来凑通过。

## 验收口径与必须返回的证据

返回源码提交／树、清单提交、操作系统和 Python 版本、官方程序来源核验结论、
原文件SHA256／字节数、pytest退出码、六场景各自状态和完整分母、日志及XML的字节数和SHA256。
返回六条 `CLAUDE_CLI_PROBE` 元数据，或在未到达观察阶段时返回第一条经过审阅的错误。
不要上传原生文件、整份测试根、配置、HOME、状态文件正文、真实凭据、请求／响应原文。
日志/XML只能在检查确实不含真实私密数据后提供；必要时仅返回其哈希和脱敏摘要。

每场景最多两次明确操作：版本查询和一个合成研究请求；服务只在127.0.0.1，使用
代码内明确标记的假密钥。生产HTTPS配置没有放宽；测试HTTP覆盖不证明真实TLS。
状态检查只覆盖测试根内存留文件，不证明外部路径、瞬时创建后删除、其他网络活动、
真实模型身份或实际费用。即使六项通过，activation_authorized仍为false，G2仍未关闭。
若出现新的状态文件或额外请求，保留第一份负面证据，由协调者另行审阅；本地Agent不得
自行批准真实运行。此前源码/合成CI通过不替代这次官方程序的真实结果。
