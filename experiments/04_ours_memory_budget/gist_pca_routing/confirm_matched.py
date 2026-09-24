"""Predeclared second comparison: match baseline L100 validation Recall."""
import json,time
from prepare import OUT,dump,sha
import run as r
def main():
 r.setup();rule=json.loads((OUT/'matched_protocol.json').read_text());target=rule['validation_target'];candidates=[]
 for dim in (960,128,256,512):
  options=[]
  for p in sorted((OUT/'tune').glob(f'd{dim}_k*_grid_r1/acceptance.json')):
   keep=int(p.parent.name.split('_')[1][1:])
   for x in json.loads((p.parent/'result.json').read_text())['summary_rows']:
    if x['recall']>=target:options.append(dict(dim=dim,keep=keep,width=x['search_width'],recall=x['recall'],qps=x['qps'],source=str(p.parent)))
  candidates.append(dict(eligible=True,**max(options,key=lambda x:x['qps']))if options else dict(dim=dim,eligible=False))
 dump(OUT/'matched_selection_lock.json',dict(target_recall=target,candidates=candidates,binary_sha256=sha(r.r.s.BIN),selected_before_test=True,locked_at=time.time(),rule_created_before_any_test=rule['created_at']))
 for rep in (1,2):
  for x in candidates if rep==1 else list(reversed(candidates)):
   if x['eligible']:r.execute(x['dim'],x['keep'],widths=(x['width'],),split='test',rep=rep,tag='_matched')
 dump(OUT/'state.json',dict(status='completed',matched_confirmation=True,updated=time.time()))
if __name__=='__main__':main()
