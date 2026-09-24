"""Generate auditable partial/final tables from accepted runs only."""
import csv
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'results/04_ours_memory_budget/gist_joint_optimization'


def read(p): return json.loads(p.read_text())


def main():
    rows=[]
    for split in ['tune','test']:
        for p in sorted((OUT/split).glob('*/acceptance.json')):
            folder=p.parent
            config=read(folder/'config.json')
            result=read(folder/'result.json')
            route=read(folder/'routing_stats.json')
            mem=read(folder/'memory_measurement.json')
            plan=read(folder/'memory_plan.json')
            trace=[json.loads(line) for line in (folder/'queries.jsonl').read_text().splitlines()]
            for summary in result['summary_rows']:
                w=summary['search_width'];q=summary['query_count']
                r=route.get(str(w),{});cache=read(folder/'memory_stats'/f'L{w}.json')
                requests=r.get('requests',0)
                queried=[x for x in trace if x['search_width']==w]
                qhits=sum(x['query_cache_hits'] for x in queried)
                qmisses=sum(x['query_cache_misses'] for x in queried)
                rows.append(dict(split=split,run=folder.name,L=w,queries=q,recall=summary['recall'],qps=summary['qps'],
                    route_pages_q=r.get('reads',0)/q,record_graph_pages_q=summary['sectors_4k_per_query']-r.get('reads',0)/q,
                    route_io_fraction=r.get('reads',0)/(q*summary['sectors_4k_per_query']),
                    route_hit_fraction=r.get('hits',0)/requests if requests else None,
                    route_merge_fraction=r.get('merges',0)/requests if requests else None,
                    record_hits_q=cache['record_hits']/q,
                    query_page_cache_hits_q=qhits/q,query_page_cache_misses_q=qmisses/q,
                    query_page_cache_hit_fraction=qhits/(qhits+qmisses) if qhits+qmisses else None,
                    route_wait_ms_q=r.get('wait_ns',0)/q/1e6,route_lock_ms_q=r.get('lock_wait_ns',0)/q/1e6,
                    pages_per_submit=r.get('submitted_pages',0)/r['submit_calls'] if r.get('submit_calls') else None,
                    lock_calls_q=r.get('lock_calls',0)/q,
                    p95_ms=summary['latency_p95_us']/1000,
                    peak_rss_mib=mem['kernel_wait4_peak_rss_bytes']/2**20,vmpeak_mib=mem['sampled_high_water_bytes']['VmPeak']/2**20,
                    budget_mib=config['budget_mib'],record_reserved_mib=cache['cache_reserved_bytes']/2**20,
                    static_records=cache['payload_nodes'],dynamic_capacity=(cache.get('dynamic_records') or {}).get('capacity_nodes',0),
                    route_capacity=plan['routing_capacity_pages'],source=str((folder/'result.json').relative_to(OUT))))
    if not rows: return
    with (OUT/'measured_results.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    lines=['# GIST 联合缓存与分页优化验证','',
        '**状态：进行中；本报告自动收录通过验收的组，尚未完成全部确认实验。**','',
        '固定完整960维codes、原图/布局、L100、beam1、32 workers。主矩阵640 MiB整进程RLIMIT_AS；L前缀补充组为538 MiB。页内缓存固定4 MiB/worker；分页页槽默认16 MiB，分配扫描另设额度，factors常驻。训练/调参使用原validation的互斥train/tune各100查询，热点来自train，测试确认另列。每组预热100查询，测量不计预热。', '',
        '主机已识别的MSMARCO建图任务在整批运行期间临时暂停，结束自动恢复；其他共享负载及设备缓存不能保证完全可控。使用相同O_DIRECT预读规则，不声称冷缓存。诊断目录中的并发smoke不纳入性能表。', '',
        '只收录硬内存上限、VmPeak计划、逐查询结果/搜索计数、物理I/O计账通过的结果。分页+记录缓存同时启用检查实际缓存节点与命中；名称hot_dynamic不意味着动态容量非零。', '',
        '|split / run|L|Recall|QPS|route页/q|record+graph页/q|route hit %|record hits/q|route wait ms/q|lock ms/q|页/submit|RSS MiB|',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        hit='—' if r['route_hit_fraction'] is None else f"{r['route_hit_fraction']*100:.2f}"
        batch='—' if r['pages_per_submit'] is None else f"{r['pages_per_submit']:.2f}"
        lines.append(f"|[{r['split']}/{r['run']}]({r['source']})|{r['L']}|{r['recall']:.3f}|{r['qps']:.2f}|{r['route_pages_q']:.2f}|{r['record_graph_pages_q']:.2f}|{hit}|{r['record_hits_q']:.2f}|{r['route_wait_ms_q']:.2f}|{r['route_lock_ms_q']:.2f}|{batch}|{r['peak_rss_mib']:.2f}|")
    lines += ['', 'route hit=hits/requests；merge单列于CSV；等待和锁时间为多线程累计后除查询数，不能直接与批次墙钟相加。route I/O占比按物理页计，非耗时占比。', '',
        '[逐轮完整CSV](measured_results.csv) · [冻结矩阵](experiment.json) · [构建来源](build.json)', '',
        '待完成：两轮反序矩阵、调度瓶颈归因、独立test确认、最终效果分类与适用边界。']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n')
    print(f'{len(rows)} accepted rows')


if __name__=='__main__': main()
