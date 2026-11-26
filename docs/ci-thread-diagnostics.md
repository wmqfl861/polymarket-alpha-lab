# CI traceback diagnostics and the PR39 interpreter crash

This is explicitly loaded TEST infrastructure, not a runtime dependency, product
watchdog, new report layer or permission to retry failing acceptance. The first
PR39 Windows native run 34974025205 failed with an access violation while CPython
3.12.10 printed a C-watchdog timeout traceback. It produced no final JUnit, and
was not merged or counted as passed. Other successful workflows did not waive it.

A fixed stdlib-only paired probe on the SAME Windows interpreter, without project
imports, native extensions, a database or deliberate invalid memory access,
reproduced the crash in four of four `dump_traceback_later` samples. Four of four
Python-thread-triggered synchronous `dump_traceback` samples completed normally.
Probe source and original stdout/stderr are retained on review/pr39-native-crash-
20260915 at commit667129448ff71a11337bd041016f6c5c930834ab, with run34975611566 /
artifact10399510615. Its fixed diagnostic samples are not release-test retries.
The reproduction strongly supports the asynchronous C diagnostic dump as the
failure mechanism; it does not prove all possible interpreter bugs eliminated.

## Explicit replacement, unchanged acceptance

The three Windows workflows used this pytest invocation in September 2026:

```
python -m pytest -p no:faulthandler -p tests.ci_thread_dump -o faulthandler_timeout=120 ...
```

Distribution keeps its original180second diagnostic value. The built-in plugin
is REPLACED, not simply disabled: the local plugin installs fatal exception
reporting and one Python-thread diagnostic for each setup/call/teardown protocol.
It uses synchronous `faulthandler.dump_traceback(all_threads=True)` and never calls
`dump_traceback_later`. A descriptor duplicate stays owned until its worker ends,
so finishing one case cannot make its worker write into a later reused descriptor.
Cancellation and worker errors are checked; errors become a nonzero pytest run.
Interactive exception/PDB hooks cancel pending diagnostics. Original enabled
fatal-handler state is restored at teardown, within the same stream-restoration
limitations as pytest's original handler (there is no getter for its prior file).

All old test paths, assertions, ordinary subprocess limits, lock/permission checks
and CI job deadlines applied to that experiment. The120/180second settings are diagnostic triggers,
not the test process's hard exit policy: the previous workflows did NOT enable
`faulthandler_exit_on_timeout`. The new plugin refuses a nonpositive/nonfinite
trigger, simultaneous built-in plugin, or a requested hard-exit mode rather than
silently weakening those settings. It does not alter test collection, reports,
results, skip conditions or retries. Fatal exception reporting remains enabled.
The Linux verification used its existing configuration at the time.

Important tradeoff: the Python diagnostic thread must acquire the GIL. If a native
extension holds it indefinitely, this trigger cannot produce a stack dump. The
outer 20- and 10-minute CI deadlines bounded the original experiment. This is not identical diagnostic coverage, deadlock recovery, a real-time
guarantee, or permission to mark a missing report successful. No test timeout or
assertion is removed to get a passing gate.

Unit tests run real pytest children to check setup/call/teardown dumps, normal
failure and interrupt exit codes, configuration refusal, fatal-handler state,
owned descriptor lifetime, constructor/close errors, and actual Python/pathlib
churn without the unsafe C watchdog. Separately designed review tests first
exposed two descriptor/error-propagation defects in the replacement; both initial
failures and their corrections are retained.

No product module, SQL migration, interpreter version or dependency was changed
for this correction. It does not close the older PR30PowerShell5.1 first-run
reliability issue or any real-model/source/fee/strategy acceptance gate.

References (checked2026-09-15):
https://docs.python.org/3.12/library/faulthandler.html
https://docs.pytest.org/en/stable/how-to/writing_plugins.html
https://docs.pytest.org/en/stable/how-to/failures.html
Upstream similar symptom, not sole causal evidence:
https://github.com/pytest-dev/pytest/issues/7022
