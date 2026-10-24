"""Paired local B experiment; python -m grasp_b.benchmark --execute.

Standardized paired comparison: M0 Random, M1 Legacy primary, M1 Static v2 ablation, and M2 task-aware risk.
"""
from dataclasses import asdict
import argparse
import hashlib
import json
import math
from pathlib import Path
import tempfile
import time

from grasp_a.contracts import candidate_sha256
from .adaptive import AdaptiveConfig, generate_adaptive
from .models import ROOT, read_model
from .motion import command_context
from .risk import Physics, rank_feasible
from .scoring import score_geometry, GEOMETRY_SCORE_VERSION
from .prototype import select_random
from .generation import write_candidates
from .diagnostics import candidate_diagnostics, contact_key

TASKS = ('transport_place','handover_transfer','pour_water','pour_prep','handover_prep')


def agreement(a,b):
    """Pairwise concordance in [0,1]; null with <2 candidates."""
    if len(a) < 2:
        return None
    index = {cid:i for i,cid in enumerate(b)}
    return sum(index[a[i]] < index[a[j]] for i in range(len(a)) for j in range(i+1,len(a)))/(len(a)*(len(a)-1)/2)


def distribution(values):
    if not values:
        return dict(count=0,min=None,max=None,mean=None,unique_6dp=0)
    return dict(count=len(values),min=min(values),max=max(values),mean=sum(values)/len(values),
                unique_6dp=len({round(v,6) for v in values}))


def run_benchmark(output, *, tasks=TASKS, seeds=(0,), execute=False, physics=Physics(),
                  generation=AdaptiveConfig(), speed_scale=1., sequence_config=None):
    import pybullet_data
    from grasp_a.sequence import get_sequence_candidates, run_sequence
    from grasp_a.simulation import get_feasible_candidates, run_episode
    from grasp_a.control import ObservationConfig
    from grasp_a.sequence_contracts import SequenceConfig
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    if not tasks or any(t not in TASKS for t in tasks) or not seeds or any(type(s) is not int for s in seeds):
        raise ValueError('explicit supported tasks and integer seeds required')
    config = sequence_config or SequenceConfig()
    observation = ObservationConfig()
    output.mkdir(parents=True)
    panda = Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf'
    report = dict(schema_version='b_comparison_v2',scope='paired_M0_M1_M2_baseline_comparison_development_protocol',
                  seed_effects={
                      'scene_seed':'A randomizes actual cup XY by +/-8 mm and world yaw by +/-0.08 rad; its feasibility probe uses the frozen observation estimate',
                      'selection_seed':'same integer seed drives M0 local RNG permutation; M1/M2 are deterministic for a given feasible list',
                      'noise_seed':'observation noise remains fixed at the shared ObservationConfig (zero by default)'},
                  method_definitions={'M0':'seeded random selection on A-feasible five-field records',
                                      'M1':'task-independent static geometry and physical proxy score; legacy is primary, static_v2 is an internal refinement/ablation',
                                      'M2':'task-specific handcrafted trajectory/space risk rule over explicit B geometry/context'},
                  physics=asdict(physics),generation=asdict(generation),speed_scale=speed_scale,
                  sequence_config=asdict(config),observation_config=asdict(observation),execute=execute,
                  candidate_sets={},trials=[],summary={},
                  source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in sorted((ROOT/'grasp_b').glob('*.py'))})
    prepared = {}
    for profile in sorted({'solid' if t.endswith('_prep') else 'hollow' for t in tasks}):
        started = time.perf_counter()
        model = read_model(profile,panda)
        candidates = generate_adaptive(model,generation)
        elapsed = time.perf_counter()-started
        write_candidates(output/f'{profile}.candidates.json',candidates)
        prepared[profile] = (model,candidates)
        report['candidate_sets'][profile] = dict(model=model.manifest(),count=len(candidates),
            candidate_sha256=candidate_sha256(candidates),generation_seconds=elapsed)
    for task in tasks:
        profile = 'solid' if task.endswith('_prep') else 'hollow'
        model,candidates = prepared[profile]
        context = command_context(task,config=config,speed_scale=speed_scale)
        for seed in seeds:
            start = time.perf_counter()
            filtered = (get_feasible_candidates(seed,candidates,observation_config=observation) if profile=='solid' else
                        get_sequence_candidates(task,seed,candidates,config=config,observation_config=observation))
            filter_seconds = time.perf_counter()-start
            public = filtered['feasible_candidates']
            generation_diag=candidate_diagnostics(candidates,model,filtered)
            family={cid:value for cid,value in generation_diag['families'].items()}
            trial = dict(task_id=task,seed=seed,candidate_sha256=candidate_sha256(candidates),
                         generation_count=len(candidates),feasible_count=len(public),
                         feasibility_rate=len(public)/len(candidates),filter_seconds=filter_seconds,
                         generation_seconds=report['candidate_sets'][profile]['generation_seconds'],
                         contact_diagnostics=generation_diag,
                         rejection_reason_distribution=generation_diag['rejection_reason_counts'],
                         filter=filtered,motion_context=asdict(context),methods={},agreement={})
            report['trials'].append(trial)
            if public:
                start=time.perf_counter()
                random = select_random(public,task_id=task,seed=seed)
                trial['methods']['M0']=dict(method_id='M0_RANDOM',ranking=random['ranking'],
                    ranking_seconds=time.perf_counter()-start,score_used=False,selection_seed=seed)
                feasible_ids = {r['id'] for r in public}
                start=time.perf_counter()
                scoring=json.loads((ROOT/'configs/part_b/scoring_experimental_v1.json').read_text())
                if scoring.get('version') != GEOMETRY_SCORE_VERSION:
                    raise ValueError('legacy scoring version mismatch')
                legacy = [score_geometry(c,model.geometry,scoring['weights']) for c in candidates if c.id in feasible_ids]
                if any(r['status']!='ok' for r in legacy):
                    raise ValueError('legacy comparison unsupported; no method-specific candidate deletion allowed')
                legacy.sort(key=lambda r:(-r['score'],r['candidate_id']))
                trial['methods']['M1_LEGACY']=dict(method_id='M1_LEGACY',version=GEOMETRY_SCORE_VERSION,
                    ranking=[r['candidate_id'] for r in legacy],scores=legacy,ranking_seconds=time.perf_counter()-start)
                for method,ctx in [('M1_STATIC_V2',None),('M2',context)]:
                    start=time.perf_counter()
                    result=rank_feasible(candidates,public,model,context=ctx,physics=physics)
                    trial['methods'][method]=dict(result,method_id=method,ranking_seconds=time.perf_counter()-start)
                for name,result in trial['methods'].items():
                    result['selected_id']=result['ranking'][0]
                    result['selected_contact_family']=family[result['selected_id']]
                    result['ranking_contact_families']=[family[cid] for cid in result['ranking']]
                    result['score_distribution']=distribution([r['score'] for r in result.get('scores',[])])
                    result['execution']=None
                names=list(trial['methods'])
                for i,a in enumerate(names):
                    for b in names[i+1:]:
                        ar,br=trial['methods'][a]['ranking'],trial['methods'][b]['ranking']
                        trial['agreement'][a+'__'+b]=dict(pairwise_concordance=agreement(ar,br),
                                                        top1_agrees=ar[0]==br[0])
            _write(output/'report.json',report)
            # Decisions are persisted before execution. No outcomes affect ranking.
            if execute:
                shared = {}
                for result in trial['methods'].values():
                    if result['selected_id'] in shared:
                        result['execution']=shared[result['selected_id']]
                        result['execution_seconds']=0.
                        result['execution_reused_same_task_seed_id']=True
                        continue
                    start=time.perf_counter()
                    result['execution']=(run_episode(task,seed,result['selected_id'],candidates,observation_config=observation)
                        if profile=='solid' else run_sequence(task,seed,result['selected_id'],candidates,
                                                              config=config,observation_config=observation))
                    shared[result['selected_id']]=result['execution']
                    result['execution_reused_same_task_seed_id']=False
                    result['execution_seconds']=time.perf_counter()-start
                    _write(output/'report.json',report)
    for task in tasks:
        trials=[t for t in report['trials'] if t['task_id']==task]
        for method in ('M0','M1_LEGACY','M1_STATIC_V2','M2'):
            results=[t['methods'][method] for t in trials if method in t['methods']]
            episodes=[r['execution'] for r in results if r['execution'] is not None]
            success=sum(bool(e['success']) for e in episodes)
            report['summary'][task+'/'+method]=dict(trials=len(trials),covered=len(results),executed=len(episodes),successes=success,
                conditional_execution_success_rate=success/len(episodes) if episodes else None,
                end_to_end_success_rate=success/len(trials) if execute else None,
                coverage=len(results)/len(trials),
                mean_ranking_seconds=sum(r['ranking_seconds'] for r in results)/len(results) if results else None)
    _write(output/'report.json',report)
    return report


def _write(path,data):
    path.write_text(json.dumps(data,indent=2,allow_nan=False)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tasks',nargs='+',choices=TASKS,default=list(TASKS))
    parser.add_argument('--seeds',nargs='+',type=int,default=list(range(10)))
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--speed-scale',type=float,default=1.)
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    if args.out:
        output=args.out.resolve()
        if not output.is_relative_to((ROOT/'results/local').resolve()):
            parser.error('--out must be inside results/local')
    else:
        local=ROOT/'results/local'
        local.mkdir(exist_ok=True,parents=True)
        output=Path(tempfile.mkdtemp(prefix='b_risk_',dir=local))/'experiment'
    run_benchmark(output,tasks=args.tasks,seeds=args.seeds,execute=args.execute,speed_scale=args.speed_scale)
    print(output/'report.json')


if __name__=='__main__':
    main()
