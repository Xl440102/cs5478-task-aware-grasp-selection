"""Independent task-lex numerical tie bands, never a legacy fallback."""
from dataclasses import asdict,dataclass
import math

from .relaxed_solver import TaskLexResult,NAMESPACE
from .contact_regions import digest

RANKING_VERSION='task_lex_anchored_numeric_tie_bands_v1'


@dataclass(frozen=True,kw_only=True)
class NearTiePolicy:
    residual_absolute: float=1e-6
    utilization_absolute: float=1e-6
    relative: float=1e-8

    def __post_init__(self):
        for value in asdict(self).values():
            if isinstance(value,bool) or not math.isfinite(value) or value<0:raise ValueError('finite nonnegative tie tolerances required')


def _bands(rows,key,absolute,relative):
    remaining=sorted(rows,key=lambda r:(r[key],r['candidate_id']));groups=[]
    while remaining:
        anchor=remaining[0][key];limit=anchor+absolute+relative*max(1.,abs(anchor))
        inside=[r for r in remaining if r[key]<=limit];remaining=[r for r in remaining if r[key]>limit]
        groups.append(inside)
    return groups


def rank_task_results(results,expected_ids,*,policy=None):
    policy=policy or NearTiePolicy();ids=list(expected_ids)
    if len(set(ids))!=len(ids):raise ValueError('duplicate feasible candidate IDs')
    records=[r.to_dict() if isinstance(r,TaskLexResult) else TaskLexResult(r).to_dict() for r in results]
    if len(records)!=len(ids) or {r['candidate_id'] for r in records}!=set(ids):raise ValueError('complete one-per-candidate result set required')
    signatures={(r['namespace'],r['task'],r['method'],r['object_identity'],r['common_context_identity'],r['frame'],tuple(r['reference_point_m']),r['config_identity']) for r in records}
    if len(signatures)>1:raise ValueError('mixed method/task/physics/scaling/reference inputs')
    active=[r for r in records if r['status']=='scored_conditional']
    # M1 shares precisely one TWS. M2 may have candidate-dependent demands.
    if active and active[0]['method']=='relaxed_m1_task_lex_v1' and len({r['task_demand_signature'] for r in active})>1:
        raise ValueError('M1 task demand sets differ across candidates')
    for r in active:
        if not all(isinstance(r[k],(int,float)) and math.isfinite(r[k]) and r[k]>=0 for k in ('risk','utilization')):
            raise ValueError('complete finite task-lex objectives required')
        if r['namespace']!=NAMESPACE:raise ValueError('independent namespace required')
    ordered=[];groups=[]
    for primary in _bands(active,'risk',policy.residual_absolute,policy.relative):
        secondary_groups=[]
        for secondary in _bands(primary,'utilization',policy.utilization_absolute,policy.relative):
            secondary=sorted(secondary,key=lambda r:r['candidate_id'])
            ordered.extend(r['candidate_id'] for r in secondary)
            secondary_groups.append([r['candidate_id'] for r in secondary])
        groups.append(dict(primary_ids=[r['candidate_id'] for r in primary],secondary_ties=secondary_groups))
    ordered+=sorted(r['candidate_id'] for r in records if r['status']!='scored_conditional')
    selected=ordered[0] if active else None
    return dict(version=RANKING_VERSION,namespace=NAMESPACE,policy=asdict(policy),policy_identity=digest(asdict(policy)),
        ranking=ordered,selected_id=selected,selection_status='conditional_numeric_only' if active else 'no_complete_score',
        scored_count=len(active),status_by_id={r['candidate_id']:r['status'] for r in records},tie_groups=groups,
        top_primary_near_tie_ids=groups[0]['primary_ids'] if groups else [],
        top_both_near_tie_ids=groups[0]['secondary_ties'][0] if groups else [],
        interpretation='numeric bands only; no calibrated physical ordering or execution authorization')
