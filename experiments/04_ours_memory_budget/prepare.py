"""Snapshot the native experiment and add opt-in locality-cache hooks only."""
from pathlib import Path
import hashlib,json,shutil
ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
OUT=ROOT/'work/ours_memory_budget/v2/native'
SRC=ROOT/'experiments/02_disk_shared_graph/native/src'

def replace_once(text,old,new):
 if text.count(old)!=1:raise ValueError(f'expected exactly one source anchor: {old[:90]!r}')
 return text.replace(old,new,1)

def prepare():
 OUT.mkdir(parents=True,exist_ok=True)
 sources=[SRC/n for n in ['lib.rs','ours_port.rs','locality.rs','baseline_disk.rs','routing.rs','main.rs']]
 sources += [ROOT/n for n in ['src/graph_core/src/ours_diskann.rs','src/graph_core/src/config.rs','src/graph_core/native/rabitq_bridge.cpp','src/disk_bench/native/query_page_cache.hpp','src/disk_bench/native/direct_io.cpp','experiments/02_disk_shared_graph/native/direct_io_bridge.cpp','experiments/02_disk_shared_graph/native/routing_io.cpp','Ours/core/hnswlib/space_rabitq.h','Ours/core/hnswlib/hnswlib.h']]
 for name in ['lib.rs','ours_port.rs','locality.rs','baseline_disk.rs','routing.rs']:shutil.copy2(SRC/name,OUT/name)
 shutil.copy2(ROOT/'src/graph_core/src/ours_diskann.rs',OUT/'ours_diskann.rs')
 p=OUT/'lib.rs';s=p.read_text()
 s=replace_once(s,'Some(Box::new(unsafe { bytes.assume_init() }))\n        } else {\n            None','Some(Box::new(unsafe { bytes.assume_init() }))\n        } else {\n            crate::memory_experiment::page_get(file, page)')
 s=replace_once(s,'        unsafe { qgraph05_query_cache_put','        crate::memory_experiment::page_put(file, page, bytes);\n        unsafe { qgraph05_query_cache_put');p.write_text(s)
 p=OUT/'locality.rs';s=p.read_text()
 s=replace_once(s,'        let (rows, stats) = self.records(&[id], false)?;', '''        crate::memory_experiment::record_access(0, &[id]);
        if let Some(v)=crate::memory_experiment::neighbors(id) {return Ok((v,IoStats::default()));}
        let (rows, stats) = self.records(&[id], false)?;
        crate::memory_experiment::record_io(0, &stats);''')
 for method,residual,extract in [('compact',False,'row[self.layout.graph_bytes..].to_vec()'),('residual',True,'row')]:
  begin=s.index(f'    pub fn {method}(');end=s.index('\n    }',begin)+6
  s=s[:begin]+f'''    pub fn {method}(&mut self, ids: &[u32]) -> ANNResult<(Vec<u8>, IoStats)> {{
        crate::memory_experiment::record_access({2 if residual else 1}, ids);
        if !crate::memory_experiment::has_records() {{
            let (rows,stats)=self.records(ids,{str(residual).lower()})?;
            crate::memory_experiment::record_io({2 if residual else 1}, &stats);
            return Ok((rows.into_iter().flat_map(|row| {extract}).collect(),stats));
        }}
        let missing=ids.iter().copied().filter(|id|!crate::memory_experiment::contains_record(*id)).collect::<Vec<_>>();
        let (rows,stats)=if missing.is_empty(){{(Vec::new(),IoStats::default())}}else{{self.records(&missing,{str(residual).lower()})?}};
        crate::memory_experiment::record_io({2 if residual else 1}, &stats);
        let mut rows=rows.into_iter();let mut output=Vec::new();
        for &id in ids {{
            if let Some((compact,residual))=crate::memory_experiment::record(id) {{output.extend_from_slice({'residual' if residual else 'compact'});}}
            else {{let row=rows.next().expect("one row per cache miss");output.extend({extract});}}
        }}
        Ok((output,stats))
    }}'''+s[end:]
 p.write_text(s)
 p=OUT/'ours_port.rs';s=p.read_text().replace('    fn paper_estimates(', '    pub(crate) fn paper_estimates(')
 s=replace_once(s,'    visited[0] = true;','    let entry=crate::memory_experiment::entry(&prepared)?;\n    visited[entry as usize] = true;')
 s=replace_once(s,'        &[0],\n        ablation.payload_coalescing(),','        &[entry],\n        ablation.payload_coalescing(),')
 s=replace_once(s,'            id: 0,\n            expanded: false,','            id: entry,\n            expanded: false,');p.write_text(s)
 s=(SRC/'main.rs').read_text().replace('diskann_fair::disk_port','crate::disk_port').replace('diskann_fair::ours_diskann','crate::ours_diskann')
 s='pub use diskann_fair::config;\nmod ours_diskann;\n#[path="lib.rs"] mod disk_port;\nmod memory_experiment;\n'+s
 anchor='''    let workers: usize = args.number("--workers")?;
    let warmup_queries: usize = args.number("--warmup-queries")?;
    // This is an admission reservation'''
 s=replace_once(s,anchor,'''    let policy=args.optional("--memory-policy").unwrap_or("baseline");
    let optional_bytes=args.optional("--memory-cache-bytes").unwrap_or("0").parse::<usize>().map_err(err)?;
    let profile=Path::new(args.optional("--memory-profile-dir").ok_or("missing memory profile dir")?);
    let stats=Path::new(args.optional("--memory-stats-dir").ok_or("missing memory stats dir")?);
    let layout=codec.locality.as_ref().ok_or("memory experiment requires original locality layout")?;
    crate::memory_experiment::setup(policy,optional_bytes,profile,stats,layout);
    let workers: usize = args.number("--workers")?;
    let warmup_queries: usize = args.number("--warmup-queries")?;
    // This is an admission reservation''')
 s=replace_once(s,'                let direct = execute_config_ours(', '                crate::memory_experiment::begin_config();\n                let direct = execute_config_ours(')
 # Only the Ours timing barrier is instrumented; the original scheduler is retained.
 begin=s.index('fn execute_config_ours(');end=s.index('\nfn ',begin+4)
 fragment=s[begin:end]
 fragment=replace_once(fragment,'        measured_start = Some(Instant::now());','        crate::memory_experiment::measurement_begin();\n        measured_start = Some(Instant::now());')
 s=s[:begin]+fragment+s[end:]
 begin=s.index('fn run_search_with_ours(');end=s.index('\nfn ',begin+4);fragment=s[begin:end]
 fragment=replace_once(fragment,'                measured_peak_rss_bytes = measured_peak_rss_bytes.max(direct.peak_rss_bytes);','                crate::memory_experiment::finish_config(width);\n                measured_peak_rss_bytes = measured_peak_rss_bytes.max(direct.peak_rss_bytes);')
 s=s[:begin]+fragment+s[end:]
 s=replace_once(s,'fn main() {\n    if let Err(error) = run() {','fn main() {\n    let result=run();\n    if result.is_ok(){crate::memory_experiment::finish();}\n    if let Err(error) = result {')
 (OUT/'native_main.rs').write_text(s)
 for name in ['main.rs','memory_experiment.rs']:shutil.copy2(HERE/name,OUT/name)
 deps=(ROOT/'src/graph_core/Cargo.toml').read_text().split('[dependencies]',1)[1].replace('path = "../../',f'path = "{ROOT}/')
 (OUT/'Cargo.toml').write_text(f'''[package]
name="ours-memory-budget-experiment-v2"
version="0.1.0"
edition="2021"
[[bin]]
name="ours_memory_budget_v2"
path="main.rs"
[dependencies]
serde_json="1"
diskann-fair={{path="{ROOT}/src/graph_core"}}
'''+deps)
 sources+=list(HERE.glob('*.rs'))+[HERE/'prepare.py']
 snapshot={'original_sources':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in sources},'generated_sources':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in OUT.glob('*.rs')}}
 (OUT/'snapshot.json').write_text(json.dumps(snapshot,indent=2));return OUT
if __name__=='__main__':print(prepare())
