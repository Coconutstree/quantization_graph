"""Separate statistical native-gate failures from deterministic PCA-tail bounds."""
import json,struct,subprocess
import numpy as np
from prepare import ROOT,WORK,OUT,dump,sha
def fvecs(p):
 with open(p,'rb') as f:d=struct.unpack('<I',f.read(4))[0]
 return np.memmap(p,dtype='<f4',mode='r').reshape(-1,d+1)[:,1:]
def main():
 folder=OUT/'bound_audit_v3';folder.mkdir(exist_ok=True)
 base=fvecs(ROOT/'data/gist/gist_base.fvecs')
 split=ROOT/'results/04_ours_memory_budget/hybrid_tuning/splits/train'
 queries=np.array(fvecs(split/'validation_query.fvecs'),dtype=np.float32)
 gt=np.fromfile(split/'validation_gt.ivecs',dtype='<i4');gt=gt.reshape(-1,int(gt[0])+1)[:,1:11]
 rng=np.random.default_rng(20260921);random_ids=rng.choice(len(base),512,replace=False)
 ids=[np.concatenate([random_ids,truth]) for truth in gt]
 labels=['validation_train']*len(queries)
 self_ids=random_ids[:16];queries=np.vstack([queries,np.array(base[self_ids])]);ids += [np.array([i]) for i in self_ids];labels+=['self']*len(self_ids)
 with (folder/'queries.fvecs').open('wb') as f,(folder/'ids.bin').open('wb') as g:
  for q,ix in zip(queries,ids):f.write(struct.pack('<I',len(q)));q.astype('<f4').tofile(f);g.write(struct.pack('<I',len(ix)));ix.astype('<u4').tofile(g)
 unique=np.unique(np.concatenate(ids));x=np.array(base[unique],dtype=np.float64)
 mean=np.fromfile(WORK/'mean.bin',dtype='<f4').astype(np.float64);basis=np.fromfile(WORK/'basis.bin',dtype='<f4').reshape(960,960).astype(np.float64)
 variance=np.fromfile(WORK/'variance.bin',dtype='<f4').astype(np.float64)
 y=(x-mean)@basis.T;z=(queries.astype(np.float64)-mean)@basis.T
 command=json.loads((ROOT/'results/04_ours_memory_budget/gist_route_locality/tune/original_i256_r1/command.json').read_text());flags=dict(zip(command[1::2],command[2::2]));index=flags['--disk-index-dir']
 allrows=[]
 for k in (128,256,512,960):
  output=folder/f'bounds_d{k}.f32';directory='original' if k==960 else str(WORK/f'd{k}')
  cmd=[str(WORK/'ours_gist_pca_budget'),'--pca-audit',index,directory,str(folder/'queries.fvecs'),str(folder/'ids.bin'),str(output)]
  subprocess.run(cmd,check=True);bounds=np.fromfile(output,dtype='<f4').astype(np.float64)
  offsets=np.cumsum([0]+[len(ix) for ix in ids]);assert len(bounds)==offsets[-1]
  counts={name:0 for name in ('pairs','native_projected_violations','native_full_violations','norm_corrected_violations','stat3_corrected_violations','norm_tail_only_violations','stat3_tail_only_violations','self_violations')};worst=0.;worst_tail=0.
  for qi,ix in enumerate(ids):
   at=np.searchsorted(unique,ix);full=np.sum((x[at]-queries[qi])**2,axis=1);projected=np.sum((y[at,:k]-z[qi,:k])**2,axis=1)
   true_tail=np.sum((y[at,k:]-z[qi,k:])**2,axis=1);xn=np.sum(y[at,k:]**2,axis=1);qn=np.sum(z[qi,k:]**2)
   norm=(np.sqrt(xn)-np.sqrt(qn))**2;sigma=np.sqrt(np.sum(variance[k:]*z[qi,k:]**2));stat=np.maximum(norm,xn+qn-6*sigma)
   b=bounds[offsets[qi]:offsets[qi+1]];tol=1e-5*np.maximum(1.,full)
   counts['pairs']+=len(ix);counts['native_projected_violations']+=int(np.sum(b>projected+tol));counts['native_full_violations']+=int(np.sum(b>full+tol));counts['norm_corrected_violations']+=int(np.sum(b+norm>full+tol));counts['stat3_corrected_violations']+=int(np.sum(b+stat>full+tol));counts['norm_tail_only_violations']+=int(np.sum(norm>true_tail+tol));counts['stat3_tail_only_violations']+=int(np.sum(stat>true_tail+tol))
   if labels[qi]=='self':counts['self_violations']+=int(np.sum(b+norm>full+tol))
   worst=max(worst,float(np.max(b+norm-full)));worst_tail=max(worst_tail,float(np.max(stat-true_tail)))
  deterministic_violations=None;deterministic_excess=None
  if k!=960:
   doutput=folder/f'deterministic_d{k}.f32'
   subprocess.run(cmd[:6]+[str(doutput),'norm'],check=True)
   db=np.fromfile(doutput,dtype='<f4').astype(np.float64);deterministic_violations=0;deterministic_excess=0.
   for qi,ix in enumerate(ids):
    at=np.searchsorted(unique,ix);full=np.sum((x[at]-queries[qi])**2,axis=1);v=db[offsets[qi]:offsets[qi+1]]
    deterministic_violations+=int(np.sum(v>full+1e-5*np.maximum(1.,full)));deterministic_excess=max(deterministic_excess,float(np.max(v-full)))
  allrows.append(dict(dim=k,**counts,max_norm_corrected_excess=worst,max_stat3_tail_excess=worst_tail,deterministic_full_violations=deterministic_violations,deterministic_max_excess=deterministic_excess))
 # Exact counterexample: collinear discarded coordinates have zero residual distance,
 # while a three-sigma statistical correction can be strictly positive.
 counterexample=dict(residual_x=[10.,0.],residual_q=[10.,0.],tail_variance=[1.,1.],true_tail=0.,norm_bound=0.,stat3_bound=140.)
 proof=dict(formula='L_total=L_projected+(norm(x_tail)-norm(q_tail))^2',condition='orthogonal projection and valid L_projected; then total bound valid. Original native epsilon=1.9 gate is statistical, not unconditional.',finite_sample_not_proof=True,rows=allrows,counterexample=counterexample,binary_sha256=sha(WORK/'ours_gist_pca_budget'),basis_orthogonality_max_abs_error=float(np.max(np.abs(basis@basis.T-np.eye(960)))))
 dump(folder/'audit.json',proof)
 print(json.dumps(proof,indent=2))
if __name__=='__main__':main()
