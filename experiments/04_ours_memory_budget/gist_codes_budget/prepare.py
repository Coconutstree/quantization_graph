import hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
OLD=ROOT/'work/ours_memory_budget/gist_data_budget';WORK=ROOT/'work/ours_memory_budget/gist_codes_budget';NATIVE=WORK/'native'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def replace(p,a,b):
 s=p.read_text();assert s.count(a)==1,(p,a[:60],s.count(a));p.write_text(s.replace(a,b,1))
def main():
 assert not WORK.exists(),'Preserve previous version'
 manifest=json.loads((OLD/'build.json').read_text())
 for p,h in manifest['generated_sources'].items():assert sha(Path(p))==h
 shutil.copytree(OLD/'native',NATIVE)
 p=NATIVE/'Cargo.toml';p.write_text(p.read_text().replace('ours-gist-data-budget','ours-gist-codes-budget').replace('ours_gist_data_budget','ours_gist_codes_budget'))
 p=NATIVE/'auto_memory.rs';p.write_text(p.read_text()+'\n'+(HERE/'codes_plan.rs').read_text())
 replace(p,'args.optional("--data-cache-budget-bytes")','args.optional("--codes-cache-budget-bytes")')
 replace(p,'let plan=choose_data(data,budget,fixed,','let plan=choose_codes(data,budget,fixed,')
 replace(p,'"data_cache_budget_bytes":data,"routing_resident_threshold_bytes":codes+factors,"codes_only_bytes":codes,"codes_alone_fit":data as u64>=codes,"full_routing_fits":data as u64>=codes+factors,','"codes_cache_budget_bytes":data,"codes_resident_threshold_bytes":codes,"factors_outside_codes_budget_bytes":factors,"factors_resident":true,"codes_alone_fit":data as u64>=codes,"codes_cache_data_bytes":pages*4096,"routing_page_metadata_outside_budget_bytes":pages*256,')
 subprocess.run(['cargo','build','--release','--offline','--manifest-path',str(NATIVE/'Cargo.toml'),'--target-dir',str(ROOT/'src/graph_core/target')],check=True)
 binary=WORK/'ours_gist_codes_budget';shutil.copy2(ROOT/'src/graph_core/target/release'/binary.name,binary)
 (WORK/'build.json').write_text(json.dumps(dict(binary_sha256=sha(binary),parent_binary_sha256=manifest['binary_sha256'],generated_sources={str(p):sha(p) for p in NATIVE.iterdir() if p.is_file()},dependency_manifest=str(OLD/'build.json')),indent=2)+'\n')
if __name__=='__main__':main()
