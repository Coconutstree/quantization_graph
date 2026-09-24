"""Freeze three M=32 empirical-gate variants in an isolated binary."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
WORK=ROOT/'work/ours_memory_budget/gist_pca_residual_ablation'
OUT=ROOT/'results/04_ours_memory_budget/gist_pca_residual_ablation'
ASSETS=ROOT/'work/ours_memory_budget/gist_pca_budget'
PARENT=ROOT/'work/ours_memory_budget/gist_adaptive_resident'
BIN=WORK/'ours_gist_pca_residual_ablation'

def dump(p,x):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def replace(p,a,b,count=1):
 s=p.read_text();assert s.count(a)==count,(p,a,s.count(a));p.write_text(s.replace(a,b))

def build():
 if BIN.exists():
  b=json.loads((OUT/'build.json').read_text());assert sha(BIN)==b['binary_sha256']
  for p,h in b['sources'].items():assert sha(p)==h,p
  return
 native=WORK/'native';assert not native.exists(),'preserve an incomplete build'
 parent=json.loads((ROOT/'results/04_ours_memory_budget/gist_adaptive_resident/build.json').read_text())
 for p,h in parent['sources'].items():assert sha(p)==h,p
 shutil.copytree(PARENT/'native',native)
 p=native/'Cargo.toml';replace(p,'ours-gist-adaptive-resident','ours-gist-pca-residual-ablation');replace(p,'ours_gist_adaptive_resident',BIN.name)
 p=native/'pca.rs';s=p.read_text();start=s.index('impl PcaRoute {');end=s.index('pub fn export(',start)
 p.write_text(s[:start]+(HERE/'pca_route.rs').read_text()+'\n'+s[end:])
 p=native/'ours_port.rs'
 replace(p,'    pub navigation_keep: Option<usize>,','    pub navigation_keep: Option<usize>,\n    pub comparison_gate: bool,')
 replace(p,'navigation_keep:None,','navigation_keep:None,\n            comparison_gate:true,',2)
 s=p.read_text();start=s.index('        let epsilon=if self.owner.pca');end=s.index('        let mut output =',start)
 s=s[:start]+'''        let correct=|output:&mut [RabitqPaperEstimate]|{
            // epsilon=0 is the shared top-M ranking pass; residual affects only the gate.
            if epsilon>0. {
                if let(Some(p),Some(q))=(&self.owner.pca,&self.pca){
                    for(&id,e)in ids.iter().zip(output){
                        e.lower_bound=e.lower_bound.max(0.)+p.correction(id as usize,q);
                    }
                }
            }
        };
'''+s[end:];p.write_text(s)
 replace(p,'            let mut survivor_ids = Vec::with_capacity(fresh.len());','''            if codec.comparison_gate && ablation.uses_gate() {
                let started=Instant::now();
                estimates=prepared.paper_estimates(&fresh,epsilon)?;
                stats.db1_checks+=fresh.len() as u64;
                stats.queue_compute_us+=started.elapsed().as_secs_f64()*1_000_000.;
            }
            let mut survivor_ids = Vec::with_capacity(fresh.len());''')
 replace(p,'if codec.navigation_keep.is_none() && ablation.uses_gate()','if codec.comparison_gate && ablation.uses_gate()',2)
 s=p.read_text();start=s.index('                let modes = estimates\n');end=s.index('                let short_ips =',start)
 s=s[:start]+'''                // Same full-dimensional verification kernel in every variant.
                // No reuse of lowdim or INT8 MSB inner products.
                let modes=vec![DIST_MODE_RECOMPUTE_FULL;estimates.len()];
'''+s[end:];p.write_text(s)
 p=native/'native_main.rs';s=p.read_text();start=s.index('            if codec.pca.is_some() && !matches!(codec.navigation_keep');end=s.index('            codec.route_keep = keep;',start)
 s=s[:start]+'''            if codec.navigation_keep!=Some(32){return Err("three-way comparison requires M=32".into());}
            codec.comparison_gate=match args.optional("--comparison-gate").unwrap_or("1") {
                "0"=>false,"1"=>true,_=>return Err("comparison gate must be 0 or 1".into())
            };
            if codec.pca.is_some() && !["none","norm"].contains(&args.optional("--pca-tail-mode").unwrap_or("none")) {
                return Err("PCA residual mode must be none or norm".into());
            }
'''+s[end:];p.write_text(s)
 p=native/'auto_memory.rs'
 replace(p,' if mode!="resident" {return Err("adaptive resident routing requires resident admission".into());}',' if args.optional("--pca-route-dir").is_some() && mode!="resident" {return Err("PCA codes must be resident".into());}')
 replace(p,' let fixed=sum(&[fixed,pca_extra])?;',''' let residual_bytes=if args.optional("--pca-tail-mode")==Some("norm"){
  let dir=args.optional("--pca-route-dir").ok_or("residual mode requires PCA")?;
  crate::pca::field(&crate::pca::meta(std::path::Path::new(dir))?,"count")?.checked_mul(4).ok_or("residual memory overflow")?
 }else{0};
 let fixed=sum(&[fixed,pca_extra,residual_bytes])?;''')
 replace(p,'"pca_extra_reserved_bytes":pca_extra,','"pca_extra_reserved_bytes":pca_extra,"pca_residual_norm_bytes":residual_bytes,')
 rebuild()

def rebuild():
 native=WORK/'native'
 parent=json.loads((ROOT/'results/04_ours_memory_budget/gist_adaptive_resident/build.json').read_text())
 OUT.mkdir(parents=True,exist_ok=True)
 command=['cargo','build','--release','--offline','--manifest-path',str(native/'Cargo.toml'),'--target-dir',str(ROOT/'src/graph_core/target')]
 with (WORK/'build.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
 shutil.copy2(ROOT/'src/graph_core/target/release'/BIN.name,BIN)
 dump(OUT/'build.json',dict(binary_sha256=sha(BIN),parent_binary_sha256=parent['binary_sha256'],sources={str(p):sha(p)for p in [*native.iterdir(),*(ASSETS/'dependencies').rglob('*')]if p.is_file()}))

if __name__=='__main__':build()
