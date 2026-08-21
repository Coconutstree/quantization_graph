#!/usr/bin/env python3
"""Render GIST1M query-codec sweep charts as dependency-free SVG files."""

from __future__ import annotations

import argparse
import csv
from html import escape
from pathlib import Path


SERIES = {
    "full": {"color": "#2563EB", "dash": "", "marker": "circle", "filled": True},
    "b1": {"color": "#D97706", "dash": "9 5", "marker": "square", "filled": False},
    "int4": {"color": "#6B8E23", "dash": "12 4 2 4", "marker": "triangle", "filled": True},
    "int8": {"color": "#C2417A", "dash": "2 4", "marker": "diamond", "filled": False},
}
ORDER = tuple(SERIES)
INK = "#202124"
MUTED = "#62666D"
GRID = "#DDE1E6"
AXIS = "#7A7F87"
BG = "#FCFCFD"


def load_rows(path: Path) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            rows.append(
                {
                    key: value if key == "codec" else float(value)
                    for key, value in raw.items()
                }
            )
    if len(rows) != 36:
        raise ValueError(f"expected 36 aggregate rows, got {len(rows)}")
    return rows


def marker(shape: str, x: float, y: float, color: str, filled: bool) -> str:
    fill = color if filled else BG
    common = f'fill="{fill}" stroke="{color}" stroke-width="2"'
    if shape == "circle":
        return f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5" {common}/>'
    if shape == "square":
        return f'<rect x="{x-5:.2f}" y="{y-5:.2f}" width="10" height="10" {common}/>'
    if shape == "triangle":
        points = f"{x:.2f},{y-6:.2f} {x-6:.2f},{y+5:.2f} {x+6:.2f},{y+5:.2f}"
        return f'<polygon points="{points}" {common}/>'
    points = f"{x:.2f},{y-7:.2f} {x-6:.2f},{y:.2f} {x:.2f},{y+7:.2f} {x+6:.2f},{y:.2f}"
    return f'<polygon points="{points}" {common}/>'


def text(
    x: float,
    y: float,
    body: str,
    *,
    size: int = 16,
    fill: str = INK,
    anchor: str = "start",
    weight: int = 400,
    rotate: int | None = None,
) -> str:
    transform = f' transform="rotate({rotate} {x:.2f} {y:.2f})"' if rotate else ""
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" fill="{fill}" '
        f'text-anchor="{anchor}" font-weight="{weight}" '
        f'dominant-baseline="middle"{transform}>{escape(body)}</text>'
    )


def scale(value: float, domain: tuple[float, float], start: float, span: float) -> float:
    low, high = domain
    return start + (value - low) * span / (high - low)


def add_legend(parts: list[str], x: float, y: float) -> None:
    for index, codec in enumerate(ORDER):
        style = SERIES[codec]
        lx = x + index * 175
        dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
        parts.append(
            f'<line x1="{lx:.2f}" y1="{y:.2f}" x2="{lx+42:.2f}" y2="{y:.2f}" '
            f'stroke="{style["color"]}" stroke-width="3"{dash}/>'
        )
        parts.append(marker(style["marker"], lx + 21, y, style["color"], style["filled"]))
        parts.append(text(lx + 52, y, codec, size=16, weight=600))


def add_panel(
    parts: list[str],
    rows: list[dict[str, float | str]],
    *,
    panel_id: str,
    panel_title: str,
    x_field: str,
    y_field: str,
    x_domain: tuple[float, float],
    y_domain: tuple[float, float],
    x_ticks: list[float],
    y_ticks: list[float],
    x_label: str,
    y_label: str,
    plot_x: float,
    plot_y: float,
    plot_w: float,
    plot_h: float,
    x_format,
    y_format,
    error_fields: tuple[str, str] | None = None,
    annotate_efs: tuple[int, ...] = (),
) -> None:
    parts.append(text(plot_x, plot_y - 36, panel_title, size=18, weight=650))
    parts.append(f'<clipPath id="{panel_id}"><rect x="{plot_x}" y="{plot_y}" width="{plot_w}" height="{plot_h}"/></clipPath>')

    for value in y_ticks:
        py = plot_y + plot_h - scale(value, y_domain, 0, plot_h)
        parts.append(f'<line x1="{plot_x}" y1="{py:.2f}" x2="{plot_x+plot_w}" y2="{py:.2f}" stroke="{GRID}" stroke-width="1"/>')
        parts.append(text(plot_x - 14, py, y_format(value), size=13, fill=MUTED, anchor="end"))
    for value in x_ticks:
        px = scale(value, x_domain, plot_x, plot_w)
        parts.append(f'<line x1="{px:.2f}" y1="{plot_y}" x2="{px:.2f}" y2="{plot_y+plot_h}" stroke="{GRID}" stroke-width="1"/>')
        parts.append(text(px, plot_y + plot_h + 24, x_format(value), size=13, fill=MUTED, anchor="middle"))

    parts.append(f'<line x1="{plot_x}" y1="{plot_y+plot_h}" x2="{plot_x+plot_w}" y2="{plot_y+plot_h}" stroke="{AXIS}" stroke-width="1.5"/>')
    parts.append(f'<line x1="{plot_x}" y1="{plot_y}" x2="{plot_x}" y2="{plot_y+plot_h}" stroke="{AXIS}" stroke-width="1.5"/>')
    parts.append(text(plot_x + plot_w / 2, plot_y + plot_h + 62, x_label, size=15, anchor="middle", weight=550))
    parts.append(text(plot_x - 70, plot_y + plot_h / 2, y_label, size=15, anchor="middle", weight=550, rotate=-90))

    for codec in ORDER:
        style = SERIES[codec]
        points = sorted(
            (row for row in rows if row["codec"] == codec),
            key=lambda row: float(row["ef_search"]),
        )
        coords: list[tuple[float, float, dict[str, float | str]]] = []
        for row in points:
            x_value = float(row[x_field])
            y_value = float(row[y_field])
            px = scale(x_value, x_domain, plot_x, plot_w)
            py = plot_y + plot_h - scale(y_value, y_domain, 0, plot_h)
            coords.append((px, py, row))

        dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
        poly = " ".join(f"{px:.2f},{py:.2f}" for px, py, _ in coords)
        parts.append(
            f'<polyline points="{poly}" fill="none" stroke="{style["color"]}" '
            f'stroke-width="3" stroke-linejoin="round" stroke-linecap="round"{dash} clip-path="url(#{panel_id})"/>'
        )
        for px, py, row in coords:
            if error_fields:
                lower = plot_y + plot_h - scale(float(row[error_fields[0]]), y_domain, 0, plot_h)
                upper = plot_y + plot_h - scale(float(row[error_fields[1]]), y_domain, 0, plot_h)
                parts.append(f'<line x1="{px:.2f}" y1="{lower:.2f}" x2="{px:.2f}" y2="{upper:.2f}" stroke="{style["color"]}" stroke-width="1.3" opacity="0.65"/>')
                parts.append(f'<line x1="{px-4:.2f}" y1="{lower:.2f}" x2="{px+4:.2f}" y2="{lower:.2f}" stroke="{style["color"]}" stroke-width="1.3" opacity="0.65"/>')
                parts.append(f'<line x1="{px-4:.2f}" y1="{upper:.2f}" x2="{px+4:.2f}" y2="{upper:.2f}" stroke="{style["color"]}" stroke-width="1.3" opacity="0.65"/>')
            parts.append(marker(style["marker"], px, py, style["color"], style["filled"]))
            ef = int(float(row["ef_search"]))
            if codec == "int8" and ef in annotate_efs:
                parts.append(text(px + 9, py - 13, f"ef={ef}", size=11, fill=MUTED))


def svg_document(width: int, height: int, body: list[str]) -> str:
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            f'<rect width="100%" height="100%" fill="{BG}"/>',
            '<style>text { font-family: "DejaVu Sans", Arial, sans-serif; }</style>',
            *body,
            "</svg>",
        ]
    )


def build_recall_qps(rows: list[dict[str, float | str]], output: Path) -> None:
    parts = [
        text(70, 44, "Recall@10 vs QPS", size=28, weight=700),
        text(70, 78, "GIST1M, 800 queries, single search thread; mean and observed range from two order-reversed runs", size=15, fill=MUTED),
    ]
    add_legend(parts, 255, 118)
    qps_rows = [dict(row, recall_pct=float(row["recall_at_10"]) * 100.0) for row in rows]
    add_panel(
        parts,
        qps_rows,
        panel_id="qps_clip",
        panel_title="Ordered efSearch sweep (20 to 500)",
        x_field="recall_pct",
        y_field="qps_mean",
        x_domain=(75.0, 99.0),
        y_domain=(0.0, 3000.0),
        x_ticks=[75, 80, 85, 90, 95, 99],
        y_ticks=[0, 500, 1000, 1500, 2000, 2500, 3000],
        x_label="Recall@10",
        y_label="Queries per second",
        plot_x=105,
        plot_y=180,
        plot_w=1010,
        plot_h=445,
        x_format=lambda value: f"{value:.0f}%",
        y_format=lambda value: f"{value:,.0f}",
        error_fields=("qps_min", "qps_max"),
        annotate_efs=(100, 200, 500),
    )
    parts.append(text(70, 686, "Whiskers show the two-run observed QPS range; they are not confidence intervals.", size=13, fill=MUTED))
    output.write_text(svg_document(1200, 720, parts), encoding="utf-8")


def build_recall_cost(rows: list[dict[str, float | str]], output: Path) -> None:
    cost_rows = [dict(row, recall_pct=float(row["recall_at_10"]) * 100.0) for row in rows]
    parts = [
        text(70, 42, "Recall@10 vs Search Cost", size=28, weight=700),
        text(70, 76, "Algorithmic survivor cost (left) and measured coarse-kernel time (right); same 36 search points", size=15, fill=MUTED),
    ]
    add_legend(parts, 445, 112)
    common = dict(
        parts=parts,
        rows=cost_rows,
        y_field="recall_pct",
        y_domain=(75.0, 99.0),
        y_ticks=[75, 80, 85, 90, 95, 99],
        y_label="Recall@10",
        plot_y=176,
        plot_w=620,
        plot_h=430,
        y_format=lambda value: f"{value:.0f}%",
        annotate_efs=(),
    )
    add_panel(
        **common,
        panel_id="calls_clip",
        panel_title="Remaining 4-bit distance calls per query",
        x_field="remaining_4bit_calls",
        x_domain=(0.0, 7500.0),
        x_ticks=[0, 1500, 3000, 4500, 6000, 7500],
        x_label="Remaining 4-bit calls / query",
        plot_x=90,
        x_format=lambda value: "0" if value == 0 else f"{value/1000:.1f}k",
    )
    add_panel(
        **common,
        panel_id="time_clip",
        panel_title="Measured coarse-kernel time per query",
        x_field="coarse_kernel_us_mean",
        x_domain=(0.0, 6000.0),
        x_ticks=[0, 1200, 2400, 3600, 4800, 6000],
        x_label="paper_batch_us + flush_us (microseconds)",
        plot_x=880,
        x_format=lambda value: "0" if value == 0 else f"{value/1000:.1f}k",
    )
    parts.append(text(70, 674, "Cost is averaged over 800 queries; timing is the mean of two order-reversed runs.", size=13, fill=MUTED))
    output.write_text(svg_document(1600, 710, parts), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aggregate", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    rows = load_rows(args.aggregate)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    build_recall_qps(rows, args.out_dir / "recall_qps.svg")
    build_recall_cost(rows, args.out_dir / "recall_cost.svg")
    print(args.out_dir / "recall_qps.svg")
    print(args.out_dir / "recall_cost.svg")


if __name__ == "__main__":
    main()
