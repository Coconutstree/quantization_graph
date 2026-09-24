"""Read sweep progress without changing logs or interfering with the controller."""
import json,re
from pathlib import Path
root=Path(__file__).resolve().parents[3]/'results/04_ours_memory_budget/gist_budget_sweep'
state=json.loads((root/'state.json').read_text())
state['accepted_groups']={split:len(list((root/split).glob('*/acceptance.json'))) for split in ['pilot','full']}
log=root/state.get('stage','')/'terminal.log'
if log.is_file():
 with log.open('rb') as stream:
  stream.seek(max(0,log.stat().st_size-16384));tail=stream.read().decode(errors='replace')
 widths=re.findall(r'05C Ours-Disk search:.*?width=(\d+)',tail)
 stages=[s for s in tail.splitlines() if s.startswith('memory_stage ')]
 if widths:state['current_L']=int(widths[-1])
 if stages:state['latest_memory_stage']=stages[-1]
print(json.dumps(state,ensure_ascii=False,indent=2))
