"""Descriptive plots: historical and recent cohorts remain visibly separate."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[3]
BASE = ROOT / 'results/04_ours_memory_budget/gist_routing_diagnostics'
OUT=BASE/'figures';OUT.mkdir(exist_ok=True)
rows=json.loads((BASE/'tables/routing_paged_comparison.json').read_text())
plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['DejaVu Sans'],
 'font.size':7,'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42,'svg.fonttype':'none',
 'legend.frameon':False,'axes.labelsize':8,'axes.titlesize':9})
def label(s):return s.replace(' (同期)',' (recent)').replace('Ours-Paged-','Paged ')
def export(fig,name):
 fig.savefig(OUT/f'{name}.svg')
 fig.savefig(OUT/f'{name}.pdf')
 fig.savefig(OUT/f'{name}.png',dpi=600)
 plt.close(fig)
colors=['#323232','#4878A8','#D18A39','#8064A2','#438A80','#B85A69','#777777']
# Contract: quantitative grid, descriptive speed/recall, no cross-cohort speedup.
# Every source row is shown; no interpolation, significance test or invented CI.
fig,axs=plt.subplots(1,2,figsize=(183/25.4,120/25.4))
for ax,historical,title in zip(axs,[True,False],['a  Historical methods (800 queries)','b  Recent Ours (100 queries / round)']):
 data=[r for r in rows if r['cohort'].startswith('historical')==historical]
 methods=list(dict.fromkeys(r['method'] for r in data))
 for i,method in enumerate(methods):
  rr=sorted([r for r in data if r['method']==method],key=lambda r:r['L'])
  assert all(r['qps']>0 for r in rr)
  text=label(method)+(' [4 GiB diagnostic]' if method=='Starling-Disk' else '')
  # Scatter avoids implying interpolation or a monotonic frontier.
  ax.scatter([r['recall'] for r in rr],[r['qps'] for r in rr],s=13,marker=['o','s','^','D','v','P','X'][i],color=colors[i],label=text)
 ax.set_yscale('log');ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter('{x:g}'));ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter());ax.set_xlabel('Recall@10');ax.set_ylabel('QPS (log scale)');ax.set_title(title,loc='left')
 ax.grid(alpha=.15);ax.legend(loc='upper center',bbox_to_anchor=(.5,-.20),fontsize=6)
fig.subplots_adjust(left=.09,right=.98,top=.89,bottom=.35,wspace=.32)
fig.text(.5,.97,'GIST | beam1, workers32 | descriptive cross-protocol reference',ha='center',fontsize=8)
fig.text(.5,.025,'Historical and recent query sets, budgets and storage protocols differ. No fair ranking implied.\nRecent L100: two pooled rounds; L300/580: one correctness run. No confidence intervals estimated.',ha='center',fontsize=6)
export(fig,'routing_paged_recall_qps')
fig,axs=plt.subplots(1,2,figsize=(183/25.4,125/25.4))
for ax,historical,title in zip(axs,[True,False],['a  Historical methods','b  Recent Ours variants']):
 data=[r for r in rows if r['L']==100 and r['cohort'].startswith('historical')==historical]
 for i,r in enumerate(data):
  ax.barh(i,r['qps'],color=colors[i],height=.65)
  ax.text(r['qps']+2,i,f"{r['qps']:.2f}",va='center',fontsize=6)
 ax.set_yticks(range(len(data)),[label(r['method'])+('\n4 GiB diagnostic' if r['method']=='Starling-Disk' else '') for r in data],fontsize=6)
 ax.invert_yaxis();ax.set_xlim(0,245);ax.set_xlabel('QPS');ax.set_title(title,loc='left');ax.xaxis.grid(alpha=.15)
fig.subplots_adjust(left=.20,right=.98,top=.86,bottom=.20,wspace=1.20)
fig.text(.5,.97,'GIST L100 | beam1, workers32',ha='center',fontsize=10)
fig.text(.5,.075,'Separate cohorts: historical n=800; recent n=100 per round, two rounds pooled.\nBudgets and recall differ; compare Recall@10 in the source table. No cross-cohort speedup claim.',ha='center',fontsize=6)
export(fig,'routing_paged_L100_qps')
(OUT/'routing_paged_figure_notes.md').write_text('''# Figure contract and QA

Python/matplotlib; quantitative grid; descriptive speed/recall comparison. PNG previews and editable SVG/PDF, 183 mm wide. All 284 source rows appear in the recall/QPS figure. The L100 detail selects 13 L100 rows explicitly; other widths remain in the complete figure. Different cohorts have separate panels. QPS aggregates use total queries / total duration. No statistical intervals are estimated from two timing rounds; points are descriptive measurements. L300/580 recent timings are correctness regressions, not optimization evidence. Source data: ../tables/routing_paged_comparison.json and .csv. Historical Starling uses 4 GiB and is diagnostic. No simulated data, smoothing, interpolation or cold-device claim.

Panels: historical curves describe existing methods; recent curves describe paging variants; L100 panels provide exact throughput labels. Check all panels for label clearance, cohort separation, editable text and the 5 pt font floor before delivery.
''')
print(OUT)

# Requested combined overview: a = all recall/QPS curves, b = L100 detail.
fig=plt.figure(figsize=(183/25.4,205/25.4))
a=fig.add_axes([.12,.60,.57,.31]);b=fig.add_axes([.31,.13,.62,.34])
methods=list(dict.fromkeys(r['method'] for r in rows))
palette=['#252525','#718DA6','#9C824F','#9988AE','#719990','#B4707C','#167DB7','#075681','#67ABC3','#3993A8','#D27023','#AAAEB1','#727B82']
markers=['o','s','^','D','v','P','o','s','^','D','X','v','P']
for i,method in enumerate(methods):
 rr=sorted([r for r in rows if r['method']==method],key=lambda r:r['L'])
 historical=rr[0]['cohort'].startswith('historical')
 a.plot([r['recall'] for r in rr],[r['qps'] for r in rr],color=palette[i],
        linestyle='--' if historical else '-',marker=markers[i],markersize=2.4,linewidth=.9,
        label=label(method)+(' [4 GiB]' if method=='Starling-Disk' else ''))
a.set_yscale('log');a.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter('{x:g}'))
a.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
a.set_xlabel('Recall@10');a.set_ylabel('QPS (log scale)');a.grid(alpha=.15)
a.set_title('a  Recall–QPS curves',loc='left',fontweight='bold')
a.legend(loc='upper left',bbox_to_anchor=(1.015,1),fontsize=5.5,handlelength=1.6,labelspacing=.8)
data=[r for r in rows if r['L']==100]
for i,r in enumerate(data):
 idx=methods.index(r['method']);historical=r['cohort'].startswith('historical')
 b.barh(i,r['qps'],height=.70,color=palette[idx],alpha=.65 if historical else 1)
 b.text(r['qps']+2,i,f"{r['qps']:.2f}",va='center',fontsize=6)
b.axhline(5.5,color='#777777',linestyle=':',linewidth=.8)
b.set_yticks(range(len(data)),[label(r['method'])+(' [4 GiB]' if r['method']=='Starling-Disk' else '') for r in data],fontsize=6)
b.invert_yaxis();b.set_xlim(0,245);b.set_xlabel('QPS');b.xaxis.grid(alpha=.15)
b.set_title('b  L100 throughput (historical above dotted line)',loc='left',fontweight='bold',fontsize=8)
fig.text(.5,.97,'GIST | beam1, workers32',ha='center',fontsize=10)
fig.text(.12,.525,'Dashed: historical 800-query runs. Solid: recent 100-query runs.\nLines connect measured L values; no interpolation or cross-protocol speedup inference.',fontsize=6)
fig.text(.5,.045,'Recent L100: two rounds pooled; L300/580: one correctness run. Different budgets / protocols.\nCLOCK 75% leads the paged variants; recent resident Ours remains faster.\nStarling is a 4 GiB diagnostic and has different recall; it is not a matched comparison.',ha='center',fontsize=6)
export(fig,'routing_paged_comparison')
