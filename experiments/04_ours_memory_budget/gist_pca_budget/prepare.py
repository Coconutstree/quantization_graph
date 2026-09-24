"""Independent PCA experiment; never restore the retired PCA/NNP prototype."""
import argparse, hashlib, json, shutil, struct, subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[3]
WORK=ROOT/'work/ours_memory_budget/gist_pca_budget'
OUT=ROOT/'results/04_ours_memory_budget/gist_pca_budget'
HERE=Path(__file__).resolve().parent
DIMS=(128,256,512)
def dump(p,x): p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2)+'\n')
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def fit():
 WORK.mkdir(parents=True,exist_ok=True);OUT.mkdir(parents=True,exist_ok=True)
 if (OUT/'spectrum.json').exists():return
 base=ROOT/'data/gist/gist_base.fvecs';d=960;n=base.stat().st_size//(4*(d+1))
 raw=np.memmap(base,dtype='<f4',mode='r',shape=(n,d+1))
 ids=np.sort(np.random.default_rng(20260921).choice(n,32768,replace=False))
 x=np.asarray(raw[ids,1:],dtype=np.float64);mean=x.mean(0);x-=mean
 cov=x.T@x/(len(x)-1);vals,vec=np.linalg.eigh(cov);vals=vals[::-1];basis=vec[:,::-1].T.copy()
 assert np.max(np.abs(basis@basis.T-np.eye(d)))<1e-10
 mean.astype('<f4').tofile(WORK/'mean.bin');basis.astype('<f4').tofile(WORK/'basis.bin');vals.astype('<f4').tofile(WORK/'variance.bin');ids.astype('<u4').tofile(WORK/'sample_ids.bin')
 dump(OUT/'spectrum.json',dict(base_sha256=sha(base),sample_count=len(ids),seed=20260921,fit_source='base only; no query/groundtruth',eigenvalues=vals.tolist(),retained={str(k):float(vals[:k].sum()/vals.sum()) for k in (*DIMS,d)},source_hashes={p.name:sha(p) for p in WORK.glob('*.bin')}))
def encode():
 base=ROOT/'data/gist/gist_base.fvecs';n=base.stat().st_size//3844
 raw=np.memmap(base,dtype='<f4',mode='r',shape=(n,961))
 mean=np.fromfile(WORK/'mean.bin',dtype='<f4');basis=np.fromfile(WORK/'basis.bin',dtype='<f4').reshape(960,960)
 for k in DIMS:
  folder=WORK/f'd{k}';folder.mkdir(exist_ok=True)
  if (folder/'encoded.json').exists():continue
  print('project',k,flush=True)
  with (folder/'projected.bin').open('wb') as f,(folder/'residual.bin').open('wb') as r:
   for start in range(0,n,4096):
    x=np.asarray(raw[start:start+4096,1:],dtype=np.float32)-mean;y=x@basis[:k].T
    y.astype('<f4').tofile(f)
    np.maximum(np.sum(x*x,axis=1,dtype=np.float64)-np.sum(y*y,axis=1,dtype=np.float64),0).astype('<f4').tofile(r)
  for name in ('mean.bin','basis.bin','variance.bin'):shutil.copy2(WORK/name,folder/name)
  dump(folder/'pca.json',dict(dim=k,original_dim=960,count=n,seed=17))
  print('native encode',k,flush=True)
  subprocess.run([str(WORK/'ours_gist_pca_budget'),'--pca-export',str(folder)],check=True)
  m=json.loads((folder/'pca.json').read_text());stride=m['code_stride'];codebytes=n*stride
  cmd=json.loads((ROOT/'results/04_ours_memory_budget/gist_route_locality/test/original_i256_r1/command.json').read_text());flags=dict(zip(cmd[1::2],cmd[2::2]))
  mapping=Path(flags['--locality-layout-dir'])/'id_to_slot.u32';slots=np.fromfile(mapping,dtype='<u4')
  assert len(slots)==n and np.array_equal(np.sort(slots),np.arange(n))
  src=folder/'sidecar.bin';dst=folder/'sidecar_bfs.bin';shutil.copy2(src,dst)
  old=np.memmap(src,dtype='u1',mode='r',offset=24,shape=(n,stride));new=np.memmap(dst,dtype='u1',mode='r+',offset=24,shape=(n,stride))
  for start in range(0,n,8192):new[slots[start:start+8192]]=old[start:start+8192]
  new.flush()
  for start in range(0,n,8192):assert np.array_equal(new[slots[start:start+8192]],old[start:start+8192])
  dump(folder/'encoded.json',dict(mapping_sha256=sha(mapping),all_code_rows_equal=True,files={p.name:sha(p) for p in folder.iterdir() if p.is_file() and p.name not in ('projected.bin','encoded.json')}))
  (folder/'projected.bin').unlink()

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--encode',action='store_true');args=parser.parse_args()
 fit()
 if args.encode:
  encode()
  dump(OUT/'preparation.json',dict(status='encoded',dimensions=list(DIMS),binary_sha256=sha(WORK/'ours_gist_pca_budget')))
if __name__=='__main__':main()
