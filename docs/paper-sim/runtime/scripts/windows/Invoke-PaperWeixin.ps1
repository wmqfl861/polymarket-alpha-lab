# Windows PowerShell 5.1. The only process argument is the task nonce.
Set-StrictMode -Version 3
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$Utf8 = [Text.UTF8Encoding]::new($false, $true)
$Invariant = [Globalization.CultureInfo]::InvariantCulture
$Protocol = 'paper-weixin-fixed-v1'
$Hex64 = '\A[a-f0-9]{64}\z'
$Hex32 = '\A[a-f0-9]{32}\z'
$StageKey = '\A[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\z'
$UtcStamp = '\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)\z'
$JobKeys = @('stage_key', 'nonce', 'target_digest', 'body_variant', 'body_digest',
    'starts_utc', 'cutoff_utc', 'deadline_utc')
$LegacyReceiptKeys = @('stage_key', 'nonce', 'target_digest', 'body_variant', 'body_digest', 'cutoff_utc',
    'state', 'platform', 'target_verified', 'native_success', 'chat_verified', 'native_exit_code',
    'message_id_sha256', 'send_started_at', 'confirmed_at')
$FixedReceiptKeys = $LegacyReceiptKeys + @('protocol', 'script_sha256', 'starts_utc', 'deadline_utc')
$RefusalReasons = [Collections.Generic.Dictionary[string, string]]::new([StringComparer]::Ordinal)
$RefusalReasons.Add('TOOL_DENIED', 'zcode_tool_permission_denied')
$RefusalReasons.Add('PLATFORM_DENIED', 'hermes_platform_write_denied')
$RefusalReasons.Add('OWNER_REPLY_REQUIRED', 'weixin_owner_message_required')
$OnWindows = [IO.Path]::DirectorySeparatorChar -eq '\'
$Pins = [Collections.Generic.List[IDisposable]]::new()

# Parse the flat wire objects with ordinal, unique literal keys. Each value has
# one unambiguous JSON type. PowerShell's default JSON reader collapses duplicate keys.
$JsonProperty = [regex]'\G[ \t\r\n]*"(?<key>[a-z_][a-z_0-9]*)"[ \t\r\n]*:[ \t\r\n]*(?:"(?<string>(?:[^\x00-\x1f"\\]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*)"|(?<integer>0|[1-9][0-9]*)|(?<boolean>true|false))[ \t\r\n]*(?<end>[,}])'
$JsonEscapes = @{ '"' = '"'; '\' = '\'; '/' = '/'; 'b' = [char]8; 'f' = [char]12;
    'n' = [char]10; 'r' = [char]13; 't' = [char]9 }
$Unescape = [Text.RegularExpressions.MatchEvaluator]{
    param($match)
    $escape = $match.Groups[1].Value
    if ($escape.Length -eq 5) {
        return [string][char][Convert]::ToInt32($escape.Substring(1), 16)
    }
    return [string]$JsonEscapes[$escape]
}

function Read-FlatObject([byte[]]$Bytes) {
    $json = $Utf8.GetString($Bytes)
    $start = [regex]::Match($json, '\A[ \t\r\n]*\{')
    if (!$start.Success) { throw 'Invalid JSON object' }
    $index = $start.Length
    $fields = [Collections.Generic.Dictionary[string, object]]::new([StringComparer]::Ordinal)
    do {
        $property = $JsonProperty.Match($json, $index)
        if (!$property.Success) { throw 'Invalid JSON property' }
        if ($property.Groups['string'].Success) {
            $value = [regex]::Replace($property.Groups['string'].Value, '\\(u[0-9a-fA-F]{4}|["\\/bfnrt])', $Unescape)
        } elseif ($property.Groups['integer'].Success) {
            $value = [int]::Parse($property.Groups['integer'].Value, $Invariant)
        } else {
            $value = $property.Groups['boolean'].Value -ceq 'true'
        }
        $fields.Add($property.Groups['key'].Value, $value)
        $index += $property.Length
    } until ($property.Groups['end'].Value -ceq '}')
    if ($json.Substring($index) -cnotmatch '\A[ \t\r\n]*\z') { throw 'Trailing JSON data' }
    return ,$fields
}

function Test-Keys($Object, [string[]]$Keys) {
    if ($Object.Count -ne $Keys.Count) { return $false }
    foreach ($key in $Keys) {
        if (!$Object.ContainsKey($key)) { return $false }
    }
    return $true
}

function Test-String($Value, [string]$Pattern) {
    return ($Value -is [string] -and $Value -cmatch $Pattern)
}

function Test-ExactString($Value, [string]$Expected) {
    return ($Value -is [string] -and $Value -ceq $Expected)
}

function Read-Bounded([IO.Stream]$Stream, [int]$Maximum) {
    $buffer = [byte[]]::new($Maximum + 1)
    $count = 0
    while ($count -lt $buffer.Length) {
        $read = $Stream.Read($buffer, $count, $buffer.Length - $count)
        if ($read -eq 0) { break }
        $count += $read
    }
    if ($count -gt $Maximum) { throw 'Input limit exceeded' }
    $bytes = [byte[]]::new($count)
    [Array]::Copy($buffer, $bytes, $count)
    return ,$bytes
}

function Get-Sha256([IO.Stream]$Stream) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($algorithm.ComputeHash($Stream)).Replace('-', '').ToLowerInvariant()
    } finally { $algorithm.Dispose() }
}

function Get-BytesSha256([byte[]]$Bytes) {
    $stream = [IO.MemoryStream]::new($Bytes, $false)
    try { return Get-Sha256 $stream } finally { $stream.Dispose() }
}

function Write-Reply([int]$Code, $Fields) {
    $reply = [ordered]@{ protocol = $Protocol; purpose = $Request['purpose']; code = $Code; script_sha256 = $ScriptHash }
    foreach ($key in $Fields.Keys) { $reply.Add($key, $Fields[$key]) }
    [Console]::Write((ConvertTo-Json -InputObject $reply -Compress))
}

# Pin Windows path components without compiling a helper or starting another
# process. A no-delete-share handle prevents replacement while it is held.
function Add-NativeFileMethod($Builder, [string]$Name, [string]$Library, [type]$Result, [type[]]$Arguments) {
    $calling = if ($OnWindows) { [Runtime.InteropServices.CallingConvention]::Winapi }
        else { [Runtime.InteropServices.CallingConvention]::Cdecl }
    $charset = if ($OnWindows) { [Runtime.InteropServices.CharSet]::Unicode }
        else { [Runtime.InteropServices.CharSet]::Ansi }
    $method = $Builder.DefinePInvokeMethod($Name, $Library, [Reflection.MethodAttributes]'Public, Static, PinvokeImpl',
        [Reflection.CallingConventions]::Standard, $Result, $Arguments, $calling, $charset)
    $method.SetImplementationFlags($method.GetMethodImplementationFlags() -bor [Reflection.MethodImplAttributes]::PreserveSig)
}

function New-NativeFileApi {
    $name = [Reflection.AssemblyName]::new('PaperWeixinFileApi')
    $access = [Reflection.Emit.AssemblyBuilderAccess]::Run
    $assembly = if ($OnWindows) { [AppDomain]::CurrentDomain.DefineDynamicAssembly($name, $access) }
        else { [Reflection.Emit.AssemblyBuilder]::DefineDynamicAssembly($name, $access) }
    $builder = $assembly.DefineDynamicModule('FileApi').DefineType('PaperWeixinFileApi', [Reflection.TypeAttributes]::Public)
    if ($OnWindows) {
        Add-NativeFileMethod $builder 'CreateFileW' 'kernel32.dll' ([Microsoft.Win32.SafeHandles.SafeFileHandle]) @(
            [string], [uint32], [uint32], [IntPtr], [uint32], [uint32], [IntPtr])
        Add-NativeFileMethod $builder 'GetFileInformationByHandle' 'kernel32.dll' ([bool]) @(
            [Microsoft.Win32.SafeHandles.SafeFileHandle], [IntPtr])
        Add-NativeFileMethod $builder 'CreateDirectoryW' 'kernel32.dll' ([bool]) @([string], [IntPtr])
    } else {
        # Same source can be exercised with a local Linux PowerShell interpreter.
        Add-NativeFileMethod $builder 'mkdir' 'libc' ([int]) @([string], [uint32])
    }
    return $builder.CreateType()
}

function Get-AbsolutePath([string]$Path) {
    if ($OnWindows) {
        if ($Path -cnotmatch '\A[A-Za-z]:\\[^:\x00-\x1f]*\z') { throw 'Non-local Windows path' }
    } elseif (!$Path.StartsWith('/')) { throw 'Relative path' }
    return [IO.Path]::GetFullPath($Path)
}

function Get-SafeAttributes([string]$Path, [bool]$Directory) {
    try {
        $attributes = [IO.File]::GetAttributes($Path)
    } catch [IO.FileNotFoundException], [IO.DirectoryNotFoundException] { return $null }
    $isDirectory = [bool]($attributes -band [IO.FileAttributes]::Directory)
    if ($isDirectory -ne $Directory -or ($attributes -band [IO.FileAttributes]'ReparsePoint, Device')) {
        throw 'Unexpected path type'
    }
    return $attributes
}

function Open-WindowsHandle([string]$Path, [bool]$Directory) {
    # OPEN_REPARSE_POINT. Directories additionally need BACKUP_SEMANTICS.
    [uint32]$flags = 0x00200000
    [uint32]$access = 2147483648 # GENERIC_READ
    [uint32]$share = 1 # FILE_SHARE_READ
    if ($Directory) {
        $flags = $flags -bor 0x02000000
        # Attribute-only handles do not participate in Windows share checks.
        $access = 0x81 # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES
        $share = 3 # FILE_SHARE_READ | FILE_SHARE_WRITE
    }
    $handle = $NativeFileApi::CreateFileW($Path, $access, $share, [IntPtr]::Zero, 3, $flags, [IntPtr]::Zero)
    $verified = $false
    try {
        if ($handle.IsInvalid) { throw 'Cannot pin path' }
        # BY_HANDLE_FILE_INFORMATION is 13 DWORDs. Attributes are first and
        # nNumberOfLinks follows the two file-size DWORDs.
        $info = [Runtime.InteropServices.Marshal]::AllocHGlobal(52)
        try {
            if (!$NativeFileApi::GetFileInformationByHandle($handle, $info)) { throw 'Cannot verify path' }
            $attributes = [IO.FileAttributes][Runtime.InteropServices.Marshal]::ReadInt32($info, 0)
            $links = [Runtime.InteropServices.Marshal]::ReadInt32($info, 40)
            $isDirectory = [bool]($attributes -band [IO.FileAttributes]::Directory)
            if ($isDirectory -ne $Directory -or ($attributes -band [IO.FileAttributes]'ReparsePoint, Device') -or
                (!$Directory -and $links -ne 1)) { throw 'Unsafe pinned path' }
            $verified = $true
            return $handle
        } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($info) }
    } finally {
        if (!$verified) { $handle.Dispose() }
    }
}

function Pin-Directory([string]$Path, [bool]$Create, $Handles) {
    $parent = [IO.Directory]::GetParent($Path)
    if ($null -ne $parent -and !(Pin-Directory $parent.FullName $false $Handles)) { return $false }
    if ($null -eq (Get-SafeAttributes $Path $true)) {
        if (!$Create) { return $false }
        $null = [IO.Directory]::CreateDirectory($Path)
        if ($null -eq (Get-SafeAttributes $Path $true)) { throw 'Missing directory' }
    }
    if ($OnWindows) {
        if ($null -eq $parent) {
            if ([IO.DriveInfo]::new($Path).DriveType -ne [IO.DriveType]::Fixed) { throw 'Non-local volume' }
        } else {
            $Handles.Add((Open-WindowsHandle $Path $true))
        }
    }
    return $true
}

function Open-ReadFile([string]$Path) {
    if ($null -eq (Get-SafeAttributes $Path $false)) { throw 'Missing file' }
    if (!$OnWindows) { return [IO.File]::Open($Path, 'Open', 'Read', 'Read') }
    $handle = Open-WindowsHandle $Path $false
    try { return [IO.FileStream]::new($handle, [IO.FileAccess]::Read) }
    catch { $handle.Dispose(); throw }
}

function Read-PinnedBytes([string]$Path, [int]$Maximum) {
    $stream = Open-ReadFile $Path
    try { return ,(Read-Bounded $stream $Maximum) } finally { $stream.Dispose() }
}

function Write-NewFile([string]$Path, [byte[]]$Bytes) {
    $stream = [IO.File]::Open($Path, 'CreateNew', 'Write', 'None')
    try {
        $stream.Write($Bytes, 0, $Bytes.Length)
        $stream.Flush($true)
    } finally { $stream.Dispose() }
}

function Write-AtomicObject([string]$Directory, [string]$Name, $Object) {
    $pending = [IO.Path]::Combine($Directory, "$Name.pending")
    $final = [IO.Path]::Combine($Directory, $Name)
    $bytes = $Utf8.GetBytes((ConvertTo-Json -InputObject $Object -Compress))
    Write-NewFile $pending $bytes
    [IO.File]::Move($pending, $final)
    return ,$bytes
}

function Read-Window($Job) {
    if (!(Test-String $Job['starts_utc'] $UtcStamp) -or !(Test-String $Job['cutoff_utc'] $UtcStamp) -or
        !(Test-String $Job['deadline_utc'] $UtcStamp)) { throw 'Invalid UTC timestamps' }
    $starts = [DateTimeOffset]::Parse($Job['starts_utc'], $Invariant)
    $cutoff = [DateTimeOffset]::Parse($Job['cutoff_utc'], $Invariant)
    $deadline = [DateTimeOffset]::Parse($Job['deadline_utc'], $Invariant)
    if ($starts -ge $cutoff -or $cutoff -gt $deadline) { throw 'Invalid UTC window' }
    return @{ starts = $starts; cutoff = $cutoff }
}

function Test-Job($Job) {
    return ((Test-Keys $Job $JobKeys) -and (Test-String $Job['stage_key'] $StageKey) -and
        (Test-ExactString $Job['nonce'] $TaskNonce) -and
        (Test-ExactString $Job['target_digest'] $Request['target_digest']) -and
        $Job['body_variant'] -is [string] -and $Job['body_variant'] -cin @('primary', 'fallback') -and
        (Test-String $Job['body_digest'] $Hex64))
}

function Format-Utc([DateTimeOffset]$Stamp) {
    return $Stamp.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'", $Invariant)
}

function Assert-Window($Window, [DateTimeOffset]$Stamp, [int]$Budget) {
    if ($Stamp -lt $Window.starts -or $Stamp -ge $Window.cutoff -or
        $Stamp.AddSeconds($Budget + 3) -ge $Window.cutoff) { exit 22 }
}

function Get-HermesStart([string[]]$Argv) {
    $start = [Diagnostics.ProcessStartInfo]::new($Hermes)
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    if ($OnWindows) {
        # .NET Framework has only Arguments. Encode the fixed argument vector
        # with Windows argv quoting, including backslashes before quotes.
        $quoted = foreach ($argument in $Argv) {
            '"' + ([regex]::Replace(([regex]::Replace($argument, '(\\*)"', '$1$1\"')), '(\\+)\z', '$1$1')) + '"'
        }
        $start.Arguments = $quoted -join ' '
    } else {
        foreach ($argument in $Argv) { $start.ArgumentList.Add($argument) }
    }
    return $start
}

function Get-RemainingMilliseconds($Clock, [int]$Seconds) {
    return [int][Math]::Max(0, ($Seconds * 1000) - $Clock.ElapsedMilliseconds)
}

function Invoke-Hermes([string[]]$Argv, [int]$Seconds, $Window = $null) {
    $process = [Diagnostics.Process]::new()
    $started = $false
    try {
        $process.StartInfo = Get-HermesStart $Argv
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $when = [DateTimeOffset]::UtcNow
        if ($null -ne $Window) { Assert-Window $Window $when $Seconds }
        $started = $process.Start()
        if (!$started) { return $null }
        $null = $process.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
        $stream = $process.StandardOutput.BaseStream
        $buffer = [byte[]]::new(32769)
        $count = 0
        while ($count -lt $buffer.Length) {
            $pending = $stream.ReadAsync($buffer, $count, $buffer.Length - $count)
            if (!$pending.Wait((Get-RemainingMilliseconds $clock $Seconds))) { return $null }
            if ($pending.Result -eq 0) { break }
            $count += $pending.Result
        }
        if ($count -eq $buffer.Length -or !$process.WaitForExit((Get-RemainingMilliseconds $clock $Seconds))) {
            return $null
        }
        $bytes = [byte[]]::new($count)
        [Array]::Copy($buffer, $bytes, $count)
        return @{ code = $process.ExitCode; bytes = $bytes; started = $when }
    } catch {
        return $null
    } finally {
        if ($started) {
            try { if (!$process.HasExited) { $process.Kill() } } catch {}
        }
        $process.Dispose()
    }
}

# The native JSON has a nested platform listing. The .NET JSON/XML reader keeps
# individual members, so duplicate or wrongly typed proof fields can be rejected.
function Read-NativeObject([byte[]]$Bytes) {
    if ($OnWindows) {
        # Windows PowerShell uses the CLR framework identity for both assemblies.
        $framework = [object].Assembly.GetName()
        $serialization = [Reflection.AssemblyName]::new('System.Runtime.Serialization')
        $serialization.Version = $framework.Version
        $serialization.CultureInfo = [Globalization.CultureInfo]::InvariantCulture
        $serialization.SetPublicKeyToken($framework.GetPublicKeyToken())
        $null = [Reflection.Assembly]::Load($serialization)
    }
    $quotas = [Xml.XmlDictionaryReaderQuotas]::new()
    $quotas.MaxDepth = 16
    $quotas.MaxStringContentLength = 32768
    $reader = [Runtime.Serialization.Json.JsonReaderWriterFactory]::CreateJsonReader($Bytes, $quotas)
    try {
        $xml = [Xml.XmlDocument]::new()
        $xml.XmlResolver = $null
        $xml.Load($reader)
        $root = $xml.PSBase.DocumentElement
        if ($root.GetAttribute('type') -cne 'object') { throw 'Native result must be an object' }
        return ,$root
    } finally { $reader.Dispose() }
}

function Get-NativeMember([Xml.XmlElement]$Object, [string]$Name) {
    if ($null -eq $Object -or $Object.GetAttribute('type') -cne 'object') { return $null }
    $result = $null
    foreach ($child in $Object.PSBase.ChildNodes) {
        if ($child.PSBase.LocalName -ieq $Name) {
            if ($null -ne $result -or $child.PSBase.LocalName -cne $Name) { throw 'Ambiguous native member' }
            $result = $child
        }
    }
    return ,$result
}

function Get-NativeValue([Xml.XmlElement]$Object, [string]$Name, [string]$Type) {
    $member = Get-NativeMember $Object $Name
    if ($null -ne $member -and $member.GetAttribute('type') -ceq $Type) {
        return $member.PSBase.InnerText
    }
    return $null
}

function Stop-OnRefusal([Xml.XmlElement]$Result, [string]$Directory = '') {
    $success = Get-NativeValue $Result 'success' 'boolean'
    $code = Get-NativeValue $Result 'error_code' 'string'
    if ($success -cne 'false' -or $null -eq $code -or !$RefusalReasons.ContainsKey($code)) { return }
    $reason = $RefusalReasons[$code]
    if ($Directory) {
        try { $null = Write-AtomicObject $Directory 'outcome.json' @{ state = 'refused'; reason = $reason } } catch {}
    }
    Write-Reply 32 @{ reason = $reason }
    exit 32
}

function Get-VerifiedTarget([int]$Seconds, [string]$Directory = '') {
    $listed = Invoke-Hermes @('send', '--list', 'weixin', '--json') $Seconds
    if ($null -eq $listed) { exit 23 }
    try {
        $listing = Read-NativeObject $listed.bytes
        Stop-OnRefusal $listing $Directory
        if ($listed.code -ne 0) { exit 23 }
        $success = Get-NativeMember $listing 'success'
        $nativeError = Get-NativeMember $listing 'error_code'
        if (($null -ne $success -and (Get-NativeValue $listing 'success' 'boolean') -cne 'true') -or
            ($null -ne $nativeError -and $nativeError.GetAttribute('type') -cne 'null')) { exit 23 }
        $platforms = Get-NativeMember $listing 'platforms'
        $targets = Get-NativeMember $platforms 'weixin'
        if ($null -eq $targets -or $targets.GetAttribute('type') -cne 'array' -or
            $targets.PSBase.ChildNodes.Count -ne 1) { exit 24 }
        $target = $targets.PSBase.ChildNodes[0]
        $id = Get-NativeValue $target 'id' 'string'
        if ((Get-NativeValue $target 'type' 'string') -cne 'dm' -or !$id -or $id -match '[:\s\p{Cc}]') { exit 24 }
        $qualified = 'weixin:' + $id
        $hash = Get-BytesSha256 ($Utf8.GetBytes($qualified))
        if ($hash -cne $Binding['target_digest'] -or $hash -cne $Request['target_digest']) { exit 25 }
        return @{ raw = $id; qualified = $qualified }
    } catch { exit 23 }
}

function Remove-JobFiles([string]$Directory, [string[]]$Names) {
    foreach ($name in $Names) {
        $path = [IO.Path]::Combine($Directory, $name)
        if ($null -ne (Get-SafeAttributes $path $false)) { [IO.File]::Delete($path) }
    }
}

function Clear-StaleJobs([string]$Root) {
    # Receipt recovery covers submissions for 24 hours. Keep a full extra
    # hour for transport, including a job whose local claim preceded send.
    $stale = [DateTime]::UtcNow.AddHours(-25)
    $names = @('job.json', 'job.json.pending', 'note.txt', 'send.claimed', 'receipt.json', 'receipt.json.pending',
        'outcome.json', 'outcome.json.pending')
    $checked = 0
    $removed = 0
    foreach ($directory in [IO.Directory]::EnumerateDirectories($Root)) {
        if (++$checked -gt 128 -or $removed -ge 24) { break }
        if ([IO.Path]::GetFileName($directory) -cnotmatch $Hex32) { continue }
        $pin = $null
        try {
            if ($null -eq (Get-SafeAttributes $directory $true)) { continue }
            if ($OnWindows) { $pin = Open-WindowsHandle $directory $true }
            if ([IO.Directory]::GetCreationTimeUtc($directory) -ge $stale) { continue }
            $old = $true
            foreach ($name in $names) {
                $file = [IO.Path]::Combine($directory, $name)
                if ($null -eq (Get-SafeAttributes $file $false)) { continue }
                if ([IO.File]::GetLastWriteTimeUtc($file) -ge $stale) { $old = $false; break }
                if ($name -cin @('job.json', 'job.json.pending', 'send.claimed')) {
                    $saved = Read-FlatObject (Read-PinnedBytes $file 4096)
                    if (!(Test-String $saved['cutoff_utc'] $UtcStamp) -or
                        [DateTimeOffset]::Parse($saved['cutoff_utc'], $Invariant) -gt [DateTimeOffset]::UtcNow) {
                        $old = $false
                        break
                    }
                }
            }
            if (!$old) { continue }
            Remove-JobFiles $directory $names
        } catch { continue }
        finally { if ($null -ne $pin) { $pin.Dispose() } }
        try { [IO.Directory]::Delete($directory, $false); $removed += 1 } catch {}
    }
}

function Get-ReceiptProjection($Receipt, [string[]]$Keys) {
    foreach ($name in $Keys) {
        if (!$Receipt.ContainsKey($name)) { throw 'Missing receipt binding' }
    }
    if (!(Test-String $Receipt['stage_key'] $StageKey) -or !(Test-ExactString $Receipt['nonce'] $TaskNonce) -or
        !(Test-ExactString $Receipt['target_digest'] $Request['target_digest']) -or
        $Receipt['body_variant'] -isnot [string] -or $Receipt['body_variant'] -cnotin @('primary', 'fallback') -or
        !(Test-String $Receipt['body_digest'] $Hex64) -or !(Test-String $Receipt['message_id_sha256'] $Hex64) -or
        !(Test-ExactString $Receipt['state'] 'accepted') -or !(Test-ExactString $Receipt['platform'] 'weixin') -or
        $Receipt['native_exit_code'] -isnot [int] -or $Receipt['native_exit_code'] -ne 0) {
        throw 'Invalid receipt binding'
    }
    foreach ($name in @('target_verified', 'native_success', 'chat_verified')) {
        if ($Receipt[$name] -isnot [bool] -or !$Receipt[$name]) { throw 'Invalid native receipt status' }
    }
    # Legacy receipts could contain a seventh fractional digit. Preserve the
    # original value for the existing Linux verifier.
    $timePattern = if ($Keys.Count -eq $FixedReceiptKeys.Count) { $UtcStamp }
        else { '\A[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,7})?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})\z' }
    foreach ($name in @('cutoff_utc', 'send_started_at', 'confirmed_at')) {
        if (!(Test-String $Receipt[$name] $timePattern)) { throw 'Invalid receipt timestamp' }
    }
    if ($Keys.Count -eq $FixedReceiptKeys.Count) {
        if (!(Test-ExactString $Receipt['protocol'] $Protocol) -or
            !(Test-ExactString $Receipt['script_sha256'] $ScriptHash) -or
            !(Test-String $Receipt['starts_utc'] $UtcStamp) -or !(Test-String $Receipt['deadline_utc'] $UtcStamp)) {
            throw 'Invalid receipt protocol'
        }
    }
    $projection = [ordered]@{}
    foreach ($name in $Keys) { $projection.Add($name, $Receipt[$name]) }
    return $projection
}

function Read-LegacyReceipt([byte[]]$Bytes) {
    $root = Read-NativeObject $Bytes
    $fields = [Collections.Generic.Dictionary[string, object]]::new([StringComparer]::Ordinal)
    foreach ($name in $LegacyReceiptKeys) {
        if ($name -cin @('target_verified', 'native_success', 'chat_verified')) {
            $fields.Add($name, ((Get-NativeValue $root $name 'boolean') -ceq 'true'))
        } elseif ($name -ceq 'native_exit_code') {
            if ((Get-NativeValue $root $name 'number') -cne '0') { throw 'Invalid native exit code' }
            $fields.Add($name, 0)
        } else {
            $fields.Add($name, (Get-NativeValue $root $name 'string'))
        }
    }
    return ,$fields
}

try {
    if ($args.Count -ne 1 -or !(Test-String $args[0] $Hex32)) { exit 21 }
    $TaskNonce = $args[0]
    try {
        $Request = Read-FlatObject (Read-Bounded ([Console]::OpenStandardInput()) 20480)
        if (!(Test-ExactString $Request['protocol'] $Protocol) -or !(Test-String $Request['script_sha256'] $Hex64) -or
            !(Test-String $Request['target_digest'] $Hex64) -or
            !(Test-String $Request['hostname'] $StageKey) -or $Request['hostname'] -ine [Environment]::MachineName) {
            exit 21
        }
        $requestKeys = @('protocol', 'purpose', 'hostname', 'script_sha256', 'target_digest')
        $purpose = $Request['purpose']
        if ($purpose -isnot [string]) { exit 21 }
        switch -CaseSensitive ($purpose) {
            'check' {}
            'prepare' { $requestKeys += @('stage_key', 'body_variant', 'body_digest', 'body', 'starts_utc', 'cutoff_utc', 'deadline_utc') }
            'send' { $requestKeys += 'send_budget_seconds' }
            'receipt' { $requestKeys += 'receipt_generation' }
            'cleanup' {}
            default { exit 21 }
        }
        if (!(Test-Keys $Request $requestKeys)) { exit 21 }
        if ($purpose -ceq 'send' -and ($Request['send_budget_seconds'] -isnot [int] -or
            $Request['send_budget_seconds'] -lt 1 -or $Request['send_budget_seconds'] -gt 575)) { exit 21 }
        if ($purpose -ceq 'receipt' -and ($Request['receipt_generation'] -isnot [string] -or
            $Request['receipt_generation'] -cnotin @('fixed', 'legacy'))) { exit 21 }
    } catch { exit 21 }

    $NativeFileApi = New-NativeFileApi
    try {
        $scriptPath = Get-AbsolutePath $PSCommandPath
        $install = [IO.Path]::GetDirectoryName($scriptPath)
        if (!(Pin-Directory $install $false $Pins)) { exit 27 }
        $scriptFile = Open-ReadFile $scriptPath
        $Pins.Add($scriptFile)
        $ScriptHash = Get-Sha256 $scriptFile
        if ($ScriptHash -cne $Request['script_sha256']) { exit 21 }
        $Binding = Read-FlatObject (Read-PinnedBytes ([IO.Path]::Combine($install, 'weixin-binding.json')) 4096)
        if (!(Test-Keys $Binding @('hermes_path', 'target_digest')) -or
            $Binding['hermes_path'] -isnot [string] -or !(Test-String $Binding['target_digest'] $Hex64)) { exit 27 }
        if ($Binding['target_digest'] -cne $Request['target_digest']) { exit 25 }
    } catch { exit 27 }

    if ($purpose -cin @('check', 'send')) {
        try {
            $Hermes = Get-AbsolutePath $Binding['hermes_path']
            if ($OnWindows -and [IO.Path]::GetFileName($Hermes) -ine 'hermes.exe') { exit 23 }
            if (!(Pin-Directory ([IO.Path]::GetDirectoryName($Hermes)) $false $Pins)) { exit 23 }
            $Pins.Add((Open-ReadFile $Hermes))
        } catch { exit 23 }
    }
    if ($purpose -ceq 'check') {
        $null = Get-VerifiedTarget 16
        Write-Reply 0 @{ hostname_matches = $true; target_verified = $true }
        exit 0
    }

    try {
        if ($purpose -ceq 'receipt' -and $Request['receipt_generation'] -ceq 'legacy') {
            $temp = Get-AbsolutePath ([IO.Path]::GetTempPath())
            $directory = [IO.Path]::Combine($temp, "paper-football-wx-$TaskNonce")
        } else {
            $local = Get-AbsolutePath $env:LOCALAPPDATA
            if (!(Pin-Directory $local $false $Pins)) { exit 27 }
            foreach ($name in @('PolymarketAlphaLab', 'WeixinDelivery', 'jobs')) {
                $local = [IO.Path]::Combine($local, $name)
                if (!(Pin-Directory $local ($purpose -ceq 'prepare') $Pins)) {
                    if ($purpose -ceq 'receipt') { Write-Reply 0 @{ present = $false }; exit 0 }
                    if ($purpose -ceq 'cleanup') { Write-Reply 0 @{}; exit 0 }
                    exit 27
                }
            }
            $jobsRoot = $local
            $directory = [IO.Path]::Combine($jobsRoot, $TaskNonce)
        }

        if ($purpose -ceq 'prepare') {
            $job = [Collections.Generic.Dictionary[string, object]]::new([StringComparer]::Ordinal)
            $job.Add('nonce', $TaskNonce)
            $job.Add('target_digest', $Request['target_digest'])
            foreach ($name in @('stage_key', 'body_variant', 'body_digest', 'starts_utc', 'cutoff_utc', 'deadline_utc')) {
                $job.Add($name, $Request[$name])
            }
            $body = $Request['body']
            if (!(Test-Job $job) -or $body -isnot [string] -or $body.IndexOf([char]0) -ge 0 -or
                $body -cmatch '\A[\s\x1c-\x1f]*\z' -or
                [regex]::Matches($body, '[\uD800-\uDBFF][\uDC00-\uDFFF]|[\s\S]').Count -gt 1500) { exit 21 }
            try {
                $bodyBytes = $Utf8.GetBytes($body)
                $window = Read-Window $job
            } catch { exit 21 }
            if ((Get-BytesSha256 $bodyBytes) -cne $job['body_digest']) { exit 26 }
            Assert-Window $window ([DateTimeOffset]::UtcNow) 0
            $created = if ($OnWindows) { $NativeFileApi::CreateDirectoryW($directory, [IntPtr]::Zero) }
                else { $NativeFileApi::mkdir($directory, 448) -eq 0 } # 0700
            if (!$created) { exit 29 }
            if (!(Pin-Directory $directory $false $Pins)) { exit 27 }
            $note = [IO.Path]::Combine($directory, 'note.txt')
            Write-NewFile $note $bodyBytes
            $noteFile = Open-ReadFile $note
            try {
                if ((Get-Sha256 $noteFile) -cne $job['body_digest']) { exit 26 }
                $null = Write-AtomicObject $directory 'job.json' $job
            } finally { $noteFile.Dispose() }
            try { Clear-StaleJobs $jobsRoot } catch {}
            Write-Reply 0 @{ prepared = $true }
            exit 0
        }

        if (!(Pin-Directory $directory $false $Pins)) {
            if ($purpose -ceq 'receipt') { Write-Reply 0 @{ present = $false }; exit 0 }
            if ($purpose -ceq 'cleanup') { Write-Reply 0 @{}; exit 0 }
            exit 27
        }
        $receiptPath = [IO.Path]::Combine($directory, 'receipt.json')
        $claimPath = [IO.Path]::Combine($directory, 'send.claimed')
        $outcomePath = [IO.Path]::Combine($directory, 'outcome.json')
        if ($purpose -ceq 'cleanup') {
            Remove-JobFiles $directory @('note.txt', 'job.json', 'job.json.pending')
            Write-Reply 0 @{}
            exit 0
        }
        if ($purpose -ceq 'receipt') {
            $keys = $LegacyReceiptKeys
            if ($Request['receipt_generation'] -ceq 'fixed') {
                $keys = $FixedReceiptKeys
                if ($null -eq (Get-SafeAttributes $claimPath $false) -or
                    $null -eq (Get-SafeAttributes $outcomePath $false)) {
                    Write-Reply 0 @{ present = $false }
                    exit 0
                }
                $outcome = Read-FlatObject (Read-PinnedBytes $outcomePath 4096)
                if ((Test-Keys $outcome @('state', 'reason')) -and (Test-ExactString $outcome['state'] 'refused') -and
                    $outcome['reason'] -is [string] -and $RefusalReasons.ContainsValue($outcome['reason'])) {
                    Write-Reply 32 @{ reason = $outcome['reason'] }
                    exit 32
                }
                if (!(Test-Keys $outcome @('state', 'receipt_sha256')) -or !(Test-ExactString $outcome['state'] 'accepted') -or
                    !(Test-String $outcome['receipt_sha256'] $Hex64)) { exit 27 }
            }
            if ($null -eq (Get-SafeAttributes $receiptPath $false)) { Write-Reply 0 @{ present = $false }; exit 0 }
            $bytes = Read-PinnedBytes $receiptPath 4096
            if ($Request['receipt_generation'] -ceq 'fixed') {
                $parsed = Read-FlatObject $bytes
                if (!(Test-Keys $parsed $keys) -or (Get-BytesSha256 $bytes) -cne $outcome['receipt_sha256']) { exit 27 }
                $null = Get-ReceiptProjection $parsed $keys
            } else {
                # Old receipts had no fixed schema for extra fields. Keep
                # only the original verifier's fields across the boundary.
                $projection = Get-ReceiptProjection (Read-LegacyReceipt $bytes) $keys
                $bytes = $Utf8.GetBytes((ConvertTo-Json -InputObject $projection -Compress))
            }
            Write-Reply 0 @{ contents = [Convert]::ToBase64String($bytes) }
            exit 0
        }

        foreach ($name in @('send.claimed', 'receipt.json', 'receipt.json.pending', 'outcome.json', 'outcome.json.pending')) {
            if ($null -ne (Get-SafeAttributes ([IO.Path]::Combine($directory, $name)) $false)) { exit 29 }
        }
        $metadata = Open-ReadFile ([IO.Path]::Combine($directory, 'job.json'))
        $Pins.Add($metadata)
        $job = Read-FlatObject (Read-Bounded $metadata 4096)
        if (!(Test-Job $job)) { exit 21 }
        $window = Read-Window $job
        $budget = $Request['send_budget_seconds']
        Assert-Window $window ([DateTimeOffset]::UtcNow) $budget
        $note = [IO.Path]::Combine($directory, 'note.txt')
        $noteFile = Open-ReadFile $note
        $Pins.Add($noteFile)
        if ($noteFile.Length -gt 6000 -or (Get-Sha256 $noteFile) -cne $job['body_digest']) { exit 26 }
        $claimBytes = $Utf8.GetBytes((ConvertTo-Json -InputObject @{ cutoff_utc = $job['cutoff_utc'] } -Compress))
        try { Write-NewFile $claimPath $claimBytes } catch { exit 29 }

        $target = Get-VerifiedTarget ([Math]::Min(10, $budget)) $directory
        $sent = Invoke-Hermes @('send', '--to', $target.qualified, '--file', $note, '--json') $budget $window
        if ($null -eq $sent) { exit 31 }
        try {
            $native = Read-NativeObject $sent.bytes
            Stop-OnRefusal $native $directory
            $message = Get-NativeValue $native 'message_id' 'string'
            $chat = Get-NativeValue $native 'chat_id' 'string'
            $nativeError = Get-NativeMember $native 'error_code'
            if ($sent.code -ne 0 -or (Get-NativeValue $native 'success' 'boolean') -cne 'true' -or
                ($null -ne $nativeError -and $nativeError.GetAttribute('type') -cne 'null') -or
                (Get-NativeValue $native 'platform' 'string') -cne 'weixin' -or
                !$message -or $message.Length -gt 512 -or $message -match '[\s\p{Cc}]' -or
                ($chat -cne $target.raw -and $chat -cne $target.qualified)) { exit 31 }
        } catch { exit 31 }
        $confirmed = [DateTimeOffset]::UtcNow
        if ($confirmed -lt $sent.started -or $confirmed -ge $window.cutoff) { exit 31 }
        $receipt = [ordered]@{
            protocol = $Protocol; script_sha256 = $ScriptHash; state = 'accepted'; platform = 'weixin'
            target_verified = $true; native_success = $true; chat_verified = $true; native_exit_code = 0
            message_id_sha256 = Get-BytesSha256 ($Utf8.GetBytes($message))
            send_started_at = Format-Utc $sent.started
            confirmed_at = Format-Utc $confirmed
        }
        foreach ($name in $JobKeys) { $receipt.Add($name, $job[$name]) }
        $receiptBytes = Write-AtomicObject $directory 'receipt.json' $receipt
        $null = Write-AtomicObject $directory 'outcome.json' @{
            state = 'accepted'; receipt_sha256 = Get-BytesSha256 $receiptBytes
        }
        Write-Reply 0 @{ sent = $true }
        exit 0
    } catch { exit 27 }
} catch { exit 28 }
finally {
    for ($index = $Pins.Count - 1; $index -ge 0; $index--) { $Pins[$index].Dispose() }
}
