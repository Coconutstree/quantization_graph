"""Build current native implementation and freeze its source inputs separately."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
WORK=ROOT/'work/ours_memory_budget/routing_paged'

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def build():
    WORK.mkdir(parents=True,exist_ok=True)
    subprocess.run(['cargo','build','--release','--offline','--manifest-path',str(ROOT/'src/graph_core/Cargo.toml'),'--bin','qgraph05_shared_graph_port'],check=True,cwd=ROOT)
    binary=WORK/'ours_routing_paged'
    shutil.copy2(ROOT/'src/graph_core/target/release/qgraph05_shared_graph_port',binary)
    sources=[]
    for folder,patterns in [('experiments/02_disk_shared_graph/native',['**/*.rs','*.cpp']),('src/graph_core',['src/*.rs','native/*.cpp','Cargo.*','build.rs']),('src/disk_bench/native',['*.cpp','*.hpp']),('Ours/core/hnswlib',['*.h']),('experiments/04_ours_memory_budget/routing_paged',['*.py','*.cpp'])]:
        for pattern in patterns:sources.extend((ROOT/folder).glob(pattern))
    hashes={}
    for source in sources:
        if not source.is_file():continue
        relative=source.relative_to(ROOT);target=WORK/'snapshot'/relative
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target);hashes[str(relative)]=sha(source)
    (WORK/'build.json').write_text(json.dumps(dict(binary=str(binary),binary_sha256=sha(binary),source_sha256=hashes),indent=2)+'\n')
    return binary
if __name__=='__main__':print(build())
