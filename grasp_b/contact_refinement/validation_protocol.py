"""Independent validation DESIGN helpers. No real execution endpoint exists.

Mock records have their own namespace and cannot stand in for A outcomes.
This is an auditable test harness, not a hostile-Python security sandbox.
"""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
from collections import Counter,defaultdict
from itertools import combinations

from scipy.stats import binomtest

from grasp_b.wrench_v2.contacts import _freeze_json,_plain
from grasp_b.wrench_v2.episode_store import EpisodeStore,StoreError,digest
from .evaluation_report import StaticDecision,assert_matched,METHODS

VERSION='independent_validation_protocol_draft_v1'
MOCK_DOMAIN='mock_only_not_physical_authorization_v1'


@dataclass(frozen=True)
class ProtocolDraft:
    payload: object
    def __post_init__(self):
        p=json.loads(json.dumps(_plain(self.payload),allow_nan=False));expected=p.pop('protocol_hash',None)
        if p.get('version')!=VERSION or p.get('physical_execution_enabled') is not False:raise ValueError('design-only protocol required')
        if tuple(p['methods'])!=METHODS:raise ValueError('exact five versioned report methods required')
        dev=p['development_seeds'];final=p['final_candidate_seeds']
        if any(type(s) is not int or s<0 for s in dev+final) or len(set(dev))!=len(dev) or len(set(final))!=len(final) or set(dev)&set(final):raise ValueError('disjoint unique nonnegative seed lists required')
        if p['liquid_mode']!='pbf':raise ValueError('wet PBF Pour required')
        actual=digest(p)
        if expected is not None and expected!=actual:raise ValueError('protocol draft identity mismatch')
        p['protocol_hash']=actual;object.__setattr__(self,'payload',_freeze_json(p))
    def to_dict(self):return _plain(self.payload)
    @classmethod
    def from_json(cls,text):
        p=json.loads(text)
        if 'protocol_hash' not in p:raise ValueError('stored protocol identity required')
        return cls(p)


def plan_design(decisions,physical_config,*,protocol_hash):
    """Read frozen sidecars only; NEVER generate/filter/select/score/execute."""
    required={'scene_config','sequence_config','resources','liquid_mode','observation_policy','replicate_index'}
    if set(physical_config)!=required or physical_config['liquid_mode']!='pbf':raise ValueError('complete physical-only configuration required; no prediction/method fields')
    # Canonicalize the design once BEFORE hashing, never relax store checks.
    physical_config=json.loads(json.dumps(_plain(physical_config),allow_nan=False))
    grouped=defaultdict(list)
    for item in decisions:
        d=StaticDecision(item).to_dict();grouped[(d['task'],d['scene_seed'])].append(d)
    episodes={};links=[];decision_hashes=[]
    for cell,rows in sorted(grouped.items()):
        assert_matched(rows)
        for row in rows:
            selected=row['selected_id'];key=None;decision_hashes.append(row['decision_hash'])
            if selected is not None:
                physical=dict(version='mock_physical_identity_design_v1',task=row['task'],scene_seed=row['scene_seed'],
                    candidate_id=selected,candidate_hash=row['candidate_hashes'][selected],candidate_pool_hash=row['candidate_pool_hash'],
                    observation_input_identity=row['provenance']['filter_record_hash'],physical_config=physical_config)
                key=digest(physical)
                if key in episodes and episodes[key]!=physical:raise StoreError('physical hash collision')
                episodes[key]=physical
            links.append(dict(task=row['task'],scene_seed=row['scene_seed'],method=row['method_label'],decision_hash=row['decision_hash'],episode_key=key,
                abstention_reason='no_complete_score' if key is None else None))
    p=dict(version='five_method_mock_plan_design_v1',domain=MOCK_DOMAIN,protocol_hash=protocol_hash,decision_hashes=decision_hashes,
           episodes=episodes,ordered_episode_keys=list(episodes),method_links=links,
           physical_execution_enabled=False,phase1_complete=False,interpretation='design-only demonstration; not an executable frozen physical plan')
    p['plan_hash']=digest(p);return p


def validate_mock_plan(plan):
    if plan.get('domain')!=MOCK_DOMAIN or plan.get('physical_execution_enabled') is not False or plan.get('phase1_complete') is not False:
        raise StoreError('mock namespace required')
    if digest({k:v for k,v in plan.items() if k!='plan_hash'})!=plan['plan_hash']:raise StoreError('mock plan hash mismatch')
    keys=plan['ordered_episode_keys']
    if len(set(keys))!=len(keys) or set(keys)!=set(plan['episodes']):raise StoreError('ordered mock keys mismatch')
    if any(digest(p)!=key for key,p in plan['episodes'].items()):raise StoreError('mock physical identity mismatch')
    return True


@dataclass(frozen=True)
class MockPermit:
    protocol_hash: str
    plan_hash: str
    ordered_keys: tuple
    helper_source_hash: str
    domain: str=MOCK_DOMAIN


def mock_permit(plan):
    validate_mock_plan(plan)
    return MockPermit(plan['protocol_hash'],plan['plan_hash'],tuple(plan['ordered_episode_keys']),hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def run_mock_plan(plan,store,*,permit=None,runner=None):
    """Mock runner ONLY; refuses real A stores before making any new records.

    Missing/corrupt/unverifiable attempts stop, valid task failure may continue.
    No runtime path imports or calls Part A run_sequence.
    """
    from unittest.mock import Mock
    validate_mock_plan(plan)
    expected=mock_permit(plan)
    if not isinstance(permit,MockPermit) or permit!=expected:raise StoreError('matching MOCK-only permit required')
    if not isinstance(runner,Mock):raise StoreError('only unittest.mock.Mock runner accepted; no real execution adapter')
    if any(store.path(name).exists() for name in ['protocol.json','episode_plan.json']):raise StoreError('never write into a historical/physical store')
    store.write_once('mock_protocol.json',dict(domain=MOCK_DOMAIN,protocol_hash=plan['protocol_hash'],plan_hash=plan['plan_hash']))
    calls=reused=0;categories=Counter()
    for key in plan['ordered_episode_keys']:
        physical=plan['episodes'][key]
        def validate(record):
            if record.get('domain')!=MOCK_DOMAIN or record.get('physical_identity')!=physical or record.get('episode_key')!=key or record.get('category') not in ['success','task_failure']:
                raise StoreError('invalid mock outcome identity/category')
            outcome=record.get('mock_outcome')
            if not isinstance(outcome,dict) or outcome.get('mock_only') is not True or outcome.get('physical_identity')!=physical or type(outcome.get('success')) is not bool:
                raise StoreError('missing/malformed mock outcome')
            if record['category']!=('success' if outcome['success'] else 'task_failure'):raise StoreError('mock category mismatch')
            digest(outcome)
            return record
        try:
            value=validate(store.read(f'mock_episodes/{key}.json'));categories[value['category']]+=1;reused+=1;continue
        except FileNotFoundError:pass
        if store.path(f'mock_attempts/{key}.json').exists():raise StoreError('unverifiable started mock attempt; no automatic retry')
        acquired=store.write_once(f'mock_attempts/{key}.json',dict(episode_key=key,physical_identity=physical,domain=MOCK_DOMAIN),exclusive=True)
        if not acquired:raise StoreError('mock attempt race; stop')
        calls+=1;outcome=runner(physical)
        value=validate(dict(domain=MOCK_DOMAIN,episode_key=key,physical_identity=physical,mock_outcome=outcome,
                            category='success' if isinstance(outcome,dict) and outcome.get('success') is True else 'task_failure'))
        store.write_once(f'mock_episodes/{key}.json',value);categories[value['category']]+=1
    return dict(mock_only=True,calls=calls,reused=reused,categories=dict(categories),method_links=len(plan['method_links']),no_selection=sum(l['episode_key'] is None for l in plan['method_links']))


def paired_statistics(a,b,*,alpha=.05):
    """Paired binary labels AFTER decision freeze; no selector entry point.

    None = infrastructure/missing, never silently a normal task failure.
    A registered abstention is encoded 0 for the end-to-end metric ONLY.
    """
    if len(a)!=len(b) or not 0<alpha<1 or any(x not in (None,0,1) for x in list(a)+list(b)):raise ValueError('paired binary/unknown outcomes required')
    n=len(a);known=[(x,y) for x,y in zip(a,b) if x is not None and y is not None]
    if len(known)!=n:return dict(status='incomplete',planned_pairs=n,resolved_pairs=len(known),inference=None)
    if n==0:return dict(status='empty',planned_pairs=0,inference=None)
    wins=sum(x==1 and y==0 for x,y in known);losses=sum(x==0 and y==1 for x,y in known);discordant=wins+losses
    p=binomtest(wins,discordant,.5).pvalue if discordant else 1.
    # Each discordant-category marginal is binomial over seeds. Bonferroni
    # CP intervals give a conservative >=1-alpha CI for their difference;
    # no independence between win/loss category counts is assumed.
    plus=binomtest(wins,n).proportion_ci(confidence_level=1-alpha/2,method='exact')
    minus=binomtest(losses,n).proportion_ci(confidence_level=1-alpha/2,method='exact')
    return dict(status='complete',planned_pairs=n,wins=wins,losses=losses,discordant_pairs=discordant,p_exact=float(p),
        risk_difference=(wins-losses)/n,confidence_interval=[max(-1.,plus.low-minus.high),min(1.,plus.high-minus.low)],
        confidence_method='Bonferroni exact Clopper-Pearson marginal bounds for paired risk difference',
        sign_test_interpretation='two-sided exact conditional McNemar; 0 discordance gives p=1, not evidence of equality')


def holm(pvalues):
    if any(not 0<=p<=1 for p in pvalues):raise ValueError('finite p values required')
    ordered=sorted(range(len(pvalues)),key=lambda i:pvalues[i]);adjusted=[0.]*len(pvalues);running=0.
    for position,i in enumerate(ordered):running=max(running,(len(pvalues)-position)*pvalues[i]);adjusted[i]=min(1.,running)
    return adjusted


def outcome_denominators(categories):
    counts=Counter(categories);allowed={'success','task_failure','no_selection','missing_outcome','infrastructure_error'}
    if set(counts)-allowed:raise ValueError('explicit outcome category required')
    n=sum(counts.values());unresolved=counts['missing_outcome']+counts['infrastructure_error'];selected=n-counts['no_selection'];valid=counts['success']+counts['task_failure']
    return dict(planned=n,counts=dict(counts),selected=selected,valid_executed=valid,unresolved=unresolved,
        selection_coverage=selected/n if n else None,end_to_end_success=counts['success']/n if n and not unresolved else None,
        end_to_end_identification_bounds=[counts['success']/n,(counts['success']+unresolved)/n] if n else None,
        observed_conditional_success=counts['success']/valid if valid else None,
        complete_conditional_success=counts['success']/selected if selected and not unresolved else None,
        interpretation='no-selection is zero end-to-end success without a physical task failure; unresolved is not a failure label')
