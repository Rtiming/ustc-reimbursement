"""后台启动的身份/请求冻结/不重放；真正断开MCP由stdio smoke验证。"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from assistant_api import AssistantService, atomic_json, read_json
import assistant_worker_launch as launch


class LaunchFailureTests(unittest.TestCase):
    def test_unknown_launch_retains_original_job_and_cannot_dispatch_twice(self):
        with tempfile.TemporaryDirectory() as temp:
            service = AssistantService(temp, [temp])
            service.new_case('synthetic_case')
            with patch('assistant_api.launch_worker', side_effect=launch.WorkerLaunchUnknown('synthetic uncertainty')) as worker:
                first = service.enqueue('synthetic_case', 'check', {}, 'launch-1')
                same = service.enqueue('synthetic_case', 'check', {}, 'launch-1')
            self.assertEqual(worker.call_count, 1)
            self.assertEqual(first['job_id'], same['job_id'])
            self.assertTrue(first['recovery_required'])
            self.assertTrue(same['reused_existing_job'])
            self.assertEqual(same['status'], 'unknown')


@unittest.skipUnless(os.name == 'nt', 'Windows WMI adapter')
class WindowsLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root/'.local/assistant_jobs/synthetic_case'/('j_'+'a'*24)/'request.json'
        self.value = {'workspace':str(self.root),'case_id':'synthetic_case','operation':'check'}
        atomic_json(self.path, self.value)

    def test_fixed_wmi_command_uses_json_input_hidden_window_and_selected_environment(self):
        response = subprocess.CompletedProcess([], 0, b'{"return_value":0,"pid":4321}', b'')
        with patch.dict(os.environ, {'SYNTHETIC_PASSWORD':'must-not-forward'}), patch.object(launch.subprocess, 'run', return_value=response) as call:
            worker = launch.launch_worker(sys.executable, self.path, cwd=ROOT)
        self.assertEqual(worker.pid, 4321)
        arguments = call.call_args.args[0]
        self.assertNotIn(str(self.path), arguments)
        payload = json.loads(call.call_args.kwargs['input'])
        self.assertNotIn('must-not-forward', json.dumps(payload))
        self.assertIn('assistant_worker_launch.py', payload['command_line'])
        self.assertIn(hashlib.sha256(self.path.read_bytes()).hexdigest(), payload['command_line'])
        self.assertIn('ShowWindow=[uint16]0', launch._WMI_LAUNCH)
        self.assertIn('0x09000200', launch._WMI_LAUNCH)
        self.assertEqual(call.call_args.kwargs['timeout'], 60)

    def test_launcher_stage_error_is_unknown_without_replay_or_sensitive_message(self):
        response = subprocess.CompletedProcess([], 1, b'{"launcher_error":true,"stage":"startup","error_type":"System.Exception"}', b'private diagnostics')
        with patch.object(launch.subprocess, 'run', return_value=response) as call:
            with self.assertRaisesRegex(launch.WorkerLaunchUnknown, 'startup:System.Exception') as error:
                launch.launch_worker(sys.executable, self.path, cwd=ROOT)
        self.assertNotIn('private diagnostics', str(error.exception))
        self.assertEqual(call.call_count, 1)

    def test_wmi_timeout_is_unknown_and_never_falls_back_to_child_process(self):
        with patch.object(launch.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixed launcher',20)) as call:
            with self.assertRaises(launch.WorkerLaunchUnknown):
                launch.launch_worker(sys.executable, self.path, cwd=ROOT)
        self.assertEqual(call.call_count, 1)

    def test_wmi_explicit_rejection_is_not_reported_as_started(self):
        response = subprocess.CompletedProcess([], 0, b'{"return_value":2,"pid":0}', b'')
        with patch.object(launch.subprocess, 'run', return_value=response):
            with self.assertRaisesRegex(RuntimeError, '已拒绝'):
                launch.launch_worker(sys.executable, self.path, cwd=ROOT)

    def test_worker_consumes_exact_bytes_once_under_same_user(self):
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with patch('assistant_job.run') as run:
            launch.run_frozen(self.path, digest, launch.current_user_sid())
            self.assertEqual(run.call_args.kwargs['frozen_request'], self.value)
            with self.assertRaises(FileExistsError):
                launch.run_frozen(self.path, digest, launch.current_user_sid())
        self.assertEqual(run.call_count, 1)
        self.assertEqual(read_json(self.path.parent/'worker_claim.json')['request_sha256'], digest)

    def test_changed_request_or_user_stops_before_execution(self):
        with patch('assistant_job.run') as run:
            with self.assertRaisesRegex(ValueError, 'request_changed'):
                launch.run_frozen(self.path, '0'*64, launch.current_user_sid())
            with self.assertRaisesRegex(ValueError, 'identity_mismatch'):
                launch.run_frozen(self.path, hashlib.sha256(self.path.read_bytes()).hexdigest(), 'S-1-5-21-synthetic')
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
