# PR #17 本机下载器与 BTC 公开读取复核

本轮只检查此前真正失败的本机环节：Windows PowerShell 5.1 下载交接文件，以及 BTC 官方检索。实现已经通过 PR #17 的完整离线、Windows 原生和实际分发包验收；不要重做全仓、数据库、备份恢复或 ETH 预览，不再检查已知为“未知/未授权”的模型配置。

仓库：`wmqfl861/polymarket-alpha-lab`
固定测试源码：`c7a6f19ed4ef5e9cbc0d838db3da6ff72f08c3cb`
固定实现树：`9b7e48d9e10bfbd242a6c875daf84f9538343837`
PR：`https://github.com/wmqfl861/polymarket-alpha-lab/pull/17`
本轮无补丁，不使用 git apply，不提交或合并代码。

## 已有事实及本轮授权

用户报告的 PR16 首次下载编码错误、第二次下载超时、BTC IncompleteRead 均保留为失败；ETH prepared 和 80 项专项通过属于此前结果。不要追溯改写为全绿。

本轮明确允许 BTC 检索最多三次尝试。只有 incomplete/timeout/reset/abort 可以由新代码受控重试；每次失败都在 attempts 中保留。之后的三源预览不重试。因此本轮行情网络请求上限为六次（最多三次检索和一次预览的最多三次 GET），不包括获取 Git 源码、交接文件和锁定依赖。严禁外层静默重跑命令增加此上限；不要重跑旧探针。

不读 .env、密钥、passfile、token、用户环境或凭据管理器；不调用任何模型，不批准 terms_sha256，不连接/启动/迁移数据库，不登记市场或写预测，不加代理/镜像/杀软排除，不提权、不改执行策略。旧 kit、.local、postgres.installing、既有工作区与失败证据全都保留。不更新用户级全局规则，不重新发布 Release，不安装或升级全局工具。

## 1. 固定源码与 ASCII 下载器

先执行 ChatGPT 消息内纯 ASCII 引导代码。它将普通 Git 源码检出到全新目录并检查 commit/tree、下载器 SHA256，然后使用仓库脚本下载本交接文件。不要把包含中文的整个 Markdown 文件保存成 .ps1 执行；可执行引导代码和 download_handoff.ps1 都是 ASCII，Markdown 只是以 UTF-8 阅读的说明。

下载器路径：`scripts/download_handoff.ps1`
原始文件大小：6968 字节。
SHA256：`81a2655968780f8df78d5f4ffacd5d11c1ffbc25f749b6a82a820833140ffb2c`

下载必须指向消息中给出的固定交付 commit，不使用 main/raw/latest。manifest.json 的外部 SHA256 必须与消息给定值一致；再按清单逐件核对，下载器全部通过后才返回 status=verified 和最终目录。下载器不执行所得文件，不能把 verified 理解成已跑测试。

本轮必须在 Windows PowerShell 5.1 执行下载器（PSVersionTable.PSVersion.Major 应为 5）。不要为此安装新 shell；系统已有 powershell.exe 即可。可以由已有本地 Agent 调用普通 powershell.exe -NoProfile -File，不使用 ExecutionPolicy Bypass。哈希计算用 .NET，不依赖 Get-FileHash 自动载入。下载失败会返回固定阶段码并保留 .downloading/.part；原样报告并停止，不删目录后重来。

记录实际 shell 版本、loader 哈希、交付 commit、manifest 哈希、status/files/executed 和最终目录。verified 必须同时满足 executed=false；检查 LOCAL_AGENT_PROMPT.md 与 LOCAL_CHECK.ps1 的字节数及哈希。通读两文件后才执行下一步。

## 2. 使用本工作区环境，不访问旧实例

引导已经提供新源码目录 $Work 和已验证交付目录 $Drop。在同一普通用户 PowerShell 会话执行：

```powershell
Push-Location $Work
try {
    uv sync --locked --python 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed.' }
} finally { Pop-Location }
```

只需运行依赖，不需 dev 或 postgres extra。不改 uv.lock，不复用旧 editable 安装，不安装新的全局 uv/Python。安装失败即报告；不自动升级或修改依赖。

执行已通读并通过下载器校验的 ASCII 本地检查脚本：

```powershell
& (Join-Path $Drop 'LOCAL_CHECK.ps1') -SourceRoot $Work
$CheckExit = $LASTEXITCODE
Write-Output ('local_check_exit=' + $CheckExit)
```

LOCAL_CHECK.ps1 再次验证源码 commit/tree/干净状态，要求无 .local 和无 PROJECT-BUNDLE.json，先运行禁用联网的 CLI，再只运行一次：

```powershell
.\.venv\Scripts\python.exe -I scripts/discover_crypto_research.py --team crypto_btc --preview --attempts 3 --allow-public-fetch
```

脚本不是再造一个研究程序，只调用本次已验收的生产 CLI。检索与预览输出保存在本次进程内，用于返回聚合和选中项；不生成文件业务日志。未找到候选、HTTP拒绝、校验失败、预览阻断或持续中断均如实返回，不虚构概率或数据。

运行后检查新工作区仍干净、未产生 .local；不为证明旧库未变而启动旧数据库。脚本自然退出，不设置周期任务，不做外层自动重试。

## 3. 结果解释与回传

回传固定源码commit/tree、实际新工作区、PowerShell/Python/uv版本、下载器和两份交接文件校验结果；以及 BTC 的 status、request_attempts、每次 attempts、recovered_after_failure、preview_invocations、public_gets_upper_bound、configured_public_gets_ceiling。

若 prepared，回传选中市场的 condition/slug、公开问题、采集时刻、计划结束与临时预测截止时间、三源哈希、行情时间窗与比较根数、最大价差及 model_called=false/database_written=false。不要上传完整检索候选全集或数据库文件。

若第一次失败、后续成功，报告“受控重试后恢复”，不得称首次成功；若检索成功而预览失败，也不能说 BTC 全链路通过。没有 eligible candidate 不是网络失败；没有行情预览不是模型失败。无论成功与否，旧报告的第一次 IncompleteRead 都继续保留。

prepared 只代表输入就绪，不批准研究、不保证未发生结果、不等同于结算来源一致。Coinbase/Kraken USD 小时数据不等于 Binance USDT 分钟结算数据。遇到时间段内触碰/跌破/最高/最低之类问题，当前几根K线不能证明此前是否已满足事件条件；仅披露，不自动认证或回填历史。临时 cutoff/hash 不得复用为之后真实模型运行的授权。

模型四项未知已经确认，本轮不再调查，不读取配置寻找凭据。提供商/准确模型、公开输入发送授权和首轮费用上限须由用户明确决定，本地 Agent 不得自行代为批准。此次结束停在真实公开读取复核，不执行 launch_crypto_research。

没有新错误就报告本机针对性复核结果即可。任何错误保留首轮退出码和未执行步骤，不擅自扩展任务。
