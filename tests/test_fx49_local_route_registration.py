"""Exercise the deployment proof, local transport boundary and real tick/worker entry offline."""

from concurrent.futures import ThreadPoolExecutor
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import socket
import subprocess
import sys
import threading
import time
import zipfile

import pytest


BASE = Path(__file__).resolve().parents[1] / 'docs/paper-sim/runtime'
NOTIFY = BASE / 'notification'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


local = module('fx49_test_registration', NOTIFY / 'local_route_registration.py')
ENROLLMENT = BASE / 'scripts/route-enrollment'
admin = module('fx49_test_registrar', ENROLLMENT / 'register_local_route.py')
entry = module('fx49_test_entry', NOTIFY / 'entry.py')
RELEASE = 'c' * 40
LOCAL_ID, WINDOWS_ID = '1' * 32, '2' * 32
WITNESS = (111, 222, 333, 444, 'b' * 64)


def hex_digest(data):
    return hashlib.sha256(data).hexdigest()


def settings():
    return dict(zip(local.FIELDS, ('/synthetic/hc', 'synthetic-account', '/synthetic/endpoint',
                                   hex_digest(LOCAL_ID.encode()), hex_digest(WINDOWS_ID.encode()), 'synthetic-peer')))


def receipt(release=RELEASE, salt=b's' * 32, vals=None, witness=WITNESS):
    return local.MAGIC + salt + local.proof(salt, release, vals or settings(), witness)


class Handoff(Exception):
    pass


@pytest.fixture
def boundary(monkeypatch):
    state = SimpleNamespace(record=receipt(), captures=0, original=0, lookups=0, stamp=WITNESS[2:4])
    def kernel(_):
        state.lookups += 1
        if state.record is None:
            raise OSError('synthetic-unavailable')
        return state.record
    def observe(business, until):
        state.captures += 1
        return settings(), WITNESS
    def route(until):
        state.original += 1
        assert all(getattr(fixed, attr) == settings()[name] for name, attr in local.FIELDS.items())
        return SimpleNamespace(context=SimpleNamespace(socket_identity=state.stamp))
    fixed = SimpleNamespace(route=route, RELEASE_COMMIT=RELEASE, HandoffError=Handoff)
    monkeypatch.setattr(local.KernelRecord, 'read', kernel)
    monkeypatch.setattr(local, 'observe', observe)
    monkeypatch.setattr(local, 'socket_stamp', lambda *_: state.stamp)
    return fixed, state


def test_proof_contains_only_a_random_salted_check_and_binds_every_value():
    digest = receipt()
    assert len(digest) == 68
    assert all(v.encode() not in digest for v in settings().values())
    assert digest != receipt(salt=b't' * 32)
    assert digest != receipt(release='a' * 40)
    assert digest != receipt(witness=(112, *WITNESS[1:]))
    for name in local.FIELDS:
        assert digest != receipt(vals=settings() | {name: 'different'})


def test_parallel_calls_publish_one_observation_and_reuse_only_current_kernel_proof(boundary):
    fixed, state = boundary
    route = local.RegisteredRoute(fixed, Path('/fixture'))
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(list(pool.map(route, [time.monotonic() + 5] * 8))) == 8
    assert (state.captures, state.original, state.lookups) == (1, 8, 8)


@pytest.mark.parametrize('bad', ['missing', 'different proof', 'other release'])
def test_cold_or_restarted_service_refuses_missing_or_wrong_proof_and_never_retries(boundary, bad):
    fixed, state = boundary
    state.record = {'missing': None, 'different proof': receipt(vals=settings() | {'PAL_WINDOWS_HOST': 'changed'}),
                    'other release': receipt(release='d' * 40)}[bad]
    route = local.RegisteredRoute(fixed, Path('/fixture'))
    with pytest.raises(Handoff, match='^route_context_unverified$'):
        route(time.monotonic() + 3)
    state.record = receipt()
    with pytest.raises(Handoff):
        route(time.monotonic() + 3)
    assert state.original == 0 and state.lookups == 1
    assert all(getattr(fixed, attr) == '' for attr in local.FIELDS.values())
    local.RegisteredRoute(fixed, Path('/fixture'))(time.monotonic() + 3)
    assert state.original == 1


@pytest.mark.parametrize('changed', ['missing', 'renewed registration', 'endpoint'])
def test_process_already_bound_rejects_revocation_or_listener_change(boundary, changed):
    fixed, state = boundary
    route = local.RegisteredRoute(fixed, Path('/fixture'))
    route(time.monotonic() + 3)
    if changed == 'endpoint':
        state.stamp = (2, 4)
    else:
        state.record = None if changed == 'missing' else receipt(salt=b'x' * 32)
    with pytest.raises(Handoff):
        route(time.monotonic() + 3)
    assert state.original == 1 and all(getattr(fixed, f) == '' for f in local.FIELDS.values())


def test_late_or_lock_contended_call_does_not_reach_product_or_extend_deadline(boundary):
    fixed, state = boundary
    route = local.RegisteredRoute(fixed, Path('/fixture'))
    route.lock.acquire()
    try:
        deadline = time.monotonic() + 0.015
        with pytest.raises(Handoff):
            route(deadline)
    finally:
        route.lock.release()
    assert time.monotonic() < deadline + 1
    with pytest.raises(Handoff):
        route(time.monotonic() - 1)
    assert state.captures == state.original == 0


def test_endpoint_swapped_during_original_checks_never_reaches_caller(boundary):
    fixed, state = boundary
    fixed.route = lambda until: SimpleNamespace(context=SimpleNamespace(socket_identity=(2, 4)))
    route = local.RegisteredRoute(fixed, Path('/fixture'))
    with pytest.raises(Handoff):
        route(time.monotonic() + 3)
    assert all(getattr(fixed, f) == '' for f in local.FIELDS.values())


def test_native_local_read_accepts_only_a_bounded_single_controller_status():
    client, server = socket.socketpair()
    with client, server:
        server.sendall((json.dumps({'id': 1, 'result': {'core': {'id': LOCAL_ID}}}) + '\n').encode())
        assert local.local_status(client, time.monotonic() + 1) == settings()['PAL_WEIXIN_HC_CONTEXT_SHA256']
        assert json.loads(server.recv(1024)) == {'jsonrpc': '2.0', 'id': 1, 'method': 'server.status', 'params': {}}


@pytest.mark.parametrize('reply', [b'{}\n', b'bad\n', b'x' * 16385, b'{"id":1,"error":{}}\n'])
def test_malformed_local_reply_never_supplies_identity(reply):
    client, server = socket.socketpair()
    with client, server:
        server.sendall(reply)
        with pytest.raises((ValueError, KeyError, local.Unverified)):
            local.local_status(client, time.monotonic() + 1)


def test_changed_site_literals_declined_before_import_or_exec(tmp_path):
    (tmp_path / 'weixin_delivery.py').write_text('raise AssertionError("unverified private source executed")')
    with pytest.raises(local.Unverified):
        local.site_constants(tmp_path)


def test_registration_uses_only_these_existing_site_literals(tmp_path, monkeypatch):
    source = b'CORE="/example"\nWINDOWS_HOST="synthetic"\nWINDOWS_FINGERPRINT="fixture"\nraise AssertionError()\n'
    (tmp_path / 'weixin_delivery.py').write_bytes(source)
    monkeypatch.setattr(local, 'SITE_SOURCE_SHA256', hex_digest(source))
    assert local.site_constants(tmp_path) == {'CORE': '/example', 'WINDOWS_HOST': 'synthetic', 'WINDOWS_FINGERPRINT': 'fixture'}


def test_kernel_descriptor_requires_exact_name_root_ownership_and_readonly_possessor_flags(monkeypatch):
    obj = object.__new__(local.KernelRecord)
    responses = ['user;0;0;', 'user;99;0;', 'user;0;0;', 'login;0;0;', 'user;0;0;']
    for index, prefix in enumerate(responses):
        perm = local.PERMISSIONS | (0x04000000 if index == 2 else 0)
        tag = b'other-record' if index == 4 else local.RECORD_NAME
        value = prefix.encode() + f'{perm:x};'.encode() + tag
        def fake(name, argtypes, key, buf, size):
            assert name == 'keyctl_describe' and key == 123
            ctypes.memmove(buf, value + b'\0', len(value) + 1)
            return len(value) + 1
        monkeypatch.setattr(obj, 'call', fake)
        if index:
            with pytest.raises(local.Unverified):
                obj.verify_owner(123)
        else:
            obj.verify_owner(123)


def test_ordinary_service_cannot_publish_or_use_a_receipt_as_authorization(monkeypatch):
    monkeypatch.setattr(admin.os, 'geteuid', lambda: 12)
    with pytest.raises(admin.RegistrationError):
        admin.publish(local, 12, receipt())


@pytest.mark.parametrize('args,idle,registered', [(['tick'], True, True), (['tick'], False, True),
                                                 (['worker', '3'], False, True), (['route-check'], False, True),
                                                 (['worker', '3'], True, True), (['worker', '3'], False, False),
                                                 (['tick'], False, False)])
def test_normal_entry_uses_pinned_modules_common_orig_route_and_no_ambient_private_env(tmp_path, monkeypatch, args, idle, registered):
    release, business, bundle = tmp_path / 'checkout', tmp_path / 'working', tmp_path / 'calls.zip'
    release.mkdir()
    business.mkdir()
    f_src = BASE / 'scripts/weixin_delivery.py'
    for n in ('entry.py', 'local_route_registration.py'):
        (release / n).write_bytes((NOTIFY / n).read_bytes())
    (release / 'weixin_delivery.py').write_bytes(f_src.read_bytes())
    (release / 'site-helpers.sha256').write_bytes(b'')
    daily = ('import sys, time, weixin_delivery\n'
             'def main():\n'
             '    args = sys.argv[1:]\n'
             f'    if {idle!r}: return args\n'
             '    actual = weixin_delivery.route(time.monotonic() + 5)\n'
             '    return args, actual.local_name\n')
    pins = {}
    with zipfile.ZipFile(bundle, 'w') as archive:
        for n, code in [('football_daily.py', daily.encode()), ('football_store.py', b'import weixin_delivery\n')]:
            (business / n).write_bytes(code)
            archive.writestr(n, code)
            pins[n] = (hex_digest(code), hex_digest(code))
    def git(*parts):
        return subprocess.check_output(['/usr/bin/git', '-C', str(release), *parts], stderr=subprocess.DEVNULL)
    git('init', '-q')
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
    sha = git('rev-parse', 'HEAD').decode().strip()
    monkeypatch.setenv('PAL_WEIXIN_RELEASE_COMMIT', sha)
    if registered:
        monkeypatch.setenv('PAL_WEIXIN_ROUTE_ORIGIN', 'kernel-v1')
    else:
        monkeypatch.delenv('PAL_WEIXIN_ROUTE_ORIGIN', raising=False)
    for name, value in settings().items():
        if registered:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setattr(entry, 'ROOT', release)
    monkeypatch.setattr(entry, 'HERE', release)
    monkeypatch.setattr(entry, 'TRANSPORT', release / 'weixin_delivery.py')
    monkeypatch.setattr(entry, 'CALLERS', pins)
    monkeypatch.chdir(release)
    monkeypatch.setattr(sys, 'argv', list(sys.argv))
    monkeypatch.setattr(sys, 'meta_path', list(sys.meta_path))
    for n in ('weixin_delivery', 'football_daily', 'football_store', '_pal_local_route_registration'):
        monkeypatch.setitem(sys.modules, n, None)
        del sys.modules[n]
    loaded = entry.load_verified
    captures, calls, shas = [], [], {}
    def capture(name, path, data):
        mod = loaded(name, path, data)
        if name == '_pal_local_route_registration':
            monkeypatch.setattr(mod.KernelRecord, 'read', lambda self: (captures.append('kernel'), receipt(sha))[1])
            monkeypatch.setattr(mod, 'observe', lambda b, until: (captures.append('local'), (settings(), WITNESS))[1])
            monkeypatch.setattr(mod, 'socket_stamp', lambda *_: WITNESS[2:4])
        if name == 'weixin_delivery':
            # All product decisions remain the original ones. The native HC I/O is synthetic.
            monkeypatch.setattr(mod, '_hc_context', lambda: SimpleNamespace(socket_identity=WITNESS[2:4]))
            def hc(context, args, until):
                calls.append(tuple(args))
                if args == ['--version']:
                    return f'hc {mod.HC_VERSION}\n'.encode()
                if args[:2] == ['server', 'status']:
                    return json.dumps({'version': mod.HC_VERSION, 'core': {'id': LOCAL_ID, 'name': 'fixture'}}).encode()
                assert args == ['machine', 'ls', '--json']
                return json.dumps({'machines': [{'id': WINDOWS_ID, 'name': 'paired', 'host': 'synthetic-peer',
                                                 'state': 'connected'}]}).encode()
            monkeypatch.setattr(mod, '_hc_call', hc)
            monkeypatch.setattr(mod, 'fixed_request', lambda *a, **kw: pytest.fail('real send or remote program requested'))
        shas[name] = hex_digest(data)
        return mod
    monkeypatch.setattr(entry, 'load_verified', capture)
    answer = entry.run(business, bundle, args)
    assert answer == (args if idle else (args, 'fixture'))
    assert captures == ([] if idle or not registered else ['kernel', 'local'])
    assert calls == ([] if idle else [('--version',), ('server', 'status', '--json'), ('machine', 'ls', '--json')])
    assert shas['weixin_delivery'] == hex_digest(f_src.read_bytes())
    if registered:
        assert shas['_pal_local_route_registration'] == hex_digest((NOTIFY / 'local_route_registration.py').read_bytes())
        assert all(k not in os.environ for k in local.FIELDS)
    else:
        assert '_pal_local_route_registration' not in shas
        assert {name: os.environ[name] for name in local.FIELDS} == settings()
    assert list(sys.argv[1:]) == args


def test_first_deployment_requires_independent_match_and_clears_local_values(tmp_path, monkeypatch):
    retained = []
    def observed(business, until):
        current = settings()
        retained.append(current)
        return current, WITNESS
    monkeypatch.setattr(local, 'observe', observed)
    for name in ('setgid', 'setuid', 'setgroups'):
        monkeypatch.setattr(admin.os, name, lambda *args: None)
    for name in ('getuid', 'geteuid'):
        monkeypatch.setattr(admin.os, name, lambda: 42)
    monkeypatch.setattr(admin.pwd, 'getpwuid', lambda uid: SimpleNamespace(pw_gid=42, pw_dir='/synthetic/home'))
    monkeypatch.setattr(admin.os, 'environ', {'SYNTHETIC_OLD_MANAGER_VALUE': 'fixture'})
    dirs = []
    monkeypatch.setattr(admin.os, 'chdir', dirs.append)
    checks = []
    def confirm(kernel, own, witness, query, until):
        checks.append((own.copy(), witness, query))
        assert admin.os.environ == {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'HOME': '/synthetic/home'}
        if query == 'denied':
            raise admin.RegistrationError()
    monkeypatch.setattr(admin, 'confirm_windows', confirm)
    raw = admin.make_receipt(local, 'match', tmp_path, RELEASE, 42, time.monotonic() + 5)
    assert len(raw) == local.RECORD_SIZE and raw[36:] == local.proof(raw[4:36], RELEASE, settings(), WITNESS)
    with pytest.raises(admin.RegistrationError):
        admin.make_receipt(local, 'denied', tmp_path, RELEASE, 42, time.monotonic() + 5)
    assert dirs == ['/synthetic/home'] * 2 and checks[0] == (settings(), WITNESS, 'match')
    assert retained == [{}, {}]


def test_actual_named_local_peer_only_receives_status_and_derives_the_witness_in_memory(tmp_path, monkeypatch):
    path = tmp_path / 'private.sock'
    tmp_path.chmod(0o700)
    constants = {'CORE': str(Path('/proc/self/exe').resolve()), 'WINDOWS_FINGERPRINT': 'a' * 64, 'WINDOWS_HOST': 'fixture-peer'}
    monkeypatch.setattr(local, 'site_constants', lambda path: constants)
    monkeypatch.setattr(local, 'default_socket', lambda uid, home: path)
    requests = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        listener.listen(1)
        def serve():
            conn, _ = listener.accept()
            with conn:
                requests.append(json.loads(conn.recv(1024)))
                conn.sendall((json.dumps({'id': 1, 'result': {'core': {'id': LOCAL_ID}}}) + '\n').encode())
        server = threading.Thread(target=serve, daemon=True)
        server.start()
        actual, witness = local.observe(tmp_path, time.monotonic() + 3)
        server.join(1)
    assert not server.is_alive() and list(actual) == list(local.FIELDS)
    assert actual['PAL_WEIXIN_HC_CONTEXT_SHA256'] == settings()['PAL_WEIXIN_HC_CONTEXT_SHA256']
    assert witness[2:4] == (path.stat().st_dev, path.stat().st_ino)
    assert requests == [{'jsonrpc': '2.0', 'id': 1, 'method': 'server.status', 'params': {}}]


def test_daily_kernel_mode_refuses_lost_anchor_even_with_valid_legacy_values(boundary, monkeypatch):
    fixed, state = boundary
    state.record = None
    for name, attr in local.FIELDS.items():
        monkeypatch.setenv(name, settings()[name])
        setattr(fixed, attr, settings()[name])
    registered = local.RegisteredRoute(fixed, Path('/fixture'))
    with pytest.raises(Handoff):
        registered(time.monotonic() + 3)
    assert state.original == 0 and all(getattr(fixed, attr) == '' for attr in local.FIELDS.values())


def test_deployment_file_is_the_exercised_public_query_and_check_needs_unique_connected_peer():
    import base64
    text = (ENROLLMENT / 'Read-PairedController.ps1').read_bytes()
    assert hex_digest(text) == admin.WINDOWS_QUERY_SHA256
    nonce = '3' * 32
    signed = lambda value: base64.b64encode(hashlib.sha256((nonce + ':' + value).encode()).digest()).decode()
    row = {'proof': signed(LOCAL_ID), 'connected': True}
    data = {'schema': 1, 'status': 'controller_read', 'controller_process_seen': True,
            'own': signed(WINDOWS_ID), 'peers': [row]}
    admin.verify_reverse(data, nonce, LOCAL_ID, WINDOWS_ID)
    for change in ({'peers': []}, {'peers': [row, row]}, {'peers': [row | {'connected': False}]},
                   {'own': signed(LOCAL_ID)}, {'controller_process_seen': False}):
        with pytest.raises(admin.RegistrationError):
            admin.verify_reverse(data | change, nonce, LOCAL_ID, WINDOWS_ID)


def test_deployment_parent_waits_for_exit_not_just_pipe_and_kills_to_absolute_deadline(monkeypatch, tmp_path):
    def stuck(*args):
        time.sleep(15)
    monkeypatch.setattr(admin, 'make_receipt', stuck)
    start = time.monotonic()
    with pytest.raises(admin.RegistrationError):
        admin.collect_from_original(local, '', tmp_path, RELEASE, 42, start + 0.05)
    assert time.monotonic() - start < 1


def test_deployment_requires_protected_source_before_execution(tmp_path):
    example = tmp_path / 'script.py'
    example.write_text('raise AssertionError()', encoding='ascii')
    with pytest.raises(admin.RegistrationError):
        admin.protected_path(example)


@pytest.mark.parametrize('wrong', ['none', 'local disconnected', 'reverse disconnected'])
def test_deployment_only_standin_validates_both_controllers_and_one_registered_program(tmp_path, wrong):
    import base64
    import re
    site = settings()
    path = tmp_path / 'peer.sock'
    tmp_path.chmod(0o700)
    site['PAL_WEIXIN_HC_SOCKET'] = str(path)
    requests = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        server.listen(1)
        witness = (os.getuid(), os.getpid(), path.stat().st_dev, path.stat().st_ino,
                   local.digest_file(Path('/proc/self/exe'), time.monotonic() + 5))
        def serve():
            client, _ = server.accept()
            with client, client.makefile('rb') as stream:
                for line in stream:
                    request = json.loads(line)
                    name, params = request['method'], request['params']
                    requests.append((name, params))
                    if name == 'server.status':
                        result = {'core': {'id': LOCAL_ID, 'name': 'fixture'}}
                    elif name == 'machine.list':
                        state = 'down' if wrong == 'local disconnected' else 'connected'
                        result = {'machines': [{'id': WINDOWS_ID, 'name': 'fixture-peer', 'host': site['PAL_WINDOWS_HOST'], 'state': state}]}
                    else:
                        assert name == 'machine.exec' and params['on'] == WINDOWS_ID
                        script = params['argv'][-1]
                        nonce = re.search("GetBytes\\('([a-f0-9]{32})'", script)[1]
                        signed = lambda value: base64.b64encode(hashlib.sha256((nonce + ':' + value).encode()).digest()).decode()
                        proof = {'schema': 1, 'status': 'controller_read', 'controller_process_seen': True, 'own': signed(WINDOWS_ID),
                                 'peers': [{'proof': signed(LOCAL_ID), 'connected': wrong != 'reverse disconnected'}]}
                        result = {'code': 0, 'stdout': base64.b64encode(json.dumps(proof).encode()).decode()}
                    client.sendall((json.dumps({'id': request['id'], 'result': result}) + '\n').encode())
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        query = (ENROLLMENT / 'Read-PairedController.ps1').read_text()
        if wrong == 'none':
            admin.confirm_windows(local, site, witness, query, time.monotonic() + 5)
        else:
            with pytest.raises(admin.RegistrationError):
                admin.confirm_windows(local, site, witness, query, time.monotonic() + 5)
        thread.join(1)
    assert not thread.is_alive()
    assert [n for n, _ in requests] == (['server.status', 'machine.list'] if wrong == 'local disconnected'
                                        else ['server.status', 'machine.list', 'machine.exec'])
    if requests[-1][0] == 'machine.exec':
        args = requests[-1][1]['argv']
        assert args[:4] == [admin.SHELL, '-NoProfile', '-NonInteractive', '-Command']
        assert all(value not in args[-1] for value in (LOCAL_ID, WINDOWS_ID, site['PAL_WINDOWS_HOST']))


def test_open_proc_exe_hashes_the_running_image_after_its_installed_path_is_replaced(tmp_path):
    import shutil
    original = tmp_path / 'fixture-program'
    replacement = tmp_path / 'fixture-upgrade'
    shutil.copy2('/usr/bin/sleep', original)
    shutil.copy2('/usr/bin/true', replacement)
    deadline = time.monotonic() + 5
    previous = local.digest_file(original, deadline)
    process = subprocess.Popen([original, '10'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        image = Path('/proc') / str(process.pid) / 'exe'
        os.replace(replacement, original)
        current = local.digest_file(original, deadline)
        assert current != previous
        assert local.digest_file(image, deadline, owner=os.getuid()) == previous
    finally:
        process.terminate()
        process.wait(timeout=2)
