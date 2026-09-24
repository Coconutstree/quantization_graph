"""Prepare reviewed, targeted changes to the experiment plan; no remote writes."""
from pathlib import Path
from html import escape
import json
import shutil
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
base = json.loads((HERE / 'fetch.json').read_text())
tree = ET.fromstring('<doc>' + base['data']['document']['content'] + '</doc>')
local = ROOT / 'docs/plans/DISK_EXPERIMENT_PROTOCOL_20260917.md'
original = local.read_text()
text = original
patches = []


def save(block_id, xml):
    ET.fromstring('<doc>' + xml + '</doc>')
    file = f'plan_patch_{len(patches):02d}.xml'
    (HERE / file).write_text(xml + '\n')
    patches.append({'command': 'block_replace', 'block_id': block_id, 'file': file})


def replace(block_id, new, tag='p'):
    global text
    old = next(e for e in tree.iter() if e.get('id') == block_id)
    previous = ''.join(old.itertext())
    assert previous in text, previous
    text = text.replace(previous, new, 1)
    save(block_id, f'<{tag}>{escape(new)}</{tag}>')


replace('doxcnOVQlqMm3nGYDJdU5jI4OCc',
    '主对比明确包含四个完整磁盘系统：Ours-Disk、DiskANN-PQ-Disk、Starling-Disk 和 AiSAQ-Disk。'
    '固定相同数据、硬件、查询负载和 32 个查询 worker，每个正式配置只运行一次。Ours 主实验强制 2 GiB 内存上限；'
    '三个 baseline 使用各自原生内存、量化和缓存配置，不要求预算或用量与 Ours 相等。'
    '所有方法报告实际峰值 RSS、磁盘开销和匹配 Recall@10 的性能。05A/05B 保留组件实验，05C 承担四系统主对比；'
    '05A resident 单列为计算参考，Ours 内存扫描单列为补充。Starling/AiSAQ 的配置和支持矩阵见第 7.1—7.4 节。')
replace('doxcnUcmcmD4L90WyHX2akMpeJd', 'Ours 内存扫描（补充）')
replace('doxcnk0aApQ6TkkXaaBBZCrlw1b',
    '仅 Ours 在固定 32 worker 下扫描 1/2/4/8 GiB；baseline 保持各自冻结配置，不要求随 Ours 改预算。')
replace('doxcnaU3jF06nhs7cHuCHTXohDe',
    'PQ/SQ/SAQ 共用 baseline 图，Ours 固定自有图；分别生成 Recall-QPS Pareto frontier')
replace('doxcnqb5oyxvK3fJQT0CYuJiGzc',
    'Ours 固定 2 GiB；baseline 各自固定配置；同一数据任务')
replace('doxcnEDlw9aPyGSaoPtq822kOzc',
    '仅 Ours 的 1/2/4/8 GiB；固定配置敏感性或按预算调参分别标注')
replace('doxcnun5xXB8p5zQpUbSA3aZQ1d',
    'Ours 接入 2 GiB cgroup 与峰值/events；所有方法接入真实进程峰值 RSS。baseline 默认只观察，不施加共同硬限额。')
replace('doxcnJDoYPpxMac3ezTmg2k9L8g',
    '按新协议核对四方法集合、Ours 2 GiB 和 baseline 各自配置；正式接口、缓存与可测指标逐方法验收。')

# Keep old audit evidence, but make its time and superseded rules explicit at the section itself.
heading = '11. 2026-09-17 初次核验记录（历史状态）'
note = ('本节表格保存首次核验时的状态，“执行器待接入”“4 GiB 是预检起点”等不再作为当前执行要求。'
        '当前外层 runner 已实现；Ours 主实验为 2 GiB，baseline 各自配置。Starling/AiSAQ 的最新接入状态见第 7.3—7.4 节，'
        '其余实施状态见第 16 节。')
old_heading = '11. 2026-09-17 核验结论与待修复项'
text = text.replace('## ' + old_heading, '## ' + heading + '\n\n' + note, 1)
save('doxcnoMVyVzLLgXNUi8KVmkUWnc', f'<h2>{escape(heading)}</h2><p>{escape(note)}</p>')
old = next(e for e in tree.iter() if e.get('id') == 'doxcnpfhyzX4DLcUruh5x4sxhhc')
replace('doxcnpfhyzX4DLcUruh5x4sxhhc', ''.join(old.itertext()).replace('新版第 1—9、11—14 节', '当前第 1—9、12—16 节'))
label = 'AGNews 磁盘环境 32 线程实验分析（历史补充参考）'
text = text.replace('AGNews 磁盘环境 32 线程实验分析（本次读取核验对象）', label, 1)
save('doxcncLhsJ4Yd8dV7OyYWpoT6Lf', '<p><a href="https://my.feishu.cn/docx/AhoKdQxEBogmWoxfk6xcxHKjnsg">' + label + '</a></p>')

xml_parts, md_parts = [], []


def p(s):
    xml_parts.append('<p>' + escape(s) + '</p>')
    md_parts.append(s)


def h(s):
    xml_parts.append('<h3>' + escape(s) + '</h3>')
    md_parts.append('### ' + s)


def table(headers, rows):
    xml_parts.append('<table><thead><tr>' + ''.join('<th><p>' + escape(c) + '</p></th>' for c in headers)
        + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td><p>' + escape(c) + '</p></td>' for c in row)
        + '</tr>' for row in rows) + '</tbody></table>')
    md_parts.append('\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |']
                              + ['| ' + ' | '.join(row) + ' |' for row in rows]))


h('7.1 四个主方法分别比较什么')
table(['方法', '保留的原生流程', '内存规则与实验作用'], [
    ['Ours-Disk', 'DB1 筛选、ExRaBitQ4、locality 布局、残差重排；冻结当前实现。', '整个搜索进程执行 2 GiB cgroup 限额；作为被比较方法。'],
    ['DiskANN-PQ-Disk', '保留原生图搜索、PQ 候选评分、SSD 节点读取与精确评分；披露现有 reader 差异。', '全量 PQ 短码及配置启用的缓存驻留；使用自身配置，报告真实峰值 RSS。'],
    ['Starling-Disk', '原生磁盘建图 → 抽样导航图 → 图分区与重布局 → 导航图引导的 page search；保留全量 PQ 和内存导航图。', '使用自身配置；比较导航图与磁盘局部性优化后的完整系统，不套用 Ours 的 2 GiB 限额。'],
    ['AiSAQ-Disk', '建图和查询均启用 --use_aisaq；从 SSD 取 PQ 码，保留官方 inline/rearrange 与可选 PQ 缓存。', '使用自身配置；比较 PQ 码移到 SSD 后的性能、内存与磁盘代价，DRAM-free 不等于进程 RSS 为零。'],
])
p('Starling 与 AiSAQ 加入 05C 主系统对比；05A 继续比较量化器，05B 继续比较既定机制，不能把这两个完整系统改造成共享 Ours 图或量化器的端口。主图预登记四方法，实际曲线只使用通过该数据集验收的方法，缺失项保留状态。')
xml_parts.append('<p>论文依据：<a href="https://arxiv.org/html/2401.02116v3">Starling，SIGMOD 2024</a>；<a href="https://arxiv.org/html/2404.06004v2">AiSAQ: All-in-Storage ANNS with Product Quantization for DRAM-free Information Retrieval</a>。本节具体 CLI 参数以本地固定 release 为准，不把 release 的全部选项都当作原论文实验参数。</p>')
md_parts.append('论文依据：[Starling，SIGMOD 2024](https://arxiv.org/html/2401.02116v3)；[AiSAQ: All-in-Storage ANNS with Product Quantization for DRAM-free Information Retrieval](https://arxiv.org/html/2404.06004v2)。本节具体 CLI 参数以本地固定 release 为准，不把 release 的全部选项都当作原论文实验参数。')

h('7.2 Starling 与 AiSAQ 的配置登记')
p('下表是现有接入脚本的起点和必须登记的参数，不是已调优的正式配置，也不是要求两个 baseline 数值相等。最终值只在独立 validation 集上选择，连同实际索引头、完整命令和哈希冻结；test 不再修改。')
table(['项目', 'Starling-Disk', 'AiSAQ-Disk'], [
    ['固定版本', '17dc3e8a011533a62374445f53963e951b72883a；本轮源码检查通过，无登记补丁。', 'f0a48e984c685bd498e3c4f88386b47e0e4ab1ac；本轮源码检查通过，现用构建含已登记 sysfs 扇区查询兼容补丁。'],
    ['图与 PQ', 'GIST 已验证 R48，L_build=400；目标 PQ=32 B，现存 GIST 索引实际为 31 B，以索引头为准。其他数据先查页容量。', '脚本起点 R64、L_build=400、PQ=32 B；须用目标原维度样本验证布局，再选择实际图度数与码长。'],
    ['方法特有建索引参数', '脚本起点：导航抽样比例 0.01、导航 R48/L128、分区迭代 8；执行完整 partitioner 和 index_relayout。', '--use_aisaq；脚本默认 inline_pq=-1（按页余量自动选择）、rearrange 关闭。inline 数量和重排作为原生配置登记。'],
    ['方法特有搜索参数', '--use_page_search=1、--mem_L 非零；脚本起点 mem_L=32、use_ratio=1、use_sq=0。', '--use_aisaq、pq_read_io_engine=aio；vector_beam 默认 1，且不得大于 beam。'],
    ['缓存', '登记节点缓存和导航图；不能为统一缓存口径删除导航图或全量 PQ。', '登记节点缓存、共享 PQ 缓存和每线程 PQ 读页缓存。读页缓存要求 rearrange；32 线程容量须合计并包含元数据开销。'],
    ['搜索工作负载', 'k=10、workers=32、每配置一次；L、beam 按本方法 validation 冻结。GIST 旧诊断 beam=1。', 'k=10、workers=32、每配置一次；L、beam 按本方法 validation 冻结。脚本 beam 起点为 4。'],
    ['外层内存', '默认不新增统一硬限额，报告实际峰值 RSS。旧 AS=4 GiB 诊断保留原标签。', '默认不新增统一硬限额，报告实际峰值 RSS；PQ 缓存容量不等于整个进程内存。'],
])
p('两个官方 CLI 的起始搜索宽度为 20/40/80/160/320，仅用于制定 validation 扫描；候选范围是否覆盖 0.90/0.95/0.99 目标召回率须先核验，不能把相同 L 当作相同搜索工作量。脚本默认 node_cache=0、AiSAQ 的两个 PQ 缓存为 0，只是起点，正式配置允许按各自方法调优。')
p('AiSAQ 的 sysfs 补丁仅替代设备扇区大小查询，不修改图、PQ、候选队列或搜索逻辑；仍需在报告中披露。已存无补丁验证完成建索引，但查询失败，环境记录指向设备节点访问问题，不能把兼容补丁版称为完全无修改原版。')

h('7.3 三个 core 数据集的支持与验收状态')
table(['数据集', 'Starling-Disk', 'AiSAQ-Disk'], [
    ['GIST，D=960', 'R48 的完整原生流程小样本通过，百万索引已存在；w32/as4g 诊断 result.json 为 done，但 formal_ready=false、throughput_comparable=false。', '已有 1024×64、16 query 通用功能验证；当前证据尚不足以证明 GIST 目标维度和全量正式验收通过。'],
    ['AGNews，D=1024', '当前固定版本 FP32/R64 路径单节点 4356 B，超过 4096 B；已存状态 blocked。仅降低图度数不能解决 FP32 向量占满一页的问题。', '列为主 baseline 的计划验收项；目标维度、完整索引、正确性和计时待验证，不根据论文或方法名称直接标 passed。'],
    ['DBpedia，D=1536', '当前固定版本 FP32/R64 路径单节点 6404 B，超过 4096 B；已存状态 blocked。其他原生配置需独立验证后才能改变支持状态。', '列为主 baseline 的计划验收项；目标维度、完整索引、正确性和计时待验证。'],
])
p('GIST 的 FP32/R64 节点长 4100 B，也超过一页；因此不能直接把通用脚本默认 R64 套到 Starling 的 GIST 正式命令。AGNews/DBpedia 不通过加内存、截断维度或自行改页长补齐；若验证出完整可用的官方压缩路径，则另立配置、披露输入与存储差异。这里的 unsupported 仅针对所查版本和路径，不声称 Starling 算法对所有实现都不支持。')

h('7.4 已加入方法集合，还差哪些执行工作')
p('本轮重新核对 native_contract.py 的 METHOD_SPECS/LAYER_METHODS 与 ports.local.json：四方法主集合已包含 Ours、DiskANN、Starling、AiSAQ。Starling/AiSAQ 的注册状态仍为 pending；官方 CLI 可生成命令计划并记录原生结果，但尚不能作为完整 05-suite 正式端口运行。加入文档和方法列表不等于完成正式接入。')
p('后续按方法完成：核验目标维度与输入/结果 ID → 连接正式 artifact 与外部 RSS 测量 → 验证纯搜索计时和 CPU/NUMA/预热条件 → validation 调参 → 冻结源码、二进制、索引和配置 → 每个正式配置测一次 → 在共同达到的 Recall 区间绘图。缺少可选逐查询指标时写 null；必需的 Recall、QPS、RSS 与溯源证据必须真实可核验。')
p('run_official_disk_baseline.py 默认只输出命令计划；--execute 会执行建索引和搜索。默认不传 --search-memory-gib，也不要求 cgroup。只有单独研究该 baseline 的限额时才显式传 --search-memory-gib 和 --cgroup-parent。建索引 -M/-B 不能当作整个搜索进程的硬限额。现有 CLI 仍为诊断接口，本轮不执行全量实验，不自动把 pending 改成 ready。')
p('本轮证据：docs/analysis/plan_document_audit_20260917/source_registry_check.json；docs/analysis/official_disk_baselines_20260916/smoke_validation.json；GIST Starling 的 diagnostics/w32_as4g_20260917/result.json 及 AGNews/DBpedia 的 Starling-Disk/result.json。Ours 与 DiskANN 实际二进制哈希仍不同于登记值，需独立核验后更新登记，不能因新增两个 baseline 自动放行。')

anchor = next(e for e in tree.iter() if e.get('id') == 'doxcnYaHnYaDgAHEzyEKMO0nYNb')
save(anchor.get('id'), '<p>' + escape(''.join(anchor.itertext())) + '</p>' + ''.join(xml_parts))
assert text.count('## 8. 搜索参数') == 1
text = text.replace('## 8. 搜索参数', '\n\n'.join(md_parts) + '\n\n## 8. 搜索参数', 1)
(HERE / 'local_protocol_before.md').write_text(original)
local.write_text(text)
(HERE / 'plan_before.json').write_text(json.dumps(base, ensure_ascii=False, indent=2) + '\n')
(HERE / 'plan_patches.json').write_text(json.dumps(patches, ensure_ascii=False, indent=2) + '\n')
shutil.copyfile(ROOT / 'docs/analysis/ours_2g_native_baselines_20260917/publish_corrections.py', HERE / 'publish_corrections.py')
print(f'Prepared {len(patches)} patches; local method sections updated.')
