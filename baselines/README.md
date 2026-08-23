# Repository-local formal baselines

Formal experiments resolve third-party implementations only from this
directory. Pinned source revisions are recorded in `DEPENDENCY_LOCK.json`.
The non-system C++ runtime dependencies used by SAQ (`glog` and `fmt`) are
also installed under `baselines/deps/local`; its executable must not contain
a `RUNPATH` to a sibling checkout.

Large generated inputs and indexes are deliberately separate from source:

- common raw datasets live in `data/<dataset>/`;
- reproducible intermediate data live in `work/`;
- 05 disk indexes live under the explicit NVMe `--disk-root`;
- result logs and figures live in `results/`.

SAQ's existing PCA/IVF inputs are exposed at `baselines/saq/data` using
same-filesystem hard links, so all runtime paths are repository-local without
duplicating 15 GiB. New or regenerated SAQ artifacts must use `work/`.

No runner should silently fall back to a sibling checkout. Environment or
CMake overrides remain explicit opt-ins for development only and must be
recorded in formal manifests.
