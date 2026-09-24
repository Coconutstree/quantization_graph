"""Pool both repetitions without mixing cold-start and warm measurements."""
import csv,json
from pathlib import Path
from build import ROOT
OUT=ROOT/'results/04_ours_memory_budget/routing_paged'

def main():
    raw=json.loads((OUT/'summary.json').read_text());pooled=[]
    for dataset in ['gist','agnews']:
        for temperature in ['cold','warm']:
            for width in [100,300,580]:
                for mode in ['resident','p25','p75']:
                    rr=[r for r in raw if r['dataset']==dataset and r['L']==width and r['run'] in [f'{temperature}_{mode}_r1',f'{temperature}_{mode}_r2']]
                    if len(rr)!=2:continue
                    assert rr[0]['recall']==rr[1]['recall']
                    pooled.append(dict(dataset=dataset,temperature=temperature,mode=mode,L=width,recall=rr[0]['recall'],qps=2/sum(1/r['qps'] for r in rr),qps_min=min(r['qps'] for r in rr),qps_max=max(r['qps'] for r in rr),pages_per_query=sum(r['total_pages_per_query'] for r in rr)/2,routing_pages_per_query=sum((r['routing'] or {}).get('reads',0) for r in rr)/200,routing_wait_ms_per_query=sum((r['routing'] or {}).get('wait_ns',0) for r in rr)/200/1e6,peak_VmPeak=max(r['VmPeak'] for r in rr),peak_RSS=max(r['RSS'] for r in rr)))
    (OUT/'pooled.json').write_text(json.dumps(pooled,indent=2)+'\n')
    if pooled:
        with (OUT/'pooled.csv').open('w') as stream:
            writer=csv.DictWriter(stream,fieldnames=pooled[0]);writer.writeheader();writer.writerows(pooled)
    lines=['# 低内存 routing 异步分页试跑','',f'状态：{"completed" if len(pooled)==36 else "incomplete"}；已合并 {len(pooled)}/36 个配置点。','',
           'GIST / AG News；从既有 800 条测试查询顺序中取前 100 条，保存原 ID 映射；L100/300/580、beam1、workers32、两轮。',
           'cold：每档 L 从空 routing cache 开始测量整批查询；warm：同批 100 查询预热。设备缓存不受控，两者不能解释为冷 SSD 与热 SSD。',
           '常驻对照使用 2 GiB；分页组按预算公式设置较低 RLIMIT_AS，缓存约覆盖 sidecar 页数的 25% / 75%。这是容量扩展与代价测量，不是同预算算法排名。',
           '每点 QPS 按两轮合计查询数／合计时间汇总；每轮 P95/P99、内存、routing 命中／合并／读页计数见 summary.json 和原始目录。',
           '', '| 数据集 | 缓存状态 | 模式 | L | Recall | 合并 QPS | routing 页/查询 | routing 等待 ms/查询 | VmPeak MiB |',
           '|---|---|---|---:|---:|---:|---:|---:|---:|']
    for r in pooled:lines.append(f"| {r['dataset']} | {r['temperature']} | {r['mode']} | {r['L']} | {r['recall']:.6f} | {r['qps']:.2f} | {r['routing_pages_per_query']:.2f} | {r['routing_wait_ms_per_query']:.2f} | {r['peak_VmPeak']/1024**2:.2f} |")
    lines += ['', '验收：逐查询结果 ID、召回、访问、距离计算、DB1 检查／幸存、精算和重排候选计数一致；routing 物理 I/O 与总 I/O 差值一致；进程内存不超硬预算；全局在途和页占用不超额度。',
              '', '实验脚本：`experiments/04_ours_memory_budget/routing_paged/`。', '源代码快照与二进制：`work/ours_memory_budget/routing_paged/`。',
              '结果：`results/04_ours_memory_budget/routing_paged/{gist,agnews}/runs/`；每组保存 command、逐查询 trace、内存测量、预算、routing 统计及 acceptance。',
              '未完成或开发阶段运行存入 development_before_wait_telemetry，不参与本表。', '', '独立测试包括：页边界、尾页、1 页缓存并发、在途合并、固定页保护、文件截断、反序完成、部分提交失败以及四种查询精度的逐位评分一致性。']
    body='\n'.join(lines)+'\n';(ROOT/'ours_routing_paged_tests.md').write_text(body);(OUT/'report.md').write_text(body)
if __name__=='__main__':main()
