# Results Layout

`results/` is organized by execution environment first.

```text
results/
├── memory_environment/
│   ├── 01_quantizer_fair/
│   ├── 02_diskann_fair/
│   ├── 03_system_fair/
│   ├── dataset_artifacts/
│   ├── diagnostics/
│   ├── paper_tables/
│   └── archive/
└── disk_environment/
    ├── 01_quantizer_fair/
    ├── 02_diskann_fair/
    ├── 03_system_fair/
    ├── .formal_runs/
    ├── .test_runs/
    └── archive/
```

The disk 05A/05B/05C runs are published under the matching numbered disk
folders:

- `05A_disk_quantizer_io` -> `disk_environment/01_quantizer_fair`
- `05B_diskann_shared_graph` -> `disk_environment/02_diskann_fair`
- `05C_disk_system_fair` -> `disk_environment/03_system_fair`

Legacy disk-suite raw outputs live under
`results/disk_environment/archive/05_disk_system_fair_legacy/`.

Some old diagnostic query-codec result folders may still be visible directly
under `results/` when they are owned by `nobody:nogroup`; move them into
`results/memory_environment/diagnostics/` after fixing ownership.
