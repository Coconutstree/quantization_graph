//! Independent sweep admission; fixed cost is locked from calibration evidence.
use std::{fs,path::Path,sync::OnceLock};
use crate::{Args,Result,err};
use crate::disk_port::routing::{RoutingPlan,PagingPlan,PAGE_CHARGE,SERVICE_RESERVE};
const MIB:usize=1024*1024;
#[derive(Clone,Debug)]
pub struct Plan {pub fixed:usize,pub budget:usize,pub routing:RoutingPlan,pub optional:usize,pub policy:String,pub expected_peak:usize}
static PLAN:OnceLock<Plan>=OnceLock::new();
pub fn get()-> &'static Plan {PLAN.get().expect("memory plan precedes loading")}
pub fn stage(name:&str){
 let status=fs::read_to_string("/proc/self/status").unwrap();
 let fields=status.lines().filter(|s|s.starts_with("VmSize:")||s.starts_with("VmPeak:")||s.starts_with("VmRSS:")||s.starts_with("Threads:")).collect::<Vec<_>>().join("; ");
 eprintln!("memory_stage name={name} {fields}");
}
pub fn choose(budget:usize,fixed:usize,codes:usize,factors:usize,scratch:usize,mode:&str,cache_cap:Option<usize>)->Result<Plan>{
 let whole=codes.checked_add(factors).ok_or("routing overflow")?;
 let available=budget.checked_sub(fixed).ok_or_else(||format!("minimum fixed memory deficit {} bytes",fixed-budget))?;
 let resident=match mode {"auto"=>available>=whole,"resident"|"hot_dynamic"=>true,"paged"|"factors"=>false,_=>return Err("invalid sweep memory policy".into())};
 if resident {
  let spare=available.checked_sub(whole).ok_or_else(||format!("resident memory deficit {} bytes",whole-available))?;
  let quota=if mode=="resident" {0}else{spare.min(cache_cap.unwrap_or(usize::MAX))};
  let optional=if quota>=4_000_000+1177 {quota}else{0}; // GIST node map plus one full record
  return Ok(Plan{budget,fixed,routing:RoutingPlan::Resident,optional,policy:if optional==0{"resident"}else{"hot_dynamic"}.into(),expected_peak:fixed+whole+optional});
 }
 let overhead=scratch.checked_add(SERVICE_RESERVE).ok_or("paging overhead overflow")?;
 let min_page=overhead.checked_add(PAGE_CHARGE).ok_or("page minimum overflow")?;
 let keep_factors=mode=="factors" || (mode=="auto" && available>=factors.checked_add(min_page).ok_or("factor minimum overflow")?);
 let kept=if keep_factors{factors}else{0};
 let minimum=kept.checked_add(min_page).ok_or("paging minimum overflow")?;
 if available<minimum{return Err(format!("minimum paging memory deficit {} bytes",minimum-available));}
 let bytes=(available-kept-overhead).min(cache_cap.unwrap_or(usize::MAX));
 let disk_bytes=if keep_factors{codes}else{whole};
 let capacity=(bytes/PAGE_CHARGE).min(disk_bytes.checked_add(24).ok_or("page count overflow")?.div_ceil(4096));
 if capacity==0{return Err("routing cache cap cannot hold one page".into());}
 let reserved=SERVICE_RESERVE+capacity*PAGE_CHARGE;
 Ok(Plan{budget,fixed,routing:RoutingPlan::Paged(PagingPlan{clock:true,resident_factors:keep_factors,capacity,inflight:64.min(capacity),reserved_bytes:reserved}),optional:0,
  policy:if keep_factors{"factors"}else{"paged"}.into(),expected_peak:fixed+scratch+kept+reserved})
}
pub fn routing(args:&Args,codes:u64,factors:u64,centroid:u64,stride:u64,workers:u64,_mapping:u64)->Result<RoutingPlan>{
 if workers!=32 || args.text("--layer")?!="05c" || args.text("--cache-mode")?!="c0" {return Err("sweep requires 05c C0 workers32".into());}
 let declared=(args.number::<f64>("--search-dram-budget-gib")?*(1u64<<30)as f64).floor();
 if !declared.is_finite() || declared<1. || declared>=usize::MAX as f64{return Err("invalid sweep budget".into());}
 let limits=fs::read_to_string("/proc/self/limits").map_err(err)?;
 let limit=limits.lines().find_map(|l|l.strip_prefix("Max address space")).and_then(|l|l.split_whitespace().next()).ok_or("missing RLIMIT_AS")?;
 let soft=if limit=="unlimited"{usize::MAX}else{limit.parse().map_err(err)?};let budget=(declared as usize).min(soft);
 let calibration=args.optional("--auto-calibration")==Some("1");
 let fixed=if calibration{0}else{
  let path=args.optional("--auto-calibration-file").ok_or("missing locked calibration file")?;
  let v:serde_json::Value=serde_json::from_slice(&fs::read(path).map_err(err)?).map_err(err)?;
  if v["locked"]!=true{return Err("calibration is not locked".into());}
  usize::try_from(v["fixed_bytes"].as_u64().ok_or("invalid calibrated fixed bytes")?).map_err(err)?
 };
 let scratch=usize::try_from(stride.checked_mul(2).and_then(|n|n.checked_add(1024)).and_then(|n|n.checked_mul(64)).and_then(|n|n.checked_mul(workers)).ok_or("scratch overflow")?).map_err(err)?;
 let cap=args.optional("--memory-cache-bytes").filter(|s|*s!="auto").map(str::parse::<usize>).transpose().map_err(err)?;
 let mode=args.optional("--memory-policy").unwrap_or("auto");
 let plan=choose(budget,fixed,usize::try_from(codes).map_err(err)?,usize::try_from(factors).map_err(err)?,scratch,mode,cap)?;
 let (pages,inflight)=match plan.routing{RoutingPlan::Resident=>(0,0),RoutingPlan::Paged(p)=>(p.capacity,p.inflight)};
 let value=serde_json::json!({"mode":plan.policy,"budget_bytes":budget,"fixed_bytes":fixed,"codes_bytes":codes,"factors_bytes":factors,"centroid_bytes":centroid,"paging_scratch_bytes":if pages>0{scratch}else{0},"routing_capacity_pages":pages,"max_inflight_pages":inflight,"optional_cache_bytes":plan.optional,"expected_peak_bytes":plan.expected_peak,"calibration":calibration});
 if let Some(path)=args.optional("--auto-plan-output"){fs::write(path,serde_json::to_vec_pretty(&value).map_err(err)?).map_err(err)?;}
 eprintln!("auto_memory_plan {value}");stage("before_codec");
 let result=plan.routing;PLAN.set(plan).map_err(|_|"memory plan already initialized")?;
 if args.optional("--preflight-only")==Some("1"){std::process::exit(0);}
 Ok(result)
}
#[cfg(test)] mod tests{
 use super::*;
 #[test]fn all_boundaries(){let(f,c,k,s)=(1000,100_000_000,20_000_000,1024);let t=f+c+k;
 assert!(matches!(choose(t,f,c,k,s,"auto",None).unwrap().routing,RoutingPlan::Resident));
 assert_eq!(choose(t,f,c,k,s,"auto",None).unwrap().optional,0);
 assert_eq!(choose(t+1,f,c,k,s,"auto",None).unwrap().optional,0);
 assert_eq!(choose(t+4_001_177,f,c,k,s,"auto",None).unwrap().optional,4_001_177);
 assert_eq!(choose(t-1,f,c,k,s,"auto",None).unwrap().policy,"factors");
 let m=f+s+SERVICE_RESERVE+PAGE_CHARGE;
 assert!(choose(m-1,f,c,k,s,"auto",None).is_err());
 let p=choose(m,f,c,k,s,"auto",None).unwrap();assert_eq!(p.policy,"paged");
 if let RoutingPlan::Paged(p)=p.routing{assert_eq!((p.capacity,p.inflight),(1,1));}else{panic!()}
 assert_eq!(choose(m+k-1,f,c,k,s,"auto",None).unwrap().policy,"paged");
 assert_eq!(choose(m+k,f,c,k,s,"auto",None).unwrap().policy,"factors");
 assert!(choose(m+k-1,f,c,k,s,"factors",None).is_err());
 assert!(choose(usize::MAX,0,usize::MAX,1,s,"auto",None).is_err());
 }
}
