# evidence.md — 2026-11-21 进度交接包逐项证据

约定：每项给来源（冻结登记字段 / GitHub 对象 / 操作员本机回执指纹）、完整提交或运行号、复核命令、适用范围、字节数与 SHA-256（如有）。【事实】= 登记值或可直接复算；【推断】= 由事实推出并注明依据；【缺证】= 未交付或不可复核，附补证条件。快照时刻 2026-11-21T04:50:20+00:00。提交 URL 模式：`https://github.com/wmqfl861/polymarket-alpha-lab/commit/<完整SHA>`。

## 1. 第一父链（B..M）

从正本仓库的固定 Git 对象重建第一父链。

```bash
git log --first-parent --reverse --format='%H %P %s' c4367b6b3ce4d957ce79ce645a7ab3425be5a133..2bdc4cccacc925ea8e6569c8865b90c0b0c671e6
```

【事实】期望输出 44 行合并提交，顺序与下表 merge 列一致；`git show -s --format='%P' <merge>` 的第二父应等于同行"集成头"。

| PR | 节点 | 合并提交（第一父链） | PR 头（第二父） |
| --- | --- | --- | --- |
| [#69](https://github.com/wmqfl861/polymarket-alpha-lab/pull/69) | 固定审核交接包 | `e25103155015a73b0584eba58b1ffa6d001672cc` | `cffc38631bd4df4d9f52073b5b4593066c973e00` |
| [#70](https://github.com/wmqfl861/polymarket-alpha-lab/pull/70) | MO-0、登记与蓝图 v4 | `81eb20e3566e0ded57589814b2f57b460991dfb7` | `7649ad1416c3bf1fafa9cce6e92cc83a277dd9b6` |
| [#71](https://github.com/wmqfl861/polymarket-alpha-lab/pull/71) | FX-27 和 FX-30 覆盖分母 | `7a2605e7402491dceb41839bc4a1d2fc31a47b78` | `ae7b675b8b56a68ca5cf010871d47bf79e858e6e` |
| [#74](https://github.com/wmqfl861/polymarket-alpha-lab/pull/74) | FX-28 库外依据 | `9bfafeda670211192669320c11f65529a3e8e030` | `f182e48d41ecf0178e1a63e0b9894d09458e92d4` |
| [#76](https://github.com/wmqfl861/polymarket-alpha-lab/pull/76) | MO-1 模型规格 | `e5c8ae8a875dece6169c6491eece90bdafc5451a` | `24ccf040b91be08086e34910428247fc235fdc2e` |
| [#77](https://github.com/wmqfl861/polymarket-alpha-lab/pull/77) | FX-26 比赛键和开赛时间 | `d37650ca5daccacf71ace4e42f18615d70b32bea` | `a3a14e91ebbd0d056243aa43a72cad46c56cf695` |
| [#78](https://github.com/wmqfl861/polymarket-alpha-lab/pull/78) | 首批交付登记 | `a17d1fe2b3160511077272951bf6f952a08d82a3` | `8a59516769dd649b390ee9f0bde82c28e506adc5` |
| [#79](https://github.com/wmqfl861/polymarket-alpha-lab/pull/79) | MO-4 数据集 | `4e6ffa871e9a0062e3b752855c6515dcffb8575c` | `2f2cd5b3bd3ed577fd76b9c2a8f0432f2dbf0243` |
| [#80](https://github.com/wmqfl861/polymarket-alpha-lab/pull/80) | MO-5a 俱乐部足球 | `d148e91a8091e8b8f5569ded340c9bdce3bd73c7` | `905b0c881b11b8c584356840c9fb01afa9601e2b` |
| [#81](https://github.com/wmqfl861/polymarket-alpha-lab/pull/81) | M2 B1 扫描 | `8ca60c2781fc4806ca14c468ab9f14656e8dd999` | `2f6f2683ceb2f119acf1895acf21449eb0aba872` |
| [#82](https://github.com/wmqfl861/polymarket-alpha-lab/pull/82) | MO-5c NBA | `647b81784465b6ce3352f601b5a8124dec0c8b26` | `3d4b8fda00e008ff5aa4723de75fe69e24cb5bd8` |
| [#83](https://github.com/wmqfl861/polymarket-alpha-lab/pull/83) | MO-2 受管注册表 | `d95fadecac6da0ce1e18d96234cd89b45565ab63` | `b8fa5d2ad790beee6277813869f7664ec461212f` |
| [#84](https://github.com/wmqfl861/polymarket-alpha-lab/pull/84) | MO-5b 国家队 | `3e52516f71fdadeb86ef0d521521e6384ad9bb68` | `21a5870cdfdd8144ad70f1958d83a63f17ac5bd4` |
| [#85](https://github.com/wmqfl861/polymarket-alpha-lab/pull/85) | MO-5d 加密 | `86feaf1ad88be3f9c262a26cdac650cb26c1a573` | `dd1b8f36da1b46805632c398dd6fc36f1247c19d` |
| [#86](https://github.com/wmqfl861/polymarket-alpha-lab/pull/86) | MO-3 评估和回归 | `fa01be0b6b7cc0d01886f5aee40456c4d3b726bd` | `c47f2105c3baaa7d7144408ada576a54fb538c76` |
| [#90](https://github.com/wmqfl861/polymarket-alpha-lab/pull/90) | MO-3 配对和留出证据 | `8ca119802d0a08333a0022aaa65b0cfce418eb15` | `621085de60594eefb805ce6fc2a4c88ff76f6509` |
| [#91](https://github.com/wmqfl861/polymarket-alpha-lab/pull/91) | B2 取价与赛前 CLOSE | `6c549f267edd9cd915476e572e6f3547df68aefb` | `2ed0c948cf7681e99c3e0b52513aa175abe85330` |
| [#87](https://github.com/wmqfl861/polymarket-alpha-lab/pull/87) | MO-5e 跨联赛强度 | `82f6e32c5cadf43b747df2aac56ff2f30ab310ac` | `a13a9682767408612e1395e62bfdcbb7146792be` |
| [#88](https://github.com/wmqfl861/polymarket-alpha-lab/pull/88) | FX-31 地址卫生 | `ed5eabbd4ff01b1d10a4798e34f505d6186cc57b` | `11e030c72bc4485420b2f04b0eec39e8bf4e783a` |
| [#89](https://github.com/wmqfl861/polymarket-alpha-lab/pull/89) | FX-33 过期文字 | `fe5ae538420e3fe060cd1fed2c12121cbd8301ba` | `c06f5e2bb0d93e58e72009268ee8f359513bce23` |
| [#92](https://github.com/wmqfl861/polymarket-alpha-lab/pull/92) | B3 初次结算 | `f83567eac1f3476e8ccdc8249afda1d23daf9f13` | `3f6fd68dd6978dd1d361307716284e46a269e8d0` |
| [#93](https://github.com/wmqfl861/polymarket-alpha-lab/pull/93) | G7 受管事务修复 | `e77e9075dc1affd2249c08f7184ca7f4c01a4859` | `7b98692b2c4d71cb23da237c3f675a63d8adf4d7` |
| [#94](https://github.com/wmqfl861/polymarket-alpha-lab/pull/94) | 派工规则及地址卫生记录 | `3f2142ff233d75238ec749736182898431f57109` | `825bee041fc1064ff952917baa8303c675e7ce60` |
| [#95](https://github.com/wmqfl861/polymarket-alpha-lab/pull/95) | WP-P4 受管读取兼容性 | `bb1e056a5ddaed1b2f366b7260bef1808d5cc24a` | `bbedc0eaf212adb2f34d4b893de07127a8ff1cec` |
| [#96](https://github.com/wmqfl861/polymarket-alpha-lab/pull/96) | MO-7 影子 | `ad381fb5ca6f5903d4b7b6619a85bff888a649a1` | `8b72f470fc1d546fae53b25dbd5ba49b2341c4ac` |
| [#97](https://github.com/wmqfl861/polymarket-alpha-lab/pull/97) | 第二批交付登记 | `033481b57ac334d63c516090522e216d077f0031` | `e48ebed8b3f8ee2654c030fd228845f5b3956490` |
| [#98](https://github.com/wmqfl861/polymarket-alpha-lab/pull/98) | FX-18 归档清点 | `ca7640a244d8a4d399aeb1ec3c1246c0ca05f08a` | `f1bdad24caf9dcf742e8bb4938fd423d7af751b5` |
| [#99](https://github.com/wmqfl861/polymarket-alpha-lab/pull/99) | MO-8 判定和角色 | `f3f86ae5cffde2ec11bd51e543b2c470a635ad1b` | `7afe487ebf74e651ad72c82533966ae55835a737` |
| [#100](https://github.com/wmqfl861/polymarket-alpha-lab/pull/100) | 受管 CLI 提交持久性 | `a066e965179901b99b4ef6f71e47080dcc12c5c4` | `a0cf0aa02678f707c24af572622f11e8b533ff9d` |
| [#101](https://github.com/wmqfl861/polymarket-alpha-lab/pull/101) | FX-25 Linux 开发规则 | `e899408813881698069218cd9b1537d2bc5e4821` | `2f176304d1e61a61e7e3b044e4ff21bc23c1cf72` |
| [#102](https://github.com/wmqfl861/polymarket-alpha-lab/pull/102) | 受管规格读取事务 | `dfd28e47624c703f427b56e058dc0da08be59d73` | `cf12b1c7f1145f37332e38b254a3adb11997bd01` |
| [#103](https://github.com/wmqfl861/polymarket-alpha-lab/pull/103) | FX-25 合入记录 | `3cd9a354966ba10142fbe4a186eb3166a4875d4f` | `fa0847eb435fadfe881afaa9ce110e7478778480` |
| [#104](https://github.com/wmqfl861/polymarket-alpha-lab/pull/104) | B3 逐笔费用与赛果 | `a666759d6fc3b0a44c04b9fc148a39b80efade2b` | `692495af7f8add1019a5d7a586e9082db7fc78db` |
| [#105](https://github.com/wmqfl861/polymarket-alpha-lab/pull/105) | MO-6 运行器和搜索 | `193205fd773e8e551aa51d2310914da3cc8df5ab` | `9de2ce65d1f94a27238c295933b028a16f5eae68` |
| [#106](https://github.com/wmqfl861/polymarket-alpha-lab/pull/106) | FX-35 标准 F | `fd23a76b0e5a0adf6d784dff43b20b39482e7d76` | `9f0c28a5a5deecee71c311de6431f1835c9fc442` |
| [#107](https://github.com/wmqfl861/polymarket-alpha-lab/pull/107) | MO-6 运行边界 | `4323b62b9da64dbc2f4ab02c7722e83ebbea2805` | `ce5d3dda95882735361f7349934f284c97e2df6c` |
| [#108](https://github.com/wmqfl861/polymarket-alpha-lab/pull/108) | B4 报告快照及加时终场 | `026035e6f668a0c72c43990008be75fc7420adb8` | `f30361c00583019309e6c09ad1fe7dae11fb7b42` |
| [#109](https://github.com/wmqfl861/polymarket-alpha-lab/pull/109) | MO-8 空前向样本 | `77acea8ca8f525b74ba7e5a37feb371a1047c2d8` | `6bd9e7702f69e6b2e23ffa1048c98afbdcbea6eb` |
| [#110](https://github.com/wmqfl861/polymarket-alpha-lab/pull/110) | D1 冻结归档 | `5d49666757d520ff55832338f856a91dd761affe` | `3d65f64199bcfafc2f23d8bfa4020d98026df450` |
| [#111](https://github.com/wmqfl861/polymarket-alpha-lab/pull/111) | FX-35 逐笔读序 | `af4ff553262a7ee532e36e1b0d8ba4939522f4ba` | `919234b61f8e23d7e7140ebc09497031d0200aaa` |
| [#112](https://github.com/wmqfl861/polymarket-alpha-lab/pull/112) | D1 公开市场与价格取证 | `63506df2e2470e548e100041cdacf9db6f401fc8` | `a7c70ba2a15ac90586cd329f7655c12a7d6fc201` |
| [#113](https://github.com/wmqfl861/polymarket-alpha-lab/pull/113) | FX-35 预登记重放 | `11fb457c6e714844ec2ee1c0eb3c90dbd145eaa7` | `8dc40d09d1c0591489dfcc77bcebadbc137cc3ae` |
| [#115](https://github.com/wmqfl861/polymarket-alpha-lab/pull/115) | D1 同一实例预检 | `8cba9d37bab50baaf777104cccfdf3a9afd628cd` | `39c6c2e6fa5096dd0442301ef519a66fd244f0b0` |
| [#117](https://github.com/wmqfl861/polymarket-alpha-lab/pull/117) | 现行 ZCode 审核规则 | `2bdc4cccacc925ea8e6569c8865b90c0b0c671e6` | `8d41cba5504b0737bd2af457584ac520ff8e7c45` |

【事实】M=`2bdc4cccacc925ea8e6569c8865b90c0b0c671e6` 即 #117 合并，为快照时 main 顶端。

## 2. 开放 PR 与 CI

### 2.1 #114（MO-6）

【事实】状态（冻结登记 `mo6_status` 原文）："open; latest green jobs checked old 0e9210f0 based on 8cba9d37; current GitHub merge ref a0086b82; original reviewer allowed normal integration retry, no newer result"。PR：https://github.com/wmqfl861/polymarket-alpha-lab/pull/114

两端固定差异复核命令：

```bash
git diff --name-status 2bdc4cccacc925ea8e6569c8865b90c0b0c671e6 26dfa2a05357121665009981cd600e23c91e093a   # 重开快照基（=冻结 main）
git diff --name-status 11fb457c6e714844ec2ee1c0eb3c90dbd145eaa7 26dfa2a05357121665009981cd600e23c91e093a   # 历史快照基（登记值）
```

【缺证】本包未附文件级 name-status 清单（冻结登记未含）；接收方按上列命令复算并独立核对。

CI（来源：冻结登记 `mo6_ci` 两轮快照；作业日志指纹为抓取时点值，活体变化另行记录）。两轮快照自身指纹：历史 18,095 字节 / SHA-256 `116158fb18145f7f697bbed6dd81fa60544e44c74c9dfb5a848b49c77d3592b6`；重开 20,435 字节 / SHA-256 `58a76d383c97f093697340b39bc942289c0c61fb78fa6f5d8e25d2d0bded939c`。

历史快照（pull_refs：#114，head=`26dfa2a0…`，base=`11fb457c…`，base_ref=main）：

| workflow | run id (attempt/事件) | 作业 | job id | 结论 | 实际 checkout | 日志字节 | 日志 SHA-256 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Offline verification | 44853537834 (1/pull_request) | offline | 136690239786 | success | `0e9210f0…` | 160400 | `c17dd618b346f74df6de10483e2b933a03f54df81c0896cc93d0dffe90e46f9e` |
| Native project PostgreSQL | 44853537814 (1/pull_request) | linux-native-storage | 136690239634 | success | `0e9210f0…` | 73201 | `f9208759ddb4a7a70ad6c97a254040fd0fb761bc6d959d06bf6891449cd38da7` |
| Native project PostgreSQL | 44853537814 (1/pull_request) | linux-native-research | 136690239924 | success | `0e9210f0…` | 86878 | `441fb33c795fa4851ebd16ea56ab1b7576c02a6d034b007669599440338eea81` |
| Native project PostgreSQL | 44853537814 (1/pull_request) | linux-native-papersim | 136690240016 | success | `0e9210f0…` | 92169 | `52c4e534665c387c13edf1eed364b06719f85c1fd70582729dc62fbebd189c2b` |
| Native project PostgreSQL | 44853537814 (1/pull_request) | linux-native-dispatch | 136690240190 | success | `0e9210f0…` | 78160 | `bcca5643166c421f3f74a26f0bb74f583457378cb496c0128fec499b87548798` |
| Research paper bridge | 44853537975 (1/pull_request) | windows-paper | 136690240627 | success | `0e9210f0…` | 41016 | `95042130f7c7c00fe764b0d709df96f9fa4eeb69b35ba39cfb7a29d2017b0d7c` |

重开快照（pull_refs：#114，head=`26dfa2a0…`，base=`2bdc4ccc…`，base_ref=main）：

| workflow | run id (attempt/事件) | 作业 | job id | 结论 | 实际 checkout | 日志字节 | 日志 SHA-256 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Offline verification | 44859358224 (1/pull_request) | offline | 136708906590 | success | `0e9210f0…` | 160545 | `a5d27897b0f78e0ca98616a6551b0db53da3f1030f3864f20513cf49a5ab37e6` |
| Native project PostgreSQL | 44859358299 (1/pull_request) | linux-native-storage | 136708907087 | success | `0e9210f0…` | 73361 | `c89cba1da225df0e4ca55624497fa3ef5eebdd3d9ac4e8ecae66f4bf49f13657` |
| Native project PostgreSQL | 44859358299 (1/pull_request) | linux-native-papersim | 136708907271 | success | `0e9210f0…` | 92267 | `4b308ba87f28b4554303d6c0c42367759394e194c4de260e50814dbdf15b57f0` |
| Native project PostgreSQL | 44859358299 (1/pull_request) | linux-native-research | 136708907283 | success | `0e9210f0…` | 87049 | `42512afaf81e974d539a3e168e5ebbeb1a3423e5148e1974e471817a868de2f6` |
| Native project PostgreSQL | 44859358299 (1/pull_request) | linux-native-dispatch | 136708907314 | success | `0e9210f0…` | 77933 | `c985bf428323cdca41675e213c91bf4a1bf3c70bb8b7165e7de4d27372891ae6` |
| Research paper bridge | 44859358236 (1/pull_request) | windows-paper | 136708906617 | success | `0e9210f0…` | 41212 | `c54b5fa8a7227492f09b01ca5a910e35093e73eaab8b33ab18459e134dd06915` |

【事实】检出对象 `0e9210f0206af1259c8a8cc3a74358c4a59bc236`：树 `d4931550e299c4a09e7c33c03817dfb6aea1178a`，双亲 [`8cba9d37bab50baaf777104cccfdf3a9afd628cd`（#115 合并）, `26dfa2a05357121665009981cd600e23c91e093a`（#114 头）]，登记 `base_verified: true`。
【推断】两轮 head_sha 均为 `26dfa2a0…` 的绿作业，实际检出的是基于旧 main（`8cba9d37…`）的旧合并对象 `0e9210f0…`，未覆盖当前 GitHub 合并参照 `a0086b82…`；依据为上列 checkout 与双亲登记。故 MO-6 功能出口仍待核，与 `mo6_status` 一致。

```bash
gh api repos/wmqfl861/polymarket-alpha-lab/git/commits/0e9210f0206af1259c8a8cc3a74358c4a59bc236 \
  --jq '{sha, tree: .tree.sha, parents: [.parents[].sha]}'
git fetch https://github.com/wmqfl861/polymarket-alpha-lab.git refs/pull/114/head
git cat-file -e 26dfa2a05357121665009981cd600e23c91e093a^{commit}
```

### 2.2 #116（FX-18）

【事实】head `8c7b536514eda6dfc772bf7fc6a4fc597892a294`，base `11fb457c6e714844ec2ee1c0eb3c90dbd145eaa7`，状态 open；登记效果：两个文档/状态文件标记 B1 候选待清理。PR：https://github.com/wmqfl861/polymarket-alpha-lab/pull/116

【缺证】文件级清单按命令复算后独立核对：

```bash
git diff --name-status 11fb457c6e714844ec2ee1c0eb3c90dbd145eaa7 8c7b536514eda6dfc772bf7fc6a4fc597892a294
```

【事实】三条绿运行（均 attempt 1、success、head `8c7b536…`）：Research paper bridge run 44850542816（pull_request）；Offline verification run 44850543464（pull_request）；Native project PostgreSQL run 44850545864（workflow_dispatch）。

## 3. 三族真实历史记分卡（原审归属）

来源：冻结登记 `historical_scorecards`。三族共同值【事实】：原运行代码提交 P=`e234c3364c204db513cc4fbcdcb6d5eb43c7417b`；seed=20261120；rule_version=v1；exit_code=0；`opened_holdout_lineages`、`registered_lineages` 均为空；`holdout_callbacks`=0；`has_unknown_predecessor_progress`=false；`known_duplicate_starts`=0。正式历史运行档案：88,845 字节，SHA-256 `03c616e1d94efe5e3a7879e3277645b5d60e4e407bdef6e9d31d9f7eb3366ad5`。

| 字段 | club-soccer-recalib | intl-elo-goal-residuals | nba-pace-efficiency |
| --- | --- | --- | --- |
| optimization_id / 事件区间 | 1（事件 1→2） | 2（事件 3→4） | 3（事件 5→6） |
| outcome | INSUFFICIENT_DATA | NO_IMPROVEMENT | INSUFFICIENT_DATA |
| data_cutoff | 2026-11-10T10:33:51Z | 2026-10-07T00:00:00Z | 2026-11-10T05:00:00Z |
| schedule_id | mo6-club-official-round-suffix-v1 | mo6-intl-complete-day-suffix-v1 | mo6-nba-espn-november-suffix-v1 |
| holdout 合格谱系（块/场） | match_winner 1/57；over_under 1/57 | over_under 227/1803 | moneyline 8/66；totals 8/66 |
| 地板（min_blocks/min_events） | 20/300 | 0/300 | 30/300 |
| holdout 实际（块/事件） | 1/57 | 227/1803 | 8/66 |
| 评估/完成试验 | 65/65 | 864/864 | 10/9（空试验 1） |
| 唯一评估规格数 | 1 | 36 | 1 |
| 物理起点下限 | 65 | 864 | 10 |
| search_folds / search_predictions | 65 / 2556 | 24 / 191 | 10 / 144 |
| manifest（字节 / SHA-256） | 7592 / `2fe5130806a15469d0752b2c8492d9f4885e232b8ce96a42731e130844cd9bca` | 953 / `be87b6cbc808421b5ebf76841262dfabfb14988667da521ab8c9f5cbc3384364` | 1478 / `092ee74ae9ef5075173321f9ed753af862a934013ac0f8a1b58f1a412032970d` |
| artifacts（字节 / SHA-256） | 15711059 / `9eda4a4d4bf3d47b9bd7440f12fef1f82662caac1687497e8e15ba09ed0d07f1` | 28037573 / `2e265e4c090e131da540fa6fda2beaaf1944c37dfff14959e6e70523443bd0c7` | 922617 / `9fa2d6ae18986d0f896575124da1c5df27abfcb194822e4bddd7117d58e9e3dd` |
| scorecard SHA-256 | `b27ca7855ae147d27c76537289fd904c61f1fdb95be28fb5815cbd5f12a34fb9` | `e47b518723c52ac369562af6a11b85ae24cb4c62fb6262e6ae298784a91e3d65` | `8d7b2a4f498aa99ae20349d6744657c37a785ea30d097e19f44ddc743d57fe10` |

【事实】限制：club-soccer 与 nba 未达 holdout 地板（1/57 对 20/300；8/66 对 30/300），结论为数据不足；intl 达标（227/1803 对 0/300）但无改进。三族均未开留出、未登记谱系、无 holdout 回调。
【缺证】scorecard/artifacts 原件不入本包（体量与边界）；可独立复算范围=正式档案指纹+本表数值；补证条件：在受控环境按档案与清单重放。

## 4. MO-6 本机证明（操作员侧，非 GitHub 对象）

来源：冻结登记 `mo6_local_proofs` / `mo6_local_parity`。仅为指纹与计数；原件留在操作员本机回执库，不入包。

| 回执 | 头 | 关键结果 | 字节 | SHA-256 |
| --- | --- | --- | --- | --- |
| linux.json | `26dfa2a0…`（head_before） | 全量：43771 passed, 410 skipped, 20 warnings，3807.50s，exit 0 | 1976 | `ff34831860cd1bbb0f3962fd5f660da20ce35a9a272f6fc2aed3a51e87726f17` |
| native/papersim.json | `26dfa2a0…` | 617 passed / 0 fail / 0 skip，exit 0 | 113932 | `96b73aefb2b03e2fcacadd9171b51d8a1974cfa5e895d616e9a8c5cd59d43a6a` |
| native/version-switch.json | `26dfa2a0…` | 89 passed / 0 fail / 0 skip，exit 0 | 18475 | `b5b4d0bea2a9c64695c529fb1f0638e86aa7970036ddf5a3fb71fd77062668f5` |
| pair/focused-pair.json | `26dfa2a0…` | 79 passed / 0 fail / 0 skip，exit 0 | 16738 | `2a3e2825815b438a7f9f23b1f233bd929ade66a9d28eb02b37470e5af0f11164` |
| anchors/anchors.json | `26dfa2a0…` | 6 项锚点全过；压力 equal：4 登记 × 200 种子，72.076s；better：200 × 200，241.036s；group3 快照 4,345,243 字节 / SHA-256 `51d7da0e6dd09176b8c6141ba1fd99dc7e63e0995ea4a354f6b5fc1c41ce7899` | 54736 | `3a8660bef558c2592a283683d0bf0d6d6125af9e5d26873c51c71f6590cb25fc` |
| verified-parity-and-historic.json | `26dfa2a0…` | 空注册表逐位一致与历史核对登记 | 4181 | `752d2b5a26d5cd17f1980711437e9653eb71b591efc0a4f3be9777b8ff295613` |
| final-independent-review.json | `26dfa2a0…` | VERDICT: PASS；原审者 mo6_managed_final_review_sol（gpt-6.2-sol，#117 前旧路由，保持原归属） | 2029 | `9ec6828e4a9fd20b41e3db652aa5536a136b5db1c194a31d7feb937c1011e569` |
| signed/receipt.json | `26dfa2a0…` | 签署回执登记 | 4308 | `99e79448609a76f1b928bd83b4d99071ff5b6340b0100004fbbe854534b53baa` |

【事实】锚点（全部 passed）：`tests/test_papersim_optimize_metrics.py::test_anchor1_new_soccer_30_cells_brier`；`…::test_anchor1_reproduces_byte_identically_on_rerun`；`…::test_anchor2_nba_group_logloss`；`…::test_anchor3_wp_p4_paired_comparison`；`tests/test_papersim_optimize_runner.py::test_empty_registry_parity_executed_twice_exact_counts`；`tests/test_papersim_evaluation.py::test_ledger_snapshot_reproduces_published_audit_numbers`。

【缺证→可复现】上述测试可由接收方在 `26dfa2a0…` 检出上复跑（`git fetch https://github.com/wmqfl861/polymarket-alpha-lab.git refs/pull/114/head`）；本表计数仅作对照值，接收方复跑结果独立成立。

命令指纹（`mo6_local_parity`，仅指纹；原文与原始输出不入包）：verified 534 字节 / `edc8c8496533f75f457be4992241f26fe56e486b07b03003b50b687cf3763eec`；run-scan 过程 808 / `6c20f333fe1ba7ec6d597d0bafff134e803c8bb10bf837f5080d30b7ce553eaa`、stdout 578 / `db1380c39a1a5018a1abde4ce97a91f910cd80a3f677fcb563d37c1a54975549`；replay 722 / `214d0244d865cd1e921a67d37d6318bed0321db7b8940ee89877895ceaca519b`、336 / `4f381d3df9319cf5b9a759fb0ef29838709825e019e31dbbdca27f5d3245decf`；settle 791 / `8f3df8e0df4f19064366fef3de00f63d14d4dc51f3332da9948ea154e7fd08f4`、363 / `02a4ef21dec532e03ea2aa9a78562e9d25147e011333a17f89436a19d07dfbb4`；evaluate 644 / `b0abcd39ab16ea5dacaf71f8e49d398bf39d22fa4a51fad4061e767e799093a7`、4407 / `280f12c48d8443d80c31781529851a3ed3a790f2567e3dc2d0884c0b7f328455`；report 642 / `ed00fec64d8fb6da3f10c802907efd3b8c40b35e5e772563fe8771644960702b`、6351 / `50c9ab8f56a0edf9bbcb6294ea638a060fb8c15c0653735d455049de70a9ab1f`；report_archive stdout 6350 / `d4d110885ab91ed66e65f9b0ee246076a39433e18d1ea275419c43674361feb0`。补证条件：操作员按指纹核对归档，或接收方按本节可复现路径复算。

## 5. D1 已推送候选（PR #118）

2026-11-21 05:03 UTC，GitHub 回读确认 PR 头为 `f09850ba8ef010895d6a959cbeff401fbc706363`，基线为 `2bdc4cccacc925ea8e6569c8865b90c0b0c671e6`。交付包含取价恢复、公开终局原件取证、逐笔模拟结算恢复及节点设计。[PR #118](https://github.com/wmqfl861/polymarket-alpha-lab/pull/118) · [固定提交](https://github.com/wmqfl861/polymarket-alpha-lab/commit/f09850ba8ef010895d6a959cbeff401fbc706363) · [节点设计](https://github.com/wmqfl861/polymarket-alpha-lab/blob/f09850ba8ef010895d6a959cbeff401fbc706363/docs/paper-sim/design/d1-operation-recovery-plan.md)

- 同一候选 Linux 全量 43,517 项通过、358 项跳过、20 条警告。编译、冻结夹具、自检、地址与秘密扫描及 CodeGraph 同步通过。
- 同一候选 PostgreSQL 18.6 隔离聚焦测试 317 项通过、0 项跳过，含加时结算和走盘退款；实例已清理。
- 独立只读 ZCode 核对代码、节点设计和准确提交的本机测试，给出允许推送的 `VERDICT: PASS`。
- [Offline verification 44869068160](https://github.com/wmqfl861/polymarket-alpha-lab/actions/runs/44869068160) · [Native project PostgreSQL 44869067934](https://github.com/wmqfl861/polymarket-alpha-lab/actions/runs/44869067934) · [Research paper bridge 44869068049](https://github.com/wmqfl861/polymarket-alpha-lab/actions/runs/44869068049)
- 【事实】本机回执：`d1_current_focused`（head `f09850ba…`：offline 256 passed、PG opt-in 61 skipped、exit 0；静态复审范围 C/S1/O/A）；`d1_current_native`（317 passed / 0 fail / 0 skip，exit 0；168,348 字节，SHA-256 `3dd691c7beff0cc64586b77c813fe2bfc6c46cb7f869823cdd16dd0583e11a59`）。
- 【事实】先前基线回执（`d1_prior_native`）：capture/papersim 727 passed @ `522827397f7639e11bb3709211b07e836da4753d`（135,542 字节，`2de90f984502b9620641869fb2dbac4973af686082b91ab5a607a0ea75f35557`）；capture/version-switch 89 passed @ `52282739…`（18,476 字节，`ce6721dd11e2ee7be369f2fc9a8082e17826e3c133fb42c4006f0579924238f7`）；ot/settlement-terminal-focused 317 passed @ `1e84d2696516ac7135a21c83ee13708bcca2a0bc`（168,340 字节，`0e7bd3aa80080cc55467b187fa4e5c907e37aada086ce1e449120372f19351c3`）。
- 【事实】变更路径（登记全列 14 项）：`.github/workflows/native-postgres.yml`；`docs/paper-sim/design/d1-operation-recovery-plan.md`；`src/polymarket_alpha_lab/papersim/capture.py`；`src/polymarket_alpha_lab/papersim/cli.py`；`src/polymarket_alpha_lab/papersim/d1compare/acquire.py`；`src/polymarket_alpha_lab/papersim/d1compare/operations.py`；`src/polymarket_alpha_lab/papersim/settle.py`；`src/polymarket_alpha_lab/papersim/store_psycopg.py`；`tests/test_native_postgres_workflow.py`；`tests/test_papersim_capture_marks_native.py`；`tests/test_papersim_d1_operations_native.py`；`tests/test_papersim_d1compare_journal.py`；`tests/test_papersim_settle_store_native.py`；`tools/d1_compare.py`。
- S2 公开赛果登记和三进程联验、七个实际 UTC 模拟并跑日、D2 切换仍待验收。

## 6. 原审 §3.8 六项本地复核（观察时点 2026-11-20、MO-0 实现前）

来源：六项登记文档（`DELIVERY_PLAN.md` §80.9 同源登记）。生产观察与之后的一次性 PG18.6 证明使用不同环境。

| 原审条目 | 已观察事实及证据边界 |
| --- | --- |
| 1 定时任务与最近扫描 | 仅一条相关定时任务，每 30 分钟调用库外 `understat_refresh.py`。该目录无 Git 提交可记录，该轮刷新报 `JSONDecodeError`，没有定时任务调用仓库代码。11-16 最新留证的扫描缺提交号和工作区状态。线上当时无 `papersim.command_run_events`。 |
| 2 11-12 后账户及跨联赛复开 | 11-12 后记录 7 笔入场（#72–74 为 owner 委派，#75–78 为四笔各 50 的校准入场）、8 条结算（含两笔 VOIDED 退款）。现金加占用为 13,768.43 → 14,471.73。11-16 之后截至观察无新增写入。11-24 恢复跨联赛胜负的首笔 `cross_league_mw_check` 当时尚未到期，线上无受管队列表或候选可供留证。 |
| 3 线上迁移与旧账 | 生产应用受管迁移 **0** 条，两个受管 schema 和迁移台账不存在，只有一次性验证库应用过。原复核发现旧行自 11-13 未被重写的行级 `xmin` 证据及两个旧报告视图的 `pg_class.xmin` 等于 §73 基线。既有例外是市场 6509633 的旧结算行复用，先为 VOIDED，11-16 改为 win 且沿用原时间戳。保留原记录和例外。 |
| 4 四类库外计划/复审原件 | 09D 设计 v2、M1 节点计划、M2/M3 节点计划、M2/M3 复审回执分别为「原件缺失」，服务器和本机均未找回，无法比对原登记摘要（前两类的登记前缀 `b88161a1` / `0a48b7bc`）。另存的 FX-08、FX-10、09L、M1 synthesis 是既有执行证据，按实际身份入库。 |
| 5 两份欧洲研究 | 欧洲篮球按公开 README 方法重放，`verification.json` 逐字节一致（比赛 3,083 行、盘口 2,374 行、严格合约 1,600、独立比赛 961），六个保留输出均字节一致。国内欧洲足球的 14 个「联赛×盘口」格、42 个对数损失按发表 4 位精度吻合，`summary.json` 的对数损失最大差 1 ulp，分别保留发布和独立重放摘要。原复核将末位差异归于浮点求和次序（推断，保留原归属）。 |
| 6 正本、分叉与 #68 | wmqfl861 与 Lordakee 是不同的 GitHub 账号。正本属于 wmqfl861。#68 源自 Lordakee 名下的 `polymarket-alpha-lab-contrib`（当时最后推送 11-19），由 Lordakee 发起、wmqfl861 普通合并，合并即 B。旧 `Lordakee/polymarket-alpha-lab` 请求返回 HTTP 404，符合 M0 整改书 §5-5。 |

【事实】欧洲复算指纹：汇总 `section38-europe-retained-output-identity.json` SHA-256 `0388ef32dd801f397334159c44ba6178d569660ea482eeca4a0b664b0ba1226c`；篮球验证 `d0ffe021b75ffdea95014bc77c271e0625609f98b50ade26180d86500876f635`；足球发表 `cbed42762971f55bcdbd4a44a4c022808b83540006632c59a726247423573388`、独立复算 `754b384f39db5e2cab869ed0bb13d717cc6bd72ad237f18dfbac51d661a0328c`。可复算检查：下载 README §6 两个欧洲固定文件并计算 SHA-256，对照上列发表值。

## 7. 派工与角色（现行）

【事实】#117（合并 `2bdc4ccc…`、提交 `8d41cba5…`）生效后的现行路由：计划起草与实质修订 → 原生 Codex gpt-6.2-astra（reasoning_effort=max）；开发、实施、修复（含测试代码）→ 原生 Codex gpt-6.2-sol（max）；其余一切项目工作（计划审核、代码审核、独立复审、阶段/交接审核、调查、测试执行、独立验收、文档清点、交接准备、授权内裁定）→ 本机实际 ZCode 客户端（Linux）。嵌套派工显式指定模型与 effort；快速模式一律关闭。
【事实】历史签字保持原归属：MO-6 终审 PASS（§4）由 mo6_managed_final_review_sol（gpt-6.2-sol）在 按启动该复审时的路由完成。
【事实】本包由实际 Linux ZCode 文档准备者编写，与两次计划会话及独立验证/复审会话分离；`MANIFEST.tsv` 由协调方机械生成。

## 8. 阶段边界与可复现边界

- 【事实】`phase1_checkpoint` 原文："formal first phase exit and docs/handoffs/2026-11-19-optimizer-phase1.md pending true endpoint evidence"。
- 【事实】第二阶段登记摘要："MO-7 and MO-8 software changes on main. MO-9 plan passed; paper forward cycle and two weekly reports not yet evidenced"。
- 【事实】S2 边界：S2 公共结果登记与三进程集成未完成（`d1_scope`），不在本包授权内。
- 【事实】M2/D1 七个真实并跑日未交付（`d1_scope`："No qualifying seven days or production cutover"）。
- 【事实+推断】可复现边界：GitHub 侧对象（第一父链、PR、CI、固定文件）可由接收方独立复算；操作员侧回执（§3 档案、§4/§5 本机回执与命令指纹）不能从本包再生，只能按指纹核对，或按指明命令在公开检出（如 `26dfa2a0…`）上复跑近似范围。包制作辅助程序（冻结登记生成、脱敏器）仅本机保留，其产物以指纹交付；凡库外工具逻辑成为某复核声明唯一依据者，须先明示"仅操作员可复核"，再按已审计划转成可移植、可脱敏运行的独立代码节点并经审推送后，方可作为外部证据（本包未包含此类依赖）。
