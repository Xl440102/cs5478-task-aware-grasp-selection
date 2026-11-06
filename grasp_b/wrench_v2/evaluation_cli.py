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
from .evaluation import evaluate_selection, validate_filter, dry_task_sets, VERSION


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


def run_selection(output, *, seeds=(0, 1), selector_seed=2305478,
                  tasks=SEQUENCE_TASKS, resolutions=(16, 32, 64), scene='wide_strict_v2'):
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
    task_sets = dry_task_sets(body, cfg)
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
    write_new(output/'manifest.json', manifest)
    write_new(output/'candidates.input.json', [asdict(c) for c in candidates])
    prepared = {}
    for seed in seeds:
        for task in tasks:
            print(f'filter {task} seed={seed}, {len(candidates)} generated', flush=True)
            record, filter_metrics = filter_without_rollout(task, seed, candidates, sequence_config=seq, scene_config=sc)
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
                task_sets=task_sets, selector_seed=selector_seed, resolutions=resolutions, sequence_config=seq)
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
    args=p.parse_args()
    result=run_selection(args.output_dir,seeds=tuple(args.scene_seeds),selector_seed=args.selector_seed,
        tasks=tuple(args.tasks),resolutions=tuple(args.samples),scene=args.scene)
    print(json.dumps(dict(total_wall_s=result['total_wall_s'],trials=len(result['trials']))))


if __name__=='__main__':
    main()
