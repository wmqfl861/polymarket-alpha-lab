# Local offline acceptance runner. Keep this file out of the production install.
[CmdletBinding(DefaultParameterSetName = 'Run')]
param(
    [Parameter(Mandatory = $true)]
    [string]$ApprovedScriptSha256,
    [Parameter(Mandatory = $true, ParameterSetName = 'Run')]
    [string]$Workspace,
    [Parameter(Mandatory = $true, ParameterSetName = 'Boundary')]
    [string]$ProbeScript
)

Set-StrictMode -Version 3
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Utf8 = [Text.UTF8Encoding]::new($false, $true)
$Culture = [Globalization.CultureInfo]::InvariantCulture
$Protocol = 'paper-weixin-fixed-v1'
$ScriptName = 'Invoke-PaperWeixin.ps1'
$Nonce = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
$FixtureId = 'fx49-fixture-"dm"\'
$FixtureMessage = 'fx49-fixture-message'
$FixtureBody = ("Synthetic header" + [char]13 + [char]10 + [char]0x7B2C + [char]0x4E8C +
    [char]0x884C + ' ' + [char]::ConvertFromUtf32(0x1F9EA) + [char]10 +
    'Quotes: " '' $ % \ ' + [char]96 + [char]10)

function Get-PathDigest([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-ApprovedSource {
    $source = [IO.Path]::Combine([IO.Directory]::GetParent($PSScriptRoot).FullName, $ScriptName)
    $manifest = [IO.File]::ReadAllText($source + '.sha256', $Utf8).TrimEnd([char[]]@(13, 10))
    if ($manifest -cne "$ApprovedScriptSha256  $ScriptName" -or (Get-PathDigest $source) -cne $ApprovedScriptSha256) {
        throw 'Staging bytes do not match approved manifest'
    }
    return $source
}

function Get-TextDigest([string]$Text) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($sha.ComputeHash($Utf8.GetBytes($Text))).Replace('-', '').ToLowerInvariant()
    } finally { $sha.Dispose() }
}

function Format-Time([DateTimeOffset]$Time) {
    return $Time.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'", $Culture)
}

function Assert-LocalDirectory([string]$Path) {
    if ($Path -cnotmatch '\A[A-Za-z]:\\[^:\x00-\x1f]*\z') { throw 'Use a local absolute workspace' }
    $resolved = [IO.Path]::GetFullPath($Path)
    if ([IO.DriveInfo]::new([IO.Path]::GetPathRoot($resolved)).DriveType -ne [IO.DriveType]::Fixed) {
        throw 'Use a fixed local volume'
    }
    $current = [IO.DirectoryInfo]::new($resolved)
    while ($null -ne $current) {
        if (!$current.Exists -or ($current.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Workspace and ancestors must be regular directories'
        }
        $current = $current.Parent
    }
    return $resolved
}

function Invoke-LocalProcess([string]$Program, [string[]]$Arguments, [string]$Temp,
                             [string]$Profile = '', [string]$Request = '') {
    $start = [Diagnostics.ProcessStartInfo]::new($Program)
    $start.Arguments = (($Arguments | ForEach-Object {
        '"' + ([regex]::Replace(([regex]::Replace($_, '(\\*)"', '$1$1\"')), '(\\+)\z', '$1$1')) + '"'
    }) -join ' ')
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.WorkingDirectory = $Temp
    $start.EnvironmentVariables['TEMP'] = $Temp
    $start.EnvironmentVariables['TMP'] = $Temp
    if ($Profile) { $start.EnvironmentVariables['LOCALAPPDATA'] = $Profile }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    $launched = $false
    try {
        $previousInputEncoding = [Console]::InputEncoding
        try {
            [Console]::InputEncoding = $Utf8
            $launched = $process.Start()
        } finally {
            [Console]::InputEncoding = $previousInputEncoding
        }
        if (!$launched) { throw 'Subprocess start failed' }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $bytes = $Utf8.GetBytes($Request)
        $inputStream = $process.StandardInput.BaseStream
        $stdinFailed = $false
        try {
            if ($bytes.Length -gt 0) { $inputStream.Write($bytes, 0, $bytes.Length) }
        } catch [IO.IOException] {
            $stdinFailed = $true
        } finally { $inputStream.Dispose() }
        if (!$process.WaitForExit(35000) -or !$stdout.Wait(1000) -or !$stderr.Wait(1000)) {
            throw 'Offline subprocess timed out'
        }
        return @{ exit_code = $process.ExitCode; stdout = $stdout.Result
            stderr_present = [bool]$stderr.Result; stdin_failed = $stdinFailed }
    } finally {
        if ($launched) {
            try { if (!$process.HasExited) { $process.Kill() } } catch {}
        }
        $process.Dispose()
    }
}

function Invoke-Entry([string]$Profile, [string]$Purpose, $Fields = @{}) {
    $request = [ordered]@{
        protocol = $Protocol; purpose = $Purpose; hostname = [Environment]::MachineName
        script_sha256 = $ApprovedScriptSha256; target_digest = $TargetDigest
    }
    foreach ($name in $Fields.Keys) { $request.Add($name, $Fields[$name]) }
    $json = ConvertTo-Json -InputObject $request -Compress
    $argv = @('-NoProfile', '-NonInteractive', '-File', $StagedScript, $Nonce)
    $result = Invoke-LocalProcess $Engine $argv $RunRoot $Profile $json
    $result['reply'] = $null
    if ($result.stdout) {
        try { $result['reply'] = ConvertFrom-Json -InputObject $result.stdout } catch {}
    }
    return $result
}

function Test-Reply($Result, [int]$Code, [string]$Purpose, [string[]]$Fields) {
    if ($Result.exit_code -ne $Code -or $Result.stderr_present -or $Result.stdin_failed) { return $false }
    if ($Code -ne 0 -and $Code -ne 32) { return !$Result.stdout }
    if ($null -eq $Result.reply) { return $false }
    $keys = @('protocol', 'purpose', 'code', 'script_sha256')
    if ($null -ne $Fields) { $keys += $Fields }
    if ($null -ne (Compare-Object $keys @($Result.reply.PSObject.Properties.Name) -CaseSensitive)) { return $false }
    return ($Result.reply.protocol -is [string] -and $Result.reply.protocol -ceq $Protocol -and
        $Result.reply.purpose -is [string] -and $Result.reply.purpose -ceq $Purpose -and
        ($Result.reply.code -is [int] -or $Result.reply.code -is [long]) -and $Result.reply.code -eq $Code -and
        $Result.reply.script_sha256 -is [string] -and $Result.reply.script_sha256 -ceq $ApprovedScriptSha256)
}

function New-Fixture([string]$Name, [string]$Mode) {
    $profile = [IO.Path]::Combine($RunRoot, $Name)
    $null = [IO.Directory]::CreateDirectory($profile)
    [IO.File]::WriteAllText([IO.Path]::Combine($profile, 'fx49-fixture-mode'), $Mode, $Utf8)
    [IO.File]::WriteAllText([IO.Path]::Combine($profile, 'fx49-expected-note'), $FixtureBody, $Utf8)
    return $profile
}

function Get-ReceiptState($Result, $Job, [string]$ReceiptPath, [string]$ExpectedReason) {
    if (Test-Reply $Result 32 'receipt' @('reason')) {
        if (!$ExpectedReason -or $Result.reply.reason -isnot [string] -or
            $Result.reply.reason -cne $ExpectedReason) { return 'invalid' }
        if (![IO.File]::Exists($ReceiptPath)) { return 'refused' }
        return 'conflict'
    }
    if ((Test-Reply $Result 0 'receipt' @('present')) -and $Result.reply.present -is [bool] -and
        !$Result.reply.present -and ![IO.File]::Exists($ReceiptPath)) { return 'absent' }
    if (!(Test-Reply $Result 0 'receipt' @('contents'))) { return 'invalid' }

    try {
        $receipt = ConvertFrom-Json -InputObject ($Utf8.GetString([Convert]::FromBase64String($Result.reply.contents)))
        $keys = @('protocol', 'script_sha256', 'state', 'platform', 'target_verified', 'native_success',
            'chat_verified', 'native_exit_code', 'message_id_sha256', 'send_started_at', 'confirmed_at', 'stage_key',
            'nonce', 'target_digest', 'body_variant', 'body_digest', 'starts_utc', 'cutoff_utc', 'deadline_utc')
        if ($null -ne (Compare-Object $keys @($receipt.PSObject.Properties.Name) -CaseSensitive)) { return 'invalid' }
        if ($Result.reply.contents -cne [Convert]::ToBase64String([IO.File]::ReadAllBytes($ReceiptPath)) -or
            $receipt.state -cne 'accepted' -or $receipt.platform -cne 'weixin' -or $receipt.nonce -cne $Nonce -or
            $receipt.protocol -cne $Protocol -or $receipt.script_sha256 -cne $ApprovedScriptSha256 -or
            $receipt.target_digest -cne $TargetDigest -or $receipt.native_exit_code -ne 0 -or
            $receipt.message_id_sha256 -cne (Get-TextDigest $FixtureMessage)) { return 'invalid' }
        foreach ($name in @('target_verified', 'native_success', 'chat_verified')) {
            if ($receipt.$name -isnot [bool] -or !$receipt.$name) { return 'invalid' }
        }
        foreach ($name in @('stage_key', 'body_variant', 'body_digest', 'starts_utc', 'cutoff_utc', 'deadline_utc')) {
            if ($receipt.$name -cne $Job[$name]) { return 'invalid' }
        }
        $stampFormat = '\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z\z'
        if ($receipt.send_started_at -cnotmatch $stampFormat -or $receipt.confirmed_at -cnotmatch $stampFormat) {
            return 'invalid'
        }
        $starts = [DateTimeOffset]::Parse($receipt.starts_utc, $Culture)
        $started = [DateTimeOffset]::Parse($receipt.send_started_at, $Culture)
        $confirmed = [DateTimeOffset]::Parse($receipt.confirmed_at, $Culture)
        $cutoff = [DateTimeOffset]::Parse($receipt.cutoff_utc, $Culture)
        $deadline = [DateTimeOffset]::Parse($receipt.deadline_utc, $Culture)
        if ($starts -le $started -and $started -le $confirmed -and $confirmed -lt $cutoff -and $cutoff -le $deadline) {
            return 'accepted'
        }
    } catch {}
    return 'invalid'
}

function Test-Case($Case) {
    $record = [ordered]@{
        case = $Case.case; variant = $Case.mode; expected_exit_code = $Case.code; observed_exit_code = $null
        expected_send_calls = $Case.calls; observed_send_calls = 0; expected_receipt = $Case.receipt
        receipt = 'unverified'; prepare_exit_code = $null; replay_exit_code = $null; refusal_reason = $null
        completed_at_utc = $null; passed = $false
    }
    $profile = New-Fixture ('case-' + $Case.mode) $Case.mode
    $countPath = [IO.Path]::Combine($profile, 'fx49-native-send-count')
    try {
        $now = [DateTimeOffset]::UtcNow
        $job = [ordered]@{
            stage_key = 'fx49.fixture'; body = $FixtureBody; body_variant = 'primary'
            body_digest = Get-TextDigest $FixtureBody; starts_utc = Format-Time ($now.AddSeconds(-20))
            cutoff_utc = Format-Time ($now.AddMinutes(2)); deadline_utc = Format-Time ($now.AddMinutes(3))
        }
        $dir = [IO.Path]::Combine([string[]]@($profile, 'PolymarketAlphaLab', 'WeixinDelivery', 'jobs', $Nonce))
        $receiptPath = [IO.Path]::Combine($dir, 'receipt.json')
        $prepare = Invoke-Entry $profile 'prepare' $job
        $record.prepare_exit_code = $prepare.exit_code
        if (!(Test-Reply $prepare 0 'prepare' @('prepared')) -or
            $prepare.reply.prepared -isnot [bool] -or !$prepare.reply.prepared) { return $record }
        if ((Get-PathDigest ([IO.Path]::Combine($dir, 'note.txt'))) -cne $job.body_digest) { return $record }

        if ($Case.mode -ceq 'body-changed') {
            [IO.File]::WriteAllText([IO.Path]::Combine($dir, 'note.txt'), 'changed fixture note', $Utf8)
        }
        if ($Case.mode -ceq 'expired') {
            $metadataPath = [IO.Path]::Combine($dir, 'job.json')
            $metadata = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($metadataPath, $Utf8))
            $metadata.cutoff_utc = Format-Time ([DateTimeOffset]::UtcNow)
            [IO.File]::WriteAllText($metadataPath, (ConvertTo-Json -InputObject $metadata -Compress), $Utf8)
        }

        $send = Invoke-Entry $profile 'send' @{ send_budget_seconds = 12 }
        $record.observed_exit_code = $send.exit_code
        $receipt = Invoke-Entry $profile 'receipt' @{ receipt_generation = 'fixed' }
        $expectedReason = $Case['reason']
        $record.receipt = Get-ReceiptState $receipt $job $receiptPath $expectedReason
        $fields = switch ($Case.code) { 0 { 'sent' } 32 { 'reason' } }
        if (!(Test-Reply $send $Case.code 'send' @($fields)) -or $record.receipt -cne $Case.receipt) { return $record }
        if ($Case.code -eq 0 -and ($send.reply.sent -isnot [bool] -or !$send.reply.sent)) { return $record }
        if ($Case.code -eq 32) {
            if ($send.reply.reason -isnot [string] -or $send.reply.reason -cne $expectedReason) { return $record }
            $record.refusal_reason = $send.reply.reason
        }
        if ($Case.code -in @(0, 32)) {
            if (!(Test-Reply (Invoke-Entry $profile 'cleanup') 0 'cleanup' @()) -or
                [IO.File]::Exists([IO.Path]::Combine($dir, 'note.txt')) -or
                [IO.File]::Exists([IO.Path]::Combine($dir, 'job.json'))) { return $record }
            $replay = Invoke-Entry $profile 'send' @{ send_budget_seconds = 12 }
            $record.replay_exit_code = $replay.exit_code
            if (!(Test-Reply $replay 29 'send' @())) { return $record }
            $again = Invoke-Entry $profile 'receipt' @{ receipt_generation = 'fixed' }
            if ((Get-ReceiptState $again $job $receiptPath $expectedReason) -cne $Case.receipt) { return $record }
        }
        $record.passed = $true
        return $record
    } catch {
        return $record
    } finally {
        if ([IO.File]::Exists($countPath)) { $record.observed_send_calls = [IO.File]::ReadAllLines($countPath).Length }
        $record.passed = $record.passed -and ($record.observed_send_calls -eq $Case.calls)
        $record.completed_at_utc = Format-Time ([DateTimeOffset]::UtcNow)
    }
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
    $PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1 -or
    $ApprovedScriptSha256 -cnotmatch '\A[a-f0-9]{64}\z') {
    [Console]::Error.WriteLine('This offline runner requires Windows PowerShell 5.1 and an approved script SHA-256.')
    exit 2
}

if ($PSCmdlet.ParameterSetName -ceq 'Boundary') {
    try {
        $null = Get-ApprovedSource
        if ((Get-PathDigest $ProbeScript) -cne $ApprovedScriptSha256) { exit 2 }
        $tokens = $null
        $parseErrors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($ProbeScript, [ref]$tokens, [ref]$parseErrors)
        $guards = @($ast.FindAll({
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Assert-Window'
        }, $false))
        if ($parseErrors.Count -ne 0 -or $guards.Count -ne 1) { exit 2 }
        # Exercise the pure guard from the hash-matched source with identical UTC values.
        . ([scriptblock]::Create($guards[0].Extent.Text))
        $cutoff = [DateTimeOffset]::UtcNow
        Assert-Window @{ starts = $cutoff.AddSeconds(-1); cutoff = $cutoff } $cutoff 0
    } catch {}
    exit 2
}

$evidence = [ordered]@{
    protocol = 'fx49-offline-acceptance-v1'; engine = $PSVersionTable.PSVersion.ToString()
    script_sha256 = $ApprovedScriptSha256; status = 'failed'; phase = 'staging'; completed_at_utc = $null
    compiler_exit_code = $null; check_exit_code = $null; cutoff_equality_exit_code = $null; cases = @()
}
$RunRoot = $null
try {
    if ((Get-ExecutionPolicy -Scope Process) -ne 'Undefined') { throw 'Process execution policy override is present' }
    $root = Assert-LocalDirectory $Workspace
    $source = Get-ApprovedSource
    $RunRoot = [IO.Path]::Combine($root, 'fx49-offline-' + [Guid]::NewGuid().ToString('N'))
    $install = [IO.Path]::Combine($RunRoot, 'isolated install')
    $null = [IO.Directory]::CreateDirectory($install)
    $StagedScript = [IO.Path]::Combine($install, $ScriptName)
    [IO.File]::Copy($source, $StagedScript, $false)
    if ((Get-PathDigest $StagedScript) -cne $ApprovedScriptSha256) { throw 'Staged script changed' }

    $evidence.phase = 'compile-substitute'
    $Engine = [IO.Path]::Combine([string[]]@($env:SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'))
    $compiler = [IO.Path]::Combine([Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory(), 'csc.exe')
    $hermes = [IO.Path]::Combine($install, 'hermes.exe')
    $substitute = [IO.Path]::Combine($PSScriptRoot, 'HermesSubstitute.cs')
    $compilerArgs = @('/nologo', '/noconfig', '/target:exe', '/warnaserror+', "/out:$hermes", $substitute)
    $compiled = Invoke-LocalProcess $compiler $compilerArgs $RunRoot
    $evidence.compiler_exit_code = $compiled.exit_code
    if ($compiled.exit_code -ne 0 -or ![IO.File]::Exists($hermes)) { throw 'Local substitution compiler failed' }
    $TargetDigest = Get-TextDigest ('weixin:' + $FixtureId)
    $binding = ConvertTo-Json -InputObject @{ hermes_path = $hermes; target_digest = $TargetDigest } -Compress
    [IO.File]::WriteAllText([IO.Path]::Combine($install, 'weixin-binding.json'), $binding, $Utf8)

    $evidence.phase = 'entry-check'
    $preflight = Invoke-Entry (New-Fixture 'preflight' 'success') 'check'
    $evidence.check_exit_code = $preflight.exit_code
    if (!(Test-Reply $preflight 0 'check' @('hostname_matches', 'target_verified')) -or
        $preflight.reply.hostname_matches -isnot [bool] -or $preflight.reply.target_verified -isnot [bool] -or
        !$preflight.reply.hostname_matches -or !$preflight.reply.target_verified) { throw 'Fixed File entry check failed' }

    $evidence.phase = 'cutoff-equality'
    $boundaryArgs = @('-NoProfile', '-NonInteractive', '-File', $PSCommandPath,
        '-ApprovedScriptSha256', $ApprovedScriptSha256, '-ProbeScript', $StagedScript)
    $boundary = Invoke-LocalProcess $Engine $boundaryArgs $RunRoot
    $evidence.cutoff_equality_exit_code = $boundary.exit_code
    if ($boundary.exit_code -ne 22 -or $boundary.stderr_present -or $boundary.stdout) {
        throw 'Exact cutoff guard check failed'
    }

    $evidence.phase = 'cases'
    $cases = @(
        @{ case = 'success'; mode = 'success'; code = 0; calls = 1; receipt = 'accepted' }
        @{ case = 'target-not-unique'; mode = 'zero'; code = 24; calls = 0; receipt = 'absent' }
        @{ case = 'target-not-unique'; mode = 'multiple'; code = 24; calls = 0; receipt = 'absent' }
        @{ case = 'target-not-unique'; mode = 'group'; code = 24; calls = 0; receipt = 'absent' }
        @{ case = 'target-digest'; mode = 'wrong-digest'; code = 25; calls = 0; receipt = 'absent' }
        @{ case = 'body-digest'; mode = 'body-changed'; code = 26; calls = 0; receipt = 'absent' }
        @{ case = 'window-closed'; mode = 'expired'; code = 22; calls = 0; receipt = 'absent' }
        @{ case = 'native-refusal'; mode = 'platform-refusal'; code = 32; calls = 1; receipt = 'refused'
            reason = 'hermes_platform_write_denied' }
        @{ case = 'native-refusal'; mode = 'tool-refusal'; code = 32; calls = 1; receipt = 'refused'
            reason = 'zcode_tool_permission_denied' }
        @{ case = 'native-refusal'; mode = 'owner-refusal'; code = 32; calls = 1; receipt = 'refused'
            reason = 'weixin_owner_message_required' }
    )
    foreach ($case in $cases) { $evidence.cases += (Test-Case $case) }
    if (@($evidence.cases | Where-Object { !$_.passed }).Count -eq 0 -and
        (Get-PathDigest $StagedScript) -ceq $ApprovedScriptSha256) {
        $evidence.status = 'passed'
    }
} catch {
    # Retain phase and desensitized observations, never raw diagnostics.
} finally {
    $evidence.completed_at_utc = Format-Time ([DateTimeOffset]::UtcNow)
    $report = ConvertTo-Json -InputObject $evidence -Depth 8
    if ($null -ne $RunRoot -and [IO.Directory]::Exists($RunRoot)) {
        [IO.File]::WriteAllText([IO.Path]::Combine($RunRoot, 'fx49-offline-evidence.json'), $report, $Utf8)
    }
    [Console]::WriteLine($report)
}
if ($evidence.status -ceq 'passed') { exit 0 }
exit 1
