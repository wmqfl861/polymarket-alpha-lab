using namespace System.Management.Automation.Language

Set-StrictMode -Version 3
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$probeArgs = @($args)
$entrySha = 'e86beca8f4073da75a8bc1cd3aeef3e85230417c0b19f9424432e79baa7998c6'

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

function Get-Condition([IfStatementAst]$Guard) {
    if ($Guard.Clauses.Count -ne 1 -or $null -ne $Guard.ElseClause) { throw 'FX-49 guard AST invalid' }
    $condition = $Guard.Clauses[0].Item1
    if ($condition -isnot [PipelineAst] -or $condition.PipelineElements.Count -ne 1 -or
        $condition.PipelineElements[0] -isnot [CommandExpressionAst]) { throw 'FX-49 guard AST invalid' }
    return $condition.PipelineElements[0].Expression
}

function Split-Or([ExpressionAst]$Expression) {
    if ($Expression -is [BinaryExpressionAst] -and $Expression.Operator -eq [TokenKind]::Or) {
        Split-Or $Expression.Left
        Split-Or $Expression.Right
    } else { $Expression }
}

function Get-Status([bool]$Passed) {
    if ($Passed) { return 'pass' }
    return 'fail'
}

function Test-Guard([scriptblock]$Reject) {
    $rejected = & $Reject
    if ($rejected -isnot [bool]) { throw 'FX-49 guard result invalid' }
    return (!$rejected)
}

function Observe-Frame([byte[]]$Bytes, [string]$Side, $Facts) {
    # Give Read-FlatObject the full buffer. Repeat strict decoding only to locate a rejection.
    try {
        $Request = Read-FlatObject $Bytes
    } catch {
        try { $null = $Utf8.GetString($Bytes) }
        catch [Text.DecoderFallbackException] {
            $Facts["${Side}_decode"] = 'fail'
            return
        }
        $Facts["${Side}_decode"] = 'pass'
        $Facts["${Side}_flat_object"] = 'fail'
        return
    }
    $Facts["${Side}_decode"] = 'pass'
    $Facts["${Side}_flat_object"] = 'pass'
    foreach ($axis in $guards.Keys) {
        $key = if ($axis -clike 'hostname_*') { 'hostname' } else { $axis }
        if (!$Request.ContainsKey($key) -or ($axis -ceq 'hostname_equal' -and $Request[$key] -isnot [string])) {
            continue
        }
        $Facts["${Side}_$axis"] = Get-Status (Test-Guard $guards[$axis])
    }
    $null = . $bindKeys
    $Facts["${Side}_keys"] = Get-Status (Test-Guard $rejectKeys)
    if ($Request.ContainsKey('purpose')) {
        $null = . $bindPurpose
        $Facts["${Side}_purpose"] = Get-Status ((Test-Guard $rejectPurpose) -and $purpose -ceq $checkName)
    }
}

function Get-Prefix([byte[]]$Bytes) {
    if ($Bytes.Length -eq 0) { return @{ kind = 'unknown'; size = 0 } }
    $first = [BitConverter]::ToString($Bytes, 0, [Math]::Min($Bytes.Length, 4))
    switch -Regex -CaseSensitive ($first) {
        '\AFF-FE-00-00' { return @{ kind = 'utf32le-bom'; size = 4 } }
        '\A00-00-FE-FF' { return @{ kind = 'utf32be-bom'; size = 4 } }
        '\AEF-BB-BF' { return @{ kind = 'utf8-bom'; size = 3 } }
        '\AFF-FE' { return @{ kind = 'utf16le-bom'; size = 2 } }
        '\AFE-FF' { return @{ kind = 'utf16be-bom'; size = 2 } }
        '\A7B(?:-|\z)' { return @{ kind = 'none'; size = 0 } }
        default { return @{ kind = 'other'; size = 0 } }
    }
}

function Test-Bytes([byte[]]$Received, [byte[]]$Expected, [int]$Offset) {
    if ($Received.Length -ne $Expected.Length + $Offset) { return $false }
    for ($index = 0; $index -lt $Expected.Length; $index++) {
        if ($Received[$index + $Offset] -ne $Expected[$index]) { return $false }
    }
    return $true
}

$facts = [ordered]@{
    oracle = 'unknown'; bytes = 'unknown'; prefix = 'unknown'; arg_count = 'unknown'
    nonce_shape = 'unknown'; nonce_equal = 'unknown'
}
$frameAxes = @('decode', 'flat_object', 'protocol', 'script_sha256', 'target_digest',
    'hostname_shape', 'hostname_equal', 'purpose', 'keys')
foreach ($side in @('actual', 'canonical')) {
    foreach ($axis in $frameAxes) { $facts["${side}_$axis"] = 'unknown' }
}

$code = 2
try {
    if ($env:GITHUB_ACTIONS -cne 'true' -or $env:RUNNER_ENVIRONMENT -cne 'github-hosted' -or
        [Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or $PSVersionTable.PSEdition -cne 'Desktop' -or
        $PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1) {
        throw 'FX-49 engine unavailable'
    }
    $workspace = [IO.Directory]::GetParent($PSScriptRoot).Parent.FullName
    $sourcePath = [IO.Path]::Combine($workspace, 'Invoke-PaperWeixin.ps1.blob')
    $sourceBytes = [IO.File]::ReadAllBytes($sourcePath)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $digest = [BitConverter]::ToString($sha.ComputeHash($sourceBytes)).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
    if ($sourceBytes.Length -ne 35487 -or $digest -cne $entrySha) { throw 'FX-49 reader source invalid' }

    $decoder = [Text.UTF8Encoding]::new($false, $true)
    $tokens = $null
    $parseErrors = $null
    $tree = [Parser]::ParseInput($decoder.GetString($sourceBytes), [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count -ne 0 -or $null -eq $tree.EndBlock) { throw 'FX-49 reader AST invalid' }
    $approved = @(
        'Set-StrictMode', 'ErrorActionPreference', 'WarningPreference', 'ProgressPreference',
        'Utf8', 'Invariant', 'Protocol', 'Hex64', 'Hex32', 'StageKey', 'JsonProperty', 'JsonEscapes', 'Unescape',
        'Read-FlatObject', 'Test-Keys', 'Test-String', 'Test-ExactString', 'Read-Bounded', 'Get-Sha256', 'Get-BytesSha256'
    )
    $selected = @($tree.EndBlock.Statements | Where-Object { (Get-SliceName $_) -cin $approved })
    $names = @($selected | ForEach-Object { Get-SliceName $_ })
    if (($names -join ',') -cne ($approved -join ',')) { throw 'FX-49 reader AST invalid' }

    $main = @($tree.EndBlock.Statements | Where-Object { $_ -is [TryStatementAst] })
    if ($main.Count -ne 1 -or $main[0].Body.Statements.Count -lt 3) { throw 'FX-49 entry AST invalid' }
    $body = $main[0].Body.Statements
    if ($body[0] -isnot [IfStatementAst] -or
        $body[0].Extent.Text -cne 'if ($args.Count -ne 1 -or !(Test-String $args[0] $Hex32)) { exit 21 }' -or
        $body[2] -isnot [TryStatementAst]) { throw 'FX-49 entry AST invalid' }
    $parse = $body[2].Body.Statements
    if ($parse.Count -lt 7 -or
        $parse[0].Extent.Text -cne '$Request = Read-FlatObject (Read-Bounded ([Console]::OpenStandardInput()) 20480)' -or
        $parse[2].Extent.Text -cne '$requestKeys = @(''protocol'', ''purpose'', ''hostname'', ''script_sha256'', ''target_digest'')' -or
        $parse[3].Extent.Text -cne '$purpose = $Request[''purpose'']' -or
        $parse[5] -isnot [SwitchStatementAst]) { throw 'FX-49 check AST invalid' }

    # Bind the original terms separately so an early rejection leaves the other observable axes available.
    $expectedTerms = [ordered]@{
        protocol = '!(Test-ExactString $Request[''protocol''] $Protocol)'
        script_sha256 = '!(Test-String $Request[''script_sha256''] $Hex64)'
        target_digest = '!(Test-String $Request[''target_digest''] $Hex64)'
        hostname_shape = '!(Test-String $Request[''hostname''] $StageKey)'
        hostname_equal = '$Request[''hostname''] -ine [Environment]::MachineName'
    }
    $terms = @(Split-Or (Get-Condition $parse[1]))
    if ($terms.Count -ne $expectedTerms.Count) { throw 'FX-49 check AST invalid' }
    $guards = [ordered]@{}
    $index = 0
    foreach ($axis in $expectedTerms.Keys) {
        $text = $terms[$index].Extent.Text
        if ($text -cne $expectedTerms[$axis]) { throw 'FX-49 check AST invalid' }
        $guards.Add($axis, [scriptblock]::Create($text))
        $index++
    }
    $purposeCondition = Get-Condition $parse[4]
    $keyCondition = Get-Condition $parse[6]
    $purposeSwitch = $parse[5]
    $checkCase = @($purposeSwitch.Clauses | Where-Object {
        $_.Item1 -is [StringConstantExpressionAst] -and $_.Item1.Value -ceq 'check'
    })
    if ($purposeCondition.Extent.Text -cne '$purpose -isnot [string]' -or
        $keyCondition.Extent.Text -cne '!(Test-Keys $Request $requestKeys)' -or
        $purposeSwitch.Flags -ne [SwitchFlags]::CaseSensitive -or
        $purposeSwitch.Condition.Extent.Text -cnotin @('$purpose', '($purpose)') -or
        $checkCase.Count -ne 1 -or $checkCase[0].Item2.Statements.Count -ne 0) {
        throw 'FX-49 check AST invalid'
    }
    $bindKeys = [scriptblock]::Create($parse[2].Extent.Text)
    $bindPurpose = [scriptblock]::Create($parse[3].Extent.Text)
    $rejectKeys = [scriptblock]::Create($keyCondition.Extent.Text)
    $rejectPurpose = [scriptblock]::Create($purposeCondition.Extent.Text)
    $checkName = $checkCase[0].Item1.Value
    foreach ($statement in $selected) {
        $null = . ([scriptblock]::Create($statement.Extent.Text))
    }

    $facts.arg_count = switch ($probeArgs.Count) { 0 { 'zero' } 1 { 'one' } default { 'multiple' } }
    if ($probeArgs.Count -eq 1) {
        $facts.nonce_shape = Get-Status (Test-String $probeArgs[0] $Hex32)
        $facts.nonce_equal = Get-Status (Test-ExactString $probeArgs[0] 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
    }

    $canonical = [ordered]@{
        protocol = $Protocol; purpose = $checkName; hostname = [Environment]::MachineName
        script_sha256 = $entrySha; target_digest = 'e53f63885ae627c2a4da5913bb5b7a7d26bffa415549f9f5b9a482bbca5b9c4c'
    }
    $expected = $Utf8.GetBytes((ConvertTo-Json -InputObject $canonical -Compress))
    $lengthText = $env:PAL_FX49_FRAME_LENGTH
    $expectedSha = $env:PAL_FX49_FRAME_SHA256
    if ($lengthText -cnotmatch '\A[1-9][0-9]{0,4}\z' -or $expectedSha -cnotmatch $Hex64 -or
        $expected.Length -gt 20480 -or $expected.Length -ne [int]::Parse($lengthText, $Invariant) -or
        (Get-BytesSha256 $expected) -cne $expectedSha) { throw 'FX-49 canonical frame unavailable' }
    $facts.oracle = 'pass'

    $received = $null
    try { $received = Read-Bounded ([Console]::OpenStandardInput()) 20480 } catch {}
    if ($null -ne $received) {
        $prefix = Get-Prefix $received
        $facts.prefix = $prefix.kind
        if (Test-Bytes $received $expected 0) { $facts.bytes = 'equal' }
        elseif ($prefix.size -gt 0 -and (Test-Bytes $received $expected $prefix.size)) { $facts.bytes = 'prefix-only' }
        else { $facts.bytes = 'different' }
        Observe-Frame $received 'actual' $facts
    }
    Observe-Frame $expected 'canonical' $facts

    $actualAccepted = $facts.arg_count -ceq 'one' -and $facts.nonce_shape -ceq 'pass'
    foreach ($axis in $frameAxes) {
        $actualAccepted = $actualAccepted -and $facts["actual_$axis"] -ceq 'pass'
    }
    if ($actualAccepted) { $code = 0 } else { $code = 21 }
} catch {}

try {
    [Console]::Write((ConvertTo-Json -InputObject $facts -Compress))
    exit $code
} catch { exit 2 }
