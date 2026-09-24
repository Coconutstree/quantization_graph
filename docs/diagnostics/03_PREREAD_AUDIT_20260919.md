# 03 实验预读核查

主要核查对象是造成109 QPS争议的 results/archive/03_disk_system_unadmitted_20260921/gist/test_L_400_w_32/raw 当前保存结果，并遍历各数据集 test_L_400_w_32 的存储协议；不把 legacy_snapshot 或已删除旧 Starling 记录混为当前实验。

| 方法（GIST 当前保存记录） | 全索引 O_DIRECT 显式预读 | 查询预热 | 设备缓存受控 |
|---|---|---|---|
| Ours-Disk | 原测量未发现此步骤；9月19日诊断另加 | 100 | false |
| DiskANN-PQ-Disk | 未发现此步骤 | 100 | false |
| SymphonyQG-DiskPort | 未发现此步骤 | 100 | false |
| OG-LVQ-DiskPort | 未发现此步骤 | 100 | false |
| Glass-NSG-DiskPort | 未发现此步骤 | 100 | false |
| Starling 官方诊断 w32_as4g_20260917 | 未发现此步骤 | 0，源码 WARMUP=false | false |

“未发现”指现存命令、启动脚本和协议没有本次 dd iflag=direct 全文件预读操作，不是对所有外部历史操作的证明。查询预热会访问索引，不能因此称为完全未预读/冷盘。c0 和零应用节点缓存也不保证设备缓存为空。

证据：每组 command.json、storage_protocol.json、result.json；DiskANN native_diskann/src/main.rs 的1162行附近确实执行计时前查询预热，其他三个C++移植器也有对应循环（og_lvq 677、glass 721、symphonyqg 716行附近）。当前统一入口 src/disk_bench/orchestrator.py 也传递固定查询预热参数；未接入04新增的直接预读开关。

## 已发现的前置状态差异

Ours 原测量启动前，graph_compact.pages/residual.pages 最近写入时间分别约14.59/12.13秒。其他四个方法主索引文件写入距测量开始约59至65小时。来源是当时 storage_protocol 的 input_snapshot 与 memory_measurement.started_unix；mtime不能证明期间没有读取或缓存命中。

历史队列 scripts/local_runs/run_optimized_05_queue.py 先导出/校验 Ours 布局，然后为方法执行 sampled_preflight，再进入测量。scripts/local_runs/sampled_05_parity.py 使用32个查询及3个L档，包含磁盘与内存参考路径。这些加载、正确性预检和布局写入都可能改变缓存状态，因此不能把其他方法描述为完全没有前置读取；脚本路径经过后续迁移，现存源码与当时实际过程的吻合以原始命令和结果记录为界。

## Starling 特例

GIST Starling 顶层 README 指向 diagnostics/w32_as4g_20260917：这是4 GiB地址空间约束的官方原版诊断；命令 num_nodes_to_cache=0，源代码样本缓存生成受此参数保护，WARMUP=false。结果明确 throughput_comparable=false、formal_ready=false；协议 historical_protocol_equivalent=false。不能将其当成与上述2 GiB/100查询预热一致的正式比较。

## 结论

其余四个方法有查询预热，没有发现全索引直接预读。Ours原测量同样没有这次新增的显式步骤，但刚导出布局这一前置条件不同。所有核查到的协议均未控制设备缓存；现有信息不足以认定所有方法测量时处于相同缓存状态，也不足以证明这些差异偏向谁、偏向多少。GIST formal_acceptance.json 本来就是 formal_ready=false，包含控制器缓存协议尚待接受项。

本次只读核查，没有启动性能实验、清缓存或改动算法。提取记录见 work/03_preread_audit_20260919/protocol_records.json。
