// B covers resident codes or codes page DATA buffers; factors stay outside B.
pub fn choose_codes(data:usize,process:usize,fixed:usize,codes:usize,factors:usize,scratch:usize,mode:&str,cap:Option<usize>)->Result<Plan>{
 let resident=match mode {"auto"=>data>=codes,"resident"|"hot_dynamic"=>true,"factors"=>false,_=>return Err("codes-capacity experiment keeps factors resident; invalid policy".into())};
 let mut p=if resident {
  // Reuse record-cache allocation rules with codes as the only charged array.
  choose(data,0,codes,0,scratch,mode,cap)?
 }else{
  let capacity=(data.min(cap.unwrap_or(usize::MAX))/4096).min(codes.checked_add(24).ok_or("code page span overflow")?.div_ceil(4096));
  if capacity==0{return Err("codes page-buffer capacity deficit: minimum 4096 bytes".into());}
  let reserved=capacity.checked_mul(PAGE_CHARGE).and_then(|n|n.checked_add(SERVICE_RESERVE)).ok_or("paging allocation overflow")?;
  Plan{budget:process,fixed:0,routing:RoutingPlan::Paged(PagingPlan{clock:true,resident_factors:true,capacity,inflight:64.min(capacity),reserved_bytes:reserved}),optional:0,policy:"factors".into(),expected_peak:reserved.checked_add(scratch).ok_or("paging scratch overflow")?}
 };
 p.budget=process;p.fixed=fixed;
 p.expected_peak=p.expected_peak.checked_add(factors).and_then(|n|n.checked_add(fixed)).ok_or("process estimate overflow")?;
 if p.expected_peak>process{return Err(format!("independent process allowance deficit {} bytes; codes policy unchanged",p.expected_peak-process));}
 Ok(p)
}
#[cfg(test)]mod codes_tests{
 use super::*;
 #[test]fn factors_do_not_consume_codes_capacity(){
  let(c,s,fixed)=(128_000_000,2_703_360,421*MIB);
  for f in [20_000_000,40_000_000]{for process in [1024*MIB,2048*MIB]{
   assert_eq!(choose_codes(c-1,process,fixed,c,f,s,"auto",None).unwrap().policy,"factors");
   let p=choose_codes(c,process,fixed,c,f,s,"auto",None).unwrap();assert_eq!(p.policy,"resident");assert_eq!(p.optional,0);assert_eq!(p.expected_peak,fixed+c+f);
   assert_eq!(choose_codes(c+1,process,fixed,c,f,s,"auto",None).unwrap().policy,"resident");
   assert_eq!(choose_codes(c+4_001_177,process,fixed,c,f,s,"auto",None).unwrap().policy,"hot_dynamic");
   assert_eq!(choose_codes(128*MIB,process,fixed,c,f,s,"auto",None).unwrap().policy,"hot_dynamic");
   assert!(choose_codes(4095,process,fixed,c,f,s,"auto",None).is_err());
   let p=choose_codes(4096,process,fixed,c,f,s,"auto",None).unwrap();
   if let RoutingPlan::Paged(p)=p.routing{assert_eq!(p.capacity,1);assert_eq!(p.inflight,1);assert!(p.resident_factors);}else{panic!()}
  }}
  assert!(choose_codes(c,fixed+c+20_000_000-1,fixed,c,20_000_000,s,"auto",None).unwrap_err().contains("process allowance deficit"));
 }
}
