"""FIG-01: observed validation Recall/QPS and Recall/I/O, fixed M=32."""
import csv,json,os
os.environ.setdefault('MPLCONFIGDIR','/tmp/quantization_graph_matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from prepare import OUT,HERE,dump,sha

def main():
 rows=list(csv.DictReader((OUT/'measured_results.csv').open()))
 rows=[x for x in rows if x['split']=='tune'and('_grid_'in x['run']or'_extended_'in x['run'])]
 directory=OUT/'figures';directory.mkdir(exist_ok=True)
 style={'pca':('PCA + 1-bit (128D)','#0072B2','o','-'),
        'pca_residual':('PCA + 1-bit + residual norm (128D)','#D55E00','^','--'),
        'full1bit':('Full 1-bit (960D)','#009E73','s','-.')}
 spec=dict(id='FIG-01',purpose='Compare measured tradeoffs for the three fixed-M32 variants',
  source='../measured_results.csv',source_sha256=sha(OUT/'measured_results.csv'),script_sha256=sha(HERE/'plot.py'),
  data_availability='local accepted command/result/query records; no external dataset claims',
  type='two-panel line plot connecting measured search widths only',sample='validation100; one run per width; no uncertainty intervals',
  panels={'a':'Recall versus measured QPS','b':'Recall versus total 4KiB pages per query'},
  conditions=dict(budget_mib=538,M=32,workers=32,beam=1),
  caption='Fixed M=32 validation tradeoffs; numbers label search widths. Residual term is an empirical gate. Curves connect observed points without smoothing.',
  publication_status='experiment inspection; not claimed submission-ready',
  style='colorblind palette; shape and dash redundancy; editable SVG/PDF; zero y origins',points=rows)
 dump(directory/'figure_spec.json',spec)
 plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,
  'axes.spines.right':False,'pdf.fonttype':42,'ps.fonttype':42,'svg.fonttype':'none'})
 fig,axes=plt.subplots(1,2,figsize=(10.4,4.1))
 for variant,(label,color,marker,ls)in style.items():
  xs=sorted([r for r in rows if r['variant']==variant],key=lambda r:int(r['width']))
  assert xs and all(int(x['M'])==32 for x in xs)
  recalls=[float(x['recall'])for x in xs]
  for ax,key in zip(axes,('qps','total_pages_q')):
   values=[float(x[key])for x in xs]
   ax.plot(recalls,values,color=color,marker=marker,linestyle=ls,label=label,linewidth=1.5,
           markersize=6 if variant!='pca' else 8,markerfacecolor='white'if variant=='pca'else color)
   if variant!='pca_residual':
    for x,y,r in zip(recalls,values,xs):
     dy=(7 if variant=='pca'else -13)if key=='qps'else(-13 if variant=='pca'else 7)
     ax.annotate(r['width'],(x,y),xytext=(5,dy),textcoords='offset points',fontsize=8,color=color)
 for i,ax in enumerate(axes):
  ax.set_xlabel('Recall@10');ax.set_xlim(.88,.99);ax.set_ylim(bottom=0)
  ax.grid(axis='y',alpha=.2);ax.text(-.12,1.04,'ab'[i],transform=ax.transAxes,fontweight='bold',fontsize=13)
 axes[0].set_ylabel('QPS');axes[1].set_ylabel('Total 4 KiB pages / query')
 axes[0].set_title('Recall–throughput');axes[1].set_title('Recall–I/O')
 handles,labels=axes[0].get_legend_handles_labels()
 fig.legend(handles,labels,loc='lower center',bbox_to_anchor=(.5,.01),ncol=3,frameon=False,fontsize=9)
 fig.suptitle('GIST · 538 MiB · M=32 · validation100',fontsize=12,y=.99)
 fig.subplots_adjust(left=.08,right=.98,top=.84,bottom=.24,wspace=.32)
 files=[]
 for extension in ('png','pdf','svg'):
  path=directory/f'recall_qps_validation.{extension}';fig.savefig(path,dpi=240);files.append(path)
 plt.close(fig)
 dump(directory/'manifest.json',dict(figure_id='FIG-01',point_count=len(rows),data_sha256=sha(OUT/'measured_results.csv'),
   files={p.name:sha(p)for p in files},spec_sha256=sha(directory/'figure_spec.json')))
 (directory/'qa.md').write_text('''# FIG-01 QA

- Sources: accepted measured_results.csv; exact points copied into figure_spec.json.
- Every curve uses M=32; widths label measured points, not interpolated targets.
- Validation100, one timing observation per point; no invented error bars or smoothing.
- Zero y origins; units and fixed memory budget explicit.
- Color, marker shape and dash pattern distinguish methods; SVG text remains editable.
- PCA curves may overlap because the measured differences are small; no offsets applied to data.
- Intended use: experiment inspection. Final manuscript placement/typography review not requested.
- Visual inspection: pending.
''')

if __name__=='__main__':main()
