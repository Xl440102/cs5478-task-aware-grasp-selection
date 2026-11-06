"""Predict sampled first contacts from Candidate and static geometry only.

The object is held fixed during hypothetical independent finger closure. All
successful predictions are approximate: finite inward-face rays cannot certify
full mesh clearance, actual closure, contact area, friction, or force closure.
No approach text, ID suffix, execution record, force, or success label is read.
"""
from collections import Counter
from dataclasses import dataclass
import math
from time import perf_counter

import numpy as np

from grasp_a.contracts import Candidate, candidate_sha256
from .contacts import (
    CONTACT_SPEC_VERSION, ContactMechanism, ContactModel, ContactPoint,
    ContactSource, ContactSpec, ContactStatus, FingerCapacityGroup, GraspFamily,
)
from .geometry import (
    GEOMETRY_VERSION, TOLERANCE_M, GeometryError, contains, on_surface,
    containing_primitives, quaternion_matrix, union_intersections, vector3,
)

ADAPTER_VERSION = "sampled_static_contacts_v2"
SIMULTANEOUS_TOLERANCE_M = 1e-6  # numerical tie only, not compliant indentation
FAMILY_MAP = {"side": GraspFamily.BODY, "body": GraspFamily.BODY,
              "top": GraspFamily.TOP, "handle": GraspFamily.HANDLE, "rim": GraspFamily.RIM}


@dataclass(frozen=True)
class GeneratorInfo:
    name: str
    version: str
    family: str
    candidate_id: str
    candidate_hash: str | None = None
    mechanism: ContactMechanism | None = None


class _PredictionFailure(Exception):
    def __init__(self, status, reason, phase, details=None):
        self.status, self.reason, self.phase = status, reason, phase
        self.details = details or {}


def _fail(reason, phase="closing_geometry", status=ContactStatus.INVALID, details=None):
    raise _PredictionFailure(status, reason, phase, details)


def _mechanism(family, info, approach=None, closing=None):
    if info.mechanism is not None:
        result = ContactMechanism(info.mechanism)
        allowed = {
            GraspFamily.BODY: {ContactMechanism.BODY_SIDE},
            GraspFamily.TOP: {ContactMechanism.BODY_TOP_DOWN},
            GraspFamily.RIM: {ContactMechanism.RIM_INNER_OUTER},
            GraspFamily.HANDLE: {ContactMechanism.HANDLE_SIDE, ContactMechanism.HANDLE_TOP_DOWN,
                                ContactMechanism.HANDLE_BAR_PINCH},
        }
        if result not in allowed[family]:
            raise ValueError("generator family/mechanism mismatch")
        return result
    if family is GraspFamily.BODY:
        return ContactMechanism.BODY_SIDE
    if family is GraspFamily.TOP:
        return ContactMechanism.BODY_TOP_DOWN
    if family is GraspFamily.RIM:
        return ContactMechanism.RIM_INNER_OUTER
    return (ContactMechanism.HANDLE_TOP_DOWN if approach is not None and approach[2] < -.95
            else ContactMechanism.HANDLE_SIDE)


def _first_contact(hits, geometry, origin, closing, sign, inner_offset, finger, opening, *, reference=False):
    start_t = sign*opening/2+inner_offset
    start_point = np.asarray(origin)+start_t*closing
    if reference:
        inside = [p.primitive_id for p in geometry.primitives if contains(p, start_point, strict=True, reference=True)]
    else:
        membership = containing_primitives(geometry, [start_point], strict=True)[0]
        inside = [p.primitive_id for p, covered in zip(geometry.primitives, membership) if covered]
    if inside:
        _fail("initial_inward_finger_sample_inside_object", details=dict(
            finger=finger.finger_id, position_m=list(map(float, start_point)), penetrating_primitives=inside))
    reachable = []
    for hit in hits:
        q = sign*(hit.t_m-inner_offset)
        if finger.lower_m-TOLERANCE_M <= q <= opening/2+TOLERANCE_M:
            reachable.append((float(q), hit))
    if not reachable:
        return None
    q, hit = max(reachable, key=lambda pair: pair[0])
    if hit.ambiguous:
        _fail("ambiguous_edge_seam_or_tangent", "predicted_contacts", ContactStatus.UNKNOWN,
              dict(finger=finger.finger_id, position_m=list(hit.position_m),
                   representative_surface=hit.surface_id, unique_normal=False))
    if sign*float(np.dot(hit.normal_outward, closing)) <= 1e-8:
        _fail("first_face_not_opposing_finger_closure", "predicted_contacts", ContactStatus.UNKNOWN)
    return q, hit


def build_contact_spec(candidate: Candidate, object_geometry, gripper_geometry, *, generator_info: GeneratorInfo,
                       reference=False, timings=None):
    """Build a sidecar, preserving diagnostic status instead of inventing contacts.

    Bad/mismatched identity metadata and nonfinite Candidate payloads raise
    ValueError: no trustworthy hash can be fabricated. Finite but invalid poses
    and unsupported geometry return identified, schema-valid diagnostic specs.
    """
    began = perf_counter()
    sample_started = None
    intersection_s = surface_s = 0.
    if not isinstance(candidate, Candidate) or not isinstance(generator_info, GeneratorInfo):
        raise ValueError("Candidate and explicit GeneratorInfo are required")
    if candidate.id != generator_info.candidate_id:
        raise ValueError("generator candidate ID mismatch")
    fingerprint = candidate_sha256([candidate])
    if generator_info.candidate_hash is not None and generator_info.candidate_hash != fingerprint:
        raise ValueError("generator candidate hash mismatch")
    if generator_info.family not in FAMILY_MAP:
        raise ValueError("unsupported explicit generator family label")
    family = FAMILY_MAP[generator_info.family]
    mechanism = _mechanism(family, generator_info)
    validation = dict(candidate_pose="not_checked", closing_geometry="not_checked",
                      predicted_contacts="not_checked", contactspec_schema="valid")
    provenance = dict(
        adapter_version=ADAPTER_VERSION, geometry_version=GEOMETRY_VERSION,
        generator_family=generator_info.family,
        object_profile=object_geometry.profile, object_model_version=object_geometry.model_version,
        object_asset_sha256=object_geometry.asset_sha256, panda_sha256=gripper_geometry.panda_sha256,
        finger_mesh_sha256={f.finger_id: f.sha256 for f in gripper_geometry.fingers},
        palm_mesh_sha256=gripper_geometry.palm_sha256,
        model="declared collision primitive union; fixed object; sampled inward mesh faces",
        units={"position": "m", "force": "N", "torque": "N*m", "normal": "unit vector"},
        normal_semantics="object outward", validation=validation,
        simultaneous_tolerance_m=SIMULTANEOUS_TOLERANCE_M,
        inward_mesh_normal_y_threshold=.9,
        palm_and_swept_mesh_clearance="not_checked",
        observed_execution_used=False,
    )
    assumptions = (
        "Object fixed; independent fingers stop at their sampled first-contact joint positions.",
        "Finite mesh-supported rays approximate closure; not contact area or full collision checking.",
        "Declared primitives/OBJ surfaces omit Bullet margins and collision convexification.",
        "No measured force, friction, compliance, torsional support, or physical stability is inferred.",
    )
    contacts, reasons = [], []
    closing = approach = None
    status = ContactStatus.APPROXIMATE
    try:
        try:
            center = vector3(candidate.grasp_pose.position)
            pre = vector3(candidate.pregrasp_pose.position)
            r = quaternion_matrix(candidate.grasp_pose.quaternion)
            pre_r = quaternion_matrix(candidate.pregrasp_pose.quaternion)
            closing, approach = r[:, 1], r[:, 2]
            delta = center-pre
            offset = float(delta@approach)
            if offset <= 0 or np.linalg.norm(delta-offset*approach) > TOLERANCE_M or not np.allclose(
                    pre_r, r, atol=1e-8, rtol=0.):
                _fail("invalid_pregrasp_axis_or_orientation", "candidate_pose")
            expected_region = "body" if family in (GraspFamily.BODY, GraspFamily.TOP) else family.value
            if candidate.region != expected_region:
                _fail("candidate_region_generator_family_mismatch", "candidate_pose")
            if isinstance(candidate.gripper_width, bool) or not math.isfinite(candidate.gripper_width):
                _fail("invalid_gripper_opening", "candidate_pose")
            if not 0 < candidate.gripper_width <= gripper_geometry.max_opening_m:
                _fail("opening_outside_finger_joint_limits", "candidate_pose")
            validation["candidate_pose"] = "valid"
            mechanism = _mechanism(family, generator_info, approach, closing)
        except GeometryError as exc:
            _fail(str(exc), "candidate_pose")

        if family in (GraspFamily.BODY, GraspFamily.TOP, GraspFamily.RIM) and abs(closing[2]) > 1e-8:
            _fail("nonhorizontal_body_or_wall_closing_axis", status=ContactStatus.UNSUPPORTED)
        if family in (GraspFamily.TOP, GraspFamily.RIM) and approach[2] > -.95:
            _fail("expected_top_down_approach", status=ContactStatus.UNSUPPORTED)
        if family is GraspFamily.BODY and abs(approach[2]) > .95:
            _fail("body_side_label_with_top_down_approach", status=ContactStatus.UNSUPPORTED)
        if (family is GraspFamily.HANDLE and abs(closing[2]) > 1e-8 and
                mechanism is not ContactMechanism.HANDLE_BAR_PINCH):
            _fail("vertical_handle_axis_requires_explicit_bar_pinch", status=ContactStatus.UNSUPPORTED)
        if family is GraspFamily.RIM and object_geometry.profile != "hollow":
            _fail("rim_requires_hollow_interior", status=ContactStatus.UNSUPPORTED)

        sample_started = perf_counter()
        sampled = {"left": [], "right": []}
        central = {}
        ray_records = []
        provenance["sampled_rays"] = ray_records
        provenance["sampled_mesh_footprint_xz_m"] = [
            [min(s[i] for s in gripper_geometry.pad_samples), max(s[i] for s in gripper_geometry.pad_samples)]
            for i in (0, 1)] if gripper_geometry.pad_samples else []
        target_region = "handle" if family is GraspFamily.HANDLE else "body"
        for x, z, left_y, right_y in gripper_geometry.pad_samples:
            origin = center+r[:, 0]*x+approach*z
            query_started = perf_counter()
            hits = union_intersections(object_geometry, origin, closing, reference=reference)
            intersection_s += perf_counter()-query_started
            row = dict(tool_x_m=x, tool_z_m=z, first={})
            ray_records.append(row)
            for name, sign, inner_y in (("left", 1, left_y), ("right", -1, right_y)):
                found = _first_contact(hits, object_geometry, origin, closing, sign, inner_y,
                                       gripper_geometry.finger(name), candidate.gripper_width, reference=reference)
                if found is not None:
                    q, hit = found
                    sampled[name].append((q, hit, x, z, inner_y))
                    row["first"][name] = dict(joint_position_m=q, surface=hit.surface_id,
                                              region=hit.region)
                if abs(x) < 1e-12 and abs(z) < 1e-12:
                    central[name] = found
        if any(central.get(name) is None for name in ("left", "right")):
            _fail("central_ray_no_contact_within_commanded_opening_and_travel")
        central_hits = [central[name][1] for name in ("left", "right")]
        provenance["central_contacts"] = [dict(surface=h.surface_id, position_m=list(h.position_m),
                                                normal_outward=list(h.normal_outward)) for h in central_hits]
        if any(h.region != target_region for h in central_hits):
            _fail("central_closure_hits_other_region", status=ContactStatus.UNSUPPORTED)
        if family is GraspFamily.RIM:
            a, b = central_hits
            if a.primitive_id != b.primitive_id or object_geometry.primitive(a.primitive_id).shape.kind != "box":
                _fail("rim_central_contacts_not_on_same_wall_segment", status=ContactStatus.UNSUPPORTED)
            wall = object_geometry.primitive(a.primitive_id).shape
            radial = np.asarray(wall.position[:2])
            radial /= np.linalg.norm(radial)
            if (abs(np.dot(a.normal_outward[:2], radial)) < .99 or
                    float(np.dot(a.normal_outward, b.normal_outward)) > -.99):
                _fail("rim_contacts_not_inner_outer_wall_faces", status=ContactStatus.UNSUPPORTED)
        provenance["central_surface_separation_m"] = float(np.linalg.norm(
            np.asarray(central_hits[0].position_m)-central_hits[1].position_m))

        joint_positions = {}
        first_hits = []
        for name, sign in (("left", 1), ("right", -1)):
            # q decreases from initial opening. Only maximal q contacts are
            # simultaneous; later rays are diagnostic, not extra support points.
            q_first = max(s[0] for s in sampled[name])
            joint_positions[name] = q_first
            for q, hit, x, z, inner_y in sampled[name]:
                if q_first-q > SIMULTANEOUS_TOLERANCE_M:
                    continue
                if hit.region != target_region:
                    _fail("sampled_first_closure_hits_other_region", status=ContactStatus.UNSUPPORTED)
                primitive = object_geometry.primitive(hit.primitive_id)
                validation_started = perf_counter()
                try:
                    if not on_surface(primitive, hit.position_m, hit.normal_outward, hit.surface_id, reference=reference):
                        _fail("predicted_point_outside_declared_surface", "predicted_contacts")
                    tool_point = r.T@(np.asarray(hit.position_m)-center)
                    finger = gripper_geometry.finger(name)
                    query = finger.inner_face_reference if reference else finger.inner_face
                    mesh_y = query(float(tool_point[0]), float(tool_point[2]))
                    if mesh_y is None or abs(tool_point[1]-(sign*q+mesh_y)) > TOLERANCE_M:
                        _fail("predicted_contact_outside_inward_finger_face", "predicted_contacts")
                finally:
                    surface_s += perf_counter()-validation_started
                contacts.append(ContactPoint(position_m=hit.position_m, normal_outward=hit.normal_outward,
                    finger_id=name, surface_id=hit.surface_id, contact_model=ContactModel.PCWF,
                    source=ContactSource.PREDICTED_ANALYTICAL, capacity_group_id=name))
                first_hits.append(hit)
        provenance["predicted_finger_joint_positions_m"] = joint_positions
        provenance["partial_sampled_pad_coverage"] = any(len(v) < len(gripper_geometry.pad_samples)
                                                         for v in sampled.values())
        validation["closing_geometry"] = "valid_under_sampled_fixed_object_model"
        validation["predicted_contacts"] = "approximate"
        reasons.append("finite_inward_mesh_sampling_not_full_contact_manifold")
        if provenance["partial_sampled_pad_coverage"]:
            reasons.append("partial_sampled_pad_coverage")
        if family is GraspFamily.RIM and len({h.primitive_id for h in first_hits}) != 1:
            from .diagnostics import rim_contact_diagnostics
            continuity = rim_contact_diagnostics(contacts, object_geometry, sampled_rays=ray_records)
            provenance["rim_continuity"] = continuity
            if continuity["resolved_piecewise_planar_contacts"]:
                reasons.append("rim_unique_contacts_on_connected_piecewise_planar_walls")
            else:
                status = ContactStatus.UNKNOWN
                validation["predicted_contacts"] = "unknown"
                reasons.append("rim_first_contacts_span_multiple_wall_segments")
    except _PredictionFailure as exc:
        status = exc.status
        validation[exc.phase] = exc.status.value
        reasons = [exc.reason]
        contacts = []
        provenance["failure_details"] = exc.details

    construction_started = perf_counter()
    groups = tuple(FingerCapacityGroup(group_id=name, finger_id=name) for name in ("left", "right"))
    spec = ContactSpec(schema_version=CONTACT_SPEC_VERSION, candidate_id=candidate.id,
        candidate_hash=fingerprint, generator_name=generator_info.name, generator_version=generator_info.version,
        family=family, mechanism=mechanism, object_frame="mug", reference_point_m=object_geometry.reference_point_m,
        contacts=tuple(contacts), status=status, provenance=provenance, assumptions=assumptions,
        capacity_groups=groups, closing_direction=None if closing is None else tuple(map(float, closing)),
        approach_direction=None if approach is None else tuple(map(float, approach)), reasons=tuple(reasons))
    spec.assert_candidate_identity(candidate.id, fingerprint)
    if timings is not None:
        ended = perf_counter()
        timings.clear()
        timings.update(setup_s=(sample_started or construction_started)-began,
                       primitive_intersection_s=intersection_s,
                       contact_sampling_s=(max(0., construction_started-sample_started-intersection_s-surface_s)
                                           if sample_started is not None else 0.),
                       surface_validation_s=surface_s,
                       contactspec_construction_s=ended-construction_started,
                       total_s=ended-began)
    return spec


def audit_candidates(candidates, object_geometry, gripper_geometry, *, records, generator_name, generator_version,
                     reference=False, profile=False):
    """In-memory coverage/timing. One row per input, no experiment file writes.

    Errors that prevent a trustworthy spec retain an error row, not fabricated
    contacts/hash. ``specs`` contains only successfully constructed sidecars.
    """
    counts = Counter(c.id for c in candidates)
    duplicates = sorted(cid for cid, n in counts.items() if n > 1)
    if duplicates:
        raise ValueError(f"duplicate Candidate IDs: {duplicates}")
    records = list(records)
    by_id = {r["id"]: r for r in records}
    if len(by_id) != len(records) or set(by_id) != set(counts):
        raise ValueError("generator records do not align with Candidate IDs")
    rows, specs, statuses, reasons = [], [], {}, Counter()
    exception_count = 0
    started = perf_counter()
    for candidate in candidates:
        family = by_id[candidate.id]["family"]
        record = by_id[candidate.id]
        info = GeneratorInfo(generator_name, generator_version, family, candidate.id,
                             candidate_hash=record.get("candidate_hash"), mechanism=record.get("mechanism"))
        begin = perf_counter()
        timings = {} if profile else None
        status = statuses.setdefault(family, {**{s.value: 0 for s in ContactStatus}, "error": 0})
        try:
            spec = build_contact_spec(candidate, object_geometry, gripper_geometry, generator_info=info,
                                      reference=reference, timings=timings)
        except (ValueError, GeometryError) as exc:
            exception_count += 1
            status["error"] += 1
            reason = f"{type(exc).__name__}:{exc}"
            reasons[reason] += 1
            rows.append(dict(candidate_id=candidate.id, family=family, status="error", contacts=0,
                             seconds=perf_counter()-begin, reasons=[reason]))
            continue
        elapsed = perf_counter()-begin
        specs.append(spec)
        status[spec.status.value] += 1
        reasons.update(spec.reasons)
        rows.append(dict(candidate_id=candidate.id, family=family, status=spec.status.value,
                         contacts=len(spec.contacts), seconds=elapsed, reasons=list(spec.reasons)))
        if profile:
            rows[-1]["stages_s"] = timings
    return dict(adapter_version=ADAPTER_VERSION, specs=tuple(specs), candidates=rows,
                by_family=statuses, reason_counts=dict(sorted(reasons.items())),
                total_seconds=perf_counter()-started, duplicate_candidate_ids=duplicates,
                geometry_invalid_count=sum(s.status is ContactStatus.INVALID for s in specs),
                geometry_unknown_count=sum(s.status is ContactStatus.UNKNOWN for s in specs),
                geometry_exception_count=exception_count)
