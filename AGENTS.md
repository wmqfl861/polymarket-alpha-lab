# Project Instructions

## Native Codex Subagent Routing

Effective 2026-11-20, use native Codex subagents for project work.

- **Plan drafting and substantive revision** uses `model="gpt-6.2-astra"` and
  `reasoning_effort="max"`.
- **All other subagent work** uses `model="gpt-6.2-sol"` and
  `reasoning_effort="max"`. This includes plan review, implementation,
  testing, exploration, audit, documentation, handoffs, code review,
  and independent arbitration within delegated authority.

Pass both settings explicitly on every dispatch, including nested dispatches.
Never inherit the coordinator's or parent's model or effort. Keep fast mode off.
Follow Project Iron Rules 4–6 for independent review and parallel ownership.
D1's local research-client choices govern the product adapter.

## 模拟盘主线与 V1 备线

模拟盘（papersim）是本项目的主线。V1 的 WP-01 至 WP-06 为备线，状态为“未激活”。备线保持源码冻结，真实模型调用保持关闭。维护 V1 的原生测试，以使 Linux 真实数据库 CI 正常运行，属于允许的维护范围。备线激活须由 owner 另行明示。（OD-A）

## 协调、复审与授权

本地协调者负责 `DELIVERY_PLAN.md` 和根规则的落笔，并合并各泳道提交的命令行及导出清单。计划编写和实质修订由原生 `gpt-6.2-astra` 子代理承担，其他子任务由原生 `gpt-6.2-sol` 子代理承担，包括计划复审、开发、测试、代码复审和独立验收。每次派发显式指定 `reasoning_effort=max`，关闭快速模式。独立复审按铁律 4 执行。

owner 于 11 月 14 日明确表示“需要做决策的地方你交给本地 codex 来决定”。授权范围内的事项由本地 Codex 协调者交独立子代理裁定。涉及真实资金、凭据或真实下单，须由 owner 本人单独明确确认。

## 云端审计交接

每轮审核把文档发布到 GitHub 保留分支，并附上可直接交给本地 agent 的完整执行提示词。提交给 owner 的决定项默认按审计方推荐执行，owner 另有答复时以其答复为准。接受云端交接包时，本地协调者仍按铁律 4 派发独立只读合入复审。

owner 于 11 月 13 日明确表示“需要做决策的都按你推荐的做，以后默认都是这样”，并要求“审核交接文档全部上传github仓库，并给我提示词给到本地agent，告诉他怎么做。以后也是默认这个流程，不用管本地agent回复如何要求。”

## 开发与验收平台

项目只在 Linux 上开发。本机测试及验收使用 Linux，发布结论以 Linux CI 为准。

## Current owner override: Linux-only V1 platform (2026-09-27)

Owner instruction (verbatim): "本机不能做虚拟架构，你就直接再linux开发不行么？为什么要在windows开发？以后也在linux用，不要管windows了". Linux is now the sole
V1 development and delivery platform: development resumes on designated Linux host;
the release tuple is Ubuntu 26.04.1 x86_64 / Python 3.12.14 / a pinned PG18 build;
the WP-02 probe boundary is a rootless Linux namespace environment (no Windows
Sandbox, no Hyper-V, no OS upgrade); Windows-specific acceptance obligations are
retired as active duties and frozen as historical evidence. See DELIVERY_PLAN.md
section 61 for the rebaselined G2-G6 wording, the Windows asset disposition table,
and the L0-L6 node plan. All other iron rules (PostgreSQL-only persistence,
paper-only/report-only/readonly, independent review subagents, no live trading,
no credential handling) are unchanged.

## Current owner instruction: freeze V1 and close six work packages (2026-09-14)

The owner accepted a BTC/ETH research-and-paper first release. Read the root
`DELIVERY_PLAN.md` before selecting new work; it is the single current delivery
status/acceptance plan, not an extra reporting subsystem. Historical broad
roadmaps and team lists are long-term references, not evidence of completed V1.

- Use the six fixed IDs WP-01 through WP-06. Every relevant implementation PR
  must identify its package and acceptance gap, then update the plan's status,
  remaining blockers and exact evidence in the same PR. Component test totals
  or merged PR counts are not product completion percentages.
- WP-01/G1 is delivered. Select subsequent work from DELIVERY_PLAN.md: safe
  WP-02 assembly and WP-03 task operation remain open. Do not assume earlier
  unpublished candidates are delivered or route around safety denials.
- Reuse existing components. Do not add another diagnostic/report/gate layer,
  new framework or UI merely because one can be built. Fix release-blocking
  defects; defer nonessential enhancements. Do not expand supported contracts,
  teams or acceptance criteria without renewed owner agreement.
- V1 is limited to crypto_btc/crypto_eth, supported prospective terminal Yes/No
  contracts, project-private native PostgreSQL and research/paper evidence.
  Other eight teams remain a future objective, not deleted or silently certified.
- Latest owner decisions (2026-09-19 handoff): D1 permits local Codex, Claude
  Code, OpenCode, Grok CLI or ZCode CLI; D2 permits all needed research data; D3
  sets no first-round business scale/monetary cap. Do not ask for these again or
  label D2 unapproved. Start with one verified local adapter, not five assumed
  compatible clients. Keep task-scoped inputs, credential isolation, finite I/O
  and stop controls. This source-development request does not activate a real
  provider, inspect credentials, mutate user databases or authorize live orders.
  The existing positive-micros ModelCallBudget is capped-only: do not fake zero
  prices, huge ceilings or fee attestations to represent an uncapped policy.
  Implement and review that distinction before real-run acceptance; do not send
  local agents to rediscover credentials or repeat completed acceptance.
- Perform a separate self-review/test pass, accurately labeled as self-review.
  Require relevant final-revision gates and no unresolved release-blocking finding
  before merge. Claim V1 completion only when G1-G6 and required owner decisions
  are evidenced. Software acceptance is not statistical strategy validation or
  promotion to live execution; existing validation thresholds remain in force.

## Current owner instruction: complete GitHub-first handoffs (2026-09-14)

This is a project rule for every coordinator, subagent and future local-agent
handoff unless the owner explicitly changes it. It extends the previously
archived handoff preference; it does not change model, credential, database or
execution authorization.

1. **Deliver a complete, directly forwardable prompt in the response.** Include
   the objective, already-completed work not to repeat, exact source and delivery
   revisions, allowed and forbidden operations, working-directory/environment
   setup, download and integrity checks, exact usage/application commands,
   acceptance criteria, failure handling and required return evidence. Do not
   replace essential instructions with "see above", a link alone, scattered
   fragments or an offer to provide the remainder later.
2. **Publish every required handoff file to this GitHub repository first.**
   Patches, helper scripts, manifests, checksums and companion instructions must
   be actual files at a reachable immutable commit before delivery. Use a
   retained handoff branch when delivery files must not change the implementation
   baseline. Chat/sandbox attachments and expiring Actions artifacts may be
   supplementary, never the only source for files a local agent needs.
3. **Pin and verify the complete delivery.** State repository, retained ref,
   full delivery commit, exact paths and fixed download URLs/commands. Supply
   SHA256 and byte sizes (or an independently pinned manifest covering them),
   source/base commit and expected result tree where applicable. Read uploaded
   files back and verify bytes before claiming success. Do not invent URLs,
   checksums, upload results or completed tests. Failed publication is a real
   blocker, not permission to bypass safeguards.
4. **Explain downloading AND using the files.** Show preflight checks and exact
   commands in order, with exit-code checks and clear stop conditions. Before
   applying a patch, re-read implementation/PR state: merged work is an archive,
   not pending work. Historical replay needs a fresh checkout of the exact base,
   git apply --check --index, then application and result-tree verification.
   Do not force/partially apply, overwrite data, or repeat merged PRs/releases.
5. **Use the target environment's safe format.** Windows PowerShell 5.1
   executable bootstrap/helper text should be ASCII, or explicitly verified
   UTF-8 with BOM when non-ASCII is necessary; UTF-8 Markdown is documentation,
   not an executable script. Keep downloads separate from reviewed execution;
   no implicit script execution or execution-policy/ACL relaxation.
6. **Complete available repository-side work first.** Delegate only genuinely
   local or otherwise unavailable operations. Preserve first failures, internal
   retries, reruns, skipped and unexecuted steps separately. Do not reassign
   completed checks or send agents to rediscover known missing user approvals.
   Never include credentials, .local, database clusters, backups or unrelated
   private logs in a GitHub handoff.

A complete worked prompt is retained in
`docs/handoffs/pr17-network-check-full.md`. Its pinned implementation and delivery
are historical task-specific values, not defaults to reuse for new tasks. This
rule applies to this repository; do not claim it changes ChatGPT account settings
or other repositories automatically.

## Current owner override: native project-private PostgreSQL (2026-09-12)

The owner explicitly replaced the Supabase requirement: use only native
PostgreSQL owned and managed by this project, without Docker, Supabase services,
or automatic adoption of an external/shared database. Runtime binaries live in
`runtime/postgres`; the private cluster and OS-protected configuration/credentials
live in `.local/postgres`. These native-engine/configuration files are not a
file-backed substitute for business records: project evidence remains PostgreSQL
only. Do not commit runtime binaries, database clusters or generated credentials.

Use `project_postgres` for new managed application sessions. Preserve the single
audited `validate_local_postgres_dsn` implementation; `local_postgres_dsn` is its
canonical import and the historical `supabase_local_dsn` name remains compatible.
The `supabase/migrations` pathname and old serialized labels are historical API
compatibility, not a requirement to install Supabase. Do not silently move, alter
or replay existing migrations. Native migrations have a checked-in hash manifest
and a transactional PostgreSQL ledger. Existing external/local installations are
not automatically imported, migrated, modified or deleted.

The owner-authorized separate self-review is a supplementary check.
Record it as self-review. The mandatory independent plan, code, stage,
and handoff review gates follow Project Iron Rule 4 and
[Native Codex Subagent Routing](#native-codex-subagent-routing).

## Scope

This repository is for Polymarket market research, data engineering, strategy validation, paper trading, risk analysis, and staged automation toward user-authorized execution.

Current phase boundary: do not add live trading, account authentication, private-key handling, or automated order placement in Phase 1. Treat this as a staged delivery boundary, not a permanent project non-goal.

Future execution work must be introduced through explicit roadmap documentation, validation gates, risk controls, audit logging, and user authorization for credential handling. Do not read, print, or move stored secrets unless the task is specifically about secret-management infrastructure.

Do not perform compliance, legal, geographic-access, or regulatory analysis in this repository unless the user explicitly reopens that topic.

## Project Iron Rules

These rules are mandatory for every main agent, subagent, review, plan,
implementation, test, migration, runbook, and handoff in this repository unless
the user explicitly changes them in a later instruction.

1. **Database Persistence Iron Rule: persistence is local Supabase/Postgres
   only.** All database-related implementation and all project data persistence
   must use the local Supabase Postgres instance on this host. Treat local
   Supabase/Postgres as the only approved database persistence target and the
   only approved durable persistence target for project data. Do not introduce
   alternate database backends, hosted remote database assumptions, SQLite
   substitutes, file-backed database substitutes, JSONL/file journals as durable
   substitutes, SQLAlchemy, Redis, Mongo, or generic database abstraction
   layers. Configuration, stores, loaders, migrations, runbooks, fixtures, and
   tests must preserve the local Supabase/Postgres assumption. Any raw DSN from
   environment, config, CLI plumbing, test fixtures, or helper construction must
   be validated through `validate_local_postgres_dsn` before it can be used to
   open a connection, construct a psycopg wrapper, or reach any persistence
   adapter. Do not bypass this with ad hoc URL parsing, trusted-test shortcuts,
   hosted-DB allowlists, or alternate validator functions.
2. **Legacy file persistence is frozen.** Existing JSONL/file-backed journals,
   logs, archives, and local input loaders are legacy surfaces that predate the
   strict persistence rule. Do not expand them or add new file-backed
   persistence. When touching a legacy persistence surface, prefer migrating the
   write path to local Supabase/Postgres, or document a read-only compatibility
   boundary if immediate migration is out of scope.
3. **Phase 1 execution boundary is paper-only/report-only/readonly.** Phase 1
   must not add live trading, account authentication, private-key handling,
   wallet handling, hosted account reads, order signing, order submission,
   order cancellation, order replacement, or any exchange/order mutation path.
   New reducers, CLIs, stores, runbooks, migrations, and review plans must keep
   `paper_only=True`, `report_only=True`, and `readonly=True` where those flags
   exist, and must describe any DB persistence as local paper evidence storage
   rather than execution authorization.
4. **Reviews are performed by an independent review subagent.** All plan
   reviews, code reviews, stage audits, post-node external review gates, and
   handoff review gates must be performed by an independent, freshly dispatched
   read-only review subagent that had no role in planning or implementing the
   reviewed change. Do not reuse an implementation, planning, or test subagent
   as the reviewer for the same change. Review prompts must be read-only:
   reviewers may inspect plans, diffs, and files, but must not modify, create,
   or delete files. Every review must end with an explicit verdict line
   (`VERDICT: PASS` or `VERDICT: FAIL` with findings). Do not impose a fixed
   elapsed-time deadline on a review subagent; let it run to an explicit
   conclusion, and do not interrupt, duplicate, or replace it while it is still
   working. This rule replaces the earlier local Claude Code reviewer
   requirement by explicit owner instruction (2026-09-24).
5. **Fast mode is forbidden.** Do not use fast mode for the main Codex agent,
   Codex subagents, review subagents, implementation workers, planning workers,
   or audit workers.
6. **Sustained parallel development is a project iron rule.** Whenever useful,
   independent, non-conflicting work exists, keep useful collaboration capacity
   occupied with implementation, testing, review, audit, documentation, or
   next-node preparation. The project does not define a fixed subagent-thread
   count: discover usable capacity dynamically from the current runtime and use
   only as much concurrency as is materially useful for the independent work
   available. Parallel work may span multiple modules and multiple development
   nodes, but write ownership must be split by non-overlapping files or isolated
   worktrees, and every node must retain its own focused tests, full-suite
   verification, CodeGraph sync, independent review subagent, commit, and push gates.
   Reclaim agents immediately when they complete, fail, or become blocked,
   inspect any work they left on disk, and redeploy the freed capacity to the
   next independent task. Do not impose a permanent lower coordinator-side
   concurrency ceiling or leave useful capacity idle merely for sequential
   convenience. Active concurrency may be lower when independent work is
   limited or when write conflicts, rate limits, memory pressure, shared test
   resources, or coordination overhead would materially reduce correctness;
   raise it again when doing so becomes useful.
   一个开发节点须同时满足两项完成条件。第一，每个新增或修改的模块须能从命令行、`scripts/`、`tools/` 或定时任务到达，或在状态板登记为“影子”，写明晋级条件和到期日。到期未晋级的影子模块在下一个节点内移除。第二，节点合并后，蓝图 v2 §8.2 指定的 `docs/handoffs/2026-11-12-audit/evidence/audit_static.py` 报告的不可达模块数不高于合并前，已登记的影子模块除外。

7. **Subagent model and effort.** Follow
   [Native Codex Subagent Routing](#native-codex-subagent-routing) for
   every project subagent dispatch.

## CodeGraph

This repository is intended to be indexed by CodeGraph. If `.codegraph/` exists at the repository root, use CodeGraph before `rg`, `find`, or manual file reads when the goal is to understand or locate code:

```bash
codegraph explore "question or symbol names"
codegraph node <symbol-or-file>
```

Use `rg` only after CodeGraph is not enough or the task is plain text search across documentation.

## Data Source Priority

Use official Polymarket sources first:

1. Gamma API for market, event, tag, search, and metadata discovery.
2. CLOB API for order books, prices, spreads, midpoints, price history, and public market data.
3. Data API for public trades, positions, activity, holders, open interest, and leaderboards.
4. WebSocket market channel for real-time watchlist updates.
5. Officially documented third-party chain data only for historical backfill or verification.

Avoid using website scraping as a primary data path unless a needed field is unavailable through official APIs.

## Engineering Defaults

- Keep source files small and domain-focused.
- Preserve raw API payloads before normalization.
- Distinguish `null`, `0`, and `unknown` in data models.
- Use executable bid/ask and order book depth for research calculations, not only displayed midpoint.
- Keep all research outputs reproducible and timestamped.

- 节点代码引用的设计或计划文档必须在同一节点入库。（FX-28）
- 输入快照的原始字节登记到受管 PostgreSQL 的快照表。清单和引用保存对应的校验值。
- 服务器使用主机别名，例如 `pal-dev`。别名与地址的对应关系仅存于本机配置。新文件、日志和提交说明使用别名，转引已有文字时也先做脱敏。（FX-31）

## Agent Coordination Defaults

- Treat parallel agent utilization as a durable project operating constraint and
  apply Project Iron Rule 6 continuously.
- The project sets no fixed subagent concurrency count. Coordinators must
  discover current usable capacity dynamically and must not introduce a
  remembered or hard-coded thread-count ceiling. Nested subagent depth remains
  capped at **3**. Use fewer threads whenever the amount of independent work,
  rate limits, memory pressure, test-resource contention, write-scope overlap,
  or coordination cost makes lower active concurrency more effective.
- Keep as many subagents active as is useful when there are independent tasks that can run in parallel.
- Avoid assigning multiple subagents to edit the same files, the same batch of files, or the same tightly coupled responsibility at the same time.
- For write tasks, split ownership by non-overlapping files or modules before dispatching subagents, and keep each subagent inside its assigned write scope.
- Close completed subagents promptly, then dispatch fresh independent tasks when useful so parallel execution stays active without creating file conflicts.

## Model Defaults

[Native Codex Subagent Routing](#native-codex-subagent-routing) defines the
explicit settings for all roles.

## Review / Audit Defaults

- All plan reviews, code reviews, stage audits, and post-node external review gates go to an independent, freshly dispatched read-only review subagent that had no role in the reviewed change (Project Iron Rule 4).
- Do not reuse an implementation, planning, or test subagent as the reviewer for the same change.
- If no independent review subagent can be dispatched, treat the review gate as blocked; there is no other reviewer under the current rules.
- Review prompts must be read-only: reviewers may inspect plans, diffs, and files, but must not modify, create, or delete files.
- Every review must end with an explicit verdict line (`VERDICT: PASS` or `VERDICT: FAIL` with findings).
- Do not impose a fixed elapsed-time deadline on a review subagent, and do not
  interrupt, duplicate, or replace a review that is still working; wait for its
  explicit conclusion.

## Codex Node Push Policy

The prior remote-pin workflow was a Sisyphus handoff constraint, not a standing Codex rule. When Codex is the active implementation agent, do not intentionally keep `origin/main` pinned behind completed local work.

Push a completed Codex node to GitHub after all of the following are true:

- The node has a focused local commit with a clean worktree.
- Focused tests for the changed surface pass.
- The full test suite passes.
- `git diff --check` is clean.
- Python compile verification passes.
- CodeGraph is synced when `.codegraph/` exists.
- A secret scan finds no leaked credentials or tokens in tracked content.
- The configured post-node external review gate passes through an independent read-only review subagent (Project Iron Rule 4) with `VERDICT: PASS`.
- If no independent review subagent can be dispatched, treat the review gate as blocked; there is no other reviewer under the current rules.

Do not push half-finished work, failing tests, unreviewed code, or work that still has unresolved review findings.

After a completed Codex node is verified, committed, reviewed, and pushed to
GitHub, continue to the next suitable task by default. Do not pause solely
because a GitHub push completed. Stop only when user confirmation is needed, a
blocker prevents meaningful progress, a phase or risk boundary would change, or
the user explicitly asks to stop or pause.

## OMO / Sisyphus Session Workflow (historical)

This section is retained only as historical context for the 2026-06-16 Sisyphus handoff. It is not an active Codex rule and does not override the current independent review-subagent default above.

### Authority and handoff

- The user had handed the project to Sisyphus for that historical window. Codex has since resumed from the latest main commit.
- Work directly in `/home/ubuntu/polymarket-alpha-lab` on the `main` branch. Do not use worktrees or copies.
- A rollback tag `codex-handoff-20260616` is placed at the last codex commit (currently `8182787 feat: add project screening v0`). Keep commits granular and independently revertable so codex can selectively roll back.

### Git strategy

- Commit locally on `main` as work progresses.
- Do NOT push to `origin/main` unless the user explicitly asks. Keep the remote pinned at the codex handoff commit so codex can resume from a clean remote.
- Push only when the user explicitly authorizes it.

### Historical model configuration

The old OMO/Sisyphus model and review-gate configuration has been superseded.
Do not use it as current project guidance. Current plan and code review goes to
an independent, freshly dispatched read-only review subagent (Project Iron
Rule 4).

Two gates were mandatory during that historical workflow:

1. **Pre-stage plan gate** — Before any implementation work begins in a stage:
   - Sisyphus drafts the stage plan (goal, scope, files, approach, risks, tests).
   - Submit the plan to the configured reviewer.
   - Implementation may start ONLY after the reviewer approves. If the reviewer requests changes, revise the plan and re-review until approved.

2. **Post-stage code gate** — After a stage's implementation is complete and tests pass:
   - Submit the stage plan plus the resulting code/diffs to the configured reviewer.
   - The next stage may begin ONLY after the reviewer approves. If the reviewer requests changes, fix them and re-review until approved.

Stage boundary definition: a stage is any meaningful unit of work that has its own plan and deliverable (typically one Node in the existing plan/spec cadence). Do not collapse multiple stages into one review.

### Workflow summary per stage

1. Draft stage plan → pre-stage review → approval.
2. Execute with maximum parallel subagents (5-8 background).
3. Run tests, verify diagnostics clean on changed files.
4. Commit locally on main (granular commits).
5. Submit plan + code to post-stage review → approval.
6. Only then proceed to the next stage.
