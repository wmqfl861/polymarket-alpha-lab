# Windows fixed Weixin relay

The native x64 executable connects HC to the existing fixed delivery script under the already bound account’s standard Windows token. The HC invocation has exactly two arguments in its program vector. The receiver takes the existing JSON on standard input.

```text
%ProgramFiles%\PolymarketAlphaLab\WeixinDelivery\PaperWeixinRelay.exe <32 lowercase hexadecimal characters>
```

The local task starts the same executable with its single reserved argument `--serve` under the bound account’s ordinary token. Use Windows 10 version 1607 or later, or a corresponding Windows Server release, with the native Windows PowerShell installation. The relay reads the binding file’s security descriptor only. The original script and Hermes perform their usual binding checks.

## Installation boundary

Install the executable as `PaperWeixinRelay.exe` alongside the approved `Invoke-PaperWeixin.ps1` and `weixin-binding.json` under the fixed Program Files directory. The executable requires a protected DACL with direct, non-inheritable allow entries for SYSTEM and Administrators and exactly one direct read/execute entry for the bound account. The owner must be Administrators or SYSTEM. Store no account identifier in arguments or configuration for the relay. Keep the installation directory and original files protected from modification and replacement by the standard account.

The worker checks its integrity level, elevation, effective file and directory rights, canonical non-reparse paths, and the SHA-256 of the approved script. It keeps handles to the verified inputs during its lifetime. The account must be able to read the protected Hermes installation and access its existing private binding. Its standard user profile supplies the original local job directory.

## Request execution

The worker creates one local-only named pipe. Its DACL grants the bound account data read/write and synchronization. The client and worker check each other’s process image, installed path, account and live process handle. The client also checks that the worker is unprivileged. The client opens the pipe with anonymous security quality of service. The worker performs its identity checks using query-only process tokens.

A request contains the nonce and up to 20,480 original input bytes. The worker executes the fixed system PowerShell with the arguments below. The working directory and child environment come from Windows system and bound-account profile directories. The environment block is constructed locally.

```text
-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File <protected original script> <nonce>
```

The original script owns schema checks, claim creation, send, cleanup, and receipt recovery. Its normal exit codes and up to 8,192 bytes of stdout pass back unchanged. A relay failure returns 70 with empty stdout. The existing Linux caller decides how to treat that unconfirmed outcome. A busy or disconnected pipe fails the invocation immediately. The relay starts at most one PowerShell invocation per accepted connection.

The child and descendants join a kill-on-close job at process creation. A disconnected client or expired request cancels its job. A durable claim that the original script already wrote remains available to its existing recovery path. Each transfer uses the original 575-second maximum, the HC caller’s shorter per-purpose deadline, bounded local framing and the original script deadlines. The client watchdog closes stalled invocation output. The on-machine rehearsal checks the eight-second cleanup/receipt-class budget with a synthetic refusal.

## Build

From this directory, using an x64 Visual Studio developer prompt with the Windows SDK:

```bat
cl /nologo /std:c++17 /EHsc /O2 /W4 /MT /DUNICODE /D_UNICODE src\main.cpp src\child.cpp src\identity.cpp /Fe:PaperWeixinRelay.exe /link /DYNAMICBASE /HIGHENTROPYVA /NXCOMPAT Advapi32.lib Authz.lib Bcrypt.lib Shell32.lib Ole32.lib Uuid.lib
```

For a native x64 PE from a Linux cross-build, use the POSIX-thread MinGW-w64 compiler. The build uses system DLLs and statically links the compiler runtime.

```sh
x86_64-w64-mingw32-g++-posix -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror \
  -DUNICODE -D_UNICODE -municode -static -s \
  -Wl,--no-insert-timestamp,--nxcompat,--dynamicbase,--high-entropy-va \
  src/main.cpp src/child.cpp src/identity.cpp \
  -lauthz -ladvapi32 -lshell32 -lole32 -luuid -lbcrypt -o PaperWeixinRelay.exe
```

## Offline tests

The portable tests exercise the actual request/response headers, nonce grammar, byte limits, original exit allowlist and the complete command line constructed by the production worker.

```sh
c++ -std=c++17 -Wall -Wextra -Wpedantic -Werror tests/test_protocol.cpp -o test_protocol
./test_protocol
```

After protected installation and start of the ordinary worker, run `tests/Test-PaperWeixinRelayOffline.ps1` under the current script policy from the same account as the HC launcher. It submits only malformed synthetic requests. The genuine script rejects these before reading its binding or calling Hermes. The test obtains the actual child process from local Windows process management, checks its executable and entire command line against the expected fixed system PowerShell, options, protected script and nonce, then requires exit 21 with empty stdout. A missed child observation fails the test. It also checks invalid client arguments, bounds, and rejection of a different client executable. The existing offline substitute suite covers the six delivery cases in the original script. A successful offline relay test does not produce a delivery receipt.
