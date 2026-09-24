"""Separate binary to probe higher shared I/O concurrency; scratch windows stay 64."""
import json
import shutil
import subprocess
from prepare import ROOT, WORK, sha


def main():
    native=WORK/'native_scheduler'
    assert not native.exists(), 'Preserve scheduler build'
    shutil.copytree(WORK/'native',native)
    p=native/'Cargo.toml'
    p.write_text(p.read_text().replace('ours-gist-joint-optimization','ours-gist-joint-scheduler').replace('ours_gist_joint_optimization','ours_gist_joint_scheduler'))
    p=native/'auto_memory.rs'
    src=p.read_text()
    assert 'inflight>64' in src
    p.write_text(src.replace('inflight>64','inflight>256').replace('inflight must be 1..64','inflight must be 1..256'))
    # Service slots already support up to 4096 requests. Only its explicit cap changes;
    # the worker scoring window and its charged scratch remain unchanged at 64.
    with (WORK/'scheduler_build.log').open('w') as log:
        subprocess.run(['cargo','build','--release','--offline','--manifest-path',str(native/'Cargo.toml'),'--target-dir',str(ROOT/'src/graph_core/target')],stdout=log,stderr=subprocess.STDOUT,check=True)
    binary=WORK/'ours_gist_joint_scheduler'
    shutil.copy2(ROOT/'src/graph_core/target/release'/binary.name,binary)
    (WORK/'scheduler_build.json').write_text(json.dumps(dict(binary_sha256=sha(binary),parent_build_sha=sha(WORK/'build.json'),sources={str(p):sha(p) for p in native.iterdir() if p.is_file()}),indent=2)+'\n')


if __name__=='__main__':main()
