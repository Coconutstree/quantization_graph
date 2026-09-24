"""Regenerate the final report from accepted runs after the supervisor exits."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT/'results/04_ours_memory_budget/gist_width40_test100'
pid = int(sys.argv[1])
while Path(f'/proc/{pid}').exists():
    # A reparented finished supervisor may briefly remain a zombie.
    stat = Path(f'/proc/{pid}/stat')
    try:
        if stat.read_text().split(') ',1)[1].startswith('Z'): break
    except FileNotFoundError:
        break
    time.sleep(15)
subprocess.run([sys.executable,str(HERE/'report.py')],check=True)
pdf=OUT/'figures/memory_curves.pdf'
if pdf.exists():
    with (OUT/'validation/pdf_text_audit.log').open('w') as log:
        result=subprocess.run([sys.executable,'/home/kai3/.agents/skills/nature-figure/scripts/audit_pdf_text.py',str(pdf)],stdout=log,stderr=subprocess.STDOUT)
    (OUT/'validation/final_report.json').write_text(json.dumps(dict(pdf_text_exit_code=result.returncode,
        source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob('*.py')},
        experiment_state=json.loads((OUT/'state.json').read_text()),full800_enabled=False),indent=2)+'\n')
