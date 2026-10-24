"""Static v2 and independent task risk APIs. Smaller risk is better.

Explicit candidate/model/context inputs only; no ID feature lookup or episodes.
These are dimensionless engineering proxies, not failure probabilities.
"""
from dataclasses import dataclass, replace
import math
import numpy as np
from .features import candidate_features, tool_axes
from .models import rotation
from .motion import MotionSample, TaskContext

STATIC_VERSION = 'static_load_margin_v2'
RISK_VERSION = 'task_trajectory_risk_v2'


@dataclass(frozen=True)
class Physics:
    friction: float = .6
    normal_capacity_per_finger_n: float = 15.
    torsion_radius_m: float = .008
    finger_half_height_m: float = .008
    clearance_reference_m: float = .02
    gravity_m_s2: float = 9.81

    def __post_init__(self):
        for value in self.__dict__.values():
            if type(value) not in (int,float) or not math.isfinite(value) or value <= 0:
                raise ValueError('finite positive physics assumptions required')


def features(candidate, model, physics=Physics()):
    raw = candidate_features(candidate, model.geometry)
    if raw['contact_model_status'] != 'ideal_body_point_contacts_only' or not raw['approach_consistent']:
        raise ValueError('requires supported horizontal body contact and consistent approach')
    contact_midpoint = np.mean(raw['ideal_contacts_mug_m'],axis=0)
    margin = min(candidate.grasp_pose.position[2]-model.body_bounds[0][2],
                 model.body_bounds[1][2]-candidate.grasp_pose.position[2])-physics.finger_half_height_m
    if candidate.gripper_width < 2*model.geometry.radius or candidate.gripper_width > model.geometry.max_opening or margin < 0:
        raise ValueError('insufficient aperture/contact edge margin')
    return dict(raw=raw, contact_midpoint_m=tuple(contact_midpoint), contact_margin_m=margin,
                com_offset_m=tuple(np.array(model.geometry.com)-contact_midpoint),
                contact_model='ideal outer-cylinder proxy; actual composite collisions remain A authority')


def load_sample(candidate, model, sample, context, physics=Physics(), *, feature_data=None):
    """Coulomb + soft-contact torsion surrogate; output forces N, moments N m."""
    f = feature_data if feature_data is not None else features(candidate,model,physics)
    rot = rotation(sample.rotation_rpy_rad)
    closing = rot @ np.array(tool_axes(candidate.grasp_pose.quaternion)[1])
    contact = np.array(f['contact_midpoint_m'])
    masses = [(model.mass_kg,np.array(model.geometry.com))]
    if context.liquid_mass_kg:
        masses.append((context.liquid_mass_kg,np.array(context.liquid_com_m)))
    omega,alpha = np.array(sample.omega_world_rad_s),np.array(sample.alpha_world_rad_s2)
    force, torque = np.zeros(3),np.zeros(3)
    for mass,com in masses:
        world_com = rot@com
        acceleration = np.array(sample.acceleration_world_m_s2)+np.cross(alpha,world_com)+np.cross(omega,np.cross(omega,world_com))
        load = mass*(acceleration-np.array((0,0,-physics.gravity_m_s2)))
        force += load
        torque += np.cross(rot@(com-contact),load)
    inertia = rot@np.array(model.inertia_kg_m2)@rot.T
    torque += inertia@alpha+np.cross(omega,inertia@omega)
    # Liquid point mass at declared fixed COM; rigid entrainment is only a
    # sensitivity proxy. No draining, slosh, deformation or dynamic pressure.
    normal = abs(float(force@closing))
    tangent = float(np.linalg.norm(force-(force@closing)*closing))
    torsion = abs(float(torque@closing))
    bending = float(np.linalg.norm(torque-(torque@closing)*closing))
    separation = model.geometry.radius
    required = normal/2 + tangent/(2*physics.friction) + bending/(2*physics.friction*separation) + torsion/(2*physics.friction*physics.torsion_radius_m)
    load_ratio = required/physics.normal_capacity_per_finger_n
    force_norm, torque_norm = math.hypot(*force), math.hypot(*torque)
    if not (np.isfinite(force).all() and np.isfinite(torque).all()
            and math.isfinite(required) and math.isfinite(load_ratio)
            and math.isfinite(force_norm) and math.isfinite(torque_norm)):
        raise ValueError('load calculation produced nonfinite force, moment or normalized demand')
    return dict(stage=sample.stage,time_s=sample.time_s,source=sample.source,
                force_n=force_norm,torque_nm=torque_norm,
                normal_force_required_per_finger_n=required,
                assumed_available_normal_force_per_finger_n=physics.normal_capacity_per_finger_n,
                normal_force_margin_per_finger_n=physics.normal_capacity_per_finger_n-required,
                normalized_force_margin=1-load_ratio,
                load_ratio=load_ratio,
                angle_rad=sample.rotation_rpy_rad,speed_m_s=sample.speed_m_s,
                angular_speed_rad_s=float(np.linalg.norm(omega)))


def static_score(candidate, model, physics=Physics()):
    f = features(candidate,model,physics)
    sample = MotionSample('static',0)
    context = TaskContext('transport_place',(sample,))
    load = load_sample(candidate,model,sample,context,physics,feature_data=f)
    edge_penalty = physics.finger_half_height_m/(f['contact_margin_m']+physics.finger_half_height_m)
    risk = load['load_ratio']+edge_penalty
    return dict(candidate_id=candidate.id,version=STATIC_VERSION,score=1/(1+risk),risk=risk,
                features=f,load=load,edge_penalty=edge_penalty,assumptions=physics.__dict__)


def _box(center, rotation_matrix, half):
    extent = np.abs(rotation_matrix)@np.array(half)
    return np.array(center)-extent,np.array(center)+extent


def _overlap(a,b):
    size = np.maximum(0,np.minimum(a[1],b[1])-np.maximum(a[0],b[0]))
    return float(np.prod(size))


def _gap(a,b):
    return float(np.linalg.norm(np.maximum(0,np.maximum(a[0]-b[1],b[0]-a[1]))))


def donor_envelopes(candidate, model, *, released=False):
    """Declared conservative pad/palm AABBs, NOT Panda mesh collision queries."""
    axes = np.array(tool_axes(candidate.grasp_pose.quaternion)).T
    point = np.array(candidate.grasp_pose.position)
    half_width = model.geometry.max_opening/2 if released else model.geometry.radius
    fingers = [_box(point+sign*(half_width+.004)*axes[:,1],axes,(.009,.004,.025)) for sign in (-1,1)]
    return fingers+[_box(point-.065*axes[:,2],axes,(.035,.05,.025))]


def transport_risk(candidate,model,context,physics=Physics()):
    f = features(candidate,model,physics)
    rows = [load_sample(candidate,model,s,context,physics,feature_data=f) for s in context.samples]
    worst = max(rows,key=lambda r:r['load_ratio'])
    edge = physics.finger_half_height_m/(f['contact_margin_m']+physics.finger_half_height_m)
    return dict(risk=worst['load_ratio']+edge,worst_stage=worst['stage'],
                max_load_ratio=worst['load_ratio'],contact_edge_penalty=edge,
                contact_margin_m=f['contact_margin_m'],com_offset_m=f['com_offset_m'],samples=rows)


def handover_risk(candidate,model,context,physics=Physics()):
    # A's receiver: base y=+.13, finger y offset=-.035, half-y=.025;
    # approach rail moves +.12 -> 0; open Z centers +/- .046, half-z=.006.
    receiver_sweep = (np.array((-.032,.070,-.052)),np.array((.032,.240,.052)))
    handle = tuple(np.array(v) for v in model.handle_bounds)
    volume = float(np.prod(handle[1]-handle[0]))
    records = []
    relevant = [s for s in context.samples if s.stage in
                ('receiver_approach','receiver_close','donor_release','task_motion','hold')]
    if not relevant:
        raise ValueError('handover context requires receiver/presentation stages')
    for sample in relevant:
        rot = rotation(sample.rotation_rpy_rad)
        for released in (False,True):
            # Transform conservative local envelopes to nominal transfer axes.
            boxes = [_box(rot@((b[0]+b[1])/2),rot,(b[1]-b[0])/2)
                     for b in donor_envelopes(candidate,model,released=released)]
            posed_handle = _box(rot@((handle[0]+handle[1])/2),rot,(handle[1]-handle[0])/2)
            gap = min(_gap(b,receiver_sweep) for b in boxes)
            occlusion = min(1.,sum(_overlap(b,posed_handle) for b in boxes)/volume)
            collision_fraction = min(1.,sum(_overlap(b,receiver_sweep) for b in boxes)/float(np.prod(receiver_sweep[1]-receiver_sweep[0])))
            accessible = min(1.,_overlap(posed_handle,receiver_sweep)/volume)
            approach = rot@np.array(tool_axes(candidate.grasp_pose.quaternion)[2])
            withdrawal = max(0.,float(-approach[1]))
            records.append(dict(stage=sample.stage,release_opening=released,
                receiver_gap_m=gap,handle_occlusion_fraction=occlusion,
                handle_accessible_fraction=accessible,swept_overlap_fraction=collision_fraction,
                withdrawal_toward_receiver=withdrawal,
                geometry_risk=occlusion+collision_fraction+(1-accessible)+withdrawal+
                    physics.clearance_reference_m/(gap+physics.clearance_reference_m)))
    hold = transport_risk(candidate,model,context,physics)
    worst = max(records,key=lambda r:r['geometry_risk'])
    stability_risk=hold['risk']
    return dict(risk=worst['geometry_risk']+stability_risk,
                worst_stage=worst['stage'],geometry=records,
                grasp_stability_risk=stability_risk,
                withdrawal_toward_receiver=max(r['withdrawal_toward_receiver'] for r in records),
                load=hold,receiver_model='A receiver swept AABB' if context.task_id=='handover_transfer' else 'hypothetical receiver envelope; prep has no receiver',
                pose_frame='nominal world transfer axes; donor/handle transformed by sample cup orientation')


def pouring_risk(candidate,model,context,physics=Physics()):
    load = transport_risk(candidate,model,context,physics)
    # Rim relative to TCP and donor envelope: opening must remain clear.
    radius,top = model.geometry.radius,model.body_bounds[1][2]
    rim = (np.array((-radius,-radius,top-.004)),np.array((radius,radius,top+.004)))
    boxes = donor_envelopes(candidate,model)
    gap = min(_gap(b,rim) for b in boxes)
    obstruction = min(1.,sum(_overlap(b,rim) for b in boxes)/float(np.prod(rim[1]-rim[0])))
    # Static handle interference remains geometric, independent of COM torque.
    handle = tuple(np.array(v) for v in model.handle_bounds)
    handle_obstruction = min(1.,sum(_overlap(b,handle) for b in boxes)/float(np.prod(handle[1]-handle[0])))
    clearance = physics.clearance_reference_m/(gap+physics.clearance_reference_m)
    stability_risk=load['risk']
    return dict(risk=stability_risk+obstruction+handle_obstruction+clearance,
                worst_stage=load['worst_stage'],load=load,rim_gap_m=gap,rim_obstruction_fraction=obstruction,
                grasp_stability_risk=stability_risk,
                handle_obstruction_fraction=handle_obstruction,rim_clearance_penalty=clearance,
                liquid_model='fixed mass at declared cup-local COM; quasi-static/rigid entrainment surrogate, no CFD or slosh',
                sampled_angle_range_rad=[min(s.rotation_rpy_rad[i] for s in context.samples) for i in range(3)],
                sampled_angle_max_rad=[max(s.rotation_rpy_rad[i] for s in context.samples) for i in range(3)])


def task_risk(candidate, model, context, physics=Physics()):
    evaluator = {'transport_place':transport_risk,'handover_transfer':handover_risk,
                 'handover_prep':handover_risk,'pour_water':pouring_risk,'pour_prep':pouring_risk}[context.task_id]
    result = evaluator(candidate,model,context,physics)
    return dict(candidate_id=candidate.id,version=RISK_VERSION,task_id=context.task_id,
                score=1/(1+result['risk']),assumptions=physics.__dict__,**result)


def rank_feasible(candidates, public_feasible, model, *, context=None, physics=Physics()):
    """Independent experimental API, with explicit full geometry inputs.

    A's caller-provided five-field list is authoritative. Every record must
    match its full Candidate. No candidate is dropped by scoring. Any unsupported
    candidate fails the comparison instead of changing methods' selection sets.
    """
    from .selectors import validate_public
    validate_public(public_feasible)
    if len({c.id for c in candidates}) != len(candidates):
        raise ValueError('duplicate raw IDs')
    by_id = {c.id:c for c in candidates}
    for public in public_feasible:
        if public['id'] not in by_id or public != by_id[public['id']].public_metadata():
            raise ValueError('feasible metadata does not match raw candidates')
    rows = [(task_risk(by_id[r['id']],model,context,physics) if context is not None
             else static_score(by_id[r['id']],model,physics)) for r in public_feasible]
    # Symmetric roll pairs must not be distinguished by roundoff in trig or
    # AABB arithmetic. Quantize only ranking keys; retain raw scores for audit.
    rows.sort(key=lambda r:(-round(r['score'],12),r['candidate_id']))
    return dict(ranking=[r['candidate_id'] for r in rows],scores=rows,ranking_score_decimals=12,
                version=RISK_VERSION if context is not None else STATIC_VERSION)
