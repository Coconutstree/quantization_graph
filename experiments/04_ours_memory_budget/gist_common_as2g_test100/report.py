"""Summarize only accepted curves under the verified common OS ceiling."""
import csv
import importlib.util
import json
from pathlib import Path
import statistics
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OUT=ROOT/'results/04_ours_memory_budget/gist_common_as2g_test100_inflight256';MIB=2**20


def main():
    rows=[]
    for folder in sorted((OUT/'runs').glob('*')):
        if not (folder/'acceptance.json').exists():continue
        x=json.loads((folder/'result.json').read_text());mem=json.loads((folder/'memory_measurement.json').read_text())
        assert mem['rlimit_as_bytes']==2*1024**3 and mem['hard_limit_verified'] and mem['user_address_space_budget_passed']
        samples=list(map(json.loads,(folder/'rss_samples.jsonl').read_text().splitlines()))
        plan=json.loads((folder/'memory_plan.json').read_text()) if (folder/'memory_plan.json').exists() else {}
        assert len(x['summary_rows'])==40
        for r in x['summary_rows']:
            w=r['search_width'];rss=[v['VmRSS']/MIB for v in samples if v['width']==w and v['phase']=='measurement']
            path=folder/f'memory_stats/L{w}.json';cache=json.loads(path.read_text()) if path.exists() else {}
            rows.append(dict(run=folder.name,method=x['method'],width=w,query_count=r['query_count'],recall=r['recall'],qps=r['qps'],
                planning_mib=mem['configured_planning_budget_bytes']/MIB,enforced_as_limit_mib=2048,
                process_peak_rss_mib=mem['kernel_wait4_peak_rss_bytes']/MIB,
                query_rss_median_mib=statistics.median(rss) if rss else None,query_rss_sampled_peak_mib=max(rss) if rss else None,
                query_rss_samples=len(rss),read_pages_per_query=r['sectors_4k_per_query'],read_bytes_per_query=r['bytes_read_per_query'],
                policy=plan.get('mode',x['cache_mode']),codes_resident=plan.get('mode')!='paged',
                routing_capacity_pages=plan.get('routing_capacity_pages',0),cache_reserved_bytes=cache.get('cache_reserved_bytes',x['cache_bytes']),
                static_nodes=cache.get('payload_nodes',x.get('cache_nodes',0)),dynamic_capacity_nodes=(cache.get('dynamic_records') or {}).get('capacity_nodes',0)))
    (OUT/'measured_results.json').write_text(json.dumps(rows,indent=2)+'\n')
    if rows:
        with (OUT/'measured_results.csv').open('w') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        spec=importlib.util.spec_from_file_location('common_plot',HERE.parent/'gist_width40_test100/plot.py')
        plot=importlib.util.module_from_spec(spec);spec.loader.exec_module(plot);plot.OUT=OUT
        plot.main(rows,stem='recall_qps')
    state=json.loads((OUT/'state.json').read_text())
    lines=['# GIST：共同2 GiB地址空间上限', '',f"状态：{state['status']}；已验收 {len(rows)//40}/7 条曲线。当前配置：{state.get('run','—')}。",'',
        '双方每次启动前均设置RLIMIT_AS soft=hard=2147483648字节，并通过prlimit读回确认。该值是虚拟地址空间上限，不是物理内存或RSS上限。', '',
        '完整40 widths × 相同test100，单轮、32 workers、beam1、每width预热100条。双方搜索文件均在进程启动前顺序O_DIRECT预读；设备缓存不可控。未启动800查询。', '',
        'Ours保留538/616分页、resident无记录缓存、768/1024静态热点、1536 hot+dynamic这6个配置。图例MiB表示内存规划额度，所有配置的实际硬上限均为2048 MiB。DiskANN仅c0、PQ常驻；该批次重新测量，不混入旧数据。', '',
        'Peak RSS覆盖整进程；查询RSS为50 ms采样。100条查询在低width可能耗时很短，本轮共同上限不能替代稳定性验证。原始点全部保留、不平滑。', '',
        '[CSV](measured_results.csv) · [实验配置](experiment.json)', '',
        '| 配置 | L | Recall | QPS | Peak RSS MiB | 查询RSS中位数 MiB | 页/查询 |', '|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        if r['width'] not in [100,300,580]:continue
        rss=f"{r['query_rss_median_mib']:.1f}" if r['query_rss_median_mib'] is not None else '无样本'
        lines.append(f"| [{r['run']}](runs/{r['run']}/result.json) | {r['width']} | {r['recall']:.3f} | {r['qps']:.2f} | {r['process_peak_rss_mib']:.1f} | {rss} | {r['read_pages_per_query']:.2f} |")
    if rows:lines+=['','![Recall–QPS](figures/recall_qps.png)','', '[PDF](figures/recall_qps.pdf) · [SVG](figures/recall_qps.svg)']
    if state['status']=='failed':lines+=['',f"失败信息：{state.get('error')}。保留日志，不填零吞吐。"]
    (OUT/'report.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':main()
