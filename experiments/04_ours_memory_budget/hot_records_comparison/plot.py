"""All 40 widths; two full test repeats; independent panel per dataset."""
import json, os, sys, subprocess
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR','/tmp/hot_records_comparison_mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'hybrid_tuning'))
from plot_comparisons import ROOT,load_series,canvas,csvwrite
plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['DejaVu Sans'],'svg.fonttype':'none','pdf.fonttype':42,'savefig.dpi':300})
OUT=ROOT/'results/04_ours_memory_budget/hot_records_comparison'
def draw(dataset):
    root=OUT/dataset;dest=root/'figures';dest.mkdir(exist_ok=True)
    selected=json.loads((root/'protocol.json').read_text())['selected_bps']
    contract={'backend':'python','claim':'Compare hot records and validation-selected hybrid on independent test queries at equal available memory','archetype':'quantitative comparison','size_mm':[183,128],'query_count_per_L':800,'repeats':2,'aggregation':'total queries / total seconds','spread':'two-run min-max, not CI','exports':['png','pdf','svg'],'excluded_rows':0}
    (dest/'figure_contract.json').write_text(json.dumps(contract,indent=2))
    fig,ax=canvas(('GIST' if dataset=='gist' else 'AG News')+': Hot Records vs Hybrid','800 test queries per L; two runs; baseline and hybrid reused from prior measurements')
    specs=[('Baseline',['baseline_start','baseline_end'],'#666666',':','o'),('Hot Records',['hot_records_r1','hot_records_r2'],'#C76A2C','-','s'),(f'Hybrid {selected/100:g}%',['hybrid_r1','hybrid_r2'],'#1679B5','-','^')]
    raw=[];hashes={};ref=None
    for label,names,c,style,marker in specs:
        x,y,lo,hi,rr,hh=load_series([root/'runs'/n for n in names],800,label)
        if ref is None:ref=x
        else:assert np.array_equal(ref,x)
        raw+=rr;hashes.update(hh)
        ax.fill_between(x,lo,hi,color=c,alpha=.1,lw=0)
        ax.plot(x,y,label=label,color=c,ls=style,marker=marker,ms=2.8,lw=1.5)
    ax.set_xlim(max(0,float(ref.min())-.01),1)
    fig.legend(*ax.get_legend_handles_labels(),loc='upper center',bbox_to_anchor=(.54,.85),ncol=3,frameon=False)
    fig.text(.12,.10,'Lines: pooled QPS. Shading: two-run min–max, not confidence intervals.',fontsize=7)
    fig.text(.12,.052,'All 40 measured L values. Same budget; controls measured earlier; timing confounding remains.',fontsize=7)
    ax.set_ylim(bottom=0)
    assert ax.get_ylim()[1]>max(float(np.max(line.get_ydata())) for line in ax.lines)
    fig.savefig(dest/'recall_qps.png',dpi=300,facecolor='white')
    fig.savefig(dest/'recall_qps.pdf',facecolor='white')
    fig.savefig(dest/'recall_qps.svg',facecolor='white')
    plt.close(fig)
    csvwrite(dest/'source_runs.csv',raw)
    assert len(raw)==240
    skill=Path('/home/kai3/.agents/skills/nature-figure/scripts')
    for name,cmd in [('static_preflight',[sys.executable,str(skill/'validate_figure.py'),str(Path(__file__))]),('pdf_text_audit',[sys.executable,str(skill/'audit_pdf_text.py'),str(dest/'recall_qps.pdf'),'--min-pt','5'])]:
        r=subprocess.run(cmd,capture_output=True,text=True);(dest/(name+'.txt')).write_text(r.stdout+r.stderr);assert r.returncode==0
    (dest/'qa.json').write_text(json.dumps({'source_rows':240,'excluded':0,'source_hashes':hashes,'visual_review':'pending','notes':'Shared helpers export 183x128 mm, PNG 300 dpi and editable PDF/SVG; no TIFF requested.'},indent=2))
if __name__=='__main__':
    for dataset in ['gist','agnews']:draw(dataset)
