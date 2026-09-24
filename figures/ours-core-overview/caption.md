**Figure 2. Primary-derived routing for selective SSD access.**
(A) The most-significant bit (MSB) of each primary 4-bit code coordinate is extracted to form a 1-bit routing view, stored with scoring factors in DRAM. The complete primary code remains on SSD; no separate 1-bit quantizer is trained.
(B) Asymmetric screening evaluates newly discovered graph neighbors before requesting their fine payloads. Surviving candidates are scored using the primary representation to update the search frontier. Graph adjacency is SSD-backed; screening acts on neighbors after their discovery, not before all graph I/O.
(C) The derived 1-bit view supports screening, the complete primary representation supports navigation, and primary plus residual 4-bit information refines the final candidate shortlist.
The figure shows the resident-routing configuration. Queries enter as real-valued vectors; the current disk implementation uses an INT8-derived query representation for gate/navigation kernels. Bit strings and candidate counts are illustrative. Logical refinement stages do not imply separate physical reads, and multiple candidate records can share an SSD page.

中文说明：蓝色最高位直接取自 primary，橙色 residual 表示对 primary 重构的残差修正。筛选针对候选节点，读取以页面为单位。此图不承诺零 Recall 损失或固定 I/O 改善比例；分页 routing、热点缓存及调度策略不在本图范围内。
