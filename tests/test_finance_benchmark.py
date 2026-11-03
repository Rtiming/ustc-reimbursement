import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from assistant_api import atomic_json
from finance_benchmark import run, statistics, validate_targets


class MetricsTests(unittest.TestCase):
    def test_nearest_rank_uses_all_samples_and_does_not_hide_failure(self):
        samples = [{'ok': True, 'elapsed_seconds': n / 10} for n in range(1, 21)]
        result = statistics(samples, 20, 5)
        self.assertEqual(result['p50_seconds'], 1)
        self.assertEqual(result['p95_seconds'], 1.9)
        self.assertTrue(result['target_met'])
        for modified in (samples[:19], samples + [{'ok': False}],
                         [{**s, 'reused': i == 0} for i, s in enumerate(samples)]):
            self.assertFalse(statistics(modified, 20, 5)['target_met'])
        self.assertFalse(statistics(samples, 20, 1)['target_met'])


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve(); self.form = self.root / 'form.json'
        atomic_json(self.form, {'serial': '123', 'expected_userid': 'TEST001'})
        self.service = SimpleNamespace(root=self.root, material_roots=[self.root],
                                       resolve=lambda case: (None, {'form': self.form}))
        self.targets = [{'case_id': 'fixture', 'action': 'bill-read', 'domain': 'basic'}]
        patcher = patch('finance_benchmark.load_definition', return_value=({'expected_userid': 'TEST001'}, None, None, None))
        patcher.start(); self.addCleanup(patcher.stop)

    def test_preview_is_offline_and_rejects_writer_and_unknown_fields(self):
        with patch('finance_benchmark.RegisteredSession') as session:
            report = run(self.service, root_case='root', targets=self.targets)
        session.assert_not_called(); self.assertFalse(report['browser_actions_executed'])
        for item in ({**self.targets[0], 'action': 'save'}, {**self.targets[0], 'url': 'http://example'},
                     {**self.targets[0], 'domain': 'unknown'}):
            with self.assertRaises(ValueError): validate_targets(self.service, 'root', [item], 20)
        with self.assertRaises(ValueError): validate_targets(self.service, 'root', self.targets * 2, 20)

    def test_real_task_boundary_is_reused_and_three_failures_stop_without_retry(self):
        calls = []
        class Session:
            state = {'tab_id': '7'}
            def ensure(self): pass
            def job(self, case, operation, parameters, label, **kwargs):
                calls.append((operation, parameters, kwargs['request_id']))
                kwargs['on_enqueued']({'job_id': 'j_' + str(len(calls))})
                raise RuntimeError('synthetic network failure')
        with patch('finance_benchmark.RegisteredSession', return_value=Session()):
            report = run(self.service, root_case='root', targets=self.targets, samples=20,
                         output_dir=self.root / '.local/report', execute=True)
        self.assertEqual(report['state'], 'stopped'); self.assertFalse(report['ok'])
        self.assertEqual(len(calls), 3); self.assertEqual(len({c[2] for c in calls}), 3)
        self.assertTrue(all(c[0] == 'business' and c[1]['action'] == 'bill-read' for c in calls))
        self.assertEqual(report['results'][0]['statistics']['failures'], 3)
        self.assertEqual(len(report['results'][0]['samples']), 3)


if __name__ == '__main__': unittest.main()
