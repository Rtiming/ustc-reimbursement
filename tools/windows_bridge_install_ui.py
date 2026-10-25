"""仅安装本项目Edge扩展的原生UI Automation向导；不操作财务页面。"""
import argparse
import base64
import ctypes
from ctypes import wintypes as wt
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile
import uuid

from windows_edge_bridge import ROOT, STATE, atomic_private
from windows_bridge_setup import capabilities, extension_id, install as install_host

RECEIPT = STATE/'installation-ui.json'
USER = ctypes.WinDLL('user32', use_last_error=True) if os.name == 'nt' else None
if USER:
    USER.GetForegroundWindow.restype = wt.HWND
    USER.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    USER.SetForegroundWindow.argtypes = [wt.HWND]
    USER.IsWindow.argtypes = [wt.HWND]
    USER.GetWindow.argtypes = [wt.HWND, wt.UINT]
    USER.GetWindow.restype = wt.HWND
    USER.GetLastActivePopup.argtypes = [wt.HWND]
    USER.GetLastActivePopup.restype = wt.HWND
    USER.GetDlgCtrlID.argtypes = [wt.HWND]
    USER.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    USER.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    USER.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_size_t)]
    USER.SendMessageTimeoutW.restype = wt.LPARAM
    USER.IsWindowEnabled.argtypes = [wt.HWND]
    USER.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
    USER.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]


def powershell(source):
    encoded = base64.b64encode(source.encode('utf-16le')).decode('ascii')
    completed = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=25)
    if completed.returncode:
        raise RuntimeError(completed.stderr.decode('utf-8', errors='replace')[:1200])
    return json.loads(completed.stdout.decode('utf-8-sig'))


def windows():
    return powershell(r'''$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$rows=@([System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition) | ForEach-Object {
 if($_.Current.ClassName -eq 'Chrome_WidgetWin_1') {
  $p=Get-Process -Id $_.Current.ProcessId
  if($p.ProcessName -eq 'msedge') { [pscustomobject]@{handle=$_.Current.NativeWindowHandle;name=$_.Current.Name;pid=$_.Current.ProcessId;executable=$p.Path} }
 }
})
ConvertTo-Json -InputObject $rows -Compress''')


def foreground():
    hwnd, pid = USER.GetForegroundWindow(), wt.DWORD()
    USER.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return {'handle': hwnd, 'pid': pid.value}


def load_receipt():
    return json.loads(RECEIPT.read_text(encoding='utf-8'))


def manager():
    record = load_receipt()
    candidates = [w for w in windows() if w['handle'] == record['manager_handle'] and w['pid'] == record['edge_pid']]
    if len(candidates) != 1 or not any(x in candidates[0]['name'] for x in ['扩展', 'Extensions']):
        raise RuntimeError('本次扩展管理窗口不再匹配；不操作其他窗口')
    return record, candidates[0]


def navigate_manager(record, window):
    source = r'''$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]HANDLE)
$edits=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants, (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Edit))) | Where-Object {$_.Current.Name -in @('地址和搜索栏','Address and search bar') -or $_.Current.AutomationId -eq 'addressEditBox'})
if($edits.Count -ne 1) { throw 'Unique Edge address bar unavailable' }
$address=$edits[0]
$value=$address.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
$value.SetValue('edge://extensions/')
$address.SetFocus()
$focused=[System.Windows.Automation.AutomationElement]::FocusedElement
if($focused.Current.ProcessId -ne $root.Current.ProcessId -or $focused.Current.ControlType -ne [System.Windows.Automation.ControlType]::Edit) { throw 'Address focus was not verified' }
[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
@{fixed_url='edge://extensions/';address_set=$true}|ConvertTo-Json -Compress'''.replace('HANDLE', str(window['handle']))
    result = powershell(source)
    record.update(manager_handle=window['handle'], phase='manager_navigation_requested')
    record['events'].append(result)
    atomic_private(RECEIPT, record)
    deadline = time.monotonic()+8
    while time.monotonic()<deadline:
        seen = [w for w in windows() if w['handle']==window['handle'] and any(x in w['name'] for x in ['扩展','Extensions'])]
        if seen:
            record['phase']='manager_open'
            atomic_private(RECEIPT, record)
            return record
        time.sleep(.15)
    raise RuntimeError('扩展管理页导航未确认；不操作页面控件')


def prepare_manager():
    if capabilities()['bridge_connected']:
        return {'already_connected': True, 'ui_used': False}
    if RECEIPT.exists():
        old = load_receipt()
        if old.get('manager_handle') and USER.IsWindow(old['manager_handle']):
            if old['phase'] in ['manager_window_created', 'manager_navigation_requested']:
                target = next(w for w in windows() if w['handle']==old['manager_handle'] and w['pid']==old['edge_pid'])
                return navigate_manager(old, target)
            record, _ = manager()
            return record
        if old.get('phase') == 'open_manager_intent':
            added = [w for w in windows() if w['pid']==old['edge_pid'] and w['handle'] not in old['original_window_handles']]
            if len(added)==1:
                old.update(manager_handle=added[0]['handle'], phase='manager_window_created')
                atomic_private(RECEIPT, old)
                return navigate_manager(old, added[0])
            raise RuntimeError('原开窗意图无法绑定唯一新窗口；不重复开窗')
    before = windows()
    if not before:
        raise RuntimeError('现有Edge未运行；不自动启动新profile')
    # 当前前台属于Edge时选同一浏览器，否则只允许唯一现有Edge进程。
    original = foreground()
    candidates = [w for w in before if w['pid'] == original['pid']] or before
    identities = {(w['pid'], w['executable']) for w in candidates}
    if len(identities) != 1:
        raise RuntimeError('存在多个Edge渠道/进程；不猜目标浏览器')
    pid, executable = next(iter(identities))
    record = {'schema': 'ustc-edge-extension-install-ui-v1', 'system_write': False,
              'finance_page_operated': False, 'extension_id': extension_id(), 'edge_pid': pid,
              'edge_executable': executable, 'original_foreground': original,
              'original_window_handles': [w['handle'] for w in before], 'manager_handle': None,
              'phase': 'open_manager_intent', 'events': []}
    atomic_private(RECEIPT, record)
    subprocess.Popen([executable, '--new-window', 'edge://extensions/'], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic()+12
    while time.monotonic()<deadline:
        added = [w for w in windows() if w['pid'] == pid and w['handle'] not in record['original_window_handles']]
        if len(added) == 1:
            record.update(manager_handle=added[0]['handle'], phase='manager_window_created')
            atomic_private(RECEIPT, record)
            return navigate_manager(record, added[0])
        time.sleep(.2)
    raise RuntimeError('未确认新扩展管理窗口；保留打开意图，不重复开窗')


def inspect_manager():
    record, window = manager()
    source = r'''$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]HANDLE)
$document=$root.FindFirst([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Document)))
$rows=@($document.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition) | ForEach-Object {
 if($_.Current.Name -or $_.Current.AutomationId) {
  [pscustomobject]@{name=$_.Current.Name;type=$_.Current.ControlType.ProgrammaticName;automation_id=$_.Current.AutomationId;class=$_.Current.ClassName;enabled=$_.Current.IsEnabled}
 }
})
ConvertTo-Json -InputObject $rows -Depth 4 -Compress'''.replace('HANDLE', str(window['handle']))
    rows = powershell(source)
    return {'manager_handle': window['handle'], 'rows': rows}


def capture_manager():
    """只抓本次安装窗口；PrintWindow避免普通屏幕裁剪混入遮挡它的其他应用。"""
    record, window=manager()
    path=Path(tempfile.gettempdir())/('codex-edge-install-'+uuid.uuid4().hex+'.png')
    source=r'''$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class InstallCapture { [StructLayout(LayoutKind.Sequential)] public struct Rect {public int L,T,R,B;} [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out Rect r); [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint f); }'
$r=New-Object InstallCapture+Rect
[void][InstallCapture]::GetWindowRect([IntPtr]HANDLE,[ref]$r)
$b=New-Object System.Drawing.Bitmap(($r.R-$r.L),($r.B-$r.T))
$g=[System.Drawing.Graphics]::FromImage($b);$dc=$g.GetHdc()
try { $ok=[InstallCapture]::PrintWindow([IntPtr]HANDLE,$dc,2) } finally {$g.ReleaseHdc($dc);$g.Dispose()}
if(-not $ok) {$b.Dispose();throw 'PrintWindow unavailable'}
$path=[System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('PATH_B64'))
$b.Save($path,[System.Drawing.Imaging.ImageFormat]::Png);$b.Dispose()
@{path=$path;installation_window_only=$true}|ConvertTo-Json -Compress'''.replace('HANDLE',str(window['handle'])).replace('PATH_B64',base64.b64encode(str(path).encode('utf-8')).decode('ascii'))
    return powershell(source)


def load_extension():
    record, window = manager()
    source = r'''$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]HANDLE)
$doc=$root.FindFirst([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Document)))
function Elements { @($doc.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)) }
$loads=@(Elements | Where-Object {$_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.Name -in @('加载解压缩的扩展','加载解压缩','Load unpacked')})
$developerChanged=$false
if($loads.Count -eq 0) {
 $toggle=@(Elements | Where-Object {$_.Current.AutomationId -eq 'dev-switch'})
 if($toggle.Count -ne 1 -or -not $toggle[0].Current.IsEnabled) {throw 'Developer switch unavailable or controlled by policy'}
 $state=$toggle[0].GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
 if($state.Current.ToggleState -ne [System.Windows.Automation.ToggleState]::On) { $state.Toggle(); $developerChanged=$true }
 Start-Sleep -Milliseconds 300
}
$matches=@(Elements | Where-Object {$_.Current.Name.Contains('EXTENSION_ID')})
if($matches.Count -gt 0) { @{already_installed=$true;developer_mode_changed=$developerChanged}|ConvertTo-Json -Compress; exit }
$loads=@(Elements | Where-Object {$_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.Name -in @('加载解压缩的扩展','加载解压缩','Load unpacked')})
if($loads.Count -ne 1 -or -not $loads[0].Current.IsEnabled) {throw 'Load unpacked unavailable; browser restrictions were not bypassed'}
$loads[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
@{already_installed=$false;developer_mode_changed=$developerChanged;folder_dialog_requested=$true}|ConvertTo-Json -Compress'''.replace('HANDLE', str(window['handle'])).replace('EXTENSION_ID', extension_id())
    result = powershell(source)
    record['events'].append(result)
    record['phase']='extension_already_installed' if result.get('already_installed') else 'folder_dialog_requested'
    atomic_private(RECEIPT, record)
    return result


def reload_installed():
    current=capabilities()
    if current['bridge_connected']:
        return {'already_connected':True,'reloaded':False,'capabilities':current}
    if current.get('active_request_id'):
        raise RuntimeError('仍有原财务请求，安装器不重载扩展')
    record,window=manager()
    source=r'''$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]HANDLE)
$cards=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)|Where-Object {$_.Current.ControlType -eq [System.Windows.Automation.ControlType]::ListItem -and $_.Current.Name.Contains('EXTENSION_ID')})
if($cards.Count -ne 1) {throw 'Exact installed extension card is not unique'}
$buttons=@($cards[0].FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)|Where-Object {$_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.Name -in @('重新加载','Reload')})
if($buttons.Count -ne 1) {throw 'Exact extension reload unavailable'}
$buttons[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
@{same_extension_reloaded=$true}|ConvertTo-Json -Compress'''.replace('HANDLE',str(window['handle'])).replace('EXTENSION_ID',extension_id())
    result=powershell(source)
    record['events'].append(result);record['phase']='same_extension_reloaded'
    atomic_private(RECEIPT,record)
    time.sleep(1)
    return {**result,'capabilities':capabilities()}


def inspect_worker():
    record,window=manager()
    source=r'''$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]HANDLE)
$cards=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)|Where-Object {$_.Current.ControlType -eq [System.Windows.Automation.ControlType]::ListItem -and $_.Current.Name.Contains('EXTENSION_ID')})
if($cards.Count -ne 1) {throw 'Exact installed extension card is not unique'}
$buttons=@($cards[0].FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)|Where-Object {$_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.Name -match '^(服务工作进程|service worker)'})
if($buttons.Count -ne 1) {throw 'Own extension worker inspection unavailable'}
$buttons[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
@{own_worker_inspection_requested=$true}|ConvertTo-Json -Compress'''.replace('HANDLE',str(window['handle'])).replace('EXTENSION_ID',extension_id())
    result=powershell(source)
    record['events'].append(result);record['worker_inspection_requested']=True
    atomic_private(RECEIPT,record)
    time.sleep(.5)
    result['diagnostic_windows']=[w for w in windows() if extension_id() in w['name']]
    return result


def read_worker_console():
    record=load_receipt()
    candidates=[w for w in windows() if w['pid']==record['edge_pid'] and w['handle'] not in [record['manager_handle'],*record['original_window_handles']] and w['name']=='DevTools']
    if len(candidates)!=1:
        raise RuntimeError('本项目刚打开的服务工作进程检查窗口不唯一')
    window=candidates[0]
    source=r'''$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]HANDLE)
$all=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition))
$tabs=@($all|Where-Object {$_.Current.Name -in @('控制台','Console') -and $_.Current.ControlType.ProgrammaticName -in @('ControlType.TabItem','ControlType.Button')})
if($tabs.Count -eq 1) {
 $pattern=$null
 if($tabs[0].TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern,[ref]$pattern)) {$pattern.Select()}
 elseif($tabs[0].TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)) {$pattern.Invoke()}
 Start-Sleep -Milliseconds 200
}
$all=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition))
$rows=@($all|Where-Object {$_.Current.Name -match 'USTC|native|Native|host|Host|控制台|Console|错误|Error|ERROR|not found|message' }|ForEach-Object {[pscustomobject]@{name=$_.Current.Name;type=$_.Current.ControlType.ProgrammaticName}})
ConvertTo-Json -InputObject $rows -Compress'''.replace('HANDLE',str(window['handle']))
    result={'diagnostic_handle':window['handle'],'rows':powershell(source)}
    record['diagnostic_handle']=window['handle']
    atomic_private(RECEIPT,record)
    return result


def folder_dialog():
    record = load_receipt()
    deadline=time.monotonic()+8
    popup = USER.GetLastActivePopup(record['manager_handle'])
    while (not popup or popup==record['manager_handle'] or USER.GetWindow(popup,4)!=record['manager_handle']) and time.monotonic()<deadline:
        time.sleep(.1)
        popup=USER.GetLastActivePopup(record['manager_handle'])
    if not popup or popup == record['manager_handle'] or USER.GetWindow(popup,4) != record['manager_handle']:
        raise RuntimeError('本次管理窗口没有独立的原生选择目录对话框')
    observed = powershell(r'''$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
$e=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]HANDLE)
$p=Get-Process -Id $e.Current.ProcessId
@{handle=$e.Current.NativeWindowHandle;name=$e.Current.Name;class=$e.Current.ClassName;pid=$e.Current.ProcessId;executable=$p.Path}|ConvertTo-Json -Compress'''.replace('HANDLE', str(popup)))
    if observed['class']!='#32770' or observed['executable'].lower()!=record['edge_executable'].lower() or observed['name'] not in ['选择扩展目录。','选择扩展目录','Select the extension directory.','Select Extension Directory']:
        raise RuntimeError('原生对话框身份不符；不填写其他文件选择器')
    return record, observed


def inspect_folder():
    record, dialog = folder_dialog()
    source = r'''$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]HANDLE)
$rows=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition) | Where-Object {$_.Current.ControlType.ProgrammaticName -in @('ControlType.Edit','ControlType.Button','ControlType.ComboBox')} | ForEach-Object {[pscustomobject]@{name=$_.Current.Name;automation_id=$_.Current.AutomationId;type=$_.Current.ControlType.ProgrammaticName;class=$_.Current.ClassName;enabled=$_.Current.IsEnabled}})
ConvertTo-Json -InputObject $rows -Compress'''.replace('HANDLE', str(dialog['handle']))
    return {'dialog':dialog, 'controls':powershell(source)}


def folder_native_controls():
    record, dialog = folder_dialog()
    rows=[]
    callback_type=ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    @callback_type
    def visit(hwnd, _):
        cls=ctypes.create_unicode_buffer(256)
        USER.GetClassNameW(hwnd, cls, len(cls))
        if cls.value in ['Edit','ComboBox','Button']:
            caption=ctypes.create_unicode_buffer(512)
            USER.GetWindowTextW(hwnd,caption,len(caption))
            rows.append({'handle':hwnd,'class':cls.value,'id':USER.GetDlgCtrlID(hwnd),'name':caption.value})
        return True
    USER.EnumChildWindows.argtypes=[wt.HWND,callback_type,wt.LPARAM]
    USER.EnumChildWindows(dialog['handle'],visit,0)
    return {'dialog':dialog,'controls':rows}


def choose_verified_package():
    observed=folder_native_controls()
    record=load_receipt()
    installed=json.loads((STATE/'installation.json').read_text(encoding='utf-8'))
    package=STATE/'package/extension'
    if Path(installed['extension_directory']).resolve()!=package.resolve():
        raise RuntimeError('安装登记目录不匹配')
    for name, digest in installed['source_sha256'].items():
        original=ROOT/'extension'/name
        if hashlib.sha256(original.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('已登记源码SHA不符：'+name)
        if name=='native_bridge.js':
            normalized=(package/name).read_text(encoding='utf-8').replace('const BUILD_SHA = '+json.dumps(installed['build_sha256'])+';', 'const BUILD_SHA = null;')
            matches=normalized==original.read_text(encoding='utf-8')
        else:
            matches=(package/name).read_bytes()==original.read_bytes()
        if not matches:
            raise RuntimeError('安装包源码内容不符：'+name)
    edits=[x for x in observed['controls'] if x['class']=='Edit' and x['id']==1152]
    buttons=[x for x in observed['controls'] if x['class']=='Button' and x['id']==1 and x['name'] in ['选择文件夹','Select Folder','Select folder']]
    if len(edits)!=1 or len(buttons)!=1:
        raise RuntimeError('固定文件夹输入与确认控件不唯一')
    record.update(phase='package_selection_intent', selected_package=str(package), selected_build_sha256=installed['build_sha256'])
    atomic_private(RECEIPT,record)
    # BM_CLICK对非活动原生对话框可能无效；本安装器已获临时前台授权。
    prior=foreground()
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentThreadId.restype=wt.DWORD
    current_thread=kernel.GetCurrentThreadId()
    focused_thread=USER.GetWindowThreadProcessId(prior['handle'],None)
    linked=bool(USER.AttachThreadInput(current_thread,focused_thread,True)) if focused_thread!=current_thread else False
    try:
        USER.SetForegroundWindow(observed['dialog']['handle'])
    finally:
        if linked: USER.AttachThreadInput(current_thread,focused_thread,False)
    focus_deadline=time.monotonic()+2
    while foreground()['handle']!=observed['dialog']['handle'] and time.monotonic()<focus_deadline:
        time.sleep(.05)
    if foreground()['handle']!=observed['dialog']['handle']:
        # 固定原生对话框支持IDOK消息；不向未确认前台发送键盘输入。
        pass
    # 该原生目录对话框未公开Value/Invoke模式；仅对已核定1152/1控件发标准消息。
    buffer=ctypes.create_unicode_buffer(str(package)); returned=ctypes.c_size_t()
    if not USER.SendMessageTimeoutW(edits[0]['handle'],0x000C,0,ctypes.cast(buffer,ctypes.c_void_p).value,2,2000,ctypes.byref(returned)):
        raise RuntimeError('固定目录输入控件未确认写入')
    readback=ctypes.create_unicode_buffer(2048)
    if not USER.SendMessageTimeoutW(edits[0]['handle'],0x000D,len(readback),ctypes.cast(readback,ctypes.c_void_p).value,2,2000,ctypes.byref(returned)):
        raise RuntimeError('原生目录文本回读失败')
    if readback.value!=str(package) or not USER.IsWindowEnabled(buttons[0]['handle']):
        raise RuntimeError('目录回读或选择文件夹按钮状态不符；未确认')
    if not USER.SendMessageTimeoutW(observed['dialog']['handle'],0x0111,1,buttons[0]['handle'],2,3000,ctypes.byref(returned)):
        raise RuntimeError('原生目录选择响应未确认；先查安装状态，不重复加载')
    result={'directory_readback_verified':True,'selection_invoked':True,'native_dialog_control_ids':[1152,1]}
    record['events'].append(result)
    record['phase']='package_selection_requested'
    atomic_private(RECEIPT,record)
    deadline=time.monotonic()+12
    while time.monotonic()<deadline:
        current=capabilities()
        if current['bridge_connected']:
            record.update(phase='bridge_connected', capabilities=current)
            atomic_private(RECEIPT,record)
            return record
        time.sleep(.2)
    return {'phase':'connection_not_yet_observed','capabilities':capabilities(),'receipt':str(RECEIPT)}


def finish():
    record, window = manager()
    original = record['original_foreground']
    current_pid = wt.DWORD()
    USER.GetWindowThreadProcessId(original['handle'], ctypes.byref(current_pid))
    before = foreground()
    owned = before['handle'] == record['manager_handle'] or USER.GetWindow(before['handle'],4) == record['manager_handle']
    if owned and USER.IsWindow(original['handle']) and current_pid.value == original['pid']:
        requested = bool(USER.SetForegroundWindow(original['handle']))
        time.sleep(.1)
    else:
        requested = False
    record.update(restoration_requested=requested, user_foreground_preserved=not owned, after_foreground=foreground(),
                  capabilities=capabilities())
    atomic_private(RECEIPT, record)
    return record


def close_installation_windows():
    record=load_receipt()
    before=foreground(); current=windows()
    originals={w['handle'] for w in current}.intersection(record['original_window_handles'])
    targets=[record.get('diagnostic_handle'),record.get('manager_handle')]
    closed=[]
    if not originals:
        record['cleanup']='original_edge_window_missing_preserved_new_window'
        atomic_private(RECEIPT,record)
        return record
    for handle in targets:
        rows=[w for w in current if w['handle']==handle and w['pid']==record['edge_pid'] and handle not in record['original_window_handles']]
        if not rows:
            continue
        valid=(handle==record.get('diagnostic_handle') and rows[0]['name']=='DevTools') or (handle==record.get('manager_handle') and any(x in rows[0]['name'] for x in ['扩展','Extensions']))
        if not valid:
            continue
        if USER.PostMessageW(handle,0x0010,0,0):
            closed.append(handle)
    deadline=time.monotonic()+5
    while any(USER.IsWindow(handle) for handle in closed) and time.monotonic()<deadline:
        time.sleep(.1)
    original=record['original_foreground']
    if before['handle'] in targets and USER.IsWindow(original['handle']):
        USER.SetForegroundWindow(original['handle'])
        time.sleep(.1)
    after=foreground()
    record.update(phase='installation_complete' if capabilities()['bridge_connected'] else 'installation_needs_diagnosis',
                  closed_installation_handles=[h for h in closed if not USER.IsWindow(h)],
                  original_edge_windows_preserved=all(USER.IsWindow(h) for h in originals),
                  after_foreground=after, foreground_restored_or_user_preserved=(after==original or before==after),
                  capabilities=capabilities())
    atomic_private(RECEIPT,record)
    return record


def install():
    current=capabilities()
    if current['bridge_connected']:
        return {'ok':True,'already_connected':True,'ui_used':False,'capabilities':current}
    install_host()
    prepare_manager()
    try:
        observation=load_extension()
        if observation.get('already_installed'):
            reload_installed()
        else:
            choose_verified_package()
        deadline=time.monotonic()+10
        while not capabilities()['bridge_connected'] and time.monotonic()<deadline:
            time.sleep(.2)
        current=capabilities()
        return {'ok':current['bridge_connected'],'receipt':str(RECEIPT),'capabilities':current}
    finally:
        if capabilities()['bridge_connected']:
            close_installation_windows()
        else:
            finish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install','prepare-manager', 'inspect-manager', 'capture-manager', 'load-extension', 'reload-installed', 'inspect-worker', 'read-worker-console', 'inspect-folder', 'inspect-folder-native', 'choose-package', 'finish','close-installation-windows'])
    args = parser.parse_args()
    if os.name != 'nt':
        raise ValueError('仅用于Windows现有Edge的本项目扩展安装')
    result = {'install':install,'prepare-manager': prepare_manager, 'inspect-manager': inspect_manager, 'capture-manager':capture_manager, 'load-extension':load_extension, 'reload-installed':reload_installed, 'inspect-worker':inspect_worker, 'read-worker-console':read_worker_console, 'inspect-folder':inspect_folder, 'inspect-folder-native':folder_native_controls, 'choose-package':choose_verified_package, 'finish': finish,'close-installation-windows':close_installation_windows}[args.action]()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
