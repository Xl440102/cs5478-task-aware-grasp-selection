"""B-only generation diagnostics and retrospective pouring IK audit.

No diagnostic result filters candidates in selection experiments.
"""
from collections import Counter
from dataclasses import asdict
import argparse
import json
import math
from pathlib import Path
import time
import numpy as np
from grasp_a.contracts import Pose, candidate_sha256
from .features import candidate_features, tool_axes
from .models import ROOT, read_model
from .adaptive import AdaptiveConfig, generate_adaptive


def contact_key(candidate, model):
    """Ideal unordered contact pair; ignores approach and finger permutation.

    Quantized to micrometres. This is a geometric diagnostic, not robot or
    outcome equivalence. Antipodal approaches may share these same contacts.
    """
    contacts=candidate_features(candidate,model.geometry)['ideal_contacts_mug_m']
    if contacts is None:
        raise ValueError('unsupported contact family')
    return tuple(sorted(tuple(round(float(x),6) for x in p) for p in contacts))


def candidate_diagnostics(candidates, model, filtered):
    keys={c.id:contact_key(c,model) for c in candidates}
    # Parallel closing lines are antipodal-equivalent; round the signless
    # local tool-Y vector to micrometre-scale precision.
    directions=set()
    for c in candidates:
        axis=tool_axes(c.grasp_pose.quaternion)[1]
        direction=tuple(round(float(v if v>=0 else -v),6) for v in axis)
        # Canonicalize using the first component larger than 1e-6.
        first=next((v for v in axis if abs(v)>1e-6),1.)
        directions.add(tuple(round(float(v*(1 if first>0 else -1)),6) for v in axis))
    multiplicity=Counter(keys.values())
    feasible={r['id'] for r in filtered['feasible_candidates']}
    rejection=Counter(r for c in filtered['checks'] if not c['feasible'] for r in set(c['reasons']))
    primary=Counter(c['reasons'][0] for c in filtered['checks'] if not c['feasible'])
    primary=Counter(c['reasons'][0] for c in filtered['checks'] if not c['feasible'])
    return dict(generated=len(candidates),unique_contact_families=len(set(keys.values())),
                feasible=len(feasible),feasibility_rate=len(feasible)/len(candidates) if candidates else 0.,
                unique_contact_directions=len(directions),
                contact_family_multiplicity=dict(sorted(Counter(multiplicity.values()).items())),
                feasible_contact_families=len({keys[cid] for cid in feasible}),
                rejection_reason_counts=dict(sorted(rejection.items())),primary_rejection_counts=dict(sorted(primary.items())),
                rejection_count_policy='multi-label per rejected candidate; primary counts partition rejected candidates',
                families={cid:json.dumps(key,separators=(',',':')) for cid,key in keys.items()},
                family_definition='unordered ideal contact pair, rounded to 1 micrometre; not robot equivalence')


def pouring_audit(output, *, generation=AdaptiveConfig(), execute=True):
    import pybullet_data
    from grasp_a.sequence import SequenceScene, get_sequence_candidates, run_sequence
    from grasp_a.sequence_contracts import SequenceConfig
    import pybullet as p
    output=Path(output)
    if output.exists(): raise FileExistsError(output)
    output.mkdir(parents=True)
    model=read_model('hollow',Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf')
    candidates=generate_adaptive(model,generation)
    config=SequenceConfig()
    filtered=get_sequence_candidates('pour_water',0,candidates,config=config)
    ids={r['id'] for r in filtered['feasible_candidates']}
    report=dict(schema_version='b_pour_audit_v1',generation=asdict(generation),model=model.manifest(),
                candidate_sha256=candidate_sha256(candidates),config=asdict(config),seed=0,
                diagnostic_scope='post-selection diagnostics only; never selection eligibility',
                initial_filter=filtered,planning=[],executions=[])
    # Nominal kinematic probes: reset robot joints only inside saved state.
    # No real cup motion or liquid is simulated; no collision/dynamics claim.
    with SequenceScene('pour_water',0,config=config) as scene:
        for c in candidates:
            saved=p.saveState(physicsClientId=scene.client)
            start_index=len(scene.ik_diagnostics)
            try:
                carry=Pose((config.target_xy[0],config.target_xy[1]-.045,config.carry_height_m),(0,0,0,1))
                def tool(obj):
                    pos,q=p.multiplyTransforms(obj.position,obj.quaternion,c.grasp_pose.position,c.grasp_pose.quaternion)
                    return Pose(tuple(pos),tuple(q))
                target=tool(Pose(carry.position,tuple(p.getQuaternionFromEuler((math.radians(config.pour_angle_deg),0,0)))))
                q,_,_=scene.ik(tool(carry),pos_tol=.008,rot_tol_deg=3,stage='diagnostic_carry')
                path=None
                if q is not None:
                    scene._set_arm(q)
                    path=scene._plan_cartesian(target,'diagnostic_tilt')
                report['planning'].append(dict(id=c.id,initially_feasible=c.id in ids,
                    grasp_pose=asdict(c.grasp_pose),target_pose=asdict(target),
                    nominal_carry_ik=q is not None,nominal_tilt_path=path is not None,
                    ik_diagnostics=scene.ik_diagnostics[start_index:],
                    scope='nominal kinematic path from solved carry pose; no dynamics or collision guarantee'))
            finally:
                p.restoreState(stateId=saved,physicsClientId=scene.client)
                p.removeState(saved,physicsClientId=scene.client)
    _write(output/'report.json',report)
    if execute:
        for c in candidates:
            if c.id not in ids: continue
            start=time.perf_counter()
            result=run_sequence('pour_water',0,c.id,candidates,config=config)
            failed=[d for d in result['ik_diagnostics'] if not d['success'] and d['stage'].startswith('tilt')]
            report['executions'].append(dict(id=c.id,grasp_pose=asdict(c.grasp_pose),
                success=result['success'],failure_reason=result['failure_reason'],status=result['status'],
                reached_tilt=any(s['id']=='tilt' and s['control_status']!='not_executed' for s in result['stages']),
                completed_tilt=any(s['id']=='tilt' and s['control_status']=='completed' for s in result['stages']),
                failed_tilt_ik=failed,stages=result['stages'],seconds=time.perf_counter()-start,
                trajectory_scope='actual A execution diagnostics',episode=result))
            _write(output/'report.json',report)
    return report


def _write(path,data): path.write_text(json.dumps(data,indent=2,allow_nan=False)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--planning-only',action='store_true')
    args=parser.parse_args()
    if not args.out.resolve().is_relative_to((ROOT/'results/local').resolve()):
        parser.error('--out must be under results/local')
    pouring_audit(args.out,execute=not args.planning_only)
    print(args.out/'report.json')


if __name__=='__main__': main()
