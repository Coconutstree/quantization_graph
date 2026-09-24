"""Accepted paper measurements only; RAM/RSS and cgroup protocols stay distinct."""
import csv
import json
from pathlib import Path

from cgroup_memory import dump


def best(rows, threshold):
    eligible = [r for r in rows if r['recall'] >= threshold]
    return max(eligible, key=lambda r: (r['qps'], -r['width'])) if eligible else None


def collect(out):
    rows, failures = [], []
    for p in sorted((out / 'paper').glob('*/*/L*/status.json')):
        status = json.loads(p.read_text())
        folder = p.parent
        width = int(folder.name[1:])
        budget, method = int(folder.parent.parent.name), folder.parent.name
        if status['classification'] != 'completed':
            failures.append(dict(budget_mib=budget, method=method, width=width,
                                 status=status['classification']))
            continue
        # Verify immutable evidence even when report is invoked independently.
        import hashlib
        for name, expected in status['files'].items():
            with (folder / name).open('rb') as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != expected:
                raise ValueError('modified accepted evidence: ' + str(folder / name))
        result = json.loads((folder / 'result.json').read_text())['summary_rows'][0]
        mem = json.loads((folder / 'memory_measurement.json').read_text())
        if result['query_count'] != 800 or mem['classification'] != 'completed':
            raise ValueError('not an accepted paper measurement')
        mode = json.loads((out/'experiment.json').read_text()).get('memory_mode', 'cgroup')
        if mode == 'rss_budget':
            if (mem.get('scope') != 'planned_process_ram_budget_rss_admission'
                    or mem.get('budget_bytes') != budget*2**20 or mem.get('budget_admitted') is not True
                    or mem.get('memory_enforcement') != 'none' or mem.get('memory_limit_bytes') is not None
                    or not 0 < mem['observed_peak_rss_bytes'] <= budget*2**20
                    or mem.get('sampled_swap_bytes') != 0):
                raise ValueError('wrong RSS budget admission')
        elif (not mem['limits_verified'] or not mem['membership_verified']
              or mem['memory_max_bytes'] != budget*2**20 or mem['swap_max_bytes'] != 0):
            raise ValueError('wrong physical budget')
        trace = [json.loads(l) for l in (folder / 'queries.jsonl').read_text().splitlines()]
        samples = [json.loads(l) for l in (folder / 'rss_samples.jsonl').read_text().splitlines()]
        rss = [s['VmRSS'] for s in samples if s['phase']=='measurement' and 'VmRSS' in s]
        allocation = json.loads((folder / 'memory_plan.json').read_text()) if method=='ours' else None
        rows.append(dict(budget_mib=budget, method=method, width=width, recall=result['recall'],
                         qps=result['qps'], bytes_per_query=sum(t['bytes_read'] for t in trace)/800,
                         reads_per_query=sum(t['io_requests'] for t in trace)/800,
                         pages_4k_per_query=sum(t['sectors_4k'] for t in trace)/800,
                         peak_rss_bytes=mem['observed_peak_rss_bytes'],
                         cgroup_peak_bytes=mem.get('cgroup_peak_bytes'),
                         query_rss_min_bytes=min(rss) if rss else None,
                         query_rss_max_bytes=max(rss) if rss else None,
                         allocation=allocation, source=str(folder.relative_to(out))))
    return rows, failures


def generate(out):
    out = Path(out)
    rows, failures = collect(out)
    dump(out / 'paper_rows.json', rows)
    dump(out / 'paper_failures.json', failures)
    definition = json.loads((out/'experiment.json').read_text()) if (out/'experiment.json').exists() else {}
    budgets = definition.get('budgets_mib', [512,2048,4096,8192])
    rss_mode = definition.get('memory_mode') == 'rss_budget'
    summary = []
    for budget in budgets:
        for method in ('ours','diskann'):
            selected = [r for r in rows if r['budget_mib']==budget and r['method']==method]
            for threshold in (.90,.95,.98):
                row = best(selected, threshold)
                summary.append(dict(budget_mib=budget, method=method, threshold=threshold,
                                    status='measured' if row else ('not_reached' if selected else 'no_accepted_measurement'),
                                    **({k:v for k,v in row.items() if k not in ('budget_mib','method','allocation')} if row else {})))
    dump(out / 'threshold_summary.json', summary)
    keys = ['budget_mib','method','threshold','status','width','recall','qps','bytes_per_query',
            'reads_per_query','pages_4k_per_query','peak_rss_bytes','cgroup_peak_bytes',
            'query_rss_min_bytes','query_rss_max_bytes','source']
    with (out / 'threshold_summary.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=keys);writer.writeheader();writer.writerows(summary)
    lines = ['# GIST — '+('planned RAM budget / observed RSS' if rss_mode else 'same total RAM (cgroup)'), '',
             ('仅包含预算配置＋实测RSS验收的800-query单轮测量；无OS硬限制。' if rss_mode else '仅包含独立cgroup、swap=0的800-query单轮测量。')+' test100和历史结果不进入曲线。', '',
             f'已接受 {len(rows)}/320 个点；已记录 {len(failures)} 个失败点。', '',
             'Peak RSS 为采样 RSS/HWM 与 wait4 最大观测值；请求原生程序保留整进程HWM，仍不承诺捕获不支持该开关的程序的所有瞬时峰值。RSS模式的cgroup peak留空。', '',
             '阈值表选取所有达标 width 中实测 QPS 最大者（相同 QPS 选较小 width），I/O 和内存均来自同一点。单轮结果不提供稳定性或置信区间声明。', '',
             '[阈值摘要 CSV](threshold_summary.csv) · [全部测量点](paper_rows.json) · [失败点](paper_failures.json)', '']
    lines += ['| RAM MiB | Method | Recall ≥ | Width | Actual recall | QPS | Bytes/query | Reads/query | Peak RSS MiB | cgroup peak MiB |',
              '|---|---|---|---|---|---|---|---|---|---|']
    for row in summary:
        if row['status']=='measured':
            lines.append(f"| {row['budget_mib']} | {row['method']} | {row['threshold']:.2f} | {row['width']} | {row['recall']:.5f} | {row['qps']:.2f} | {row['bytes_per_query']:.1f} | {row['reads_per_query']:.2f} | {row['peak_rss_bytes']/2**20:.2f} | {format(row['cgroup_peak_bytes']/2**20, '.2f') if row['cgroup_peak_bytes'] is not None else '—'} |")
        else:
            lines.append(f"| {row['budget_mib']} | {row['method']} | {row['threshold']:.2f} | — | {row['status']} | — | — | — | — | — |")
    lines.append('')
    if rows:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        plt.rcParams.update({'font.size':9, 'pdf.fonttype':42, 'svg.fonttype':'none',
                             'axes.spines.top':False,'axes.spines.right':False})
        figures=out/'figures';figures.mkdir(exist_ok=True)
        for budget in budgets:
            fig,ax=plt.subplots(figsize=(4.8,3.5),layout='constrained')
            available=False
            for method,label,color,marker in [('ours','Ours','#0072B2','o'),('diskann','DiskANN','#D55E00','s')]:
                data=sorted([r for r in rows if r['budget_mib']==budget and r['method']==method],key=lambda r:r['width'])
                if data:
                    available=True
                    ax.plot([r['recall'] for r in data],[r['qps'] for r in data],label=f'{label} ({len(data)}/40)',
                            color=color,marker=marker,markersize=3,linewidth=1.2)
            if available:
                ax.set(xlabel='Recall@10',ylabel='QPS',title=f'GIST · RAM budget = {budget} MiB'+(' (RSS admitted)' if rss_mode else ' (cgroup)'))
                ax.set_yscale('log');ax.grid(axis='y',alpha=.18);ax.legend(frameon=False)
                for ext in ('png','pdf','svg'):
                    fig.savefig(figures/f'recall_qps_{budget}.{ext}',dpi=300)
                lines += [f'![RAM {budget} MiB](figures/recall_qps_{budget}.png)', '']
            plt.close(fig)
    (out/'report.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path)
    generate(parser.parse_args().output)
