"""Render admitted GIST 03/05 measurements without interpolation or filtering."""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = [10, 20, 40, 60, 100, 160, 240, 400, 580]

def load(relative):
    path = ROOT / relative / 'tables/formal_test_rows.csv'
    manifest = json.loads(path.with_suffix('.manifest.json').read_text())
    assert manifest['formal_ready'], path
    rows = list(csv.DictReader(path.open()))
    assert all(r['formal_ready'].lower() == 'true' and r['budget_admitted'].lower() == 'true' for r in rows)
    return path, rows

def render(experiment, sources, group_key, dataset="gist"):
    rows = [r for _, batch in sources for r in batch]
    groups = list(dict.fromkeys(r[group_key] for r in rows))
    for group in groups:
        assert sorted(int(r['search_width']) for r in rows if r[group_key] == group) == WIDTHS
    out = ROOT / f'results/{experiment}/{dataset}/tables'
    out.mkdir(parents=True, exist_ok=True)
    fields = ['method', 'search_dram_budget_gib', 'search_width', 'beam_width', 'recall', 'qps', 'io_requests_per_query', 'sectors_4k_per_query', 'bytes_read_per_query', 'latency_mean_us', 'process_peak_rss_bytes', 'ours_route_mode', 'ours_route_dimension', 'hot_record_nodes', 'dynamic_record_capacity_nodes', 'run_id', 'artifact_path']
    with (out/'recall_qps_summary.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k:r.get(k,'') for k in fields} for r in rows)
    title = '03 GIST 系统对比（共同预算 4 GiB）' if experiment.startswith('03') else '05 GIST Ours 内存预算实验'
    title = title.replace('GIST', dataset.upper())
    text = [f'# {title}', '', '正式 test；每档 800 个查询、32 workers、1 次测量。Recall@10 以百分比表示；QPS 为 queries/s；I/O 为每查询读请求数。未插值、未筛除非 Pareto 点。单次测量不提供误差条。', '', '预算按完整进程峰值 RSS 和采样 swap 验收；不是 cgroup 硬限制。峰值 RSS 是整次九档运行的峰值，不是每个 width 的独立峰值。', '']
    labels = [g if group_key=='method' else f'{float(g):g} GiB' for g in groups]
    text += ['## Recall–QPS 对照表', '', '| width | ' + ' | '.join(f'{label} Recall / QPS' for label in labels) + ' |', '| ---: | ' + ' | '.join('---:' for _ in groups) + ' |']
    for width in WIDTHS:
        cells=[]
        for g in groups:
            r=next(r for r in rows if r[group_key]==g and int(r['search_width'])==width)
            cells.append(f"{float(r['recall'])*100:.2f}% / {float(r['qps']):.2f}")
        text.append(f'| {width} | ' + ' | '.join(cells) + ' |')
    for g,label in zip(groups,labels):
        batch=sorted((r for r in rows if r[group_key]==g),key=lambda r:int(r['search_width']))
        r=batch[0]
        text += ['',f'## {label}', '', f"预算 {float(r['search_dram_budget_gib']):g} GiB；beam={r['beam_width']}；整次运行峰值 RSS={float(r['process_peak_rss_bytes'])/2**30:.3f} GiB。"]
        if r['method']=='Ours-Disk':
            text += ['',f"路由：{r['ours_route_mode']}，{r['ours_route_dimension']} 维；hot records={r['hot_record_nodes']}；dynamic 容量={r['dynamic_record_capacity_nodes']} 条（容量不代表实际已缓存）。"]
        text += ['', '| width | Recall@10 (%) | QPS | I/O/query | 4 KiB sectors/query | 读取 KiB/query | 平均延迟 ms |', '| ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
        for r in batch:
            values=[float(r['recall'])*100,float(r['qps']),float(r['io_requests_per_query']),float(r['sectors_4k_per_query']),float(r['bytes_read_per_query'])/1024,float(r['latency_mean_us'])/1000]
            text.append('| '+r['search_width']+' | '+' | '.join(f'{v:.2f}' for v in values)+' |')
    text += ['', '## 数据来源', '']
    for p,_ in sources:
        text += [f'- `{p.relative_to(ROOT)}`']
    text += ['', 'CSV： [recall_qps_summary.csv](recall_qps_summary.csv)', '']
    (out/'recall_qps_summary.md').write_text('\n'.join(text))
    (out/'recall_qps_summary.sources.json').write_text(json.dumps([{'path':str(p.relative_to(ROOT)), 'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'rows':len(batch)} for p,batch in sources],indent=2)+'\n')
    print(out/'recall_qps_summary.md',len(rows),'rows')

if __name__=='__main__':
    render('03_disk_system',[load('results/03_disk_system/gist/gist_w9_beam4_4g_20260921')],'method')
    render('05_memory_budget',[load(f'results/05_memory_budget/gist/gist_ours_beam4_ram{b}_20260921') for b in [1,2,4,8]],'search_dram_budget_gib')
