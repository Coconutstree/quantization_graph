"""Updated a/b overview including factors and independently trained hot pages."""
import csv,hashlib,json,shutil
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import StrMethodFormatter,NullFormatter
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'results/04_ours_memory_budget/routing_factors_hot'
TARGET = ROOT / 'results/04_ours_memory_budget/gist_routing_diagnostics'

def main():
 summary=json.loads((OUT/'summary.json').read_text())
 history=[r for r in json.loads((TARGET/'tables/routing_paged_comparison.json').read_text()) if r['cohort'].startswith('historical')]
 recent=[]
 for mode,tier in [('resident',0),('clock',25),('clock',75),('factors',25),('factors',75),('hot',25),('hot',75)]:
  label='Ours-Resident (current)' if mode=='resident' else {'clock':'CLOCK','factors':'Factors','hot':'Factors + hot'}[mode]+f' / {793 if tier==25 else 868} MiB'
  for width in [100,300,580]:
   rr=[r for r in summary if r['mode']==mode and r['budget_tier']==tier and r['L']==width]
   if not rr:continue
   recent.append(dict(method=label,L=width,qps=len(rr)/sum(1/r['qps'] for r in rr),recall=sum(r['recall'] for r in rr)/len(rr),
      queries=100*len(rr),repeats=len(rr),budget_bytes=rr[0]['budget_bytes'],cohort='current_factors_experiment',
      source=';'.join('results/04_ours_memory_budget/routing_factors_hot/gist/runs/'+r['run']+'/result.json' for r in rr)))
 rows=history+recent
 assert len(recent)==21,'finish L300/580 regression before plotting'
 copied={}
 for src in (OUT/'gist').rglob('*'):
  if not src.is_file():continue
  dst=TARGET/'raw/Ours-Routing-Factors-Hot'/src.relative_to(OUT/'gist');dst.parent.mkdir(parents=True,exist_ok=True)
  h=hashlib.sha256(src.read_bytes()).hexdigest()
  if dst.exists():assert hashlib.sha256(dst.read_bytes()).hexdigest()==h
  else:shutil.copy2(src,dst)
  copied[str(dst.relative_to(TARGET))]=h
 (TARGET/'raw/Ours-Routing-Factors-Hot/import_manifest.json').write_text(json.dumps(dict(source=str(OUT/'gist'),files=copied),indent=2)+'\n')
 assert all(r['qps']>0 for r in rows)
 figure=TARGET/'figures';table=TARGET/'tables'
 figure.mkdir(parents=True,exist_ok=True);table.mkdir(parents=True,exist_ok=True)
 (table/'routing_factors_hot_source.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
 shutil.copy2(OUT/'pooled.csv',table/'routing_factors_hot_comparison.csv')
 shutil.copy2(OUT/'report.md',table/'routing_factors_hot_comparison.md')
 plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['DejaVu Sans'],'font.size':7,'axes.labelsize':8,
  'axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'none','pdf.fonttype':42,'legend.frameon':False})
 fig=plt.figure(figsize=(183/25.4,190/25.4))
 a=fig.add_axes([.12,.59,.57,.32]);b=fig.add_axes([.30,.15,.63,.29])
 methods=list(dict.fromkeys(r['method'] for r in rows))
 colors=['#252525','#718DA6','#9C824F','#9988AE','#719990','#B4707C','#D27023','#74A8BE','#28769F','#B4A269','#8C7129','#97AA86','#557142']
 markers=['o','s','^','D','v','P','X','o','s','^','D','v','P']
 for i,method in enumerate(methods):
  rr=sorted([r for r in rows if r['method']==method],key=lambda r:r['L'])
  historical=rr[0]['cohort'].startswith('historical')
  a.plot([r['recall'] for r in rr],[r['qps'] for r in rr],color=colors[i],linestyle='--' if historical else '-',marker=markers[i],
    markersize=2.5,linewidth=.9,label=method+(' [4 GiB]' if method=='Starling-Disk' else ''))
 a.set_yscale('log');a.yaxis.set_major_formatter(StrMethodFormatter('{x:g}'));a.yaxis.set_minor_formatter(NullFormatter())
 a.set_xlabel('Recall@10');a.set_ylabel('QPS (log scale)');a.grid(alpha=.15)
 a.set_title('a  Recall–QPS curves',loc='left',fontweight='bold',fontsize=9)
 a.legend(loc='upper left',bbox_to_anchor=(1.015,1),fontsize=5.5,handlelength=1.4,labelspacing=.8)
 data=[r for r in recent if r['L']==100]
 for i,r in enumerate(data):
  b.barh(i,r['qps'],height=.64,color=colors[methods.index(r['method'])])
  b.text(r['qps']+1.5,i,f"{r['qps']:.2f}",va='center',fontsize=7)
 b.set_yticks(range(len(data)),[r['method'] for r in data],fontsize=7);b.invert_yaxis();b.set_xlim(0,max(r['qps'] for r in data)*1.17)
 b.set_xlabel('QPS');b.xaxis.grid(alpha=.15)
 b.set_title('b  Current Ours variants at L100',loc='left',fontweight='bold',fontsize=9)
 fig.text(.5,.97,'GIST | beam1, workers32 | factors and hot-page trials',ha='center',fontsize=10)
 fig.text(.12,.515,'Dashed: historical 800-query runs. Solid: current 100-query runs.\nMeasured L points are connected; budgets / protocols differ across cohorts.',fontsize=6)
 fig.text(.5,.055,'Current L100: two timing rounds pooled; L300/580: one correctness run. No confidence intervals estimated.\nFactors and hot pages share the original total budget; hotspots use independent validation queries.\nStarling is a 4 GiB diagnostic with different recall, not a matched baseline.',ha='center',fontsize=6)
 fig.savefig(figure/'routing_factors_hot_comparison.svg');fig.savefig(figure/'routing_factors_hot_comparison.pdf');fig.savefig(figure/'routing_factors_hot_comparison.png',dpi=600);plt.close(fig)
 section='''\n## 新方案：factors 常驻与热点页\n\n![Factors 常驻与热点页对比](../figures/routing_factors_hot_comparison.png)\n\n[实测表](routing_factors_hot_comparison.md) · [CSV](routing_factors_hot_comparison.csv) · [PDF](../figures/routing_factors_hot_comparison.pdf) · [SVG](../figures/routing_factors_hot_comparison.svg)\n\na 图已加入最新 Recall–QPS 曲线；b 图只比较本次同期 Ours。793/868 MiB 是总预算的四舍五入标签，常驻参考为 2048 MiB；不再用原 25%/75% 比例命名 factors 模式。旧图与历史对照表保留在下方。\n'''
 p=table/'routing_paged_comparison.md';body=p.read_text()
 if '## 新方案：' not in body:
  pos=body.index('\n');p.write_text(body[:pos]+section+'\n'+body[pos:])
 (figure/'routing_factors_hot_qa.md').write_text('''# Figure contract

Quantitative grid; a answers the speed/recall question across all historical widths and current three widths; b shows exact current L100 throughput. Python/matplotlib only. 183 mm wide; editable PDF/SVG and 600 dpi PNG preview. Source data: ../tables/routing_factors_hot_source.json. All historical comparison rows and 21 current aggregate rows plotted. Previous v1/LRU pilot cohorts remain in the previous figure and are not mixed with the current experiment. L100 pools two timing runs; L300/580 single correctness runs. No CI or significance claims. Dashed historical and solid current curves connect actual L points without estimating intermediate observations. Budgets and protocols differ; Starling is a 4 GiB diagnostic. Validation-trained hotspots do not use test queries. Check glyph floor and panel collisions before delivery.
''')
 print(figure/'routing_factors_hot_comparison.png')
if __name__=='__main__':main()
