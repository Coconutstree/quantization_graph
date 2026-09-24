"""Build a report only from accepted runs; separate observations and design."""
import csv
import json
from statistics import mean, harmonic_mean
from policy import ROOT, OUT, MIB, calibrated_plan, dump


def read(path):
    return json.loads(path.read_text())


def main():
    rows = []
    for accepted in sorted(OUT.glob('*/*/acceptance.json')):
        folder = accepted.parent
        selection = read(folder / 'adaptive_selection.json')
        plan = read(folder / 'memory_plan.json')
        memory = read(folder / 'memory_measurement.json')
        for row in read(folder / 'result.json')['summary_rows']:
            stats = read(folder / 'memory_stats' / f'L{row["search_width"]}.json')
            rows.append(dict(split=folder.parent.name, run=folder.name, budget_mib=plan['budget_bytes'] / MIB,
                 dim=selection['dimension'], padded_bits=selection['padded_dimension'], M=selection['M'],
                 width=row['search_width'], recall=row['recall'], qps=row['qps'],
                 route_pages_q=0, total_pages_q=row['sectors_4k_per_query'],
                 full4_pages_q=row['full4_page_reads'], full4_candidates_q=row['full4_candidates'],
                 record_hits_q=stats['record_hits'] / row['query_count'],
                 query_prep_us=row['query_prep_us'], record_cache_mib=plan['optional_cache_bytes'] / MIB,
                 admission_mib=plan['admission_bytes'] / MIB,
                 vmpeak_mib=memory['sampled_high_water_bytes']['VmPeak'] / MIB,
                 source=str((folder / 'result.json').relative_to(OUT))))
    with (OUT / 'measured_results.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    plans = {str(b): calibrated_plan(b) for b in (525, 538, 542, 550, 580, 632)}
    dump(OUT / 'memory_ledger.json', plans)
    test = [x for x in rows if x['split'] == 'test']
    assert len(test) == 2 and len({(x['dim'], x['M'], x['width']) for x in test}) == 1
    effect = dict(dim=test[0]['dim'], M=test[0]['M'], width=test[0]['width'], repeats=len(test),
                  recall=mean(x['recall'] for x in test), qps=harmonic_mean(x['qps'] for x in test),
                  total_pages_q=mean(x['total_pages_q'] for x in test), route_pages_q=0,
                  full4_candidates_q=mean(x['full4_candidates_q'] for x in test))
    baseline = next(x for x in read(ROOT / 'results/04_ours_memory_budget/gist_pca_routing/effects.json')
                    if x['cohort'] == 'matched_baseline' and x['dim'] == 960)
    effect['historical_baseline'] = baseline
    effect['historical_qps_change'] = effect['qps'] / baseline['qps'] - 1
    effect['historical_io_change'] = effect['total_pages_q'] / baseline['total_pages_q'] - 1
    dump(OUT / 'effects.json', effect)
    lines = ['# GIST：预算内最高维度的常驻 1-bit routing', '',
        '第一阶段已实现并验证：按照完整准入账本选择最高可常驻整数维度；538 MiB 自动选择 128 维，542 MiB 自动选择 255 维。低维路径没有启用 hard prune。第二阶段完成同一 4-bit 分数的下界推导和精确算术参考，尚未接入 native 搜索。', '',
        '## 实际筛选条件与 Recall', '',
        '每次扩展：低维 1-bit 分数排序 → 前 M 个新邻居进入原始完整 4-bit 验证 → 按 4-bit 距离维护 width 大小的候选池。未入选的节点允许从其他边重访；低维分数不与 τ 比较。M=32/64 是近似 shortlist，仍可能影响 Recall。', '',
        f'当前 538 MiB validation 在 M=32/64、width=100/180/260 中选择 M={effect["M"]}、width={effect["width"]}。独立 test800 两轮 Recall 均为 {effect["recall"]:.6f}，QPS 调和均值 {effect["qps"]:.2f}，总 I/O {effect["total_pages_q"]:.2f} 个 4 KiB 页/query，route I/O=0。', '',
        f'与此前冻结的原始 960 维分页基线比较：test Recall 同为 {baseline["recall"]:.6f}，总 I/O 从 {baseline["total_pages_q"]:.2f} 降到 {effect["total_pages_q"]:.2f}（{effect["historical_io_change"]:+.1%}）。历史 QPS 为 {baseline["qps"]:.2f}，本次为 {effect["qps"]:.2f}；这是跨批次参考，不是新做的交错配对吞吐实验。不能据此证明所有预算/数据集上 Recall 都不下降。', '',
        '## 维度选择', '',
        '按当前 native 实际补齐规则计费：DD=next_power_of_two(ceil(d/64)×64)，而不是直接用 d/8。逐个检查默认 64 到 959 维的 PCA 前缀，以及可直接复用原方法的 960 维；原始维度能常驻时不使用 PCA。', '',
        '|总预算 MiB|最高保留维度|实际 bit 数/向量|codes MiB|factors MiB|PCA 额外预留 MiB|record cache MiB|含 reserve 的准入 MiB|',
        '|---:|---:|---:|---:|---:|---:|---:|---:|']
    for key, p in plans.items():
        extra = sum(p[k] for k in ('projection_bytes', 'mean_bytes', 'rotation_reserved_bytes', 'query_scratch_reserved_bytes'))
        lines.append(f'|{key}|{p["dimension"]}|{p["padded_dimension"]}|{p["codes_bytes"]/MIB:.3f}|{p["factors_bytes"]/MIB:.3f}|{extra/MIB:.3f}|{p["record_cache_bytes"]/MIB:g}|{p["admission_bytes"]/MIB:.6f}|')
    lines += ['', '固定项 426 MiB、安全 reserve 64 MiB 保持原锁定值。128 维纯 codes=15.259 MiB，factors=19.073 MiB，PCA 额外预留=1.191 MiB；538 MiB 总预算下还可分配 12 MiB record cache。残差统计量、剪枝证书当前为 0。原始图、完整 4-bit 和 residual 仍在 SSD，按需读取。', '',
        '542 MiB 下 255 维准入为 541.997314 MiB；256 维需要 542.000977 MiB，超出 1024 bytes。255/256 都占 256-bit code，差别是投影矩阵的存储。保留最大维度是本策略的目标，并不代表牺牲 cache 后吞吐一定最优。', '',
        '预算是整进程 RLIMIT_AS。准入账本含保守预留，实际 VmPeak 另测；不能把这些门槛解释成物理内存极限。生成 sidecar 为离线操作，改变预算可能需要生成新的维度资产，不是在线截断旧 code。', '',
        '## 本轮 validation（统一 M=32/64）', '',
        '|M|width|Recall@10|QPS|route 页/query|总页/query|完整 4-bit 候选/query|record 命中/query|',
        '|---:|---:|---:|---:|---:|---:|---:|---:|']
    for x in rows:
        if x['split'] == 'tune':
            lines.append(f'|{x["M"]}|{x["width"]}|{x["recall"]:.6f}|{x["qps"]:.2f}|0|{x["total_pages_q"]:.2f}|{x["full4_candidates_q"]:.2f}|{x["record_hits_q"]:.2f}|')
    lines += ['', '本轮 M=32 与 M=64 在各个共同 width 上 Recall 相同；这是观测，不是安全剪枝证明。validation100 单轮选参，仅在 Recall≥0.95 的点中比较 QPS；test 不重新选参。PCA query 投影成本计入 QPS。', '',
        '## 真实运行的内存验收', '',
        '|split|预算 MiB|维度|M|width|准入 MiB|VmPeak MiB|Recall|',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for x in rows:
        lines.append(f'|{x["split"]}|{x["budget_mib"]:g}|{x["dim"]}|{x["M"]}|{x["width"]}|{x["admission_mib"]:.6f}|{x["vmpeak_mib"]:.3f}|{x["recall"]:.6f}|')
    lines += ['', '255 维仅使用 train100 做非标准维度加载/准入检查，不与 validation/test 混合比较质量。550/580/632 MiB 若见于 preflight 审计，只代表 native 准入交叉检查，不代表这些预算都测过 QPS。', '',
        '所有测量采用 32 workers、beam=1、O_DIRECT，预热 100 查询；查询和 ground truth split 沿用已冻结方案。设备缓存不受控，不能宣称完全独占环境。既有图构建仅在测量期间暂停，结束后恢复。首次启动因预算浮点类型导致 RLIMIT_AS 设置失败，未开始搜索，记录保存在 failed_before_search。', '',
        '## 第二阶段：安全 hard prune', '',
        '目标是在读取完整 4-bit 前使用 `pool 已满 && L_safe(q,x) > tau`；L_safe 必须下界当前 native 4-bit 分数。真实 L2 下界与量化 τ 不能混用。候选方案需要低维符号重建误差证书、数值误差界，并把所有证书与 scratch 内存重新计入选择器。', '',
        '当前只完成精确算术参考与 2304 对小维度穷举检查；没有 GIST native 安全性或性能结论。缺误差证书时参考接口保持不剪枝，线上低维 hard prune 始终关闭。', '',
        '[安全条件、公式与内存代价](hard_prune.md) · [CSV](measured_results.csv) · [账本](memory_ledger.json) · [validation 选参](selection_538.json) · [native 准入交叉检查](preflight.json) · [审计](audit.json) · [复现入口](../../../experiments/04_ours_memory_budget/gist_adaptive_resident/README.md)', '']
    (OUT / 'report.md').write_text('\n'.join(lines))


if __name__ == '__main__':
    main()
