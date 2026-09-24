import sys,os,json,argparse
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path.cwd()/'src'))
from disk_bench import orchestrator
from disk_bench import official_system03 as o
from disk_bench.common import write_fvecs,write_ivecs
from disk_bench.native_contract import specs_for,sha256_file,validate_artifact,flatten_artifact,atomic_write_csv
from types import SimpleNamespace
os.environ['PATH']=str(Path.cwd()/'work/tools/numactl/root/usr/bin')+':'+os.environ['PATH']
parser=argparse.ArgumentParser(description='Official CLI correctness fixture only, not a performance benchmark')
parser.add_argument('--output',type=Path,required=True)
a=parser.parse_args()
root=a.output.resolve();root.mkdir(parents=True,exist_ok=False)
data=root/'data'/'fixture';data.mkdir(parents=True,exist_ok=True)
rng=np.random.default_rng(319)
base=rng.normal(size=(1024,64)).astype('f4');q=rng.normal(size=(16,64)).astype('f4');warm=rng.normal(size=(100,64)).astype('f4')
write_fvecs(data/'fixture_base.fvecs',base);write_fvecs(data/'test.fvecs',q);write_fvecs(data/'warm.fvecs',warm)
write_ivecs(data/'gt.ivecs',np.argsort(((q[:,None]-base)**2).sum(2),axis=1)[:,:10])
write_ivecs(data/'validation.ivecs',np.argsort(((warm[:,None]-base)**2).sum(2),axis=1)[:,:10])
manifest=root/'input.json';manifest.write_text('{}')
for method,name in o.METHODS.items():
 spec=specs_for('05c',[method])[0]
 binary=Path.cwd()/'build/03_fixed_official_20260924'/name/('apps' if name=='aisaq' else 'tests')/'search_disk_index'
 port=dict(artifact_bridge=o.KIND,command=[str(binary)],official_build_root=str(Path.cwd()/'build/03_fixed_official_20260924'),binary_sha256=sha256_file(binary),implementation_fingerprint='correctness-fixture-only')
 item=SimpleNamespace(spec=spec,repeat_id=0,workers=32,budget_gib=4)
 lock=root/(name+'.lock.json');lock.write_text(json.dumps(dict(system03_protocol=o.PROTOCOL,selected={method+'::hybrid_disk':dict(config_id='beam4',official_configuration=o.configuration(method,'fixture'))})))
 args=dict(port=port,item=item,dataset='fixture',run_id='diagnostic_correctness_only',data_root=root/'data',disk_root=root/'disk',query_path=data/'test.fvecs',gt_path=data/'gt.ivecs',split_sha256='fixture',input_manifest=manifest,input_manifest_sha256=sha256_file(manifest),seed=1729,tuning_lock=lock,measurement_width=20,warmup_query=data/'warm.fvecs',cpu_affinity=tuple(range(32)),numa_node=0,experiment='correctness_fixture')
 for phase in ('export','validate','test'):
  print(method,phase,flush=True)
  artifact=root/'runs'/name/phase/'result.json'
  current=dict(args)
  if phase=='validate':current.update(query_path=data/'warm.fvecs',gt_path=data/'validation.ivecs',tuning_lock=None)
  r=orchestrator._invoke_port(**current,phase=phase,artifact_path=artifact,search_dram_budget_gib=4,candidate_row_offset=0)
  print(r['formal_ready'],r.get('peak_rss_bytes'),flush=True)
  if phase=='validate':
   content=json.loads(lock.read_text());content['selected'][method+'::hybrid_disk']['resource_preflight']=dict(path=str(artifact.resolve()),sha256=sha256_file(artifact))
   lock.write_text(json.dumps(content))
  if phase=='test':
   o.validate(json.loads(artifact.read_text()),{},root/'disk')
   admitted=validate_artifact(artifact,spec=spec,expected=dict(method=method,phase='test'),disk_root=root/'disk')
   atomic_write_csv(root/(name+'_row_contract.csv'),flatten_artifact(artifact,admitted))
   for field,value in [('workers',16),('native_binary_sha256','0'*64)]:
    bad=dict(r);bad[field]=value
    try:o.validate(bad,{},root/'disk')
    except o.ContractError:pass
    else:raise AssertionError('accepted altered '+field)
