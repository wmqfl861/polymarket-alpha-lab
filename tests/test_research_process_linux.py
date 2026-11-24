"""Synthetic Linux containment fixtures and launch-policy tests; no provider.

Non-native tests validate declaration-only construction, artifact pinning,
credential separation and fixed-code refusals on any host. Native opt-in tests
(``POLYMARKET_ALPHA_LAB_RUN_LINUX_CONTAINMENT=1``) compile the checked-in C
stand-in ELF at test time and exercise the real rootless namespace lifecycle;
missing prerequisites after an explicit opt-in are failures, never skips. The
stand-in's version output and envelope are explicitly synthetic and are never
official-image evidence.
"""
from contextlib import ExitStack
from hashlib import sha256
import ast
import json
import os
from pathlib import Path
import posixpath
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import types

import pytest

from polymarket_alpha_lab import research_linux_relay as relay
from polymarket_alpha_lab import research_process_linux as linux
from polymarket_alpha_lab.research_claude_exec import CLAUDE_VERSION
from polymarket_alpha_lab.research_process import (
    ResearchProcessError, ResearchProcessSpec, run_research_process,
)

CONTAINMENT_ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_RUN_LINUX_CONTAINMENT') == '1'
HELPER_DIGEST = linux._helper_digest()

# Fixed synthetic paths: golden digests must not depend on tmp_path layout.
FIXED_WRAPPER = '/opt/pal-synthetic/bin/bwrap'
FIXED_INTERP = '/opt/pal-synthetic/bin/python3'
FIXED_RUNTIME = '/opt/pal-synthetic/runtime/libc.so.6'
FIXED_CGROUP = '/opt/pal-synthetic/cgroup'

STANDIN_C_SOURCE = r'''
/* Synthetic stand-in vendor for contained Linux tests (compile at test time).
 * Explicitly NOT the official Claude CLI: its version output and result
 * envelope are fixed synthetic fixtures and never official-image evidence.
 * Behavior is selected by argv flags and stdin markers. */
#include <arpa/inet.h>
#include <dlfcn.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

#define BUF_CAP (1u << 21)
static char input[BUF_CAP];
static char work[BUF_CAP];
static char escaped[BUF_CAP];

static void die(int code) { _exit(code); }

static void read_stdin(void) {
    size_t used = 0;
    for (;;) {
        if (used >= BUF_CAP) die(80);
        ssize_t n = read(0, input + used, BUF_CAP - used);
        if (n < 0) die(81);
        if (n == 0) break;
        used += (size_t)n;
    }
    input[used] = 0;
}

static void json_escape(const char *src) {
    char *out = escaped;
    while (*src && out + 8 < escaped + BUF_CAP) {
        unsigned char c = (unsigned char)*src++;
        if (c == '"' || c == '\\') { *out++ = '\\'; *out++ = (char)c; }
        else if (c < 0x20) { out += sprintf(out, "\\u%04x", c); }
        else { *out++ = (char)c; }
    }
    *out = 0;
}

static int find_ids(char ids[][160]) {
    /* Depth-independent extraction with deduplication: the prompt embeds
     * the transcript one or more times, keys carry varying backslash
     * runs, and later observations repeat earlier source ids. */
    const char *cursor = strstr(input, "sources");
    if (!cursor) return -1;
    int count = 0;
    while (count < 8) {
        const char *hit = strstr(cursor, "source_id");
        if (!hit) break;
        const char *after = hit + 9;
        int guard = 0;
        while ((*after == '"' || *after == '\\') && guard < 6) { after++; guard++; }
        if (*after != ':') { cursor = hit + 9; continue; }
        after++;
        while (*after == ' ' || *after == '\\') after++;
        if (*after != '"') { cursor = hit + 9; continue; }
        after++;
        const char *value = after;
        const char *endp = value;
        while (*endp && *endp != '"' && *endp != '\\') endp++;
        size_t length = (size_t)(endp - value);
        if (length == 0 || length >= 159) break;
        int duplicate = 0;
        for (int i = 0; i < count; i++)
            if (strncmp(ids[i], value, length) == 0 && ids[i][length] == 0) { duplicate = 1; break; }
        if (!duplicate) {
            memcpy(ids[count], value, length);
            ids[count][length] = 0;
            count++;
        }
        cursor = endp;
    }
    return count;
}
static void emit(const char *calls) {
    json_escape(calls);
    snprintf(work, BUF_CAP,
        "{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false,"
        "\"num_turns\":1,\"session_id\":\"11111111-1111-4111-8111-111111111111\","
        "\"duration_ms\":4,\"duration_api_ms\":3,\"stop_reason\":\"end_turn\","
        "\"result\":\"%s\",\"permission_denials\":[],"
        "\"usage\":{\"input_tokens\":3,\"output_tokens\":5,"
        "\"cache_creation_input_tokens\":7,\"cache_read_input_tokens\":11},"
        "\"modelUsage\":{\"claude-opus-5\":{\"inputTokens\":3,\"outputTokens\":5,"
        "\"cacheCreationInputTokens\":7,\"cacheReadInputTokens\":11}}}",
        escaped);
    fputs(work, stdout);
    fputc('\n', stdout);
    fflush(stdout);
}

static long flag_number(int argc, char **argv, const char *prefix) {
    size_t n = strlen(prefix);
    for (int i = 1; i < argc; i++)
        if (strncmp(argv[i], prefix, n) == 0) return strtol(argv[i] + n, 0, 10);
    return -1;
}

static int has(int argc, char **argv, const char *flag) {
    for (int i = 1; i < argc; i++) if (strcmp(argv[i], flag) == 0) return 1;
    return 0;
}

static void sleepers(int count) {
    for (int i = 0; i < count; i++) {
        pid_t pid = fork();
        if (pid == 0) { setsid(); signal(SIGHUP, SIG_IGN); pause(); die(90); }
    }
}

int main(int argc, char **argv) {
    if (has(argc, argv, "--version")) {
        long hang = flag_number(argc, argv, "--pal-version-hang=");
        if (hang > 0) sleep((unsigned)hang);
        fputs("2.1.278 (Claude Code)\n", stdout);
        return 0;
    }
    read_stdin();
    if (strstr(input, "blocked-eth") && !strstr(input, "\\\"tool_calls\\\"")) {
        pause(); die(90);
    }
    {
        long port = flag_number(argc, argv, "--pal-deny-net=");
        if (port >= 0) {
            int fd = socket(AF_INET, SOCK_STREAM, 0);
            struct sockaddr_in address;
            memset(&address, 0, sizeof(address));
            address.sin_family = AF_INET;
            address.sin_port = htons((unsigned short)port);
            address.sin_addr.s_addr = htonl(0x7f000001u);
            if (connect(fd, (struct sockaddr *)&address, sizeof(address)) == 0) {
                fputs("NET-OPEN\n", stdout);
                return 2;
            }
            fputs("NET-DENIED\n", stdout);
            fflush(stdout);
            close(fd);
        }
    }
    if (has(argc, argv, "--pal-hang")) { pause(); die(90); }
    if (has(argc, argv, "--pal-deny-host")) {
        FILE *handle = fopen("/etc/passwd", "r");
        if (handle) { fputs("HOST-OPEN\n", stdout); return 3; }
        fputs("HOST-DENIED\n", stdout);
        fflush(stdout);
    }
    if (has(argc, argv, "--pal-write-home")) {
        char path[512];
        const char *home = getenv("HOME");
        snprintf(path, sizeof(path), "%s/marker", home ? home : "/");
        FILE *handle = fopen(path, "w");
        if (handle) { fprintf(handle, "%ld\n", (long)getpid()); fclose(handle); }
        fputs("HOME-WRITTEN\n", stdout);
        fflush(stdout);
    }
    if (has(argc, argv, "--pal-escape")) sleepers(2);
    long forks = flag_number(argc, argv, "--pal-forks=");
    if (forks >= 0) sleepers((int)forks);
    long fill = flag_number(argc, argv, "--pal-fill=");
    if (fill >= 0) {
        char path[512];
        snprintf(path, sizeof(path), "%s", fill == 1 ? "/tmp/fill" : "/pal/scratch/fill");
        FILE *handle = fopen(path, "w");
        unsigned long blocks = 0;
        static char chunk[65536];
        memset(chunk, 'x', sizeof(chunk));
        while (handle && fwrite(chunk, 1, sizeof(chunk), handle) == sizeof(chunk)) blocks++;
        if (handle) fclose(handle);
        printf("FILLED=%lu\n", blocks);
        fflush(stdout);
    }
    long allocate = flag_number(argc, argv, "--pal-alloc=");
    if (allocate > 0) {
        char *memory = calloc((size_t)allocate, 1024 * 1024);
        if (!memory) { fputs("ALLOC-REFUSED\n", stdout); return 0; }
        for (long i = 0; i < allocate; i++) memory[i * 1024 * 1024] = 1;
        fputs("ALLOC-OK\n", stdout);
        fflush(stdout);
    }
    if (has(argc, argv, "--pal-dep")) {
        void *library = dlopen("libpaldep.so", RTLD_NOW);
        if (!library) { fputs("DEP-MISSING\n", stdout); return 4; }
        int (*value)(void) = (int (*)(void))dlsym(library, "pal_dep_value");
        if (!value) { fputs("DEP-NOSYM\n", stdout); return 5; }
        printf("DEP=%d\n", value());
        fflush(stdout);
    }
    if (has(argc, argv, "--pal-stderr")) { fputs("SENTINEL-STDERR\n", stderr); fflush(stderr); }
    if (has(argc, argv, "--pal-flood")) {
        static char chunk[65536];
        memset(chunk, 'F', sizeof(chunk));
        for (;;) { ssize_t n = write(1, chunk, sizeof(chunk)); (void)n; }
    }
    long delay = flag_number(argc, argv, "--pal-delay=");
    if (delay > 0) sleep((unsigned)delay);
    char ids[8][160];
    if (strstr(input, "tool_calls") == NULL) {
        emit("{\"calls\":[{\"name\":\"search_evidence\","
             "\"arguments_json\":\"{\\\"query\\\":\\\"*\\\"}\"}]}");
    } else {
        int count = find_ids(ids);
        int finish = strstr(input, "reference") != NULL;
        if (count <= 0) die(82);
        if (!finish) {
            snprintf(work, BUF_CAP, "{\"calls\":[");
            size_t used = strlen(work);
            for (int i = 0; i < count; i++)
                used += (size_t)snprintf(work + used, BUF_CAP - used,
                    "%s{\"name\":\"read_evidence\","
                    "\"arguments_json\":\"{\\\"source_id\\\":\\\"%s\\\"}\"}",
                    i ? "," : "", ids[i]);
            snprintf(work + used, BUF_CAP - used, "]}");
            emit(work);
        } else {
            snprintf(work, BUF_CAP, "{\"calls\":[{\"name\":\"finish_research\",\"arguments_json\":\"{\\\"probability_yes\\\":\\\"0.6\\\",\\\"confidence\\\":\\\"0.5\\\",\\\"summary\\\":\\\"Synthetic contained research\\\",\\\"source_ids\\\":[");
            size_t used = strlen(work);
            for (int i = 0; i < count; i++)
                used += (size_t)snprintf(work + used, BUF_CAP - used, "%s\\\"%s\\\"",
                                         i ? "," : "", ids[i]);
            snprintf(work + used, BUF_CAP - used, "]}\"}]}");
            emit(work);
        }
    }
    long code = flag_number(argc, argv, "--pal-exit=");
    return code >= 0 ? (int)code : 0;
}
'''

DEP_C_SOURCE = 'int pal_dep_value(void) { return 42; }\n'

FAKE_WRAPPER_C_SOURCE = r'''
#include <stdio.h>
int main(int argc, char **argv) {
    (void)argc;
    fputs("bwrap: Unknown option --pass-fd\n", stderr);
    return 1;
}
'''


def _compile(directory, name, source, *flags):
    compiler = shutil.which('cc') or shutil.which('gcc')
    if compiler is None:
        pytest.fail('C compiler (cc/gcc) required to compile the synthetic ELF')
    target = directory / name
    (directory / (name + '.c')).write_text(source, encoding='ascii')
    subprocess.run([compiler, '-O1', '-o', str(target), str(directory / (name + '.c')), *flags],
                   check=True, capture_output=True, timeout=180)
    payload = target.read_bytes()
    return str(target), sha256(payload).hexdigest(), len(payload)


def compile_standin(directory):
    """Compile the synthetic stand-in ELF from checked-in source at test time.

    No prebuilt binary blobs and no Python-command shims (A3)."""
    return _compile(directory, 'pal-standin', STANDIN_C_SOURCE, '-ldl')


def require_native_containment():
    """Fail closed (never skip) when the explicit opt-in lacks prerequisites."""
    if not CONTAINMENT_ENABLED:
        pytest.skip('explicit native containment proof is opt-in')
    problems = []
    if sys.platform != 'linux':
        problems.append('linux host required')
    if not (shutil.which('cc') or shutil.which('gcc')):
        problems.append('C compiler (cc/gcc) required')
    if not (shutil.which('bwrap') or Path('/usr/bin/bwrap').is_file()):
        problems.append('bwrap containment executable required')
    root = os.environ.get('POLYMARKET_ALPHA_LAB_LINUX_CGROUP_ROOT', '')
    if not root or not Path(root, 'cgroup.controllers').is_file():
        problems.append('POLYMARKET_ALPHA_LAB_LINUX_CGROUP_ROOT must name a delegated '
                        'cgroup v2 subtree with memory and pids controllers')
    else:
        controllers = Path(root, 'cgroup.controllers').read_text().split()
        if 'memory' not in controllers or 'pids' not in controllers:
            problems.append('delegated cgroup subtree lacks memory/pids controllers')
    if problems:
        pytest.fail('containment prerequisites missing: ' + '; '.join(problems))


def _interpreter_stdlib_root(interpreter):
    probe = 'import sysconfig;print(sysconfig.get_paths()["stdlib"])'
    completed = subprocess.run([interpreter, '-S', '-c', probe], capture_output=True,
                               check=True, timeout=60, env={'PATH': '/usr/bin:/bin'})
    return Path(completed.stdout.decode('utf-8').strip())


def parse_ld_trace_pairs(trace_text):
    """Extract ``soname => hostpath`` pairs from LD_TRACE_LOADED_OBJECTS text.

    Arrow-less lines (linux-vdso, the dynamic loader itself, statically
    linked binaries) and ``=> not found`` resolutions carry no bindable
    host path and are ignored. Pure string parsing, so it is testable on
    any host; the traced subprocess itself runs only on Linux.
    """
    pairs = []
    for line in trace_text.splitlines():
        if '=>' not in line:
            continue
        left, right = line.split('=>', 1)
        soname = left.strip()
        target = right.strip().split(' ', 1)[0] if right.strip() else ''
        if not soname or not target.startswith('/'):
            continue
        pairs.append((soname, target))
    return pairs


def soname_default_search_bindings(trace_text):
    """Map interpreter trace pairs to guest bindings reachable by soname.

    /proc/self/maps exposes version-suffixed real paths (libz.so.1.3,
    libexpat.so.1.9.1) while the guest binary's DT_NEEDED entries name the
    soname, and the namespace carries no ld.so cache: the loader therefore
    falls back to its default search directories and needs the soname path
    there. Every traced pair binds at ``dirname(hostpath)/soname`` (the
    trace's right side is usually already that soname-resolved path, e.g.
    /lib/x86_64-linux-gnu/libz.so.1); pairs collapsing onto one guest path
    keep the first binding. posixpath keeps the guest path POSIX-shaped on
    Windows development hosts.
    """
    bindings = {}
    for soname, hostpath in parse_ld_trace_pairs(trace_text):
        guest = posixpath.join(posixpath.dirname(hostpath), soname)
        bindings.setdefault(guest, hostpath)
    return bindings


# The stdlib modules the closure probe imports before tracing: pure-Python
# stdlib files are only captured when actually imported by the probe, so this
# tuple is the union of the top-level stdlib imports of BOTH guest helper
# sources (HELPER_SOURCE and RELAY_HELPER_SOURCE). A helper import outside
# this tuple dies in the namespace with ModuleNotFoundError because its file
# was never bound into /pal/runtime/lib/python3.12/ (preview run 37716295041:
# the relay helper's socket). The coupling test at the bottom of this module
# keeps this tuple a superset of both sources' imports.
CLOSURE_PROBE_STDLIB_MODULES = (
    'hashlib', 'json', 'os', 'select', 'signal', 'socket', 'sys', 'time')


def _closure_probe_program():
    """The single ``-c`` program whose import and loaded-object trace defines
    the guest runtime closure: one import statement over the sorted module
    tuple, then sys.modules files plus /proc/self/maps objects."""
    return (
        'import ' + ','.join(CLOSURE_PROBE_STDLIB_MODULES) + '\n'
        'files={m.__file__ for m in sys.modules.values() if getattr(m,"__file__",None)}\n'
        'mapped=set()\n'
        'for line in open("/proc/self/maps"):\n'
        '    path=line.rstrip().rsplit(" ",1)[-1]\n'
        '    if path.startswith("/") and ".so" in path: mapped.add(path)\n'
        'print(json.dumps(sorted(files|mapped)))\n')


def discover_runtime_closure(interpreter, vendor_path):
    """Measure the interpreter stdlib/lib closure plus the vendor's ELF deps.

    Every entry comes from an actual import trace or loaded-object listing;
    nothing is invented or defaulted."""
    probe = _closure_probe_program()
    completed = subprocess.run([interpreter, '-S', '-B', '-c', probe], capture_output=True,
                               check=True, timeout=120, env={'PATH': '/usr/bin:/bin'})
    traced = set(json.loads(completed.stdout.decode('utf-8')))
    traced_env = dict(os.environ)
    traced_env['LD_TRACE_LOADED_OBJECTS'] = '1'
    vendor_deps = subprocess.run([vendor_path], capture_output=True, check=False, timeout=60,
                                 env=traced_env)
    for line in vendor_deps.stdout.decode('utf-8', 'replace').splitlines():
        for token in line.split():
            if token.startswith('/'):
                traced.add(token)
    # The interpreter's own DT_NEEDED closure must also be reachable by
    # soname in the loader's default search directories: maps only exposed
    # version-suffixed real paths and the namespace has no ld.so cache.
    interpreter_deps = subprocess.run([interpreter], capture_output=True, check=False,
                                      timeout=60, env=traced_env)
    soname_bindings = soname_default_search_bindings(
        interpreter_deps.stdout.decode('utf-8', 'replace'))
    traced.update(soname_bindings.values())
    soname_guests = {host_path: guest for guest, host_path in soname_bindings.items()}
    stdlib_root = _interpreter_stdlib_root(interpreter)
    runtime, seen, digests = [], set(), set()
    for host in sorted(traced):
        path = Path(host)
        if not path.is_file():
            continue
        payload = path.read_bytes()
        digest = sha256(payload).hexdigest()
        if digest in digests:
            continue
        try:
            relative = path.relative_to(stdlib_root)
            guest = '/pal/runtime/lib/python3.12/' + str(relative).replace('\\', '/')
        except ValueError:
            # System libraries (dynamic loader, libc, ...) bind at their
            # original absolute paths: the ELF PT_INTERP interpreter and the
            # loader's default search must resolve inside the namespace.
            # Interpreter-traced dependencies prefer their default-search
            # soname path so DT_NEEDED resolution succeeds without a cache.
            guest = soname_guests.get(host, str(path))
        if guest in seen:
            continue
        seen.add(guest)
        digests.add(digest)
        runtime.append(linux.LinuxRuntimeFile(guest, str(path.resolve()), digest, len(payload)))
    assert runtime, 'runtime closure discovery found nothing'
    return runtime


def build_native_launch(directory, **launch_changes):
    """Assemble a real launch from measured host facts (native tests only)."""
    require_native_containment()
    vendor = directory / 'vendor'
    vendor.mkdir(parents=True, exist_ok=True)
    binary, digest, size = compile_standin(vendor)
    interpreter = str(Path(sys.executable).resolve())
    interp_bytes = Path(interpreter).read_bytes()
    wrapper_path = shutil.which('bwrap') or '/usr/bin/bwrap'
    wrapper_bytes = Path(wrapper_path).read_bytes()
    dep_directory = directory / 'dep'
    dep_directory.mkdir(parents=True, exist_ok=True)
    dep_path, dep_digest, dep_size = _compile(dep_directory, 'libpaldep.so', DEP_C_SOURCE,
                                              '-shared', '-fPIC')
    runtime = discover_runtime_closure(interpreter, binary)
    runtime.append(linux.LinuxRuntimeFile('/pal/runtime/lib/libpaldep.so', dep_path,
                                          dep_digest, dep_size))
    values = dict(
        wrapper=linux.LinuxArtifactPin(str(Path(wrapper_path).resolve()),
                                       sha256(wrapper_bytes).hexdigest(), len(wrapper_bytes)),
        helper_sha256=HELPER_DIGEST,
        interpreter=linux.LinuxArtifactPin(interpreter, sha256(interp_bytes).hexdigest(),
                                           len(interp_bytes)),
        supervisor_python_home=sys.base_prefix,
        runtime_files=tuple(runtime),
        vendor_guest_path='/pal/vendor/claude',
        helper_guest_path='/pal/runtime/helper.py',
        vendor_size_bytes=size,
        expected_version_output=CLAUDE_VERSION + ' (Claude Code)\n',
        cgroup_root=os.environ['POLYMARKET_ALPHA_LAB_LINUX_CGROUP_ROOT'],
        scratch_size_bytes=64 * 1024 * 1024,
        guest_tmp_size_bytes=32 * 1024 * 1024,
        memory_max_bytes=512 * 1024 * 1024,
        pids_max=32)
    values.update(launch_changes)
    return binary, digest, size, linux.LinuxLaunchSpec(**values)


def contained_vendor_spec(binary, digest, work, key='SYNTHETIC-NOT-A-REAL-KEY', argv=()):
    return ResearchProcessSpec(
        (binary, '--print', '--bare', *argv), str(work),
        (('HOME', str(work / 'host-home')), ('CLAUDE_CONFIG_DIR', str(work / 'host-config')),
         ('ANTHROPIC_BASE_URL', 'https://gateway.example.invalid'),
         ('CLAUDE_CODE_MAX_OUTPUT_TOKENS', '8192'),
         ('ANTHROPIC_API_KEY', key)), digest, 30000)


def fixed_runtime_files():
    return (linux.LinuxRuntimeFile('/pal/runtime/lib/libc.so.6', FIXED_RUNTIME,
                                   'e' * 64, 123456),)


def fixed_launch():
    return linux.LinuxLaunchSpec(
        wrapper=linux.LinuxArtifactPin(FIXED_WRAPPER, 'a' * 64, 65536),
        helper_sha256=HELPER_DIGEST,
        interpreter=linux.LinuxArtifactPin(FIXED_INTERP, 'b' * 64, 12345678),
        supervisor_python_home='/opt/pal-synthetic/python-home',
        runtime_files=fixed_runtime_files(),
        vendor_guest_path='/pal/vendor/claude',
        helper_guest_path='/pal/runtime/helper.py',
        vendor_size_bytes=98765432,
        expected_version_output=CLAUDE_VERSION + ' (Claude Code)\n',
        cgroup_root=FIXED_CGROUP)


def synthetic_launch(tmp_path=None, **changes):
    """Declaration-only launch with fixed synthetic POSIX paths (no files)."""
    values = dict(
        wrapper=linux.LinuxArtifactPin(FIXED_WRAPPER, 'a' * 64, 65536),
        helper_sha256=HELPER_DIGEST,
        interpreter=linux.LinuxArtifactPin(FIXED_INTERP, 'b' * 64, 12345678),
        supervisor_python_home='/opt/pal-synthetic/python-home',
        runtime_files=fixed_runtime_files(),
        vendor_guest_path='/pal/vendor/claude',
        helper_guest_path='/pal/runtime/helper.py',
        vendor_size_bytes=98765432,
        expected_version_output=CLAUDE_VERSION + ' (Claude Code)\n',
        cgroup_root=FIXED_CGROUP)
    values.update(changes)
    return linux.LinuxLaunchSpec(**values)


def vendor_spec(tmp_path, environment=None):
    return ResearchProcessSpec(
        (str(tmp_path / 'claude-native'), '--print', '--bare'), str(tmp_path / 'work'),
        tuple(environment or (
            ('HOME', str(tmp_path / 'home')), ('CLAUDE_CONFIG_DIR', str(tmp_path / 'config')),
            ('ANTHROPIC_BASE_URL', 'https://gateway.example.invalid'),
            ('CLAUDE_CODE_MAX_OUTPUT_TOKENS', '8192'),
            ('ANTHROPIC_API_KEY', 'SYNTHETIC-NOT-A-REAL-KEY'))),
        'd' * 64, 15000)


def relaunch(launch, **changes):
    values = {name: getattr(launch, name) for name in
              linux.LinuxLaunchSpec.__dataclass_fields__}
    values.update(changes)
    return linux.LinuxLaunchSpec(**values)


_REAL_POPEN = subprocess.Popen
_REAL_SOCKET = socket.socket
_RELAY_HELPER_DIGEST = relay._relay_helper_digest()


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    # Native opt-in tests intentionally spawn real supervisors and wrappers;
    # the tripwires apply only to the synthetic unit tests.
    if CONTAINMENT_ENABLED:
        return
    monkeypatch.setattr('socket.socket', lambda *a, **k: pytest.fail('network entered'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('process spawned'))


def test_no_external_io_fixture_is_inert_under_the_native_optin(no_external_io):
    """Regression: with the explicit opt-in the autouse tripwire must leave
    the real process/network surfaces untouched (native tests spawn on
    purpose); without it, the synthetic unit tests stay guarded."""
    if CONTAINMENT_ENABLED:
        assert subprocess.Popen is _REAL_POPEN
    else:
        assert subprocess.Popen is not _REAL_POPEN


def test_launch_spec_construction_is_declaration_only(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail('launch construction performed I/O')
    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    monkeypatch.setattr(Path, 'is_dir', forbidden)
    launch = synthetic_launch(tmp_path)
    assert launch.launch_schema == 'research-linux-launch-v1'
    assert launch.egress_policy == 'offline'
    assert launch.memory_swap_max_bytes == 0
    assert list(tmp_path.iterdir()) == []


def test_helper_pin_binds_the_checked_in_source(tmp_path):
    assert HELPER_DIGEST == sha256(linux.HELPER_SOURCE.encode('utf-8')).hexdigest()
    with pytest.raises(ValueError, match='linux_launch_spec_invalid'):
        synthetic_launch(tmp_path, helper_sha256='0' * 64)


@pytest.mark.parametrize('changes', [
    {'launch_schema': 'research-linux-launch-v2'},
    {'helper_protocol_version': 'research-linux-helper-v2'},
    {'namespaces': ('user', 'mount', 'pid', 'net', 'ipc')},
    {'namespaces': ('user', 'mount', 'pid', 'net', 'ipc', 'uts', 'cgroup')},
    {'version_argument': '-V'},
    {'cgroup_allocation_rule': 'shared-directory'},
    {'guest_home_path': '/pal/work'},
    {'guest_scratch_path': '/pal/work'},
    {'vendor_guest_path': 'relative/claude'},
    {'helper_guest_path': '/'},
    {'cgroup_root': '/not/../absolute'},
    {'supervisor_python_home': 'relative'},
    {'expected_version_output': '2.1.278'},
    {'expected_version_output': '2.1.278\n\n'},
    {'expected_version_output': '2.1.278\r\n'},
    {'expected_version_output': '2.1.278\n\x01'},
    {'expected_version_output': ''},
    {'runtime_files': ()},
    {'wrapper': linux.LinuxArtifactPin('/other-wrapper', 'b' * 64, 10)},
    {'interpreter': linux.LinuxArtifactPin('/other-interpreter', 'a' * 64, 10)},
])
def test_invalid_launch_mutations_fail_closed(tmp_path, changes):
    with pytest.raises(ValueError, match='linux_launch_spec_invalid'):
        relaunch(synthetic_launch(tmp_path), **changes)


@pytest.mark.parametrize('egress', ['host', 'loopback-relay', 'qualified', 'online', ''])
def test_no_production_egress_constructor_exists(tmp_path, egress):
    with pytest.raises(ValueError, match='linux_launch_spec_invalid'):
        relaunch(synthetic_launch(tmp_path), egress_policy=egress)


@pytest.mark.parametrize('field,weak,strong', [
    ('memory_max_bytes', 4294967297, 8388608),
    ('scratch_size_bytes', 1073741825, 1048576),
    ('guest_tmp_size_bytes', 536870913, 1048576),
    ('pids_max', 65, 8),
    ('memory_max_bytes', 0, 8388608),
    ('pids_max', 0, 8),
    ('memory_swap_max_bytes', 1, 0),
])
def test_declared_resource_bounds_cannot_be_weakened(tmp_path, field, weak, strong):
    with pytest.raises(ValueError, match='linux_launch_spec_invalid'):
        relaunch(synthetic_launch(tmp_path), **{field: weak})
    assert getattr(relaunch(synthetic_launch(tmp_path), **{field: strong}), field) == strong


@pytest.mark.parametrize('changes', [
    {'wrapper': linux.LinuxArtifactPin('/same', 'a' * 64, 10),
     'interpreter': linux.LinuxArtifactPin('/same', 'b' * 64, 20)},
    {'wrapper': linux.LinuxArtifactPin('/w1', 'a' * 64, 10),
     'interpreter': linux.LinuxArtifactPin('/w2', 'a' * 64, 20)},
])
def test_wrapper_and_interpreter_keep_separate_identities(tmp_path, changes):
    with pytest.raises(ValueError, match='linux_launch_spec_invalid'):
        relaunch(synthetic_launch(tmp_path), **changes)


def test_runtime_members_cannot_alias_wrapper_or_interpreter(tmp_path):
    alias_path = linux.LinuxArtifactPin('/w', 'a' * 64, 10)
    member = linux.LinuxRuntimeFile('/pal/runtime/lib/x.so', '/w', 'a' * 64, 10)
    with pytest.raises(ValueError, match='linux_launch_spec_invalid'):
        relaunch(synthetic_launch(tmp_path), wrapper=alias_path,
                 interpreter=linux.LinuxArtifactPin('/i', 'b' * 64, 20),
                 runtime_files=(member,))


@pytest.mark.parametrize('allowed', [
    (), ('ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'CLAUDE_CODE_MAX_OUTPUT_TOKENS',
         'CLAUDE_CONFIG_DIR'),
    ('ANTHROPIC_API_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL',
     'CLAUDE_CODE_MAX_OUTPUT_TOKENS', 'CLAUDE_CONFIG_DIR', 'HOME'),
    ('ANTHROPIC_BASE_URL', 'CLAUDE_CODE_MAX_OUTPUT_TOKENS', 'CLAUDE_CONFIG_DIR',
     'HOME', 'ANTHROPIC_API_KEY'),
    tuple(sorted(('ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'CLAUDE_CODE_MAX_OUTPUT_TOKENS',
                  'CLAUDE_CONFIG_DIR', 'HOME', 'PATH-HOST'))),
    ('ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'CLAUDE_CODE_MAX_OUTPUT_TOKENS',
     'CLAUDE_CONFIG_DIR', 'HOME', 7),
])
def test_allowed_guest_env_is_sorted_unique_and_required(tmp_path, allowed):
    with pytest.raises(ValueError, match='linux_launch_spec_invalid'):
        relaunch(synthetic_launch(tmp_path), allowed_guest_env=allowed)


def test_policy_dict_is_secret_free_ephemeral_free_and_stable():
    policy = fixed_launch().policy_dict()
    encoded = json.dumps(policy, sort_keys=True)
    # Key NAMES are policy; credential VALUES, random allocation names and
    # prompt material must never appear.
    for forbidden in ('pal-call', 'SYNTHETIC', 'stdin', 'messages_json'):
        assert forbidden not in encoded
    assert 'ANTHROPIC_API_KEY' in policy['environment']['allowlist']
    assert policy['egress']['production_egress'] == 'unavailable'
    assert policy['egress']['host_network_fallback'] == 'forbidden'
    assert policy['egress']['relay'] == 'not-shipped-in-this-node'
    assert policy['protocol']['retries'] == 'none'
    assert policy['protocol']['per_phase_executions'] == 1
    assert policy['protocol']['teardown_between_phases'] == 'verified-empty-subtree'
    assert policy['environment']['credential'] == 'one-use-anonymous-pipe-model-phase-only'
    assert policy['mounts']['writable_host_surfaces'] == []
    assert policy['descriptors']['inherited_from_parent'] == []
    assert fixed_launch().policy_dict() == policy


def _pinned_file(tmp_path, payload=b'\x7fELF' + b'payload' * 8):
    path = tmp_path / 'artifact.bin'
    path.write_bytes(payload)
    return str(path), sha256(payload).hexdigest(), len(payload)


def test_verify_pinned_bytes_accepts_exact_identity(tmp_path):
    path, digest, size = _pinned_file(tmp_path)
    assert linux.verify_pinned_bytes(path, digest, size, max_bytes=size + 1,
                                     require_elf=True) == b'\x7fELF'


@pytest.mark.parametrize('defect', ['sha', 'size', 'max-bytes', 'magic', 'directory', 'missing'])
def test_verify_pinned_bytes_rejects_substitution(tmp_path, defect):
    payload = b'\x7fELF' + b'vendor-bytes' * 16
    path = tmp_path / 'image.bin'
    path.write_bytes(payload)
    digest, size = sha256(payload).hexdigest(), len(payload)
    expect = ('0' * 64, size) if defect == 'sha' else (
        (digest, size - 1) if defect == 'size' else (digest, size))
    limit = size - 1 if defect == 'max-bytes' else size + 1
    if defect == 'magic':
        path = tmp_path / 'script.bin'
        path.write_bytes(b'#!/bin/sh\n')
        expect = (sha256(path.read_bytes()).hexdigest(), path.stat().st_size)
    elif defect == 'directory':
        path = tmp_path / 'dir.bin'
        path.mkdir()
    elif defect == 'missing':
        path = tmp_path / 'missing.bin'
    with pytest.raises((ValueError, OSError)):
        linux.verify_pinned_bytes(str(path), expect[0], expect[1], max_bytes=limit,
                                  require_elf=True)


@pytest.mark.skipif(sys.platform != 'linux' or not hasattr(os, 'O_NOFOLLOW'),
                    reason='O_NOFOLLOW symlink refusal is a Linux admission rule')
def test_verify_pinned_bytes_refuses_symlink_replacement(tmp_path):
    path, digest, size = _pinned_file(tmp_path)
    link = tmp_path / 'link.bin'
    try:
        os.symlink(path, link)
    except OSError:
        pytest.skip('symlink creation unavailable on this host')
    with pytest.raises(OSError):
        linux.verify_pinned_bytes(str(link), digest, size, max_bytes=size + 1,
                                  require_elf=True)


def test_guest_configuration_separates_credential_and_substitutes_paths(tmp_path):
    launch = synthetic_launch(tmp_path)
    spec = vendor_spec(tmp_path)
    pairs, credential = linux._guest_configuration(spec, launch)
    keys = dict(pairs)
    assert credential == 'SYNTHETIC-NOT-A-REAL-KEY'
    assert 'ANTHROPIC_API_KEY' not in keys
    assert 'HOME' not in keys and 'CLAUDE_CONFIG_DIR' not in keys
    assert keys['ANTHROPIC_BASE_URL'] == 'https://gateway.example.invalid'
    assert tuple(keys) == tuple(sorted(keys))
    assert credential not in json.dumps([list(pair) for pair in pairs])


@pytest.mark.parametrize('environment', [
    (('HOME', '/h'), ('ANTHROPIC_API_KEY', 'SYNTHETIC-KEY-1'), ('SECRET_ENV', 'x')),
    (('HOME', '/h'),),
    (('HOME', '/h'), ('ANTHROPIC_API_KEY', '')),
    (('HOME', '/h'), ('ANTHROPIC_API_KEY', 'has space')),
    (('HOME', '/h'), ('ANTHROPIC_API_KEY', 'k' * 4097)),
])
def test_guest_configuration_refuses_forbidden_env_and_bad_credentials(tmp_path, environment):
    with pytest.raises(ResearchProcessError):
        linux._guest_configuration(vendor_spec(tmp_path, environment=environment),
                                   synthetic_launch(tmp_path))


@pytest.mark.skipif(sys.platform == 'linux', reason='non-linux platform refusal')
def test_run_research_process_requires_linux_for_contained_path(tmp_path):
    with pytest.raises(ResearchProcessError, match='platform_unsupported'):
        run_research_process(spec=vendor_spec(tmp_path), stdin=b'{}', allow_process_start=True,
                             linux_launch=synthetic_launch())
    with pytest.raises(ResearchProcessError, match='research_process_failed'):
        run_research_process(spec=vendor_spec(tmp_path), stdin=b'{}', allow_process_start=True,
                             linux_launch=object())


def test_run_research_process_none_launch_keeps_legacy_path(monkeypatch, tmp_path):
    monkeypatch.setattr(linux, 'run_linux_contained_process',
                        lambda **kwargs: pytest.fail('contained path entered'))
    monkeypatch.setattr('polymarket_alpha_lab.research_process._verify_executable',
                        lambda _: None)
    monkeypatch.setattr('polymarket_alpha_lab.research_process._spawn',
                        lambda value: (_ for _ in ()).throw(OSError('legacy path')))
    with pytest.raises(ResearchProcessError, match='research_process_failed'):
        run_research_process(spec=vendor_spec(tmp_path), stdin=b'{}', allow_process_start=True,
                             linux_launch=None)


def test_cleanup_failure_is_reported_not_swallowed():
    slots = (
        ('_liveness_w', os.O_WRONLY),
        ('_prompt_w', os.O_WRONLY),
        ('_credential_w', os.O_WRONLY),
        ('_control_w', os.O_WRONLY),
        ('_stdout_r', os.O_RDONLY),
        ('_stderr_r', os.O_RDONLY),
        ('_report_r', os.O_RDONLY),
    )
    session = linux._ContainedSession.__new__(linux._ContainedSession)
    session._supervisor = None

    expected_exit = SystemExit(0)

    class Stuck:
        def __init__(self):
            self.kill_called = False

        def poll(self):
            return None

        def kill(self):
            self.kill_called = True
            raise expected_exit

    class FakeAdmission:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    stuck = Stuck()
    admission = FakeAdmission()
    session._supervisor = stuck
    session._admission = admission

    ownership_transferred = False

    def close_owned_slot(name):
        if ownership_transferred:
            fd = getattr(session, name, None)
            if fd is not None:
                setattr(session, name, None)
                os.close(fd)
            return
        fd = allocated_by_slot.pop(name, None)
        if fd is not None:
            if getattr(session, name, None) == fd:
                setattr(session, name, None)
            os.close(fd)

    owner = ExitStack()
    allocated_fds = []
    allocated_by_slot = {}
    try:
        for name, flags in slots:
            fd = os.open(os.devnull, flags)
            try:
                allocated_fds.append(fd)
                allocated_by_slot[name] = fd
                owner.callback(close_owned_slot, name)
            except BaseException:
                allocated_by_slot.pop(name, None)
                os.close(fd)
                raise
            setattr(session, name, fd)

        actual_fds = tuple(getattr(session, name) for name, _ in slots)
        assert actual_fds == tuple(allocated_fds)
        assert len(set(actual_fds)) == len(slots)
        for fd in actual_fds:
            os.fstat(fd)

        ownership_transferred = True

        with pytest.raises(SystemExit) as raised:
            session._cleanup(5)

        assert raised.value is expected_exit
        assert raised.value.code == 0
        assert stuck.kill_called
        assert admission.closed
    finally:
        owner.close()


@pytest.mark.skipif(sys.platform != 'linux',
                    reason='the native session waits on pipe descriptors')
def test_supervisor_death_does_not_discard_a_buffered_final_report(monkeypatch):
    """The await/version/ready loops historically raised the generic failure
    the moment poll() showed the supervisor exited, discarding its final
    classified report line still buffered in the pipe — a scheduling race
    latent since L5 and surfaced by the faster substituted dev server (a
    wrapper-option refusal was misreported as research_process_failed). The
    drain-first guard now delivers the buffered event instead."""
    # The dead supervisor is a real reaped Popen; narrowly lift the autouse
    # process tripwire for it (the engine-executing tests' idiom) so the
    # default stripped-opt-in full suite on Linux runs this for real.
    monkeypatch.setattr(subprocess, 'Popen', _REAL_POPEN)
    dead = subprocess.Popen([sys.executable, '-c', 'pass'])
    dead.wait()

    def session_with(report_line):
        session = linux._ContainedSession.__new__(linux._ContainedSession)
        session._stop = None
        session._deadline = time.monotonic_ns() + 10_000_000_000
        session._supervisor = dead
        report_r, report_w = os.pipe()
        os.write(report_w, report_line)
        os.close(report_w)
        session._report_r = report_r
        control_r, control_w = os.pipe()
        session._control_w = control_w
        return session, report_r, (control_r, control_w)

    classified = b'{"type":"version_done","code":"wrapper_option_unsupported"}\n'
    # _await_first_event returns the buffered event, not the generic failure.
    session, report_r, control = session_with(classified)
    try:
        assert session._await_first_event() == {
            'type': 'version_done', 'code': 'wrapper_option_unsupported'}
    finally:
        os.close(report_r)
        for fd in control:
            os.close(fd)
    # run_version_phase maps the buffered classified refusal code.
    session, report_r, control = session_with(classified)
    try:
        with pytest.raises(ResearchProcessError,
                           match='research_process_wrapper_unsupported'):
            session.run_version_phase()
    finally:
        os.close(report_r)
        for fd in control:
            os.close(fd)
    # An exited supervisor whose report channel is truly empty stays the
    # generic failure - nothing was discarded.
    session, report_r, control = session_with(b'')
    try:
        with pytest.raises(ResearchProcessError, match='research_process_failed'):
            session._await_first_event()
    finally:
        os.close(report_r)
        for fd in control:
            os.close(fd)


MODEL_PHASE_OUTPUT = b'{"synthetic":"model-output"}'


def _driver_session(monkeypatch, record, *, version=None, model=None, cleanup=None,
                    model_output=(MODEL_PHASE_OUTPUT, 7)):
    """Fake two-phase session for driver-ordering tests.

    Every phase call is recorded; version/model/cleanup inject failures. The
    one-use credential channel is written only inside the real run_model_phase
    (after readiness), so ``'model' in record`` marks the earliest point the
    credential could ever be released. Nothing real is spawned.
    """

    class FakeSession:
        def __init__(self, spec, launch, stop, deadline):
            record.append('construct')

        def _stopped(self):
            return False

        def run_version_phase(self):
            record.append('version')
            if version is not None:
                raise version

        def run_model_phase(self, stdin):
            record.append('model')
            if model is not None:
                raise model
            return model_output

        def _cleanup(self, timeout_ms):
            record.append('cleanup')
            return cleanup

    monkeypatch.setattr(linux, '_ContainedSession', FakeSession)


def _drive(monkeypatch, tmp_path, stdin=b'{"schema_version":"probe"}'):
    # Satisfy the driver's fixed platform/SIGCHLD preconditions on any
    # development host; the session itself is the fake installed above.
    monkeypatch.setattr(linux, 'sys', types.SimpleNamespace(platform='linux'))
    monkeypatch.setattr(linux, 'signal', types.SimpleNamespace(
        SIG_DFL=signal.SIG_DFL, SIGCHLD='placeholder', getsignal=lambda _: signal.SIG_DFL))
    return linux.run_linux_contained_process(spec=vendor_spec(tmp_path), stdin=stdin,
                                             launch=synthetic_launch(tmp_path), stop=None)


def test_clean_success_returns_after_cleanup_with_exact_bytes(monkeypatch, tmp_path):
    record = []
    _driver_session(monkeypatch, record)
    result = _drive(monkeypatch, tmp_path)
    assert record == ['construct', 'version', 'model', 'cleanup']
    assert type(result) is linux.ResearchProcessResult
    assert result.stdout == MODEL_PHASE_OUTPUT
    assert result.stderr_bytes == 7
    assert result.elapsed_ms >= 0


def test_cleanup_failure_after_model_success_suppresses_the_output(monkeypatch, tmp_path):
    """Regression for the return-inside-try defect: a cleanup error recorded
    by ``finally`` must suppress the successful output and flow through the
    driver's fixed error handling, never bypass it with an early return."""
    record = []
    _driver_session(monkeypatch, record,
                    cleanup=ResearchProcessError('research_process_cleanup_failed'))
    with pytest.raises(ResearchProcessError, match='research_process_cleanup_failed'):
        _drive(monkeypatch, tmp_path)
    assert record == ['construct', 'version', 'model', 'cleanup']


@pytest.mark.parametrize('model_error', [
    ResearchProcessError('research_process_nonzero_exit'),
    ValueError('research_process_launch_invalid'),
])
def test_cleanup_failure_keeps_precedence_over_ordinary_model_errors(
        monkeypatch, tmp_path, model_error):
    """An ordinary model-phase exception is superseded by a cleanup failure,
    exactly as before the suppression fix."""
    record = []
    _driver_session(monkeypatch, record, model=model_error,
                    cleanup=ResearchProcessError('research_process_cleanup_failed'))
    with pytest.raises(ResearchProcessError, match='research_process_cleanup_failed'):
        _drive(monkeypatch, tmp_path)
    assert record == ['construct', 'version', 'model', 'cleanup']


def test_original_interrupts_survive_a_concurrent_cleanup_failure(monkeypatch, tmp_path):
    record = []
    _driver_session(monkeypatch, record, model=KeyboardInterrupt('original interrupt'),
                    cleanup=ResearchProcessError('research_process_cleanup_failed'))
    with pytest.raises(KeyboardInterrupt):
        _drive(monkeypatch, tmp_path)
    assert record == ['construct', 'version', 'model', 'cleanup']


def test_version_mismatch_precedes_model_launch_and_credential_release(monkeypatch, tmp_path):
    """A malformed vendor banner fails inside the version phase: the model
    namespace is never launched and the one-use credential channel (released
    only inside run_model_phase after readiness) is never written."""
    record = []
    _driver_session(monkeypatch, record,
                    version=ResearchProcessError('research_process_version_mismatch'))
    with pytest.raises(ResearchProcessError, match='research_process_version_mismatch'):
        _drive(monkeypatch, tmp_path)
    assert record == ['construct', 'version', 'cleanup']
    assert 'model' not in record


def test_helper_source_is_stdlib_only_and_protocol_pinned():
    import ast
    tree = ast.parse(linux.HELPER_SOURCE)
    imported = {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.Import) for alias in node.names}
    assert imported <= {'hashlib', 'json', 'os', 'select', 'signal', 'sys', 'time'}
    assert 'polymarket_alpha_lab' not in linux.HELPER_SOURCE
    assert 'psycopg' not in linux.HELPER_SOURCE


# Every stdlib name either checked-in helper source may import: a name outside
# this set is a non-stdlib dependency leaking into the offline guest namespace
# and must fail loudly rather than surface as a namespace ModuleNotFoundError.
_HELPER_STDLIB_ALLOWLIST = frozenset((
    'hashlib', 'json', 'os', 'select', 'signal', 'socket', 'sys', 'time'))


def _helper_imported_module_names(source):
    """Dotted-root module names a helper source imports, from actual parsing.

    ``import X.Y`` counts as X and ``from X import y`` counts as X; relative
    imports (level > 0) cannot exist in a guest script and are ignored. The
    walk also covers function-local imports: a deferred import dies just as
    hard inside the namespace as a top-level one."""
    import ast
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split('.')[0])
    return names


def test_closure_probe_covers_every_helper_stdlib_import():
    """Couple the closure probe to both guest helper sources (pure parsing,
    no subprocess, Windows-runnable). Pure-Python stdlib files reach
    /pal/runtime/lib/python3.12/ only when the probe actually imports them,
    so a helper import the probe lacks killed the guest helper with
    ModuleNotFoundError: No module named 'socket' (preview run 37716295041).
    Both sources must stay inside the stdlib allowlist, and the probe's one
    import statement must import exactly CLOSURE_PROBE_STDLIB_MODULES."""
    import ast
    helper_imports = (_helper_imported_module_names(linux.HELPER_SOURCE)
                      | _helper_imported_module_names(relay.RELAY_HELPER_SOURCE))
    non_stdlib = helper_imports - _HELPER_STDLIB_ALLOWLIST
    assert not non_stdlib, ('helper sources import non-stdlib modules',
                            sorted(non_stdlib))
    program = ast.parse(_closure_probe_program())
    import_nodes = [node for node in program.body
                    if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert len(import_nodes) == 1 and isinstance(import_nodes[0], ast.Import)
    probed = {alias.name for alias in import_nodes[0].names}
    missing = helper_imports - probed
    assert not missing, ('closure probe does not import helper stdlib modules',
                         sorted(missing))
    assert probed == set(CLOSURE_PROBE_STDLIB_MODULES)
    assert list(CLOSURE_PROBE_STDLIB_MODULES) == sorted(CLOSURE_PROBE_STDLIB_MODULES)


def test_helper_source_mirrors_output_caps_and_credential_bound():
    """The supervisor relay must enforce the same byte caps as the parent and
    the guest must mirror the parent's 4096-byte credential bound."""
    assert 'model_output_limit' in linux.HELPER_SOURCE
    assert 'max_stdout_bytes' in linux.HELPER_SOURCE
    assert 'max_stderr_bytes' in linux.HELPER_SOURCE
    assert '8192' not in linux.HELPER_SOURCE
    assert 'size <= 4096' in linux.HELPER_SOURCE


def test_supervisor_configuration_carries_the_spec_output_caps(tmp_path):
    """Pure configuration check: the supervisor receives the parent's exact
    stdout/stderr caps, and no credential value ever enters the config."""
    spec = vendor_spec(tmp_path)
    capped = ResearchProcessSpec(spec.argv, spec.cwd, spec.environment,
                                 spec.executable_sha256, spec.timeout_ms,
                                 max_stdout_bytes=4096, max_stderr_bytes=2048)
    configuration = linux._supervisor_configuration(
        capped, synthetic_launch(), wrapper_fd=101, helper_fd=102, interp_fd=103,
        vendor_fd=104, runtime_fds=(105,), allocation='pal-call-regression',
        report_fd=106, liveness_fd=107, prompt_fd=108, credential_fd=109)
    assert configuration['max_stdout_bytes'] == 4096
    assert configuration['max_stderr_bytes'] == 2048
    encoded = json.dumps(configuration)
    assert 'SYNTHETIC-NOT-A-REAL-KEY' not in encoded
    assert 'pal-call-regression' in configuration['alloc_name']


def test_model_phase_error_maps_relay_cap_to_output_limit():
    """The supervisor's mirrored cap failure maps to the parent's fixed
    output-limit error, never to a generic failure or a hang."""
    mapped = linux._model_phase_error('model_output_limit')
    assert type(mapped) is ResearchProcessError
    assert mapped.args[0] == 'research_process_output_limit'
    assert linux._model_phase_error('phase_timeout').args[0] == 'research_process_timeout'
    assert linux._model_phase_error('teardown_failed').args[0] == 'research_process_cleanup_failed'
    assert (linux._model_phase_error('wrapper_option_unsupported').args[0]
            == 'research_process_wrapper_unsupported')
    assert linux._model_phase_error('model_nonzero_exit').args[0] == 'research_process_failed'
    assert linux._model_phase_error(None).args[0] == 'research_process_failed'


def test_standin_source_is_synthetic_and_secret_free():
    assert 'api_key' not in STANDIN_C_SOURCE
    assert 'ANTHROPIC' not in STANDIN_C_SOURCE
    assert 'sk-' not in STANDIN_C_SOURCE
    # The stand-in prints the exact official identity banner for --version,
    # never the pre-correction bare version line.
    assert '--version' in STANDIN_C_SOURCE
    assert (CLAUDE_VERSION + ' (Claude Code)\\n') in STANDIN_C_SOURCE
    assert (CLAUDE_VERSION + '\\n') not in STANDIN_C_SOURCE


SYNTHETIC_INTERPRETER_LD_TRACE = (
    '\tlinux-vdso.so.1 (0x00007ffc7a9be000)\n'
    '\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007f2e8c9a1000)\n'
    '\tlibz.so.1 => /lib/x86_64-linux-gnu/libz.so.1 (0x00007f2e8c3b1000)\n'
    '\tlibexpat.so.1 => /usr/lib/x86_64-linux-gnu/libexpat.so.1.9.1 (0x00007f2e8c12f000)\n'
    '\tlibpalmissing.so.7 => not found\n'
    '\t/lib64/ld-linux-x86-64.so.2 (0x00007f2e8cdff000)\n'
    '\tlibz.so.1 => /lib/x86_64-linux-gnu/libz.so.1.3 (0x00007f2e8c3b1000)\n')


def test_interpreter_ld_trace_binds_sonames_in_default_search_dirs():
    """Pure unit test of the runtime-closure discovery mapping: no
    subprocess and no Linux requirement, so the soname-binding rule is
    verified on Windows development hosts too. Arrow-less lines
    (linux-vdso, the dynamic loader itself) and ``=> not found``
    resolutions are ignored; every real pair binds at
    ``dirname(hostpath)/soname`` so DT_NEEDED resolution succeeds inside
    the namespace where no ld.so cache exists; and a versioned-vs-soname
    duplicate for the same library collapses onto one guest path."""
    assert parse_ld_trace_pairs(SYNTHETIC_INTERPRETER_LD_TRACE) == [
        ('libc.so.6', '/lib/x86_64-linux-gnu/libc.so.6'),
        ('libz.so.1', '/lib/x86_64-linux-gnu/libz.so.1'),
        ('libexpat.so.1', '/usr/lib/x86_64-linux-gnu/libexpat.so.1.9.1'),
        ('libz.so.1', '/lib/x86_64-linux-gnu/libz.so.1.3')]
    assert soname_default_search_bindings(SYNTHETIC_INTERPRETER_LD_TRACE) == {
        '/lib/x86_64-linux-gnu/libc.so.6': '/lib/x86_64-linux-gnu/libc.so.6',
        '/lib/x86_64-linux-gnu/libz.so.1': '/lib/x86_64-linux-gnu/libz.so.1',
        '/usr/lib/x86_64-linux-gnu/libexpat.so.1': '/usr/lib/x86_64-linux-gnu/libexpat.so.1.9.1'}


@pytest.mark.skipif(not CONTAINMENT_ENABLED, reason='explicit native containment proof is opt-in')
def test_native_contained_two_phase_round_trip(tmp_path):
    """Real bwrap/cgroup/memfd lifecycle with the compiled stand-in ELF.

    The version phase and the model phase execute from the same sealed
    snapshots under offline egress; the synthetic envelope returns through
    the relay and the phase counters advance.
    """
    binary, digest, _size, launch = build_native_launch(tmp_path)
    work = tmp_path / 'work'
    work.mkdir()
    before = linux.LINUX_PHASE_COUNTERS.snapshot()
    result = run_research_process(spec=contained_vendor_spec(binary, digest, work),
                                  stdin=b'{"schema_version":"probe"}',
                                  allow_process_start=True, linux_launch=launch)
    after = linux.LINUX_PHASE_COUNTERS.snapshot()
    assert b'"type":"result"' in result.stdout
    assert b'"subtype":"success"' in result.stdout
    assert result.stderr_bytes == 0
    assert after['version_executions'] == before['version_executions'] + 1
    assert after['model_executions'] == before['model_executions'] + 1
    assert not list(work.iterdir()), 'no writable host surface may be used'


@pytest.mark.skipif(not CONTAINMENT_ENABLED, reason='explicit native containment proof is opt-in')
def test_native_in_flight_replacement_of_vendor_and_dependency_is_ignored(tmp_path):
    """Replacing the host vendor image and a runtime dependency file during
    the version phase cannot alter the executed bytes: the model namespace
    binds the same sealed snapshots, never the mutated pathnames."""
    import threading
    binary, digest, _size, launch = build_native_launch(tmp_path)
    dep_host = next(item.host_path for item in launch.runtime_files
                    if item.guest_path.endswith('libpaldep.so'))
    work = tmp_path / 'work'
    work.mkdir()
    swapped = threading.Event()

    def swap_when_admitted():
        seen = linux.LINUX_PHASE_COUNTERS.snapshot()['version_executions']
        while linux.LINUX_PHASE_COUNTERS.snapshot()['version_executions'] == seen:
            time.sleep(0.002)
        time.sleep(0.03)
        Path(binary).write_bytes(b'\x7fELFswapped-not-the-vendor')
        Path(dep_host).write_bytes(b'not an elf object')
        swapped.set()

    thread = threading.Thread(target=swap_when_admitted, daemon=True)
    thread.start()
    result = run_research_process(
        spec=contained_vendor_spec(binary, digest, work, argv=('--pal-dep',)),
        stdin=b'{}', allow_process_start=True, linux_launch=launch)
    assert swapped.is_set()
    assert b'DEP=42' in result.stdout
    assert b'"type":"result"' in result.stdout
    assert result.stderr_bytes == 0


@pytest.mark.skipif(not CONTAINMENT_ENABLED, reason='explicit native containment proof is opt-in')
def test_native_pre_admission_tampering_is_refused(tmp_path):
    binary, digest, _size, launch = build_native_launch(tmp_path)
    Path(binary).write_bytes(b'\x7fELF' + b'tampered-after-pinning' * 4)
    work = tmp_path / 'work'
    work.mkdir()
    with pytest.raises(ResearchProcessError, match='research_process_failed'):
        run_research_process(spec=contained_vendor_spec(binary, digest, work),
                             stdin=b'{}', allow_process_start=True, linux_launch=launch)


@pytest.mark.skipif(not CONTAINMENT_ENABLED, reason='explicit native containment proof is opt-in')
def test_native_unsupported_wrapper_option_fails_closed(tmp_path):
    """A containment executable that does not support the required wrapper
    options is classified and refused, never silently retried."""
    stub = tmp_path / 'stub'
    stub.mkdir()
    stub_path, stub_digest, stub_size = _compile(stub, 'fake-bwrap', FAKE_WRAPPER_C_SOURCE)
    _binary, _digest, _size, launch = build_native_launch(tmp_path)
    launch = relaunch(launch, wrapper=linux.LinuxArtifactPin(
        stub_path, stub_digest, stub_size))
    work = tmp_path / 'work'
    work.mkdir()
    with pytest.raises(ResearchProcessError, match='wrapper_unsupported'):
        run_research_process(spec=contained_vendor_spec(_binary, _digest, work),
                             stdin=b'{}', allow_process_start=True, linux_launch=launch)


def test_model_phase_error_maps_input_incomplete():
    """A relay that loses accepted prompt bytes fails with the public
    input-incomplete code, never a silent success."""
    mapped = linux._model_phase_error('model_input_incomplete')
    assert type(mapped) is ResearchProcessError
    assert mapped.args[0] == 'research_process_input_incomplete'
    assert 'research_process_input_incomplete' in linux._ADMITTED_ERRORS


def test_helper_source_conserves_prompt_bytes():
    """Static regression for the EOF-plus-writable schedule: on parent EOF the
    helper must close only the prompt channel, keep forwarding buffered bytes,
    close guest stdin only when fully drained, treat EPIPE with unsent bytes
    as input-incomplete (output-pipe failures as relay failures), and never
    report success with undelivered buffers."""
    source = linux.HELPER_SOURCE
    assert 'not prompt_open and not prompt_buf' in source
    # Input conservation on the prompt relay; output pipes report relay
    # failures instead of masquerading as lost prompts.
    assert ("failure = failure or ('model_input_incomplete'"
            in source and "'model_relay_failed')" in source)
    assert linux._model_phase_error(
        'model_relay_failed').args[0] == 'research_process_relay_failed'
    assert 'research_process_relay_failed' in linux._ADMITTED_ERRORS
    # The old premature-close branch must be gone.
    assert 'Prompt EOF is the vendor' not in source
    assert ('if failure is None and (out_buf or err_buf or prompt_buf):'
            in source)
    # The flush loop includes the prompt buffer.
    assert "(staged['stdin_w'], prompt_buf))" in source


# ---------------------------------------------------------------------------
# L9 W3: parent-side relay wiring (plan l9-w3-plan sections 4/7 as amended
# by the R-W7 gate addendum section 13). Offline tests run on every host;
# the two native parent-path proofs stay opt-in-gated and skip cleanly.
# ---------------------------------------------------------------------------


def relay_trust_declaration(ca_path='/opt/pal-synthetic/relay-ca.pem',
                            ca_sha256=None, ca_size=4096):
    """Declaration-only reviewed trust record (inert; no file is read)."""
    return relay.RelayTrustConfig(
        ca_bundle=relay.RelayCaBundlePin(ca_path, ca_sha256 or 'c' * 64,
                                         ca_size))


def synthetic_relay_launch(tmp_path=None, **changes):
    """Declaration-only relay launch: the relay helper digest, the closed
    relay egress value and the typed trust record (no files, no I/O)."""
    changes.setdefault('relay_trust', relay_trust_declaration())
    return synthetic_launch(tmp_path, egress_policy=linux.EGRESS_RELAY,
                            helper_sha256=_RELAY_HELPER_DIGEST, **changes)


def relay_vendor_spec(tmp_path, base_url=None):
    """Relay-branch spec environment: the SYMBOLIC endpoint declaration and
    the fixed synthetic credential sentinel."""
    return ResearchProcessSpec(
        (str(tmp_path / 'claude-relay'), '--print', '--bare'),
        str(tmp_path / 'work'),
        (('HOME', str(tmp_path / 'home')),
         ('CLAUDE_CONFIG_DIR', str(tmp_path / 'config')),
         ('ANTHROPIC_BASE_URL',
          linux.RELAY_SYMBOLIC_ENDPOINT if base_url is None else base_url),
         ('CLAUDE_CODE_MAX_OUTPUT_TOKENS', '8192'),
         ('ANTHROPIC_API_KEY', 'SYNTHETIC-NOT-A-REAL-KEY')), 'd' * 64, 15000)


def test_relay_symbolic_endpoint_substitution_is_exact_and_fail_closed(tmp_path):
    """Plan 7.1.1: the symbolic declaration is substituted with the exact
    drawn numeric loopback endpoint; every other pair is byte-identical and
    order-preserved; the caller-owned input is never mutated; the exact
    window bounds pass and every neighbor refuses closed."""
    spec = relay_vendor_spec(tmp_path)
    pairs, credential = linux._guest_configuration(spec, synthetic_launch(tmp_path))
    assert credential == 'SYNTHETIC-NOT-A-REAL-KEY'
    assert dict(pairs)['ANTHROPIC_BASE_URL'] == linux.RELAY_SYMBOLIC_ENDPOINT
    assert 'ANTHROPIC_API_KEY' not in dict(pairs)
    substituted = linux._substitute_relay_endpoint(pairs, 21007)
    expected = dict(pairs)
    expected['ANTHROPIC_BASE_URL'] = 'http://127.0.0.1:21007'
    assert dict(substituted) == expected
    assert tuple(dict(substituted)) == tuple(dict(pairs))
    assert 'ANTHROPIC_API_KEY' not in json.dumps(
        [list(pair) for pair in substituted])
    # the input tuple is never mutated
    assert dict(pairs)['ANTHROPIC_BASE_URL'] == linux.RELAY_SYMBOLIC_ENDPOINT
    low, high = relay.RELAY_PORT_WINDOW
    for port in (low, high):
        assert dict(linux._substitute_relay_endpoint(pairs, port))[
            'ANTHROPIC_BASE_URL'] == 'http://127.0.0.1:%d' % port
    for port in (low - 1, high + 1, 0, -1, 65536):
        with pytest.raises(ResearchProcessError,
                           match='research_process_launch_env_forbidden'):
            linux._substitute_relay_endpoint(pairs, port)


@pytest.mark.parametrize('base_url', [
    'https://api.anthropic.com', 'http://127.0.0.1:1',
    'http://127.0.0.0:0', 'http://localhost:0', 'http://127.0.0.1',
    'http://127.0.0.1:0/', 'http://127.0.0.1:0/path', 'http://127.0.0.1:0?q=1',
    'http://127.0.0.1:0#frag', 'http://user@127.0.0.1:0',
    'http://user:pw@127.0.0.1:0', 'https://127.0.0.1:0', 'http://[::1]:0',
    'http://127.0.0.1:00', '',
])
def test_relay_endpoint_substitution_refuses_every_deviation(tmp_path, base_url):
    """Plan 7.1.1: every deviation from the exact symbolic literal — wrong
    scheme, authority, port, path, query, fragment or userinfo spelling —
    refuses closed with the existing admitted code."""
    spec = relay_vendor_spec(tmp_path, base_url)
    pairs, _credential = linux._guest_configuration(spec, synthetic_launch(tmp_path))
    with pytest.raises(ResearchProcessError,
                       match='research_process_launch_env_forbidden'):
        linux._substitute_relay_endpoint(pairs, 21007)


def test_relay_endpoint_substitution_requires_the_endpoint_pair(tmp_path):
    """Plan 7.1.1: a relay call without the endpoint declaration is a
    deviation and refuses closed (never a silent passthrough)."""
    base = relay_vendor_spec(tmp_path)
    environment = tuple(pair for pair in base.environment
                        if pair[0] != 'ANTHROPIC_BASE_URL')
    spec = ResearchProcessSpec(base.argv, base.cwd, environment,
                               base.executable_sha256, base.timeout_ms)
    pairs, _credential = linux._guest_configuration(spec, synthetic_launch(tmp_path))
    with pytest.raises(ResearchProcessError,
                       match='research_process_launch_env_forbidden'):
        linux._substitute_relay_endpoint(pairs, 21007)


def test_relay_symbolic_endpoint_constant_matches_profile_declaration():
    """Plan 7.1.2: the process-layer constant is byte-equal to the profile
    layer's declaration (test-side import only) and the layering pin holds:
    the process layer never imports the profile module."""
    from polymarket_alpha_lab import research_claude_profile as profiles
    assert linux.RELAY_SYMBOLIC_ENDPOINT == profiles.RELAY_ENDPOINT_DECLARATION
    assert linux.RELAY_SYMBOLIC_ENDPOINT == 'http://127.0.0.1:0'
    source = Path(linux.__file__).read_text(encoding='utf-8')
    assert 'research_claude_profile' not in source


@pytest.mark.skipif(linux.fcntl is None or not hasattr(socket, 'AF_UNIX'),
                    reason='the relay channel builder (AF_UNIX socketpair '
                           'plus the F_DUPFD idiom) is a Linux runtime '
                           'surface; fcntl is absent on Windows hosts')
def test_relay_channel_builder_moves_both_ends_above_100(monkeypatch):
    """Plan 7.1.3: both channel halves land at descriptors >= 100 (never
    the canonical 3..6 supervisor slots), are distinct live descriptors,
    and closing them leaves the descriptor set exactly as before (the
    originals are closed; no fd leaks). The builder constructs an AF_UNIX
    socketpair (module-level socketpair wraps descriptors through the
    socket factory), so the no-network tripwire is narrowly lifted for
    this test exactly like the engine-executing tests do."""
    monkeypatch.setattr('socket.socket', _REAL_SOCKET)

    def open_fd_set():
        directory = os.open('/proc/self/fd', os.O_RDONLY | os.O_DIRECTORY)
        try:
            return frozenset(int(name) for name in os.listdir(directory)) - {directory}
        finally:
            os.close(directory)
    before = open_fd_set()
    engine_fd, supervisor_fd = linux._create_relay_channel()
    try:
        assert engine_fd >= 100 and supervisor_fd >= 100
        assert engine_fd != supervisor_fd
        os.fstat(engine_fd)
        os.fstat(supervisor_fd)
        assert open_fd_set() - before == {engine_fd, supervisor_fd}
    finally:
        os.close(engine_fd)
        os.close(supervisor_fd)
    assert open_fd_set() == before


def _wire_session_constructor(monkeypatch, *, relay, base_url=None,
                               draw_port=21007, channel=(107, 108)):
    """Offline constructor wiring harness: the admission and the spawn are
    fakes (the real ones are Linux-only), while the production constructor
    logic under test — substitution, the ONE draw, port retention, the CONF
    keys, pass_fds, env_pairs — runs for real. Records every spawn and the
    CONF document the constructor hands the supervisor."""
    record = {'conf': None, 'popen': [], 'draws': 0, 'channel': channel}

    class FakeAdmission:
        closed = False
        vendor_fd, wrapper_fd, helper_fd, interp_fd = 201, 202, 203, 204
        engine_fd = None
        ca_fd = None
        runtime_fds = [205]

        def __init__(self, spec, launch):
            pass

        def protect(self):
            pass

        def close(self):
            self.closed = True

    class FakePopen:
        def __init__(self, argv, **kwargs):
            record['popen'].append((list(argv), kwargs))

        def poll(self):
            return None

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(linux, '_Admission', FakeAdmission)
    monkeypatch.setattr(linux, 'subprocess', types.SimpleNamespace(
        Popen=FakePopen, DEVNULL=subprocess.DEVNULL,
        TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(linux, '_create_relay_channel', lambda: channel)

    def fake_draw():
        record['draws'] += 1
        return draw_port

    monkeypatch.setattr(linux, 'draw_relay_port', fake_draw)
    monkeypatch.setattr(linux._ContainedSession, '_send_conf',
                        lambda self, configuration: record.__setitem__(
                            'conf', configuration))
    monkeypatch.setattr(linux._ContainedSession, '_await_first_event',
                        lambda self: {'type': 'started'})
    root = Path('C:/pal-w3-wiring') if os.name == 'nt' else Path('/opt/pal-w3-wiring')
    if relay:
        launch = synthetic_relay_launch()
        spec = relay_vendor_spec(root, base_url)
    else:
        launch = synthetic_launch()
        spec = vendor_spec(root)
    return record, launch, spec


def test_relay_session_conf_gains_exactly_the_two_amendment_keys(monkeypatch):
    """Plan 7.1.4: the relay constructor substitutes the guest endpoint
    BEFORE env_pairs are set, performs exactly ONE production draw, retains
    the drawn port and the >=100 parent channel end on the session, and
    injects exactly the two amendment-named CONF keys."""
    record, launch, spec = _wire_session_constructor(monkeypatch, relay=True)
    session = linux._ContainedSession(spec, launch, None,
                                      time.monotonic_ns() + 30000000000)
    try:
        assert record['draws'] == 1
        conf = record['conf']
        assert conf['relay_channel_fd'] == record['channel'][1] >= 100
        assert conf['relay_port'] == 21007
        assert relay.RELAY_PORT_WINDOW[0] <= conf['relay_port'] <= relay.RELAY_PORT_WINDOW[1]
        assert dict(map(tuple, conf['env_pairs']))[
            'ANTHROPIC_BASE_URL'] == 'http://127.0.0.1:21007'
        assert linux.RELAY_SYMBOLIC_ENDPOINT not in json.dumps(conf['env_pairs'])
        assert 'SYNTHETIC-NOT-A-REAL-KEY' not in json.dumps(conf)
        assert session._relay_port == 21007
        assert session._relay_channel == record['channel'][0] >= 100
        # M-1 normal path: a successful spawn closed and relinquished the
        # child-side pipe tracking (the relay child fd ownership idiom).
        assert session._child_side_fds is None
        assert session._relay_child_fd is None
        assert len(record['popen']) == 1
        _argv, kwargs = record['popen'][0]
        assert record['channel'][1] in kwargs['pass_fds']
        assert record['channel'][0] not in kwargs['pass_fds']
    finally:
        session._cleanup(50)
    assert record['conf'] is not None


def test_offline_session_conf_has_neither_relay_key(monkeypatch):
    """Plan 7.1.4 (offline half): the offline constructor performs no draw,
    allocates no channel, keeps the declared (unresolved) guest endpoint
    pair, and its CONF is structurally identical to the pre-branch builder
    output — absence of the keys, never None-valued placeholders."""
    record, launch, spec = _wire_session_constructor(monkeypatch, relay=False)
    session = linux._ContainedSession(spec, launch, None,
                                      time.monotonic_ns() + 30000000000)
    try:
        conf = record['conf']
        assert 'relay_channel_fd' not in conf and 'relay_port' not in conf
        assert record['draws'] == 0
        assert session._relay_port is None and session._relay_channel is None
        # M-1 normal path (offline half): the child-side tracking tuple is
        # empty after the successful Popen inheritance -- offline behavior
        # identical to the pre-fix constructor.
        assert session._child_side_fds is None
        assert session._relay_child_fd is None
        assert dict(map(tuple, conf['env_pairs']))[
            'ANTHROPIC_BASE_URL'] == 'https://gateway.example.invalid'
        baseline = linux._supervisor_configuration(
            spec, launch, wrapper_fd=conf['wrapper_fd'],
            helper_fd=conf['helper_fd'], interp_fd=conf['interp_fd'],
            vendor_fd=conf['vendor_fd'], runtime_fds=(conf['runtime'][0]['fd'],),
            allocation=conf['alloc_name'], report_fd=conf['report_fd'],
            liveness_fd=conf['liveness_fd'], prompt_fd=conf['prompt_fd'],
            credential_fd=conf['credential_fd'])
        baseline['env_pairs'] = conf['env_pairs']
        baseline['passthrough_env_keys'] = conf['passthrough_env_keys']
        assert baseline == conf
    finally:
        session._cleanup(50)


def test_supervisor_spawn_failure_closes_every_child_side_pipe_end(
        monkeypatch, tmp_path):
    """M-1 portable half (every host): with the supervisor spawn exploding,
    every one of the seven child-bound pipe ends handed to the spawn -- the
    three stdio ends plus the four pass_fds channel ends -- is CLOSED by the
    constructor's cleanup (each probe close fails: the descriptor is
    already gone), and the ORIGINAL spawn error still propagates. The
    Linux-gated sibling below adds the rigorous fd-set equality proof."""
    record, launch, spec = _wire_session_constructor(monkeypatch, relay=False)
    handed = {}

    class ExplodingPopen:
        def __init__(self, argv, **kwargs):
            record['popen'].append((list(argv), kwargs))
            handed['ctrl_r'] = kwargs['stdin']
            handed['vout_w'] = kwargs['stdout']
            handed['verr_w'] = kwargs['stderr']
            (handed['rep_w'], handed['live_r'],
             handed['prom_r'], handed['cred_r']) = kwargs['pass_fds'][:4]
            raise OSError('synthetic supervisor spawn failure')

    monkeypatch.setattr(linux.subprocess, 'Popen', ExplodingPopen)
    with pytest.raises(OSError, match='synthetic supervisor spawn failure'):
        linux._ContainedSession(spec, launch, None,
                                time.monotonic_ns() + 30000000000)
    assert len(record['popen']) == 1
    assert len(handed) == 7
    for name, fd in sorted(handed.items()):
        with pytest.raises(OSError):
            os.close(fd)


@pytest.mark.skipif(sys.platform != 'linux',
                    reason='the /proc/self/fd leak proof is a Linux runtime '
                           'surface (the existing open_fd_set idiom)')
def test_supervisor_spawn_failure_leaks_no_child_side_pipe_end(
        monkeypatch, tmp_path):
    """M-1 regression (section 68 post-L9 audit, R-N4-corrected count): a
    supervisor Popen failure must not leak the seven LOCAL child-bound pipe
    ends (of the fourteen descriptors the constructor creates). The REAL
    constructor and the REAL cleanup run against an exploding Popen; the
    descriptor set is compared before/after with the open_fd_set idiom, the
    ORIGINAL spawn error still propagates (cleanup never masks it), and
    through the run driver the same failure surfaces as the fixed generic
    code research_process_failed -- an unadmitted spawn OSError never
    invents a new vocabulary member."""
    record, launch, spec = _wire_session_constructor(monkeypatch, relay=False)

    class ExplodingPopen:
        def __init__(self, argv, **kwargs):
            record['popen'].append((list(argv), kwargs))
            raise OSError('synthetic supervisor spawn failure')

    monkeypatch.setattr(linux.subprocess, 'Popen', ExplodingPopen)

    def open_fd_set():
        directory = os.open('/proc/self/fd', os.O_RDONLY | os.O_DIRECTORY)
        try:
            return frozenset(int(name) for name in os.listdir(directory)) \
                - {directory}
        finally:
            os.close(directory)

    before = open_fd_set()
    with pytest.raises(OSError, match='synthetic supervisor spawn failure'):
        linux._ContainedSession(spec, launch, None,
                                time.monotonic_ns() + 30000000000)
    assert len(record['popen']) == 1
    assert open_fd_set() == before
    monkeypatch.setattr(linux, 'sys', types.SimpleNamespace(platform='linux'))
    monkeypatch.setattr(linux, 'signal', types.SimpleNamespace(
        SIG_DFL=signal.SIG_DFL, SIGCHLD='placeholder',
        getsignal=lambda _: signal.SIG_DFL))
    with pytest.raises(ResearchProcessError, match='research_process_failed'):
        linux.run_linux_contained_process(spec=spec, stdin=b'', launch=launch,
                                          stop=None)
    assert len(record['popen']) == 2
    assert open_fd_set() == before


def _exec_engine_namespace():
    namespace = {}
    exec(compile(relay.ENGINE_SOURCE, '<engine-source>', 'exec'), namespace)
    return namespace


def test_engine_conf_builder_shape_and_no_credential():
    """Plan 7.1.5: the engine CONF document has exactly the keys the W1
    engine main() validator accepts — the channel named twice, four
    pairwise-distinct channel fds disjoint from it, the concrete total
    budget, the numeric bound authority — is hex-line encodable, carries no
    credential anywhere, and its trust document passes the real engine's
    _validate_trust."""
    policy = relay_trust_declaration().policy_dict()
    conf = linux._engine_configuration(
        policy, channel_fd=120, control_fd=121, report_fd=122,
        liveness_fd=123, ca_fd=124, total_timeout_ms=15000, port=21007)
    assert set(conf) == {'trust', 'relay_read_fd', 'relay_write_fd',
                         'control_fd', 'report_fd', 'liveness_fd', 'ca_fd',
                         'total_timeout_ms', 'bound_authority'}
    assert conf['relay_read_fd'] == conf['relay_write_fd'] == 120
    assert (conf['control_fd'], conf['report_fd'], conf['liveness_fd'],
            conf['ca_fd']) == (121, 122, 123, 124)
    assert conf['total_timeout_ms'] == 15000
    assert conf['bound_authority'] == '127.0.0.1:21007'
    serialized = json.dumps(conf, separators=(',', ':'))
    line = b'CONF ' + serialized.encode('utf-8')
    assert line.hex().encode('ascii') + b'\n'
    # No credential surface: no credential VALUE, no credential key. (The
    # trust policy legitimately pins the forwarded header NAME x-api-key;
    # the credential value itself must never appear anywhere.)
    for forbidden in ('SYNTHETIC', 'ANTHROPIC_API_KEY', 'pal-call'):
        assert forbidden not in serialized
    code, config = _exec_engine_namespace()['_validate_trust'](policy)
    assert code is None and config is not None
    port = int(conf['bound_authority'].split(':', 1)[1])
    assert relay.RELAY_PORT_WINDOW[0] <= port <= relay.RELAY_PORT_WINDOW[1]

    def build(**kwargs):
        values = dict(channel_fd=120, control_fd=121, report_fd=122,
                      liveness_fd=123, ca_fd=124, total_timeout_ms=15000,
                      port=21007)
        values.update(kwargs)
        return linux._engine_configuration(policy, **values)

    for total in (1, 86400000):
        assert build(total_timeout_ms=total)['total_timeout_ms'] == total
    for total in (0, -1, 86400001, None, '15000', 15000.0):
        with pytest.raises(ValueError, match='research_process_launch_invalid'):
            build(total_timeout_ms=total)
    for port in (relay.RELAY_PORT_WINDOW[0] - 1,
                 relay.RELAY_PORT_WINDOW[1] + 1, 0, 65536, '21007'):
        with pytest.raises(ValueError, match='research_process_launch_invalid'):
            build(port=port)
    for kwargs in ({'control_fd': 120}, {'report_fd': 120},
                   {'liveness_fd': 120}, {'ca_fd': 120},
                   {'control_fd': '121'}, {'channel_fd': '120'},
                   {'control_fd': 122, 'report_fd': 122},
                   {'liveness_fd': 123, 'ca_fd': 123}, {'channel_fd': -1}):
        with pytest.raises(ValueError, match='research_process_launch_invalid'):
            build(**kwargs)
    with pytest.raises(ValueError, match='research_process_launch_invalid'):
        linux._engine_configuration(
            None, channel_fd=120, control_fd=121, report_fd=122,
            liveness_fd=123, ca_fd=124, total_timeout_ms=15000, port=21007)


def _restore_real_process_surfaces(monkeypatch):
    """Narrowly restore the real Popen/socket surfaces for the
    engine-executing tests only (plan 7.1 preamble): the autouse tripwire
    keeps guarding every other test."""
    monkeypatch.setattr(subprocess, 'Popen', _REAL_POPEN)
    monkeypatch.setattr('socket.socket', _REAL_SOCKET)


def test_engine_real_source_lifecycle_offline(tmp_path, monkeypatch):
    """Plan 7.1.6: the REAL ENGINE_SOURCE under a real interpreter —
    malformed CONF lines exit 96/97/98/99 (never a hang); the started
    handshake, the STOP conclusion (engine_done relay_stopped, exit 0) and
    the parent-liveness EOF conclusion (relay_parent_lost, exit 0) all run
    through the W1 subprocess harness; the sealed engine bytes stay
    digest-pinned."""
    _restore_real_process_surfaces(monkeypatch)
    from tests.test_research_linux_relay import EngineRun, SYNTHETIC_CA, _trust
    assert sha256(relay.ENGINE_SOURCE.encode('utf-8')).hexdigest() \
        == relay._engine_digest() == linux.RELAY_ENGINE_DIGEST
    engine_py = tmp_path / 'engine.py'
    engine_py.write_text(relay.ENGINE_SOURCE, encoding='ascii')
    for line, expected in ((b'', 96), (b'zz\n', 97),
                           (b'hello world'.hex().encode('ascii') + b'\n', 98),
                           (b'CONF {'.hex().encode('ascii') + b'\n', 99),
                           (b'CONF {}'.hex().encode('ascii'), 96)):
        proc = subprocess.Popen([sys.executable, str(engine_py)],
                                stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        proc.communicate(line, timeout=120)
        assert proc.returncode == expected, (line, proc.returncode)
    trust = _trust().policy_dict()
    run = EngineRun(tmp_path / 'stop', trust, SYNTHETIC_CA['pem'])
    try:
        run.wait_started()
        run.stop()
        event = run.engine_done()
        assert event['code'] == 'relay_stopped', event
        assert run.wait_exit() == 0
    finally:
        run.close()
    run = EngineRun(tmp_path / 'lost', trust, SYNTHETIC_CA['pem'])
    try:
        run.wait_started()
        run.close_liveness()
        event = run.engine_done()
        assert event['code'] == 'relay_parent_lost', event
        assert run.wait_exit() == 0
    finally:
        run.close()


@pytest.mark.skipif(os.name != 'posix',
                    reason='the parent CONF fd numbers reach the engine '
                           'child unchanged only through POSIX pass_fds')
def test_engine_main_accepts_the_parent_conf_document(tmp_path, monkeypatch):
    """Plan 7.1.5/7.1.6 (POSIX full-main proof): the CONF document built by
    the production _engine_configuration passes the REAL engine main()
    validator end to end — CA verified from the CONF-named descriptor, the
    started report emitted, STOP honored, engine_done relay_stopped with
    exit 0."""
    _restore_real_process_surfaces(monkeypatch)
    from tests.test_research_linux_relay import SYNTHETIC_CA, _trust
    engine_py = tmp_path / 'engine.py'
    engine_py.write_text(relay.ENGINE_SOURCE, encoding='ascii')
    chan_r, _chan_w = os.pipe()
    ctrl_r, ctrl_w = os.pipe()
    rep_r, rep_w = os.pipe()
    live_r, live_w = os.pipe()
    ca_r, ca_w = os.pipe()
    conf = linux._engine_configuration(
        _trust().policy_dict(), channel_fd=chan_r, control_fd=ctrl_r,
        report_fd=rep_w, liveness_fd=live_r, ca_fd=ca_r,
        total_timeout_ms=15000, port=relay.draw_relay_port())
    os.write(ca_w, SYNTHETIC_CA['pem'])
    os.close(ca_w)

    def read_json_line(fd):
        data = bytearray()
        while not data.endswith(b'\n'):
            block = os.read(fd, 4096)
            if not block:
                raise AssertionError('engine report EOF; tail='
                                     + repr(bytes(data[-120:])))
            data.extend(block)
        return json.loads(bytes(data).decode('utf-8'))

    try:
        proc = subprocess.Popen(
            [sys.executable, str(engine_py)], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=[chan_r, ctrl_r, rep_w, live_r, ca_r])
        line = b'CONF ' + json.dumps(conf, separators=(',', ':')).encode('utf-8')
        proc.stdin.write(line.hex().encode('ascii') + b'\n')
        proc.stdin.close()
        started = read_json_line(rep_r)
        assert started['type'] == 'started', started
        assert started['protocol_version'] == relay.RELAY_PROTOCOL_VERSION
        os.write(ctrl_w, b'STOP\n')
        done = read_json_line(rep_r)
        assert done['type'] == 'engine_done', done
        assert done['code'] == 'relay_stopped', done
        assert all(type(value) is int for value in done['counters'].values())
        assert proc.wait(timeout=30) == 0
    finally:
        for fd in (chan_r, _chan_w, ctrl_r, ctrl_w, rep_r, rep_w, live_r,
                   live_w, ca_r):
            try:
                os.close(fd)
            except OSError:
                pass
        if proc.poll() is None:
            proc.kill()


def test_engine_ca_pin_mismatch_records_fixed_code(tmp_path, monkeypatch):
    """Plan 7.1.7 as amended by R-W7 MINOR-3: literal garbage through the
    channel refuses as relay_refused_framing; a VALID-framing approved
    request over the correctly-pinned but non-loadable (non-PEM) bundle
    reaches relay_ca_pin_mismatch — with zero DNS resolutions and zero
    upstream connections (the network-free proof for the native tests)."""
    _restore_real_process_surfaces(monkeypatch)
    from tests.test_research_linux_relay import (
        EngineRun, SYNTHETIC_CA, _trust, approved_head)
    trust = _trust().policy_dict()
    run = EngineRun(tmp_path / 'garbage', trust, SYNTHETIC_CA['pem'])
    try:
        run.wait_started()
        run.send_guest(b'PAL-W3 not-http garbage\r\n\r\n')
        event = run.call_done()
        assert event['code'] == 'relay_refused_framing', event
        assert event['counters']['dns_resolutions'] == 0
        assert event['counters']['upstream_connections'] == 0
        run.close_guest()
        event = run.engine_done()
        assert event['code'] == 'relay_refused_framing', event
        assert run.wait_exit() == 0
    finally:
        run.close()
    run = EngineRun(tmp_path / 'valid', trust, SYNTHETIC_CA['pem'],
                    bound_port=21222)
    try:
        run.wait_started()
        run.send_guest(approved_head('127.0.0.1:21222'))
        event = run.call_done()
        assert event['code'] == 'relay_ca_pin_mismatch', event
        counters = event['counters']
        assert counters['requests_observed'] == 1
        assert counters['dns_resolutions'] == 0
        assert counters['upstream_connections'] == 0
        run.close_guest()
        assert run.engine_done()['code'] == 'relay_ca_pin_mismatch'
        assert run.wait_exit() == 0
    finally:
        run.close()
    # a truncated (short) head then channel EOF is a cardinality failure,
    # never a hang
    run = EngineRun(tmp_path / 'short', trust, SYNTHETIC_CA['pem'])
    try:
        run.wait_started()
        run.send_guest(b'POST /v1/messages?beta=true HTTP/1.1\r\nHost: 127')
        run.close_guest()
        event = run.engine_done()
        assert event['code'] == 'relay_refused_cardinality', event
        assert run.wait_exit() == 0
    finally:
        run.close()


def test_relay_outcome_error_mapping_is_closed():
    """Plan 7.1.8: every RELAY_OUTCOMES member maps onto an EXISTING
    admitted ResearchProcessError code per the amended table (channel
    breaks take the already-admitted relay-failure code); the startup error
    codes and unknown values map onto the generic failure; nothing outside
    the admitted vocabulary is ever produced."""
    timeout_group = ('relay_total_timeout', 'relay_connect_timeout',
                     'relay_handshake_timeout', 'relay_idle_timeout')
    specific = {'relay_stopped': 'research_process_stopped',
                'relay_teardown_failed': 'research_process_cleanup_failed',
                'relay_channel_failed': 'research_process_relay_failed'}
    for code in relay.RELAY_OUTCOMES:
        error = linux._relay_outcome_error(code)
        assert type(error) is ResearchProcessError, code
        expected = specific.get(
            code, 'research_process_timeout' if code in timeout_group
            else 'research_process_failed')
        assert error.args[0] == expected, code
        assert error.args[0] in linux._ADMITTED_ERRORS
    for code in (*relay.RELAY_REPORT_CODES, 'relay_retry', None, 7, ''):
        assert linux._relay_outcome_error(code).args[0] \
            == 'research_process_failed', code


def test_supervisor_spec_value_error_is_admitted_and_stays_precise(
        monkeypatch, tmp_path):
    """M-5 (section 68 post-L9 audit): the _supervisor_configuration
    refusal code 'linux_launch_spec_invalid' is a member of
    _ADMITTED_VALUE_ERRORS, so through the run driver's normalization it
    surfaces as itself -- never masked into the generic
    research_process_failed. The refusal site is today pre-validated away
    (fd>=7 and the port window are checked before construction), so this
    pins the mapping for the day it becomes reachable."""
    assert 'linux_launch_spec_invalid' in linux._ADMITTED_VALUE_ERRORS
    launch = synthetic_relay_launch(tmp_path)
    spec = relay_vendor_spec(tmp_path)
    with pytest.raises(ValueError, match='linux_launch_spec_invalid'):
        linux._supervisor_configuration(
            spec, launch, wrapper_fd=201, helper_fd=203, interp_fd=204,
            vendor_fd=201, runtime_fds=[205], allocation='pal-call-x',
            report_fd=210, liveness_fd=211, prompt_fd=212, credential_fd=213,
            relay_channel_fd=3, relay_port=21007)  # fd 3 < 7: below the slots

    class RefusingSession:
        def __init__(self, spec, launch, stop, deadline_ns):
            raise ValueError('linux_launch_spec_invalid')

    monkeypatch.setattr(linux, '_ContainedSession', RefusingSession)
    monkeypatch.setattr(linux, 'sys', types.SimpleNamespace(platform='linux'))
    monkeypatch.setattr(linux, 'signal', types.SimpleNamespace(
        SIG_DFL=signal.SIG_DFL, SIGCHLD='placeholder',
        getsignal=lambda _: signal.SIG_DFL))
    with pytest.raises(ValueError, match='linux_launch_spec_invalid'):
        linux.run_linux_contained_process(spec=vendor_spec(tmp_path),
                                          stdin=b'',
                                          launch=synthetic_launch(), stop=None)


def test_relay_evidence_accessor_is_single_slot_fixed_code_int_counters():
    """Plan 7.1.9: the accessor returns the fixed record shape — a closed
    RELAY_OUTCOMES code (or None) plus integer counters only; the final
    report updates the counters without moving the code (it stays pinned to
    the last call_done record); the last completed call wins under two
    concurrent writers; the serialized record carries no credential,
    prompt, allocation or descriptor content."""
    holder = linux._LINUX_RELAY_EVIDENCE
    holder.record_call(None, {})
    assert linux.LINUX_RELAY_EVIDENCE() == {'code': None, 'counters': {}}
    holder.record_call('relay_ok', {
        'requests_observed': 1, 'requests_refused': 0, 'dns_resolutions': 0,
        'upstream_connections': 0, 'guest_bytes_in': 64,
        'guest_bytes_out': 0, 'teardown_drain_bytes': 0,
        'teardown_failures': 0, 'not-an-int': 'x', 7: 1})
    record = linux.LINUX_RELAY_EVIDENCE()
    assert record['code'] == 'relay_ok'
    relay.validate_relay_outcome(record['code'])
    assert all(type(key) is str and type(value) is int
               for key, value in record['counters'].items())
    assert 'not-an-int' not in record['counters'] and 7 not in record['counters']
    assert record['counters']['upstream_connections'] == 0
    holder.record_final({'teardown_drain_bytes': 4096, 'teardown_failures': 1,
                         'guest_bytes_out': 'not-an-int'})
    record = linux.LINUX_RELAY_EVIDENCE()
    assert record['code'] == 'relay_ok'
    # the counters are REPLACED by the final report's (integer-only) set
    assert record['counters'] == {'teardown_drain_bytes': 4096,
                                  'teardown_failures': 1}
    encoded = json.dumps(record)
    for forbidden in ('SYNTHETIC', 'ANTHROPIC', 'pal-call', 'stdin',
                      'messages_json'):
        assert forbidden not in encoded

    def worker():
        for index in range(200):
            holder.record_call('relay_ok' if index % 2 else 'relay_stopped',
                               {'requests_observed': index})

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    record = linux.LINUX_RELAY_EVIDENCE()
    assert record['code'] in ('relay_ok', 'relay_stopped')
    assert type(record['counters']['requests_observed']) is int
    # M-2 (section 68 post-L9 audit): begin_call empties the slot for the
    # NEXT admitted relay call -- code None with integer-only (empty)
    # counters, never the previous call's outcome or counters.
    holder.begin_call()
    assert linux.LINUX_RELAY_EVIDENCE() == {'code': None, 'counters': {}}
    holder.record_call(None, {})


def test_relay_evidence_slot_resets_for_each_admitted_call(tmp_path,
                                                           monkeypatch):
    """M-2 regression (section 68 post-L9 audit): the single-slot evidence
    never carries one call's outcome into the next. Call 1 concludes
    relay_ok with its counters; call 2 is admitted through the REAL
    _spawn_engine and its engine dies before its first report -- the
    evidence must show call 2's own empty state (code None, no counters),
    never call-1's relay_ok misattributed with call-2 accounting."""
    holder = linux._LINUX_RELAY_EVIDENCE
    holder.record_call('relay_ok', {'requests_observed': 1,
                                    'guest_bytes_in': 64,
                                    'guest_bytes_out': 64})
    holder.record_final({'requests_observed': 1, 'teardown_drain_bytes': 4096,
                         'teardown_failures': 0})
    assert linux.LINUX_RELAY_EVIDENCE()['code'] == 'relay_ok'
    spawns = []

    class SilentEngine:
        # The call-2 engine: constructs, then dies before its first report.
        def __init__(self, **kwargs):
            spawns.append(kwargs)

    monkeypatch.setattr(linux, '_RelayEngineProcess', SilentEngine)
    session = _relay_race_session(tmp_path, None)
    session._launch = synthetic_relay_launch(tmp_path)
    session._relay_channel = 107
    session._admission = types.SimpleNamespace(interp_fd=204, engine_fd=206,
                                               ca_fd=207)
    session._spawn_engine()
    assert len(spawns) == 1 and session._relay_channel is None
    assert linux.LINUX_RELAY_EVIDENCE() == {'code': None, 'counters': {}}
    holder.record_call(None, {})


# A stub engine script for the parent-side teardown races (plan 7.1.10): it
# speaks the exact engine report protocol over the production pipes and
# selects its pathology by scenario. Test-only code; never shipped.
STUB_ENGINE_SOURCE = '''\
import json
import os
import select
import sys
import time

scenario = sys.argv[1]
fds = {}
for spec in sys.argv[2:]:
    name, _, value = spec.partition('=')
    if os.name == 'nt':
        import msvcrt
        fds[name] = msvcrt.open_osfhandle(int(value, 16), os.O_BINARY | os.O_RDWR)
    else:
        fds[name] = int(value)
for name in ('control', 'liveness'):
    try:
        os.set_blocking(fds[name], False)
    except OSError:
        pass
sys.stdin.buffer.readline(1 << 20)  # consume the CONF line (never parsed)

def report(value):
    os.write(fds['report'],
             (json.dumps(value, separators=(',', ':')) + chr(10)).encode('ascii'))

ZEROS = {'requests_observed': 0, 'requests_refused': 0, 'dns_resolutions': 0,
         'upstream_connections': 0, 'guest_bytes_in': 0, 'guest_bytes_out': 0,
         'teardown_drain_bytes': 0, 'teardown_failures': 0}
COUNTERS = dict(ZEROS)

def call_done(code, **deltas):
    COUNTERS.update(deltas)
    report({'type': 'call_done', 'code': code, 'counters': dict(COUNTERS)})

report({'type': 'started', 'protocol_version': 'stub'})
if scenario == 'hang':
    while True:
        time.sleep(1)
elif scenario == 'die-no-report':
    sys.exit(7)
elif scenario == 'die-after-ok':
    call_done('relay_ok', requests_observed=1, guest_bytes_in=64)
    sys.exit(7)
elif scenario == 'refuse':
    call_done('relay_refused_target', requests_observed=1)
elif scenario == 'ok':
    call_done('relay_ok', requests_observed=1, guest_bytes_in=64,
              guest_bytes_out=64)
_SELECT_OK = True

def await_end():
    global _SELECT_OK
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        watched = [fds['control'], fds['liveness']]
        ready = []
        if _SELECT_OK:
            try:
                ready = select.select(watched, [], [], 0.2)[0]
            except (OSError, ValueError):
                _SELECT_OK = False
            except InterruptedError:
                continue
        if not _SELECT_OK:
            time.sleep(0.05)
            ready = watched
        for name in ('control', 'liveness'):
            fd = fds[name]
            if fd not in ready:
                continue
            try:
                block = os.read(fd, 4096)
            except OSError:
                continue
            if name == 'control':
                if block.strip() == b'STOP':
                    return 'relay_stopped'
            elif block == b'':
                return 'relay_parent_lost'
    return 'relay_total_timeout'

report({'type': 'engine_done', 'code': await_end(),
        'counters': dict(COUNTERS)})
sys.exit(0)
'''


class _StubEngine(linux._RelayEngineProcess):
    """The production engine-supervision machinery driven by a stub script:
    only the spawn differs (a host-compatible launcher; the production
    /proc/self/fd spawn is the Linux native path). The production __init__
    runs for real — pipes, CONF line, the parent channel close, report
    parsing, STOP, liveness EOF and the bounded force ladder."""

    def __init__(self, tmp_path, scenario):
        self._scenario = scenario
        self._stub_root = tmp_path / ('stub-' + scenario)
        self._stub_root.mkdir(parents=True, exist_ok=True)
        self._stub_path = self._stub_root / 'stub.py'
        self._stub_path.write_text(STUB_ENGINE_SOURCE, encoding='ascii')
        self._channel_r, self._channel_w = os.pipe()
        super().__init__(
            interp_fd=0, engine_fd=0, ca_fd=0, channel_fd=self._channel_r,
            trust_policy=relay_trust_declaration().policy_dict(),
            total_timeout_ms=15000, port=21007,
            python_home=sys.base_prefix, cwd=str(self._stub_root))

    def _spawn(self, argv, *, stdin, pass_fds, env, cwd):
        # pass_fds order is the production construction: channel, control,
        # report, liveness, then the (placeholder zero) artifact pins.
        assert len(pass_fds) == 7 and pass_fds[4] == pass_fds[5] == pass_fds[6] == 0
        named = {'channel': pass_fds[0], 'control': pass_fds[1],
                 'report': pass_fds[2], 'liveness': pass_fds[3]}
        stub_argv = [sys.executable, str(self._stub_path), self._scenario]
        if os.name == 'nt':
            import msvcrt
            for fd in named.values():
                os.set_inheritable(fd, True)
            stub_argv += ['%s=%s' % (name, hex(msvcrt.get_osfhandle(fd)))
                          for name, fd in named.items()]
            return _REAL_POPEN(stub_argv, stdin=stdin,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               env=dict(os.environ), cwd=cwd, shell=False,
                               close_fds=False)
        stub_argv += ['%s=%d' % (name, fd) for name, fd in named.items()]
        return _REAL_POPEN(stub_argv, stdin=stdin, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           pass_fds=list(named.values()), env=dict(os.environ),
                           cwd=cwd, shell=False, close_fds=True,
                           start_new_session=True)

    def discard(self):
        for fd in (self._channel_w,):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


class _RaceAdmission:
    closed = False

    def close(self):
        self.closed = True


def _relay_race_session(tmp_path, engine):
    """A _RelayContainedSession wired for the offline race tests: the real
    engine supervision methods against a real stub process, with the
    Linux-only supervisor/admission seat reduced to inert fakes."""
    session = linux._RelayContainedSession.__new__(
        linux._RelayContainedSession)
    session._spec = vendor_spec(tmp_path)
    session._stop = None
    session._deadline = time.monotonic_ns() + 30000000000
    session._prompt_w = session._credential_w = session._control_w = None
    session._stdout_r = session._stderr_r = None
    session._report_r = session._liveness_w = None
    session._relay_channel = None
    session._relay_child_fd = None
    session._relay_port = 21007
    session._supervisor = None
    session._admission = _RaceAdmission()
    session._launch = None
    session._engine = engine
    session._last_call_done_code = None
    session._engine_done_code = None
    return session


def _await_stub_started(engine, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        engine.collect_reports()
        if engine.started_seen:
            return
        if engine.poll() is not None:
            raise AssertionError('stub engine died before started: %r'
                                 % engine.poll())
        time.sleep(0.01)
    raise AssertionError('stub engine never reported started')


def test_synthetic_engine_teardown_race_hang_is_killed_and_suppresses(
        tmp_path, monkeypatch):
    """Plan 7.1.10(a): a stub engine that hangs ignoring STOP is killed
    inside the bounded cleanup budget; the teardown failure is recorded
    (research_process_cleanup_failed, which the driver discipline uses to
    suppress an otherwise successful result) and the phase counter bumps."""
    _restore_real_process_surfaces(monkeypatch)
    before = linux.LINUX_PHASE_COUNTERS.snapshot()['teardown_failures']
    engine = _StubEngine(tmp_path, 'hang')
    try:
        _await_stub_started(engine)
        session = _relay_race_session(tmp_path, engine)
        started = time.monotonic()
        failure = session._teardown_engine(600)
        elapsed = time.monotonic() - started
        assert failure is not None
        assert failure.args[0] == 'research_process_cleanup_failed'
        assert engine.poll() is not None
        assert elapsed < 10
        assert engine.conclusion() == {'exit_code': engine.poll(),
                                       'forced': True, 'engine_done': False}
        assert linux.LINUX_PHASE_COUNTERS.snapshot()['teardown_failures'] \
            == before + 1
    finally:
        engine.discard()
    engine = _StubEngine(tmp_path / 'second', 'hang')
    try:
        _await_stub_started(engine)
        session = _relay_race_session(tmp_path, engine)
        failure = session._cleanup(600)
        assert failure is not None
        assert failure.args[0] == 'research_process_cleanup_failed'
        assert session._engine is None
    finally:
        engine.discard()


def test_relay_driver_cleanup_failure_suppresses_a_successful_result(
        monkeypatch, tmp_path):
    """Plan 7.1.10(a) through the run driver: a relay session whose cleanup
    fails suppresses the successful model output — and the dispatch check
    sends relay-bearing launches to the relay session class."""
    record = []

    class FakeRelaySession:
        def __init__(self, spec, launch, stop, deadline):
            record.append('construct')

        def _stopped(self):
            return False

        def run_version_phase(self):
            record.append('version')

        def run_model_phase(self, stdin):
            record.append('model')
            return (b'relay-output', 3)

        def _cleanup(self, timeout_ms):
            record.append('cleanup')
            return ResearchProcessError('research_process_cleanup_failed')

    monkeypatch.setattr(linux, '_RelayContainedSession', FakeRelaySession)
    monkeypatch.setattr(linux, 'sys', types.SimpleNamespace(platform='linux'))
    monkeypatch.setattr(linux, 'signal', types.SimpleNamespace(
        SIG_DFL=signal.SIG_DFL, SIGCHLD='placeholder',
        getsignal=lambda _: signal.SIG_DFL))
    with pytest.raises(ResearchProcessError,
                       match='research_process_cleanup_failed'):
        linux.run_linux_contained_process(
            spec=relay_vendor_spec(tmp_path), stdin=b'{}',
            launch=synthetic_relay_launch(tmp_path), stop=None)
    assert record == ['construct', 'version', 'model', 'cleanup']


def test_synthetic_engine_death_without_report_fails_closed(tmp_path, monkeypatch):
    """Plan 7.1.10(b): a stub engine that exits nonzero with no final
    report fails the call research_process_failed with the evidence code
    None (never guessed); the already-dead engine is only reaped."""
    _restore_real_process_surfaces(monkeypatch)
    linux._LINUX_RELAY_EVIDENCE.record_call(None, {})
    engine = _StubEngine(tmp_path, 'die-no-report')
    try:
        _await_stub_started(engine)
        deadline = time.monotonic() + 20
        while engine.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert engine.poll() == 7
        session = _relay_race_session(tmp_path, engine)
        with pytest.raises(ResearchProcessError, match='research_process_failed'):
            session._pump_engine_events()
        evidence = linux.LINUX_RELAY_EVIDENCE()
        assert evidence['code'] is None
        assert session._teardown_engine(500) is None
    finally:
        engine.discard()


def test_synthetic_engine_death_after_ok_keeps_the_last_record(
        tmp_path, monkeypatch):
    """Plan 7.1.10(c): a stub engine that reports call_done relay_ok and
    then dies before engine_done fails the call (research_process_failed)
    while the evidence keeps the last recorded call state."""
    _restore_real_process_surfaces(monkeypatch)
    linux._LINUX_RELAY_EVIDENCE.record_call(None, {})
    engine = _StubEngine(tmp_path, 'die-after-ok')
    try:
        _await_stub_started(engine)
        deadline = time.monotonic() + 20
        while engine.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert engine.poll() == 7
        session = _relay_race_session(tmp_path, engine)
        with pytest.raises(ResearchProcessError, match='research_process_failed'):
            session._pump_engine_events()
        evidence = linux.LINUX_RELAY_EVIDENCE()
        assert evidence['code'] == 'relay_ok'
        assert evidence['counters']['requests_observed'] == 1
        assert evidence['counters']['upstream_connections'] == 0
        assert session._teardown_engine(500) is None
    finally:
        engine.discard()


def test_synthetic_engine_refusal_closes_the_call_with_no_resend(
        tmp_path, monkeypatch):
    """Plan 7.1.10(d): a call_done refusal mid-model fails the call
    immediately with the mapped fixed code; no second MODEL command is
    written and no engine respawn happens; the STOP-driven teardown after
    the refusal is clean and the evidence keeps the precise refusal code."""
    _restore_real_process_surfaces(monkeypatch)
    linux._LINUX_RELAY_EVIDENCE.record_call(None, {})
    engine = _StubEngine(tmp_path, 'refuse')
    try:
        _await_stub_started(engine)
        session = _relay_race_session(tmp_path, engine)
        sent = []
        spawns = []
        session._send = lambda line: sent.append(line)
        session._spawn_engine = lambda: spawns.append(1)
        with pytest.raises(ResearchProcessError, match='research_process_failed'):
            session._pump_engine_events()
        assert linux.LINUX_RELAY_EVIDENCE()['code'] == 'relay_refused_target'
        failure = session._cleanup(2000)
        assert failure is None
        assert engine.conclusion() == {'exit_code': 0, 'forced': False,
                                       'engine_done': True}
        assert sent == [] and spawns == []
        assert linux.LINUX_RELAY_EVIDENCE()['code'] == 'relay_refused_target'
    finally:
        engine.discard()


def test_synthetic_engine_success_conclusion_pins_the_call_done_code(
        tmp_path, monkeypatch):
    """W3 plan addendum (R-W7 MAJOR-1): after a clean model_done the success
    contract is the LAST call_done code; the parent-initiated STOP exit is
    the expected clean exit (never an error) and never overwrites the
    pinned evidence code."""
    _restore_real_process_surfaces(monkeypatch)
    linux._LINUX_RELAY_EVIDENCE.record_call(None, {})
    engine = _StubEngine(tmp_path, 'ok')
    try:
        _await_stub_started(engine)
        session = _relay_race_session(tmp_path, engine)
        session._pump_engine_events()
        assert session._last_call_done_code == 'relay_ok'
        session._conclude_relay_call()
        order = []
        original_base = linux._ContainedSession._cleanup

        def recording_base(self, timeout_ms):
            order.append('supervisor-cleanup-started')
            return original_base(self, timeout_ms)

        monkeypatch.setattr(linux._ContainedSession, '_cleanup', recording_base)
        failure = session._cleanup(2000)
        assert failure is None
        assert engine.conclusion() == {'exit_code': 0, 'forced': False,
                                       'engine_done': True}
        # Teardown ordering: the engine fully concluded (engine_done
        # observed) BEFORE the frozen supervisor teardown began.
        assert order == ['supervisor-cleanup-started']
        assert engine.engine_done_seen
        evidence = linux.LINUX_RELAY_EVIDENCE()
        assert evidence['code'] == 'relay_ok'
        assert evidence['counters']['requests_observed'] == 1
    finally:
        engine.discard()


def test_relay_launch_refuses_non_symbolic_base_url_at_the_launch_layer(
        monkeypatch):
    """Plan 7.1.11: a relay launch whose spec declares anything but the
    symbolic endpoint refuses closed at the launch layer — before any
    spawn and before any CONF line exists."""
    record, launch, spec = _wire_session_constructor(
        monkeypatch, relay=True, base_url='https://gateway.example.invalid')
    with pytest.raises(ResearchProcessError,
                       match='research_process_launch_env_forbidden'):
        linux._ContainedSession(spec, launch, None,
                                time.monotonic_ns() + 30000000000)
    assert record['popen'] == [] and record['conf'] is None
    environment = tuple(pair for pair in spec.environment
                        if pair[0] != 'ANTHROPIC_BASE_URL')
    missing = ResearchProcessSpec(spec.argv, spec.cwd, environment,
                                  spec.executable_sha256, spec.timeout_ms)
    with pytest.raises(ResearchProcessError,
                       match='research_process_launch_env_forbidden'):
        linux._ContainedSession(missing, launch, None,
                                time.monotonic_ns() + 30000000000)
    assert record['popen'] == []


def test_offline_path_byte_identity_after_w3():
    """Plan 7.1.12: cheap defense-in-depth re-assertion that the offline
    helper/CONF/argv/policy surfaces still equal the frozen W6 pins on the
    post-W3 tree (W6's own pins run in the same suite; this guards the
    gate recipe from this file too). The pins were re-pinned at the L9.1
    reap-race fix in the W6 module; this test follows them automatically."""
    from tests.test_research_linux_relay_helper_gates import (
        OFFLINE_ARGV_MODEL_SHA256, OFFLINE_ARGV_VERSION_SHA256,
        OFFLINE_CONF_SHA256, OFFLINE_HELPER_SHA256, OFFLINE_POLICY_SHA256,
        _argv_digest, _conf_digest, _gate_conf, _helper_namespace)
    assert sha256(linux.HELPER_SOURCE.encode('utf-8')).hexdigest() \
        == OFFLINE_HELPER_SHA256
    conf = _gate_conf()
    assert _conf_digest(conf) == OFFLINE_CONF_SHA256
    assert 'relay_channel_fd' not in conf and 'relay_port' not in conf
    namespace = _helper_namespace()
    assert _argv_digest(namespace['_bwrap_argv'](conf, 'version')) \
        == OFFLINE_ARGV_VERSION_SHA256
    assert _argv_digest(namespace['_bwrap_argv'](conf, 'model')) \
        == OFFLINE_ARGV_MODEL_SHA256
    assert _conf_digest(fixed_launch().policy_dict()) == OFFLINE_POLICY_SHA256


# ---------------------------------------------------------------------------
# L9 W3 native parent-path proofs (plan 7.3): opt-in exactly like the file's
# other native tests, skip-until-landed through W6's surface resolver, fail
# closed after the opt-in. DELIVERY_PLAN section 68 production insight: the
# trusted parent already lives inside the delegated cgroup subtree on the
# containment host, so these tests add NO cgroup-migration workaround.
# Network-free by construction: the probe vendor writes a garbage head that
# the real sealed engine refuses before any DNS or upstream connection.
# ---------------------------------------------------------------------------

W3_PROBE_BANNER = 'pal-w3-relay-parent-probe-v1 (synthetic)\n'
W3_PROBE_KEY = 'PAL-W3-PARENT-PATH-SYNTHETIC-KEY'

W3_RELAY_PROBE_C_SOURCE = r'''
/* Synthetic W3 relay parent-path probe vendor (compiled at test time).
 * Explicitly NOT the official image and NOT official evidence: it reads
 * stdin to EOF, then connects to the loopback endpoint named by its own
 * ANTHROPIC_BASE_URL environment and either writes one fixed garbage head
 * (the framing-refusal probe; the credential is never read) or hangs
 * (--pal-hang: the stop/driver-loss probe). No real endpoint anywhere. */
#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

static int endpoint_port(void) {
    const char *url = getenv("ANTHROPIC_BASE_URL");
    if (!url) return -1;
    const char *colon = strrchr(url, ':');
    if (!colon) return -1;
    int port = atoi(colon + 1);
    if (port < 1 || port > 65535) return -1;
    return port;
}

static int connect_loopback(int port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    struct timeval tv;
    tv.tv_sec = 20;
    tv.tv_usec = 0;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    struct sockaddr_in address;
    memset(&address, 0, sizeof address);
    address.sin_family = AF_INET;
    address.sin_port = htons((unsigned short)port);
    address.sin_addr.s_addr = htonl(0x7f000001u);
    if (connect(fd, (struct sockaddr *)&address, sizeof address) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--version") == 0) {
        fputs("pal-w3-relay-parent-probe-v1 (synthetic)\n", stdout);
        return 0;
    }
    char buffer[65536];
    for (;;) {
        ssize_t got = read(0, buffer, sizeof buffer);
        if (got <= 0) break;
    }
    int hang = 0;
    for (int i = 1; i < argc; i++)
        if (strcmp(argv[i], "--pal-hang") == 0) hang = 1;
    int port = endpoint_port();
    if (port < 0) { fputs("W3-NO-ENDPOINT\n", stdout); return 84; }
    int fd = connect_loopback(port);
    if (fd < 0) { fputs("W3-CONNECT-REFUSED\n", stdout); return 83; }
    if (hang) {
        fputs("W3-VENDOR-CONNECTED\n", stdout);
        fflush(stdout);
        for (;;) pause();
    }
    static const char garbage[] = "PAL-W3-SYNTHETIC-GARBAGE HTTP/1.1\r\n\r\n";
    if (write(fd, garbage, sizeof garbage - 1) < 0) return 85;
    shutdown(fd, SHUT_WR);
    struct timeval tv;
    tv.tv_sec = 5;
    tv.tv_usec = 0;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    long total = 0;
    for (;;) {
        ssize_t got = read(fd, buffer, sizeof buffer);
        if (got <= 0) break;
        total += got;
    }
    close(fd);
    printf("W3-VENDOR-DONE %ld\n", total);
    return 0;
}
'''


def require_relay_parent_path():
    """Opt-in gate for the W3 native parent-path proofs: the file's own
    containment opt-in (skip without it, fail closed after it) plus the W2
    surface resolution (skip-until-landed through W6's resolver; a
    landed-but-divergent surface FAILS)."""
    require_native_containment()
    from tests.test_research_linux_relay_helper_gates import (
        relay_crossing_surface)
    return relay_crossing_surface()


def build_native_relay_probe(directory, *, hang=False):
    """Assemble the native relay call from measured host facts: the W3
    synthetic C probe vendor, the real bwrap/interpreter/closure, the relay
    helper digest, the symbolic endpoint declaration, a REAL synthetic CA
    file pinned exactly, and the declaration-only reviewed trust record.
    Returns (launch, spec, ca_bytes)."""
    from tests.test_research_linux_relay import SYNTHETIC_CA
    surface = require_relay_parent_path()
    # The driver-loss runner passes sys.argv[1] (a str); in-process callers
    # pass pytest's Path. Coerce once so both shapes work identically.
    directory = Path(directory)
    vendor_dir = directory / 'vendor'
    vendor_dir.mkdir(parents=True, exist_ok=True)
    binary, digest, size = _compile(vendor_dir, 'w3-relay-probe',
                                    W3_RELAY_PROBE_C_SOURCE)
    ca_path = directory / 'relay-ca.pem'
    ca_path.write_bytes(SYNTHETIC_CA['pem'])
    trust = relay.RelayTrustConfig(
        ca_bundle=relay.RelayCaBundlePin(str(ca_path), SYNTHETIC_CA['sha256'],
                                         SYNTHETIC_CA['size']))
    interpreter = str(Path(sys.executable).resolve())
    interp_bytes = Path(interpreter).read_bytes()
    wrapper_path = shutil.which('bwrap') or '/usr/bin/bwrap'
    wrapper_bytes = Path(wrapper_path).read_bytes()
    runtime = discover_runtime_closure(interpreter, binary)
    launch = linux.LinuxLaunchSpec(
        wrapper=linux.LinuxArtifactPin(
            str(Path(wrapper_path).resolve()),
            sha256(wrapper_bytes).hexdigest(), len(wrapper_bytes)),
        helper_sha256=surface['helper_digest'],
        interpreter=linux.LinuxArtifactPin(
            interpreter, sha256(interp_bytes).hexdigest(), len(interp_bytes)),
        supervisor_python_home=sys.base_prefix,
        runtime_files=tuple(runtime),
        vendor_guest_path='/pal/vendor/claude',
        helper_guest_path='/pal/runtime/helper.py',
        vendor_size_bytes=size,
        expected_version_output=W3_PROBE_BANNER,
        cgroup_root=os.environ['POLYMARKET_ALPHA_LAB_LINUX_CGROUP_ROOT'],
        scratch_size_bytes=64 * 1024 * 1024,
        guest_tmp_size_bytes=32 * 1024 * 1024,
        memory_max_bytes=512 * 1024 * 1024,
        pids_max=32,
        egress_policy=linux.EGRESS_RELAY,
        relay_trust=trust)
    work = directory / 'work'
    work.mkdir(parents=True, exist_ok=True)
    spec = ResearchProcessSpec(
        (binary, *(('--pal-hang',) if hang else ())), str(work),
        (('HOME', str(directory / 'home')),
         ('CLAUDE_CONFIG_DIR', str(directory / 'config')),
         ('ANTHROPIC_BASE_URL', linux.RELAY_SYMBOLIC_ENDPOINT),
         ('CLAUDE_CODE_MAX_OUTPUT_TOKENS', '8192'),
         ('ANTHROPIC_API_KEY', W3_PROBE_KEY)), digest, 60000,
        cleanup_timeout_ms=15000)
    return launch, spec, SYNTHETIC_CA['pem']


def _probe_open_fds():
    from tests.test_research_linux_relay_helper_gates import _probe_open_fds
    return _probe_open_fds()


@pytest.mark.skipif(not CONTAINMENT_ENABLED,
                    reason='explicit native containment proof is opt-in')
def test_relay_parent_path_synthetic_exchange_native(tmp_path):
    """Plan 7.3.13: the FULL production parent path — run_linux_contained_
    process with a relay launch built from measured host facts, the W3
    synthetic probe vendor and the real sealed ENGINE. The sealed engine
    bytes equal ENGINE_SOURCE at admission; the garbage head the probe
    writes through the inherited channel is refused (relay_refused_framing
    in LINUX_RELAY_EVIDENCE) and the call FAILS CLOSED; zero DNS
    resolutions and zero upstream connections prove the run network-free;
    clean teardown leaves no cgroup survivors and no fd leaks; the offline
    pins stay byte-identical in the same run. The channel bytes reaching
    the engine (guest_bytes_in > 0) is the end-to-end proof that the engine
    was spawned and reading the channel before the vendor wrote: the
    started gate precedes MODEL by construction, so the credential is
    released only after readiness."""
    launch, spec, ca_bytes = build_native_relay_probe(tmp_path)
    probe_admission = linux._Admission(spec, launch)
    try:
        os.lseek(probe_admission.engine_fd, 0, os.SEEK_SET)
        sealed = bytearray()
        while True:
            block = os.read(probe_admission.engine_fd, 65536)
            if not block:
                break
            sealed.extend(block)
        os.lseek(probe_admission.engine_fd, 0, os.SEEK_SET)
        assert bytes(sealed) == relay.ENGINE_SOURCE.encode('utf-8')
        assert sha256(bytes(sealed)).hexdigest() == linux.RELAY_ENGINE_DIGEST
        os.lseek(probe_admission.ca_fd, 0, os.SEEK_SET)
        assert os.read(probe_admission.ca_fd, len(ca_bytes) + 1) == ca_bytes
        os.lseek(probe_admission.ca_fd, 0, os.SEEK_SET)
    finally:
        probe_admission.close()
    before_fds = _probe_open_fds()
    counters_before = linux.LINUX_PHASE_COUNTERS.snapshot()
    with pytest.raises(ResearchProcessError, match='research_process_failed'):
        linux.run_linux_contained_process(
            spec=spec, stdin=b'{"schema_version":"probe"}', launch=launch,
            stop=None)
    evidence = linux.LINUX_RELAY_EVIDENCE()
    assert evidence['code'] == 'relay_refused_framing', evidence
    counters = evidence['counters']
    assert counters['guest_bytes_in'] > 0
    assert counters['dns_resolutions'] == 0
    assert counters['upstream_connections'] == 0
    assert counters['requests_observed'] == 1
    assert all(type(value) is int for value in counters.values())
    counters_after = linux.LINUX_PHASE_COUNTERS.snapshot()
    assert counters_after['version_executions'] \
        == counters_before['version_executions'] + 1
    assert counters_after['model_executions'] \
        == counters_before['model_executions'] + 1
    assert counters_after['contained_calls'] \
        == counters_before['contained_calls'] + 1
    assert not [name for name in os.listdir(launch.cgroup_root)
                if name.startswith('pal-call-')]
    assert _probe_open_fds() == before_fds
    test_offline_path_byte_identity_after_w3()


@pytest.mark.skipif(not CONTAINMENT_ENABLED,
                    reason='explicit native containment proof is opt-in')
def test_relay_parent_path_stop_and_driver_loss_native(tmp_path):
    """Plan 7.3.14 through the production parent path: a cooperative stop
    mid-model (engine STOP honored, fixed code, never a guessed evidence
    code) and parent-liveness loss (a child parent dies mid-call; the
    engine exits on liveness EOF, the supervisor tears the cgroup down and
    empties it, the namespace dies with the parent: no surviving
    contained process anywhere)."""
    from polymarket_alpha_lab.research_dispatch_runner import (
        ResearchDispatchStop)
    launch, spec, _ca = build_native_relay_probe(tmp_path / 'stop', hang=True)
    linux._LINUX_RELAY_EVIDENCE.record_call(None, {})
    stop = ResearchDispatchStop()

    def request_stop_later():
        time.sleep(1.5)
        stop.request_stop()

    thread = threading.Thread(target=request_stop_later)
    thread.start()
    try:
        with pytest.raises(ResearchProcessError,
                           match='research_process_stopped'):
            linux.run_linux_contained_process(
                spec=spec, stdin=b'{"schema_version":"probe"}',
                launch=launch, stop=stop)
    finally:
        thread.join()
    evidence = linux.LINUX_RELAY_EVIDENCE()
    assert evidence['code'] is None  # no call_done ever concluded: never guessed
    assert all(type(value) is int for value in evidence['counters'].values())
    assert not [name for name in os.listdir(launch.cgroup_root)
                if name.startswith('pal-call-')]

    # Parent-liveness loss: a child process occupies the parent seat, is
    # killed mid-call, and every component must die without a daemon.
    runner = tmp_path / 'runner.py'
    repo_root = str(Path(__file__).resolve().parent.parent)
    runner.write_text(
        'import sys\n'
        'sys.path.insert(0, %r)\n'
        'from polymarket_alpha_lab import research_process_linux as linux\n'
        'from tests.test_research_process_linux import build_native_relay_probe\n'
        'launch, spec, _ca = build_native_relay_probe(sys.argv[1], hang=True)\n'
        "print('RUNNING', flush=True)\n"
        'linux.run_linux_contained_process('
        "spec=spec, stdin=b'{\"schema_version\":\"probe\"}', launch=launch, "
        'stop=None)\n' % repo_root, encoding='utf-8')
    child = subprocess.Popen(
        [sys.executable, str(runner), str(tmp_path / 'driver-loss')],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=repo_root)
    try:
        deadline = time.monotonic() + 300
        line = b''
        while time.monotonic() < deadline:
            block = child.stdout.readline()
            if not block:
                pytest.fail('driver-loss runner died before RUNNING')
            line = block
            if line.strip() == b'RUNNING':
                break
        assert line.strip() == b'RUNNING'
        time.sleep(3)  # let the engine, supervisor and vendor come up
    finally:
        child.kill()
        child.wait()
        child.stdout.close()

    def surviving_contained_processes():
        survivors = []
        for entry in os.listdir('/proc'):
            if not entry.isdigit():
                continue
            try:
                with open('/proc/%s/cmdline' % entry, 'rb') as handle:
                    cmdline = handle.read()
            except OSError:
                continue
            if ((b'/proc/self/fd/' in cmdline and b'-B' in cmdline)
                    or cmdline.startswith(b'bwrap')
                    or b'/pal/vendor/claude' in cmdline):
                survivors.append((entry, cmdline[:80]))
        return survivors

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if (not surviving_contained_processes()
                and not [name for name in os.listdir(launch.cgroup_root)
                         if name.startswith('pal-call-')]):
            break
        time.sleep(0.5)
    assert not surviving_contained_processes()
    assert not [name for name in os.listdir(launch.cgroup_root)
                if name.startswith('pal-call-')]


# ---------------------------------------------------------------------------
# L9.1 reap-race regression (2026-10-09): the per-iteration _try_reap in the
# frozen helper sources' phase loops is guarded by ``status is _RUNNING`` so
# an ECHILD None can never overwrite a reaped status. The deterministic proof
# runs on every host (the static shape test below); the behavioral proof
# drives the REAL phase-loop code offline on POSIX (no bwrap, no cgroup, no
# namespace - only fork, pipes and WNOHANG, which the Windows host lacks).
# ---------------------------------------------------------------------------

REAP_RACE_STDERR = b'unrecognized option --pass-fd\n'


class _NullCgroup:
    """Phase-loop stand-in cgroup: admission is the stub stage's to skip and
    teardown always succeeds (no kernel cgroup is touched offline)."""

    def admit(self, pid):
        pass

    def kill(self, deadline_ms):
        return True


def _stub_stage(cfg, cgroup, mode, cred_source_fd):
    """Fork-based stand-in for the helper's _stage on POSIX: same staged
    dict shape (pid plus three pipe fd ends), no namespace and no wrapper
    re-verification. The staged child writes the fixed classify-marker
    stderr and exits 3; a forked GRANDCHILD deliberately outlives it while
    holding the dup2'd stderr write end, so the phase loop's LAST pipe EOF
    arrives strictly AFTER the first successful reap. That is exactly the
    L5-era window in which the pre-fix unconditional per-iteration waitpid
    re-reaps the already-reaped pid (ECHILD -> None) and destroys the
    classified status."""
    stdin_r, stdin_w = os.pipe()
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.dup2(stdin_r, 0)
            os.dup2(stdout_w, 1)
            os.dup2(stderr_w, 2)
            for fd in (stdin_r, stdin_w, stdout_r, stdout_w, stderr_r,
                       stderr_w):
                try:
                    os.close(fd)
                except OSError:
                    pass
            grand = os.fork()
            if grand == 0:
                time.sleep(0.4)
                os._exit(0)
            os.write(2, REAP_RACE_STDERR)
            os._exit(3)
        except BaseException:
            os._exit(98)
    for fd in (stdin_r, stdout_w, stderr_w):
        os.close(fd)
    return {'pid': pid, 'stdin_w': stdin_w, 'stdout_r': stdout_r,
            'stderr_r': stderr_r}


def _run_stub_version_phase(source):
    """Exec the REAL frozen helper source and drive its REAL _run_version
    loop against the stub stage; return the one reported version_done
    event. Only _stage (namespace machinery the race does not touch) is
    replaced."""
    namespace = {}
    exec(compile(source, '<helper-source>', 'exec'), namespace)
    namespace['_stage'] = _stub_stage
    report_r, report_w = os.pipe()
    live_r, live_w = os.pipe()
    cfg = {'max_version_output_bytes': 65536,
           'expected_version_output_hex': b'synthetic-version\n'.hex()}
    try:
        namespace['_run_version'](cfg, _NullCgroup(), report_w, live_r, 15000)
        os.close(report_w)
        report_w = None
        data = bytearray()
        while not data.endswith(b'\n'):
            block = os.read(report_r, 4096)
            assert block, 'the helper closed the report pipe mid-line'
            data.extend(block)
        return json.loads(bytes(data).decode('utf-8'))
    finally:
        for fd in (report_w, report_r, live_r, live_w):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


@pytest.mark.skipif(os.name != 'posix',
                    reason='the fork/pipe/WNOHANG reap mechanism is '
                           'POSIX-only (the Windows host runs the static '
                           'L9.1 guard test instead)')
@pytest.mark.parametrize('source_name',
                         ['HELPER_SOURCE', 'RELAY_HELPER_SOURCE'])
def test_reap_guard_classifies_nonzero_stub_exit_every_repetition(source_name):
    """L9.1 behavioral regression net: the phase loop must classify a
    nonzero wrapper exit as wrapper_option_unsupported on EVERY of 10
    repetitions, for BOTH frozen helper sources.

    Honest scope statement: the stub stage ENGINEERS the race window (the
    grandchild holds the stderr write end 0.4s past the child's exit), so on
    POSIX the pre-fix code fails this deterministically, not
    probabilistically - the observed ~30% production flake was only the
    uncontrolled version of the same window. The 10 repetitions exist as a
    stability net against scheduler variance in the harness itself (the
    pre-fix production race hit ~30%, so 10 catches would be ~97% even
    uncontrolled). The deterministic proof on EVERY host (Windows included)
    is the code change plus the static shape test below; the disappearance
    of the production ~30% flake is observable only in the coordinator's
    server loop runs."""
    source = (linux.HELPER_SOURCE if source_name == 'HELPER_SOURCE'
              else relay.RELAY_HELPER_SOURCE)
    for _ in range(10):
        event = _run_stub_version_phase(source)
        assert event['type'] == 'version_done', event
        assert event['code'] == 'wrapper_option_unsupported', event
        assert event['exit_code'] == 3, event
        assert event['stderr_bytes'] == len(REAP_RACE_STDERR), event


def _is_running_test(node):
    return (isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name) and node.left.id == 'status'
            and len(node.ops) == 1 and isinstance(node.ops[0], ast.Is)
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Name)
            and node.comparators[0].id == '_RUNNING')


def _is_not_running_test(node):
    return (isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name) and node.left.id == 'status'
            and len(node.ops) == 1 and isinstance(node.ops[0], ast.IsNot)
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Name)
            and node.comparators[0].id == '_RUNNING')


def _conjunction_tests(test):
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        out = []
        for value in test.values:
            out.extend(_conjunction_tests(value))
        return out
    return [test]


def _calls_under_running_guard(function_node, callee):
    """Every ``callee(...)`` call that executes only under an
    ``if status is _RUNNING:`` test (the test itself or one conjunct of an
    ``and``): the guard dominates its whole body, so such calls can never
    run after a finalized status exists."""
    guarded = []
    stack = [(child, False)
             for child in ast.iter_child_nodes(function_node)]
    while stack:
        node, under = stack.pop()
        if isinstance(node, ast.If):
            holds = any(_is_running_test(part)
                        for part in _conjunction_tests(node.test))
            for child in node.body:
                stack.append((child, under or holds))
            for child in node.orelse:
                stack.append((child, under))
            continue
        if (under and isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == callee):
            guarded.append(node)
        for child in ast.iter_child_nodes(node):
            stack.append((child, under))
    return guarded


def _named_calls(function_node, callee):
    return [node for node in ast.walk(function_node)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == callee]


def test_both_helper_phase_loops_guard_every_try_reap():
    """L9.1 static shape proof (offline, Windows-runnable): in BOTH frozen
    helper sources, every _try_reap call inside a phase loop
    (_run_version/_run_model) executes only under a
    ``status is _RUNNING`` condition, the deadline-window _reap result is
    assigned only under such a condition, the teardown _reap result is a
    bare discarded expression, and _reap's own poll returns its first
    non-_RUNNING result immediately (so it can never overwrite a finalized
    status). The closed inventory also fails loudly if a _try_reap site
    ever appears in any OTHER helper function."""
    for source_name, source in (('HELPER_SOURCE', linux.HELPER_SOURCE),
                                ('RELAY_HELPER_SOURCE',
                                 relay.RELAY_HELPER_SOURCE)):
        tree = ast.parse(source)
        functions = {node.name: node for node in tree.body
                     if isinstance(node, ast.FunctionDef)}
        enclosing = set()
        for name, node in functions.items():
            if _named_calls(node, '_try_reap'):
                enclosing.add(name)
        assert enclosing == {'_reap', '_run_version', '_run_model'}, \
            (source_name, sorted(enclosing))
        for phase in ('_run_version', '_run_model'):
            node = functions[phase]
            calls = _named_calls(node, '_try_reap')
            assert calls, (source_name, phase)
            guarded = _calls_under_running_guard(node, '_try_reap')
            for call in calls:
                assert any(call is candidate for candidate in guarded), (
                    source_name, phase,
                    'an unguarded per-iteration _try_reap: the L9.1 '
                    'reap race is back')
            reaps = _named_calls(node, '_reap')
            guarded_reaps = _calls_under_running_guard(node, '_reap')
            assigned = [candidate.value for candidate in ast.walk(node)
                        if isinstance(candidate, ast.Assign)
                        and isinstance(candidate.value, ast.Call)
                        and isinstance(candidate.value.func, ast.Name)
                        and candidate.value.func.id == '_reap']
            assert len(reaps) == 2, (source_name, phase, len(reaps))
            # Exactly one _reap result is assigned, and only under the
            # running-status guard (the deadline window).
            assert len(assigned) == 1, (source_name, phase)
            assert any(assigned[0] is candidate for candidate in guarded_reaps), (
                source_name, phase,
                'the assigned _reap result is not gated by '
                'status is _RUNNING')
            # The other _reap call's result is discarded bare (teardown).
            discarded = [call for call in reaps
                         if not any(call is candidate
                                    for candidate in assigned)]
            assert len(discarded) == 1, (source_name, phase)
            bare = [candidate for candidate in ast.walk(node)
                    if isinstance(candidate, ast.Expr)
                    and isinstance(candidate.value, ast.Call)
                    and candidate.value is discarded[0]]
            assert bare, (source_name, phase,
                          'the teardown _reap result is not a bare '
                          'discarded expression')
        # _reap's own poll: the loop assigns _try_reap's result and returns
        # immediately on the first non-_RUNNING value, so an ECHILD None
        # can only ever be _reap's own return, never an overwrite of a
        # stored finalized status. The third statement is the poll sleep.
        loop = [candidate for candidate in functions['_reap'].body
                if isinstance(candidate, ast.While)]
        assert len(loop) == 1, source_name
        body = loop[0].body
        assert (len(body) == 3 and isinstance(body[0], ast.Assign)
                and len(body[0].targets) == 1
                and isinstance(body[0].targets[0], ast.Name)
                and body[0].targets[0].id == 'status'
                and isinstance(body[0].value, ast.Call)
                and isinstance(body[0].value.func, ast.Name)
                and body[0].value.func.id == '_try_reap'
                and isinstance(body[1], ast.If)
                and _is_not_running_test(body[1].test)
                and len(body[1].body) == 1
                and isinstance(body[1].body[0], ast.Return)
                and isinstance(body[1].body[0].value, ast.Name)
                and body[1].body[0].value.id == 'status'
                and isinstance(body[2], ast.Expr)
                and isinstance(body[2].value, ast.Call)), source_name
