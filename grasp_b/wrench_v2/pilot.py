"""Frozen Phase1 decisions -> authorized Phase2 adapter. No selector feedback.

The CLI exposes Phase1 ONLY. Real execution additionally requires an explicit
authorization bound to the complete protocol AND plan hashes, supplied later.
"""
from collections import Counter
from dataclasses import dataclass,field,asdict
from importlib.metadata import version
from pathlib import Path
import platform
import time

from grasp_a.contracts import Candidate,candidate_sha256
from grasp_a.control import scene_randomization,SceneRandomization
from grasp_a.sequence_contracts import SequenceConfig,SEQUENCE_TASKS,SEQUENCE_CONTROLLER,SEQUENCE_EVALUATOR
from .contacts import _freeze_json,_plain,ContactModel
from .episode_store import EpisodeStore,StoreError,digest
from .evaluation import validate_filter
from .evaluation import random_ranking
from .motion_prediction import MotionModel

ROOT=Path(__file__).resolve().parents[2]
METHODS=('M0','M1','M2')


def resources(root=ROOT):
    """No simulation: hash exact source/assets including Panda mesh files."""
    import pybullet_data
    import hashlib
    root=Path(root);data=Path(pybullet_data.getDataPath())
    files=list((root/'grasp_a').glob('*.py'))+list((root/'assets').rglob('*'))+list((data/'franka_panda').rglob('*'))+[data/'plane.urdf',data/'plane100.obj']
    hashes={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file() and '__pycache__' not in p.parts}
    bfiles=list((root/'grasp_b/wrench_v2').glob('*.py'))+[root/'grasp_b/multifamily.py',root/'grasp_b/models.py']
    return dict(physical=dict(file_sha256=hashes,environment=dict(python=platform.python_version(),**{k:version(k) for k in ('numpy','scipy','pybullet')}),
        timestep_s=1/240,solver_iterations=150,deterministic_overlapping_pairs=1,
        controller_version=SEQUENCE_CONTROLLER,evaluation_version=SEQUENCE_EVALUATOR,
        initialization='fresh_A_SequenceScene_v1'),
        selection_source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in bfiles})


@dataclass(frozen=True,slots=True,kw_only=True)
class PilotProtocol:
    candidate_pool_hash:str
    resources:dict
    scene_config:dict
    sequence_config:dict
    scoring_config:dict
    prediction_models:dict
    scene_seeds:tuple=(2,3,4)
    selector_seed:int=2305478
    tasks:tuple=SEQUENCE_TASKS
    samples_per_stage:int=64
    purpose:str='formal_pilot_preparation'
    replicate_index:int=0
    version:str=field(default='b_pilot_protocol_v1',init=False)
    protocol_hash:str=field(init=False)

    def __post_init__(self):
        if len(self.candidate_pool_hash)!=64 or any(c not in '0123456789abcdef' for c in self.candidate_pool_hash):raise ValueError('pool SHA256 required')
        if tuple(self.tasks)!=SEQUENCE_TASKS or tuple(self.scene_seeds)!=(2,3,4) or self.selector_seed!=2305478:raise ValueError('Task08B-0 task/seed protocol is fixed')
        if type(self.samples_per_stage) is not int or self.samples_per_stage not in (16,64):raise ValueError('explicit 16/64 sampling profile required')
        if self.purpose not in ('formal_pilot_preparation','development_dry_run') or (self.purpose=='formal_pilot_preparation' and self.samples_per_stage!=64):raise ValueError('formal pilot requires 64 samples/stage')
        if type(self.replicate_index) is not int or self.replicate_index<0:raise ValueError('nonnegative replicate required')
        for name in ('scene_seeds','tasks'):object.__setattr__(self,name,tuple(getattr(self,name)))
        scene=scene_randomization(SceneRandomization(**_plain(self.scene_config)))
        if _plain(asdict(scene))!=_plain(asdict(scene_randomization('wide_strict_v2'))):raise ValueError('wide_strict_v2 required')
        SequenceConfig(**_plain(self.sequence_config))
        physics=self.scoring_config
        if physics['k']!=16 or physics['friction']['coefficient']!=.5 or set(physics['finger_capacities'])!={'left','right'} or any(v['normal_force_n']!=5 for v in physics['finger_capacities'].values()) or physics['contact_model']!=ContactModel.PCWF.value:raise ValueError('fixed development GWS assumptions required')
        if set(self.prediction_models)!=set(SEQUENCE_TASKS) or self.prediction_models['pour_water']['model_label']!='wet_rigid_uniform_cylinder_v1':raise ValueError('explicit wet Pour prediction required')
        from .wet_payload import WetPayloadSpec,validate_wet_object,verify_dry_object
        from .tasks import ObjectParameters
        wm=self.prediction_models['pour_water'];spec=WetPayloadSpec.from_dict(_plain(wm['wet_payload']))
        wb=ObjectParameters(**_plain(wm['object_parameters']))
        binding=validate_wet_object(wb,spec)
        if digest({k:v for k,v in wm.items() if k!='object_parameters'})!=digest(binding):raise ValueError('protocol wet binding mismatch')
        for task in SEQUENCE_TASKS[:2]:
            if self.prediction_models[task]['model_label']!='dry':raise ValueError('Transport/Handover remain dry')
            dm=self.prediction_models[task];db=ObjectParameters(**_plain(dm['object_parameters']))
            if verify_dry_object(db,spec)!=dm['dry_object_hash'] or digest(db)!=dm['combined_object_hash'] or dm['dry_object_hash']!=binding['dry_object_hash']:raise ValueError('protocol dry identity mismatch')
        for name in ('resources','scene_config','sequence_config','scoring_config','prediction_models'):
            object.__setattr__(self,name,_freeze_json(getattr(self,name)))
        object.__setattr__(self,'protocol_hash',digest(self._data()))

    def _data(self):
        return {k:_plain(getattr(self,k)) for k in self.__dataclass_fields__ if k!='protocol_hash'}

    def to_dict(self):return dict(self._data(),protocol_hash=self.protocol_hash)

    @classmethod
    def from_dict(cls,value):
        if set(value)!=set(cls.__dataclass_fields__) or value['version']!='b_pilot_protocol_v1':raise ValueError('protocol schema mismatch')
        p=cls(**{k:v for k,v in value.items() if k not in ('version','protocol_hash')})
        if p.protocol_hash!=value['protocol_hash']:raise ValueError('protocol hash mismatch')
        return p


def validate_decision(protocol,candidates,trial):
    task,seed=trial['task'],trial['scene_seed']
    if task not in protocol.tasks or seed not in protocol.scene_seeds:raise ValueError('unplanned task/seed')
    if (task,seed)!=(trial['filter_record']['task_id'],trial['filter_record']['seed']):raise ValueError('trial/filter task seed mismatch')
    if trial['motion_models']!=[asdict(MotionModel(samples_per_stage=protocol.samples_per_stage,interpolation='half_cosine'))]:raise ValueError('motion protocol mismatch')
    ids=validate_filter(candidates,trial['filter_record'])
    if trial['candidate_sha256']!=protocol.candidate_pool_hash or trial['feasible_ids']!=list(ids) or trial['selector_seed']!=protocol.selector_seed:raise ValueError('decision pool/seed mismatch')
    if digest(trial['filter_record']['scene_randomization'])!=digest(protocol.scene_config) or digest(trial['filter_record']['task_spec']['config'])!=digest(protocol.sequence_config):raise ValueError('filter/protocol config mismatch')
    if digest(trial['physics'])!=digest(protocol.scoring_config):raise ValueError('score config mismatch')
    expected_label=protocol.prediction_models[task]['model_label']
    if trial['payload_mode']!=expected_label:raise ValueError('load-model mismatch')
    if digest(trial['body'])!=protocol.prediction_models[task]['combined_object_hash']:raise ValueError('decision object identity mismatch')
    if task=='pour_water' and digest(trial['load_model_identity'])!=digest({k:v for k,v in protocol.prediction_models[task].items() if k!='object_parameters'}):raise ValueError('wet identity mismatch')
    names=('M0','M1',f'M2/{protocol.samples_per_stage}')
    if set(trial['methods'])!=set(names):raise ValueError('all three methods required, exactly one frozen M2 resolution')
    result=[]
    for name,short in zip(names,METHODS):
        m=trial['methods'][name];rank=m['ranking'];selected=m['selected_id']
        if len(rank)!=len(ids) or set(rank)!=set(ids) or m['input_feasible_ids']!=list(ids):raise ValueError('incomplete/mismatched method ranking')
        scored=[r for r in m['scores'] if r['status']=='scored'] if short!='M0' else []
        if selected is not None and (selected not in ids or selected!=rank[0]):raise ValueError('invalid selection')
        if short!='M0':
            if len(m['scores'])!=len(ids) or {r['candidate_id'] for r in m['scores']}!=set(ids):raise ValueError('score coverage mismatch')
            if bool(scored)!=(selected is not None) or (selected is not None and selected not in {r['candidate_id'] for r in scored}):raise ValueError('strict abstention/selection mismatch')
            for row in scored:
                value=row['worst_utilization']
                if value is None or not __import__('math').isfinite(value) or value<0 or row['score']!=1-value:raise ValueError('invalid score')
            ordered=sorted(m['scores'],key=lambda r:(0,r['worst_utilization'],r['candidate_id']) if r['status']=='scored' else (1,0,r['candidate_id']))
            if [r['candidate_id'] for r in ordered]!=rank:raise ValueError('ranking does not implement strict score policy')
        elif rank!=random_ranking(ids,protocol.selector_seed)['ranking'] or selected!=(rank[0] if rank else None):raise ValueError('M0 uniform seed policy mismatch')
        rho=next((r['worst_utilization'] for r in scored if r['candidate_id']==selected),None)
        margin=next((r['score'] for r in scored if r['candidate_id']==selected),None)
        if rho is not None and (not __import__('math').isfinite(rho) or rho<0 or margin!=1-rho):raise ValueError('invalid utilization/margin')
        result.append(dict(method=short,method_id=m['method_id'],selected_id=selected,
            selected_family=m['selected_family'],scored_count=len(scored),status_counts=m['status_counts'],
            utilization=rho,margin=margin,predicted_over_capacity=rho is not None and rho>1,
            abstention_reason='no_complete_score' if selected is None else None))
    return result


def build_plan(protocol,candidates,trials):
    if len(candidates)!=116 or candidate_sha256(candidates)!=protocol.candidate_pool_hash:raise ValueError('frozen 116-candidate pool required')
    expected={(t,s) for t in protocol.tasks for s in protocol.scene_seeds}
    if len(trials)!=len(expected) or {(t['task'],t['scene_seed']) for t in trials}!=expected:raise ValueError('Phase1 must freeze ALL planned cells before execution')
    by_id={c.id:c for c in candidates};jobs={};links=[];decisions=[]
    for trial in sorted(trials,key=lambda r:(r['task'],r['scene_seed'])):
        method_rows=validate_decision(protocol,candidates,trial)
        decision_hash=digest(trial)
        decisions.append(dict(task=trial['task'],scene_seed=trial['scene_seed'],decision_hash=decision_hash,
            feasible_count=trial['feasible_count'],methods=method_rows))
        for method in method_rows:
            selected=method['selected_id'];key=None
            if selected is not None:
                physical=dict(task=trial['task'],scene_seed=trial['scene_seed'],candidate_id=selected,
                    candidate_hash=candidate_sha256([by_id[selected]]),candidate_pool_hash=protocol.candidate_pool_hash,
                    scene_config=_plain(protocol.scene_config),observation_config=None,
                    expected_observation_config=trial['filter_record']['observation_config'],
                    expected_initial_pose_estimate=trial['filter_record']['initial_object_pose_estimate'],
                    sequence_config=_plain(protocol.sequence_config),liquid_mode='pbf',
                    physical_resources=_plain(protocol.resources['physical']),replicate_index=protocol.replicate_index,
                    gui=False,media_dir=None,show_landmarks=True)
                key=digest(physical);jobs[key]=physical
            links.append(dict(task=trial['task'],scene_seed=trial['scene_seed'],**method,
                decision_hash=decision_hash,episode_key=key))
    return dict(version='frozen_episode_plan_v1',protocol_hash=protocol.protocol_hash,
        decisions=decisions,episodes=jobs,method_links=links,
        unique_episode_count=len(jobs),decision_count=len(links),abstentions=sum(x['episode_key'] is None for x in links),
        predicted_negative_margin_selections=sum(x['predicted_over_capacity'] for x in links),
        model_mismatch='Pour rigid-water prediction != PBF evolving fluid execution; no equivalence claim',
        frozen=True,human_authorization_required=True)


def freeze_bundle(store,protocol,candidates,trials):
    plan=build_plan(protocol,candidates,trials)
    store.write_once('protocol.json',protocol.to_dict())
    store.write_once('candidates.json',[asdict(c) for c in candidates])
    for t in trials:store.write_once(f'decisions/{digest(t)}.json',t)
    store.write_once('method_links.json',plan['method_links'])
    # Commit marker LAST. No plan -> no execution, even if some decisions exist.
    store.write_once('episode_plan.json',plan)
    return plan


def load_bundle(store):
    protocol=PilotProtocol.from_dict(store.read('protocol.json'))
    candidates=[Candidate.from_dict(v) for v in store.read('candidates.json')]
    # Candidate.from_dict normalizes quaternions. Preserve exact frozen values
    # after validation, so tiny normalization differences never change identity.
    from grasp_a.contracts import Pose
    raw=store.read('candidates.json')
    candidates=[Candidate(**{**v,'grasp_pose':Pose(tuple(v['grasp_pose']['position']),tuple(v['grasp_pose']['quaternion'])),
                                'pregrasp_pose':Pose(tuple(v['pregrasp_pose']['position']),tuple(v['pregrasp_pose']['quaternion']))}) for v in raw]
    plan=store.read('episode_plan.json')
    trials=[store.read(f'decisions/{r["decision_hash"]}.json') for r in plan['decisions']]
    if plan!=build_plan(protocol,candidates,trials) or store.read('method_links.json')!=plan['method_links']:raise StoreError('frozen plan/decision integrity mismatch')
    return protocol,candidates,plan


@dataclass(frozen=True,slots=True)
class ExecutionAuthorization:
    protocol_hash:str
    plan_hash:str
    approved_by:str
    confirmation:str


def classify_outcome(raw,physical):
    if not isinstance(raw,dict) or any(raw.get(k)!=v for k,v in dict(task_id=physical['task'],seed=physical['scene_seed'],selected_id=physical['candidate_id'],candidate_sha256=physical['candidate_pool_hash']).items()):raise StoreError('outcome identity mismatch')
    if raw.get('controller_version')!=physical['physical_resources']['controller_version'] or raw.get('evaluation_version')!=physical['physical_resources']['evaluation_version']:raise StoreError('outcome controller/evaluator mismatch')
    for field,expected in (('scene_randomization',physical['scene_config']),('observation_config',physical['expected_observation_config']),('initial_object_pose_estimate',physical['expected_initial_pose_estimate'])):
        if digest(raw.get(field))!=digest(expected):raise StoreError('outcome scene/observation mismatch')
    status=raw.get('status')
    if status in ('simulation_error','simulation_invalid'):return 'infrastructure_error'
    if status not in ('completed','execution_failed','rejected') or type(raw.get('success')) is not bool:raise StoreError('invalid outcome schema/status')
    if raw['success']:
        if status!='completed' or not raw.get('success_checks') or not all(v is True for v in raw['success_checks'].values()):raise StoreError('inconsistent success')
        return 'success'
    return 'task_failure'


def failure_diagnostics(raw):
    control=next((s['id'] for s in raw.get('stages',[]) if s.get('control_status') in ('failed','simulation_error')),None)
    physical=next((s.get('id') for s in raw.get('task_spec',{}).get('stages',[])
        if raw.get('stage_evaluation',{}).get(s.get('id'),{}).get('evidence') and
           raw['stage_evaluation'][s['id']].get('physical_pass') is False),None)
    return dict(control_failure_stage=control,first_evidenced_physical_failure_stage=physical,
                failure_reason=raw.get('failure_reason'),stage_inference_policy='explicit stage logs/evidence only; otherwise unknown')


def execution_summary(store,plan):
    links=[];unique={}
    for link in plan['method_links']:
        key=link['episode_key'];item=dict(link)
        if key is None:item.update(outcome_category='no_selection',episode_reused=False,dispatch_state='not_applicable')
        else:
            try:
                value=store.read(f'episodes/{key}.json')
                if value['episode_key']!=key or value['physical_identity']!=plan['episodes'][key]:raise StoreError('episode identity mismatch')
                if value['raw_outcome'] is None and (value['category']!='infrastructure_error' or not value.get('error')):raise StoreError('missing raw outcome cannot masquerade as valid success/failure')
                if value['raw_outcome'] is not None and classify_outcome(value['raw_outcome'],value['physical_identity'])!=value['category']:raise StoreError('episode classification mismatch')
                if value['category'] not in ('success','task_failure','infrastructure_error'):raise StoreError('invalid stored category')
                item.update(outcome_category=value['category'],episode_reused=key in unique,dispatch_state='recorded')
                unique[key]=value['category']
            except FileNotFoundError:
                state='not_started'
                try:
                    attempt=store.read(f'attempts/{key}.json')
                    if attempt!={'episode_key':key,'physical_identity':plan['episodes'][key]}:raise StoreError('attempt identity mismatch')
                    state='started_without_outcome'
                except FileNotFoundError:pass
                item.update(outcome_category='missing_outcome',episode_reused=False,dispatch_state=state)
        links.append(item)
    counts=Counter(v['outcome_category'] for v in links)
    return dict(method_links=links,method_outcome_counts=dict(counts),unique_recorded_episodes=len(unique),
        unique_outcome_counts=dict(Counter(unique.values())),expected_unique_episodes=len(plan['episodes']))


def execute_frozen_plan(store,*,authorization=None,runner=None):
    """No selection code is called. None/partial/tampered bundles fail closed.

    Supplying a mock runner is ONLY for tests. An interrupted started marker
    without a result is missing_outcome, never an automatic second execution.
    A later human must reconcile it or approve a new replicate protocol.
    """
    protocol,candidates,plan=load_bundle(store)
    if authorization is None:return dict(status='authorization_required',summary=execution_summary(store,plan))
    if not isinstance(authorization,ExecutionAuthorization) or authorization.protocol_hash!=protocol.protocol_hash or authorization.plan_hash!=digest(plan) or not authorization.approved_by.strip() or authorization.confirmation!='AUTHORIZE_PHYSICAL_EXECUTION':raise ValueError('explicit matching human authorization required')
    if digest(resources())!=digest(protocol.resources):raise StoreError('runtime source/assets/dependencies changed after freeze')
    execution_summary(store,plan)  # validate every existing cache BEFORE any call
    new_calls=reused=0
    for key,physical in plan['episodes'].items():
        # Recheck the COMPLETE frozen bundle before every dispatch.
        if load_bundle(store)[2]!=plan:raise StoreError('frozen bundle changed')
        try:
            store.read(f'episodes/{key}.json')
            execution_summary(store,plan)  # validates cached identity/category
            reused+=1;continue
        except FileNotFoundError:pass
        acquired=store.write_once(f'attempts/{key}.json',dict(episode_key=key,physical_identity=physical),exclusive=True)
        if not acquired:continue
        began=time.perf_counter()
        try:
            if runner is None:
                from grasp_a.sequence import run_sequence
                call=run_sequence
            else:call=runner
            new_calls+=1
            raw=call(physical['task'],physical['scene_seed'],physical['candidate_id'],candidates,
                config=SequenceConfig(**physical['sequence_config']),scene_config=SceneRandomization(**physical['scene_config']),
                observation_config=None,liquid=physical['liquid_mode'],gui=False,media_dir=None,show_landmarks=True)
        except Exception as exc:
            value=dict(episode_key=key,physical_identity=physical,category='infrastructure_error',raw_outcome=None,
                error=dict(type=type(exc).__name__,message=str(exc)),wall_s=time.perf_counter()-began)
        else:
            try:
                category=classify_outcome(raw,physical)
                digest(raw)  # reject nonfinite/non-serializable results too
                value=dict(episode_key=key,physical_identity=physical,category=category,raw_outcome=raw,
                    error=None,failure_diagnostics=failure_diagnostics(raw),wall_s=time.perf_counter()-began)
            except (ValueError,TypeError) as exc:
                value=dict(episode_key=key,physical_identity=physical,category='infrastructure_error',raw_outcome=None,
                    error=dict(type=type(exc).__name__,message=str(exc)),wall_s=time.perf_counter()-began)
        store.write_once(f'episodes/{key}.json',value)
    summary=execution_summary(store,plan)
    result=dict(status='pending_reconciliation' if summary['method_outcome_counts'].get('missing_outcome',0) else 'dispatch_complete',new_episode_calls=new_calls,reused_episode_records=reused,summary=summary)
    store.write_once(f'summaries/{digest(result)}.json',result)
    return result
