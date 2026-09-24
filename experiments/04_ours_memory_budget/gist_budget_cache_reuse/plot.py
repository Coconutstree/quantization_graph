"""Plot all measured configurations; observations, not isolated performance claims."""
import hashlib,json,os
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR','/tmp/gist-cache-reuse-mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OUT=ROOT/'results/04_ours_memory_budget/gist_budget_cache_reuse'
FIG=OUT/'figures'
plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Arial','DejaVu Sans'],'font.size':8,'axes.titlesize':9,'axes.labelsize':8,'xtick.labelsize':7,'ytick.labelsize':8,'legend.fontsize':6.5,'axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'none','pdf.fonttype':42})

def save(fig,name):
 fig.savefig(FIG/f'{name}.png',dpi=300)
 fig.savefig(FIG/f'{name}.pdf')
 fig.savefig(FIG/f'{name}.svg')
 plt.close(fig)

def main():
 FIG.mkdir(exist_ok=True)
 source=OUT/'measured_results.json';rows=json.loads(source.read_text())
 assert len(rows)==24 and all(r['qps']>0 for r in rows)
 groups={}
 for r in rows:groups.setdefault(r['run'],[]).append(r)
 for g in groups.values():
  g.sort(key=lambda r:r['L']);assert [r['L'] for r in g]==[100,300,580]
 contract=dict(conclusion='Observed throughput varies with memory budget and selected cache configuration; these concurrent single-run measurements do not establish speedups.',archetype='quantitative grid',backend='python',main_panels=['Recall versus observed QPS, connecting L100/300/580 in search-width order','Budget versus observed QPS, auto and fixed 64-page diagnostic paths kept separate'],source_rows=24,main_unique_rows=15,supplement_rows=12,reference_rows_repeated=3,excluded_rows=0,uncertainty='None: one run per configuration, no repeat-based intervals',units='MiB; QPS log10 axis; recall fraction',exports=['PNG preview 300dpi','PDF editable','SVG editable'],size_mm=[183,137],data_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
 (OUT/'validation/figure_contract.json').write_text(json.dumps(contract,indent=2)+'\n')
 fig=plt.figure(figsize=(183/25.4,137/25.4),layout='constrained')
 grid=fig.add_gridspec(3,2,height_ratios=[5,.45,1.4])
 a=fig.add_subplot(grid[0,0]);b=fig.add_subplot(grid[0,1])
 note=fig.add_subplot(grid[1,0]);note.axis('off')
 status=fig.add_subplot(grid[1,1],sharex=b)
 la=fig.add_subplot(grid[2,0]);lb=fig.add_subplot(grid[2,1]);la.axis('off');lb.axis('off')
 specs=[('diagnostics/low_budget_384','384 MiB: paged, 64 pages','#78569a','--'),('diagnostics/low_budget_512','512 MiB: paged, 64 pages','#b07a9d','--'),('auto_checks/budget_512','512 MiB: factors + codes paging','#0072B2','-'),('auto_checks/budget_640','640 MiB: resident + hot_dynamic','#009E73','-'),('calibration/resident','2 GiB: resident calibration reference','#666666',':')]
 handles=[]
 for key,label,color,style in specs:
  data=groups[key]
  line,=a.plot([r['recall'] for r in data],[r['qps'] for r in data],color=color,ls=style,lw=1.2,label=label)
  handles.append(line)
  for r,marker in zip(data,['o','^','s']):a.scatter(r['recall'],r['qps'],color=color,marker=marker,s=15,zorder=3)
 a.set(xlabel='Recall@10',ylabel='Observed QPS (log scale)',title='a  Recall–throughput',yscale='log',xlim=(.947,.983))
 a.set_xticks([.95,.96,.97,.98])
 widths=[(100,'#0072B2','o'),(300,'#D55E00','^'),(580,'#009E73','s')]
 for width,color,marker in widths:
  for prefix,style in [('auto_checks/','-'),('diagnostics/','--')]:
   data=sorted([r for r in rows if r['run'].startswith(prefix) and r['L']==width],key=lambda r:r['budget_mib'])
   b.plot([r['budget_mib'] for r in data],[r['qps'] for r in data],color=color,marker=marker,ms=3.5,ls=style,lw=1.2)
 b.set(ylabel='Observed QPS (log scale)',title='b  Fixed search widths',yscale='log',xlim=(230,670))
 b.tick_params(axis='x',labelbottom=False)
 b.legend(handles=[Line2D([],[],color=c,marker=m,label=f'L{w}',lw=1) for w,c,m in widths],loc='upper left',title='Search width',title_fontsize=7)
 for ax in [a,b]:ax.set_ylim(.45,160);ax.grid(axis='y',which='major',alpha=.2)
 status.set(ylim=(0,1),yticks=[],xlabel='Process budget (MiB)');status.set_xticks([256,384,512,640]);status.spines['left'].set_visible(False)
 status.plot(256,.5,marker='x',color='#a33f3f',ms=5,ls='none')
 status.plot(384,.5,marker='^',color='#777777',ms=4,ls='none')
 status.set_ylabel('Status',rotation=0,rotation_mode='anchor',labelpad=11,fontsize=6.5)
 note.text(0,.5,'Markers: circle L100; triangle L300; square L580',fontsize=6.5,va='center')
 la.legend(handles=handles,loc='upper left',frameon=False,borderaxespad=0,title='Configuration (panel a)',title_fontsize=7)
 lb.legend(handles=[Line2D([],[],color='#333333',ls='-',label='Auto policy'),Line2D([],[],color='#333333',ls='--',label='Explicit paging, 64-page cache'),Line2D([],[],color='#a33f3f',marker='x',ls='none',label='256 MiB: runtime failure (no QPS)'),Line2D([],[],color='#777777',marker='^',ls='none',label='384 MiB: auto rejected; paging passed')],loc='upper left',frameon=False,borderaxespad=0,title='Paths and status (panel b)',title_fontsize=7)
 fig.suptitle('GIST · cache reuse · beam1 · 32 workers\n100 validation queries; single run; concurrent work affects timing',fontsize=9)
 save(fig,'budget_curves')
 # All calibration data are shown separately because the 2 GiB limits do not
 # imply equal cache allocations (1 MiB paging versus 64 MiB records).
 fig,ax=plt.subplots(figsize=(120/25.4,120/25.4),layout='constrained')
 labels={'resident':'Resident; no record cache','factors':'Factors resident; paging cap 1 MiB','paged':'All paged; paging cap 1 MiB','hot_dynamic':'Resident + hot_dynamic; cap 64 MiB'}
 for mode,color in [('resident','#666666'),('factors','#0072B2'),('paged','#78569a'),('hot_dynamic','#009E73')]:
  data=groups['calibration/'+mode]
  ax.plot([r['recall'] for r in data],[r['qps'] for r in data],color=color,label=labels[mode],lw=1.2)
  for r,marker in zip(data,['o','^','s']):ax.scatter(r['recall'],r['qps'],color=color,marker=marker,s=18)
 ax.set(xlabel='Recall@10',ylabel='Observed QPS (log scale)',yscale='log',xlim=(.947,.983),ylim=(.45,160))
 ax.grid(axis='y',alpha=.2);fig.legend(*ax.get_legend_handles_labels(),loc='outside lower center',fontsize=6.5,frameon=False)
 ax.set_title('Calibration · 2 GiB process limit\nConcurrent timing; different explicit cache caps',fontsize=9)
 save(fig,'calibration_curves')
 print(FIG/'budget_curves.png')
if __name__=='__main__':main()
