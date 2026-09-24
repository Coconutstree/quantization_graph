"""Planned RAM budget with observed process RSS admission; no privileged setup."""
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from disk_bench.memory_runner import run_measured
from cgroup_memory import dump, host_snapshot, classify


def measure(command, folder, root, budget, timeout=86400):
    if root is not None:
        raise ValueError('RSS budget mode does not accept a cgroup root')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    dump(folder / 'host_before.json', host_snapshot())
    dump(folder / 'command.json', command)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('QG05_') and k not in ('LD_PRELOAD', 'LD_AUDIT')}
    env.update(MALLOC_ARENA_MAX='2', OMP_DYNAMIC='FALSE', OPENBLAS_NUM_THREADS='1',
               MKL_NUM_THREADS='1', QG05_MEASURE_WHOLE_PROCESS='1')
    try:
        e = run_measured(command, evidence_path=folder/'resources.json',
                         log_path=folder/'terminal.log', budget_bytes=budget,
                         rss_budget=True, timeout=timeout, env=env)
        state = e['status']
        if state == 'runtime_error':
            state = classify(e['exit_code'], {}, (folder/'terminal.log').read_text())
        shutil.copyfile(e['rss_samples_path'], folder/'rss_samples.jsonl')
        e.update(classification=state, budget_bytes=budget,
                 scope='planned_process_ram_budget_rss_admission')
        dump(folder/'memory_measurement.json', e)
        return e
    finally:
        dump(folder/'host_after.json', host_snapshot())
