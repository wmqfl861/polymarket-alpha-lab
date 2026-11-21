# P1-4 第一处隔离复现

2026-11-21 在受审基线 `2bdc4cccacc925ea8e6569c8865b90c0b0c671e6` 上执行。本机 ZCode 启动每轮验证。每轮使用新建的隔离 PostgreSQL 18.6 实例，运行后验证临时实例已清理。

| 次数 | 回执字节数 | 完整本机回执 SHA-256 | 结果 |
| --- | --- | --- | --- |
| 1 | 5,268 | `cf4d24c881343997588e824b14ba3ade996c3831ff4ce40cc261149044cc3c75` | `EXPERIMENT_CONDITIONS_UNMET` |
| 2 | 5,938 | `254d217cf0601066e8ad49bcdb552317ae7815dc3178658691cbec6dd84cd063` | `EXPERIMENT_CONDITIONS_UNMET` |
| 3 | 10,892 | `ed76568b51689ad4ffcbce0b94bf52970b2e47a3db17c7291fcca6ee213680b1` | `REPRODUCED` |

前两轮用市场包支持的数组形态输入 `variant_quotes`。扫描在写入候选时失败。第二轮确认 `store_psycopg.record_scan` 将 `double precision[]` 参数转为 `jsonb` 失败，SQLSTATE 为 `42846`。此缺陷单独登记。

第三轮沿用现有市场包生成器的 JSON 文本序列化，两种形态解析后的报价数据一致。扫描写入一条俱乐部再校准候选的影子记录，拒绝原因为 `fit_materials_missing`，概率为空。同一查询按扫描实际出价版本 `soccer-r23` 关联到 1 条拒绝行，按登记现役版本 `p4r1+6dc3f30fed0d` 关联到 0 行。两种版本口径下的可评分配对均为 0。只读 `optimize score` 输出市场数和事件簇数均为零，证据时间为空。登记完成后，扫描和评分新增角色事件与前向判定均为零。

复现覆盖固定基线、指定谱系和单场隔离输入。两次失败和一次成功的原始回执保存在本机。
