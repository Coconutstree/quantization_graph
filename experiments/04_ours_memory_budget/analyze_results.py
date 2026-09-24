"""Generate a source-checked analysis of completed GIST v2 runs; no benchmarks."""
import json
from pathlib import Path
from run import verify_acceptance
from protocol import OUT, ROOT, MODES, WIDTHS

LABEL = dict(zip(MODES, ['基线','page缓存','热点邻接表','热点精细记录','混合缓存','导航小图','导航＋page','导航＋混合','全库精细记录']))

def main():
    data={}; stats={}; memory={}
    for mode in MODES:
        folder=OUT/mode
        verify_acceptance(folder)
        rows=json.loads((folder/'result.json').read_text())['summary_rows']
        assert [r['search_width'] for r in rows]==list(WIDTHS)
        data[mode]={r['search_width']:r for r in rows}
        stats[mode]={w:json.loads((folder/'memory_stats'/f'L{w}.json').read_text()) for w in WIDTHS}
        memory[mode]=json.loads((folder/'memory_measurement.json').read_text())
        assert memory[mode]['user_address_space_budget_passed']
        if not mode.startswith('nav'):
            assert json.loads((folder/'parity.json').read_text())['passed']
    mib=lambda n:n/2**20
    lines=['# GIST 2 GiB 剩余内存实验：结果分析','',
        '## Recall–QPS曲线','', '![各方案Recall–QPS曲线](results/04_ours_memory_budget/v2/figures/gist_recall_qps.png)','', '左图为全召回范围（QPS对数坐标），右图为高召回放大（QPS线性坐标）。紫色Full Records为全库精细记录，橙色Hybrid Cache为混合缓存，蓝色Page Cache为page缓存；虚线表示加入导航。越靠右上越好。全部360个原始点按L连接，无平滑；单次运行不画误差条。','', '[PDF](results/04_ours_memory_budget/v2/figures/gist_recall_qps.pdf) · [SVG](results/04_ours_memory_budget/v2/figures/gist_recall_qps.svg) · [绘图源数据CSV](results/04_ours_memory_budget/v2/figures/recall_qps_source.csv)','', '## 结论','',
        '**本轮没有一个方案同时在所有召回率、吞吐和尾延迟上最优。** 在GIST、2 GiB地址空间上限和既有搜索配置下：', '',
        '- **目标Recall≥0.95：全库精细记录最有优势。** L=100时QPS为82.00，是基线的8.43倍；P99约711 ms。它确实能装入本次预算，因此应作为实际候选方案，而不只是诊断端点。',
        '- **目标Recall≥0.98：混合缓存的吞吐开始占优。** L=220时QPS为41.67，而全库精细记录为40.40；差距仅约3%，不足以凭单次运行认定稳定领先。到L=300和580，混合方案的吞吐优势更明显。',
        '- **单独page缓存是有效的简单方案。** 不需离线热点训练；L=100、300、580时分别达到基线约3.96、7.72、10.80倍QPS，进程地址空间也比混合方案宽裕。',
        '- **热点邻接表单独缓存收益很小；当前导航小图不值得默认加入。** 导航改变入口和召回，增益并不稳定，不能因某一个档位更快就判断总体有效。',
        '- **热点精细记录适合较小的附加内存投入。** 只使用约403 MiB缓存，仍有明显收益；混合方案则利用了约1,274 MiB额度，二者不是等实际占用的比较。','',
        '以上是本数据集、本次执行的结果。只有一次完整扫描，没有独立重复、误差条或显著性检验。','',
        '## 数据与比较口径','',
        '- 数据：GIST，100万条960维底库向量；800条测试查询，200条验证查询用于离线热点统计。',
        '- 固定原40档L、beam=1、workers=32、top10、epsilon=1.9、rerank最多100条，原图和BFS页布局、查询顺序均不变。',
        '- 每个L清空跨查询page缓存，再按原流程预热100条；统计与计时排除预热。静态热点预加载时间单列，不计入QPS。',
        '- 基线与纯缓存方案逐查询核验返回ID、Recall、访问/候选计数；导航方案改变入口，不要求与基线返回ID一致。',
        '- 9条性能曲线×40档，共360行；验证阶段另有40档。分析脚本重新校验各运行的验收文件与输出哈希；未使用旧v1诊断成绩。',
        '- 下文“目标召回”选择所有实测Recall达到阈值的档位中QPS最高者，**不插值，不代表实际Recall完全相同**。表中列出实际Recall和L；选择较高L偶尔更快，可能受运行波动影响。',
        '- P99统一转换为毫秒。内存峰值是整个40档进程的峰值，不是某一档的独立测量。','',
        '## 满足目标召回率时的吞吐与P99','']
    selected={}
    for target in [.90,.95,.98,.985]:
        lines += [f'### Recall ≥ {target:.3f}','', '| 方案 | L | 实测Recall | QPS | 相对基线QPS | P99 ms |','|---|---:|---:|---:|---:|---:|']
        selected[target]={mode:max((r for r in data[mode].values() if r['recall']>=target),key=lambda r:r['qps']) for mode in MODES}
        base=selected[target]['baseline']['qps']
        for mode,r in selected[target].items():
            lines.append(f"| {LABEL[mode]} | {r['search_width']} | {r['recall']:.6f} | {r['qps']:.2f} | {r['qps']/base:.2f}× | {r['latency_p99_us']/1000:.1f} |")
        lines.append('')
    lines += ['在Recall≥0.98时，混合缓存吞吐略高，但P99为2,539 ms；全库精细记录P99为1,388 ms，约低45%。因此吞吐优先和尾延迟优先可能选出不同方案。Recall≥0.985时，全库精细记录的吞吐落后于混合缓存，但其表中P99仍更低。','',
        '## 同L下的缓存收益与交叉点','',
        '纯缓存保持完全一致的查询结果，因此同L比较可以直接观察内存策略的作用：','',
        '| L | Recall | 基线QPS | page QPS | 混合QPS | 全库精细QPS | 混合/全库 |','|---|---:|---:|---:|---:|---:|---:|']
    for w in [30,100,140,180,220,260,300,580]:
        b,p,h,f=[data[m][w] for m in ['baseline','pages','hybrid','full_payload']]
        lines.append(f"| {w} | {b['recall']:.6f} | {b['qps']:.2f} | {p['qps']:.2f} | {h['qps']:.2f} | {f['qps']:.2f} | {h['qps']/f['qps']:.2f}× |")
    lines += ['',
        '实测交叉出现在L=180与220之间，不应把220解释为普适阈值。全库精细记录让compact/residual读取归零，但图仍在磁盘上；L增加后图访问仍增长。混合方案缓存部分精细记录并复用跨查询页，在高L时压低了总读页数。','',
        '## I/O解释：缓存命中不等于等量减少磁盘读取','',
        '以下为L=580，所有数值均为每查询实际提交读取的4 KiB页数；按触发读取的操作归属，不能把三类当作互不影响的独立因果贡献。','',
        '| 方案 | compact触发 | graph触发 | residual触发 | 总页/查询 | 相对同L基线减少 | QPS |','|---|---:|---:|---:|---:|---:|---:|']
    for mode in MODES:
        ops=stats[mode][580]['operations'];r=data[mode][580];total=r['sectors_4k_per_query']
        assert abs(sum(v['read_pages'] for v in ops.values())/800-total)<1e-7
        lines.append(f"| {LABEL[mode]} | {ops['compact']['read_pages']/800:.2f} | {ops['graph']['read_pages']/800:.2f} | {ops['residual']['read_pages']/800:.2f} | {total:.2f} | {(1-total/data['baseline'][580]['sectors_4k_per_query'])*100:.2f}% | {r['qps']:.2f} |")
    lines += ['',
        '**热点邻接表为何收效有限：** 原BFS布局中graph与compact在同一页。缓存graph后，compact仍可能需要该页；L=580时graph触发读取从303.59降至198.16页，但总读页仅减少约2.6%，QPS从2.63变为2.69。该方案缓存90,977个热点节点，不能据此推论“所有内存图缓存都无效”。','',
        '**精细记录缓存为何增加graph触发读取：** 基线通常通过读取compact顺带将合并页带入每查询缓存。直接从内存取得compact后，这个顺带预取不再发生。L=580时热点精细记录的graph触发读取为411.68页，全库精细记录为551.39页，均高于基线303.59页；但compact/residual的减少仍产生净收益。这与当前访问路径一致，不能简单理解为图搜索展开变多。','',
        '**为什么不能只看总页数：** L=580时page缓存读167.06页/查询，少于混合方案189.06页，但QPS分别为28.41和35.31。读取时序、等待时间、热点记录直接命中与缓存管理成本也会影响吞吐；本轮没有CPU剖析，无法量化各项贡献。','',
        '## 内存占用、实际缓存规模与预算余量','',
        f"新基线VmPeak={mib(memory['baseline']['sampled_high_water_bytes']['VmPeak']):.2f} MiB；统一附加额度为2048−709.50−64=**1274.50 MiB**。这是分配计账额度，不能与RSS或实际VmPeak简单相加。",'',
        '| 方案 | 缓存计账 MiB | 峰值RSS MiB | VmPeak MiB | 距2 GiB的VmPeak余量 MiB | 邻接表节点 | 精细记录节点 | page容量 | 初始化秒 |','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for mode in MODES:
        st=stats[mode][580];mem=memory[mode];vm=mib(mem['sampled_high_water_bytes']['VmPeak'])
        lines.append(f"| {LABEL[mode]} | {mib(st['cache_reserved_bytes']):.2f} | {mib(mem['kernel_wait4_peak_rss_bytes']):.2f} | {vm:.2f} | {2048-vm:.2f} | {st['graph_nodes']:,} | {st['payload_nodes']:,} | {st['page_capacity']:,} | {st['setup_seconds']:.3f} |")
    lines += ['',
        '- 混合方案VmPeak约2012.9 MiB，距离上限仅约35.1 MiB；本轮通过硬限制验收，但预先扣除64 MiB不保证最终还剩64 MiB。应把内存压力测试作为采用前的必要验证。',
        '- 热点邻接表仅约33 MiB，热点精细记录约403 MiB，是验证集正分热点覆盖形成的实际规模。未用满额度并非分配失败；混合策略已把静态缓存未用额度转给page。',
        '- 全库精细记录计账约1126.3 MiB，统一额度内还剩约148.2 MiB未用；当前方案没有把余量转给page，这是隔离“全库精细记录”效果的既定实现。',
        '- page容量是可容纳的页数，不是每档实际填满的页数；缓存计账也不等同于实测常驻内存。',
        '- 初始化耗时不含完整验证集训练及离线导航构建，不能把表中秒数当作全流程预处理成本。','',
        '## 导航小图是否值得保留','',
        '当前导航仅有2,048个代表点，16NN加双向环，改变原图入口；结论只针对这个实现。','',
        '| L | 导航/基线QPS | 导航＋page/page QPS | 导航＋混合/混合 QPS | 基线Recall | 导航Recall |','|---|---:|---:|---:|---:|---:|']
    for w in [30,100,300,580]:
        lines.append(f"| {w} | {data['nav'][w]['qps']/data['baseline'][w]['qps']:.3f}× | {data['nav_pages'][w]['qps']/data['pages'][w]['qps']:.3f}× | {data['nav_hybrid'][w]['qps']/data['hybrid'][w]['qps']:.3f}× | {data['baseline'][w]['recall']:.6f} | {data['nav'][w]['recall']:.6f} |")
    lines += ['',
        '增益随L变化，部分档位反而变慢；导航还会轻微改变召回。目标Recall≥0.985时导航较早达到阈值，因此需要较低L，表中优势包含这个因素。本轮不足以证明稳定增益，更不能推广到HNSW等其他导航实现。','',
        '## 结论边界与下一步','',
        '1. 本轮只有GIST及固定查询顺序，各方案按队列顺序单次执行；没有随机化运行顺序或多次重复。硬件负载、设备缓存及FIFO并发插入顺序可能影响QPS，小幅差距应视为待复验。O_DIRECT并不意味着设备处于受控冷态。',
        '2. 2 GiB约束是RLIMIT_AS用户进程地址空间上限，未覆盖内核/设备缓存；输入、线程栈、分配器与进程内其他组件均计入程序自身预算。所有方案通过本轮验收，不代表内存余量对所有运行条件都充足。',
        '3. 热点和导航均只用验证查询或底库生成；测试曲线上选择最快配置属于本轮事后比较，若要给出固定部署配置，还应在独立查询上验证。',
        '4. 下一轮优先复验page、混合、全库精细记录三条曲线，重复运行并交错顺序；重点检查约0.95、0.98、0.985召回附近的QPS、P99与VmPeak。',
        '5. 值得新增的消融是“全库精细记录＋余量page”以及“热点精细记录＋page、不缓存邻接表”。前者利用约148 MiB剩余额度，后者检验混合方案中的邻接表是否必要。二者目前均未实现或运行，不能从本轮数据声称会更快。',
        '6. 若必须立即选候选：约0.95召回优先全库精细记录；高召回且吞吐优先考虑混合；实现简单和地址空间余量优先考虑page。尾延迟优先则单独比较P99，不直接跟随QPS排名。','',
        '## 结果来源与复现','',
        '- [完整实验配置与360行结果](ours_memory_budget_tests.md)',
        '- [原始运行结果目录](results/04_ours_memory_budget/v2/)',
        '- [机器可读分析表](results/04_ours_memory_budget/v2/analysis.json)',
        '- [分析生成脚本](experiments/04_ours_memory_budget/analyze_results.py)',
        '', '```bash','python experiments/04_ours_memory_budget/analyze_results.py','```','',
        '脚本仅读取并校验已完成结果，不启动性能测试。表格来自各方案result.json、memory_stats/L*.json、memory_measurement.json与验收证据；解释性判断已与直接测量区分。','']
    if (OUT/'performance_review.json').exists():lines[2:2]=['**性能异常待核查：本轮baseline在L=100仅9.73 QPS，历史同配置为109.30 QPS。进一步ABBA复测发现历史原程序在当前环境也仅约10 QPS，主要等待存储I/O完成；历史环境为何更快仍未确定。下文加速倍数和最优方案判断暂不推广，仅保留本轮存储条件下的观察。详见[基线异常核查](ours_memory_budget_baseline_audit.md)。**','']
    (ROOT/'ours_memory_budget_analysis.md').write_text('\n'.join(lines))
    (OUT/'analysis.json').write_text(json.dumps({'selection':'max observed QPS with measured recall >= target; no interpolation','targets':selected,'runs':{m:{'memory':memory[m],'L580_stats':stats[m][580]}for m in MODES},'repeats':1},indent=2))
    print('Wrote ours_memory_budget_analysis.md; verified 9 accepted runs / 360 rows.')

if __name__=='__main__':main()
