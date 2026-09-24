import json,shutil,subprocess
from pathlib import Path
from prepare import ROOT,WORK,HERE,sha
native=WORK/'validation_native'
assert not native.exists()
shutil.copytree(WORK/'native',native)
p=native/'lib.rs';p.write_text(p.read_text()+'\n'+(HERE/'sharing_tests.rs').read_text())
p=native/'Cargo.toml';p.write_text(p.read_text().replace('ours-gist-budget-cache-reuse','ours-gist-budget-cache-reuse-tests').replace('ours_gist_budget_cache_reuse','ours_gist_budget_cache_reuse_tests'))
out=ROOT/'results/04_ours_memory_budget/gist_budget_cache_reuse/validation'
with (out/'sharing_identity.log').open('w') as f:
 r=subprocess.run(['cargo','test','--release','--offline','--manifest-path',str(native/'Cargo.toml'),'--target-dir',str(ROOT/'src/graph_core/target'),'disk_port::cache_reuse_validation::initialization_shares_and_reset_preserves_allocation','--','--exact','--test-threads=1'],stdout=f,stderr=subprocess.STDOUT)
assert r.returncode==0
assert '1 passed' in (out/'sharing_identity.log').read_text()
(out/'sharing_identity.json').write_text(json.dumps(dict(passed=True,test_source_sha256=sha(HERE/'sharing_tests.rs'),production_sources_unchanged=True),indent=2)+'\n')
