# PR #15 本机只读接入检查：结算核验清单

## 当前任务，不要重复旧工作

仓库：`https://github.com/wmqfl861/polymarket-alpha-lab`

本轮代码已通过 PR #15 合并，无待应用补丁：
- 固定实现提交：`6ab3bd7d3b836dbc6ba128a5c1008ae84f44be8c`
- 固定实现代码树：`9e35a2a701c2dccc261c38d1288d511c15439313`
- 开发头：`acb4ea2f13e8d66a5e4d9924991e81f912084b7b`
- PR：`https://github.com/wmqfl861/polymarket-alpha-lab/pull/15`
- 新增入口：`scripts/review_resolution_queue.py`
- 说明：`docs/research-resolution-queue.md`

本轮只检查新入口能否使用本机已经验收的项目私有实例，并返回实际工作清单的聚合数量。云端已完成 81 项新专项、36200/11 全仓验证、445/2 Windows 原生验收及 95/0 分发包验收。不要重复整套原生/全仓/备份恢复验收，不重做 PR #14，不重复追加已保存的用户级交接规则，也不覆盖既有 Release。

这是已合并源码的接入检查，不是补丁实施。代码直接从固定 GitHub 提交获取；禁止对当前 main、交接分支或旧 kit 执行 git apply。交接文件应从 ChatGPT 消息给出的固定 GitHub 提交下载，并按同时给出的 SHA256 校验。本文件所在交接分支只保存说明，不作为实现基线。

## 安全与保全范围

已验收的数据库所在项目根目录：
`C:\Albert\project\polymarket-alpha-lab-kit-startup-4d63b08`

保留此目录，以及下列原始验收与日志目录，不覆盖、搬移、删除或重建：
- `C:\Albert\project\polymarket-alpha-lab-acceptance-4d63b08`
- `C:\Albert\acceptance-assets\runner-temp`
- 任何此前失败时遗留的 `postgres.installing` 目录。

不要把新 kit 覆盖解压到旧项目；不要复制 `.local` 来迁移实例；不要修改旧 kit 的 `PROJECT-BUNDLE.json`。使用新的源码 checkout 和它自己的虚拟环境，通过 `--root` 显式指定原数据库根目录。这个选项不会改变实例的路径身份绑定。

本轮不执行 init/migrate/restore/backup/up/down，不启动定时任务，不加 --collect 或 --allow-public-fetch，不创建市场或模拟预测，不确认结算，不调用付费模型或读取模型密钥。只允许管理器为只读会话按既有机制短暂启动/关闭本项目实例；数据库日志/WAL等运行文件可能正常变化，但不得新增或修改业务记录。

复用现有 uv 和 Python。允许在新工作区安装锁定的项目依赖，不更新 uv.lock，不安装/升级全局工具，不部署 Docker/Supabase/系统数据库服务，不添加杀软排除、不放宽 ACL、不提权。

## 一、获取固定源码

普通用户 PowerShell 中执行。每个外部命令都检查退出码；失败即停止。

```powershell
$ErrorActionPreference = 'Stop'
$Repo = 'https://github.com/wmqfl861/polymarket-alpha-lab.git'
$Commit = '6ab3bd7d3b836dbc6ba128a5c1008ae84f44be8c'
$ExpectedTree = '9e35a2a701c2dccc261c38d1288d511c15439313'
$Parent = 'C:\Albert\project'
if (-not (Test-Path -LiteralPath $Parent -PathType Container)) {
    throw '原项目父目录不存在，先核对路径，勿猜测替代位置。'
}
$Work = Join-Path $Parent ('polymarket-resolution-readback-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $Work) { throw '源码验收路径必须是全新的。' }
git clone --no-checkout $Repo $Work
if ($LASTEXITCODE -ne 0) { throw 'clone 失败。' }
git -C $Work config core.autocrlf false
if ($LASTEXITCODE -ne 0) { throw '仓库级换行配置失败。' }
git -C $Work checkout --detach $Commit
if ($LASTEXITCODE -ne 0) { throw '固定提交 checkout 失败。' }
$Head = git -C $Work rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $Head -ne $Commit) { throw '提交不一致。' }
$Tree = git -C $Work rev-parse 'HEAD^{tree}'
if ($LASTEXITCODE -ne 0 -or $Tree -ne $ExpectedTree) { throw '源码树不一致。' }
$Dirty = git -C $Work status --porcelain
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw '源码初始状态不干净。' }
Push-Location $Work
try {
    uv sync --locked --extra dev --extra postgres --python 3.12
    if ($LASTEXITCODE -ne 0) { throw '锁定依赖安装失败，不允许更新锁文件绕过。' }
    & '.\.venv\Scripts\python.exe' -m pytest -q tests/test_research_resolution_queue.py tests/test_research_resolution_poll.py
    if ($LASTEXITCODE -ne 0) { throw '新增专项失败；保留首次结果，不继续访问原实例。' }
} finally {
    Pop-Location
}
$Python = Join-Path $Work '.venv\Scripts\python.exe'
```

专项预期81项通过；以实际输出为准，任何异常跳过须解释。这里只重复轻量的新入口相关测试，不重跑此前完整验收。

## 二、确认原实例和迁移清单

先阅读新源码 `AGENTS.md`、`docs/research-resolution-queue.md` 和原项目的 `database/quickstart.md`。新旧迁移清单必须完全一致，本节点未新增SQL：

```powershell
$DbRoot = 'C:\Albert\project\polymarket-alpha-lab-kit-startup-4d63b08'
foreach ($Rel in @('.local\postgres\initialized', 'runtime\postgres\runtime.json', 'database\migrations.lock.json')) {
    if (-not (Test-Path -LiteralPath (Join-Path $DbRoot $Rel) -PathType Leaf)) {
        throw '原实例缺少必需文件；不初始化、不导入运行时、不替换根目录。'
    }
}
$MigrationHash = 'c0dcf17209298316332a581cd1384c42e9ca5b8e344afbfc2f71ec94362fb85f'
foreach ($Root in @($Work, $DbRoot)) {
    $Hash = (Get-FileHash -LiteralPath (Join-Path $Root 'database\migrations.lock.json') -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Hash -ne $MigrationHash) { throw '迁移清单不同，停止本轮只读接入，不自动 migrate。' }
}
$PgOverrides = @(Get-ChildItem Env: | Where-Object { $_.Name -like 'PG*' })
if ($PgOverrides.Count -ne 0) {
    throw '检测到 PG* 环境覆盖，停止并报告变量名称即可；不要显示其值或修改用户级环境。'
}
$Manager = Join-Path $Work 'scripts\project_database.py'
$StatusRaw = & $Python -I $Manager --root $DbRoot status
if ($LASTEXITCODE -ne 0) { throw '原实例状态校验失败，不重建、不绕过身份检查。' }
$Before = ($StatusRaw -join "`n") | ConvertFrom-Json
if ($Before.status -ne 'stopped') {
    throw '本轮要求原实例已空闲停止；不停止他人的会话。报告当前状态，本机读回暂未执行。'
}
if ($Before.version -ne '17.11' -or -not $Before.instance_id) {
    throw '原实例版本或身份与既有验收不符，请报告，不自动升级或接管。'
}
```

这里用管理器读取状态，不直接读取/打印 passfile。它内部按既有规则使用项目凭据，不访问系统凭据管理器。

## 三、只读列出两次，返回聚合计数

```powershell
$QueueScript = Join-Path $Work 'scripts\review_resolution_queue.py'
$Reports = @()
for ($Run = 1; $Run -le 2; $Run++) {
    $Raw = & $Python -I $QueueScript --root $DbRoot
    if ($LASTEXITCODE -ne 0) {
        throw '清单入口失败。保留本轮固定错误码，不删库、不静默重试、不启用采集。'
    }
    $Report = ($Raw -join "`n") | ConvertFrom-Json
    if ($Report.paper_only -ne $true -or $Report.report_only -ne $true -or $Report.readonly -ne $true -or
        $Report.live_model_called -ne $false -or $Report.outcome_confirmation_performed -ne $false) {
        throw '清单报告的安全标志不符合预期。'
    }
    if (($Report.unresolved_market_count + $Report.settled_market_count) -ne $Report.registered_market_count) {
        throw '清单数量关系不一致。'
    }
    if ($Report.evaluation_blocked_by_incomplete -ne ($Report.incomplete_execution_count -gt 0)) {
        throw '未完成任务提示不一致。'
    }
    $StateTotal = 0
    foreach ($Property in $Report.state_counts.PSObject.Properties) { $StateTotal += [int64]$Property.Value }
    if ($StateTotal -ne $Report.unresolved_market_count -or @($Report.items).Count -ne $Report.unresolved_market_count) {
        throw '状态计数或完整清单长度不一致。'
    }
    $Reports += $Report
    [pscustomobject]@{
        run = $Run
        generated_at = $Report.generated_at
        registered_market_count = $Report.registered_market_count
        unresolved_market_count = $Report.unresolved_market_count
        settled_market_count = $Report.settled_market_count
        state_counts = $Report.state_counts
        incomplete_execution_count = $Report.incomplete_execution_count
        evaluation_blocked_by_incomplete = $Report.evaluation_blocked_by_incomplete
        live_model_called = $Report.live_model_called
        outcome_confirmation_performed = $Report.outcome_confirmation_performed
    } | ConvertTo-Json -Depth 6
    $AfterRaw = & $Python -I $Manager --root $DbRoot status
    if ($LASTEXITCODE -ne 0) { throw '读回后的实例状态检查失败。' }
    $After = ($AfterRaw -join "`n") | ConvertFrom-Json
    if ($After.status -ne 'stopped' -or $After.instance_id -ne $Before.instance_id -or
        $After.version -ne $Before.version -or $After.port -ne $Before.port) {
        throw '实例状态或身份未保持；只报告，不强停或修改数据。'
    }
}
foreach ($Name in @('registered_market_count','unresolved_market_count','settled_market_count','incomplete_execution_count')) {
    if ($Reports[0].$Name -ne $Reports[1].$Name) { throw '两次聚合数量变化，请检查是否有其他会话，不自动修复。' }
}
git -C $Work diff --check
if ($LASTEXITCODE -ne 0) { throw '源码格式检查异常。' }
$Dirty = git -C $Work status --porcelain
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw '测试或读回改变了跟踪源码，请报告。' }
```

两次 generated_at 应是各自数据库时间；state_counts 可能因正常跨越截止/刷新边界而变化，不要因此伪造时间或写回状态。检查合理解释即可。原实例应仍停止，instance_id/版本/端口不变。

如所有数量都是0，请如实报告“本机实例可读，但尚无已登记研究市场；真实市场采集与模型质量未验收”。禁止为了让清单有内容而向原实例写测试市场、回填预测或伪造历史。

如果有 fetch_due / needs_confirmation / blocked_review / incomplete 记录，只回报聚合计数和不含敏感内容的原因汇总。本轮不 fetch、不人工确认、不修复 incomplete、不读取原始证据文本。真实业务记录与额外网络/写入验收另行安排。

## 四、回传与不做事项

回传固定源码commit/tree、新源码路径、Python/uv版本、81项专项的实际结果、两次上述聚合输出、开始/结束的实例状态及身份一致性、首次异常与未执行步骤。

区分本轮执行和引用CI结果。失败保留证据，不覆盖第一次失败日志，不通过删库、迁移、调大限额、替换数据源或忽略断言使结果变绿。

不要重复生成PR、提交源码、发布新Release、下载或重建引擎、重复安装全局交接规则。旧 `v0.1.0-native-preview.1` Release 仍是PR #14，本轮新源码直接使用以上Git提交。任何文件上传都不得包含 .local、数据库、passfile、备份或未脱敏原始日志。
