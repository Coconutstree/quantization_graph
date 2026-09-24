"""Read-only verification of actual layout files and migration records."""
import json,hashlib,subprocess
from pathlib import Path
from protocol import ROOT,OUT,REFERENCE,flags
D=ROOT/'results/04_ours_memory_budget/baseline_diagnosis_20260919'
def digest(p):
 with p.open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def main():
 a=flags(json.loads((REFERENCE/'command.json').read_text()));b=flags(json.loads((OUT/'baseline/command.json').read_text()))
 layout=Path(a['--locality-layout-dir']);assert a['--locality-layout-dir']==b['--locality-layout-dir']
 snapshot=json.loads((REFERENCE/'storage_protocol.json').read_text())['input_snapshot'];files={}
 for name,key in [('graph_compact.pages','--locality-combined-sha256'),('residual.pages','--locality-residual-sha256'),('id_to_slot.u32','--locality-mapping-sha256')]:
  p=layout/name;st=p.stat();history=snapshot[str(p)];h=digest(p)
  symlinks=[str(x) for x in [p,*p.parents]if x.is_symlink()]
  files[name]={'path':str(p),'realpath':str(p.resolve()),'sha256':h,'hash_matches_historical_and_v2':h==a[key]==b[key],'inode':st.st_ino,'same_historical_inode':st.st_ino==history['inode'],'mtime_ns':st.st_mtime_ns,'same_historical_mtime':st.st_mtime_ns==history['mtime_ns'],'size':st.st_size,'device':st.st_dev,'symlink_components':symlinks}
  assert files[name]['hash_matches_historical_and_v2'] and files[name]['same_historical_inode'] and files[name]['same_historical_mtime']
 migration=json.loads((ROOT/'results/manifests/path_migration_20260918.json').read_text())
 matching=[v for v in migration['moves']if str(v['old']).startswith('work/') or str(v['new']).startswith('work/')]
 extents=subprocess.check_output(['filefrag','-v',str(layout/'graph_compact.pages'),str(layout/'residual.pages')],text=True)
 (D/'layout_current_extents.txt').write_text(extents)
 preflight=ROOT/'results/archive/native_runs/runs/disk_03_graph_build_reuse_20260910_gist/manifests/preflight.json'
 fio=preflight.with_name('fio_preflight.json')
 result={'same_command_layout_path':True,'same_disk_index_path':a['--disk-index-dir']==b['--disk-index-dir'],'files':files,'migration_work_entries':matching,'historical_preflight_source':str(preflight.relative_to(ROOT)),'historical_preflight':json.loads(preflight.read_text()),'historical_fio':json.loads(fio.read_text()),'current_mount':json.loads((D/'layout_current_mount.json').read_text()),'limits':'No 2026-09-12 filesystem UUID, physical extent map or RAID physical-disk mapping snapshot available; inode and mtime do not prove unchanged physical block placement.'}
 (D/'layout_location_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
 print('PASS: same BFS path, page hashes, inode and mtime; no work/ layout relocation in migration manifest.');print(extents)
if __name__=='__main__':main()
