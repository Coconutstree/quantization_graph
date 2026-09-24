> 2026-09-23：本文保留 AiSAQ/Starling 的官方接入说明。当前 03/05 主名单已改为 Ours、SymphonyQG-DiskPort、DiskANN、Starling；AiSAQ 可选，PipeANN 后续增强。统一预算与可信对照以 [最新协议](SYMPHONY_TRUSTED_COMPARISON.md) 为准，下文旧名单和单独限额不再生效。

# 05C 官方磁盘 baseline（当前方案，2026-09-16）

用户确认主实验为 Ours-Disk、DiskANN-PQ-Disk、AiSAQ-Disk、Starling-Disk。
GIST、AGNews、DBpedia 的具体公平实验设计、原算法不改的准入边界与高维支持核验见 [磁盘 baseline 公平对比方案](DISK_BASELINE_FAIR_COMPARISON_20260916.md)。原生实现准入仍遵循该方案；2026-09-17 修订的预算、重复次数与执行验收以 [新版实验协议](DISK_EXPERIMENT_PROTOCOL_20260917.md)为准（外层执行器和统计已接入，正式端口验收仍待完成）。
Glass/SymphonyQG 移植版归补充实验，OG-LVQ 移植版未通过核验。
本文件替代旧 DISK_SYSTEM_EXPERIMENT_PLAN.md 中针对 05C 的五方法集合、强制共享驻留模式与统一图参数规定；05A/05B 不变。

## 查询期间的内存构成

| 对象 | AiSAQ（启用 --use_aisaq） | Starling（标准 PQ ANNS） |
|---|---|---|
| 全体向量的 PQ 短码 | 存储在 SSD，不要求全量驻留；按配置缓存一部分 | 驻留 DRAM，用于候选距离和路由 |
| PQ 码本、维度/分块元数据 | 驻留 | 驻留 |
| 图 | 主图在 SSD；可选缓存节点 | 主图在 SSD；另有抽样向量构成的内存导航图，含向量、邻接与 ID 数据 |
| 索引元数据 | medoid/可选额外入口 ID 与其 PQ 码、少量入口向量等 | 节点到磁盘块的映射及分区辅助数据、入口等 |
| 缓存 | 可选节点缓存、共享静态 PQ 缓存、每线程 PQ 读页 LRU | 可选节点缓存及搜索过程中使用的读取缓冲 |
| 每线程状态 | query、PQ LUT、候选/结果队列、visited、对齐 I/O 缓冲、AIO context | query、PQ LUT、候选/结果队列、visited、块读取缓冲、AIO context |

AiSAQ 的 DRAM-free 是不必让全体向量的 PQ 码驻留，并不等于 RSS=0。当前官方版本支持静态 PQ 缓存和每线程动态缓存；后者需启用 rearrange，容量上限按官方参数限制。32 线程 × 每线程 4 MiB 页缓存仅此项就有 128 MiB，外加容器与 I/O 开销。未启用过滤的实验不计不存在的过滤表；若以后启用，标签等元数据必须另计。

Starling 的 PQ 短码部分约 N × m 字节（m 为每向量 PQ 字节数），另外必须加导航图、ID/块映射、码本、线程状态和缓存。1,000 万向量 × 32 字节即 320 MB（约 305 MiB），并非 Starling 的全部内存。

原始证据：AiSAQ `include/pq_flash_index.h`、`src/pq_flash_index.cpp::aisaq_init`、`workflows/AiSAQ_index.md`；Starling 论文 §5.2 Algorithm 2、§6.4 与 Appendix N。
- https://github.com/kioxia-jp/aisaq-diskann/blob/aisaq_release/workflows/AiSAQ_index.md
- https://arxiv.org/html/2401.02116v3

## 接入与复现

固定源码及全部本次使用的子模块版本见 baselines/DEPENDENCY_LOCK.json。
- AiSAQ: f0a48e984c685bd498e3c4f88386b47e0e4ab1ac，官方 aisaq_release。
- Starling: 17dc3e8a011533a62374445f53963e951b72883a，官方仓库。
- AiSAQ 唯一补丁 `baselines/patches/aisaq-logical-block-size-sysfs.patch`：缺少 /dev/block 时，从同一设备的 sysfs logical_block_size 获取与 BLKSSZGET 对应的扇区大小。两条路径均失败则拒绝读取，不猜测，不退回 buffered I/O。搜索、量化、图和编码格式不变。补丁单独记录 SHA256；setup/runner 均核验有效源码差异。

```bash
python3 scripts/setup_disk_baselines.py --jobs 3
```

依赖：MKL、Boost program_options、libaio、tcmalloc、liburing 的开发文件。当前环境已具备前四项；liburing-dev 2.1-2build1 下载后通过 dpkg-deb -x 解包到 baselines/deps/liburing-dev，无系统安装操作。

独立官方 CLI 接入入口：

```bash
python3 experiments/05_disk_system_fair/run_official_disk_baseline.py \
  --method AiSAQ-Disk --base /path/base.fvecs --query /path/query.fvecs \
  --work-dir /path/on/target/disk/new-aisaq-run

python3 experiments/05_disk_system_fair/run_official_disk_baseline.py \
  --method Starling-Disk --base /path/base.fvecs --query /path/query.fvecs \
  --work-dir /path/on/target/disk/new-starling-run
```

默认只打印命令计划；加 `--execute` 才执行建索引和搜索。按用户最新要求，AiSAQ/Starling 使用各自配置，默认不传 `--search-memory-gib`，不额外施加统一硬限额，也不要求 cgroup；外层记录真实峰值 RSS。只有显式研究某 baseline 的限额时，才同时指定 `--search-memory-gib` 和 `--cgroup-parent`（或 `QG05_CGROUP_PARENT`）。work-dir 必须是新目录，避免混合旧图/结果。

当前配置起点与三个 core 数据集的支持状态已补入[实验协议第 7.1—7.4 节](DISK_EXPERIMENT_PROTOCOL_20260917.md)。特别是 Starling 的 GIST FP32 路径需使用已验证的 R48 起点，不能直接套用本脚本通用默认 R64；AGNews/DBpedia 的当前 FP32 路径另记布局阻塞。默认参数不是已调优的正式配置。
AiSAQ 在 build/search 两处强制 `--use_aisaq`。Starling 执行原版建图、抽样、导航图构建、partitioner、index_relayout，再以 `--use_page_search 1`、非零 `--mem_L` 搜索。
Python 仅转换 fvecs 为官方 bin 格式、启动原生程序并留存输入/命令/二进制哈希与日志，不实现搜索或量化。输出为官方 result IDs/distances 二进制，保留官方日志。

## 当前验证状态

两种原生程序均编译成功，各自完成 1024 × 64 维数据建图、16 查询、L=10/40 的磁盘搜索。结果 ID 合法且 top-10 无重复；返回距离与原始向量 float64 L2² 重算一致（rtol=1e-5、atol=1e-4），最大绝对误差约 1.12e-5。
证据见 docs/analysis/official_disk_baselines_20260916/。
这些是当前文件系统的小规模功能验证，不是目标 NVMe 的正式性能结果，也不用于比较两个方法的优劣。

**加入默认方法列表和官方 CLI 已完成；统一正式跑批接口仍未完成。** 新注册项 status=pending，防止将 CLI 原生日志当作满足完整正式契约的测量。尚需：
1. 05-suite 逐查询 artifact/trace 接口，计时不包含 ground truth 评估，并正确记录实际页数与距离计算。
2. 真实进程峰值 RSS、可测分类和测量范围：索引、查询工作区、所有线程与可选缓存。baseline 不需要和 Ours 的 2 GiB 上限相等；缺失可选分类如实标记。
3. 真实数据上的正确性验证及独立验证集调参，绑定最终源码、补丁、二进制、图和参数。
4. 将官方原生 CLI 与测量接口结果对照，确认 instrumentation 不改变有序结果。

不生成假的 parity 标记，不将 --build-memory-gib 或 Starling 的 -B 当成已执行的整个搜索进程内存限制。当前独立 runner 不执行统一的 2 GiB 限制；其 manifest 明确 formal_ready=false。

## 公平比较规则

以相同设备、查询集、k、并发工作负载和相同 Recall@10 比较。Ours 主实验限额 2 GiB；DiskANN、Starling、AiSAQ 使用各自配置，不要求预算或实际内存相等，并报告各自真实峰值。各方法保留官方图、码长、驻留策略与搜索调度，通过独立验证集选择配置。无需强制相同 PQ 字节或相同 L。Starling 导航图、AiSAQ inline/rearrange/cache 等是方法本身可调配置，不能删除后仍声称完整原方法。

主图为四方法的完整系统比较；原移植算法可显式选择用于补充诊断，但不能混入同一正式四方法验收清单。历史结果/固定二进制与旧跑批脚本不自动迁移或续跑；新方法新版本必须使用新 run-id。
