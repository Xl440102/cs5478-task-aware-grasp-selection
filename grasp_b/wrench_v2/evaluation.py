"""B-only selection over a validated A-filter snapshot. Never executes a choice.

M0 sees IDs only. M1/M2 share a single caller-prepared contact/LP model.
Filter rejection diagnostics are logged but never used as ranking features.
"""
from collections import Counter
from dataclasses import asdict
import random
from time import perf_counter

import numpy as np

from grasp_a.contracts import candidate_sha256, Pose
from grasp_a.sequence_contracts import SequenceConfig, SEQUENCE_TASKS
from .contacts import _plain
from .m1 import score_prepared, fingerprint
from .m2 import score_trajectory, rank_m2_results, ExactWrenchCache, TrajectoryPolicy
from .ranking import rank_results
from .motion_prediction import PredictedPose, MotionModel, predict_motion
from .dynamics import predict_dynamics
from .tasks import build_task_wrench_set, axis_disturbances, pour_orientations_x

VERSION = "b_only_shared_feasible_selection_v1"


def validate_filter(candidates, record):
    """Bind every accepted/rejected ID to the exact ordered full input."""
    by_id = {c.id: c for c in candidates}
    if len(by_id) != len(candidates) or any(not c.id for c in candidates):
        raise ValueError("unique nonempty candidate IDs required")
    if record['candidate_sha256'] != candidate_sha256(candidates):
        raise ValueError("A filter candidate pool hash mismatch")
    if record['task_id'] not in SEQUENCE_TASKS or type(record['seed']) is not int:
        raise ValueError("invalid task/scene seed")
    if record['scene_randomization']['filter_mode'] != 'execution':
        raise ValueError("this integration requires execution-consistent filtering")
    checks = record['checks']
    if len(checks) != len(by_id) or {r['candidate_id'] for r in checks} != set(by_id):
        raise ValueError("filter checks must cover every candidate exactly once")
    if any(type(r['feasible']) is not bool for r in checks):
        raise ValueError("boolean feasibility required")
    public = record['feasible_candidates']
    ids = [r['id'] for r in public]
    if len(set(ids)) != len(ids) or set(ids) != {r['candidate_id'] for r in checks if r['feasible']}:
        raise ValueError("public feasible pool/checks mismatch")
    if any(r != by_id[r['id']].public_metadata() for r in public):
        raise ValueError("A public snapshot differs from original Candidate")
    return tuple(sorted(ids))


def random_ranking(ids, selector_seed):
    """Uniform permutation, independent local RNG; no scores/task/scene input."""
    if type(selector_seed) is not int or len(set(ids)) != len(ids):
        raise ValueError("integer selector seed and unique IDs required")
    ranking = sorted(ids)
    random.Random(selector_seed).shuffle(ranking)
    return dict(method_id='m0_uniform_random_v1', ranking=ranking,
        selected_id=ranking[0] if ranking else None,
        selection_status='random_choice' if ranking else 'empty_feasible_pool',
        selector_seed=selector_seed, ranking_policy='canonical_IDs_then_local_RNG_shuffle_v1',
        status_counts={'random_ranked': len(ids)}, scores=[])


def dry_task_sets(body, config):
    """Task04 finite TWS definition with explicit DRY payload, no retuning."""
    common = dict(reference_point_m=config.reference_point_m, gravity_world_m_s2=(0, 0, -9.81),
        context_source='Task07 development dry rigid mug; not A filled-fluid execution',
        disturbances=axis_disturbances(horizontal_force_n=.1, vertical_force_n=0,
            application_point_m=(0, 0, 0), source='explicit uncalibrated development +/-0.1 N fixture'))
    return {task: build_task_wrench_set(task, body, **common, **(
        dict(orientations=pour_orientations_x((0, -55*np.pi/180, -110*np.pi/180),
            source='finite absolute WORLD-X static attitude fixture; A endpoint -110 degrees'))
        if task == 'pour_water' else {})) for task in SEQUENCE_TASKS}


def wet_task_sets(body, config):
    """Same finite TWS parameters, explicit combined wet body, no second payload."""
    common=dict(reference_point_m=config.reference_point_m,gravity_world_m_s2=(0,0,-9.81),
        context_source='development rigid-water wet prediction; NOT fluid execution',
        disturbances=axis_disturbances(horizontal_force_n=.1,vertical_force_n=0,
            application_point_m=(0,0,0),source='unchanged uncalibrated +/-0.1 N development fixture'))
    return {'pour_water':build_task_wrench_set('pour_water',body,**common,
        orientations=pour_orientations_x((0,-55*np.pi/180,-110*np.pi/180),source='same finite WORLD-X attitudes as dry; not observed fluid'))}


def _ranking_export(ranked, ids, families):
    if len(ranked['ranking']) != len(ids) or set(ranked['ranking']) != set(ids):
        raise ValueError("method ranking is not a full feasible-ID permutation")
    if ranked['selected_id'] is not None and (ranked['selected_id'] not in ids or
                                             ranked['selected_id'] != ranked['ranking'][0]):
        raise ValueError("selected ID must be feasible and first-ranked")
    value = {k: v for k, v in ranked.items() if k != 'scores'}
    rows = ranked.get('scores', [])
    value['scores'] = [r.to_dict() for r in rows]
    value['status_counts'] = dict(Counter(r.status for r in rows))
    value['selected_family'] = families.get(ranked['selected_id'])
    value['input_feasible_ids'] = list(ids)
    value['input_feasible_ids_sha256'] = fingerprint(ids)
    return value


def evaluate_selection(candidates, filter_record, prepared_by_id, *, body, config,
                       task_sets, selector_seed, resolutions=(16, 32, 64), sequence_config=None,
                       load_model='dry', wet_payload=None):
    """Pure numerical selection AFTER A filtering. No outcome/episode interface.

    Prepared models may be reused across trials only with exact candidate/config
    identity. LP caches are local to this call and one candidate; resolutions
    may share exact math inputs but trials never share responses or decisions.
    """
    began = perf_counter()
    ids = validate_filter(candidates, filter_record)
    if not resolutions or len(set(resolutions)) != len(resolutions):
        raise ValueError('nonempty unique resolution list required')
    models = [MotionModel(samples_per_stage=n, interpolation='half_cosine') for n in resolutions]
    seq = SequenceConfig() if sequence_config is None else sequence_config
    if not isinstance(seq, SequenceConfig):
        raise ValueError('SequenceConfig required')
    task = filter_record['task_id']
    binding=None
    if load_model!='dry':
        from .wet_payload import LABEL,LATTICE_LABEL,validate_wet_object
        if load_model not in (LABEL,LATTICE_LABEL) or wet_payload is None or wet_payload.model_name!=load_model or task!='pour_water':
            raise ValueError('explicit supported Pour wet model/spec required')
        if wet_payload.config_hash!=fingerprint(asdict(seq)) or wet_payload.water_mass_kg!=seq.water_mass_kg:
            raise ValueError('water model and SequenceConfig mismatch')
        binding=validate_wet_object(body,wet_payload)
    elif wet_payload is not None or 'wet_payload' in body.provenance or 'payload' in body.provenance:
        raise ValueError('wet body/spec must not be labelled dry')
    task_set = task_sets.get(task)
    if task_set is None or task_set.task_name != task:
        raise ValueError('matching explicit task wrench set required')
    if any(fingerprint(s.object_parameters) != fingerprint(body) or
           s.reference_point_m != config.reference_point_m or
           s.gravity_world_m_s2 != (0., 0., -9.81) for s in task_set.scenarios):
        raise ValueError('M1/M2 must share object physics, gravity and reference')
    if filter_record['task_spec']['config'] != _plain(asdict(seq)):
        # A native in-memory tuple and JSON round-tripped list are equivalent.
        if fingerprint(filter_record['task_spec']['config']) != fingerprint(asdict(seq)):
            raise ValueError('filter/motion sequence config mismatch')
    by_id = {c.id: c for c in candidates}
    families = {}
    for cid in ids:
        if cid not in prepared_by_id:
            raise ValueError('missing prepared model for feasible ID')
        p = prepared_by_id[cid]
        if (p.spec.candidate_id, p.spec.candidate_hash) != (cid, candidate_sha256([by_id[cid]])) or fingerprint(p.config.to_dict()) != fingerprint(config.to_dict()):
            raise ValueError('prepared Candidate/config identity mismatch')
        families[cid] = p.spec.family.value
    initial = Pose.from_dict(filter_record['initial_object_pose_estimate'])
    pose = PredictedPose(initial.position, initial.quaternion)
    m0_start = perf_counter()
    m0 = random_ranking(ids, selector_seed)
    m0.update(input_feasible_ids=list(ids), input_feasible_ids_sha256=fingerprint(ids),
              selected_family=families.get(m0['selected_id']), runtime_s=perf_counter()-m0_start)
    report = dict(version=VERSION, task=task, scene_seed=filter_record['seed'], selector_seed=selector_seed,
        candidate_sha256=candidate_sha256(candidates), generated_count=len(candidates),
        feasible_ids=list(ids), feasible_count=len(ids), feasible_ids_sha256=fingerprint(ids),
        contact_status_counts=dict(Counter(prepared_by_id[c].spec.status.value for c in ids)),
        contact_model_identities={c: fingerprint(prepared_by_id[c].spec.to_dict()) for c in ids},
        methods={'M0': m0}, physics=config.to_dict(), body=_plain(body), payload_mode=load_model,
        observation_input=asdict(initial), filter_record=filter_record,
        policy=_plain(TrajectoryPolicy()), motion_models=[asdict(m) for m in models],
        execution_performed=False, execution_outcomes_used=False,
        method_input_boundaries={'M0':'feasible IDs + independent selector seed',
            'M1':'same IDs/contact/physics + finite static TWS',
            'M2':'same IDs/contact/physics + A observed pose estimate, nominal commands, analytical dynamics'},
        warning='kinematic feasibility is not physical task success; dry B load is not A filled pour' if load_model=='dry' else 'rigid-water prediction is not PBF or actual draining; all parameters uncalibrated')
    if binding is not None:
        report['load_model_identity']=binding
    t = perf_counter()
    static_rows = [score_prepared(prepared_by_id[c], task_set) for c in ids]
    evaluation_s = perf_counter()-t
    t = perf_counter()
    ranked = rank_results(static_rows, ids)
    ranking_s = perf_counter()-t
    t = perf_counter()
    m1 = _ranking_export(ranked, ids, families)
    m1['timings'] = dict(evaluation_s=evaluation_s, ranking_assembly_s=ranking_s,
        serialization_s=perf_counter()-t,
        lp_calls=sum(r.diagnostics.get('linprog_calls',0) for row in static_rows for _,r in row.scenario_results))
    report['methods']['M1'] = m1
    dynamic_rows = {n: [] for n in resolutions}
    timings = {n: dict(motion_s=0., dynamics_s=0.) for n in resolutions}
    for cid in ids:
        cache = ExactWrenchCache()
        for model in models:
            n = model.samples_per_stage
            t = perf_counter()
            motion = predict_motion(by_id[cid], task, initial_object_pose=pose,
                estimate_source='A initial_object_pose_estimate; simulator-derived sensor proxy if noisy_truth; not measured execution',
                model=model, sequence_config=seq)
            timings[n]['motion_s'] += perf_counter()-t
            t = perf_counter()
            dynamic = predict_dynamics(motion, body, reference_point_m=config.reference_point_m,
                gravity_world_m_s2=(0, 0, -9.81), source='dry static snapshot + analytical command dynamics, not rollout' if load_model=='dry' else 'explicit asset-bound rigid-water prediction + analytical dynamics; not fluid rollout')
            timings[n]['dynamics_s'] += perf_counter()-t
            dynamic_rows[n].append(score_trajectory(prepared_by_id[cid], motion, dynamic, cache=cache))
    for n, rows in dynamic_rows.items():
        t = perf_counter()
        ranked = rank_m2_results(rows, ids)
        ranking_s = perf_counter()-t
        t = perf_counter()
        result = _ranking_export(ranked, ids, families)
        result['timings'] = dict(timings[n], ranking_assembly_s=ranking_s,
            serialization_s=perf_counter()-t,
            evaluation_s=sum(r.timings_s['evaluation'] for r in rows),
            lp_calls=sum(r.timings_s['lp_calls'] for r in rows),
            cache_hits=sum(r.timings_s['cache_hits'] for r in rows))
        report['methods'][f'M2/{n}'] = result
    report['total_selection_s'] = perf_counter()-began
    if binding is not None:
        for name,value in report['methods'].items():
            value['load_model_identity']=binding
            value['experiment_method_id']=value['method_id']+'__'+load_model
    return report
