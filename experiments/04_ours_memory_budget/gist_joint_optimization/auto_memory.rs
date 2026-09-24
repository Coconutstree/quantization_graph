//! Whole-process admission: fixed, factors and unallocated safety are distinct.
use std::{fs,sync::OnceLock};
use crate::{Args,Result,err};
use crate::disk_port::routing::{RoutingPlan,PagingPlan,PAGE_CHARGE,SERVICE_RESERVE};
const MIB:usize=1024*1024;
#[derive(Clone,Debug)]
pub struct Plan {pub budget:usize,pub routing:RoutingPlan,pub optional:usize,pub policy:String,pub expected_peak:usize}
static PLAN:OnceLock<Plan>=OnceLock::new();
pub fn get()-> &'static Plan {PLAN.get().expect("memory plan precedes loading")}
pub fn stage(name:&str){
 let status=fs::read_to_string("/proc/self/status").unwrap();
 let fields=status.lines().filter(|s|s.starts_with("VmSize:")||s.starts_with("VmPeak:")||s.starts_with("VmRSS:")||s.starts_with("Threads:")).collect::<Vec<_>>().join("; ");
 eprintln!("memory_stage name={name} {fields}");
}
fn sum(v:&[usize])->Result<usize>{v.iter().try_fold(0usize,|a,&b|a.checked_add(b).ok_or("memory arithmetic overflow".into()))}
fn available(b:usize,need:usize)->Result<usize>{b.checked_sub(need).ok_or_else(||format!("memory deficit {} bytes",need-b))}
pub fn thresholds(fixed:usize,reserve:usize,c:usize,f:usize,scratch:usize,minimum_record:usize)->Result<[usize;3]>{
 let base=sum(&[fixed,reserve,f])?;
 Ok([sum(&[base,SERVICE_RESERVE,scratch,PAGE_CHARGE])?,sum(&[base,c])?,sum(&[base,c,minimum_record])?])
}
pub fn choose(b:usize,fixed:usize,reserve:usize,c:usize,f:usize,scratch:usize,min_record:usize,mode:&str,cap:Option<usize>)->Result<Plan>{
 let u=available(b,sum(&[fixed,reserve,f])?)?;
 let resident=match mode{"auto"=>u>=c,"resident"|"hot_dynamic"=>true,"paged"=>false,_=>return Err("policy must be auto, resident, hot_dynamic or paged; factors always resident".into())};
 if resident {
  let spare=available(u,c)?;
  let quota=if mode=="resident"{0}else{spare.min(cap.unwrap_or(usize::MAX))};
  let optional=if quota>=min_record{quota}else{0};
  Ok(Plan{budget:b,routing:RoutingPlan::Resident,optional,policy:if optional==0{"resident"}else{"hot_dynamic"}.into(),expected_peak:sum(&[fixed,f,c,optional])?})
 }else{
  let bytes=available(u,sum(&[scratch,SERVICE_RESERVE])?)?.min(cap.unwrap_or(usize::MAX));
  if bytes<PAGE_CHARGE{return Err(format!("minimum codes paging deficit {} bytes",PAGE_CHARGE-bytes));}
  let capacity=(bytes/PAGE_CHARGE).min(sum(&[c,24])?.div_ceil(4096));
  if capacity==0{return Err("codes paging capacity is zero".into());}
  let reserved=sum(&[SERVICE_RESERVE,capacity.checked_mul(PAGE_CHARGE).ok_or("page capacity overflow")?])?;
  Ok(Plan{budget:b,routing:RoutingPlan::Paged(PagingPlan{clock:true,resident_factors:true,capacity,inflight:64.min(capacity),reserved_bytes:reserved}),optional:0,policy:"paged".into(),expected_peak:sum(&[fixed,f,scratch,reserved])?})
 }
}
pub fn routing(args:&Args,codes:u64,factors:u64,centroid:u64,stride:u64,workers:u64,_mapping:u64)->Result<RoutingPlan>{
 if workers!=32 || args.text("--layer")?!="05c" || args.text("--cache-mode")?!="c0"{return Err("experiment requires 05c C0 workers32".into());}
 let declared=(args.number::<f64>("--search-dram-budget-gib")?*(1u64<<30)as f64).floor();
 if !declared.is_finite()||declared<1.||declared>=usize::MAX as f64{return Err("invalid process budget".into());}
 let limits=fs::read_to_string("/proc/self/limits").map_err(err)?;
 let limit=limits.lines().find_map(|l|l.strip_prefix("Max address space")).and_then(|l|l.split_whitespace().next()).ok_or("missing RLIMIT_AS")?;
 let soft=if limit=="unlimited"{usize::MAX}else{limit.parse().map_err(err)?};let b=(declared as usize).min(soft);
 let calibration=args.optional("--auto-calibration")==Some("1");
 let (fixed,reserve)=if calibration{(0,0)}else{
  let v:serde_json::Value=serde_json::from_slice(&fs::read(args.optional("--auto-calibration-file").ok_or("missing calibration file")?).map_err(err)?).map_err(err)?;
  if v["locked"]!=true{return Err("calibration not locked".into());}
  (usize::try_from(v["fixed_bytes"].as_u64().ok_or("missing fixed bytes")?).map_err(err)?,usize::try_from(v["reserve_bytes"].as_u64().ok_or("missing reserve bytes")?).map_err(err)?)
 };
 let scratch=usize::try_from(stride.checked_mul(2).and_then(|n|n.checked_add(1024)).and_then(|n|n.checked_mul(64)).and_then(|n|n.checked_mul(workers)).ok_or("scratch overflow")?).map_err(err)?;
 // Metadata has already been validated by preflight_ours_routing.
 use std::io::Read;
 let mut h=[0u8;68];fs::File::open(std::path::Path::new(args.text("--disk-index-dir")?).join("ours_quantizer.bin")).map_err(err)?.read_exact(&mut h).map_err(err)?;
 let field=|o|usize::try_from(u64::from_le_bytes(h[o..o+8].try_into().unwrap())).map_err(err);
 let n=field(28)?;let record=sum(&[field(36)?,field(44)?])?;
 let min_record=sum(&[n.checked_mul(std::mem::size_of::<u32>()).ok_or("record map overflow")?,record])?;
 let c=usize::try_from(codes).map_err(err)?;let f=usize::try_from(factors).map_err(err)?;
 let t=thresholds(fixed,reserve,c,f,scratch,min_record)?;
 let cap=args.optional("--memory-cache-bytes").filter(|s|*s!="auto").map(str::parse::<usize>).transpose().map_err(err)?;
 let mode=args.optional("--memory-policy").unwrap_or("auto");
 let joint=args.optional("--record-cache-budget-bytes").map(str::parse::<usize>).transpose().map_err(err)?;
 let result=if let Some(record)=joint {
  (|| -> Result<Plan> {
   if record>0 && record<min_record{return Err("record quota below minimum map and record".into());}
   let route_cap=args.optional("--route-page-budget-bytes").map(str::parse::<usize>).transpose().map_err(err)?;
   if !matches!(mode,"resident"|"paged"){return Err("joint experiment requires explicit resident/paged mode".into());}
   let mut p=choose(available(b,record)?,fixed,reserve,c,f,scratch,min_record,mode,route_cap)?;
   p.budget=b;p.optional=record;p.expected_peak=sum(&[p.expected_peak,record])?;
   if let RoutingPlan::Paged(ref mut page)=p.routing {
    let inflight=args.optional("--routing-max-inflight-pages").unwrap_or("64").parse::<usize>().map_err(err)?;
    if inflight==0 || inflight>64{return Err("diagnostic inflight must be 1..64".into());}
    page.inflight=inflight.min(page.capacity);
    page.clock=match args.optional("--routing-cache-policy").unwrap_or("clock") {"clock"=>true,"lru"=>false,_=>return Err("invalid cache policy".into())};
   }
   Ok(p)
  })()
 }else{choose(b,fixed,reserve,c,f,scratch,min_record,mode,cap)};
 let mut value=serde_json::json!({"budget_bytes":b,"fixed_bytes":fixed,"reserve_bytes":reserve,"factors_bytes":f,"factors_resident":true,"codes_bytes":c,"centroid_bytes":centroid,"usable_bytes":b.checked_sub(sum(&[fixed,reserve,f])?),"threshold_paged_bytes":t[0],"threshold_resident_bytes":t[1],"threshold_record_cache_bytes":t[2],"record_cache_minimum_bytes":min_record,"calibration":calibration});
 if let Ok(ref p)=result{
  let (pages,inflight,service,ps)=match p.routing{RoutingPlan::Resident=>(0,0,0,0),RoutingPlan::Paged(x)=>(x.capacity,x.inflight,SERVICE_RESERVE,scratch)};
  let extra=serde_json::json!({"mode":p.policy,"routing_capacity_pages":pages,"max_inflight_pages":inflight,"routing_page_data_bytes":pages*4096,"routing_page_metadata_bytes":pages*(PAGE_CHARGE-4096),"paging_service_bytes":service,"paging_scratch_bytes":ps,"optional_cache_bytes":p.optional,"expected_peak_bytes":p.expected_peak,"admission_bytes":sum(&[p.expected_peak,reserve])?,"unused_after_reserve_bytes":b-sum(&[p.expected_peak,reserve])?,"status":"admitted"});
  value.as_object_mut().unwrap().extend(extra.as_object().unwrap().clone());
 }else{value["status"]="admission_rejected".into();value["error"]=result.as_ref().unwrap_err().clone().into();value["minimum_auto_deficit_bytes"]=t[0].min(t[1]).saturating_sub(b).into();}
 if let Some(path)=args.optional("--auto-plan-output"){fs::write(path,serde_json::to_vec_pretty(&value).map_err(err)?).map_err(err)?;}
 eprintln!("auto_memory_plan {value}");stage("before_codec");
 let plan=result?;let routing=plan.routing;PLAN.set(plan).map_err(|_|"memory plan already set")?;
 if args.optional("--preflight-only")==Some("1"){std::process::exit(0);}Ok(routing)
}
#[cfg(test)]mod tests{
 use super::*;
 #[test]fn whole_process_boundaries(){
 let(fixed,reserve,c,f,s,m)=(357*MIB,64*MIB,128000000,20000000,2703360,4001177);
 let t=thresholds(fixed,reserve,c,f,s,m).unwrap();
 let go=|b|choose(b,fixed,reserve,c,f,s,m,"auto",None);
 assert!(go(t[0]-1).is_err());
 let p=go(t[0]).unwrap();if let RoutingPlan::Paged(x)=p.routing{assert_eq!(x.capacity,1);assert_eq!(x.inflight,1);assert!(x.resident_factors);}else{panic!()}
 assert_eq!(go(t[1]-1).unwrap().policy,"paged");
 assert_eq!(go(t[1]).unwrap().policy,"resident");assert_eq!(go(t[1]+1).unwrap().optional,0);
 assert_eq!(go(t[2]-1).unwrap().optional,0);assert_eq!(go(t[2]).unwrap().optional,m);assert_eq!(go(t[2]+1).unwrap().optional,m+1);
 for b in [t[0],t[0]+1,t[1]-1,t[1],t[2],640*MIB]{let p=go(b).unwrap();assert!(p.expected_peak+reserve<=b);}
 assert_eq!(thresholds(fixed,reserve,c,f+123,s,m).unwrap().map(|v|v-123),t);
 assert_eq!(thresholds(fixed+5,reserve+7,c,f,s,m).unwrap().map(|v|v-12),t);
 assert!(thresholds(usize::MAX,reserve,c,f,s,m).is_err());
 assert!(choose(t[0],fixed,reserve,c,f,s,m,"paged",Some(PAGE_CHARGE-1)).is_err());
 }
}
