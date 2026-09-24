# Ours 剩余内存实验：统一直接预读重测

**当前运行：v3_direct。复用已恢复性能的v2原生程序，所有策略重新测量。旧v2性能结果和图表已按用户要求删除，仅保留构建与核验记录。**

## 当前统一预读队列

本轮入口与下方同一套代码，通过 `OURS_MEMORY_RUN=v3_direct` 隔离输出。所有新执行的baseline、profile及策略搜索，均先调用 `src/disk_bench/storage_precondition.py` 的 `prepare_search`；每个进程前完整顺序O_DIRECT预读两个实际BFS页文件一次，保存 `storage_precondition.json`。失败不启动搜索，不回退；耗时不计查询QPS。预读证据与模块源码SHA加入验收。

仍为GIST、原40档、800测试查询/200验证查询、100预热、beam1、workers32、2 GiB RLIMIT_AS、原查询顺序和BFS布局。沿用04原进程启动方式，无NUMA绑定；不冒充正式03 NUMA绑定运行。无需安装numactl。

顺序：baseline → profile → pages → hot_graph → hot_payload → hybrid → nav → nav_pages → nav_hybrid → full_payload。重新按本轮baseline VmPeak计算额度。新baseline先做逐查询验收，再与 `20260919_uniform03_current_full` 恢复结果比较：L100 QPS和完整40档总吞吐比均须在0.75–1.35内。该预先声明门槛只拦截明显异常，并非统计等效检验；不通过就停止后续方案。

```bash
# 后台队列已启动时不要重复运行；这是可复用的入口
OURS_MEMORY_RUN=v3_direct python -u experiments/04_ours_memory_budget/background_queue.py
# 查看状态和日志
cat results/04_ours_memory_budget/v3_direct/queue_state.json
tail -f results/04_ours_memory_budget/v3_direct/queue.log
# 只刷新本轮文档
OURS_MEMORY_RUN=v3_direct python experiments/04_ours_memory_budget/report.py
```

结果放在 `results/04_ours_memory_budget/v3_direct/<方案>/`。每档结束追加 `queries.jsonl` 和 `memory_stats/L*.json`，完整40档结束保存Recall/QPS/P99到 `result.json`；`performance_gate.json`保存基线性能门槛检查。队列每阶段后更新根目录 `ours_memory_budget_tests.md` 和本轮 `report.md`；`summary.json`仅含已验收记录。

后台巡检每30秒检测进度、进程与逐查询一致性，保存 `watch_state.json`/`watch.log`。失败会留证并停止后续测试；巡检不会自行修改代码，不声称具备自动修复能力。旧v2分析脚本含写死的旧结论，不能用于新结果，待本轮全部完成后依据实测值重新分析。

## 以下为原v2协议与实现说明

下方v2路径/命令为旧协议说明，旧性能文件已删除；当前执行命令必须带 `OURS_MEMORY_RUN=v3_direct`，当前成绩不与旧v2混算。


入口 `run.py` 默认只验证。完整协议、状态及结果见根目录 [`ours_memory_budget_tests.md`](../../ours_memory_budget_tests.md)。本目录旧版自定义调度器已替换；旧四组结果仍在 `results/04_ours_memory_budget/gist/`，不作为 v2 成绩。

## 固定条件

从 `results/archive/03_disk_system_unadmitted_20260921/gist/test_L_400_w_32/raw/Ours-Disk/command.json`、`result.json`、`memory_measurement.json` 读取并核对：

- GIST 1,000,000 × 960；原 R64 / Lbuild400 Ours 图。
- 原 BFS graph+compact 合并页、独立 residual 页；映射和数据按原 SHA-256 核对。
- 原800条测试查询、ground truth、原查询顺序；原流程预热100条（测试顺序的前100条，与原实现一致）。
- 原40档 L：10–30逐整数、40–100步长10、140–580步长40；beam1，workers32，top10，epsilon1.9，最多100条重排。
- 原 O_DIRECT/libaio、4096字节页、最多128在途I/O；repeat_id=0。
- 原 `measure_05_process.py` 和环境变量；进程 RLIMIT_AS=2 GiB。不是 cgroup 总内存限制，不宣称受控冷盘。

仅允许改变新增内存的用途。缓存容量统一从**新基线完整40档扫描的最大 VmPeak**计算：`2 GiB - VmPeak - 64 MiB`。64 MiB是所有方案统一保留的运行波动余量，不是缓存上限；缓存元数据也计入可用额度。硬上限仍由原测量器执行，OOM/分配失败不算有效结果，不能只给某方案放宽预算。静态热点不足或导航小图较小时不强行填满剩余空间，实际占用单列。

`full_payload` 缓存全库 compact+residual，若可用额度不足即失败，不放宽预算；它不是新的2-bit筛选层。

## 文件职责

- `protocol.py`：原配置锁定、路径迁移、内容哈希、输入规模和验证/测试集不重叠检查。
- `prepare.py`：复制原生入口和内核到独立 v2 工作目录，使用唯一锚点插入缓存钩子；锚点不匹配则失败。
- `main.rs`：包含生成的原生入口；没有独立的查询调度循环。
- `memory_experiment.rs`：page FIFO、热点邻接表、热点 compact+residual、导航小图、计账及统计。
- `run.py`：计划/执行、复用原测量器、基线回归检查及缓存逐查询一致性核验。
- `test_protocol.py`：参数偏移、输入替换、VmPeak计算和查询结果异常的回归测试。
- `report.py`：仅接纳 v2 中通过验收的完整运行；不会重新导入旧诊断结果。

## 不启动性能实验的核验命令

```bash
python experiments/04_ours_memory_budget/run.py --stage verify
python experiments/04_ours_memory_budget/run.py --stage build
python -m unittest discover -s experiments/04_ours_memory_budget -p 'test_protocol.py' -v
cargo test --release --offline --manifest-path work/ours_memory_budget/v2/native/Cargo.toml --target-dir src/graph_core/target memory_experiment::tests
python experiments/04_ours_memory_budget/run.py --stage baseline
python experiments/04_ours_memory_budget/report.py
```

## 完整执行顺序（当前已由后台队列执行，勿重复启动）

```bash
# 1. 新版无额外缓存基线：完整40档，800条测试查询。
python experiments/04_ours_memory_budget/run.py --stage baseline --execute
# 2. 原200条验证查询及其保存顺序、完整40档，生成热点统计；底库构建导航图。
python experiments/04_ours_memory_budget/run.py --stage profile --execute
# 3. 各缓存/导航方案：相同原配置、相同可用内存额度。
python experiments/04_ours_memory_budget/run.py --stage test --execute
python experiments/04_ours_memory_budget/report.py
```

不带 `--execute` 的 baseline/profile/test 只生成命令计划；test计划需要先有合格新基线，不能用旧诊断RSS推算容量。拒绝覆盖已有运行。新程序不发送 SIGCONT，不恢复旧暂停队列。

## 正确性与来源边界

先将新基线与保存的原实验逐查询返回ID、召回、访问/候选数比较；不一致就停止性能解释。纯缓存再与新基线比较。导航方案改变入口，以完整Recall–QPS曲线比较，不要求ID相同。

缓存节点数据有 DiskANN §3.4 的依据；FIFO分片、热点对象拆分和混合静态各最多25%、其余page属于本实验设计，尚未证明最优。导航小图为2,048个底库随机代表点的16NN+双向环，使用DB1点估计导航（beam16、最多32次展开），属于待验证原型，不是HNSW复现。这些方案内部参数属于新增内存策略，不能混同原搜索的beam1/40档L。

原论文：[DiskANN](https://suhasjs.github.io/files/diskann_neurips19.pdf)、[HNSW](https://arxiv.org/abs/1603.09320)。

## 热点统计、淘汰与容量回收

- 仅使用原200条验证查询。每个L保留原100条预热，在所有worker预热完成、正式计时开始前清零热点与I/O计数。预热不参与排名。
- 分别统计graph、compact、residual的逻辑请求次数；重复请求照计，不等同于磁盘读取。对每个类别、每个L，节点分数为“节点请求次数 / 此类别该L总请求次数”，再对原40档L等权平均；某档总数为0则贡献0。
- 邻接表按graph分数排名；完整精细记录按 `0.5 × compact分数 + 0.5 × residual分数` 排名。只选正分节点，同分按节点ID升序。这是频率代理，50%/50%不是已证实的最优权重。
- 热点在测试前预加载，测试期间不新增、不替换。page缓存按实际读取动态填入，16分片各自FIFO，命中不刷新插入顺序；每个L开始清空，随后沿用原预热。
- hybrid在扣除导航元数据后，邻接表与精细记录各最多使用25%；page得到扣除两者实际计账后的全部剩余额度（至少50%，可更多）。无可缓存节点时不分配空映射表；page按16分片整页取整，少量尾部余量不强填。单独热点方案仍允许用不满，以便隔离策略效果。
- `memory_stats/L*.json` 分别记录三类的logical_requests、io_requests、read_pages、read_bytes，以及page容量、缓存命中和总计账。I/O按触发读取的操作归属；graph与compact共享页，不能把某类别的减少当作独立因果收益。性能表对同L新基线计算总读页差，纯缓存仍要求逐查询结果与候选一致。
- profile保存 `L{L}_{graph,compact,residual}_counts.u64` 原始计数、四份 `*_scores.f64` 分数及 `profile_policy.json`。原始计数与分数均为little-endian，按逻辑节点ID排列，可重算排名；旧u32训练文件不再使用。
- 统计钩子对新版基线和各策略一致启用，有额外计数成本；性能比较采用同一新版二进制，历史QPS仅作为背景。

## 输出记录时机

`result.json` 在完整40档结束后保存Recall/QPS/延迟汇总；`queries.jsonl` 在每档完成后追加逐查询结果。`memory_stats/L*.json` 在该档结束时保存计数。所有这些文件及热点文件均加入最终验收哈希，未通过完整验收不作为成绩。

## 当前后台队列

`background_queue.py` 依次执行 baseline → profile → pages → hot_graph → hot_payload → hybrid → nav → nav_pages → nav_hybrid → full_payload。每阶段通过验收后自动更新 Markdown；任一步失败即停止后续阶段，保存失败原因。使用文件锁防止重复运行，拒绝覆盖已有结果。旧v1进程继续挂起。

```bash
# 查看整体进度
cat results/04_ours_memory_budget/v2/queue_state.json
# 持续查看队列日志
tail -f results/04_ours_memory_budget/v2/queue.log
```

## 后台巡检

`watch_queue.py` 每30秒检查队列进程及已完成L的查询结果、候选计数和I/O总量。状态写入 `watch_state.json`，日志写入 `watch.log`。异常标记为 `attention_required`；巡检为只读，不更改参数，不自动重试错误结果，也不能自动修改代码。正式验收与失败停止仍由执行队列负责。

## 已完成结果分析

9条曲线、360行结果的解释见 [`ours_memory_budget_analysis.md`](../../ours_memory_budget_analysis.md)，包括召回阈值比较、P99、I/O归因、内存余量和结论边界。运行 `python experiments/04_ours_memory_budget/analyze_results.py` 可重新校验结果证据并生成分析；不会启动性能实验。


## 存储预读对照与历史性能恢复

独立诊断入口（不覆盖已有 baseline/策略结果）：

```bash
python3 experiments/04_ours_memory_budget/run_storage_replay.py --pre-read direct --execute
python3 experiments/04_ours_memory_budget/run_storage_replay.py --pre-read none --execute
```

串行运行；省略 `--execute` 只显示计划。默认 `none` 不做额外预读，但不保证冷缓存；`direct` 在全部40档搜索之前顺序直接读取两个布局页文件，预读耗时不计入查询QPS，单独保存。选项仅作用于此诊断入口，不改变 `run.py` 的默认测量协议。

详见[原因、证据与协议说明](../../docs/diagnostics/OURS_QPS_STORAGE_RECOVERY_20260919.md)。
