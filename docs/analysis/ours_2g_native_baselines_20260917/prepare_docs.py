"""Prepare reviewed local/Feishu changes for the user's asymmetric memory policy."""
from pathlib import Path
import copy
import json
import shutil
import xml.etree.ElementTree as E

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
NEW_ID = 'disk_ours2g_native_baselines_20260917'

PREFIXES = {
'本实验在统一硬件': '本实验在相同硬件、数据和查询负载下比较 Ours 与 ANN 方法，固定 32 个查询 worker，每个配置只运行一次。按用户最新决定：Ours 主实验强制 2 GiB 内存上限；其他方法保留各自原生内存、量化和缓存配置，不要求与 Ours 预算或用量相等。所有方法报告实际峰值 RSS、磁盘开销和匹配 Recall@10 的性能。05A/05B/05C 结构保持；05A resident 作为单列计算参考。内存扫描仅为单独的补充实验，不是主对比的统一预算要求。',
'2026-09-17 核验更新，基于本飞书文档': '2026-09-17 最新规则：Ours=2 GiB，baseline 各自配置，每个正式配置只跑一次。此前统一 4 GiB 方案已被本规则取代。Ours 需以 cgroup 验证真实限额；baseline 默认只记录系统实测峰值 RSS，不新增统一硬限额。缺少 cgroup 委派只阻塞需要限额的运行，不作为 baseline 内存不同的失效理由。旧 RLIMIT_AS、对象求和和来源不明结果仍不能改标签充当新测量。第 10—11 节中的审计记录按原时点理解，不覆盖本规则。',
'12.2 每次独立 repeat': '12.2 Ours 的磁盘测量在 exec/加载索引之前进入独立 cgroup，主实验设置 memory.max=2147483648 B（2 GiB）、memory.swap.max=0，记录父组约束、峰值和 OOM 事件。缺少委派时阻塞 Ours，不以 RSS 采样代替硬限额。baseline 使用各自原生设置，默认不新增 OS 硬限额，记录 memory_enforcement=none、memory_limit_bytes=null 和真实峰值 RSS；若另做显式限额补充实验，单独记录其限额。',
'12.3 总预算包括': '12.3 Ours 的 2 GiB 限额覆盖整个搜索进程生命周期及组内子进程，包括索引、码本、全部 worker、缓存、查询缓冲、运行时和 cgroup 计入的文件缓存/内核内存。baseline 的实际峰值和可测分类如实报告，不要求小于 2 GiB。建索引另测，加载计入生命周期内存，Recall/GT 评估排除在 QPS 计时之外；已加载 GT 所占内存不得事后扣除。',
'12.4 memory.peak': '12.4 Ours 保存 cgroup memory.peak/current/stat/events 与 swap 记录；所有方法同时保存系统测得的进程峰值 RSS。baseline 未使用 cgroup 时相关字段为 null，不伪造零值。RSS 与资源组峰值不是相同范围，不能相加；原生进程扫描多个宽度时明确共享生命周期峰值，不假称逐点查询峰值。',
'12.5 固定目标': '12.5 固定目标设备、CPU/NUMA、查询顺序与预热协议，查询时隔离构建/fio 等干扰。各方法可以有不同原生缓存容量和生命周期：Ours 计入自己的 2 GiB 限额，baseline 如实报告用量，不强制等额缓存或总内存。O_DIRECT 不消除设备缓存；buffered/mmap 的页缓存和预热条件需单独记录。',
'4 GiB 预检只确认': 'Ours 按 2 GiB 预检并固定主配置；baseline 按各自原生配置预检，不为对齐 Ours 而减少 PQ、导航图、线程或缓存。只在独立 validation 上选择参数，不能用测试集挑预算。某个方法不支持当前数据时单独记录，其他方法可继续。补充内存扫描与主结果分开，不把它变成所有方法的统一门槛。',
'原生 CLI 的 run_official_disk_baseline.py': '原生 CLI 的 run_official_disk_baseline.py --execute 默认保留 AiSAQ/Starling 自身的 PQ、导航和缓存配置，只测真实峰值 RSS，不要求 cgroup 或统一 4 GiB。只有显式传入 --search-memory-gib 才启用该方法的 cgroup 限额。该入口仍为诊断，formal_ready=false，正式 artifact、计时与原生一致性验收继续保留。',
'原生 CLI 的 `run_official_disk_baseline.py': '原生 CLI 的 `run_official_disk_baseline.py --execute` 默认保留 AiSAQ/Starling 自身的 PQ、导航和缓存配置，只测真实峰值 RSS，不要求 cgroup 或统一 4 GiB。只有显式传入 `--search-memory-gib` 才启用该方法的 cgroup 限额。该入口仍为诊断，`formal_ready=false`，正式 artifact、计时与原生一致性验收继续保留。',
'主要图表：①': '主要图表：① Ours 2 GiB 与 baseline 各自配置的 Recall@10–QPS 曲线；② 同 Recall 下实际峰值 RSS、磁盘索引和延迟；③ 单独的内存敏感性补充实验；④ 05A/05B 组件证据。图表必须明确各自内存设置，不称同内存预算比较。不能覆盖的 Recall 区间留空，不外推加速比；缺少逐查询数据时不伪造尾延迟。',
'小型 Python 回归与 C++ 编译记录见': '小型检查和源码记录见 docs/analysis/ours_2g_native_baselines_20260917/。当前缺少 cgroup 委派会阻塞 Ours 2 GiB 的真实限额检查；baseline 原生配置的 RSS 观测不依赖该委派。CPU/NUMA 工具、二进制版本、原生一致性和计时验收仍分别检查，不能把内存政策放宽当作这些项目已通过。',
'后续实际核验：': '后续核验确认当前宿主账号没有可写 cgroup 委派，且缺少 numactl；因此 Ours 2 GiB 尚未验证。五个二进制与登记哈希不一致，AiSAQ/Starling 的正式接入仍 pending。Rust 计时已补修，32 workers 极小查询检查通过；这些不是全量性能或受限内存验收。最新实施记录见 docs/analysis/ours_2g_native_baselines_20260917/README.md。',
}

REPLACE = {
'05C 内存扫描': 'Ours 内存扫描（补充）',
'05C 预算扫描': 'Ours 预算扫描（补充）',
'1/2/4/8 GiB；全部保持 32 线程，不因某方法失败而单独降线程。': 'Ours 可单独扫描 1/2/4/8 GiB，固定 32 线程；baseline 各自配置不随之强制变更。',
'cgroup memory.peak、进程峰值 RSS、索引/线程工作区分类；VmPeak 仅作地址空间诊断。': '所有方法报告系统测得的峰值 RSS；Ours 另报 cgroup 峰值/events；可测分类单列，VmPeak 仅作地址空间诊断。',
'memory.max = declared budget in bytes; memory.swap.max = 0': 'Ours: memory.max = 2147483648 bytes; memory.swap.max = 0; native baseline: no added cap',
'resource peaks/events and actual constraints recorded; no OOM/failure': 'process peak RSS recorded for every method; cgroup peaks/events required for capped Ours; no failure',
'CSV 哈希与完整重复后才筛方法': 'CSV 哈希与单次结果溯源后才筛方法',
'05A payload_on_ssd、05B、05C 拟采用 cgroup v2 memory.max=4294967296 B（4 GiB），memory.swap.max=0；预检后冻结。': 'Ours 磁盘主实验为 cgroup memory.max=2147483648 B（2 GiB）、swap=0；baseline 各自配置，不新增共同硬限额。',
'同一数据与查询/GT、设备、32 worker、统一 cgroup 预算；各方法在预算内使用自己的结构与缓存，不要求同样耗尽内存。': '同一数据、查询/GT、设备与 32 worker；Ours 限额 2 GiB，baseline 保留各自原生内存与缓存设置，并报告实测峰值。',
'4 GiB 待验证；同 Recall@10 比较 QPS、实际内存和磁盘开销。': 'Ours 2 GiB 待验证；baseline 各自配置；同 Recall@10 比较 QPS、实际内存和磁盘开销。',
'磁盘主实验拟 4 GiB；05C 扫描 1/2/4/8 GiB；均使用相同 cgroup 计量。05A resident 单列。': 'Ours 主实验 2 GiB；baseline 不要求预算相同，报告各自配置与实测峰值。预算扫描作为补充，05A resident 单列。',
'32 线程；拟 4 GiB；输入、设备、预热和统计协议一致': '32 线程；Ours 2 GiB，baseline 各自配置；输入、设备、预热和统计协议一致',
'保留原候选或图控制关系；统一新磁盘预算': '保留原候选或图控制关系；Ours 限额与 baseline 各自配置明确报告',
'主组默认 4 GiB、32 workers；`budget_scan` 显式选择 1/2/4/8 GiB，始终 32 workers；`thread_scaling` 单列，每个线程数独立 run-id。': '主组为 Ours 2 GiB、baseline 各自配置、32 workers；补充 budget_scan 仅改变 Ours 的显式限额；同一方法在调参与测试之间保持配置一致。',
'磁盘查询在 exec/加载索引前加入独立 cgroup v2；设置 memory.max、memory.swap.max=0，保存 peak/events/stat。另以 wait4 获取真实生命周期峰值 RSS；VmPeak 明确为采样诊断。': 'Ours 磁盘查询在加载前进入 2 GiB cgroup，保存限额与峰值/events；baseline 默认不加统一限额。两者均以 wait4 获取真实生命周期峰值 RSS，VmPeak 只作采样诊断。',
'同一查询集、32 worker、冻结后的 cgroup 预算与 I/O 口径': '同一查询集、32 worker、Ours 2 GiB 与各 baseline 冻结配置、明确 I/O 口径',
'只在实际共同支持的数据集、预算和 recall 范围内给出方法间比较': '只在实际共同支持的数据集和 recall 范围内比较，同时披露各自内存设置与实测用量',
'32 线程、4 GiB 及预算扫描属于本项目协议': '32 线程、Ours 2 GiB 与 baseline 各自配置属于本项目协议',
'通过新版总预算、原生实现准入': '通过 Ours 限额、各方法实测内存、原生实现准入',
'正式运行仍需具备可写委派 cgroup、核验 CPU/NUMA 和原生端口准入': 'Ours 限额运行仍需可写委派 cgroup；所有方法均需核验 CPU/NUMA、版本与原生端口准入',
'memory_enforcement = cgroup_v2 (disk-backed new protocol)': 'memory_enforcement = cgroup_v2 (Ours disk) / none (native baseline observation)',
'disk_cgroup_v2_20260917': NEW_ID,
'_cg4g_': '_ours2g_native_',
}


def rewrite(text):
    for prefix, value in PREFIXES.items():
        if text.startswith(prefix):
            return value
    for old, new in REPLACE.items():
        text = text.replace(old, new)
    return text


def main():
    local = ROOT/'docs/plans/DISK_EXPERIMENT_PROTOCOL_20260917.md'
    before = local.read_text()
    local.write_text('\n'.join(rewrite(line) for line in before.splitlines())+'\n')
    for name in ('plan',):
        raw = json.loads((HERE/f'{name}_before.json').read_text())['data']['document']
        root = E.fromstring('<doc>'+raw['content']+'</doc>')
        patches=[]
        # Edit text blocks only, preserving resource/citation blocks.
        for e in root.iter():
            if e.tag not in ('p','pre'):
                continue
            old=''.join(e.itertext());new=rewrite(old)
            if new==old:
                continue
            if any(child.tag in ('cite','img','source','whiteboard') for child in e.iter()):
                raise RuntimeError('manual review required for resource/citation paragraph')
            node=copy.deepcopy(e)
            if any(old.startswith(prefix) for prefix in PREFIXES):
                node=E.Element('p');node.text=new
            else:
                for child in node.iter():
                    if child.text:child.text=rewrite(child.text)
                    if child.tail:child.tail=rewrite(child.tail)
                    child.attrib.pop('id',None)
            file=f'{name}_patch_{len(patches):02d}.xml'
            (HERE/file).write_text(E.tostring(node,encoding='unicode')+'\n')
            patches.append({'command':'block_replace','block_id':e.get('id'),'file':file})
        (HERE/f'{name}_patches.json').write_text(json.dumps(patches,ensure_ascii=False,indent=2)+'\n')
        print(name,'patches',len(patches))
    shutil.copyfile(ROOT/'docs/analysis/agnews_verification_20260917/publish_corrections.py', HERE/'publish_corrections.py')


if __name__=='__main__':main()
