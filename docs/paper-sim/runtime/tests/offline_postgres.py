"""Start and shut down a PostgreSQL cluster dedicated to the offline tests."""

from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
import time

import psycopg
from psycopg.rows import dict_row


ROLE = "pal_runtime_test"
DATABASE = "pal_runtime_test"
PORT = 55432
_connect = psycopg.connect
_database = None


class ScratchDatabase:
    def __init__(self, socket_dir):
        self.socket_dir = socket_dir

    def connect(self, *, database=DATABASE):
        return _connect(
            host=str(self.socket_dir), hostaddr="", port=PORT, dbname=database,
            user=ROLE, row_factory=dict_row, autocommit=True,
            options="-c search_path=football_notify,pg_catalog -c timezone=UTC",
            connect_timeout=2, sslmode="disable", gssencmode="disable",
        )


def scratch_database(case):
    if _database is None:
        case.skipTest("run run_offline.py to include the temporary PostgreSQL cases")
    return _database


@contextmanager
def temporary_postgres(pg_bin):
    global _database
    with tempfile.TemporaryDirectory(prefix="pal-run-") as work:
        scratch = Path(work).resolve()
        data, socket = scratch / "pgdata", scratch / "sock"
        socket.mkdir(mode=0o700)
        args = [str(pg_bin / "initdb"), "-D", str(data), "-A", "trust", "-U", ROLE,
                "--no-locale", "-E", "UTF8", "-N"]
        subprocess.run(args, check=True, stdout=subprocess.DEVNULL, timeout=45)
        with (scratch / "postgres.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen(
                [str(pg_bin / "postgres"), "-D", str(data), "-k", str(socket), "-p", str(PORT),
                 "-c", "listen_addresses=", "-c", "unix_socket_permissions=0700"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                instance = ScratchDatabase(socket)
                expires = time.monotonic() + 20
                while server.poll() is None and time.monotonic() < expires:
                    try:
                        with instance.connect(database="postgres") as connection:
                            connection.execute("CREATE DATABASE pal_runtime_test")
                    except psycopg.OperationalError:
                        time.sleep(0.1)
                    else:
                        break
                else:
                    details = (scratch / "postgres.log").read_text()
                    raise RuntimeError(f"temporary PostgreSQL did not start\n{details}")
                _database = instance
                yield
            finally:
                _database = None
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()
