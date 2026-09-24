#!/usr/bin/env python3
"""Test-mode runner for the 05 disk suite (not the formal experiment).

Executes the 05A/05B/05C disk suite as a *test* experiment and separates the
work into two independent parts:

  A. build / graph-construction statistics:
     - 05B shared graph + Ours graph are rebuilt in memory by the 02
       ``run_diskann_fair`` binary; graph-construction time, peak RSS, encode
       time and instrumented graph-build distance evaluations are recorded;
     - 05A quantizer train/encode and every disk export capture wall time,
       peak RSS and storage read bytes via ``QG05_CAPTURE_BUILD_STATS=1``;
     - 05C third-party systems (SymphonyQG / OG-LVQ / Glass) report their
       official build.json stats (distance counts unavailable); DiskANN-PQ
       builds during export.
  B. query statistics per worker round (recall / qps / I-O, Ours staged
     db1/full4/rerank page reads), with one full 05A+05B+05C pass and plot per
     worker count.

Worker rounds run in descending order 32 -> 16 -> 8 -> 4 -> 2 -> 1; each round
finishes all three layers and its figures before the next round starts.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SUITE = REPO_ROOT / "src" / "disk_bench" / "run_disk_suite.py"
PLOTTER = REPO_ROOT / "src" / "disk_bench" / "plot_05_disk_suite.py"
REGISTRY_WRITER = REPO_ROOT / "scripts" / "write_05_ports_local.py"
BIN02 = (
    REPO_ROOT
    / "src"
    / "graph_core"
    / "target"
    / "release"
    / "run_diskann_fair"
)

from layout import LAYER_DIRS, run_metadata_root, dataset_run_root
LAYER_METHODS = {
    "05a": "PQ_4bit,SQ_4bit,SAQ_B4,Ours_RaBitQ_K1",
    "05b": "PQ-DiskANN-Disk,SQ-DiskANN-Disk,SAQ-DiskANN-Disk,Ours-Disk",
    "05c": (
        "Ours-Disk,DiskANN-PQ-Disk,AiSAQ-Disk,Starling-Disk"
    ),
}
DEFAULT_WORKERS = (32, 16, 8, 4, 2, 1)
SEED = 20260813
BUDGET = 2.0
FULL_CURVE_WIDTHS = (
    tuple(range(1, 31))
    + tuple(range(40, 101, 10))
    + tuple(range(140, 461, 40))
    + (480,)
)
FULL_CURVE_WIDTHS_ENV = ",".join(str(value) for value in FULL_CURVE_WIDTHS)

# 05C third-party build records (configs match the FAST tuning lock).
THIRD_PARTY_BUILDS = {
    "SymphonyQG-DiskPort": ("SymphonyQG", "SymphonyQG_R64_EF400_t3_build.json"),
    "OG-LVQ-DiskPort": ("OG-LVQ", "OG-LVQ_LVQ4_R64_W400_build.json"),
    "Glass-NSG-DiskPort": ("Glass-NSG", "Glass-NSG_R64_L400_build.json"),
    "Ours-Disk": ("Ours", "Ours_OursDiskANN_M64_build.json"),
}


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["QG05_FAST"] = "1"
    env["QG05_OURS_ABLATIONS"] = "db1+coalescing+reuse"
    env["QG05_CAPTURE_BUILD_STATS"] = "1"
    local = REPO_ROOT / "baselines" / "deps" / "local"
    lib = local / "usr" / "lib" / "x86_64-linux-gnu"
    if lib.exists():
        env["LD_LIBRARY_PATH"] = (
            f"{lib}:{lib / 'openblas-pthread'}:{env.get('LD_LIBRARY_PATH', '')}"
        )
    return env


def run_cmd(
    args: list[str],
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
    log_path: Path | None = None,
) -> subprocess.CompletedProcess:
    print("[run] " + " ".join(str(a) for a in args), flush=True)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w") as stream:
            proc = subprocess.run(
                args, stdout=stream, stderr=subprocess.STDOUT, text=True, env=env
            )
    else:
        proc = subprocess.run(args, text=True, env=env)
    if check and proc.returncode:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(str(a) for a in args)}"
        )
    return proc


def suite_flags(
    args: argparse.Namespace,
    *,
    layers: str = "05a,05b,05c",
) -> list[str]:
    return [
        sys.executable,
        str(SUITE),
        "--layers", layers,
        "--datasets", args.dataset,
        "--ports", str(args.ports.resolve()),
        "--disk-root", str(args.disk_root.resolve()),
        "--disk-profile", args.disk_profile,
        "--out-root", str(args.out_root.resolve()),
        "--search-dram-budget-gib", str(BUDGET),
    ]


def _phase_call(
    args: argparse.Namespace,
    run_id: str,
    phase: str,
    layers: str,
    workers: int,
    log_path: Path,
    fast_widths: str | None = None,
) -> None:
    flags = suite_flags(args, layers=layers)
    methods = [
        m
        for layer in layers.split(",")
        for m in LAYER_METHODS[layer].split(",")
        if m not in args.exclude_methods
    ]
    if methods != [m for layer in layers.split(",") for m in LAYER_METHODS[layer].split(",")]:
        flags += ["--methods", ",".join(methods)]
    env = _env()
    if fast_widths is not None:
        env["QG05_FAST_WIDTHS"] = fast_widths
    run_cmd(
        flags
        + [
            "--phase", phase,
            "--run-id", run_id,
            "--workers", str(workers),
            "--repeats", "1",
            "--seed", str(SEED),
        ],
        env=env,
        log_path=log_path,
    )


def query_splits(dataset: str) -> tuple[Path, Path]:
    split_dir = (
        REPO_ROOT / "results" / "03_system_fair" / dataset / "csv" / "_query_splits"
    )
    query = split_dir / "test_query.fvecs"
    gt = split_dir / "test_gt.ivecs"
    if not query.exists() or not gt.exists():
        raise RuntimeError(f"query splits missing under {split_dir}")
    return query, gt


def read_graph_build_stats(
    args: argparse.Namespace,
    run_root: Path,
) -> list[dict[str, str]]:
    """Load the graph-build rows already captured under this test run."""
    csv_path = (
        run_root
        / "02build"
        / "02_diskann_fair"
        / args.dataset
        / "csv"
        / "diskann_fair_raw.csv"
    )
    if not csv_path.exists():
        return []
    with csv_path.open() as stream:
        rows = list(csv.DictReader(stream))
    wanted = {"PQ": "shared_graph", "Ours": "ours_graph"}
    stats = []
    for row in rows:
        role = wanted.get(row.get("method", ""))
        if role:
            stats.append(
                {
                    "part": role,
                    "method": row.get("method", ""),
                    "graph_build_mode": row.get("graph_build_mode", ""),
                    "graph_build_time_ms": row.get("graph_build_time_ms", ""),
                    "graph_build_distance_evaluations": row.get(
                        "graph_build_distance_evaluations", ""
                    ),
                    "encode_time_ms": row.get("encode_time_ms", ""),
                    "build_time_ms": row.get("build_time_ms", ""),
                    "peak_rss_mb": row.get("peak_rss_mb", ""),
                }
            )
    return stats


def build_graphs(
    args: argparse.Namespace,
    run_root: Path,
    log_dir: Path,
) -> list[dict[str, str]]:
    """Rebuild shared + Ours graphs in memory (02 diskann fair) and record stats."""
    dataset = args.dataset
    if not BIN02.exists():
        raise RuntimeError(
            f"{BIN02} missing; build with `bash scripts/build_formal_local.sh`"
        )
    query, gt = query_splits(dataset)
    build_out = run_root / "02build"
    build_out.mkdir(parents=True, exist_ok=True)
    common = [
        str(BIN02),
        "--dataset", dataset,
        "--data-root", str((REPO_ROOT / "data").resolve()),
        "--out-root", str(build_out.resolve()),
        "--max-degree", "64",
        "--build-beam", "400",
        "--alpha", "1.2",
        "--seed", str(SEED),
        "--refine-passes", "1",
        "--build-prune-cap", "256",
        "--build-early-stop-hops", "2",
        "--search-list-sizes", "10",
        "--repeats", "1",
        "--threads", str(args.build_threads),
        "--query-path", str(query.resolve()),
        "--gt-path", str(gt.resolve()),
    ]
    # Shared baseline graph (PQ/SQ/SAQ share this file).
    run_cmd(
        common + ["--methods", "PQ", "--shared-graph"],
        env=_env(),
        log_path=log_dir / "02_shared_graph_build.log",
    )
    # Ours native graph.
    run_cmd(
        common + ["--methods", "Ours"],
        env=_env(),
        log_path=log_dir / "02_ours_graph_build.log",
    )

    dest_root = REPO_ROOT / "artifacts" / "indexes" / "legacy_aliases" / dataset / "indexes" / "02_diskann_fair"
    shared_src = (
        build_out
        / dataset
        / "indexes"
        / "02_diskann_fair"
        / "shared_graph"
        / "diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"
    )
    ours_src = (
        build_out
        / dataset
        / "indexes"
        / "02_diskann_fair"
        / "Ours"
        / f"{dataset}_Ours_R64_Lbuild400.graph.bin"
    )
    for src, rel in (
        (shared_src, "shared_graph/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.bin"),
        (ours_src, f"Ours/{dataset}_Ours_R64_Lbuild400.graph.bin"),
    ):
        if not src.exists():
            raise RuntimeError(f"graph build did not produce {src}")
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"[build] graph -> {dst}", flush=True)

    return read_graph_build_stats(args, run_root)


def run_export(
    args: argparse.Namespace,
    run_id: str,
    log_dir: Path,
) -> None:
    _phase_call(
        args, run_id, "export", "05a,05b", 32, log_dir / "05_export_ab.log"
    )
    _phase_call(
        args, run_id, "export", "05c", 32, log_dir / "05_export_c.log"
    )


def run_validate(
    args: argparse.Namespace,
    run_id: str,
    log_dir: Path,
) -> None:
    """Native validate phase: writes the parity.passed markers the test
    phase requires (05A resident-vs-disk, 05B/05C memory-vs-direct)."""
    _phase_call(
        args, run_id, "validate", "05a,05b", 32, log_dir / "05_validate_ab.log"
    )
    _phase_call(
        args, run_id, "validate", "05c", 32, log_dir / "05_validate_c.log"
    )


def run_round(
    args: argparse.Namespace,
    run_id: str,
    workers: int,
    log_dir: Path,
) -> Path:
    _phase_call(
        args, run_id, "run", "05a", workers, log_dir / f"05_run_a_w{workers}.log"
    )
    _phase_call(
        args,
        run_id,
        "run",
        "05b",
        workers,
        log_dir / f"05_run_b_w{workers}.log",
        fast_widths=FULL_CURVE_WIDTHS_ENV,
    )
    _phase_call(
        args,
        run_id,
        "run",
        "05c",
        workers,
        log_dir / f"05_run_c_w{workers}.log",
        fast_widths=FULL_CURVE_WIDTHS_ENV,
    )
    for plot_layers, methods in (
        ("05a,05b", [m for m in ",".join(LAYER_METHODS["05a"].split(",") + LAYER_METHODS["05b"].split(",")).split(",")]),
        ("05c", LAYER_METHODS["05c"].split(",")),
    ):
        filtered = [m for m in methods if m not in args.exclude_methods]
        plot_cmd = [
            sys.executable,
            str(PLOTTER),
            "--run-root", str(run_metadata_root(args.out_root.resolve(), run_id)),
            "--layers", plot_layers,
            "--datasets", args.dataset,
            "--workers", str(workers),
            "--methods", ",".join(filtered),
        ]
        run_cmd(
            plot_cmd,
            env=_env(),
            log_path=log_dir / f"05_plot_{plot_layers.replace(',', '')}_w{workers}.log",
        )
    round_dir = args.out_root / "rounds" / f"round_w{workers}"
    round_dir.mkdir(parents=True, exist_ok=True)
    for layer, layer_dir in LAYER_DIRS.items():
        aggregate = (
            dataset_run_root(run_metadata_root(args.out_root.resolve(), run_id), layer, args.dataset) / "tables"
            / "formal_test_rows.csv"
        )
        if aggregate.exists():
            shutil.copy2(aggregate, round_dir / f"{layer}_rows.csv")
        figures = (
            dataset_run_root(run_metadata_root(args.out_root.resolve(), run_id), layer, args.dataset) / "figures"
        )
        if figures.exists():
            archived = round_dir / "figures" / layer
            archived.mkdir(parents=True, exist_ok=True)
            for figure in figures.iterdir():
                if figure.is_file():
                    shutil.copy2(figure, archived / figure.name)
    return round_dir


def read_third_party_build_stats(dataset: str) -> list[dict[str, str]]:
    base = REPO_ROOT / "artifacts" / "indexes" / "legacy_aliases" / dataset / "indexes" / "03_system_fair"
    stats = []
    for method, (dir_name, file_name) in THIRD_PARTY_BUILDS.items():
        path = base / dir_name / file_name
        if not path.exists():
            stats.append(
                {
                    "part": "05c_build",
                    "method": method,
                    "note": f"missing official build record {path.relative_to(REPO_ROOT)}",
                }
            )
            continue
        record = json.loads(path.read_text())
        stats.append(
            {
                "part": "05c_build",
                "method": method,
                "build_time_ms": str(record.get("build_time_ms", "")),
                "graph_build_time_ms": str(record.get("graph_build_time_ms", "")),
                "peak_rss_mb": str(record.get("peak_rss_mb", "")),
                "index_size_mb": str(record.get("index_size_mb", "")),
                "graph_build_distance_evaluations": "unavailable",
                "note": "third-party official build record",
            }
        )
    return stats


def read_export_sidecars(
    args: argparse.Namespace, run_id: str
) -> list[dict[str, str]]:
    run_root = run_metadata_root(args.out_root.resolve(), run_id)
    stats = []
    for layer, layer_dir in LAYER_DIRS.items():
        artifacts = dataset_run_root(run_root, layer, args.dataset) / "raw"
        for sidecar in sorted(artifacts.glob("*/export/*.build_stats.json")):
            record = json.loads(sidecar.read_text())
            stats.append(
                {
                    "part": "export",
                    "layer": layer,
                    "file": sidecar.name.replace(".build_stats.json", ""),
                    "wall_seconds": str(record.get("wall_seconds", "")),
                    "peak_rss_bytes": str(record.get("peak_rss_bytes", "")),
                    "read_bytes": str(record.get("read_bytes", "")),
                }
            )
    return stats


def selected_row(rows: list[dict[str, str]], target_recall: float = 0.95):
    """Pick the row whose recall is closest to the paper target."""
    best = None
    best_delta = float("inf")
    for row in rows:
        try:
            recall = float(row.get("recall", "nan"))
        except ValueError:
            continue
        delta = abs(recall - target_recall)
        if delta < best_delta:
            best = row
            best_delta = delta
    return best


def _byte_value(row: dict[str, str], key: str) -> int:
    try:
        return int(float(row.get(key, "0") or 0))
    except (TypeError, ValueError):
        return 0


def collect_round_queries(
    args: argparse.Namespace,
) -> dict[str, dict[str, dict[str, dict[str, str]]]]:
    """round -> layer -> method(+ablation) -> selected aggregate row."""
    result: dict[str, dict[str, dict[str, dict[str, str]]]] = {}
    for round_dir in sorted(
        (args.out_root / "rounds").glob("round_w*"), key=lambda p: int(p.name.split("_w")[1])
    ):
        workers = int(round_dir.name.split("_w")[1])
        round_rows: dict[str, dict[str, dict[str, str]]] = {}
        for layer, layer_dir in LAYER_DIRS.items():
            csv_path = round_dir / f"{layer}_rows.csv"
            if not csv_path.exists():
                continue
            with csv_path.open() as stream:
                rows = [r for r in csv.DictReader(stream)]
            rows = [r for r in rows if r.get("workers", "") == str(workers)]
            by_method: dict[str, dict[str, str]] = {}
            for method in LAYER_METHODS[layer].split(","):
                method_rows = [
                    r for r in rows if r.get("method") == method
                    and r.get("ablation", "") in ("", "db1+coalescing+reuse")
                ]
                chosen = selected_row(method_rows)
                if chosen is not None:
                    by_method[method] = chosen
            round_rows[layer] = by_method
        result[str(workers)] = round_rows
    return result


def write_summary(
    args: argparse.Namespace,
    run_id: str,
    build_stats: list[dict[str, str]],
    third_party: list[dict[str, str]],
    export_sidecars: list[dict[str, str]],
) -> tuple[Path, Path]:
    summary_dir = args.out_root / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    csv_path = summary_dir / "summary.csv"
    md_path = summary_dir / "summary.md"

    all_rows: list[dict[str, str]] = []
    for record in build_stats + third_party + export_sidecars:
        row = {
            "section": "A_build",
            "run_id": run_id,
            "dataset": args.dataset,
        }
        row.update(record)
        all_rows.append(row)

    rounds = collect_round_queries(args)
    for workers, layers in sorted(
        rounds.items(), key=lambda kv: int(kv[0]), reverse=True
    ):
        for layer, methods in layers.items():
            for method, row in methods.items():
                ours_4bit_payload = _byte_value(row, "ours_4bit_payload_bytes")
                ours_8bit_payload = _byte_value(row, "ours_8bit_payload_bytes")
                ours_adjacency = _byte_value(row, "ours_adjacency_bytes")
                is_ours = method == "Ours-Disk"
                ours_4bit_index = ours_4bit_payload + ours_adjacency
                ours_8bit_index = ours_8bit_payload + ours_adjacency
                all_rows.append(
                    {
                        "section": "B_query",
                        "run_id": run_id,
                        "dataset": args.dataset,
                        "part": "query",
                        "workers": workers,
                        "layer": layer,
                        "method": method,
                        "recall": row.get("recall", ""),
                        "qps": row.get("qps", ""),
                        "index_size_mb": row.get("index_size_mb", ""),
                        "resident_bytes": row.get("resident_bytes", ""),
                        "peak_rss_bytes": row.get("peak_rss_bytes", ""),
                        "ours_4bit_payload_bytes": row.get(
                            "ours_4bit_payload_bytes", ""
                        ),
                        "ours_8bit_payload_bytes": row.get(
                            "ours_8bit_payload_bytes", ""
                        ),
                        "ours_adjacency_bytes": row.get(
                            "ours_adjacency_bytes", ""
                        ),
                        "ours_fp32_base_bytes": row.get(
                            "ours_fp32_base_bytes", ""
                        ),
                        "ours_4bit_index_bytes": (
                            str(ours_4bit_index) if is_ours else ""
                        ),
                        "ours_8bit_index_bytes": (
                            str(ours_8bit_index) if is_ours else ""
                        ),
                        "ours_4bit_index_mb": (
                            f"{ours_4bit_index / (1 << 20):.6f}" if is_ours else ""
                        ),
                        "ours_8bit_index_mb": (
                            f"{ours_8bit_index / (1 << 20):.6f}" if is_ours else ""
                        ),
                        "io_requests_per_query": row.get("io_requests_per_query", ""),
                        "bytes_read_per_query": row.get("bytes_read_per_query", ""),
                        "io_wait_us": row.get("io_wait_us", ""),
                        "db1_checks": row.get("db1_checks", ""),
                        "db1_survivors": row.get("db1_survivors", ""),
                        "full4_page_reads": row.get("full4_page_reads", ""),
                        "rerank_page_reads": row.get("rerank_page_reads", ""),
                    }
                )

    fieldnames = [
        "section", "run_id", "dataset", "part", "layer", "method", "workers",
        "file", "wall_seconds", "peak_rss_bytes", "read_bytes",
        "graph_build_mode", "graph_build_time_ms", "graph_build_distance_evaluations",
        "encode_time_ms", "build_time_ms", "peak_rss_mb", "index_size_mb",
        "resident_bytes",
        "ours_4bit_payload_bytes", "ours_8bit_payload_bytes",
        "ours_adjacency_bytes", "ours_fp32_base_bytes",
        "ours_4bit_index_bytes", "ours_8bit_index_bytes",
        "ours_4bit_index_mb", "ours_8bit_index_mb",
        "recall", "qps", "io_requests_per_query", "bytes_read_per_query",
        "io_wait_us", "db1_checks", "db1_survivors", "full4_page_reads",
        "rerank_page_reads", "note",
    ]
    with csv_path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    lines: list[str] = [
        f"# 05 disk-suite test run `{run_id}` (dataset={args.dataset})",
        "",
        "Test-mode run: `QG05_FAST=1`, budget=2 GiB, repeats=1, "
        "Ours ablation=`db1+coalescing+reuse`.",
        "",
        "## A. Build / graph-construction statistics (independent part 1)",
        "",
        "| part | method/layer | graph_build_time_ms | distance_evaluations | "
        "encode_time_ms | build_time_ms | peak_rss_mb | note |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for record in build_stats + third_party:
        lines.append(
            "| {part} | {method} | {graph_build_time_ms} | "
            "{graph_build_distance_evaluations} | {encode_time_ms} | "
            "{build_time_ms} | {peak_rss_mb} | {note} |".format(
                **{
                    "part": record.get("part", ""),
                    "method": record.get("method", record.get("layer", "")),
                    "graph_build_time_ms": record.get("graph_build_time_ms", ""),
                    "graph_build_distance_evaluations": record.get(
                        "graph_build_distance_evaluations", ""
                    ),
                    "encode_time_ms": record.get("encode_time_ms", ""),
                    "build_time_ms": record.get("build_time_ms", ""),
                    "peak_rss_mb": record.get("peak_rss_mb", ""),
                    "note": record.get("note", ""),
                }
            )
        )
    lines += [
        "",
        "Export phase sidecars (wall_seconds / peak_rss_bytes / read_bytes):",
        "",
        "| layer | artifact | wall_seconds | peak_rss_bytes | read_bytes |",
        "|---|---|---|---|---|",
    ]
    for record in export_sidecars:
        lines.append(
            "| {layer} | {file} | {wall_seconds} | {peak_rss_bytes} | {read_bytes} |".format(
                **record
            )
        )

    lines += [
        "",
        "## B. Query statistics per worker round (independent part 2)",
        "",
        "Selected row nearest Recall@10 = 0.95 per method at each worker count.",
        "",
        "| workers | layer | method | recall | qps | io_req/query | "
        "bytes/query | io_wait_us | db1_checks | full4_pages | rerank_pages |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for workers, layers in sorted(
        rounds.items(), key=lambda kv: int(kv[0]), reverse=True
    ):
        for layer in ("05a", "05b", "05c"):
            for method, row in layers.get(layer, {}).items():
                lines.append(
                    "| {workers} | {layer} | {method} | {recall} | {qps} | "
                    "{io} | {bytes} | {wait} | {db1_checks} | {full4} | {rerank} |".format(
                        workers=workers,
                        layer=layer,
                        method=method,
                        recall=row.get("recall", ""),
                        qps=row.get("qps", ""),
                        io=row.get("io_requests_per_query", ""),
                        bytes=row.get("bytes_read_per_query", ""),
                        wait=row.get("io_wait_us", ""),
                        db1_checks=row.get("db1_checks", ""),
                        full4=row.get("full4_page_reads", ""),
                        rerank=row.get("rerank_page_reads", ""),
                    )
                )
    lines += [
        "",
        "## Ours storage accounting",
        "",
        "Logical 4-bit index = 4-bit payload + adjacency; logical 8-bit "
        "index = (4-bit + residual) payload + adjacency. The full-precision "
        "base-vector file is separate from these index totals.",
        "",
        "| layer | 4-bit payload bytes | 8-bit payload bytes | adjacency bytes | "
        "4-bit index MiB | 8-bit index MiB | FP32 bytes inside measured index |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    storage_rows: dict[str, dict[str, str]] = {}
    for layers in rounds.values():
        for layer in ("05b", "05c"):
            ours = layers.get(layer, {}).get("Ours-Disk")
            if ours is not None:
                storage_rows.setdefault(layer, ours)
    for layer in ("05b", "05c"):
        row = storage_rows.get(layer)
        if row is None:
            continue
        payload4 = _byte_value(row, "ours_4bit_payload_bytes")
        payload8 = _byte_value(row, "ours_8bit_payload_bytes")
        adjacency = _byte_value(row, "ours_adjacency_bytes")
        fp32 = _byte_value(row, "ours_fp32_base_bytes")
        lines.append(
            f"| {layer} | {payload4} | {payload8} | {adjacency} | "
            f"{(payload4 + adjacency) / (1 << 20):.3f} | "
            f"{(payload8 + adjacency) / (1 << 20):.3f} | {fp32} |"
        )
    lines += ["", "## Storage / I/O", ""]
    preflight = (
        run_metadata_root(args.out_root.resolve(), run_id)
        / "preflight.json"
    )
    fio = (
        run_metadata_root(args.out_root.resolve(), run_id)
        / "fio_preflight.json"
    )
    for path, label in ((preflight, "preflight"), (fio, "fio")):
        if path.exists():
            record = json.loads(path.read_text())
            lines.append(f"### {label}")
            lines.append("```json")
            lines.append(json.dumps(record, indent=2))
            lines.append("```")
    lines.append("")
    md_path.write_text("\n".join(lines))
    return csv_path, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="gist")
    parser.add_argument("--run-id", default="")
    parser.add_argument(
        "--out-root",
        type=Path,
        default=REPO_ROOT / "results" / "diagnostics" / "smoke_runs",
    )
    parser.add_argument(
        "--disk-root",
        type=Path,
        default=REPO_ROOT / "work" / "05_disk_system_fair" / "disk_root",
    )
    parser.add_argument("--disk-profile", default="auto")
    parser.add_argument(
        "--ports",
        type=Path,
        default=REPO_ROOT
        / "src"
        / "disk_bench"
        / "ports.local.json",
    )
    parser.add_argument(
        "--workers-list",
        default="32,16,8,4,2,1",
        help="descending worker rounds (comma separated)",
    )
    parser.add_argument("--build-threads", type=int, default=32)
    parser.add_argument("--skip-doctor", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument(
        "--exclude-methods",
        default="",
        help="comma-separated methods to exclude from 05C validate/run/plot "
        "(e.g. a hung third-party port)",
    )
    args = parser.parse_args(argv)

    workers = tuple(int(w) for w in args.workers_list.split(",") if w.strip())
    if workers != tuple(sorted(workers, reverse=True)):
        raise RuntimeError("worker rounds must be descending (32 -> 1)")
    run_id = args.run_id or (
        f"{args.dataset}_test_{time.strftime('%Y%m%d_%H%M%S')}"
    )
    args.out_root = args.out_root.resolve() / args.dataset / run_id
    args.exclude_methods = {
        m.strip() for m in args.exclude_methods.split(",") if m.strip()
    }
    run_root = run_metadata_root(args.out_root, run_id)
    log_dir = args.out_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_doctor:
        run_cmd(
            suite_flags(args) + ["--phase", "doctor", "--run-id", run_id],
            env=_env(),
            log_path=log_dir / "doctor.log",
            check=False,
        )

    build_stats = []
    if not args.skip_build:
        build_stats = build_graphs(args, run_root, log_dir)
    else:
        print("[build] skipped (--skip-build); reusing existing graphs", flush=True)
        build_stats = read_graph_build_stats(args, run_root)
        if not build_stats:
            print(
                "[build] warning: prior graph-build CSV not found; "
                "build-stat summary will be empty",
                flush=True,
            )

    run_export(args, run_id, log_dir)
    run_validate(args, run_id, log_dir)
    for workers_value in workers:
        print(
            f"== round workers={workers_value}: 05A+05B+05C ==",
            flush=True,
        )
        run_round(args, run_id, workers_value, log_dir)
        print(f"== round workers={workers_value} done ==", flush=True)

    third_party = read_third_party_build_stats(args.dataset)
    sidecars = read_export_sidecars(args, run_id)
    csv_path, md_path = write_summary(args, run_id, build_stats, third_party, sidecars)
    print(f"[done] summary: {md_path}", flush=True)
    print(f"[done] summary csv: {csv_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
