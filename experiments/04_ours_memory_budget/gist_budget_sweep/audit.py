"""Audit physical I/O accounting for every accepted run, including record caches."""
import collections,json
import run as sweep

def main():
 checks=[]
 for split in ['pilot','full']:
  for path in sorted((sweep.OUT/split).glob('*/acceptance.json')):
   folder=path.parent;accept=json.loads(path.read_text())
   assert accept['passed'] and accept['binary_sha256']==sweep.sha(sweep.BIN)
   for name,digest in accept['files'].items():assert sweep.sha(folder/name)==digest,(folder,name)
   totals=collections.defaultdict(collections.Counter);keys=set()
   for line in (folder/'queries.jsonl').open():
    row=json.loads(line);key=(row['search_width'],row['query_id'])
    assert key not in keys,(folder,key);keys.add(key)
    for field in ['io_requests','sectors_4k','bytes_read']:totals[row['search_width']][field]+=row[field]
   routing=json.loads((folder/'routing_stats.json').read_text())
   for width,actual in totals.items():
    ops=json.loads((folder/'memory_stats'/f'L{width}.json').read_text())['operations'];rs=routing.get(str(width),{})
    expected={field:sum(v[op] for v in ops.values())+rs.get(rkey,0) for field,op,rkey in [('io_requests','io_requests','reads'),('sectors_4k','read_pages','reads'),('bytes_read','read_bytes','bytes')]}
    assert dict(actual)==expected,(folder,width,actual,expected)
   assert len(keys)==accept['queries']
   checks.append(dict(split=split,run=folder.name,queries=len(keys),widths=len(totals),unique_queries=True,physical_io_accounting_passed=True))
 sweep.dump(sweep.OUT/'validation/physical_io_audit.json',dict(passed=True,groups=checks,measured_queries=sum(r['queries'] for r in checks)))
 print(len(checks),'groups passed physical I/O accounting and trace uniqueness')
if __name__=='__main__':main()
