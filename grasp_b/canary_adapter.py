"""Subset-authorized canary extension; frozen wrench_v2 sources stay untouched.

No selector, generator, IK, LP or ranking is called here. Extension code identity
is separately bound to the canary manifest and the human authorization. There
is deliberately no generic confirmation-string or execute-all interface.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from grasp_a.contracts import Candidate,Pose,candidate_sha256
from grasp_a.control import SceneRandomization
from grasp_a.sequence_contracts import SequenceConfig,SEQUENCE_TASKS
from grasp_b.wrench_v2.episode_store import EpisodeStore,StoreError,digest,canonical
from grasp_b.wrench_v2.pilot import (PilotProtocol,resources,classify_outcome,
    failure_diagnostics,execution_summary)
from grasp_b.wrench_v2.evaluation import validate_filter

EXPECTED_PROTOCOL='184dd0353ce2d8473de1fdc46e9be88fb21e02f9a4b02d953356540c3db43f1c'
EXPECTED_PLAN='c653e45a8a53f4fb4ee050e3036924f7f5d3cf26f5e63c10cb46be84f652f9b4'


def extension_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def frozen_preflight(store,*,expected_protocol=EXPECTED_PROTOCOL,expected_plan=EXPECTED_PLAN):
    """Read/validate frozen values, without load_bundle's M0 recomputation."""
    protocol=PilotProtocol.from_dict(store.read('protocol.json'))
    plan=store.read('episode_plan.json')
    if protocol.protocol_hash!=expected_protocol or digest(plan)!=expected_plan or plan['protocol_hash']!=expected_protocol:
        raise StoreError('frozen protocol/plan identity mismatch; STOP, no refreeze')
    if digest(resources())!=digest(protocol.resources):raise StoreError('runtime source/asset/dependency mismatch; STOP')
    raw=store.read('candidates.json');candidates=[]
    for v in raw:
        Candidate.from_dict(v)  # validate, then preserve exact frozen floats
        candidates.append(Candidate(**dict(v,grasp_pose=Pose(tuple(v['grasp_pose']['position']),tuple(v['grasp_pose']['quaternion'])),
            pregrasp_pose=Pose(tuple(v['pregrasp_pose']['position']),tuple(v['pregrasp_pose']['quaternion'])))))
    if len(candidates)!=116 or candidate_sha256(candidates)!=protocol.candidate_pool_hash:raise StoreError('candidate pool mismatch')
    by_id={c.id:c for c in candidates}
    if len(by_id)!=116:raise StoreError('duplicate Candidate ID')
    expected={(task,seed) for task in protocol.tasks for seed in protocol.scene_seeds}
    if len(plan['decisions'])!=len(expected) or {(r['task'],r['scene_seed']) for r in plan['decisions']}!=expected:raise StoreError('incomplete frozen cells')
    trials={};expected_links=[]
    for row in plan['decisions']:
        trial=store.read('decisions/'+row['decision_hash']+'.json')
        if digest(trial)!=row['decision_hash'] or (trial['task'],trial['scene_seed'])!=(row['task'],row['scene_seed']):raise StoreError('decision hash/cell mismatch')
        record=trial['filter_record'];ids=validate_filter(candidates,record)
        if (record['task_id'],record['seed'])!=(row['task'],row['scene_seed']) or list(ids)!=trial['feasible_ids'] or len(ids)!=row['feasible_count']:raise StoreError('frozen A-feasible pool mismatch')
        if digest(record['scene_randomization'])!=digest(protocol.scene_config) or digest(record['task_spec']['config'])!=digest(protocol.sequence_config):raise StoreError('frozen filter config mismatch')
        if digest(trial['physics'])!=digest(protocol.scoring_config) or digest(trial['body'])!=protocol.prediction_models[row['task']]['combined_object_hash']:raise StoreError('frozen model mismatch')
        if not trial['filter_metrics']['step_guard_enforced'] or trial['filter_metrics']['physical_step_calls']!=0:raise StoreError('missing frozen nonphysics filter evidence')
        names=('M0','M1',f'M2/{protocol.samples_per_stage}')
        if set(trial['methods'])!=set(names):raise StoreError('method set mismatch')
        for short,name,summary in zip(('M0','M1','M2'),names,row['methods']):
            m=trial['methods'][name]
            if summary['method']!=short or (m['selected_id'] is not None and m['ranking'][:1]!=[m['selected_id']]):raise StoreError('frozen selected ID mismatch')
            if len(m['ranking'])!=len(ids) or set(m['ranking'])!=set(ids) or m['input_feasible_ids']!=list(ids):raise StoreError('method pool/ranking coverage mismatch')
            if (m['selected_id'],m['selected_family'])!=(summary['selected_id'],summary['selected_family']):raise StoreError('summary/decision selection mismatch')
            if short!='M0':
                scores=m['scores']
                if [r['candidate_id'] for r in scores]!=m['ranking']:raise StoreError('frozen score/ranking association mismatch')
                for score in scores:
                    if score['candidate_hash']!=candidate_sha256([by_id[score['candidate_id']]]):raise StoreError('score Candidate identity mismatch')
                if m['selected_id'] is not None:
                    selected=scores[0]
                    if selected['status']!='scored' or selected['worst_utilization']!=summary['utilization'] or selected['score']!=summary['margin']:raise StoreError('selected score mismatch')
            links=[l for l in plan['method_links'] if (l['task'],l['scene_seed'],l['method'])==(row['task'],row['scene_seed'],short)]
            if len(links)!=1 or links[0]['decision_hash']!=row['decision_hash']:raise StoreError('method link mismatch')
            link=links[0];key=link['episode_key']
            if m['selected_id'] is None:
                if key is not None:raise StoreError('abstention has episode')
            else:
                job=plan['episodes'][key]
                if digest(job)!=key or (job['task'],job['scene_seed'],job['candidate_id'])!=(row['task'],row['scene_seed'],m['selected_id']):raise StoreError('episode identity mismatch')
                if job['candidate_hash']!=candidate_sha256([by_id[m['selected_id']]]) or job['candidate_pool_hash']!=protocol.candidate_pool_hash:raise StoreError('episode Candidate mismatch')
                if digest(job['scene_config'])!=digest(protocol.scene_config) or digest(job['sequence_config'])!=digest(protocol.sequence_config) or digest(job['physical_resources'])!=digest(protocol.resources['physical']):raise StoreError('episode physics mismatch')
                if digest(job['expected_observation_config'])!=digest(record['observation_config']) or digest(job['expected_initial_pose_estimate'])!=digest(record['initial_object_pose_estimate']):raise StoreError('episode observation mismatch')
                if job['liquid_mode']!='pbf' or job['replicate_index']!=protocol.replicate_index or job['observation_config'] is not None:raise StoreError('execution mode mismatch')
            expected_links.append(link)
        trials[(row['task'],row['scene_seed'])]=trial
    if len(expected_links)!=27 or {l['episode_key'] for l in expected_links if l['episode_key']}!=set(plan['episodes']) or store.read('method_links.json')!=plan['method_links']:raise StoreError('orphan/missing jobs or links')
    for folder in ('episodes','attempts'):
        if any(p.stem not in plan['episodes'] for p in (store.root/folder).glob('*.json')):raise StoreError('unknown record in frozen store')
    execution_summary(store,plan)  # validates all existing records, no dispatch
    return protocol,candidates,plan,trials


def make_canary_manifest(store,episode_keys,*,expected_protocol=EXPECTED_PROTOCOL,expected_plan=EXPECTED_PLAN):
    protocol,_,plan,_=frozen_preflight(store,expected_protocol=expected_protocol,expected_plan=expected_plan)
    keys=tuple(episode_keys)
    if len(keys)!=3 or len(set(keys))!=3 or any(k not in plan['episodes'] for k in keys):raise ValueError('exactly three distinct planned episode keys required')
    if tuple(plan['episodes'][k]['task'] for k in keys)!=SEQUENCE_TASKS or plan['episodes'][keys[2]]['scene_seed']!=3:raise ValueError('ordered Transport/Handover/Pour; Pour seed3 required')
    data=dict(version='canary_subset_v1',protocol_hash=protocol.protocol_hash,plan_hash=digest(plan),
        ordered_episode_keys=list(keys),episodes={k:plan['episodes'][k] for k in keys},
        adapter_sha256=extension_sha256(),stop_on_first_failure=True,authorization_scope='exact_canary_subset_only')
    return dict(data,canary_hash=digest(data))


@dataclass(frozen=True,slots=True)
class CanaryAuthorization:
    protocol_hash:str
    plan_hash:str
    canary_hash:str
    approved_episode_keys:tuple
    adapter_sha256:str
    approved_by:str

    def __post_init__(self):
        object.__setattr__(self,'approved_episode_keys',tuple(self.approved_episode_keys))
        if not isinstance(self.approved_by,str) or not self.approved_by.strip():raise ValueError('explicit caller identity required')


def canary_evidence(raw,total_steps):
    final=raw.get('final_evidence') or {};water=final.get('water')
    scored_steps=round(raw.get('elapsed_s',0)*240)
    return dict(control_status=raw.get('status'),task_success=raw.get('success'),
        success_checks=raw.get('success_checks'),failure_diagnostics=failure_diagnostics(raw),
        pbf_model=raw.get('liquid_model'),pbf_initial_check=raw.get('liquid_initial_check'),
        final_water=water,transfer_ratio=water.get('transfer_ratio') if water else None,
        spill_ratio=water.get('spill_ratio') if water else None,
        collision=raw.get('collision'),dropped=raw.get('dropped'),
        total_stepSimulation_calls=total_steps,scored_steps_from_A_elapsed=scored_steps,
        pre_episode_step_difference=total_steps-scored_steps,
        step_note='serial wrapper counts original calls including PBF pre-settle; A elapsed excludes successful liquid initialization')


def run_canary(store,manifest,*,authorization=None,runner=None):
    """Later explicit approval ONLY. Tests inject Mock; this module has no CLI."""
    expected=make_canary_manifest(store,manifest['ordered_episode_keys'],expected_protocol=manifest['protocol_hash'],expected_plan=manifest['plan_hash'])
    if digest(expected)!=digest(manifest):raise StoreError('canary manifest/subset/extension mismatch')
    if authorization is None:return dict(status='authorization_required',canary_hash=manifest['canary_hash'],new_calls=0)
    if not isinstance(authorization,CanaryAuthorization) or (
        authorization.protocol_hash,authorization.plan_hash,authorization.canary_hash,
        authorization.approved_episode_keys,authorization.adapter_sha256)!=(
        manifest['protocol_hash'],manifest['plan_hash'],manifest['canary_hash'],
        tuple(manifest['ordered_episode_keys']),manifest['adapter_sha256']):raise ValueError('explicit subset-bound authorization required; generic authorization rejected')
    protocol,candidates,plan,_=frozen_preflight(store,expected_protocol=manifest['protocol_hash'],expected_plan=manifest['plan_hash'])
    store.write_once(f'canary_authorizations/{digest(authorization)}.json',authorization)
    new_calls=0;reused=0
    for key in manifest['ordered_episode_keys']:
        frozen_preflight(store,expected_protocol=manifest['protocol_hash'],expected_plan=manifest['plan_hash'])
        job=plan['episodes'][key]
        try:
            cached=store.read(f'episodes/{key}.json');reused+=1
            if cached['category']!='success':return dict(status='stopped_cached_failure',failed_key=key,new_calls=new_calls,reused=reused)
            if cached.get('canary_hash')!=manifest['canary_hash'] or not cached.get('canary_evidence'):
                return dict(status='existing_episode_requires_canary_evidence_review',episode_key=key,new_calls=new_calls,reused=reused)
            continue
        except FileNotFoundError:pass
        if not store.write_once(f'attempts/{key}.json',dict(episode_key=key,physical_identity=job),exclusive=True):
            return dict(status='missing_outcome_requires_reconciliation',episode_key=key,new_calls=new_calls,reused=reused)
        began=perf_counter();raw=None;steps=0;error=None;evidence=None
        try:
            import pybullet as p
            original=p.stepSimulation
            def counted(*args,**kwargs):
                nonlocal steps
                steps+=1
                return original(*args,**kwargs)
            if runner is None:
                from grasp_a.sequence import run_sequence
                call=run_sequence
            else:call=runner
            with patch.object(p,'stepSimulation',side_effect=counted):
                new_calls+=1
                raw=call(job['task'],job['scene_seed'],job['candidate_id'],candidates,
                    config=SequenceConfig(**job['sequence_config']),scene_config=SceneRandomization(**job['scene_config']),
                    observation_config=None,liquid=job['liquid_mode'],gui=job['gui'],media_dir=job['media_dir'],show_landmarks=job['show_landmarks'])
            category=classify_outcome(raw,job)
            required={'schema_version','stages','stage_evaluation','terminal_checks','trajectory','elapsed_s','final_evidence'}
            if not required<=set(raw) or raw['schema_version']!='sequence_episode_v1':raise StoreError('incomplete A episode output')
            canonical(raw)
            evidence=canary_evidence(raw,steps)
            if job['task']=='pour_water' and category=='success':
                water=evidence['final_water']
                if raw.get('liquid_mode')!='pbf' or not raw.get('liquid_model') or raw['liquid_model'].get('model')!='position_based_fluid' or not raw.get('liquid_initial_check') or not water or water.get('finite_state') is not True:
                    raise StoreError('successful PBF episode lacks initialization/terminal fluid evidence')
                if evidence['pre_episode_step_difference']!=120:raise StoreError('PBF step accounting differs from frozen 120-step initialization')
        except Exception as exc:
            category='infrastructure_error';error=dict(type=type(exc).__name__,message=str(exc))
            # Preserve valid raw A output even on canary completeness failure.
            try:canonical(raw)
            except (ValueError,TypeError):raw=None
        record=dict(episode_key=key,physical_identity=job,category=category,raw_outcome=raw,
            error=error,wall_s=perf_counter()-began,canary_hash=manifest['canary_hash'],
            authorization_hash=digest(authorization),canary_evidence=evidence,total_stepSimulation_calls=steps,
            validation_error=error is not None and raw is not None)
        # Frozen store's validator requires raw category consistency. If an A
        # success is structurally incomplete, retain it as rejected evidence,
        # not a falsely valid physical result.
        if record['validation_error']:
            record['rejected_raw_outcome']=raw;record['raw_outcome']=None
        store.write_once(f'episodes/{key}.json',record)
        if digest(store.read(f'episodes/{key}.json'))!=digest(record):raise StoreError('stored episode round-trip mismatch')
        if category!='success':return dict(status='stopped_canary_failure',failed_key=key,new_calls=new_calls,reused=reused)
    return dict(status='canary_complete',new_calls=new_calls,reused=reused,canary_hash=manifest['canary_hash'])
