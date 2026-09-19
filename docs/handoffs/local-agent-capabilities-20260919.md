# WP-02 local-agent capability handoff — 2026-09-19

## Owner decisions (accepted, not pending)
Repository: `wmqfl861/polymarket-alpha-lab`.
Source reference: `a0fffde24d547cd70c950a39a3fada5f7e29cf92`.
Source tree: `2df55db14ee07573cd55c8547fc7600763d59c60`.
Owner update recorded in PR58 comment `5737627565`.

- D1: use an already-installed local agent; Codex, Claude Code, OpenCode,
  Grok CLI and ZCode CLI are allowed choices. Start capability evaluation with
  Codex, then Claude Code. The owner did not require all five at once.
- D2: all data needed for the project research is allowed, including the selected
  questions, complete rules, approved evidence and subsequent research tool messages.
  This is not permission to transmit credentials or unrelated local files.
- D3: no first-round business quota. Do not invent a money/event/run-count limit
  or encode unlimited as zero, a huge integer or a fake cost-bound attestation.
  Stop/cancellation, bounded individual I/O/output/context and paper-only operation
  remain separate engineering constraints. Current finite request/model limits have
  NOT already been removed by this document.

These instructions supersede the older statement that D1-D3 are undecided.
No additional API-provider selection or API key is required from the owner for
this capability task. Local CLI execution is not necessarily offline inference.
Its own login may be used by a future adapter; this task does not extract it.

## Objective and completed work
The coordinator has checked current main, the original model interface
`complete(*, messages_json, max_output_tokens) -> ResearchModelReply`, and official
Codex/Claude/OpenCode documentation. No local-agent adapter or new runtime entry
has been implemented or activated in this decision-recording stage.

The actual Windows tool versions, distribution identities, launch forms and
supported isolation/output options are not available to the coordinator:
Desktop Commander reports the owner's device offline. Resolve only those local
facts. Do not repeat completed project/CI tests, rewrite code or rerun old PRs.
Do not ask the owner to decide D1-D3 again.

The next implementation will preserve PostgreSQL evidence, original task identity,
completed/incomplete replay and the project's bounded research tools. A CLI
invocation is not necessarily one cloud request; unknown token/cost information
must not be invented or reported as zero. The existing positive-integer
ModelCallBudget is not an unlimited subscription budget.

## Allowed work
Only identify the five named commands from the existing shell command table and
run their documented version/help forms once, in a fresh empty scratch directory.
Prefer the same native Windows environment used to run the project. Report WSL
separately if it is already the intended runtime; do not install or configure WSL.

This is metadata collection, not a Claude/Codex review or a research/model task.
No `exec <prompt>`, `-p <prompt>`, `run <prompt>`, login, auth-status, doctor,
inspect, update, plugin enumeration or credential/config discovery is permitted.
Do not follow a startup prompt or browser login; record it and stop that tool.

No installation/upgrades, new account, secret-store reads, environment dump,
credential fill, browsing of user agent histories, `.env` reads or arbitrary
filesystem search. No execution-policy/ACL/approval bypass; no `--yolo`,
`--dangerously-skip-permissions`, `--full-auto`, automatic permission acceptance
or administrative mode. Do not touch existing agent processes.

Do not write to main, any feature branch, PR49, any database, `.local`, old kit
or backup. Do not run project initialization, migration, database or native tests.
Do not restart the previously blocked CAPI experiment. No real model/market/order
requests. A version/help failure is not permission to run an alternative command.

## Steps

### 1. Source reference and working directory
Read this document from the immutable DELIVERY_SHA supplied by the coordinator.
Record that delivery SHA and source reference in the report. A moved main is not
permission to mutate history: report the current value if already accessible,
but do not block this read-only local inventory merely because main advanced.

Do not clone or switch the user's working repository. Create an empty scratch
directory outside the business installation. In PowerShell, for example:

```powershell
$ErrorActionPreference = 'Stop'
$Scratch = Join-Path ([IO.Path]::GetTempPath()) ('pal-agent-capabilities-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Scratch -ErrorAction Stop | Out-Null
Set-Location -LiteralPath $Scratch
$PSVersionTable.PSVersion.ToString()
[Environment]::Is64BitProcess
```

Only the scratch directory and optional non-secret local report may be created.
No report is a durable business ledger. There are no helper scripts to execute.

### 2. Locate exact named commands without running them
Use the existing command table, not a filesystem-wide search:

```powershell
$Names = @('codex', 'claude', 'opencode', 'grok', 'zcode')
foreach ($Name in $Names) {
    Get-Command -Name $Name -All -ErrorAction SilentlyContinue |
        Select-Object Name, CommandType, Source
}
```

Record missing commands as NOT_INSTALLED_IN_THIS_ENVIRONMENT.
Record duplicate/shadowing entries. Only resolve native applications or normal
installed CLI wrappers; do not invoke an alias, shell function or unknown script.
Do not guess a same-named Grok/ZCode product or install a replacement.

For a known npm wrapper, identify only its interpreter/package entrypoint needed
to explain the launch form. Do not execute a wrapper if it embeds credentials,
changes policy or is otherwise unexpected; report UNSUPPORTED_WRAPPER instead.
Do not publish raw wrapper contents or full environment variables.

### 3. Version and help, no prompt
Use the verified absolute path from step 2. Execute the following ONLY for that
installed tool, separately, preserving stdout, stderr and exit code:

```text
<verified codex path> --version
<verified codex path> --help
<verified codex path> exec --help

<verified claude path> --version
<verified claude path> --help

<verified opencode path> --version
<verified opencode path> --help
<verified opencode path> run --help

<verified grok path> --version
<verified grok path> --help

<verified zcode path> --version
<verified zcode path> --help
```

In PowerShell use the call operator, not Invoke-Expression:

```powershell
& '<verified absolute executable path>' --version
$VersionExit = $LASTEXITCODE
```

Replace the placeholder with the already verified command path. Never construct
a shell string from model output. A nonzero exit or a command requiring login,
network setup or installation stops further commands for that tool; continue
only with the next independently installed allowed tool. No blind retries.

Run in an inspectable terminal session. If a metadata command does not finish,
observe its owned PID/session instead of launching another copy. Do not kill
unrelated/pre-existing agent processes or report an unfinished command as success.
Report a stuck process and what, if anything, was done to that owned process.

### 4. Extract capability evidence, not just a check mark
For Codex check help for:
`exec`, stdin `-`, `--json`, `--output-schema`, `--ephemeral`,
`--sandbox read-only`, `--skip-git-repo-check`, `--ignore-user-config`.
Do not pass `--ignore-rules` or bypass flags. Configuration/tool-isolation behavior
not established by help must be recorded UNKNOWN, not guessed.

For Claude Code check help for:
`--print`, `--output-format json`, `--json-schema`, `--tools`,
`--strict-mcp-config`, `--no-session-persistence`, `--safe-mode` or `--bare`.
These are candidate capabilities, not a command to run with research input.
Do not assume `--bare` and `--safe-mode` have the same authentication behavior.
Do not use a permissions bypass to compensate for a missing option.

For OpenCode record the exact major version (v1/v2 permission formats differ),
`run`, JSON output and supported agent selection. Do not launch its server,
share a session, create an agent or modify permissions/configuration.

For Grok/ZCode record vendor/project identity from the already available version/
help text and installation metadata. If it is ambiguous, report AMBIGUOUS_PRODUCT;
do not substitute a community wrapper or infer an official identity from its name.

For all tools report whether actual configured model, structured-output shape,
token usage, no tool/plugin execution, temporary-session behavior and process-tree
cancellation can be established WITHOUT a real prompt. Usually several items will
remain UNKNOWN and need the chosen adapter's later synthetic smoke test.
Do not report those future tests as completed now.

### 5. Selection and return
Prefer Codex when its installed version provides the required non-interactive
and isolation surface; otherwise assess Claude Code. This selects a development
target, not an automatic fallback chain for a failing research request. Other
allowed tools remain candidates, not silently marked supported.

Return:
- Actual OS/runtime (native Windows vs WSL), shell version, scratch path.
- Each exact command path, wrapper/native identity, version, help command exits.
- Short relevant help excerpts showing supported/missing options.
- Chosen first integration target and reason; unknown capabilities explicitly.
- Any command error, login/setup prompt, unfinished process and cleanup performed.
- Confirmation of no real prompt/model call, secret access, project/data mutation
  or changes to existing CLI settings.

Do not claim that help proves zero internal HTTP calls, a verified invoice,
no hooks on future runs, full sandbox isolation or successful real research.
Keep report content non-secret. No automatic GitHub upload of raw local output.
Return the concise metadata report to the owner for the coordinator to implement
and test the version-matched adapter. Stop here; no code or live-run step follows.

## Primary references checked 2026-09-19
These references describe available interfaces, not the installed local version.
- https://developers.openai.com/codex/noninteractive/
- https://developers.openai.com/codex/cli/reference/
- https://code.claude.com/docs/en/headless
- https://code.claude.com/docs/en/cli-reference
- https://opencode.ai/docs/cli/
- https://opencode.ai/docs/permissions/
- https://zcode.z.ai/en/docs/welcome

Separate coordinator self-review of this handoff checked: exact owner decisions,
read-only and non-secret scope, no old-approval re-request, no real prompt,
no alias execution, no guessed vendor/version/flag support, no database operation,
and a single return boundary. No runtime test suite or real-agent test was executed.
