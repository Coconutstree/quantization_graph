# 05的磁盘实验需要确认以及修改的方向

## 确认
### 1 数据的存放
- 目前的Ours方法里，磁盘是如何存放4bit的，是直接代替原数据存放在索引，原数据单独存放；还是索引里顺序存放原数据，4bit单独存放

### 2 数据的存放
- 构建的时候信息如何存放的，是所有信息都放在内存吗（图和数据等等），构建完再按照要求放入磁盘

### 3 现在目前磁盘实验的进程数量
- 01，02，03实验的进程数量都是多少

### 4 目前查询内存限制的最大使用量是多少（32/64）

### 5 分析为什么现在实验需要时间这么久的原因

## 方法修改
### 1 统计构建时间
- 02 和 03 实验的方法里，构建时候构建的所有信息都放在内存（其中包括构建的时间（包括距离计算次数，I/O（读取磁盘次数）），占用的内存（peak memory)

- 在ours的实验里面，构建完成以后，内存放1bit量化码。磁盘放4bit+邻接表/8bit(4bit+residual)+邻接表；全精度向量单独存放在磁盘；我们也要分开统计4bit和8bit的索引大小

### 2 统计查询时间

- 每个方法的查询，我们都需要统计recall,qps，以及I/O

- 对于Ours方法，I/O 需要精细到不同stage（第一部分1bit 初筛的I/O；4bit完整计算的I/O；加上4residual部分的I/O）

### 检测不同进程数的影响

- 1，2，4，8，16，32依次测试这几个进程数量

## 05 GIST 测试固定运行口径

- 以后 05B/05C 的查询曲线统一使用 47 个 `width`：`1-30`、`40-100`（步长 10）、`140-460`（步长 40），以及 `480`。
- 完整列表：`1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,40,50,60,70,80,90,100,140,180,220,260,300,340,380,420,460,480`。
- 05A 保留原来的完整 sweep；05B/05C 不再使用单个 `width` 测试点。
- 进程数按 `32,16,8,4,2,1` 的顺序运行，每个进程数完成后保存一套独立图片，避免被下一轮覆盖。

继续运行命令：

```bash
python3 experiments/05_disk_system_fair/run_05_gist_test.py \
  --dataset gist \
  --run-id gist_test_20260829_204245 \
  --workers-list 32,16,8,4,2,1 \
  --skip-doctor --skip-build \
  --exclude-methods SymphonyQG-DiskPort,DiskANN-PQ-Disk
```

运行完成后出图：

```bash
python3 experiments/05_disk_system_fair/plot_05_gist_test_memory_style.py \
  --out-root results/disk_environment/.test_runs/gist/gist_test_20260829_204245
```

- `--skip-doctor --skip-build` 用于跳过已经完成的检查和构建。
- 当前容器排除 SymphonyQG（validate 卡死）和 DiskANN-PQ（容器禁止 `io_uring`）；换到支持 `io_uring` 的正式机后，可以从 `--exclude-methods` 中移除 `DiskANN-PQ-Disk`。
- 恢复后的执行顺序：`validate_c`（Ours/OG-LVQ/Glass）→ 6 轮查询（32→16→8→4→2→1）→ summary → 出图。


