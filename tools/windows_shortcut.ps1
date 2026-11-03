param([Parameter(Mandatory=$true)][string]$SpecPath)
$ErrorActionPreference = 'Stop'
function Write-Utf8Json($Value) {
    $bytes = [Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Compress) + [Environment]::NewLine)
    $stream = [Console]::OpenStandardOutput()
    $stream.Write($bytes, 0, $bytes.Length)
    $stream.Flush()
}
$stage = 'read_spec'
try {
    $spec = Get-Content -LiteralPath $SpecPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($spec.action -eq 'desktop') {
        $stage = 'desktop_directory'
        Write-Utf8Json @{path=[Environment]::GetFolderPath('DesktopDirectory')}
        exit 0
    }
    if ($spec.action -notin @('inspect','create')) { throw 'Unsupported shortcut action' }
    if (-not [IO.Path]::IsPathRooted($spec.path) -or [IO.Path]::GetExtension($spec.path) -ne '.lnk') { throw 'Absolute .lnk path required' }
    $stage = 'create_shell'
    $shell = New-Object -ComObject WScript.Shell
    $stage = 'open_shortcut'
    $link = $shell.CreateShortcut($spec.path)
    if ($spec.action -eq 'create') {
        if (Test-Path -LiteralPath $spec.path) { throw 'Existing shortcut is preserved' }
        $stage = 'set_target'
        $link.TargetPath = $spec.target
        $stage = 'set_arguments'
        $link.Arguments = $spec.arguments
        $stage = 'set_working_directory'
        $link.WorkingDirectory = $spec.working_directory
        $stage = 'set_description'
        $link.Description = $spec.description
        $stage = 'set_window_style'
        $link.WindowStyle = 7
        $stage = 'save_shortcut'
        $link.Save()
        $stage = 'read_shortcut'
        $link = $shell.CreateShortcut($spec.path)
    }
    $stage = 'write_readback'
    Write-Utf8Json @{path=$spec.path; target=$link.TargetPath; arguments=$link.Arguments; working_directory=$link.WorkingDirectory; description=$link.Description}
} catch {
    throw "Shortcut step=$stage HRESULT=$($_.Exception.HResult): $($_.Exception.Message) $($_.InvocationInfo.PositionMessage) $($_.Exception.InnerException)"
}
