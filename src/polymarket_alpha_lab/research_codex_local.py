"""Explicit pinned local Codex execution; no credential discovery or fallback.

The CLI, not this module, uses the operator-selected auth directory. Each exec
uses a fresh private workspace/state directory and an allowlisted environment.
Native tools are absent from the selected local model catalog. This is not a
sandbox against a malicious executable, administrator or same-user file race.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field, replace
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import tempfile

from polymarket_alpha_lab.project_postgres.files import no_links, private_directory, write_private
from polymarket_alpha_lab.research_codex_protocol import (
    CLI_VERSION, INSTRUCTIONS, MAX_STDERR_BYTES, MAX_STDOUT_BYTES, VERSION,
    build_prompt, output_schema, parse_output,
)
from polymarket_alpha_lab.research_model_budget import digest
from polymarket_alpha_lab.team_research_agent_types import integer, strict_json


@dataclass(frozen=True, slots=True)
class CodexLocalProfile:
    executable: Path = field(repr=False)
    executable_sha256: str
    codex_home: Path = field(repr=False)
    workspace_parent: Path = field(repr=False)
    model_id: str
    timeout_seconds: int = 180

    def __post_init__(self):
        for name in ('executable', 'codex_home', 'workspace_parent'):
            value = getattr(self, name)
            if not isinstance(value, Path) or not value.is_absolute() or '..' in value.parts:
                raise ValueError('research_codex_profile_path_invalid')
        digest(self.executable_sha256)
        if type(self.model_id) is not str or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', self.model_id) is None:
            raise ValueError('research_codex_model_invalid')
        integer('timeout_seconds', self.timeout_seconds, 1, 600)
        if (self.codex_home == self.workspace_parent or self.codex_home in self.workspace_parent.parents
                or self.workspace_parent in self.codex_home.parents):
            raise ValueError('research_codex_workspace_must_be_separate')

    @property
    def content_sha256(self):
        body = asdict(self)
        for name in ('executable', 'codex_home', 'workspace_parent'):
            body[name] = str(body[name])
        body.update(protocol=VERSION, reasoning_effort='max', hard_output_cap=False)
        return sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def copy_profile(value):
    if type(value) is not CodexLocalProfile:
        raise ValueError('research_codex_profile_invalid')
    return replace(value)


def read_profile(path):
    """Read ONE explicitly selected nonsecret configuration; never search paths."""
    try:
        path = Path(path)
        no_links(path)
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError
        with path.open('rb') as source:
            data = source.read(16385)
        if not 1 <= len(data) <= 16384:
            raise ValueError
        body = strict_json(data.decode('utf-8'))
        expected = {'executable', 'executable_sha256', 'codex_home', 'workspace_parent', 'model_id', 'timeout_seconds'}
        if type(body) is not dict or set(body) != expected:
            raise ValueError
        for name in ('executable', 'codex_home', 'workspace_parent'):
            if type(body[name]) is not str:
                raise ValueError
            body[name] = Path(body[name])
        return CodexLocalProfile(**body)
    except Exception:
        raise ValueError('research_codex_profile_invalid') from None


def _catalog(model_id):
    # Restrictive local client metadata, not a claim about provider capacity.
    # No shell/apply-patch/hosted/experimental tools or automatic instructions.
    return {'models': [{'slug': model_id, 'display_name': model_id, 'description': None,
        'default_reasoning_level': 'max',
        'supported_reasoning_levels': [{'effort': 'max', 'description': 'Explicit research effort'}],
        'shell_type': 'disabled', 'visibility': 'hide', 'supported_in_api': True, 'priority': 0,
        'availability_nux': None, 'upgrade': None, 'base_instructions': INSTRUCTIONS,
        'include_skills_usage_instructions': False, 'include_plugin_usage_instructions': False,
        'include_apps_usage_instructions': False, 'support_verbosity': False, 'default_verbosity': None,
        'apply_patch_tool_type': None, 'truncation_policy': {'mode': 'bytes', 'limit': 100000},
        'context_window': 200000, 'experimental_supported_tools': [],
        'input_modalities': ['text'], 'use_responses_lite': False}]}


def _overrides(work):
    settings = {
        'model_provider': 'pal_codex', 'model_reasoning_effort': 'max',
        'approval_policy': 'never', 'mcp_servers': {},
        'model_providers.pal_codex.name': 'OpenAI',
        'model_providers.pal_codex.requires_openai_auth': True,
        'model_providers.pal_codex.wire_api': 'responses',
        'model_providers.pal_codex.request_max_retries': 0,
        'model_providers.pal_codex.stream_max_retries': 0,
        'model_providers.pal_codex.supports_websockets': False,
        'model_providers.pal_codex.stream_idle_timeout_ms': 60000,
        'web_search': 'disabled', 'project_doc_max_bytes': 0,
        'tools.update_plan.enabled': False, 'tools.experimental_request_user_input.enabled': False,
        'include_environment_context': False, 'include_collaboration_mode_instructions': False,
        'include_apps_instructions': False, 'skills.include_instructions': False,
        'skills.bundled.enabled': False, 'check_for_update_on_startup': False,
        'analytics.enabled': False, 'feedback.enabled': False, 'history.persistence': 'none',
        'cli_auth_credentials_store': 'file', 'model_catalog_json': str(work / 'models.json'),
        'sqlite_home': str(work / 'state'), 'log_dir': str(work / 'logs'),
        'suppress_unstable_features_warning': True,
    }
    for flag in ('shell_tool', 'unified_exec', 'multi_agent', 'multi_agent_v2', 'plugins', 'hooks',
                 'memories', 'shell_snapshot', 'view_image', 'apps', 'recommended_plugins',
                 'skill_search', 'skill_mcp_dependency_install', 'browser_use', 'computer_use',
                 'image_generation', 'chronicle', 'goals', 'remote_models', 'api_key_model_discovery',
                 'remote_compaction_v2', 'context_management', 'token_budget', 'responses_websockets',
                 'responses_websockets_v2', 'fast_mode', 'unbounded_connection_retries',
                 'deferred_executor', 'current_time_reminder', 'sleep_tool', 'request_permissions_tool',
                 'tool_suggest', 'external_agent_memory_import', 'external_migration'):
        settings['features.' + flag] = False
    settings['features.skip_host_skill_discovery'] = True
    return settings


def _system_configuration_directory():
    # Match the pinned CLI's OS-known system directory, not an environment path.
    if sys.platform.startswith('linux'):
        return Path('/etc/codex')
    if os.name != 'nt':
        raise ValueError('research_codex_platform_not_supported')
    import ctypes
    from ctypes import wintypes as w
    import uuid
    guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID('62ab5d82-fdc1-4dc3-a9dd-070d1d495d97').bytes_le)
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, w.DWORD, w.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole = ctypes.WinDLL('ole32'); ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    value = ctypes.c_wchar_p()
    try:
        if shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(value)) != 0 or not value:
            raise ValueError('research_codex_system_directory_unresolved')
        return Path(value.value) / 'OpenAI' / 'Codex'
    finally:
        if value:
            ole.CoTaskMemFree(ctypes.cast(value, ctypes.c_void_p))


def _require_unmanaged_profile(profile):
    # Empty-table overrides MERGE: they cannot erase inherited MCP servers.
    # Refuse managed/default configuration, never disable or rewrite a policy.
    # Supporting managed installations requires separate adapter review.
    system = _system_configuration_directory()
    paths = [profile.codex_home / name for name in ('AGENTS.md', 'AGENTS.override.md', 'managed_config.toml')]
    paths.extend(system / name for name in ('config.toml', 'requirements.toml', 'managed_config.toml'))
    for path in paths:
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise ValueError('research_codex_inherited_configuration_not_supported')


def _environment(profile, work):
    # Do not inherit PATH, provider variables, proxies, debug flags or shell hooks.
    env = {'CODEX_HOME': str(profile.codex_home), 'HOME': str(work / 'user'),
        'USERPROFILE': str(work / 'user'), 'APPDATA': str(work / 'user'),
        'LOCALAPPDATA': str(work / 'user'), 'XDG_CONFIG_HOME': str(work / 'user'),
        'XDG_DATA_HOME': str(work / 'user'), 'XDG_CACHE_HOME': str(work / 'user'),
        'TMP': str(work), 'TEMP': str(work), 'TMPDIR': str(work),
        'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'RUST_LOG': 'off', 'NO_COLOR': '1'}
    if os.name == 'nt':
        system = os.environ.get('SystemRoot')
        if not system or not Path(system).is_absolute():
            raise ValueError('research_codex_system_root_missing')
        env.update(SystemRoot=system, WINDIR=system, PATH=str(Path(system) / 'System32'))
    else:
        env['PATH'] = '/usr/bin:/bin'
    return env


class _WindowsJob:
    """Kill-on-close ownership for this child's process tree; no taskkill scan."""
    def __init__(self, pid):
        import ctypes
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                ('flags', w.DWORD), ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t),
                ('active', w.DWORD), ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]; api.CreateJobObjectW.restype = w.HANDLE
        api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        api.SetInformationJobObject.restype = w.BOOL
        api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]; api.OpenProcess.restype = w.HANDLE
        api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]; api.AssignProcessToJobObject.restype = w.BOOL
        api.CloseHandle.argtypes = [w.HANDLE]; api.CloseHandle.restype = w.BOOL
        self.api, self.handle = api, api.CreateJobObjectW(None, None)
        process = None
        try:
            limits = Extended(); limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.handle or not api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ValueError
            process = api.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
            if not process or not api.AssignProcessToJobObject(self.handle, process):
                raise ValueError
        except Exception:
            self.close()
            raise ValueError('research_codex_process_ownership_failed') from None
        finally:
            if process:
                api.CloseHandle(process)

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            if not self.api.CloseHandle(handle):
                raise ValueError('research_codex_process_cleanup_failed')


async def _run_process(args, *, data, env, cwd, timeout, stdout_limit=MAX_STDOUT_BYTES):
    """Bound pipes and reap owned children. Filesystem/startup OS delays excluded."""
    process = None
    job = None
    tasks = []
    try:
        options = {'start_new_session': True} if os.name != 'nt' else {}
        process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=cwd, env=env, limit=65536, **options)
        if os.name == 'nt':
            job = _WindowsJob(process.pid)

        async def read(stream, limit, keep):
            result, count = [], 0
            while chunk := await stream.read(16384):
                count += len(chunk)
                if count > limit:
                    raise ValueError('research_codex_output_limit')
                if keep:
                    result.append(chunk)
            return b''.join(result)

        async def send():
            process.stdin.write(data)
            await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()

        tasks = [asyncio.create_task(read(process.stdout, stdout_limit, True)),
                 asyncio.create_task(read(process.stderr, MAX_STDERR_BYTES, False)),
                 asyncio.create_task(send()), asyncio.create_task(process.wait())]
        async with asyncio.timeout(timeout):
            output, _, _, code = await asyncio.gather(*tasks)
        if code != 0:
            raise ValueError('research_codex_process_failed')
        return output
    finally:
        if process is not None:
            # Close the job even after the parent exits: descendants can own pipes.
            cleanup_error = None
            if job is not None:
                try:
                    job.close()
                except Exception:
                    cleanup_error = ValueError('research_codex_process_cleanup_failed')
                    if process.returncode is None:
                        process.kill()
            elif os.name != 'nt':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.returncode is None:
                process.kill()  # Assignment failure: no prompt has been written.
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                async with asyncio.timeout(5):
                    await process.wait()
            except TimeoutError:
                # asyncio has no public pipe-transport close; use its supported
                # CPython transport here rather than leave a blocked pipe reader.
                process._transport.close()
                raise ValueError('research_codex_process_cleanup_failed') from None
            if cleanup_error is not None:
                raise cleanup_error from None


def _run(args, **kwargs):
    # This is the project's synchronous API. Do not nest event loops silently.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise ValueError('research_codex_sync_context_required')
    factory = asyncio.ProactorEventLoop if os.name == 'nt' else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        return runner.run(_run_process(args, **kwargs))


def invoke_codex(profile, *, messages_json, max_output_tokens, call_number):
    """One explicit CLI exec, no repair/resume/retry or default model selection.

    Max output tokens is requested and checked after the response, NOT attested
    as a provider-side limit. Use only the explicit uncapped authorization path.
    """
    try:
        profile = copy_profile(profile)
        integer('call_number', call_number, 1, 32)
        prompt = build_prompt(messages_json, max_output_tokens)
        for path in (profile.executable, profile.codex_home, profile.workspace_parent):
            no_links(path)
        info = profile.executable.stat()
        if (not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= 536870912
                or (os.name == 'nt' and profile.executable.suffix.lower() != '.exe')):
            raise ValueError
        with profile.executable.open('rb') as source:
            if file_digest(source, 'sha256').hexdigest() != profile.executable_sha256:
                raise ValueError
        private_directory(profile.codex_home)
        _require_unmanaged_profile(profile)
        private_directory(profile.workspace_parent)
        with tempfile.TemporaryDirectory(prefix='pal-codex-', dir=profile.workspace_parent) as name:
            work = Path(name)
            for child in ('user', 'state', 'logs', 'workspace'):
                (work / child).mkdir(mode=0o700)
            # A fresh repository boundary prevents parent project config discovery.
            (work / 'workspace' / '.git').mkdir(mode=0o700)
            write_private(work / 'models.json', json.dumps(_catalog(profile.model_id)))
            write_private(work / 'output-schema.json', json.dumps(output_schema()))
            env = _environment(profile, work)
            version = _run([str(profile.executable), '--version'], data=b'', env=env,
                           cwd=work / 'workspace', timeout=min(15, profile.timeout_seconds), stdout_limit=512)
            if version.strip() != ('codex-cli ' + CLI_VERSION).encode():
                raise ValueError
            args = [str(profile.executable), 'exec', '--ephemeral', '--ignore-user-config',
                '--strict-config', '--skip-git-repo-check', '--json', '--sandbox', 'read-only',
                '--color', 'never', '-m', profile.model_id, '--output-schema', str(work / 'output-schema.json')]
            for key, value in _overrides(work).items():
                args.extend(('-c', key + '=' + json.dumps(value, ensure_ascii=False)))
            args.append('-')
            raw = _run(args, data=prompt, env=env, cwd=work / 'workspace', timeout=profile.timeout_seconds)
            return parse_output(raw, call_number=call_number, max_output_tokens=max_output_tokens)
    except Exception:
        # Never expose argv, paths, config, stdout/stderr or chained CLI errors.
        raise ValueError('research_codex_invocation_failed') from None
