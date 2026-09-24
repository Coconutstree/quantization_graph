"""Run one comparison at a time and restore the existing competing build."""
import json,os,signal,subprocess,sys,time
from pathlib import Path
from prepare import HERE,OUT,dump
pid=2388331;paused=False;note=OUT/f'competing_build_{time.time_ns()}.json'
def stop(signum,frame):raise KeyboardInterrupt(signum)
try:
 signal.signal(signal.SIGTERM,stop)
 p=Path(f'/proc/{pid}/cmdline')
 if p.exists()and b'msmarco_graph_build_20260915/run_diskann_fair'in p.read_bytes()and'\nState:\tT'not in Path(f'/proc/{pid}/status').read_text():
  os.kill(pid,signal.SIGSTOP);paused=True;dump(note,dict(pid=pid,paused=time.time()))
 subprocess.run([sys.executable,str(HERE/'run.py'),*sys.argv[1:]],check=True)
finally:
 if paused:
  os.kill(pid,signal.SIGCONT);value=json.loads(note.read_text());value['resumed']=time.time();dump(note,value)
