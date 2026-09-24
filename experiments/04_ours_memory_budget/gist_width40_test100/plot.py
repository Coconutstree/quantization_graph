"""Single-panel Recall–QPS comparison, Python backend, 183 x 140 mm.
Preserve all 40 points per curve in width order, including equal-recall points.
Single observations: no smoothing, envelope selection, or uncertainty claim.
"""
from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib as mpl
from matplotlib.ticker import FixedLocator, FuncFormatter, NullFormatter

OUT = Path(__file__).resolve().parents[3]/'results/04_ours_memory_budget/gist_width40_test100'
STYLES = {
    'ours_538': ('Ours · 538 MiB · paged', '#9174AC', 'v', '--'),
    'ours_616': ('Ours · 616 MiB · paged', '#65508B', '^', '--'),
    'ours_resident': ('Ours · resident, no record cache', '#7B818A', 'D', '-.'),
    'ours_768': ('Ours · 768 MiB · hot', '#59A4BD', 's', '-'),
    'ours_1024': ('Ours · 1024 MiB · hot', '#2677A5', 'o', '-'),
    'ours_1536': ('Ours · 1536 MiB · hot + dynamic', '#C45936', 'o', '-'),
    'diskann_c0': ('DiskANN · c0', '#252A31', 'x', '--'),
}


def main(rows, stem='memory_curves'):
    mpl.rcParams.update({'font.family': 'sans-serif', 'font.sans-serif': ['Arial', 'DejaVu Sans'],
        'font.size': 10, 'axes.labelsize': 11, 'xtick.labelsize': 9, 'ytick.labelsize': 9,
        'pdf.fonttype': 42, 'svg.fonttype': 'none', 'axes.spines.top': False,
        'axes.spines.right': False, 'axes.linewidth': .8, 'text.color': '#252A31',
        'axes.labelcolor': '#252A31', 'xtick.color': '#525861', 'ytick.color': '#525861'})
    assert rows and all(r['qps'] > 0 for r in rows)
    directory = OUT/'figures'; directory.mkdir(exist_ok=True)
    configs = {r['run'].rsplit('_r', 1)[0] for r in rows}
    order = [c for c in STYLES if c in configs] + sorted(configs-set(STYLES))
    fig, ax = plt.subplots(figsize=(7.204724, 5.511811))
    fig.subplots_adjust(left=.115, right=.975, top=.88, bottom=.30)
    lines, labels = [], []
    for index, config in enumerate(order):
        group = sorted([r for r in rows if r['run'].rsplit('_r', 1)[0] == config], key=lambda r:r['width'])
        assert len(group) == 40 and len({r['width'] for r in group}) == 40
        label, color, marker, style = STYLES.get(config,
            (config.replace('ours_', 'Ours · ')+' MiB', mpl.colormaps['viridis'](.15+.7*index/max(1,len(order)-1)), 'o', '-'))
        line, = ax.plot([r['recall'] for r in group], [r['qps'] for r in group],
            color=color, linestyle=style, linewidth=1.65, marker=marker, markersize=3.2,
            markevery=4, markerfacecolor='white', markeredgewidth=.9,
            zorder=4 if config=='ours_1536' else 3)
        lines.append(line); labels.append(label)
    ax.set_xlabel('Recall@10', labelpad=8)
    ax.set_ylabel('QPS (log scale)', labelpad=9)
    ax.set_xlim(.585,1.002)
    ax.set_yscale('log')
    ax.set_ylim(min(r['qps'] for r in rows)/1.25, max(r['qps'] for r in rows)*1.3)
    ax.xaxis.set_major_locator(FixedLocator([.60,.65,.70,.75,.80,.85,.90,.95,1.0]))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value,pos:f'{value:.2f}'))
    ax.yaxis.set_major_locator(FixedLocator([3,10,30,100,300,1000,3000]))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value,pos:f'{value:,.0f}'))
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.tick_params(which='major', length=3.5, width=.7)
    ax.tick_params(axis='y', which='minor', length=0)
    ax.grid(axis='y', which='major', color='#E4E7EB', linewidth=.65)
    ax.set_axisbelow(True)
    fig.text(.115,.95,'GIST1M',fontsize=13,fontweight='bold',va='top')
    fig.text(.975,.943,'100 queries · 40 widths · 32 workers',fontsize=9,
             color='#626B76',ha='right',va='top')
    # Column-major ordering places Ours states first and the neutral baseline last.
    legend=fig.legend(lines,labels,loc='lower left',bbox_to_anchor=(.11,.025),ncol=2,
        fontsize=8.5,frameon=False,handlelength=2.8,columnspacing=2.2,
        handletextpad=.7,labelspacing=.85,borderaxespad=0)
    fig.canvas.draw()
    legend_box=legend.get_window_extent(fig.canvas.get_renderer())
    assert legend_box.x1 <= fig.bbox.x1 and legend_box.y0 >= 0
    fig.savefig(directory/f'{stem}.png',dpi=600)
    fig.savefig(directory/f'{stem}.pdf')
    fig.savefig(directory/f'{stem}.svg')
    plt.close(fig)
    (directory/f'{stem}_contract.json').write_text(json.dumps(dict(
        backend='python',archetype='single quantitative panel',
        question='Observed Recall–QPS tradeoffs across the retained configurations',
        input_rows=len(rows),plotted_rows=len(rows),excluded_rows=0,curves=len(order),
        x='Recall@10',y='QPS; log scale',order='search width; equal-recall points retained',
        markers='every fourth point for readability; line contains all 40 points',
        repeats=1,uncertainty='not estimated',smoothing=False,size_mm=[183,140],
        budget_labels='configured MiB, not measured RSS'),indent=2)+'\n')
