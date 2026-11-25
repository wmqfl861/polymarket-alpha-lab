using namespace System.Management.Automation.Language

param([string]$Workspace, [string]$ApprovedScriptSha256)

Set-StrictMode -Version 3
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'

function Get-SliceName([StatementAst]$Statement) {
    if ($Statement -is [FunctionDefinitionAst]) { return $Statement.Name }
    if ($Statement -is [AssignmentStatementAst] -and $Statement.Left -is [VariableExpressionAst]) {
        return $Statement.Left.VariablePath.UserPath
    }
    if ($Statement -is [PipelineAst] -and $Statement.Extent.Text -ceq 'Set-StrictMode -Version 3') {
        return 'Set-StrictMode'
    }
    return ''
}

$report = [ordered]@{
    engine = 'unknown'; input_encoding = 'unknown'; input_preamble = 'unknown'
    child_exit = 'unknown'; child_stderr = 'unknown'; stdin_failed = 'unknown'; reader = $null
}
try {
    $version = $PSVersionTable.PSVersion
    if ($env:GITHUB_ACTIONS -cne 'true' -or $env:RUNNER_ENVIRONMENT -cne 'github-hosted' -or
        [Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
        $PSVersionTable.PSEdition -cne 'Desktop' -or $version -isnot [Version] -or
        $version.Major -ne 5 -or $version.Minor -ne 1 -or $version.Build -lt 0 -or $version.Revision -lt 0) {
        throw 'FX-49 engine unavailable'
    }
    $report.engine = $version.ToString()
    if ($ApprovedScriptSha256 -cne 'e86beca8f4073da75a8bc1cd3aeef3e85230417c0b19f9424432e79baa7998c6' -or
        ![IO.Directory]::Exists($Workspace)) { throw 'FX-49 staging invalid' }

    $sourcePath = [IO.Path]::Combine($PSScriptRoot, 'Test-PaperWeixinOffline.ps1.blob')
    $sourceBytes = [IO.File]::ReadAllBytes($sourcePath)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $digest = [BitConverter]::ToString($sha.ComputeHash($sourceBytes)).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
    if ($sourceBytes.Length -ne 19359 -or
        $digest -cne 'ef0f816c51b7dbd8ebb47231230b02ecb45f60fab61dd8dd0b4f7a4f563dd0c6') {
        throw 'FX-49 drill source invalid'
    }
    $decoder = [Text.UTF8Encoding]::new($false, $true)
    $tokens = $null
    $parseErrors = $null
    $tree = [Parser]::ParseInput($decoder.GetString($sourceBytes), [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count -ne 0 -or $null -eq $tree.EndBlock) { throw 'FX-49 drill AST invalid' }

    $main = @($tree.EndBlock.Statements | Where-Object { $_ -is [TryStatementAst] })
    if ($main.Count -ne 1) { throw 'FX-49 drill AST invalid' }
    $approved = @(
        'Set-StrictMode', 'ErrorActionPreference', 'ProgressPreference', 'Utf8', 'Culture',
        'Protocol', 'ScriptName', 'Nonce', 'FixtureId', 'FixtureMessage', 'FixtureBody',
        'Get-TextDigest', 'Invoke-LocalProcess', 'Invoke-Entry', 'RunRoot',
        'RunRoot', 'install', 'StagedScript', 'Engine', 'TargetDigest'
    )
    $selected = @(
        foreach ($statement in (@($tree.EndBlock.Statements) + @($main[0].Body.Statements))) {
            if ((Get-SliceName $statement) -cin $approved) { $statement }
        }
    )
    $names = @($selected | ForEach-Object { Get-SliceName $_ })
    if (($names -join ',') -cne ($approved -join ',')) { throw 'FX-49 drill AST invalid' }

    # Audit the whole blob because a console input encoding change would affect Process.Start().
    $encodingAssignments = @($tree.FindAll({
        param($node)
        $node -is [AssignmentStatementAst] -and $node.Left -is [VariableExpressionAst] -and
            $node.Left.VariablePath.UserPath -imatch '(?:\A|:)(?:Utf8|OutputEncoding|PSDefaultParameterValues)\z'
    }, $true))
    $console = @($tree.FindAll({
        param($node)
        $node -is [TypeExpressionAst] -and $node.TypeName.FullName -iin @('Console', 'System.Console')
    }, $true))
    if ($encodingAssignments.Count -ne 1 -or
        $encodingAssignments[0].Extent.Text -cne '$Utf8 = [Text.UTF8Encoding]::new($false, $true)' -or
        ![object]::ReferenceEquals($encodingAssignments[0], $selected[3]) -or
        $console.Count -ne 2 -or $console[0].Parent.Extent.Text -cne '[Console]::Error' -or
        $console[1].Parent.Extent.Text -cne '[Console]::WriteLine($report)') {
        throw 'FX-49 encoding parity unknown'
    }

    $root = $Workspace
    foreach ($statement in $selected) {
        $null = . ([scriptblock]::Create($statement.Extent.Text))
    }
    if ($Utf8.CodePage -ne 65001 -or $Utf8.GetPreamble().Length -ne 0 -or
        $Utf8.EncoderFallback -isnot [Text.EncoderExceptionFallback] -or
        $Utf8.DecoderFallback -isnot [Text.DecoderExceptionFallback] -or
        $TargetDigest -cne 'e53f63885ae627c2a4da5913bb5b7a7d26bffa415549f9f5b9a482bbca5b9c4c') {
        throw 'FX-49 writer initialization invalid'
    }

    $caseProfile = [IO.Path]::Combine($RunRoot, 'preflight')
    $null = [IO.Directory]::CreateDirectory($caseProfile)
    $null = [IO.Directory]::CreateDirectory($install)
    [IO.File]::Copy([IO.Path]::Combine($PSScriptRoot, 'fx49_hosted_entry_reader.ps1'), $StagedScript, $false)

    $consoleEncoding = [Console]::InputEncoding
    $report.input_encoding = switch ($consoleEncoding.CodePage) {
        65001 { 'utf8' } 1200 { 'utf16le' } 1201 { 'utf16be' }
        12000 { 'utf32le' } 12001 { 'utf32be' } default { 'other' }
    }
    $report.input_preamble = switch -CaseSensitive ([BitConverter]::ToString($consoleEncoding.GetPreamble())) {
        '' { 'none' }
        'EF-BB-BF' { 'utf8-bom' }
        'FF-FE' { 'utf16le-bom' }
        'FE-FF' { 'utf16be-bom' }
        'FF-FE-00-00' { 'utf32le-bom' }
        '00-00-FE-FF' { 'utf32be-bom' }
        default { 'other' }
    }

    $result = Invoke-Entry $caseProfile 'check' 2>$null 3>$null 4>$null 5>$null 6>$null
    if ($result['exit_code'] -is [int] -and $result['exit_code'] -in @(0, 21)) {
        $report.child_exit = $result['exit_code'].ToString([Globalization.CultureInfo]::InvariantCulture)
    }
    if ($result['stderr_present'] -is [bool]) {
        $report.child_stderr = if ($result['stderr_present']) { 'yes' } else { 'no' }
    }
    if ($result['stdin_failed'] -is [bool]) {
        $report.stdin_failed = if ($result['stdin_failed']) { 'yes' } else { 'no' }
    }

    # Preserve observations from exit 21 while allowing only the finite report vocabulary out.
    $key = '(?:oracle|bytes|prefix|arg_count|nonce_shape|nonce_equal|(?:actual|canonical)_(?:decode|flat_object|protocol|script_sha256|target_digest|hostname_shape|hostname_equal|purpose|keys))'
    $word = '(?:pass|fail|unknown|equal|prefix-only|different|none|utf8-bom|utf16le-bom|utf16be-bom|utf32le-bom|utf32be-bom|other|zero|one|multiple)'
    $pair = '"' + $key + '":"' + $word + '"'
    $shape = '\A\{' + $pair + '(?:,' + $pair + ')*\}\z'
    $raw = $result['stdout']
    if ($raw -is [string] -and $raw.Length -le 2048 -and $raw -cmatch $shape) { $report.reader = $raw }
} catch {}

try {
    [Console]::Write((ConvertTo-Json -InputObject $report -Compress))
    exit 0
} catch { exit 2 }
