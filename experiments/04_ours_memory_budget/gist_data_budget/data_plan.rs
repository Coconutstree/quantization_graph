// Data/cache capacity selects residency; process admission is a separate check.
pub fn choose_data(data:usize,process:usize,fixed:usize,codes:usize,factors:usize,scratch:usize,mode:&str,cap:Option<usize>)->Result<Plan>{
 let whole=codes.checked_add(factors).ok_or("routing size overflow")?;
 let overhead=scratch.checked_add(SERVICE_RESERVE).ok_or("paging overhead overflow")?;
 let resident=match mode {"auto"=>data>=whole,"resident"|"hot_dynamic"=>true,"paged"|"factors"=>false,_=>return Err("invalid data policy".into())};
 // Existing paging planner charges scheduler/scratch. Supply those separately;
 // cache slots (4096 payload + 256 metadata) remain charged to the data quota.
 let virtual_budget=if resident{data}else{data.checked_add(overhead).ok_or("data budget overflow")?};
 let mut p=choose(virtual_budget,0,codes,factors,scratch,mode,cap)?;
 // For data < whole, choose(auto) must not switch to resident due to the
 // scheduler allowance added above. Force the data-based paging decision.
 if !resident && mode=="auto" {
  let keep=data>=factors.checked_add(PAGE_CHARGE).ok_or("factors minimum overflow")?;
  p=choose(virtual_budget,0,codes,factors,scratch,if keep{"factors"}else{"paged"},cap)?;
 }
 p.budget=process;p.fixed=fixed;
 p.expected_peak=fixed.checked_add(p.expected_peak).ok_or("process estimate overflow")?;
 if p.expected_peak>process{return Err(format!("process allowance deficit {} bytes; data policy is unchanged",p.expected_peak-process));}
 Ok(p)
}
#[cfg(test)]mod data_tests {
 use super::*;
 #[test]fn residency_depends_only_on_data_budget(){
  let(c,f,s,b)=(128_000_000,20_000_000,2_703_360,421*MIB);let t=c+f;
  for process in [1024*MIB,2048*MIB]{
   assert_eq!(choose_data(t-1,process,b,c,f,s,"auto",None).unwrap().policy,"factors");
   let exact=choose_data(t,process,b,c,f,s,"auto",None).unwrap();assert_eq!(exact.policy,"resident");assert_eq!(exact.optional,0);
   assert_eq!(choose_data(t+1,process,b,c,f,s,"auto",None).unwrap().policy,"resident");
   assert_eq!(choose_data(t+4_001_177,process,b,c,f,s,"auto",None).unwrap().policy,"hot_dynamic");
   assert_eq!(choose_data(c,process,b,c,f,s,"auto",None).unwrap().policy,"factors");
   assert_eq!(choose_data(f+PAGE_CHARGE-1,process,b,c,f,s,"auto",None).unwrap().policy,"paged");
   assert_eq!(choose_data(f+PAGE_CHARGE,process,b,c,f,s,"auto",None).unwrap().policy,"factors");
   assert!(choose_data(PAGE_CHARGE-1,process,b,c,f,s,"auto",None).is_err());
   assert_eq!(choose_data(PAGE_CHARGE,process,b,c,f,s,"auto",None).unwrap().policy,"paged");
  }
  assert!(choose_data(t,b+t-1,b,c,f,s,"auto",None).unwrap_err().contains("process allowance deficit"));
 }
}
