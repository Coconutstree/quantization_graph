"""Replay all original widths with the preserved historical executable, in isolation."""
import json,os,re,threading,time,traceback
from pathlib import Path
from protocol import ROOT,OUT,REFERENCE,WIDTHS,LIMIT,flags,sha
from run import load_measure,read_trace,assert_inputs_unchanged
from diagnose_baseline import OLD,monitor
D=ROOT/'results/04_ours_memory_budget/historical_full_replay_20260919'
FIELDS=['result_ids','recall_at_10','visited_nodes','distance_evaluations','db1_checks','db1_survivors','full4_candidates','sectors_4k','io_requests']

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
    text=['# 历史原程序完整40档复现','',f"状态：**{state['status']}**；进度以[state.json](state.json)为准。",'',
          '原历史二进制、原40档L及其顺序、800查询、100预热、32线程、beam1、2 GiB限制。仅迁移已验哈希的输入路径及独立输出路径。没有加入缓存钩子、没有改布局、没有主动清设备/系统缓存。', '',
          '目的：检验L100前27档扫描带来的预热，是否足以在当前环境重现历史109.30 QPS。单次复现不能控制历史RAID缓存、外部负载或设备状态。','']
    if comparisons:
        text += ['| L | Recall | 历史QPS | 本次原程序QPS | 本次/历史 | 本次I/O等待ms/查询 |','|---|---:|---:|---:|---:|---:|']
        for r in comparisons:text.append(f"| {r['L']} | {r['recall']:.6f} | {r['historical_qps']:.2f} | {r['replay_qps']:.2f} | {r['ratio']:.3f} | {r['io_wait_ms']:.2f} |")
        r=next(r for r in comparisons if r['L']==100)
        text += ['',f"L100实测{r['replay_qps']:.2f} QPS，为历史的{100*r['ratio']:.1f}%。这是连续扫描27个先前档位后的结果。",'',
                 '若仍约10 QPS，说明本次相同前序不足以恢复历史速度；不能因此断言所有设备缓存因素都已排除。若明显恢复，则前序/运行状态是重要线索，但仍需隔离验证才能作单因素因果解释。']
    if state.get('error'):text+=['','失败原因：'+state['error']]
    text+=['','同目录保存command.json、diagnostic_protocol.json、queries.jsonl、result.json、memory_measurement.json、host_samples.jsonl、parity.json及comparison.json。只有全部配置、内存、结果一致性检查通过才标记completed。','']
    (D/'report.md').write_text('\n'.join(text))

def main():
    D.mkdir(exist_ok=False)
    state={'pid':os.getpid(),'status':'preflight','started_unix':time.time(),'completed_widths':[]}
    save(state);render(state)
    comparisons=None
    try:
        assert sha(OLD)=='a64dfc64e5f351aa48e5d929d8413143935aa0b6079046402d3a04a2f826c92c'
        v=json.loads((OUT/'verification.json').read_text());assert_inputs_unchanged(v)
        historical=json.loads((REFERENCE/'command.json').read_text());original=flags(historical);args=original.copy();args.update(v['resolved_paths'])
        # Preserve run-id and implementation metadata; only actual output paths change.
        args.update({'--result-json':str(D/'result.json'),'--query-trace':str(D/'queries.jsonl')})
        allowed=set(v['resolved_paths'])|{'--result-json','--query-trace'}
        assert all(args[k]==value for k,value in original.items() if k not in allowed)
        assert list(map(int,args['--integration-widths'].split(',')))==list(WIDTHS)
        command=[str(OLD)]+[x for k,value in args.items() for x in (k,value)]
        protocol={'historical_binary_sha256':sha(OLD),'original_command_sha256':sha(REFERENCE/'command.json'),'changed_arguments':{k:{'old':original.get(k),'new':args[k]}for k in args if args[k]!=original.get(k)},'widths':WIDTHS,'diagnostic_only':True,'input_hashes':v['input_hashes'],'affinity':sorted(os.sched_getaffinity(0))}
        (D/'diagnostic_protocol.json').write_text(json.dumps(protocol,indent=2))
        state['status']='running';save(state);render(state)
        stop=threading.Event();threads=[threading.Thread(target=monitor,args=(stop,D/'host_samples.jsonl')),threading.Thread(target=progress,args=(stop,state))]
        for t in threads:t.start()
        try:mem=load_measure().measure(command,D,budget=LIMIT,timeout=86400)
        finally:
            stop.set()
            for t in threads:t.join()
        assert mem['user_address_space_budget_passed']
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

if __name__=='__main__':main()
