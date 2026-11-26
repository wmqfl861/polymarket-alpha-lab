"""Deployment-only enrollment of one volatile check value for the existing service user."""

import base64
import ctypes
import errno
import hashlib
import hmac
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import resource
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
from types import ModuleType


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
NOTIFY = HERE.parents[1] / 'notification'
WINDOWS_QUERY_SHA256 = '0b897d7aceae4a487c2b704ea85c556e2613e16f8f8e4b6e2acadef2d504ed99'
SHELL = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
SITE_FILES = {
    'football_daily.py': 'a907dcd25264b039613dece60068ae6fa970488f65a81a3062e01c039afe2ead',
    'football_store.py': 'f0d7ce1ef7e0550b9fa9e6c4d07080943a0999ce9b19caeabe927a3ec1f4ed81',
}
GIT_ENV = {'PATH': '/usr/bin:/bin', 'GIT_ALLOW_PROTOCOL': '', 'GIT_NO_LAZY_FETCH': '1',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}


class RegistrationError(Exception):
    pass


def need(condition):
    if not condition:
        raise RegistrationError('deployment_registration_unverified')


def load_bytes(path, data):
    module = ModuleType('pal_deployment_' + path.stem)
    module.__file__ = str(path)
    exec(compile(data, str(path), 'exec', dont_inherit=True), module.__dict__)
    return module


def checked_source(path, limit=512 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        metadata = os.fstat(source.fileno())
        need(stat.S_ISREG(metadata.st_mode) and metadata.st_size <= limit)
        data = source.read(limit + 1)
    need(len(data) <= limit)
    return data


def protected_path(path):
    need(path.is_absolute() and path.resolve(strict=True) == path)
    for component in (path, *path.parents):
        metadata = component.lstat()
        need(metadata.st_uid == 0 and not metadata.st_mode & 0o022)


def seconds(until):
    left = until - time.monotonic()
    need(left > 0)
    return left


def git(until, *args):
    result = subprocess.run(['/usr/bin/git', '--no-replace-objects', '-C', ROOT, *args], env=GIT_ENV,
                            timeout=min(10, seconds(until)), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    need(result.returncode == 0)
    return result.stdout


def release_bytes(release, path, until):
    protected_path(path)
    raw = checked_source(path)
    need(raw == git(until, 'cat-file', 'blob', release + ':' + path.relative_to(ROOT).as_posix()))
    return raw


def publish(kernel, target_uid, record):
    need(os.geteuid() == 0 and len(record) == kernel.RECORD_SIZE and record.startswith(kernel.MAGIC))
    k = kernel.KernelRecord()
    target = k.ring(target_uid)
    try:
        old = k.find(target)
    except OSError as exc:
        if exc.errno != errno.ENOKEY:
            raise
    else:
        # Never update a look-alike owned by the service user.
        k.verify_owner(old)
        k.call('keyctl_update', [ctypes.c_int32, ctypes.c_void_p, ctypes.c_size_t], old, record, len(record))
        return
    thread = k.call('keyctl_get_keyring_ID', [ctypes.c_int32, ctypes.c_int], kernel.THREAD_KEYRING, 1)
    key = k.call('add_key', [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int32],
                 b'user', kernel.RECORD_NAME, record, len(record), thread)
    k.call('keyctl_setperm', [ctypes.c_int32, ctypes.c_uint32], key, kernel.PERMISSIONS)
    k.verify_owner(key)
    k.call('keyctl_link', [ctypes.c_int32, ctypes.c_int32], key, target)


def verify_reverse(data, nonce, own_id, windows_id):
    def challenged(value):
        return base64.b64encode(hashlib.sha256((nonce + ':' + value).encode('ascii')).digest()).decode('ascii')
    need(own_id != windows_id and data.get('schema') == 1 and data.get('status') == 'controller_read'
         and data.get('controller_process_seen') is True)
    need(hmac.compare_digest(data['own'], challenged(windows_id)))
    rows = data['peers']
    need(isinstance(rows, list) and 0 < len(rows) < 1000)
    matched = [r for r in rows if hmac.compare_digest(r['proof'], challenged(own_id))]
    need(len(matched) == 1 and matched[0].get('connected') is True)


def confirm_windows(kernel, settings, witness, query, until):
    def rpc(connection, sequence, name, params):
        wire = json.dumps({'jsonrpc': '2.0', 'id': sequence, 'method': name, 'params': params},
                          separators=(',', ':')).encode() + b'\n'
        answer = kernel.local_exchange(connection, wire, until, 1024 * 1024)
        need(answer.get('id') == sequence and 'error' not in answer and isinstance(answer.get('result'), dict))
        return answer['result']

    uid, pid, device, inode, executable_sha = witness
    endpoint = Path(settings['PAL_WEIXIN_HC_SOCKET'])
    need(kernel.socket_stamp(endpoint, uid) == (device, inode))
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(seconds(until))
        connection.connect(str(endpoint))
        actual_pid, actual_uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        need((actual_pid, actual_uid) == (pid, uid))
        listener = Path('/proc') / str(pid) / 'exe'
        need(hmac.compare_digest(kernel.digest_file(listener, until, owner=uid), executable_sha))
        own = rpc(connection, 1, 'server.status', {})['core']
        local_id, local_name = own['id'], own['name']
        need(re.fullmatch('[0-9a-f]{32}', local_id) and type(local_name) is str)
        need(hashlib.sha256(local_id.encode()).hexdigest() == settings['PAL_WEIXIN_HC_CONTEXT_SHA256'])
        rows = rpc(connection, 2, 'machine.list', {})['machines']
        need(isinstance(rows, list) and 0 < len(rows) < 1000)
        need(all(isinstance(r, dict) and re.fullmatch('[0-9a-f]{32}', r['id']) and type(r['name']) is str for r in rows))
        selected = [r for r in rows if hashlib.sha256(r['id'].encode()).hexdigest() == settings['PAL_HERDR_WINDOWS_FINGERPRINT']]
        need(len(selected) == 1)
        peer = selected[0]
        need(peer.get('state') == 'connected' and peer['host'].casefold() == settings['PAL_WINDOWS_HOST'].casefold())
        need(peer['id'] not in (local_id, local_name) and not any(r is not peer and r['name'] == peer['id'] for r in rows))
        nonce = secrets.token_hex(16)
        need(query.count('CHALLENGE') == 1)
        budget = min(22, int(seconds(until)) - 2)
        need(budget > 0)
        # This deployment-only request is the single registered public-metadata query. Its program receives just the challenge.
        response = rpc(connection, 3, 'machine.exec', {'on': peer['id'], 'argv': [SHELL, '-NoProfile', '-NonInteractive',
                       '-Command', query.replace('CHALLENGE', nonce)], 'timeout': budget})
        need(response.get('code') == 0 and not response.get('timed_out') and not response.get('truncated'))
        data = json.loads(base64.b64decode(response['stdout'], validate=True).decode('utf-8-sig'))
        verify_reverse(data, nonce, local_id, peer['id'])
    need(kernel.socket_stamp(endpoint, uid) == (device, inode))


def make_receipt(kernel, query, business, release, uid, until):
    account = pwd.getpwuid(uid)
    need(uid > 0 and os.path.isabs(account.pw_dir))
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(uid)
    need(os.getuid() == os.geteuid() == uid)
    os.environ.clear()
    os.environ.update({'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'HOME': account.pw_dir})
    os.chdir(account.pw_dir)
    settings, witness = kernel.observe(business, until)
    try:
        confirm_windows(kernel, settings, witness, query, until)
        seconds(until)
        salt = secrets.token_bytes(32)
        return kernel.MAGIC + salt + kernel.proof(salt, release, settings, witness)
    finally:
        settings.clear()


def collect_from_original(kernel, query, business, release, uid, until):
    # Only the child created here writes the pipe, and nobody supplies a purported receipt through stdin.
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        child = os.fork()
    except BaseException:
        os.close(read_fd)
        os.close(write_fd)
        raise
    if child == 0:
        os.close(read_fd)
        try:
            limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
            upper = os.sysconf("SC_OPEN_MAX") if limit == resource.RLIM_INFINITY else limit
            os.closerange(3, write_fd)
            os.closerange(write_fd + 1, upper)
            record = make_receipt(kernel, query, business, release, uid, until)
            need(len(record) == kernel.RECORD_SIZE and os.write(write_fd, record) == len(record))
            status = 0
        except BaseException:
            status = 1
        os._exit(status)
    os.close(write_fd)
    pidfd = None
    reaped = False
    try:
        pidfd = os.pidfd_open(child)
        need(select.select([pidfd], [], [], seconds(until))[0])
        waited, status = os.waitpid(child, os.WNOHANG)
        reaped = waited == child
        need(reaped and os.waitstatus_to_exitcode(status) == 0)
        raw = os.read(read_fd, kernel.RECORD_SIZE + 1)
        need(len(raw) == kernel.RECORD_SIZE)
        return raw
    finally:
        os.close(read_fd)
        if not reaped:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if pidfd is not None:
                select.select([pidfd], [], [], 0.25)
            os.waitpid(child, os.WNOHANG)
        if pidfd is not None:
            os.close(pidfd)


def run(release, business):
    until = time.monotonic() + 40
    need(sys.flags.isolated and os.geteuid() == os.getuid() == 0 and re.fullmatch('[0-9a-f]{40}', release))
    need(business.is_absolute() and business.resolve(strict=True) == business)
    protected_path(ROOT)
    protected_path(ROOT / '.git')
    need((ROOT / '.git').is_dir() and not (ROOT / '.git/objects/info/alternates').exists())
    need(git(until, 'rev-parse', '--verify', 'HEAD') == (release + '\n').encode())
    release_bytes(release, HERE / 'register_local_route.py', until)
    core_path = NOTIFY / 'local_route_registration.py'
    core_bytes = release_bytes(release, core_path, until)
    query_bytes = release_bytes(release, HERE / 'Read-PairedController.ps1', until)
    need(hashlib.sha256(query_bytes).hexdigest() == WINDOWS_QUERY_SHA256)
    core = load_bytes(core_path, core_bytes)
    uid = business.stat().st_uid
    need(uid != 0)
    for name, digest in SITE_FILES.items():
        path = business / name
        need(path.stat().st_uid == uid and hashlib.sha256(checked_source(path)).hexdigest() == digest)
    record = collect_from_original(core, query_bytes.decode('ascii'), business, release, uid, until)
    seconds(until)
    publish(core, uid, record)


if __name__ == '__main__':
    try:
        need(len(sys.argv) == 3)
        run(sys.argv[1], Path(sys.argv[2]))
    except Exception:
        print('deployment_registration_unverified', file=sys.stderr)
        raise SystemExit(1)
    print('volatile_route_origin_registered')
