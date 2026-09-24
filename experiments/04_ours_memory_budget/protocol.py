"""Immutable reference configuration and content-verified path migration."""
from pathlib import Path
import hashlib,json,struct,os
ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
RUN_NAME=os.environ.get('OURS_MEMORY_RUN','v2')
if RUN_NAME not in ('v2','v3_direct'):raise ValueError('unknown memory experiment run')
OUT=ROOT/'results/04_ours_memory_budget'/RUN_NAME
REFERENCE=ROOT/'results/archive/03_disk_system_unadmitted_20260921/gist/test_L_400_w_32/raw/Ours-Disk'
BINARY=ROOT/'work/ours_memory_budget/v2/bin/ours_memory_budget_v2'
WIDTHS=list(range(10,31))+list(range(40,101,10))+list(range(140,581,40))
MODES=('baseline','pages','hot_graph','hot_payload','hybrid','nav','nav_pages','nav_hybrid','full_payload')
VALIDATION_ORDER=ROOT/'results/archive/03_disk_system_unadmitted_20260921/gist/legacy_snapshot_20260918/raw/legacy_snapshot/manifests/query_order_validate_c0b5376b1e41_seed20260813.u32'
LIMIT=2<<30
SAFETY=64<<20  # common unallocated headroom, not a cache-size ceiling

def sha(path):
 with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def flags(command):
 if len(command)%2!=1:raise ValueError('expected flag/value pairs')
 pairs=list(zip(command[1::2],command[2::2]));result=dict(pairs)
 if len(pairs)!=len(result):raise ValueError('duplicate flag')
 return result
def pair_hash(q,g):
 h=hashlib.sha256()
 for p in (q,g):h.update(p.name.encode());h.update(bytes.fromhex(sha(p)))
 return h.hexdigest()
def shape(p):
 with Path(p).open('rb') as f:d=struct.unpack('<I',f.read(4))[0]
 n,rem=divmod(Path(p).stat().st_size,4*(d+1))
 if d<=0 or rem:raise ValueError(f'invalid vecs: {p}')
 return n,d

def reference():
 a=json.loads((REFERENCE/'command.json').read_text());f=flags(a)
 r=json.loads((REFERENCE/'result.json').read_text());mem=json.loads((REFERENCE/'memory_measurement.json').read_text())
 assert list(map(int,f['--integration-widths'].split(',')))==WIDTHS
 assert [(v['search_width'],v['beam_width'],v['query_count'])for v in r['summary_rows']]==[(w,1,800)for w in WIDTHS]
 assert f['--workers']=='32' and f['--warmup-queries']=='100' and float(f['--search-dram-budget-gib'])==2
 assert mem['rlimit_as_bytes']==LIMIT and mem['user_address_space_budget_passed']
 return a,f,r,mem

def verify():
 a,f,r,mem=reference()
 paths={'--query':ROOT/'artifacts/query_splits/gist/shared/test_query.fvecs','--groundtruth':ROOT/'artifacts/query_splits/gist/shared/test_gt.ivecs','--query-order':REFERENCE.parent/'optimized_order.u32','--ours-graph':ROOT/'artifacts/graphs/gist/Ours/gist_Ours_R64_Lbuild400.graph.bin','--input-manifest':ROOT/'results/archive/native_runs/runs/disk_03_graph_build_reuse_20260910_gist/manifests/inputs/05c_gist.json','--disk-index-dir':Path(f['--disk-index-dir']),'--locality-layout-dir':Path(f['--locality-layout-dir'])}
 checks={}
 def check(label,value,expected):
  if value!=expected:raise ValueError(f'{label}: {value} != {expected}')
  checks[label]=value
 check('query_shape',shape(paths['--query']),(800,960));check('gt_rows',shape(paths['--groundtruth'])[0],800)
 check('query_split_sha256',pair_hash(paths['--query'],paths['--groundtruth']),f['--query-split-sha256'])
 for key,flag in [('--query-order','--query-order-sha256'),('--ours-graph','--ours-graph-sha256'),('--input-manifest','--input-manifest-sha256')]:check(flag,sha(paths[key]),f[flag])
 order=list(struct.unpack('<800I',paths['--query-order'].read_bytes()));check('order_permutation',sorted(order),list(range(800)))
 del checks['order_permutation'];checks['order_permutation']=True
 layout=paths['--locality-layout-dir']
 for name,flag in [('graph_compact.pages','--locality-combined-sha256'),('residual.pages','--locality-residual-sha256'),('id_to_slot.u32','--locality-mapping-sha256')]:check(name,sha(layout/name),f[flag])
 manifest=json.loads((layout/'manifest.json').read_text())
 for old,digest in manifest['source_hashes'].items():check('source:'+Path(old).name,sha(Path(old)),digest)
 vq=ROOT/'artifacts/query_splits/gist/shared/validation_query.fvecs';vg=vq.with_name('validation_gt.ivecs')
 check('validation_shape',shape(vq),(200,960));check('validation_gt_rows',shape(vg)[0],200)
 check('validation_order_permutation',sorted(struct.unpack('<200I',VALIDATION_ORDER.read_bytes())),list(range(200)))
 checks['validation_order_permutation']=True
 def records(p):
  n,d=shape(p);raw=p.read_bytes();stride=4*(d+1);return {raw[i*stride:(i+1)*stride]for i in range(n)}
 check('validation_test_vector_overlap',len(records(vq)&records(paths['--query'])),0)
 # Hash every resolved input used by native execution and calibration.
 files=[paths[k]for k in paths if paths[k].is_file()]+[vq,vg,VALIDATION_ORDER]+list(paths['--disk-index-dir'].glob('*'))+[layout/n for n in ['graph_compact.pages','residual.pages','id_to_slot.u32']]
 base=ROOT/'data/gist/gist_base.fvecs'
 original_inputs=json.loads(paths['--input-manifest'].read_text())
 check('base_sha256',sha(base),original_inputs['files']['base']['sha256'])
 hashes={str(p):sha(p)for p in files if p.is_file()}
 hashes[str(base)]=checks['base_sha256']
 result={'status':'verified','reference_command_sha256':sha(REFERENCE/'command.json'),'reference_result_sha256':sha(REFERENCE/'result.json'),'resolved_paths':{k:str(v)for k,v in paths.items()},'checks':checks,'input_hashes':hashes,'widths':WIDTHS,'beam':1,'workers':32,'test_queries':800,'validation_queries':200,'warmup_queries':100,'memory_limit_bytes':LIMIT,'memory_scope':mem['scope'],'environment':mem['environment'],'source_binary_sha256':r['native_binary_sha256'],'current_native_source_sha256':sha(ROOT/'experiments/02_disk_shared_graph/native/src/main.rs'),'status_note':'Configuration verified; fresh baseline parity/performance not run.'}
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'verification.json').write_text(json.dumps(result,indent=2,ensure_ascii=False));return result

def cache_allowance(measurement):
 if not measurement.get('user_address_space_budget_passed') or measurement.get('rlimit_as_bytes')!=LIMIT:raise ValueError('verified baseline memory evidence required')
 peak=measurement['sampled_high_water_bytes']['VmPeak']
 if peak<=0 or peak>=LIMIT:raise ValueError('invalid baseline VmPeak')
 return max(0,LIMIT-peak-SAFETY)

def check_locked(command,stage='test'):
 _,original,_,_=reference();current=flags(command)
 variable={'--query','--groundtruth','--query-order','--query-split-sha256','--query-order-sha256'} if stage=='profile' else set()
 path_keys={'--query','--groundtruth','--query-order','--ours-graph','--input-manifest','--disk-index-dir','--locality-layout-dir'}
 output_keys={'--result-json','--query-trace','--run-id','--native-binary-sha256','--implementation-fingerprint'}
 for k,v in original.items():
  if k not in path_keys|output_keys|variable and current.get(k)!=v:raise ValueError(f'locked parameter changed: {k}')
 allowed=set(original)|{'--integration-beams','--memory-policy','--memory-cache-bytes','--memory-profile-dir','--memory-stats-dir'}
 if set(current)-allowed:raise ValueError('unsupported extra parameters')
 if current.get('--integration-beams')!='1':raise ValueError('beam must be 1')
 verification=json.loads((OUT/'verification.json').read_text())
 for k,v in verification['resolved_paths'].items():
  if k not in variable and current.get(k)!=v:raise ValueError(f'resolved path changed: {k}')
 if current.get('--memory-policy') not in MODES+('profile',):raise ValueError('unknown policy')
 if current.get('--memory-policy') in ('baseline','profile') and current.get('--memory-cache-bytes')!='0':raise ValueError('baseline/profile cannot allocate shared cache')
