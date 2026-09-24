"""Isolated latest paging + existing static/dynamic cache; no historical edits."""
import importlib.util,json,hashlib,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
WORK=ROOT/'work/ours_memory_budget/gist_budget_sweep';NATIVE=WORK/'native'
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def replace(s,a,b):
 assert s.count(a)==1,(a[:90],s.count(a));return s.replace(a,b,1)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def snapshot_dependencies():
 # Preserve locally modified C++/Rust dependencies used by Cargo as well as generated entry sources.
 roots=['src/graph_core/src','src/graph_core/native','src/disk_bench/native','Ours/core/hnswlib']
 paths=[ROOT/'src/graph_core/build.rs',ROOT/'src/graph_core/Cargo.toml',ROOT/'src/graph_core/Cargo.lock',ROOT/'experiments/02_disk_shared_graph/native/routing_io.cpp',ROOT/'experiments/02_disk_shared_graph/native/direct_io_bridge.cpp']
 for root in roots:paths.extend(p for p in (ROOT/root).rglob('*') if p.is_file())
 manifest={}
 for source in paths:
  target=WORK/'source_dependencies'/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True)
  if target.exists():assert sha(target)==sha(source),'dependency changed; archive old snapshot before rebuilding'
  else:shutil.copy2(source,target)
  manifest[str(source.relative_to(ROOT))]=sha(source)
 (WORK/'source_dependencies.json').write_text(json.dumps(manifest,indent=2)+'\n')
 return manifest

def prepare():
 m=load('base_prepare',HERE.parent/'prepare.py');m.OUT=WORK/'base';m.prepare()
 d=load('dynamic_prepare',HERE.parent/'dynamic_records/prepare.py');d.SOURCE=m.OUT;d.DEST=NATIVE;d.prepare()
 shutil.copy2(HERE/'auto_memory.rs',NATIVE/'auto_memory.rs')
 p=NATIVE/'native_main.rs';s=p.read_text();s='mod auto_memory;\n'+s
 a=s.index('    let paging_scratch=',s.index('fn preflight_ours_routing'));b=s.index('\nconst PORT_KIND',a)
 s=s[:a]+'    crate::auto_memory::routing(args,codes,factors,centroid_bytes,record_stride,workers,mapping)\n}\n'+s[b:]
 s=replace(s,'    let policy=args.optional("--memory-policy").unwrap_or("baseline");','    let policy=if crate::auto_memory::get().optional>0 {"hot_dynamic"}else{"baseline"};')
 s=replace(s,'    let optional_bytes=args.optional("--memory-cache-bytes").unwrap_or("0").parse::<usize>().map_err(err)?;','    let optional_bytes=crate::auto_memory::get().optional;')
 a=s.index('    // This is an admission reservation',s.index('fn run_search_with_ours'));b=s.index('    let cache_mode =',a)
 s=s[:a]+'''    let worker_scratch_bytes=workers*(codec.layout.record_count+4*1024*1024+2*1024*1024);
    let budget_bytes=crate::auto_memory::get().budget;
    let fixed_bytes=budget_bytes-optional_bytes;
    crate::auto_memory::stage("after_cache_setup");
'''+s[b:]
 s=replace(s,'            let codec = Arc::new(codec);','            crate::auto_memory::stage("after_codec_and_mapping");\n            let codec = Arc::new(codec);')
 a=s.index('fn execute_config_ours(');b=s.index('\nfn ',a+4);part=s[a:b]
 part=replace(part,'        measured_start = Some(Instant::now());','        crate::auto_memory::stage("after_warmup");\n        measured_start = Some(Instant::now());')
 s=s[:a]+part+s[b:]
 s=replace(s,'    let args = Args::parse()?;','    crate::auto_memory::stage("startup");\n    let args = Args::parse()?;')
 p.write_text(s)
 p=NATIVE/'Cargo.toml';s=p.read_text().replace('ours-memory-budget-experiment-v2','ours-gist-budget-sweep').replace('ours_memory_budget_v2','ours_gist_budget_sweep');p.write_text(s)
 return NATIVE
if __name__=='__main__':
 prepare();dependencies=snapshot_dependencies();subprocess.run(['cargo','build','--release','--offline','--manifest-path',str(NATIVE/'Cargo.toml'),'--target-dir',str(ROOT/'src/graph_core/target')],check=True)
 binary=WORK/'ours_gist_budget_sweep';shutil.copy2(ROOT/'src/graph_core/target/release/ours_gist_budget_sweep',binary)
 manifest=dict(dependency_sources=dependencies,binary_sha256=sha(binary),generated_sources={str(p):sha(p) for p in NATIVE.iterdir() if p.is_file()},inputs={str(p):sha(p) for p in HERE.iterdir() if p.is_file()})
 (WORK/'build.json').write_text(json.dumps(manifest,indent=2)+'\n');print(binary)
