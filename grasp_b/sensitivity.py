"""Offline parameter study; no execution outcomes, no fabricated measurements."""
from dataclasses import asdict, replace
import argparse
import json
from pathlib import Path
import pybullet_data
from .adaptive import generate_adaptive
from .models import ROOT, read_model
from .motion import command_context
from .risk import Physics, static_score, task_risk, rank_feasible
from grasp_a.contracts import Candidate, Pose
from .geometry import tool_orientation
from grasp_a.sequence_contracts import SequenceConfig


def study():
    model=read_model('hollow',Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf')
    q=tool_orientation(0,0)
    def grasp(z,yaw=0):
        from .geometry import approach_axis
        quat=tool_orientation(yaw,0)
        return Candidate('pose_'+str(z)+'_'+str(yaw),'body','side',.078,0.,Pose((0,0,z),quat),
                         Pose(tuple(p-.15*a for p,a in zip((0,0,z),approach_axis(yaw))),quat))
    candidates=[grasp(z) for z in (-.02,0,.02)]
    public=[c.public_metadata() for c in candidates]
    rows=[]
    for axis,values in [('speed_scale',(.5,1.,2.,4.)),('mass_scale',(.5,1.,2.)),
                        ('com_x_m',(-.025,0.,.025)),('com_z_m',(-.025,0.,.025)),
                        ('liquid_mass_kg',(0.,.1,.2)),('friction',(.3,.6,1.2))]:
        for value in values:
            changed=replace(model,mass_kg=model.mass_kg*value,
                            inertia_kg_m2=tuple(tuple(x*value for x in row) for row in model.inertia_kg_m2)) if axis=='mass_scale' else model
            if axis=='com_x_m':
                changed=replace(model,geometry=replace(model.geometry,com=(value,0,0)))
            if axis=='com_z_m':
                changed=replace(model,geometry=replace(model.geometry,com=(0,0,value)))
            physics=Physics(friction=value) if axis=='friction' else Physics()
            # Liquid load is defined only for pouring. Applying it to transport
            # or handover would create a physically inconsistent counterfactual.
            tasks = ('pour_water',) if axis == 'liquid_mass_kg' else (
                'transport_place','handover_transfer','pour_water')
            for task in tasks:
                context=command_context(task,speed_scale=value if axis=='speed_scale' else 1.)
                if axis=='liquid_mass_kg':
                    from dataclasses import replace as dc_replace
                    context=dc_replace(context,liquid_mass_kg=value,liquid_com_m=(0,0,-.025))
                ranking=rank_feasible(candidates,public,changed,context=context,physics=physics)
                rows.append(dict(axis=axis,value=value,task=task,ranking=ranking['ranking'],
                                 scores=[dict(id=r['candidate_id'],risk=r['risk'],score=r['score'],worst_stage=r['worst_stage']) for r in ranking['scores']]))
    angle_rows=[]
    for angle in (-80.,-90.,-110.,-135.):
        from dataclasses import replace as dc_replace
        ctx=command_context('pour_water',config=SequenceConfig(pour_angle_deg=angle))
        ranked=rank_feasible(candidates,public,model,context=ctx)
        angle_rows.append(dict(angle_deg=angle,ranking=ranked['ranking'],risks=[x['risk'] for x in ranked['scores']],
                               max_angular_acceleration_rad_s2=max(float(__import__('numpy').linalg.norm(s.alpha_world_rad_s2)) for s in ctx.samples)))
    # Same COM, different approach. Static ties; M2 detects physical occlusion.
    a,b=grasp(0,0),grasp(0,270)
    handover=[dict(pose=asdict(c.grasp_pose),static=static_score(c,model),
                   M2=task_risk(c,model,command_context('handover_transfer'))) for c in (a,b)]
    center,low=grasp(0),grasp(-.02)
    context=replace(command_context('pour_water'),liquid_mass_kg=.2,liquid_com_m=(0,0,-.025))
    pouring=[dict(pose=asdict(c.grasp_pose),static=static_score(c,model),M2=task_risk(c,model,context)) for c in (center,low)]
    # A-feasible generated set reused for a declared high-COM/fast-motion
    # counterfactual. Filter once on the actual A asset; altered mass properties
    # are NOT installed in A, so this is prediction sensitivity, not validation.
    from grasp_a.sequence import get_sequence_candidates
    generated=generate_adaptive(model)
    filtered=get_sequence_candidates('transport_place',0,generated)
    high_com=replace(model,geometry=replace(model.geometry,com=(0,0,.035)))
    context=command_context('transport_place',speed_scale=6.)
    static=rank_feasible(generated,filtered['feasible_candidates'],high_com)
    dynamic=rank_feasible(generated,filtered['feasible_candidates'],high_com,context=context)
    return dict(schema_version='b_sensitivity_v1',scope='offline assumed-state sensitivity, not execution or held-out evidence',
                model=model.manifest(),physics=asdict(Physics()),rows=rows,
                handover_contrast=handover,pouring_contrast=pouring,pouring_angle_sweep=angle_rows,
                feasible_counterfactual=dict(note='A filter on original asset; high COM .035 m and 6x speed only in B prediction',
                    feasible_count=len(filtered['feasible_candidates']),static=static,M2=dynamic))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    output=args.out.resolve()
    if not output.is_relative_to((ROOT/'results/local').resolve()):
        parser.error('--out must be under results/local')
    if output.exists():
        raise FileExistsError(output)
    data=study()
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(data,indent=2,allow_nan=False)+'\n')
    print(output)


if __name__=='__main__': main()
