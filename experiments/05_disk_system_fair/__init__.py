"""05 disk-system fair experiment suite.

Layers
------
05A  fixed-candidate quantizer + payload-on-SSD micro-benchmark
05B  shared Vamana graph + unified search loop, disk mechanism experiment
05C  five end-to-end disk systems

Formal runs require an exclusive physical disk root (``--disk-root``) with
working O_DIRECT/native AIO. Use ``--disk-profile nvme`` for non-rotational
NVMe/SSD experiments, or ``--disk-profile hdd_raid`` for HDD/RAID
disk-resident experiments. The profile is recorded in run manifests.
"""

__version__ = "0.1.0"
