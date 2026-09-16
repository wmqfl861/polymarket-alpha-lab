你接手 wmqfl861/polymarket-alpha-lab 的一个已经完成容器侧开发和分开自审、但尚未发布的候选。任务只包括：补齐 GitHub-first 上架、在新 SOURCE 分支应用同一份补丁、创建草稿 PR 并核对该最终提交的 CI。不要重新实现功能，不要合并 main，不要操作用户的业务安装。

## 固定身份
- 仓库：https://github.com/wmqfl861/polymarket-alpha-lab
- 必须匹配的当前 main：4df08873f4b9b8f1378913c0d00ca6bb183e51e2
- 基线 tree：401f5a691e6898c670e446d142340d396862dae9
- 补丁结果 tree：87486a77918a1eef57e19b7960032211a8578ab9
- implementation.patch：46577 字节；SHA256 f87ca9e56929d0716e11fe0873cfc671001a82ff7bd64ee7f977ceedd0d81830
- 候选包外层校验值由协调者在本次答复及单独完整提示词中提供；包内 PAYLOAD-MANIFEST.json 覆盖全部载荷。

## 当前状态和不重复事项
协调者实际完成：35 项新单元／反例测试；相关244通过/2原生跳过；单独工作副本130通过；完整本地37973通过/42跳过，最终 PASS。环境为 Linux Python3.13.5/预装依赖，不是锁定 Windows CI。
自审实际复现恢复中相同数量的新增迁移被改写仍可发布的缺陷，已改为比较完整目录指纹；review-red.log 首败1失败/12通过保留。
候选源码已冻结，九个文件内容由 source-manifest.json 绑定。新66->67原生测试已编写但未执行。尚未上传、未创建PR、未触发候选CI、未合并。不要借用先前PR44/45通过记录。
当前会话 GitHub 工具没有提交/PR写入接口，远程工具也没有执行能力。附件只是保全成果；GitHub-first 上架尚未完成，不能声称已经有固定 GitHub 下载链接。

## 1. 只先补文件上架
取得用户明确交给你的候选 ZIP 的真实路径，不要递归搜用户目录、凭据或旧数据库。核对协调者提供的外层字节数和 SHA256；在新的普通用户可写目录中解压，禁止覆盖已有文件。验证 PAYLOAD-MANIFEST.json 的文件集合、每个文件大小和 SHA256；多件、少件、校验失败立即停止。

先读 REVIEW.md、README.md、candidate-identity.json、source-manifest.json、validation-summary.json 和补丁。包不包含原生引擎、备份、.local、.env 或凭据。使用已有正常 Git/GitHub 登录，不读取、打印或传递认证文件内容，不索取 API 密钥。

在一个全新的 SOURCE checkout 中：
```powershell
git clone --no-checkout https://github.com/wmqfl861/polymarket-alpha-lab.git <NEW_SOURCE_CHECKOUT>
# 进入刚创建的目录，每条命令后检查 $LASTEXITCODE，非零立即停止。
git fetch origin main
git rev-parse origin/main
# 必须精确等于 4df08873f4b9b8f1378913c0d00ca6bb183e51e2，变化就停止报告，不自动变基。
git switch --detach 4df08873f4b9b8f1378913c0d00ca6bb183e51e2
git status --porcelain
# 必须干净；读取此版本 AGENTS.md、DELIVERY_PLAN.md，遵守当前覆盖规则。
git switch -c handoff/wp06-backup-catalog-20260916
```
把候选包全部已验证载荷（不要把ZIP自身再套入）复制到 `handoffs/wp06-backup-catalog-20260916/`。只提交这个目录；保持所有原业务源码、迁移、依赖及工作流不变。运行差异、大小/哈希及有限凭据检查，记录只读自审，再用正常 hooks 提交、非强制推送。分支已存在时停止核对，不能覆盖或强推。

必须取得完整上架 commit SHA，并经 GitHub API 或新的只读取件工作区回读全部交接文件，验证其大小/哈希。记录固定下载位置：
`https://raw.githubusercontent.com/wmqfl861/polymarket-alpha-lab/<DELIVERY_SHA>/handoffs/wp06-backup-catalog-20260916/implementation.patch`
其余清单与审核材料同前缀。不要把模板中的 DELIVERY_SHA 当真实SHA。

只有上架成功和回读通过后，才继续以下正式应用步骤。如果写入/授权/API受到拒绝，保留原错误并停止；不要换身份、关闭安全限制或修改用户业务库来绕过。

## 2. 从固定 GitHub 交接版本应用，不覆盖业务安装
另开全新 SOURCE 工作区或干净的独立 Git worktree，起点仍为上述 main SHA。不要把 handoff 目录合入功能树。
创建 `feature/wp06-backup-catalog-20260916`，从刚验证的 GitHub DELIVERY_SHA 获取补丁到工作区外，再次核对补丁字节数和 SHA256，然后：
```powershell
git apply --check --index <VERIFIED_PATCH_PATH>
# 检查退出码，成功后才执行下一条。
git apply --index <VERIFIED_PATCH_PATH>
git diff --cached --check
git write-tree
# 必须等于 87486a77918a1eef57e19b7960032211a8578ab9。
git diff --cached --name-only
```
恰好九条路径：
`.github/workflows/native-postgres.yml`
`DELIVERY_PLAN.md`
`database/README.md`
`database/quickstart.md`
`src/polymarket_alpha_lab/project_postgres/backup.py`
`src/polymarket_alpha_lab/project_postgres/cli.py`
`tests/test_project_postgres_backup_native.py`
`tests/test_project_postgres_backup_catalog.py`
`tests/test_project_postgres_backup_catalog_review.py`

按 source-manifest.json 再核对每份最终文件。任何差异、冲突或额外文件都停止；不得强制/部分应用，不能顺手改代码来凑树。保留原67份迁移、备份格式和默认严格拒绝；--allow-catalog-extension不授权启动、迁移、覆盖或降级数据。

## 3. 正常提交和最终 CI；不代替协调者合并
读取补丁做单独只读复核，记录其固定 tree、已执行本地结果及未执行项。无需重跑协调者已完成的同树Linux测试；若本地有额外提交门禁则遵循且如实区分结果。不得禁用hooks。正常提交九条路径并非强制推送，确认远端 head/tree 与本地一致。
用正常已配置的 GitHub CLI/网页创建草稿PR，base=main，标题建议：
`WP-06: explicit cold-backup catalog-prefix compatibility`
PR说明引用固定交接提交、基线、候选tree、REVIEW.md；明确默认行为不变、没有用户数据操作、Windows尚待验收、G6/D1-D3未关闭。

等待并检查这个确切 PR head 的四条适用流程：Offline verification、Native project PostgreSQL、Research paper bridge、Native project distribution。不能用旧SHA通过代替，不能只看绿标。下载原始日志/JUnit并检查最终源码保全步骤。
新原生用例必须实际运行且不是skip：
`tests/test_project_postgres_backup_native.py::test_native_old_backup_restore_requires_explicit_catalog_extension_and_migration`
它只允许在 CI 自建隔离实例中执行；检查66迁移原备份→67目录、默认拒绝、显式许可、原数据存在时拒绝、缺失目标恢复、实际pending账本阻断、显式1次迁移及原成功/失败记录保全。
两个新单元测试模块的35项也必须在Windows执行。保留首败；不预热、不增加原超时、不减少断言、不自动重跑失败CI。任何失败、未知状态、超时或旧主线变动均如实停止并报告。不要在用户旧kit/业务库启动原生测试来替代CI。

返回：DELIVERY_SHA及完整文件路径/固定URL/回读结果；PR编号/URL；源head/tree和base；每条CI的run/job/head、通过/跳过/失败数；新原生用例执行结果；原始日志和artifact ID/哈希；工作树状态；独立复核结论及全部未执行项。不得上传包含凭据的冷备份、数据库目录或口令。保持PR草稿、不要合并main，由协调者读取最终证据再复核。
