"""Small resource/acceptance regressions; no datasets or benchmark sweep required."""
import copy
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_runner import run_measured, check_environment, MemoryEnvironmentError, digest
from protocol import PROTOCOL_ID, aggregate_repeats, validate_resource_evidence, attach_resource_evidence
from orchestrator import _work_items, specs_for, make_parser, _dataset_admission_errors
from native_contract import SUMMARY_FIELDS


def rows():
    return [dict(protocol_id=PROTOCOL_ID, formal_ready=True, repeat_id=0,
                 run_id='unit', dataset='gist', method='Ours-Disk', workers=32,
                 storage_mode='hybrid_disk', cache_mode='standard', search_dram_budget_gib=4,
                 config_id='one', search_width=40, beam_width=4, recall=0.95,
                 qps=123.45, latency_p95_us=456.78, native_binary_sha256='a'*64,
                 artifact_path='/tmp/a0.json')]


class StatisticsTests(unittest.TestCase):
    def test_single_run_preserves_real_values_and_source(self):
        self.assertEqual(aggregate_repeats(rows()),rows())
    def test_no_synthetic_dispersion(self):
        r=rows();r[0].update(qps_iqr=0,qps_cv=0,latency_p95_us_iqr=0,latency_p95_us_cv=0)
        a=aggregate_repeats(r)[0]
        for field in ('qps_iqr','qps_cv','latency_p95_us_iqr','latency_p95_us_cv'):
            self.assertNotIn(field,a)
    def test_second_repeat_rejected(self):
        r=rows();r[0]['repeat_id']=1
        with self.assertRaisesRegex(ValueError,'repeat_id=0'):aggregate_repeats(r)
    def test_duplicate_point_rejected(self):
        with self.assertRaisesRegex(ValueError,'duplicate'):aggregate_repeats(rows()+rows())
    def test_float_csv_repeat_id(self):
        r=rows();r[0]['repeat_id']=0.0
        self.assertEqual(aggregate_repeats(r)[0]['repeat_id'],0)
    def test_legacy_rejected(self):
        r=rows();r[0]['protocol_id']='old'
        with self.assertRaisesRegex(ValueError,'legacy'):aggregate_repeats(r)
    def test_missing_optional_metrics_stay_missing(self):
        r=rows();r[0]['io_wait_us']=None
        self.assertIsNone(aggregate_repeats(r)[0]['io_wait_us'])
    def test_nonfinite_or_missing_core_metrics_rejected(self):
        for value in (float('inf'), float('nan'),0):
            r=rows();r[0]['qps']=value
            with self.assertRaises(ValueError):aggregate_repeats(r)
    def test_different_budget_points_retained_without_averaging(self):
        r=rows();other=copy.deepcopy(r);other[0].update(search_dram_budget_gib=8,qps=400)
        self.assertEqual([x['qps'] for x in aggregate_repeats(r+other)],[123.45,400])
    def test_csv_fields_unique_and_no_repeat_statistics(self):
        self.assertEqual(len(SUMMARY_FIELDS),len(set(SUMMARY_FIELDS)))
        self.assertNotIn('qps_cv',SUMMARY_FIELDS)


class MatrixTests(unittest.TestCase):
    def test_primary_matches_tuning(self):
        spec=specs_for('05c',['Ours-Disk'])
        for phase,count in [('validation',1),('test',1)]:
            items=_work_items(spec,phase,(32,),1,'gist',('hybrid_disk',))
            self.assertEqual(len(items),count)
            self.assertEqual({(x.workers,x.budget_gib,x.cache_mode) for x in items},{(32,4,'standard')})
    def test_explicit_budget_c0(self):
        items=_work_items(specs_for('05c',['Ours-Disk']),'test',(32,),1,'gist',('hybrid_disk',),8,'c0')
        self.assertEqual({(x.workers,x.budget_gib,x.cache_mode) for x in items},{(32,8,'c0')})
    def test_05a_cache_always_c0(self):
        items=_work_items(specs_for('05a',['PQ_4bit']),'validation',(32,),1,'agnews',('resident','payload_on_ssd'))
        self.assertEqual({x.cache_mode for x in items},{'c0'})
        self.assertEqual(len(items),2)
    def test_parser_defaults(self):
        a=make_parser().parse_args(['--phase','run'])
        self.assertEqual((a.search_dram_budget_gib,a.repeats,a.workers),(4,1,'32'))
    def test_completeness_matches_explicit_condition(self):
        from native_contract import validate_layer_completeness, ContractError
        r=rows()
        for a in r:a.update(layer='05c',phase='test')
        kw=dict(layer='05c',dataset='gist',repeats=1,workers=(32,),methods=['Ours-Disk'])
        validate_layer_completeness(r,**kw)
        extra=copy.deepcopy(r[0]);extra['workers']=16
        with self.assertRaisesRegex(ContractError,'matrix'):
            validate_layer_completeness(r+[extra],**kw)

    def test_ours_lower_bound_does_not_block_other_methods(self):
        mod=importlib.import_module('orchestrator')
        with patch.object(mod,'_fvec_shape',return_value=(114000000,1024)):
            self.assertEqual(_dataset_admission_errors('msmarco',('05c',),Path('/tmp'),4,('AiSAQ-Disk',)),[])
            self.assertTrue(_dataset_admission_errors('msmarco',('05c',),Path('/tmp'),4,('Ours-Disk',)))


class ResourceTests(unittest.TestCase):
    def test_cgroup_evidence_rejects_oom_wrong_budget_and_tampering(self):
        # Synthetic evidence tests validation logic; it is not a live cgroup test.
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);a_path=p/'a.json';e_path=p/'r.json'
            e=dict(protocol_id=PROTOCOL_ID,status='completed',exit_code=0,binary_sha256='a'*64,
                   command=['/native','--result-json',str(a_path)],process_peak_rss_bytes=4096,
                   process_peak_rss_source='wait4_ru_maxrss_linux_kib',
                   memory_enforcement='cgroup_v2',memory_limit_scope='cgroup',memory_limit_bytes=4*(1<<30),
                   swap_limit_bytes=0,cgroup_memory_peak_bytes=8192,memory_events={'oom':0,'oom_kill':0},
                   budget_verified=True,cgroup_path='/sys/fs/cgroup/private',
                   launch={'joined_cgroup':'/sys/fs/cgroup/private'})
            a=dict(protocol_id=PROTOCOL_ID,storage_mode='hybrid_disk',search_dram_budget_gib=4,
                   native_binary_sha256='a'*64,resource_measurement_path=str(e_path),
                   process_peak_rss_bytes=4096,peak_rss_bytes=4096,
                   memory_enforcement='cgroup_v2',memory_limit_scope='cgroup',memory_limit_bytes=4*(1<<30),
                   cgroup_memory_peak_bytes=8192)
            def save():
                e_path.write_text(json.dumps(e));a['resource_measurement_sha256']=digest(e_path)
            save();validate_resource_evidence(a,dict(a,artifact_path=str(a_path)))
            e['memory_events']['oom_kill']=1;save()
            with self.assertRaisesRegex(ValueError,'OOM'):
                validate_resource_evidence(a,dict(a,artifact_path=str(a_path)))
            e['memory_events']['oom_kill']=0;save()
            with self.assertRaisesRegex(ValueError,'budget'):
                validate_resource_evidence(a,dict(a,artifact_path=str(a_path),search_dram_budget_gib=2))
            e_path.write_text('{}')
            with self.assertRaisesRegex(ValueError,'hash'):
                validate_resource_evidence(a,dict(a,artifact_path=str(a_path)))

    def test_missing_cgroup_does_not_execute(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);mark=p/'should_not_exist'
            with self.assertRaises(MemoryEnvironmentError):
                run_measured([sys.executable,'-c',f'open({str(mark)!r},"w").close()'],evidence_path=p/'r.json',log_path=p/'r.log',budget_bytes=1<<28)
            self.assertFalse(mark.exists())
            self.assertEqual(json.loads((p/'r.json').read_text())['status'],'blocked_environment')
    def test_regular_directory_is_not_a_cgroup(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(MemoryEnvironmentError):check_environment(d,1<<28)
    def test_real_rss_reference_is_not_budget_proof(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            e=run_measured([sys.executable,'-c','a=bytearray(24*1024*1024)'],evidence_path=p/'r.json',log_path=p/'r.log',reference=True)
            self.assertEqual(e['status'],'completed')
            self.assertGreater(e['process_peak_rss_bytes'],24*1024*1024)
            self.assertFalse(e['budget_verified'])
            self.assertEqual(e['memory_limit_scope'],'unconstrained_reference')
            self.assertEqual(e,json.loads((p/'r.json').read_text()))
            with self.assertRaises(FileExistsError):
                run_measured([sys.executable,'-c','pass'],evidence_path=p/'r.json',log_path=p/'r.log',reference=True)
    def test_child_cpu_affinity_is_applied(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);cpu=min(os.sched_getaffinity(0))
            e=run_measured([sys.executable,'-c','import os; print(sorted(os.sched_getaffinity(0)))'],
                           evidence_path=p/'r.json',log_path=p/'r.log',reference=True,cpu_affinity=[cpu])
            self.assertEqual(e['status'],'completed')
            self.assertEqual(e['launch']['cpu_affinity'],[cpu])
            self.assertEqual((p/'r.log').read_text().splitlines()[-1],str([cpu]))

    def test_runtime_error_not_budget_exceeded(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            e=run_measured([sys.executable,'-c','raise SystemExit(7)'],evidence_path=p/'r.json',log_path=p/'r.log',reference=True)
            self.assertEqual(e['status'],'runtime_error');self.assertEqual(e['exit_code'],7)
    def test_timeout_not_budget_exceeded(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            e=run_measured([sys.executable,'-c','import time; time.sleep(10)'],evidence_path=p/'r.json',log_path=p/'r.log',reference=True,timeout=.2)
            self.assertEqual(e['status'],'timeout')
    def test_attach_and_validate_reference_with_path_binding(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);artifact=p/'a.json';evidence=p/'r.json'
            command=[sys.executable,'-c','pass','--result-json',str(artifact)]
            e=run_measured(command,evidence_path=evidence,log_path=p/'r.log',reference=True)
            native=dict(storage_mode='resident',peak_rss_bytes=12,summary_rows=[{'peak_rss_bytes':9}],native_binary_sha256=digest(sys.executable))
            artifact.write_text(json.dumps(native))
            a=attach_resource_evidence(artifact,evidence,e)
            self.assertEqual(a['native_reported_peak_rss_bytes'],12)
            self.assertGreater(a['peak_rss_bytes'],12)
            self.assertEqual(json.loads(artifact.with_suffix('.native.json').read_text()),native)
            validate_resource_evidence(a,dict(a,artifact_path=str(artifact)))
            with self.assertRaisesRegex(ValueError,'produce this artifact'):
                validate_resource_evidence(a,dict(a,artifact_path='/tmp/wrong.json'))
            a['storage_mode']='hybrid_disk';a['search_dram_budget_gib']=4
            with self.assertRaisesRegex(ValueError,'cgroup'):
                validate_resource_evidence(a,dict(a,artifact_path=str(artifact)))
    @unittest.skipUnless(os.environ.get('QG05_TEST_CGROUP_PARENT'), 'requires a delegated writable cgroup with memory.peak')
    def test_real_cgroup_oom(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            e=run_measured([sys.executable,'-c','a=bytearray(256*1024*1024)'],evidence_path=p/'r.json',log_path=p/'r.log',budget_bytes=64*1024*1024,cgroup_parent=os.environ['QG05_TEST_CGROUP_PARENT'])
            self.assertEqual(e['status'],'budget_exceeded')
            self.assertGreater(e['memory_events']['oom_kill'],0)
            self.assertFalse(e['budget_verified'])

class PlotAcceptanceTests(unittest.TestCase):
    def test_legacy_manifest_rejected_even_with_fast_mode(self):
        os.environ.setdefault('MPLCONFIGDIR','/tmp/qgraph05_test_mpl')
        import plot_05_disk_suite as plot
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);base=root/'05C_disk_system_fair'/'gist'/'aggregate';base.mkdir(parents=True)
            (base/'formal_test_rows.csv').write_text('method\nOurs-Disk\n')
            (base/'formal_test_rows.manifest.json').write_text('{}')
            with patch.dict(os.environ,{'QG05_FAST':'1'}):
                with self.assertRaisesRegex(plot.ContractError,'legacy'):
                    plot._load_complete(root,'05c','gist')
    def test_public_csv_cannot_bypass_evidence(self):
        import plot_05_disk_suite as plot
        with self.assertRaisesRegex(plot.ContractError,'insufficient'):
            plot._load_public_rows(Path('/tmp'),'05c','gist')

if __name__=='__main__':unittest.main(verbosity=2)
