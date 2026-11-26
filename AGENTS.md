# Project Instructions

## 模拟盘主线与 V1 备线

模拟盘（papersim）是本项目的主线。V1 的 WP-01 至 WP-06 为备线，状态为“未激活”。备线保持源码冻结，真实模型调用保持关闭。V1 的 Linux 原生数据库测试属于允许的维护范围。备线激活须由 owner 另行明示。（OD-A）

V1 covers BTC/ETH research on prospective terminal Yes/No contracts. WP-01/G1
is delivered. The release tuple is Ubuntu 26.04.1 x86_64 with Python 3.12.14
and a pinned PostgreSQL 18 build. The WP-02 probe runs in a rootless Linux
namespace. The D1 research adapter choices are local Codex, Claude Code,
OpenCode, Grok CLI, and ZCode CLI.
D2 permits the research data needed for the task. D3 sets no first-round
business spending cap. The adapter still requires task-scoped inputs,
credential isolation, finite I/O and stop controls. The positive-micros
ModelCallBudget represents capped policies only. Keep other research teams
outside V1 and retain the registered market and validation limits.

## Scope

Phase 1 covers market research, data engineering, strategy validation,
paper trading and risk analysis. Keep `paper_only=True`, `report_only=True`
and `readonly=True` wherever these flags exist. Do not add live trading,
account authentication, hosted account reads, wallet or private-key handling,
order signing, submission, cancellation, replacement, or exchange mutations.
Do not read, print or move credentials. Only the owner can authorize real funds,
credential use or live orders separately. Statistical strategy validation and
promotion retain their registered thresholds.

Do not perform compliance, legal, geographic-access, or regulatory analysis in
this repository unless the user explicitly reopens that topic.

## Database persistence

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

**Database Persistence Iron Rule.** All database-related implementation uses
project-private native PostgreSQL as the only approved database persistence
target. The older term “local Supabase/Postgres” refers to this local storage
boundary. Alternate database backends, hosted remote database connections,
SQLite, Redis, Mongo, SQLAlchemy, file-backed database substitutes and generic
persistence abstractions are outside scope. Legacy file-backed journals and input loaders are frozen
compatibility surfaces. Validate every raw DSN with `validate_local_postgres_dsn`
before opening a connection or constructing a persistence adapter.

## Data Source Priority

Use official Polymarket sources first:

1. Gamma API for market, event, tag, search, and metadata discovery.
2. CLOB API for order books, prices, spreads, midpoints, price history, and public market data.
3. Data API for public trades, positions, activity, holders, open interest, and leaderboards.
4. WebSocket market channel for real-time watchlist updates.
5. Officially documented third-party chain data only for historical backfill or verification.

Avoid using website scraping as a primary data path unless a needed field is unavailable through official APIs.

## Research data

- Preserve raw API payloads before normalization.
- Distinguish `null`, `0`, and `unknown` in data models.
- Use executable bid/ask and order book depth for research calculations.
- Keep research outputs reproducible and timestamped.
- 输入快照的原始字节登记到受管 PostgreSQL 的快照表。清单和引用保存对应的校验值。
- 主机地址、端口、机器标识和微信收件人标识仅留在本机配置，文件、日志和提交说明须脱敏。

## 云端审计

云端审计方只读，不改项目代码。每轮审核的文档发布在 `handoff/` 开头的保留分支上，这些分支不删除、不强推。
审核包里提交给 owner 的决定项默认按审计方的推荐执行；owner 另有答复时以 owner 的答复为准。
