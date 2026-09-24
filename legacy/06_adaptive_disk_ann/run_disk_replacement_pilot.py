"""Run serial direct-I/O pilots on an explicit AGNews query slice (not formal results)."""
import hashlib
import json
import pathlib
import struct
import subprocess
import time
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--dataset', choices=('agnews','gist','dbpedia'), default='agnews')
parser.add_argument('--route-dir', type=pathlib.Path)
parser.add_argument('--widths', default='49')
parser.add_argument('--trace-neighbors', action='store_true', help='Diagnostic only; resulting QPS is not benchmark data')
parser.add_argument('--queries', type=int, default=1)
parser.add_argument('--query-offset', type=int, default=0)
parser.add_argument('--norm-dir', type=pathlib.Path)
parser.add_argument('--budget-gib', type=float, default=2.0)
parser.add_argument('--cache-mode', choices=('c0', 'standard'), default='standard')
parser.add_argument('--variants', default='baseline,d64',
                    help='Comma separated baseline,d64,d256_k32_t100 etc.; k0 disables cap, t100 is projected threshold ratio 1.0')
args = parser.parse_args()
if args.queries < 1 or args.budget_gib <= 0 or args.query_offset < 0:
    parser.error('queries and budget must be positive')

ROOT = pathlib.Path(__file__).resolve().parents[2]
source = ROOT / 'results/disk_environment/.formal_runs/runs/agnews_05c_rerun_20260901_152210/05C_disk_system_fair/agnews/artifacts/test/Ours-Disk__hybrid_disk__B2__standard__w32__r0.terminal.log'
argv = json.loads(source.read_text().splitlines()[0].removeprefix('argv='))
base = dict(zip(argv[1::2], argv[2::2]))
if args.dataset != 'agnews':
    source = ROOT / f'results/disk_environment/.formal_runs/runs/disk_03_graph_build_reuse_20260910_{args.dataset}/05C_disk_system_fair/{args.dataset}/artifacts/export/Ours-Disk__hybrid_disk__B2__c0__w1__r0.terminal.log'
    argv = json.loads(source.read_text().splitlines()[0].removeprefix('argv='))
    base = dict(zip(argv[1::2], argv[2::2]))
    base['--phase'] = 'test'
base['--query'] = str(ROOT / f'data/{args.dataset}/{args.dataset}_query.fvecs')
base['--groundtruth'] = str(ROOT / f'data/{args.dataset}/{args.dataset}_groundtruth.ivecs')
base['--ours-graph'] = str(ROOT / f'results/graph/{args.dataset}/Ours/{args.dataset}_Ours_R64_Lbuild400.graph.bin')
out = ROOT / f'results/disk_environment/06_adaptive_disk_ann/{args.dataset}' / (time.strftime('disk_replacement_%Y%m%d_%H%M%S') + f'_{time.time_ns()%1000000000:09d}')
out.mkdir(parents=True)
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
for key, name in [('--query', 'query.fvecs'), ('--groundtruth', 'gt.ivecs')]:
    with pathlib.Path(base[key]).open('rb') as f:
        header = f.read(4)
        row_bytes = (struct.unpack('<I', header)[0] + 1) * 4
        f.seek(args.query_offset * row_bytes)
        row = f.read(args.queries * row_bytes)
        if len(row) != args.queries * row_bytes:
            raise ValueError('query slice exceeds input rows: ' + base[key])
    dest = out / name
    dest.write_bytes(row)
    base[key] = str(dest)
order = out / 'order.u32'
order.write_bytes(struct.pack('<' + 'I' * args.queries, *range(args.queries)))
base.update({'--query-order': str(order), '--query-order-sha256': sha(order),
             '--query-split-sha256': sha(out / 'query.fvecs'),
             '--workers': '32', '--warmup-queries': '0', '--cache-mode': args.cache_mode,
             '--search-dram-budget-gib': str(args.budget_gib),
             '--integration-widths': args.widths, '--integration-beams': '1',
             '--native-binary-sha256': sha(pathlib.Path(argv[0])),
             '--git-commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()})
print(out, flush=True)
(out / 'pilot_config.json').write_text(json.dumps(vars(args), indent=2, default=str))
for variant in args.variants.split(','):
    flags = dict(base)
    flags.update({'--run-id': 'native_integration_adaptive_' + variant,
                  '--implementation-fingerprint': 'adaptive-replacement-pilot-' + variant,
                  '--result-json': str(out / (variant + '.json')),
                  '--query-trace': str(out / (variant + '.queries.jsonl'))})
    if variant != 'baseline':
        import re
        match = re.fullmatch(r'd(64|128|256|512|1024)(?:_k(\d+))?(?:_t(\d+))?(?:_(n|c))?(_rv)?', variant)
        if not match:
            raise ValueError('invalid variant: ' + variant)
        flags.update({'--adaptive-route-dir': str(ROOT / 'results/disk_environment/06_adaptive_disk_ann/agnews/full_route_encode_20260909/codes_nnp'),
                      '--adaptive-route-dim': match[1],
                      '--adaptive-route-keep': match[2] or '0',
                      '--adaptive-route-ratio': str(int(match[3]) / 100) if match[3] else '0'})
        flags['--adaptive-route-revisit'] = '1' if match[5] else '0'
        if args.trace_neighbors:
            flags['--adaptive-route-trace-dir'] = str(out / (variant + '.neighbors'))
        if args.route_dir:
            flags['--adaptive-route-dir'] = str(args.route_dir.resolve())
        elif args.dataset != 'agnews':
            raise ValueError('new dataset requires its own --route-dir')
        if match[4]:
            if args.norm_dir is None:
                raise ValueError('norm correction requires --norm-dir')
            flags['--adaptive-route-norms'] = str(args.norm_dir / f'norms_d{match[1]}.f32')
            flags['--adaptive-route-calibrate'] = '1' if match[4] == 'c' else '0'
            flags['--adaptive-route-norms-sha256'] = sha(pathlib.Path(flags['--adaptive-route-norms']))
            manifest = json.loads((args.norm_dir / f'norms_d{match[1]}.json').read_text())
            if manifest['dimension'] != int(match[1]) or manifest['norms_sha256'] != flags['--adaptive-route-norms-sha256']:
                raise ValueError('route norm manifest mismatch')
            projection = pathlib.Path(flags['--adaptive-route-dir']).parent / 'projection_nnp'
            if manifest['mean_sha256'] != sha(projection / 'mean.f32.npy') or manifest['components_sha256'] != sha(projection / 'components.f32.npy'):
                raise ValueError('norms were encoded using a different projection')
    cmd = [argv[0]] + [v for pair in flags.items() for v in pair]
    (out / (variant + '.argv.json')).write_text(json.dumps(cmd, indent=2))
    print('START', variant, flush=True)
    with (out / (variant + '.log')).open('w') as log:
        result = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    print('EXIT', variant, result.returncode, flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)
