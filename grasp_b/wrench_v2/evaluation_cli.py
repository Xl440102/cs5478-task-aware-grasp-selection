"""Opt-in B-only A-feasible selection smoke. No selected-candidate execution.

python -B -m grasp_b.wrench_v2.evaluation_cli --output-dir NEW_DIRECTORY
"""
import argparse
from collections import Counter
from dataclasses import asdict
from importlib.metadata import version
import hashlib
import json
from pathlib import Path
import platform
import subprocess
from time import perf_counter
from unittest.mock import patch

import pybullet_data

from grasp_a.contracts import candidate_sha256
from grasp_a.control import scene_randomization
from grasp_a.sequence_contracts import SequenceConfig, SEQUENCE_TASKS
from grasp_b.models import read_model
from grasp_b.multifamily import generate_multifamily, pruned_config, VERSION as GENERATOR_VERSION
from .adapter import GeneratorInfo
from .geometry import ObjectGeometry, read_gripper_geometry
from .m1 import development_config, prepare_candidate
from .physics_snapshot import static_physics_snapshot
from .dynamics import object_parameters_from_snapshot
from .evaluation import evaluate_selection, validate_filter, dry_task_sets, wet_task_sets, VERSION
from .wet_payload import LABEL, LATTICE_LABEL, build_wet_payload, combine_wet_object


def write_new(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def filter_without_rollout(task, seed, candidates, *, sequence_config, scene_config):
    """Call unchanged public A API, fail closed if anything attempts a step.

    Serial process-local guard; no parallel PyBullet clients while patched.
    A sets initial motor targets and resets poses for kinematic replay. Those
    operations do not integrate physics or execute a selected candidate.
    """
    import pybullet as p
    from grasp_a.sequence import get_sequence_candidates
    began = perf_counter()
    with patch.object(p, 'stepSimulation', side_effect=RuntimeError('physical stepping forbidden in Task07')) as guard:
        record = get_sequence_candidates(task, seed, candidates, config=sequence_config, scene_config=scene_config)
        if guard.call_count:
            raise RuntimeError('A filter attempted physical stepping')
    if (record['task_id'],record['seed']) != (task,seed):
        raise ValueError('A filter returned a different task/seed')
    validate_filter(candidates, record)
    return record, dict(runtime_s=perf_counter()-began, step_guard_enforced=True,
        physical_step_calls=guard.call_count,
        scope='execution-consistent donor waypoint IK/collision replay; carried mug follows nominal rigid grasp; no grasp closure/slip/fluid validation',
        a_scope_string_note='A API retains a legacy initial-only scope string; use filter_mode and actual replay source for interpretation')


def verified_cached_filter(directory,task,seed,candidates,*,sequence_config,scene_config,root):
    """Read only filter fields from historical selection-only records; fail closed.

    B scoring may evolve. A source/assets, generator, runtime dependencies and
    actual filter configuration must match, not merely the scene name or IDs.
    """
    began=perf_counter();directory=Path(directory);root=Path(root)
    manifest=json.loads((directory/'summary.json').read_text())
    if manifest.get('execution_performed') is not False or manifest['candidate_sha256']!=candidate_sha256(candidates):
        raise ValueError('cache is not a matching selection-only pool')
    if json.dumps(manifest['sequence_config'],sort_keys=True)!=json.dumps(asdict(sequence_config),sort_keys=True) or json.dumps(manifest['scene_config'],sort_keys=True)!=json.dumps(asdict(scene_config),sort_keys=True):
        raise ValueError('cached scene/filter/sequence configuration mismatch')
    for name in ('grasp_a/contracts.py','grasp_a/sequence.py','grasp_a/control.py','grasp_a/simulation.py','grasp_a/sequence_contracts.py','grasp_b/multifamily.py','grasp_b/models.py'):
        if manifest['source_sha256'].get(name)!=hashlib.sha256((root/name).read_bytes()).hexdigest():raise ValueError('stale filter source: '+name)
    result=subprocess.run(['git','diff','--quiet',manifest['git_head'],'--','grasp_a','assets'],cwd=root)
    if result.returncode!=0:raise ValueError('A code/assets changed since filter provenance commit')
    for name in ('numpy','pybullet'):
        if manifest['environment'][name]!=version(name):raise ValueError('filter dependency version mismatch')
    if manifest['environment']['python']!=platform.python_version():raise ValueError('filter Python version mismatch')
    for entry in manifest['static_snapshot']['assets'].values():
        path=Path(entry['asset_path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['asset_sha256']:raise ValueError('stale cached asset')
    rows=[r for r in manifest['trials'] if (r['task'],r['scene_seed'])==(task,seed)]
    if len(rows)!=1:raise ValueError('unique cached task/seed required')
    path=Path(rows[0]['artifact'])
    if path.is_absolute() or '..' in path.parts:raise ValueError('invalid cached artifact path')
    raw=json.loads((directory/path).read_text())
    if raw.get('execution_performed') is not False:raise ValueError('not a selection-only artifact')
    historical=raw.get('contact_model_identities',{})
    if historical:
        # Asset identities only, never historical scores or chosen IDs.
        g=read_gripper_geometry(Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf')
        for row in raw['methods']['M1']['scores']:
            source=row['provenance']['contact_spec']['provenance']
            if source['panda_sha256']!=g.panda_sha256 or source['palm_mesh_sha256']!=g.palm_sha256 or source['finger_mesh_sha256']!={f.finger_id:f.sha256 for f in g.fingers}:
                raise ValueError('cached gripper mesh identity mismatch')
    record=raw['filter_record']  # NO method scores/rankings/outcomes as selector inputs
    if (record['task_id'],record['seed'])!=(task,seed) or record['scene_randomization']!=manifest['scene_config'] or record['task_spec']['config']!=manifest['sequence_config']:
        raise ValueError('cached trial identity mismatch')
    ids=validate_filter(candidates,record)
    if list(ids)!=rows[0]['feasible_ids'] or raw['feasible_ids']!=list(ids):raise ValueError('cached feasible IDs mismatch')
    if raw['filter_metrics'].get('physical_step_calls')!=0 or not raw['filter_metrics'].get('step_guard_enforced'):
        raise ValueError('missing no-step filter evidence')
    return record,dict(runtime_s=perf_counter()-began,cache_hit=True,
        source_artifact=str(directory/path),source_artifact_sha256=hashlib.sha256((directory/path).read_bytes()).hexdigest(),
        original_filter_runtime_s=raw['filter_metrics']['runtime_s'],physical_step_calls=0,
        scope='verified historical A execution filter, no new A calls; only initial/filter data consumed')


def run_selection(output, *, seeds=(0, 1), selector_seed=2305478,
                  tasks=SEQUENCE_TASKS, resolutions=(16, 32, 64), scene='wide_strict_v2',
                  load_model='dry', water_radius_m=None, cached_filter_dir=None):
    output = Path(output)
    sc = scene_randomization(scene)
    if sc.filter_mode != 'execution':
        raise ValueError('execution filter required')
    if not seeds or len(set(seeds)) != len(seeds) or any(type(s) is not int for s in seeds):
        raise ValueError('unique integer scene seeds required')
    if type(selector_seed) is not int or not tasks or len(set(tasks)) != len(tasks) or any(t not in SEQUENCE_TASKS for t in tasks):
        raise ValueError('integer selector seed and unique supported tasks required')
    if not resolutions or len(set(resolutions)) != len(resolutions) or any(type(n) is not int or n < 3 for n in resolutions):
        raise ValueError('unique integer sample counts >=3 required')
    if load_model not in ('dry',LABEL,LATTICE_LABEL) or (load_model!='dry' and tuple(tasks)!=('pour_water',)) or (load_model=='dry' and water_radius_m is not None):
        raise ValueError('wet models are explicit Pour-only opt-ins; dry cannot carry water overrides')
    if output.exists():
        raise FileExistsError('refusing existing output directory')
    root = Path(__file__).resolve().parents[2]
    output.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    snapshot = static_physics_snapshot(root)
    body = object_parameters_from_snapshot(snapshot, asset_path=root/'assets/mug_hollow.urdf')
    panda = Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf'
    model = read_model('hollow', panda)
    geometry, gripper = ObjectGeometry.from_model(model), read_gripper_geometry(panda)
    candidates, records = generate_multifamily(model, pruned_config())
    cfg, seq = development_config(), SequenceConfig()
    wet_payload=None;load_binding=None
    if load_model!='dry':
        wet_payload=build_wet_payload(seq,radius_m=water_radius_m,model_name=load_model)
        body,load_binding=combine_wet_object(body,wet_payload)
    task_sets = dry_task_sets(body, cfg) if load_model=='dry' else wet_task_sets(body,cfg)
    infos = {r['id']: GeneratorInfo('grasp_b.multifamily', GENERATOR_VERSION, r['family'], r['id']) for r in records}
    def git(*args):
        return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    sources = list((root/'grasp_b/wrench_v2').glob('*.py')) + [root/p for p in (
        'grasp_a/contracts.py','grasp_a/sequence.py','grasp_a/control.py','grasp_a/simulation.py',
        'grasp_a/sequence_contracts.py','grasp_b/multifamily.py','grasp_b/models.py')]
    manifest = dict(version=VERSION, scope='development_A_execution_feasible_selection_only',
        git_head=git('rev-parse','HEAD'), git_branch=git('branch','--show-current'), git_status=git('status','--short'),
        source_sha256={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        environment=dict(python=platform.python_version(), **{k:version(k) for k in ('numpy','scipy','pybullet')}),
        pool_count=len(candidates), candidate_sha256=candidate_sha256(candidates),
        generator=dict(name='grasp_b.multifamily', version=GENERATOR_VERSION, config=asdict(pruned_config())),
        static_snapshot=snapshot, physics=cfg.to_dict(), sequence_config=asdict(seq),
        scene_config=asdict(sc), seeds=list(seeds), selector_seed=selector_seed,
        seed_policy='selector seed is explicitly supplied, never derived from scene seed; local RNG; no global state',
        payload_mode='dry B scoring; A filter is kinematic, does not execute filled pour',
        m1_task_sets={k:v.to_dict() for k,v in task_sets.items() if k in tasks},
        resolutions=list(resolutions), motion_interpolation='half_cosine',
        cache_policy='prepared geometry/LP per immutable candidate reused within run; exact wrench cache per candidate within trial; no cross-trial decisions or LP cache',
        scene_observation_note='wide_strict_v2 A derives noisy pose estimates from randomized initial simulator pose; this is a sensor proxy, not real perception or execution feedback',
        execution_performed=False, trials=[])
    if load_binding is not None:
        manifest.update(payload_mode=load_model,load_model_identity=load_binding)
    write_new(output/'manifest.json', manifest)
    write_new(output/'candidates.input.json', [asdict(c) for c in candidates])
    prepared = {}
    for seed in seeds:
        for task in tasks:
            print(f'filter {task} seed={seed}, {len(candidates)} generated', flush=True)
            if cached_filter_dir is None:
                record, filter_metrics = filter_without_rollout(task, seed, candidates, sequence_config=seq, scene_config=sc)
            else:
                record, filter_metrics = verified_cached_filter(cached_filter_dir,task,seed,candidates,sequence_config=seq,scene_config=sc,root=root)
            ids = validate_filter(candidates, record)
            preparation_s = 0.
            new_preparations = 0
            for c in candidates:
                if c.id in ids and c.id not in prepared:
                    t = perf_counter()
                    prepared[c.id] = prepare_candidate(c, geometry, gripper, generator_info=infos[c.id], config=cfg)
                    preparation_s += perf_counter()-t
                    new_preparations += 1
            trial = evaluate_selection(candidates, record, prepared, body=body, config=cfg,
                task_sets=task_sets, selector_seed=selector_seed, resolutions=resolutions, sequence_config=seq,
                load_model=load_model,wet_payload=wet_payload)
            trial.update(filter_metrics=filter_metrics, preparation_s=preparation_s,
                new_prepared_candidates=new_preparations,
                prepared_model_timings_s={c:dict(prepared[c].timings_s) for c in ids},
                rejection_counts=dict(Counter(reason for r in record['checks'] if not r['feasible'] for reason in r['reasons'])))
            filename=f'{task}_scene{seed}_selector{selector_seed}.json'
            write_new(output/filename, trial)
            summary = dict(task=task, scene_seed=seed, feasible_count=len(ids), feasible_ids=list(ids),
                contact_status_counts=trial['contact_status_counts'],filter_metrics=filter_metrics,
                selection_s=trial['total_selection_s'], preparation_s=preparation_s, rejection_counts=trial['rejection_counts'],
                artifact=filename, methods={k:{field:v.get(field) for field in (
                    'method_id','selected_id','selected_family','status_counts','selection_status','ranking','timings')} for k,v in trial['methods'].items()})
            manifest['trials'].append(summary)
            print(json.dumps(dict(task=task,seed=seed,feasible_count=len(ids),selected={k:v['selected_id'] for k,v in trial['methods'].items()})),flush=True)
    manifest['total_wall_s']=perf_counter()-started
    manifest['prepared_unique_candidates']=len(prepared)
    write_new(output/'summary.json', manifest)
    return manifest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--scene-seeds',type=int,nargs='+',default=[0,1])
    p.add_argument('--selector-seed',type=int,default=2305478)
    p.add_argument('--tasks',nargs='+',choices=SEQUENCE_TASKS,default=SEQUENCE_TASKS)
    p.add_argument('--samples',type=int,nargs='+',default=[16,32,64])
    p.add_argument('--scene',choices=('wide_strict_v2','wide_strict'),default='wide_strict_v2')
    p.add_argument('--load-model',choices=('dry',LABEL,LATTICE_LABEL),default='dry')
    p.add_argument('--water-radius-m',type=float)
    p.add_argument('--cached-filter-dir',type=Path)
    args=p.parse_args()
    result=run_selection(args.output_dir,seeds=tuple(args.scene_seeds),selector_seed=args.selector_seed,
        tasks=tuple(args.tasks),resolutions=tuple(args.samples),scene=args.scene,
        load_model=args.load_model,water_radius_m=args.water_radius_m,cached_filter_dir=args.cached_filter_dir)
    print(json.dumps(dict(total_wall_s=result['total_wall_s'],trials=len(result['trials']))))


if __name__=='__main__':
    main()
