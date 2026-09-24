# Ours — editable core overview (Figure 2)

## Files

- `ours-core-overview.drawio`: primary deliverable; native mxGraphModel, three named panel layers plus background, editable text, bit cells, nodes, and connectors. No embedded raster.
- `ours-core-overview.svg`: vector preview with live SVG text.
- `ours-core-overview.pdf`: vector PDF, embedded Arial/Arial Bold font subsets, selectable text, no image objects.
- `ours-core-overview.png`: PDF-rendered convenience preview, not the paper source.
- `caption.md`: English manuscript caption and Chinese scope notes.
- `build_figure.py`: reproducible native diagram builder and limited-subset exporter.
- `qa.json`, `qa.md`, `pdf-text.txt`: structural, typography, provenance, and visual audit.
- `reference/`: ImageGen composition reference only. It is not embedded or traced into any final asset.

## Edit and export

Open `ours-core-overview.drawio` in draw.io Desktop or https://app.diagrams.net/ using **File → Open from → Device**. Text, bit cells, candidate nodes, and connectors are native objects, not a flattened image. Panel A/B/C are separate layers. Edit an individual object directly or select a whole panel layer for bulk changes.

Export modified files from draw.io as SVG/PDF; select the complete diagram and preserve text where available. The provided export has a white canvas, 178 × 86.03 mm. Most labels are approximately 7.1–8.4 pt at that width; the smallest secondary labels are 6.73 pt. Do not shrink to single-column width. Venue typography must be checked against the actual manuscript template, which was not provided.

There is no draw.io Desktop CLI in the current environment. The delivered SVG is rendered **from the generated draw.io XML**, then converted to PDF by CairoSVG. This is a local vector fallback, not a draw.io Desktop export. It supports this file's rectangles, rounded rectangles, ellipses, plain text, polylines, explicit source/target attachment points, and arrowheads. Arbitrary draw.io shapes, HTML labels, rotations, groups, or compressed saved diagrams require the native draw.io exporter. Native-app rendering has not been independently inspected.

Rebuild the initial layout (overwrites the generated diagram and previews):

```bash
python -m pip install cairosvg pymupdf
python figures/ours-core-overview/build_figure.py
```

Render edits to uncompressed XML within the supported subset without rebuilding the diagram:

```bash
python figures/ours-core-overview/build_figure.py --export-only
```

For this session only, dependencies were installed to `/tmp/ours-figure-render-deps`; the command used was:

```bash
PYTHONPATH=/tmp/ours-figure-render-deps python figures/ours-core-overview/build_figure.py
```

No project-wide Python packages or method/experiment source files were changed. Both code and the standalone .drawio can be retained; after manual changes, use export-only or native export rather than rebuilding the original design.

## Manuscript insertion

Use the PDF as a double-column method figure. Keep the title and full caption outside the artwork. With the supplied physical dimensions, insert near 178 mm wide, adapting only after checking the venue template.

```latex
\begin{figure*}[t]
  \centering
  \includegraphics[width=0.98\textwidth]{figures/ours-core-overview/ours-core-overview.pdf}
  \caption{Primary-derived routing for selective SSD access. ...}
  \label{fig:ours-core-overview}
\end{figure*}
```

Replace the abbreviated caption with `caption.md`. In particular, retain the resident-routing scope and INT8 query-kernel note.

## Scientific provenance

The evidence table is in `docs/ours_core_overview_figure_plan.md`. The figure depicts the audited primary-derived MSB path only. Residual codeword values and candidate counts are illustrative, not measured data. No experimental claims or external icons are included.

The built-in ImageGen tool created a composition reference. Its useful aspects were blue/orange role coding, an explicit DRAM/SSD boundary, and aligned precision views. The final layout was independently rebuilt with native shapes. The reference's `full precision` description of primary 4-bit, ambiguous arrows, gradients, lengthy labels, and cylinder decorations were not retained. See `reference/prompt.txt` for the generation prompt.
