"""Versioned static comparison records and tie-aware descriptive rank metrics."""
from dataclasses import dataclass
from itertools import combinations
import json
from collections import Counter
import numpy as np
from scipy.stats import spearmanr,kendalltau
from grasp_b.wrench_v2.contacts import _freeze_json,_plain
from .contact_regions import digest

VERSION='matched_static_five_method_comparison_v1'
METHODS=('M0_random','M1_strict_gws_v2','M2_strict_trajectory_gws_v2',
         'M1_relaxed_joint_lex_v1','M2_relaxed_joint_lex_v1')
STATUS_MAP={'scored':'scored','scored_conditional':'relaxed_scored','model_infeasible':'strict_model_infeasible',
            'geometry_invalid':'geometry_invalid','geometry_unsupported':'unsupported','geometry_unknown':'unsupported',
            'missing_inertia':'missing_parameters','missing_parameters':'missing_parameters','numerical_error':'numerical_error',
            'invalid_input':'numerical_error','unknown_motion':'unsupported','random_ranked':'random_ranked'}


@dataclass(frozen=True)
class StaticDecision:
    """Deep immutable sidecar, not Candidate or a Task08 decision replacement."""
    payload: object
    def __post_init__(self):
        p=json.loads(json.dumps(_plain(self.payload),allow_nan=False));prior=p.pop('decision_hash',None)
        if p.get('version')!=VERSION:raise ValueError('comparison namespace required')
        ids=p['feasible_ids'];ranking=p['ranking']
        if len(ids)!=len(set(ids)) or len(ranking)!=len(ids) or set(ids)!=set(ranking):raise ValueError('complete identical feasible IDs required')
        if set(p['scores'])!=set(ids):raise ValueError('one status per feasible candidate required')
        selected=p['selected_id']
        if selected is not None and (not ranking or selected!=ranking[0]):raise ValueError('selection/ranking mismatch')
        if p['method_label']=='M0_random' and any(r.get('residual') is not None or r.get('utilization') is not None for r in p['scores'].values()):
            raise ValueError('M0 must not consume GWS scores')
        identity=digest(p)
        if prior is not None and prior!=identity:raise ValueError('comparison decision hash mismatch')
        p['decision_hash']=identity;object.__setattr__(self,'payload',_freeze_json(p))
    def to_dict(self):return _plain(self.payload)
    @classmethod
    def from_json(cls,text):
        p=json.loads(text)
        if 'decision_hash' not in p:raise ValueError('persisted identity required')
        return cls(p)


def assert_matched(decisions):
    values=[x.to_dict() if isinstance(x,StaticDecision) else StaticDecision(x).to_dict() for x in decisions]
    if len(values)!=5 or {x['method_label'] for x in values}!=set(METHODS):raise ValueError('five independent method labels required')
    for key in ['task','scene_seed','selector_seed','candidate_pool_hash','feasible_ids','candidate_hashes','common_input_hash']:
        if len({digest(x[key]) for x in values})!=1:raise ValueError('unmatched method input '+key)
    return values


def tier_ranks(d):
    if d.get('tie_groups'):
        tiers=[ids for group in d['tie_groups'] for ids in group['secondary_ties']]
    elif d['method_label']=='M0_random':return {}
    else:
        tiers=[]
        for cid in d['ranking']:
            row=d['scores'][cid]
            if row['status'] not in ('scored','relaxed_scored'):continue
            if tiers and d['scores'][tiers[-1][0]]['utilization']==row['utilization']:tiers[-1].append(cid)
            else:tiers.append([cid])
    ranks={};position=1
    for ids in tiers:
        average=position+(len(ids)-1)/2
        ranks.update({cid:average for cid in ids});position+=len(ids)
    return ranks


def correlation(left,right):
    common=sorted(set(left)&set(right))
    if len(common)<2:return dict(status='insufficient',n=len(common),spearman=None,kendall=None)
    x=[left[c] for c in common];y=[right[c] for c in common]
    if len(set(x))<2 or len(set(y))<2:return dict(status='undefined_all_tied',n=len(common),spearman=None,kendall=None)
    return dict(status='defined',n=len(common),spearman=float(spearmanr(x,y).statistic),kendall=float(kendalltau(x,y).statistic),
                effective_tiers=[len(set(x)),len(set(y))])


def compare_pair(a,b):
    ra,rb=tier_ranks(a),tier_ranks(b)
    tie_sets=[set(d.get('near_tie_ids',[])) for d in (a,b) if len(d.get('near_tie_ids',[]))>1]
    changed=a['selected_id']!=b['selected_id']
    relation=('same_id' if not changed else 'no_selection_difference' if a['selected_id'] is None or b['selected_id'] is None
              else 'near_tie_compatible_change' if any({a['selected_id'],b['selected_id']}<=group for group in tie_sets)
              else 'both_clear_model_specific_change' if a.get('winner_status')==b.get('winner_status')=='clear_winner'
              else 'different_winners_with_uncertain_or_unqualified_side')
    return dict(left=a['method_label'],right=b['method_label'],top1_changed=changed,
        family_changed=a['selected_family']!=b['selected_family'],change_interpretation=relation,
        common_scored=correlation(ra,rb),full_ordinal=correlation({c:i for i,c in enumerate(a['ranking'])},{c:i for i,c in enumerate(b['ranking'])}),
        full_ordinal_caveat='includes ID-ordered unscored tails; descriptive only, not quality correlation',
        top_overlap={str(k):len(set(a['ranking'][:k])&set(b['ranking'][:k])) for k in (1,3,5)},
        scored_coverage=[len(ra),len(rb)])


def comparisons(decisions):
    grouped={}
    for d in decisions:grouped.setdefault((d['task'],d['scene_seed']),[]).append(d)
    return [dict(task=key[0],seed=key[1],**compare_pair(a,b)) for key,rows in sorted(grouped.items()) for a,b in combinations(rows,2)]


def distribution(values):
    values=[float(x) for x in values if x is not None]
    return dict(count=len(values),median=float(np.median(values)),p95=float(np.percentile(values,95)),maximum=max(values)) if values else dict(count=0)
