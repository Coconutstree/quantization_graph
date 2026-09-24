"""Two-repeat data-capacity figures; exact-byte boundary cases stay distinct."""
import json,os,statistics,hashlib
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR','/tmp/gist-data-budget-mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'results/04_ours_memory_budget/gist_data_budget'
plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Arial','DejaVu Sans'],'font.size':8,'axes.titlesize':9,'axes.labelsize':8,'xtick.labelsize':7,'ytick.labelsize':8,'legend.fontsize':7,'svg.fonttype':'none','pdf.fonttype':42,'axes.spines.top':False,'axes.spines.right':False})
def main():
 source=OUT/'measured_results.json';rows=json.loads(source.read_text());exp=json.loads((OUT/'experiment.json').read_text())
 assert len(rows)==48
 groups={}
 for r in rows:groups.setdefault((r['case'],r['L']),[]).append(r)
 assert all(len(g)==2 and {r['repeat'] for r in g}=={1,2} and all(r['queries']==100 for r in g) for g in groups.values())
 def value(case,width):
  g=groups[case,width];q=[r['qps'] for r in g]
  assert all(x>0 for x in q)
  assert len({r['recall'] for r in g})==1
  return g[0]['recall'],2/sum(1/x for x in q),min(q),max(q)
 cases=[c for c,b in exp['cases']]
 labels={'64MiB':'64 MiB','128MiB':'128 MiB','Tminus1byte':'T − 1 byte','Texact':'T (exact)','Tplus16MiB':'T + 16 MiB','256MiB':'256 MiB','512MiB':'512 MiB','resident_reference':'Resident reference'}
 colors=['#0072B2','#56B4E9','#78569a','#009E73','#e69f00','#d55e00','#8c564b','#666666']
 fig,axes=plt.subplots(1,2,figsize=(7.2047244,4.7244094),layout='constrained')
 handles=[]
 for case,color in zip(cases+['resident_reference'],colors):
  v=[value(case,w) for w in [100,300,580]]
  line,=axes[0].plot([x[0] for x in v],[x[1] for x in v],color=color,lw=1.2,marker='o',ms=3,ls='--' if case=='resident_reference' else '-',label=labels[case])
  axes[0].fill_between([x[0] for x in v],[x[2] for x in v],[x[3] for x in v],color=color,alpha=.1)
  handles.append(line)
 for w,color in [(100,'#0072B2'),(300,'#D55E00'),(580,'#009E73')]:
  values=[value(c,w) for c in cases];q=np.array([x[1] for x in values]);lo=np.array([x[2] for x in values]);hi=np.array([x[3] for x in values])
  axes[1].errorbar(range(len(cases)),q,yerr=[q-lo,hi-q],color=color,marker='o',ms=3,lw=1.2,capsize=2,label=f'L{w}')
 axes[0].set(title='a  Recall–throughput',xlabel='Recall@10',ylabel='QPS')
 axes[1].set(title='b  Fixed search widths',xlabel='Data/cache budget (discrete settings)',ylabel='QPS')
 axes[1].set_xticks(range(len(cases)),['64\nMiB','128\nMiB','T−1\nbyte','T\nexact','T+16\nMiB','256\nMiB','512\nMiB'])
 axes[1].legend(frameon=False,loc='upper left')
 for ax in axes:ax.grid(axis='y',alpha=.18);ax.set_ylim(bottom=0)
 fig.legend(handles=handles,loc='outside lower center',ncol=4,frameon=False)
 fig.suptitle('GIST · beam1 · 32 workers · 100 test queries\nTwo runs; process cap fixed at 2 GiB; T = 141.143799 MiB',fontsize=9)
 folder=OUT/'figures';folder.mkdir(exist_ok=True)
 fig.savefig(folder/'data_budget_curves.png',dpi=300)
 fig.savefig(folder/'data_budget_curves.pdf')
 fig.savefig(folder/'data_budget_curves.svg')
 plt.close(fig)
 contract=dict(backend='python',archetype='quantitative grid',claim='Data capacity selects paging versus full routing residency independently of process protection',panels={'a':'Recall/QPS for each data quota and resident reference','b':'QPS against discrete quota settings preserving T minus one byte versus T'},input_rows=48,excluded_rows=0,repeats=2,center='harmonic mean QPS (equal measured query count)',uncertainty='two-run min/max, not confidence interval',process_cap_mib=2048,data_threshold_bytes=exp['resident_data_threshold_bytes'],source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),exports=['PNG report preview 300 dpi','PDF editable','SVG editable'],size_mm=[183,120])
 (OUT/'validation/figure_contract.json').write_text(json.dumps(contract,indent=2)+'\n')
if __name__=='__main__':main()
