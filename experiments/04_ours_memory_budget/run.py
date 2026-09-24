"""Replay the original full GIST configuration; default action is verification only."""
import argparse,importlib.util,json,shutil,struct,subprocess,sys
from pathlib import Path
from protocol import ROOT,HERE,OUT,REFERENCE,BINARY,WIDTHS,MODES,LIMIT,SAFETY,VALIDATION_ORDER,sha,flags,pair_hash,verify,reference,check_locked,cache_allowance

sys.path.insert(0,str(ROOT/'src/disk_bench'))
from storage_precondition import prepare_search, PROTOCOL as STORAGE_PROTOCOL

def driver_hashes():
 return {str(p):sha(p)for p in [HERE/'run.py',HERE/'protocol.py',ROOT/'scripts/local_runs/measure_05_process.py',ROOT/'src/disk_bench/storage_precondition.py']}

def load_measure():
 p=ROOT/'scripts/local_runs/measure_05_process.py';spec=importlib.util.spec_from_file_location('original_measure',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def build():
 from prepare import prepare
 source=prepare()
 subprocess.run(['cargo','build','--release','--offline','--manifest-path',str(source/'Cargo.toml'),'--target-dir',str(ROOT/'src/graph_core/target')],check=True,cwd=ROOT)
 BINARY.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/'src/graph_core/target/release/ours_memory_budget_v2',BINARY)
 (OUT/'build.json').write_text(json.dumps({'binary':str(BINARY),'sha256':sha(BINARY),'snapshot':json.loads((source/'snapshot.json').read_text())},indent=2))

def assert_build_current():
 manifest=json.loads((OUT/'build.json').read_text())
 if sha(BINARY)!=manifest['sha256']:raise ValueError('binary changed since build')
 for path,digest in manifest['snapshot']['original_sources'].items():
  if sha(path)!=digest:raise ValueError(f'source changed since build: {path}')
 return manifest['sha256']

def make_command(mode,verification,allowance=0,folder=None):
 a,_,_,_=reference();f=flags(a)
 folder=folder or OUT/mode
 f.update(verification['resolved_paths'])
 f.update({'--result-json':str(folder/'result.json'),'--query-trace':str(folder/'queries.jsonl'),'--run-id':'native_integration_memory_budget_v2','--native-binary-sha256':sha(BINARY),'--implementation-fingerprint':'ours-memory-budget-v2-locality','--integration-beams':'1','--memory-policy':mode,'--memory-cache-bytes':str(allowance),'--memory-profile-dir':str(OUT/'profile'),'--memory-stats-dir':str(folder/'memory_stats')})
 if mode=='profile':
  q=ROOT/'artifacts/query_splits/gist/shared/validation_query.fvecs';g=q.with_name('validation_gt.ivecs');order=VALIDATION_ORDER
  # Preserve the saved validation order and use all original search widths.
  f.update({'--query':str(q),'--groundtruth':str(g),'--query-order':str(order),'--query-split-sha256':pair_hash(q,g),'--query-order-sha256':sha(order)})
 cmd=[str(BINARY)]+[x for k,v in f.items()for x in (k,v)]
 check_locked(cmd,'profile' if mode=='profile' else 'test');return cmd

def assert_inputs_unchanged(verification):
 for p,h in verification['input_hashes'].items():
  if sha(p)!=h:raise ValueError(f'input mutated: {p}')

def read_trace(path):
 rows={}
 with Path(path).open() as f:
  for line in f:
   r=json.loads(line);key=(r['search_width'],r['query_id'])
   if key in rows:raise ValueError('duplicate trace key')
   rows[key]=r
 return rows

def parity(actual,expected):
 a,b=read_trace(actual),read_trace(expected)
 fields=['result_ids','recall_at_10','visited_nodes','distance_evaluations','db1_checks','db1_survivors','full4_candidates']
 if not a or a.keys()!=b.keys():raise ValueError('trace keys differ')
 errors=[{'query':key,'field':k}for key in a for k in fields if k not in a[key] or k not in b[key] or a[key][k]!=b[key][k]]
 return {'passed':not errors,'queries':len(a),'fields':fields,'mismatch_count':len(errors),'first_mismatches':errors[:20],'actual_sha256':sha(actual),'reference_sha256':sha(expected)}

def validate_result(folder,mode):
 result=json.loads((folder/'result.json').read_text());rows=result['summary_rows']
 expected_count=200 if mode=='profile' else 800
 if [(r['search_width'],r['beam_width'],r['query_count'])for r in rows]!=[(w,1,expected_count)for w in WIDTHS]:raise ValueError('result settings do not match reference')
 if result['workers']!=32 or result['warmup_queries']!=100 or not result['locality_layout_dir']:raise ValueError('result configuration drift')
 if not result['direct_io'] or not result['native_aio']:raise ValueError('native direct I/O required')
 trace=read_trace(folder/'queries.jsonl')
 if set(trace)!={(w,q)for w in WIDTHS for q in range(expected_count)}:raise ValueError('incomplete query trace')
 if any(len(row['result_ids'])!=10 for row in trace.values()):raise ValueError('expected top10 per query')
 identity=json.loads((folder/'identity.json').read_text())
 for width in WIDTHS:
  stats=json.loads((folder/'memory_stats'/f'L{width}.json').read_text())
  if stats['cache_reserved_bytes']>identity['optional_allowance_bytes']:raise ValueError('cache exceeds declared remaining-memory allowance')
  for metric,trace_key in [('io_requests','io_requests'),('read_pages','sectors_4k'),('read_bytes','bytes_read')]:
   actual=sum(stats['operations'][kind][metric] for kind in ['graph','compact','residual'])
   expected=sum(row[trace_key] for (w,q),row in trace.items() if w==width)
   if actual!=expected:raise ValueError(f'operation I/O differs from query trace: L{width} {metric}')
 if mode=='profile':validate_profile(folder)
 if mode!='profile':
  target=REFERENCE/'queries.jsonl' if mode=='baseline' else OUT/'baseline/queries.jsonl'
  if not mode.startswith('nav'):
   evidence=parity(folder/'queries.jsonl',target);(folder/'parity.json').write_text(json.dumps(evidence,indent=2))
   if not evidence['passed']:raise ValueError('search changed; no performance claim allowed')


def validate_profile(folder, expected_nodes=1_000_000):
 import numpy as np
 policy=json.loads((folder/'profile_policy.json').read_text())
 if policy['schema']!=2 or policy['widths']!=list(WIDTHS) or policy['exclude_warmup'] is not True:raise ValueError('invalid hotspot training policy')
 scores=[]
 for kind in ['graph','compact','residual']:
  score=np.fromfile(folder/f'{kind}_scores.f64',dtype='<f8')
  if len(score)!=expected_nodes or not np.isfinite(score).all() or (score<0).any():raise ValueError('invalid hotspot scores')
  expected=np.zeros_like(score)
  for width in WIDTHS:
   counts=np.fromfile(folder/f'L{width}_{kind}_counts.u64',dtype='<u8')
   if len(counts)!=len(score):raise ValueError('invalid hotspot raw counts')
   total=int(counts.sum())
   stats=json.loads((folder/'memory_stats'/f'L{width}.json').read_text())
   if total!=stats['operations'][kind]['logical_requests']:raise ValueError('profile count mismatch')
   if total:expected+=counts/total
  if not np.allclose(score,expected/len(WIDTHS),rtol=1e-12,atol=1e-15):raise ValueError('profile normalization mismatch')
  scores.append(score)
 payload=np.fromfile(folder/'payload_scores.f64',dtype='<f8')
 if payload.shape!=scores[0].shape or not np.allclose(payload,(scores[1]+scores[2])/2,rtol=1e-12,atol=1e-15):raise ValueError('payload ranking score mismatch')


def make_nav(verification):
 # Built only from base vectors; no test query or ground truth participates.
 import numpy as np
 folder=OUT/'profile';base=ROOT/'data/gist/gist_base.fvecs'
 from protocol import shape
 n,d=shape(base);rng=np.random.default_rng(20260813);ids=np.sort(rng.choice(n,2048,replace=False)).astype('<u4');x=np.empty((2048,d),dtype=np.float32)
 with base.open('rb') as f:
  for j,i in enumerate(ids):f.seek(int(i)*4*(d+1)+4);x[j]=np.frombuffer(f.read(d*4),dtype='<f4')
 norms=(x*x).sum(1);dist=norms[:,None]+norms[None,:]-2*x@x.T;np.fill_diagonal(dist,np.inf)
 edges=np.argsort(dist,axis=1)[:,:16].astype('<u4');ring=np.arange(len(ids),dtype='<u4')
 edges=np.column_stack([edges,(ring+1)%len(ids),(ring+len(ids)-1)%len(ids)]).astype('<u4')
 (folder/'nav.bin').write_bytes(struct.pack('<II',len(ids),edges.shape[1])+ids.tobytes()+edges.tobytes())
 (folder/'nav.json').write_text(json.dumps({'seed':20260813,'representatives':2048,'degree':18,'scheme':'base-only L2 16NN plus bidirectional ring; exploratory, not HNSW','sha256':sha(folder/'nav.bin')},indent=2))

def verify_acceptance(folder):
 a=json.loads((folder/'acceptance.json').read_text())
 if a['status']!='complete':raise ValueError('incomplete prerequisite')
 for name,h in a['artifact_sha256'].items():
  if sha(folder/name)!=h:raise ValueError(f'prerequisite changed: {name}')
 return a

def fresh_baseline():
 d=OUT/'baseline';verify_acceptance(d);m=json.loads((d/'memory_measurement.json').read_text());p=json.loads((d/'parity.json').read_text());identity=json.loads((d/'identity.json').read_text())
 if m['binary_sha256']!=assert_build_current() or not p['passed']:raise ValueError('fresh, matching, parity-passing baseline required')
 v=json.loads((OUT/'verification.json').read_text())
 if identity['input_hashes']!=v['input_hashes']:raise ValueError('baseline inputs differ')
 if identity['driver_hashes']!=driver_hashes():raise ValueError('driver changed after baseline')
 if identity.get('storage_precondition_protocol')!=STORAGE_PROTOCOL:raise ValueError('baseline lacks uniform storage preparation')
 if not json.loads((d/'performance_gate.json').read_text())['passed']:raise ValueError('baseline performance gate failed')
 return cache_allowance(m)

def check_baseline_performance(folder):
 # A predeclared gross-regression guard, not a statistical equivalence test.
 reference_path=ROOT/'results/diagnostics/03_disk_system/layout_state_control/gist/20260919_uniform03_current_full/result.json'
 expected=json.loads(reference_path.read_text())['summary_rows']
 actual=json.loads((folder/'result.json').read_text())['summary_rows']
 total=lambda rows:sum(r['query_count']/r['qps'] for r in rows)
 q100=lambda rows:next(r['qps'] for r in rows if r['search_width']==100)
 ratios={'L100_qps':q100(actual)/q100(expected),'full_scan_throughput':total(expected)/total(actual)}
 evidence={'reference':str(reference_path),'reference_sha256':sha(reference_path),'ratios':ratios,
           'allowed_ratio':[0.75,1.35],'purpose':'stop gross drift before testing strategies; not statistical equivalence',
           'passed':all(0.75<=r<=1.35 for r in ratios.values())}
 (folder/'performance_gate.json').write_text(json.dumps(evidence,indent=2))
 if not evidence['passed']:raise ValueError('baseline performance drift; inspect performance_gate.json before continuing')

def execute(mode,v,allowance):
 assert_build_current();assert_inputs_unchanged(v)
 folder=OUT/mode
 if folder.exists():raise FileExistsError(f'refuse to overwrite run: {folder}')
 if mode not in ('baseline','profile','pages'):
  verify_acceptance(OUT/'profile')
  identity=json.loads((OUT/'profile/identity.json').read_text())
  if identity['binary_sha256']!=sha(BINARY) or identity['input_hashes']!=v['input_hashes'] or identity['driver_hashes']!=driver_hashes():raise ValueError('matching validation profile required')
  for f in ['graph_scores.f64','compact_scores.f64','residual_scores.f64','payload_scores.f64','profile_policy.json','nav.bin']:
   if not (OUT/'profile'/f).is_file():raise ValueError(f'missing validation profile: {f}')
 folder.mkdir();(folder/'memory_stats').mkdir()
 cmd=make_command(mode,v,allowance,folder)
 identity={'driver_hashes':driver_hashes(),'binary_sha256':sha(BINARY),'input_hashes':v['input_hashes'],'reference_command_sha256':v['reference_command_sha256'],'mode':mode,'optional_allowance_bytes':allowance,'unallocated_headroom_bytes':SAFETY,'stage':'validation_profile' if mode=='profile' else 'test'}
 (folder/'identity.json').write_text(json.dumps(identity,indent=2))
 preparation=prepare_search(cmd,folder/'storage_precondition.json')
 if preparation['status']!='completed':raise ValueError('storage preparation incomplete')
 for item in preparation['files']:
  if item['sha256']!=v['input_hashes'].get(item['path']):raise ValueError('prepared file identity mismatch')
 identity.update(storage_precondition_protocol=STORAGE_PROTOCOL,storage_precondition_sha256=sha(folder/'storage_precondition.json'),numa_binding='none; original 04 launch protocol')
 (folder/'identity.json').write_text(json.dumps(identity,indent=2))
 evidence=load_measure().measure(cmd,folder,budget=LIMIT)
 if not evidence['user_address_space_budget_passed']:raise ValueError('memory budget not verified')
 validate_result(folder,mode)
 if mode=='profile':make_nav(v)
 if mode=='baseline':check_baseline_performance(folder)
 artifacts=['storage_precondition.json','result.json','queries.jsonl','memory_measurement.json','identity.json'] + [f'memory_stats/L{w}.json' for w in WIDTHS]
 if mode=='baseline':artifacts.append('performance_gate.json')
 if (folder/'parity.json').exists():artifacts.append('parity.json')
 if mode=='profile':artifacts += [str(p.relative_to(folder)) for p in sorted(folder.glob('L*_counts.u64'))]
 if mode=='profile':artifacts+=['graph_scores.f64','compact_scores.f64','residual_scores.f64','payload_scores.f64','profile_policy.json','nav.bin','nav.json']
 (folder/'acceptance.json').write_text(json.dumps({'status':'complete','mode':mode,'binary_sha256':sha(BINARY),'artifact_sha256':{name:sha(folder/name)for name in artifacts},'performance_result':mode!='profile','scope':'same_kernel_and_inputs; navigation_changes_entry' if mode.startswith('nav') else 'same_query_results_and_candidates'},indent=2))
 return folder

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',choices=['verify','build','profile','baseline','test'],default='verify');p.add_argument('--modes',default=','.join(MODES[1:]));p.add_argument('--execute',action='store_true',help='explicitly run new performance/profile processes; does not resume the obsolete paused queue');a=p.parse_args()
 v=verify()
 if a.stage=='verify':print('PASS original configuration, split, order, graph and locality hashes');return
 if a.stage=='build':build();return
 modes=['profile'] if a.stage=='profile' else ['baseline'] if a.stage=='baseline' else a.modes.split(',')
 if any(m not in MODES+('profile',)for m in modes):p.error('unknown memory policy')
 allowance=0 if a.stage in ('baseline','profile') else fresh_baseline()
 if a.stage=='test' and allowance<=0:raise ValueError('no optional memory after baseline workspace and headroom')
 if not a.execute:
  plans=[make_command(m,v,allowance)for m in modes];(OUT/f'{a.stage}_plan.json').write_text(json.dumps(plans,indent=2));print('Plan written. No experiment started.');return
 state={'status':'running','stage':a.stage,'modes':modes}
 (OUT/'execution_state.json').write_text(json.dumps(state,indent=2))
 try:
  for mode in modes:print(execute(mode,v,allowance),flush=True)
 except BaseException:
  state['status']='failed';(OUT/'execution_state.json').write_text(json.dumps(state,indent=2));raise
 state['status']='completed';(OUT/'execution_state.json').write_text(json.dumps(state,indent=2))
if __name__=='__main__':main()
