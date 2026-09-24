"""Real native export/PCA encoding/O_DIRECT search parity; correctness, not QPS evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from ours_pca import plan_route, prepare_route_args, verify_assets
import orchestrator  # Installs the shared diskfair package namespace.
from diskfair.ours_records import prepare_record_args, profile_args, POLICY as CACHE_POLICY
from native_contract import validate_ours_record_cache, flatten_artifact
from memory_runner import run_measured
from run_pq_shared_graph_native_integration import make_common, write_fvecs, write_ivecs, write_graph


def replace(args, flag, value):
    args[args.index(flag) + 1] = str(value)


def main():
    repo = HERE.parents[2]
    binary = repo / "src/graph_core/target/release/qgraph05_shared_graph_port"
    env = dict(os.environ, QG05_FAST="0", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    with tempfile.TemporaryDirectory(prefix="formal-pca-smoke-") as tmp:
        root = Path(tmp)
        dataset = root / "data/tiny"
        dataset.mkdir(parents=True)
        count, dim, workers = 8192, 256, 4
        rng = np.random.default_rng(42)
        base = rng.normal(size=(count, dim)).astype(np.float32)
        queries = base[:8] + rng.normal(scale=.01, size=(8, dim)).astype(np.float32)
        truth = [np.argsort(np.sum((base - q)**2, axis=1))[:10].tolist() for q in queries]
        base_path, query, gt = dataset / "tiny_base.fvecs", root / "q.fvecs", root / "gt.ivecs"
        write_fvecs(base_path, base.tolist()); write_fvecs(query, queries.tolist()); write_ivecs(gt, truth)
        heldout = base[1000:1008] + rng.normal(scale=.01, size=(8,dim)).astype(np.float32)
        heldout_truth = [np.argsort(np.sum((base-q)**2,axis=1))[:10].tolist() for q in heldout]
        test_query, test_gt = root / "test.fvecs", root / "test.ivecs"
        write_fvecs(test_query,heldout.tolist());write_ivecs(test_gt,heldout_truth)
        graph = root / "ours.graph.bin"
        write_graph(graph, count, 64, virtual_entry=False)
        order = root / "order.u32"
        np.arange(len(queries), dtype="<u4").tofile(order)
        index = root / "disk/05c_ours"
        measurements = {}
        def command(phase, result):
            args = make_common(binary, root, root / "data", graph, graph, query, gt, order,
                               result, result.with_suffix(".queries.jsonl"), phase, "Ours-Disk", "05c")
            replace(args, "--workers", workers)
            replace(args, "--integration-widths", "40,80")
            return args
        def run(args, name):
            if "--ours-record-cache-policy" in args and args[args.index("--ours-record-cache-policy")+1] == "hot_dynamic":
                budget = int(float(args[args.index("--search-dram-budget-gib")+1]) * (1<<30))
                e = run_measured(args, evidence_path=root/f"{name}.resources.json",
                                 log_path=root/f"{name}.log", rss_budget=True, budget_bytes=budget,
                                 env=env, cpu_affinity=sorted(os.sched_getaffinity(0))[:workers], timeout=120)
                assert e["exit_code"] == 0, (e,(root/f"{name}.log").read_text()[-2000:])
                # The forced PCA threshold is ~81 MiB, outside the formal grid.
                # It tests maximum-prefix and zero-cache boundaries, not a claim
                # that this tiny allowance covers the whole launch/parity RSS.
                admitted = e["observed_peak_rss_bytes"] <= budget
                assert e["status"] == ("completed" if admitted else "budget_exceeded")
                assert e["budget_admitted"] == admitted and e["sampled_swap_bytes"] == 0
                if name.startswith("full"):
                    assert admitted
                measurements[name] = dict(budget_bytes=budget, peak_rss_bytes=e["observed_peak_rss_bytes"],
                                          budget_admitted=e["budget_admitted"], status=e["status"],
                                          swap_bytes=e["sampled_swap_bytes"])
                return
            with (root / f"{name}.log").open("w") as log:
                p = subprocess.run(args, env=env, stdout=log, stderr=subprocess.STDOUT)
            if p.returncode:
                raise RuntimeError((root / f"{name}.log").read_text())
        run(command("export", root / "export.json"), "export")
        query_bytes = 2 * (query.stat().st_size + gt.stat().st_size)
        full = plan_route(binary, index, workers, 2., query_bytes)
        assert full["mode"] == "full1bit"
        low_budget = (full["required_bytes"] - 1) / (1 << 30)
        results = {}
        for name, budget in (("full", 2.), ("pca", low_budget)):
            result = root / f"{name}.json"
            args = command("validate", result)
            replace(args, "--search-dram-budget-gib", budget)
            args += prepare_route_args(binary=binary, index=index, base=base_path, query_source=query,
                                       groundtruth_source=gt, workers=workers, budget_gib=budget, artifact=result)
            run(args, name)
            artifact = json.loads(result.read_text())
            parity = json.loads(result.with_suffix(".parity.json").read_text())
            assert artifact["implementation_parity"] == "passed"
            assert parity["query_comparisons"] == len(queries) * 2
            assert parity["mean_top10_overlap"] == 1
            assert parity["max_recall_delta"] == 0
            assert parity["mean_distance_count_relative_delta"] == 0
            assert artifact["ours_route_plan"]["mode"] == ("full1bit" if name == "full" else "pca1bit")
            cached_result = root / f"{name}_cached.json"
            cached = command("validate", cached_result)
            replace(cached, "--cache-mode", "standard")
            replace(cached, "--search-dram-budget-gib", budget)
            cached += prepare_route_args(binary=binary, index=index, base=base_path, query_source=query,
                                        groundtruth_source=gt, workers=workers, budget_gib=budget, artifact=cached_result)
            cached += prepare_record_args(cached, cached_result, phase="validate", tuning_lock=None)
            run(cached, name+"_cached")
            hot = json.loads(cached_result.read_text())
            validate_ours_record_cache(hot)
            assert hot["ours_record_cache_policy"] == CACHE_POLICY
            def rows(path):
                return [json.loads(line) for line in path.with_suffix(".queries.jsonl").read_text().splitlines()]
            before, after = rows(result), rows(cached_result)
            for a,b in zip(before,after,strict=True):
                for key in ("query_id", "result_ids", "recall_at_10", "visited_nodes", "distance_evaluations", "full4_candidates"):
                    assert a[key] == b[key], (name,key,a[key],b[key])
            assert sum(r["sectors_4k_per_query"] for r in hot["summary_rows"]) <= sum(r["sectors_4k_per_query"] for r in artifact["summary_rows"])
            if name == "full":
                assert hot["ours_record_cache_stats"][0]["cache"]["static_nodes"] > 0
                assert hot["ours_record_cache_stats"][0]["cache"]["dynamic"]["capacity_nodes"] > 0
                assert sum(flatten_artifact(cached_result,hot)[0][key] for key in ("hot_record_part_hits","dynamic_record_part_hits")) > 0
            lock = root / f"{name}.lock.json"
            selected = {k: hot[k] for k in ("ours_record_cache_policy", "ours_hot_profile_manifest", "ours_hot_profile_sha256")}
            selected["config_id"] = "beam1"
            lock.write_text(json.dumps({"selected":{"Ours-Disk::hybrid_disk":selected}}))
            test_result = root / f"{name}_test.json"
            test = command("test",test_result)
            replace(test,"--query",test_query);replace(test,"--groundtruth",test_gt)
            replace(test,"--cache-mode","standard"); replace(test,"--search-dram-budget-gib",budget)
            test += ["--tuning-lock",str(lock)]
            test += prepare_route_args(binary=binary,index=index,base=base_path,query_source=query,
                                       groundtruth_source=gt,workers=workers,budget_gib=budget,artifact=test_result)
            test += prepare_record_args(test,test_result,phase="test",tuning_lock=lock)
            run(test,name+"_test")
            frozen=json.loads(test_result.read_text());validate_ours_record_cache(frozen)
            assert frozen["ours_hot_profile_sha256"]==hot["ours_hot_profile_sha256"]
            assert not test_result.with_suffix(".hot_profile").exists()
            uncached_result = root / f"{name}_test_uncached.json"
            uncached = list(test)
            replace(uncached,"--ours-record-cache-policy","off")
            replace(uncached,"--result-json",uncached_result)
            replace(uncached,"--query-trace",uncached_result.with_suffix(".queries.jsonl"))
            run(uncached,name+"_test_uncached")
            for a,b in zip(rows(uncached_result),rows(test_result),strict=True):
                for key in ("result_ids","recall_at_10","visited_nodes","distance_evaluations"):
                    assert a[key]==b[key]
            if name == "full":
                dynamic = frozen["ours_record_cache_stats"][0]["cache"]["dynamic"]
                assert dynamic["node_insertions"]>0 and sum(dynamic["hits"])>0, dynamic
            manifest = Path(hot["ours_hot_profile_manifest"])
            ranks = manifest.parent / "ranks.u32"
            original = ranks.read_bytes(); ranks.write_bytes(original + bytes(4))
            try:
                profile_args(manifest, {}, hot["ours_hot_profile_sha256"])
            except ValueError as exc:
                assert "hash mismatch" in str(exc)
            else:
                raise AssertionError("modified hot ranks admitted")
            p = subprocess.run(test,env=env,capture_output=True,text=True)
            assert p.returncode != 0 and "hot-record profile hash mismatch" in p.stderr
            ranks.write_bytes(original)
            if name == "pca":
                assert artifact["ours_route_plan"]["shortlist_m"] == 32
                assert artifact["ours_route_plan"]["residual_norm_bytes"] == 0
                folder = Path(artifact["pca_route_dir"])
                assert not (folder / "residual.bin").exists()
                verify_assets(folder, {})
                # Native loader must fail closed after an asset is modified.
                with (folder / "basis.bin").open("r+b") as stream:
                    first = stream.read(1); stream.seek(0); stream.write(bytes([first[0] ^ 1]))
                p = subprocess.run(args, env=env, capture_output=True, text=True)
                assert p.returncode != 0 and "PCA asset hash mismatch" in p.stderr
            results[name] = dict(plan=artifact["ours_route_plan"], parity=parity,
                                 full4_candidates=artifact["summary_rows"][0]["full4_candidates"],
                                 record_cache=hot["ours_record_cache_stats"],
                                 heldout_record_cache=frozen["ours_record_cache_stats"],
                                 test_reused_validation_profile=True,
                                 pages_before=[r["sectors_4k_per_query"] for r in artifact["summary_rows"]],
                                 pages_after=[r["sectors_4k_per_query"] for r in hot["summary_rows"]])
        print(json.dumps(dict(status="passed", queries_per_branch=len(queries), widths=[40,80], workers=workers,
                              measurements=measurements, results=results), indent=2))


if __name__ == "__main__":
    main()
