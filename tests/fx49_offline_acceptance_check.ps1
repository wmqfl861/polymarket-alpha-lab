# Exercise the offline verifier with synthetic receipts. No native process is invoked.
param([string]$Runner, [string]$Scratch)
Set-StrictMode -Version 3
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Runner, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Rehearsal parse failed' }
foreach ($name in 'Test-Reply', 'Read-AcceptedReceiptFields', 'Get-ReceiptState', 'Get-TextDigest') {
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
$TargetDigest = 'b' * 64
$Nonce = 'c' * 32
$FixtureMessage = 'offline-accepted-fixture'
$job = @{ stage_key = 'demo'; body_variant = 'primary'; body_digest = 'd' * 64
    starts_utc = '2026-11-26T10:00:00.000000Z'; cutoff_utc = '2026-11-26T10:01:00.000000Z'
    deadline_utc = '2026-11-26T10:02:00.000000Z' }
$path = Join-Path $Scratch 'synthetic-receipt.json'
$states = [ordered]@{}
foreach ($variant in 'intact', 'number-string', 'bool-number', 'state-array', 'stage-array',
                      'duplicate-state', 'fractional-exit', 'timestamp-shape', 'cutoff-equality',
                      'body-mismatch', 'contents-array', 'file-mismatch', 'missing-flag', 'integer-flag', 'false-flag') {
    $receipt = $job.Clone()
    $fields = @{ protocol = $Protocol; script_sha256 = $ApprovedScriptSha256; state = 'accepted'
        platform = 'weixin'; target_verified = $true; native_success = $true; chat_verified = $true
        native_exit_code = 0; message_id_sha256 = Get-TextDigest $FixtureMessage; nonce = $Nonce
        target_digest = $TargetDigest; send_started_at = '2026-11-26T10:00:00.100000Z'
        confirmed_at = '2026-11-26T10:00:01.120000Z' }
    foreach ($k in $fields.Keys) { $receipt.Add($k, $fields[$k]) }
    switch ($variant) {
        'number-string' { $receipt.native_exit_code = '0' }
        'bool-number' { $receipt.native_exit_code = $false }
        'missing-flag' { $receipt.Remove('native_success') }
        'integer-flag' { $receipt.chat_verified = 1 }
        'false-flag' { $receipt.target_verified = $false }
        'state-array' { $receipt.state = @('accepted') }
        'stage-array' { $receipt.stage_key = @('demo') }
        'timestamp-shape' { $receipt.send_started_at = '2026-11-26T10:00:00.1Z' }
        'cutoff-equality' { $receipt.confirmed_at = $job.cutoff_utc }
        'body-mismatch' { $receipt.body_digest = 'e' * 64 }
    }
    $json = ConvertTo-Json -InputObject $receipt -Compress
    if ($variant -ceq 'duplicate-state') { $json = '{"state":"ignored",' + $json.Substring(1) }
    if ($variant -ceq 'fractional-exit') { $json = $json.Replace('"native_exit_code":0','"native_exit_code":0.0') }
    $bytes = $Utf8.GetBytes($json)
    [IO.File]::WriteAllBytes($path, $bytes)
    $contents = [Convert]::ToBase64String($bytes)
    if ($variant -ceq 'contents-array') { $contents = @($contents) }
    if ($variant -ceq 'file-mismatch') { [IO.File]::AppendAllText($path, ' ', $Utf8) }
    $result = @{ exit_code = 0; stderr_present = $false; stdin_failed = $false; stdout = 'fixture'
        reply = [pscustomobject]@{ protocol = $Protocol; purpose = 'receipt'; code = 0
            script_sha256 = $ApprovedScriptSha256; contents = $contents } }
    $states[$variant] = Get-ReceiptState $result $job $path ''
}
[Console]::WriteLine((ConvertTo-Json -InputObject $states -Compress))
