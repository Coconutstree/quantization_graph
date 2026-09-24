"""Audit ranking-only execution, budgets, result hashes, and split isolation."""
import json,struct
from pathlib import Path
from prepare import ROOT,OUT,WORK,ASSETS,HERE,dump,sha
def read(p):return json.loads(p.read_text())
def vectors(path):
 raw=Path(path).read_bytes();stride=4*(struct.unpack_from('<I',raw)[0]+1)
 return {raw[i:i+stride]for i in range(0,len(raw),stride)}
def main():
 assert read(OUT/'state.json')['status']=='completed'
 build=read(OUT/'build.json');assert sha(WORK/'ours_gist_pca_routing')==build['binary_sha256']
 for p,h in build['sources'].items():assert sha(p)==h,p
 assert read(OUT/'smoke.json')['passed']
 locks={'_locked_':read(OUT/'selection_lock.json'),'_matched_':read(OUT/'matched_selection_lock.json')}
 rule=read(OUT/'matched_protocol.json')
 reference=read(ROOT/rule['reference'])['summary_rows']
 assert next(x['recall']for x in reference if x['search_width']==100)==rule['validation_target']==locks['_matched_']['target_recall']
 for lock in locks.values():assert lock['selected_before_test']and lock['binary_sha256']==build['binary_sha256']
 assert locks['_matched_']['rule_created_before_any_test']==rule['created_at']
 for tag,lock in locks.items():
  for choice in lock['candidates']:
   options=[]
   for p in sorted((OUT/'tune').glob(f"d{choice['dim']}_k*/result.json")):
    if '_grid_'not in p.parent.name and not(tag=='_locked_'and'_extended_'in p.parent.name):continue
    assert (p.parent/'acceptance.json').exists()
    for x in read(p)['summary_rows']:
     if x['recall']>=lock['target_recall']:options.append((x['qps'],int(p.parent.name.split('_')[1][1:]),x['search_width'],str(p.parent)))
   assert choice['eligible']==bool(options)
   if options:assert (choice['qps'],choice['keep'],choice['width'],choice['source'])==max(options)
 source=(WORK/'native/ours_port.rs').read_text()
 assert source.count('if codec.navigation_keep.is_none() && ablation.uses_gate()')==2
 assert 'if codec.pca.is_none() && ablation.uses_gate() && estimate.valid != 0'in source
 accepted=[];count=0;queryfiles={}
 for p in sorted(OUT.glob('*/*/acceptance.json')):
  a=read(p);assert a['passed']and a['binary_sha256']==build['binary_sha256']
  for name,h in a['files'].items():assert sha(p.parent/name)==h,(p,name)
  cfg=read(p.parent/'config.json');plan=read(p.parent/'memory_plan.json');mem=read(p.parent/'memory_measurement.json')
  assert plan['admission_bytes']<=cfg['budget_mib']*2**20
  assert mem['user_address_space_budget_passed']and mem['hard_limit_verified']
  assert mem['sampled_high_water_bytes']['VmPeak']<=plan['expected_peak_bytes']
  cmd=read(p.parent/'command.json');f=dict(zip(cmd[1::2],cmd[2::2]));queryfiles[cfg['split']]=f['--query']
  if '--pca-route-dir'in f:assert f['--pca-tail-mode']=='route'and '--pca-route-keep'in f and plan['factors_bytes']==20000000
  if cfg['split']=='test':
   tag=next(t for t in locks if t in p.parent.name);lock=locks[tag]
   start=read(p.parent/'environment_start.json')['time'];assert start>=lock['locked_at']and start>=rule['created_at']
   dim=int(p.parent.name.split('_')[0][1:]);keep=int(p.parent.name.split('_')[1][1:]);choice=next(x for x in lock['candidates']if x['dim']==dim)
   assert choice['eligible']and choice['keep']==keep
   assert all(x['search_width']==choice['width']for x in read(p.parent/'result.json')['summary_rows'])
  count+=a['query_count'];accepted.append(str(p.relative_to(OUT)))
 sets={s:vectors(p)for s,p in queryfiles.items()}
 assert not sets['train']&sets['tune']and not sets['train']&sets['test']and not sets['tune']&sets['test']
 for k in(128,256,512):
  folder=ASSETS/f'd{k}';a=read(folder/'encoded.json');assert a['all_code_rows_equal']
  for name,h in a['files'].items():assert sha(folder/name)==h
 for name in ('competing_build.json','competing_build_matched.json'):
  paused=read(OUT/name);assert paused['resumed']>paused['paused']
 effects=read(OUT/'effects.json');assert len(effects)==8 and all(x['repeats']==2 for x in effects)
 fields=('result_ids','recall_at_10','visited_nodes','distance_evaluations','db1_checks','db1_survivors','full4_candidates','rerank_candidates')
 for p in sorted((OUT/'test').glob('*_r1/queries.jsonl')):
  other=p.parent.with_name(p.parent.name[:-1]+'2')/'queries.jsonl'
  traces=[]
  for q in (p,other):
   xs=[json.loads(line)for line in q.read_text().splitlines()];assert len(xs)==800
   traces.append({x['query_id']:[x[f]for f in fields]for x in xs})
  assert traces[0]==traces[1],p
 dump(OUT/'audit.json',dict(passed=True,accepted_runs=len(accepted),measured_queries=count,accepted=accepted,unique_query_values={s:len(v)for s,v in sets.items()},test_rows_preserved=800,lowdim_hard_prune_disabled=True,matched_rule_precedes_all_test=True,selection_reproduced_from_validation=True,test_repeats_semantically_identical=True,distinct_test_cohorts=True,binary_sha256=build['binary_sha256'],script_hashes={p.name:sha(p)for p in HERE.iterdir()if p.is_file()},report_sha256=sha(OUT/'report.md'),competing_build_restored=True))
 print('audit passed',len(accepted),'runs',count,'measured queries')
if __name__=='__main__':main()
