"""Analytical rigid-body Newton--Euler demands; no LP, execution or scoring.

All kinematics are world-frame point derivatives. Inertia is about COM in mug
axes. Required non-gravitational force=m(a_COM-g). About mug-local reference o:
tau_o=I_W alpha+omega x I_W omega + R(c-o) x F. Rotate the completed wrench
back to mug axes exactly once; never add a second TCP/grasp moment.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy.spatial.transform import Rotation

from .contacts import _freeze_json, _plain
from .gws import _text, _array, _scalar
from .motion_prediction import PredictedMotionContext, PredictedPose, vector, motion_identity
from .tasks import ObjectParameters

DYNAMICS_VERSION = "analytical_rigid_newton_euler_v1"


def object_parameters_from_snapshot(snapshot, *, asset_path, asset_key="sequence", expected_flags=0):
    """Bind loaded initial inertia to exact asset bytes/flags; no live query here.

    PyBullet reports diagonal inertia in the local inertial frame; rotate it
    into mug/link axes and retain both original values and loading provenance.
    Existing Task03 combine_payload handles COM/parallel-axis inertia explicitly.
    """
    if snapshot.get("physics_steps") != 0 or snapshot.get("motor_commands") != 0:
        raise ValueError("initial static asset snapshot required, not an episode snapshot")
    entry = snapshot["assets"][asset_key]
    digest = hashlib.sha256(Path(asset_path).read_bytes()).hexdigest()
    if entry["asset_sha256"] != digest or entry["flags"] != expected_flags or expected_flags != 0 or entry["fixed_base"]:
        raise ValueError("snapshot asset/loader mismatch; no automatic inertia substitution")
    d = entry["dynamics"]
    r = Rotation.from_quat(PredictedPose((0, 0, 0), d["local_inertial_quaternion_xyzw"]).quaternion_xyzw).as_matrix()
    diagonal = np.array(vector(d["local_inertia_diagonal_kg_m2"]))
    if np.any(diagonal < 0):
        raise ValueError("invalid loaded inertia")
    inertia = r@np.diag(diagonal)@r.T
    return ObjectParameters(mass_kg=d["mass_kg"], com_m=d["local_inertial_position_m"],
        inertia_com_kg_m2=inertia, source="runtime_loaded_initial_asset_dynamics_not_measured_trajectory",
        provenance=dict(asset_sha256=digest, loading_flags=expected_flags, inertia_frame="mug axes about COM",
            original_inertia_frame="local inertial axes", snapshot_version=snapshot["snapshot_version"],
            pybullet=snapshot["pybullet"], snapshot_entry=entry,
            snapshot_sha256=hashlib.sha256(json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()).hexdigest(),
            source_sha256=snapshot["source_sha256"]))


def point_kinematics(origin_pose, velocity_world, acceleration_world, omega_world, alpha_world, point_local_m):
    """Position/v/a of an offset rigid point; supports object-origin OR TCP origin.

    point_local_m must be expressed in origin_pose's axes. For a TCP origin,
    convert the mug c-p_g vector to TCP axes first (R_OT.T @ (c-p_g)).
    """
    if not isinstance(origin_pose, PredictedPose):
        raise ValueError("PredictedPose required")
    v, a, w, alpha, point = (np.array(vector(x)) for x in
        (velocity_world, acceleration_world, omega_world, alpha_world, point_local_m))
    offset = Rotation.from_quat(origin_pose.quaternion_xyzw).apply(point)
    return (vector(np.array(origin_pose.position_m)+offset), vector(v+np.cross(w, offset)),
            vector(a+np.cross(alpha, offset)+np.cross(w, np.cross(w, offset))))


@dataclass(frozen=True, slots=True)
class DynamicSample:
    stage: str
    time_s: float
    timed: bool
    status: str
    com_position_world_m: tuple
    com_velocity_world_m_s: tuple
    com_acceleration_world_m_s2: tuple
    reference_position_world_m: tuple
    net_required_force_world_n: tuple
    net_required_torque_com_world_nm: tuple | None
    net_required_torque_reference_world_nm: tuple | None
    required_contact_wrench_mug: tuple | None
    side: str
    limitations: tuple

    def __post_init__(self):
        _text(self.stage, "stage")
        object.__setattr__(self, "time_s", _scalar(self.time_s, "nominal time_s"))
        if type(self.timed) is not bool or self.status not in ("complete", "missing_inertia", "unsupported_support_transition"):
            raise ValueError("invalid dynamics status or timing flag")
        for name in ("com_position_world_m", "com_velocity_world_m_s", "com_acceleration_world_m_s2",
                     "reference_position_world_m", "net_required_force_world_n"):
            object.__setattr__(self, name, vector(getattr(self, name)))
        for name in ("net_required_torque_com_world_nm", "net_required_torque_reference_world_nm"):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, vector(getattr(self, name)))
        if self.required_contact_wrench_mug is not None:
            object.__setattr__(self, "required_contact_wrench_mug", tuple(map(float, _array(self.required_contact_wrench_mug, shape=(6,)))))
        if (self.status == "complete") != (self.required_contact_wrench_mug is not None):
            raise ValueError("only complete dynamics may expose a donor wrench")
        object.__setattr__(self, "limitations", tuple(self.limitations))


@dataclass(frozen=True, slots=True)
class PredictedDynamicsContext:
    candidate_id: str
    candidate_hash: str
    task: str
    reference_point_m: tuple
    gravity_world_m_s2: tuple
    body: ObjectParameters
    samples: tuple
    provenance: Mapping
    timings_s: Mapping
    model_version: str = field(default=DYNAMICS_VERSION, init=False)

    def __post_init__(self):
        object.__setattr__(self, "samples", tuple(self.samples))
        for key in ("reference_point_m", "gravity_world_m_s2"):
            object.__setattr__(self, key, vector(getattr(self, key)))
        for key in ("provenance", "timings_s"):
            object.__setattr__(self, key, _freeze_json(getattr(self, key)))

    def to_dict(self):
        return dict(_plain(self), required_wrench_frame="mug", wrench_units="N,N*m",
                    state_vectors_frame="world", source="analytical_estimate_not_measured")


def predict_dynamics(motion, body, *, reference_point_m, gravity_world_m_s2, source):
    """Vectorized physics + identified per-sample support/missing-inertia states.

    A contact-transition sample retains net Newton--Euler demand for diagnostics
    but has NO donor required-contact wrench: support allocation is unknown.
    Missing inertia only permits full wrench when rotational terms vanish.
    """
    began = perf_counter()
    if not isinstance(motion, PredictedMotionContext) or not isinstance(body, ObjectParameters):
        raise ValueError("PredictedMotionContext and explicit ObjectParameters required")
    _text(source, "dynamics assumptions/source")
    reference, gravity = np.array(vector(reference_point_m)), np.array(vector(gravity_world_m_s2))
    samples = motion.samples
    rotations = Rotation.from_quat(np.array([s.object_pose.quaternion_xyzw for s in samples])).as_matrix()
    positions = np.array([s.object_pose.position_m for s in samples])
    velocity = np.array([s.object_origin_velocity_world_m_s for s in samples])
    acceleration = np.array([s.object_origin_acceleration_world_m_s2 for s in samples])
    omega, alpha = (np.array([getattr(s, key) for s in samples]) for key in ("omega_world_rad_s", "alpha_world_rad_s2"))
    com_offset = rotations@np.array(body.com_m)
    reference_offset = rotations@reference
    com_position = positions+com_offset
    com_velocity = velocity+np.cross(omega, com_offset)
    com_acceleration = acceleration+np.cross(alpha, com_offset)+np.cross(omega, np.cross(omega, com_offset))
    force = body.mass_kg*(com_acceleration-gravity)
    if body.inertia_com_kg_m2 is None:
        rotational_known = np.all(omega == 0, axis=1)&np.all(alpha == 0, axis=1)
        torque_com = np.zeros_like(force)
    else:
        rotational_known = np.ones(len(samples), dtype=bool)
        inertia_world = rotations@np.array(body.inertia_com_kg_m2)@np.swapaxes(rotations, 1, 2)
        torque_com = np.einsum("nij,nj->ni", inertia_world, alpha)+np.cross(omega, np.einsum("nij,nj->ni", inertia_world, omega))
    torque_reference = torque_com+np.cross(com_offset-reference_offset, force)
    inverse = np.swapaxes(rotations, 1, 2)
    wrench = np.concatenate((np.einsum("nij,nj->ni", inverse, force), np.einsum("nij,nj->ni", inverse, torque_reference)), axis=1)
    if not all(np.isfinite(a).all() for a in (com_position, com_velocity, com_acceleration, force, torque_com, torque_reference, wrench)):
        raise ValueError("nonfinite dynamics; input scale/model invalid")
    numerical_s = perf_counter()-began
    output = []
    for i, sample in enumerate(samples):
        limitations = []
        if not rotational_known[i]:
            limitations.append("missing rotational inertia; force only, full wrench unavailable")
        if sample.support != "donor_only_rigid_attachment_assumed":
            limitations.append("table/support transition; donor load allocation unknown")
        status = "complete" if not limitations else ("unsupported_support_transition" if sample.support != "donor_only_rigid_attachment_assumed" else "missing_inertia")
        output.append(DynamicSample(sample.stage, sample.time_s, sample.timed, status,
            vector(com_position[i]), vector(com_velocity[i]), vector(com_acceleration[i]), vector(positions[i]+reference_offset[i]),
            vector(force[i]), vector(torque_com[i]) if rotational_known[i] else None,
            vector(torque_reference[i]) if rotational_known[i] else None,
            tuple(map(float, wrench[i])) if status == "complete" else None, sample.side, tuple(limitations)))
    return PredictedDynamicsContext(motion.candidate_id, motion.candidate_hash, motion.task, tuple(reference), tuple(gravity), body,
        tuple(output), dict(motion_version=motion.model_version, motion_input_sha256=motion_identity(motion), motion_provenance=motion.provenance, stage_boundaries=motion.boundaries, source=source,
            assumptions=("rigid body/entrained payload only; no sloshing, draining, impacts or contact feedback",
                         "predicted kinematics, not measured motor tracking; no feasibility guarantee"),
            equation="F=m(a_COM-g); tau_C=I_W alpha+omega cross I_W omega; tau_o=tau_C+R(c-o) cross F",
            execution_outcome_used=False), dict(numerical_calculation=numerical_s, total=perf_counter()-began))
