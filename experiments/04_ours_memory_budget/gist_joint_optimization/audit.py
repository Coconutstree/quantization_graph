"""Final artifact audit; refuses partial matrices or altered accepted evidence."""
import hashlib
import json
import struct
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'results/04_ours_memory_budget/gist_joint_optimization'
WORK=ROOT/'work/ours_memory_budget/gist_joint_optimization'


def read(p):return json.loads(p.read_text())


def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    for stage in ['validation','allocation','scheduler','confirmation','low_budget']:
        assert read(OUT/f'{stage}_completed.json')['passed']
    build_hashes=set()
    experiment=read(OUT/'experiment.json')
    training=ROOT/'results/04_ours_memory_budget/hybrid_tuning/splits/train/validation_query.fvecs'
    evaluation=ROOT/'results/04_ours_memory_budget/hybrid_tuning/splits/tune/validation_query.fvecs'
    assert sha(training)==experiment['training_query_sha']
    assert sha(evaluation)==experiment['evaluation_query_sha']
    def vectors(path):
        raw=Path(path).read_bytes();stride=4*(struct.unpack_from('<I',raw)[0]+1)
        assert len(raw)%stride==0
        return set(raw[i:i+stride] for i in range(0,len(raw),stride))
    assert len(vectors(training))==len(vectors(evaluation))==100
    assert not vectors(training)&vectors(evaluation)
    test_command=read(OUT/'test/T0_resident_r0_r1/command.json')
    test_flags=dict(zip(test_command[1::2],test_command[2::2]))
    test_raw=Path(test_flags['--query']).read_bytes()
    test_stride=4*(struct.unpack_from('<I',test_raw)[0]+1)
    assert len(test_raw)==800*test_stride
    # Original test contains repeated vector values; keep all original query IDs.
    test_unique_vectors=len(vectors(test_flags['--query']))
    assert not (vectors(training)|vectors(evaluation))&vectors(test_flags['--query'])
    for name,h in experiment['record_profiles'].items():
        assert sha(ROOT/'results/04_ours_memory_budget/hybrid_tuning/runs/train_profile'/name)==h
    for name,binary in [('build.json','ours_gist_joint_optimization'),('scheduler_build.json','ours_gist_joint_scheduler')]:
        b=read(WORK/name)
        assert sha(WORK/binary)==b['binary_sha256']
        for p,h in b['sources'].items():assert sha(p)==h,p
        build_hashes.add(b['binary_sha256'])
    expected={'tune':38,'test':14}
    queries=0
    routing_sidecars=set()
    graph_hashes=set()
    for split,n in expected.items():
        accepted=list((OUT/split).glob('*/acceptance.json'))
        assert len(accepted)==n,(split,len(accepted),n)
        for p in accepted:
            a=read(p);assert a['passed'] and a['binary_sha256'] in build_hashes
            assert a['query_count']==(800 if split=='test' else 100)
            queries+=a['query_count']
            for f,h in a['files'].items():assert sha(p.parent/f)==h,(p,f)
            cmd=read(p.parent/'command.json');flags=dict(zip(cmd[1::2],cmd[2::2]))
            assert sha(flags['--query'])==flags['--query-split-sha256']
            assert sha(flags['--query-order'])==flags['--query-order-sha256']
            result=read(p.parent/'result.json')
            assert result['dimension']==960 and result['adaptive_route_mode']=='disabled'
            assert result['workers']==32 and len(result['summary_rows'])==1
            assert result['summary_rows'][0]['search_width']==100
            assert result['summary_rows'][0]['beam_width']==1
            assert result['summary_rows'][0]['qps']>0
            graph_hashes.add(result['source_graph_sha256'])
            routing_sidecars.add(read(p.parent/'routing_storage_precondition.json')['sha256'])
            config=read(p.parent/'config.json')
            plan=read(p.parent/'memory_plan.json')
            assert plan['budget_bytes']==config['budget_mib']*2**20
            assert config['budget_mib']==(538 if p.parent.name.startswith('L') else 640)
    assert len(graph_hashes)==len(routing_sidecars)==1
    assert queries==15000
    cases=read(OUT/'preflight_tests/results.json');assert len(cases)==8 and all(r['passed'] for r in cases)
    scheduler_cases=read(OUT/'scheduler_preflight_tests/results.json')
    assert len(scheduler_cases)==2 and all(r['passed'] for r in scheduler_cases)
    regression=(OUT/'routing_regression.log').read_text()
    assert 'test result: ok.' in regression and '0 failed' in regression
    assert 'FAILED' not in regression
    failures=list((OUT/'tune').glob('*/failure.json'))+list((OUT/'test').glob('*/failure.json'))
    assert not failures,failures
    pause=read(OUT/'competing_build.json');assert pause['resumed']>=pause['paused']
    result=dict(passed=True,accepted_runs=52,measurement_queries=queries,full_route_dimension=960,preflight_cases=10,test_rows=800,test_unique_vectors=test_unique_vectors,stages=['joint_allocation','hot_pages','clock_lru','inflight_16_32_64_128_256','heldout_test800','low_budget538'],test_repeats=2,scope='L100 GIST only',report_sha256=sha(OUT/'report.md'))
    (OUT/'final_audit.json').write_text(json.dumps(result,indent=2)+'\n')
    state=read(OUT/'pipeline_state.json')
    state.update(status='completed',final_audit_passed=True,audited_unix=time.time())
    (OUT/'pipeline_state.json').write_text(json.dumps(state,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
