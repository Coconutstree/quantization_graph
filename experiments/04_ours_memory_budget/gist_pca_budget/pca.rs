//! PCA route only. Full-dimensional payload decoding remains in OursResidentCodec.
use std::{fs::{self,File},io::{Read,Write,Seek,SeekFrom,BufReader,BufWriter},path::Path,sync::Arc};
use crate::ours_diskann::{RabitqSpace,QueryComputer};
use crate::config::QueryCoarseCodec;
type R<T> = Result<T,String>;
fn floats(p:&Path)->R<Vec<f32>> {
 let mut f=BufReader::new(File::open(p).map_err(|e|e.to_string())?);
 let n=f.get_ref().metadata().map_err(|e|e.to_string())?.len() as usize;
 if n%4!=0{return Err("unaligned float file".into());}
 let mut v=Vec::with_capacity(n/4);let mut b=[0;4];
 for _ in 0..n/4{f.read_exact(&mut b).map_err(|e|e.to_string())?;v.push(f32::from_le_bytes(b));}
 if v.iter().any(|x|!x.is_finite()){return Err("nonfinite PCA asset".into());}Ok(v)
}
pub fn meta(dir:&Path)->R<serde_json::Value>{serde_json::from_slice(&fs::read(dir.join("pca.json")).map_err(|e|e.to_string())?).map_err(|e|e.to_string())}
pub fn field(m:&serde_json::Value,k:&str)->R<usize>{m[k].as_u64().and_then(|v|usize::try_from(v).ok()).ok_or(format!("invalid PCA field {k}"))}
pub fn extra_bytes(m:&serde_json::Value)->R<usize>{
 let d=field(m,"original_dim")?;let k=field(m,"dim")?;
 // Resident PCA basis, mean, eigenvalues, conservative lowdim rotation storage,
 // and per-worker query preparation buffers. Residual norms are charged separately.
 Ok(4*(d*d+2*d)+16*k*k+32*16*d)
}
pub struct PcaRoute {pub space:Arc<RabitqSpace>,pub norms:Vec<f32>,mean:Vec<f32>,basis:Vec<f32>,variance:Vec<f32>,pub k:usize,pub d:usize,pub tail_mode:String,pub extra:usize}
pub struct Prepared {pub computer:QueryComputer,pub norm:f32,pub projected_norm:f32,pub sigma:f32,_projected:Vec<f32>}
impl PcaRoute {
 pub fn load(dir:&Path,n:usize,d:usize,tail_mode:&str)->R<Self>{
  let m=meta(dir)?;let k=field(&m,"dim")?;
  if ![128,256,512].contains(&k)||field(&m,"original_dim")?!=d||field(&m,"count")?!=n{return Err("PCA dimension/count mismatch".into());}
  if !["none","norm","stat3"].contains(&tail_mode){return Err("PCA tail mode must be none/norm/stat3".into());}
  let mean=floats(&dir.join("mean.bin"))?;let basis=floats(&dir.join("basis.bin"))?;
  let variance=floats(&dir.join("variance.bin"))?;let norms=floats(&dir.join("residual.bin"))?;
  if mean.len()!=d||basis.len()!=d*d||variance.len()!=d||norms.len()!=n||norms.iter().chain(&variance).any(|&x|x<0.){return Err("invalid PCA asset lengths/norms".into());}
  let space=RabitqSpace::new_with_centroids(k,17,1,&vec![0.;k])?;
  if space.paper_msb_code_bytes()!=field(&m,"code_stride")?||space.paper_factor_bytes()!=field(&m,"factor_stride")?{return Err("PCA native layout mismatch".into());}
  Ok(Self{space,norms,mean,basis,variance,k,d,tail_mode:tail_mode.into(),extra:extra_bytes(&m)?})
 }
 pub fn configure(&self,c:QueryCoarseCodec){self.space.set_query_coarse_codec(if self.tail_mode=="norm"{QueryCoarseCodec::Full}else{c});}
 pub fn prepare(&self,q:&[f32])->R<Prepared>{
  if q.len()!=self.d{return Err("PCA query dimension mismatch".into());}
  let x=q.iter().zip(&self.mean).map(|(a,b)|a-b).collect::<Vec<_>>();
  let y=self.basis.chunks_exact(self.d).map(|row|row.iter().zip(&x).map(|(a,b)|(*a as f64)*(*b as f64)).sum::<f64>() as f32).collect::<Vec<_>>();
  let norm=y[self.k..].iter().map(|x|x*x).sum();
  let sigma=y[self.k..].iter().zip(&self.variance[self.k..]).map(|(q,v)|q*q*v).sum::<f32>().max(0.).sqrt();
  let projected_norm=y[..self.k].iter().map(|v|v*v).sum();
  Ok(Prepared{computer:self.space.prepare_query(&y[..self.k]).map_err(|e|e.to_string())?,norm,projected_norm,sigma,_projected:y})
 }
 pub fn correction(&self,id:usize,q:&Prepared)->f32{
  if self.tail_mode=="none"{return 0.;}
  let x=self.norms[id];let deterministic=(x.sqrt()-q.norm.sqrt()).powi(2);
  if self.tail_mode=="norm"{return deterministic;}
  // MRQ-inspired statistical gate, NOT an unconditional lower bound.
  deterministic.max(x+q.norm-6.*q.sigma)
 }
}
pub fn export(dir:&Path)->R<()> {
 let mut m=meta(dir)?;let k=field(&m,"dim")?;let n=field(&m,"count")?;
 let space=RabitqSpace::new_with_centroids(k,17,1,&vec![0.;k])?;
 let stride=space.paper_msb_code_bytes();let fstride=space.paper_factor_bytes();
 let target=dir.join("sidecar.bin");if target.exists(){return Err("preserve existing sidecar".into());}
 let mut out=File::create(&target).map_err(|e|e.to_string())?;
 out.write_all(b"QG05OSC1").map_err(|e|e.to_string())?;
 out.write_all(&((n*stride)as u64).to_le_bytes()).map_err(|e|e.to_string())?;
 out.write_all(&((n*fstride)as u64).to_le_bytes()).map_err(|e|e.to_string())?;
 out.set_len((24+n*(stride+fstride)) as u64).map_err(|e|e.to_string())?;
 let mut source=BufReader::new(File::open(dir.join("projected.bin")).map_err(|e|e.to_string())?);
 if source.get_ref().metadata().map_err(|e|e.to_string())?.len()!= (n*k*4)as u64{return Err("projected file length mismatch".into());}
 for start in (0..n).step_by(2048){
  let count=(n-start).min(2048);let mut compact=Vec::with_capacity(count*space.compact_record_bytes());
  for _ in 0..count{let mut row=vec![0.;k];for x in &mut row{let mut b=[0;4];source.read_exact(&mut b).map_err(|e|e.to_string())?;*x=f32::from_le_bytes(b);}let(c,_)=space.encode_parts(&row).map_err(|e|e.to_string())?;compact.extend(c);}
  let(c,f)=space.export_sidecar(&compact,count).map_err(|e|e.to_string())?;
  out.seek(SeekFrom::Start((24+start*stride)as u64)).map_err(|e|e.to_string())?;out.write_all(&c).map_err(|e|e.to_string())?;
  out.seek(SeekFrom::Start((24+n*stride+start*fstride)as u64)).map_err(|e|e.to_string())?;out.write_all(&f).map_err(|e|e.to_string())?;
 }
 m["code_stride"]=stride.into();m["factor_stride"]=fstride.into();
 let mut w=BufWriter::new(File::create(dir.join("pca.json")).map_err(|e|e.to_string())?);
 serde_json::to_writer_pretty(&mut w,&m).map_err(|e|e.to_string())?;
 Ok(())
}

/// Offline diagnostic only: raw gate bounds, without residual correction.
pub fn audit(args:&[String])->R<()> {
 use crate::disk_port::{ours_port::OursResidentCodec,routing::RoutingPlan};
 let index=Path::new(&args[2]);let directory=if args[3]=="original"{None}else{Some(Path::new(&args[3]))};
 let mode=args.get(7).map(String::as_str).unwrap_or("none");
 let codec=OursResidentCodec::load_planned_pca(&index.join("ours_quantizer.bin"),&index.join("ours_db1_sidecar.bin"),None,RoutingPlan::Resident,directory,mode).map_err(|e|e.to_string())?;
 codec.configure_query_codec(QueryCoarseCodec::Int8);
 let mut queries=BufReader::new(File::open(&args[4]).map_err(|e|e.to_string())?);
 let mut pairs=BufReader::new(File::open(&args[5]).map_err(|e|e.to_string())?);
 let mut out=BufWriter::new(File::create(&args[6]).map_err(|e|e.to_string())?);
 loop {
  let mut b=[0;4];match queries.read_exact(&mut b){Ok(())=>{},Err(e)if e.kind()==std::io::ErrorKind::UnexpectedEof=>break,Err(e)=>return Err(e.to_string())};
  let d=u32::from_le_bytes(b)as usize;let mut q=vec![0.;d];
  for x in &mut q{queries.read_exact(&mut b).map_err(|e|e.to_string())?;*x=f32::from_le_bytes(b);}
  pairs.read_exact(&mut b).map_err(|e|e.to_string())?;let count=u32::from_le_bytes(b)as usize;let mut ids=vec![0;count];
  for id in &mut ids{pairs.read_exact(&mut b).map_err(|e|e.to_string())?;*id=u32::from_le_bytes(b);}
  let prepared=codec.prepare(&q).map_err(|e|e.to_string())?;
  for e in prepared.paper_estimates(&ids,1.9).map_err(|e|e.to_string())?{
   if e.valid==0{return Err("invalid native bound".into());}out.write_all(&e.lower_bound.to_le_bytes()).map_err(|e|e.to_string())?;
  }
 }
 Ok(())
}
