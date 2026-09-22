"""N3 seed regressions for linker defects L7-L9 (RV05 node N3).

For N4: adopt/adapt into tests/test_soak_linker_review.py. These
assertions target the PATCHED linker (subpatch linker-subpatch-L7-L9)
and encode the contracts from plan.md's nine-counterexample table:

* L7  -- over-limit probe: a legal JSON prefix of exactly the cap plus
         an illegal tail is 'over_limit', never parsed; duplicate keys,
         NaN/Infinity/-Infinity, non-finite floats and non-object top
         levels are 'invalid'; exact-cap and valid records still pass.
* L8  -- OpenProcess NULL classifies by GetLastError into UNKNOWN
         (access denied = permission unknown, no elevation; invalid
         parameter / other codes = unknown too); never dead.
* L9  -- WAIT_FAILED stays UNKNOWN with the handle still closed exactly
         once; only WAIT_OBJECT_0 proves exit; 258 is alive.

The liveness injections go through the linker's own seam
(``_win_liveness(pid, dll=..., get_error=...)`` / ``_win_start_utc``),
so the classification logic under test is the shipped code path, not a
copy. Windows-real cases are guarded by skipif and run on win32 hosts.
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

LINKER = Path(__file__).resolve().parents[1] / 'tests' / 'support' / 'soak_linker.py'


def _load_linker_module():
    spec = importlib.util.spec_from_file_location(
        'soak_linker_under_test_l7l9', LINKER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


L = _load_linker_module()


def legal_object_of_exact_size(prefix_bytes: int) -> bytes:
    base = {'state': 'STARTED', 'schema': 'x', 'pad': ''}
    minimal = json.dumps(base)
    assert prefix_bytes >= len(minimal)
    base['pad'] = 'x' * (prefix_bytes - len(minimal))
    raw = json.dumps(base).encode('utf-8')
    assert len(raw) == prefix_bytes
    return raw


# ---------------------------------------------------------------- L7 ----

class TestL7BoundedRead:
    def test_exact_cap_accepted(self, tmp_path):
        path = tmp_path / 'exact.json'
        path.write_bytes(legal_object_of_exact_size(L.RECORD_MAX_BYTES))
        raw = L._read_capped(path, L.RECORD_MAX_BYTES)
        assert isinstance(raw, bytes) and len(raw) == L.RECORD_MAX_BYTES
        doc, reason = L._read_json_record(path)
        assert reason == '' and doc['state'] == 'STARTED'

    def test_one_probe_byte_over_limit(self, tmp_path):
        path = tmp_path / 'over1.json'
        path.write_bytes(
            legal_object_of_exact_size(L.RECORD_MAX_BYTES) + b'X')
        assert L._read_capped(path, L.RECORD_MAX_BYTES) == 'OVER_LIMIT'
        doc, reason = L._read_json_record(path)
        assert doc is None and reason == 'over_limit'

    def test_l7_shape_prefix_plus_tail_rejected(self, tmp_path):
        # historical repro shape: 65536-byte legal prefix + illegal tail
        path = tmp_path / 'l7.json'
        path.write_bytes(
            legal_object_of_exact_size(L.RECORD_MAX_BYTES) + b'NOT-JSON{{{')
        doc, reason = L._read_json_record(path)
        assert doc is None and reason == 'over_limit'

    def test_duplicate_keys_invalid(self, tmp_path):
        for text in ('{"a": 1, "a": 2}', '{"o": {"x": 1, "x": 2}}'):
            path = tmp_path / 'dup.json'
            path.write_text(text, encoding='utf-8')
            assert L._read_json_record(path) == (None, 'invalid')

    @pytest.mark.parametrize('literal', ['NaN', 'Infinity', '-Infinity'])
    def test_nonstandard_constants_invalid(self, tmp_path, literal):
        path = tmp_path / 'const.json'
        path.write_text('{"v": ' + literal + '}', encoding='utf-8')
        assert L._read_json_record(path) == (None, 'invalid')

    def test_overflow_exponent_invalid(self, tmp_path):
        path = tmp_path / 'inf.json'
        path.write_text('{"v": 1e999}', encoding='utf-8')
        assert L._read_json_record(path) == (None, 'invalid')

    @pytest.mark.parametrize('text', ['"s"', '123', 'true', 'null', '[]'])
    def test_non_object_top_level_invalid(self, tmp_path, text):
        path = tmp_path / 'top.json'
        path.write_text(text, encoding='utf-8')
        assert L._read_json_record(path) == (None, 'invalid')

    def test_valid_record_accepted_control(self, tmp_path):
        path = tmp_path / 'ok.json'
        path.write_text(json.dumps(
            {'state': 'ARMED_WAITING', 'ratio': 0.25, 'sci': 1.5e10,
             'u': 'héllo ✓', 'nest': {'a': [1, 2]}}), encoding='utf-8')
        doc, reason = L._read_json_record(path)
        assert reason == '' and doc['nest']['a'] == [1, 2]

    def test_deep_nesting_invalid_not_crash(self, tmp_path):
        path = tmp_path / 'deep.json'
        path.write_text('[' * 5000 + ']' * 5000, encoding='utf-8')
        assert L._read_json_record(path) == (None, 'invalid')

    def test_absent_vs_unreadable(self, tmp_path):
        assert L._read_json_record(tmp_path / 'missing.json') == \
            (None, 'absent')
        (tmp_path / 'adir').mkdir()
        assert L._read_json_record(tmp_path / 'adir') == (None, 'unreadable')

    def test_load_state_over_limit_refused(self, tmp_path):
        linker = L.Linker.__new__(L.Linker)  # load_state only needs state_path
        linker.state_path = tmp_path / 'state.json'
        linker.state_path.write_bytes(
            legal_object_of_exact_size(L.RECORD_MAX_BYTES) + b'}')
        with pytest.raises(L.Refused, match='bounded-record'):
            linker.load_state()

    def test_load_binding_over_limit_rejected(self, tmp_path):
        path = tmp_path / 'binding.json'
        path.write_bytes(b'x' * (1024 * 1024 + 1))
        with pytest.raises(L.BindingError, match='exceeds'):
            L.load_binding(path)

    def test_load_binding_nan_rejected(self, tmp_path):
        path = tmp_path / 'binding.json'
        path.write_text('{"a": NaN}', encoding='utf-8')
        with pytest.raises(L.BindingError, match='invalid JSON'):
            L.load_binding(path)


# ------------------------------------------------------------ L8/L9 ----

class FakeDll:
    """Injection seam stand-in; records every call."""

    def __init__(self, *, open_result, open_error=0, wait_result=None,
                 wait_error=0, close_result=1, close_raises=False,
                 open_raises=None, times_result=1):
        self.open_result, self.open_error = open_result, open_error
        self.wait_result, self.wait_error = wait_result, wait_error
        self.close_result, self.close_raises = close_result, close_raises
        self.open_raises, self.times_result = open_raises, times_result
        self.last_error = 0
        self.open_calls, self.wait_calls = [], []
        self.close_calls, self.times_calls = [], []

    def OpenProcess(self, access, inherit, pid):
        self.open_calls.append((access, bool(inherit), pid))
        if self.open_raises is not None:
            raise self.open_raises
        self.last_error = self.open_error
        return self.open_result

    def WaitForSingleObject(self, handle, ms):
        self.wait_calls.append((handle, ms))
        self.last_error = self.wait_error
        return self.wait_result

    def CloseHandle(self, handle):
        self.close_calls.append(handle)
        if self.close_raises:
            raise OSError('close failed')
        return self.close_result

    def GetProcessTimes(self, handle, creation, exit_t, kernel_t, user_t):
        self.times_calls.append(handle)
        total = (1767323045 + 11644473600) * 10_000_000  # 2026-01-02T03:04:05Z
        target = getattr(creation, '_obj', creation)
        target.lo, target.hi = total & 0xFFFFFFFF, total >> 32
        return self.times_result


PID = 4242
WIDE_HANDLE = 0xABCDEF1200000001


class TestL8L9Classification:
    def test_openprocess_null_access_denied_unknown_no_elevation(self):
        dll = FakeDll(open_result=None, open_error=5)
        state, reason = L._win_liveness(PID, dll=dll)
        assert state == 'unknown' and 'access_denied' in reason
        assert dll.open_calls == [(L._WIN_SYNCHRONIZE, False, PID)]
        assert dll.close_calls == []  # NULL is never closed

    def test_openprocess_null_invalid_parameter_unknown(self):
        dll = FakeDll(open_result=0, open_error=87)
        state, reason = L._win_liveness(PID, dll=dll)
        assert state == 'unknown' and 'invalid_parameter' in reason

    def test_openprocess_null_other_error_unknown(self):
        dll = FakeDll(open_result=None, open_error=1816)
        state, reason = L._win_liveness(PID, dll=dll)
        assert state == 'unknown' and 'gle=1816' in reason

    def test_wait_failed_unknown_single_close(self):
        dll = FakeDll(open_result=0x1234, wait_result=0xFFFFFFFF,
                      wait_error=1460)
        state, reason = L._win_liveness(PID, dll=dll)
        assert state == 'unknown' and 'wait_failed' in reason
        assert dll.close_calls == [0x1234]  # exactly once, same handle

    def test_wait_timeout_alive(self):
        dll = FakeDll(open_result=0x50, wait_result=258)
        assert L._win_liveness(PID, dll=dll)[0] == 'alive'
        assert dll.close_calls == [0x50]

    def test_wait_object0_dead_trusted(self):
        dll = FakeDll(open_result=0x60, wait_result=0)
        state, reason = L._win_liveness(PID, dll=dll)
        assert state == 'dead' and 'wait_object_0' in reason
        assert dll.close_calls == [0x60]

    def test_close_failure_no_state_change_no_double_close(self):
        for kwargs in ({'close_result': 0}, {'close_raises': True}):
            dll = FakeDll(open_result=0x70, wait_result=258, **kwargs)
            assert L._win_liveness(PID, dll=dll)[0] == 'alive'
            assert len(dll.close_calls) == 1

    def test_handle_64bit_no_truncation(self):
        dll = FakeDll(open_result=WIDE_HANDLE, wait_result=258)
        L._win_liveness(PID, dll=dll)
        assert dll.wait_calls == [(WIDE_HANDLE, 0)]
        assert dll.close_calls == [WIDE_HANDLE]

    def test_unexpected_wait_code_unknown(self):
        dll = FakeDll(open_result=0x90, wait_result=7)
        assert L._win_liveness(PID, dll=dll)[0] == 'unknown'

    def test_openprocess_oserror_unknown(self):
        dll = FakeDll(open_result=None, open_raises=OSError('boom'))
        state, _ = L._win_liveness(PID, dll=dll)
        assert state == 'unknown' and dll.close_calls == []

    def test_process_times_injection(self):
        ok = FakeDll(open_result=0xA0, times_result=1)
        started = L._win_start_utc(PID, dll=ok)
        assert started == _dt.datetime(2026, 1, 2, 3, 4, 5,
                                       tzinfo=_dt.timezone.utc)
        assert ok.close_calls == [0xA0]
        assert L._win_start_utc(PID, dll=FakeDll(
            open_result=None, open_error=5)) is None
        failed = FakeDll(open_result=0xA1, times_result=0)
        assert L._win_start_utc(PID, dll=failed) is None
        assert failed.close_calls == [0xA1]


@pytest.mark.skipif(os.name != 'nt', reason='Windows-only real ABI')
class TestL8L9RealWindows:
    def test_abi_declared(self):
        kernel32, _ft = L._win_kernel32()
        import ctypes
        assert kernel32.OpenProcess.restype is ctypes.c_void_p
        assert kernel32.WaitForSingleObject.restype is ctypes.c_uint32
        assert kernel32.WaitForSingleObject.argtypes[0] is ctypes.c_void_p
        assert kernel32.CloseHandle.argtypes == [ctypes.c_void_p]
        assert kernel32.GetProcessTimes.restype is ctypes.c_int

    def test_self_alive_real(self):
        assert L._win_liveness(os.getpid())[0] == 'alive'

    def test_pid_alive_mapping_real(self):
        assert L._pid_alive(os.getpid()) is True

    def test_process_start_utc_real(self):
        started = L._process_start_utc(os.getpid())
        assert started is not None
        age = abs((_dt.datetime.now(_dt.timezone.utc)
                   - started).total_seconds())
        assert age < 3600
