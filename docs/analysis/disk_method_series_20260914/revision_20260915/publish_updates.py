from pathlib import Path
import json,subprocess,xml.etree.ElementTree as ET,re
out=Path('docs/analysis/disk_method_series_20260914');rev=out/'revision_20260915'
for item in json.loads((out/'manifest.json').read_text())[1:]:
 key=item['key'];target=rev/(key+'_update.xml');old=json.loads((rev/(key+'_before.json')).read_text());revision=old['data']['document']['revision_id']
 r=subprocess.run(['lark-cli','docs','+update','--as','user','--doc',item['document_id'],'--command','overwrite','--revision-id',str(revision),'--doc-format','xml','--content','@./'+str(target)],capture_output=True,text=True)
 (rev/(key+'_update_result.json')).write_text(r.stdout);(rev/(key+'_update_stderr.txt')).write_text(r.stderr)
 print(key,'update exit',r.returncode,flush=True)
 v=subprocess.run(['lark-cli','docs','+fetch','--as','user','--doc',item['document_id'],'--detail','with-ids','--doc-format','xml'],capture_output=True,text=True)
 (rev/(key+'_after.json')).write_text(v.stdout)
 if v.returncode:print('Fetch failed',v.stderr,flush=True);break
 result=json.loads(v.stdout);actual=ET.fromstring('<root>'+result['data']['document']['content']+'</root>');expected=ET.fromstring('<root>'+target.read_text()+'</root>')
 def norm(root):return re.sub(r'\s+','',''.join(root.itertext()))
 assert norm(actual)==norm(expected),f'{key}: content does not match; inspect before retrying'
 for t in ['img','latex','table','h1','h2']:assert len(list(actual.iter(t)))==len(list(expected.iter(t))),(key,t)
 print(key,'verified two parts, text, images and equations',flush=True)
