# 2026-09-18 results 目录迁移记录

当前主结果入口统一为 `results/01_disk_quantizer/`、`results/02_disk_shared_graph/`、`results/03_disk_system/`，内部按数据集和 run-id 划分。运行器与绘图器使用同一套路径函数；取消新运行的 published 复制目录。公共协议和环境清单位于 `results/manifests/<run-id>/`。

图、索引及查询划分移至 `artifacts/`；诊断进入 `results/diagnostics/`；旧内存实验及其他历史材料归入 `results/archive/`。`data/` 未修改。迁移使用同一文件系统内的重命名，不复制大型索引。

## 证据保留

迁移前清单记录 6,930 个普通文件。迁移后逐一核对设备号、inode、大小、纳秒修改时间；其中不超过 1 MiB 的 6,401 个文件额外逐一核对 SHA-256，全部一致。没有对大型文件重新扫描全量哈希，因此完整性结论依赖同文件系统重命名以及文件身份与元数据未变。35 个原有软链接及 51 个目录兼容链接均可解析。

原始 JSON/CSV 未修改。旧路径由 `results/manifests/path_migration_20260918.json` 记录；读取证据时按最长匹配前缀解析，新路径已存在时优先使用新路径。命令行辅助工具为 `scripts/resolve_result_path.py`。历史独立脚本并非都支持自动映射，正式入口以 README 为准。

历史三层目录没有一致的 run 划分，归入 `legacy_snapshot_20260918`，新增清单明确标记 `not_revalidated`、`formal_ready=false`。`test_L_400_w_32` 保留原始 `pending` 状态。目录位置不代表已通过正式验收，历史导入材料不能直接绕过绘图检查。

## 检查

检查记录在 `docs/validation/results_layout_20260918/`：

- `integrity_final.json`：所有原始文件及链接检查无错误。
- `build.log`：C++/Rust 正式目标重新构建完成；`registry.log`：刷新本地二进制哈希登记。
- `layout_tests.log`：12 项布局、入口和历史路径测试通过。
- `contract_tests.log`：10/10 协议契约检查通过。
- `storage_tests.log`：20/20 存储与数据策略检查通过。
- `protocol_tests.log`：27 项测量协议测试，26 通过、1 项因缺少可写 cgroup 跳过。
- `native_tests.log`：宿主运行 45 项 Python 磁盘测试，44 通过、同一 cgroup 测试跳过。
- `ctest.log`：5/5 C++ 测试通过。
- `doctor01.log`、`doctor02.log`：GIST 检查分别 24/24、26/26 通过。
- `doctor03.log`：23 项通过，端口检查失败；AiSAQ/Starling 缺少原有正式逐查询、内存和验收证据，仍为 pending。

本次没有重新运行正式论文性能实验。既有性能值、方法准入标准及原始验收状态未因目录整理而提升。
