# 03 固定官方对照实现记录（2026-09-24）

状态：代码、独立构建、正确性检查及飞书方案已更新。正式 SSD/NVMe 实验未启动，当前设备检查拒绝 HDD/未知设备；本记录不包含正式 QPS 结果。

## 实现

- 03 专用入口默认 Ours / DiskANN / Starling / AiSAQ，beam=4、32 workers、4 GiB 峰值 RSS、repeat=1、九档固定宽度。05/06 的历史名单与默认策略不随之改变。
- 每个 test 宽度独立进程；Ours/DiskANN 增加单宽度测量和独立 warmup 输入，官方 C++ CLI 增加计时外独立预热。没有修改搜索内核。
- Starling/AiSAQ 直接走固定版本官方 CLI，使用独立参考进程检查统计开关前后有序 Top10，重算 L2 距离、Recall 并核验原始结果、环境、RSS 和资源侧证。正式前先做 validation 资源/正确性预检并冻结锁。
- Starling 参数按官方 sample 配置（R48、L128、MEM_L=0 等），AiSAQ 按官方全 inline PQ 示例；具体来源和例外进入配置。原生可选缓存遵循官方 0 默认，不依据 QPS 选容量。
- 新 registry `src/disk_bench/ports.fixed03.local.json` 使用隔离构建；Ours/DiskANN 对 278 个相关源码文件保存 SHA256，Starling/AiSAQ 核验 upstream commit + 声明补丁。旧二进制、索引与结果未覆盖。
- 原始记录与 CSV 保存协议、源版本、二进制/输入/索引哈希、峰值 RSS、swap 和整进程 block I/O。查询 QueryStats 与整进程读盘量区分；无法完整覆盖 PQ 的查询级读盘字节留空，不能填 0。
- 主曲线检查四方法各九点，拒绝旧协议、缺点和重复点；匹配 Recall 表采用达到目标 Recall 的最快实测点，记录实际 Recall，不插值。高维 Starling 页格式限制单列。
- 飞书原文已备份并替换，revision 10 已回读确认：四方法、固定 beam、单轮、取消选优及硬件阻塞说明均一致。

## 验证

- 两个 Rust release 独立构建成功；两个官方 C++ 搜索程序及构建工具独立构建成功。
- Ours/DiskANN：512 base、64 test、32 workers 的原生正确性测试；beam=4、单宽度20、独立 warmup。Ours 通过；DiskANN 首次因沙箱禁止 io_uring 返回 EPERM，放开该沙箱限制后同一合成测试通过。该重试是环境故障修复，不用于挑选性能点。
- Starling/AiSAQ：1024 base、100 validation、16 test 的官方 CLI 原生集成检查；涵盖 export、最大宽度580 validation、width20 test、结果一致性、距离复算、资源和复用验证。测试数据为合成 fixture，不能进入论文数据集。
- 固定协议测试9项、方法管线4项、官方适配8项、官方数值验证2项、队列1项、绘图合同4项通过；协议相关28项中27项通过、1项环境条件跳过。
- 真正启动 03 时 SSD guard 返回拒绝；没有正式性能进程被启动。

检查产物：`results/diagnostics/03_fixed_official_fixture_final_20260924/`。前序 fixture 失败/中间检查均保留，其中首次 validation 使用不足100条的 fixture 被原生计数检查拒绝，随后修正测试数据；不是修改官方算法。

## 执行入口与待满足条件

新计划队列：`results/diagnostics/03_trusted_queue_fixed_b4_single_ready_20260924/status.json`。其中 `/tmp` 只用于展示与存储拒绝测试，绝非已批准 SSD。提供真实 SSD/NVMe 后，必须以实际目录和新 tag 生成队列，核验 CPU/NUMA、数据来源及设备空闲后执行。禁止直接复用 `/tmp` 计划开始正式测试。

```bash
python scripts/run_03_trusted_queue.py --tag NEW_SSD_RUN_ID \
  --ports src/disk_bench/ports.fixed03.local.json \
  --disk-root "$SSD_ROOT" --cpu-affinity "$CPU_LIST" --numa-node 0 --execute
```

GIST/BIGANN10M 正式索引复用、全数据内存准入、SSD 九点曲线及图策略受控消融均尚未运行；需要实际 SSD 路径，不能由本次小数据接口检查推断通过。历史查询的开发用途仍须在论文披露，不能宣称重新切分后获得独立最终 test。
