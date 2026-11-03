param([Parameter(Mandatory=$true)][string]$ToolRoot, [Parameter(Mandatory=$true)][string]$Platform)
$ErrorActionPreference = 'Stop'
$offlineRoot = Join-Path ([IO.Path]::GetFullPath($ToolRoot)) 'offline'
function Get-OfflineSha([string]$path) {
    $stream = [IO.File]::OpenRead($path)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hasher.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $hasher.Dispose() }
}
function Assert-OfflineMember([string]$name) {
    if ([string]::IsNullOrWhiteSpace($name) -or $name.Contains('\') -or $name.Contains(':') -or $name.StartsWith('/') -or @($name.Split('/') | Where-Object { $_ -in @('', '.', '..') }).Count) { throw 'Invalid offline member path' }
    $target = [IO.Path]::GetFullPath((Join-Path $offlineRoot $name))
    if (-not $target.StartsWith($offlineRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Offline member is outside its package' }
    $cursor = $target
    while ($cursor.Length -ge $offlineRoot.Length) {
        $entry = Get-Item -LiteralPath $cursor -Force
        if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Offline members cannot be links' }
        $cursor = Split-Path -Parent $cursor
    }
    return $target
}
$manifestPath = Assert-OfflineMember 'manifest.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.schema -ne 'ustc-offline-runtime-v1' -or $manifest.platform -ne $Platform -or $Platform -ne 'windows-x86_64') { throw 'Offline runtime platform mismatch' }
$pin = @(Import-Csv -LiteralPath (Join-Path $ToolRoot 'distribution\bootstrap.tsv') -Delimiter "`t" | Where-Object { $_.platform -eq $Platform })
if ($pin.Count -ne 1 -or $pin[0].python -ne $manifest.python_version) { throw 'Offline Python version mismatch' }
$locks = @('requirements-mcp.lock.txt', 'requirements-materials.lock.txt', 'requirements-ocr.lock.txt')
foreach ($name in $locks) {
    if ((Get-OfflineSha (Join-Path $ToolRoot $name)) -ne $manifest.requirements.$name) { throw 'Offline dependencies do not match this release. No network fallback was attempted.' }
}
$seen = @{}
Write-Host 'Verifying offline runtime and dependencies...'
foreach ($row in $manifest.files) {
    if ($seen.ContainsKey($row.path)) { throw 'Duplicate offline member' }
    $seen[$row.path] = $true
    $target = Assert-OfflineMember $row.path
    if ((Get-Item -LiteralPath $target).Length -ne $row.size -or (Get-OfflineSha $target) -ne $row.sha256) { throw ('Offline member checksum failed: ' + $row.path) }
}
if (-not $seen.ContainsKey('python/python.exe')) { throw 'Offline Python is missing' }
$wheelCount = 0
foreach ($wheel in Get-ChildItem -LiteralPath (Join-Path $offlineRoot 'wheels') -File -Force) {
    if (-not $seen.ContainsKey('wheels/' + $wheel.Name)) { throw 'Unregistered offline dependency' }
    $wheelCount++
}
if ($wheelCount -eq 0) { throw 'Offline dependencies are missing' }
return (Join-Path $offlineRoot 'python\python.exe')
