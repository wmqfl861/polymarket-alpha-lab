"""Run the published football and basketball regressions offline."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch


HERE = Path(__file__).resolve().parent


def reject_external_io(event, _args):
    if event.startswith("socket.") or event in {"os.system", "os.posix_spawn", "subprocess.Popen"}:
        raise AssertionError(f"unexpected external I/O in the regression suite ({event})")


def reject_external_database(*_args, **_kwargs):
    raise AssertionError("the regression suite uses only its temporary PostgreSQL")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-bin", type=Path, help="directory containing initdb and postgres")
    args = parser.parse_args()
    if args.postgres_bin is None:
        try:
            bindir = subprocess.check_output(["pg_config", "--bindir"], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            parser.error("select a local PostgreSQL installation via --postgres-bin")
        pg_bin = Path(bindir)
    else:
        pg_bin = args.postgres_bin
    pg_bin = pg_bin.resolve()
    if not all(os.access(pg_bin / name, os.X_OK) for name in ("initdb", "postgres")):
        parser.error("PostgreSQL bin directory must contain initdb and postgres")
    for variable in tuple(os.environ):
        if variable.startswith(("PG", "PAL_", "PYTEST_")):
            del os.environ[variable]
    os.environ["PAL_PAPER_ACCOUNT_KEY"] = "synthetic-runtime-test"
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    sys.dont_write_bytecode = True

    import psycopg
    import pytest
    from offline_postgres import temporary_postgres

    with temporary_postgres(pg_bin), patch.object(psycopg, "connect", reject_external_database):
        sys.addaudithook(reject_external_io)
        return pytest.main(["-q", "-p", "no:cacheprovider", str(HERE)])


if __name__ == "__main__":
    raise SystemExit(main())
