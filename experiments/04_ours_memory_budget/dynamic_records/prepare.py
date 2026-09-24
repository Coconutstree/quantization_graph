from pathlib import Path
import json,hashlib,shutil
ROOT=Path(__file__).resolve().parents[3]
SOURCE=ROOT/'work/ours_memory_budget/hybrid_tuning/native'
DEST=ROOT/'work/ours_memory_budget/dynamic_records_native'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def prepare():
 old=json.loads((ROOT/'results/04_ours_memory_budget/hybrid_tuning/build.json').read_text())
 for p,h in old['generated_sources'].items():assert sha(Path(p))==h
 DEST.mkdir(parents=True,exist_ok=True)
 for src in SOURCE.iterdir():
  if src.is_file():shutil.copyfile(src,DEST/src.name)
 p=DEST/'Cargo.toml';p.write_text((SOURCE/'Cargo.toml').read_text().replace('ours-hybrid-ratio-experiment','ours-dynamic-records-experiment').replace('ours_hybrid_tuning','ours_dynamic_records'))
 (DEST/'dynamic_records.rs').write_text(Path(__file__).with_name('dynamic_records.rs').read_text())
 p=DEST/'native_main.rs';p.write_text((SOURCE/p.name).read_text().replace('mod memory_experiment;','mod memory_experiment;\nmod dynamic_records;'))
 p=DEST/'memory_experiment.rs';s=(SOURCE/p.name).read_text()
 s=s.replace('    pages: Vec<Mutex<Pages>>,','    dynamic: Option<crate::dynamic_records::DynamicRecords>,\n    pages: Vec<Mutex<Pages>>,')
 s=s.replace('        pages: vec![],','        dynamic: None,\n        pages: vec![],').replace('            pages: (0..16)','            dynamic: None,\n            pages: (0..16)')
 s=s.replace('"hot_payload" => available,','"hot_payload" | "hot_dynamic" => available,')
 s=s.replace('    let page_budget = page_allowance', '''    if mode == "hot_dynamic" {
        let d = crate::dynamic_records::DynamicRecords::new(n,s.cb,s.rb,available-s.allocated,&s.record_map);
        s.allocated += d.allocated; s.dynamic=Some(d);
    }
    let page_budget = page_allowance''')
 s=s.replace('        for p in &s.pages {','        if let Some(d)=&s.dynamic {d.clear()}\n        for p in &s.pages {')
 s=s.replace('fn reset_counters() {','fn reset_counters() {\n    if let Some(d)=STATE.get().and_then(|s|s.dynamic.as_ref()){d.reset()}')
 s=s.replace('"operations":operations});','"dynamic_records":s.dynamic.as_ref().map(|d|d.stats()),\n            "operations":operations});')
 s+='''
pub fn dynamic_enabled()->bool {STATE.get().is_some_and(|s|s.dynamic.is_some())}
pub fn cached_part(id:u32,kind:usize,out:&mut[u8])->bool {
    if let Some((c,r))=record(id){out.copy_from_slice(if kind==0{c}else{r});return true}
    STATE.get().and_then(|s|s.dynamic.as_ref()).is_some_and(|d|d.get(id,kind,out))
}
pub fn admit_part(id:u32,kind:usize,bytes:&[u8]) {
    if contains_record(id){return}
    if let Some(d)=STATE.get().and_then(|s|s.dynamic.as_ref()){d.put(id,kind,bytes)}
}
'''
 p.write_text(s)
 p=DEST/'locality.rs';s=(SOURCE/p.name).read_text()
 for method,kind in [('compact',0),('residual',1)]:
  anchor=f'    pub fn {method}(&mut self, ids: &[u32]) -> ANNResult<(Vec<u8>, IoStats)> {{'
  assert s.count(anchor)==1;s=s.replace(anchor,anchor+f'\n        if crate::memory_experiment::dynamic_enabled() {{return self.dynamic_parts(ids,{kind});}}')
 anchor='    pub fn compact(&mut self, ids:'
 helper='''    fn dynamic_parts(&mut self, ids:&[u32], kind:usize)->ANNResult<(Vec<u8>,IoStats)> {
        if ids.iter().any(|&id|id as usize>=self.layout.slots.len()){return Err(ann_error("locality logical ID out of range"))}
        crate::memory_experiment::record_access(kind+1,ids);
        let size=if kind==0{self.layout.compact_bytes}else{self.layout.residual_bytes};
        let mut output=vec![0;ids.len()*size];let mut missing=Vec::new();let mut positions=Vec::new();
        // Copy each hit under its shard lock once. Never re-check after I/O: another worker may evict.
        for (pos,&id) in ids.iter().enumerate(){
            if !crate::memory_experiment::cached_part(id,kind,&mut output[pos*size..(pos+1)*size]){missing.push(id);positions.push(pos)}
        }
        let (rows,stats)=if missing.is_empty(){(Vec::new(),IoStats::default())}else{self.records(&missing,kind==1)?};
        assert_eq!(rows.len(),missing.len());
        crate::memory_experiment::record_io(kind+1,&stats);
        for ((&id,&pos),row) in missing.iter().zip(&positions).zip(rows.iter()){
            let part=if kind==0{&row[self.layout.graph_bytes..]}else{&row[..]};
            output[pos*size..(pos+1)*size].copy_from_slice(part);
            crate::memory_experiment::admit_part(id,kind,part);
        }
        Ok((output,stats))
    }
'''
 assert s.count(anchor)==1;s=s.replace(anchor,helper+anchor);p.write_text(s)
 # Add a real O_DIRECT layout test with static hits, dynamic misses, duplicate IDs and partial records.
 original=(SOURCE/'locality.rs').read_text()
 start=original.index('    #[test]')
 end=original.index('        let mut reader = LocalityReader::new(layout).unwrap();',start)
 fixture=original[start:end].replace('fn shared_page_reuse_and_residual_namespace()', 'fn dynamic_records_mixed_hits_and_lazy_residual()')
 fixture+='        fs::write(dir.join("payload_scores.f64"),[0f64,1f64].iter().flat_map(|v|v.to_le_bytes()).collect::<Vec<_>>()).unwrap();\n        crate::memory_experiment::setup("hot_dynamic",32768,&dir,&dir,&layout);\n        crate::memory_experiment::begin_config();\n        let mut first=LocalityReader::new(layout.clone()).unwrap();\n        let (codes,stats)=first.compact(&[0,1,0]).unwrap();\n        assert_eq!(codes,[10,10,10,10,11,11,11,11,10,10,10,10]);assert_eq!(stats.requests,1);\n        crate::memory_experiment::measurement_begin();\n        let mut second=LocalityReader::new(layout.clone()).unwrap();\n        assert_eq!(second.compact(&[0,1]).unwrap().1.requests,0);\n        let (rest,stats)=second.residual(&[1,0,0]).unwrap();\n        assert_eq!(rest,[21,21,21,21,20,20,20,20,20,20,20,20]);assert_eq!(stats.requests,1);\n        let mut third=LocalityReader::new(layout.clone()).unwrap();\n        assert_eq!(third.residual(&[0,1]).unwrap().1.requests,0);\n        assert!(third.compact(&[2]).is_err());\n        crate::memory_experiment::begin_config();\n        let mut fourth=LocalityReader::new(layout).unwrap();\n        assert_eq!(fourth.compact(&[1]).unwrap().1.requests,0);\n        assert_eq!(fourth.compact(&[0]).unwrap().1.requests,1);\n        fs::remove_dir_all(dir).unwrap();\n    }\n'
 text=p.read_text();p.write_text(text[:text.rfind('}')]+fixture+'}\n')
if __name__=='__main__':prepare()
