"""Document admission arithmetic from the actual frozen GIST calibration."""
import json,math

def section(out):
 mib=2**20
 lock=json.loads((out/'calibration/lock.json').read_text())
 p=json.loads((out/'calibration/resident/memory_plan.json').read_text())
 paged=json.loads((out/'calibration/paged/memory_plan.json').read_text())
 fixed=lock['fixed_bytes'];codes=p['codes_bytes'];factors=p['factors_bytes'];threshold=fixed+codes+factors
 assert threshold==lock['resident_threshold_bytes']
 residual=max(r['nonrouting_peak'] for r in lock['observations'])
 assert math.ceil((residual+lock['full_query_extra_bytes'])/mib)*mib+lock['safety_bytes']==fixed
 cache_min=4_000_000+1177
 minimum_paged=fixed+paged['paging_scratch_bytes']+10*mib+4352
 explanation=dict(scope='GIST BFS, cache-reuse binary, beam1, workers32, current calibration workload and environment',unit='bytes',fixed_including_centroid=fixed,codes=codes,factors=factors,centroid_already_in_fixed=p['centroid_bytes'],resident_threshold=threshold,minimum_record_cache=cache_min,record_cache_start_threshold=threshold+cache_min,minimum_paged_threshold=minimum_paged,minimum_factors_threshold=minimum_paged+factors,maximum_calibrated_residual=residual,full_query_allowance=lock['full_query_extra_bytes'],safety=lock['safety_bytes'],generic_across_datasets=False)
 (out/'validation/threshold_arithmetic.json').write_text(json.dumps(explanation,ensure_ascii=False,indent=2)+'\n')
 return ['', '## 562.14 MiB 是什么：完整 routing 常驻的整进程准入阈值', '',
 '**562.14 MiB 不是 1-bit codes 的体积，也不是完整图索引／原始向量全部进内存所需的大小。它表示：在本次 GIST BFS、beam1、32 workers、缓存复用版本及其校准环境下，整进程预算达到该值时，自动计划允许 codes 与 factors 全部常驻。精细记录仍从 SSD 读取；到这个边界时没有额外精细记录缓存额度。**', '',
 '| 本次 GIST 的组成 | 字节 | MiB | 用途与计账位置 |', '|---|---:|---:|---|',
 f'| 经校准的基础预留 B | {fixed:,} | {fixed/mib:.6f} | 非 codes/factors 的进程开销及余量；包含已校准的 centroid |',
 f'| 完整 1-bit codes C | {codes:,} | {codes/mib:.6f} | 所有节点的 routing 二进制编码 |',
 f'| 完整 factors F | {factors:,} | {factors/mib:.6f} | routing 评分辅助数据 |',
 f'| 整进程常驻准入阈值 T = B + C + F | {threshold:,} | {threshold/mib:.6f} | 到此阈值刚好允许完整 routing 常驻 |', '',
 '因此，完整 routing 数组本身只需 **141.143799 MiB**；562.14 MiB 还包含了整个进程的基础预留。当前按整数 MiB 设预算时，562 MiB 尚不足，563 MiB 才跨过这一估算边界。', '',
 '### 对任意数据集，加载前如何计算', '',
 '设数据集有 N 个节点，现有文件实际 codes stride 为 s_code 字节/节点，factors stride 为 s_factor 字节/节点：', '',
 '```text\nC = N × s_code\nF = N × s_factor\n有效预算 M = min(用户指定整进程预算, RLIMIT_AS soft limit)\nT = B(数据集、索引布局、线程数、二进制、工作负载、运行环境) + C + F\nM >= T：完整 routing 常驻\nM <  T：继续判断 factors 常驻＋codes 分页，或全部分页\n```', '',
 'C、F 取自现有 sidecar 的 24 字节文件头，并与 metadata 中的 N×stride 交叉校验；文件总长度必须等于 24+C+F，所有乘加检查溢出。不能只按 N×原始维度/8 估算，因为编码可能有 padding／对齐。此 GIST 有 1,000,000 个节点，实际 codes stride=128 字节、factors stride=20 字节，因此得到 128,000,000 与 20,000,000 字节。换数据集后重新读其文件头和 metadata，不能照用这些数值。', '',
 '### 421 MiB 从哪里来，包含什么', '',
 'B 是**本配置经校准的基础预留**，不是所有数据集都固定的 421 MiB。当前用 resident、factors、paged、hot_dynamic 四个分支校准：每组测量进程 VmPeak，再扣除该组已明确计账的 routing／可选缓存／分页专用空间，取剩余值的最大值。', '',
 '```text\nR_j = max(0, 本组 VmPeak_j − 本组已单独计账的空间 K_j)\nB   = ceil_to_MiB(max_j R_j + 完整查询集额外余量) + 安全余量\n```', '',
 'K_j 在常驻组是 C+F+实际精细记录缓存；在分页组是常驻 factors（若有）＋页槽容量×4352＋10 MiB 服务预留＋评分 scratch。它们在后续计划中再按选定分支计入，避免重复收费。', '',
 f'本次 max R_j = {residual:,} 字节 = {residual/mib:.6f} MiB；完整查询集额外余量 8 MiB，安全余量 64 MiB，因此 **ceil({residual/mib:.6f}+8)+64 = 421 MiB**。', '',
 '剩余值涵盖线程栈、每 worker 查询缓存、visited 等工作区、输入/输出、映射及其初始化临时空间、库和分配器地址空间、加载/运行峰值等。它是整体观测后的估算包络，未对每一类实现精确归因；8 MiB 和 64 MiB 是预留，不是测出来的占用。不能再把旧的每线程 12 MiB、额外 256 MiB 等重复加上。', '',
 f'**centroid 的计账：**本次为 {p["centroid_bytes"]:,} 字节。当前校准扣除 K_j 时没有扣掉 centroid，因此它留在 B 中；不能在 T=B+C+F 后再加一遍。若以后改成将 centroid 单列，就必须先从 B 中移出，再用 T=B_other+C+F+centroid。', '',
 '### 跨过常驻阈值以后，余量用来放什么', '',
 '```text\n常驻后的剩余额度 Q = M − B − C − F\nQ 不足以建立记录缓存：只有 routing 常驻\nQ 足以建立记录缓存：routing 常驻＋hot_dynamic\n```', '',
 f'本次 GIST 记录缓存启动还需要至少 **4,001,177 字节（{cache_min/mib:.6f} MiB）**，即 4,000,000 字节节点映射＋一条记录及其计账。因此 hot_dynamic 的估算启动边界为 **{(threshold+cache_min)/mib:.6f} MiB**，不是 562.14 MiB；整数 MiB 预算下从 566 MiB 起满足这一启动判断。此处是无额外 cache cap 时的计划判断，实际初始化仍需验收。', '',
 '例如 640 MiB − 562.143799 MiB = **77.856201 MiB**，这是本次精细记录缓存额度。现有策略静态记录优先，实际装入 65,962 条静态记录，动态容量为 0；余量增加后才可能建立非零动态缓存。', '',
 '### 不足以常驻时的另外两个阈值', '',
 '```text\nS = 当前分页评分 scratch\nH = routing 服务预留（本版本 10 MiB）\nP = 一页数据及元数据（本版本 4096+256 = 4352 字节）\n最小全分页准入     = B + S + H + P\nfactors 常驻最低准入 = B + F + S + H + P\n```', '',
 f'本次 S={paged["paging_scratch_bytes"]:,} 字节，得到全分页最低估算准入 **{minimum_paged/mib:.6f} MiB**、factors 常驻最低估算准入 **{(minimum_paged+factors)/mib:.6f} MiB**。这是沿用同一保守 B 的自动准入；384 MiB 显式诊断通过，并不意味着代码已把 auto 阈值改成 384 MiB。', '',
 '### 换数据集时的适用范围与当前代码限制', '',
 '1. 重新读取并校验 C、F、centroid、节点数及实际 stride；节点规模会影响 visited/映射，维度和记录布局会影响编码、评分 scratch 与加载峰值。\n2. 使用对应数据集、索引布局、线程数、二进制和查询工作负载重新校准 B，再确定并验证其查询集余量和安全余量。不能只换 C/F，却继续复用 GIST 的 421 MiB 校准值。\n3. 分页 scratch 当前按 (2×(s_code+s_factor)+1024)×64×workers 估算；记录缓存最低映射/记录计账也应按该数据集的真实布局计算。\n4. 当前入口明确是 **GIST 专用实验**：原生预检已读取真实 C/F，但 Python 校准脚本的阈值输出仍写死 +148,000,000 字节，记录缓存启动判断写死 4,000,000+1177，原生入口还要求 workers=32。因此当前代码**尚不是换数据集即可直接使用的通用入口**；上述专用常量和校准关联校验需要泛化后才能支持其他数据集。本文只澄清现有计算与适用范围，不将未实现的泛化描述成已完成。', '',
 '可核查证据：[本次逐项计算](validation/threshold_arithmetic.json) · [校准锁](calibration/lock.json) · [原生常驻计划](calibration/resident/memory_plan.json)。当前公式见源码 `gist_budget_sweep/auto_memory.rs` 的 choose/routing 和 `gist_budget_sweep/run.py` 的 calibrate（缓存复用入口导入了这两个实现）。', '']
