"""Bounded stdlib-only timeout-dump diagnosis; never a release acceptance gate.

Compare a C watchdog dump with a Python-thread-triggered synchronous dump while
ordinary pathlib frames are changing. No project imports, DB, network or secrets.
Both subprocess exit codes and untouched stderr are evidence, not retry-to-green.
"""
from __future__ import annotations

import faulthandler
import gc
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import subprocess
import sys
from threading import Event, Thread
import time


def churn(depth, serial):
    if depth:
        return churn(depth - 1, serial + 1)
    p = PureWindowsPath('C:/diagnostic/owned/ordinary-path/' + str(serial))
    return p.root, p.parts, p.parent, p.name


def child(mode):
    # Deliberately no ctypes/native libraries and no artificial access violation.
    faulthandler.enable()
    stop = Event()
    with open(os.devnull, 'w') as sink:
        def locked_dumper():
            while not stop.wait(0.001):
                faulthandler.dump_traceback(file=sink, all_threads=True)
        if mode == 'c-watchdog':
            faulthandler.dump_traceback_later(0.001, repeat=True, file=sink)
            thread = None
        else:
            thread = Thread(target=locked_dumper)
            thread.start()
        deadline = time.monotonic() + 3
        count = 0
        try:
            while time.monotonic() < deadline:
                churn(count % 35, count)
                count += 1
                if count % 200 == 0:
                    gc.collect(0)
        finally:
            if thread is None:
                faulthandler.cancel_dump_traceback_later()
            else:
                stop.set()
                thread.join(5)
                if thread.is_alive():
                    raise RuntimeError('diagnostic thread did not stop')
    print(json.dumps(dict(mode=mode, iterations=count, completed=True)))


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--child':
        if sys.argv[2] not in ('c-watchdog', 'gil-thread'):
            raise ValueError('unknown diagnosis mode')
        child(sys.argv[2])
        return
    if len(sys.argv) != 2:
        raise ValueError('explicit new output directory required')
    destination = Path(sys.argv[1])
    destination.mkdir(exist_ok=False)
    records = []
    for mode in ('c-watchdog', 'gil-thread'):
        for index in range(4):
            name = mode + '-' + str(index)
            timed_out = False
            with (destination / (name + '.stderr')).open('wb') as err:
                try:
                    run = subprocess.run([sys.executable, '-I', __file__, '--child', mode],
                        stdout=subprocess.PIPE, stderr=err, timeout=20, check=False)
                    code, output = run.returncode, run.stdout.decode('utf-8', errors='replace')
                except subprocess.TimeoutExpired:
                    code, output, timed_out = None, '', True
            (destination / (name + '.stdout')).write_text(output, encoding='utf-8')
            records.append(dict(mode=mode, index=index, returncode=code,
                                timed_out=timed_out, output=output))
    summary = dict(python=sys.version, platform=platform.platform(),
        cases=records, release_acceptance=False, application_tests_executed=False,
        repeat_is_fixed_diagnostic_sample_not_acceptance_retry=True)
    (destination / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
