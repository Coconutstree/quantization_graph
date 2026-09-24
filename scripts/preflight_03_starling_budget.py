#!/usr/bin/env python3
"""Validation-only RSS preflight of the existing GIST Starling index.

Does not grant formal search acceptance, tune on test queries, or change any
default budget. Uses the pinned official binary and preserves raw evidence.
"""
import argparse
import json
import os
from pathlib import Path
import struct
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src/disk_bench"))
sys.path.insert(0, str(REPO / "experiments/03_disk_system/adapters"))
from memory_runner import run_measured, write_json
from official_sources import verify_source
from storage_precondition import prepare_search
from run_official_disk_baseline import fvecs_to_bin, sha256
from run_starling_existing_layout import starling_memory_lower_bound


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--budget-gib", type=float, default=4.)
    parser.add_argument("--numa-node", type=int, default=None,
                        help="optional explicit membind via numactl; default is unbound memory (diagnostic only)")
    parser.add_argument("--widths", type=int, nargs="+", default=[40,100,580])
    parser.add_argument("--warmup-queries", type=int, default=0)
    parser.add_argument("--query-order", type=Path)
    args = parser.parse_args()
    if len(set(args.widths)) != len(args.widths) or any(w<10 for w in args.widths): raise ValueError("invalid widths")
    if args.budget_gib not in (2., 4., 8.):
        raise ValueError("preflight candidates are 2, 4 and 8 GiB")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = verify_source(REPO, "starling")
    index = REPO / "work/starling_existing_layout_full_20260916/gist"
    inputs = REPO / "artifacts/query_splits/gist/shared"
    query, gt = inputs / "validation_query.fvecs", inputs / "validation_gt.ivecs"
    count, dim = fvecs_to_bin(query, out / "validation.bin")
    if dim != 960 or count != 200:
        raise ValueError("expected unchanged GIST validation split (200 x 960)")
    if not 0 <= args.warmup_queries <= count:
        raise ValueError("invalid warmup count")
    import numpy as np
    order = np.arange(count)
    if args.query_order:
        order = np.fromfile(args.query_order, dtype='<u4')
        if sorted(order.tolist()) != list(range(count)):
            raise ValueError("query order must be a complete permutation")
        rows = np.fromfile(out / 'validation.bin', dtype='<f4', offset=8).reshape(count, dim)
        with (out / 'validation.bin').open('wb') as f:
            f.write(struct.pack('<II', count, dim))
            f.write(rows[order].tobytes())
    command = json.loads((REPO / "docs/analysis/starling_memory_recovery_20260917/w32_as4g_command.json").read_text())
    command[command.index("--query_file") + 1] = str(out / "validation.bin")
    command[command.index("--result_path") + 1] = str(out / "result")
    command[command.index("-L") + 1:command.index("-W")] = [str(w) for w in args.widths]
    cpus = [int(x) for x in Path("/sys/devices/system/node/node0/cpulist").read_text().strip().split(",")][:32]
    if len(cpus) != 32 or not set(cpus) <= os.sched_getaffinity(0):
        raise ValueError("preflight requires 32 permitted CPUs on NUMA node 0")
    lower = starling_memory_lower_bound(index / "index",out / "validation.bin",index / "nav_index",32)
    record = dict(status="preparing", diagnostic_only=True, formal_ready=False, performance_sample=False,
                  method="Starling-Disk",dataset="gist",phase="validation",workers=32,widths=args.widths,
                  query_count=count,source_commit=source["commit"],binary_sha256=sha256(command[0]),
                  query_sha256=sha256(query),groundtruth_sha256=sha256(gt),command=command,
                  memory_lower_bound=lower, planned_budget_gib=args.budget_gib,
                  cpu_affinity=cpus,memory_numa_binding=args.numa_node)
    record.update(warmup_queries=args.warmup_queries,
                  query_order_sha256=sha256(args.query_order) if args.query_order else None)
    write_json(out / "preflight.json", record)
    try:
        prepare_search(command, out / "storage_precondition.json", "Starling-Disk")
        measured = run_measured(command,evidence_path=out / "resources.json",log_path=out / "terminal.log",
                                rss_budget=True,budget_bytes=int(args.budget_gib*(1<<30)),cpu_affinity=cpus,numa_node=args.numa_node,
                                env=dict(os.environ,OMP_NUM_THREADS="32",MKL_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",QG05_OFFICIAL_TRACE_PREFIX=str(out/"query_stats"),QG05_WARMUP_QUERIES=str(args.warmup_queries)),
                                timeout=900)
        record.update(status=measured["status"],budget_admitted=measured["budget_admitted"],
                      observed_peak_rss_bytes=measured["observed_peak_rss_bytes"],sampled_swap_bytes=measured["sampled_swap_bytes"],
                      resources_sha256=sha256(out / "resources.json"),
                      storage_precondition_sha256=sha256(out / "storage_precondition.json"))
        if measured["status"] != "completed":
            raise RuntimeError(f"preflight failed: {out / 'resources.json'}")
        # Independently verify output IDs and exact distances against FP32 input.
        import numpy as np
        base = np.memmap(REPO / "data/gist/gist_base.fvecs",dtype="<f4",mode="r",shape=(1_000_000,961))[:,1:]
        queries = np.memmap(query,dtype="<f4",mode="r",shape=(count,961))[:,1:][order]
        with gt.open("rb") as f: k = struct.unpack("<I",f.read(4))[0]
        truth = np.memmap(gt,dtype="<u4",mode="r",shape=(count,k+1))[:,1:11][order]
        verified = []
        for width in record["widths"]:
            ids_path = out / f"result_{width}_idx_uint32.bin"
            dist_path = out / f"result_{width}_dists_float.bin"
            for path in (ids_path,dist_path):
                with path.open("rb") as f:
                    if struct.unpack("<II",f.read(8)) != (count,10): raise ValueError("native result shape mismatch")
            ids = np.fromfile(ids_path,dtype="<u4",offset=8).reshape(count,10)
            distances = np.fromfile(dist_path,dtype="<f4",offset=8).reshape(count,10)
            if (ids >= len(base)).any() or any(len(set(row)) != 10 for row in ids):
                raise ValueError("invalid or duplicate result IDs")
            errors = []
            recalls = []
            for i,row in enumerate(ids):
                diff = np.asarray(base[row],dtype=np.float64)-np.asarray(queries[i],dtype=np.float64)
                expected = np.sum(diff*diff,axis=1)
                if not np.allclose(expected,distances[i],rtol=2e-5,atol=2e-5):
                    raise ValueError("reported distance differs from original input")
                errors.append(float(np.max(np.abs(expected-distances[i]))))
                recalls.append(len(set(row)&set(truth[i]))/10)
            verified.append(dict(width=width,valid_unique_ids=True,max_absolute_distance_error=max(errors),
                                 recall_at_10=float(np.mean(recalls)),ids_sha256=sha256(ids_path),distances_sha256=sha256(dist_path)))
        verify_source(REPO,"starling")
        record["output_validation"] = verified
        record["note"] = "Validation diagnostics with explicit query order, per-width untimed warmup and native query metrics. Unified formal adapter and tuning/test locks remain required; do not use QPS as paper evidence."
    except BaseException as exc:
        record.update(status="failed",error=str(exc))
        raise
    finally:
        write_json(out / "preflight.json",record)
    print(json.dumps(record,indent=2))


if __name__ == "__main__": main()
