"""Draw the 03/05 paper figures from admitted table rows (nature-figure skill, Python).

Figure contract
  core conclusion (03)
      Under a shared 4 GiB process RAM budget, Ours-Disk matches the official
      DiskANN-PQ disk baseline below ~90% Recall@10 and is 1.4-3.6x faster at
      93-98% recall; DiskANN is faster only below ~90% recall and is the only
      method that reaches >=99%, above Ours' 100-candidate rerank cap.
  evidence chain
      (a) hero panel  - Recall@10 vs QPS for Ours vs DiskANN, linear tightened QPS
      (b) matched-recall speedup - Ours/DiskANN QPS ratio at fixed recall gates,
          with the >=99% band marked unreachable for Ours
      (c) context panel - all four methods, log QPS, every admitted point
  archetype
      quantitative grid with an asymmetric hero panel
  export contract
      180 x 72 mm, >=7 pt glyphs, editable SVG/PDF text, 600 dpi TIFF, 300 dpi PNG,
      source CSV, caption and provenance written next to the figure.

No interpolation, no smoothing, no Pareto filtering: all 36 admitted points are
plotted; matched-recall gates use the first width that reaches the gate.
"""
import csv
import hashlib
import json
import math
import os
from pathlib import Path

os.environ.setdefault('MPLCONFIGDIR', '/tmp/gist-paper-matplotlib')
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import NullFormatter, ScalarFormatter

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = [10, 20, 40, 60, 100, 160, 240, 400, 580]

# nature-figure PALETTE: hero blue, one distinct baseline hue per competitor.
SIGNAL = '#0F4D92'      # blue_main   - Ours
BASELINE = '#B64342'    # red_strong  - DiskANN (main baseline)
ACCENT = '#42949E'      # teal        - SymphonyQG disk port
CONTRAST = '#9A4D8E'    # violet      - Glass-NSG disk port
MUTED = '#767676'
FLOOR = '#E5E5E5'

plt.rcParams.update({
    'font.family': 'sans-serif',
    'font.sans-serif': ['Arial', 'DejaVu Sans', 'Liberation Sans'],
    'svg.fonttype': 'none',
    'pdf.fonttype': 42,
    'font.size': 7,
    'axes.labelsize': 7.5,
    'legend.fontsize': 6.8,
    'xtick.labelsize': 7,
    'ytick.labelsize': 7,
    'axes.spines.top': False,
    'axes.spines.right': False,
    'axes.linewidth': 0.7,
    'xtick.major.width': 0.6,
    'ytick.major.width': 0.6,
    'legend.frameon': False,
})

SYSTEM_GROUPS = [
    ('Ours-Disk', 'Ours', SIGNAL, 'o', '-', 1.5),
    ('DiskANN-PQ-Disk', 'DiskANN', BASELINE, 's', '-', 1.2),
    ('SymphonyQG-DiskPort', 'SymphonyQG (disk port)', ACCENT, '^', '-', 1.2),
    ('Glass-NSG-DiskPort', 'Glass-NSG (disk port)', CONTRAST, 'D', '-', 1.2),
]
# Glass-NSG stays below 80% recall, so it cannot appear in the 80-100% window used by
# the main figure; it remains in the companion SI panel and in the source data.
MAIN_GROUPS = SYSTEM_GROUPS[:3]
XMIN, XMAX = 80.0, 100.4
GATES = [(0.90, '90%'), (0.93, '93%'), (0.95, '95%'), (0.97, '97%'), (0.98, '98%')]


def _curve(rows, method):
    batch = sorted([r for r in rows if r['method'] == method], key=lambda r: int(r['search_width']))
    assert [int(r['search_width']) for r in batch] == WIDTHS, method
    return batch


def _first_at(curve, gate):
    """First measured width reaching the recall gate; no interpolation by design."""
    for row in curve:
        if float(row['recall']) >= gate:
            return row
    return None


def _plot_series(ax, curve, label, color, marker, linestyle, width):
    x = [100 * float(r['recall']) for r in curve]
    y = [float(r['qps']) for r in curve]
    assert all(0 <= a <= 100 for a in x) and all(b > 0 for b in y)
    ax.plot(x, y, color=color, marker=marker, linestyle=linestyle, linewidth=width,
            markersize=3.4, markeredgewidth=0.6, markeredgecolor=color,
            markerfacecolor='white', label=label, zorder=3)
    return x, y


def _label_series(ax, entries, xmin):
    """Place direct labels next to each curve's first visible point, highest curve first.

    entries = [(curve, label, colour), ...]; offsets alternate above/below so the labels
    of adjacent curves do not collide regardless of which system is on top.
    """
    visible = []
    for curve, label, color in entries:
        point = next(((100 * float(r['recall']), float(r['qps'])) for r in curve
                      if 100 * float(r['recall']) >= xmin), None)
        if point is not None:
            visible.append((point, label, color))
    visible.sort(key=lambda item: -item[0][1])
    total = len(visible)
    for index, ((x, y), label, color) in enumerate(visible):
        # Top and bottom series are labelled above their curves (never onto the axis); the
        # middle series takes whichever side has the larger vertical gap.
        if index in (0, total - 1):
            above = True
        else:
            above_gap = visible[index - 1][0][1] - y
            below_gap = y - visible[index + 1][0][1]
            above = above_gap >= below_gap
        right = x > 90
        ax.annotate(label, (x, y), textcoords='offset points',
                    xytext=(-6 if right else 6, 8 if above else -11),
                    ha='right' if right else 'left', fontsize=7,
                    color=color, fontweight='bold', zorder=6)


def _save(fig, out, stem, source):
    fig.savefig(out / f'{stem}.pdf')
    fig.savefig(out / f'{stem}.svg')
    fig.savefig(out / f'{stem}.png', dpi=300)
    fig.savefig(out / f'{stem}.tiff', dpi=600, pil_kwargs={'compression': 'tiff_lzw'})
    plt.close(fig)
    (out / f'{stem}.source.csv').write_bytes(source.read_bytes())


def _system_figure(rows, dataset, stem, out, source):
    ours, diskann = _curve(rows, 'Ours-Disk'), _curve(rows, 'DiskANN-PQ-Disk')
    qps_all = [float(r['qps']) for r in rows]
    fig = plt.figure(figsize=(180 / 25.4, 72 / 25.4))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.55, 0.80, 1.25], wspace=0.42,
                          left=0.062, right=0.985, bottom=0.155, top=0.80)

    ax = fig.add_subplot(gs[0, 0])
    plotted = {}
    for curve, label, color, marker, ls, lw in (
            (ours, 'Ours', SIGNAL, 'o', '-', 1.5),
            (diskann, 'DiskANN', BASELINE, 's', '--', 1.1)):
        plotted[label] = _plot_series(ax, curve, label, color, marker, ls, lw)
    ax.set_ylim(0, max(qps_all) * 1.10)
    ax.set_xlim(XMIN, XMAX)
    ax.set_xticks([80, 85, 90, 95, 100])
    ax.set_xlabel('Recall@10 (%)')
    ax.set_ylabel('QPS (queries/s)')
    ax.grid(axis='y', color=FLOOR, linewidth=0.5, zorder=0)
    gate = 0.95
    ax.axvline(gate * 100, color=MUTED, linewidth=0.6, linestyle=(0, (2, 2)), zorder=1)
    ours_g, disk_g = _first_at(ours, gate), _first_at(diskann, gate)
    ratio = float(ours_g['qps']) / float(disk_g['qps'])
    # Bracket the gap on the 95% gate instead of drawing a floating annotation.
    ax.annotate('', xy=(gate * 100 - 1.4, float(ours_g['qps'])),
                xytext=(gate * 100 - 1.4, float(disk_g['qps'])),
                arrowprops=dict(arrowstyle='<->', color=SIGNAL, linewidth=0.7,
                                shrinkA=0, shrinkB=0))
    if ratio >= 1:
        gap_text, gap_color = f'Ours {ratio:.1f}x\nfaster', SIGNAL
    else:
        gap_text, gap_color = f'DiskANN {1 / ratio:.1f}x\nfaster', BASELINE
    ax.text(gate * 100 - 3.0, (float(ours_g['qps']) + float(disk_g['qps'])) / 2,
            gap_text, fontsize=7, color=gap_color, ha='right', va='center')
    _label_series(ax, [(ours, 'Ours', SIGNAL), (diskann, 'DiskANN', BASELINE)], XMIN)
    ax.text(0.005, 1.02, 'a', transform=ax.transAxes, fontsize=8, fontweight='bold',
            va='bottom', ha='left')

    axb = fig.add_subplot(gs[0, 1])
    labels, ratios = [], []
    for gate_value, name in GATES:
        o, d = _first_at(ours, gate_value), _first_at(diskann, gate_value)
        labels.append(name)
        ratios.append(float(o['qps']) / float(d['qps']) if (o and d) else float('nan'))
    # Direction-adaptive bars: the bar always shows the faster system's advantage, and its
    # colour/label name that system. Values below 1 therefore mean DiskANN leads.
    advantage = [max(value, 1.0 / value) if value == value else float('nan') for value in ratios]
    colours = [SIGNAL if value >= 1 else BASELINE for value in ratios]
    axb.barh(range(len(labels)), advantage, height=0.62, color=colours, zorder=3)
    axb.axvline(1.0, color=MUTED, linewidth=0.7, zorder=4)
    for i, (value, factor) in enumerate(zip(ratios, advantage)):
        label = (f'Ours {value:.2f}x' if value >= 1 else f'DiskANN {factor:.1f}x')
        axb.text(factor + 0.08, i, label, va='center', fontsize=6.8,
                 color=SIGNAL if value >= 1 else BASELINE)
    axb.set_yticks(range(len(labels)))
    axb.set_yticklabels([f'{n} recall' for n in labels])
    axb.invert_yaxis()
    axb.set_ylim(len(labels) - 0.4, -1.25)
    axb.set_xlim(0, max(advantage) * 1.45)
    axb.set_xlabel('Throughput advantage at matched recall (x)')
    axb.grid(axis='x', color=FLOOR, linewidth=0.5, zorder=0)
    ours_max = max(float(r['recall']) for r in ours)
    diskann_max = max(float(r['recall']) for r in diskann)
    ceiling = (f'Ours recall ceiling {ours_max * 100:.1f}% '
               f'(rerank cap 100);\nDiskANN reaches {diskann_max * 100:.1f}%')
    axb.text(max(advantage) * 0.5, -0.95, ceiling,
             fontsize=6.6, color=BASELINE, va='center', ha='center',
             bbox=dict(boxstyle='round,pad=0.3', facecolor='white',
                       edgecolor=MUTED, linewidth=0.5))
    axb.text(1.04, len(labels) - 0.60, 'equal', fontsize=6.2, color=MUTED, va='center')
    axb.text(0.005, 1.02, 'b', transform=axb.transAxes, fontsize=8, fontweight='bold',
             va='bottom', ha='left')

    axc = fig.add_subplot(gs[0, 2])
    for group, label, color, marker, ls, lw in MAIN_GROUPS:
        _plot_series(axc, _curve(rows, group), label, color, marker, ls, lw)
    # Same linear QPS treatment as panel a: one axis convention across the figure.
    axc.set_ylim(0, max(qps_all) * 1.10)
    axc.set_xlim(XMIN, XMAX)
    axc.set_xticks([80, 85, 90, 95, 100])
    axc.set_xlabel('Recall@10 (%)')
    axc.set_ylabel('QPS (queries/s)')
    axc.grid(axis='y', color=FLOOR, linewidth=0.5, zorder=0)
    # Direct labels instead of a legend: in the 80-100% window a legend box would cover the curves.
    _label_series(axc, [(_curve(rows, MAIN_GROUPS[0][0]), 'Ours', SIGNAL),
                        (_curve(rows, MAIN_GROUPS[1][0]), 'DiskANN', BASELINE),
                        (_curve(rows, MAIN_GROUPS[2][0]), 'SymphonyQG', ACCENT)], XMIN)
    axc.text(0.005, 1.02, 'c', transform=axc.transAxes, fontsize=8, fontweight='bold',
             va='bottom', ha='left')

    fig.text(0.062, 0.955,
             f'{dataset.upper()} | 4 GiB process budget | 32 workers | 800 test queries per point',
             fontsize=7.5, va='top')
    _save(fig, out, stem, source)

    # Companion SI panel: the same four systems on a log QPS axis, every point kept.
    fig_si, ax_si = plt.subplots(figsize=(89 / 25.4, 74 / 25.4))
    fig_si.subplots_adjust(left=0.17, right=0.975, bottom=0.14, top=0.86)
    for group, label, color, marker, ls, lw in SYSTEM_GROUPS:
        _plot_series(ax_si, _curve(rows, group), label, color, marker, ls, lw)
    ax_si.set_yscale('log')
    ax_si.set_ylim(min(qps_all) * 0.55, max(qps_all) * 2.0)
    ax_si.yaxis.set_major_formatter(ScalarFormatter())
    ax_si.yaxis.set_minor_formatter(NullFormatter())
    ax_si.set_xlim(46, 100.6)
    ax_si.set_xticks([50, 60, 70, 80, 90, 100])
    ax_si.set_xlabel('Recall@10 (%)')
    ax_si.set_ylabel('QPS (queries/s, log)')
    ax_si.grid(axis='y', color=FLOOR, linewidth=0.5, zorder=0)
    ax_si.legend(loc='lower left', handlelength=2.2, labelspacing=0.3, borderpad=0.1)
    fig_si.text(0.17, 0.965, f'{dataset.upper()} | all four disk systems | log QPS axis',
                fontsize=7.5, va='top')
    fig_si.savefig(out / f'{stem}.log_si.pdf')
    fig_si.savefig(out / f'{stem}.log_si.svg')
    fig_si.savefig(out / f'{stem}.log_si.png', dpi=300)
    fig_si.savefig(out / f'{stem}.log_si.tiff', dpi=600, pil_kwargs={'compression': 'tiff_lzw'})
    plt.close(fig_si)

    return {
        'panels': {'a': 'Recall@10 vs QPS, Ours vs DiskANN (linear QPS, tightened)',
                   'b': 'Ours/DiskANN QPS ratio at fixed recall gates',
                   'c': 'Ours, DiskANN and SymphonyQG (linear QPS)',
                   'si': 'all four disk systems on a log QPS axis (separate file, same points)'},
        'matched_recall_ratios_first_width_reaching_gate': {
            name: value for (_, name), value in zip(GATES, ratios)},
        'size_mm': [180, 72],
        'recall_window_pct': [XMIN, XMAX],
        'methods_in_main_figure': [group for group, *_ in MAIN_GROUPS],
        'omitted_from_main_figure': {
            'Glass-NSG-DiskPort': 'recall stays at 49.0-79.6%, outside the 80-100% window; '
                                  'kept in the SI panel and in the source data'},
    }


def _memory_figure(rows, dataset, stem, out, source):
    fig, ax = plt.subplots(figsize=(120 / 25.4, 78 / 25.4))
    fig.subplots_adjust(left=0.145, right=0.975, bottom=0.155, top=0.80)
    budgets = [(1.0, '1 GiB', '#9ECAE1', 'o'), (2.0, '2 GiB', '#6BAED6', 's'),
               (4.0, '4 GiB', '#2171B5', '^'), (8.0, '8 GiB', '#08306B', 'D')]
    for budget, label, color, marker in budgets:
        curve = sorted([r for r in rows if float(r['search_dram_budget_gib']) == budget],
                       key=lambda r: int(r['search_width']))
        assert [int(r['search_width']) for r in curve] == WIDTHS, budget
        _plot_series(ax, curve, label, color, marker, '-', 1.2)
    ax.set_ylim(0, max(float(r['qps']) for r in rows) * 1.12)
    ax.set_xlim(min(62, math.floor(min(float(r['recall']) * 100 for r in rows) / 10) * 10 - 4), 100.6)
    ax.set_xlabel('Recall@10 (%)')
    ax.set_ylabel('QPS (queries/s)')
    ax.grid(axis='y', color=FLOOR, linewidth=0.5, zorder=0)
    ax.legend(loc='lower left', handlelength=2.2, labelspacing=0.3,
              title='Ours budget', title_fontsize=6.8)
    fig.text(0.145, 0.955,
             f'{dataset.upper()} | Ours | 4 RAM budgets | 32 workers | 800 test queries per point',
             fontsize=7.5, va='top')
    _save(fig, out, stem, source)
    return {'panels': {'a': 'Recall@10 vs QPS for the 1/2/4/8 GiB budgets (linear QPS)'},
            'size_mm': [120, 78]}


def draw(experiment, dataset="gist"):
    root = ROOT / 'results' / experiment / dataset
    source = root / 'tables/recall_qps_summary.csv'
    rows = list(csv.DictReader(source.open()))
    assert len(rows) == 36, len(rows)
    is_system = experiment.startswith('03')
    out = root / 'figures'
    out.mkdir(exist_ok=True)
    stem = 'system_qps_recall' if is_system else 'memory_qps_recall'

    if is_system:
        detail = _system_figure(rows, dataset, stem, out, source)
        caption = (
            f'{dataset.upper()} disk systems under a shared 4 GiB process RAM budget, plotted over the '
            '80-100% Recall@10 window that both decisive systems reach. '
            '(a) Recall@10 versus throughput for Ours-Disk and the official DiskANN-PQ disk port on a '
            'linear QPS axis tightened to the measured range; the dashed line marks 95% recall. '
            '(b) Throughput advantage at matched recall gates, always drawn for the faster system (blue = '
            'Ours, red = DiskANN), using the first measured width that reaches each gate without '
            'interpolation; the box states each system\'s recall ceiling, which for Ours is set by its '
            '100-candidate residual rerank cap. '
            '(c) Ours, DiskANN and the SymphonyQG disk port on the same linear QPS axis and the same '
            '80-100% window. Glass-NSG is omitted from panels a-c because its recall stays below 80% '
            '(49.0-79.6%); its nine admitted points are kept in the source data and in the companion SI '
            'file (system_qps_recall.log_si), which shows all four systems over the full recall range on a '
            'logarithmic QPS axis. '
            'Ours and DiskANN use beam 4; the Glass-NSG and SymphonyQG disk ports keep their native beam 1. '
            'Markers are the nine widths 10, 20, 40, 60, 100, 160, 240, 400 and 580 in that order, joined by '
            'lines that are not fitted curves; non-monotonic throughput and coincident recalls are retained. '
            'Each point is one measured configuration (800 test queries, 32 workers, warmup excluded); '
            'uncertainty was not estimated because each configuration was measured once. '
            'Memory admission uses whole-process peak RSS and zero sampled swap, not a cgroup hard limit. '
            'Source Data are supplied alongside the figure.'
        )
    else:
        detail = _memory_figure(rows, dataset, stem, out, source)
        modes = sorted({(r['ours_route_mode'], r['ours_route_dimension']) for r in rows})
        caption = (
            f'{dataset.upper()} Ours-Disk memory-budget comparison at 1, 2, 4 and 8 GiB with beam 4; '
            f'recorded routing modes/dimensions {modes}. Linear QPS axis. '
            'Markers are the nine widths 10, 20, 40, 60, 100, 160, 240, 400 and 580 in that order, joined by '
            'lines that are not fitted curves; all 36 admitted points are retained. '
            'Each point is one measured configuration (800 test queries, 32 workers, warmup excluded); '
            'uncertainty was not estimated because each configuration was measured once. '
            'Source Data are supplied alongside the figure.'
        )

    (out / f'{stem}.caption.md').write_text(caption + '\n')
    (out / f'{stem}.provenance.json').write_text(json.dumps({
        'source': str(source.relative_to(ROOT)),
        'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'input_rows': 36,
        'plotted_rows_main_figure': 27 if is_system else 36,
        'plotted_rows_companion_si': 36,
        'rows_not_drawn_in_main_figure': (
            {'count': 9, 'method': 'Glass-NSG-DiskPort',
             'reason': 'recall 49.0-79.6% lies outside the 80-100% main-figure window; kept in SI and source data'}
            if is_system else {}),
        'excluded_from_dataset': 0,
        'backend': 'python/matplotlib (nature-figure skill)',
        'size_mm': detail['size_mm'],
        'panels': detail['panels'],
        'interpolation': 'none',
        'smoothing': 'none',
        'pareto_filtering': 'none',
        'script': 'scripts/plot_gist_formal_recall_qps.py',
        **({'matched_recall_ratios_first_width_reaching_gate':
            detail['matched_recall_ratios_first_width_reaching_gate']}
           if 'matched_recall_ratios_first_width_reaching_gate' in detail else {}),
    }, indent=2) + '\n')
    print(out / f'{stem}.pdf')


if __name__ == '__main__':
    draw('03_disk_system')
    draw('05_memory_budget')
