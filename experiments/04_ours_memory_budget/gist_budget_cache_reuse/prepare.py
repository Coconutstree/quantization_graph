"""Build a cache-reuse variant from the frozen sweep, without changing its inputs."""
import hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
OLD=ROOT/'work/ours_memory_budget/gist_budget_sweep'
WORK=ROOT/'work/ours_memory_budget/gist_budget_cache_reuse'
NATIVE=WORK/'native'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def replace(p,a,b):
 s=p.read_text();assert s.count(a)==1,(p,a[:70],s.count(a));p.write_text(s.replace(a,b,1))
def prepare():
 assert not WORK.exists(), 'Existing version must be preserved'
 old=json.loads((OLD/'build.json').read_text())
 for p,h in old['generated_sources'].items():assert sha(Path(p))==h
 shutil.copytree(OLD/'native',NATIVE)
 deps=WORK/'dependencies';shutil.copytree(OLD/'source_dependencies',deps)
 shutil.copytree(ROOT/'experiments/02_disk_shared_graph/native/src',deps/'experiments/02_disk_shared_graph/native/src')
 core=deps/'src/graph_core'
 p=core/'Cargo.toml';p.write_text(p.read_text().replace('../../baselines/',str(ROOT/'baselines')+'/'))
 p=NATIVE/'Cargo.toml';p.write_text(p.read_text().replace(str(ROOT/'src/graph_core'),str(core)).replace('ours-gist-budget-sweep','ours-gist-budget-cache-reuse').replace('ours_gist_budget_sweep','ours_gist_budget_cache_reuse'))
 header=deps/'src/disk_bench/native/query_page_cache.hpp'
 replace(header,'    std::size_t allocated_bytes() const {','''    // Called only between queries, under the owning worker's cache mutex.
    // Keep page storage and hash capacity; stale bytes are unreachable after reset.
    void clear() noexcept {
        std::fill(table_.begin(), table_.end(), none);
        for (auto& slot : slots_) { slot.prev = none; slot.next = none; }
        used_ = 0; head_ = tail_ = none;
        hits = misses = evictions = 0;
    }
    std::size_t allocated_bytes() const {''')
 bridge=deps/'experiments/02_disk_shared_graph/native/direct_io_bridge.cpp'
 replace(bridge,'void qgraph05_query_cache_delete(void* cache) noexcept {','''void qgraph05_query_cache_clear(void* cache) noexcept {
    static_cast<qgraph05::QueryPageCache*>(cache)->clear();
}
void qgraph05_query_cache_delete(void* cache) noexcept {''')
 lib=NATIVE/'lib.rs'
 replace(lib,'    fn qgraph05_query_cache_new() -> *mut c_void;','    fn qgraph05_query_cache_new() -> *mut c_void;\n    fn qgraph05_query_cache_clear(cache: *mut c_void);')
 replace(lib,'    pub fn clear_query_cache(&mut self) {\n        // Normally the reader is created per query. Retain the public reset API.\n        self.query_cache = Arc::new(Mutex::new(QueryPageCache::new().expect("query cache allocation")));\n    }','''    pub fn clear_query_cache(&mut self) -> ANNResult<()> {
        let cache = self.query_cache.lock().map_err(|_| ann_error("query cache lock poisoned"))?;
        unsafe { qgraph05_query_cache_clear(cache.0) };
        Ok(())
    }''')
 replace(lib,'                    .clear_query_cache();','                    .clear_query_cache()?;')
 replace(lib,'    fn start_point(&self) -> u32 {','''    pub fn shared_query_cache(&self) -> ANNResult<Arc<Mutex<QueryPageCache>>> {
        match self {
            Self::Direct { reader, .. } => Ok(reader.lock()
                .map_err(|_| ann_error("direct graph reader lock poisoned"))?.query_cache.clone()),
            _ => Err(ann_error("shared query cache requires direct backend")),
        }
    }

    fn start_point(&self) -> u32 {''')
 loc=NATIVE/'locality.rs'
 replace(loc,'    fn records(&mut self, ids: &[u32], residual: bool)', '''    pub fn with_cache(layout: Arc<LocalityLayout>, cache: Arc<Mutex<QueryPageCache>>) -> ANNResult<Self> {
        Ok(Self { combined: DirectAioHandle::new(&layout.dir.join("graph_compact.pages"))?,
            residual: DirectAioHandle::new(&layout.dir.join("residual.pages"))?, cache, layout })
    }
    fn records(&mut self, ids: &[u32], residual: bool)''')
 replace(NATIVE/'native_main.rs','LocalityReader::new(layout.clone()).map_err(err)?','LocalityReader::with_cache(layout.clone(), graph_factory.shared_query_cache().map_err(err)?).map_err(err)?')
 build()
def build():
 deps=WORK/'dependencies';old=json.loads((OLD/'build.json').read_text())
 # Pin both generated sources and C++ dependencies used by this build.
 subprocess.run(['cargo','build','--release','--offline','--manifest-path',str(NATIVE/'Cargo.toml'),'--target-dir',str(ROOT/'src/graph_core/target')],check=True)
 binary=WORK/'ours_gist_budget_cache_reuse';shutil.copy2(ROOT/'src/graph_core/target/release'/binary.name,binary)
 manifest=dict(parent_binary_sha256=old['binary_sha256'],binary_sha256=sha(binary),generated_sources={str(p):sha(p) for p in NATIVE.iterdir() if p.is_file()},dependency_sources={str(p.relative_to(deps)):sha(p) for p in deps.rglob('*') if p.is_file()})
 (WORK/'build.json').write_text(json.dumps(manifest,indent=2)+'\n')
if __name__=='__main__':
 import sys
 build() if '--build-only' in sys.argv else prepare()
