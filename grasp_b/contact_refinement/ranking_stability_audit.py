"""Evidence intervals and sensitivity, not a replacement ranking policy."""
import math


def objective_envelope(row,objective):
    """Deterministic numerical bound, NOT a statistical confidence interval."""
    if row.get('validation_status')!='verified':return None
    name='risk' if objective=='risk' else 'utilization'
    lo,hi=row[name+'_interval']
    error=max(row.get(name+'_numerical_allowance',0.),row.get(name+'_oracle_error',0.),row.get(name+'_repeat_error',0.))
    if not all(math.isfinite(x) for x in (lo,hi,error)) or error<0:return None
    return (max(0.,lo-error),hi+error)


def classify_cell(rows,ranking,variants):
    active=[r for r in rows if r.get('status')=='scored_conditional']
    selected=ranking['selected_id']
    if not active or selected is None or any(objective_envelope(r,'risk') is None or objective_envelope(r,'utilization') is None for r in active):
        return dict(classification='unresolved',reason='missing_selection_or_certificate',near_tie_ids=[],numeric_clear=False)
    by_id={r['candidate_id']:r for r in active}
    risk_bounds={cid:objective_envelope(r,'risk') for cid,r in by_id.items()}
    best_upper=min(b[1] for b in risk_bounds.values())
    possible={cid for cid,b in risk_bounds.items() if b[0]<=best_upper}
    # Keep declared D1 policy indifference separately from certificate overlap.
    possible.update(ranking['top_primary_near_tie_ids'])
    util_bounds={cid:objective_envelope(by_id[cid],'utilization') for cid in possible}
    best_u_upper=min(b[1] for b in util_bounds.values())
    numeric_set={cid for cid,b in util_bounds.items() if b[0]<=best_u_upper}
    policy_set=set(ranking['top_both_near_tie_ids'])
    near=numeric_set|policy_set
    numeric_clear=len(numeric_set)==1 and selected in numeric_set
    baseline_clear=numeric_clear and len(policy_set)==1
    variant_tops={v['profile']:v['ranking']['selected_id'] for v in variants}
    valid_variants=all(v.get('all_certificates_verified',False) for v in variants)
    changed=[v for v in variants if v['ranking']['selected_id']!=selected]
    if not valid_variants:
        classification='unresolved';reason='sensitivity_certificate_incomplete'
    elif not baseline_clear:
        classification='near_tie';reason='certificate_overlap_or_declared_policy_indifference'
    elif any(len(v['ranking']['top_both_near_tie_ids'])>1 for v in variants):
        classification='near_tie';reason='sensitivity_contains_multiple_plausible_winners_even_if_id_stable'
    elif changed:
        # A k change is a DIFFERENT finite cone, not a numerical CI. Do not
        # mislabel clear but different discretization winners as solver noise.
        within=all(v['ranking']['selected_id'] in near or selected in v['ranking']['top_both_near_tie_ids'] for v in changed)
        classification='near_tie' if within else 'unresolved'
        reason='sensitivity_indifference' if within else 'discretization_or_parameter_sensitive_clear_winner'
    else:
        classification='clear_winner';reason='lexicographic_separation_exceeds_numeric_bounds_and_sensitivity_stable'
    for v in variants:
        near.update(v['ranking']['top_both_near_tie_ids'])
    return dict(classification=classification,reason=reason,numeric_clear=numeric_clear,
        numeric_possible_top_ids=sorted(numeric_set),policy_near_tie_ids=sorted(policy_set),near_tie_ids=sorted(near),
        primary_possible_ids=sorted(possible),variant_top_ids=variant_tops,
        top_changes=len(changed),bound_interpretation='deterministic numerical envelopes; not confidence intervals')
