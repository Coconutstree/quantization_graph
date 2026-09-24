"""Replay GIST current baseline with an explicit storage pre-read condition."""
import argparse,fcntl,json,os,re,threading,time,traceback
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from protocol import ROOT,OUT,REFERENCE,WIDTHS,LIMIT,flags,sha
from run import load_measure,read_trace,assert_inputs_unchanged
from diagnose_baseline import NEW as OLD,monitor
D=ROOT/'results/diagnostics/03_disk_system/layout_state_control/gist/20260919_direct_current_full'
PRE_READ='none'
FIELDS=['result_ids','recall_at_10','visited_nodes','distance_evaluations','db1_checks','db1_survivors','full4_candidates','sectors_4k','io_requests','query_cache_hits','query_cache_misses','query_cache_evictions','query_cache_allocated_bytes']

def save(state):
    state['updated_unix']=time.time()
    p=D/'state.json.tmp';p.write_text(json.dumps(state,indent=2));p.replace(D/'state.json')

def progress(stop,state):
    offset=0;partial='';counts={}
    while not stop.is_set():
        try:
            p=D/'queries.jsonl'
            if p.exists():
                with p.open() as f:
                    f.seek(offset);chunk=f.read();offset=f.tell()
                lines=(partial+chunk).split('\n');partial=lines.pop()
                for line in lines:
                    r=json.loads(line);w=r['search_width'];counts[w]=counts.get(w,0)+1
            state['completed_widths']=[w for w in WIDTHS if counts.get(w)==800]
            state['recorded_queries']=sum(counts.values())
            p=D/'terminal.log'
            if p.exists():
                matches=re.findall(r'beam=1 width=(\d+)',p.read_text())
                if matches:state['current_width']=int(matches[-1])
            save(state)
        except Exception as error:
            (D/'progress_error.log').open('a').write(str(error)+'\n')
        stop.wait(5)

def render(state,comparisons=None):
    lines=['# 当前 baseline 存储条件对照','',f"状态：{state['status']}。",'', f'原有布局；pre_read={PRE_READ}。direct 为搜索进程启动前顺序直接读取两个主文件；none 不额外预读，不保证冷缓存。输入身份核验仍会普通读取文件。当前 v2 baseline，optional cache=0，原40档顺序、32线程、800测试查询、100预热、2 GiB RLIMIT_AS。预处理在查询计时之外并单独记录。诊断复测，非正式结果。', '', '| L | 历史 QPS | 本次 QPS | 比例 |','|---|---:|---:|---:|']
    for r in comparisons or []:lines.append(f"| {r['L']} | {r['historical_qps']:.3f} | {r['replay_qps']:.3f} | {r['ratio']:.3f} |")
    if state.get('error'):lines+=['',state['error']]
    (D/'report.md').write_text('\n'.join(lines)+'\n')

def main():
    D.mkdir(exist_ok=False)
    state={'pid':os.getpid(),'status':'preflight','started_unix':time.time(),'completed_widths':[]}
    save(state);render(state)
    comparisons=None
    try:
        assert sha(OLD)=='8e5526a71d9ffa960358d251635e07d7c2b0e727130c5ba05944789de0b58637'
        v=json.loads((OUT/'verification.json').read_text());assert_inputs_unchanged(v)
        historical=json.loads((REFERENCE/'command.json').read_text());original=flags(historical)
        from run import make_command
        (D/'memory_stats').mkdir()
        args=flags(make_command('baseline',v,folder=D))
        from storage_condition import prepare_storage
        prepare_storage(Path(args['--locality-layout-dir']), PRE_READ, D/'preparation.json')

        args.update({'--result-json':str(D/'result.json'),'--query-trace':str(D/'queries.jsonl')})
        allowed=set(v['resolved_paths'])|{'--result-json','--query-trace','--run-id','--native-binary-sha256','--implementation-fingerprint'}
        assert all(args[k]==value for k,value in original.items() if k not in allowed)
        assert list(map(int,args['--integration-widths'].split(',')))==list(WIDTHS)
        command=[str(OLD)]+[x for k,value in args.items() for x in (k,value)]
        protocol={'current_binary_sha256':sha(OLD),'original_command_sha256':sha(REFERENCE/'command.json'),'changed_arguments':{k:{'old':original.get(k),'new':args[k]}for k in args if args[k]!=original.get(k)},'widths':WIDTHS,'diagnostic_only':True,'pre_read':PRE_READ,'pre_read_scope':'once_before_search_process_not_each_query_or_width','preparation_file':'preparation.json','input_hashes':v['input_hashes'],'affinity':sorted(os.sched_getaffinity(0))}
        (D/'diagnostic_protocol.json').write_text(json.dumps(protocol,indent=2))
        state['status']='running';save(state);render(state)
        stop=threading.Event();threads=[threading.Thread(target=monitor,args=(stop,D/'host_samples.jsonl')),threading.Thread(target=progress,args=(stop,state))]
        for t in threads:t.start()
        try:mem=load_measure().measure(command,D,budget=LIMIT,timeout=86400)
        finally:
            stop.set()
            for t in threads:t.join()
        assert mem['user_address_space_budget_passed'] and mem['binary_sha256']==sha(OLD)
        actual=read_trace(D/'queries.jsonl');reference=read_trace(REFERENCE/'queries.jsonl')
        assert actual.keys()==reference.keys() and len(actual)==32000
        errors=[(key,f) for key in actual for f in FIELDS if actual[key][f]!=reference[key][f]]
        (D/'parity.json').write_text(json.dumps({'passed':not errors,'count':len(errors),'fields':FIELDS,'first_errors':errors[:20],'queries':len(actual)},indent=2))
        assert not errors,'trace mismatch'
        result=json.loads((D/'result.json').read_text());rows=result['summary_rows']
        assert [(r['search_width'],r['beam_width'],r['query_count'])for r in rows]==[(w,1,800)for w in WIDTHS]
        old={r['search_width']:r for r in json.loads((REFERENCE/'result.json').read_text())['summary_rows']}
        comparisons=[{'L':r['search_width'],'recall':r['recall'],'historical_qps':old[r['search_width']]['qps'],'replay_qps':r['qps'],'ratio':r['qps']/old[r['search_width']]['qps'],'io_wait_ms':r['io_wait_us']/1000}for r in rows]
        (D/'comparison.json').write_text(json.dumps(comparisons,indent=2))
        state.update(status='completed',completed_widths=WIDTHS,recorded_queries=32000)
    except BaseException as error:
        state.update(status='failed',error=str(error));traceback.print_exc();raise
    finally:
        save(state);render(state,comparisons)

def active_searches():
    found=[]
    for path in Path('/proc').glob('[0-9]*/exe'):
        try:
            if Path(os.readlink(path)).name.startswith(('qgraph05_', 'ours_memory_budget')):
                found.append(int(path.parent.name))
        except OSError:
            pass
    return found

def cli():
    global D, PRE_READ
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pre-read',choices=['none','direct'],default='none',help='none: no extra pre-read (not cold-cache); direct: sequential O_DIRECT before all 40 widths')
    parser.add_argument('--output',type=Path,help='Fresh output directory under results/diagnostics')
    parser.add_argument('--execute',action='store_true',help='Execute; otherwise show plan only')
    opts=parser.parse_args();PRE_READ=opts.pre_read
    D=(opts.output or ROOT/'results/diagnostics/03_disk_system/layout_state_control/gist'/('replay_'+PRE_READ+'_'+time.strftime('%Y%m%d_%H%M%S'))).resolve()
    if not D.is_relative_to(ROOT/'results/diagnostics'):parser.error('output must be under results/diagnostics')
    if D.exists():parser.error('refusing to overwrite existing output')
    print(json.dumps({'execute':opts.execute,'pre_read':PRE_READ,'output':str(D),'widths':WIDTHS,'workers':32,'queries':800,'warmup_queries':100,'diagnostic_only':True},indent=2),flush=True)
    if not opts.execute:return
    lockpath=ROOT/'work/storage_recovery_20260919/replay.lock';lockpath.parent.mkdir(parents=True,exist_ok=True)
    with lockpath.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if active_searches():raise RuntimeError('another native search is active')
        D.parent.mkdir(parents=True,exist_ok=True)
        main()

if __name__=='__main__':cli()
