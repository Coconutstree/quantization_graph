"""Completion gate and effect classification, using independent test results."""
import csv
import json
import statistics
from pathlib import Path
import report

OUT=report.OUT


def main():
    report.main()
    for stage in ['validation','allocation','scheduler','confirmation','low_budget']:
        assert report.read(OUT/f'{stage}_completed.json')['passed'],stage
    rows=list(csv.DictReader((OUT/'measured_results.csv').open()))
    def group(split,name):
        found=[r for r in rows if r['split']==split and r['run'] in [name+'_r1',name+'_r2']]
        assert len(found)==2,(split,name)
        return sorted(found,key=lambda r:r['run'])
    def harmonic(rs):return len(rs)/sum(1/float(r['qps']) for r in rs)
    def avg(rs,key):return statistics.mean(float(r[key]) for r in rs)
    pairs=[
        ('常驻增加8 MiB记录缓存','T0_resident_r0','T1_resident_r8'),
        ('同640 MiB：常驻+8 vs 分页+96','T1_resident_r8','T3_paged_p16_r96'),
        ('固定112 MiB可选额度：route优先 vs record优先','T2_paged_p112_r0','T3_paged_p16_r96'),
        ('固定分页16 MiB：记录缓存8→96 MiB','T4_paged_p16_r8','T3_paged_p16_r96'),
        ('固定页槽保护10%热点','T4_paged_p16_r8','T5_paged_hot10_r8'),
        ('validation选择的共享I/O并发','T4_paged_p16_r8','T6_paged_scheduler_r8'),
    ]
    comparisons=[]
    for label,base,candidate in pairs:
        a,b=group('test',base),group('test',candidate)
        assert {r['recall'] for r in a+b}=={a[0]['recall']}
        gain=harmonic(b)/harmonic(a)-1
        paired=[float(y['qps'])/float(x['qps'])-1 for x,y in zip(a,b)]
        classification='明确吞吐收益（本配置）' if gain>=.05 and min(paired)>0 else '明显退化（本配置）' if gain<=-.05 and max(paired)<0 else '小幅或不稳定，未证实明确吞吐收益'
        comparisons.append(dict(comparison=label,baseline=base,candidate=candidate,baseline_qps=harmonic(a),candidate_qps=harmonic(b),gain=gain,paired_gains=paired,classification=classification,
            baseline_route_pages_q=avg(a,'route_pages_q'),candidate_route_pages_q=avg(b,'route_pages_q'),baseline_nonroute_pages_q=avg(a,'record_graph_pages_q'),candidate_nonroute_pages_q=avg(b,'record_graph_pages_q')))
    (OUT/'comparisons.json').write_text(json.dumps(comparisons,ensure_ascii=False,indent=2)+'\n')
    # Preserve full accepted-row report separately from the concise final analysis.
    (OUT/'all_runs.md').write_text((OUT/'report.md').read_text().replace('**状态：进行中；本报告自动收录通过验收的组，尚未完成全部确认实验。**','**状态：所列验证与独立test确认已完成。**').replace('待完成：两轮反序矩阵、调度瓶颈归因、独立test确认、最终效果分类与适用边界。','效果分析见 report.md。'))
    lock=report.read(OUT/'confirmation_lock.json')
    lines=['# GIST 低内存优化验证报告','',
        '已完成联合分配、热点保护、缓存策略和共享I/O并发扫描，并在独立800条test上确认。完整960维route codes、原图与精算保持不变。主矩阵为L100、beam1、32 workers、640 MiB整进程地址空间硬上限；另有明确标注的538 MiB补充组。不泛化为其他width或数据集的结论。','',
        '## 独立test效果','',
        'QPS为两轮等查询数的调和均值。正值表示候选比基线快；逐轮方向也必须一致，才把≥5%的提升归类为明确收益。此门槛是实用判据，不是统计显著性检验。','',
        '|对照（基线→候选）|基线QPS|候选QPS|变化|判定|',
        '|---|---:|---:|---:|---|']
    for c in comparisons:
        lines.append(f"|{c['comparison']}|{c['baseline_qps']:.2f}|{c['candidate_qps']:.2f}|{c['gain']:+.1%}|{c['classification']}|")
    lines += ['', '## 实际建议', '']
    if comparisons[1]['gain']<0 and comparisons[2]['gain']<0:
        lines += ['本次GIST配置下，内存应先保证route常驻或较大的route页缓存，再分配记录缓存。联合分配入口已可用，但“少放route页、腾出大记录缓存”没有胜出；不应仅为低内存曲线更平滑而改为记录缓存优先。', '']
    scheduler_effect=comparisons[-1]
    if scheduler_effect['classification'].startswith('明确'):
        limit=lock['configs']['T6_paged_scheduler_r8']['inflight']
        lines += [f'可保留的调度候选是共享在途上限{limit}页：相对64页，独立test的QPS变化为{scheduler_effect["gain"]:+.1%}，且两轮同向。先在相同硬件、32 workers的分页分支使用；服务并发不等于每worker候选窗口，窗口和搜索算法仍保持原值。', '']
        base,candidate=group('test','T4_paged_p16_r8'),group('test','T6_paged_scheduler_r8')
        lines += [f"对应route读页变化仅{avg(candidate,'route_pages_q')/avg(base,'route_pages_q')-1:+.2%}；每次提交从{avg(base,'pages_per_submit'):.2f}页增至{avg(candidate,'pages_per_submit'):.2f}页，route累计等待/查询下降{1-avg(candidate,'route_wait_ms_q')/avg(base,'route_wait_ms_q'):.1%}。这些观测支持分页服务并发/批量效率是可优化瓶颈。锁等待也从{avg(base,'route_lock_ms_q'):.2f}增至{avg(candidate,'route_lock_ms_q'):.2f} ms/查询，说明提高并发并未消除锁竞争；本轮没有据此宣称锁分片已有效。", '']
    else:
        lines += ['共享在途上限扫描尚未在独立test达到预设的稳定收益门槛，保留64页默认值；不把validation最高点直接升级成默认策略。', '']
    hot_effect=comparisons[-2]
    lines += [f'10%热点页保护的判定：{hot_effect["classification"]}（test QPS {hot_effect["gain"]:+.1%}）。该结论针对本次训练量、热点比例和缓存容量，不说明所有热点缓存策略都无效。', '']
    lines += ['',f"调度候选在validation上锁定为 `{lock['selected_scheduler']}`，锁定后才运行test。所有test组均使用同一scheduler二进制，64页基线同版本复测。",'',
        '输入审计：train100、tune100与test之间无相同向量内容。原test的800行包含798个不同向量值；完整保留800行、原查询ID与顺序，没有为提高吞吐而去重。','',
        '## I/O与内存证据','',
        '|test配置|Recall|route页/q|record+graph页/q|route hit %|route wait ms/q|锁等待 ms/q|页/submit|RSS MiB范围|VmPeak MiB范围|',
        '|---|---:|---:|---:|---:|---:|---:|---:|---|---|']
    for name in lock['configs']:
        rs=group('test',name)
        hit='—' if not rs[0]['route_hit_fraction'] else f"{100*avg(rs,'route_hit_fraction'):.2f}"
        batch='—' if not rs[0]['pages_per_submit'] else f"{avg(rs,'pages_per_submit'):.2f}"
        ranges=lambda k:'–'.join(f'{v:.2f}' for v in [min(float(r[k]) for r in rs),max(float(r[k]) for r in rs)])
        lines.append(f"|{name}|{float(rs[0]['recall']):.6f}|{avg(rs,'route_pages_q'):.2f}|{avg(rs,'record_graph_pages_q'):.2f}|{hit}|{avg(rs,'route_wait_ms_q'):.2f}|{avg(rs,'route_lock_ms_q'):.2f}|{batch}|{ranges('peak_rss_mib')}|{ranges('vmpeak_mib')}|")
    lines += ['',
        'route hit=hits/requests，inflight合并不算命中；record+graph包含compact/residual/graph。route读页占比不等于耗时占比。等待与锁时间是线程累计值/查询数，不可直接与批次墙钟相加。相同RLIMIT_AS不代表相同实际RSS。实际静态节点、动态容量和记录命中见CSV。','',
        '## 完整validation扫描','',
        '|配置|两轮QPS|route页/q|record+graph页/q|',
        '|---|---:|---:|---:|']
    names=sorted({r['run'].rsplit('_r',1)[0] for r in rows if r['split']=='tune'})
    for name in names:
        rs=group('tune',name)
        lines.append(f"|{name}|{harmonic(rs):.2f}|{avg(rs,'route_pages_q'):.2f}|{avg(rs,'record_graph_pages_q'):.2f}|")
    low=group('tune','L0_538_p16_r0')
    lines += ['', '## 538 MiB低预算补充', '',
        '本节使用tune100，不冒充独立test确认；L前缀组均为538 MiB，其余validation组640 MiB。640 MiB所选调度上限直接用于本节，不再根据此处重新选参数。538 MiB低于继承账本的完整codes常驻准入阈值，这不证明显式常驻在该预算下必然OOM；本轮未降低fixed或安全预留来改变准入。', '',
        '|相对L0分页16 MiB页槽、无记录缓存|QPS变化|route页/q变化|record+graph页/q变化|',
        '|---|---:|---:|---:|']
    for name in ['L1_538_p8_r8','L2_538_hot10','L3_538_scheduler']:
        rs=group('tune',name)
        lines.append(f"|{name}|{harmonic(rs)/harmonic(low)-1:+.1%}|{avg(rs,'route_pages_q')-avg(low,'route_pages_q'):+.2f}|{avg(rs,'record_graph_pages_q')-avg(low,'record_graph_pages_q'):+.2f}|")
    lines += ['', '## 实施与验证边界','',
        '- 新入口允许route-page与record两项额度同时生效；10项真实入口边界测试覆盖超预算、记录映射最低额度、最小页槽和64/256页在途上限，另有4项原生分页回归。每组实际记录节点/命中、页容量、物理I/O总账、RLIMIT_AS、VmPeak通过验收。','- 所有组逐查询ID、Recall、访问、剪枝和精算计数与同输入常驻参考一致；变化来自系统执行，不是以Recall换QPS。','- 热点训练使用互斥的validation train100，评估使用tune100；test800只用于锁定后确认。每组100查询预热，统一O_DIRECT预读，设备缓存不可完全控制。','- 固定112 MiB可选额度的四种分配避免只比较一个任意的小route缓存；未使用预算不会被自动转给其他缓存。','- 已有批量页排序/去重、跨worker在途合并、服务批量提交均保留；本轮调度验证改变的是共享在途并发，不将这些已有机制宣传为新增优化。','- 锁分片、预测预取、sidecar压缩和降维没有实现A/B，不能称为已验证有效。保持960维符合本轮目标；这些方向应由本轮读页/等待证据决定是否继续。','- 当前631.1438 MiB为继承账本的完整codes准入阈值，不是本轮测得的普适性能下限。640 MiB比较不能独自确定精确转折点。','- 两轮不足以消除共享环境噪声；MSMARCO建图已按日志暂停/恢复，但不承诺宿主机完全独占。没有执行800×40 widths全曲线。','',
        '## 复现与原始证据','',
        '[协议](protocol.md) · [逐轮表](all_runs.md) · [CSV](measured_results.csv) · [效果计算](comparisons.json) · [确认配置锁](confirmation_lock.json) · [边界测试](preflight_tests/results.json) · [256页边界](scheduler_preflight_tests/results.json) · [原生回归](routing_regression.log) · [最终审计](final_audit.json) · [建图暂停/恢复](competing_build.json)','',
        '入口：`experiments/04_ours_memory_budget/gist_joint_optimization/`。依次prepare、run --validation、run --allocation、prepare_scheduler、run --scheduler、run --confirm、run --low-budget、finalize、audit；已验收目录按哈希复用，失败目录不会静默重跑。pipeline自动完成validation之后的测量、routing回归和报告生成，audit另作最终核验。']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n')
    print('Final report generated; inspect before claiming completion.')


if __name__=='__main__':main()
