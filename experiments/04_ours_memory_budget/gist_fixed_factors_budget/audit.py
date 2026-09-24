"""Independent acceptance-file hashes, cross-round parity and budget checks."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'results/04_ours_memory_budget/gist_memory_auto_policy'
FIELDS=['result_ids','recall_at_10','visited_nodes','distance_evaluations','db1_checks','db1_survivors','full4_candidates','rerank_candidates']
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def traces(folder):
 # Match the canonical experiment trace decoder, including nested batch files.
 import driver_base
 return driver_base.pilot.traces(folder)
def main(split):
 completed=json.loads((OUT/split/'completed.json').read_text());reference={(r['search_width'],r['query_id']):r for r in traces(OUT/split/'resident_reference_r1')};groups=[]
 build=json.loads((OUT/'build.json').read_text())
 for p,h in build['generated_sources'].items():assert sha(Path(p))==h
 assert sha(ROOT/'work/ours_memory_budget/gist_fixed_factors_budget/ours_gist_fixed_factors_budget')==build['binary_sha256']
 for a in sorted((OUT/split).glob('*/acceptance.json')):
  accepted=json.loads(a.read_text());assert accepted['passed'] and accepted['whole_process_accounting'] and accepted['physical_io']
  for p,h in accepted['files'].items():assert sha(a.parent/p)==h,(a.parent,p)
  p=json.loads((a.parent/'memory_plan.json').read_text());m=json.loads((a.parent/'memory_measurement.json').read_text())
  assert m['hard_limit_verified'] and m['user_address_space_budget_passed']
  assert m['sampled_high_water_bytes']['VmPeak']<=p['expected_peak_bytes']<=p['budget_bytes']-p['reserve_bytes']
  assert p['factors_resident']
  data=traces(a.parent);seen=set()
  for r in data:
   key=r['search_width'],r['query_id'];assert key not in seen;seen.add(key)
   for field in FIELDS:assert r[field]==reference[key][field],(a.parent,key,field)
  assert seen==set(reference)
  groups.append(dict(run=a.parent.name,queries=len(data),cross_round_parity=True,hashes=True,reserve_unallocated=True))
 assert len(groups)==2*(len(completed['budgets'])+1)
 out=OUT/'validation'/f'{split}_audit.json';out.write_text(json.dumps(dict(passed=True,groups=groups,queries=sum(g['queries'] for g in groups)),indent=2)+'\n')
if __name__=='__main__':
 import sys
 main(sys.argv[1])
