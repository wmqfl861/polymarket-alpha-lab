# ASCII-only, one BTC public probe, no database/model/credentials.
param([Parameter(Mandatory=$true)][string]$SourceRoot)
$ErrorActionPreference = 'Stop'
$Commit = 'c7a6f19ed4ef5e9cbc0d838db3da6ff72f08c3cb'
$Tree = '9b7e48d9e10bfbd242a6c875daf84f9538343837'
$Head = git -C $SourceRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $Head -ne $Commit) { throw 'Wrong source commit.' }
$ActualTree = git -C $SourceRoot rev-parse 'HEAD^{tree}'
if ($LASTEXITCODE -ne 0 -or $ActualTree -ne $Tree) { throw 'Wrong source tree.' }
$Dirty = git -C $SourceRoot status --porcelain
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'Source must be clean.' }
if ((Test-Path -LiteralPath (Join-Path $SourceRoot '.local')) -or
    (Test-Path -LiteralPath (Join-Path $SourceRoot 'PROJECT-BUNDLE.json'))) {
    throw 'Use a new source checkout, not an initialized project or kit.'
}
$Python = Join-Path $SourceRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Project Python is missing.' }
$Cli = Join-Path $SourceRoot 'scripts\discover_crypto_research.py'
$DisabledRaw = & $Python -I $Cli --team crypto_btc --preview --attempts 3
if ($LASTEXITCODE -ne 0) { throw 'Disabled preflight failed.' }
$Disabled = ($DisabledRaw -join "`n") | ConvertFrom-Json -ErrorAction Stop
if ($Disabled.status -ne 'disabled' -or $Disabled.public_gets_upper_bound -ne 0 -or
    $Disabled.model_called -ne $false -or $Disabled.database_written -ne $false) {
    throw 'Disabled preflight contract failed.'
}
# Exactly one invocation. Internal explicit discovery retries are reported.
$Raw = & $Python -I $Cli --team crypto_btc --preview --attempts 3 --allow-public-fetch
$Code = $LASTEXITCODE
$Report = ($Raw -join "`n") | ConvertFrom-Json -ErrorAction Stop
if ($Report.model_called -ne $false -or $Report.database_written -ne $false -or
    $Report.paper_only -ne $true -or $Report.report_only -ne $true -or $Report.readonly -ne $true -or
    $Report.configured_public_gets_ceiling -ne 6 -or $Report.public_gets_upper_bound -gt 6 -or
    $Report.discovery_request_attempts -lt 1 -or $Report.discovery_request_attempts -gt 3 -or
    @($Report.results).Count -ne 1) { throw 'Public report boundary failed.' }
$Item = @($Report.results)[0]
if ($Item.team_id -ne 'crypto_btc' -or @($Item.attempts).Count -ne $Item.request_attempts -or
    $Item.request_attempts -ne $Report.discovery_request_attempts -or
    $Item.preview_invocations -lt 0 -or $Item.preview_invocations -gt 1 -or
    $Item.public_gets_upper_bound -ne ($Item.request_attempts + 3 * $Item.preview_invocations) -or
    $Item.public_gets_upper_bound -ne $Report.public_gets_upper_bound) {
    throw 'Per-attempt accounting failed.'
}
if (($Code -eq 0) -ne ($Item.status -eq 'prepared')) { throw 'Exit code and result disagree.' }
$Dirty = git -C $SourceRoot status --porcelain
if ($LASTEXITCODE -ne 0 -or $Dirty -or (Test-Path -LiteralPath (Join-Path $SourceRoot '.local'))) {
    throw 'Source changed or unexpected database state appeared.'
}
[pscustomobject]@{
    implementation_commit = $Head
    implementation_tree = $ActualTree
    shell_major = $PSVersionTable.PSVersion.Major
    process_exit_code = $Code
    status = $Item.status
    reason_code = $Item.reason_code
    preview_reason_code = $Item.preview_reason_code
    request_attempts = $Item.request_attempts
    attempts = $Item.attempts
    recovered_after_failure = $Item.recovered_after_failure
    preview_invocations = $Item.preview_invocations
    public_gets_upper_bound = $Report.public_gets_upper_bound
    configured_public_gets_ceiling = $Report.configured_public_gets_ceiling
    preview = $Item.preview
    model_called = $false
    database_written = $false
    source_unchanged = $true
} | ConvertTo-Json -Depth 12
exit $Code
