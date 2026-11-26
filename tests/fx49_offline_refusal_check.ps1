# Isolate the rehearsal's case and receipt checks from its Windows process launcher.
param([string]$Runner, [string]$Scratch)
Set-StrictMode -Version 3
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Runner, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Rehearsal parse failed' }
foreach ($name in 'Test-Reply', 'Get-ReceiptState', 'Test-Case', 'Get-TextDigest', 'Format-Time') {
    $fn = @($ast.FindAll({ param($n)
        $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name
    }, $false))
    if ($fn.Count -ne 1) { throw 'Expected rehearsal function missing' }
    . ([scriptblock]::Create($fn[0].Extent.Text))
}

$Utf8 = [Text.UTF8Encoding]::new($false, $true)
$Culture = [Globalization.CultureInfo]::InvariantCulture
$ApprovedScriptSha256 = 'a' * 64
$Protocol = 'paper-weixin-fixed-v1'
$Nonce = 'a' * 32
$FixtureBody = 'offline acceptance fixture'
$expected = 'hermes_platform_write_denied'
$other = 'zcode_tool_permission_denied'
$case = @{ case = 'native-refusal'; mode = 'platform-refusal'; code = 32; calls = 1
    receipt = 'refused'; reason = $expected }

function New-Fixture { return $profileRoot }
function Get-PathDigest { return (Get-TextDigest $FixtureBody) }
function Invoke-Entry([string]$Profile, [string]$Purpose) {
    $reply = @{ protocol = $Protocol; purpose = $Purpose; code = 0; script_sha256 = $ApprovedScriptSha256 }
    switch ($Purpose) {
        'prepare' { $reply.prepared = $true }
        'send' {
            $script:sendRequests++
            if ($sendRequests -eq 1 -or $variant -ceq 'sent-twice') {
                [IO.File]::AppendAllText((Join-Path $Profile 'fx49-native-send-count'), ("1" + [char]10))
            }
            if ($sendRequests -eq 1) {
                $reply.code = 32
                $reply.reason = $expected
                if ($variant -ceq 'send-array') { $reply.reason = @($expected) }
            } else {
                $reply.code = if ($variant -ceq 'wrong-retry-code') { 22 } else { 29 }
            }
        }
        'receipt' {
            $script:reads++
            $reply.code = 32
            $reply.reason = $expected
            if (($variant -ceq 'wrong-first-read' -and $reads -eq 1) -or
                ($variant -ceq 'wrong-retained-read' -and $reads -eq 2)) { $reply.reason = $other }
            if ($variant -ceq 'retained-array' -and $reads -eq 2) { $reply.reason = @($expected) }
        }
        'cleanup' {}
        default { throw 'Unexpected test operation' }
    }
    $nativeExit = [int]$reply.code
    if ($Purpose -ceq 'receipt' -and $reads -eq 2) {
        switch ($variant) {
            'intact' { $reply.code = [long]$reply.code }
            'code-as-string' { $reply.code = [string]$reply.code }
            'protocol-array' { $reply.protocol = @($Protocol) }
            'purpose-array' { $reply.purpose = @($Purpose) }
            'digest-array' { $reply.script_sha256 = @($ApprovedScriptSha256) }
            'extra-field' { $reply['extra'] = 'fixture' }
        }
    }
    $hasOutput = $nativeExit -in @(0, 32) -or ($variant -ceq 'retry-with-output' -and $nativeExit -eq 29)
    return @{ reply = [pscustomobject]$reply; exit_code = $nativeExit
        stdout = $hasOutput; stderr_present = $false; stdin_failed = $false }
}

$results = [ordered]@{}
foreach ($variant in 'intact', 'wrong-first-read', 'wrong-retained-read', 'retained-array',
                      'send-array', 'code-as-string', 'protocol-array', 'purpose-array', 'digest-array',
                      'extra-field', 'wrong-retry-code', 'retry-with-output', 'sent-twice') {
    $profileRoot = Join-Path $Scratch $variant
    $null = [IO.Directory]::CreateDirectory($profileRoot)
    $script:reads = 0
    $script:sendRequests = 0
    $record = Test-Case $case
    $results[$variant] = @{ passed = $record.passed; reads = $reads; calls = $record.observed_send_calls }
}
[Console]::WriteLine((ConvertTo-Json -InputObject $results -Depth 3 -Compress))
