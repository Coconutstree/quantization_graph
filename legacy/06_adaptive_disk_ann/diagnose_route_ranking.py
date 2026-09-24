"""Streaming exhaustive ranking diagnostic, NOT graph-search throughput.

Separate projection and sign-code ranking loss on the same full GIST base.
Projected float rows are temporary batches, not claimed as a resident ANN code.
"""
import argparse
import json
from pathlib import Path
import resource
import struct
import numpy as np

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--queries',type=int,default=100)
p.add_argument('--offset',type=int,default=100)
a=p.parse_args()
root=Path(__file__).resolve().parents[2]
routes=root/'results/disk_environment/06_adaptive_disk_ann/gist/cross_dataset_v2'
mean=np.load(routes/'projection_nnp/mean.f32.npy')
components=np.load(routes/'projection_nnp/components.f32.npy')[:256]
def read_rows(path,offset,count,kind):
    with path.open('rb') as f:
        d=struct.unpack('<I',f.read(4))[0]
        f.seek(offset*4*(d+1))
        b=f.read(count*4*(d+1))
    if len(b)!=count*4*(d+1):raise ValueError('truncated query slice')
    rows=np.frombuffer(b,dtype=kind).reshape(count,d+1)
    assert np.all(rows[:,0].view('<i4')==d)
    return rows[:,1:].copy()
queries=read_rows(root/'data/gist/gist_query.fvecs',a.offset,a.queries,'<f4')
gt=read_rows(root/'data/gist/gist_groundtruth.ivecs',a.offset,a.queries,'<i4')[:,:10]
q=(queries-mean)@components.T
k=49
best={name:(np.full((a.queries,k),np.inf,np.float32),np.full((a.queries,k),-1,np.int64))
      for name in ('original_float','projected_float','projected_sign')}
def update(name,dist,start):
    scores,ids=best[name]
    scores=np.concatenate((scores,dist),axis=1)
    ids=np.concatenate((ids,np.broadcast_to(np.arange(start,start+dist.shape[1]),dist.shape)),axis=1)
    keep=np.argpartition(scores,k-1,axis=1)[:,:k]
    best[name]=(np.take_along_axis(scores,keep,axis=1),np.take_along_axis(ids,keep,axis=1))
start=0
with (root/'data/gist/gist_base.fvecs').open('rb') as f, \
     (routes/'codes_nnp/codes_d256.bin').open('rb') as cf, \
     (routes/'codes_nnp/scales_d256.f16').open('rb') as sf:
    while True:
        raw=f.read(8192*4*(len(mean)+1))
        if not raw:break
        x=np.frombuffer(raw,dtype='<f4').reshape(-1,len(mean)+1)
        assert np.all(x[:,0].view('<i4')==len(mean))
        x=x[:,1:]
        y=(x-mean)@components.T
        code=np.frombuffer(cf.read(len(x)*32),dtype=np.uint8).reshape(len(x),32)
        scales=np.frombuffer(sf.read(len(x)*2),dtype='<f2').astype(np.float32)
        signs=np.unpackbits(code,axis=1,bitorder='little').astype(np.float32)*2-1
        update('original_float',np.sum(x*x,axis=1)[None,:]-2*(queries@x.T),start)
        update('projected_float',np.sum(y*y,axis=1)[None,:]-2*(q@y.T),start)
        update('projected_sign',256*scales[None,:]**2-2*(q@signs.T)*scales[None,:],start)
        start+=len(x)
        if start%131072==0:print('ranked',start,flush=True)
result=dict(dataset='gist',dim=256,query_offset=a.offset,queries=a.queries,base_rows=start,
            hypothetical_float_code_bytes=start*256*4,peak_diagnostic_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
            graph_search=False,qps_comparison=False,metrics={})
for name,(scores,ids) in best.items():
    order=np.argsort(scores,axis=1)
    ids=np.take_along_axis(ids,order,axis=1)
    per_query={str(c):[len(set(row[:c])&set(target))/10 for row,target in zip(ids,gt)] for c in (10,49)}
    result['metrics'][name]={f'gt10_coverage_at_{c}':float(np.mean(per_query[str(c)])) for c in (10,49)}
    result['metrics'][name]['per_query']=per_query
with a.output.open('x') as f:json.dump(result,f,indent=2)
print({k:{m:v for m,v in d.items() if m!='per_query'} for k,d in result['metrics'].items()},flush=True)
