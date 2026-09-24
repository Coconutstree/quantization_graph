"""Sequential, detached v2 queue. User-authorized execution; never resumes v1."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

from protocol import HERE, OUT, ROOT, MODES
from run import assert_build_current


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'queue.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert_build_current()
        for mode in ('profile',) + MODES:
            if (OUT / mode).exists():
                raise FileExistsError(f'refuse to overwrite existing run: {OUT / mode}')
        state = {'pid': os.getpid(), 'status': 'running', 'started_unix': time.time(),
                 'current': 'baseline', 'completed': [], 'order': ['baseline', 'profile', *MODES[1:]]}

        def save():
            state['updated_unix'] = time.time()
            temporary = OUT / 'queue_state.json.tmp'
            temporary.write_text(json.dumps(state, indent=2) + '\n')
            temporary.replace(OUT / 'queue_state.json')
            (OUT / 'execution_state.json').write_text(json.dumps(state, indent=2) + '\n')

        def report():
            subprocess.run([sys.executable, str(HERE / 'report.py')], cwd=ROOT, check=True)

        try:
            for mode in state['order']:
                state['current'] = mode
                save()
                report()
                stage = mode if mode in ('baseline', 'profile') else 'test'
                command = [sys.executable, '-u', str(HERE / 'run.py'), '--stage', stage, '--execute']
                if stage == 'test':
                    command += ['--modes', mode]
                print(f'{time.strftime("%Y-%m-%d %H:%M:%S")} START {mode}', flush=True)
                with subprocess.Popen(command, cwd=ROOT) as child:
                    state['stage_pid'] = child.pid
                    save()
                    code = child.wait()
                state.pop('stage_pid', None)
                if code:
                    raise RuntimeError(f'{mode} exited with code {code}; remaining queue stopped')
                state['completed'].append(mode)
                save()
                report()
                print(f'{time.strftime("%Y-%m-%d %H:%M:%S")} ACCEPTED {mode}', flush=True)
            state['status'] = 'completed'
            state['current'] = None
        except BaseException as error:
            state['status'] = 'failed'
            state['error'] = str(error)
            traceback.print_exc()
            raise
        finally:
            save()
            report()


if __name__ == '__main__':
    main()
