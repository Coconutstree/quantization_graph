"""Paired physical-layout A/B at fixed 538 MiB and full 960-dimensional codes."""
import importlib.util,json,os,signal,time,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('joint_runner',HERE.parent/'gist_joint_optimization/run.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
OLD=r.OUT
r.OUT=ROOT/'results/04_ours_memory_budget/gist_route_locality'
r.WORK=ROOT/'work/ours_memory_budget/gist_route_locality'
r.s.OUT=r.OUT;r.s.WORK=r.WORK;r.s.BIN=r.WORK/'ours_gist_route_locality'
original_inputs=r.inputs
LAYOUT=False
def inputs(split):
 f=original_inputs(split)
 f['--implementation-fingerprint']='route-code-bfs-physical-layout'
 if LAYOUT:
  f['--disk-index-dir']=str(r.WORK/'index')
  f['--routing-code-layout']='bfs'
 return f
r.inputs=inputs

def main():
 global LAYOUT
 build=r.read(r.WORK/'build.json');assert r.s.sha(r.s.BIN)==build['binary_sha256']
 for p,h in build['sources'].items():assert r.s.sha(p)==h
 r.s.dump(r.OUT/'build.json',build)
 r.s.dump(r.OUT/'calibration/lock.json',r.read(OLD/'calibration/lock.json'))
 configs={'original_i256':dict(mode='paged',record=0,inflight=256),'bfs_i256':dict(mode='paged',record=0,inflight=256)}
 r.s.dump(r.OUT/'experiment.json',dict(configs=configs,budget_mib=538,route_page_quota_mib=16,repeats=2,dimension=960,change='only code physical order; factors retain logical order; existing BFS Arc reused',test_queries=800,widths=[100]))
 for split in ['tune','test']:
  reference=OLD/('tune/A1_resident_r0_r1' if split=='tune' else 'test/T0_resident_r0_r1')
  for rep in [1,2]:
   for name in list(configs) if rep==1 else list(reversed(configs)):
    LAYOUT=name.startswith('bfs')
    r.execute(name,configs[name],split,rep,reference=reference,budget=538)
  r.s.dump(r.OUT/f'{split}_completed.json',dict(passed=True))
 r.s.dump(r.OUT/'state.json',dict(status='completed',updated=time.time()))

if __name__=='__main__':
 pid=2388331;paused=False
 try:
  p=Path(f'/proc/{pid}/cmdline')
  if p.exists() and b'msmarco_graph_build_20260915/run_diskann_fair' in p.read_bytes():
   assert '\nState:\tT' not in Path(f'/proc/{pid}/status').read_text()
   os.kill(pid,signal.SIGSTOP);paused=True
   r.s.dump(r.OUT/'competing_build.json',dict(pid=pid,paused=time.time()))
  def terminate(signum,frame):raise KeyboardInterrupt(signum)
  signal.signal(signal.SIGTERM,terminate)
  main()
 except BaseException as e:
  r.s.dump(r.OUT/'state.json',dict(status='failed',error=str(e),traceback=traceback.format_exc()));raise
 finally:
  if paused:
   os.kill(pid,signal.SIGCONT)
   note=r.read(r.OUT/'competing_build.json');note['resumed']=time.time();r.s.dump(r.OUT/'competing_build.json',note)
