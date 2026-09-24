// PCA prefix routing with optional residual-norm contribution.
// The benchmark compares these scores with approximate full4 thresholds;
// this is an empirical gate, not a certified native full4 lower bound.
pub fn residual_squared(centered: &[f32], projected: &[f32]) -> f32 {
    let full = centered.iter().map(|&x| (x as f64).powi(2)).sum::<f64>();
    let kept = projected.iter().map(|&x| (x as f64).powi(2)).sum::<f64>();
    (full - kept).max(0.) as f32
}
pub fn residual_lower_bound(x_squared: f32, q_squared: f32) -> f32 {
    (x_squared.max(0.).sqrt() - q_squared.max(0.).sqrt()).powi(2)
}
impl PcaRoute {
 pub fn load(dir:&Path,n:usize,d:usize,tail_mode:&str)->R<Self>{
  let m=meta(dir)?;let k=field(&m,"dim")?;
  if k==0||k>=d||field(&m,"original_dim")?!=d||field(&m,"count")?!=n{return Err("PCA dimension/count mismatch".into());}
  if !["none","norm"].contains(&tail_mode){return Err("PCA residual mode must be none or norm".into());}
  let mean=floats(&dir.join("mean.bin"))?;
  let basis=floats_prefix(&dir.join("basis.bin"),d*k)?;
  let norms=if tail_mode=="norm"{floats(&dir.join("residual.bin"))?}else{Vec::new()};
  if mean.len()!=d||basis.len()!=d*k||(tail_mode=="norm"&&norms.len()!=n)||norms.iter().any(|&x|x<0.){
   return Err("PCA asset dimensions or residual norms invalid".into());
  }
  let space=RabitqSpace::new_with_centroids(k,17,1,&vec![0.;k])?;
  if space.paper_msb_code_bytes()!=field(&m,"code_stride")?||space.paper_factor_bytes()!=field(&m,"factor_stride")?{
   return Err("PCA native layout mismatch".into());
  }
  Ok(Self{space,norms,mean,basis,variance:Vec::new(),k,d,tail_mode:tail_mode.into(),extra:extra_bytes(&m)?})
 }
 pub fn configure(&self,c:QueryCoarseCodec){self.space.set_query_coarse_codec(c);}
 pub fn prepare(&self,q:&[f32])->R<Prepared>{
  if q.len()!=self.d{return Err("PCA query dimension mismatch".into());}
  let x=q.iter().zip(&self.mean).map(|(a,b)|a-b).collect::<Vec<_>>();
  let y=self.basis.chunks_exact(self.d).map(|row|row.iter().zip(&x).map(|(a,b)|(*a as f64)*(*b as f64)).sum::<f64>() as f32).collect::<Vec<_>>();
  let norm=if self.tail_mode=="norm"{residual_squared(&x,&y)}else{0.};
  let projected_norm=y.iter().map(|v|v*v).sum();
  Ok(Prepared{computer:self.space.prepare_query(&y).map_err(|e|e.to_string())?,norm,projected_norm,sigma:0.,_projected:y})
 }
 pub fn correction(&self,id:usize,q:&Prepared)->f32{
  if self.tail_mode=="norm"{residual_lower_bound(self.norms[id],q.norm)}else{0.}
 }
}
#[cfg(test)] mod residual_tests {
 use super::*;
 #[test] fn orthogonal_tail_and_query_formula(){
  let x=[3.,4.,12.];let q=[1.,2.,5.];
  let tx=residual_squared(&x,&x[..2]);let tq=residual_squared(&q,&q[..2]);
  assert_eq!(tx,144.);assert_eq!(tq,25.);
  let lb=residual_lower_bound(tx,tq);
  assert_eq!(lb,49.);assert_eq!(residual_lower_bound(tx,tx),0.);
  let same_norm_opposite_tail=[-12.,0.];let other=[12.,0.];
  assert!(residual_lower_bound(144.,144.)<=same_norm_opposite_tail.iter().zip(other).map(|(a,b)|(*a-b)*(*a-b)).sum::<f32>());
 }
}
