# 未发布候选：旧冷备份的迁移目录前缀兼容

这不是已合并版本。仓库写入与最终 Windows/CI 验收尚未完成。
请先阅读 `REVIEW.md`、`candidate-identity.json` 和测试日志。

- 固定源代码基线：4df08873f4b9b8f1378913c0d00ca6bb183e51e2。
- 预期补丁结果树：87486a77918a1eef57e19b7960032211a8578ab9。
- 补丁：implementation.patch。
- 逐源码文件内容：sources/ 下对应九条仓库路径。
- 逐源码哈希：source-manifest.json；整个包的载荷清单：PAYLOAD-MANIFEST.json。

不得覆盖已有 kit，不得对用户数据库执行 backup/restore/migrate/init，
不得复制或删除 .local，不得上传实际备份、口令或凭据。
本候选只允许在固定基线的新 SOURCE checkout 中审核与测试。

本轮无法完成 GitHub-first 上架；不要把此附件称为仓库中已有的交付文件。
上架补救应先创建独立 handoff 分支，上传补丁、清单与审核证据，固定提交后
从 GitHub 回读校验。正式应用补丁及验收随后才使用该固定 GitHub 版本。
不自动合并 main，不把本地通过当作 Windows 原生通过。
