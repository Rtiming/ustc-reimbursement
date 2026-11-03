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
    $stage = 'load_unicode_shell_link'
    # IShellLinkW keeps Chinese paths independent of the Windows ANSI code page.
    # https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-ishelllinkw-setpath
    Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
[ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface UstcShellLinkW {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder value, int count, IntPtr data, uint flags);
    void GetIDList(out IntPtr value);
    void SetIDList(IntPtr value);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder value, int count);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string value);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder value, int count);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string value);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder value, int count);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string value);
    void GetHotkey(out short value);
    void SetHotkey(short value);
    void GetShowCmd(out int value);
    void SetShowCmd(int value);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder value, int count, out int index);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string value, int index);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string value, uint reserved);
    void Resolve(IntPtr window, uint flags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string value);
}
public sealed class UstcShortcutView {
    public string path { get; set; }
    public string target { get; set; }
    public string arguments { get; set; }
    public string working_directory { get; set; }
    public string description { get; set; }
}
public static class UstcShortcut {
    static object NewLink() {
        return Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("00021401-0000-0000-C000-000000000046")));
    }
    public static void Create(string path, string target, string args, string directory, string description) {
        string temporary = Path.Combine(Path.GetDirectoryName(path), ".ustc-link-" + Guid.NewGuid().ToString("N") + ".lnk");
        try {
            object obj = NewLink();
            try {
                UstcShellLinkW link = (UstcShellLinkW)obj;
                link.SetPath(target);
                link.SetArguments(args);
                link.SetWorkingDirectory(directory);
                link.SetDescription(description);
                link.SetShowCmd(7);
                ((IPersistFile)obj).Save(temporary, true);
            } finally { Marshal.FinalReleaseComObject(obj); }
            File.Move(temporary, path); // Two-argument Move never overwrites an existing entry.
        } finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }
    public static UstcShortcutView Read(string path) {
        object obj = NewLink();
        try {
            ((IPersistFile)obj).Load(path, 0);
            UstcShellLinkW link = (UstcShellLinkW)obj;
            StringBuilder target = new StringBuilder(32768), args = new StringBuilder(32768);
            StringBuilder directory = new StringBuilder(32768), description = new StringBuilder(8192);
            link.GetPath(target, target.Capacity, IntPtr.Zero, 4);
            link.GetArguments(args, args.Capacity);
            link.GetWorkingDirectory(directory, directory.Capacity);
            link.GetDescription(description, description.Capacity);
            return new UstcShortcutView {path=path, target=target.ToString(), arguments=args.ToString(),
                working_directory=directory.ToString(), description=description.ToString()};
        } finally { Marshal.FinalReleaseComObject(obj); }
    }
}
'@
    if ($spec.action -eq 'create') {
        if (Test-Path -LiteralPath $spec.path) { throw 'Existing shortcut is preserved' }
        $stage = 'create_unicode_shortcut'
        [UstcShortcut]::Create($spec.path, $spec.target, $spec.arguments, $spec.working_directory, $spec.description)
    }
    $stage = 'read_unicode_shortcut'
    Write-Utf8Json ([UstcShortcut]::Read($spec.path))

} catch {
    throw "Shortcut step=$stage HRESULT=$($_.Exception.HResult): $($_.Exception.Message) $($_.InvocationInfo.PositionMessage) $($_.Exception.InnerException)"
}
