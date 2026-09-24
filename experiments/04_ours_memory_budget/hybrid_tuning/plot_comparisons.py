"""Two single-panel GIST Recall–QPS plots; validation and test stay separate.
Python, 183 x 128 mm, editable vector text, all measured widths, no smoothing.
"""
import csv,json,os,sys
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR','/tmp/gist_hybrid_matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from run import verify_acceptance
from protocol import ROOT,WIDTHS,MODES,sha
TUNE=ROOT/'results/04_ours_memory_budget/hybrid_tuning'
V3=ROOT/'results/04_ours_memory_budget/v3_direct'
F=TUNE/'figures';F.mkdir(exist_ok=True)
G=V3/'figures';G.mkdir(exist_ok=True)
plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['DejaVu Sans'],'font.size':8,'axes.labelsize':9,'xtick.labelsize':8,'ytick.labelsize':8,'legend.fontsize':8,'axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'none','pdf.fonttype':42})

def csvwrite(path,rows):
 with path.open('w',newline='')as f:
  wr=csv.DictWriter(f,fieldnames=rows[0]);wr.writeheader();wr.writerows(rows)
def load_series(folders,count,label):
 vals=[];recalls=[];raw=[];hashes={}
 for folder in folders:
  verify_acceptance(folder);p=folder/'result.json';hashes[str(folder.relative_to(ROOT))]=sha(p)
  rs=json.loads(p.read_text())['summary_rows'];assert [r['search_width']for r in rs]==WIDTHS
  assert all(r['query_count']==count for r in rs)
  vals.append([r['qps']for r in rs]);recalls.append([r['recall']for r in rs])
  raw += [{'series':label,'run':folder.name,'L':r['search_width'],'recall':r['recall'],'qps':r['qps'],'query_count':count}for r in rs]
 assert all(np.array_equal(recalls[0],r)for r in recalls)
 q=np.array(vals);assert np.isfinite(q).all() and (q>0).all()
 return np.array(recalls[0]),len(vals)/np.sum(1/q,axis=0),q.min(axis=0),q.max(axis=0),raw,hashes

def canvas(title,subtitle):
 fig,ax=plt.subplots(figsize=(7.2047244094,5.0393700787))
 fig.subplots_adjust(left=.12,right=.975,bottom=.20,top=.70)
 fig.suptitle(title,y=.974,fontsize=12,fontweight='bold')
 fig.text(.5,.918,subtitle,ha='center',fontsize=8)
 ax.set(xlabel='Recall@10',ylabel='QPS',xlim=(.60,1))
 ax.grid(axis='y',color='#E5E8EB',lw=.6);ax.set_axisbelow(True)
 return fig,ax

def save(fig,path):
 ax=fig.axes[0];ax.set_ylim(bottom=0)
 assert ax.get_ylim()[1]>max(float(np.max(line.get_ydata())) for line in ax.lines)
 for ext in ['png','pdf','svg']:fig.savefig(path.with_suffix('.'+ext),dpi=300,facecolor='white')
 plt.close(fig)

def main():
 fig,ax=canvas('GIST: hybrid record-quota comparison','Validation set: 100 queries per L; two runs per quota; fixed cache budget')
 ratios=[0,1250,2500,3750,5000,7500]
 specs=[('Baseline',[TUNE/'runs/validation_baseline',TUNE/'runs/validation_baseline_end'],'#666666',':','o')]
 colors=['#9C6BA4','#D6A322','#C76A2C','#1679B5','#379584','#7B624A'];marks=['s','D','v','^','P','X']
 for b,c,m in zip(ratios,colors,marks):specs.append((f'Hybrid {b/100:g}%'+(' (selected)'if b==3750 else ''),[TUNE/f'runs/validation_r1_p{b}',TUNE/f'runs/validation_r2_p{b}'],c,'-',m))
 raw=[];pooled=[];hashes={};reference=None
 for label,folders,c,style,marker in specs:
  x,y,lo,hi,rr,hh=load_series(folders,100,label);raw+=rr;hashes.update(hh)
  if reference is None:reference=x
  else:assert np.array_equal(reference,x)
  ax.fill_between(x,lo,hi,color=c,alpha=.07,lw=0)
  ax.plot(x,y,label=label,color=c,ls=style,marker=marker,ms=2.8,lw=1.9 if 'selected'in label else 1.15,zorder=4 if 'selected'in label else 2)
  pooled +=[{'series':label,'L':w,'recall':float(a),'qps':float(b),'run_min_qps':float(l),'run_max_qps':float(h)}for w,a,b,l,h in zip(WIDTHS,x,y,lo,hi)]
 fig.legend(*ax.get_legend_handles_labels(),loc='upper center',bbox_to_anchor=(.54,.877),ncol=3,frameon=False,handlelength=2.6,columnspacing=1.15)
 fig.text(.12,.10,'Lines: total queries / total measured seconds. Shading: two-run min–max, not confidence intervals.',fontsize=7)
 fig.text(.12,.052,'All 40 measured L values shown. Ratios are record-budget caps; graph cap remains 25%.',fontsize=7)
 save(fig,F/'gist_hybrid_final');csvwrite(F/'source_runs.csv',raw);csvwrite(F/'source_pooled.csv',pooled)
 assert len(raw)==560 and len(pooled)==280
 qa={'data_split':'validation (100 tuning queries; disjoint from 100 hotspot-training queries)','raw_rows':len(raw),'pooled_rows':len(pooled),'excluded':0,'smoothing':False,'source_hashes':hashes,'visual_review':'pending'}
 (F/'qa.json').write_text(json.dumps(qa,indent=2))
 (F/'figure_contract.json').write_text(json.dumps({'backend':'python','archetype':'quantitative comparison','claim':'Compare all six hybrid record quotas using validation measurements only','axes':'Recall@10 versus pooled QPS','panels':1,'n':2,'size_mm':[183,128],'aggregation':'pooled queries / pooled measured seconds','spread':'min-max, not CI','exports':['png','pdf','svg']},indent=2))
 fig,ax=canvas('GIST: memory-strategy comparison','Test set: 800 queries per L; one complete scan per strategy; 2 GiB process limit')
 labels=['Baseline','Page Cache','Hot Graph','Hot Records','Hybrid 25%','Navigation','Navigation + Page','Navigation + Hybrid','Full Records']
 colors=['#666666','#239582','#A39257','#9764A0','#CF7E2C','#697AB0','#239582','#CF7E2C','#1679B5']
 markers=['o','s','D','v','^','x','s','^','P'];raw=[];hashes={}
 for mode,label,c,marker in zip(MODES,labels,colors,markers):
  if mode=='full_payload':continue  # User-requested exclusion from strategy figure only.
  x,y,_,_,rr,hh=load_series([V3/mode],800,label);raw+=rr;hashes.update(hh)
  ax.plot(x,y,color=c,label=label,ls='--'if mode.startswith('nav')else ':'if mode=='baseline'else '-',marker=marker,ms=2.5,lw=1.6 if mode in ['hybrid','full_payload']else 1.05)
 fig.legend(*ax.get_legend_handles_labels(),loc='upper center',bbox_to_anchor=(.54,.877),ncol=3,frameon=False,handlelength=2.6,columnspacing=1.2)
 fig.text(.12,.10,'All 320 displayed measurements; no smoothing or interpolation. One run: no uncertainty band.',fontsize=7)
 fig.text(.12,.052,'Hybrid uses the original 25% cap. Navigation may change recall. Full Records omitted as requested.',fontsize=7)
 save(fig,G/'gist_memory_strategies');csvwrite(G/'memory_strategies_source.csv',raw);assert len(raw)==320
 (G/'memory_strategies_qa.json').write_text(json.dumps({'original_rows':360,'raw_rows':320,'excluded':40,'exclusion_reason':'User requested removal of Full Records curve; raw experiment retained','data_split':'test','repeats':1,'source_hashes':hashes,'visual_review':'pending','size_mm':[183,128]},indent=2))
 # Replace the earlier two-panel section; do not alter accepted measurements.
 p=ROOT/'ours_hybrid_ratio_tests.md';txt=p.read_text();txt=txt.split('## GIST最终验收曲线')[0].split('## GIST六档配额曲线')[0].rstrip()
 txt+='''\n\n## GIST六档配额曲线（验证集）

![GIST六档hybrid配额Recall–QPS](results/04_ours_memory_budget/hybrid_tuning/figures/gist_hybrid_final.png)

包含基线和0%、12.5%、25%、37.5%、50%、75%全部六档配额；单张Recall–QPS图，已删除右侧增长幅度图。数据来自100条调参验证查询，各配额两轮，主线为两轮合计查询数/合计查询时间，阴影为两轮最小–最大值，不是置信区间。基线也使用调参前后两轮。全部40档均保留，无平滑或插值。同L召回一致。

这里只能用验证集：测试集实际只测了25%和选中37.5%，其余配额没有测试集结果。该图不能当作六档配额的测试集验收图。

[PDF](results/04_ours_memory_budget/hybrid_tuning/figures/gist_hybrid_final.pdf) · [SVG](results/04_ours_memory_budget/hybrid_tuning/figures/gist_hybrid_final.svg) · [两轮合并源数据](results/04_ours_memory_budget/hybrid_tuning/figures/source_pooled.csv)

## GIST不同内存方案曲线（测试集）

![GIST八种内存方案Recall–QPS](results/04_ours_memory_budget/v3_direct/figures/gist_memory_strategies.png)

原v3_direct的基线＋7个方案，每组800条测试查询、40档L、一次完整扫描，共320个展示点，无误差带。图中Hybrid为原25%配额；没有混入后来单独调参实验的37.5%结果。导航三组会改变召回；已按要求从图中删除Full Records曲线及其40个点，原始实验记录保留。

[PDF](results/04_ours_memory_budget/v3_direct/figures/gist_memory_strategies.pdf) · [SVG](results/04_ours_memory_budget/v3_direct/figures/gist_memory_strategies.svg) · [绘图源数据](results/04_ours_memory_budget/v3_direct/figures/memory_strategies_source.csv)
'''
 p.write_text(txt)
 print('Rendered single-panel validation quota plot (560 raw / 280 pooled) and test strategy plot (320 displayed raw).')
if __name__=='__main__':main()
