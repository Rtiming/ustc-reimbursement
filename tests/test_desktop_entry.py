"""实际Windows .lnk创建、只读核验及用户原入口保护；不启动浏览器。"""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from desktop_entry import install,shell


@unittest.skipUnless(os.name=='nt','Windows快捷方式实测')
class DesktopEntryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)/'中文 工具';self.desktop=Path(temp.name)/'桌面'
        (self.root/'.local/mcp-venv/Scripts').mkdir(parents=True);(self.root/'tools').mkdir();self.desktop.mkdir()
        (self.root/'.local/mcp-venv/Scripts/pythonw.exe').write_bytes(b'')
        (self.root/'tools/operator_workbench.py').write_text('# synthetic',encoding='utf-8')

    def test_actual_link_readback_repeat_and_missing_repair(self):
        first=install(self.root,desktop_dir=self.desktop);second=install(self.root,check_only=True)
        self.assertEqual(first['sha256'],second['sha256']);self.assertFalse(first['browser_started'])
        self.assertIn('中文 工具',second['target']);self.assertIn('--open',second['arguments'])
        Path(first['path']).unlink()
        with self.assertRaises(ValueError):install(self.root,check_only=True)
        restored=install(self.root);self.assertEqual(restored['path'],first['path'])

    def test_foreign_shortcut_is_preserved(self):
        foreign=self.desktop/'报销工作台.lnk'
        shell(self.root,{'action':'create','path':str(foreign),'target':str(self.root/'.local/mcp-venv/Scripts/pythonw.exe'),
                         'arguments':'--foreign','working_directory':str(self.root),'description':'Another user tool'})
        before=foreign.read_bytes();made=install(self.root,desktop_dir=self.desktop)
        self.assertNotEqual(made['path'],str(foreign));self.assertEqual(foreign.read_bytes(),before)


if __name__=='__main__':unittest.main()
