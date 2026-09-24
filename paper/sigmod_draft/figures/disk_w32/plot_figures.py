"""Reproduce disk ANN paper figures from the frozen source-data snapshot.

All available completed observations are retained. Cropped views are reported.
One measurement per setting: no inferred across-run uncertainty or smoothing.
"""
from pathlib import Path
import csv
import json
import math
import os
import string

ROOT = Path(__file__).resolve().parent
os.environ.setdefault('MPLCONFIGDIR', str(ROOT / '.mplconfig'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter
import numpy as np

DATASETS = ['agnews', 'gist', 'dbpedia']
NAMES = {'agnews': 'AGNews', 'gist': 'GIST', 'dbpedia': 'DBpedia'}
METHODS = ['Ours-Disk', 'DiskANN-PQ-Disk', 'SymphonyQG-DiskPort', 'OG-LVQ-DiskPort', 'Glass-NSG-DiskPort']
DISPLAY = ['Ours', 'DiskANN-PQ', 'SymphonyQG', 'OG-LVQ', 'Glass-NSG']
COLORS = ['#C44E52', '#4C78A8', '#8172B3', '#46968C', '#7F8994']
MARKERS = ['o', 's', '^', 'D', 'v']
STYLES = ['-', '--', '-.', ':', (0, (5, 2))]
plt.rcParams.update({
    'font.family': 'sans-serif', 'font.sans-serif': ['DejaVu Sans'],
    'font.size': 8, 'axes.labelsize': 8, 'axes.titlesize': 8,
    'xtick.labelsize': 8, 'ytick.labelsize': 8, 'legend.fontsize': 8,
    'axes.spines.top': False, 'axes.spines.right': False, 'axes.linewidth': 0.6,
    'xtick.major.width': 0.6, 'ytick.major.width': 0.6,
    'svg.fonttype': 'none', 'pdf.fonttype': 42,
    'savefig.facecolor': 'white', 'axes.facecolor': 'white',
})


def write_csv(name, rows):
    keys = list(dict.fromkeys(k for row in rows for k in row))
    with (ROOT / 'source_data' / name).open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def load():
    result, audit, missing = {}, [], []
    for dataset in DATASETS:
        for method in METHODS:
            p = ROOT / 'source_data/snapshot' / dataset / method / 'result.json'
            if not p.exists():
                missing.append({'dataset': dataset, 'method': method, 'status': 'unmeasured'})
                continue
            obj = json.loads(p.read_text())
            assert obj['status'] == 'done' and obj['phase'] == 'test'
            assert obj['workers'] == 32 and obj['repeat_id'] == 0
            assert obj['cache_mode'] == 'c0' and obj['page_size'] == 4096
            rows = sorted(obj['summary_rows'], key=lambda r: r['search_width'])
            assert len({r['search_width'] for r in rows}) == len(rows)
            for i, row in enumerate(rows):
                assert 0 <= row['recall'] <= 1
                for field in ['qps','latency_p95_us','bytes_read_per_query','io_requests_per_query']:
                    assert math.isfinite(row[field]) and row[field] > 0, (dataset, method, field)
                row.update(dataset=dataset, method=method, source_row=i,
                           qps_units='queries/s', p95_ms=row['latency_p95_us']/1000,
                           read_mib=row['bytes_read_per_query']/2**20,
                           formal_ready=obj['formal_ready'], workers=obj['workers'],
                           cache_mode=obj['cache_mode'], repeat_id=obj['repeat_id'],
                           source_run_id=obj['run_id'], binary_sha256=obj['native_binary_sha256'],
                           query_split_sha256=obj['query_split_sha256'])
            result[dataset,method] = rows
            audit.append({'dataset':dataset,'method':method,'observations':len(rows),
                          'test_queries_per_point':sorted({r['query_count'] for r in rows}),
                          'formal_ready':obj['formal_ready'],
                          'memory_accounting_complete':obj['memory_accounting_complete'],
                          'locality_layout':bool(obj.get('locality_layout_dir')),
                          'recall_min':min(r['recall'] for r in rows),
                          'recall_max':max(r['recall'] for r in rows),
                          'query_order_sha256':obj['query_order_sha256']})
    for d in DATASETS:
        a=[x for x in audit if x['dataset']==d]
        assert len({x['query_order_sha256'] for x in a}) == 1
    allrows = [r for group in result.values() for r in group]
    write_csv('all_measurements.csv', allrows)
    selected=[]
    for d in DATASETS:
        for target in [.95,.99]:
            for m in METHODS:
                rows=result.get((d,m),[])
                eligible=[r for r in rows if r['recall']>=target]
                record={'dataset':d,'target_recall':target,'method':m}
                if eligible:
                    row=max(eligible,key=lambda r:r['qps'])
                    record.update(status='measured',**row)
                else:
                    record['status']='target_not_reached' if rows else 'unmeasured'
                    if rows:record['max_measured_recall']=max(r['recall'] for r in rows)
                selected.append(record)
    write_csv('target_recall.csv',selected)
    return result, audit, missing, allrows


def number(x, pos=None):
    return f'{x/1000:g}k' if x>=1000 else f'{x:g}'


def export(fig, name):
    fig.canvas.draw()
    for t in fig.findobj(matplotlib.text.Text):
        if t.get_visible() and t.get_text():
            assert t.get_fontsize() >= 5
    fig.savefig(ROOT / (name + '.pdf'))
    fig.savefig(ROOT / (name + '.svg'))
    fig.savefig(ROOT / (name + '.png'), dpi=300)
    fig.savefig(ROOT / (name + '.tiff'), dpi=600, pil_kwargs={'compression':'tiff_lzw'})
    plt.close(fig)


def legend(fig):
    handles=[Line2D([],[],color=COLORS[i],marker=MARKERS[i],linestyle=STYLES[i],
                    lw=1.6 if i==0 else 1.1,markersize=3.5,markerfacecolor='white',label=label)
             for i,label in enumerate(DISPLAY)]
    fig.legend(handles=handles,loc='upper center',bbox_to_anchor=(.53,1.0),
               ncol=5,frameon=False,handlelength=2,columnspacing=1.1,handletextpad=.5)


FIELDS={
 'qps':('Throughput (queries/s)',(.7,800),[1,10,100]),
 'read_mib':('Read volume (MiB/query)',(.08,150),[.1,1,10,100]),
 'p95_ms':('p95 latency (ms)',(50,70000),[100,1000,10000]),
 'io_requests_per_query':('I/O requests/query',(10,30000),[10,100,1000,10000])}


def triptych(data,name,field,zoom=True):
    fig,axes=plt.subplots(1,3,figsize=(7.007874,2.440945)) # 178 x 62 mm
    fig.subplots_adjust(left=.088,right=.988,bottom=.215,top=.73,wspace=.28)
    legend(fig)
    label,limits,ticks=FIELDS[field]
    viewport=[]
    for j,(d,ax) in enumerate(zip(DATASETS,axes)):
        ax.set_title(f'({string.ascii_lowercase[j]})  {NAMES[d]}',pad=5)
        ax.set_yscale('log') # loader validates strictly positive values
        ax.set_ylim(*limits);ax.set_yticks(ticks);ax.yaxis.set_major_formatter(FuncFormatter(number))
        ax.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
        ax.set_xlim(.90 if zoom else 0,1.002)
        ax.set_xticks([.90,.95,1.] if zoom else [0,.5,1.])
        ax.xaxis.set_major_formatter(FuncFormatter(lambda x,pos:f'{x:.2f}'))
        ax.set_xlabel('Recall@10',labelpad=3)
        if j==0:ax.set_ylabel(label,labelpad=4)
        ax.grid(axis='y',color='#E1E4E8',lw=.45);ax.set_axisbelow(True)
        for i,m in enumerate(METHODS):
            rows=data.get((d,m),[])
            if not rows:continue
            ax.plot([r['recall'] for r in rows],[r[field] for r in rows],
                    color=COLORS[i],linestyle=STYLES[i],lw=1.65 if i==0 else 1.05,
                    marker=MARKERS[i],markevery=5,markersize=3.2,
                    markerfacecolor='white',markeredgewidth=.85,zorder=5 if i==0 else 3)
            n=sum(r['recall']>=.9 for r in rows) if zoom else len(rows)
            assert all(limits[0]<r[field]<limits[1] for r in rows if not zoom or r['recall']>=.9)
            viewport.append(dict(figure=name,panel=string.ascii_lowercase[j],dataset=d,method=m,
                                 total=len(rows),in_window=n,outside_window=len(rows)-n))
    export(fig,name)
    return viewport


def screening(data):
    fig,axes=plt.subplots(2,3,figsize=(7.007874,3.937008)) # 178 x 100 mm
    fig.subplots_adjust(left=.088,right=.988,bottom=.13,top=.85,hspace=.72,wspace=.28)
    records=[]
    for j,d in enumerate(DATASETS):
        rows=data[d,'Ours-Disk'];x=[r['search_width'] for r in rows]
        for k in range(2):
            ax=axes[k,j];ax.set_xscale('log');ax.set_xlim(9,650)
            ax.set_xticks([10,100,580]);ax.xaxis.set_major_formatter(FuncFormatter(number))
            ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
            ax.set_xlabel('Search width' if k==1 else '',labelpad=2)
            ax.grid(axis='y',color='#E1E4E8',lw=.45);ax.set_axisbelow(True)
            ax.set_title(f'({string.ascii_lowercase[k*3+j]})  {NAMES[d]}',pad=4)
        for key,c,m in [('db1_checks','#7F8994','s'),('db1_survivors',COLORS[0],'o')]:
            axes[0,j].plot(x,[r[key] for r in rows],color=c,marker=m,markevery=5,
                           markersize=3,markerfacecolor='white',lw=1.3,label=key)
        axes[0,j].set_yscale('log');axes[0,j].set_ylim(200,30000)
        axes[0,j].set_yticks([1000,10000]);axes[0,j].yaxis.set_major_formatter(FuncFormatter(number))
        axes[0,j].yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
        values=[np.array([r[key] for r in rows]) for key in ['full4_page_reads','rerank_page_reads','sectors_4k_per_query']]
        values[2]=values[2]-values[0]-values[1];assert np.all(values[2]>=-1e-6) # floating-point subtraction tolerance; no clipping
        for y,c,m,ls in zip(values,[COLORS[1],COLORS[2],COLORS[3]],['s','^','D'],['-','--',':']):
            axes[1,j].plot(x,y,color=c,marker=m,markevery=5,markersize=3,markerfacecolor='white',lw=1.2,ls=ls)
        axes[1,j].set_ylim(0,4000);axes[1,j].set_yticks([0,2000,4000])
        axes[1,j].yaxis.set_major_formatter(FuncFormatter(number))
        for r,o in zip(rows,values[2]):
            assert 0 <= r['db1_survivors'] <= r['db1_checks']
            records.append(dict(dataset=d,search_width=r['search_width'],db1_checks=r['db1_checks'],
                                db1_survivors=r['db1_survivors'],other_read_pages=float(o)))
    axes[0,0].set_ylabel('Candidates/query');axes[1,0].set_ylabel('4 KiB pages/query')
    top=[Line2D([],[],color=c,marker=m,markerfacecolor='white',lw=1.3,label=l)
         for c,m,l in [('#7F8994','s','DB1 checks'),(COLORS[0],'o','DB1 survivors')]]
    bottom=[Line2D([],[],color=c,marker=m,markerfacecolor='white',ls=ls,lw=1.2,label=l)
            for c,m,ls,l in zip(COLORS[1:4],['s','^','D'],['-','--',':'],['Primary reads','Rerank reads','Other reads'])]
    fig.legend(handles=top,loc='upper center',bbox_to_anchor=(.54,1),ncol=2,frameon=False)
    fig.legend(handles=bottom,loc='upper center',bbox_to_anchor=(.54,.535),ncol=3,frameon=False)
    write_csv('screening_io.csv',records)
    export(fig,'fig04_screening_io')


def main():
    from pypdf import PdfReader,PdfWriter
    (ROOT/'qa').mkdir(exist_ok=True)
    data,audit,missing,allrows=load()
    names=['fig01_throughput','fig02_read_volume','fig03_tail_latency','fig05_full_sweep','fig06_requests']
    view=[]
    for name,field,zoom in zip(names,['qps','read_mib','p95_ms','qps','io_requests_per_query'],[True,True,True,False,True]):
        view+=triptych(data,name,field,zoom)
    screening(data)
    write_csv('viewport_audit.csv',view)
    (ROOT/'qa/data_audit.json').write_text(json.dumps(dict(total_observations=len(allrows),
        artifacts=audit,missing_artifacts=missing,excluded_observations=0,
        markers='Every fifth curve vertex; all vertices retained in lines and CSV',
        uncertainty='Single run per setting; no between-run intervals',
        scope='Locality Ours; acceptance and complete memory accounting pending'),indent=2))
    writer=PdfWriter()
    for name in names[:3]+['fig04_screening_io']+names[3:]:
        writer.add_page(PdfReader(ROOT/(name+'.pdf')).pages[0])
    writer.write(ROOT/'all_figures.pdf')
    print(f'Redrawn six figures from {len(allrows)} observations.')


if __name__=='__main__':main()
