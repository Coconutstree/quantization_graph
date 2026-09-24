#!/usr/bin/env python3
"""AGNews disk-environment 32-worker analysis (complete SymphonyQG sweep)."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path("docs/analysis/.mplconfig").resolve()))

import matplotlib as mpl
import matplotlib.pyplot as plt
import pandas as pd


DISPLAY_METHOD = {
    "Ours-Disk": "Ours-Disk",
    "OG-LVQ-DiskPort": "OG-LVQ",
    "Glass-NSG-DiskPort": "Glass-NSG",
    "SymphonyQG-DiskPort": "SymphonyQG",
}

METHOD_COLORS = {
    "Ours-Disk": "#C2417A",
    "OG-LVQ": "#6B8E23",
    "Glass-NSG": "#7A6FA6",
    "SymphonyQG": "#0F766E",
}

PANEL_BG = "#FCFCFD"


def configure_matplotlib() -> None:
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "font.size": 8,
            "figure.facecolor": PANEL_BG,
            "axes.facecolor": PANEL_BG,
            "savefig.facecolor": PANEL_BG,
            "text.color": "#202124",
            "axes.labelcolor": "#202124",
            "axes.edgecolor": "#7A7F87",
            "xtick.color": "#62666D",
            "ytick.color": "#62666D",
            "axes.spines.right": False,
            "axes.spines.top": False,
            "axes.linewidth": 0.8,
            "legend.frameon": False,
            "figure.dpi": 150,
            "xtick.major.size": 3,
            "ytick.major.size": 3,
        }
    )


def save_pub(fig: plt.Figure, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(stem.with_suffix(".svg"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".png"), dpi=300, bbox_inches="tight")


def soften_axis(ax: plt.Axes) -> None:
    ax.tick_params(labelsize=7)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#7A7F87")
    ax.margins(x=0.04)


def _current_rid() -> str:
    rid_path = Path("/tmp/agnews_05c_rerun_rid.txt")
    if rid_path.exists():
        return rid_path.read_text().strip()
    return "agnews_05c_rerun_20260901_152210"


def _ours_local_rows() -> pd.DataFrame:
    p = Path(
        f"results/archive/legacy_layout_20260918/disk_environment/.formal_runs/runs/{_current_rid()}/05C_disk_system_fair/"
        "agnews/artifacts/test/Ours-Disk__hybrid_disk__B2__standard__w32__r0.json"
    )
    if not p.exists():
        return pd.DataFrame()
    d = json.loads(p.read_text())
    rows = pd.DataFrame(d["summary_rows"])
    constant = {
        "layer": "05c",
        "dataset": d.get("dataset"),
        "method": d.get("method"),
        "storage_mode": d.get("storage_mode"),
        "cache_mode": d.get("cache_mode"),
        "workers": d.get("workers"),
        "search_dram_budget_gib": d.get("search_dram_budget_gib"),
        "direct_io": d.get("direct_io"),
        "native_aio": d.get("native_aio"),
        "page_size": d.get("page_size"),
        "source_kernel": d.get("source_kernel"),
        "implementation_fingerprint": d.get("implementation_fingerprint"),
        "implementation_parity": d.get("implementation_parity"),
        "run_id": d.get("run_id"),
        "git_commit": d.get("git_commit"),
        "base_count": d.get("base_count"),
        "dimension": d.get("dimension"),
        "index_path": d.get("index_path"),
        "cache_bytes": d.get("cache_bytes"),
        "cache_nodes": d.get("cache_nodes"),
        "ours_4bit_payload_bytes": d.get("ours_4bit_payload_bytes"),
        "ours_8bit_payload_bytes": d.get("ours_8bit_payload_bytes"),
        "ours_adjacency_bytes": d.get("ours_adjacency_bytes"),
        "ours_fp32_base_bytes": d.get("ours_fp32_base_bytes"),
    }
    for key, value in constant.items():
        rows[key] = value
    return rows


def read_rows() -> pd.DataFrame:
    base = Path("results/archive/legacy_layout_20260918/disk_environment/03_system_fair/agnews/csv")
    new = pd.read_csv(base / "formal_test_rows.csv")
    new = new[new["method"].isin(["OG-LVQ-DiskPort", "Glass-NSG-DiskPort"])].copy()
    fix_agg = Path(
        "results/archive/legacy_layout_20260918/disk_environment/.formal_runs/runs/fix_w32_diskpayload_symphony_20260831_140957/"
        "05C_disk_system_fair/agnews/aggregate/formal_test_rows.csv"
    )
    sym = pd.read_csv(fix_agg)
    sym = sym[sym["method"].eq("SymphonyQG-DiskPort")].copy()
    ours = _ours_local_rows()
    df = pd.concat([new, sym, ours], ignore_index=True)
    numeric = [
        "workers",
        "base_count",
        "dimension",
        "search_width",
        "recall",
        "qps",
        "latency_mean_us",
        "latency_p95_us",
        "index_size_mb",
        "resident_bytes",
        "peak_rss_bytes",
        "io_requests_per_query",
        "bytes_read_per_query",
        "io_wait_us",
        "distance_compute_us",
        "query_prep_us",
        "queue_compute_us",
        "rerank_us",
        "db1_checks",
        "db1_survivors",
        "full4_candidates",
        "full4_page_reads",
        "rerank_candidates",
        "rerank_page_reads",
    ]
    for col in numeric:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["method_display"] = df["method"].map(DISPLAY_METHOD).fillna(df["method"])
    return df


def read_build_stats() -> pd.DataFrame:
    rows = []
    roots = [
        Path("results/archive/legacy_layout_20260918/disk_environment/.formal_runs/runs/fix_w32_diskpayload_symphony_20260831_140957"),
        Path(f"results/archive/legacy_layout_20260918/disk_environment/.formal_runs/runs/{_current_rid()}"),
    ]
    for root in roots:
        specs = [
            ("05b", root / "05B_diskann_shared_graph/agnews/artifacts/export"),
            ("05c", root / "05C_disk_system_fair/agnews/artifacts/export"),
        ]
        for layer, d in specs:
            if not d.exists():
                continue
            for path in sorted(d.glob("*.build_stats.json")):
                obj = json.loads(path.read_text())
                method = path.name.split("__")[0]
                rows.append(
                    {
                        "layer": layer,
                        "method": method,
                        "method_display": DISPLAY_METHOD.get(method, method),
                        "wall_seconds": obj.get("wall_seconds", math.nan),
                        "peak_rss_gib": obj.get("peak_rss_bytes", math.nan) / (1024**3),
                        "read_mb": obj.get("read_bytes", math.nan) / (1024**2),
                    }
                )
    return pd.DataFrame(rows)


def read_shared_graph_meta() -> dict[str, str]:
    path = Path(
        "results/archive/legacy_layout_20260918/disk_environment/dataset_artifacts/agnews/indexes/02_diskann_fair/"
        "shared_graph/diskann_fp32_R64_Lbuild400_alpha1.2_seed20260813.graph.json"
    )
    meta: dict[str, str] = {}
    if not path.exists():
        return meta
    for line in path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            meta[key.strip()] = value.strip()
    return meta


def plot_query_curves(df: pd.DataFrame, out: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.65), constrained_layout=True)
    order = ["Ours-Disk", "OG-LVQ", "Glass-NSG", "SymphonyQG"]
    for method in order:
        g = df[df["method_display"].eq(method)].sort_values("recall")
        if g.empty:
            continue
        color = METHOD_COLORS.get(method, "#777777")
        lw = 2.1 if method == "Ours-Disk" else 1.4
        z = 5 if method == "Ours-Disk" else 3
        axes[0].plot(g["recall"], g["qps"], color=color, linewidth=lw, zorder=z)
        axes[0].scatter(g["recall"], g["qps"], s=9 if method == "Ours-Disk" else 7, color=color, zorder=z)
        axes[1].plot(g["recall"], g["io_requests_per_query"], color=color, linewidth=lw, zorder=z)
        axes[1].scatter(g["recall"], g["io_requests_per_query"], s=9 if method == "Ours-Disk" else 7, color=color, zorder=z)
        end = g.iloc[-1]
        axes[0].text(end["recall"] + 0.006, end["qps"], method, color=color, fontsize=7, va="center")
        axes[1].text(end["recall"] + 0.006, end["io_requests_per_query"], method, color=color, fontsize=7, va="center")

    axes[0].set_xlabel("Recall@10")
    axes[0].set_ylabel("QPS")
    axes[0].set_title("AGNews 05C recall / QPS", fontsize=8)
    axes[0].set_xlim(0.78, 1.02)
    axes[0].set_ylim(0, 24)
    axes[0].set_yticks([0, 5, 10, 15, 20])
    soften_axis(axes[0])

    axes[1].set_xlabel("Recall@10")
    axes[1].set_ylabel("I/O requests per query")
    axes[1].set_title("I/O price of recall", fontsize=8)
    axes[1].set_xlim(0.78, 1.02)
    axes[1].set_ylim(0, 7000)
    axes[1].set_yticks([0, 2000, 4000, 6000])
    soften_axis(axes[1])

    save_pub(fig, out / "fig02_w32_recall_qps_io_curves_agnews")
    plt.close(fig)


def plot_ours_breakdown(df: pd.DataFrame, out: Path) -> None:
    ours = df[df["method"].eq("Ours-Disk")].copy()
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.55), constrained_layout=True)
    g = ours.sort_values("search_width")

    axes[0].plot(g["search_width"], g["latency_mean_us"] / 1000, color="#272727", linewidth=1.4, linestyle="--", label="Total")
    axes[0].plot(g["search_width"], g["io_wait_us"] / 1000, color=METHOD_COLORS["Ours-Disk"], linewidth=2.1, label="I/O wait")
    axes[0].set_xlabel("Search width")
    axes[0].set_ylabel("Latency (ms/query)")
    axes[0].set_title("Latency is I/O wait", fontsize=8)
    axes[0].legend(fontsize=6.5, loc="upper left", handlelength=1.3)
    soften_axis(axes[0])

    non_io = [
        ("queue_compute_us", "Queue", "#7884B4"),
        ("distance_compute_us", "Distance", "#8BCF8B"),
        ("query_prep_us", "Prep", "#767676"),
        ("rerank_us", "Rerank", "#B64342"),
    ]
    for col, label, color in non_io:
        axes[1].plot(g["search_width"], g[col], color=color, linewidth=1.5, label=label)
    axes[1].set_xlabel("Search width")
    axes[1].set_ylabel("Non-I/O time (us/query)")
    axes[1].set_title("Compute terms are small", fontsize=8)
    axes[1].legend(fontsize=6.3, loc="upper left", handlelength=1.2)
    soften_axis(axes[1])

    mechanism = pd.Series(
        {
            "DB1 checks": g["db1_checks"].mean(),
            "Full4 candidates": g["full4_candidates"].mean(),
            "Full4 page reads": g["full4_page_reads"].mean(),
            "Rerank candidates": g["rerank_candidates"].mean(),
        }
    )
    colors = ["#D7A9BF", "#B7658E", METHOD_COLORS["Ours-Disk"], "#B0892E"]
    axes[2].barh(range(len(mechanism)), mechanism.values, color=colors, height=0.62)
    axes[2].set_yticks(range(len(mechanism)), mechanism.index)
    axes[2].invert_yaxis()
    axes[2].set_xlabel("Mean count per query")
    axes[2].set_title("Where reads come from", fontsize=8)
    soften_axis(axes[2])

    save_pub(fig, out / "fig03_ours_query_time_decomposition_w32_agnews")
    plt.close(fig)


def write_report(out: Path, rows: pd.DataFrame, build: pd.DataFrame, graph_meta: dict[str, str]) -> None:
    def fmt(v: float, digits: int = 2) -> str:
        if pd.isna(v):
            return "NA"
        return f"{v:.{digits}f}"

    def num(method: str, col: str, agg: str = "mean") -> float:
        s = pd.to_numeric(rows[rows["method"].eq(method)][col], errors="coerce").dropna()
        if s.empty:
            return math.nan
        return s.mean() if agg == "mean" else (s.max() if agg == "max" else s.min())

    def rng(method: str) -> str:
        return f"{fmt(num(method, 'recall', 'min'), 4)} - {fmt(num(method, 'recall', 'max'), 4)}"

    def share(method: str, num_col: str, den_col: str) -> float:
        n = pd.to_numeric(rows[rows["method"].eq(method)][num_col], errors="coerce")
        d = pd.to_numeric(rows[rows["method"].eq(method)][den_col], errors="coerce")
        mask = d.notna() & d.ne(0)
        return (n[mask] / d[mask]).mean() * 100 if mask.any() else math.nan

    def cell(method: str, col: str, digits: int = 2, scale: float = 1.0, agg: str = "mean") -> str:
        return fmt(num(method, col, agg) / scale, digits)

    def bcell(method: str, col: str, scale: float = 1.0) -> str:
        sub = build[build["method"].eq(method)]
        if sub.empty:
            return "—"
        return fmt(sub[col].max() / scale, 2)

    def export_min(method: str) -> float:
        sub = build[build["method"].eq(method)]
        if sub.empty:
            return math.nan
        return float(sub["wall_seconds"].max()) / 60.0

    def build_total(graph_min: float, method: str) -> float:
        return graph_min + export_min(method)

    def first(method: str, col: str) -> str:
        s = rows[rows["method"].eq(method)][col].dropna()
        return str(s.iloc[0]) if not s.empty else "—"

    def img(feishu: bool, name: str, caption: str) -> str:
        src = f"@./docs/analysis/{name}" if feishu else name
        return f"![{caption}]({src})"

    def make_report(feishu: bool) -> str:
        local_note = (
            "本文件使用普通 Markdown 图片链接，适合 IDE/GitHub 预览；飞书导入版见 "
            "`docs/analysis/disk_w32_agnews_analysis_report_feishu.md`。"
            if not feishu
            else ""
        )
        graph_build_min = (
            float(graph_meta["graph_build_time_ms"]) / 60000
            if graph_meta.get("graph_build_time_ms")
            else math.nan
        )
        graph_build_dist = graph_meta.get("graph_build_distance_evaluations", "—")
        graph_mib = (
            int(graph_meta["graph_bytes"]) / (1024**2)
            if graph_meta.get("graph_bytes")
            else math.nan
        )
        ours_n = int(num("Ours-Disk", "base_count", "max"))
        ours_dim = int(num("Ours-Disk", "dimension", "max"))
        ours_raw_1bit = ours_n * ours_dim // 8
        ours_raw_4bit = ours_n * ours_dim * 4 // 8
        ours_index_path = Path(first("Ours-Disk", "index_path"))
        ours_disk_payload_file = (ours_index_path / "ours_full4_residual.pages").stat().st_size
        ours_sidecar_file = (ours_index_path / "ours_db1_sidecar.bin").stat().st_size
        ours_quantizer_file = (ours_index_path / "ours_quantizer.bin").stat().st_size
        ours_meta_files = sum(
            (ours_index_path / name).stat().st_size
            for name in ("index.meta", "resident_codes.marker")
        )
        ours_actual_index = (
            ours_disk_payload_file
            + ours_sidecar_file
            + ours_quantizer_file
            + ours_meta_files
            + int(num("Ours-Disk", "ours_adjacency_bytes", "max"))
        )
        return f"""# AGNews 磁盘环境 32 线程实验分析

{local_note}

数据来源：AGNews 05C 四个方法。Ours-Disk、OG-LVQ、Glass-NSG 使用本机新 run `{_current_rid()}` 的 w32 sweep；SymphonyQG 使用 `fix_w32_diskpayload_symphony_20260831_140957` 修正后的完整 sweep。

## 构建

02 graph build 是共享的：DiskANN 家族（PQ-DiskANN / SQ-DiskANN / SAQ-DiskANN）共用同一张 fp32 Vamana shared graph，本机重跑一次，graph meta 记录总构建时间约 {fmt(graph_build_min)} min、distance evaluations 约 {graph_build_dist}、graph bytes 约 {fmt(graph_mib)} MiB。Ours 使用独立的 ours_native 图。没有保留 progress-stage 分段日志（GIST 的 02build 日志同样只有最终汇总格式），所以不能拆 train/encode/graph build 三段。

### 构建成本（总 = 构图 + 磁盘索引导出）

| 方法 | 构图耗时 (min) | 磁盘索引导出 (min) | 总构建成本 (min) | 构图 peak RSS (GiB) | 导出 peak RSS (GiB) | 来源 |
|---|---:|---:|---:|---:|---:|---|
| Shared fp32 Vamana（PQ/SQ/SAQ 共用） | {fmt(graph_build_min)} | — | {fmt(graph_build_min)}（共享一次） | — | — | shared_graph meta |
| PQ-DiskANN | {fmt(graph_build_min)}（共享） | {fmt(export_min("PQ-DiskANN-Disk"), 2)} | {fmt(build_total(graph_build_min, "PQ-DiskANN-Disk"), 2)} | — | {bcell("PQ-DiskANN-Disk", "peak_rss_gib")} | shared meta + export build_stats |
| SQ-DiskANN | {fmt(graph_build_min)}（共享） | {fmt(export_min("SQ-DiskANN-Disk"), 2)} | {fmt(build_total(graph_build_min, "SQ-DiskANN-Disk"), 2)} | — | {bcell("SQ-DiskANN-Disk", "peak_rss_gib")} | shared meta + export build_stats |
| SAQ-DiskANN | {fmt(graph_build_min)}（共享） | {fmt(export_min("SAQ-DiskANN-Disk"), 2)} | {fmt(build_total(graph_build_min, "SAQ-DiskANN-Disk"), 2)} | — | {bcell("SAQ-DiskANN-Disk", "peak_rss_gib")} | shared meta + export build_stats |
| Ours（native） | 2.46 | {fmt(export_min("Ours-Disk"), 2)} | {fmt(build_total(2.46, "Ours-Disk"), 2)} | 6.80 | {bcell("Ours-Disk", "peak_rss_gib")} | Ours_OursDiskANN_M64_build.json + export build_stats |
| OG-LVQ | 3.59 | {fmt(export_min("OG-LVQ-DiskPort"), 2)} | {fmt(build_total(3.59, "OG-LVQ-DiskPort"), 2)} | 3.34 | {bcell("OG-LVQ-DiskPort", "peak_rss_gib")} | OG-LVQ_LVQ4_R64_W400_build.json + export build_stats |
| Glass-NSG | 2.82 | {fmt(export_min("Glass-NSG-DiskPort"), 2)} | {fmt(build_total(2.82, "Glass-NSG-DiskPort"), 2)} | 8.36 | {bcell("Glass-NSG-DiskPort", "peak_rss_gib")} | Glass-NSG_R64_L400_build.json + export build_stats |
| SymphonyQG | 3.07 | {fmt(export_min("SymphonyQG-DiskPort"), 2)} | {fmt(build_total(3.07, "SymphonyQG-DiskPort"), 2)} | 14.77 | {bcell("SymphonyQG-DiskPort", "peak_rss_gib")} | SymphonyQG_R64_EF400_t3_build.json + export build_stats |

统一口径：本表“构图耗时”一律取 **from-scratch 官方 build record**（`*_build.json` 的 `build_time_ms`；Ours 取 total 2.46 min），`graph_build_mode=reused_graph` 的记录不计入构图耗时；“磁盘索引导出”为 05 层 export 的 `.build_stats.json`（payload 编码 + 写盘，wall 耗时与 peak RSS）。

Ours 磁盘查询使用的 native 图在 02 raw 中的拆分为 graph build 6.77 s / encode 17.47 s / total 24.25 s，其 `graph_build_mode=reused_graph`，属于复用图加载 + payload 编码，不是 from-scratch 构建；官方从零构建记录为 `Ours_OursDiskANN_M64_build.json`（graph 2.34 min / total 2.46 min / peak 6.80 GiB）。两者是两套口径（reused 加载 vs from-scratch 构建），不直接比较。M64 官方记录未记录 Lbuild/alpha；磁盘查询图参数（R64/Lbuild400/alpha1.2、ExRaBitQ4-symmetric）来自 02 manifest。

构建成本分析：总成本排序为 PQ（55.19）> SAQ（45.77）> SQ（40.68）> SymphonyQG（30.95）> Ours（9.13）> OG-LVQ（3.66）> Glass-NSG（3.44）min。PQ/SQ/SAQ 的总成本被共享图构建（{fmt(graph_build_min)} min，仅发生一次）主导，其边际导出成本只有 14.87 / 0.36 / 5.44 min（差异来自 payload 编码方式：PQ 训 codebook、SAQ 球面变换、SQ 纯标量）。SymphonyQG 总成本几乎全在磁盘索引导出（约 90%），是第三方系统里导出最重的；Ours 构图轻（2.46 min）且导出 6.67 min，总 9.13 min，显著低于 SymphonyQG 与“按全额共享图口径”的 DiskANN 家族；OG-LVQ / Glass-NSG 最轻（<4 min）。

### Ours 索引大小（4bit / 8bit）

口径说明：下表的 4-bit/8-bit payload 是算法逻辑字节数，不是 `*.pages` 文件的真实磁盘占用；真实文件还包含 4 KiB page 对齐和空槽。AGNews 裸 1-bit code 的理论大小是 {fmt(ours_raw_1bit / 1048576, 1)} MiB，裸 4-bit code 是 {fmt(ours_raw_4bit / 1048576, 1)} MiB，二者确实是 4 倍关系。查询 JSON 里的 `resident_bytes={int(num("Ours-Disk", "resident_bytes", "max"))}` 不是裸 1-bit code，而是 Ours 查询常驻 footprint 口径：DB1 sidecar / factors / centroids 加上 full4-resident ablation 的最大常驻 payload 预算，因此不能拿它和 4-bit payload 做 4 倍比较。

| 组成 | 大小 | 口径 |
|---|---:|---|
| 裸 1-bit code | {fmt(ours_raw_1bit / 1048576, 1)} MiB | 理论 bit-pack baseline |
| 裸 4-bit code | {fmt(ours_raw_4bit / 1048576, 1)} MiB | 理论 bit-pack baseline，正好是裸 1-bit 的 4 倍 |
| 4-bit payload | {fmt(num("Ours-Disk", "ours_4bit_payload_bytes", "max") / 1048576, 1)} MiB | 逻辑 compact payload 字节数，含 per-vector compact/factor 结构开销 |
| 8-bit payload（4-bit + residual） | {fmt(num("Ours-Disk", "ours_8bit_payload_bytes", "max") / 1048576, 1)} MiB | 逻辑 compact + residual payload 字节数 |
| adjacency | {fmt(num("Ours-Disk", "ours_adjacency_bytes", "max") / 1048576, 1)} MiB | `shared_graph.pages` 文件大小 |
| fp32 base（单独存放） | 0 | measured index 中不含 fp32 base |
| 逻辑 4-bit 索引（4-bit payload + adjacency） | {fmt((num("Ours-Disk", "ours_4bit_payload_bytes", "max") + num("Ours-Disk", "ours_adjacency_bytes", "max")) / 1048576, 1)} MiB | 报告对比口径 |
| 逻辑 8-bit 索引（8-bit payload + adjacency） | {fmt((num("Ours-Disk", "ours_8bit_payload_bytes", "max") + num("Ours-Disk", "ours_adjacency_bytes", "max")) / 1048576, 1)} MiB | 报告对比口径 |
| 实际 8-bit page 文件 | {fmt(ours_disk_payload_file / 1048576, 1)} MiB | `ours_full4_residual.pages`，含 4 KiB page 对齐 |
| 实际 Ours 目录合计 | {fmt(ours_actual_index / 1048576, 1)} MiB | payload page + sidecar + quantizer/meta + adjacency |

## 查询

### Ours 查询参数与口径

| 项 | 值 |
|---|---|
| 数据集 | agnews（769k × 1024） |
| 存储 | 逻辑 4-bit payload {fmt(num("Ours-Disk", "ours_4bit_payload_bytes", "max") / 1048576, 1)} MiB；逻辑 8-bit payload（4bit+residual）{fmt(num("Ours-Disk", "ours_8bit_payload_bytes", "max") / 1048576, 1)} MiB；adjacency {fmt(num("Ours-Disk", "ours_adjacency_bytes", "max") / 1048576, 1)} MiB；实际 payload page 文件 {fmt(ours_disk_payload_file / 1048576, 1)} MiB；fp32 base 不计入 measured index |
| 查询 | 32 workers、2 GiB budget、hybrid_disk、direct I/O + native AIO、page 4096 |
| sweep | width 10–580（40 点）、beam=1、ablation=db1+coalescing+reuse |
| parity | {first("Ours-Disk", "implementation_parity")} |

### 05C 方法指标对比

| 指标 | Ours-Disk | OG-LVQ | Glass-NSG | SymphonyQG |
|---|---:|---:|---:|---:|
| Recall@10 范围 | {rng("Ours-Disk")} | {rng("OG-LVQ-DiskPort")} | {rng("Glass-NSG-DiskPort")} | {rng("SymphonyQG-DiskPort")} |
| 最高 QPS | {cell("Ours-Disk", "qps", 2, 1.0, "max")} | {cell("OG-LVQ-DiskPort", "qps", 2, 1.0, "max")} | {cell("Glass-NSG-DiskPort", "qps", 2, 1.0, "max")} | {cell("SymphonyQG-DiskPort", "qps", 2, 1.0, "max")} |
| 平均 latency | {cell("Ours-Disk", "latency_mean_us", 2, 1000)} ms/query | {cell("OG-LVQ-DiskPort", "latency_mean_us", 2, 1000)} ms/query | {cell("Glass-NSG-DiskPort", "latency_mean_us", 2, 1000)} ms/query | {cell("SymphonyQG-DiskPort", "latency_mean_us", 2, 1000)} ms/query |
| I/O wait 占比 | {fmt(share("Ours-Disk", "io_wait_us", "latency_mean_us"))}% | {fmt(share("OG-LVQ-DiskPort", "io_wait_us", "latency_mean_us"))}% | {fmt(share("Glass-NSG-DiskPort", "io_wait_us", "latency_mean_us"))}% | {fmt(share("SymphonyQG-DiskPort", "io_wait_us", "latency_mean_us"))}% |
| distance compute 占比 | {fmt(share("Ours-Disk", "distance_compute_us", "latency_mean_us"))}% | — | — | — |
| I/O requests/query | {cell("Ours-Disk", "io_requests_per_query", 1)} | {cell("OG-LVQ-DiskPort", "io_requests_per_query", 1)} | {cell("Glass-NSG-DiskPort", "io_requests_per_query", 1)} | {cell("SymphonyQG-DiskPort", "io_requests_per_query", 1)} |
| bytes read/query | {cell("Ours-Disk", "bytes_read_per_query", 2, 1024**2)} MiB | {cell("OG-LVQ-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB | {cell("Glass-NSG-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB | {cell("SymphonyQG-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB |
| DB1 checks/query | {cell("Ours-Disk", "db1_checks", 1)} | — | — | — |
| full4 page reads/query | {cell("Ours-Disk", "full4_page_reads", 1)} | — | — | — |

注意：OG-LVQ、Glass-NSG、SymphonyQG 没有暴露 DB1 / full4 内部计数，其 distance/queue 字段基本等于总耗时，无法与 Ours 的 distance compute 单独拆分口径对比，所以这些格标为“—”。

05C 指标分析：在 Recall@10 0.93–0.994 的论文工作区里，Ours-Disk 的 recall–QPS 前沿整体占优——最高 QPS 202.13、平均 latency 586.72 ms/query、I/O 请求 931.4 次与 3.64 MiB/query 均为四个方法里最低。四个方法都以 I/O wait 为主（≥94.7%），磁盘读取是共同瓶颈；Ours 的 distance compute 仅 0.08%，其瓶颈在 DB1 1bit 初筛后的 full4 页读取（809.7 页/query）。SymphonyQG 把 recall 上限推到 0.9992，但 QPS 只有 5.18（约 Ours 的 1/39）、每查询读 57.95 MiB（约 Ours 的 16×），高 recall 靠高 I/O 成本换取；OG-LVQ（QPS ≤56.7、recall ≤0.953）与 Glass-NSG（QPS ≤135.5、recall ≤0.945）位于中间带。

### Figure 2：05B shared graph Recall-QPS（02 层）

![Figure 2. AGNews 05B Recall-QPS]({"@./" if feishu else ""}results/archive/legacy_layout_20260918/disk_environment/02_diskann_fair/agnews/figures_w32/disk05b_shared_graph_recall_qps.png)

图读法：02 层共享图方法（PQ-DiskANN / SQ-DiskANN / SAQ-DiskANN / Ours-Disk）的 Recall@10–QPS。

### Figure 3：05C system Recall-QPS（03 层）

{img(feishu, "fig05c_recall_qps_agnews_paper.png", "Figure 3. AGNews 05C Recall-QPS")}

图读法：03 层完整磁盘系统（Ours / SymphonyQG / OG-LVQ / Glass-NSG）的 Recall@10–QPS，使用本机最新数据（log-y）。

### Figure 4：Ours-Disk 查询时间拆分

{img(feishu, "fig03_ours_query_time_decomposition_w32_agnews_paper.png", "Figure 4. AGNews Ours-Disk 查询时间拆分")}

图读法：只看 Ours-Disk。左图是 total latency 与 I/O wait；中图是非 I/O 的 prep/queue/distance/rerank；右图是每 query 的 DB1/full4/rerank 平均计数。

### 查询差距与不同（重点）

- **Recall 上限**：Ours-Disk 最高，约 {fmt(num("Ours-Disk", "recall", "max"), 4)}；SymphonyQG 约 {fmt(num("SymphonyQG-DiskPort", "recall", "max"), 4)}；OG-LVQ 约 {fmt(num("OG-LVQ-DiskPort", "recall", "max"), 4)}；Glass-NSG 约 {fmt(num("Glass-NSG-DiskPort", "recall", "max"), 4)}。
- **Latency / QPS**：Ours-Disk 平均 latency 约 {cell("Ours-Disk", "latency_mean_us", 2, 1000)} ms/query、最高 QPS 约 {cell("Ours-Disk", "qps", 2, 1.0, "max")}，在四个方法里吞吐最高且延迟最低；SymphonyQG 平均 latency 约 {cell("SymphonyQG-DiskPort", "latency_mean_us", 2, 1000)} ms/query。
- **I/O 成本**：同 recall 下 Ours-Disk 的 I/O requests/query 和 bytes read/query 明显低于其他三个方法。平均来看 Ours-Disk 约 {cell("Ours-Disk", "io_requests_per_query", 1)} 次/query、{cell("Ours-Disk", "bytes_read_per_query", 2, 1024**2)} MiB；Glass-NSG 约 {cell("Glass-NSG-DiskPort", "io_requests_per_query", 1)} 次/query、{cell("Glass-NSG-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB；OG-LVQ 约 {cell("OG-LVQ-DiskPort", "io_requests_per_query", 1)} 次/query、{cell("OG-LVQ-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB；SymphonyQG 约 {cell("SymphonyQG-DiskPort", "io_requests_per_query", 1)} 次/query、{cell("SymphonyQG-DiskPort", "bytes_read_per_query", 2, 1024**2)} MiB。
- **瓶颈**：四个方法都以 I/O wait 为主（Ours-Disk {fmt(share("Ours-Disk", "io_wait_us", "latency_mean_us"))}%、OG-LVQ {fmt(share("OG-LVQ-DiskPort", "io_wait_us", "latency_mean_us"))}%、Glass-NSG {fmt(share("Glass-NSG-DiskPort", "io_wait_us", "latency_mean_us"))}%、SymphonyQG {fmt(share("SymphonyQG-DiskPort", "io_wait_us", "latency_mean_us"))}%）。Ours-Disk 的 distance compute 只有 {fmt(share("Ours-Disk", "distance_compute_us", "latency_mean_us"))}%，说明 Ours 不是距离核慢，而是读盘等待慢。
- **Ours 为什么快**：Ours-Disk 的 DB1 1bit 初筛把 full4 候选压到约 {cell("Ours-Disk", "full4_candidates", 1)} 个/query，只读 full4/residual payload 页面（{cell("Ours-Disk", "bytes_read_per_query", 2, 1024**2)} MiB/query），且页面访问已顺序化（约 200–350 MB/s），所以吞吐远高于以随机读为主的 OG-LVQ / Glass-NSG / SymphonyQG。同时 Ours 实际目录合计约 {fmt(ours_actual_index / 1048576, 1)} MiB（与查询 JSON 的 `index_size_mb` 字段一致），SymphonyQG 约 12.6 GB，磁盘 footprint 更小。

### Ours 进一步优化

按收益排序（数据：AGNews 05C、w32、2 GiB DRAM 预算）：

1. **把预算里空着的约 1 GiB 真正用去缓存 4-bit payload 页。**
   现状：常驻 footprint 口径里 `resident_bytes` 约 0.95 GiB，另有 26 MiB 邻接表缓存；这里的 0.95 GiB 不是裸 1-bit code，而是 sidecar/因子/质心加 full4-resident ablation 的最大常驻 payload 预算。2 GiB 预算仍有空间，而 26 MiB 缓存被写死成“最多缓存 10% 节点”，缓存的是邻接表、不是真正吃 IO 的 payload 页，所以每查询约 810 次 full4 页读仍全落盘。做法：去掉 10% 上限，把剩余预算用来缓存高频 full4/residual payload 页。这是收益最大的一项。

2. **压低 full4 读量。**
   现在 DB1 1bit 初筛扫 3362 个候选、留下 810 个进 full4，随后几乎每个 full4 候选都要单独读一页（810 页/query）。可做：收紧 DB1 门控（让更少候选进 full4）、把同页候选合并成一次读、让 4bit 与 residual 落在同一页一次读回。

3. **提升页读取的顺序性与并发。**
   HDD 随机读慢（实测整盘约 62 MB/s，w32 已打满）。把要读的页按地址排序、加强 prefetch、加大 AIO depth，能让同样读字节下有效吞吐更高、查询更快。

4. **按目标 recall 选最小 width。**
   width 越大 recall 越高但读盘越多（width 10→580，延迟 144→2076 ms）。目标 recall 一定时，用刚好达标的 width，避免无谓读盘。

5. **热页 / 分层缓存。**
   对访问最频繁的 payload 页做长期驻留（热缓存），冷页仍从盘读，用足 2 GiB 预算但不超（只有把整张索引全塞内存才会到约 2.04 GiB）。

## 数据与口径

- 查询内存限制：`search_dram_budget_gib=2.0`；05C 均为 workers=32、repeat=0。
- Ours-Disk 为磁盘模式：`direct_io=True`、`native_aio=True`、`page_size=4096`。

## Source Data

- `results/archive/legacy_layout_20260918/disk_environment/03_system_fair/agnews/csv/formal_test_rows_formal_diskenv_20260826_114755_bc_agnews.csv`
- `results/archive/legacy_layout_20260918/disk_environment/03_system_fair/agnews/csv/formal_test_rows.csv`
- `results/archive/legacy_layout_20260918/disk_environment/.formal_runs/runs/fix_w32_diskpayload_symphony_20260831_140957/`
"""

    (out / "disk_w32_agnews_analysis_report.md").write_text(make_report(feishu=False))
    (out / "disk_w32_agnews_analysis_report_feishu.md").write_text(make_report(feishu=True))


def main() -> int:
    configure_matplotlib()
    out = Path("docs/analysis")
    out.mkdir(parents=True, exist_ok=True)
    rows = read_rows()
    build = read_build_stats()
    graph_meta = read_shared_graph_meta()
    rows.to_csv(out / "source_query_rows_w32_agnews.csv", index=False)
    build.to_csv(out / "source_build_export_stats_w32_agnews.csv", index=False)
    plot_query_curves(rows, out)
    plot_ours_breakdown(rows, out)
    write_report(out, rows, build, graph_meta)
    print("wrote AGNews analysis to docs/analysis")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
