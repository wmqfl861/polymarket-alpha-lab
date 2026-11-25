# FX-49 初检退出 21 诊断补遗 r2（PR #131）

r1 的失败裁定绑定 SHA-256 `0c79a4d63d66355e3808307dbb9bf7f2a1c06043edab2be3a81a73746aaa6ed8`。本件以 UTF-8 无 BOM、LF 换行的完整正文送原计划审阅者，摘要在正文外登记。取得绑定该摘要的 `VERDICT: PASS` 后，即可从当时的 `main` 开独立草稿 PR，无须等待 M2。#131 的旧头及历次失败封存。

## 失败基线与固定输入

`windows-fx49` 运行 `45702610544`，attempt `1`，job `139516338008`，结论 `failure`。base 为 `289ed1c217cdd97a68da959bfcecdd17db9fc8e5`，PR head 为 `69858354dd3728418095eb5ed5454a3821484299`，合并检出为 `342f863ae5dfdc333aea50cf8b4b7a973c09d2ad`。日志读取成功，配置标签为 `windows-2025`，镜像发行号为 `20261116.315.1`。采集器缺少类型化镜像类别，精确引擎版本亦未观测，两项均记 `unknown`。编译退出 `0`，初检退出 `21`，空 stderr 属于外层演练。截止等值结果为 `unknown`，十例无记录。便携门的 `7.4.22` 只归属便携运行。

两项输入均来自提交 `97e9cbcbf510de0f10606ec06380376d627bff50`，所给公开头也含相同 DIRECT blob。下表路径相对于 `docs/paper-sim/runtime/scripts/windows/`。

| 文件 | Git blob OID | 字节 | SHA-256 |
|---|---|---:|---|
| `offline-acceptance/Test-PaperWeixinOffline.ps1` | `80401df29554bf4e2dcfdb560498e12bd7ecc934` | 19359 | `ef0f816c51b7dbd8ebb47231230b02ecb45f60fab61dd8dd0b4f7a4f563dd0c6` |
| `Invoke-PaperWeixin.ps1` | `e57e6d79749fc216246017e3b511566a45b8beef` | 35487 | `e86beca8f4073da75a8bc1cd3aeef3e85230417c0b19f9424432e79baa7998c6` |

制备诊断 PR 时优先复用树内相同 blob。若当时基线缺项，只将原 blob 挂入诊断专用测试路径并登记映射。两平台均从实际诊断检出的路径核对普通文件模式及 OID，用本地 `git cat-file blob` 读原字节，复核长度和 SHA-256。浅检出无须包含历史提交，作业无须联网补取材料。

## 一个合法 `check` 正例

1. **独立预期。** Python 按 `protocol,purpose,hostname,script_sha256,target_digest` 原序生成无 BOM、无尾换行的紧凑 UTF-8 JSON。协议取 `paper-weixin-fixed-v1`，用途取 `check`，脚本摘要取上表固定入口值。目标摘要取 `weixin:` 与原 `$FixtureId` 串接后的 UTF-8 SHA-256，引号和末尾反斜线均参与散列。Linux 独测在同一检出显式执行 `python -m pytest -q tests/fx49_hosted_entry.py::test_valid_check_frame`。该节点执行上述双源完整性检查，并以公开合成名 `fx49-lab` 对照独立固定的字面帧、长度及摘要，逐字节核对 Python 结果。既有 Linux 套件照常执行。Linux 证据限于源码和帧预期。
2. **托管写端。** `Research paper bridge / windows-paper` 在原 pytest 命令中显式点名同一节点。按原验证器的标准句柄启动第 298 行指定的系统 Windows PowerShell 5.1。按整份演练 blob 的 AST 白名单和源码顺序取初始化、常量、目标赋值、`Invoke-Entry` 及 `Invoke-LocalProcess`。第 15 行构造严格无 BOM UTF-8 对象，第 256、355 行仅写控制台，所给全表无 `Console.InputEncoding` 赋值。须复核并执行全部影响编码的原语句，无法核齐即记 `unknown` 并失败。保留原夹具、argv 拼装、重定向、EOF 和时限，仅将 `-File` 的目标指向合成观测脚本。写端只启动这一个子进程。
3. **实收与读端。** 启动写端前，Python 用 `GetComputerNameW` 在内存取本机名并独立算出预期，仅经进程私有环境传递整帧长度和摘要。子进程用静态模板和自己在内存取得的名称构造规范帧，先核长度及摘要。它有界读取原 stdin 一次，对实收字节与规范帧作逐字节比较，再让两份缓冲分别经过固定源码的解码、`Read-FlatObject` 和 `check` 字段谓词。原 reader 始终收到未经裁剪的缓冲。另测实参数量、十六进制 32 位类别、与原 nonce 等值，以及主机形状和解析所得名称的原等值谓词。未到达项记 `unknown`。
4. **判定与保密。** 仅当实收全等、实参正确、两帧均被原 reader 接受且主机谓词均通过，节点才通过。材料、规定引擎或必要观测缺失均失败。探测限于仓库的演练写端和合成读端，脚本自校验、查找、发送及 owner 现场均在探测范围外。真实名称仅存在于内存和被测匿名管道。生成脚本只含静态材料。观测输出限预置枚举，异常使用固定码，禁用断言值展开及原始流回显。同一测试模块以 `-p tests.fx49_hosted_entry` 加载报告钩子，在 JUnit 写盘前将报告器的 `hostname` 固定为公共合成值。真实标识、敏感值及动态帧摘要均不得进入输出、文件、异常或 argv。

Microsoft .NET Framework 参考源码中，`Process.cs` blob `3096cb509f6881646098c6e20ed2d02aba79f410` 以 `Console.InputEncoding` 建写端并开启 `AutoFlush`，`StreamWriter.cs` blob `aca3af9b02eb22fbedca8932934f7f1bf976072f` 的刷新路径可能先写前导，`Console.cs` blob `c41430775918478e19c80ab83c7ef87cd0090bd6` 说明输入编码的取值路径。失败管道的实际首字节仍为 `unknown`。

## 观测与处置

| 单一可区分的观测 | 最小候选动作 |
|---|---|
| 实参及本机形状合格，仅多出已识别前导，余段全等，实收拒绝而规范帧通过 | 在新版离线演练创建写端、首次刷新前验证无前导写法。修后须实收全等且原 reader 接受。 |
| 字节全等、两帧及主机通过，仅实参数量、类别或等值失败 | 只修新版演练的 5.1 实参拼装。 |
| 字节全等、实参正确，两帧在同一解码或字段谓词被拒 | 对照固定契约核定位点。仅在证实演练构造有误时修该构造。涉及受保护入口即止于证据。 |
| 字节、实参及解析通过，仅主机形状或等值失败 | 证实取值错误才修演练取值。托管主机本身不符则记环境不相容，保留原守卫。 |

全绿、短写未解、多轴异常或同字节判读矛盾时，交付已排除项，#131 根因记 `unknown`。回执登记诊断 base／head／checkout、双源路径及 blob、run／job／attempt、节点实际执行次数与结果、编码及实收前导类别。逐项比对两作业的配置标签、类型化镜像类别、发行号和实际系统引擎完整版本。缺项的比较及相应运行者归因均记 `unknown`。在 JUnit 七天留存期内将脱敏回执存入协调证据，随后无论红绿均以**未合并**关闭诊断 PR。

## 实际修正与发布边界

有单点证据才另立修订。同一正例须在旧 blob 上实测旧红，在修正候选**实际路径的 blob** 上实测新绿。测试和收集参数只可随真实修正正常合入。演练字节变化另立发布修订，验证器变化另立被审修订。封存 v3 字节、索引、三份回执及历次失败。实际发送、安装、绑定、接收配置、重试策略、检出身份、时限、夹具及全部守卫沿用原契约。

发布时按实际 `main` 复核差异，依次固定源码提交、单亲索引提交和单亲回执提交。原计划、代码及阶段审阅者分别对准确修订给出 `VERDICT: PASS`，独测另签。返回 R／T、冻结 DIRECT 路径及 blob、专项与全量命令、收集清单和退出码。最终 `windows-fx49` 以实际 base／head／合并检出调用 `scripts/verify_fx49_publication.py windows`，同一作业只运行一次原演练。发布条件包括编译 `0`、初检 `0`、截止等值 `22`、冻结十例逐项通过及零真实投递。`Offline verification` 和两项 FX-49 必需作业须各自为 `CURRENT` 且成功，合并后按原流程匿名回读核验。

**FX-49 现场验收仍为 0/6。** 诊断 PR、托管 CI 及其十例对现场六例、自然投递回执和所有现场门的贡献均为零。现场仍受既有执行策略和受限端点要求阻塞，本计划没有 owner 操作步骤。