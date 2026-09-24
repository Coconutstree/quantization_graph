"""Build isolated dimension ablation only after cache-selection measurements finish."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import hashlib
ROOT=Path(__file__).resolve().parents[1]
queue=ROOT/'results/diagnostics/gist_cache_selection_20260923'
work=ROOT/'work/06_resident_20260923'
work.mkdir(exist_ok=False)
status=work/'status.json'
def write(**kw):status.write_text(json.dumps(kw,indent=2)+'\n')
try:
 write(status='waiting_for_cache_selection')
 while json.loads((queue/'status.json').read_text())['status']=='running':time.sleep(5)
 if json.loads((queue/'status.json').read_text())['status']!='completed':raise ValueError('cache selection failed; 06 not launched')
 sys.path.insert(0,str(ROOT/'scripts'))
 from remeasure_cache_serial import active_io
 while active_io():time.sleep(5)
 write(status='building_isolated_binary')
 env=dict(os.environ,CARGO_BUILD_JOBS='4',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
 command=['cargo','build','--release','--manifest-path','src/graph_core/Cargo.toml','--bin','qgraph05_shared_graph_port','--target-dir','work/cache_allocation_build']
 subprocess.run(command,cwd=ROOT,env=env,check=True)
 subprocess.run(['cargo','test','--release','--manifest-path','src/graph_core/Cargo.toml','--lib','disk_port::pca::tests','--target-dir','work/cache_allocation_build'],cwd=ROOT,env=env,check=True)
 binary=work/'qgraph06_resident_port';shutil.copy2(ROOT/'work/cache_allocation_build/release/qgraph05_shared_graph_port',binary)
 h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
 (work/'build_environment.json').write_text(json.dumps(dict(command=command,binary_sha256=h(binary),rustc=subprocess.check_output(['rustc','--version'],text=True).strip(),sources={str(p.relative_to(ROOT)):h(p) for folder in ['experiments/02_disk_shared_graph/native/src','src/graph_core/src'] for p in (ROOT/folder).rglob('*.rs')}),indent=2))
 write(status='running_06',binary=str(binary))
 subprocess.run([sys.executable,str(ROOT/'scripts/run_06_resident_dimensions.py'),'--binary',str(binary),'--selection-queue',str(queue),'--output',str(ROOT/'results/diagnostics/gist_06_resident_20260923')],cwd=ROOT,env=env,check=True)
 write(status='completed')
except Exception as exc:write(status='failed',error=repr(exc));raise
