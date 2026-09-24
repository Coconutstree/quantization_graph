"""05 disk-system fair experiment suite.

Layers
------
05A  fixed-candidate quantizer + payload-on-SSD micro-benchmark
05B  shared Vamana graph + unified search loop, disk mechanism experiment
05C  five end-to-end disk systems

Formal runs require a physical disk root (``--disk-root``) with working
O_DIRECT/native AIO. Use ``--disk-profile auto`` to infer non-rotational
NVMe/SSD versus HDD/RAID from the backing device, or pass ``nvme`` /
``hdd_raid`` explicitly. The profile is recorded in run manifests.
"""

__version__ = "0.1.0"
