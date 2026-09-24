from pathlib import Path
import json,sys,csv,subprocess
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'baselines/svs/python'));import svs
out=ROOT/'results/diagnostics/03_other_methods_admission_20260921/og_distance';out.mkdir(exist_ok=False)
index=ROOT/'work/05_disk_system_fair/disk_root/05_disk_system_fair/05C_disk_system_fair/agnews/OG-LVQ-DiskPort/hybrid_disk'
official=ROOT/'artifacts/indexes/legacy_disk_store/05_disk_system_fair/05C_disk_system_fair/agnews/OG-LVQ-DiskPort/hybrid_disk/official_svs'
query=ROOT/'artifacts/query_splits/agnews/shared/validation_query.fvecs'
subprocess.run([str(ROOT/'build/disk/native/qgraph05_og_lvq_distance_probe'),str(index),str(query),str(out/'native.csv')],check=True)
reference=svs.Vamana(str(official/'config'),svs.GraphLoader(str(official/'graph')),svs.LVQLoader(str(official/'data')),svs.DistanceType.L2,num_threads=1)
x=np.fromfile(query,dtype='<f4').reshape(-1,1025)[:,1:]
rows=[]
for qid,id,value in csv.reader((out/'native.csv').open()):
 expected=reference.get_distance(int(id),np.ascontiguousarray(x[int(qid)]));observed=float(value)
 rows.append({'qid':int(qid),'id':int(id),'official':expected,'native':observed,'absolute_error':abs(expected-observed),'relative_error':abs(expected-observed)/max(abs(expected),1e-12)})
report={'dataset':'agnews','comparisons':len(rows),'max_absolute_error':max(r['absolute_error'] for r in rows),'max_relative_error':max(r['relative_error'] for r in rows),'bitwise_equal':all(r['official']==r['native'] for r in rows),'allclose_rtol_1e_5':all(np.isclose(r['official'],r['native'],rtol=1e-5,atol=1e-6) for r in rows),'formal_ready':False,'scope':'official Vamana.get_distance vs actual disk-port lvq_distance; sampled pairs, not end-to-end search parity'}
(out/'pairs.json').write_text(json.dumps(rows,indent=2)+'\n');(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
