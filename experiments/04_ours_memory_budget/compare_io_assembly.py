import subprocess,re,json
from pathlib import Path
root=Path('/home/kai3/coco/quantization_graph');out=root/'results/04_ours_memory_budget/baseline_diagnosis_20260919'
old=root/'results/diagnostics/03_disk_system/runtime_test_L_400_w_32/agnews_acceptance_20260912/binaries/qgraph05_shared_graph_port';new=root/'work/ours_memory_budget/v2/bin/ours_memory_budget_v2'
def code(path,start,size):
 text=subprocess.check_output(['objdump','-d','--no-show-raw-insn',f'--start-address={start}',f'--stop-address={start+size}',str(path)],text=True)
 lines=[]
 for line in text.splitlines():
  m=re.match(r'\s*[0-9a-f]+:\s+(.*)',line)
  if not m:continue
  ins=m[1].split('#')[0].strip()
  ins=re.sub(r'-?0x[0-9a-f]+\(%rip\)','RELOC(%rip)',ins)
  ins=re.sub(r'\b[0-9a-f]+\s+(<.*>)',r'\1',ins)
  lines.append(ins)
 return lines
result={}
for label,a,b,size in [('read_pages',0x1bf880,0x1f1f70,0xa6f),('constructor',0x1bf510,0x1f1c00,0x363),('bridge',0x1c26d0,0x1f4dc0,0x2cc)]:
 x,y=code(old,a,size),code(new,b,size)
 (out/f'{label}_old.asm').write_text('\n'.join(x));(out/f'{label}_new.asm').write_text('\n'.join(y))
 result[label]={'same_normalized_instructions':x==y,'instruction_counts':[len(x),len(y)],'normalization':'remove load addresses, RIP relocation displacements and comments; retain symbolic targets and instruction operands'}
print(json.dumps(result,indent=2));(out/'io_assembly_comparison.json').write_text(json.dumps(result,indent=2))
