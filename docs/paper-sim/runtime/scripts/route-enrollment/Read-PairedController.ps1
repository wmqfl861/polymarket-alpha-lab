$ErrorActionPreference = 'Stop'
function Proof([string]$Id) {
  if ($Id -cnotmatch '^[a-f0-9]{32}$') {throw 'invalid_public_identity'}
  $text = [Text.Encoding]::ASCII.GetBytes('CHALLENGE' + ':' + $Id)
  $hash = [Security.Cryptography.SHA256]::Create()
  try {return [Convert]::ToBase64String($hash.ComputeHash($text))}
  finally {$hash.Dispose()}
}
$result = @{schema=1;status='unconfirmed'}
try {
  $client = @(Get-Command hc.exe -CommandType Application -ErrorAction SilentlyContinue)
  if ($client.Count -ne 1) { $result.status='unique_controller_executable_unavailable' }
  else {
    $binary = $client[0].Source
    $running = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        try { $_.Path -and [string]::Equals($_.Path,$binary,[StringComparison]::OrdinalIgnoreCase) } catch { $false }
    })
    $own = (& $binary server status --json 2>$null | Out-String | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0 -or -not $own.core.id) {throw 'missing_metadata'}
    $peers = (& $binary machine ls --json 2>$null | Out-String | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0 -or $null -eq $peers.machines) {throw 'missing_metadata'}
    $seen=@()
    foreach($peer in $peers.machines) {
       $seen+=@{proof=(Proof $peer.id); connected=([string]$peer.state -eq 'connected');
                attests_pairing=($null -ne $peer.PSObject.Properties['paired_at'] -or $true -eq $peer.trusted -or $true -eq $peer.paired)}
    }
    $result=@{schema=1;status='controller_read'; own=(Proof $own.core.id); peers=@($seen); controller_process_seen=($running.Count -gt 0)}
  }
} catch { $result=@{schema=1;status='public_metadata_unavailable'} }
[Console]::Out.Write(($result | ConvertTo-Json -Compress -Depth 4))
