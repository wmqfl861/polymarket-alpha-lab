# Windows PowerShell 5.1. Use only after the authorized install and worker start.
# Sends only an invalid synthetic JSON object. Invoke-PaperWeixin refuses it before any external client.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$exe = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'PolymarketAlphaLab\WeixinDelivery\PaperWeixinRelay.exe'
$nonce = '0123456789abcdef0123456789abcdef'
$fixedScript = Join-Path ([IO.Path]::GetDirectoryName($exe)) 'Invoke-PaperWeixin.ps1'
$systemShell = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
$expectedCommand = '"{0}" -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "{1}" {2}' -f $systemShell,$fixedScript,$nonce
$workers = @(Get-CimInstance Win32_Process -Filter "Name='PaperWeixinRelay.exe'" -Property ProcessId,ExecutablePath,CommandLine |
    Where-Object { [string]::Equals($_.ExecutablePath,$exe,[StringComparison]::OrdinalIgnoreCase) -and $_.CommandLine -cmatch '\s--serve$' })
if ($workers.Count -ne 1) { throw 'Expected one fixed worker at the installation path' }
$childFilter = "ParentProcessId=$($workers[0].ProcessId) AND Name='powershell.exe'"
$null = Get-CimInstance Win32_Process -Filter $childFilter -Property ExecutablePath,CommandLine

function Check-Refusal([string]$Arguments, [string]$Body, [int]$Expected, [switch]$ObserveChild) {
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $exe
    $start.Arguments = $Arguments
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $process = [Diagnostics.Process]::Start($start)
    try {
        $out = $process.StandardOutput.ReadToEndAsync()
        $err = $process.StandardError.ReadToEndAsync()
        $inputBytes = [Text.Encoding]::ASCII.GetBytes($Body)
        try {
            $process.StandardInput.BaseStream.Write($inputBytes, 0, $inputBytes.Length)
        } catch [IO.IOException] {
            if ($Expected -ne 70) { throw }
        } finally { $process.StandardInput.BaseStream.Close() }
        if ($ObserveChild) {
            $observed = $false
            do {
                $children = @(Get-CimInstance Win32_Process -Filter $childFilter -Property ExecutablePath,CommandLine)
                foreach ($child in $children) {
                    if (![string]::Equals($child.ExecutablePath,$systemShell,[StringComparison]::OrdinalIgnoreCase) -or
                        $child.CommandLine -cne $expectedCommand) { throw 'Actual child executable or full argument vector differed' }
                    $observed = $true
                }
            } while (!$observed -and !$process.HasExited -and $clock.ElapsedMilliseconds -lt 7500)
            if (!$observed) { throw 'Actual fixed PowerShell process was not observed' }
        }
        $left = [Math]::Max(0, 8000 - [int]$clock.ElapsedMilliseconds)
        if (!$process.WaitForExit($left)) { $process.Kill(); throw 'Relay exceeded the shortest existing HC deadline' }
        if ($process.ExitCode -ne $Expected -or $out.Result.Length -ne 0 -or $err.Result.Length -ne 0) {
            throw 'Unexpected fixed relay result'
        }
    } finally { $process.Dispose() }
}

# Unknown or additional arguments are rejected by the client, before an IPC request.
Check-Refusal 'UPPERCASE' '{}' 70
Check-Refusal ($nonce + ' extra') '{}' 70
Check-Refusal $nonce '' 70
Check-Refusal $nonce ('x' * 20481) 70

# The native client must reach the original script and preserve its empty, exact exit 21.
# No target, message, account credential, external client or actual claim can be accessed by this envelope.
Check-Refusal $nonce '{}' 21 -ObserveChild
Check-Refusal $nonce '{"program":"ignored","argv":["never-executed"],"environment":{}}' 21
Check-Refusal $nonce '{}' 21

# A caller with the same user rights but the wrong executable cannot submit a raw pipe request.
$pipe = [IO.Pipes.NamedPipeClientStream]::new('.', 'PolymarketAlphaLab.FixedWeixin.v1',
                                              [IO.Pipes.PipeDirection]::InOut, [IO.Pipes.PipeOptions]::Asynchronous)
try {
    $pipe.Connect(1000)
    $buffer = [byte[]]::new(1)
    $read = $pipe.ReadAsync($buffer, 0, 1)
    try {
        if (!$read.Wait(3000) -or $read.Result -ne 0) { throw 'Untrusted client received a reply' }
    } catch [AggregateException] {
        if ($_.Exception.GetBaseException() -isnot [IO.IOException]) { throw }
    }
} catch [UnauthorizedAccessException] {
    # Windows can reject the generic pipe open at the DACL before a connection exists.
} finally { $pipe.Dispose() }
Check-Refusal $nonce '{}' 21

Write-Output 'fixed local relay offline acceptance passed'
