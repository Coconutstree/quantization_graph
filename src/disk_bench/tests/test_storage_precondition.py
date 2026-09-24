"""Search-file mapping and real O_DIRECT preparation regression coverage."""
import sys,tempfile,struct,json,hashlib,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from storage_precondition import search_files,prepare_search

class StoragePreparationTests(unittest.TestCase):
    def test_files_direct_read_and_failure(self):
        with tempfile.TemporaryDirectory(prefix='storage_protocol_test_') as tmp:
         root=Path(tmp).resolve()
         names=['shared_graph.pages','ours_full4_residual.pages','graph_compact.pages','residual.pages','node_rows.pages','graph.pages','lvq4.pages','sq4u_codes.pages','diskann_pq_R64_L400_A1.2_disk.index','partition.index']
         for n in names:(root/n).write_bytes(b'x'*8193)
         expected={'Ours-Disk':['shared_graph.pages','ours_full4_residual.pages'],'SymphonyQG-DiskPort':['node_rows.pages'],'OG-LVQ-DiskPort':['graph.pages','lvq4.pages'],'Glass-NSG-DiskPort':['graph.pages','sq4u_codes.pages'],'DiskANN-PQ-Disk':['diskann_pq_R64_L400_A1.2_disk.index']}
         for method,names in expected.items():
          cmd=['binary','--method',method,'--disk-index-dir',str(root)]
          assert [p.name for p in search_files(cmd)]==names
          r=prepare_search(cmd,root/(method+'.json'));assert r['status']=='completed' and r['total_bytes']==8193*len(names)
          assert all(x['sha256']==hashlib.sha256(b'x'*8193).hexdigest()for x in r['files'])
         cmd=['binary','--method','Ours-Disk','--disk-index-dir',str(root),'--locality-layout-dir',str(root)]
         assert [p.name for p in search_files(cmd)]==['graph_compact.pages','residual.pages']
         assert search_files(['search_disk_index','--disk_file_path',str(root/'partition.index')],'Starling-Disk')==[root/'partition.index']
         for reorder in [0,1]:
          for rearranged in [0,1]:
           h=bytearray(4096);struct.pack_into('<Q',h,64,reorder);struct.pack_into('<Q',h,88+24*reorder,rearranged)
           (root/'index_disk.index').write_bytes(h)
           tail='_pq_compressed_rearranged.bin' if rearranged else '_pq_compressed.bin'
           (root/('index'+tail)).write_bytes(b'q'*8193)
           cmd=['search_disk_index','--index_path_prefix',str(root/'index'),'--use_aisaq']
           assert search_files(cmd,'AiSAQ-Disk')==[root/'index_disk.index',root/('index'+tail)]
         with patch('storage_precondition.os.open',side_effect=OSError('simulated unsupported direct IO')):
          try:prepare_search(['b','--method','Ours-Disk','--disk-index-dir',str(root)],root/'failure.json')
          except OSError:pass
          else:raise AssertionError('must fail closed')
         assert json.loads((root/'failure.json').read_text())['status']=='failed'

if __name__=='__main__': unittest.main()
