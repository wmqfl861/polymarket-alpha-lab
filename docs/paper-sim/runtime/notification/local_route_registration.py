"""Verify the deployment's volatile receipt before handing local settings to the fixed route."""

import ast
import ctypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import struct
import sys
import threading
import time


SITE_SOURCE_SHA256 = '8edf943951d480520f840b0cf238fdf7dc5eab346268f96104010a4ea1f7ddb9'
RECORD_NAME = b'polymarket-alpha-lab:fixed-notification-origin:v1'
MAGIC = b'PAL1'
RECORD_SIZE = len(MAGIC) + 64
# A possessor can view, read and search. Only the root owner can write, link or change attributes.
PERMISSIONS = 0x0B3F0000
THREAD_KEYRING = -1
FIELDS = {
    'PAL_WEIXIN_HC_PATH': 'HC_PATH', 'PAL_WEIXIN_HC_USER': 'HC_USER',
    'PAL_WEIXIN_HC_SOCKET': 'HC_SOCKET', 'PAL_WEIXIN_HC_CONTEXT_SHA256': 'HC_CONTEXT_SHA256',
    'PAL_HERDR_WINDOWS_FINGERPRINT': 'WINDOWS_FINGERPRINT', 'PAL_WINDOWS_HOST': 'WINDOWS_HOST',
}


class Unverified(Exception):
    pass


def require(condition):
    if not condition:
        raise Unverified('route_context_unverified')


def remaining(until):
    seconds = until - time.monotonic()
    require(seconds > 0)
    return seconds


def digest_file(path, until, *, owner=None):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        limit = 512 * 1024 * 1024
        metadata = os.fstat(stream.fileno())
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_size <= limit)
        if owner is not None:
            require(metadata.st_uid in (0, owner) and not metadata.st_mode & 0o022)
        digest = hashlib.sha256()
        while True:
            remaining(until)
            data = stream.read(min(limit, 1024 * 1024) + 1)
            require(len(data) <= limit)
            if not data:
                return digest.hexdigest()
            digest.update(data)
            limit -= len(data)



def site_constants(business):
    with (business / 'weixin_delivery.py').open('rb') as stream:
        data = stream.read(512 * 1024 + 1)
    require(len(data) <= 512 * 1024 and hashlib.sha256(data).hexdigest() == SITE_SOURCE_SHA256)
    names = {'CORE', 'WINDOWS_HOST', 'WINDOWS_FINGERPRINT'}
    values = {}
    for node in ast.parse(data).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in names:
                require(name not in values and isinstance(node.value, ast.Constant) and type(node.value.value) is str)
                values[name] = node.value.value
    require(values.keys() == names)
    return values


def default_socket(uid, home):
    # Existing controller's default session. Only the specific optional HC_SOCKET is read.
    configured = os.environ.get('HC_SOCKET')
    if configured:
        return Path(configured)
    runtime = Path('/run/user') / str(uid)
    if runtime.is_dir():
        return runtime / 'herdr-core' / 'default.sock'
    return Path(home) / '.local/state/herdr-core/default/default.sock'


def socket_stamp(endpoint, uid):
    require(endpoint.is_absolute() and endpoint.resolve(strict=True) == endpoint)
    node, parent = endpoint.lstat(), endpoint.parent.lstat()
    require(stat.S_ISSOCK(node.st_mode) and node.st_uid == uid and stat.S_ISDIR(parent.st_mode)
            and parent.st_uid == uid and not parent.st_mode & 0o077)
    return node.st_dev, node.st_ino


def local_exchange(connection, wire, until, limit):
    connection.settimeout(remaining(until))
    connection.sendall(wire)
    answer = bytearray()
    while not answer.endswith(b'\n'):
        connection.settimeout(remaining(until))
        packet = connection.recv(min(4096, limit + 1 - len(answer)))
        require(packet and len(answer) + len(packet) <= limit)
        answer.extend(packet)
    return json.loads(answer)


def status_id(connection, until):
    wire = b'{"jsonrpc":"2.0","id":1,"method":"server.status","params":{}}\n'
    response = local_exchange(connection, wire, until, 16384)
    require(response.get('id') == 1 and 'error' not in response)
    value = response['result']['core']['id']
    require(type(value) is str and re.fullmatch('[0-9a-f]{32}', value))
    return value


def local_status(connection, until):
    return hashlib.sha256(status_id(connection, until).encode('ascii')).hexdigest()


def observe(business, until):
    uid = os.getuid()
    require(uid == os.geteuid())
    account = pwd.getpwuid(uid)
    constants = site_constants(business)
    executable = Path(constants['CORE'])
    require(executable.is_absolute() and os.access(executable, os.X_OK))
    executable_sha = digest_file(executable, until)
    endpoint = default_socket(uid, account.pw_dir)
    stamp = socket_stamp(endpoint, uid)
    until = min(until, time.monotonic() + 4)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(remaining(until))
        connection.connect(str(endpoint))
        pid, peer_uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require(pid > 0 and peer_uid == uid)
        # Opening the procfs link pins the actual executable image, including a replaced or unlinked image.
        listener = Path('/proc') / str(pid) / 'exe'
        require(hmac.compare_digest(digest_file(listener, until, owner=uid), executable_sha))
        local_digest = local_status(connection, until)
    require(socket_stamp(endpoint, uid) == stamp and hmac.compare_digest(digest_file(executable, until), executable_sha))
    remaining(until)
    settings = {'PAL_WEIXIN_HC_PATH': str(executable), 'PAL_WEIXIN_HC_USER': account.pw_name,
                'PAL_WEIXIN_HC_SOCKET': str(endpoint), 'PAL_WEIXIN_HC_CONTEXT_SHA256': local_digest,
                'PAL_HERDR_WINDOWS_FINGERPRINT': constants['WINDOWS_FINGERPRINT'], 'PAL_WINDOWS_HOST': constants['WINDOWS_HOST']}
    return settings, (uid, pid, *stamp, executable_sha)


def proof(salt, release, settings, witness):
    require(len(salt) == 32 and re.fullmatch('[0-9a-f]{40}', release) and settings.keys() == FIELDS.keys())
    evidence = json.dumps([release, *[settings[name] for name in FIELDS], *witness], ensure_ascii=True,
                          separators=(',', ':')).encode('ascii')
    return hashlib.sha256(RECORD_NAME + b'\0' + salt + evidence).digest()


class KernelRecord:
    """Use one exact kernel item. No keyring lists or other payloads are requested."""

    def __init__(self):
        arch = getattr(sys.implementation, '_multiarch', None)
        require(isinstance(arch, str) and re.fullmatch('[a-zA-Z0-9_-]+', arch))
        library = (Path('/usr/lib') / arch / 'libkeyutils.so.1').resolve(strict=True)
        for path in (library, *library.parents):
            metadata = path.stat()
            require(metadata.st_uid == 0 and not metadata.st_mode & 0o022)
        self.library = ctypes.CDLL(str(library), use_errno=True)

    def call(self, name, argtypes, *args):
        func = getattr(self.library, name)
        func.argtypes = argtypes
        serial_result = name in ('add_key', 'keyctl_search', 'keyctl_get_persistent', 'keyctl_get_keyring_ID')
        func.restype = ctypes.c_int32 if serial_result else ctypes.c_long
        result = func(*args)
        if result < 0:
            raise OSError(ctypes.get_errno(), 'kernel_record_unavailable')
        return result

    def ring(self, uid):
        # Link this UID's existing kernel container into a transient thread keyring.
        # The kernel may create an empty container; lookup alone can never create a proof.
        thread = self.call('keyctl_get_keyring_ID', [ctypes.c_int32, ctypes.c_int], THREAD_KEYRING, 1)
        return self.call('keyctl_get_persistent', [ctypes.c_uint32, ctypes.c_int32], uid, thread)

    def find(self, ring):
        return self.call('keyctl_search', [ctypes.c_int32, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int32],
                         ring, b'user', RECORD_NAME, 0)

    def verify_owner(self, key):
        output = ctypes.create_string_buffer(256)
        size = self.call('keyctl_describe', [ctypes.c_int32, ctypes.c_void_p, ctypes.c_size_t], key, output, len(output))
        require(size <= len(output))
        kind, uid, _, perm, name = output.value.split(b';', 4)
        require(kind == b'user' and uid == b'0' and int(perm, 16) == PERMISSIONS and name == RECORD_NAME)

    def read(self):
        key = self.find(self.ring(os.geteuid()))
        self.verify_owner(key)
        output = ctypes.create_string_buffer(RECORD_SIZE)
        size = self.call('keyctl_read', [ctypes.c_int32, ctypes.c_void_p, ctypes.c_size_t], key, output, len(output))
        require(size == RECORD_SIZE and output.raw.startswith(MAGIC))
        return output.raw


class RegisteredRoute:
    def __init__(self, transport, business):
        self.transport = transport
        self.original_route = transport.route
        self.business = business
        self.lock = threading.Lock()
        self.accepted_record = None
        self.endpoint = None
        self.refused = False
        self.clear()

    def clear(self):
        for attribute in FIELDS.values():
            setattr(self.transport, attribute, '')

    def __call__(self, until):
        if not self.lock.acquire(timeout=max(0, until - time.monotonic())):
            raise self.transport.HandoffError('route_context_unverified')
        try:
            try:
                remaining(until)
                require(not self.refused)
                record = KernelRecord().read()
                if self.accepted_record is None:
                    settings, witness = observe(self.business, until)
                    try:
                        require(hmac.compare_digest(record[36:], proof(record[4:36], self.transport.RELEASE_COMMIT, settings, witness)))
                        self.endpoint = (Path(settings['PAL_WEIXIN_HC_SOCKET']), witness[0], witness[2:4])
                        for key, attribute in FIELDS.items():
                            setattr(self.transport, attribute, settings[key])
                    finally:
                        settings.clear()
                    self.accepted_record = record
                else:
                    require(hmac.compare_digest(record, self.accepted_record))
                path, uid, stamp = self.endpoint
                require(socket_stamp(path, uid) == stamp)
                remaining(until)
            except Exception:
                self.refused = True
                self.accepted_record = None
                self.endpoint = None
                self.clear()
                raise self.transport.HandoffError('route_context_unverified') from None
            route = self.original_route(until)
            if route.context.socket_identity != stamp:
                self.refused = True
                self.accepted_record = None
                self.endpoint = None
                self.clear()
                raise self.transport.HandoffError('route_context_unverified')
            return route
        finally:
            self.lock.release()
