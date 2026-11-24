# Linux Local Development Setup

Use this guide to install the checked-in project and verify it on Linux (Bash).
Linux is the sole V1 development and delivery platform per DELIVERY_PLAN.md
section 61 (owner instruction, 2026-09-27); the pinned release tuple is
**Ubuntu 26.04.1 x86_64 / Python 3.12.14 / an exactly pinned PostgreSQL 18
build**. The historical
[Windows local setup guide](windows-local-setup.md) is retained unchanged as
history; Windows setup, verification and acceptance are no longer active V1
obligations.

Verification is offline and keeps Phase 1 paper-only, report-only, and
readonly. It does not fetch market data or require a database.

## Prerequisites

- Git, uv, and network access for the pinned interpreter/packages when they
  are not cached. The project supports Python 3.12 or newer; **Python 3.12.14
  is the pinned release interpreter** for this setup.
- A checkout of this repository. The examples use `~/polymarket-alpha-lab`
  (the recorded development host deployment path); adjust the path for your
  checkout.
- The checked-in `pyproject.toml` and `uv.lock`. Dependency installation may
  need network access for packages or the interpreter when they are not
  cached.

The recorded Linux development environment (DELIVERY_PLAN.md sections 49-50)
used uv-managed Python 3.12.14 with the locked dependencies on Ubuntu 26.04.1
and passed the full offline suite plus the native PostgreSQL matrix against a
portable PostgreSQL 18 prefix. Those are historical engineering evidence for
feasibility, not a fresh verification of your checkout.

Use `--locked` below so setup also checks that the lock matches project
metadata. Do not regenerate `uv.lock` or copy a virtual environment from
another machine or platform to resolve an installation failure; recreate the
environment from the lock instead.

## Install and Verify

Run each command in order and stop on failure; check `$?` after each command
as shown:

```bash
cd ~/polymarket-alpha-lab
uv sync --locked --extra dev --extra postgres --python 3.12.14
if [ $? -ne 0 ]; then echo 'Locked installation failed' >&2; exit 1; fi
uv pip check --python .venv/bin/python
if [ $? -ne 0 ]; then echo 'Dependency check failed' >&2; exit 1; fi
.venv/bin/python scripts/verify_local.py --quick
if [ $? -ne 0 ]; then echo 'Quick verification failed' >&2; exit 1; fi
```

For a complete offline baseline:

```bash
.venv/bin/python scripts/verify_local.py --full
if [ $? -ne 0 ]; then echo 'Full verification failed' >&2; exit 1; fi
```

The `dev` extra supplies test dependencies. The `postgres` extra supplies the
existing psycopg driver and is optional for offline setup: omit
`--extra postgres` if the driver is not needed. The reference environment
includes it. Installing the driver does not start or connect to Postgres.

## What Verification Covers

[`scripts/verify_local.py`](../../scripts/verify_local.py) accepts `--quick`
(the default when no mode is supplied) or `--full`. These are mutually
exclusive; there are no other task selectors.

Both modes require a virtual environment and its Python executable, verify
installed distribution metadata, and check that the editable install points
to this checkout. They exercise both the module entrypoint with Python `-I`
and the virtual environment's console executable from a temporary directory
outside the repository. This checks installation independently of pytest's
configured `src` import path. If `TMPDIR` or `TEMP` points inside the
checkout, the verifier uses a temporary sibling directory instead and removes
it after the checks. It reports an error if no writable location outside the
repository is available. CLI smoke commands are limited to `--help` and
[`report-discovery`](../cli/phase1-report-discovery.md), which lists registered
report entrypoints without executing them.

- `--quick` runs focused tests and compiles `src`, `tests`, and `scripts`.
- `--full` runs the complete pytest suite instead of the focused selection,
  along with the same installation, CLI, and compilation checks. It does not
  separately rerun the focused tests.

The verifier derives the repository root from its own file, uses the invoking
Python interpreter for subprocesses, and reports failing steps with a nonzero
exit code. It does not install dependencies, change the lock, or start
services. Run full verification once at a time; record the revision, command,
exit code, actual pytest counts, warnings, and elapsed time when reporting a
baseline.

The verifier removes inherited `POLYMARKET_ALPHA_LAB_*`, `PYTEST_ADDOPTS`,
`PYTEST_PLUGINS`, `PYTHONPATH`, and `PYTHONHOME` overrides from its subprocess
environments, then sets `POLYMARKET_ALPHA_LAB_RUN_SUPABASE_SMOKE=0` and
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` there. It leaves the calling shell
environment unchanged, does not import `.env`, and requires no DSN. Keep the
real database smoke disabled for offline verification.

You can invoke the script from any working directory by using absolute paths
to both the repository's virtual environment Python and the script:

```bash
/home/ubuntu/polymarket-alpha-lab/.venv/bin/python /home/ubuntu/polymarket-alpha-lab/scripts/verify_local.py --quick
```

Use `--full` in that command for a full run. The legacy `scan` examples in the
README fetch public market data and use legacy file outputs; they are outside
this onboarding flow.

## Troubleshooting

- **uv or Python is unavailable:** make the prerequisite tools available
  through your normal development toolchain (for example, the uv installer or
  your distribution's package manager), then rerun the locked installation
  from the repository root. Use the pinned Python 3.12.14 and a uv version
  compatible with the checked-in lock.
- **Locked installation fails:** inspect the reported interpreter, download,
  or metadata error. Keep `--locked`; do not regenerate `uv.lock`, upgrade all
  dependencies, or copy a virtual environment from another checkout or
  platform to hide the failure. Uncached installation needs package access,
  whereas verification itself is offline.
- **Wrong interpreter or editable source:** use this checkout's
  `.venv/bin/python`, not a system Python, a shell alias, or a virtual
  environment copied from another checkout or from Windows. Rerun the locked
  sync from this repository. A pytest pass alone does not prove the editable
  installation.
- **Verification fails:** inspect the first failing step and its output. Keep
  the failing command and result for diagnosis; do not suppress assertions or
  enable database smoke to make an offline run pass.

The CodeGraph CLI was unavailable on the reference workstation. If it is
installed on your Linux host, use `codegraph status .` and `codegraph explore`
from the repository root; otherwise record its unavailability honestly rather
than claiming a synchronization that did not happen.

## Database Work Is Separate

Offline tests, fake database adapters, and an installed psycopg driver do not
prove real native PostgreSQL integration. The project-private native
PostgreSQL path is described in [`database/README.md`](../../database/README.md)
and [`database/quickstart.md`](../../database/quickstart.md): a trusted
portable PostgreSQL 18 prefix (bin/, lib/, share/) is imported with
`scripts/project_database.py install-runtime`; nothing is downloaded or
executed implicitly, and no system PostgreSQL service, Docker, or shared
instance is used. The recorded development prefix was assembled from real
package files into a symlink-free directory (DELIVERY_PLAN.md section 50);
release construction must pin the exact PG18 build, provenance and inventory.

[`.env.example`](../../.env.example) is a variable reference, not a
configuration file to import automatically. Do not copy or import it wholesale
for onboarding. Only configure the variables required for a specific later
database task.

Local Supabase/Postgres remains the only durable project data store. Every
raw DSN must pass `validate_local_postgres_dsn` before a connection or
persistence adapter is constructed.

The real Supabase smoke test inserts, reads back, and deletes test rows. It
remains disabled by default. A later integration check needs a confirmed
existing local instance, the required schema, and an explicitly agreed
test-write and cleanup scope. Onboarding does not run that integration check.
Any later database evidence remains local paper evidence, with
`paper_only=True`, `report_only=True`, and `readonly=True` wherever those
flags exist.

## Platform boundary notes

- The recorded development host (project SSH alias `pal-dev`; the actual
  HostName/User are kept in each operator's own SSH configuration, never in
  this repository) is a shared, development-only machine. It holds no
  provider credentials; real research activation requires a separately
  approved Linux operating installation and concrete execution
  authorization (DELIVERY_PLAN.md section 61).
- Do not copy `.local`, clusters, backups or credentials between platforms or
  machines. Moving an existing Windows cluster is outside the Linux pivot; a
  later business-history transfer would be a separately reviewed migration
  task.
- The historical Windows setup guide, Windows kits and PowerShell-first-run
  evidence are frozen; do not extend or re-verify them for V1, and do not
  treat Linux results as Windows acceptance or the reverse.
