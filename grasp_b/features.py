"""Offline geometric diagnostics ONLY; never selector-visible under v0.3.

No physics, episode results, task labels or candidate-ID lookup is used.
"""
from dataclasses import asdict
import hashlib
import json
import math

from grasp_a.contracts import Pose

VERSION = 'cylinder_diagnostics_v1'


def dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def norm(v):
    return math.sqrt(dot(v, v))


def tool_axes(quaternion):
    q = Pose.from_dict({'position': [0, 0, 0], 'quaternion': quaternion}).quaternion
    x, y, z, w = q
    return ((1-2*(y*y+z*z), 2*(x*y+w*z), 2*(x*z-w*y)),
            (2*(x*y-w*z), 1-2*(x*x+z*z), 2*(y*z+w*x)),
            (2*(x*z+w*y), 2*(y*z-w*x), 1-2*(x*x+y*y)))


FORMULAS = {
    'local_position_m': ('Candidate.grasp_pose.position, mug-local TCP', 'm'),
    'local_quaternion_xyzw': ('normalized Candidate.grasp_pose.quaternion', '1'),
    'approach_axis_mug': ('R(q) * (0,0,1)', '1'),
    'closing_axis_mug': ('R(q) * (0,1,0)', '1'),
    'ideal_contacts_mug_m': ('p+t*u; solve (px+t*ux)^2+(py+t*uy)^2=r^2; horizontal u only', 'm'),
    'height_m': ('TCP.z in mug frame', 'm'),
    'azimuth_deg': ('atan2(approach.y, approach.x) modulo 360; null if vertical', 'deg'),
    'opening_margin_m': ('total jaw opening - 2*body radius (diameter reference only)', 'm'),
    'pregrasp_offset_m': ('dot(grasp-pregrasp, tool_Z)', 'm'),
    'approach_residual_m': ('norm(delta - dot(delta,tool_Z)*tool_Z)', 'm'),
    'contact_alignment_proxy': ('min(abs(dot(side normal, tool_Y)))', '1'),
    'contact_symmetry_proxy': ('1-abs(abs(t_minus)-abs(t_plus))/(abs(t_minus)+abs(t_plus))', '1'),
    'contact_midpoint_com_distance_m': ('norm((contact_minus+contact_plus)/2 - URDF COM)', 'm'),
}


def candidate_features(candidate, geometry):
    """Intersect a horizontal closing line through TCP with the finite body.

    Ideal point contacts only; excludes handle, finger pads, mesh compliance,
    friction and reachability. Unsupported contact geometry yields null proxies.
    """
    pose = Pose.from_dict(asdict(candidate.grasp_pose))
    pre = Pose.from_dict(asdict(candidate.pregrasp_pose))
    _, closing, approach = tool_axes(pose.quaternion)
    delta = tuple(a-b for a, b in zip(pose.position, pre.position))
    distance = dot(delta, approach)
    residual = norm(tuple(d-distance*a for d, a in zip(delta, approach)))
    values = dict(height_m=pose.position[2],
                  azimuth_deg=(math.degrees(math.atan2(approach[1], approach[0])) % 360
                               if math.hypot(*approach[:2]) > 1e-10 else None),
                  opening_margin_m=candidate.gripper_width-2*geometry.radius,
                  pregrasp_offset_m=distance, approach_residual_m=residual,
                  local_position_m=pose.position, local_quaternion_xyzw=pose.quaternion,
                  approach_axis_mug=approach, closing_axis_mug=closing,
                  contact_alignment_proxy=None, contact_symmetry_proxy=None,
                  contact_midpoint_com_distance_m=None)
    status, contacts = 'unsupported_closing_axis_or_height', None
    x, y, z = pose.position
    if abs(closing[2]) < 1e-8 and abs(z) < geometry.height/2:
        a = dot(closing[:2], closing[:2])
        b = 2*dot((x, y), closing[:2])
        c = x*x+y*y-geometry.radius**2
        discriminant = b*b-4*a*c
        status = 'closing_line_misses_body_or_tcp_outside'
        if discriminant > 1e-16 and c < 0:
            roots = ((-b-math.sqrt(discriminant))/(2*a),
                     (-b+math.sqrt(discriminant))/(2*a))
            contacts = [tuple(v+t*d for v, d in zip(pose.position, closing)) for t in roots]
            normals = [(p[0]/geometry.radius, p[1]/geometry.radius, 0) for p in contacts]
            midpoint = tuple((a+b)/2 for a, b in zip(*contacts))
            values.update(contact_alignment_proxy=min(abs(dot(n, closing)) for n in normals),
                          contact_symmetry_proxy=1-abs(abs(roots[0])-abs(roots[1]))/
                          (abs(roots[0])+abs(roots[1])),
                          contact_midpoint_com_distance_m=norm(tuple(a-b for a,b in
                                                               zip(midpoint, geometry.com))))
            status = 'ideal_body_point_contacts_only'
    return dict(id=candidate.id, values=values, contact_model_status=status,
                ideal_contacts_mug_m=contacts,
                approach_consistent=distance > 0 and residual <= 1e-8)


def equivalent_pairs(candidates, position_tolerance_m=1e-6, axis_tolerance=1e-6):
    """Pairwise approximate contact-family equivalence; NOT robot equivalence.

    Same grasp/pregrasp, width and approach; closing axis sign is ignored.
    Pairwise tolerances are not transitive; no clustering/representative deletion.
    """
    if not all(math.isfinite(t) and t >= 0 for t in (position_tolerance_m, axis_tolerance)):
        raise ValueError('invalid equivalence tolerance')
    ordered = sorted(candidates, key=lambda c: c.id)
    pairs = []
    for i, a in enumerate(ordered):
        _, ca, za = tool_axes(a.grasp_pose.quaternion)
        _, pca, pza = tool_axes(a.pregrasp_pose.quaternion)
        for b in ordered[i+1:]:
            _, cb, zb = tool_axes(b.grasp_pose.quaternion)
            _, pcb, pzb = tool_axes(b.pregrasp_pose.quaternion)
            if (norm(tuple(x-y for x,y in zip(a.grasp_pose.position,b.grasp_pose.position))) <= position_tolerance_m
                    and norm(tuple(x-y for x,y in zip(a.pregrasp_pose.position,b.pregrasp_pose.position))) <= position_tolerance_m
                    and norm(tuple(x-y for x,y in zip(pza,pzb))) <= axis_tolerance
                    and min(norm(tuple(x-y for x,y in zip(pca,pcb))),
                            norm(tuple(x+y for x,y in zip(pca,pcb)))) <= axis_tolerance
                    and abs(a.gripper_width-b.gripper_width) <= position_tolerance_m
                    and norm(tuple(x-y for x,y in zip(za,zb))) <= axis_tolerance
                    and min(norm(tuple(x-y for x,y in zip(ca,cb))),
                            norm(tuple(x+y for x,y in zip(ca,cb)))) <= axis_tolerance):
                pairs.append([a.id, b.id])
    return pairs


def feature_sidecar(candidates, geometry, source_sha256):
    payload = json.dumps([asdict(c) for c in candidates], sort_keys=True,
                         separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return dict(version=VERSION, visibility='internal_diagnostics_not_selector_input',
                candidate_sha256=hashlib.sha256(payload.encode()).hexdigest(),
                source_sha256=source_sha256, model=asdict(geometry),
                provenance={'formulas': FORMULAS, 'input': 'static Candidate mug-local poses and URDF',
                            'axes': 'quaternion rotates tool +Y closing / +Z approach into mug frame',
                            'COM': 'URDF mug inertial origin; not estimated real mug COM',
                            'unimplemented': ['force_closure', 'epsilon', 'rotation_freedom',
                                              'opening_obstruction', 'handle_obstruction', 'task_suitability']},
                candidates=[candidate_features(c, geometry) for c in candidates],
                equivalence={'position_tolerance_m': 1e-6, 'axis_tolerance': 1e-6,
                             'pairs': equivalent_pairs(candidates),
                             'meaning': 'ideal contact family only; robot IK/collision may differ'})
