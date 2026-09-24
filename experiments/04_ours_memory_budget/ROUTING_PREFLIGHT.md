# 1-bit 常驻加载预检

原生 Ours 搜索在全量 sidecar 分配前读取并校验 quantizer/sidecar 文件头，
返回结构化 `RoutingPlan::Resident` 或 `RoutingPlan::Paged`。

常驻需求包括实际 codes、factors、centroid、线程工作区、布局加载峰值、
显式额外预留和安全预留；有效预算取指定预算与 RLIMIT_AS soft limit 的较小值。
不足时进入有界异步分页路径；最低分页运行预算也不足则提前返回错误。

完整行为、参数和验证协议见 [routing_paged/README.md](routing_paged/README.md)。
历史二进制不会自动更新，需要重新构建；已有实验结果保持原样。

`routing_preflight.py` 仍是独立的基础常驻估算工具：只读文件头，不启动搜索。
它不代替原生最终准入判断。其 other reserve 需包含布局及其他未单列开销，
调用者传入目标进程有效预算。未校准预留时，估算不是整个进程可成功运行的保证。
