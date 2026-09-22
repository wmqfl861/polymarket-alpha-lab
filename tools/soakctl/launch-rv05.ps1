# RV-05 corrected-72h one-shot launcher (PAL_CORRECTIVE_LONGTASK_20260921_V3).
# N5 parameterized copy (PAL_RV05_CAPACITY_20260921, node N5); distributable
# placement at tools/soakctl/ (PAL_RV05_CLOSURE_20260922, fix wave).
#
# Deltas vs the frozen V3 launcher (all additive; launch still one-shot):
#   - receipt/claim/preflight-report/work-temp locations are injectable via
#     spec.paths (receipt / claim / preflight_last / work_temp); defaults
#     remain next to this script exactly as before;
#   - claim-first one-shot protocol: a unique launch claim is persisted
#     (atomically) AFTER the preflight GO gate and campaign-absent gate pass
#     and BEFORE any directory creation or process start. If a receipt never
#     follows the claim (crash between launch and receipt write, or a
#     half-written/unreadable receipt), the launch state is LOST-ACK /
#     UNKNOWN: the script refuses to launch again (exit 6), keeps the claim,
#     performs NO automatic second launch, NO claim recovery and NO receipt
#     rewrite - manual adjudication is required;
#   - receipt schema bumped to pal-rv05-launch-receipt-v2 (adds claim_id,
#     spec_path, frozen identity echo, parameter-source fields); old v1
#     receipts (dry_run=false) still block a second launch;
#   - closeout follow-up command deadline/label injectable via
#     gates.closeout_deadline_utc / launch.closeout_label (defaults = the
#     frozen V3 values);
#   - distributable placement delta (fix wave): -SpecPath has NO default (the
#     private host default is gone) and is REQUIRED; a RELATIVE -SpecPath is
#     resolved against this repository's root (script dir's grandparent), so
#     `tools/soakctl/launch-spec-...json` works from any working directory.
#     No host/personal path ships in this file.
#
# Contract (unchanged from V3):
#   1. Runs the READ-ONLY preflight (preflight-rv05.py) first.
#   2. Launches ONLY if the preflight verdict is GO (every check PASS).
#   3. -DryRun performs and prints everything except the actual launch:
#      the preflight still runs (read-only), the exact launch command,
#      campaign paths, STOP path and receipt path are printed, nothing is
#      created under the planned campaign root, no claim is written and no
#      process is started.
#   4. Records PID, exact launch command, STOP path and a launch receipt
#      JSON. One shot: a valid receipt with dry_run=false (v1 or v2), or a
#      claim without such a receipt (lost-ack), refuses any second launch.
#
# This script never touches the original campaign, the original root or any
# historical PID; it does not stop, kill or scan processes.
#
# Exit codes: 0 launched / dry-run OK; 2 preflight not GO or no report, or
# invalid invocation (-SpecPath missing); 3 duplicate launch refused; 4
# planned campaign root exists; 5 launched process exited within 8s; 6
# lost-ack (UNKNOWN launch state) refused.

param(
    [switch]$DryRun,
    # REQUIRED: the bound launch spec. No default is assumed (nothing in
    # this repository is pre-bound); fill a copy of launch-spec-template.json
    # per candidate. Relative paths resolve against the repository root.
    [Parameter(Mandatory = $false)]
    [string]$SpecPath
)

$ErrorActionPreference = 'Stop'

if (-not $SpecPath -or -not $SpecPath.Trim()) {
    Write-Output 'USAGE: launch-rv05.ps1 -SpecPath <bound-launch-spec.json> [-DryRun]'
    Write-Output '  -SpecPath is REQUIRED (no default): fill a copy of'
    Write-Output '  tools/soakctl/launch-spec-template.json per candidate and pass it'
    Write-Output '  explicitly. Relative paths resolve against the repository root.'
    exit 2
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent (Split-Path -Parent $ScriptDir)
if (-not [System.IO.Path]::IsPathRooted($SpecPath)) {
    $SpecPath = Join-Path $RepoRoot $SpecPath
}
$SpecPath = [System.IO.Path]::GetFullPath($SpecPath)
$Spec = Get-Content -LiteralPath $SpecPath -Raw -Encoding UTF8 | ConvertFrom-Json

$Python     = $Spec.paths.python_exe
$Driver     = $Spec.paths.driver_py
$Audit      = $Spec.paths.audit_py
$Closeout   = $Spec.paths.closeout_py
$Config     = $Spec.paths.config
$Manifest   = $Spec.paths.manifest
$Preflight  = $Spec.paths.preflight
$SrcRoot    = $Spec.paths.src_root
$CampaignRoot = $Spec.paths.planned_campaign_root
$CampaignDir  = $Spec.paths.planned_campaign_dir
if ($Spec.paths -and $Spec.paths.PSObject.Properties['work_temp']) {
    $WorkTemp = $Spec.paths.work_temp
} else {
    $WorkTemp = Join-Path $ScriptDir 'work-temp'
}
if ($Spec.paths -and $Spec.paths.PSObject.Properties['receipt']) {
    $ReceiptPath = $Spec.paths.receipt
} else {
    $ReceiptPath = Join-Path $ScriptDir 'launch-receipt-rv05.json'
}
if ($Spec.paths -and $Spec.paths.PSObject.Properties['claim']) {
    $ClaimPath = $Spec.paths.claim
} else {
    $ClaimPath = Join-Path $ScriptDir 'launch-claim-rv05.json'
}
if ($Spec.paths -and $Spec.paths.PSObject.Properties['preflight_last']) {
    $PreflightLast = $Spec.paths.preflight_last
} else {
    $PreflightLast = Join-Path $ScriptDir 'preflight-last.json'
}
if ($Spec.gates -and $Spec.gates.PSObject.Properties['closeout_deadline_utc']) {
    $CloseoutDeadline = $Spec.gates.closeout_deadline_utc
} else {
    $CloseoutDeadline = '2026-09-28T00:36:41Z'
}
if ($Spec.PSObject.Properties['launch'] -and $Spec.launch.PSObject.Properties['closeout_label']) {
    $CloseoutLabel = $Spec.launch.closeout_label
} else {
    $CloseoutLabel = 'rv05-corrected-closeout'
}
$StopPath   = Join-Path $CampaignDir 'stop'

function Get-FileSha256([string]$Path) {
    if ($Path -and (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    }
    return $null
}

# --- one-shot guard (BEFORE preflight; reads receipt/claim evidence only) --
# States:
#   valid receipt with dry_run=false            -> DUPLICATE  (exit 3)
#   receipt present but unreadable/half-written -> LOST-ACK   (exit 6)
#   claim present without a completed receipt   -> LOST-ACK   (exit 6)
# A claim is only ever written by a real (non-dry) launch attempt after every
# gate passes, so its presence means the one shot may already be consumed.
$PriorLaunch = $false
$ReceiptUnreadable = $false
$ReceiptPresent = Test-Path -LiteralPath $ReceiptPath -PathType Leaf
if ($ReceiptPresent) {
    try {
        $Prior = Get-Content -LiteralPath $ReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($Prior.PSObject.Properties['dry_run'] -and -not $Prior.dry_run) {
            $PriorLaunch = $true
        }
    } catch {
        $ReceiptUnreadable = $true
    }
}
$ClaimPresent = Test-Path -LiteralPath $ClaimPath -PathType Leaf
$ClaimUnreadable = $false
if ($ClaimPresent) {
    try {
        $null = Get-Content -LiteralPath $ClaimPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        $ClaimUnreadable = $true
    }
}

if (-not $DryRun) {
    if ($PriorLaunch) {
        Write-Output "REFUSED: a real launch receipt already exists at $ReceiptPath"
        Write-Output 'The corrected 72h is one-shot; no second launch is permitted.'
        exit 3
    }
    if ($ReceiptUnreadable -or $ClaimPresent) {
        Write-Output 'LOST_ACK: launch state UNKNOWN - refusing to launch again.'
        if ($ReceiptUnreadable) {
            Write-Output "  launch receipt exists at $ReceiptPath but is unreadable/half-written"
        }
        if ($ClaimPresent) {
            Write-Output "  launch claim exists at $ClaimPath without a valid completed receipt"
            if ($ClaimUnreadable) {
                Write-Output '  (the claim file itself is unreadable/half-written)'
            }
        }
        Write-Output 'Semantics: the unique one-shot claim may already have been consumed.'
        Write-Output 'State stays UNKNOWN: no automatic second launch, no claim recovery,'
        Write-Output 'no receipt rewrite. Manual adjudication of the campaign directory,'
        Write-Output 'process evidence and the retained claim file is required first.'
        exit 6
    }
} else {
    if ($PriorLaunch) { Write-Output "NOTE (dry-run): a real launch receipt already exists at $ReceiptPath" }
    if ($ReceiptUnreadable) { Write-Output "NOTE (dry-run): receipt at $ReceiptPath is unreadable/half-written (real run would be LOST_ACK refused)" }
    if ($ClaimPresent) { Write-Output "NOTE (dry-run): claim at $ClaimPath present without completed receipt (real run would be LOST_ACK refused)" }
}

# --- 1. read-only preflight ----------------------------------------------
Write-Output "PREFLIGHT: $Python -I -S -B `"$Preflight`" --spec `"$SpecPath`" --json-out `"$PreflightLast`""
try {
    & $Python -I -S -B $Preflight --spec $SpecPath --json-out $PreflightLast | Out-Null
    $PreflightExit = $LASTEXITCODE
} catch {
    # e.g. an unfilled template spec (placeholder python_exe/preflight paths):
    # fail closed exactly like a non-GO preflight; nothing else has happened.
    Write-Output "PREFLIGHT_FAILED: could not run the preflight ($_): refusing to launch."
    exit 2
}
if (-not (Test-Path -LiteralPath $PreflightLast -PathType Leaf)) {
    Write-Output 'PREFLIGHT_FAILED: no preflight report produced; refusing to launch.'
    exit 2
}
$Report = Get-Content -LiteralPath $PreflightLast -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Output ("PREFLIGHT_VERDICT: {0} (unmet: {1})" -f $Report.overall, ($Report.unmet_ids -join ', '))
# --- 2. compose the exact launch command ----------------------------------
# Start-Process joins an -ArgumentList ARRAY with bare spaces (no quoting),
#   which would split a path containing spaces.
# Pass ONE pre-quoted argument STRING instead.
$ArgList = @('-I', '-S', '-B', $Driver, 'run', '--campaign', $CampaignDir, '--config', $Config)
$QuotedArgs = $ArgList | ForEach-Object { '"' + ($_ -replace '"', '') + '"' }
$ArgumentString = $QuotedArgs -join ' '
$LaunchCommand = ('"' + $Python + '"') + ' ' + $ArgumentString

$TargetEndUtc = ([DateTimeOffset]::UtcNow.AddSeconds([double]$Spec.gates.max_wall_seconds + 1800)).UtcDateTime.ToString('yyyy-MM-ddTHH:mm:ssZ')
$CloseoutCommand = ('"{0}" -I -S -B "{1}" wait --campaign "{2}" --out "{3}" --stop-file "{4}"' +
                    ' --deadline "{5}" --target-end "{6}"' +
                    ' --audit-config "{7}" --audit-manifest "{8}" --label {9}' +
                    ' --interval 300 --stability 900') -f `
    $Python, $Closeout, $CampaignDir, (Join-Path $CampaignRoot 'closeout-wait.jsonl'), $StopPath,
    $CloseoutDeadline, $TargetEndUtc, $Config, $Manifest, $CloseoutLabel

if ($DryRun) {
    Write-Output ''
    Write-Output '=== DRY RUN: nothing below is executed ==='
    if ($PreflightExit -ne 0 -or $Report.overall -ne 'GO') {
        Write-Output 'A REAL LAUNCH WOULD BE REFUSED AT THIS POINT (preflight not all-green).'
        Write-Output 'The planned commands are printed for static validation only:'
    }
    Write-Output "CREATE DIR  : New-Item -ItemType Directory -Path `"$CampaignDir`""
    Write-Output "WORK TEMP   : $WorkTemp (TEMP/TMP for the driver process)"
    Write-Output "CLAIM       : $ClaimPath (written only by a REAL launch, before the process starts)"
    Write-Output "LAUNCH CMD  : Start-Process -FilePath `"$Python`" -ArgumentList '$ArgumentString' -WindowStyle Hidden -WorkingDirectory `"$SrcRoot`" -PassThru"
    Write-Output "STOP PATH   : $StopPath  (write a note into this file to cooperatively stop)"
    Write-Output "RECEIPT     : $ReceiptPath"
    Write-Output "CLOSEOUT NEXT (recorded, NOT auto-run):"
    Write-Output "  $CloseoutCommand"
    Write-Output '=== DRY RUN END ==='
    if ($PreflightExit -ne 0 -or $Report.overall -ne 'GO') { exit 2 }
    exit 0
}

# --- 2b. real launch gate (reached only outside -DryRun) ------------------
if ($PreflightExit -ne 0 -or $Report.overall -ne 'GO') {
    Write-Output 'REFUSING TO LAUNCH: preflight is not all-green.'
    foreach ($chk in @($Report.checks)) {
        if ($chk.status -ne 'PASS') {
            Write-Output ("  NOT-MET [{0}] {1}: {2}" -f $chk.status, $chk.id, ($chk.detail | ConvertTo-Json -Compress -Depth 4))
        }
    }
    Write-Output "Full report: $PreflightLast"
    exit 2
}

# --- 2c. planned campaign root must not exist ------------------------------
if (Test-Path -LiteralPath $CampaignRoot) {
    Write-Output "REFUSED: planned campaign root already exists: $CampaignRoot"
    exit 4
}

# --- 2d. persist the UNIQUE launch claim BEFORE any launch effect ----------
# Claim-first one-shot protocol: the claim is the durable record that the
# single launch slot has been consumed. If anything below fails before the
# receipt is written, the claim alone means LOST-ACK/UNKNOWN on the next
# invocation - never an automatic second launch.
$ClaimId = [guid]::NewGuid().ToString('N')
$ClaimedUtc = ([DateTimeOffset]::UtcNow).UtcDateTime.ToString('yyyy-MM-ddTHH:mm:ssZ')
$Claim = [ordered]@{
    schema            = 'pal-rv05-launch-claim-v1'
    task_id           = $Spec.task_id
    claim_id          = $ClaimId
    dry_run           = $false
    state             = 'claimed'
    claimed_at_utc    = $ClaimedUtc
    campaign_dir      = $CampaignDir
    spec_path         = $SpecPath
    frozen_commit     = $Spec.frozen.commit
    frozen_tree       = $Spec.frozen.tree
    launch_window_utc = $Spec.launch_window_utc
    once_only         = $true
    notes             = @(
        'Written BEFORE the launch attempt: the unique one-shot claim.',
        'If no completed receipt follows this claim, the launch state is UNKNOWN (lost-ack):',
        'keep UNKNOWN, never auto-relaunch, never rewrite the claim or receipt; adjudicate manually.'
    )
}
$ClaimTmp = "$ClaimPath.tmp"
$Claim | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ClaimTmp -Encoding UTF8
Move-Item -Force -LiteralPath $ClaimTmp -Destination $ClaimPath
Write-Output "CLAIMED (one-shot): $ClaimPath (claim_id=$ClaimId)"

# --- 3. real launch (reached only when preflight == GO) -------------------
New-Item -ItemType Directory -Path $CampaignDir | Out-Null
if (-not (Test-Path -LiteralPath $WorkTemp)) {
    New-Item -ItemType Directory -Path $WorkTemp | Out-Null
}

# Sanitize the child environment: own TEMP/TMP; -I ignores PYTHON* vars anyway.
$env:TEMP = $WorkTemp
$env:TMP = $WorkTemp

$StartedUtc = ([DateTimeOffset]::UtcNow).UtcDateTime.ToString('yyyy-MM-ddTHH:mm:ssZ')
Write-Output "LAUNCHING: $LaunchCommand"
$Proc = Start-Process -FilePath $Python -ArgumentList $ArgumentString -WindowStyle Hidden -WorkingDirectory $SrcRoot -PassThru
Start-Sleep -Seconds 8

$Alive = $false
try { $Proc.Refresh(); $Alive = -not $Proc.HasExited } catch { $Alive = $false }
$CampaignJsonWritten = Test-Path -LiteralPath (Join-Path $CampaignDir 'campaign.json') -PathType Leaf
$LockWritten = Test-Path -LiteralPath (Join-Path $CampaignDir 'driver.lock') -PathType Leaf

$Receipt = [ordered]@{
    schema            = 'pal-rv05-launch-receipt-v2'
    task_id           = $Spec.task_id
    dry_run           = $false
    launch_window_utc = $Spec.launch_window_utc
    once_only         = $true
    claim_id          = $ClaimId
    launched_at_utc   = $StartedUtc
    pid               = $Proc.Id
    pid_alive_at_t8s  = $Alive
    launch_command    = $LaunchCommand
    working_directory = $SrcRoot
    campaign_dir      = $CampaignDir
    stop_path         = $StopPath
    campaign_json_written = $CampaignJsonWritten
    driver_lock_written   = $LockWritten
    config            = $Config
    config_sha256     = Get-FileSha256 $Config
    manifest          = $Manifest
    manifest_sha256   = Get-FileSha256 $Manifest
    driver_sha256     = Get-FileSha256 $Driver
    audit_sha256      = Get-FileSha256 $Audit
    closeout_sha256   = Get-FileSha256 $Closeout
    python_sha256     = Get-FileSha256 $Python
    frozen_commit     = $Spec.frozen.commit
    frozen_tree       = $Spec.frozen.tree
    spec_path         = $SpecPath
    spec_binding      = $Spec.binding
    preflight_report  = $PreflightLast
    preflight_verdict = $Report.overall
    gates             = $Spec.gates
    closeout_next_command = $CloseoutCommand
    notes             = @(
        'STOP: write a short note into stop_path to cooperatively stop the driver.',
        'One-shot: this receipt blocks a second real launch of this script.',
        'Lost-ack: a claim without this receipt keeps the launch state UNKNOWN; no auto relaunch.',
        'The closeout command is recorded for the next stage; it is NOT started here.'
    )
}
$ReceiptTmp = "$ReceiptPath.tmp"
$Receipt | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ReceiptTmp -Encoding UTF8
Move-Item -Force -LiteralPath $ReceiptTmp -Destination $ReceiptPath

if (-not $Alive) {
    Write-Output ("LAUNCH_FAILED_OR_IMMEDIATE_EXIT: pid {0} is not alive after 8s; receipt written: {1}" -f $Proc.Id, $ReceiptPath)
    exit 5
}
Write-Output ("LAUNCHED: pid={0} campaign={1}" -f $Proc.Id, $CampaignDir)
Write-Output ("STOP PATH: {0}" -f $StopPath)
Write-Output ("RECEIPT:   {0}" -f $ReceiptPath)
exit 0
