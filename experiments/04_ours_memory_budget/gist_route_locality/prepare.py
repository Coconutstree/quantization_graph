"""Cluster route codes by existing BFS slots; preserve all factors and logical IDs."""
import hashlib, json, shutil, struct, subprocess
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[3]
WORK=ROOT/'work/ours_memory_budget/gist_route_locality'
OUT=ROOT/'results/04_ours_memory_budget/gist_route_locality'
PARENT=ROOT/'work/ours_memory_budget/gist_joint_optimization'
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def main():
 assert not WORK.exists(),'Preserve existing attempt'
 WORK.mkdir(parents=True);OUT.mkdir(parents=True,exist_ok=True)
 cmd=json.loads((ROOT/'results/04_ours_memory_budget/gist_joint_optimization/tune/A3_paged_p16_r0_r1/command.json').read_text());f=dict(zip(cmd[1::2],cmd[2::2]))
 source=Path(f['--disk-index-dir']);mapping=Path(f['--locality-layout-dir'])/'id_to_slot.u32'
 slots=np.fromfile(mapping,dtype='<u4');n=len(slots);assert np.array_equal(np.sort(slots),np.arange(n))
 original=source/'ours_db1_sidecar.bin'
 with original.open('rb') as r:header=r.read(24)
 magic,codes,factors=struct.unpack('<8sQQ',header);stride=codes//n;assert n*stride==codes
 index=WORK/'index';index.mkdir()
 for p in source.iterdir():
  if p.name!='ours_db1_sidecar.bin':(index/p.name).symlink_to(p.resolve(),target_is_directory=p.is_dir())
 target=index/'ours_db1_sidecar.bin'
 old=np.memmap(original,dtype='u1',mode='r',offset=24,shape=(n,stride))
 with target.open('wb') as w:
  w.write(header);w.truncate(24+codes+factors)
 new=np.memmap(target,dtype='u1',mode='r+',offset=24,shape=(n,stride))
 for start in range(0,n,8192):new[slots[start:start+8192]]=old[start:start+8192]
 new.flush()
 for start in range(0,n,8192):assert np.array_equal(new[slots[start:start+8192]],old[start:start+8192])
 with original.open('rb') as r,target.open('r+b') as w:
  r.seek(24+codes);w.seek(24+codes);shutil.copyfileobj(r,w)
 proof=dict(layout='bfs',rows=n,code_stride=stride,codes_bytes=codes,factors_bytes=factors,mapping_sha256=sha(mapping),original_sha256=sha(original),sidecar_sha256=sha(target),all_code_rows_exact=True,additional_resident_mapping_bytes=0)
 (index/'routing_layout.json').write_text(json.dumps(proof,indent=2)+'\n');(OUT/'layout.json').write_text(json.dumps(proof,indent=2)+'\n')
 native=WORK/'native';shutil.copytree(PARENT/'native_scheduler',native)
 p=native/'Cargo.toml';p.write_text(p.read_text().replace('ours-gist-joint-scheduler','ours-gist-route-locality').replace('ours_gist_joint_scheduler','ours_gist_route_locality'))
 p=native/'routing.rs';s=p.read_text();s=s.replace('pub struct RoutingReader {','pub struct RoutingReader {\n    pub code_layout: Option<std::sync::Arc<super::locality::LocalityLayout>>,',1)
 s=s.replace('            profile_pages: 0,','            profile_pages: 0,\n            code_layout: None,',1)
 a='let start = (id as usize)\n                        .checked_mul(stride)'
 b='let physical_id = if is_factor { id as usize } else { self.code_layout.as_ref().map_or(id as usize, |layout| layout.slots[id as usize] as usize) };\n                    let start = physical_id\n                        .checked_mul(stride)'
 assert a in s;s=s.replace(a,b,1);p.write_text(s)
 p=native/'native_main.rs';s=p.read_text();anchor='            let routing_plan=if route.is_none()'
 guard='''            let layout_manifest=Path::new(args.text("--disk-index-dir")?).join("routing_layout.json");
            let code_bfs=args.optional("--routing-code-layout")==Some("bfs");
            if layout_manifest.exists()!=code_bfs {return Err("BFS route sidecar requires matching layout flag and manifest".into());}
            if code_bfs {
                let info:serde_json::Value=serde_json::from_slice(&std::fs::read(&layout_manifest).map_err(err)?).map_err(err)?;
                if info["sidecar_sha256"].as_str()!=Some(sha256(&files.ours_sidecar)?.as_str()) || info["mapping_sha256"].as_str()!=args.optional("--locality-mapping-sha256") {return Err("route layout hash mismatch".into());}
            }
'''
 assert anchor in s;s=s.replace(anchor,guard+anchor,1)
 anchor='            codec.route_keep = keep;'
 attach='''            if code_bfs {
                let layout=codec.locality.as_ref().ok_or("BFS route codes require existing BFS record layout")?.clone();
                codec.routing.as_mut().ok_or("BFS route sidecar is paged-only")?.code_layout=Some(layout);
            }
'''
 assert anchor in s;s=s.replace(anchor,attach+anchor,1);p.write_text(s)
 with (WORK/'build.log').open('w') as log:
  subprocess.run(['cargo','build','--release','--offline','--manifest-path',str(native/'Cargo.toml'),'--target-dir',str(ROOT/'src/graph_core/target')],stdout=log,stderr=subprocess.STDOUT,check=True)
 binary=WORK/'ours_gist_route_locality';shutil.copy2(ROOT/'src/graph_core/target/release'/binary.name,binary)
 (WORK/'build.json').write_text(json.dumps(dict(binary_sha256=sha(binary),sources={str(p):sha(p) for p in native.iterdir() if p.is_file()},layout=proof),indent=2)+'\n')
if __name__=='__main__':main()
