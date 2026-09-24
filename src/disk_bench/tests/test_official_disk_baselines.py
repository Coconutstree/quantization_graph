"""Run with python; tests native command routing and baseline admission."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_contract import LAYER_METHODS, METHOD_SPECS, specs_for, validate_registry, ContractError
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "experiments/03_disk_system/adapters"))
from run_official_disk_baseline import commands, parser, fvecs_to_bin, memory_options
from memory_runner import run_measured


class OfficialBaselineTests(unittest.TestCase):
    def args(self, method):
        return parser().parse_args(['--method', method, '--base', '/tmp/base.fvecs',
                                    '--query', '/tmp/query.fvecs', '--work-dir', '/tmp/official-plan'])

    def test_primary_and_supplementary_are_separate(self):
        self.assertEqual(LAYER_METHODS['05c'], ('Ours-Disk', 'SymphonyQG-DiskPort', 'DiskANN-PQ-Disk', 'Starling-Disk'))
        self.assertEqual([s.method for s in specs_for('05c', ['Glass-NSG-DiskPort'])], ['Glass-NSG-DiskPort'])

    def test_missing_new_baselines_rejects_old_registry(self):
        ports = {s.key: dict(status='ready', command=['native'], source_suite=s.source_suite,
                 source_kernel=s.source_kernel, port_kind=s.port_kind,
                 implementation_fingerprint='test', binary_sha256='a'*64)
                 for s in specs_for('05c')}
        validate_registry(ports, ['05c'])  # Supplementary ports are not mandatory.
        del ports['05c:SymphonyQG-DiskPort']
        with self.assertRaisesRegex(ContractError, 'missing 05c:SymphonyQG-DiskPort'):
            validate_registry(ports, ['05c'])

    def test_aisaq_never_silently_runs_memory_pq(self):
        build, search = commands(self.args('AiSAQ-Disk'), 10000)
        self.assertIn('--use_aisaq', build)
        self.assertIn('--use_aisaq', search)
        self.assertEqual(search[search.index('--pq_read_io_engine')+1], 'aio')
        self.assertEqual(search[search.index('--pq_cache_size')+1], '0')

    def test_starling_uses_navigation_partition_and_page_search(self):
        plan = commands(self.args('Starling-Disk'), 10000)
        self.assertEqual(len(plan), 6)
        self.assertEqual(Path(plan[3][0]).name, 'partitioner')
        self.assertEqual(Path(plan[4][0]).name, 'index_relayout')
        search = plan[-1]
        self.assertEqual(search[search.index('--use_page_search')+1], '1')
        self.assertGreater(int(search[search.index('--mem_L')+1]), 0)
        self.assertTrue(search[search.index('--disk_file_path')+1].endswith('index_partition_tmp.index'))

    def test_conversion_preserves_values_and_checks_headers(self):
        with tempfile.TemporaryDirectory() as d:
            source, target = Path(d)/'source.fvecs', Path(d)/'target.bin'
            source.write_bytes(struct.pack('<iffiff', 2, 1.25, -2.5, 2, 3.75, 4.0))
            self.assertEqual(fvecs_to_bin(source, target), (2, 2))
            self.assertEqual(target.read_bytes(), struct.pack('<IIffff', 2, 2, 1.25, -2.5, 3.75, 4.0))
            source.write_bytes(struct.pack('<iffiff', 2, 1.25, -2.5, 3, 3.75, 4.0))
            with self.assertRaisesRegex(ValueError, 'mixed'):
                fvecs_to_bin(source, Path(d)/'bad.bin')

    def test_new_registry_entries_remain_pending(self):
        registry = json.loads((Path(__file__).resolve().parents[1]/'ports.local.json').read_text())['ports']
        for name in ('AiSAQ-Disk', 'Starling-Disk'):
            self.assertEqual(registry['05c:'+name]['status'], 'pending')

    def test_explicit_diagnostic_memory_policies_and_invalid_budgets(self):
        args = self.args('AiSAQ-Disk')
        args.cgroup_parent = Path('/tmp/unused-cgroup')
        self.assertIsNone(memory_options(args)['cgroup_parent'])
        args.memory_policy = 'observe'
        self.assertEqual(memory_options(args), dict(rss_budget=False, observe_only=True,
                                                   budget_bytes=None,cgroup_parent=None))
        args.memory_policy = 'cgroup'
        self.assertEqual(memory_options(args), dict(rss_budget=False, observe_only=False,
                          budget_bytes=4*(1<<30),cgroup_parent=args.cgroup_parent))
        for value in (0, -1, float('inf'), float('nan')):
            args.search_memory_gib = value
            with self.assertRaisesRegex(ValueError, 'finite and positive'):
                memory_options(args)

    def test_adapter_memory_options_reject_real_over_budget_process(self):
        # Tests actual resource enforcement of adapter settings, not an official search.
        for method in ('AiSAQ-Disk', 'Starling-Disk'):
            for budget, expected_status in ((2., 'completed'), (8/1024, 'budget_exceeded')):
                with self.subTest(method=method,budget=budget), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp); args = self.args(method); args.search_memory_gib = budget
                    e = run_measured([sys.executable, '-c', 'a=bytearray(32*1024*1024)'],
                        evidence_path=root/'resources.json',log_path=root/'terminal.log',**memory_options(args))
                    self.assertEqual(e['status'],expected_status)
                    self.assertEqual(e['budget_admitted'],expected_status == 'completed')
                    self.assertEqual(e['launch']['rlimit_as'],'unlimited')
                    self.assertIsNone(e['memory_limit_bytes'])


if __name__ == '__main__':
    unittest.main()
