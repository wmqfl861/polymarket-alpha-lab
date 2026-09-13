# 本地 Agent 提示词：GitHub 补丁交付与已合并状态核对

## 本轮目的与当前状态

接续仓库 https://github.com/wmqfl861/polymarket-alpha-lab 。本轮只核对补丁交付、既有修复与规则保存，不扩展业务功能，也不重复已经完成的修复任务。

这份原始补丁已经通过 PR #14 合并：
- 实现提交：`d4caa7c3bc4e48f37f195290f47928d3e2cc9034`
- 合并提交：`2b20002c83ee33acb95fb2ca3de841f3e883ebbf`
- 实现代码树：`d6f6750b1068a3c75ab6249d5ca4f1ef7e010ee4`
- PR：https://github.com/wmqfl861/polymarket-alpha-lab/pull/14
- 已存在预发布：https://github.com/wmqfl861/polymarket-alpha-lab/releases/tag/v0.1.0-native-preview.1

旧 HANDOFF 中“尚未推送、待建 PR、待发布 Release”的描述是历史状态，不是当前任务。先检查最新仓库状态；若仍与上述状态一致，不重放补丁，不再创建相同 PR/tag/Release，不重跑已通过的全部验收来制造新结果。发现不同状态时先比较内容，不强推、回退或覆盖。

## 所有后续项目的交接规则

用户要求：凡交给本地 Agent 的补丁，先作为实际文件保存到对应 GitHub 仓库的可达提交中，再交付提示词。提示词必须含固定提交下载地址、下载命令、SHA256、基线、应用前检查、应用命令及验收要求。聊天附件和会过期的 Actions artifact 不得是唯一渠道。上传后需读回核验；上传失败须如实报告，不伪造链接、不绕过安全限制。能在仓库侧完成的工作先完成，仅将确实需要本地执行的任务交给本地 Agent。

仓库中的可复用规则：
https://github.com/wmqfl861/polymarket-alpha-lab/blob/0b06ba5387e58dab2b48a34d22459507b5ddf7a8/handoffs/AGENTS.md

用户希望规则跨项目生效。若你实际使用的本地 Agent 支持用户级全局规则，先依据该运行器的文档和已有配置确认正确位置，再将上述交接规则幂等追加到其已有全局规则文件；保留其他内容，重复存在则不再追加。不猜测路径，不覆盖现有规则，不改插件权限，不更改系统安全策略。回报实际规则路径及新增内容。不能确认支持方式时，报告“未安装全局规则”；不要声称已修改 ChatGPT 账号设置。仓库文件只保存规则内容，不自动改变其他仓库或 ChatGPT 账号级自定义指令。

## 一、从 GitHub 下载，不依赖聊天附件

交付分支（保留此分支，不用于实现开发）：`handoff/runtime-publication-20260913`
固定补丁交付提交：`0b06ba5387e58dab2b48a34d22459507b5ddf7a8`
目录：`handoffs/runtime-publication-20260913/`

补丁直链：
https://raw.githubusercontent.com/wmqfl861/polymarket-alpha-lab/0b06ba5387e58dab2b48a34d22459507b5ddf7a8/handoffs/runtime-publication-20260913/polymarket-runtime-publication-fix.patch

SHA256：`833be08c01d84729282a2826cfea486d3489ab878086526f81428e642945f6f8`
补丁大小：34,096 字节。

在普通用户 PowerShell 中下载到项目之外的新目录；复用已安装工具，不安装或升级全局工具：

```powershell
$ErrorActionPreference = 'Stop'
$Parent = 'C:\Albert\acceptance-assets'
if (-not (Test-Path -LiteralPath $Parent -PathType Container)) {
    throw '既有验收资源父目录不存在；请先核对路径。'
}
$Drop = Join-Path $Parent ('github-patch-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Drop -ErrorAction Stop | Out-Null
$Delivery = '0b06ba5387e58dab2b48a34d22459507b5ddf7a8'
$Raw = "https://raw.githubusercontent.com/wmqfl861/polymarket-alpha-lab/$Delivery/handoffs/runtime-publication-20260913"
$Name = 'polymarket-runtime-publication-fix.patch'
foreach ($File in @($Name, "$Name.sha256", 'manifest.json')) {
    Invoke-WebRequest -UseBasicParsing -Uri "$Raw/$File" -OutFile (Join-Path $Drop $File)
}
$Patch = Join-Path $Drop $Name
$Expected = '833be08c01d84729282a2826cfea486d3489ab878086526f81428e642945f6f8'
$Actual = (Get-FileHash -LiteralPath $Patch -Algorithm SHA256).Hash.ToLowerInvariant()
if ($Actual -ne $Expected -or (Get-Item -LiteralPath $Patch).Length -ne 34096) {
    throw '补丁字节数或 SHA256 不匹配，禁止应用。'
}
$Checksum = (Get-Content -LiteralPath (Join-Path $Drop "$Name.sha256") -Raw).Trim()
if ($Checksum -ne "$Expected  $Name") { throw '校验文件不匹配。' }
$Manifest = Get-Content -LiteralPath (Join-Path $Drop 'manifest.json') -Raw | ConvertFrom-Json
if ($Manifest.patch_sha256 -ne $Expected -or
    $Manifest.base_commit -ne '4d63b0877ba8a3c4d9aaca26bd2110770cf1ef3c' -or
    $Manifest.result_tree -ne 'd6f6750b1068a3c75ab6249d5ca4f1ef7e010ee4' -or
    $Manifest.merged_commit -ne '2b20002c83ee33acb95fb2ca3de841f3e883ebbf') {
    throw '补丁清单与固定交付信息不一致。'
}
Write-Output "补丁下载及校验通过：$Patch"
```

HTTP 失败、文件为空或哈希不匹配时停止。不要复制网页渲染后的文本代替原始补丁，不要修改校验值来适配错误文件。普通公开下载不需要读取凭据管理器中的原始 token。

## 二、默认路径：确认已合并，不重复应用

在选定的源码仓库确认没有未提交改动，并记录工作区路径和实际 HEAD；保护原验收目录、旧 kit、postgres.installing 和所有日志。只读检查不等于授权修改已有数据库。

用正常 Git 授权执行 `git fetch origin main`，检查退出码，然后执行：

```powershell
git merge-base --is-ancestor 2b20002c83ee33acb95fb2ca3de841f3e883ebbf origin/main
# 退出码 0：远端包含已合并修复；归档补丁，不 git apply。
# 退出码 1：不包含；先检查远端变化，不据此自动重放或回退。
# 其他退出码：Git 检查错误，停止。
```

另外查看 PR #14 和上述 Release。当前发布包预期 SHA256 为：
`c32647030e8d5cb2bab1ea3d6c93c6c253f80752390ea7240741f25b4022b705`。
已存在且匹配时只回报，不覆盖、重建或重复上传。使用发布包应按其 quickstart，在全新目录准备；不要覆盖已有初始化目录。

## 三、补丁使用方法：仅历史复现时执行

本段用于用户明确要求复现、或确需验证补丁可应用性的情况。不是默认任务；绝不对当前 main 再次应用。必须在全新 SOURCE checkout 的精确旧基线上执行，不能在交付分支或带 PROJECT-BUNDLE.json 的已验收 kit 中执行。

补丁基线：`4d63b0877ba8a3c4d9aaca26bd2110770cf1ef3c`
应用后预期树：`d6f6750b1068a3c75ab6249d5ca4f1ef7e010ee4`

在同一 PowerShell 会话中保留上一节已校验的 `$Patch`：

```powershell
$Replay = Join-Path 'C:\Albert\project' ('polymarket-patch-replay-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $Replay) { throw '复现目标必须是全新目录。' }
git clone --no-checkout https://github.com/wmqfl861/polymarket-alpha-lab.git $Replay
if ($LASTEXITCODE -ne 0) { throw 'clone 失败。' }
git -C $Replay config core.autocrlf false
if ($LASTEXITCODE -ne 0) { throw '本仓库换行配置失败。' }
git -C $Replay checkout --detach 4d63b0877ba8a3c4d9aaca26bd2110770cf1ef3c
if ($LASTEXITCODE -ne 0) { throw '基线 checkout 失败。' }
$Head = git -C $Replay rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $Head -ne '4d63b0877ba8a3c4d9aaca26bd2110770cf1ef3c') {
    throw '不是指定基线。'
}
$Dirty = git -C $Replay status --porcelain
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw '基线工作区不干净。' }
git -C $Replay apply --check --index $Patch
if ($LASTEXITCODE -ne 0) { throw '补丁预检失败，禁止强制应用。' }
git -C $Replay apply --index $Patch
if ($LASTEXITCODE -ne 0) { throw '应用失败。' }
$Tree = git -C $Replay write-tree
if ($LASTEXITCODE -ne 0 -or $Tree -ne 'd6f6750b1068a3c75ab6249d5ca4f1ef7e010ee4') {
    throw '应用后代码树不匹配。'
}
git -C $Replay diff --cached --check
if ($LASTEXITCODE -ne 0) { throw '差异格式校验失败。' }
Write-Output "历史复现通过；只保留复现工作区，不重复提交已合并修复：$Replay"
```

应用失败时不要用 --reject、--3way、--unsafe-paths、--ignore-whitespace 或 --whitespace=fix 改写结果；不 reset/clean，不把失败改报成功。档案分支新增交付文件，因此它的整体 tree 不等于上述实现结果 tree，这不是补丁错误。

## 四、真正出现新差异时的验收要求

已有 PR #14 包含专项、本机原生、三次原失败用例和三条 CI 的验收记录；先复核，不重复安排已完成任务。只有出现新的代码差异、缺失证据或用户明确要求重验时，才在新工作区执行：

```powershell
uv sync --locked --extra dev --extra postgres --python 3.12
.\.venv\Scripts\python.exe -m pytest -q tests/test_project_postgres_publication.py
.\.venv\Scripts\python.exe scripts/verify_local.py --full
```

Windows 原生验收复用 `C:\Albert\acceptance-assets\pg-prefix-17.11\pgsql`，建立本轮独立 RUNNER_TEMP，按 `.github/workflows/native-postgres.yml` 启用原生用例。确认两项真实目录句柄用例实际执行而非跳过。保留首次失败与单独重跑记录；不要关闭杀软、添加排除、放宽 ACL、提权或删除暂存目录来获得通过。新的源码修复才需要新的自审、PR、最终 head 三条 CI 和合并树核对；本轮归档核验本身不重复创建修复 PR。

## 五、回传

回传固定下载提交与 URL、补丁 SHA256 和大小、当前 main/PR #14/Release 状态、判定为已合并或需要另行处理的依据、本地用户级规则实际保存路径与内容（无法保存则明确未完成）。区分本轮实际执行的检查和仅引用既有记录。没有新失败就报告“归档核验完成，无需重复修复”。不得上传 .local、数据库、备份、passfile、token 或含敏感信息的日志。
