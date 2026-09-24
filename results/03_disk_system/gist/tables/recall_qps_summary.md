# GIST 03 Recall–QPS summary

Current pair reference diagnostic: 4 GiB, beam 4, 32 workers, 800 test queries per width.
Ours-Disk and DiskANN-PQ-Disk are from `gist_aligned_20260922_03_pair`; Glass and SymphonyQG are preserved from the previously admitted GIST 03 run.
External parity for the new pair was stopped by user request; these replacement rows are therefore marked `formal_ready=false` and are not a formal admission claim.

| method | width | Recall@10 | QPS |
| --- | ---: | ---: | ---: |
| DiskANN-PQ-Disk | 10 | 71.800% | 2187.300 |
| DiskANN-PQ-Disk | 20 | 82.125% | 1673.382 |
| DiskANN-PQ-Disk | 40 | 90.450% | 1049.312 |
| DiskANN-PQ-Disk | 60 | 94.262% | 733.884 |
| DiskANN-PQ-Disk | 100 | 96.925% | 470.865 |
| DiskANN-PQ-Disk | 160 | 98.750% | 307.051 |
| DiskANN-PQ-Disk | 240 | 99.412% | 195.991 |
| DiskANN-PQ-Disk | 400 | 99.737% | 107.723 |
| DiskANN-PQ-Disk | 580 | 99.762% | 72.035 |
| Glass-NSG-DiskPort | 10 | 49.037% | 111.612 |
| Glass-NSG-DiskPort | 20 | 60.650% | 85.693 |
| Glass-NSG-DiskPort | 40 | 69.825% | 58.721 |
| Glass-NSG-DiskPort | 60 | 73.637% | 44.798 |
| Glass-NSG-DiskPort | 100 | 76.837% | 30.453 |
| Glass-NSG-DiskPort | 160 | 78.212% | 20.986 |
| Glass-NSG-DiskPort | 240 | 78.975% | 15.177 |
| Glass-NSG-DiskPort | 400 | 79.450% | 10.081 |
| Glass-NSG-DiskPort | 580 | 79.625% | 7.538 |
| Ours-Disk | 10 | 68.300% | 3403.330 |
| Ours-Disk | 20 | 79.325% | 2229.723 |
| Ours-Disk | 40 | 88.213% | 1427.052 |
| Ours-Disk | 60 | 91.900% | 1111.123 |
| Ours-Disk | 100 | 95.275% | 815.825 |
| Ours-Disk | 160 | 97.312% | 640.331 |
| Ours-Disk | 240 | 98.138% | 529.337 |
| Ours-Disk | 400 | 98.500% | 429.882 |
| Ours-Disk | 580 | 98.650% | 382.981 |
| SymphonyQG-DiskPort | 10 | 52.963% | 175.493 |
| SymphonyQG-DiskPort | 20 | 70.125% | 135.593 |
| SymphonyQG-DiskPort | 40 | 83.587% | 96.899 |
| SymphonyQG-DiskPort | 60 | 88.850% | 73.944 |
| SymphonyQG-DiskPort | 100 | 93.737% | 55.525 |
| SymphonyQG-DiskPort | 160 | 96.362% | 41.650 |
| SymphonyQG-DiskPort | 240 | 97.875% | 32.486 |
| SymphonyQG-DiskPort | 400 | 98.912% | 24.407 |
| SymphonyQG-DiskPort | 580 | 99.325% | 20.196 |
