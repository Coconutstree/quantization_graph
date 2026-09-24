import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

import cgroup_memory as cg
import report
import run


class ContractTests(unittest.TestCase):
    def test_threshold_selects_fastest_not_lowest_width(self):
        rows=[dict(width=100,recall=.96,qps=100),dict(width=140,recall=.97,qps=110),
              dict(width=80,recall=.94,qps=200)]
        self.assertEqual(report.best(rows,.95)['width'],140)
        self.assertIsNone(report.best(rows,.98))

    def test_threshold_tie_and_exact_boundary(self):
        rows=[dict(width=100,recall=.95,qps=100),dict(width=80,recall=.95,qps=100)]
        self.assertEqual(report.best(rows,.95)['width'],80)

    def test_oom_requires_resource_evidence(self):
        self.assertEqual(cg.classify(-9,{},''),'program_error')
        self.assertEqual(cg.classify(-9,{'oom_kill':1},''),'cgroup_oom')
        self.assertEqual(cg.classify(1,{},'unsupported cache mode'),'parameter_error')
        self.assertEqual(cg.classify(1,{},'resident PQ and worker scratch require 900 bytes'),'admission_rejected')

    def test_no_unbounded_fallback(self):
        with tempfile.TemporaryDirectory() as d, patch('cgroup_memory.subprocess.Popen') as popen:
            with self.assertRaisesRegex(RuntimeError,'cgroup unavailable'):
                cg.measure(['/bin/true'],Path(d)/'out',Path(d)/'missing',1024)
            popen.assert_not_called()

    def test_empty_cgroup_parent_required(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            for name,value in [('cgroup.controllers','memory'),('cgroup.subtree_control','memory'),('cgroup.procs','123')]:
                (p/name).write_text(value)
            self.assertFalse(cg.inspect(p)['ready'])
            (p/'cgroup.procs').write_text('')
            self.assertTrue(cg.inspect(p)['ready'])

    def test_freeze_rejects_configuration_change(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'lock.json'
            run.freeze(p,{'budget':768});run.freeze(p,{'budget':768})
            with self.assertRaises(RuntimeError):run.freeze(p,{'budget':1024})

    def test_summary_io_must_match_trace(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            cg.dump(p/'result.json',{'summary_rows':[dict(search_width=10,query_count=1,qps=1,recall=.9,bytes_read_per_query=4096)]})
            (p/'queries.jsonl').write_text(json.dumps(dict(search_width=10,query_id=0,recall_at_10=.9,bytes_read=8192))+'\n')
            with self.assertRaisesRegex(ValueError,'trace/summary'):run.validate(p,[10],1)

    def test_no_engineering_data_in_paper_report(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'engineering/768/ours/L10';p.mkdir(parents=True)
            cg.dump(p/'status.json',{'classification':'completed'})
            self.assertEqual(report.collect(Path(d)),([],[]))

    def test_default_rss_measurement_needs_no_cgroup(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'run'
            code='import time; x=bytearray(16*1024**2); print("warmup complete; starting disk batch",flush=True); time.sleep(.15)'
            m=run.measure([sys.executable,'-c',code],folder,None,128*2**20)
            self.assertEqual(m['classification'],'completed')
            self.assertTrue(m['budget_admitted'])
            self.assertEqual(m['memory_enforcement'],'none')
            samples=[json.loads(l) for l in (folder/'rss_samples.jsonl').read_text().splitlines()]
            self.assertTrue(any(s['phase']=='measurement' and s.get('VmRSS',0)>0 for s in samples))
            self.assertEqual(run.measured_peak(m),m['observed_peak_rss_bytes'])

    def test_rss_over_budget_does_not_get_a_curve_point(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d); folder=out/'paper/512/ours/L10'
            m=run.measure([sys.executable,'-c','x=bytearray(32*1024**2)'],folder,None,8*2**20)
            self.assertEqual(m['classification'],'budget_exceeded')
            cg.dump(folder/'status.json',{'classification':m['classification']})
            rows, failures=report.collect(out)
            self.assertEqual(rows,[])
            self.assertEqual(failures[0]['status'],'budget_exceeded')

    def test_report_accepts_rss_evidence_and_rejects_false_admission(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d); folder=out/'paper/512/diskann/L10'; folder.mkdir(parents=True)
            cg.dump(out/'experiment.json',dict(memory_mode='rss_budget',budgets_mib=run.BUDGETS))
            cg.dump(folder/'result.json',dict(summary_rows=[dict(query_count=800,recall=.95,qps=10)]))
            trace=dict(bytes_read=4096,io_requests=1,sectors_4k=1)
            (folder/'queries.jsonl').write_text((json.dumps(trace)+'\n')*800)
            (folder/'rss_samples.jsonl').write_text(json.dumps(dict(phase='measurement',VmRSS=32*2**20))+'\n')
            mem=dict(classification='completed',scope='planned_process_ram_budget_rss_admission',
                     budget_bytes=512*2**20,budget_admitted=True,memory_enforcement='none',
                     memory_limit_bytes=None,observed_peak_rss_bytes=32*2**20,sampled_swap_bytes=0)
            def save():
                cg.dump(folder/'memory_measurement.json',mem)
                files={p.name:run.sha(p) for p in folder.iterdir() if p.name!='status.json'}
                cg.dump(folder/'status.json',dict(classification='completed',files=files))
            save()
            rows, failures=report.collect(out)
            self.assertEqual(len(rows),1)
            self.assertIsNone(rows[0]['cgroup_peak_bytes'])
            self.assertEqual(failures,[])
            mem['observed_peak_rss_bytes']=513*2**20
            save()
            with self.assertRaisesRegex(ValueError,'RSS budget'):
                report.collect(out)

    def test_matrix_is_single_round_full_width(self):
        self.assertEqual(run.BUDGETS,[512,2048,4096,8192])
        self.assertEqual(len(set(run.WIDTHS)),40)
        self.assertEqual(run.WIDTHS[-1],580)


if __name__=='__main__':unittest.main()
