# 微信投递的 Linux 配置

`weixin_delivery.py` 通过已登记的 HC 上下文选择唯一 Windows 主机。每次操作都以同一方式启动受保护的本机中转程序。命令行仅含中转程序的绝对路径和一枚 32 位小写十六进制 nonce。操作、正文、时间和收件绑定摘要均由既有 JSON 协议通过标准输入传递。

## 固定入口

在投递服务的本机环境中登记下列三个完整路径。该部署采用系统目录 `C:\Windows` 和 64 位 `C:\Program Files`，路径中的空格是值的一部分。

| 配置项 | 登记值 |
| --- | --- |
| `PAL_WEIXIN_RELAY_PATH` | `C:\Program Files\PolymarketAlphaLab\WeixinDelivery\PaperWeixinRelay.exe` |
| `PAL_WEIXIN_POWERSHELL_PATH` | `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe` |
| `PAL_WEIXIN_SCRIPT_PATH` | `C:\Program Files\PolymarketAlphaLab\WeixinDelivery\Invoke-PaperWeixin.ps1` |

Linux 校验三项登记后把固定的中转程序路径传给 HC。中转程序把请求交给原绑定 Windows 账户的普通权限进程。该进程使用登记的系统 PowerShell，以 `-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File`、固定脚本绝对路径和原 nonce 调用脚本，并原样返回脚本的标准输出与退出码。

`PAL_WEIXIN_RELEASE_COMMIT` 必须等于 Linux 部署检出的完整提交号。程序从该提交的 Git 对象和检出文件同时校验固定 PowerShell 脚本及其 SHA-256 清单。变更投递入口时沿用已经登记的 HC 可执行文件、用户、套接字、上下文摘要、Windows 主机绑定和私聊绑定。它们的现场值留在原有私有配置中。

## 部署回执

安装回执登记构建 relay 和 worker 的源码提交、相对路径、各源码 SHA-256、编译器版本以及最终 Windows 可执行文件的 SHA-256。安装后从受保护目录重新计算文件摘要，并验证普通权限进程无法写入或替换程序、脚本和其依赖。另行核对实际工作进程是原绑定账户的普通权限进程，且 Hermes 使用原配置并只匹配既有私聊。

在受保护入口完成替身验收后，使用 Linux 投递程序的 `--route-check` 从原本机配置提供收件绑定摘要。这个入口仅调用 `check`。真实验收使用一次符合原条件的自然投递。对失联或不完整应答，使用同一 nonce 按原时限读取回执，保持既有账本状态和防重发规则。

## 通知调用方的切换

`notification/entry.py` 只构建两个通知调用文件。输入是原通知服务工作目录里的 `football_daily.py` 和 `football_store.py`。登记的差异来自 `97e9cbcb`。它给提交、回查和入账补上固定回执代际、提交时间、有效窗口及明确拒绝。现场数据库连接、原有授权文件读取方式和所有其他业务函数保留原字节。制品是两个 Python 源文件的 ZIP，输入和输出均有逐文件 SHA-256。构建在临时目录完成，发布采用单文件原子创建。输入漂移、输出校验失败或同名存在不同制品都会停止。

使用最终部署的干净主线检出和已登记的 `PAL_WEIXIN_RELEASE_COMMIT` 来构建。在受保护发布目录为这一版本选一个新文件名，使用原服务 Python 执行以下命令。占位符分别指向已核对的本机路径。

```text
<service-python> -I -B <main-checkout>/docs/paper-sim/runtime/notification/entry.py stage <existing-working-directory> <new-bundle>
```

预备好的制品交给原服务身份只读使用。切换时仅给现有通知调度服务（`tick`）和通知工作进程模板（`worker`）选择新入口，保留原服务身份、工作目录、环境文件和原任务参数。同一版本还可用于手动 `route-check`。定时器、赛事扫描、业务模块和数据库结构沿用现场配置。

```text
<service-python> -I -B <main-checkout>/docs/paper-sim/runtime/notification/entry.py run <new-bundle> tick
<service-python> -I -B <main-checkout>/docs/paper-sim/runtime/notification/entry.py run <new-bundle> worker <original-worker-instance>
```

运行入口在加载业务代码前校验主线检出、投递模块、制品中的两个源文件及工作目录里的两个原件。它把实际校验过的投递模块登记为 Python 的 `weixin_delivery`，再加载相同制品的账本和通知调用方。其他业务模块继续从原工作目录导入。工作目录中的旧发送器不参与解析。新旧账本恢复分别依据原有尝试的记录走各自代际。已明确拒绝的记录保持终态。

切换时登记主线提交、制品摘要和两个单元的有效启动入口。在新旧调度或工作进程仍可能并行时保持原入口；等待在途进程自然退出并核实边界后再让两类单元使用同一代际。主线源码入库和本机替身回归分别登记，正式切换以现场有效配置与自然投递的实际回执为准。
