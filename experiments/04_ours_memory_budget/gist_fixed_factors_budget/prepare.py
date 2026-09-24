"""Immutable native snapshot; only this experiment's admission policy changes."""
import hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
OLD=ROOT/'work/ours_memory_budget/gist_codes_budget';WORK=ROOT/'work/ours_memory_budget/gist_fixed_factors_budget';NATIVE=WORK/'native'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 assert not WORK.exists(),'Preserve existing build'
 old=json.loads((OLD/'build.json').read_text())
 for p,h in old['generated_sources'].items():assert sha(Path(p))==h,p
 shutil.copytree(OLD/'native',NATIVE)
 p=NATIVE/'Cargo.toml';p.write_text(p.read_text().replace('ours-gist-codes-budget','ours-gist-fixed-factors-budget').replace('ours_gist_codes_budget','ours_gist_fixed_factors_budget'))
 shutil.copy2(HERE/'auto_memory.rs',NATIVE/'auto_memory.rs')
 p=NATIVE/'native_main.rs';s=p.read_text();s=s.replace('                crate::memory_experiment::finish_config(width);','                crate::auto_memory::stage("after_measurement_and_workers");\n                crate::memory_experiment::finish_config(width);');p.write_text(s)
 subprocess.run(['cargo','build','--release','--offline','--manifest-path',str(NATIVE/'Cargo.toml'),'--target-dir',str(ROOT/'src/graph_core/target')],check=True)
 binary=WORK/'ours_gist_fixed_factors_budget';shutil.copy2(ROOT/'src/graph_core/target/release'/binary.name,binary)
 (WORK/'build.json').write_text(json.dumps(dict(binary_sha256=sha(binary),parent_binary_sha256=old['binary_sha256'],generated_sources={str(p):sha(p) for p in NATIVE.iterdir() if p.is_file()},dependency_manifest=str(OLD/'build.json')),indent=2)+'\n')
if __name__=='__main__':main()
