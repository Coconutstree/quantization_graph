"""Real small native searches through the separate-reference admission pipeline.

Synthetic datasets only: passing does not certify a full dataset/hardware run.
"""
import copy
import json
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import orchestrator
from diskfair.admission import prepare_reference, finalize, write, replace, PROTOCOL as PARITY_PROTOCOL
from diskfair.native_contract import validate_artifact, SPECS_BY_KEY, ContractError
from diskfair.protocol import attach_resource_evidence, PROTOCOL_ID, sha256
from diskfair.memory_runner import run_measured_isolated as run_measured
from diskfair.storage_precondition import prepare_search, PROTOCOL as STORAGE_PROTOCOL
from diskfair.ours_pca import prepare_route_args, plan_route
from diskfair.ours_records import prepare_record_args
from run_pq_shared_graph_native_integration import make_common, write_fvecs, write_ivecs, write_graph
from run_diskann_native_integration import command as diskann_command


class AdmissionTests(unittest.TestCase):
    def run_native(self, method, pca=False, fixed_beam=None, allocation="graph_first", explicit_widths=None, paired_reference=False):
        widths = explicit_widths or (20,40)
        repo = HERE.parents[2]
        binary = repo / (
            "experiments/03_disk_system/native_diskann/target/release/qgraph05_diskann_port"
            if method == "DiskANN-PQ-Disk"
            else "src/graph_core/target/release/qgraph05_shared_graph_port"
        )
        if method == "Ours-Disk" and os.environ.get("OURS_CACHE_TEST_BINARY"):
            binary = Path(os.environ["OURS_CACHE_TEST_BINARY"]).resolve()
        self.assertTrue(binary.is_file())
        with tempfile.TemporaryDirectory(prefix="formal-admission-") as tmp:
            root = Path(tmp)
            (root / "data/tiny").mkdir(parents=True)
            rng = random.Random(37)
            n, dim, workers = ((32768 if os.environ.get("OURS_CACHE_TEST_BINARY") else 8192) if pca else 512), 256, 16
            base = [[rng.uniform(-1, 1) for _ in range(dim)] for _ in range(n)]
            queries = [base[i] for i in range(8)]
            truth = [sorted(range(n), key=lambda i: sum((x-y)**2 for x,y in zip(base[i],q)))[:10] for q in queries]
            base_path = root / "data/tiny/tiny_base.fvecs"
            query, gt = root / "query.fvecs", root / "gt.ivecs"
            write_fvecs(base_path, base); write_fvecs(query, queries); write_ivecs(gt, truth)
            # Fixture generation is offline. Release the Python float matrix before
            # fork/exec so its inherited RSS does not become launcher high-water.
            del base
            import gc, ctypes
            gc.collect()
            ctypes.CDLL(None).malloc_trim(0)
            order = root / "order.u32"; order.write_bytes(struct.pack("<8I", 6,2,1,7,0,5,3,4))
            write(root / "input.json", {"dataset":"tiny"})
            graph = root / "graph.bin"
            write_graph(graph,n,32,virtual_entry=method != "Ours-Disk")
            graph.with_suffix(".json").write_text(f"suite=02_diskann_fair\nstart_index=0\nbase_count={n}\ndimension=256\n")
            def command(phase, result):
                if method == "Ours-Disk":
                    c = make_common(binary,root,root/"data",graph,graph,query,gt,order,
                                    result,result.with_suffix(".queries.jsonl"),phase,method,"05c")
                    replace(c,"--implementation-fingerprint","05c-ours-cache-allocation-v6" if os.environ.get("OURS_CACHE_TEST_BINARY") else "05c-ours-pca1bit-hot-dynamic-v4")
                    c += ["--input-manifest",str(root/"input.json")]
                    replace(c,"--input-manifest-sha256",sha256(root/"input.json"))
                else:
                    c = diskann_command(binary,root,phase=phase,result=result,trace=result.with_suffix(".queries.jsonl"),
                                        native_hash=sha256(binary),order_hash=sha256(order))
                    c += ["--shared-graph",str(graph),"--shared-graph-sha256",sha256(graph)]
                for flag,value in (("--workers",workers),("--integration-widths","20,40"),
                                    ("--search-dram-budget-gib",2),("--cache-mode","standard")):
                    replace(c,flag,value)
                if method == "Ours-Disk":
                    c += ["--ours-cache-allocation", allocation]
                    if explicit_widths:
                        c += ["--search-widths", ",".join(map(str,widths))]
                if fixed_beam is not None:
                    c += ["--fixed-beam", str(fixed_beam)]
                return c
            env = dict(os.environ,QG05_FAST="0",OPENBLAS_NUM_THREADS="1",MKL_NUM_THREADS="1",OMP_NUM_THREADS="1")
            exported = subprocess.run(command("export",root/"export.json"),env=env,capture_output=True,text=True,timeout=120)
            self.assertEqual(exported.returncode,0,exported.stderr[-2000:])
            artifact = root / "measured.json"
            args = command("validate",artifact)
            index = Path(args[args.index("--disk-index-dir")+1])
            budget = 2.
            if pca:
                plan = plan_route(binary,index,workers,budget,2*(query.stat().st_size+gt.stat().st_size))
                budget = (plan["required_bytes"]-1)/(1<<30)
                replace(args,"--search-dram-budget-gib",budget)
            if method == "Ours-Disk":
                args += prepare_route_args(binary=binary,index=index,base=base_path,query_source=query,groundtruth_source=gt,
                                           workers=workers,budget_gib=budget,artifact=artifact)
                args += prepare_record_args(args,artifact,phase="validate",tuning_lock=None)
            gc.collect()
            ctypes.CDLL(None).malloc_trim(0)
            cpus = sorted(os.sched_getaffinity(0))[:workers]
            try:
                args, manifest = prepare_reference(args,artifact,cpu_affinity=cpus,env=env)
            except ValueError as exc:
                log = artifact.with_suffix(".reference") / "terminal.log"
                self.fail(str(exc)+"\n"+(log.read_text()[-3000:] if log.exists() else ""))
            storage = artifact.with_suffix(".storage.json")
            self.assertEqual(args[args.index('--parity-mode') + 1], 'external')
            ref_resources = json.loads((artifact.with_suffix('.reference') / 'resources.json').read_text())
            self.assertEqual(ref_resources['command'][ref_resources['command'].index('--parity-mode') + 1], 'internal')
            self.assertTrue(ref_resources['reference_only'])
            prepare_search(args,storage)
            resources = artifact.with_suffix(".resources.json")
            e = run_measured(args,evidence_path=resources,log_path=artifact.with_suffix(".log"),rss_budget=True,
                             budget_bytes=int(budget*(1<<30)),cpu_affinity=cpus,env=env,timeout=120)
            self.assertEqual(e["status"],"completed",e)
            self.assertNotEqual(ref_resources['pid'], e['pid'])
            self.assertLessEqual(ref_resources['finished_unix'], e['started_unix'])
            doc = attach_resource_evidence(artifact,resources,e)
            doc.update(storage_precondition_protocol=STORAGE_PROTOCOL,storage_precondition_path=str(storage),
                       storage_precondition_sha256=sha256(storage))
            write(artifact,doc)
            expected = {key:doc[key] for key in ("dataset","phase","run_id","repeat_id","workers","storage_mode",
                "cache_mode","implementation_fingerprint","native_binary_sha256","input_manifest_sha256",
                "query_split_sha256","query_order_sha256","query_order_seed","warmup_queries","search_dram_budget_gib")}
            expected["protocol_id"] = PROTOCOL_ID
            spec = SPECS_BY_KEY["05c:"+method]
            kw = dict(spec=spec,expected=expected,disk_root=root)
            with self.assertRaises(ContractError):
                validate_artifact(artifact,**kw)  # Merely measuring RSS must not grant admission.
            result = finalize(artifact,manifest,**kw)
            validate_artifact(artifact,**kw)
            self.assertTrue(result["formal_ready"])
            self.assertFalse(result["memory_accounting_complete"])
            self.assertEqual(result["separate_reference_protocol"],PARITY_PROTOCOL)
            self.assertFalse(json.loads(artifact.with_suffix(".native.json").read_text())["formal_ready"])
            self.assertEqual(json.loads(artifact.with_suffix(".parity.json").read_text())["query_comparisons"],0)
            self.assertEqual(result["parity"]["query_comparisons"],8*len(widths))
            if method == "Ours-Disk":
                self.assertEqual(result["ours_route_plan"]["mode"],"pca1bit" if pca else "full1bit")
            if method == "Ours-Disk" and os.environ.get("OURS_CACHE_TEST_BINARY"):
                from diskfair.admission import exact_traces, measurement_environment
                self.assertEqual(result["ours_cache_allocation"], allocation)
                if allocation == "records_only":
                    self.assertEqual(result["ours_graph_cache_bytes"], 0)
                    self.assertEqual(result["ours_record_cache_budget_bytes"], result["ours_route_plan"]["cache_available_bytes"])
                traces = []
                for strategy in ("graph_first", "records_only"):
                    diag = list(args)
                    replace(diag, "--ours-cache-allocation", strategy)
                    replace(diag, "--result-json", root / f"{strategy}.json")
                    replace(diag, "--query-trace", root / f"{strategy}.jsonl")
                    diag += ["--ours-search-path-dir", str(root / strategy)]
                    with self.assertRaises(ValueError): measurement_environment(diag, env)
                    proc = subprocess.run(diag, env=env, capture_output=True, text=True, timeout=120)
                    self.assertEqual(proc.returncode, 0, proc.stderr[-3000:])
                    self.assertFalse(json.loads((root / f"{strategy}.json").read_text())["throughput_comparable"])
                    exact_traces(root / f"{strategy}.jsonl", result["query_trace_path"])
                    traces.append({f.name:f.read_bytes() for f in (root/strategy).glob("*.tsv")})
                self.assertEqual(len(traces[0]), 8*len(widths))
                self.assertEqual(traces[0], traces[1])
                for row in result["summary_rows"]:
                    for total, part in (("io_requests_per_query","io_requests_per_query"),("bytes_read_per_query","bytes_read_per_query")):
                        self.assertAlmostEqual(row[total],sum(row[f"{prefix}_{part}"] for prefix in ("graph","full4_record","rerank_record")), places=5)
            if paired_reference:
                from diskfair.paired_reference import prepare as prepare_pair, KIND
                if paired_reference=='results_only':
                    from diskfair.paired_reference import prepare_results as prepare_pair, RESULT_KIND as KIND
                paired = root/'paired.json' 
                target=list(args)
                replace(target,'--ours-cache-allocation','records_only')
                replace(target,'--run-id','native_integration_cache_pair')
                replace(target,'--result-json',paired)
                replace(target,'--query-trace',paired.with_suffix('.queries.jsonl'))
                target,proof=prepare_pair(target,paired,artifact,cpu_affinity=cpus,env=env)
                st=paired.with_suffix('.storage.json');prepare_search(target,st)
                res=paired.with_suffix('.resources.json')
                e=run_measured(target,evidence_path=res,log_path=paired.with_suffix('.log'),rss_budget=True,
                               budget_bytes=int(budget*(1<<30)),cpu_affinity=cpus,env=env)
                self.assertEqual(e['status'],'completed')
                doc=attach_resource_evidence(paired,res,e)
                doc.update(storage_precondition_protocol=STORAGE_PROTOCOL,storage_precondition_path=str(st),storage_precondition_sha256=sha256(st))
                write(paired,doc)
                pair_expected=dict(expected,run_id='native_integration_cache_pair')
                admitted=finalize(paired,proof,spec=spec,expected=pair_expected,disk_root=root)
                self.assertEqual(admitted['parity']['proof_chain'],KIND)
                validate_artifact(paired,spec=spec,expected=pair_expected,disk_root=root)
                if paired_reference=='results_only':
                    self.assertFalse(admitted['parity']['expansion_paths_checked'])
                    self.assertFalse((paired.with_suffix('.reference')/'graph_first').exists())
                    altered=json.loads(proof.read_text());altered['comparisons']-=1
                    write(proof,altered)
                    with self.assertRaises(ContractError):
                        validate_artifact(paired,spec=spec,expected=pair_expected,disk_root=root)
                else:
                    path=next((paired.with_suffix('.reference')/'records_only/paths').glob('*.tsv'))
                    path.write_text(path.read_text()+'tampered\n')
                    with self.assertRaisesRegex(ContractError,'search paths changed'):
                        validate_artifact(paired,spec=spec,expected=pair_expected,disk_root=root)
            baseline = os.environ.get("OURS_ALIGNMENT_BASELINE")
            if baseline and method == "Ours-Disk":
                old_args = list(args)
                old_args[0] = baseline
                replace(old_args, "--native-binary-sha256", sha256(Path(baseline)))
                replace(old_args, "--result-json", root / "before.json")
                replace(old_args, "--query-trace", root / "before.queries.jsonl")
                for flag in ("--ours-record-cache-policy", "--ours-hot-profile-manifest", "--ours-hot-profile-sha256", "--ours-hot-ranks", "--ours-hot-ranks-sha256"):
                    if flag in old_args:
                        i = old_args.index(flag)
                        del old_args[i:i+2]
                (root / "before.route_plan.json").write_bytes(artifact.with_suffix(".route_plan.json").read_bytes())
                old_args += prepare_record_args(old_args, root / "before.json", phase="validate", tuning_lock=None)
                before = subprocess.run(old_args, env=env, capture_output=True, text=True, timeout=120)
                self.assertEqual(before.returncode, 0, before.stderr[-2000:])
                old_rows = [json.loads(x) for x in (root / "before.queries.jsonl").read_text().splitlines()]
                new_rows = [json.loads(x) for x in Path(result["query_trace_path"]).read_text().splitlines()]
                def search_signature(row):
                    return {k: v for k,v in row.items() if k in (
                        "query_id", "search_width", "result_ids", "result_distances",
                        "visited_nodes", "distance_evaluations", "db1_checks", "db1_survivors")}
                self.assertEqual([search_signature(r) for r in old_rows],
                                 [search_signature(r) for r in new_rows])
                print("Ours pre/post frontier batching search signatures identical", flush=True)
            # Missing evidence and self-asserted category flags cannot bypass checks.
            for field in ("separate_reference_sha256","storage_precondition_sha256","resource_measurement_sha256"):
                altered = copy.deepcopy(result); altered[field] = "0"*64
                write(artifact,altered)
                with self.assertRaises(ContractError):
                    validate_artifact(artifact,**kw)
            write(artifact,result)
            for metric in ("recall", "qps"):
                altered = copy.deepcopy(result); altered["summary_rows"][0][metric] = -1
                write(artifact,altered)
                with self.assertRaisesRegex(ContractError,"summary differs from preserved native result"):
                    validate_artifact(artifact,**kw)
            write(artifact,result)
            # Even an updated trace hash cannot legitimize changed ordered IDs.
            trace = Path(result["query_trace_path"])
            original = trace.read_bytes()
            lines = [json.loads(x) for x in trace.read_text().splitlines()]
            lines[0]["result_ids"][0] = n+1
            trace.write_text("\n".join(json.dumps(x) for x in lines)+"\n")
            altered = copy.deepcopy(result); altered["query_trace_sha256"] = sha256(trace)
            write(artifact,altered)
            with self.assertRaisesRegex(ContractError,"differs from separate reference trace"):
                validate_artifact(artifact,**kw)
            trace.write_bytes(original); write(artifact,result)
            if fixed_beam is not None:
                self.assertEqual({r["beam_width"] for r in result["summary_rows"]}, {fixed_beam})
                self.assertEqual({r["search_width"] for r in result["summary_rows"]}, set(widths))
            # Changing reference inputs invalidates the previously accepted artifact.
            with query.open("ab") as f: f.write(bytes(4))
            with self.assertRaisesRegex(ContractError,"reference input changed"):
                validate_artifact(artifact,**kw)
            print(json.dumps(dict(method=method,pca=pca,synthetic=True,queries=8,widths=[20,40],workers=workers,
                budget_bytes=e["planned_budget_bytes"],rss_bytes=e["process_peak_rss_bytes"],
                parity_comparisons=result["parity"]["query_comparisons"],admission_pipeline_passed=True)),flush=True)

    @unittest.skipUnless(os.environ.get("OURS_CACHE_TEST_BINARY"), "new cache binary required")
    def test_results_only_reference_chain(self):
        self.run_native("Ours-Disk", fixed_beam=4, explicit_widths=(60,100,180), paired_reference='results_only')

    @unittest.skipUnless(os.environ.get("OURS_CACHE_TEST_BINARY"), "new cache binary required")
    def test_paired_reference_chain(self):
        self.run_native("Ours-Disk", fixed_beam=4, explicit_widths=(60,100,180), paired_reference=True)

    @unittest.skipUnless(os.environ.get("OURS_CACHE_TEST_BINARY"), "new cache binary required")
    def test_ours_formal_three_widths(self):
        self.run_native("Ours-Disk", fixed_beam=4, explicit_widths=(60,100,180))

    @unittest.skipUnless(os.environ.get("OURS_CACHE_TEST_BINARY"), "new cache binary required")
    def test_ours_records_only_full(self):
        self.run_native("Ours-Disk", allocation="records_only", fixed_beam=4)

    @unittest.skipUnless(os.environ.get("OURS_CACHE_TEST_BINARY"), "new cache binary required")
    def test_ours_records_only_pca(self):
        self.run_native("Ours-Disk", pca=True, allocation="records_only", fixed_beam=4)

    def test_ours_full(self): self.run_native("Ours-Disk")
    def test_ours_pca(self): self.run_native("Ours-Disk",pca=True)
    def test_diskann(self): self.run_native("DiskANN-PQ-Disk")
    def test_fixed_beam_ours(self): self.run_native("Ours-Disk", fixed_beam=4)
    def test_fixed_beam_diskann(self): self.run_native("DiskANN-PQ-Disk", fixed_beam=4)


if __name__ == "__main__": unittest.main(verbosity=2)
