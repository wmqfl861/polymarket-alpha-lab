"""Native project-only PostgreSQL lifecycle. No Docker or Supabase services.

Never adopt an external cluster, reset data, force-kill a process, run a model,
fetch software, silently migrate existing databases, or print raw child output.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import socket

from polymarket_alpha_lab.local_postgres_dsn import validate_local_postgres_dsn
from . import sql
from .files import (Layout, ProjectDatabaseError, clean_environment, fail,
                    no_links, private_directory, read_private, write_private)
from .runtime import executable, verify_runtime, native_command as command


def _quote(value: str) -> str:
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


class ProjectPostgres:
    """An explicit project root owns all runtime, configuration and state paths."""
    def __init__(self, root: Path):
        self.layout = Layout(root)

    def __repr__(self):
        return 'ProjectPostgres(project_private=True)'

    def _program(self, name):
        return str(executable(self.layout.runtime, name))

    def _control(self, action, *options, accepted=(0,)):
        return command([self._program('pg_ctl'), action, '-D', str(self.layout.cluster), *options],
                       accepted=accepted, timeout=75, capture_output=False)

    def _disk_identifier(self):
        text = command([self._program('pg_controldata'), str(self.layout.cluster)], timeout=10).stdout
        match = re.search(r'^Database system identifier:\s+(\d+)\s*$', text, re.M)
        if not match:
            fail('project_postgres_cluster_identity_missing')
        return match[1]

    def _state(self, *, ready=True):
        layout = self.layout
        private_directory(layout.private)
        private_directory(layout.home)
        try:
            info = json.loads(read_private(layout.home / 'instance.json'))
            if (set(info) != {'format', 'instance_id', 'root_sha256', 'system_identifier', 'version', 'port'}
                    or info['format'] != 'project-postgres-v1'
                    or not re.fullmatch('[0-9a-f]{32}', info['instance_id'])
                    or info['root_sha256'] != layout.root_hash
                    or not re.fullmatch('[0-9]{1,20}', info['system_identifier'])
                    or not re.fullmatch(r'(16|17|18)\.\d+', info['version'])):
                raise ValueError
            sql.server_configuration(info['port'])
            if ready and read_private(layout.home / 'initialized', limit=64) != info['instance_id']:
                raise ValueError
            if read_private(layout.cluster / 'PG_VERSION', limit=32).strip() != info['version'].split('.')[0]:
                raise ValueError
            if self._disk_identifier() != info['system_identifier']:
                raise ValueError
            self._configuration(info)
            return info
        except (OSError, ValueError, KeyError, TypeError):
            fail('project_postgres_instance_invalid_or_incomplete')

    def _configuration(self, info):
        cluster = self.layout.cluster
        if (read_private(cluster / 'postgresql.conf') != sql.server_configuration(info['port'])
                or read_private(cluster / 'pg_hba.conf') != sql.authentication_configuration()):
            fail('project_postgres_configuration_changed')
        if any(line.strip() and not line.lstrip().startswith('#')
               for line in read_private(cluster / 'postgresql.auto.conf').splitlines()):
            fail('project_postgres_configuration_changed')
        for name in ('owner.pgpass', 'app.pgpass'):
            value = read_private(self.layout.home / name, limit=1024)
            role = sql.ADMIN if name.startswith('owner') else sql.APPLICATION
            if not re.fullmatch(rf'127\.0\.0\.1:{info["port"]}:\*:{role}:[0-9a-f]{{64}}\n', value):
                fail('project_postgres_credentials_invalid')

    def _dsn(self, info, *, owner=False, database=sql.DATABASE):
        if database not in ('postgres', sql.DATABASE):
            fail('project_postgres_database_not_allowed')
        values = {'host': '127.0.0.1', 'port': str(info['port']),
                  'dbname': database, 'user': sql.ADMIN if owner else sql.APPLICATION,
                  'passfile': str(self.layout.home / ('owner.pgpass' if owner else 'app.pgpass')),
                  'connect_timeout': '5', 'sslmode': 'disable', 'gssencmode': 'disable',
                  'application_name': 'polymarket-alpha-lab', 'options': '-c timezone=UTC'}
        dsn = ' '.join(f'{key}={_quote(value)}' for key, value in values.items())
        validate_local_postgres_dsn(dsn, env_var_name='POLYMARKET_ALPHA_LAB_PROJECT_POSTGRES')
        return dsn

    def _psql(self, info, script, *, database=sql.DATABASE, owner=True):
        dsn = self._dsn(info, owner=owner, database=database)
        env = clean_environment()
        # Never inherit psqlrc, default credentials, services or PGOPTIONS.
        return command([self._program('psql'), '-X', '-w', '-qAt', '-v', 'ON_ERROR_STOP=1', '-d', dsn],
            env=env, stdin="SET statement_timeout='30s'; SET lock_timeout='5s';\n" + script,
            timeout=65).stdout.strip()

    def _server_identity(self, info, *, bootstrap=False):
        try:
            value = json.loads(self._psql(info, "SELECT json_build_object("
                "'directory',current_setting('data_directory'),'port',current_setting('port'),"
                "'listen',current_setting('listen_addresses'),'version',current_setting('server_version_num'),"
                "'system',(SELECT system_identifier::text FROM pg_control_system()));", database='postgres'))
            if (Path(value['directory']).resolve() != self.layout.cluster.resolve()
                    or value['port'] != str(info['port']) or value['listen'] != '127.0.0.1'
                    or int(value['version']) // 10000 != int(info['version'].split('.')[0])
                    or value['system'] != info['system_identifier']):
                raise ValueError
            if not bootstrap:
                identity = json.loads(self._psql(info, "SELECT json_build_array(instance_id,root_sha256,"
                    "system_identifier) FROM project_private.instance WHERE singleton IS TRUE;"))
                if identity != [info['instance_id'], info['root_sha256'], info['system_identifier']]:
                    raise ValueError
        except (ValueError, KeyError, TypeError, OSError):
            fail('project_postgres_server_identity_mismatch')

    def _running(self, info):
        result = self._control('status', accepted=(0, 3))
        if result.returncode == 3:
            return False
        # Validate PID metadata before any stop. Never signal a PID from an
        # unrelated directory, even if pg_ctl's status says a process exists.
        lines = read_private(self.layout.cluster / 'postmaster.pid', limit=16384).splitlines()
        if (len(lines) < 4 or not lines[0].isdigit()
                or Path(lines[1]).resolve() != self.layout.cluster.resolve()
                or lines[3] != str(info['port'])):
            fail('project_postgres_pid_identity_mismatch')
        return True

    def _start(self, info):
        if self._running(info):
            self._server_identity(info)
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(('127.0.0.1', info['port']))
            except OSError:
                fail('project_postgres_port_in_use')
        self._control('start', '-w', '-t', '60', '-l', str(self.layout.home / 'server.log'))
        self._server_identity(info)
        return True

    def _stop(self, info, *, bootstrap=False):
        if not self._running(info):
            return
        self._server_identity(info, bootstrap=bootstrap)
        active = self._psql(info, "SELECT count(*) FROM pg_stat_activity WHERE "
            "backend_type='client backend' AND pid<>pg_backend_pid();", database='postgres')
        if active != '0':
            fail('project_postgres_connections_active')
        self._control('stop', '-m', 'smart', '-w', '-t', '60')

    def _pending(self, info):
        catalog = sql.migration_catalog(self.layout)
        raw = self._psql(info, "SELECT coalesce(json_agg(json_build_object('name',name,'sha256',sha256) "
                         "ORDER BY name),'[]'::json) FROM project_private.migrations;")
        try:
            return sql.pending_migrations(catalog, json.loads(raw))
        except (ValueError, TypeError):
            fail('project_postgres_migration_history_conflict')

    def _migrate(self, info):
        pending = self._pending(info)
        for name, digest, body in pending:
            self._psql(info, sql.migration_sql(name, digest, body))
        self._psql(info, sql.GRANTS)
        if self._pending(info):
            fail('project_postgres_migrations_pending')
        return len(pending)

    def initialize(self, *, port: int = 55432) -> dict:
        sql.server_configuration(port)
        layout = self.layout
        with layout.lock():
            runtime = verify_runtime(layout)
            sql.migration_catalog(layout)  # Validate ALL scripts before initdb.
            if layout.home.exists():
                fail('project_postgres_existing_data_not_reinitialized')
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                try:
                    probe.bind(('127.0.0.1', port))
                except OSError:
                    fail('project_postgres_port_in_use')
            private_directory(layout.home, create=True)
            owner, application = secrets.token_hex(32), secrets.token_hex(32)
            password_path = layout.home / 'initial-password'
            write_private(password_path, owner + '\n')
            started = False
            info = None
            try:
                command([self._program('initdb'), '-D', str(layout.cluster), '-U', sql.ADMIN,
                    '--auth-host=scram-sha-256', '--auth-local=reject', '--encoding=UTF8', '--locale=C',
                    '--data-checksums', '--no-clean', '--pwfile=' + str(password_path),
                    '-L', str(layout.runtime / 'share')], timeout=120)
                # These two files were just created by our initdb in a fresh
                # exclusively-owned directory, never by an existing user DB.
                for name, contents in (('postgresql.conf', sql.server_configuration(port)),
                                       ('pg_hba.conf', sql.authentication_configuration())):
                    target = layout.cluster / name
                    no_links(target)
                    target.unlink()
                    write_private(target, contents)
                write_private(layout.home / 'owner.pgpass', f'127.0.0.1:{port}:*:{sql.ADMIN}:{owner}\n')
                write_private(layout.home / 'app.pgpass', f'127.0.0.1:{port}:*:{sql.APPLICATION}:{application}\n')
                info = {'format': 'project-postgres-v1', 'instance_id': secrets.token_hex(16),
                    'root_sha256': layout.root_hash, 'system_identifier': self._disk_identifier(),
                    'version': runtime['version'], 'port': port}
                write_private(layout.home / 'instance.json', json.dumps(info, sort_keys=True))
                started = True  # pg_ctl may time out while startup still completes.
                self._control('start', '-w', '-t', '60', '-l', str(layout.home / 'server.log'))
                self._server_identity(info, bootstrap=True)
                self._psql(info, sql.create_database_sql(application), database='postgres')
                self._psql(info, sql.bootstrap_sql(info['instance_id'], layout.root_hash, info['system_identifier']))
                applied = self._migrate(info)
                self._server_identity(info)
                write_private(layout.home / 'initialized', info['instance_id'])
            finally:
                # Only the initialization password is transient. On any failure
                # preserve the cluster/credentials for diagnosis, never reset it.
                if password_path.exists():
                    password_path.unlink()
                if started:
                    self._stop(info, bootstrap=True)
            return {'status': 'initialized', 'version': runtime['version'], 'migrations_applied': applied}

    def up(self) -> dict:
        with self.layout.lock():
            runtime = verify_runtime(self.layout)
            info = self._state()
            if runtime['version'] != info['version']:
                fail('project_postgres_runtime_upgrade_required')
            started = self._start(info)
            pending = len(self._pending(info))
            return {'status': 'migrations_pending' if pending else 'running',
                    'started_here': started, 'pending_migrations': pending, 'port': info['port']}

    def down(self) -> dict:
        with self.layout.lock():
            runtime = verify_runtime(self.layout)
            info = self._state(ready=False)
            if runtime['version'] != info['version']:
                fail('project_postgres_runtime_upgrade_required')
            self._stop(info, bootstrap=not (self.layout.home / 'initialized').exists())
            return {'status': 'stopped', 'data_preserved': True}

    def status(self) -> dict:
        if not self.layout.home.exists():
            return {'status': 'not_initialized'}
        with self.layout.lock():
            runtime = verify_runtime(self.layout)
            info = self._state()
            if runtime['version'] != info['version']:
                fail('project_postgres_runtime_upgrade_required')
            running = self._running(info)
            if running:
                self._server_identity(info)
            return {'status': 'running' if running else 'stopped', 'port': info['port'],
                    'version': info['version'], 'instance_id': info['instance_id']}

    def migrate(self) -> dict:
        with self.layout.lock():
            runtime = verify_runtime(self.layout)
            info = self._state()
            if runtime['version'] != info['version']:
                fail('project_postgres_runtime_upgrade_required')
            started = self._start(info)
            try:
                return {'status': 'migrated', 'migrations_applied': self._migrate(info)}
            finally:
                if started:
                    self._stop(info)

    def _verify_session_bundles(self):
        """Reuse kit verification at entry, before any private lifecycle access.

        Source and data roots may differ. Check both when either kit marker is
        present; a source checkout with neither marker keeps its existing path.
        This is byte integrity, not authentication or a hostile-Python sandbox.
        """
        from .distribution import ENGINE, MANIFEST, verify_distribution
        source_root = Path(__file__).absolute().parents[3]
        for root in dict.fromkeys((source_root, self.layout.root)):
            seed, manifest = root / ENGINE, root / MANIFEST
            try:
                no_links(seed)
                no_links(manifest)
                if seed.exists() or manifest.exists():
                    verify_distribution(root)
            except OSError:
                fail('project_bundle_invalid_or_changed')

    @contextmanager
    def session(self):
        """Own lifecycle while any number of in-process research tasks execute.

        Other processes cannot stop/migrate the cluster while this lease is
        held. If it was already explicitly up, this session leaves it up.
        """
        self._verify_session_bundles()
        from .research import ProjectResearchSession
        with self.layout.lock():
            runtime = verify_runtime(self.layout)
            info = self._state()
            if runtime['version'] != info['version']:
                fail('project_postgres_runtime_upgrade_required')
            started = self._start(info)
            session = ProjectResearchSession(self, info)
            try:
                if self._pending(info):
                    fail('project_postgres_migrations_pending')
                yield session
            finally:
                session.close()
                if started:
                    self._stop(info)
