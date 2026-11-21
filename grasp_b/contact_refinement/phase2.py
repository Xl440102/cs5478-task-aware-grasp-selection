"""Frozen five-method development dispatch, separate from all selectors.

Preflight is read-only. Dispatch requires exact future confirmation; mock
attempts/results use a disjoint domain. At-most-once automatic dispatch only.
"""
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from unittest.mock import Mock, patch
import hashlib
import json
import subprocess
import traceback

from grasp_a.contracts import Candidate, Pose, candidate_sha256
from grasp_a.control import ObservationConfig, SceneRandomization
from grasp_a.sequence_contracts import SequenceConfig
from grasp_b.wrench_v2.contacts import _plain
from grasp_b.wrench_v2.episode_store import EpisodeStore, StoreError, canonical, digest
from grasp_b.wrench_v2.evaluation import validate_filter
from grasp_b.batch_adapter import validate_outcome
from grasp_b.wrench_v2.pilot import failure_diagnostics
from grasp_b.canary_adapter import canary_evidence
from . import phase1
from .phase2_authorization import Approval, authorization_request, validate_authorization, check_hash, MOCK_DOMAIN

ROOT = Path(__file__).resolve().parents[2]
RUN_A = ROOT/'results/local/task09f3a_development_phase1_20261112_run_a'
RUN_B = ROOT/'results/local/task09f3a_development_phase1_20261112_run_b'
PLAN_HASH = '5a7939bb6f50522b685a99b79f277020c00d5e061785b7d084a2667cb2db5fb2'
PROTOCOL_HASH = 'a165b8ded358d3319460056acd219e2f796a78da3e92fa50f5fdfb0e680b8d6b'
VERSION = 'five_method_frozen_physical_adapter_v1'
POLICY = dict(version='continue_valid_task_failure_stop_infrastructure_v1', at_most_once=True,
    exactly_once=False, automatic_retry=False, continue_on=['success','task_failure'],
    stop_on=['infrastructure_error','missing_outcome','started_without_outcome','corrupt_outcome','identity_mismatch'],
    execution_feedback_to_selectors=False)


class ReadOnlyStore(EpisodeStore):
    def __init__(self, root):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise StoreError('missing frozen directory')

    def write_once(self, *args, **kwargs):
        raise StoreError('frozen store is read-only')


def seal(p, field):
    if field in p:
        raise StoreError('identity field already present')
    return dict(p, **{field:digest(p)})


def raw_candidates(catalog):
    """Validate without replacing frozen floats with normalized loader output."""
    result=[]
    for row in catalog['candidates']:
        Candidate.from_dict(row)  # validation only
        value=dict(row)
        for k in ('grasp_pose','pregrasp_pose'):
            value[k]=Pose(tuple(row[k]['position']),tuple(row[k]['quaternion']))
        result.append(Candidate(**value))
    if candidate_sha256(result)!=catalog['pool_hash']:
        raise StoreError('full candidate catalog hash mismatch')
    return result


def adapter_sources():
    paths=[Path(__file__),Path(__file__).with_name('phase2_authorization.py'),Path(__file__).with_name('phase2_cli.py'),
        ROOT/'grasp_b/wrench_v2/episode_store.py',ROOT/'grasp_b/batch_adapter.py',ROOT/'grasp_b/canary_adapter.py',
        ROOT/'grasp_b/wrench_v2/pilot.py']
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def load_frozen(run_a=RUN_A, run_b=RUN_B):
    """Only read saved records, hash resources, rebuild dedup from saved IDs.

    No legacy load_bundle, M0 replay, scorer, generator, or feasibility call.
    """
    if subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()!='feature/part-b-contact-refinement':
        raise StoreError('wrong branch')
    registry,draft=phase1.load_registry()
    stores=[ReadOnlyStore(p) for p in (run_a,run_b)]
    bundles=[]
    for store in stores:
        phase1.verify_store(store)  # pure integrity checks, no ranking/selector
        plan=store.read('episode_plan.json'); protocol=store.read('protocol.json')
        if plan['plan_hash']!=PLAN_HASH or protocol['protocol_hash']!=PROTOCOL_HASH:
            raise StoreError('unexpected frozen plan/protocol')
        if store.read('method_registry.json')!=registry or protocol['parent_protocol_hash']!=draft['protocol_hash']:
            raise StoreError('parent F2 identity mismatch')
        if protocol['physical_config']!=draft['physical_config'] or protocol['prediction_models']!=draft['prediction_models']:
            raise StoreError('physical/load model mismatch')
        for name,h in protocol['implementation_sources'].items():
            if phase1.sha(ROOT/name)!=h:
                raise StoreError('Phase1 source mismatch')
        catalog=store.read('candidate_pool.json'); candidates=raw_candidates(catalog)
        pools=store.read('feasible_pools.json')
        if set(pools)!={f'{t}/{s}' for t in phase1.TASKS for s in phase1.SEEDS}:
            raise StoreError('nine registered development cells required')
        for cell,pool in pools.items():
            record=pool['filter_record']; task,seed=cell.split('/')
            if ((record['task_id'],record['seed'])!=(task,int(seed)) or
                list(validate_filter(candidates,record))!=pool['feasible_ids'] or digest(record)!=pool['filter_hash'] or
                pool['candidate_pool_hash']!=catalog['pool_hash'] or record['scene_randomization']!=protocol['physical_config']['scene_config'] or
                record['task_spec']['config']!=protocol['physical_config']['sequence_config']):
                raise StoreError('feasible pool/config/check identity mismatch')
            phase1.verify_seal(store.read('protocol.json'),'protocol_hash')
        decisions=[store.read('decisions/'+h+'.json') for h in plan['decision_hashes']]
        for d in decisions:
            if d['method_id'] in phase1.METHODS[3:]:
                if not {'status','policy','tie_groups','top_primary_near_tie_ids','top_both_near_tie_ids'}<=set(d['near_tie']):
                    raise StoreError('missing frozen near-tie evidence')
        if (len(decisions)!=45 or len(plan['episodes'])!=20 or len(plan['method_links'])!=45 or
            sum(l['episode_key'] is None for l in plan['method_links'])!=8 or
            Counter(e['task'] for e in plan['episodes'].values())!=dict(zip(phase1.TASKS,(7,7,6))) or
            sum(len(p['feasible_ids']) for p in pools.values())!=278):
            raise StoreError('frozen cohort count mismatch')
        bundles.append(dict(registry=registry,protocol=protocol,plan=plan,catalog=catalog,pools=pools,decisions=decisions))
    if bundles[0]!=bundles[1]:
        raise StoreError('independent Phase1 runs disagree; no normalization')
    bundle=bundles[0]
    physical=bundle['protocol']['physical_config']; resources=physical['resources']; seq=physical['sequence_config']; sc=physical['scene_config']
    if (resources['timestep_s']!=1/240 or resources['solver_iterations']!=150 or resources['deterministic_overlapping_pairs']!=1 or
        sc['name']!='wide_strict_v2' or sc['disable_wrist_hand_collision'] is not False or sc['filter_mode']!='execution' or
        physical['liquid_mode']!='pbf' or physical['replicate_index']!=0 or
        any(seq[k]!=v for k,v in dict(fluid_particles=256,fluid_iterations=8,fluid_substeps=2,pour_angle_deg=-110.,pour_dwell_s=2.).items())):
        raise StoreError('physical settings differ from frozen protocol')
    bundle['validation']=dict(valid=True,registry_hash=registry['registry_hash'],protocol_hash=PROTOCOL_HASH,plan_hash=PLAN_HASH,
        decisions=45,selected_method_links=37,abstentions=8,unique_episodes=20,feasible_instances=278,
        independent_runs_exact=True,generation_calls=0,feasibility_calls=0,selector_calls=0,physical_calls=0)
    return bundle


def execution_jobs(bundle):
    """Expand, don't replace, frozen physical identity; prediction not included."""
    config=bundle['protocol']['physical_config']; out={}
    for key,original in bundle['plan']['episodes'].items():
        if digest(original)!=key:
            raise StoreError('episode key mismatch')
        pool=bundle['pools'][f"{original['task']}/{original['scene_seed']}"]; record=pool['filter_record']
        contract=dict(task=original['task'],scene_seed=original['scene_seed'],candidate_id=original['candidate_id'],
            candidate_hash=original['candidate_hash'],candidate_pool_hash=original['candidate_pool_hash'],
            physical_resources=config['resources'],scene_config=config['scene_config'],sequence_config=config['sequence_config'],
            expected_observation_config=record['observation_config'],expected_initial_pose_estimate=record['initial_object_pose_estimate'],
            liquid_mode=config['liquid_mode'],replicate_index=config['replicate_index'],gui=False,media_dir=None,show_landmarks=True)
        identity=dict(version='five_method_expanded_physical_identity_v1',phase1_episode_key=key,
            frozen_physical_identity=original,dispatch_contract=contract)
        out[key]=dict(identity=identity,execution_identity_hash=digest(identity),dispatch_contract=contract)
    return out


def prepare_manifests(bundle):
    jobs=execution_jobs(bundle); order=bundle['plan']['ordered_episode_keys']; links=bundle['plan']['method_links']
    # Greedy additional METHOD coverage, tie by original frozen plan order.
    # Uses no score, geometry status, physical result, or runtime information.
    covered=set(); chosen=[]
    for task in phase1.TASKS:
        options=[k for k in order if jobs[k]['dispatch_contract']['task']==task]
        def methods(k):return {l['method_id'] for l in links if l['episode_key']==k}
        key=min(options,key=lambda k:(-len(methods(k)-covered),order.index(k)))
        chosen.append(key);covered|=methods(key)
    canary=[k for k in order if k in chosen]; remainder=[k for k in order if k not in chosen]
    sources=adapter_sources()
    def make(label,keys,prerequisite=None):
        payload=dict(version=VERSION,batch_id=label,protocol_hash=PROTOCOL_HASH,registry_hash=bundle['registry']['registry_hash'],
            phase1_plan_hash=PLAN_HASH,ordered_episode_keys=keys,episodes={k:jobs[k] for k in keys},
            method_links={k:[l for l in links if l['episode_key']==k] for k in keys},
            adapter_sources=sources,adapter_identity=digest(sources),resource_identity=digest(bundle['protocol']['physical_config']['resources']),
            physical_configuration_hash=digest(bundle['protocol']['physical_config']),replicate_index=0,
            execution_policy=POLICY,policy_hash=digest(POLICY),prerequisite_manifest_hash=prerequisite,
            selection_rule='task order Transport,Handover,Pour; maximize newly represented linked methods; tie original plan index; retain original relative dispatch order',
            physical_execution_authorized=False)
        return seal(payload,'manifest_hash')
    a=make('development_canary_v1',canary)
    b=make('development_remainder_v1',remainder,a['manifest_hash'])
    return a,b


def verify_manifest(manifest,bundle):
    check_hash(manifest,'manifest_hash')
    expected={m['batch_id']:m for m in prepare_manifests(bundle)}
    if expected.get(manifest.get('batch_id'))!=manifest:
        raise StoreError('batch/source/config/order identity mismatch')
    return True


def _safe_store_root(root,bundle,domain):
    root=Path(root).resolve()
    local=ROOT/'results/local'
    if root.is_relative_to(ROOT) and not root.is_relative_to(local):
        raise StoreError('execution records must not enter source/test directories')
    if root.is_relative_to(local):
        parts=root.relative_to(local).parts
        if not parts or not parts[0].startswith('task09f3b_execution_'):
            raise StoreError('new task09f3b_execution_ namespace required; historical stores forbidden')
    elif domain!=MOCK_DOMAIN:
        raise StoreError('physical records require dedicated repository local-results namespace')
    for p in (RUN_A,RUN_B):
        if root==p.resolve() or root.is_relative_to(p.resolve()):
            raise StoreError('Phase1 store forbidden')
    if any((root/n).exists() for n in ('protocol.json','episode_plan.json')):
        raise StoreError('legacy/frozen store forbidden')
    expected=dict(namespace='five_method_phase2_store_v1',domain=domain,phase1_plan_hash=PLAN_HASH,protocol_hash=PROTOCOL_HASH)
    if root.exists() and (root/'store_identity.json').exists():
        if ReadOnlyStore(root).read('store_identity.json')!=expected:
            raise StoreError('execution store identity mismatch')
    elif root.exists() and any(root.rglob('*.json')):
        raise StoreError('unidentified nonempty result store')
    return root,expected


def _existing(store,manifest,key,domain):
    base=domain+'/'; job=manifest['episodes'][key]
    def read(name):return store.read(base+name+'/'+key+'.json')
    if store.path(base+'incidents/'+key+'.json').exists():
        raise StoreError('infrastructure_error requires manual reconciliation: '+key)
    try:record=read('episodes')
    except FileNotFoundError:
        if store.path(base+'attempts/'+key+'.json').exists():
            raise StoreError('started_without_outcome: '+key)
        if store.path(base+'raw/'+key+'.json').exists():
            raise StoreError('orphan raw outcome: '+key)
        return None
    if record['episode_key']!=key or record['execution_identity_hash']!=job['execution_identity_hash'] or record['domain']!=domain:
        raise StoreError('cached physical identity mismatch')
    if record['category'] not in ('success','task_failure'):
        raise StoreError('cached infrastructure outcome is not reusable')
    raw=read('raw');attempt=read('attempts');completion=read('completions')
    author=store.read(base+'authorizations/'+record['authorization_hash']+'.json')
    prior_manifest=store.read(base+'manifests/'+record['manifest_hash']+'.json')
    check_hash(prior_manifest,'manifest_hash')
    if any(prior_manifest[k]!=manifest[k] for k in ('adapter_identity','resource_identity','physical_configuration_hash','protocol_hash','registry_hash','phase1_plan_hash','policy_hash','replicate_index')):
        raise StoreError('cached source/config/policy mismatch; no implicit compatibility')
    validate_authorization(prior_manifest,Approval(author),mock_only=domain==MOCK_DOMAIN)
    if (key not in prior_manifest['ordered_episode_keys'] or prior_manifest['episodes'][key]!=job or
        record['raw_checksum']!=digest(raw) or record['attempt_checksum']!=digest(attempt) or
        completion['episode_checksum']!=digest(record) or completion['raw_checksum']!=digest(raw) or
        attempt['authorization_hash']!=record['authorization_hash'] or digest(author)!=record['authorization_hash'] or
        attempt['execution_identity_hash']!=job['execution_identity_hash']):
        raise StoreError('cached audit/checksum/authorization mismatch')
    if validate_outcome(raw,job['dispatch_contract'])!=record['category']:
        raise StoreError('cached outcome classification mismatch')
    return record


def inspect_batch(manifest,store_root,*,run_a=RUN_A,run_b=RUN_B,mock_only=False):
    bundle=load_frozen(run_a,run_b);verify_manifest(manifest,bundle)
    domain=MOCK_DOMAIN if mock_only else 'physical'
    root,_=_safe_store_root(store_root,bundle,domain)
    statuses={};errors=[]
    if root.exists():
        store=ReadOnlyStore(root)
        # Entire store must be accounted for, not just the requested subset.
        all_jobs=execution_jobs(bundle)
        scope=dict(manifest,episodes=all_jobs)
        for path in (root/domain/'attempts').glob('*.json'):
            if path.stem not in all_jobs:
                errors.append('foreign attempt: '+path.stem)
            else:
                try:_existing(store,scope,path.stem,domain)
                except (ValueError,KeyError,TypeError,FileNotFoundError) as exc:errors.append(str(exc))
        for key in manifest['ordered_episode_keys']:
            try:
                value=_existing(store,manifest,key,domain)
                statuses[key]='not_started' if value is None else value['category']
            except (ValueError,KeyError,TypeError,FileNotFoundError) as exc:
                text=str(exc)
                state=('infrastructure_error' if 'infrastructure_error' in text else
                    'started_without_outcome' if 'started_without_outcome' in text else
                    'corrupt_outcome' if 'corrupt record' in text else
                    'missing_outcome' if isinstance(exc,FileNotFoundError) or 'orphan raw outcome' in text else 'identity_mismatch')
                statuses[key]=state;errors.append(text)
        prerequisite=manifest['prerequisite_manifest_hash']
        if prerequisite:
            canary=prepare_manifests(bundle)[0]
            for key in canary['ordered_episode_keys']:
                try:
                    if _existing(store,canary,key,domain) is None:errors.append('canary prerequisite incomplete: '+key)
                except (ValueError,KeyError,TypeError,FileNotFoundError) as exc:errors.append(str(exc))
    else:
        statuses={k:'not_started' for k in manifest['ordered_episode_keys']}
        if manifest['prerequisite_manifest_hash']:errors.append('canary prerequisite incomplete')
    return dict(valid=not errors,physical_execution_authorized=False,status_by_episode=statuses,errors=errors,
        counts=dict(Counter(statuses.values())),no_store_writes=True)


def _dispatch_contract(job,candidates):
    c=job['dispatch_contract']
    return (c['task'],c['scene_seed'],c['candidate_id'],candidates),dict(config=SequenceConfig(**c['sequence_config']),
        # Same call contract as frozen Phase1: A derives its seeded observation.
        # Returned config/estimate must still match the frozen records exactly.
        observation_config=None,scene_config=SceneRandomization(**c['scene_config']),
        liquid=c['liquid_mode'],gui=c['gui'],media_dir=c['media_dir'],show_landmarks=c['show_landmarks'])


def run_batch(manifest,store_root,*,approval=None,runner=None,run_a=RUN_A,run_b=RUN_B):
    """Only future exact human approval reaches real A. Mock domain never can.

    CLI deliberately exposes no dispatch option. Failed/incomplete attempts
    remain claimed, so a crash between filesystem records stops automatic retry.
    """
    bundle=load_frozen(run_a,run_b);verify_manifest(manifest,bundle)
    mock_only=isinstance(runner,Mock)
    if runner is not None and not mock_only:
        raise StoreError('custom callable rejected; mock runner or original A only')
    auth_hash=validate_authorization(manifest,approval,mock_only=mock_only)
    domain=MOCK_DOMAIN if mock_only else 'physical'
    root,store_identity=_safe_store_root(store_root,bundle,domain)
    inspection=inspect_batch(manifest,root,run_a=run_a,run_b=run_b,mock_only=mock_only)
    if not inspection['valid']:
        raise StoreError('pre-dispatch store/prerequisite failure: '+str(inspection['errors']))
    # Only now may any store be created or attempt/approval persisted.
    store=EpisodeStore(root);store.write_once('store_identity.json',store_identity);base=domain+'/'
    store.write_once(base+'manifests/'+manifest['manifest_hash']+'.json',manifest)
    store.write_once(base+'authorizations/'+auth_hash+'.json',approval.to_dict())
    candidates=raw_candidates(bundle['catalog']);calls=reused=0;stop=None
    for key in manifest['ordered_episode_keys']:
        try:
            fresh=load_frozen(run_a,run_b);verify_manifest(manifest,fresh)
            check=inspect_batch(manifest,root,run_a=run_a,run_b=run_b,mock_only=mock_only)
            if not check['valid']:raise StoreError('identity/store changed before dispatch')
            if _existing(store,manifest,key,domain) is not None:
                reused+=1;continue
            job=manifest['episodes'][key]
            attempt=dict(version=VERSION,domain=domain,episode_key=key,execution_identity_hash=job['execution_identity_hash'],
                manifest_hash=manifest['manifest_hash'],authorization_hash=auth_hash,policy_hash=manifest['policy_hash'],
                method_links=manifest['method_links'][key],dispatch_timestamp=datetime.now(timezone.utc).isoformat(),attempt_status='started')
            if not store.write_once(base+'attempts/'+key+'.json',attempt,exclusive=True):
                raise StoreError('attempt already claimed; no retry')
            began=perf_counter();steps=0;raw=None
            try:
                args,kwargs=_dispatch_contract(job,candidates)
                if mock_only:
                    calls+=1;raw=runner(*args,**kwargs)
                else:
                    import pybullet as p
                    from grasp_a.sequence import run_sequence
                    original=p.stepSimulation
                    def counted(*a,**kw):
                        nonlocal steps
                        steps+=1
                        return original(*a,**kw)
                    with patch.object(p,'stepSimulation',side_effect=counted):
                        calls+=1;raw=run_sequence(*args,**kwargs)
                verify_manifest(manifest,load_frozen(run_a,run_b))
                category=validate_outcome(raw,job['dispatch_contract'])
                if not mock_only:
                    expected_steps=round(raw['elapsed_s']*240)+(120 if job['dispatch_contract']['task']=='pour_water' and raw['status']!='rejected' else 0)
                    if steps!=expected_steps:raise StoreError('physical step accounting mismatch')
                raw_hash=store.write_once(base+'raw/'+key+'.json',raw)
                record=dict(version=VERSION,domain=domain,episode_key=key,execution_identity_hash=job['execution_identity_hash'],
                    manifest_hash=manifest['manifest_hash'],authorization_hash=auth_hash,attempt_checksum=digest(attempt),raw_checksum=raw_hash,
                    category=category,method_links=manifest['method_links'][key],wall_clock_s=perf_counter()-began,
                    diagnostics=dict(canary_evidence(raw,steps),measurement_domain='mock_only_no_physical_measurement' if mock_only else 'actual_A_episode',
                        pre_episode_step_difference=None if mock_only else steps-round(raw['elapsed_s']*240)),failure_diagnostics=failure_diagnostics(raw),
                    physical_step_count=steps if not mock_only else None,mock_only=mock_only)
                record_hash=store.write_once(base+'episodes/'+key+'.json',record)
                store.write_once(base+'completions/'+key+'.json',dict(episode_checksum=record_hash,raw_checksum=raw_hash,attempt_status='finished'))
                _existing(store,manifest,key,domain)
            except Exception as exc:
                # No invalid raw payload may become a reusable outcome.
                try:
                    canonical(raw)
                    rejected_raw=raw
                except (ValueError,TypeError):
                    rejected_raw=None
                incident=dict(domain=domain,episode_key=key,category='infrastructure_error',error_type=type(exc).__name__,
                    error=str(exc),traceback=traceback.format_exc(),wall_clock_s=perf_counter()-began,
                    rejected_raw_outcome=rejected_raw,forensic_raw_repr=repr(raw),automatic_retry=False)
                store.write_once(base+'incidents/'+key+'.json',incident)
                raise
        except Exception as exc:
            stop=dict(episode_key=key,error_type=type(exc).__name__,message=str(exc));break
    result=dict(version=VERSION,domain=domain,status='stopped_infrastructure' if stop else 'complete',new_runner_calls=calls,
        reused=reused,stop=stop,method_links=sum(len(v) for v in manifest['method_links'].values()),
        independent_episode_count=len(manifest['ordered_episode_keys']),physical_execution_authorized=not mock_only)
    # Unique run audit; repeated reads/reuse never overwrite previous summaries.
    name=digest(dict(result=result,timestamp=datetime.now(timezone.utc).isoformat()))
    store.write_once(base+'runs/'+name+'.json',result)
    return result


def completion_audit(manifest,store_root,**kwargs):
    value=inspect_batch(manifest,store_root,**kwargs)
    value['complete']=value['valid'] and all(s in ('success','task_failure') for s in value['status_by_episode'].values())
    value['independent_completed_episodes']=sum(s in ('success','task_failure') for s in value['status_by_episode'].values())
    value['selected_method_links']=sum(len(v) for v in manifest['method_links'].values())
    value['phase1_no_selection_decisions']=8
    value['method_denominator_policy']='one decision per task/seed/method; shared episode is not an independent replicated physical sample'
    return value
