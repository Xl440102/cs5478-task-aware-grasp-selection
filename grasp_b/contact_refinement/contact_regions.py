"""Immutable geometry evidence only: never a pressure patch or force budget.

All positions/displacements are metres in mug/tool frames as explicitly named.
Numerical tolerance is NOT a calibrated contact-position uncertainty.
"""
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
import re

from grasp_b.wrench_v2.contacts import _freeze_json, _plain

VERSION = 'rigid_contact_formation_evidence_v1'


class Simultaneity(str, Enum):
    COMPATIBLE = 'geometrically_compatible'
    SEQUENTIAL = 'sequential_contact'
    CONFLICT = 'geometry_conflict'
    INSUFFICIENT = 'insufficient_evidence'
    UNSUPPORTED = 'unsupported'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate evidence JSON key')
        result[key] = value
    return result


@dataclass(frozen=True, kw_only=True)
class GeometryTolerances:
    surface_m: float = 1e-8
    displacement_m: float = 1e-6
    tangent_cosine: float = 1e-8
    boundary_factor: float = 2.

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError('positive finite geometric tolerances required')
        if self.boundary_factor <= 1 or self.tangent_cosine >= 1:
            raise ValueError('invalid boundary/tangency threshold')


def displacement_evidence(joint_positions_m, opening_joint_m, *, tolerance_m, boundary_factor=2.):
    """Independent scalar closing coordinates, q decreases towards contact.

    Equality within tolerance is compatibility of a model, not pressure or
    simultaneous independent force support. A numerical boundary stays unknown.
    """
    q = tuple(float(x) for x in joint_positions_m)
    if not q or not all(math.isfinite(x) for x in (*q, opening_joint_m, tolerance_m, boundary_factor)):
        raise ValueError('finite nonempty closing positions required')
    if tolerance_m <= 0 or boundary_factor <= 1:
        raise ValueError('positive tolerance and boundary factor >1 required')
    gaps = tuple(max(q)-x for x in q)
    spread = max(gaps)
    if min(q) < 0 or max(q) > opening_joint_m+tolerance_m:
        status = Simultaneity.CONFLICT
    elif tolerance_m/boundary_factor <= spread <= tolerance_m*boundary_factor:
        status = Simultaneity.INSUFFICIENT
    elif spread > tolerance_m:
        status = Simultaneity.SEQUENTIAL
    else:
        status = Simultaneity.COMPATIBLE
    return dict(classification=status.value, first_joint_position_m=max(q),
                first_contact_displacement_m=opening_joint_m-max(q), closing_gaps_m=list(gaps),
                spread_m=spread, displacement_tolerance_m=tolerance_m,
                position_uncertainty_m=None, uncertainty_reason='not calibrated; numerical tolerance only')


@dataclass(frozen=True)
class GeometryEvidence:
    payload: object

    def __post_init__(self):
        data = json.loads(json.dumps(_plain(self.payload), allow_nan=False))
        if data.get('algorithm_version') != VERSION:
            raise ValueError('unknown geometry evidence version')
        Simultaneity(data['classification'])
        if data.get('frame') != 'mug' or data.get('length_unit') != 'm':
            raise ValueError('explicit mug-local metres required')
        for key in ('candidate_hash', 'contact_spec_hash', 'input_hash', 'mug_geometry_hash', 'gripper_geometry_hash'):
            if not re.fullmatch('[0-9a-f]{64}', data[key]):
                raise ValueError('invalid identity: '+key)
        GeometryTolerances(**data['tolerances'])
        for contact in data['contacts']:
            if contact['finger_id'] not in ('left', 'right'):
                raise ValueError('unknown finger')
            for key in ('position_mug_m', 'normal_outward'):
                v = contact[key]
                if len(v) != 3 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in v):
                    raise ValueError('invalid point/normal')
            if abs(sum(x*x for x in contact['normal_outward'])-1) > 1e-7:
                raise ValueError('invalid unit outward normal')
        previous = data.pop('evidence_hash', None)
        actual = digest(data)
        if previous is not None and previous != actual:
            raise ValueError('geometry evidence checksum mismatch')
        data['evidence_hash'] = actual
        object.__setattr__(self, 'payload', _freeze_json(data))

    @property
    def classification(self):
        return Simultaneity(self.payload['classification'])

    def to_dict(self):
        return _plain(self.payload)

    def to_json(self):
        return json.dumps(self.to_dict(), sort_keys=True, separators=(',', ':'), allow_nan=False)

    @classmethod
    def from_json(cls, text):
        data = json.loads(text, object_pairs_hook=_unique,
                          parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        if 'evidence_hash' not in data:
            raise ValueError('serialized checksum required')
        return cls(data)

    def assert_identity(self, **expected):
        allowed = {'candidate_id', 'candidate_hash', 'contact_spec_hash', 'input_hash', 'mug_geometry_hash', 'gripper_geometry_hash'}
        if not expected or set(expected)-allowed:
            raise ValueError('explicit supported identity required')
        if any(self.payload[key] != value for key, value in expected.items()):
            raise ValueError('geometry evidence identity mismatch')
