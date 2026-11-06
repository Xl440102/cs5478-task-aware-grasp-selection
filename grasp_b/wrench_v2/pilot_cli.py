"""Phase1 ONLY: freeze protocol, nine decisions and a deduplicated episode plan.

No --execute flag exists. Physical execution needs later explicit authorization.
python -B -m grasp_b.wrench_v2.pilot_cli --output-dir NEW_DIRECTORY
"""
import argparse
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
import json
import subprocess
from unittest.mock import patch

import pybullet_data
from grasp_a.contracts import candidate_sha256
from grasp_a.control import scene_randomization
from grasp_a.sequence_contracts import SequenceConfig,SEQUENCE_TASKS
from grasp_b.models import read_model
from grasp_b.multifamily import generate_multifamily,pruned_config,VERSION
from .adapter import GeneratorInfo
from .geometry import ObjectGeometry,read_gripper_geometry
from .m1 import development_config,prepare_candidate
from .dynamics import object_parameters_from_snapshot
from .physics_snapshot import static_physics_snapshot
from .wet_payload import build_wet_payload,combine_wet_object,LABEL
from .evaluation import dry_task_sets,wet_task_sets,evaluate_selection
from .evaluation_cli import filter_without_rollout
from .contacts import _plain
from .episode_store import EpisodeStore,digest
from .pilot import PilotProtocol,resources,freeze_bundle,load_bundle,execution_summary,ROOT


def compact_decision(trial):
    """Freeze all IDs/scores/statuses/identities, not repeated LP coefficient dumps.

    Prediction inputs live in protocol/candidates/filter_record; per-candidate
    model and motion hashes, worst samples and solver residuals remain visible.
    This does NOT drop unscored candidates or required-stage failure evidence.
    """
    for name,method in trial['methods'].items():
        rows=[]
        for row in method['scores']:
            value={k:v for k,v in row.items() if k not in ('samples','solver_results','scenario_results','provenance')}
            prov=row['provenance']
            value['model_trace']={k:v for k,v in prov.items() if k in (
                'config_hash','task_set_hash','primitive_fingerprint','contact_model_sha256',
                'motion_input_sha256','motion_version','dynamics_version','sample_status_counts',
                'excluded_samples','original_missing_inertia_samples','reference_point_m',
                'stage_max_utilization_PARTIAL')}
            if 'scenario_status_counts' in prov:value['scenario_status_counts']=prov['scenario_status_counts']
            catalog=row.get('solver_results',{})
            if 'scenario_results' in row:catalog={v['scenario_id']:v['solver_result'] for v in row['scenario_results']}
            value['solver_diagnostics']=[dict(status=r['status'],backend=r['backend_name'],version=r['backend_version'],
                diagnostics=r['diagnostics'],runtime_s=r['runtime_s']) for r in catalog.values()]
            rows.append(value)
        method['scores']=rows
    return trial


def prepare_pilot(output,*,samples=64):
    output=Path(output)
    if output.exists():raise FileExistsError('new pilot directory required')
    if samples not in (16,64):raise ValueError('explicit formal64 or development16 profile required')
    began=perf_counter();store=EpisodeStore(output)
    def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
    snapshot=static_physics_snapshot(ROOT)
    dry=object_parameters_from_snapshot(snapshot,asset_path=ROOT/'assets/mug_hollow.urdf')
    water=build_wet_payload();wet,binding=combine_wet_object(dry,water)
    panda=Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf'
    model=read_model('hollow',panda);obj,gripper=ObjectGeometry.from_model(model),read_gripper_geometry(panda)
    candidates,records=generate_multifamily(model,pruned_config())
    info={r['id']:GeneratorInfo('grasp_b.multifamily',VERSION,r['family'],r['id']) for r in records}
    cfg,seq,scene=development_config(),SequenceConfig(),scene_randomization('wide_strict_v2')
    dry_model=dict(model_label='dry',dry_object_hash=digest(dry),combined_object_hash=digest(dry),object_parameters=_plain(dry))
    protocol=PilotProtocol(candidate_pool_hash=candidate_sha256(candidates),resources=resources(),
        scene_config=asdict(scene),sequence_config=asdict(seq),scoring_config=cfg.to_dict(),
        prediction_models={SEQUENCE_TASKS[0]:dry_model,SEQUENCE_TASKS[1]:dry_model,
                           'pour_water':dict(binding,object_parameters=_plain(wet))},
        samples_per_stage=samples,purpose='formal_pilot_preparation' if samples==64 else 'development_dry_run')
    store.write_once('protocol.json',protocol.to_dict())
    store.write_once('candidates.json',[asdict(c) for c in candidates])
    store.write_once('preflight.json',dict(git_head=git('rev-parse','HEAD'),git_status=git('status','--short'),
        protocol_hash=protocol.protocol_hash,physical_execution_authorized=False,physics_snapshot=snapshot))
    sets=dry_task_sets(dry,cfg);sets['pour_water']=wet_task_sets(wet,cfg)['pour_water']
    prepared={};trials=[]
    for seed in protocol.scene_seeds:
        for task in protocol.tasks:
            print(f'Phase1 {task} seed={seed} samples={samples}',flush=True)
            record,filter_metrics=filter_without_rollout(task,seed,candidates,sequence_config=seq,scene_config=scene)
            allowed={r['id'] for r in record['feasible_candidates']}
            t=perf_counter();new=0
            for c in candidates:
                if c.id in allowed and c.id not in prepared:
                    prepared[c.id]=prepare_candidate(c,obj,gripper,generator_info=info[c.id],config=cfg);new+=1
            prep_s=perf_counter()-t
            is_wet=task=='pour_water'
            trial=evaluate_selection(candidates,record,prepared,body=wet if is_wet else dry,config=cfg,
                task_sets=sets,selector_seed=protocol.selector_seed,resolutions=(samples,),sequence_config=seq,
                load_model=LABEL if is_wet else 'dry',wet_payload=water if is_wet else None)
            trial.update(filter_metrics=filter_metrics,preparation_s=prep_s,new_prepared_candidates=new,
                rejection_counts=dict(Counter(reason for r in record['checks'] if not r['feasible'] for reason in r['reasons'])))
            trial=compact_decision(trial)
            store.write_once(f'decisions/{digest(trial)}.json',trial)
            trials.append(trial)
            print(json.dumps(dict(task=task,seed=seed,feasible=len(allowed),selected={k:v['selected_id'] for k,v in trial['methods'].items()})),flush=True)
    if digest(resources())!=digest(protocol.resources):raise ValueError('source/assets changed during Phase1; no executable plan frozen')
    plan=freeze_bundle(store,protocol,candidates,trials)
    load_bundle(store)
    summary=dict(protocol_hash=protocol.protocol_hash,plan_hash=digest(plan),phase1_complete=True,
        ready_for_physical_pilot=False,engineering_adapter_ready=True,
        blocking_condition='explicit human authorization bound to protocol and plan required',
        samples_per_stage=samples,purpose=protocol.purpose,expected_unique_episodes=plan['unique_episode_count'],
        method_decisions=plan['decision_count'],abstentions=plan['abstentions'],
        negative_margin_selections=plan['predicted_negative_margin_selections'],
        decision_hashes=[r['decision_hash'] for r in plan['decisions']],decision_summary=plan['decisions'],
        prepared_unique_candidates=len(prepared),wall_s=perf_counter()-began,
        filter_s=sum(t['filter_metrics']['runtime_s'] for t in trials),
        selection_s=sum(t['total_selection_s'] for t in trials),
        physical_execution_calls=0,execution_outcomes_created=0,
        outcome_inventory=execution_summary(store,plan))
    store.write_once('summary.json',summary)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',required=True,type=Path)
    p.add_argument('--development-16',action='store_true',help='separate labelled development protocol; formal default remains64')
    args=p.parse_args()
    # Independent guard covers the ENTIRE Phase1 path, not only A filtering.
    with (patch('grasp_a.sequence.run_sequence',side_effect=AssertionError('Task08B-0 forbids real episodes')),
          patch('pybullet.stepSimulation',side_effect=AssertionError('Phase1 cannot step physics'))):
        result=prepare_pilot(args.output_dir,samples=16 if args.development_16 else 64)
    print(json.dumps({k:v for k,v in result.items() if k not in ('decision_summary','outcome_inventory')},indent=2))


if __name__=='__main__':main()
