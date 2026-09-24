"""All disk methods share planned RAM budgets and measured RSS admission."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_runner import run_measured, digest, MemoryEnvironmentError
from protocol import (PROTOCOL_ID, attach_resource_evidence, validate_resource_evidence,
                      requires_memory_limit, method_budget_gib)
from orchestrator import _work_items, specs_for, make_parser, configure_experiment, validate_primary_budget
from orchestrator import ContractError as OrchestratorContractError
from native_contract import validate_layer_completeness, ContractError, METHOD_SPECS
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "experiments/03_disk_system/adapters"))
from run_official_disk_baseline import parser as official_parser


class MethodMemoryPolicyTests(unittest.TestCase):
    def test_default_shared_budget_needs_no_os_limit(self):
        for layer,budget in (('01',2),('02',2),('03',4),('05',2)):
            args=make_parser(layer).parse_args(['--phase','run'])
            configure_experiment(args,layer)
            self.assertEqual(args.search_dram_budget_gib,budget)
        self.assertFalse(requires_memory_limit('Ours-Disk', 'hybrid_disk'))
        self.assertFalse(requires_memory_limit('Ours_RaBitQ_K1', 'resident'))
        for method in ('DiskANN-PQ-Disk','AiSAQ-Disk','Starling-Disk','Glass-NSG-DiskPort','SymphonyQG-DiskPort'):
            self.assertFalse(requires_memory_limit(method, 'hybrid_disk'))

    def test_shared_budget_survives_tuning_and_completeness(self):
        methods = [s.method for s in METHOD_SPECS if s.layer == '05c']
        configs = {m: 4 for m in methods if m != 'Ours-Disk'}
        observed = []
        for phase in ('validation','test'):
            items = _work_items(specs_for('05c', methods), phase, (32,), 1, 'agnews',
                                ('hybrid_disk',), budget_gib=4, baseline_budgets=configs)
            observed.append({i.spec.method: i.budget_gib for i in items})
        self.assertEqual(observed, [{m: 4 for m in methods}]*2)
        rows = [dict(layer='05c', dataset='agnews', phase='test', method=m, workers=32,
                     repeat_id=0, storage_mode='hybrid_disk', cache_mode='standard',
                     search_dram_budget_gib=b) for m,b in observed[0].items()]
        kwargs = dict(layer='05c', dataset='agnews', repeats=1, workers=(32,),
                      methods=methods, budget_gib=4, baseline_budgets=configs)
        validate_layer_completeness(rows, **kwargs)
        rows[1]['search_dram_budget_gib'] = 7
        with self.assertRaisesRegex(ContractError, 'matrix'):
            validate_layer_completeness(rows, **kwargs)

    def test_shared_runner_admits_2g_rss_evidence_for_each_method(self):
        # Exercise the shared policy with a tiny allocation process, not native search.
        for method in (s.method for s in METHOD_SPECS if s.layer == '05c'):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as d:
                p=Path(d); artifact=p/'a.json'; evidence=p/'resources.json'
                cmd=[sys.executable,'-c','data=bytearray(24*1024*1024)', '--result-json', str(artifact)]
                e=run_measured(cmd, evidence_path=evidence, log_path=p/'terminal.log',
                               rss_budget=True, budget_bytes=2*2**30)
                self.assertEqual(e['status'], 'completed')
                self.assertTrue(e['budget_admitted'])
                self.assertFalse(e['budget_verified'])
                self.assertIsNone(e['memory_limit_bytes'])
                self.assertEqual(e['launch']['rlimit_as'], 'unlimited')
                artifact.write_text(json.dumps(dict(method=method, storage_mode='hybrid_disk',
                    search_dram_budget_gib=2, peak_rss_bytes=12, summary_rows=[],
                    native_binary_sha256=digest(sys.executable))))
                a=attach_resource_evidence(artifact,evidence,e)
                self.assertEqual(a['memory_policy'], 'ram_budget_rss')
                validate_resource_evidence(a,dict(a,artifact_path=str(artifact)))
                with self.assertRaisesRegex(ValueError,'budget'):
                    validate_resource_evidence(a,dict(a,artifact_path=str(artifact),search_dram_budget_gib=4))
                a['budget_admitted']=False
                with self.assertRaisesRegex(ValueError,'admission'):
                    validate_resource_evidence(a,dict(a,artifact_path=str(artifact)))

    def test_over_budget_process_is_retained_but_not_admitted(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            e=run_measured([sys.executable,'-c','data=bytearray(32*1024*1024)'],
                          evidence_path=p/'r.json',log_path=p/'r.log',rss_budget=True,budget_bytes=8*2**20)
            self.assertEqual(e['exit_code'],0)
            self.assertEqual(e['status'],'budget_exceeded')
            self.assertFalse(e['budget_admitted'])
            self.assertTrue((p/'r.json').exists())

    def test_mismatched_method_budgets_rejected(self):
        with self.assertRaisesRegex(ValueError,'same planned'):
            method_budget_gib('DiskANN-PQ-Disk',2,{'DiskANN-PQ-Disk':4})

    def test_observation_and_explicit_cgroup_keep_separate_contracts(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            with self.assertRaisesRegex(ValueError,'cannot claim'):
                run_measured([sys.executable,'-c','pass'], evidence_path=p/'a.json',
                             log_path=p/'a.log', observe_only=True, budget_bytes=2*(1<<30))
            with self.assertRaises(MemoryEnvironmentError):
                run_measured([sys.executable,'-c','pass'], evidence_path=p/'b.json',
                             log_path=p/'b.log', budget_bytes=2*(1<<30))

    def test_official_cli_defaults_to_shared_4g_rss_admission(self):
        from run_official_disk_baseline import memory_options
        for method in ('AiSAQ-Disk', 'Starling-Disk'):
            args=official_parser().parse_args(['--method',method,'--base','/tmp/b',
                                              '--query','/tmp/q','--work-dir','/tmp/w'])
            self.assertEqual(args.search_memory_gib, 4)
            self.assertEqual(memory_options(args), dict(rss_budget=True, observe_only=False,
                             budget_bytes=4*(1<<30), cgroup_parent=None))

    def test_primary_budget_applies_to_every_03_method_and_05_remains_independent(self):
        for method in (s.method for s in METHOD_SPECS if s.layer == '05c'):
            args=make_parser('03').parse_args(['--phase','run','--methods',method,'--search-dram-budget-gib','2'])
            configure_experiment(args,'03')
            with self.assertRaisesRegex(OrchestratorContractError,'primary RAM budget is 4 GiB'):
                validate_primary_budget(args)
        for budget in (.5,2,4,8):
            args=make_parser('05').parse_args(['--phase','run','--search-dram-budget-gib',str(budget)])
            configure_experiment(args,'05');validate_primary_budget(args)
            self.assertEqual(args.search_dram_budget_gib,budget)
        args=make_parser().parse_args(['--phase','run','--layers','03'])
        configure_experiment(args)
        self.assertEqual(args.search_dram_budget_gib,4)

    def test_legacy_protocol_does_not_become_valid_after_policy_change(self):
        with self.assertRaisesRegex(ValueError,'legacy'):
            validate_resource_evidence({'protocol_id':'disk_cgroup_v2_20260917'}, {})

    def test_plot_label_does_not_describe_baselines_as_2g_capped(self):
        from plot_05_disk_suite import _memory_condition_label
        rows=[dict(method='Ours-Disk', storage_mode='hybrid_disk',search_dram_budget_gib=2),
              dict(method='DiskANN-PQ-Disk',storage_mode='hybrid_disk',search_dram_budget_gib=2)]
        self.assertEqual(_memory_condition_label(rows),'RAM budget 2 GiB; measured RSS (no OS cap)')


if __name__=='__main__':
    unittest.main(verbosity=2)
