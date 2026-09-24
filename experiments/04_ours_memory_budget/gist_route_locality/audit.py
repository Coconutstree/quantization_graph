"""Verify frozen evidence, same search semantics, and layout misuse rejection."""
import hashlib,json,os,resource,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'results/04_ours_memory_budget/gist_route_locality';WORK=ROOT/'work/ours_memory_budget/gist_route_locality'
def read(p):return json.loads(p.read_text())
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def main():
 assert read(OUT/'state.json')['status']=='completed'
 build=read(WORK/'build.json');binary=WORK/'ours_gist_route_locality';assert sha(binary)==build['binary_sha256']
 for p,h in build['sources'].items():assert sha(p)==h
 fields=['result_ids','recall_at_10','visited_nodes','distance_evaluations','db1_checks','db1_survivors','full4_candidates','rerank_candidates']
 count=0
 for split,n in [('tune',100),('test',800)]:
  paths=list((OUT/split).glob('*/acceptance.json'));assert len(paths)==4
  reference=OUT/split/'original_i256_r1'
  ref={q['query_id']:q for q in map(json.loads,(reference/'queries.jsonl').read_text().splitlines())}
  for p in paths:
   a=read(p);assert a['passed'];count+=a['query_count'];assert a['query_count']==n
   for f,h in a['files'].items():assert sha(p.parent/f)==h
   qs=list(map(json.loads,(p.parent/'queries.jsonl').read_text().splitlines()));assert len(qs)==n
   for q in qs:
    for f in fields:assert q[f]==ref[q['query_id']][f],(p,f,q['query_id'])
   plan=read(p.parent/'memory_plan.json');assert plan['budget_bytes']==538*2**20 and plan['max_inflight_pages']==256 and plan['optional_cache_bytes']==0 and plan['routing_capacity_pages']==3855
   stats=read(p.parent/'routing_stats.json')['100'];ops=read(p.parent/'memory_stats/L100.json')['operations']
   assert sum(q['sectors_4k'] for q in qs)==stats['reads']+sum(x['read_pages'] for x in ops.values())
 assert count==3600
 base=read(OUT/'test/bfs_i256_r1/command.json');flags=dict(zip(base[1::2],base[2::2]));checks=[]
 for case,expected in [('missing_flag','requires matching layout flag'),('wrong_mapping_hash','route layout hash mismatch'),('resident_disallowed','paged-only')]:
  d=OUT/'guards'/case;d.mkdir(parents=True,exist_ok=True);f=flags.copy()
  f.update({'--result-json':str(d/'result.json'),'--query-trace':str(d/'queries.jsonl'),'--memory-stats-dir':str(d/'memory_stats'),'--auto-plan-output':str(d/'memory_plan.json'),'--search-dram-budget-gib':'0.625'})
  if case=='missing_flag':del f['--routing-code-layout']
  elif case=='wrong_mapping_hash':f['--locality-mapping-sha256']='0'*64
  else:f['--memory-policy']='resident'
  def limits():resource.setrlimit(resource.RLIMIT_AS,(640*2**20,640*2**20))
  cmd=[str(binary)]+[x for k,v in f.items() for x in [k,v]]
  p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=60,preexec_fn=limits,env=dict(os.environ,MALLOC_ARENA_MAX='2'))
  (d/'terminal.log').write_text(p.stdout);(d/'command.json').write_text(json.dumps(cmd,indent=2)+'\n')
  assert p.returncode!=0 and expected in p.stdout,(case,p.stdout)
  checks.append(dict(case=case,passed=True,returncode=p.returncode))
 proof=read(OUT/'factor_and_storage_audit.json');assert proof['factors_identical'] and proof['same_filesystem_device']
 assert read(OUT/'layout.json')['all_code_rows_exact']
 note=read(OUT/'competing_build.json') if (OUT/'competing_build.json').exists() else {};assert not note or note['resumed']>=note['paused']
 result=dict(passed=True,runs=8,queries=count,guards=checks,report_sha256=sha(OUT/'report.md'),updated=time.time())
 (OUT/'audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
