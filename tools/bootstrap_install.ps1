# 仅在当前进程配置路径；不写注册表、不改PowerShell或浏览器安全设置。
$ErrorActionPreference = 'Stop'
# 使用.NET原生SHA256，避免子进程继承PowerShell 7模块路径后缺少Get-FileHash。
function Get-ReimbursementSha([string]$path) {
    $stream = [IO.File]::OpenRead($path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $sha.Dispose() }
}
$toolRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $toolRoot
if ($args.Count -gt 0 -and $args[0] -eq '--check') {
    $python = Join-Path $toolRoot '.local\mcp-venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw '尚未安装；先运行install.cmd。自检不会联网安装。' }
    & $python -X utf8 -I (Join-Path $toolRoot 'tools\portable_install.py') @args
    exit $LASTEXITCODE
}
$cpu = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
$platform = switch ($cpu) { 'ARM64' { 'windows-arm64' } 'AMD64' { 'windows-x86_64' } default { throw '仅支持Windows x64/ARM64' } }
$rows = @(Import-Csv -LiteralPath (Join-Path $toolRoot 'distribution\bootstrap.tsv') -Delimiter "`t" | Where-Object { $_.platform -eq $platform })
if ($rows.Count -ne 1) { throw '冻结的安装工具记录不唯一' }
$row = $rows[0]
if (-not $row.url.StartsWith('https://files.pythonhosted.org/packages/')) { throw '安装工具不属于已核定发布源' }
$bootstrap = Join-Path $toolRoot ".local\bootstrap\$platform"
New-Item -ItemType Directory -Force -Path $bootstrap | Out-Null
$archive = Join-Path $bootstrap "uv-$($row.uv_version).zip"
if (-not (Test-Path $archive) -or (Get-ReimbursementSha $archive) -ne $row.sha256) {
    Invoke-WebRequest -UseBasicParsing -Uri $row.url -OutFile "$archive.part"
    if ((Get-ReimbursementSha "$archive.part") -ne $row.sha256) { throw '下载SHA不符，停止执行' }
    Move-Item -Force -LiteralPath "$archive.part" -Destination $archive
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($archive)
try {
    $member = $zip.GetEntry($row.member)
    if ($null -eq $member) { throw '原包缺少固定的uv成员' }
    [IO.Compression.ZipFileExtensions]::ExtractToFile($member, (Join-Path $bootstrap 'uv.exe'), $true)
} finally { $zip.Dispose() }
Get-ChildItem Env:UV_* | Remove-Item
$env:UV_CACHE_DIR = Join-Path $toolRoot '.local\cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $toolRoot '.local\python'
$env:UV_PYTHON_BIN_DIR = Join-Path $toolRoot '.local\python-bin'
$env:UV_NO_CONFIG = '1'
foreach ($key in @('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','UV_CONFIG_FILE','UV_PYTHON')) { Remove-Item "Env:$key" -ErrorAction SilentlyContinue }
$uv = Join-Path $bootstrap 'uv.exe'
& $uv python install $row.python --no-bin --no-registry --no-config
if ($LASTEXITCODE -ne 0) { throw '独立Python安装失败；保留下载与日志，修复网络后重试' }
$python = (& $uv python find $row.python --managed-python --no-project --no-config | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0 -or -not $python.StartsWith($env:UV_PYTHON_INSTALL_DIR, [StringComparison]::OrdinalIgnoreCase)) { throw '运行时不属于当前工具目录' }
& $python -X utf8 -I (Join-Path $toolRoot 'tools\portable_install.py') @args
exit $LASTEXITCODE
