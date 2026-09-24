"""Quantitative grid: assess cross-dataset recall/throughput tradeoffs, not assert a win.

Contract: Python; 183x88 mm; editable PDF/SVG + PNG preview; all 12 test
observations, no smoothing, no interpolation. Each point is one 900-query run
(not 900 independent throughput replicates). No uncertainty bars: one repeat.
Two panels answer generalization on distinct datasets at a matched 1 GiB budget.
"""
import argparse
import csv
import json
import os
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR','/tmp/adaptive_curve_mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('directory',type=Path)
a=p.parse_args()
with (a.directory/'source_data.csv').open() as f:rows=list(csv.DictReader(f))
assert len(rows)==12, 'require all two-method, three-width, two-dataset points'
plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['DejaVu Sans'],'font.size':7,'axes.titlesize':8,
 'axes.labelsize':7,'xtick.labelsize':7,'ytick.labelsize':7,'legend.fontsize':6,
 'pdf.fonttype':42,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False})
fig,axes=plt.subplots(1,2,figsize=(7.2047244,3.4645669),layout='constrained')
for i,(ds,ax) in enumerate(zip(('gist','dbpedia'),axes)):
    frozen=json.loads((a.directory/f'{ds}_frozen.json').read_text())
    variants=['baseline',frozen['curve_lowdim']]
    for v,color,marker in zip(variants,('#555555','#0072B2'),('o','s')):
        data=sorted([r for r in rows if r['dataset']==ds and r['variant']==v],key=lambda r:int(r['width']))
        assert [int(r['width']) for r in data]==[16,49,96]
        assert all(int(r['query_count'])==900 and float(r['budget_gib'])==1 for r in data)
        x=[float(r['recall']) for r in data];y=[float(r['qps']) for r in data]
        assert all(0<=val<=1 for val in x) and all(val>0 for val in y)
        label='Ours (Full DB1)' if v=='baseline' else f"Lowdim {v.split('_')[0]} ({'Selected' if frozen['lowdim_is_selected'] else 'Diagnostic'})"
        ax.plot(x,y,color=color,marker=marker,markersize=3.5,linewidth=1,
                linestyle='-' if v=='baseline' or frozen['lowdim_is_selected'] else '--',label=label)
        for j,r in enumerate(data):
            ax.annotate(str(r['width']),(x[j],y[j]),xytext=(4,5) if v=='baseline' else (-6,-10),
                        ha='left' if v=='baseline' else 'right',textcoords='offset points',fontsize=6)
    ax.set_title(('a  GIST' if i==0 else 'b  DBpedia'),loc='left',fontweight='bold')
    ax.set_xlabel('Recall@10');ax.set_ylabel('Queries per second')
    ax.set_ylim(bottom=0);ax.margins(x=.15,y=.2)
    ax.legend(loc='best',frameon=False)
fig.suptitle('Disk graph search • 1 GiB accounting budget • 32 workers',fontsize=8)
fig.savefig(a.directory/'recall_qps.pdf')
fig.savefig(a.directory/'recall_qps.svg')
fig.savefig(a.directory/'recall_qps.png',dpi=600)
plt.close(fig)
(a.directory/'figure_caption.md').write_text(
 '# Recall–QPS cross-dataset pilot\n\n'
 'Markers are individual 900-query measurements; numbers denote search width '
 '(16,49,96). Lines connect measurements in width order; no interpolation or '
 'smoothing. Each configuration was measured once, so no repeat-based error '
 'bars or significance claims are available. Selection used queries 0–99 at '
 'width49, followed by frozen testing on queries 100–999. DBpedia has another '
 '9000 queries not evaluated here; identical pilot query counts were used for '
 'both datasets. No measured curve points were excluded. Dashed Diagnostic '
 'curves are not automatic-selection successes. Budget is accounting-based, '
 'not a cgroup-enforced limit. The two panels use their own dataset-specific '
 'Ours graphs, reused unchanged across methods. Source data: source_data.csv.\n')
print(a.directory/'recall_qps.pdf')
