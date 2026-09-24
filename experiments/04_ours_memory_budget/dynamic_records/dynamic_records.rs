//! Fixed-capacity, 16-shard FIFO. Bytes and both validity bits share node lifetime.
use std::sync::{Mutex,atomic::{AtomicU64,Ordering}};
const SHARDS:usize=16;
const NONE:u32=u32::MAX;
struct Shard {
    map:Vec<u32>, ids:Vec<u32>, valid:Vec<u8>, data:Vec<u8>,
    cb:usize, rb:usize, next:usize, len:usize,
}
impl Shard {
    fn new(n:usize,cap:usize,cb:usize,rb:usize)->Self {
        Self{map:vec![NONE;n],ids:vec![NONE;cap],valid:vec![0;cap],data:vec![0;cap*(cb+rb)],cb,rb,next:0,len:0}
    }
    fn range(&self,slot:usize,kind:usize)->std::ops::Range<usize> {
        assert!(kind<2);let start=slot*(self.cb+self.rb)+if kind==0 {0}else{self.cb};
        start..start+if kind==0{self.cb}else{self.rb}
    }
    fn get(&self,id:usize,kind:usize,out:&mut[u8])->bool {
        let slot=self.map[id];if slot==NONE{return false}
        let slot=slot as usize;if self.valid[slot]&(1<<kind)==0{return false}
        out.copy_from_slice(&self.data[self.range(slot,kind)]);true
    }
    fn put(&mut self,id:usize,kind:usize,bytes:&[u8])->(bool,bool) {
        if self.ids.is_empty(){return(false,false)}
        assert_eq!(bytes.len(),if kind==0{self.cb}else{self.rb});
        let mut slot=self.map[id];let inserted=slot==NONE;let mut evicted=false;
        if inserted {
            slot=self.next as u32;self.next=(self.next+1)%self.ids.len();
            let old=self.ids[slot as usize];
            if old!=NONE{self.map[old as usize]=NONE;evicted=true}else{self.len+=1}
            self.ids[slot as usize]=id as u32;self.map[id]=slot;self.valid[slot as usize]=0;
        }
        let range=self.range(slot as usize,kind);self.data[range].copy_from_slice(bytes);
        self.valid[slot as usize]|=1<<kind;(inserted,evicted)
    }
    fn clear(&mut self){self.map.fill(NONE);self.ids.fill(NONE);self.valid.fill(0);self.len=0;self.next=0}
    fn bytes(&self)->usize{self.map.capacity()*4+self.ids.capacity()*4+self.valid.capacity()+self.data.capacity()}
}
pub struct DynamicRecords {
    shards:Vec<Mutex<Shard>>,pub allocated:usize,
    hits:[AtomicU64;2],misses:[AtomicU64;2],inserts:AtomicU64,evictions:AtomicU64,
}
impl DynamicRecords {
    pub fn new(n:usize,cb:usize,rb:usize,budget:usize,static_map:&[u32])->Self {
        assert!(n<u32::MAX as usize && cb>0 && rb>0);
        let mut s=Self{shards:Vec::new(),allocated:0,hits:std::array::from_fn(|_|AtomicU64::new(0)),misses:std::array::from_fn(|_|AtomicU64::new(0)),inserts:AtomicU64::new(0),evictions:AtomicU64::new(0)};
        // 4096 covers container headers, mutexes and allocator rounding (64 allocations).
        let overhead=n*4+4096;
        if budget<overhead+SHARDS*(cb+rb+5){return s}
        let per=(budget-overhead)/SHARDS/(cb+rb+5);
        s.shards=Vec::with_capacity(SHARDS);
        for k in 0..SHARDS {
            let count=(k..n).step_by(SHARDS).count();
            let eligible=(k..n).step_by(SHARDS).filter(|&i|static_map.get(i).copied().unwrap_or(NONE)==NONE).count();
            s.shards.push(Mutex::new(Shard::new(count,per.min(eligible),cb,rb)));
        }
        s.allocated=4096+s.shards.iter().map(|p|p.lock().unwrap().bytes()).sum::<usize>();
        assert!(s.allocated<=budget);s
    }
    pub fn get(&self,id:u32,kind:usize,out:&mut[u8])->bool {
        if self.shards.is_empty(){return false}
        let hit=self.shards[id as usize%SHARDS].lock().unwrap().get(id as usize/SHARDS,kind,out);
        (if hit{&self.hits}else{&self.misses})[kind].fetch_add(1,Ordering::Relaxed);hit
    }
    pub fn put(&self,id:u32,kind:usize,bytes:&[u8]) {
        if self.shards.is_empty(){return}
        let (insert,evict)=self.shards[id as usize%SHARDS].lock().unwrap().put(id as usize/SHARDS,kind,bytes);
        self.inserts.fetch_add(insert as u64,Ordering::Relaxed);self.evictions.fetch_add(evict as u64,Ordering::Relaxed);
    }
    pub fn clear(&self){for p in &self.shards{p.lock().unwrap().clear()}self.reset()}
    pub fn reset(&self){for c in self.hits.iter().chain(self.misses.iter()).chain([&self.inserts,&self.evictions]){c.store(0,Ordering::Relaxed)}}
    pub fn stats(&self)->serde_json::Value {
        let mut cap=0;let mut nodes=0;let mut compact=0;let mut residual=0;let mut valid_bytes=0;
        for p in &self.shards{let p=p.lock().unwrap();cap+=p.ids.len();nodes+=p.len;for &v in &p.valid{if v&1!=0{compact+=1;valid_bytes+=p.cb}if v&2!=0{residual+=1;valid_bytes+=p.rb}}}
        serde_json::json!({"reserved_bytes":self.allocated,"capacity_nodes":cap,"resident_nodes":nodes,"compact_records":compact,"residual_records":residual,"valid_payload_bytes":valid_bytes,"hits":[self.hits[0].load(Ordering::Relaxed),self.hits[1].load(Ordering::Relaxed)],"misses":[self.misses[0].load(Ordering::Relaxed),self.misses[1].load(Ordering::Relaxed)],"node_insertions":self.inserts.load(Ordering::Relaxed),"node_evictions":self.evictions.load(Ordering::Relaxed)})
    }
}
#[cfg(test)] mod tests {
 use super::*;
 #[test]fn fifo_kind_validity_and_eviction(){
  let mut s=Shard::new(4,2,2,3);let mut out=[0;3];
  assert_eq!(s.put(0,0,&[1,2]),(true,false));assert!(!s.get(0,1,&mut out));
  assert_eq!(s.put(0,1,&[3,4,5]),(false,false));assert!(s.get(0,1,&mut out));assert_eq!(out,[3,4,5]);
  s.put(1,0,&[6,7]);s.get(0,1,&mut out);assert_eq!(s.put(2,1,&[8,9,10]),(true,true));
  assert!(!s.get(0,1,&mut out));assert!(!s.get(2,0,&mut [0;2]));assert!(s.get(2,1,&mut out));
  s.clear();assert!(!s.get(2,1,&mut out));assert_eq!(s.len,0);
 }
 #[test]fn budgets_and_static_exclusion(){
  for b in [0,4000,20000,100000]{let mut excluded=vec![NONE;1000];for i in (0..1000).step_by(2){excluded[i]=0}
   let d=DynamicRecords::new(1000,13,21,b,&excluded);assert!(d.allocated<=b);assert!(d.stats()["capacity_nodes"].as_u64().unwrap()<=500);
  }
 }
 #[test]fn concurrent_hits_are_copied_before_eviction(){
  let d=std::sync::Arc::new(DynamicRecords::new(3200,13,21,30000,&[]));
  let threads=(0..8).map(|t|{let d=d.clone();std::thread::spawn(move||{for i in 0..2000{let id=((i*16+t)%3200)as u32;let data=[(id%251)as u8;13];d.put(id,0,&data);let mut out=[0;13];if d.get(id,0,&mut out){assert_eq!(out,data)}}})}).collect::<Vec<_>>();
  for t in threads{t.join().unwrap()}assert!(d.stats()["node_evictions"].as_u64().unwrap()>0);
 }
}
