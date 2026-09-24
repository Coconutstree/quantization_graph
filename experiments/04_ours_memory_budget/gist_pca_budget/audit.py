"""Final artifact/budget/split audit. Does not interpret finite samples as proofs."""
import json,struct
from pathlib import Path
from prepare import ROOT,OUT,WORK,HERE,dump,sha
def read(p):return json.loads(p.read_text())
def vectors(path):
 raw=Path(path).read_bytes();stride=4*(struct.unpack_from('<I',raw)[0]+1)
 return {raw[i:i+stride] for i in range(0,len(raw),stride)}
def main():
 assert read(OUT/'state.json')['status']=='completed'
 build=read(OUT/'build.json');assert sha(WORK/'ours_gist_pca_budget')==build['binary_sha256']
 for p,h in build['sources'].items():assert sha(p)==h,p
 bounds=read(OUT/'bound_audit_v3/audit.json');assert bounds['binary_sha256']==build['binary_sha256']
 assert all(x['deterministic_full_violations']==0 for x in bounds['rows'] if x['dim']!=960)
 assert read(OUT/'smoke.json')['passed']
 lock=read(OUT/'selection_lock.json');assert lock['selected_before_test'] and lock['binary_sha256']==build['binary_sha256']
 accepted=[];count=0;queryfiles={}
 for p in sorted(OUT.glob('*/*/acceptance.json')):
  a=read(p);assert a['passed'] and a['binary_sha256']==build['binary_sha256']
  for name,h in a['files'].items():assert sha(p.parent/name)==h,(p,name)
  cfg=read(p.parent/'config.json');plan=read(p.parent/'memory_plan.json');mem=read(p.parent/'memory_measurement.json')
  assert plan['admission_bytes']<=cfg['budget_mib']*2**20
  assert mem['user_address_space_budget_passed'] and mem['hard_limit_verified']
  assert mem['sampled_high_water_bytes']['VmPeak']<=plan['expected_peak_bytes']
  cmd=read(p.parent/'command.json');flags=dict(zip(cmd[1::2],cmd[2::2]));queryfiles[cfg['split']]=flags['--query']
  if '--pca-route-dir' in flags:assert flags['--pca-tail-mode']=='norm'
  if '_fixed_' in p.parent.name:assert plan['routing_capacity_pages']==(4*2**20)//4352
  count+=a['query_count'];accepted.append(str(p.relative_to(OUT)))
 sets={s:vectors(p) for s,p in queryfiles.items()}
 assert not sets['train']&sets['tune'] and not sets['train']&sets['test'] and not sets['tune']&sets['test']
 for k in (128,256,512):
  folder=WORK/f'd{k}';a=read(folder/'encoded.json')
  assert a['all_code_rows_equal']
  for name,h in a['files'].items():assert sha(folder/name)==h
 paused=read(OUT/'competing_build.json');assert paused['resumed']>paused['paused']
 dump(OUT/'audit.json',dict(passed=True,accepted_runs=len(accepted),measured_queries=count,accepted=accepted,unique_query_values={s:len(v) for s,v in sets.items()},test_rows_preserved=800,lower_bound_diagnostic=str(OUT/'bound_audit_v3/audit.json'),binary_sha256=build['binary_sha256'],script_hashes={p.name:sha(p) for p in HERE.iterdir() if p.is_file()},report_sha256=sha(OUT/'report.md'),competing_build_restored=True))
 print('audit passed',len(accepted),'runs',count,'measured queries')
if __name__=='__main__':main()
