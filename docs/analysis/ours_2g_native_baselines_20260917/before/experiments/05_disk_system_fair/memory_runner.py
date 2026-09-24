"""External cgroup-v2 search measurement; no algorithm changes or AS/RSS fallback.

A writable delegated cgroup with memory enabled is required. Each measurement
gets a new child group; the native process joins it before exec/index loading.
Lifecycle wall time is NOT query QPS time. See --help for standalone use.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import shutil
import subprocess
import sys
import time
import uuid

PROTOCOL_ID = 'disk_cgroup_v2_20260917'


class MemoryEnvironmentError(RuntimeError):
    pass


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def read_pairs(path):
    return {key: int(value) for key, value in
            (line.split() for line in Path(path).read_text().splitlines())}


def cgroup_mount(path):
    """Reject normal directories and v1; honor the longest matching mount."""
    path = Path(path).resolve()
    matches = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ', 1)
        mount = Path(left.split()[4].replace('\\040', ' ').replace('\\134', '\\'))
        if path == mount or mount in path.parents:
            matches.append((len(mount.parts), mount, right.split()[0]))
    if not matches or max(matches)[2] != 'cgroup2':
        raise MemoryEnvironmentError(f'not a cgroup-v2 path: {path}')
    return max(matches)[1]


class MemoryGroup:
    def __init__(self, parent, budget):
        self.parent = Path(parent).resolve()
        self.budget = budget
        self.path = None
        self.ancestors = []

    def create(self):
        if not isinstance(self.budget, int) or self.budget <= 0:
            raise MemoryEnvironmentError('memory budget must be a positive integer in bytes')
        mount = cgroup_mount(self.parent)
        if 'memory' not in (self.parent/'cgroup.subtree_control').read_text().split():
            raise MemoryEnvironmentError('parent must already delegate the memory controller')
        for ancestor in (self.parent, *self.parent.parents):
            if ancestor != mount and mount not in ancestor.parents:
                break
            entry = {'path': str(ancestor)}
            for name in ('memory.max', 'memory.high'):
                f = ancestor/name
                if f.exists():
                    value = f.read_text().strip()
                    entry[name] = value
                    if value != 'max' and int(value) < self.budget:
                        raise MemoryEnvironmentError(f'ancestor {f}={value} is below requested budget')
            self.ancestors.append(entry)
        self.path = self.parent/('qgraph05-' + uuid.uuid4().hex)
        try:
            self.path.mkdir()
            for name in ('memory.max', 'memory.swap.max', 'memory.peak', 'memory.current',
                         'memory.stat', 'memory.events', 'memory.swap.current', 'cgroup.procs'):
                if not (self.path/name).exists():
                    raise MemoryEnvironmentError(f'required cgroup interface missing: {name}')
            (self.path/'memory.max').write_text(str(self.budget))
            (self.path/'memory.swap.max').write_text('0')
            if (self.path/'memory.oom.group').exists():
                (self.path/'memory.oom.group').write_text('1')
            if int((self.path/'memory.max').read_text()) != self.budget:
                raise MemoryEnvironmentError('memory.max readback mismatch')
            if int((self.path/'memory.swap.max').read_text()) != 0:
                raise MemoryEnvironmentError('memory.swap.max readback mismatch')
            return self
        except (OSError, MemoryEnvironmentError):
            self.close()
            raise

    def snapshot(self):
        return {
            'memory_limit_bytes': int((self.path/'memory.max').read_text()),
            'swap_limit_bytes': int((self.path/'memory.swap.max').read_text()),
            'cgroup_memory_peak_bytes': int((self.path/'memory.peak').read_text()),
            'cgroup_memory_current_bytes': int((self.path/'memory.current').read_text()),
            'swap_current_bytes': int((self.path/'memory.swap.current').read_text()),
            'memory_events': read_pairs(self.path/'memory.events'),
            'memory_stat': read_pairs(self.path/'memory.stat'),
        }

    def pids(self):
        return [int(x) for x in (self.path/'cgroup.procs').read_text().split()]

    def kill(self):
        if self.path and (self.path/'cgroup.kill').exists():
            (self.path/'cgroup.kill').write_text('1')
        elif self.path:
            for pid in self.pids():
                try: os.kill(pid, signal.SIGKILL)
                except ProcessLookupError: pass

    def close(self):
        if self.path:
            try: self.path.rmdir()
            except FileNotFoundError: pass
            except OSError:
                # Retain the group and its path as evidence if still populated.
                return False
        return True


def check_environment(parent, budget):
    if parent is None:
        raise MemoryEnvironmentError('provide --cgroup-parent pointing to a writable delegated cgroup')
    group = MemoryGroup(parent, budget)
    try:
        group.create()
        return {'status': 'ready', 'parent': str(group.parent),
                'visible_ancestor_limits': group.ancestors, **group.snapshot()}
    except OSError as exc:
        raise MemoryEnvironmentError(str(exc)) from exc
    finally:
        group.close()


def run_measured(command, *, evidence_path, log_path, budget_bytes=None,
                 cgroup_parent=None, reference=False, cwd=None, env=None, timeout=86400,
                 cpu_affinity=None, numa_node=None):
    evidence_path, log_path = Path(evidence_path), Path(log_path)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    if evidence_path.exists() or log_path.exists():
        raise FileExistsError('measurement outputs already exist; use a fresh run/attempt')
    if not reference and (budget_bytes is None or cgroup_parent is None):
        # Do not launch anything when a budget cannot be enforced.
        error = 'disk measurement requires budget_bytes and a delegated cgroup_parent'
        write_json(evidence_path, {'protocol_id': PROTOCOL_ID, 'status': 'blocked_environment',
                                  'failure_reason': error, 'command': list(command)})
        raise MemoryEnvironmentError(error)
    if reference and (budget_bytes is not None or cgroup_parent is not None):
        raise ValueError('unconstrained reference cannot claim a cgroup budget')
    command = [str(x) for x in command]
    policy = {'cpu_affinity': sorted(set(cpu_affinity)) if cpu_affinity is not None else None,
              'numa_node': numa_node, 'numactl': shutil.which('numactl') if numa_node is not None else None}
    if policy['cpu_affinity'] is not None and (not policy['cpu_affinity'] or
            not set(policy['cpu_affinity']) <= os.sched_getaffinity(0)):
        raise MemoryEnvironmentError('requested CPUs are outside the allowed affinity')
    if numa_node is not None and (numa_node < 0 or policy['numactl'] is None):
        raise MemoryEnvironmentError('explicit NUMA binding requires numactl and a valid node')
    executable = Path(command[0]).resolve()
    if not executable.is_file():
        raise FileNotFoundError(f'command executable must resolve to a file: {executable}')
    marker = evidence_path.with_suffix('.launch.json')
    if marker.exists():
        raise FileExistsError(marker)
    evidence = {
        'protocol_id': PROTOCOL_ID, 'status': 'running', 'command': command,
        'binary_sha256': digest(executable), 'memory_enforcement': 'none' if reference else 'cgroup_v2',
        'memory_limit_bytes': budget_bytes, 'memory_limit_scope': 'unconstrained_reference' if reference
            else 'cgroup_including_descendants_file_cache_and_kernel_memory',
        'measurement_scope': 'process_lifecycle_including_load_warmup_and_query',
        'wall_time_scope': 'whole_process_not_query_qps', 'started_unix': time.time(),
        'category_attribution_complete': False, 'reference_only': reference,
        'budget_verified': False,
        'launch_policy': policy,
    }
    group = None
    try:
        if not reference:
            group = MemoryGroup(cgroup_parent, budget_bytes)
            group.create()
            evidence.update(cgroup_path=str(group.path), visible_ancestor_limits=group.ancestors,
                            events_before=group.snapshot()['memory_events'])
    except (OSError, MemoryEnvironmentError) as exc:
        evidence.update(status='blocked_environment', failure_reason=str(exc))
        write_json(evidence_path, evidence)
        raise MemoryEnvironmentError(str(exc)) from exc
    write_json(evidence_path, evidence)
    proc = None
    started = time.monotonic()
    peaks = {'VmPeak': 0, 'VmHWM': 0, 'VmSwap': 0}
    try:
        child = [sys.executable, str(Path(__file__).resolve()), '--child',
                 str(group.path) if group else '-', str(marker.resolve()), json.dumps(policy), *command]
        with log_path.open('x') as log:
            log.write('argv=' + json.dumps(command) + '\n'); log.flush()
            proc = subprocess.Popen(child, cwd=cwd, env=env, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            evidence['pid'] = proc.pid
            while True:
                try:
                    for line in Path(f'/proc/{proc.pid}/status').read_text().splitlines():
                        key, _, value = line.partition(':')
                        if key in peaks:
                            peaks[key] = max(peaks[key], int(value.split()[0])*1024)
                except (FileNotFoundError, ProcessLookupError): pass
                pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
                if pid:
                    proc.returncode = os.waitstatus_to_exitcode(status)
                    break
                if time.monotonic()-started > timeout:
                    evidence['timed_out'] = True
                    if group: group.kill()
                    try: os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    _, status, usage = os.wait4(proc.pid, 0)
                    proc.returncode = os.waitstatus_to_exitcode(status)
                    break
                time.sleep(0.05)
        launch = json.loads(marker.read_text()) if marker.exists() else {}
        evidence.update(exit_code=proc.returncode, elapsed_seconds=time.monotonic()-started,
                        process_peak_rss_bytes=int(usage.ru_maxrss)*1024,
                        process_peak_rss_source='wait4_ru_maxrss_linux_kib',
                        sampled_vm_peak_bytes=peaks['VmPeak'], sampled_rss_high_water_bytes=peaks['VmHWM'],
                        sampled_swap_bytes=peaks['VmSwap'], launch=launch)
        if group:
            evidence.update(group.snapshot())
            evidence['remaining_pids'] = group.pids()
            if evidence['remaining_pids']: group.kill()
        events = evidence.get('memory_events', {})
        if events.get('oom_kill', 0) or events.get('oom_group_kill', 0):
            state = 'budget_exceeded'
        elif evidence.get('timed_out'):
            state = 'timeout'
        elif not launch.get('exec_ready'):
            state = 'blocked_environment'
        elif proc.returncode != 0 or evidence.get('remaining_pids'):
            state = 'runtime_error'
        elif not reference and (evidence['cgroup_memory_peak_bytes'] > budget_bytes or events.get('oom', 0)):
            state = 'resource_review_required'
        else:
            state = 'completed'
        evidence.update(status=state, budget_verified=(state=='completed' and not reference
                            and launch.get('joined_cgroup')==str(group.path)
                            and evidence['memory_limit_bytes']==budget_bytes
                            and evidence['swap_limit_bytes']==0))
        return evidence
    except BaseException as exc:
        evidence.update(status='measurement_error', failure_reason=str(exc))
        if proc and proc.returncode is None:
            if group: group.kill()
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            _, status, _ = os.wait4(proc.pid, 0)
            proc.returncode = os.waitstatus_to_exitcode(status)
        raise
    finally:
        if group: evidence['cgroup_removed'] = group.close()
        evidence['finished_unix'] = time.time()
        write_json(evidence_path, evidence)


def child_exec(args):
    group, marker, policy_json, *command = args
    policy = json.loads(policy_json)
    state = {'exec_ready': False, 'joined_cgroup': None}
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            raise MemoryEnvironmentError('inherited finite RLIMIT_AS hard limit; launch from unrestricted parent')
        resource.setrlimit(resource.RLIMIT_AS, (resource.RLIM_INFINITY, hard))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if policy['cpu_affinity'] is not None:
            os.sched_setaffinity(0, policy['cpu_affinity'])
            if sorted(os.sched_getaffinity(0)) != policy['cpu_affinity']:
                raise MemoryEnvironmentError('CPU affinity readback mismatch')
        state['cpu_affinity'] = sorted(os.sched_getaffinity(0))
        state['requested_numa_node'] = policy['numa_node']
        if group != '-':
            path = Path(group)
            (path/'cgroup.procs').write_text(str(os.getpid()))
            if os.getpid() not in [int(x) for x in (path/'cgroup.procs').read_text().split()]:
                raise MemoryEnvironmentError('cgroup membership readback failed')
            state['joined_cgroup'] = str(path)
        state.update(exec_ready=True, rlimit_as='unlimited')
        write_json(marker, state)
        if policy['numa_node'] is not None:
            command = [policy['numactl'], '--membind='+str(policy['numa_node']), *command]
        os.execvpe(command[0], command, os.environ)
    except BaseException as exc:
        state.update(exec_ready=False, error=str(exc)); write_json(marker, state)
        print(str(exc), file=sys.stderr)
        return 125


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--child': return child_exec(sys.argv[2:])
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cgroup-parent', type=Path)
    p.add_argument('--budget-gib', type=float, default=4)
    p.add_argument('--check', action='store_true')
    p.add_argument('--output', type=Path)
    p.add_argument('--timeout', type=float, default=86400)
    p.add_argument('command', nargs=argparse.REMAINDER)
    a=p.parse_args()
    if not 0<a.budget_gib<1024*1024: p.error('budget-gib must be finite and positive')
    budget=int(a.budget_gib*(1<<30))
    try:
        if a.check:
            print(json.dumps(check_environment(a.cgroup_parent,budget),indent=2)); return 0
        command=a.command[1:] if a.command[:1]==['--'] else a.command
        if not a.output or not command:p.error('--output and command are required')
        a.output.mkdir(parents=True,exist_ok=False)
        result=run_measured(command,evidence_path=a.output/'memory.json',log_path=a.output/'terminal.log',
                            cgroup_parent=a.cgroup_parent,budget_bytes=budget,timeout=a.timeout)
        print(json.dumps(result,indent=2));return 0 if result['budget_verified'] else 2
    except (MemoryEnvironmentError,OSError,ValueError) as exc:
        print(json.dumps({'status':'blocked_environment','reason':str(exc)}));return 2


if __name__=='__main__':raise SystemExit(main())
