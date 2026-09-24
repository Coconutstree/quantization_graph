"""Frozen representation, shared logical hot ranking and policy identity."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import orchestrator
from diskfair.ours_pca import prepare_route_args, sha
from diskfair.ours_records import prepare_record_args, POLICY
from diskfair.admission import measurement_environment

class CacheAllocationTests(unittest.TestCase):
    def test_frozen_representation_checked_before_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); query=root/'q';query.write_bytes(b'1234')
            frozen=root/'frozen.json';plan=dict(mode='full1bit',dimension=960)
            frozen.write_text(json.dumps(plan))
            kwargs=dict(binary=root/'bin',index=root,base=root/'base',query_source=query,
                        groundtruth_source=query,workers=32,budget_gib=4,artifact=root/'result.json',frozen_route=frozen)
            with patch('diskfair.ours_pca.plan_route',return_value=plan), patch('diskfair.ours_pca.ensure_assets',return_value=None) as assets:
                prepare_route_args(**kwargs);assets.assert_called_once()
                frozen.write_text(json.dumps(dict(mode='pca1bit',dimension=128)))
                with self.assertRaisesRegex(ValueError,'frozen paired plan'): prepare_route_args(**kwargs)
                assets.assert_called_once()

    def test_both_strategies_reuse_same_validation_ranks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);query=root/'query';query.write_bytes(b'query')
            (root/'ranks.u32').write_bytes((0).to_bytes(4,'little'))
            plan=dict(mode='pca1bit',dimension=128)
            manifest=root/'manifest.json'
            manifest.write_text(json.dumps(dict(policy=POLICY,source_phase='validation',route_plan=plan,
                native_binary_sha256='binary',source_graph_sha256='graph',index_dir=str(root),
                pca_assets_sha256='pca',query_sha256=sha(query),query_split_sha256='split',query_order_sha256='order',
                ranks_sha256=sha(root/'ranks.u32'))))
            artifact=root/'test.json';artifact.with_suffix('.route_plan.json').write_text(json.dumps(plan))
            base=['bin','--cache-mode','standard','--native-binary-sha256','binary','--ours-graph-sha256','graph',
                  '--disk-index-dir',str(root),'--pca-assets-sha256','pca','--query',str(query),
                  '--query-split-sha256','split','--query-order-sha256','order']
            outputs=[]
            with patch('diskfair.ours_records.run_measured',side_effect=AssertionError('unexpected profile rebuild')):
                for strategy in ('graph_first','records_only'):
                    outputs.append(prepare_record_args(base+['--ours-cache-allocation',strategy],artifact,
                        phase='validation',tuning_lock=None,shared_profile=manifest))
            self.assertEqual(outputs[0],outputs[1])
            lock=root/'lock.json';lock.write_text(json.dumps(dict(selected={'Ours-Disk::hybrid_disk':dict(
                ours_cache_allocation='graph_first',ours_record_cache_policy=POLICY,
                ours_hot_profile_manifest=str(manifest),ours_hot_profile_sha256=sha(manifest))})))
            with self.assertRaisesRegex(ValueError,'allocation differs'):
                prepare_record_args(base+['--ours-cache-allocation','records_only'],artifact,phase='test',tuning_lock=lock)

    def test_preparation_memory_does_not_enter_native_peak(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            code = """
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from memory_runner import run_measured_isolated
root=Path(sys.argv[2])
preparation=bytearray(256*1024*1024)
for name,size in [('small',8),('large',192)]:
    e=run_measured_isolated([sys.executable,'-c',f'x=bytearray({size}*1024*1024)'],
       evidence_path=root/(name+'.json'),log_path=root/(name+'.log'),
       rss_budget=True,budget_bytes=128*1024*1024)
    assert e['status']==('completed' if name=='small' else 'budget_exceeded'),e
"""
            result=subprocess.run([sys.executable,'-c',code,str(Path(__file__).resolve().parents[1]),str(root)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_path_trace_never_measured(self):
        c=['bin','--layer','05c','--method','Ours-Disk','--parity-mode','external',
           '--ours-search-path-dir','paths']
        with self.assertRaisesRegex(ValueError,'diagnostics'): measurement_environment(c,{})

if __name__=='__main__': unittest.main()
