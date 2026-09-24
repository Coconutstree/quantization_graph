"""Deterministic validation-only cache allocation selection and immutable locks."""
import hashlib
import json
import math
from pathlib import Path

WIDTHS=(60,100,180)
STRATEGIES=('graph_first','records_only')
RULE={'name':'validation_cache_selection_v1','widths':list(WIDTHS),'aggregation':'queries_over_total_query_seconds','width_weight':'equal_geometric_mean','records_min_ratio':1.05,'both_rounds_faster':True,'fallback':'graph_first'}

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def choose(runs):
    good={}
    for s in STRATEGIES:
        rr=[r for r in runs if r['strategy']==s]
        if len(rr)!=2 or sorted(r['round'] for r in rr)!=[0,1]:raise ValueError('require two attempts per strategy')
        if all(r['status']=='passed' for r in rr):
            for r in rr:
                if sorted(p['search_width'] for p in r['points'])!=list(WIDTHS):raise ValueError('width coverage')
                if any(not math.isfinite(p['qps']) or p['qps']<=0 or p['query_count']!=200 for p in r['points']):raise ValueError('invalid validation points')
            good[s]=sorted(rr,key=lambda r:r['round'])
    if not good:raise ValueError('neither strategy passed')
    if len(good)==1:return dict(selected=next(iter(good)),reason='only_strategy_passing_both_rounds',rule=RULE)
    def point(s,r,w):return next(p for p in good[s][r]['points'] if p['search_width']==w)
    gm=lambda xs:math.exp(sum(math.log(x) for x in xs)/len(xs))
    ratios=[gm([point('records_only',r,w)['qps']/point('graph_first',r,w)['qps'] for w in WIDTHS]) for r in (0,1)]
    merged={s:{str(w):sum(point(s,r,w)['query_count'] for r in (0,1))/sum(point(s,r,w)['query_count']/point(s,r,w)['qps'] for r in (0,1)) for w in WIDTHS} for s in STRATEGIES}
    ratio=gm([merged['records_only'][str(w)]/merged['graph_first'][str(w)] for w in WIDTHS])
    winner='records_only' if all(r>1 for r in ratios) and (ratio>=1.05 or math.isclose(ratio,1.05,rel_tol=1e-12)) else 'graph_first'
    return dict(selected=winner,round_ratios=ratios,merged_qps=merged,merged_ratio=ratio,rule=RULE,reason='two_round_direction_and_five_percent_gate' if winner=='records_only' else 'conservative_default')

def verify_lock(lock,identity,selected):
    if lock['identity']!=identity or lock['decision']['selected']!=selected:raise ValueError('selection lock identity mismatch')
    if lock['decision']['rule']!=RULE:raise ValueError('selection rule changed')
    for f,h in lock['files'].items():
        if digest(f)!=h:raise ValueError('locked input or validation evidence changed: '+f)
    return True

def cache_audit(a):
    remain=a['ours_route_plan']['budget_bytes']-a['ours_route_plan']['required_bytes']
    graph=a['ours_graph_cache_bytes'];records=a['ours_record_cache_reserved_bytes']
    if min(graph,records,remain)<0 or graph+records>remain:raise ValueError('cache exceeds remaining budget')
    if a['ours_cache_allocation']=='records_only' and graph!=0:raise ValueError('records_only has shared graph cache')
    if a['ours_record_cache_budget_bytes']!=remain-graph:raise ValueError('record budget mismatch')
    for item in a['ours_record_cache_stats']:
        c=item['cache']
        if c['reserved_bytes']!=records or c['static_reserved_bytes']+c['dynamic']['reserved_bytes']>records:raise ValueError('record accounting mismatch')
        if c['static_nodes']+c['dynamic']['capacity_nodes']>a['base_count']:raise ValueError('capacity exceeds dataset')
    return dict(remaining_bytes=remain,graph_bytes=graph,record_bytes=records,unused_bytes=remain-graph-records,per_width=a['ours_record_cache_stats'])
