"""Small real Rust searches after moving Recall evaluation out of batch timing.

These are correctness diagnostics, with 512 vectors / 64 queries and 32 workers.
They do not assert performance, cgroup compliance, or official algorithm parity.
"""
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SUITE = HERE.parent
REPO = SUITE.parents[1]
sys.path.insert(0, str(SUITE))
from memory_runner import run_measured


def fixture(name):
    spec = importlib.util.spec_from_file_location(name, HERE/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RustTimingTests(unittest.TestCase):
    def run_fixture(self, method):
        shared = fixture('run_pq_shared_graph_native_integration')
        official = fixture('run_diskann_native_integration')
        binary = REPO / ('baselines/diskann/target/release/qgraph05_diskann_port'
                         if method == 'DiskANN-PQ-Disk' else
                         'src/graph_core/target/release/qgraph05_shared_graph_port')
        binary = Path(os.environ.get('QG_TEST_DISKANN_BINARY' if method == 'DiskANN-PQ-Disk'
                                     else 'QG_TEST_OURS_BINARY', str(binary)))
        self.assertTrue(binary.is_file(), f'build {binary} before this integration check')
        with tempfile.TemporaryDirectory(prefix='qgraph05_rust_timing_') as tmp:
            root = Path(tmp)
            (root/'data/tiny').mkdir(parents=True)
            rng = random.Random(20260917)
            base = [[rng.uniform(-2, 2) for _ in range(16)] for _ in range(512)]
            queries = [[base[i][j]+rng.uniform(-.01, .01) for j in range(16)] for i in range(64)]
            truth = [sorted(range(len(base)), key=lambda i:
                     sum((base[i][j]-q[j])**2 for j in range(16)))[:10] for q in queries]
            shared.write_fvecs(root/'data/tiny/tiny_base.fvecs', base)
            shared.write_fvecs(root/'query.fvecs', queries)
            shared.write_fvecs(root/'warmup.fvecs', [[v + 0.25 for v in q] for q in queries[:4]])
            shared.write_ivecs(root/'gt.ivecs', truth)
            # Deliberately shuffle: post-batch evaluation must use query IDs,
            # rather than assuming results appear in groundtruth row order.
            order = list(range(len(queries)))
            rng.shuffle(order)
            (root/'order.u32').write_bytes(struct.pack('<64I', *order))
            (root/'input.json').write_text('{"dataset":"tiny"}\n')
            graph, ours_graph = root/'shared.graph.bin', root/'ours.graph.bin'
            shared.write_graph(graph, 512, 16, virtual_entry=True)
            shared.write_graph(ours_graph, 512, 16, virtual_entry=False)
            graph.with_suffix('.json').write_text(
                'suite=02_diskann_fair\nstart_index=0\nbase_count=512\ndimension=16\n')

            def command(phase, result, trace):
                if method == 'DiskANN-PQ-Disk':
                    cmd = official.command(binary, root, phase=phase, result=result, trace=trace,
                        native_hash=shared.sha256(binary), order_hash=shared.sha256(root/'order.u32'))
                    cmd += ['--shared-graph', str(graph), '--shared-graph-sha256', shared.sha256(graph)]
                else:
                    cmd = shared.make_common(binary, root, root/'data', graph, ours_graph,
                        root/'query.fvecs', root/'gt.ivecs', root/'order.u32', result, trace,
                        phase, method, layer='05c' if method == 'Ours-Disk' else '05b')
                for key, value in [('--workers','32'), ('--warmup-queries','4'),
                                   ('--search-dram-budget-gib','0.5'), ('--integration-widths','20')]:
                    cmd[cmd.index(key)+1] = value
                if os.environ.get('QG_TEST_FIXED03') == '1' and method in ('Ours-Disk', 'DiskANN-PQ-Disk'):
                    cmd += ['--fixed-beam', '4', '--measurement-width', '20',
                            '--warmup-query', str(root/'warmup.fvecs'),
                            '--warmup-query-sha256', shared.sha256(root/'warmup.fvecs')]
                return cmd

            env = dict(os.environ, OMP_NUM_THREADS='4', MKL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
            with (root/'export.log').open('w') as f:
                exported = subprocess.run(command('export', root/'export.json', root/'unused.jsonl'),
                                          stdout=f, stderr=subprocess.STDOUT, env=env, timeout=120)
            self.assertEqual(exported.returncode, 0, (root/'export.log').read_text()[-5000:])
            result, trace = root/'result.json', root/'trace.jsonl'
            cpus = sorted(os.sched_getaffinity(0))[:32]
            self.assertEqual(len(cpus), 32)
            # Real native search under shared 512 MiB planning/RSS admission.
            # No cgroup or RLIMIT_AS is imposed.
            e = run_measured(command('validation', result, trace),
                evidence_path=root/'resources.json', log_path=root/'search.log',
                rss_budget=True, budget_bytes=512*2**20, cpu_affinity=cpus, env=env, timeout=120)
            self.assertEqual(e['status'], 'completed', (root/'search.log').read_text()[-3000:])
            self.assertFalse(e['budget_verified'])
            self.assertTrue(e['budget_admitted'])
            self.assertLessEqual(e['observed_peak_rss_bytes'],512*2**20)
            self.assertEqual(e['launch']['cpu_affinity'], cpus)
            a = json.loads(result.read_text())
            self.assertFalse(a['formal_ready'])
            self.assertEqual(a['timing_semantics'], 'search_wall_excludes_recall_evaluation')
            rows = [json.loads(line) for line in trace.read_text().splitlines()]
            self.assertEqual(len(rows), 64)
            self.assertEqual({r['query_id'] for r in rows}, set(range(64)))
            for r in rows:
                recall = sum(i in truth[r['query_id']] for i in r['result_ids'][:10])/10
                self.assertAlmostEqual(r['recall_at_10'], recall, places=8)
                self.assertEqual(r['workers'], 32)
                self.assertGreater(r['latency_us'], 0)
            self.assertEqual(len(a['summary_rows']), 1)
            row = a['summary_rows'][0]
            self.assertAlmostEqual(row['recall'], sum(r['recall_at_10'] for r in rows)/64, places=8)
            self.assertTrue(math.isfinite(row['qps']) and row['qps']>0)
            self.assertGreater(row['bytes_read_per_query'], 0)
            print(json.dumps({'method': method, 'vectors':512, 'queries':64, 'workers':32,
                'query_order':'shuffled', 'recall_matches_result_ids': True,
                'process_peak_rss_bytes':e['process_peak_rss_bytes'],
                'budget_verified':False, 'budget_admitted':e['budget_admitted'], 'formal_ready':False,
                'binary_sha256':shared.sha256(binary)}), flush=True)

    def test_ours(self):
        self.run_fixture('Ours-Disk')

    def test_shared_pq(self):
        self.run_fixture('PQ-DiskANN-Disk')

    def test_diskann(self):
        self.run_fixture('DiskANN-PQ-Disk')


if __name__ == '__main__':
    unittest.main(verbosity=2)
