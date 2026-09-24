"""Whole-process budget curves; accepted runs only, two-repeat min/max."""
import os,json,hashlib
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR','/tmp/gist-auto-mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'results/04_ours_memory_budget/gist_memory_auto_policy'
plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Arial','DejaVu Sans'],'font.size':8,'axes.titlesize':9,'axes.labelsize':8,'xtick.labelsize':7,'ytick.labelsize':7,'legend.fontsize':6,'svg.fonttype':'none','pdf.fonttype':42,'axes.spines.top':False,'axes.spines.right':False})
def main(split):
 source=OUT/'measured_results.json';rows=[r for r in json.loads(source.read_text()) if r['split']==split]
 completed=json.loads((OUT/split/'completed.json').read_text());widths=completed['widths'];budgets=completed['budgets'];groups={}
 for r in rows:groups.setdefault(('Reference' if r['reference'] else int(r['budget_mib']),r['L']),[]).append(r)
 assert len(rows)==2*len(widths)*(len(budgets)+1)
 def val(b,w):
  g=groups[b,w];assert len(g)==2 and {r['repeat'] for r in g}=={1,2};assert g[0]['recall']==g[1]['recall']
  q=[r['qps'] for r in g];assert min(q)>0
  return g[0]['recall'],2/sum(1/x for x in q),min(q),max(q)
 fig,axes=plt.subplots(1,2,figsize=(7.2047244,5.1),layout='constrained');handles=[]
 colors=plt.colormaps['viridis'](np.linspace(.08,.92,len(budgets)))
 for b,color in list(zip(budgets,colors))+[('Reference','#555555')]:
  v=[val(b,w) for w in widths]
  line,=axes[0].plot([x[0] for x in v],[x[1] for x in v],color=color,lw=1,marker='o' if split=='pilot' else None,ms=2,ls='--' if b=='Reference' else '-',label='Resident reference' if b=='Reference' else f'{b} MiB')
  axes[0].fill_between([x[0] for x in v],[x[2] for x in v],[x[3] for x in v],color=color,alpha=.10);handles.append(line)
 for w,color in [(100,'#0072B2'),(300,'#D55E00'),(580,'#009E73')]:
  v=[val(b,w) for b in budgets];q=np.array([x[1] for x in v]);axes[1].errorbar(budgets,q,yerr=[q-np.array([x[2] for x in v]),np.array([x[3] for x in v])-q],color=color,lw=1,marker='o',ms=2,capsize=2,label=f'L{w}')
 exp=json.loads((OUT/'experiment.json').read_text());ts=[x/2**20 for x in exp['thresholds'].values()]
 for t,style in zip(ts,[':','--','-.']):axes[1].axvline(t,color='#888888',lw=.7,ls=style)
 axes[0].set(title='a  Recall–throughput',xlabel='Recall@10',ylabel='QPS')
 axes[1].set(title='b  Fixed search widths',xlabel='Whole-process budget (MiB)',ylabel='QPS');axes[1].legend(frameon=False,loc='upper center')
 for ax in axes:ax.grid(axis='y',alpha=.15);ax.set_ylim(bottom=0)
 fig.legend(handles=handles,loc='outside lower center',ncol=4,frameon=False)
 rejected=[256,384]+[b for b in exp['requested_mib'] if b not in budgets]
 fig.suptitle(f'GIST · {split} · beam1 · 32 workers\nFixed / factors / safety accounted separately',fontsize=9)
 fig.text(.50,.16,'Preflight rejected (no QPS): '+', '.join(map(str,sorted(set(rejected))))+' MiB',ha='center',fontsize=6)
 # Reserve a separate status line above the shared legend.
 fig.get_layout_engine().set(rect=(0,.06,1,.94))
 folder=OUT/'figures';folder.mkdir(exist_ok=True)
 fig.savefig(folder/f'{split}_curves.png',dpi=300);fig.savefig(folder/f'{split}_curves.pdf');fig.savefig(folder/f'{split}_curves.svg');plt.close(fig)
 contract=dict(claim='Whole-process headroom determines codes paging, residency and optional record-cache capacity',archetype='quantitative grid',backend='python',input_rows=len(rows),excluded_performance_rows=0,repeats=2,center='harmonic mean QPS',spread='two-run min/max; not confidence interval',fixed_width_panel=[100,300,580],recall_panel_widths=widths,preflight_rejected=sorted(set(rejected)),thresholds_mib=ts,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),size_mm=[183,129.54],exports=['PNG preview','PDF','SVG'])
 (OUT/'validation'/f'{split}_figure_contract.json').write_text(json.dumps(contract,indent=2)+'\n')
if __name__=='__main__':
 import sys
 main(sys.argv[1] if len(sys.argv)>1 else 'pilot')
