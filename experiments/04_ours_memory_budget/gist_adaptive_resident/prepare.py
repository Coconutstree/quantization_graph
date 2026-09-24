"""Isolated native build and on-demand encoding of the selected PCA prefix."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from policy import ROOT, HERE, WORK, OUT, ASSETS, calibrated_plan, dump, padded_dimension

BIN = WORK / 'ours_gist_adaptive_resident'
SOURCE = ROOT / 'work/ours_memory_budget/gist_pca_routing/native'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def replace(path, old, new):
    value = path.read_text()
    assert value.count(old) == 1, (path, old, value.count(old))
    path.write_text(value.replace(old, new))


def build():
    if BIN.exists():
        frozen = json.loads((OUT / 'build.json').read_text())
        assert sha(BIN) == frozen['binary_sha256']
        for path, digest in frozen['sources'].items():
            assert sha(path) == digest, path
        return
    native = WORK / 'native'
    if native.exists():
        raise RuntimeError('incomplete native build exists; inspect before rebuilding')
    prior = json.loads((ROOT / 'results/04_ours_memory_budget/gist_pca_routing/build.json').read_text())
    for path, digest in prior['sources'].items():
        assert sha(path) == digest, path
    shutil.copytree(SOURCE, native)
    manifest = native / 'Cargo.toml'
    replace(manifest, 'ours-gist-pca-routing', 'ours-gist-adaptive-resident')
    replace(manifest, 'ours_gist_pca_routing', BIN.name)
    pca = native / 'pca.rs'
    replace(pca, 'Ok(4*(d*k+d)+16*k*k+32*16*d)',
            'let dd=k.max(64).checked_next_power_of_two().ok_or("PCA dimension overflow")?;\n '
            'Ok(4*(d*k+d)+16*dd*dd+32*16*d)')
    replace(pca, '![128,256,512].contains(&k)', '(k==0||k>=d)')
    # This binary exposes only ranking for low-dimensional scores. No legacy
    # statistical or true-L2 bound can accidentally enable a pool-threshold gate.
    replace(pca, '!["none","norm","stat3","route"].contains(&tail_mode)', 'tail_mode!="route"')
    main = native / 'native_main.rs'
    replace(main, 'if codec.navigation_keep.is_some_and(|k|k>64){return Err("route shortlist must be 0(all)..64".into());}',
            'if codec.pca.is_some() && !matches!(codec.navigation_keep,Some(32)|Some(64)){return Err("adaptive routing requires M=32 or M=64".into());}\n '
            'if codec.pca.is_none() && codec.navigation_keep.is_some(){return Err("original resident route preserves its original search path".into());}')
    # A budgeted admission must never silently switch this method to paged.
    auto = native / 'auto_memory.rs'
    replace(auto, 'let mode=args.optional("--memory-policy").unwrap_or("auto");',
            'let mode=args.optional("--memory-policy").unwrap_or("auto");\n '
            'if mode!="resident" {return Err("adaptive resident routing requires resident admission".into());}')
    OUT.mkdir(parents=True, exist_ok=True)
    with (WORK / 'build.log').open('w') as log:
        subprocess.run(['cargo', 'build', '--release', '--offline', '--manifest-path', str(manifest),
                        '--target-dir', str(ROOT / 'src/graph_core/target')],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    shutil.copy2(ROOT / 'src/graph_core/target/release' / BIN.name, BIN)
    dump(OUT / 'build.json', dict(binary_sha256=sha(BIN),
         parent_binary_sha256=prior['binary_sha256'],
         sources={str(p): sha(p) for p in [*native.iterdir(), *(ASSETS / 'dependencies').rglob('*')]
                  if p.is_file()}))


def asset_for(dim):
    if dim == 960:
        return None
    old = ASSETS / f'd{dim}'
    return old if (old / 'encoded.json').exists() else WORK / f'd{dim}'


def encode(dim):
    if dim == 960:
        return None
    folder = asset_for(dim)
    if (folder / 'encoded.json').exists():
        value = json.loads((folder / 'encoded.json').read_text())
        for name, digest in value['files'].items():
            assert sha(folder / name) == digest, name
        meta = json.loads((folder / 'pca.json').read_text())
        assert meta['dim'] == dim and meta['code_stride'] == padded_dimension(dim) // 8
        return folder
    if folder.exists():
        raise RuntimeError(f'incomplete assets exist: {folder}')
    import numpy as np
    base = ROOT / 'data/gist/gist_base.fvecs'
    count = base.stat().st_size // 3844
    assert count == 1_000_000 and base.stat().st_size == count * 3844
    raw = np.memmap(base, dtype='<f4', mode='r', shape=(count, 961))
    mean = np.fromfile(ASSETS / 'mean.bin', dtype='<f4')
    basis = np.fromfile(ASSETS / 'basis.bin', dtype='<f4').reshape(960, 960)
    folder.mkdir(parents=True)
    with (folder / 'projected.bin').open('wb') as stream:
        for start in range(0, count, 2048):
            # Match runtime: float32 centering, float64 projection, float32 output.
            x = np.asarray(raw[start:start + 2048, 1:], dtype=np.float32) - mean
            (x.astype(np.float64) @ basis[:dim].T.astype(np.float64)).astype('<f4').tofile(stream)
    for name in ('mean.bin', 'basis.bin'):
        shutil.copy2(ASSETS / name, folder / name)
    dump(folder / 'pca.json', dict(dim=dim, original_dim=960, count=count, seed=17))
    subprocess.run([str(BIN), '--pca-export', str(folder)], check=True)
    meta = json.loads((folder / 'pca.json').read_text())
    assert meta['code_stride'] == padded_dimension(dim) // 8 and meta['factor_stride'] == 20
    dump(folder / 'encoded.json', dict(
         source_pca={name: sha(ASSETS / name) for name in ('mean.bin', 'basis.bin')},
         base_sha256=json.loads((ROOT / 'results/04_ours_memory_budget/gist_pca_budget/spectrum.json').read_text())['base_sha256'],
         projection='float32 centering, float64 matmul, float32 output',
         route='resident logical-ID order; no BFS copy or residual statistics',
         files={p.name: sha(p) for p in folder.iterdir() if p.is_file() and p.name != 'projected.bin'}))
    (folder / 'projected.bin').unlink()
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--budget-mib', type=float, default=538)
    args = parser.parse_args()
    plan = calibrated_plan(args.budget_mib)
    build()
    asset = encode(plan['dimension'])
    dump(OUT / f'plan_{args.budget_mib:g}.json', dict(**plan, asset=str(asset) if asset else None))
    print(json.dumps(dict(dimension=plan['dimension'], asset=str(asset), binary=str(BIN))))


if __name__ == '__main__':
    # Respect an externally supplied CPU quota during offline preparation.
    os.environ.setdefault('OPENBLAS_NUM_THREADS', '8')
    main()
