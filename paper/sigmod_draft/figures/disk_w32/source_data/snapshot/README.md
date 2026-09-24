# Current result locations

- `agnews/`: new acceptance-run measurements; `_references/` contains comparisons.
- `gist/`: latest existing GIST measurements, using the OLD Ours layout/binary.
  Relocation is not a locality rerun or formal acceptance.
- `dbpedia/`, `sift10m/`: subsequent queue output destinations.
- `exports/`: plots and CSV from the new AGNews run only.
- `_runtime/`: preserved commands, pinned binaries, queue state and historical
  dependencies. Not a second set of current paper results.

The two previous root paths are compatibility symlinks, not duplicated storage.
Running processes and provenance documents still reference those paths; do not
remove the links until those dependencies have been retired.
See `migration.json` for the physical moves and inode identity checks.

## Timing warning

Live processes were briefly stopped during relocation on 2026-09-12.
DBpedia started prematurely and was paused after detection, before AGNews
finished its DiskANN disk sweep. Therefore AGNews DiskANN tail measurements
(conservatively widths 540 and 580) and the initial DBpedia measurement require
clean remeasurement before formal acceptance. Exact interference duration was
not measured. Do not interpret a successful parity check as timing acceptance.
DBpedia resumes only after the AGNews acceptance supervisor exits.
