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
DISPLAY = ['ExRaBitQ-Disk (locality)', 'DiskANN-PQ', 'SymphonyQG disk port', 'OG-LVQ disk port', 'Glass-NSG disk port']
COLORS = ['#C44E52', '#4C78A8', '#8172B3', '#46968C', '#7F8994']
MARKERS = ['o', 's', '^', 'D', 'v']
STYLES = ['-', '--', '-.', ':', (0, (5, 2))]
plt.rcParams.update({
    'font.family': 'sans-serif', 'font.sans-serif': ['DejaVu Sans'],
    'font.size': 7, 'axes.labelsize': 7, 'axes.titlesize': 8,
    'xtick.labelsize': 7, 'ytick.labelsize': 7, 'legend.fontsize': 7,
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
    if x>=1000:return f'{x/1000:g}k'
    return f'{x:g}'


def log_axis(ax):
    ax.set_yscale('log')
    ax.yaxis.set_major_locator(LogLocator(base=10,numticks=5))
    ax.yaxis.set_major_formatter(FuncFormatter(number))
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.grid(axis='y',which='major',color='#E5E8EB',linewidth=.45)
    ax.set_axisbelow(True)


def new_grid(legend):
    fig,axes=plt.subplots(2,3,figsize=(183/25.4,126/25.4),sharex='col')
    fig.subplots_adjust(left=.095,right=.985,bottom=.115,top=.805,wspace=.36,hspace=.43)
    fig.legend(handles=legend,loc='upper center',bbox_to_anchor=(.52,.992),ncol=3,
               frameon=False,handlelength=2.2,columnspacing=1.4,labelspacing=.8)
    for i,ax in enumerate(axes.flat):
        ax.text(-.15,1.075,string.ascii_lowercase[i],transform=ax.transAxes,fontweight='bold',fontsize=8)
    return fig,axes


def save(fig, name, axes):
    fig.canvas.draw()
    # Final-size glyph floor and canvas geometry; PDF audit is run separately.
    for text in fig.findobj(matplotlib.text.Text):
        if text.get_text() and text.get_visible():assert text.get_fontsize()>=5
    for ext in ['pdf','svg']:
        fig.savefig(ROOT / f'{name}.{ext}')
    fig.savefig(ROOT/f'{name}.png',dpi=220)
    fig.savefig(ROOT/f'{name}.tiff',dpi=600,pil_kwargs={'compression':'tiff_lzw'})
    # Each crop is produced by the same Python figure, for panel-by-panel QA.
    for i,ax in enumerate(axes.flat):
        bbox=ax.get_tightbbox(fig.canvas.get_renderer()).transformed(fig.dpi_scale_trans.inverted()).expanded(1.03,1.08)
        fig.savefig(ROOT/'qa'/f'{name}_{string.ascii_lowercase[i]}.png',dpi=180,bbox_inches=bbox)
    plt.close(fig)


def comparison(data,name,fields,labels,zoom):
    legend=[Line2D([],[],color=c,marker=m,linestyle=s,lw=1.6 if i==0 else 1,
                   markersize=3,label=l) for i,(l,c,m,s) in enumerate(zip(DISPLAY,COLORS,MARKERS,STYLES))]
    fig,axes=new_grid(legend)
    viewport=[]
    for col,d in enumerate(DATASETS):
        present=sum((d,m) in data for m in METHODS)
        axes[0,col].set_title(NAMES[d]+(' (2/5 systems)' if present<5 else ''),pad=8)
        for row,(field,label) in enumerate(zip(fields,labels)):
            ax=axes[row,col];log_axis(ax)
            values=[]
            for i,m in enumerate(METHODS):
                points=data.get((d,m),[])
                if not points:continue
                x=[p['recall'] for p in points];y=[p[field] for p in points]
                # Preserve search-width order, including nonmonotone recall.
                ax.plot(x,y,color=COLORS[i],marker=MARKERS[i],linestyle=STYLES[i],
                        markersize=2.3,markeredgewidth=.5,lw=1.65 if i==0 else 1,
                        zorder=10 if i==0 else 3)
                visible=[p for p in points if not zoom or p['recall']>=.9]
                values.extend(p[field] for p in visible)
                viewport.append({'figure':name,'panel':string.ascii_lowercase[row*3+col],
                                 'dataset':d,'method':m,'field':field,'total':len(points),
                                 'in_window':len(visible),'outside_window':len(points)-len(visible)})
            if values:
                lo,hi=min(values),max(values)
                ax.set_ylim(lo/1.5,hi*1.6)
            ax.set_xlim(.9 if zoom else 0,1.002)
            ax.set_xticks([.9,.95,1] if zoom else [0,.25,.5,.75,1])
            ax.xaxis.set_major_formatter(FuncFormatter(lambda x,pos:f'{x:.2f}'))
            ax.set_ylabel(label)
            if row==1:ax.set_xlabel('Recall@10')
    fig.text(.5,.036,'32 workers · C0 · one measurement/setting · locality Ours · acceptance pending',
             ha='center',fontsize=6.5,color='#4C535B')
    save(fig,name,axes)
    return viewport


def screening(data):
    legend=[Line2D([],[],color='#7F8994',marker='s',lw=1,markersize=3,label='DB1 checks'),
            Line2D([],[],color='#C44E52',marker='o',lw=1.6,markersize=3,label='DB1 survivors'),
            Line2D([],[],color='#4C78A8',lw=1.1,marker='s',markersize=3,label='Primary-request pages'),
            Line2D([],[],color='#8172B3',lw=1.1,marker='^',markersize=3,label='Rerank-request pages'),
            Line2D([],[],color='#46968C',lw=1.1,marker='D',markersize=3,label='Other read pages')]
    fig,axes=new_grid(legend)
    records=[]
    for col,d in enumerate(DATASETS):
        rows=data[d,'Ours-Disk'];width=np.array([r['search_width'] for r in rows])
        axes[0,col].set_title(NAMES[d],pad=8)
        for row in range(2):
            ax=axes[row,col];ax.set_xscale('log');ax.set_xlim(9,650)
            ax.set_xticks([10,30,100,300,580]);ax.xaxis.set_major_formatter(FuncFormatter(number))
            ax.xaxis.set_minor_formatter(NullFormatter());ax.grid(axis='y',color='#E5E8EB',lw=.45)
        log_axis(axes[0,col])
        axes[0,col].set_ylabel('Candidates / query')
        for key,color,marker,lw in [('db1_checks','#7F8994','s',1),('db1_survivors','#C44E52','o',1.6)]:
            axes[0,col].plot(width,[r[key] for r in rows],color=color,marker=marker,markersize=2.3,lw=lw)
        values={k:np.array([r[k] for r in rows]) for k in ['sectors_4k_per_query','full4_page_reads','rerank_page_reads']}
        other=values['sectors_4k_per_query']-values['full4_page_reads']-values['rerank_page_reads']
        assert np.all(other>=-1e-6), (d,other.min())
        for value,color,marker,style in [(values['full4_page_reads'],'#4C78A8','s','-'),
                                        (values['rerank_page_reads'],'#8172B3','^','--'),
                                        (other,'#46968C','D',':')]:
            axes[1,col].plot(width,value,color=color,marker=marker,markersize=2.3,lw=1.1,linestyle=style)
        axes[1,col].set_ylim(bottom=0);axes[1,col].set_xlabel('Search width')
        axes[1,col].set_ylabel('4 KiB pages / query')
        axes[1,col].yaxis.set_major_formatter(FuncFormatter(number))
        for r,v in zip(rows,other):
            assert 0<=r['db1_survivors']<=r['db1_checks']
            records.append({'dataset':d,'search_width':r['search_width'],'recall':r['recall'],
                            'db1_checks':r['db1_checks'],'db1_survivors':r['db1_survivors'],
                            'survivor_fraction':r['db1_survivors']/r['db1_checks'],
                            'primary_request_pages':r['full4_page_reads'],
                            'rerank_request_pages':r['rerank_page_reads'],'other_read_pages':float(v)})
    fig.text(.5,.036,'ExRaBitQ-Disk locality layout · descriptive counters; no gate-only control · acceptance pending',
             ha='center',fontsize=6.5,color='#4C535B')
    write_csv('screening_io.csv',records)
    save(fig,'fig04_screening_io',axes)


def main():
    (ROOT/'qa').mkdir(exist_ok=True)
    data,audit,missing,allrows=load()
    view=[]
    view+=comparison(data,'fig01_tradeoff_high_recall',['qps','read_mib'],['Throughput (QPS)','Read volume (MiB / query)'],True)
    view+=comparison(data,'fig02_tradeoff_full_sweep',['qps','read_mib'],['Throughput (QPS)','Read volume (MiB / query)'],False)
    view+=comparison(data,'fig03_latency_requests',['p95_ms','io_requests_per_query'],['p95 latency (ms)','I/O requests / query'],True)
    screening(data)
    write_csv('viewport_audit.csv',view)
    report={'total_observations':len(allrows),'excluded_observations':0,'artifacts':audit,
            'missing_artifacts':missing+[{'dataset':'sift10m','method':m,'status':'unmeasured'} for m in METHODS],
            'data_filter':'Only current top-level completed result artifacts; no archive/reference/preflight duplicates',
            'uncertainty':'One measurement per point; no between-run intervals available',
            'cropping':'High-recall views use x>=0.90; full view and CSV retain every row',
            'line_order':'Consecutive search width, no smoothing or interpolation',
            'formal_status':'Acceptance pending in supplied artifacts; not promoted by plotting',
            'log_axes':'All plotted values validated finite and strictly positive; screening page counts use linear y',
            'exports':'PDF/SVG at 183 x 126 mm; TIFF 600 dpi; PNG 220 dpi'}
    (ROOT/'qa/data_audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print(f'Generated 4 figures from {len(allrows)} observations; no source rows excluded.')


if __name__=='__main__':main()
