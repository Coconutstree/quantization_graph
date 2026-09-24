"""Build an isolated native fork, keeping original graphs/payloads immutable."""
import json, shutil, subprocess
from pathlib import Path
from prepare import ROOT, WORK, HERE, OUT, dump, sha

def replace(path, old, new, count=1):
    s=path.read_text();assert s.count(old)>=count,(path,old)
    path.write_text(s.replace(old,new,count))

def main():
    native=WORK/'native'
    if not native.exists():
        shutil.copytree(ROOT/'work/ours_memory_budget/gist_route_locality/native',native)
        p=native/'Cargo.toml'
        replace(p,'ours-gist-route-locality','ours-gist-pca-budget')
        replace(p,'ours_gist_route_locality','ours_gist_pca_budget')
        p=native/'native_main.rs'
        replace(p,'mod auto_memory;','mod auto_memory;\nmod pca;')
        replace(p,'fn main() {','fn main() {\n    let a=std::env::args().collect::<Vec<_>>();\n    if a.get(1).map(String::as_str)==Some("--pca-export"){crate::pca::export(Path::new(&a[2])).unwrap();return;}')
        replace(p,'    let mut header = [0u8; 24];','''    if let Some(dir)=args.optional("--pca-route-dir") {
        let dir=Path::new(dir);let m=crate::pca::meta(dir)?;
        let n=crate::pca::field(&m,"count")?;let d=crate::pca::field(&m,"original_dim")?;
        if n!=meta.base_count || d!=meta.dimension{return Err("PCA preflight dimension mismatch".into());}
        let cs=crate::pca::field(&m,"code_stride")?;let fs=crate::pca::field(&m,"factor_stride")?;
        let len=std::fs::metadata(dir.join("sidecar.bin")).map_err(err)?.len();
        if len!=(24+n*(cs+fs))as u64{return Err("PCA sidecar length mismatch".into());}
        return crate::auto_memory::routing(args,(n*cs)as u64,(n*(fs+4))as u64,0,(cs+fs)as u64,args.number("--workers")?,0);
    }
    let mut header = [0u8; 24];''')
        replace(p,'            let mut codec = OursResidentCodec::load_planned(\n                &files.ours_metadata, &files.ours_sidecar, route, routing_plan).map_err(err)?;', '''            if args.optional("--pca-route-dir").is_some() && (route.is_some()||code_bfs){return Err("PCA must use original index metadata without adaptive route".into());}
            let mut codec = OursResidentCodec::load_planned_pca(
                &files.ours_metadata, &files.ours_sidecar, route, routing_plan,
                args.optional("--pca-route-dir").map(Path::new),args.optional("--pca-tail-mode").unwrap_or("stat3")).map_err(err)?;''')
        replace(p,'let mut f=File::open(&files.ours_sidecar).map_err(err)?;', 'let routing_sidecar=args.optional("--pca-route-dir").map(|p|Path::new(p).join("sidecar.bin")).unwrap_or(files.ours_sidecar.clone());\n                let mut f=File::open(routing_sidecar).map_err(err)?;')
        replace(p,'            codec.route_keep = keep;', '''            if codec.pca.is_some() {
                if let Some(r)=codec.routing.as_mut(){r.code_layout=Some(codec.locality.as_ref().ok_or("PCA paging requires BFS layout")?.clone());}
            }
            codec.route_keep = keep;''')
        p=native/'auto_memory.rs'
        replace(p,' let scratch=usize::try_from(stride', ''' let pca_extra=if let Some(dir)=args.optional("--pca-route-dir"){crate::pca::extra_bytes(&crate::pca::meta(std::path::Path::new(dir))?)?}else{0};
 let fixed=sum(&[fixed,pca_extra])?;
 let scratch=usize::try_from(stride''')
        replace(p,'"centroid_bytes":centroid,','"centroid_bytes":centroid,"pca_extra_reserved_bytes":pca_extra,')
        p=native/'ours_port.rs'
        replace(p,'pub struct OursResidentCodec {','pub struct OursResidentCodec {\n    pub pca: Option<crate::pca::PcaRoute>,')
        anchor='''        let mut meta = BufReader::new(File::open(metadata_path)?);'''
        replace(p,anchor,'''        Self::load_planned_pca(metadata_path,sidecar_path,route,plan,None,"none")
    }
    pub fn load_planned_pca(metadata_path:&Path,sidecar_path:&Path,route:Option<(&Path,usize)>,plan:super::routing::RoutingPlan,pca_dir:Option<&Path>,tail_mode:&str)->ANNResult<Self>{
        let mut meta = BufReader::new(File::open(metadata_path)?);''')
        replace(p,'                adaptive: Some(adaptive),','                adaptive: Some(adaptive),\n                pca: None,')
        replace(p,'        let mut sidecar = BufReader::new(File::open(sidecar_path)?);','''        let pca=pca_dir.map(|p|crate::pca::PcaRoute::load(p,record_count,dim).map_err(ann_error)).transpose()?;
        let (msb_bytes,factor_bytes)=pca.as_ref().map_or((msb_bytes,factor_bytes),|p|(p.space.paper_msb_code_bytes(),p.space.paper_factor_bytes()));
        let pca_sidecar=pca_dir.map(|p|p.join(if matches!(plan,super::routing::RoutingPlan::Resident){"sidecar.bin"}else{"sidecar_bfs.bin"}));
        let sidecar_path=pca_sidecar.as_deref().unwrap_or(sidecar_path);
        let mut sidecar = BufReader::new(File::open(sidecar_path)?);'''.replace('::load(p,record_count,dim)','::load(p,record_count,dim,tail_mode)'))
        replace(p,'            adaptive: None,','            adaptive: None,\n            pca,')
        replace(p,'        self.msb.len() + self.factors.len() + self.centroid_bytes','        self.msb.len() + self.factors.len() + self.centroid_bytes + self.pca.as_ref().map_or(0,|p|p.norms.len()*4+p.extra)')
        replace(p,'        self.space.set_query_coarse_codec(codec);','        self.space.set_query_coarse_codec(codec);\n        if let Some(p)=&self.pca{p.configure(codec);}')
        replace(p,'        Ok(OursPreparedQuery {','        Ok(OursPreparedQuery {\n            pca: self.pca.as_ref().map(|p|p.prepare(query).map_err(ann_error)).transpose()?,')
        replace(p,"pub struct OursPreparedQuery<'a> {","pub struct OursPreparedQuery<'a> {\n    pca: Option<crate::pca::Prepared>,")
        start=p.read_text().index('    pub(crate) fn paper_estimates(')
        end=p.read_text().index('\n    fn distances(',start)
        s=p.read_text();section=s[start:end]
        section=section.replace('        let mut output =', '''        let space=self.owner.pca.as_ref().map_or(&self.owner.space,|p|&p.space);
        let computer=self.pca.as_ref().map_or(&self.computer,|p|&p.computer);
        let correct=|output:&mut [RabitqPaperEstimate]|{
            if let(Some(p),Some(q))=(&self.owner.pca,&self.pca){for(&id,e)in ids.iter().zip(output){e.lower_bound+=p.correction(id as usize,q);}}
        };
        let mut output =''')
        section=section.replace('self.owner.space.paper_', 'space.paper_').replace('self.computer.paper_estimate_batch_sidecar','computer.paper_estimate_batch_sidecar')
        section=section.replace('            return Ok(output);','            correct(&mut output);\n            return Ok(output);').replace('        Ok(output)','        correct(&mut output);\n        Ok(output)')
        p.write_text(s[:start]+section+s[end:])
        # Lowdim short_ip belongs to another quantizer and must NEVER feed full4 reuse.
        replace(p,'if ablation.uses_gate() && estimate.valid != 0 {','if codec.pca.is_none() && ablation.uses_gate() && estimate.valid != 0 {')
    shutil.copy2(HERE/'pca.rs',native/'pca.rs')
    p=native/'native_main.rs'
    if '--pca-audit' not in p.read_text():
        replace(p,'    let result=run();','    if a.get(1).map(String::as_str)==Some("--pca-audit"){crate::pca::audit(&a).unwrap();return;}\n    let result=run();')
    replace(p,'unwrap_or("stat3")','unwrap_or("norm")') if 'unwrap_or("stat3")' in p.read_text() else None
    p=native/'ours_port.rs'
    if 'let epsilon=if self.owner.pca' not in p.read_text():
        replace(p,'        let computer=self.pca.as_ref().map_or(&self.computer,|p|&p.computer);',
                '        let computer=self.pca.as_ref().map_or(&self.computer,|p|&p.computer);\n        let epsilon=if self.owner.pca.as_ref().is_some_and(|p|p.tail_mode=="norm"){((space.paper_msb_code_bytes()*8-1)as f32).sqrt()}else{epsilon};')
        replace(p,'e.lower_bound+=p.correction(id as usize,q);',
                'e.lower_bound+=p.correction(id as usize,q); if p.tail_mode=="norm"{let off=id as usize*space.paper_factor_bytes();let xn=f32::from_le_bytes(self.owner.factors[off..off+4].try_into().unwrap());e.lower_bound-=1e-4*(1.+xn+q.projected_norm+p.norms[id as usize]+q.norm);}')
    # Two quantizers coexist per worker. The inherited C++ singleton would alias
    # their prepared states; use an independent frozen dependency copy.
    dependencies=WORK/'dependencies'
    original=ROOT/'work/ours_memory_budget/gist_budget_cache_reuse/dependencies'
    if not dependencies.exists():
        shutil.copytree(original,dependencies)
        h=dependencies/'Ours/core/hnswlib/space_rabitq.h'
        replace(h,'        thread_local PreparedQuery prepared_storage;\n        PreparedQuery *prepared = &prepared_storage;',
                '        thread_local std::unordered_map<const RaBitQSpace *, PreparedQuery> prepared_storage_by_space;\n        PreparedQuery *prepared = &prepared_storage_by_space[this];')
        if '#include <unordered_map>' not in h.read_text():h.write_text('#include <unordered_map>\n'+h.read_text())
        p=native/'Cargo.toml';replace(p,str(original/'src/graph_core'),str(dependencies/'src/graph_core'))
    with (WORK/'build.log').open('w') as log:
        subprocess.run(['cargo','build','--release','--offline','--manifest-path',str(native/'Cargo.toml'),'--target-dir',str(ROOT/'src/graph_core/target')],stdout=log,stderr=subprocess.STDOUT,check=True)
    binary=WORK/'ours_gist_pca_budget';shutil.copy2(ROOT/'src/graph_core/target/release'/binary.name,binary)
    dump(OUT/'build.json',dict(binary_sha256=sha(binary),sources={str(p):sha(p) for p in [*native.iterdir(),*dependencies.rglob('*')] if p.is_file()}))
if __name__=='__main__':main()
