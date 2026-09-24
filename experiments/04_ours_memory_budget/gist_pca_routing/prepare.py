"""Lowdim ranking followed by full4 verification; no lowdim hard-prune claim."""
import hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
WORK=ROOT/'work/ours_memory_budget/gist_pca_routing';OUT=ROOT/'results/04_ours_memory_budget/gist_pca_routing'
ASSETS=ROOT/'work/ours_memory_budget/gist_pca_budget'
def sha(p):
 with Path(p).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def dump(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def replace(p,a,b,count=1):
 s=p.read_text();assert s.count(a)>=count,(p,a);p.write_text(s.replace(a,b,count))
def main():
 assert not WORK.exists(),'preserve previous build';WORK.mkdir(parents=True);OUT.mkdir(parents=True,exist_ok=True)
 native=WORK/'native';shutil.copytree(ASSETS/'native',native)
 p=native/'Cargo.toml';replace(p,'ours-gist-pca-budget','ours-gist-pca-routing');replace(p,'ours_gist_pca_budget','ours_gist_pca_routing')
 p=native/'pca.rs'
 replace(p,'fn floats(p:&Path)->R<Vec<f32>> {','fn floats(p:&Path)->R<Vec<f32>> { floats_prefix(p,usize::MAX) }\nfn floats_prefix(p:&Path,count:usize)->R<Vec<f32>> {')
 replace(p,'let mut v=Vec::with_capacity(n/4);','let n=n.min(count.saturating_mul(4));\n let mut v=Vec::with_capacity(n/4);')
 replace(p,'Ok(4*(d*d+2*d)+16*k*k+32*16*d)','Ok(4*(d*k+d)+16*k*k+32*16*d)')
 replace(p,'if !["none","norm","stat3"].contains(&tail_mode)','if !["none","norm","stat3","route"].contains(&tail_mode)')
 replace(p,'let mean=floats(&dir.join("mean.bin"))?;let basis=floats(&dir.join("basis.bin"))?;', '''let mean=floats(&dir.join("mean.bin"))?;
  if tail_mode=="route" {
   let basis=floats_prefix(&dir.join("basis.bin"),d*k)?;
   if mean.len()!=d||basis.len()!=d*k{return Err("PCA projection dimensions mismatch".into());}
   let space=RabitqSpace::new_with_centroids(k,17,1,&vec![0.;k])?;
   if space.paper_msb_code_bytes()!=field(&m,"code_stride")?||space.paper_factor_bytes()!=field(&m,"factor_stride")?{return Err("PCA native layout mismatch".into());}
   return Ok(Self{space,norms:Vec::new(),mean,basis,variance:Vec::new(),k,d,tail_mode:tail_mode.into(),extra:extra_bytes(&m)?});
  }
  let basis=floats(&dir.join("basis.bin"))?;''')
 replace(p,'let sigma=y[self.k..].iter().zip(&self.variance[self.k..])','let sigma=y[self.k..].iter().zip(self.variance.get(self.k..).unwrap_or(&[]))')
 replace(p,'if self.tail_mode=="none"{return 0.;}','if self.tail_mode=="none"||self.tail_mode=="route"{return 0.;}')
 p=native/'native_main.rs'
 replace(p,'(n*(fs+4))as u64','(n*fs)as u64')
 replace(p,'args.optional("--pca-tail-mode").unwrap_or("norm")','args.optional("--pca-tail-mode").unwrap_or("route")')
 replace(p,'            codec.route_keep = keep;', '''            codec.navigation_keep=args.optional("--pca-route-keep").map(str::parse::<usize>).transpose().map_err(err)?;
            if codec.navigation_keep.is_some_and(|k|k>64){return Err("route shortlist must be 0(all)..64".into());}
            if codec.pca.is_some() && (codec.navigation_keep.is_none()||args.optional("--pca-tail-mode").unwrap_or("route")!="route"){return Err("PCA routing requires route mode and explicit shortlist".into());}
            codec.route_keep = keep;''')
 p=native/'ours_port.rs'
 replace(p,'pub struct OursResidentCodec {','pub struct OursResidentCodec {\n    pub navigation_keep: Option<usize>,')
 replace(p,'                pca: None,','                pca: None,\n                navigation_keep:None,')
 replace(p,'            pca,','            pca,\n            navigation_keep:None,')
 replace(p,'let estimates = if ablation.uses_gate() && codec.adaptive.is_none() {','let mut estimates = if ablation.uses_gate() && codec.adaptive.is_none() {')
 replace(p,'let values = prepared.paper_estimates(&fresh, epsilon)?;','let values = prepared.paper_estimates(&fresh, if codec.navigation_keep.is_some(){0.}else{epsilon})?;')
 replace(p,'            let mut survivor_ids = Vec::with_capacity(fresh.len());', '''            if let Some(keep)=codec.navigation_keep {
                let mut ranked=fresh.iter().copied().zip(estimates.iter().copied()).collect::<Vec<_>>();
                ranked.sort_by(|a,b|a.1.lower_bound.total_cmp(&b.1.lower_bound).then(a.0.cmp(&b.0)));
                if keep>0{
                    for (id,_) in ranked.iter().skip(keep){visited[*id as usize]=false;}
                    ranked.truncate(keep);
                }
                fresh=ranked.iter().map(|x|x.0).collect();estimates=ranked.iter().map(|x|x.1).collect();
            }
            let mut survivor_ids = Vec::with_capacity(fresh.len());''')
 replace(p,'if ablation.uses_gate()\n                    && pool.len()', 'if codec.navigation_keep.is_none() && ablation.uses_gate()\n                    && pool.len()')
 replace(p,'if ablation.uses_gate()\n                        && pool.len()', 'if codec.navigation_keep.is_none() && ablation.uses_gate()\n                        && pool.len()')
 # Route-only scores share the return structure, but never enter a bound-vs-tau test.
 rebuild()
def rebuild():
 native=WORK/'native'
 with (WORK/'build.log').open('w')as log:subprocess.run(['cargo','build','--release','--offline','--manifest-path',str(native/'Cargo.toml'),'--target-dir',str(ROOT/'src/graph_core/target')],stdout=log,stderr=subprocess.STDOUT,check=True)
 binary=WORK/'ours_gist_pca_routing';shutil.copy2(ROOT/'src/graph_core/target/release'/binary.name,binary)
 dump(OUT/'build.json',dict(binary_sha256=sha(binary),sources={str(p):sha(p)for p in [*native.iterdir(),*(ASSETS/'dependencies').rglob('*')]if p.is_file()}))
if __name__=='__main__':main()
