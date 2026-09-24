"""Export existing measurements without treating historical runs as paired trials."""
import csv
import os
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'results/04_ours_memory_budget/routing_paged_optimized'
TARGET = ROOT / 'results/04_ours_memory_budget/gist_routing_diagnostics'

HISTORY = ROOT / 'results/archive/03_disk_system_unadmitted_20260921/gist/test_L_400_w_32'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    dest = TARGET / 'raw/Ours-Routing-Paged-Optimized'
    # Verify source acceptance before copying; never replace differing artifacts.
    for p in (SOURCE / 'gist/runs').glob('*/acceptance.json'):
        a = json.loads(p.read_text())
        assert a['passed']
        for name, sha in a['files'].items():
            assert digest(p.parent / name) == sha
    artifacts = list((SOURCE / 'gist').rglob('*'))
    copied = {}
    for src in artifacts:
        if not src.is_file():
            continue
        dst = dest / src.relative_to(SOURCE / 'gist')
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            assert digest(dst) == digest(src), f'Conflicting artifact: {dst}'
        else:
            shutil.copy2(src, dst)
        copied[str(dst.relative_to(TARGET))] = digest(dst)
    for name in ['build.json', 'validation/checks.json', 'validation/final_audit.json']:
        src, dst = SOURCE / name, dest / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            assert digest(dst) == digest(src)
        else:
            shutil.copy2(src, dst)
        copied[str(dst.relative_to(TARGET))] = digest(dst)

    rows = []
    methods = ['Ours-Disk', 'DiskANN-PQ-Disk', 'SymphonyQG-DiskPort',
               'OG-LVQ-DiskPort', 'Glass-NSG-DiskPort', 'Starling-Disk']
    for method in methods:
        path = HISTORY / 'raw' / method / 'result.json'
        if method == 'Starling-Disk':
            path = path.parent / 'diagnostics/w32_as4g_20260917/result.json'
        result = json.loads(path.read_text())
        for r in result['summary_rows']:
            rows.append(dict(method=method, cohort='historical_4GiB_diagnostic' if method == 'Starling-Disk' else 'historical_800',
                             L=r['search_width'], recall=r['recall'], qps=r['qps'],
                             queries=r['query_count'], repeats=1, workers=result.get('workers',32),
                             budget_MiB=result['search_dram_budget_gib']*1024,
                             p95_ms=None if r.get('latency_p95_us') is None else r['latency_p95_us']/1000,
                             p99_ms=None if r.get('latency_p99_us') is None else r['latency_p99_us']/1000,
                             source=os.path.relpath(path, TARGET)))
    groups = {}
    for folder in sorted((dest / 'runs').iterdir()):
        a = json.loads((folder / 'acceptance.json').read_text())
        b = json.loads((folder / 'budget.json').read_text())
        for r in json.loads((folder / 'result.json').read_text())['summary_rows']:
            key = (b['mode'], b['fraction'], r['search_width'])
            groups.setdefault(key, []).append((folder, a, r))
    for (mode, fraction, width), members in groups.items():
        label = 'Ours-Resident (同期)' if mode == 'resident' else f'Ours-Paged-{mode.upper()} {fraction}%'
        count = sum(r['query_count'] for _, _, r in members)
        latency = []
        for folder, _, _ in members:
            for line in (folder / 'queries.jsonl').read_text().splitlines():
                r = json.loads(line)
                if r['search_width'] == width:
                    latency.append(r['latency_us']/1000)
        latency.sort()
        assert len(latency) == count
        def quantile(q):
            k = (len(latency)-1)*q
            i = int(k)
            return latency[i]+(latency[min(i+1,len(latency)-1)]-latency[i])*(k-i)
        rows.append(dict(method=label,cohort='optimized_100_L100' if width==100 else 'optimized_100_regression',
                         L=width, recall=sum(r['recall']*r['query_count'] for _,_,r in members)/count,
                         qps=count/sum(r['query_count']/r['qps'] for _,_,r in members),
                         queries=count,repeats=len(members),workers=32,budget_MiB=members[0][1]['budget']/2**20,
                         p95_ms=quantile(.95),p99_ms=quantile(.99),
                         source=';'.join(str((f/'result.json').relative_to(TARGET)) for f,_,_ in members)))
    table = TARGET / 'tables'; table.mkdir(exist_ok=True)
    with (table/'routing_paged_comparison.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (table/'routing_paged_comparison.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
    lines=['# GIST：分页优化版与现有方法的速度对照','',
           '这是已有测量的并列参考，不是相同条件下重新运行的公平排名。历史原始结果与 manifest 不变。',
           '', '## 条件差异','',
           '- 历史方法：每档 800 条查询；分页实验：每轮 100 条查询。均为 workers32，召回随方法和查询集不同。',
           '- L100 分页及同期常驻为两轮，QPS 按总查询数／总时间合并；L300/580 为一次正确性回归，不用其耗时评价优化收益。',
           '- 历史存储协议与新版 O_DIRECT 顺序预读协议不同；设备缓存未受控。历史 Ours 与同期常驻 Ours 分开列出。',
           '- Starling 是 4 GiB 诊断结果，原文件标记 throughput_comparable=false；其余历史方法及同期常驻为各自记录预算。',
           '- 缓存比例是 routing sidecar 页比例，不是进程总内存比例。不同方法同 L 不代表相同召回。',
           '- 目录名 L_400 不代表本表搜索 L=400：历史实际搜索档位包括 380、420，没有 400；这里只对齐现有共同档位，不插值。',
           '', '## 共同档位','']
    for width in [100,300,580]:
        lines += [f'### L={width}','', '| 方法 | 实验组 | Recall@10 | QPS | 查询总数/轮数 | 预算 MiB |', '|---|---|---:|---:|---:|---:|']
        for r in rows:
            if r['L']==width:
                lines.append(f"| {r['method']} | {r['cohort']} | {r['recall']:.4f} | {r['qps']:.2f} | {r['queries']}/{r['repeats']} | {r['budget_MiB']:.1f} |")
        lines.append('')
    lines += ['完整历史档位和 P95/P99 见 [CSV](routing_paged_comparison.csv)；新版尾延迟由逐查询 trace 合并计算。',
              '分页原始结果副本：[Ours-Routing-Paged-Optimized](../raw/Ours-Routing-Paged-Optimized/)。',
              '旧版分页 v1 与新版 CLOCK 的同期同预算 L100 对照可用于本次优化分析；跨历史实验不计算加速比。']
    (table/'routing_paged_comparison.md').write_text('\n'.join(lines)+'\n')
    (dest/'import_manifest.json').write_text(json.dumps(dict(source=str(SOURCE/'gist'),
        comparison_scope='diagnostic_cross_protocol_reference',files=copied),indent=2)+'\n')
    (TARGET/'routing_paged_comparison.md').write_text('# GIST 分页速度对照\n\n[打开完整对照表](tables/routing_paged_comparison.md)\n\n包含历史各方法、原版 Ours、同期常驻 Ours、分页 v1、LRU 与 CLOCK。查询规模、预算和存储协议差异已在表中说明；不作为公平排名。\n')
    print(f'Exported {len(rows)} comparison rows and {len(copied)} verified artifacts to {TARGET}')


if __name__ == '__main__':
    main()
