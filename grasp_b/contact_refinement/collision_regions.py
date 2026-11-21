"""Collision-consistent static evidence, without contact mechanics or execution.

All crossings of the *sampled* signed-distance sequence are retained. Refinement
subdivides detected intervals; neither monotonicity nor unique global first
contact is assumed. A finite sweep cannot certify continuous clearance.
"""
from dataclasses import asdict, dataclass
import importlib.metadata
import json
from time import perf_counter

import numpy as np
import pybullet as p

from grasp_a.contracts import candidate_sha256
from grasp_b.models import rotation, read_model
from grasp_b.wrench_v2.geometry import ObjectGeometry
from .bullet_alignment import _native
from .contact_evidence import CollisionRegionEvidence, VERSION
from .contact_regions import digest, GeometryEvidence


@dataclass(frozen=True, kw_only=True)
class RegionQueryConfig:
    sweep_intervals: int = 32
    displacement_resolution_m: float = 1e-7
    distance_threshold_m: float = 0.
    monotonicity_tolerance_m: float = 1e-7
    event_point_band_m: float = 2e-6
    region_link_distance_m: float = 2e-4
    region_normal_angle_deg: float = 5.
    legacy_proximity_m: float = 1e-4
    analytic_surface_tolerance_m: float = 2e-6
    maximum_refinement_levels: int = 14

    def __post_init__(self):
        for key, value in asdict(self).items():
            if isinstance(value, bool) or not np.isfinite(value):
                raise ValueError("finite explicit query parameter required")
            if key != "distance_threshold_m" and value <= 0:
                raise ValueError("positive query parameter required")
        if type(self.sweep_intervals) is not int or self.sweep_intervals < 2:
            raise ValueError("at least two sweep intervals required")
        if type(self.maximum_refinement_levels) is not int or self.maximum_refinement_levels > 20:
            raise ValueError("bounded integer refinement levels required")
        if self.region_normal_angle_deg > 180:
            raise ValueError("normal angle in degrees, <=180")


def analyze_samples(samples, config):
    """Pure signed-distance analysis; distances are positive for separation."""
    if len(samples) < 2:
        raise ValueError("at least two samples required")
    displacement = np.array([x["displacement_m"] for x in samples])
    if not np.isfinite(displacement).all() or np.any(np.diff(displacement) <= 0):
        raise ValueError("strictly increasing finite displacement required")
    ds = [x["minimum_distance_m"] for x in samples]
    if any(x is None for x in ds):
        return dict(status="range_censored", transitions=[], sampled_monotone_nonincreasing=None)
    distances = np.array(ds)
    if not np.isfinite(distances).all():
        raise ValueError("non-finite query distance")
    transitions = []
    contact = distances <= config.distance_threshold_m
    for i in range(len(samples) - 1):
        if contact[i] != contact[i + 1]:
            transitions.append(dict(kind="entry" if contact[i + 1] else "exit", indices=[i, i + 1],
                                    interval_m=displacement[i:i + 2].tolist()))
    return dict(status="sampled", transitions=transitions,
                opening_overlap_or_contact=bool(contact[0]),
                sampled_monotone_nonincreasing=bool(np.all(np.diff(distances) <= config.monotonicity_tolerance_m)),
                sampled_minimum_distance_m=float(distances.min()),
                observed_large_distance_change_intervals=[int(i) for i in np.flatnonzero(
                    np.abs(np.diff(distances)) > np.diff(displacement)+config.monotonicity_tolerance_m)],
                distance_change_note="diagnostic only: large changes may reflect EPA/shape switching; no continuity certificate",
                continuous_monotonicity_certified=False, continuous_first_contact_certified=False)


def refine_events(query, lo, hi, config):
    """Uniformly subdivide *every* observed transition, preserving new branches.

    This is deliberately not a monotonic binary root search. Unobserved tangent
    touches/transient intervals remain possible, including within final brackets.
    """
    pending = [(lo, hi)]; leaves = []; calls = 0
    for _ in range(config.maximum_refinement_levels):
        next_pending = []
        for a, b in pending:
            if b - a <= config.displacement_resolution_m:
                leaves.append((a, b)); continue
            values = [query(float(x)) for x in np.linspace(a, b, 5)]; calls += 5
            analysis = analyze_samples(values, config)
            if analysis["status"] != "sampled":
                return dict(status="range_censored", brackets=[], query_requests=calls)
            for transition in analysis["transitions"]:
                next_pending.append(tuple(transition["interval_m"]))
        pending = next_pending
        if not pending:
            break
    leaves.extend(pending)
    # Endpoints determine entry/exit for each finite bracket, not a unique root.
    brackets = []
    for a, b in sorted(set(leaves)):
        left, right = query(a), query(b); calls += 2
        if left["minimum_distance_m"] is None or right["minimum_distance_m"] is None:
            return dict(status="range_censored", brackets=[], query_requests=calls)
        brackets.append(dict(kind="entry" if right["minimum_distance_m"] <= config.distance_threshold_m else "exit",
                             interval_m=[a, b], endpoints=[left, right],
                             resolution_reached=b-a <= config.displacement_resolution_m))
    return dict(status="refined_detected_intervals", brackets=brackets, query_requests=calls,
                unique_first_contact_certified=False)


def cluster_witnesses(witnesses, config):
    """Connected spatial/normal evidence groups, not an area or contact patch.

    Region identity is local to this record. Engine child/face IDs are unknown.
    All original witnesses are retained; grouping adds no mechanical capacity.
    """
    groups = []; remaining = set(range(len(witnesses)))
    def near(i, j):
        a, b = witnesses[i], witnesses[j]
        distance = np.linalg.norm(np.array(a["point_on_mug_m"])-b["point_on_mug_m"])
        cosine = np.clip(np.dot(a["normal_on_mug_mug"], b["normal_on_mug_mug"]), -1, 1)
        return distance <= config.region_link_distance_m and np.degrees(np.arccos(cosine)) <= config.region_normal_angle_deg
    while remaining:
        component = {min(remaining)}; remaining -= component; frontier = list(component)
        while frontier:
            i = frontier.pop()
            neighbors = {j for j in remaining if near(i, j)}
            component |= neighbors; remaining -= neighbors; frontier.extend(sorted(neighbors))
        rows = [witnesses[i] for i in sorted(component)]
        positions = np.array([x["point_on_mug_m"] for x in rows])
        normals = np.array([x["normal_on_mug_mug"] for x in rows])
        groups.append(dict(region_id=f"region_{len(groups):03d}", witnesses=rows,
                           displacement_support_m=[min(x["displacement_m"] for x in rows), max(x["displacement_m"] for x in rows)],
                           point_extent_m=(positions.max(axis=0)-positions.min(axis=0)).tolist(),
                           normal_max_pair_angle_deg=float(np.degrees(np.arccos(np.clip((normals@normals.T).min(), -1, 1)))),
                           compound_child_identity="not_exposed", physical_contact_patch="not_established"))
    return groups


def representation_identity(session):
    session._check()
    joints = {}
    for name, index in session.fingers.items():
        info = p.getJointInfo(session.robot, index, physicsClientId=session.client)
        joints[name] = dict(index=index, name=_native(info[1]), link_name=_native(info[12]),
                            type=int(info[2]), axis=list(info[13]), limits_m=[info[8], info[9]],
                            parent_frame_position=list(info[14]), parent_frame_quaternion=list(info[15]))
    return dict(version="bullet_representation_observation_v1", observation_status="representation_partially_observed",
                assets=dict(session.assets), loading_flags=dict(panda=session.robot_flags, mug=session.mug_flags),
                fixed_base=dict(panda=True, mug=False), pybullet_version=importlib.metadata.version("pybullet"),
                api_version=p.getAPIVersion(), joints=joints, collision_metadata=session.metadata,
                current_reported_dynamics={name: _native(p.getDynamicsInfo(session.robot, index, physicsClientId=session.client))
                                           for name, index in session.fingers.items()},
                static_query_configuration=asdict(session.config), world_robot_base_pose=_native(session.base_pose),
                limitations=["internal convex/compound topology and per-child margins not fully exported",
                             "simulation vertices and reported link margin are partial observations"])


def analytic_surface_associations(point, normal, geometry, config):
    """Nearest finite URDF box faces; association is not an engine child ID.

    The cylinder and hidden shape topology remain unsupported by this face
    matcher. Rounded proxy edges need not lie on any declared planar face.
    """
    associations = []
    for primitive in geometry.primitives:
        shape = primitive.shape
        if shape.kind != 'box': continue
        r = rotation(shape.rpy); half = np.array(shape.dimensions)/2
        local = r.T@(np.asarray(point)-shape.position)
        for axis in range(3):
            for sign in (-1, 1):
                nearest = np.clip(local, -half, half); nearest[axis] = sign*half[axis]
                distance = float(np.linalg.norm(local-nearest))
                angle = float(np.degrees(np.arccos(np.clip(np.dot(normal, sign*r[:,axis]), -1, 1))))
                associations.append(dict(primitive_id=primitive.primitive_id,
                    raw_box_face='xyz'[axis]+('+' if sign > 0 else '-'),
                    finite_face_distance_m=distance, query_normal_angle_deg=angle,
                    within_declared_surface_tolerance=distance <= config.analytic_surface_tolerance_m,
                    identity_status='geometric_association_only_not_engine_child_identity'))
    return sorted(associations, key=lambda x:(x['finite_face_distance_m'], x['primitive_id'], x['raw_box_face']))[:3]


def build_collision_region_evidence(session, candidate, spec, raw_evidence, *, config=None, geometry=None):
    config = config or RegionQueryConfig(); began = perf_counter()
    raw_evidence = GeometryEvidence.from_json(json.dumps(raw_evidence, allow_nan=False)).to_dict()
    spec.assert_candidate_identity(candidate.id, candidate_sha256([candidate]))
    if raw_evidence["candidate_hash"] != spec.candidate_hash or raw_evidence["contact_spec_hash"] != digest(spec.to_dict()):
        raise ValueError("raw evidence identity mismatch")
    if raw_evidence["geometry_assets"]["mug_sha256"] != session.assets["mug_urdf"]:
        raise ValueError("raw mug identity mismatch")
    if (raw_evidence["geometry_assets"]["panda_sha256"] != session.assets["panda_urdf"] or
        any(h != session.assets["finger_obj"] for h in raw_evidence["geometry_assets"]["finger_sha256"].values())):
        raise ValueError("raw Panda/finger identity mismatch")
    if raw_evidence["frame"] != "mug" or raw_evidence["length_unit"] != "m":
        raise ValueError("raw evidence frame/unit mismatch")
    geometry = geometry or ObjectGeometry.from_model(read_model('hollow', session.panda))
    if geometry.asset_sha256 != session.assets['mug_urdf']:
        raise ValueError('surface association asset mismatch')
    representation = representation_identity(session); session.place(candidate)
    before = session.query_count; hits = session.cache_hits; fingers = []
    for name in ("left", "right"):
        opening = candidate.gripper_width / 2
        limits = representation["joints"][name]["limits_m"]
        if not np.isfinite(opening) or opening <= limits[0] or opening > limits[1]+1e-12:
            raise ValueError("candidate opening outside declared joint limits")
        # A fixed mug and the full declared inward stroke. The other finger is
        # reset to opening before each sweep; no coupled force/control model.
        for finger, index in session.fingers.items():
            p.resetJointState(session.robot, index, opening, physicsClientId=session.client)
        def query(displacement):
            row = session.at_joint(name, opening-displacement)
            return dict(displacement_m=float(displacement), q_m=opening-float(displacement),
                        minimum_distance_m=row["minimum_distance_m"], points=row["points"],
                        finger_link_to_mug_transform=(np.linalg.inv(session.world_object)@session.link_pose(session.fingers[name])).tolist())
        samples = [query(float(x)) for x in np.linspace(0., opening-limits[0], config.sweep_intervals+1)]
        analysis = analyze_samples(samples, config); events = []
        for transition in analysis["transitions"]:
            result = refine_events(query, *transition["interval_m"], config)
            events.append(result)
        witnesses = []
        source_rows = [endpoint for event in events for bracket in event["brackets"] for endpoint in bracket["endpoints"]]
        if analysis.get("opening_overlap_or_contact"):
            source_rows += samples[:1]
        rotation = session.world_object[:3, :3]; origin = session.world_object[:3, 3]
        for row in source_rows:
            minimum = row["minimum_distance_m"]
            if minimum is None: continue
            for point in row["points"]:
                if point["distance_m"] > minimum+config.event_point_band_m: continue
                witnesses.append(dict(displacement_m=row["displacement_m"], q_m=row["q_m"],
                    signed_distance_m=point["distance_m"],
                    point_on_mug_m=(rotation.T@(np.array(point["position_on_B_world_m"])-origin)).tolist(),
                    point_on_finger_mug_m=(rotation.T@(np.array(point["position_on_A_world_m"])-origin)).tolist(),
                    normal_on_mug_mug=(rotation.T@np.array(point["normal_on_B_world"])).tolist(),
                    finger_link_index=session.fingers[name], mug_link_index=-1,
                    evidence_source="bullet_collision_proxy_closest_point"))
        for witness in witnesses:
            witness['surface_associations'] = analytic_surface_associations(
                witness['point_on_mug_m'], witness['normal_on_mug_mug'], geometry, config)
            witness['normal_semantics'] = 'Bullet normalOnB in mug frame, not a proven material facet normal'
        regions = cluster_witnesses(witnesses, config)
        legacy = []
        for i, contact in enumerate(spec.contacts):
            if contact.finger_id.value != name: continue
            matches = []
            for region in regions:
                distances = [np.linalg.norm(np.array(contact.position_m)-w["point_on_mug_m"]) for w in region["witnesses"]]
                matches.append(dict(region_id=region["region_id"], minimum_distance_m=float(min(distances)),
                                    within_declared_proximity=bool(min(distances) <= config.legacy_proximity_m)))
            legacy.append(dict(contact_index=i, position_mug_m=list(contact.position_m),
                               outward_normal=list(contact.normal_outward), surface_id=contact.surface_id, region_distances=matches))
        opening_conflict = analysis.get("opening_overlap_or_contact", False)
        ambiguous = len(regions) > 1
        sequential = [[a["region_id"], b["region_id"]] for i, a in enumerate(regions) for b in regions[i+1:]
                      if (a["displacement_support_m"][1]+config.displacement_resolution_m < b["displacement_support_m"][0] or
                          b["displacement_support_m"][1]+config.displacement_resolution_m < a["displacement_support_m"][0])]
        category = ("unknown" if analysis["status"] != "sampled" else "geometry_conflict" if opening_conflict else
                    "sequential_contact_evidence" if sequential else
                    "ambiguous_multiple_regions" if ambiguous else "collision_geometry_supported" if regions else "unknown")
        fingers.append(dict(finger_id=name, closing_axis_tool=[0, -1 if name == "left" else 1, 0],
                            opening_q_m=opening, q_limits_m=limits, samples=samples, sampled_analysis=analysis,
                            event_refinements=events, regions=regions, legacy_contact_comparison=legacy,
                            sequential_region_pairs=sequential,
                            classification=category, simultaneity="unknown_load_bearing_support_not_observed"))
    # Equal-displacement compatibility is only one declared kinematic policy;
    # Panda fingers also have independent joint coordinates. Neither proves load.
    entry_intervals = []
    for finger in fingers:
        entry_intervals.append([b["interval_m"] for e in finger["event_refinements"] for b in e["brackets"] if b["kind"] == "entry"])
    intersections = [[max(a[0], b[0]), min(a[1], b[1])] for a in entry_intervals[0] for b in entry_intervals[1] if max(a[0], b[0]) <= min(a[1], b[1])]
    classes = {f["classification"] for f in fingers}
    category = next((x for x in ("geometry_conflict", "unknown", "sequential_contact_evidence", "ambiguous_multiple_regions") if x in classes), "collision_geometry_supported")
    raw_groups = raw_evidence["finger_groups"]
    raw_by_finger = {g["finger_id"]: g for g in raw_groups}
    earlier = any(interval[1] < raw_by_finger[finger["finger_id"]]["first_contact_displacement_m"]
                  for finger, intervals in zip(fingers, entry_intervals)
                  if raw_by_finger[finger["finger_id"]].get("first_contact_displacement_m") is not None for interval in intervals)
    return CollisionRegionEvidence(dict(version=VERSION, candidate_id=candidate.id, candidate_hash=spec.candidate_hash,
        family=spec.family.value, mechanism=spec.mechanism.value, contact_spec_status=spec.status.value,
        contact_spec_hash=digest(spec.to_dict()), raw_evidence_hash=raw_evidence["evidence_hash"],
        representation_hash=digest(representation), representation=representation,
        input_hash=digest(dict(candidate=spec.candidate_hash, spec=digest(spec.to_dict()), raw=raw_evidence["evidence_hash"],
                              representation=digest(representation), query=asdict(config), version=VERSION)),
        frame="mug", length_unit="m", classification=category, query_config=asdict(config),
        actual_solver_manifold="not_observed", fingers=fingers,
        evidence_status="static_collision_proxy_only",
        region_coverage_status="partial_local_witness_groups_no_global_region_certificate",
        world_object_transform=session.world_object.tolist(), world_tcp_transform=session.tcp.tolist(),
        simultaneous_symmetric_closing=dict(common_entry_intervals_m=intersections,
            interpretation="interval overlap only; no continuous-contact or independent-force guarantee",
            independent_finger_coordinates_allowed=True),
        coverage=dict(collision_geometry_supported=all(f["sampled_analysis"]["status"] == "sampled" for f in fingers),
            contact_region_evidence_available=all(f["regions"] for f in fingers),
            multiple_region_ambiguity=any(len(f["regions"])>1 for f in fingers),
            earlier_collision_evidence=earlier, geometry_conflict="geometry_conflict" in classes,
            unknown_or_unsupported="unknown" in classes),
        raw_mesh_comparison=dict(classification=raw_evidence["classification"],
            finger_groups=[dict(finger_id=g["finger_id"], predicted_first_displacement_m=g.get("first_contact_displacement_m"),
                                raw_mesh_sweep=g["mesh_sweep"], rigid_contact_configuration=g.get("rigid_contact_configuration")) for g in raw_groups]),
        provenance=dict(source="predicted_geometry_query", motion="static_joint_resets_fixed_object", algorithm_version=VERSION),
        assumptions=["same A assets/flags; pair-local fixed mug and arm at home", "full inward joint stroke, no motor control",
                     "spatial/normal graph groups are diagnostic evidence, not contact patches"],
        limitations=["finite sweep cannot prove unique first contact, monotonicity, tangent clearance or continuity",
                     "compound child identities, edge/facet identities and complete manifold are not observable",
                     "no pressure, capacity, torsion, compliance or task-success inference"],
        runtime_s=perf_counter()-began, actual_query_calls=session.query_count-before, cache_hits=session.cache_hits-hits))
