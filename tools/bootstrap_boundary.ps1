param([Parameter(Mandatory=$true)][string]$ToolRoot)
$ErrorActionPreference = 'Stop'
$stateRoot = Join-Path $ToolRoot '.local'
$stateEntries = @(Get-ChildItem -LiteralPath $ToolRoot -Force | Where-Object { $_.Name -eq '.local' })
if ($stateEntries.Count -gt 1) { throw 'Ambiguous runtime directory / 运行目录名称不唯一' }
if ($stateEntries.Count -eq 0) { return }
if (($stateEntries[0].Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw '.local cannot be a reparse point / .local不能是重解析点' }

# uv 0.13.x creates a minor-version alias pointing to the pinned full version.
# Allow only these exact local pairs; never traverse aliases or general links.
$aliases = @{}
$architectures = @{ 'windows-x86_64' = 'x86_64'; 'windows-arm64' = 'aarch64' }
$pythonRoot = Join-Path $stateRoot 'python'
foreach ($row in Import-Csv -LiteralPath (Join-Path $ToolRoot 'distribution\bootstrap.tsv') -Delimiter "`t") {
    if (-not $architectures.ContainsKey($row.platform)) { continue }
    if ($row.python -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid pinned Python version' }
    $minor = ([Version]$row.python).ToString(2)
    $arch = $architectures[$row.platform]
    $alias = Join-Path $pythonRoot "cpython-$minor-windows-$arch-none"
    if ($aliases.ContainsKey($alias)) { throw 'Ambiguous pinned Python alias' }
    $aliases[$alias] = Join-Path $pythonRoot "cpython-$($row.python)-windows-$arch-none"
}
function Test-FrozenPythonAlias($entry) {
    if (-not $entry.PSIsContainer -or $entry.LinkType -notin @('Junction', 'SymbolicLink')) { return $false }
    if (-not $aliases.ContainsKey($entry.FullName)) { return $false }
    $targets = @($entry.Target)
    if ($targets.Count -ne 1) { return $false }
    $raw = [string]$targets[0]
    # No UNC/device paths, relative targets, alternate destinations or chains.
    if ($raw -notmatch '^[A-Za-z]:\\' -or -not [string]::Equals($raw.TrimEnd('\'), $aliases[$entry.FullName], [StringComparison]::OrdinalIgnoreCase)) { return $false }
    try { $target = Get-Item -LiteralPath $aliases[$entry.FullName] -Force -ErrorAction Stop }
    catch { return $false }
    return ($target.PSIsContainer -and (($target.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0))
}
function Assert-RuntimeEntry($entry) {
    if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        if (Test-FrozenPythonAlias $entry) { return $false }
        throw ('Runtime reparse point is not allowed / 拒绝非固定运行链接，保留原状: ' + $entry.FullName + ' [' + $entry.LinkType + '] -> ' + ($entry.Target -join ', '))
    }
    return $true
}
$pending = New-Object 'System.Collections.Generic.Stack[string]'
$pending.Push($stateRoot)
while ($pending.Count -gt 0) {
    $item = Get-Item -LiteralPath $pending.Pop() -Force
    if (-not (Assert-RuntimeEntry $item)) { continue }
    if ($item.PSIsContainer) {
        foreach ($child in Get-ChildItem -LiteralPath $item.FullName -Force) {
            if (-not (Assert-RuntimeEntry $child)) { continue }
            if ($child.PSIsContainer) { $pending.Push($child.FullName) }
        }
    } elseif ($item.FullName -eq $stateRoot) { throw '.local must be a directory / .local须为目录' }
}
