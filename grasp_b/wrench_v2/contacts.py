"""Immutable, predictive ContactSpec v2 sidecars, independent of A/C runtime.

All positions and directions use the mug-local frame. Positions are metres,
forces newtons, and moments newton-metres; no unit conversion is performed.
Normals point OUT of the object, so compressive forces point against them.
Closing/approach directions denote tool +Y/+Z in that frame. Quaternion poses
stay in the unchanged Candidate contract (PyBullet xyzw), not in this record.

``valid`` means structurally valid under the declared model, not measured
contact, force closure, calibrated capacity, or physical grasp success. Even a
valid spec may have unknown friction and force limits. Surface membership and
predicted closure geometry belong to the next geometry implementation task.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
import math
from numbers import Real
import re
from types import MappingProxyType


CONTACT_SPEC_VERSION = "contact_spec_v2"
Vector3 = tuple[float, float, float]
_UNIT_TOLERANCE = 1e-8


class FingerID(str, Enum):
    LEFT = "left"
    RIGHT = "right"


class GraspFamily(str, Enum):
    """Canonical families: main's generator 'side' maps explicitly to BODY.

    TOP remains distinct even though its Candidate.region is also 'body'.
    No mapping from generator labels or semantic prose is inferred here.
    """

    BODY = "body"
    TOP = "top"
    HANDLE = "handle"
    RIM = "rim"


class ContactMechanism(str, Enum):
    """Mechanism labels supplement, never replace, explicit tool directions.

    In particular main's top-down handle closing and the old v3 crossbar
    pinch may use different closing axes despite sharing the HANDLE family.
    """

    BODY_SIDE = "body_side"
    BODY_TOP_DOWN = "body_top_down"
    HANDLE_SIDE = "handle_side"
    HANDLE_TOP_DOWN = "handle_top_down"
    HANDLE_BAR_PINCH = "handle_bar_pinch"
    RIM_INNER_OUTER = "rim_inner_outer"


class ContactModel(str, Enum):
    """Supported model vocabulary, not a claim that parameters are calibrated.

    A future finite-patch approximation can use several PCWF samples sharing
    one finger group. Naming SOFT_FINGER does not infer a torsional capacity.
    """

    FRICTIONLESS_POINT = "frictionless_point"
    PCWF = "pcwf"
    SOFT_FINGER = "soft_finger"


class ContactSource(str, Enum):
    PREDICTED_ANALYTICAL = "predicted_analytical"
    PREDICTED_GEOMETRY_QUERY = "predicted_geometry_query"
    # Vocabulary only: predictive ContactPoint explicitly rejects this source.
    OBSERVED_EXECUTION = "observed_execution"


class ContactStatus(str, Enum):
    """Declared geometric-model status; none implies physical grasp success.

    VALID: passes validation under the stated model.
    APPROXIMATE: uses a recorded approximation.
    UNSUPPORTED: the requested geometry cannot currently be represented.
    UNKNOWN: information required to establish geometry is missing.
    INVALID: a known geometric constraint is violated (record remains valid).
    """

    VALID = "valid"
    APPROXIMATE = "approximate"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"
    INVALID = "invalid"


_FAMILY_MECHANISMS = {
    GraspFamily.BODY: frozenset({ContactMechanism.BODY_SIDE}),
    GraspFamily.TOP: frozenset({ContactMechanism.BODY_TOP_DOWN}),
    GraspFamily.HANDLE: frozenset({ContactMechanism.HANDLE_SIDE,
                                  ContactMechanism.HANDLE_TOP_DOWN,
                                  ContactMechanism.HANDLE_BAR_PINCH}),
    GraspFamily.RIM: frozenset({ContactMechanism.RIM_INNER_OUTER}),
}


def _enum(value, enum_type, name):
    if isinstance(value, enum_type):
        return value
    if type(value) is str:
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise ValueError(f"{name} must be one of {[v.value for v in enum_type]}")


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be nonempty text without surrounding whitespace")
    return value


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number (no strings or booleans)")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _vector(value, name, *, unit=False):
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(f"{name} must contain exactly three components")
    result = tuple(_number(v, name) for v in value)
    if unit and not math.isclose(math.hypot(*result), 1., rel_tol=0.,
                                 abs_tol=_UNIT_TOLERANCE):
        raise ValueError(f"{name} must be a unit vector; vectors are not repaired")
    return result


def _sequence(value, name):
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list or tuple")
    return tuple(value)


def _texts(value, name):
    return tuple(_text(v, name) for v in _sequence(value, name))


def _freeze_json(value, name="provenance", ancestors=None):
    """Copy and recursively freeze JSON data, rejecting cycles and NaN/Inf."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if not isinstance(value, (Mapping, list, tuple)):
        raise ValueError(f"{name} must contain only finite JSON data")
    ancestors = set() if ancestors is None else ancestors
    if id(value) in ancestors:
        raise ValueError(f"{name} must not contain cycles")
    ancestors.add(id(value))
    try:
        if isinstance(value, Mapping):
            if any(type(k) is not str for k in value):
                raise ValueError(f"{name} keys must be strings")
            return MappingProxyType({k: _freeze_json(v, f"{name}.{k}", ancestors)
                                     for k, v in value.items()})
        return tuple(_freeze_json(v, name, ancestors) for v in value)
    finally:
        ancestors.remove(id(value))


def _plain(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    return value


def _record(data, cls):
    if not isinstance(data, Mapping) or set(data) != {f.name for f in fields(cls)}:
        raise ValueError(f"{cls.__name__} fields do not match {CONTACT_SPEC_VERSION}")
    return dict(data)


def _parameter_metadata(source, calibrated, known):
    _text(source, "parameter source")
    if type(calibrated) is not bool:
        raise ValueError("calibrated must be a boolean")
    if known and source == "unknown":
        raise ValueError("known parameters require an explicit source")
    if calibrated and (not known or source == "unknown"):
        raise ValueError("calibrated requires a known parameter and explicit source")


class _JsonRecord:
    __slots__ = ()

    def to_dict(self) -> dict:
        """Return a detached JSON-compatible copy, not mutable internal storage."""
        return _plain(self)


@dataclass(frozen=True, slots=True, kw_only=True)
class ContactParameters(_JsonRecord):
    """Optional local bounds. None means unknown, never a legacy default.

    A local normal limit bounds this sample; it does NOT grant extra capacity
    beyond its finger group. The optional soft-finger coefficient has metres
    as units (moment per normal force); it is not Bullet's spinning scalar.
    ``calibrated`` applies to supplied values only; None values stay unknown.
    """

    friction_coefficient: float | None = None
    normal_force_limit_n: float | None = None
    torsional_moment_per_normal_m: float | None = None
    source: str = "unknown"
    calibrated: bool = False

    def __post_init__(self):
        names = ("friction_coefficient", "normal_force_limit_n",
                 "torsional_moment_per_normal_m")
        for name in names:
            value = getattr(self, name)
            if value is not None:
                value = _number(value, name)
                if value < 0:
                    raise ValueError(f"{name} must be nonnegative")
                object.__setattr__(self, name, value)
        _parameter_metadata(self.source, self.calibrated,
                            any(getattr(self, name) is not None for name in names))

    @classmethod
    def from_dict(cls, data):
        return cls(**_record(data, cls))


@dataclass(frozen=True, slots=True, kw_only=True)
class FingerCapacityGroup(_JsonRecord):
    """One aggregate normal-force budget per finger, shared by all its samples.

    This is a declared contact-force bound, not inferred from a motor limit.
    No budget is inferred by summing ContactParameters.normal_force_limit_n.
    """

    group_id: str
    finger_id: FingerID
    normal_force_budget_n: float | None = None
    source: str = "unknown"
    calibrated: bool = False

    def __post_init__(self):
        _text(self.group_id, "group_id")
        object.__setattr__(self, "finger_id", _enum(self.finger_id, FingerID, "finger_id"))
        if self.normal_force_budget_n is not None:
            value = _number(self.normal_force_budget_n, "normal_force_budget_n")
            if value < 0:
                raise ValueError("normal_force_budget_n must be nonnegative")
            object.__setattr__(self, "normal_force_budget_n", value)
        _parameter_metadata(self.source, self.calibrated,
                            self.normal_force_budget_n is not None)

    @classmethod
    def from_dict(cls, data):
        return cls(**_record(data, cls))


@dataclass(frozen=True, slots=True, kw_only=True)
class ContactPoint(_JsonRecord):
    """A predicted object-surface point, with outward normal, in mug coordinates."""

    position_m: Vector3
    normal_outward: Vector3
    finger_id: FingerID
    surface_id: str
    contact_model: ContactModel
    source: ContactSource
    capacity_group_id: str
    parameters: ContactParameters = field(default_factory=ContactParameters)

    def __post_init__(self):
        object.__setattr__(self, "position_m", _vector(self.position_m, "position_m"))
        object.__setattr__(self, "normal_outward",
                           _vector(self.normal_outward, "normal_outward", unit=True))
        for name, enum in (("finger_id", FingerID), ("contact_model", ContactModel),
                           ("source", ContactSource)):
            object.__setattr__(self, name, _enum(getattr(self, name), enum, name))
        if self.source is ContactSource.OBSERVED_EXECUTION:
            raise ValueError("observed_execution contacts are forbidden in predictive ContactSpec v2")
        _text(self.surface_id, "surface_id")
        _text(self.capacity_group_id, "capacity_group_id")
        if not isinstance(self.parameters, ContactParameters):
            raise ValueError("parameters must be ContactParameters")
        if (self.contact_model is ContactModel.FRICTIONLESS_POINT and
                self.parameters.friction_coefficient not in (None, 0.)):
            raise ValueError("frictionless_point cannot specify nonzero friction")
        if (self.contact_model is not ContactModel.SOFT_FINGER and
                self.parameters.torsional_moment_per_normal_m not in (None, 0.)):
            raise ValueError("torsional capacity requires the soft_finger contact model")

    @classmethod
    def from_dict(cls, data):
        record = _record(data, cls)
        record["parameters"] = ContactParameters.from_dict(record["parameters"])
        return cls(**record)


@dataclass(frozen=True, slots=True, kw_only=True)
class ContactSpec(_JsonRecord):
    """Internal sidecar only; this class has no Candidate/selector dependency.

    candidate_hash is A's candidate_sha256([candidate]): SHA256 of canonical
    JSON for the ordered singleton full Candidate, not just public metadata.
    The caller computes that fingerprint and can verify it via
    assert_candidate_identity(). No ID prefix is assumed or rewritten.

    Non-valid statuses require reasons; approximate additionally requires an
    assumption. Invalid describes a known invalid geometric proposal, not a
    malformed record: NaN, invalid normals, etc. always raise ValueError.
    """

    schema_version: str
    candidate_id: str
    candidate_hash: str
    generator_name: str
    generator_version: str
    family: GraspFamily
    mechanism: ContactMechanism
    object_frame: str
    reference_point_m: Vector3
    contacts: tuple[ContactPoint, ...]
    status: ContactStatus
    provenance: Mapping[str, object]
    assumptions: tuple[str, ...]
    capacity_groups: tuple[FingerCapacityGroup, ...] = ()
    closing_direction: Vector3 | None = None
    approach_direction: Vector3 | None = None
    reasons: tuple[str, ...] = ()

    def __post_init__(self):
        if self.schema_version != CONTACT_SPEC_VERSION:
            raise ValueError(f"schema_version must be {CONTACT_SPEC_VERSION}")
        for name in ("candidate_id", "generator_name", "generator_version"):
            _text(getattr(self, name), name)
        if not isinstance(self.candidate_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", self.candidate_hash):
            raise ValueError("candidate_hash must be a lowercase SHA256 fingerprint")
        for name, enum in (("family", GraspFamily), ("mechanism", ContactMechanism),
                           ("status", ContactStatus)):
            object.__setattr__(self, name, _enum(getattr(self, name), enum, name))
        if self.mechanism not in _FAMILY_MECHANISMS[self.family]:
            raise ValueError("family and mechanism contradict each other")
        if self.object_frame != "mug":
            raise ValueError("object_frame must be 'mug'; no coordinate/unit conversion is performed")
        object.__setattr__(self, "reference_point_m", _vector(self.reference_point_m, "reference_point_m"))
        object.__setattr__(self, "contacts", _sequence(self.contacts, "contacts"))
        object.__setattr__(self, "capacity_groups", _sequence(self.capacity_groups, "capacity_groups"))
        object.__setattr__(self, "assumptions", _texts(self.assumptions, "assumptions"))
        object.__setattr__(self, "reasons", _texts(self.reasons, "reasons"))
        if not isinstance(self.provenance, Mapping) or not self.provenance:
            raise ValueError("provenance must be a nonempty JSON mapping")
        object.__setattr__(self, "provenance", _freeze_json(self.provenance))
        for name in ("closing_direction", "approach_direction"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _vector(value, name, unit=True))
        if self.closing_direction is not None and self.approach_direction is not None:
            if abs(math.fsum(a*b for a, b in zip(self.closing_direction, self.approach_direction))) > _UNIT_TOLERANCE:
                raise ValueError("closing_direction and approach_direction must be orthogonal")
        self._validate_groups()
        if self.status in (ContactStatus.VALID, ContactStatus.APPROXIMATE):
            if {p.finger_id for p in self.contacts} != {FingerID.LEFT, FingerID.RIGHT}:
                raise ValueError("valid/approximate requires contacts on both fingers")
            if self.closing_direction is None or self.approach_direction is None:
                raise ValueError("valid/approximate requires explicit closing/approach directions")
        if self.status is not ContactStatus.VALID and not self.reasons:
            raise ValueError("non-valid status requires traceable reasons")
        if self.status is ContactStatus.APPROXIMATE and not self.assumptions:
            raise ValueError("approximate status requires explicit assumptions")

    def _validate_groups(self):
        if any(not isinstance(p, ContactPoint) for p in self.contacts):
            raise ValueError("contacts must contain ContactPoint records")
        if any(not isinstance(g, FingerCapacityGroup) for g in self.capacity_groups):
            raise ValueError("capacity_groups must contain FingerCapacityGroup records")
        groups = {g.group_id: g for g in self.capacity_groups}
        if len(groups) != len(self.capacity_groups):
            raise ValueError("duplicate capacity group_id")
        if len({g.finger_id for g in self.capacity_groups}) != len(self.capacity_groups):
            raise ValueError("one aggregate capacity group per finger is required")
        for point in self.contacts:
            group = groups.get(point.capacity_group_id)
            if group is None or group.finger_id is not point.finger_id:
                raise ValueError("contact capacity group is missing or belongs to another finger")
        if len(set(self.contacts)) != len(self.contacts):
            raise ValueError("duplicate contact samples")

    def capacity_for_finger(self, finger_id: FingerID) -> FingerCapacityGroup | None:
        """Return the shared declaration once; never sum or infer sample limits."""
        finger_id = _enum(finger_id, FingerID, "finger_id")
        return next((g for g in self.capacity_groups if g.finger_id is finger_id), None)

    def assert_candidate_identity(self, candidate_id: str, candidate_hash: str) -> None:
        if candidate_id != self.candidate_id or candidate_hash != self.candidate_hash:
            raise ValueError("candidate identity does not match ContactSpec provenance")

    @classmethod
    def from_dict(cls, data):
        record = _record(data, cls)
        record["contacts"] = tuple(ContactPoint.from_dict(p)
                                   for p in _sequence(record["contacts"], "contacts"))
        record["capacity_groups"] = tuple(FingerCapacityGroup.from_dict(g)
                                          for g in _sequence(record["capacity_groups"], "capacity_groups"))
        return cls(**record)
