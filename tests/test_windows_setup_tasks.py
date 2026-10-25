"""Windows固定安装任务的请求去重、冻结源和不重放；不运行安装器或财务页面。"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import windows_bridge_tasks as tasks
from platform_runtime import windows_bridge_install_lock


class TaskInputTests(unittest.TestCase):
    def test_request_id_cannot_select_paths_or_commands(self):
        for request in ('../other', '', 'x;anything', 'a' * 97, None):
            with self.assertRaises(ValueError): tasks.folder_for(request)
        self.assertEqual(tasks.folder_for('setup-1'), tasks.folder_for('setup-1'))

    def test_result_for_other_user_is_rejected_before_cleanup_or_result_read(self):
        from assistant_api import atomic_json
        with tempfile.TemporaryDirectory() as temp, patch.object(tasks, 'STATE', Path(temp).resolve()), \
                patch.object(tasks, 'current_user_sid', return_value='SYNTHETIC-USER-B'), \
                patch.object(tasks, 'powershell') as cleanup:
            folder = tasks.folder_for('setup-1')
            atomic_json(folder / 'request.json', {'root': str(tasks.ROOT), 'request_id': 'setup-1',
                        'user_sid': 'SYNTHETIC-USER-A', 'task': {}})
            atomic_json(folder / 'result.json', {'ok': True})
            with self.assertRaisesRegex(ValueError, '不属于当前用户'):
                tasks.result('setup-1')
            cleanup.assert_not_called()


@unittest.skipUnless(os.name == 'nt', 'Windows原生互斥及本地启动合同')
class WindowsSetupTasks(unittest.TestCase):
    def test_native_install_mutex_blocks_a_second_process_without_stopping_it(self):
        command = [sys.executable, '-c', 'import sys;sys.path.insert(0,sys.argv[1]);from platform_runtime import windows_bridge_install_lock\nwith windows_bridge_install_lock(): print("entered")', str(Path(__file__).resolve().parents[1] / 'tools')]
        with windows_bridge_install_lock():
            blocked = subprocess.run(command, capture_output=True, timeout=10)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertNotIn(b'entered', blocked.stdout)
        allowed = subprocess.run(command, capture_output=True, timeout=10)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_unknown_schedule_is_retained_and_same_id_cannot_dispatch_twice(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(tasks, 'STATE', Path(temp).resolve()), \
                patch.object(tasks, 'verify_members', return_value={'source_tree_sha256': 'b' * 64}), \
                patch('windows_bridge_setup.registration_snapshot', return_value={'registration_sha256': 'a' * 64}), \
                patch.object(tasks, 'powershell', side_effect=RuntimeError('synthetic unknown')) as launch:
            with self.assertRaises(RuntimeError): tasks.start('setup-1', 'a' * 64)
            first = (tasks.folder_for('setup-1') / 'request.json').read_bytes()
            result = tasks.start('setup-1', 'a' * 64)
            self.assertEqual(result['state'], 'setup_running_or_unknown')
            self.assertEqual(launch.call_count, 1)
            self.assertEqual((tasks.folder_for('setup-1') / 'request.json').read_bytes(), first)
            with self.assertRaises(ValueError): tasks.start('setup-1', 'c' * 64)
