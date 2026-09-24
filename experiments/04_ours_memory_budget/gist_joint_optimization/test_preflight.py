"""Exercise new joint quota admission through the real native entrypoint."""
import json
import resource
import subprocess
import sys
import run as r


def main():
    cases=[('resident8','resident',8*r.MIB,16*r.MIB,64,True),
           ('paged96','paged',96*r.MIB,16*r.MIB,64,True),
           ('resident96_overcommit','resident',96*r.MIB,16*r.MIB,64,False),
           ('record_below_map','paged',1,16*r.MIB,64,False),
           ('page_too_small','paged',0,4351,64,False),
           ('zero_inflight','paged',0,16*r.MIB,0,False),
           ('scratch_inflight_excess','paged',0,16*r.MIB,65,False),
           ('one_page','paged',0,4352,64,True)]
    output='preflight_tests'
    if '--scheduler' in sys.argv:
        r.s.BIN=r.WORK/'ours_gist_joint_scheduler'
        output='scheduler_preflight_tests'
        cases=[('inflight256','paged',8*r.MIB,16*r.MIB,256,True),
               ('inflight257_rejected','paged',8*r.MIB,16*r.MIB,257,False)]
    results=[]
    for name,mode,record,pages,inflight,ok in cases:
        folder=r.OUT/output/name
        assert not folder.exists(), folder
        folder.mkdir(parents=True)
        f=r.inputs('tune')
        f.update({'--memory-policy':mode,'--record-cache-budget-bytes':str(record),
            '--route-page-budget-bytes':str(pages),'--routing-max-inflight-pages':str(inflight),
            '--search-dram-budget-gib':'0.625','--preflight-only':'1',
            '--auto-plan-output':str(folder/'plan.json')})
        cmd=[str(r.s.BIN)]+[v for k,x in f.items() for v in (k,x)]
        def limits():
            resource.setrlimit(resource.RLIMIT_AS,(640*r.MIB,640*r.MIB))
            resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,preexec_fn=limits,timeout=30)
        (folder/'terminal.log').write_text(p.stdout)
        plan=json.loads((folder/'plan.json').read_text())
        assert (p.returncode==0)==ok and (plan['status']=='admitted')==ok,(name,p.stdout)
        if ok:
            assert plan['optional_cache_bytes']==record
            assert plan['admission_bytes']<=640*r.MIB
            if mode=='paged':
                assert plan['routing_capacity_pages']==pages//4352
                assert plan['max_inflight_pages']==min(inflight,pages//4352)
                expected=plan['fixed_bytes']+plan['factors_bytes']+plan['paging_scratch_bytes']+plan['paging_service_bytes']+plan['routing_capacity_pages']*4352+record
            else: expected=plan['fixed_bytes']+plan['factors_bytes']+plan['codes_bytes']+record
            assert plan['expected_peak_bytes']==expected
        results.append(dict(name=name,expected_admitted=ok,passed=True))
    r.s.dump(r.OUT/output/'results.json',results)
    print(f'{len(results)} joint-quota boundary cases passed')


if __name__=='__main__': main()
