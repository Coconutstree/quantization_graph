"""FIG-CACHE-05: admitted 0.5 GiB cache allocation Recall-QPS comparison."""
from pathlib import Path
import argparse
import csv
import json
import os
os.environ.setdefault('MPLCONFIGDIR','/tmp/qgraph-cache-figure-mpl')


def draw(out, budget=0.5):
    from run_ours_cache_allocation import audit_result, compare_paths, sha256, write
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    out=Path(out)
    state=json.loads((out/'status.json').read_text())
    widths=[60,100,180]
    if state['widths'] != widths: raise ValueError('figure requires the three declared widths')
    pair=next(p for p in state['result_comparisons'] if p['budget_gib']==budget)
    path=Path(pair['path'])
    if pair['status']!='passed' or sha256(path)!=pair['sha256']: raise ValueError('result comparison evidence missing or changed')
    proof=json.loads(path.read_text())
    for side in ('left','right'):
        if sha256(Path(proof[side]))!=proof[side+'_sha256']:raise ValueError('comparison source changed')
    sources=[]; data={}; rows=[];identity=None
    for strategy in ('graph_first','records_only'):
        jobs=[j for j in state['jobs'] if j['budget_gib']==budget and j['strategy']==strategy and j['round']==0 and j['status']=='admitted']
        if len(jobs)!=1 or jobs[0]['status']!='admitted': raise ValueError('figure requires both admitted strategies')
        job=jobs[0];artifact=Path(job['artifact'])
        if sha256(artifact)!=job['artifact_sha256']: raise ValueError('artifact changed')
        a=audit_result(artifact)
        current=(a['native_binary_sha256'],a['ours_route_plan'],a['pca_assets_sha256'],a['ours_hot_ranks_sha256'])
        if identity is None:identity=current
        elif identity!=current:raise ValueError('paired representation or binary differs')
        points=sorted(a['summary_rows'],key=lambda r:r['search_width'])
        if [r['search_width'] for r in points]!=widths or any(r['query_count']!=800 for r in points):
            raise ValueError('incomplete three-width test800 result')
        data[strategy]=points
        sources.append(dict(strategy=strategy,path=str(artifact),sha256=sha256(artifact)))
        for r in points:
            rows.append(dict(strategy=strategy,width=r['search_width'],recall=r['recall'],qps=r['qps'],
                peak_rss_bytes=r['peak_rss_bytes'],source=str(artifact),source_sha256=sha256(artifact)))
    dest=out/'figures';dest.mkdir(exist_ok=True)
    stem=f'recall_qps_b{budget}'
    with (dest/(stem+'.csv')).open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'svg.fonttype':'none','pdf.fonttype':42,
                         'axes.spines.top':False,'axes.spines.right':False})
    fig,ax=plt.subplots(figsize=(4.2,3.2),layout='constrained')
    for strategy,label,color,marker,style in [('graph_first','Graph cache first','#0072B2','o','-'),
                                             ('records_only','Hot + dynamic only','#D55E00','s','--')]:
        points=data[strategy]
        ax.plot([r['recall'] for r in points],[r['qps'] for r in points],label=label,color=color,
                marker=marker,linestyle=style,linewidth=1.4,markersize=5,markerfacecolor='white')
        for r in points:
            ax.annotate(str(r['search_width']),(r['recall'],r['qps']),xytext=(4,5 if strategy=='graph_first' else -12),
                        textcoords='offset points',fontsize=8,color=color)
    ax.xaxis.set_major_formatter(PercentFormatter(1,decimals=0))
    ax.set(xlabel='Recall@10',ylabel='Throughput (queries/s)',title=f'GIST · {budget} GiB · PCA128',ylim=(0,None))
    ax.margins(x=.12,y=.22);ax.grid(axis='y',alpha=.2);ax.legend(frameon=False,fontsize=8)
    for ext in ('svg','pdf','png'):fig.savefig(dest/(stem+'.'+ext),dpi=300)
    plt.close(fig)
    caption=('GIST, 0.5 GiB, resident PCA128 1-bit, 32 workers, beam=4. Labels show search widths 60/100/180; '
             'each point uses 800 test queries, one run. Lines connect measured configurations, without interpolation claims. '
             'Both strategies passed RSS and correctness admission and ordered-result/count comparison. Expansion paths were not checked at user request. '
             'No repeated-run uncertainty is available. Reference/diagnostic timings are excluded.')
    (dest/(stem+'.caption.md')).write_text(caption+'\n')
    write(dest/(stem+'.provenance.json'),dict(figure_id='FIG-CACHE-05',sources=sources,result_evidence=pair,expansion_paths_checked=False,
         data_sha256=sha256(dest/(stem+'.csv')),script_sha256=sha256(Path(__file__)),
         files={ext:sha256(dest/(stem+'.'+ext)) for ext in ('svg','pdf','png')},caption=caption,
         purpose='Compare measured Recall-QPS at fixed resident representation under two cache allocations',
         qa=dict(data_and_admission='passed',encoding='linear QPS, explicit widths, distinct colors/markers',
                 visual_review='pending',publication_ready=False)))
    return dest/(stem+'.svg')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('queue',type=Path)
    a=p.parse_args();print(draw(a.queue))
