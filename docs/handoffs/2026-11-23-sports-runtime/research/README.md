# 未完成研究线的源码

补丁用 `git diff --binary --full-index` 从各自的公开合并基线生成。在对应基线上应用补丁可还原原分支的完整文件树。`sources.tsv` 记录完整源 ref、源提交、基线、目标 tree 和补丁 SHA-256。FX-40 与 FX-44 的基线是 PR #118 的合并提交，FX-41 NBA v2 的基线是 PR #122 的合并提交。两个基线均可从已上线 `main` 提交 `ab6edff2d64ac8aa5c2831d748b8730b63f8312d` 取得。[源码清单](sources.tsv) · [公开主线](https://github.com/wmqfl861/polymarket-alpha-lab/tree/ab6edff2d64ac8aa5c2831d748b8730b63f8312d)

| 研究线 | 已保留的代码 | 仍待完成或验收 |
| --- | --- | --- |
| FX-40 市场准备 | 从登记快照重建市场、盘口和模型输入，按选定的足球或加密模型规格生成市场包，记录来源证据和无法构造的目标。包含两个模块及对应测试。[补丁](fx40-prepare.patch) | 完成 CLI 和 `run-scan` 正常入口接线、登记输入的生产。核对空注册表下四个冻结夹具逐字节一致，验证重复准备同一批快照及受管并跑能产出带模型判断的候选。 |
| FX-44 收盘价与观察绑定 | 增加可按登记规则开启的开赛前或到期前 60 分钟窗口、降级原因汇总、观察绑定迁移草案及 59、60、61 分钟边界测试。窗口下界含、上界不含。[补丁](fx44-observation-bindings.patch) | 将逐次采集的规则登记及激活接入实际取价路径，接完存储写入与绑定校验，补登记规则版本。以受管采集验证早到观察降为普通标记、标准 C 计为缺失，最后一条合格收盘价参与评估。 |
| FX-41 NBA v2 | 保留双季历史日程、输入与清单登记、受管运行及检索、试验网格与原生回归草案。受管日程包括 17 个规格和 4,811 次物理试验。[补丁](fx41-nba-v2.patch) | 最终源码复审、受管耗时验收及真实快照登记仍待证据。登记日程、输入、种子与网格后完成受管历史运行，核对完整留出窗口与比较结果并归档新记分卡。原 NBA 运行 ID 3 的 `INSUFFICIENT_DATA` 是历史结果。 |

FX-40、FX-44 的具体验收口径和 FX-41 的留出规则见既有进度审核。NBA 历史记录和先登记再执行的批准状态见公开账本 §87.3。[整改与下一步](https://github.com/wmqfl861/polymarket-alpha-lab/blob/ab6edff2d64ac8aa5c2831d748b8730b63f8312d/docs/handoffs/2026-11-21-progress-review/02-remediation-and-next-steps.md) · [公开账本](https://github.com/wmqfl861/polymarket-alpha-lab/blob/ab6edff2d64ac8aa5c2831d748b8730b63f8312d/DELIVERY_PLAN.md)

## 校验与恢复

在 Linux 上执行 `bash research/verify.sh`。脚本匿名克隆公开 `main`，对各自基线建立干净检出，验证补丁 SHA-256，依次运行 `git apply --check --index`、`git apply --index` 和 `git write-tree`，将写出的 tree 与清单逐项比对。三个目标已在这一流程下全部匹配。校验范围是 Git 文件树还原，研究运行与数据库验收仍待各研究线完成。

如需保留恢复后的工作树，可在各自的新检出里应用单份补丁。例如从交付包根目录恢复 FX-40：

```bash
patch_file=$(realpath research/fx40-prepare.patch)
git clone --no-tags --single-branch --branch main https://github.com/wmqfl861/polymarket-alpha-lab.git pal-fx40
git -C pal-fx40 switch --detach 00199b24d38d5d9334ec7eb07ca8141db33852da
git -C pal-fx40 apply --check --index "$patch_file"
git -C pal-fx40 apply --index "$patch_file"
git -C pal-fx40 write-tree
```

以 `sources.tsv` 中 FX-44 或 FX-41 NBA v2 对应的补丁名、基线及目标 tree 可同样恢复另外两条研究线。补丁保留完整文件树，原提交身份、父提交和历史由原始 refs 保存。
