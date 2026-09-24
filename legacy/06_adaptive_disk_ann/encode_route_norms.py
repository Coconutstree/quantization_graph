"""Encode projected squared norms from base only; never rewrite existing route codes."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--base', type=Path, required=True)
p.add_argument('--projection-dir', type=Path, required=True)
p.add_argument('--output-dir', type=Path, required=True)
p.add_argument('--dimension', type=int, default=256)
a = p.parse_args()
mean = np.load(a.projection_dir / 'mean.f32.npy')
components = np.load(a.projection_dir / 'components.f32.npy')[:a.dimension]
if not 0 < a.dimension <= len(components):
    raise ValueError('invalid dimension')
data = np.memmap(a.base, dtype='<f4', mode='r')
if data.size % (mean.size + 1):
    raise ValueError('truncated fvecs')
data = data.reshape(-1, mean.size + 1)
if not np.all(data[:, 0].view('<i4') == mean.size):
    raise ValueError('invalid fvecs dimension headers')
a.output_dir.mkdir(parents=True, exist_ok=True)
dest = a.output_dir / f'norms_d{a.dimension}.f32'
with dest.open('xb') as f:
    for start in range(0, len(data), 8192):
        y = (data[start:start+8192, 1:] - mean) @ components.T
        norm = np.sum(y*y, axis=1, dtype=np.float64).astype('<f4')
        if not np.isfinite(norm).all():
            raise ValueError('nonfinite projected norm')
        norm.tofile(f)
        if start % 131072 == 0:
            print('rows', min(start+8192, len(data)), '/', len(data), flush=True)
def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8*1024*1024), b''):
            h.update(block)
    return h.hexdigest()
manifest = dict(rows=len(data), dimension=a.dimension, base=str(a.base),
                base_sha256=sha(a.base), norms_sha256=sha(dest),
                mean_sha256=sha(a.projection_dir/'mean.f32.npy'),
                components_sha256=sha(a.projection_dir/'components.f32.npy'),
                added_resident_bytes=4*len(data), uses_query_or_gt=False)
(a.output_dir/f'norms_d{a.dimension}.json').write_text(json.dumps(manifest, indent=2))
print('DONE', dest, flush=True)
