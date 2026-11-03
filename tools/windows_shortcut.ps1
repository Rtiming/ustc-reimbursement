param([Parameter(Mandatory=$true)][string]$SpecPath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$spec = Get-Content -LiteralPath $SpecPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($spec.action -eq 'desktop') {
    @{path=[Environment]::GetFolderPath('DesktopDirectory')} | ConvertTo-Json -Compress
    exit 0
}
if ($spec.action -notin @('inspect','create')) { throw 'Unsupported shortcut action' }
if (-not [IO.Path]::IsPathRooted($spec.path) -or [IO.Path]::GetExtension($spec.path) -ne '.lnk') { throw 'Absolute .lnk path required' }
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($spec.path)
if ($spec.action -eq 'create') {
    if (Test-Path -LiteralPath $spec.path) { throw 'Existing shortcut is preserved' }
    $link.TargetPath = $spec.target
    $link.Arguments = $spec.arguments
    $link.WorkingDirectory = $spec.working_directory
    $link.Description = $spec.description
    $link.WindowStyle = 7
    $link.Save()
    $link = $shell.CreateShortcut($spec.path)
}
@{path=$spec.path; target=$link.TargetPath; arguments=$link.Arguments; working_directory=$link.WorkingDirectory; description=$link.Description} | ConvertTo-Json -Compress
