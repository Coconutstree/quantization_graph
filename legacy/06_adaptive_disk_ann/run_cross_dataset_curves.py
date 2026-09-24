"""Matched-budget cross-dataset pilot: validation-only selection, then width curves."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import time
from select_route_profile import select_profile

root=Path(__file__).resolve().parents[2]
out=root/'results/disk_environment/06_adaptive_disk_ann'/time.strftime('cross_dataset_curves_%Y%m%d_%H%M%S')
out.mkdir(parents=True)
print('OUTPUT',out,flush=True)
rows=[]
for ds in ('gist','dbpedia'):
    routes=root/f'results/disk_environment/06_adaptive_disk_ann/{ds}/cross_dataset_v2/codes_nnp'
    def run(variants,count,offset,widths,label):
        cmd=[sys.executable,'legacy/06_adaptive_disk_ann/run_disk_replacement_pilot.py',
             '--dataset',ds,'--route-dir',str(routes),'--budget-gib','1',
             '--queries',str(count),'--query-offset',str(offset),'--widths',widths,'--variants',variants]
        print('START',ds,label,variants,widths,flush=True)
        with (out/f'{ds}_{label}.log').open('w') as log:
            p=subprocess.Popen(cmd,cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            folder=None
            for line in p.stdout:
                print(line,end='',flush=True); log.write(line);log.flush()
                if folder is None and line.startswith(str(root)):folder=Path(line.strip())
            if p.wait():raise RuntimeError(f'{ds} {label} failed; see {out}')
        return folder
    variants=['baseline']+[f'd{k}_k32_t100' for k in (64,128,256,512)]
    validation=run(','.join(variants),100,0,'49','validation')
    profiles=[]
    for v in variants:
        d=json.loads((validation/f'{v}.json').read_text());r=d['summary_rows'][0]
        profiles.append(dict(variant=v,status='done',split='validation',budget_gib=1,
            recall=r['recall'],qps=r['qps'],peak_rss_bytes=r['peak_rss_bytes'],
            accounted_bytes=sum(d[k] for k in ('resident_bytes','codebook_bytes','worker_scratch_bytes','cache_bytes'))))
    decision=select_profile(profiles,1,profiles[0]['recall'],.001)
    selected=decision['selected']['variant'] if decision['selected'] else None
    # If the automatic selector falls back to baseline, retain a explicitly
    # labelled diagnostic lowdim candidate chosen by VALIDATION recall only.
    lowdim=selected if selected and selected!='baseline' else max(profiles[1:],key=lambda p:(p['recall'],p['qps']))['variant']
    decision.update(profiles=profiles,curve_lowdim=lowdim,
                    lowdim_is_selected=selected==lowdim,validation_dir=str(validation),
                    query_split=dict(validation=[0,100],test=[100,1000]),
                    widths=[16,49,96],projection='base-only fixed NNP v2',hard_memory_limit=False)
    with (out/f'{ds}_frozen.json').open('x') as f:json.dump(decision,f,indent=2)
    print('FROZEN',ds,'selected',selected,'curve lowdim',lowdim,flush=True)
    folder=run('baseline,'+lowdim,900,100,'16,49,96','test_curves')
    for v in ('baseline',lowdim):
        d=json.loads((folder/f'{v}.json').read_text())
        for r in d['summary_rows']:
            rows.append(dict(dataset=ds,variant=v,role='baseline' if v=='baseline' else ('selected' if selected==v else 'diagnostic'),
                width=r['search_width'],recall=r['recall'],qps=r['qps'],io=r['io_requests_per_query'],
                peak_rss_mib=r['peak_rss_bytes']/2**20,resident_mib=d['resident_bytes']/2**20,
                query_count=r['query_count'],budget_gib=1,artifact=str(folder/f'{v}.json')))
    with (out/'source_data.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    print('DONE DATASET',ds,flush=True)
print('COMPLETE',out,flush=True)
