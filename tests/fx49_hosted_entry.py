import ast
import ctypes
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

import pytest
from _pytest import junitxml


_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES = 'tests/fixtures/fx49-hosted-entry'
_DRILL = 'Test-PaperWeixinOffline.ps1'
_ENTRY = 'Invoke-PaperWeixin.ps1'
_DRILL_SHA = 'ef0f816c51b7dbd8ebb47231230b02ecb45f60fab61dd8dd0b4f7a4f563dd0c6'
_ENTRY_SHA = 'e86beca8f4073da75a8bc1cd3aeef3e85230417c0b19f9424432e79baa7998c6'
_MATERIAL = (
    (_DRILL, '80401df29554bf4e2dcfdb560498e12bd7ecc934', 19359, _DRILL_SHA),
    (_ENTRY, 'e57e6d79749fc216246017e3b511566a45b8beef', 35487, _ENTRY_SHA),
)
_PUBLIC_NAME = 'fx49-lab'
_FIXTURE_ID = 'fx49-fixture-"dm"\\'
_TARGET_SHA = 'e53f63885ae627c2a4da5913bb5b7a7d26bffa415549f9f5b9a482bbca5b9c4c'
_LITERAL_FRAME = (
    b'{"protocol":"paper-weixin-fixed-v1","purpose":"check","hostname":"fx49-lab",'
    b'"script_sha256":"e86beca8f4073da75a8bc1cd3aeef3e85230417c0b19f9424432e79baa7998c6",'
    b'"target_digest":"e53f63885ae627c2a4da5913bb5b7a7d26bffa415549f9f5b9a482bbca5b9c4c"}'
)
_LITERAL_SHA = '98694689d6764e613aa984bf7a29919adfcb3054af52f6337b98b64f11bcdb3e'

_PREFIXES = {'none', 'utf8-bom', 'utf16le-bom', 'utf16be-bom', 'utf32le-bom', 'utf32be-bom', 'other', 'unknown'}
_STATUS = {'pass', 'fail', 'unknown'}
_FRAME_AXES = (
    'decode', 'flat_object', 'protocol', 'script_sha256', 'target_digest',
    'hostname_shape', 'hostname_equal', 'purpose', 'keys',
)
_READER_FIELDS = {
    'oracle': {'pass', 'unknown'},
    'bytes': {'equal', 'prefix-only', 'different', 'unknown'},
    'prefix': _PREFIXES,
    'arg_count': {'zero', 'one', 'multiple', 'unknown'},
    'nonce_shape': _STATUS,
    'nonce_equal': _STATUS,
    **{f'{side}_{axis}': _STATUS for side in ('actual', 'canonical') for axis in _FRAME_AXES},
}
_WRITER_FIELDS = {
    'input_encoding': {'utf8', 'utf16le', 'utf16be', 'utf32le', 'utf32be', 'other', 'unknown'},
    'input_preamble': _PREFIXES,
    'child_exit': {'0', '21', 'unknown'},
    'child_stderr': {'yes', 'no', 'unknown'},
    'stdin_failed': {'yes', 'no', 'unknown'},
}
_SOURCE_FAILURE = 'FX-49 pinned source validation failed'
_HANDLE_FAILURE = 'FX-49 verifier standard handles could not be confirmed'
_REPORT_FAILURE = 'FX-49 observation report is invalid'


class _DiagnosticFailure(Exception):
    pass


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_sessionfinish(session):
    if session.config.stash.get(junitxml.xml_key, None) is None:
        yield
        return
    # The locked reporter reads its own platform binding during XML serialization.
    original = junitxml.platform
    junitxml.platform = SimpleNamespace(node=lambda: _PUBLIC_NAME)
    try:
        yield
    finally:
        junitxml.platform = original


def _require(condition, label):
    if not condition:
        raise _DiagnosticFailure(label)


def _git(*args):
    result = subprocess.run(
        ['git', '--no-replace-objects', *args], cwd=_ROOT, stdin=subprocess.DEVNULL,
        capture_output=True, timeout=20, check=False,
        env={**os.environ, 'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': ''},
    )
    _require(result.returncode == 0 and not result.stderr, 'FX-49 local Git read failed')
    return result.stdout


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _source_blob(checkout, path, oid, size, digest):
    info = (_ROOT / path).lstat()
    reparse = getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
    _require(stat.S_ISREG(info.st_mode) and not reparse, _SOURCE_FAILURE)
    tree_entry = _git('ls-tree', '-z', checkout, '--', path)
    suffix = f' blob {oid}\t{path}\0'.encode('ascii')
    _require(tree_entry in (b'100644' + suffix, b'100755' + suffix), _SOURCE_FAILURE)
    # Use object bytes so checkout newline conversion cannot alter the parsed plan inputs.
    data = _git('cat-file', 'blob', oid)
    _require(len(data) == size and _sha256(data) == digest, _SOURCE_FAILURE)
    return data


def _target_digest():
    return _sha256(('weixin:' + _FIXTURE_ID).encode('utf-8'))


def _frame(hostname):
    fields = {
        'protocol': 'paper-weixin-fixed-v1',
        'purpose': 'check',
        'hostname': hostname,
        'script_sha256': _ENTRY_SHA,
        'target_digest': _target_digest(),
    }
    return json.dumps(fields, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _computer_name():
    get_name = ctypes.WinDLL('kernel32.dll', use_last_error=True).GetComputerNameW
    get_name.argtypes = (wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    get_name.restype = wintypes.BOOL
    capacity = wintypes.DWORD(256)
    buffer = ctypes.create_unicode_buffer(capacity.value)
    _require(
        get_name(buffer, ctypes.byref(capacity)) and 0 < capacity.value < len(buffer) and buffer.value,
        'FX-49 Windows frame expectation is unavailable',
    )
    return buffer.value


def _verifier_stdin(checkout):
    # The verifier delegates its handle setup to run(). Read the tracked options without importing it.
    source = _git('cat-file', 'blob', f'{checkout}:scripts/verify_fx49_publication.py')
    _require(0 < len(source) <= 1_000_000, _HANDLE_FAILURE)
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        raise _DiagnosticFailure(_HANDLE_FAILURE) from None
    wrappers = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run']
    _require(len(wrappers) == 1, _HANDLE_FAILURE)
    calls = [
        node for node in ast.walk(wrappers[0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess'
        and node.func.attr == 'run'
    ]
    _require(len(calls) == 1, _HANDLE_FAILURE)
    call = calls[0]
    _require(len(call.args) <= 1 and all(not isinstance(arg, ast.Starred) for arg in call.args), _HANDLE_FAILURE)
    options = {keyword.arg: ast.unparse(keyword.value) for keyword in call.keywords}
    _require(None not in options and len(options) == len(call.keywords), _HANDLE_FAILURE)
    capture = options.get('capture_output', 'False')
    stdout = options.get('stdout', 'None')
    stderr = options.get('stderr', 'None')
    _require(
        (capture == 'True' and stdout == stderr == 'None')
        or (capture == 'False' and stdout == stderr == 'subprocess.PIPE'),
        _HANDLE_FAILURE,
    )
    _require(
        options.get('shell', 'False') == 'False'
        and options.get('startupinfo', 'None') == 'None'
        and options.get('creationflags', '0') == '0'
        and options.get('start_new_session', 'False') == 'False',
        _HANDLE_FAILURE,
    )
    stdin = options.get('stdin', 'None')
    input_arg = options.get('input', 'None')
    if stdin == 'None' and input_arg in ("b''", "''"):
        return subprocess.PIPE
    choices = {'None': None, 'subprocess.DEVNULL': subprocess.DEVNULL, 'subprocess.PIPE': subprocess.PIPE}
    _require(input_arg == 'None' and stdin in choices, _HANDLE_FAILURE)
    return choices[stdin]


def _unique_object(pairs):
    value = {}
    seen = set()
    for key, item in pairs:
        folded = key.casefold()
        _require(folded not in seen, _REPORT_FAILURE)
        seen.add(folded)
        value[key] = item
    return value


def _report_object(text):
    _require(text.isascii() and text.startswith('{') and text.endswith('}'), _REPORT_FAILURE)
    try:
        return json.loads(text, object_pairs_hook=_unique_object)
    except ValueError:
        raise _DiagnosticFailure(_REPORT_FAILURE) from None


def _check_fields(report, schema):
    for name, choices in schema.items():
        value = report[name]
        _require(type(value) is str and value in choices, _REPORT_FAILURE)


def _check_report(result, record):
    _require(result.returncode == 0 and not result.stderr, 'FX-49 Windows writer failed')
    _require(0 < len(result.stdout) <= 4096 and result.stdout.isascii(), _REPORT_FAILURE)
    writer = _report_object(result.stdout.decode('ascii'))
    _require(type(writer) is dict and writer.keys() == _WRITER_FIELDS.keys() | {'engine', 'reader'}, _REPORT_FAILURE)
    _check_fields(writer, _WRITER_FIELDS)
    version = writer['engine']
    _require(
        type(version) is str and (
            version == 'unknown' or re.fullmatch(r'5\.1\.(?:0|[1-9][0-9]{0,9})\.(?:0|[1-9][0-9]{0,9})', version)
        ),
        _REPORT_FAILURE,
    )
    record('fx49.engine', version)
    for name in _WRITER_FIELDS:
        record(f'fx49.{name}', writer[name])

    raw_reader = writer['reader']
    if raw_reader is None:
        reader = dict.fromkeys(_READER_FIELDS, 'unknown')
    else:
        _require(type(raw_reader) is str and 0 < len(raw_reader) <= 2048, _REPORT_FAILURE)
        reader = _report_object(raw_reader)
        _require(type(reader) is dict and reader.keys() == _READER_FIELDS.keys(), _REPORT_FAILURE)
        _check_fields(reader, _READER_FIELDS)
    for name, value in reader.items():
        record(f'fx49.{name}', value)

    expected = dict.fromkeys(_READER_FIELDS, 'pass')
    expected.update(bytes='equal', prefix='none', arg_count='one')
    _require(
        version != 'unknown' and writer['input_encoding'] != 'unknown' and writer['input_preamble'] != 'unknown'
        and writer['child_exit'] == '0' and writer['child_stderr'] == 'no' and writer['stdin_failed'] == 'no'
        and reader == expected,
        'FX-49 hosted check did not pass',
    )


def _windows_check(checkout, sources, record):
    system_root = Path(os.environ.get('SystemRoot', ''))
    _require(system_root.is_absolute() and re.fullmatch(r'[A-Za-z]:', system_root.drive),
             'FX-49 system Windows PowerShell 5.1 is unavailable')
    engine = system_root / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    _require(engine.is_file(), 'FX-49 system Windows PowerShell 5.1 is unavailable')
    stdin = _verifier_stdin(checkout)
    expected = _frame(_computer_name())
    env = {**os.environ, 'PAL_FX49_FRAME_LENGTH': str(len(expected)), 'PAL_FX49_FRAME_SHA256': _sha256(expected)}
    with tempfile.TemporaryDirectory(prefix='fx49-hosted-') as directory:
        scratch = Path(directory)
        workspace = scratch / 'workspace'
        workspace.mkdir()
        (scratch / f'{_DRILL}.blob').write_bytes(sources[_DRILL])
        (workspace / f'{_ENTRY}.blob').write_bytes(sources[_ENTRY])
        for role, name in (('writer', _DRILL), ('reader', 'fx49_hosted_entry_reader.ps1')):
            helper = _git('cat-file', 'blob', f'{checkout}:tests/fx49_hosted_entry_{role}.ps1')
            _require(helper and helper.isascii() and b'\0' not in helper, 'FX-49 static helper is invalid')
            (scratch / name).write_bytes(helper)
        result = subprocess.run(
            [str(engine), '-NoProfile', '-NonInteractive', '-File', str(scratch / _DRILL),
             '-Workspace', str(workspace), '-ApprovedScriptSha256', _ENTRY_SHA],
            cwd=scratch, env=env, stdin=stdin, capture_output=True, timeout=240, check=False,
        )
    _check_report(result, record)


def _check(record):
    _require(sys.platform in ('linux', 'win32'), 'FX-49 diagnostic platform is unsupported')
    if sys.platform == 'win32':
        _require(
            os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted',
            'FX-49 Windows diagnostic requires a GitHub-hosted runner',
        )
    match = re.fullmatch(rb'([a-f0-9]{40})\r?\n', _git('rev-parse', '--verify', 'HEAD'))
    _require(match is not None, 'FX-49 checkout identity is unavailable')
    checkout = match.group(1).decode('ascii')
    _require(os.environ.get('GITHUB_SHA') in (None, checkout), 'FX-49 checkout identity differs')
    record('fx49.checkout', checkout)
    record('fx49.plan_sha256', '46d1394ab5dd2812d9bba6dad03ee20e3814ea41617673a2ce54769a6e76a099')
    record('fx49.original_signed_r2_sha256', 'a68c46561c73609457ad1cee554b880a73e0ac9310a9b7dce13599a8fd626f9f')
    sources = {}
    for name, oid, size, digest in _MATERIAL:
        path = f'{_FIXTURES}/{name}.blob'
        sources[name] = _source_blob(checkout, path, oid, size, digest)
        record(f'fx49.source.{name}', f'{path}@{oid}')
    frame = _frame(_PUBLIC_NAME)
    _require(
        _target_digest() == _TARGET_SHA and frame == _LITERAL_FRAME and len(frame) == 242
        and _sha256(frame) == _LITERAL_SHA,
        'FX-49 public frame oracle failed',
    )
    if sys.platform == 'win32':
        _windows_check(checkout, sources, record)


def test_valid_check_frame(record_testsuite_property):
    failure = None
    try:
        _check(record_testsuite_property)
    except _DiagnosticFailure as error:
        failure = error.args[0]
    except Exception:
        failure = 'FX-49 diagnostic could not complete'
    if failure is not None:
        pytest.fail(failure, pytrace=False)
