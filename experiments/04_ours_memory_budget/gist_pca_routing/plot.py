"""Plot fixed-M validation curves and separate locked-test observations."""
import csv
import json
import math
import os
from pathlib import Path
import statistics

os.environ.setdefault('MPLCONFIGDIR', '/tmp/gist_pca_routing_mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FormatStrFormatter, NullLocator
from prepare import OUT, ROOT, dump, sha

FIG = OUT / 'figures'
STYLE = {
    128: ('128D · resident', '#2677A5', 'o', '-'),
    256: ('256D · paged', '#C45936', 's', '--'),
    512: ('512D · paged', '#8064A2', '^', '-.'),
    960: ('960D · original, paged', '#343B43', 'D', ':'),
}
WIDTH_MARKERS = {60: 'o', 100: 's', 180: '^'}


def inputs():
    rows = list(csv.DictReader((OUT / 'measured_results.csv').open()))
    used = []
    sources = {}
    for row in rows:
        if row['split'] != 'test' and not (row['split'] == 'tune' and '_grid_' in row['run']):
            continue
        for field in ('dim', 'keep', 'width'):
            row[field] = int(row[field])
        for field in ('recall', 'qps'):
            row[field] = float(row[field])
        path = OUT / row['source']
        acceptance = json.loads((path.parent / 'acceptance.json').read_text())
        assert acceptance['passed'] and sha(path) == acceptance['files']['result.json']
        result = json.loads(path.read_text())
        raw = next(x for x in result['summary_rows'] if x['search_width'] == row['width'])
        assert raw['query_count'] == (800 if row['split'] == 'test' else 100)
        assert math.isclose(raw['recall'], row['recall'], abs_tol=1e-12)
        assert math.isclose(raw['qps'], row['qps'], abs_tol=1e-12)
        assert json.loads((path.parent / 'config.json').read_text())['budget_mib'] == 538
        sources[row['source']] = sha(path)
        used.append(row)
    assert len([x for x in used if x['split'] == 'tune']) == 39
    assert len([x for x in used if x['split'] == 'test']) == 16
    return used, sources


def axes_style(ax, *, logarithmic=False):
    ax.set_xlabel('Recall@10')
    ax.set_axisbelow(True)
    ax.grid(axis='y', color='#E4E7EB', linewidth=.65)
    ax.tick_params(length=3, width=.7)
    ax.xaxis.set_major_formatter(FormatStrFormatter('%.2f'))
    if logarithmic:
        ax.set_yscale('log')
        ax.set_ylim(8, 160)
        ax.yaxis.set_major_locator(FixedLocator([10, 20, 40, 80, 160]))
        ax.yaxis.set_major_formatter(FormatStrFormatter('%d'))
        ax.yaxis.set_minor_locator(NullLocator())
        ax.set_xlim(.90, .986)
        ax.xaxis.set_major_locator(FixedLocator([.90, .92, .94, .96, .98]))
    else:
        ax.set_ylim(0, 85)
        ax.set_xlim(.94, .983)
        ax.xaxis.set_major_locator(FixedLocator([.94, .95, .96, .97, .98]))
        ax.axvline(.95, color='#AAB1BA', linewidth=.8, linestyle='--', zorder=0)


def legend(fig, y, *, curves=True, width_encoded=False):
    handles = [Line2D([], [], color=c, marker=None if width_encoded else m, linestyle=l if curves else 'none', markersize=4.5,
                      markerfacecolor='white', label=label)
               for label, c, m, l in STYLE.values()]
    return fig.legend(handles=handles, loc='lower center', bbox_to_anchor=(.52, y),
                      ncol=2, frameon=False, fontsize=8.5, handlelength=2.5,
                      columnspacing=2.2, labelspacing=.65)


def save(fig, stem, figure_legend):
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    box = figure_legend.get_window_extent(renderer)
    assert box.x0 >= 0 and box.y0 >= 0 and box.x1 <= fig.bbox.x1 and box.y1 <= fig.bbox.y1
    for suffix in ('png', 'pdf', 'svg'):
        fig.savefig(FIG / f'{stem}.{suffix}', dpi=400, facecolor='white')
    plt.close(fig)
    assert '<text' in (FIG / f'{stem}.svg').read_text(), 'SVG labels must remain editable'


def validation(rows):
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 6.75), sharex=True, sharey=True)
    fig.subplots_adjust(left=.105, right=.978, bottom=.26, top=.865, wspace=.18, hspace=.32)
    for ax, keep, panel in zip(axes.flat, (0, 16, 32, 48), 'abcd'):
        for dim, (_, color, marker, line) in STYLE.items():
            group = sorted((x for x in rows if x['split'] == 'tune' and x['dim'] == dim
                            and x['keep'] == (0 if dim == 960 else keep)), key=lambda x: x['width'])
            assert [x['width'] for x in group] == [60, 100, 180]
            ax.plot([x['recall'] for x in group], [x['qps'] for x in group],
                    color=color, linestyle=line, linewidth=1.6)
            for point in group:
                ax.plot(point['recall'], point['qps'], color=color, linestyle='none',
                        marker=WIDTH_MARKERS[point['width']], markersize=4.3,
                        markerfacecolor='white', markeredgewidth=1.15)
                if dim == 128:
                    ax.annotate(f"w={point['width']}", (point['recall'], point['qps']),
                                xytext=(0, 8), textcoords='offset points', ha='center',
                                fontsize=7, color=color)
        axes_style(ax, logarithmic=True)
        title = 'M = 0 (verify all)' if keep == 0 else f'M = {keep}'
        ax.set_title(f'{panel}   {title}', loc='left', fontsize=10, pad=8)
    for ax in axes[0]:
        ax.set_xlabel('')
    for ax in axes[:, 0]:
        ax.set_ylabel('QPS (log scale)')
    fig.text(.105, .969, 'GIST1M · PCA routing', fontsize=13, weight='bold', va='top')
    fig.text(.105, .917, 'Validation: 100 queries · 538 MiB · 32 workers', fontsize=9, color='#626B76')
    lg = legend(fig, .125, width_encoded=True)
    fig.legend(handles=[Line2D([], [], color='#343B43', marker=m, linestyle='none',
                              markerfacecolor='white', label=f'width={w}')
                        for w,m in WIDTH_MARKERS.items()],
               loc='lower center', bbox_to_anchor=(.52, .070), ncol=3, frameon=False, fontsize=8.5)
    fig.text(.105, .032, 'Shape encodes width on every curve; blue labels repeat the width values.',
             fontsize=8, color='#626B76')
    fig.text(.105, .009, 'M applies to low-dimensional routes; the original 960D baseline is repeated in every panel.',
             fontsize=7.5, color='#626B76')
    save(fig, 'recall_qps_validation', lg)


def by_width(rows):
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 6.1), sharex=True, sharey='col')
    fig.subplots_adjust(left=.105, right=.978, bottom=.20, top=.865, wspace=.27, hspace=.29)
    for row_axes, keep in zip(axes, (16, 32)):
        for dim, (_, color, marker, line) in STYLE.items():
            group=sorted((x for x in rows if x['split']=='tune' and x['dim']==dim
                          and x['keep']==(0 if dim==960 else keep)), key=lambda x:x['width'])
            assert [x['width'] for x in group]==[60,100,180]
            for ax,metric in zip(row_axes,('recall','qps')):
                ax.plot([x['width'] for x in group], [x[metric] for x in group],
                        color=color,marker=marker,linestyle=line,linewidth=1.6,
                        markersize=4.3,markerfacecolor='white',markeredgewidth=1.15)
        for ax in row_axes:
            ax.set_xlim(50,190)
            ax.set_xticks([60,100,180])
            ax.set_xlabel('Search width')
            ax.set_axisbelow(True)
            ax.grid(axis='y',color='#E4E7EB',linewidth=.65)
        row_axes[0].set_ylim(.89,.99)
        row_axes[0].set_yticks([.90,.92,.94,.96,.98])
        row_axes[0].set_ylabel('Recall@10')
        row_axes[1].set_ylim(0,135)
        row_axes[1].set_ylabel('Throughput (QPS)')
    for ax in axes[0]:ax.set_xlabel('')
    for ax,title in zip(axes.flat,('a   M=16 · Recall','b   M=16 · QPS','c   M=32 · Recall','d   M=32 · QPS')):
        ax.set_title(title,loc='left',fontsize=10,pad=8)
    fig.text(.105,.969,'GIST1M · Explicit width sweep',fontsize=13,weight='bold',va='top')
    fig.text(.105,.917,'Validation: 100 queries · 538 MiB · measured widths: 60, 100, 180',fontsize=8.5,color='#626B76')
    lg=legend(fig,.064)
    fig.text(.105,.032,'Width changes search breadth; M limits verified neighbors per expansion.',fontsize=8,color='#626B76')
    fig.text(.105,.009,'One run per point; all points shown. The 960D baseline uses the original method.',fontsize=7.8,color='#626B76')
    save(fig,'recall_qps_by_width',lg)


def test_points(rows):
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 4.05), sharex=True, sharey=True)
    fig.subplots_adjust(left=.105, right=.978, bottom=.32, top=.78, wspace=.18)
    plotted = []
    for ax, tag, title in zip(axes, ('_locked_', '_matched_'),
                             ('a   Validation target ≥ 0.95', 'b   Match baseline validation Recall')):
        for dim, (_, color, marker, _) in STYLE.items():
            group = [x for x in rows if x['split'] == 'test' and x['dim'] == dim and tag in x['run']]
            assert len(group) == 2 and len({(x['keep'], x['width'], x['recall']) for x in group}) == 1
            qps = statistics.harmonic_mean(x['qps'] for x in group)
            recall = group[0]['recall']
            lo, hi = min(x['qps'] for x in group), max(x['qps'] for x in group)
            ax.errorbar(recall, qps, yerr=[[qps-lo], [hi-qps]], fmt=marker,
                        color=color, markersize=5.5, markerfacecolor='white',
                        markeredgewidth=1.3, capsize=3, elinewidth=1.1)
            plotted.append(dict(cohort='common095' if tag == '_locked_' else 'matched_baseline',
                                dim=dim, keep=group[0]['keep'], width=group[0]['width'],
                                recall=recall, qps=qps, qps_min=lo, qps_max=hi,
                                sources=[x['source'] for x in group]))
        axes_style(ax)
        ax.set_title(title, loc='left', fontsize=9, pad=9)
    axes[0].set_ylabel('Throughput (QPS)')
    best128 = next(x for x in plotted if x['dim'] == 128 and x['cohort'] == 'matched_baseline')
    axes[1].annotate(f"128D: M={best128['keep']}, width={best128['width']}", (best128['recall'], best128['qps']),
                     xytext=(.958, 63), fontsize=8, color=STYLE[128][1],
                     arrowprops=dict(arrowstyle='-', color=STYLE[128][1], lw=.8))
    fig.text(.105, .962, 'GIST1M · Locked test configurations', fontsize=13, weight='bold', va='top')
    fig.text(.105, .890, '800 queries × 2 runs · 538 MiB · no lines between different configurations',
             fontsize=8.5, color='#626B76')
    lg = legend(fig, .125, curves=False)
    fig.text(.105, .087, 'QPS: harmonic mean; error bars: two-run min–max (not confidence intervals).',
             fontsize=7.8, color='#626B76')
    fig.text(.105, .05, 'a: low-dimensional M=16, width=100.  b: 256D/512D M=32, width=100.',
             fontsize=7.8, color='#626B76')
    fig.text(.105, .013, '960D uses the original method, width=100. Dashed guide: test Recall=0.95.',
             fontsize=7.8, color='#626B76')
    save(fig, 'recall_qps_test', lg)
    dump(FIG / 'test_points.json', plotted)


def main():
    FIG.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9,
                         'axes.labelsize': 9, 'xtick.labelsize': 8, 'ytick.labelsize': 8,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.linewidth': .8, 'pdf.fonttype': 42, 'svg.fonttype': 'none',
                         'text.color': '#252A31', 'axes.labelcolor': '#252A31'})
    rows, sources = inputs()
    dump(FIG / 'figure_spec.json', dict(
        backend='project-standard Python/Matplotlib', templates=['line_trend', 'scatter'],
        figure_ids=['FIG-PCA-ROUTING-VALIDATION', 'FIG-PCA-ROUTING-TEST', 'FIG-PCA-ROUTING-WIDTH'],
        purpose='Show observed Recall–QPS tradeoffs for fixed-M low-dimensional routing and held-out confirmation.',
        source_data='../measured_results.csv', source_data_sha256=sha(OUT/'measured_results.csv'),
        experiment='gist_pca_routing', script=str(Path(__file__).relative_to(ROOT)),
        validation=dict(queries=100, repeats=1, unique_points=39, displayed_points=48,
                        baseline_repeated_in_each_panel=True, widths=[60,100,180],
                        shortlists=[0,16,32,48], xscale='linear', yscale='log', uncertainty='not estimated',
                        marker_encoding='circle=width60, square=width100, triangle=width180'),
        by_width=dict(x='search width', widths=[60,100,180], shortlists=[16,32],
                      y=['Recall@10','QPS'], all_axes='linear', split='validation only'),
        test=dict(queries=800, repeats=2, points=8, aggregation='harmonic QPS mean per locked cohort/config',
                  error_bars='observed min/max, not CI', xscale='linear', yscale='linear', connect_points=False),
        smoothing=False, interpolation=False, available_data='all accepted validation grid and locked test records',
        acceptance_checked=True, source_json_hashes=sources,
        captions={
            'validation':'Fixed-M Recall–QPS curves on validation100. Marker shape identifies width 60/100/180; 960D retains the original gate. One run per point.',
            'by_width':'Recall and QPS against measured search width on validation100 at fixed M=16/32. M is the per-expansion shortlist and is distinct from width.',
            'test':'Independent test800 confirmations of two validation-locked selection rules. Markers are configurations; error bars denote two-run QPS min–max, not confidence intervals.'},
        scope='Experiment report; not a final manuscript or advisor submission.'))
    validation(rows)
    by_width(rows)
    test_points(rows)
    dump(FIG/'manifest.json', dict(script_sha256=sha(__file__), source_data_sha256=sha(OUT/'measured_results.csv'),
                                 files={p.name:sha(p) for p in sorted(FIG.iterdir()) if p.suffix in ('.png','.svg','.pdf')}))
    print('Rendered validation curves and test confirmation points in', FIG)


if __name__ == '__main__':
    main()
