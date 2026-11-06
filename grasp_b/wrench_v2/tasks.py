"""Pre-execution finite quasi-static task loads, not trajectories or rankings.

R maps mug to world; all output wrenches are mug-local about an EXPLICIT
reference o. Gravity: F=R.T m g, tau=(COM-o) cross F. Contact demand is the
negative of the external wrench. Neither a TCP moment nor receiver support is
added. Static inertia is retained as provenance, not used in equilibrium.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

from .contacts import _freeze_json, _plain
from .gws import PrimitiveWrenchData, _array, _scalar, _text

TASK_MODEL_VERSION = "finite_quasistatic_tws_v1"
TASK_NAMES = ("transport_place", "handover_transfer", "pour_water")


def _vector(value):
    return tuple(map(float, _array(value, shape=(3,))))


def _tensor(value):
    if value is None:
        return None
    a = _array(value, shape=(3, 3))
    if not np.allclose(a, a.T, atol=1e-14, rtol=0.) or np.min(np.linalg.eigvalsh(a)) < 0:
        raise ValueError("inertia must be symmetric positive semidefinite, kg*m^2")
    return tuple(tuple(map(float, row)) for row in a)


def _digest(value):
    return hashlib.sha256(json.dumps(_plain(value), sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def _quaternion(value):
    q = _array(value, shape=(4,))
    if not math.isclose(float(q@q), 1., abs_tol=1e-10, rel_tol=0.):
        raise ValueError("unit xyzw quaternion required; no implicit normalization")
    # Canonical hemisphere only; never repair or normalize an invalid input.
    for v in q[::-1]:
        if v != 0:
            if v < 0:
                q = -q
            break
    return tuple(float(v) if v != 0 else 0. for v in q)


def rotation_matrix(quaternion_xyzw):
    x, y, z, w = _quaternion(quaternion_xyzw)
    return np.array(((1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)),
                     (2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)),
                     (2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y))))


@dataclass(frozen=True, slots=True, kw_only=True)
class ObjectParameters:
    mass_kg: float
    com_m: tuple
    source: str
    inertia_com_kg_m2: tuple | None = None
    provenance: Mapping = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "mass_kg", _scalar(self.mass_kg, "mass_kg", positive=True))
        object.__setattr__(self, "com_m", _vector(self.com_m))
        object.__setattr__(self, "inertia_com_kg_m2", _tensor(self.inertia_com_kg_m2))
        _text(self.source, "object parameter source")
        if not isinstance(self.provenance, Mapping):
            raise ValueError("object provenance must be a mapping")
        object.__setattr__(self, "provenance", _freeze_json(self.provenance))


def read_declared_object(urdf_path, *, link_name="mug"):
    """Read DECLARED single-link inertial data, never effective PyBullet dynamics.

    Inertia about COM is rotated from URDF inertial axes to mug/link axes.
    File hash binds model identity; no simulator or URDF loading is involved.
    """
    path = Path(urdf_path)
    raw = path.read_bytes()
    links = ET.fromstring(raw).findall("link")
    if len(links) != 1 or links[0].get("name") != link_name:
        raise ValueError("explicit single mug link required; no multi-link mass aggregation")
    inertial = links[0].find("inertial")
    if inertial is None or inertial.find("mass") is None:
        raise ValueError("missing declared mass")
    origin = inertial.find("origin")
    com = _vector([float(x) for x in ("0 0 0" if origin is None else origin.get("xyz", "0 0 0")).split()])
    rpy = _vector([float(x) for x in ("0 0 0" if origin is None else origin.get("rpy", "0 0 0")).split()])
    tensor = inertial.find("inertia")
    inertia = None
    if tensor is not None:
        xx, yy, zz, xy, xz, yz = (float(tensor.attrib[k]) for k in ("ixx", "iyy", "izz", "ixy", "ixz", "iyz"))
        r, p, y = rpy
        rx = np.array(((1, 0, 0), (0, math.cos(r), -math.sin(r)), (0, math.sin(r), math.cos(r))))
        ry = np.array(((math.cos(p), 0, math.sin(p)), (0, 1, 0), (-math.sin(p), 0, math.cos(p))))
        rz = np.array(((math.cos(y), -math.sin(y), 0), (math.sin(y), math.cos(y), 0), (0, 0, 1)))
        rotation = rz@ry@rx
        inertia = rotation@np.array(((xx, xy, xz), (xy, yy, yz), (xz, yz, zz)))@rotation.T
    return ObjectParameters(mass_kg=float(inertial.find("mass").attrib["value"]), com_m=com,
        inertia_com_kg_m2=inertia, source="urdf_declared_not_runtime_verified",
        provenance=dict(asset_path=str(path), asset_sha256=hashlib.sha256(raw).hexdigest(),
                        link_name=link_name, inertial_rpy_rad=rpy, units="kg,m,kg*m^2"))


@dataclass(frozen=True, slots=True, kw_only=True)
class RigidPayload:
    """Explicit assumed rigid water case, not PBF/sloshing or drained mass.

    None inertia stays unknown. A zero inertia may be provided explicitly for
    an assumed point-mass model, but is never automatically substituted.
    """
    mass_kg: float
    com_m: tuple
    source: str
    inertia_com_kg_m2: tuple | None = None

    def __post_init__(self):
        object.__setattr__(self, "mass_kg", _scalar(self.mass_kg, "payload mass_kg"))
        object.__setattr__(self, "com_m", _vector(self.com_m))
        object.__setattr__(self, "inertia_com_kg_m2", _tensor(self.inertia_com_kg_m2))
        _text(self.source, "payload assumption source")


def combine_payload(body, payload):
    if not isinstance(body, ObjectParameters) or not isinstance(payload, RigidPayload):
        raise ValueError("ObjectParameters and RigidPayload required")
    m = body.mass_kg+payload.mass_kg
    center = (body.mass_kg*np.array(body.com_m)+payload.mass_kg*np.array(payload.com_m))/m
    inertia = None
    if payload.mass_kg == 0:
        inertia = body.inertia_com_kg_m2
    elif body.inertia_com_kg_m2 is not None and payload.inertia_com_kg_m2 is not None:
        inertia = np.zeros((3, 3))
        for item in (body, payload):
            d = np.array(item.com_m)-center
            inertia += np.array(item.inertia_com_kg_m2)+item.mass_kg*((d@d)*np.eye(3)-np.outer(d, d))
    return ObjectParameters(mass_kg=m, com_m=center, inertia_com_kg_m2=inertia,
        source="rigid_payload_approximation_not_fluid_dynamics",
        provenance=dict(dry_object=_plain(body), payload=_plain(payload),
                        combination="mass sum; mass-weighted COM; parallel-axis inertia only if known"))


@dataclass(frozen=True, slots=True, kw_only=True)
class Orientation:
    quaternion_xyzw: tuple
    source: str

    def __post_init__(self):
        object.__setattr__(self, "quaternion_xyzw", _quaternion(self.quaternion_xyzw))
        _text(self.source, "orientation source")


def pour_orientations_x(angles_rad, *, source, base_quaternion_xyzw=(0., 0., 0., 1.)):
    """Explicit finite WORLD-X rotations relative to a supplied base attitude.

    A's donor_moves uses identity carry and an ABSOLUTE WORLD X tilt. The base
    here is an explicitly assumed CARRY attitude, not automatically the scene's
    initial yaw: lift preserves the estimate but transport commands identity.
    A nonidentity base is an uncertainty scenario, not A's nominal tilt goal.
    Angles/uncertainty are caller inputs, never inferred from outcomes/motion.py.
    """
    base = _quaternion(base_quaternion_xyzw)
    result = []
    for angle in angles_rad:
        a = float(_array(angle, shape=()))
        v, w = np.array((math.sin(a/2), 0., 0.)), math.cos(a/2)
        bv, bw = np.array(base[:3]), base[3]
        q = (*tuple(w*bv+bw*v+np.cross(v, bv)), w*bw-float(v@bv))
        result.append(Orientation(quaternion_xyzw=q, source=source))
    if not result:
        raise ValueError("nonempty finite angle set required")
    return tuple(result)


@dataclass(frozen=True, slots=True, kw_only=True)
class Disturbance:
    """Force/free couple in declared frame; application point ALWAYS mug-local."""
    force_n: tuple
    application_point_m: tuple
    source: str
    free_torque_nm: tuple = (0., 0., 0.)
    frame: str = "world"
    force_unit: str = "N"
    torque_unit: str = "N*m"
    position_unit: str = "m"

    def __post_init__(self):
        for name in ("force_n", "application_point_m", "free_torque_nm"):
            object.__setattr__(self, name, _vector(getattr(self, name)))
        if self.frame not in ("world", "mug") or (self.force_unit, self.torque_unit, self.position_unit) != ("N", "N*m", "m"):
            raise ValueError("explicit world/mug disturbance with N,N*m,m units required")
        _text(self.source, "disturbance source")


ZERO_DISTURBANCE = Disturbance(force_n=(0., 0., 0.), application_point_m=(0., 0., 0.),
                             source="zero external perturbation by definition")
UPRIGHT = Orientation(quaternion_xyzw=(0., 0., 0., 1.), source="upright definition; A donor_moves carry identity")


def axis_disturbances(*, horizontal_force_n, vertical_force_n, application_point_m, source, frame="world"):
    """Nominal plus +/-X,+/-Y and optional +/-Z; amplitudes explicit, in N.

    Application point is ALWAYS mug-local, even when force/torque is world.
    This finite cross is not the eight vertices of a three-axis box.
    """
    h = _scalar(horizontal_force_n, "horizontal_force_n")
    v = _scalar(vertical_force_n, "vertical_force_n")
    result = [Disturbance(force_n=(0., 0., 0.), application_point_m=application_point_m, source=source, frame=frame)]
    for axis, amplitude in enumerate((h, h, v)):
        if amplitude == 0:
            continue
        for sign in (1., -1.):
            force = np.zeros(3)
            force[axis] = sign*amplitude
            result.append(Disturbance(force_n=force, application_point_m=application_point_m, source=source, frame=frame))
    return tuple(result)


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskWrenchScenario:
    task_name: str
    stage: str
    object_parameters: ObjectParameters
    reference_point_m: tuple
    gravity_world_m_s2: tuple
    orientation: Orientation
    disturbance: Disturbance
    context_source: str
    assumptions: tuple = ()
    scenario_id: str = field(init=False)
    external_wrench: tuple = field(init=False)
    required_contact_wrench: tuple = field(init=False)
    coordinate_frame: str = field(default="mug", init=False)
    model_version: str = field(default=TASK_MODEL_VERSION, init=False)

    def __post_init__(self):
        if self.task_name not in TASK_NAMES:
            raise ValueError("unsupported task name")
        _text(self.stage, "stage")
        _text(self.context_source, "gravity/reference context source")
        if self.stage != "donor_hold":
            raise ValueError("only donor_hold scenarios supported; use unsupported TaskWrenchSet for other stages")
        if not isinstance(self.object_parameters, ObjectParameters) or not isinstance(self.orientation, Orientation) or not isinstance(self.disturbance, Disturbance):
            raise ValueError("typed physical parameters, orientation and disturbance required")
        for name in ("reference_point_m", "gravity_world_m_s2"):
            object.__setattr__(self, name, _vector(getattr(self, name)))
        if isinstance(self.assumptions, str):
            raise ValueError("assumptions must be a sequence, not a string")
        assumptions = tuple(self.assumptions)
        if not all(isinstance(x, str) and x.strip() for x in assumptions):
            raise ValueError("assumptions must be a sequence of nonempty strings")
        object.__setattr__(self, "assumptions", assumptions)
        body, d = self.object_parameters, self.disturbance
        r = rotation_matrix(self.orientation.quaternion_xyzw)
        fg = r.T@(body.mass_kg*np.array(self.gravity_world_m_s2))
        fd = np.array(d.force_n) if d.frame == "mug" else r.T@d.force_n
        td = np.array(d.free_torque_nm) if d.frame == "mug" else r.T@d.free_torque_nm
        torque = (np.cross(np.array(body.com_m)-self.reference_point_m, fg)+
                  np.cross(np.array(d.application_point_m)-self.reference_point_m, fd)+td)
        external = _array(np.concatenate((fg+fd, torque)), shape=(6,))
        object.__setattr__(self, "external_wrench", tuple(map(float, external)))
        object.__setattr__(self, "required_contact_wrench", tuple(map(float, -external)))
        identity = dict(task_name=self.task_name, stage=self.stage, body=_plain(body),
            reference_point_m=self.reference_point_m, gravity_world_m_s2=self.gravity_world_m_s2,
            orientation=_plain(self.orientation), disturbance=_plain(d), assumptions=self.assumptions,
            context_source=self.context_source, model_version=TASK_MODEL_VERSION)
        object.__setattr__(self, "scenario_id", "TWS_"+_digest(identity)[:24])

    def to_dict(self):
        return dict(_plain(self), force_unit="N", torque_unit="N*m", position_unit="m",
                    gravity_unit="m/s^2", quaternion_convention="xyzw; mug-to-world")


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskWrenchSet:
    task_name: str
    stage: str
    scenarios: tuple[TaskWrenchScenario, ...]
    unsupported_reason: str | None = None
    model_version: str = field(default=TASK_MODEL_VERSION, init=False)

    def __post_init__(self):
        if self.task_name not in TASK_NAMES:
            raise ValueError("unsupported task name")
        _text(self.stage, "stage")
        scenarios = tuple(self.scenarios)
        if any(not isinstance(s, TaskWrenchScenario) or s.task_name != self.task_name or s.stage != self.stage for s in scenarios):
            raise ValueError("inconsistent task scenario")
        if bool(scenarios) == (self.unsupported_reason is not None):
            raise ValueError("nonempty supported set OR empty unsupported set required")
        if self.unsupported_reason is not None:
            _text(self.unsupported_reason, "unsupported reason")
        if len({s.scenario_id for s in scenarios}) != len(scenarios):
            raise ValueError("duplicate scenario identity; do not duplicate identical inputs")
        if scenarios and len({(s.reference_point_m, s.gravity_world_m_s2) for s in scenarios}) != 1:
            raise ValueError("shared reference point and gravity required")
        object.__setattr__(self, "scenarios", scenarios)

    def to_dict(self):
        return dict(_plain(self), status="unsupported" if self.unsupported_reason else "supported")

    def diagnostics(self):
        # Exact duplicates only; no tolerance-based physical equivalence claim.
        unique = len({s.required_contact_wrench for s in self.scenarios})
        return dict(scenario_count=len(self.scenarios), unique_wrench_count=unique,
                    exact_duplicate_wrench_count=len(self.scenarios)-unique,
                    json_bytes=len(json.dumps(self.to_dict(), allow_nan=False).encode()))


def build_task_wrench_set(task_name, body, *, reference_point_m, gravity_world_m_s2, context_source,
                          orientations=None, disturbances=(ZERO_DISTURBANCE,),
                          payload_cases=(), stage="donor_hold"):
    """Finite Cartesian set: payload cases x attitudes x disturbances, in input order.

    None payload means dry object. Multiple explicit payload mass/COM cases
    represent uncertainty; they do not imply a draining/time sequence. Pour
    attitudes must be supplied. No trajectory, acceleration or outcome access.
    Sets can be reused across candidates sharing object/context/reference.
    """
    if task_name not in TASK_NAMES or not isinstance(body, ObjectParameters):
        raise ValueError("official task name and ObjectParameters required")
    reference = _vector(reference_point_m)
    gravity = _vector(gravity_world_m_s2)
    _text(context_source, "gravity/reference context source")
    if stage != "donor_hold":
        return TaskWrenchSet(task_name=task_name, stage=stage, scenarios=(), unsupported_reason=(
            "Only donor-held static/quasi-static equilibrium is modeled; receiver support, "
            "transfer force allocation, table support, release and fluid dynamics are unsupported."))
    if orientations is None:
        if task_name == "pour_water":
            raise ValueError("pour requires explicit finite orientation set")
        orientations = (UPRIGHT,)
    orientations, disturbances, payload_cases = tuple(orientations), tuple(disturbances), tuple(payload_cases)
    if not orientations or not disturbances or any(not isinstance(x, Orientation) for x in orientations) or any(not isinstance(x, Disturbance) for x in disturbances):
        raise ValueError("nonempty typed orientation/disturbance sets required")
    if payload_cases and task_name != "pour_water":
        raise ValueError("rigid water payload cases currently supported only for pour")
    if any(p is not None and not isinstance(p, RigidPayload) for p in payload_cases):
        raise ValueError("explicit RigidPayload or None dry case required")
    scenarios = []
    for payload in payload_cases or (None,):
        combined = body if payload is None else combine_payload(body, payload)
        for orientation in orientations:
            for disturbance in disturbances:
                scenarios.append(TaskWrenchScenario(task_name=task_name, stage=stage,
                    object_parameters=combined, reference_point_m=reference, gravity_world_m_s2=gravity,
                    orientation=orientation, disturbance=disturbance, context_source=context_source,
                    assumptions=("quasi-static donor-only support; no receiver/table assistance",
                                 "finite input scenarios, not all disturbances or continuous trajectory",
                                 "rigid payload case, no sloshing/draining" if payload is not None else "dry object")))
    return TaskWrenchSet(task_name=task_name, stage=stage, scenarios=tuple(scenarios))


def required_wrench_for_primitives(scenario, primitives):
    """Checked sign/frame/reference adapter, no LP or ranking side effects."""
    if not isinstance(scenario, TaskWrenchScenario) or not isinstance(primitives, PrimitiveWrenchData):
        raise ValueError("TaskWrenchScenario and PrimitiveWrenchData required")
    if scenario.coordinate_frame != "mug" or scenario.reference_point_m != tuple(primitives.reference_point_m):
        raise ValueError("mug-local wrench reference must exactly match GWS; no implicit TCP shift")
    return scenario.required_contact_wrench
