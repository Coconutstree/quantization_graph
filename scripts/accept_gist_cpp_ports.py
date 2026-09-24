"""Real GIST admission proof for the two pinned C++ disk ports, not a paper sweep."""
from pathlib import Path
import json,sys,os
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from disk_bench.admission import prepare_reference,finalize,replace,write
from disk_bench.protocol import sha256,attach_resource_evidence,PROTOCOL_ID
from disk_bench.memory_runner import run_measured
from disk_bench.storage_precondition import prepare_search,PROTOCOL as STORAGE
from disk_bench.native_contract import SPECS_BY_KEY,validate_artifact
out=ROOT/'results/diagnostics/03_cpp_admission_gist_20260921_r2';out.mkdir(exist_ok=False)
cpus=[i for i in range(0,160,4) if i in os.sched_getaffinity(0)][:32]
env=dict(os.environ,PATH=str(ROOT/'work/tools/numactl/root/usr/bin')+os.pathsep+os.environ['PATH'],MALLOC_ARENA_MAX='2',OMP_NUM_THREADS='32',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',QG05_FAST='0')
os.environ['PATH']=env['PATH']
for method,stem in [('Glass-NSG-DiskPort','glass'),('SymphonyQG-DiskPort','symphonyqg')]:
 folder=out/stem;folder.mkdir()
 old=ROOT/'results/archive/03_disk_system_unadmitted_20260921/gist/test_L_400_w_32/raw'/method/'command.json'
 cmd=json.loads(old.read_text());cmd[0]=str(ROOT/f'build/disk/native/qgraph05_{stem}_disk_port')
 artifact=folder/'result.json';query=ROOT/'artifacts/query_splits/gist/shared/validation_query.fvecs';gt=query.with_name('validation_gt.ivecs')
 inputs=ROOT/'results/archive/native_runs/runs/disk_03_graph_build_reuse_20260910_gist/manifests/inputs/05c_gist.json'
 values={'--phase':'validation','--query':str(query),'--groundtruth':str(gt),'--query-order':str(ROOT/'results/diagnostics/symphony_shared_cache_20260921_verified/order.u32'),'--input-manifest':str(inputs),'--input-manifest-sha256':sha256(inputs),'--query-split-sha256':sha256(query),'--native-binary-sha256':sha256(cmd[0]),'--result-json':str(artifact),'--query-trace':str(folder/'queries.jsonl'),'--integration-widths':'10,20,40,60,100,160,240,400,580','--run-id':'gist_cpp_admission_w9','--cache-mode':'standard','--warmup-queries':'100','--workers':'32','--search-dram-budget-gib':'4'}
 for flag,value in values.items():replace(cmd,flag,value)
 replace(cmd,'--query-order-sha256',sha256(values['--query-order']))
 write(folder/'command.json',cmd)
 print(method,'reference',flush=True)
 cmd,manifest=prepare_reference(cmd,artifact,cpu_affinity=cpus,numa_node=0,env=env)
 storage=folder/'storage.json';prepare_search(cmd,storage)
 print(method,'measured',flush=True)
 resources=folder/'resources.json';e=run_measured(cmd,evidence_path=resources,log_path=folder/'terminal.log',budget_bytes=4*2**30,rss_budget=True,cpu_affinity=cpus,numa_node=0,env=env,timeout=3600)
 if e['status']!='completed':raise RuntimeError(e)
 doc=attach_resource_evidence(artifact,resources,e);doc.update(storage_precondition_protocol=STORAGE,storage_precondition_path=str(storage),storage_precondition_sha256=sha256(storage));write(artifact,doc)
 expected={key:doc[key] for key in ('dataset','phase','run_id','repeat_id','workers','storage_mode','cache_mode','implementation_fingerprint','native_binary_sha256','input_manifest_sha256','query_split_sha256','query_order_sha256','query_order_seed','warmup_queries','search_dram_budget_gib')};expected['protocol_id']=PROTOCOL_ID
 kw=dict(spec=SPECS_BY_KEY['05c:'+method],expected=expected,disk_root=ROOT/'work/05_disk_system_fair/disk_root')
 result=finalize(artifact,manifest,**kw);validate_artifact(artifact,**kw)
 print(method,'ADMITTED',flush=True)
