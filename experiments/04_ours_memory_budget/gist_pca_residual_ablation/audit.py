"""Verify the M32 three-way experiment and independent test selection."""
import json,struct
from pathlib import Path
from prepare import ROOT,HERE,WORK,OUT,ASSETS,BIN,build,dump,sha
FIELDS=('result_ids','recall_at_10','visited_nodes','distance_evaluations','db1_checks','db1_survivors','full4_candidates','rerank_candidates')
def read(p):return json.loads(Path(p).read_text())
def traces(p):return {(x['search_width'],x['query_id']):x for x in map(json.loads,p.read_text().splitlines())}
def vectors(p):
 data=Path(p).read_bytes();stride=4*(struct.unpack_from('<I',data)[0]+1)
 return {data[i:i+stride]for i in range(0,len(data),stride)}
def main():
 build();assert read(OUT/'state.json')['status']=='completed'
 protocol=read(OUT/'protocol.json');lock=read(OUT/'selection_lock.json')
 assert protocol['M']==32 and lock['selected_before_test']and lock['binary_sha256']==sha(BIN)
 assert read(OUT/'smoke.json')['passed']
 source=(WORK/'native/ours_port.rs').read_text()
 assert source.count('if codec.comparison_gate && ablation.uses_gate()')==3
 assert 'let modes=vec![DIST_MODE_RECOMPUTE_FULL;estimates.len()];'in source
 assert 'e.lower_bound=e.lower_bound.max(0.)+p.correction(id as usize,q);'in source
 pca=(WORK/'native/pca.rs').read_text()
 assert 'if self.tail_mode=="norm"{residual_lower_bound(self.norms[id],q.norm)}else{0.}'in pca
 accepted=[];count=0;queries={}
 for p in sorted(OUT.glob('*/*/acceptance.json')):
  f=p.parent;a=read(p);v=read(f/'variant.json');cfg=read(f/'config.json');plan=read(f/'memory_plan.json');mem=read(f/'memory_measurement.json')
  assert a['passed']and a['binary_sha256']==sha(BIN)
  for name,h in a['files'].items():assert sha(f/name)==h,(p,name)
  command=read(f/'command.json');flags=dict(zip(command[1::2],command[2::2]))
  assert flags['--pca-route-keep']=='32'and v['M']==32
  assert flags['--comparison-gate']==str(int(v['gate']))
  assert plan['admission_bytes']<=538*2**20 and mem['hard_limit_verified']and mem['user_address_space_budget_passed']
  assert mem['sampled_high_water_bytes']['VmPeak']<=plan['expected_peak_bytes']
  assert plan['pca_residual_norm_bytes']==(4_000_000 if v['variant']=='pca_residual'else 0)
  if v['variant']!='full1bit':
   assert plan['routing_capacity_pages']==0 and v['dimension']==128
   assert flags['--pca-tail-mode']==('norm'if v['variant']=='pca_residual'else'none')
  else:assert plan['routing_capacity_pages']>0 and flags['--routing-code-layout']=='bfs'
  ts=traces(f/'queries.jsonl')
  for x in ts.values():
   if v['gate']:
    gate_inputs=x['db1_checks']-x['visited_nodes']+1
    assert gate_inputs>=x['db1_survivors']and x['full4_candidates']==x['db1_survivors']+1
  queries[cfg['split']]=flags['--query'];count+=a['query_count'];accepted.append(str(p.relative_to(OUT)))
  if cfg['split']=='test':
   selected=next(x['selected']for x in lock['candidates']if x['variant']==v['variant'])
   assert cfg['widths']==[selected['width']]and read(f/'environment_start.json')['time']>=lock['locked_at']
 for choice in lock['candidates']:
  options=[x for x in choice['options']if x['recall']>=lock['target_recall']]
  assert choice['selected']==(max(options,key=lambda x:x['qps'])if options else None)
  for x in choice['options']:
   assert any(r['search_width']==x['width']and r['recall']==x['recall']and r['qps']==x['qps']for r in read(Path(x['source'])/'result.json')['summary_rows'])
  if choice['selected']:
   name=choice['variant'];a=traces(OUT/f'test/{name}_locked_r1/queries.jsonl');b=traces(OUT/f'test/{name}_locked_r2/queries.jsonl')
   assert len(a)==len(b)==800 and a.keys()==b.keys()
   for key in a:assert all(a[key][k]==b[key][k]for k in FIELDS)
 a=traces(OUT/'train/pca_gateoff_cache8_r1/queries.jsonl');b=traces(OUT/'train/pca_residual_gateoff_cache8_r1/queries.jsonl')
 assert a.keys()==b.keys()
 for key in a:assert all(a[key][k]==b[key][k]for k in FIELDS)
 sets={s:vectors(p)for s,p in queries.items()}
 assert not sets['train']&sets['tune']and not sets['train']&sets['test']and not sets['tune']&sets['test']
 for name,h in read(ASSETS/'d128/encoded.json')['files'].items():assert sha(ASSETS/'d128'/name)==h
 for p in OUT.glob('competing_build_*.json'):
  v=read(p);assert v['resumed']>v['paused']
 unit=(WORK/'unit_tests.log').read_text();assert '1 passed; 0 failed'in unit
 figure=read(OUT/'figures/manifest.json');assert figure['data_sha256']==sha(OUT/'measured_results.csv')
 for name,h in figure['files'].items():assert sha(OUT/'figures'/name)==h
 dump(OUT/'audit.json',dict(passed=True,accepted_runs=len(accepted),measured_queries=count,accepted=accepted,
  M=32,no_M64_runs=True,binary_sha256=sha(BIN),same_full4_recomputation_kernel=True,
  gate_off_residual_parity=True,selection_reproduced_from_validation=True,test_started_after_lock=True,
  test_repeats_semantically_identical=True,unique_query_vectors={k:len(v)for k,v in sets.items()},
  whole_process_budget_verified=True,residual_norm_memory_accounted=True,competing_build_restored=True,
  empirical_gate_only=True,strict_native_hard_prune_claim=False,unit_tests_sha256=sha(WORK/'unit_tests.log'),
  report_sha256=sha(OUT/'report.md'),scripts={p.name:sha(p)for p in HERE.iterdir()if p.is_file()}))
 print('audit passed:',len(accepted),'runs,',count,'measured queries; M=32 only')
if __name__=='__main__':main()
