"""Audit saved auto-policy pilot results; does not launch or change benchmarks."""
import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'results/04_ours_memory_budget/gist_memory_auto_policy'
OUT = ROOT / 'results/04_ours_memory_budget/gist_low_memory_diagnosis'


def read(path):
    return json.loads(path.read_text())


def main():
    rows = []
    with (SOURCE / 'measured_results.csv').open() as stream:
        saved = list(csv.DictReader(stream))
    for row in saved:
        if row['split'] != 'pilot' or row['reference'].lower() == 'true':
            continue
        folder = (SOURCE / row['source']).parent
        width, count = int(row['L']), int(row['queries'])
        result = read(folder / 'result.json')
        summary = next(s for s in result['summary_rows'] if s['search_width'] == width)
        routing = read(folder / 'routing_stats.json').get(str(width), {})
        stats = read(folder / 'memory_stats' / f'L{width}.json')
        trace = [json.loads(line) for line in (folder / 'queries.jsonl').read_text().splitlines()]
        trace = [q for q in trace if q['search_width'] == width]
        assert len(trace) == count == summary['query_count']
        total = summary['sectors_4k_per_query'] * count
        route = routing.get('reads', 0)
        assert abs(route - float(row['routing_reads'])) < 1e-6
        assert routing.get('bytes', 0) == route * 4096
        # Residual includes graph/record reads; do not label it pure payload I/O.
        nonroute = sum(v['read_pages'] for v in stats['operations'].values())
        assert abs(total - route - nonroute) < 1e-4
        requests = routing.get('requests', 0)
        if requests:
            assert requests == route + routing['hits'] + routing['merges']
        hits = sum(q['query_cache_hits'] for q in trace)
        misses = sum(q['query_cache_misses'] for q in trace)
        rows.append(dict(
            run=row['run'], budget_mib=float(row['budget_mib']), L=width,
            repeat=int(row['repeat']), queries=count, mode=row['mode'],
            recall=summary['recall'], qps=summary['qps'],
            route_pages_per_query=route/count,
            nonroute_pages_per_query=nonroute/count,
            route_io_fraction=route/total if total else None,
            route_hit_fraction=routing.get('hits', 0)/requests if requests else None,
            route_merge_fraction=routing.get('merges', 0)/requests if requests else None,
            query_page_cache_hit_fraction=hits/(hits+misses) if hits+misses else None,
            record_hits_per_query=stats['record_hits']/count,
            record_cache_reserved_mib=float(row['cache_reserved_mib']),
            routing_capacity_pages=int(row['routing_capacity_pages']),
            source=str((folder / 'result.json').relative_to(ROOT)),
        ))
    assert rows
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'diagnosis.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    groups = defaultdict(list)
    for row in rows:
        groups[row['budget_mib'], row['L']].append(row)
    lines = [
        '# GIST 低内存退化：已存数据诊断', '',
        '本报告仅审计 auto-policy 的既有 test100 pilot；没有启动新实验或修改算法。',
        'cache-reuse 的 validation100 数据存在并发影响且校准版本不同，不与这里合并。', '',
        '## 指标口径', '',
        '- route I/O 占比按物理 4 KiB 页计算：routing reads / (sectors per query × query count)。原报告 routing reads 是整批计数，不能直接除以每查询总页数。',
        '- non-route 包括 graph、compact、residual；逐组验证它与 route 页数之和等于总页数。I/O 页占比不等于耗时占比。',
        '- route hit = hits / requests；merge 单列，不算缓存命中。常驻没有 route 请求，命中率记为空，不记 100%。',
        '- query page cache 命中率由逐查询 hits/(hits+misses) 汇总；record 仅报每查询命中次数，未把不同层缓存的分母混用。',
        '- 表中两轮 QPS 为调和均值，页数、route 占比及 route hit 为等查询数两轮算术均值；完整逐轮指标见 diagnosis.csv。', '',
        '## 固定 L=100 的观测', '',
        '|预算 MiB|模式|Recall|QPS|route 页/query|non-route 页/query|route I/O %|route hit %|',
        '|---:|---|---:|---:|---:|---:|---:|---:|',
    ]
    for (budget, width), group in sorted(groups.items()):
        assert len(group) == 2
        assert len({r['recall'] for r in group}) == 1
        if width != 100:
            continue
        avg = lambda k: sum(r[k] for r in group)/len(group)
        hit = '—' if group[0]['route_hit_fraction'] is None else f"{100*avg('route_hit_fraction'):.2f}"
        qps = len(group)/sum(1/r['qps'] for r in group)
        lines.append(f"|{budget:g}|{group[0]['mode']}|{group[0]['recall']:.3f}|{qps:.2f}|{avg('route_pages_per_query'):.2f}|{avg('nonroute_pages_per_query'):.2f}|{100*avg('route_io_fraction'):.2f}|{hit}|")
    lines += ['', '## 能支持什么结论', '',
        '1. 低预算存在很大的 route 分页读放大。优先定位该项，但页占比本身不能证明全部 QPS 损失均由 SSD 服务时间造成；还需区分等待、合并、锁和排队。',
        '2. 常驻后记录缓存增大伴随 non-route I/O 减少，说明缓存分配值得对照。当前分页分支 optional=0，因此尚未比较同预算的“常驻＋小记录缓存”和“分页＋大记录缓存”，不能断言常驻优先最优。',
        '3. 当前各预算固定完整 codes；既有报告还验收逐查询 ID、访问、剪枝、精算一致。对本轮预算变化，没有路由质量下降证据；这不回答 256/128 维量化质量。',
        '4. 631.1438 MiB 是当前校准版本的 codes 常驻准入阈值，634.9596 MiB 是记录缓存启动准入阈值。它们不是实测最低内存，也不是已独立定位的性能临界点；不能与 cache-reuse 的 562.1438 MiB 跨版本拼接。', '',
        '下一步矩阵及尚缺能力见 [matrix.md](matrix.md)。', '',
        '复现：`python experiments/04_ours_memory_budget/gist_low_memory_diagnosis.py`',
    ]
    (OUT / 'report.md').write_text('\n'.join(lines) + '\n')
    print(f'Audited {len(rows)} rows; wrote {OUT}')


if __name__ == '__main__':
    main()
