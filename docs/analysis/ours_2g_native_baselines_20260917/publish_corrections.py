"""Apply reviewed Feishu block patches, with revision guards and read-back checks."""
from pathlib import Path
import copy,json,re,subprocess,sys,xml.etree.ElementTree as E
P=Path(__file__).resolve().parent;REPO=P.parents[2]
name=sys.argv[1]
DOC={'agnews':'AhoKdQxEBogmWoxfk6xcxHKjnsg','plan':'BpvBdhAILonRVkxT0Jicnwf4nfH'}[name.removesuffix('_single')]
def invoke(args,path):
 r=subprocess.run(['lark-cli',*args],cwd=REPO,capture_output=True,text=True)
 path.write_text(r.stdout or r.stderr)
 if r.returncode:raise RuntimeError(f'CLI failed: {path.name}: '+r.stderr[:180])
 d=json.loads(r.stdout)
 if not d.get('ok'):raise RuntimeError(f'CLI not ok: {path.name}')
 return d

def fetch(label):
 for n in range(4):
  try:return invoke(['docs','+fetch','--as','user','--doc',DOC,'--detail','full','--doc-format','xml'],P/f'{name}_{label}_read{n}.json')['data']['document']
  except RuntimeError:
   if n==3:raise

def tree(s):return E.fromstring('<doc>'+s+'</doc>')
def norm(r):return re.sub(r'\s+','',''.join(r.itertext()))
def resources(r):return [(e.tag,dict(e.attrib)) for e in r.iter() if e.tag in ('img','whiteboard','source','sheet','bitable')]
def apply(root,patch):
 r=copy.deepcopy(root);nodes=list(tree((P/patch['file']).read_text()))
 if patch['command']=='block_replace':
  matches=[(parent,e) for parent in r.iter() for e in parent if e.get('id')==patch['block_id']]
  assert len(matches)==1,patch
  parent,old=matches[0];idx=list(parent).index(old);parent.remove(old)
  for n,e in enumerate(nodes):parent.insert(idx+n,e)
 else:r.extend(nodes)
 return r
base=json.loads((P/f'{name}_before.json').read_text())['data']['document']
patches=json.loads((P/f'{name}_patches.json').read_text());expected=tree(base['content']);original_resources=resources(expected)
progressfile=P/f'{name}_progress.json';progress=json.loads(progressfile.read_text()) if progressfile.exists() else []
for patch in patches[:len(progress)]:expected=apply(expected,patch)
current=fetch('resume')
if norm(tree(current['content']))!=norm(expected):
 if len(progress)<len(patches) and norm(tree(current['content']))==norm(apply(expected,patches[len(progress)])):
  i=len(progress);expected=apply(expected,patches[i]);progress.append({'step':i,'revision':current['revision_id'],'verified':True,'recovered_ack':True});progressfile.write_text(json.dumps(progress,indent=2))
 else:raise RuntimeError('Remote changed; inspect before applying patches')
for i in range(len(progress),len(patches)):
 patch=patches[i];trial=apply(expected,patch)
 for attempt in range(4):
  args=['docs','+update','--as','user','--doc',DOC,'--doc-format','xml','--command',patch['command'],'--revision-id',str(current['revision_id']),'--content','@./'+str((P/patch['file']).relative_to(REPO))]
  if patch.get('block_id'):args+=['--block-id',patch['block_id']]
  try:
   d=invoke(args,P/f'{name}_update{i:02d}_{attempt}.json')
   if d.get('data',{}).get('result')!='success' or d.get('data',{}).get('warnings'):print('Write response needs inspection',flush=True)
  except RuntimeError as exc:print(str(exc),flush=True)
  current=fetch(f'verify{i:02d}_{attempt}');actual=tree(current['content'])
  if resources(actual)!=original_resources:raise RuntimeError('Protected resource attributes changed')
  if norm(actual)==norm(trial):break
  if norm(actual)!=norm(expected):raise RuntimeError(f'Unexpected body after patch {i}; inspect before continuing')
  if attempt==3:raise RuntimeError('Write did not apply')
 expected=trial;progress.append({'step':i,'revision':current['revision_id'],'verified':True});progressfile.write_text(json.dumps(progress,indent=2));print(f'{name} {i+1}/{len(patches)} verified revision {current["revision_id"]}',flush=True)
(P/f'{name}_after.json').write_text(json.dumps({'ok':True,'data':{'document':current}},ensure_ascii=False,indent=2));(P/f'{name}_after.xml').write_text(current['content'])
print('DONE: body and protected resources verified',flush=True)
