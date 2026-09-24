# AiSAQ 无补丁运行环境核验

日期：2026-09-16。目标：保留官方算法与源码，检查原生设备查询的运行条件。

## 已核实的环境事实

- 实际检查的索引位置：`work/05_official_baseline_smoke_aisaq_sysfs`，文件系统设备号为 `253:2`。
- 沙箱内该路径是 XFS bind mount，`/dev/block`、`/dev/mapper`、`/dev/dm-2` 不可见；sysfs 中设备可见。
- 沙箱外主机上，`/home` 挂载源为 `/dev/mapper/ubuntu--vg-ubuntu--home`；`/dev/block/253:2` 正确链接到 `/dev/dm-2`。
- `/dev/dm-2` 权限为 `brw-rw---- root disk`；当前账号 `kai3`（UID 8017）不在 `disk` 组。
- 主机上以当前账号实际调用 `open('/dev/block/253:2', O_RDONLY)` 返回 `errno=13, Permission denied`，因此尚未执行到 `BLKSSZGET` ioctl。
- sysfs 报告该逻辑设备扇区大小为 512 B；该信息用于诊断，未注入或替代官方程序的查询。
- `sudo -n -l` 返回需要密码；本会话没有可直接使用的管理员执行权限。
- sysfs 中该 device-mapper 设备的底层依赖包含 `sda3`。本次未确认其物理介质型号，不能仅凭路径把它描述成已核实的目标 NVMe SSD。正式实验若更换索引位置，须重新核验设备映射。

沙箱外检查经过执行权限审核并成功完成；当前阻塞来自主机设备访问权限，不是工具自动审批拒绝。

## 独立无补丁验证目录

`work/aisaq_official_unpatched_20260916/`：

- `source/`：独立 checkout，提交 `f0a48e984c685bd498e3c4f88386b47e0e4ab1ac`；创建后 `git status --porcelain` 为空。未复制现有工作区的 sysfs 补丁。
- `build/`：独立 Release 构建目录，目标 `build_disk_index`、`search_disk_index`。
- `compile.log`：编译日志。使用项目内解包的 liburing 开发文件，仅通过编译/链接参数指定位置。
- `commands.json`：官方小样本建索引与搜索命令，启用 `--use_aisaq`，独立输出位置。
- `run_smoke.py`：检查源码提交与洁净状态，复制原始小样本输入，以无补丁二进制重新建索引和搜索，记录退出码和二进制哈希。
- `validation.json`、`smoke/build.log`、`smoke/search.log`：执行后生成的验证结果与日志。

这套验证不覆盖正式数据集、2 GiB 限制或正式性能验收。现有补丁 checkout 和历史结果保持原样；正式方案不自动复用补丁版结果。

## 所需环境操作

应在设备节点可见且官方程序可读该设备的环境中运行无补丁程序。当前主机节点已存在，无需创建虚假设备节点或伪造扇区大小。

剩余条件需要管理员协助：由管理员按机器的访问政策提供运行环境或执行经审查的原版验证命令。若选择赋予设备读权限，需要认识到这是底层卷的原始读取权限，范围超过单个索引文件；本次没有改设备权限、加入 disk 组或授予此访问。

管理员条件满足后，以实验账号重新验证 `open` 和 `BLKSSZGET`，再运行官方搜索并检查结果。不要将 Python 驱动脚本整体以 root 执行作为默认解决方案。若无法提供合适访问环境，保持 AiSAQ 正式运行待完成，不恢复补丁作为替代验收。

## 实际执行结果

无补丁 Release 编译成功。主机环境中小样本（1024 条、64 维）官方建索引退出码为 0；官方搜索退出码为 255，日志最后报告 `failed to detect PQ vectors file block size`。结合独立的只读 open 探测，确认本次搜索受到设备读权限阻挡。源码在构建和运行后仍为干净 checkout。

- [验证清单与二进制哈希](aisaq_unpatched_validation.json)
- [官方搜索失败日志](aisaq_unpatched_search.log)

当前结论：无补丁程序可构建且可建索引，但尚未在当前账号下完成搜索；未验证搜索结果正确性，也未通过正式实验验收。待管理员处理访问条件后，可用 `commands.json` 中的第二条命令对已生成的独立索引重新运行搜索，使用新的结果前缀和日志文件保存重试证据。无需重复建索引或改动源码。
