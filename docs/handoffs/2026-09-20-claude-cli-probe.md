# WP-02：固定 Claude 官方程序的隔离验收任务

本提示词是一次验收交接，不是源码开发、模型登录或真实研究任务。目标：在已有、无秘密的独立测试宿主中，使用已经核验发布者来源的 Claude Code 2.1.278 原生程序运行六个固定场景，返回第一批完整证据。

## 固定版本与已完成工作

仓库：`wmqfl861/polymarket-alpha-lab`。PR #65 已合并，合并提交：`96c706503902630a829a12421205e7e7ccfac2b3`。
源码保留分支：`feature/wp02-claude-cli-probe-20260920`。
必须检出源码提交 `b0bd04a246b664a0be4e79fe259a964d6ff32c3a`，完整树 `b33bfcd4e3a2e2ad20d77e581e5ca1136d9590dc`。
源码校验清单固定在提交 `54928b345558e98433dc82ff3d3e8a39b443677a` 的 `docs/handoffs/2026-09-20-claude-cli-probe.manifest.json`：1526 字节，SHA256 `60af794588687f39255307d201b4af96918989c086ff6eb8266e733a6e052d23`。

协调者已完成源码复查、合成进程测试、全量验证及 Windows 原生/安装包验收。首轮 Windows 文件身份读取与测试故障注入问题已修正，失败证据保留。不要重复全量测试、数据库、行情网络或旧安装包验收；不要执行旧 a64e 候选，不应用补丁，不改源码、断言、版本、限额或工作流。本任务不需要子 Agent，禁止 fast mode。

## 必须先具备的条件

只用操作者已批准的 Windows x64 一次性测试宿主。它必须不含真实 API 密钥、登录缓存、个人文件或业务数据库；不能复制用户 HOME、`.claude`、`.env`、`.local`、浏览器或数据库。必须已有来源可验证的 Claude 2.1.278 原生文件、Git、uv 0.10.0，以及可准备的锁定 Python 3.12 x64 环境。任一条件缺失，返回 BLOCKED 与缺失项，不扫描用户目录寻找替代品。

执行任何 Claude 命令之前，宿主既有隔离机制必须已阻断外部出站，允许本机 loopback；环境变量和临时目录不是隔离实现。本任务不创建虚拟机、不修改防火墙/ACL/执行策略、不解除现有网络限制，也不借用普通业务安装。官方二进制先前下载受限：不换镜像、包管理器、代理、CI或身份代取，不临时安装/升级/登录，不采纳订阅或 OAuth 凭据。只允许使用已经具备且经独立来源核验的文件。

准备源码/依赖与执行程序分开。准备阶段可使用宿主原已许可的网络，但绝不启动 Claude（包括 --version）。若外网已经禁用，不得解除；使用事先准备且满足相同版本/哈希校验的源码和依赖，否则 BLOCKED。

## A. 在新 PowerShell 5.1 -NoProfile 会话中准备源码

以下可执行片段全部为 ASCII。每个失败都立即停止，保留首次结果，不重复整段凑成功。

```powershell
$ErrorActionPreference = 'Stop'
$Source = 'b0bd04a246b664a0be4e79fe259a964d6ff32c3a'
$Tree = 'b33bfcd4e3a2e2ad20d77e581e5ca1136d9590dc'
$ManifestCommit = '54928b345558e98433dc82ff3d3e8a39b443677a'
$ManifestHash = '60af794588687f39255307d201b4af96918989c086ff6eb8266e733a6e052d23'
$Repo = 'https://github.com/wmqfl861/polymarket-alpha-lab.git'
$Ref = 'feature/wp02-claude-cli-probe-20260920'
$pr = Invoke-RestMethod -Uri 'https://api.github.com/repos/wmqfl861/polymarket-alpha-lab/pulls/65'
if (-not $pr.merged -or $pr.head.sha -cne $Source) { throw 'BLOCKED: fixed source not accepted' }
$RunRoot = Join-Path $env:TEMP ('pal-claude-probe-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $RunRoot) { throw 'BLOCKED: existing run root' }
New-Item -ItemType Directory -Path $RunRoot | Out-Null
$Src = Join-Path $RunRoot 'source'
$ManifestPath = Join-Path $RunRoot 'manifest.json'
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
    if ((Get-Item -LiteralPath $p).Length -ne $f.bytes) { throw 'BLOCKED: source size' }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLowerInvariant() -cne $f.sha256) { throw 'BLOCKED: source hash' }
}
Set-Location -LiteralPath $Src
$UvVersion = uv --version
if ($LASTEXITCODE -ne 0 -or $UvVersion -cnotmatch '^uv 0\.10\.0(?: \([^()\r\n]+\))?$') { throw 'BLOCKED: pinned uv unavailable' }
uv sync --locked --extra dev --python 3.12
if ($LASTEXITCODE -ne 0) { throw 'BLOCKED: locked environment unavailable' }
$Py = Join-Path $Src '.venv\Scripts\python.exe'
& $Py -c "import sys,struct;from pathlib import Path;import polymarket_alpha_lab as p;assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8;assert Path(p.__file__).resolve()==Path('src/polymarket_alpha_lab/__init__.py').resolve();print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw 'BLOCKED: Python or source origin' }
$Dirty = git -C $Src status --porcelain --untracked-files=all
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'BLOCKED: modified source' }
Write-Output ('Prepared only: ' + $RunRoot)
```

离线准备也必须核对相同 HEAD、tree、清单和 Python 来源，不自动接受其他 editable 安装。不重复运行 A 来刷新工作目录；A 完成后才进入隔离执行阶段。

## B. 外部出站已阻断后，执行唯一一批六场景

继续同一会话，保留 A 的变量。下面的路径、哈希和字节数由操作者根据已有发布者证据提供，不是密钥。不得把随意计算的文件哈希当作来源证明。

```powershell
if ((Read-Host 'Confirm disposable secret-free host, egress denied, loopback allowed: type ISOLATED') -cne 'ISOLATED') { throw 'BLOCKED: isolation prerequisite' }
$Image = Read-Host 'Existing publisher-verified Claude 2.1.278 native image absolute path'
$ImageHash = Read-Host 'Independently verified lowercase SHA256'
$ImageBytesText = Read-Host 'Independently recorded positive byte count'
if ($Image -cnotmatch '^[A-Za-z]:[\\/]') { throw 'BLOCKED: local absolute image path required' }
if ($ImageHash -cnotmatch '^[0-9a-f]{64}$' -or $ImageBytesText -cnotmatch '^[1-9][0-9]{0,8}$') { throw 'BLOCKED: image metadata' }
$ImageBytes = [long]$ImageBytesText
if ($ImageBytes -gt 536870912) { throw 'BLOCKED: image size limit' }
$item = Get-Item -LiteralPath $Image
if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.Length -ne $ImageBytes) { throw 'BLOCKED: image type or size' }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Image).Hash.ToLowerInvariant() -cne $ImageHash) { throw 'BLOCKED: image hash' }
$Dirty = git -C $Src status --porcelain --untracked-files=all
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'BLOCKED: source changed after preparation' }
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
if (-not (Test-Path -LiteralPath $Xml)) { throw 'BLOCKED: missing XML; preserve first log, no rerun' }
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
if ($Code -ne 0 -or $GitCode -ne 0 -or $Dirty -or $Cases.Count -ne 6 -or $Mismatch.Count -ne 0 -or $WrongClass -ne 0 -or $Bad -ne 0 -or $Properties.Count -ne 6) { throw 'BLOCKED: incomplete or failed fixed batch; preserve evidence, no retry' }
Write-Output 'COMPATIBILITY_SUBSET_PASSED_NOT_ACTIVATION_AUTHORIZATION'
```

`--basetemp` 会清理其目标目录，只能使用上面首次不存在的 `pytest-first-batch`；禁止指向项目根、整个 TEMP、已用测试目录或业务路径。六场景为同一批，允许记录每个预定场景的失败，不追加第二批，不挑成功样本上报。错误版本、超时、状态写入、额外请求、跳过、缺失 XML 或其他异常均保留失败/BLOCKED，不改限额、清空目录、删日志或换版本凑通过。

## 返回证据与解释边界

返回源码提交/tree、操作系统/Python版本、独立宿主与发布者来源核验结论、原生文件SHA256/准确字节数、退出码、六场景完整状态、日志/XML大小及SHA256，和六条 `CLAUDE_CLI_PROBE` 元数据。未能到达观察阶段时，返回首条经过脱敏审阅的错误及未执行项。

原生程序、整个测试根、配置/HOME、状态文件正文、原始请求/响应、真实凭据均不得上传。日志/XML须先审阅，必要时仅返回哈希和脱敏摘要；禁止为了上报而扩大读取范围。

每个场景最多两次明确操作：版本查询、一个合成提示；模拟服务只在127.0.0.1，使用代码内的非凭据测试字符串。生产HTTPS限制未放宽，测试HTTP覆盖不证明TLS。快照只观察测试根内存留状态，不能证明外部路径、瞬时写入后删除、全部网络活动、真实模型身份或费用。即使6项通过，`activation_authorized=false`，G2仍未关闭；本地Agent不得自行批准真实研究。
