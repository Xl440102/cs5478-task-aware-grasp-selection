"""Explain static contacts without changing status, tolerance, or real normals.

Point interior depth below is distance to a primitive boundary, NOT a Bullet
mesh-pair penetration measurement. Wall solid connectivity is kept separate
from exposed face continuity and from physical finger contact continuity.
"""
import math

import numpy as np

from grasp_b.models import rotation
from .geometry import GeometryError, TOLERANCE_M, contains, on_surface, quaternion_matrix

CONTINUITY_VERSION = "declared_wall_face_continuity_v1"
NORMAL_TOLERANCE = 1e-8


def _matrix(r, p):
    out = np.eye(4)
    out[:3, :3], out[:3, 3] = r, p
    return out


def opening_diagnostics(candidate, geometry, gripper):
    r = quaternion_matrix(candidate.grasp_pose.quaternion)
    center = np.array(candidate.grasp_pose.position)
    transforms, penetrations = {}, []
    for finger in gripper.fingers:
        sign = 1 if finger.finger_id == "left" else -1
        root = np.array(finger.joint_origin_tool_m)+np.array((0., sign*candidate.gripper_width/2, 0.))
        collision_r = rotation(finger.collision_rpy)
        tool_mesh = _matrix(collision_r, root)
        mug_mesh = _matrix(r@collision_r, center+r@root)
        transforms[finger.finger_id] = dict(mesh_to_tool=tool_mesh.tolist(), mesh_to_mug=mug_mesh.tolist(),
                                           joint_position_m=candidate.gripper_width/2)
        for x, z, yl, yr in gripper.pad_samples:
            y = yl if sign == 1 else yr
            p_tool = np.array((x, sign*candidate.gripper_width/2+y, z))
            p_mug = center+r@p_tool
            p_mesh = collision_r.T@(p_tool-root)
            mesh_y = finger.inner_face(x, z)
            pad_error = None if mesh_y is None else abs(mesh_y-y)
            roundtrip = float(np.linalg.norm((mug_mesh@np.r_[p_mesh, 1.])[:3]-p_mug))
            for primitive in geometry.primitives:
                if not contains(primitive, p_mug, strict=True):
                    continue
                shape = primitive.shape
                local = rotation(shape.rpy).T@(p_mug-shape.position)
                depth = (float(np.min(np.array(shape.dimensions)/2-np.abs(local))) if shape.kind == "box" else
                         min(shape.dimensions[0]-math.hypot(*local[:2]), shape.dimensions[1]/2-abs(local[2])))
                penetrations.append(dict(finger=finger.finger_id, primitive_id=primitive.primitive_id,
                    mesh_local_m=p_mesh.tolist(), tool_local_m=p_tool.tolist(), mug_local_m=p_mug.tolist(),
                    primitive_local_m=local.tolist(), point_interior_depth_m=depth,
                    inward_mesh_covered=mesh_y is not None and pad_error <= TOLERANCE_M,
                    inward_mesh_residual_m=pad_error, transform_roundtrip_error_m=roundtrip))
    if any(p["transform_roundtrip_error_m"] > TOLERANCE_M for p in penetrations):
        classification = "frame_transform_error"
    elif any(not p["inward_mesh_covered"] for p in penetrations):
        classification = "mesh_sampling_artifact"
    elif penetrations and all(p["point_interior_depth_m"] > TOLERANCE_M for p in penetrations):
        classification = "declared_geometry_conflict"
    else:
        classification = "insufficient_information"
    return dict(classification=classification, finger_transforms=transforms, penetrations=penetrations,
                depth_definition="distance from sampled point to nearest declared primitive boundary")


def normal_angle_deg(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(a, b))), float(a@b)))


def _walls(geometry):
    return sorted((p for p in geometry.primitives if p.shape.region == "body" and p.shape.kind == "box"),
                  key=lambda p: math.atan2(p.shape.position[1], p.shape.position[0]) % (2*math.pi))


def _wall_overlap(a, b):
    """Exact rectangle SAT plus Z overlap for the current extruded wall boxes."""
    if any(abs(v) > 1e-12 for p in (a, b) for v in p.shape.rpy[:2]):
        return None
    ra, rb = rotation(a.shape.rpy), rotation(b.shape.rpy)
    ha, hb = np.array(a.shape.dimensions)/2, np.array(b.shape.dimensions)/2
    delta = np.array(b.shape.position)-a.shape.position
    margins = []
    for axis in (ra[:2, 0], ra[:2, 1], rb[:2, 0], rb[:2, 1]):
        radius = float(ha[:2]@np.abs(ra[:2, :2].T@axis)+hb[:2]@np.abs(rb[:2, :2].T@axis))
        margins.append(radius-abs(float(delta[:2]@axis)))
    margins.append(float(ha[2]+hb[2]-abs(delta[2])))
    return dict(intersects=all(m >= -TOLERANCE_M for m in margins),
                minimum_sat_overlap_m=min(margins), z_overlap_m=margins[-1])


def _face_meeting(geometry, a, b, sign, z_m):
    ra, rb = rotation(a.shape.rpy), rotation(b.shape.rpy)
    na, nb = sign*ra[:, 0], sign*rb[:, 0]
    ca = np.array(a.shape.position)+na*a.shape.dimensions[0]/2
    cb = np.array(b.shape.position)+nb*b.shape.dimensions[0]/2
    matrix = np.array((na[:2], nb[:2]))
    if abs(np.linalg.det(matrix)) <= 1e-12:
        if np.linalg.norm(na-nb) > NORMAL_TOLERANCE or abs(float(na@(cb-ca))) > TOLERANCE_M:
            return None
        tangent = ra[:, 1]
        intervals = [(float(tangent@np.array(p.shape.position))-p.shape.dimensions[1]/2,
                      float(tangent@np.array(p.shape.position))+p.shape.dimensions[1]/2) for p in (a, b)]
        lo, hi = max(i[0] for i in intervals), min(i[1] for i in intervals)
        if hi < lo-TOLERANCE_M:
            return None
        point = ca+tangent*((lo+hi)/2-float(tangent@ca))
        point[2] = z_m
    else:
        xy = np.linalg.solve(matrix, (float(na@ca), float(nb@cb)))
        point = np.array((*xy, z_m))
    face = "x+" if sign == 1 else "x-"
    if not all(on_surface(p, point, n, p.primitive_id+":"+face) for p, n in ((a, na), (b, nb))):
        return None
    if any(p.primitive_id not in (a.primitive_id, b.primitive_id) and contains(p, point, strict=True)
           for p in geometry.primitives):
        return None
    return point


def wall_pair_diagnostics(geometry, primitive_a, primitive_b, *, z_m=0.):
    """Adjacency requires actual solid intersection; normal continuity is stricter.

    A single planar patch requires connected solids, meeting exposed inner and
    outer faces, and unchanged normals equal within 1e-8. Merely consecutive
    wall indices never establish surface/contact continuity.
    """
    walls = _walls(geometry)
    ids = [p.primitive_id for p in walls]
    if primitive_a not in ids or primitive_b not in ids:
        return dict(supported=False, reason="not_extruded_body_wall_boxes")
    ia, ib = ids.index(primitive_a), ids.index(primitive_b)
    a, b = walls[ia], walls[ib]
    distance = min(abs(ia-ib), len(walls)-abs(ia-ib))
    overlap = _wall_overlap(a, b)
    if overlap is None:
        return dict(supported=False, reason="wall_not_aligned_with_mug_z")
    outer = _face_meeting(geometry, a, b, 1, z_m)
    inner = _face_meeting(geometry, a, b, -1, z_m)
    na, nb = rotation(a.shape.rpy)[:, 0], rotation(b.shape.rpy)[:, 0]
    equal = float(np.linalg.norm(na-nb)) <= NORMAL_TOLERANCE
    connected = overlap["intersects"] and overlap["z_overlap_m"] > TOLERANCE_M
    return dict(supported=True, primitive_ids=[primitive_a, primitive_b], wall_indices=[ia, ib],
                circular_index_distance=distance, adjacent=distance == 1 and connected,
                material_connected=connected, normal_angle_deg=normal_angle_deg(na, nb),
                normals_continuous=equal, outer_faces_meet=outer is not None, inner_faces_meet=inner is not None,
                outer_meeting_m=None if outer is None else outer.tolist(),
                inner_meeting_m=None if inner is None else inner.tolist(),
                single_planar_wall_patch=connected and equal and outer is not None and inner is not None,
                **overlap)


def rim_contact_diagnostics(contacts, geometry, *, sampled_rays=()):
    wall_ids = {p.primitive_id for p in _walls(geometry)}
    contact_rows = []
    walls = _walls(geometry)
    ids = [p.primitive_id for p in walls]
    for contact in contacts:
        pid, face = contact.surface_id.rsplit(":", 1)
        primitive = geometry.primitive(pid)
        seams = []
        if pid in wall_ids and face in ("x+", "x-"):
            index = ids.index(pid)
            for other in (walls[(index-1) % len(walls)], walls[(index+1) % len(walls)]):
                pair = wall_pair_diagnostics(geometry, pid, other.primitive_id, z_m=contact.position_m[2])
                meeting = pair.get("outer_meeting_m" if face == "x+" else "inner_meeting_m")
                if pair.get("adjacent") and meeting is not None:
                    seams.append(float(np.linalg.norm(np.array(meeting)-contact.position_m)))
        seam_distance = min(seams) if seams else None
        contact_rows.append(dict(finger=contact.finger_id.value, primitive_id=pid, surface_id=contact.surface_id,
            position_m=list(contact.position_m), normal_outward=list(contact.normal_outward),
            nearest_exposed_facet_seam_m=seam_distance,
            on_facet_seam=seam_distance is not None and seam_distance <= TOLERANCE_M,
            top_edge_distance_m=primitive.shape.position[2]+primitive.shape.dimensions[2]/2-contact.position_m[2]))
    pairs = []
    for a in (p for p in contacts if p.finger_id.value == "left"):
        for b in (p for p in contacts if p.finger_id.value == "right"):
            pa, pb = a.surface_id.rsplit(":", 1)[0], b.surface_id.rsplit(":", 1)[0]
            pairs.append(dict(distance_m=float(np.linalg.norm(np.array(a.position_m)-b.position_m)),
                              opposing_normal_angle_deg=normal_angle_deg(a.normal_outward, b.normal_outward),
                              opposite_wall_faces={a.surface_id.rsplit(":", 1)[1],
                                                   b.surface_id.rsplit(":", 1)[1]} == {"x+", "x-"},
                              wall_pair=wall_pair_diagnostics(geometry, pa, pb, z_m=a.position_m[2])))
    sampled = {name: sorted({r["first"][name]["surface"].rsplit(":", 1)[0]
                            for r in sampled_rays if name in r["first"]})
               for name in ("left", "right")}
    # C0 surface connectivity is sufficient for representing separate PCWF
    # points. C1 normal continuity is NOT required and normals are never averaged.
    # Neither criterion proves a continuous physical finger contact patch.
    resolved = bool(pairs) and all(
        not row["on_facet_seam"] and row["nearest_exposed_facet_seam_m"] is not None and
        row["top_edge_distance_m"] > TOLERANCE_M for row in contact_rows) and all(
        pair["opposite_wall_faces"] and pair["wall_pair"].get("material_connected") and
        (pair["wall_pair"].get("circular_index_distance") == 0 or
         (pair["wall_pair"].get("adjacent") and pair["wall_pair"].get("outer_faces_meet") and
          pair["wall_pair"].get("inner_faces_meet"))) for pair in pairs)
    return dict(criterion_version=CONTINUITY_VERSION, tolerance_m=TOLERANCE_M,
                normal_tolerance=NORMAL_TOLERANCE, contacts=contact_rows, left_right_pairs=pairs,
                sampled_wall_segments_by_finger=sampled,
                sampled_footprint_crosses_segments={name: len(ids) > 1 for name, ids in sampled.items()},
                resolved_piecewise_planar_contacts=resolved,
                decision="approximate only for unique off-seam points on same/connected neighboring walls; no normal smoothing")


def rim_diagnostics(spec, geometry):
    return rim_contact_diagnostics(spec.contacts, geometry,
                                   sampled_rays=spec.provenance.get("sampled_rays", ()))


def diagnose_candidate(candidate, geometry, gripper, *, generator_info):
    from .adapter import build_contact_spec
    spec = build_contact_spec(candidate, geometry, gripper, generator_info=generator_info)
    out = dict(candidate_id=candidate.id, candidate_hash=spec.candidate_hash, status=spec.status.value,
               closing_direction=spec.closing_direction, approach_direction=spec.approach_direction,
               reasons=list(spec.reasons))
    if spec.family.value == "top" or spec.status.value == "invalid":
        try:
            out["opening"] = opening_diagnostics(candidate, geometry, gripper)
        except GeometryError as exc:
            out["opening"] = dict(classification="insufficient_information", reason=str(exc),
                                  finger_transforms={}, penetrations=[])
    if spec.family.value == "rim":
        out["rim"] = rim_diagnostics(spec, geometry)
    return out
