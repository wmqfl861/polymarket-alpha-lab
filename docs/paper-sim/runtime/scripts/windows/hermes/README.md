# Protected Hermes Weixin client

The fixed PowerShell job invokes this client in two forms. It lists the existing private Weixin directory and sends the prevalidated job's note to the same protected recipient digest. The client's production directory is `%ProgramFiles%\PolymarketAlphaLab\WeixinDelivery\Hermes`. The fixed script's existing `weixin-binding.json` stays in the parent directory. The bound account runs the worker with its standard interactive token.

`hermes.exe` embeds the upstream-pinned Win64 CPython in its own process. It loads that interpreter by absolute path, restricts DLL search, enables the Windows policy blocking child processes, and uses isolated startup without site initialization. Terminating the process terminates the sender. `fixed_entry.py` runs the upstream `hermes_cli.send_cmd.cmd_send` and requires the official bundled Weixin adapter from the installed source. Hermes selects the original user profile and applies its own enablement and egress decisions. An admitted plugin whose code comes from outside the approved implementation, including a user model provider, ends the process with status 78. It sends no retry. The native sender retains its existing behavior.

## Rebuild on Linux

The public sources, SHA-256 digests, Win64 wheel set and expected executable hash are listed in [upstream-inputs.json](upstream-inputs.json). The Hermes archive is fixed by its commit and digest. Its `pm/lock.json` supplies the CPython archive and its `uv.lock` supplies the dependency versions and wheel digests. The dependency selection is the locked core with aiohttp for the Weixin adapter.

Download the two listed public archives and place the wheels in a separate build directory. From this source directory, the following commands build the executable and seal the installation archive. Use the MinGW-w64 x86-64 POSIX compiler from Ubuntu's GCC 13 series. The assembler verifies the named inputs, rejects a mismatched executable and leaves an existing output untouched.

```bash
python3 -m pip download --isolated --disable-pip-version-check --no-input \
  --platform win_amd64 --implementation cp --python-version 3.14 --abi cp314 \
  --only-binary=:all: --no-deps --require-hashes --dest "$WHEEL_DIR" \
  --requirement windows-requirements.lock

x86_64-w64-mingw32-gcc-posix -std=c17 -O2 -Wall -Wextra -Wpedantic -Werror \
  -municode -static -s -Wl,--no-insert-timestamp,--nxcompat,--dynamicbase,--high-entropy-va \
  hermes.c -o "$BUILD_DIR/hermes.exe"

python3 prepare_media.py --source-archive "$HERMES_SOURCE_ARCHIVE" \
  --python-archive "$CPYTHON_ARCHIVE" --wheel-dir "$WHEEL_DIR" \
  --launcher "$BUILD_DIR/hermes.exe" --output "$BUILD_DIR/Protected-Hermes-Windows-x64.zip"
```

The media contains `source`, `runtime`, `wheels`, a hash-locked requirements file, the bootstrap and the executable. The operator verifies the whole archive digest, then stages it in the administrator-controlled release directory. Before activation, the bundled interpreter installs the supplied wheel payload under the protected directory. Run the following command from the verified release directory in the elevated installation session.

```powershell
.\runtime\python.exe -I -m pip --isolated --disable-pip-version-check install --no-index --only-binary=:all: --no-deps --require-hashes --no-compile --find-links .\wheels --target .\site-packages --requirement .\windows-requirements.lock
```

The release and its ancestors must pass the project's existing ordinary-token checks for write, delete, replacement and ACL rights. Use the protected absolute path to this executable in the existing binding. Preserve the selected profile, the scope of the binding and the fixed script's existing timeouts. The ordinary worker and Hermes must inherit the bound user's normal Windows temporary directory. No user-supplied plugin, authentication or recipient data is included in the media.

## Verification

`verify_synthetic.py` uses sealed read-only program roots, fresh generated fixtures, an unavailable external network and a substitute at the official adapter's transport call. It exercises the production Python entry. Supply Linux CPython 3.14.9, its matching locked Linux dependencies and the exact program source tree from the media. The test uses bubblewrap and sudo to create the namespaces and drops to the caller's UID inside them.

```bash
python3 verify_synthetic.py --source "$PROGRAM_SOURCE" --runtime "$LINUX_RUNTIME" --site "$LINUX_SITE"
```

A successful Linux receipt covers the production bootstrap's argument checks, code origins, registration, native list and send JSON, original disabled-policy behavior, and refusal before transport. Windows acceptance still requires the ordinary interactive token, the installed application in its protected directory, the original six substituted cases, and the one authorized natural delivery within its existing window. The repository retains the sources, locks, and bounded receipts. Installation binaries and personal binding files stay local.
