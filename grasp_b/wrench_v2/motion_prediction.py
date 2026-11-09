"""Candidate-dependent nominal TCP interpolation, NEVER a measured trajectory.

A supplies TCP goals derived from object goals. Interpolate the TCP, then infer
T_WO=T_WT inv(T_OT). Object endpoints can agree across grasps while interior
positions differ during rotation. No IK, collision replay, control or outcomes.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field, asdict
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy.spatial.transform import Rotation

from grasp_a.contracts import Candidate, candidate_sha256
from grasp_a.sequence_contracts import SequenceConfig, task_spec
from .contacts import _freeze_json, _plain
from .gws import _array, _scalar, _text
from .tasks import _quaternion

MOTION_VERSION = "tcp_geodesic_analytical_motion_v1"
AUDITED_SOURCE_SHA256 = {
    "grasp_a/sequence.py": "7b9c14b6ef05016599fb23155b9e7814c67cd305ead061aa7246738a2ce65b29",
    "grasp_a/simulation.py": "17b64935c0efb1d0c19b4521bb6e1a2c76f6d3b028179cf1fe2cb890e25cd861",
    "grasp_a/control.py": "041b30e75f15f76048f934b66da1b81e4b02c73f093c2c1c31901ca8fad33c9b",
    "grasp_a/sequence_contracts.py": "e1dd49fa119f2e9e16933145e42699ddb4f034d1411dc83124e0ed7296a128e0",
}


def vector(value):
    return tuple(map(float, _array(value, shape=(3,))))


@dataclass(frozen=True, slots=True)
class PredictedPose:
    position_m: tuple
    quaternion_xyzw: tuple

    def __post_init__(self):
        object.__setattr__(self, "position_m", vector(self.position_m))
        object.__setattr__(self, "quaternion_xyzw", _quaternion(self.quaternion_xyzw))


def compose(a, b):
    r = Rotation.from_quat(a.quaternion_xyzw)
    return PredictedPose(np.array(a.position_m)+r.apply(b.position_m),
                         (r*Rotation.from_quat(b.quaternion_xyzw)).as_quat())


def inverse(pose):
    r = Rotation.from_quat(pose.quaternion_xyzw).inv()
    return PredictedPose(-r.apply(pose.position_m), r.as_quat())


@dataclass(frozen=True, slots=True, kw_only=True)
class MotionModel:
    samples_per_stage: int = 32
    interpolation: str = "half_cosine"
    source: str = "assumed analytical time law; A endpoints/durations, not motor tracking"

    def __post_init__(self):
        if type(self.samples_per_stage) is not int or self.samples_per_stage < 3:
            raise ValueError("at least three samples per moving stage")
        if self.interpolation not in ("linear", "quadratic", "half_cosine", "quintic"):
            raise ValueError("unsupported explicit time law")
        _text(self.source, "analytical time-law assumption")


def progress_law(name, u, duration_s):
    """Return f, df/dt, d2f/dt2; endpoint values are ONE-SIDED derivatives.

    Quadratic is a constant-acceleration numerical fixture. Half-cosine is C1
    rest-to-rest with acceleration jumps; quintic is C2 rest-to-rest. Neither
    is claimed to reproduce A's joint interpolation/motor/arrival dynamics.
    """
    duration = _scalar(duration_s, "duration_s", positive=True)
    u = _array(u)
    if np.any((u < 0) | (u > 1)):
        raise ValueError("progress must be in [0,1]")
    if name == "linear":
        return u, np.ones_like(u)/duration, np.zeros_like(u)
    if name == "quadratic":
        return u*u, 2*u/duration, np.full_like(u, 2/duration**2)
    if name == "half_cosine":
        return (1-np.cos(np.pi*u))/2, np.pi*np.sin(np.pi*u)/(2*duration), np.pi**2*np.cos(np.pi*u)/(2*duration**2)
    if name == "quintic":
        return 10*u**3-15*u**4+6*u**5, (30*u**2-60*u**3+30*u**4)/duration, (60*u-180*u**2+120*u**3)/duration**2
    raise ValueError("unknown time law")


@dataclass(frozen=True, slots=True)
class PredictedStage:
    name: str
    tcp_start: PredictedPose
    tcp_end: PredictedPose
    start_s: float
    duration_s: float | None
    source_command: str
    path: str = "cartesian"

    def __post_init__(self):
        _text(self.name, "stage")
        _text(self.source_command, "command source")
        object.__setattr__(self, "start_s", _scalar(self.start_s, "nominal start_s"))
        if self.duration_s is not None:
            object.__setattr__(self, "duration_s", _scalar(self.duration_s, "duration_s", positive=True))
        if not isinstance(self.tcp_start, PredictedPose) or not isinstance(self.tcp_end, PredictedPose):
            raise ValueError("PredictedPose endpoints required")
        if self.duration_s is None and self.tcp_start != self.tcp_end:
            raise ValueError("untimed state must be a stationary hold")


@dataclass(frozen=True, slots=True)
class PredictedMotionSample:
    stage: str
    time_s: float
    stage_time_s: float
    object_pose: PredictedPose
    tcp_pose: PredictedPose
    object_origin_velocity_world_m_s: tuple
    object_origin_acceleration_world_m_s2: tuple
    tcp_velocity_world_m_s: tuple
    tcp_acceleration_world_m_s2: tuple
    omega_world_rad_s: tuple
    alpha_world_rad_s2: tuple
    support: str = "donor_only_rigid_attachment_assumed"
    side: str = "interior"
    timed: bool = True

    def __post_init__(self):
        _text(self.stage, "stage")
        for name in ("time_s", "stage_time_s"):
            object.__setattr__(self, name, _scalar(getattr(self, name), name))
        for name in ("object_origin_velocity_world_m_s", "object_origin_acceleration_world_m_s2",
                     "tcp_velocity_world_m_s", "tcp_acceleration_world_m_s2", "omega_world_rad_s", "alpha_world_rad_s2"):
            object.__setattr__(self, name, vector(getattr(self, name)))
        if not isinstance(self.object_pose, PredictedPose) or not isinstance(self.tcp_pose, PredictedPose):
            raise ValueError("typed poses required")
        if type(self.timed) is not bool or self.side not in ("start", "end", "interior", "untimed_hold"):
            raise ValueError("explicit sample side and timing flag required")
        if self.support not in ("donor_only_rigid_attachment_assumed", "table_contact_transition_unknown"):
            raise ValueError("unknown support semantics")


def sample_stage(stage, grasp_pose, model, *, nominal_table_contact_center_z_m=None, timings=None):
    began = perf_counter()
    if not isinstance(stage, PredictedStage) or not isinstance(grasp_pose, PredictedPose) or not isinstance(model, MotionModel):
        raise ValueError("typed stage, T_OT and time model required")
    if stage.path != "cartesian":
        raise ValueError("joint-space paths need an explicit joint plan; TCP interpolation is not a joint model")
    count = 1 if stage.duration_s is None else model.samples_per_stage
    u = np.linspace(0, 1, count)
    if stage.duration_s is None:
        f = df = ddf = np.zeros(1)
    else:
        f, df, ddf = progress_law(model.interpolation, u, stage.duration_s)
    r0, r1 = (Rotation.from_quat(p.quaternion_xyzw) for p in (stage.tcp_start, stage.tcp_end))
    phi_world = (r1*r0.inv()).as_rotvec()
    r_tcp = Rotation.from_rotvec(f[:, None]*phi_world)*r0
    r_obj = r_tcp*Rotation.from_quat(grasp_pose.quaternion_xyzw).inv()
    delta = np.array(stage.tcp_end.position_m)-stage.tcp_start.position_m
    tcp_position = np.array(stage.tcp_start.position_m)+f[:, None]*delta
    offset = r_obj.apply(np.tile(grasp_pose.position_m, (count, 1)))
    obj_position = tcp_position-offset
    object_quat, tcp_quat = r_obj.as_quat(), r_tcp.as_quat()
    pose_time = perf_counter()-began
    began = perf_counter()
    tcp_v, tcp_a = df[:, None]*delta, ddf[:, None]*delta
    omega, alpha = df[:, None]*phi_world, ddf[:, None]*phi_world
    obj_v = tcp_v-np.cross(omega, offset)
    obj_a = tcp_a-np.cross(alpha, offset)-np.cross(omega, np.cross(omega, offset))
    derivative_time = perf_counter()-began
    began = perf_counter()
    samples = []
    for i in range(count):
        support = "donor_only_rigid_attachment_assumed"
        if nominal_table_contact_center_z_m is not None and obj_position[i, 2] <= nominal_table_contact_center_z_m:
            support = "table_contact_transition_unknown"
        samples.append(PredictedMotionSample(stage.name, stage.start_s+(0 if stage.duration_s is None else u[i]*stage.duration_s),
            0 if stage.duration_s is None else u[i]*stage.duration_s,
            PredictedPose(obj_position[i], object_quat[i]), PredictedPose(tcp_position[i], tcp_quat[i]),
            obj_v[i], obj_a[i], tcp_v[i], tcp_a[i], omega[i], alpha[i], support,
            "untimed_hold" if stage.duration_s is None else ("start" if i == 0 else "end" if i == count-1 else "interior"),
            stage.duration_s is not None))
    if timings is not None:
        timings.update(pose_sampling=pose_time, velocity_acceleration=derivative_time, sample_records=perf_counter()-began)
    return tuple(samples)


@dataclass(frozen=True, slots=True)
class PredictedMotionContext:
    candidate_id: str
    candidate_hash: str
    task: str
    grasp_pose: PredictedPose
    stages: tuple
    samples: tuple
    boundaries: tuple
    provenance: Mapping
    timings_s: Mapping
    model_version: str = field(default=MOTION_VERSION, init=False)

    def __post_init__(self):
        _text(self.candidate_id, "candidate ID")
        if len(self.candidate_hash) != 64 or any(c not in "0123456789abcdef" for c in self.candidate_hash):
            raise ValueError("candidate SHA256 required")
        for key in ("stages", "samples", "boundaries"):
            object.__setattr__(self, key, tuple(getattr(self, key)))
        if not self.samples or any(not isinstance(s, PredictedMotionSample) for s in self.samples):
            raise ValueError("nonempty analytical samples required")
        if any(b.time_s < a.time_s for a, b in zip(self.samples, self.samples[1:])):
            raise ValueError("nominal times must be nondecreasing")
        for key in ("provenance", "timings_s"):
            object.__setattr__(self, key, _freeze_json(getattr(self, key)))
        object.__setattr__(self, "boundaries", tuple(_freeze_json(b) for b in self.boundaries))

    def to_dict(self):
        return dict(_plain(self), source="analytical_estimate_not_measured", vector_frame="world",
            linear_reference_points="object origin and TCP, separately named", units="m,s,m/s,m/s^2,rad/s,rad/s^2")


def audited_sources(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    hashes = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in AUDITED_SOURCE_SHA256}
    if hashes != AUDITED_SOURCE_SHA256:
        raise ValueError("A command/control source changed; review the analytical mirror first")
    return hashes


def motion_identity(motion):
    """Stable input lineage excluding nondeterministic profiling measurements."""
    if not isinstance(motion, PredictedMotionContext):
        raise ValueError("PredictedMotionContext required")
    value = motion.to_dict()
    value.pop("timings_s")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def predict_motion(candidate, task, *, initial_object_pose, estimate_source, model=None, sequence_config=None):
    """After-close nominal command clock, excluding variable refinement/arrival.

    Handover ends with an untimed stationary pre-contact hold; receiver encoder
    timeout is NOT duration. Lowering samples reaching nominal table support
    are flagged, never interpreted as validated donor-only dynamics.
    """
    began = perf_counter()
    if not isinstance(candidate, Candidate) or not isinstance(initial_object_pose, PredictedPose):
        raise ValueError("Candidate and explicit estimated initial object pose required")
    _text(estimate_source, "pre-execution pose estimate source")
    model = MotionModel() if model is None else model
    c = SequenceConfig() if sequence_config is None else sequence_config
    if not isinstance(model, MotionModel) or not isinstance(c, SequenceConfig):
        raise ValueError("MotionModel and SequenceConfig required")
    spec = task_spec(task, c)
    hashes = audited_sources()
    grasp = PredictedPose(candidate.grasp_pose.position, candidate.grasp_pose.quaternion)
    initial_tcp = compose(initial_object_pose, grasp)
    lifted_tcp = PredictedPose(np.array(initial_tcp.position_m)+(0, 0, .14), initial_tcp.quaternion_xyzw)
    carry_object = PredictedPose((c.target_xy[0], c.target_xy[1]+(math.copysign(.045, c.pour_angle_deg) if task == "pour_water" else 0), c.carry_height_m), (0, 0, 0, 1))
    carry_tcp = compose(carry_object, grasp)
    commands = [("lift", lifted_tcp, 1., "A donor_moves lift; TCP +0.14 m, retain estimated attitude"),
                ("transport", carry_tcp, 1.3, "A donor_moves transport; object carry identity -> TCP goal")]
    if task == "transport_place":
        lower = compose(PredictedPose((*c.target_xy, .642), (0, 0, 0, 1)), grasp)
        commands.append(("lower", lower, 1.3, "A donor_moves lower; compliant overtravel, contact dynamics unknown"))
    elif task == "handover_transfer":
        commands.append(("pre_receiver_contact_hold", carry_tcp, None, "A receiver_rail encoder-controlled duration unknown; donor nominally stationary before contact"))
    else:
        tilt_object = PredictedPose(carry_object.position_m, Rotation.from_rotvec((math.radians(c.pour_angle_deg), 0, 0)).as_quat())
        tilt = compose(tilt_object, grasp)
        commands.extend((("tilt", tilt, 2., "A donor_moves ABSOLUTE WORLD-X object goal -> TCP"),
                         ("pour_dwell", tilt, c.pour_dwell_s, "A fixed pour_dwell_s; rigid-entrainment approximation"),
                         ("return_upright", carry_tcp, 2., "A donor_moves return absolute identity object attitude")))
    stages, elapsed, previous = [], 0., initial_tcp
    for name, endpoint, duration, source in commands:
        stages.append(PredictedStage(name, previous, endpoint, elapsed, duration, source))
        elapsed += 0 if duration is None else duration
        previous = endpoint
    construction_s = perf_counter()-began
    sampled, boundaries = [], []
    component_times = dict(pose_sampling=0., velocity_acceleration=0., sample_records=0.)
    t = perf_counter()
    for stage in stages:
        stage_times = {}
        values = sample_stage(stage, grasp, model, nominal_table_contact_center_z_m=spec["point_B"][2] if stage.name == "lower" else None, timings=stage_times)
        for key in component_times:
            component_times[key] += stage_times[key]
        if sampled:
            before, after = sampled[-1], values[0]
            boundaries.append(dict(time_s=after.time_s, before=before.stage, after=after.stage,
                velocity_jump_world_m_s=vector(np.array(after.object_origin_velocity_world_m_s)-before.object_origin_velocity_world_m_s),
                acceleration_jump_world_m_s2=vector(np.array(after.object_origin_acceleration_world_m_s2)-before.object_origin_acceleration_world_m_s2),
                omega_jump_world_rad_s=vector(np.array(after.omega_world_rad_s)-before.omega_world_rad_s),
                alpha_jump_world_rad_s2=vector(np.array(after.alpha_world_rad_s2)-before.alpha_world_rad_s2),
                policy="one-sided derivatives; no finite-difference impulse or fabricated arrival dynamics"))
        sampled.extend(values)
    sampling_s = perf_counter()-t
    return PredictedMotionContext(candidate.id, candidate_sha256([candidate]), task, grasp,
        tuple(stages), tuple(sampled), tuple(boundaries),
        dict(source_hashes=hashes, initial_pose_estimate=_plain(initial_object_pose), estimate_source=estimate_source,
            model=asdict(model), sequence_config=asdict(c), stage_specs=spec["stages"],
            time_origin="after assumed grasp establishment; nominal command clock, not episode time",
            assumptions=("rigid attachment; no slip, IK, collision or motor tracking prediction",
                "TCP straight-line/SO(3) analytical interpolation, not actual joint interpolation",
                "one-sided stage derivatives; arrival/refinement waits excluded",
                "receiver contact/support, gripper release, table impact and fluid dynamics unsupported"),
            excluded_stages=("pregrasp", "approach", "close", "release", "receiver_close", "donor_release", "retreat", "receiver_lift", "settle", "terminal"),
            measured_trajectory_used=False, execution_outcome_used=False),
        dict(construction=construction_s, pose_and_derivatives=sampling_s, **component_times, total=perf_counter()-began))
