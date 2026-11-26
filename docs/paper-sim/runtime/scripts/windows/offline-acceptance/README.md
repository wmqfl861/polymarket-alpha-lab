# FX-49 Windows 离线演练

在获准接触 Windows、放置文件及执行替身验收之后，使用与部署场景一致的系统 Windows PowerShell 5.1 和已固定的运行层提交。独立的试验账号或试验目录由放置方保护。将同一提交的 `Invoke-PaperWeixin.ps1` 和 `Invoke-PaperWeixin.ps1.sha256` 放在试验源目录，把本目录保留在其下的 `offline-acceptance`。从该 Git 提交取得预期 SHA-256。准备一个位于本机固定卷的空试验目录，例如 `%LOCALAPPDATA%\PolymarketAlphaLab\WeixinDeliveryAcceptance\scratch`，避开原业务的 `WeixinDelivery\jobs`。

```powershell
$acceptance = Join-Path $env:LOCALAPPDATA 'PolymarketAlphaLab\WeixinDeliveryAcceptance'
$offlineRunner = Join-Path $acceptance 'source\offline-acceptance\Test-PaperWeixinOffline.ps1'
$scratch = Join-Path $acceptance 'scratch'
$approvedSha256 = '<SHA-256 from the pinned Git manifest>'
$engine = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
& $engine -NoProfile -NonInteractive -File $offlineRunner -Workspace $scratch -ApprovedScriptSha256 $approvedSha256
$LASTEXITCODE
```

`$offlineRunner` 是试验源目录下 `offline-acceptance\Test-PaperWeixinOffline.ps1` 的绝对路径。`$scratch` 指向获准的试验目录，`$approvedSha256` 是已审提交清单中的小写 SHA-256。测试脚本核对源文件、清单和实际复制的字节，然后用已安装 .NET Framework 的 `csc.exe` 把 `HermesSubstitute.cs` 编译到试验安装目录中的 `hermes.exe`。替身只读取合成说明、比对实传的完整文件并写入计数，配置与试验资料均在独立运行目录。执行策略拒绝、工具无法运行或任一断言不符时，保留失败记录并停止切换。

| 情形 | 脚本退出码 | 原生发送调用次数 | 回执结果 |
| --- | ---: | ---: | --- |
| 成功 | 0 | 1 | 接受，且清理后仍可核验 |
| 接收目标不唯一或为群聊 | 24 | 0 | 无接受回执，覆盖零条、多条及单条群聊 |
| 唯一私聊摘要不符 | 25 | 0 | 无接受回执 |
| 正文文件摘要不符 | 26 | 0 | 无接受回执，准备后更改合成文件 |
| 窗口关闭 | 22 | 0 | 无接受回执 |
| 原生权限拒绝 | 32 | 1 | 无接受回执，独立读取保留各类拒绝原因，重调仍为一次 |

关闭窗口例实际调用了固定入口。截止时刻相等的补充检查在同一 Windows PowerShell 5.1 版本内对哈希匹配的 `Assert-Window` 定义传入两个相等的 UTC 时刻。它单独记录退出码 `22`，无原生调用。

`$scratch\fx49-offline-<随机运行号>\fx49-offline-evidence.json` 保存引擎版本、固定脚本 SHA-256、逐例实测退出码、替身发送次数、回执断言、重调退出码与 UTC 完成时间。六类中有十条实测记录。交付只附此脱敏结果和对应的 Git 提交号。现场结果不由 Linux 测试代填。
