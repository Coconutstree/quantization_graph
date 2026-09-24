"""Recalibrate isolated cache reuse version; probe every requested low budget.
Diagnostic memory runs may overlap the preserved old sweep: no QPS claims.
"""
import importlib.util,json,time,traceback
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
spec=importlib.util.spec_from_file_location('sweep',HERE.parent/'gist_budget_sweep/run.py');s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
s.WORK=ROOT/'work/ours_memory_budget/gist_budget_cache_reuse'
s.OUT=ROOT/'results/04_ours_memory_budget/gist_budget_cache_reuse'
s.BIN=s.WORK/'ours_gist_budget_cache_reuse'
BUDGETS=[256,384,512,640]
def probe(mib,reference):
 folder=s.OUT/'diagnostics'/f'low_budget_{mib}'
 if (folder/'probe.json').exists():return
 assert not folder.exists(), 'Preserve unclassified failed attempt'
 folder.mkdir(parents=True);(folder/'memory_stats').mkdir()
 # Small fixed routing cache checks implementation feasibility independently of admission estimate.
 cmd=s.command(folder,'calibration',mib*s.MIB,'paged',0,[100,300,580],cap=64*4352,calibration=True)
 s.progress(f'low_budget_probe_{mib}')
 try:
  s.pilot.precondition(cmd,folder)
  mem=s.pilot.measurement.measure(cmd,folder,budget=mib*s.MIB,timeout=900)
  s.verify(folder,'calibration',[100,300,580],reference,True,mem)
  status='passed_three_widths_exact_parity'
 except Exception as e:
  log=(folder/'terminal.log').read_text() if (folder/'terminal.log').exists() else ''
  mem=json.loads((folder/'memory_measurement.json').read_text()) if (folder/'memory_measurement.json').exists() else {}
  status='timeout' if mem.get('timed_out') else 'memory_or_thread_resource_failure' if any(x in log for x in ['std::bad_alloc','memory allocation','Cannot allocate memory','os error 12','Resource temporarily unavailable']) else 'setup_failure_cause_not_exposed' if 'worker failed during setup/warmup' in log else 'unexpected_failure'
  s.dump(folder/'failure.json',dict(status=status,error=str(e),traceback=traceback.format_exc(),no_retry=True))
  if status=='unexpected_failure':raise
 s.dump(folder/'probe.json',dict(status=status,budget_mib=mib,widths=[100,300,580],queries_per_width=100,warmup_per_width=100,workers=32,cache_pages=64,binary_sha256=s.sha(s.BIN),performance_claim=False))
def main():
 s.OUT.mkdir(parents=True,exist_ok=True)
 build=json.loads((s.WORK/'build.json').read_text());assert s.sha(s.BIN)==build['binary_sha256']
 for p,h in build['generated_sources'].items():assert s.sha(p)==h
 s.dump(s.OUT/'build.json',build)
 try:
  config=s.calibrate();s.preflight(config)
  reference=s.OUT/'calibration/resident'
  # Validate auto where admitted; probe rejected budgets explicitly.
  for mib in BUDGETS:
   status=json.loads((s.OUT/'preflight'/str(mib)/'status.json').read_text())['status']
   if status=='admitted':
    folder=s.OUT/'auto_checks'/f'budget_{mib}'
    s.execute(folder,'calibration',mib*s.MIB,'auto',1,[100,300,580],reference=reference)
    note=s.OUT/'diagnostics'/f'low_budget_{mib}'/'probe.json'
    if not note.exists():s.dump(note,dict(status='not_run_auto_branch_already_verified',budget_mib=mib,source_acceptance=str(folder/'acceptance.json'),reason='Selected auto policy already passed; no redundant 64-page diagnostic',performance_claim=False))
   else:probe(mib,reference)
  s.dump(s.OUT/'state.json',dict(status='completed',scope='calibration and low-budget correctness/memory validation; no performance claim',updated=time.time()))
 except BaseException as e:
  s.dump(s.OUT/'state.json',dict(status='failed',error=str(e),updated=time.time()));raise
if __name__=='__main__':main()
