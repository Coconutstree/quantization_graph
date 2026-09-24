"""Report only accepted complete 40-width runs; retain all raw measurements."""
import csv
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT/'results/04_ours_memory_budget/gist_width40_test100'
MIB = 2**20


def main():
    primary_names = set(json.loads((OUT/'selection.json').read_text())['primary_runs'])
    rows = []
    for folder in sorted((OUT/'runs').glob('*')):
        if not (folder/'acceptance.json').exists():
            continue
        result = json.loads((folder/'result.json').read_text())
        memory = json.loads((folder/'memory_measurement.json').read_text())
        samples = [json.loads(line) for line in (folder/'rss_samples.jsonl').read_text().splitlines()]
        plan = json.loads((folder/'memory_plan.json').read_text()) if (folder/'memory_plan.json').exists() else {}
        for summary in result['summary_rows']:
            width = summary['search_width']
            rss = [v['VmRSS']/MIB for v in samples if v['width'] == width and v['phase'] == 'measurement']
            cachefile = folder/'memory_stats'/f'L{width}.json'
            cache = json.loads(cachefile.read_text()) if cachefile.exists() else {}
            rows.append(dict(run=folder.name, role='primary' if folder.name in primary_names else 'supplementary', method=result['method'], width=width, queries=summary['query_count'],
                recall=summary['recall'], qps=summary['qps'],
                process_peak_rss_mib=memory['kernel_wait4_peak_rss_bytes']/MIB,
                query_rss_median_mib=statistics.median(rss) if rss else None,
                query_rss_sampled_peak_mib=max(rss) if rss else None,
                query_rss_samples=len(rss), read_pages_per_query=summary['sectors_4k_per_query'],
                read_bytes_per_query=summary['bytes_read_per_query'],
                configured_budget_mib=result['search_dram_budget_gib']*1024,
                enforced_as_limit_mib=memory['rlimit_as_bytes']/MIB if memory['rlimit_as_bytes'] else None,
                policy=plan.get('mode',result['cache_mode']), codes_resident=plan.get('mode')!='paged',
                factors_resident=plan.get('factors_resident'),
                routing_capacity_pages=plan.get('routing_capacity_pages',0),
                cache_reserved_bytes=cache.get('cache_reserved_bytes',result['cache_bytes']),
                dynamic_capacity_nodes=(cache.get('dynamic_records') or {}).get('capacity_nodes',0),
                cache_nodes=result.get('cache_nodes',0), static_payload_nodes=cache.get('payload_nodes',0), raw_result=str((folder/'result.json').relative_to(OUT))))
    (OUT/'measured_results.json').write_text(json.dumps(rows, indent=2)+'\n')
    if rows:
        with (OUT/'measured_results.csv').open('w') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    primary = [r for r in rows if r['role']=='primary']
    supplementary = [r for r in rows if r['role']=='supplementary']
    state = json.loads((OUT/'state.json').read_text()) if (OUT/'state.json').exists() else {'status':'prepared'}
    text = ['# GIST 40 widths × test100：实测内存—性能', '',
        f"状态：{state['status']}；主配置已验收 {len(primary)//40}/{len(primary_names)} 条完整曲线（6条Ours＋1条已完成DiskANN参考）；另保留 {len(supplementary)//40} 条补充曲线。", '',
        '**后续诊断已完成：**旧DiskANN二进制＋原800查询＋2 GiB硬限制、无额外预读的独立L100诊断为112.97 QPS，未复现历史25.54。两组800诊断与历史800逐查询结果及读页全部一致；设备状态、运行时段和完整width扫描上下文仍未控制，不能唯一归因。见[诊断报告](../gist_diskann_protocol_audit/report.md)与[正式协议](../gist_diskann_protocol_audit/formal_protocol.md)。正式40-width × 800-query批次未启动。', '',
        '**跨批次性能比较待核查：**本轮DiskANN相对历史800-query结果出现明显提速，不能把两批曲线差异直接归因于算法或内存优化，也未证明任一批QPS计算错误。历史与当前二进制、查询规模、存储预处理和DiskANN硬限制不同。详细证据见[历史性能核对](validation/historical_performance_audit.json)。', '',
        '以L100作为诊断点（不是等Recall比较）：Ours无记录缓存旧109.30、本轮102.07 QPS；DiskANN旧25.54、本轮158.00 QPS。同100条查询映射核对后，两方法各4000条记录的结果ID、Recall、访问计数和读页数均与旧记录一致。DiskANN这100条查询的平均I/O等待从1219.58 ms降为153.29 ms，明显差异存在于实际计时记录，不只是汇总Recall的查询子集变化。尚未通过控制变量实验确定原因。', '',
        '本轮每档100条预热覆盖了全部100条测量查询；旧800-query实验仅覆盖前100条。历史DiskANN为fixed_query_warmup_v1，本轮增加整索引顺序O_DIRECT预读；设备缓存不可控。旧DiskANN有2 GiB地址空间硬限制，本轮仅配置额度。保留全部原始曲线，不基于预期排名修正或替换QPS；800-query新实验尚未启动。', '',
        '32 workers、beam1、每档预热100查询、测量100条相同test查询；每个配置仅运行一轮。所有实验串行。800 queries未启用。', '',
        'Ours沿用固定factors常驻的自动策略及冻结二进制；主配置为538/616 MiB分页、resident无记录缓存参考、768/1024/1536 MiB记录缓存；依据见[选点说明](selection.json)。DiskANN仅复用本轮已完成的PQ常驻c0曲线，不再新增缓存档。Ours硬限制是RLIMIT_AS（地址空间）；DiskANN额度仅用于原生缓存规划。横向比较使用实测RSS，不能把配置额度视为实测内存或相同硬上限。', '',
        '**hot+dynamic是否启用：**1536 MiB主配置及2048 MiB补充配置均启用了静态热点＋动态记录缓存，且有实际动态命中。768/1024 MiB虽然策略名为hot_dynamic，但静态热点优先使用额度后，动态容量为0，实际只有静态热点缓存。resident是刻意保留的无记录缓存对照，不代表优化后的Ours。', '',
        '**Recall核对：**按原查询ID映射，将本轮100条查询与历史hot_dynamic的800查询记录逐项对齐；resident、768、1024、1536、2048五个配置各4000条记录的结果ID、Recall及访问/候选计数全部一致。见[逐查询核对](validation/hot_dynamic_source_parity.json)。本轮L100/300/580的Recall为0.944/0.986/0.988；历史全文档为800查询统计，不能直接用两者的均值差判断实现变化。不同批次QPS仍受查询规模和运行时段影响。', '',
        'Peak RSS来自wait4，覆盖整个进程；查询RSS为原生日志测量阶段标记之间50 ms采样的VmRSS，存在边界采样误差，短批次可能无样本。整进程峰值在每个width行重复列出，不是该width的独立峰值。读页/字节是查询阶段实际提交读取，不含预热。每组预先顺序O_DIRECT读取搜索文件，设备缓存未控制。', '',
        '历史DiskANN基线已有40 widths × 800 queries，单独保留：[原始结果](../../archive/03_disk_system_unadmitted_20260921/gist/test_L_400_w_32/raw/DiskANN-PQ-Disk/result.json)。本页曲线为test100，不将历史test800混入。', '',
        '[全部逐width CSV](measured_results.csv) · [JSON](measured_results.json) · [实验配置](experiment.json)', '',
        '| 配置 / 用途 | Peak RSS MiB | L10 Recall / QPS | L580 Recall / QPS |',
        '|---|---:|---:|---:|']
    for name in sorted({r['run'] for r in rows}):
        group = [r for r in rows if r['run']==name]; first,last=group[0],group[-1]
        text.append(f"| [{name}](runs/{name}/result.json) / {first['role']} | {first['process_peak_rss_mib']:.1f} | {first['recall']:.3f} / {first['qps']:.2f} | {last['recall']:.3f} / {last['qps']:.2f} |")
    if rows:
        text.extend(['', '## 常用width的实测性能（主配置）', '',
            '| 配置 | L | Recall@10 | QPS | 读页/查询 |', '|---|---:|---:|---:|---:|'])
        for row in primary:
            if row['width'] in [100,300,580]:
                text.append(f"| {row['run']} | {row['width']} | {row['recall']:.3f} | {row['qps']:.2f} | {row['read_pages_per_query']:.2f} |")
    if rows:
        text.extend(['', '## 配置与查询内存（L300）', '',
            '| 配置 | codes常驻 | routing页槽 | 记录/共享缓存 MiB | 静态节点 / 动态容量 | 查询RSS中位数 MiB | 读页/查询 |',
            '|---|---|---:|---:|---:|---:|---:|'])
        for row in rows:
            if row['width'] != 300: continue
            rss = f"{row['query_rss_median_mib']:.1f}" if row['query_rss_median_mib'] is not None else '无样本'
            static = row['static_payload_nodes'] if row['method']=='Ours-Disk' else row['cache_nodes']
            text.append(f"| {row['run']} | {row['codes_resident']} | {row['routing_capacity_pages']} | {row['cache_reserved_bytes']/MIB:.2f} | {static} / {row['dynamic_capacity_nodes']} | {rss} | {row['read_pages_per_query']:.2f} |")
    if rows:
        import plot
        plot.main(primary)
        if supplementary:
            plot.main(supplementary, stem='supplementary_curves')
            text.extend(['', '[全部补充曲线](figures/supplementary_curves.png)。补充数据不混入代表配置主图，完整CSV保留所有已验收点并标记role。'])
        text.extend(['', '![Recall–QPS曲线](figures/memory_curves.png)', '', '各条线保留全部40个width；单轮观测，不提供重复实验误差条。纵轴为对数刻度；图例中的MiB是配置额度，不是实测RSS。未完成配置不填零。'])
    if state['status']=='failed': text.extend(['',f"停止原因：`{state['error']}`。保留失败日志，不补零性能。"])
    audit = audit_rows(primary)
    (OUT/'stability_audit.json').write_text(json.dumps(audit, indent=2)+'\n')
    text.extend(['', '## test100检查', '', f"完整曲线：{audit['complete_curves']}/{len(primary_names)}。每配置一轮，不进行跨轮稳定性评估。", f"沿width的Recall下降点：{len(audit['recall_decreases'])}。", f"查询阶段RSS无样本点：{len(audit['missing_query_rss'])}；详细数据见[采样与趋势检查](stability_audit.json)。800查询需先检查完整结果后再安排。"])
    (OUT/'report.md').write_text('\n'.join(text)+'\n')


def audit_rows(rows):
    audit = dict(complete_curves=len(rows)//40, paired_width_points=0,
        repeat_recall_differences=[], recall_decreases=[], rss_repeat_differences_over_5pct=[],
        qps_repeat_differences_over_20pct=[], missing_query_rss=[])
    lookup = {(r['run'],r['width']):r for r in rows}
    for name in sorted({r['run'] for r in rows}):
        group = sorted([r for r in rows if r['run']==name],key=lambda r:r['width'])
        for a,b in zip(group,group[1:]):
            if b['recall'] < a['recall'] - 1e-9:
                audit['recall_decreases'].append(dict(run=name,from_width=a['width'],to_width=b['width'],delta=b['recall']-a['recall']))
    for row in rows:
        if row['query_rss_median_mib'] is None:
            audit['missing_query_rss'].append([row['run'],row['width']])
        if not row['run'].endswith('_r1'): continue
        second=lookup.get((row['run'][:-1]+'2',row['width']))
        if second is None: continue
        audit['paired_width_points']+=1
        point=dict(config=row['run'][:-3],width=row['width'])
        if abs(row['recall']-second['recall'])>1e-9:
            audit['repeat_recall_differences'].append(point)
        for key,threshold,target in [('query_rss_median_mib',.05,'rss_repeat_differences_over_5pct'),('qps',.20,'qps_repeat_differences_over_20pct')]:
            a,b=row[key],second[key]
            if a is not None and b is not None:
                relative=abs(a-b)/((a+b)/2)
                if relative>threshold: audit[target].append(dict(**point,relative_difference=relative,round1=a,round2=b))
    return audit


if __name__=='__main__': main()
