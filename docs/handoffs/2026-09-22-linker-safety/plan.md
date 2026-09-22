# RV05 自动衔接安全修正与多日交付计划

任务：PAL_RV05_CONTROL_SAFETY_20260922。继续原PR67和09-28绝对截止，不重新计七天；不新增长测指标、不重写容量体系。先解除未消费的旧自动启动，再在既有控制代码中修复明确反例。原d929 soak保持。

## 已核验的边界

候选b061b7d7ad51cd21d7a64c84eba0941a1fd08085，完整树79ec2d7945f9dd2b492e243521e0c166f8fbebd0。协调者通过现有contents-read-only源码导出核对6543个Git blob及完整树。只读查看四工作流success；另外下载Windows-soak证据ZIP并核验SHA256、CRC、XML：456passed/0failed/0skipped，其中N5为39项，linker模块为0项。当前workflow选择未含tests/test_soak_linker.py。不能把别的456项当成20项linker已获Windows证明。

精确历史linker SHA256：1e1edfd4a90d5f57ab0e4845859e18ed3a30b9f6dd14a4ed3d2d084fe6709965。协调者有限探针在纯合成目录上，使用原模块及替身preflight/launch/Win32，复现9条路径，5个合法控制正确；没有真实进程启动。原20项linker测试在Linux/Python3.13.5/pytest9.0.2实际20passed，8.79秒，不是Windows结果或全量验证。一个初始净化启动选择到不含pytest的系统解释器，导入前失败；改用已知/opt/pyvenv/bin/python后才执行上述测试，未改变源码或下载依赖。

用户报告原进程和新wait PID74784于09-22T04:26:39Z处于ARMED_WAITING、151552容量通过及本地preflight全GO。这些是本地报告，不是协调者对本机的新读取。没有证据显示下述异常已在真实campaign发生，不认定数据泄漏、双发射或业务损失。

## 九条确定反例，五类修复

| ID | 历史实际行为 | 必须保持的合同 |
|---|---|---|
| L1 | preflight替身执行期间另一个cancel已写CANCELLED，返回后仍进入launch替身并改写STARTED | 撤销必须在不可逆启动前最后一处安全点有效；收到撤销不能继续消费新launch |
| L2 | preflight期间时间跨过latest，返回后仍进入launch替身 | 预检耗时不能使窗口校验过期；linker和launcher两层都在实际启动边界核对 |
| L3 | launcher rc7先写NO_GO，下次wait却把原失败回执解释为STARTED | 非零/拒绝/未知不得变成成功；再次观察不补发、不改终态语义 |
| L4 | launcher rc0且campaign不存在，仍写STARTED | 退出0不是实际启动证据；核对绑定的启动回执、claim与进程/文件身份；不足UNKNOWN |
| L5 | launch_argv调用无关命令，只把pinned launcher作为未使用参数，load_binding仍接受 | 校验实际argv结构和执行位置，不是字符串成员检查；测试替身走注入边界，不放宽真实语法 |
| L6 | binding接受-ExecutionPolicy Bypass；交付README也推荐该写法 | 禁止绕过执行策略；删除示例并拒绝此参数及等价缩写/编码变体，不能转用另一种绕过 |
| L7 | 64KiB合法JSON前缀后附非法尾巴，读取截断后解析成功 | max+1超限探针、错误类型与重复键拒绝；不把截断后的合法前缀当整个文件 |
| L8/L9 | Fake OpenProcess NULL、WAIT_FAILED均返回False而非unknown | 按具体Win32返回/错误码分清alive/exited/unknown；失败不能凭空证明退出 |

L1/L2是linker本层的可复现缺陷；没有断言实际PowerShell后续复核必然同样失效。L3没有再次启动，只是错误升级状态。L4未声称真实launcher当前会返回这种组合；其外层证明强度确实不足。L5/L6只解析未执行。L8/L9是API返回形状注入，非实际WindowsDLL；当前有锁分支false会NO_GO，并不是单独证明活进程被当已清理并启动覆盖。

另外源码未声明OpenProcess/GetProcessTimes/WaitForSingleObject/CloseHandle的完整ctypes ABI；Windows测试需验证64位句柄与错误映射。官方文档说明OpenProcess失败需看GetLastError，WAIT_FAILED是失败；ctypes未声明restype默认C int。不得把任何访问拒绝处理成不存在，也不提权获取信息。读取辅助协议、短写claim、预检结果新鲜性、所有tmp/输出路径与重解析点属于这些原合同的邻接检查，不要求创造新指标或新框架。

参考：
- https://docs.python.org/3.12/library/ctypes.html
- https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess
- https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject

## 立即动作：只撤销尚未消费的旧衔接

不要改运行中脚本来触发hash漂移。N0先只读用户已给binding路径和state、claim、receipt，核对确为本批。当前时间在最早窗口前、state仍ARMED_WAITING且两个启动层都没有claim/receipt时，使用用户已提供的准确cancel命令；不运行launcher或任何带Bypass命令。确认旧wait进程按其真实创建标识退出，不能只看到cancel子命令退出0就当长期进程已退出。

只写linker独立STOP和其控制状态，保留old arm与cancel记录；不写原/新campaign STOP、不清空claim、不杀历史PID。若已CLAIMED/STARTED/UNKNOWN、窗口已开、路径/字节/权限不可信，只读记录HANDOFF_STATE_CHANGED，禁止执行本预窗口快捷撤销或重arm；由N0核对当前不可逆边界，再请求具体安全处置。其他独立源码修复继续。

旧slot未被消费且旧wait确实退出后，可准备新控制包。旧绑定/输出不覆盖，新目录新控制身份与新claim；必须确保同一个新campaign没有第二个活控制器。槽位已消费或不确定就不能新目录绕过一次性限制。系统执行策略拒绝必须记录BLOCKED，不用ExecutionPolicy Bypass、Unblock-File、EncodedCommand、stdin脚本或换解释器方式绕过。

## 六逻辑节点与文件所有权

| 节点 | 文件/模块owner | 交付与依赖 |
|---|---|---|
| N0 | 本批状态、身份、控制包、唯一PR整合 | 先撤旧arm并确认；保全已有151552容量与原长测；维护READY队列；只此节点提交整合 |
| N1 | tests/support/soak_linker.py 主writer、状态测试 | L1-L4：预检后再次读取撤销/时钟/绑定/新鲜报告；claim短写拒绝；失败/未知回执不升级；跨两层协议的至多一次尝试，不宣称必达exactly-once |
| N2 | tools/soakctl/launch-rv05.ps1、preflight/模板/说明 | L5-L6：封闭argv与明确cwd/env、无策略绕过；两层启动最后安全点、回执身份；涉及linker的diff交N1，不并行写同文件 |
| N3 | Windows辅助代码和有界记录子补丁 | L7-L9与同源ABI；先在独立分支提供补丁给N1，不能改活文件；有界读取、UNKNOWN、句柄每次正确关闭；无新增监控服务 |
| N4 | tests/test_soak_linker_review.py、既有Windows专项选择 | 独立将九反例转正确pytest断言和合法控制；真Windows句柄/权限/预检-撤销-返回码子进程组合；原20+39与既有平台用例不丢 |
| N5 | 既有DELIVERY_PLAN/ops/最终证据 | 纠正文档ARMED状态不准确之处到新更正记录，标废弃Bypass示例；记录最终构建与操作来源，长测后双campaign审查和原后置故障合同，不新增报告层 |

N1/N2/N3先明确接口，N4独立设计反例，N0按文件owner集成。逻辑Agent并发仅用既有获准能力，不能新建机器/身份/CLI。合成worker初始只1个，代码阅读可并行。节点完成就做就绪任务或交叉只读审核；不为空等造第五套启动器、不为维持六节点重复容量或全量。

## 实现与验证要求

- 清理旧arm是操作步骤，不是删除错误证据。已报告的容量、DST修复、39项N5和11+2不重做。历史repro可在同源新scratch仅运行一次核对，输出3/9/5是RED，不是软件通过；修复后探针拒绝不同hash正常，应用pytest中的新断言。
- 真实CLI仅允许严格固定的PowerShell可执行文件/-NoProfile/-NonInteractive/-File位置及SpecPath；不靠禁止几个字符串的黑名单。测试可通过内部依赖注入使用惰性runner，不让真实配置携带synthetic clock、stop-after claim或另一脚本。固定程序路径、完整参数、工作目录、环境与输出上限须可核对。
- 撤销/开始竞争要明确线性化点：cancel已确认于不可逆边界前则不启动；已claim且结果不确定则保留unknown与已消费槽位。单纯多加一次if不等于修复所有竞态。预检跨窗、取消、pin漂移、旧GO报告、重复控制器、partial write都要有确定性barrier控制，不随机sleep碰运气。
- 预检返回码与报告逻辑不能互相矛盾；非零NO_GO不能随意升级UNKNOWN或重试成GO。报告必须绑定本次调用/完整门禁/配置，不能读取上一次残留GO后启动。launcher返回0必须有可核对的启动证据；返回非零可能已经做过操作，保留失败/未知、不再发。
- 新started回执和重复wait必须验证task/binding/candidate/claim/schema/结果，拒绝缺字段、错类型、null/假claim、半写/重复键。未知不自动治愈成started；合法迟到证据只按明确规则重新判定，不能从rc7回执推出成功。
- 权限错误不提权；Windows API声明argtypes/restype、0/258/WAIT_FAILED区别、NULL错误码及句柄释放通过真实Windows+故障注入核验。逻辑测试Linux通过不等于64位ABI验证。复用项目已有被审查的只读查询能力，不能复制有缺陷代码以绕过生产文件范围。
- 当前Windowsworkflow漏掉linker测试，必须补原20项和新回归，保存完整实际JUnit清单、零模块级跳过、原N5=39与两个平台用例。新候选原适用CI仍需要；禁止通过反复rerun同失败或扩大超时/权限凑绿。
- 原始生成器/被测函数/审计/manifest/runtime字节未变，只修改控制包时保持SUT b061不变，不要求再算151552。N0分别记录SUT_SHA和CONTROL_SHA并证实依赖影响。若确实修改受测代码，则另冻结并按受影响范围补验，不借旧长测时间；不能用包提交号不同就无差别重跑全部。

## 多日推进与唯一重新武装

09-22：撤销未启动arm，保留证据；N1-N4复现/修复/CI，N5准备交付。09-23：控制包独立副本复核、Windows真环境验证、单次衔接短演练，确认旧wait已退出且无消费claim，然后绑定新控制包。额外授权到此为止，不改变原时限或正式指标。

新arm只允许在旧未消费slot被可核验撤销且新测试/审核/适用门禁通过后。不要求用户重复批准同一离线修正；但本地Agent不能因自己说通过就跳过证据或未知状态。允许有限衔接自动到窗口启动一次已批准的合成长测，非真实模型；如能力不支持持久进程明确NOT_STARTED。

开始窗口仍2026-09-24T02:14:50Z至12:36:41Z，前提是原驱动实际退出、孩子清理和原证据固定。台北为09-24 10:14:50至20:36:41。最终截止09-28T00:36:41Z（台北09-28 08:36:41）。补修赶不上就NOT_STARTED，不缩短259200秒/864有效passed轮/100000合格不同输入，不第三次长测，不滚动续七天。

09-24至09-27按既定新72h执行；原和新结果分别审核。期间冻结目标/控制文件，不插新feature，不循环既有39999项全量。N4/N5执行非干扰的源码/发布材料审核而非制造任务。结束后运行第二次原两类故障合同，核验实际时间/输入/失败/unknown/资源与清理；READY、exit0和绿标均不等于产品qualified。自动控制只做有限观察与汇总，不能自动唤醒LLM或合并PR。

资源原界保持：合计工作集目标2GiB、相关新目录4GiB、证据512MiB、卷剩余至少10GiB。旧材料、复制及失败证据都计入；不可测为未测，不保证硬上限。旧任务压力大暂停旁路，不能停止原soak腾资源；不改休眠/系统策略。每2小时检查点、12小时摘要；只读必要本批元数据，不扫用户进程/目录。

## 不得越界与收尾

保持原禁止的生产src/SQL/依赖锁/权限/业务数据库、真实CLI/API/OAuth、真实行情/钱包/订单、Sandbox/BIOS/系统策略/ACL/防火墙/服务/计划任务、全局安装和厂商下载绕过。三处旧业务根不读写，已知准备目录只读指定材料。所有例子中的模拟程序不是厂商程序。

本地只更新一个草稿PR67；禁止强推、合并main或发布未经批准的正式产品Release。任何未知启动不得靠删除claim、新目录、改task ID重发。新测试support能力仍不是业务调度器或G2-G6验收。official_cases_run=0、sandbox_started=false、activation_authorized=false。

最终报告先区分：旧arm是否撤销/进程退出、新arm是否真正运行、SUT与控制包各自身份、九反例RED-GREEN、合法控制、真实Windows用例、旧39999绿标/新门禁、原与修正版长测、剩余阻断。全部有用工作完成可提前收尾，不以天数为KPI。有限故障路径核验不承诺零缺陷；同Agent分离自审须如实标注。
