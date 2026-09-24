"""AG News complete validation Recall-QPS curves; no incomplete test data."""
from pathlib import Path
import json
import os
os.environ.setdefault("MPLCONFIGDIR", "/tmp/agnews_plot_mpl")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from plot_comparisons import ROOT,WIDTHS,load_series,canvas,save,csvwrite
plt.rcParams.update({"font.family": "sans-serif", "font.sans-serif": ["DejaVu Sans"], "svg.fonttype": "none", "pdf.fonttype": 42, "savefig.dpi": 300})
OUT=ROOT/'results/04_ours_memory_budget/hybrid_agnews'
FIG=OUT/'figures';FIG.mkdir(exist_ok=True)

def plot_series(specs,count,title,subtitle,stem):
 fig,ax=canvas(title,subtitle);raw=[];pooled=[];hashes={};xmin=1.;reference=None
 for label,folders,color,style,marker in specs:
  x,y,lo,hi,rr,hh=load_series(folders,count,label);raw+=rr;hashes.update(hh)
  if reference is None:reference=x
  else:assert np.array_equal(reference,x)
  xmin=min(xmin,float(x.min()))
  ax.fill_between(x,lo,hi,color=color,alpha=.07,lw=0)
  ax.plot(x,y,label=label,color=color,ls=style,marker=marker,ms=2.8,lw=1.9 if 'selected' in label else 1.15)
  pooled +=[{'series':label,'L':w,'recall':float(a),'qps':float(b),'run_min_qps':float(l),'run_max_qps':float(h)}for w,a,b,l,h in zip(WIDTHS,x,y,lo,hi)]
 ax.set_xlim(max(0,xmin-.004),1)
 fig.legend(*ax.get_legend_handles_labels(),loc='upper center',bbox_to_anchor=(.54,.877),ncol=3,frameon=False,handlelength=2.6,columnspacing=1.15)
 fig.text(.12,.10,'Lines: total queries / total measured seconds. Shading: two-run min–max, not confidence intervals.',fontsize=7)
 fig.text(.12,.052,'All 40 measured L values shown. No smoothing or interpolation; same recall at matched L.',fontsize=7)
 save(fig,FIG/stem);csvwrite(FIG/(stem+'_runs.csv'),raw);csvwrite(FIG/(stem+'_pooled.csv'),pooled)
 (FIG/(stem+'_qa.json')).write_text(json.dumps({'raw_rows':len(raw),'pooled_rows':len(pooled),'excluded':0,'source_hashes':hashes,'repeats':2,'matched_recall':True,'visual_review':'pending','size_mm':[183,128]},indent=2))

def main():
 selected=json.loads((OUT/'selection_lock.json').read_text())['selected_bps']
 specs=[('Baseline',[OUT/'runs/validation_baseline',OUT/'runs/validation_baseline_end'],'#666666',':','o')]
 for b,c,m in zip([0,1250,2500,3750,5000,7500],['#9C6BA4','#D6A322','#C76A2C','#1679B5','#379584','#7B624A'],['s','D','v','^','P','X']):
  specs.append((f'Hybrid {b/100:g}%'+(' (selected)'if b==selected else ''),[OUT/f'runs/validation_r1_p{b}',OUT/f'runs/validation_r2_p{b}'],c,'-',m))
 plot_series(specs,100,'AG News: hybrid record-quota comparison','Validation set: 100 queries per L; two runs per quota; fixed cache budget','agnews_hybrid_quotas')
 doc=['# AG News：hybrid Recall–QPS图','', '## 六档配额对比（验证集）','', '![AG News六档配额对比](results/04_ours_memory_budget/hybrid_agnews/figures/agnews_hybrid_quotas.png)','', '基线与0%、12.5%、25%、37.5%、50%、75%全部六档；100条调参验证查询，每档两轮。主线为合计查询数/合计查询时间，阴影为两轮最小–最大范围，不是置信区间。所有40档L均保留，按L连接，不平滑、不插值。另100条独立验证查询训练热点，测试查询不参与配额选择。','', '[PDF](results/04_ours_memory_budget/hybrid_agnews/figures/agnews_hybrid_quotas.pdf) · [SVG](results/04_ours_memory_budget/hybrid_agnews/figures/agnews_hybrid_quotas.svg)','']
 state=json.loads((OUT/'state.json').read_text())
 if state['status']=='completed':
  specs=[('Baseline',[OUT/'runs/baseline_start',OUT/'runs/baseline_end'],'#666666',':','o')]
  for b,c,m in [(2500,'#C76A2C','s'),(selected,'#1679B5','^')]:
   if b==2500 and selected==2500:continue
   specs.append((f'Hybrid {b/100:g}%'+(' (selected)'if b==selected else ''),[OUT/f'runs/hybrid_p{b}_r1',OUT/f'runs/hybrid_p{b}_r2'],c,'-',m))
  plot_series(specs,800,'AG News: final hybrid test comparison','Test set: 800 queries per L; two runs per method; 2 GiB process limit','agnews_hybrid_test')
  doc+=['## 最终验收（测试集）','', '![AG News测试集验收](results/04_ours_memory_budget/hybrid_agnews/figures/agnews_hybrid_test.png)','', '使用锁定比例后的800条测试查询，两轮合并；不与验证集混用。']
 else:doc+=['测试集尚未全部完成，目前不绘制测试集验收图。完成后重新运行本绘图脚本可生成。']
 (ROOT/'ours_hybrid_agnews_figures.md').write_text('\n'.join(doc)+'\n')
 (FIG/'figure_contract.json').write_text(json.dumps({'backend':'python','archetype':'quantitative comparison','claim':'Compare measured recall-throughput across six validation quotas; keep test evidence separate','aggregation':'pooled query count / pooled measured seconds','uncertainty':'two technical-run min-max, not CI','size_mm':[183,128],'exports':['png','pdf','svg']},indent=2))
 print('Plot complete; test stage:',state['status'],state.get('current'))
if __name__=='__main__':main()
