"""Summarize ABBA runs and sampled block-device/CPU activity; no performance edits."""
import json,statistics
from pathlib import Path
from protocol import ROOT
D=ROOT/'results/04_ours_memory_budget/baseline_diagnosis_20260919'

def disk(sample):
 return next(list(map(int,line.split()[3:])) for line in sample['diskstats'].splitlines() if line.split()[2]=='sda')
def cpu(sample):
 return list(map(int,sample['stat'].splitlines()[0].split()[1:9]))
def main():
 summaries=json.loads((D/'summary.json').read_text());output=[]
 for r in summaries:
  samples=[json.loads(line)for line in (D/r['run']/'host_samples.jsonl').read_text().splitlines()]
  intervals=[]
  for a,b in zip(samples,samples[1:]):
   duration=b['time']-a['time'];ds=[y-x for x,y in zip(disk(a),disk(b))];cs=[y-x for x,y in zip(cpu(a),cpu(b))]
   if ds[0]/duration<500 or not any('Threads:\t33' in v['status'] for v in b['children'].values()):continue
   intervals.append({'duration':duration,'reads':ds[0],'read_ms':ds[3],'busy_ms':ds[9],'cpu_total':sum(cs),'cpu_idle':cs[3],'cpu_iowait':cs[4]})
  total=lambda key:sum(v[key] for v in intervals)
  row={'run':r['run'],'qps':r['qps'],'recall':r['recall'],'io_wait_ms_per_query':r['io_wait_us']/1000,'pages_per_query':r['sectors_4k_per_query'],'VmPeak_MiB':r['VmPeak']/2**20,
       'active_samples':len(intervals),'sda_reads_per_second':total('reads')/total('duration'),'sda_read_await_ms':total('read_ms')/total('reads'),'sda_busy_percent':total('busy_ms')/total('duration')/10,'host_cpu_idle_percent':100*total('cpu_idle')/total('cpu_total'),'host_cpu_iowait_percent':100*total('cpu_iowait')/total('cpu_total')}
  output.append(row)
 data={'method':'sda aggregate counters; 1 s intervals with >=500 reads/s and a 33-thread child; includes warmup and search, not a pure measured-phase device average','runs':output}
 if len(output)==4:
  old=[r['qps']for r in output if r['run'].endswith('old')];new=[r['qps']for r in output if r['run'].endswith('new')]
  data['old_mean_qps']=statistics.mean(old);data['new_mean_qps']=statistics.mean(new);data['new_over_old']=statistics.mean(new)/statistics.mean(old)
 (D/'diagnosis_metrics.json').write_text(json.dumps(data,indent=2))
 print(json.dumps(data,indent=2))
if __name__=='__main__':main()
