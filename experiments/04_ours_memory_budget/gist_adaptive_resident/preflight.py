"""Cross-check the Python selector with the compiled native byte admission."""
import json
import os
import resource
import subprocess
from policy import OUT, MIB, calibrated_plan, dump
from prepare import BIN, asset_for, sha


def main():
    command = json.loads((OUT / 'tune/b538_d128_m32_grid_r1/command.json').read_text())
    template = dict(zip(command[1::2], command[2::2]))
    rows = []
    for budget, override in [(538, None), (542, None), (550, None), (580, None), (632, None), (542, 256)]:
        selection = calibrated_plan(budget)
        dim = override or selection['dimension']
        folder = OUT / 'preflight' / f'b{budget}_d{dim}'
        folder.mkdir(parents=True, exist_ok=True)
        flags = dict(template)
        flags.update({'--search-dram-budget-gib': repr(budget / 1024), '--preflight-only': '1',
                      '--auto-plan-output': str(folder / 'memory_plan.json'),
                      '--record-cache-budget-bytes': str(0 if override else selection['record_cache_bytes'])})
        for key in ('--pca-route-dir', '--pca-tail-mode', '--pca-route-keep'):
            flags.pop(key, None)
        if dim != 960:
            flags.update({'--pca-route-dir': str(asset_for(dim)), '--pca-tail-mode': 'route', '--pca-route-keep': '32'})
        cmd = [str(BIN)] + [v for key, value in flags.items() for v in (key, value)]
        dump(folder / 'command.json', cmd)
        def limits():
            resource.setrlimit(resource.RLIMIT_AS, (budget * MIB, budget * MIB))
        env = dict(os.environ, MALLOC_ARENA_MAX='2', OMP_DYNAMIC='FALSE', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
        with (folder / 'terminal.log').open('w') as log:
            result = subprocess.run(cmd, env=env, preexec_fn=limits, stdout=log, stderr=subprocess.STDOUT)
        native = json.loads((folder / 'memory_plan.json').read_text())
        if override:
            assert result.returncode != 0 and native['status'] == 'admission_rejected'
            assert native['threshold_resident_bytes'] - budget * MIB == 1024
        else:
            assert result.returncode == 0 and native['status'] == 'admitted'
            for key in ('admission_bytes', 'codes_bytes', 'factors_bytes', 'reserve_bytes'):
                assert selection[key] == native[key], (budget, key)
            assert native['routing_capacity_pages'] == 0
        rows.append(dict(budget_mib=budget, dimension=dim, selected=override is None,
                         status=native['status'], exit_code=result.returncode,
                         memory_plan_sha256=sha(folder / 'memory_plan.json')))
    dump(OUT / 'preflight.json', dict(passed=True, rows=rows, binary_sha256=sha(BIN)))
    print('native preflight passed:', len(rows), 'cases')


if __name__ == '__main__':
    main()
