"""Resume targeted Feishu edits, verifying full text and never blindly retrying writes."""
from pathlib import Path
import copy, json, subprocess, xml.etree.ElementTree as ET, re
P=Path(__file__).resolve().parent;REPO=P.parents[2];DOC='BpvBdhAILonRVkxT0Jicnwf4nfH'
def invoke(args,path):
 r=subprocess.run(['lark-cli',*args],cwd=REPO,capture_output=True,text=True)
 path.write_text(r.stdout or r.stderr)
 if r.returncode:raise RuntimeError(f'CLI failed: {path.name}: '+r.stderr[:250])
 d=json.loads(r.stdout)
 if not d.get('ok'):raise RuntimeError(f'CLI not ok: {path.name}')
 return d

def fetch(path):
 for attempt in range(4):
  try:return invoke(['docs','+fetch','--as','user','--doc',DOC,'--detail','full','--doc-format','xml'],path.with_name(path.stem+f'_attempt{attempt}.json'))['data']['document']
  except RuntimeError:
   if attempt==3:raise
   print('Read timed out; retrying read',flush=True)
def tree(s):return ET.fromstring('<doc>'+s+'</doc>')
def norm(r):return re.sub(r'\s+','',''.join(r.itertext()))
def apply(root,patch):
 r=copy.deepcopy(root);nodes=list(tree((P/patch['file']).read_text()))
 if patch['command']=='block_replace':
  matches=[e for e in r if e.get('id')==patch['block_id']];assert len(matches)==1
  idx=list(r).index(matches[0]);r.remove(matches[0])
  for n,e in enumerate(nodes):r.insert(idx+n,e)
 else:r.extend(nodes)
 return r
base=json.loads((P/'before.json').read_text())['data']['document'];patches=json.loads((P/'patches.json').read_text());expected=tree(base['content'])
progress=json.loads((P/'publish_progress.json').read_text()) if (P/'publish_progress.json').exists() else []
for patch in patches[:len(progress)]:expected=apply(expected,patch)
current=fetch(P/'resume_before.json')
if norm(tree(current['content']))!=norm(expected):
 if len(progress)<len(patches) and norm(tree(current['content']))==norm(apply(expected,patches[len(progress)])):
  i=len(progress);expected=apply(expected,patches[i]);progress.append({'step':i,'reason':patches[i]['reason'],'revision':current['revision_id'],'body_verified':True,'recovered_ack':True});(P/'publish_progress.json').write_text(json.dumps(progress,ensure_ascii=False,indent=2))
 else:raise RuntimeError('Remote differs from verified state; inspect before continuing')
for i in range(len(progress),len(patches)):
 patch=patches[i];trial=apply(expected,patch)
 for attempt in range(4):
  args=['docs','+update','--as','user','--doc',DOC,'--doc-format','xml','--command',patch['command'],'--revision-id',str(current['revision_id']),'--content','@./'+str((P/patch['file']).relative_to(REPO))]
  if patch.get('block_id'):args+=['--block-id',patch['block_id']]
  try:
   response=invoke(args,P/f'update_{i:02d}_attempt{attempt}.json')
   if response.get('data',{}).get('result')!='success' or response.get('data',{}).get('warnings'):raise RuntimeError('Update response requires verification')
  except RuntimeError as e:print(str(e),flush=True)
  current=fetch(P/f'verify_{i:02d}_write{attempt}.json');actual=tree(current['content'])
  if norm(trial)==norm(actual):break
  if norm(expected)!=norm(actual):raise RuntimeError(f'Unexpected remote text at step {i}; stop to inspect')
  if attempt==3:raise RuntimeError(f'Update step {i} repeatedly not applied')
  print('Verified write not applied; retrying same block against current revision',flush=True)
 expected=trial;progress.append({'step':i,'reason':patch['reason'],'revision':current['revision_id'],'body_verified':True});(P/'publish_progress.json').write_text(json.dumps(progress,ensure_ascii=False,indent=2));print(f'{i+1}/{len(patches)} verified; revision={current["revision_id"]}',flush=True)
(P/'after.json').write_text(json.dumps({'ok':True,'data':{'document':current}},ensure_ascii=False,indent=2));(P/'after.xml').write_text(current['content']);print('DONE: all edits and preserved text verified',flush=True)
