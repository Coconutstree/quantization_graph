"""Serialize measurements and restore the previously identified competing build."""
import json,os,signal,subprocess,sys,time,traceback
from pathlib import Path
from prepare import OUT,HERE,dump
pid=2388331;paused=False
try:
 p=Path(f'/proc/{pid}/cmdline')
 if p.exists() and b'msmarco_graph_build_20260915/run_diskann_fair' in p.read_bytes():
  status=Path(f'/proc/{pid}/status').read_text()
  if '\nState:\tT' not in status:
   os.kill(pid,signal.SIGSTOP);paused=True
   dump(OUT/'competing_build.json',dict(pid=pid,paused=time.time()))
 def stop(signum,frame):raise KeyboardInterrupt(signum)
 signal.signal(signal.SIGTERM,stop)
 subprocess.run([sys.executable,str(HERE/'run.py'),sys.argv[1] if len(sys.argv)>1 else 'all'],check=True)
finally:
 if paused:
  os.kill(pid,signal.SIGCONT)
  info=json.loads((OUT/'competing_build.json').read_text());info['resumed']=time.time();dump(OUT/'competing_build.json',info)
 subprocess.run([sys.executable,str(HERE/'report.py')],check=False)
