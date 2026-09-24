"""Fail-closed cgroup v2 launcher; monitor stays outside the constrained child."""
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import time
import uuid


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    tmp.replace(path)


def counters(path):
    return {k: int(v) for k, v in (line.split() for line in path.read_text().splitlines())}


def host_snapshot():
    result = {name: Path('/proc', name).read_text() for name in ('loadavg', 'diskstats', 'meminfo')}
    result.update(uname=list(os.uname()), monitor_affinity=sorted(os.sched_getaffinity(0)))
    return result


def inspect(root):
    root = Path(root).resolve()
    reasons = []
    for name in ('cgroup.controllers', 'cgroup.subtree_control', 'cgroup.procs'):
        if not (root / name).exists():
            reasons.append('missing ' + name)
    if not reasons:
        if 'memory' not in (root / 'cgroup.subtree_control').read_text().split():
            reasons.append('memory controller not enabled for children')
        if not os.access(root, os.W_OK):
            reasons.append('delegated root is not writable')
        if (root / 'cgroup.procs').read_text().strip():
            reasons.append('delegated parent contains processes; use an empty dedicated subtree')
    return dict(root=str(root), ready=not reasons, reasons=reasons)


def classify(exit_code, events, log, timed_out=False):
    if timed_out:
        return 'timeout'
    if events.get('oom_kill', 0) or events.get('oom', 0):
        return 'cgroup_oom'
    if exit_code == 0:
        return 'completed'
    if 'resident PQ and worker scratch require' in log or 'memory deficit' in log:
        return 'admission_rejected'
    if any(s in log for s in ('memory allocation', 'Cannot allocate memory', 'std::bad_alloc', 'os error 12')):
        return 'allocation_failure'
    if any(s in log for s in ('unsupported', 'missing argument', 'invalid argument')):
        return 'parameter_error'
    return 'program_error'


def measure(command, folder, root, budget, timeout=86400):
    folder, root = Path(folder), Path(root).resolve()
    check = inspect(root)
    if not check['ready']:
        raise RuntimeError('cgroup unavailable: ' + '; '.join(check['reasons']))
    for ancestor in [root, *root.parents]:
        limit = ancestor / 'memory.max'
        if limit.exists():
            value = limit.read_text().strip()
            if value != 'max' and int(value) < budget:
                raise RuntimeError('ancestor memory.max is smaller than requested budget: ' + str(ancestor))
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / 'memory_measurement.json').exists():
        raise RuntimeError('preserve previous measurement: ' + str(folder))
    cg = root / ('gist-' + uuid.uuid4().hex)
    cg.mkdir()
    proc = None
    evidence = dict(budget_bytes=budget, cgroup=str(cg), started_unix=time.time(),
                    scope='cgroup_v2_memory_accounting', sample_interval_seconds=0.02)
    dump(folder / 'host_before.json', host_snapshot())
    try:
        for name, value in [('memory.max', budget), ('memory.swap.max', 0), ('memory.oom.group', 1)]:
            (cg / name).write_text(str(value))
            if (cg / name).read_text().strip() != str(value):
                raise RuntimeError('cgroup limit readback mismatch: ' + name)
        if not (cg / 'memory.peak').exists():
            raise RuntimeError('kernel must expose memory.peak')
        if not (cg / 'cgroup.kill').exists():
            raise RuntimeError('kernel must expose cgroup.kill for isolated cleanup')
        env = {k: v for k, v in os.environ.items()
               if not k.startswith('QG05_') and k not in ('LD_PRELOAD', 'LD_AUDIT')}
        env.update(MALLOC_ARENA_MAX='2', OMP_DYNAMIC='FALSE', OPENBLAS_NUM_THREADS='1',
                   MKL_NUM_THREADS='1', QG05_MEASURE_WHOLE_PROCESS='1')

        def enter():
            # Move BEFORE exec/load. Parent is never moved. No transient root process.
            (cg / 'cgroup.procs').write_text(str(os.getpid()))
            resource.setrlimit(resource.RLIMIT_AS, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            if str(os.getpid()) not in (cg / 'cgroup.procs').read_text().split():
                raise RuntimeError('child cgroup membership mismatch')
            (folder / 'child_constraint.json').write_text(json.dumps(dict(
                pid=os.getpid(), cgroup=str(cg), membership_verified=True,
                rlimit_as=list(resource.getrlimit(resource.RLIMIT_AS)))))

        dump(folder / 'command.json', command)
        membership = False
        phase, width, offset = 'loading', None, 0
        rss_peak = 0
        timed_out = False
        with (folder / 'terminal.log').open('w') as log, (folder / 'rss_samples.jsonl').open('w') as samples:
            proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                    env=env, preexec_fn=enter, start_new_session=True)
            evidence['pid'] = proc.pid
            while True:
                with (folder / 'terminal.log').open() as reader:
                    reader.seek(offset)
                    for line in reader:
                        match = re.search(r'(?:width=| L=)(\d+)', line)
                        if match:
                            width = int(match[1])
                        if ' search:' in line:
                            phase = 'warmup'
                        if 'name=after_warmup ' in line or 'warmup complete; starting disk batch' in line:
                            phase = 'measurement'
                        if 'name=after_measurement_and_workers ' in line or 'disk batch complete' in line:
                            phase = 'post_measurement'
                    offset = reader.tell()
                sample = dict(unix=time.time(), phase=phase, width=width,
                              cgroup_current_bytes=int((cg / 'memory.current').read_text()))
                try:
                    membership |= str(proc.pid) in (cg / 'cgroup.procs').read_text().split()
                    evidence['rlimit_as'] = list(resource.prlimit(proc.pid, resource.RLIMIT_AS))
                    for line in Path(f'/proc/{proc.pid}/status').read_text().splitlines():
                        k, _, value = line.partition(':')
                        if k in ('VmRSS', 'VmHWM', 'VmPeak', 'VmSwap'):
                            sample[k] = int(value.split()[0]) * 1024
                    rss_peak = max(rss_peak, sample.get('VmRSS', 0), sample.get('VmHWM', 0))
                except (FileNotFoundError, ProcessLookupError):
                    pass
                samples.write(json.dumps(sample) + '\n')
                pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
                if pid:
                    proc.returncode = os.waitstatus_to_exitcode(status)
                    break
                if time.time() - evidence['started_unix'] > timeout:
                    timed_out = True
                    (cg / 'cgroup.kill').write_text('1')
                time.sleep(0.02)
        events = counters(cg / 'memory.events')
        text = (folder / 'terminal.log').read_text()
        evidence.update(exit_code=proc.returncode, membership_verified=membership,
                        limits_verified=True, memory_max_bytes=int((cg / 'memory.max').read_text()),
                        swap_max_bytes=int((cg / 'memory.swap.max').read_text()),
                        cgroup_peak_bytes=int((cg / 'memory.peak').read_text()),
                        memory_events=events, memory_stat=counters(cg / 'memory.stat'),
                        wait4_peak_rss_bytes=int(usage.ru_maxrss) * 1024,
                        observed_peak_rss_bytes=max(rss_peak, int(usage.ru_maxrss) * 1024),
                        rss_note='max sampled RSS/HWM and wait4; native clear_refs can reset HWM',
                        timed_out=timed_out, finished_unix=time.time(),
                        classification=classify(proc.returncode, events, text, timed_out))
        child = json.loads((folder / 'child_constraint.json').read_text())
        membership |= child['pid'] == proc.pid and child['cgroup'] == str(cg) and child['membership_verified']
        evidence['membership_verified'] = membership
        evidence.setdefault('rlimit_as', child['rlimit_as'])
        if not membership or evidence.get('rlimit_as') != [-1, -1]:
            evidence['classification'] = 'constraint_verification_failure'
        dump(folder / 'memory_measurement.json', evidence)
        dump(folder / 'host_after.json', host_snapshot())
        return evidence
    finally:
        if proc is not None and proc.returncode is None:
            (cg / 'cgroup.kill').write_text('1')
            proc.wait()
        if (cg / 'cgroup.procs').read_text().strip():
            (cg / 'cgroup.kill').write_text('1')
        try:
            cg.rmdir()
        except OSError as exc:
            dump(folder / 'cleanup_error.json', dict(cgroup=str(cg), error=str(exc)))
