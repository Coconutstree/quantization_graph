//! Validation-frozen hot records, then the 04 bounded sharded FIFO.
use super::dynamic_records::DynamicRecords;
use serde_json::{json, Value};
use std::{fs::File, io::{BufReader, Read, Write}, path::Path,
    sync::{Mutex, atomic::{AtomicU64, Ordering}}};

pub const POLICY: &str = "validation_hot_then_dynamic_fifo16_v1";
const NONE: u32 = u32::MAX;
type R<T> = Result<T, String>;

pub struct RecordCache {
    map: Vec<u32>, records: Vec<u8>, cb: usize, rb: usize,
    pub allocated: usize, static_bytes: usize, dynamic: DynamicRecords,
    hits: [AtomicU64; 2], budget: usize,
}
impl RecordCache {
    pub fn load(n: usize, cb: usize, rb: usize, budget: usize, ranks: &Path,
                mut read: impl FnMut(u32) -> R<(Vec<u8>, Vec<u8>)>) -> R<Self> {
        let mut input = BufReader::new(File::open(ranks).map_err(|e|e.to_string())?);
        let len = input.get_ref().metadata().map_err(|e|e.to_string())?.len() as usize;
        if len % 4 != 0 || len / 4 > n || n >= NONE as usize || cb == 0 || rb == 0 {
            return Err("invalid hot-record ranks or record shape".into());
        }
        // Account the dense ID map, records, and container/allocator allowance.
        // Stream ranks: no O(N) ranking array remains alongside cache allocation.
        let overhead = n.checked_mul(4).and_then(|v|v.checked_add(4096)).ok_or("cache size overflow")?;
        let stride = cb.checked_add(rb).ok_or("record size overflow")?;
        let count = budget.saturating_sub(overhead) / stride;
        let count = count.min(len / 4);
        let mut map = if count > 0 {vec![NONE;n]} else {vec![]};
        let mut records = vec![0; count * stride];
        for slot in 0..count {
            let mut bytes = [0;4]; input.read_exact(&mut bytes).map_err(|e|e.to_string())?;
            let id = u32::from_le_bytes(bytes) as usize;
            if id >= n || map[id] != NONE {return Err("invalid/duplicate hot record ID".into());}
            let (c,r) = read(id as u32)?;
            if c.len()!=cb || r.len()!=rb {return Err("hot record shape mismatch".into());}
            records[slot*stride..slot*stride+cb].copy_from_slice(&c);
            records[slot*stride+cb..(slot+1)*stride].copy_from_slice(&r);
            map[id] = slot as u32;
        }
        let static_bytes = if count > 0 {4096 + map.capacity()*4 + records.capacity()} else {0};
        if static_bytes > budget {return Err("hot records exceed available RAM".into());}
        let dynamic = DynamicRecords::new(n, cb, rb, budget-static_bytes, &map);
        let allocated = static_bytes + dynamic.allocated;
        if allocated > budget {return Err("record caches exceed available RAM".into());}
        Ok(Self{map, records, cb, rb, allocated, static_bytes, dynamic, budget,
            hits:std::array::from_fn(|_|AtomicU64::new(0))})
    }
    pub fn get(&self, id:u32, kind:usize, out:&mut[u8])->bool {
        if let Some(&slot) = self.map.get(id as usize).filter(|&&s|s!=NONE) {
            let start = slot as usize*(self.cb+self.rb)+if kind==0 {0}else{self.cb};
            out.copy_from_slice(&self.records[start..start+out.len()]);
            self.hits[kind].fetch_add(1,Ordering::Relaxed); true
        } else {self.dynamic.get(id,kind,out)}
    }
    pub fn put(&self,id:u32,kind:usize,bytes:&[u8]) {
        if self.map.get(id as usize).copied().unwrap_or(NONE)==NONE {self.dynamic.put(id,kind,bytes)}
    }
    pub fn clear(&self) {self.dynamic.clear();self.reset();}
    pub fn reset(&self) {self.dynamic.reset();for h in &self.hits{h.store(0,Ordering::Relaxed)}}
    pub fn stats(&self)->Value {json!({"policy":POLICY,"budget_bytes":self.budget,
        "reserved_bytes":self.allocated,"static_reserved_bytes":self.static_bytes,
        "static_nodes":self.records.len()/(self.cb+self.rb),"static_payload_bytes":self.records.len(),
        "static_hits":[self.hits[0].load(Ordering::Relaxed),self.hits[1].load(Ordering::Relaxed)],
        "dynamic":self.dynamic.stats()})}
}

/// Profiling is an offline validation pass, never used for measured QPS.
pub struct RecordProfile {counts:Vec<AtomicU64>, scores:Mutex<Vec<f64>>}
impl RecordProfile {
    pub fn new(n:usize)->Self {Self{counts:(0..n).map(|_|AtomicU64::new(0)).collect(),scores:Mutex::new(vec![0.;n])}}
    pub fn record(&self,ids:&[u32]) {for &id in ids{self.counts[id as usize].fetch_add(1,Ordering::Relaxed);}}
    pub fn reset(&self) {for c in &self.counts{c.store(0,Ordering::Relaxed)}}
    pub fn finish_config(&self) {
        let total:u64=self.counts.iter().map(|c|c.load(Ordering::Relaxed)).sum();
        if total>0 {for (s,c) in self.scores.lock().unwrap().iter_mut().zip(&self.counts){
            *s += c.load(Ordering::Relaxed) as f64/total as f64;
        }}
    }
    pub fn save(&self,path:&Path)->R<()> {
        let scores=self.scores.lock().unwrap();
        let mut ids=(0..scores.len()).filter(|&i|scores[i]>0.).collect::<Vec<_>>();
        ids.sort_unstable_by(|&a,&b|scores[b].total_cmp(&scores[a]).then_with(||a.cmp(&b)));
        let mut out=File::create(path).map_err(|e|e.to_string())?;
        for id in ids{out.write_all(&(id as u32).to_le_bytes()).map_err(|e|e.to_string())?;}
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn static_survives_reset_dynamic_is_cleared_and_budget_is_bounded() {
        let path=std::env::temp_dir().join(format!("formal-hot-cache-{}.u32",std::process::id()));
        std::fs::write(&path,1u32.to_le_bytes()).unwrap();
        for budget in [0,100,4357,20000] {
            let c=RecordCache::load(64,2,3,budget,&path,|id|Ok((vec![id as u8;2],vec![9;3]))).unwrap();
            assert!(c.allocated<=budget);
            if budget<4096+64*4+5 {assert_eq!(c.stats()["static_nodes"],0);continue;}
            assert!(c.get(1,0,&mut[0;2]));
            c.put(2,0,&[4,5]);
            if c.stats()["dynamic"]["capacity_nodes"].as_u64().unwrap()>0 {
                assert!(c.get(2,0,&mut[0;2]));assert!(!c.get(2,1,&mut[0;3]));
                c.reset();assert!(c.get(2,0,&mut[0;2]));
            }
            c.clear();assert!(!c.get(2,0,&mut[0;2]));assert!(c.get(1,1,&mut[0;3]));
        }
        std::fs::remove_file(path).unwrap();
    }
    #[test]
    fn validation_profile_normalizes_configs_and_breaks_ties_by_id() {
        let p=RecordProfile::new(4);
        p.record(&[3,3,3]);p.reset(); // warmup is excluded
        p.record(&[0,0,1,1]);p.finish_config();p.reset();
        p.record(&[2]);p.finish_config();
        let path=std::env::temp_dir().join(format!("formal-hot-profile-{}.u32",std::process::id()));
        p.save(&path).unwrap();
        let ranks=std::fs::read(&path).unwrap().chunks_exact(4)
            .map(|b|u32::from_le_bytes(b.try_into().unwrap())).collect::<Vec<_>>();
        assert_eq!(ranks,vec![2,0,1]);std::fs::remove_file(path).unwrap();
    }
}
