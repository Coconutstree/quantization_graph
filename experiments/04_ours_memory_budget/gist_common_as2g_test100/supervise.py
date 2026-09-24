"""User-authorized temporary build suspension, always restored on driver exit."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OUT=ROOT/'results/04_ours_memory_budget/gist_common_as2g_test100_inflight256'
BUILD=2388331


def stop(signum, frame):
    raise KeyboardInterrupt(f'supervisor signal {signum}')


def main():
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP): signal.signal(sig,stop)
    cmd=Path(f'/proc/{BUILD}/cmdline').read_bytes().replace(b'\0',b' ').decode()
    assert 'msmarco_graph_build_20260915/run_diskann_fair' in cmd and '--build-only' in cmd, cmd
    state={'build_pid':BUILD,'build_command':cmd,'supervisor_pid':os.getpid(),'paused_unix':time.time(),'authorization':'user explicitly approved temporary suspension and automatic restoration'}
    child=None
    try:
        os.kill(BUILD,signal.SIGSTOP)
        (OUT/'build_suspension.json').write_text(json.dumps(state,indent=2)+'\n')
        child=subprocess.Popen(['python',str(HERE/'run.py'),'--run'],cwd=ROOT,start_new_session=True)
        state['driver_pid']=child.pid
        (OUT/'build_suspension.json').write_text(json.dumps(state,indent=2)+'\n')
        code=child.wait()
        state['driver_exit_code']=code
    finally:
        if child is not None and child.poll() is None:
            os.killpg(child.pid,signal.SIGINT)
            try: child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL);child.wait()
        try:
            os.kill(BUILD,signal.SIGCONT)
            state['restored_unix']=time.time()
        except ProcessLookupError:
            state['build_already_exited']=True
        (OUT/'build_suspension.json').write_text(json.dumps(state,indent=2)+'\n')


if __name__=='__main__':main()
