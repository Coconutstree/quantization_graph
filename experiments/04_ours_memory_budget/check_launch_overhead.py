"""After full replay, compare direct launches without proc polling/host sampling."""
import fcntl,json,os,resource,subprocess,time,traceback
from pathlib import Path
from protocol import ROOT,OUT,REFERENCE,LIMIT,flags,sha
from run import read_trace
from diagnose_baseline import OLD,NEW
FULL=ROOT/'results/04_ours_memory_budget/historical_full_replay_20260919'
D=ROOT/'results/04_ours_memory_budget/launch_path_diagnosis_20260919'
FIELDS=['result_ids','recall_at_10','visited_nodes','distance_evaluations','db1_checks','db1_survivors','full4_candidates','sectors_4k','io_requests']

def main():
 D.mkdir(exist_ok=False)
 state={'status':'waiting_for_full_replay','pid':os.getpid(),'started_unix':time.time(),'runs':[]}
 def save():
  state['updated_unix']=time.time();temp=D/'state.json.tmp';temp.write_text(json.dumps(state,indent=2));temp.replace(D/'state.json')
 def render():
  lines=['# baseline启动封装对照','',f"状态：{state['status']}。",'',
   '历史完整40档复现结束后才执行；不与该任务并发。诊断仅L100，原800查询、100预热、32线程、beam1、2 GiB RLIMIT_AS、输入和页布局保留。去掉原测量器每50ms读取/proc和主机每秒采样；直接启动，使用进程退出时的/usr/bin/time资源统计。此试验检查轮询/采样是否造成数量级开销，不用于证明所有启动环境完全一致。','',
   '| 程序 | QPS | 读取路径等待 ms/查询 | 查询一致性 |','|---|---:|---:|---|']
  for r in state['runs']:lines.append(f"| {r['mode']} | {r['qps']:.2f} | {r['io_wait_ms']:.2f} | 通过 |")
  if state.get('error'):lines+=['','错误：'+state['error']]
  (D/'report.md').write_text('\n'.join(lines)+'\n')
 save();render()
 try:
  while True:
   full=json.loads((FULL/'state.json').read_text())
   if full['status']=='completed':break
   if full['status']=='failed':raise RuntimeError('full replay failed; direct tests not started')
   os.kill(full['pid'],0)
   time.sleep(15)
  ref={k:v for k,v in read_trace(REFERENCE/'queries.jsonl').items()if k[0]==100}
  for mode,binary,source in [('new_unwrapped',NEW,OUT/'baseline'),('old_unwrapped',OLD,FULL)]:
   assert sha(binary)==json.loads((source/'command.json').read_text())[json.loads((source/'command.json').read_text()).index('--native-binary-sha256')+1]
   folder=D/mode;folder.mkdir();(folder/'memory_stats').mkdir()
   args=flags(json.loads((source/'command.json').read_text()))
   args.update({'--integration-widths':'100','--result-json':str(folder/'result.json'),'--query-trace':str(folder/'queries.jsonl')})
   if mode.startswith('new'):args['--memory-stats-dir']=str(folder/'memory_stats')
   command=[str(binary)]+[v for k,val in args.items()for v in(k,val)]
   env=os.environ.copy()
   for k in list(env):
    if k.startswith('QG05_') or k in {'LD_PRELOAD','LD_AUDIT'}:del env[k]
   env.update(MALLOC_ARENA_MAX='2',OMP_DYNAMIC='FALSE',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',QG05_MEASURE_WHOLE_PROCESS='1')
   def limits():
    resource.setrlimit(resource.RLIMIT_AS,(LIMIT,LIMIT));resource.setrlimit(resource.RLIMIT_CORE,(0,0))
   (folder/'command.json').write_text(json.dumps(command,indent=2))
   (folder/'launch_protocol.json').write_text(json.dumps({'rlimit_as_bytes':LIMIT,'without_periodic_sampling':True,'environment':{k:env[k]for k in ['MALLOC_ARENA_MAX','OMP_DYNAMIC','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','QG05_MEASURE_WHOLE_PROCESS']},'affinity':sorted(os.sched_getaffinity(0))},indent=2))
   state.update(status='running',current=mode);save();render()
   started=time.time()
   with (folder/'terminal.log').open('w') as log:
    child=subprocess.Popen(['/usr/bin/time','-v','-o',str(folder/'resource_usage.txt'),*command],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,preexec_fn=limits)
    state['child_pid']=child.pid;save();code=child.wait()
   elapsed=time.time()-started
   assert code==0,f'native exit {code}'
   actual=read_trace(folder/'queries.jsonl');assert actual.keys()==ref.keys()
   differences=[(key,k)for key in actual for k in FIELDS if actual[key][k]!=ref[key][k]]
   (folder/'parity.json').write_text(json.dumps({'passed':not differences,'count':len(differences),'first_errors':differences[:20]}))
   assert not differences
   r=json.loads((folder/'result.json').read_text())['summary_rows'];assert len(r)==1 and r[0]['search_width']==100 and r[0]['query_count']==800
   state['runs'].append({'mode':mode,'qps':r[0]['qps'],'io_wait_ms':r[0]['io_wait_us']/1000,'outer_wall_seconds':elapsed})
   save();render()
  state.update(status='completed',current=None);state.pop('child_pid',None)
 except BaseException as error:
  state.update(status='failed',error=str(error));traceback.print_exc();raise
 finally:save();render()
if __name__=='__main__':main()
