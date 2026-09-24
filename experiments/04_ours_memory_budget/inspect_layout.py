"""Read-only layout/degree audit; no index rewrite and no performance test."""
import json
import numpy as np
from protocol import ROOT,OUT

def main():
    index=ROOT/'work/05_disk_system_fair/disk_root/05_disk_system_fair/05C_disk_system_fair/gist/Ours-Disk/hybrid_disk'
    meta=dict(line.split('=',1)for line in (index/'index.meta').read_text().splitlines())
    n=int(meta['base_count']);record=int(meta['record_bytes']);per=4096//record
    raw=np.memmap(index/'shared_graph.pages',dtype='<u4',mode='r').reshape(-1,1024)
    # Respect page padding; take the first word of each fixed-width graph slot.
    degrees=raw[:,np.arange(per)*(record//4)].reshape(-1)[:n]
    assert len(degrees)==n and int(degrees.max())<=int(meta['max_degree'])
    compact=int(meta['ours_compact_record_bytes']);residual=int(meta['ours_residual_record_bytes'])
    dense_graph=4*(int(degrees.max())+1)
    result={'nodes':n,'declared_max_degree':int(meta['max_degree']),'min':int(degrees.min()),'max':int(degrees.max()),'mean':float(degrees.mean()),'percentiles':np.percentile(degrees,[50,90,95,99]).tolist(),'fixed_adjacency_bytes':n*record,'actual_degree_plus_ids_bytes':int((degrees.astype('uint64')+1).sum()*4),'unused_adjacency_slot_bytes':int(n*record-(degrees.astype('uint64')+1).sum()*4),'layouts':{}}
    for label,size in [('combined_current',record+compact),('combined_actual_max_degree',dense_graph+compact),('residual',residual)]:
        count=4096//size;pages=(n+count-1)//count
        result['layouts'][label]={'record_bytes':size,'records_per_page':count,'page_utilization':count*size/4096,'total_pages':pages,'file_bytes':pages*4096,'padding_bytes':pages*4096-n*size}
    result['note']='Alternative is a size calculation only, not an exported index or benchmark. Logical IDs, edge order and all edges must be preserved.'
    OUT.mkdir(exist_ok=True,parents=True)
    (OUT/'layout_degree_audit.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
